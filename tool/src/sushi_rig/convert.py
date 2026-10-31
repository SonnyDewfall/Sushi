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

from .rigfile import KIND, SCHEMA_VERSION, SEND_SUFFIX, WIDTHS, emit_rig, parse_rig

_TOP = ("_amp", "_meta", "host_config", "initial_state", "tracks")
_TRACK = ("name", "channels", "inputs", "outputs", "plugins")
_PLUGIN = ("name", "type", "uri", "path", "uid")
_STATE = ("processor", "bypassed", "program", "parameters", "properties")

DEFAULT_INPUT = ("guitar-in", "mono")
MONITORS = "monitor-out"


class ConvertError(ValueError):
    """A config that cannot be written as a rig file without losing something."""

    def __init__(self, problems: list[str]):
        self.problems = problems
        super().__init__("\n".join(problems))


@dataclass(frozen=True)
class Input:
    role: str
    width: str


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
    tracks = config.get("tracks") or []
    inputs = dict(inputs or {})
    if not inputs and len(tracks) == 1:
        inputs = {tracks[0].get("name"): Input(*DEFAULT_INPUT)}
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
    unused = sorted(set(inputs) - {t.get("name") for t in tracks})
    problems += [f"--input names {t!r}, which is not a track in the config" for t in unused]

    by_name = {c["name"]: c for c in channels}
    for state in config.get("initial_state") or []:
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
            by_name[processor]["strip"] = settings.get("parameters", {})
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
    if description:
        rig["description"] = description
    if meta:
        rig["meta"] = meta
    if config.get("host_config"):
        rig["host"] = dict(config["host_config"])
    if config.get("_amp"):
        rig["amp"] = copy.deepcopy(config["_amp"])
    rig["channels"] = [
        {k: c[k] for k in ("name", "input", "width", "strip", "chain") if k in c} for c in channels
    ]
    rig["master"] = {"output": MONITORS, "chain": []}
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
    for track in v1.get("tracks", []):
        got = tracks.get(track["name"])
        if got is None:
            found.append(f"track {track['name']!r} is not in the rig")
            continue
        plugins = [p for p in got["plugins"] if p["name"] != track["name"] + SEND_SUFFIX]
        if plugins != track["plugins"]:
            found.append(f"track {track['name']!r}: plugins {plugins!r}, was {track['plugins']!r}")
    state = {e["processor"]: e for e in compiled.get("initial_state", [])}
    for entry in v1.get("initial_state", []):
        if state.get(entry["processor"]) != entry:
            found.append(
                f"initial_state for {entry['processor']!r}: {state.get(entry['processor'])!r}, was {entry!r}"
            )
    for key in ("host_config", "_amp"):
        if v1.get(key) != compiled.get(key):
            found.append(f"{key}: {compiled.get(key)!r}, was {v1.get(key)!r}")
    for key in ("name", "description"):
        if (v1.get("_meta") or {}).get(key) != (compiled.get("_meta") or {}).get(key):
            found.append(f"_meta {key}: {(compiled.get('_meta') or {}).get(key)!r}, was {(v1.get('_meta') or {}).get(key)!r}")
    return found


def convert(config: dict[str, Any], name: str,
            inputs: dict[str, Input] | None = None) -> dict[str, Any]:
    """`to_rig`, refused unless the rig compiles back to everything `config` was."""
    rig = to_rig(config, name, inputs)
    found = differences(config, emit_rig(parse_rig(rig)))
    if found:
        raise ConvertError(["the converted rig does not hold the same tone:"] + found)
    return rig


def render_rig_file(rig: dict[str, Any], source: str) -> str:
    """The rig as YAML, with a header saying where it came from."""
    import yaml

    header = (
        f"# Converted from {source} by `sushi-rig convert`, and checked to compile\n"
        "# back to every plugin and setting it held. Edit this file, not that one.\n\n"
    )
    return header + yaml.safe_dump(rig, sort_keys=False, allow_unicode=True, width=100)
