"""A channel removed from the running rig through the agent (#190): POST
/rig/channels/<channel>/remove.

Against real files in a temporary checkout, as test_agent_channel_load.py
runs a load: the frozen rigs, their machine file and the real compiler. The
running rig is `test_agent.RigLive`, a fake Sushi loaded from that compile,
which the agent's real live switch drives, its `remove_track` standing in
for Sushi's (live.py's `remove_live_track`), so "the rig plays it" is a
reading of the fake after the removal, not a word in an answer.

NOT shown: any of it against a live Sushi, and above all Sushi deleting a
track while it plays.
"""

from __future__ import annotations

import asyncio

import pytest
import yaml

pytest.importorskip("aiohttp")
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from sushi_rig import rigedit, rigfile  # noqa: E402

from test_agent_channel_load import (  # noqa: E402,F401
    _agents_release_their_pools, call, calls, git, make_agent, plugins_of, rig_file, root, with_midi,
)

GUITAR = ["guitar_compressor", "guitar_overdrive", "guitar_eq", "guitar_chorus", "guitar_delay", "guitar_room"]
VOCAL = ["vocal_dynamics", "vocal_deess", "vocal_eq", "vocal_reverb", "vocal_limiter"]


def remove(agent, channel, **body):
    return call(agent, "POST", f"/rig/channels/{channel}/remove", json=body)


def counting_syncs(agent):
    """`_midi_sync` counted: the listener, and its links to Sushi, follow the rig."""
    syncs, real = [], agent._midi_sync

    async def counting():
        syncs.append(1)
        await real()

    agent._midi_sync = counting
    return syncs


# --------------------------------------------------------------------------- #
# The request's shape, and what in a MIDI map names a channel (pure)
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("body", [[], "vocal", {"channel": "vocal"}, {"rig": ""}, {"rig": 3},
                                  {"discard": "yes"}, {"drop_midi": 1}, {"restart": "no"}])
def test_a_removal_takes_four_optional_keys_and_nothing_else(body):
    with pytest.raises(rigedit.EditRequestError):
        rigedit.remove_request(body)
    assert rigedit.remove_request(None) == {"rig": None, "discard": False, "drop_midi": False, "restart": None}
    assert rigedit.remove_request({"rig": "r", "discard": True, "drop_midi": True, "restart": False}) == {
        "rig": "r", "discard": True, "drop_midi": True, "restart": False}


def test_what_names_a_channel_is_its_plugins_mute_volume_and_every_blend_it_is_in():
    midi = {"controller": "c", "controls": [
        {"control": "a", "bypass": "vocal/vocal_eq"},
        {"control": "b", "parameter": "vocal/vocal_eq/High"},
        {"control": "c", "mute": "vocal"},
        {"control": "d", "volume": "vocal"},
        {"control": "e", "blend": ["guitar", "vocal"]},
        {"control": "f", "bypass": "guitar/guitar_eq"},
        {"control": "g", "mute": "vocalist"},
        {"control": "h", "parameter": "vocalist/x/y"},
        "not a control", {"control": "i"},
    ]}
    named = rigedit.midi_naming(midi, "vocal")
    assert [n["control"] for n in named] == ["a", "b", "c", "d", "e"]
    assert named[4] == {"control": "e", "binding": "blend", "target": ["guitar", "vocal"],
                        "why": "a blend needs two channels"}
    assert rigedit.named_said(named[0]) == "a (bypass vocal/vocal_eq): its plugin goes with the channel"
    assert rigedit.named_said(named[4]) == "e (blend guitar, vocal): a blend needs two channels"
    assert rigedit.midi_naming(None, "vocal") == [] and rigedit.midi_naming({"controls": None}, "vocal") == []
    raw = {"name": "r", "channels": [{"name": "guitar"}, {"name": "vocal", "chain": []}], "midi": midi}
    out, entry = rigedit.without_channel(raw, "vocal")
    assert [c["name"] for c in out["channels"]] == ["guitar"] and entry == {"name": "vocal", "chain": []}
    assert len(raw["channels"]) == 2 and out["midi"] == midi, "pure, and the map is left to the caller"
    with pytest.raises(LookupError, match="has no channel called keys"):
        rigedit.without_channel(raw, "keys")


