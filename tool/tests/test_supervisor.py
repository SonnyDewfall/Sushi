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

pytestmark = pytest.mark.skipif(
    sys.version_info < (3, 11), reason="the agent's supervisor needs subprocess's process_group",
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
        supervisor_pid=2, pgid=2, headless=True, config_name="t", config=Path("c"),
        children={}, log_dir=Path("l"), started_at="s"))
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


def test_a_group_stopped_outside_the_agent_is_a_stop_not_a_crash(world):
    """`sushi-rig down --direct` signals the group from the state file: the
    holder dies of it at once, and the exits that follow are that stop."""
    lines: list[str] = []
    supervisor = Supervisor(world.world, say=lines.append)

    async def go():
        assert (await supervisor.up("t", headless=True)).ok
        os.killpg(supervisor.held.pgid, signal.SIGTERM)
        await settle_until(lambda: supervisor.held is None)
        return supervisor.describe()

    described = run(go())
    assert described["crashed"] == []
    assert "signalled from outside the agent" in described["note"]


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
    group lives on while a slower member does, so `down`'s escalation is
    still what ends it."""
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
    assert sushi.returncode == -signal.SIGKILL, "it took the last step to end it"
