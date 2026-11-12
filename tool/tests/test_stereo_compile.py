"""The compiler's copy to stereo after a mono plugin (stereo.py), without a Sushi.

What these pin is the decisions: which plugins get a copy, from what table,
what is refused, and that the copy stays invisible and attached -- to the rig
file, SAVE, the live diff, a move, an add, a remove, a bypass and undo. What
it sounds like is measured in CI's container (test_stereo.py); the live
calls that make a copy are not run against a live Sushi anywhere.
"""

from __future__ import annotations

import copy
from pathlib import Path

import pytest
import yaml

from sushi_rig import rigfile, stereo
from sushi_rig.agent import (
    AddRequest, AgentError, Conflict, add_request, chain_after_move, check_add, check_bypass,
    check_remove, check_undo_move, StaleUndo,
)
from sushi_rig.library import build_library
from sushi_rig.live import decide_move, protected_processor
from sushi_rig.plugins import read_manifest
from sushi_rig.stereo import COPY_UID, Ports

ROOT = Path(__file__).resolve().parents[2]
COMP = "http://guitarix.sourceforge.net/plugins/gx_compressor#_compressor"
TS9 = "http://guitarix.sourceforge.net/plugins/gxts9#ts9sim"
EQ = "http://distrho.sf.net/plugins/3BandEQ"
DELAY = "http://drobilla.net/plugins/mda/Delay"


def rig(chain, master=(), name="board"):
    return {
        "kind": "rig", "schema_version": 1, "name": "r",
        "channels": [{"name": name, "input": "guitar-in", "width": "mono", "chain": list(chain)}],
        "master": {"output": "monitor-out", "chain": list(master)},
    }


def chains_of(config):
    return {t["name"]: [p["name"] for p in t["plugins"]] for t in config["tracks"]}


# --------------------------------------------------------------------------- #
# The table
# --------------------------------------------------------------------------- #

def test_the_committed_table_reads_and_covers_the_manifest_but_the_amp():
    table = stereo.load_ports(ROOT)
    manifest = read_manifest((ROOT / "plugin-manifest.txt").read_text())
    assert set(manifest) - set(table) == {"http://github.com/mikeoliphant/neural-amp-modeler-lv2"}
    assert set(table) <= set(manifest)
    assert table[COMP] == Ports(1, 1) and table[EQ] == Ports(2, 2)


@pytest.mark.parametrize("text, problem", [
    ("1 1\n", "expected 'inputs outputs uri'"),
    ("one 1 urn:x\n", "expected 'inputs outputs uri'"),
    ("1 1 urn:x\n2 2 urn:x\n", "urn:x is listed twice"),
])
def test_a_malformed_table_is_refused_with_its_line(text, problem):
    with pytest.raises(stereo.PortsError, match=problem):
        stereo.read_ports("# a comment\n\n" + text)


# --------------------------------------------------------------------------- #
# The compiler
# --------------------------------------------------------------------------- #

def test_a_copy_follows_each_mono_plugin_and_no_stereo_one():
    config = rigfile.emit_rig(rigfile.parse_rig(rig(
        [{"name": "comp", "uri": COMP}, {"name": "eq", "uri": EQ}, {"name": "ts", "uri": TS9, "bypassed": True}],
        master=[{"name": "ceiling", "uri": TS9}],
    )))
    assert chains_of(config) == {
        "board": ["comp", "comp_to_stereo", "eq", "ts", "ts_to_stereo", "board_send"],
        "master": ["master_return", "ceiling", "ceiling_to_stereo"],
    }
    copies = [p for t in config["tracks"] for p in t["plugins"] if p["name"].endswith("_to_stereo")]
    assert all(p == {"name": p["name"], "type": "internal", "uid": COPY_UID} for p in copies)
    assert not any(e["processor"].endswith("_to_stereo") for e in config["initial_state"]), \
        "the copy has nothing to set, and is never bypassed by the config"


