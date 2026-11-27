"""The agent's MIDI listener (#34, #93): the decisions, and the thread.

What a message means is a pure function from the bytes on the wire and the
rig's `_midi_agent` bindings to one action, so every shape is pinned here
with no ALSA, no pedal and no Sushi: the MPD218's pads and knob as `aseqdump`
showed them on 2026-11-01 (note on/off on channel 0, notes 36 to 41, CC 3,
channel aftertouch while a pad is held), a footswitch's CC, a Program Change.

The bytes below are written by hand, from the MIDI 1.0 status bytes; the
bytes ALSA's own decoder produces for the same events are checked against
`decode` in test_midi_alsa.py, which needs the `midi` extra.
"""

from __future__ import annotations

import json
import queue
import threading
from pathlib import Path

import pytest
from types import SimpleNamespace

from sushi_rig import midi_listener as ml
from sushi_rig.midi_listener import (
    Binding,
    BindingError,
    Message,
    MidiError,
    Off,
    PortError,
    Refused,
    SelectRig,
    SetBypass,
    SetMute,
    ToggleBypass,
    ToggleMute,
    decode,
    dispatch,
    find_port,
    next_bypass,
    parse_bindings,
    plan_for,
    program_order,
    programs_table,
)

# What `compile_midi` writes for the MPD218 (see test_rigfile.py's MIDI
# section): a pad on a bypass, a pad on a mute, a footswitch CC on a bypass,
# and Program Change rig switching. "all" is the machine file's channel.
PAD_BYPASS = {"control": "pad1", "note": 36, "channel": "all", "bypass": "lead/drive"}
PAD_MUTE = {"control": "pad2", "note": 38, "channel": "all", "mute": "clean"}
SWITCH = {"control": "stomp", "cc": 80, "channel": "all", "bypass": "lead/hall"}
PROGRAMS = {"programs": "rigs", "port": 0, "channel": "all"}
ALL = [PAD_BYPASS, PAD_MUTE, SWITCH, PROGRAMS]

RIGS = ["clean", "guitar_vocal", "lead"]


def bindings(*raw):
    return parse_bindings(list(raw or ALL))


def act(data, raw=None, switches=None, rigs=RIGS, current="guitar_vocal"):
    """The action alone, for a message that does not touch a switch."""
    action, _ = dispatch(decode(bytes(data)), bindings(*(raw or ALL)), switches or {}, rigs, current)
    return action


# --------------------------------------------------------------------------- #
# decode: the wire, as the listener reads it
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("data, expected", [
    ([0x90, 36, 100], Message("note_on", 0, 36, 100)),
    ([0x95, 41, 1], Message("note_on", 5, 41, 1)),
    # Velocity 0 is a note off by the MIDI spec, and devices use it so.
    ([0x90, 36, 0], Message("note_off", 0, 36, 0)),
    ([0x80, 36, 64], Message("note_off", 0, 36, 64)),
    ([0xB0, 3, 127], Message("cc", 0, 3, 127)),
    ([0xBF, 80, 0], Message("cc", 15, 80, 0)),
    ([0xC0, 2], Message("program", 0, 2, 0)),
])
def test_the_channel_messages_the_listener_acts_on_are_decoded(data, expected):
    """Channels are 0 to 15 as they are on the wire and as `aseqdump` prints
    them: the agent's own listener counts from 0, whatever Sushi does."""
    assert decode(bytes(data)) == expected


@pytest.mark.parametrize("data", [
    [0xD0, 64],            # channel aftertouch: an MPD218 pad sends it while held
    [0xA0, 36, 64],        # polyphonic aftertouch
    [0xE0, 0, 64],         # pitch bend
    [0xF8],                # clock
    [0xF0, 0x7E, 0xF7],    # sysex
    [],                    # nothing
    [36, 100],             # a data byte first: running status, which the reader turns off
    [0x90, 36],            # truncated
    [0x90, 200, 100],      # a "data byte" above 127
])
def test_everything_else_is_not_a_message_the_listener_reads(data):
    assert decode(bytes(data)) is None


# --------------------------------------------------------------------------- #
# The bindings, as the compiled config carries them
# --------------------------------------------------------------------------- #

def test_the_compiled_shapes_parse():
    parsed = bindings()
    assert parsed.controls == (
        Binding("pad1", "note", 36, None, "bypass", "drive", "lead/drive"),
        Binding("pad2", "note", 38, None, "mute", "clean", "clean"),
        Binding("stomp", "cc", 80, None, "bypass", "hall", "lead/hall"),
    )
    assert parsed.programs == ml.Programs(channel=None, port=0)


def test_a_numbered_channel_is_kept_as_the_machine_file_gives_it():
    parsed = parse_bindings([dict(PAD_BYPASS, channel=9), dict(PROGRAMS, channel=0)])
    assert parsed.controls[0].channel == 9
    assert parsed.programs.channel == 0


@pytest.mark.parametrize("raw, said", [
    ("not a list", "must be a list"),
    ([7], "is 7, not a mapping"),
    ([{"control": "pad1", "note": 36, "channel": "all"}], "exactly one of bypass, mute"),
    ([dict(PAD_BYPASS, mute="lead")], "exactly one of bypass, mute"),
    ([{"control": "pad1", "channel": "all", "bypass": "lead/drive"}], "exactly one of note, cc"),
    ([dict(PAD_BYPASS, cc=3)], "exactly one of note, cc"),
    ([dict(PAD_BYPASS, note=128)], "0 to 127"),
    ([dict(PAD_BYPASS, note=True)], "0 to 127"),
    ([dict(PAD_BYPASS, channel=16)], "0 to 15 or \"all\""),
    ([dict(PAD_BYPASS, channel="1")], "0 to 15 or \"all\""),
    ([dict(PAD_BYPASS, bypass="drive")], "channel/plugin"),
    ([dict(PAD_MUTE, mute="")], "a channel name"),
    ([PAD_BYPASS, dict(PAD_MUTE, note=36)], "both send note 36"),
    # "all" overlaps every channel: one of the two would never be served.
    ([PAD_BYPASS, dict(PAD_MUTE, note=36, channel=1)], "both send note 36"),
    ([dict(PAD_BYPASS, channel=1), dict(PAD_MUTE, note=36)], "both send note 36"),
    ([PROGRAMS, PROGRAMS], "Program Change is bound twice"),
    ([dict(PROGRAMS, programs="presets")], "programs must be \"rigs\""),
    ([dict(PAD_BYPASS, colour="red")], "unknown key 'colour'"),
])
def test_a_binding_the_listener_does_not_understand_is_refused_not_guessed(raw, said):
    with pytest.raises(BindingError, match=said):
        parse_bindings(raw)


def test_one_number_on_two_separate_channels_is_two_bindings():
    parsed = parse_bindings([dict(PAD_BYPASS, channel=1), dict(PAD_MUTE, note=36, channel=2)])
    assert len(parsed.controls) == 2


def test_every_problem_is_named_at_once():
    with pytest.raises(BindingError) as caught:
        parse_bindings([dict(PAD_BYPASS, note=200), dict(PAD_MUTE, channel=99)])
    assert len(caught.value.problems) == 2


# --------------------------------------------------------------------------- #
# dispatch: one message, the bindings, one action
# --------------------------------------------------------------------------- #

def test_a_pad_press_toggles_its_plugins_bypass():
    assert act([0x90, 36, 100]) == ToggleBypass("pad1", "drive")


def test_a_pad_release_does_nothing():
    """A pad sends note on when pressed and note off when let go; one press
    is one toggle, so the release is not a second one."""
    assert act([0x80, 36, 0]) is None
    assert act([0x90, 36, 0]) is None


def test_a_pad_on_a_mute_toggles_the_channels_mute():
    assert act([0x90, 38, 90]) == ToggleMute("pad2", "clean")


def test_aftertouch_from_a_held_pad_is_ignored():
    """Measured on the MPD218: holding a pad sends channel aftertouch. It is
    not a message the listener reads, so it cannot re-trigger the pad."""
    assert decode(bytes([0xD0, 80])) is None
    assert decode(bytes([0xA0, 36, 80])) is None


@pytest.mark.parametrize("data", [
    [0x90, 40, 100],   # pad3: a note the rig binds to nothing
    [0xB0, 3, 100],    # knob1: a CC the agent does not serve (Sushi's, if anyone's)
    [0x80, 41, 0],
])
def test_an_unbound_message_is_ignored(data):
    assert act(data) is None


def test_a_bound_note_on_another_channel_is_ignored_and_all_matches_any():
    on_nine = [dict(PAD_BYPASS, channel=9)]
    assert act([0x90, 36, 100], raw=on_nine) is None
    assert act([0x99, 36, 100], raw=on_nine) == ToggleBypass("pad1", "drive")
    assert act([0x9F, 36, 100]) == ToggleBypass("pad1", "drive"), "\"all\" is omni"


