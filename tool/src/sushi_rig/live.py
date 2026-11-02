"""Read and write live Sushi state over gRPC, via elkpy.

elkpy is a heavy, environment-specific dependency (brief §3.1) — imported
lazily inside each function, with a clear message if it's missing, so the
rest of the package stays importable and testable without it.

Two elkpy facts worth restating because they contradict what a first guess
would assume (confirmed empirically against Sushi 1.3.0 / elkpy API 1.2.0,
see MANIFEST.md "Open questions"):

- `parameters.get_parameter_value()` returns the **normalised** 0.0-1.0 value.
  `get_parameter_value_in_domain()` returns the **real-world** value — despite
  elkpy's own docstring claiming the opposite for the latter. `capture` uses
  `get_parameter_value` throughout.
- Graph-editing calls return a `SushiCommandResponse` (an `asyncio.Event`
  subclass). In a synchronous program, do not call `.wait()` on it — that
  returns a coroutine and never actually blocks. Poll `.is_set()` instead.

`ParameterInfo.automatable` distinguishes settable parameters from read-only
ones (meters, "Latency OUT"). Confirmed empirically: baking a captured value
for a non-automatable parameter into `initial_state` makes Sushi refuse to
load the config at all ("Failed to load the initial processor states.", exit
7) — not silently ignore it. `capture` must skip these.
"""

from __future__ import annotations

import contextlib
import threading

import sys
import time
from dataclasses import dataclass, field, replace
from typing import Any, Callable

from .spec import PROBE_TRACK

DEFAULT_GRPC_ADDRESS = "localhost:51051"


class LiveError(Exception):
    """A live call that Sushi did not carry out: it reported an error, or did
    not confirm in time. The change may or may not have been applied."""


class ElkpyMissing(LiveError):
    """elkpy is not installed. Not Sushi's doing, and not a reason to exit the
    process from a library: the CLI turns it into an exit (`cli.main`), and a
    long-running caller (the listener, the agent) reports it and carries on.
    """


# How long a strict `_wait` gives Sushi to confirm a command.
CONFIRM_TIMEOUT = 5.0


def _controller(address: str):
    try:
        from elkpy import sushicontroller as sc
    except ImportError:
        raise ElkpyMissing(
            "elkpy not found. Install it into the tool's venv: "
            "pip install elkpy (see README: Environment)"
        ) from None
    return sc.SushiController(address)


def close_controller(controller) -> None:
    """Close an elkpy `SushiController` so it lets go of what it holds now,
    not whenever the garbage collector next runs. Every close here goes
    through this.

    elkpy 1.3.0's own `close()` only asks its NotificationController's event
    loop to stop: it never waits for that loop's thread or closes the loop, so
    the loop's file descriptors (its selector and self-pipe, and the gRPC
    channel its pending task holds) outlive the controller. The
    sub-controllers also refer back to the controller, a cycle that only the
    cyclic collector breaks. A process that connects once a second -- the
    agent, under the web UI's poll of /live -- ran out of file descriptors
    within minutes ("OSError: [Errno 24] Too many open files", on the rig
    machine, 2026-11-02). Measured here: about three descriptors left per
    connect-and-close with nothing listening, none with this.

    So: elkpy's close, then wait (briefly) for the notification thread, cancel
    and finish what is left on its loop, close the loop, and break the cycle.
    A thread that will not stop in time is left, and its loop with it, rather
    than closed from under it. Reads elkpy's private attributes: checked
    against 1.3.0 only, and `test_live_close.py` says so.
    """
    import asyncio

    controller.close()
    notifications = getattr(controller, "notifications", None)
    thread = getattr(notifications, "notification_thread", None)
    loop = getattr(notifications, "loop", None)
    if notifications is None or getattr(notifications, "_async", True) or thread is None or loop is None:
        return
    thread.join(timeout=CLOSE_TIMEOUT)
    if thread.is_alive():
        return
    pending = [task for task in asyncio.all_tasks(loop) if not task.done()]
    for task in pending:
        task.cancel()
    if pending:
        loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
    loop.run_until_complete(loop.shutdown_asyncgens())
    loop.close()
    # elkpy's __del__ calls close() again, which would now find the loop
    # closed and raise: leave it nothing to do.
    notifications.tasks, notifications._async, notifications._parent = [], True, None
    for sub in list(vars(controller).values()):
        if sub is not notifications and hasattr(sub, "_parent") and hasattr(sub, "__dict__"):
            sub.__dict__.clear()


# How long `close_controller` waits for elkpy's notification thread to stop.
CLOSE_TIMEOUT = 2.0


def connect(address: str = DEFAULT_GRPC_ADDRESS):
    """A new controller, for a caller that shares one among several calls
    (the `controller=` parameters below) and closes it itself."""
    return _controller(address)


def _safe(fn, *args):
    try:
        return fn(*args)
    except Exception:  # noqa: BLE001 - optional data, absence is fine
        return None