def test_a_track_is_removed_live_only_where_the_caller_names_it():
    """`rigfile.diff`'s `remove_tracks`: the track named is one step, its
    processors with it, while any other missing track, and every switch
    that names none, is the restart it always was."""
    import copy

    def config(*names):
        tracks = []
        for i, name in enumerate(names):
            tracks.append({"name": name, "channels": 2, "inputs": [{"engine_channel": i, "track_channel": 0}],
                           "outputs": [{"engine_bus": i + 1, "track_bus": 0}],
                           "plugins": [{"name": f"{name}_eq", "type": "lv2", "uri": "urn:eq"},
                                       {"name": f"{name}_send", "type": "internal", "uid": rigfile.SEND_UID}]})
        tracks.append({"name": "master", "channels": 2, "inputs": [], "outputs": [{"engine_bus": 0, "track_bus": 0}],
                       "plugins": [{"name": "master_return", "type": "internal", "uid": rigfile.RETURN_UID}]})
        wiring = {n: {"source": f"{n}-in", "sink": None} for n in names} | {"master": {"source": None, "sink": "out"}}
        return {"tracks": tracks, "initial_state": [], "host_config": {}, rigfile.WIRING: wiring}

    running, upcoming = config("a", "b"), config("a")
    assert rigfile.diff(running, upcoming).codes == ["track_removed"]
    assert rigfile.diff(running, upcoming, remove_tracks=("c",)).codes == ["track_removed"]
    plan = rigfile.diff(running, upcoming, remove_tracks=("b",))
    assert [s.as_dict() for s in plan.steps] == [{"op": "remove_track", "track": "b"}]
    assert rigfile.apply_to_chains({"a": ["a_eq"], "b": ["b_eq"]}, plan) == {"a": ["a_eq"]}
    # The first of two: the second's engine input moves, which only a restart does.
    assert "track" in rigfile.diff(running, config("b"), remove_tracks=("a",)).codes
    # Never the master.
    masterless = copy.deepcopy(upcoming)
    masterless["tracks"] = [t for t in masterless["tracks"] if t["name"] != "master"]
    assert "track_removed" in rigfile.diff(upcoming, masterless, remove_tracks=("master",)).codes


# --------------------------------------------------------------------------- #
# A removal, live and by restart
# --------------------------------------------------------------------------- #

def test_the_last_channel_of_the_file_is_removed_live_its_chain_and_send_with_it(root):
    agent, world, fake, runner = make_agent(root)
    syncs = counting_syncs(agent)
    path = rig_file(root, "guitar_vocal")
    before = path.read_text()
    status, body = remove(agent, "vocal", rig="guitar_vocal", restart=False)
    assert status == 200, body
    assert body["how"] == "live" and body["plan"] == [{"op": "remove_track", "track": "vocal"}], body
    assert runner.calls == [], "nothing was restarted"
    assert fake.tracks == ["guitar", "master"] and "vocal" not in fake.chains, "the rig plays it: the fake"
    assert not any(name.startswith("vocal_") for name in fake.plugins), "its chain and its send went with it"
    assert plugins_of(fake, "guitar") == GUITAR, "the other channel is as it was"
    assert body["removed"] == {"plugins": VOCAL, "send": "vocal_send"} and body["dropped"] == []
    written = yaml.safe_load(path.read_text())
    assert [c["name"] for c in written["channels"]] == ["guitar"]
    assert written["channels"][0] == yaml.safe_load(before)["channels"][0]
    text = path.read_text()
    assert text.startswith("# Guitar and vocals as a v2 rig (#30)"), "the file's comments are kept"
    assert "vocal" not in text.split("master:")[0].split("channels:")[1], "the vocal's lines are gone"
    assert body["entry"]["op"] == "rig" and body["entry"]["args"]["remove_channel"] == "vocal"
    assert body["kept_before"]["id"], "the file as it was is a revert point"
    assert world.state["rig"] == "config/rigs/guitar_vocal.yaml"
    assert syncs, "the pedals' listener follows the rig after"
    # The removal is a baseline: nothing unsaved after it, and the live undo stops at it.
    assert call(agent, "POST", "/live/undo")[0] == 409