@pytest.mark.parametrize("value, bypassed", [(127, False), (64, False), (63, True), (0, True)])
def test_a_cc_switch_is_on_at_64_and_above(value, bypassed):
    """On is the effect engaged, as a stompbox's switch: on at 64 and above
    is "not bypassed", off below is "bypassed"."""
    action, switches = dispatch(decode(bytes([0xB0, 80, value])), bindings(), {}, RIGS, None)
    assert action == SetBypass("stomp", "hall", bypassed)
    assert switches == {"stomp": value >= 64}


def test_a_cc_switch_acts_only_on_a_transition():
    """An expression pedal or a chattering switch sends many values; only a
    crossing of 64 is a change. The first value heard is a transition from
    not knowing."""
    parsed = bindings()
    switches: dict = {}
    seen = []
    for value in (0, 10, 63, 64, 100, 127, 64, 63, 0, 127):
        action, switches = dispatch(Message("cc", 0, 80, value), parsed, switches, RIGS, None)
        seen.append(None if action is None else action.bypassed)
    assert seen == [True, None, None, False, None, None, None, True, None, False]


def test_dispatch_does_not_change_the_switches_it_was_given():
    before = {"stomp": True}
    _, after = dispatch(Message("cc", 0, 80, 0), bindings(), before, RIGS, None)
    assert before == {"stomp": True} and after == {"stomp": False}


def test_a_program_change_selects_the_nth_rig_by_name():
    assert act([0xC0, 0]) == SelectRig(0, "clean")
    assert act([0xC0, 2]) == SelectRig(2, "lead")


def test_a_program_change_out_of_range_is_refused_and_says_what_there_is():
    action = act([0xC0, 3])
    assert isinstance(action, Refused)
    assert "program 3" in action.reason and "0 to 2" in action.reason
    action = act([0xC0, 0], rigs=[])
    assert isinstance(action, Refused) and "config/rigs/ holds no rigs" in action.reason


def test_a_program_change_for_the_rig_already_running_restarts_nothing():
    """Switching costs about five seconds of silence; doing that to land on
    the rig that is already playing would be a fault the player caused by
    pressing the right button."""
    assert act([0xC0, 1], current="guitar_vocal") is None


def test_a_program_change_is_ignored_without_a_programs_binding_or_on_another_channel():
    assert act([0xC0, 0], raw=[PAD_BYPASS]) is None
    assert act([0xC0, 0], raw=[dict(PROGRAMS, channel=3)]) is None
    assert act([0xC3, 0], raw=[dict(PROGRAMS, channel=3)]) == SelectRig(0, "clean")


# --------------------------------------------------------------------------- #
# What a toggle sets, from what Sushi says now
# --------------------------------------------------------------------------- #

def test_a_toggle_inverts_the_state_sushi_reports_and_a_switch_sets_its_own():
    assert next_bypass(False, None) is True
    assert next_bypass(True, None) is False
    assert next_bypass(True, False) is False
    assert next_bypass(None, True) is True, "a switch says its state; it needs none from Sushi"


def test_a_toggle_refuses_when_sushi_would_not_say_which_way():
    with pytest.raises(MidiError, match="would not say"):
        next_bypass(None, None)


def test_a_cc_on_a_mute_is_the_agents_and_follows_its_value():
    """#210: every mute is the agent's, a CC's too (an untyped knob, a
    latching switch): Sushi maps no CC to the send's bypass, and the track's
    own mute, which it can map, acts after the send. Muted at 64 and above,
    acting only on a crossing, as a CC on a bypass does."""
    knob = {"control": "knob3", "cc": 9, "channel": "all", "mute": "lead"}
    parsed = parse_bindings([knob, dict(knob, control="toe", cc=43, switch="latching")])
    switches: dict = {}
    seen = []
    for number, value in [(9, 0), (9, 100), (9, 120), (9, 10), (43, 127), (43, 0)]:
        action, switches = dispatch(Message("cc", 0, number, value), parsed, switches)
        seen.append(action)
    assert seen == [SetMute("knob3", "lead", False), SetMute("knob3", "lead", True), None,
                    SetMute("knob3", "lead", False), SetMute("toe", "lead", True), SetMute("toe", "lead", False)]


# --------------------------------------------------------------------------- #
# The rig order a Program Change counts through
# --------------------------------------------------------------------------- #

def test_programs_count_through_config_rigs_sorted_by_file_name(tmp_path):
    """By file name, not by stem: "a-b.yaml" sorts before "a.yaml", because
    '-' sorts before '.'. The repository's own electric-chorus-2.yaml comes
    before electric-chorus.yaml for exactly that reason."""
    rigs = tmp_path / "config" / "rigs"
    rigs.mkdir(parents=True)
    for name in ("electric-chorus.yaml", "electric-chorus-2.yaml", "accustomed.yaml",
                 "notes.txt", "electric_board.yaml"):
        (rigs / name).write_text("")
    (rigs / "sub.yaml").mkdir()
    assert program_order(tmp_path) == [
        "accustomed", "electric-chorus-2", "electric-chorus", "electric_board",
    ]
    assert programs_table(["a", "b"]) == [{"program": 0, "rig": "a"}, {"program": 1, "rig": "b"}]


def test_no_rigs_folder_is_no_programs(tmp_path):
    assert program_order(tmp_path) == []


# --------------------------------------------------------------------------- #
# find_port: the machine file's name against what ALSA lists
# --------------------------------------------------------------------------- #

def test_a_port_is_found_by_client_and_port_name():
    available = [("Midi Through:Midi Through Port-0", (14, 0)), ("MPD218:MPD218 Port A", (20, 0))]
    assert find_port("MPD218:MPD218 Port A", available) == (20, 0)


def test_a_missing_port_is_refused_with_what_there_is():
    with pytest.raises(PortError, match=r"no MIDI port called 'MPD218:MPD218 Port A'.*Midi Through:Midi Through Port-0"):
        find_port("MPD218:MPD218 Port A", [("Midi Through:Midi Through Port-0", (14, 0))])
    with pytest.raises(PortError, match="ALSA lists no MIDI ports"):
        find_port("MPD218:MPD218 Port A", [])


def test_two_ports_of_one_name_are_refused_not_chosen_between():
    with pytest.raises(PortError, match="two ports"):
        find_port("MPD218:MPD218 Port A", [("MPD218:MPD218 Port A", (20, 0)), ("MPD218:MPD218 Port A", (24, 0))])


# --------------------------------------------------------------------------- #
# plan_for: what the running rig asks the listener to serve, from its files
# --------------------------------------------------------------------------- #

MACHINE = """\
schema_version: 1
roles:
  guitar-in: {ports: ["Audient iD14:capture_AUX0"]}
controllers:
  mpd218:
    port: "MPD218:MPD218 Port A"
    channel: all
    controls:
      pad1: {note: 36}
"""

RIG = """\
kind: rig
schema_version: 1
name: lead
channels:
- {name: lead, input: guitar-in, width: mono, chain: [{name: drive, uri: "http://drobilla.net/plugins/mda/Overdrive"}]}
master: {output: monitor-out, chain: []}
midi:
  controller: mpd218
  programs: rigs
  controls:
  - {control: pad1, bypass: lead/drive}
"""


def rig_files(tmp_path, bindings_=None, machine=MACHINE, rig=RIG):
    (tmp_path / "config" / "rigs").mkdir(parents=True)
    (tmp_path / "config" / "rigs" / "lead.yaml").write_text(rig)
    (tmp_path / "machines").mkdir()
    (tmp_path / "machines" / "box.yaml").write_text(machine)
    compiled = tmp_path / "run" / "rigs" / "lead.json"
    compiled.parent.mkdir(parents=True)
    config = {"tracks": []}
    if bindings_ is not False:
        config["_midi_agent"] = bindings_ or [PAD_BYPASS, PROGRAMS]
    compiled.write_text(json.dumps(config))
    return {
        "running": True, "config_name": "lead", "config": str(compiled),
        "rig": "config/rigs/lead.yaml", "machine": str(tmp_path / "machines" / "box.yaml"),
    }


def test_the_plan_is_the_compiled_bindings_on_the_machine_files_port(tmp_path):
    plan = plan_for(rig_files(tmp_path), tmp_path)
    assert plan.rig == "lead"
    assert plan.controller == "mpd218"
    assert plan.port == "MPD218:MPD218 Port A"
    assert plan.bindings.controls[0].target == "drive"
    assert plan.bindings.programs is not None


def test_nothing_to_serve_is_off_with_the_reason(tmp_path):
    assert plan_for({"running": False}, tmp_path) == Off("no rig is running")
    v1 = {"running": True, "config_name": "electric_board", "config": str(tmp_path / "x.json"), "rig": None}
    (tmp_path / "x.json").write_text(json.dumps({"tracks": []}))
    assert plan_for(v1, tmp_path) == Off("electric_board has no MIDI bindings for the agent")
    assert plan_for(rig_files(tmp_path / "b", bindings_=False), tmp_path / "b") == \
        Off("lead has no MIDI bindings for the agent")


