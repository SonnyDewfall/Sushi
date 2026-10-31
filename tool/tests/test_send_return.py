"""What Sushi's send and return add to the path you hear, measured.

The monitor mix is made in Sushi (decided 2026-10-31): each channel keeps its
own output, so stems exist, and sends a copy to a master track that feeds the
monitors. That puts `sushi.testing.send` and `sushi.testing.return` in the
monitor path, so what they add has to be known before anything is built on them.

Sushi 1.3.0's source says what to expect (return_plugin.cpp, `send_audio`): a
send that runs on the return's thread *before* the return has processed this
block writes straight into the block the return is about to play — no delay. A
send that runs after it writes into the next block — one internal chunk late,
`AUDIO_CHUNK_SIZE`, 64 samples in the stock build. Not a PipeWire quantum: Sushi
processes each 256-sample quantum as four 64-sample chunks.

So the order of tracks in the config should be the whole story. These tests
render through a real Sushi and compare whole stereo renders, sample for sample,
against a plain passthrough. Each channel of the stimulus is a different signal,
so a send that dropped, swapped or inverted a channel would not match.

What this cannot say: whether the live JACK frontend runs tracks on more than
one thread. A send on another thread is always a chunk late. `up` runs Sushi
without `--multicore-processing`, so it should be one thread there too; that is
for the rig machine to confirm.
"""

from __future__ import annotations

import json

import pytest

np = pytest.importorskip("numpy")

from sushi_rig.paths import checkout_root  # noqa: E402
from sushi_rig.render import Stimulus, render_offline  # noqa: E402

ROOT = checkout_root()
SUSHI = ROOT / "sushi"
SR = 48000
# A shift of a PipeWire block or more would be a different mechanism from the
# one the source describes, and would be audible as one.
QUANTUM = 256
# Passthrough is a copy, so a matching render matches to float rounding.
EXACT = 1e-6

needs_sushi = pytest.mark.skipif(not SUSHI.exists(), reason="needs the Sushi binary")


class Distinct(Stimulus):
    """The default sweep on the left, and on the right the same sweep inverted
    and 6 dB down: a dropped, swapped or inverted channel cannot match."""

    def render(self, sample_rate: int):
        fed = super().render(sample_rate).copy()
        fed[:, 1] *= -0.5
        return fed


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


def through_master(*, master_first: bool, channel_output: bool) -> dict:
    """A channel sending to a master track's return.

    `channel_output` is the decided design: the channel keeps its own output as
    well as sending. With only one stereo bus offline, both land on bus 0, so the
    render is the channel twice — which is what shows the send copies the signal
    without taking anything from the channel's own output.
    """
    channel = passthrough_track("channel", BUS0, BUS0 if channel_output else [])
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


def render(tmp_path, name: str, config: dict) -> "np.ndarray":
    path = tmp_path / f"{name}.json"
    path.write_text(json.dumps(config))
    out = render_offline(path, ROOT, tmp_path / name, stimulus=Distinct(duration_s=1.0),
                         sushi_bin=str(SUSHI))
    return out["rendered"]


def shift_matching(rendered, direct) -> int | None:
    """The delay, in samples, at which `rendered` is `direct` exactly, if any."""
    for shift in range(QUANTUM):
        later = rendered[shift:]
        if np.max(np.abs(later - direct[:len(later)])) < EXACT:
            return shift
    return None


@pytest.fixture(scope="module")
def direct(tmp_path_factory):
    rendered = render(tmp_path_factory.mktemp("direct"), "direct", DIRECT)
    # Something must come out, and on both sides, or every comparison below
    # is between silences.
    assert np.max(np.abs(rendered[:, 0])) > 0.1 and np.max(np.abs(rendered[:, 1])) > 0.05
    return rendered


@needs_sushi
@pytest.mark.slow
def test_a_master_after_the_channel_adds_nothing(tmp_path, direct):
    rendered = render(tmp_path, "after", through_master(master_first=False, channel_output=False))
    assert shift_matching(rendered, direct) == 0


@needs_sushi
@pytest.mark.slow
def test_a_master_before_the_channel_is_late_by_less_than_a_block(tmp_path, direct):
    """One internal chunk, by the source: 64 samples in the stock build, which
    is what 1.3.0 measured here. Asserted as a shift under one PipeWire block
    rather than as 64, so a build with another chunk size still reads true."""
    rendered = render(tmp_path, "before", through_master(master_first=True, channel_output=False))
    shift = shift_matching(rendered, direct)
    print(f"master before the channel: {shift} samples late")
    assert shift is not None and 0 < shift < QUANTUM, shift


@needs_sushi
@pytest.mark.slow
def test_a_channel_that_sends_keeps_its_own_output(tmp_path, direct):
    """The decided design: the channel's own output and the master's copy, both
    on bus 0 here, sum to the channel exactly twice over."""
    rendered = render(tmp_path, "both", through_master(master_first=False, channel_output=True))
    assert np.max(np.abs(rendered - 2 * direct)) < EXACT
