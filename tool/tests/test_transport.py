"""Record and stop (#57): the decisions on their own, then a whole take.

The take is real — a folder, a recorder, stems on disk — and only the engine is
fake: a thread pushing synthetic blocks, as the rig's capture engine will push
PipeWire's. What this cannot say is anything about PipeWire; that is the
engine's question, and the rig machine's.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime

import pytest

from sushi_rig import take
from sushi_rig.paths import checkout_root
from sushi_rig.take import DRY, PROCESSED, Stem
from sushi_rig.transport import (
    NOT_MEASURED,
    NoEngine,
    TakePlan,
    TakeSession,
    Transport,
    TransportError,
    auto_name,
)

SR = 48000
Q = 256
NOW = datetime(2026, 10, 31, 14, 25, 1)


class FakeSession:
    """Records what the transport asked of it, and does what it is told."""

    def __init__(self, start_error=None, stop_error=None):
        self.calls = []
        self.start_error = start_error
        self.stop_error = stop_error

    def start(self, name):
        self.calls.append(("start", name))
        if self.start_error:
            raise self.start_error
        return f"recording {name}"

    def stop(self):
        self.calls.append(("stop",))
        if self.stop_error:
            raise self.stop_error
        return "recorded"


# --------------------------------------------------------------------------- #
# Decisions
# --------------------------------------------------------------------------- #

def test_a_take_recorded_without_a_name_is_named_by_when_it_was_recorded():
    assert auto_name(NOW) == "take-20261031-142501"
    assert not take.check_name(auto_name(NOW))


def test_record_uses_the_last_name_received():
    session = FakeSession()
    transport = Transport(session, clock=lambda: NOW)
    transport.on_name("  riff-idea ")
    assert transport.on_record() == "recording riff-idea"
    assert session.calls == [("start", "riff-idea")]
    assert transport.recording == "riff-idea"


def test_record_with_no_name_still_records():
    session = FakeSession()
    assert Transport(session, clock=lambda: NOW).on_record() == "recording take-20261031-142501"


def test_a_name_that_could_escape_the_takes_folder_never_reaches_the_session():
    session = FakeSession()
    transport = Transport(session)
    transport.on_name("../../etc")
    said = transport.on_record()
    assert said.startswith("record refused: invalid take name")
    assert session.calls == []
    assert transport.recording is None


def test_record_while_recording_is_refused_not_restarted():
    session = FakeSession()
    transport = Transport(session)
    transport.on_name("riff-idea")
    transport.on_record()
    transport.on_name("other")
    assert transport.on_record() == "already recording riff-idea — press STOP first"
    assert session.calls == [("start", "riff-idea")]


def test_stop_while_stopped_says_so():
    session = FakeSession()
    assert Transport(session).on_stop() == "not recording"
    assert session.calls == []


def test_stop_ends_the_take_and_record_works_again():
    session = FakeSession()
    transport = Transport(session)
    transport.on_name("riff-idea")
    transport.on_record()
    assert transport.on_stop() == "recorded"
    assert transport.recording is None
    assert transport.on_record() == "recording riff-idea"


def test_a_take_that_would_not_start_leaves_the_transport_stopped():
    transport = Transport(FakeSession(start_error=TransportError("no ports")))
    assert transport.on_record() == "record failed: no ports"
    assert transport.recording is None
    assert transport.on_stop() == "not recording"


@pytest.mark.parametrize("error", [TransportError("disk gone"), RuntimeError("disk gone")])
def test_a_stop_that_fails_still_stops(error):
    """Otherwise a failed stop leaves the player unable to record again."""
    transport = Transport(FakeSession(stop_error=error))
    transport.on_name("riff-idea")
    transport.on_record()
    said = transport.on_stop()
    assert said.startswith("stop failed for riff-idea:") and "disk gone" in said
    assert transport.recording is None


def test_an_unexpected_error_never_reaches_the_listener():
    transport = Transport(FakeSession(start_error=RuntimeError("boom")))
    assert transport.on_record() == "record failed: unexpected error: boom"


def test_without_an_engine_record_says_why_it_cannot():
    transport = Transport()
    said = transport.on_record()
    assert said == f"record failed: {NoEngine.MESSAGE}"
    assert "no capture engine" in said
    assert transport.recording is None


def test_a_reloaded_panel_forgets_the_name_it_no_longer_shows():
    """The reload empties the name field; RECORD on an empty field must not
    record, and archive, under a name typed before it."""
    session = FakeSession()
    transport = Transport(session, clock=lambda: NOW)
    transport.on_name("riff-idea")
    assert transport.on_panel_reloaded() is None
    assert transport.on_record() == "recording take-20261031-142501"


def test_a_reloaded_panel_is_told_a_take_is_still_running():
    transport = Transport(FakeSession())
    transport.on_name("riff-idea")
    transport.on_record()
    assert transport.on_panel_reloaded() == "recording riff-idea"
    assert transport.recording == "riff-idea"


def test_closing_the_listener_finishes_a_running_take():
    session = FakeSession()
    transport = Transport(session)
    assert transport.close() is None
    transport.on_record()
    assert transport.close() == "recorded"
    assert session.calls[-1] == ("stop",)


# --------------------------------------------------------------------------- #
# A whole take
# --------------------------------------------------------------------------- #

np = pytest.importorskip("numpy")

from sushi_rig import signals  # noqa: E402

WIDTHS = (2, 1, 2, 1)  # guitar stereo, its mono dry tap, vocal stereo, its dry tap


def plan(tmp_path, stems=None) -> TakePlan:
    stems = stems or [
        Stem("guitar", PROCESSED, "sushi:audio_output_0"),
        Stem("guitar", DRY, "Audient iD14:capture_AUX0"),
        Stem("vocal", PROCESSED, "sushi:audio_output_2"),
        Stem("vocal", DRY, "Audient iD14:capture_AUX1"),
    ]
    return TakePlan(
        takes_dir=tmp_path,
        config=json.loads((checkout_root() / "config" / "guitar_vocal.json").read_text()),
        config_source="config/guitar_vocal.json",
        stems=tuple(zip(stems, WIDTHS)),
        sample_rate=SR,
        quantum=Q,
    )


def source(seconds: float) -> "np.ndarray":
    """A different level on every column, so a stem given the wrong columns shows."""
    frames = round(SR * seconds)
    return np.tile(np.arange(1, sum(WIDTHS) + 1, dtype=np.float32) / 10, (frames, 1))


class FakeEngine:
    """Pushes `audio` from its own thread, a quantum at a time, as the capture
    engine will from PipeWire's."""

    def __init__(self, audio=None, start_error=None, stop_error=None):
        self.audio = source(0.5) if audio is None else audio
        self.start_error = start_error
        self.stop_error = stop_error
        self.stems = None
        self._thread = None

    def start(self, stems, push):
        if self.start_error:
            raise self.start_error
        self.stems = stems
        self._thread = threading.Thread(target=self._run, args=(push,))
        self._thread.start()

    def _run(self, push):
        for at in range(0, len(self.audio), Q):
            push(self.audio[at:at + Q])

    def stop(self):
        self._thread.join()
        if self.stop_error:
            raise self.stop_error


