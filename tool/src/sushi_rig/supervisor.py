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
  can read its exit status: a child that exits while the rig is up is noticed
  within two `WATCH_INTERVAL`s and reported in `/health` (`agent.supervisor.crashed`)
  and on stderr. It is not restarted. Principle 9: the agent heals nothing on
  its own, and the answer to a crash is `restart`, which the player asks for.
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

What runs is not decided here. `rig.prepare_launch` refuses, compiles and
plans (`plan_children` stays the one planner), and `rig`'s shared helpers say
what the state file holds and what a startup problem is called. This module
decides only how the children are started and watched, and its effects are
injectable (`World`) so the lifecycle is tested with fake children.
"""

from __future__ import annotations

import asyncio
import io
import os
import signal
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Sequence

from . import rig as rig_module
from .paths import log_dir
from .procs import SHUTDOWN_STEPS, pgid_alive, stop_group, wait_for_grpc
from .qpwgraph import start_qpwgraph
from .state import clear_state, read_state, write_state

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

# Long enough for `sushi-rig panel` to read every parameter of a big rig over
# gRPC; a bound so that a hung one fails `up` rather than holding the lifecycle
# lock for ever. A guess: the forked supervisor never bounded it.
PANEL_TIMEOUT = 60.0


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


def _sushi_ready(alive: Callable[[], bool] | None) -> bool:
    return wait_for_grpc(rig_module.GRPC_HOST, rig_module.GRPC_PORT, rig_module.GRPC_TIMEOUT, alive)


def _generate_panel(command: list[str], root: Path, env: dict[str, str]) -> tuple[int, str]:
    try:
        done = subprocess.run(
            command, cwd=root, env=env, capture_output=True, text=True, check=False,
            timeout=PANEL_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return 1, f"'sushi-rig panel' did not finish within {PANEL_TIMEOUT:g}s"
    except OSError as exc:
        return 1, str(exc)
    return done.returncode, done.stderr


def _link_problems(
    plan: list[dict[str, Any]], root: Path, running: set[str], rig_run: bool,
) -> list[str]:
    return rig_module._unmade_link_problems(
        rig_module._patchbay_in(plan, root), running, rig_run=rig_run,
    )


@dataclass(frozen=True)
class World:
    """What the supervisor reaches outside itself, so its tests can stand each
    in: planning a rig, starting a process, qpwgraph, Sushi's gRPC, the panel,
    the patchbay check and the amp's knobs. The defaults are the ones `up`'s
    forked supervisor uses."""

    prepare: Callable[..., "rig_module.Launch"] = rig_module.prepare_launch
    spawn: Callable[..., subprocess.Popen] = _spawn
    start_qpwgraph: Callable[..., "subprocess.Popen | None"] = start_qpwgraph
    sushi_ready: Callable[["Callable[[], bool] | None"], bool] = _sushi_ready
    generate_panel: Callable[[list[str], Path, dict[str, str]], tuple[int, str]] = _generate_panel
    link_problems: Callable[[list, Path, set, bool], list[str]] = _link_problems
    replay_amp: Callable[[dict[str, float]], None] = rig_module._replay_amp
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
    its life. `stopping` is set before the agent signals the group, so the
    exits that follow are a `down`, not crashes."""

    launch: "rig_module.Launch"
    holder: subprocess.Popen
    logs: Path
    children: dict[str, Child] = field(default_factory=dict)
    ready: bool = False
    stopping: bool = False
    record: dict[str, Any] | None = None  # the state file, as written
    task: "asyncio.Task | None" = None

    @property
    def pgid(self) -> int:
        return self.holder.pid

    def add(self, name: str, proc: subprocess.Popen, log: Path | None) -> None:
        self.children[name] = Child(name, proc, log)