def capture(address: str = DEFAULT_GRPC_ADDRESS, controller: Any = None) -> dict[str, Any]:
    """Read the full normalised parameter state of a running Sushi.

    Returns a dict of processor name to {"parameters": {...}, "program": int?,
    "bypassed": bool?, "properties": {...}?} (the brief's §5.2 shape, plus
    properties). Tracks are included alongside plugins, since tracks carry
    parameters too (gain, pan).

    Also returns `"tracks"`: `{track_name: [processor names, in chain order]}`.
    Processor *values* alone are not enough to describe a session, because
    pedal order is itself a tonal decision — a compressor before a drive is a
    different sound from one after it. Without this, reordering the chain live
    and then saving would write the new values under the old order and lose the
    change silently.

    The order costs nothing to collect: `get_track_processors` already returns
    chain order, and this function already walks it. The key is additive, so
    state files written before it existed stay valid and simply don't reorder.
    """
    # `controller` lets a caller that also wants something else share one
    # connection (`read_rig`); it then stays the caller's to close.
    own = controller is None
    controller = controller or _controller(address)
    processors: dict[str, Any] = {}
    track_order: dict[str, list[str]] = {}
    try:
        for track in controller.audio_graph.get_all_tracks():
            if track.name == PROBE_TRACK:
                # Left by a selftest killed mid-probe (probes.py): no part of
                # any rig, so left out here, the one place every reader of a
                # capture goes through. Kept in, SAVE refused the capture as
                # "a different rig" and the save was lost under the wrong cause
                # (found by review, 2026-11-02). It is inaudible — its inputs
                # and outputs are ones the rig leaves free — so it is reported,
                # not a reason to refuse.
                print(
                    f"warning: {PROBE_TRACK!r} is still in Sushi, left by a selftest "
                    "that did not finish; it is no part of the rig and is left out "
                    "of this capture. `sushi-rig restart` removes it",
                    file=sys.stderr,
                )
                continue
            # The track itself is captured first, then its plugins in chain
            # order. `is_track` matters below: a track's bypass is not just its
            # own state.
            targets = [(track.id, track.name, True)]
            chain = list(controller.audio_graph.get_track_processors(track.id))
            track_order[track.name] = [proc.name for proc in chain]
            for proc in chain:
                targets.append((proc.id, proc.name, False))

            for proc_id, proc_name, is_track in targets:
                param_infos = controller.parameters.get_processor_parameters(proc_id)
                if not param_infos:
                    print(f"warning: {proc_name!r} returned no parameters", file=sys.stderr)

                params: dict[str, float] = {}
                for param in param_infos:
                    # Non-automatable parameters are read-only (meters,
                    # "Latency OUT"). Sushi refuses to load a config whose
                    # initial_state tries to set one, so there's no point
                    # capturing it.
                    if not getattr(param, "automatable", True):
                        continue
                    params[param.name] = controller.parameters.get_parameter_value(
                        proc_id, param.id
                    )

                entry: dict[str, Any] = {"parameters": params}

                # Properties too: a send's destination_name is one, and it is
                # the compiled rig's most important setting. Without it here
                # `verify` could not see whether a send reached its return,
                # and a channel silent in the monitors passed as applied (the
                # audit's finding; #78 had claimed otherwise). Read with
                # `_safe` so a Sushi that cannot answer leaves the key absent,
                # which `verify.diff_state` reports as unchecked rather than
                # as a pass.
                #
                # Recorded for checking, not for writing back: which of a
                # plugin's properties can be set at load is not something the
                # listing says, and an IR loader's path read back as "" is the
                # kind of value Sushi refuses a config over. `save` strips them
                # before `emit`, and a compiled rig writes only its own.
                listing = (controller.parameters.get_track_properties if is_track
                           else controller.parameters.get_processor_properties)
                infos = _safe(listing, proc_id)
                if infos is None:
                    print(
                        f"warning: {proc_name!r}: properties could not be listed, so "
                        "verify will report them as unchecked",
                        file=sys.stderr,
                    )
                else:
                    # A property listed but unreadable is kept as None, so verify
                    # says "could not be read" rather than "not a property".
                    entry["properties"] = {
                        info.name: _safe(controller.parameters.get_property_value, proc_id, info.id)
                        for info in infos
                    }

                # A program is only recorded for a processor we captured no
                # parameters for. Sushi applies a program *over* the parameters
                # in the same initial_state entry, so recording both silently
                # destroys the captured values on the next load — a saved EQ
                # came back as the plugin's factory preset every time, because
                # the preset index was written alongside the very values it
                # overwrites.
                #
                # Isolated against a running Sushi: with the program key the
                # parameters are ignored entirely; remove only that key and they
                # all apply. Captured parameters are the actual observed state,
                # so they are the better record when there is a choice.
                #
                # Programs still matter where there is no choice — a preset
                # convolver exposes presets and no automatable parameters at
                # all, and then the program is the only handle on its state.
                if not params:
                    info_obj = _safe(
                        controller.audio_graph.get_processor_info, proc_id
                    )
                    if info_obj is not None and getattr(info_obj, "program_count", 0) > 0:
                        program = _safe(
                            controller.programs.get_processor_current_program, proc_id
                        )
                        if program is not None:
                            entry["program"] = program

                # Deliberately NOT captured for a track. Setting `bypassed` on
                # a track in initial_state cascades to every processor on it,
                # so a single `"bypassed": false` on the track silently undoes
                # the per-plugin bypass flags in the same file — which is
                # exactly what happened: a saved config recorded four bypassed
                # pedals correctly and loaded with all of them active.
                #
                # Isolated against a running Sushi. With a track entry carrying
                # `bypassed: false` the plugin's own flag is wiped, whether that
                # entry comes first or last; remove just that one key and the
                # plugin flags apply. Everything else in the file — every
                # parameter value, and the chain order — applied correctly
                # throughout, which is what made it look like bypass was simply
                # unsupported.
                #
                # Nothing needs a track's bypass state today, and writing it
                # destroys state we do need.
                if not is_track:
                    bypassed = _safe(
                        controller.audio_graph.get_processor_bypass_state, proc_id
                    )
                    if bypassed is not None:
                        entry["bypassed"] = bool(bypassed)

                processors[proc_name] = entry
    finally:
        if own:
            close_controller(controller)

    return {"processors": processors, "tracks": track_order}


