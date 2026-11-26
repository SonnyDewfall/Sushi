"""The agent's supervisor (#29): the agent starts the rig's children, holds
their process group, notices a crash and stops what it holds.

The children are fake, the processes are not. Every command the plan names is
swapped for a Python that sleeps (or exits, for a crash), started for real into
a real process group led by the real holder, so what these pin -- that the
group is the holder's and not the agent's, that `down` takes every member,
that an exit is seen and a stop is not taken for one, that the state file
`status` reads is the one the agent wrote -- is the kernel's answer and not a
mock's. Standing in: Sushi's gRPC, qpwgraph's retries, the patchbay check and
the amp's knobs, which need the rig's own programs.

NOT covered, and not claimed: starting real Sushi, qpwgraph, fmit or Carla
this way, under PipeWire. That is the check MANIFEST.md
describes.

Everything a test starts is killed by its recorded process group at the end,
never by pattern.
"""

from __future__ import annotations

import asyncio
import json
import dataclasses
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from sushi_rig import rig, supervisor as supervisor_module
from sushi_rig import state as state_file
from sushi_rig.procs import pgid_alive
from sushi_rig.rig import Launch, plan_children
from sushi_rig.supervisor import (
    Outcome, Supervisor, World, crash_message, exit_record, parse_lifecycle,
)

SLEEPER = [sys.executable, "-c", "import time; time.sleep(60)"]
QUICK_STEPS = ((signal.SIGINT, 1.0), (signal.SIGKILL, 1.0))


def crasher(code: int, after: float = 0.3) -> list[str]:
    return [sys.executable, "-c", f"import sys, time; time.sleep({after}); sys.exit({code})"]


def crash_when(go: Path, code: int) -> list[str]:
    """A child that exits with `code` once `go` exists, so a test makes it
    crash while the rig is up whatever the start took. A timed crasher
    raced the start: `crasher(3, after=0.3)` read as a start's exit on a
    slow CI runner (PR #200), and does on master too with a start 0.35 s
    slower."""
    return [sys.executable, "-c",
            f"import os, sys, time\nwhile not os.path.exists({str(go)!r}): time.sleep(0.02)\nsys.exit({code})"]


class Spawner:
    """`World.spawn` for real, with each command the plan names swapped for a
    stand-in: a sleeper unless `scripts` says otherwise for that program. The
    holder (this Python) runs as itself. Records what it was asked to start,
    and every process it started, so the test can stop them by group."""

    def __init__(self, scripts: dict[str, list[str]] | None = None) -> None:
        self.scripts = scripts or {}
        self.asked: list[tuple[list[str], int]] = []
        self.started: list[subprocess.Popen] = []
        self.refuse: set[str] = set()

    def __call__(self, command, *, cwd, env, log, process_group):
        command = list(command)
        self.asked.append((command, process_group))
        if command[0] in self.refuse:
            raise FileNotFoundError(2, "No such file or directory", command[0])
        real = command if command[0] == sys.executable else self.scripts.get(command[0], SLEEPER)
        proc = supervisor_module._spawn(real, cwd=cwd, env=env, log=log, process_group=process_group)
        self.started.append(proc)
        return proc

    def commands(self) -> list[str]:
        return [command[0] for command, _ in self.asked]


@pytest.fixture
def world(runtime, tmp_path, monkeypatch):
    """A World whose processes are stand-ins, a real state file in a private
    runtime dir, and teardown that kills every group a test left behind."""
    monkeypatch.setattr(rig, "port_is_open", lambda *a, **k: False)
    spawner = Spawner()
    asked: list[dict] = []
    root = tmp_path / "checkout"
    (root / "config").mkdir(parents=True)
    config = root / "config" / "t.json"
    config.write_text("{}")

    def launch(name="t", *, tuner=True, children=None, **over):
        plan = children if children is not None else plan_children(root, config, tuner=tuner)
        fields = dict(
            root=root, config_name=name, config=config, children=plan,
            amp_model=None, amp_values={}, tuner_requested=tuner, amp_requested=False,
            amp_model_requested=None, rig_source=None, machine_file=None,
        )
        fields.update(over)
        return Launch(**fields)

    state = {"launch": launch}

    def prepare(config_name, *, tuner, amp, amp_model, warn, visualiser_tap):
        # The tap asked for is recorded as asked for; whether it is planned is
        # the test's (a plan of its own, through `launch(children=...)`).
        asked.append(dict(config_name=config_name, tuner=tuner, amp=amp,
                          amp_model=amp_model, visualiser_tap=visualiser_tap))
        return state["launch"](config_name, tuner=tuner,
                               visualiser_tap_requested=visualiser_tap)

    def start_qpwgraph(child, root, env, logs, process_group=None):
        return spawner(child["command"], cwd=root, env=env, log=logs / "qpwgraph.log",
                       process_group=process_group)

    made = World(
        prepare=prepare, spawn=spawner, start_qpwgraph=start_qpwgraph,
        sushi_ready=lambda alive: True,
        link_problems=lambda plan, root, running, rig_run: [],
        replay_amp=lambda values: None, shutdown_steps=QUICK_STEPS, watch_interval=0.05,
        link_tap=lambda config: [],
    )
    made_ns = type("W", (), {})()
    made_ns.world, made_ns.spawner, made_ns.asked, made_ns.state, made_ns.root = (
        made, spawner, asked, state, root)
    yield made_ns
    for proc in spawner.started:
        try:
            os.killpg(proc.pid, signal.SIGKILL) if os.getpgid(proc.pid) == proc.pid else proc.kill()
        except (ProcessLookupError, PermissionError):
            pass
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass


def run(coro):
    return asyncio.run(coro)


async def settle_until(condition, seconds: float = 5.0) -> None:
    deadline = asyncio.get_running_loop().time() + seconds
    while not condition():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("timed out waiting")
        await asyncio.sleep(0.02)


def as_rig_of_channels(monkeypatch, world):
    """`prepare_rig` and `compile_named_rig` give back `world.root`'s
    config/t.json as rig t's compiled config, with no live graph or machine
    file: every `up` is a rig of channels since the v1 configs were retired
    (#218), and these tests are about the plugin check and the refusals of
    `prepare_launch`, not the wiring (test_rig.py's)."""
    import json

    def compile_named(name, root, machine_file=None):
        return json.loads((root / "config" / f"{name}.json").read_text())

    monkeypatch.setattr(rig, "compile_named_rig", compile_named)
    monkeypatch.setattr(rig, "prepare_rig", lambda name, root, runtime, **kw: {
        "config": root / "config" / f"{name}.json", "patchbay": None, "source": f"config/rigs/{name}.yaml",
        "warnings": [], "machine": None})


# --------------------------------------------------------------------------- #
# The decisions, pure
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("body, command", [
    ({"config_name": "guitar_vocal"}, "up"),
    ({"config_name": "x", "rig": True, "tuner": False, "amp": False,
      "amp_model": "Some Amp.nam"}, "up"),
    ({"config_name": "x", "visualiser_tap": False}, "up"),
    (None, "down"),
    ({"force": True}, "down"),
    ({}, "restart"),
    ({"amp": True}, "restart"),
])
def test_the_steps_the_agent_plans_are_read_back_as_they_were_meant(body, command):
    """`parse_lifecycle` is `lifecycle_arguments` backwards, so a route, a
    Program Change switch and whatever else calls `_run_lifecycle` with CLI
    arguments get the supervisor call those arguments mean."""
    agent = pytest.importorskip("sushi_rig.agent")
    parsed_command, options = parse_lifecycle(agent.lifecycle_arguments(command, body))
    assert parsed_command == command
    if command == "up":
        body = body or {}
        assert options == {
            "config_name": body["config_name"],
            "tuner": body.get("tuner", True),
            "amp": body.get("amp", True), "amp_model": body.get("amp_model"), "machine": None,
            "visualiser_tap": body.get("visualiser_tap", True),
        }
    elif command == "down":
        assert options == {"force": bool((body or {}).get("force"))}
    else:
        assert options == ({"amp": True} if body.get("amp") else {})


def test_a_machine_file_the_agent_carries_is_read_back_as_up_s_machine():
    """A switch by restart passes the run's machine file (`switch_steps`),
    which only the agent supplies, never a request; it reaches `up` intact."""
    agent = pytest.importorskip("sushi_rig.agent")
    args = agent.lifecycle_arguments("up", {"config_name": "x", "rig": True}, machine="/m/stage.yaml")
    assert args == ["up", "x", "--machine", "/m/stage.yaml"]
    assert parse_lifecycle(args) == ("up", {"config_name": "x", "tuner": True,
                                            "amp": True, "amp_model": None, "machine": "/m/stage.yaml",
                                            "visualiser_tap": True})
    with pytest.raises(agent.AgentError, match="an absolute path"):
        agent.lifecycle_arguments("up", {"config_name": "x"}, machine="m/stage.yaml")
    assert "--machine" not in agent.lifecycle_arguments("up", {"config_name": "x", "machine": "/etc/x"}), \
        "a request's body never names one"


@pytest.mark.parametrize("args", [
    [], ["shell"], ["up"], ["up", "--headless"], ["up", "x", "--headless"], ["up", "x", "--rig"], ["up", "x", "--force"],
    ["up", "x", "--amp-model"],
    ["down", "--headless"], ["restart", "now"], ["restart", "--amp", "--amp"], ["restart", "--no-amp"],
])
def test_anything_else_is_not_a_lifecycle_step(args):
    with pytest.raises(ValueError):
        parse_lifecycle(args)


def test_an_exit_by_signal_is_named_and_the_message_says_nothing_was_done():
    record = exit_record("amp", 4003, -11, at="t", phase="running", config_name="board",
                         log=Path("/x/amp.log"))
    assert record["signal"] == "SIGSEGV"
    message = crash_message(record)
    assert "amp (pid 4003) was killed by SIGSEGV while it was up in board" in message
    assert "/x/amp.log" in message
    assert "Not restarted" in message and "POST /rig/restart" in message
    assert exit_record("fmit", 5, 3, at="t", phase="startup", config_name="b", log=None)["signal"] is None


