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

from . import stereo
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
            "pip install elkpy (see docs/reference/environment.md)"
        ) from None
    controller = sc.SushiController(address)
    # Before any command is sent on it, so no response is registered yet.
    controller.command_responses = Confirmations()
    return controller


# --------------------------------------------------------------------------- #
# A command's confirmation (#241)
# --------------------------------------------------------------------------- #
#
# How elkpy 1.3.0 confirms an async command, read from its installed source
# (sushicontroller.py, notificationcontroller.py): the command's RPC returns
# ASYNC_RESPONSE with an id, and only then, on the caller's thread,
# `validate_command_response` registers a SushiCommandResponse under that id
# in `controller.command_responses`. Sushi's notice that the command was
# carried out comes on another stream, SubscribeToAsyncCommandUpdates, which
# elkpy's notification thread opens on a channel of its own when the
# connection is made, and matches by `command_responses.get(request_id)`. A
# notice is lost, and the command never confirmed, two ways:
#
# 1. It came before the response was registered: Sushi carried the command
#    out before its RPC's answer had been read on the caller's thread. elkpy
#    finds nothing under the id and drops it. At any time on any connection.
# 2. It came before the subscription was up: Sushi sends a notice to the
#    subscribers it has, and the thread's channel was still connecting. Only
#    on a new connection, so nearly always its first command. Nothing the
#    client does recovers that notice, and nothing elkpy exposes says when
#    the stream is up (waiting for the command channel to be ready does not:
#    it is already up, since elkpy's constructor made an RPC on it).
#
# `Confirmations` closes the first: a notice that comes first is kept, and
# confirms its command when the command is registered. The second is closed
# by reading the change back (`_wait`'s `applied`): a strict change that has
# no notice yet is looked for in Sushi itself, so a change that was made is
# confirmed by what Sushi holds, and one that is not there by the deadline is
# reported as not applied. Inferred from the source and from the probe's
# counts (tool/spikes/live_remove_track/): not yet run against a real Sushi.

# How many notices with no command registered a connection keeps. Every
# subscriber is sent every client's notices, so most of them are someone
# else's; the newest are kept, since a command's own notice comes within
# moments of its answer.
EARLY_NOTICES_KEPT = 256


class _EarlyNotice:
    """A notice that came before its command was registered: what elkpy's
    notification thread sets in place of a SushiCommandResponse (`error`,
    then `set()`), passed on to the response once it is registered."""

    def __init__(self, lock: threading.Lock):
        self._lock = lock
        self.error = False
        self.done = False
        self.response: Any = None

    def set(self) -> None:
        with self._lock:
            self.done = True
            response = self.response
        if response is not None:
            _confirm(response, self.error)


def _confirm(response: Any, error: bool) -> None:
    if error:
        response.error = True
    response.set()


class Confirmations(dict):
    """`SushiController.command_responses` that does not lose a notice which
    comes before its command is registered (see above). elkpy's own use of
    it is all there is: the caller's thread stores a response under its id,
    the notification thread `get`s an id, sets what it got, and `pop`s the
    id. Anything else is a plain dict's."""

    def __init__(self) -> None:
        super().__init__()
        self._lock = threading.Lock()
        self._early: dict[int, _EarlyNotice] = {}

    def get(self, key, default=None):
        with self._lock:
            if dict.__contains__(self, key):
                return dict.get(self, key)
            notice = self._early.get(key)
            if notice is None:
                notice = self._early[key] = _EarlyNotice(self._lock)
                while len(self._early) > EARLY_NOTICES_KEPT:
                    del self._early[next(iter(self._early))]
            return notice

    def pop(self, key, *default):
        with self._lock:
            # elkpy pops with no default after each notice it matched,
            # including one matched to a kept notice, which is not here.
            return dict.pop(self, key, *(default or (None,)))

    def __setitem__(self, key, response) -> None:
        with self._lock:
            notice = self._early.pop(key, None)
            if notice is None:
                dict.__setitem__(self, key, response)
                return
            if not notice.done:
                # The notification thread has it and is about to set it.
                notice.response = response
                return
        _confirm(response, notice.error)


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


