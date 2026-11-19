"""The agent's MIDI listener (#34, #93): the pedal bindings Sushi cannot serve.

A rig's `midi:` section compiles to Sushi's own `cc_mappings` for sweeps,
blends and a CC on a mute, which run in the engine with nothing from here.
Sushi 1.3.0 cannot do the rest -- `midi_dispatcher.cpp` has no CC-to-bypass,
and its Program Change reaches a plugin's program, not a rig -- so
`compile_midi` writes those into the compiled config under `_midi_agent`
(`rigfile.AGENT_BINDINGS`), and the agent serves them by opening the
controller's port itself:

- a note on a bypass: each press (note on) toggles the plugin's bypass;
- a CC on a bypass: a switch, on (the effect engaged) at 64 and above, off
  (bypassed) below, acting only when it crosses;
- a note on a mute: each press toggles the track's `mute` parameter;
- a momentary footswitch's CC (#191) on a bypass or a mute: each press (64 and
  above) toggles it, since the release that follows must not undo it;
- a Program Change: program n selects the nth rig in config/rigs/, sorted by
  file name, brought up by the agent's lifecycle;
- a footswitch whose job is `previous-rig` or `next-rig` (#191), from the
  machine file rather than the rig's map, so on every rig: a press steps one
  rig back or on through that same order, stopping at either end.

A rig whose map names one controller, on a machine with another that steps
rigs (the MPD218 and the FBV), is two ports: a `Plan` is a `Feed` per port.

## Shape

What a message means is decided here, purely, from the bytes on the wire and
the bindings (`decode`, `parse_bindings`, `dispatch`), and is tested for every
shape with no port and no Sushi. The effects are two small pieces around it:
`AlsaPort`, which opens the port, and `Listener`, a thread that reads it and
hands each message to the agent's event loop. What an action does to Sushi
is the agent's (`agent.py`, the MIDI section), through the same calls as the
HTTP routes.

## Channels count from 0 here

The agent's listener reads the ALSA sequencer, where a channel is 0 to 15, as
`aseqdump` prints it and as the status byte carries it. A binding's channel
is the machine file's number, compared with that directly. Whether Sushi
counts from 0 or 1 for its own `cc_mappings` is not settled (the rig
session's list); this listener does not depend on the answer.

## Why alsa-midi

The rig is Linux with PipeWire, whose MIDI is the ALSA sequencer, and the
machine file names a port as ALSA lists it (`MPD218:MPD218 Port A`, client
and port). alsa-midi talks to the sequencer directly, subscribes to that one
port by those names, and ships wheels for CPython 3.10 to 3.14 that need only
libasound at run time. mido's usual backend, python-rtmidi, had no wheel past
3.12 at 1.5.8 (checked on PyPI, 2026-11-02), so CI's 3.13 job and the rig
machine's 3.14 would have compiled it from source, and it names ALSA ports
with the client and port numbers appended, which change with the order
devices are plugged in.

## What is not verified

Nothing here has heard a real pedal. `AlsaPort` has not opened a port: there
is no sequencer in CI or in the container. The bytes libasound decodes for
each event are checked against `decode` (test_midi_alsa.py); the port name,
the channel the pedal really sends on, and whether the subscription receives
anything once PipeWire also holds the device, are for the rig session.
"""

from __future__ import annotations

import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional, Protocol, Sequence, Union

# A CC switch is on at this value and above, as MIDI's own switch controllers
# (sustain, CC 64) are: 0-63 off, 64-127 on.
SWITCH_ON = 64

# How long one read of the port waits before looking whether it has been told
# to stop. The bound on how long `down` waits for the listener to let go.
POLL_SECONDS = 0.25

# The agent's name in the ALSA sequencer, as `aseqdump -l` will list it.
CLIENT_NAME = "sushi-rig agent"

CHANNELS = 16
MIDI_MAX = 127

# The alsa-midi names `AlsaPort` uses; test_midi_alsa.py checks each exists in
# the installed version (`Owner.attribute`, or a module-level name).
ALSA_NAMES = (
    "SequencerClient",
    "SequencerClient.list_ports",
    "SequencerClient.create_port",
    "SequencerClient.event_input",
    "SequencerClient.close",
    "Port.connect_from",
    "PortInfo",
    "WRITE_PORT",
    "PortType.MIDI_GENERIC",
    "PortType.APPLICATION",
    "SYSTEM_ANNOUNCE",
    "MidiBytesEvent",
    "ClientExitEvent",
    "PortExitEvent",
    "ALSAError",
)