def test_every_committed_guitar_chain_gets_its_copies():
    """Of the rigs in config/rigs/ as they are, and of the frozen ones, which
    are known to hold both plugins (rig_fixtures.py)."""
    from rig_fixtures import FROZEN, committed, compiled

    seen = set()
    for path in committed() + sorted(FROZEN.glob("*.yaml")):
        config = compiled(path)
        for track in config["tracks"]:
            names = [p["name"] for p in track["plugins"]]
            for plugin in track["plugins"]:
                if plugin.get("uri") in (COMP, TS9):
                    assert names[names.index(plugin["name"]) + 1] == plugin["name"] + "_to_stereo", path.name
                    seen.add(plugin["uri"])
    assert seen == {COMP, TS9}, "no rig holds the compressor and the overdrive, so this proved nothing"


@pytest.mark.parametrize("entry, problem", [
    ({"name": "x", "uri": "urn:not-in-the-table"}, "is not in plugin-ports.txt"),
    ({"name": "amp", "uri": "http://github.com/mikeoliphant/neural-amp-modeler-lv2"}, "runs in Carla"),
])
def test_a_plugin_the_compiler_cannot_place_is_refused_by_name(entry, problem):
    with pytest.raises(rigfile.RigFileError, match=problem) as caught:
        rigfile.emit_rig(rigfile.parse_rig(rig([entry])))
    assert repr(entry["name"]) in str(caught.value)


def test_a_plugin_with_no_audio_output_is_refused():
    with pytest.raises(rigfile.RigFileError, match="no audio output"):
        rigfile.compile_rig(rigfile.parse_rig(rig([{"name": "meter", "uri": "urn:meter"}])),
                            ports={"urn:meter": Ports(2, 0)})


def test_an_internal_plugin_is_left_as_written():
    spec, _ = rigfile.compile_rig(rigfile.parse_rig(rig([{"name": "g", "uid": "sushi.testing.gain",
                                                            "type": "internal"}])), ports={})
    assert [p.name for p in spec.tracks[0].plugins] == ["g", "board_send"]


@pytest.mark.parametrize("raw", [
    rig([{"name": "comp_to_stereo", "uri": EQ}]),
    rig([], name="lead_to_stereo"),
])
def test_the_copys_suffix_is_reserved(raw):
    with pytest.raises(rigfile.RigFileError, match="_to_stereo"):
        rigfile.compile_rig(rigfile.parse_rig(raw))


def test_a_captured_order_cannot_separate_a_plugin_from_its_copy():
    config = rigfile.emit_rig(
        rigfile.parse_rig(rig([{"name": "comp", "uri": COMP}, {"name": "eq", "uri": EQ}])),
        {"tracks": {"board": ["comp", "eq", "comp_to_stereo", "board_send"]},
         "processors": {"comp_to_stereo": {"bypassed": True, "parameters": {}}}},
    )
    assert chains_of(config)["board"] == ["comp", "comp_to_stereo", "eq", "board_send"]
    assert "comp_to_stereo" not in {e["processor"] for e in config["initial_state"]}


def test_a_save_writes_nothing_of_the_copies():
    raw = rig([{"name": "comp", "uri": COMP}, {"name": "eq", "uri": EQ}])
    captured = {
        "tracks": {"board": ["eq", "comp", "comp_to_stereo", "board_send"], "master": ["master_return"]},
        "processors": {"comp": {"bypassed": False, "parameters": {"RATIO": 0.5}},
                       "comp_to_stereo": {"bypassed": False, "parameters": {}},
                       "eq": {"bypassed": False, "parameters": {}}},
    }
    folded = rigfile.fold_state(copy.deepcopy(raw), captured)
    assert [e["name"] for e in folded["channels"][0]["chain"]] == ["eq", "comp"]
    assert "_to_stereo" not in yaml.safe_dump(folded)


def test_a_capture_naming_a_copy_of_nothing_the_rig_has_is_still_refused():
    raw = rig([{"name": "eq", "uri": EQ}])
    with pytest.raises(rigfile.RigFileError, match="ghost_to_stereo"):
        rigfile.fold_state(raw, {"processors": {"ghost_to_stereo": {}}})


# --------------------------------------------------------------------------- #
# Units, moves and undo
# --------------------------------------------------------------------------- #

