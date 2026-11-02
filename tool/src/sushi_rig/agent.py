"""The rig agent (#29): one local-first process that owns the rig.

Control reaches the rig from three places today: the panel speaks OSC to Sushi,
`listen` handles SAVE and reorder, the CLI speaks gRPC. Every v2 feature needs
one place that owns the rig instead, and this is its first slice: the lifecycle
and the two things worth reading about a rig, served over HTTP on localhost.

    GET  /health        the agent and the rig, in one answer
    GET  /rig           what is running, if anything (`describe_rig`), and which
                        Program Change selects which rig when the rig's map switches rigs
    GET  /doctor        `doctor`'s checks, as data
    POST /rig/up        {"config_name" (required), "rig", "headless", "tuner", "amp", "amp_model"}
    POST /rig/down      {"force"}
    POST /rig/restart   {}
    GET  /live          the running rig's parameters, bypass and chains, with each parameter's range
    POST /live/parameter  {"processor", "parameter", "value"}   value is normalised, 0 to 1
    POST /live/bypass   {"processor", "bypassed"}
    POST /live/move     {"processor", "direction": "up" | "down"}
    GET  /live/log      the changes made through /live, newest last (the last 500), and
                        `first`, the index of the oldest one held
    POST /live/undo     {} -- put back what the newest change not yet undone replaced;
                        {"skip": true} -- give up on the newest one that cannot be

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
undo comes from. It lives in this process and is lost when the agent stops.

## Undo reads the log and writes to it

`POST /live/undo` takes the newest entry that is not an undo and has not been
undone, and applies its `previous` through the same functions the routes use:
`set_live_parameter`, `set_live_bypass`, and a move the opposite way. It is a
change like the others (same busy check, same lock, same `LIVE_TIMEOUT`, same
503/502/504/409), and is logged like one: `op: "undo"`, `undoes: <index>`, `args`
what was applied and `previous` what that replaced, so a redo can be built from
the entry alone.

An entry's index is how many entries came before it since the agent started, not
its place in the list: the list is trimmed at the front, and a position would
change under the `undoes` that refer to it. `GET /live/log` says the index of the
first entry it holds (`first`). An undo is always newer than what it undoes, so
trimming can drop an entry and keep its undo but never the reverse, and one
backward pass over the log finds the next entry to undo.

Refused rather than guessed, with nothing applied or logged. A 409 carries a
`code` beside its `reason` so a client need not read prose: `busy` (a lifecycle
command is running, checked first); `nothing_to_undo` (an empty log, or one with
nothing left); `stale` (the rig is not as the entry assumes: a move's processor
is not exactly where the move left it, or a parameter or bypass is no longer at
the value the change set, or Sushi never said what to put back -- an undo must
not overwrite what was changed meanwhile). A processor the rig no longer has is
400, as the other routes answer it. A refused entry is not skipped to reach an
older one: that would undo something the caller did not ask about.

That would wedge the stack, though: the panel's listener moves processors
outside the agent's lock, so the newest entry can become impossible to undo for
good. `{"skip": true}` is the way out. It asks the same question of the newest
entry without sending anything, and if the entry is refused records an
`op: "stale"` entry (`stale: <index>`, with the reason), after which the next undo
goes on to the one before. An entry that can be undone is not skipped (409
`not_stale`). Skip was chosen over naming an entry to undo because a named index
would let a request undo out of order. Any other body is 400, since a body that
asked for more must not be answered as if understood.

A rig switch made by a Program Change (`op: "rig"`) is a barrier: nothing
before it is undone (409 `nothing_to_undo`, saying so), since it was done to
the rig that was up then. Out of scope: undoing an undo (a redo). The entries carry what it needs; it is
not built. Also not verified against a live Sushi: it is shown against the fake.

## MIDI: the bindings Sushi cannot serve (#34, #93)

When the running rig's compiled config carries `_midi_agent`, the agent opens
the controller's port itself and serves it: a pad toggles a plugin's bypass
(and a CC switches it) through the calls `POST /live/bypass` makes, a pad on
a mute toggles a track's `mute` parameter, and a Program Change switches
rigs by running `down` and then `up --rig` with the options `restart` would
bring back. Each change is in the live log with `source: "midi"`. The
listener starts when a rig comes up (and at the agent's own start, for a rig
already up) and stops before anything takes the rig down. A port that will
not open is a named problem in `/health` (`agent.midi`), not a retry loop, and
nothing the port does reaches the HTTP side. The decisions are in
`midi_listener.py`; the wiring is the MIDI section at the end of `Agent`.

## Local-first, so localhost first

The CLI binds it to 127.0.0.1:8029 unless told otherwise. `--host 0.0.0.0`
opens it to the LAN, and until the permission split the issue describes exists
(local: everything; remote: start, stop, load, parameters) that is the
operator's call to make, not a default.
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

from . import live as live_module
from . import midi_listener
from .live import DEFAULT_GRPC_ADDRESS, ElkpyMissing, LiveError, Move
from .midi_listener import MidiError
from .rig import describe_rig, restart_arguments

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

    @property
    def reason(self) -> str:
        """Why it failed, in its own words where it gave any."""
        return self.stderr.strip() or f"exit status {self.returncode}"


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


def log_entry(
    op: str, args: dict[str, Any], previous: Any, at: str,
    undoes: int | None = None, stale: int | None = None,
) -> dict[str, Any]:
    """One operation-log entry. `previous` is what the change replaced, or None
    where it is not known; it is always present so an undo reads one shape.

    `undoes` is the index (see `pick_undo`) of the entry an undo put back, and
    `stale` that of an entry a skip gave up on. Each is a key of its own kind of
    entry alone: ordinary entries keep their four.
    """
    entry = {"at": at, "op": op, "args": args, "previous": previous}
    if undoes is not None:
        entry["undoes"] = undoes
    if stale is not None:
        entry["stale"] = stale
    return entry


def trim_log(log: list, limit: int) -> None:
    """Drop the oldest entries, in place, until `limit` remain."""
    if len(log) > limit:
        del log[: len(log) - limit]


# --------------------------------------------------------------------------- #
# Undo: the decisions, pure
# --------------------------------------------------------------------------- #

OPPOSITE = {"up": "down", "down": "up"}

# How far a parameter read back may be from the value an undo expects to find
# before it counts as changed meanwhile. Sushi holds a float32 and a value read
# back differs from the one sent in the seventh digit; this is far above that
# and far below a hand on a fader. A guess: no live Sushi was to hand.
PARAMETER_TOLERANCE = 1e-4


class StaleUndo(AgentError):
    """An entry that cannot be put back because the rig is no longer as the
    entry assumes, or never told us what to put back. The undo route answers it
    409 (`code: "stale"`), and a skip may give up on the entry. Other
    `AgentError`s keep the status the other routes give them."""


def undo_request(body: Any) -> bool:
    """Whether the request asks to skip, or `AgentError` for anything else than
    an empty body or `{"skip": true}` (or false). The route undoes the newest
    change and nothing else; a body naming an index or a processor would be
    ignored, and a request ignored while looking understood is how the wrong
    thing gets undone."""
    if body is None:
        return False
    if isinstance(body, dict) and set(body) <= {"skip"} and isinstance(body.get("skip", False), bool):
        return body.get("skip", False)
    raise AgentError(
        f"the body of an undo must be empty, or {{\"skip\": true}}, not {body!r}: "
        "it undoes the newest change")


class UndoBarrier(AgentError):
    """A rig switch stands between the undo and anything older: answered 409
    `nothing_to_undo` with this reason."""


def pick_undo(log: list[dict[str, Any]], first: int) -> tuple[int, dict[str, Any]] | None:
    """`(index, entry)` of the newest entry that is not an undo or a skip and
    has been neither undone nor marked stale, or None. `first` is the index of
    `log[0]`.

    An index counts every entry recorded since the agent started, so it is
    stable as the log is trimmed at the front, which a position in the list is
    not. An undo or a stale marker is recorded after what it names, so a single
    pass from the newest end meets each before its target; and trimming, which
    drops the oldest, can lose a target and keep its marker but never the
    reverse, so an entry that is still here is never mistaken for one not yet
    dealt with.

    A rig switch (`op: "rig"`, made by a Program Change) is a barrier, raising
    `UndoBarrier`: what came before it was done to another rig, and the rig now
    up may have processors of the same names, so undoing it would change one
    nobody touched since. It is neither undone nor skipped past.
    """
    handled: set[int] = set()
    for position in range(len(log) - 1, -1, -1):
        entry = log[position]
        if entry["op"] == "rig":
            raise UndoBarrier(
                f"nothing to undo: a rig switch to {entry['args'].get('rig')!r} is in the way, and "
                "what came before it was done to the rig that was up then")
        if entry["op"] == "undo":
            handled.add(entry["undoes"])
        elif entry["op"] == "stale":
            handled.add(entry["stale"])
        elif first + position not in handled:
            return first + position, entry
    return None


def undo_inverse(entry: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """`(op, args)` that puts an entry back, in the arguments the route for
    that op takes, or `StaleUndo` if it cannot be done without guessing.

    A parameter goes back to its previous value and a bypass to its previous
    state. A move goes back by the opposite step, which is exact because a move
    is a step: the processor swapped places with its neighbour. Where `previous`
    is None Sushi would not say what it was, so there is nothing to put back.
    """
    op, args, previous = entry["op"], entry["args"], entry["previous"]
    if op not in ("parameter", "bypass", "move"):
        raise AgentError(f"cannot undo an entry of op {op!r}")
    if previous is None:
        raise StaleUndo(
            f"cannot undo the {op} of {args['processor']!r}: Sushi did not say what "
            f"{args['processor']} was before it, so there is nothing to put back")
    if op == "parameter":
        return op, {"processor": args["processor"], "parameter": args["parameter"], "value": previous}
    if op == "bypass":
        return op, {"processor": args["processor"], "bypassed": previous}
    return op, {"processor": args["processor"], "direction": OPPOSITE[args["direction"]]}


def chain_after_move(chain: list[str], processor: str, direction: str) -> list[str] | None:
    """The chain as a one-step move leaves it, or None if the processor is not
    on it or already at that end (such a move is never logged). A step swaps the
    processor with its neighbour, which is all the arithmetic undo needs."""
    if processor not in chain:
        return None
    i = chain.index(processor)
    j = i + DIRECTIONS[direction]
    if not 0 <= j < len(chain):
        return None
    after = list(chain)
    after[i], after[j] = after[j], after[i]
    return after


def check_undo_move(entry: dict[str, Any], current: list[str] | None) -> None:
    """`StaleUndo` unless `current` -- the chain the processor is on now, None
    if it is on none -- is exactly what the move left. Undoing a move whose chain
    has changed since would move the processor one step in a chain the entry knows
    nothing about, which is a different change from the one asked for."""
    args = entry["args"]
    expected = chain_after_move(entry["previous"], args["processor"], args["direction"])
    if current != expected:
        raise StaleUndo(
            f"cannot undo the move of {args['processor']!r}: it is no longer where the move "
            f"left it (the chain is now {current}, the move left {expected})")


def check_undo_state(entry: dict[str, Any], current: Any) -> None:
    """`StaleUndo` unless a parameter or bypass is still as the change left it
    (`current` is what the rig says now), so an undo never overwrites a value
    changed meanwhile -- by the panel, or by a later change another undo will
    reach first. A value Sushi would not say (None) cannot be confirmed, and is
    refused rather than assumed."""
    args = entry["args"]
    if entry["op"] == "parameter":
        left = args["value"]
        same = (isinstance(current, (int, float)) and not isinstance(current, bool)
                and math.isclose(current, left, rel_tol=0.0, abs_tol=PARAMETER_TOLERANCE))
    else:
        left = args["bypassed"]
        same = isinstance(current, bool) and current == left
    if not same:
        raise StaleUndo(
            f"cannot undo the {entry['op']} of {args['processor']!r}: it is no longer as the "
            f"change left it (it is now {current!r}, the change left {left!r})")


def _conflict(reason: str, code: str):
    """A 409 that carries a `code` beside its `reason`, so a client tells busy,
    nothing to undo and stale apart without reading prose."""
    exc = web.HTTPConflict(reason=reason)
    exc.agent_code = code
    return exc


def sushi_unreachable(exc: BaseException) -> bool:
    try:
        from elkpy.sushierrors import SushiUnavailableError
    except ImportError:
        return False
    return isinstance(exc, SushiUnavailableError)


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

    @classmethod
    def at(cls, address: str = DEFAULT_GRPC_ADDRESS) -> "Live":
        m = live_module
        return cls(
            connect=functools.partial(m.connect, address),
            disconnect=lambda c: c.close(),
            snapshot=lambda c: m.read_rig(address, c),
            parameter=lambda c, proc, name: m.read_live_parameter(proc, name, address, c),
            bypass=lambda c, proc: m.read_live_bypass(proc, address, c),
            plan_move=lambda c, proc, direction: m.plan_live_move(proc, direction, address, c),
            set_parameter=lambda c, proc, name, value: m.set_live_parameter(proc, name, value, address, c),
            set_bypass=lambda c, proc, state: m.set_live_bypass(proc, state, address, c),
            send_move=lambda c, move: m.send_live_move(move, address, c),
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
        log_limit: int = LOG_LIMIT,

        midi: midi_listener.World | None = None,
    ) -> None:
        self.root = root
        self.log_limit = log_limit
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
        self._trimmed = 0  # entries dropped from the front, so log[i] has index _trimmed + i
        # Sushi calls run here (see LIVE_WORKERS), not in the loop's executor.
        self._executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=LIVE_WORKERS, thread_name_prefix="sushi-live")
        self._in_flight: set = set()  # calls to Sushi that have not returned
        self._abandoned: set = set()  # ... of which the agent has stopped waiting for these
        self._midi_init(midi)  # the MIDI listener's state: see the MIDI section

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
        app.router.add_post("/live/undo", self.live_undo)
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
                "midi": self._midi_status(),
            },
            "rig": self.describe(),
        })

    async def rig(self, request):
        return web.json_response(await self._rig_answer())

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

        outcome, args = await self._run_lifecycle(command, [args])
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
        answer["reason"] = outcome.reason
        return web.json_response(answer, status=504 if outcome.abandoned else 409)

    async def _run_lifecycle(
        self, command: str, steps: list[list[str]], fallback: list[str] | None = None,
    ) -> tuple[Outcome, list[str]]:
        """Run lifecycle commands in order under the lifecycle lock, stopping
        at the first that fails: one for a route, `down` then `up` for a rig
        switched by Program Change, which therefore takes exactly the path a
        request does. Answers what the last command run said, and its
        arguments; a failure is remembered for /health.

        `fallback` is run when a step after the first fails, which for a rig
        switch is the `up` of the rig that `down` took away: the player gets
        the old rig back rather than silence. The failure is still what is
        answered and remembered, with what the fallback did beside it.

        The MIDI listener is stopped before every command, `up` too (a
        listener can still be following a rig that died outside the agent, and
        a pad pressed while `up` runs must not reach the rig coming up), and
        afterwards follows the rig as it now is, whatever happened.
        """
        self._refuse_while_busy()
        async with self._lock:
            self.busy = command
            try:
                await self._midi_stop(f"stopped while '{command}' runs")
                restored = ""
                for step, args in enumerate(steps):
                    outcome = await self.run(args)
                    if not outcome.ok:
                        if fallback is not None and step > 0:
                            back = await self.run(fallback)
                            restored = (f"; {fallback[1]} was brought back" if back.ok else
                                        f"; bringing {fallback[1]} back failed too: {back.reason}")
                        break
            finally:
                self.busy = None
                await self._midi_sync()
            if outcome.ok and (self.last_error or {}).get("kind") == "lifecycle":
                # A later success supersedes the old failure: left in place,
                # a healthy agent reports a refused request forever and
                # /health cannot be trusted to say "nothing is wrong now".
                # Only a lifecycle failure, though: an unexpected error from
                # /doctor or /rig says nothing about the rig coming up, so a
                # `down` that works must not forget it.
                self.last_error = None
            if not outcome.ok:
                self._remember(args, outcome.reason + restored, kind="lifecycle")
                if restored:
                    outcome = Outcome(False, outcome.returncode, outcome.stdout, outcome.stderr.strip() + restored)
        return outcome, args

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
            raise _conflict(f"busy: '{self.busy}' is still running", "busy")

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

    def _record(self, op: str, args: dict[str, Any], previous: Any, **marks: int) -> dict[str, Any]:
        """Append one entry (`marks`: `undoes` or `stale`, see `log_entry`)."""
        entry = log_entry(
            op, args, previous, datetime.now(timezone.utc).isoformat(timespec="seconds"), **marks)
        self.log.append(entry)
        before = len(self.log)
        trim_log(self.log, self.log_limit)
        self._trimmed += before - len(self.log)
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
        return web.json_response({
            "ok": True, "limit": self.log_limit, "first": self._trimmed, "log": list(self.log),
        })

    # The read-check-set of each change, shared by its route and by undo, so an
    # undo is checked by the very code a change is. `expect` is called with the
    # value (or chain) read, after the existing checks and before anything is
    # sent, and may raise (undo uses it to refuse a rig that has moved on);
    # `dry_run` stops short of sending, to ask whether the change would go
    # through (a skip). Both raise `AgentError` for what the rig lacks.

    async def _apply_parameter(
        self, request, controller, processor: str, parameter: str, value: float,
        *, expect: Callable[[Any], None] | None = None, dry_run: bool = False,
    ) -> float:
        """Set one parameter and return the value it replaced."""
        reading = await self._sushi(
            request, self.live.parameter, controller, processor, parameter, reading=True)
        previous = check_parameter(reading, processor, parameter)
        if expect:
            expect(previous)
        if not dry_run:
            await self._sushi(
                request, self.live.set_parameter, controller, processor, parameter, value, reading=False)
        return previous

    async def _apply_bypass(
        self, request, controller, processor: str, bypassed: bool,
        *, expect: Callable[[Any], None] | None = None, dry_run: bool = False,
    ) -> bool | None:
        """Bypass or un-bypass one processor and return the state it replaced."""
        reading = await self._sushi(request, self.live.bypass, controller, processor, reading=True)
        previous = check_bypass(reading, processor)
        if expect:
            expect(previous)
        if not dry_run:
            await self._sushi(
                request, self.live.set_bypass, controller, processor, bypassed, reading=False)
        return previous

    async def _apply_move(
        self, request, controller, processor: str, direction: str,
        *, expect: Callable[[Any], None] | None = None, dry_run: bool = False,
    ) -> tuple[Move, Move | None]:
        """Move one processor a step; `(planned, moved)`, where `moved` is None
        if nothing was sent (already at that end, or a dry run). One walk, one
        decision (`live.decide_move`), and the command sent is the one decided
        on: success is what Sushi confirmed, not a word in a status line, and a
        processor found already at that end only now is "unchanged", not a
        failure. `planned.chain` is the chain before."""
        planned = await self._sushi(
            request, self.live.plan_move, controller, processor, DIRECTIONS[direction], reading=True)
        check_move(planned)
        if expect:
            expect(planned.chain)
        if planned.outcome == "unchanged" or dry_run:
            return planned, None
        return planned, await self._sushi(request, self.live.send_move, controller, planned, reading=False)

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
            try:
                previous = await self._apply_parameter(request, controller, processor, parameter, value)
            except AgentError as exc:
                raise web.HTTPBadRequest(reason=str(exc))
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
            try:
                previous = await self._apply_bypass(request, controller, processor, bypassed)
            except AgentError as exc:
                raise web.HTTPBadRequest(reason=str(exc))
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
            try:
                planned, moved = await self._apply_move(request, controller, processor, direction)
            except AgentError as exc:
                raise web.HTTPBadRequest(reason=str(exc))
            if moved is None:
                return web.json_response({
                    "ok": True, "changed": False, "chain": planned.chain,
                    "message": planned.message,
                })
            entry = self._record(
                "move", {"processor": processor, "direction": direction}, planned.chain)
        return web.json_response({"ok": True, "changed": True, "message": moved.message, **entry})

    # How undo puts each op back: (previous, state) -- what the undo replaced,
    # and the state it set. `args` is `undo_inverse`'s, the route's own words.

    async def _undo_parameter(self, request, controller, undone, args, dry_run):
        previous = await self._apply_parameter(
            request, controller, args["processor"], args["parameter"], args["value"],
            expect=lambda current: check_undo_state(undone, current), dry_run=dry_run)
        return previous, args

    async def _undo_bypass(self, request, controller, undone, args, dry_run):
        previous = await self._apply_bypass(
            request, controller, args["processor"], args["bypassed"],
            expect=lambda current: check_undo_state(undone, current), dry_run=dry_run)
        return previous, args

    async def _undo_move(self, request, controller, undone, args, dry_run):
        # Where the move left the chain, the opposite step is always possible
        # and restores it (tested: `chain_after_move`), so `check_undo_move`
        # passing is what stands between this and an "unchanged" that would be
        # logged as an undo having done something.
        planned, _ = await self._apply_move(
            request, controller, args["processor"], args["direction"],
            expect=lambda chain: check_undo_move(undone, chain), dry_run=dry_run)
        return planned.chain, {"processor": args["processor"], "chain": undone["previous"]}

    async def live_undo(self, request):
        """Put back what the newest change not yet undone replaced (`pick_undo`),
        through the same read-check-set the routes make, and log the undo.

        Body: none, or `{"skip": true}`. Statuses and their `code`:

        - 409 `busy`: a lifecycle command is running (checked first, so it is
          never reported as anything else).
        - 409 `nothing_to_undo`: an empty log, or one with nothing left. A
          conflict with the log's state rather than a missing resource, so 409
          and not 404; said without a connection, since no rig need be up.
        - 409 `stale`: the rig is no longer as the entry assumes (a move's
          processor is elsewhere, a parameter or bypass was changed meanwhile),
          or Sushi never said what to put back. Nothing is applied or logged,
          and the entry is not skipped to reach an older one.
        - 400, no code: what the other routes answer for the same refusal, such
          as a processor the rig no longer has.

        The target is chosen under the live lock, from the log as it is then:
        two undos queued together undo two entries, not one twice.

        Skip is the way out of a refusal. The panel's listener moves processors
        outside the agent's lock, so the newest entry can become impossible to
        undo for good, and with no way past it every older change would be
        unreachable. `{"skip": true}` asks the same question of the newest
        entry without sending anything, and if it is refused, records an
        `op: "stale"` entry (`stale: <index>`, the reason in `args`) after which
        the next call goes on to the one before. The history stays honest: the
        entry is marked, not removed. An entry that *can* be undone is not
        skipped (409 `not_stale`): giving up on a change that would go through
        is the one thing skip must not do by mistake. Chosen over targeting an
        entry by index, which would let a request undo out of order.

        Undoing an undo is not supported: an undo is passed over, and a redo is
        for later. Not run against a live Sushi.
        """
        try:
            skip = undo_request(await self._json_body(request))
        except AgentError as exc:
            raise web.HTTPBadRequest(reason=str(exc))
        self._refuse_while_busy()  # before the fast path: busy is the truer answer
        if self._pick_undo() is None:
            raise _conflict("nothing to undo", "nothing_to_undo")
        async with self._connected_for_a_change(request) as controller:
            picked = self._pick_undo()  # the log may have changed while waiting
            if picked is None:
                raise _conflict("nothing to undo", "nothing_to_undo")
            index, undone = picked
            try:
                op, args = undo_inverse(undone)
                previous, state = await {
                    "parameter": self._undo_parameter, "bypass": self._undo_bypass,
                    "move": self._undo_move,
                }[op](request, controller, undone, args, skip)
            except AgentError as exc:
                if not skip:
                    hint = (f"{exc}; to give up on this entry and go on to the one before it, "
                            'send {"skip": true}')
                    if isinstance(exc, StaleUndo):
                        raise _conflict(hint, "stale")
                    raise web.HTTPBadRequest(reason=hint)
                entry = self._record("stale", {"of": undone["op"], "reason": str(exc)}, None, stale=index)
                return web.json_response({"ok": True, **entry, "skipped": undone})
            if skip:
                raise _conflict(
                    "the newest change can be undone as it stands: undo it, "
                    "a skip is only for one that cannot be", "not_stale")
            entry = self._record("undo", {"of": op, **args}, previous, undoes=index)
        # `state` is what was set and Sushi confirmed, not read back again.
        return web.json_response({"ok": True, **entry, "undone": undone, "state": state})

    def _pick_undo(self) -> tuple[int, dict[str, Any]] | None:
        """`pick_undo` on the agent's log, a rig switch in the way answered as
        409 `nothing_to_undo` with the reason (skip included: a switch is not
        an entry that could not be undone, it is the end of what can be)."""
        try:
            return pick_undo(self.log, self._trimmed)
        except UndoBarrier as exc:
            raise _conflict(str(exc), "nothing_to_undo")

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
            body = {"ok": False, "status": exc.status, "reason": exc.reason}
            if getattr(exc, "agent_code", None):
                body["code"] = exc.agent_code
            return web.json_response(body, status=exc.status)
        except Exception as exc:  # noqa: BLE001 -- said, remembered, not swallowed
            reason = f"{type(exc).__name__}: {exc}"
            self._remember([request.method, request.path], reason)
            return web.json_response({"ok": False, "status": 500, "reason": reason}, status=500)

    # =================================================================== MIDI
    #
    # The MIDI listener's wiring (#34, #93). The decisions are midi_listener.py's;
    # this block only connects them to the routes' own machinery, and is kept
    # in one place so it merges past other work on this file. Outside it the
    # agent calls in at: __init__ (`_midi_init`), /health (`_midi_status`),
    # /rig (`_rig_answer`), `_run_lifecycle` (`_midi_stop`, `_midi_sync`) and
    # `serve` (`midi_adopt`, `midi_release`).
    #
    # Threading: the listener's thread reads the port and hands each message to
    # the loop (`call_soon_threadsafe`); everything below runs on the loop. A
    # generation number, bumped by every stop, tags each listener and each
    # action it caused, so a message that was on its way when the rig went
    # down is dropped rather than acted on against the rig that comes up next.

    def _midi_init(self, world: midi_listener.World | None) -> None:
        self._midi_world = world or midi_listener.World()
        self._listener: midi_listener.Listener | None = None
        self._midi_plan: midi_listener.Plan | None = None
        self._midi_generation = 0
        self._midi_switches: dict[str, bool] = {}  # each CC switch's last state, for `dispatch`
        # One MIDI action at a time, in the order they were played: two toggles
        # in quick succession must reach Sushi in that order, and a switch's
        # off-then-on must not arrive on-then-off. Not the live lock: that
        # serialises only the read-decide-set, and connections are made outside
        # it, so two actions could otherwise overtake each other while connecting.
        self._midi_lock = asyncio.Lock()
        self._midi_tasks: set = set()
        self._midi_released = False  # set by `midi_release`: nothing starts a listener after it
        self._midi: dict[str, Any] = {
            "state": "off", "reason": "the agent has not looked for a rig yet",
            "rig": None, "controller": None, "port": None, "bindings": [], "programs": False,
            "heard": 0, "last": None, "problem": None, "last_error": None,
        }

    def _midi_root(self) -> Path:
        from .paths import checkout_root

        return self.root or checkout_root()

    def _midi_status(self) -> dict[str, Any]:
        """The listener, for /health: `state` is off (with the `reason`),
        opening, listening, or failed (with the `problem`). `heard` counts every
        message the port delivered since it opened, aftertouch included, and
        `last` is the last one the listener reads and what it did with it: what
        to look at when a pedal seems to do nothing."""
        listener = self._listener
        return {**self._midi, "heard": listener.heard if listener is not None else self._midi["heard"]}

    async def _rig_answer(self) -> dict[str, Any]:
        """/rig: `describe_rig`, and when the running rig's map switches rigs by
        Program Change, which number is which rig (`programs`), read now, as a
        Program Change reads it (off the loop: it lists a folder)."""
        described = self.describe()
        plan = self._midi_plan
        if plan is not None and plan.bindings.programs is not None:
            rigs = await asyncio.to_thread(self._midi_world.rigs, self._midi_root())
            described["programs"] = midi_listener.programs_table(rigs)
        return described

    def _midi_say(self, message: str) -> None:
        print(f"sushi-rig agent: MIDI: {message}", file=sys.stderr, flush=True)

    def _midi_off(self, reason: str) -> None:
        self._midi.update(state="off", reason=reason, rig=None, controller=None, port=None,
                          bindings=[], programs=False, problem=None)

    def _midi_fail(self, problem: str) -> None:
        self._midi.update(state="failed", reason=None, problem=problem)
        self._midi_say(problem)

    async def midi_adopt(self, app=None) -> None:
        """At the agent's start: listen for a rig already up, which `up` from
        the CLI leaves running whether or not an agent was there to see it."""
        await self._midi_sync()

    async def midi_release(self, app=None) -> None:
        """At the agent's end: cancel the actions in flight and wait for them,
        then stop the listener. In that order, and with `_midi_released` set
        first, because a switch cancelled inside `_run_lifecycle` runs its
        finally, whose `_midi_sync` would otherwise start a fresh listener
        after this one had stopped the old."""
        self._midi_released = True
        for task in list(self._midi_tasks):
            task.cancel()
        await asyncio.gather(*list(self._midi_tasks), return_exceptions=True)
        await self._midi_stop("the agent is stopping")

    async def _midi_sync(self) -> None:
        """Make the listener match the rig as it now is: none for no rig, or a
        rig with nothing for the agent; the one its files describe otherwise.
        A listener already serving that same plan is left alone. Never raises:
        whatever goes wrong is the listener's problem, in /health, and not the
        lifecycle command's. Does nothing once the agent has released MIDI."""
        if self._midi_released:
            return
        try:
            plan = await asyncio.to_thread(self._midi_world.plan, self.describe(), self._midi_root())
        except Exception as exc:  # noqa: BLE001 -- the reason goes to /health and stderr
            await self._midi_stop("the rig's files could not say what to serve")
            detail = str(exc) if isinstance(exc, MidiError) else f"{type(exc).__name__}: {exc}"
            self._midi_fail(f"MIDI is off for the running rig: {detail}")
            return
        if isinstance(plan, midi_listener.Off):
            await self._midi_stop(plan.reason)
            self._midi_off(plan.reason)
            return
        if plan == self._midi_plan and self._midi["state"] in ("opening", "listening"):
            return
        await self._midi_stop("replaced")
        if not self._midi_released:  # released while the plan was being read
            self._midi_start(plan)

    def _midi_start(self, plan: midi_listener.Plan) -> None:
        loop = asyncio.get_running_loop()
        generation = self._midi_generation
        self._midi_plan = plan
        self._midi_switches = {}
        self._midi.update(
            state="opening", reason=None, rig=plan.rig, controller=plan.controller, port=plan.port,
            bindings=[b.describe() for b in plan.bindings.controls],
            programs=plan.bindings.programs is not None,
            heard=0, last=None, problem=None, last_error=None,
        )
        self._listener = midi_listener.Listener(
            plan.port, self._midi_world.open_port,
            post=loop.call_soon_threadsafe,
            on_open=functools.partial(self._midi_opened, generation),
            on_message=functools.partial(self._midi_heard, generation),
            on_failure=functools.partial(self._midi_failed, generation),
        )
        self._listener.start()

    async def _midi_stop(self, reason: str) -> None:
        """Stop the listener, if there is one, and drop what it handed over
        that has not been acted on. Waits (off the loop) for its thread to let
        go of the port; a thread stuck in ALSA past that is said and left, as a
        daemon, rather than holding up a `down`."""
        self._midi_generation += 1
        self._midi_switches = {}
        listener, self._listener = self._listener, None
        if listener is None:
            return
        self._midi_plan = None
        self._midi["heard"] = listener.heard
        if not await asyncio.to_thread(listener.stop, 2 * midi_listener.POLL_SECONDS + 1.0):
            self._midi_say(f"the listener on {listener.port_name!r} did not stop; left to end with the agent")
        self._midi_off(reason)

    def _midi_opened(self, generation: int) -> None:
        if generation == self._midi_generation:
            self._midi.update(state="listening")

    def _midi_failed(self, generation: int, exc: BaseException, opened: bool) -> None:
        if generation != self._midi_generation:
            return
        plan = self._midi_plan
        doing = "stopped reading" if opened else "could not open"
        who = f"the port of controller {plan.controller!r}, which {plan.rig}'s MIDI map is written for" if plan else "the port"
        self._midi_fail(
            f"{doing} MIDI port {self._midi['port']!r}, {who}: {exc}. Bypass and rig switching "
            "from the pedals are off until the rig is next brought up or restarted"
        )

    def _midi_heard(self, generation: int, message: midi_listener.Message) -> None:
        """A message from the listener, on the loop: decided now, in the order
        played, and acted on in a task of its own, one at a time.

        A Program Change is decided in its task instead, because deciding it
        lists config/rigs/, which is file access and not for the loop. The
        switch state `dispatch` returns is not kept here: `_midi_act` commits
        it once the action has gone through."""
        plan = self._midi_plan
        if generation != self._midi_generation or plan is None:
            return
        try:
            if message.kind == "program":
                if plan.bindings.programs is None:
                    action = None
                else:
                    action = message  # decided in `_midi_act`, off the loop
            else:
                # A switch's crossing is recorded as it is decided, so the values
                # that follow on the same side (a pedal sends several) are not
                # crossings too; `_midi_forget` takes it back if the action is
                # dropped or fails, so the next value plays it again.
                action, self._midi_switches = midi_listener.dispatch(message, plan.bindings, self._midi_switches)
        except Exception as exc:  # noqa: BLE001 -- a loop callback's error would reach only the loop's log
            self._midi["last_error"] = {
                "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "message": f"{message.describe()}: {type(exc).__name__}: {exc}",
            }
            self._midi_say(self._midi["last_error"]["message"])
            return
        self._midi_last(message, action)
        if action is None:
            return
        if len(self._midi_tasks) >= MIDI_PENDING:
            # Sushi is not keeping up (a stuck call holds each action up to
            # LIVE_TIMEOUT): a pedal's presses queued behind it would all land
            # at once, long after they were played.
            self._midi_error(action, f"{len(self._midi_tasks)} MIDI actions are still waiting on Sushi, so this one was dropped")
            self._midi_forget(action)
            return
        task = asyncio.ensure_future(self._midi_act(generation, action))
        self._midi_tasks.add(task)
        task.add_done_callback(self._midi_tasks.discard)

    def _midi_last(self, message: midi_listener.Message, action: Any) -> None:
        if action is message:
            done = "deciding which rig"
        elif action is None:
            done = "nothing (not bound, a release, or no change)"
        else:
            done = action.describe()
        self._midi["last"] = {
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "message": message.describe(), "action": done,
        }

    def _midi_error(self, action: Any, message: str) -> None:
        """An action that could not be done: there is no client to answer, so
        it is said on stderr and kept in /health (`agent.midi.last_error`)."""
        text = f"{action.describe()}: {message}"
        self._midi["last_error"] = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "message": text}
        self._midi_say(text)

    async def _midi_act(self, generation: int, action: Any) -> None:
        async with self._midi_lock:
            if generation != self._midi_generation:
                return  # the listener that heard it has been stopped since
            if isinstance(action, midi_listener.Message):  # a Program Change, to decide
                message, plan = action, self._midi_plan
                try:
                    rigs = await asyncio.to_thread(self._midi_world.rigs, self._midi_root())
                except Exception as exc:  # noqa: BLE001 -- said, the agent carries on
                    self._midi["last_error"] = {
                        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                        "message": f"{message.describe()}: reading config/rigs/ failed: {type(exc).__name__}: {exc}"}
                    self._midi_say(self._midi["last_error"]["message"])
                    return
                if generation != self._midi_generation or plan is None:
                    return
                action, _ = midi_listener.dispatch(message, plan.bindings, {}, rigs, plan.rig)
                if self._midi["last"] and self._midi["last"]["action"] == "deciding which rig":
                    self._midi_last(message, action)
                if action is None:
                    return
            # What `_sushi` and the live lock read off a request, for a press.
            origin = _MidiOrigin("MIDI", getattr(action, "control", None) or f"program {action.program}")
            try:
                if isinstance(action, midi_listener.Refused):
                    raise MidiError(action.reason)
                if isinstance(action, (midi_listener.ToggleBypass, midi_listener.SetBypass)):
                    await self._midi_bypass(origin, action)
                elif isinstance(action, midi_listener.ToggleMute):
                    await self._midi_mute(origin, action)
                elif isinstance(action, midi_listener.SelectRig):
                    await self._midi_switch(action)
            except web.HTTPException as exc:  # what a route would have answered
                self._midi_error(action, exc.reason)
                self._midi_forget(action, generation)
            except (AgentError, MidiError) as exc:
                self._midi_error(action, str(exc))
                self._midi_forget(action, generation)
            except Exception as exc:  # noqa: BLE001 -- said, with its type; the agent carries on
                self._midi_error(action, f"{type(exc).__name__}: {exc}")
                self._midi_forget(action, generation)

    def _midi_forget(self, action: Any, generation: int | None = None) -> None:
        """Take back the crossing a CC switch's dropped or failed action
        recorded, so the next value on that side plays it again. Only while
        the switch still holds this action's state (a later crossing stands)
        and the listener that heard it is still the one listening."""
        if not isinstance(action, midi_listener.SetBypass):
            return
        if generation is not None and generation != self._midi_generation:
            return
        if self._midi_switches.get(action.control) == (not action.bypassed):
            self._midi_switches = {k: v for k, v in self._midi_switches.items() if k != action.control}

    def _midi_logged(self, entry: dict[str, Any], control: str | None) -> None:
        """Mark a live-log entry as MIDI's. `_record` returns the entry it
        appended, so this marks the one in the log: no await comes between."""
        entry["source"] = "midi"
        if control is not None:
            entry["control"] = control

    async def _midi_bypass(self, origin: "_MidiOrigin", action: Any) -> None:
        """`POST /live/bypass`'s path: one connection, the live lock, the same
        read and the same check, then the set. A toggle sets the opposite of
        what Sushi reports now; a switch sets its own state, and does nothing
        when the plugin is already there (which is not logged, as a move
        that changes nothing is not)."""
        processor = action.processor
        want = action.bypassed if isinstance(action, midi_listener.SetBypass) else None
        async with self._connected_for_a_change(origin) as controller:
            reading = await self._sushi(origin, self.live.bypass, controller, processor, reading=True)
            previous = check_bypass(reading, processor)
            bypassed = midi_listener.next_bypass(previous, want)
            if bypassed == previous:
                return
            await self._sushi(origin, self.live.set_bypass, controller, processor, bypassed, reading=False)
            self._midi_logged(
                self._record("bypass", {"processor": processor, "bypassed": bypassed}, previous), action.control)

    async def _midi_mute(self, origin: "_MidiOrigin", action: Any) -> None:
        """`POST /live/parameter`'s path, on the track's `mute`."""
        track = action.track
        async with self._connected_for_a_change(origin) as controller:
            reading = await self._sushi(origin, self.live.parameter, controller, track, "mute", reading=True)
            previous = check_parameter(reading, track, "mute")
            value = midi_listener.next_mute(previous)
            await self._sushi(origin, self.live.set_parameter, controller, track, "mute", value, reading=False)
            self._midi_logged(
                self._record("parameter", {"processor": track, "parameter": "mute", "value": value}, previous),
                action.control)

    async def _midi_switch(self, action: Any) -> None:
        """Bring up rig `action.rig` with the lifecycle a request runs, with
        the options `restart` would bring the running rig back with: checked
        to compile first, and with the previous rig brought back if its `up`
        fails."""
        state = await asyncio.to_thread(self._midi_world.read_state) or {}
        steps = switch_steps(state, action.rig)
        previous = self._midi_plan.rig if self._midi_plan is not None else None
        # A rig that cannot compile is refused before the running one is taken
        # down: otherwise the player is left with no rig and no pedals.
        self._refuse_while_busy()
        problem = await asyncio.to_thread(
            self._midi_world.check_rig, action.rig, self._midi_root(), state.get("machine"))
        if problem:
            reason = f"not switched to {action.rig}, which would not come up: {problem}"
            self._remember(["switch", action.rig], reason, kind="lifecycle")
            raise MidiError(reason)
        # And if its `up` fails anyway (what only `up` finds: the live graph,
        # Sushi loading it), the rig that was playing is brought back.
        fallback = switch_steps(state, previous)[1] if previous else None
        outcome, args = await self._run_lifecycle("switch", steps, fallback=fallback)
        if not outcome.ok:
            raise MidiError(f"'{' '.join(args)}' failed: {outcome.reason}")
        self._midi_logged(self._record("rig", {"rig": action.rig, "program": action.program}, previous), None)


