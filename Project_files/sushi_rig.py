#!/usr/bin/env python3
"""
sushi-rig — author an LV2 rig live in Sushi, then capture it back as a config file.

The workflow this supports:

    1. probe    Build a catalogue of LV2 plugin metadata from the local LV2 world
                (port symbols, indices, ranges, defaults, flags). Used for
                sanity-checking and for drift detection between machines.
    2. emit     Turn rig.yaml into a Sushi JSON config. With --state, also
                writes an "initial_state" block from captured values.
    3. push     Build the rig described by rig.yaml inside an already-running
                Sushi instance, over gRPC. Use this to get something audible
                that you can then tweak.
    4. capture  Read every processor's parameters and normalised values back
                out of the running Sushi and write them to a state file.
    5. verify   Run `sushi --dump-plugins` against an emitted config and check
                that every parameter name referenced in initial_state actually
                exists on the processor. Catches name drift and typos.
    6. panel    Generate an Open Stage Control panel from a --dump-plugins
                dump, so you have faders to tweak with.

Dependencies, all optional per-command:
    probe            lilv Python bindings (Debian/Ubuntu: python3-lilv)
    push, capture    elkpy  (pip install elkpy)
    emit, verify     PyYAML
If the lilv bindings are unavailable, `lv2info -p <uri>` plus rdflib over the
plugin's .ttl is a workable fallback, but the bindings are far less fiddly.

Parameter values in Sushi are always normalised 0.0-1.0 regardless of plugin
format, so capture and emit both deal exclusively in normalised values. The
probe catalogue keeps the real-world ranges so you can interpret them.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_GRPC_ADDRESS = "localhost:51051"


# --------------------------------------------------------------------------- #
# Rig specification
# --------------------------------------------------------------------------- #


@dataclass
class PluginSpec:
    name: str
    uri: str | None = None          # LV2
    path: str | None = None         # VST2 / VST3
    uid: str | None = None          # internal / VST3 sub-plugin id
    type: str = "lv2"

    def to_sushi(self) -> dict[str, Any]:
        entry: dict[str, Any] = {"name": self.name, "type": self.type}
        if self.type == "lv2":
            if not self.uri:
                raise ValueError(f"plugin {self.name!r}: lv2 type requires 'uri'")
            entry["uri"] = self.uri
        else:
            if self.path:
                entry["path"] = self.path
            if self.uid:
                entry["uid"] = self.uid
        return entry


@dataclass
class TrackSpec:
    name: str
    channels: int = 2
    multibus: bool = False
    buses: int | None = None
    thread: int | None = None
    inputs: list[dict[str, int]] = field(default_factory=list)
    outputs: list[dict[str, int]] = field(default_factory=list)
    plugins: list[PluginSpec] = field(default_factory=list)

    def to_sushi(self) -> dict[str, Any]:
        entry: dict[str, Any] = {"name": self.name}
        if self.multibus:
            entry["multibus"] = True
            entry["buses"] = self.buses if self.buses is not None else 1
            entry["channels"] = self.channels
        else:
            entry["channels"] = self.channels
        if self.thread is not None:
            entry["thread"] = self.thread
        entry["inputs"] = self.inputs
        entry["outputs"] = self.outputs
        entry["plugins"] = [p.to_sushi() for p in self.plugins]
        return entry


@dataclass
class RigSpec:
    host: dict[str, Any] = field(default_factory=dict)
    tracks: list[TrackSpec] = field(default_factory=list)
    midi: dict[str, Any] = field(default_factory=dict)
    osc: dict[str, Any] = field(default_factory=dict)
    cv_control: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> "RigSpec":
        import yaml

        raw = yaml.safe_load(path.read_text()) or {}
        tracks = []
        for t in raw.get("tracks", []):
            plugins = [PluginSpec(**p) for p in t.pop("plugins", [])]
            tracks.append(TrackSpec(plugins=plugins, **t))
        return cls(
            host=raw.get("host", {}),
            tracks=tracks,
            midi=raw.get("midi", {}),
            osc=raw.get("osc", {}),
            cv_control=raw.get("cv_control", {}),
        )

    def plugin_names(self) -> list[str]:
        return [p.name for t in self.tracks for p in t.plugins]

    def validate(self) -> list[str]:
        """Sushi requires globally unique track and processor names."""
        problems: list[str] = []
        seen: dict[str, str] = {}
        for track in self.tracks:
            if track.name in seen:
                problems.append(f"duplicate name {track.name!r}")
            seen[track.name] = "track"
            for plugin in track.plugins:
                if plugin.name in seen:
                    problems.append(
                        f"duplicate name {plugin.name!r} "
                        f"(already used as a {seen[plugin.name]})"
                    )
                seen[plugin.name] = "plugin"
        return problems


# --------------------------------------------------------------------------- #
# 1. probe — LV2 metadata catalogue via lilv
# --------------------------------------------------------------------------- #


def probe(uris: list[str] | None) -> dict[str, Any]:
    """Build a catalogue of control-port metadata for the given URIs (or all)."""
    try:
        import lilv
    except ImportError:  # pragma: no cover
        sys.exit(
            "lilv Python bindings not found. On Debian/Ubuntu: "
            "apt install python3-lilv"
        )

    world = lilv.World()
    world.load_all()
    ns = world.ns

    wanted = set(uris) if uris else None
    catalogue: dict[str, Any] = {}

    for plugin in world.get_all_plugins():
        uri = str(plugin.get_uri())
        if wanted is not None and uri not in wanted:
            continue

        ports = []
        for index in range(plugin.get_num_ports()):
            port = plugin.get_port_by_index(index)
            if not port.is_a(ns.lv2.ControlPort):
                continue

            default, minimum, maximum = port.get_range()
            entry: dict[str, Any] = {
                "index": index,
                "symbol": str(port.get_symbol()),
                "name": str(port.get_name()),
                "direction": "input" if port.is_a(ns.lv2.InputPort) else "output",
                "min": _as_float(minimum),
                "max": _as_float(maximum),
                "default": _as_float(default),
                "toggled": port.has_property(ns.lv2.toggled),
                "integer": port.has_property(ns.lv2.integer),
                "logarithmic": port.has_property(ns.pprops.logarithmic),
                "enumeration": port.has_property(ns.lv2.enumeration),
            }

            scale_points = []
            for sp in port.get_scale_points():
                scale_points.append(
                    {"label": str(sp.get_label()), "value": _as_float(sp.get_value())}
                )
            if scale_points:
                entry["scale_points"] = scale_points
            ports.append(entry)

        catalogue[uri] = {
            "name": str(plugin.get_name()),
            "control_ports": ports,
            # Non-port state is the thing that will bite you: Sushi does not
            # support the LV2 patch: extension, so any plugin relying on it
            # (file loaders, convolvers, sample banks) cannot be configured
            # from a JSON config at all.
            "has_state_interface": bool(
                plugin.has_feature(ns.state.loadDefaultState)
            )
            if hasattr(ns, "state")
            else None,
        }

    if wanted:
        for missing in sorted(wanted - set(catalogue)):
            print(f"warning: {missing} not found in LV2_PATH", file=sys.stderr)

    return catalogue


def _as_float(node: Any) -> float | None:
    if node is None:
        return None
    try:
        return float(node)
    except (TypeError, ValueError):
        return None


def normalise(value: float, port: dict[str, Any]) -> float:
    """Map a real-world port value into Sushi's 0.0-1.0 domain.

    This assumes linear mapping, which is what Sushi's wrappers do for plain
    control ports. Logarithmic and enumerated ports are flagged rather than
    guessed at, because getting them silently wrong is worse than being told.
    """
    lo, hi = port.get("min"), port.get("max")
    if lo is None or hi is None or hi == lo:
        raise ValueError(f"port {port['symbol']!r} has no usable range")
    if port.get("logarithmic") or port.get("enumeration"):
        print(
            f"warning: {port['symbol']!r} is "
            f"{'logarithmic' if port.get('logarithmic') else 'enumerated'}; "
            "verify the mapping against Sushi before trusting it",
            file=sys.stderr,
        )
    return max(0.0, min(1.0, (value - lo) / (hi - lo)))


# --------------------------------------------------------------------------- #
# 2. emit — rig.yaml (+ captured state) -> Sushi JSON config
# --------------------------------------------------------------------------- #


def emit(rig: RigSpec, state: dict[str, Any] | None) -> dict[str, Any]:
    problems = rig.validate()
    if problems:
        sys.exit("rig spec invalid:\n  " + "\n  ".join(problems))

    config: dict[str, Any] = {"host_config": dict(rig.host)}
    config["tracks"] = [t.to_sushi() for t in rig.tracks]
    if rig.midi:
        config["midi"] = rig.midi
    if rig.osc:
        config["osc"] = rig.osc
    if rig.cv_control:
        config["cv_control"] = rig.cv_control

    if state:
        known = set(rig.plugin_names()) | {t.name for t in rig.tracks}
        initial_state = []
        for processor, values in state.get("processors", {}).items():
            if processor not in known:
                print(
                    f"warning: captured state for {processor!r}, which is not "
                    "in the rig spec — skipping",
                    file=sys.stderr,
                )
                continue
            entry: dict[str, Any] = {"processor": processor}
            if values.get("bypassed") is not None:
                entry["bypassed"] = values["bypassed"]
            if values.get("program") is not None:
                entry["program"] = values["program"]
            if values.get("parameters"):
                entry["parameters"] = [
                    {"name": name, "value": round(float(val), 6)}
                    for name, val in values["parameters"].items()
                ]
            if values.get("properties"):
                entry["properties"] = [
                    {"name": name, "value": val}
                    for name, val in values["properties"].items()
                ]
            initial_state.append(entry)
        if initial_state:
            config["initial_state"] = initial_state

    return config


# --------------------------------------------------------------------------- #
# 3/4. push and capture — live Sushi over gRPC
# --------------------------------------------------------------------------- #


def _controller(address: str, proto: str | None):
    from elkpy import sushicontroller as sc

    return sc.SushiController(address, proto) if proto else sc.SushiController(address)


_PLUGIN_TYPE = {"internal": 1, "vst2x": 2, "vst3x": 3, "lv2": 4}


def push(rig: RigSpec, address: str, proto: str | None) -> None:
    """Create the rig's tracks and processors in a running Sushi."""
    problems = rig.validate()
    if problems:
        sys.exit("rig spec invalid:\n  " + "\n  ".join(problems))

    from elkpy import sushi_info_types as info

    controller = _controller(address, proto)
    try:
        for track in rig.tracks:
            event = controller.audio_graph.create_track(track.name, track.channels)
            _wait(event)
            track_id = getattr(event, "sushi_id", None)
            if track_id is None:
                track_id = controller.audio_graph.get_processor_id(track.name)
            print(f"track {track.name!r} -> id {track_id}")

            for plugin in track.plugins:
                plugin_type = getattr(
                    info.PluginType, plugin.type.upper(), _PLUGIN_TYPE[plugin.type]
                )
                event = controller.audio_graph.create_processor_on_track(
                    name=plugin.name,
                    uid=plugin.uid or "",
                    path=plugin.path or plugin.uri or "",
                    plugin_type=plugin_type,
                    track_id=track_id,
                    before_processor_id=track_id,
                    add_to_back=True,
                )
                _wait(event)
                print(f"  + {plugin.name!r} ({plugin.uri or plugin.path})")
    finally:
        controller.close()


