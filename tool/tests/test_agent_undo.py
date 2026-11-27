"""One Undo for everything (#209): GET /undo and POST /undo, and history.py.

Against the real agent over the web UI's fake (`ui_fake.Served`): `UiRig`
for Sushi, `UiSave`'s checkout for the rig files, the bin and the saves, its
machine file with the FBV for a pedal's assignment. Each kind of action the
history records is done through its own route and undone through POST /undo,
and what was put back is read where it lives: Sushi's value, the rig file's
bytes, the bin, the agent's pedal bindings.

NOT shown: any of it against a live Sushi (the fake answers as `agent.Live`
expects); a revert's or a channel load's switch is `UiLifecycle`'s stand-in.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

import pytest

pytest.importorskip("aiohttp")

from sushi_rig import history as history_module  # noqa: E402

from ui_fake import Served  # noqa: E402


def call(served, method, path, body=None):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(served.url + path, data=data, method=method,
                                 headers={"content-type": "application/json"} if data else {})
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def undo(served):
    return call(served, "POST", "/undo", {})


def value(served, processor, parameter):
    return served.rig.info[processor][parameter]["value"]


@pytest.fixture
def served():
    with Served(save=True, pedals=True) as s:
        yield s


def rig_file(served, name="electric_board"):
    return served.save.root / "config" / "rigs" / f"{name}.yaml"


# ------------------------------------------------------------- history.py, pure

def test_entries_go_oldest_first_and_the_depth_is_the_limit():
    clock = iter(range(0, 1000, 10))
    h = history_module.History(limit=50, clock=lambda: next(clock))
    for n in range(60):
        h.push("file", f"step {n}")
    assert len(h.entries) == 50
    assert [e["label"] for e in h.entries[:2]] == ["step 10", "step 11"]
    assert h.newest()["label"] == "step 59"


def test_a_drag_of_one_control_is_one_step_and_another_control_is_another():
    now = [0.0]
    h = history_module.History(clock=lambda: now[0])

    def knob(index, parameter, at):
        now[0] = at
        return h.push_live(index, {"op": "parameter", "args": {"processor": "overdrive", "parameter": parameter}})

    knob(0, "Drive", 0.0)
    knob(1, "Drive", 0.5)
    knob(2, "Drive", 1.0)
    knob(3, "Tone", 1.2)
    knob(4, "Drive", 10.0)  # the same control, long after: a step of its own
    assert [(e["label"], e["indices"]) for e in h.entries] == [
        ("overdrive Drive", [0, 1, 2]), ("overdrive Tone", [3]), ("overdrive Drive", [4])]


def test_a_switch_drops_the_live_steps_and_keeps_the_files():
    h = history_module.History(clock=lambda: 0.0)
    h.push_live(0, {"op": "bypass", "args": {"processor": "delay", "bypassed": True}})
    h.push("file", "add channel keys")
    h.push_live(1, {"op": "remove", "args": {"processor": "octave"}})
    h.drop_live()
    assert [e["label"] for e in h.entries] == ["add channel keys"]


def test_only_changes_are_steps():
    h = history_module.History(clock=lambda: 0.0)
    for op in ("save", "rig", "record", "undo", "stale"):
        assert h.push_live(0, {"op": op, "args": {}}) is None
    assert h.entries == []


# ------------------------------------------------------------- the routes

def test_nothing_to_undo_is_said_and_get_says_so(served):
    status, body = call(served, "GET", "/undo")
    assert status == 200 and body["next"] is None and body["limit"] == history_module.LIMIT
    status, body = undo(served)
    assert status == 409 and body["code"] == "nothing_to_undo"


def test_a_knob_is_undone(served):
    before = value(served, "overdrive", "Drive")
    assert call(served, "POST", "/live/parameter",
                {"processor": "overdrive", "parameter": "Drive", "value": 0.9})[0] == 200
    assert call(served, "GET", "/undo")[1]["next"]["label"] == "overdrive Drive"
    status, body = undo(served)
    assert status == 200, body
    assert body["said"] == "Undone: overdrive Drive" and body["how"] == "live"
    assert value(served, "overdrive", "Drive") == pytest.approx(before)
    assert body["next"] is None


def test_a_drag_is_undone_in_one_press(served):
    before = value(served, "overdrive", "Drive")
    for v in (0.7, 0.75, 0.8, 0.85):
        call(served, "POST", "/live/parameter", {"processor": "overdrive", "parameter": "Drive", "value": v})
    status, body = undo(served)
    assert status == 200, body
    assert value(served, "overdrive", "Drive") == pytest.approx(before)
    assert body["next"] is None


def test_a_bypass_is_undone(served):
    assert served.rig.bypassed["delay"] is False
    call(served, "POST", "/live/bypass", {"processor": "delay", "bypassed": True})
    status, body = undo(served)
    assert status == 200 and body["said"] == "Undone: bypass delay"
    assert served.rig.bypassed["delay"] is False


def test_a_plugin_removal_is_undone(served):
    status, body = call(served, "POST", "/live/remove", {"processor": "octave"})
    assert status == 200, body
    assert "octave" not in served.rig.chains["board"]
    status, body = undo(served)
    assert status == 200, body
    assert body["said"] == "Undone: remove octave"
    assert served.rig.chains["board"][:3] == ["overdrive", "octave", "delay"]


def test_several_undos_go_back_newest_first(served):
    call(served, "POST", "/live/bypass", {"processor": "delay", "bypassed": True})
    call(served, "POST", "/live/parameter", {"processor": "overdrive", "parameter": "Tone", "value": 0.1})
    call(served, "POST", "/live/remove", {"processor": "octave"})
    said = [undo(served)[1]["said"] for _ in range(3)]
    assert said == ["Undone: remove octave", "Undone: overdrive Tone", "Undone: bypass delay"]
    assert undo(served)[1]["code"] == "nothing_to_undo"
    assert served.rig.bypassed["delay"] is False and "octave" in served.rig.chains["board"]


def test_a_value_changed_elsewhere_since_cannot_be_undone_and_the_next_press_goes_on(served):
    call(served, "POST", "/live/bypass", {"processor": "delay", "bypassed": True})
    call(served, "POST", "/live/parameter", {"processor": "overdrive", "parameter": "Drive", "value": 0.9})
    served.rig.info["overdrive"]["Drive"]["value"] = 0.1  # a MIDI mapping Sushi serves, which the agent does not see
    status, body = undo(served)
    assert status == 409 and body["code"] == "cant_undo"
    assert body["skipped"]["label"] == "overdrive Drive"
    assert value(served, "overdrive", "Drive") == pytest.approx(0.1)  # left as it is
    status, body = undo(served)
    assert status == 200 and body["said"] == "Undone: bypass delay"


def test_a_rig_delete_is_undone(served):
    clean = rig_file(served, "clean")
    clean.write_text(rig_file(served).read_text().replace("name: electric_board", "name: clean"))
    original = clean.read_bytes()
    status, body = call(served, "POST", "/rigs/clean/delete", {})
    assert status == 200 and not clean.exists()
    assert call(served, "GET", "/undo")[1]["next"]["label"] == "delete clean"
    status, body = undo(served)
    assert status == 200, body
    assert body["said"] == "Undone: delete clean" and body["how"] == "restored"
    assert clean.read_bytes() == original


def test_a_rig_put_back_some_other_way_cannot_be_undone_again(served):
    clean = rig_file(served, "clean")
    clean.write_text(rig_file(served).read_text())
    entry = call(served, "POST", "/rigs/clean/delete", {})[1]["binned"]["entry"]
    assert call(served, "POST", "/rigs/clean/restore", {"entry": entry})[0] == 200
    nxt = call(served, "GET", "/undo")[1]["next"]
    assert nxt["can"] is False and "there again" in nxt["why"]
    status, body = undo(served)
    assert status == 409 and body["code"] == "cant_undo"
    assert call(served, "GET", "/undo")[1]["next"] is None


def test_a_save_over_the_file_is_undone_and_its_changes_are_unsaved_again(served):
    path = rig_file(served)
    original = path.read_bytes()
    call(served, "POST", "/live/parameter", {"processor": "overdrive", "parameter": "Drive", "value": 0.9})
    status, body = call(served, "POST", "/rig/save", {})
    assert status == 200, body
    assert path.read_bytes() != original
    nxt = call(served, "GET", "/undo")[1]["next"]
    assert nxt["label"] == "save electric_board" and nxt["kind"] == "file" and nxt["can"]
    status, body = undo(served)
    assert status == 200, body
    assert body["said"] == "Undone: save electric_board" and body["how"] == "file"
    assert path.read_bytes() == original
    # The knob is still turned, and is unsaved again: a restart says so.
    assert value(served, "overdrive", "Drive") == pytest.approx(0.9)
    status, body = call(served, "POST", "/rig/restart", {})
    assert status == 409 and body["code"] == "unsaved"
    # And the knob is the next step back.
    assert call(served, "GET", "/undo")[1]["next"]["label"] == "overdrive Drive"


def test_a_file_changed_since_its_write_cannot_be_put_back(served):
    path = rig_file(served)
    call(served, "POST", "/live/parameter", {"processor": "overdrive", "parameter": "Drive", "value": 0.2})
    assert call(served, "POST", "/rig/save", {})[0] == 200
    path.write_text(path.read_text() + "# an editor's line\n")
    nxt = call(served, "GET", "/undo")[1]["next"]
    assert nxt["label"] == "save electric_board"
    assert nxt["can"] is False and "changed since" in nxt["why"]
    status, body = undo(served)
    assert status == 409 and body["code"] == "cant_undo"
    assert path.read_text().endswith("# an editor's line\n")
    # Dropped: the next press goes on to the knob.
    assert call(served, "GET", "/undo")[1]["next"]["label"] == "overdrive Drive"


def test_a_save_as_is_recorded_and_cannot_be_undone(served):
    call(served, "POST", "/live/bypass", {"processor": "delay", "bypassed": True})
    status, body = call(served, "POST", "/rig/save", {"name": "electric_two"})
    assert status == 200, body
    nxt = call(served, "GET", "/undo")[1]["next"]
    assert nxt["label"] == "save as electric_two" and nxt["can"] is False and nxt["why"]
    status, body = undo(served)
    assert status == 409 and body["code"] == "cant_undo"
    assert rig_file(served, "electric_two").exists()


def test_an_added_channel_is_undone_and_says_no_restart_is_needed(served):
    path = rig_file(served)
    original = path.read_bytes()
    status, body = call(served, "POST", "/rigs/electric_board/channels/add",
                        {"name": "keys", "input": "keys-in", "width": "mono"})
    assert status == 200, body
    assert body["takes_effect"] == "reload"
    status, body = undo(served)
    assert status == 200, body
    assert body["said"] == "Undone: add channel keys"
    # The rig never played the channel, and the file is as it plays again.
    assert body["how"] == "file"
    assert path.read_bytes() == original


def test_an_input_change_undone_after_a_restart_says_restart(served):
    path = rig_file(served)
    original = path.read_bytes()
    status, body = call(served, "POST", "/rigs/electric_board/channels/vocal/input", {"input": "keys-in"})
    assert status == 200, body
    assert call(served, "POST", "/rig/restart", {})[0] == 200
    status, body = undo(served)
    assert status == 200, body
    assert body["said"] == "Undone: input vocal"
    assert body["how"] == "restart" and body["offer"] == "restart"
    assert path.read_bytes() == original


def test_a_pedal_assignment_is_undone_live(served):
    path = rig_file(served)
    original = path.read_bytes()
    status, body = call(served, "POST", "/rig/midi/assign", {"control": "c", "bypass": "board/delay"})
    assert status == 200, body
    assert body["how"] == "live"
    assert "delay" in json.dumps(served.save.config.get("_agent_bindings") or served.save.config)
    status, body = undo(served)
    assert status == 200, body
    assert body["said"] == "Undone: pedal c" and body["how"] == "live"
    assert path.read_bytes() == original
    from sushi_rig.rigfile import AGENT_BINDINGS
    assert not served.save.config.get(AGENT_BINDINGS)


def test_a_new_rig_is_undone_into_the_bin(served):
    status, body = call(served, "POST", "/rigs/new",
                        {"name": "keys_rig", "channels": [{"name": "keys", "input": "keys-in", "width": "mono"}]})
    assert status == 200, body
    status, body = undo(served)
    assert status == 200, body
    assert body["said"] == "Undone: new rig keys_rig"
    assert not rig_file(served, "keys_rig").exists()
    assert call(served, "GET", "/rigs/bin")[1]["entries"][0]["rig"] == "keys_rig"


def test_a_revert_is_undone_by_switching_back(served):
    path = rig_file(served)
    call(served, "POST", "/live/bypass", {"processor": "delay", "bypassed": True})
    assert call(served, "POST", "/rig/save", {})[0] == 200
    saved = path.read_bytes()
    point = call(served, "GET", "/rigs/electric_board/saves")[1]["points"]
    commit = next(p for p in point if p["kind"] == "commit")
    status, body = call(served, "POST", "/rigs/electric_board/revert", {"save": commit["id"]})
    assert status == 200, body
    assert path.read_bytes() != saved
    status, body = undo(served)
    assert status == 200, body
    assert body["said"] == "Undone: revert electric_board" and body["how"] == "switched"
    assert path.read_bytes() == saved


def test_kinds_mixed_are_undone_in_the_order_they_were_done(served):
    clean = rig_file(served, "clean")
    clean.write_text(rig_file(served).read_text())
    call(served, "POST", "/live/bypass", {"processor": "delay", "bypassed": True})
    call(served, "POST", "/rigs/clean/delete", {})
    call(served, "POST", "/live/remove", {"processor": "octave"})
    said = []
    while True:
        status, body = undo(served)
        if status != 200:
            assert body["code"] == "nothing_to_undo"
            break
        said.append(body["said"])
    assert said == ["Undone: remove octave", "Undone: delete clean", "Undone: bypass delay"]


def test_the_depth_is_fifty(served):
    for n in range(55):
        # Each its own control, so none merge into one step.
        call(served, "POST", "/live/bypass", {"processor": "delay", "bypassed": n % 2 == 0})
    assert call(served, "GET", "/undo")[1]["depth"] == 50
    done = 0
    while undo(served)[0] == 200:
        done += 1
    assert done == 50
    # Five were past the depth: the delay is as the fifth change (n = 4) left it.
    assert served.rig.bypassed["delay"] is True


def test_the_old_live_undo_takes_its_step_out_of_the_history(served):
    call(served, "POST", "/live/bypass", {"processor": "delay", "bypassed": True})
    assert call(served, "POST", "/live/undo", {})[0] == 200
    assert call(served, "GET", "/undo")[1]["next"] is None


def test_a_body_is_refused(served):
    status, body = call(served, "POST", "/undo", {"skip": True})
    assert status == 400


def test_an_amp_parameter_is_undone():
    with Served(amp=True) as served:
        amp = call(served, "GET", "/amp")[1]
        name = next(p["name"] for p in amp["parameters"])
        was = next(p for p in amp["parameters"] if p["name"] == name)["value"]
        assert call(served, "POST", "/amp/parameter", {"name": name, "value": (was or 0) - 3})[0] == 200
        status, body = undo(served)
        assert status == 200, body
        assert body["said"] == f"Undone: amp {name}"
        now = call(served, "GET", "/amp")[1]
        assert next(p for p in now["parameters"] if p["name"] == name)["value"] == pytest.approx(was)


# ------------------------------------------------------------- #210's pedals, merged

def test_a_pedal_mapping_sushi_took_live_is_taken_back_live(served):
    """#210 maps a volume pedal in Sushi live (elkpy's connect, read back);
    its Undo puts the file back and disconnects the mapping live, by the
    assignment's own path, with no restart. Against test_agent_midi_assign's
    LiveCC: a real Sushi doing this is unverified."""
    from sushi_rig.rigfile import AGENT_BINDINGS
    from test_agent_midi_assign import LiveCC, listening

    sushi = LiveCC(served.rig)
    listening(served)
    path = rig_file(served)
    original = path.read_bytes()
    status, body = call(served, "POST", "/rig/midi/assign", {"control": "pedal", "volume": "vocal"})
    assert status == 200 and body["how"] == "live", body
    assert [m["plugin_name"] for m in sushi.held] == ["vocal_send"]
    status, body = undo(served)
    assert status == 200, body
    assert body["said"] == "Undone: pedal" and body["how"] == "live", body
    assert path.read_bytes() == original
    assert sushi.calls[-1] == ("disconnect", "vocal_send", 7) and sushi.held == [], "taken off Sushi live"
    assert not (served.save.config.get("midi") or {}).get("cc_mappings"), "the running config says so"
    assert not served.save.config.get(AGENT_BINDINGS)
    assert served.lifecycle.calls == [], "nothing restarted"


def test_a_pedal_mapping_that_cannot_be_taken_back_live_offers_the_restart(served):
    from test_agent_midi_assign import LiveCC, listening

    sushi = LiveCC(served.rig)
    listening(served)
    path = rig_file(served)
    original = path.read_bytes()
    assert call(served, "POST", "/rig/midi/assign", {"control": "pedal", "volume": "vocal"})[1]["how"] == "live"
    sushi.stuck = True  # Sushi keeps the mapping a disconnect asks it to drop
    status, body = undo(served)
    assert status == 200, body
    assert body["how"] == "restart" and body["offer"] == "restart", body
    assert any(r["code"] == "midi_live" for r in body["why"]), body["why"]
    assert path.read_bytes() == original, "the file is back whatever Sushi did"


def test_a_channel_muted_from_a_footswitch_is_unmuted_by_undo(served):
    """#210: a channel's mute is its send bypassed. A press of C, assigned
    to the vocal's on/off, is a live step like any bypass, said as a mute."""
    from test_agent_midi_assign import listening

    listening(served)
    status, body = call(served, "POST", "/rig/midi/assign", {"control": "c", "mute": "vocal"})
    assert status == 200 and body["how"] == "live", body
    served.pedals.press("c")
    deadline = time.monotonic() + 5
    while served.rig.bypassed["vocal_send"] is not True and time.monotonic() < deadline:
        time.sleep(0.02)
    assert served.rig.bypassed["vocal_send"] is True, "the press muted the channel"
    assert call(served, "GET", "/undo")[1]["next"]["label"] == "mute vocal"
    status, body = undo(served)
    assert status == 200, body
    assert body["said"] == "Undone: mute vocal" and body["how"] == "live"
    assert served.rig.bypassed["vocal_send"] is False
    status, body = undo(served)
    assert status == 200 and body["said"] == "Undone: pedal c", body


