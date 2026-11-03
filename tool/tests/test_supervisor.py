"""The agent's supervisor (#29): the agent starts the rig's children, holds
their process group, notices a crash and stops what it holds.

The children are fake, the processes are not. Every command the plan names is
swapped for a Python that sleeps (or exits, for a crash), started for real into
a real process group led by the real holder, so what these pin -- that the
group is the holder's and not the agent's, that `down` takes every member,
that an exit is seen and a stop is not taken for one, that the state file
`status` reads is the one the agent wrote -- is the kernel's answer and not a
mock's. Standing in: Sushi's gRPC, qpwgraph's retries, the patchbay check, the
panel's generation and the amp's knobs, which need the rig's own programs.

NOT covered, and not claimed: starting real Sushi, qpwgraph, fmit, Carla or
open-stage-control this way, under PipeWire. That is the check MANIFEST.md
describes.

Everything a test starts is killed by its recorded process group at the end,
never by pattern.
"""

from __future__ import annotations

import asyncio
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

    def launch(name="t", *, headless=True, tuner=True, children=None, **over):
        plan = children if children is not None else plan_children(
            root, config, None, headless=headless, tuner=tuner)
        fields = dict(
            root=root, config_name=name, config=config, children=plan, headless=headless,
            amp_model=None, amp_values={}, tuner_requested=tuner, amp_requested=False,
            amp_model_requested=None, rig_source=None, machine_file=None,
        )
        fields.update(over)
        return Launch(**fields)

    state = {"launch": launch}

    def prepare(config_name, *, headless, tuner, amp, amp_model, rig, warn):
        asked.append(dict(config_name=config_name, headless=headless, tuner=tuner, amp=amp,
                          amp_model=amp_model, rig=rig))
        return state["launch"](config_name, headless=headless, tuner=tuner)

    def start_qpwgraph(child, root, env, logs, process_group=None):
        return spawner(child["command"], cwd=root, env=env, log=logs / "qpwgraph.log",
                       process_group=process_group)

    made = World(
        prepare=prepare, spawn=spawner, start_qpwgraph=start_qpwgraph,
        sushi_ready=lambda alive: True, generate_panel=lambda command, root, env: (0, ""),
        link_problems=lambda plan, root, running, rig_run: [],
        replay_amp=lambda values: None, shutdown_steps=QUICK_STEPS, watch_interval=0.05,
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


# --------------------------------------------------------------------------- #
# The decisions, pure
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("body, command", [
    ({"config_name": "guitar_vocal"}, "up"),
    ({"config_name": "x", "rig": True, "headless": True, "tuner": False, "amp": False,
      "amp_model": "Some Amp.nam"}, "up"),
    (None, "down"),
    ({"force": True}, "down"),
    ({}, "restart"),
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
            "config_name": body["config_name"], "rig": body.get("rig", False),
            "headless": body.get("headless", False), "tuner": body.get("tuner", True),
            "amp": body.get("amp", True), "amp_model": body.get("amp_model"),
        }
    elif command == "down":
        assert options == {"force": bool((body or {}).get("force"))}
    else:
        assert options == {}


@pytest.mark.parametrize("args", [
    [], ["shell"], ["up"], ["up", "--headless"], ["up", "x", "--force"], ["up", "x", "--amp-model"],
    ["down", "--headless"], ["restart", "now"],
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
        outcome = await supervisor.up("t", headless=True)
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
        world.root, world.root / "config" / "t.json", None, headless=True, tuner=True)]
    assert world.spawner.commands() == [sys.executable, *planned], "the holder, then the plan, in order"
    assert "Rig up: t (headless mode)" in outcome.stdout
    assert world.asked == [dict(config_name="t", headless=True, tuner=True, amp=True,
                                amp_model=None, rig=False)]


def test_the_state_file_the_agent_writes_round_trips_through_status(world, capsys):
    """Same shape as the forked supervisor's: `liveness` says running, and
    `status` and `describe_rig` read it with no change to either."""
    supervisor = Supervisor(world.world, say=lambda m: None)

    async def go():
        assert (await supervisor.up("t", headless=True, tuner=False)).ok
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
    assert recorded["mode"] == "headless" and recorded["tuner_requested"] is False
    assert described["verdict"] == "running"
    assert set(described["children"]) == {"qpwgraph", "sushi"}
    assert all(child["alive"] for child in described["children"].values())
    printed = capsys.readouterr().out
    assert printed.startswith("t (headless mode), up ")
    assert f"  group      {recorded['pgid']}" in printed
    assert "(DEAD)" not in printed
    assert rig.restart_arguments(recorded) == ("t", {
        "headless": True, "tuner": False, "amp": False, "amp_model": None, "rig": False})


def test_panel_mode_generates_the_panel_and_holds_open_stage_control_too(world):
    supervisor = Supervisor(world.world, say=lambda m: None)

    async def go():
        assert (await supervisor.up("t", headless=False)).ok
        names = set(supervisor.held.children)
        recorded = state_file.read_state()
        await supervisor.down()
        return names, recorded

    names, recorded = run(go())
    assert {"listen", "sushi", "open-stage-control"} <= names
    assert "open-stage-control" in recorded["children"]
    assert recorded["mode"] == "panel"


def test_a_second_up_is_refused_by_the_state_file_the_agent_wrote(world, monkeypatch):
    """Not a check of the agent's own: `prepare_launch` refuses a second rig,
    and the agent's rig is a rig like any other to it."""
    world.world = dataclasses.replace(world.world, prepare=rig.prepare_launch)
    monkeypatch.setattr(rig, "checkout_root", lambda: world.root)
    supervisor = Supervisor(world.world, say=lambda m: None)

    async def go():
        first = await supervisor.up("t", headless=True, tuner=False, amp=False)
        second = await supervisor.up("t", headless=True, tuner=False, amp=False)
        await supervisor.down()
        return first, second

    first, second = run(go())
    assert first.ok, first
    assert not second.ok and "a rig is already running" in second.reason


# --------------------------------------------------------------------------- #
# A crash is noticed and said, and nothing is restarted
# --------------------------------------------------------------------------- #

def test_a_child_that_crashes_is_reported_and_not_restarted(world):
    world.spawner.scripts["fmit"] = crasher(3, after=0.3)
    lines: list[str] = []
    supervisor = Supervisor(world.world, say=lines.append)

    async def go():
        assert (await supervisor.up("t", headless=True)).ok
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
        assert (await supervisor.up("t", headless=True, tuner=False)).ok
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

    world.spawner.scripts["fmit"] = crasher(255, after=0.2)
    supervisor = Supervisor(world.world, say=lambda m: None)
    agent = Agent(supervisor=supervisor)

    async def go():
        async with TestClient(TestServer(agent.app())) as client:
            up = await client.post("/rig/up", json={"config_name": "t", "headless": True})
            assert up.status == 200, await up.text()
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
        outcome = await supervisor.up("t", headless=True)
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
        assert (await supervisor.up("t", headless=True)).ok
        pgid = supervisor.held.pgid
        outcome = await supervisor.down()
        return pgid, outcome

    pgid, outcome = run(go())
    assert outcome.ok, outcome
    assert outcome.stdout == "Stopping t (headless mode)...\nRig offline.\n"
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

    world.spawner.scripts["fmit"] = crasher(3, after=1.5)
    supervisor = Supervisor(world.world, say=lambda m: None)

    async def go():
        try:
            assert (await supervisor.up("t", headless=True)).ok
            monkeypatch.setattr(supervisor_module.rig_module, "down", hangs)
            supervisor.timeout = 0.3
            outcome = await supervisor.run(["down"])
            assert outcome.abandoned, outcome
            assert supervisor.held is not None and not supervisor.held.stopping
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
        assert (await supervisor.up("t", headless=True)).ok
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
        assert (await supervisor.up("t", headless=True, tuner=False, amp=False)).ok
        first = supervisor.held.pgid
        outcome = await supervisor.restart()
        second = supervisor.held.pgid
        await supervisor.down()
        return first, second, outcome

    first, second, outcome = run(go())
    assert outcome.ok, outcome
    assert first != second and not pgid_alive(first)
    assert outcome.stdout.startswith("Restarting t...\nStopping t (headless mode)...\nRig offline.\n")
    assert "Rig up: t (headless mode)" in outcome.stdout
    assert world.asked[0] == world.asked[1] == dict(
        config_name="t", headless=True, tuner=False, amp=False, amp_model=None, rig=False)


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
        assert (await first.up("t", headless=True)).ok
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
        assert (await supervisor.up("t", headless=True)).ok
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

    outcome = run(supervisor.up("t", headless=True))
    assert not outcome.ok and "sushi did not start" in outcome.reason
    assert all(proc.poll() is not None for proc in world.spawner.started), "what did start was stopped"
    assert state_file.read_state() is None and supervisor.held is None
    assert supervisor.crashed == [], "a teardown is not a crash"


def test_a_sushi_that_never_answers_fails_up_with_where_to_look(world):
    world.world = dataclasses.replace(world.world, sushi_ready=lambda alive: False)
    supervisor = Supervisor(world.world, say=lambda m: None)
    outcome = run(supervisor.up("t", headless=True))
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
        run(supervisor.up("t", headless=True))
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
        assert (await supervisor.up("t", headless=True, tuner=False)).ok
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
            return await supervisor.run(["up", "t", "--headless"])
        finally:
            release.set()  # before the loop closes, which waits for its threads

    outcome = run(go())
    assert outcome.abandoned
    assert "'up t --headless' did not finish within 0.3s" in outcome.reason
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
            outcome = await supervisor.run(["up", "t", "--headless"])
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
                up = await client.post("/rig/up", json={"config_name": "t", "headless": True})
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
        outcome = await supervisor.up("t", headless=True)
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
    outcome = run(supervisor.up("t", headless=True))
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
        assert (await supervisor.up("t", headless=True)).ok
        state_file.write_state({**state_file.read_state(), "config_name": "u"})
        outcome = await supervisor.restart()
        await supervisor.down()
        return outcome

    outcome = run(go())
    assert outcome.ok and outcome.stdout.startswith("Restarting u...\nStopping u (headless mode)...")
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

    def launch(name="t", *, headless=True, tuner=False, **over):
        plan = plan_children(world.root, world.root / "config" / "t.json", None, headless=headless, tuner=tuner,
                             amp_project=project, amp_model="amp/models/Old.nam", amp_values={"Output Lvl": 6.0})
        fields = dict(
            root=world.root, config_name=name, config=world.root / "config" / "t.json", children=plan,
            headless=headless, amp_model="amp/models/Old.nam", amp_values={"Output Lvl": 6.0},
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
        assert (await supervisor.up("t", headless=True)).ok
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
        assert (await supervisor.up("t", headless=True)).ok
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
        assert (await supervisor.up("t", headless=True)).ok
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
        assert (await supervisor.up("t", headless=True)).ok
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
        no_amp(name, **kw), children=plan_children(world.root, world.root / "config" / "t.json", None,
                                                   headless=True, tuner=False))

    async def go():
        assert (await supervisor.up("t", headless=True)).ok
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
            assert (await client.post("/rig/up", json={"config_name": "t", "headless": True})).status == 200
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
        assert (await supervisor.up("t", headless=True)).ok
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
        assert (await supervisor.up("t", headless=True)).ok
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
        assert (await supervisor.up("t", headless=True)).ok
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
