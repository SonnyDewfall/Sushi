"""The profile validator: what stops a proposed profile inventing anything.

The facts here are real: `fixtures/probe_gxts9.json` is `probe` run against the
installed Guitarix Tube Screamer, and `examples/profiles/gxts9.yaml` is written
from it. Cases the real plugin cannot supply (an enumeration, a unit, a version)
use small synthetic facts, built by editing the real ones so they stay
probe-shaped.
"""

import copy
import json
from pathlib import Path

import pytest
import yaml

from sushi_rig.profile import (
    Finding,
    ProfileError,
    derive_scale,
    dump_profile,
    errors,
    load_profile,
    load_profile_text,
    validate,
    warnings,
)

HERE = Path(__file__).parent
EXAMPLE = HERE.parent / "examples" / "profiles" / "gxts9.yaml"


@pytest.fixture
def facts() -> dict:
    return json.loads((HERE / "fixtures" / "probe_gxts9.json").read_text())


@pytest.fixture
def profile() -> dict:
    return load_profile(EXAMPLE)


def codes(findings: list[Finding]) -> list[str]:
    return [f.code for f in findings]


def param(profile: dict, name: str) -> dict:
    return next(p for p in profile["parameters"] if p["name"] == name)


def with_choices(facts: dict, name: str = "Tone") -> dict:
    """Facts where one port is an enumeration — nothing real here has one."""
    facts = copy.deepcopy(facts)
    port = next(p for p in facts["parameters"] if p["name"] == name)
    port.update(
        enumeration=True,
        integer=True,
        scale_points=[{"value": 0, "label": "Low"}, {"value": 1, "label": "High"}],
    )
    return facts


# --- the valid case ----------------------------------------------------------


def test_the_real_example_has_no_errors(profile, facts):
    assert errors(validate(profile, facts)) == []


def test_the_only_warning_on_the_real_example_is_the_unverifiable_version(profile, facts):
    """probe does not report a version yet, so the pin cannot be checked. That
    must stay visible rather than pass silently."""
    assert codes(warnings(validate(profile, facts))) == ["unverified-version"]


def test_a_matching_version_from_probe_is_clean(profile, facts):
    facts["version"] = "43.0"
    assert validate(profile, facts) == []


def test_a_mismatched_version_is_an_error(profile, facts):
    facts["version"] = "44.0"
    assert "fact-mismatch" in codes(errors(validate(profile, facts)))


# --- facts: nothing invented -------------------------------------------------


def test_unknown_symbol_is_invented(profile, facts):
    param(profile, "Drive")["symbol"] = "fslider9_"
    found = errors(validate(profile, facts))
    assert codes(found) == ["invented-parameter"]
    assert "fslider9_" in found[0].message


def test_unknown_sushi_name_is_invented(profile, facts):
    """The symbol is real but the name is not what Sushi calls the port."""
    param(profile, "Drive")["name"] = "Gain"
    found = errors(validate(profile, facts))
    assert codes(found) == ["invented-parameter"]
    assert "'Drive'" in found[0].message  # says what Sushi really calls it


def test_symbol_and_name_of_different_ports_is_a_mismatch(profile, facts):
    param(profile, "Drive")["name"] = "Tone"
    # Tone is also declared, so this is a duplicate name as well; the point is
    # that the identity error is raised and not swallowed by it.
    assert "identity-mismatch" in codes(errors(validate(profile, facts)))


def test_output_ports_are_not_parameters(profile, facts):
    """probe lists input control ports only; a meter or audio port named in a
    profile is therefore unknown."""
    profile["parameters"].append(
        {"name": "Out", "symbol": "out", "visibility": {"value": "hidden", "provenance": "draft"}}
    )
    assert codes(errors(validate(profile, facts))) == ["invented-parameter"]


@pytest.mark.parametrize(
    "key, wrong",
    [("min", -1.0), ("max", 2.0), ("default", 0.9), ("toggled", True),
     ("integer", True), ("enumeration", True), ("logarithmic", True),
     ("settable", False)],
)
def test_restated_fact_that_disagrees_with_probe_is_an_error(profile, facts, key, wrong):
    param(profile, "Drive")["facts"][key] = wrong
    found = errors(validate(profile, facts))
    assert "fact-mismatch" in codes(found)
    assert f"facts.{key}" in found[0].path


def test_restated_range_is_compared_not_just_present(profile, facts):
    """A restatement that is right passes — the check is equality, not
    presence."""
    assert param(profile, "Tone")["facts"]["min"] == 100.0
    assert errors(validate(profile, facts)) == []


