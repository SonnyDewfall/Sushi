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

import copy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .save import NAME_PATTERN
from .spec import PluginSpec, RigSpec, TrackSpec, looks_like_rig_file  # noqa: F401 (re-exported)

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

_KEYS = ("kind", "schema_version", "name", "description", "meta", "host", "amp",
         "channels", "master")
_CHANNEL_KEYS = ("name", "input", "width", "strip", "chain")
_MASTER_KEYS = ("output", "chain")
_PLUGIN_KEYS = ("name", "uri", "path", "uid", "type")
# A component's settings, as `emit` writes them into `initial_state`.
_SETTING_KEYS = ("bypassed", "program", "parameters", "properties")


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
    # Each processor's settings by name — a channel's strip under the channel's
    # own name, as Sushi keys a track's parameters — in `initial_state` form.
    settings: dict[str, dict[str, Any]] = field(default_factory=dict)
    # The amp as `amp.read_amp` reads it from a config's `_amp`; and whatever
    # provenance a config's `_meta` carried beyond its name and description.
    amp: dict[str, Any] | None = None
    meta: dict[str, Any] = field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Reading
# --------------------------------------------------------------------------- #

def is_rig_file(raw: Any) -> bool:
    """Whether a parsed YAML document is one of these rather than a v1 rig.yaml."""
    return isinstance(raw, dict) and raw.get("kind") == KIND


def load_rig(path: Path) -> Rig:
    import yaml

    raw = yaml.safe_load(Path(path).read_text())
    return parse_rig(raw)


# The header a rig file written from nothing carries when it came from a v1 config.
_HEADER = (
    "# Converted from {source} by `sushi-rig convert`, and checked to compile\n"
    "# back to every plugin and setting it held. Edit this file, not that one.\n\n"
)


def write_rig(path: Path, rig: dict, *, source: str | None = None) -> None:
    """Write `rig` (the dict `parse_rig` reads) to `path`, keeping what a person wrote.

    A file already there is updated in place to hold `rig` — changed values set,
    new keys added, absent keys removed, chain entries matched by name — and
    written back with its comments, blank lines, key order and layout intact,
    so an unchanged rig is an unchanged file (yamlmerge.py has the rules, and
    what it will not do). A file not there is written from nothing, in block
    style with keys in `rig`'s order, under the header saying where it came
    from if `source` is given.

    Nothing is written, and `RigFileError` is raised, when `rig` is not one
    `parse_rig` accepts, when the file there is not YAML or not a mapping or
    holds a comment that cannot be kept, or when the text would not read back
    as `rig`. The file is replaced whole or not at all."""
    import os

    import yaml

    from . import yamlmerge

    path = Path(path)
    parse_rig(copy.deepcopy(rig))  # refuse what load_rig would refuse
    existing = path.read_text() if path.exists() else ""
    try:
        if existing.strip():
            text = yamlmerge.merged(existing, rig)
        else:
            text = yamlmerge.fresh(rig, _HEADER.format(source=source) if source else "")
    except yamlmerge.MergeError as exc:
        raise RigFileError([f"{path}: {exc}"]) from exc
    try:
        readable = yaml.safe_load(text) == rig
    except yaml.YAMLError:
        readable = False
    if not readable:
        raise RigFileError([f"{path}: the text written would not read back as the rig given; "
                            "nothing was written"])
    path.parent.mkdir(parents=True, exist_ok=True)
    scratch = path.with_name(path.name + ".tmp")
    scratch.write_text(text)
    os.replace(scratch, path)


def load_any(path: Path) -> "Rig | RigSpec":
    """Either kind of rig file, decided in one place: a `Rig` for one of these,
    a `RigSpec` for a rig.yaml of tracks. Raises `RigFileError` (a ValueError)
    for a rig file with problems, and yaml's own error for a file that is not
    YAML at all."""
    import yaml

    raw = yaml.safe_load(Path(path).read_text()) or {}
    if looks_like_rig_file(raw):
        return parse_rig(raw)
    return RigSpec.from_dict(raw, path)


def spec_of(path: Path) -> RigSpec:
    """The tracks either kind of rig file runs as: compiled from channels and a
    master, or read as written. For the tools that read routing — the patchbay
    and the probes. Anything that writes a config goes through `emit_rig`
    instead, because the sends need state this does not carry."""
    rig = load_any(path)
    return compile_rig(rig)[0] if isinstance(rig, Rig) else rig


def _unknown(where: str, entry: dict, known: tuple[str, ...]) -> list[str]:
    # Refused rather than ignored: a misspelt `widht: stereo` silently giving a
    # mono channel is the kind of near miss that is heard, not read.
    return [
        f"{where}: unknown key {key!r} (expected one of {', '.join(known)})"
        for key in entry if key not in known
    ]