def test_a_rig_whose_files_cannot_say_is_a_named_problem(tmp_path):
    described = rig_files(tmp_path, machine=MACHINE.replace('    port: "MPD218:MPD218 Port A"\n', ""))
    with pytest.raises(MidiError, match="gives controller 'mpd218' no port"):
        plan_for(described, tmp_path)
    described["config"] = str(tmp_path / "gone.json")
    with pytest.raises(MidiError, match="could not read the running rig's compiled config"):
        plan_for(described, tmp_path)


def test_bindings_the_listener_cannot_read_are_a_problem_not_a_partial_listener(tmp_path):
    described = rig_files(tmp_path, bindings_=[dict(PAD_BYPASS, note=300)])
    with pytest.raises(BindingError):
        plan_for(described, tmp_path)


# --------------------------------------------------------------------------- #
# The listener thread, against a fake port
# --------------------------------------------------------------------------- #

class FakePort:
    """A port that hands out what is put in it; `fail` makes the next read raise."""

    def __init__(self):
        self.inbox: queue.Queue = queue.Queue()
        self.closed = threading.Event()
        self.fail = None

    def read(self, timeout):
        try:
            item = self.inbox.get(timeout=timeout)
        except queue.Empty:
            return None
        if isinstance(item, Exception):
            raise item
        return item

    def close(self):
        self.closed.set()


def run_listener(port=None, open_error=None):
    events: queue.Queue = queue.Queue()

    def open_port(name):
        events.put(("open", name))
        if open_error:
            raise open_error
        return port

    listener = ml.Listener(
        "MPD218:MPD218 Port A", open_port,
        post=lambda fn, *args: fn(*args),
        on_open=lambda: events.put(("opened",)),
        on_message=lambda m: events.put(("message", m)),
        on_failure=lambda exc, opened: events.put(("failed", str(exc), opened)),
        poll=0.01,
    )
    listener.start()
    return listener, events


def test_the_listener_hands_on_decoded_messages_and_drops_the_rest():
    port = FakePort()
    listener, events = run_listener(port)
    try:
        assert events.get(timeout=2) == ("open", "MPD218:MPD218 Port A")
        assert events.get(timeout=2) == ("opened",)
        for data in ([0x90, 36, 100], [0xD0, 50], [0xD0, 51], [0x80, 36, 0]):
            port.inbox.put(bytes(data))
        assert events.get(timeout=2) == ("message", Message("note_on", 0, 36, 100))
        assert events.get(timeout=2) == ("message", Message("note_off", 0, 36, 0))
    finally:
        assert listener.stop(2)
    assert port.closed.is_set(), "the thread that opened the port closes it"
    assert listener.heard == 4, "aftertouch is heard and counted, then dropped"
    assert events.empty()


def test_a_port_that_will_not_open_is_reported_once_and_not_retried():
    listener, events = run_listener(open_error=PortError("no MIDI port called 'x'"))
    assert events.get(timeout=2) == ("open", "MPD218:MPD218 Port A")
    assert events.get(timeout=2) == ("failed", "no MIDI port called 'x'", False)
    listener.thread.join(2)
    assert not listener.thread.is_alive(), "it gives up; it does not spin trying again"
    assert listener.stop(1)
    assert events.empty()


def test_a_port_that_fails_while_listening_is_reported_and_closed():
    port = FakePort()
    listener, events = run_listener(port)
    assert events.get(timeout=2)[0] == "open" and events.get(timeout=2) == ("opened",)
    port.inbox.put(PortError("the controller went away"))
    assert events.get(timeout=2) == ("failed", "the controller went away", True)
    listener.thread.join(2)
    assert not listener.thread.is_alive()
    assert port.closed.is_set()


def test_a_rig_to_switch_to_is_checked_to_compile_from_its_files(tmp_path, monkeypatch):
    """What a switch checks before taking the running rig down: the real
    files, through the real compiler, without a pw-dump or a Sushi. One
    compile for `up --rig` and a switch (`rig.compile_named_rig`, which
    replaced `rig_problem` here), and every failure a `RigError` naming the
    file: a YAML typo or an unreadable file too, not only what the compiler
    foresees."""
    from sushi_rig.rig import RigError, compile_named_rig

    def problem(name):
        try:
            compile_named_rig(name, tmp_path, Path(described["machine"]))
        except RigError as exc:
            return str(exc)
        return None

    described = rig_files(tmp_path)
    assert problem("lead") is None
    assert "no such rig" in problem("absent")
    lead = tmp_path / "config" / "rigs" / "lead.yaml"
    lead.write_text(RIG.replace("control: pad1", "control: pad9"))
    assert "'pad9' is not a control of controller 'mpd218'" in problem("lead")
    lead.write_text(RIG.replace("kind: rig", "kind: [rig"))
    assert problem("lead").startswith("lead.yaml: ") and "Error" in problem("lead")
    lead.write_text(RIG)
    # Unreadable. Stood in, because a mode of 0 is no obstacle to root, which
    # the container job runs as, and an assertion that root passes over tests
    # nothing there.
    # Both ways a file is read: the compile reads its bytes once (#142).
    for method in ("read_text", "read_bytes"):
        def refuse(self, *args, _real=getattr(Path, method), **kwargs):
            if self == lead:
                raise PermissionError(13, "Permission denied", str(self))
            return _real(self, *args, **kwargs)

        monkeypatch.setattr(Path, method, refuse)
    said = problem("lead")
    assert said is not None and said.startswith("lead.yaml: ") and "PermissionError" in said, said


# --------------------------------------------------------------------------- #
# The pedalboard (#191): momentary switches, rig stepping, more than one port
# --------------------------------------------------------------------------- #

# What `compile_midi` writes for a typed footswitch (test_rigfile.py's FBV).
MOMENTARY_BYPASS = {"control": "c", "cc": 22, "channel": 0, "bypass": "lead/drive", "switch": "momentary"}
MOMENTARY_MUTE = {"control": "d", "cc": 23, "channel": 0, "mute": "clean", "switch": "momentary"}
LATCHING_BYPASS = {"control": "c", "cc": 22, "channel": 0, "bypass": "lead/drive", "switch": "latching"}

# The machine file's FBV, as `MachineSpec.controller` describes it.
FBV = {
    "port": "FBV Express Mk II:FBV Express Mk II MIDI 1", "channel": 0, "type": "pedal",
    "controls": {"a": ("cc", 20), "b": ("cc", 21), "c": ("cc", 22), "pedal": ("cc", 7)},
    "model": {
        "a": {"kind": "switch", "behaviour": "momentary", "range": None, "job": "previous-rig"},
        "b": {"kind": "switch", "behaviour": "momentary", "range": None, "job": "next-rig"},
        "c": {"kind": "switch", "behaviour": "momentary", "range": None, "job": None},
        "pedal": {"kind": "expression", "behaviour": None, "range": [0, 127], "job": None},
    },
}


def cc(control_value, number):
    return Message("cc", 0, number, control_value)


def test_a_momentary_switch_toggles_on_each_press_and_its_release_does_nothing():
    """The fix for "on while held": 127 on press, 0 on release, and a press
    is a toggle. A press is the rise through 64, so a value resent while the
    switch is held (the last 127) is not a second press."""
    parsed = parse_bindings([MOMENTARY_BYPASS, MOMENTARY_MUTE])
    switches: dict = {}
    seen = []
    for value in (127, 0, 127, 0, 127, 127):
        action, switches = dispatch(cc(value, 22), parsed, switches)
        seen.append(action)
    press = ToggleBypass("c", "drive")
    assert seen == [press, None, press, None, press, None]
    assert dispatch(cc(127, 23), parsed, {})[0] == ToggleMute("d", "clean")
    assert dispatch(cc(0, 23), parsed, {})[0] is None


def test_a_latching_switch_on_a_bypass_follows_its_value():
    parsed = parse_bindings([LATCHING_BYPASS])
    action, switches = dispatch(cc(127, 22), parsed, {})
    assert action == SetBypass("c", "drive", bypassed=False)
    action, switches = dispatch(cc(0, 22), parsed, switches)
    assert action == SetBypass("c", "drive", bypassed=True)


@pytest.mark.parametrize("raw, said", [
    ([dict(MOMENTARY_BYPASS, switch="sticky")], "switch must be momentary, single or latching"),
    ([dict(PAD_BYPASS, switch="momentary")], "on a cc"),
])
def test_a_switch_binding_in_a_shape_compile_midi_does_not_write_is_refused(raw, said):
    with pytest.raises(BindingError, match=said):
        parse_bindings(raw)


def test_the_machine_files_rig_stepping_controls_are_bindings_of_their_own():
    steps = ml.step_bindings(FBV)
    assert steps == (
        Binding("a", "cc", 20, 0, "step", "previous", "previous-rig", "momentary"),
        Binding("b", "cc", 21, 0, "step", "next", "next-rig", "momentary"),
    )
    assert ml.step_bindings({"port": "x:y", "channel": "all", "controls": {"k": ("cc", 3)}}) == ()