def test_a_channel_in_front_of_another_is_a_restart_asked_first(root):
    """guitar is the first channel: without it the vocal's engine input moves,
    which Sushi takes only at load. With `"restart": false` it is asked
    about first (`restarts`, nothing written); yes is a restart."""
    agent, world, fake, runner = make_agent(root)
    path = rig_file(root, "guitar_vocal")
    before = path.read_bytes()
    status, body = remove(agent, "guitar", restart=False)
    assert status == 409 and body["code"] == "restarts", body
    assert "track" in [r["code"] for r in body["why"]], body
    assert body["reason"].startswith("nothing was written: removing guitar restarts Sushi")
    assert path.read_bytes() == before and runner.calls == [] and "guitar" in fake.chains
    assert call(agent, "GET", "/undo")[1]["next"] is None, "nothing to undo: nothing happened"
    status, body = remove(agent, "guitar", restart=True)
    assert status == 200, body
    assert body["how"] == "restart" and "track" in [r["code"] for r in body["why"]], body
    assert runner.calls and runner.calls[0][0] == "down"
    assert [c["name"] for c in yaml.safe_load(path.read_text())["channels"]] == ["vocal"]


def test_a_channel_added_to_the_file_and_not_yet_playing_is_removed_live_with_nothing_to_do(root):
    agent, world, fake, runner = make_agent(root)
    path = rig_file(root, "guitar_vocal")
    status, body = call(agent, "POST", "/rigs/guitar_vocal/channels/add",
                        json={"name": "keys", "input": "vocal-in", "width": "mono"})
    assert status == 200 and body["takes_effect"] == "restart", body
    added = path.read_bytes()
    status, body = remove(agent, "keys", restart=False)
    assert status == 200 and body["how"] == "live" and body["plan"] == [], body
    assert fake.tracks == ["guitar", "vocal", "master"] and runner.calls == []
    assert [c["name"] for c in yaml.safe_load(path.read_text())["channels"]] == ["guitar", "vocal"]
    assert path.read_bytes() != added


# --------------------------------------------------------------------------- #
# Refusals: nothing written
# --------------------------------------------------------------------------- #

def test_the_rigs_last_channel_is_refused(root):
    agent, world, fake, runner = make_agent(root, rig="accustomed")
    path = rig_file(root, "accustomed")
    before = path.read_bytes()
    status, body = remove(agent, "board")
    assert status == 409 and body["code"] == "last_channel", body
    assert "board is accustomed's only channel" in body["reason"]
    assert path.read_bytes() == before and runner.calls == []


def test_a_channel_the_rig_does_not_have_another_rig_and_a_bad_body_are_refused(root):
    agent, world, fake, runner = make_agent(root)
    before = rig_file(root, "guitar_vocal").read_bytes()
    status, body = remove(agent, "keys")
    assert status == 409 and body["code"] == "no_channel" and "has no channel called keys" in body["reason"], body
    status, body = remove(agent, "vocal", rig="accustomed")
    assert status == 409 and body["code"] == "changed", body
    assert "the running rig is guitar_vocal, not accustomed" in body["reason"]
    status, body = call(agent, "POST", "/rig/channels/vocal/remove", json={"channel": "vocal"})
    assert status == 400, body
    world.state["mode"] = "panel"
    status, body = remove(agent, "vocal")
    assert status == 409 and body["code"] == "panel", body
    assert rig_file(root, "guitar_vocal").read_bytes() == before and fake.tracks == ["guitar", "vocal", "master"]


def test_no_rig_up_is_refused(root):
    agent, world, fake, runner = make_agent(root)
    world.described = lambda: {"verdict": "absent", "running": False, "message": "No rig is running."}
    agent.describe = world.described
    status, body = remove(agent, "vocal")
    assert status == 409 and body["code"] == "no_rig", body


