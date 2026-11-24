"""The agent's MIDI listener (#34, #93): the pedal bindings Sushi cannot serve.

A rig's `midi:` section compiles to Sushi's own `cc_mappings` for sweeps,
blends and volumes, which run in the engine with nothing from here.
Sushi 1.3.0 cannot do the rest -- `midi_dispatcher.cpp` has no CC-to-bypass,
and its Program Change reaches a plugin's program, not a rig -- so
`compile_midi` writes those into the compiled config under `_midi_agent`
(`rigfile.AGENT_BINDINGS`), and the agent serves them by opening the
controller's port itself:

- a note on a bypass: each press (note on) toggles the plugin's bypass;
- a CC on a bypass: a switch, on (the effect engaged) at 64 and above, off
  (bypassed) below, acting only when it crosses;
- a mute (#210), whatever sends it: the channel's send bypassed, so the
  master's return, what the monitors hear, has nothing of the channel. A
  track's own `mute` acts after its plugins and so after the send, and was
  heard to silence nothing. A note toggles it, and a CC follows the value
  (muted at 64 and above) unless it is a momentary or single switch;
- a momentary or single footswitch's CC (#191) on a bypass or a mute: each
  press toggles it (a momentary switch's rise through 64, a single one's every
  message), its release does nothing, and a bounce within `DEBOUNCE_SECONDS`
  is ignored;
- a pedal on a channel's volume (#191): at or below its heel the agent
  silences the send, which Sushi's own mapping leaves 40 dB down;
- a Program Change: program n selects the nth rig in config/rigs/, sorted by
  file name, brought up by the agent's lifecycle;
- a footswitch whose job is `previous-rig` or `next-rig` (#191), from the
  machine file rather than the rig's map, so on every rig: a press steps one
  rig back or on through that same order, stopping at either end.

A rig whose map names one controller, on a machine with another that steps
rigs (the MPD218 and the FBV), is two ports: a `Plan` is a `Feed` per port.
A port whose pedal is not plugged in is waited for (`AlsaPort`, `Listener`),
and is a problem only for the controller the rig's own map is written for
(`Feed.required`).

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
is the machine file's number, compared with that directly. Sushi counts
its own `cc_mappings`' channels from 0 too: a mapping on channel 0 followed
the FBV sending on the wire's channel 0 (the laptop, 2026-11-23, #210).

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

import errno
import json
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional, Protocol, Sequence, Union

# A CC switch is on at this value and above, as MIDI's own switch controllers
# (sustain, CC 64) are: 0-63 off, 64-127 on.
SWITCH_ON = 64

# How long one read of the port waits before looking whether it has been told
# to stop. The bound on how long `down` waits for the listener to let go.
POLL_SECONDS = 0.25

# A footswitch press within this long of the last one on the same control is
# a bounce or a resend, not a second press (#191): 40 ms is shorter than any
# two presses a foot makes, and longer than a contact's bounce.
DEBOUNCE_SECONDS = 0.04

# The agent's name in the ALSA sequencer, as `aseqdump -l` will list it.
CLIENT_NAME = "sushi-rig agent"

# Sushi's own name in the ALSA sequencer (#210). Sushi 1.3.0 started with -j
# (its ALSA MIDI frontend, as `up` starts it) is client `Sushi`, its first
# MIDI input port 0 `listen:in` and its output port 1 `read:out`: `aconnect
# -l` on the laptop, 2026-11-23, across three Sushi restarts. Its client
# number is given at run time (129 there), so it is found by this name.
SUSHI_CLIENT = "Sushi"

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
    "SequencerClient.subscribe_port",
    "SequencerClient.unsubscribe_port",
    "SequencerClient.list_port_subscribers",
    "Port.connect_from",
    "PortInfo",
    "WRITE_PORT",
    "PortType.MIDI_GENERIC",
    "PortType.APPLICATION",
    "PortType.ANY",
    "SubscriptionQueryType.READ",
    "SYSTEM_ANNOUNCE",
    "MidiBytesEvent",
    "ClientStartEvent",
    "PortStartEvent",
    "ClientExitEvent",
    "PortExitEvent",
    "PortUnsubscribedEvent",
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


class PortAbsent(PortError):
    """No port of that name is listed: the pedal is not plugged in (#191). Not
    a fault for a controller the running rig's map does not use, and one the
    listener waits for, in case it is plugged in later."""


class LinkProblem(MidiError):
    """The controller could not be linked to Sushi's MIDI input (#210), and
    why. Not a fault of the port: the agent still reads it."""


class MidiUnavailable(PortError):
    """The machine cannot read MIDI at all: alsa-midi is not installed, or
    the ALSA sequencer (/dev/snd/seq) will not open. Said as a problem only
    where the running rig's own map needs a pedal."""


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
    # A CC's switch behaviour (#191): "momentary" is pressed when its value
    # rises through 64, "single" by every message, and either toggles;
    # "latching", and None (a binding written before the device model), follow
    # the value. A note is a press either way.
    switch: str | None = None
    # A pedal on a channel's volume (#191, action "snap"): `heel` is the value
    # at or below which the agent silences the send, and `low`, `high` the
    # send gain Sushi's own mapping gives at 0 and at 127, which the agent
    # puts back once the pedal has left the heel.
    heel: int = 0
    low: float = 0.0
    high: float = 0.0

    def describe(self) -> str:
        on = "all channels" if self.channel is None else f"channel {self.channel}"
        how = f" ({self.switch})" if self.switch else ""
        if self.action == "snap":
            return f"{self.control}: {self.kind} {self.number} on {on}, silence {self.written} at the heel ({self.heel})"
        return f"{self.control}: {self.kind} {self.number} on {on}{how}, {self.action} {self.written}"

    def gain_at(self, value: int) -> float:
        """The send gain a snap binding's pedal at `value` should have: silent
        at or below the heel, otherwise what Sushi's mapping gives."""
        if value <= self.heel:
            return 0.0
        return self.low + (self.high - self.low) * value / MIDI_MAX


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
_SNAP_KEYS = ("control", "cc", "channel", "snap", "heel", "low", "high")
SWITCH_BEHAVIOURS = ("momentary", "single", "latching")  # machine.BEHAVIOURS, which this must not import
PRESSED = ("momentary", "single")  # the behaviours whose press toggles
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
        if "snap" in entry:
            snap = _snap(entry, at, problems)
            if snap is not None:
                controls.append(snap)
            continue
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
            problems.append(f"{at}: switch must be momentary, single or latching, on a cc "
                            f"(got {switch!r} on a {kind})")
            continue
        channel = _channel(entry.get("channel", "all"), at, problems)
        if action == "bypass":
            if not (isinstance(written, str) and written.count("/") == 1 and all(written.split("/"))):
                problems.append(f"{at}: bypass must be channel/plugin (got {written!r})")
                continue
            target = written.split("/")[1]
        else:
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
    for snap in (b for b in controls if b.action == "snap"):
        clash = next((b.control for b in controls if b is not snap and b.kind == "cc" and b.number == snap.number
                      and (b.channel is None or snap.channel is None or b.channel == snap.channel)), None)
        if clash is not None:
            problems.append(f"{snap.control!r} and {clash!r} both send cc {snap.number} on overlapping channels")
    if problems:
        raise BindingError(problems)
    return Bindings(tuple(controls), programs)


def _snap(entry: dict[str, Any], at: str, problems: list[str]) -> Binding | None:
    """A pedal on a channel's volume (#191): `compile_midi`'s
    `{"control", "cc", "channel", "snap": <channel>, "heel", "low", "high"}`."""
    problems += [f"{at}: unknown key {k!r}" for k in entry if k not in _SNAP_KEYS]
    name, number, channel_name = entry.get("control"), entry.get("cc"), entry.get("snap")
    heel, low, high = entry.get("heel"), entry.get("low"), entry.get("high")
    if not isinstance(name, str) or not name:
        problems.append(f"{at}: control must name the control")
        return None
    if type(number) is not int or not 0 <= number <= MIDI_MAX:
        problems.append(f"{at}: a pedal on a volume sends a cc, 0 to 127 (got {entry.get('cc')!r})")
        return None
    if not isinstance(channel_name, str) or not channel_name:
        problems.append(f"{at}: snap must name the channel (got {channel_name!r})")
        return None
    if type(heel) is not int or not 0 <= heel < MIDI_MAX:
        problems.append(f"{at}: heel must be 0 to 126 (got {heel!r})")
        return None
    gains = [v for v in (low, high) if isinstance(v, (int, float)) and not isinstance(v, bool) and 0 <= v <= 1]
    if len(gains) != 2 or low > high:
        problems.append(f"{at}: low and high must be the send's gain, 0 to 1, low at most high (got {low!r}, {high!r})")
        return None
    channel = _channel(entry.get("channel", "all"), at, problems)
    return Binding(name, "cc", number, channel, "snap", channel_name + "_send", channel_name,
                   heel=heel, low=float(low), high=float(high))


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
    """A press on a channel's mute (#210): the agent toggles the bypass of
    the channel's send, not the track's own `mute`, which acts after it."""

    control: str
    track: str

    def describe(self) -> str:
        return f"{self.control}: toggle the mute of {self.track}"


@dataclass(frozen=True)
class SetMute:
    """A CC that follows its value on a mute (#210): a latching footswitch,
    or an untyped controller's CC, muted at 64 and above."""

    control: str
    track: str
    muted: bool

    def describe(self) -> str:
        return f"{self.control}: {'mute' if self.muted else 'unmute'} {self.track}"


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
class Snap:
    """A pedal on a channel's volume has reached its heel (#191): the agent
    silences the send, which Sushi's mapping leaves at its floor."""

    control: str
    processor: str  # the channel's send
    binding: Binding

    def describe(self) -> str:
        return f"{self.control}: set {self.binding.written}'s volume to the pedal's (silent at the heel)"


@dataclass(frozen=True)
class Refused:
    """A message that was bound and cannot be acted on, and why: said, not dropped."""

    control: str
    reason: str

    def describe(self) -> str:
        return f"{self.control}: refused, {self.reason}"


# What `dispatch` decides: one of these, or None for nothing to do.
Action = Union[ToggleBypass, SetBypass, ToggleMute, SetMute, SelectRig, StepRig, StepTo, Snap, Refused]


def _on(channel: int | None, message: Message) -> bool:
    return channel is None or channel == message.channel


def dispatch(
    message: Message | None,
    bindings: Bindings,
    switches: dict[Any, Any],
    rigs: Sequence[str] = (),
    current: str | None = None,
    now: float | None = None,
) -> tuple[Optional[Action], dict[Any, Any]]:
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

    A CC switch's press (#191) depends on its behaviour:

    - momentary: a rising edge, a value of 64 or more after one below 64 (or
      the first heard), so a resend while held is not a second press;
    - single: every message is a press, whatever its value (FBV Control's
      Single, one message a press);
    - latching: each crossing of 64 is a press, since the switch alternates
      its value (FBV Control's Toggle).

    A momentary or single press within `DEBOUNCE_SECONDS` of the last one
    accepted on that control is a bounce and does nothing, and so is a
    momentary rise within that of its last release (a release that bounces); `now` is the
    caller's clock (monotonic seconds), and None turns the debounce off. A
    press of a bypass or a mute toggles; a press of a step is `StepRig`, for
    the agent to decide against the order. A latching switch on a bypass or
    a mute, and an untyped CC on either, still follow the value: the effect
    on, or the channel muted, at 64 and above (`SetBypass`, `SetMute`).

    A pedal on a volume (`snap`) is `Snap` at or below its heel and None
    above it, where Sushi's own mapping has it.

    `switches` holds each switch's last level under its control's name, the
    time of its last press under `("pressed", control)` and of a momentary
    switch's last release under `("released", control)`.
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
        if binding.action == "snap":
            if message.value <= binding.heel:
                return Snap(binding.control, binding.target, binding), switches
            return None, switches
        on = message.value >= SWITCH_ON
        if binding.switch in PRESSED:
            level = {**switches, binding.control: on}
            momentary = binding.switch == "momentary"
            if momentary and not on and switches.get(binding.control) is True and now is not None:
                level[("released", binding.control)] = now  # a falling edge, which a bounce follows
            pressed = on and switches.get(binding.control) is not True if momentary else True
            if not pressed:
                return None, level
            last = switches.get(("pressed", binding.control))
            if now is not None and last is not None and now - last < DEBOUNCE_SECONDS:
                return None, level
            # A rise straight after a release is the release bouncing (0, 127, 0
            # inside a few ms as the foot comes off), not a press.
            released = switches.get(("released", binding.control))
            if now is not None and released is not None and now - released < DEBOUNCE_SECONDS:
                return None, level
            if now is not None:
                level[("pressed", binding.control)] = now
            return _press(binding), level
        if switches.get(binding.control) == on:
            return None, switches
        crossed = {**switches, binding.control: on}
        if binding.action == "step":
            return _press(binding), crossed
        if binding.action == "mute":
            return SetMute(binding.control, binding.target, muted=on), crossed
        return SetBypass(binding.control, binding.target, bypassed=not on), crossed
    return None, switches


def snap_binding(message: Message | None, bindings: Bindings) -> Binding | None:
    """The pedal-on-a-volume binding `message` moves, if any: the agent keeps
    each one's latest value, so a silencing that lands after the pedal has
    left the heel puts Sushi's value back rather than leaving it silent."""
    if message is None or message.kind != "cc":
        return None
    return next((b for b in bindings.controls if b.action == "snap" and b.number == message.number
                 and _on(b.channel, message)), None)


def _press(binding: Binding) -> Action:
    """What one press of a switch does: toggle its bypass or its mute, or
    step rigs. The one place a press becomes an action, so a guard on presses
    (a double tap, #191's later work) has one place to go."""
    if binding.action == "step":
        return StepRig(binding.control, binding.target)
    if binding.action == "mute":
        return ToggleMute(binding.control, binding.target)
    return ToggleBypass(binding.control, binding.target)


def step_to(step: StepRig, rigs: Sequence[str], current: str | None,
            running: str | None = None) -> StepTo | Refused:
    """Where a step press goes, in the program order (`program_order`):
    the rig before or after `current`, or `Refused` at either end -- it does
    not wrap round, so a stray press on the last rig cannot jump to the first
    -- and for a running rig the order does not hold. `current` is the
    running rig's name in config/rigs/, None when it runs from anywhere else
    (`Plan.rig`), and `running` what to call it then."""
    if current is None or current not in rigs:
        return Refused(step.control, f"the running rig {current or running or '(unnamed)'} is not a file of "
                                     "config/rigs/, so it has no previous or next")
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
    names it, and what its messages do. `required` when the running rig's
    own map is written for this controller (#191): a required pedal that is
    not plugged in is a problem; any other (a machine's rig-stepping pedal on
    a rig mapped elsewhere, or with no map) is only "not connected"."""

    controller: str
    port: str
    bindings: Bindings
    required: bool = True
    # Every control of the controller as the machine file describes it,
    # `(name, "cc"|"note", number)`, and the channel it sends on (None for
    # all): which control a message came from, bound or not, for the page's
    # "heard" light (#192). Empty for a feed built without the machine file.
    controls: tuple[tuple[str, str, int], ...] = ()
    channel: int | None = None
    # Sushi's MIDI input to link this pedal to (#210), the map's `port` for
    # the controller the running rig's map is written for, 0 for any other
    # (where an assignment made live would go); None for none, and
    # `link_reason` says why (two controllers on one of Sushi's CCs).
    sushi_port: int | None = None
    link_reason: str | None = None

    def control_of(self, message: "Message") -> str | None:
        """The control `message` came from, by what each control sends and
        the controller's channel; None for any other message (a program
        change, a CC no control sends, another channel)."""
        if self.channel is not None and message.channel != self.channel:
            return None
        kind = "note" if message.kind in ("note_on", "note_off") else message.kind
        for name, sends, number in self.controls:
            if sends == kind and number == message.number:
                return name
        return None


@dataclass(frozen=True)
class Plan:
    """The listeners to start for rig `rig`: a `Feed` per port, the rig's
    own map's controller first. Built with one feed's `controller`, `port`
    and `bindings`, as before the device model, or with `feeds`. `rig` is the
    running rig's name in config/rigs/, and None for a run from any other
    file (a v1 config, `config/src/`), which a step cannot count from even
    where a rig of config/rigs/ has the same name; `running` is what to call
    it either way."""

    rig: str | None
    controller: str = ""
    port: str = ""
    bindings: Bindings = Bindings()
    feeds: tuple[Feed, ...] = ()
    running: str = ""
    # The rig file could not be read to say which pedal its map of sweeps
    # needs: said in /health (`agent.midi.rig_problem`), not swallowed.
    rig_problem: str | None = None

    def __post_init__(self) -> None:
        if not self.running:
            object.__setattr__(self, "running", self.rig or "the running rig")
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
      the map was compiled for, as the machine file the run was compiled
      against describes it;
    - the machine's rig-stepping controls (#191), on every running rig, with
      or without a map, from that same machine file. A machine file that is
      not there has none; one that will not read or validate is a
      `MidiError`, since the pedals it describes would then be silently off.

    A feed is `required` only for the controller the running rig's map was
    compiled for, whether or not its map has anything for the agent (a map
    of sweeps alone still needs its pedal, and is a feed of its own when no
    other source makes one, so that its pedal is linked to Sushi and said
    missing).

    A feed is linked to Sushi's MIDI input (#210, `Feed.sushi_port`) only
    when Sushi maps something of it: the required controller, when the
    running config's cc_mappings (all of them its own, the map's) are not
    empty, to the input they use (the map's `port`, 0 without one). Every
    other feed is not linked, `link_reason` saying why: a controller whose
    only bindings are rig steps or the agent's (a bypass, a mute, a heel)
    needs nothing of Sushi's input, and one that sends a CC the map has Sushi
    use (`_clash`) would move that mapping too, since Sushi maps a CC by
    number and channel, not by device (#210's review: every feed was linked,
    and a link made at the start stayed after an assignment made it a
    clash). That controller is the compiled
    config's (`_meta`'s `rigfile.MIDI_CONTROLLER`), not the rig file's as it
    is now, which may have been edited for the next restart (#192's
    review). A config with nothing of a map in it (no bindings for the agent,
    no cc_mappings) needs no pedal. A config compiled before the controller
    was recorded there, with a map, has the rig file read for it, as before;
    one that will not read is a `MidiError` only where the compiled config
    has bindings that need it.
    """
    import json

    from .machine import MachineError, MachineSpec, machine_path
    from .rigfile import AGENT_BINDINGS, MIDI_CONTROLLER, load_rig

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
    rig_name = rig_in_order(source, Path(root))

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
    by_controller: dict[str, dict[str, Any]] = {}  # each feed's controller, as the machine file describes it
    rig_problem = None
    meta = (config.get("_meta") if isinstance(config, dict) else None) or {}
    if isinstance(meta, dict) and MIDI_CONTROLLER in meta:
        # What the running rig was compiled with: the rig file may have been
        # edited since (a pedal assigned for the next restart, #192), and the
        # running rig still needs what it was started with.
        required = meta[MIDI_CONTROLLER] if isinstance(meta[MIDI_CONTROLLER], str) else None
        said = f"{name}'s compiled config"
    elif not raw and not (isinstance(config, dict) and (config.get("midi") or {}).get("cc_mappings")):
        # Nothing of a map was compiled into it: the rig needs no pedal,
        # whatever its file has gained since it started.
        required, said = None, None
    else:
        # A config compiled before the controller was recorded in it: the
        # rig file says which, as it did.
        required, said = None, source
        rig = None
        if isinstance(source, str) and source:
            try:
                rig = load_rig(Path(root) / source)
            except Exception as exc:  # noqa: BLE001 -- a YAML typo too, which load_rig lets through
                if raw:
                    raise MidiError(f"could not read {source} for its controller: {exc}") from exc
                # Sweeps alone are Sushi's and need nothing of the agent, but
                # which pedal they need is the rig file's to say: unread, a
                # missing pedal would show only as "not connected".
                if isinstance(config, dict) and (config.get("midi") or {}).get("cc_mappings"):
                    rig_problem = " ".join(
                        f"could not read {source} to say which pedal its MIDI map needs, so a missing one "
                        f"is not reported as a problem (the steps still work): {exc}".split())
        if raw and (not isinstance(source, str) or not source):
            raise MidiError(f"{name} carries MIDI bindings but names no rig file to say which controller")
        if raw and rig is not None and rig.midi is None:
            raise MidiError(f"{source} has no midi: section now, though the running rig was compiled with one")
        required = rig.midi.controller if rig is not None and rig.midi is not None else None
    mappings = [m for m in ((config.get("midi") or {}).get("cc_mappings") or [])
                if isinstance(m, dict)] if isinstance(config, dict) else []
    if raw or (mappings and required is not None):
        bindings = parse_bindings(raw) if raw else Bindings()
        if required is None:
            raise MidiError(f"{said or name} names no controller for the MIDI bindings it carries")
        try:
            controller = load_machine().controller_for(required)
        except MachineError as exc:
            raise MidiError(str(exc)) from exc
        port = controller.get("port")
        if not port:
            raise MidiError(
                f"{machine_file.name} gives controller {required!r} no port, so the agent "
                "cannot open it: add its ALSA name (`aseqdump -l`), e.g. port: \"MPD218:MPD218 Port A\""
            )
        feeds[required] = (port, bindings)
        by_controller[required] = controller

    steppers = load_machine().steppers() if machine_file.is_file() else {}
    for controller_name, controller in steppers.items():
        steps = step_bindings(controller)
        port, bindings = feeds.get(controller_name, (controller["port"], Bindings()))
        feeds[controller_name] = (port, Bindings(bindings.controls + steps, bindings.programs))
        by_controller.setdefault(controller_name, controller)
    if not feeds:
        return Off(f"{name} has no MIDI bindings for the agent" + (f"; {rig_problem}" if rig_problem else ""))
    programs_port = next((e.get("port") for e in raw or [] if isinstance(e, dict) and "programs" in e), None)
    own_port = next((m.get("port") for m in mappings if type(m.get("port")) is int), programs_port)
    out = []
    for c, (port, b) in feeds.items():
        heard = _heard_by(by_controller.get(c))
        if c == required and mappings:
            sushi_port, reason = (own_port if type(own_port) is int else 0), None
        elif c == required:
            sushi_port, reason = None, (f"not linked to Sushi: {name}'s map has Sushi play nothing of it (no "
                                        "volume, sweep or blend), so it needs no link")
        else:
            sushi_port = None
            reason = _clash(c, heard, mappings, name) or (
                f"not linked to Sushi: {name}'s map is not written for it, so Sushi plays nothing of it")
        out.append(Feed(c, port, b, c == required, *heard, sushi_port=sushi_port, link_reason=reason))
    return Plan(rig_name, feeds=tuple(out), running=rig_name or name, rig_problem=rig_problem)


def _clash(controller: str, heard: tuple[tuple[tuple[str, str, int], ...], int | None],
           mappings: Sequence[dict[str, Any]], rig: str) -> str | None:
    """Why `controller`, which the running rig's map is not written for, must
    not be linked to Sushi even by hand, or None: one of its controls sends
    a CC the rig's cc_mappings map, on an overlapping channel. Sushi maps a
    CC by number, channel and input, not by device, so linked to that input
    it would move that mapping too."""
    controls, channel = heard
    for mapping in mappings:
        on = mapping.get("channel")
        if channel is not None and isinstance(on, int) and on != channel:
            continue
        for control, kind, number in controls:
            if kind == "cc" and number == mapping.get("cc_number"):
                return (f"not linked to Sushi: its {control} sends cc {number}, which {rig}'s map has Sushi "
                        f"drive {mapping.get('plugin_name')}'s {mapping.get('parameter_name')} with on its MIDI "
                        f"input {mapping.get('port', 0)}, so it would move that too")
    return None


def _heard_by(controller: dict[str, Any] | None) -> tuple[tuple[tuple[str, str, int], ...], int | None]:
    """A feed's `controls` and `channel`, from `MachineSpec.controller`'s
    description of its controller (`Feed.control_of`)."""
    if not controller:
        return (), None
    channel = controller.get("channel", "all")
    controls = tuple((name, kind, number) for name, (kind, number) in sorted(controller.get("controls", {}).items()))
    return controls, None if channel == "all" else channel


def rig_in_order(source: Any, root: Path) -> str | None:
    """The name of the running rig in config/rigs/, from the file it runs
    from (`config/rigs/<name>.yaml`), or None for any other file: a rig of
    `config/src/` or a v1 config is not in the order a step counts through,
    whatever its name."""
    if not isinstance(source, str) or not source:
        return None
    path = Path(source)
    folder = (Path(root) / "config" / "rigs").resolve()
    resolved = (path if path.is_absolute() else Path(root) / path).resolve()
    if resolved.suffix != ".yaml" or resolved.parent != folder:
        return None
    return resolved.stem


# --------------------------------------------------------------------------- #
# The port
# --------------------------------------------------------------------------- #

class Port(Protocol):
    """What the listener needs of a port. Used from one thread only: the
    listener's, which opens it, reads it and closes it.

    A port may be open with its pedal not there (#191): `attached` False,
    and `absence` saying why. Reading it then waits for the pedal to be
    plugged in, and a pedal that goes away mid-set leaves it unattached
    again; the listener watches `attached` and says each change. A port
    without the attribute is taken to be attached throughout.

    A port asked to link the pedal to Sushi (#210) says whether it is linked
    (`linked`) and why (`link_reason`); the listener says each change too. A
    port without them was not asked to link."""

    def read(self, timeout: float) -> bytes | None:
        """One complete MIDI message, or None if none came within `timeout`.
        Raises `PortError` when the port can no longer be read."""

    def close(self) -> None: ...


def find_port(name: str, available: Sequence[tuple[str, Any]]) -> Any:
    """The address of the port called `name` among `(name, address)` pairs, or
    `PortError` saying what there is. Exact: the machine file's name is
    `client:port`, as `aseqdump -l` shows the two columns, and a near match
    would be a guess about which device is the pedal. Two ports of one name
    (two of the same controller plugged in) are refused for the same reason.
    A name that is not listed is `PortAbsent`: the pedal is not plugged in."""
    matches = [address for listed, address in available if listed == name]
    if len(matches) == 1:
        return matches[0]
    if matches:
        raise PortError(f"two ports are called {name!r}, so which is the pedal is not known: unplug one")
    if not available:
        raise PortAbsent(f"no MIDI port called {name!r}: ALSA lists no MIDI ports at all. Is it plugged in?")
    listed = ", ".join(sorted(n for n, _ in available))
    raise PortAbsent(f"no MIDI port called {name!r}; ALSA lists: {listed}. Is it plugged in?")


def sushi_input(listed: Sequence[tuple[str, int, int]], index: int) -> tuple[int, int]:
    """The address of Sushi's MIDI input number `index` (a rig map's `port`,
    as `compile_midi` writes it into each cc_mapping) among the sequencer's
    writable ports, `(client name, client, port)`; `LinkProblem` saying
    what there is otherwise. Pure.

    Found by the client's name (`SUSHI_CLIENT`), never its number, which
    ALSA gives at run time; its inputs are its writable ports in port order.
    Seen: port 0 `listen:in`, with one input (the laptop, 2026-11-23). That a
    rig with `midi_inputs: 2` has its second input at the next port is how
    the numbering reads, and has not been seen. Two clients called Sushi are
    refused, as two pedals of one name are: which is the rig is not known."""
    clients = sorted({client for name, client, _ in listed if name == SUSHI_CLIENT})
    if not clients:
        raise LinkProblem(f"no ALSA MIDI input called {SUSHI_CLIENT!r} is listed: Sushi is not running, or "
                          "not yet, or runs without its ALSA MIDI frontend (-j)")
    if len(clients) > 1:
        raise LinkProblem(f"{len(clients)} ALSA clients are called {SUSHI_CLIENT!r} ({', '.join(map(str, clients))}), "
                          "so which is the rig is not known")
    inputs = sorted(port for name, client, port in listed if client == clients[0])
    if index >= len(inputs):
        raise LinkProblem(f"the rig's map is on Sushi's MIDI input {index}, and Sushi lists "
                          f"{len(inputs)} ({SUSHI_CLIENT} client {clients[0]})")
    return clients[0], inputs[index]


# What `AlsaPort` says of a pedal it was not asked to link and that is not
# linked: the agent shows the plan's reason in its place (`Feed.link_reason`).
NOT_ASKED = "not linked, and not asked to be"

# The links between a pedal and Sushi's input that this process made
# (#210's review), as `(pedal, Sushi input)` ALSA addresses: the only links
# the agent ever takes down. One made by hand, by `up --direct` (another
# process), or by an agent before this one, is left as it is. ALSA keeps no
# record of who subscribed a port to another, so this is kept here, shared by
# every `AlsaPort` of the process: a listener replaced by the next (a re-plan,
# a switch) leaves its links to the one after it, which knows them as its own.
_MADE: set[tuple[tuple[int, int], tuple[int, int]]] = set()
_MADE_LOCK = threading.Lock()


def _made(source: tuple[int, int], dest: tuple[int, int]) -> bool:
    with _MADE_LOCK:
        return (tuple(source), tuple(dest)) in _MADE


def _forget(keep: Callable[[tuple[int, int], tuple[int, int]], bool]) -> None:
    with _MADE_LOCK:
        for link in [link for link in _MADE if not keep(*link)]:
            _MADE.discard(link)


class AlsaPort:
    """The controller's port, read through the ALSA sequencer (alsa-midi).

    The agent registers a sequencer client of its own (`CLIENT_NAME`) with
    one input port, and subscribes it to the controller's port: ALSA delivers
    a port's events to every subscriber, so this takes nothing from Sushi's
    own MIDI input or anything else listening. It also subscribes to the
    system announcements (#191): a controller that is not plugged in yet, or
    is unplugged mid-set, leaves the port open and unattached (`attached`),
    and each client or port that starts afterwards has the ports listed again,
    so the pedal is subscribed to as soon as it appears, with no polling.

    Events are read as bytes through libasound's decoder, with running status
    off (alsa-midi sets that), so each read is one whole message for `decode`.

    `MidiUnavailable` when there is no MIDI to read at all (no alsa-midi, no
    sequencer). NOT VERIFIED against a sequencer: nothing here has run with
    /dev/snd/seq, and the hot-plug is shown against a stand-in for alsa-midi.

    **The link to Sushi (#210).** Sushi's `cc_mappings` (sweeps, volumes,
    blends) act only on what reaches Sushi's own MIDI input, and the patchbay
    links audio alone, so with `sushi_port` given this subscribes Sushi's
    input number `sushi_port` (`sushi_input`) to the pedal, as `aconnect
    "<pedal>" "Sushi:0"` does: by name, whenever the pedal is attached and
    Sushi is listed. What there is is read from ALSA's own lists each time
    (`_links`): at the open, and at every announcement while the pedal is
    attached (a client or port starting or going, a subscription made or
    taken down), so `linked` says what the subscriber list says, whoever made
    the link.

    - A link already there is left as it is and not made twice.
    - A link this process made (`_MADE`) to any input of Sushi's but the one
      asked for, `sushi_port` None included, is taken down: a re-plan that no
      longer links the pedal, or links it elsewhere, unlinks it. No other link
      is ever taken down, so one made by hand stays, and is said as linked.
    - The link asked for, taken down by hand while that Sushi is still there,
      is not made again for that Sushi (`_by_hand`, its client number): a
      Sushi that goes, or a Sushi of another client number, lifts it, so the
      next Sushi is linked whichever ALSA announces first, the link going
      or Sushi going (#210's review).

    The link outlives the port: it is the pedal's and Sushi's, and goes when
    either does. `linked` is whether the pedal reaches an input of Sushi's,
    and `link_reason` says which and who made it, or why not. The manual
    link made the volume pedal follow on the laptop (2026-11-23); this code
    has run only against a stand-in for alsa-midi.
    """

    def __init__(self, name: str, sushi_port: int | None = None):
        try:
            import alsa_midi
        except (ImportError, OSError) as exc:
            raise MidiUnavailable(f"alsa-midi is not installed, so the agent cannot read pedals: "
                                  f"pip install -e tool[midi] ({exc})") from exc
        self._alsa = alsa_midi
        self.name = name
        self._source: Any = None
        self.absence: str | None = None
        self._sushi_port = sushi_port
        self._linked_to: tuple[int, int] | None = None  # the input asked for, while seen linked
        self._by_hand: int | None = None  # the Sushi whose link asked for was taken down by hand
        self.linked: bool = False
        self.link_reason: str = "not linked yet: opening the port"
        try:
            self._client = alsa_midi.SequencerClient(CLIENT_NAME)
        except alsa_midi.ALSAError as exc:
            raise MidiUnavailable(f"cannot open the ALSA sequencer (/dev/snd/seq): {exc}") from exc
        try:
            self._port = self._client.create_port(
                "pedals in", caps=alsa_midi.WRITE_PORT,
                type=alsa_midi.PortType.MIDI_GENERIC | alsa_midi.PortType.APPLICATION)
            self._port.connect_from(alsa_midi.SYSTEM_ANNOUNCE)
            self._attach()
        except alsa_midi.ALSAError as exc:
            self._client.close()
            raise PortError(f"cannot subscribe to {name!r}: {exc}") from exc
        except BaseException:
            self._client.close()
            raise

    @property
    def attached(self) -> bool:
        return self._source is not None

    def _attach(self) -> None:
        """Subscribe to the pedal if it is listed now; else say why not."""
        listed = [(f"{p.client_name}:{p.name}", (p.client_id, p.port_id))
                  for p in self._client.list_ports(input=True)]
        try:
            source = find_port(self.name, listed)
        except PortAbsent as exc:
            self.absence = str(exc)
            self._unlinked("not linked: the pedal is not plugged in")
            return
        self._port.connect_from(source)
        self._source, self.absence = tuple(source), None
        self._links()

    def _unlinked(self, reason: str) -> None:
        self.linked, self.link_reason, self._linked_to = False, reason, None

    def _links(self) -> None:
        """Take the pedal's links to Sushi to what is asked, from ALSA's own
        lists, and say what there is then (#210)."""
        a, source = self._alsa, tuple(self._source)
        try:
            listed = [(p.client_name, p.client_id, p.port_id)
                      for p in self._client.list_ports(output=True, type=a.PortType.ANY)]
            held = {tuple(s.addr) for s in
                    self._client.list_port_subscribers(source, type=a.SubscriptionQueryType.READ)}
        except a.ALSAError as exc:
            self._unlinked(f"not linked: ALSA would not list the ports or the pedal's links: {exc}")
            return
        sushis = sorted({client for name, client, _ in listed if name == SUSHI_CLIENT})
        inputs = {client: sorted(port for _, c, port in listed if c == client) for client in sushis}

        def where(dest: tuple[int, int]) -> str:
            index = inputs.get(dest[0], []).index(dest[1]) if dest[1] in inputs.get(dest[0], []) else "?"
            return f"{self.name} to Sushi's MIDI input {index} ({dest[0]}:{dest[1]})"

        # What this process made and is gone (the pedal or Sushi went, or it
        # was taken down by hand) is forgotten; the link asked for, gone
        # while its Sushi is still there, was taken down by hand.
        _forget(lambda src, dest: src != source or dest in held)
        if self._linked_to is not None and self._linked_to not in held and self._linked_to[0] in sushis:
            self._by_hand = self._linked_to[0]
        if self._by_hand is not None and self._by_hand not in sushis:
            self._by_hand = None  # that Sushi has gone: the next one is linked
        target, problem, how = None, None, None
        if self._sushi_port is not None:
            try:
                target = tuple(sushi_input(listed, self._sushi_port))
            except LinkProblem as exc:
                problem = str(exc)
        said: list[str] = []
        for dest in sorted(d for d in held if d[0] in sushis and d != target and _made(source, d)):
            try:
                self._client.unsubscribe_port(source, dest)
            except a.ALSAError as exc:
                said.append(f"taking the agent's own link {where(dest)} down failed: {exc}")
                continue
            held.discard(dest)
            _forget(lambda src, d, gone=dest: (src, d) != (source, gone))
            said.append(f"the agent's own link {where(dest)} taken down, as it is not asked for now")
        if target is not None and target[0] == self._by_hand and target not in held:
            problem = ("the link was taken down (by hand?), and is left down while that Sushi runs; it is "
                       "made again for the next Sushi, or when the rig is next brought up, restarted or switched")
        elif target is not None and target in held:
            how = ("linked by the agent" if _made(source, target)
                   else "linked already, not by this agent, and left as it is")
        elif target is not None:
            try:
                self._client.subscribe_port(source, target)
            except a.ALSAError as exc:
                if getattr(exc, "errnum", None) != -errno.EBUSY:  # EBUSY: subscribed already
                    problem = f"linking {where(target)} failed: {exc}"
                else:
                    how = "linked already, not by this agent, and left as it is"
                    held.add(target)
            else:
                with _MADE_LOCK:
                    _MADE.add((source, target))
                held.add(target)
                how = "linked by the agent"
        others = sorted(d for d in held if d[0] in sushis and d != target)
        self._linked_to = target if target is not None and target in held else None
        if self._linked_to is not None:
            self.linked, self.link_reason = True, f"{how}: {where(target)}"
        elif others:
            self.linked = True
            self.link_reason = (f"linked, not by this agent, and left as it is: {where(others[0])}"
                                + (f"; not linked as asked: {problem}" if problem else ""))
        elif problem is not None:
            self.linked, self.link_reason = False, f"not linked: {problem}"
        else:
            self.linked, self.link_reason = False, NOT_ASKED
        if said:
            self.link_reason += " (" + "; ".join(said) + ")"

    def read(self, timeout: float) -> bytes | None:
        a = self._alsa
        try:
            event = self._client.event_input(prefer_bytes=True, timeout=timeout)
        except a.ALSAError as exc:
            raise PortError(f"reading the controller failed: {exc}") from exc
        if isinstance(event, a.ClientExitEvent):
            # A client gone takes its links with it: what this process made
            # from or to it is not there to take down.
            gone = getattr(getattr(event, "addr", None), "client_id", None)
            _forget(lambda src, dest: gone not in (src[0], dest[0]))
        if self._source is None:
            if isinstance(event, (a.ClientStartEvent, a.PortStartEvent)):
                try:
                    self._attach()
                except a.ALSAError as exc:
                    raise PortError(f"cannot subscribe to {self.name!r}: {exc}") from exc
            return None
        if isinstance(event, a.MidiBytesEvent):
            return bytes(event.midi_bytes)
        client, port = self._source
        if isinstance(event, a.ClientExitEvent) and event.addr.client_id == client:
            self._source = None
            self.absence = "the controller went away (its ALSA client exited): was it unplugged?"
            self._unlinked("not linked: the pedal is not plugged in")
        elif isinstance(event, a.PortExitEvent) and tuple(event.addr) == (client, port):
            self._source = None
            self.absence = "the controller's port went away: was it unplugged?"
            self._unlinked("not linked: the pedal is not plugged in")
        elif event is not None:
            # Any other announcement (a Sushi starting or going, a link made or
            # taken down, by hand or by Sushi going): the lists say what now.
            try:
                self._links()
            except a.ALSAError as exc:
                self._unlinked(f"not linked: {exc}")
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

    A pedal that is not plugged in, or is unplugged mid-set, is not a
    failure (#191): the port stays open, unattached, and the listener says so
    (`on_absent(reason)`) and goes on reading, which waits for the pedal to
    appear; `on_open` says it is attached, each time it is. A port that cannot
    be opened at all, or fails to read once open, is reported once
    (`on_failure(exc, opened)`) and the thread ends: it does not retry, since
    a loop of attempts is a hot thread on the machine running the audio; the
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
        on_absent: Callable[[str], None] | None = None,
        poll: float = POLL_SECONDS,
        link: int | None = None,
        on_link: Callable[[bool, str], None] | None = None,
    ) -> None:
        self.port_name = port_name
        # Sushi's MIDI input to link the pedal to (#210), or None. `open_port`
        # is called as `open_port(name)` for None and `open_port(name, link)`
        # otherwise; an opener must take both (`World.open_port`'s AlsaPort
        # does), since which a feed gets is `plan_for`'s to say.
        self.link = link
        self.on_link = on_link
        self.open_port = open_port
        self.post = post
        self.on_open = on_open
        self.on_message = on_message
        self.on_failure = on_failure
        self.on_absent = on_absent
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

    def _said(self, port: Any, was: bool | None) -> bool:
        """Say whether the pedal is there, when that has changed."""
        attached = getattr(port, "attached", True)
        if attached != was:
            if attached:
                self._hand(self.on_open)
            elif self.on_absent is not None:
                self._hand(self.on_absent, getattr(port, "absence", None) or "the pedal is not plugged in")
        return attached

    def _linked(self, port: Any, was: tuple[Any, Any] | None) -> tuple[Any, Any] | None:
        """Say whether the pedal is linked to Sushi, when that has changed."""
        now = (getattr(port, "linked", None), getattr(port, "link_reason", None))
        if now[0] is not None and now != was and self.on_link is not None:
            self._hand(self.on_link, bool(now[0]), now[1] or "")
        return now

    def _run(self) -> None:
        try:
            port = self.open_port(self.port_name) if self.link is None else self.open_port(self.port_name, self.link)
        except Exception as exc:  # noqa: BLE001 -- reported, never raised into a dead thread
            self._hand(self.on_failure, exc, False)
            return
        try:
            if self._stop.is_set():
                return
            attached = self._said(port, None)
            linked = self._linked(port, None)
            while not self._stop.is_set():
                data = port.read(self.poll)
                attached = self._said(port, attached)
                linked = self._linked(port, linked)
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
# One link, with no agent: `up --direct` (#210)
# --------------------------------------------------------------------------- #

# How long `link_once` waits for Sushi's MIDI input to be listed: `up`
# reports the rig up once Sushi answers gRPC, and whether its ALSA client is
# already there by then has not been seen either way.
LINK_ONCE_SECONDS = 2.0


def link_once(described: dict[str, Any], root: Path, *, open_port: Callable[..., Any] = AlsaPort,
              plan: Callable[[dict[str, Any], Path], "Plan | Off"] = plan_for,
              wait: float = LINK_ONCE_SECONDS) -> list[str]:
    """Link the running rig's own pedal to Sushi once, for a rig brought up
    with no agent (`up --direct`), and say what became of it, a line each.

    Only the controller the rig's map is written for, and only when Sushi
    maps something of it (cc_mappings): without the agent nothing else of
    the pedals is served, and nothing could be assigned live. One look, not
    a listener: a pedal plugged in later, or a Sushi restarted, is not
    linked, and the agent, started later, links it as it starts listening.
    Never raises: a rig up without its link is said, not a failed `up`."""
    try:
        planned = plan(described, root)
    except Exception as exc:  # noqa: BLE001 -- said; the rig is up regardless
        return [f"pedals not linked to Sushi: the rig's files could not say which pedal ({exc})"]
    if isinstance(planned, Off):
        return []
    said = []
    for feed in planned.feeds:
        if feed.sushi_port is None:  # `plan_for`'s rule, the agent's too: Sushi maps nothing of it
            continue
        try:
            port = open_port(feed.port, feed.sushi_port)
        except Exception as exc:  # noqa: BLE001 -- said
            said.append(f"{feed.controller} not linked to Sushi: {exc}")
            continue
        try:
            deadline = time.monotonic() + wait
            while not port.linked and getattr(port, "attached", True) and time.monotonic() < deadline:
                port.read(min(POLL_SECONDS, max(0.0, deadline - time.monotonic())))
            said.append(f"{feed.controller}: {port.link_reason}")
        except Exception as exc:  # noqa: BLE001 -- said
            said.append(f"{feed.controller} not linked to Sushi: {exc}")
        finally:
            try:
                port.close()
            except Exception:  # noqa: BLE001 -- the link is made or not; closing changes neither
                pass
    return said


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

    open_port: Callable[..., Port] = AlsaPort  # (name) or (name, sushi_port): `Listener.link`
    plan: Callable[[dict[str, Any], Path], "Plan | Off"] = plan_for
    rigs: Callable[[Path], list[str]] = program_order