def get_live_parameter_info(
    address: str = DEFAULT_GRPC_ADDRESS, controller: Any = None
) -> dict[str, dict[str, dict]]:
    """Per-processor, per-parameter {"automatable", "value", "min_domain_value",
    "max_domain_value"}.

    `value` is the current normalised value, i.e. whatever Sushi actually has
    loaded right now — the plugin's own default if nothing has changed it
    yet, or a baked-in `initial_state` value otherwise. This exists for
    `panel.py`: a fader's starting value must come from here, never from a
    guessed constant like 0.5. Proven necessary the hard way — many LSP gain
    parameters are linear amplitude multipliers with domains up to 1000
    (`Input gain`, `Output gain`, `Makeup gain`) or 63 (every graphic EQ band
    gain), where the real default sits near the *bottom* of the range. `0.5`
    normalised on `Input gain`'s `[0, 1000]` domain is real-world `500` —
    500x amplification, instant clipping — for a fader whose sane starting
    point is `~0.001` normalised (real-world `1.0`, unity gain).

    The domain bounds are for a second, related problem: even with a safe
    starting value, dragging a fader across a domain that wide is nearly
    unusable — the entire musically useful ±12dB range of a graphic EQ band
    (domain `[~0.016, ~63]`) occupies the bottom ~6% of the fader's linear
    travel. `panel.py` uses these bounds to decide which faders need
    `logScale`.
    """
    own = controller is None
    controller = controller or _controller(address)
    result: dict[str, dict[str, dict]] = {}
    try:
        for track in controller.audio_graph.get_all_tracks():
            targets = [(track.id, track.name)]
            for proc in controller.audio_graph.get_track_processors(track.id):
                targets.append((proc.id, proc.name))

            for proc_id, proc_name in targets:
                params: dict[str, dict] = {}
                for param in controller.parameters.get_processor_parameters(proc_id):
                    params[param.name] = {
                        "automatable": bool(getattr(param, "automatable", True)),
                        "value": controller.parameters.get_parameter_value(proc_id, param.id),
                        "min_domain_value": getattr(param, "min_domain_value", 0.0),
                        "max_domain_value": getattr(param, "max_domain_value", 1.0),
                    }
                result[proc_name] = params
    finally:
        if own:
            close_controller(controller)

    return result


def get_live_bypass_state(address: str = DEFAULT_GRPC_ADDRESS) -> dict[str, bool]:
    """Per-processor bypass state, as `{processor_name: bypassed}`.

    Separate from `get_live_parameter_info` rather than folded into it,
    because that function's values are keyed by parameter name and callers
    iterate them as parameters — bypass is a property of the processor, not
    one of its parameters, and Sushi treats it that way too.

    This exists for `panel.py`'s bypass toggles. They need to *reflect* the
    current state rather than impose one: a panel opening must not silently
    un-bypass a pedal the saved config deliberately had switched off.

    Bypass is a host-level facility, not a plugin feature — Sushi provides it
    for every processor regardless of whether the plugin has its own bypass
    parameter. That matters because plugin-provided bypass is unreliable to
    build on: only some plugins have it (Guitarix does, MDA doesn't), and
    where it exists its semantics can be undiscoverable — the Guitarix
    pedals expose `BYPASS` with a default of 1.0, range 0-1 and no scale
    points, so nothing in the metadata says whether 1 means active or
    bypassed.
    """
    controller = _controller(address)
    result: dict[str, bool] = {}
    try:
        for track in controller.audio_graph.get_all_tracks():
            for proc in controller.audio_graph.get_track_processors(track.id):
                state = _safe(
                    controller.audio_graph.get_processor_bypass_state, proc.id
                )
                if state is not None:
                    result[proc.name] = bool(state)
    finally:
        close_controller(controller)

    return result


@contextlib.contextmanager
def _connection(address: str, controller: Any = None):
    """A controller for one piece of work: the caller's if it passed one (then
    it stays theirs to close), otherwise a new one closed afterwards.

    The agent passes its own so that reading what a request names and setting it
    share one connection -- each construction builds a dozen sub-controllers,
    makes an RPC and starts a notification thread.
    """
    if controller is not None:
        yield controller
        return
    controller = _controller(address)
    try:
        yield controller
    finally:
        close_controller(controller)


