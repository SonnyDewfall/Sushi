"""The master's limiter and the master volume's top (#137, option 2), without
Sushi.

Option 2, chosen 2026-11-17: every rig's master ends with mda/Limiter as a soft
safety net (`rigfile.LIMITER`), and the master volume stops at 0 dB, since
Sushi applies the master's gain after the master's plugins. Here: the compiler
puts the limiter in and holds its settings over any capture; a save never
writes it into a rig file; the agent never moves, removes, bypasses or
retunes it, and puts what is added to the master in front of it; the master's
gain is refused above 0 dB on every path that sets it, and the dip never puts
more back; a rig file that says more still loads, and plays at 0 dB.

What Sushi does with it (that it loads, takes the settings, passes the null
tests' references bit for bit and holds a hot input) is test_limiter_sushi.py's,
in the container. The rigs named here are invented or frozen (#133); the
committed rigs are read only through `rig_fixtures`, assuming nothing of them.
"""

from __future__ import annotations

import copy

import pytest

from rig_fixtures import FROZEN, compiled, frozen, valid_committed
from sushi_rig import live, rigfile, verify
from sushi_rig.rigfile import (
    LIMITER, LIMITER_SETTINGS, LIMITER_URI, MASTER_GAIN_MAX, RigFileError, compile_rig, diff, emit_rig,
    fold_state, gain_to_level, level_to_gain, limiter_problem, master_gain_problem, merged_state, parse_rig,
)

LIMITER_ENTRY = {"name": LIMITER, "type": "lv2", "uri": LIMITER_URI}
ROOM = {"name": "room", "uri": "urn:ardour:a-reverb"}


def rig(master_chain=(), level=None, chain=()):
    master = {"output": "monitor-out", "chain": [dict(p) for p in master_chain]}
    if level is not None:
        master["level"] = level
    return {
        "kind": "rig", "schema_version": 1, "name": "safety",
        "channels": [{"name": "board", "input": "guitar-in", "width": "mono", "chain": [dict(p) for p in chain]}],
        "master": master,
    }


def master_of(config):
    return next(t for t in config["tracks"] if t["name"] == "master")


def state_of(config):
    return {e["processor"]: e for e in config["initial_state"]}


# --------------------------------------------------------------------------- #
# The compiler
# --------------------------------------------------------------------------- #

def test_the_settings_are_the_researched_ones_and_say_a_ceiling_of_minus_one_dbfs():
    """tool/spikes/master_limiter/: Thresh 0.975 is 10^(2*0.975 - 2), -1 dBFS;
    Output 0.5 is a trim of exactly 1; Attack 0.15 its default; hard knee."""
    p = LIMITER_SETTINGS["parameters"]
    assert p == {"Thresh": 0.975, "Output": 0.5, "Release": 0.5, "Attack": 0.15, "Knee": 0.0}
    assert 20 * (2 * p["Thresh"] - 2) == pytest.approx(rigfile.LIMITER_CEILING_DBFS)
    assert 10 ** (2 * p["Output"] - 1) == 1.0
    assert LIMITER_SETTINGS["bypassed"] is False


@pytest.mark.parametrize("master_chain", [(), (ROOM,)], ids=["empty master", "a master with a plugin"])
def test_every_compiled_master_ends_with_the_limiter_at_its_settings(master_chain):
    config = emit_rig(parse_rig(rig(master_chain)))
    names = [p["name"] for p in master_of(config)["plugins"]]
    assert names == ["master_return", *[p["name"] for p in master_chain], LIMITER]
    assert master_of(config)["plugins"][-1] == LIMITER_ENTRY
    assert state_of(config)[LIMITER] == {"processor": LIMITER, **LIMITER_SETTINGS}
    assert verify.limiter_problems(config) == []


