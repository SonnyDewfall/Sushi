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


# --------------------------------------------------------------------------- #
# PR #202's review
# --------------------------------------------------------------------------- #

BOTH = RIG_FILE + """\
midi:
  controller: fbv
  controls:
  - {control: c, bypass: board/overdrive}
  - {control: pedal, volume: board}
"""


def on_loop(s, coroutine):
    """Run `coroutine` on the served agent's loop and wait for it."""
    import asyncio

    return asyncio.run_coroutine_threadsafe(coroutine, s._loop).result(10)


def refused_restart(s):
    """A live change, then a restart the agent refuses for it: the lifecycle's
    `finally` re-syncs the listener with the rig still up."""
    assert ask(s, "POST", "/live/bypass", {"processor": "delay", "bypassed": True})[0] == 200
    status, answer = ask(s, "POST", "/rig/restart", {})
    assert status == 409 and answer["code"] == "unsaved", answer


def test_a_map_written_for_the_restart_does_not_turn_the_running_rigs_pedals_off():
    """The file loses its midi: (both controls taken off, for the restart):
    a re-sync before that restart still plans from what runs, so C still
    switches the drive and A and B still step (the reviewer's scenario)."""
    with Served(save=True, pedals=True, rig_file=BOTH) as s:
        listening(s)
        assert assign(s, control="pedal", none=True)[1]["how"] == "restart"
        assert assign(s, control="c", none=True)[1]["how"] == "restart"
        assert rig_midi(s) is None
        refused_restart(s)
        listening(s)
        health = midi_health(s)
        assert health["problem"] is None and health["feeds"][0]["required"] is True
        assert any("bypass board/overdrive" in b for b in health["bindings"]), health["bindings"]
        before = s.rig.bypassed["overdrive"]
        s.pedals.press("c")
        until(lambda: s.rig.bypassed["overdrive"] is (not before), what="C, as the running rig was compiled")


def test_a_map_gained_for_the_restart_does_not_make_the_running_rig_wait_for_its_pedal(served):
    """No map runs; one is written for the restart. Unplugged, the board is
    still only not connected after a re-sync: the running rig needs no pedal."""
    listening(served)
    assert assign(served, control="pedal", volume="vocal")[1]["how"] == "restart"
    assert rig_midi(served)["controller"] == "fbv"
    served.pedals.plugged = False
    until(lambda: midi_health(served)["feeds"][0]["state"] == "absent", what="absent")
    refused_restart(served)
    assert midi_health(served)["feeds"][0] == {**midi_health(served)["feeds"][0], "state": "absent", "required": False}
    assert midi_health(served)["state"] == "absent"


def test_a_switch_on_a_channel_the_file_has_and_the_rig_does_not_waits_for_the_restart(served):
    """A channel added to the file is not on the rig until it restarts: D on
    its mute is written for that restart, not served on nothing."""
    listening(served)
    status, answer = ask(served, "POST", "/rigs/electric_board/channels/add",
                         {"name": "lead", "input": "guitar-in", "width": "mono"})
    assert status == 200, answer
    status, answer = assign(served, control="d", mute="lead")
    assert status == 200, answer
    assert answer["how"] == "restart" and "not_running" in [r["code"] for r in answer["why"]], answer
    assert AGENT_BINDINGS not in served.save.config
    assert not any("mute lead" in b for b in midi_health(served)["bindings"])


def test_a_switch_on_a_plugin_taken_out_live_is_not_served_live(served):
    """Taken out through the agent and not saved: the file still has it, the
    rig does not. Not live; and the restart that would play it would drop the
    unsaved remove, so it waits for a Save, with nothing written."""
    listening(served)
    assert ask(served, "POST", "/live/remove", {"processor": "overdrive"})[0] == 200
    before = rig_text(served)
    status, answer = assign(served, control="c", bypass="board/overdrive")
    assert status == 409 and answer["code"] == "unsaved", answer
    assert rig_text(served) == before
    assert ask(served, "POST", "/rig/save", {})[0] == 200, "SAVE is not blocked by a map naming nothing"


