"""The capture engine's bookkeeping, fed by stand-in ports carrying a counter.

The counter is the spike's source (`spikes/live_rig/capture_engine.py`), moved
here as a fixture: every column plays a sample counter at its own offset, so a
column in the wrong stem, a lost block or a repeated one shows in the files
read back. Product code never plays anything.

The stand-in ports implement `get_array()` and nothing else, which is all the
pump asks of a JACK port. What this cannot say is whether JACK hands the pump
what a stand-in does, or whether Python keeps up; the rig run said the second
(see `capture.py`), and the first is for the next one.

Its own module because it needs numpy, and `importorskip` mid-module skips
everything in the file; the plan and the checks are in test_capture.py.
"""

from __future__ import annotations

import pytest

np = pytest.importorskip("numpy")

from sushi_rig import capture, signals  # noqa: E402
from sushi_rig.capture import BlockPump  # noqa: E402
from sushi_rig.recorder import Recorder  # noqa: E402

SR = 48000
Q = 256
WRAP = 1 << 20  # k / 2**20 is exact in float32
# A guitar_vocal take: two stereo stems and two mono dry taps, six columns.
STEMS = [("guitar.wav", 2), ("guitar-dry.wav", 1), ("vocal.wav", 2), ("vocal-dry.wav", 1)]
WIDTH = sum(c for _, c in STEMS)


def counter(column: int, start: int, frames: int):
    """The sample counter of one column over `frames` samples from `start`:
    never zero (the silence check would read a zero as silence), and offset per
    column so that columns are told apart."""
    k = (np.arange(frames, dtype=np.int64) + start + 1000 * column) % WRAP + 1
    return (k / WRAP).astype(np.float32)


class Bus:
    """Six stand-in ports on one clock. `cycle(frames)` is one process cycle:
    it moves every port's buffer on, then calls the pump."""

    def __init__(self, width=WIDTH):
        self.position = 0
        self.frames = 0
        self.muted: set[int] = set()
        self.ports = [self.Port(self, column) for column in range(width)]

    class Port:
        def __init__(self, bus, column):
            self.bus, self.column = bus, column

        def get_array(self):
            if self.column in self.bus.muted:
                return np.zeros(self.bus.frames, dtype=np.float32)
            return counter(self.column, self.bus.position, self.bus.frames)

    def cycle(self, pump, frames=Q):
        self.frames = frames
        pump(frames)
        self.position += frames


@pytest.fixture
def rig(tmp_path):
    recorder = Recorder([(tmp_path / name, c) for name, c in STEMS], SR, Q)
    recorder.start()
    bus = Bus()
    pump = BlockPump(bus.ports, recorder.push, Q)
    return recorder, bus, pump, tmp_path


def read_back(tmp_path):
    return np.concatenate([signals.read_wav(tmp_path / name).samples for name, _ in STEMS], axis=1)


def test_every_column_reaches_its_stem_exactly_and_in_order(rig):
    recorder, bus, pump, tmp_path = rig
    for _ in range(200):
        bus.cycle(pump)
    summary = recorder.stop().summary()
    assert summary["frames"] == 200 * Q and summary["lost_frames"] == 0 and summary["error"] == ""
    assert summary["silent_blocks"] == {}
    recorded = read_back(tmp_path)
    for column in range(WIDTH):
        assert np.array_equal(recorded[:, column], counter(column, 0, 200 * Q)), f"column {column}"
    assert pump.blocks == 200 and pump.skipped_blocks == 0 and pump.error == ""


def test_a_block_of_zeros_on_three_ports_is_reported_though_nothing_was_lost(rig):
    """The 31 Oct rig run: three of six ports delivered one block of zeros,
    207 s into a take, with xruns, lost frames and errors all at zero. The
    take now says which three, and when."""
    recorder, bus, pump, tmp_path = rig
    for cycle in range(300):
        bus.muted = {0, 1, 3} if cycle == 187 else set()
        bus.cycle(pump)
    summary = recorder.stop().summary()
    assert summary["lost_frames"] == 0 and summary["dropouts"] == [] and summary["error"] == ""
    assert summary["silent_blocks"] == {
        "guitar.wav[0]": {"count": 1, "first_at_seconds": round(187 * Q / SR, 3)},
        "guitar.wav[1]": {"count": 1, "first_at_seconds": round(187 * Q / SR, 3)},
        "vocal.wav[0]": {"count": 1, "first_at_seconds": round(187 * Q / SR, 3)},
    }
    # And the files hold what the ports delivered: 256 zeros in place, counter
    # continuous either side, the other columns untouched.
    recorded = read_back(tmp_path)
    assert not recorded[187 * Q:188 * Q, [0, 1, 3]].any()
    assert recorded[187 * Q:188 * Q, [2, 4, 5]].all()
    assert (recorded[:, 0] == 0).sum() == Q