def _wait(event: Any, timeout: float = 5.0) -> None:
    """elkpy's graph-editing calls return an asyncio.Event-like handle."""
    waiter = getattr(event, "wait", None)
    if waiter is None:
        return
    try:
        result = waiter()
        if hasattr(result, "__await__"):  # async context, caller must await
            return
    except Exception as exc:  # noqa: BLE001 - surfaced, not swallowed
        print(f"warning: graph command may not have completed: {exc}", file=sys.stderr)


def capture(address: str, proto: str | None) -> dict[str, Any]:
    """Read the full parameter state of a running Sushi."""
    controller = _controller(address, proto)
    processors: dict[str, Any] = {}
    try:
        for track in controller.audio_graph.get_tracks():
            targets = [(track.id, track.name)]
            for proc in controller.audio_graph.get_track_processors(track.id):
                targets.append((proc.id, proc.name))

            for proc_id, proc_name in targets:
                params: dict[str, float] = {}
                for param in controller.parameters.get_processor_parameters(proc_id):
                    # Sushi normalises all parameter values to 0.0-1.0.
                    params[param.name] = controller.parameters.get_parameter_value(
                        proc_id, param.id
                    )

                entry: dict[str, Any] = {"parameters": params}

                info_obj = _safe(controller.audio_graph.get_processor_info, proc_id)
                if info_obj is not None and getattr(info_obj, "program_count", 0) > 0:
                    entry["program"] = _safe(
                        controller.programs.get_processor_current_program, proc_id
                    )

                bypassed = _safe(controller.audio_graph.get_processor_bypass_state, proc_id)
                if bypassed is not None:
                    entry["bypassed"] = bool(bypassed)

                processors[proc_name] = entry
    finally:
        controller.close()

    return {"processors": processors}