def test_restated_enum_values_must_match(profile, facts):
    facts = with_choices(facts)
    tone = param(profile, "Tone")
    tone["facts"]["enumeration"] = True
    tone["facts"]["integer"] = True
    tone["facts"]["scale_points"] = [{"value": 0, "label": "Low"}, {"value": 1, "label": "High"}]
    tone["facts"].update(min=100.0, max=1000.0, default=400.0)
    tone["scale"] = {"value": "enum", "provenance": "draft"}
    tone["control"] = {"value": "selector", "provenance": "draft"}
    assert errors(validate(profile, facts)) == []

    tone["facts"]["scale_points"][1]["label"] = "Higher"
    assert codes(errors(validate(profile, facts))) == ["fact-mismatch"]


def test_restated_fact_probe_did_not_report_is_unverifiable(profile, facts):
    """Trusting a unit probe never gave us would be the invention door."""
    param(profile, "Level")["facts"]["unit"] = "dB"
    assert codes(errors(validate(profile, facts))) == ["unverifiable-fact"]


def test_restated_unit_is_checked_when_probe_reports_it(profile, facts):
    next(p for p in facts["parameters"] if p["name"] == "Level")["unit"] = "dB"
    param(profile, "Level")["facts"]["unit"] = "dB"
    assert errors(validate(profile, facts)) == []
    param(profile, "Level")["facts"]["unit"] = "Hz"
    assert codes(errors(validate(profile, facts))) == ["fact-mismatch"]


def test_restated_plugin_facts_are_checked(profile, facts):
    profile["plugin"]["name"] = "Something Else"
    profile["ports"]["audio_out"] = 2
    assert codes(errors(validate(profile, facts))) == ["fact-mismatch", "fact-mismatch"]


def test_profile_for_a_different_plugin_is_rejected(profile, facts):
    profile["plugin"]["uri"] = "http://example.org/other"
    assert "plugin-mismatch" in codes(errors(validate(profile, facts)))


def test_version_must_be_a_string(profile, facts):
    """`version: 43.0` unquoted is a YAML float, and 1.10 would become 1.1."""
    profile["plugin"]["version"] = 43.0
    assert "bad-type" in codes(errors(validate(profile, facts)))


# --- judgements checked against facts ---------------------------------------


def test_toggle_control_on_a_port_that_is_not_toggled(profile, facts):
    drive = param(profile, "Drive")
    drive["control"] = {"value": "toggle", "provenance": "draft"}
    drive["scale"] = {"value": "toggle", "provenance": "draft"}
    found = errors(validate(profile, facts))
    assert set(codes(found)) == {"control-not-allowed"}


def test_selector_on_a_port_with_nothing_to_select(profile, facts):
    drive = param(profile, "Drive")
    drive["control"] = {"value": "selector", "provenance": "draft"}
    drive["scale"] = {"value": "enum", "provenance": "draft"}
    assert set(codes(errors(validate(profile, facts)))) == {"control-not-allowed"}


def test_selector_is_allowed_where_the_port_is_an_enumeration(profile, facts):
    facts = with_choices(facts, "Drive")
    drive = param(profile, "Drive")
    drive["facts"].update(enumeration=True, integer=True)
    drive["control"] = {"value": "selector", "provenance": "draft"}
    drive["scale"] = {"value": "enum", "provenance": "draft"}
    facts_drive = next(p for p in facts["parameters"] if p["name"] == "Drive")
    drive["facts"].update(min=facts_drive["min"], max=facts_drive["max"])
    assert errors(validate(profile, facts)) == []


def test_log_scale_across_zero_is_rejected(profile, facts):
    param(profile, "Level")["scale"] = {"value": "log", "provenance": "draft"}
    found = errors(validate(profile, facts))
    assert codes(found) == ["control-not-allowed"]
    assert "-20.0" in found[0].message


def test_log_scale_is_fine_above_zero(profile, facts):
    param(profile, "Tone")["scale"] = {"value": "log", "provenance": "draft"}
    assert errors(validate(profile, facts)) == []


def test_db_scale_is_rejected_for_a_value_already_in_decibels(profile, facts):
    next(p for p in facts["parameters"] if p["name"] == "Drive")["unit"] = "dB"
    param(profile, "Drive")["scale"] = {"value": "db", "provenance": "draft"}
    assert "control-not-allowed" in codes(errors(validate(profile, facts)))


def test_a_knob_cannot_present_a_toggle_scale(profile, facts):
    bypass = param(profile, "BYPASS")
    bypass["control"] = {"value": "knob", "provenance": "draft"}
    assert codes(errors(validate(profile, facts))) == ["control-scale-mismatch"]


