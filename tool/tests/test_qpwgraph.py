"""Starting qpwgraph — issue #14, and the two causes that were found.

No qpwgraph, no PipeWire and no audio needed: what is pinned here is the
decisions — which lock files are safe to remove, what environment the process
needs, and how hard to try before giving up.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from types import SimpleNamespace

from sushi_rig import qpwgraph
from sushi_rig.qpwgraph import (
    node_ports,
    ports_without_sources,
    stale_qpwgraph_sockets,
)
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


# --- is the node even there? -------------------------------------------------


def test_node_ports_lists_a_nodes_ports_whether_wired_or_not():
    """`ports_without_sources` answers "which are unwired", which cannot
    distinguish a fully wired node from one that is absent. This answers "is it
    here at all", and the two together can."""
    assert node_ports(PW_LINK_INPUTS, "alsa_capture.fmit") == [
        "alsa_capture.fmit:input_MONO"
    ]


def test_node_ports_finds_nothing_for_a_node_that_is_not_listed():
    assert node_ports(PW_LINK_INPUTS, "alsa_capture.nosuch") == []


def test_an_absent_node_is_reported_as_unknown_not_as_wired(monkeypatch):
    """The bug this closes: the settle loop returned `[]` both for "every port
    has a source" and for "PipeWire has never heard of this node", so a check
    that could not tell reported success. The caller needs those apart — one is
    a healthy tuner, the other is a renamed node and an unanswered question."""
    monkeypatch.setattr(
        qpwgraph.subprocess, "run",
        lambda *a, **k: SimpleNamespace(stdout=PW_LINK_INPUTS, returncode=0),
    )
    assert qpwgraph.unconnected_inputs("alsa_capture.nosuch", settle=0.0) is None


def test_a_wired_node_reports_an_empty_list(monkeypatch):
    wired = PW_LINK_INPUTS.replace(
        "alsa_capture.fmit:input_MONO\n",
        "alsa_capture.fmit:input_MONO\n  |<- alsa_input.usb-Audient:capture_AUX0\n",
    )
    monkeypatch.setattr(
        qpwgraph.subprocess, "run",
        lambda *a, **k: SimpleNamespace(stdout=wired, returncode=0),
    )
    assert qpwgraph.unconnected_inputs("alsa_capture.fmit", settle=0.0) == []


def test_an_unwired_node_names_its_ports(monkeypatch):
    monkeypatch.setattr(
        qpwgraph.subprocess, "run",
        lambda *a, **k: SimpleNamespace(stdout=PW_LINK_INPUTS, returncode=0),
    )
    assert qpwgraph.unconnected_inputs("alsa_capture.fmit", settle=0.0) == [
        "alsa_capture.fmit:input_MONO"
    ]


def test_a_missing_pw_link_is_raised_not_reported_as_wired(monkeypatch):
    """`up` must be able to tell "the tuner is fine" from "I could not ask".
    Reporting the second as the first is how the amp stayed dead for a session."""
    def boom(*a, **k):
        raise FileNotFoundError("pw-link")

    monkeypatch.setattr(qpwgraph.subprocess, "run", boom)
    with pytest.raises(OSError):
        qpwgraph.unconnected_inputs("alsa_capture.fmit", settle=0.0)


# --- every link the patchbay asked for (2026-11-01) -------------------------

from sushi_rig.patchbay import Link, live_port_names  # noqa: E402
from sushi_rig.qpwgraph import listed_links, unmade_links  # noqa: E402