CHAINS = {"board": ["comp", "comp_to_stereo", "eq", "ts", "ts_to_stereo", "board_send"]}
IDS = {"board": 1, "comp": 2, "comp_to_stereo": 3, "eq": 4, "ts": 5, "ts_to_stereo": 6, "board_send": 7}
UIDS = {"comp_to_stereo": COPY_UID, "ts_to_stereo": COPY_UID, "board_send": "sushi.testing.send"}


def test_a_chain_is_units_of_a_plugin_and_its_copy():
    assert stereo.units(CHAINS["board"], UIDS) == [
        ["comp", "comp_to_stereo"], ["eq"], ["ts", "ts_to_stereo"], ["board_send"]]
    assert stereo.units(["eq", "comp_to_stereo"]) == [["eq"], ["comp_to_stereo"]], \
        "a copy not after its plugin is not its unit"


@pytest.mark.parametrize("processor, direction, commands", [
    # Up past a plain neighbour: the unit, in order, in front of it.
    ("ts", -1, [(5, 1, 4, False), (6, 1, 4, False)]),
    # Up past a unit of two: in front of its head.
    ("eq", -1, [(4, 1, 2, False)]),
    # Down past a plain neighbour: the unit in front of what follows it.
    ("comp", 1, [(2, 1, 5, False), (3, 1, 5, False)]),
])
def test_a_move_takes_the_copy_with_its_plugin(processor, direction, commands):
    move = decide_move(CHAINS, IDS, processor, direction, UIDS)
    assert move.outcome == "planned"
    assert [move.command, *move.then] == commands


def test_a_move_past_a_neighbour_with_a_copy_steps_past_both():
    move = decide_move(CHAINS, IDS, "eq", 1, UIDS)
    assert [move.command, *move.then] == [(4, 1, 7, False)]
    assert chain_after_move(CHAINS["board"], "eq", "down") == [
        "comp", "comp_to_stereo", "ts", "ts_to_stereo", "eq", "board_send"]


@pytest.mark.parametrize("processor, direction, uids", [
    ("comp_to_stereo", 1, UIDS), ("comp_to_stereo", -1, {}), ("ts", 1, UIDS),
])
def test_a_copy_never_moves_alone_and_a_send_never_moves(processor, direction, uids):
    assert decide_move(CHAINS, IDS, processor, direction, uids).outcome == "protected"


def test_undo_of_a_move_knows_the_units():
    previous = CHAINS["board"]
    moved = chain_after_move(previous, "ts", "up")
    assert moved == ["comp", "comp_to_stereo", "ts", "ts_to_stereo", "eq", "board_send"]
    entry = {"op": "move", "args": {"processor": "ts", "direction": "up"}, "previous": previous}
    check_undo_move(entry, moved)
    with pytest.raises(StaleUndo):
        check_undo_move(entry, ["comp", "comp_to_stereo", "ts", "eq", "ts_to_stereo", "board_send"])
    assert chain_after_move(moved, "ts", "down") == previous


def test_the_copy_is_protected_by_uid_or_by_name():
    assert "copy to stereo" in protected_processor("board", "renamed", COPY_UID)
    assert "copy to stereo" in protected_processor("board", "comp_to_stereo")
    assert protected_processor("board", "comp") is None


# --------------------------------------------------------------------------- #
# Add, remove and bypass, live
# --------------------------------------------------------------------------- #

def library():
    manifest = read_manifest((ROOT / "plugin-manifest.txt").read_text())
    return build_library(manifest, {}, {}, audio=stereo.load_ports(ROOT))


def reading():
    return {"chains": copy.deepcopy(CHAINS), "ids": dict(IDS), "uids": dict(UIDS)}


def test_the_library_says_each_plugins_audio_ports_from_the_table():
    entries = {e["uri"]: e for e in library()["plugins"]}
    assert entries[COMP]["audio"] == {"in": 1, "out": 1}
    assert entries[EQ]["audio"] == {"in": 2, "out": 2}
    assert entries["http://github.com/mikeoliphant/neural-amp-modeler-lv2"]["audio"] is None


