"""Channel presets (#127): a channel's sound, saved by name apart from any rig,
to load onto any input.

A preset is one file, `config/channels/<name>.yaml`, as legible as a rig file
and written by the same rules (`write_preset`, which is `write_rig`'s:
updated in place keeping its comments, or written fresh under a header):

    kind: channel
    schema_version: 1
    name: crunch
    width: mono                 # the width it prefers: what it was saved from
    level: -3.0                 # optional, dB: the channel's volume in the mix
    heard_through_amp: amp/models/Neve 2.nam   # optional: see "The amp"
    saved_from: {rig: electric_board, channel: board}   # optional: where it came from
    chain:
      - name: compressor
        uri: http://guitarix.sourceforge.net/plugins/gx_compressor#_compressor
        bypassed: false
        parameters: {RATIO: 0.07}

It holds the chain with its settings and bypass, the channel's `level` and a
preferred width, and **no input**: which socket it plays from is chosen when
it is loaded. Everything here is pure over dicts as YAML reads them, except
the reading and writing of the files themselves.

## Names

Sushi's processor names are global, so two channels loaded from one preset
would clash (#125 found the compiler refuses the duplicates). A preset
therefore stores each plugin under its **base name**, with the channel's
prefix taken off when it is saved (`guitar_compressor` on the channel
`guitar` is stored as `compressor`, `base_names`), and loading names each
plugin for the channel it lands on (`processor_name`: `lead_compressor` on
`lead`). A base name that already starts with the channel's prefix is not
given a second one. Whether the names that result are acceptable -- unique
across the rig, not the compiler's own (`<channel>_send`, `_to_stereo`) --
is the compiler's to say when the rig is compiled, as for any rig file.

## Stereo copies

The compiler puts `<plugin>_to_stereo` after a mono plugin (#119). It is
never in a rig file, and never in a preset: a name ending so is refused
(`stereo.reserved_problem`), and what is saved comes from `fold_state`,
which never writes one.

## The amp

The amp is one instance, in Carla, patched in front of `guitar-in`
(`amp.amp_patchbay`), so it is not part of a preset. A preset saved from a
channel the amp was in front of says so (`heard_through_amp`, the model it
was heard through), so that loading it where no amp is in front can say so
when it is loaded (`amp_note`), not be found out by ear. An amp per channel
is not built.

## MIDI

A rig's MIDI map names plugins as `channel/plugin`. Loading a preset onto a
channel replaces its chain, so a mapping onto that channel's old plugins
either follows (the new chain has the same plugin, by URI, under the same
base name: renamed to its new name) or would point at nothing, which is
refused unless it is dropped (`midi_after_load`).
"""

from __future__ import annotations

import copy
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import stereo
from .save import NAME_PATTERN

KIND = "channel"
SCHEMA_VERSION = 1
PRESETS_DIR = Path("config") / "channels"

_KEYS = ("kind", "schema_version", "name", "description", "width", "level", "heard_through_amp",
         "saved_from", "chain")
_FROM_KEYS = ("rig", "channel")
# The channel a preset is compiled on to check it (`check_preset`): its names
# are prefixed with it, as a load prefixes them.
_PROBE_CHANNEL = "preset"


class PresetError(ValueError):
    """A preset that cannot be used. Carries every problem, not the first."""

    def __init__(self, problems: list[str]):
        self.problems = problems
        super().__init__("\n".join(problems))


@dataclass(frozen=True)
class Preset:
    """A preset as its file holds it. `chain` is the file's entries, copied
    (a load copies them into a rig file as written: settings, bypass and
    all)."""

    name: str
    width: str
    chain: tuple[dict[str, Any], ...]
    level: float = 0.0
    description: str = ""
    heard_through_amp: str | None = None
    saved_from: dict[str, str] | None = None

    @property
    def plugins(self) -> list[str]:
        return [entry["name"] for entry in self.chain]


# --------------------------------------------------------------------------- #
# Names
# --------------------------------------------------------------------------- #

def processor_name(channel: str, base: str) -> str:
    """What a preset's plugin `base` is called on channel `channel`: the
    channel's name, `_`, and the base, unless the base already starts so."""
    prefix = f"{channel}_"
    return base if base.startswith(prefix) else prefix + base