def test_a_re_plan_while_the_heel_is_being_silenced_keeps_the_silence_it_lands():
    """The pedal at its heel: the agent's silencing is in flight (Sushi slow
    to answer) when the plan changes under it. The listener takes the new
    plan in place, so the silencing lands for the plan that runs, and the
    pedal's next rise has the agent put Sushi's value back."""
    import threading

    mapped = RIG_FILE + "midi:\n  controller: fbv\n  controls:\n  - {control: pedal, volume: vocal}\n"
    with Served(save=True, pedals=True, rig_file=mapped) as s:
        listening(s)
        gate, entered = threading.Event(), threading.Event()
        real = s.rig.set_parameter

        def slow(connection, processor, parameter, value):
            if processor == "vocal_send" and value == 0.0 and not gate.is_set():
                entered.set()
                gate.wait(10)
            return real(connection, processor, parameter, value)

        s.rig.set_parameter = slow
        s.pedals.sweep(0)
        until(entered.is_set, what="the silencing to reach Sushi")
        # The plan changes (C given the drive, as an assignment applied live
        # leaves the running config) while that call is out.
        config = json.loads(json.dumps(s.save.config))
        config[AGENT_BINDINGS] = config[AGENT_BINDINGS] + [
            {"control": "c", "cc": 22, "channel": 0, "bypass": "board/overdrive", "switch": "momentary"}]
        s.save.write_config("electric_board", config)
        on_loop(s, s.agent._midi_sync())
        assert any("bypass board/overdrive" in b for b in midi_health(s)["bindings"])
        gate.set()
        until(lambda: s.rig.info["vocal_send"]["gain"]["value"] == 0.0, what="the silence")
        s.pedals.sweep(100)
        until(lambda: any(c[:3] == ("set_parameter", "vocal_send", "gain") and c[3] > 0 for c in s.rig.calls),
              what="the agent to put Sushi's value back as the pedal rises")


def test_a_press_a_stopped_listener_heard_is_said_as_dropped():
    """A press waiting its turn when the listener is replaced is not acted on,
    and /health says so rather than naming it as done."""
    mapped = RIG_FILE + "midi:\n  controller: fbv\n  controls:\n  - {control: c, bypass: board/overdrive}\n"
    with Served(save=True, pedals=True, rig_file=mapped) as s:
        listening(s)
        on_loop(s, s.agent._midi_lock.acquire())
        s.pedals.press("c")
        until(lambda: (midi_health(s)["last"] or {}).get("message", "").startswith("cc 22"), what="the press")
        on_loop(s, s.agent._midi_stop("a test replacing it"))

        async def release():
            s.agent._midi_lock.release()

        on_loop(s, release())
        until(lambda: "dropped" in ((midi_health(s)["last_error"] or {}).get("message") or ""), what="said")
        assert s.rig.bypassed["overdrive"] is False


def test_nothing_to_change_is_not_written_and_not_a_question(served):
    """`none` for a control the map does not have, or the same assignment
    again, writes nothing and says so; a control no device has is refused.
    With two devices and no map, a no-op is not answered with `which`."""
    listening(served)
    path = served.save.root / "config" / "rigs" / f"{RIG}.yaml"
    stamp = path.stat().st_mtime_ns
    status, answer = assign(served, control="d", none=True)
    assert status == 200 and answer["how"] == "unchanged", answer
    assert path.stat().st_mtime_ns == stamp and rig_midi(served) is None
    status, answer = assign(served, control="zz", none=True)
    assert status == 409 and answer["code"] == "no_control", answer
    assert assign(served, control="c", bypass="board/overdrive")[1]["how"] == "live"
    stamp = path.stat().st_mtime_ns
    status, answer = assign(served, control="c", bypass="board/overdrive")
    assert status == 200 and answer["how"] == "unchanged" and path.stat().st_mtime_ns == stamp, answer
    assert assign(served, control="c", none=True)[1]["how"] == "live"
    served.save.machine.write_text(MACHINE_FILE + FBV_CONTROLLERS + FBV_CONTROLLERS.split("\n", 1)[1]
                                   .replace("  fbv:", "  fbv2:").replace("FBV Express Mk II MIDI 1", "MIDI 2"))
    status, answer = assign(served, control="c", none=True)
    assert status == 200 and answer["how"] == "unchanged", answer


def test_a_write_that_outlasts_the_answer_is_still_followed_by_the_listener(served, monkeypatch):
    """The running config's write is slow past the deadline: the answer is a
    504, and once the write is over the listener serves what it wrote."""
    import threading

    from sushi_rig import agent as agent_module

    listening(served)
    monkeypatch.setattr(agent_module, "SAVE_TIMEOUT", 0.5)
    gate = threading.Event()
    real = served.save.write_config

    def slow(name, compiled):
        gate.wait(10)
        return real(name, compiled)

    served.save.write_config = slow
    served.agent.switching = served.save.switching()
    status, answer = assign(served, control="c", bypass="board/overdrive")
    assert status == 504, answer
    assert not any("bypass" in b for b in midi_health(served)["bindings"])
    gate.set()
    until(lambda: any("bypass board/overdrive" in b for b in midi_health(served)["bindings"]),
          what="the listener to follow the late write")


