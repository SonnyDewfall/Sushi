"""A channel of one rig loaded onto a channel of another (#208): its chain,
settings and bypass, and its level, copied from the rig file in
`config/rigs/` as it stands, onto a channel of the running rig. This was
channel presets' job (#127); a preset is now simply a channel of any rig.

What is copied is the source channel's `chain` and `level`. Never its
input, width or strip, which stay the target channel's own, and never the
source rig's MIDI map, which names that rig's channels and belongs to it.
Everything here is pure over dicts as YAML reads them.

## Names

Sushi's processor names are global, so the copy is named for the channel it
lands on (review of #162). Each plugin's **base name** is its name without
the source channel's prefix (`strings_hall` on `strings` is `hall`,
`base_names`), and the copy is called `<target>_<base>` (`processor_name`),
always, so two base names never become one name (`guitar_eq` beside `eq`
becomes `guitar_guitar_eq` beside `guitar_eq`). Whether the names that
result are acceptable -- unique across the rig, not the compiler's own
(`<channel>_send`, `_to_stereo`) -- is the compiler's to say when the
edited rig is compiled, as for any rig file.

## The amp

The amp is one instance, in Carla, patched in front of `guitar-in`
(`amp.amp_patchbay`). A source channel on `guitar-in` of a rig with an amp
was heard through it (`heard_through_amp`, the model), so loading it where no
amp is in front says so (`amp_note`), rather than be found out by ear.

## MIDI

The target rig's MIDI map names plugins as `channel/plugin`. Loading replaces
the target channel's chain, so a mapping onto its old plugins either follows
(the new chain has the same plugin, by URI) or would point at nothing, which
the agent refuses unless it is dropped (`midi_after_load`).
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import stereo
from .save import NAME_PATTERN


@dataclass(frozen=True)
class Source:
    """Channel `channel` of rig `rig` as a load copies it: its chain under
    base names (`base_names`), settings and bypass as written, its level,
    its width and input (said, never copied), and the amp it was heard
    through in its rig, if any."""

    rig: str
    channel: str
    width: str
    input: str
    chain: tuple[dict[str, Any], ...]
    level: float = 0.0
    heard_through_amp: str | None = None

    @property
    def name(self) -> str:
        """How the page and the answers name it: `rig / channel`."""
        return f"{self.rig} / {self.channel}"

    @property
    def plugins(self) -> list[str]:
        return [entry["name"] for entry in self.chain]


# --------------------------------------------------------------------------- #
# Names
# --------------------------------------------------------------------------- #

def processor_name(channel: str, base: str) -> str:
    """What base name `base` is called on channel `channel`: the channel's
    name, `_`, and the base. Always both, so distinct base names give
    distinct names (one-to-one by construction)."""
    return f"{channel}_{base}"


def base_names(channel: str, names: list[str]) -> dict[str, str]:
    """Each of a channel's plugin names without `<channel>_` where it has
    that prefix. If taking the prefixes off would make two names one
    (`guitar_eq` and `eq`), none is taken off, since the names are then the
    file's own and stay unique."""
    prefix = f"{channel}_"
    based = {name: name[len(prefix):] if name.startswith(prefix) and len(name) > len(prefix) else name
             for name in names}
    if len(set(based.values())) != len(based):
        return {name: name for name in names}
    return based


# --------------------------------------------------------------------------- #
# The source
# --------------------------------------------------------------------------- #

def source_of(raw: Any, rig: str, channel: str) -> Source:
    """Channel `channel` of rig file `raw` (rig `rig`) as a load copies it,
    or `LookupError` saying why when the rig has no such channel. A copy to
    stereo is never in a rig file; a name ending as one is left out all the
    same."""
    from .patchbay import GUITAR_ROLE

    channels = raw.get("channels") if isinstance(raw, dict) else None
    entry = next((c for c in channels if isinstance(c, dict) and c.get("name") == channel), None) \
        if isinstance(channels, list) else None
    if entry is None:
        names = [c.get("name") for c in channels or [] if isinstance(c, dict)]
        raise LookupError(f"{rig} has no channel called {channel} (it has: {', '.join(map(str, names)) or 'none'})")
    chain = [copy.deepcopy(e) for e in entry.get("chain") or []
             if isinstance(e, dict) and not stereo.is_copy(str(e.get("name")))]
    based = base_names(channel, [str(e.get("name")) for e in chain])
    for e in chain:
        e["name"] = based[str(e.get("name"))]
    level = entry.get("level", 0.0)
    if not isinstance(level, (int, float)) or isinstance(level, bool):
        level = 0.0
    amp = raw.get("amp") if isinstance(raw.get("amp"), dict) else None
    model = amp.get("model") if amp else None
    heard = model if isinstance(model, str) and model and entry.get("input") == GUITAR_ROLE else None
    return Source(rig, channel, str(entry.get("width")), str(entry.get("input")), tuple(chain), float(level), heard)


# --------------------------------------------------------------------------- #
# Loading one onto a channel
# --------------------------------------------------------------------------- #

