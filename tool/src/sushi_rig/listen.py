"""An always-running OSC listener that triggers `save_config` on request.

This is one caller of `save.save_config` — the one the Open Stage Control
panel's save button talks to (see `panel.py`'s `save_button`/`config_name`
widgets). `sushi-rig save <name>` on the CLI is the other; both call the same
core, deliberately, so nothing here is load-bearing beyond "receive a name
over OSC, call `save_config`, report the outcome back".

The name and the save trigger are **two independent messages**, not the name
carried as an argument on the save button. That was the first design, and it
doesn't work: verified with an isolated two-widget test panel and a raw OSC
listener that a button's `preArgs` referencing another widget's live value
(`@{other_widget}`) is not re-evaluated per send in real open-stage-control
1.31.1 — only the button's own tap value (`1.0`) ever arrived, regardless of
what the referenced input held or how its value was committed. An `input`
widget sending its *own* value via its own `address`/`target`, confirmed
against the same test panel, works correctly. So `NAME_ADDRESS` updates
`_last_name` below; `SAVE_ADDRESS` triggers a save using whatever name was
last received, carrying no payload of its own.

`python-osc` is a heavy, environment-specific dependency (matching how
`live.py` treats `elkpy`, per brief §3.1) — imported lazily so the rest of the
package stays importable and testable without it.
"""

from __future__ import annotations

import sys
from pathlib import Path

from .live import DEFAULT_GRPC_ADDRESS, move_processor
from .save import SaveError, save_config

DEFAULT_LISTEN_PORT = 24025
DEFAULT_STATUS_HOST = "127.0.0.1"
DEFAULT_STATUS_PORT = 8080  # open-stage-control's own OSC input (its HTTP port)
STATUS_ADDRESS = "/sushi-rig/status"
SAVE_ADDRESS = "/sushi-rig/save"
NAME_ADDRESS = "/sushi-rig/name"

# The processor is in the address rather than an argument, mirroring how Sushi
# itself addresses per-processor operations (/bypass/<processor>). That keeps
# the button's payload a bare int and avoids `preArgs` entirely — panel.py
# documents preArgs as not being re-evaluated per send for a button, which is
# what broke the first attempt at the save bar.
MOVE_ADDRESS_PREFIX = "/sushi-rig/move/"


def handle_save(
    name: str,
    rig_path: Path,
    out_dir: Path,
    archive_dir: Path,
    address: str = DEFAULT_GRPC_ADDRESS,
) -> str:
    """Run `save_config` and return a human-readable outcome message.

    Deliberately never raises — an always-running listener must survive a bad
    request (a malformed name, Sushi unreachable, whatever), not go down with
    it. Split out from `serve()`'s OSC wiring so it's testable without a
    socket or `python-osc` installed at all.
    """
    try:
        out_path = save_config(name, rig_path, out_dir, archive_dir, address)
    except SaveError as exc:
        return f"save failed: {exc}"
    except Exception as exc:  # noqa: BLE001 - keep the listener alive
        return f"save failed: unexpected error: {exc}"
    return f"saved {out_path.name}"


def serve(
    rig_path: Path,
    out_dir: Path,
    archive_dir: Path,
    address: str = DEFAULT_GRPC_ADDRESS,
    host: str = "127.0.0.1",
    port: int = DEFAULT_LISTEN_PORT,
    status_host: str = DEFAULT_STATUS_HOST,
    status_port: int = DEFAULT_STATUS_PORT,
) -> None:
    """Block, handling `NAME_ADDRESS`/`SAVE_ADDRESS` messages until interrupted.

    Binds `host:port` for incoming requests, and reports save outcomes to
    `status_host:status_port` at `STATUS_ADDRESS` — open-stage-control's own
    OSC input by default, so a failed save shows up in the panel instead of
    disappearing silently.

    `host` defaults to loopback-only. This endpoint writes files to disk on
    receipt of an unauthenticated UDP message; widening it past localhost is
    a deliberate choice for the caller to make, never the default.
    """
    try:
        from pythonosc.dispatcher import Dispatcher
        from pythonosc.osc_server import BlockingOSCUDPServer
        from pythonosc.udp_client import SimpleUDPClient
    except ImportError:
        sys.exit(
            "python-osc not found. Install it into the tool's venv: "
            "pip install python-osc (see README: Environment, or the 'live' extra)"
        )

    status_client = SimpleUDPClient(status_host, status_port)
    last_name = ""

    def _report(message: str) -> None:
        print(message, file=sys.stderr)
        status_client.send_message(STATUS_ADDRESS, message)

    def _on_name(_osc_address: str, *args) -> None:
        nonlocal last_name
        last_name = str(args[0]) if args else ""

    def _on_save(_osc_address: str, *_args) -> None:
        # The save button carries no payload — see the module docstring for
        # why the name arrives as its own message instead.
        _report(handle_save(last_name, rig_path, out_dir, archive_dir, address))

    def _on_move(osc_address: str, *args) -> None:
        processor = osc_address[len(MOVE_ADDRESS_PREFIX):]
        if not processor:
            _report("move failed: no processor in address")
            return
        try:
            direction = int(float(args[0])) if args else 0
        except (TypeError, ValueError):
            _report(f"move failed: {processor} got a non-numeric direction")
            return
        if direction == 0:
            _report(f"move failed: {processor} got no direction")
            return
        try:
            _report(move_processor(processor, direction, address))
        except Exception as exc:  # noqa: BLE001 - a bad move must not kill the listener
            _report(f"move failed: {exc}")

    dispatcher = Dispatcher()
    dispatcher.map(NAME_ADDRESS, _on_name)
    dispatcher.map(SAVE_ADDRESS, _on_save)
    dispatcher.map(MOVE_ADDRESS_PREFIX + "*", _on_move)

    server = BlockingOSCUDPServer((host, port), dispatcher)
    print(
        f"sushi-rig listen: {NAME_ADDRESS} / {SAVE_ADDRESS} on {host}:{port}, "
        f"rig={rig_path}"
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
