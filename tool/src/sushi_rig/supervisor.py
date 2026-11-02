"""The agent's supervisor (#29): the agent starts the rig's children itself
and holds their process group, so a phone can press start.

`up` from the CLI forks a supervisor that leads the group and then waits
(`rig._supervise`). The agent cannot do that: it serves HTTP from an event
loop with worker threads, and a fork from a threaded process inherits whatever
locks another thread held at that instant. So the agent never forks Python.
It starts processes the way `subprocess` does, with a fork and an immediate
exec in C, which is safe from a threaded process, and it arranges the group
around them:

- **A holder leads the group.** The first process started is a tiny Python
  that does nothing but `pause()`, started with `process_group=0` so that it
  leads a new group whose id is its pid. Every child is then started with
  `process_group=<that id>`. The holder is what the state file records as
  `supervisor_pid`, and the group id is its pid, so `liveness`, `describe_rig`,
  `status` and `down` read the file exactly as they read one the forked
  supervisor wrote. The agent itself is not in the group: `down` signals the
  group, and the agent must survive its own `down`.
- **Why not the agent as leader, or a child as leader.** A group can only be
  joined from inside the same session, so the holder stays in the agent's
  session rather than calling `setsid`; and the agent cannot lead the group it
  signals. A child as leader would make `status` call the rig orphaned the
  moment that child died, which is a crash report, not an orphan.
- **The children are the agent's own.** Each is a direct child, so the agent
  can read its exit status: a child that exits while the rig is up, with no
  `down` asking it to, is noticed within `WATCH_INTERVAL` and reported in
  `/health` (`agent.supervisor.crashed`) and on stderr. Each child is judged
  on its own: the holder dying (a `kill` of the recorded supervisor pid, the
  OOM killer) is a problem of its own in `agent.supervisor.problems`, and
  says nothing about the children. Nothing is restarted. Principle 9: the
  agent heals nothing on its own, and the answer to a crash is `restart`,
  which the player asks for.
- **`subprocess.Popen`, not asyncio's subprocesses.** asyncio's subprocess
  transport kills a child that is still running when the transport is closed
  or collected, and at the end of the event loop it is: observed on Python
  3.13 on 2026-11-02 (a `sleep` started with `create_subprocess_exec` was
  killed when `asyncio.run` returned; on 3.11 it survived). The rig must not
  die because the agent stopped. A `Popen` never kills its child, so the
  children are `Popen`s and a task on the loop polls them.
- **The rig outlives the agent.** When the agent stops, the holder and the
  children carry on, and the state file still describes them. The next agent
  does not hold that rig -- it was not started by this process, so its exit
  statuses are someone else's to read -- but `down` and `restart` still act on
  it through the state file (`rig.down`), as they do on a rig `up` started
  with no agent running. Crashes in such a rig show as `DEAD` children in
  `/rig`, from probing their pids, rather than in `crashed`.

**The amp alone can be restarted** (`restart_amp`, for the agent's POST
/amp/model): Carla is the one child whose state is chosen at start (its
model, by the project it loads) and that can be stopped and started again
inside the held group without taking the rest of the rig down. It is
stopped by its own pid, never the group (the group is the whole rig), then
started again by `rig.start_child` and given its knobs by `World.replay_amp`,
the path `start_children` takes, and its exit on the way is not a crash.
Nothing else is ever restarted this way.

What runs is not decided here. `rig.prepare_launch` refuses, compiles and
plans (`plan_children` stays the one planner); `rig.start_children` starts
and checks the children, for both supervisors, here in a worker thread with
the group bound into its `spawn`; `rig.state_record` says what the state file
holds; `rig.down` stops a rig, the held one included. This module decides how
the children are grouped, watched and bounded in time, and its effects are
injectable (`World`) so the lifecycle is tested with fake children.
"""

from __future__ import annotations

import asyncio
import dataclasses
import functools
import io
import os
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Sequence

from . import rig as rig_module
from .paths import log_dir
from .procs import SHUTDOWN_STEPS, pgid_alive, stop_group
from .qpwgraph import start_qpwgraph
from .state import read_state, write_state

# How often the agent looks at its children. A crash is in /health within
# two of these (see `_watch`); the cost is a `waitpid(WNOHANG)` per child per tick.
WATCH_INTERVAL = 0.2

# The group holder: a Python that waits for a signal and dies of it. `-I -S`
# so it imports nothing from the environment or the site. SIGINT and SIGTERM
# are set to their defaults because a process started with either ignored
# (a background job of a non-interactive shell ignores SIGINT) passes that
# on, and a holder that outlived the polite signals would hold `down` for the
# whole escalation.
HOLDER: tuple[str, ...] = (
    sys.executable, "-I", "-S", "-c",
    "import signal; signal.signal(signal.SIGINT, signal.SIG_DFL); "
    "signal.signal(signal.SIGTERM, signal.SIG_DFL); signal.pause()",
)

# How long one lifecycle step (`up`, `down` or `restart`) may take before the
# agent stops waiting and answers 504. Each part of a start is bounded on its
# own (Sushi's gRPC 15 s, qpwgraph's attempts, the patchbay check 10 s, the
# panel 60 s, the amp 5 s), but what runs before them -- compiling the rig,
# reading the PipeWire graph, preparing the amp, clearing a stale qpwgraph --
# is not, and a hang there would hold the lifecycle lock, and with it every
# /rig/* request and every Program Change, until the agent restarted. The
# limit the CLI child process had before #29, restored around the step.
LIFECYCLE_TIMEOUT = 120.0


