"""Today's tones as v2 rigs (#30): converted, held to the configs they replace,
and null-tested against the references those configs were calibrated to.

Until `up` runs v2 rigs, each tone exists twice — `config/<name>.json` and
`config/rigs/<name>.yaml` — and the two must not drift. The fast test says they
hold the same plugins and settings; the slow one says they sound the same.
"""

from __future__ import annotations

import json

import pytest
import yaml

from sushi_rig import rigfile
from sushi_rig.convert import ConvertError, Input, convert, differences, parse_input, to_rig
from sushi_rig.paths import checkout_root

ROOT = checkout_root()
CONFIGS = sorted((ROOT / "config").glob("*.json"))


def v1(name: str) -> dict:
    return json.loads((ROOT / "config" / f"{name}.json").read_text())


@pytest.mark.parametrize("config", CONFIGS, ids=lambda p: p.stem)
def test_every_tone_has_a_v2_rig_that_holds_everything_it_did(config):
    rig = ROOT / "config" / "rigs" / f"{config.stem}.yaml"
    assert rig.exists(), f"no v2 rig for {config.name}: `sushi-rig convert {config}`"
    assert differences(json.loads(config.read_text()), rigfile.emit_rig(rigfile.load_rig(rig))) == []


@pytest.mark.parametrize("config", CONFIGS, ids=lambda p: p.stem)
def test_every_rig_names_the_plugins_its_config_names_read_without_the_converter(config):
    """The test above asks `differences` whether a rig matches its config, and
    `differences` lives in convert.py beside the code that wrote the rigs: if
    the two share a blind spot, it passes. This reads both files with nothing
    but json and yaml, and compares the plugin names and URIs on each channel
    with those on the config's track of the same name, so a plugin the converter
    dropped, renamed or moved to another channel fails here whatever it believes.
    """
    raw_config = json.loads(config.read_text())
    raw_rig = yaml.safe_load((ROOT / "config" / "rigs" / f"{config.stem}.yaml").read_text())

    def plugin(entry):
        return entry["name"], entry.get("uri") or entry.get("uid")

    wanted = {t["name"]: {plugin(p) for p in t["plugins"]} for t in raw_config["tracks"]}
    found = {c["name"]: {plugin(p) for p in c["chain"]} for c in raw_rig["channels"]}
    assert found == wanted
    assert all(found.values()), "a channel with no plugins would make this vacuous"


def test_a_converted_tone_is_a_mono_guitar_channel_into_the_master():
    rig = convert(v1("electric-clean"), "electric-clean")
    (channel,) = rig["channels"]
    assert (channel["name"], channel["input"], channel["width"]) == ("board", "guitar-in", "mono")
    assert channel["strip"] == {"gain": 0.833333, "pan": 0.5, "mute": 0.0}
    assert rig["master"] == {"output": "monitor-out", "chain": []}
    assert rig["amp"]["model"] == "amp/models/Neve 2.nam"
    assert rig["meta"]["source"] == "config/src/electric_board.yaml"


def test_a_conversion_that_lost_a_setting_would_be_refused():
    config = v1("electric-clean")
    rig = to_rig(config, "electric-clean")
    rig["channels"][0]["chain"][0]["parameters"]["RATIO"] = 0.5
    found = differences(config, rigfile.emit_rig(rigfile.parse_rig(rig)))
    assert len(found) == 1 and found[0].startswith("initial_state for 'compressor'")


def test_a_conversion_that_reordered_the_chain_would_be_refused():
    """Pedal order is a tone decision; a rig holding every setting in the wrong
    order is not the same tone."""
    config = v1("electric-clean")
    rig = to_rig(config, "electric-clean")
    chain = rig["channels"][0]["chain"]
    chain[0], chain[1] = chain[1], chain[0]
    found = differences(config, rigfile.emit_rig(rigfile.parse_rig(rig)))
    assert len(found) == 1 and found[0].startswith("track 'board': plugins")


