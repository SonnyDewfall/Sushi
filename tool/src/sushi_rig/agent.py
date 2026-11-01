"""The rig agent (#29): one local-first process that owns the rig.

Control reaches the rig from three places today: the panel speaks OSC to Sushi,
`listen` handles SAVE and reorder, the CLI speaks gRPC. Every v2 feature needs
one place that owns the rig instead, and this is its first slice: the lifecycle
and the two things worth reading about a rig, served over HTTP on localhost.

    GET  /health        the agent and the rig, in one answer
    GET  /rig           what is running, if anything (`describe_rig`)
    GET  /doctor        `doctor`'s checks, as data
    POST /rig/up        {"config_name" (required), "rig", "headless", "tuner", "amp", "amp_model"}
    POST /rig/down      {"force"}
    POST /rig/restart   {}

## A fixed command set, never a shell

Each route maps to one CLI command with arguments the agent builds itself
(`lifecycle_arguments`): a config name is a name, not a path, and a flag is a
boolean. Nothing a request carries is passed through as text.

## The lifecycle runs in a child process, not in this one

`up` forks the supervisor. This process serves HTTP from an event loop with
worker threads, and a fork from a multi-threaded process inherits whatever locks
another thread held at that instant -- Python warns about exactly this. So the
agent runs `sushi-rig up`, `down` and `restart` as the CLI they already are and
reports what they said. The supervisor is therefore still the one `up` has
always forked, detached from the agent; moving it in here, so that the agent
*is* the supervisor and can run as a service, is the next slice, and this one
does not pretend otherwise.

One lifecycle operation at a time: a second request while one is running is
answered `409 busy`, because `up` and `down` racing is a second rig on top of a
running one, which is what takes a live session down.

## Local-first, so localhost first

The CLI binds it to 127.0.0.1:8029 unless told otherwise. `--host 0.0.0.0`
opens it to the LAN, and until the permission split the issue describes exists
(local: everything; remote: start, stop, load, parameters) that is the
operator's call to make, not a default.
"""

from __future__ import annotations

import asyncio
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Sequence

from .rig import describe_rig

try:
    from aiohttp import web
except ImportError:  # the `agent` extra is optional; `serve` says so
    web = None  # type: ignore[assignment]

VERSION = "0.1"

# `up` waits up to GRPC_TIMEOUT for Sushi and `down` is bounded by
# SHUTDOWN_STEPS, both well inside this; a command still running at the limit
# is stuck, and is killed rather than holding the lock forever.
LIFECYCLE_TIMEOUT = 120.0

# The CLI is run as this module, not as `sushi-rig` from PATH, so the agent
# drives the installation it was started from.
CLI: tuple[str, ...] = (sys.executable, "-m", "sushi_rig.cli")

# A config name names config/<name>.json and an amp model names
# amp/models/<name>. `up` checks each file exists, but a name with a separator
# in it would be looked up outside those folders, so both are refused here
# first: letters, digits and a few marks, no separators, and never a leading
# '-' that would read as a flag. `fullmatch`, because `$` matches before a
# trailing newline.
CONFIG_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")
MODEL_NAME = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_ .,()'+-]*")

LIFECYCLE = ("up", "down", "restart")


class AgentError(Exception):
    """A request the agent will not act on, with the reason."""


