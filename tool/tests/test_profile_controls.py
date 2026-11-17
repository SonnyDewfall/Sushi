"""A profile's controls (#154): one knob that drives several parameters, and
what the validator holds them to.

The facts are real: `fixtures/probe_MaGigaverb.json` and
`fixtures/probe_a-reverb.json` are `probe` run against the installed
bundles (Ubuntu 24.04's dpf-plugins-lv2 and ardour-lv2-plugins, 2026-11-16),
and `examples/profiles/magigaverb.yaml` and `a-reverb.yaml` are written from
them. The broken cases are the hall's own profile with one thing changed.
"""

from __future__ import annotations

import copy
import json
import math
from pathlib import Path

import pytest

from sushi_rig.profile import (
    CURVES,
    curve_at,
    driven_value,
    dump_profile,
    errors,
    load_profile,
    validate,
    warnings,
)

HERE = Path(__file__).parent
PROFILES = HERE.parent / "examples" / "profiles"


def facts(name: str) -> dict:
    return json.loads((HERE / "fixtures" / f"probe_{name}.json").read_text())


@pytest.fixture
def hall() -> dict:
    return load_profile(PROFILES / "magigaverb.yaml")


def mix(profile: dict) -> dict:
    (control,) = profile["controls"]
    return control


def codes(findings) -> list[str]:
    return [f.code for f in findings]


@pytest.mark.parametrize("file, probe", [("magigaverb.yaml", "MaGigaverb"), ("a-reverb.yaml", "a-reverb")])
def test_the_reverb_profiles_are_clean_against_their_plugins(file, probe):
    assert validate(load_profile(PROFILES / file), facts(probe)) == []


@pytest.mark.parametrize("file", ["magigaverb.yaml", "a-reverb.yaml"])
def test_the_reverb_profiles_are_canonical(file):
    text = (PROFILES / file).read_text()
    assert dump_profile(load_profile(PROFILES / file)) == text


def test_the_room_shows_blend_as_mix_and_hides_its_own_enable():
    room = {p["name"]: p for p in load_profile(PROFILES / "a-reverb.yaml")["parameters"]}
    assert room["Blend"]["label"]["value"] == "Mix" and room["Blend"]["visibility"]["value"] == "primary"
    assert room["Room Size"]["visibility"]["value"] == "primary"
    assert room["Enable"]["visibility"]["value"] == "hidden"


def test_the_curves_end_where_they_say_and_meet_at_equal_power():
    for curve in CURVES:
        assert curve_at(curve, 0) == 0 and curve_at(curve, 1) == pytest.approx(1)
        assert curve_at(curve, -1) == 0 and curve_at(curve, 2) == pytest.approx(1), "clamped"
    for m in (0.1, 0.5, 0.9):
        fade_out = driven_value({"curve": "cosine", "at_0": 1.0, "at_1": 0.0}, m)
        fade_in = driven_value({"curve": "sine", "at_0": 0.0, "at_1": 1.0}, m)
        assert fade_out ** 2 + fade_in ** 2 == pytest.approx(1.0)
    assert curve_at("linear", 0.3) == pytest.approx(0.3)
    with pytest.raises(ValueError):
        curve_at("exponential", 0.5)


def test_the_hall_mix_is_dry_at_0_and_wet_at_100(hall):
    drives = {d["parameter"]: d for d in mix(hall)["drives"]["value"]}
    assert {p: driven_value(d, 0) for p, d in drives.items()} == {"dry": 1.0, "early": 0.0, "tail": 0.0}
    assert {p: driven_value(d, 1) for p, d in drives.items()} == pytest.approx({"dry": 0.0, "early": 0.8, "tail": 0.4})


# --- what the validator refuses --------------------------------------------

def drive(profile: dict, parameter: str) -> dict:
    return next(d for d in mix(profile)["drives"]["value"] if d["parameter"] == parameter)


@pytest.mark.parametrize("change, code", [
    (lambda p: drive(p, "early").update(parameter="Early"), "invented-parameter"),
    (lambda p: drive(p, "early").update(at_1=1.5), "bad-value"),
    (lambda p: drive(p, "early").update(at_0="none"), "bad-value"),
    (lambda p: drive(p, "early").update(curve="exponential"), "bad-value"),
    (lambda p: drive(p, "early").update(gain=2), "unknown-key"),
    (lambda p: mix(p)["drives"]["value"].append(dict(drive(p, "dry"))), "duplicate-parameter"),
    (lambda p: mix(p)["drives"].update(provenance="fact"), "fact-not-derivable"),
    (lambda p: mix(p)["drives"].update(value=[]), "bad-value"),
    (lambda p: mix(p).pop("drives"), "missing-judgement"),
    (lambda p: mix(p).pop("visibility"), "missing-judgement"),
    (lambda p: mix(p)["label"].update(provenance="fact"), "fact-not-derivable"),
    (lambda p: mix(p)["control"].update(value="toggle"), "bad-value"),
    (lambda p: mix(p)["group"].update(value="nowhere"), "undefined-group"),
    (lambda p: mix(p).update(id="dry"), "control-is-a-parameter"),
    (lambda p: mix(p).update(colour="red"), "unknown-key"),
    (lambda p: p["controls"].append(copy.deepcopy(mix(p))), "duplicate-control"),
])
def test_a_control_that_misstates_its_plugin_is_an_error(hall, change, code):
    change(hall)
    assert code in codes(errors(validate(hall, facts("MaGigaverb"))))