def test_more_than_one_track_must_be_told_its_inputs():
    with pytest.raises(ConvertError) as caught:
        to_rig(v1("guitar_vocal"), "guitar_vocal")
    assert "track 'guitar': which input it plays from is not in the config" in caught.value.problems[0]
    rig = convert(v1("guitar_vocal"), "guitar_vocal", {
        "guitar": Input("guitar-in", "mono"), "vocal": Input("vocal-in", "mono"),
    })
    assert [c["input"] for c in rig["channels"]] == ["guitar-in", "vocal-in"]


@pytest.mark.parametrize("change, expected", [
    (lambda c: c.update(midi={}), "top-level 'midi' has no place in a rig file"),
    (lambda c: c["tracks"][0].update(multibus=True), "track 'board': 'multibus' has no place"),
    (lambda c: c["tracks"][0].update(outputs=[{"engine_bus": 1, "track_bus": 0}]), "sums onto engine bus 0"),
    (lambda c: c["initial_state"].append({"processor": "ghost", "parameters": {}}), "'ghost', which is not in the config"),
    (lambda c: c["initial_state"][0].update(bypassed=True), "only parameters are kept for a track's strip"),
])
def test_what_a_rig_file_cannot_hold_is_refused_by_name(change, expected):
    config = v1("electric-clean")
    change(config)
    with pytest.raises(ConvertError) as caught:
        to_rig(config, "electric-clean")
    assert any(expected in p for p in caught.value.problems), caught.value.problems


def test_an_input_is_given_as_track_role_width():
    assert parse_input("vocal=vocal-in:mono") == ("vocal", Input("vocal-in", "mono"))
    with pytest.raises(ConvertError):
        parse_input("vocal=vocal-in")


def test_the_hand_written_guitar_vocal_is_the_same_tone_as_its_config():
    """Written before `convert` existed, with comments a conversion would lose."""
    rig = rigfile.load_rig(ROOT / "config" / "rigs" / "guitar_vocal.yaml")
    assert differences(v1("guitar_vocal"), rigfile.emit_rig(rig)) == []


def test_the_command_writes_once_and_refuses_to_overwrite(tmp_path, capsys):
    from sushi_rig.cli import main

    out = tmp_path / "clean.yaml"
    assert main(["convert", str(ROOT / "config" / "electric-clean.json"), "-o", str(out)]) == 0
    text = out.read_text()
    assert text.startswith("# Converted from config/electric-clean.json")
    assert rigfile.parse_rig(yaml.safe_load(text)).name == "electric-clean"
    assert main(["convert", str(ROOT / "config" / "electric-clean.json"), "-o", str(out)]) == 1
    assert "exists; --force replaces it" in capsys.readouterr().out


def test_settings_in_a_rig_file_are_checked_for_shape():
    raw = yaml.safe_load((ROOT / "config" / "rigs" / "electric-clean.yaml").read_text())
    raw["channels"][0]["chain"][0]["parameters"]["RATIO"] = "high"
    raw["channels"][0]["chain"][1]["bypassed"] = "no"
    with pytest.raises(rigfile.RigFileError) as caught:
        rigfile.parse_rig(raw)
    assert len(caught.value.problems) == 2


def test_a_capture_overrides_the_files_settings_and_keeps_the_rest():
    rig = rigfile.load_rig(ROOT / "config" / "rigs" / "electric-clean.yaml")
    config = rigfile.emit_rig(rig, {"processors": {"compressor": {"parameters": {"RATIO": 0.5}}}})
    state = {e["processor"]: e for e in config["initial_state"]}
    assert state["compressor"]["parameters"]["RATIO"] == 0.5
    assert state["compressor"]["parameters"]["KNEE"] == 0.1
    assert state["compressor"]["bypassed"] is False
    assert state["board_send"]["properties"] == {"destination_name": "master_return"}