@dataclass(frozen=True)
class Outcome:
    """What a lifecycle command said: its exit status and both streams.

    `returncode` is None when the agent stopped waiting: the command was
    abandoned, not finished, and whatever it had started may still be running.
    """

    ok: bool
    returncode: int | None
    stdout: str
    stderr: str

    @property
    def abandoned(self) -> bool:
        return self.returncode is None

    @property
    def reason(self) -> str:
        """Why it failed, in its own words where it gave any."""
        return self.stderr.strip() or f"exit status {self.returncode}"


def parse_lifecycle(args: Sequence[str]) -> tuple[str, dict[str, Any]]:
    """The command and keyword arguments for one lifecycle step, from the CLI
    arguments `agent.lifecycle_arguments` builds; `ValueError` for anything
    else. Pure, and the exact inverse of that function, so the steps the agent
    plans (a route, a Program Change switch, and whatever later routes call
    `_run_lifecycle`) run unchanged whether the CLI or this module runs them.
    """
    args = list(args)
    if not args:
        raise ValueError("no lifecycle command")
    command, rest = args[0], args[1:]
    if command == "up":
        if not rest or rest[0].startswith("-"):
            raise ValueError("'up' needs a config name first")
        options: dict[str, Any] = {
            "config_name": rest[0], "rig": False, "headless": False,
            "tuner": True, "amp": True, "amp_model": None,
        }
        flags = {
            "--rig": ("rig", True), "--headless": ("headless", True),
            "--no-tuner": ("tuner", False), "--no-amp": ("amp", False),
        }
        i = 1
        while i < len(rest):
            if rest[i] in flags:
                key, value = flags[rest[i]]
                options[key] = value
                i += 1
            elif rest[i] == "--amp-model" and i + 1 < len(rest):
                options["amp_model"] = rest[i + 1]
                i += 2
            else:
                raise ValueError(f"'up' does not take {rest[i]!r}")
        return "up", options
    if command == "down" and rest in ([], ["--force"]):
        return "down", {"force": rest == ["--force"]}
    if command == "restart" and not rest:
        return "restart", {}
    raise ValueError(f"not a lifecycle command: {' '.join(args)!r}")


def exit_record(
    name: str, pid: int, returncode: int, *, at: str, phase: str,
    config_name: str, log: Path | None,
) -> dict[str, Any]:
    """One child's exit, as /health shows it. A negative status is the signal
    that killed it, named, since "-11" is not something a player can read."""
    try:
        killed_by = signal.Signals(-returncode).name if returncode < 0 else None
    except ValueError:
        killed_by = None
    return {
        "name": name, "pid": pid, "returncode": returncode, "signal": killed_by,
        "at": at, "phase": phase, "config_name": config_name,
        "log": str(log) if log is not None else None,
    }


def crash_message(record: dict[str, Any]) -> str:
    """The stderr line for a crash: what died, how, where its log is, and that
    nothing was done about it, so a journal read later says the whole story."""
    how = f"was killed by {record['signal']}" if record["signal"] else f"exited with status {record['returncode']}"
    when = "while it was starting" if record["phase"] == "startup" else "while it was up"
    log = f"; its log is {record['log']}" if record["log"] else ""
    return (
        f"{record['name']} (pid {record['pid']}) {how} {when} in {record['config_name']}{log}. "
        "Not restarted: the agent heals nothing on its own (principle 9). "
        "POST /rig/restart brings the rig back."
    )


# --------------------------------------------------------------------------- #
# The effects, injectable
# --------------------------------------------------------------------------- #

def _spawn(
    command: Sequence[str], *, cwd: Path, env: dict[str, str], log: Path | None,
    process_group: int,
) -> subprocess.Popen:
    """Start one process into a process group, its output to `log`."""
    if log is None:
        return subprocess.Popen(
            list(command), cwd=cwd, env=env, stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            process_group=process_group,
        )
    with open(log, "wb") as sink:
        return subprocess.Popen(
            list(command), cwd=cwd, env=env, stdin=subprocess.DEVNULL,
            stdout=sink, stderr=sink, process_group=process_group,
        )


def group_members(pgid: int) -> list[int]:
    """Every process in group `pgid`, from /proc: what is left when a group
    would not stop, said by pid. Empty where /proc cannot be read."""
    members = []
    try:
        entries = os.listdir("/proc")
    except OSError:
        return []
    for entry in entries:
        if not entry.isdigit():
            continue
        try:
            with open(f"/proc/{entry}/stat") as stat:
                # "pid (comm) state ppid pgrp ...": comm may hold spaces and
                # parentheses, so split after the last ')'.
                fields = stat.read().rsplit(")", 1)[1].split()
        except (OSError, IndexError):
            continue
        if len(fields) > 2 and fields[2] == str(pgid):
            members.append(int(entry))
    return sorted(members)