def set_live_parameter(
    processor: str, parameter: str, value: float,
    address: str = DEFAULT_GRPC_ADDRESS, controller: Any = None,
) -> None:
    """Set one parameter of a running Sushi, by processor and parameter name,
    to a *normalised* (0.0-1.0) value.

    Names, not ids, as everywhere here: the ids are looked up on the same
    connection that sets the value. An unknown name is Sushi's refusal
    (elkpy raises `SushiNotFoundError`) and is left to propagate, because a
    caller that wants it refused before this runs has `read_live_parameter`.
    Raises `LiveError` if Sushi reports an error or does not confirm.

    Unverified against a live Sushi when written: `get_processor_id` is used
    by `push` and `get_parameter_id` / `set_parameter_value` are read from
    elkpy 1.3.0's source, not run.
    """
    with _connection(address, controller) as c:
        proc_id = c.audio_graph.get_processor_id(processor)
        param_id = c.parameters.get_parameter_id(proc_id, parameter)
        _wait(c.parameters.set_parameter_value(proc_id, param_id, float(value)), strict=True)


def set_live_bypass(
    processor: str, bypassed: bool,
    address: str = DEFAULT_GRPC_ADDRESS, controller: Any = None,
) -> None:
    """Bypass or un-bypass one processor of a running Sushi, by name.

    Not for a track: a track's bypass cascades to every processor on it (see
    `capture`), which is a different operation from switching one pedal.
    Raises `LiveError` if Sushi reports an error or does not confirm.
    Unverified against a live Sushi when written, as `set_live_parameter`.
    """
    with _connection(address, controller) as c:
        proc_id = c.audio_graph.get_processor_id(processor)
        _wait(c.audio_graph.set_processor_bypass_state(proc_id, bool(bypassed)), strict=True)


def _walk_chains(controller) -> tuple[dict[str, list[str]], dict[str, int]]:
    """`({track: [processor names in chain order]}, {name: id})` for every
    track and processor: one call for the tracks and one per track, however many
    parameters anything has. What the targeted reads below start from."""
    chains: dict[str, list[str]] = {}
    ids: dict[str, int] = {}
    for track in controller.audio_graph.get_all_tracks():
        ids[track.name] = track.id
        procs = list(controller.audio_graph.get_track_processors(track.id))
        chains[track.name] = [proc.name for proc in procs]
        for proc in procs:
            ids[proc.name] = proc.id
    return chains, ids


def read_live_parameter(
    processor: str, parameter: str,
    address: str = DEFAULT_GRPC_ADDRESS, controller: Any = None,
) -> dict[str, Any]:
    """What a request to set one parameter needs to be decided, without walking
    the rig's parameters:

        {"chains": {track: [processors]},
         "parameters": {name: {"automatable": bool}} | None,
         "value": float | None}

    `parameters` is that one processor's (a track counts: it has gain and pan)
    and is None if the rig has no such processor; `value` is the named
    parameter's current normalised value, and is None unless it exists and is
    automatable. The cost is the tracks, the processor's parameter listing and
    one value, not every value of every processor as `get_live_parameter_info`.
    """
    with _connection(address, controller) as c:
        chains, ids = _walk_chains(c)
        reading: dict[str, Any] = {"chains": chains, "parameters": None, "value": None}
        if processor in ids:
            infos = {
                info.name: info
                for info in c.parameters.get_processor_parameters(ids[processor])
            }
            reading["parameters"] = {
                name: {"automatable": bool(getattr(info, "automatable", True))}
                for name, info in infos.items()
            }
            info = infos.get(parameter)
            if info is not None and reading["parameters"][parameter]["automatable"]:
                reading["value"] = c.parameters.get_parameter_value(ids[processor], info.id)
        return reading


def read_live_bypass(
    processor: str, address: str = DEFAULT_GRPC_ADDRESS, controller: Any = None
) -> dict[str, Any]:
    """`{"chains": ..., "bypassed": bool | None}`: the chains, and the named
    processor's bypass state (None for a track, an unknown name, or a state
    Sushi would not give). One bypass read.
    """
    with _connection(address, controller) as c:
        chains, ids = _walk_chains(c)
        bypassed = None
        if processor in ids and processor not in chains:
            state = _safe(c.audio_graph.get_processor_bypass_state, ids[processor])
            bypassed = None if state is None else bool(state)
        return {"chains": chains, "bypassed": bypassed}


def read_rig(address: str = DEFAULT_GRPC_ADDRESS, controller: Any = None) -> dict[str, Any]:
    """`capture`'s answer plus `"parameter_info"`, on one connection."""
    with _connection(address, controller) as c:
        state = capture(address, c)
        state["parameter_info"] = get_live_parameter_info(address, c)
        return state


# How long to wait after `/SESSION/OPEN` before re-selecting the tab. Long
# enough for open-stage-control to rebuild its widget tree (measured well under
# half a second for a seven-plugin panel), short enough not to be noticed.
TAB_RESELECT_DELAY = 0.75


def _start_timer(delay: float, action: Callable[[], None]) -> None:
    """Run `action` after `delay` seconds, on a thread of its own.

    Deliberately NOT a daemon thread. A daemon dies with the process, and a
    one-shot caller (`sushi-rig panel`, a script) exits long before the delay is
    up — the reload lands, the re-select never goes out. Non-daemon costs a
    long-lived caller at most `delay` on shutdown.
    """
    threading.Timer(delay, action).start()