def test_what_is_heard_offline_is_the_master_alone():
    config = rigfile.emit_rig(rigfile.load_rig(ROOT / "config" / "rigs" / "guitar_vocal.yaml"))
    views = dict(rigfile.heard(config))
    assert list(views) == ["guitar_vocal.guitar", "guitar_vocal.vocal"]
    vocal = views["guitar_vocal.vocal"]
    assert [t["name"] for t in vocal["tracks"]] == ["vocal", "master"]
    assert vocal["tracks"][0]["outputs"] == []
    assert {e["engine_channel"] for e in vocal["tracks"][0]["inputs"]} == {0}
    assert [e["processor"] for e in vocal["initial_state"]] == ["vocal_send"]


# --------------------------------------------------------------------------- #
# Against a real Sushi
# --------------------------------------------------------------------------- #

SUSHI = ROOT / "sushi"
RIGS = sorted((ROOT / "config" / "rigs").glob("*.yaml"))


@pytest.mark.skipif(not SUSHI.exists(), reason="needs the Sushi binary")
@pytest.mark.slow
@pytest.mark.parametrize("rig", RIGS, ids=lambda p: p.stem)
def test_every_v2_rig_sounds_like_the_config_it_replaces(rig, tmp_path):
    """#30's done-means: rendered through the master, each v2 rig matches the
    fingerprint its v1 config was calibrated to, within that reference's own
    tolerance."""
    pytest.importorskip("numpy")
    from sushi_rig.nulltest import compare, from_dict
    from sushi_rig.render import measure

    config = rigfile.emit_rig(rigfile.load_rig(rig))
    for name, view in rigfile.heard(config):
        reference = ROOT / "config" / "sound" / f"{name}.yaml"
        assert reference.exists(), f"no reference for {name}"
        source = tmp_path / f"{name}.json"
        source.write_text(json.dumps(view, indent=2))
        expected, tolerance = from_dict(yaml.safe_load(reference.read_text()))
        found = compare(expected, measure(source, ROOT, tmp_path / name, sushi_bin=str(SUSHI)), tolerance)
        assert found == [], f"{name}: " + "; ".join(str(f) for f in found)


# --------------------------------------------------------------------------- #
# Review fixes
# --------------------------------------------------------------------------- #

def _clean_rig() -> dict:
    return yaml.safe_load((ROOT / "config" / "rigs" / "electric-clean.yaml").read_text())


def test_a_captured_program_replaces_the_files_parameters():
    """emit drops a program written beside parameters, so keeping the file's
    would reload the previous program's sound."""
    config = rigfile.emit_rig(rigfile.parse_rig(_clean_rig()), {"processors": {"compressor": {"program": 3}}})
    (entry,) = [e for e in config["initial_state"] if e["processor"] == "compressor"]
    assert entry.get("program") == 3 and "parameters" not in entry


def test_a_setting_only_the_rig_has_is_a_difference_too():
    """electric_board's config has no initial_state, so a one-way check let
    anything be added to its rig unseen."""
    raw = yaml.safe_load((ROOT / "config" / "rigs" / "electric_board.yaml").read_text())
    raw["channels"][0]["chain"][0]["bypassed"] = True
    found = differences(v1("electric_board"), rigfile.emit_rig(rigfile.parse_rig(raw)))
    assert len(found) == 1 and "was None" in found[0]


def test_a_strip_off_unity_is_refused_in_the_file_and_from_a_capture():
    """Sushi applies a track's gain after its plugins, so after the send: a
    strip would change the stem and not the monitors."""
    raw = _clean_rig()
    raw["channels"][0]["strip"]["mute"] = 1.0
    with pytest.raises(rigfile.RigFileError, match="acts after its send"):
        rigfile.parse_rig(raw)
    with pytest.raises(rigfile.RigFileError, match="gain 0.5 is not unity"):
        rigfile.emit_rig(rigfile.parse_rig(_clean_rig()),
                         {"processors": {"board": {"parameters": {"gain": 0.5}}}})


@pytest.mark.parametrize("key", ["name", "description"])
def test_meta_cannot_rename_the_rig(key):
    raw = _clean_rig()
    raw["meta"][key] = "other"
    with pytest.raises(rigfile.RigFileError, match=f"meta: '{key}' is the rig's own"):
        rigfile.parse_rig(raw)