# --------------------------------------------------------------------------- #
# up: the agent holds the group, and status reads what it wrote
# --------------------------------------------------------------------------- #

def test_up_starts_what_plan_children_plans_in_one_group_the_agent_holds(world, capsys):
    supervisor = Supervisor(world.world, say=lambda m: None)

    async def go():
        outcome = await supervisor.up("t")
        assert outcome.ok, outcome
        held = supervisor.held
        groups = {os.getpgid(child.proc.pid) for child in held.children.values()}
        assert groups == {held.pgid}, "every child is in the holder's group"
        assert held.pgid != os.getpgrp(), "and that group is not the agent's own"
        assert os.getpgid(held.holder.pid) == held.holder.pid, "the holder leads it"
        assert os.getsid(held.holder.pid) == os.getsid(0), "in the agent's session, so it can be joined"
        await supervisor.down()
        return outcome

    outcome = run(go())
    planned = [c["command"][0] for c in plan_children(
        world.root, world.root / "config" / "t.json", tuner=True)]
    assert world.spawner.commands() == [sys.executable, *planned], "the holder, then the plan, in order"
    assert "Rig up: t\n" in outcome.stdout
    assert world.asked == [dict(config_name="t", tuner=True, amp=True,
                                amp_model=None, visualiser_tap=True)]


def test_the_state_file_the_agent_writes_round_trips_through_status(world, capsys):
    """Same shape as the forked supervisor's: `liveness` says running, and
    `status` and `describe_rig` read it with no change to either."""
    supervisor = Supervisor(world.world, say=lambda m: None)

    async def go():
        assert (await supervisor.up("t", tuner=False)).ok
        recorded = state_file.read_state()
        described = rig.describe_rig()
        rig.status()
        await supervisor.down()
        return recorded, described

    recorded, described = run(go())
    assert recorded["supervisor_pid"] == recorded["pgid"], "the holder is the supervisor of record"
    assert set(recorded) == set(rig.state_record(
        world.state["launch"](), supervisor_pid=2, pgid=2, children={}, log_dir=Path("l"),
        started_at="s"))
    assert "mode" not in recorded and recorded["tuner_requested"] is False
    assert described["verdict"] == "running"
    assert set(described["children"]) == {"qpwgraph", "sushi"}
    assert all(child["alive"] for child in described["children"].values())
    printed = capsys.readouterr().out
    assert printed.startswith("t, up ")
    assert f"  group      {recorded['pgid']}" in printed
    assert "(DEAD)" not in printed
    assert recorded["visualiser_tap_requested"] is True, "what was asked for, as the tuner's is"
    assert rig.restart_arguments(recorded) == ("t", {
        "tuner": False, "amp": False, "amp_model": None, "machine": None,
        "visualiser_tap": True})


def test_a_second_up_is_refused_by_the_state_file_the_agent_wrote(world, monkeypatch):
    """Not a check of the agent's own: `prepare_launch` refuses a second rig,
    and the agent's rig is a rig like any other to it."""
    world.world = dataclasses.replace(world.world, prepare=rig.prepare_launch)
    monkeypatch.setattr(rig, "checkout_root", lambda: world.root)
    as_rig_of_channels(monkeypatch, world)
    supervisor = Supervisor(world.world, say=lambda m: None)

    async def go():
        first = await supervisor.up("t", tuner=False, amp=False)
        second = await supervisor.up("t", tuner=False, amp=False)
        await supervisor.down()
        return first, second

    first, second = run(go())
    assert first.ok, first
    assert not second.ok and "a rig is already running" in second.reason


def test_an_up_through_the_agent_whose_plugins_are_not_all_in_plugins_is_refused_in_ups_words(
        world, monkeypatch, tmp_path):
    """#188, through the agent's own route with the real supervisor and the
    real `prepare_launch`: the page's Start and `sushi-rig up` with an agent
    running both come this way. Refused before anything starts (409), the
    reason the one `up --direct` prints, and the CLI exits 1 with it."""
    pytest.importorskip("aiohttp")
    import json

    from aiohttp.test_utils import TestClient, TestServer

    from rig_fixtures import declaring, fake_lv2_system
    from sushi_rig import agent_client
    from sushi_rig.agent import Agent

    calf = "http://calf.sourceforge.net/plugins/EnvelopeFilter"
    gate = "http://hippie.lt/lv2/gate"
    (world.root / "config" / "t.json").write_text(json.dumps(
        {"tracks": [{"name": "lead", "plugins": [{"name": "gate", "uri": gate}, {"name": "filter", "uri": calf}]}]}))
    declaring(world.root / "plugins", "abGate", gate)
    multiarch = fake_lv2_system(tmp_path, monkeypatch)
    declaring(multiarch, "calf", calf)
    world.world = dataclasses.replace(world.world, prepare=rig.prepare_launch)
    monkeypatch.setattr(rig, "checkout_root", lambda: world.root)
    as_rig_of_channels(monkeypatch, world)
    agent = Agent(supervisor=Supervisor(world.world, say=lambda m: None))

    async def go():
        async with TestClient(TestServer(agent.app())) as client:
            up = await client.post("/rig/up", json={"config_name": "t", "tuner": False,
                                                    "amp": False})
            answer = up.status, await up.json()
            agent._executor.shutdown(wait=True)
            return answer

    status, body = run(go())
    assert status == 409 and body["ok"] is False, body
    plugins = world.root / "plugins"
    assert body["reason"].splitlines() == [
        f"t was not started: 1 plugin(s) it loads are not in {plugins}, the only folder Sushi loads plugins "
        "from (LV2_PATH), so Sushi would quit at startup saying only \"Failed to load tracks from the Json "
        "config file\":",
        f"  {calf}: installed in {multiarch}, which `sushi-rig plugins add` copies from",
        "Nothing was started. To fix it, run this, then start the rig again:",
        "  sushi-rig plugins add",
    ]
    assert world.spawner.asked == [], "nothing was started, not even the holder"
    assert state_file.read_state() is None
    code, _, said = agent_client.lifecycle_result(status, body)
    assert code == 1 and calf in said


# --------------------------------------------------------------------------- #
# A crash is noticed and said, and nothing is restarted
# --------------------------------------------------------------------------- #

def test_a_child_that_crashes_is_reported_and_not_restarted(world):
    world.spawner.scripts["fmit"] = crash_when(world.root / "go", 3)
    lines: list[str] = []
    supervisor = Supervisor(world.world, say=lines.append)

    async def go():
        assert (await supervisor.up("t")).ok
        (world.root / "go").touch()  # the crash, once the rig is up
        await settle_until(lambda: supervisor.crashed)
        await asyncio.sleep(0.3)  # long enough for a restart, if there were one
        described = supervisor.describe()
        await supervisor.down()
        return described

    described = run(go())
    [crash] = described["crashed"]
    assert (crash["name"], crash["returncode"], crash["phase"]) == ("fmit", 3, "running")
    assert crash["log"].endswith("fmit.log")
    assert described["holds"]["children"]["fmit"] == {
        "pid": crash["pid"], "running": False, "returncode": 3}
    assert described["holds"]["children"]["sushi"]["running"] is True
    assert world.spawner.commands().count("fmit") == 1, "principle 9: nothing restarted it"
    assert any("fmit" in line and "Not restarted" in line for line in lines), "and it was said"


def test_a_child_killed_from_outside_is_a_crash_named_by_its_signal(world):
    supervisor = Supervisor(world.world, say=lambda m: None)

    async def go():
        assert (await supervisor.up("t", tuner=False)).ok
        os.kill(supervisor.held.children["sushi"].proc.pid, signal.SIGKILL)
        await settle_until(lambda: supervisor.crashed)
        crashed = list(supervisor.crashed)
        await supervisor.down()
        return crashed

    [crash] = run(go())
    assert crash["name"] == "sushi" and crash["signal"] == "SIGKILL"


def test_a_crash_shows_in_health(world):
    """Through the agent's own route, with the real supervisor behind it."""
    pytest.importorskip("aiohttp")
    from aiohttp.test_utils import TestClient, TestServer

    from sushi_rig.agent import Agent

    world.spawner.scripts["fmit"] = crash_when(world.root / "go", 255)
    supervisor = Supervisor(world.world, say=lambda m: None)
    agent = Agent(supervisor=supervisor)

    async def go():
        async with TestClient(TestServer(agent.app())) as client:
            up = await client.post("/rig/up", json={"config_name": "t"})
            assert up.status == 200, await up.text()
            (world.root / "go").touch()  # the crash, once the rig is up
            await settle_until(lambda: supervisor.crashed)
            health = await (await client.get("/health")).json()
            down = await client.post("/rig/down")
            assert down.status == 200
            after = await (await client.get("/health")).json()
            agent._executor.shutdown(wait=True)
            return health, after

    health, after = run(go())
    seen = health["agent"]["supervisor"]
    assert [c["name"] for c in seen["crashed"]] == ["fmit"]
    assert seen["crashed"][0]["returncode"] == 255
    assert seen["holds"]["state"] == "up"
    assert health["rig"]["running"] is True
    assert health["rig"]["children"]["fmit"]["alive"] is False, "describe_rig sees it too"
    assert after["agent"]["supervisor"]["holds"] is None
    assert [c["name"] for c in after["agent"]["supervisor"]["crashed"]] == ["fmit"], \
        "still readable after the down that followed it"
    assert after["rig"]["running"] is False


def test_an_optional_child_that_dies_at_start_is_a_warning_and_a_crash(world):
    world.spawner.scripts["fmit"] = crasher(1, after=0.0)

    def slow_ready(alive):
        time.sleep(0.3)  # long enough for the tuner to have gone
        return True

    world.world = dataclasses.replace(world.world, sushi_ready=slow_ready)
    supervisor = Supervisor(world.world, say=lambda m: None)

    async def go():
        outcome = await supervisor.up("t")
        crashed = list(supervisor.crashed)
        await supervisor.down()
        return outcome, crashed

    outcome, crashed = run(go())
    assert outcome.ok
    assert "warning: fmit exited during startup (status 1)" in outcome.stderr
    assert [(c["name"], c["phase"]) for c in crashed] == [("fmit", "startup")]


