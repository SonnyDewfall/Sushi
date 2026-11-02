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
    GET  /live          the running rig's parameters, bypass and chains, with each parameter's range
    POST /live/parameter  {"processor", "parameter", "value"}   value is normalised, 0 to 1
    POST /live/bypass   {"processor", "bypassed"}
    POST /live/move     {"processor", "direction": "up" | "down"}
    GET  /live/log      the changes made through /live, newest last (the last 500)

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

## Live operations talk to Sushi, and never start or stop it

`/live` reads and changes the rig that is running, over gRPC, through the
functions in `live.py` (names, not ids). A request is checked in two stages:
its shape and types first, with no Sushi involved (a value that is not a number
from 0 to 1 is refused having touched nothing); then what the running rig has,
from a read that costs what the request names -- the tracks and chains, that one
processor's parameters and the one value -- and not a walk of every parameter
of the rig, which a fader sending twenty changes a second could not afford. A
processor or parameter the rig lacks, a read-only parameter, a track where a
pedal is meant: refused (400) from that read, before anything is set. Values are
normalised, 0 to 1, for now: real units come with the profiles.

They refuse (`409 busy`) while a lifecycle command runs, checked when the
request gets its turn, because a rig half up or half down is not one to be
changing; they do not take the lifecycle lock, so a `down` can still begin while
one is in flight and will make it fail as `502`. They are serialised among
themselves, so the "previous value" each change records is the value it actually
replaced. A rig that is not up is `503`. Sushi going away, refusing, or not
confirming a change in time is `502` with the reason, and then nothing is logged:
the log holds changes Sushi confirmed. elkpy not being installed is `500`, the
agent's own install and not Sushi's doing. All but the `503` are remembered in
`last_error` until a live change next works.

Every change that succeeded is appended to the operation log, which is where
undo will come from; there is no undo yet. It lives in this process and is
lost when the agent stops.

## Local-first, so localhost first

