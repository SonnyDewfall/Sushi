"""What a plugin's own Turtle says, for the questions #153 and #155 ask.

    python3.12 tool/spikes/plugin_research/inspect_plugins.py URI [URI ...] [--json]

Needs python3-lilv (Ubuntu's, for its python3.12). For each URI: name, class,
audio ports in and out, every control port with its range, the latency port,
atom and CV ports, required features, extension data, and two verdicts:

- `sushi_loads`: every required feature is one Sushi 1.3.0 offers, and no CV
  port (src/library/lv2/lv2_model.cpp `_initialize_host_feature_list`,
  `_check_for_required_features`; lv2_wrapper.cpp, CV "not yet implemented").
- `knob_shaped`: all its state is in control ports: no `patch:writable`
  property and no `state:interface`, the signals `sushi-rig probe` uses
  (tool/src/sushi_rig/probe.py). Atom ports alone are reported, not judged:
  Sushi gives them an empty sequence and most plugins only use them for a GUI.

It reads and reports; the README holds the judgements.
"""

from __future__ import annotations

import json
import sys

import lilv

LV2 = "http://lv2plug.in/ns/lv2core#"
SUSHI_FEATURES = {
    "http://lv2plug.in/ns/ext/urid#map",
    "http://lv2plug.in/ns/ext/urid#unmap",
    "http://lv2plug.in/ns/ext/log#log",
    "http://lv2plug.in/ns/ext/worker#schedule",
    "http://lv2plug.in/ns/ext/state#makePath",
    "http://lv2plug.in/ns/ext/options#options",
    "http://lv2plug.in/ns/ext/state#loadDefaultState",
    "http://lv2plug.in/ns/ext/buf-size#powerOf2BlockLength",
    "http://lv2plug.in/ns/ext/buf-size#fixedBlockLength",
    "http://lv2plug.in/ns/ext/buf-size#boundedBlockLength",
    LV2 + "isLive",
}
STATE_INTERFACE = "http://lv2plug.in/ns/ext/state#interface"
PATCH_WRITABLE = "http://lv2plug.in/ns/ext/patch#writable"


def _num(node) -> float | None:
    return None if node is None else float(str(node))


def describe(world: lilv.World, plugin) -> dict:
    n = world.new_uri
    audio, control, cv = n(LV2 + "AudioPort"), n(LV2 + "ControlPort"), n(LV2 + "CVPort")
    atom = n("http://lv2plug.in/ns/ext/atom#AtomPort")
    inp, optional = n(LV2 + "InputPort"), n(LV2 + "connectionOptional")
    toggled, integer, enum = n(LV2 + "toggled"), n(LV2 + "integer"), n(LV2 + "enumeration")
    latency_desig = n(LV2 + "latency")
    reports_latency = n(LV2 + "reportsLatency")
    out = {
        "uri": str(plugin.get_uri()),
        "name": str(plugin.get_name()),
        "class": str(plugin.get_class().get_label()),
        "bundle": str(plugin.get_bundle_uri()).replace("file://", ""),
        "audio_in": 0, "audio_out": 0, "atom_in": 0, "atom_out": 0, "cv": 0,
        "controls": [], "outputs": [], "latency_port": None,
    }
    for i in range(plugin.get_num_ports()):
        port = plugin.get_port_by_index(i)
        sym = str(port.get_symbol())
        is_in = port.is_a(inp)
        if port.is_a(audio):
            out["audio_in" if is_in else "audio_out"] += 1
        elif port.is_a(atom):
            out["atom_in" if is_in else "atom_out"] += 1
        elif port.is_a(cv):
            if not port.has_property(optional):
                out["cv"] += 1
        elif port.is_a(control):
            de, lo, hi = port.get_range()  # lilv's order: default, minimum, maximum
            entry = {"symbol": sym, "name": str(port.get_name()),
                     "min": _num(lo), "max": _num(hi), "default": _num(de)}
            for prop, label in ((toggled, "toggled"), (integer, "integer"), (enum, "enum")):
                if port.has_property(prop):
                    entry[label] = True
            if is_in:
                out["controls"].append(entry)
            else:
                out["outputs"].append(sym)
                if port.has_property(reports_latency) or sym == "latency" or (
                        port.get(n(LV2 + "designation")) is not None
                        and str(port.get(n(LV2 + "designation"))) == str(latency_desig)):
                    out["latency_port"] = sym
    out["required_features"] = sorted(str(f) for f in plugin.get_required_features())
    out["optional_features"] = sorted(str(f) for f in plugin.get_optional_features())
    out["extension_data"] = sorted(str(e) for e in plugin.get_extension_data())
    writable = world.find_nodes(plugin.get_uri(), n(PATCH_WRITABLE), None)
    out["patch_writable"] = sorted(str(w) for w in writable)
    unsupported = [f for f in out["required_features"] if f not in SUSHI_FEATURES]
    out["sushi_loads"] = not unsupported and not out["cv"]
    out["sushi_refuses_for"] = unsupported + (["CV port"] if out["cv"] else [])
    out["knob_shaped"] = not out["patch_writable"] and STATE_INTERFACE not in out["extension_data"]
    return out


def main(argv: list[str]) -> int:
    as_json = "--json" in argv
    uris = [a for a in argv if not a.startswith("--")]
    world = lilv.World()
    world.load_all()
    plugins = world.get_all_plugins()
    results = []
    for uri in uris:
        plugin = plugins.get_by_uri(world.new_uri(uri))
        if plugin is None:
            results.append({"uri": uri, "error": "not installed"})
            continue
        results.append(describe(world, plugin))
    if as_json:
        print(json.dumps(results, indent=1))
        return 0
    for r in results:
        if "error" in r:
            print(f"{r['uri']}: {r['error']}")
            continue
        print(f"{r['name']}  ({r['class']})  {r['uri']}")
        print(f"  audio {r['audio_in']} in / {r['audio_out']} out; atom {r['atom_in']}/{r['atom_out']}; "
              f"cv {r['cv']}; latency port {r['latency_port']}")
        print(f"  sushi loads: {r['sushi_loads']} {r['sushi_refuses_for'] or ''}; knob-shaped: {r['knob_shaped']}"
              f"{' patch:writable ' + str(r['patch_writable']) if r['patch_writable'] else ''}")
        print(f"  required {r['required_features']}; extension data {r['extension_data']}")
        for c in r["controls"]:
            flags = " ".join(k for k in ("toggled", "integer", "enum") if c.get(k))
            print(f"    {c['symbol']:<16} {c['name']:<28} [{c['min']}, {c['max']}] default={c['default']} {flags}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
