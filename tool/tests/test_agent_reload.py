"""The running rig's file reloaded, and a channel moved, through the agent
(#142): GET and POST /rig/reload, POST /rigs/<rig>/channels/<channel>/move.

Against real files in a temporary checkout, as test_agent_channel_load.py
runs a load: copies of the committed rigs (rig_fixtures.frozen_checkout),
their machine file and the real compiler. The running rig is
`test_agent.RigLive`, a fake Sushi loaded from that compile, which the
agent's real live switch drives, so "the rig plays it" is a reading of the
fake after the reload, not a word in an answer; a restart is the fake
runner's `down` and `up`.

NOT shown: any of it against a live Sushi, and whether a reload played live
is heard without a gap there.
"""

from __future__ import annotations

import pytest
import yaml

pytest.importorskip("aiohttp")

from sushi_rig import rigedit, rigfile  # noqa: E402
from sushi_rig.agent import reload_refusal  # noqa: E402

from test_agent_channel_load import (  # noqa: E402,F401
    _agents_release_their_pools, another, call, make_agent, plugins_of, rig_file, root,
)

OVERDRIVE = '      - {name: guitar_overdrive, uri: "http://guitarix.sourceforge.net/plugins/gxts9#ts9sim"}\n'


def reload(agent, **body):
    return call(agent, "POST", "/rig/reload", json=body)


def looked(agent):
    status, body = call(agent, "GET", "/rig/reload")
    assert status == 200, body
    return body


def hand_edit(path, parameter, value):
    """The overdrive's parameter set by hand, as a person would type it:
    the flow-style line made a block with a `parameters:` key."""
    text = path.read_text()
    assert OVERDRIVE in text
    path.write_text(text.replace(OVERDRIVE, (
        "      - name: guitar_overdrive\n"
        '        uri: "http://guitarix.sourceforge.net/plugins/gxts9#ts9sim"\n'
        f"        parameters: {{{parameter}: {value}}}  # set by hand\n")))


def channel_names(path):
    return [c["name"] for c in yaml.safe_load(path.read_text())["channels"]]


# --------------------------------------------------------------------------- #
# The requests' shapes, and a move as a document (pure)
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("body", [[], "x", {"rig": ""}, {"rig": 3}, {"discard": "yes"}, {"restart": "no"},
                                  {"channel": "guitar"}])
def test_a_reload_takes_three_optional_keys_and_nothing_else(body):
    with pytest.raises(rigedit.EditRequestError):
        rigedit.reload_request(body)
    assert rigedit.reload_request(None) == {"rig": None, "discard": False, "restart": None}
    assert rigedit.reload_request({"rig": "r", "discard": True, "restart": False}) == {
        "rig": "r", "discard": True, "restart": False}


@pytest.mark.parametrize("body", [None, {}, {"direction": "left"}, {"direction": "up", "by": 2}, []])
def test_a_move_says_up_or_down_and_nothing_else(body):
    with pytest.raises(rigedit.EditRequestError):
        rigedit.move_request(body)
    assert rigedit.move_request({"direction": "up"}) == "up"
    assert rigedit.move_request({"direction": "down"}) == "down"


def test_a_channel_moves_one_place_and_the_document_is_left_as_it_was():
    raw = {"name": "r", "channels": [{"name": "a"}, {"name": "b"}, {"name": "c"}]}
    out, to = rigedit.with_channel_moved(raw, "c", "up")
    assert [c["name"] for c in out["channels"]] == ["a", "c", "b"] and to == 1
    assert [c["name"] for c in raw["channels"]] == ["a", "b", "c"], "pure"
    out, to = rigedit.with_channel_moved(raw, "a", "down")
    assert [c["name"] for c in out["channels"]] == ["b", "a", "c"] and to == 1
    with pytest.raises(IndexError, match="already r's first channel"):
        rigedit.with_channel_moved(raw, "a", "up")
    with pytest.raises(IndexError, match="already r's last channel"):
        rigedit.with_channel_moved(raw, "c", "down")
    with pytest.raises(LookupError, match="has no channel called d"):
        rigedit.with_channel_moved(raw, "d", "up")