def test_every_frozen_rig_compiles_with_the_limiter_last():
    for path in sorted(FROZEN.glob("*.yaml")):
        config = compiled(path)
        assert master_of(config)["plugins"][-1] == LIMITER_ENTRY, path.name
        assert verify.limiter_problems(config) == [], path.name


@pytest.mark.committed_rigs
def test_every_committed_rig_compiles_with_the_limiter_last():
    """Whatever config/rigs/ holds: the limiter is the compiler's, so no rig
    file can be without it."""
    for path in valid_committed():
        config = compiled(path)
        assert master_of(config)["plugins"][-1] == LIMITER_ENTRY, path.name
        assert state_of(config)[LIMITER] == {"processor": LIMITER, **LIMITER_SETTINGS}, path.name


def test_the_limiter_is_never_in_a_rig_file_and_its_name_is_the_compilers():
    for where in ("master", "channel"):
        raw = rig(master_chain=[{"name": LIMITER, "uri": LIMITER_URI}]) if where == "master" \
            else rig(chain=[{"name": LIMITER, "uri": LIMITER_URI}])
        with pytest.raises(RigFileError, match="master_limiter.*limiter"):
            parse_rig(raw)
    raw = rig()
    raw["channels"][0]["name"] = LIMITER
    with pytest.raises(RigFileError, match="its return or limiter"):
        parse_rig(raw)


@pytest.mark.parametrize("binding", [{"parameter": "master/master_limiter/Thresh"}, {"bypass": "master/master_limiter"}])
def test_a_midi_map_cannot_reach_the_limiter(binding):
    raw = rig()
    raw["midi"] = {"controller": "pedal", "controls": [{"control": "exp", **binding}]}
    with pytest.raises(RigFileError, match="'master_limiter', which is not in master's chain"):
        parse_rig(raw)


def test_a_player_may_still_put_mda_limiter_on_the_master_under_another_name():
    config = emit_rig(parse_rig(rig([{"name": "ceiling", "uri": LIMITER_URI}])))
    assert [p["name"] for p in master_of(config)["plugins"]] == ["master_return", "ceiling", LIMITER]


def test_a_capture_cannot_bypass_retune_or_move_the_limiter():
    """Captured from a Sushi set beside the agent: the compiler's settings and
    place hold, as a send's destination does."""
    captured = {
        "processors": {LIMITER: {"bypassed": True, "parameters": {"Thresh": 1.0, "Output": 1.0}}},
        "tracks": {"master": [LIMITER, "room", "master_return"]},
    }
    config = emit_rig(parse_rig(rig([ROOM])), captured)
    assert [p["name"] for p in master_of(config)["plugins"]] == ["master_return", "room", LIMITER]
    assert state_of(config)[LIMITER] == {"processor": LIMITER, **LIMITER_SETTINGS}


def test_a_rig_compiled_routing_only_has_the_limiter_too():
    """The patchbay's and SAVE's source check: the running config names it,
    so the spec they compare with must."""
    spec, _ = compile_rig(parse_rig(rig()), routing_only=True)
    assert [p.name for p in spec.tracks[-1].plugins] == ["master_return", LIMITER]


# --------------------------------------------------------------------------- #
# The master volume's top, in the files
# --------------------------------------------------------------------------- #

def test_a_rig_file_over_the_top_still_loads_and_plays_at_unity():
    """Decided: a file written before the cap is not refused (that would
    break a rig that loaded yesterday); it compiles at 0 dB, and the next save
    writes 0 dB, so the file comes to say what plays."""
    parsed = parse_rig(rig(level=4.5))
    assert parsed.master.level == 4.5, "read as written"
    config = emit_rig(parsed)
    assert "master" not in state_of(config), "unity, which the compiler writes nowhere"
    saved = fold_state(rig(level=4.5), {"processors": {"master": {"parameters": {"gain": level_to_gain(0.0)}}}})
    assert saved["master"]["level"] == 0.0


