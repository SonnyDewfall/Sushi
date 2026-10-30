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
import sys
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

# The amp's LV2 URI by the short name a config records it under.
#
# `_amp` names the plugin as `"plugin": "nam"` and carries no URI, because
# nothing on the Sushi side ever needed one — Sushi does not host the amp. But
# Carla resolves it through the *same* `LV2_PATH` the supervisor sets for every
# child, so the amp is as much a part of the rig's LV2 tree as anything in
# `tracks`, and `plugins check` has to be able to see it. Exported as a mapping
# rather than a bare constant so an unrecognised name is something the caller
# can notice rather than silently treat as "no amp".
PLUGIN_URIS: dict[str, str] = {"nam": NAM_URI}


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


def panel_amp(config_path: Path, root: Path | None = None) -> dict[str, Any] | None:
    """What the panel needs to draw an amp tab, from a config's `_amp`.

    Returns None when the config describes no amp, which is the signal for the
    panel to have no amp tab at all rather than an empty one.

    `root` is where a relative model path resolves from. It is optional only so
    that callers without one still get a usable tab — but then the model cannot
    be read, so the Quality control is left off rather than guessed at, and the
    reason is said out loud. An earlier version guessed the root from the config
    path and swallowed every failure, which meant an unreadable model made that
    control vanish with no explanation at all.
    """
    described = amp_from_config_file(config_path)
    if not described or not described.get("model"):
        return None

    # How many inference paths the model has. A control that picks between them
    # is meaningless on a model that only has one, so the panel is told the
    # count rather than assuming two.
    tiers = 0
    if root is not None:
        try:
            tiers = len(describe_model(resolve_model(described["model"], root))["tiers"])
        except AmpError as exc:
            # Not fatal — the panel is still worth drawing without the Quality
            # control. But it must not be silent: `up` refuses to start on this,
            # so a panel generated from a config that cannot start should say so.
            print(f"warning: amp model unreadable, Quality control omitted — {exc}",
                  file=sys.stderr)

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


