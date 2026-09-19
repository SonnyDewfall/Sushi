"""sushi-rig — author an LV2 rig live in Sushi, then capture it back as a config file.

See IMPLEMENTATION_BRIEF.md for the full workflow this supports.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .dump import dump_plugins
from .emit import emit
from .live import DEFAULT_GRPC_ADDRESS
from .listen import DEFAULT_LISTEN_PORT, DEFAULT_STATUS_HOST, DEFAULT_STATUS_PORT
from .panel import build_osc_panel
from .rig import DEFAULT_CONFIG_NAME
from .spec import RigSpec


def write_json(path: Path, payload) -> None:
    path.write_text(json.dumps(payload, indent=2) + "\n")
    print(f"wrote {path}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sushi-rig", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("emit", help="rig.yaml (+ state) -> Sushi config")
    p.add_argument("rig", type=Path)
    p.add_argument("--state", type=Path, help="captured state file to fold in")
    p.add_argument("-o", "--out", type=Path, default=Path("rig.json"))

    p = sub.add_parser("push", help="build the rig in a running Sushi")
    p.add_argument("rig", type=Path)
    p.add_argument("--address", default=DEFAULT_GRPC_ADDRESS)

    p = sub.add_parser("capture", help="read parameter state from a running Sushi")
    p.add_argument("--address", default=DEFAULT_GRPC_ADDRESS)
    p.add_argument("-o", "--out", type=Path, default=Path("state.json"))

    p = sub.add_parser(
        "panel",
        help="Open Stage Control panel from a config, using live values from a running Sushi",
    )
    p.add_argument("config", type=Path)
    p.add_argument("--sushi", default="sushi")
    p.add_argument(
        "--address",
        default=DEFAULT_GRPC_ADDRESS,
        help="the running Sushi to read current parameter values from — "
        "must already be running this same config",
    )
    p.add_argument(
        "--osc-port",
        type=int,
        default=24024,
        help="Sushi's OSC receive port (--osc-rcv-port), just to print the matching "
        "open-stage-control --send value — not written into the panel file",
    )
    p.add_argument(
        "--listener-port",
        type=int,
        default=DEFAULT_LISTEN_PORT,
        help="port the save button targets — must match 'sushi-rig listen --port'",
    )
    p.add_argument("-o", "--out", type=Path, default=Path("panel.json"))

    p = sub.add_parser(
        "probe",
        help="what an LV2 plugin declares about itself — parameters, ranges, "
        "and whether it can be driven from a config at all",
    )
    p.add_argument(
        "uri",
        nargs="?",
        help="plugin URI. Omit to list every plugin visible on LV2_PATH, which "
        "is how you find candidates in the first place",
    )
    p.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="full detail as JSON, including every parameter's range and flags",
    )

    p = sub.add_parser("save", help="capture live state and write it as a named config")
    p.add_argument("name", help="config name — becomes config/<name>.json")
    p.add_argument("--rig", type=Path, required=True, help="rig.yaml this config is built from")
    p.add_argument("--out-dir", type=Path, default=Path("../config"))
    p.add_argument("--archive-dir", type=Path, default=Path("../config/archive"))
    p.add_argument("--address", default=DEFAULT_GRPC_ADDRESS)

    p = sub.add_parser(
        "up",
        help="start the whole rig — patchbay, tuner, Sushi, and (by default) "
        "the control panel — then return to the prompt",
    )
    p.add_argument(
        "config_name",
        nargs="?",
        default=DEFAULT_CONFIG_NAME,
        help=f"names config/<name>.json [default: {DEFAULT_CONFIG_NAME}]",
    )
    p.add_argument(
        "--headless",
        action="store_true",
        help="no panel and no save listener — for playing through the rig "
        "rather than authoring a tone",
    )
    p.add_argument(
        "--no-tuner", dest="tuner", action="store_false", help="skip fmit"
    )

    p = sub.add_parser("down", help="stop a running rig and verify it stopped")
    p.add_argument(
        "--force",
        action="store_true",
        help="skip straight to SIGKILL, giving Sushi no chance to release its "
        "JACK ports cleanly — for a rig that is already wedged",
    )

    sub.add_parser("status", help="what is running, if anything")

    p = sub.add_parser(
        "listen",
        help="run an OSC listener that saves a config on request (for the panel's save button)",
    )
    p.add_argument("--rig", type=Path, required=True, help="rig.yaml every save is built from")
    p.add_argument("--out-dir", type=Path, default=Path("../config"))
    p.add_argument("--archive-dir", type=Path, default=Path("../config/archive"))
    p.add_argument("--address", default=DEFAULT_GRPC_ADDRESS, help="Sushi's gRPC address")
    p.add_argument(
        "--host",
        default="127.0.0.1",
        help="address to listen on — defaults to loopback-only; widen deliberately",
    )
    p.add_argument("--port", type=int, default=DEFAULT_LISTEN_PORT)
    p.add_argument(
        "--status-host",
        default=DEFAULT_STATUS_HOST,
        help="where to send save-outcome messages — open-stage-control's OSC input",
    )
    p.add_argument("--status-port", type=int, default=DEFAULT_STATUS_PORT)

    # Supplying these turns on panel auto-refresh: after a move, the panel is
    # regenerated and open-stage-control is told to reload it, so the tabs end
    # up in the rig's new order. Optional — without them a move still works, it
    # just leaves the tabs showing the old order.
    p.add_argument(
        "--panel-config",
        type=Path,
        help="config to regenerate the panel from after a chain reorder",
    )
    p.add_argument(
        "--panel-out",
        type=Path,
        help="panel file to rewrite and reload (the one open-stage-control loaded)",
    )
    p.add_argument(
        "--sushi",
        default="sushi",
        help="sushi binary, used to re-dump plugin parameters when regenerating",
    )

    args = parser.parse_args(argv)

    if args.command == "emit":
        state = json.loads(args.state.read_text()) if args.state else None
        write_json(args.out, emit(RigSpec.load(args.rig), state))
    elif args.command == "push":
        from .live import push

        push(RigSpec.load(args.rig), args.address)
    elif args.command == "capture":
        from .live import capture

        write_json(args.out, capture(args.address))
    elif args.command == "panel":
        from .live import get_live_bypass_state, get_live_parameter_info
        from .probe import units_for_config

        dump = dump_plugins(args.config, args.sushi)
        live_info = get_live_parameter_info(args.address)
        bypass_info = get_live_bypass_state(args.address)
        write_json(
            args.out,
            build_osc_panel(
                dump,
                live_info,
                args.listener_port,
                bypass_info,
                units_for_config(args.config),
            ),
        )
        print(
            f"open with: open-stage-control --load {args.out} "
            f"--send 127.0.0.1:{args.osc_port}"
        )
    elif args.command == "probe":
        from .probe import probe, summarise

        described = probe(args.uri)
        if args.as_json:
            print(json.dumps(described if args.uri is None else described[0], indent=2))
        elif args.uri is None:
            print(summarise(described))
            drivable = sum(1 for d in described if d["config_drivable"])
            print(
                f"\n{len(described)} plugins, {drivable} config-drivable. "
                "STATE marks the rest: they keep state outside their control "
                "ports (a file, a model, a sample), which Sushi cannot set "
                "from a config.",
                file=sys.stderr,
            )
        else:
            d = described[0]
            print(f"{d['name']}  ({d['class'] or 'no class declared'})")
            print(f"  uri     {d['uri']}")
            print(f"  bundle  {d['bundle']}")
            print(f"  audio   {d['audio_in']} in / {d['audio_out']} out")
            if d["config_drivable"]:
                print("  config  drivable — all state lives in control ports")
            else:
                reasons = []
                if d["atom_ports"]:
                    reasons.append(f"{d['atom_ports']} atom port(s)")
                if d["patch_writables"]:
                    reasons.append(f"{len(d['patch_writables'])} patch:writable")
                if d["state_interface"]:
                    reasons.append("state:interface")
                print(
                    "  config  NOT fully drivable — " + ", ".join(reasons) + "."
                    " Sushi cannot set state held outside control ports."
                )
            print(f"  {len(d['parameters'])} parameter(s):")
            for prm in d["parameters"]:
                flags = " ".join(
                    f for f in ("toggled", "integer", "enumeration", "logarithmic")
                    if prm[f]
                )
                rng = f"[{prm['min']}, {prm['max']}]"
                print(
                    f"    {prm['name'][:28]:30s} {rng:24s} "
                    f"default={prm['default']}{'  ' + flags if flags else ''}"
                )

    elif args.command == "save":
        from .save import SaveError, save_config

        try:
            out_path = save_config(
                args.name, args.rig, args.out_dir, args.archive_dir, args.address
            )
        except SaveError as exc:
            sys.exit(str(exc))
        print(f"saved {out_path}")
    elif args.command in ("up", "down", "status"):
        from .rig import RigError, down, status, up

        try:
            if args.command == "up":
                return up(args.config_name, headless=args.headless, tuner=args.tuner)
            if args.command == "down":
                return down(args.force)
            return status()
        except RigError as exc:
            sys.exit(str(exc))
    elif args.command == "listen":
        from .listen import serve

        serve(
            args.rig,
            args.out_dir,
            args.archive_dir,
            address=args.address,
            host=args.host,
            port=args.port,
            status_host=args.status_host,
            status_port=args.status_port,
            panel_config=args.panel_config,
            panel_out=args.panel_out,
            sushi_bin=args.sushi,
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