# ------------------------------------------------------------- PR #235's review

def _revert_to_commit(served):
    """A knob saved, then the rig reverted to its commit: a revert to undo."""
    call(served, "POST", "/live/bypass", {"processor": "delay", "bypassed": True})
    assert call(served, "POST", "/rig/save", {})[0] == 200
    saved = rig_file(served).read_bytes()
    points = call(served, "GET", "/rigs/electric_board/saves")[1]["points"]
    commit = next(p for p in points if p["kind"] == "commit")
    status, body = call(served, "POST", "/rigs/electric_board/revert", {"save": commit["id"]})
    assert status == 200, body
    return saved, commit


def _count_syncs(served):
    agent, calls = served.agent, []
    real = agent._midi_sync

    async def counting():
        calls.append(1)
        await real()

    agent._midi_sync = counting
    return calls


def test_undoing_a_revert_has_the_pedals_follow_the_rig_as_the_revert_does(served):
    """Review 1: the switch back stops the MIDI listener (`_switch_held`), so
    the Undo, as the revert, has it follow the rig after; without it the
    pedals were dead until the next lifecycle command."""
    calls = _count_syncs(served)
    saved, _ = _revert_to_commit(served)
    by_revert = len(calls)
    assert by_revert >= 1
    status, body = undo(served)
    assert status == 200 and body["how"] == "switched", body
    assert len(calls) - by_revert >= 1, "the listener follows the rig the undo switched into"
    assert rig_file(served).read_bytes() == saved