def test_a_reload_with_unsaved_work_is_refused_in_its_own_words():
    state = {"rig": "config/rigs/r.yaml", "started_at": "2026-11-06T10:00:00+00:00"}
    log = [{"op": "parameter", "at": "2026-11-06T10:01:00+00:00",
            "args": {"processor": "p", "parameter": "x", "value": 0.2}}]
    assert reload_refusal(state, []) is None
    reason, code = reload_refusal(state, log)
    assert code == "unsaved" and reason.startswith("r is running with 1 change made through the agent")
    assert "a reload plays the file as it is now, which drops them" in reason
    assert "its file is what it plays" in reload_refusal(state, log, changed=False)[0]


# --------------------------------------------------------------------------- #
# A hand edit, reloaded
# --------------------------------------------------------------------------- #

def test_a_parameter_changed_by_hand_is_file_changed_and_reloads_live(root):
    """#142's first "done": a parameter changed by hand in the running rig's
    file is played by a reload with no restart, and the answer says it was
    live."""
    agent, world, fake, runner = make_agent(root)
    path = rig_file(root, "guitar_vocal")
    assert looked(agent)["changed"] is False, "the file is what plays"
    turned = next(iter(fake.values["guitar_overdrive"]))
    hand_edit(path, turned, 0.25)
    said = looked(agent)
    assert said["changed"] is True and said["how"] == "live" and said["why"] == [], said
    status, body = reload(agent, rig="guitar_vocal", restart=False)
    assert status == 200, body
    assert body["how"] == "live", body
    assert {"op": "parameter", "processor": "guitar_overdrive", "parameter": turned, "value": 0.25} in body["plan"]
    assert runner.calls == [], "nothing was restarted"
    assert fake.values["guitar_overdrive"][turned] == pytest.approx(0.25), "the rig plays it: the fake"
    assert body["entry"]["op"] == "rig" and body["entry"]["args"]["reload"] is True
    assert "# set by hand" in path.read_text(), "nothing was written"
    assert looked(agent)["changed"] is False, "the file is what plays again"


def test_a_reload_is_undone_by_the_one_undo_the_file_and_the_rig_put_back(root):
    agent, world, fake, runner = make_agent(root)
    path = rig_file(root, "guitar_vocal")
    before = path.read_bytes()
    assert looked(agent)["changed"] is False
    turned = next(iter(fake.values["guitar_overdrive"]))
    was = fake.values["guitar_overdrive"][turned]
    hand_edit(path, turned, 0.25)
    assert reload(agent, restart=False)[0] == 200
    status, state = call(agent, "GET", "/undo")
    assert state["next"]["label"] == "reload guitar_vocal" and state["next"]["act"] == "reload", state
    status, body = call(agent, "POST", "/undo", json={"seq": state["next"]["seq"], "restart": False})
    assert status == 200 and body["how"] == "switched", body
    assert path.read_bytes() == before, "the file as it played before the reload"
    assert fake.values["guitar_overdrive"][turned] == pytest.approx(was), "and the rig plays it again"
    assert runner.calls == []


def test_a_file_that_is_what_plays_is_a_reload_that_says_so_and_does_nothing(root):
    agent, world, fake, runner = make_agent(root)
    status, body = reload(agent, restart=False)
    assert status == 200, body
    assert body["how"] == "unchanged" and body["changed"] is False, body
    assert body["note"] == "config/rigs/guitar_vocal.yaml is what guitar_vocal plays: nothing to reload"
    assert "entry" not in body and runner.calls == []
    assert [e["op"] for e in agent.log] == [], "no switch was logged"
    assert call(agent, "GET", "/undo")[1]["next"] is None, "nothing to undo"


def test_a_comment_changed_by_hand_is_not_file_changed(root):
    agent, world, fake, runner = make_agent(root)
    path = rig_file(root, "guitar_vocal")
    path.write_text(path.read_text().replace("# Empty for now.", "# Still empty."))
    assert looked(agent)["changed"] is False
    assert reload(agent)[1]["how"] == "unchanged"