class MidiError(Exception):
    """Something the listener will not do, or could not, with the reason."""


class BindingError(MidiError):
    """`_midi_agent` in a shape this listener does not understand. Carries every problem."""

    def __init__(self, problems: list[str]):
        self.problems = problems
        super().__init__("; ".join(problems))


class PortError(MidiError):
    """The controller's port could not be opened, or stopped working."""


# --------------------------------------------------------------------------- #
# The wire
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class Message:
    """One channel message the listener reads. `channel` is 0 to 15, as on the
    wire; `number` is the note, the controller or the program; `value` the
    velocity or the controller's value (0 for a program)."""

    kind: str  # "note_on" | "note_off" | "cc" | "program"
    channel: int
    number: int
    value: int = 0

    def describe(self) -> str:
        if self.kind == "program":
            return f"program change {self.number} on channel {self.channel}"
        if self.kind == "cc":
            return f"cc {self.number} = {self.value} on channel {self.channel}"
        word = "on" if self.kind == "note_on" else "off"
        return f"note {word} {self.number} (velocity {self.value}) on channel {self.channel}"


def decode(data: bytes | Sequence[int]) -> Message | None:
    """The message in one complete MIDI message's bytes, or None for anything
    the listener never acts on.

    None covers aftertouch -- an MPD218 pad sends channel aftertouch for as
    long as it is held (aseqdump, 2026-11-01), which must not read as more
    presses -- and pitch bend, system messages, and anything malformed. A
    note on at velocity 0 is a note off, by the MIDI spec. Running status is
    not expected: `AlsaPort` reads through libasound's decoder with it turned
    off, so a message that starts with a data byte is malformed here.
    """
    data = bytes(data)
    if not data or data[0] < 0x80 or any(b > MIDI_MAX for b in data[1:]):
        return None
    status, channel = data[0] & 0xF0, data[0] & 0x0F
    if status == 0xC0:
        return Message("program", channel, data[1]) if len(data) >= 2 else None
    if len(data) < 3:
        return None
    if status == 0x90:
        return Message("note_on" if data[2] else "note_off", channel, data[1], data[2])
    if status == 0x80:
        return Message("note_off", channel, data[1], data[2])
    if status == 0xB0:
        return Message("cc", channel, data[1], data[2])
    return None  # 0xA0 and 0xD0 aftertouch, 0xE0 pitch bend, 0xF0 system


# --------------------------------------------------------------------------- #
# The bindings
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class Binding:
    """One control the agent serves. `channel` None is "all". `target` is
    what Sushi calls it -- the plugin for a bypass (`lead/drive` -> `drive`,
    as `compile_midi` names processors), the track for a mute -- and
    `written` the target as the rig file wrote it."""

    control: str
    kind: str  # "note" | "cc"
    number: int
    channel: int | None
    action: str  # "bypass" | "mute" | "step"
    target: str  # for a step, "previous" or "next"
    written: str
    # A CC's switch behaviour (#191): "momentary" toggles on each press;
    # "latching", and None (a binding written before the device model), follow
    # the value. A note is a press either way.
    switch: str | None = None

    def describe(self) -> str:
        on = "all channels" if self.channel is None else f"channel {self.channel}"
        how = f" ({self.switch})" if self.switch else ""
        return f"{self.control}: {self.kind} {self.number} on {on}{how}, {self.action} {self.written}"


@dataclass(frozen=True)
class Programs:
    """Program Change selects a rig. `port` is Sushi's MIDI input number, as
    the rig file gives it; the agent reads the controller directly and does
    not use it."""

    channel: int | None
    port: int = 0


@dataclass(frozen=True)
class Bindings:
    controls: tuple[Binding, ...] = ()
    programs: Programs | None = None


_CONTROL_KEYS = ("control", "note", "cc", "channel", "bypass", "mute", "switch")
SWITCH_BEHAVIOURS = ("momentary", "latching")  # machine.BEHAVIOURS, which this must not import
_PROGRAM_KEYS = ("programs", "port", "channel")


