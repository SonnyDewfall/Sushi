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
        # Listing what is actually there turns "that file is missing" into
        # "here is the name you meant" — models are renamed and replaced far
        # more often than configs are edited.
        library = Path(root) / "amp" / "models"
        available = sorted(p.name for p in library.glob("*.nam")) if library.is_dir() else []
        found = (
            "\n\nModels in amp/models/:\n  " + "\n  ".join(available)
            if available else "\n\nThere are no models in amp/models/ at all."
        )
        raise AmpError(
            f"amp model not found: {path}"
            f"{found}\n\n"
            "Point the config's _amp.model at one of these, or start without "
            "the amp:\n  sushi-rig up <config> --no-amp"
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

    # How many inference paths the model actually has. A control that picks
    # between them is meaningless on a model that only has one, so the panel is
    # told the count rather than assuming two. Best effort: an unreadable model
    # is a problem for `up` to report, not for the panel to fail on.
    tiers = 0
    try:
        tiers = len(describe_model(Path(config_path).parent.parent / described["model"])["tiers"])
    except (AmpError, OSError, ValueError, IndexError):
        try:
            tiers = len(describe_model(described["model"])["tiers"])
        except Exception:  # noqa: BLE001 - genuinely optional
            tiers = 0

    return {
        "model_name": Path(described["model"]).stem,
        "parameters": dict(described.get("parameters") or {}),
        "tiers": tiers,
    }


# The JACK client and ports Carla gives the amp. "NAM" is the <Name> in the
# generated project, and Carla names its ports after it.
AMP_NODE = "NAM"
AMP_INPUT = "NAM:Input"
AMP_OUTPUT = "NAM:Output"

_PATCHBAY_ITEM = (
    '  <item node-type="pipewire" port-type="pipewire-audio">\n'
    '   <output node="{out_node}" port="{out_port}"/>\n'
    '   <input node="{in_node}" port="{in_port}"/>\n'
    '  </item>\n'
)


def amp_patchbay(base_xml: str, sushi_node: str = "sushi") -> str:
    """The base patchbay, rewired to put the amp in front of the guitar.

    Whatever feeds `sushi:audio_input_0` is taken to be the guitar. That source
    is redirected into the amp, the amp feeds the inputs it used to feed, and
    everything else is left exactly as it was — Sushi's outputs, the tuner's dry
    feed, and any other source feeding other inputs.

    Deliberately *not* "redirect everything that feeds Sushi". The saved
    patchbay also wires a second interface channel into Sushi's unused inputs
    2 and 3, and NAM is mono — so that rule summed two physical inputs into one
    amp. Caught by generating it and reading the result.

    Derived from the saved patchbay rather than kept as a second file, so it
    cannot drift out of step with it. The saved file stays the one thing to edit
    when routing changes.

    Two files are needed rather than one because qpwgraph *maintains* its saved
    patch: it reconnects anything missing, so a single file holding both the
    direct and the through-the-amp routes would keep both live and you would
    hear the dry guitar under the amped one. Confirmed the hard way — qpwgraph
    repeatedly undid a by-hand rewiring during testing.
    """
    import re

    item_re = re.compile(
        r'[ \t]*<item[^>]*>\s*'
        r'<output node="([^"]*)" port="([^"]*)"/>\s*'
        r'<input node="([^"]*)" port="([^"]*)"/>\s*'
        r'</item>\s*',
        re.S,
    )

    first_input = f"{sushi_node}:audio_input_0"
    guitar: tuple[str, str] | None = None
    for match in item_re.finditer(base_xml):
        out_node, out_port, _in_node, in_port = match.groups()
        if in_port == first_input:
            guitar = (out_node, out_port)
            break
    if guitar is None:
        # Nothing feeds Sushi's first input, so there is nothing to put an amp
        # in front of. Better an unchanged patchbay than a guessed one.
        return base_xml

    def redirect(match: re.Match) -> str:
        out_node, out_port, in_node, in_port = match.groups()
        if (out_node, out_port) == guitar and in_port.startswith(
            f"{sushi_node}:audio_input_"
        ):
            return _PATCHBAY_ITEM.format(
                out_node=AMP_NODE, out_port=AMP_OUTPUT,
                in_node=in_node, in_port=in_port,
            )
        return match.group(0)

    rewired = item_re.sub(redirect, base_xml)
    feed = _PATCHBAY_ITEM.format(
        out_node=guitar[0], out_port=guitar[1],
        in_node=AMP_NODE, in_port=AMP_INPUT,
    )
    return rewired.replace("</items>", feed + "</items>", 1)


# --- reading a .nam model ---------------------------------------------------

# The rig runs at this rate, and NAM does no resampling: a model trained at a
# different rate plays at the wrong pitch and speed with nothing to warn you.
RIG_SAMPLE_RATE = 48000

# NAM A2. Not one model but a container holding several, and the plugin's
# `Quality` control picks which one runs — below 0.5 the first, above it the
# last. An A1 model names its architecture directly ("WaveNet", "LSTM") and has
# a single set of weights, so `Quality` does nothing at all for it.
SLIMMABLE = "SlimmableContainer"


def describe_model(path: Path | str) -> dict[str, Any]:
    """What a `.nam` file declares about itself.

    A `.nam` is plain JSON, and it carries everything worth knowing before
    loading it: the architecture, the sample rate it was trained at, what was
    modelled, and — for A2 — the separate quality tiers inside it.
    """
    path = Path(path)
    try:
        model = json.loads(path.read_text())
    except OSError as exc:
        raise AmpError(f"cannot read {path}: {exc}") from exc
    except ValueError as exc:
        raise AmpError(
            f"{path.name} is not valid JSON, so it is not a .nam model: {exc}"
        ) from exc

    if not isinstance(model, dict) or "architecture" not in model:
        raise AmpError(f"{path.name} has no 'architecture' — not a NAM model file")

    meta = model.get("metadata") or {}
    architecture = model["architecture"]
    tiers = []
    if architecture == SLIMMABLE:
        for index, submodel in enumerate(model.get("config", {}).get("submodels", [])):
            inner = submodel.get("model", {})
            layers = inner.get("config", {}).get("layers", []) or []
            tiers.append({
                "index": index,
                "architecture": inner.get("architecture", "?"),
                "channels": [layer.get("channels") for layer in layers],
                "weights": len(inner.get("weights") or []),
            })
    else:
        layers = model.get("config", {}).get("layers", []) or []
        tiers.append({
            "index": 0,
            "architecture": architecture,
            "channels": [layer.get("channels") for layer in layers],
            "weights": len(model.get("weights") or []),
        })

    try:
        rate = int(float(model.get("sample_rate", 0))) or None
    except (TypeError, ValueError):
        rate = None

    return {
        "name": meta.get("name") or path.stem,
        "path": str(path),
        "architecture": architecture,
        "slimmable": architecture == SLIMMABLE,
        "sample_rate": rate,
        "gear_make": meta.get("gear_make"),
        "gear_type": meta.get("gear_type"),
        "modeled_by": meta.get("modeled_by"),
        "trainer": meta.get("trainer"),
        "tiers": tiers,
    }


def quality_hint(tier_count: int, index: int) -> str:
    """Which `Quality` setting selects a tier."""
    if tier_count < 2:
        return "Quality has no effect — this model has a single path"
    if index == 0:
        return "Quality < 0.5"
    if index == tier_count - 1:
        return "Quality > 0.5"
    return "intermediate"


def summarise_model(described: dict[str, Any], rig_rate: int = RIG_SAMPLE_RATE) -> str:
    """`describe_model` as something to read."""
    lines = [f"{described['name']}  ({described['path']})"]
    gear = " / ".join(x for x in (described["gear_make"], described["gear_type"]) if x)
    if gear:
        lines.append(f"  gear      {gear}")
    by = " / ".join(x for x in (described["modeled_by"], described["trainer"]) if x)
    if by:
        lines.append(f"  by        {by}")
    lines.append(
        f"  format    {described['architecture']}"
        + ("  (NAM A2 — Quality selects a tier)" if described["slimmable"] else "")
    )

    rate = described["sample_rate"]
    if rate is None:
        lines.append("  rate      not declared")
    elif rate != rig_rate:
        lines.append(
            f"  rate      {rate} Hz  ** MISMATCH: the rig runs at {rig_rate} Hz **\n"
            "            NAM does no resampling, so this will play at the wrong "
            "pitch and speed."
        )
    else:
        lines.append(f"  rate      {rate} Hz  (matches the rig)")

    tiers = described["tiers"]
    lines.append(f"  {len(tiers)} path(s):")
    for tier in tiers:
        channels = ",".join(str(c) for c in tier["channels"] if c is not None) or "?"
        lines.append(
            f"    [{tier['index']}] {tier['architecture']:8} "
            f"channels={channels:6} {tier['weights']:>8,} weights"
            f"   {quality_hint(len(tiers), tier['index'])}"
        )
    if len(tiers) > 1:
        heaviest = max(t["weights"] for t in tiers)
        lightest = min(t["weights"] for t in tiers)
        if lightest:
            lines.append(
                f"\n  The full path has {heaviest / lightest:.1f}x the parameters of "
                "the lite one.\n  Compare `channels` against other models to judge "
                "relative cost; measure with `sushi-rig top`."
            )
    return "\n".join(lines)
