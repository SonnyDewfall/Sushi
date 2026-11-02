"""Left against right for a mono source on a stereo channel, measured.

Sonny on the rig, 2026-11-02: "I only get the left channel out." A guitar is
one input. The machine file's `mono_to_stereo` feeds it to both halves of its
channel, every channel is a two-channel track, and the master goes out on
both monitors. So the right channel is lost somewhere inside Sushi, and these
renders say where, by measuring each piece alone with the same mono stimulus
on both channels that `mono_to_stereo` gives a channel:

- each plugin the committed rigs use, alone on a stereo track, as it is,
  bypassed, and each of those with `sushi.testing.mono_summing` after it
  (the candidate fix: a processor that puts what is on the track into both
  channels);
- each committed rig as the compiler emits it, through the master, as the
  null tests render it (`rigfile.heard`), and the same with mono_summing
  inserted after every plugin lilv says has one audio output;
- each v1 config, as `nulltest --all` renders it;
- an empty track, and the compiler's send and return with an empty chain.

Each render's left and right RMS and their correlation go into a table that
`conftest.pytest_terminal_summary` prints at the end of the run, which is the
only way to read them: the real Sushi and the plugins exist only in CI's
container. The plugin rows carry lilv's count of audio ports, the one place
the channel counts can be read: in Sushi 1.3.0 neither `--dump-plugins` (its
processor records are name, label, ids and parameters, parameter_dump.cpp)
nor gRPC's `ProcessorInfo` (id, label, name, parameter and program counts,
sushi_rpc.proto) carries them.

What Sushi 1.3.0's source predicts (src/engine/audio_engine.cpp
`add_plugin_to_track`, src/engine/track.cpp `_process_plugins`): a plugin on
a track is given min(its ports, the track's channels) inputs and outputs, so
a plugin with one output on a stereo track writes channel 0 only, and the
track then *clears* channel 1 "to avoid passing garbage to the next
processor". Bypassed is no different, since a bypassed processor keeps its
channel count (processor.cpp `bypass_process`). These renders are what tests
that prediction.

These assert only that each render came back usable: they are the
measurement, not yet a guarantee.
"""

from __future__ import annotations

import json
import os

import pytest

np = pytest.importorskip("numpy")

import stereo_table  # noqa: E402
from sushi_rig import rigfile  # noqa: E402
from sushi_rig.analysis import channel_balance, check_finite  # noqa: E402
from sushi_rig.paths import checkout_root  # noqa: E402
from sushi_rig.render import Stimulus, channels_of, render_offline  # noqa: E402

ROOT = checkout_root()
SUSHI = ROOT / "sushi"
RIGS = sorted((ROOT / "config" / "rigs").glob("*.yaml"))
V1_CONFIGS = sorted((ROOT / "config").glob("*.json"))

# The null tests' sweep, shorter: level and correlation need no low band
# resolved, and there are seventy renders. Two channels of the same samples
# (`signals.log_sweep` repeats one wave), which is what `mono_to_stereo` and
# the compiler's mono input give a channel.
STIMULUS = Stimulus(duration_s=1.0)

MONO_SUMMING = "sushi.testing.mono_summing"

needs_sushi = pytest.mark.skipif(not SUSHI.exists(), reason="needs the Sushi binary")


def _rig_uris() -> list[str]:
    found: dict[str, None] = {}
    for path in RIGS:
        rig = rigfile.load_rig(path)
        for plugin in [p for c in rig.channels for p in c.chain] + list(rig.master.chain):
            if plugin.uri:
                found[plugin.uri] = None
    return sorted(found)


PLUGIN_URIS = _rig_uris()


@pytest.fixture(scope="session")
def lilv_ports():
    """`{uri: (audio in, audio out)}` from lilv, over plugins/ alone as the
    rig and Sushi see it (`verify.sushi_env`)."""
    lilv = pytest.importorskip("lilv")

    before = os.environ.get("LV2_PATH")
    os.environ["LV2_PATH"] = str(ROOT / "plugins")
    try:
        world = lilv.World()
        world.load_all()
        lv2 = world.ns.lv2
        ports = {}
        for uri in PLUGIN_URIS:
            plugin = world.get_all_plugins().get_by_uri(world.new_uri(uri))
            assert plugin is not None, f"lilv cannot find {uri} under plugins/"
            ports[uri] = (plugin.get_num_ports_of_class(lv2.AudioPort, lv2.InputPort),
                          plugin.get_num_ports_of_class(lv2.AudioPort, lv2.OutputPort))
        return ports
    finally:
        if before is None:
            os.environ.pop("LV2_PATH", None)
        else:
            os.environ["LV2_PATH"] = before