def refresh_panel(
    panel_path: str,
    select_tab: str | None = None,
    host: str = "127.0.0.1",
    port: int = 8080,
    tab_delay: float = TAB_RESELECT_DELAY,
    schedule: Callable[[float, Callable[[], None]], None] | None = None,
) -> None:
    """Tell a running open-stage-control to reload `panel_path`.

    Uses open-stage-control's remote-control OSC API, which is documented in
    the app's own bundled docs (`docs/docs/remote-control`) rather than
    anywhere obvious in its source — this project previously concluded from
    grepping the minified client that no remote reload existed, and wrote that
    wrong conclusion into a status message and a comment.

    `/SESSION/OPEN` reloads the whole session, which resets the visible tab to
    the first one. `/TABS` then re-selects the tab the caller came from,
    otherwise moving the reverb would bounce you to the compressor tab every
    time.

    The two messages cannot be sent back to back. `/SESSION/OPEN` returns
    immediately while the client is still tearing down and rebuilding the
    widget tree, and a `/TABS` that lands during that window is silently
    dropped — verified against the running rig, where the reload worked but the
    selection always fell back to the first tab. Sent on its own once the
    rebuild has settled, the very same message selects the tab correctly. So
    the re-select is deferred by `tab_delay`, on a timer rather than a sleep so
    the caller (the OSC listener, which must stay responsive) isn't blocked.
    `schedule(delay, action)` is how that deferral is carried out; it is a
    parameter so a test can run the action itself instead of waiting out the
    wall clock, and defaults (looked up at call time, so a test can replace
    `_start_timer`) to a real `threading.Timer`.

    Fire-and-forget: OSC is unacknowledged, so there is nothing to await and no
    confirmation to check. A panel that failed to reload is a cosmetic problem;
    the caller's actual work has already succeeded by this point.
    """
    from pythonosc.udp_client import SimpleUDPClient

    client = SimpleUDPClient(host, port)
    client.send_message("/SESSION/OPEN", str(panel_path))
    if not select_tab:
        return

    def _reselect() -> None:
        try:
            SimpleUDPClient(host, port).send_message("/TABS", select_tab)
        except Exception:  # noqa: BLE001,S110 - nobody is listening on this thread
            pass

    if tab_delay <= 0:
        _reselect()
    else:
        (schedule or _start_timer)(tab_delay, _reselect)


def plan_move(chain: list[str], processor: str, direction: int) -> dict[str, Any] | None:
    """Where `processor` should land to move one step through `chain`.

    Pure, so the position maths is testable without a running Sushi — it is
    fiddly enough to be worth pinning, and getting it wrong silently rearranges
    someone's signal chain.

    Returns `None` when the move is a no-op (already at that end, or the
    processor isn't on the track), otherwise the `move_processor_on_track`
    arguments that achieve it: `{"before": <processor name or None>,
    "add_to_back": bool}`.

    Sushi's API expresses position as "before this processor" or "at the back",
    so moving *later* past the final element has to become `add_to_back`
    rather than "before" anything.
    """
    if processor not in chain:
        return None
    index = chain.index(processor)

    if direction < 0:
        if index == 0:
            return None
        return {"before": chain[index - 1], "add_to_back": False}

    if index >= len(chain) - 1:
        return None
    if index + 1 == len(chain) - 1:
        # The neighbour is last, so "after it" is the end of the chain.
        return {"before": None, "add_to_back": True}
    return {"before": chain[index + 2], "add_to_back": False}


@dataclass(frozen=True)
class Move:
    """What a request to move a processor found and did, as data, so no caller
    has to read the panel's status line to learn how it went.

    `outcome` is one of:
      "planned"   -- found, and not already at that end of its chain; the
                     command is worked out and nothing has been sent
      "moved"     -- Sushi confirmed the command
      "unchanged" -- already at that end of its chain; nothing was sent
      "absent"    -- on no track; nothing was sent
      "protected" -- it is, or it would swap with, a channel's send or the
                     master's return (`protected_processor`); nothing was sent
    `chain` is the chain it was on before the move, in order (None if absent);
    `chains` is every track's, for a refusal to name; `why` says what is
    protected, for a "protected" move.
    """

    outcome: str
    processor: str
    direction: int
    chain: list[str] | None
    chains: dict[str, list[str]]
    # The ids `move_processor_on_track` takes, from the same walk the plan was
    # made on: (processor, track, before, add_to_back). Only a "planned" or
    # "moved" move has them.
    command: tuple[int, int, int, bool] | None = field(default=None, repr=False, compare=False)
    why: str | None = field(default=None, compare=False)

    @property
    def message(self) -> str:
        """The panel's status line."""
        if self.outcome == "absent":
            return f"no processor named {self.processor!r} on any track"
        if self.outcome == "unchanged":
            edge = "first" if self.direction < 0 else "last"
            return f"{self.processor} is already {edge} in the chain"
        where = "earlier" if self.direction < 0 else "later"
        if self.outcome == "protected":
            return f"{self.processor} cannot move {where}: {self.why}"
        if self.outcome == "planned":
            return f"{self.processor} would move {where}"
        # Deliberately says only what happened. It used to append
        # "regenerate the panel to reorder tabs", which was both a chore to
        # read on every move and based on a wrong belief that
        # open-stage-control could not be told to reload -- see
        # refresh_panel above. The listener now refreshes the panel itself.
        return f"moved {self.processor} {where}"