def _channel(value: Any, at: str, problems: list[str]) -> int | None:
    if value == "all":
        return None
    if type(value) is int and 0 <= value < CHANNELS:
        return value
    problems.append(f'{at}: channel must be 0 to 15 or "all" (got {value!r})')
    return None


def parse_bindings(raw: Any) -> Bindings:
    """The compiled config's `_midi_agent`, or `BindingError` naming every
    problem. Strict on purpose: these are written by `compile_midi`, so a shape
    it does not write means a config from some other version, and a listener
    guessing at it would act on pedals in ways nobody wrote down.
    """
    if not isinstance(raw, list):
        raise BindingError([f"_midi_agent must be a list (got {raw!r})"])
    problems: list[str] = []
    controls: list[Binding] = []
    programs: Programs | None = None
    # (kind, number) -> [(channel, control)]: a clash is the same message on
    # overlapping channels, and "all" (None) overlaps every channel, so note 36
    # on "all" and note 36 on channel 1 clash -- `dispatch` would only ever
    # serve the first.
    claimed: dict[tuple[str, int], list[tuple[int | None, str]]] = {}
    for index, entry in enumerate(raw):
        at = f"_midi_agent entry {index + 1}"
        if not isinstance(entry, dict):
            problems.append(f"{at} is {entry!r}, not a mapping")
            continue
        if "programs" in entry:
            problems += [f"{at}: unknown key {k!r}" for k in entry if k not in _PROGRAM_KEYS]
            if entry["programs"] != "rigs":
                problems.append(f'{at}: programs must be "rigs" (got {entry["programs"]!r})')
            if programs is not None:
                problems.append(f"{at}: Program Change is bound twice")
            programs = Programs(_channel(entry.get("channel", "all"), at, problems),
                                entry.get("port", 0))
            continue
        name = entry.get("control")
        at = f"{at} ({name})" if isinstance(name, str) else at
        problems += [f"{at}: unknown key {k!r}" for k in entry if k not in _CONTROL_KEYS]
        sends = [k for k in ("note", "cc") if k in entry]
        does = [k for k in ("bypass", "mute") if k in entry]
        if not isinstance(name, str) or not name:
            problems.append(f"{at}: control must name the control")
            continue
        if len(sends) != 1:
            problems.append(f"{at}: exactly one of note, cc")
            continue
        if len(does) != 1:
            problems.append(f"{at}: exactly one of bypass, mute")
            continue
        kind, action = sends[0], does[0]
        number, written = entry[kind], entry[action]
        if type(number) is not int or not 0 <= number <= MIDI_MAX:
            problems.append(f"{at}: {kind} must be 0 to 127 (got {number!r})")
            continue
        switch = entry.get("switch")
        if switch is not None and (switch not in SWITCH_BEHAVIOURS or kind != "cc"):
            problems.append(f"{at}: switch must be momentary or latching, on a cc (got {switch!r} on a {kind})")
            continue
        channel = _channel(entry.get("channel", "all"), at, problems)
        if action == "bypass":
            if not (isinstance(written, str) and written.count("/") == 1 and all(written.split("/"))):
                problems.append(f"{at}: bypass must be channel/plugin (got {written!r})")
                continue
            target = written.split("/")[1]
        else:
            if kind == "cc" and switch != "momentary":
                # compile_midi gives a latching CC on a mute to Sushi, which
                # does it natively; one here is a config this listener did not
                # come from. A momentary one toggles, which Sushi cannot.
                problems.append(f"{at}: a CC on a mute is Sushi's own cc_mappings, not the agent's, "
                                "unless it is a momentary switch")
                continue
            if not isinstance(written, str) or not written:
                problems.append(f"{at}: mute must be a channel name (got {written!r})")
                continue
            target = written
        clash = next((other for ch, other in claimed.get((kind, number), [])
                      if ch is None or channel is None or ch == channel), None)
        if clash is not None:
            problems.append(f"{at}: {clash!r} and {name!r} both send {kind} {number} on overlapping channels")
            continue
        claimed.setdefault((kind, number), []).append((channel, name))
        controls.append(Binding(name, kind, number, channel, action, target, written, switch))
    if problems:
        raise BindingError(problems)
    return Bindings(tuple(controls), programs)


