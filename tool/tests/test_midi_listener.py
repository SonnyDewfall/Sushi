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
import os
import queue
import threading
from pathlib import Path

import pytest

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
    ToggleBypass,
    ToggleMute,
    decode,
    dispatch,
    find_port,
    next_bypass,
    next_mute,
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
    # A CC on a mute is Sushi's own mapping (compile_midi), never the agent's:
    # one here means a config this listener does not understand.
    ([{"control": "knob3", "cc": 9, "channel": "all", "mute": "lead"}], "Sushi's own cc_mappings"),
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


@pytest.mark.parametrize("previous, expected", [(0.0, 1.0), (1.0, 0.0), (0.2, 1.0), (0.7, 0.0)])
def test_a_mute_toggle_flips_the_parameter_about_half_way(previous, expected):
    assert next_mute(previous) == expected


def test_a_mute_toggle_refuses_an_unknown_value():
    with pytest.raises(MidiError):
        next_mute(None)


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
- {name: lead, input: guitar-in, width: mono, chain: [{name: drive, uri: "urn:drive"}]}
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


def test_a_rig_to_switch_to_is_checked_to_compile_from_its_files(tmp_path):
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
    lead.chmod(0)
    try:
        if not os.access(lead, os.R_OK):  # root reads it anyway
            assert "PermissionError" in problem("lead")
    finally:
        lead.chmod(0o644)
