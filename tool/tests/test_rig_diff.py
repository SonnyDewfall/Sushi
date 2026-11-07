"""Switching rigs live (#93): `rigfile.diff`, the decision, on every pair of rigs.

`diff` says how to turn the running rig into the next one through the live
operations, or why a restart is needed. These pin what it decides without a
Sushi: on every ordered pair of the rigs in config/rigs/, a plan is checked by
playing it on a model of what a loaded config holds, and must leave exactly
what loading the next rig would; a restart must name only reasons the table
knows. The model is invented, not captured: it holds what a config states,
and treats a parameter it does not state as "at the plugin's default", which
is what Sushi is assumed to load (not observed here: no Sushi in this suite).
The agent's use of the plan, against a fake Sushi, is in test_agent.py.

The amp is in the model too, as what was last sent Carla: an `amp` step
sets it, and a restart (a config load) sets it to the next rig's `_amp`.
Nothing here is Carla's: what it holds cannot be read, only what was sent.
"""

from __future__ import annotations

import copy
import itertools
import re
from pathlib import Path

import pytest

from sushi_rig import rigfile, stereo
from sushi_rig.amp import PARAMETER_INDEX
from sushi_rig.live import protected_processor
from sushi_rig.rigfile import (
    RESTART_REASONS, SEND_UID, UNITY_STRIP, WIRING, NeedsRestart, SwitchPlan, apply_to_chains, diff,
    level_to_gain,
)

ROOT = Path(__file__).resolve().parents[2]
RIGS = sorted((ROOT / "config" / "rigs").glob("*.yaml"))
COMPILED = {path.stem: rigfile.emit_rig(rigfile.load_rig(path)) for path in RIGS}
PAIRS = list(itertools.permutations(sorted(COMPILED), 2))


def compiled(name):
    return copy.deepcopy(COMPILED[name])


# --------------------------------------------------------------------------- #
# A model of what a loaded config holds, and the plan played on it
# --------------------------------------------------------------------------- #

def _identity(plugin):
    return (plugin.get("type"), plugin.get("uri"), plugin.get("uid"), plugin.get("path"))