class Supervisor:
    """Starts, watches and stops the rig the agent holds; acts through the
    state file on a rig it does not hold. One lifecycle command at a time is
    the agent's lock to keep (`Agent._run_lifecycle`), not this class's."""

    def __init__(self, world: World | None = None, say: Callable[[str], None] | None = None) -> None:
        self.world = world or World()
        self.held: Held | None = None
        # Exits of the held rig's children that no `down` caused, newest last.
        # Kept after the rig is stopped, so a crash is still readable after the
        # `down` that followed it; cleared when the agent next starts a rig.
        self.crashed: list[dict[str, Any]] = []
        self.note: str | None = None
        self._say = say or (lambda message: print(f"sushi-rig agent: {message}", file=sys.stderr, flush=True))

    # ------------------------------------------------------------- the runner

    async def run(self, args: Sequence[str]) -> Outcome:
        """One lifecycle step, given as the CLI arguments `_run_lifecycle`
        hands its runner. A refusal or a failure the player must fix is an
        unsuccessful `Outcome` with the reason, as the CLI's exit was; a bug
        is raised, so the agent answers 500 and remembers it."""
        from .amp import AmpError

        try:
            command, options = parse_lifecycle(args)
        except ValueError as exc:
            return Outcome(False, 2, "", str(exc))
        try:
            if command == "up":
                return await self.up(**options)
            if command == "down":
                return await self.down(**options)
            return await self.restart()
        except (rig_module.RigError, AmpError) as exc:
            return Outcome(False, 1, "", str(exc))

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
        env = dict(os.environ)
        # As `_supervise` does, and for its reason: plugins/ alone, so a plugin
        # the rig lacks fails loudly instead of resolving against a system copy.
        env["LV2_PATH"] = str(launch.root / "plugins")
        holder = world.spawn(world.holder, cwd=launch.root, env=env, log=None, process_group=0)
        held = Held(launch=launch, holder=holder, logs=logs)
        self.held, self.crashed, self.note = held, [], None
        held.task = asyncio.create_task(self._watch(held))
        problems: list[str] = []
        try:
            failure = await self._start_children(held, env, problems)
            if failure is None:
                record = rig_module.state_record(
                    supervisor_pid=held.pgid, pgid=held.pgid, headless=launch.headless,
                    config_name=launch.config_name, config=launch.config,
                    children={name: child.proc.pid for name, child in held.children.items()},
                    log_dir=logs, started_at=datetime.now(timezone.utc).isoformat(),
                    amp_model=launch.amp_model, tuner_requested=launch.tuner_requested,
                    amp_requested=launch.amp_requested,
                    amp_model_requested=launch.amp_model_requested,
                    rig_source=launch.rig_source, machine_file=launch.machine_file,
                )
                write_state(record)
                held.record = record
                held.ready = True
        except BaseException:
            # Whatever did start must not be left running, even on a bug or a
            # cancellation; the exception is then the agent's to report.
            await self._tear_down(held)
            raise
        if failure is not None:
            await self._tear_down(held)
            return Outcome(False, 1, "", _with_warnings(warnings + problems, failure))
        return Outcome(
            True, 0, rig_module.up_summary(launch.config_name, launch.headless, held.record or {}),
            "".join(f"warning: {w}\n" for w in warnings + problems),
        )

    async def _start_children(self, held: Held, env: dict[str, str], problems: list[str]) -> str | None:
        """Start the planned children into the held group, in order, with the
        checks `_supervise` makes; the failure that stops the start, or None.
        Mirrors `_supervise` step for step: the decisions are rig's helpers,
        and only how a process is started differs."""
        world, launch, logs = self.world, held.launch, held.logs
        root = launch.root
        for child in launch.children:
            name = child["name"]
            if name == "qpwgraph":
                proc = await asyncio.to_thread(
                    world.start_qpwgraph, child, root, env, logs, process_group=held.pgid)
                if proc is None:
                    problems.append(rig_module.qpwgraph_problem(root))
                    continue
                held.add(name, proc, logs / "qpwgraph.log")
                continue
            log = logs / f"{name}.log"
            try:
                proc = world.spawn(
                    child["command"], cwd=root, env=rig_module.child_environment(env, child),
                    log=log, process_group=held.pgid,
                )
            except OSError as exc:
                if child["optional"]:
                    problems.append(f"{name} did not start ({exc})")
                    continue
                return f"{name} did not start: {exc}"
            held.add(name, proc, log)

        sushi = held.children.get("sushi")
        alive = (lambda: sushi.proc.poll() is None) if sushi else None
        if not await asyncio.to_thread(world.sushi_ready, alive):
            why = (
                "Sushi exited during startup"
                if sushi and sushi.proc.poll() is not None
                else f"Sushi's gRPC never came up on {rig_module.GRPC_HOST}:{rig_module.GRPC_PORT}"
            )
            return f"{why} — see {logs / 'sushi.log'}"

        if not launch.headless:
            generate, serve = rig_module.panel_commands(root, launch.config)
            code, err = await asyncio.to_thread(world.generate_panel, generate, root, env)
            if code != 0:
                return f"could not generate the panel:\n{err.strip()}"
            try:
                osc = world.spawn(serve, cwd=root, env=env, log=logs / "panel.log", process_group=held.pgid)
            except OSError as exc:
                return f"open-stage-control did not start: {exc}"
            held.add("open-stage-control", osc, logs / "panel.log")

        # A child that starts and then exits: said, as `_supervise` says it.
        for name, child in held.children.items():
            if child.proc.poll() is not None:
                problems.append(rig_module.startup_exit_problem(name, child.proc.returncode, logs))

        running = {name for name, child in held.children.items() if child.proc.poll() is None}
        if "qpwgraph" in running:
            problems += await asyncio.to_thread(
                world.link_problems, launch.children, root, running, launch.rig_source is not None)

        if launch.amp_model and "amp" in running:
            try:
                await asyncio.to_thread(world.replay_amp, launch.amp_values or {})
            except Exception as exc:  # noqa: BLE001 - an amp at its defaults beats no rig
                problems.append(f"amp parameters not restored: {exc}")
        return None

    # ------------------------------------------------------------------- down

    async def down(self, force: bool = False) -> Outcome:
        """Stop the rig. The one this agent holds is stopped by its group id,
        whatever the state file says; any other is `rig.down`'s, through the
        state file, which is how a rig started without the agent, or by an
        agent before this one, is stopped."""
        held = self.held
        if held is None or not pgid_alive(held.pgid):
            if held is not None:
                await self._settle(held)
            out, err = io.StringIO(), io.StringIO()
            code = await asyncio.to_thread(lambda: rig_module.down(force, out=out, err=err))
            return Outcome(code == 0, code, out.getvalue(), err.getvalue())

        launch = held.launch
        said = f"Stopping {launch.config_name} ({'headless' if launch.headless else 'panel'} mode)...\n"
        steps = ((signal.SIGKILL, 2.0),) if force else self.world.shutdown_steps
        held.stopping = True
        # In a thread: the escalation sleeps. Exited children are reaped by
        # the watch on the loop meanwhile, since a zombie still counts as a
        # member of its group and the escalation would otherwise run in full.
        gone = await asyncio.to_thread(stop_group, held.pgid, steps)
        if not gone:
            held.stopping = False
            return Outcome(False, 1, said, (
                f"Process group {held.pgid} survived SIGKILL. Something is stuck in an "
                "uninterruptible state — check with: ps -eo pid,pgid,args | "
                f"awk '$2 == {held.pgid}'"
            ))
        await self._settle(held)
        recorded = read_state()
        if recorded and recorded.get("pgid") == held.pgid:
            clear_state()
        return Outcome(True, 0, said + "Rig offline.\n", "")

    async def restart(self) -> Outcome:
        """Stop the rig and bring the same one back, as `rig.restart` does:
        with what was asked for, read from the record the agent wrote for a
        rig it holds and from the state file for one it does not."""
        held = self.held
        state = held.record if held is not None and held.record is not None else read_state()
        if not state:
            return Outcome(False, 1, "", (
                "no rig is running, so there is nothing to restart.\n"
                "Start one with: sushi-rig up <config>"
            ))
        config_name, options = rig_module.restart_arguments(state)
        said = f"Restarting {config_name}...\n"
        stopped = await self.down()
        if not stopped.ok:
            return Outcome(False, stopped.returncode, said + stopped.stdout, _joined(
                stopped.stderr, "could not stop the running rig, so it was not restarted"))
        started = await self.up(config_name, **options)
        return Outcome(started.ok, started.returncode, said + stopped.stdout + started.stdout,
                       stopped.stderr + started.stderr)

    # -------------------------------------------------------------- watching

    async def _watch(self, held: Held) -> None:
        """Poll the held rig's children until every process the agent started
        has exited.

        An exit while the agent is not stopping the rig is a crash, recorded
        and said -- but only on the tick after it is seen, with the holder
        looked at first. A group signalled from outside the agent (`sushi-rig
        down --direct`, a `kill`) kills the holder, which has nothing to clean
        up, as soon as it is scheduled; a child can still be seen gone a moment
        before it is. Classified one tick later, such an exit reads as the stop
        it was. The cost is that a crash reaches /health one tick late.
        """
        interval = self.world.watch_interval
        seen: list[Child] = []  # exits seen on the last tick, not yet classified
        while True:
            holder_gone = held.holder.poll() is not None
            if holder_gone and not held.stopping:
                held.stopping = True
                self.note = (
                    f"{held.launch.config_name}'s process group {held.pgid} was signalled from "
                    f"outside the agent at {_now()} (its holder, pid {held.pgid}, exited with "
                    f"status {held.holder.returncode}), so the exits since are that stop, not "
                    "crashes"
                )
                self._say(self.note)
            for child in seen:
                if not held.stopping:
                    self.crashed.append(child.exit)
                    self._say(crash_message(child.exit))
            seen = []
            for child in list(held.children.values()):
                if child.exit is None and child.proc.poll() is not None:
                    child.exit = exit_record(
                        child.name, child.proc.pid, child.proc.returncode, at=_now(),
                        phase="running" if held.ready else "startup",
                        config_name=held.launch.config_name, log=child.log,
                    )
                    seen.append(child)
            if holder_gone and all(child.exit is not None for child in held.children.values()):
                # Every process the agent started has exited, so there is
                # nothing left whose exit it could read; the last exits are
                # the stop's (`stopping` is set by now). Grandchildren, such
                # as an AppImage's re-exec'd binary, are `stop_group`'s.
                if self.held is held:
                    self.held = None
                return
            await asyncio.sleep(interval)

    async def _settle(self, held: Held, timeout: float = 2.0) -> None:
        """Wait for the watch to see the group gone, briefly: after a stop that
        succeeded every member has exited, so this is one tick or two."""
        task = held.task
        if task is not None and not task.done():
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout)
            except asyncio.TimeoutError:
                task.cancel()
        if self.held is held:
            self.held = None

    async def _tear_down(self, held: Held) -> None:
        """Stop a start that failed: everything it started, at once."""
        held.stopping = True
        try:
            await asyncio.to_thread(stop_group, held.pgid, self.world.shutdown_steps)
        except (PermissionError, ValueError):
            pass
        await self._settle(held)

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
                f"leaving {held.launch.config_name} running in process group {held.pgid}: "
                "the rig outlives the agent, and 'sushi-rig down' or the next agent stops it"
            )

    # ---------------------------------------------------------------- health

    def describe(self, rig: dict[str, Any] | None = None) -> dict[str, Any]:
        """For /health: the rig this agent holds, if any, each child's state
        as the agent last saw it, and the crashes. `rig` is `describe_rig`'s
        answer, to say when a running rig is not one this agent holds and so
        has no exits to report, only pids to probe."""
        held = self.held
        described: dict[str, Any] = {"holds": None, "crashed": list(self.crashed), "note": self.note}
        if held is not None:
            described["holds"] = {
                "config_name": held.launch.config_name,
                "pgid": held.pgid,
                "state": "stopping" if held.stopping else "up" if held.ready else "starting",
                "children": {
                    name: {"pid": child.proc.pid, "running": child.exit is None,
                           "returncode": child.exit["returncode"] if child.exit else None}
                    for name, child in held.children.items()
                },
            }
        elif rig and rig.get("running"):
            # What is true now outranks a note about a rig that has gone.
            described["note"] = (
                f"{rig.get('config_name')} was not started by this agent (process group "
                f"{rig.get('pgid')}), so its children's exits cannot be read here; a dead one "
                "shows as alive: false under rig.children. down and restart still act on it"
            )
        return described


# --------------------------------------------------------------------------- #

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