def base_names(channel: str, names: list[str]) -> dict[str, str]:
    """Each of a channel's plugin names as a preset stores it: without
    `<channel>_` where it has that prefix. If taking the prefixes off would
    make two names one (`guitar_eq` and `eq`), none is taken off, since the
    names are then the file's own and stay unique."""
    prefix = f"{channel}_"
    based = {name: name[len(prefix):] if name.startswith(prefix) and len(name) > len(prefix) else name
             for name in names}
    if len(set(based.values())) != len(based):
        return {name: name for name in names}
    return based


# --------------------------------------------------------------------------- #
# Reading
# --------------------------------------------------------------------------- #

def parse_preset(raw: Any) -> Preset:
    """A `Preset` from a parsed YAML document, or `PresetError` naming every
    problem with its shape. The chain's entries are checked as a rig file's
    are (`rigfile._chain`: the keys, the settings' shapes); what the plugins
    are (in the manifest's port table, not the amp) is `check_preset`'s."""
    from .rigfile import WIDTHS, _chain, _level, _unknown

    if not isinstance(raw, dict):
        raise PresetError([f"a channel preset is a mapping, not {type(raw).__name__}"])
    problems = _unknown("preset", raw, _KEYS)
    if raw.get("kind") != KIND:
        problems.append(f"kind must be {KIND!r} (got {raw.get('kind')!r})")
    if raw.get("schema_version") != SCHEMA_VERSION:
        problems.append(f"schema_version {raw.get('schema_version')!r} is not one this tool reads "
                        f"(it reads {SCHEMA_VERSION})")
    name = raw.get("name")
    if not isinstance(name, str) or not NAME_PATTERN.fullmatch(name):
        problems.append(f"name {name!r} must match {NAME_PATTERN.pattern}")
    width = raw.get("width")
    if not (isinstance(width, str) and width in WIDTHS):
        problems.append(f"width must be one of {', '.join(WIDTHS)}, the width it prefers (got {width!r})")
    level = _level("preset", raw, problems, "the send's")
    description = raw.get("description")
    if description is not None and not isinstance(description, str):
        problems.append(f"description must be text (got {description!r})")
    amp = raw.get("heard_through_amp")
    if amp is not None and (not isinstance(amp, str) or not amp.strip()):
        problems.append(f"heard_through_amp must name the amp's model it was heard through (got {amp!r})")
    source = raw.get("saved_from")
    if source is not None:
        if not isinstance(source, dict) or set(source) - set(_FROM_KEYS) or not all(
                isinstance(source.get(k), str) and source.get(k) for k in _FROM_KEYS):
            problems.append(f"saved_from must be {{rig: <rig>, channel: <channel>}} (got {source!r})")
    entries = raw.get("chain")
    if not isinstance(entries, list):
        problems.append(f"chain must be a list of plugins (got {entries!r})")
        entries = []
    _chain("preset", entries, problems, {})
    seen: set[str] = set()
    for entry in entries:
        plugin = entry.get("name") if isinstance(entry, dict) else None
        if not isinstance(plugin, str):
            continue
        if plugin in seen:
            problems.append(f"preset: plugin {plugin!r} appears twice")
        seen.add(plugin)
        problems += [f"preset: {p}" for p in [stereo.reserved_problem("plugin", plugin)] if p]
        if not any(isinstance(entry.get(k), str) and entry.get(k) for k in ("uri", "path", "uid")):
            problems.append(f"preset: plugin {plugin!r} says no uri: which plugin it is is not known")
    if problems:
        raise PresetError(problems)
    return Preset(str(name), str(width), tuple(copy.deepcopy(e) for e in entries), level,
                  description or "", amp, dict(source) if source else None)


def load_preset(path: Path) -> Preset:
    """The preset in file `path`, or `PresetError` (a file that is not YAML
    is one too, said as such)."""
    import yaml

    path = Path(path)
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise PresetError([f"{path.name} is not YAML: {' '.join(str(exc).split())}"]) from exc
    return parse_preset(raw)