def step_bindings(controller: dict[str, Any]) -> tuple[Binding, ...]:
    """The rig-stepping controls of `controller` (`MachineSpec.controller`'s
    description, `model` and all), as bindings: the device's own job, read
    from the machine file and not from any rig's map (#191)."""
    channel = controller.get("channel", "all")
    found = []
    for control, model in sorted((controller.get("model") or {}).items()):
        job = model.get("job")
        if not job:
            continue
        kind, number = controller["controls"][control]
        direction = "previous" if job == "previous-rig" else "next"
        found.append(Binding(control, kind, number, None if channel == "all" else channel, "step",
                             direction, job, model.get("behaviour") if kind == "cc" else None))
    return tuple(found)


# --------------------------------------------------------------------------- #
# What a message does
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class ToggleBypass:
    control: str
    processor: str

    def describe(self) -> str:
        return f"{self.control}: toggle the bypass of {self.processor}"


@dataclass(frozen=True)
class SetBypass:
    control: str
    processor: str
    bypassed: bool

    def describe(self) -> str:
        return f"{self.control}: {'bypass' if self.bypassed else 'engage'} {self.processor}"


@dataclass(frozen=True)
class ToggleMute:
    control: str
    track: str

    def describe(self) -> str:
        return f"{self.control}: toggle the mute of {self.track}"


@dataclass(frozen=True)
class SelectRig:
    program: int
    rig: str

    def describe(self) -> str:
        return f"program {self.program}: switch to the rig {self.rig}"


@dataclass(frozen=True)
class StepRig:
    """A press of a rig-stepping footswitch (#191), before the order is read:
    decided against config/rigs/ off the loop, by `step_to`."""

    control: str
    direction: str  # "previous" | "next"

    def describe(self) -> str:
        return f"{self.control}: step to the {self.direction} rig"


@dataclass(frozen=True)
class StepTo:
    """A step decided: rig `rig`, number `index` in the order."""

    control: str
    direction: str
    index: int
    rig: str

    def describe(self) -> str:
        return f"{self.control}: the {self.direction} rig, {self.rig}"


@dataclass(frozen=True)
class Refused:
    """A message that was bound and cannot be acted on, and why: said, not dropped."""

    control: str
    reason: str

    def describe(self) -> str:
        return f"{self.control}: refused, {self.reason}"


# What `dispatch` decides: one of these, or None for nothing to do.
Action = Union[ToggleBypass, SetBypass, ToggleMute, SelectRig, StepRig, StepTo, Refused]


def _on(channel: int | None, message: Message) -> bool:
    return channel is None or channel == message.channel


def dispatch(
    message: Message | None,
    bindings: Bindings,
    switches: dict[str, bool],
    rigs: Sequence[str] = (),
    current: str | None = None,
) -> tuple[Optional[Action], dict[str, bool]]:
    """What `message` does under `bindings`: `(action or None, switches)`.

    `switches` is each CC switch's last state (True for on, at 64 and above),
    which is what makes a CC act only when it crosses 64; the one returned is
    new, and the one given is not changed. The caller commits it only once
    the action went through (or Sushi was already there): a crossing whose
    action was dropped or failed is then played again by the next value on
    that side, instead of being believed and ignored. A switch never heard from is not
    known, so its first value is a transition. `rigs` is the program order
    (`program_order`) and `current` the rig that is running, which a Program
    Change for it leaves alone: a switch is seconds of silence, and the
    player who pressed the number of the rig already playing has asked for
    nothing.

    None is "nothing to do": an unbound message, a pad's release (a note off,
    or a note on at velocity 0), a switch that did not cross, a Program
    Change on a rig that does not switch by them. A Program Change past the
    last rig is `Refused`, because the player pressed something and should
    find out why nothing happened.

    A CC switch's press (#191) depends on its behaviour. Momentary: every
    value of 64 and above is a press and anything below a release, with no
    state kept, so a switch that sends one value per press (FBV Control's
    Single) works as one that sends press and release. Latching: each
    crossing of 64 is a press, since the switch alternates its value.
    A press of a bypass or a mute toggles; a press of a step is `StepRig`,
    for the agent to decide against the order. A latching switch on a bypass,
    and a CC bypass written before the device model, still follow the value.
    """
    if message is None:
        return None, switches
    if message.kind == "program":
        programs = bindings.programs
        if programs is None or not _on(programs.channel, message):
            return None, switches
        if not rigs:
            return Refused(f"program {message.number}", "config/rigs/ holds no rigs"), switches
        if message.number >= len(rigs):
            return Refused(
                f"program {message.number}",
                f"program {message.number} has no rig: config/rigs/ holds {len(rigs)}, "
                f"programs 0 to {len(rigs) - 1} (GET /rig lists them)",
            ), switches
        rig = rigs[message.number]
        return (None if rig == current else SelectRig(message.number, rig)), switches
    kind = "cc" if message.kind == "cc" else "note"
    for binding in bindings.controls:
        if binding.kind != kind or binding.number != message.number or not _on(binding.channel, message):
            continue
        if kind == "note":
            if message.kind != "note_on":
                return None, switches
            return _press(binding), switches
        on = message.value >= SWITCH_ON
        if binding.switch == "momentary":
            return (_press(binding) if on else None), switches
        if switches.get(binding.control) == on:
            return None, switches
        crossed = {**switches, binding.control: on}
        if binding.action == "step":
            return _press(binding), crossed
        return SetBypass(binding.control, binding.target, bypassed=not on), crossed
    return None, switches


