"""The recorder's core: what a take's audio passes through on its way to disk.

Issue #53. Writing to disk from the audio callback causes xruns, which at a 256
quantum means dropouts. So the shape is the one the issue asks for: the audio
thread copies each block into a ring buffer and does nothing else, and a writer
thread drains the ring into the take's stems.

## What is here, and what is not

Here: the ring, how big it is, the writer thread, the files, and the report of
what was captured and what was lost. All of it is tested by feeding it a
synthetic source, faster than real time and with the writer deliberately
stalled, which a test can do and a rig machine cannot.

Not here: the engine — whatever takes audio from PipeWire and calls `push`.
That needs the rig machine, and it holds the biggest open question in Phase 2:
whether a Python callback can keep up at a 256 quantum without xruns. If it
cannot, the engine becomes a small native helper, and nothing in this module
changes, because `push` is all the engine sees of it.

## Every tap in one ring

All the take's taps travel through one ring, as the columns of each block. So
if the writer ever falls behind, every stem loses the same frames at the same
moment and they stay aligned with each other — which is what the take's offset
describes. Lost frames are not replaced with silence: inserting it would need
the audio thread and the writer to agree on positions while both run. So the
take's absolute time shifts by what was lost, and the report says where and how
much, for `take.yaml` to carry. A take with dropouts is a take that says so.

## Realtime safety, as far as Python allows

`push` allocates nothing and never waits. The ring and its gap records are
allocated up front, and a block that does not fit is counted, not queued. It
relies on CPython's interpreter lock for the order in which the two threads see
the ring's indices; each index has a single writer, so that ordering is all it
needs.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

from ._numpy import require_numpy
from .signals import WavWriter

# How long the writer may stall — a slow disk, a busy machine — before the
# ring is full and frames are lost. Two seconds is a margin chosen, not
# measured: the rig machine is where it gets measured, by a take that reports
# its dropouts. At 48 kHz and four stereo-or-mono taps it is a few megabytes.
HEADROOM_S = 2.0

# How often the writer pushes everything to disk. A killed process loses
# nothing the writer has written; a power cut loses up to this much.
SYNC_EVERY_S = 2.0

# Gap records kept before they too are only counted. Consecutive overflows
# merge into one record, so this is distinct episodes, not blocks.
MAX_GAPS = 1024


def ring_capacity(sample_rate: int, quantum: int, headroom_s: float = HEADROOM_S) -> int:
    """Frames the ring holds: `headroom_s` of audio, in whole quanta, and never
    fewer than two, so one block can be read while the next is written."""
    if sample_rate <= 0 or quantum <= 0:
        raise ValueError(f"sample_rate and quantum must be positive, got {sample_rate} and {quantum}")
    if not headroom_s > 0:
        raise ValueError(f"headroom_s must be positive, got {headroom_s}")
    blocks = -(-int(sample_rate * headroom_s) // quantum)
    return max(2, blocks) * quantum


class Ring:
    """Single producer, single consumer, and the producer never waits.

    `push` is what the audio thread calls, `pull` what the writer calls. Each
    index has exactly one writer: the producer moves `_written` and the
    consumer `_read`, and each only reads the other's.
    """

    def __init__(self, capacity: int, channels: int, max_gaps: int = MAX_GAPS):
        np = require_numpy()
        if capacity < 1 or channels < 1:
            raise ValueError(f"capacity and channels must be at least 1, got {capacity} and {channels}")
        self.capacity = capacity
        self.channels = channels
        self._buffer = np.zeros((capacity, channels), dtype=np.float32)
        self._written = 0
        self._read = 0
        # Read only once recording has stopped, so the producer is their only
        # writer while it runs and needs no coordination with the consumer.
        self._gap_at = np.zeros(max_gaps, dtype=np.int64)
        self._gap_frames = np.zeros(max_gaps, dtype=np.int64)
        self._gaps = 0
        self.dropped = 0
        self.unrecorded_gaps = 0

    def push(self, block) -> int:
        """Copy in as much of `block` as fits; count the rest. Returns frames kept."""
        frames = len(block)
        room = self.capacity - (self._written - self._read)
        kept = frames if frames <= room else room
        if kept:
            start = self._written % self.capacity
            first = min(kept, self.capacity - start)
            self._buffer[start:start + first] = block[:first]
            if kept > first:
                self._buffer[:kept - first] = block[first:kept]
        if kept < frames:
            self._gap(self._written + kept, frames - kept)
        self._written += kept  # published last, after the frames it covers
        return kept

    def _gap(self, at: int, frames: int) -> None:
        self.dropped += frames
        if self._gaps and self._gap_at[self._gaps - 1] == at:
            self._gap_frames[self._gaps - 1] += frames
        elif self._gaps < len(self._gap_at):
            self._gap_at[self._gaps] = at
            self._gap_frames[self._gaps] = frames
            self._gaps += 1
        else:
            self.unrecorded_gaps += 1

    def pull(self, max_frames: int):
        """Up to `max_frames` frames, oldest first, as a copy. Empty when none."""
        np = require_numpy()
        frames = min(max_frames, self._written - self._read)
        if frames <= 0:
            return np.zeros((0, self.channels), dtype=np.float32)
        start = self._read % self.capacity
        first = min(frames, self.capacity - start)
        out = np.empty((frames, self.channels), dtype=np.float32)
        out[:first] = self._buffer[start:start + first]
        if frames > first:
            out[first:] = self._buffer[:frames - first]
        self._read += frames
        return out

    def gaps(self) -> list[tuple[int, int]]:
        """(frame the loss came after, frames lost), for once recording has stopped."""
        return [(int(a), int(f)) for a, f in zip(self._gap_at[:self._gaps], self._gap_frames[:self._gaps])]


@dataclass
class Recording:
    """What a take captured, and what it did not."""

    frames: int
    sample_rate: int
    dropped_frames: int = 0
    # (seconds into the take the loss came after, frames lost)
    gaps: list[tuple[float, int]] = field(default_factory=list)
    unrecorded_gaps: int = 0
    error: str = ""

    @property
    def seconds(self) -> float:
        return self.frames / self.sample_rate

    def dropouts(self) -> list[dict[str, Any]]:
        """The gaps as `take.yaml` records them."""
        return [{"at_seconds": round(at, 3), "frames": frames} for at, frames in self.gaps]


class Recorder:
    """A take's audio, from `push` to its stems on disk.

    `stems` is (path, channels) in the order their channels sit in each pushed
    block: a block for a guitar's processed stereo stem, its mono dry tap and a
    vocal's two is six columns wide. Each stem gets its own `WavWriter`.
    """

    def __init__(self, stems: Sequence[tuple[Path, int]], sample_rate: int, quantum: int,
                 *, headroom_s: float = HEADROOM_S, sync_every_s: float = SYNC_EVERY_S):
        if not stems:
            raise ValueError("a recording needs at least one stem")
        self.stems = [(Path(path), int(channels)) for path, channels in stems]
        self.sample_rate = sample_rate
        self.quantum = quantum
        self.sync_every_s = sync_every_s
        self.ring = Ring(ring_capacity(sample_rate, quantum, headroom_s),
                         sum(channels for _, channels in self.stems))
        self._writers: list[WavWriter] = []
        self._thread: threading.Thread | None = None
        self._stopping = threading.Event()
        self._error = ""

    def start(self) -> None:
        self._writers = [WavWriter(path, channels, self.sample_rate) for path, channels in self.stems]
        self._thread = threading.Thread(target=self._drain, name="recorder-writer", daemon=True)
        self._thread.start()

    def push(self, block) -> int:
        """What the audio thread calls, once a block. Never blocks, never allocates."""
        return self.ring.push(block)

    def _write(self, block) -> None:
        column = 0
        for writer in self._writers:
            writer.write(block[:, column:column + writer.channels])
            column += writer.channels

    def _drain(self) -> None:
        idle = self.quantum / self.sample_rate
        last_sync = time.monotonic()
        try:
            while True:
                block = self.ring.pull(self.quantum * 16)
                if len(block):
                    self._write(block)
                elif self._stopping.is_set():
                    return
                else:
                    time.sleep(idle)
                if time.monotonic() - last_sync >= self.sync_every_s:
                    for writer in self._writers:
                        writer.sync()
                    last_sync = time.monotonic()
        except Exception as exc:  # noqa: BLE001 — a recorder must report, not vanish
            # A full disk, most likely. What is already written stays readable,
            # because every block brings its file's header up to date; the
            # producer keeps pushing into a ring nobody drains, so the loss is
            # counted as well as reported.
            self._error = f"{type(exc).__name__}: {exc}"

    def stop(self) -> Recording:
        """Drain what is left, close the files, and say what was captured."""
        self._stopping.set()
        if self._thread is not None:
            self._thread.join()
        for writer in self._writers:
            try:
                writer.close()
            except OSError as exc:
                self._error = self._error or f"{type(exc).__name__}: {exc}"
        frames = self._writers[0].frames if self._writers else 0
        return Recording(
            frames=frames,
            sample_rate=self.sample_rate,
            dropped_frames=self.ring.dropped,
            gaps=[(at / self.sample_rate, lost) for at, lost in self.ring.gaps()],
            unrecorded_gaps=self.ring.unrecorded_gaps,
            error=self._error,
        )
