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
replaced; a read (`GET /live`) takes no part in that, since it cannot disturb a
`previous`, so a panel polling it does not make fader writes wait. Each request
makes one connection to Sushi, does its reading and changing on it, and closes
it. No call to Sushi may take longer than `LIVE_TIMEOUT`: past it the agent
stops waiting, releases the lock and answers `504`, though it cannot stop the
call (see the constant); the call's connection is closed if it returns. Sushi
calls run in a pool of their own (`LIVE_WORKERS`), and when stuck calls fill it
a request is `503`, not queued; `/health` counts them. A rig that is not up is
`503`. Sushi going away, refusing, or not
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

## The library and chain edits (#32)

    GET  /library       every plugin in plugin-manifest.txt: uri, name, ports, profile and its groups
    POST /live/add      {"track", "uri", "name"?, "after"?}
    POST /live/remove   {"processor"}

What S3 of the UX story needs: pick a plugin from the library, add it to a
chain, drag it into place (`/live/move`), take one out. The library is built
once, on first use, and kept for the agent's life (`library.read_library`:
the manifest, the bundles in `plugins/` read without lilv, the profiles); it
is the manifest's, never the system's LV2 paths. An add is checked in the same
two stages as every live change: the request's shape and that the URI is in
the manifest and can be hosted by Sushi (400, Sushi untouched); then, from a
walk of the chains under the live lock, that the track exists, the name is
free and `after` is on that track (409: a conflict with the rig as it is,
which the next read may change). It goes in before the track's send unless
told otherwise, never after it: the send is what carries a channel to the
master, and audio placed after it is not heard. A remove of a send, a return
or a track is 409, as is one of what the rig does not have, and a move of a
send or return, or past one, is refused too: one rule (`live.protected_processor`)
knows them, by the uid Sushi's session reports and by the compiler's names. Each is confirmed
by reading the chain back, not by Sushi's acknowledgement alone, and an edit
whose read-back is not what was asked is 502 and is not logged, as a change
Sushi did not confirm is not. Not run against a live Sushi: see `live.py`.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import contextlib
import functools
import math
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Sequence

from . import library as library_module
from . import live as live_module
from .live import DEFAULT_GRPC_ADDRESS, ElkpyMissing, LiveError, Move
from .live import is_send, protected_processor
from .rig import describe_rig
from .save import NAME_PATTERN

try:
    from aiohttp import web
except ImportError:  # the `agent` extra is optional; `serve` says so
    web = None  # type: ignore[assignment]

VERSION = "0.1"

# `up` waits up to GRPC_TIMEOUT for Sushi and `down` is bounded by
# SHUTDOWN_STEPS, both well inside this; a command still running at the limit
# is stuck, and is killed rather than holding the lock forever.
LIFECYCLE_TIMEOUT = 120.0

# How long one blocking call to Sushi (connecting, a read, a change) may take
# before the agent stops waiting for it and answers 504. elkpy's gRPC calls
# carry no deadline of their own, so a Sushi that accepts the connection and
# then stops answering would hold the live lock, and with it every /live
# request, for ever. The longest thing a call legitimately does is a full read
# of the rig (hundreds of RPCs at about a millisecond each on loopback) or a
# change waiting `live.CONFIRM_TIMEOUT` (5 s) for Sushi to confirm; this is
# four times that. A guess, not a measurement: no live Sushi was to hand.
#
# What the timeout does not do: stop the call. A thread in a blocking gRPC call
# cannot be cancelled from Python (there is no way to interrupt a thread inside
# C code that holds no deadline, and `Future.cancel` only reaches a call that
# has not started), so the worker stays stuck until Sushi answers or the process
# ends: its thread cannot be reclaimed, only waited for. The agent stops
# waiting and lets the next request through, and when the call does come back
# (slowly, rather than never) closes the connection it was using; `/health`
# reports how many calls are in that state (`stuck_live_calls`).
LIVE_TIMEOUT = 20.0

# How many calls to Sushi may be in flight at once, stuck ones included. Sushi
# calls run in a pool of this size that is the agent's own, not the loop's
# default executor: the default has min(32, cpus + 4) workers and is shared
# with /doctor, so a Sushi that never answers used up every worker after a
# handful of 504s, after which each request waited for a worker and was
# reported as Sushi not answering though its call had not started. A request
# makes its calls one after another, so this is also the number of requests
# that can be in Sushi at once; past it a request is refused (503) rather than
# queued, since a queue behind stuck threads would wait for ever.
LIVE_WORKERS = 8

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
# direction `live.plan_move` takes is negative for earlier, positive for later.
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
    if key not in body:
        raise AgentError(f"{key!r} is required")
    value = body[key]
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