def test_a_mono_plugin_added_live_brings_its_copy_in_the_same_operation():
    asked = add_request({"track": "board", "uri": TS9, "name": "drive"}, library())
    assert asked.copy == "drive_to_stereo"
    addition = check_add(reading(), asked)
    assert addition.copy == "drive_to_stereo"
    assert addition.expected == ["comp", "comp_to_stereo", "eq", "ts", "ts_to_stereo",
                                 "drive", "drive_to_stereo", "board_send"]
    assert add_request({"track": "board", "uri": EQ, "name": "eq2"}, library()).copy is None


def test_an_add_after_a_mono_plugin_goes_after_its_copy():
    addition = check_add(reading(), AddRequest("board", EQ, "eq2", "comp", True))
    assert addition.expected[:3] == ["comp", "comp_to_stereo", "eq2"]
    assert addition.before_id == IDS["eq"]


def test_an_add_whose_copy_name_is_taken_is_a_conflict():
    taken = reading()
    taken["chains"]["board"].insert(2, "drive_to_stereo")
    with pytest.raises(Conflict, match="drive_to_stereo"):
        check_add(taken, AddRequest("board", TS9, "drive", None, True, "drive_to_stereo"))


@pytest.mark.parametrize("body, problem", [
    ({"track": "board", "uri": EQ, "name": "eq_to_stereo"}, "_to_stereo"),
    ({"track": "board", "uri": "urn:x"}, "not in plugin-manifest.txt"),
])
def test_an_add_the_compiler_could_not_make_is_refused(body, problem):
    with pytest.raises(AgentError, match=problem):
        add_request(body, library())


def test_an_add_of_a_plugin_the_table_does_not_list_is_refused():
    manifest = read_manifest((ROOT / "plugin-manifest.txt").read_text())
    without = build_library(manifest, {}, {}, audio={})
    with pytest.raises(AgentError, match="plugin-ports.txt"):
        add_request({"track": "board", "uri": EQ, "name": "eq2"}, without)


def test_a_remove_takes_the_copy_and_a_copy_is_not_removed_alone():
    found = {**reading(), "plugin": {"uri": TS9, "uid": None, "type": "lv2"}}
    removal = check_remove(found, "ts")
    assert (removal.copy, removal.copy_id) == ("ts_to_stereo", 6)
    assert removal.expected == ["comp", "comp_to_stereo", "eq", "board_send"]
    with pytest.raises(Conflict, match="copy to stereo"):
        check_remove({**reading(), "plugin": {"uri": None, "uid": COPY_UID, "type": "internal"}},
                     "ts_to_stereo")
    plain = check_remove({**reading(), "plugin": {"uri": EQ, "uid": None, "type": "lv2"}}, "eq")
    assert plain.copy is None and plain.expected == ["comp", "comp_to_stereo", "ts", "ts_to_stereo", "board_send"]


def test_a_copy_is_not_bypassed():
    with pytest.raises(AgentError, match="bypass 'comp' instead"):
        check_bypass({"chains": copy.deepcopy(CHAINS), "bypassed": False}, "comp_to_stereo")


# --------------------------------------------------------------------------- #
# The live switch
# --------------------------------------------------------------------------- #

def _compiled(raw, ports=None):
    rig_ = rigfile.parse_rig(raw)
    return rigfile.emit_compiled(rig_, *rigfile.compile_rig(rig_, ports=ports))


TWO_MONO = rig([{"name": "comp", "uri": COMP}, {"name": "ts", "uri": TS9}])
TABLE = {COMP: Ports(1, 1), TS9: Ports(1, 1)}


def test_a_switch_between_rigs_whose_copies_disagree_is_a_named_restart():
    """Only a changed table does this: the same plugin, a copy in one rig only."""
    running = _compiled(TWO_MONO, {**TABLE, COMP: Ports(1, 2)})
    decided = rigfile.diff(running, _compiled(TWO_MONO, TABLE))
    assert isinstance(decided, rigfile.NeedsRestart) and decided.codes == ["stereo"]
    assert "'comp'" in decided.reasons[0].detail