def test_unsaved_work_is_refused_and_dropped_with_discard(root):
    agent, world, fake, runner = make_agent(root)
    before = rig_file(root, "guitar_vocal").read_bytes()
    status, said = call(agent, "POST", "/live/bypass", json={"processor": "guitar_eq", "bypassed": True})
    assert status == 200, said
    status, body = remove(agent, "vocal")
    assert status == 409 and body["code"] == "unsaved", body
    assert "removing vocal writes the rig file and plays it" in body["reason"]
    assert 'remove it with discard ({"discard": true})' in body["reason"]
    assert rig_file(root, "guitar_vocal").read_bytes() == before
    status, body = remove(agent, "vocal", discard=True)
    assert status == 200 and body["how"] == "live" and body["discarded"] is True, body
    assert fake.bypassed["guitar_eq"] is False, "the dropped bypass is put back by the switch"
    assert body["discard"]["changes"] == 1


def test_a_busy_agent_is_refused(root):
    """The lifecycle lock, as every rig file edit takes it."""
    agent, world, fake, runner = make_agent(root)

    async def held():
        async with agent._lock:
            agent.busy = "switch"
            async with TestClient(TestServer(agent.app())) as client:
                response = await client.post("/rig/channels/vocal/remove", json={})
                return response.status, await response.json()

    status, body = asyncio.run(held())
    assert status == 409 and body["code"] == "busy", body
    assert "vocal" in yaml.safe_load(rig_file(root, "guitar_vocal").read_text())["channels"][1]["name"]


def test_another_sites_page_is_refused_before_the_route(root):
    """#161's guard, as in front of every route that changes something."""
    agent, world, fake, runner = make_agent(root)
    before = rig_file(root, "guitar_vocal").read_bytes()

    async def go():
        async with TestClient(TestServer(agent.app())) as client:
            response = await client.post("/rig/channels/vocal/remove", json={},
                                         headers={"Origin": "http://elsewhere.example", "Sec-Fetch-Site": "cross-site"})
            return response.status, await response.json()

    status, body = asyncio.run(go())
    assert status == 403 and body["code"] == "cross_site", body
    assert rig_file(root, "guitar_vocal").read_bytes() == before and fake.tracks == ["guitar", "vocal", "master"]


# --------------------------------------------------------------------------- #
# The MIDI map
# --------------------------------------------------------------------------- #

def test_controls_on_the_channel_are_refused_and_dropped_with_drop_midi(root):
    """A bypass of the vocal's EQ and the vocal's mute name it; the guitar's
    pad does not. Refused, each named; dropped with `drop_midi`, the rest of
    the map kept. Pads are the agent's own bindings, not Sushi's, so the
    map changing is no restart."""
    with_midi(root, "guitar_vocal", [{"control": "pad1", "bypass": "vocal/vocal_eq"},
                                     {"control": "pad2", "mute": "vocal"},
                                     {"control": "pad3", "bypass": "guitar/guitar_eq"}])
    agent, world, fake, runner = make_agent(root)
    syncs = counting_syncs(agent)
    path = rig_file(root, "guitar_vocal")
    before = path.read_bytes()
    status, body = remove(agent, "vocal", restart=False)
    assert status == 409 and body["code"] == "midi", body
    assert [m["control"] for m in body["mappings"]] == ["pad1", "pad2"]
    assert body["reason"].startswith("nothing was written: 2 controls of guitar_vocal's MIDI map name vocal")
    assert "pad1 (bypass vocal/vocal_eq): its plugin goes with the channel" in body["reason"]
    assert path.read_bytes() == before and "vocal" in fake.chains
    status, body = remove(agent, "vocal", restart=False, drop_midi=True)
    assert status == 200 and body["how"] == "live", body
    assert [d["control"] for d in body["dropped"]] == ["pad1", "pad2"]
    written = yaml.safe_load(path.read_text())
    assert written["midi"]["controls"] == [{"control": "pad3", "bypass": "guitar/guitar_eq"}]
    assert written["midi"]["controller"] == "mpd218"
    assert syncs, "the listener follows the rig, and its bindings with it"