def test_a_take_from_record_to_stop(tmp_path):
    engine = FakeEngine()
    transport = Transport(TakeSession(plan(tmp_path), engine))
    transport.on_name("riff-idea")
    assert transport.on_record() == "recording riff-idea"
    assert transport.on_stop() == "recorded riff-idea: 0.5 s"

    finished = take.load_take(tmp_path / "riff-idea")
    assert finished.complete
    assert take.check_take(finished) == []
    assert finished.metadata["capture"]["lost_frames"] == 0
    assert finished.metadata["alignment"]["measured_by"] == NOT_MEASURED
    assert "was not measured" in finished.metadata["alignment"]["note"]

    # Each stem holds its own columns, all of them, and nothing else.
    columns = iter(range(sum(WIDTHS)))
    for stem, width in engine.stems:
        data = signals.read_wav(tmp_path / "riff-idea" / stem.filename)
        assert data.samples.shape == (round(SR * 0.5), width)
        for column in range(width):
            expected = (next(columns) + 1) / 10
            assert np.allclose(data.samples[:, column], expected)


def test_recording_over_a_name_keeps_the_earlier_take_and_says_where(tmp_path):
    session = TakeSession(plan(tmp_path), FakeEngine(), clock=lambda: NOW.astimezone())
    assert session.start("riff-idea") == "recording riff-idea"
    session.stop()
    session.engine = FakeEngine()
    assert session.start("riff-idea") == (
        "recording riff-idea — the earlier riff-idea is in _archive/riff-idea/"
    )
    session.stop()
    assert len(list((tmp_path / take.ARCHIVE_DIR / "riff-idea").iterdir())) == 1


