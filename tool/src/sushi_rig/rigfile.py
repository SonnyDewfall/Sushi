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
- **A mono plugin's output is put back on both halves.** Sushi runs a plugin
  with one output on channel 0 alone and clears channel 1 after it, so every
  guitar chain, which starts with a mono compressor, came out of the left
  monitor only (2026-11-02). After each such plugin the compiler inserts
  `<plugin>_to_stereo`, Sushi's own `mono_summing`: see `stereo.py`, and
  `plugin-ports.txt` for where the compiler reads which plugins are mono.

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

from . import stereo
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
# Where the compiled config carries the MIDI bindings Sushi cannot hold (bypass,
# a note on a mute, Program Change rig switching), for the agent to serve.
AGENT_BINDINGS = "_midi_agent"
# Where the compiled config records each track's machine roles, for `diff`.
WIRING = "_wiring"
SEND_UID = "sushi.testing.send"
RETURN_UID = "sushi.testing.return"
MASTER_BUS = 0

_KEYS = ("kind", "schema_version", "name", "description", "meta", "host", "amp",
         "channels", "master", "midi")
_MIDI_KEYS = ("controller", "port", "programs", "controls")
_CONTROL_KEYS = ("control", "blend", "parameter", "mute", "bypass", "range")
_BINDINGS = ("blend", "parameter", "mute", "bypass")
_CHANNEL_KEYS = ("name", "input", "width", "strip", "chain", "level")
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
    # The channel's level in the mix, in dB: what the monitors hear of it,
    # with the stem untouched. It is the send's gain (#18): a strip would act
    # after the send and change the stem, so the strip stays at unity and the
    # level lives here. 0 is unity; the send allows -120 to +24.
    level: float = 0.0

    @property
    def inputs_used(self) -> int:
        return WIDTHS[self.width]


@dataclass(frozen=True)
class Master:
    output: str
    chain: tuple[PluginSpec, ...] = ()


@dataclass(frozen=True)
class MidiControl:
    """One binding in a rig's MIDI map (#34): a named control on the machine's
    controller, and what it does. `binding` is one of `_BINDINGS`; `target` is
    the channel pair for a blend, `channel/plugin/parameter` for a parameter,
    a channel for mute, `channel/plugin` for bypass. `range` is the normalised
    span a sweep covers, (0, 1) unless the file narrows it."""

    control: str
    binding: str
    target: Any
    range: tuple[float, float] = (0.0, 1.0)


@dataclass(frozen=True)
class MidiMap:
    """The rig's MIDI map: which controller it is written for, Sushi's MIDI
    input it arrives on, whether Program Changes switch rigs (the agent's
    job, #93), and the controls."""

    controller: str
    controls: tuple[MidiControl, ...] = ()
    port: int = 0
    programs: str | None = None


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
    midi: MidiMap | None = None


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
    holds a comment or a layout that cannot be kept, or when the text would not
    read back as `rig` (yamlmerge checks that). The file is replaced whole or
    not at all, with its permissions."""
    import os
    import shutil

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
    path.parent.mkdir(parents=True, exist_ok=True)
    scratch = path.with_name(path.name + ".tmp")
    try:
        scratch.write_text(text)
        if path.exists():
            shutil.copymode(path, scratch)  # the file keeps its permissions
        os.replace(scratch, path)
    except BaseException:
        scratch.unlink(missing_ok=True)
        raise


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


def spec_of(path: Path, *, routing_only: bool = False) -> RigSpec:
    """The tracks either kind of rig file runs as: compiled from channels and a
    master, or read as written. For the tools that read the spec rather than
    write a config -- the patchbay, SAVE's source check and the probes.
    Anything that writes a config goes through `emit_rig` instead, because the
    sends need state this does not carry.

    `routing_only` is for a caller that reads tracks, roles and names and
    never puts the chains in front of Sushi (the patchbay, SAVE's source
    check): it compiles without the copies to stereo, so it needs no
    `plugin-ports.txt` and refuses no plugin for missing from it. The probes
    build tracks Sushi loads from these chains, so they take the default."""
    rig = load_any(path)
    return compile_rig(rig, routing_only=routing_only)[0] if isinstance(rig, Rig) else rig


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
# The send's gain parameter, from Sushi 1.3.0's send_plugin.cpp: dB, -120 to
# +24, default 0, stored normalised. These two are its unit conversion.
LEVEL_RANGE = (-120.0, 24.0)


def level_to_gain(level_db: float) -> float:
    """A level in dB as the send's normalised `gain`: 0 dB is 120/144."""
    low, high = LEVEL_RANGE
    return (float(level_db) - low) / (high - low)


def gain_to_level(gain: float) -> float:
    """The send's normalised `gain` as a level in dB, to a thousandth, which
    is below anything audible and above a float32 capture's noise. Clamped
    to the send's range, as Sushi clamps what it is sent, so a capture that
    reads 1.05 is +24 dB and not a refusal at save; and never -0.0, which
    YAML would write as `-0.0`."""
    low, high = LEVEL_RANGE
    gain = min(1.0, max(0.0, float(gain)))
    return round(low + gain * (high - low), 3) + 0.0


# A strip at unity, as Sushi stores it: the gain is 0 dB on the same -120 to
# +24 range a send has, derived rather than written twice.
UNITY_STRIP = {"gain": level_to_gain(0.0), "pan": 0.5, "mute": 0.0}


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
        "at gain 0.833333, pan 0.5, mute 0; a channel's level in the mix is `level:` on the "
        "channel, in dB, which is the send's gain (#18)"
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


def _parse_midi(raw: Any, problems: list[str]) -> MidiMap | None:
    """The `midi:` section: a controller named in the machine file, and the
    controls it binds. Shapes only; what the targets name is checked by
    `check_rig`, which knows the channels."""
    if not isinstance(raw, dict):
        problems.append(f"midi must be a mapping (got {raw!r})")
        return None
    problems += _unknown("midi", raw, _MIDI_KEYS)
    controller = raw.get("controller")
    if not isinstance(controller, str) or not controller:
        problems.append("midi: controller must name a controller in the machine file")
        controller = ""
    port = raw.get("port", 0)
    if type(port) is not int or port < 0:
        problems.append(f"midi: port must be Sushi's MIDI input number, 0 or more (got {port!r})")
        port = 0
    programs = raw.get("programs")
    if programs is not None and programs != "rigs":
        problems.append(f"midi: programs must be 'rigs' (a Program Change switches rigs) or absent (got {programs!r})")
    controls: list[MidiControl] = []
    entries = raw.get("controls", [])
    if not isinstance(entries, list):
        problems.append(f"midi: controls must be a list (got {entries!r})")
        entries = []
    shapes_said = {
        "blend": "a list of two channel names",
        "parameter": "channel/plugin/parameter",
        "mute": "a channel name",
        "bypass": "channel/plugin",
    }
    for index, entry in enumerate(entries):
        at = f"midi: control {index + 1}"
        if not isinstance(entry, dict):
            problems.append(f"{at} is {entry!r}, not a mapping")
            continue
        problems += _unknown(at, entry, _CONTROL_KEYS)
        name = entry.get("control")
        if not isinstance(name, str) or not name:
            problems.append(f"{at}: control must name a control on the controller")
            continue
        bound = [key for key in _BINDINGS if key in entry]
        if len(bound) != 1:
            problems.append(f"{at} ({name}): exactly one of {', '.join(_BINDINGS)}")
            continue
        binding, target = bound[0], entry[bound[0]]
        span = (0.0, 1.0)
        if "range" in entry:
            value = entry["range"]
            numbers = isinstance(value, list) and len(value) == 2 and all(
                isinstance(v, (int, float)) and not isinstance(v, bool) and 0 <= v <= 1 for v in value
            )
            if not numbers:
                problems.append(f"{at} ({name}): range must be two numbers from 0 to 1, normalised")
            elif binding != "parameter":
                problems.append(f"{at} ({name}): range applies to a parameter only")
            else:
                span = (float(value[0]), float(value[1]))
        shapes = {
            "blend": isinstance(target, list) and len(target) == 2 and all(isinstance(t, str) for t in target),
            "parameter": isinstance(target, str) and target.count("/") == 2 and all(target.split("/")),
            "mute": isinstance(target, str) and bool(target),
            "bypass": isinstance(target, str) and target.count("/") == 1 and all(target.split("/")),
        }
        if not shapes[binding]:
            problems.append(f"{at} ({name}): {binding} must be {shapes_said[binding]}")
            continue
        controls.append(MidiControl(name, binding, tuple(target) if isinstance(target, list) else target, span))
    return MidiMap(controller, tuple(controls), port, programs)


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
        level = 0.0
        if "level" in entry:
            value = entry["level"]
            low, high = LEVEL_RANGE
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                problems.append(f"{where}: level must be a number in dB (got {value!r})")
            elif not low <= value <= high:
                problems.append(f"{where}: level {value} dB is outside the send's {low:g} to {high:g}")
            else:
                # To a thousandth, the precision SAVE writes back, so a file
                # holding more is not rewritten on a save that changed nothing.
                level = round(float(value), 3) + 0.0
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
            channels.append(Channel(entry["name"], str(entry.get("input")), entry["width"], chain, level))

    midi = _parse_midi(raw.get("midi"), problems) if "midi" in raw else None

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
              settings, raw.get("amp"), dict(raw.get("meta") or {}), midi)
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
    if isinstance(rig.host, dict) and rig.host.get("midi_inputs") is not None:
        # `compile_rig` raises it to hold the MIDI map's port. Here rather
        # than in `parse_rig`, so a Rig built any other way meets the same
        # named problem and not a TypeError from `max`. `null` is absent, as
        # the compiler has always read it. `bool` is an `int`, and `true` is
        # not a count of ports.
        count = rig.host["midi_inputs"]
        if type(count) is not int or count < 0:
            problems.append(f"host.midi_inputs must be a whole number, 0 or more (got {count!r})")
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
        problems += [p for p in [stereo.reserved_problem("channel", channel.name)] if p]

    for owner, chain in [(f"channel {c.name!r}", c.chain) for c in rig.channels] + [
        ("master", rig.master.chain)
    ]:
        for plugin in chain:
            if plugin.name in reserved:
                problems.append(
                    f"{owner}: plugin {plugin.name!r} has a name the compiler gives its "
                    "own send, return or master track; rename it"
                )
            problems += [f"{owner}: {p}" for p in [stereo.reserved_problem("plugin", plugin.name)] if p]

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
    if rig.midi is not None:
        problems += _midi_problems(rig)
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