def test_fact_provenance_is_rederived_not_trusted(profile, facts):
    """Stamping a wrong value `fact` must not be a free pass."""
    param(profile, "Drive")["scale"] = {"value": "log", "provenance": "fact"}
    assert "fact-not-derivable" in codes(errors(validate(profile, facts)))


def test_fact_provenance_on_an_underivable_judgement(profile, facts):
    param(profile, "Drive")["order"] = {"value": 1, "provenance": "fact"}
    assert codes(errors(validate(profile, facts))) == ["fact-not-derivable"]


def test_derive_scale_prefers_what_the_port_declares():
    assert derive_scale({"toggled": True, "logarithmic": True}) == "toggle"
    assert derive_scale({"enumeration": True}) == "enum"
    assert derive_scale({"logarithmic": True}) == "log"
    assert derive_scale({}) == "linear"


def test_bad_judgement_values_are_errors(profile, facts):
    drive = param(profile, "Drive")
    drive["visibility"] = {"value": "loud", "provenance": "draft"}
    drive["phone"] = {"value": 0, "provenance": "draft"}
    drive["order"] = {"value": True, "provenance": "draft"}
    drive["control"] = {"value": "dial", "provenance": "draft"}
    assert codes(errors(validate(profile, facts))).count("bad-value") == 4


def test_judgement_without_provenance_is_an_error(profile, facts):
    param(profile, "Drive")["label"] = {"value": "Drive"}
    assert "bad-value" in codes(errors(validate(profile, facts)))


def test_visibility_is_required(profile, facts):
    del param(profile, "Drive")["visibility"]
    assert codes(errors(validate(profile, facts))) == ["missing-judgement"]


# --- structure ---------------------------------------------------------------


def test_group_that_is_not_defined(profile, facts):
    param(profile, "Drive")["group"] = {"value": "nowhere", "provenance": "draft"}
    assert codes(errors(validate(profile, facts))) == ["undefined-group"]


def test_duplicate_group_definition(profile, facts):
    profile["groups"].append(dict(profile["groups"][0]))
    assert codes(errors(validate(profile, facts))) == ["duplicate-group"]


def test_duplicate_parameter(profile, facts):
    profile["parameters"].append(copy.deepcopy(param(profile, "Drive")))
    assert codes(errors(validate(profile, facts))) == ["duplicate-parameter"]


@pytest.mark.parametrize("version, expected", [
    (None, "missing-schema-version"),
    (2, "unknown-schema-version"),
    ("1", "unknown-schema-version"),
    (True, "unknown-schema-version"),
])
def test_schema_version_must_be_present_and_known(profile, facts, version, expected):
    if version is None:
        del profile["schema_version"]
    else:
        profile["schema_version"] = version
    assert codes(errors(validate(profile, facts))) == [expected]


def test_typos_are_not_silently_ignored(profile, facts):
    param(profile, "Drive")["visbility"] = {"value": "primary", "provenance": "draft"}
    assert codes(errors(validate(profile, facts))) == ["unknown-key"]


def test_kind_must_be_known(profile, facts):
    profile["kind"] = "pedal"
    assert codes(errors(validate(profile, facts))) == ["bad-value"]


def test_unfamiliar_host_is_a_warning_not_an_error(profile, facts):
    profile["host"] = "carla-lv3"
    assert "unknown-host" in codes(warnings(validate(profile, facts)))
    assert errors(validate(profile, facts)) == []


def test_instrument_extras_only_on_instruments(profile, facts):
    profile["instrument"] = {"midi_in": True, "polyphony": None, "patch_banks": []}
    assert codes(errors(validate(profile, facts))) == ["bad-value"]
    profile["kind"] = "instrument"
    assert errors(validate(profile, facts)) == []


def test_controls_for_a_plugin_that_cannot_be_driven(profile, facts):
    facts["config_drivable"] = False
    facts["state_interface"] = True
    found = codes(errors(validate(profile, facts)))
    assert "not-drivable" in found
    # ...and the restated `config_drivable: true` is now a false restatement.
    assert "fact-mismatch" in found


def test_a_plugin_that_cannot_be_driven_may_have_an_empty_profile(profile, facts):
    facts["config_drivable"] = False
    profile["parameters"] = []
    profile["ports"]["config_drivable"] = False
    assert errors(validate(profile, facts)) == []


# --- face zones --------------------------------------------------------------


SHOT = {"file": "gxts9.png", "width": 400, "height": 200}