def test_a_reload_with_unsaved_work_is_refused_until_it_is_dropped(root):
    agent, world, fake, runner = make_agent(root)
    path = rig_file(root, "guitar_vocal")
    turned = next(iter(fake.values["guitar_overdrive"]))
    hand_edit(path, turned, 0.25)
    status, said = call(agent, "POST", "/live/parameter",
                        json={"processor": "guitar_eq", "parameter": next(iter(fake.values["guitar_eq"])),
                              "value": 0.1})
    assert status == 200, said
    assert looked(agent)["unsaved"] == 1
    status, body = reload(agent, restart=False)
    assert status == 409 and body["code"] == "unsaved", body
    assert "a reload plays the file as it is now, which drops them" in body["reason"]
    assert fake.values["guitar_overdrive"][turned] != pytest.approx(0.25), "nothing changed"
    status, body = reload(agent, restart=False, discard=True)
    assert status == 200 and body["how"] == "live", body
    assert fake.values["guitar_overdrive"][turned] == pytest.approx(0.25)
    assert body["discard"]["changes"] == 1


def test_a_file_that_will_not_compile_is_said_and_not_reloaded(root):
    agent, world, fake, runner = make_agent(root)
    path = rig_file(root, "guitar_vocal")
    path.write_text(path.read_text().replace("input: vocal-in", "input: no-such-in"))
    said = looked(agent)
    assert said["changed"] is True and said["code"] == "rig_problem" and said["how"] is None, said
    assert said["problem"].startswith("not reloaded: "), said
    status, body = reload(agent)
    assert status == 409 and body["code"] == "rig_problem", body
    assert "no-such-in" in body["reason"]
    assert runner.calls == []


def test_a_reload_meant_for_another_rig_changes_nothing(root):
    agent, world, fake, runner = make_agent(root)
    status, body = reload(agent, rig="accustomed")
    assert status == 409 and body["code"] == "changed", body


def test_a_save_over_the_file_as_it_played_is_not_file_changed(root):
    """A save writes live values into the file and leaves the running
    config as Sushi loaded it: the file is what plays all the same."""
    agent, world, fake, runner = make_agent(root)
    assert looked(agent)["changed"] is False
    turned = next(iter(fake.values["guitar_eq"]))
    assert call(agent, "POST", "/live/parameter",
                json={"processor": "guitar_eq", "parameter": turned, "value": 0.1})[0] == 200
    status, body = call(agent, "POST", "/rig/save", json={})
    assert status == 200, body
    assert looked(agent)["changed"] is False


# --------------------------------------------------------------------------- #
# A rack edit offers the reload, which says why it restarts first
# --------------------------------------------------------------------------- #

def test_a_channel_added_offers_a_reload_that_restarts_and_says_why_first(root):
    agent, world, fake, runner = make_agent(root)
    path = rig_file(root, "guitar_vocal")
    before = path.read_bytes()
    status, body = call(agent, "POST", "/rigs/guitar_vocal/channels/add",
                        json={"name": "keys", "input": "vocal-in", "width": "mono"})
    assert status == 200, body
    assert (body["takes_effect"], body["offer"], body["how_reload"]) == ("reload", "reload", "restart"), body
    assert "track_added" in [r["code"] for r in body["why"]]
    assert "until it is reloaded" in body["note"] and "the reload restarts it" in body["note"]
    said = looked(agent)
    assert said["changed"] is True and said["how"] == "restart"
    assert "track_added" in [r["code"] for r in said["why"]]
    status, body = reload(agent, restart=False)
    assert status == 409 and body["code"] == "restarts", body
    assert body["reason"].startswith("nothing was changed: reloading guitar_vocal restarts Sushi: track_added")
    assert "track_added" in [r["code"] for r in body["why"]]
    assert runner.calls == [] and fake.tracks == ["guitar", "vocal", "master"], "asked first: nothing changed"
    status, body = reload(agent, restart=True)
    assert status == 200, body
    assert body["how"] == "restart" and "track_added" in [r["code"] for r in body["why"]], body
    assert runner.calls and runner.calls[0][0] == "down", runner.calls
    # One Undo for both: the reload's, with the add inside it.
    status, state = call(agent, "GET", "/undo")
    assert state["next"]["label"] == "reload guitar_vocal", state
    status, body = call(agent, "POST", "/undo", json={"seq": state["next"]["seq"], "restart": True})
    assert status == 200, body
    assert path.read_bytes() == before, "the file as it was before the add"
    assert call(agent, "GET", "/undo")[1]["next"] is None, "the add was in the reload's step"


def test_an_input_change_offers_a_reload(root):
    agent, world, fake, runner = make_agent(root)
    status, body = call(agent, "POST", "/rigs/guitar_vocal/channels/vocal/input", json={"input": "guitar-in"})
    assert status == 200, body
    assert (body["offer"], body["how_reload"]) == ("reload", "restart"), body
    assert "wiring" in [r["code"] for r in body["why"]], body