# The processors already warned about for having no parameters, by track and
# name, for the life of this process. See `_no_parameters`.
_SAID_NO_PARAMETERS: set[tuple[str, str]] = set()


def _no_parameters(track: str, processor: str) -> None:
    """Say, once, that a processor returned no parameters, unless it is one
    that has none by design.

    `capture` runs under the web UI's poll, about once a second, and the
    warning it gave every time for the master's return (`master_return`, a
    send/return pair has nothing to set) filled the agent's terminal on the
    rig on 2026-11-02 and buried the two lines that mattered, a crashed tuner
    and a refused take. The compiler's own send and return
    (`protected_processor`) are expected to have none and are not mentioned.
    Anything else is said once per processor per process: a plugin with no
    parameters is worth knowing about, not worth repeating. An internal
    processor the compiler did not put there cannot be told by name here
    (the capture reads no session), so it falls to the once-only rule."""
    if protected_processor(track, processor) is not None or (track, processor) in _SAID_NO_PARAMETERS:
        return
    _SAID_NO_PARAMETERS.add((track, processor))
    print(f"warning: {processor!r} on {track!r} returned no parameters "
          "(said once; it is not repeated while this process runs)", file=sys.stderr)


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
                    _no_parameters(track.name, proc_name)

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
        _wait(c.parameters.set_parameter_value(proc_id, param_id, float(value)), strict=True,
              applied=lambda: _holds_value(c, proc_id, param_id, float(value)))


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
        _wait(c.audio_graph.set_processor_bypass_state(proc_id, bool(bypassed)), strict=True,
              applied=lambda: bool(c.audio_graph.get_processor_bypass_state(proc_id)) == bool(bypassed))


# --------------------------------------------------------------------------- #
# A pedal's mapping, live (#210)
# --------------------------------------------------------------------------- #
#
# UNVERIFIED AGAINST A REAL SUSHI. Read from elkpy 1.3.0's midicontroller.py
# and its sushi_rpc.proto, not run: whether Sushi 1.3.0 honours
# ConnectCCToParameter and DisconnectCC on a running rig, whether the
# connection then drives the parameter as the config's cc_mapping would,
# and whether GetAllCCInputConnections lists it. The agent calls these only
# behind `Agent._midi_cc_live`, which reads the connections back and treats
# any failure as "plays after a restart", the answer it gave before.

# The proto's MidiChannel: DUMMY 0, MIDI_CH_1 1 .. MIDI_CH_16 16, OMNI 17. A
# cc_mapping's channel is the machine file's, from 0 (the pedal followed a
# mapping on channel 0 sending on the wire's channel 0, the laptop,
# 2026-11-23), or a string for any channel.
MIDI_CHANNEL_OMNI = 17


def midi_channel(channel: Any) -> int:
    """A cc_mapping's `channel` as the gRPC API's MidiChannel numbers it."""
    if isinstance(channel, int) and not isinstance(channel, bool) and 0 <= channel < 16:
        return channel + 1
    return MIDI_CHANNEL_OMNI


def _cc_ids(c, mapping: dict[str, Any]) -> tuple[int, int]:
    proc_id = c.audio_graph.get_processor_id(mapping["plugin_name"])
    return proc_id, c.parameters.get_parameter_id(proc_id, mapping["parameter_name"])


def connect_live_cc(mapping: dict[str, Any], address: str = DEFAULT_GRPC_ADDRESS, controller: Any = None) -> None:
    """Have the running Sushi map one CC as a config's `cc_mappings` entry
    (`rigfile.compile_midi`'s shape) would: elkpy's
    `MidiController.connect_cc_to_parameter`. Strict: `LiveError` if Sushi
    reports an error or does not confirm. Unverified: see above."""
    with _connection(address, controller) as c:
        proc_id, param_id = _cc_ids(c, mapping)
        _wait(c.midi_controller.connect_cc_to_parameter(
            proc_id, param_id, midi_channel(mapping.get("channel")), int(mapping.get("port", 0)),
            int(mapping["cc_number"]), float(mapping["min_range"]), float(mapping["max_range"]),
            mapping.get("mode") == "relative"), strict=True,
            applied=lambda: _holds_cc(c, proc_id, param_id, mapping))