def faced(profile: dict, *zones: dict) -> dict:
    profile["face"] = {"screenshot": dict(SHOT), "zones": list(zones)}
    return profile


def zone(parameter="Drive", bbox=(10, 10, 50, 50), confirmed="sweep") -> dict:
    return {"parameter": parameter, "bbox": list(bbox), "confirmed": confirmed}


def test_swept_zones_inside_the_screenshot_are_clean(profile, facts):
    faced(profile, zone(), zone("Tone", (350, 150, 50, 50)))
    assert codes(validate(profile, facts)) == ["unverified-version"]


@pytest.mark.parametrize("bbox", [
    (380, 10, 50, 50),   # right edge past 400
    (10, 180, 50, 50),   # bottom edge past 200
    (-1, 10, 50, 50),
    (10, 10, 0, 50),
])
def test_zone_outside_its_screenshot(profile, facts, bbox):
    faced(profile, zone(bbox=bbox))
    assert codes(errors(validate(profile, facts))) == ["zone-out-of-bounds"]


def test_zone_may_touch_the_screenshot_edge(profile, facts):
    faced(profile, zone(bbox=(350, 150, 50, 50)))
    assert errors(validate(profile, facts)) == []


def test_zone_pointing_at_an_unknown_parameter(profile, facts):
    faced(profile, zone("Presence"))
    assert codes(errors(validate(profile, facts))) == ["zone-unknown-parameter"]


def test_zone_pointing_at_a_parameter_the_profile_does_not_declare(profile, facts):
    profile["parameters"] = [p for p in profile["parameters"] if p["name"] != "Tone"]
    faced(profile, zone("Tone"))
    assert "zone-undeclared-parameter" in codes(errors(validate(profile, facts)))


def test_malformed_bbox(profile, facts):
    faced(profile, zone(bbox=(1, 2, 3)))
    assert codes(errors(validate(profile, facts))) == ["bad-value"]


def test_screenshot_dimensions_are_required(profile, facts):
    faced(profile, zone())
    del profile["face"]["screenshot"]["width"]
    assert "bad-value" in codes(errors(validate(profile, facts)))


def test_draft_zone_warns_but_is_not_an_error(profile, facts):
    faced(profile, zone(confirmed="draft"))
    assert errors(validate(profile, facts)) == []
    assert "draft-zone" in codes(warnings(validate(profile, facts)))


def test_unknown_zone_confirmation(profile, facts):
    faced(profile, zone(confirmed="eyeballed"))
    assert codes(errors(validate(profile, facts))) == ["bad-value"]


# --- warnings ----------------------------------------------------------------


def test_settable_parameter_the_profile_never_mentions(profile, facts):
    profile["parameters"] = [p for p in profile["parameters"] if p["name"] != "Tone"]
    found = warnings(validate(profile, facts))
    assert [f.code for f in found if f.code != "unverified-version"] == ["unmentioned-parameter"]
    assert any("Tone" in f.message for f in found)


def test_ambiguous_order_within_a_group_warns(profile, facts):
    param(profile, "Tone")["order"] = {"value": 1, "provenance": "draft"}
    assert "duplicate-order" in codes(warnings(validate(profile, facts)))


def test_linear_scale_against_a_logarithmic_hint_warns(profile, facts):
    next(p for p in facts["parameters"] if p["name"] == "Tone")["logarithmic"] = True
    param(profile, "Tone")["facts"]["logarithmic"] = True
    param(profile, "Tone")["scale"] = {"value": "linear", "provenance": "draft"}
    assert "scale-contradicts-hint" in codes(warnings(validate(profile, facts)))


def test_long_description_warns(profile, facts):
    param(profile, "Drive")["description"] = {"value": "x" * 200, "provenance": "draft"}
    assert "long-description" in codes(warnings(validate(profile, facts)))


# --- the validator never raises on junk --------------------------------------


@pytest.mark.parametrize("junk", [
    None, [], "profile", 3,
    {"schema_version": 1, "plugin": [], "parameters": "x", "groups": 5, "face": []},
    {"schema_version": 1, "parameters": [None, 3, {"name": ["a"], "symbol": {}}]},
    {"schema_version": 1, "parameters": [], "face": {"screenshot": [], "zones": [1, {"parameter": ["x"], "bbox": "no"}]}},
    {"schema_version": 1, "parameters": [{"name": "Drive", "symbol": "fslider2_",
        "control": {"value": ["x"], "provenance": "draft"},
        "group": {"value": {}, "provenance": "draft"}, "facts": 4}]},
])
def test_junk_is_reported_not_raised(facts, junk):
    assert errors(validate(junk, facts))