def _press(binding: Binding) -> Action:
    """What one press of a switch does: toggle its bypass or its mute, or
    step rigs. The one place a press becomes an action, so a guard on presses
    (a double tap, #191's later work) has one place to go."""
    if binding.action == "step":
        return StepRig(binding.control, binding.target)
    if binding.action == "mute":
        return ToggleMute(binding.control, binding.target)
    return ToggleBypass(binding.control, binding.target)


def step_to(step: StepRig, rigs: Sequence[str], current: str | None) -> StepTo | Refused:
    """Where a step press goes, in the program order (`program_order`):
    the rig before or after `current`, or `Refused` at either end -- it does
    not wrap round, so a stray press on the last rig cannot jump to the first
    -- and for a running rig the order does not hold."""
    if current not in rigs:
        return Refused(step.control, f"the running rig {current or '(unnamed)'} is not one of config/rigs/, "
                                     "so it has no previous or next")
    index = rigs.index(current) + (-1 if step.direction == "previous" else 1)
    if index < 0:
        return Refused(step.control, f"{current} is the first rig in config/rigs/; there is none before it")
    if index >= len(rigs):
        return Refused(step.control, f"{current} is the last rig in config/rigs/; there is none after it")
    return StepTo(step.control, step.direction, index, rigs[index])


def next_bypass(previous: bool | None, want: bool | None) -> bool:
    """The bypass state to set: `want` for a switch, which says its own state;
    the opposite of `previous` for a toggle. A toggle where Sushi would not say
    the current state is refused rather than guessed at."""
    if want is not None:
        return want
    if previous is None:
        raise MidiError("Sushi would not say whether it is bypassed, so a toggle cannot know which way to go")
    return not previous


def next_mute(previous: float | None) -> float:
    """A track's `mute` toggled: it is a parameter, normalised 0 to 1, and is
    read as muted from half way, the same threshold Sushi's own CC mapping to
    it crosses."""
    if previous is None:
        raise MidiError("Sushi would not say whether the track is muted, so a toggle cannot know which way to go")
    return 0.0 if previous >= 0.5 else 1.0


# --------------------------------------------------------------------------- #
# The program order
# --------------------------------------------------------------------------- #

def program_order(root: Path) -> list[str]:
    """The rigs a Program Change counts through: config/rigs/*.yaml, sorted by
    file name, as names. Program 0 is the first (many pedals display it as 1).
    Read when a Program Change arrives and when `GET /rig` is asked, so the two
    agree as of now, not as of `up`."""
    folder = Path(root) / "config" / "rigs"
    if not folder.is_dir():
        return []
    return [p.stem for p in sorted(folder.glob("*.yaml"), key=lambda p: p.name) if p.is_file()]


def programs_table(rigs: Sequence[str]) -> list[dict[str, Any]]:
    """Which number is which rig, for a player to read."""
    return [{"program": n, "rig": rig} for n, rig in enumerate(rigs)]


