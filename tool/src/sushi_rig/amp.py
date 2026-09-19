"""The neural amp, which runs outside Sushi — and the config that describes it.

Sushi cannot host this part of the rig. NAM sets its model through a
`patch:writable` / `atom:Path` file parameter, and Sushi's LV2 wrapper does not
bridge those — verified directly against a running Sushi, where an internal
plugin reported its `file` property and an LV2 plugin with the identical kind of
parameter reported none at all. Elk confirm it (forum, January 2025), and NAM's
own documentation says the same: setting the model "requires that your LV2 host
supports atom:Path parameters".

So the amp runs in Carla instead, as its own JACK client, and this module is
everything the rest of the tool needs to know about that arrangement.

Two things are worth stating plainly, because they are the cost of it:

The model is selected by *which Carla project is loaded*, not by any live call.
Changing model means rewriting the project and restarting Carla.

Parameter values here are what we last **sent**, not what Carla currently holds.
Carla's OSC is write-only from where we sit — it has a feedback channel for
registered clients, but that is a much larger thing to implement. On the Sushi
side `capture` asks the rig what it is actually doing; here we can only report
what we asked for. The two agree as long as the panel is the only thing touching
Carla, which it is, but they are not the same guarantee and should not be
described as if they were.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

NAM_URI = "http://github.com/mikeoliphant/neural-amp-modeler-lv2"
MODEL_PROPERTY = NAM_URI + "#model"

# Carla's own OSC server. The address scheme is
# `/Carla/<plugin>/set_parameter_value <index> <value>`, and plugins are
# numbered from 0 — established by asking: id 1 came back "invalid plugin id",
# id 0 was accepted silently.
CARLA_OSC_PORT = 22752
CARLA_PLUGIN_ID = 0

# Carla numbers parameters over a plugin's *control ports only*, in port order.
# NAM's LV2 port indices are 4, 5 and 6 (0-3 being audio and atom ports), so
# these are deliberately not the same numbers.
PARAMETER_INDEX = {"Input Lvl": 0, "Output Lvl": 1, "Quality": 2}

PARAMETER_RANGE = {
    "Input Lvl": (-20.0, 20.0),
    "Output Lvl": (-20.0, 20.0),
    "Quality": (0.0, 1.0),
}

PARAMETER_UNIT = {"Input Lvl": "dB", "Output Lvl": "dB", "Quality": ""}

DEFAULTS: dict[str, float] = {"Input Lvl": 0.0, "Output Lvl": 0.0, "Quality": 1.0}

# Quality is deliberately absent: it selects between a lite and a full inference
# path (above 0.5 is full), so it trades CPU against accuracy rather than
# shaping tone. It is still saved and restored, just not put on a fader next to
# controls that do change the sound.
PANEL_PARAMETERS = ("Input Lvl", "Output Lvl")

CONFIG_KEY = "_amp"


class AmpError(Exception):
    """Something about the amp the user needs to fix."""


def carla_project(model_path: Path | str) -> str:
    """A Carla project loading NAM with `model_path` already selected.

    The model lives in a `CustomData` entry typed `atom:Path`, which is how
    Carla persists an LV2 plugin's patch state. Generated rather than kept as a
    file, because the whole point is that the model changes per config.

    The path is written absolute: Carla resolves it at load time with no
    reference to where the project file sits.
    """
    return (
        "<?xml version='1.0' encoding='UTF-8'?>\n"
        "<!DOCTYPE CARLA-PROJECT>\n"
        "<CARLA-PROJECT VERSION='2.0'>\n"
        " <Plugin>\n"
        "  <Info>\n"
        "   <Type>LV2</Type>\n"
        "   <Name>NAM</Name>\n"
        f"   <URI>{NAM_URI}</URI>\n"
        "  </Info>\n"
        "  <Data>\n"
        "   <Active>Yes</Active>\n"
        "   <CustomData>\n"
        "    <Type>http://lv2plug.in/ns/ext/atom#Path</Type>\n"
        f"    <Key>{MODEL_PROPERTY}</Key>\n"
        f"    <Value>{escape(str(model_path))}</Value>\n"
        "   </CustomData>\n"
        "  </Data>\n"
        " </Plugin>\n"
        "</CARLA-PROJECT>\n"
    )


def read_amp(config: dict[str, Any]) -> dict[str, Any] | None:
    """The `_amp` section of a Sushi config, if it has one.

    Sushi ignores unknown top-level keys — checked by loading a config carrying
    this one, which it accepted without comment — so the amp can travel in the
    same file as the rig it belongs to rather than in a sidecar. That is the
    whole point: one file, one save, one thing to copy.
    """
    amp = config.get(CONFIG_KEY)
    return amp if isinstance(amp, dict) else None


def write_amp(
    config: dict[str, Any], model: str | None, parameters: dict[str, float]
) -> dict[str, Any]:
    """Put the amp into a config, or take it out when there is no amp."""
    if model is None:
        config.pop(CONFIG_KEY, None)
        return config
    config[CONFIG_KEY] = {
        "plugin": "nam",
        "model": model,
        "parameters": {
            name: round(float(parameters.get(name, DEFAULTS[name])), 4)
            for name in DEFAULTS
        },
    }
    return config


def relative_model(model_path: Path, root: Path) -> str:
    """A model path stored relative to the checkout where it can be.

    An absolute path bakes in one machine's home directory, which is the
    portability problem issue #6 exists for. Paths outside the checkout are kept
    absolute rather than turned into a chain of `..` that would break anyway.
    """
    try:
        return str(Path(model_path).resolve().relative_to(Path(root).resolve()))
    except ValueError:
        return str(model_path)


def resolve_model(model: str, root: Path) -> Path:
    """Turn a stored model path back into something Carla can open.

    Raises rather than returning a missing path: a Carla started with a model
    that is not there comes up looking perfectly healthy and processes silence,
    which is the single most confusing failure this rig can produce.
    """
    path = Path(model)
    if not path.is_absolute():
        path = Path(root) / path
    if not path.is_file():
        raise AmpError(
            f"amp model not found: {path}\n"
            "The config names a model that is not on this machine. Put it in "
            "amp/models/ or change the config."
        )
    return path


def clamp(name: str, value: float) -> float:
    """Keep a parameter inside what the plugin declares.

    Carla does its own clamping, but doing it here means the value we remember
    for saving is the value that actually took effect.
    """
    low, high = PARAMETER_RANGE.get(name, (float("-inf"), float("inf")))
    return max(low, min(high, float(value)))


def parameter_message(name: str, value: float) -> tuple[str, list[Any]]:
    """The Carla OSC message that sets `name`, ready to send.

    Pure, so the index mapping and the argument shape are pinned by a test
    rather than discovered by ear against a running amp.
    """
    if name not in PARAMETER_INDEX:
        raise AmpError(f"unknown amp parameter {name!r}")
    return (
        f"/Carla/{CARLA_PLUGIN_ID}/set_parameter_value",
        [PARAMETER_INDEX[name], clamp(name, value)],
    )


def amp_from_config_file(config_path: Path) -> dict[str, Any] | None:
    try:
        return read_amp(json.loads(Path(config_path).read_text()))
    except (OSError, ValueError):
        return None


def panel_amp(config_path: Path) -> dict[str, Any] | None:
    """What the panel needs to draw an amp tab, from a config's `_amp`.

    Returns None when the config describes no amp, which is the signal for the
    panel to have no amp tab at all rather than an empty one.
    """
    described = amp_from_config_file(config_path)
    if not described or not described.get("model"):
        return None
    return {
        "model_name": Path(described["model"]).stem,
        "parameters": dict(described.get("parameters") or {}),
    }