class Model:
    """What a rig holds as far as configs can say: chains, which plugin each
    processor is, the parameters set on it (absent: the plugin's default) and
    its bypass. `play` applies a plan step by step and fails the test on any
    step the agent would be refused: a processor that is not there, a name
    taken, a send or return moved, an add behind a send. A plugin's copy to
    stereo goes with it, as the agent's remove, add and move take it
    (`agent.check_remove`, `place_switch_add`, `live.decide_move`)."""

    def __init__(self, config):
        self.tracks = {t["name"] for t in config["tracks"]}
        self.chains = {t["name"]: [p["name"] for p in t["plugins"]] for t in config["tracks"]}
        self.plugins = {p["name"]: _identity(p) for t in config["tracks"] for p in t["plugins"]}
        self.uids = {p["name"]: p.get("uid") for t in config["tracks"] for p in t["plugins"]}
        state = {e["processor"]: e for e in config.get("initial_state", [])}
        self.values = {name: dict((state.get(name) or {}).get("parameters") or {})
                       for name in list(self.plugins) + list(self.tracks)}
        self.bypassed = {name: bool((state.get(name) or {}).get("bypassed", False)) for name in self.plugins}
        # What Carla was sent: `up` replays the config's `_amp` parameters.
        self.amp = dict(((config.get("_amp") or {}).get("parameters")) or {})

    def _track(self, name):
        return next((t for t, chain in self.chains.items() if name in chain), None)

    def play(self, plan):
        for step in plan.steps:
            args = step.args
            if step.op == "remove":
                track = self._track(args["processor"])
                assert track is not None, f"remove of {args['processor']!r}, which is not there"
                assert not protected_processor(track, args["processor"], self.uids.get(args["processor"]))
                gone = [args["processor"]]
                chain = self.chains[track]
                at = chain.index(args["processor"])
                if at + 1 < len(chain) and chain[at + 1] == stereo.copy_name(args["processor"]):
                    gone.append(chain[at + 1])
                for name in gone:
                    chain.remove(name)
                    for table in (self.plugins, self.values, self.bypassed, self.uids):
                        table.pop(name, None)
            elif step.op == "add":
                name, track, before = args["name"], args["track"], args["before"]
                assert name not in self.plugins and name not in self.tracks, f"{name!r} is taken"
                chain = self.chains[track]
                names = [name] + ([args["copy"]] if args.get("copy") else [])
                if before is None:
                    assert not any(protected_processor(track, n, self.uids.get(n)) and n.endswith("_send")
                                   for n in chain), f"{name!r} added behind the send"
                    at = len(chain)
                else:
                    assert before in chain, f"{name!r} goes in front of {before!r}, which is not there"
                    at = chain.index(before)
                chain[at:at] = names
                self.plugins[name] = ("lv2", args["uri"], None, None)
                self.values[name] = {}  # a fresh plugin, at its defaults
                self.bypassed[name] = False
                if args.get("copy"):
                    copy_ = args["copy"]
                    self.plugins[copy_] = ("internal", None, stereo.COPY_UID, None)
                    self.uids[copy_], self.values[copy_], self.bypassed[copy_] = stereo.COPY_UID, {}, False
            elif step.op == "move":
                track = self._track(args["processor"])
                chain = self.chains[track]
                units = stereo.units(chain, self.uids)
                i = next(k for k, unit in enumerate(units) if unit[0] == args["processor"])
                j = i - 1 if args["direction"] == "up" else i + 1
                assert 0 <= j < len(units), f"move of {args['processor']!r} past the end"
                for name in (units[i][0], units[j][0]):
                    assert not protected_processor(track, name, self.uids.get(name)), f"{name!r} moved"
                self.chains[track] = stereo.step_units(chain, args["processor"],
                                                       -1 if args["direction"] == "up" else 1, self.uids)
            elif step.op == "parameter":
                assert args["processor"] in self.values, f"parameter of {args['processor']!r}, not there"
                self.values[args["processor"]][args["parameter"]] = args["value"]
            elif step.op == "bypass":
                assert args["processor"] in self.bypassed, f"bypass of {args['processor']!r}, not a processor"
                self.bypassed[args["processor"]] = args["bypassed"]
            elif step.op == "amp":
                assert args["name"] in PARAMETER_INDEX, f"amp parameter {args['name']!r}, not the amp's"
                self.amp[args["name"]] = args["value"]
            else:
                raise AssertionError(f"unknown op {step.op!r}")
        return self

    def held(self):
        """Comparable: a value at its known default (a strip at unity, a
        send's gain at 0 dB) is the same as one not set."""
        known = {**{t: UNITY_STRIP for t in self.tracks},
                 **{n: {"gain": level_to_gain(0.0)} for n, uid in self.uids.items() if uid == SEND_UID}}
        values = {
            name: {p: round(v, 6) for p, v in params.items()
                   if not (p in known.get(name, {}) and abs(v - known[name][p]) < 1e-9)}
            for name, params in self.values.items()
        }
        return self.chains, self.plugins, values, self.bypassed, self.amp


def capture_of(config):
    """What `live.capture` would read from a rig loaded from `config`, as far
    as the model knows it: every processor's stated parameters and its bypass,
    and the chains."""
    model = Model(config)
    # Sushi reads back every parameter, so a track's strip and a send's gain
    # are there whether the config stated them or not, at their defaults.
    processors = {name: {"parameters": {**UNITY_STRIP, **model.values[name]}} for name in model.tracks}
    for name in model.plugins:
        defaults = {"gain": level_to_gain(0.0)} if model.uids.get(name) == SEND_UID else {}
        processors[name] = {"parameters": {**defaults, **model.values[name]}, "bypassed": model.bypassed[name]}
    return {"processors": processors, "tracks": {t: list(c) for t, c in model.chains.items()}}


def shape(plan):
    """The plan's ops as letters, to check their order."""
    return "".join({"bypass": "B", "remove": "R", "move": "M", "add": "A", "parameter": "P", "amp": "N"}[s.op]
                   for s in plan.steps)