def test_a_step_press_is_a_step_and_its_release_nothing():
    parsed = ml.Bindings(ml.step_bindings(FBV))
    assert dispatch(cc(127, 21), parsed, {})[0] == ml.StepRig("b", "next")
    assert dispatch(cc(0, 21), parsed, {})[0] is None
    assert dispatch(cc(100, 20), parsed, {})[0] == ml.StepRig("a", "previous")


def test_a_latching_step_switch_steps_on_every_change_of_its_value():
    latching = ml.Bindings((Binding("b", "cc", 21, 0, "step", "next", "next-rig", "latching"),))
    switches: dict = {}
    seen = []
    for value in (127, 127, 0, 127):
        action, switches = dispatch(cc(value, 21), latching, switches)
        seen.append(action)
    step = ml.StepRig("b", "next")
    assert seen == [step, None, step, step]


def test_a_step_goes_one_rig_on_or_back_in_file_name_order():
    assert ml.step_to(ml.StepRig("b", "next"), RIGS, "clean") == ml.StepTo("b", "next", 1, "guitar_vocal")
    assert ml.step_to(ml.StepRig("a", "previous"), RIGS, "lead") == ml.StepTo("a", "previous", 1, "guitar_vocal")


def test_a_step_stops_at_either_end_and_does_not_wrap_round():
    last = ml.step_to(ml.StepRig("b", "next"), RIGS, "lead")
    assert isinstance(last, Refused) and "lead is the last rig" in last.reason
    first = ml.step_to(ml.StepRig("a", "previous"), RIGS, "clean")
    assert isinstance(first, Refused) and "clean is the first rig" in first.reason
    alone = ml.step_to(ml.StepRig("b", "next"), ["clean"], "clean")
    assert isinstance(alone, Refused)


def test_a_step_from_a_rig_outside_config_rigs_is_refused_and_said():
    gone = ml.step_to(ml.StepRig("b", "next"), RIGS, "electric_board")
    assert isinstance(gone, Refused) and "electric_board is not a file of config/rigs/" in gone.reason


FBV_MACHINE = MACHINE + """\
  fbv:
    type: pedal
    port: "FBV Express Mk II:FBV Express Mk II MIDI 1"
    channel: 0
    controls:
      a: {cc: 20, kind: switch, behaviour: momentary, job: previous-rig}
      b: {cc: 21, kind: switch, behaviour: momentary, job: next-rig}
      c: {cc: 22, kind: switch, behaviour: momentary}
      pedal: {cc: 7, kind: expression}
"""

UNMAPPED_RIG = RIG[:RIG.index("midi:")]


def test_a_rig_with_no_map_on_a_machine_with_a_stepping_pedal_listens_for_the_steps(tmp_path):
    """A and B work on every rig: a rig with no midi: at all still has a
    listener, on the stepping pedal's port, for its steps alone."""
    described = rig_files(tmp_path, bindings_=False, machine=FBV_MACHINE, rig=UNMAPPED_RIG)
    plan = plan_for(described, tmp_path)
    assert plan.rig == "lead"
    assert [(f.controller, f.port) for f in plan.feeds] == [("fbv", "FBV Express Mk II:FBV Express Mk II MIDI 1")]
    assert [(b.control, b.action, b.target) for b in plan.feeds[0].bindings.controls] == [
        ("a", "step", "previous"), ("b", "step", "next")]
    assert not plan.programs


def test_a_map_on_another_controller_is_a_second_port_beside_the_steps(tmp_path):
    """The MPD218's map keeps working unchanged, and the FBV still steps:
    two ports, the rig's own controller first."""
    plan = plan_for(rig_files(tmp_path, machine=FBV_MACHINE), tmp_path)
    assert [f.controller for f in plan.feeds] == ["mpd218", "fbv"]
    assert plan.controller == "mpd218" and plan.port == "MPD218:MPD218 Port A"
    assert [b.control for b in plan.feeds[0].bindings.controls] == ["pad1"]
    assert [b.control for b in plan.feeds[1].bindings.controls] == ["a", "b"]
    assert plan.programs


def test_a_map_on_the_stepping_pedal_is_one_port_with_both(tmp_path):
    rig = RIG.replace("controller: mpd218", "controller: fbv").replace("control: pad1", "control: c")
    described = rig_files(tmp_path, machine=FBV_MACHINE, rig=rig,
                          bindings_=[MOMENTARY_BYPASS, PROGRAMS])
    plan = plan_for(described, tmp_path)
    assert [f.controller for f in plan.feeds] == ["fbv"]
    assert [b.control for b in plan.bindings.controls] == ["c", "a", "b"]


def test_a_machine_file_that_does_not_validate_is_a_named_problem_not_silently_no_steps(tmp_path):
    described = rig_files(tmp_path, bindings_=False, machine=FBV_MACHINE.replace("job: next-rig", "job: onwards"),
                          rig=UNMAPPED_RIG)
    with pytest.raises(MidiError, match="job must be previous-rig or next-rig"):
        plan_for(described, tmp_path)


# --------------------------------------------------------------------------- #
# PR #199's review: single, debounce, the heel, rigs outside the order, absent
# pedals and plugging one in
# --------------------------------------------------------------------------- #

SINGLE_BYPASS = dict(MOMENTARY_BYPASS, switch="single")
SNAP = {"control": "pedal", "cc": 7, "channel": 0, "snap": "lead", "heel": 3, "low": 0.5, "high": 0.8}


def presses(parsed, values, times, number=22):
    switches: dict = {}
    seen = []
    for value, at in zip(values, times):
        action, switches = dispatch(cc(value, number), parsed, switches, now=at)
        seen.append(action)
    return seen


def test_a_single_switch_is_pressed_by_every_message_whatever_its_value():
    """FBV Control's Single: one message a press. A momentary reading would
    miss a 0, and a second 127 with no release between."""
    seen = presses(parse_bindings([SINGLE_BYPASS]), (127, 127, 0), (0.0, 1.0, 2.0))
    assert seen == [ToggleBypass("c", "drive")] * 3


def test_a_press_within_the_debounce_of_the_last_is_a_bounce():
    press = ToggleBypass("c", "drive")
    # momentary: 127, 0, 127 inside 40 ms is one press bouncing; after it, a press.
    assert presses(parse_bindings([MOMENTARY_BYPASS]), (127, 0, 127, 0, 127),
                   (1.0, 1.005, 1.01, 1.2, 1.3)) == [press, None, None, None, press]
    # single: a resend 10 ms after is a bounce.
    assert presses(parse_bindings([SINGLE_BYPASS]), (127, 127, 127), (1.0, 1.01, 1.1)) == [press, None, press]
    assert ml.DEBOUNCE_SECONDS == 0.04


def test_the_debounce_holds_for_a_mute_and_for_a_rig_step_too():
    mute = parse_bindings([dict(MOMENTARY_MUTE, switch="single")])
    assert presses(mute, (127, 127), (1.0, 1.02), number=23) == [ToggleMute("d", "clean"), None]
    steps = ml.Bindings(ml.step_bindings(FBV))
    assert presses(steps, (127, 0, 127), (1.0, 1.01, 1.02), number=21) == [ml.StepRig("b", "next"), None, None]


def test_a_pedal_on_a_volume_is_silenced_at_its_heel_and_left_to_sushi_above_it():
    parsed = parse_bindings([SNAP])
    (snap,) = parsed.controls
    assert snap.target == "lead_send" and snap.heel == 3
    for value in (0, 3):
        action, _ = dispatch(cc(value, 7), parsed, {})
        assert action == ml.Snap("pedal", "lead_send", snap)
    assert dispatch(cc(4, 7), parsed, {})[0] is None
    assert snap.gain_at(3) == 0.0 and snap.gain_at(127) == 0.8
    assert snap.gain_at(64) == pytest.approx(0.5 + 0.3 * 64 / 127)
    assert ml.snap_binding(cc(90, 7), parsed) is snap and ml.snap_binding(cc(90, 8), parsed) is None


@pytest.mark.parametrize("raw, said", [
    ([dict(SNAP, heel=127)], "heel must be 0 to 126"),
    ([dict(SNAP, low=0.9)], "low at most high"),
    ([dict(SNAP, high=1.5)], "0 to 1"),
    ([dict(SNAP, snap="")], "snap must name the channel"),
    ([dict(SNAP, colour="red")], "unknown key 'colour'"),
    ([SNAP, dict(MOMENTARY_BYPASS, cc=7)], "both send cc 7"),
])
def test_a_pedal_binding_compile_midi_does_not_write_is_refused(raw, said):
    with pytest.raises(BindingError, match=said):
        parse_bindings(raw)


def test_only_a_file_of_config_rigs_is_in_the_order(tmp_path):
    assert ml.rig_in_order("config/rigs/electric_board.yaml", tmp_path) == "electric_board"
    assert ml.rig_in_order(str(tmp_path / "config" / "rigs" / "lead.yaml"), tmp_path) == "lead"
    assert ml.rig_in_order("config/src/electric_board.yaml", tmp_path) is None
    assert ml.rig_in_order("config/rigs/old/lead.yaml", tmp_path) is None
    assert ml.rig_in_order(None, tmp_path) is None