def decide_move(
    chains: dict[str, list[str]], ids: dict[str, int], processor: str, direction: int,
    uids: dict[str, str | None] | None = None,
) -> Move:
    """The decision half of a move, pure: given the tracks and ids a walk found
    (`_walk_chains`), is `processor` absent, already at that end, protected, or
    to be moved -- and if so by which command. Nothing here talks to Sushi.

    A move is a swap with a neighbour, so it is "protected" when either is a
    send or a return (`protected_processor`, by name and, with `uids`, by the
    uid Sushi reports): a pedal moved behind its channel's send is not heard,
    and the send or return moved is the compiled routing broken (found by
    review; it was allowed).
    """
    track = next((t for t, chain in chains.items() if processor in chain), None)
    if track is None:
        return Move("absent", processor, direction, None, chains)
    chain = chains[track]
    target = plan_move(chain, processor, direction)
    if target is None:
        return Move("unchanged", processor, direction, chain, chains)
    neighbour = chain[chain.index(processor) + (-1 if direction < 0 else 1)]
    for name in (processor, neighbour):
        why = protected_processor(track, name, (uids or {}).get(name))
        if why:
            return Move("protected", processor, direction, chain, chains,
                        why=f"{why}, which stays where the compiler put it")
    before = ids[target["before"]] if target["before"] else 0
    return Move(
        "planned", processor, direction, chain, chains,
        (ids[processor], ids[track], before, bool(target["add_to_back"])),
    )


def plan_live_move(
    processor: str, direction: int,
    address: str = DEFAULT_GRPC_ADDRESS, controller: Any = None, uids: bool = False,
) -> Move:
    """Walk the tracks once and decide (`decide_move`); sends nothing. A "planned"
    move is handed to `send_live_move` as it is, so the command sent is the one
    decided on, not one worked out again from a second walk.

    With `uids`, Sushi's session is read too (`session_uids`), so a send or
    return is known by its uid and not only its name: the agent asks for that.
    The panel's listener does not, and keeps the name rule alone, because the
    session read is not yet verified against Sushi and the listener's moves
    worked without it.
    """
    with _connection(address, controller) as c:
        chains, ids = _walk_chains(c)
        known = session_uids(c) if uids else None
        return decide_move(chains, ids, processor, direction, known)


def send_live_move(
    move: Move, address: str = DEFAULT_GRPC_ADDRESS, controller: Any = None,
) -> Move:
    """Send a "planned" move and return it as "moved" once Sushi confirms.
    Raises `LiveError` if Sushi reports an error or does not confirm (the move
    may or may not have happened), and `ValueError` for a move that was not
    planned -- sending is not something to do by guess.

    The ids are those of the walk the plan was made on. If the chain was
    rearranged since (the panel's listener also moves processors, and is not
    behind the agent's lock) Sushi still gets a well-formed command about the
    same processor, and either applies it or refuses it; it is not re-planned.
    """
    if move.outcome != "planned" or move.command is None:
        raise ValueError(f"only a planned move can be sent, not {move.outcome!r}")
    processor_id, track_id, before_id, add_to_back = move.command
    with _connection(address, controller) as c:
        response = c.audio_graph.move_processor_on_track(
            processor_id, track_id, track_id, before_id, add_to_back,
        )
        # Strict: "moved" is said only of a move Sushi confirmed.
        _wait(response, strict=True)
    return replace(move, outcome="moved")


def move_live_processor(
    processor: str, direction: int,
    address: str = DEFAULT_GRPC_ADDRESS, controller: Any = None,
) -> Move:
    """Move `processor` one step earlier (direction < 0) or later through its
    track's chain, and say what happened as a `Move`: one walk of the tracks,
    one decision on what that walk found, at most one command. Raises
    `LiveError` if Sushi reports an error or does not confirm, in which case
    the move may or may not have happened.

    Pedal order is a tonal decision, so this exists to let it be tried by ear
    rather than by editing yaml and relaunching. `emit` persists whatever order
    results, via the `tracks` key `capture` records.
    """
    with _connection(address, controller) as c:
        move = plan_live_move(processor, direction, address, c)
        return send_live_move(move, address, c) if move.outcome == "planned" else move