The CLI binds it to 127.0.0.1:8029 unless told otherwise. `--host 0.0.0.0`
opens it to the LAN, and until the permission split the issue describes exists
(local: everything; remote: start, stop, load, parameters) that is the
operator's call to make, not a default.
"""

from __future__ import annotations

import asyncio
import functools
import math
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Sequence

from . import live as live_module
from .live import DEFAULT_GRPC_ADDRESS, ElkpyMissing, LiveError, plan_move
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

# The operation log keeps this many entries, newest last.
LOG_LIMIT = 500

# "up" the chain is earlier in it, as the panel's move buttons are: the
# direction `move_processor` takes is negative for earlier, positive for later.
DIRECTIONS = {"up": -1, "down": 1}


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


# --------------------------------------------------------------------------- #
# Live operations: the decisions, pure
# --------------------------------------------------------------------------- #

def _object(body: Any) -> dict[str, Any]:
    if not isinstance(body, dict):
        raise AgentError("the request body must be a JSON object")
    return body


def _name(body: dict[str, Any], key: str) -> str:
    value = body.get(key)
    if key not in body:
        raise AgentError(f"{key!r} is required")
    if not isinstance(value, str) or not value:
        raise AgentError(f"{key!r} must be a non-empty string, not {value!r}")
    return value


def parameter_request(body: Any) -> tuple[str, str, float]:
    """`(processor, parameter, value)` from a request body, or `AgentError`.

    The value is in Sushi's normalised domain, 0 to 1, and nothing else yet:
    real-world units (dB, Hz) come with the profiles. A bool is not a number
    here, though Python says it is, and NaN and infinity are refused by the
    range check rather than passed on to Sushi.
    """
    body = _object(body)
    processor, parameter = _name(body, "processor"), _name(body, "parameter")
    if "value" not in body:
        raise AgentError("'value' is required: a number from 0 to 1")
    value = body["value"]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AgentError(f"'value' must be a number from 0 to 1, not {value!r}")
    if not (math.isfinite(value) and 0.0 <= value <= 1.0):
        raise AgentError(f"'value' must be from 0 to 1 (normalised), not {value!r}")
    return processor, parameter, float(value)


def bypass_request(body: Any) -> tuple[str, bool]:
    body = _object(body)
    processor = _name(body, "processor")
    if "bypassed" not in body:
        raise AgentError("'bypassed' is required: true or false")
    if not isinstance(body["bypassed"], bool):
        raise AgentError(f"'bypassed' must be true or false, not {body['bypassed']!r}")
    return processor, body["bypassed"]


def move_request(body: Any) -> tuple[str, str]:
    """`(processor, direction)`, the direction as the word it came as."""
    body = _object(body)
    processor = _name(body, "processor")
    direction = body.get("direction")
    if not isinstance(direction, str) or direction not in DIRECTIONS:
        raise AgentError(f"'direction' must be \"up\" or \"down\", not {direction!r}")
    return processor, direction


def _on_the_rig(chains: dict[str, list[str]]) -> list[str]:
    """Every track and every processor on one, by name."""
    return sorted({name for track, chain in chains.items() for name in [track, *chain]})


def check_parameter(reading: dict[str, Any], processor: str, parameter: str) -> float:
    """The parameter's current normalised value, or `AgentError` if the running
    rig has no such processor or parameter, or it is read-only (a meter).

    `reading` is `read_live_parameter`'s answer: the chains, that one
    processor's parameters (None if it has none because it does not exist) and
    the one value. The names offered in a refusal are the rig's own, so a typo
    can be seen.
    """
    parameters = reading.get("parameters")
    if parameters is None:
        raise AgentError(
            f"the running rig has no processor {processor!r}: it has "
            f"{_on_the_rig(reading.get('chains', {}))}"
        )
    if parameter not in parameters:
        raise AgentError(
            f"{processor!r} has no parameter {parameter!r}: it has {sorted(parameters)}"
        )
    if not parameters[parameter].get("automatable", True):
        raise AgentError(f"{processor!r} parameter {parameter!r} is read-only")
    return reading["value"]


def check_bypass(reading: dict[str, Any], processor: str) -> bool | None:
    """The processor's current bypass state (None where Sushi would not say),
    or `AgentError` if it is not a processor of the running rig. A track is
    refused: its bypass cascades to every processor on it (see `live.capture`),
    which is not what a request to bypass one pedal means.

    `reading` is `read_live_bypass`'s answer.
    """
    chains = reading.get("chains", {})
    if processor in chains:
        raise AgentError(
            f"{processor!r} is a track, not a processor on one: bypassing a track "
            "silences everything on it"
        )
    on_chains = sorted({name for chain in chains.values() for name in chain})
    if processor not in on_chains:
        raise AgentError(f"the running rig has no processor {processor!r}: it has {on_chains}")
    return reading.get("bypassed")


def check_move(chains: dict[str, list[str]], processor: str, direction: str) -> tuple[list[str], bool]:
    """`(the chain it is on, whether moving would change it)`, or `AgentError`
    if the processor is on no track. Whether it would is `plan_move`'s decision:
    a processor already at that end of its chain is not an error, and not a
    change.
    """
    for chain in chains.values():
        if processor in chain:
            return list(chain), plan_move(chain, processor, DIRECTIONS[direction]) is not None
    raise AgentError(f"the running rig has no processor {processor!r} on any track")


def log_entry(op: str, args: dict[str, Any], previous: Any, at: str) -> dict[str, Any]:
    """One operation-log entry. `previous` is what the change replaced, or None
    where it is not known; it is always present so an undo reads one shape.
    """
    return {"at": at, "op": op, "args": args, "previous": previous}


def trim_log(log: list, limit: int = LOG_LIMIT) -> None:
    """Drop the oldest entries, in place, until `limit` remain."""
    if len(log) > limit:
        del log[: len(log) - limit]


def sushi_unreachable(exc: BaseException) -> bool:
    try:
        from elkpy.sushierrors import SushiUnavailableError
    except ImportError:
        return False
    return isinstance(exc, SushiUnavailableError)


@dataclass(frozen=True)
class Live:
    """Everything the live routes need from Sushi, each a blocking call. The
    reads are targeted -- they cost what the request names, not a walk of
    every parameter of the rig -- and their answers are what the `check_*`
    functions decide on. Defaults to `live.py`.
    """

    snapshot: Callable[[], dict[str, Any]]  # capture + "parameter_info", one connection
    chains: Callable[[], dict[str, list[str]]]
    parameter: Callable[[str, str], dict[str, Any]]  # (processor, parameter) -> reading
    bypass: Callable[[str], dict[str, Any]]  # (processor) -> reading
    set_parameter: Callable[[str, str, float], None]
    set_bypass: Callable[[str, bool], None]
    move: Callable[[str, int], str]

    @classmethod
    def at(cls, address: str = DEFAULT_GRPC_ADDRESS) -> "Live":
        def bound(fn):
            return functools.partial(fn, address=address)

        return cls(
            snapshot=bound(live_module.read_rig),
            chains=bound(live_module.get_live_chains),
            parameter=bound(live_module.read_live_parameter),
            bypass=bound(live_module.read_live_bypass),
            set_parameter=bound(live_module.set_live_parameter),
            set_bypass=bound(live_module.set_live_bypass),
            move=bound(live_module.move_processor),
        )


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
    state file, `doctor` for the examination, `run` for the CLI, `live` for
    Sushi's gRPC.
    """

    def __init__(
        self,
        *,
        root: Path | None = None,
        describe: Callable[[], dict[str, Any]] = describe_rig,
        doctor: Callable[[], list] | None = None,
        run: Runner = run_cli,
        live: Live | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.root = root
        self.describe = describe
        self.doctor = doctor or self._doctor
        self.run = run
        self.live = live or Live.at()
        self.clock = clock
        self.started = clock()
        self.started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.last_error: dict[str, Any] | None = None
        self.busy: str | None = None  # the lifecycle command running, if one is
        self._lock = asyncio.Lock()  # lifecycle: one command at a time
        self._live_lock = asyncio.Lock()  # live: one change at a time; waits, not 409
        self.log: list[dict[str, Any]] = []  # operations made through /live, newest last

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
        app.router.add_get("/live", self.live_state)
        app.router.add_get("/live/log", self.live_log)
        app.router.add_post("/live/parameter", self.live_parameter)
        app.router.add_post("/live/bypass", self.live_bypass)
        app.router.add_post("/live/move", self.live_move)
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
        body = await self._json_body(request)
        try:
            args = lifecycle_arguments(command, body)
        except AgentError as exc:
            raise web.HTTPBadRequest(reason=str(exc))

        self._refuse_while_busy()
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

    # ------------------------------------------------------------------- live

    async def _json_body(self, request) -> Any:
        """The request's JSON, or None if it sent no body."""
        if not request.can_read_body:
            return None
        try:
            return await request.json()
        except ValueError as exc:  # JSONDecodeError, or bytes that are not text
            raise web.HTTPBadRequest(reason=f"the request body is not JSON: {exc}")

    def _refuse_while_busy(self) -> None:
        if self._lock.locked():
            raise web.HTTPConflict(reason=f"busy: '{self.busy}' is still running")

    async def _sushi(self, request, fn: Callable[..., Any], *args: Any, reading: bool) -> Any:
        """Run one blocking Sushi call off the loop, and say how it failed.

        - No Sushi to connect to is 503 when a request is only being read
          (nothing has changed), and 502 once a change is under way.
        - Sushi refusing, or not confirming (`LiveError`), or failing any other
          way is 502, with the reason.
        - elkpy not installed (`ElkpyMissing`) is not Sushi's doing: 500.
        Everything but the 503 is remembered for /health until a live change
        next works.
        """
        where = [request.method, request.path]
        try:
            return await asyncio.get_running_loop().run_in_executor(None, fn, *args)
        except ElkpyMissing as exc:
            self._remember(where, str(exc), kind="live")
            raise web.HTTPInternalServerError(reason=str(exc))
        except LiveError as exc:  # our own words, not an elkpy type's
            self._remember(where, str(exc), kind="live")
            raise web.HTTPBadGateway(reason=str(exc))
        except Exception as exc:  # noqa: BLE001 -- said, with its type, never swallowed
            message = " ".join(f"{type(exc).__name__}: {exc}".split())  # one line
            if sushi_unreachable(exc):
                if reading:
                    raise web.HTTPServiceUnavailable(
                        reason=f"Sushi is not reachable, so no rig is up to read or change: {message}"
                    )
                message = f"Sushi went away during the change: {message}"
            self._remember(where, message, kind="live")
            raise web.HTTPBadGateway(reason=message)

    def _record(self, op: str, args: dict[str, Any], previous: Any) -> dict[str, Any]:
        entry = log_entry(op, args, previous, datetime.now(timezone.utc).isoformat(timespec="seconds"))
        self.log.append(entry)
        trim_log(self.log)
        if (self.last_error or {}).get("kind") == "live":
            self.last_error = None  # a change that worked supersedes a live failure
        return entry

    async def live_state(self, request):
        async with self._live_lock:
            self._refuse_while_busy()
            rig = await self._sushi(request, self.live.snapshot, reading=True)
        return web.json_response({
            "ok": True,
            "processors": rig.get("processors", {}),
            "tracks": rig.get("tracks", {}),
            "parameter_info": rig.get("parameter_info", {}),
        })

    async def live_log(self, request):
        return web.json_response({"ok": True, "limit": LOG_LIMIT, "log": list(self.log)})

    async def live_parameter(self, request):
        """Set one parameter, by processor and parameter name.

        `value` is Sushi's normalised domain, a number from 0 to 1; real units
        come with the profiles. The shape is refused (400) before Sushi is
        contacted; whether the running rig has the processor and parameter, and
        whether the parameter is writable, is decided from what Sushi reports
        before anything is set.
        """
        try:
            processor, parameter, value = parameter_request(await self._json_body(request))
        except AgentError as exc:
            raise web.HTTPBadRequest(reason=str(exc))
        async with self._live_lock:
            self._refuse_while_busy()
            reading = await self._sushi(request, self.live.parameter, processor, parameter, reading=True)
            try:
                previous = check_parameter(reading, processor, parameter)
            except AgentError as exc:
                raise web.HTTPBadRequest(reason=str(exc))
            await self._sushi(request, self.live.set_parameter, processor, parameter, value, reading=False)
            entry = self._record(
                "parameter", {"processor": processor, "parameter": parameter, "value": value}, previous,
            )
        return web.json_response({"ok": True, **entry})

    async def live_bypass(self, request):
        """Bypass or un-bypass one processor (not a track) by name."""
        try:
            processor, bypassed = bypass_request(await self._json_body(request))
        except AgentError as exc:
            raise web.HTTPBadRequest(reason=str(exc))
        async with self._live_lock:
            self._refuse_while_busy()
            reading = await self._sushi(request, self.live.bypass, processor, reading=True)
            try:
                previous = check_bypass(reading, processor)
            except AgentError as exc:
                raise web.HTTPBadRequest(reason=str(exc))
            await self._sushi(request, self.live.set_bypass, processor, bypassed, reading=False)
            entry = self._record("bypass", {"processor": processor, "bypassed": bypassed}, previous)
        return web.json_response({"ok": True, **entry})

    async def live_move(self, request):
        """Move one processor a step earlier ("up") or later ("down") in its
        track's chain. A processor already at that end is `changed: false`,
        and is neither sent to Sushi nor logged.
        """
        try:
            processor, direction = move_request(await self._json_body(request))
        except AgentError as exc:
            raise web.HTTPBadRequest(reason=str(exc))
        async with self._live_lock:
            self._refuse_while_busy()
            chains = await self._sushi(request, self.live.chains, reading=True)
            try:
                chain, would_change = check_move(chains, processor, direction)
            except AgentError as exc:
                raise web.HTTPBadRequest(reason=str(exc))
            if not would_change:
                edge = "first" if direction == "up" else "last"
                return web.json_response({
                    "ok": True, "changed": False, "chain": chain,
                    "message": f"{processor} is already {edge} in the chain",
                })
            message = await self._sushi(
                request, self.live.move, processor, DIRECTIONS[direction], reading=False)
            # `move_processor` reports in words. Anything but "moved" means
            # the chain was not what it was a moment ago (or Sushi declined):
            # said as it was said, not logged.
            if not str(message).startswith("moved "):
                reason = f"the move did not happen: {message}"
                self._remember([request.method, request.path], reason, kind="live")
                raise web.HTTPBadGateway(reason=reason)
            entry = self._record(
                "move", {"processor": processor, "direction": direction}, chain)
        return web.json_response({"ok": True, "changed": True, "message": message, **entry})

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