def _midi_problems(rig: Rig) -> list[str]:
    """Every MIDI binding names something the rig has."""
    problems: list[str] = []
    channels = {c.name for c in rig.channels}
    chains = {c.name: {p.name for p in c.chain} for c in rig.channels}
    chains[MASTER] = {p.name for p in rig.master.chain}
    seen: set[str] = set()
    for control in rig.midi.controls:
        at = f"midi: {control.control}"
        if control.control in seen:
            problems.append(f"{at} is bound twice")
        seen.add(control.control)
        if control.binding == "blend":
            a, b = control.target
            for name in (a, b):
                if name not in channels:
                    problems.append(f"{at}: blend names channel {name!r}, which the rig does not have")
            if a == b:
                problems.append(f"{at}: a blend needs two different channels")
        elif control.binding == "mute":
            if control.target not in channels:
                problems.append(f"{at}: mute names channel {control.target!r}, which the rig does not have")
        else:
            channel, plugin = control.target.split("/")[:2]
            if channel not in chains:
                problems.append(f"{at}: {control.binding} names channel {channel!r}, which the rig does not have")
            elif plugin not in chains[channel]:
                problems.append(f"{at}: {control.binding} names {plugin!r}, which is not in {channel}'s chain")
    return problems


def compile_midi(rig: Rig, controller: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Sushi's `midi` section for the rig's map on `controller`, and the
    bindings Sushi cannot hold, which are the agent's.

    `controller` is `MachineSpec.controller(name)`: the port, the channel
    and each control's (`cc`|`note`, number). What goes to Sushi, verified
    against 1.3.0's `json_configurator.cpp` and `midi_dispatcher.cpp` on
    2026-11-01, is `cc_mappings` only: a CC drives a parameter as
    `min_range + (max_range - min_range) * value / 127`, in the parameter's
    normalised domain, in the engine with no Python in the path. The channel
    is written as the machine file gives it, as Sushi counts it, or "all"
    (any string is omni to Sushi); whether Sushi counts channels from 0 is
    not verified yet and is on the rig session's list.

    - **parameter**: one mapping, the control's range.
    - **mute**: one mapping on the channel's track, parameter `mute`, 0 to 1;
      a track's `mute` is a parameter, so a whole channel mutes natively.
    - **blend**: two mappings on the two channels' sends, parameter `gain`,
      one from the channel's own level down to silence and the other the
      reverse, so a single pedal fades one channel out as the other comes
      in; heel is the first channel, toe the second. A channel's `level` is
      its loudest, so the blend respects the mix.
    - **bypass**: Sushi has no CC-to-bypass connection, so nothing is
      written whether the control is a note or a CC; the binding is returned
      for the agent to serve over gRPC (a CC as a switch: on at 64 and above).
    - **programs**: a Program Change reaches a plugin's program in Sushi,
      not a rig, so rig switching is the agent's too (#93).

    A note cannot sweep, so a control that sends a note may only bypass, or
    mute as a switch through the agent. `RigFileError` for a control the
    controller does not have, or a note bound to a sweep.
    """
    sends = controller["controls"]
    channel = controller.get("channel", "all")
    port = rig.midi.port
    levels = {c.name: c.level for c in rig.channels}
    mappings: list[dict[str, Any]] = []
    agent: list[dict[str, Any]] = []
    problems: list[str] = []

    def mapping(plugin: str, parameter: str, low: float, high: float, number: int) -> dict[str, Any]:
        return {
            "port": port, "channel": channel, "cc_number": number,
            "plugin_name": plugin, "parameter_name": parameter,
            "min_range": round(low, 6), "max_range": round(high, 6), "mode": "absolute",
        }

    for control in rig.midi.controls:
        if control.control not in sends:
            problems.append(
                f"midi: {control.control!r} is not a control of controller {rig.midi.controller!r}; "
                f"it has: {', '.join(sorted(sends))}"
            )
            continue
        kind, number = sends[control.control]
        if control.binding == "bypass":
            agent.append({"control": control.control, kind: number, "channel": channel,
                          "bypass": control.target})
            continue
        if kind == "note":
            if control.binding != "mute":
                problems.append(f"midi: {control.control!r} sends a note, which cannot sweep a {control.binding}")
                continue
            agent.append({"control": control.control, kind: number, "channel": channel,
                          "mute": control.target})
            continue
        if control.binding == "parameter":
            _, plugin, parameter = control.target.split("/")
            mappings.append(mapping(plugin, parameter, *control.range, number))
        elif control.binding == "mute":
            mappings.append(mapping(control.target, "mute", 0.0, 1.0, number))
        else:
            first, second = control.target
            mappings.append(mapping(first + SEND_SUFFIX, "gain", level_to_gain(levels[first]), 0.0, number))
            mappings.append(mapping(second + SEND_SUFFIX, "gain", 0.0, level_to_gain(levels[second]), number))
    if rig.midi.programs:
        agent.append({"programs": rig.midi.programs, "port": port, "channel": channel})
    if problems:
        raise RigFileError(problems)
    section = {"cc_mappings": mappings} if mappings else {}
    return section, agent


def _stereo_chain(where: str, chain: tuple[PluginSpec, ...], ports: dict[str, Any],
                  problems: list[str]) -> list[PluginSpec]:
    """`chain` with the compiler's copy to stereo after every LV2 plugin that
    has one output (`stereo.py`), and a problem for every LV2 plugin whose
    ports the table does not give. A plugin of another type (an internal one
    named by `uid`) is left as written: Sushi's internal plugins take a
    track's channels as they come."""
    from .amp import PLUGIN_URIS as AMP_URIS

    out: list[PluginSpec] = []
    for plugin in chain:
        out.append(plugin)
        if (plugin.type or "lv2") != "lv2":
            continue
        problem = stereo.plugin_problem(plugin.name, plugin.uri, ports, frozenset(AMP_URIS.values()))
        if problem:
            problems.append(f"{where}: {problem}")
        elif stereo.needs_copy(plugin.uri, ports):
            out.append(PluginSpec(name=stereo.copy_name(plugin.name), uid=stereo.COPY_UID, type="internal"))
    return out


def compile_rig(
    rig: Rig, controller: dict[str, Any] | None = None, ports: dict[str, Any] | None = None,
    *, routing_only: bool = False,
) -> tuple[RigSpec, dict[str, Any]]:
    """The `RigSpec` that runs `rig`, and the state its sends need.

    The state is what `emit` takes as captured state: each send's
    `destination_name`, without which a send goes nowhere and the channel is
    silent in the monitors while its stem looks healthy. It is
    `{"processors": {...}}`, and `"agent"` beside it, when the MIDI map has
    bindings only the agent can serve (a bypass, a Program Change): a list
    of them, for `emit_compiled` to write into the config.

    `controller` is the machine's description of the controller the rig's
    MIDI map names (`MachineSpec.controller`). Without one the MIDI map is
    left out, which is right for reading the rig's routing and wrong for
    running it: `emit_rig` refuses that, since a rig quietly running without
    its pedals is the silent failure principle 8 forbids.

    `ports` is each plugin's audio ports by URI (`stereo.load_ports`), the
    committed `plugin-ports.txt` unless given: a plugin with one output gets
    its copy to stereo after it, and one the table does not list is refused
    by name. That is for every caller whose chains Sushi will load (`emit`,
    `up --rig`, the renders, the probes). `routing_only` is for a caller that
    reads only tracks and roles and never loads the chains (the patchbay,
    SAVE's source check, `spec_of(routing_only=True)`): the chains are
    compiled as written, without the copies, and need no table.
    """
    problems = check_rig(rig)
    if problems:
        raise RigFileError(problems)
    if routing_only:
        ports = None
    elif ports is None:
        from .paths import checkout_root

        try:
            ports = stereo.load_ports(checkout_root())
        except stereo.PortsError as exc:
            raise RigFileError(exc.problems) from exc
    problems = []
    if routing_only:
        chains = {c.name: list(c.chain) for c in rig.channels}
        master_chain = list(rig.master.chain)
    else:
        chains = {c.name: _stereo_chain(f"channel {c.name!r}", c.chain, ports, problems) for c in rig.channels}
        master_chain = _stereo_chain("master", rig.master.chain, ports, problems)
    if problems:
        raise RigFileError(problems)
    # Without a controller the spec is still right for routing (the patchbay,
    # the probes, SAVE's source check), only without its MIDI map. Writing a
    # config is where a missing controller is refused: `emit_rig`.
    midi_section: dict[str, Any] = {}
    agent_bindings: list[dict[str, Any]] = []
    host = dict(rig.host or {"samplerate": 48000})
    if rig.midi is not None and controller is not None:
        midi_section, agent_bindings = compile_midi(rig, controller)
        # Sushi opens `midi_inputs` ports and refuses a mapping on one it did
        # not open, so the host asks for enough to hold the map's port.
        # `check_rig` has already refused a value that is not a count.
        host["midi_inputs"] = max(host.get("midi_inputs") or 0, rig.midi.port + 1)
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
            plugins=chains[channel.name] + [PluginSpec(name=send, uid=SEND_UID, type="internal")],
            source=channel.input,
        ))
        processors[send] = {"properties": {"destination_name": RETURN}}
        if channel.level != 0.0:
            # Unity is the send's default, so a channel at 0 dB writes nothing
            # and every existing rig compiles exactly as before.
            processors[send]["parameters"] = {"gain": level_to_gain(channel.level)}  # emit rounds
        next_input += channel.inputs_used
    # Last, always: see the module docstring.
    tracks.append(TrackSpec(
        name=MASTER,
        channels=2,
        inputs=[],
        outputs=[{"engine_bus": MASTER_BUS, "track_bus": 0}],
        plugins=[PluginSpec(name=RETURN, uid=RETURN_UID, type="internal")] + master_chain,
        sink=rig.master.output,
    ))
    spec = RigSpec(
        host=host,
        tracks=tracks,
        meta={"name": rig.name, **({"description": rig.description} if rig.description else {}),
              **rig.meta},
        midi=midi_section,
    )
    problems = spec.validate()
    if problems:
        raise RigFileError(problems)
    fixed: dict[str, Any] = {"processors": processors}
    if agent_bindings:
        # Their own key, not a pseudo-processor in the map above: that map is
        # read as processors by everything that touches it. `emit_compiled`
        # writes them into the config for the agent.
        fixed["agent"] = agent_bindings
    return spec, fixed


