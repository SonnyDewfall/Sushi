"""The agent's library and chain edits (#32): `GET /library`, `POST /live/add`
and `POST /live/remove`.

Its own module so the routes `test_agent.py` covers and these can change
without meeting in a merge; the fake Sushi here copies that file's pattern
(one numbered connection per request, every call checked against it) and adds
what an edit needs: the chains with their ids, each processor's plugin class,
and a graph that changes when told to.

The rig the edits run against is a compiled one: a channel `guitar` whose
chain ends in its send, and `master` whose chain starts with the return. That
shape is the compiler's (`rigfile.compile_rig`); the names and ids are invented.
"""

from __future__ import annotations

import asyncio
import copy
from dataclasses import replace
from pathlib import Path

import pytest

aiohttp = pytest.importorskip("aiohttp")
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from sushi_rig.agent import (  # noqa: E402
    AddRequest,
    Agent,
    AgentError,
    Conflict,
    Outcome,
    Unconfirmed,
    add_request,
    check_add,
    check_remove,
    confirm_chain,
    derive_name,
    remove_request,
)
from sushi_rig.library import build_library, read_bundle, read_library  # noqa: E402
from sushi_rig.agent import _track_of, check_move  # noqa: E402
from sushi_rig.live import LiveError, decide_move, protected_processor  # noqa: E402
from sushi_rig.paths import checkout_root  # noqa: E402

ROOT = checkout_root()
FIXTURES = Path(__file__).parent / "fixtures" / "lv2"
GXTS9 = "http://guitarix.sourceforge.net/plugins/gxts9#ts9sim"
DELAY = "http://drobilla.net/plugins/mda/Delay"
NAM = "http://github.com/mikeoliphant/neural-amp-modeler-lv2"
UP = {"verdict": "running", "running": True, "config_name": "guitar_vocal"}

CHAINS = {
    "guitar": ["comp", "drive", "guitar_send"],
    "master": ["master_return", "limiter"],
    "aux": [],
}
IDS = {"guitar": 1, "comp": 2, "drive": 3, "guitar_send": 4, "master": 5,
       "master_return": 6, "limiter": 7, "aux": 8}
CLASSES = {
    "comp": {"uri": "http://guitarix.sourceforge.net/plugins/gx_compressor#_compressor",
             "uid": None, "type": "lv2"},
    "drive": {"uri": GXTS9, "uid": None, "type": "lv2"},
    "guitar_send": {"uri": None, "uid": "sushi.testing.send", "type": "internal"},
    "master_return": {"uri": None, "uid": "sushi.testing.return", "type": "internal"},
    "limiter": {"uri": "http://drobilla.net/plugins/mda/Limiter", "uid": None, "type": "lv2"},
}


def reading():
    return {"chains": copy.deepcopy(CHAINS), "ids": dict(IDS),
            "uids": {name: c["uid"] for name, c in CLASSES.items()}}


def removal_reading(processor):
    on_a_chain = any(processor in chain for chain in CHAINS.values())
    return {**reading(), "plugin": CLASSES.get(processor) if on_a_chain else None}


def the_library():
    """The real manifest, with the captured gxts9 and mda bundles read."""
    found = {**read_bundle(FIXTURES / "gxts9.lv2")[0], **read_bundle(FIXTURES / "mda.lv2")[0]}
    from sushi_rig.plugins import read_manifest

    return build_library(read_manifest((ROOT / "plugin-manifest.txt").read_text()), found, {})


LIBRARY = the_library()


# --------------------------------------------------------------------------- #
# The decisions
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("plugin_name, expected", [
    ("GxTubeScreamer", "gxtubescreamer"),
    ("MDA Delay", "mda_delay"),
    ("L/C/R Delay", "l_c_r_delay"),
    ("3 Band EQ", "3_band_eq"),
    ("Rubber Band R3 Mono Pitch Shifter", "rubber_band_r3_mono_pitch_shifter"),
    ("  --Ünïcode--  ", "n_code"),
    ("x" * 80, "x" * 64),
    ("a" * 63 + " b", "a" * 63),
    ("!!!", ""),
])
def test_a_name_derived_from_the_plugins_is_a_config_name(plugin_name, expected):
    assert derive_name(plugin_name) == expected


def test_an_add_request_is_a_track_a_manifest_uri_and_perhaps_a_name_and_a_place():
    request = add_request({"track": "guitar", "uri": GXTS9}, LIBRARY)
    assert (request.track, request.uri, request.name, request.after, request.named) == \
        ("guitar", GXTS9, "gxtubescreamer", None, False)
    request = add_request({"track": "guitar", "uri": DELAY, "name": "slap", "after": "comp"}, LIBRARY)
    assert (request.name, request.after, request.named) == ("slap", "comp", True)
    assert add_request({"track": "guitar", "uri": DELAY, "after": None}, LIBRARY).after is None
    assert add_request({"track": "guitar", "uri": DELAY}, LIBRARY).name == "mda_delay"