def test_a_blend_of_the_channel_and_another_is_refused_and_dropped_by_restart(root):
    """A blend needs two channels, so it goes with either. A blend is Sushi's
    own mapping, loaded only with the config: dropping it is a restart,
    asked about first."""
    with_midi(root, "guitar_vocal", [{"control": "knob1", "blend": ["guitar", "vocal"]}])
    agent, world, fake, runner = make_agent(root)
    path = rig_file(root, "guitar_vocal")
    before = path.read_bytes()
    status, body = remove(agent, "vocal", restart=False)
    assert status == 409 and body["code"] == "midi", body
    assert body["mappings"] == [{"control": "knob1", "binding": "blend", "target": ["guitar", "vocal"],
                                 "why": "a blend needs two channels"}]
    status, body = remove(agent, "vocal", restart=False, drop_midi=True)
    assert status == 409 and body["code"] == "restarts", body
    assert "midi" in [r["code"] for r in body["why"]]
    assert path.read_bytes() == before
    status, body = remove(agent, "vocal", restart=True, drop_midi=True)
    assert status == 200 and body["how"] == "restart", body
    assert yaml.safe_load(path.read_text())["midi"]["controls"] == []


# --------------------------------------------------------------------------- #
# One Undo (#209)
# --------------------------------------------------------------------------- #

def test_a_live_removal_is_one_undo_step_the_file_back_and_the_rig_restarted_into_it(root):
    """The file's bytes back, and the rig switched into it. The live
    operations make no tracks, so putting the channel back is a restart,
    asked about first (`restarts`) as the page's "Restart?" asks."""
    agent, world, fake, runner = make_agent(root)
    path = rig_file(root, "guitar_vocal")
    before = path.read_bytes()
    assert remove(agent, "vocal", restart=False)[0] == 200
    status, state = call(agent, "GET", "/undo")
    assert status == 200, state
    assert state["next"]["label"] == "remove channel vocal" and state["next"]["act"] == "remove_channel", state
    syncs = counting_syncs(agent)
    status, body = call(agent, "POST", "/undo", json={"seq": state["next"]["seq"], "restart": False})
    assert status == 409 and body["code"] == "restarts", body
    assert "track_added" in [r["code"] for r in body["why"]]
    assert path.read_bytes() != before, "nothing put back yet"
    status, body = call(agent, "POST", "/undo", json={"seq": state["next"]["seq"], "restart": True})
    assert status == 200, body
    assert body["said"] == "Undone: remove channel vocal" and body["how"] == "switched", body
    assert path.read_bytes() == before, "the file as it was, byte for byte"
    assert runner.calls and runner.calls[0][0] == "down", "the rig restarted into it"
    assert syncs, "and the pedals follow it"
    assert body["next"] is None


def test_a_removal_with_its_midi_dropped_is_undone_with_its_map(root):
    with_midi(root, "guitar_vocal", [{"control": "pad2", "mute": "vocal"}])
    agent, world, fake, runner = make_agent(root)
    path = rig_file(root, "guitar_vocal")
    before = path.read_bytes()
    assert remove(agent, "vocal", drop_midi=True)[0] == 200
    status, body = call(agent, "POST", "/undo", json={"restart": True})
    assert status == 200, body
    assert path.read_bytes() == before
    assert yaml.safe_load(path.read_text())["midi"]["controls"] == [{"control": "pad2", "mute": "vocal"}]


def test_a_removal_changed_since_cannot_be_undone(root):
    agent, world, fake, runner = make_agent(root)
    path = rig_file(root, "guitar_vocal")
    assert remove(agent, "vocal")[0] == 200
    path.write_text(path.read_text() + "# a hand edit\n")
    status, body = call(agent, "POST", "/undo", json={"restart": True})
    assert status == 409 and body["code"] == "cant_undo" and "has changed since" in body["reason"], body
