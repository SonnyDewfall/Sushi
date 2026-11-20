"""Assigning a pedal from the rack (#192): POST /rig/midi/assign, and the
"heard" counts in /health's `agent.midi.seen`.

Against a real agent served on a port (`ui_fake.Served(save=True,
pedals=True)`): a checkout in a temporary folder holding `ui_fake.RIG_FILE`
(the rig the fake Sushi holds, with comments a write must keep) and a machine
file with the FBV Express MkII (`ui_fake.FBV_CONTROLLERS`), the real compiler
(`rig.compile_rig_draft`) before every write, the real `rigfile.write_rig`,
and the agent's real MIDI listener and `plan_for` over fake ports, into which
a test sends what a footswitch would.

What stands in for what: Sushi is `ui_fake.UiRig`; the running config is
the real compile of the rig file as it was when the fake rig "came up", and
a restart (`UiLifecycle`) compiles the file again. Nothing here is a board,
ALSA, or a running Sushi: that the FBV sends what its entry says is
docs/reference/midi.md's list of what is unverified.
"""

from __future__ import annotations

import json
import re
import time
import urllib.request

import pytest
import yaml

pytest.importorskip("aiohttp")

from sushi_rig import agent_client  # noqa: E402
from sushi_rig.rigfile import AGENT_BINDINGS  # noqa: E402

from ui_fake import FBV_CONTROLLERS, MACHINE_FILE, RIG_FILE, Served  # noqa: E402

RIG = "electric_board"

# A map already written, each entry with a comment of its own, for the
# writes that must keep them with their entries.
MAPPED = RIG_FILE + """\
midi:
  controller: fbv   # the board on the floor
  controls:
  - {control: c, bypass: board/overdrive}   # C: the drive
  - {control: d, mute: vocal}               # D: the vocal
  - {control: pedal, parameter: board/delay/FX Mix}   # the pedal: the delay's mix
"""


@pytest.fixture
def served():
    with Served(save=True, pedals=True) as s:
        yield s


@pytest.fixture
def mapped():
    with Served(save=True, pedals=True, rig_file=MAPPED) as s:
        yield s


def until(condition, timeout=5.0, what="the condition"):
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > deadline:
            raise AssertionError(f"timed out waiting for {what}")
        time.sleep(0.01)


def ask(s, method, path, body=None):
    return agent_client.ask(s.url, method, path, body)


def assign(s, **body):
    return ask(s, "POST", "/rig/midi/assign", body)


def rig_text(s):
    return (s.save.root / "config" / "rigs" / f"{RIG}.yaml").read_text()


def rig_midi(s):
    return yaml.safe_load(rig_text(s)).get("midi")


def midi_health(s):
    return ask(s, "GET", "/health")[1]["agent"]["midi"]


def listening(s):
    until(lambda: midi_health(s)["state"] == "listening", what="the listener")


# --------------------------------------------------------------------------- #
# Assigning, written as the rack's other edits write the file
# --------------------------------------------------------------------------- #

def test_a_switch_on_an_effect_is_written_with_the_controller_and_plays_now(served):
    """No midi: in the file, one typed device in the machine file: the map is
    written for it, the file keeps its comments, and since a bypass is the
    agent's alone the running config takes the binding and the listener is
    re-planned, so a press of C toggles the drive with no restart."""
    listening(served)
    before = rig_text(served)
    status, body = assign(served, control="c", bypass="board/overdrive")
    assert status == 200, body
    assert body["how"] == "live" and body["takes_effect"] == "now" and body["offer"] is None
    assert body["change"]["controller_set"] is True and body["change"]["was"] is None
    assert body["assignments"] == {"controller": "fbv", "controls": [{"control": "c", "bypass": "board/overdrive"}]}
    after = rig_text(served)
    assert after.startswith(before), "the file changes by its new midi: lines and nothing else"
    assert "# the drive: a comment the save keeps" in after and after.startswith("# The fake rig")
    assert rig_midi(served) == {"controller": "fbv", "controls": [{"control": "c", "bypass": "board/overdrive"}]}
    assert served.save.config[AGENT_BINDINGS] == [
        {"control": "c", "cc": 22, "channel": 0, "bypass": "board/overdrive", "switch": "momentary"}]
    assert served.lifecycle.calls == [], "nothing restarted"
    listening(served)
    assert any("bypass board/overdrive" in b for b in midi_health(served)["bindings"]), midi_health(served)
    assert served.rig.bypassed["overdrive"] is False
    served.pedals.press("c")
    until(lambda: served.rig.bypassed["overdrive"] is True, what="C to bypass the drive")
    (entry,) = ask(served, "GET", "/live/log")[1]["log"]
    assert entry["source"] == "midi" and entry["control"] == "c" and entry["op"] == "bypass"