@pytest.mark.parametrize("body, why", [
    ({"uri": GXTS9}, "'track' is required"),
    ({"track": "guitar"}, "'uri' is required"),
    ({"track": "guitar", "uri": 7}, "non-empty string"),
    ({"track": "guitar", "uri": "http://example.org/not-ours"}, "not in plugin-manifest.txt"),
    ({"track": "guitar", "uri": NAM}, "Carla"),
    ({"track": "guitar", "uri": GXTS9, "name": "Drive!"}, "must match"),
    ({"track": "guitar", "uri": GXTS9, "name": ""}, "must match"),
    ({"track": "guitar", "uri": GXTS9, "name": 3}, "must match"),
    ({"track": "guitar", "uri": GXTS9, "after": ""}, "'after'"),
    ({"track": "guitar", "uri": GXTS9, "after": 2}, "'after'"),
    (["guitar", GXTS9], "JSON object"),
])
def test_an_add_request_that_is_not_that_is_refused(body, why):
    with pytest.raises(AgentError, match=why) as caught:
        add_request(body, LIBRARY)
    assert not isinstance(caught.value, Conflict), "a bad request, not a conflict with the rig"


def test_a_plugin_whose_name_gives_nothing_must_be_named():
    library = build_library(["urn:x:!!!"], {"urn:x:!!!": {"name": "!!!", "ports": 2, "bundle": "x.lv2"}}, {})
    with pytest.raises(AgentError, match="give it a 'name'"):
        add_request({"track": "guitar", "uri": "urn:x:!!!"}, library)


def request(name="new", after=None, track="guitar", uri=GXTS9):
    return add_request({"track": track, "uri": uri, "name": name, "after": after}, LIBRARY)


def test_an_add_goes_before_the_send_by_default_since_the_send_must_stay_last():
    addition = check_add(reading(), request())
    assert addition.chain == ["comp", "drive", "guitar_send"]
    assert addition.expected == ["comp", "drive", "new", "guitar_send"]
    assert (addition.track_id, addition.before_id, addition.add_to_back) == (1, 4, False)


@pytest.mark.parametrize("track, after, expected, command", [
    ("guitar", "comp", ["comp", "new", "drive", "guitar_send"], (1, 3, False)),
    ("guitar", "drive", ["comp", "drive", "new", "guitar_send"], (1, 4, False)),
    ("master", None, ["master_return", "limiter", "new"], (5, 0, True)),
    ("master", "master_return", ["master_return", "new", "limiter"], (5, 7, False)),
    ("aux", None, ["new"], (8, 0, True)),
])
def test_after_names_the_processor_it_follows(track, after, expected, command):
    """Sushi places by "before this one" or "at the back", so "after X" is
    before X's successor, or the back when X is last. The master's return is
    first and stays first: nothing is ever placed before something."""
    addition = check_add(reading(), request(track=track, after=after))
    assert addition.expected == expected
    assert (addition.track_id, addition.before_id, addition.add_to_back) == command


@pytest.mark.parametrize("kwargs, why", [
    ({"after": "guitar_send"}, "send must stay last"),
    ({"track": "bass"}, r"no track 'bass': it has \['aux', 'guitar', 'master'\]"),
    ({"after": "limiter"}, r"no processor 'limiter' on track 'guitar': it has \['comp', 'drive', 'guitar_send'\]"),
    ({"name": "drive"}, "'drive' is already on the rig, on track 'guitar'"),
    ({"name": "limiter"}, "already on the rig, on track 'master'"),
    ({"name": "master"}, "'master' is a track"),
])
def test_an_add_the_rig_cannot_take_is_a_conflict(kwargs, why):
    with pytest.raises(Conflict, match=why):
        check_add(reading(), request(**kwargs))


def test_a_derived_name_that_clashes_says_to_give_one():
    derived = AddRequest(track="guitar", uri=GXTS9, name="drive", after=None, named=False)
    with pytest.raises(Conflict, match="give a 'name'"):
        check_add(reading(), derived)


def test_a_remove_names_one_processor():
    assert remove_request({"processor": "comp"}) == "comp"
    for body in ({}, {"processor": ""}, {"processor": 3}, [1]):
        with pytest.raises(AgentError):
            remove_request(body)


def test_a_remove_records_what_undo_needs_to_put_it_back():
    removal = check_remove(removal_reading("drive"), "drive")
    assert (removal.track, removal.chain, removal.expected) == \
        ("guitar", ["comp", "drive", "guitar_send"], ["comp", "guitar_send"])
    assert removal.plugin == {"uri": GXTS9, "uid": None, "type": "lv2"}
    assert (removal.processor_id, removal.track_id) == (3, 1)