def child_pids(pid: int) -> list[int]:
    """Every process whose parent is `pid`, and theirs, from /proc: what a
    child started of its own, which signalling the child alone would leave.
    Empty where /proc cannot be read."""
    parents: dict[int, list[int]] = {}
    try:
        entries = os.listdir("/proc")
    except OSError:
        return []
    for entry in entries:
        if not entry.isdigit():
            continue
        try:
            with open(f"/proc/{entry}/stat") as stat:
                fields = stat.read().rsplit(")", 1)[1].split()
        except (OSError, IndexError):
            continue
        if len(fields) > 1 and fields[1].isdigit():
            parents.setdefault(int(fields[1]), []).append(int(entry))
    found, todo = [], [pid]
    while todo:
        for kid in parents.get(todo.pop(), []):
            if kid not in found:
                found.append(kid)
                todo.append(kid)
    return sorted(found)


def running(pid: int) -> bool:
    """Whether `pid` is a process that has not exited: alive, and not a
    zombie. A helper whose parent died before reaping it is reparented to
    whatever reaps orphans, and where nothing does (a process running as pid
    1 in a container, as CI runs the tests) it stays a zombie for good: dead,
    though `kill(pid, 0)` still finds it."""
    from .procs import pid_alive

    try:
        with open(f"/proc/{pid}/stat") as stat:
            return stat.read().rsplit(")", 1)[1].split()[0] != "Z"
    except (OSError, IndexError):
        return pid_alive(pid)


def stop_child(proc: subprocess.Popen, steps: tuple) -> list[int]:
    """Stop one child of the rig, and what it started, by pid, through the
    shutdown escalation `steps`: the pids still alive afterwards, empty when
    every one has gone. Not `stop_group`: the group is the whole rig.

    What it started is found first, since once the child has gone they are
    no longer its children; each is signalled with it, so a helper Carla
    spawned is not left holding the amp's JACK client or its OSC port while
    the next Carla tries to take them."""
    kids = child_pids(proc.pid)
    for sig, wait in steps:
        for pid in [proc.pid, *kids]:
            try:
                os.kill(pid, sig)
            except (ProcessLookupError, PermissionError):
                pass
        deadline = time.monotonic() + wait
        while time.monotonic() < deadline:
            if proc.poll() is not None and not any(running(kid) for kid in kids):
                return []
            time.sleep(0.05)
    proc.poll()
    return [pid for pid in [proc.pid, *kids] if (pid != proc.pid or proc.returncode is None) and running(pid)]


@dataclass(frozen=True)
class AmpRestart:
    """How `Supervisor.restart_amp` went. `ok` is the new model playing,
    replayed, with `pid` its Carla. Otherwise `reason` says why, and
    `restored` whether the previous model was brought back (True), could not
    be (False), or was never taken away (None: refused before Carla was
    touched). `code` says which refusal or failure, for the agent's answer:
    `not_held`, `no_amp`, `project_unreadable`, `no_state` (refused);
    `would_not_stop`, `did_not_start` (failed). `problems` are said either
    way: links the patchbay did not remake, a log to read."""

    ok: bool
    reason: str | None = None
    pid: int | None = None
    restored: bool | None = None
    problems: tuple[str, ...] = ()
    code: str | None = None


@dataclass(frozen=True)
class World:
    """What the supervisor reaches outside itself, so its tests can stand each
    in: planning a rig, starting a process, qpwgraph, Sushi's gRPC, the panel,
    the patchbay check, the amp's knobs and stopping a group. The defaults are
    the ones `up`'s forked supervisor uses."""

    prepare: Callable[..., "rig_module.Launch"] = rig_module.prepare_launch
    spawn: Callable[..., subprocess.Popen] = _spawn
    start_qpwgraph: Callable[..., "subprocess.Popen | None"] = start_qpwgraph
    sushi_ready: Callable[["Callable[[], bool] | None"], bool] = rig_module.sushi_ready
    generate_panel: Callable[[list[str], Path, dict[str, str]], tuple[int, str]] = rig_module.generate_panel
    link_problems: Callable[[list, Path, set, bool], list[str]] = rig_module.link_problems
    replay_amp: Callable[[dict[str, float]], None] = rig_module._replay_amp
    stop_group: Callable[..., bool] = stop_group
    log_dir: Callable[[], Path] = log_dir
    holder: tuple[str, ...] = HOLDER
    shutdown_steps: tuple = SHUTDOWN_STEPS
    watch_interval: float = WATCH_INTERVAL


# --------------------------------------------------------------------------- #
# What the agent holds
# --------------------------------------------------------------------------- #

def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Child:
    name: str
    proc: subprocess.Popen
    log: Path | None
    exit: dict[str, Any] | None = None


@dataclass
class Held:
    """A rig this agent started: its holder, its children, and where it is in
    its life. `stopping` is set by the agent, and only by the agent, before it
    signals the group, so the exits that follow are a `down`, not crashes.
    `stuck` is a start whose teardown left processes in the group."""

    launch: "rig_module.Launch"
    holder: subprocess.Popen
    logs: Path
    children: dict[str, Child] = field(default_factory=dict)
    ready: bool = False
    stopping: bool = False
    stuck: bool = False
    record: dict[str, Any] | None = None  # the state file, as written
    holder_exit: dict[str, Any] | None = None
    startup_exits: list[dict[str, Any]] = field(default_factory=list)  # the start's, not crashes
    task: "asyncio.Task | None" = None
    # Children being stopped on purpose, one at a time (`restart_amp`): their
    # exit is the restart's, not a crash, as `stopping` is for the whole rig.
    restarting: set = field(default_factory=set)

    @property
    def pgid(self) -> int:
        return self.holder.pid

    def add(self, name: str, proc: subprocess.Popen, log: Path | None) -> None:
        # Called from the start's worker thread; a dict assignment, which the
        # loop's snapshots (`list(...)`) never see half-done.
        self.children[name] = Child(name, proc, log)

    def provisional(self) -> dict[str, Any]:
        """A state record for what is running now: written as the state file
        once the start succeeds, and what `rig.down` is handed for a held
        rig whose file is gone or was never written."""
        return rig_module.state_record(
            self.launch, supervisor_pid=self.pgid, pgid=self.pgid,
            children={name: child.proc.pid for name, child in list(self.children.items())},
            log_dir=self.logs, started_at=datetime.now(timezone.utc).isoformat(),
        )