def test_without_the_re_plan_the_listener_would_still_serve_the_old_bindings(served, monkeypatch):
    """The re-plan is what makes it live: with `_midi_sync` stood down, the
    same write leaves C doing nothing. (Shows the test above is about the
    listener, not about the file.)"""
    listening(served)
    calls = []

    async def no_sync():
        calls.append(1)

    monkeypatch.setattr(served.agent, "_midi_sync", no_sync)
    assert assign(served, control="c", bypass="board/overdrive")[0] == 200
    assert calls == [1]
    assert not any("bypass" in b for b in midi_health(served)["bindings"])


def test_a_pedal_on_a_volume_is_written_and_plays_after_a_restart(served):
    """A volume is a cc_mapping, which Sushi loads only with the config: the
    file is written, the running config and the listener are left as they
    are, and the answer offers the restart with the compiler's reason."""
    listening(served)
    running_before = json.loads(json.dumps(served.save.config))
    bindings_before = midi_health(served)["bindings"]
    status, body = assign(served, control="pedal", volume="vocal")
    assert status == 200, body
    assert body["how"] == "restart" and body["offer"] == "restart" and body["takes_effect"] == "restart"
    # The cc_mappings, and the MIDI input Sushi opens for them (a rig with
    # no map had none): both loaded only when Sushi starts.
    assert [r["code"] for r in body["why"]] == ["host", "midi"], body["why"]
    assert "plays when electric_board restarts" in body["note"]
    assert rig_midi(served) == {"controller": "fbv", "controls": [{"control": "pedal", "volume": "vocal"}]}
    assert served.save.config == running_before, "the running config still says what Sushi loaded"
    assert midi_health(served)["bindings"] == bindings_before
    assert ask(served, "POST", "/rig/restart")[0] == 200
    listening(served)
    assert any("silence vocal" in b for b in midi_health(served)["bindings"]), "the restart brought it in"


def test_assigning_a_control_in_use_moves_it_and_says_from_what(mapped):
    """C was the drive's; given the vocal's mute it leaves the drive, keeps
    its place and its comment in the map, and the answer names what it was
    taken from. D was on the vocal's mute, so it is taken off it: a target
    holds one control of a binding."""
    listening(mapped)
    status, body = assign(mapped, control="c", mute="vocal")
    assert status == 200, body
    assert body["change"]["moved"] == {"control": "c", "from": {"bypass": "board/overdrive"},
                                       "said": "bypass board/overdrive"}
    assert body["change"]["replaced"] == [{"control": "d", "mute": "vocal"}]
    assert rig_midi(mapped)["controls"] == [{"control": "c", "mute": "vocal"},
                                            {"control": "pedal", "parameter": "board/delay/FX Mix"}]
    text = rig_text(mapped)
    assert re.search(r"- \{control: c, mute: vocal\} +# C: the drive\n", text), text
    assert "# D: the vocal" not in text, "D's comment went with D's entry"
    assert "# the pedal: the delay's mix" in text and "# the board on the floor" in text


def test_none_takes_a_control_off_and_the_others_keep_their_comments(mapped):
    """D, in the middle of the map, is taken off: C and the pedal keep their
    own comments (the map's list is matched by `control`, not by position)."""
    listening(mapped)
    status, body = assign(mapped, control="d", none=True)
    assert status == 200, body
    assert body["change"]["was"] == {"mute": "vocal"}
    text = rig_text(mapped)
    assert re.search(r"- \{control: c, bypass: board/overdrive\} +# C: the drive\n", text), text
    assert re.search(r"- \{control: pedal, parameter: board/delay/FX Mix\} +# the pedal: the delay's mix\n", text)
    assert "# D: the vocal" not in text and "control: d" not in text


def test_taking_off_the_last_control_takes_the_map_out(served):
    listening(served)
    assert assign(served, control="c", bypass="board/overdrive")[0] == 200
    status, body = assign(served, control="c", none=True)
    assert status == 200, body
    assert body["assignments"] is None and rig_midi(served) is None
    assert AGENT_BINDINGS not in served.save.config
    assert body["how"] == "live"


