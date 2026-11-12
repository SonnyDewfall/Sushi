"""The master's `level:` (2026-11-06): the web UI's master volume, written by
a save as `master: level: <dB>` and compiled to the master track's own gain,
as a channel's `level:` compiles to its send's (#101).

Pure, against the committed rigs of config/rigs and small documents. The
track's gain range, -120 to +24 dB, is rigfile.py's record of Sushi 1.3.0's
source (`LEVEL_RANGE`, `UNITY_STRIP`); no Sushi is run here.
"""

from __future__ import annotations

import copy

import pytest
import yaml
from rig_fixtures import FROZEN, committed, controller_of

from sushi_rig import rigfile
from sushi_rig.rigfile import MASTER, RigFileError, diff, emit_rig, fold_state, level_to_gain, load_rig, parse_rig

# The frozen rigs, none of which states a master level, and the rigs in
# config/rigs/, which may come to (rig_fixtures.py; the page has a master
# fader, and Save writes it).
RIGS = [("frozen", path) for path in sorted(FROZEN.glob("*.yaml"))] + [("committed", path) for path in committed()]


def doc(**master):
    return {
        "kind": "rig", "schema_version": 1, "name": "two",
        "channels": [{"name": "guitar", "input": "guitar-in", "width": "mono", "chain": []},
                     {"name": "vocal", "input": "vocal-in", "width": "mono", "chain": [], "level": -6.0}],
        "master": {"output": "monitor-out", "chain": [], **master},
    }


def master_state(config):
    return [e for e in config.get("initial_state", []) if e["processor"] == MASTER]


@pytest.mark.committed_rigs
@pytest.mark.parametrize("where, path", RIGS, ids=[f"{where}:{path.stem}" for where, path in RIGS])
def test_every_rig_compiles_as_before_with_no_master_level(where, path):
    """Unity writes nothing: a rig that states no master level gets no master
    entry, and stating `level: 0` changes nothing either. No frozen rig
    states one, which is how every rig was when the master level was added.
    (Checked once by hand too, on 2026-11-06: every committed rig's compiled
    config, byte for byte, the same as the code before this change gave.)

    A committed rig may state one since, and is then asked the same thing the
    other way about: its level is the master's one entry, and with the level
    taken out, or put at 0, it compiles to the same config without it."""
    raw = yaml.safe_load(path.read_text())
    rig = load_rig(path)
    config = emit_rig(rig, controller=controller_of(rig))
    if where == "frozen":
        assert "level" not in raw["master"] and rig.master.level == 0.0
    if rig.master.level == 0.0:
        assert master_state(config) == []
        at_unity = config
    else:
        (entry,) = master_state(config)
        assert entry["parameters"] == {"gain": round(level_to_gain(rig.master.level), 6)}
        at_unity = {**config, "initial_state": [e for e in config["initial_state"] if e["processor"] != MASTER]}
    for level in ("left out", 0):
        unity = copy.deepcopy(raw)
        unity["master"].pop("level", None)
        if level == 0:
            unity["master"]["level"] = 0
        assert emit_rig(parse_rig(unity), controller=controller_of(rig)) == at_unity, f"master level {level}"


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