@dataclass(frozen=True)
class Outcome:
    """What a command said: its exit status and both streams.

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


Runner = Callable[[Sequence[str]], Awaitable[Outcome]]


def lifecycle_arguments(command: str, body: dict[str, Any] | None) -> list[str]:
    """The CLI arguments for one lifecycle request, or `AgentError`.

    Pure, and the whole of the request's influence on what runs: anything not
    read here is ignored.
    """
    if body is None:
        body = {}
    if not isinstance(body, dict):
        raise AgentError("the request body must be a JSON object")
    if command not in LIFECYCLE:
        raise AgentError(f"unknown command {command!r}")

    def flag(name: str, default: bool) -> bool:
        value = body.get(name, default)
        if not isinstance(value, bool):
            raise AgentError(f"{name!r} must be true or false, not {value!r}")
        return value

    if command == "up":
        # Required, with no default: against an idle agent a stray body-less
        # POST would otherwise start a rig nobody asked for (found live,
        # 2026-11-01). `down` and `restart` act on a rig that exists; `up`
        # makes one, so it must be told which.
        if "config_name" not in body:
            raise AgentError("config_name is required: the name of a file in config/")
        name = body["config_name"]
        if not isinstance(name, str) or not CONFIG_NAME.fullmatch(name) or ".." in name:
            raise AgentError(f"invalid config name {name!r}: letters, digits, '_', '-' and '.'")
        args = ["up", name]
        if flag("rig", False):
            # A rig of channels, config/rigs/<name>.yaml, compiled at start
            # with its own patchbay (#30, #96). The same name check applies:
            # it names a file in config/rigs/, never a path.
            args.append("--rig")
        if flag("headless", False):
            args.append("--headless")
        if not flag("tuner", True):
            args.append("--no-tuner")
        if not flag("amp", True):
            args.append("--no-amp")
        model = body.get("amp_model")
        if model is not None:
            if not isinstance(model, str) or not MODEL_NAME.fullmatch(model) or ".." in model:
                raise AgentError(
                    f"invalid amp model {model!r}: the name of a file in amp/models, not a path"
                )
            args += ["--amp-model", model]
        return args
    if command == "down":
        return ["down", "--force"] if flag("force", False) else ["down"]
    return ["restart"]


async def run_cli(
    args: Sequence[str],
    timeout: float = LIFECYCLE_TIMEOUT,
    program: Sequence[str] = CLI,
) -> Outcome:
    """Run one CLI command to completion and report what it said.

    A command that outlives `timeout` is abandoned: the CLI process is killed
    so the lock is released, and the report says so. What that command had
    started is not stopped, and cannot be from here: `up`'s supervisor has
    already detached into its own process group, so killing the `up` that
    forked it leaves the rig coming up. The answer therefore carries the rig
    as it is afterwards, and a client reads that rather than the exit.
    """
    proc = await asyncio.create_subprocess_exec(
        *program, *args,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:  # finished in the gap; its exit is still read
            pass
        await proc.wait()
        return Outcome(
            False, None, "",
            f"'{' '.join(args)}' did not finish within {timeout:g}s, so the agent "
            "stopped waiting for it. What it started, if anything, is still "
            "running: read /rig.",
        )
    return Outcome(
        proc.returncode == 0, proc.returncode,
        out.decode(errors="replace"), err.decode(errors="replace"),
    )


class Agent:
    """The routes and the one piece of state they share: whether a lifecycle
    command is running, and the last thing that went wrong (until a lifecycle
    command next succeeds).

    Everything that touches the world is injectable, so the routes are tested
    without a rig, a PipeWire or a child process: `describe` answers for the
    state file, `doctor` for the examination, `run` for the CLI.
    """

    def __init__(
        self,
        *,
        root: Path | None = None,
        describe: Callable[[], dict[str, Any]] = describe_rig,
        doctor: Callable[[], list] | None = None,
        run: Runner = run_cli,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.root = root
        self.describe = describe
        self.doctor = doctor or self._doctor
        self.run = run
        self.clock = clock
        self.started = clock()
        self.started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.last_error: dict[str, Any] | None = None
        self.busy: str | None = None  # the lifecycle command running, if one is
        self._lock = asyncio.Lock()

    def _doctor(self) -> list:
        from .doctor import run
        from .paths import checkout_root

        return run(self.root or checkout_root())

    # ----------------------------------------------------------------- routes

    def app(self):
        app = web.Application(middlewares=[self._json_errors])
        app.router.add_get("/health", self.health)
        app.router.add_get("/rig", self.rig)
        app.router.add_get("/doctor", self.doctor_route)
        for command in LIFECYCLE:
            app.router.add_post(f"/rig/{command}", self.lifecycle)
        return app

    async def health(self, request):
        return web.json_response({
            "ok": True,
            "agent": {
                "version": VERSION,
                "started_at": self.started_at,
                "uptime_seconds": round(self.clock() - self.started, 1),
                "busy": self.busy,
                "last_error": self.last_error,
            },
            "rig": self.describe(),
        })

    async def rig(self, request):
        return web.json_response(self.describe())

    async def doctor_route(self, request):
        from .doctor import FAULT, UNCHECKED

        # pw-dump and the environment probes block; off the loop they go.
        checks = await asyncio.get_running_loop().run_in_executor(None, self.doctor)
        return web.json_response({
            "ok": not any(c.state == FAULT for c in checks),
            "faults": sum(c.state == FAULT for c in checks),
            "unchecked": sum(c.state == UNCHECKED for c in checks),
            "checks": [c.as_dict() for c in checks],
        })

    async def lifecycle(self, request):
        command = request.path.rsplit("/", 1)[-1]
        body = None
        if request.can_read_body:
            try:
                body = await request.json()
            except ValueError as exc:  # JSONDecodeError, or bytes that are not text
                raise web.HTTPBadRequest(reason=f"the request body is not JSON: {exc}")
        try:
            args = lifecycle_arguments(command, body)
        except AgentError as exc:
            raise web.HTTPBadRequest(reason=str(exc))

        if self._lock.locked():
            raise web.HTTPConflict(reason=f"busy: '{self.busy}' is still running")
        async with self._lock:
            self.busy = command
            try:
                outcome = await self.run(args)
            finally:
                self.busy = None
            if outcome.ok and (self.last_error or {}).get("kind") == "lifecycle":
                # A later success supersedes the old failure: left in place,
                # a healthy agent reports a refused request forever and
                # /health cannot be trusted to say "nothing is wrong now".
                # Only a lifecycle failure, though: an unexpected error from
                # /doctor or /rig says nothing about the rig coming up, so a
                # `down` that works must not forget it.
                self.last_error = None

        answer: dict[str, Any] = {
            "ok": outcome.ok,
            "command": args,
            "output": outcome.stdout,
            "rig": self.describe(),
        }
        if outcome.ok:
            return web.json_response(answer)
        # Three failures, three answers, one body shape (`reason` says why,
        # as every error here does). The CLI refused or failed -- "a rig is
        # already running", "no rig to restart", Sushi never came up -- which
        # is a conflict with the rig's state, 409. The agent gave up waiting:
        # 504, and the rig in the answer is what to read next.
        reason = outcome.stderr.strip() or f"exit status {outcome.returncode}"
        self._remember(args, reason, kind="lifecycle")
        answer["reason"] = reason
        return web.json_response(answer, status=504 if outcome.abandoned else 409)

    async def announce_stopping(self, app=None) -> None:
        """Say on stdout that the agent is stopping, and whether it was
        mid-command. SIGTERM and SIGINT stop aiohttp cleanly and silently, so
        afterwards "stopped cleanly" and "killed mid-request" looked the same
        in the log (2026-11-01). A line here means the stop was handled; its
        absence, with the process gone, means it was killed outright.
        """
        doing = f"'{self.busy}' still running" if self.busy else "no lifecycle command running"
        print(f"sushi-rig agent stopping ({doing})", flush=True)

    def _remember(self, command: list[str] | None, reason: str, kind: str = "request") -> None:
        self.last_error = {
            "kind": kind,
            "command": command,
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "message": reason,
        }

    @web.middleware if web else (lambda f: f)
    async def _json_errors(self, request, handler):
        """Every error is JSON with a `reason`: aiohttp's own 404, and
        anything a route did not expect, which is also remembered for
        /health rather than left as a plain-text 500 and a log line."""
        try:
            return await handler(request)
        except web.HTTPException as exc:
            if exc.status < 400:
                raise
            return web.json_response(
                {"ok": False, "status": exc.status, "reason": exc.reason}, status=exc.status,
            )
        except Exception as exc:  # noqa: BLE001 -- said, remembered, not swallowed
            reason = f"{type(exc).__name__}: {exc}"
            self._remember([request.method, request.path], reason)
            return web.json_response({"ok": False, "status": 500, "reason": reason}, status=500)


def serve(host: str, port: int, root: Path | None = None) -> int:
    """Run the agent until interrupted."""
    if web is None:
        raise AgentError("aiohttp is not installed; pip install -e tool[agent]")

    agent = Agent(root=root)
    app = agent.app()
    app.on_shutdown.append(agent.announce_stopping)
    print(f"sushi-rig agent {VERSION} on http://{host}:{port}  (Ctrl+C to stop)", flush=True)
    web.run_app(app, host=host, port=port, print=None, handle_signals=True)
    return 0