# --------------------------------------------------------------------------- #
# A channel moved
# --------------------------------------------------------------------------- #

def test_a_channel_moved_is_written_in_place_and_its_reload_is_a_restart(root):
    """Channel order is track order in Sushi, and each channel's engine input
    and bus: a move is never live, and the reload it offers says why."""
    agent, world, fake, runner = make_agent(root)
    path = rig_file(root, "guitar_vocal")
    text = path.read_text()
    status, body = call(agent, "POST", "/rigs/guitar_vocal/channels/vocal/move", json={"direction": "up"})
    assert status == 200, body
    assert body["change"] == {"op": "move_channel", "channel": "vocal", "direction": "up", "to": 0,
                              "order": ["vocal", "guitar"]}
    assert channel_names(path) == ["vocal", "guitar"]
    moved = path.read_text()
    assert moved.startswith("# Guitar and vocals as a v2 rig (#30)"), "the file's comments are kept"
    assert "# Gate, compressor, sibilance, tone, space, ceiling" in moved, "and the moved channel's"
    assert moved.index("# Gate, compressor") < moved.index("# The electric_board chain, shortened"), \
        "each chain's comments went with it"
    assert sorted(moved.splitlines()) == sorted(text.splitlines()), "only lines moved"
    assert body["how_reload"] == "restart", body
    codes = [r["code"] for r in body["why"]]
    assert "track_order" in codes and "track" in codes, codes
    status, said = call(agent, "GET", "/undo")
    assert said["next"]["label"] == "move channel vocal" and said["next"]["act"] == "move_channel"
    status, body = reload(agent, restart=False)
    assert status == 409 and body["code"] == "restarts", body
    status, body = reload(agent, restart=True)
    assert status == 200 and body["how"] == "restart", body
    assert world.state["rig"] == "config/rigs/guitar_vocal.yaml"


def test_a_move_past_either_end_or_of_no_channel_writes_nothing(root):
    agent, world, fake, runner = make_agent(root)
    path = rig_file(root, "guitar_vocal")
    before = path.read_bytes()
    status, body = call(agent, "POST", "/rigs/guitar_vocal/channels/guitar/move", json={"direction": "up"})
    assert status == 409 and body["code"] == "edge", body
    assert body["reason"] == "nothing was written: guitar is already guitar_vocal's first channel"
    status, body = call(agent, "POST", "/rigs/guitar_vocal/channels/vocal/move", json={"direction": "down"})
    assert status == 409 and body["code"] == "edge", body
    status, body = call(agent, "POST", "/rigs/guitar_vocal/channels/keys/move", json={"direction": "up"})
    assert status == 409 and body["code"] == "no_channel", body
    assert path.read_bytes() == before


def test_a_move_of_the_running_rig_waits_for_unsaved_work(root):
    agent, world, fake, runner = make_agent(root)
    path = rig_file(root, "guitar_vocal")
    before = path.read_bytes()
    turned = next(iter(fake.values["guitar_eq"]))
    assert call(agent, "POST", "/live/parameter",
                json={"processor": "guitar_eq", "parameter": turned, "value": 0.1})[0] == 200
    status, body = call(agent, "POST", "/rigs/guitar_vocal/channels/vocal/move", json={"direction": "up"})
    assert status == 409 and body["code"] == "unsaved", body
    assert "reloads or restarts from its file" in body["reason"]
    assert path.read_bytes() == before


def test_a_move_of_another_rig_waits_for_its_next_start(root):
    agent, world, fake, runner = make_agent(root)
    another(root, "duo")
    path = rig_file(root, "duo")
    status, body = call(agent, "POST", "/rigs/duo/channels/vocal/move", json={"direction": "up"})
    assert status == 200, body
    assert (body["running"], body["takes_effect"], body["offer"]) == (False, "next start", None)
    assert channel_names(path) == ["vocal", "guitar"]


# --------------------------------------------------------------------------- #
# What plays is recorded where it starts to play (PR #256's review)
# --------------------------------------------------------------------------- #

def tweak(agent, fake, processor, value):
    parameter = next(iter(fake.values[processor]))
    status, body = call(agent, "POST", "/live/parameter",
                        json={"processor": processor, "parameter": parameter, "value": value})
    assert status == 200, body