def test_a_rig_run_from_outside_config_rigs_is_not_stepped_from_even_by_a_matching_name(tmp_path):
    """config/src/electric_board.yaml and config/rigs/electric_board.yaml
    share a name; a step from the first must not count from the second."""
    described = rig_files(tmp_path, bindings_=False, machine=FBV_MACHINE, rig=UNMAPPED_RIG)
    (tmp_path / "config" / "src").mkdir()
    (tmp_path / "config" / "src" / "lead.yaml").write_text(UNMAPPED_RIG)
    described["rig"] = "config/src/lead.yaml"
    plan = plan_for(described, tmp_path)
    assert plan.rig is None and plan.running == "lead"
    refused = ml.step_to(ml.StepRig("b", "next"), ["clean", "lead", "zed"], plan.rig, plan.running)
    assert isinstance(refused, Refused) and "lead is not a file of config/rigs/" in refused.reason


def test_a_feed_is_required_only_for_the_controller_the_rigs_map_names(tmp_path):
    plan = plan_for(rig_files(tmp_path / "a", machine=FBV_MACHINE), tmp_path / "a")
    assert [(f.controller, f.required) for f in plan.feeds] == [("mpd218", True), ("fbv", False)]
    plan = plan_for(rig_files(tmp_path / "b", bindings_=False, machine=FBV_MACHINE, rig=UNMAPPED_RIG),
                    tmp_path / "b")
    assert [(f.controller, f.required) for f in plan.feeds] == [("fbv", False)]


def test_a_map_of_sweeps_alone_on_the_stepping_pedal_still_requires_it(tmp_path):
    """No bindings for the agent (a blend is Sushi's), but the rig's map is
    written for the FBV: its being unplugged is the rig's problem."""
    rig = RIG.replace("controller: mpd218", "controller: fbv").replace(
        "  programs: rigs\n", "").replace("{control: pad1, bypass: lead/drive}", "{control: pedal, parameter: lead/drive/Drive}")
    described = rig_files(tmp_path, bindings_=False, machine=FBV_MACHINE, rig=rig)
    # A compiled map of sweeps is Sushi's cc_mappings (a config with nothing
    # of a map in it needs no pedal, #192's review): as `up` would write it,
    # from before the controller was recorded in `_meta`.
    config = json.loads(Path(described["config"]).read_text())
    config["midi"] = {"cc_mappings": [{"port": 0, "channel": 0, "cc_number": 7, "plugin_name": "drive",
                                       "parameter_name": "Drive", "min_range": 0.0, "max_range": 1.0,
                                       "mode": "absolute"}]}
    Path(described["config"]).write_text(json.dumps(config))
    plan = plan_for(described, tmp_path)
    assert [(f.controller, f.required) for f in plan.feeds] == [("fbv", True)]


def test_a_port_not_listed_is_absent_and_two_of_one_name_is_a_fault():
    with pytest.raises(ml.PortAbsent):
        find_port("FBV:FBV 1", [("MPD218:MPD218 Port A", (20, 0))])
    with pytest.raises(ml.PortAbsent):
        find_port("FBV:FBV 1", [])
    with pytest.raises(PortError) as caught:
        find_port("FBV:FBV 1", [("FBV:FBV 1", (20, 0)), ("FBV:FBV 1", (21, 0))])
    assert not isinstance(caught.value, ml.PortAbsent)


class HotPort(FakePort):
    """A port whose pedal comes and goes: `attached` and `absence`, as
    `AlsaPort` has them."""

    def __init__(self, attached=False):
        super().__init__()
        self.attached = attached
        self.absence = "no MIDI port called 'FBV:FBV 1'"


def run_hot_listener(port):
    events: queue.Queue = queue.Queue()
    listener = ml.Listener(
        "FBV:FBV 1", lambda name: port,
        post=lambda fn, *args: fn(*args),
        on_open=lambda: events.put(("opened",)),
        on_message=lambda m: events.put(("message", m)),
        on_failure=lambda exc, opened: events.put(("failed", str(exc), opened)),
        on_absent=lambda reason: events.put(("absent", reason)),
        poll=0.01,
    )
    listener.start()
    return listener, events


def test_the_listener_waits_for_a_pedal_that_is_not_plugged_in_and_picks_it_up():
    port = HotPort(attached=False)
    listener, events = run_hot_listener(port)
    try:
        assert events.get(timeout=2) == ("absent", "no MIDI port called 'FBV:FBV 1'")
        assert listener.thread.is_alive(), "it waits, it does not end"
        port.attached = True
        assert events.get(timeout=2) == ("opened",)
        port.inbox.put(bytes([0xB0, 21, 127]))
        assert events.get(timeout=2) == ("message", Message("cc", 0, 21, 127))
        port.absence = "the controller's port went away: was it unplugged?"
        port.attached = False
        assert events.get(timeout=2) == ("absent", "the controller's port went away: was it unplugged?")
        port.attached = True
        assert events.get(timeout=2) == ("opened",), "and plugged in again, it is back"
    finally:
        assert listener.stop(2)
    assert port.closed.is_set()


class FakeAlsa:
    """A stand-in for alsa-midi: the calls `AlsaPort` makes, a list of ports
    the test changes, and events it hands out."""

    class ALSAError(Exception):
        pass

    class MidiBytesEvent:
        def __init__(self, data):
            self.midi_bytes = data

    class ClientStartEvent:
        pass

    class PortStartEvent:
        pass

    class ClientExitEvent:
        def __init__(self, client):
            self.addr = type("Addr", (), {"client_id": client})()

    class PortExitEvent:
        def __init__(self, client, port):
            self.addr = (client, port)

    class PortUnsubscribedEvent:
        def __init__(self, sender, dest):
            self.connect_sender, self.connect_dest = sender, dest

    class PortSubscribedEvent(PortUnsubscribedEvent):
        pass

    WRITE_PORT = 1
    SYSTEM_ANNOUNCE = (0, 1)

    class PortType:
        ANY = 0
        MIDI_GENERIC = 1
        APPLICATION = 2

    class SubscriptionQueryType:
        READ = 0
        WRITE = 1

    def __init__(self):
        self.ports = []
        # Sushi's and anything else's writable ports, as list_ports(output=True)
        # lists them: (client name, port name, client, port).
        self.inputs = []
        # Links between two other clients' ports, (sender, dest), as ALSA
        # holds them: `aconnect` by hand, or the agent's subscribe_port.
        self.links: list = []
        self.subscribe_calls: list = []
        self.unsubscribe_calls: list = []
        self.subscribe_error = None
        self.events: queue.Queue = queue.Queue()
        self.subscribed = []
        alsa = self

        class Port:
            def connect_from(self, source):
                alsa.subscribed.append(source)

        class Client:
            def __init__(self, name):
                pass

            def list_ports(self, input=None, output=None, type=1):
                listed = alsa.inputs if output else alsa.ports
                return [SimpleNamespace(client_name=c, name=n, client_id=i, port_id=j) for c, n, i, j in listed]

            def list_port_subscribers(self, port, type=None):
                assert type == FakeAlsa.SubscriptionQueryType.READ, "who the pedal sends to"
                return [SimpleNamespace(addr=dest) for sender, dest in alsa.links if tuple(sender) == tuple(port)]

            def subscribe_port(self, sender, dest):
                alsa.subscribe_calls.append((tuple(sender), tuple(dest)))
                if alsa.subscribe_error is not None:
                    raise alsa.subscribe_error
                alsa.links.append((tuple(sender), tuple(dest)))

            def unsubscribe_port(self, sender, dest):
                alsa.unsubscribe_calls.append((tuple(sender), tuple(dest)))
                alsa.links.remove((tuple(sender), tuple(dest)))

            def create_port(self, name, caps, type):
                return Port()

            def event_input(self, prefer_bytes, timeout):
                try:
                    return alsa.events.get(timeout=timeout)
                except queue.Empty:
                    return None

            def close(self):
                pass

        self.SequencerClient = Client


def test_alsa_port_subscribes_when_the_pedal_appears_and_lets_go_when_it_goes(monkeypatch):
    import sys

    alsa = FakeAlsa()
    monkeypatch.setitem(sys.modules, "alsa_midi", alsa)
    port = ml.AlsaPort("FBV:FBV 1")
    assert not port.attached and "no MIDI port called 'FBV:FBV 1'" in port.absence
    assert alsa.subscribed == [FakeAlsa.SYSTEM_ANNOUNCE], "announcements only, until the pedal is there"
    alsa.ports.append(("FBV", "FBV 1", 24, 0))
    alsa.events.put(FakeAlsa.PortStartEvent())
    assert port.read(0.01) is None
    assert port.attached and alsa.subscribed[-1] == (24, 0)
    alsa.events.put(FakeAlsa.MidiBytesEvent(b"\xb0\x15\x7f"))
    assert port.read(0.01) == b"\xb0\x15\x7f"
    alsa.events.put(FakeAlsa.ClientExitEvent(24))
    alsa.ports.clear()
    assert port.read(0.01) is None
    assert not port.attached and "went away" in port.absence
    alsa.ports.append(("FBV", "FBV 1", 25, 0))
    alsa.events.put(FakeAlsa.ClientStartEvent())
    port.read(0.01)
    assert port.attached and alsa.subscribed[-1] == (25, 0), "a replugged pedal has a new client number"