RIG_TEXT = """\
# A rig written before the cap (#137): its master over 0 dB.
kind: rig
schema_version: 1
name: safety
channels:
  - name: board
    input: guitar-in   # the guitar
    width: mono
    chain: []
master:
  output: monitor-out
  chain: []
  level: 4.5           # hot, from before
"""


def test_a_file_over_the_top_saves_as_unity_and_nothing_else_changes(tmp_path):
    """Through `write_rig`, as the agent's Save writes: the one line changes."""
    import yaml

    path = tmp_path / "safety.yaml"
    path.write_text(RIG_TEXT)
    raw = yaml.safe_load(RIG_TEXT)
    captured = {"processors": {"master": {"parameters": {"gain": MASTER_GAIN_MAX, "pan": 0.5, "mute": 0.0}},
                               LIMITER: {"parameters": dict(LIMITER_SETTINGS["parameters"])}},
                "tracks": {"master": ["master_return", LIMITER], "board": ["board_send"]}}
    rigfile.write_rig(path, fold_state(raw, captured))
    assert path.read_text() == RIG_TEXT.replace("level: 4.5", "level: 0.0")


def test_a_level_under_the_top_is_untouched():
    config = emit_rig(parse_rig(rig(level=-3.0)))
    assert state_of(config)["master"]["parameters"]["gain"] == pytest.approx(level_to_gain(-3.0))


def test_a_captured_master_gain_over_the_top_is_held_to_it_in_the_config_and_in_the_save():
    hot = {"processors": {"master": {"parameters": {"gain": level_to_gain(6.0)}}}}
    config = emit_rig(parse_rig(rig()), hot)
    assert state_of(config)["master"]["parameters"]["gain"] == pytest.approx(MASTER_GAIN_MAX)
    assert fold_state(rig(level=-2.0), hot)["master"]["level"] == 0.0
    _, fixed = compile_rig(parse_rig(rig()))
    merged = merged_state(fixed, hot)
    assert merged["processors"]["master"]["parameters"]["gain"] == MASTER_GAIN_MAX


def test_a_save_never_writes_the_limiter_into_the_rig_file():
    raw = rig([ROOM])
    captured = {
        "processors": {"room": {"parameters": {"Room": 0.4}},
                       LIMITER: {"bypassed": False, "parameters": dict(LIMITER_SETTINGS["parameters"])}},
        "tracks": {"master": ["master_return", "room", LIMITER], "board": ["board_send"]},
    }
    saved = fold_state(raw, captured)
    assert [e["name"] for e in saved["master"]["chain"]] == ["room"]
    assert saved["master"]["chain"][0]["parameters"] == {"Room": 0.4}
    assert LIMITER not in str(saved)


def test_a_save_of_a_master_with_no_plugins_keeps_its_empty_chain():
    captured = {"processors": {LIMITER: {"parameters": {"Thresh": 0.975}}},
                "tracks": {"master": ["master_return", LIMITER]}}
    assert fold_state(rig(), captured)["master"]["chain"] == []


# --------------------------------------------------------------------------- #
# The refusals, pure
# --------------------------------------------------------------------------- #

def test_the_master_gain_is_refused_over_unity_and_nothing_under_it():
    assert MASTER_GAIN_MAX == level_to_gain(0.0) == 120 / 144
    for fine in (0.0, level_to_gain(-60.0), MASTER_GAIN_MAX, MASTER_GAIN_MAX + 5e-7):
        assert master_gain_problem(fine) is None, fine
    why = master_gain_problem(level_to_gain(0.5))
    assert why.startswith("the master volume stops at 0 dB") and "+0.5 dB" in why
    assert master_gain_problem(1.0) is not None
    for shape in (True, "0.9", None, float("nan")):
        assert master_gain_problem(shape) is None, "the request's own check refuses a bad shape"