def _on_the_rig(chains: dict[str, list[str]], with_tracks: bool = True) -> list[str]:
    """Every processor on a chain, by name, and with `with_tracks` the tracks
    too: what a refusal offers, sorted. A refusal names only what the request
    could have meant -- a bypass cannot mean a track."""
    return sorted({
        name for track, chain in chains.items()
        for name in ([track, *chain] if with_tracks else chain)
    })


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
    on_chains = _on_the_rig(chains, with_tracks=False)
    if processor not in on_chains:
        raise AgentError(f"the running rig has no processor {processor!r}: it has {on_chains}")
    return reading.get("bypassed")


def check_move(move: Move) -> None:
    """`AgentError` if the move's processor is on no track. A processor already
    at that end of its chain is not an error and not a change: `move.outcome`
    says which, from `live.decide_move`, the one place that decides it.
    """
    if move.outcome == "absent":
        raise AgentError(
            f"the running rig has no processor {move.processor!r} on any track: it has "
            f"{_on_the_rig(move.chains, with_tracks=False)}"
        )
    if move.outcome == "protected":
        # A send or return is, or would be swapped with, the one moved (#32).
        raise Conflict(move.message)


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


# =========================================================================== #
# The library and chain edits (#32): the decisions, pure
# =========================================================================== #

class Conflict(AgentError):
    """A request the running rig cannot take as it is now (409): a name it
    already has, a track it lacks, a send that must stay where it is. Unlike a
    bad request it may succeed later, against a different rig."""


class Unconfirmed(AgentError):
    """Sushi did not give what an edit needs or did not do what it was asked,
    as read back (502)."""


def derive_name(plugin_name: str) -> str:
    """A processor name from a plugin's name: lower case, every run of anything
    but a-z and 0-9 one '_', at most 64 characters (`save.NAME_PATTERN`, which
    a saved rig's names must match). "" when nothing is left, which
    `add_request` refuses rather than inventing a name."""
    name = re.sub(r"[^a-z0-9]+", "_", plugin_name.lower()).strip("_")
    return name[:64].rstrip("_")


@dataclass(frozen=True)
class AddRequest:
    """What an add asks for, checked for shape. `named` is whether the client
    chose `name`, which only changes what a clash tells it to do."""

    track: str
    uri: str
    name: str
    after: str | None
    named: bool


def add_request(body: Any, library: dict[str, Any]) -> AddRequest:
    """An `AddRequest` from a request body, or `AgentError` (400).

    `library` is `library.read_library`'s answer. The URI must be one of its
    entries -- the manifest is the list of what the rig may load -- and one
    Sushi can host (`addable`; the amp is not). With no `name`, the plugin's
    name as the library has it, made a config name (`derive_name`); a given
    name must already be one, since it is what a save will write.
    """
    body = _object(body)
    track, uri = _name(body, "track"), _name(body, "uri")
    entries = {entry["uri"]: entry for entry in library.get("plugins", [])}
    entry = entries.get(uri)
    if entry is None:
        raise AgentError(
            f"{uri!r} is not in plugin-manifest.txt, which lists every plugin the rig "
            f"may load: GET /library lists its {len(entries)}")
    if not entry.get("addable", True):
        raise AgentError(f"{entry['name']} cannot be added to a Sushi chain: {entry.get('why_not')}")
    named = body.get("name") is not None
    if named:
        name = body["name"]
        if not isinstance(name, str) or not NAME_PATTERN.fullmatch(name):
            raise AgentError(f"'name' must match {NAME_PATTERN.pattern}, not {name!r}")
    else:
        name = derive_name(entry["name"])
        if not NAME_PATTERN.fullmatch(name):
            raise AgentError(
                f"no processor name can be made from {entry['name']!r}: give it a 'name' "
                f"matching {NAME_PATTERN.pattern}")
    after = body.get("after")
    if after is not None and (not isinstance(after, str) or not after):
        raise AgentError(f"'after' must name a processor on the track, or be left out, not {after!r}")
    return AddRequest(track, uri, name, after, named)



def _track_of(chains: dict[str, list[str]], processor: str) -> str | None:
    return next((track for track, chain in chains.items() if processor in chain), None)