def _wait(
    response: Any, timeout: float | None = None, interval: float = 0.02, strict: bool = False
) -> None:
    """Poll a SushiCommandResponse until Sushi confirms the command.

    Lenient by default, for `push`, which builds a whole rig and carries on past
    a slow answer with a warning. With `strict`, a command Sushi reports as
    failed, or does not confirm within `timeout` (`CONFIRM_TIMEOUT`), raises
    `LiveError` instead: the live setters and `move_live_processor` use it, because
    their callers report the change as made.

    Not `.wait()` — see the module docstring. `is_set()` is a plain attribute
    read on `asyncio.Event`, safe to poll from a thread other than the one
    running elkpy's event loop.
    """
    is_set = getattr(response, "is_set", None)
    if is_set is None:
        return
    if timeout is None:
        timeout = CONFIRM_TIMEOUT
    deadline = time.monotonic() + timeout
    while not is_set():
        if time.monotonic() > deadline:
            if strict:
                raise LiveError(
                    f"Sushi did not confirm the command within {timeout:g}s, so it "
                    "may or may not have been applied"
                )
            print(
                f"warning: command not confirmed within {timeout}s, continuing anyway",
                file=sys.stderr,
            )
            return
        time.sleep(interval)
    if getattr(response, "error", False):
        if strict:
            raise LiveError("Sushi reported an error completing the command")
        print("warning: Sushi reported an error completing the command", file=sys.stderr)


_PLUGIN_TYPE_NAMES = {"internal": "INTERNAL", "vst2x": "VST2X", "vst3x": "VST3X", "lv2": "LV2"}


def push(rig, address: str = DEFAULT_GRPC_ADDRESS) -> None:
    """Build the rig's tracks and processors in a running Sushi.

    Uses `create_processor_on_track`'s real elkpy 1.2.0 keyword names
    (`processor_type`, `before_processor`) — the brief's own reference
    (`plugin_type`, `before_processor_id`) is for a different elkpy release.
    """
    from elkpy import sushi_info_types as info

    problems = rig.validate()
    if problems:
        sys.exit("rig spec invalid:\n  " + "\n  ".join(problems))

    controller = _controller(address)
    try:
        for track in rig.tracks:
            response = controller.audio_graph.create_track(track.name, track.channels)
            _wait(response)
            track_id = controller.audio_graph.get_processor_id(track.name)
            print(f"track {track.name!r} -> id {track_id}")

            for plugin in track.plugins:
                plugin_type = getattr(info.PluginType, _PLUGIN_TYPE_NAMES[plugin.type])
                response = controller.audio_graph.create_processor_on_track(
                    name=plugin.name,
                    uid=plugin.uid or "",
                    path=plugin.path or plugin.uri or "",
                    processor_type=plugin_type,
                    track_id=track_id,
                    before_processor=track_id,
                    add_to_back=True,
                )
                _wait(response)
                print(f"  + {plugin.name!r} ({plugin.uri or plugin.path})")
    finally:
        close_controller(controller)


# --------------------------------------------------------------------------- #
# Adding and removing a processor (#32), for the agent's /live/add and
# /live/remove. The decisions -- where it goes, what may be removed, whether the
# read-back confirms it -- are the agent's (`agent.check_add`, `check_remove`,
# `confirm_chain`), and pure; these only read and send.
#
# NOT RUN AGAINST A LIVE SUSHI. No Sushi was available when they were written
# (2026-11-02). The calls are read from elkpy 1.3.0's installed source:
#
#   AudioGraphController.create_processor_on_track(
#       self, name: str, uid: str, path: str,
#       processor_type: info_types.PluginType, track_id: int,
#       before_processor: int, add_to_back: bool) -> SushiCommandResponse
#   AudioGraphController.delete_processor_from_track(
#       self, processor: int, track: int) -> SushiCommandResponse
#   SessionController.get_session(self)   # the SaveSession RPC: a SessionState
#
# `push` already calls `create_processor_on_track` with these keyword names
# (written against elkpy 1.2.0, where they are the same). PluginType.LV2 is 4
# in `sushi_info_types`. What is unverified: that Sushi 1.3.0 places a new
# processor as `before_processor` says when `add_to_back` is false, that the
# chain read straight after the confirmation already shows the change, and that
# SaveSession's `PluginClass.path` holds an LV2 plugin's URI. That last is
# inferred: the create request's field is also `path`, which elkpy documents as
# "the path to the processor library (for vst2 or vst3 processors) or the URI
# of an installed lv2 plugin", and a saved session must be able to recreate
# what it saved. Sushi 1.3.0's own source was not read for it.
# --------------------------------------------------------------------------- #

def protected_processor(track: str, processor: str, uid: str | None = None) -> str | None:
    """What makes `processor` on `track` the compiler's, or None: one rule for
    the agent's add, remove and move, and the listener's move.

    A channel's send is `<track>_send` (`rigfile.SEND_SUFFIX`) or any processor
    Sushi reports with the send's uid; the master's return is `master_return`
    on `master`, or the return's uid. The uid is the stronger of the two -- a
    send renamed by hand is still the send -- and the name serves where no uid
    was read. Pure.
    """
    # Here, not at the top: rigfile imports save, which imports this module.
    from .rigfile import MASTER, RETURN, RETURN_UID

    if is_send(track, processor, uid):
        return f"{processor!r} is {track!r}'s send"
    if uid == RETURN_UID or (track == MASTER and processor == RETURN):
        return f"{processor!r} is the master's return"
    return None


def is_send(track: str, processor: str, uid: str | None = None) -> bool:
    """Whether `processor` on `track` is the channel's send: what an add goes
    in front of, and `protected_processor`'s send half, so the two cannot
    disagree about which processor is the send."""
    from .rigfile import SEND_SUFFIX, SEND_UID

    return uid == SEND_UID or processor == track + SEND_SUFFIX


