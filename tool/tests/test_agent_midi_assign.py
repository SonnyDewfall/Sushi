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
    """A write that dies part way leaves the file as it was, never half of it."""
    from pathlib import Path

    from sushi_rig import rig, state

    monkeypatch.setattr(state, "state_path", lambda: tmp_path / "rig.json")
    target = tmp_path / "rig.json" if which == "state" else tmp_path / "rigs" / "lead.json"
    target.parent.mkdir(exist_ok=True)
    target.write_text('{"old": true}\n')
    real = Path.write_text

    def dies(self, text, *args, **kwargs):
        real(self, text[: len(text) // 2], *args, **kwargs)
        raise OSError("the disk filled")

    monkeypatch.setattr(Path, "write_text", dies)
    with pytest.raises(OSError):
        if which == "state":
            state.write_state({"new": True, "and": "more"})
        else:
            rig.write_compiled("lead", {"new": True, "and": "more"}, tmp_path)
    monkeypatch.setattr(Path, "write_text", real)
    assert json.loads(target.read_text()) == {"old": True}
    assert [p.name for p in target.parent.iterdir()] == [target.name]