def blended_channels(rig: Rig) -> frozenset[str]:
    """The channels a blend drives. A blend moves their sends' gain with the
    pedal, so a gain read off such a send is the pedal's position and not the
    channel's level: neither `fold_state` nor `merged_state` may take it as
    one. (Found by review, 2026-11-02: saved, the next blend is silent at
    every position; emitted, the rig comes up with a channel silent until the
    pedal first moves.)"""
    return frozenset(
        name for control in (rig.midi.controls if rig.midi else ())
        if control.binding == "blend" for name in control.target
    )


def merged_state(
    fixed: dict[str, Any], captured: dict[str, Any] | None,
    blended: frozenset[str] | set[str] = frozenset(),
) -> dict[str, Any]:
    """Captured state over the compiler's, except where the compiler's must hold.

    Captured properties are not merged at all. `live.capture` records them so
    `verify` can check a rig, but which of a plugin's properties may be set at
    load is not known from the listing, and a value read back as "" would be
    baked into the config. The compiler's own properties, each send's
    destination, always win: a destination other than the master is a send
    to nothing, and the channel is silent in the monitors while its stem
    looks healthy. The compiler's `agent` bindings are not state and are not
    merged; `emit_compiled` takes them from `fixed` itself.

    `blended` names the channels a blend drives (`blended_channels`). The
    captured `gain` of their sends is not merged: it is the pedal's position,
    and the compiler's value, from the channel's `level`, holds.
    """
    captured = captured or {}
    merged = {key: value for key, value in captured.items() if key != "processors"}
    # The file's settings first, a capture over them: a capture is newer, the
    # same relation a capture has to a v1 rig.yaml's.
    processors = copy.deepcopy(fixed["processors"])
    for name, values in (captured.get("processors") or {}).items():
        if not isinstance(values, dict) or stereo.is_copy(name):
            # A copy to stereo is the compiler's, with nothing to set: a
            # captured bypass of one is never written into the config.
            continue
        entry = processors.setdefault(name, {})
        if name.endswith(SEND_SUFFIX) and name.removesuffix(SEND_SUFFIX) in blended:
            if isinstance(values.get("parameters"), dict):
                kept = {k: v for k, v in values["parameters"].items() if k != "gain"}
                values = {k: v for k, v in values.items() if k != "parameters"}
                if kept:
                    values["parameters"] = kept
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