def test_a_running_rig_compiled_before_the_copies_is_its_own_reason():
    """Not "the table changed": a rig started before the copies existed has
    none at all, and a restart compiles them in (review)."""
    running = _compiled(TWO_MONO, {COMP: Ports(1, 2), TS9: Ports(1, 2)})
    decided = rigfile.diff(running, _compiled(TWO_MONO, TABLE))
    assert isinstance(decided, rigfile.NeedsRestart) and decided.codes == ["stereo_missing"]
    assert "left channel only" in decided.reasons[0].detail


def test_a_replaced_plugin_is_not_a_copy_disagreement():
    """The check runs on the survivors once replacements are dropped: a
    plugin replaced is removed with its copy and added with its own."""
    raw = rig([{"name": "comp", "uri": COMP, "parameters": {"RATIO": 0.3}}, {"name": "ts", "uri": TS9}])
    running = _compiled(raw, {**TABLE, COMP: Ports(1, 2)})
    plan = rigfile.diff(running, _compiled(TWO_MONO, TABLE))
    assert isinstance(plan, rigfile.SwitchPlan), plan
    adds = [s.args for s in plan.steps if s.op == "add"]
    assert adds and adds[0]["name"] == "comp" and adds[0]["copy"] == "comp_to_stereo"


def test_a_plan_step_that_cannot_be_applied_is_named_not_stored_as_none():
    plan = rigfile.SwitchPlan((rigfile.SwitchStep("move", {"processor": "eq", "direction": "down"}),))
    with pytest.raises(ValueError, match="'eq'.*cannot be applied|cannot be applied.*'eq'"):
        rigfile.apply_to_chains({"board": ["comp", "comp_to_stereo", "eq"]}, plan)


def test_a_planned_remove_takes_the_copy_only_as_the_real_remove_does():
    remove = rigfile.SwitchPlan((rigfile.SwitchStep("remove", {"processor": "comp"}),))
    assert rigfile.apply_to_chains({"board": ["comp", "comp_to_stereo", "eq"]}, remove) == {"board": ["eq"]}
    apart = {"board": ["comp", "eq", "comp_to_stereo"]}
    assert rigfile.apply_to_chains(apart, remove) == {"board": ["eq", "comp_to_stereo"]}
    assert check_remove({"chains": apart, "ids": {"board": 1, "comp": 2, "eq": 3, "comp_to_stereo": 4},
                         "plugin": {"uri": COMP, "uid": None, "type": "lv2"}}, "comp").expected == \
        ["eq", "comp_to_stereo"]


def test_a_digit_int_cannot_read_is_a_ports_error_with_its_line():
    with pytest.raises(stereo.PortsError, match="line 2"):
        stereo.read_ports("1 1 urn:a\n\u00b2 1 urn:b\n")


def test_a_broken_table_is_a_rig_file_error_and_a_library_problem(tmp_path, monkeypatch):
    from sushi_rig import paths
    from sushi_rig.library import read_library

    (tmp_path / "plugin-ports.txt").write_text("\u0663 1 " + EQ + "\n")
    (tmp_path / "plugin-manifest.txt").write_text(EQ + "\n")
    monkeypatch.setattr(paths, "checkout_root", lambda: tmp_path)
    with pytest.raises(rigfile.RigFileError, match="plugin-ports.txt line 1"):
        rigfile.compile_rig(rigfile.parse_rig(rig([{"name": "eq", "uri": EQ}])))
    built = read_library(tmp_path)
    assert any("line 1" in p for p in built["problems"]) and built["plugins"][0]["audio"] is None


def test_a_routing_only_compile_needs_no_table_and_makes_no_copies(tmp_path, monkeypatch):
    from sushi_rig import paths

    monkeypatch.setattr(paths, "checkout_root", lambda: tmp_path)  # no plugin-ports.txt here
    raw = rig([{"name": "comp", "uri": COMP}, {"name": "x", "uri": "urn:not-in-any-table"}])
    spec, _ = rigfile.compile_rig(rigfile.parse_rig(raw), routing_only=True)
    assert [p.name for p in spec.tracks[0].plugins] == ["comp", "x", "board_send"]
    with pytest.raises(rigfile.RigFileError, match="no plugin-ports.txt"):
        rigfile.compile_rig(rigfile.parse_rig(raw))
    path = tmp_path / "r.yaml"
    path.write_text(yaml.safe_dump(raw))
    assert [p.name for p in rigfile.spec_of(path, routing_only=True).tracks[0].plugins][:2] == ["comp", "x"]