# --------------------------------------------------------------------------- #
# down and restart act on what the agent holds
# --------------------------------------------------------------------------- #

def test_down_stops_every_member_and_is_not_a_crash(world):
    supervisor = Supervisor(world.world, say=lambda m: None)

    async def go():
        assert (await supervisor.up("t")).ok
        pgid = supervisor.held.pgid
        outcome = await supervisor.down()
        return pgid, outcome

    pgid, outcome = run(go())
    assert outcome.ok, outcome
    assert outcome.stdout == "Stopping t...\nRig offline.\n"
    assert not pgid_alive(pgid)
    assert all(proc.poll() is not None for proc in world.spawner.started)
    assert supervisor.crashed == [] and supervisor.held is None
    assert state_file.read_state() is None


def test_a_down_abandoned_at_the_time_limit_leaves_the_rig_watched(world, monkeypatch):
    """A `down` that hangs past the limit is cancelled mid-await. The rig it
    did not stop is still playing, so a crash after it must still be seen:
    left marked as stopping, every exit would pass as part of the stop."""
    release = threading.Event()
    real_down = rig.down

    def hangs(*args, **kwargs):
        release.wait(30)
        return 1

    world.spawner.scripts["fmit"] = crash_when(world.root / "go", 3)
    supervisor = Supervisor(world.world, say=lambda m: None)

    async def go():
        try:
            assert (await supervisor.up("t")).ok
            monkeypatch.setattr(supervisor_module.rig_module, "down", hangs)
            supervisor.timeout = 0.3
            outcome = await supervisor.run(["down"])
            assert outcome.abandoned, outcome
            assert supervisor.held is not None and not supervisor.held.stopping
            (world.root / "go").touch()  # the crash, after the abandoned down
            await settle_until(lambda: supervisor.crashed)  # fmit's, after the abandoned down
            return supervisor.crashed
        finally:
            release.set()
            monkeypatch.setattr(supervisor_module.rig_module, "down", real_down)
            supervisor.timeout = 30
            await supervisor.down()

    [crash] = run(go())
    assert crash["name"] == "fmit"


def test_down_stops_the_held_rig_even_with_its_state_file_gone(world):
    """What the agent holds, it stops: a deleted rig.json does not leave a
    rig playing that `down` cannot reach."""
    supervisor = Supervisor(world.world, say=lambda m: None)

    async def go():
        assert (await supervisor.up("t")).ok
        pgid = supervisor.held.pgid
        state_file.clear_state()
        return pgid, await supervisor.down()

    pgid, outcome = run(go())
    assert outcome.ok and not pgid_alive(pgid)


def test_down_with_nothing_held_is_the_clis_down_through_the_state_file(world):
    supervisor = Supervisor(world.world, say=lambda m: None)
    outcome = run(supervisor.down())
    assert outcome == Outcome(True, 0, "No rig is running.\n", "")


def test_restart_brings_back_what_was_asked_for_in_a_new_group(world):
    supervisor = Supervisor(world.world, say=lambda m: None)

    async def go():
        assert (await supervisor.up("t", tuner=False, amp=False)).ok
        first = supervisor.held.pgid
        outcome = await supervisor.restart()
        second = supervisor.held.pgid
        await supervisor.down()
        return first, second, outcome

    first, second, outcome = run(go())
    assert outcome.ok, outcome
    assert first != second and not pgid_alive(first)
    assert outcome.stdout.startswith("Restarting t...\nStopping t...\nRig offline.\n")
    assert "Rig up: t\n" in outcome.stdout
    assert world.asked[0] == world.asked[1] == dict(
        config_name="t", tuner=False, amp=False, amp_model=None, visualiser_tap=True)


def test_restart_with_the_amp_on_brings_the_amp_to_a_run_started_without_it(world):
    """`restart --amp` (#246): a run started with --no-amp keeps it through a
    plain restart; this one asks `up` for the amp, the rest as asked before."""
    supervisor = Supervisor(world.world, say=lambda m: None)

    async def go():
        assert (await supervisor.up("t", tuner=False, amp=False)).ok
        assert (await supervisor.restart()).ok
        outcome = await supervisor.run(["restart", "--amp"])
        await supervisor.down()
        return outcome

    outcome = run(go())
    assert outcome.ok, outcome
    assert [a["amp"] for a in world.asked] == [False, False, True]
    assert world.asked[2] == dict(
        config_name="t", tuner=False, amp=True, amp_model=None, visualiser_tap=True)


def test_a_restart_whose_up_would_refuse_is_refused_before_down_and_the_rig_plays_on(world, monkeypatch, tmp_path):
    """Review of PR #198: the agent's restart took the rig down, then `up`
    refused it for a plugin plugins/ lacked, and left no rig. Now the
    restart's rig is checked first (`rig.restart_plugins_refusal`) and the
    one running is left as it was."""
    import json
    import shutil

    from rig_fixtures import declaring, fake_lv2_system

    calf = "http://calf.sourceforge.net/plugins/EnvelopeFilter"
    (world.root / "config" / "t.json").write_text(json.dumps(
        {"tracks": [{"name": "lead", "plugins": [{"name": "filter", "uri": calf}]}]}))
    bundle = declaring(world.root / "plugins", "calf", calf)
    fake_lv2_system(tmp_path, monkeypatch)
    monkeypatch.setattr(rig, "checkout_root", lambda: world.root)
    as_rig_of_channels(monkeypatch, world)
    supervisor = Supervisor(world.world, say=lambda m: None)

    async def go():
        assert (await supervisor.up("t", tuner=False, amp=False)).ok
        held = supervisor.held
        shutil.rmtree(bundle)
        outcome = await supervisor.restart()
        still = supervisor.held is held and held.holder.poll() is None
        await supervisor.down()
        return outcome, still

    outcome, still = run(go())
    assert not outcome.ok
    assert outcome.reason.startswith(f"t was not restarted: 1 plugin(s) it loads are not in {world.root / 'plugins'}")
    assert "Nothing was stopped: t plays on." in outcome.reason
    assert still, "the running rig was left as it was"
    assert len(world.asked) == 1, "no second up"


def test_restart_with_nothing_running_refuses_and_says_how_to_start(world):
    outcome = run(Supervisor(world.world, say=lambda m: None).restart())
    assert not outcome.ok and "nothing to restart" in outcome.reason


# --------------------------------------------------------------------------- #
# The rig outlives the agent, and the next one can stop it
# --------------------------------------------------------------------------- #

def test_the_rig_outlives_the_agent_and_the_next_agent_stops_it(world):
    lines: list[str] = []
    first = Supervisor(world.world, say=lines.append)

    async def start_then_leave():
        assert (await first.up("t")).ok
        pgid = first.held.pgid
        await first.release()
        return pgid

    pgid = run(start_then_leave())
    assert pgid_alive(pgid), "the agent's loop ended and the rig is still up"
    assert any("leaving t running" in line for line in lines)
    assert rig.describe_rig()["verdict"] == "running"

    second = Supervisor(world.world, say=lambda m: None)
    assert second.describe(rig.describe_rig())["holds"] is None
    assert "not started by this agent" in second.describe(rig.describe_rig())["note"]
    # A dead agent's children are reparented and reaped by init (or the
    # systemd user manager). Here the test process is still their parent, and
    # a zombie counts as a group member, so this thread stands in for init.
    done = threading.Event()

    def reap():
        while not done.is_set():
            for proc in world.spawner.started:
                proc.poll()
            time.sleep(0.02)

    reaper = threading.Thread(target=reap)
    reaper.start()
    try:
        outcome = run(second.down())
    finally:
        done.set()
        reaper.join()
    assert outcome.ok, outcome
    assert "Rig offline." in outcome.stdout
    assert not pgid_alive(pgid)


def test_a_holder_that_dies_alone_is_a_problem_and_the_children_are_still_judged_each_on_its_own(world):
    """A kill of the recorded supervisor pid, or the OOM killer: the rig is
    still playing, so /health must not call it stopping, and a child that
    crashes after is a crash."""
    lines: list[str] = []
    supervisor = Supervisor(world.world, say=lines.append)

    async def go():
        assert (await supervisor.up("t")).ok
        holder = supervisor.held.holder
        os.kill(holder.pid, signal.SIGKILL)
        await settle_until(lambda: supervisor.problems)
        playing = supervisor.describe()
        os.kill(supervisor.held.children["fmit"].proc.pid, signal.SIGTERM)
        await settle_until(lambda: supervisor.crashed)
        later = supervisor.describe()
        verdict = rig.describe_rig()["verdict"]
        stopped = await supervisor.down()
        return playing, later, verdict, stopped

    playing, later, verdict, stopped = run(go())
    [problem] = playing["problems"]
    assert problem["kind"] == "holder_lost" and "killed by SIGKILL" in problem["message"]
    assert playing["holds"]["state"] == "up", "a playing rig is not 'stopping'"
    assert playing["holds"]["holder"]["running"] is False
    assert playing["crashed"] == []
    assert [(c["name"], c["signal"]) for c in later["crashed"]] == [("fmit", "SIGTERM")]
    assert verdict == "orphaned", "status says what the state file now amounts to"
    assert stopped.ok, "down still stops the group by its id"
    assert any("group holder" in line for line in lines)


# --------------------------------------------------------------------------- #
# A start that fails leaves nothing behind
# --------------------------------------------------------------------------- #

def test_a_required_child_that_cannot_start_fails_up_and_takes_the_group_down(world):
    world.spawner.refuse.add("pw-jack")  # Sushi's command
    supervisor = Supervisor(world.world, say=lambda m: None)

    outcome = run(supervisor.up("t"))
    assert not outcome.ok and "sushi did not start" in outcome.reason
    assert all(proc.poll() is not None for proc in world.spawner.started), "what did start was stopped"
    assert state_file.read_state() is None and supervisor.held is None
    assert supervisor.crashed == [], "a teardown is not a crash"