# A pending MIDI action past this many is dropped (and said), not queued.
MIDI_PENDING = 16


@dataclass(frozen=True)
class _MidiOrigin:
    """Where a MIDI action came from, in the two fields `Agent._sushi` and the
    live lock read off a request for their messages: "MIDI" and the control."""

    method: str
    path: str


def switch_steps(state: dict[str, Any], rig: str) -> list[list[str]]:
    """The lifecycle commands that switch the running rig (`state`, the state
    file) to rig `rig` of config/rigs: `down`, then `up --rig` with the
    headless, tuner and amp choices `restart` would bring back
    (`rig.restart_arguments`), so a switch keeps how the rig is being run.
    Not the amp model override: that was chosen for the rig being left, and
    the next one's own `_amp` says its model. `AgentError` for a rig name that
    is not one. Pure."""
    _, options = restart_arguments(state)
    up = lifecycle_arguments("up", {
        "config_name": rig, "rig": True,
        "headless": options["headless"], "tuner": options["tuner"], "amp": options["amp"],
    })
    return [["down"], up]


def serve(host: str, port: int, root: Path | None = None) -> int:
    """Run the agent until interrupted."""
    if web is None:
        raise AgentError("aiohttp is not installed; pip install -e tool[agent]")

    agent = Agent(root=root)
    app = agent.app()
    app.on_startup.append(agent.midi_adopt)  # MIDI: a rig already up has its pedals served
    app.on_shutdown.append(agent.announce_stopping)
    app.on_cleanup.append(agent.midi_release)  # MIDI: before the pool its actions use
    app.on_cleanup.append(agent.release_workers)
    print(f"sushi-rig agent {VERSION} on http://{host}:{port}  (Ctrl+C to stop)", flush=True)
    web.run_app(app, host=host, port=port, print=None, handle_signals=True)
    return 0
