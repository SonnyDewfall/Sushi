"""Record and stop (#57): a whole take, from RECORD to STOP.

The take is real — a folder, a recorder, stems on disk — and only the engine is
fake: a thread pushing synthetic blocks, as the rig's capture engine will push
PipeWire's. What this cannot say is anything about PipeWire; that is the
engine's question, and the rig machine's.

Its own module because it needs numpy, and `importorskip` mid-module skips
everything in the file: the transport's decisions are in test_transport.py and
run on a machine with no numpy.
"""

from __future__ import annotations

import json
import shutil
import threading
from datetime import datetime

import pytest

np = pytest.importorskip("numpy")

from sushi_rig import signals, take  # noqa: E402
from rig_fixtures import frozen, frozen_v1  # noqa: E402
from sushi_rig.take import DRY, PROCESSED, Stem  # noqa: E402
from sushi_rig.transport import (  # noqa: E402
    NOT_MEASURED,
    TakePlan,
    TakeSession,
    Transport,
    TransportError,
)

SR = 48000
Q = 256
NOW = datetime(2026, 10, 31, 14, 25, 1)

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
        config=json.loads(frozen_v1("guitar_vocal").read_text()),
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


def test_the_engines_account_is_kept_in_the_take_beside_the_recorders(tmp_path):
    """The engine saw things the recorder cannot: it is a different witness, so
    its numbers sit under their own key."""

    class Reporting(FakeEngine):
        def stop(self):
            super().stop()
            return {"xruns": 2, "skipped_frames": 0, "skipped_blocks": 0, "quantum_changes": 0, "error": ""}

    transport = Transport(TakeSession(plan(tmp_path), Reporting()))
    transport.on_name("riff-idea")
    transport.on_record()
    said = transport.on_stop()
    assert said.startswith("recorded riff-idea: 0.5 s — 1 problem(s): the audio server reported 2 xrun(s)")
    kept = take.load_take(tmp_path / "riff-idea").metadata["capture"]
    assert kept["engine"]["xruns"] == 2
    assert kept["lost_frames"] == 0, "an xrun is not a lost frame; the recorder's count is its own"


def test_an_engine_that_failed_to_start_still_has_its_account_in_the_take(tmp_path):
    class Refused(FakeEngine):
        def start(self, stems, push):
            raise OSError("no such port")

        def stop(self):
            return {"xruns": 0, "error": "", "blocks": 0}

    session = TakeSession(plan(tmp_path), Refused())
    with pytest.raises(TransportError, match="no such port"):
        session.start("riff-idea")
    kept = take.load_take(tmp_path / "riff-idea").metadata["capture"]
    assert kept["engine"] == {"xruns": 0, "error": "", "blocks": 0}
    assert kept["silent_blocks"] == {}, "the recorder ran, so the watch did: watched, nothing seen"


def test_a_take_that_never_got_a_recorder_was_never_watched_so_has_no_report(tmp_path):
    """`silent_blocks: {}` means watched and found clean. A take with no
    recorder was not watched, and must not say it was."""
    def broken(*_args, **_kwargs):
        raise OSError("read-only file system")

    session = TakeSession(plan(tmp_path), FakeEngine(), recorder_factory=broken)
    with pytest.raises(TransportError):
        session.start("riff-idea")
    assert "silent_blocks" not in take.load_take(tmp_path / "riff-idea").metadata["capture"]


def test_an_engine_with_nothing_to_add_leaves_no_engine_key(tmp_path):
    session = TakeSession(plan(tmp_path), FakeEngine())
    session.start("riff-idea")
    session.stop()
    assert "engine" not in take.load_take(tmp_path / "riff-idea").metadata["capture"]