def _safe(fn, *args):
    try:
        return fn(*args)
    except Exception:  # noqa: BLE001 - optional data, absence is fine
        return None


# --------------------------------------------------------------------------- #
# 5. verify — cross-check emitted config against sushi --dump-plugins
# --------------------------------------------------------------------------- #


def dump_plugins(config_path: Path, sushi_bin: str = "sushi") -> Any:
    """Run Sushi's own introspection. This is the authoritative name source."""
    result = subprocess.run(
        [sushi_bin, "--dump-plugins", "-c", str(config_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        sys.exit(
            f"{sushi_bin} --dump-plugins failed (exit {result.returncode}):\n"
            f"{result.stderr.strip()}"
        )
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        # Sushi may print log lines before the JSON payload.
        start = result.stdout.find("{")
        if start == -1:
            sys.exit("could not find JSON in --dump-plugins output")
        return json.loads(result.stdout[start:])


def collect_parameter_names(dump: Any) -> dict[str, set[str]]:
    """Walk the dump generically and index parameter names by processor name.

    Written defensively rather than against a fixed schema, because the shape of
    the dump has moved between Sushi versions.
    """
    found: dict[str, set[str]] = {}

    def visit(node: Any, current: str | None) -> None:
        if isinstance(node, dict):
            name = node.get("name")
            params = node.get("parameters")
            if isinstance(params, list) and isinstance(name, str):
                bucket = found.setdefault(name, set())
                for param in params:
                    if isinstance(param, dict) and isinstance(param.get("name"), str):
                        bucket.add(param["name"])
                current = name
            for value in node.values():
                visit(value, current)
        elif isinstance(node, list):
            for item in node:
                visit(item, current)

    visit(dump, None)
    return found


def verify(config_path: Path, sushi_bin: str) -> int:
    config = json.loads(config_path.read_text())
    actual = collect_parameter_names(dump_plugins(config_path, sushi_bin))

    failures = 0
    for entry in config.get("initial_state", []):
        processor = entry.get("processor")
        if processor not in actual:
            print(f"FAIL {processor}: not present in --dump-plugins output")
            failures += 1
            continue
        for param in entry.get("parameters", []):
            if param["name"] not in actual[processor]:
                closest = sorted(actual[processor])[:8]
                print(
                    f"FAIL {processor}.{param['name']!r}: no such parameter. "
                    f"Available (first 8): {closest}"
                )
                failures += 1

    if failures:
        print(f"\n{failures} problem(s) found.")
    else:
        total = sum(len(e.get("parameters", [])) for e in config.get("initial_state", []))
        print(f"OK — {total} parameter reference(s) resolved across {len(actual)} processors.")
    return 1 if failures else 0


# --------------------------------------------------------------------------- #
# 6. panel — Open Stage Control panel from a plugin dump
# --------------------------------------------------------------------------- #


def build_osc_panel(dump: Any, osc_port: int = 24024) -> dict[str, Any]:
    """Generate a tabbed Open Stage Control panel: one tab per processor."""
    tabs = []
    for processor, params in sorted(collect_parameter_names(dump).items()):
        widgets = []
        for i, param in enumerate(sorted(params)):
            widgets.append(
                {
                    "type": "fader",
                    "id": f"{processor}/{param}",
                    "label": param,
                    "address": f"/parameter/{processor}/{param}",
                    "range": {"min": 0, "max": 1},
                    "default": 0.5,
                    "width": 90,
                    "height": 260,
                    "left": 10 + (i % 8) * 100,
                    "top": 10 + (i // 8) * 280,
                }
            )
        tabs.append({"type": "tab", "id": processor, "label": processor, "widgets": widgets})

    return {
        "type": "root",
        "id": "sushi-rig",
        "sendPort": str(osc_port),
        "widgets": [{"type": "panel", "id": "tabs", "tabs": tabs}],
    }


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sushi-rig", description=__doc__.split("\n")[1])
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("probe", help="catalogue LV2 control-port metadata")
    p.add_argument("--uri", action="append", help="restrict to this URI (repeatable)")
    p.add_argument("-o", "--out", type=Path, default=Path("catalogue.json"))

    p = sub.add_parser("emit", help="rig.yaml (+ state) -> Sushi config")
    p.add_argument("rig", type=Path)
    p.add_argument("--state", type=Path, help="captured state file to fold in")
    p.add_argument("-o", "--out", type=Path, default=Path("rig.json"))

    p = sub.add_parser("push", help="build the rig in a running Sushi")
    p.add_argument("rig", type=Path)
    p.add_argument("--address", default=DEFAULT_GRPC_ADDRESS)
    p.add_argument("--proto", help="path to sushi_rpc.proto")

    p = sub.add_parser("capture", help="read parameter state from a running Sushi")
    p.add_argument("--address", default=DEFAULT_GRPC_ADDRESS)
    p.add_argument("--proto", help="path to sushi_rpc.proto")
    p.add_argument("-o", "--out", type=Path, default=Path("state.json"))

    p = sub.add_parser("verify", help="check initial_state names against Sushi")
    p.add_argument("config", type=Path)
    p.add_argument("--sushi", default="sushi", help="path to the sushi binary")

    p = sub.add_parser("panel", help="Open Stage Control panel from a config")
    p.add_argument("config", type=Path)
    p.add_argument("--sushi", default="sushi")
    p.add_argument("--osc-port", type=int, default=24024)
    p.add_argument("-o", "--out", type=Path, default=Path("panel.json"))

    args = parser.parse_args(argv)

    if args.command == "probe":
        write_json(args.out, probe(args.uri))
    elif args.command == "emit":
        state = json.loads(args.state.read_text()) if args.state else None
        write_json(args.out, emit(RigSpec.load(args.rig), state))
    elif args.command == "push":
        push(RigSpec.load(args.rig), args.address, args.proto)
    elif args.command == "capture":
        write_json(args.out, capture(args.address, args.proto))
    elif args.command == "verify":
        return verify(args.config, args.sushi)
    elif args.command == "panel":
        dump = dump_plugins(args.config, args.sushi)
        write_json(args.out, build_osc_panel(dump, args.osc_port))

    return 0


def write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=2) + "\n")
    print(f"wrote {path}")


if __name__ == "__main__":
    sys.exit(main())
