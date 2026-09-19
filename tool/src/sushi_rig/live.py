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

import threading

import sys
import time
from typing import Any

DEFAULT_GRPC_ADDRESS = "localhost:51051"


def _controller(address: str):
    try:
        from elkpy import sushicontroller as sc
    except ImportError:
        sys.exit(
            "elkpy not found. Install it into the tool's venv: "
            "pip install elkpy (see README: Environment)"
        )
    return sc.SushiController(address)


def _safe(fn, *args):
    try:
        return fn(*args)
    except Exception:  # noqa: BLE001 - optional data, absence is fine
        return None


def capture(address: str = DEFAULT_GRPC_ADDRESS) -> dict[str, Any]:
    """Read the full normalised parameter state of a running Sushi.

    Returns the shape documented in the brief §5.2: a dict of processor name
    to {"parameters": {...}, "program": int?, "bypassed": bool?}. Tracks are
    included alongside plugins, since tracks carry parameters too (gain, pan).

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
    controller = _controller(address)
    processors: dict[str, Any] = {}
    track_order: dict[str, list[str]] = {}
    try:
        for track in controller.audio_graph.get_all_tracks():
            targets = [(track.id, track.name)]
            chain = list(controller.audio_graph.get_track_processors(track.id))
            track_order[track.name] = [proc.name for proc in chain]
            for proc in chain:
                targets.append((proc.id, proc.name))

            for proc_id, proc_name in targets:
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

                info_obj = _safe(controller.audio_graph.get_processor_info, proc_id)
                if info_obj is not None and getattr(info_obj, "program_count", 0) > 0:
                    program = _safe(
                        controller.programs.get_processor_current_program, proc_id
                    )
                    if program is not None:
                        entry["program"] = program

                bypassed = _safe(controller.audio_graph.get_processor_bypass_state, proc_id)
                if bypassed is not None:
                    entry["bypassed"] = bool(bypassed)

                processors[proc_name] = entry
    finally:
        controller.close()

    return {"processors": processors, "tracks": track_order}


def get_live_parameter_info(address: str = DEFAULT_GRPC_ADDRESS) -> dict[str, dict[str, dict]]:
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
    controller = _controller(address)
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
        controller.close()

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
        controller.close()

    return result


# How long to wait after `/SESSION/OPEN` before re-selecting the tab. Long
# enough for open-stage-control to rebuild its widget tree (measured well under
# half a second for a seven-plugin panel), short enough not to be noticed.
TAB_RESELECT_DELAY = 0.75


def refresh_panel(
    panel_path: str,
    select_tab: str | None = None,
    host: str = "127.0.0.1",
    port: int = 8080,
    tab_delay: float = TAB_RESELECT_DELAY,
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
        timer = threading.Timer(tab_delay, _reselect)
        timer.daemon = True
        timer.start()


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


def move_processor(
    processor: str, direction: int, address: str = DEFAULT_GRPC_ADDRESS
) -> str:
    """Move `processor` one step earlier (direction < 0) or later through its
    track's chain. Returns a human-readable outcome for the panel's status line.

    Pedal order is a tonal decision, so this exists to let it be tried by ear
    rather than by editing yaml and relaunching. `emit` persists whatever order
    results, via the `tracks` key `capture` records.
    """
    controller = _controller(address)
    try:
        for track in controller.audio_graph.get_all_tracks():
            procs = list(controller.audio_graph.get_track_processors(track.id))
            chain = [p.name for p in procs]
            if processor not in chain:
                continue

            target = plan_move(chain, processor, direction)
            where = "earlier" if direction < 0 else "later"
            if target is None:
                edge = "first" if direction < 0 else "last"
                return f"{processor} is already {edge} in the chain"

            by_name = {p.name: p.id for p in procs}
            before_id = by_name[target["before"]] if target["before"] else 0
            response = controller.audio_graph.move_processor_on_track(
                by_name[processor],
                track.id,
                track.id,
                before_id,
                target["add_to_back"],
            )
            _wait(response)
            # Deliberately says only what happened. It used to append
            # "regenerate the panel to reorder tabs", which was both a chore to
            # read on every move and based on a wrong belief that
            # open-stage-control could not be told to reload — see
            # refresh_panel above. The listener now refreshes the panel itself.
            return f"moved {processor} {where}"

        return f"no processor named {processor!r} on any track"
    finally:
        controller.close()


def _wait(response: Any, timeout: float = 5.0, interval: float = 0.02) -> None:
    """Poll a SushiCommandResponse until Sushi confirms the command.

    Not `.wait()` — see the module docstring. `is_set()` is a plain attribute
    read on `asyncio.Event`, safe to poll from a thread other than the one
    running elkpy's event loop.
    """
    is_set = getattr(response, "is_set", None)
    if is_set is None:
        return
    deadline = time.monotonic() + timeout
    while not is_set():
        if time.monotonic() > deadline:
            print(
                f"warning: command not confirmed within {timeout}s, continuing anyway",
                file=sys.stderr,
            )
            return
        time.sleep(interval)
    if getattr(response, "error", False):
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
        controller.close()
