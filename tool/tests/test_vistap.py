"""The visualiser's tap (#195): the rig's output, offered to the browser as an
input, by a `pw-loopback` child linked to Sushi's master outputs.

What stands in for what. `pw-link` is a fake graph (`Graph`) for the pure
linking. The agent's on-demand tap, its lease and its stops are
test_supervisor.py's, with fake `pw-loopback` and `pw-link` programs.
Nothing here is PipeWire: that the nodes appear as the browser's "Sushi rig
output", are followers of Sushi's driver and vanish with the process was
seen in a container (tool/spikes/visualiser_audio/README.md, "The rig's
output"), not here.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from sushi_rig import rig, vistap
from sushi_rig.rig import plan_children

ROOT = Path(__file__).resolve().parents[2]
MASTER_L, MASTER_R = "sushi:audio_output_0", "sushi:audio_output_1"


def v2_config(**over):
    """A compiled rig of channels, as `emit_compiled` shapes the parts read here."""
    config = {"tracks": [
        {"name": "guitar", "outputs": [{"engine_bus": 1, "track_bus": 0}]},
        {"name": "vocal", "outputs": [{"engine_bus": 2, "track_bus": 0}]},
        {"name": "master", "outputs": [{"engine_bus": 0, "track_bus": 0}]},
    ]}
    config.update(over)
    return config


# --------------------------------------------------------------------------- #
# What is started, and what it is linked from: pure
# --------------------------------------------------------------------------- #

def test_the_tap_is_a_pw_loopback_whose_source_the_browser_lists_and_nothing_autolinks():
    command = vistap.command()
    assert command[0] == "pw-loopback"
    capture = command[command.index("--capture-props") + 1]
    playback = command[command.index("--playback-props") + 1]
    assert f"node.name={vistap.TAP_NODE}" in capture and "media.class=Stream/Input/Audio" in capture
    assert f"node.name={vistap.SOURCE_NODE}" in playback and "media.class=Audio/Source" in playback
    assert f'node.description="{vistap.SOURCE_LABEL}"' in playback
    assert vistap.SOURCE_LABEL == "Sushi rig output"
    for props in (capture, playback):
        assert "node.autoconnect=false" in props, "never linked by the session manager: not to the interface"
    assert command[command.index("-g") + 1] == vistap.SOURCE_NODE, "both sides in one node group"
    assert command[command.index("-c") + 1] == "2" and command[command.index("-m") + 1] == "[ FL FR ]"
    assert "--channel-map" not in command, "PipeWire 1.0.5's pw-loopback refuses it, and exits"


def test_the_tap_starts_realtime_and_up_never_plans_one(tmp_path):
    """The agent starts it on demand (review of PR #200): `up` from the
    command line plans no tap, whatever it was told."""
    config = tmp_path / "c.json"
    for headless in (True, False):
        plan = plan_children(ROOT, config, None, headless=headless, tuner=True)
        assert vistap.CHILD not in [c["name"] for c in plan]
        assert "pw-loopback" not in [c["command"][0] for c in plan]
    child = vistap.child()
    assert child["name"] == vistap.CHILD and child["optional"] is True and child["command"] == vistap.command()
    assert child["env_set"] == {"PIPEWIRE_CONFIG_NAME": "client-rt.conf"}, \
        "client.conf loads no module-rt: its data thread ran at normal priority in Sushi's cycle"
    assert "PIPEWIRE_QUANTUM" not in child["env_set"], "it asks the graph for nothing"


def test_the_master_is_the_master_tracks_outputs_and_a_v1_configs_monitor_bus():
    assert vistap.master_ports(v2_config()) == (MASTER_L, MASTER_R), "never a stem"
    v1 = {"tracks": [{"name": "guitar", "outputs": [{"engine_bus": 0, "track_bus": 0}]},
                     {"name": "vocal", "outputs": [{"engine_bus": 0, "track_bus": 0}]},
                     {"name": "stem", "outputs": [{"engine_bus": 3, "track_bus": 0}]}]}
    assert vistap.master_ports(v1) == (MASTER_L, MASTER_R)
    channels = {"tracks": [{"name": "master", "outputs": [{"engine_channel": 0}, {"engine_channel": 1}]}]}
    assert vistap.master_ports(channels) == (MASTER_L, MASTER_R)
    assert vistap.master_ports({"tracks": []}) == () and vistap.master_ports("junk") == ()
    assert vistap.master_ports({"tracks": [{"name": "master", "outputs": [{"engine_bus": "0"}, "x"]}]}) == ()


def test_left_goes_to_left_and_anything_but_a_pair_is_refused():
    assert vistap.tap_links((MASTER_L, MASTER_R)) == [
        (MASTER_L, "sushi_rig_output_tap:input_FL"), (MASTER_R, "sushi_rig_output_tap:input_FR")]
    assert "on 1 of Sushi's outputs" in vistap.tap_links((MASTER_L,))
    assert "on 0 of Sushi's outputs" in vistap.tap_links(())


@pytest.mark.committed_rigs
def test_every_committed_rig_has_its_master_on_the_same_two_ports_so_a_live_switch_keeps_the_links():
    """A live switch keeps Sushi, its ports and the tap's links: they stay
    right only because every rig's master is on bus 0 (rigfile.py). Any rig
    compiled that put it elsewhere would leave the tap on the wrong ports."""
    machine = ROOT / "machines" / "sonny-dewfall-XPS-9320.yaml"
    for path in sorted((ROOT / "config" / "rigs").glob("*.yaml")):
        compiled = rig.compile_named_rig(path.stem, ROOT, machine)
        assert vistap.master_ports(compiled) == (MASTER_L, MASTER_R), path.name


# --------------------------------------------------------------------------- #
# Linking, against a fake graph
# --------------------------------------------------------------------------- #

class Graph:
    """`pw-link` as `subprocess.run` sees it: `-l` lists the links, `a b`
    makes one if both ports exist ("File exists" if it is made already).
    `appear` is how many listings pass before the tap's ports exist, as they
    appear a beat after pw-loopback starts."""

    def __init__(self, appear=0, fail_list=False):
        self.links: set[tuple[str, str]] = set()
        self.appear = appear
        self.fail_list = fail_list
        self.calls: list[list[str]] = []

    def ports(self):
        tap = list(vistap.TAP_PORTS) if self.appear <= 0 else []
        return {MASTER_L, MASTER_R, "Audient iD14:playback_FL", *tap}

    def __call__(self, args, **kwargs):
        self.calls.append(list(args))
        assert args[0] == "pw-link" and kwargs.get("timeout"), "pw-link, and never unbounded"
        if args[1:] == ["-l"]:
            self.appear -= 1
            if self.fail_list:
                return subprocess.CompletedProcess(args, 1, "", "failed to connect: Host is down")
            out = []
            for a, b in sorted(self.links):
                out += [a, f"  |-> {b}", b, f"  |<- {a}"]
            return subprocess.CompletedProcess(args, 0, "\n".join(out) + "\n", "")
        a, b = args[1:]
        if a not in self.ports() or b not in self.ports():
            return subprocess.CompletedProcess(args, 1, "", "failed to link ports: No such file or directory")
        if (a, b) in self.links:
            return subprocess.CompletedProcess(args, 255, "", "failed to link ports: File exists")
        self.links.add((a, b))
        return subprocess.CompletedProcess(args, 0, "", "")


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def write(tmp_path, config):
    path = tmp_path / "rig.json"
    path.write_text(json.dumps(config))
    return path


def link(tmp_path, graph, config=None, settle=5.0):
    clock = Clock()
    return vistap.link(write(tmp_path, config or v2_config()), run=graph, settle=settle,
                       sleep=clock.sleep, clock=clock)


def test_the_master_is_linked_into_the_tap_and_read_back(tmp_path):
    graph = Graph()
    assert link(tmp_path, graph) == []
    assert graph.links == {(MASTER_L, vistap.TAP_PORTS[0]), (MASTER_R, vistap.TAP_PORTS[1])}
    assert not any("Audient" in " ".join(call) for call in graph.calls), "the interface is never touched"


def test_a_tap_whose_ports_are_late_is_linked_once_they_appear(tmp_path):
    graph = Graph(appear=3)
    assert link(tmp_path, graph) == []
    assert len(graph.links) == 2


def test_linking_again_changes_nothing_so_it_is_also_the_relink(tmp_path):
    graph = Graph()
    assert link(tmp_path, graph) == []
    made = [c for c in graph.calls if c[1:] != ["-l"]]
    assert link(tmp_path, graph) == []
    assert [c for c in graph.calls if c[1:] != ["-l"]] == made, "a link already there is not asked for again"
    graph.links.discard((MASTER_R, vistap.TAP_PORTS[1]))
    assert link(tmp_path, graph) == [] and len(graph.links) == 2, "one gone is made again"


def test_ports_that_never_appear_are_one_problem_naming_the_links_and_the_fallback(tmp_path):
    (problem,) = link(tmp_path, Graph(appear=10 ** 6), settle=1.0)
    assert "could not be linked to Sushi's master outputs, so it was stopped" in problem
    assert f"{MASTER_L} -> {vistap.TAP_PORTS[0]}" in problem
    assert "The sound is unaffected; the visualiser listens to the guitar's input instead" in problem


def test_a_graph_that_cannot_be_listed_is_said_not_taken_for_linked(tmp_path):
    (problem,) = link(tmp_path, Graph(fail_list=True), settle=1.0)
    assert "pw-link -l exited 1: failed to connect: Host is down" in problem


def test_no_pw_link_and_no_master_and_no_config_are_each_said(tmp_path):
    def missing(*a, **k):
        raise FileNotFoundError(2, "No such file or directory", "pw-link")

    clock = Clock()
    (problem,) = vistap.link(write(tmp_path, v2_config()), run=missing, sleep=clock.sleep, clock=clock)
    assert "pw-link could not be run" in problem
    (problem,) = link(tmp_path, Graph(), config={"tracks": []})
    assert "not a left and right pair" in problem
    (problem,) = vistap.link(tmp_path / "absent.json", run=Graph())
    assert "could not be read" in problem


def test_the_settle_is_read_when_linking_not_when_link_was_defined(tmp_path, monkeypatch):
    """A test (or a caller) that shortens LINK_SETTLE shortens the wait: it
    was a default bound when `link` was defined, so the override did
    nothing (review of PR #200)."""
    monkeypatch.setattr(vistap, "LINK_SETTLE", 0.5)
    clock = Clock()
    (problem,) = vistap.link(write(tmp_path, v2_config()), run=Graph(appear=10 ** 6), sleep=clock.sleep, clock=clock)
    assert "not made" in problem
    assert clock.now < 1.0, f"waited {clock.now} s, not the 0.5 s set"


# --------------------------------------------------------------------------- #
# Realtime or nothing, and the lease: pure
# --------------------------------------------------------------------------- #

def test_a_tap_with_a_realtime_thread_passes_and_one_without_is_refused_with_the_fix():
    assert vistap.realtime_refusal((("pw-loopback", 0, 0), ("pw-data-loop", 1, 83)), 7) is None
    assert vistap.realtime_refusal((("pw-data-loop", 2, 50),), 7) is None, "SCHED_RR counts"
    refusal = vistap.realtime_refusal((("pw-loopback", 0, 0), ("pw-data-loop", 0, 0)), 7)
    assert "pid 7" in refusal and "pw-loopback, pw-data-loop" in refusal and "so it was stopped" in refusal
    assert "a cycle the graph did not finish" in refusal and "rtkit" in refusal
    assert "none could be read" in vistap.realtime_refusal((), 7)


def test_a_lease_lasts_from_its_last_renewal_and_the_tap_lingers_after_the_last_one():
    leases = vistap.Leases(lease=30, linger=180)
    leases.take("a", 0)
    assert not leases.due(29) and leases.pages(29) == ["a"]
    leases.take("a", 20)  # renewed
    assert not leases.due(49) and leases.stops_in(49) is None
    assert leases.pages(51) == [] and not leases.due(51), "out at 50: lingers from then"
    assert leases.stops_in(51) == pytest.approx(179) and not leases.due(229)
    assert leases.due(230)
    leases.take("a", 231)
    assert not leases.due(232), "taken again: not due"


def test_two_pages_share_one_tap_and_it_lingers_once_both_have_given_it_back():
    leases = vistap.Leases(lease=30, linger=180)
    leases.take("a", 0)
    leases.take("b", 1)
    leases.give_back("a", 5)
    assert leases.pages(5) == ["b"] and not leases.due(5)
    leases.give_back("b", 6)
    assert not leases.due(185) and leases.due(186)


def test_a_trip_gives_it_back_at_once_unless_another_page_holds_it():
    leases = vistap.Leases(lease=30, linger=180)
    leases.take("a", 0)
    leases.take("b", 0)
    leases.give_back("a", 1, at_once=True)
    assert not leases.due(1), "b still listens"
    leases.give_back("b", 2, at_once=True)
    assert leases.due(2) and leases.stops_in(2) == 0.0
    leases.give_back("never-held", 3)
    assert leases.due(3)


def test_the_linger_starts_when_the_last_lease_lapses_not_the_first():
    """Review of PR #200 (rr200/leases.py): a's lease lapsed while b held
    one; that set the linger's start, so it ended 180 s after a, not b."""
    leases = vistap.Leases(lease=30, linger=180)
    leases.take("a", 0)   # until 30, and its page goes without a word
    leases.take("b", 25)  # until 55
    assert leases.pages(31) == ["b"] and leases.idle_since is None
    leases.give_back("b", 33)
    assert leases.stops_in(33) == pytest.approx(180)
    both = vistap.Leases(lease=30, linger=180)
    both.take("a", 0)
    both.take("b", 25)
    both.pages(31)
    assert not both.due(210) and not both.due(234) and both.due(235), "from b's end at 55"


def test_a_record_names_one_process_by_its_start_time(tmp_path):
    import os
    import sys

    path = tmp_path / "vistap.json"
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        vistap.write_record(proc.pid, os.getpgrp(), path=path)
        record = vistap.read_record(path)
        assert record["pid"] == proc.pid and record["starttime"] == vistap.starttime(proc.pid)
        assert vistap.recorded_alive(record)
        assert not vistap.recorded_alive({**record, "starttime": record["starttime"] + 1}), "a reused pid"
        assert not vistap.recorded_alive({**record, "pid": 1}) and not vistap.recorded_alive(None)
        vistap.clear_record(proc.pid + 1, path=path)
        assert vistap.read_record(path) is not None, "another pid's clear leaves it"
        vistap.clear_record_of_group(os.getpgrp() + 1, path=path)
        assert vistap.read_record(path) is not None, "another group's down leaves it"
        vistap.clear_record_of_group(os.getpgrp(), path=path)
        assert vistap.read_record(path) is None
    finally:
        proc.kill()
        proc.wait()
    assert not vistap.recorded_alive(record), "gone"


def test_a_record_is_written_whole_by_a_rename_and_names_its_owner(tmp_path, monkeypatch):
    """Never half a record: written beside it and renamed over it. The owner
    is the agent that wrote it, by pid and start time."""
    import os

    path = tmp_path / "vistap.json"
    path.write_text('{"pid": 1, "old": true}\n')
    renames = []
    real = os.replace
    monkeypatch.setattr(vistap.os, "replace", lambda a, b: renames.append((Path(a).name, Path(b).name)) or real(a, b))
    vistap.write_record(os.getpid(), os.getpgrp(), path=path)
    record = vistap.read_record(path)
    assert renames == [(f".vistap.json.{os.getpid()}.tmp", "vistap.json")]
    assert record["owner"] == {"pid": os.getpid(), "starttime": vistap.starttime(os.getpid())}
    assert vistap.owner_alive(record) and not vistap.owner_alive({"pid": 5}), "no owner recorded: none alive"
    assert list(tmp_path.iterdir()) == [path], "nothing left beside it"


def test_a_record_left_half_written_never_replaces_the_one_there(tmp_path, monkeypatch):
    import os

    path = tmp_path / "vistap.json"
    vistap.write_record(os.getpid(), os.getpgrp(), path=path)
    before = path.read_text()

    def dies(*a, **k):
        raise OSError("killed mid-write")

    monkeypatch.setattr(vistap.os, "replace", dies)
    with pytest.raises(OSError):
        vistap.write_record(os.getpid(), 12345, path=path)
    assert path.read_text() == before and list(tmp_path.iterdir()) == [path]


def test_an_owner_that_is_pid_1_is_alive_and_a_tap_that_is_pid_1_never_is(monkeypatch):
    """In a container the agent can be pid 1 (CI runs pytest so): read as
    gone, a second agent would stop the tap it holds. The tap is the one
    signalled, so pid 1 is never a tap."""
    started = vistap.starttime(1)
    monkeypatch.setattr(vistap, "starttime", lambda pid: started if pid == 1 else None)
    record = {"pid": 1, "pgid": 1, "starttime": started, "owner": {"pid": 1, "starttime": started}}
    assert vistap.owner_alive(record), "pid 1 as the owner, its start time matching: alive"
    assert not vistap.owner_alive({**record, "owner": {"pid": 1, "starttime": started + 1}})
    assert not vistap.owner_alive({**record, "owner": {"pid": 0, "starttime": started}})
    assert not vistap.recorded_alive(record), "pid 1 is never a tap"