# --------------------------------------------------------------------------- #
# What the running rig asks for, from its files
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class Off:
    """No listener, and not a fault: the running rig has nothing for the agent."""

    reason: str


@dataclass(frozen=True)
class Feed:
    """One port to listen to: the controller, its port as the machine file
    names it, and what its messages do."""

    controller: str
    port: str
    bindings: Bindings


@dataclass(frozen=True)
class Plan:
    """The listeners to start for rig `rig`: a `Feed` per port, the rig's
    own map's controller first. Built with one feed's `controller`, `port`
    and `bindings`, as before the device model, or with `feeds`."""

    rig: str
    controller: str = ""
    port: str = ""
    bindings: Bindings = Bindings()
    feeds: tuple[Feed, ...] = ()

    def __post_init__(self) -> None:
        if not self.feeds:
            object.__setattr__(self, "feeds", (Feed(self.controller, self.port, self.bindings),))
        first = self.feeds[0]
        object.__setattr__(self, "controller", first.controller)
        object.__setattr__(self, "port", first.port)
        object.__setattr__(self, "bindings", first.bindings)

    @property
    def programs(self) -> bool:
        return any(feed.bindings.programs is not None for feed in self.feeds)


def plan_for(described: dict[str, Any], root: Path) -> Plan | Off:
    """What the rig `described` (`describe_rig`'s answer) asks the listener to
    serve: `Off` when nothing, `MidiError` when its files cannot say.

    Two sources, merged by controller into one `Feed` a port:

    - the rig's map: the bindings are the compiled config's, as `up` wrote
      them. The port is not in them -- `compile_midi` resolves each control to
      a note or CC and keeps no port name -- so it comes from the controller
      the rig file's map names, as the machine file the run was compiled
      against describes it;
    - the machine's rig-stepping controls (#191), on every running rig, with
      or without a map, from that same machine file. A machine file that is
      not there has none; one that will not read or validate is a
      `MidiError`, since the pedals it describes would then be silently off.
    """
    import json

    from .machine import MachineError, MachineSpec, machine_path
    from .rigfile import AGENT_BINDINGS, RigFileError, load_rig

    if not described.get("running"):
        return Off("no rig is running")
    name = described.get("config_name") or "the running rig"
    try:
        config = json.loads(Path(described["config"]).read_text())
    except (KeyError, TypeError, OSError, ValueError) as exc:
        raise MidiError(f"could not read the running rig's compiled config ({described.get('config')}): {exc}") from exc
    raw = config.get(AGENT_BINDINGS) if isinstance(config, dict) else None
    machine_file = Path(described["machine"]) if described.get("machine") else machine_path(Path(root))
    source = described.get("rig") or ((config.get("_meta") or {}).get("source") if isinstance(config, dict) else None)
    rig_name = Path(source).stem if isinstance(source, str) and source else name

    machine: MachineSpec | None = None

    def load_machine() -> MachineSpec:
        nonlocal machine
        if machine is None:
            try:
                machine = MachineSpec.load(machine_file)
            except MachineError as exc:
                raise MidiError(f"the pedals need {machine_file.name}, which could not be read: {exc}") from exc
            problems = machine.validate()
            if problems:
                raise MidiError(f"{machine_file.name}: " + "; ".join(problems))
        return machine

    feeds: dict[str, tuple[str, Bindings]] = {}
    if raw:
        bindings = parse_bindings(raw)
        if not isinstance(source, str) or not source:
            raise MidiError(f"{name} carries MIDI bindings but names no rig file to say which controller")
        try:
            rig = load_rig(Path(root) / source)
        except (OSError, RigFileError) as exc:
            raise MidiError(f"could not read {source} for its controller: {exc}") from exc
        if rig.midi is None:
            raise MidiError(f"{source} has no midi: section now, though the running rig was compiled with one")
        try:
            controller = load_machine().controller_for(rig.midi.controller)
        except MachineError as exc:
            raise MidiError(str(exc)) from exc
        port = controller.get("port")
        if not port:
            raise MidiError(
                f"{machine_file.name} gives controller {rig.midi.controller!r} no port, so the agent "
                "cannot open it: add its ALSA name (`aseqdump -l`), e.g. port: \"MPD218:MPD218 Port A\""
            )
        feeds[rig.midi.controller] = (port, bindings)

    steppers = load_machine().steppers() if machine_file.is_file() else {}
    for controller_name, controller in steppers.items():
        steps = step_bindings(controller)
        port, bindings = feeds.get(controller_name, (controller["port"], Bindings()))
        feeds[controller_name] = (port, Bindings(bindings.controls + steps, bindings.programs))
    if not feeds:
        return Off(f"{name} has no MIDI bindings for the agent")
    return Plan(rig_name, feeds=tuple(Feed(c, port, b) for c, (port, b) in feeds.items()))


