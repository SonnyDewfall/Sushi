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

The record bar (#57) is wired the same way — `TAKE_NAME_ADDRESS`, then a bare
`RECORD_ADDRESS` or `STOP_ADDRESS` — and what they mean is decided in
`transport.py`, not here.

`python-osc` is a heavy, environment-specific dependency (matching how
`live.py` treats `elkpy`, per brief §3.1) — imported lazily so the rest of the
package stays importable and testable without it.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from .amp import AmpError, CARLA_OSC_PORT, parameter_message
from .live import DEFAULT_GRPC_ADDRESS, move_processor, refresh_panel
from .save import SaveError, save_any

if TYPE_CHECKING:
    from .transport import Session

DEFAULT_LISTEN_PORT = 24025
DEFAULT_STATUS_HOST = "127.0.0.1"
DEFAULT_STATUS_PORT = 8080  # open-stage-control's own OSC input (its HTTP port)
STATUS_ADDRESS = "/sushi-rig/status"
SAVE_ADDRESS = "/sushi-rig/save"
NAME_ADDRESS = "/sushi-rig/name"

# The record bar (#57), the same shape as the save bar: the take's name is its
# own message, and RECORD and STOP carry nothing. Its status is a separate
# address so a save's outcome and a take's do not overwrite each other on a
# panel showing both. See transport.py.
TAKE_NAME_ADDRESS = "/sushi-rig/take-name"
RECORD_ADDRESS = "/sushi-rig/record"
STOP_ADDRESS = "/sushi-rig/stop"
RECORD_STATUS_ADDRESS = "/sushi-rig/record-status"

# The processor is in the address rather than an argument, mirroring how Sushi
# itself addresses per-processor operations (/bypass/<processor>). That keeps
# the button's payload a bare int and avoids `preArgs` entirely — panel.py
# documents preArgs as not being re-evaluated per send for a button, which is
# what broke the first attempt at the save bar.
MOVE_ADDRESS_PREFIX = "/sushi-rig/move/"

# The amp's faders come here rather than going straight to Carla, because
# the listener has to see the values in order to save them — Carla cannot be
# asked what it currently holds. See amp.py.
AMP_ADDRESS_PREFIX = "/sushi-rig/amp/"


NO_SOURCE = (
    "save failed: this rig has no source to save from — the config carries no "
    "_meta.source and no config/src/<name>.yaml exists — so SAVE is off for it"
)


def _checkable(saved: Path) -> Path:
    """The config `verify` can check for what was just saved: the file itself
    for a config, or the compiled form of a rig of channels, written under a
    scratch directory beside the state file. Not into config/, and not where
    `prepare_rig` put the running rig's compiled config, which Sushi loaded and
    `--panel-config` reads: a check must not overwrite what is running."""
    import json

    from .paths import runtime_dir
    from .rigfile import emit_rig, load_rig

    if saved.suffix != ".yaml":
        return saved
    out = runtime_dir() / "checks"
    out.mkdir(parents=True, exist_ok=True)
    compiled = out / f"{saved.stem}.json"
    compiled.write_text(json.dumps(emit_rig(load_rig(saved)), indent=2) + "\n")
    return compiled


def handle_save(
    name: str,
    rig_path: Path | None,
    out_dir: Path,
    archive_dir: Path,
    address: str = DEFAULT_GRPC_ADDRESS,
    amp_model: str | None = None,
    amp_parameters: dict[str, float] | None = None,
    sushi_bin: str = "sushi",
) -> str:
    """Run `save_config` and return a human-readable outcome message.

    Deliberately never raises — an always-running listener must survive a bad
    request (a malformed name, Sushi unreachable, whatever), not go down with
    it. Split out from `serve()`'s OSC wiring so it's testable without a
    socket or `python-osc` installed at all.
    """
    if rig_path is None:
        return NO_SOURCE
    try:
        # A rig of channels is written back beside its source, with its
        # comments; a rig.yaml of tracks becomes a config. save_any decides.
        out_path = save_any(
            name, rig_path, out_dir, archive_dir, address,
            amp_model=amp_model, amp_parameters=amp_parameters,
        )
    except SaveError as exc:
        return f"save failed: {exc}"
    except SystemExit as exc:
        # Library code that still calls sys.exit (live._controller when elkpy
        # is missing) must not take the listener with it. A bare exit code is
        # no reason a player can act on.
        reason = exc.code if isinstance(exc.code, str) and exc.code else "a tool exited without saying why"
        return f"save failed: {reason}"
    except Exception as exc:  # noqa: BLE001 - keep the listener alive
        return f"save failed: unexpected error: {exc}"

    # The cheap checks on what was just written. Reported rather than raised:
    # the save has already happened, and losing it would be worse than a config
    # with a problem the player can now see and fix. Never fatal, for the same
    # reason the rest of this function never raises.
    try:
        from .verify import verify

        problems = verify(_checkable(out_path), sushi_bin, quick=True)
    except SystemExit as exc:
        return f"saved {out_path.name}, but it does not load: {exc}"
    except Exception as exc:  # noqa: BLE001 - keep the listener alive
        return f"saved {out_path.name} (could not check it: {exc})"
    if problems:
        return f"saved {out_path.name} — {len(problems)} problem(s): {problems[0]}"
    return f"saved {out_path.name}"


def amp_command(osc_address: str, args: tuple) -> tuple[str, list, str, float]:
    """What an amp message means: the Carla message to send, and the name and
    value to remember. Raises `AmpError` with the words for the panel when it
    means nothing, so the decision is testable without a socket.

    A value that is not a finite number is refused here, not sent: it would
    arrive as the top of the parameter's range (see `amp.clamp`).
    """
    name = osc_address[len(AMP_ADDRESS_PREFIX):]
    if not args:
        raise AmpError(f"{name} sent no value")
    try:
        value = float(args[0])
    except (TypeError, ValueError, OverflowError):
        raise AmpError(f"{name} got a non-numeric value") from None
    osc_path, payload = parameter_message(name, value)  # raises on NaN and infinity
    return osc_path, payload, name, payload[1]  # already clamped by parameter_message


def move_direction(args: tuple) -> int:
    """The direction a move message asks for; 0 when it carries none. Raises
    `ValueError` for anything that is not a finite number, infinity included:
    `int(float("inf"))` is an OverflowError, which the handler used not to
    catch, leaving the panel hearing nothing and a traceback in the log."""
    if not args:
        return 0
    try:
        return int(float(args[0]))
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("non-numeric direction") from exc


def serve(
    rig_path: Path | None,
    out_dir: Path,
    archive_dir: Path,
    address: str = DEFAULT_GRPC_ADDRESS,
    host: str = "127.0.0.1",
    port: int = DEFAULT_LISTEN_PORT,
    status_host: str = DEFAULT_STATUS_HOST,
    status_port: int = DEFAULT_STATUS_PORT,
    panel_config: Path | None = None,
    panel_out: Path | None = None,
    sushi_bin: str = "sushi",
    amp_model: str | None = None,
    amp_parameters: dict[str, float] | None = None,
    session: "Session | None" = None,
) -> None:
    """Block, handling `NAME_ADDRESS`/`SAVE_ADDRESS` messages until interrupted.

    Binds `host:port` for incoming requests, and reports save outcomes to
    `status_host:status_port` at `STATUS_ADDRESS` — open-stage-control's own
    OSC input by default, so a failed save shows up in the panel instead of
    disappearing silently.

    `host` defaults to loopback-only. This endpoint writes files to disk on
    receipt of an unauthenticated UDP message; widening it past localhost is
    a deliberate choice for the caller to make, never the default.

    `session` records takes for the record bar. Without one the record bar's
    messages are still answered, by `NoEngine`, which says why it cannot.
    """
    from .transport import Transport

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
    # What we last sent the amp. Not what Carla holds — see amp.py.
    amp_state: dict[str, float] = dict(amp_parameters or {})

    transport = Transport(session)

    def _report(message: str, status_address: str = STATUS_ADDRESS) -> None:
        print(message, file=sys.stderr)
        status_client.send_message(status_address, message)

    def _guarded(handler):
        """A handler that cannot end the listener.

        python-osc's server catches Exception from a handler and nothing else,
        so a SystemExit from library code (live._controller, emit before it
        grew EmitError) propagated out of serve_forever and the listener died
        with the save button: found by the audit. Every message that could do
        that is reported to the panel instead. KeyboardInterrupt still stops
        the listener, as it should.
        """
        def wrapped(address, *args):
            try:
                handler(address, *args)
            except KeyboardInterrupt:
                raise
            except BaseException as exc:  # noqa: BLE001 - see docstring
                _report(f"{address}: failed — {type(exc).__name__}: {exc}")
        return wrapped

    def _on_take_name(_osc_address: str, *args) -> None:
        transport.on_name(str(args[0]) if args else "")

    def _on_record(_osc_address: str, *_args) -> None:
        _report(transport.on_record(), RECORD_STATUS_ADDRESS)

    def _on_stop(_osc_address: str, *_args) -> None:
        _report(transport.on_stop(), RECORD_STATUS_ADDRESS)

    def _on_name(_osc_address: str, *args) -> None:
        nonlocal last_name
        last_name = str(args[0]) if args else ""

    def _on_save(_osc_address: str, *_args) -> None:
        # The save button carries no payload — see the module docstring for
        # why the name arrives as its own message instead.
        _report(handle_save(
            last_name, rig_path, out_dir, archive_dir, address,
            amp_model=amp_model, amp_parameters=amp_state, sushi_bin=sushi_bin,
        ))

    def _on_amp(osc_address: str, *args) -> None:
        """Forward an amp control to Carla, and remember what we sent.

        Remembering is the reason this goes through the listener at all. Carla's
        OSC is write-only from here, so the value we last sent is the only
        record there is of where the amp is set — and the save button needs it.
        """
        try:
            osc_path, payload, name, remembered = amp_command(osc_address, args)
        except AmpError as exc:
            _report(f"amp: {exc}")
            return
        try:
            SimpleUDPClient("127.0.0.1", CARLA_OSC_PORT).send_message(osc_path, payload)
        except Exception as exc:  # noqa: BLE001 - a dead amp must not kill the listener
            _report(f"amp: could not reach Carla ({exc})")
            return
        amp_state[name] = remembered

    def _on_move(osc_address: str, *args) -> None:
        processor = osc_address[len(MOVE_ADDRESS_PREFIX):]
        if not processor:
            _report("move failed: no processor in address")
            return
        try:
            direction = move_direction(args)
        except ValueError:
            _report(f"move failed: {processor} got a non-numeric direction")
            return
        if direction == 0:
            _report(f"move failed: {processor} got no direction")
            return
        try:
            outcome = move_processor(processor, direction, address)
        except Exception as exc:  # noqa: BLE001 - a bad move must not kill the listener
            _report(f"move failed: {exc}")
            return

        _report(outcome)
        if outcome.startswith("moved "):
            _rebuild_panel(processor)

    def _rebuild_panel(select_tab: str) -> None:
        """Regenerate the panel and have open-stage-control reload it, so the
        tabs end up in the rig's new order.

        Needs the panel arguments to have been supplied; without them a move
        still works, it just leaves the tabs stale — which is how this behaved
        before the panel could be refreshed at all.

        Never fatal. A move that succeeded followed by a panel that failed to
        refresh is a cosmetic problem; an exception here would take down the
        listener and with it the save button.
        """
        nonlocal last_name
        if panel_config is None or panel_out is None:
            return
        try:
            from .dump import dump_plugins
            from .live import get_live_bypass_state, get_live_parameter_info
            from .panel import build_osc_panel, has_record_bar
            from .amp import panel_amp
            from .probe import units_for_config
            from .paths import checkout_root

            # Whatever the panel being replaced had, it keeps: the record bar
            # is a choice made when the panel was made, not one for a reorder
            # to undo.
            record_bar = has_record_bar(Path(panel_out))
            panel = build_osc_panel(
                dump_plugins(panel_config, sushi_bin),
                get_live_parameter_info(address),
                port,
                get_live_bypass_state(address),
                units_for_config(panel_config),
                panel_amp(panel_config, checkout_root()),
                record_bar=record_bar,
            )
            Path(panel_out).write_text(json.dumps(panel, indent=2) + "\n")
            refresh_panel(str(panel_out), select_tab, status_host, status_port)
            # The reload empties both name fields, so the names held here go
            # too: SAVE or RECORD on an empty field must not use a name typed
            # before the reload.
            last_name = ""
            said = transport.on_panel_reloaded()
            if said:
                _report(said, RECORD_STATUS_ADDRESS)
        except Exception as exc:  # noqa: BLE001 - see docstring
            _report(f"panel refresh failed (the move still applied): {exc}")

    dispatcher = Dispatcher()
    for address, handler in (
        (NAME_ADDRESS, _on_name),
        (SAVE_ADDRESS, _on_save),
        (TAKE_NAME_ADDRESS, _on_take_name),
        (RECORD_ADDRESS, _on_record),
        (STOP_ADDRESS, _on_stop),
        (MOVE_ADDRESS_PREFIX + "*", _on_move),
        (AMP_ADDRESS_PREFIX + "*", _on_amp),
    ):
        dispatcher.map(address, _guarded(handler))

    server = BlockingOSCUDPServer((host, port), dispatcher)
    print(
        f"sushi-rig listen: {NAME_ADDRESS} / {SAVE_ADDRESS} / {RECORD_ADDRESS} on {host}:{port}, "
        f"rig={rig_path}"
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        # A take running when the listener stops is finished, not abandoned.
        said = transport.close()
        if said:
            _report(said, RECORD_STATUS_ADDRESS)
        server.server_close()
