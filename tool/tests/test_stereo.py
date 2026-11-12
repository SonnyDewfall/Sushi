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
  null tests render it (`rigfile.heard`), and the same with the compiler's
  copies to stereo taken out, which is what the rig was before them;
- each of those plugins as the compiler compiles it alone on a channel;
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

Measured in CI's container, 2026-11-02 (ba0f918): every one-output plugin
leaves the right channel silent, as is and bypassed alike, and mono_summing
after it puts it back; seven rigs had right at about -279 dBFS and four only
what a later reverb or delay fed across, 5 to 18 dB down; with mono_summing
after each one-output plugin every rig was within 0.3 dB. The compiler now
inserts it (stereo.py).

The guarantee: for this mono stimulus, every committed rig as compiled, and
every plugin compiled alone, has left and right within `BALANCE_DB` and
neither silent (`test_each_rig_through_the_master`,
`test_each_plugin_compiled_alone_is_balanced`); and the committed table the
compiler decides on is what lilv reads (`test_the_port_table_is_what_lilv_reads`).
The other renders measure and assert only that they came back usable.
"""

from __future__ import annotations

import json
import os

import pytest

np = pytest.importorskip("numpy")

import stereo_table  # noqa: E402
from sushi_rig import rigfile, stereo  # noqa: E402
from sushi_rig.analysis import channel_balance, check_finite  # noqa: E402
from sushi_rig.paths import checkout_root  # noqa: E402
from rig_fixtures import committed, committed_that, compiled_to_render  # noqa: E402
from sushi_rig.render import Stimulus, channels_of, render_offline  # noqa: E402

ROOT = checkout_root()
SUSHI = ROOT / "sushi"
# Every committed rig, whatever it holds: balance is asked of any rig (rig_fixtures.py).
RIGS = committed()
V1_CONFIGS = sorted((ROOT / "config").glob("*.json"))

# The null tests' sweep, shorter: level and correlation need no low band
# resolved, and there are seventy renders. Two channels of the same samples
# (`signals.log_sweep` repeats one wave), which is what `mono_to_stereo` and
# the compiler's mono input give a channel.
STIMULUS = Stimulus(duration_s=1.0)

MONO_SUMMING = "sushi.testing.mono_summing"

# How far apart left and right may be for a mono source, as compiled. A
# centred mono chain is exactly balanced; a stereo reverb or chorus after it
# is not, by a little (measured: within 0.3 dB on every rig). A silent side
# is -inf, and fails by any margin.
BALANCE_DB = 1.0

needs_sushi = pytest.mark.skipif(not SUSHI.exists(), reason="needs the Sushi binary")


def _rig_uris() -> list[str]:
    found: dict[str, None] = {}
    for _, rig in committed_that(rigfile.load_rig):  # at import: one that does not load is not this module's to raise
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


def _without_copies(view: dict) -> dict:
    """`view` without the compiler's copies to stereo: the rig as it was
    compiled before them, kept in the table as the before."""
    view = json.loads(json.dumps(view))
    for track in view["tracks"]:
        track["plugins"] = stereo.strip_copies(track["plugins"])
    return view


def _balanced(what, balance):
    assert balance.left_dbfs > float("-inf") and balance.right_dbfs > float("-inf"), \
        f"{what}: a channel is silent (L {balance.left_dbfs}, R {balance.right_dbfs} dBFS)"
    assert abs(balance.left_dbfs - balance.right_dbfs) <= BALANCE_DB, \
        f"{what}: L {balance.left_dbfs:.2f} and R {balance.right_dbfs:.2f} dBFS, more than {BALANCE_DB} dB apart"


@needs_sushi
@pytest.mark.slow
@pytest.mark.parametrize("variant", ("as compiled", "without the copies to stereo"))
@pytest.mark.parametrize("rig", RIGS, ids=lambda p: p.stem)
def test_each_rig_through_the_master(rig, variant, tmp_path, request):
    """As compiled, the guarantee: left and right within BALANCE_DB. Without
    the copies, the measurement of what they fix, asserted on nothing."""
    config = compiled_to_render(rig)  # emit_rig, but for a MIDI map, which no render plays
    for name, view in rigfile.heard(config):
        if variant != "as compiled":
            view = _without_copies(view)
        balance = _record(request, "each committed rig through the master (rigfile.heard)",
                          f"{name} [{variant}]", "", _render(tmp_path, name, view))
        if variant == "as compiled":
            _balanced(name, balance)


@needs_sushi
@pytest.mark.slow
@pytest.mark.parametrize("uri", PLUGIN_URIS)
def test_each_plugin_compiled_alone_is_balanced(uri, tmp_path, request, lilv_ports):
    """Each plugin the rigs use, alone on a mono channel as the compiler
    builds it (with its copy to stereo when it is mono), through the master."""
    raw = {
        "kind": "rig", "schema_version": 1, "name": "alone",
        "channels": [{"name": "board", "input": "guitar-in", "width": "mono",
                      "chain": [{"name": "plugin", "uri": uri}]}],
        "master": {"output": "monitor-out", "chain": []},
    }
    ((_, view),) = rigfile.heard(rigfile.emit_rig(rigfile.parse_rig(raw)))
    ins, outs = lilv_ports[uri]
    what = f"{uri.rsplit('/', 1)[-1]} [compiled]"
    _balanced(what, _record(request, "each plugin compiled alone on a mono channel, through the master",
                            what, f"{ins}/{outs}", _render(tmp_path, "compiled", view)))


@pytest.mark.slow
def test_the_port_table_is_what_lilv_reads():
    """plugin-ports.txt, which the compiler decides the copies on, against
    lilv over plugins/ for every URI in the manifest. The neural amp is the
    one exception, by name: no distro packages it, so this image cannot read
    it, and it runs in Carla, never on a Sushi track (the compiler refuses it
    there)."""
    lilv = pytest.importorskip("lilv")
    from sushi_rig.amp import NAM_URI
    from sushi_rig.plugins import read_manifest

    table = stereo.load_ports(ROOT)
    manifest = read_manifest((ROOT / "plugin-manifest.txt").read_text())
    assert NAM_URI not in table
    assert sorted(set(table) - set(manifest)) == [], "a plugin the rig may not load"
    assert sorted(set(manifest) - set(table) - {NAM_URI}) == [], "a plugin the compiler would refuse"
    before = os.environ.get("LV2_PATH")
    os.environ["LV2_PATH"] = str(ROOT / "plugins")
    try:
        world = lilv.World()
        world.load_all()
        lv2 = world.ns.lv2
        wrong = []
        for uri, ports in sorted(table.items()):
            plugin = world.get_all_plugins().get_by_uri(world.new_uri(uri))
            if plugin is None:
                wrong.append(f"{uri}: lilv cannot find it under plugins/")
                continue
            found = (plugin.get_num_ports_of_class(lv2.AudioPort, lv2.InputPort),
                     plugin.get_num_ports_of_class(lv2.AudioPort, lv2.OutputPort))
            if found != (ports.inputs, ports.outputs):
                wrong.append(f"{uri}: the table says {ports.inputs} in {ports.outputs} out, lilv {found}")
    finally:
        if before is None:
            os.environ.pop("LV2_PATH", None)
        else:
            os.environ["LV2_PATH"] = before
    assert wrong == []


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
