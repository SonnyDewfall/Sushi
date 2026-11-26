"""Record and stop: what the web UI's record bar drives.

Issue #57. A take is started under a name (with none, under the session's
default, `<rig>-<date>-take-<nn>`, #134) and finished by STOP, and every
outcome goes back as a sentence. It was first built for the Open Stage Control
panel's record bar, through the OSC listener; the panel and the listener are
retired (#139, #218), and the web UI records through the recorder.

## What is here, and what is not

Here: the effects (`TakeSession`, which composes `take.start_take`,
`recorder.Recorder` and a capture engine into one take, and finishes it with
what the recorder says it captured), and which ports a take records
(`rig_stems`, below), from which `record_session` builds a session.

The web UI records through another process: `sushi-rig recorder`
(recorder_process.py), a child of the rig, builds a session with
`record_session` at each RECORD the agent sends it, from the run's files as
they are then (a live switch, #93, rewrites the config under a running rig),
and runs `TakeSession` start and stop. Whether a take is running is the
recorder's to say: RECORD while recording and STOP while stopped are refused
with a message, not acted on.

The engine is `capture.CaptureEngine` (#53). When a run cannot have one the
session is `NoEngine`, which refuses every take with a sentence naming the
reason: a record button that silently did nothing would be worse than none.

## Which ports a take records

For a rig of channels (`rigfile.py`), in the rig's order, each channel gives
two stems, and the master one:

    guitar.wav       the channel's stem: its track's own output, stereo
    guitar-dry.wav   its dry tap: the machine file's port(s) for its input role
    vocal.wav, vocal-dry.wav
    master.wav       the master track's output: what reached the monitors

So guitar_vocal is eight columns: `sushi:audio_output_2`/`_3` and
`Audient iD14:capture_AUX0`, `_4`/`_5` and `capture_AUX1`, then `_0`/`_1`.

Why these. A channel's stem is the output the compiler gives every channel
(engine bus 1 + its index) and the patchbay links to nothing, so it is the
channel alone, after its chain and before its level, which is the send's gain:
neither stem holds the other, as #57 requires, and tapping it changes nothing
anyone hears. The dry tap is the physical input, not Sushi's input, because
with the amp running Sushi's input is the amp's output; the port is what went
into the rig, which is what re-amping needs. The master is kept because only
it has the mix and the master chain, the take as it was heard. The ports are
named the way everything else names them: `patchbay.track_route` and
`patchbay.sushi_ports` (shared with selftest's probes) for Sushi's, read off
the tracks of the config Sushi loaded rather than a fresh compile of the rig
file, so they are the ports running even if the file has changed since `up`;
and `MachineSpec.role_ports` (the patchbay's reading) for the inputs. A stem of
two ports must be two consecutively numbered ones, because that is all
`capture.numbered_ports` can link; anything else is refused, not guessed.

The plan names those ports as the machine file does, by PipeWire alias
(`Audient iD14:capture_AUX0`), and takes carry them so. It is not what a JACK
client links from: through `pw-jack` the same port is `Audient iD14
Multichannel:capture_AUX0`, and an engine that looked the alias up among JACK's
names alone refused every take on the rig (2026-11-02, the MANIFEST). The
plan names each port as a JACK client sees it, off the live graph
(`jack_port`, below), and the engine finds each by that name among the ports
its client lists at RECORD (`capture.match_sources`).

Channels that play from one input (a blend of two chains on one guitar) share
one dry tap, recorded once and named for the role, `guitar-in-dry.wav`: it is
one signal, and two stems of it would be one signal under two names. The rig
in the take says which channels to re-amp it through, and `take check`
accepts a dry stem named for one of that rig's inputs.

## One socket, three names

A port is named three ways, and a take must use the third. The machine file and
the saved patchbay name it by its `port.alias`
(`Audient iD14:capture_AUX0`), which is what qpwgraph shows; `pw-link -l` names
it `<node.name>:<port.name>`; and a JACK client through `pw-jack` -- which the
capture engine is -- names it `<node.description>:<port.name>`,
`Audient iD14 Multichannel:capture_AUX0`. For an ALSA device those are three
different strings, because the alias carries the node's *nick*, which every
profile of the device shares. For Sushi they are all `sushi`.

So the plan resolves every port through `jack_port` before it checks anything,
with the map `patchbay.jack_port_names` reads off a live `pw-dump`
(`_live_jack_names`): the one resolver of a take's ports (#217), and a strict
one. A port the graph does not have, or has under two nodes, and a graph that
cannot be read, refuse the plan. `take.yaml` then records the name the column
was really linked from, and the record bar's stem list shows it.

This is the defect the record button's first live run found, on 2026-11-06: the
machine file's alias is no port to the engine, so `capture.missing_sources`
refused every take over its dry tap, and three takes were written as headers
with nothing in them. It had been written down as unknown since 2026-11-02 --
"whether the machine file's port names (as qpwgraph shows them) are what a JACK
client sees through `pw-jack`". They are not.

A rig.yaml of tracks (v1) gets no plan. Its outputs are whatever its routing
says, and the routing that shipped summed every track into the monitor bus
(`take.py`, "No two stems from one port"), so there is no stem of one track to
tap. `sushi-rig up <name>` runs a rig of channels, which has them, and is
all it runs since the v1 configs were retired (#218).

The rate is the compiled config's `host_config.samplerate`, and it must be
`rig.RATE`: `up` runs PipeWire at that rate whatever the config says, so a
config at another is refused when the plan is made rather than by the engine
at every RECORD. The quantum is `rig.QUANTUM`, the one `up` gives Sushi and the
recorder. The config a take carries is the one Sushi loaded, read at RECORD,
with the amp as the agent last sent it (the session is built with that hook,
`amp.write_amp`, the rule SAVE uses). Parameters moved on Sushi
since `up` are not in it: a take does not capture Sushi's state the way SAVE
does. The rig file and the library's commit are read at each RECORD, since a
SAVE rewrites the one and a commit moves the other while the rig runs.

**NOT VERIFIED: no take has been recorded this way.** Nothing here has run
against a live PipeWire past the refusal above; the tests plan ports and stand
the engine in. Answered by the rig: the machine file's port names (written as
qpwgraph shows them) are *not* the names JACK clients see through `pw-jack`,
which is why they are matched by alias. Unknown until the rig machine says:
whether pipewire-jack's aliases carry the machine file's names (`pw-jack
jack_lsp -A`, the MANIFEST's rig check), whether eight columns keep up as the spike's
six did, and whether tapping an output the patchbay leaves unlinked reads its
signal. The first press of RECORD on a `--rig` run, and `sushi-rig take
check` on what it wrote, is the test.

Also not here: measuring the stems' offsets. They are recorded as not
measured, which `take.yaml` states in words; measuring a dry tap against what
came out of an amp is a hypothesis to test on the rig, not a number to write
in.
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence

from .take import (
    ARCHIVE_DIR,
    DRY,
    PROCESSED,
    Stem,
    TakeError,
    check_stems,
    check_take,
    default_takes_dir,
    finish_take,
    library_state,
    start_take,
)

# The recorder writes float32 and nothing else; see signals.WavWriter.
SUBTYPE = "float32"

NOT_MEASURED = "not measured: the record button does not measure offsets yet"


class TransportError(Exception):
    """A take that could not be started or finished. The message is what the
    player reads on the page, so it is a sentence, not a code."""


# --------------------------------------------------------------------------- #
# Effects
# --------------------------------------------------------------------------- #

class Session(Protocol):
    """One take at a time: `start` begins it, `stop` ends it.

    Both return the sentence the page shows, and raise `TransportError` with
    one if they cannot do what was asked. `default_name` is what a take
    started at `now` (local time) is called when the player gave no name, by
    the one rule for that (`take.default_name`, #134); it raises
    `TransportError` when there is nothing to name a take after.
    """

    def default_name(self, now: datetime) -> str: ...

    def start(self, name: str) -> str: ...

    def stop(self) -> str: ...


class NoEngine:
    """The session of a run that cannot record, saying why.

    `reason` is the sentence's second half, the thing to fix; without one the
    message is the generic `MESSAGE`.
    """

    MESSAGE = "recording is not available: no take was planned for this run"

    def __init__(self, reason: str | None = None):
        self.reason = reason
        self.message = f"recording is not available: {reason}" if reason else self.MESSAGE

    def default_name(self, now: datetime) -> str:
        # No run to record is no rig to name a take after: RECORD says why
        # recording is off, as it did when the name was the time.
        raise TransportError(self.message)

    def start(self, name: str) -> str:
        raise TransportError(self.message)

    def stop(self) -> str:
        raise TransportError(self.message)


class Engine(Protocol):
    """Whatever reads the rig's ports and feeds the recorder.

    `start` begins calling `push` from the audio thread with blocks whose
    columns are the stems' channels, in the order given. `stop` returns once
    it will call `push` no more, and may return the engine's own account of the
    take (xruns, blocks it could not copy; see `capture.BlockPump.account`) for
    `take.yaml` to keep beside the recorder's. An engine with nothing to add
    returns None.
    """

    def start(self, stems: Sequence[tuple[Stem, int]], push: Callable[[Any], int]) -> None: ...

    def stop(self) -> dict[str, Any] | None: ...


@dataclass(frozen=True)
class TakePlan:
    """Everything about a take except its name.

    `stems` pairs each stem with its channel count, in the order the engine
    lays them out in a block. `config` is the config as it is live, amp
    included; `rig_text` the v2 rig it was compiled from, if any, and
    `library` where the library was; see `take.start_take`.

    `rig_path` and `library_root`, when set, are read again at every RECORD
    and win over `rig_text` and `library`: a SAVE rewrites the rig file and a
    commit moves the library while the rig runs, and a take must say
    where they were when it was made, not when the plan was.
    """

    takes_dir: Path
    config: dict[str, Any]
    config_source: str
    stems: tuple[tuple[Stem, int], ...]
    sample_rate: int
    quantum: int
    rig_text: str | None = None
    library: dict[str, Any] | None = None
    rig_path: Path | None = None
    library_root: Path | None = None


class TakeSession:
    """A take, from RECORD to a finished folder.

    The order is the take module's two phases: the folder exists and says it
    is unfinished before the engine pushes a sample, so a rig killed mid-take
    leaves a take that describes itself.
    """

    def __init__(self, plan: TakePlan, engine: Engine, *,
                 recorder_factory: Callable[..., Any] | None = None,
                 clock: Callable[[], datetime] | None = None,
                 amend_config: Callable[[dict[str, Any]], dict[str, Any]] | None = None):
        if recorder_factory is None:
            from .recorder import Recorder as recorder_factory
        self.plan = plan
        self.engine = engine
        # Given a copy of `plan.config` at RECORD, returns the config the take
        # carries. `record_session` sets it to write in the amp as it was
        # last sent, which changes while the rig runs and the plan does not.
        self.amend_config = amend_config
        self._recorder_factory = recorder_factory
        self._clock = clock
        self._folder: Path | None = None
        self._recorder = None

    def default_name(self, now: datetime) -> str:
        """`<rig>-<date>-take-<nn>` for a take started at `now`
        (`take.next_name`): the rig is the run's rig file, by its file's
        name, which is what the agent calls the running rig too (the state
        file's `rig`), so a take is named alike by the recorder and the page.
        Counted from the takes folder as it is now, at each RECORD.

        A plan with no rig file has no rig to name a take after. No run of
        the recorder makes one (`plan_for_run` refuses a run
        without a rig file); said as a refusal, not papered over with
        another kind of name."""
        from .take import next_name

        plan = self.plan
        if plan.rig_path is None:
            raise TransportError(
                "this take has no name, and the run has no rig file to name it after: "
                "type a name for the take")
        return next_name(Path(plan.takes_dir), Path(plan.rig_path).stem, now)

    def start(self, name: str) -> str:
        if self._recorder is not None:
            raise TransportError(f"already recording {self._folder.name}")
        plan = self.plan
        replacing = (Path(plan.takes_dir) / name).exists()
        config = plan.config
        if self.amend_config is not None:
            try:
                config = self.amend_config(copy.deepcopy(plan.config))
            except Exception as exc:  # noqa: BLE001 — said as this take's refusal
                raise TransportError(
                    f"cannot record {name}: the config it would carry could not be "
                    f"made ({type(exc).__name__}: {exc})"
                ) from exc
        rig_text, library = plan.rig_text, plan.library
        try:
            # Read now, not when the plan was made: see `TakePlan`.
            if plan.rig_path is not None:
                rig_text = Path(plan.rig_path).read_text()
        except OSError as exc:
            raise TransportError(
                f"cannot record {name}: the rig file {plan.rig_path} could not be read: {exc}"
            ) from exc
        if plan.library_root is not None:
            library = library_state(plan.library_root)
        try:
            paths = start_take(
                plan.takes_dir, name, config, [stem for stem, _ in plan.stems],
                sample_rate=plan.sample_rate, subtype=SUBTYPE, quantum=plan.quantum,
                config_source=plan.config_source,
                now=self._clock() if self._clock else None,
                rig_text=rig_text, library=library,
            )
        except (TakeError, OSError) as exc:
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
            # It may have got part of the way — a thread running, some ports
            # connected — so it is told to stop before the recorder is.
            account = None
            try:
                account = self.engine.stop()
            except Exception:  # noqa: BLE001 — the start failure is the news
                pass
            summary = recorder.stop().summary()
            summary["error"] = summary["error"] or reason
            if isinstance(account, dict):
                summary["engine"] = account
            _finish(folder, summary)
            raise TransportError(f"cannot record {name}: {reason}") from exc
        self._folder, self._recorder = folder, recorder
        if replacing:
            # Said on the page, not only in the folder: a player pressing
            # RECORD again under the same name should know the last attempt
            # moved rather than wonder where it went.
            return f"recording {name} — the earlier {name} is in {ARCHIVE_DIR}/{name}/"
        return f"recording {name}"

    def stop(self) -> str:
        if self._recorder is None:
            raise TransportError("not recording")
        folder, recorder = self._folder, self._recorder
        self._folder = self._recorder = None
        engine_error = ""
        account = None
        try:
            account = self.engine.stop()
        except Exception as exc:  # noqa: BLE001 — the recorder still has to stop
            engine_error = f"the capture engine did not stop cleanly: {exc}"
        recording = recorder.stop()
        summary = recording.summary()
        if engine_error:
            # Not `error`, which says the capture failed and the take is not
            # complete (`take.capture_failure`): the recorder has every block
            # the engine pushed, and what is missing is the engine's account.
            summary["stop_error"] = engine_error
        if isinstance(account, dict):
            # Kept apart from the recorder's own numbers: they are different
            # witnesses, and an xrun the engine saw is not a frame the ring lost.
            summary["engine"] = account
        take = _finish(folder, summary)
        problems = check_take(take)
        said = f"recorded {folder.name}: {recording.seconds:.1f} s"
        if problems:
            return f"{said} — {len(problems)} problem(s): {problems[0]}"
        return said


def _nothing_captured(error: str) -> dict[str, Any]:
    """A recorder's summary for a take that never got as far as a recorder.

    No `silent_blocks`: `{}` there means watched and found clean, and nothing
    was watching."""
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
# Which ports a take records, and the session that records them
# --------------------------------------------------------------------------- #

class PlanError(Exception):
    """A take that cannot be planned for this run. Carries every problem, for
    `take.TakeError`'s reason; `NoEngine` shows them as its reason."""

    def __init__(self, problems: Sequence[str]):
        self.problems = list(problems)
        super().__init__("; ".join(self.problems))


class GraphUnreadable(PlanError):
    """The live graph could not be read (`pw-dump` missing, hung, failing, or
    unparseable), so no port could be named as JACK sees it. A plan refused
    for it says nothing of the rig's ports: doctor reports it as a check it
    could not make, not a fault (`doctor.record_check`)."""


def jack_port(port: str, jack_names: Mapping[str, Sequence[str]] | None) -> tuple[str, str | None]:
    """The name a JACK client connects `port` by, and why that cannot be said.
    A take's one resolver of its ports (#217).

    `jack_names` is `patchbay.jack_port_names(dump, "out")`: port alias -> the
    name(s) a JACK client sees it under. A machine file names a port by its
    alias, which is what qpwgraph shows and what the saved patchbay keys links
    by; the capture engine is a JACK client, and through `pw-jack` an ALSA
    device's port carries the node's *description* instead -- so the alias
    `Audient iD14:capture_AUX0` is no port at all to the engine, while
    `Audient iD14 Multichannel:capture_AUX0` is the same socket. That is the
    defect this exists to fix: the record button's first live run (2026-11-06)
    refused every take over its dry tap, and recorded three takes of headers.
    Sushi's own ports come back unchanged, since its description, nick and
    `node.name` are all `sushi`.

    Strict, and pure. A port the graph lists under one name is that name. One
    it lists under two (two live nodes answering to one alias: two profiles of
    a device, or two of the same interface) is refused, since linking a take's
    column from either would be a guess at which socket the player meant. One
    it does not list at all is refused too: the device is gone, or the machine
    file names it otherwise than qpwgraph does. It used to be passed on as
    written, for the capture engine to look for among JACK's aliases at the
    start; that was a second resolver, needed while the OSC listener planned
    its takes before Sushi had registered, which could disagree with this one
    and was never seen to work on the rig. The recorder plans each take at
    RECORD, with the rig up, so there is nothing left for it to do.

    `jack_names` None is no graph at all, for a plan made from the files alone
    (the tests of what a plan holds): every port as written.
    """
    if jack_names is None:
        return port, None
    # One entry per live node's port, never de-duplicated by name: two of the
    # same interface share a description, so both give one JACK name, and
    # counting names would take two sockets for one.
    names = list(jack_names.get(port) or [])
    if len(names) == 1:
        return names[0], None
    if names:
        return port, (
            f"{port} is a live port of {len(names)} nodes at once "
            f"({', '.join(names)}), so which of them a take records cannot be told; "
            f"the alias names the socket and not the profile, so leave one profile "
            f"of the device live, or name the node in the machine file"
        )
    return port, (
        f"{port} is no capture port of the live graph: no node has a port called or aliased "
        f"{port}, so a take cannot be linked from it; is the device connected, and does the "
        f"machine file name the port as qpwgraph shows it?"
    )


def _consecutive(ports: Sequence[str]) -> str | None:
    """Why `ports` cannot be one stem, or None. The capture engine links a stem
    of n channels from the n consecutively numbered ports starting at its
    first (`capture.numbered_ports`), so any other set would be linked wrong
    and silently."""
    from .capture import numbered_ports

    expected = numbered_ports(ports[0], len(ports))
    if isinstance(expected, str):
        return expected
    if list(ports) != expected:
        return (
            f"its ports {', '.join(ports)} are not consecutively numbered, and a "
            f"stem of {len(ports)} channels is captured from {', '.join(expected)}"
        )
    return None


def rig_stems(rig: Any, machine: Any, config: dict[str, Any],
              jack_names: Mapping[str, Sequence[str]] | None = None) -> tuple[tuple[Stem, int], ...]:
    """The stems a take of `rig` records on `machine`, each with its channel
    count, in the order a block lays them out. See the module docstring for the
    choice and why.

    `rig` is a `rigfile.Rig`, for its channels and the input each plays from;
    `machine` a `MachineSpec` that has passed `validate`, for those inputs'
    ports; `config` the config Sushi loaded, for where each track's output is.
    The outputs come from the config and not from compiling `rig` again, so
    the ports are the ones running even if the rig file has changed since
    `up`. Pure. Raises `PlanError` with every problem: a plan with one stem
    wrong is not recorded with the others.

    `jack_names` resolves every port to the name the capture engine connects
    it by (`jack_port`), before the checks below, so that each one -- the
    consecutive numbering, two stems on one port -- is made on the names that
    will actually be linked and not on the aliases they were written as.
    With None the ports stay as the machine file and the config name them, a
    plan from the files alone; `plan_for_run` always gives the live graph's.

    Channels that share an input role (a blend of two chains on one guitar)
    share one dry tap, recorded once and named for the role, since it is one
    signal: `guitar-in-dry.wav`. A role only one channel plays from keeps the
    channel's name, `guitar-dry.wav`.
    """
    from types import SimpleNamespace

    from .capture import CaptureError, plan_columns
    from .patchbay import SUSHI_NODE, sushi_ports, track_route
    from .rigfile import MASTER

    tracks = {
        track.get("name"): SimpleNamespace(name=track.get("name"), outputs=track.get("outputs") or [])
        for track in (config.get("tracks") or []) if isinstance(track, dict)
    }
    roles = machine.role_ports()
    problems: list[str] = []
    stems: list[tuple[Stem, int]] = []

    def linked(ports: Sequence[str]) -> list[str]:
        """`ports` as the capture engine will name them, gathering any reason
        one of them cannot be named."""
        resolved = []
        for port in ports:
            name, why = jack_port(port, jack_names)
            if why:
                problems.append(why)
            resolved.append(name)
        return resolved

    def output(track_name: str) -> None:
        track = tracks.get(track_name)
        if track is None:
            problems.append(
                f"the config Sushi loaded has no track {track_name!r}, which the rig "
                f"file has: the rig file has changed since `up`, so restart the rig"
            )
            return
        route, broken = track_route(track, "outputs")
        ports = sushi_ports(SUSHI_NODE, "output", route)
        if broken or not ports:
            problems.extend(broken or [f"track {track_name!r} has no output on Sushi to record"])
            return
        ports = linked(ports)
        why = _consecutive(ports)
        if why:
            problems.append(f"{track_name}'s output cannot be recorded: {why}")
            return
        stems.append((Stem(track_name, PROCESSED, ports[0]), len(ports)))

    sharing: dict[str, list[Any]] = {}
    for channel in rig.channels:
        sharing.setdefault(channel.input, []).append(channel)

    for channel in rig.channels:
        output(channel.name)
        players = sharing[channel.input]
        if channel is not players[0]:
            continue  # its dry tap was planned with the first channel on the role
        label = channel.name if len(players) == 1 else channel.input
        ports = roles.get(channel.input)
        if not ports:
            who = " and ".join(repr(c.name) for c in players)
            problems.append(
                f"channel {who} plays from {channel.input!r}, which the machine file "
                f"gives no ports, so it has no dry tap"
            )
            continue
        wrong = [c for c in players if len(ports) != c.inputs_used]
        for c in wrong:
            problems.append(
                f"channel {c.name!r} is {c.width} but {c.input!r} "
                f"has {len(ports)} port(s), so its dry tap is not one input"
            )
        if wrong:
            continue
        ports = linked(ports)
        why = _consecutive(ports)
        if why:
            problems.append(f"{label}'s dry tap cannot be recorded: {why}")
            continue
        stems.append((Stem(label, DRY, ports[0]), len(ports)))
    output(MASTER)

    if not problems:
        problems += check_stems([stem for stem, _ in stems])
    if not problems:
        try:
            plan_columns(stems)
        except CaptureError as exc:
            problems += exc.problems
    if problems:
        raise PlanError(problems)
    return tuple(stems)


def _capture_engine(sample_rate: int, quantum: int) -> Engine:
    """The real engine, or `CaptureUnavailable` if JACK-Client cannot load.

    Loaded here, when a take is planned, so a machine without it says so in
    the refusal. `CaptureEngine.prepare` is not called: the recorder starts
    before Sushi, so the graph's quantum need not yet be the one Sushi will
    hold, and a read now could be wrong for every take. The first
    `start` reads it on its own client instead, half a second paid once.
    """
    from .capture import CaptureEngine, load_jack

    load_jack()
    return CaptureEngine(sample_rate, quantum)


def _live_jack_names() -> dict[str, list[str]]:
    """Port alias -> the name a JACK client sees it under, for every live
    capture port, read off `pw-dump`; `GraphUnreadable`, a `PlanError`, when
    the graph cannot be read.

    Refused rather than planned with no map, since #217: with no map the
    ports stayed as the machine file writes them, and only the capture
    engine's match by JACK alias could still have found them, a second
    resolver the rig never showed working. A take that cannot be named as
    JACK sees it is not started.

    Capture ports only (`"out"`, the direction from the node's side): every
    source a take records is something that produces audio, and the filter is
    what keeps `Audient iD14:playback_FL` from answering for a dry tap.
    """
    from .patchbay import PatchbayError, jack_port_names, read_pw_dump

    try:
        return jack_port_names(read_pw_dump(), "out")
    except Exception as exc:  # noqa: BLE001 -- said as the plan's refusal
        kind = "" if isinstance(exc, PatchbayError) else f"{type(exc).__name__}: "
        raise GraphUnreadable([f"the live graph could not be read to name the take's ports as JACK sees them "
                         f"({kind}{exc})"]) from exc


def plan_for_run(rig_path: Path | None, config_path: Path | None, *,
                 machine_file: Path | None = None, takes_dir: Path | None = None,
                 root: Path | None = None, quantum: int | None = None) -> TakePlan:
    """The plan for every take of this run, or `PlanError` saying why there is
    none.

    `rig_path` is the run's rig file; `config_path` the config Sushi loaded
    (for a `--rig` run, the one `prepare_rig` compiled);
    `machine_file` what that was compiled against, this host's file by default.

    Reads the live graph once (`_live_jack_names`) to name the ports the way
    the capture engine does. Called at every RECORD, so a device that came
    back under another profile is resolved afresh for the next take rather
    than held from an earlier plan.
    """
    from .machine import MachineError, MachineSpec, machine_path
    from .paths import checkout_root
    from .rigfile import Rig, RigFileError, load_any

    if rig_path is None:
        raise PlanError([
            "this run has no rig file, so there is nothing to say which ports a "
            "take records"
        ])
    try:
        rig = load_any(rig_path)
    except RigFileError as exc:
        raise PlanError([f"{rig_path}: {problem}" for problem in exc.problems]) from exc
    except Exception as exc:  # noqa: BLE001 — unreadable, not YAML, not a rig
        raise PlanError([f"{rig_path} could not be read: {exc}"]) from exc
    if not isinstance(rig, Rig):
        raise PlanError([
            f"{rig_path} is a rig.yaml of tracks, and takes are planned only for a "
            f"rig of channels, where each channel has an output of its own to record: "
            f"run a rig of config/rigs/ with `sushi-rig up <name>`"
        ])
    if config_path is None:
        raise PlanError([
            "this run names no config Sushi loaded, which every take carries"
        ])
    try:
        config = json.loads(Path(config_path).read_text())
    except (OSError, ValueError) as exc:
        raise PlanError([f"the config Sushi loaded, {config_path}, could not be read: {exc}"]) from exc
    rate = (config.get("host_config") or {}).get("samplerate") if isinstance(config, dict) else None
    if type(rate) is float and rate.is_integer():
        rate = int(rate)
    if type(rate) is not int or rate <= 0:
        raise PlanError([f"{config_path} states no sample rate (host_config.samplerate is {rate!r})"])
    from .rig import RATE

    if rate != RATE:
        # `up` runs PipeWire at RATE whatever the config says, so the capture
        # engine would refuse every take at RECORD; said now instead.
        raise PlanError([
            f"{config_path} runs at {rate} Hz, but the rig runs PipeWire at {RATE} Hz, "
            f"so a take could not be captured at the rate it would declare"
        ])

    root = root or checkout_root()
    machine_file = machine_file or machine_path(root)
    try:
        machine = MachineSpec.load(machine_file)
    except MachineError as exc:
        raise PlanError([f"the machine file names the dry taps, and {exc}"]) from exc
    problems = machine.validate()
    if problems:
        raise PlanError([f"{Path(machine_file).name}: {p}" for p in problems])
    stems = rig_stems(rig, machine, config, _live_jack_names())

    if quantum is None:
        from .rig import QUANTUM as quantum
    meta = config.get("_meta") if isinstance(config.get("_meta"), dict) else {}
    source = meta.get("source")
    return TakePlan(
        takes_dir=Path(takes_dir) if takes_dir else default_takes_dir(),
        config=config,
        config_source=source if isinstance(source, str) and source else str(config_path),
        stems=stems,
        sample_rate=rate,
        quantum=quantum,
        # Both read again at every RECORD: a SAVE rewrites the rig file and a
        # commit moves the library while the rig runs (`TakePlan`).
        rig_path=Path(rig_path),
        library_root=root,
    )


def record_session(rig_path: Path | None, config_path: Path | None, *,
                   machine_file: Path | None = None, takes_dir: Path | None = None,
                   root: Path | None = None, quantum: int | None = None,
                   amp_model: str | None = None,
                   amp_parameters: dict[str, float] | None = None,
                   engine: Callable[[int, int], Engine] | None = None) -> Session:
    """The session for a take of this run: a `TakeSession` when this run can
    be recorded, otherwise a `NoEngine` whose message says exactly why not.

    The take's config carries the amp by SAVE's rule (`amp.write_amp`): the
    model this run has, `amp_model`, and `amp_parameters`, the values last
    sent, read when the take starts.

    Never raises: the recorder answers every RECORD, and a refusal is a
    sentence, not a crash. `engine(sample_rate,
    quantum)` makes the engine, `capture.CaptureEngine` by default, and raises
    `capture.CaptureUnavailable` when JACK-Client is not there; the tests stand
    it in.
    """
    from .capture import CaptureError

    try:
        plan = plan_for_run(rig_path, config_path, machine_file=machine_file,
                            takes_dir=takes_dir, root=root, quantum=quantum)
    except PlanError as exc:
        return NoEngine(f"no take can be planned for this run: {exc}")
    except Exception as exc:  # noqa: BLE001 — see the docstring
        return NoEngine(f"no take can be planned for this run: unexpected error: {exc}")
    try:
        made = (engine or _capture_engine)(plan.sample_rate, plan.quantum)
    except CaptureError as exc:
        return NoEngine(str(exc))
    except Exception as exc:  # noqa: BLE001 — see the docstring
        return NoEngine(f"the capture engine could not be made: {exc}")
    from .amp import write_amp

    amp = amp_parameters if amp_parameters is not None else {}
    return TakeSession(plan, made,
                       amend_config=lambda config: write_amp(config, amp_model, amp))