def test_no_alsa_midi_is_midi_unavailable(monkeypatch):
    import sys

    monkeypatch.setitem(sys.modules, "alsa_midi", None)
    with pytest.raises(ml.MidiUnavailable, match="alsa-midi is not installed"):
        ml.AlsaPort("FBV:FBV 1")


def test_a_run_from_a_file_that_is_not_a_rig_file_still_has_its_steps(tmp_path):
    """The rig file is read whenever the run names one, to know which pedal
    it needs; a v1 source that is not a rig file has none, and is no reason
    to lose A and B."""
    described = rig_files(tmp_path, bindings_=False, machine=FBV_MACHINE, rig=UNMAPPED_RIG)
    (tmp_path / "config" / "src").mkdir()
    (tmp_path / "config" / "src" / "electric_board.yaml").write_text("tracks: []\n")
    described["rig"] = "config/src/electric_board.yaml"
    plan = plan_for(described, tmp_path)
    assert [(f.controller, f.required) for f in plan.feeds] == [("fbv", False)] and plan.rig is None


# --------------------------------------------------------------------------- #
# PR #199's re-review
# --------------------------------------------------------------------------- #

def test_a_release_that_bounces_is_not_a_press():
    """Held half a second, then let go with a bounce (0, 127, 0 inside 10 ms):
    one press. The debounce from the last press alone let the bounce's rise,
    half a second on, toggle again."""
    parsed = parse_bindings([MOMENTARY_BYPASS])
    switches: dict = {}
    seen = []
    for at, value in ((0.0, 127), (0.5, 0), (0.505, 127), (0.51, 0)):
        action, switches = dispatch(cc(value, 22), parsed, switches, now=at)
        seen.append(action)
    assert seen == [ToggleBypass("c", "drive"), None, None, None]
    action, _ = dispatch(cc(127, 22), parsed, switches, now=0.8)
    assert action == ToggleBypass("c", "drive"), "and the next real press is one"


def test_an_unreadable_rig_file_behind_a_map_of_sweeps_is_said_not_swallowed(tmp_path):
    """No bindings for the agent, so the rig file is needed only to say which
    pedal the map is for; unread, the steps still work and the problem is
    carried to /health rather than a missing pedal reading "not connected"."""
    described = rig_files(tmp_path, bindings_=False, machine=FBV_MACHINE, rig=UNMAPPED_RIG)
    config = Path(described["config"])
    config.write_text(json.dumps({"tracks": [], "midi": {"cc_mappings": [{"cc_number": 7}]}}))
    (tmp_path / "config" / "rigs" / "lead.yaml").write_text("kind: [rig\n")
    plan = plan_for(described, tmp_path)
    assert [b.control for b in plan.feeds[0].bindings.controls] == ["a", "b"]
    assert plan.rig_problem and "could not read config/rigs/lead.yaml" in plan.rig_problem
    config.write_text(json.dumps({"tracks": []}))
    assert plan_for(described, tmp_path).rig_problem is None, "no map of sweeps, nothing it needs"


def test_the_real_fbvs_messages_through_the_committed_machine_file_and_a_rig_using_every_control():
    """What the FBV Express MkII was heard to send on the laptop (2026-11-22,
    after FBV Control): each switch CC n = 0 on a press and CC 0 = 0 on
    release, the pedal CC 7 in Vol and CC 11 in Wah with the heel resting at 8
    to 13 (and 14 to 17 just after a move, the laptop, 2026-11-23), the toe
    switch CC 43 toggling 127 / 0. Replayed through the
    committed machine file and the real compile, every control assigned: a
    press is one action and its release none, the heel silences the volume
    and the travel above it does not, the toe follows its mode."""
    from sushi_rig.machine import MachineSpec
    from sushi_rig.paths import checkout_root
    from sushi_rig.rigfile import compile_midi, parse_rig

    fbv = MachineSpec.load(checkout_root() / "machines" / "sonny-dewfall-XPS-9320.yaml").controller("fbv")
    rig = parse_rig({
        "kind": "rig", "schema_version": 1, "name": "fbv-all",
        "channels": [
            {"name": "clean", "input": "guitar-in", "width": "mono",
             "chain": [{"name": "comp", "uri": "http://drobilla.net/plugins/mda/Dynamics"}]},
            {"name": "lead", "input": "guitar-in", "width": "mono", "level": -6.0,
             "chain": [{"name": "drive", "uri": "http://drobilla.net/plugins/mda/Overdrive"},
                       {"name": "hall", "uri": "http://distrho.sf.net/plugins/MaGigaverb"}]},
        ],
        "master": {"output": "monitor-out", "chain": [{"name": "lim", "uri": "http://drobilla.net/plugins/mda/Loudness"}]},
        "midi": {"controller": "fbv", "controls": [
            {"control": "c", "bypass": "lead/drive"},
            {"control": "d", "mute": "clean"},
            {"control": "pedal", "volume": "lead"},
            {"control": "wah", "parameter": "lead/hall/Mix"},
            {"control": "toe", "bypass": "lead/hall"},
        ]},
    })
    section, agent = compile_midi(rig, fbv)
    # The pedal's volume and the wah's sweep are Sushi's; nothing is on CC 0.
    assert sorted(m["cc_number"] for m in section["cc_mappings"]) == [7, 11]
    parsed = parse_bindings(agent)
    parsed = ml.Bindings(parsed.controls + ml.step_bindings(fbv), parsed.programs)
    heard = [  # (cc, value, seconds), as aseqdump printed them
        (20, 0, 0.0), (0, 0, 0.3), (21, 0, 1.0), (0, 0, 1.3),
        (22, 0, 2.0), (0, 0, 2.3), (22, 0, 3.0), (0, 0, 3.3), (23, 0, 4.0), (0, 0, 4.3),
        (7, 8, 5.0), (7, 13, 5.1), (7, 15, 5.2), (7, 16, 5.3), (7, 17, 5.31), (7, 20, 5.32),
        (7, 21, 5.33), (7, 127, 5.4),
        (43, 127, 6.0), (11, 9, 6.5), (43, 0, 7.0),
    ]
    switches: dict = {}
    seen = []
    for number, value, at in heard:
        action, switches = dispatch(cc(value, number), parsed, switches,
                                    rigs=["a", "fbv-all", "z"], current="fbv-all", now=at)
        seen.append(type(action).__name__ if action is not None else None)
    assert seen == [
        "StepRig", None, "StepRig", None,
        "ToggleBypass", None, "ToggleBypass", None, "ToggleMute", None,
        # Resting at 16 or 17 just after a move is the heel too (#210).
        "Snap", "Snap", "Snap", "Snap", "Snap", "Snap", None, None,
        "SetBypass", None, "SetBypass",
    ]


# --------------------------------------------------------------------------- #
# #210: the pedal linked to Sushi's MIDI input, by name
# --------------------------------------------------------------------------- #

# As `aconnect -l` showed them on the laptop (2026-11-23): the FBV is client
# 20, Sushi client 129 with `listen:in` at port 0. The numbers are ALSA's to
# give; a test moves them to show nothing here holds on to them.
FBV_NAME = "FBV Express Mk II:FBV Express Mk II MIDI 1"
FBV_ALSA = ("FBV Express Mk II", "FBV Express Mk II MIDI 1", 20, 0)
SUSHI_IN = ("Sushi", "listen:in", 129, 0)


def fake_alsa(monkeypatch, pedal=True, sushi=True):
    import sys

    alsa = FakeAlsa()
    alsa.ALSAError = type("ALSAError", (Exception,), {"__init__": lambda self, message, errnum: (
        Exception.__init__(self, message), setattr(self, "errnum", errnum))[0]})
    monkeypatch.setitem(sys.modules, "alsa_midi", alsa)
    monkeypatch.setattr(ml, "_MADE", set())  # the links this process made: none, for each test
    if pedal:
        alsa.ports.append(FBV_ALSA)
    alsa.inputs.append(("sushi-rig agent", "pedals in", 130, 0))
    if sushi:
        alsa.inputs.append(SUSHI_IN)
    return alsa