class Supervisor:
    """Starts, watches and stops the rig the agent holds; acts through the
    state file on a rig it does not hold. One lifecycle command at a time is
    the agent's lock to keep (`Agent._run_lifecycle`), not this class's; this
    class makes sure no command holds that lock for longer than `timeout`."""

    def __init__(
        self, world: World | None = None, say: Callable[[str], None] | None = None,
        timeout: float = LIFECYCLE_TIMEOUT,
    ) -> None:
        self.world = world or World()
        self.timeout = timeout
        self.held: Held | None = None
        # Exits of the held rig's children that no `down` caused, newest last.
        # Kept after the rig is stopped, so a crash is still readable after the
        # `down` that followed it; cleared when the agent next starts a rig.
        self.crashed: list[dict[str, Any]] = []
        # What is wrong beyond a child's exit: the holder lost, a start that
        # left processes behind. Cleared, like `crashed`, by the next start.
        self.problems: list[dict[str, Any]] = []
        self.doing: str | None = None  # what the step running now is doing, for a 504
        self._torn: str | None = None  # what a cancelled start's teardown said
        self._say = say or (lambda message: print(f"sushi-rig agent: {message}", file=sys.stderr, flush=True))

    # ------------------------------------------------------------- the runner

    async def run(self, args: Sequence[str]) -> Outcome:
        """One lifecycle step, given as the CLI arguments `_run_lifecycle`
        hands its runner. A refusal or a failure the player must fix is an
        unsuccessful `Outcome` with the reason, as the CLI's exit was; a bug
        is raised, so the agent answers 500 and remembers it.

        Bounded by `timeout`: past it the agent stops waiting, stops what the
        step had started, and answers an abandoned `Outcome` (504) saying what
        was still running. That part runs in a worker thread, which cannot be
        interrupted from Python; it is left to finish, and whatever it returns
        is dropped (a start told it is abandoned starts nothing more)."""
        from .amp import AmpError

        try:
            command, options = parse_lifecycle(args)
        except ValueError as exc:
            return Outcome(False, 2, "", str(exc))
        said = " ".join(args)
        self.doing = f"running '{said}'"
        task = asyncio.ensure_future(self._dispatch(command, options))
        try:
            done, _ = await asyncio.wait({task}, timeout=self.timeout)
        except asyncio.CancelledError:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            raise
        if task in done:
            self.doing = None
            try:
                return task.result()
            except (rig_module.RigError, AmpError) as exc:
                return Outcome(False, 1, "", str(exc))
        doing = self.doing
        self._torn = None
        task.cancel()
        # The start's teardown runs in the cancelled task, and is bounded by
        # the shutdown escalation.
        await asyncio.gather(task, return_exceptions=True)
        self.doing = None
        torn = self._torn
        return Outcome(False, None, "", (
            f"'{said}' did not finish within {self.timeout:g}s, so the agent stopped waiting "
            f"for it. It was {doing}, which cannot be interrupted from here and is left to "
            "finish on its own; nothing it does now is kept. "
            + (torn or ("Nothing had been started." if command == "up" else ""))
            + " Read /rig."
        ))

    async def _dispatch(self, command: str, options: dict[str, Any]) -> Outcome:
        if command == "up":
            return await self.up(**options)
        if command == "down":
            return await self.down(**options)
        return await self.restart()

    # --------------------------------------------------------------------- up

    async def up(
        self, config_name: str, *, rig: bool = False, headless: bool = False,
        tuner: bool = True, amp: bool = True, amp_model: str | None = None,
    ) -> Outcome:
        """Start a rig and hold it. Refused, compiled and planned by
        `rig.prepare_launch`, which refuses a second rig (the agent's own
        included, through the state file it wrote) before anything starts."""
        warnings: list[str] = []
        if sys.version_info < (3, 11):
            # `process_group` arrived in 3.11; without it a child cannot be put
            # into the holder's group except by running Python between fork and
            # exec, which is the deadlock this module exists to avoid.
            return Outcome(False, 1, "", (
                "the agent can start a rig only on Python 3.11 or later (it needs "
                f"subprocess's process_group); this is {sys.version.split()[0]}. "
                "'sushi-rig up --direct' starts one without the agent."
            ))
        self.doing = "preparing the rig (rig.prepare_launch: compiling it, reading the graph, the amp, the plan)"
        try:
            launch = await asyncio.to_thread(
                self.world.prepare, config_name, headless=headless, tuner=tuner, amp=amp,
                amp_model=amp_model, rig=rig, warn=warnings.append,
            )
        except Exception as exc:
            if not _plain(exc):
                raise
            # A refusal keeps the warnings said on the way to it.
            return Outcome(False, 1, "", _with_warnings(warnings, str(exc)))
        return await self._start(launch, warnings)

    async def _start(self, launch: "rig_module.Launch", warnings: list[str]) -> Outcome:
        world = self.world
        logs = world.log_dir()
        logs.mkdir(parents=True, exist_ok=True)
        env = _rig_env(launch)
        holder = world.spawn(world.holder, cwd=launch.root, env=env, log=None, process_group=0)
        held = Held(launch=launch, holder=holder, logs=logs)
        self.held, self.crashed, self.problems = held, [], []
        held.task = asyncio.create_task(self._watch(held))
        abandoned = threading.Event()

        def step(doing: str) -> None:
            self.doing = doing

        try:
            _, problems, failure = await asyncio.to_thread(
                rig_module.start_children, launch, env=env, logs=logs,
                spawn=functools.partial(world.spawn, process_group=held.pgid),
                start_qpwgraph=functools.partial(world.start_qpwgraph, process_group=held.pgid),
                sushi_ready=world.sushi_ready, generate_panel=world.generate_panel,
                link_problems=world.link_problems, replay_amp=world.replay_amp,
                on_start=held.add, step=step, abandoned=abandoned.is_set,
            )
            if failure is None:
                record = held.provisional()
                write_state(record)
                held.record = record
                # Exits not yet seen happened while it started: the start's,
                # warned of above, and now a rig's crashes like any other,
                # since there is a state file for `restart` to bring back.
                self._sweep(held)
                held.ready = True
                for exited in held.startup_exits:
                    self.crashed.append(exited)
                    self._say(crash_message(exited))
        except BaseException:
            # Whatever did start must not be left running, even on a bug or a
            # cancellation (the step's deadline); the exception is then the
            # caller's to report.
            abandoned.set()
            self._torn = await self._tear_down(held)
            raise
        if failure is not None:
            # A child that exited during a start that failed belongs to the
            # failure: there is no rig to restart, so it is said here and not
            # recorded as a crash.
            self._sweep(held)
            said = " ".join([failure, *problems]).lower()
            also = [
                f"{e['name']} {_how(e)} while starting; see {e['log']}"
                for e in held.startup_exits if e["name"].lower() not in said
            ]
            torn = await self._tear_down(held)
            reason = failure + "".join(f"\n{line}" for line in also) + (f"\n{torn}" if torn else "")
            return Outcome(False, 1, "", _with_warnings(warnings + problems, reason))
        return Outcome(
            True, 0, rig_module.up_summary(launch.config_name, launch.headless, held.record or {}),
            "".join(f"warning: {w}\n" for w in warnings + problems),
        )

    # ------------------------------------------------------------------- down

    async def down(self, force: bool = False) -> Outcome:
        """Stop the rig, through `rig.down`, the CLI's own. For the rig this
        agent holds it is handed the state file when that names the held
        group, and the agent's own record of the group when the file is gone
        or was never written, so what the agent holds is stopped whatever the
        file says. Any other rig is stopped through the state file, as the CLI
        stops it: one started without the agent, or by an agent before this."""
        held = self.held
        state = None
        if held is not None and pgid_alive(held.pgid):
            recorded = read_state()
            state = recorded if recorded and recorded.get("pgid") == held.pgid else held.provisional()
            held.stopping = True
        self.doing = "stopping the rig (rig.down: the shutdown escalation)"
        out, err = io.StringIO(), io.StringIO()
        # In a thread: the escalation sleeps. Exited children are reaped by the
        # watch on the loop meanwhile, since a zombie still counts as a member
        # of its group and the escalation would otherwise run in full.
        code = None
        try:
            code = await asyncio.to_thread(lambda: rig_module.down(force, out=out, err=err, state=state))
        finally:
            # A stop that failed, or was abandoned at LIFECYCLE_TIMEOUT (the
            # await is cancelled, so nothing after it runs), leaves the rig
            # watched again: left `stopping`, every later crash of a rig still
            # playing would pass as part of the stop and /health would say
            # nothing. A crash wrongly reported is the safer mistake.
            if held is not None and code != 0:
                held.stopping = False
        if held is not None and code == 0:
            await self._settle(held)
            self.problems = [p for p in self.problems if p["kind"] != "stuck"]
        return Outcome(code == 0, code, out.getvalue(), err.getvalue())

    async def restart_amp(
        self, project: str, values: dict[str, float], *, model: str, override: str,
    ) -> AmpRestart:
        """Restart Carla alone, in the held group, on another model: the rest
        of the rig plays on, and the sound stops only for as long as Carla
        takes to stop and start (not measured: no Carla was to hand).

        1. Refused, with Carla untouched, unless this agent holds a rig that
           is up and planned an amp, its project can be read (to put back),
           and the state file names the held group: the run as it is now,
           a live switch's config included, is what the new model is
           recorded into, never the rig `up` first started.
        2. Carla is stopped by its pid and what it started (`stop_child`),
           its exit marked as the restart's. One that will not stop is a
           failure, and nothing is started over it.
        3. `project` (amp.carla_project's text) replaces the planned project
           file, and Carla is started from it by `rig.start_child`, into the
           group, as `start_children` starts it; `World.replay_amp` sends
           `values` once its OSC answers. Carla exiting, or its OSC never
           answering, is a failure.
        4. On a failure after Carla was stopped, the previous project is put
           back and Carla is started from it the same way, so the player
           gets the old amp back rather than none; `restored` says whether
           that worked.
        5. Then the patchbay's links are checked (`World.link_problems`,
           which waits for them): qpwgraph keeps its patch, so it should wire
           the new Carla's ports as they appear, and a link it did not remake
           is a guitar that is dry or silent, said in `problems`.
        6. On success the state file is rewritten (`rig.amp_restarted_state`)
           when it names the held group, and the held launch follows, so
           `restart` keeps the model and /health names the new pid.

        The caller holds the agent's lifecycle lock; this bounds itself by the
        shutdown escalation and replay_amp's own wait, twice at most."""
        held = self.held
        if held is None or not held.ready or held.stopping:
            return AmpRestart(False, (
                "this agent does not hold a running rig, so it cannot restart its amp: a rig "
                "started with 'up --direct', or by an agent before this one, is restarted whole "
                "(POST /rig/restart)"), code="not_held")
        planned = next((c for c in held.launch.children if c.get("name") == "amp"), None)
        path = rig_module.amp_project_of(held.launch.children)
        child = held.children.get("amp")
        if planned is None or path is None or child is None:
            return AmpRestart(False, "the rig was started with no amp, so there is no Carla to restart",
                              code="no_amp")
        world = self.world
        try:
            previous, recorded = await asyncio.to_thread(lambda: (path.read_text(), read_state()))
        except OSError as exc:
            return AmpRestart(False, f"the amp's project {path} cannot be read ({exc}), so the model "
                                     "it plays could not be put back: not restarted",
                              code="project_unreadable")
        if not recorded or recorded.get("pgid") != held.pgid:
            return AmpRestart(False, (
                "the state file does not name the rig this agent holds (it is gone, or names group "
                f"{(recorded or {}).get('pgid')} and not {held.pgid}), so the new model could not be "
                "recorded for restart to keep: not restarted. POST /rig/restart writes it again"),
                code="no_state")
        env = _rig_env(held.launch)
        held.restarting.add("amp")
        try:
            self.doing = f"stopping the amp (Carla, pid {child.proc.pid})"
            left = await asyncio.to_thread(stop_child, child.proc, world.shutdown_steps)
            self._sweep(held)
            if left:
                return AmpRestart(False, (
                    f"Carla would not stop: pid(s) {', '.join(map(str, left))} are still running, so "
                    "no new Carla was started over it. The amp may be half there: POST /rig/restart"),
                    restored=False, code="would_not_stop")
            failure = await self._start_amp(held, planned, env, path, project, values)
            if failure is None:
                problems = await self._amp_links(held)
                await self._record_amp(held, recorded, model=model, override=override)
                return AmpRestart(True, pid=held.children["amp"].proc.pid, problems=tuple(problems))
            self._say(f"the amp did not come up on {model}: {failure}; putting the previous model back")
            back = await self._start_amp(held, planned, env, path, previous, values)
            if back is None:
                problems = await self._amp_links(held)
                await self._record_amp(held, recorded, model=None, override=None)
                return AmpRestart(False, failure, pid=held.children["amp"].proc.pid, restored=True,
                                  problems=tuple(problems), code="did_not_start")
            said = (f"the amp did not come back on its previous model either: {back}. There is NO AMP "
                    "in the signal path, so the guitar is running dry: POST /rig/restart")
            self._say(said)
            self.problems.append({"kind": "no_amp", "at": _now(), "message": said})
            return AmpRestart(False, f"{failure}; and {said}", restored=False, code="did_not_start")
        finally:
            # Seen while still marked, so a Carla that this restart stopped,
            # or that failed to come up, is the restart's and not a crash.
            self._sweep(held)
            held.restarting.discard("amp")
            self.doing = None

    async def _start_amp(
        self, held: Held, planned: dict[str, Any], env: dict[str, str], path: Path, project: str,
        values: dict[str, float],
    ) -> str | None:
        """Write `project`, start Carla from it into the held group and replay
        `values`: None once it plays, or what went wrong (with anything that
        did start stopped again, so a second try starts clean)."""
        world = self.world
        self.doing = "starting the amp (Carla) and restoring its knobs"

        def start() -> str | None:
            path.write_text(project)
            try:
                proc, log = rig_module.start_child(
                    planned, root=held.launch.root, env=env, logs=held.logs,
                    spawn=functools.partial(world.spawn, process_group=held.pgid))
            except OSError as exc:
                return f"Carla did not start: {exc}"
            held.add("amp", proc, log)
            try:
                world.replay_amp(dict(values))
                failed = None
            except Exception as exc:  # noqa: BLE001 -- said, and the old model is put back
                failed = f"its knobs could not be restored ({exc})"
            if proc.poll() is not None:
                # Not `startup_exit_problem`'s words, which say there is no
                # amp: the caller may yet bring the previous model back.
                failed = f"Carla exited as it started (status {proc.returncode}): see {log}"
            if failed is not None and proc.poll() is None:
                stop_child(proc, world.shutdown_steps)
            return failed

        return await asyncio.to_thread(start)

    async def _amp_links(self, held: Held) -> list[str]:
        """The patchbay's links, checked as `start_children` checks them once
        the rig is up: only when qpwgraph is one of the children running."""
        running = {name for name, child in list(held.children.items())
                   if child.exit is None and child.proc.poll() is None}
        if "qpwgraph" not in running:
            return []
        self.doing = "checking the patchbay's links were made to the new Carla"
        try:
            return await asyncio.to_thread(
                self.world.link_problems, held.launch.children, held.launch.root, running,
                held.launch.rig_source is not None)
        except Exception as exc:  # noqa: BLE001 -- a check that cannot run is said, not fatal
            return [f"could not check the patchbay's links after the amp restarted: {exc}"]

    async def _record_amp(
        self, held: Held, recorded: dict[str, Any], *, model: str | None, override: str | None,
    ) -> None:
        """The state file, and the held launch, after Carla was restarted: the
        new pid always, and the model when it changed (None: it was put back).

        Written over the run as it is now: the state file as it reads now if
        it still names the held group, else as it read when the restart began
        (`recorded`, checked then to name it). Never `held.provisional()`,
        which is the rig `up` started, with a fresh `started_at`: after a live
        switch that would bring the switched-from rig back on `restart`, and
        reseed the amp from its config. Off the loop: it reads and writes."""
        pid = held.children["amp"].proc.pid

        def write() -> dict[str, Any]:
            now = read_state()
            base = now if now and now.get("pgid") == held.pgid else recorded
            if model is None:
                updated = {**base, "children": {**(base.get("children") or {}), "amp": pid}}
            else:
                updated = rig_module.amp_restarted_state(base, model=model, override=override, pid=pid)
            write_state(updated)
            return updated

        held.record = await asyncio.to_thread(write)
        if model is not None:
            held.launch = dataclasses.replace(held.launch, amp_model=model, amp_model_requested=override)

    async def restart(self) -> Outcome:
        """`rig.restart`'s steps with the agent's own `down` and `up`, and its
        words. What to bring back is read from the state file first, as the
        CLI reads it -- a live switch (#93) rewrites the file to the rig it
        switched to -- and from the agent's record of a held rig whose file
        is gone."""
        held = self.held
        state = read_state() or (held.record if held is not None else None)
        if not state:
            return Outcome(False, 1, "", rig_module.NOTHING_TO_RESTART)
        config_name, options = rig_module.restart_arguments(state)
        said = rig_module.restart_heading(config_name) + "\n"
        stopped = await self.down()
        if not stopped.ok:
            return Outcome(False, stopped.returncode, said + stopped.stdout,
                           _joined(stopped.stderr, rig_module.NOT_RESTARTED))
        started = await self.up(config_name, **options)
        return Outcome(started.ok, started.returncode, said + stopped.stdout + started.stdout,
                       stopped.stderr + started.stderr)

    # -------------------------------------------------------------- watching

    def _sweep(self, held: Held) -> None:
        """Look at each process once. Each child is judged on its own: an exit
        while the agent is not stopping the rig is a crash once the rig is up,
        and the start's own before then. The holder's exit says nothing about
        the children and is a problem of its own."""
        for child in list(held.children.values()):
            if child.exit is not None or child.proc.poll() is None:
                continue
            child.exit = exit_record(
                child.name, child.proc.pid, child.proc.returncode, at=_now(),
                phase="running" if held.ready else "startup",
                config_name=self._running_name(held), log=child.log,
            )
            if held.stopping or child.name in held.restarting:
                continue
            if held.ready:
                self.crashed.append(child.exit)
                self._say(crash_message(child.exit))
            else:
                held.startup_exits.append(child.exit)
        if held.holder_exit is None and held.holder.poll() is not None:
            held.holder_exit = exit_record(
                "group holder", held.pgid, held.holder.returncode, at=_now(), phase="running",
                config_name=self._running_name(held), log=None,
            )
            if not held.stopping:
                message = (
                    f"the group holder of {held.holder_exit['config_name']} (pid {held.pgid}) "
                    f"{_how(held.holder_exit)} at {held.holder_exit['at']}, though nothing asked it "
                    "to. The children are still watched, each on its own, but `sushi-rig status` "
                    "now reads the rig as orphaned and `up` refuses until `down` stops the group "
                    "(POST /rig/down, or sushi-rig down)"
                )
                self.problems.append({"kind": "holder_lost", "at": held.holder_exit["at"], "message": message})
                self._say(message)

    def _running_name(self, held: Held) -> str:
        """The held rig's name as it is now: the state file's, which a live
        switch rewrites, when it names the held group."""
        recorded = read_state()
        if recorded and recorded.get("pgid") == held.pgid and recorded.get("config_name"):
            return str(recorded["config_name"])
        return held.launch.config_name

    async def _watch(self, held: Held) -> None:
        """Sweep the held rig until its group is gone."""
        interval = self.world.watch_interval
        while True:
            self._sweep(held)
            if (held.holder_exit is not None
                    and all(child.exit is not None for child in list(held.children.values()))
                    and (held.ready or held.stopping)
                    and not pgid_alive(held.pgid)):
                if self.held is held:
                    self.held = None
                return
            await asyncio.sleep(interval)

    async def _settle(self, held: Held, timeout: float = 2.0) -> None:
        """Wait, briefly, for the watch to see a stopped group gone, and stop
        holding it if it is. A group that is still there stays held, so that
        `down` can be asked again and /health still names it."""
        task = held.task
        if task is not None and not task.done():
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout)
            except asyncio.TimeoutError:
                pass
        if self.held is held and not pgid_alive(held.pgid):
            if task is not None and not task.done():
                task.cancel()
            self.held = None

    async def _tear_down(self, held: Held) -> str:
        """Stop a start that failed: everything it started, at once. What to
        say about it: nothing when the group is gone; otherwise which pids are
        left, which is also kept in /health with the group still held."""
        held.stopping = True
        self.doing = f"stopping what the start had started (process group {held.pgid})"

        def reap() -> None:
            # A zombie still counts as a member of its group. The watch reaps
            # the children it knows; a start abandoned mid-step may have just
            # started one it does not (qpwgraph, before `start_qpwgraph`
            # returned it), so any child of ours in the group is reaped too.
            # Its exit status is lost to its Popen, which is a teardown's
            # to lose: these exits are the stop's.
            for proc in [held.holder, *(child.proc for child in list(held.children.values()))]:
                proc.poll()
            while True:
                try:
                    pid, _ = os.waitpid(-held.pgid, os.WNOHANG)
                except ChildProcessError:
                    return
                if pid == 0:
                    return

        try:
            gone = await asyncio.to_thread(
                self.world.stop_group, held.pgid, self.world.shutdown_steps, poll=reap)
        except PermissionError:
            gone = False
        await self._settle(held)
        if gone or not pgid_alive(held.pgid):
            return "What it had started was stopped."
        held.stuck = True
        left = group_members(held.pgid) or [
            proc.pid for proc in [held.holder, *(c.proc for c in list(held.children.values()))]
            if proc.poll() is None]
        message = (
            f"The start's processes did NOT all stop: pid(s) {', '.join(map(str, left))} are still "
            f"in process group {held.pgid}, with no state file. POST /rig/down (or sushi-rig "
            f"down) tries again; look at them with: ps -o pid,stat,args -g {held.pgid}"
        )
        self.problems.append({"kind": "stuck", "at": _now(), "message": message, "pids": left})
        self._say(message)
        return message

    async def release(self, app: Any = None) -> None:
        """At the agent's end: stop watching, and leave the rig playing. The
        holder keeps the group and the state file describes it, so the next
        agent (or `sushi-rig down`) finds it there."""
        held = self.held
        if held is None:
            return
        if held.task is not None:
            held.task.cancel()
            await asyncio.gather(held.task, return_exceptions=True)
        if not held.stopping:
            self._say(
                f"leaving {self._running_name(held)} running in process group {held.pgid}: "
                "the rig outlives the agent, and 'sushi-rig down' or the next agent stops it"
            )

    # ---------------------------------------------------------------- health

    def describe(self, rig: dict[str, Any] | None = None) -> dict[str, Any]:
        """For /health: the rig this agent holds, if any, each process's state
        as the agent last saw it, the crashes, the problems, and what a step
        still running is doing. `rig` is `describe_rig`'s answer, to say when
        a running rig is not one this agent holds and so has no exits to
        report, only pids to probe."""
        held = self.held
        described: dict[str, Any] = {
            "holds": None, "crashed": list(self.crashed), "problems": list(self.problems), "note": None,
        }
        if self.doing:
            described["doing"] = self.doing
        if held is not None:
            holder = held.holder_exit
            described["holds"] = {
                "config_name": self._running_name(held),
                "pgid": held.pgid,
                "state": ("stuck" if held.stuck else "stopping" if held.stopping
                          else "up" if held.ready else "starting"),
                "holder": {"pid": held.pgid, "running": holder is None,
                           "returncode": holder["returncode"] if holder else None},
                "children": {
                    name: {"pid": child.proc.pid, "running": child.exit is None,
                           "returncode": child.exit["returncode"] if child.exit else None}
                    for name, child in list(held.children.items())
                },
            }
            if held.stuck:
                described["holds"]["left"] = group_members(held.pgid)
        elif rig and rig.get("running"):
            described["note"] = (
                f"{rig.get('config_name')} was not started by this agent (process group "
                f"{rig.get('pgid')}), so its children's exits cannot be read here; a dead one "
                "shows as alive: false under rig.children. down and restart still act on it"
            )
        return described


# --------------------------------------------------------------------------- #

def _rig_env(launch: "rig_module.Launch") -> dict[str, str]:
    """The environment the rig's children start from. As `_supervise` makes
    it, and for its reason: plugins/ alone on LV2_PATH, so a plugin the rig
    lacks fails loudly instead of resolving against a system copy. One
    function, so Carla restarted alone gets the environment `up` gave it."""
    env = dict(os.environ)
    env["LV2_PATH"] = str(launch.root / "plugins")
    return env


def _how(record: dict[str, Any]) -> str:
    return f"was killed by {record['signal']}" if record.get("signal") else f"exited with status {record['returncode']}"


def _plain(exc: BaseException) -> bool:
    """Whether an exception is a refusal the player must act on, said as a
    message, rather than a bug."""
    from .amp import AmpError

    return isinstance(exc, (rig_module.RigError, AmpError))


def _with_warnings(warnings: list[str], reason: str) -> str:
    """A failure's reason, after the warnings said on the way to it, as the
    CLI's stderr would have shown them."""
    return "".join(f"warning: {w}\n" for w in warnings) + reason


def _joined(first: str, then: str) -> str:
    return f"{first.strip()}\n{then}" if first.strip() else then