def test_the_master_pan_stays_at_the_centre():
    """Sushi pans after the plugins, and its pan law lifts the side panned to
    by up to 3 dB (PR #172's review, measured in Sushi): every pan but the
    centre lifts a side over unity, so only the centre is allowed."""
    assert rigfile.MASTER_PAN == 0.5
    assert rigfile.master_pan_problem(0.5) is None and rigfile.master_pan_problem(0.5 + 5e-7) is None
    for off in (0.0, 0.49, 0.51, 1.0):
        assert rigfile.master_pan_problem(off).startswith("the master's pan stays at the centre"), off
    for shape in (True, None, "0.5", float("nan")):
        assert rigfile.master_pan_problem(shape) is None


def test_a_captured_master_pan_off_the_centre_is_put_back_in_the_config():
    off = {"processors": {"master": {"parameters": {"pan": 0.0}}}}
    config = emit_rig(parse_rig(rig()), off)
    assert state_of(config)["master"]["parameters"]["pan"] == 0.5
    assert fold_state(rig(), off)["master"] == rig()["master"], "a rig file has no key for it"


def test_the_limiters_settings_are_fixed_and_it_is_never_bypassed():
    assert limiter_problem(LIMITER, "Thresh", 1.0).startswith("'master_limiter' is the master's limiter")
    assert limiter_problem(LIMITER, "Output", 0.6) is not None
    assert limiter_problem(LIMITER, "Thresh", 0.975) is None, "put back to its setting: allowed"
    assert limiter_problem(LIMITER, "Thresh", 0.975 + 1e-7) is None, "a float32's worth away"
    assert limiter_problem(LIMITER, bypassed=True) is not None
    assert limiter_problem(LIMITER, bypassed=False) is None, "un-bypassing it puts it right"
    assert limiter_problem("ceiling", "Thresh", 1.0) is None, "a player's mda/Limiter is theirs"
    assert limiter_problem(LIMITER, "nothing", 1.0) is None, "a parameter it lacks is check_parameter's"


def test_the_limiter_is_protected_on_the_master_and_a_tail_there_only():
    assert live.protected_processor("master", LIMITER) == "'master_limiter' is the master's limiter"
    assert live.is_tail("master", LIMITER) and live.is_tail("board", "board_send")
    assert not live.is_tail("master", "master_return")
    assert live.protected_processor("board", LIMITER) is None
    assert live.protected_processor("master", "ceiling") is None


# --------------------------------------------------------------------------- #
# The agent's decisions, pure
# --------------------------------------------------------------------------- #

agent = pytest.importorskip("sushi_rig.agent")


def reading(master=("master_return", "room", LIMITER)):
    chains = {"board": ["comp", "board_send"], "master": list(master)}
    names = [n for chain in chains.values() for n in chain] + list(chains)
    return {"chains": chains, "ids": {n: i for i, n in enumerate(names, 1)},
            "uids": {"board_send": rigfile.SEND_UID, "master_return": rigfile.RETURN_UID}}


def add(track="master", after=None, first=False, name="delay"):
    return agent.AddRequest(track, "http://drobilla.net/plugins/mda/Delay", name, after, True, None, first=first)


def test_an_add_to_the_master_goes_in_front_of_the_limiter():
    for request in (add(), add(after="room")):
        decided = agent.check_add(reading(), request)
        assert decided.expected == ["master_return", "room", "delay", LIMITER], request
        assert not decided.add_to_back
    empty = reading(("master_return", LIMITER))
    for request in (add(), add(first=True), add(after="master_return")):
        assert agent.check_add(empty, request).expected == ["master_return", "delay", LIMITER], request


def test_nothing_is_added_after_the_limiter():
    with pytest.raises(agent.Conflict, match="the master's limiter stays last"):
        agent.check_add(reading(), add(after=LIMITER))


def test_a_name_on_the_rig_cannot_be_the_limiters():
    with pytest.raises(agent.Conflict, match="already on the rig"):
        agent.check_add(reading(), add(track="board", name=LIMITER))


