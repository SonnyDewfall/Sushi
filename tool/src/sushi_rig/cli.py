"""sushi-rig — author an LV2 rig live in Sushi, then capture it back as a config file.

See IMPLEMENTATION_BRIEF.md for the full workflow this supports.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .dump import dump_plugins
from .emit import emit
from .live import DEFAULT_GRPC_ADDRESS
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

    p = sub.add_parser("panel", help="Open Stage Control panel from a config")
    p.add_argument("config", type=Path)
    p.add_argument("--sushi", default="sushi")
    p.add_argument(
        "--osc-port",
        type=int,
        default=24024,
        help="Sushi's OSC receive port (--osc-rcv-port), just to print the matching "
        "open-stage-control --send value — not written into the panel file",
    )
    p.add_argument("-o", "--out", type=Path, default=Path("panel.json"))

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
        dump = dump_plugins(args.config, args.sushi)
        write_json(args.out, build_osc_panel(dump))
        print(
            f"open with: open-stage-control --load {args.out} "
            f"--send 127.0.0.1:{args.osc_port}"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