@pytest.mark.parametrize("processor, why", [
    ("guitar_send", "the compiler's"),
    ("master_return", "the compiler's"),
    ("guitar", "is a track"),
    ("delay", r"no processor 'delay': it has \['comp', 'drive', 'guitar_send', 'limiter', 'master_return'\]"),
])
def test_a_remove_the_rig_cannot_take_is_a_conflict(processor, why):
    with pytest.raises(Conflict, match=why):
        check_remove(removal_reading(processor), processor)


def test_a_send_is_known_by_its_uid_as_well_as_its_name():
    """A send renamed by hand (or a compiler that names them differently one
    day) is still the send: the uid Sushi reports is what makes it one."""
    state = removal_reading("comp")
    state["plugin"] = {"uri": None, "uid": "sushi.testing.send", "type": "internal"}
    with pytest.raises(Conflict, match="the compiler's"):
        check_remove(state, "comp")


def test_a_remove_whose_plugin_sushi_will_not_name_is_not_made():
    """Undo re-adds from the log; a remove that could not say what it removed
    could not be undone, so it is refused, and as Sushi's failure (502)."""
    state = removal_reading("comp")
    state["plugin"] = None
    with pytest.raises(Unconfirmed, match="could not say which plugin"):
        check_remove(state, "comp")
    state["plugin"] = {"uri": None, "uid": None, "type": "lv2"}
    with pytest.raises(Unconfirmed, match="could not say which plugin"):
        check_remove(state, "comp")


def test_a_chain_read_back_confirms_the_edit_or_says_what_it_found():
    confirm_chain({"guitar": ["a", "b"]}, "guitar", ["a", "b"], "added 'b' to 'guitar'")
    with pytest.raises(Unconfirmed, match=r"read back \['b', 'a'\], not \['a', 'b'\]"):
        confirm_chain({"guitar": ["b", "a"]}, "guitar", ["a", "b"], "added 'b' to 'guitar'")
    with pytest.raises(Unconfirmed, match="no track 'guitar'"):
        confirm_chain({}, "guitar", ["a"], "added 'a' to 'guitar'")


# --------------------------------------------------------------------------- #
# The routes, against a fake Sushi
# --------------------------------------------------------------------------- #

@pytest.fixture(autouse=True)
def _agents_release_their_pools(monkeypatch):
    """As in test_agent.py: an Agent's pool of Sushi threads, left alive, makes
    a later test that forks fork a threaded process."""
    made = []
    original = Agent.__init__

    def recording(self, *args, **kwargs):
        original(self, *args, **kwargs)
        made.append(self)

    monkeypatch.setattr(Agent, "__init__", recording)
    yield
    for agent in made:
        agent._executor.shutdown(wait=True, cancel_futures=True)


def call(agent, method, path, **kwargs):
    async def go():
        async with TestClient(TestServer(agent.app())) as client:
            response = await client.request(method, path, **kwargs)
            return response.status, await response.json()

    return asyncio.run(go())