@pytest.mark.parametrize("which", ["state", "compiled"])
def test_the_running_config_and_the_state_file_are_written_whole_or_not_at_all(tmp_path, monkeypatch, which):
    """A write that dies part way (the disk full as it is flushed) leaves the
    file as it was, never half of it, and no scratch file beside it."""
    import os

    from sushi_rig import rig, state

    monkeypatch.setattr(state, "state_path", lambda: tmp_path / "rig.json")
    target = tmp_path / "rig.json" if which == "state" else tmp_path / "rigs" / "lead.json"
    target.parent.mkdir(exist_ok=True)
    target.write_text('{"old": true}\n')

    def dies(fd):
        raise OSError("the disk filled")

    monkeypatch.setattr(os, "fsync", dies)
    with pytest.raises(OSError):
        if which == "state":
            state.write_state({"new": True, "and": "more"})
        else:
            rig.write_compiled("lead", {"new": True, "and": "more"}, tmp_path)
    monkeypatch.undo()
    assert json.loads(target.read_text()) == {"old": True}
    assert [p.name for p in target.parent.iterdir()] == [target.name]


def test_two_writers_at_once_each_replace_the_file_whole(tmp_path):
    """The supervisor's threads and an assignment's write the state file
    and the running config: two at once each land whole, with no scratch
    file shared between them, and the file keeps its permissions."""
    import os
    import threading

    from sushi_rig.state import write_atomically

    target = tmp_path / "rig.json"
    target.write_text("{}\n")
    os.chmod(target, 0o640)
    failures = []

    def writer(tag):
        try:
            for i in range(300):
                write_atomically(target, json.dumps({"writer": tag, "i": i, "pad": "x" * 2000}) + "\n")
        except Exception as exc:  # noqa: BLE001 -- collected, then asserted
            failures.append(exc)

    threads = [threading.Thread(target=writer, args=(tag,)) for tag in ("a", "b")]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert failures == []
    assert json.loads(target.read_text())["i"] == 299
    assert [p.name for p in tmp_path.iterdir()] == ["rig.json"]
    assert target.stat().st_mode & 0o777 == 0o640


# --------------------------------------------------------------------------- #
# PR #202's re-review
# --------------------------------------------------------------------------- #

@pytest.fixture
def real_compile(monkeypatch):
    """A save-as's compile the real one, as `up --rig` compiles (the fake's
    stands in a config of no tracks)."""
    from pathlib import Path

    import ui_fake
    from sushi_rig.rig import compile_named_rig

    def compile_(self, name, root, machine):
        self.compiled.append(name)
        return compile_named_rig(name, Path(root), Path(machine) if machine else None)

    monkeypatch.setattr(ui_fake.UiSave, "compile", compile_)


def test_a_save_as_keeps_the_running_config_when_its_file_waits_for_a_restart(real_compile):
    """The pedal assigned to a volume for the restart, then Save as: the
    run, renamed, still says what Sushi loaded (no cc_mappings, no MIDI
    input, no controller) and the listener does not silence the channel at
    the heel with no mapping to bring it back; the answer says a restart is
    needed."""
    with Served(save=True, pedals=True) as s:
        listening(s)
        before = json.loads(json.dumps(s.save.config))
        assert assign(s, control="pedal", volume="board")[1]["how"] == "restart"
        status, answer = ask(s, "POST", "/rig/save", {"name": "foo"})
        assert status == 200 and answer["ok"], answer
        assert "midi" in [r["code"] for r in answer["restart"]["why"]], answer
        config = s.save.config
        assert config.get("midi") == before.get("midi") and config["host_config"] == before["host_config"]
        assert "midi_controller" not in config["_meta"] and config["_meta"]["source"] == "config/rigs/foo.yaml"
        listening(s)
        assert midi_health(s)["rig"] == "foo"
        assert not any("silence" in b for b in midi_health(s)["bindings"]), midi_health(s)["bindings"]


def test_a_save_as_keeps_the_running_tracks_when_a_channel_waits_for_a_restart(real_compile):
    with Served(save=True, pedals=True) as s:
        listening(s)
        tracks = [t["name"] for t in s.save.config["tracks"]]
        status, answer = ask(s, "POST", "/rigs/electric_board/channels/add",
                             {"name": "lead", "input": "guitar-in", "width": "mono"})
        assert status == 200, answer
        status, answer = ask(s, "POST", "/rig/save", {"name": "foo"})
        assert status == 200 and answer["ok"], answer
        assert "track_added" in [r["code"] for r in answer["restart"]["why"]], answer
        assert [t["name"] for t in s.save.config["tracks"]] == tracks