def _rounded(value: Any) -> Any:
    """A captured number as the file should hold it: six decimals, as `emit`
    writes them, and an integer kept whole."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return value
    if isinstance(value, int):
        return value
    rounded = round(value, 6)
    return int(rounded) if rounded == int(rounded) and value == int(value) else rounded


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _folded(written: Any, captured: Any) -> Any:
    """The value a save writes for a parameter the file holds as `written`
    and the capture read as `captured`.

    Rounded as `emit` rounds (`_rounded`), and then, where the file had a
    number: the file's own value when the two are the same number, so `1.0`
    stays `1.0` and is not rewritten as `1`; and a float when the file had a
    float. `_rounded` writes a whole number as an int, and in YAML `1` is an
    int where `1.0` was a float, so the first save of a rig rewrote every
    whole-number float, sixteen lines for one knob on the rig (#117,
    2026-11-02): equal values, a different file, and the player's change lost
    among them in `git diff`. A value the file did not have is written as
    `_rounded` gives it."""
    value = _rounded(captured)
    if _is_number(written) and _is_number(value):
        if value == written:
            return written
        if isinstance(written, float) and isinstance(value, int):
            return float(value)
    return value


def fold_state(raw: dict[str, Any], captured: dict[str, Any] | None) -> dict[str, Any]:
    """The rig file's document with a live capture folded into it: what SAVE
    writes for a rig of channels (#30's last slice).

    `raw` is the document `parse_rig` reads, as `write_rig` wants it, so the
    file keeps its comments. Per chain, the captured order of the track's
    processors becomes the chain's order (a plugin the capture does not
    mention keeps its place after those it does, as `emit` appends it), and
    each plugin's captured `bypassed`, `program` and `parameters` are set on
    its entry; a captured program with no parameters drops the entry's
    parameters, which were the previous program's, the same rule as
    `merged_state`. Properties are never folded, for `merged_state`'s reason.

    Not folded, on purpose: a channel's strip, held at unity (`UNITY_STRIP`)
    because a strip moved live changes the stem and not the mix, and the
    sends' destinations and the return, which are the compiler's. A send's
    gain is folded, as the channel's `level` in dB (#18): that is where a
    channel's level in the mix lives.

    A capture naming a processor the rig does not have is refused, as `emit`
    refuses it: that is a capture of a different rig, or a plugin added live.
    So is a chain the capture names that lacks one of the file's plugins: one
    removed live, which folding would otherwise have kept in the file.
    """
    rig = parse_rig(copy.deepcopy(raw))  # refuse what load_rig would refuse
    captured = captured or {}
    processors = captured.get("processors") or {}
    orders = captured.get("tracks") or {}
    known = {p.name for ch in rig.channels for p in ch.chain} | {p.name for p in rig.master.chain}
    known |= {ch.name for ch in rig.channels} | {MASTER}
    internal = {ch.name + SEND_SUFFIX for ch in rig.channels} | {RETURN}
    named = set(processors) | {n for order in orders.values() for n in (order or [])}
    # The copies to stereo after the mono plugins (`stereo.py`): the
    # compiler's, never the file's, and known by their plugin's name.
    internal |= {n for n in named if stereo.is_copy(n) and stereo.source_of(n) in known}
    unknown = sorted(named - known - internal)
    if unknown:
        # Checked first: a capture of another rig (a switch that raced the
        # save) is the larger fault, and must not be told as a chain edit.
        raise RigFileError([
            f"the capture names {', '.join(unknown)}, which {rig.name} does not have: that is a capture "
            "of a different rig, or a plugin added live, which a save cannot write (it writes values, "
            "bypass and order, never which plugins a chain holds), so nothing was saved"
        ])
    # A plugin of the file that a chain the capture names does not hold was
    # removed live (#32's POST /live/remove). Folding used to keep it in its
    # place, so the saved file still had it with nothing said; it is refused,
    # as an added one is above. A track the capture does not name says
    # nothing, and the compiler's own processors (`internal`) are not the
    # file's to list. Found by review, 2026-11-02.
    lacking = []
    for track, entries in [(ch.name, ch.chain) for ch in rig.channels] + [(MASTER, rig.master.chain)]:
        order = orders.get(track)
        if isinstance(order, list):
            lacking += [f"{p.name} (on {track})" for p in entries if p.name not in order]
    if lacking:
        raise RigFileError([
            f"the running rig lacks {', '.join(lacking)}, which {rig.name} has: a plugin removed live "
            "cannot be saved (a save writes values, bypass and order, never which plugins a chain "
            "holds), so nothing was saved. Undo the remove, or edit the rig file and restart the rig"
        ])

    def fold_chain(entries: list[dict[str, Any]], track: str) -> list[dict[str, Any]]:
        by_name = {e["name"]: e for e in entries}
        order = [n for n in (orders.get(track) or []) if n in by_name]
        rest = [e["name"] for e in entries if e["name"] not in order]
        folded = [by_name[n] for n in order + rest]
        for entry in folded:
            values = processors.get(entry["name"]) or {}
            if not isinstance(values, dict):
                continue
            if values.get("program") is not None and not values.get("parameters"):
                entry.pop("parameters", None)
            if values.get("program") is not None:
                entry["program"] = values["program"]
            # `bypassed: false` is the default, so it is written only where the
            # file said true: a save must not touch every entry of a chain
            # nothing changed in, or the file's history stops showing what
            # the player changed.
            if values.get("bypassed") is not None:
                if values["bypassed"]:
                    entry["bypassed"] = True
                elif entry.get("bypassed"):
                    entry["bypassed"] = False
            if isinstance(values.get("parameters"), dict) and values["parameters"]:
                # Rounded as `emit` rounds, because a capture carries float32
                # noise (0.35 reads back as 0.3500000238418579) that would
                # rewrite every parameter line on every save and show a value
                # that did not change as changed.
                written = entry.get("parameters") or {}
                entry["parameters"] = {
                    **written,
                    **{k: _folded(written.get(k), v) for k, v in values["parameters"].items()},
                }
        return folded

    blended = blended_channels(rig)
    out = copy.deepcopy(raw)
    for channel in out.get("channels") or []:
        if not isinstance(channel, dict):
            continue
        if channel.get("chain"):
            channel["chain"] = fold_chain(channel["chain"], channel["name"])
        # The send's gain moved live is the channel's level (#18). Written
        # when it differs from what the file says, as a level in dB. Not for
        # a channel a blend drives: there the gain is the pedal's position,
        # and saving it as the level would make the next blend silent at
        # every position (found by review, 2026-11-02).
        if channel.get("name") in blended:
            continue
        send = processors.get(str(channel.get("name")) + SEND_SUFFIX) or {}
        gain = (send.get("parameters") or {}).get("gain") if isinstance(send, dict) else None
        if isinstance(gain, (int, float)) and not isinstance(gain, bool):
            level = gain_to_level(gain)
            current = channel.get("level", 0.0)
            if level != current:
                channel["level"] = level
    master = out.get("master")
    if isinstance(master, dict) and master.get("chain"):
        master["chain"] = fold_chain(master["chain"], MASTER)
    return out


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
        # Each copy to stereo straight after its plugin (`stereo.pin_copies`),
        # for the same reason: a capture's order or emit's appending can put
        # it anywhere, and anywhere else is a silent right channel.
        plugins = track["plugins"] = stereo.pin_copies(track["plugins"])
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


def emit_rig(
    rig: Rig, captured: dict[str, Any] | None = None,
    controller: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The Sushi config for `rig`, with any captured state folded in, and
    its MIDI map compiled for `controller`. A rig with a MIDI map and no
    controller is refused: its controls cannot be resolved to anything Sushi
    can hear, and a config written without its pedals would run silently
    without them."""
    if rig.midi is not None and controller is None:
        raise RigFileError([
            f"the rig's MIDI map is written for controller {rig.midi.controller!r}, and no "
            "controller was given: the machine file says what its controls send"
        ])
    spec, fixed = compile_rig(rig, controller)
    return emit_compiled(rig, spec, fixed, captured)