def test_undo_across_a_switch_waits_for_unsaved_work_as_a_revert_does(served):
    """Review 2: a live change that could not be undone (moved from outside the agent)
    is dropped from the history but still plays. The revert's Undo, which
    switches, would drop it: refused `unsaved`, the step kept, until the
    page says to drop it (`discard`)."""
    saved, _ = _revert_to_commit(served)
    assert call(served, "POST", "/live/bypass", {"processor": "delay", "bypassed": True})[0] == 200
    served.rig.bypassed["delay"] = False  # moved since from elsewhere
    status, body = undo(served)
    assert status == 409 and body["code"] == "cant_undo", body
    served.rig.bypassed["delay"] = True  # and back, still unsaved
    reverted = rig_file(served).read_bytes()
    status, body = undo(served)
    assert status == 409 and body["code"] == "unsaved", body
    assert body["step"]["label"] == "revert electric_board"
    assert rig_file(served).read_bytes() == reverted, "nothing put back"
    assert call(served, "GET", "/undo")[1]["next"]["label"] == "revert electric_board", "the step stays"
    status, body = call(served, "POST", "/undo", {"discard": True})
    assert status == 200 and body["how"] == "switched", body
    assert body["discard"], "what was dropped is said"
    assert rig_file(served).read_bytes() == saved