def _is_width(value: Any) -> bool:
    """Whether `value` names a width. Not `value in WIDTHS` alone: a list or a
    mapping in a rig file is unhashable, and the membership test would raise
    where every other bad value is reported by name."""
    return isinstance(value, str) and value in WIDTHS


def _settings(at: str, entry: dict, problems: list[str]) -> dict[str, Any]:
    """A component's settings, each checked for the shape `emit` writes."""
    found: dict[str, Any] = {}
    for key in _SETTING_KEYS:
        if key not in entry:
            continue
        value = entry[key]
        ok = {
            "bypassed": isinstance(value, bool),
            "program": type(value) is int,
            "parameters": isinstance(value, dict) and all(
                isinstance(k, str) and type(v) in (int, float) for k, v in value.items()
            ),
            "properties": isinstance(value, dict) and all(
                isinstance(k, str) and isinstance(v, str) for k, v in value.items()
            ),
        }[key]
        if ok:
            found[key] = value
        else:
            problems.append(f"{at}: {key} {value!r} is not a {_SETTING_SHAPES[key]}")
    return found


_SETTING_SHAPES = {
    "bypassed": "true or false",
    "program": "program number",
    "parameters": "mapping of parameter names to numbers",
    "properties": "mapping of property names to strings",
}


# A track's gain, pan and mute at unity, normalised as Sushi stores them: 0 dB
# on its -120..24 dB range, pan centred on -1..1, unmuted.
UNITY_STRIP = {"gain": 120 / 144, "pan": 0.5, "mute": 0.0}


def _strip_problems(where: str, strip: dict[str, Any]) -> list[str]:
    """A strip anywhere but unity changes the stem and not the monitors.

    Sushi 1.3.0 applies a track's gain, pan and mute after its plugins
    (src/engine/track.cpp, `Track::process_audio`), and the send that feeds
    the master is the last plugin, so it copies the signal before them. Until
    the mixer strip (#18) decides where a channel's level lives, a strip that
    would make the two disagree is refused rather than written.
    """
    off = [
        f"{key} {value}" for key, value in strip.items()
        if key in UNITY_STRIP and abs(value - UNITY_STRIP[key]) > 1e-5
    ]
    if not off:
        return []
    return [
        f"{where}: strip {', '.join(off)} is not unity, and a channel's strip acts after "
        "its send — it would change the stem and not what the monitors hear. Leave it "
        "at gain 0.833333, pan 0.5, mute 0 until the mixer strip (#18) is designed"
    ]


def _chain(where: str, raw: Any, problems: list[str],
           settings: dict[str, dict[str, Any]]) -> tuple[PluginSpec, ...]:
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
        problems += _unknown(at, entry, _PLUGIN_KEYS + _SETTING_KEYS)
        if not isinstance(entry.get("name"), str):
            problems.append(f"{at} has no name")
            continue
        plugins.append(PluginSpec(**{k: v for k, v in entry.items() if k in _PLUGIN_KEYS}))
        found = _settings(f"{where}: {entry['name']}", entry, problems)
        if found:
            settings[entry["name"]] = found
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
    for key in ("meta", "amp"):
        if raw.get(key) is not None and not isinstance(raw[key], dict):
            problems.append(f"{key} must be a mapping (got {raw[key]!r})")
    for key in ("name", "description"):
        if isinstance(raw.get("meta"), dict) and key in raw["meta"]:
            problems.append(f"meta: {key!r} is the rig's own {key}, set at the top of the file")

    settings: dict[str, dict[str, Any]] = {}
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
        if not _is_width(entry.get("width")):
            problems.append(
                f"{where}: width must be one of {', '.join(WIDTHS)} (got {entry.get('width')!r})"
            )
        chain = _chain(where, entry.get("chain"), problems, settings)
        if "strip" in entry and isinstance(entry.get("name"), str):
            # The track's own gain, pan and mute, normalised as Sushi stores them.
            strip = _settings(f"{where}: strip", {"parameters": entry["strip"]}, problems)
            if strip and not strip["parameters"]:
                # `strip: {}` would compile to an initial_state entry holding only
                # the track's name, which nothing has ever sent to Sushi.
                problems.append(f"{where}: strip must name gain, pan or mute")
            elif strip and set(strip["parameters"]) - set(UNITY_STRIP):
                # Beside a valid key, an unknown one would compile to a track
                # parameter Sushi was never told about.
                problems.append(
                    f"{where}: strip may only name gain, pan or mute, not "
                    f"{', '.join(sorted(set(strip['parameters']) - set(UNITY_STRIP)))}"
                )
            elif strip:
                problems += _strip_problems(where, strip["parameters"])
                settings[entry["name"]] = strip
        if isinstance(entry.get("name"), str) and _is_width(entry.get("width")):
            channels.append(Channel(entry["name"], str(entry.get("input")), entry["width"], chain))

    description = raw.get("description")
    if description is not None and not isinstance(description, str):
        problems.append(f"description must be text (got {description!r})")

    master_raw = raw.get("master")
    master = None
    if not isinstance(master_raw, dict):
        problems.append("master must say where the mix goes: master: {output: <role>}")
    else:
        problems += _unknown("master", master_raw, _MASTER_KEYS)
        if not isinstance(master_raw.get("output"), str) or not master_raw["output"].strip():
            problems.append("master: output must name a role in the machine file")
        master = Master(str(master_raw.get("output")),
                        _chain("master", master_raw.get("chain"), problems, settings))

    if problems:
        raise RigFileError(problems)
    rig = Rig(str(name), tuple(channels), master, description or "", host,
              settings, raw.get("amp"), dict(raw.get("meta") or {}))
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
        if not _is_width(channel.width):
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

    if any(not _is_width(c.width) for c in rig.channels):
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
    processors: dict[str, Any] = copy.deepcopy(rig.settings)
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
        meta={"name": rig.name, **({"description": rig.description} if rig.description else {}),
              **rig.meta},
    )
    problems = spec.validate()
    if problems:
        raise RigFileError(problems)
    return spec, {"processors": processors}