def inversions(before, after):
    return sum(1 for i, j in itertools.combinations(range(len(after)), 2)
               if before.index(after[i]) > before.index(after[j]))


# --------------------------------------------------------------------------- #
# Every pair of rigs in config/rigs/
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("running, upcoming", PAIRS, ids=[f"{a}->{b}" for a, b in PAIRS])
def test_every_pair_is_a_plan_that_reaches_the_next_rig_or_a_named_restart(running, upcoming):
    """The contract: a plan, played on what the running rig holds, leaves
    exactly what loading the next rig would -- chains, plugins, values and
    bypass -- with every step one the agent would make; or a restart, for
    reasons the table names."""
    before, after = compiled(running), compiled(upcoming)
    decided = diff(before, after)
    if isinstance(decided, NeedsRestart):
        assert decided.reasons, "a restart says why"
        assert set(decided.codes) <= set(RESTART_REASONS)
        return
    assert Model(before).play(decided).held() == Model(after).held()
    chains = {t["name"]: [p["name"] for p in t["plugins"]] for t in before["tracks"]}
    assert apply_to_chains(chains, decided) == Model(after).chains
    # The order the docstring gives: bypass on, removes, moves, each add with
    # its bypass, parameters, the amp's, then what comes in.
    assert re.fullmatch(r"B*R*M*(AB)*P*N*B*", shape(decided)), shape(decided)


@pytest.mark.parametrize("running, upcoming", PAIRS, ids=[f"{a}->{b}" for a, b in PAIRS])
def test_every_pair_restarts_exactly_where_the_live_operations_cannot_reach(running, upcoming):
    """For the committed rigs, which have no MIDI maps and one host config,
    what decides is the amp's presence and model (Carla loads a model only as
    it starts, and is started only with the rig) and the tracks (none are
    made or removed live): a restart for those, and only those. The amp's
    parameters differing is not one: they are `amp` steps."""
    before, after = compiled(running), compiled(upcoming)
    decided = diff(before, after)
    tracks_differ = [t["name"] for t in before["tracks"]] != [t["name"] for t in after["tracks"]]

    def carla(config):
        amp = config.get("_amp")
        return None if amp is None else {k: v for k, v in amp.items() if k != "parameters"}

    amp_differs = carla(before) != carla(after)
    assert isinstance(decided, NeedsRestart) == (tracks_differ or amp_differs)
    if isinstance(decided, NeedsRestart):
        if tracks_differ:
            assert {"track_added", "track_removed"} & set(decided.codes)
        if amp_differs:
            assert "amp" in decided.codes
    elif before["_amp"]["parameters"] != after["_amp"]["parameters"]:
        assert any(s.op == "amp" for s in decided.steps), "the amp's settings are steps"


@pytest.mark.parametrize("running, upcoming", PAIRS, ids=[f"{a}->{b}" for a, b in PAIRS])
def test_a_reading_that_is_the_running_config_makes_the_same_plan(running, upcoming):
    """With the rig as its config says, the plan made from a live reading is
    the plan made from the config."""
    before, after = compiled(running), compiled(upcoming)
    assert diff(before, after, capture_of(before)) == diff(before, after)


def test_both_kinds_of_pair_are_among_the_rigs():
    """Not vacuous: some pairs switch live and some restart."""
    kinds = {type(diff(compiled(a), compiled(b))) for a, b in PAIRS}
    assert kinds == {SwitchPlan, NeedsRestart}


def test_how_many_of_the_committed_pairs_switch_live():
    """The README's count. 26 of the 110 while the amp's parameters were a
    restart; with them as steps, every pair of two rigs with the same tracks
    and the same amp model (all nine with an amp use one) is live. What is
    left restarts for an amp on one side only, or for the tracks."""
    live = [pair for pair in PAIRS if isinstance(diff(compiled(pair[0]), compiled(pair[1])), SwitchPlan)]
    assert (len(live), len(PAIRS)) == (72, 110)
    amp_only = [pair for pair in PAIRS if pair not in live
                and diff(compiled(pair[0]), compiled(pair[1])).codes == ["amp"]]
    assert len(amp_only) == 18, "an amp on one side only, nothing else between them"