def test_sushis_input_is_found_by_its_clients_name_never_its_number():
    listed = [("sushi-rig agent", 130, 0), ("Sushi", 129, 0), ("Midi Through", 14, 0)]
    assert ml.sushi_input(listed, 0) == (129, 0)
    assert ml.sushi_input([("Sushi", 140, 0), ("Sushi", 140, 2)], 1) == (140, 2), "inputs in port order"
    with pytest.raises(ml.LinkProblem, match="no ALSA MIDI input called 'Sushi'"):
        ml.sushi_input([("sushi-rig agent", 130, 0)], 0)
    with pytest.raises(ml.LinkProblem, match="Sushi's MIDI input 1, and Sushi lists 1"):
        ml.sushi_input(listed, 1)
    with pytest.raises(ml.LinkProblem, match="2 ALSA clients are called 'Sushi'"):
        ml.sushi_input([("Sushi", 129, 0), ("Sushi", 131, 0)], 0)


def test_the_pedal_is_linked_to_sushi_by_name_as_the_port_opens(monkeypatch):
    alsa = fake_alsa(monkeypatch)
    port = ml.AlsaPort(FBV_NAME, sushi_port=0)
    assert port.attached
    assert alsa.links == [((20, 0), (129, 0))], "aconnect \"FBV Express Mk II:0\" \"Sushi:0\", by names"
    assert port.linked is True and port.link_reason.startswith("linked by the agent"), port.link_reason
    assert alsa.subscribed[-1] == (20, 0), "and the agent still reads the pedal itself"


def test_a_port_not_asked_to_link_links_nothing_and_says_what_alsa_has(monkeypatch):
    alsa = fake_alsa(monkeypatch)
    port = ml.AlsaPort(FBV_NAME)
    assert alsa.subscribe_calls == [] and port.linked is False and port.link_reason == ml.NOT_ASKED
    # A link by hand is said from ALSA's own list, whoever made it (#210's review).
    alsa.links.append(((20, 0), (129, 0)))
    alsa.events.put(FakeAlsa.PortSubscribedEvent((20, 0), (129, 0)))
    port.read(0.01)
    assert port.linked is True and "not by this agent" in port.link_reason, port.link_reason
    assert alsa.unsubscribe_calls == [], "a link the agent did not make is never taken down"


def test_a_link_made_by_hand_is_left_alone_and_not_made_twice(monkeypatch):
    alsa = fake_alsa(monkeypatch)
    alsa.links.append(((20, 0), (129, 0)))  # the owner's `aconnect 20:0 129:0`, kept
    port = ml.AlsaPort(FBV_NAME, sushi_port=0)
    assert alsa.subscribe_calls == [], "not subscribed a second time"
    assert alsa.links == [((20, 0), (129, 0))]
    assert port.linked is True and "linked already" in port.link_reason
    # Asked for no link now (a re-plan), it is still not the agent's to take down.
    again = ml.AlsaPort(FBV_NAME)
    assert alsa.unsubscribe_calls == [] and again.linked is True


def test_a_link_alsa_says_is_already_there_is_linked(monkeypatch):
    import errno

    alsa = fake_alsa(monkeypatch)
    alsa.subscribe_error = alsa.ALSAError("Device or resource busy", -errno.EBUSY)
    port = ml.AlsaPort(FBV_NAME, sushi_port=0)
    assert port.linked is True and "linked already" in port.link_reason


def test_a_link_alsa_refuses_is_said_and_the_pedal_is_still_read(monkeypatch):
    import errno

    alsa = fake_alsa(monkeypatch)
    alsa.subscribe_error = alsa.ALSAError("Operation not permitted", -errno.EPERM)
    port = ml.AlsaPort(FBV_NAME, sushi_port=0)
    assert port.attached and port.linked is False
    assert "Operation not permitted" in port.link_reason
    alsa.events.put(FakeAlsa.MidiBytesEvent(b"\xb0\x07\x40"))
    assert port.read(0.01) == b"\xb0\x07\x40"


def test_a_sushi_that_starts_after_the_pedal_is_linked_when_it_appears(monkeypatch):
    """A restart: the old Sushi goes (its link with it), a new one comes
    with another client number, and is linked from the announcements."""
    alsa = fake_alsa(monkeypatch, sushi=False)
    port = ml.AlsaPort(FBV_NAME, sushi_port=0)
    assert port.linked is False and "no ALSA MIDI input called 'Sushi'" in port.link_reason
    alsa.inputs.append(SUSHI_IN)
    alsa.events.put(FakeAlsa.PortStartEvent())
    assert port.read(0.01) is None
    assert port.linked is True and alsa.links == [((20, 0), (129, 0))]
    # Sushi restarts under the listener: its exit, then a new client.
    alsa.inputs.remove(SUSHI_IN)
    alsa.links.clear()
    alsa.events.put(FakeAlsa.ClientExitEvent(129))
    port.read(0.01)
    assert port.linked is False and "no ALSA MIDI input called 'Sushi'" in port.link_reason
    alsa.inputs.append(("Sushi", "listen:in", 131, 0))
    alsa.events.put(FakeAlsa.ClientStartEvent())
    port.read(0.01)
    assert port.linked is True and alsa.links == [((20, 0), (131, 0))], "the new Sushi, by name"


def test_a_pedal_plugged_in_after_the_rig_is_up_is_linked_as_it_is_attached(monkeypatch):
    alsa = fake_alsa(monkeypatch, pedal=False)
    port = ml.AlsaPort(FBV_NAME, sushi_port=0)
    assert not port.attached and port.linked is False and "not plugged in" in port.link_reason
    assert alsa.subscribe_calls == []
    alsa.ports.append(("FBV Express Mk II", "FBV Express Mk II MIDI 1", 24, 0))
    alsa.events.put(FakeAlsa.ClientStartEvent())
    port.read(0.01)
    assert port.attached and port.linked is True and alsa.links == [((24, 0), (129, 0))]
    alsa.events.put(FakeAlsa.ClientExitEvent(24))  # unplugged: the link goes with it
    alsa.ports.clear()
    port.read(0.01)
    assert port.linked is False and "not plugged in" in port.link_reason


def test_a_link_taken_down_by_hand_is_not_made_again_by_this_listener(monkeypatch):
    alsa = fake_alsa(monkeypatch)
    port = ml.AlsaPort(FBV_NAME, sushi_port=0)
    alsa.links.clear()
    alsa.events.put(FakeAlsa.PortUnsubscribedEvent((20, 0), (129, 0)))  # aconnect -d
    port.read(0.01)
    assert port.linked is False and "taken down" in port.link_reason
    alsa.events.put(FakeAlsa.PortStartEvent())  # anything else appearing does not undo it
    port.read(0.01)
    assert port.linked is False and len(alsa.subscribe_calls) == 1


def test_a_link_taken_down_by_hand_is_seen_without_its_announcement(monkeypatch):
    """The subscriber list says it, whatever ALSA announces: the next look
    finds the link gone with Sushi still there, and leaves it down."""
    alsa = fake_alsa(monkeypatch)
    port = ml.AlsaPort(FBV_NAME, sushi_port=0)
    alsa.links.clear()
    alsa.events.put(FakeAlsa.PortStartEvent())
    port.read(0.01)
    assert port.linked is False and "taken down" in port.link_reason
    assert len(alsa.subscribe_calls) == 1


@pytest.mark.parametrize("unsubscribed_first", [True, False])
def test_a_link_taken_down_by_hand_is_made_again_for_the_next_sushi(monkeypatch, unsubscribed_first):
    """#210's review (the reviewer's probe): the link taken down by hand,
    then Sushi restarted under the listener. Whichever ALSA announces first,
    the link going or Sushi's exit, the next Sushi is linked."""
    alsa = fake_alsa(monkeypatch)
    port = ml.AlsaPort(FBV_NAME, sushi_port=0)
    unsubscribed = FakeAlsa.PortUnsubscribedEvent((20, 0), (129, 0))
    exits = [FakeAlsa.PortExitEvent(129, 0), FakeAlsa.ClientExitEvent(129)]
    alsa.links.clear()  # aconnect -d, or Sushi's going
    if unsubscribed_first:
        # By hand, seen while Sushi still runs; then Sushi stops.
        alsa.events.put(unsubscribed)
        port.read(0.01)
        assert port.linked is False and "taken down" in port.link_reason
        alsa.inputs.remove(SUSHI_IN)
        events = exits
    else:
        # Sushi stops, its exit announced before the unsubscription.
        alsa.inputs.remove(SUSHI_IN)
        events = exits + [unsubscribed]
    for event in events:
        alsa.events.put(event)
    for _ in range(len(events)):
        port.read(0.01)
    alsa.inputs.append(("Sushi", "listen:in", 131, 0))
    alsa.events.put(FakeAlsa.ClientStartEvent())
    port.read(0.01)
    assert port.linked is True and alsa.links == [((20, 0), (131, 0))], port.link_reason