def test_a_column_that_goes_silent_mid_take_is_on_the_panel_and_in_the_file(tmp_path):
    """The whole chain for #81: audio with a block of zeros on the second dry
    tap, through the engine, the real recorder and the real take. Every counter
    the recorder keeps reads zero; the take names the port anyway."""
    audio = source(0.5)
    audio[10 * Q:11 * Q, 5] = 0.0  # the vocal's dry tap, one block
    transport = Transport(TakeSession(plan(tmp_path), FakeEngine(audio)))
    transport.on_name("riff-idea")
    transport.on_record()
    said = transport.on_stop()
    assert "vocal-dry.wav (captured from Audient iD14:capture_AUX1) went silent 1 time(s)" in said
    kept = take.load_take(tmp_path / "riff-idea").metadata["capture"]
    assert kept["lost_frames"] == 0 and kept["dropouts"] == [] and kept["error"] == ""
    assert kept["silent_blocks"] == {"vocal-dry.wav": {"count": 1, "first_at_seconds": round(10 * Q / SR, 3)}}


def test_a_take_from_a_v2_rig_carries_the_rig_and_the_library_through_the_transport(tmp_path):
    """The plan carries them; the session hands them on; the folder has them."""
    import dataclasses

    rig_text = frozen("guitar_vocal").read_text()
    library = {"commit": "0123456789abcdef0123456789abcdef01234567", "dirty": True}
    with_rig = dataclasses.replace(plan(tmp_path), rig_text=rig_text, library=library)
    transport = Transport(TakeSession(with_rig, FakeEngine()))
    transport.on_name("riff-idea")
    transport.on_record()
    transport.on_stop()
    finished = take.load_take(tmp_path / "riff-idea")
    assert (finished.folder / take.RIG_FILE).read_text() == rig_text
    assert finished.metadata["rig"] == take.RIG_FILE
    assert finished.metadata["library"] == library
    assert take.check_take(finished) == []


def test_takes_recorded_without_a_name_are_numbered_for_the_rig_and_the_day(tmp_path):
    """#134 through the real session, recorder and folders: RECORD with
    nothing typed is `<rig file's name>-<date>-take-<nn>`, counted from the
    takes folder at each RECORD, the day by the listener's (local) clock."""
    import dataclasses

    clock = [NOW]
    with_rig = dataclasses.replace(plan(tmp_path), rig_path=frozen("guitar_vocal"))
    session = TakeSession(with_rig, FakeEngine())
    transport = Transport(session, clock=lambda: clock[0])
    assert transport.on_record() == "recording guitar_vocal-2026-10-31-take-01"
    assert transport.on_stop() == "recorded guitar_vocal-2026-10-31-take-01: 0.5 s"
    session.engine = FakeEngine()
    assert transport.on_record() == "recording guitar_vocal-2026-10-31-take-02"
    transport.on_stop()
    assert take.check_take(take.load_take(tmp_path / "guitar_vocal-2026-10-31-take-02")) == []
    # A take recorded over is archived, and one archived keeps its number.
    session.engine = FakeEngine()
    transport.on_name("guitar_vocal-2026-10-31-take-02")
    assert "the earlier guitar_vocal-2026-10-31-take-02 is in _archive/" in transport.on_record()
    transport.on_stop()
    shutil.rmtree(tmp_path / "guitar_vocal-2026-10-31-take-02")
    session.engine = FakeEngine()
    transport.on_name("")
    assert transport.on_record() == "recording guitar_vocal-2026-10-31-take-03"
    transport.on_stop()
    clock[0] = datetime(2026, 11, 1, 0, 0, 1)
    session.engine = FakeEngine()
    assert transport.on_record() == "recording guitar_vocal-2026-11-01-take-01"
    transport.on_stop()
    assert take.take_names(tmp_path) == [
        "guitar_vocal-2026-10-31-take-01", "guitar_vocal-2026-10-31-take-03", "guitar_vocal-2026-11-01-take-01"]


def test_a_run_with_no_rig_file_has_no_default_name_and_says_to_type_one(tmp_path):
    """No rig, no default: refused in words, and nothing is written. No run
    of the listener makes such a plan (`plan_for_run` refuses first)."""
    transport = Transport(TakeSession(plan(tmp_path), FakeEngine()), clock=lambda: NOW)
    assert transport.on_record() == (
        "record failed: this take has no name, and the run has no rig file to name it after: "
        "type a name for the take")
    assert transport.recording is None and list(tmp_path.iterdir()) == []
    transport.on_name("riff-idea")
    assert transport.on_record() == "recording riff-idea"
    transport.on_stop()


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