def emit_compiled(
    rig: Rig, spec: RigSpec, fixed: dict[str, Any], captured: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """`emit_rig`'s second half, for a caller that already compiled the rig
    (`prepare_rig` compiles once for the patchbay and the config)."""
    from .emit import emit

    state = merged_state(fixed, captured, blended_channels(rig))
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
    bindings = fixed.get("agent")
    config = pin_monitor_path(emit(spec, state))
    if rig.amp:
        config["_amp"] = copy.deepcopy(rig.amp)
    if bindings:
        # What the agent serves: Sushi ignores an underscored key, as it does
        # `_amp` and `_meta`, and `up` leaves nothing of the map unrecorded.
        config[AGENT_BINDINGS] = bindings
    # Which machine roles each track is wired to (#93). Sushi never sees them
    # (`TrackSpec.to_sushi` leaves them out) and only the patchbay does, so
    # without this a live switch between two rigs that differ only in a
    # channel's input could not tell, and would keep playing the old input
    # through the next rig's chain. Underscored, so Sushi ignores it as it
    # ignores `_amp`.
    config[WIRING] = {track.name: {"source": track.source, "sink": track.sink} for track in spec.tracks}
    return config


def heard(config: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """What the monitors hear, as configs Sushi's offline frontend can render.

    The offline frontend has one stereo bus, so the stems — each channel's own
    output — are left out, and only the master on bus 0 is rendered. That loses
    nothing about the sound: the send is the channel exactly, sample for sample
    (test_send_return.py). One config for a one-channel rig, named after it; one
    per channel otherwise, named `<rig>.<channel>`. Each is `render._alone`'s
    cut of that channel — the names and the cut `render.channels_of` gives a v1
    config — with the master put back. The names are the rig's own references,
    `config/sound/<name>.yaml` (`render.rig_channels`, `nulltest --all`); it
    used to be checked against its v1 config's, until the compiler's copies to
    stereo (stereo.py, 2026-11-02) made the two sound different, and the v1
    config's are `<name>.v1-mono.yaml` now.
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


# =========================================================================== #
# Switching rigs live (#93): the difference between two compiled rigs
# =========================================================================== #
#
# A switch between songs by restarting Sushi costs about five seconds of
# silence. `diff` says how to get from the running rig to the next one through
# the agent's live operations instead (`/live/remove`, `/live/add`,
# `/live/move`, `/live/parameter`, `/live/bypass`), or why that cannot be done
# and a restart is needed. It decides; the agent applies, verifies, and
# restarts when told to or when anything goes wrong.
#
# The contract is that a live switch leaves the rig as `up` of the next rig
# would: the same processors in the same order, each with the next rig's
# settings. What the live operations cannot reach is a restart, named, never
# an approximation.
#
# NOT RUN AGAINST A LIVE SUSHI, and not heard (2026-11-02). What the plan
# relies on about Sushi 1.3.0 that has not been observed, each a guess:
#
# - A processor whose `initial_state` entry says nothing of `bypassed` loads
#   un-bypassed. (`emit` has always written none for such a one.)
# - A processor added live starts at the plugin's defaults, the same values
#   a config load gives it, so a replaced processor is the restarted one.
# - A processor deleted live frees its name at once, for an add straight
#   after under the same name (a replacement).
# - A cc_mapping is bound to the processor when the config loads, so one
#   removed and added again is no longer driven by its pedal (`midi_target`).
# - cc_mappings cannot be set live by anything this tool has run. elkpy
#   1.3.0 has `MidiController.connect_cc_to_parameter` and `disconnect_cc`
#   (the ConnectCCToParameter RPC), which look like a live way; they are
#   unverified, so a difference in cc_mappings is a restart until they are.
# - A track's strip is at `UNITY_STRIP` when the config sets none (a send's
#   gain at 0 dB is read from send_plugin.cpp; the strip is not).
#
# The live calls the agent makes for each step carry their own unverified
# list in live.py (placing an add, the session read, the read-back).
#
# The amp is the one part of a rig outside Sushi (amp.py). Its parameters
# are set live, over Carla's OSC, by the call `POST /amp/parameter` makes
# (`amp` steps); its model is loaded only when Carla starts, and Carla is
# started, and the patchbay wired through it, only with the rig, so a model
# or an amp present in one rig and not the other stays a restart. What a
# parameter is at in Carla cannot be read back: an `amp` step is compared
# with what the agent last sent, never with what Carla holds.

# Below float32's resolution on 0..1 (about 6e-8) and the six decimals a rig
# file holds, so a value read back from Sushi that only carries float32 noise
# is not sent again, and any value a rig file can state differently is.
SET_TOLERANCE = 5e-7

# Why a switch needs a restart, by code: what the answer and the log carry.
RESTART_REASONS = {
    "unreadable": "a config is not a compiled rig this tool can compare",
    "host": "the host config (sample rate, MIDI inputs) is read only when Sushi starts",
    # elkpy's connect_cc_to_parameter may make this live; unverified, see above.
    "midi": "Sushi's midi section (cc_mappings) is loaded only with the config",
    "config_section": "a section of the config the live operations do not reach differs",
    "amp": "the amp's model differs, or one rig has an amp and the other not: Carla loads a model "
           "only when it starts, and is started, with the patchbay wired through it, only with the rig",
    "wiring": "a channel's input or the master's output is another role, which only the patchbay wires",
    "wiring_unknown": "a config does not say which roles its tracks are wired to (compiled before #93)",
    "track_added": "a track would be added: the live operations make no tracks",
    "track_removed": "a track would be removed: the live operations remove no tracks",
    "track_order": "the tracks are in another order, which Sushi takes only at load",
    "track": "a track's channels, inputs or outputs differ, which Sushi takes only at load",
    "protected": "a send or the master's return would have to be added, removed or replaced",
    "plugin_type": "a plugin to add is not LV2, the only kind the live add makes",
    "program": "a program would have to be set, and no live operation sets one",
    "properties": "a property would have to be set, and no live operation sets one",
    "parameter_default": "a parameter the next rig leaves at its default, whose default is not known",
    "midi_target": "a processor a cc_mapping drives would be replaced, and the mapping is bound at load",
    "drifted": "the running rig is no longer what its config says, so a plan made from it would be wrong",
    "stereo": "a plugin has a copy to stereo in one rig and not the other (plugin-ports.txt changed between them)",
    "stereo_missing": "the running rig was compiled before the copies to stereo existed, so a restart adds them",
}

# The settings of the compiler's own processors that the next rig may leave
# unstated, and what Sushi has them at then: a track's strip at unity, and a
# send's gain at 0 dB (send_plugin.cpp's default, as `compile_rig` relies on).
_KNOWN_DEFAULTS = {"track": UNITY_STRIP, SEND_UID: {"gain": level_to_gain(0.0)}, RETURN_UID: {}}


@dataclass(frozen=True)
class SwitchStep:
    """One live operation, in the words of the route that makes it: `args` is
    that route's body (an add's `before` is the processor it goes in front of,
    None for the back of the chain, which is Sushi's own way of placing one)."""

    op: str
    args: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {"op": self.op, **self.args}


@dataclass(frozen=True)
class SwitchPlan:
    """The steps that take the running rig to the next one, in order (see
    `diff` for why that order)."""

    steps: tuple[SwitchStep, ...]

    def counts(self) -> dict[str, int]:
        found: dict[str, int] = {}
        for step in self.steps:
            found[step.op] = found.get(step.op, 0) + 1
        return found


@dataclass(frozen=True)
class RestartReason:
    code: str  # a key of RESTART_REASONS
    detail: str

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "detail": self.detail}


@dataclass(frozen=True)
class NeedsRestart:
    """Every reason the switch cannot be made live, not the first: a player
    told "the amp differs" and then, after fixing that, "and a track" has been
    told one at a time what could have been said at once."""

    reasons: tuple[RestartReason, ...]

    @property
    def codes(self) -> list[str]:
        return [reason.code for reason in self.reasons]


def _identity(plugin: dict[str, Any]) -> tuple[Any, ...]:
    """Which plugin a processor is: the same name over another plugin is a
    different processor, and is replaced rather than retuned."""
    return (plugin.get("type"), plugin.get("uri"), plugin.get("uid"), plugin.get("path"))


def _close(a: Any, b: Any) -> bool:
    numbers = all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in (a, b))
    return numbers and abs(a - b) <= SET_TOLERANCE