def disconnect_live_cc(mapping: dict[str, Any], address: str = DEFAULT_GRPC_ADDRESS,
                       controller: Any = None) -> None:
    """Take one CC mapping off the running Sushi: elkpy's
    `MidiController.disconnect_cc`, with the mapping's own values. Strict, as
    `connect_live_cc`. Unverified: see above."""
    with _connection(address, controller) as c:
        proc_id, param_id = _cc_ids(c, mapping)
        _wait(c.midi_controller.disconnect_cc(
            proc_id, param_id, midi_channel(mapping.get("channel")), int(mapping.get("port", 0)),
            int(mapping["cc_number"]), float(mapping["min_range"]), float(mapping["max_range"]),
            mapping.get("mode") == "relative"), strict=True,
            applied=lambda: _negate(_holds_cc(c, proc_id, param_id, mapping)))


def read_live_cc(address: str = DEFAULT_GRPC_ADDRESS, controller: Any = None) -> list[dict[str, Any]]:
    """Every CC mapping the running Sushi holds, as `{plugin_name,
    parameter_name, channel, port, cc_number, min_range, max_range, mode}` in
    the cc_mappings' own terms
    (the channel from 0, or "all"): what `Agent._midi_cc_live` checks a live
    mapping against. A processor or parameter it cannot name is said by its
    ids. Unverified: see above."""
    with _connection(address, controller) as c:
        names: dict[int, str] = {}
        for track in c.audio_graph.get_all_tracks():
            names[track.id] = track.name
            for processor in c.audio_graph.get_track_processors(track.id):
                names[processor.id] = processor.name
        out = []
        for connection in c.midi_controller.get_all_cc_input_connections() or []:
            parameters = {}
            try:
                parameters = {p.id: p.name for p in c.parameters.get_processor_parameters(connection.processor_id)}
            except Exception:  # noqa: BLE001 -- said by id instead
                pass
            channel = connection.channel
            out.append({
                "plugin_name": names.get(connection.processor_id, f"processor {connection.processor_id}"),
                "parameter_name": parameters.get(connection.parameter_id, f"parameter {connection.parameter_id}"),
                "channel": channel - 1 if 1 <= channel <= 16 else "all",
                "port": connection.port, "cc_number": connection.cc_number,
                # Compared within a tolerance (float32 on the wire): a range
                # Sushi holds wrongly is not the mapping asked for (#210's review).
                "min_range": float(connection.min_range), "max_range": float(connection.max_range),
                "mode": "relative" if connection.relative_mode else "absolute",
            })
        return out


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
    # "moved" move has them. `then` is the commands after it, in order, when
    # a plugin and its copy to stereo move as one (`stereo.units`): the copy
    # goes where its plugin went, or a neighbour's copy where its plugin did.
    command: tuple[int, int, int, bool] | None = field(default=None, repr=False, compare=False)
    why: str | None = field(default=None, compare=False)
    then: tuple[tuple[int, int, int, bool], ...] = field(default=(), repr=False, compare=False)
    # With `then`: the command that puts the plugin back in front of its copy
    # if the copy's own command fails, so the two are never left apart, and
    # the names, for saying what state the chain is in if that fails too.
    back: tuple[int, int, int, bool] | None = field(default=None, repr=False, compare=False)
    unit: tuple[str, ...] = field(default=(), repr=False, compare=False)

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

    The swap is of units (`stereo.units`): a mono plugin and the copy to
    stereo after it move as one, and a neighbour with a copy is stepped past
    whole, so no move separates a plugin from its copy or puts anything
    between them. A copy asked to move by itself is protected, like a send.
    Moving a unit of two is two of Sushi's commands, sent in order: for the
    moment between them the right channel can be silent, or a stereo
    plugin's image folded, for one block or so.
    """
    track = next((t for t, chain in chains.items() if processor in chain), None)
    if track is None:
        return Move("absent", processor, direction, None, chains)
    chain = chains[track]
    uids = uids or {}
    grouped = stereo.units(chain, uids)
    index = next(i for i, unit in enumerate(grouped) if processor in unit)
    if grouped[index][0] != processor:
        # A copy to stereo, asked to move by itself: it goes with its plugin.
        return Move("protected", processor, direction, chain, chains,
                    why=f"{protected_processor(track, processor, uids.get(processor))}, which moves "
                        f"with {stereo.source_of(processor)!r}: move that instead")
    other = index + (-1 if direction < 0 else 1)
    if not 0 <= other < len(grouped):
        return Move("unchanged", processor, direction, chain, chains)
    for name in (processor, grouped[other][0]):
        why = protected_processor(track, name, uids.get(name))
        # A copy heading a unit is a stray: one with no plugin before it,
        # left by a two-command edit that failed half way and could not be
        # undone. Moving it is part of the repair, so it is allowed.
        if why and not stereo.is_copy(name, uids.get(name)):
            return Move("protected", processor, direction, chain, chains,
                        why=f"{why}, which stays where the compiler put it")
    # Each of this unit, in order, in front of the neighbour's head (up), or
    # in front of what follows the neighbour, or at the back (down): one swap.
    if direction < 0:
        ahead_of = grouped[other][0]
    else:
        ahead_of = grouped[other + 1][0] if other + 1 < len(grouped) else None
    unit = grouped[index]
    commands = tuple(
        (ids[name], ids[track], ids[ahead_of] if ahead_of else 0, ahead_of is None)
        for name in unit
    )
    back = (ids[unit[0]], ids[track], ids[unit[1]], False) if len(unit) > 1 else None
    return Move("planned", processor, direction, chain, chains, commands[0], then=commands[1:],
                back=back, unit=tuple(unit))


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
    def send(c, command) -> None:
        processor_id, track_id, before_id, add_to_back = command
        response = c.audio_graph.move_processor_on_track(
            processor_id, track_id, track_id, before_id, add_to_back,
        )
        # Strict: "moved" is said only of a move Sushi confirmed, by its
        # notice or by the chain read back.
        _wait(response, strict=True,
              applied=lambda: _moved(c, processor_id, track_id, before_id, add_to_back))

    with _connection(address, controller) as c:
        send(c, move.command)
        for command in move.then:
            try:
                send(c, command)
            except Exception as exc:  # noqa: BLE001 -- put the plugin back, then say so
                head, copy = move.unit[0], move.unit[-1]
                try:
                    send(c, move.back)
                except Exception as again:  # noqa: BLE001
                    raise LiveError(
                        f"moved {head!r} but not its copy to stereo {copy!r} ({exc}), and putting "
                        f"{head!r} back in front of it failed too ({again}): the two are apart, so the "
                        f"right channel is silent after {head!r} and {copy!r} folds what comes before it. "
                        f"A copy with no plugin before it can be moved or removed: move or remove "
                        f"{copy!r}, or restart the rig") from again
                raise LiveError(
                    f"moved {head!r} but not its copy to stereo {copy!r} ({exc}), so {head!r} was put "
                    "back in front of it: nothing moved") from exc
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
    response: Any, timeout: float | None = None, interval: float = 0.02, strict: bool = False,
    applied: Callable[[], bool | None] | None = None,
) -> None:
    """Poll a SushiCommandResponse until Sushi confirms the command.

    Lenient by default, for `push`, which builds a whole rig and carries on past
    a slow answer with a warning. With `strict`, a command Sushi reports as
    failed, or does not confirm within `timeout` (`CONFIRM_TIMEOUT`), raises
    `LiveError` instead: the live setters and `move_live_processor` use it, because
    their callers report the change as made.

    `applied` reads the change back from Sushi: True if Sushi holds it, False
    if it does not, None if the reading cannot tell (or fails). It is what
    decides a command whose notice has not come (#241, "A command's
    confirmation" above): looked at `READBACK_AFTER` in and every
    `READBACK_EVERY` after, a change Sushi holds is confirmed then, and at the
    deadline one it does not hold is "not applied" rather than "may or may
    not". A notice that says the command failed is still a failure, whatever
    a reading says.

    Not `.wait()` -- see the module docstring. `is_set()` is a plain attribute
    read on `asyncio.Event`, safe to poll from a thread other than the one
    running elkpy's event loop.
    """
    is_set = getattr(response, "is_set", None)
    if is_set is None:
        return
    if timeout is None:
        timeout = CONFIRM_TIMEOUT
    start = time.monotonic()
    deadline, look = start + timeout, start + READBACK_AFTER
    while not is_set():
        now = time.monotonic()
        if now > deadline:
            seen = _read_back(applied)
            if seen and not is_set():
                return
            if is_set():
                break
            if strict:
                if seen is False:
                    raise LiveError(
                        f"Sushi did not confirm the command within {timeout:g}s, and reading it "
                        "back shows it was not applied"
                    )
                raise LiveError(
                    f"Sushi did not confirm the command within {timeout:g}s, so it "
                    "may or may not have been applied"
                )
            print(
                f"warning: command not confirmed within {timeout}s, continuing anyway",
                file=sys.stderr,
            )
            return
        if applied is not None and now >= look:
            look = now + READBACK_EVERY
            if _read_back(applied) and not is_set():
                return  # Sushi holds the change; its notice was lost
            continue  # the reading took time: look at the notice again first
        time.sleep(interval)
    if getattr(response, "error", False):
        if strict:
            raise LiveError("Sushi reported an error completing the command")
        print("warning: Sushi reported an error completing the command", file=sys.stderr)


# When a strict `_wait` with nothing heard yet first reads the change back,
# and how often after. A notice normally comes within milliseconds; a lost one
# then costs this, not `CONFIRM_TIMEOUT`.
READBACK_AFTER = 0.25
READBACK_EVERY = 0.25


def _read_back(applied: Callable[[], bool | None] | None) -> bool | None:
    """`applied()`, with a reading that fails taken as one that cannot tell."""
    if applied is None:
        return None
    try:
        return applied()
    except Exception:  # noqa: BLE001 -- a reading that fails says nothing either way
        return None


# -- what each strict command reads back (`_wait`'s `applied`) ------------- #
# Each answers True (Sushi holds the change), False (it does not) or None (the
# reading cannot tell). Read with the same calls the reads in this module use.

def _negate(seen: bool | None) -> bool | None:
    return None if seen is None else not seen


def _holds_value(c, proc_id: int, param_id: int, value: float) -> bool | None:
    """A parameter reads back as set, within `verify.TOLERANCE` (the margin
    the switch's own check gives: float32 on the wire). Any other value
    cannot say it was not set, since Sushi may hold a stepped or clamped
    parameter at a value next to the one asked: None."""
    from .verify import TOLERANCE

    now = c.parameters.get_parameter_value(proc_id, param_id)
    return True if abs(float(now) - value) <= TOLERANCE else None


def _on_track(c, track_id: int, *, name: str | None = None, processor_id: int | None = None) -> bool:
    """Whether the track holds a processor of that name, or that id."""
    for proc in c.audio_graph.get_track_processors(track_id):
        if (name is not None and proc.name == name) or (processor_id is not None and proc.id == processor_id):
            return True
    return False


def _moved(c, processor_id: int, track_id: int, before_id: int, add_to_back: bool) -> bool:
    """Whether the processor stands where the move put it: last, or just in
    front of `before_id`. A planned move always changes where it stands."""
    order = [proc.id for proc in c.audio_graph.get_track_processors(track_id)]
    if processor_id not in order:
        return False
    at = order.index(processor_id)
    if add_to_back:
        return at == len(order) - 1
    return at + 1 < len(order) and order[at + 1] == before_id


def _holds_cc(c, proc_id: int, param_id: int, mapping: dict[str, Any]) -> bool:
    """Whether Sushi holds a CC connection with the mapping's own values on
    that parameter, the ranges within `CC_READ_TOLERANCE` (float32 on the
    wire)."""
    for held in c.midi_controller.get_cc_input_connections_for_processor(proc_id) or []:
        if (held.parameter_id == param_id
                and held.channel == midi_channel(mapping.get("channel"))
                and held.port == int(mapping.get("port", 0))
                and held.cc_number == int(mapping["cc_number"])
                and abs(float(held.min_range) - float(mapping["min_range"])) <= CC_READ_TOLERANCE
                and abs(float(held.max_range) - float(mapping["max_range"])) <= CC_READ_TOLERANCE
                and bool(held.relative_mode) == (mapping.get("mode") == "relative")):
            return True
    return False


# How far a CC range read back may be from the one sent: float32 holds a
# range to about 1e-7 of its size.
CC_READ_TOLERANCE = 1e-5


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
    was read. The master's limiter (#137) is `master_limiter` on `master`, by
    name alone: it is an LV2 plugin, whose uid is its URI, and a player may
    put the same plugin anywhere else. Pure.
    """
    # Here, not at the top: rigfile imports save, which imports this module.
    from .rigfile import MASTER, RETURN, RETURN_UID

    if is_send(track, processor, uid):
        return f"{processor!r} is {track!r}'s send"
    if uid == RETURN_UID or (track == MASTER and processor == RETURN):
        return f"{processor!r} is the master's return"
    if is_limiter(track, processor):
        return f"{processor!r} is the master's limiter"
    if stereo.is_copy(processor, uid):
        # The compiler's copy to stereo after a mono plugin (stereo.py): it
        # goes, moves and is removed with its plugin, never on its own.
        return f"{processor!r} is {stereo.source_of(processor)!r}'s copy to stereo"
    return None


def is_send(track: str, processor: str, uid: str | None = None) -> bool:
    """Whether `processor` on `track` is the channel's send: what an add goes
    in front of, and `protected_processor`'s send half, so the two cannot
    disagree about which processor is the send."""
    from .rigfile import SEND_SUFFIX, SEND_UID

    return uid == SEND_UID or processor == track + SEND_SUFFIX


def is_limiter(track: str, processor: str) -> bool:
    """Whether `processor` on `track` is the master's limiter (#137), which
    stays last on the master as a send stays last on its channel."""
    from .rigfile import LIMITER, MASTER

    return track == MASTER and processor == LIMITER


def is_tail(track: str, processor: str, uid: str | None = None) -> bool:
    """Whether `processor` is what ends its track's chain and stays there: a
    channel's send, or the master's limiter. What an add with no place goes
    in front of, and what nothing may be added after."""
    return is_send(track, processor, uid) or is_limiter(track, processor)


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


def read_live_values(
    processor: str, address: str = DEFAULT_GRPC_ADDRESS, controller: Any = None,
) -> dict[str, Any]:
    """`{"parameters": {name: normalised value}, "bypassed": bool | None}`:
    one processor's automatable parameters and its bypass, read the way
    `capture` reads them (read-only parameters left out, a bypass Sushi will
    not give None). What a remove records just before it removes, so an undo
    can put the plugin back as it was, and what an add reads just after it
    adds, the plugin's defaults, so a save writes only the values that differ
    from them (#32). Its id is asked of Sushi by name (`get_processor_id`, as
    `set_live_parameter` asks it); an unknown name is Sushi's refusal and
    propagates. A program is not read: a plugin whose state is a program alone
    comes back at its default one. Not run against a live Sushi."""
    with _connection(address, controller) as c:
        proc_id = c.audio_graph.get_processor_id(processor)
        parameters = {
            info.name: c.parameters.get_parameter_value(proc_id, info.id)
            for info in c.parameters.get_processor_parameters(proc_id)
            if getattr(info, "automatable", True)
        }
        state = _safe(c.audio_graph.get_processor_bypass_state, proc_id)
        return {"parameters": parameters, "bypassed": None if state is None else bool(state)}


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
        _wait(response, strict=True, applied=lambda: _on_track(c, track_id, name=name))


def add_live_copy(
    name: str, track_id: int, before_id: int, add_to_back: bool,
    address: str = DEFAULT_GRPC_ADDRESS, controller: Any = None,
) -> None:
    """Create the compiler's copy to stereo (`stereo.COPY_UID`, an internal
    plugin) as processor `name`, placed as `add_live_processor` places one.
    Strict, as it is. Not run against a live Sushi: the internal type and an
    empty path are what `push` sends for the sends and returns."""
    from elkpy import sushi_info_types as info

    with _connection(address, controller) as c:
        response = c.audio_graph.create_processor_on_track(
            name=name, uid=stereo.COPY_UID, path="", processor_type=info.PluginType.INTERNAL,
            track_id=track_id, before_processor=before_id, add_to_back=add_to_back,
        )
        _wait(response, strict=True, applied=lambda: _on_track(c, track_id, name=name))


def remove_live_processor_named(
    name: str, track_id: int, address: str = DEFAULT_GRPC_ADDRESS, controller: Any = None,
) -> None:
    """Delete processor `name` from the track with `track_id`, its id asked
    of Sushi first (`get_processor_id`, as `push` asks a track's): for undoing
    an add, whose new processor's id no reading holds yet. Strict."""
    with _connection(address, controller) as c:
        processor_id = c.audio_graph.get_processor_id(name)
        _wait(c.audio_graph.delete_processor_from_track(processor_id, track_id), strict=True,
              applied=lambda: _negate(_on_track(c, track_id, processor_id=processor_id)))


def remove_live_processor(
    processor_id: int, track_id: int,
    address: str = DEFAULT_GRPC_ADDRESS, controller: Any = None,
) -> None:
    """Delete the processor with `processor_id` from the track with `track_id`.
    Strict, as `add_live_processor`."""
    with _connection(address, controller) as c:
        _wait(c.audio_graph.delete_processor_from_track(processor_id, track_id), strict=True,
              applied=lambda: _negate(_on_track(c, track_id, processor_id=processor_id)))


def remove_live_track(track: str, address: str = DEFAULT_GRPC_ADDRESS, controller: Any = None) -> None:
    """Delete track `track` and everything on it (a channel removed from the
    running rig, #190): each processor from the back of its chain to the
    front, its send first, then the track. Strict, each command as
    `add_live_processor`'s. A processor is taken off before the track goes,
    rather than left for `DeleteTrack` to take with it, since what Sushi
    does with a track's processors when it is deleted is not known here.

    NOT RUN AGAINST A LIVE SUSHI. Read from elkpy 1.3.0's installed source:
    `AudioGraphController.delete_track(self, track_id: int)`, beside the
    `delete_processor_from_track` the removes use. What is unverified: that
    Sushi 1.3.0 deletes a track while it plays, and that the engine inputs
    and outputs it was connected to are let go with it. A command that fails
    raises, the agent's switch then restarts into the next rig."""
    with _connection(address, controller) as c:
        chains, ids = _walk_chains(c)
        if track not in chains:
            raise LiveError(f"there is no track {track!r} to remove (the tracks are {sorted(chains)})")
        track_id = ids[track]
        for name in reversed(chains[track]):
            _wait(c.audio_graph.delete_processor_from_track(ids[name], track_id), strict=True,
                  applied=lambda gone=ids[name]: _negate(_on_track(c, track_id, processor_id=gone)))
        _wait(c.audio_graph.delete_track(track_id), strict=True,
              applied=lambda: all(t.id != track_id for t in c.audio_graph.get_all_tracks()))


# --------------------------------------------------------------------------- #
# elkpy's prints, for a long-running caller (the agent, #111)
# --------------------------------------------------------------------------- #

# What elkpy 1.3.0 prints on every connection, read from its source: one
# `SushiController` prints three of these (grpc_gen.py, sushicontroller.py,
# notificationcontroller.py). The CLI connects once and the lines are noise;
# the agent connects once per request, so a page polling /live printed three
# a second and buried everything the agent had to say (found live on the rig
# laptop, 2026-11-02).
#
# "Could not find a running Sushi" is among them because it is not the
# failure itself: elkpy prints it and carries on, and the call after it is
# what fails (SushiUnavailableError), which the agent answers, remembers and
# says itself (`Agent._sushi`). elkpy 1.3.0 prints it for a version mismatch
# too, since its own `except Exception` turns SushiAPIMismatchError into
# SushiNotFoundError.
ELKPY_CHATTER = (
    "ELKPY: Compiled proto found!",
    "ELKPY: Compiled proto file!",
    "ELKPY: Asyncio context detected.",
    "ELKPY: Synchronous context detected.",
    "ELKPY: Sushi API match!",
    "ELKPY: Could not find a running Sushi",
)

# Printed once each, then not again: true of every connection while it is
# true at all, so said once it is said.
ELKPY_ONCE = ("ELKPY: API version mismatch!",)

# The elkpy modules that call print(); each gets `elkpy_print` as its `print`.
ELKPY_PRINTING = ("grpc_gen", "sushicontroller", "notificationcontroller", "sushierrors", "sushi_info_types")


def elkpy_line(text: str, said: set[str]) -> str | None:
    """What becomes of one line elkpy prints: None if it is dropped, else the
    line to write to stderr, marked as elkpy's. `said` holds the once-only
    lines already written, and is added to. Pure but for `said`."""
    if text.startswith(ELKPY_CHATTER):
        return None
    if text.startswith(ELKPY_ONCE):
        if text in said:
            return None
        said.add(text)
    return f"sushi-rig: elkpy said: {text}"


_elkpy_said: set[str] = set()
_elkpy_lock = threading.Lock()


def elkpy_print(*args: Any, sep: str = " ", end: str = "\n", file: Any = None, flush: bool = False) -> None:
    """`print` for elkpy's modules: its chatter dropped (`elkpy_line`), the
    rest to stderr, never stdout. A print with an explicit `file` is left as
    it was asked. Called from Sushi worker threads, hence the lock."""
    if file is not None:
        print(*args, sep=sep, end=end, file=file, flush=flush)
        return
    text = sep.join(str(a) for a in args)
    with _elkpy_lock:
        lines = [elkpy_line(line, _elkpy_said) for line in text.splitlines() or [""]]
    for line in lines:
        if line is not None:
            print(line, file=sys.stderr, flush=True)


_quieted: list[str] | None = None  # the modules `quiet_elkpy` patched, once it has run
# Not `_elkpy_lock`: importing an elkpy module can print through a module
# already patched, which takes that one, and it is not reentrant.
_quiet_lock = threading.Lock()


def quiet_elkpy() -> list[str]:
    """Give elkpy's printing modules `elkpy_print` as their `print`, for the
    life of the process, and say which were patched. Idempotent: the work is
    done once, and later calls answer what it found.

    Module by module, and never raising: a module elkpy does not have (a
    version that moved one), or one whose import fails some other way, is
    left printing as it did and named once on stderr, and the rest are still
    patched. Quiet output is not worth a rig the agent cannot serve, so this
    must never be what stops it. Called on the agent's first connection
    (`agent.connect_quietly`), not at its start, so an agent that never
    talks to Sushi never imports elkpy, gRPC and protobuf.

    A module-level name that shadows the builtin, not a redirect of
    sys.stdout: a redirect is the whole process's, so the agent's own lines
    printed by another thread meanwhile would be caught by it. This touches
    nothing but elkpy's own calls to `print` (principle 4: the one
    customisation of elkpy here, recorded with what it works around).
    """
    global _quieted
    import importlib

    with _quiet_lock:
        if _quieted is not None:
            return list(_quieted)
        patched, missed = [], []
        for name in ELKPY_PRINTING:
            try:
                module = importlib.import_module(f"elkpy.{name}")
                module.print = elkpy_print  # type: ignore[attr-defined]
            except Exception as exc:  # noqa: BLE001 -- said below, never raised
                missed.append(f"elkpy.{name} ({type(exc).__name__}: {exc})")
            else:
                patched.append(name)
        _quieted = patched
    if missed and patched:
        # With none patched, elkpy itself is missing or broken, and the
        # connection being made says so (ElkpyMissing), louder than this.
        print("sushi-rig: elkpy's own prints are not silenced for " + "; ".join(missed),
              file=sys.stderr, flush=True)
    return list(patched)