def test_saves_source_check_reads_a_running_config_with_copies_against_a_routing_only_spec(tmp_path):
    """SAVE's check that a rig file describes the running config: the config
    has the copies, the routing-only spec does not, and that is no mismatch."""
    import json

    from sushi_rig.rig import rig_source_problem

    (tmp_path / "config" / "rigs").mkdir(parents=True)
    raw = rig([{"name": "comp", "uri": COMP}])
    (tmp_path / "config" / "rigs" / "r.yaml").write_text(yaml.safe_dump(raw))
    running = tmp_path / "r.json"
    running.write_text(json.dumps(rigfile.emit_rig(rigfile.parse_rig(raw))))
    assert "comp_to_stereo" in running.read_text()
    assert rig_source_problem(running, tmp_path) is None


def test_the_refusals_for_a_copy_read_as_one_sentence():
    move = decide_move(CHAINS, IDS, "comp_to_stereo", 1, UIDS)
    assert move.why == "'comp_to_stereo' is 'comp''s copy to stereo, which moves with 'comp': move that instead"
    with pytest.raises(Conflict) as caught:
        check_remove({**reading(), "plugin": {"uri": None, "uid": COPY_UID, "type": "internal"}}, "ts_to_stereo")
    assert str(caught.value) == "'ts_to_stereo' is 'ts''s copy to stereo, removed with 'ts': remove that instead"


# --------------------------------------------------------------------------- #
# A pair that a failing Sushi would leave apart (review, 2026-11-02)
# --------------------------------------------------------------------------- #

class Graph:
    """Sushi's audio graph as the live calls use it, over one track's chain
    by id, failing the commands numbered in `fail` (1 = the first command)."""

    def __init__(self, chain, fail=()):
        self.ids = {"board": 1, **{name: i + 2 for i, name in enumerate(chain)}}
        self.chain = list(chain)
        self.fail, self.count = set(fail), 0

    def _name(self, processor_id):
        return next(n for n, i in self.ids.items() if i == processor_id)

    def _answer(self):
        from types import SimpleNamespace

        self.count += 1
        return SimpleNamespace(is_set=lambda: True, error=self.count in self.fail)

    def _place(self, name, before, add_to_back):
        if add_to_back:
            self.chain.append(name)
        else:
            self.chain.insert(self.chain.index(self._name(before)), name)

    def move_processor_on_track(self, processor, source_track, dest_track, before, add_to_back):
        answer = self._answer()
        if not answer.error:
            name = self._name(processor)
            self.chain.remove(name)
            self._place(name, before, add_to_back)
        return answer

    def create_processor_on_track(self, name, uid, path, processor_type, track_id, before_processor, add_to_back):
        answer = self._answer()
        if not answer.error:
            self.ids[name] = max(self.ids.values()) + 1
            self._place(name, before_processor, add_to_back)
        return answer

    def delete_processor_from_track(self, processor, track):
        answer = self._answer()
        if not answer.error:
            self.chain.remove(self._name(processor))
        return answer

    def get_processor_id(self, name):
        return self.ids[name]


def controller_of(graph):
    from types import SimpleNamespace

    return SimpleNamespace(audio_graph=graph)


PAIR = ["comp", "comp_to_stereo", "eq", "board_send"]


def _move(graph, processor, direction):
    from sushi_rig.live import send_live_move

    move = decide_move({"board": list(graph.chain)}, dict(graph.ids), processor, direction, {})
    return send_live_move(move, controller=controller_of(graph))


def test_a_move_whose_copy_fails_puts_the_plugin_back():
    from sushi_rig.live import LiveError

    graph = Graph(PAIR, fail={2})
    with pytest.raises(LiveError, match="put back in front of it: nothing moved"):
        _move(graph, "comp", 1)
    assert graph.chain == PAIR