def test_a_parameter_keeps_its_range_only_for_the_same_parameter(served):
    raw = yaml.safe_load(rig_text(served))
    raw["midi"] = {"controller": "fbv", "controls": [
        {"control": "pedal", "parameter": "board/delay/FX Mix", "range": [0.2, 0.6]}]}
    from sushi_rig.rigedit import with_assignment

    same, _ = with_assignment(raw, "fbv", {"control": "pedal", "binding": "parameter",
                                           "target": "board/delay/FX Mix", "controller": None})
    other, said = with_assignment(raw, "fbv", {"control": "pedal", "binding": "parameter",
                                               "target": "board/delay/Feedback", "controller": None})
    assert same["midi"]["controls"] == [{"control": "pedal", "parameter": "board/delay/FX Mix", "range": [0.2, 0.6]}]
    assert other["midi"]["controls"] == [{"control": "pedal", "parameter": "board/delay/Feedback"}]
    assert said["moved"]["said"] == "parameter board/delay/FX Mix"


# --------------------------------------------------------------------------- #
# Refused, with the compiler's words, and nothing written
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("body, code, said", [
    ({"control": "pedal", "bypass": "board/overdrive"}, "rig_problem",
     "'pedal' is an expression pedal, which can take volume or parameter or blend, not a bypass"),
    ({"control": "c", "volume": "vocal"}, "rig_problem", "'c' is a switch, which can take bypass or mute, not a volume"),
    ({"control": "a", "bypass": "board/overdrive"}, "rig_problem",
     "'a' is fbv's previous-rig on every rig (its job in the machine file), so a rig cannot assign it"),
    ({"control": "e", "bypass": "board/overdrive"}, "rig_problem", "'e' is not a control of controller 'fbv'"),
    ({"control": "c", "bypass": "board/chorus"}, "rig_problem", "'chorus', which is not in board's chain"),
    ({"control": "pedal", "volume": "master"}, "rig_problem", "a pedal cannot drive the master's volume"),
    ({"control": "c", "bypass": "board/overdrive", "controller": "mpd218"}, "controller",
     "no controller called 'mpd218'"),
])
def test_what_the_compiler_refuses_is_refused_with_its_words_and_nothing_written(served, body, code, said):
    before = rig_text(served)
    status, answer = assign(served, **body)
    assert status == 409, answer
    assert answer["code"] == code and said in answer["reason"], answer["reason"]
    assert rig_text(served) == before


def test_a_second_driver_of_a_volume_is_refused(mapped):
    """The pedal on the vocal's volume while a blend of board and vocal
    drives it: two drivers of one send, the compiler's refusal."""
    raw = yaml.safe_load(rig_text(mapped))
    raw["midi"]["controls"].append({"control": "d", "blend": ["board", "vocal"]})
    raw["midi"]["controls"] = [c for c in raw["midi"]["controls"] if c["control"] != "d" or "blend" in c]
    path = mapped.save.root / "config" / "rigs" / f"{RIG}.yaml"
    from sushi_rig.rigfile import write_rig

    write_rig(path, raw)
    before = rig_text(mapped)
    status, answer = assign(mapped, control="pedal", volume="vocal")
    assert status == 409 and "both drive vocal's volume" in answer["reason"], answer
    assert rig_text(mapped) == before


@pytest.mark.parametrize("body", [
    {"control": "c"}, {"control": "c", "bypass": "a/b", "mute": "vocal"}, {"control": "c", "none": False},
    {"control": "c", "none": True, "mute": "vocal"}, {"control": "", "mute": "vocal"},
    {"control": "c", "blend": "board"}, {"control": "c", "mute": "vocal", "learn": True}, ["c"],
])
def test_a_body_of_the_wrong_shape_is_400(served, body):
    before = rig_text(served)
    status, answer = ask(served, "POST", "/rig/midi/assign", body)
    assert status == 400, answer
    assert rig_text(served) == before