def test_an_edit_of_a_rig_not_running_then_switched_to_offers_a_restart(served):
    """Review 3: what a step does is decided at the Undo. An edit of `clean`
    made while it was not running needed nothing then; switched to since,
    `clean` plays what was written, so putting it back offers a restart."""
    clean = rig_file(served, "clean")
    clean.write_text(rig_file(served).read_text())
    status, body = call(served, "POST", "/rigs/clean/channels/add", {"name": "keys", "input": "keys-in", "width": "mono"})
    assert status == 200 and body["running"] is False, body
    written = clean.read_bytes()
    status, body = call(served, "POST", "/rig/switch", {"config_name": "clean"})
    assert status == 200, body
    status, body = undo(served)
    assert status == 200, body
    assert body["said"] == "Undone: add channel keys"
    assert body["how"] == "restart" and body["offer"] == "restart", body
    assert clean.read_bytes() != written


def test_a_save_undone_after_a_restart_offers_a_restart(served):
    path = rig_file(served)
    original = path.read_bytes()
    call(served, "POST", "/live/parameter", {"processor": "overdrive", "parameter": "Drive", "value": 0.9})
    assert call(served, "POST", "/rig/save", {})[0] == 200
    assert call(served, "POST", "/rig/restart", {})[0] == 200
    status, body = undo(served)
    assert status == 200, body
    assert body["said"] == "Undone: save electric_board"
    assert body["how"] == "restart" and body["offer"] == "restart", "the rig plays the save until it restarts"
    assert path.read_bytes() == original


