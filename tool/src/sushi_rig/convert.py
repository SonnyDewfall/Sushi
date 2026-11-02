"""Today's tones as v2 rigs: a v1 config in, a rig file of channels out.

Issue #30. Every tone in `config/*.json` was made by hand-spelling Sushi tracks,
or by capturing a running rig into one. `convert` writes the same tone as a rig
file (rigfile.py) — channels, their chains and every setting — and checks its
own work before writing a byte: the rig it wrote, compiled, must hold every
plugin and every setting of the config it came from, in the same order
(`differences`). A conversion that cannot say that writes nothing.

What a v1 config does not say, the conversion has to be told, and does not
guess past what every tone here already is:

- **Which input each track plays from.** A v1 config names engine buses, not
  the roles below them. A single-track config is taken to be the guitar —
  `guitar-in`, mono — because every single-track tone here is played that way
  (each comes from `config/src/electric_board.yaml`, whose track takes
  `guitar-in` with `mono_to_stereo`). Anything with more than one track must be
  told, with `--input <track>=<role>:<width>`.
- **Nothing else.** A config carrying something a rig file has no place for —
  a track that is not a stereo track on engine bus 0, `midi`, `osc`, `events`,
  a setting on a processor that is not in the config — is refused by name,
  because dropping it would change the tone without a word.

The sound is checked separately, by rendering: `test_convert.py` null-tests
each converted rig against the reference its v1 config was calibrated to.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .patchbay import GUITAR_ROLE, MONITOR_ROLE
from .rigfile import (
    _CHANNEL_KEYS,
    _PLUGIN_KEYS, _SETTING_KEYS, KIND, MASTER, RETURN, SCHEMA_VERSION, SEND_SUFFIX,
    UNITY_STRIP, WIDTHS, emit_rig, parse_rig,
)

_TOP = ("_amp", "_meta", "host_config", "initial_state", "tracks")
_TRACK = ("name", "channels", "inputs", "outputs", "plugins")
_PLUGIN = _PLUGIN_KEYS
_STATE = ("processor",) + _SETTING_KEYS
# The one input shape a track can have for its input to go untold: a stereo
# bus 0, which every single-track tone here has and which the patchbay feeds
# from `guitar-in` with mono_to_stereo.
_DEFAULT_SHAPE = [{"engine_bus": 0, "track_bus": 0}]

DEFAULT_INPUT = (GUITAR_ROLE, "mono")


class ConvertError(ValueError):
    """A config that cannot be written as a rig file without losing something."""

    def __init__(self, problems: list[str]):
        self.problems = problems
        super().__init__("\n".join(problems))


@dataclass(frozen=True)
class Input:
    role: str
    width: str


def parse_inputs(texts: list[str]) -> dict[str, Input]:
    """Every --input, refusing one track given twice rather than keeping the last."""
    inputs: dict[str, Input] = {}
    for text in texts:
        track, given = parse_input(text)
        if track in inputs:
            raise ConvertError([f"--input gives track {track!r} twice"])
        inputs[track] = given
    return inputs


def parse_input(text: str) -> tuple[str, Input]:
    """`guitar=guitar-in:mono` as given on the command line."""
    track, _, rest = text.partition("=")
    role, _, width = rest.partition(":")
    if not track or not role or width not in WIDTHS:
        raise ConvertError([
            f"--input {text!r} must be <track>=<role>:<width>, width one of "
            f"{', '.join(WIDTHS)}"
        ])
    return track, Input(role, width)


def to_rig(config: dict[str, Any], name: str,
           inputs: dict[str, Input] | None = None) -> dict[str, Any]:
    """The rig file for `config`, as a document ready to write.

    Raises `ConvertError` naming everything the rig file could not hold.
    """
    problems: list[str] = []
    if not isinstance(config, dict):
        raise ConvertError([f"a config is a mapping, not {type(config).__name__}"])
    problems += [f"top-level {key!r} has no place in a rig file" for key in config if key not in _TOP]
    tracks = config.get("tracks")
    states = config.get("initial_state") or []
    malformed = (
        not isinstance(tracks, list) or not all(isinstance(t, dict) for t in tracks)
        or not all(isinstance(p, dict) for t in tracks for p in (t.get("plugins") or []))
        or not isinstance(states, list) or not all(isinstance(e, dict) for e in states)
    )
    if malformed:
        raise ConvertError(problems + [
            "tracks, their plugins and initial_state must be lists of mappings; this is "
            "not a Sushi config this tool wrote"
        ])
    inputs = dict(inputs or {})
    if not inputs and len(tracks) == 1:
        if tracks[0].get("inputs") == _DEFAULT_SHAPE:
            inputs = {tracks[0].get("name"): Input(*DEFAULT_INPUT)}
        else:
            # Anything else — two engine channels, a bus other than 0 — may be a
            # stereo source, and taking it for a mono guitar would downmix it.
            problems.append(
                f"track {tracks[0].get('name')!r}: inputs {tracks[0].get('inputs')!r} are "
                "not the guitar's stereo bus 0, so which input this is cannot be assumed; "
                f"give --input {tracks[0].get('name')}=<role>:<width>"
            )
    plugins_of: dict[str, str] = {}
    channels = []
    for track in tracks:
        tname = track.get("name")
        where = f"track {tname!r}"
        problems += [f"{where}: {key!r} has no place in a rig file" for key in track if key not in _TRACK]
        if track.get("channels") != 2:
            problems.append(f"{where}: a channel is a stereo track, and this has {track.get('channels')!r} channels")
        if track.get("outputs") != [{"engine_bus": 0, "track_bus": 0}]:
            problems.append(
                f"{where}: outputs {track.get('outputs')!r}, where every channel here "
                "sums onto engine bus 0 for the monitors; a rig's master does that now"
            )
        if tname not in inputs:
            problems.append(f"{where}: which input it plays from is not in the config; give --input {tname}=<role>:<width>")
        chain = []
        for plugin in track.get("plugins") or []:
            problems += [
                f"{where}: plugin {plugin.get('name')!r}: {key!r} has no place in a rig file"
                for key in plugin if key not in _PLUGIN
            ]
            entry = {k: plugin[k] for k in _PLUGIN if k in plugin}
            if entry.get("type") == "lv2":
                del entry["type"]  # the default, as in config/src
            chain.append(entry)
            plugins_of[plugin.get("name")] = tname
        channel = {"name": tname, "input": inputs[tname].role, "width": inputs[tname].width} \
            if tname in inputs else {"name": tname}
        channel["chain"] = chain
        channels.append(channel)
    meta_in = config.get("_meta")
    if meta_in is not None and not isinstance(meta_in, dict):
        problems.append(f"_meta must be a mapping (got {meta_in!r})")
    elif meta_in and meta_in.get("description") is not None and not isinstance(meta_in["description"], str):
        problems.append(f"_meta: description must be text (got {meta_in['description']!r})")
    if config.get("_amp") is not None and not isinstance(config["_amp"], dict):
        problems.append(f"_amp must be a mapping (got {config['_amp']!r})")
    unused = sorted(set(inputs) - {t.get("name") for t in tracks})
    problems += [f"--input names {t!r}, which is not a track in the config" for t in unused]

    by_name = {c["name"]: c for c in channels}
    for state in states:
        processor = state.get("processor")
        problems += [
            f"initial_state for {processor!r}: {key!r} has no place in a rig file"
            for key in state if key not in _STATE
        ]
        # Copied, never shared: a rig file built from views into the config
        # it came from changes when the config does, and the check that the
        # two agree would then always pass.
        settings = copy.deepcopy({k: state[k] for k in _STATE[1:] if k in state})
        if processor in by_name:
            # A track's own state is its strip: gain, pan and mute.
            if set(settings) - {"parameters"}:
                problems.append(f"track {processor!r}: only parameters are kept for a track's strip, got {sorted(settings)}")
            strip = settings.get("parameters")
            if not strip:
                # Not written as `strip: {}`, which a rig file refuses; and not
                # dropped unsaid either, since the config held an entry for it.
                problems.append(f"track {processor!r}: a strip with no gain, pan or mute cannot be kept")
            elif set(strip) - set(UNITY_STRIP):
                problems.append(
                    f"track {processor!r}: only gain, pan and mute are kept for a track's "
                    f"strip, got {sorted(set(strip) - set(UNITY_STRIP))}"
                )
            else:
                by_name[processor]["strip"] = strip
        elif processor in plugins_of:
            for entry in by_name[plugins_of[processor]]["chain"]:
                if entry["name"] == processor:
                    entry.update(settings)
        else:
            problems.append(f"initial_state for {processor!r}, which is not in the config")
    if problems:
        raise ConvertError(problems)

    meta = dict(config.get("_meta") or {})
    meta.pop("name", None)
    description = meta.pop("description", None)
    rig: dict[str, Any] = {"kind": KIND, "schema_version": SCHEMA_VERSION, "name": name}
    # Empty and absent are the same here and in `differences`: a rig file has no
    # way to say "an empty description", so neither side may count it.
    if description:
        rig["description"] = description
    if meta:
        rig["meta"] = meta
    if config.get("host_config"):
        rig["host"] = dict(config["host_config"])
    if config.get("_amp"):
        rig["amp"] = copy.deepcopy(config["_amp"])
    rig["channels"] = [
        {k: c[k] for k in _CHANNEL_KEYS if k in c} for c in channels
    ]
    rig["master"] = {"output": MONITOR_ROLE, "chain": []}
    return rig


# --------------------------------------------------------------------------- #
# Checking a conversion
# --------------------------------------------------------------------------- #

def differences(v1: dict[str, Any], compiled: dict[str, Any]) -> list[str]:
    """Everything the compiled rig holds differently from the config it replaces.

    Routing is expected to differ — that is the point — and so are the sends,
    the return and the master track the compiler adds. Everything else must
    match exactly: each track's plugins in order, every initial_state entry, the
    host, the amp and the config's name and description.
    """
    found = []
    tracks = {t["name"]: t for t in compiled.get("tracks", [])}
    v1_names = {t["name"] for t in v1.get("tracks", [])}
    for name in sorted(set(tracks) - v1_names - {MASTER}):
        found.append(f"track {name!r} is in the rig and not in the config")
    master = tracks.get(MASTER)
    if master is not None and [p["name"] for p in master["plugins"]] != [RETURN]:
        found.append(f"the master carries {[p['name'] for p in master['plugins'][1:]]!r}, which the config had no place for")
    for track in v1.get("tracks", []):
        got = tracks.get(track["name"])
        if got is None:
            found.append(f"track {track['name']!r} is not in the rig")
            continue
        plugins = [p for p in got["plugins"] if p["name"] != track["name"] + SEND_SUFFIX]
        if plugins != track["plugins"]:
            found.append(f"track {track['name']!r}: plugins {plugins!r}, was {track['plugins']!r}")
    # Both ways: a setting only the rig has is as much a different tone as one
    # it lost, and a config with no initial_state would otherwise let anything
    # be added to its rig unseen.
    # The sends the compiler added, by the names it gave them. Not by suffix: a
    # plugin the player called `delay_send` is theirs and must be held to the config.
    sends = {n + SEND_SUFFIX for n in tracks if n != MASTER}
    state = {e["processor"]: e for e in compiled.get("initial_state", [])
             if e["processor"] not in sends}
    was = {e["processor"]: e for e in v1.get("initial_state", [])}
    for name in sorted(set(state) | set(was)):
        if state.get(name) != was.get(name):
            found.append(f"initial_state for {name!r}: {state.get(name)!r}, was {was.get(name)!r}")
    # Empty and absent alike, as `to_rig` treats them: it writes no host, amp or
    # description for a falsy one, so a config holding `{}` or "" must not be
    # refused as a different tone for something the rig file cannot express.
    # Normalised into `held` for the comparison only; the message prints what the
    # config actually held.
    held = {key: v1.get(key) for key in ("host_config", "_amp", "_meta")}
    if isinstance(held["_meta"], dict) and not held["_meta"].get("description", True):
        held["_meta"] = {k: v for k, v in held["_meta"].items() if k != "description"}
    for key, value in held.items():
        if (value or None) != (compiled.get(key) or None):
            found.append(f"{key}: {compiled.get(key)!r}, was {v1.get(key)!r}")
    return found


def convert(config: dict[str, Any], name: str,
            inputs: dict[str, Input] | None = None) -> dict[str, Any]:
    """`to_rig`, refused unless the rig compiles back to everything `config` was."""
    rig = to_rig(config, name, inputs)
    expected = config
    if not config.get("_meta"):
        # No provenance to keep, but the rig is still named: that, and only
        # that, may be in `_meta` when the config had none.
        expected = {**config, "_meta": {"name": name}}
    elif config["_meta"].get("name") != name:
        # Converted under a name of its own: that, and only that, may differ.
        expected = {**config, "_meta": {**config["_meta"], "name": name}}
    found = differences(expected, emit_rig(parse_rig(rig)))
    if found:
        raise ConvertError(["the converted rig does not hold the same tone:"] + found)
    return rig