def read_live_chains(
    address: str = DEFAULT_GRPC_ADDRESS, controller: Any = None, uids: bool = False,
) -> dict[str, Any]:
    """`{"chains": {track: [processor names]}, "ids": {name: id}}`, from one
    walk of the tracks (`_walk_chains`), and with `uids` `"uids": {name: uid}`
    from Sushi's session (`session_uids`): what an add is decided on, which
    needs to know the send; and, without it, the read-back that confirms an
    add or a remove."""
    with _connection(address, controller) as c:
        chains, ids = _walk_chains(c)
        reading: dict[str, Any] = {"chains": chains, "ids": ids}
        if uids:
            reading["uids"] = session_uids(c)
        return reading


def session_uids(controller) -> dict[str, str | None]:
    """`{processor name: uid or None}` for everything Sushi's session lists."""
    return {name: plugin["uid"] for name, plugin in _session_plugins(controller).items()}


def _session_plugins(controller) -> dict[str, dict[str, Any]]:
    """`{processor name: {"track", "uri", "uid", "type"}}` from Sushi's session,
    for every processor on a chain: the one walk of it, for the add, the
    remove and the web UI.

    The session is the only place Sushi's gRPC API says which plugin a running
    processor is: `ProcessorInfo` has an id, a label and a name, and nothing
    that names the plugin (elkpy 1.3.0's `sushi_rpc.proto`, `ProcessorInfo`).
    `SessionState.tracks[].processors[]` is a `PluginClass` with `name`, `uid`,
    `path` and `type`; its `uid` is how a send or return is known whatever it
    is called. A save of the whole session is heavier than any other read
    here, and is paid by the agent's add, remove and move, which are a
    person's gestures, and by the web UI when the processors on the rig
    change, never by a fader's stream of parameter changes. If Sushi 1.3.0
    does not answer SaveSession (unverified), those fail loudly (502) rather
    than fall back to names alone.
    """
    from elkpy import sushi_info_types as info

    plugins: dict[str, dict[str, Any]] = {}
    session = controller.session.get_session()
    for track in getattr(session, "tracks", None) or []:
        for plugin in getattr(track, "processors", None) or []:
            try:
                kind = info.PluginType(plugin.type.type).name.lower()
            except (AttributeError, ValueError):
                kind = None
            plugins[plugin.name] = {
                "track": getattr(track, "name", None), "uri": plugin.path or None,
                "uid": plugin.uid or None, "type": kind,
            }
    return plugins


def read_live_plugins(address: str = DEFAULT_GRPC_ADDRESS, controller: Any = None) -> dict[str, dict[str, Any]]:
    """`_session_plugins` on a connection: which plugin each processor is, so
    the web UI (#31) can show it with its profile. The UI asks when the
    processors or the rig change, not on every poll. Not run against a live
    Sushi, as for the add and the remove."""
    with _connection(address, controller) as c:
        return _session_plugins(c)


def read_live_removal(
    processor: str, address: str = DEFAULT_GRPC_ADDRESS, controller: Any = None,
) -> dict[str, Any]:
    """`{"chains", "ids", "uids", "plugin"}`: a walk, and for a processor found
    on a chain Sushi's session (`_session_plugins`): every processor's uid and
    this one's plugin class (None if the session does not list it). The
    session is read only when there is something to remove, so a refused
    request does not pay for it."""
    with _connection(address, controller) as c:
        chains, ids = _walk_chains(c)
        on_a_chain = any(processor in chain for chain in chains.values())
        plugins = _session_plugins(c) if on_a_chain else {}
        return {
            "chains": chains, "ids": ids,
            "uids": {name: plugin["uid"] for name, plugin in plugins.items()},
            "plugin": plugins.get(processor),
        }


def add_live_processor(
    name: str, uri: str, track_id: int, before_id: int, add_to_back: bool,
    address: str = DEFAULT_GRPC_ADDRESS, controller: Any = None,
) -> None:
    """Create LV2 plugin `uri` as processor `name` on the track with `track_id`,
    in front of the processor with `before_id`, or at the back. Strict: raises
    `LiveError` if Sushi reports an error or does not confirm, in which case it
    may or may not have been created. Sushi looks the URI up on its own
    `LV2_PATH` (`plugins/`); a URI it cannot find is its refusal.

    `before_processor` is 0 when `add_to_back` is set, as `send_live_move`
    sends it: Sushi reads the position from `add_to_back` first.
    """
    from elkpy import sushi_info_types as info

    with _connection(address, controller) as c:
        response = c.audio_graph.create_processor_on_track(
            name=name, uid="", path=uri, processor_type=info.PluginType.LV2,
            track_id=track_id, before_processor=before_id, add_to_back=add_to_back,
        )
        _wait(response, strict=True)


def remove_live_processor(
    processor_id: int, track_id: int,
    address: str = DEFAULT_GRPC_ADDRESS, controller: Any = None,
) -> None:
    """Delete the processor with `processor_id` from the track with `track_id`.
    Strict, as `add_live_processor`."""
    with _connection(address, controller) as c:
        _wait(c.audio_graph.delete_processor_from_track(processor_id, track_id), strict=True)