def merged_state(fixed: dict[str, Any], captured: dict[str, Any] | None) -> dict[str, Any]:
    """Captured state over the compiler's, except where the compiler's must hold.

    Captured properties are not merged at all. `live.capture` records them so
    `verify` can check a rig, but which of a plugin's properties may be set at
    load is not known from the listing, and a value read back as "" would be
    baked into the config. The compiler's own properties, each send's
    destination, always win: a destination other than the master is a send
    to nothing, and the channel is silent in the monitors while its stem
    looks healthy.
    """
    captured = captured or {}
    merged = {key: value for key, value in captured.items() if key != "processors"}
    # The file's settings first, a capture over them: a capture is newer, the
    # same relation a capture has to a v1 rig.yaml's.
    processors = copy.deepcopy(fixed["processors"])
    for name, values in (captured.get("processors") or {}).items():
        if not isinstance(values, dict):
            continue
        entry = processors.setdefault(name, {})
        if values.get("program") is not None and not values.get("parameters"):
            # A captured program replaces the file's parameters, which are the
            # previous program's: `emit` drops a program written beside
            # parameters, so keeping them would reload the old sound.
            entry.pop("parameters", None)
        for key, value in values.items():
            if key == "properties":
                continue
            if key == "parameters" and isinstance(value, dict):
                entry[key] = {**(entry.get(key) or {}), **value}
            elif value is not None:
                entry[key] = value
    for name, values in fixed["processors"].items():
        if name.endswith(SEND_SUFFIX) and "destination_name" in (values.get("properties") or {}):
            processors[name]["properties"] = {
                **(processors[name].get("properties") or {}),
                "destination_name": values["properties"]["destination_name"],
            }
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
    state = merged_state(fixed, captured)
    problems = [
        problem for channel in rig.channels
        for problem in _strip_problems(
            f"channel {channel.name!r}",
            (state["processors"].get(channel.name) or {}).get("parameters") or {},
        )
    ]
    if problems:
        # A capture can carry a strip moved live, which the file never had.
        raise RigFileError(problems)
    config = pin_monitor_path(emit(spec, state))
    if rig.amp:
        config["_amp"] = copy.deepcopy(rig.amp)
    return config


def heard(config: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """What the monitors hear, as configs Sushi's offline frontend can render.

    The offline frontend has one stereo bus, so the stems — each channel's own
    output — are left out, and only the master on bus 0 is rendered. That loses
    nothing about the sound: the send is the channel exactly, sample for sample
    (test_send_return.py). One config for a one-channel rig, named after it; one
    per channel otherwise, named `<rig>.<channel>`. Each is `render._alone`'s
    cut of that channel — the names and the cut `render.channels_of` gives a v1
    config — with the master put back, so a v2 rig is checked against the
    reference its v1 config was calibrated to.
    """
    from .render import _alone

    name = (config.get("_meta") or {}).get("name", "rig")
    master = next(t for t in config["tracks"] if t["name"] == MASTER)
    channels = [t for t in config["tracks"] if t["name"] != MASTER]
    views = []
    for track in channels:
        view = _alone(config, track)
        view["tracks"][0]["outputs"] = []
        view["tracks"].append(copy.deepcopy(master))
        kept = {t["name"] for t in view["tracks"]} | {
            p["name"] for t in view["tracks"] for p in t["plugins"]
        }
        view["initial_state"] = [
            e for e in config.get("initial_state", []) if e["processor"] in kept
        ]
        views.append((name if len(channels) == 1 else f"{name}.{track['name']}", view))
    return views