class EditingLive:
    """A Sushi whose graph an add or a remove changes. `misplace` makes it put
    an added processor at the back whatever it was asked (Sushi confirming a
    command and doing something else); `keep` makes a remove leave it there."""

    def __init__(self):
        self.calls = []
        self.opened = []
        self.closed = []
        self.chains = copy.deepcopy(CHAINS)
        self.ids = dict(IDS)
        self.classes = copy.deepcopy(CLASSES)
        self.fail = None
        self.fail_on = None
        self.misplace = False
        self.keep = False
        self.walk_without_chains = False  # a read-back with no "chains" key

    def _enter(self, name, connection, *args):
        assert connection in self.opened and connection not in self.closed, \
            f"{name} was not given the request's open connection"
        self.calls.append((name, *args))
        if self.fail is not None and self.fail_on in (None, name):
            raise self.fail

    def connect(self):
        connection = len(self.opened) + 1
        self.opened.append(connection)
        return connection

    def disconnect(self, connection):
        self.closed.append(connection)

    def walk(self, connection, uids=False):
        self._enter("walk", connection, uids)
        reading = {"chains": copy.deepcopy(self.chains), "ids": dict(self.ids)}
        if uids:
            reading["uids"] = self.uids()
        if self.walk_without_chains and not uids:
            del reading["chains"]
        return reading

    def uids(self):
        return {name: (c or {}).get("uid") for name, c in self.classes.items()}

    def plan_move(self, connection, processor, direction):
        """As `live.plan_live_move(uids=True)`: the walk and the session's uids."""
        self._enter("plan_move", connection, processor, direction)
        return decide_move(copy.deepcopy(self.chains), dict(self.ids), processor, direction, self.uids())

    def send_move(self, connection, move):
        self._enter("move", connection, move.processor, move.direction)
        chain = self.chains[_track_of(self.chains, move.processor)]
        i = chain.index(move.processor)
        chain.insert(i + move.direction, chain.pop(i))
        return replace(move, outcome="moved")

    def removal(self, connection, processor):
        self._enter("removal", connection, processor)
        on_a_chain = any(processor in chain for chain in self.chains.values())
        return {"chains": copy.deepcopy(self.chains), "ids": dict(self.ids),
                "uids": self.uids() if on_a_chain else {},
                "plugin": copy.deepcopy(self.classes.get(processor)) if on_a_chain else None}

    def add(self, connection, addition):
        self._enter("add", connection, addition.name, addition.uri, addition.track_id,
                    addition.before_id, addition.add_to_back)
        track = next(t for t, i in self.ids.items() if i == addition.track_id)
        chain = self.chains[track]
        if addition.add_to_back or self.misplace:
            chain.append(addition.name)
        else:
            before = next(n for n, i in self.ids.items() if i == addition.before_id)
            chain.insert(chain.index(before), addition.name)
        self.ids[addition.name] = max(self.ids.values()) + 1
        self.classes[addition.name] = {"uri": addition.uri, "uid": None, "type": "lv2"}

    def remove(self, connection, removal):
        self._enter("remove", connection, removal.processor, removal.processor_id, removal.track_id)
        if not self.keep:
            self.chains[removal.track].remove(removal.processor)

    def writes(self):
        return [c for c in self.calls if c[0] in ("add", "remove", "move")]


def edit_agent(**kwargs):
    fake = EditingLive()
    kwargs.setdefault("describe", lambda: dict(UP))
    kwargs.setdefault("library", lambda: LIBRARY)
    return Agent(live=fake, **kwargs), fake


def log(agent):
    return call(agent, "GET", "/live/log")[1]["log"]


def test_the_library_lists_the_manifest_with_the_one_profile_and_its_groups():
    """The real manifest and profile, through the route, with the agent's own
    wiring (`read_library` of the checkout). Whether names come from bundles
    depends on this machine having plugins/, so only what holds either way is
    asserted."""
    agent = Agent(describe=lambda: dict(UP), live=EditingLive())
    status, body = call(agent, "GET", "/library")
    assert status == 200 and body["ok"] is True
    entries = {e["uri"]: e for e in body["plugins"]}
    assert len(body["plugins"]) == 55
    assert entries[GXTS9]["profile"] is True
    assert [g["id"] for g in entries[GXTS9]["groups"]] == ["dirt", "output"]
    assert entries[GXTS9]["name"] == "GxTubeScreamer"
    assert sum(e["profile"] for e in body["plugins"]) == 1
    assert entries[NAM]["addable"] is False
    assert body["sources"]["plugins_dir"] == str(ROOT / "plugins")
    assert all(e["name_from"] in ("plugin", "profile", "uri") for e in body["plugins"])
    assert all(e["name_from"] == "plugin" or e["note"] for e in body["plugins"]), \
        "a name that is not the plugin's own says why"


def test_the_library_is_built_once_for_the_agents_lifetime():
    built = []

    def build():
        built.append(1)
        return read_library(ROOT, plugins_dir=FIXTURES)

    agent, fake = edit_agent(library=build)

    async def go():
        async with TestClient(TestServer(agent.app())) as client:
            first = await asyncio.gather(*(client.get("/library") for _ in range(3)))
            assert {r.status for r in first} == {200}
            again = await client.get("/library")
            assert again.status == 200
            names = {e["uri"]: e["name"] for e in (await again.json())["plugins"]}
            assert names[DELAY] == "MDA Delay"
            add = await client.post("/live/add", json={"track": "guitar", "uri": DELAY})
            assert add.status == 200

    asyncio.run(go())
    assert built == [1], "three at once, a fourth, and an add: one build"
    assert fake.calls and not any(c[0] == "library" for c in fake.calls)


def test_a_library_that_cannot_be_built_is_said_and_tried_again():
    from sushi_rig.library import LibraryError

    attempts = []

    def build():
        attempts.append(1)
        if len(attempts) == 1:
            raise LibraryError("cannot read /x/plugin-manifest.txt: gone")
        return LIBRARY

    agent, fake = edit_agent(library=build)
    status, body = call(agent, "GET", "/library")
    assert status == 500 and "plugin-manifest.txt" in body["reason"]
    assert call(agent, "GET", "/library")[0] == 200, "a failure is not cached"
    assert call(agent, "POST", "/live/add", json={"track": "guitar", "uri": DELAY})[0] == 200