@dataclass(frozen=True)
class Addition:
    """An add decided on: where it goes, and the command that puts it there.

    `chain` is the track's before, `expected` after. Sushi places a processor
    "in front of" another or "at the back", so `before_id` is the id of the one
    it goes in front of, or 0 with `add_to_back`; both from the reading the
    decision was made on, as a move's are.
    """

    track: str
    name: str
    uri: str
    after: str | None
    chain: list[str]
    expected: list[str]
    track_id: int
    before_id: int
    add_to_back: bool


def check_add(reading: dict[str, Any], request: AddRequest) -> Addition:
    """Where `request` goes on the running rig, or `Conflict` (409).

    `reading` is `live.read_live_chains(uids=True)`'s: `{"chains", "ids",
    "uids"}`. The send is known by `live.is_send`, by its uid and its name, as a
    remove and a move know it: by name alone, an add on a rig whose send was
    called something else went in behind it, was confirmed, logged, and not
    heard (found by review). The name must
    be free across the whole rig -- Sushi's processor names are one namespace,
    tracks included. With no `after` it goes last, but in front of the
    track's send when it has one; `after` the send is refused, because the
    send carries the channel to the master and whatever follows it is not
    heard. The master's return is first on its track and stays first: an add
    is always after something or at the back.
    """
    chains, ids = reading.get("chains", {}), reading.get("ids", {})
    uids = reading.get("uids") or {}
    track, name, after = request.track, request.name, request.after
    if track not in chains:
        raise Conflict(f"the running rig has no track {track!r}: it has {sorted(chains)}")
    if name in chains:
        raise Conflict(f"{name!r} is a track of the running rig; give the processor another name")
    holder = _track_of(chains, name)
    if holder is not None:
        what = "give a 'name'" if not request.named else "give another name"
        raise Conflict(f"{name!r} is already on the rig, on track {holder!r}: {what}")
    chain = list(chains[track])
    sends = [index for index, held in enumerate(chain) if is_send(track, held, uids.get(held))]
    if after is not None:
        if after not in chain:
            raise Conflict(f"there is no processor {after!r} on track {track!r}: it has {chain}")
        if chain.index(after) in sends:
            raise Conflict(
                f"nothing goes after {after!r}: the send must stay last, since it is what "
                "carries the channel to the master")
        index = chain.index(after) + 1
    else:
        index = sends[0] if sends else len(chain)
    before = chain[index] if index < len(chain) else None
    return Addition(
        track=track, name=name, uri=request.uri, after=after, chain=chain,
        expected=chain[:index] + [name] + chain[index:],
        track_id=ids[track], before_id=ids[before] if before is not None else 0,
        add_to_back=before is None,
    )


def remove_request(body: Any) -> str:
    """The processor a remove names, or `AgentError` (400)."""
    return _name(_object(body), "processor")


@dataclass(frozen=True)
class Removal:
    """A remove decided on. `plugin` is `{"uri", "uid", "type"}` as Sushi's
    session gave it, which with `track` and `chain` is what an undo needs to
    put the processor back where it was."""

    processor: str
    track: str
    chain: list[str]
    expected: list[str]
    plugin: dict[str, Any]
    processor_id: int
    track_id: int


def check_remove(reading: dict[str, Any], processor: str) -> Removal:
    """What removing `processor` from the running rig is, or `Conflict` (409)
    for a track, a send or return, or a processor the rig does not have; or
    `Unconfirmed` (502) when Sushi would not say which plugin it is.

    `reading` is `live.read_live_removal`'s: `{"chains", "ids", "plugin"}`.
    A remove that could not say what it removed could not be undone, so it is
    not made: refusing is the loud failure, removing anyway the silent one.
    """
    chains, ids = reading.get("chains", {}), reading.get("ids", {})
    if processor in chains:
        raise Conflict(f"{processor!r} is a track, not a processor on one")
    track = _track_of(chains, processor)
    if track is None:
        raise Conflict(
            f"the running rig has no processor {processor!r}: it has "
            f"{_on_the_rig(chains, with_tracks=False)}")
    plugin = reading.get("plugin")
    uid = (plugin or {}).get("uid") or (reading.get("uids") or {}).get(processor)
    protected = protected_processor(track, processor, uid)
    if protected:
        raise Conflict(f"{protected}: the compiler's, and never removed")
    if not plugin or not (plugin.get("uri") or plugin.get("uid")):
        raise Unconfirmed(
            f"Sushi could not say which plugin {processor!r} is (its session lists no "
            "URI or uid for it), so it is not removed: the log could not say how to "
            "put it back")
    chain = list(chains[track])
    return Removal(
        processor=processor, track=track, chain=chain,
        expected=[name for name in chain if name != processor],
        plugin={key: plugin.get(key) for key in ("uri", "uid", "type")},
        processor_id=ids[processor], track_id=ids[track],
    )