def loaded_chain(source: Source, channel: str) -> list[dict[str, Any]]:
    """The source's chain as channel `channel` holds it: each entry copied,
    settings and all, under the name it has there (`processor_name`)."""
    out = []
    for entry in source.chain:
        e = copy.deepcopy(entry)
        e["name"] = processor_name(channel, str(entry["name"]))
        out.append(e)
    return out


def name_problems(channel: str, source: Source) -> list[str]:
    """Why a name a load would make on `channel` cannot be a processor's:
    each must match `save.NAME_PATTERN`, as every name a rig saves does (a
    long channel name and a long base name can make one past 64
    characters). Uniqueness across the rig is the compiler's to say."""
    return [f"{channel}: {name!r} would be the name of {entry['name']} there, and a processor's name must match "
            f"{NAME_PATTERN.pattern}" for entry in source.chain
            for name in [processor_name(channel, str(entry["name"]))] if not NAME_PATTERN.fullmatch(name)]


def loaded_channel(entry: dict[str, Any], source: Source) -> dict[str, Any]:
    """Channel `entry` of a rig file with the source loaded onto it: its
    chain replaced by the source's and its level set to the source's (none
    written at unity, as the rig files write it); its name, input, width and
    strip its own. A copy: `entry` is left as it is."""
    out = copy.deepcopy(entry)
    out["chain"] = loaded_chain(source, str(entry.get("name")))
    if source.level:
        out["level"] = source.level
    else:
        out.pop("level", None)
    return out


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
    channel/plugin/parameter`, `bypass: channel/plugin`) **follows its
    plugin**, known by what it is (its URI), never by its name alone, since a
    load renames and two plugins can swap names (review of #162):

    1. kept where the new chain has the same plugin under the same name;
    2. else moved to the one plugin of the new chain with that URI, when
       only one has it (`followed`: `{"control", "from", "to"}`);
    3. else, among several of that URI, to the one under the same base name,
       or failing that to the one in the same place among them (the second
       delay to the second delay);
    4. else **orphaned** (`{"control", "binding", "target", "why"}`, the why
       naming what the new chain holds there), and left in the returned
       section as it was, for the caller to refuse or drop
       (`drop_orphaned`).

    Blends and mutes name channels, which a load does not change, and are
    left alone."""
    if not isinstance(midi, dict) or not isinstance(midi.get("controls"), list):
        return copy.deepcopy(midi), [], []
    old = {e.get("name"): e for e in old_chain if isinstance(e, dict)}
    new = {e.get("name"): e for e in new_chain if isinstance(e, dict) and isinstance(e.get("name"), str)}
    prefix = f"{channel}_"

    def base(name: str) -> str:
        return name[len(prefix):] if name.startswith(prefix) and len(name) > len(prefix) else name

    def which(entry: dict[str, Any] | None) -> Any:
        return None if entry is None else entry.get("uri") or entry.get("path") or entry.get("uid")

    def same_plugin(plugin: str) -> str | None:
        was = which(old.get(plugin))
        if was is None:
            return None
        if plugin in new and which(new[plugin]) == was:
            return plugin
        alike = [n for n, e in new.items() if which(e) == was]
        if len(alike) == 1:
            return alike[0]
        named = [n for n in alike if base(n) == base(plugin) or base(n) == plugin]
        if len(named) == 1:
            return named[0]
        olds = [n for n, e in old.items() if which(e) == was]
        place = olds.index(plugin) if plugin in olds else None
        return alike[place] if place is not None and place < len(alike) else None

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
        match = same_plugin(plugin)
        if match == plugin:
            continue
        if match is not None:
            renamed = "/".join([channel, match, *parts[2:]])
            control[binding] = renamed
            followed.append({"control": control.get("control"), "from": target, "to": renamed})
            continue
        was = which(old.get(plugin))
        there = next((n for n in new if n == plugin or base(n) == base(plugin)), None)
        if there is not None:
            why = (f"the loaded chain has {there} in its place, but it is another plugin ({which(new[there])}, "
                   f"not {was}), whose parameters are not known to match")
        else:
            held = ", ".join(new) or "nothing"
            why = f"the loaded chain has no plugin like {plugin} ({was}): it holds {held}"
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


def amp_note(source: Source, channel: str, input_role: str, rig_amp: str | None) -> dict[str, Any]:
    """Whether the amp is in front of `channel` once the source is on it, and
    what to say: `{"in_front", "heard_with", "note"}`. The amp is in front of
    every channel on `guitar-in` when the rig has one (`rig_amp`, its model),
    and of nothing else. Said when the source was heard through the amp in
    its rig and lands where it is not, and when the amp is in front of a
    source that had none."""
    from .patchbay import GUITAR_ROLE

    in_front = bool(rig_amp) and input_role == GUITAR_ROLE
    heard = source.heard_through_amp
    note = None
    if heard and not in_front:
        where = (f"{channel} plays from {input_role}, and the amp is in front of {GUITAR_ROLE} only"
                 if rig_amp else "this rig has no amp")
        note = f"{channel} has no amp: {source.name} plays through one ({Path(heard).stem}), and {where}"
    elif in_front and not heard:
        note = f"the amp ({Path(str(rig_amp)).stem}) is in front of {channel}; {source.name} has none in its rig"
    return {"in_front": in_front, "heard_with": heard, "note": note}