def test_an_add_goes_in_before_the_send_and_is_logged_with_the_chain_before():
    agent, fake = edit_agent()
    status, body = call(agent, "POST", "/live/add", json={"track": "guitar", "uri": GXTS9, "name": "ts"})
    assert status == 200
    assert fake.writes() == [("add", "ts", GXTS9, 1, 4, False)]
    assert body["chain"] == ["comp", "drive", "ts", "guitar_send"]
    assert body["message"] == "added ts to guitar"
    assert (body["op"], body["previous"]) == ("add", ["comp", "drive", "guitar_send"])
    assert body["args"] == {"track": "guitar", "uri": GXTS9, "name": "ts", "after": None}
    assert [c[:1] + c[-1:] for c in fake.calls if c[0] == "walk"] == [("walk", True), ("walk", False)], \
        "read with the uids (to know the send), change, read back"
    assert [c[0] for c in fake.calls] == ["walk", "add", "walk"]
    assert [(e["op"], e["previous"]) for e in log(agent)] == [("add", ["comp", "drive", "guitar_send"])]
    assert set(log(agent)[0]) == {"at", "op", "args", "previous"}


def test_an_add_without_a_name_is_named_from_the_plugin_and_the_log_says_which():
    agent, fake = edit_agent()
    status, body = call(agent, "POST", "/live/add",
                        json={"track": "master", "uri": DELAY, "after": "master_return"})
    assert status == 200
    assert body["args"]["name"] == "mda_delay", "undo removes it by this name"
    assert body["chain"] == ["master_return", "mda_delay", "limiter"]


@pytest.mark.parametrize("body", [
    {"track": "guitar", "uri": "http://example.org/x"},
    {"track": "guitar", "uri": NAM},
    {"track": "guitar", "uri": GXTS9, "name": "Bad Name"},
    {"uri": GXTS9},
    [1],
])
def test_an_add_badly_asked_is_refused_without_contacting_sushi(body):
    agent, fake = edit_agent()
    status, answer = call(agent, "POST", "/live/add", json=body)
    assert status == 400 and answer["reason"]
    assert fake.calls == [] and fake.opened == []
    assert log(agent) == []


@pytest.mark.parametrize("body, why", [
    ({"track": "guitar", "uri": GXTS9, "name": "drive"}, "already on the rig"),
    ({"track": "guitar", "uri": GXTS9}, None),  # derived "gxtubescreamer": no clash, see below
    ({"track": "guitar", "uri": GXTS9, "after": "guitar_send"}, "send must stay last"),
    ({"track": "bass", "uri": GXTS9}, "no track 'bass'"),
    ({"track": "guitar", "uri": GXTS9, "after": "limiter"}, "no processor 'limiter' on track 'guitar'"),
])
def test_an_add_the_rig_cannot_take_is_409_and_changes_nothing(body, why):
    agent, fake = edit_agent()
    if why is None:
        fake.chains["master"].append("gxtubescreamer")
        why = "give a 'name'"
    status, answer = call(agent, "POST", "/live/add", json=body)
    assert status == 409
    assert why in answer["reason"]
    assert fake.writes() == []
    assert log(agent) == []


def test_an_add_sushi_confirmed_but_put_elsewhere_is_a_bad_gateway_and_not_logged():
    agent, fake = edit_agent()
    fake.misplace = True
    status, body = call(agent, "POST", "/live/add", json={"track": "guitar", "uri": GXTS9, "name": "ts"})
    assert status == 502
    assert "read back ['comp', 'drive', 'guitar_send', 'ts']" in body["reason"]
    assert "not ['comp', 'drive', 'ts', 'guitar_send']" in body["reason"]
    assert log(agent) == []
    error = call(agent, "GET", "/health")[1]["agent"]["last_error"]
    assert error["kind"] == "live" and error["command"] == ["POST", "/live/add"]


def test_an_add_sushi_refused_is_a_bad_gateway_and_not_logged():
    agent, fake = edit_agent()
    fake.fail, fake.fail_on = LiveError("Sushi reported an error completing the command"), "add"
    status, body = call(agent, "POST", "/live/add", json={"track": "guitar", "uri": GXTS9})
    assert (status, body["reason"]) == (502, "Sushi reported an error completing the command")
    assert log(agent) == []


