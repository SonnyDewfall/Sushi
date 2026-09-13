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
    """
    controller = _controller(address)
    processors: dict[str, Any] = {}
    try:
        for track in controller.audio_graph.get_all_tracks():
            targets = [(track.id, track.name)]
            for proc in controller.audio_graph.get_track_processors(track.id):
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

    return {"processors": processors}


def get_live_parameter_info(address: str = DEFAULT_GRPC_ADDRESS) -> dict[str, dict[str, dict]]:
    """Per-processor, per-parameter {"automatable": bool, "value": float}.

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
                    }
                result[proc_name] = params
    finally:
        controller.close()

    return result


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