def test_moves_are_the_fewest_steps_that_put_the_survivors_in_order():
    """electric-delay to electric-chorus moves two survivors; the count is
    the inversions between the two orders, and none is a send. A plugin and
    its copy to stereo are one survivor: they move as one."""
    plan = diff(compiled("electric-delay"), compiled("electric-chorus"))
    moves = [s for s in plan.steps if s.op == "move"]
    board, chorus = Model(compiled("electric-delay")), Model(compiled("electric-chorus"))
    kept = [n for n in board.chains["board"] if chorus.plugins.get(n) == board.plugins[n]
            and not stereo.is_copy(n, board.uids.get(n))]
    target = [n for n in chorus.chains["board"] if n in kept]
    assert kept != target and len(moves) == inversions(kept, target) == 2
    assert all(not s.args["processor"].endswith("_send") for s in moves)


def test_a_reversed_chain_takes_every_inversion_and_nothing_more():
    before = compiled("electric-delay")
    after = copy.deepcopy(before)
    board = next(t for t in after["tracks"] if t["name"] == "board")
    pedals, send = board["plugins"][:-1], board["plugins"][-1]
    # Reversed as units: a plugin keeps its copy to stereo straight after it.
    by_name = {p["name"]: p for p in pedals}
    units = stereo.units([p["name"] for p in pedals])
    assert any(len(unit) == 2 for unit in units), "not vacuous: some pedals are mono"
    board["plugins"] = [by_name[n] for unit in reversed(units) for n in unit] + [send]
    plan = diff(before, after)
    assert [s.op for s in plan.steps] == ["move"] * (len(units) * (len(units) - 1) // 2)
    assert Model(before).play(plan).held() == Model(after).held()


# --------------------------------------------------------------------------- #
# Each reason to restart, by itself
# --------------------------------------------------------------------------- #

def _board(config):
    return next(t for t in config["tracks"] if t["name"] == "board")


def _entry(config, processor):
    return next(e for e in config["initial_state"] if e["processor"] == processor)


def _codes(running, upcoming, live=None):
    decided = diff(running, upcoming, live)
    assert isinstance(decided, NeedsRestart), decided
    return decided.codes


def _changed(change):
    """electric-delay, and a copy of it with `change` made."""
    before = compiled("electric-delay")
    after = copy.deepcopy(before)
    change(after)
    return before, after


def test_the_same_rig_is_an_empty_plan():
    assert diff(compiled("electric-delay"), compiled("electric-delay")) == SwitchPlan(())


def test_the_host_config_is_read_only_at_start():
    before, after = _changed(lambda c: c["host_config"].update(samplerate=44100))
    assert _codes(before, after) == ["host"]


def test_cc_mappings_differ_is_a_restart_since_sushi_loads_them_with_the_config():
    mapping = {"port": 0, "channel": "all", "cc_number": 11, "plugin_name": "delay",
               "parameter_name": "Feedback", "min_range": 0.0, "max_range": 1.0, "mode": "absolute"}
    before, after = _changed(lambda c: c.update(midi={"cc_mappings": [mapping]}))
    assert _codes(before, after) == ["midi"]


def test_the_agents_own_bindings_and_the_provenance_may_differ():
    """`_midi_agent` is the agent's own listener, which follows the next rig;
    `_meta` is where the config came from. Neither is Sushi's."""
    before, after = _changed(lambda c: c.update(_midi_agent=[{"programs": "rigs", "port": 0,
                                                               "channel": "all"}], _meta={"name": "x"}))
    assert diff(before, after) == SwitchPlan(())


def test_an_unknown_section_is_refused_rather_than_assumed_harmless():
    before, after = _changed(lambda c: c.update(osc={"enable_all_processor_outputs": True}))
    assert _codes(before, after) == ["config_section"]


def test_the_amps_model_is_loaded_only_as_carla_starts_and_is_a_restart():
    before, after = _changed(lambda c: c["_amp"].update(model="amp/models/Other.nam"))
    assert _codes(before, after) == ["amp"]
    assert "becomes 'amp/models/Other.nam'" in diff(before, after).reasons[0].detail


def test_an_amp_on_one_side_only_is_a_restart_since_carla_and_its_wiring_come_with_the_rig():
    before, after = _changed(lambda c: c.pop("_amp"))
    assert _codes(before, after) == ["amp"]
    assert _codes(after, before) == ["amp"]
    assert "the next rig has an amp" in diff(after, before).reasons[0].detail


def amp_steps(plan):
    return [s.as_dict() for s in plan.steps if s.op == "amp"]


def test_the_amps_parameters_are_steps_sent_as_the_route_sends_them():
    """accustomed to electric-chorus-2: two of Sushi's parameters, and the
    amp's two levels, each an `amp` step whose args are the body of POST
    /amp/parameter, in Carla's parameter order, after Sushi's."""
    plan = diff(compiled("accustomed"), compiled("electric-chorus-2"))
    assert shape(plan) == "PPNN"
    assert amp_steps(plan) == [
        {"op": "amp", "name": "Input Lvl", "value": 0.0},
        {"op": "amp", "name": "Output Lvl", "value": 6.0},
    ]


def test_an_amp_knob_moved_since_the_rig_came_up_is_put_where_the_next_rig_says():
    """With a live reading the plan compares with what the agent last sent
    Carla, as it compares Sushi's values with what Sushi reports: the same
    rig's `_amp`, after a knob was turned, is a step back."""
    before = compiled("electric-delay")
    sent = {"Input Lvl": 0.0, "Output Lvl": 3.0, "Quality": 1.0}
    reading = {**capture_of(before), "amp": {"parameters": sent}}
    assert amp_steps(diff(before, compiled("electric-octave-up"), reading)) == [
        {"op": "amp", "name": "Output Lvl", "value": -0.4}]
    assert amp_steps(diff(before, compiled("electric-octave-up"))) == [], \
        "without a reading, the running rig's _amp is what was sent"


def test_a_parameter_never_sent_is_sent_since_what_carla_holds_cannot_be_read():
    before = compiled("electric-delay")
    reading = {**capture_of(before), "amp": {"parameters": {"Input Lvl": 0.0, "Output Lvl": None, "Quality": 1.0}}}
    assert amp_steps(diff(before, compiled("electric-delay"), reading)) == [
        {"op": "amp", "name": "Output Lvl", "value": -0.4}]


def test_an_amp_parameter_the_next_rig_leaves_unstated_is_a_restart_not_a_guessed_default():
    before, after = _changed(lambda c: c["_amp"]["parameters"].pop("Quality"))
    assert _codes(before, after) == ["amp"]
    assert "Quality unstated" in diff(before, after).reasons[0].detail
    # Unstated in both, and never sent: it stays at the plugin's default either way.
    after_too = copy.deepcopy(after)
    assert diff(after, after_too) == SwitchPlan(())


@pytest.mark.parametrize("parameters, said", [
    ({"Input Lvl": 0.0, "Output Lvl": -0.4, "Quality": 1.0, "Gain": 1.0}, "'Gain', which the amp does not have"),
    ({"Input Lvl": float("nan"), "Output Lvl": -0.4, "Quality": 1.0}, "not a value to send"),
    ({"Input Lvl": "loud", "Output Lvl": -0.4, "Quality": 1.0}, "not a value to send"),
    ({"Input Lvl": True, "Output Lvl": -0.4, "Quality": 1.0}, "not a value to send"),
])
def test_an_amp_parameter_that_cannot_be_sent_is_a_restart(parameters, said):
    before, after = _changed(lambda c: c["_amp"].update(parameters=parameters))
    assert _codes(before, after) == ["amp"]
    assert said in diff(before, after).reasons[0].detail


def test_an_amp_value_past_the_range_is_a_step_to_its_end_as_carla_is_sent_it():
    before, after = _changed(lambda c: c["_amp"]["parameters"].update({"Output Lvl": 99.0}))
    assert amp_steps(diff(before, after)) == [{"op": "amp", "name": "Output Lvl", "value": 20.0}]


def test_a_channel_on_another_input_is_a_restart_since_only_the_patchbay_wires_it():
    before, after = _changed(lambda c: c[WIRING]["board"].update(source="mic-in"))
    assert _codes(before, after) == ["wiring"]


def test_a_config_compiled_before_the_wiring_was_recorded_is_a_restart():
    before, after = _changed(lambda c: None)
    del before[WIRING]
    assert _codes(before, after) == ["wiring_unknown"]


def test_a_track_added_or_removed_is_a_restart():
    assert "track_added" in _codes(compiled("electric_board"), compiled("guitar_vocal"))
    assert "track_removed" in _codes(compiled("guitar_vocal"), compiled("electric_board"))


def test_tracks_in_another_order_are_a_restart():
    before = compiled("guitar_vocal")
    after = copy.deepcopy(before)
    after["tracks"][0], after["tracks"][1] = after["tracks"][1], after["tracks"][0]
    assert "track_order" in _codes(before, after)


def test_a_tracks_channel_count_is_a_restart():
    before, after = _changed(lambda c: _board(c).update(channels=1))
    assert _codes(before, after) == ["track"]


def test_a_plugin_that_is_not_lv2_cannot_be_added_live():
    before, after = _changed(lambda c: _board(c)["plugins"].insert(
        0, {"name": "vst", "type": "vst3x", "path": "/x.vst3", "uid": "x"}))
    assert _codes(before, after) == ["plugin_type"]


def test_a_program_would_need_setting_and_nothing_live_sets_one():
    def program(c):
        entry = _entry(c, "eq")
        entry.pop("parameters", None)
        entry["program"] = 2
    before, after = _changed(program)
    assert _codes(before, after) == ["program"]


def test_a_property_would_need_setting_and_nothing_live_sets_one():
    """A send's destination above all: a send to anything but the master's
    return is a channel silent in the monitors."""
    before, after = _changed(lambda c: _entry(c, "board_send")["properties"].update(destination_name="x"))
    assert _codes(before, after) == ["properties"]


def test_the_send_or_return_replaced_is_refused_as_protected():
    before, after = _changed(lambda c: _board(c)["plugins"][-1].update(uid="sushi.testing.other"))
    assert "protected" in _codes(before, after)


def test_a_send_parameter_left_at_an_unknown_default_is_a_restart():
    before, after = _changed(lambda c: None)
    _entry(before, "board_send").setdefault("parameters", {})["channel_offset"] = 0.3
    assert _codes(before, after) == ["parameter_default"]


def test_a_processor_a_cc_mapping_drives_is_not_replaced_live():
    """Bound at load (a guess): replaced, it would no longer follow its pedal."""
    mapping = {"port": 0, "channel": "all", "cc_number": 11, "plugin_name": "delay",
               "parameter_name": "Feedback", "min_range": 0.0, "max_range": 1.0, "mode": "absolute"}

    def replaced(c):
        c["midi"] = {"cc_mappings": [mapping]}
        next(p for p in _board(c)["plugins"] if p["name"] == "delay")["uri"] = "http://example.org/other"

    before, after = _changed(replaced)
    before["midi"] = {"cc_mappings": [mapping]}
    assert _codes(before, after) == ["midi_target"]


def test_a_config_with_no_tracks_is_unreadable():
    assert _codes({}, compiled("electric-delay")) == ["unreadable"]


def test_every_reason_is_said_not_the_first():
    def both(c):
        c["host_config"]["samplerate"] = 44100
        c["_amp"] = {"plugin": "nam", "model": "amp/models/Other.nam"}
    before, after = _changed(both)
    assert set(_codes(before, after)) == {"host", "amp"}


# --------------------------------------------------------------------------- #
# Replacing, and the reading of the rig as it is
# --------------------------------------------------------------------------- #

def test_a_parameter_the_next_rig_leaves_at_its_default_replaces_the_processor():
    """A plugin's defaults are known only to the plugin, so a fresh one is
    the only way to them: removed, added in the same place, its stated
    parameters set, and on only if the next rig has it on."""
    before, after = _changed(lambda c: _entry(c, "delay")["parameters"].pop("Feedback"))
    plan = diff(before, after)
    assert [s.as_dict() for s in plan.steps if s.op in ("remove", "add")] == [
        {"op": "remove", "processor": "delay"},
        {"op": "add", "track": "board", "name": "delay", "uri": "http://plugin.org.uk/swh-plugins/lcrDelay",
         "before": next(p["name"] for p in _board(after)["plugins"][
             [q["name"] for q in _board(after)["plugins"]].index("delay") + 1:])},
    ]
    assert "Feedback" not in {s.args["parameter"] for s in plan.steps
                              if s.op == "parameter" and s.args["processor"] == "delay"}
    assert Model(before).play(plan).held() == Model(after).held()


def test_a_plugin_under_the_same_name_is_a_different_processor():
    before, after = _changed(lambda c: next(
        p for p in _board(c)["plugins"] if p["name"] == "delay").update(uri="http://drobilla.net/plugins/mda/Delay"))
    plan = diff(before, after)
    assert ("remove", "delay") in [(s.op, s.args.get("processor")) for s in plan.steps]
    assert ("add", "delay") in [(s.op, s.args.get("name")) for s in plan.steps]


def test_a_knob_turned_during_the_last_song_is_put_where_the_next_rig_says():
    """Equal in both files, so the config alone asks for nothing; the reading
    says the rig holds something else, and the plan puts it right rather than
    leave verification to refuse it."""
    before, after = compiled("electric-delay"), compiled("electric-delay")
    reading = capture_of(before)
    reading["processors"]["eq"]["parameters"]["Low"] = 0.9
    reading["processors"]["delay"]["bypassed"] = not reading["processors"]["delay"]["bypassed"]
    assert diff(before, after) == SwitchPlan(())
    steps = [s.as_dict() for s in diff(before, after, reading).steps]
    assert {"op": "parameter", "processor": "eq", "parameter": "Low",
            "value": _entry(after, "eq")["parameters"]["Low"]} in steps
    assert {"op": "bypass", "processor": "delay", "bypassed": _entry(after, "delay").get("bypassed", False)} in steps


def test_a_sends_gain_moved_live_goes_back_to_unity_where_the_next_rig_says_nothing():
    """A blend pedal moves a send's gain; a restart would bring it back to
    0 dB, so the switch does, the send's default being known."""
    before = compiled("electric-delay")
    reading = capture_of(before)
    reading["processors"]["board_send"]["parameters"]["gain"] = 0.2
    steps = [s.as_dict() for s in diff(before, compiled("electric-delay"), reading).steps]
    assert steps == [{"op": "parameter", "processor": "board_send", "parameter": "gain",
                      "value": level_to_gain(0.0)}]


def test_a_chain_changed_live_is_a_restart_since_the_plan_would_be_made_on_positions_not_there():
    before = compiled("electric-delay")
    reading = capture_of(before)
    reading["tracks"]["board"].insert(0, "slap")
    assert _codes(before, compiled("electric-octave-up"), reading) == ["drifted"]


def test_a_bypass_sushi_would_not_report_is_set_rather_than_assumed():
    before = compiled("electric-delay")
    reading = capture_of(before)
    reading["processors"]["eq"].pop("bypassed")
    steps = [s.as_dict() for s in diff(before, compiled("electric-delay"), reading).steps]
    assert steps == [{"op": "bypass", "processor": "eq", "bypassed": False}]


def test_steps_name_only_what_the_routes_take():
    """Each step's args are its route's body, so the agent applies them
    through the same calls, and an add's is Sushi's own placing, with the
    copy to stereo that goes in with a mono plugin where it has one."""
    bodies = {"remove": {"processor"}, "move": {"processor", "direction"}, "bypass": {"processor", "bypassed"},
              "parameter": {"processor", "parameter", "value"}, "add": {"track", "name", "uri", "before"},
              "amp": {"name", "value"}}
    for running, upcoming in (("electric-chorus", "electric-delay"), ("accustomed", "electric-chorus-2")):
        plan = diff(compiled(running), compiled(upcoming))
        assert plan.steps and all(set(s.args) - ({"copy"} if s.op == "add" else set()) == bodies[s.op]
                                  for s in plan.steps)
    adds = [s for a, b in PAIRS for s in getattr(diff(compiled(a), compiled(b)), "steps", ()) if s.op == "add"]
    with_copy = [s for s in adds if "copy" in s.args]
    assert with_copy, "not vacuous: some pair adds a mono plugin"
    assert all(s.args["copy"] == stereo.copy_name(s.args["name"]) for s in with_copy)


# --------------------------------------------------------------------------- #
# Review of the branch
# --------------------------------------------------------------------------- #

def test_what_the_plan_set_is_checked_against_its_own_targets():
    """`unapplied` reads each parameter's and processor's last target out of
    the plan: an added processor is bypassed, then brought in."""
    from sushi_rig.rigfile import SwitchStep, unapplied

    plan = SwitchPlan((
        SwitchStep("bypass", {"processor": "eq", "bypassed": True}),
        SwitchStep("parameter", {"processor": "board_send", "parameter": "gain", "value": 0.8333}),
        SwitchStep("bypass", {"processor": "eq", "bypassed": False}),
    ))
    held = {"processors": {"eq": {"bypassed": False, "parameters": {}},
                           "board_send": {"bypassed": False, "parameters": {"gain": 0.83330001}}}}
    assert unapplied(plan, held) == []
    held["processors"]["board_send"]["parameters"]["gain"] = 0.2
    held["processors"]["eq"]["bypassed"] = True
    assert unapplied(plan, held) == [
        "board_send: gain was set to 0.8333 and reads back 0.2",
        "eq: bypass was set to False and reads back True",
    ]
    del held["processors"]["eq"]
    assert "eq: bypass was set to False and reads back None" in unapplied(plan, held)


def test_a_restart_for_a_default_names_what_is_left_at_it():
    """A plugin that cannot be added fresh (not LV2) whose properties the next
    rig leaves unset: said as its properties, not as its program."""
    plugin = {"name": "vst", "type": "vst3x", "path": "/x.vst3", "uid": "x"}

    def with_vst(c):
        _board(c)["plugins"].insert(0, dict(plugin))

    before, after = _changed(with_vst)
    with_vst(before)
    before["initial_state"].append({"processor": "vst", "properties": {"preset": "a"}})
    decided = diff(before, after)
    (reason,) = [r for r in decided.reasons if r.code == "parameter_default"]
    assert "its properties (preset)" in reason.detail and "program" not in reason.detail


# --------------------------------------------------------------------------- #
# Review of the amp branch
# --------------------------------------------------------------------------- #

def test_with_no_amp_running_neither_rigs_amp_is_a_step_or_a_reason():
    """`up --no-amp`: no Carla runs, and a restart would start none, so a
    model that differs, or an amp on one side only, is no reason to restart
    and nothing is sent."""
    before, after = _changed(lambda c: c["_amp"].update(model="amp/models/Other.nam",
                                                         parameters={"Output Lvl": 9.0}))
    assert diff(before, after, amp_off=True) == SwitchPlan(())
    no_amp = copy.deepcopy(before)
    no_amp.pop("_amp")
    assert diff(no_amp, before, amp_off=True) == SwitchPlan(())
    assert diff(before, no_amp, amp_off=True) == SwitchPlan(())
    assert _codes(before, after) == ["amp"], "with an amp running it is still a restart"


def test_one_model_written_two_ways_is_the_same_model(tmp_path):
    """Compared as the file it names, from the checkout's root."""
    models = tmp_path / "amp" / "models"
    models.mkdir(parents=True)
    (models / "M.nam").write_text("{}")
    before, after = _changed(lambda c: c["_amp"].update(model=str(models / "M.nam")))
    before["_amp"]["model"] = "amp/models/M.nam"
    assert isinstance(diff(before, after, root=tmp_path), SwitchPlan)
    assert _codes(before, after) == ["amp"], "without a root, as written"
    other = copy.deepcopy(after)
    (models / "N.nam").write_text("{}")
    other["_amp"]["model"] = str(models / "N.nam")
    assert diff(before, other, root=tmp_path).codes == ["amp"], "another file is another model"
