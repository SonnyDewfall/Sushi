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
    for path in sorted((ROOT / "config" / "rigs").glob("*.yaml")):
        config = rigfile.emit_rig(rigfile.load_rig(path))
        for track in config["tracks"]:
            names = [p["name"] for p in track["plugins"]]
            for plugin in track["plugins"]:
                if plugin.get("uri") in (COMP, TS9):
                    assert names[names.index(plugin["name"]) + 1] == plugin["name"] + "_to_stereo", path.name


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

def test_a_switch_between_rigs_whose_copies_disagree_is_a_named_restart():
    """Only a changed table does this: the same plugin, a copy in one rig only."""
    raw = rig([{"name": "comp", "uri": COMP}])
    rig_ = rigfile.parse_rig(raw)
    with_copy = rigfile.emit_compiled(rig_, *rigfile.compile_rig(rig_))
    without = rigfile.emit_compiled(rig_, *rigfile.compile_rig(rig_, ports={COMP: Ports(1, 2)}))
    decided = rigfile.diff(with_copy, without)
    assert isinstance(decided, rigfile.NeedsRestart) and decided.codes == ["stereo"]
