"""Read LV2 plugin metadata from the installed bundles, via lilv.

This answers the questions you need settled *before* committing a plugin to a
rig — which is the whole friction issue #15 exists to remove. Today you
hand-author a yaml block, emit, and only then discover whether the plugin
loads, what its parameters are really called, or whether it can be configured
from a config file at all.

Everything here is derived, never guessed. lilv reads the plugin's own TTL,
so the values are the plugin author's declarations rather than our inference.

`lilv` is a system package (`python3-lilv`), not a pip dependency — the venv
only sees it when created with `--system-site-packages`. Imported lazily with
a clear message if missing, matching how `live.py` handles elkpy.

## Why `config_drivable` is the field that matters

A plugin whose state lives entirely in control ports can be fully described by
a Sushi config. A plugin that keeps state *outside* them — a file path, a
loaded model, a sample — reaches it through the LV2 `patch:`/`state:`
extensions, which **Sushi does not support**. Such a plugin loads happily and
then silently ignores the part you cared about.

Three signals, all read from the plugin's own TTL, and all three must be clear:

- **atom ports** — the usual transport for `patch:` messages
- **`patch:writable`** — properties the plugin expects to be *set*, which for a
  model loader is the model path
- **`state:interface`** in `lv2:extensionData` — the plugin implements LV2 state
  save/restore, i.e. it has state that control ports alone cannot capture

Checking atom ports alone is not enough, and the counter-example is exactly the
case you'd most want caught: `zeroconvolv`, an impulse-response convolver,
declares **no** atom ports and **no** `patch:writable`, yet is obviously not
config-drivable — it reports zero control parameters because its entire
behaviour comes from an IR file. Only `state:interface` gives it away. Judging
it by atom ports would have marked an IR loader as fully configurable.

## Why the flags matter more than they look

`logarithmic` is the one worth calling out. `panel.py` currently decides
fader scaling with a domain-ratio heuristic (`LOG_SCALE_DOMAIN_THRESHOLD`)
because at the time it had no better signal — Sushi normalises every port
linearly and drops the hint. But the hint exists in the TTL, and lilv can read
it. That makes it available to replace a guess with the plugin author's actual
declaration.

Note the deliberate scope limit: this module **reads and reports**. It does not
write a catalogue file and it does not curate which parameters are worth
showing. Those are judgment calls, and mixing them into generated data is what
makes generated data impossible to regenerate safely.
"""

from __future__ import annotations

import sys
from typing import Any

# LV2 URIs that aren't exposed as lilv namespace shortcuts.
_ATOM_PORT = "http://lv2plug.in/ns/ext/atom#AtomPort"
_PORT_PROPS = "http://lv2plug.in/ns/ext/port-props#"
_LOGARITHMIC = _PORT_PROPS + "logarithmic"
_PATCH_WRITABLE = "http://lv2plug.in/ns/ext/patch#writable"
_STATE_INTERFACE = "http://lv2plug.in/ns/ext/state#interface"


def _world():
    try:
        import lilv
    except ImportError:
        sys.exit(
            "lilv not found. It's a system package, not a pip one:\n"
            "  sudo apt install python3-lilv liblilv-dev\n"
            "and the venv must be created with --system-site-packages to see it."
        )
    world = lilv.World()
    world.load_all()
    return world


def _node_float(node) -> float | None:
    if node is None:
        return None
    try:
        return float(node)
    except (TypeError, ValueError):
        return None


def _describe_port(world, plugin, port) -> dict[str, Any]:
    lv2 = world.ns.lv2
    default, minimum, maximum = port.get_range()
    return {
        "symbol": str(port.get_symbol()),
        "name": str(port.get_name()),
        "min": _node_float(minimum),
        "max": _node_float(maximum),
        "default": _node_float(default),
        "toggled": port.has_property(world.new_uri(str(lv2.toggled))),
        "integer": port.has_property(world.new_uri(str(lv2.integer))),
        "enumeration": port.has_property(world.new_uri(str(lv2.enumeration))),
        "logarithmic": port.has_property(world.new_uri(_LOGARITHMIC)),
    }


def is_config_drivable(
    atom_ports: int, patch_writables: list[str], state_interface: bool
) -> bool:
    """Whether a Sushi JSON config can fully describe this plugin's state.

    Pure and separate from lilv so the rule itself is testable without an LV2
    install, and so the reasoning stays in one place rather than inline in a
    200-line extraction function. See the module docstring for why all three
    signals are needed — checking atom ports alone passes an IR convolver.
    """
    return atom_ports == 0 and not patch_writables and not state_interface


def describe(plugin, world) -> dict[str, Any]:
    """Everything deterministically knowable about one plugin."""
    lv2 = world.ns.lv2
    atom_port = world.new_uri(_ATOM_PORT)

    parameters: list[dict[str, Any]] = []
    audio_in = audio_out = atom_ports = 0

    for i in range(plugin.get_num_ports()):
        port = plugin.get_port_by_index(i)
        is_input = port.is_a(lv2.InputPort)
        if port.is_a(lv2.ControlPort) and is_input:
            parameters.append(_describe_port(world, plugin, port))
        elif port.is_a(lv2.AudioPort):
            if is_input:
                audio_in += 1
            else:
                audio_out += 1
        if port.is_a(atom_port):
            atom_ports += 1

    try:
        plugin_class = str(plugin.get_class().get_label())
    except Exception:
        # A plugin may declare no class, or one lilv can't label. That's a
        # gap in its metadata, not an error worth aborting a whole scan for.
        plugin_class = None

    patch_writables = [str(n) for n in plugin.get_value(world.new_uri(_PATCH_WRITABLE))]
    extension_data = [
        str(n) for n in plugin.get_value(world.new_uri(str(lv2.extensionData)))
    ]
    state_interface = _STATE_INTERFACE in extension_data

    return {
        "uri": str(plugin.get_uri()),
        "name": str(plugin.get_name()),
        "class": plugin_class,
        "bundle": str(plugin.get_bundle_uri()).replace("file://", "").rstrip("/"),
        "audio_in": audio_in,
        "audio_out": audio_out,
        "atom_ports": atom_ports,
        "patch_writables": patch_writables,
        "state_interface": state_interface,
        "config_drivable": is_config_drivable(
            atom_ports, patch_writables, state_interface
        ),
        "parameters": parameters,
    }


def probe(uri: str | None = None) -> list[dict[str, Any]]:
    """Describe one plugin by URI, or every installed plugin when uri is None."""
    world = _world()
    plugins = world.get_all_plugins()

    if uri is not None:
        plugin = plugins.get_by_uri(world.new_uri(uri))
        if plugin is None:
            sys.exit(
                f"no LV2 plugin with URI {uri!r} is installed.\n"
                "Check LV2_PATH — the rig sets it to plugins/ alone, so a plugin "
                "that exists system-wide is deliberately invisible here."
            )
        return [describe(plugin, world)]

    return sorted(
        (describe(p, world) for p in plugins),
        key=lambda d: d["uri"],
    )


def summarise(described: list[dict[str, Any]]) -> str:
    """One line per plugin — the form that's actually readable when scanning
    a whole install for candidates."""
    lines = []
    for d in described:
        flag = "" if d["config_drivable"] else "  STATE"
        lines.append(
            f"{len(d['parameters']):4d}p "
            f"{d['audio_in']}in/{d['audio_out']}out{flag:6s}  "
            f"{(d['class'] or '-')[:22]:24s} {d['name'][:28]:30s} {d['uri']}"
        )
    return "\n".join(lines)