def test_a_track_that_may_be_stereo_is_not_taken_for_the_guitar():
    config = v1("electric-clean")
    config["tracks"][0]["inputs"] = [
        {"engine_channel": 0, "track_channel": 0}, {"engine_channel": 1, "track_channel": 1},
    ]
    with pytest.raises(ConvertError, match="cannot be assumed"):
        to_rig(config, "electric-clean")
    rig = to_rig(config, "electric-clean", {"board": Input("keys-in", "stereo")})
    assert rig["channels"][0]["width"] == "stereo"


def test_a_track_given_twice_is_refused():
    from sushi_rig.convert import parse_inputs

    with pytest.raises(ConvertError, match="twice"):
        parse_inputs(["vocal=vocal-in:mono", "vocal=guitar-in:stereo"])


@pytest.mark.parametrize("tracks", [["board"], [None], "board"])
def test_a_malformed_config_is_a_fail_line_not_a_traceback(tmp_path, capsys, tracks):
    from sushi_rig.cli import main

    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"tracks": tracks}))
    assert main(["convert", str(bad), "-o", str(tmp_path / "x.yaml")]) == 1
    assert capsys.readouterr().out.startswith("FAIL ")


def test_a_copy_of_a_config_converts_under_its_own_name_or_one_given(tmp_path, capsys):
    from sushi_rig.cli import main

    copy = tmp_path / "my-tone.json"
    copy.write_text((ROOT / "config" / "electric-clean.json").read_text())
    out = tmp_path / "out.yaml"
    assert main(["convert", str(copy), "-o", str(out)]) == 0
    assert yaml.safe_load(out.read_text())["name"] == "electric-clean"
    assert main(["convert", str(copy), "-o", str(out), "--force", "--name", "my-tone"]) == 0
    assert yaml.safe_load(out.read_text())["name"] == "my-tone"


# --------------------------------------------------------------------------- #
# Edges of the check, found by running it
# --------------------------------------------------------------------------- #

def _converts(config: dict) -> list[str]:
    """The refusal's lines, or [] when the conversion is accepted."""
    try:
        convert(config, "electric-clean")
    except ConvertError as refused:
        return refused.problems
    return []


def test_an_empty_description_or_amp_is_the_same_as_none():
    """`to_rig` writes no description or amp for an empty one, and `differences`
    compared them exactly, so the tone was refused as a different tone."""
    config = v1("electric-clean")
    config["_meta"]["description"] = ""
    assert _converts(config) == []
    config = v1("electric-clean")
    config["_amp"] = {}
    assert _converts(config) == []


def test_an_empty_host_config_is_refused_as_an_absent_one_is_for_the_host_it_adds():
    """A rig always compiles a host (48000 Hz when the file gives none), so
    neither can be the same tone. The refusal names the host, and only it."""
    empty, absent = v1("electric-clean"), v1("electric-clean")
    empty["host_config"] = {}
    del absent["host_config"]
    for config in (empty, absent):
        lines = _converts(config)
        assert len(lines) == 2 and lines[1].startswith("host_config: {'samplerate': 48000}, was ")


def test_empty_and_absent_agree_in_differences_both_ways():
    config = v1("electric-clean")
    compiled = rigfile.emit_rig(rigfile.load_rig(ROOT / "config" / "rigs" / "electric-clean.yaml"))
    config["_amp"] = {}
    del compiled["_amp"]
    assert differences(config, compiled) == []
    config["_amp"], compiled["_amp"] = None, {}
    assert differences(config, compiled) == []


def _with_user_plugin(name: str) -> dict:
    config = v1("electric-clean")
    config["tracks"][0]["plugins"].append(
        {"name": name, "type": "lv2", "uri": "http://guitarix.sourceforge.net/plugins/gx_compressor#_compressor"}
    )
    config["initial_state"].append({"processor": name, "parameters": {"RATIO": 0.25}})
    return config