def test_a_save_as_with_nothing_waiting_records_the_new_compile(real_compile):
    """And with nothing for a restart, the run is the new file's compile, as
    before: no restart said."""
    with Served(save=True, pedals=True) as s:
        listening(s)
        status, answer = ask(s, "POST", "/rig/save", {"name": "foo"})
        assert status == 200 and answer["ok"] and "restart" not in answer, answer
        assert s.save.config["_meta"]["source"] == "config/rigs/foo.yaml"


def test_a_late_write_after_another_command_writes_nothing(served, monkeypatch):
    """The assignment's write outlasts its answer (504); a restart then makes
    another run. When the write gets going again it finds the run changed,
    writes nothing (the file, the running config), and the listener does
    not follow it."""
    import threading

    from sushi_rig import agent as agent_module

    listening(served)
    monkeypatch.setattr(agent_module, "SAVE_TIMEOUT", 0.5)
    gate = threading.Event()
    real = served.agent._rigfile_compile

    def slow(raw, run):
        gate.wait(10)
        return real(raw, run)

    served.agent._rigfile_compile = slow
    before = rig_text(served)
    status, answer = assign(served, control="c", bypass="board/overdrive")
    assert status == 504, answer
    assert ask(served, "POST", "/rig/restart", {})[0] == 200
    listening(served)
    started = served.save.state["started_at"]
    gate.set()
    until(lambda: not served.agent._midi_tasks, what="the late write to be over")
    assert rig_text(served) == before
    assert AGENT_BINDINGS not in served.save.config and served.save.state["started_at"] == started
    assert not any("bypass" in b for b in midi_health(served)["bindings"])


def test_following_a_late_write_says_what_holds_the_agent(served, monkeypatch):
    """While the listener follows a late write it holds the lifecycle lock,
    and a refusal meanwhile names it rather than "None"."""
    import threading

    from sushi_rig import agent as agent_module

    listening(served)
    monkeypatch.setattr(agent_module, "SAVE_TIMEOUT", 0.5)
    gate, syncing, release = threading.Event(), threading.Event(), threading.Event()
    real_write = served.save.write_config

    def slow_write(name, compiled):
        gate.wait(10)
        return real_write(name, compiled)

    served.save.write_config = slow_write
    served.agent.switching = served.save.switching()
    real_sync = served.agent._midi_sync

    async def slow_sync():
        import asyncio

        syncing.set()
        while not release.is_set():
            await asyncio.sleep(0.01)
        await real_sync()

    served.agent._midi_sync = slow_sync
    assert assign(served, control="c", bypass="board/overdrive")[0] == 504
    gate.set()
    until(syncing.is_set, what="the listener to follow")
    status, answer = ask(served, "POST", "/rig/restart", {})
    release.set()
    assert status == 409 and answer["reason"] == "busy: 'MIDI' is still running", answer


def test_a_heel_silencing_refused_as_busy_lands_once_the_agent_is_free():
    """The pedal reaches its heel while an assignment holds the agent: the
    silencing is refused as busy, waits, and lands when the assignment is
    done, from where the pedal is then."""
    import threading

    mapped = RIG_FILE + "midi:\n  controller: fbv\n  controls:\n  - {control: pedal, volume: vocal}\n"
    with Served(save=True, pedals=True, rig_file=mapped) as s:
        listening(s)
        gate, compiling = threading.Event(), threading.Event()
        real = s.agent._rigfile_compile

        def slow(raw, run):
            compiling.set()
            gate.wait(10)
            return real(raw, run)

        s.agent._rigfile_compile = slow
        answer = {}
        t = threading.Thread(target=lambda: answer.update(r=assign(s, control="c", bypass="board/overdrive")))
        t.start()
        until(compiling.is_set, what="the assignment to hold the agent")
        s.pedals.sweep(0)
        until(lambda: s.agent._midi_snapping, what="the silencing to start")
        assert s.rig.info["vocal_send"]["gain"]["value"] != 0.0
        gate.set()
        t.join(10)
        assert answer["r"][0] == 200, answer
        until(lambda: s.rig.info["vocal_send"]["gain"]["value"] == 0.0, what="the silence, once free")
        assert "busy" not in ((midi_health(s)["last_error"] or {}).get("message") or "")


# --------------------------------------------------------------------------- #
# PR #202's fourth review
# --------------------------------------------------------------------------- #