def test_a_sushi_that_never_answers_fails_up_with_where_to_look(world):
    world.world = dataclasses.replace(world.world, sushi_ready=lambda alive: False)
    supervisor = Supervisor(world.world, say=lambda m: None)
    outcome = run(supervisor.up("t"))
    assert not outcome.ok
    assert "Sushi's gRPC never came up on 127.0.0.1:51051" in outcome.reason
    assert "sushi.log" in outcome.reason
    assert all(proc.poll() is not None for proc in world.spawner.started)
    assert state_file.read_state() is None


def test_a_refusal_keeps_the_warnings_said_before_it(world):
    def prepare(config_name, *, warn, **kwargs):
        warn("SAVE is off for t")
        raise rig.RigError("no such config: t")

    world.world = dataclasses.replace(world.world, prepare=prepare)
    outcome = run(Supervisor(world.world, say=lambda m: None).up("t"))
    assert not outcome.ok
    assert outcome.reason == "warning: SAVE is off for t\nno such config: t"
    assert world.spawner.asked == [], "nothing started for a refusal"


def test_a_bug_while_starting_still_stops_what_started(world):
    def broken(plan, root, running, rig_run):
        raise KeyError("boom")

    world.world = dataclasses.replace(world.world, link_problems=broken)
    supervisor = Supervisor(world.world, say=lambda m: None)
    with pytest.raises(KeyError):
        run(supervisor.up("t"))
    assert world.spawner.started and all(proc.poll() is not None for proc in world.spawner.started)
    assert state_file.read_state() is None


def test_a_held_rig_is_held_in_a_group_the_holder_keeps_through_the_polite_signal(world):
    """The holder dies of SIGINT at once, as the forked supervisor did; the
    group lives on while a slower member does, so `down`'s escalation (the
    CLI's, shared) is still what ends it."""
    deaf = world.root / "deaf"  # written once SIGINT is ignored, so the signal cannot win the race
    world.spawner.scripts["pw-jack"] = [
        sys.executable, "-c",
        "import signal, sys, time; signal.signal(signal.SIGINT, signal.SIG_IGN); "
        "open(sys.argv[1], 'w').close(); time.sleep(60)", str(deaf),
    ]
    supervisor = Supervisor(world.world, say=lambda m: None)

    async def go():
        assert (await supervisor.up("t", tuner=False)).ok
        sushi = supervisor.held.children["sushi"].proc
        await settle_until(deaf.exists)
        outcome = await supervisor.down()
        return sushi, outcome

    sushi, outcome = run(go())
    assert outcome.ok
    # `rig.down`'s own escalation, the CLI's: SIGINT, then SIGTERM.
    assert sushi.returncode == -signal.SIGTERM, "it took the next step to end it"


# --------------------------------------------------------------------------- #
# No step holds the lifecycle lock for ever
# --------------------------------------------------------------------------- #

def test_a_preparation_that_hangs_is_abandoned_with_what_was_running(world):
    release = threading.Event()

    def hangs(config_name, **kwargs):
        release.wait(30)
        raise rig.RigError("too late to matter")

    world.world = dataclasses.replace(world.world, prepare=hangs)
    supervisor = Supervisor(world.world, say=lambda m: None, timeout=0.3)

    async def go():
        try:
            return await supervisor.run(["up", "t"])
        finally:
            release.set()  # before the loop closes, which waits for its threads

    outcome = run(go())
    assert outcome.abandoned
    assert "'up t' did not finish within 0.3s" in outcome.reason
    assert "preparing the rig (rig.prepare_launch" in outcome.reason
    assert "Nothing had been started." in outcome.reason
    assert world.spawner.asked == [] and supervisor.doing is None


def test_a_start_that_hangs_is_abandoned_and_what_it_started_is_stopped(world):
    release = threading.Event()

    def stuck_qpwgraph(child, root, env, logs, process_group=None):
        proc = world.spawner(child["command"], cwd=root, env=env, log=logs / "qpwgraph.log",
                             process_group=process_group)
        release.wait(30)
        return proc

    world.world = dataclasses.replace(world.world, start_qpwgraph=stuck_qpwgraph)
    supervisor = Supervisor(world.world, say=lambda m: None, timeout=0.5)

    async def go():
        try:
            outcome = await supervisor.run(["up", "t"])
            pgid = world.spawner.started[0].pid
            return outcome, pgid
        finally:
            release.set()

    outcome, pgid = run(go())
    assert outcome.abandoned
    assert "It was starting qpwgraph" in outcome.reason
    assert "What it had started was stopped." in outcome.reason
    assert not pgid_alive(pgid) and supervisor.held is None
    for proc in world.spawner.started:
        proc.wait(timeout=5)  # the late one too, killed once the start saw it was abandoned
    assert [c for c, _ in world.spawner.asked if c[0] == "pw-jack"] == [], "nothing started after"
    assert state_file.read_state() is None


def test_a_hung_up_is_a_504_and_frees_the_lock_for_the_next_request(world):
    pytest.importorskip("aiohttp")
    from aiohttp.test_utils import TestClient, TestServer

    from sushi_rig.agent import Agent

    release = threading.Event()

    def hangs(config_name, **kwargs):
        release.wait(30)
        raise rig.RigError("too late")

    world.world = dataclasses.replace(world.world, prepare=hangs)
    agent = Agent(supervisor=Supervisor(world.world, say=lambda m: None, timeout=0.3))

    async def go():
        try:
            async with TestClient(TestServer(agent.app())) as client:
                up = await client.post("/rig/up", json={"config_name": "t"})
                body = await up.json()
                down = await client.post("/rig/down")
                return up.status, body, down.status
        finally:
            release.set()
            agent._executor.shutdown(wait=True)

    status, body, down = run(go())
    assert status == 504 and "did not finish within 0.3s" in body["reason"]
    assert down == 200, "not 409 busy: the lock was let go"


# --------------------------------------------------------------------------- #
# A start that fails says what it leaves, and keeps its exits to itself
# --------------------------------------------------------------------------- #

def test_a_start_whose_group_will_not_stop_keeps_it_held_and_says_which_pids_are_left(world):
    world.world = dataclasses.replace(
        world.world, sushi_ready=lambda alive: False, stop_group=lambda pgid, steps, poll=None: False)
    lines: list[str] = []
    supervisor = Supervisor(world.world, say=lines.append)

    async def go():
        outcome = await supervisor.up("t")
        described = supervisor.describe()
        pgid = supervisor.held.pgid if supervisor.held else None
        again = await supervisor.down()  # the real escalation, through rig.down
        return outcome, described, pgid, again, supervisor.describe()

    outcome, described, pgid, again, after = run(go())
    assert not outcome.ok
    assert f"did NOT all stop: pid(s) {pgid}" in outcome.reason
    assert "POST /rig/down (or sushi-rig down) tries again" in outcome.reason
    assert described["holds"]["state"] == "stuck" and pgid in described["holds"]["left"]
    assert [p["kind"] for p in described["problems"]] == ["stuck"]
    assert any("did NOT all stop" in line for line in lines)
    assert state_file.read_state() is None, "no state file was ever written for it"
    assert again.ok and "Rig offline." in again.stdout
    assert not pgid_alive(pgid) and after["holds"] is None and after["problems"] == []


def test_a_child_that_exits_during_a_failed_start_is_the_failures_not_a_crash(world):
    world.spawner.scripts["fmit"] = crasher(1, after=0.0)

    def never(alive):
        time.sleep(0.3)  # the tuner has gone by now
        return False

    world.world = dataclasses.replace(world.world, sushi_ready=never)
    lines: list[str] = []
    supervisor = Supervisor(world.world, say=lines.append)
    outcome = run(supervisor.up("t"))
    assert not outcome.ok
    assert "Sushi's gRPC never came up" in outcome.reason
    assert "fmit exited with status 1 while starting; see " in outcome.reason
    assert supervisor.crashed == []
    assert "restart" not in outcome.reason and not any("restart" in line for line in lines), \
        "there is no state file for a restart to bring back"


def test_restart_brings_back_the_rig_the_state_file_names_after_a_live_switch(world):
    """A live switch (#93) rewrites the state file to the rig it switched to;
    the agent's record of what it started is older than that."""
    supervisor = Supervisor(world.world, say=lambda m: None)

    async def go():
        assert (await supervisor.up("t")).ok
        state_file.write_state({**state_file.read_state(), "config_name": "u"})
        outcome = await supervisor.restart()
        await supervisor.down()
        return outcome

    outcome = run(go())
    assert outcome.ok and outcome.stdout.startswith("Restarting u...\nStopping u...")
    assert [a["config_name"] for a in world.asked] == ["t", "u"]


# --------------------------------------------------------------------------- #
# The amp alone: Carla stopped and started again inside the held group
# --------------------------------------------------------------------------- #
#
# Carla is a stand-in that starts a helper of its own and sleeps, so that
# "stopped by its pid, with what it started" is the kernel's answer. The
# project file is real; what Carla would make of it is not shown.

CARLA = [sys.executable, "-c",
         "import subprocess, sys, time; "
         "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); time.sleep(60)"]


class Carlas(Spawner):
    """A Spawner whose Carla (`pw-jack carla ...`; Sushi is under pw-jack
    too) is each of `carlas` in turn, the last one for ever after."""

    def __init__(self, carlas):
        super().__init__()
        self.carlas = list(carlas)

    def __call__(self, command, *, cwd, env, log, process_group):
        command = list(command)
        if command[:2] == ["pw-jack", "carla"]:
            script = self.carlas.pop(0) if len(self.carlas) > 1 else self.carlas[0]
            self.asked.append((command, process_group))
            proc = supervisor_module._spawn(script, cwd=cwd, env=env, log=log, process_group=process_group)
            self.started.append(proc)
            return proc
        return super().__call__(command, cwd=cwd, env=env, log=log, process_group=process_group)

    def carla_starts(self):
        return [command for command, _ in self.asked if command[:2] == ["pw-jack", "carla"]]


