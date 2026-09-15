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

    p = sub.add_parser("save", help="capture live state and write it as a named config")
    p.add_argument("name", help="config name — becomes config/<name>.json")
    p.add_argument("--rig", type=Path, required=True, help="rig.yaml this config is built from")
    p.add_argument("--out-dir", type=Path, default=Path("../config"))
    p.add_argument("--archive-dir", type=Path, default=Path("../config/archive"))
    p.add_argument("--address", default=DEFAULT_GRPC_ADDRESS)

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
        from .live import get_live_parameter_info

        dump = dump_plugins(args.config, args.sushi)
        live_info = get_live_parameter_info(args.address)
        write_json(args.out, build_osc_panel(dump, live_info, args.listener_port))
        print(
            f"open with: open-stage-control --load {args.out} "
            f"--send 127.0.0.1:{args.osc_port}"
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
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