def test_a_port_that_stays_silent_is_one_event_and_a_return_then_a_second_drop_is_two(rig):
    recorder, bus, pump, _ = rig
    for cycle in range(60):
        bus.muted = {2} if 10 <= cycle < 30 or 40 <= cycle < 50 else set()
        bus.cycle(pump)
    assert recorder.stop().summary()["silent_blocks"]["guitar-dry.wav"]["count"] == 2


def test_a_block_of_another_length_is_recorded_and_counted_not_dropped(rig):
    """A block size that changes mid-take. The spike skipped these, leaving the
    take short with nothing saying so."""
    recorder, bus, pump, tmp_path = rig
    for frames in (Q, Q // 2, Q * 2, Q):
        bus.cycle(pump, frames)
    summary = recorder.stop().summary()
    assert summary["frames"] == Q + Q // 2 + 2 * Q + Q
    assert pump.off_quantum_blocks == 2 and pump.skipped_blocks == 0
    recorded = read_back(tmp_path)
    for column in range(WIDTH):
        assert np.array_equal(recorded[:, column], counter(column, 0, len(recorded)))


def test_a_block_too_long_for_the_buffer_is_counted_in_frames_and_not_pushed():
    pushed = []
    bus = Bus(2)
    pump = BlockPump(bus.ports, lambda block: pushed.append(len(block)) or len(block), Q, max_frames=512)
    bus.cycle(pump, 512)
    bus.cycle(pump, 513)
    bus.cycle(pump, 0)
    assert pushed == [512]
    assert (pump.blocks, pump.skipped_blocks, pump.skipped_frames) == (1, 2, 513)


def test_an_exception_in_the_callback_is_held_not_raised_and_the_cycles_go_on():
    """JACK removes a client whose callback raises, which would end the capture
    without a word. So the first error is kept, the cycle counted as skipped,
    and the next cycle tried."""
    bus = Bus(2)
    calls = []

    def push(block):
        calls.append(len(block))
        if len(calls) == 2:
            raise OSError("ring on fire")
        return len(block)

    pump = BlockPump(bus.ports, push, Q)
    for _ in range(4):
        bus.cycle(pump)
    assert calls == [Q] * 4
    assert pump.blocks == 3 and pump.skipped_blocks == 1 and pump.skipped_frames == Q
    assert pump.error == "OSError: ring on fire"


def test_a_port_that_hands_back_the_wrong_length_is_an_error_not_a_broadcast():
    bus = Bus(2)
    bus.ports[1].get_array = lambda: np.zeros(100, dtype=np.float32)
    pump = BlockPump(bus.ports, lambda block: len(block), Q)
    bus.cycle(pump)
    assert pump.blocks == 0 and pump.error.startswith("ValueError")


def test_xruns_and_block_size_changes_are_counted():
    pump = BlockPump(Bus(1).ports, lambda block: 0, Q)
    pump.xrun(12.5)
    pump.xrun()
    pump.blocksize(Q)  # the callback JACK makes on activation: no change
    pump.blocksize(1024)
    assert (pump.xruns, pump.quantum_changes) == (2, 1)


def test_the_slowest_callback_is_kept_against_the_budget():
    ticks = iter([0, 100, 1000, 6000, 7000, 7050])  # three cycles: 100, 5000 and 50 ns
    bus = Bus(1)
    pump = BlockPump(bus.ports, lambda block: 0, Q, clock=lambda: next(ticks))
    for _ in range(3):
        bus.cycle(pump)
    account = pump.account(SR)
    assert account["slowest_callback_us"] == 5.0
    assert account["budget_us"] == round(1e6 * Q / SR, 1) == 5333.3


def test_the_account_always_has_the_same_keys_so_clean_differs_from_absent():
    account = BlockPump(Bus(1).ports, lambda block: 0, Q).account(SR)
    assert set(account) == {
        "quantum", "blocks", "xruns", "quantum_changes", "off_quantum_blocks",
        "skipped_blocks", "skipped_frames", "slowest_callback_us", "budget_us", "error",
    }
    assert account["xruns"] == 0 and account["error"] == ""


@pytest.mark.parametrize("args", [((), Q), (Bus(1).ports, 0), (Bus(1).ports, 1024)])
def test_a_pump_that_could_not_hold_a_block_is_refused(args):
    ports, quantum = args
    with pytest.raises(ValueError):
        BlockPump(ports, lambda block: 0, quantum, max_frames=512)