def _amp_plan(
    amp_r: Any, amp_n: Any, sent: dict[str, Any] | None, *, off: bool = False, root: Path | None = None,
) -> tuple[list[SwitchStep], list[RestartReason]]:
    """The `amp` steps that set the next rig's amp parameters, or the reasons
    the amp needs a restart. Pure, but for reading where a model is (`root`).

    `off` is a run with no amp at all (`up --no-amp`): nothing about either
    `_amp` matters then, since no Carla is running and a restart into the
    next rig would start none (`restart_arguments` keeps the choice), so it
    is neither a step nor a reason.

    The model is compared as a file, with `amp_live.same_model` from `root`
    when one is given: one `.nam` written as a relative and an absolute path
    is the same model. Without a root, as written.

    `sent` is what the agent last sent Carla for each parameter (None where
    it has sent nothing), from the live reading; without one, the running
    rig's `_amp` stands for it, as its config stands for Sushi's values. Each
    stated parameter of the next rig is sent where it differs from that; one
    the next rig leaves unstated, which the running rig did set, is a restart:
    the plugin's own default is known only to the plugin, as with a Sushi
    parameter left at its default (`parameter_default`). Values are clamped as
    `amp.clamp` clamps what is sent, so a value outside the range is a step to
    its end, and one that is not a number is a restart rather than a guess.
    """
    from .amp import PARAMETER_INDEX, AmpError, clamp

    if off or (amp_r is None and amp_n is None):
        return [], []
    if amp_r is None or amp_n is None:
        which = "the next rig has an amp and the running one has none" if amp_r is None \
            else "the running rig has an amp and the next has none"
        return [], [RestartReason("amp", which)]
    if not isinstance(amp_r, dict) or not isinstance(amp_n, dict):
        return [], [RestartReason("amp", "an _amp section is not a mapping")]
    shape_r = {k: v for k, v in amp_r.items() if k != "parameters"}
    shape_n = {k: v for k, v in amp_n.items() if k != "parameters"}
    if root is not None and "model" in shape_r and "model" in shape_n:
        from .amp_live import same_model

        if same_model(shape_r["model"], shape_n["model"], root):
            shape_n["model"] = shape_r["model"]
    if shape_r != shape_n:
        keys = sorted(k for k in set(shape_r) | set(shape_n) if shape_r.get(k) != shape_n.get(k))
        said = ", ".join(f"{k} {shape_r.get(k)!r} becomes {shape_n.get(k)!r}" for k in keys)
        return [], [RestartReason("amp", f"the amp's {said}")]
    will = amp_n.get("parameters") or {}
    was = amp_r.get("parameters") or {}
    if not isinstance(will, dict) or not isinstance(was, dict):
        return [], [RestartReason("amp", "an _amp section's parameters are not a mapping")]

    reasons: list[RestartReason] = []
    targets: dict[str, float] = {}
    for name, value in will.items():
        if name not in PARAMETER_INDEX:
            reasons.append(RestartReason("amp", f"the next rig sets amp parameter {name!r}, which the amp does not have"))
            continue
        try:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise AmpError(f"{name} is {value!r}")
            targets[name] = clamp(name, value)
        except AmpError as exc:
            reasons.append(RestartReason("amp", f"the next rig's amp {exc}, not a value to send"))
    if sent is None:
        current: dict[str, Any] = {}
        for name, value in was.items():
            try:
                current[name] = clamp(name, value) if name in PARAMETER_INDEX else None
            except (AmpError, TypeError, ValueError):
                current[name] = None
    else:
        current = dict(sent)
    steps: list[SwitchStep] = []
    for name in PARAMETER_INDEX:  # Carla's order, so a plan reads the same each time
        now = current.get(name)
        if name not in will:
            if now is not None:
                reasons.append(RestartReason(
                    "amp", f"the next rig leaves amp parameter {name} unstated, and its default is known "
                           "only to the plugin"))
            continue
        if name in targets and not _close(now, targets[name]):
            steps.append(SwitchStep("amp", {"name": name, "value": targets[name]}))
    return ([], reasons) if reasons else (steps, [])


def _cc_targets(midi: Any) -> set[str]:
    """Every processor Sushi's midi section names, cc_mappings above all:
    a mapping is bound to the processor when the config loads (a guess about
    Sushi 1.3.0; its source was not read for it), so one that is removed and
    added again live would no longer be driven by its pedal."""
    names: set[str] = set()
    for entries in (midi or {}).values() if isinstance(midi, dict) else ():
        for entry in entries if isinstance(entries, list) else ():
            if isinstance(entry, dict):
                for key in ("plugin_name", "processor"):
                    if isinstance(entry.get(key), str):
                        names.add(entry[key])
    return names