# --- the file format ---------------------------------------------------------


def test_example_file_is_already_canonical(profile):
    """The committed example is exactly what the dumper would write, so
    regenerating it produces no diff."""
    assert dump_profile(profile) == EXAMPLE.read_text()


def test_dump_load_dump_is_byte_identical(profile):
    once = dump_profile(profile)
    assert dump_profile(load_profile_text(once)) == once


def test_dump_is_independent_of_input_key_order(profile):
    shuffled = {k: profile[k] for k in reversed(list(profile))}
    shuffled["parameters"] = [dict(reversed(list(p.items()))) for p in profile["parameters"]]
    assert dump_profile(shuffled) == dump_profile(profile)


def test_dump_loads_back_to_the_same_data(profile):
    assert load_profile_text(dump_profile(profile)) == profile


def test_canonical_key_order(profile):
    text = dump_profile(profile)
    top = [line.split(":")[0] for line in text.splitlines() if line[:1].isalpha()]
    assert top == ["schema_version", "plugin", "kind", "host", "ports", "groups", "parameters"]
    drive = text[text.index("- name: Drive"):]
    assert drive.index("symbol:") < drive.index("facts:") < drive.index("visibility:")
    assert drive.index("min:") < drive.index("max:") < drive.index("default:")


def test_awkward_floats_and_strings_survive_a_round_trip(profile):
    """1e-05 is written bare by a naive dump and read back as a *string*; a
    version like '1.10' or a label like 'no' must stay text."""
    drive = param(profile, "Drive")
    drive["facts"]["min"] = 1e-05
    drive["facts"]["max"] = 1.5e20
    drive["label"] = {"value": "no", "provenance": "draft"}
    profile["plugin"]["version"] = "1.10"
    reloaded = load_profile_text(dump_profile(profile))
    assert reloaded == profile
    assert isinstance(param(reloaded, "Drive")["facts"]["min"], float)
    assert dump_profile(reloaded) == dump_profile(profile)


def test_unicode_is_written_readably(profile):
    param(profile, "Drive")["label"] = {"value": "Gain – départ", "provenance": "draft"}
    assert "départ" in dump_profile(profile)


def test_unknown_keys_survive_a_rewrite_so_nothing_is_lost(profile):
    profile["zzz_extra"] = {"note": 1}
    assert load_profile_text(dump_profile(profile))["zzz_extra"] == {"note": 1}


def test_comments_are_not_preserved_and_that_is_documented():
    """PyYAML drops comments on load. The module says so; this pins that the
    loss is the header-only kind we regenerate, not data."""
    text = "# my own note\nschema_version: 1\n"
    assert "my own note" not in dump_profile(load_profile_text(text))


def test_a_repeated_key_is_refused(tmp_path):
    """PyYAML would silently keep the last of two `visibility:` lines."""
    with pytest.raises(ProfileError, match="duplicate key 'kind'"):
        load_profile_text("kind: effect\nkind: amp\n")


@pytest.mark.parametrize("text", ["- a\n- b\n", "just text\n", "{unclosed: [", ""])
def test_unreadable_profiles_raise_profile_error(text):
    with pytest.raises(ProfileError):
        load_profile_text(text)


def test_missing_file_raises_profile_error(tmp_path):
    with pytest.raises(ProfileError, match="cannot read"):
        load_profile(tmp_path / "absent.yaml")


def test_example_is_plain_yaml_a_person_can_read():
    """Not XML, not anchors, not tags: safe_load needs no special loader."""
    text = EXAMPLE.read_text()
    assert yaml.safe_load(text)["schema_version"] == 1
    assert "&id" not in text and "!!" not in text


# --- the command line --------------------------------------------------------


def test_cli_validate_against_saved_facts(capsys):
    from sushi_rig.cli import main

    code = main(["profile", "validate", str(EXAMPLE),
                 "--facts", str(HERE / "fixtures" / "probe_gxts9.json")])
    out = capsys.readouterr()
    assert code == 0
    assert "0 error(s), 1 warning(s)" in out.out
    assert "unverified-version" in out.err


def test_cli_validate_exits_nonzero_on_an_invented_parameter(tmp_path, profile, capsys):
    from sushi_rig.cli import main

    param(profile, "Drive")["symbol"] = "fslider9_"
    bad = tmp_path / "bad.yaml"
    bad.write_text(dump_profile(profile))
    code = main(["profile", "validate", str(bad),
                 "--facts", str(HERE / "fixtures" / "probe_gxts9.json")])
    assert code == 1
    assert "invented-parameter" in capsys.readouterr().out