def test_the_limiter_is_never_removed():
    found = {**reading(), "plugin": {"uri": LIMITER_URI, "uid": LIMITER_URI, "type": "lv2"}}
    with pytest.raises(agent.Conflict, match="master's limiter: the compiler's, and never removed"):
        agent.check_remove(found, LIMITER)


def test_the_limiter_is_never_moved_and_nothing_is_moved_past_it():
    r = reading()
    for processor, direction in ((LIMITER, -1), ("room", 1)):
        move = live.decide_move(r["chains"], r["ids"], processor, direction, r["uids"])
        assert move.outcome == "protected" and "master's limiter" in move.why, (processor, direction)


def test_a_switch_never_adds_behind_the_limiter():
    uri = "http://drobilla.net/plugins/mda/Delay"
    with pytest.raises(agent.Conflict, match="behind the master's limiter"):
        agent.place_switch_add(reading(), "master", "delay", uri, None)
    decided = agent.place_switch_add(reading(), "master", "delay", uri, LIMITER)
    assert decided.expected == ["master_return", "room", "delay", LIMITER]


def test_the_compilers_names_cannot_be_added_live():
    """On a rig from before the limiter, an add named `master_limiter` used to
    go in, and then could be neither removed, bypassed nor saved (PR #172)."""
    library = {"plugins": [{"uri": "http://drobilla.net/plugins/mda/Delay", "name": "MDA Delay",
                            "audio": {"in": 2, "out": 2}}]}
    for name in ("master", "master_return", LIMITER):
        with pytest.raises(agent.AgentError, match="a name the compiler gives the master track"):
            agent.add_request({"track": "master", "uri": "http://drobilla.net/plugins/mda/Delay", "name": name},
                              library)
    assert agent.add_request({"track": "master", "uri": "http://drobilla.net/plugins/mda/Delay",
                              "name": "delay"}, library).name == "delay"


def test_the_set_paths_refuse_the_top_and_the_limiter():
    agent._refuse_fixed("master", {"gain": MASTER_GAIN_MAX, "pan": 0.5})
    agent._refuse_fixed("board", {"pan": 0.0})
    agent._refuse_fixed(LIMITER, dict(LIMITER_SETTINGS["parameters"]))
    agent._refuse_fixed("room", {"Room": 1.0})
    for processor, values in (("master", {"gain": 1.0}), ("master", {"pan": 0.0}), (LIMITER, {"Thresh": 0.5}),
                              (LIMITER, {"Output": 0.5, "Attack": 1.0})):
        with pytest.raises(agent.AgentError):
            agent._refuse_fixed(processor, values)


# --------------------------------------------------------------------------- #
# A live switch
# --------------------------------------------------------------------------- #

def _without_limiter(config):
    old = copy.deepcopy(config)
    master_of(old)["plugins"] = [p for p in master_of(old)["plugins"] if p["name"] != LIMITER]
    old["initial_state"] = [e for e in old["initial_state"] if e["processor"] != LIMITER]
    return old


def test_a_switch_between_two_rigs_leaves_the_limiter_alone():
    a, b = compiled(frozen("electric-clean")), compiled(frozen("electric-delay"))
    plan = diff(a, b)
    assert isinstance(plan, rigfile.SwitchPlan), plan
    assert not [s for s in plan.steps if LIMITER in (s.args.get("processor"), s.args.get("name"))]


def test_a_switch_from_a_rig_started_before_the_limiter_restarts_to_put_it_in():
    a, b = compiled(frozen("electric-clean")), compiled(frozen("electric-delay"))
    decided = diff(_without_limiter(a), b)
    assert isinstance(decided, rigfile.NeedsRestart)
    assert any(r.code == "protected" and LIMITER in r.detail for r in decided.reasons), decided


