"""Starting qpwgraph — issue #14, and the two causes that were found.

No qpwgraph, no PipeWire and no audio needed: what is pinned here is the
decisions — which lock files are safe to remove, what environment the process
needs, and how hard to try before giving up.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from sushi_rig.qpwgraph import ports_without_sources, stale_qpwgraph_sockets
from sushi_rig.rig import plan_children


def _names(children):
    return [c["name"] for c in children]


@pytest.fixture
def plan_args(tmp_path):
    return tmp_path, tmp_path / "config" / "electric_board.json", "config/src/x.yaml"


# --- qpwgraph's single-instance lock (issue #14) -----------------------------


def test_a_live_instances_socket_is_never_removed():
    """That socket is how a running qpwgraph is reachable. Deleting it would
    break a patchbay the user may be watching, to fix a problem that isn't
    there."""
    sockets = [Path("/tmp/qpwgraph:someone@host")]
    assert stale_qpwgraph_sockets(sockets, any_running=True) == []


def test_sockets_left_by_a_dead_instance_are_removed():
    """The actual cause of issue #14: qpwgraph's lock socket survives SIGKILL,
    and the next instance then exits immediately and silently — no error, no
    crash, an empty log — leaving the patchbay unconnected while Sushi looks
    perfectly healthy. Confirmed by killing one and relaunching: it dies; delete
    the socket first and the identical command survives.

    Waiting for the process to disappear, which is what the shell scripts did,
    cannot fix this on its own."""
    sockets = [Path("/tmp/qpwgraph:someone@host")]
    assert stale_qpwgraph_sockets(sockets, any_running=False) == sockets


def test_no_sockets_is_not_an_error():
    assert stale_qpwgraph_sockets([], any_running=False) == []


def test_qpwgraph_is_detached_from_the_desktop_session_manager():
    """qpwgraph registers with the session manager over ICE, and from the
    supervisor's own session that registration makes Qt quit immediately and
    silently — empty log, no error, patchbay never connected. Isolated by A/B:
    identical command and environment, the only difference being a new session,
    dies; with SESSION_MANAGER removed it lives.

    This is the second, independent half of issue #14 — the stale lock socket
    is the first, and fixing either alone still leaves it broken."""
    children = plan_children(
        Path("/rig"), Path("/rig/config/x.json"), "y.yaml",
        headless=False, tuner=True,
    )
    qpwgraph = next(c for c in children if c["name"] == "qpwgraph")
    assert "SESSION_MANAGER" in qpwgraph["env_unset"]


def test_nothing_else_has_its_environment_stripped():
    """Sushi and the listener have no business losing environment; keep the
    workaround aimed at the one thing that needs it."""
    children = plan_children(
        Path("/rig"), Path("/rig/config/x.json"), "y.yaml",
        headless=False, tuner=True,
    )
    for child in children:
        if child["name"] != "qpwgraph":
            assert not child.get("env_unset")


# --- qpwgraph start retry (issue #14) ---------------------------------------


class _FakeProc:
    """A Popen stand-in whose exit behaviour the test dictates."""

    def __init__(self, exits_after: bool):
        self.pid = 4321
        self._exits = exits_after

    def poll(self):
        return 2 if self._exits else None


@pytest.fixture
def fake_launch(monkeypatch, tmp_path):
    """Record every attempt without launching anything."""
    from sushi_rig import qpwgraph

    calls = {"cleared": 0, "launched": 0, "outcomes": []}

    def fake_stop(*_args, **_kwargs):
        calls["cleared"] += 1

    def fake_popen(*_args, **_kwargs):
        proc = _FakeProc(calls["outcomes"][calls["launched"]])
        calls["launched"] += 1
        return proc

    monkeypatch.setattr(qpwgraph, "stop_existing_qpwgraph", fake_stop)
    monkeypatch.setattr(qpwgraph.subprocess, "Popen", fake_popen)
    return calls


def test_a_qpwgraph_that_stays_up_is_accepted_first_time(fake_launch, tmp_path):
    from sushi_rig.qpwgraph import start_qpwgraph

    fake_launch["outcomes"] = [False]
    proc = start_qpwgraph(
        {"command": ["qpwgraph"], "env_unset": []}, tmp_path, {}, tmp_path,
        attempts=3, settle=0,
    )
    assert proc is not None
    assert fake_launch["launched"] == 1, "no pointless retry of a working start"


def test_a_silent_exit_is_retried_rather_than_warned_about(fake_launch, tmp_path):
    """qpwgraph exits with status 2 and writes nothing at all. Warning the user
    and carrying on leaves them with no audio; trying again usually just
    works."""
    from sushi_rig.qpwgraph import start_qpwgraph

    fake_launch["outcomes"] = [True, True, False]
    proc = start_qpwgraph(
        {"command": ["qpwgraph"], "env_unset": []}, tmp_path, {}, tmp_path,
        attempts=3, settle=0,
    )
    assert proc is not None
    assert fake_launch["launched"] == 3


def test_every_attempt_clears_the_lock_first(fake_launch, tmp_path):
    """A failed attempt can leave a socket of its own, which would then block
    the next one — so clearing has to happen per attempt, not once up front."""
    from sushi_rig.qpwgraph import start_qpwgraph

    fake_launch["outcomes"] = [True, True, False]
    start_qpwgraph(
        {"command": ["qpwgraph"], "env_unset": []}, tmp_path, {}, tmp_path,
        attempts=3, settle=0,
    )
    assert fake_launch["cleared"] == 3


def test_giving_up_is_reported_rather_than_retried_forever(fake_launch, tmp_path):
    """Bounded: a patchbay that will not start should say so, not hang the
    start of the rig."""
    from sushi_rig.qpwgraph import start_qpwgraph

    fake_launch["outcomes"] = [True, True, True]
    proc = start_qpwgraph(
        {"command": ["qpwgraph"], "env_unset": []}, tmp_path, {}, tmp_path,
        attempts=3, settle=0,
    )
    assert proc is None
    assert fake_launch["launched"] == 3


# --- the neural amp ----------------------------------------------------------


# --- is the patchbay actually wiring things? --------------------------------

PW_LINK_INPUTS = """alsa_capture.fmit:input_MONO
sushi:audio_input_0
  |<- NAM:Output
