"""The recorder's core, fed by a synthetic source.

Everything a rig machine cannot do on demand is done here on purpose: push
audio faster than real time, stall the writer until the ring overflows, fill
the disk mid-take. What these cannot say is whether a Python callback keeps up
with PipeWire at a 256 quantum; that needs the rig machine, and is the engine's
question, not this module's.
"""

from __future__ import annotations

import pytest

np = pytest.importorskip("numpy")

from sushi_rig import recorder, signals  # noqa: E402
from sushi_rig.recorder import Recorder, Ring  # noqa: E402

SR = 48000
Q = 256


def source(seconds: float, channels: int) -> "np.ndarray":
    """A different sine on every channel, so a column swap would show."""
    t = np.arange(round(SR * seconds)) / SR
    return np.stack([0.5 * np.sin(2 * np.pi * (220 + 110 * c) * t) for c in range(channels)],
                    axis=1).astype(np.float32)


def blocks(audio, size=Q):
    for start in range(0, len(audio), size):
        yield audio[start:start + size]


# --------------------------------------------------------------------------- #
# Sizing
# --------------------------------------------------------------------------- #

def test_the_ring_holds_two_seconds_in_whole_quanta():
    assert recorder.ring_capacity(SR, Q) == 96000
    assert recorder.ring_capacity(SR, 300, headroom_s=1.0) == 48000, "160 blocks of 300"
    assert recorder.ring_capacity(SR, 1000, headroom_s=0.001) == 2000, "never under two blocks"


@pytest.mark.parametrize("args", [(0, Q), (SR, 0), (SR, Q, 0.0)])
def test_a_ring_that_could_not_hold_a_block_is_refused(args):
    with pytest.raises(ValueError):
        recorder.ring_capacity(*args)


# --------------------------------------------------------------------------- #
# The ring
# --------------------------------------------------------------------------- #

def test_what_goes_in_comes_out_in_order_across_the_wrap():
    """Odd block sizes against an odd capacity, so the wrap lands mid-block."""
    ring = Ring(capacity=1000, channels=3)
    audio = source(0.5, 3)
    out = []
    for i, block in enumerate(blocks(audio, 333)):
        assert ring.push(block) == len(block)
        out.append(ring.pull(257 if i % 2 else 401))
    while len(tail := ring.pull(10_000)):
        out.append(tail)
    assert np.array_equal(np.concatenate(out), audio)
    assert ring.dropped == 0


def test_a_full_ring_counts_what_it_cannot_hold_and_never_waits():
    ring = Ring(capacity=512, channels=1)
    assert ring.push(np.ones((400, 1), np.float32)) == 400
    assert ring.push(np.ones((300, 1), np.float32)) == 112, "the rest is dropped, not queued"
    assert ring.dropped == 188
    assert ring.gaps() == [(512, 188)], "lost after the 512th frame kept"


def test_consecutive_overflows_are_one_gap_not_one_per_block():
    """A writer stalled for a second is one episode, however many blocks it is."""
    ring = Ring(capacity=Q, channels=1)
    for _ in range(50):
        ring.push(np.ones((Q, 1), np.float32))
    assert ring.gaps() == [(Q, 49 * Q)]


def test_gaps_past_the_record_limit_are_still_counted():
    ring = Ring(capacity=2, channels=1, max_gaps=2)
    for _ in range(4):
        ring.push(np.ones((3, 1), np.float32))  # one kept frame, two dropped...
        ring.pull(1)                            # ...then room again, so a new gap
    assert len(ring.gaps()) == 2
    assert ring.unrecorded_gaps == 2
    assert ring.dropped == 1 + 2 + 2 + 2, "the first push kept two of three, the rest one"


# --------------------------------------------------------------------------- #
# The recorder, end to end
# --------------------------------------------------------------------------- #

def test_a_take_is_written_exactly_as_pushed_one_stem_per_tap(tmp_path):
    """A stereo processed stem and its mono dry tap, from three columns."""
    audio = source(2.0, 3)
    rec = Recorder([(tmp_path / "guitar.wav", 2), (tmp_path / "guitar-dry.wav", 1)], SR, Q)
    rec.start()
    for block in blocks(audio):
        rec.push(block)
    result = rec.stop()
    assert (result.frames, result.dropped_frames, result.error) == (96000, 0, "")
    assert result.seconds == 2.0
    assert np.array_equal(signals.read_wav(tmp_path / "guitar.wav").samples, audio[:, :2])
    assert np.array_equal(signals.read_wav(tmp_path / "guitar-dry.wav").samples[:, 0], audio[:, 2])


def test_a_stalled_writer_loses_frames_from_every_stem_alike_and_says_so(tmp_path, monkeypatch):
    """The stems stay aligned with each other through a dropout, because they
    share one ring — which is what the take's offset relies on."""
    import threading

    release = threading.Event()
    real_write = signals.WavWriter.write

    def stalled(self, block):
        release.wait()
        real_write(self, block)

    monkeypatch.setattr(signals.WavWriter, "write", stalled)
    audio = source(1.0, 2)
    rec = Recorder([(tmp_path / "a.wav", 1), (tmp_path / "b.wav", 1)], SR, Q, headroom_s=0.1)
    rec.start()
    for block in blocks(audio):
        rec.push(block)
    release.set()
    result = rec.stop()

    assert result.dropped_frames > 0
    assert result.gaps and result.gaps[0][1] > 0
    a = signals.read_wav(tmp_path / "a.wav").samples[:, 0]
    b = signals.read_wav(tmp_path / "b.wav").samples[:, 0]
    assert len(a) == len(b) == result.frames == len(audio) - result.dropped_frames
    kept = audio[:len(a)]
    assert np.array_equal(a, kept[:, 0]) and np.array_equal(b, kept[:, 1]), "row for row"
    assert result.dropouts()[0] == {"at_seconds": round(result.gaps[0][0], 3),
                                    "frames": result.gaps[0][1]}


def test_a_disk_failing_mid_take_is_reported_and_leaves_what_was_written(tmp_path, monkeypatch):
    """A full disk, most likely. The take so far stays readable, because every
    block brings its header up to date."""
    real_write = signals.WavWriter.write
    calls = {"n": 0}

    def fails_later(self, block):
        calls["n"] += 1
        if calls["n"] > 3:
            raise OSError(28, "No space left on device")
        real_write(self, block)

    monkeypatch.setattr(signals.WavWriter, "write", fails_later)
    rec = Recorder([(tmp_path / "a.wav", 1)], SR, Q)
    rec.start()
    for block in blocks(source(1.0, 1)):
        rec.push(block)
    result = rec.stop()
    assert "No space left on device" in result.error
    assert signals.wav_info(tmp_path / "a.wav").frames == result.frames > 0


def test_a_recorder_with_no_stems_is_refused():
    with pytest.raises(ValueError, match="at least one stem"):
        Recorder([], SR, Q)