def test_a_switch_puts_a_limiter_set_beside_the_agent_back_to_its_settings():
    """Sushi had it otherwise (its own OSC, the v1 panel): the plan sets it
    back, and the agent allows that set, being the setting itself."""
    a, b = compiled(frozen("electric-clean")), compiled(frozen("electric-delay"))
    from sushi_rig import live as live_module  # noqa: F401  (the reading's shape is capture's)

    reading_now = {"processors": {e["processor"]: {"parameters": dict(e.get("parameters") or {}),
                                                   "bypassed": e.get("bypassed", False)}
                                  for e in a["initial_state"]},
                   "tracks": {t["name"]: [p["name"] for p in t["plugins"]] for t in a["tracks"]}}
    reading_now["processors"].setdefault("master", {"parameters": {"gain": level_to_gain(0.0)}})
    reading_now["processors"][LIMITER] = {"parameters": {**LIMITER_SETTINGS["parameters"], "Thresh": 0.6},
                                          "bypassed": True}
    plan = diff(a, b, reading_now)
    assert isinstance(plan, rigfile.SwitchPlan), plan
    steps = [s.as_dict() for s in plan.steps if s.args.get("processor") == LIMITER]
    assert {"op": "parameter", "processor": LIMITER, "parameter": "Thresh", "value": 0.975} in steps
    assert {"op": "bypass", "processor": LIMITER, "bypassed": False} in steps
    for step in steps:
        if step["op"] == "parameter":
            assert limiter_problem(LIMITER, step["parameter"], step["value"]) is None
        else:
            assert limiter_problem(LIMITER, bypassed=step["bypassed"]) is None


# --------------------------------------------------------------------------- #
# verify's check of a compiled config
# --------------------------------------------------------------------------- #

def test_verify_finds_a_compiled_rig_without_its_limiter_or_with_it_retuned():
    config = compiled(frozen("electric-clean"))
    assert verify.limiter_problems(config) == []
    (problem,) = verify.limiter_problems(_without_limiter(config))
    assert "does not end with the limiter" in str(problem)
    retuned = copy.deepcopy(config)
    state_of(retuned)[LIMITER]["parameters"]["Thresh"] = 1.0
    (problem,) = verify.limiter_problems(retuned)
    assert "not the compiler's fixed" in str(problem)


def test_verify_holds_a_v1_config_to_nothing_here():
    import json

    from rig_fixtures import frozen_v1

    assert verify.limiter_problems(json.loads(frozen_v1("electric_board").read_text())) == []


def test_gain_to_level_of_the_top_is_unity():
    assert gain_to_level(MASTER_GAIN_MAX) == 0.0


# --------------------------------------------------------------------------- #
# Through the agent's routes, against the edit tests' fake Sushi
# --------------------------------------------------------------------------- #

pytest.importorskip("aiohttp")
import test_agent_edits as edits  # noqa: E402
from test_agent_edits import _agents_release_their_pools, call, undo  # noqa: E402,F401  (the fixture)


def limiter_agent():
    """The edit tests' agent, its master ending with the compiler's limiter
    as every compiled master now does: master_return, limiter (a player's
    mda/Limiter), master_limiter."""
    made, fake = edits.edit_agent()
    fake.chains["master"].append(LIMITER)
    fake.ids[LIMITER] = max(fake.ids.values()) + 1
    fake.classes[LIMITER] = {"uri": LIMITER_URI, "uid": None, "type": "lv2"}
    fake.values_of[LIMITER] = copy.deepcopy(LIMITER_SETTINGS)

    def bypass(connection, processor):
        """As `live.read_live_bypass`: the chains, and the processor's bypass."""
        fake._enter("bypass", connection, processor)
        return {"chains": copy.deepcopy(fake.chains),
                "bypassed": (fake.values_of.get(processor) or {}).get("bypassed")}

    fake.bypass = bypass
    return made, fake


def gain(fake):
    return fake.values_of["master"]["parameters"]["gain"]


