"""A rig as channels you choose the number of, and a master they mix into.

Issue #30, the channel-and-master layer. Today a channel is spelt out by hand
as a Sushi track, its routing, and the engine bus it uses (`config/src/*.yaml`,
read by `spec.RigSpec`). Here a channel is what a player chooses: a name, the
input it plays from, whether that input is mono or stereo, and a chain. Every
engine bus and channel number is allocated here, so choosing three channels in
the UI never means editing a bus number.

    kind: rig
    schema_version: 1
    name: guitar_vocal
    channels:
      - name: guitar
        input: guitar-in        # a role in the machine file
        width: mono             # what the input is; the chain is always stereo
        chain:
          - {name: guitar_compressor, uri: http://...}
    master:
      output: monitor-out       # a role in the machine file
      chain: []                 # where #29's limiter goes

A rig compiles to a `RigSpec`, so everything downstream — `emit`, `verify`, the
patchbay, the null tests — keeps working on what it already understands. This
module adds no second path to Sushi.

## What a compiled rig looks like

- **Each channel is a stereo track with an output of its own**, its stem, so a
  processed guitar can be recorded apart from the vocal. Nothing is linked to a
  stem unless something is recording it.
- **Each channel sends to the master**, a track carrying a return and then the
  master chain, whose output is the only thing that reaches the monitors.
  Decided 2026-10-31: the monitor mix is made in Sushi.
- **The master track is always last.** Measured (test_send_return.py): a send
  ahead of its return on one thread costs nothing; a master track listed first
  makes every channel 64 samples late. So this is the compiler's job, not a
  thing a person has to know.
- **The master is on engine bus 0**, the stems after it. Bus 0 is the one bus
  Sushi's offline frontend has, so what you hear is what can be rendered.
- **A mono input feeds both halves of its channel** from one engine channel.
  Today the patchbay duplicates the physical port into two engine channels to
  do the same; doing it inside Sushi spends one input instead of two.

## The ceiling

Sushi 1.3.0's JACK frontend has 8 input and 8 output channels
(`MAX_FRONTEND_CHANNELS`). Inputs are one per mono channel and two per stereo;
outputs are two for the master and two per channel. So three channels is the
most a rig can have, however its inputs are made up, and a rig over either
limit is refused with the arithmetic rather than left to Sushi's "Failed to
load tracks".

## Names

A channel's name becomes its track's, its stem's file name in a take, and its
send's prefix, so it follows the rule takes and configs already use
(`save.NAME_PATTERN`). `master`, `master_return` and `<channel>_send` belong to
the compiler; a name that would collide with one is refused by name.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .save import NAME_PATTERN
from .spec import PluginSpec, RigSpec, TrackSpec

KIND = "rig"
SCHEMA_VERSION = 1

# Sushi 1.3.0, src/audio_frontends/jack_frontend.h. The same in each direction.
MAX_FRONTEND_CHANNELS = 8

WIDTHS = {"mono": 1, "stereo": 2}

MASTER = "master"
RETURN = "master_return"
SEND_SUFFIX = "_send"
SEND_UID = "sushi.testing.send"
RETURN_UID = "sushi.testing.return"
MASTER_BUS = 0

_KEYS = ("kind", "schema_version", "name", "description", "host", "channels", "master")
_CHANNEL_KEYS = ("name", "input", "width", "chain")
_MASTER_KEYS = ("output", "chain")
_PLUGIN_KEYS = ("name", "uri", "path", "uid", "type")


class RigFileError(ValueError):
    """A rig file that cannot be used. Carries every problem, not the first."""

    def __init__(self, problems: list[str]):
        self.problems = problems
        super().__init__("\n".join(problems))


@dataclass(frozen=True)
class Channel:
    name: str
    input: str
    width: str
    chain: tuple[PluginSpec, ...] = ()

    @property
    def inputs_used(self) -> int:
        return WIDTHS[self.width]


@dataclass(frozen=True)
class Master:
    output: str
    chain: tuple[PluginSpec, ...] = ()


@dataclass(frozen=True)
class Rig:
    name: str
    channels: tuple[Channel, ...]
    master: Master
    description: str = ""
    host: dict[str, Any] | None = None


# --------------------------------------------------------------------------- #
# Reading
# --------------------------------------------------------------------------- #

def is_rig_file(raw: Any) -> bool:
    """Whether a parsed YAML document is one of these rather than a v1 rig.yaml."""
    return isinstance(raw, dict) and raw.get("kind") == KIND


def looks_like_rig_file(raw: Any) -> bool:
    """Whether a document was meant to be one of these, even if its `kind` is
    wrong: read as a v1 rig.yaml it would be a rig with no tracks, and emitted
    as an empty config without a word."""
    return isinstance(raw, dict) and any(key in raw for key in ("kind", "channels", "master"))


def load_rig(path: Path) -> Rig:
    import yaml

    raw = yaml.safe_load(Path(path).read_text())
    return parse_rig(raw)


def _unknown(where: str, entry: dict, known: tuple[str, ...]) -> list[str]:
    # Refused rather than ignored: a misspelt `widht: stereo` silently giving a
    # mono channel is the kind of near miss that is heard, not read.
    return [
        f"{where}: unknown key {key!r} (expected one of {', '.join(known)})"
        for key in entry if key not in known
    ]


def _chain(where: str, raw: Any, problems: list[str]) -> tuple[PluginSpec, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        problems.append(f"{where}: chain must be a list of plugins (got {raw!r})")
        return ()
    plugins = []
    for index, entry in enumerate(raw):
        at = f"{where}: chain entry {index + 1}"
        if not isinstance(entry, dict):
            problems.append(f"{at} is {entry!r}, not a plugin")
            continue
        problems += _unknown(at, entry, _PLUGIN_KEYS)
        if not isinstance(entry.get("name"), str):
            problems.append(f"{at} has no name")
            continue
        plugins.append(PluginSpec(**{k: v for k, v in entry.items() if k in _PLUGIN_KEYS}))
    return tuple(plugins)


def parse_rig(raw: Any) -> Rig:
    """A `Rig` from a parsed YAML document, or `RigFileError` naming every problem."""
    if not isinstance(raw, dict):
        raise RigFileError([f"a rig file is a mapping, not {type(raw).__name__}"])
    problems = _unknown("rig", raw, _KEYS)
    if raw.get("kind") != KIND:
        problems.append(f"kind must be {KIND!r} (got {raw.get('kind')!r})")
    if raw.get("schema_version") != SCHEMA_VERSION:
        # Refused, never guessed at, for machine.py's reason: a field that means
        # something different in another version is a rig routed wrongly.
        problems.append(
            f"schema_version {raw.get('schema_version')!r} is not one this tool reads "
            f"(it reads {SCHEMA_VERSION})"
        )
    name = raw.get("name")
    if not isinstance(name, str) or not NAME_PATTERN.fullmatch(name):
        problems.append(f"name {name!r} must match {NAME_PATTERN.pattern}")
    host = raw.get("host")
    if host is not None and not isinstance(host, dict):
        problems.append(f"host must be a mapping (got {host!r})")

    channels = []
    entries = raw.get("channels")
    if not isinstance(entries, list) or not entries:
        problems.append("channels must be a list of at least one channel")
        entries = []
    for index, entry in enumerate(entries):
        where = f"channel {index + 1}"
        if not isinstance(entry, dict):
            problems.append(f"{where} is {entry!r}, not a channel")
            continue
        if isinstance(entry.get("name"), str):
            where = f"channel {entry['name']!r}"
        problems += _unknown(where, entry, _CHANNEL_KEYS)
        if not isinstance(entry.get("name"), str):
            problems.append(f"{where} has no name")
        if not isinstance(entry.get("input"), str) or not entry["input"].strip():
            problems.append(f"{where}: input must name a role in the machine file")
        if entry.get("width") not in WIDTHS:
            problems.append(
                f"{where}: width must be one of {', '.join(WIDTHS)} (got {entry.get('width')!r})"
            )
        chain = _chain(where, entry.get("chain"), problems)
        if isinstance(entry.get("name"), str) and entry.get("width") in WIDTHS:
            channels.append(Channel(entry["name"], str(entry.get("input")), entry["width"], chain))

    master_raw = raw.get("master")
    master = None
    if not isinstance(master_raw, dict):
        problems.append("master must say where the mix goes: master: {output: <role>}")
    else:
        problems += _unknown("master", master_raw, _MASTER_KEYS)
        if not isinstance(master_raw.get("output"), str) or not master_raw["output"].strip():
            problems.append("master: output must name a role in the machine file")
        master = Master(str(master_raw.get("output")), _chain("master", master_raw.get("chain"), problems))

    if problems:
        raise RigFileError(problems)
    rig = Rig(str(name), tuple(channels), master, str(raw.get("description") or ""), host)
    problems = check_rig(rig)
    if problems:
        raise RigFileError(problems)
    return rig


# --------------------------------------------------------------------------- #
# Decisions
# --------------------------------------------------------------------------- #

def check_rig(rig: Rig) -> list[str]:
    """Names and the channel ceiling. Every problem, with the arithmetic."""
    problems = []
    seen: set[str] = set()
    reserved = {MASTER, RETURN} | {c.name + SEND_SUFFIX for c in rig.channels}
    for channel in rig.channels:
        if channel.width not in WIDTHS:
            problems.append(
                f"channel {channel.name!r}: width must be one of {', '.join(WIDTHS)} "
                f"(got {channel.width!r})"
            )
        if not NAME_PATTERN.fullmatch(channel.name):
            problems.append(
                f"channel {channel.name!r}: a name must match {NAME_PATTERN.pattern} — it "
                "is the track's name and the stem's file name in a take"
            )
        if channel.name in reserved:
            problems.append(
                f"channel {channel.name!r}: a name the compiler gives the master track, "
                "its return or a channel's send; rename it"
            )
        if channel.name in seen:
            problems.append(f"channel {channel.name!r} appears twice")
        seen.add(channel.name)

    for owner, chain in [(f"channel {c.name!r}", c.chain) for c in rig.channels] + [
        ("master", rig.master.chain)
    ]:
        for plugin in chain:
            if plugin.name in reserved:
                problems.append(
                    f"{owner}: plugin {plugin.name!r} has a name the compiler gives its "
                    "own send, return or master track; rename it"
                )

    if any(c.width not in WIDTHS for c in rig.channels):
        return problems
    # Unreachable while every stem is stereo — three channels use at most six
    # inputs — and kept for when a mono source can have a mono stem.
    inputs = sum(c.inputs_used for c in rig.channels)
    if inputs > MAX_FRONTEND_CHANNELS:
        problems.append(
            f"the channels need {inputs} inputs ("
            + " + ".join(f"{c.name} {c.inputs_used}" for c in rig.channels)
            + f") and Sushi has {MAX_FRONTEND_CHANNELS}"
        )
    outputs = 2 * (1 + len(rig.channels))
    if outputs > MAX_FRONTEND_CHANNELS:
        problems.append(
            f"{len(rig.channels)} channels need {outputs} outputs — 2 for the master "
            f"and 2 for each channel's stem — and Sushi has {MAX_FRONTEND_CHANNELS}, "
            f"so a rig has at most {MAX_FRONTEND_CHANNELS // 2 - 1} channels"
        )
    return problems


def _inputs(first: int, width: str) -> list[dict[str, int]]:
    if width == "mono":
        # One engine channel into both halves: what the patchbay's
        # mono_to_stereo does today with two engine channels.
        return [
            {"engine_channel": first, "track_channel": 0},
            {"engine_channel": first, "track_channel": 1},
        ]
    return [
        {"engine_channel": first, "track_channel": 0},
        {"engine_channel": first + 1, "track_channel": 1},
    ]


def compile_rig(rig: Rig) -> tuple[RigSpec, dict[str, Any]]:
    """The `RigSpec` that runs `rig`, and the state its sends need.

    The state is what `emit` takes as captured state: each send's
    `destination_name`, without which a send goes nowhere and the channel is
    silent in the monitors while its stem looks healthy.
    """
    problems = check_rig(rig)
    if problems:
        raise RigFileError(problems)
    tracks = []
    processors: dict[str, Any] = {}
    next_input = 0
    for index, channel in enumerate(rig.channels):
        send = channel.name + SEND_SUFFIX
        tracks.append(TrackSpec(
            name=channel.name,
            channels=2,
            inputs=_inputs(next_input, channel.width),
            outputs=[{"engine_bus": MASTER_BUS + 1 + index, "track_bus": 0}],
            plugins=list(channel.chain) + [PluginSpec(name=send, uid=SEND_UID, type="internal")],
            source=channel.input,
        ))
        processors[send] = {"properties": {"destination_name": RETURN}}
        next_input += channel.inputs_used
    # Last, always: see the module docstring.
    tracks.append(TrackSpec(
        name=MASTER,
        channels=2,
        inputs=[],
        outputs=[{"engine_bus": MASTER_BUS, "track_bus": 0}],
        plugins=[PluginSpec(name=RETURN, uid=RETURN_UID, type="internal")] + list(rig.master.chain),
        sink=rig.master.output,
    ))
    spec = RigSpec(
        host=dict(rig.host or {"samplerate": 48000}),
        tracks=tracks,
        meta={"name": rig.name, **({"description": rig.description} if rig.description else {})},
    )
    problems = spec.validate()
    if problems:
        raise RigFileError(problems)
    return spec, {"processors": processors}


def merged_state(fixed: dict[str, Any], captured: dict[str, Any] | None) -> dict[str, Any]:
    """Captured state over the compiler's, except where the compiler's must hold.

    `live.capture` records parameters, programs and chain order, not
    properties, so a capture never carries a send's destination: without the
    compiler's, every re-emit from a capture would leave the sends pointing
    nowhere and the channels silent in the monitors while their stems looked
    healthy. A hand-written state file may carry one; the compiler's still
    wins, since a destination other than the master is a send to nothing.
    """
    captured = captured or {}
    merged = {key: value for key, value in captured.items() if key != "processors"}
    processors = {
        name: dict(values) for name, values in (captured.get("processors") or {}).items()
        if isinstance(values, dict)
    }
    for name, values in fixed["processors"].items():
        entry = processors.setdefault(name, {})
        entry["properties"] = {**(entry.get("properties") or {}), **values["properties"]}
    merged["processors"] = processors
    return merged


def pin_monitor_path(config: dict[str, Any]) -> dict[str, Any]:
    """Each send last in its chain and the return first in the master.

    The panel can move any processor and a capture saves the order it finds,
    and `emit` appends a plugin the capture does not mention at the end of its
    chain. Any of those can leave a send part way up a chain, which feeds the
    monitors from the middle of it: the player hears the guitar without its
    delay while the stem has it. Neither position is a tone decision anyone
    made, so the order around them is kept and they are not. Done on the
    emitted config, after every reordering, so nothing can come after it.
    """
    for track in config["tracks"]:
        plugins = track["plugins"]
        if track["name"] == MASTER:
            track["plugins"] = (
                [p for p in plugins if p["name"] == RETURN] + [p for p in plugins if p["name"] != RETURN]
            )
        else:
            send = track["name"] + SEND_SUFFIX
            track["plugins"] = (
                [p for p in plugins if p["name"] != send] + [p for p in plugins if p["name"] == send]
            )
    return config


def emit_rig(rig: Rig, captured: dict[str, Any] | None = None) -> dict[str, Any]:
    """The Sushi config for `rig`, with any captured state folded in."""
    from .emit import emit

    spec, fixed = compile_rig(rig)
    return pin_monitor_path(emit(spec, merged_state(fixed, captured)))