def test_a_move_whose_copy_and_undo_both_fail_says_what_is_apart_and_the_stray_can_be_repaired():
    from sushi_rig.live import LiveError

    graph = Graph(PAIR, fail={2, 3})
    with pytest.raises(LiveError, match="the two are apart") as caught:
        _move(graph, "comp", 1)
    assert "move or remove 'comp_to_stereo'" in str(caught.value)
    assert graph.chain == ["comp_to_stereo", "eq", "comp", "board_send"]
    # The stray copy heads its own unit: it may be moved and removed now.
    assert decide_move({"board": graph.chain}, graph.ids, "comp_to_stereo", 1, UIDS).outcome == "planned"
    removal = check_remove({"chains": {"board": graph.chain}, "ids": graph.ids, "uids": UIDS,
                            "plugin": {"uri": None, "uid": COPY_UID, "type": "internal"}}, "comp_to_stereo")
    assert removal.expected == ["eq", "comp", "board_send"]


def _addition(graph):
    return check_add({"chains": {"board": list(graph.chain)}, "ids": dict(graph.ids), "uids": {}},
                     AddRequest("board", TS9, "drive", None, True, "drive_to_stereo"))


def test_an_add_whose_copy_fails_removes_the_plugin_again():
    pytest.importorskip("elkpy")
    from sushi_rig.agent import _add_with_copy
    from sushi_rig.live import LiveError

    graph = Graph(PAIR, fail={2})
    with pytest.raises(LiveError, match="was removed again: nothing was added"):
        _add_with_copy("unused", controller_of(graph), _addition(graph))
    assert graph.chain == PAIR


def test_an_add_whose_copy_and_undo_both_fail_says_the_plugin_is_alone():
    pytest.importorskip("elkpy")
    from sushi_rig.agent import _add_with_copy
    from sushi_rig.live import LiveError

    graph = Graph(PAIR, fail={2, 3})
    with pytest.raises(LiveError, match="'drive' is on 'board' without its copy"):
        _add_with_copy("unused", controller_of(graph), _addition(graph))
    assert graph.chain == ["comp", "comp_to_stereo", "eq", "drive", "board_send"]


def _removal(graph):
    return check_remove({"chains": {"board": list(graph.chain)}, "ids": dict(graph.ids), "uids": {},
                         "plugin": {"uri": COMP, "uid": None, "type": "lv2"}}, "comp")


def test_a_remove_whose_plugin_fails_puts_the_copy_back():
    pytest.importorskip("elkpy")
    from sushi_rig.agent import _remove_with_copy
    from sushi_rig.live import LiveError

    graph = Graph(PAIR, fail={2})
    with pytest.raises(LiveError, match="the copy was put back: nothing was removed"):
        _remove_with_copy("unused", controller_of(graph), _removal(graph))
    assert graph.chain == PAIR


def test_a_remove_whose_plugin_and_undo_both_fail_says_the_plugin_is_alone():
    pytest.importorskip("elkpy")
    from sushi_rig.agent import _remove_with_copy
    from sushi_rig.live import LiveError

    graph = Graph(PAIR, fail={2, 3})
    with pytest.raises(LiveError, match="'comp' is on 'board' without its copy"):
        _remove_with_copy("unused", controller_of(graph), _removal(graph))
    assert graph.chain == ["comp", "eq", "board_send"]


def test_a_pair_moved_added_and_removed_through_the_fake_graph_stays_together():
    pytest.importorskip("elkpy")
    from sushi_rig.agent import _add_with_copy, _remove_with_copy

    graph = Graph(PAIR)
    _move(graph, "comp", 1)
    assert graph.chain == ["eq", "comp", "comp_to_stereo", "board_send"]
    _add_with_copy("unused", controller_of(graph), _addition(graph))
    assert graph.chain == ["eq", "comp", "comp_to_stereo", "drive", "drive_to_stereo", "board_send"]
    _remove_with_copy("unused", controller_of(graph), _removal(graph))
    assert graph.chain == ["eq", "drive", "drive_to_stereo", "board_send"]