# --------------------------------------------------------------------------- #
# The port
# --------------------------------------------------------------------------- #

class Port(Protocol):
    """What the listener needs of a port. Used from one thread only: the
    listener's, which opens it, reads it and closes it."""

    def read(self, timeout: float) -> bytes | None:
        """One complete MIDI message, or None if none came within `timeout`.
        Raises `PortError` when the port can no longer be read."""

    def close(self) -> None: ...


def find_port(name: str, available: Sequence[tuple[str, Any]]) -> Any:
    """The address of the port called `name` among `(name, address)` pairs, or
    `PortError` saying what there is. Exact: the machine file's name is
    `client:port`, as `aseqdump -l` shows the two columns, and a near match
    would be a guess about which device is the pedal. Two ports of one name
    (two of the same controller plugged in) are refused for the same reason."""
    matches = [address for listed, address in available if listed == name]
    if len(matches) == 1:
        return matches[0]
    if matches:
        raise PortError(f"two ports are called {name!r}, so which is the pedal is not known: unplug one")
    if not available:
        raise PortError(f"no MIDI port called {name!r}: ALSA lists no MIDI ports at all. Is it plugged in?")
    listed = ", ".join(sorted(n for n, _ in available))
    raise PortError(f"no MIDI port called {name!r}; ALSA lists: {listed}. Is it plugged in?")


class AlsaPort:
    """The controller's port, read through the ALSA sequencer (alsa-midi).

    The agent registers a sequencer client of its own (`CLIENT_NAME`) with
    one input port, and subscribes it to the controller's port: ALSA delivers
    a port's events to every subscriber, so this takes nothing from Sushi's
    own MIDI input or anything else listening. It also subscribes to the
    system announcements, so a controller unplugged mid-set is a `PortError`
    rather than a listener that has silently stopped hearing anything.

    Events are read as bytes through libasound's decoder, with running status
    off (alsa-midi sets that), so each read is one whole message for `decode`.

    NOT VERIFIED against a sequencer: nothing here has run with /dev/snd/seq.
    """

    def __init__(self, name: str):
        try:
            import alsa_midi
        except (ImportError, OSError) as exc:
            raise PortError(f"alsa-midi is not installed, so the agent cannot read pedals: pip install -e tool[midi] ({exc})") from exc
        self._alsa = alsa_midi
        try:
            self._client = alsa_midi.SequencerClient(CLIENT_NAME)
        except alsa_midi.ALSAError as exc:
            raise PortError(f"cannot open the ALSA sequencer (/dev/snd/seq): {exc}") from exc
        try:
            listed = [(f"{p.client_name}:{p.name}", (p.client_id, p.port_id))
                      for p in self._client.list_ports(input=True)]
            self._source = find_port(name, listed)
            self._port = self._client.create_port(
                "pedals in", caps=alsa_midi.WRITE_PORT,
                type=alsa_midi.PortType.MIDI_GENERIC | alsa_midi.PortType.APPLICATION)
            self._port.connect_from(self._source)
            self._port.connect_from(alsa_midi.SYSTEM_ANNOUNCE)
        except alsa_midi.ALSAError as exc:
            self._client.close()
            raise PortError(f"cannot subscribe to {name!r}: {exc}") from exc
        except BaseException:
            self._client.close()
            raise

    def read(self, timeout: float) -> bytes | None:
        a = self._alsa
        try:
            event = self._client.event_input(prefer_bytes=True, timeout=timeout)
        except a.ALSAError as exc:
            raise PortError(f"reading the controller failed: {exc}") from exc
        if isinstance(event, a.MidiBytesEvent):
            return bytes(event.midi_bytes)
        client, port = self._source
        if isinstance(event, a.ClientExitEvent) and event.addr.client_id == client:
            raise PortError("the controller went away (its ALSA client exited): was it unplugged?")
        if isinstance(event, a.PortExitEvent) and tuple(event.addr) == (client, port):
            raise PortError("the controller's port went away: was it unplugged?")
        return None

    def close(self) -> None:
        self._client.close()