def test_several_typed_devices_and_no_controller_in_the_map_is_a_question(served):
    """Two pedals described, a rig whose map names neither: the answer asks
    which, with their names; named, it is written for that one."""
    served.save.machine.write_text(MACHINE_FILE + FBV_CONTROLLERS + FBV_CONTROLLERS.split("\n", 1)[1]
                                   .replace("  fbv:", "  fbv2:").replace("FBV Express Mk II MIDI 1", "MIDI 2"))
    before = rig_text(served)
    status, answer = assign(served, control="c", bypass="board/overdrive")
    assert status == 409 and answer["code"] == "which_controller", answer
    assert answer["controllers"] == ["fbv", "fbv2"]
    assert rig_text(served) == before
    status, answer = assign(served, control="c", bypass="board/overdrive", controller="fbv2")
    assert status == 200, answer
    assert rig_midi(served)["controller"] == "fbv2"
    status, answer = assign(served, control="d", mute="vocal", controller="fbv")
    assert status == 409 and answer["code"] == "controller" and "written for fbv2" in answer["reason"]


def test_a_change_for_the_restart_waits_for_unsaved_work_and_a_live_one_does_not(served):
    """A restart would drop knob turns not saved, so an assignment that plays
    only after one is refused until they are (`rigfile_refusal`); one that
    plays now loses nothing and is written."""
    listening(served)
    assert ask(served, "POST", "/live/parameter", {"processor": "overdrive", "parameter": "Drive", "value": 0.4})[0] == 200
    before = rig_text(served)
    status, answer = assign(served, control="pedal", volume="vocal")
    assert status == 409 and answer["code"] == "unsaved", answer
    assert rig_text(served) == before
    status, answer = assign(served, control="c", bypass="board/overdrive")
    assert status == 200 and answer["how"] == "live", answer


def test_with_no_rig_up_there_is_nothing_to_assign_on():
    from ui_fake import UiLifecycle

    with Served(save=True, pedals=True, lifecycle=UiLifecycle(up="electric_board")) as s:
        s.lifecycle.rig = None
        status, answer = assign(s, control="c", bypass="board/overdrive")
        assert status == 409 and answer["code"] == "no_rig", answer


def test_another_sites_page_cannot_assign(served):
    """Behind the one guard (#161): refused before the body is read."""
    before = rig_text(served)
    request = urllib.request.Request(
        served.url + "/rig/midi/assign", data=json.dumps({"control": "c", "bypass": "board/overdrive"}).encode(),
        method="POST", headers={"Content-Type": "application/json", "Origin": "https://example.invalid"})
    try:
        urllib.request.urlopen(request, timeout=5)
        raise AssertionError("not refused")
    except urllib.error.HTTPError as exc:
        assert exc.code == 403 and json.loads(exc.read())["code"] == "cross_site"
    assert rig_text(served) == before


# --------------------------------------------------------------------------- #
# Heard: the last message from each control
# --------------------------------------------------------------------------- #

def test_each_control_heard_is_counted_bound_or_not_with_its_value(served):
    """D is bound to nothing and the pedal neither, on a rig with no map:
    each is still counted, by what the machine file says it sends, and the
    pedal's value is its last."""
    listening(served)
    served.pedals.press("d")
    served.pedals.sweep(40)
    served.pedals.sweep(90)
    until(lambda: midi_health(served)["seen"].get("fbv", {}).get("pedal", {}).get("count") == 2, what="the pedal")
    seen = midi_health(served)["seen"]["fbv"]
    assert seen["d"]["count"] == 2 and seen["d"]["value"] == 0, "a press and its release"
    assert seen["pedal"]["value"] == 90 and seen["pedal"]["ago"] < 5
    assert set(seen) == {"d", "pedal"}
    served.pedals.send(0xB1, 7, 30)  # channel 1: not the board's
    served.pedals.send(0xB0, 99, 1)  # a CC no control sends
    served.pedals.press("a")
    until(lambda: "a" in midi_health(served)["seen"]["fbv"], what="A")
    assert midi_health(served)["seen"]["fbv"]["pedal"]["count"] == 2


def test_the_device_states_are_the_feeds(served):
    """Not connected when unplugged and the rig's map does not need it; once
    the map is on it, waiting (a problem) until it is plugged in."""
    listening(served)
    served.pedals.plugged = False
    until(lambda: midi_health(served)["feeds"][0]["state"] == "absent", what="absent")
    assert midi_health(served)["state"] == "absent"
    assert assign(served, control="c", bypass="board/overdrive")[0] == 200
    until(lambda: midi_health(served)["feeds"][0]["state"] == "waiting", what="waiting")
    assert midi_health(served)["state"] == "failed" and midi_health(served)["feeds"][0]["required"] is True
    served.pedals.plugged = True
    listening(served)