def diff(
    running: dict[str, Any], next_config: dict[str, Any], live: dict[str, Any] | None = None,
    *, root: Path | None = None, amp_off: bool = False,
) -> SwitchPlan | NeedsRestart:
    """How to turn the running rig into the next one live, or why not. Pure.

    `running` and `next_config` are compiled configs (`emit_compiled`'s). With
    `live`, a reading of the running rig as `live.capture` gives it, the plan
    is made against what the rig holds rather than what its config said: its
    chains must still be the config's (or the plan would move and place by
    positions that are not there: `drifted`), and a parameter or bypass is
    compared with the value Sushi reports, so a knob turned during the last
    song is put where the next rig says rather than left, which verification
    would then refuse. Its `amp`, `{"parameters": {name: value or None}}`, is
    what the agent last sent Carla, which is all that can be known of the amp
    (amp.py); without it, the running rig's `_amp` stands for that.
    `amp_off` is a run with no amp (`--no-amp`), where neither rig's `_amp`
    matters. `root` is where a relative model path is read from, so a model is
    compared as the file it names (`_amp_plan`).

    A processor survives the switch when the next rig has one of the same
    name, on the same track, of the same plugin. Its parameters and bypass are
    set where they differ. It is replaced (removed, then added fresh) where
    the next rig leaves at the plugin's default something the running rig set
    -- a parameter, a program, a property -- because a plugin's defaults are
    known only to the plugin and a fresh one is the only way to them. Every
    other processor is removed or added.

    The order of the steps, and why it is safe:

    1. **Bypass on**, for processors that stay and are off in the next rig: a
       pedal the next rig does not use goes quiet before anything around it
       changes, so it is never heard part-way between two settings.
    2. **Removes.** Sushi's processor names are one namespace, tracks
       included, so a replaced processor, or one moving to another track, can
       be created only once the old one is gone; and every move after this is
       through a shorter chain.
    3. **Moves**, survivors only: each chain's survivors are put in the next
       rig's order by single steps, each a swap of two neighbours that are
       out of order, so the count is the fewest possible (the inversions). A
       send is last and the master's return first in every compiled chain, so
       neither is ever one of an out-of-order pair, and `decide_move` would
       refuse to swap either anyway.
    4. **Adds**, each chain from its end backwards, each in front of the
       processor that follows it in the next rig: that one is a survivor, now
       in order, or was added just before, so it is there; and on a channel
       the send is always last, so nothing is ever added behind it. No
       added processor needs a move.
    5. **Each add is followed at once by bypassing what it added**, so a
       fresh processor is not heard at the plugin's defaults while its
       parameters are set; it comes in at step 7 if the next rig has it on.
       Explicit either way, so nothing depends on whether Sushi creates a
       processor bypassed or not.
    6. **Parameters**, after the adds, since a parameter can be set only on a
       processor that exists; every stated one for an added processor (fresh
       ones are at the plugin's defaults), the ones that differ otherwise.
       Then **the amp's parameters** (`amp` steps, `_amp_plan`), the ones that
       differ from what was last sent Carla, in Carla's parameter order.
    7. **Bypass off** last: a pedal comes in only once its parameters are the
       next rig's.

    What a restart is needed for is `RESTART_REASONS`; every reason found is
    returned, not the first.

    Not covered, and said rather than guessed at: a plugin's parameter
    neither rig's file states keeps whatever the running rig has (a restart
    would give the plugin's default, which only the plugin knows), which
    only matters for one turned live -- the strips and the sends, whose
    defaults are known, are put back to them; and a processor neither file
    states a bypass for is taken to load un-bypassed, which is what `emit`
    assumes in writing none (a guess about Sushi: see the list above this
    section).
    """
    reasons: list[RestartReason] = []

    def need(code: str, detail: str) -> None:
        reasons.append(RestartReason(code, detail))

    tracks_r, tracks_n = running.get("tracks"), next_config.get("tracks")
    if not isinstance(tracks_r, list) or not isinstance(tracks_n, list):
        return NeedsRestart((RestartReason("unreadable", "a config has no list of tracks"),))

    # -- what Sushi takes only at load, and what the agent cannot reach -------
    handled = {"tracks", "initial_state", "host_config", "midi", "_amp", WIRING,
               "_meta", AGENT_BINDINGS}
    if running.get("host_config") != next_config.get("host_config"):
        need("host", f"host config {running.get('host_config')} is {next_config.get('host_config')} in the next rig")
    if (running.get("midi") or {}) != (next_config.get("midi") or {}):
        need("midi", "the cc_mappings (Sushi's midi section) differ, and Sushi loads them only with the config")
    for key in sorted((set(running) | set(next_config)) - handled):
        # `_meta` is provenance and `_midi_agent` is the agent's own listener,
        # which follows the next rig on its own; anything else unknown is
        # refused rather than assumed harmless.
        if running.get(key) != next_config.get(key):
            need("config_section", f"{key!r} differs")
    amp_steps, amp_reasons = _amp_plan(
        running.get("_amp") or None, next_config.get("_amp") or None,
        ((live or {}).get("amp") or {}).get("parameters") if live is not None and "amp" in live else None,
        off=amp_off, root=root)
    reasons.extend(amp_reasons)
    wiring_r, wiring_n = running.get(WIRING), next_config.get(WIRING)
    if not isinstance(wiring_r, dict) or not isinstance(wiring_n, dict):
        which = "the running rig's" if not isinstance(wiring_r, dict) else "the next rig's"
        need("wiring_unknown", f"{which} config does not say which roles its tracks are wired to")
    elif wiring_r != wiring_n:
        changed = sorted(n for n in set(wiring_r) & set(wiring_n) if wiring_r[n] != wiring_n[n])
        if changed:
            need("wiring", f"{', '.join(changed)} {'is' if len(changed) == 1 else 'are'} wired to other roles")

    # -- tracks ----------------------------------------------------------------
    by_track_r = {t.get("name"): t for t in tracks_r if isinstance(t, dict)}
    by_track_n = {t.get("name"): t for t in tracks_n if isinstance(t, dict)}
    order_r = [t.get("name") for t in tracks_r if isinstance(t, dict)]
    order_n = [t.get("name") for t in tracks_n if isinstance(t, dict)]
    for name in order_n:
        if name not in by_track_r:
            need("track_added", f"track {name!r}")
    for name in order_r:
        if name not in by_track_n:
            need("track_removed", f"track {name!r}")
    common = [n for n in order_n if n in by_track_r]
    if [n for n in order_r if n in by_track_n] != common:
        need("track_order", f"{[n for n in order_r if n in by_track_n]} becomes {common}")
    for name in common:
        shape_r = {k: v for k, v in by_track_r[name].items() if k != "plugins"}
        shape_n = {k: v for k, v in by_track_n[name].items() if k != "plugins"}
        if shape_r != shape_n:
            keys = sorted(k for k in set(shape_r) | set(shape_n) if shape_r.get(k) != shape_n.get(k))
            need("track", f"track {name!r}: {', '.join(keys)} differ")

    # -- the live reading, if there is one, must be the running config --------
    if live is not None:
        chains_live = live.get("tracks") or {}
        chains_cfg = {n: [p.get("name") for p in t.get("plugins") or []] for n, t in by_track_r.items()}
        if chains_live != chains_cfg:
            off = sorted(n for n in set(chains_live) | set(chains_cfg)
                         if chains_live.get(n) != chains_cfg.get(n))
            need("drifted", "the chains of " + ", ".join(
                f"{n!r} are {chains_live.get(n)}, the config says {chains_cfg.get(n)}" for n in off))

    if reasons and any(r.code in ("track_added", "track_removed", "track_order", "drifted") for r in reasons):
        # The processors below are compared track by track; with the tracks
        # themselves different, what that would find is noise.
        return NeedsRestart(tuple(reasons))

    # -- processors --------------------------------------------------------------
    from .live import protected_processor

    # The copies to stereo (`stereo.py`) are planned with their plugins, not
    # as processors: each chain is compared without them, an add says which
    # copy goes in with its plugin, and the agent's remove and move take a
    # plugin's copy with it. So the plan below never names a copy.
    def chains(tracks: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
        return {n: stereo.strip_copies([p for p in (t.get("plugins") or []) if isinstance(p, dict)])
                for n, t in tracks.items()}

    def copied(tracks: dict[str, Any]) -> set[str]:
        return {stereo.source_of(p["name"]) for t in tracks.values() for p in (t.get("plugins") or [])
                if isinstance(p, dict) and stereo.is_copy(str(p.get("name", "")), p.get("uid"))}

    chains_r, chains_n = chains(by_track_r), chains(by_track_n)
    copied_r, copied_n = copied(by_track_r), copied(by_track_n)
    where_r = {p["name"]: (track, p) for track, ps in chains_r.items() for p in ps}
    where_n = {p["name"]: (track, p) for track, ps in chains_n.items() for p in ps}
    state_r = {e.get("processor"): e for e in running.get("initial_state") or [] if isinstance(e, dict)}
    state_n = {e.get("processor"): e for e in next_config.get("initial_state") or [] if isinstance(e, dict)}
    live_procs = (live or {}).get("processors") or {}

    survivors = {
        name for name, (track, plugin) in where_n.items()
        if name in where_r and where_r[name][0] == track and _identity(where_r[name][1]) == _identity(plugin)
    }

    def defaults(name: str) -> dict[str, float] | None:
        """What the compiler's own processors and the tracks are at when the
        next rig states nothing; None for a plugin's, which are unknown."""
        if name in by_track_n:
            return _KNOWN_DEFAULTS["track"]
        uid = where_n[name][1].get("uid") if name in where_n else None
        return _KNOWN_DEFAULTS.get(uid)

    # A survivor whose next state is not reachable by setting values is
    # replaced; one that cannot be (the compiler's own) is a restart.
    for name in sorted(survivors):
        track, plugin = where_n[name]
        was, will = state_r.get(name) or {}, state_n.get(name) or {}
        protected = protected_processor(track, name, plugin.get("uid"))
        if was.get("properties") != will.get("properties"):
            if will.get("properties") or protected:
                need("properties", f"{name!r}: {was.get('properties')} becomes {will.get('properties')}")
                continue
        if will.get("program") is not None and will.get("program") != was.get("program"):
            need("program", f"{name!r}: program {will.get('program')}")
            continue
        dropped = sorted(set(was.get("parameters") or {}) - set(will.get("parameters") or {}))
        known = defaults(name)
        unknown = [p for p in dropped if known is None or p not in known]
        reset_program = was.get("program") is not None and will.get("program") is None
        reset_properties = bool(was.get("properties")) and not will.get("properties")
        if unknown or reset_program or reset_properties:
            if protected or plugin.get("type") != "lv2":
                # Named by what is actually left at a default: parameters, the
                # program, the properties (found by review: a properties reset
                # was said as "its program").
                left = ([f"parameter {', '.join(unknown)}"] if unknown else []) + \
                    (["its program"] if reset_program else []) + \
                    ([f"its properties ({', '.join(sorted(was['properties']))})"] if reset_properties else [])
                need("parameter_default", f"{name!r} leaves {' and '.join(left)} at a default that is "
                                          "not known, and it cannot be added fresh")
                continue
            survivors.discard(name)  # replaced: removed, and added fresh below

    # Checked on the survivors left once the replacements are dropped: a
    # replaced plugin is removed with its copy and added with its own.
    if copied_n and not copied_r:
        # The running rig has no copies at all: compiled before 2026-11-02,
        # when the compiler began making them, so its mono plugins play the
        # left channel only. Not a table that changed: a restart compiles them.
        need("stereo_missing", "the running rig was compiled without copies to stereo, so its mono "
                               f"plugins ({', '.join(sorted(copied_n & survivors)) or 'all of them'}) play "
                               "the left channel only; a restart puts the copies in")
    else:
        for name in sorted(survivors):
            if (name in copied_r) != (name in copied_n):
                # The same plugin, so the same ports: only a changed
                # plugin-ports.txt between the two compiles does this.
                need("stereo", f"{name!r} has a copy to stereo in one rig and not the other")
    removed = [name for name in where_r if name not in survivors]
    added = [name for name in where_n if name not in survivors]
    for name in added:
        track, plugin = where_n[name]
        if protected_processor(track, name, plugin.get("uid")):
            need("protected", f"{name!r} on {track!r} would have to be added")
        elif plugin.get("type") != "lv2":
            need("plugin_type", f"{name!r} is {plugin.get('type')}")
        will = state_n.get(name) or {}
        if will.get("program") is not None:
            need("program", f"{name!r}, added, would need program {will['program']}")
        if will.get("properties"):
            need("properties", f"{name!r}, added, would need {sorted(will['properties'])}")
    for name in removed:
        track, plugin = where_r[name]
        if protected_processor(track, name, plugin.get("uid")):
            need("protected", f"{name!r} on {track!r} would have to be removed")
    for name in sorted(_cc_targets(running.get("midi")) & set(removed)):
        need("midi_target", f"{name!r} is driven by a cc_mapping and would be replaced")
    if reasons:
        return NeedsRestart(tuple(reasons))

    # -- the steps, in the order the docstring gives ----------------------------
    bypass_on: list[SwitchStep] = []
    bypass_off: list[SwitchStep] = []
    parameters: list[SwitchStep] = []
    for name in where_n:
        if name not in survivors:
            continue
        was, will = state_r.get(name) or {}, state_n.get(name) or {}
        now = live_procs.get(name) or {}
        current = now.get("bypassed") if live is not None else was.get("bypassed", False)
        want = will.get("bypassed", False)
        if current is None or bool(current) != want:
            (bypass_on if want else bypass_off).append(SwitchStep("bypass", {"processor": name, "bypassed": want}))

    def set_values(name: str, fresh: bool) -> None:
        was = (state_r.get(name) or {}).get("parameters") or {}
        will = (state_n.get(name) or {}).get("parameters") or {}
        now = (live_procs.get(name) or {}).get("parameters") or {}
        known = defaults(name) or {}
        targets = dict(will)
        if not fresh:
            # The compiler's own processors and the strips go to their known
            # default wherever the next rig states nothing: a send's gain a
            # blend pedal moved, read live, comes back to unity as a restart
            # would bring it. (A plugin's parameter the running rig set and
            # the next leaves unstated cannot reach here: it was replaced.)
            for parameter, default in known.items():
                targets.setdefault(parameter, default)
        for parameter, value in targets.items():
            if not fresh:
                current = now.get(parameter) if live is not None else was.get(parameter, known.get(parameter))
                if _close(current, value):
                    continue
            parameters.append(SwitchStep("parameter", {"processor": name, "parameter": parameter, "value": value}))

    for track in by_track_n:  # the strips, which are the tracks' own parameters
        set_values(track, fresh=False)
    for name in where_n:
        set_values(name, fresh=name not in survivors)

    removes = [SwitchStep("remove", {"processor": name}) for name in removed]
    moves: list[SwitchStep] = []
    adds: list[SwitchStep] = []  # each add, and the bypass that follows it
    comes_in: list[SwitchStep] = []  # what was added and the next rig has on
    for track in by_track_n:
        current = [p["name"] for p in chains_r[track] if p["name"] in survivors]
        target = [p["name"] for p in chains_n[track] if p["name"] in survivors]
        for index, name in enumerate(target):
            at = current.index(name)
            while at > index:  # each step swaps one out-of-order pair
                moves.append(SwitchStep("move", {"processor": name, "direction": "up"}))
                current[at - 1], current[at] = current[at], current[at - 1]
                at -= 1
        chain = [p["name"] for p in chains_n[track]]
        for index in range(len(chain) - 1, -1, -1):
            name = chain[index]
            if name in survivors:
                continue
            before = chain[index + 1] if index + 1 < len(chain) else None
            add = {"track": track, "name": name, "uri": where_n[name][1].get("uri"), "before": before}
            if name in copied_n:
                add["copy"] = stereo.copy_name(name)  # added with it, in the same operation
            adds.append(SwitchStep("add", add))
            adds.append(SwitchStep("bypass", {"processor": name, "bypassed": True}))
            if not (state_n.get(name) or {}).get("bypassed", False):
                comes_in.append(SwitchStep("bypass", {"processor": name, "bypassed": False}))
    return SwitchPlan(tuple(bypass_on + removes + moves + adds + parameters + amp_steps + comes_in + bypass_off))


def apply_to_chains(chains: dict[str, list[str]], plan: SwitchPlan) -> dict[str, list[str]]:
    """The chains as `plan`'s structural steps leave them, worked out without
    Sushi, so a plan can be shown to reach the next rig's chains. (The agent
    does not use it: each edit it makes is confirmed from its own read-back.)
    A move is a swap with the neighbour, as `live.decide_move` makes it, and
    a plugin's copy to stereo goes with it: moved, removed and added with it."""
    out = {track: list(chain) for track, chain in chains.items()}
    for step in plan.steps:
        args = step.args
        if step.op == "remove":
            # The processor's unit, as `agent.check_remove` takes it: with its
            # copy to stereo only when that directly follows it.
            for track, chain in out.items():
                unit = next((u for u in stereo.units(chain) if u[0] == args["processor"]), None)
                if unit is not None:
                    out[track] = [name for name in chain if name not in unit]
        elif step.op == "move":
            track = next((t for t, c in out.items() if args["processor"] in c), None)
            moved = None if track is None else stereo.step_units(
                out[track], args["processor"], -1 if args["direction"] == "up" else 1)
            if moved is None:
                # Not stored: a None chain made the next step's TypeError the
                # report (found by review).
                raise ValueError(f"{step.as_dict()} cannot be applied: {args['processor']!r} is not the head "
                                 f"of a unit on any chain, or is already at that end ({out.get(track)})")
            out[track] = moved
        elif step.op == "add":
            chain = out[args["track"]]
            names = [args["name"]] + ([args["copy"]] if args.get("copy") else [])
            at = len(chain) if args["before"] is None else chain.index(args["before"])
            chain[at:at] = names
    return out


# How far a value read back may be from what the plan set and still count as
# set: `verify.TOLERANCE` itself, the margin `diff_state` gives the config's
# own values, so the two checks after a switch agree on what "applied" means.
from .verify import TOLERANCE as APPLIED_TOLERANCE  # noqa: E402


def unapplied(plan: SwitchPlan, reading: dict[str, Any]) -> list[str]:
    """Every value `plan` set that `reading` (a `live.capture`, after the
    switch) does not hold, as one line each. Pure.

    `verify.diff_state` checks what the next config states, and a plan sets
    more than that: the strips and sends it puts back to their defaults, and
    the un-bypass of a processor the next rig leaves unstated. A set Sushi
    acknowledged and did not apply would pass `diff_state` there and be
    reported as a live switch that worked (found by review). So the switch is
    checked against the plan's own targets too: each parameter's last value,
    and each processor's last bypass, since an added processor is bypassed
    and then brought in. Not the `amp` steps: Carla cannot be read back, so
    a step it took is one it answered for (see `agent._amp_send`), and that
    is all a switch can check of it."""
    want_values: dict[tuple[str, str], float] = {}
    want_bypass: dict[str, bool] = {}
    for step in plan.steps:
        args = step.args
        if step.op == "parameter":
            want_values[(args["processor"], args["parameter"])] = args["value"]
        elif step.op == "bypass":
            want_bypass[args["processor"]] = bool(args["bypassed"])
    processors = reading.get("processors") or {}
    problems = []
    for (processor, parameter), value in want_values.items():
        got = ((processors.get(processor) or {}).get("parameters") or {}).get(parameter)
        if not (isinstance(got, (int, float)) and not isinstance(got, bool)
                and abs(got - value) <= APPLIED_TOLERANCE):
            problems.append(f"{processor}: {parameter} was set to {value:.4f} and reads back {got!r}")
    for processor, bypassed in want_bypass.items():
        got = (processors.get(processor) or {}).get("bypassed")
        if got is None or bool(got) != bypassed:
            problems.append(f"{processor}: bypass was set to {bypassed} and reads back {got!r}")
    return problems