# `pw-link -l` as the rig machine printed it on 2026-11-01 (RESULTS-hear-v2),
# device names shortened the way the results file shortened them.
PW_LINK_LIST = """alsa_output.usb-Audient_Audient_iD14-00.analog-surround-40:playback_FL
  |<- sushi:audio_output_0
alsa_output.usb-Audient_Audient_iD14-00.analog-surround-40:playback_FR
  |<- sushi:audio_output_1
alsa_input.usb-Audient_Audient_iD14-00.multichannel-input:capture_AUX0
  |-> sushi:audio_input_1
  |-> sushi:audio_input_0
alsa_input.usb-Audient_Audient_iD14-00.multichannel-input:capture_AUX1
  |-> sushi:audio_input_2
  |-> sushi:audio_input_3
sushi:audio_output_0
  |-> alsa_output.usb-Audient_Audient_iD14-00.analog-surround-40:playback_FL
sushi:audio_output_1
  |-> alsa_output.usb-Audient_Audient_iD14-00.analog-surround-40:playback_FR
sushi:audio_input_0
  |<- alsa_input.usb-Audient_Audient_iD14-00.multichannel-input:capture_AUX0
sushi:audio_input_1
  |<- alsa_input.usb-Audient_Audient_iD14-00.multichannel-input:capture_AUX0
sushi:audio_input_2
  |<- alsa_input.usb-Audient_Audient_iD14-00.multichannel-input:capture_AUX1
sushi:audio_input_3
  |<- alsa_input.usb-Audient_Audient_iD14-00.multichannel-input:capture_AUX1
"""
IN = "alsa_input.usb-Audient_Audient_iD14-00.multichannel-input"
OUT = "alsa_output.usb-Audient_Audient_iD14-00.analog-surround-40"
NAMES = {
    "Audient iD14:capture_AUX0": [f"{IN}:capture_AUX0"],
    "Audient iD14:capture_AUX1": [f"{IN}:capture_AUX1"],
    "Audient iD14:playback_FL": [f"{OUT}:playback_FL"],
    "Audient iD14:playback_FR": [f"{OUT}:playback_FR"],
}


def test_listed_links_reads_both_directions_as_one_set_of_pairs():
    links = listed_links(PW_LINK_LIST)
    assert (f"{IN}:capture_AUX0", "sushi:audio_input_0") in links
    assert ("sushi:audio_output_0", f"{OUT}:playback_FL") in links
    # Printed under both ends, counted once.
    assert len(links) == 6


def test_the_v1_patchbay_is_fully_made_on_the_rigs_own_listing():
    wanted = [
        Link("Audient iD14 Multichannel", "Audient iD14:capture_AUX0", "sushi", "sushi:audio_input_0"),
        Link("Audient iD14 Multichannel", "Audient iD14:capture_AUX1", "sushi", "sushi:audio_input_2"),
        Link("sushi", "sushi:audio_output_0", "Audient iD14 Analog Surround 4.0", "Audient iD14:playback_FL"),
    ]
    assert unmade_links(wanted, NAMES, PW_LINK_LIST) == []


def test_a_v2_rig_on_the_v1_graph_names_the_vocal_channel_as_unwired():
    """The hazard this check exists for: under v2 the vocal is on input 1 and
    nothing feeds it from the microphone, while every process is healthy."""
    wanted = [
        Link("Audient iD14 Multichannel", "Audient iD14:capture_AUX0", "sushi", "sushi:audio_input_0"),
        Link("Audient iD14 Multichannel", "Audient iD14:capture_AUX1", "sushi", "sushi:audio_input_1"),
    ]
    assert unmade_links(wanted, NAMES, PW_LINK_LIST) == [
        ("Audient iD14:capture_AUX1", "sushi:audio_input_1"),
    ]


def test_a_port_that_is_not_in_the_graph_is_unmade_not_skipped():
    """An alias with no live name cannot be wired: the device is unplugged or
    on another profile. Silence here would be the device-renamed failure the
    patchbay check was built to end."""
    wanted = [Link("Pro", "Audient iD14 Pro:capture_1", "sushi", "sushi:audio_input_0")]
    assert unmade_links(wanted, NAMES, PW_LINK_LIST) == [("Audient iD14 Pro:capture_1", "sushi:audio_input_0")]


def test_an_alias_with_two_live_names_is_made_if_either_is_linked():
    names = dict(NAMES)
    names["Audient iD14:capture_AUX0"] = ["alsa_input.other-profile:capture_AUX0", f"{IN}:capture_AUX0"]
    wanted = [Link("x", "Audient iD14:capture_AUX0", "sushi", "sushi:audio_input_0")]
    assert unmade_links(wanted, names, PW_LINK_LIST) == []


def test_live_port_names_maps_the_patchbays_alias_to_pw_links_name():
    """Against the real capture: the alias qpwgraph shows against the
    node.name:port.name that pw-link prints."""
    from pathlib import Path

    dump = (Path(__file__).parent / "fixtures" / "pw-dump-rig.json").read_text()
    names = live_port_names(dump)
    assert names["Audient iD14:capture_AUX0"] == [f"{IN}:capture_AUX0"]
    assert names["sushi:audio_input_0"] == ["sushi:audio_input_0"]
