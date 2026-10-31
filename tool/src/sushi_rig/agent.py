"""The rig agent (#29): one local-first process that owns the rig.

Control reaches the rig from three places today: the panel speaks OSC to Sushi,
`listen` handles SAVE and reorder, the CLI speaks gRPC. Every v2 feature needs
one place that owns the rig instead, and this is its first slice: the lifecycle
and the two things worth reading about a rig, served over HTTP on localhost.

    GET  /health        the agent and the rig, in one answer
    GET  /rig           what is running, if anything (`describe_rig`)
    GET  /doctor        `doctor`'s checks, as data
    POST /rig/up        {"config_name", "headless", "tuner", "amp", "amp_model"}
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

The default bind is 127.0.0.1. `--host 0.0.0.0` opens it to the LAN, and until
the permission split the issue describes exists (local: everything; remote:
start, stop, load, parameters) that is the operator's call to make, not a
default.
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Sequence

from .rig import DEFAULT_CONFIG_NAME, describe_rig

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8029  # the issue's number, and clear of the panel's 8080
VERSION = "0.1"

# `up` waits up to GRPC_TIMEOUT for Sushi and `down` is bounded by
# SHUTDOWN_STEPS, both well inside this; a command still running at the limit
# is stuck, and is killed rather than holding the lock forever.
LIFECYCLE_TIMEOUT = 120.0

# The CLI is run as this module, not as `sushi-rig` from PATH, so the agent
# drives the installation it was started from.
CLI: tuple[str, ...] = (sys.executable, "-m", "sushi_rig.cli")

# A config name names config/<name>.json. `up` checks the file exists, but a
# name with a separator in it would be looked up outside config/, so it is
# refused here first.
CONFIG_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")

LIFECYCLE = ("up", "down", "restart")


class AgentError(Exception):
    """A request the agent will not act on, with the reason."""


@dataclass(frozen=True)
class Outcome:
    """What a command said: its exit status and both streams."""

    ok: bool
    returncode: int | None
    stdout: str
    stderr: str


Runner = Callable[[Sequence[str]], Awaitable[Outcome]]


def lifecycle_arguments(command: str, body: dict[str, Any] | None) -> list[str]:
    """The CLI arguments for one lifecycle request, or `AgentError`.

    Pure, and the whole of the request's influence on what runs: anything not
    read here is ignored.
    """
    body = body or {}
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
        name = body.get("config_name", DEFAULT_CONFIG_NAME)
        if not isinstance(name, str) or not CONFIG_NAME.match(name) or ".." in name:
            raise AgentError(f"invalid config name {name!r}: letters, digits, '_', '-' and '.'")
        args = ["up", name]
        if flag("headless", False):
            args.append("--headless")
        if not flag("tuner", True):
            args.append("--no-tuner")
        if not flag("amp", True):
            args.append("--no-amp")
        model = body.get("amp_model")
        if model is not None:
            if not isinstance(model, str) or not model or model.startswith("-"):
                raise AgentError(f"invalid amp model {model!r}")
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

    A command that outlives `timeout` is killed and reported as a failure
    naming the limit, never left running under a lock nobody will release.
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
        proc.kill()
        await proc.wait()
        return Outcome(False, None, "", f"{' '.join(args)} did not finish within {timeout:g}s and was killed")
    return Outcome(
        proc.returncode == 0, proc.returncode,
        out.decode(errors="replace"), err.decode(errors="replace"),
    )


class Agent:
    """The routes and the one piece of state they share: whether a lifecycle
    command is running, and the last thing that went wrong.

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
        from aiohttp import web

        app = web.Application(middlewares=[_json_errors()])
        app.router.add_get("/health", self.health)
        app.router.add_get("/rig", self.rig)
        app.router.add_get("/doctor", self.doctor_route)
        for command in LIFECYCLE:
            app.router.add_post(f"/rig/{command}", self.lifecycle)
        return app

    async def health(self, request):
        from aiohttp import web

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
        from aiohttp import web

        return web.json_response(self.describe())

    async def doctor_route(self, request):
        from aiohttp import web

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
        from aiohttp import web

        command = request.path.rsplit("/", 1)[-1]
        body = None
        if request.can_read_body:
            try:
                body = await request.json()
            except json.JSONDecodeError as exc:
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

        answer = {
            "ok": outcome.ok,
            "command": args,
            "output": outcome.stdout,
            "error": outcome.stderr.strip() or None,
            "rig": self.describe(),
        }
        if not outcome.ok:
            self.last_error = {
                "command": args,
                "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "message": outcome.stderr.strip() or f"exit status {outcome.returncode}",
            }
            # The CLI refused or failed: "a rig is already running", "no rig
            # to restart". That is a conflict with the rig's state, not a bad
            # request and not the agent's own fault.
            return web.json_response(answer, status=409)
        return web.json_response(answer)


def _json_errors():
    """Every error is JSON with a `reason`, including aiohttp's own 404."""
    from aiohttp import web

    @web.middleware
    async def middleware(request, handler):
        try:
            return await handler(request)
        except web.HTTPException as exc:
            if exc.status < 400:
                raise
            return web.json_response(
                {"ok": False, "status": exc.status, "reason": exc.reason}, status=exc.status,
            )

    return middleware


def serve(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT, root: Path | None = None) -> int:
    """Run the agent until interrupted."""
    try:
        from aiohttp import web
    except ImportError:
        raise AgentError("aiohttp is not installed; pip install -e tool[agent]")

    agent = Agent(root=root)
    print(f"sushi-rig agent {VERSION} on http://{host}:{port}  (Ctrl+C to stop)", flush=True)
    web.run_app(agent.app(), host=host, port=port, print=None, handle_signals=True)
    return 0