@pytest.fixture
def amp_world(world, runtime):
    """`world`, with a rig that has an amp: its project in the runtime dir,
    planned as `plan_children` plans it, and `replays` recording each
    `replay_amp` (`replay_fails` says how many raise first)."""
    from sushi_rig.supervisor import child_pids

    runtime.mkdir(parents=True, exist_ok=True)
    project = runtime / "amp.carxp"
    project.write_text("<old project/>")
    world.project = project
    world.replays = []
    world.replay_fails = 0
    world.spawner = Carlas([CARLA])

    def replay(values):
        world.replays.append(dict(values))
        if world.replay_fails > 0:
            world.replay_fails -= 1
            raise RuntimeError("Carla's OSC server never came up")

    def launch(name="t", *, tuner=False, **over):
        plan = plan_children(world.root, world.root / "config" / "t.json", tuner=tuner, amp_project=project)
        fields = dict(
            root=world.root, config_name=name, config=world.root / "config" / "t.json", children=plan,
            amp_model="amp/models/Old.nam", amp_values={"Output Lvl": 6.0},
            tuner_requested=tuner, amp_requested=True, amp_model_requested=None, rig_source=None,
            machine_file=None)
        fields.update(over)
        return Launch(**fields)

    def start_qpwgraph(child, root, env, logs, process_group=None):
        return world.spawner(child["command"], cwd=root, env=env, log=logs / "qpwgraph.log",
                             process_group=process_group)

    world.state["launch"] = launch
    world.world = dataclasses.replace(world.world, spawn=world.spawner, replay_amp=replay,
                                      start_qpwgraph=start_qpwgraph)
    world.child_pids = child_pids
    yield world
    for proc in world.spawner.started:
        try:
            os.killpg(proc.pid, signal.SIGKILL) if os.getpgid(proc.pid) == proc.pid else proc.kill()
        except (ProcessLookupError, PermissionError):
            pass
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass


def test_the_amp_alone_is_restarted_on_a_new_project_in_the_held_group(amp_world):
    world = amp_world
    supervisor = Supervisor(world.world, say=lambda m: None)

    async def go():
        assert (await supervisor.up("t")).ok
        held = supervisor.held
        before = {name: child.proc.pid for name, child in held.children.items()}
        await settle_until(lambda: world.child_pids(before["amp"]))
        helper = world.child_pids(before["amp"])
        result = await supervisor.restart_amp(
            "<new project/>", {"Output Lvl": 3.0}, model="amp/models/New.nam", override="New.nam")
        await asyncio.sleep(0.2)  # long enough for the watch to have seen any exit
        after = {name: child.proc.pid for name, child in held.children.items()}
        groups = {os.getpgid(pid) for pid in after.values()}
        alive = {name: held.children[name].proc.poll() is None for name in after}
        recorded = state_file.read_state()
        crashed = list(supervisor.crashed)
        described = supervisor.describe()
        await supervisor.down()
        return result, before, after, helper, groups, alive, recorded, crashed, described, held

    result, before, after, helper, groups, alive, recorded, crashed, described, held = run(go())
    assert result.ok and result.pid == after["amp"], result
    assert after["amp"] != before["amp"], "a new Carla"
    assert {n: p for n, p in after.items() if n != "amp"} == {n: p for n, p in before.items() if n != "amp"}, \
        "nothing else was restarted: Sushi and qpwgraph kept their pids"
    assert all(alive.values()), "and everything was running afterwards"
    assert groups == {held.pgid}, "the new Carla is in the held group"
    from sushi_rig.supervisor import running

    assert not running(before["amp"]) and not any(running(pid) for pid in helper), \
        "the old Carla and the helper it started are gone (a zombie is gone: CI runs as pid 1)"
    assert world.project.read_text() == "<new project/>"
    assert world.replays == [{"Output Lvl": 6.0}, {"Output Lvl": 3.0}], "up's replay, then the restart's"
    assert world.spawner.carla_starts() == [["pw-jack", "carla", "-n", str(world.project)]] * 2, \
        "Carla started by the planned command, twice"
    assert crashed == [], "the stop was the restart's, not a crash"
    assert described["holds"]["children"]["amp"] == {"pid": after["amp"], "running": True, "returncode": None}
    assert recorded["children"]["amp"] == after["amp"]
    assert (recorded["amp_model"], recorded["amp_model_requested"]) == ("amp/models/New.nam", "New.nam")
    assert rig.restart_arguments(recorded)[1]["amp_model"] == "New.nam", "restart keeps the model"
    assert held.launch.amp_model_requested == "New.nam"


def test_a_new_model_that_does_not_come_up_puts_the_previous_one_back(amp_world):
    world = amp_world
    supervisor = Supervisor(world.world, say=lambda m: None)

    async def go():
        assert (await supervisor.up("t")).ok
        world.replay_fails = 1
        result = await supervisor.restart_amp(
            "<new project/>", {"Output Lvl": 3.0}, model="amp/models/New.nam", override="New.nam")
        await asyncio.sleep(0.2)
        recorded = state_file.read_state()
        crashed = list(supervisor.crashed)
        amp = supervisor.held.children["amp"].proc
        running = amp.poll() is None
        await supervisor.down()
        return result, recorded, crashed, amp.pid, running

    result, recorded, crashed, pid, running = run(go())
    assert not result.ok and result.restored is True and "never came up" in result.reason
    assert running and result.pid == pid
    assert world.project.read_text() == "<old project/>", "the previous model's project, back"
    assert len(world.spawner.carla_starts()) == 3, "up's, the new model's, the previous one's"
    assert recorded["children"]["amp"] == pid
    assert (recorded["amp_model"], recorded["amp_model_requested"]) == ("amp/models/Old.nam", None)
    assert crashed == []


def test_a_carla_that_exits_on_its_new_model_is_said_and_the_previous_one_comes_back(amp_world):
    world = amp_world
    world.spawner.carlas = [CARLA, crasher(1, after=0.0), CARLA]

    def slow_replay(values):
        world.replays.append(dict(values))
        time.sleep(0.3)  # long enough for the stand-in to have exited

    world.world = dataclasses.replace(world.world, replay_amp=slow_replay)
    supervisor = Supervisor(world.world, say=lambda m: None)

    async def go():
        assert (await supervisor.up("t")).ok
        result = await supervisor.restart_amp("<new/>", {}, model="amp/models/New.nam", override="New.nam")
        crashed = list(supervisor.crashed)
        await supervisor.down()
        return result, crashed

    result, crashed = run(go())
    assert not result.ok and result.restored is True
    assert "Carla exited as it started (status 1)" in result.reason and "amp.log" in result.reason
    assert crashed == []


def test_an_amp_that_comes_back_on_neither_model_is_a_problem_said_loudly(amp_world):
    world = amp_world
    lines = []
    supervisor = Supervisor(world.world, say=lines.append)

    async def go():
        assert (await supervisor.up("t")).ok
        world.replay_fails = 2
        result = await supervisor.restart_amp("<new/>", {}, model="amp/models/New.nam", override="New.nam")
        await asyncio.sleep(0.2)
        described = supervisor.describe()
        await supervisor.down()
        return result, described

    result, described = run(go())
    assert not result.ok and result.restored is False and "NO AMP" in result.reason
    assert [p["kind"] for p in described["problems"]] == ["no_amp"]
    assert described["crashed"] == [], "a problem, said once, not a crash too"
    assert any("NO AMP" in line for line in lines)
    assert world.project.read_text() == "<old project/>"


def test_the_amp_is_restarted_only_in_a_rig_this_agent_holds_and_that_has_one(amp_world):
    world = amp_world
    supervisor = Supervisor(world.world, say=lambda m: None)
    result = run(supervisor.restart_amp("<new/>", {}, model="m", override="m"))
    assert not result.ok and result.restored is None and "does not hold a running rig" in result.reason
    assert result.code == "not_held"

    no_amp = world.state["launch"]
    world.state["launch"] = lambda name="t", **kw: dataclasses.replace(
        no_amp(name, **kw), children=plan_children(world.root, world.root / "config" / "t.json",
                                                   tuner=False))

    async def go():
        assert (await supervisor.up("t")).ok
        result = await supervisor.restart_amp("<new/>", {}, model="m", override="m")
        await supervisor.down()
        return result

    result = run(go())
    assert not result.ok and result.restored is None and "no amp" in result.reason
    assert result.code == "no_amp"
    assert world.project.read_text() == "<old project/>"


def test_post_amp_model_restarts_carla_alone_through_the_agent(amp_world, tmp_path):
    """The agent's route, with the real supervisor and a real state file:
    the model changes, Sushi keeps its pid, and the state file says what
    `restart` will bring back."""
    pytest.importorskip("aiohttp")
    import json

    from aiohttp.test_utils import TestClient, TestServer

    from sushi_rig.agent import Agent

    from carla_fake import FakeCarla

    world = amp_world
    models = world.root / "amp" / "models"
    models.mkdir(parents=True)
    for name in ("Old", "New"):
        (models / f"{name}.nam").write_text(json.dumps(
            {"architecture": "WaveNet", "sample_rate": 48000, "config": {"layers": []}, "weights": [],
             "metadata": {"name": name}}))
    carla = FakeCarla()
    supervisor = Supervisor(world.world, say=lambda m: None)
    agent = Agent(root=world.root, supervisor=supervisor, carla=carla.carla())

    async def go():
        async with TestClient(TestServer(agent.app())) as client:
            assert (await client.post("/rig/up", json={"config_name": "t"})).status == 200
            sushi = supervisor.held.children["sushi"].proc.pid
            knob = await client.post("/amp/parameter", json={"name": "Output Lvl", "value": 2.0})
            assert knob.status == 200, await knob.text()
            changed = await client.post("/amp/model", json={"model": "New.nam"})
            body = await changed.json()
            amp = await (await client.get("/amp")).json()
            health = await (await client.get("/health")).json()
            same = supervisor.held.children["sushi"].proc.pid == sushi
            await client.post("/rig/down")
            agent._executor.shutdown(wait=True)
            return changed.status, body, amp, health, same

    status, body, amp, health, same = run(go())
    assert status == 200 and body["changed"], body
    assert same, "Sushi was not restarted"
    assert world.replays[-1] == {"Output Lvl": 2.0}, "the knob as last sent, replayed to the new Carla"
    assert amp["model"]["name"] == "New" and amp["held"] is True
    assert health["rig"]["amp_model"] == "amp/models/New.nam"
    assert health["rig"]["children"]["amp"]["pid"] == body["pid"]
    assert health["agent"]["supervisor"]["crashed"] == []