def confirm_chain(chains: dict[str, list[str]], track: str, expected: list[str], what: str) -> None:
    """`Unconfirmed` (502) unless `track`'s chain, read back after an edit, is
    `expected`. Sushi's acknowledgement says it carried out a command; the
    read-back says the command did what the agent meant, and only that is
    logged. A chain someone else changed in between (the panel's listener is
    not behind the agent's lock) fails here too, which is the honest answer."""
    if track not in chains:
        raise Unconfirmed(f"Sushi confirmed it {what}, but the rig read back has no track {track!r}")
    if chains[track] != expected:
        raise Unconfirmed(
            f"Sushi confirmed it {what}, but {track!r} read back {chains[track]}, not "
            f"{expected}: read /live before trying again")


# =========================================================================== #
# (end of the library and chain edits' decisions)
# =========================================================================== #


@dataclass(frozen=True)
class Live:
    """Everything the live routes need from Sushi, each a blocking call. A
    request makes one connection (`connect`), does all its reading and changing
    on it, and closes it (`disconnect`): each connection builds a dozen
    sub-controllers, makes an RPC and starts a notification thread, so a read
    and a change on separate ones paid that twice for nothing. Every other
    call takes the connection first.

    The reads are targeted -- they cost what the request names, not a walk of
    every parameter of the rig -- and their answers are what the `check_*`
    functions decide on. Defaults to `live.py`.

    One connection per request, not one held for the agent's life: a held one
    would need reconnecting when Sushi restarts (which `up`, `down` and
    `restart` do on purpose) and the failure to reconnect would be state that
    outlives the request that met it. Not run against a live Sushi.
    """

    connect: Callable[[], Any]
    disconnect: Callable[[Any], None]
    snapshot: Callable[[Any], dict[str, Any]]  # capture + "parameter_info"
    parameter: Callable[[Any, str, str], dict[str, Any]]  # (processor, parameter) -> reading
    bypass: Callable[[Any, str], dict[str, Any]]  # (processor) -> reading
    plan_move: Callable[[Any, str, int], Move]  # decides, sends nothing
    set_parameter: Callable[[Any, str, str, float], None]
    set_bypass: Callable[[Any, str, bool], None]
    send_move: Callable[[Any, Move], Move]  # sends a planned move
    # -- the chain edits (#32) --
    walk: Callable[[Any], dict[str, Any]]  # {"chains", "ids"}: an add's reading, and every read-back
    removal: Callable[[Any, str], dict[str, Any]]  # (processor) -> the walk plus its "plugin"
    add: Callable[[Any, Addition], None]  # sends a decided add
    remove: Callable[[Any, Removal], None]  # sends a decided remove

    @classmethod
    def at(cls, address: str = DEFAULT_GRPC_ADDRESS) -> "Live":
        m = live_module
        return cls(
            connect=functools.partial(m.connect, address),
            disconnect=lambda c: c.close(),
            snapshot=lambda c: m.read_rig(address, c),
            parameter=lambda c, proc, name: m.read_live_parameter(proc, name, address, c),
            bypass=lambda c, proc: m.read_live_bypass(proc, address, c),
            # uids: a send is known by Sushi's uid as well as its name (#32).
            plan_move=lambda c, proc, direction: m.plan_live_move(proc, direction, address, c, uids=True),
            set_parameter=lambda c, proc, name, value: m.set_live_parameter(proc, name, value, address, c),
            set_bypass=lambda c, proc, state: m.set_live_bypass(proc, state, address, c),
            send_move=lambda c, move: m.send_live_move(move, address, c),
            # -- the chain edits (#32) --
            walk=lambda c, uids=False: m.read_live_chains(address, c, uids=uids),
            removal=lambda c, proc: m.read_live_removal(proc, address, c),
            add=lambda c, a: m.add_live_processor(
                a.name, a.uri, a.track_id, a.before_id, a.add_to_back, address, c),
            remove=lambda c, r: m.remove_live_processor(r.processor_id, r.track_id, address, c),
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
        library: Callable[[], dict[str, Any]] | None = None,
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
        self._live_lock = asyncio.Lock()  # live: one change at a time; waits, not 409. Reads do not take it
        self.log: list[dict[str, Any]] = []  # operations made through /live, newest last
        # Sushi calls run here (see LIVE_WORKERS), not in the loop's executor.
        self._executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=LIVE_WORKERS, thread_name_prefix="sushi-live")
        self._in_flight: set = set()  # calls to Sushi that have not returned
        self._abandoned: set = set()  # ... of which the agent has stopped waiting for these
        # The library (#32): built on first use and kept; see `_library_now`.
        self._build_library = library or self._read_library
        self._library: dict[str, Any] | None = None
        self._library_lock = asyncio.Lock()

    @property
    def stuck_live_calls(self) -> int:
        """Calls to Sushi the agent gave up waiting for that have not returned."""
        return len(self._abandoned)

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
        self._edit_routes(app)
        return app

    async def health(self, request):
        return web.json_response({
            "ok": True,
            "agent": {
                "version": VERSION,
                "started_at": self.started_at,
                "uptime_seconds": round(self.clock() - self.started, 1),
                "busy": self.busy,
                "stuck_live_calls": self.stuck_live_calls,
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

    def _submit(self, fn: Callable[..., Any], *args: Any) -> concurrent.futures.Future:
        """Start one blocking call in the Sushi pool, or refuse (503) if the
        pool is full of calls that have not returned."""
        if len(self._in_flight) >= LIVE_WORKERS:
            raise web.HTTPServiceUnavailable(reason=(
                f"{len(self._in_flight)} calls to Sushi have not returned "
                f"({self.stuck_live_calls} of them for longer than {LIVE_TIMEOUT:g}s): "
                "Sushi is not answering, so no further call is made until some do"
            ))
        future = self._executor.submit(fn, *args)
        self._in_flight.add(future)
        # In the worker thread, as soon as the call returns; a set discard.
        future.add_done_callback(lambda f: (self._in_flight.discard(f), self._abandoned.discard(f)))
        return future

    def _abandon(self, future) -> None:
        """Count a call the agent has stopped waiting for as stuck.

        The done-callback that discards it runs in the worker thread, and this
        on the loop, so the call may return before, during or after: add first,
        then look, and discard if it has already returned. (A check first and an
        add after left a call that returned in between counted for ever.) Either
        order of the two discards is harmless, since a discard of what is not
        there does nothing.
        """
        self._abandoned.add(future)
        if future.done():
            self._abandoned.discard(future)

    async def _sushi(self, request, fn: Callable[..., Any], *args: Any, reading: bool) -> Any:
        """Run one blocking Sushi call off the loop, and say how it failed.

        - No answer within `LIVE_TIMEOUT` is 504, whatever was asked: the
          caller's lock is released, the stuck call is not stopped (see
          `LIVE_TIMEOUT`), and for a change it is not known whether it was
          applied. The exception carries `pending`, the call's future, for
          whoever holds the connection it is using.
        - A pool full of such calls is 503 (see `LIVE_WORKERS`).
        - No Sushi to connect to is 503 when a request is only being read
          (nothing has changed), and 502 once a change is under way.
        - Sushi refusing, or not confirming (`LiveError`), or failing any other
          way is 502, with the reason. That includes a `TimeoutError` raised by
          the call itself (a socket or gRPC deadline): on Python 3.11+ it is
          the same class as `asyncio.TimeoutError`, so it is told from the
          agent's own timeout by whether the call has returned.
        - elkpy not installed (`ElkpyMissing`) is not Sushi's doing: 500.
        Everything but the 503s is remembered for /health until a live change
        next works.
        """
        where = [request.method, request.path]
        future = self._submit(fn, *args)
        try:
            try:
                return await asyncio.wait_for(asyncio.wrap_future(future), LIVE_TIMEOUT)
            except asyncio.TimeoutError:
                if future.done() and not future.cancelled():
                    # It returned (perhaps in this instant), so this is its own
                    # result or its own error, not our deadline.
                    return future.result()
                reason = (
                    f"Sushi did not answer within {LIVE_TIMEOUT:g}s"
                    + ("" if reading else ", so the change may or may not have been applied")
                    + ": read /live before trying again"
                )
                self._remember(where, reason, kind="live")
                timeout = web.HTTPGatewayTimeout(reason=reason)
                if not future.cancelled():  # a call still running, not one that never started
                    self._abandon(future)
                    timeout.pending = future
                raise timeout
            except asyncio.CancelledError as exc:
                if not future.done():  # the client went; the call did not
                    self._abandon(future)
                    exc.pending = future
                raise
        except web.HTTPException:
            raise
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

    @contextlib.asynccontextmanager
    async def _connected(self, request):
        """One connection to Sushi for the whole of a request: opened here,
        closed when the request is done, however it ended.

        After a timeout or a cancellation a worker thread is still inside a call
        on the connection, and closing it from under the call is not ours to
        do. It is closed when that call returns, if it ever does (see
        `LIVE_TIMEOUT`); where it was the connect that timed out, the
        connection the call returns late is closed the same way.

        A close that fails does not change the answer to the request -- what it
        was asked has been done -- but is not silent: it is printed and
        remembered for /health, beside any failure the request had.
        """
        try:
            controller = await self._sushi(request, self.live.connect, reading=True)
        except (web.HTTPGatewayTimeout, asyncio.CancelledError) as exc:
            # The connect is still running (it timed out, or the client went);
            # the connection it returns late is nobody's, so close it then.
            self._close_when_it_returns(request, getattr(exc, "pending", None), None)
            raise
        pending = None
        try:
            yield controller
        except (web.HTTPGatewayTimeout, asyncio.CancelledError) as exc:
            pending = getattr(exc, "pending", None)
            raise
        finally:
            if pending is not None:
                self._close_when_it_returns(request, pending, controller)
            else:
                await self._close(request, controller)

    async def _close(self, request, controller) -> None:
        try:
            # Straight to the pool, not through `_submit`'s count: a close is
            # short, and refusing one because the pool is full of the stuck
            # calls it follows would leak the connection it was to release.
            await asyncio.wait_for(
                asyncio.wrap_future(self._executor.submit(self.live.disconnect, controller)),
                LIVE_TIMEOUT)
        except Exception as exc:  # noqa: BLE001 -- said, not raised over the answer
            self._close_failed(request, exc)

    def _close_failed(self, request, exc: BaseException) -> None:
        message = " ".join(
            f"closing the connection to Sushi failed ({request.method} {request.path}): "
            f"{type(exc).__name__}: {exc}".split())
        print(f"sushi-rig agent: {message}", file=sys.stderr, flush=True)
        self._remember_too(request, message)

    def _remember_too(self, request, message: str) -> None:
        """Remember a live problem without displacing one already remembered:
        /health keeps the reason the request failed, with this beside it."""
        if self.last_error is None:
            self._remember([request.method, request.path], message, kind="live")
        else:
            self.last_error.setdefault("also", []).append(message)

    def _close_when_it_returns(self, request, pending, controller) -> None:
        """The call that timed out (or was cancelled) is still running; when it
        returns, close the connection it was using (`controller`, or what it
        returned if it was the connect) and say how it came back."""
        if pending is None:  # the call never started: nothing is inside the connection
            if controller is not None:
                task = asyncio.ensure_future(self._close(request, controller))
                task.add_done_callback(lambda t: t.cancelled() or t.exception())
            return
        loop = asyncio.get_running_loop()
        started = self.clock()

        def returned_late() -> None:
            error = pending.exception()
            held = controller if controller is not None else (None if error else pending.result())
            how = f"failed with {type(error).__name__}: {error}" if error else "returned normally"
            message = (
                f"the call behind {request.method} {request.path} returned after the 504, "
                f"{self.clock() - started:.1f}s later, and {how}"
                + ("; if it was a change, it was applied" if not error and controller is not None else "")
            )
            print(f"sushi-rig agent: {message}", file=sys.stderr, flush=True)
            self._remember_too(request, message)
            if held is not None:
                task = asyncio.ensure_future(self._close(request, held))
                task.add_done_callback(lambda t: t.cancelled() or t.exception())

        def hand_to_loop(_future) -> None:  # in the worker thread
            try:
                loop.call_soon_threadsafe(returned_late)
            except RuntimeError:  # the loop has gone with the agent
                pass

        pending.add_done_callback(hand_to_loop)

    @contextlib.asynccontextmanager
    async def _connected_for_a_change(self, request):
        """A connection, then the live lock: the connection is made *outside*
        the lock, so a queue of fader writes each constructs its controller (a
        dozen sub-controllers, an RPC, a thread) while waiting rather than in
        turn, and only the read-decide-set that `previous` depends on is
        serialised. Busy is checked before connecting, so nothing connects to a
        Sushi that is coming up or going down, and again when the lock is won,
        since a lifecycle command may have started while this one waited.
        """
        self._refuse_while_busy()
        async with self._connected(request) as controller:
            async with self._live_lock:
                self._refuse_while_busy()
                yield controller

    def _record(self, op: str, args: dict[str, Any], previous: Any) -> dict[str, Any]:
        entry = log_entry(op, args, previous, datetime.now(timezone.utc).isoformat(timespec="seconds"))
        self.log.append(entry)
        trim_log(self.log)
        if (self.last_error or {}).get("kind") == "live":
            self.last_error = None  # a change that worked supersedes a live failure
        return entry

    async def live_state(self, request):
        """The whole running rig. Takes no part in `_live_lock`: a read changes
        nothing, so it cannot disturb the `previous` a change records, and a
        panel polling this must not make fader writes queue behind it. It is
        refused while a lifecycle command runs, as the changes are. A read made
        mid-change sees the rig before or after it, as any read of a live rig
        does.
        """
        self._refuse_while_busy()
        async with self._connected(request) as controller:
            rig = await self._sushi(request, self.live.snapshot, controller, reading=True)
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
        async with self._connected_for_a_change(request) as controller:
            reading = await self._sushi(
                request, self.live.parameter, controller, processor, parameter, reading=True)
            try:
                previous = check_parameter(reading, processor, parameter)
            except AgentError as exc:
                raise web.HTTPBadRequest(reason=str(exc))
            await self._sushi(
                request, self.live.set_parameter, controller, processor, parameter, value, reading=False)
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
        async with self._connected_for_a_change(request) as controller:
            reading = await self._sushi(request, self.live.bypass, controller, processor, reading=True)
            try:
                previous = check_bypass(reading, processor)
            except AgentError as exc:
                raise web.HTTPBadRequest(reason=str(exc))
            await self._sushi(
                request, self.live.set_bypass, controller, processor, bypassed, reading=False)
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
        async with self._connected_for_a_change(request) as controller:
            # One walk, one decision (`live.decide_move`), and the command
            # sent is the one decided on: success is what Sushi confirmed,
            # not a word in a status line, and a processor found already
            # at that end only now is "unchanged", not a failure.
            planned = await self._sushi(
                request, self.live.plan_move, controller, processor, DIRECTIONS[direction],
                reading=True)
            try:
                check_move(planned)
            except AgentError as exc:
                raise web.HTTPBadRequest(reason=str(exc))
            if planned.outcome == "unchanged":
                return web.json_response({
                    "ok": True, "changed": False, "chain": planned.chain,
                    "message": planned.message,
                })
            moved = await self._sushi(request, self.live.send_move, controller, planned, reading=False)
            entry = self._record(
                "move", {"processor": processor, "direction": direction}, planned.chain)
        return web.json_response({"ok": True, "changed": True, "message": moved.message, **entry})

    async def release_workers(self, app=None) -> None:
        """Stop handing Sushi calls to the pool. Calls still inside Sushi are
        not interrupted (they cannot be); the interpreter waits for them at exit."""
        self._executor.shutdown(wait=False, cancel_futures=True)

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

    # ======================================================================= #
    # The library and chain edits (#32): GET /library, POST /live/add|remove
    # ======================================================================= #

    def _edit_routes(self, app) -> None:
        app.router.add_get("/library", self.library_route)
        app.router.add_post("/live/add", self.live_add)
        app.router.add_post("/live/remove", self.live_remove)

    def _read_library(self) -> dict[str, Any]:
        from .paths import checkout_root

        return library_module.read_library(self.root or checkout_root())

    async def _library_now(self) -> dict[str, Any]:
        """The library, built on first use and kept for the agent's life: the
        manifest, `plugins/` and the profiles change when the checkout does,
        and the agent is restarted with it. Built off the loop (it reads a
        hundred files or so), once however many requests ask at the same
        time. A build that fails is 500 with the reason and is not kept, so
        the next request tries again."""
        if self._library is not None:
            return self._library
        async with self._library_lock:
            if self._library is None:
                try:
                    built = await asyncio.get_running_loop().run_in_executor(None, self._build_library)
                except library_module.LibraryError as exc:
                    self._remember(["library"], str(exc))
                    raise web.HTTPInternalServerError(reason=str(exc))
                self._library = built
        return self._library

    async def library_route(self, request):
        """Every plugin in the manifest, for the Library screen to pick from.
        Needs no rig: it is files, not Sushi."""
        return web.json_response({"ok": True, **(await self._library_now())})

    async def live_add(self, request):
        """Add a plugin from the library to a track of the running rig.

        Body `{"track", "uri", "name"?, "after"?}`. Refused (400) before Sushi is
        contacted for a bad shape, a URI not in the manifest or one Sushi cannot
        host; refused (409) from a walk of the chains for a track the rig lacks,
        a name it already has or an `after` not on the track. Sent as decided,
        then confirmed from a second walk. Logged as op "add" with the name
        actually used in `args` (an undo removes it by that) and `previous` the
        track's chain before.
        """
        body = await self._json_body(request)
        library = await self._library_now()
        try:
            asked = add_request(body, library)
        except AgentError as exc:
            raise web.HTTPBadRequest(reason=str(exc))
        async with self._connected_for_a_change(request) as controller:
            reading = await self._sushi(request, self.live.walk, controller, True, reading=True)
            try:
                addition = check_add(reading, asked)
            except Conflict as exc:
                raise web.HTTPConflict(reason=str(exc))
            await self._sushi(request, self.live.add, controller, addition, reading=False)
            after = await self._sushi(request, self.live.walk, controller, reading=False)
            what = f"added {addition.name!r} to {addition.track!r}"
            self._confirm(request, after, addition.track, addition.expected, what)
            entry = self._record("add", {
                "track": addition.track, "uri": addition.uri, "name": addition.name,
                "after": addition.after,
            }, list(addition.chain))
        return web.json_response({
            "ok": True, **entry, "chain": addition.expected,
            "message": f"added {addition.name} to {addition.track}",
        })

    async def live_remove(self, request):
        """Remove one processor from the running rig, by name.

        Body `{"processor"}`. Refused (409) for a track, a channel's send, the
        master's return, or a processor the rig lacks; 502 if Sushi will not
        say which plugin it is, since the log must be able to put it back.
        Confirmed from a walk afterwards. Logged as op "remove" with `previous`
        `{"track", "chain", "uri", "uid", "type"}`: where it was and what it
        was, which is what an undo re-adds. Its parameter values are not
        recorded, so a re-add would come back at the plugin's defaults.
        """
        try:
            processor = remove_request(await self._json_body(request))
        except AgentError as exc:
            raise web.HTTPBadRequest(reason=str(exc))
        async with self._connected_for_a_change(request) as controller:
            reading = await self._sushi(request, self.live.removal, controller, processor, reading=True)
            try:
                removal = check_remove(reading, processor)
            except Conflict as exc:
                raise web.HTTPConflict(reason=str(exc))
            except Unconfirmed as exc:
                self._remember([request.method, request.path], str(exc), kind="live")
                raise web.HTTPBadGateway(reason=str(exc))
            await self._sushi(request, self.live.remove, controller, removal, reading=False)
            after = await self._sushi(request, self.live.walk, controller, reading=False)
            what = f"removed {removal.processor!r} from {removal.track!r}"
            chains = self._confirm(request, after, removal.track, removal.expected, what)
            if _track_of(chains, removal.processor) is not None:
                self._unconfirmed(request, f"Sushi confirmed it {what}, but it is still on the rig")
            entry = self._record("remove", {"processor": removal.processor}, {
                "track": removal.track, "chain": list(removal.chain), **removal.plugin,
            })
        return web.json_response({
            "ok": True, **entry, "chain": removal.expected,
            "message": f"removed {removal.processor} from {removal.track}",
        })

    def _confirm(
        self, request, reading: Any, track: str, expected: list[str], what: str,
    ) -> dict[str, list[str]]:
        """The chains of a read-back, confirmed (`confirm_chain`), or 502. The
        one place a read-back's chains are taken from it, so a reading with
        none is a 502 like any other read-back that is wrong, not a KeyError's
        500 (found by review)."""
        chains = reading.get("chains") if isinstance(reading, dict) else None
        if not isinstance(chains, dict):
            self._unconfirmed(request, f"Sushi confirmed it {what}, but the read-back had no chains")
        try:
            confirm_chain(chains, track, expected, what)
        except Unconfirmed as exc:
            self._unconfirmed(request, str(exc))
        return chains

    def _unconfirmed(self, request, reason: str) -> None:
        """502, remembered for /health, and nothing logged: the log holds edits
        the rig was read back to have."""
        self._remember([request.method, request.path], reason, kind="live")
        raise web.HTTPBadGateway(reason=reason)

    # ======================================================================= #
    # (end of the library and chain edits' routes)
    # ======================================================================= #


def serve(host: str, port: int, root: Path | None = None) -> int:
    """Run the agent until interrupted."""
    if web is None:
        raise AgentError("aiohttp is not installed; pip install -e tool[agent]")

    agent = Agent(root=root)
    app = agent.app()
    app.on_shutdown.append(agent.announce_stopping)
    app.on_cleanup.append(agent.release_workers)
    print(f"sushi-rig agent {VERSION} on http://{host}:{port}  (Ctrl+C to stop)", flush=True)
    web.run_app(app, host=host, port=port, print=None, handle_signals=True)
    return 0