def undo_newest(agent, **body):
    status, state = call(agent, "GET", "/undo")
    assert state["next"], state
    return call(agent, "POST", "/undo", json={"seq": state["next"]["seq"], **body})


def test_the_config_that_plays_records_the_bytes_it_was_compiled_from(root):
    from sushi_rig.rig import playing_source, source_digest

    agent, world, fake, runner = make_agent(root)
    data = rig_file(root, "guitar_vocal").read_bytes()
    sha, recorded = playing_source(world.configs[world.state["config"]])
    assert sha == source_digest(data) and recorded == data


def test_a_save_with_no_look_before_it_is_what_plays(root):
    """Review finding 2: the save itself records what it wrote."""
    agent, world, fake, runner = make_agent(root)
    tweak(agent, fake, "guitar_eq", 0.1)
    assert call(agent, "POST", "/rig/save", json={})[0] == 200
    assert looked(agent)["changed"] is False


def test_a_save_undone_takes_the_record_back_with_the_file(root):
    """Review findings 1 and 4: undo a save and save again, with no look
    between; the work is unsaved again (the count says it), the file what
    played before the save."""
    agent, world, fake, runner = make_agent(root)
    tweak(agent, fake, "guitar_eq", 0.1)
    assert call(agent, "POST", "/rig/save", json={})[0] == 200
    status, body = undo_newest(agent)
    assert status == 200 and body["said"] == "Undone: save guitar_vocal", body
    said = looked(agent)
    assert said["changed"] is False and said["unsaved"] == 1, said
    tweak(agent, fake, "guitar_eq", 0.3)
    assert call(agent, "POST", "/rig/save", json={})[0] == 200
    said = looked(agent)
    assert said["changed"] is False and said["unsaved"] == 0, said


def test_the_second_of_two_saves_undone_leaves_the_first_as_what_plays(root):
    """Review finding 3."""
    agent, world, fake, runner = make_agent(root)
    path = rig_file(root, "guitar_vocal")
    tweak(agent, fake, "guitar_eq", 0.1)
    assert call(agent, "POST", "/rig/save", json={})[0] == 200
    first = path.read_bytes()
    tweak(agent, fake, "guitar_eq", 0.2)
    assert call(agent, "POST", "/rig/save", json={})[0] == 200
    status, body = undo_newest(agent)
    assert status == 200, body
    assert path.read_bytes() == first
    assert looked(agent)["changed"] is False


def test_a_look_landing_mid_save_never_sees_the_saved_file_as_changed(root):
    """Review finding 7: the record is written in the save's own thread,
    before git is asked anything, so a poll between the write and the save's
    end sees what the save made."""
    import asyncio

    agent, world, fake, runner = make_agent(root)
    path = rig_file(root, "guitar_vocal")
    seen = []
    real = agent._save_write

    async def watched(*args, **kwargs):
        out = await real(*args, **kwargs)
        run = await agent._rigfile_run(look=False)
        seen.append((await asyncio.to_thread(agent._file_state, run, path))["changed"])
        return out

    agent._save_write = watched
    tweak(agent, fake, "guitar_eq", 0.1)
    assert call(agent, "POST", "/rig/save", json={})[0] == 200
    assert seen == [False]
    assert looked(agent)["changed"] is False


def test_a_reload_after_a_save_over_an_unplayed_edit_cannot_be_undone(root):
    """Review finding 4's fold: a move, a knob, a save (which writes the
    move and the knob: not what plays, so not recorded), then a reload.
    Putting back what played before would lose what the save wrote, so the
    reload's step says it cannot be undone, and the save's stays."""
    agent, world, fake, runner = make_agent(root)
    path = rig_file(root, "guitar_vocal")
    assert call(agent, "POST", "/rigs/guitar_vocal/channels/vocal/move", json={"direction": "up"})[0] == 200
    tweak(agent, fake, "guitar_eq", 0.1)
    assert call(agent, "POST", "/rig/save", json={})[0] == 200
    saved = path.read_bytes()
    assert looked(agent)["changed"] is True, "the move is still not played"
    status, body = reload(agent, restart=True)
    assert status == 200 and body["how"] == "restart", body
    status, state = call(agent, "GET", "/undo")
    assert state["next"]["label"] == "reload guitar_vocal" and state["next"]["can"] is False, state
    assert "written since it was last what guitar_vocal played" in state["next"]["why"]
    status, body = undo_newest(agent, restart=True)
    assert status == 409 and body["code"] == "cant_undo", body
    assert path.read_bytes() == saved, "nothing put back"
    assert call(agent, "GET", "/undo")[1]["next"]["label"] == "save guitar_vocal"