def amp_patchbay(
    base_xml: str, guitar_port: str | None = None, sushi_node: str = "sushi"
) -> str:
    """The base patchbay, rewired to put the amp in front of the guitar.

    `guitar_port` is the physical port the machine file gives the guitar role.
    Whatever feeds it into Sushi is redirected through the amp; the amp feeds the
    inputs that port used to feed, and everything else is left exactly as it was
    — Sushi's outputs, the tuner's tap, and every other source.

    **Why a role and not a port index.** This used to take whatever fed
    `sushi:audio_input_0` to be the guitar, which was true only while the rig
    had one channel. With a microphone on a second channel, "input 0" is a
    position, and positions move: put the vocal track on bus 0 and the guitar
    on bus 1 and the amp would be reprofiling the singer. Issue #18's whole
    point is that the amp, the tuner, the panel and the recorder all key off
    the same declared channel.

    With no `guitar_port` the old positional rule still applies, because a rig
    with no machine file must keep working. `prepare` says so out loud when it
    falls back.

    Deliberately *not* "redirect everything that feeds Sushi". Sushi's inputs
    2 and 3 carry the vocal channel, and NAM is mono — so that rule summed two
    physical inputs into one amp, and would now be amping the singer alongside
    the guitar. Caught by generating it and reading the result, back when those
    inputs were spare.

    Every link leaving the guitar's *port* is redirected, whatever node name it
    is saved under. An ALSA device's port appears once per profile
    (`Audient iD14 Multichannel:capture_AUX0` and
    `Audient iD14 Pro:capture_AUX0` are the same socket), and matching on one
    node left the other profile's link feeding Sushi dry — the dry-under-amped
    failure this design exists to prevent. Only one profile is ever live, so
    feeding the amp from each is what the saved file already did.

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

    into_sushi = f"{sushi_node}:audio_input_"
    first_input = f"{sushi_node}:audio_input_0"

    port = guitar_port
    if port is None:
        # The positional fallback: whatever feeds Sushi's first input.
        for match in item_re.finditer(base_xml):
            _out_node, out_port, _in_node, in_port = match.groups()
            if in_port == first_input:
                port = out_port
                break
    if port is None:
        # The guitar's port does not feed Sushi at all, so there is nothing to
        # put an amp in front of. Better an unchanged patchbay than a guessed
        # one -- and `up` reports the amp as unwired rather than silently dry.
        return base_xml

    # Every node the port is saved under, in file order and without duplicates.
    nodes: list[str] = []
    for match in item_re.finditer(base_xml):
        out_node, out_port, _in_node, in_port = match.groups()
        if out_port == port and in_port.startswith(into_sushi) and out_node not in nodes:
            nodes.append(out_node)
    if not nodes:
        return base_xml

    def redirect(match: re.Match) -> str:
        _out_node, out_port, in_node, in_port = match.groups()
        # The tuner tap leaves the same port and is deliberately not rewired:
        # the tuner wants the dry string, and NAM's output is mono anyway. Only
        # what reaches Sushi is redirected.
        if out_port == port and in_port.startswith(into_sushi):
            return _PATCHBAY_ITEM.format(
                out_node=AMP_NODE, out_port=AMP_OUTPUT,
                in_node=in_node, in_port=in_port,
            )
        return match.group(0)

    rewired = item_re.sub(redirect, base_xml)
    feed = "".join(
        _PATCHBAY_ITEM.format(
            out_node=node, out_port=port, in_node=AMP_NODE, in_port=AMP_INPUT,
        )
        for node in nodes
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


def guitar_port(root: Path) -> str | None:
    """The physical port the machine file gives the guitar, if it says.

    None when there is no machine file for this host, or it names no guitar
    role — and then `amp_patchbay` falls back to its old positional rule. That
    fallback is said out loud rather than taken quietly: it is right for a
    single-channel rig and wrong the moment a second channel moves the guitar
    off input 0, and a silently misrouted amp sounds like a broken amp.
    """
    from .machine import MachineError, MachineSpec, machine_path
    from .patchbay import GUITAR_ROLE

    path = machine_path(Path(root))
    if not path.is_file():
        print(
            f"warning: no machine file at {path.name}, so the amp is wired to "
            "whatever feeds Sushi's first input rather than to the guitar role",
            file=sys.stderr,
        )
        return None
    try:
        machine = MachineSpec.load(path)
    except MachineError as exc:
        print(f"warning: {exc}; wiring the amp by input position instead",
              file=sys.stderr)
        return None
    problems = machine.validate()
    if problems:
        # The same refusal `machine check` and `patchbay generate` make. Without
        # it `up --amp-model` was the one path that wired NAM from a machine file
        # every other command rejects.
        print(
            f"warning: {path.name} has {len(problems)} problem(s), the first "
            f"being: {problems[0]}; wiring the amp by input position instead",
            file=sys.stderr,
        )
        return None
    ports = machine.role_ports().get(GUITAR_ROLE) or []
    if len(ports) != 1:
        print(
            f"warning: {path.name} gives {GUITAR_ROLE!r} {len(ports)} ports and "
            "NAM has one input; wiring the amp by input position instead",
            file=sys.stderr,
        )
        return None
    return ports[0]


def prepare(
    config: Path, root: Path, runtime: Path, override_model: str | None = None
) -> dict[str, Any]:
    """Everything a run needs for the amp, written out and ready.

    Lives here rather than in the supervisor because all of it is knowledge about
    Carla and about how the amp is wired — the supervisor's job is starting
    processes, not knowing what a `.carxp` contains.

    Returns `{}` when the config describes no amp and none was asked for, which
    is the supervisor's signal to start none.

    Both files are generated per run rather than kept on disk. The Carla project
    has to be, since the model changes with the config; the patchbay has to be so
    it cannot drift from the saved one it is derived from.
    """
    described = amp_from_config_file(config)
    if override_model and not described:
        # --amp-model against a config with no amp section still gets an amp:
        # auditioning a model on a rig that has never had one is reasonable.
        described = {"model": override_model, "parameters": {}}
    if not described or not described.get("model"):
        return {}

    model = described["model"]
    if override_model:
        # A bare filename means amp/models/, which is where they live.
        model = override_model if "/" in override_model else f"amp/models/{override_model}"

    runtime.mkdir(parents=True, exist_ok=True)
    project = runtime / "amp.carxp"
    project.write_text(carla_project(resolve_model(model, root)))

    # qpwgraph maintains whatever patch it loaded, so the amp needs its own file
    # rather than extra connections layered onto the saved one — otherwise the
    # direct guitar-to-Sushi route stays live too and the dry signal sits under
    # the amped one.
    patchbay = None
    base = Path(root) / "Patchbay" / "rig.qpwgraph"
    if base.is_file():
        patchbay = runtime / "rig-amp.qpwgraph"
        patchbay.write_text(amp_patchbay(base.read_text(), guitar_port(Path(root))))

    return {
        "model": model,
        "values": dict(described.get("parameters") or {}),
        "project": project,
        "patchbay": patchbay,
    }
