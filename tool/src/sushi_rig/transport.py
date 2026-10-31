"""Record and stop: what the panel's record bar drives.

Issue #57. The listener already takes a name and a save over OSC; this is the
same shape for a take. A name arrives as its own message, RECORD starts a take
under the last name received, STOP finishes it, and every outcome goes back to
the panel as a sentence. The two buttons carry no payload, for the reason
`listen.py`'s docstring gives for the save button.

## Two buttons, not a toggle

A toggle on the panel holds its own idea of whether a take is running, and
that idea can be wrong: a panel reloaded mid-take, a second panel open on a
phone, a take refused before it started. Then the button shows the opposite of
the truth and the next press does the opposite of what the player meant. Two
buttons that each say what they want, with the listener deciding, cannot
disagree with it. RECORD while recording and STOP while stopped are refused
with a message, not acted on.

## What is here, and what is not

Here: the decisions (`Transport`, which is pure and tested on its own) and the
effects (`TakeSession`, which composes `take.start_take`, `recorder.Recorder`
and a capture engine into one take, and finishes it with what the recorder
says it captured).

Not here: the engine — whatever reads the rig's ports and calls `push` — which
needs the rig machine (#53). Until one exists, the listener runs `NoEngine`,
which refuses every take with a sentence saying so. That is deliberate: a
record button that silently did nothing would be worse than none.

Also not here: which ports a take records, which follows from the machine file
and the rig and arrives with per-channel outputs, and measuring the stems'
offsets. Offsets are recorded as not measured, which `take.yaml` states in
words; measuring a dry tap against what came out of an amp is a hypothesis to
test on the rig, not a number to write in.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Protocol, Sequence

from .take import Stem, TakeError, check_name, check_take, finish_take, start_take

# The recorder writes float32 and nothing else; see signals.WavWriter.
SUBTYPE = "float32"

NOT_MEASURED = "not measured: the record button does not measure offsets yet"


class TransportError(Exception):
    """A take that could not be started or finished. The message is what the
    player reads on the panel, so it is a sentence, not a code."""


def auto_name(now: datetime) -> str:
    """`take-20261031-142501`: a take recorded without a name still gets one
    that sorts by time and passes `save.NAME_PATTERN`. Local time, because it
    is for the player to recognise; `take.yaml` records the moment in UTC."""
    return now.strftime("take-%Y%m%d-%H%M%S")


# --------------------------------------------------------------------------- #
# Effects
# --------------------------------------------------------------------------- #

class Session(Protocol):
    """One take at a time: `start` begins it, `stop` ends it.

    Both return the sentence the panel shows, and raise `TransportError` with
    one if they cannot do what was asked.
    """

    def start(self, name: str) -> str: ...

    def stop(self) -> str: ...


class NoEngine:
    """The session a listener runs until there is a capture engine."""

    MESSAGE = (
        "recording is not available on this machine yet: there is no capture "
        "engine (#53)"
    )

    def start(self, name: str) -> str:
        raise TransportError(self.MESSAGE)

    def stop(self) -> str:
        raise TransportError(self.MESSAGE)


class Engine(Protocol):
    """Whatever reads the rig's ports and feeds the recorder.

    `start` begins calling `push` from the audio thread with blocks whose
    columns are the stems' channels, in the order given. `stop` returns once
    it will call `push` no more.
    """

    def start(self, stems: Sequence[tuple[Stem, int]], push: Callable[[Any], int]) -> None: ...

    def stop(self) -> None: ...


@dataclass(frozen=True)
class TakePlan:
    """Everything about a take except its name.

    `stems` pairs each stem with its channel count, in the order the engine
    lays them out in a block. `config` is the config as it is live, amp
    included; see `take.start_take`.
    """

    takes_dir: Path
    config: dict[str, Any]
    config_source: str
    stems: tuple[tuple[Stem, int], ...]
    sample_rate: int
    quantum: int


class TakeSession:
    """A take, from RECORD to a finished folder.

    The order is the take module's two phases: the folder exists and says it
    is unfinished before the engine pushes a sample, so a rig killed mid-take
    leaves a take that describes itself.
    """

    def __init__(self, plan: TakePlan, engine: Engine, *,
                 recorder_factory: Callable[..., Any] | None = None,
                 clock: Callable[[], datetime] | None = None):
        if recorder_factory is None:
            from .recorder import Recorder as recorder_factory
        self.plan = plan
        self.engine = engine
        self._recorder_factory = recorder_factory
        self._clock = clock
        self._folder: Path | None = None
        self._recorder = None

    def start(self, name: str) -> str:
        if self._recorder is not None:
            raise TransportError(f"already recording {self._folder.name}")
        plan = self.plan
        try:
            paths = start_take(
                plan.takes_dir, name, plan.config, [stem for stem, _ in plan.stems],
                sample_rate=plan.sample_rate, subtype=SUBTYPE, quantum=plan.quantum,
                config_source=plan.config_source,
                now=self._clock() if self._clock else None,
            )
        except TakeError as exc:
            raise TransportError(f"cannot record {name}: {exc}") from exc
        folder = Path(plan.takes_dir) / name
        # From here the folder exists and says it is unfinished. A failure is
        # written into it as the take's own record rather than left a mystery;
        # a take of the same name it replaced is safe in the archive either way.
        try:
            recorder = self._recorder_factory(
                [(paths[stem.filename], channels) for stem, channels in plan.stems],
                plan.sample_rate, plan.quantum,
            )
            recorder.start()
        except Exception as exc:  # noqa: BLE001 — becomes the take's own record
            reason = f"the recorder did not start: {exc}"
            _finish(folder, _nothing_captured(reason))
            raise TransportError(f"cannot record {name}: {reason}") from exc
        try:
            self.engine.start(plan.stems, recorder.push)
        except Exception as exc:  # noqa: BLE001 — becomes the take's own record
            reason = f"the capture engine did not start: {exc}"
            summary = recorder.stop().summary()
            summary["error"] = summary["error"] or reason
            _finish(folder, summary)
            raise TransportError(f"cannot record {name}: {reason}") from exc
        self._folder, self._recorder = folder, recorder
        return f"recording {name}"

    def stop(self) -> str:
        if self._recorder is None:
            raise TransportError("not recording")
        folder, recorder = self._folder, self._recorder
        self._folder = self._recorder = None
        engine_error = ""
        try:
            self.engine.stop()
        except Exception as exc:  # noqa: BLE001 — the recorder still has to stop
            engine_error = f"the capture engine did not stop cleanly: {exc}"
        recording = recorder.stop()
        summary = recording.summary()
        if engine_error and not summary["error"]:
            summary["error"] = engine_error
        take = _finish(folder, summary)
        problems = check_take(take)
        said = f"recorded {folder.name}: {recording.seconds:.1f} s"
        if problems:
            return f"{said} — {len(problems)} problem(s): {problems[0]}"
        return said


def _nothing_captured(error: str) -> dict[str, Any]:
    """A recorder's summary for a take that never got as far as a recorder."""
    return {"frames": 0, "lost_frames": 0, "dropouts": [], "unrecorded_episodes": 0,
            "error": error}