# -- a take whose capture failed is not complete (the rig, 2026-11-02) --------------------

def test_a_take_whose_engine_did_not_start_is_finished_but_not_complete(tmp_path):
    """On the rig, ui-take-1's take.yaml said `complete: true` beside `frames:
    0` and an engine that never started. Anything filtering takes on the flag
    would have kept it as a good one."""
    session = TakeSession(plan(tmp_path), FakeEngine(start_error=OSError("no port called 'x:y'")))
    with pytest.raises(TransportError):
        session.start("ui-take-1")
    failed = take.load_take(tmp_path / "ui-take-1")
    assert failed.metadata["complete"] is False and failed.failed.endswith("no port called 'x:y'")
    problems = take.check_take(failed)
    assert problems[0] == "nothing was recorded: the capture engine did not start: no port called 'x:y' — the stems hold no audio"
    assert not any("never finished" in p for p in problems), "it was finished: it failed, it was not cut off"
    assert failed.metadata["alignment"]["note"].startswith("This take failed, so no offsets were measured")
    assert take.take_summary(tmp_path / "ui-take-1")["complete"] is False


def test_a_take_written_complete_beside_a_failure_is_flagged(tmp_path):
    """The writer before this fix: the rig's ~/sushi-rig-takes/ui-take-1, as its
    take.yaml was shown in the results (the error shortened)."""
    session = TakeSession(plan(tmp_path), FakeEngine(start_error=OSError("no port")))
    with pytest.raises(TransportError):
        session.start("ui-take-1")
    path = tmp_path / "ui-take-1" / take.TAKE_FILE
    import yaml

    meta = yaml.safe_load(path.read_text())
    meta["complete"] = True
    path.write_text(yaml.safe_dump(meta, sort_keys=False))
    old = take.load_take(tmp_path / "ui-take-1")
    problems = take.check_take(old)
    assert any(p.startswith("take.yaml says complete: true beside a capture that failed") for p in problems)
    assert any(p.startswith("nothing was recorded: the capture engine did not start") for p in problems)
    assert take.take_summary(tmp_path / "ui-take-1")["complete"] is False, "a list does not call it whole"


def test_an_engine_that_would_not_stop_leaves_a_whole_take_that_says_so(tmp_path):
    session = TakeSession(plan(tmp_path), FakeEngine(stop_error=RuntimeError("stuck")))
    session.start("riff-idea")
    session.stop()
    finished = take.load_take(tmp_path / "riff-idea")
    assert finished.complete and finished.failed is None
    assert finished.metadata["capture"]["error"] == ""
    assert any(p.startswith("the capture engine did not stop cleanly: stuck — the stems hold every block")
               for p in take.check_take(finished))


def test_an_old_take_whose_engine_would_not_stop_is_still_whole(tmp_path):
    """Before 2026-11-02 the stop failure was written into `capture.error`,
    beside a take whose stems are whole. Read as a failure, every such take
    would be called incomplete and flagged as a contradiction."""
    import yaml

    session = TakeSession(plan(tmp_path), FakeEngine(stop_error=RuntimeError("stuck")))
    session.start("riff-idea")
    session.stop()
    path = tmp_path / "riff-idea" / take.TAKE_FILE
    meta = yaml.safe_load(path.read_text())
    capture = meta["capture"]
    capture["error"] = capture.pop("stop_error")  # as the old writer put it
    assert capture["error"] == "the capture engine did not stop cleanly: stuck"
    path.write_text(yaml.safe_dump(meta, sort_keys=False))
    old = take.load_take(tmp_path / "riff-idea")
    assert old.complete and old.failed is None
    problems = take.check_take(old)
    assert any(p.startswith("the capture engine did not stop cleanly: stuck — the stems hold every block")
               for p in problems)
    assert not any("complete: true beside a capture that failed" in p or "stopped early" in p for p in problems)
    assert take.take_summary(tmp_path / "riff-idea")["complete"] is True
