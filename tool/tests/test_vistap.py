"""The visualiser's tap (#195): the rig's output, offered to the browser as an
input, by a `pw-loopback` child linked to Sushi's master outputs.

What stands in for what. `pw-link` is a fake graph (`Graph`) for the pure
linking, and a fake `pw-link` program on PATH for the real start routine;
`pw-loopback` is a Python that sleeps. Nothing here is PipeWire: that the
nodes appear as the browser's "Sushi rig output", are followers of Sushi's
driver and vanish with the process was seen in a container
(tool/spikes/visualiser_audio/README.md, "The rig's output"), not here.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from sushi_rig import rig, vistap
from sushi_rig.rig import Launch, plan_children

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
    assert command[command.index("--group") + 1] == vistap.SOURCE_NODE, "both sides in one node group"
    assert command[command.index("--channels") + 1] == "2"


def test_the_tap_is_planned_last_optional_and_realtime_and_only_when_asked_for(tmp_path):
    config = tmp_path / "c.json"
    plain = plan_children(ROOT, config, None, headless=True, tuner=True)
    tapped = plan_children(ROOT, config, None, headless=True, tuner=True, visualiser_tap=True)
    assert vistap.CHILD not in [c["name"] for c in plain]
    assert tapped[:-1] == plain, "nothing else in the plan changes"
    assert tapped[-1] == vistap.child()
    assert tapped[-1]["optional"] is True
    assert tapped[-1]["env_set"] == {"PIPEWIRE_CONFIG_NAME": "client-rt.conf"}, \
        "client.conf loads no module-rt: its data thread ran at normal priority in Sushi's cycle"
    assert "PIPEWIRE_QUANTUM" not in tapped[-1]["env_set"], "it asks the graph for nothing"


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


# --------------------------------------------------------------------------- #
# The start routine, with fake pw-loopback and pw-link programs
# --------------------------------------------------------------------------- #

FAKE_LINK = """#!{python}
import json, os, sys
db = os.environ["FAKE_GRAPH"]
links = json.load(open(db)) if os.path.exists(db) else []
if sys.argv[1:] == ["-l"]:
    for a, b in links:
        print(a); print("  |-> " + b)
    sys.exit(0)
if os.environ.get("FAKE_REFUSE"):
    print("failed to link ports: No such file or directory", file=sys.stderr); sys.exit(1)
links.append(sys.argv[1:3]); json.dump(links, open(db, "w")); sys.exit(0)
"""
FAKE_LOOPBACK = """#!{python}
import os, signal, time
open(os.environ["FAKE_TAP_PID"], "w").write(str(os.getpid()))
signal.signal(signal.SIGTERM, signal.SIG_DFL)
time.sleep(60)
"""


@pytest.fixture
def fake_tools(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, text in (("pw-link", FAKE_LINK), ("pw-loopback", FAKE_LOOPBACK)):
        path = bin_dir / name
        path.write_text(text.format(python=sys.executable))
        path.chmod(path.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_GRAPH", str(tmp_path / "graph.json"))
    monkeypatch.setenv("FAKE_TAP_PID", str(tmp_path / "tap.pid"))
    monkeypatch.setattr(vistap, "LINK_SETTLE", 1.0)
    return tmp_path


def start(tmp_path, link_tap=None, sushi_ready=lambda alive: True):
    config = write(tmp_path, v2_config())
    launch = Launch(
        root=tmp_path, config_name="t", config=config,
        children=[{"name": "sushi", "command": [sys.executable, "-c", "import time; time.sleep(60)"],
                   "optional": False}, vistap.child()],
        headless=True, amp_model=None, amp_values={}, tuner_requested=False, amp_requested=False,
        amp_model_requested=None, rig_source=None, machine_file=None, visualiser_tap_requested=True,
    )
    logs = tmp_path / "logs"
    logs.mkdir(exist_ok=True)
    stopped: list[str] = []
    procs, problems, failure = rig.start_children(
        launch, env=dict(os.environ), logs=logs, spawn=rig.spawn_child,
        start_qpwgraph=lambda *a, **k: None, sushi_ready=sushi_ready,
        generate_panel=lambda *a: (0, ""), link_problems=lambda *a: [], replay_amp=lambda v: None,
        on_stop=stopped.append, **({"link_tap": link_tap} if link_tap else {}),
    )
    return procs, problems, failure, stopped


def kill_all(procs):
    for proc in procs.values():
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=5)


def test_a_start_links_the_tap_with_pw_link_and_keeps_it(fake_tools):
    procs, problems, failure, stopped = start(fake_tools)
    try:
        assert failure is None and problems == [] and stopped == []
        assert vistap.CHILD in procs and procs[vistap.CHILD].poll() is None
        assert json.loads((fake_tools / "graph.json").read_text()) == [
            [MASTER_L, vistap.TAP_PORTS[0]], [MASTER_R, vistap.TAP_PORTS[1]]]
        environ = Path(f"/proc/{procs[vistap.CHILD].pid}/environ").read_bytes().split(b"\0")
        assert b"PIPEWIRE_CONFIG_NAME=client-rt.conf" in environ
    finally:
        kill_all(procs)


def test_a_tap_that_cannot_be_linked_is_stopped_by_its_pid_and_left_out(fake_tools, monkeypatch):
    """A source carrying nothing would be the page's first choice, and a
    silent visualiser: it is stopped, by the pid it was started with, said,
    and not one of the children the state file records."""
    monkeypatch.setenv("FAKE_REFUSE", "1")
    procs, problems, failure, stopped = start(fake_tools)
    try:
        assert failure is None, "the rig plays on"
        assert vistap.CHILD not in procs and "sushi" in procs and procs["sushi"].poll() is None
        assert stopped == [vistap.CHILD], "said to the agent before it was signalled"
        (problem,) = problems
        assert "so it was stopped" in problem and "The sound is unaffected" in problem
        pid = int((fake_tools / "tap.pid").read_text())
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)
    finally:
        kill_all(procs)


def test_a_tap_that_exits_at_start_is_not_linked_and_is_said(fake_tools, monkeypatch):
    import time

    asked: list = []
    monkeypatch.setattr(vistap, "command", lambda: [sys.executable, "-c", "import sys; sys.exit(3)"])

    def link_tap(config):
        asked.append(config)
        return []

    procs, problems, failure, stopped = start(fake_tools, link_tap=link_tap,
                                              sushi_ready=lambda alive: time.sleep(0.5) or True)
    try:
        assert failure is None
        assert asked == [], "nothing to link"
        assert f"{vistap.CHILD} exited during startup (status 3)" in " ".join(problems)
    finally:
        kill_all(procs)