def test_a_plugin_named_like_a_send_is_held_to_its_settings_not_mistaken_for_one():
    """`differences` dropped any compiled state ending in `_send`, so a player's
    own `delay_send` with settings was reported lost and the tone refused."""
    config = _with_user_plugin("delay_send")
    assert _converts(config) == []
    rig = convert(config, "electric-clean")
    rig["channels"][0]["chain"][-1]["parameters"]["RATIO"] = 0.5
    found = differences(config, rigfile.emit_rig(rigfile.parse_rig(rig)))
    assert len(found) == 1 and found[0].startswith("initial_state for 'delay_send'")


def test_the_compilers_own_send_is_still_not_counted_against_the_config():
    config = v1("electric-clean")
    compiled = rigfile.emit_rig(rigfile.load_rig(ROOT / "config" / "rigs" / "electric-clean.yaml"))
    assert any(e["processor"] == "board_send" for e in compiled["initial_state"])
    assert differences(config, compiled) == []


def test_a_track_with_no_parameters_gets_no_strip_rather_than_an_empty_one():
    """`strip: {}` is refused by the rig file, so writing one would make the
    conversion fail in the parser instead of saying what the config held."""
    config = v1("electric-clean")
    config["initial_state"][0] = {"processor": "board"}
    assert _converts(config) == [
        "track 'board': a strip with no gain, pan or mute cannot be kept"
    ]
    config["initial_state"][0] = {"processor": "board", "parameters": {}}
    assert len(_converts(config)) == 1


@pytest.mark.parametrize("parameters", [{"volume": 1.0}, {"gain": 0.833333, "volume": 1.0}])
def test_a_track_state_naming_more_than_gain_pan_mute_is_a_named_problem(parameters):
    config = v1("electric-clean")
    config["initial_state"][0] = {"processor": "board", "parameters": parameters}
    (line,) = _converts(config)
    assert line.startswith("track 'board': only gain, pan and mute are kept") and "volume" in line


def test_a_config_with_no_meta_converts_under_the_name_given():
    config = v1("electric-clean")
    del config["_meta"]
    assert _converts(config) == []
    config["_meta"] = {}
    assert _converts(config) == []


@pytest.mark.parametrize("key, value, expected", [
    ("_meta", {"name": "electric-clean", "description": {"x": 1}}, "_meta: description must be text"),
    ("_meta", {"name": "electric-clean", "description": 0}, "_meta: description must be text"),
    ("_meta", {"name": "electric-clean", "description": False}, "_meta: description must be text"),
    ("_meta", ["name"], "_meta must be a mapping"),
    ("_amp", ["model"], "_amp must be a mapping"),
    ("_amp", 0, "_amp must be a mapping"),
])
def test_a_meta_or_amp_of_the_wrong_shape_is_a_named_conversion_error(key, value, expected):
    """A non-text description escaped as a bare RigFileError from the parser, and
    a falsy one (0, false) was dropped without a word."""
    config = v1("electric-clean")
    config[key] = value
    (line,) = _converts(config)
    assert line.startswith(expected)


def test_the_mismatch_prints_the_meta_the_config_held():
    compiled = {"_meta": {"name": "other"}}
    config = {"_meta": {"name": "electric-clean", "description": ""}}
    (line,) = [f for f in differences(config, compiled) if f.startswith("_meta")]
    assert "'description': ''" in line


def test_the_roles_come_from_the_patchbay_not_a_second_spelling(monkeypatch):
    from sushi_rig import convert as convert_module
    from sushi_rig import patchbay

    assert convert_module.DEFAULT_INPUT == (patchbay.GUITAR_ROLE, "mono")
    assert to_rig(v1("electric-clean"), "t")["master"]["output"] == patchbay.MONITOR_ROLE
    monkeypatch.setattr(convert_module, "MONITOR_ROLE", "other-out")
    assert to_rig(v1("electric-clean"), "t")["master"]["output"] == "other-out"