def test_after_a_live_switch_the_new_model_is_recorded_into_the_switched_rig_not_the_one_up_started(amp_world):
    """A live switch rewrites the state file to the next rig; the held launch
    is still the rig `up` started. The model goes into the run as it is now:
    the switch's config, name and start, so `restart` brings back the rig
    that is playing and the agent does not reseed the amp (review)."""
    world = amp_world
    supervisor = Supervisor(world.world, say=lambda m: None)

    async def go():
        assert (await supervisor.up("t")).ok
        switched = {**state_file.read_state(), "config_name": "u", "config": "/run/rigs/u.json",
                    "rig": "config/rigs/u.yaml", "switched_at": "later"}
        state_file.write_state(switched)
        result = await supervisor.restart_amp("<new/>", {}, model="amp/models/New.nam", override="New.nam")
        recorded = state_file.read_state()
        await supervisor.down()
        return switched, result, recorded

    switched, result, recorded = run(go())
    assert result.ok, result
    assert {k: v for k, v in recorded.items() if k not in ("children", "amp_model", "amp_model_requested")} == \
        {k: v for k, v in switched.items() if k not in ("children", "amp_model", "amp_model_requested")}
    assert recorded["config"] == "/run/rigs/u.json" and recorded["started_at"] == switched["started_at"]
    assert rig.restart_arguments(recorded)[0] == "u"


@pytest.mark.parametrize("how, code", [("gone", "no_state"), ("another", "no_state"), ("project", "project_unreadable")])
def test_a_restart_that_could_not_be_recorded_or_put_back_is_refused_with_carla_untouched(amp_world, how, code):
    world = amp_world
    supervisor = Supervisor(world.world, say=lambda m: None)

    async def go():
        assert (await supervisor.up("t")).ok
        pid = supervisor.held.children["amp"].proc.pid
        if how == "gone":
            state_file.clear_state()
        elif how == "another":
            state_file.write_state({**state_file.read_state(), "pgid": 1234})
        else:
            world.project.unlink()
        result = await supervisor.restart_amp("<new/>", {}, model="m", override="m")
        same = supervisor.held.children["amp"].proc.pid == pid and supervisor.held.children["amp"].proc.poll() is None
        await supervisor.down()
        return result, same

    result, same = run(go())
    assert not result.ok and result.restored is None and result.pid is None and result.code == code, result
    assert same, "Carla was not touched"
    assert len(world.spawner.carla_starts()) == 1


def test_the_restart_reads_and_writes_its_files_off_the_event_loop(amp_world, monkeypatch):
    world = amp_world
    supervisor = Supervisor(world.world, say=lambda m: None)
    seen = []
    for name in ("read_state", "write_state"):
        original = getattr(supervisor_module, name)

        def watched(*args, _original=original, _name=name):
            seen.append((_name, threading.current_thread() is threading.main_thread()))
            return _original(*args)

        monkeypatch.setattr(supervisor_module, name, watched)

    async def go():
        assert (await supervisor.up("t")).ok
        # The watch names a rig by reading the state file when a child exits
        # (`_running_name`), as it always has; the old Carla's exit is one.
        # That is the watch's read, not the restart's, and is set aside here.
        supervisor._running_name = lambda held: "t"
        seen.clear()
        result = await supervisor.restart_amp("<new/>", {}, model="m", override="m")
        calls = list(seen)
        await supervisor.down()
        return result, calls

    result, calls = run(go())
    assert result.ok
    restart_io = [c for c in calls if c[0] in ("read_state", "write_state")]
    assert ("write_state", False) in restart_io
    assert all(not on_loop for _, on_loop in restart_io), restart_io


# --------------------------------------------------------------------------- #
# The visualiser's tap (#195): started on demand, held by leases, realtime or
# nothing. `pw-loopback` is a sleeper unless a test says otherwise; links and
# threads are stood in, but for the tests that run fake programs.
# --------------------------------------------------------------------------- #

FIFO = (("pw-loopback", 0, 0), ("pw-data-loop", 1, 83))
NORMAL = (("pw-loopback", 0, 0), ("pw-data-loop", 0, 0))


class Ticks:
    """A clock a test moves on (`World.clock`)."""

    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def tap_world(world, **over):
    """The fixture's world with the tap's effects stood in: links made, a
    realtime thread, a clock the test moves, a short linger."""
    ticks = Ticks()
    linked: list = []
    fields = dict(link_tap=lambda config: linked.append(config) or [], tap_threads=lambda pid: FIFO,
                  clock=ticks, tap_lease=30.0, tap_linger=60.0)
    fields.update(over)
    world.world = dataclasses.replace(world.world, **fields)
    return ticks, linked


def taps_started(world) -> list[subprocess.Popen]:
    return [p for p, (command, _) in zip(world.spawner.started, world.spawner.asked) if command[0] == "pw-loopback"]


def test_up_never_starts_a_tap(world):
    tap_world(world)
    supervisor = Supervisor(world.world, say=lambda m: None)

    async def go():
        assert (await supervisor.up("t", tuner=False)).ok
        await asyncio.sleep(0.2)
        report = supervisor.tap_report()
        await supervisor.down()
        return report

    report = run(go())
    assert "pw-loopback" not in world.spawner.commands()
    assert report["running"] is False and report["offered"] is True


def test_a_tap_is_started_for_a_page_linked_checked_and_held_in_the_rigs_group(world):
    ticks, linked = tap_world(world)
    supervisor = Supervisor(world.world, say=lambda m: None)

    async def go():
        assert (await supervisor.up("t", tuner=False)).ok
        answer = await supervisor.tap("page-a")
        tap = supervisor.held.children["vistap"].proc
        group = os.getpgid(tap.pid) == supervisor.held.pgid
        report = supervisor.tap_report()
        again = await supervisor.tap("page-a")
        await supervisor.down()
        return answer, tap, group, report, again

    answer, tap, group, report, again = run(go())
    assert answer.ok and answer.source == {"node": "sushi_rig_output", "label": "Sushi rig output"}
    assert (answer.lease, answer.renew) == (30.0, 10.0)
    assert group, "in the rig's group, so down takes it"
    assert linked == [world.root / "config" / "t.json"], "linked, from the config Sushi runs"
    assert report["running"] and report["pid"] == tap.pid and report["pages"] == 1
    assert again.ok and len(taps_started(world)) == 1, "a renewal starts nothing"
    assert tap.poll() is not None, "down took it with the group"


def test_two_pages_share_one_tap_and_it_stops_by_pid_after_the_linger_with_no_crash(world):
    ticks, _ = tap_world(world)
    lines: list[str] = []
    supervisor = Supervisor(world.world, say=lines.append)

    async def go():
        assert (await supervisor.up("t", tuner=False)).ok
        assert (await supervisor.tap("a")).ok and (await supervisor.tap("b")).ok
        tap = supervisor.held.children["vistap"].proc
        await supervisor.tap_release("a")
        await asyncio.sleep(0.2)
        still = tap.poll() is None
        await supervisor.tap_release("b")
        ticks.now += 59
        await asyncio.sleep(0.2)
        lingering = tap.poll() is None
        ticks.now += 2
        await settle_until(lambda: supervisor.tap_report()["stopped"] is not None)
        report = supervisor.tap_report()
        crashed = list(supervisor.crashed)
        children = set(supervisor.held.children)
        await supervisor.down()
        return still, lingering, report, crashed, children, tap

    still, lingering, report, crashed, children, tap = run(go())
    assert still, "b still held it"
    assert lingering, "kept for the linger after the last page let go"
    assert len(taps_started(world)) == 1
    assert tap.poll() is not None and report["stopped"]["why"] == "no page held its lease for 60s"
    assert crashed == [] and "vistap" not in children, "stopped on purpose: no crash, not held"


def test_a_lease_not_renewed_runs_out_and_the_tap_is_stopped(world):
    ticks, _ = tap_world(world)
    supervisor = Supervisor(world.world, say=lambda m: None)

    async def go():
        assert (await supervisor.up("t", tuner=False)).ok
        assert (await supervisor.tap("a")).ok
        tap = supervisor.held.children["vistap"].proc
        ticks.now += 29
        await asyncio.sleep(0.2)
        held = tap.poll() is None
        ticks.now += 62  # the lease out at 30, then the 60 s linger
        await settle_until(lambda: supervisor.tap_stopped is not None)
        await supervisor.down()
        return held, list(supervisor.crashed)

    held, crashed = run(go())
    assert held and crashed == []


def test_a_trip_stops_the_tap_at_once_unless_another_page_holds_it(world):
    tap_world(world)
    supervisor = Supervisor(world.world, say=lambda m: None)

    async def go():
        assert (await supervisor.up("t", tuner=False)).ok
        assert (await supervisor.tap("a")).ok and (await supervisor.tap("b")).ok
        tap = supervisor.held.children["vistap"].proc
        first = await supervisor.tap_release("a", at_once=True)
        alive = tap.poll() is None
        second = await supervisor.tap_release("b", at_once=True)
        report = supervisor.tap_report()
        await supervisor.down()
        return first, alive, second, tap, report

    first, alive, second, tap, report = run(go())
    assert first["stopped"] is False and alive, "b still listens"
    assert second == {"running": False, "stopped": True} and tap.poll() is not None
    assert "xrun safeguard tripped" in report["stopped"]["why"]