def check_preset(preset: Preset, ports: dict[str, Any] | None = None) -> list[str]:
    """Why `preset` would not compile onto a channel, in the compiler's
    words, or nothing: its chain compiled as a one-channel rig, each plugin
    named as a load names it (`processor_name`), with the copies to stereo
    (`ports`, the committed `plugin-ports.txt` unless given), so a plugin
    the port table lacks, the amp, a plugin with no output and a base name
    that becomes the compiler's own (`send`) are all said here, as `up`
    would say them. A name that clashes with another channel's is a
    question for the rig it is loaded into, and is the compiler's there."""
    from .rigfile import RigFileError, compile_rig, parse_rig

    raw = {
        "kind": "rig", "schema_version": 1, "name": "preset-check",
        "channels": [{"name": _PROBE_CHANNEL, "input": "probe-in", "width": preset.width,
                      "chain": loaded_chain(preset, _PROBE_CHANNEL)}],
        "master": {"output": "probe-out", "chain": []},
    }
    try:
        compile_rig(parse_rig(raw), ports=ports)
    except RigFileError as exc:
        prefix = f"channel {_PROBE_CHANNEL!r}: "
        return [p.replace(prefix, "", 1) for p in exc.problems]
    return []


def preset_path(root: Path, name: str) -> Path:
    """`config/channels/<name>.yaml` under checkout `root`, for a name the
    naming rule allows (`save.validate_name` raises `SaveError` otherwise):
    a name from a request becomes a file name only through this."""
    from .save import validate_name

    return Path(root) / PRESETS_DIR / f"{validate_name(name)}.yaml"


def preset_names(root: Path) -> list[str]:
    """Every preset file's name in `config/channels/`, in name order; only
    the folder itself, never deeper, and only names the naming rule allows."""
    folder = Path(root) / PRESETS_DIR
    if not folder.is_dir():
        return []
    return sorted(p.stem for p in folder.glob("*.yaml") if NAME_PATTERN.fullmatch(p.stem) and p.is_file()
                  and not p.is_symlink())