def test_a_remove_is_logged_with_the_chain_before_and_the_plugin_to_put_back():
    agent, fake = edit_agent()
    status, body = call(agent, "POST", "/live/remove", json={"processor": "drive"})
    assert status == 200
    assert fake.writes() == [("remove", "drive", 3, 1)]
    assert body["chain"] == ["comp", "guitar_send"]
    assert body["message"] == "removed drive from guitar"
    assert body["op"] == "remove" and body["args"] == {"processor": "drive"}
    assert body["previous"] == {
        "track": "guitar", "chain": ["comp", "drive", "guitar_send"],
        "uri": GXTS9, "uid": None, "type": "lv2",
    }
    assert [c[0] for c in fake.calls] == ["removal", "remove", "walk"]
    assert [e["op"] for e in log(agent)] == ["remove"]


@pytest.mark.parametrize("processor, why", [
    ("guitar_send", "the compiler's"),
    ("master_return", "the compiler's"),
    ("master", "is a track"),
    ("nope", "no processor 'nope'"),
])
def test_a_remove_of_a_send_a_return_or_what_is_not_there_is_409(processor, why):
    agent, fake = edit_agent()
    status, body = call(agent, "POST", "/live/remove", json={"processor": processor})
    assert status == 409 and why in body["reason"]
    assert fake.writes() == []
    assert log(agent) == []


def test_a_remove_badly_asked_is_refused_without_contacting_sushi():
    agent, fake = edit_agent()
    assert call(agent, "POST", "/live/remove", json={"processor": 3})[0] == 400
    assert call(agent, "POST", "/live/remove")[0] == 400
    assert fake.calls == []


def test_a_remove_that_is_still_there_afterwards_is_a_bad_gateway_and_not_logged():
    agent, fake = edit_agent()
    fake.keep = True
    status, body = call(agent, "POST", "/live/remove", json={"processor": "drive"})
    assert status == 502 and "read back ['comp', 'drive', 'guitar_send']" in body["reason"]
    assert log(agent) == []


def test_a_remove_whose_plugin_sushi_will_not_name_is_a_bad_gateway_and_removes_nothing():
    agent, fake = edit_agent()
    fake.classes["drive"] = None
    status, body = call(agent, "POST", "/live/remove", json={"processor": "drive"})
    assert status == 502 and "could not say which plugin" in body["reason"]
    assert fake.writes() == []


def test_an_add_then_its_remove_leaves_the_chain_as_it_was():
    agent, fake = edit_agent()
    assert call(agent, "POST", "/live/add", json={"track": "guitar", "uri": DELAY, "after": "comp"})[0] == 200
    status, body = call(agent, "POST", "/live/remove", json={"processor": "mda_delay"})
    assert status == 200
    assert body["chain"] == CHAINS["guitar"]
    assert body["previous"]["uri"] == DELAY
    assert [e["op"] for e in log(agent)] == ["add", "remove"]


@pytest.mark.parametrize("path, body", [
    ("/live/add", {"track": "guitar", "uri": GXTS9}),
    ("/live/remove", {"processor": "drive"}),
    ("/live/add", {"track": "guitar", "uri": GXTS9, "name": "drive"}),  # refused
    ("/live/remove", {"processor": "guitar_send"}),  # refused
])
def test_an_edit_makes_one_connection_does_everything_on_it_and_closes_it(path, body):
    agent, fake = edit_agent()
    status, _ = call(agent, "POST", path, json=body)
    assert status in (200, 409)
    assert fake.opened == [1] and sorted(fake.closed) == [1]


@pytest.mark.parametrize("path, body", [
    ("/live/add", {"track": "guitar", "uri": GXTS9}),
    ("/live/remove", {"processor": "drive"}),
])
def test_an_edit_is_busy_while_a_lifecycle_command_runs(path, body):
    started, release = asyncio.Event(), asyncio.Event()

    async def slow(args):
        started.set()
        await release.wait()
        return Outcome(True, 0, "", "")

    agent, fake = edit_agent(run=slow)

    async def go():
        async with TestClient(TestServer(agent.app())) as client:
            lifecycle = asyncio.ensure_future(client.post("/rig/up", json={"config_name": "electric_board"}))
            await started.wait()
            response = await client.post(path, json=body)
            assert response.status == 409
            assert "busy: 'up' is still running" in (await response.json())["reason"]
            release.set()
            assert (await lifecycle).status == 200

    asyncio.run(go())
    assert fake.calls == []


# --------------------------------------------------------------------------- #
# live.py's calls, against elkpy's own signatures (no Sushi)
# --------------------------------------------------------------------------- #

class _Recorded:
    """A SushiCommandResponse that is already set, as a confirmed one is."""
    error = False

    def is_set(self):
        return True


def _bound_to(method_name, controller_class):
    """A stand-in for one elkpy method that binds its arguments to the real
    method's signature, so a keyword elkpy does not have fails here."""
    import inspect

    signature = inspect.signature(getattr(controller_class, method_name))
    calls = []

    def stand_in(*args, **kwargs):
        calls.append(signature.bind(None, *args, **kwargs).arguments)
        return _Recorded()

    return stand_in, calls