def test_a_late_write_does_not_point_a_new_run_at_this_rigs_config(served, monkeypatch):
    """The narrow gap: the run changes after the write's first check, while
    the running config is being written. The state file of the run that is
    up now is not pointed at that config."""
    import threading

    from sushi_rig import agent as agent_module

    listening(served)
    monkeypatch.setattr(agent_module, "SAVE_TIMEOUT", 0.5)
    gate, entered = threading.Event(), threading.Event()
    real = served.save.write_config
    first = []

    def slow(name, compiled):
        if first:
            return real(name, compiled)
        first.append(1)
        entered.set()
        gate.wait(10)
        real(name, compiled)
        return "/elsewhere/electric_board.json"  # another path, which a repointing would write

    served.save.write_config = slow
    served.agent.switching = served.save.switching()
    assert assign(served, control="c", bypass="board/overdrive")[0] == 504
    until(entered.is_set, what="the write to be under way")
    assert ask(served, "POST", "/rig/restart", {})[0] == 200
    after = dict(served.save.state)
    gate.set()
    until(lambda: not served.agent._midi_tasks, what="the late write to be over")
    assert served.save.state["config"] == after["config"] != "/elsewhere/electric_board.json"


def test_a_heel_silencing_waiting_on_a_busy_agent_stops_if_its_binding_goes():
    """The silencing waits for the agent; meanwhile the plan swaps to one
    where the pedal no longer rides that volume. It does not land."""
    mapped = RIG_FILE + "midi:\n  controller: fbv\n  controls:\n  - {control: pedal, volume: vocal}\n"
    with Served(save=True, pedals=True, rig_file=mapped) as s:
        listening(s)
        on_loop(s, s.agent._lock.acquire())  # the agent held, as a lifecycle command holds it
        s.pedals.sweep(0)
        until(lambda: s.agent._midi_snapping, what="the silencing to wait")
        config = json.loads(json.dumps(s.save.config))
        config.pop(AGENT_BINDINGS)
        s.save.write_config("electric_board", config)
        on_loop(s, s.agent._midi_sync())
        assert not any("silence" in b for b in midi_health(s)["bindings"])

        async def release():
            s.agent._lock.release()

        on_loop(s, release())
        until(lambda: not s.agent._midi_snapping, what="the silencing to end")
        assert not any(c[:3] == ("set_parameter", "vocal_send", "gain") for c in s.rig.calls)


# --------------------------------------------------------------------------- #
# #210: each pedal linked to Sushi's MIDI input, said in /health
# --------------------------------------------------------------------------- #

def opened_links(s):
    return [port.link for port in s.pedals.ports]


def test_the_pedal_is_linked_to_sushi_at_the_start_and_after_a_restart(served):
    """Every listener the agent starts asks its port to link the pedal to
    Sushi's input 0 (the rig has no map: where an assignment would go), and
    /health says it is linked, per feed, with why."""
    listening(served)
    until(lambda: midi_health(served)["feeds"][0]["linked_to_sushi"] is True, what="the link")
    feed = midi_health(served)["feeds"][0]
    assert feed["link"].startswith("linked by the agent"), feed
    assert opened_links(served) == [0]
    assert ask(served, "POST", "/rig/restart", {})[0] == 200  # a new Sushi
    listening(served)
    assert opened_links(served) == [0, 0], "the restarted rig's listener links again"
    until(lambda: midi_health(served)["feeds"][0]["linked_to_sushi"] is True, what="the link")
    # A switch's own stop and start of the listener: test_agent_midi.py.


def test_a_pedal_that_cannot_be_linked_says_not_linked_and_why(served):
    listening(served)
    served.pedals.sushi = False  # Sushi's input gone from the sequencer's list
    until(lambda: midi_health(served)["feeds"][0]["linked_to_sushi"] is False, what="unlinked")
    feed = midi_health(served)["feeds"][0]
    assert feed["linked_to_sushi"] is False and "no ALSA MIDI input called 'Sushi'" in feed["link"], feed
    served.pedals.sushi = True  # Sushi's input appears: the port says so on its next read
    until(lambda: midi_health(served)["feeds"][0]["linked_to_sushi"] is True, what="the link")
    served.pedals.plugged = False  # hot-unplugged: the link goes with the board
    until(lambda: midi_health(served)["feeds"][0]["linked_to_sushi"] is False, what="unlinked")
    assert "not plugged in" in midi_health(served)["feeds"][0]["link"]
    served.pedals.plugged = True  # hot-plugged: linked again
    until(lambda: midi_health(served)["feeds"][0]["linked_to_sushi"] is True, what="relinked")