def test_an_undo_for_a_step_no_longer_the_newest_is_refused_changed(served):
    """Review 4: the page sends the step it showed (`seq`); another page's
    change, or one landing since, makes it not the newest: 409 `changed`,
    nothing undone, `next` saying what is newest now."""
    call(served, "POST", "/live/bypass", {"processor": "delay", "bypassed": True})
    shown = call(served, "GET", "/undo")[1]["next"]
    call(served, "POST", "/live/parameter", {"processor": "overdrive", "parameter": "Drive", "value": 0.9})
    status, body = call(served, "POST", "/undo", {"seq": shown["seq"]})
    assert status == 409 and body["code"] == "changed", body
    assert body["next"]["label"] == "overdrive Drive"
    assert value(served, "overdrive", "Drive") == pytest.approx(0.9) and served.rig.bypassed["delay"] is True
    status, body = call(served, "POST", "/undo", {"seq": body["next"]["seq"]})
    assert status == 200 and body["said"] == "Undone: overdrive Drive", body


@pytest.mark.parametrize("body", [{"seq": "1"}, {"seq": True}, {"discard": "yes"}, {"restart": 1}, {"skip": True}, []])
def test_an_undo_body_of_the_wrong_shape_is_400(served, body):
    assert call(served, "POST", "/undo", body)[0] == 400