def test_a_tap_that_cannot_be_linked_is_refused_stopped_and_no_crash(world):
    tap_world(world, link_tap=lambda config: ["the visualiser tap could not be linked ..., so it was stopped"])
    supervisor = Supervisor(world.world, say=lambda m: None)

    async def go():
        assert (await supervisor.up("t", tuner=False)).ok
        answer = await supervisor.tap("a")
        await asyncio.sleep(0.3)  # several watches, to see an exit if one were taken for a crash
        children, crashed, report = set(supervisor.held.children), list(supervisor.crashed), supervisor.tap_report()
        await supervisor.down()
        return answer, children, crashed, report

    answer, children, crashed, report = run(go())
    assert not answer.ok and answer.code == "not_linked" and "could not be linked" in answer.reason
    [tap] = taps_started(world)
    assert tap.poll() is not None and "vistap" not in children and crashed == []
    assert report["refused"]["code"] == "not_linked"


def test_a_tap_with_no_realtime_thread_is_refused_and_stopped_by_its_pid(world):
    """The threads of the stand-in itself, a Python sleeper at normal
    priority, read from /proc as the agent reads a real tap's."""
    tap_world(world, tap_threads=supervisor_module.threads_of, tap_realtime_wait=0.2)
    supervisor = Supervisor(world.world, say=lambda m: None)

    async def go():
        assert (await supervisor.up("t", tuner=False)).ok
        answer = await supervisor.tap("a")
        report = supervisor.tap_report()
        await supervisor.down()
        return answer, report

    answer, report = run(go())
    [tap] = taps_started(world)
    assert not answer.ok and answer.code == "not_realtime"
    assert f"pid {tap.pid}" in answer.reason and "rtkit" in answer.reason
    assert tap.poll() is not None, "stopped"
    assert report["refused"]["code"] == "not_realtime" and report["running"] is False


def test_no_visualiser_tap_and_no_rig_held_are_refusals_that_start_nothing(world):
    tap_world(world)
    supervisor = Supervisor(world.world, say=lambda m: None)

    async def go():
        nothing = await supervisor.tap("a")
        assert (await supervisor.up("t", tuner=False, visualiser_tap=False)).ok
        off = await supervisor.tap("a")
        await supervisor.down()
        return nothing, off

    nothing, off = run(go())
    assert (nothing.ok, nothing.code) == (False, "not_held")
    assert (off.ok, off.code) == (False, "off") and "--no-visualiser-tap" in off.reason
    assert "pw-loopback" not in world.spawner.commands()


def test_a_tap_that_stops_while_held_is_a_crash_with_the_sound_unaffected_and_is_started_again_when_asked(world):
    tap_world(world)
    go_file = world.root / "go"
    world.spawner.scripts["pw-loopback"] = crash_when(go_file, 1)
    lines: list[str] = []
    supervisor = Supervisor(world.world, say=lines.append)

    async def go():
        assert (await supervisor.up("t", tuner=False)).ok
        assert (await supervisor.tap("a")).ok
        go_file.touch()
        await settle_until(lambda: supervisor.crashed)
        crashed = list(supervisor.crashed)
        go_file.unlink()
        again = await supervisor.tap("a")
        await supervisor.down()
        return crashed, again

    (crash,), again = run(go())
    assert crash["name"] == "vistap" and crash["returncode"] == 1 and crash["phase"] == "running"
    assert crash["label"] == "the visualiser tap (pw-loopback)"
    assert crash["means"].startswith("the visualiser tap stopped: the sound is unaffected")
    assert any("vistap" in line and "Not restarted" in line for line in lines)
    assert again.ok and len(taps_started(world)) == 2, "started again because a page asked"


def test_an_exit_on_purpose_is_never_a_crash_whenever_the_sweep_sees_it(world):
    """The race of PR #200's review: a child stopped on purpose that a sweep
    sees go while it is still held is not a crash."""
    supervisor = Supervisor(world.world, say=lambda m: None)

    async def go():
        assert (await supervisor.up("t")).ok
        child = supervisor.held.children["fmit"]
        child.on_purpose = True
        child.proc.kill()
        child.proc.wait()
        supervisor._sweep(supervisor.held)
        crashed = list(supervisor.crashed)
        await supervisor.down()
        return crashed

    assert run(go()) == []


def test_the_agents_end_stops_the_tap_by_its_pid_and_leaves_the_rig_playing(world):
    tap_world(world)
    supervisor = Supervisor(world.world, say=lambda m: None)

    async def go():
        assert (await supervisor.up("t", tuner=False)).ok
        assert (await supervisor.tap("a")).ok
        tap = supervisor.held.children["vistap"].proc
        sushi = supervisor.held.children["sushi"].proc
        pgid = supervisor.held.pgid
        await supervisor.release()
        return tap, sushi, pgid

    tap, sushi, pgid = run(go())
    try:
        assert tap.poll() is not None, "the tap went with the agent"
        assert sushi.poll() is None and pgid_alive(pgid), "the rig outlives the agent"
    finally:
        os.killpg(pgid, signal.SIGKILL)


def test_restart_takes_the_tap_and_starts_none(world):
    tap_world(world)
    supervisor = Supervisor(world.world, say=lambda m: None)

    async def go():
        assert (await supervisor.up("t", tuner=False)).ok
        assert (await supervisor.tap("a")).ok
        tap = supervisor.held.children["vistap"].proc
        assert (await supervisor.restart()).ok
        await asyncio.sleep(0.2)
        report = supervisor.tap_report()
        await supervisor.down()
        return tap, report

    tap, report = run(go())
    assert tap.poll() is not None and report["running"] is False and len(taps_started(world)) == 1


FAKE_LINK = """import json, os, sys
db = os.environ["FAKE_GRAPH"]
links = json.load(open(db)) if os.path.exists(db) else []
if sys.argv[1:] == ["-l"]:
    for a, b in links:
        print(a); print("  |-> " + b)
    sys.exit(0)
links.append(sys.argv[1:3]); json.dump(links, open(db, "w")); sys.exit(0)
"""
FAKE_LOOPBACK = """import os, sys, time
open(os.environ["FAKE_TAP_ENV"], "w").write(os.environ.get("PIPEWIRE_CONFIG_NAME", ""))
time.sleep(60)
"""


def test_a_tap_is_linked_by_pw_link_from_the_master_and_started_realtime(world, tmp_path, monkeypatch):
    """With fake `pw-loopback` and `pw-link` programs, through the real
    `vistap.link`: the master's two ports into the tap's two, and the tap
    started with the realtime client config."""
    from sushi_rig import vistap

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, text in (("pw-link", FAKE_LINK), ("pw-loopback", FAKE_LOOPBACK)):
        (bin_dir / name).write_text(f"#!{sys.executable}\n{text}")
        (bin_dir / name).chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_GRAPH", str(tmp_path / "graph.json"))
    monkeypatch.setenv("FAKE_TAP_ENV", str(tmp_path / "tap.env"))
    (world.root / "config" / "t.json").write_text(json.dumps(
        {"tracks": [{"name": "master", "outputs": [{"engine_bus": 0, "track_bus": 0}]}]}))
    tap_world(world, link_tap=vistap.link)
    world.spawner.scripts["pw-loopback"] = [str(bin_dir / "pw-loopback")]
    supervisor = Supervisor(world.world, say=lambda m: None)

    async def go():
        assert (await supervisor.up("t", tuner=False)).ok
        answer = await supervisor.tap("a")
        await supervisor.down()
        return answer

    answer = run(go())
    assert answer.ok, answer
    assert json.loads((tmp_path / "graph.json").read_text()) == [
        ["sushi:audio_output_0", "sushi_rig_output_tap:input_FL"],
        ["sushi:audio_output_1", "sushi_rig_output_tap:input_FR"]]
    assert (tmp_path / "tap.env").read_text() == "client-rt.conf"


def test_the_tap_routes_answer_as_the_supervisor_does_and_only_this_machine(world, monkeypatch):
    pytest.importorskip("aiohttp")
    from aiohttp.test_utils import TestClient, TestServer

    from sushi_rig import agent as agent_module
    from sushi_rig.agent import Agent

    tap_world(world)
    supervisor = Supervisor(world.world, say=lambda m: None)
    agent = Agent(supervisor=supervisor)

    async def go():
        async with TestClient(TestServer(agent.app())) as client:
            refused = await client.post("/visualiser/tap", json={"page": "a"})
            refused = (refused.status, await refused.json())
            up = await client.post("/rig/up", json={"config_name": "t", "tuner": False})
            assert up.status == 200, await up.text()
            ok = await client.post("/visualiser/tap", json={"page": "a"})
            ok = (ok.status, await ok.json())
            bad = (await client.post("/visualiser/tap", json={"page": "a b"})).status
            extra = (await client.post("/visualiser/tap", json={"page": "a", "x": 1})).status
            health = await (await client.get("/health")).json()
            released = await client.post("/visualiser/tap/release", json={"page": "a", "now": True})
            released = (released.status, await released.json())
            monkeypatch.setattr(agent_module, "is_local", lambda remote: False)
            far = await client.post("/visualiser/tap", json={"page": "a"})
            far = (far.status, await far.json())
            await client.post("/rig/down")
            agent._executor.shutdown(wait=True)
            return refused, ok, bad, extra, health, released, far

    refused, ok, bad, extra, health, released, far = run(go())
    assert refused[0] == 409 and refused[1]["code"] == "not_held"
    assert ok == (200, {"ok": True, "source": {"node": "sushi_rig_output", "label": "Sushi rig output"},
                        "lease_s": 30.0, "renew_s": 10.0})
    assert bad == extra == 400
    tap = health["agent"]["supervisor"]["visualiser_tap"]
    assert tap["running"] is True and tap["pages"] == 1 and health["agent"]["supervisor"]["holds"]["children"]["vistap"]
    assert released == (200, {"ok": True, "running": False, "stopped": True})
    assert far[0] == 403 and far[1]["code"] == "not_local"


