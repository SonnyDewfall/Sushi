"""The master's `level:` (2026-11-06): the web UI's master volume, written by
a save as `master: level: <dB>` and compiled to the master track's own gain,
as a channel's `level:` compiles to its send's (#101).

Pure, against the committed rigs of config/rigs and small documents. The
track's gain range, -120 to +24 dB, is rigfile.py's record of Sushi 1.3.0's
source (`LEVEL_RANGE`, `UNITY_STRIP`); no Sushi is run here.
"""

from __future__ import annotations

import copy
from pathlib import Path

import pytest
import yaml

from sushi_rig import rigfile
from sushi_rig.rigfile import MASTER, RigFileError, diff, emit_rig, fold_state, level_to_gain, load_rig, parse_rig

ROOT = Path(__file__).resolve().parents[2]
COMMITTED = sorted((ROOT / "config" / "rigs").glob("*.yaml"))


def doc(**master):
    return {
        "kind": "rig", "schema_version": 1, "name": "two",
        "channels": [{"name": "guitar", "input": "guitar-in", "width": "mono", "chain": []},
                     {"name": "vocal", "input": "vocal-in", "width": "mono", "chain": [], "level": -6.0}],
        "master": {"output": "monitor-out", "chain": [], **master},
    }


def master_state(config):
    return [e for e in config.get("initial_state", []) if e["processor"] == MASTER]


@pytest.mark.parametrize("path", COMMITTED, ids=lambda p: p.stem)
def test_every_committed_rig_compiles_as_before_with_no_master_level(path):
    """Unity writes nothing: no committed rig states a master level, none
    gets a master entry, and stating `level: 0` changes nothing either.
    (Checked once by hand too, on 2026-11-06: every committed rig's compiled
    config, byte for byte, the same as the code before this change gave.)"""
    raw = yaml.safe_load(path.read_text())
    assert "level" not in raw["master"]
    rig = load_rig(path)
    assert rig.master.level == 0.0
    config = emit_rig(rig, controller=_controller(rig))
    assert master_state(config) == []
    unity = copy.deepcopy(raw)
    unity["master"]["level"] = 0
    assert emit_rig(parse_rig(unity), controller=_controller(rig)) == config


# The machine the committed rigs are played on. Named, not found by a glob of
# machines/: `loopback-test.yaml` sorts before it and carries a copy of this
# file's controllers, so a glob would compile a rig's MIDI map against a file
# that is no machine at all.
MACHINE = ROOT / "machines" / "sonny-dewfall-XPS-9320.yaml"


def _controller(rig):
    """The controller a rig's MIDI map names, from the real machine's file;
    None for a rig with no map (all of them, today)."""
    if rig.midi is None:
        return None
    from sushi_rig.machine import MachineSpec

    spec = MachineSpec.load(MACHINE)
    if rig.midi.controller not in (spec.controllers or {}):
        raise AssertionError(f"{MACHINE.name} does not describe {rig.midi.controller}")
    return spec.controller(rig.midi.controller)


def test_a_master_level_compiles_to_the_master_tracks_gain_as_a_channels_to_its_send():
    config = emit_rig(parse_rig(doc(level=-3.5)))
    (entry,) = master_state(config)
    assert entry["parameters"] == {"gain": round(level_to_gain(-3.5), 6)}
    send = next(e for e in config["initial_state"] if e["processor"] == "vocal_send")
    assert send["parameters"]["gain"] == round(level_to_gain(-6.0), 6), "the same conversion"


@pytest.mark.parametrize("value, said", [
    ("loud", "level must be a number in dB"), (True, "level must be a number in dB"),
    (30, "outside the master track's gain's -120 to 24"), (-121, "outside"),
])
def test_a_master_level_is_checked_as_a_channels_is(value, said):
    with pytest.raises(RigFileError, match=said):
        parse_rig(doc(level=value))


def test_a_master_level_is_kept_to_a_thousandth():
    assert parse_rig(doc(level=-3.12345)).master.level == -3.123


def test_a_save_folds_the_master_tracks_gain_into_the_masters_level():
    raw = doc()
    captured = {"processors": {MASTER: {"parameters": {"gain": level_to_gain(-4.0), "pan": 0.5, "mute": 0.0}}},
                "tracks": {}}
    out = fold_state(raw, captured)
    assert out["master"]["level"] == -4.0
    assert "pan" not in out["master"] and "mute" not in out["master"]
    assert "level" not in fold_state(raw, {"processors": {MASTER: {"parameters": {"gain": level_to_gain(0)}}},
                                           "tracks": {}})["master"], "unity on a file with none: nothing written"
    back = fold_state(doc(level=-4.0), {"processors": {MASTER: {"parameters": {"gain": level_to_gain(0)}}},
                                        "tracks": {}})
    assert back["master"]["level"] == 0.0, "moved back to unity from a file that had a level"


def test_a_master_level_round_trips_through_the_file_keeping_its_comments(tmp_path):
    path = tmp_path / "two.yaml"
    rigfile.write_rig(path, doc())
    text = path.read_text().replace("master:\n", "master:  # where the mix goes\n")
    path.write_text(text)
    rigfile.write_rig(path, fold_state(yaml.safe_load(text), {
        "processors": {MASTER: {"parameters": {"gain": level_to_gain(-2.5)}}}, "tracks": {}}))
    written = path.read_text()
    assert "# where the mix goes" in written and "level: -2.5" in written
    assert load_rig(path).master.level == -2.5


def test_a_switch_between_master_levels_is_a_live_step_not_a_restart():
    quiet, loud = emit_rig(parse_rig(doc(level=-10.0))), emit_rig(parse_rig(doc()))
    for config in (quiet, loud):
        config[rigfile.WIRING] = {"guitar": {}, "vocal": {}, MASTER: {}}
    plan = diff(quiet, loud)
    assert isinstance(plan, rigfile.SwitchPlan), plan
    assert [s.as_dict() for s in plan.steps] == [
        {"op": "parameter", "processor": MASTER, "parameter": "gain", "value": rigfile.UNITY_STRIP["gain"]}]
    back = diff(loud, quiet)
    assert [s.as_dict() for s in back.steps] == [
        {"op": "parameter", "processor": MASTER, "parameter": "gain", "value": round(level_to_gain(-10.0), 6)}]
