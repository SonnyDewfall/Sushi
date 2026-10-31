"""What Sushi's send and return add to the path you hear, measured.

The monitor mix is made in Sushi (decided 2026-10-31): each channel keeps its
own output, so stems exist, and sends a copy to a master track that feeds the
monitors. That puts `sushi.testing.send` and `sushi.testing.return` in the
monitor path, so what they add has to be known before anything is built on them.

Sushi 1.3.0's source says what to expect (return_plugin.cpp, `send_audio`): a
send that runs on the return's thread *before* the return has processed this
block writes straight into the block the return is about to play — no delay. A
send that runs after it writes into the next block — one internal chunk late,
`AUDIO_CHUNK_SIZE`, 64 samples unless the build changed it. Not a PipeWire
quantum: Sushi processes each 256-sample quantum as four 64-sample chunks.

So the order of tracks in the config should be the whole story: master after
the channels costs nothing, master before costs 64 samples. These tests render
both through a real Sushi and measure against a plain passthrough, so the
answer is the build's, not the source's.

What this cannot say: whether the live JACK frontend runs tracks on more than
one thread. A send on another thread is always a chunk late. `up` runs Sushi
without `--multicore-processing`, so it should be one thread there too; that is
for the rig machine to confirm.
"""

from __future__ import annotations

import json

import pytest

np = pytest.importorskip("numpy")

from sushi_rig import selftest  # noqa: E402
from sushi_rig.paths import checkout_root  # noqa: E402

ROOT = checkout_root()
SUSHI = ROOT / "sushi"
SR = 48000
CHUNK = 64  # Sushi's AUDIO_CHUNK_SIZE, as its source sets it

needs_sushi = pytest.mark.skipif(not SUSHI.exists(), reason="needs the Sushi binary")


def passthrough_track(name: str, inputs, outputs) -> dict:
    return {
        "name": name,
        "channels": 2,
        "inputs": inputs,
        "outputs": outputs,
        "plugins": [{"uid": "sushi.testing.passthrough", "name": f"{name}_pass", "type": "internal"}],
    }


BUS0 = [{"engine_bus": 0, "track_bus": 0}]

DIRECT = {"host_config": {"samplerate": SR}, "tracks": [passthrough_track("channel", BUS0, BUS0)]}


def through_master(master_first: bool) -> dict:
    """A channel heard only through a send to a master track's return."""
    channel = passthrough_track("channel", BUS0, [])
    channel["plugins"].append({"uid": "sushi.testing.send", "name": "channel_send", "type": "internal"})
    master = {
        "name": "master",
        "channels": 2,
        "inputs": [],
        "outputs": BUS0,
        "plugins": [{"uid": "sushi.testing.return", "name": "master_return", "type": "internal"}],
    }
    return {
        "host_config": {"samplerate": SR},
        "tracks": [master, channel] if master_first else [channel, master],
        "initial_state": [
            {"processor": "channel_send", "properties": {"destination_name": "master_return"}},
        ],
    }


def render(tmp_path, name: str, config: dict) -> selftest.Probe:
    from sushi_rig.render import Stimulus, render_offline

    path = tmp_path / f"{name}.json"
    path.write_text(json.dumps(config))
    out = render_offline(path, ROOT, tmp_path / name, stimulus=Stimulus(duration_s=1.0),
                         sushi_bin=str(SUSHI))
    return selftest.Probe(name=name, injected=out["stimulus_samples"][:, 0],
                          returned=out["rendered"][:, 0], sample_rate=out["sample_rate"])


def measured(probe: selftest.Probe) -> tuple[int | None, float, float]:
    """(delay in samples, gain in dB, peak out in dBFS), for one message."""
    delay = selftest.measure_delay(probe).samples
    levels = selftest.measure_levels(probe)
    gain = levels.returned_rms_dbfs - levels.injected_rms_dbfs
    return delay, round(gain, 3), round(levels.returned_peak_dbfs, 3)


@needs_sushi
@pytest.mark.slow
def test_send_and_return_cost_what_sushis_source_says(tmp_path):
    """Both orders in one test, so a failure prints every number at once."""
    direct = measured(render(tmp_path, "direct", DIRECT))
    after = measured(render(tmp_path, "master_after", through_master(master_first=False)))
    before = measured(render(tmp_path, "master_before", through_master(master_first=True)))
    report = f"direct {direct}, master after {after}, master before {before}"
    print(report)

    assert direct[0] is not None and after[0] is not None and before[0] is not None, report
    # The level through a 0 dB send is the level direct: nothing doubled, nothing lost.
    assert abs(after[1] - direct[1]) < 0.05, report
    assert abs(before[1] - direct[1]) < 0.05, report
    # Master after the channels: no delay added.
    assert after[0] - direct[0] == 0, report
    # Master before them: one internal chunk.
    assert before[0] - direct[0] == CHUNK, report