def _finish(folder: Path, capture: dict[str, Any]):
    try:
        return finish_take(folder, {}, measured_by=NOT_MEASURED, capture=capture)
    except (TakeError, OSError) as exc:
        raise TransportError(
            f"the take in {folder} was recorded but could not be marked finished: "
            f"{exc}. Its stems are intact; `sushi-rig take check {folder}` says more"
        ) from exc


# --------------------------------------------------------------------------- #
# Decisions
# --------------------------------------------------------------------------- #

class Transport:
    """What RECORD, STOP and the name field mean. Never raises.

    Every method returns the sentence for the panel's record status. It is
    driven from the listener's one OSC thread, so it needs no locking.
    """

    def __init__(self, session: Session | None = None,
                 clock: Callable[[], datetime] = datetime.now):
        self.session = session if session is not None else NoEngine()
        self._clock = clock
        self.name = ""
        self.recording: str | None = None

    def on_name(self, name: str) -> None:
        self.name = name.strip()

    def on_record(self) -> str:
        if self.recording is not None:
            return f"already recording {self.recording} — press STOP first"
        name = self.name or auto_name(self._clock())
        problems = check_name(name)
        if problems:
            return f"record refused: {problems[0]}"
        try:
            said = self.session.start(name)
        except TransportError as exc:
            return f"record failed: {exc}"
        except Exception as exc:  # noqa: BLE001 — the listener must survive
            return f"record failed: unexpected error: {exc}"
        self.recording = name
        return said

    def on_stop(self) -> str:
        if self.recording is None:
            return "not recording"
        name, self.recording = self.recording, None
        # Idle afterwards whatever happens: a session that raised on stop has
        # stopped as far as it can, and staying "recording" would leave the
        # player unable to start another take.
        try:
            return self.session.stop()
        except TransportError as exc:
            return f"stop failed for {name}: {exc}"
        except Exception as exc:  # noqa: BLE001 — the listener must survive
            return f"stop failed for {name}: unexpected error: {exc}"

    def close(self) -> str | None:
        """Finish a running take as the listener shuts down, so Ctrl-C ends a
        take instead of abandoning it. `sushi-rig down` sends SIGINT first, so
        it does the same; only a listener killed outright leaves a take
        unfinished, and that take says so."""
        return self.on_stop() if self.recording is not None else None
