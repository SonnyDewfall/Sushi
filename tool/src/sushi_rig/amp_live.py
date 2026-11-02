"""The amp as the agent holds it while a rig plays: what was last sent to
Carla, and the decisions about changing it (GET /amp, POST /amp/parameter,
POST /amp/model). Pure, but for `Carla`, the one effect, which is injectable.

## What the agent knows of the amp, and what it cannot

Carla's OSC is write-only from here (amp.py's docstring), so the agent's amp
is **what it last sent**, never what Carla holds, and every answer says so
(`known: "sent"`). It is seeded, for each run of the rig, from what `up`
replays: the running config's `_amp` parameters (`seed`), and the model the
state file says ran. A parameter the config does not state was never sent,
and is said as unknown (`value: null`) rather than as a default: the plugin's
own default is the plugin's to know. A seed is a claim about `up`'s replay,
which can fail ("amp parameters not restored", in `up`'s warnings); each
value says where it came from (`source`: "replayed" or "sent"), so a page can
tell a value the agent sent from one it assumes `up` did.

## When the panel runs, the panel owns the amp

In panel mode the Open Stage Control panel's faders reach Carla through the
listener (listen.py `_on_amp`), which remembers what it sent for SAVE and
the record bar. Two writers that cannot see each other would each hold a
record the other made wrong, so with the panel running the agent does not
write at all: GET /amp says `known: "panel"`, its values only what `up`
replayed, and every write is refused, 409 `panel`. Chosen over the listener
forwarding its amp messages to the agent because that is a second channel
from the listener to the agent for a mode the web UI never starts (it starts
rigs headless), and the listener would still keep its own record for SAVE:
two records again, one of them a copy. A rig switch already refuses to be
made live in panel mode for the same reason (agent `state_blockers`).

## How the agent knows Carla answered

OSC is UDP here, and a UDP send to a port nobody holds succeeds silently. So
the agent opens a TCP connection to Carla's OSC port, which Carla also serves
over TCP (`rig._replay_amp` waits on the same thing): no connection is "Carla
is not answering", 502, and nothing is recorded. That says a Carla is
listening; it does not say the value took. Nothing can, from here, short of
Carla's feedback channel for registered clients, which is a much larger thing
(amp.py).

Not on every send, though, which at fader rates would be a TCP connection to
Carla's liblo server per fader step (`needs_probe`): on the first send to a
Carla run (its pid, from the state file), after any failure, and otherwise
at most every `PROBE_EVERY` seconds. Each send also checks Carla's process is
alive (`amp_problem`, from the pid), so a Carla that died is caught at once;
what a quiet interval can miss is a Carla alive whose OSC stopped answering,
for at most `PROBE_EVERY`. The sends of one run share one UDP client
(`Carla.at`), dropped when the run changes or a send fails. None of this has
been run against a Carla.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .rig import runs_panel
from .amp import (
    CARLA_OSC_PORT, PANEL_PARAMETERS, PARAMETER_INDEX, PARAMETER_RANGE, PARAMETER_UNIT,
    RIG_SAMPLE_RATE, AmpError, clamp, describe_model, model_library, parameter_message, read_amp,
    resolve_model, summarise_model,
)

# What GET /amp says of where its values come from.
KNOWN_SENT = "sent"    # what the agent last sent Carla: not what Carla holds
KNOWN_PANEL = "panel"  # the panel's listener writes to Carla, and the agent cannot see what

# Where a value came from.
REPLAYED = "replayed"  # the config's `_amp`, which `up` replays when Carla starts
SENT = "sent"          # sent by this agent since

KNOWN_NOTE = {
    KNOWN_SENT: "what this agent last sent Carla, not what Carla holds: Carla cannot be asked",
    KNOWN_PANEL: "the panel is running and writes to Carla itself, so these are only what the rig "
                 "started with; the panel's faders may have moved them since",
}

# The ops an amp change is logged as. `amp` is a parameter, `amp_model` the model.
OPS = ("amp", "amp_model")

# How long a TCP probe of Carla's OSC port is taken to hold for the sends
# after it: long enough that a fader's stream of sends costs one probe, short
# enough that a Carla whose OSC went quiet is noticed within a gesture.
# A choice, not a measurement.
PROBE_EVERY = 2.0


def needs_probe(last: tuple | None, run: Any, now: float, every: float = PROBE_EVERY) -> bool:
    """Whether to ask Carla's OSC port before this send. `last` is `(run,
    when)` of the last probe that answered, None after a failure or before
    the first. A new Carla run (its pid) is always asked. Pure."""
    return last is None or last[0] != run or now - last[1] >= every


class AmpRefused(Exception):
    """A request about the amp the agent will not act on: `status` is the
    HTTP answer and `code` the word a client tells refusals apart by."""

    def __init__(self, reason: str, status: int = 409, code: str | None = None):
        super().__init__(reason)
        self.status = status
        self.code = code


def run_of(state: dict[str, Any] | None) -> tuple:
    """Which run of the rig an amp state belongs to: the group, the start
    and the config. A restart is a new start and a live switch a new config,
    and each replays a config's `_amp`, so either is a new seed; a model
    change (POST /amp/model) is neither, and keeps the values sent."""
    state = state or {}
    return (state.get("pgid"), state.get("started_at"), state.get("config"))


@dataclass
class AmpState:
    """What the agent last sent Carla in one run of the rig. `values` holds a
    parameter only once it is known to have been sent (by `up`'s replay, as
    far as the agent can tell, or by the agent), and `sources` says which."""

    run: tuple
    model: str | None
    values: dict[str, float] = field(default_factory=dict)
    sources: dict[str, str] = field(default_factory=dict)

    def sent(self) -> dict[str, float | None]:
        """Every parameter, None where nothing was sent: what `rigfile.diff`
        compares the next rig's `_amp` with."""
        return {name: self.values.get(name) for name in PARAMETER_INDEX}


def seed(state: dict[str, Any] | None, config: dict[str, Any] | None) -> AmpState:
    """The amp as `up` left it: the model the state file says ran, and the
    config's `_amp` parameters as `rig._replay_amp` sends them (each through
    `parameter_message`, so clamped; one it refuses, it does not send, and it
    is not here). Pure."""
    state = state or {}
    model = state.get("amp_model") or None
    described = read_amp(config or {}) or {}
    values: dict[str, float] = {}
    for name, value in (described.get("parameters") or {}).items() if isinstance(described.get("parameters"), dict) else ():
        try:
            if isinstance(value, bool):
                raise AmpError(f"{name} is not a number")
            _, payload = parameter_message(name, value)
        except (AmpError, TypeError, ValueError):
            continue
        values[name] = payload[1]
    return AmpState(run_of(state), model, values, {name: REPLAYED for name in values})


def amp_problem(state: dict[str, Any] | None, described: dict[str, Any], *, writing: bool) -> AmpRefused | None:
    """Why the running rig's amp cannot be read (or with `writing`, changed)
    now, or None. Pure over the state file and `describe_rig`'s answer.

    - no rig up: 409 `no_rig`;
    - no amp child: 409 `no_amp` (started with --no-amp, a config with no
      `_amp`, or a Carla that did not start, which `up` warned of);
    - writing in panel mode: 409 `panel` (see the module docstring), by
      `rig.runs_panel`, so a state file that does not say is the panel's;
    - writing to an amp child that is dead: 502 `carla_down`, since Carla is
      the one that would have to answer. A read still answers, saying so
      (GET /amp's `carla`).
    """
    if not described.get("running"):
        return AmpRefused(
            f"no rig is running, so there is no amp ({described.get('message') or 'nothing is up'})", 409, "no_rig")
    state = state or {}
    children = described.get("children") or {}
    if "amp" not in children:
        if state.get("amp_requested") is False:
            why = "it was started with --no-amp"
        elif not state.get("amp_model"):
            why = "its config has no _amp"
        else:
            why = "Carla did not start with it (see the warnings of the up that started it, and amp.log)"
        return AmpRefused(f"the running rig has no amp: {why}", 409, "no_amp")
    if writing and runs_panel(state):
        return AmpRefused(
            "the panel is running and writes to the amp itself (its faders go to Carla through the "
            "listener), and two writers that cannot see each other would each be wrong about what the "
            "amp is at: change it on the panel, or run the rig headless", 409, "panel")
    if writing and children["amp"].get("alive") is not True:
        return AmpRefused(
            f"Carla (pid {children['amp'].get('pid')}) is not running, so the amp cannot be set: "
            "POST /rig/restart brings it back", 502, "carla_down")
    return None


def parameter_request(body: Any) -> tuple[str, float, float]:
    """`(name, asked, sent)` from a POST /amp/parameter body, or `AmpRefused`
    (400). `sent` is `asked` clamped to the parameter's range, which is what
    goes to Carla and is remembered; a client sees both. Not a number, NaN,
    infinity or a bool is refused, not clamped: `amp.clamp` says why."""
    if not isinstance(body, dict):
        raise AmpRefused("the request body must be a JSON object: {\"name\", \"value\"}", 400)
    name = body.get("name")
    if not isinstance(name, str) or name not in PARAMETER_INDEX:
        raise AmpRefused(f"'name' must be one of the amp's parameters {list(PARAMETER_INDEX)}, not {name!r}", 400)
    if "value" not in body:
        raise AmpRefused(f"'value' is required: a number, {_range_said(name)}", 400)
    value = body["value"]
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise AmpRefused(f"'value' must be a number, {_range_said(name)}, not {value!r}", 400)
    return name, float(value), clamp(name, value)


def _range_said(name: str) -> str:
    low, high = PARAMETER_RANGE[name]
    unit = PARAMETER_UNIT.get(name) or ""
    return f"{low:g} to {high:g}{(' ' + unit) if unit else ''}"


def clamped_said(name: str, asked: float, sent: float) -> str | None:
    """What to say of a value that was clamped, or None if it was not."""
    if asked == sent:
        return None
    return f"{name} {asked:g} is outside {_range_said(name)}, so {sent:g} was sent"


# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #

def rate_problem(described: dict[str, Any], rate: int = RIG_SAMPLE_RATE) -> str | None:
    """Why a model must not be played, as `doctor` faults it (`check_models`):
    trained at another rate than the rig runs at. A model that declares no
    rate is not refused, as doctor does not fault it: it is warned of
    (`rate_warning`)."""
    declared = described.get("sample_rate")
    if declared is not None and declared != rate:
        return (f"{described.get('name')} was trained at {declared} Hz and the rig runs at {rate} Hz: NAM "
                "does no resampling, so it would play at the wrong pitch and speed")
    return None


def rate_warning(described: dict[str, Any]) -> str | None:
    if described.get("sample_rate") is None:
        return f"{described.get('name')} declares no sample rate, so a mismatch cannot be ruled out"
    return None


@dataclass
class ModelShelf:
    """The models' descriptions, read once per file version: a `.nam` is
    JSON holding every weight, megabytes for an A2 model, and the page asks
    for the list whenever its picker is drawn. Keyed by path, size and
    modification time, so a model replaced under the same name is read again."""

    describe: Callable[[Path], dict[str, Any]] = describe_model
    cache: dict = field(default_factory=dict)

    def read(self, path: Path) -> dict[str, Any]:
        """`describe_model`'s answer, or `{"error": reason}`."""
        try:
            stat = path.stat()
            key = (str(path), stat.st_size, stat.st_mtime_ns)
        except OSError as exc:
            return {"error": f"cannot read {path}: {exc}"}
        if key not in self.cache:
            try:
                self.cache[key] = self.describe(path)
            except AmpError as exc:
                self.cache[key] = {"error": str(exc)}
            except Exception as exc:  # noqa: BLE001 -- one bad model costs one entry, as in doctor
                self.cache[key] = {"error": f"{path.name} could not be read: {type(exc).__name__}: {exc}"}
        return self.cache[key]


def library(root: Path, shelf: ModelShelf, current: str | None = None) -> list[dict[str, Any]]:
    """Every model in amp/models, for the picker: by file name (what POST
    /amp/model takes), what it declares, whether it can be played (`usable`,
    and `why` not), and whether it is the one playing. Sorted by name."""
    folder = model_library(root)
    playing = None
    if current:
        try:
            playing = resolve_model(current, root).resolve()
        except AmpError:
            playing = None
    entries = []
    for path in sorted(folder.glob("*.nam")) if folder.is_dir() else []:
        described = shelf.read(path)
        entry: dict[str, Any] = {"model": path.name, "playing": playing is not None and path.resolve() == playing}
        if "error" in described:
            entry.update(usable=False, why=described["error"], name=path.stem, sample_rate=None)
        else:
            refused = rate_problem(described)
            entry.update(
                name=described["name"], sample_rate=described["sample_rate"], usable=refused is None,
                why=refused, warning=rate_warning(described), architecture=described["architecture"],
                tiers=len(described["tiers"]), gear=" / ".join(
                    x for x in (described.get("gear_make"), described.get("gear_type")) if x) or None,
            )
        entries.append(entry)
    return entries


def model_request(
    body: Any, *, local: bool, root: Path, name_pattern: re.Pattern,
) -> tuple[str, Path]:
    """`(override, path)` for a POST /amp/model body, or `AmpRefused` (400).

    The same rule as `up`'s amp_model (the agent's `MODEL_NAME`, passed in as
    `name_pattern`): from anywhere but this machine a model is the name of a
    file in amp/models, never a path, so a client on the LAN cannot have the
    agent read whatever file it likes and hand it to Carla; from this machine
    an absolute path is taken too, as the CLI's --amp-model always has.
    `override` is how the state file records it, so `restart` brings it back:
    the name for one in amp/models (which `prepare` looks up there), the path
    otherwise."""
    if not isinstance(body, dict) or "model" not in body:
        raise AmpRefused("'model' is required: the name of a file in amp/models", 400)
    model = body["model"]
    if not isinstance(model, str) or not model:
        raise AmpRefused(f"'model' must be the name of a file in amp/models, not {model!r}", 400)
    if "/" in model:
        if not local:
            raise AmpRefused(f"invalid amp model {model!r}: the name of a file in amp/models, not a path "
                             "(a path is taken only from this machine)", 400)
        if not model.startswith("/") or any(c in model for c in "\n\r\0"):
            raise AmpRefused(f"invalid amp model path {model!r}: an absolute path, on one line", 400)
        path = Path(model)
        if not path.is_file():
            raise AmpRefused(f"no amp model at {model}", 400)
        return model, path
    if not name_pattern.fullmatch(model) or ".." in model:
        raise AmpRefused(f"invalid amp model {model!r}: the name of a file in amp/models", 400)
    folder = model_library(root)
    path = folder / model
    if not path.is_file():
        there = sorted(p.name for p in folder.glob("*.nam")) if folder.is_dir() else []
        raise AmpRefused(f"there is no {model!r} in amp/models: it has {there}", 400)
    return model, path


def stored_model(override: str) -> str:
    """The model as `amp.prepare` stores what ran (`amp_model`): a bare name
    is amp/models/<name>, a path is itself."""
    return override if "/" in override else f"amp/models/{override}"


def override_of(stored: str, root: Path) -> str:
    """`stored_model` backwards, for putting a model back (an undo): a model
    in amp/models by its name, anything else by its absolute path, since
    `--amp-model` takes no path relative to the checkout."""
    path = Path(stored)
    if not path.is_absolute() and path.parent == Path("amp/models"):
        return path.name
    return str(path if path.is_absolute() else Path(root) / path)


def same_model(a: str | None, b: str | None, root: Path) -> bool:
    """Whether two stored models are one file, however each was written."""
    if not a or not b:
        return False
    try:
        return resolve_model(a, root).resolve() == resolve_model(b, root).resolve()
    except AmpError:
        return a == b


# --------------------------------------------------------------------------- #
# GET /amp
# --------------------------------------------------------------------------- #

def model_view(model: str | None, root: Path, shelf: ModelShelf) -> dict[str, Any] | None:
    """The model a GET /amp names: as stored, its path, its name and what it
    declares (`summarise_model`'s text as `description`), or why it cannot
    be read (`problem`)."""
    if not model:
        return None
    view: dict[str, Any] = {"stored": model, "name": Path(model).stem, "path": None, "description": None}
    try:
        path = resolve_model(model, root)
    except AmpError as exc:
        return {**view, "problem": str(exc).split("\n", 1)[0]}
    described = shelf.read(path)
    view["path"] = str(path)
    if "error" in described:
        return {**view, "problem": described["error"]}
    return {
        **view, "name": described["name"], "description": summarise_model(described),
        "sample_rate": described["sample_rate"], "tiers": len(described["tiers"]),
        "problem": rate_problem(described), "warning": rate_warning(described),
    }


def parameters_view(amp: AmpState) -> list[dict[str, Any]]:
    """Each parameter in Carla's order: its value (None where nothing was
    sent), range, unit, whether the panel puts it on a fader (`panel`, not
    Quality: amp.PANEL_PARAMETERS says why), and where the value came from."""
    out = []
    for name in PARAMETER_INDEX:
        low, high = PARAMETER_RANGE[name]
        out.append({
            "name": name, "value": amp.values.get(name), "min": low, "max": high,
            "unit": PARAMETER_UNIT.get(name) or None, "panel": name in PANEL_PARAMETERS,
            "source": amp.sources.get(name),
        })
    return out


def amp_view(amp: AmpState, state: dict[str, Any], model: dict[str, Any] | None) -> dict[str, Any]:
    """GET /amp's answer for a rig with an amp; `model` is `model_view`'s,
    which the agent keeps per run and model rather than reading the file's
    status and summary again on every page's poll. Pure."""
    known = KNOWN_PANEL if runs_panel(state) else KNOWN_SENT
    return {
        "ok": True, "known": known, "known_note": KNOWN_NOTE[known],
        "model": model,
        "parameters": parameters_view(amp),
        "override": state.get("amp_model_requested") or None,
    }


# --------------------------------------------------------------------------- #
# Undo
# --------------------------------------------------------------------------- #

# A parameter is "the same" within this when undo checks it is still what
# the change left. Both are numbers the agent itself sent and remembered,
# clamped the same way, so this only absorbs float noise.
TOLERANCE = 1e-9


def undo_check(entry: dict[str, Any], amp: AmpState, root: Path) -> None:
    """`AmpRefused` (409 `stale`) unless the amp is still as `entry` left it,
    by what the agent last sent: a later change, a rig switch's step or a
    restart's replay is caught; a fader on the panel is not, since it is not
    seen (and in panel mode nothing is written). `previous` None is a value
    never sent: there is nothing to put back."""
    args = entry["args"]
    if entry["op"] == "amp":
        if entry.get("previous") is None:
            raise AmpRefused(
                f"cannot undo the amp's {args['name']}: nothing had been sent for it before, so what it "
                "was is not known (the plugin's own default)", 409, "stale")
        now = amp.values.get(args["name"])
        if now is None or abs(now - args["value"]) > TOLERANCE:
            raise AmpRefused(
                f"cannot undo the amp's {args['name']}: it is no longer as the change left it (it was "
                f"last sent {now!r}, the change sent {args['value']!r})", 409, "stale")
        return
    if not same_model(amp.model, args["model"], root):
        raise AmpRefused(
            f"cannot undo the model change: the amp plays {amp.model!r} now, not {args['model']!r}",
            409, "stale")
    if not entry.get("previous"):
        raise AmpRefused("cannot undo the model change: the model before it is not known", 409, "stale")


# --------------------------------------------------------------------------- #
# Carla, the one effect
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class Carla:
    """Carla's OSC, as the agent reaches it: `answering` (a TCP connection to
    its OSC port, see the module docstring), `send` (one UDP message,
    `parameter_message`'s) and `forget` (drop the UDP client, for the next
    Carla run or after a failure). They block; the agent runs them off its
    loop."""

    answering: Callable[[], bool]
    send: Callable[[str, list], None]
    forget: Callable[[], None] = lambda: None

    @classmethod
    def at(cls, host: str = "127.0.0.1", port: int = CARLA_OSC_PORT) -> "Carla":
        from .procs import port_is_open

        held: dict[str, Any] = {}  # the one UDP client, until forgotten

        def send(address: str, payload: list) -> None:
            if "client" not in held:
                try:
                    from pythonosc.udp_client import SimpleUDPClient
                except ImportError as exc:
                    raise AmpError("python-osc is not installed, so the agent cannot reach Carla: "
                                   "pip install -e 'tool[agent]' (python-osc is in the agent extra)") from exc
                held["client"] = SimpleUDPClient(host, port)
            held["client"].send_message(address, payload)

        def forget() -> None:
            client = held.pop("client", None)
            if client is not None:
                try:
                    client._sock.close()
                except (AttributeError, OSError):
                    pass

        return cls(answering=lambda: port_is_open(host, port), send=send, forget=forget)