def _record(request, section, what, ports, rendered):
    assert check_finite(rendered) is None, f"{what}: the render is not finite"
    assert rendered.shape[1] == 2, f"{what}: the render has {rendered.shape[1]} channels"
    balance = channel_balance(rendered)
    stereo_table.rows(request.config).append(stereo_table.Row(
        section, what, ports, balance.left_dbfs, balance.right_dbfs, balance.correlation,
    ))
    return balance


def _render(tmp_path, name, config) -> "np.ndarray":
    path = tmp_path / f"{name}.json"
    path.write_text(json.dumps(config, indent=2))
    return render_offline(path, ROOT, tmp_path / name, stimulus=STIMULUS, sushi_bin=str(SUSHI))["rendered"]


def _alone(plugins, state=()) -> dict:
    """One stereo track fed as the compiler feeds a mono channel: engine
    channel 0 into both halves (`rigfile._inputs`)."""
    return {
        "host_config": {"samplerate": 48000},
        "tracks": [{
            "name": "channel",
            "channels": 2,
            "inputs": [{"engine_channel": 0, "track_channel": 0}, {"engine_channel": 0, "track_channel": 1}],
            "outputs": [{"engine_bus": 0, "track_bus": 0}],
            "plugins": list(plugins),
        }],
        "initial_state": list(state),
    }


def _summing(name: str) -> dict:
    return {"name": f"{name}_summed", "type": "internal", "uid": MONO_SUMMING}


VARIANTS = ("as is", "bypassed", "+ mono_summing", "bypassed + mono_summing")


@needs_sushi
@pytest.mark.slow
@pytest.mark.parametrize("variant", VARIANTS)
@pytest.mark.parametrize("uri", PLUGIN_URIS)
def test_each_plugin_alone_on_a_stereo_track(uri, variant, tmp_path, request, lilv_ports):
    plugins = [{"name": "plugin", "type": "lv2", "uri": uri}]
    if "mono_summing" in variant:
        plugins.append(_summing("plugin"))
    state = [{"processor": "plugin", "bypassed": True}] if variant.startswith("bypassed") else []
    rendered = _render(tmp_path, "alone", _alone(plugins, state))
    ins, outs = lilv_ports[uri]
    _record(request, "each plugin alone on a stereo track, default settings",
            f"{uri.rsplit('/', 1)[-1]} [{variant}]", f"{ins}/{outs}", rendered)


def _with_summing(view: dict, ports: dict) -> dict:
    """`view` with mono_summing after every plugin lilv says has one output:
    the candidate fix, built here to be measured, not yet the compiler's."""
    view = json.loads(json.dumps(view))
    for track in view["tracks"]:
        plugins = []
        for plugin in track["plugins"]:
            plugins.append(plugin)
            if plugin.get("uri") in ports and ports[plugin["uri"]][1] == 1:
                plugins.append(_summing(plugin["name"]))
        track["plugins"] = plugins
    return view


@needs_sushi
@pytest.mark.slow
@pytest.mark.parametrize("variant", ("as compiled", "+ mono_summing after each 1-out plugin"))
@pytest.mark.parametrize("rig", RIGS, ids=lambda p: p.stem)
def test_each_rig_through_the_master(rig, variant, tmp_path, request, lilv_ports):
    config = rigfile.emit_rig(rigfile.load_rig(rig))
    for name, view in rigfile.heard(config):
        if variant != "as compiled":
            view = _with_summing(view, lilv_ports)
        _record(request, "each committed rig through the master (rigfile.heard)",
                f"{name} [{variant}]", "", _render(tmp_path, name, view))


@needs_sushi
@pytest.mark.slow
@pytest.mark.parametrize("config", V1_CONFIGS, ids=lambda p: p.stem)
def test_each_v1_config(config, tmp_path, request):
    for channel in channels_of(json.loads(config.read_text()), config):
        _record(request, "each v1 config, as nulltest --all renders it",
                channel.name, "", _render(tmp_path, channel.name, channel.config))


EMPTY_RIG = {
    "kind": "rig", "schema_version": 1, "name": "empty",
    "channels": [{"name": "board", "input": "guitar-in", "width": "mono", "chain": []}],
    "master": {"output": "monitor-out", "chain": []},
}


@needs_sushi
@pytest.mark.slow
def test_an_empty_track_and_the_send_and_return(tmp_path, request):
    section = "nothing in the chain"
    _record(request, section, "an empty stereo track", "", _render(tmp_path, "empty", _alone([])))
    ((_, view),) = rigfile.heard(rigfile.emit_rig(rigfile.parse_rig(EMPTY_RIG)))
    _record(request, section, "send -> return, empty chains (compiled)", "",
            _render(tmp_path, "send_return", view))