def test_the_live_undo_refuses_a_pick_that_is_not_the_one_asked_for():
    from sushi_rig.agent import Agent

    Agent._expect_pick((4, {}), None)
    Agent._expect_pick((4, {}), 4)
    with pytest.raises(Exception) as caught:
        Agent._expect_pick((5, {}), 4)
    assert getattr(caught.value, "agent_code", None) == "changed" or "changed" in str(caught.value.reason)


def test_a_live_step_the_live_log_no_longer_holds_is_cant_undo_not_skipped_past(served):
    """Review 7: a step whose log entries were trimmed is said, `cant_undo`
    "too old", not passed over to undo an older one in the same press."""
    served.agent.log_limit = 2
    call(served, "POST", "/live/bypass", {"processor": "delay", "bypassed": True})
    call(served, "POST", "/live/bypass", {"processor": "overdrive", "bypassed": False})
    status, body = undo(served)
    assert status == 200 and body["said"] == "Undone: enable overdrive", body
    status, body = undo(served)
    assert status == 409 and body["code"] == "cant_undo" and "too old" in body["reason"], body
    assert body["skipped"]["label"] == "bypass delay"
    assert served.rig.bypassed["delay"] is True, "nothing older was undone in its place"


@pytest.mark.parametrize("command", ["restart", "down"])
def test_a_lifecycle_command_ends_the_live_steps_and_keeps_the_files(served, command):
    """Review 8: a bypass on the rig that was up is not undone on the run
    after a restart, a down or an up, which may be another rig."""
    status, body = call(served, "POST", "/rigs/electric_board/channels/vocal/input", {"input": "keys-in"})
    assert status == 200, body
    call(served, "POST", "/live/bypass", {"processor": "delay", "bypassed": True})
    call(served, "POST", "/live/bypass", {"processor": "overdrive", "bypassed": False})
    assert call(served, "GET", "/undo")[1]["depth"] == 3
    status, body = call(served, "POST", f"/rig/{command}", {"discard": True} if command == "restart" else {})
    assert status == 200, body
    if command == "down":
        assert call(served, "POST", "/rig/up", {"config_name": "electric_board"})[0] == 200
    nxt = call(served, "GET", "/undo")[1]
    assert nxt["next"]["label"] == "input vocal" and nxt["depth"] == 1, nxt