# --------------------------------------------------------------------------- #
# The thread that reads it
# --------------------------------------------------------------------------- #

class Listener:
    """Opens `port_name`, reads it, and hands each decoded message over, in a
    thread of its own, until told to stop.

    A thread because the sequencer read blocks, and the port is opened, read
    and closed in that one thread so that no ALSA handle is ever used from two.
    Every callback is handed over through `post` (the agent passes the loop's
    `call_soon_threadsafe`), so the agent's state is only touched on its loop.
    Messages `decode` drops, aftertouch above all, are counted (`heard`) and
    go no further, so a held pad does not flood the loop.

    A port that cannot be opened, or fails once open, is reported once
    (`on_failure(exc, opened)`) and the thread ends: it does not retry. A pedal
    that is not there now is not there in a quarter of a second either, and a
    loop of attempts is a hot thread on the machine running the audio; the
    next `up` or `restart` tries again.
    """

    def __init__(
        self,
        port_name: str,
        open_port: Callable[[str], Port],
        *,
        post: Callable[..., Any],
        on_open: Callable[[], None],
        on_message: Callable[[Message], None],
        on_failure: Callable[[BaseException, bool], None],
        poll: float = POLL_SECONDS,
    ) -> None:
        self.port_name = port_name
        self.open_port = open_port
        self.post = post
        self.on_open = on_open
        self.on_message = on_message
        self.on_failure = on_failure
        self.poll = poll
        self.heard = 0
        self._stop = threading.Event()
        self.thread = threading.Thread(target=self._run, name="midi-listener", daemon=True)

    def start(self) -> None:
        self.thread.start()

    def stop(self, timeout: float = 2.0) -> bool:
        """Ask the thread to stop and wait up to `timeout`; True if it has.
        Blocking: the agent calls it off its loop."""
        self._stop.set()
        if self.thread.is_alive():
            self.thread.join(timeout)
        return not self.thread.is_alive()

    def _hand(self, fn: Callable[..., Any], *args: Any) -> bool:
        try:
            self.post(fn, *args)
            return True
        except RuntimeError:  # the loop has closed: the agent is gone
            self._stop.set()
            return False

    def _run(self) -> None:
        try:
            port = self.open_port(self.port_name)
        except Exception as exc:  # noqa: BLE001 -- reported, never raised into a dead thread
            self._hand(self.on_failure, exc, False)
            return
        try:
            if self._stop.is_set():
                return
            self._hand(self.on_open)
            while not self._stop.is_set():
                data = port.read(self.poll)
                if data is None:
                    continue
                self.heard += 1
                message = decode(data)
                if message is not None and not self._stop.is_set():
                    self._hand(self.on_message, message)
        except Exception as exc:  # noqa: BLE001 -- reported, and the thread ends
            self._hand(self.on_failure, exc, True)
        finally:
            try:
                port.close()
            except Exception as exc:  # noqa: BLE001 -- said; there is nothing else to do with it
                print(f"sushi-rig agent: closing MIDI port {self.port_name!r} failed: {exc}",
                      file=sys.stderr, flush=True)


# --------------------------------------------------------------------------- #
# Everything the agent's wiring touches, injectable
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class World:
    """What the agent's MIDI wiring reaches outside itself, so its tests can
    stand each in: opening a port, what the running rig asks for, and the
    program order. A rig switch, whatever started it, reaches the state file
    and compiles the next rig through `agent.Switching`, the one seam for
    both (#93): this used to carry a second `read_state` and a `check_rig`
    of its own, which a switch no longer used."""

    open_port: Callable[[str], Port] = AlsaPort
    plan: Callable[[dict[str, Any], Path], "Plan | Off"] = plan_for
    rigs: Callable[[Path], list[str]] = program_order
