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
    served.rig.info["overdrive"]["Drive"]["value"] = 0.1  # the panel, which the agent does not see
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
    assert body["takes_effect"] == "restart"
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