def test_a_hand_edit_before_the_agent_looked_is_not_what_a_reloads_undo_puts_back(root):
    """Review finding 4's other half: a hand edit no look saw, a move, a
    reload. Its Undo puts back the bytes the rig was compiled from, not the
    hand-edited file that never played, and switches the rig into them."""
    agent, world, fake, runner = make_agent(root)
    path = rig_file(root, "guitar_vocal")
    before = path.read_bytes()
    turned = next(iter(fake.values["guitar_overdrive"]))
    hand_edit(path, turned, 0.25)
    assert call(agent, "POST", "/rigs/guitar_vocal/channels/vocal/move", json={"direction": "up"})[0] == 200
    assert reload(agent, restart=True)[0] == 200
    status, body = undo_newest(agent, restart=True)
    assert status == 200 and body["how"] == "switched", body
    assert path.read_bytes() == before
    assert channel_names(path) == ["guitar", "vocal"]
    assert call(agent, "GET", "/undo")[1]["next"] is None, "the move was in the reload's step"
    # (What the rig then plays is the switch's: the fake runner's `up` starts
    # nothing, so it is not read off the fake here.)


def test_another_agent_over_the_same_run_knows_what_plays(root):
    """Persisted with the running config: an agent started after a hand
    edit says File changed, and its reload's Undo puts back what played."""
    from sushi_rig import midi_listener
    from sushi_rig.agent import Agent

    agent, world, fake, runner = make_agent(root)
    path = rig_file(root, "guitar_vocal")
    before = path.read_bytes()
    turned = next(iter(fake.values["guitar_overdrive"]))
    hand_edit(path, turned, 0.25)
    later = Agent(root=root, describe=world.described, switching=world.switching(), live=fake, run=runner,
                  midi=midi_listener.World(plan=lambda described, root: midi_listener.Off("no pedals here")))
    said = looked(later)
    assert said["changed"] is True and said["how"] == "live", said
    status, body = reload(later, restart=False)
    assert status == 200 and body["how"] == "live", body
    status, body = undo_newest(later, restart=False)
    assert status == 200 and body["how"] == "switched", body
    assert path.read_bytes() == before


def test_a_machine_file_fixed_since_the_last_look_is_looked_at_again(root):
    """Review finding 5: the look is kept by the machine file's contents,
    not its path."""
    agent, world, fake, runner = make_agent(root)
    path = rig_file(root, "guitar_vocal")
    path.write_text(path.read_text().replace("input: vocal-in", "input: keys-in"))
    said = looked(agent)
    assert said["code"] == "rig_problem" and "keys-in" in said["problem"], said
    machine = world.machine
    text = machine.read_text()
    vocal = text[text.index("  vocal-in:"):]
    block = vocal[:vocal.index("\n  ", 3) if "\n  #" not in vocal[:200] else vocal.index("\n  #")]
    machine.write_text(text.replace("roles:\n", "roles:\n" + block.replace("vocal-in:", "keys-in:", 1) + "\n", 1))
    said = looked(agent)
    assert said["problem"] is None and said["changed"] is True and said["how"] == "restart", said


def test_the_look_takes_the_amps_last_wiring_and_the_reload_a_fresh_one(root):
    """Review finding 6: the page asks every three seconds, and a fresh look
    at an unwired amp is a `pw-link -l` each time: the GET takes the last
    look, as /health does; the POST, which decides a restart, looks again."""
    agent, world, fake, runner = make_agent(root)
    asked = []

    async def wiring(described, *, fresh=False, wait=True):
        asked.append({"fresh": fresh, "wait": wait})
        return {"wired": False, "problems": ["not linked"]}

    agent._amp_wiring = wiring
    looked(agent)
    looked(agent)
    assert asked == [{"fresh": False, "wait": False}] * 2
    asked.clear()
    reload(agent, restart=False)
    assert {"fresh": True, "wait": True} in asked, asked