def test_a_revert_or_its_undo_that_would_restart_sushi_is_asked_about_first(served):
    """Review 5: a revert is one tap since Undo takes it back, but a restart
    cuts the sound. With `"restart": false` (what the page sends first), a
    revert or an Undo whose switch would restart Sushi is 409 `restarts`,
    with why and nothing written; with `"restart": true` it goes ahead."""
    path = rig_file(served)
    status, body = call(served, "POST", "/rigs/electric_board/channels/add",
                        {"name": "keys", "input": "keys-in", "width": "mono"})
    assert status == 200, body
    assert call(served, "POST", "/rig/restart", {})[0] == 200  # the rig plays keys now
    with_keys = path.read_bytes()
    points = call(served, "GET", "/rigs/electric_board/saves")[1]["points"]
    commit = next(p for p in points if p["kind"] == "commit")
    status, body = call(served, "POST", "/rigs/electric_board/revert", {"save": commit["id"], "restart": False})
    assert status == 409 and body["code"] == "restarts", body
    assert body["why"] and path.read_bytes() == with_keys, "nothing written"
    status, body = call(served, "POST", "/rigs/electric_board/revert", {"save": commit["id"], "restart": True})
    assert status == 200, body
    reverted = path.read_bytes()
    # The Undo of that revert puts keys back: a switch only a restart makes.
    assert call(served, "POST", "/rig/restart", {})[0] == 200  # the rig plays the revert
    status, body = call(served, "POST", "/undo", {"restart": False})
    assert status == 409 and body["code"] == "restarts" and body["why"], body
    assert body["step"]["label"] == "revert electric_board" and path.read_bytes() == reverted
    status, body = call(served, "POST", "/undo", {"restart": True})
    assert status == 200 and body["said"] == "Undone: revert electric_board", body
    assert path.read_bytes() == with_keys