def test_an_add_sends_create_processor_on_track_as_elkpy_names_its_arguments():
    elkpy_graph = pytest.importorskip("elkpy.audiographcontroller")
    from types import SimpleNamespace

    from elkpy import sushi_info_types as info

    from sushi_rig.live import add_live_processor

    create, calls = _bound_to("create_processor_on_track", elkpy_graph.AudioGraphController)
    controller = SimpleNamespace(audio_graph=SimpleNamespace(create_processor_on_track=create))
    add_live_processor("ts", GXTS9, 1, 4, False, controller=controller)
    sent = dict(calls[0])
    sent.pop("self")
    assert sent == {
        "name": "ts", "uid": "", "path": GXTS9, "processor_type": info.PluginType.LV2,
        "track_id": 1, "before_processor": 4, "add_to_back": False,
    }


def test_a_remove_sends_delete_processor_from_track_processor_first():
    elkpy_graph = pytest.importorskip("elkpy.audiographcontroller")
    from types import SimpleNamespace

    from sushi_rig.live import remove_live_processor

    delete, calls = _bound_to("delete_processor_from_track", elkpy_graph.AudioGraphController)
    controller = SimpleNamespace(audio_graph=SimpleNamespace(delete_processor_from_track=delete))
    remove_live_processor(3, 1, controller=controller)
    assert {k: v for k, v in calls[0].items() if k != "self"} == {"processor": 3, "track": 1}


def test_the_plugin_to_put_back_is_read_from_the_saved_session():
    """The stand-in session has the shape of elkpy 1.3.0's `SessionState`
    (tracks[].processors[], each a PluginClass: name, uid, path, and type.type
    the PluginType enum's number) as its .proto declares it; what Sushi fills
    those fields with is not known from here."""
    pytest.importorskip("elkpy")
    from types import SimpleNamespace as NS

    from sushi_rig.live import read_live_removal

    def plugin(name, uid, path, kind):
        return NS(name=name, uid=uid, path=path, type=NS(type=kind))

    session = NS(tracks=[NS(name="guitar", processors=[
        plugin("drive", "", GXTS9, 4), plugin("guitar_send", "sushi.testing.send", "", 1),
    ])])
    graph = NS(
        get_all_tracks=lambda: [NS(id=1, name="guitar")],
        get_track_processors=lambda track: [NS(id=3, name="drive"), NS(id=4, name="guitar_send")],
    )
    asked = []
    controller = NS(audio_graph=graph, session=NS(get_session=lambda: asked.append(1) or session))
    # With its track: the session walk is shared with the web UI's read; the
    # removal keeps uri, uid and type (`check_remove`).
    assert read_live_removal("drive", controller=controller)["plugin"] == \
        {"track": "guitar", "uri": GXTS9, "uid": None, "type": "lv2"}
    assert read_live_removal("guitar_send", controller=controller)["plugin"] == \
        {"track": "guitar", "uri": None, "uid": "sushi.testing.send", "type": "internal"}
    assert len(asked) == 2
    assert read_live_removal("nope", controller=controller)["plugin"] is None
    assert len(asked) == 2, "nothing to remove, so the session is not read"


# --------------------------------------------------------------------------- #
# One rule for the send and the return: add, remove and move (found by review)
# --------------------------------------------------------------------------- #

RENAMED = {"guitar": ["comp", "drive", "out"], "master": ["back", "limiter"]}
RENAMED_IDS = {"guitar": 1, "comp": 2, "drive": 3, "out": 4, "master": 5, "back": 6, "limiter": 7}
RENAMED_UIDS = {"comp": None, "drive": None, "out": "sushi.testing.send",
                "back": "sushi.testing.return", "limiter": None}


def test_the_rule_knows_a_send_or_return_by_uid_or_by_name():
    assert protected_processor("guitar", "guitar_send") == "'guitar_send' is 'guitar''s send"
    assert protected_processor("guitar", "out", "sushi.testing.send") == "'out' is 'guitar''s send"
    assert protected_processor("master", "master_return") == "'master_return' is the master's return"
    assert protected_processor("master", "back", "sushi.testing.return")
    assert protected_processor("guitar", "out") is None, "no uid read, and not the compiler's name"
    assert protected_processor("guitar", "delay_send") is None, "a player's plugin may end in _send"


def test_an_add_goes_in_front_of_a_send_known_only_by_its_uid():
    """By name alone the add went in behind a send called something else:
    confirmed by the read-back, logged, and not heard."""
    state = {"chains": copy.deepcopy(RENAMED), "ids": dict(RENAMED_IDS), "uids": dict(RENAMED_UIDS)}
    addition = check_add(state, request())
    assert addition.expected == ["comp", "drive", "new", "out"]
    assert (addition.before_id, addition.add_to_back) == (4, False)
    with pytest.raises(Conflict, match="send must stay last"):
        check_add(state, request(after="out"))
    assert check_add(state, request(track="master", after="back")).expected == ["back", "new", "limiter"]