def test_the_load_counts_the_taps_nodes_only_while_it_runs(world):
    """XRUNS and LOAD count the tap while the agent runs one, and not once it
    has stopped (review of PR #200: the tile must not keep rising for a tap
    no page holds)."""
    pytest.importorskip("aiohttp")
    from sushi_rig.agent import Agent

    tap_world(world)
    supervisor = Supervisor(world.world, say=lambda m: None)
    agent = Agent(supervisor=supervisor)
    described = {"running": True, "children": {"sushi": {"pid": 1, "alive": True}}}

    async def go():
        assert (await supervisor.up("t", tuner=False)).ok
        before = agent._load_children(described)
        assert (await supervisor.tap("a")).ok
        during = agent._load_children(described)
        await supervisor.tap_release("a", at_once=True)
        after = agent._load_children(described)
        await supervisor.down()
        agent._executor.shutdown(wait=True)
        return before, during, after

    before, during, after = run(go())
    assert set(before) == {"sushi"} and set(during) == {"sushi", "vistap"} and set(after) == {"sushi"}


def test_no_visualiser_tap_reaches_prepare_and_restart_keeps_it(world):
    supervisor = Supervisor(world.world, say=lambda m: None)

    async def go():
        assert (await supervisor.up("t", visualiser_tap=False)).ok
        assert (await supervisor.restart()).ok
        await supervisor.down()

    run(go())
    assert [a["visualiser_tap"] for a in world.asked] == [False, False]


def test_a_lease_left_from_a_tap_that_crashed_does_not_hold_up_the_next(world):
    """Page a's tap crashed; page b's started anew. a's lease was on the
    dead tap: left, it held the new one up after b let go (review of PR #200)."""
    tap_world(world)
    go_file = world.root / "go"
    world.spawner.scripts["pw-loopback"] = crash_when(go_file, 1)
    supervisor = Supervisor(world.world, say=lambda m: None)

    async def go():
        assert (await supervisor.up("t", tuner=False)).ok
        assert (await supervisor.tap("a")).ok
        go_file.touch()
        await settle_until(lambda: supervisor.crashed)
        go_file.unlink()
        assert (await supervisor.tap("b")).ok
        released = await supervisor.tap_release("b", at_once=True)
        await supervisor.down()
        return released

    assert run(go()) == {"running": False, "stopped": True}


def test_a_release_from_a_page_whose_tap_has_gone_is_still_taken():
    """give_back when no tap runs: the lease goes too (`Leases` is per rig)."""
    from sushi_rig import vistap

    leases = vistap.Leases(30, 60)
    leases.take("a", 0)
    leases.give_back("a", 1)
    assert leases.pages(1) == []


def test_a_tap_that_exits_once_linked_is_refused_as_not_started_and_is_no_crash(world):
    """It exited between the links and the thread check: refused as an exit
    (`did_not_start`), not as `not_realtime`, and no crash recorded."""
    def link_then_die(config):
        tap = supervisor.held.children["vistap"].proc
        tap.kill()
        time.sleep(0.3)  # several watches see it gone, before the start does
        return []

    tap_world(world, link_tap=link_then_die)
    supervisor = Supervisor(world.world, say=lambda m: None)

    async def go():
        assert (await supervisor.up("t", tuner=False)).ok
        answer = await supervisor.tap("a")
        await asyncio.sleep(0.3)  # watches, which would take the exit for a crash
        crashed = list(supervisor.crashed)
        await supervisor.down()
        return answer, crashed

    answer, crashed = run(go())
    assert not answer.ok and answer.code == "did_not_start" and "exited as it started" in answer.reason
    assert crashed == []


def test_the_tap_reads_no_file_on_the_loop(world, monkeypatch):
    """The state file (the running config, the rig's name) is read in the
    worker, not on the event loop, as the tap starts and stops."""
    import threading as threads

    tap_world(world)
    on_loop: list[str] = []
    real = supervisor_module.read_state

    def watched():
        if threads.current_thread() is threads.main_thread():
            on_loop.append("read_state")
        return real()

    supervisor = Supervisor(world.world, say=lambda m: None)

    async def go():
        assert (await supervisor.up("t", tuner=False)).ok
        monkeypatch.setattr(supervisor_module, "read_state", watched)
        assert (await supervisor.tap("a")).ok
        await supervisor.tap_release("a", at_once=True)
        monkeypatch.setattr(supervisor_module, "read_state", real)
        await supervisor.down()

    run(go())
    assert on_loop == []


def test_a_tap_is_recorded_beside_the_state_file_while_it_runs(world):
    from sushi_rig import vistap

    tap_world(world)
    supervisor = Supervisor(world.world, say=lambda m: None)

    async def go():
        assert (await supervisor.up("t", tuner=False)).ok
        assert (await supervisor.tap("a")).ok
        pid, pgid = supervisor.held.children["vistap"].proc.pid, supervisor.held.pgid
        during = vistap.read_record()
        described = rig.describe_rig()
        await supervisor.tap_release("a", at_once=True)
        after = vistap.read_record()
        assert (await supervisor.tap("a")).ok
        await supervisor.down()
        return pid, pgid, during, described, after, vistap.read_record()

    pid, pgid, during, described, after, downed = run(go())
    assert (during["pid"], during["pgid"]) == (pid, pgid) and during["starttime"]
    assert described["visualiser_tap"] == {"pid": pid, "alive": True, "owner_alive": True}
    assert after is None, "removed when it stopped"
    assert downed is None, "and when down stopped the group"


def left_tap(tmp_path, name="pw-loopback"):
    """A process standing in for a tap an agent left: a Python run by a link
    named `name`, so its comm, as /proc says it, is that name."""
    link = tmp_path / name
    if not link.exists():
        link.symlink_to(sys.executable)
    return subprocess.Popen([str(link), "-c", "import time; time.sleep(60)"], start_new_session=True)


def test_a_tap_an_earlier_agent_left_is_stopped_by_its_pid_at_the_next_agents_start(world, tmp_path):
    """The agent died by SIGKILL with a tap running: its leases and watch
    went with it, KillMode=process kept the tap. The next agent finds it by
    its record, checks it is that process, and stops it by pid."""
    from sushi_rig import vistap

    gone = subprocess.Popen([sys.executable, "-c", "pass"])
    gone.wait()
    left = left_tap(tmp_path)
    other = left_tap(tmp_path)
    try:
        vistap.write_record(left.pid, left.pid, owner=gone.pid)  # its agent is gone
        described = rig.describe_rig(
            {"supervisor_pid": left.pid, "pgid": left.pid, "children": {}, "started_at": "x",
             "config_name": "t"},
            alive=lambda pid: True, group_alive=lambda pgid: True)
        supervisor = Supervisor(world.world, say=lambda m: None)
        run(supervisor.tidy_orphan_tap())
        assert left.wait(timeout=10) is not None, "stopped"
        assert vistap.read_record() is None and supervisor.tap_orphan["pid"] == left.pid
        assert supervisor.tap_orphan["stopped"] is True
        assert described["visualiser_tap"] == {"pid": left.pid, "alive": True, "owner_alive": False}
        assert "  vistap     pid %d  (the visualiser's tap on the rig's output)" % left.pid in rig.status_lines(described)

        # A record whose pid is now another process (a reused pid): not touched.
        vistap.write_record(other.pid, other.pid, owner=gone.pid)
        record = vistap.read_record()
        vistap.record_path().write_text(json.dumps({**record, "starttime": record["starttime"] + 1}))
        run(Supervisor(world.world, say=lambda m: None).tidy_orphan_tap())
        assert other.poll() is None, "not the tap it recorded: left alone"
        assert vistap.read_record() is None, "and the stale record cleared"
    finally:
        for proc in (left, other):
            if proc.poll() is None:
                proc.kill()
            proc.wait()


def test_a_tap_whose_agent_still_runs_is_left_to_it(world, tmp_path):
    """A second agent (another port, a dev run) must not stop the tap the
    first holds for its pages: the record names its owner, which still runs."""
    from sushi_rig import vistap

    owner = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    tap = left_tap(tmp_path)
    try:
        vistap.write_record(tap.pid, tap.pid, owner=owner.pid)
        supervisor = Supervisor(world.world, say=lambda m: None)
        run(supervisor.tidy_orphan_tap())
        assert tap.poll() is None and supervisor.tap_orphan is None, "left to the agent that holds it"
        assert vistap.read_record()["pid"] == tap.pid
        owner.kill()
        owner.wait()
        run(supervisor.tidy_orphan_tap())
        assert tap.wait(timeout=10) is not None, "its agent gone: stopped"
    finally:
        for proc in (owner, tap):
            if proc.poll() is None:
                proc.kill()
            proc.wait()


def test_a_recorded_pid_that_is_not_a_pw_loopback_is_never_signalled(world, tmp_path):
    """The start time matches, the name does not: refused, nothing signalled."""
    from sushi_rig import vistap

    gone = subprocess.Popen([sys.executable, "-c", "pass"])
    gone.wait()
    impostor = left_tap(tmp_path, name="not-a-tap")
    try:
        vistap.write_record(impostor.pid, impostor.pid, owner=gone.pid)
        supervisor = Supervisor(world.world, say=lambda m: None)
        run(supervisor.tidy_orphan_tap())
        time.sleep(0.3)
        assert impostor.poll() is None, "not signalled"
        assert supervisor.tap_orphan["stopped"] is False and "not-a-tap" in supervisor.tap_orphan["refused"]
    finally:
        impostor.kill()
        impostor.wait()

def test_a_tap_no_agent_holds_still_counts_in_the_load():
    pytest.importorskip("aiohttp")
    from sushi_rig.agent import Agent

    agent = Agent()
    described = {"running": True, "children": {"sushi": {"pid": 1, "alive": True}},
                 "visualiser_tap": {"pid": 12, "alive": True}}
    try:
        assert set(agent._load_children(described)) == {"sushi", "vistap"}
        assert set(agent._load_children({**described, "visualiser_tap": {"pid": 12, "alive": False}})) == {"sushi"}
    finally:
        agent._executor.shutdown(wait=True)
