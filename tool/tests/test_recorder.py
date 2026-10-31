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



# --------------------------------------------------------------------------- #
# Found in review
# --------------------------------------------------------------------------- #

def test_a_block_of_the_wrong_width_is_refused_not_broadcast():
    """NumPy would copy one mono column into every tap, and every stem would
    hold the same signal under a different name."""
    ring = Ring(capacity=64, channels=3)
    with pytest.raises(ValueError, match="3-channel ring"):
        ring.push(np.ones((4, 1), np.float32))
    with pytest.raises(ValueError):
        ring.push(np.ones(4, np.float32))


def test_episodes_past_the_gap_table_are_counted_once_each():
    """Once the table was full, every overflowing block counted as an episode."""
    ring = Ring(capacity=4, channels=1, max_gaps=1)
    ring.push(np.ones((6, 1), np.float32))          # episode one, recorded
    ring.pull(4)
    ring.push(np.ones((4, 1), np.float32))          # fills the ring again
    for _ in range(5):
        ring.push(np.ones((2, 1), np.float32))      # episode two, five blocks long
    assert ring.unrecorded_gaps == 1


def drained(rec: Recorder, timeout: float = 5.0) -> None:
    """Wait for the writer to catch up, so a test can say when it writes."""
    import time

    deadline = time.monotonic() + timeout
    while rec.ring._read != rec.ring._written:
        assert time.monotonic() < deadline, "the writer never caught up"
        time.sleep(0.005)


def test_a_writer_that_dies_accounts_for_everything_after_it(tmp_path, monkeypatch):
    """The frames left in its ring used to go uncounted, and the gaps after it
    to point past the end of the files. Staged in two halves so the writer
    really dies mid-take with audio still arriving."""
    real_write = signals.WavWriter.write
    calls = {"n": 0}

    def second_write_fails(self, block):
        calls["n"] += 1
        if calls["n"] > 1:
            raise OSError(28, "No space left on device")
        real_write(self, block)

    monkeypatch.setattr(signals.WavWriter, "write", second_write_fails)
    audio = source(1.0, 1)
    rec = Recorder([(tmp_path / "a.wav", 1)], SR, Q, headroom_s=0.05)
    rec.start()
    for block in blocks(audio[:2560]):
        rec.push(block)
    drained(rec)
    for block in blocks(audio[2560:]):
        rec.push(block)
    result = rec.stop()

    assert result.frames == 2560 == signals.wav_info(tmp_path / "a.wav").frames
    assert result.frames + result.dropped_frames == len(audio), "nothing unaccounted for"
    assert all(at <= result.seconds for at, _ in result.gaps), "no gap past the end"
    assert result.gaps[-1] == (result.seconds, len(audio) - 2560)
    assert result.summary()["error"].startswith("OSError")


def test_a_writer_failing_part_way_through_a_block_leaves_the_stems_equal(tmp_path, monkeypatch):
    """The stems before it held the block and those after did not."""
    real_write = signals.WavWriter.write

    def second_stem_fails_once(self, block):
        if self.path.name == "b.wav" and self.frames >= 512:
            raise OSError(28, "No space left on device")
        real_write(self, block)

    monkeypatch.setattr(signals.WavWriter, "write", second_stem_fails_once)
    rec = Recorder([(tmp_path / "a.wav", 1), (tmp_path / "b.wav", 1)], SR, Q)
    rec.start()
    for block in blocks(source(0.1, 2)):
        rec.push(block)
    result = rec.stop()
    a = signals.wav_info(tmp_path / "a.wav").frames
    b = signals.wav_info(tmp_path / "b.wav").frames
    assert a == b == result.frames, "cut back to the shortest, as a dropout would be"
    assert result.frames + result.dropped_frames == round(SR * 0.1)


def test_a_recorder_that_cannot_open_every_stem_closes_the_ones_it_did(tmp_path, monkeypatch):
    opened = []
    real = signals.WavWriter

    class Counting(real):
        def __init__(self, *args, **kwargs):
            if len(opened) == 2:
                raise OSError(28, "No space left on device")
            super().__init__(*args, **kwargs)
            opened.append(self)

    monkeypatch.setattr(recorder, "WavWriter", Counting)
    rec = Recorder([(tmp_path / f"{n}.wav", 1) for n in "abc"], SR, Q)
    with pytest.raises(OSError, match="No space"):
        rec.start()
    assert len(opened) == 2
    assert all(writer._file.closed for writer in opened)


# --------------------------------------------------------------------------- #
# A disk that fills part way through a block
# --------------------------------------------------------------------------- #

class FullDisk:
    """A stem's file on a disk with `capacity` bytes of room: a write that does
    not fit lands what does and returns the short count, the next one raises
    ENOSPC. That is what an unbuffered file does on a full disk, which was
    checked on a real 64 KiB tmpfs; everything else passes through to the real
    file, so the bytes are really on disk or really not."""

    def __init__(self, raw, capacity):
        self._raw = raw
        self._capacity = capacity

    def write(self, data):
        room = max(0, self._capacity - self._raw.tell())
        if room == 0 and len(data):
            raise OSError(28, "No space left on device")
        return self._raw.write(bytes(data[:room]))

    def __getattr__(self, name):
        return getattr(self._raw, name)