def test_a_remove_refuses_a_send_known_only_by_the_walks_uids():
    state = {"chains": copy.deepcopy(RENAMED), "ids": dict(RENAMED_IDS), "uids": dict(RENAMED_UIDS),
             "plugin": None}
    with pytest.raises(Conflict, match="'out' is 'guitar''s send"):
        check_remove(state, "out")


@pytest.mark.parametrize("processor, direction, why", [
    ("drive", 1, "'guitar_send' is 'guitar''s send"),      # behind the send
    ("guitar_send", -1, "'guitar_send' is 'guitar''s send"),  # the send itself
    ("limiter", -1, "'master_return' is the master's return"),  # before the return
    ("master_return", 1, "'master_return' is the master's return"),
])
def test_a_move_past_or_of_a_send_or_return_is_protected(processor, direction, why):
    move = decide_move(CHAINS, IDS, processor, direction)
    assert move.outcome == "protected" and move.command is None
    assert why in move.message
    with pytest.raises(Conflict, match="cannot move"):
        check_move(move)


def test_a_move_knows_a_renamed_send_by_its_uid():
    assert decide_move(RENAMED, RENAMED_IDS, "drive", 1).outcome == "planned", \
        "without uids, the name rule only: this is the listener's case"
    assert decide_move(RENAMED, RENAMED_IDS, "drive", 1, RENAMED_UIDS).outcome == "protected"
    assert decide_move(RENAMED, RENAMED_IDS, "comp", 1, RENAMED_UIDS).outcome == "planned"
    assert decide_move(CHAINS, IDS, "guitar_send", 1).outcome == "unchanged", \
        "already last: no move, so nothing to protect"


def test_the_move_route_refuses_a_move_behind_the_send_and_sends_nothing():
    agent, fake = edit_agent()
    status, body = call(agent, "POST", "/live/move", json={"processor": "drive", "direction": "down"})
    assert status == 400
    assert "drive cannot move later: 'guitar_send' is 'guitar''s send" in body["reason"]
    assert fake.writes() == [] and log(agent) == []
    status, body = call(agent, "POST", "/live/move", json={"processor": "drive", "direction": "up"})
    assert status == 200 and body["changed"] is True
    assert fake.chains["guitar"] == ["drive", "comp", "guitar_send"]


def test_the_agent_asks_live_for_the_uids_on_a_move_and_on_an_adds_reading(monkeypatch):
    from sushi_rig import live as live_module
    from sushi_rig.agent import Live

    asked = []
    monkeypatch.setattr(live_module, "plan_live_move",
                        lambda proc, direction, address, c, uids=False: asked.append(("move", uids)))
    monkeypatch.setattr(live_module, "read_live_chains",
                        lambda address, c, uids=False: asked.append(("walk", uids)))
    wired = Live.at("localhost:1")
    wired.plan_move("c", "drive", 1)
    wired.walk("c", True)
    wired.walk("c")
    assert asked == [("move", True), ("walk", True), ("walk", False)]


def test_plan_live_move_reads_the_session_only_when_asked():
    pytest.importorskip("elkpy")
    from types import SimpleNamespace as NS

    from sushi_rig.live import plan_live_move

    def plugin(name, uid):
        return NS(name=name, uid=uid, path="", type=NS(type=1))

    asked = []
    session = NS(tracks=[NS(processors=[plugin("drive", ""), plugin("out", "sushi.testing.send")])])
    controller = NS(
        audio_graph=NS(get_all_tracks=lambda: [NS(id=1, name="guitar")],
                       get_track_processors=lambda t: [NS(id=3, name="drive"), NS(id=4, name="out")]),
        session=NS(get_session=lambda: asked.append(1) or session),
    )
    assert plan_live_move("drive", 1, controller=controller).outcome == "planned"
    assert asked == []
    assert plan_live_move("drive", 1, controller=controller, uids=True).outcome == "protected"
    assert asked == [1]


def test_a_read_back_with_no_chains_is_a_bad_gateway_not_a_crash():
    agent, fake = edit_agent()
    fake.walk_without_chains = True
    status, body = call(agent, "POST", "/live/remove", json={"processor": "drive"})
    assert status == 502 and "read-back had no chains" in body["reason"]
    assert log(agent) == []
    status, body = call(agent, "POST", "/live/add", json={"track": "guitar", "uri": GXTS9})
    assert status == 502 and "read-back had no chains" in body["reason"]