def test_a_disk_error_starting_a_take_is_a_refusal_and_leaves_no_folder(tmp_path, monkeypatch):
    def full(path, text):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(take, "_write_atomic", full)
    session = TakeSession(plan(tmp_path), FakeEngine())
    with pytest.raises(TransportError, match="cannot record riff-idea: .*No space left"):
        session.start("riff-idea")
    assert list(tmp_path.iterdir()) == []


def test_an_engine_that_fails_part_way_through_starting_is_stopped(tmp_path):
    class HalfStarted(FakeEngine):
        stopped = False

        def start(self, stems, push):
            raise OSError("second port missing")

        def stop(self):
            self.stopped = True

    engine = HalfStarted()
    session = TakeSession(plan(tmp_path), engine)
    with pytest.raises(TransportError, match="second port missing"):
        session.start("riff-idea")
    assert engine.stopped


def test_a_take_that_cannot_be_honest_is_refused_before_anything_is_written(tmp_path):
    shared = [
        Stem("guitar", PROCESSED, "sushi:audio_output_0"),
        Stem("guitar", DRY, "Audient iD14:capture_AUX0"),
        Stem("vocal", PROCESSED, "sushi:audio_output_0"),
        Stem("vocal", DRY, "Audient iD14:capture_AUX1"),
    ]
    session = TakeSession(plan(tmp_path, shared), FakeEngine())
    with pytest.raises(TransportError, match="cannot record riff-idea: .*audio_output_0"):
        session.start("riff-idea")
    assert list(tmp_path.iterdir()) == []


def test_an_engine_that_will_not_start_leaves_a_take_that_says_so(tmp_path):
    session = TakeSession(plan(tmp_path), FakeEngine(start_error=OSError("no such port")))
    with pytest.raises(TransportError, match="capture engine did not start: no such port"):
        session.start("riff-idea")
    problems = take.check_take(take.load_take(tmp_path / "riff-idea"))
    assert any("the capture engine did not start: no such port" in p for p in problems)
    # And the session is free for the next attempt.
    session.engine = FakeEngine()
    assert session.start("riff-idea").startswith("recording riff-idea — the earlier")
    session.stop()


def test_a_recorder_that_will_not_start_leaves_a_take_that_says_so(tmp_path):
    def broken(*_args, **_kwargs):
        raise OSError("read-only file system")

    session = TakeSession(plan(tmp_path), FakeEngine(), recorder_factory=broken)
    with pytest.raises(TransportError, match="recorder did not start: read-only"):
        session.start("riff-idea")
    problems = take.check_take(take.load_take(tmp_path / "riff-idea"))
    assert any("the recorder did not start: read-only file system" in p for p in problems)


def test_an_engine_that_will_not_stop_still_gives_a_finished_take(tmp_path):
    transport = Transport(TakeSession(plan(tmp_path), FakeEngine(stop_error=RuntimeError("stuck"))))
    transport.on_name("riff-idea")
    transport.on_record()
    said = transport.on_stop()
    assert said.startswith("recorded riff-idea: 0.5 s — 1 problem(s):")
    assert "did not stop cleanly: stuck" in said
    assert take.load_take(tmp_path / "riff-idea").complete


def test_lost_audio_is_on_the_panel_not_only_in_the_file(tmp_path):
    """A writer that falls behind is a take the player needs to hear about now."""

    class Stalled:
        def __init__(self, stems, sample_rate, quantum):
            from sushi_rig.recorder import Recorder

            # A ring of two quanta and a writer that never starts: everything
            # past the first two blocks is lost.
            self.inner = Recorder(stems, sample_rate, quantum, headroom_s=1e-9)
            self.push = self.inner.push

        def start(self):
            self.inner._writers = [
                signals.WavWriter(path, channels, self.inner.sample_rate)
                for path, channels in self.inner.stems
            ]

        def stop(self):
            return self.inner.stop()

    session = TakeSession(plan(tmp_path), FakeEngine(), recorder_factory=Stalled)
    session.start("riff-idea")
    said = session.stop()
    assert "lost audio" in said