sushi:audio_input_1
  |<- alsa_input.usb-Audient:capture_AUX1
NAM:Input
  |<- alsa_input.usb-Audient:capture_AUX0
"""


def test_a_port_with_no_sources_is_reported():
    """The tuner no longer auto-connects, so a patchbay that does not name its
    port leaves it running and deaf — which looks exactly like a tuner that
    cannot hear the guitar."""
    assert ports_without_sources(PW_LINK_INPUTS, "alsa_capture.fmit") == [
        "alsa_capture.fmit:input_MONO"
    ]


def test_a_wired_port_is_not_reported():
    listing = PW_LINK_INPUTS.replace(
        "alsa_capture.fmit:input_MONO\n",
        "alsa_capture.fmit:input_MONO\n  |<- alsa_input.usb-Audient:capture_AUX0\n",
    )
    assert ports_without_sources(listing, "alsa_capture.fmit") == []


def test_only_the_named_nodes_ports_are_considered():
    """`sushi:audio_input_*` and `NAM:Input` are all wired here, but the point
    is that another node's unwired port must never be reported as this one's."""
    assert ports_without_sources("other:in\nalsa_capture.fmit:input_MONO\n",
                                 "alsa_capture.fmit") == [
        "alsa_capture.fmit:input_MONO"
    ]


def test_a_node_with_no_ports_at_all_reports_nothing():
    """A tuner that is not running is not an unwired tuner — the caller checks
    the process separately, and this must not invent a port for it."""
    assert ports_without_sources(PW_LINK_INPUTS, "alsa_capture.nosuch") == []