def test_a_link_taken_down_by_hand_as_sushi_goes_is_made_for_the_next(monkeypatch):
    """The other order of the probe: the unsubscription announced while the
    old Sushi is already gone from the list."""
    alsa = fake_alsa(monkeypatch)
    port = ml.AlsaPort(FBV_NAME, sushi_port=0)
    alsa.links.clear()
    alsa.inputs.remove(SUSHI_IN)
    for event in (FakeAlsa.PortUnsubscribedEvent((20, 0), (129, 0)), FakeAlsa.PortExitEvent(129, 0),
                  FakeAlsa.ClientExitEvent(129)):
        alsa.events.put(event)
    for _ in range(3):
        port.read(0.01)
    alsa.inputs.append(("Sushi", "listen:in", 131, 0))
    alsa.events.put(FakeAlsa.ClientStartEvent())
    port.read(0.01)
    assert port.linked is True, port.link_reason


def test_a_replan_takes_down_only_the_agents_own_link(monkeypatch):
    """#210's review: a pedal linked at the start and re-planned to no link
    (its mappings assigned away, or a clash) is unlinked by the next port
    opened for it, which knows the link as this process's; a link the
    agent did not make is left, and said as linked."""
    alsa = fake_alsa(monkeypatch)
    first = ml.AlsaPort(FBV_NAME, sushi_port=0)
    first.close()
    assert alsa.links == [((20, 0), (129, 0))], "the link outlives the port"
    second = ml.AlsaPort(FBV_NAME)
    assert alsa.unsubscribe_calls == [((20, 0), (129, 0))] and alsa.links == []
    assert second.linked is False and "taken down" in second.link_reason, second.link_reason
    # Linked by hand now: never taken down, and said.
    alsa.links.append(((20, 0), (129, 0)))
    third = ml.AlsaPort(FBV_NAME)
    assert third.linked is True and alsa.links == [((20, 0), (129, 0))]
    assert "not by this agent" in third.link_reason
    # Moved to another of Sushi's inputs: the agent's link there, its own here taken down.
    alsa.links.clear()
    alsa.inputs.append(("Sushi", "listen:in 2", 129, 1))
    ml.AlsaPort(FBV_NAME, sushi_port=0).close()
    moved = ml.AlsaPort(FBV_NAME, sushi_port=1)
    assert alsa.links == [((20, 0), (129, 1))] and moved.linked is True, moved.link_reason


def test_the_listener_says_each_change_of_the_link():
    class LinkingPort(FakePort):
        def __init__(self):
            super().__init__()
            self.linked, self.link_reason = False, "not linked: no Sushi"

    port = LinkingPort()
    opened_with = []
    said: queue.Queue = queue.Queue()

    def open_port(name, link):
        opened_with.append((name, link))
        return port

    listener = ml.Listener(FBV_NAME, open_port, post=lambda fn, *a: fn(*a), on_open=lambda: None,
                           on_message=lambda m: None, on_failure=lambda e, o: said.put(("failed", e)),
                           link=0, on_link=lambda linked, reason: said.put((linked, reason)), poll=0.01)
    listener.start()
    try:
        assert said.get(timeout=2) == (False, "not linked: no Sushi")
        port.linked, port.link_reason = True, "linked by the agent: here"
        assert said.get(timeout=2) == (True, "linked by the agent: here")
    finally:
        assert listener.stop(2)
    assert opened_with == [(FBV_NAME, 0)]



def test_each_feed_is_planned_onto_sushis_input_its_mappings_use(tmp_path):
    """The map's controller to its cc_mappings' port, when it has any; no
    other controller, and one that sends a CC those mappings use says so
    (Sushi maps by CC, not by device)."""
    rig = RIG.replace("controller: mpd218", "controller: fbv").replace("  programs: rigs\n", "").replace(
        "{control: pad1, bypass: lead/drive}", "{control: pedal, parameter: lead/drive/Drive}")
    described = rig_files(tmp_path / "a", bindings_=False, machine=FBV_MACHINE, rig=rig)
    config = json.loads(Path(described["config"]).read_text())
    config["midi"] = {"cc_mappings": [{"port": 1, "channel": 0, "cc_number": 7, "plugin_name": "drive",
                                       "parameter_name": "Drive", "min_range": 0.0, "max_range": 1.0,
                                       "mode": "absolute"}]}
    config["_meta"] = {"midi_controller": "fbv"}
    Path(described["config"]).write_text(json.dumps(config))
    (feed,) = plan_for(described, tmp_path / "a").feeds
    assert (feed.controller, feed.required, feed.sushi_port) == ("fbv", True, 1)
    # A map of sweeps on the MPD218 with the FBV stepping beside it: the FBV
    # is linked to port 0, unless its CCs are the map's there.
    described = rig_files(tmp_path / "b", machine=FBV_MACHINE)
    config = json.loads(Path(described["config"]).read_text())
    mapping = {"port": 0, "channel": "all", "cc_number": 3, "plugin_name": "drive", "parameter_name": "Drive",
               "min_range": 0.0, "max_range": 1.0, "mode": "absolute"}
    config["midi"] = {"cc_mappings": [mapping]}
    Path(described["config"]).write_text(json.dumps(config))
    mpd, fbv = plan_for(described, tmp_path / "b").feeds
    assert (mpd.controller, mpd.sushi_port, fbv.controller, fbv.sushi_port) == ("mpd218", 0, "fbv", None)
    assert "not written for it" in fbv.link_reason, fbv.link_reason
    config["midi"] = {"cc_mappings": [dict(mapping, cc_number=20)]}  # the FBV's A sends cc 20
    Path(described["config"]).write_text(json.dumps(config))
    mpd, fbv = plan_for(described, tmp_path / "b").feeds
    assert fbv.sushi_port is None and "its a sends cc 20" in fbv.link_reason, fbv.link_reason
    # The map's own controller with nothing of Sushi's in its map: no link either.
    config.pop("midi")
    Path(described["config"]).write_text(json.dumps(config))
    mpd, fbv = plan_for(described, tmp_path / "b").feeds
    assert (mpd.sushi_port, fbv.sushi_port) == (None, None) and "play nothing of it" in mpd.link_reason


def test_a_rig_with_no_map_links_no_pedal(tmp_path):
    """#210's review: the FBV stepping rigs on a rig with no map has rig
    steps alone, nothing of Sushi's: not linked."""
    described = rig_files(tmp_path, bindings_=False, machine=FBV_MACHINE, rig=UNMAPPED_RIG)
    (feed,) = plan_for(described, tmp_path).feeds
    assert feed.controller == "fbv" and feed.sushi_port is None and feed.link_reason


def test_a_map_of_sweeps_on_a_pedal_with_no_steps_is_a_feed_so_it_is_linked(tmp_path):
    """Before #210 a map of Sushi's sweeps alone, on a controller with no
    job, made no feed: nothing opened the port, so nothing linked it."""
    rig = RIG.replace("  programs: rigs\n", "").replace("{control: pad1, bypass: lead/drive}",
                                                        "{control: pad1, mute: lead}")
    described = rig_files(tmp_path, bindings_=False, rig=rig)
    config = json.loads(Path(described["config"]).read_text())
    config["midi"] = {"cc_mappings": [{"port": 0, "channel": "all", "cc_number": 3, "plugin_name": "drive",
                                       "parameter_name": "Drive", "min_range": 0.0, "max_range": 1.0,
                                       "mode": "absolute"}]}
    config["_meta"] = {"midi_controller": "mpd218"}
    Path(described["config"]).write_text(json.dumps(config))
    (feed,) = plan_for(described, tmp_path).feeds
    assert (feed.controller, feed.required, feed.sushi_port, feed.bindings.controls) == ("mpd218", True, 0, ())


def test_up_with_no_agent_links_the_rigs_own_pedal_once_and_says_so(tmp_path, monkeypatch):
    """`up --direct` (no agent): one look, linking the controller the rig's
    map is written for when Sushi maps something of it; nothing for a rig
    whose map Sushi has nothing of, and never an exception."""
    alsa = fake_alsa(monkeypatch)
    alsa.ports.append(("MPD218", "MPD218 Port A", 24, 0))
    rig = RIG.replace("  programs: rigs\n", "").replace("{control: pad1, bypass: lead/drive}",
                                                        "{control: pad1, mute: lead}")
    described = rig_files(tmp_path, bindings_=False, rig=rig)
    assert ml.link_once(described, tmp_path) == [], "nothing of the map is Sushi's: nothing to link"
    config = json.loads(Path(described["config"]).read_text())
    config["midi"] = {"cc_mappings": [{"port": 0, "channel": "all", "cc_number": 3, "plugin_name": "drive",
                                       "parameter_name": "Drive", "min_range": 0.0, "max_range": 1.0,
                                       "mode": "absolute"}]}
    config["_meta"] = {"midi_controller": "mpd218"}
    Path(described["config"]).write_text(json.dumps(config))
    (said,) = ml.link_once(described, tmp_path)
    assert said.startswith("mpd218: linked by the agent") and alsa.links == [((24, 0), (129, 0))], said
    alsa.inputs.remove(SUSHI_IN)
    alsa.links.clear()
    (said,) = ml.link_once(described, tmp_path, wait=0.05)
    assert "not linked" in said and alsa.links == [], said
    assert "could not say" in ml.link_once({"running": True, "config": "/nowhere"}, tmp_path)[0]