def test_the_route_refuses_the_master_over_unity_and_sends_nothing():
    made, fake = limiter_agent()
    status, body = call(made, "POST", "/live/parameter",
                        json={"processor": "master", "parameter": "gain", "value": level_to_gain(3.0)})
    assert status == 400 and body["reason"].startswith("the master volume stops at 0 dB"), body
    assert fake.writes() == [] and gain(fake) == edits.MASTER_LEVEL
    status, body = call(made, "POST", "/live/parameter",
                        json={"processor": "master", "parameter": "gain", "value": MASTER_GAIN_MAX})
    assert status == 200, body
    assert gain(fake) == MASTER_GAIN_MAX


def test_the_batch_route_refuses_the_master_over_unity_too():
    made, fake = limiter_agent()
    status, body = call(made, "POST", "/live/parameters",
                        json={"processor": "master", "parameters": {"mute": 0.0, "gain": 1.0}})
    assert status == 400 and "stops at 0 dB" in body["reason"], body
    assert fake.writes() == []


def test_the_limiter_cannot_be_retuned_bypassed_removed_or_moved_through_the_routes():
    made, fake = limiter_agent()
    asks = [
        ("/live/parameter", {"processor": LIMITER, "parameter": "Thresh", "value": 1.0}, 400),
        ("/live/parameters", {"processor": LIMITER, "parameters": {"Output": 1.0}}, 400),
        ("/live/bypass", {"processor": LIMITER, "bypassed": True}, 400),
        ("/live/remove", {"processor": LIMITER}, 409),
    ]
    for path, body_sent, code in asks:
        status, body = call(made, "POST", path, json=body_sent)
        assert status == code and "master's limiter" in body["reason"], (path, body)
    status, body = call(made, "POST", "/live/move", json={"processor": LIMITER, "direction": "up"})
    assert status in (400, 409) and "master's limiter" in body["reason"], body
    status, body = call(made, "POST", "/live/move", json={"processor": "limiter", "direction": "down"})
    assert status in (400, 409) and "master's limiter" in body["reason"], body
    assert fake.writes() == []
    assert fake.values_of[LIMITER] == LIMITER_SETTINGS
    assert fake.chains["master"] == ["master_return", "limiter", LIMITER]


def test_setting_the_limiter_to_its_own_settings_is_allowed():
    made, fake = limiter_agent()
    status, body = call(made, "POST", "/live/bypass", json={"processor": LIMITER, "bypassed": False})
    assert status == 200, body


def test_an_add_to_the_master_lands_in_front_of_the_limiter():
    made, fake = limiter_agent()
    status, body = call(made, "POST", "/live/add",
                        json={"track": "master", "uri": edits.DELAY, "name": "delay"})
    assert status == 200, body
    assert fake.chains["master"] == ["master_return", "limiter", "delay", LIMITER]
    status, body = call(made, "POST", "/live/add",
                        json={"track": "master", "uri": edits.DELAY, "name": "late", "after": LIMITER})
    assert status == 409 and "limiter stays last" in body["reason"], body


def test_live_plugins_says_the_limiter_is_the_compilers():
    made, fake = limiter_agent()
    fake.plugins = lambda connection: {
        name: {"track": track, **fake.classes.get(name, {})}
        for track, chain in fake.chains.items() for name in chain}
    status, body = call(made, "GET", "/live/plugins")
    assert status == 200, body
    roles = {name: p["role"] for name, p in body["processors"].items()}
    assert roles[LIMITER] == "limiter" and roles["limiter"] is None and roles["master_return"] == "return"


def test_an_undo_back_to_a_master_over_unity_is_refused():
    """A rig started before the cap, its master at +6 dB: turned down, the
    undo would put +6 back, and is refused as the route refuses it."""
    made, fake = limiter_agent()
    fake.values_of["master"]["parameters"]["gain"] = level_to_gain(6.0)
    status, body = call(made, "POST", "/live/parameter",
                        json={"processor": "master", "parameter": "gain", "value": level_to_gain(-3.0)})
    assert status == 200, body
    status, body = undo(made)
    assert status in (400, 409) and "stops at 0 dB" in body["reason"], body
    assert gain(fake) == pytest.approx(level_to_gain(-3.0))