def test_a_revert_that_switches_live_is_not_asked_about(served):
    _, commit = _revert_to_commit(served)
    status, body = call(served, "POST", "/undo", {"restart": False})
    assert status == 200 and body["how"] == "switched", body


# ------------------------------------------------------------- PR #235's re-review

def test_a_revert_dropping_a_plugin_added_or_removed_live_says_it_restarts(served):
    """Re-review 1: "Drop changes?" answered Yes resends `discard: true,
    restart: false`. A plugin removed live and dropped leaves chains the
    running config lacks, which the live plan refuses (`drifted`): a
    restart, so it is `restarts`, asked, not done."""
    path = rig_file(served)
    points = call(served, "GET", "/rigs/electric_board/saves")[1]["points"]
    commit = next(p for p in points if p["kind"] == "commit")
    assert call(served, "POST", "/live/remove", {"processor": "octave"})[0] == 200
    before = path.read_bytes()
    status, body = call(served, "POST", "/rigs/electric_board/revert",
                        {"save": commit["id"], "discard": True, "restart": False})
    assert status == 409 and body["code"] == "restarts", body
    assert [r["code"] for r in body["why"]] == ["discarded"], body["why"]
    assert path.read_bytes() == before and "octave" not in served.rig.chains["board"], "nothing done"


def test_what_a_discard_drops_is_counted_by_the_switchs_own_rule(served):
    """`_would_restart` with the dropped work, as both the revert and the
    Undo call it: structural work, or a value whose before Sushi did not
    report, is a restart; a knob with its before known is put back live."""
    agent = served.agent
    run = {"state": served.save.state, "described": served.save.describe()}
    compiled = served.save.config
    knob = {"op": "parameter", "args": {"processor": "delay", "parameter": "Feedback", "value": 0.9},
            "previous": 0.35}
    # One the next config states no value for, and Sushi gave none before.
    unknown = {"op": "parameter", "args": {"processor": "delay", "parameter": "Not Stated", "value": 0.5},
               "previous": None}
    moved = {"op": "move", "args": {"processor": "delay", "direction": "up"}, "previous": None}
    assert agent._would_restart(compiled, run, []) == []
    assert agent._would_restart(compiled, run, [knob]) == []
    assert [r["code"] for r in agent._would_restart(compiled, run, [moved])] == ["discarded"]
    assert [r["code"] for r in agent._would_restart(compiled, run, [knob, unknown])] == ["discarded"]


def test_a_lifecycle_command_that_changed_nothing_keeps_the_live_steps(served):
    """Re-review 2: an `up` refused because a rig is already running changes
    no run, so the live steps stay to be undone."""
    call(served, "POST", "/live/bypass", {"processor": "delay", "bypassed": True})
    status, body = call(served, "POST", "/rig/up", {"config_name": "electric_board"})
    assert status != 200, body
    nxt = call(served, "GET", "/undo")[1]
    assert nxt["next"]["label"] == "bypass delay" and nxt["depth"] == 1, nxt
    status, body = undo(served)
    assert status == 200 and body["said"] == "Undone: bypass delay", body
    assert served.rig.bypassed["delay"] is False


# ------------------------------------------------------------- what an answer says it logged (#245's review)

def test_an_undos_answer_carries_the_entries_it_logged_and_a_live_change_its_own(served):
    """The page tells its own changes from another's by the log entries its
    answers carry (#245). A live change carries its entry inline, as before,
    and nothing more; an Undo's answer carried none, so the page's own Undo
    was said as made elsewhere. Now it carries `logged`, every entry the
    request made, as they stand in the log."""
    status, moved = call(served, "POST", "/live/move", {"processor": "delay", "direction": "up"})
    assert status == 200 and moved["op"] == "move" and "logged" not in moved, "its own entry is carried already"
    status, body = undo(served)
    assert status == 200, body
    status, log = call(served, "GET", "/live/log")
    newest = log["log"][-1]
    assert newest["op"] == "undo"
    assert body["logged"] == [{"op": newest["op"], "at": newest["at"], "args": newest["args"]}]
    # A refusal carries nothing it did not do.
    status, refused = undo(served)
    assert status == 409 and "logged" not in refused