def test_a_toggle_cannot_be_driven():
    room = load_profile(PROFILES / "a-reverb.yaml")
    room["controls"] = [{"id": "both", "visibility": {"value": "primary", "provenance": "draft"},
                         "drives": {"value": [{"parameter": "Blend", "curve": "linear", "at_0": 0.0, "at_1": 1.0},
                                              {"parameter": "Enable", "curve": "linear", "at_0": 0.0, "at_1": 1.0}],
                                    "provenance": "draft"}}]
    found = errors(validate(room, facts("a-reverb")))
    assert codes(found) == ["control-not-allowed"] and "Enable" in found[0].path


def test_a_driven_parameter_left_showing_is_warned_of(hall):
    dry = next(p for p in hall["parameters"] if p["name"] == "dry")
    dry["visibility"]["value"] = "advanced"
    found = validate(hall, facts("MaGigaverb"))
    assert errors(found) == []
    assert codes(warnings(found)) == ["driven-and-shown"]


def test_a_parameter_two_controls_drive_is_warned_of(hall):
    second = copy.deepcopy(mix(hall))
    second["id"] = "wet"
    second["order"]["value"] = 2
    second["drives"]["value"] = [{"parameter": "tail", "curve": "linear", "at_0": 0.0, "at_1": 1.0}]
    hall["controls"].append(second)
    found = validate(hall, facts("MaGigaverb"))
    assert errors(found) == [] and codes(warnings(found)) == ["driven-twice"]


def test_a_plugin_sushi_cannot_drive_takes_no_controls(hall):
    undrivable = facts("MaGigaverb")
    undrivable["config_drivable"] = False
    hall["ports"]["config_drivable"] = False
    assert "not-drivable" in [f.code for f in errors(validate(hall, undrivable)) if f.path == "controls"]


def test_no_controls_is_still_a_profile(hall):
    del hall["controls"]
    for p in hall["parameters"]:
        if p["name"] in ("dry", "early", "tail"):
            p["visibility"]["value"] = "advanced"
    assert validate(hall, facts("MaGigaverb")) == []
    assert "controls" not in dump_profile(hall)


def test_equal_power_is_what_the_profile_gives_the_hall(hall):
    drives = {d["parameter"]: d for d in mix(hall)["drives"]["value"]}
    for m in [i / 20 for i in range(21)]:
        dry = driven_value(drives["dry"], m)
        wet = driven_value(drives["early"], m) / 0.8
        assert math.isclose(dry ** 2 + wet ** 2, 1.0, rel_tol=1e-12)
        assert math.isclose(driven_value(drives["early"], m), 2 * driven_value(drives["tail"], m), abs_tol=1e-15)


# --- review of PR #159 ------------------------------------------------------

def test_a_drive_that_never_moves_is_refused_and_so_is_a_control_of_only_those(hall):
    drive(hall, "tail").update(at_0=0.25, at_1=0.25)
    found = errors(validate(hall, facts("MaGigaverb")))
    assert codes(found) == ["constant-drive"] and "(tail)" in found[0].path
    for name, v in (("dry", 1.0), ("early", 0.25)):
        drive(hall, name).update(at_0=v, at_1=v)
    assert sorted(codes(errors(validate(hall, facts("MaGigaverb"))))) == ["constant-control"] + ["constant-drive"] * 3


def test_a_control_sharing_a_parameters_place_in_its_group_is_warned_of(hall):
    roomsize = next(p for p in hall["parameters"] if p["name"] == "roomsize")
    mix(hall)["group"]["value"] = "space"
    mix(hall)["order"]["value"] = roomsize["order"]["value"]
    found = validate(hall, facts("MaGigaverb"))
    assert errors(found) == []
    (warned,) = warnings(found)
    assert warned.code == "duplicate-order" and "'roomsize'" in warned.message


def test_the_panel_refuses_controls_that_are_not_a_list_of_mappings():
    from sushi_rig.ui import PanelError, panel_profile

    for controls, said in (({"id": "mix"}, "controls is not a list"), (["mix"], "control 1 is not a mapping")):
        with pytest.raises(PanelError, match=said):
            panel_profile({"_file": "p.yaml", "plugin": {"uri": "urn:x"}, "parameters": [], "controls": controls})


def test_the_panel_leaves_out_a_control_on_a_curve_it_does_not_know():
    from sushi_rig.ui import panel_profile

    flat, problems = panel_profile({"_file": "p.yaml", "plugin": {"uri": "urn:x"}, "parameters": [], "controls": [
        {"id": "mix", "drives": {"value": [{"parameter": "A", "curve": "exponential", "at_0": 0, "at_1": 1}],
                                 "provenance": "draft"}}]})
    assert flat["parameters"] == []
    assert problems == ["p.yaml: control mix has no drives the panel can follow and is left out"]