def test_the_dip_puts_back_no_more_than_unity():
    """A master over the top (set beside the agent): an edit's dip brings it
    back at 0 dB, never above."""
    made, fake = limiter_agent()

    async def no_wait(seconds):
        return None

    made.sleep = no_wait
    fake.values_of["master"]["parameters"]["gain"] = level_to_gain(6.0)
    status, body = call(made, "POST", "/live/add", json={"track": "guitar", "uri": edits.DELAY, "name": "delay"})
    assert status == 200, body
    sets = [c for c in fake.calls if c[:3] == ("set_parameter", "master", "gain")]
    assert [c[3] for c in sets] == [0.0, MASTER_GAIN_MAX]
    assert gain(fake) == MASTER_GAIN_MAX


def old_rig_agent():
    """The limiter agent, but its master as a rig started before #137: no
    limiter on it."""
    made, fake = limiter_agent()
    fake.chains["master"].remove(LIMITER)
    return made, fake


def test_on_a_rig_from_before_the_limiter_it_cannot_be_added_live():
    made, fake = old_rig_agent()
    status, body = call(made, "POST", "/live/add",
                        json={"track": "master", "uri": edits.DELAY, "name": LIMITER})
    assert status == 400 and "a name the compiler gives" in body["reason"], body
    assert fake.writes() == [] and LIMITER not in fake.chains["master"]


@pytest.mark.parametrize("path, sent", [
    ("/live/parameter", {"processor": "master", "parameter": "pan", "value": 0.0}),
    ("/live/parameters", {"processor": "master", "parameters": {"gain": 0.5, "pan": 1.0}}),
])
def test_the_routes_refuse_the_master_pan_off_the_centre(path, sent):
    made, fake = limiter_agent()
    fake.values_of["master"]["parameters"]["pan"] = 0.5
    status, body = call(made, "POST", path, json=sent)
    assert status == 400 and "the master's pan stays at the centre" in body["reason"], body
    assert fake.writes() == []


def test_an_undo_back_to_a_master_pan_off_the_centre_is_refused():
    made, fake = limiter_agent()
    fake.values_of["master"]["parameters"]["pan"] = 0.0  # set beside the agent
    status, body = call(made, "POST", "/live/parameter",
                        json={"processor": "master", "parameter": "pan", "value": 0.5})
    assert status == 200, body
    status, body = undo(made)
    assert status in (400, 409) and "stays at the centre" in body["reason"], body
    assert fake.values_of["master"]["parameters"]["pan"] == 0.5


def test_the_dip_that_brings_an_old_master_down_says_so_in_the_answer_and_health():
    made, fake = limiter_agent()

    async def no_wait(seconds):
        return None

    made.sleep = no_wait
    fake.values_of["master"]["parameters"]["gain"] = level_to_gain(6.0)
    status, body = call(made, "POST", "/live/add", json={"track": "guitar", "uri": edits.DELAY, "name": "delay"})
    assert status == 200, body
    assert body["dip"]["capped"]["from_db"] == 6.0 and body["dip"]["capped"]["to_db"] == 0.0
    assert body["master_capped"].startswith("the master volume was at +6 dB, over the 0 dB it now stops at")
    status, health = call(made, "GET", "/health")
    assert health["agent"]["master_capped"]["from_db"] == 6.0, health["agent"]
    # A master already at or under the top says nothing of it.
    status, body = call(made, "POST", "/live/add", json={"track": "guitar", "uri": edits.DELAY, "name": "delay2"})
    assert status == 200 and "master_capped" not in body and "capped" not in body["dip"], body