def listing(root: Path, ports: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Each preset of `config/channels/` as the page lists it: its name,
    file, width, level, plugins, the amp it was heard through and where it
    was saved from, or the problem that keeps it from being loaded (one that
    does not parse, or would not compile, is still listed, with why)."""
    out = []
    for name in preset_names(root):
        path = Path(root) / PRESETS_DIR / f"{name}.yaml"
        shown = (PRESETS_DIR / f"{name}.yaml").as_posix()
        try:
            preset = load_preset(path)
        except (OSError, UnicodeDecodeError, PresetError) as exc:
            problems = exc.problems if isinstance(exc, PresetError) else [f"{type(exc).__name__}: {exc}"]
            out.append({"name": name, "file": shown, "ok": False, "problem": "; ".join(problems)})
            continue
        problems = check_preset(preset, ports)
        out.append({
            "name": name, "file": shown, "ok": not problems, "problem": "; ".join(problems) or None,
            "width": preset.width, "level": preset.level, "plugins": preset.plugins,
            "description": preset.description or None, "heard_through_amp": preset.heard_through_amp,
            "saved_from": preset.saved_from,
        })
    return out


# --------------------------------------------------------------------------- #
# Making one, from a channel of a rig
# --------------------------------------------------------------------------- #

def from_channel(entry: dict[str, Any], name: str, *, heard_through_amp: str | None = None,
                 saved_from: dict[str, str] | None = None, description: str | None = None) -> dict[str, Any]:
    """A preset's document from `entry`, a channel of a rig file as
    `parse_rig` reads one (for Save channel as…, the channel as the running
    rig plays it: `rigfile.fold_state`'s). Its chain entries as written,
    each under its base name (`base_names`); its level when it has one; its
    width as the preferred width; never its input, its strip or a copy to
    stereo (a capture's copies are not in a folded chain, and any name
    ending as one is left out here too)."""
    channel = str(entry.get("name"))
    chain = [copy.deepcopy(e) for e in entry.get("chain") or []
             if isinstance(e, dict) and not stereo.is_copy(str(e.get("name")))]
    based = base_names(channel, [str(e.get("name")) for e in chain])
    for e in chain:
        e["name"] = based[str(e.get("name"))]
    out: dict[str, Any] = {"kind": KIND, "schema_version": SCHEMA_VERSION, "name": name}
    if description:
        out["description"] = description
    out["width"] = entry.get("width")
    level = entry.get("level", 0.0)
    if isinstance(level, (int, float)) and not isinstance(level, bool) and level != 0.0:
        out["level"] = level
    if heard_through_amp:
        out["heard_through_amp"] = heard_through_amp
    if saved_from:
        out["saved_from"] = dict(saved_from)
    out["chain"] = chain
    return out


def header(name: str, when: str, source: str | None) -> str:
    """The comment a new preset file starts with: what it is and where it
    came from, since the file is the record and the request is gone."""
    lines = [f"# Channel preset {name}" + (f": saved from {source} on the web UI, {when}." if source else "."),
             "# A channel's chain, settings and level, to load onto any input; it names no input. Loading",
             "# copies it into the rig file, so editing it later changes no rig. Not committed by",
             "# sushi-rig: that is yours."]
    return "\n".join(lines) + "\n\n"


def write_preset(path: Path, raw: dict[str, Any], *, header_text: str = "") -> None:
    """Write preset document `raw` to `path` by `write_rig`'s rules: a file
    already there is updated in place, its comments, blank lines and layout
    kept (yamlmerge.py); a file not there is written fresh under
    `header_text`. Nothing is written, and `PresetError` is raised, when
    `raw` is not a preset `parse_preset` accepts, or the file there cannot
    be updated without losing something, or the text would not read back as
    `raw`. Replaced whole or not at all, with its permissions."""
    from . import yamlmerge

    path = Path(path)
    parse_preset(copy.deepcopy(raw))
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    try:
        text = yamlmerge.merged(existing, raw) if existing.strip() else yamlmerge.fresh(raw, header_text)
    except yamlmerge.MergeError as exc:
        raise PresetError([f"{path}: {exc}"]) from exc
    path.parent.mkdir(parents=True, exist_ok=True)
    scratch = path.with_name(path.name + ".tmp")
    try:
        scratch.write_text(text, encoding="utf-8")
        if path.exists():
            shutil.copymode(path, scratch)
        os.replace(scratch, path)
    except BaseException:
        scratch.unlink(missing_ok=True)
        raise


# --------------------------------------------------------------------------- #
# Loading one onto a channel
# --------------------------------------------------------------------------- #

def loaded_chain(preset: Preset, channel: str) -> list[dict[str, Any]]:
    """The preset's chain as channel `channel` holds it: each entry copied,
    settings and all, under the name it has there (`processor_name`)."""
    out = []
    for entry in preset.chain:
        e = copy.deepcopy(entry)
        e["name"] = processor_name(channel, str(entry["name"]))
        out.append(e)
    return out


def loaded_channel(entry: dict[str, Any], preset: Preset) -> dict[str, Any]:
    """Channel `entry` of a rig file with the preset loaded onto it: its
    chain replaced by the preset's and its level set to the preset's (none
    written at unity, as the rig files write it); its name, input, width and
    strip its own. A copy: `entry` is left as it is."""
    out = copy.deepcopy(entry)
    out["chain"] = loaded_chain(preset, str(entry.get("name")))
    if preset.level:
        out["level"] = preset.level
    else:
        out.pop("level", None)
    return out


def channel_entry(name: str, input_role: str, width: str, preset: Preset) -> dict[str, Any]:
    """A new channel, as the rig files write one, holding the preset: for
    Add channel and New rig with a preset to start from (#125's machinery)."""
    entry: dict[str, Any] = {"name": name, "input": input_role, "width": width}
    if preset.level:
        entry["level"] = preset.level
    entry["chain"] = loaded_chain(preset, name)
    return entry


def _target_of(control: dict[str, Any]) -> tuple[str, str | None] | None:
    """`(binding, target)` of a MIDI control as the file writes it, for the
    two bindings that name a plugin (parameter, bypass); None otherwise."""
    for binding in ("parameter", "bypass"):
        target = control.get(binding)
        if isinstance(target, str):
            return binding, target
    return None


def midi_after_load(midi: Any, channel: str, old_chain: list[dict[str, Any]],
                    new_chain: list[dict[str, Any]]) -> tuple[Any, list[dict[str, Any]], list[dict[str, Any]]]:
    """`(midi, followed, orphaned)`: a rig file's `midi:` section once
    channel `channel`'s chain `old_chain` is replaced by `new_chain`. Pure;
    `midi` is left as it is.

    A control that binds a plugin of that channel (`parameter:
    channel/plugin/parameter`, `bypass: channel/plugin`) is kept where the
    new chain has the same plugin under the same name (same URI); it
    **follows** where the new chain has the same plugin (by URI) under the
    same base name, renamed (`followed`: `{"control", "from", "to"}`); and
    it is **orphaned** otherwise -- no plugin of that name, or one of that
    name that is another plugin, whose parameters are not known to match --
    (`orphaned`: `{"control", "binding", "target", "why"}`), and left in the
    returned section as it was, for the caller to refuse or drop
    (`drop_orphaned`). Blends and mutes name channels, which a load does not
    change, and are left alone."""
    if not isinstance(midi, dict) or not isinstance(midi.get("controls"), list):
        return copy.deepcopy(midi), [], []
    old = {e.get("name"): e for e in old_chain if isinstance(e, dict)}
    new = {e.get("name"): e for e in new_chain if isinstance(e, dict)}
    prefix = f"{channel}_"

    def base(name: str) -> str:
        return name[len(prefix):] if name.startswith(prefix) and len(name) > len(prefix) else name

    def which(entry: dict[str, Any] | None) -> Any:
        return None if entry is None else entry.get("uri") or entry.get("path") or entry.get("uid")

    out = copy.deepcopy(midi)
    followed, orphaned = [], []
    for control in out["controls"]:
        if not isinstance(control, dict):
            continue
        found = _target_of(control)
        if found is None:
            continue
        binding, target = found
        parts = target.split("/")
        if len(parts) < 2 or parts[0] != channel:
            continue
        plugin = parts[1]
        was = which(old.get(plugin))
        if plugin in new and which(new[plugin]) == was and was is not None:
            continue
        match = next((n for n, e in new.items() if isinstance(n, str) and base(n) == base(plugin)
                      and which(e) == was and was is not None), None)
        if match is not None:
            renamed = "/".join([channel, match, *parts[2:]])
            control[binding] = renamed
            followed.append({"control": control.get("control"), "from": target, "to": renamed})
            continue
        if plugin in new:
            why = (f"the preset's chain has a {plugin}, but it is another plugin ({which(new[plugin])}, "
                   f"not {was}), whose parameters are not known to match")
        else:
            why = f"the preset's chain has no {plugin} (nor the same plugin under another name)"
        orphaned.append({"control": control.get("control"), "binding": binding, "target": target, "why": why})
    return out, followed, orphaned


def drop_orphaned(midi: Any, orphaned: list[dict[str, Any]]) -> Any:
    """`midi` without the controls `orphaned` names. Pure. The section stays,
    with its controller and Program Changes, even with no control left."""
    if not orphaned or not isinstance(midi, dict) or not isinstance(midi.get("controls"), list):
        return copy.deepcopy(midi)
    gone = {o["control"] for o in orphaned}
    out = copy.deepcopy(midi)
    out["controls"] = [c for c in out["controls"] if not (isinstance(c, dict) and c.get("control") in gone)]
    return out


def mapping_said(o: dict[str, Any]) -> str:
    """One orphaned mapping in words: what it was, and why it would point at nothing."""
    return f"{o['control']} ({o['binding']} {o['target']}): {o['why']}"


def amp_note(preset: Preset, channel: str, input_role: str, rig_amp: str | None) -> dict[str, Any]:
    """Whether the amp is in front of `channel` once the preset is on it, and
    what to say: `{"in_front", "saved_with", "note"}`. The amp is in front of
    every channel on `guitar-in` when the rig has one (`rig_amp`, its model),
    and of nothing else. Said when the preset was heard through the amp and
    lands where it is not (a second guitar on another input, a rig with no
    amp), and when the amp is in front of a preset saved without it."""
    from .patchbay import GUITAR_ROLE

    in_front = bool(rig_amp) and input_role == GUITAR_ROLE
    saved = preset.heard_through_amp
    note = None
    if saved and not in_front:
        where = (f"{channel} plays from {input_role}, and the rig's one amp is patched in front of {GUITAR_ROLE} "
                 "only" if rig_amp else f"the rig {channel} is on has no amp")
        note = (f"{channel} has no amp: {preset.name} was saved with the amp ({Path(saved).stem}) in front of "
                f"it, and {where}. An amp per channel is not built")
    elif in_front and not saved:
        note = (f"the amp ({Path(str(rig_amp)).stem}) is in front of {channel}, on {GUITAR_ROLE}; "
                f"{preset.name} was saved without one")
    return {"in_front": in_front, "saved_with": saved, "note": note}