def fill_disk_under(monkeypatch, stem, frames, stray=0):
    """`stem`'s disk holds `frames` whole frames and `stray` bytes more."""
    real_init = signals.WavWriter.__init__

    def init(self, path, *args, **kwargs):
        real_init(self, path, *args, **kwargs)
        if signals.Path(path).name == stem:
            self._file = FullDisk(self._file, signals._DATA_AT + frames * self._block_align + stray)

    monkeypatch.setattr(signals.WavWriter, "__init__", init)


def test_a_writer_counts_only_the_frames_that_reached_the_file(tmp_path, monkeypatch):
    fill_disk_under(monkeypatch, "a.wav", 300, stray=3)
    with signals.WavWriter(tmp_path / "a.wav", 1, SR) as writer:
        writer.write(np.zeros((256, 1), np.float32))
        with pytest.raises(OSError, match="No space"):
            writer.write(np.zeros((256, 1), np.float32))
        assert writer.frames == 300, "44 of the second block landed, and the 3 bytes after them are not a frame"
        assert (tmp_path / "a.wav").stat().st_size == signals._DATA_AT + 300 * 4 + 3
        writer.truncate(writer.frames)
    assert signals.wav_info(tmp_path / "a.wav").frames == 300


def test_stems_cut_back_to_what_the_dying_one_holds_not_what_it_was_asked_for(tmp_path, monkeypatch):
    """One stem fills its disk part way through a block. The count it reported
    ran ahead of the file, so the take claimed 768 frames while that stem held
    512, no terminal gap was made, and the other stems were cut to a length
    the dying one never reached."""
    fill_disk_under(monkeypatch, "b.wav", 512, stray=3)
    audio = source(0.05, 2)  # 2400 frames, ten blocks
    rec = Recorder([(tmp_path / "a.wav", 1), (tmp_path / "b.wav", 1)], SR, Q)
    rec.start()
    for block in blocks(audio):
        rec.push(block)
    result = rec.stop()

    assert "No space left on device" in result.error
    assert result.frames == 512
    assert [signals.wav_info(tmp_path / n).frames for n in ("a.wav", "b.wav")] == [512, 512]
    assert result.frames + result.dropped_frames == len(audio)
    assert result.gaps[-1] == (512 / SR, len(audio) - 512), "the loss is one gap at the end"
    kept = signals.read_wav(tmp_path / "a.wav").samples
    assert np.array_equal(kept[:, 0], audio[:512, 0])


def test_stray_bytes_after_a_failed_write_are_cut_even_from_a_stem_already_the_right_length(
        tmp_path, monkeypatch):
    """Nothing to cut by count, so only an unconditional truncate removes the
    bytes of a frame that never finished; `wav_info` refuses them and
    `repair_wav` would make them a frame of noise."""
    fill_disk_under(monkeypatch, "a.wav", 512, stray=3)
    rec = Recorder([(tmp_path / "a.wav", 1)], SR, Q)
    rec.start()
    for block in blocks(source(0.05, 1)):
        rec.push(block)
    result = rec.stop()
    assert result.frames == 512
    assert (tmp_path / "a.wav").stat().st_size == signals._DATA_AT + 512 * 4
    assert signals.wav_info(tmp_path / "a.wav").frames == 512


def test_a_stem_that_cannot_be_cut_back_is_still_closed(tmp_path, monkeypatch):
    """A failed cut used to skip the close, so its fsync never happened."""
    fill_disk_under(monkeypatch, "a.wav", 512)
    closed = []
    real_close = signals.WavWriter.close

    def cut_fails(self, frames):
        raise OSError(5, "Input/output error")

    def noting_close(self):
        closed.append(self.path.name)
        real_close(self)

    monkeypatch.setattr(signals.WavWriter, "truncate", cut_fails)
    monkeypatch.setattr(signals.WavWriter, "close", noting_close)
    rec = Recorder([(tmp_path / "a.wav", 1), (tmp_path / "b.wav", 1)], SR, Q)
    rec.start()
    for block in blocks(source(0.05, 2)):
        rec.push(block)
    result = rec.stop()
    assert sorted(closed) == ["a.wav", "b.wav"]
    assert "No space left on device" in result.error, "the first error stays the one reported"


def test_a_block_after_a_failed_one_lands_on_a_frame_boundary(tmp_path, monkeypatch):
    """The failed block left 3 bytes past its last whole frame. The next block
    goes over them, not after them, or every sample from there on is shifted."""
    fill_disk_under(monkeypatch, "a.wav", 300, stray=3)
    audio = source(0.01, 1)  # 480 frames
    with signals.WavWriter(tmp_path / "a.wav", 1, SR) as writer:
        writer.write(audio[:256])
        with pytest.raises(OSError):
            writer.write(audio[256:512])
        writer._file._capacity = 1 << 20  # space is freed
        writer.write(audio[300:480])
    back = signals.read_wav(tmp_path / "a.wav").samples
    assert len(back) == 480 and np.array_equal(back[:, 0], audio[:, 0])
