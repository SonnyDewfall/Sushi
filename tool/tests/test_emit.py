import pytest

from sushi_rig.emit import emit
from sushi_rig.spec import PluginSpec, RigSpec, TrackSpec


def _simple_rig(**meta):
    return RigSpec(
        host={"samplerate": 48000},
        tracks=[
            TrackSpec(
                name="fx_track",
                channels=2,
                inputs=[{"engine_bus": 0, "track_bus": 0}],
                outputs=[{"engine_bus": 0, "track_bus": 0}],
                plugins=[
                    PluginSpec(name="comp", type="lv2", uri="http://example.org/comp"),
                ],
            )
        ],
        meta=meta,
    )


def test_emit_without_state_has_no_initial_state():
    config = emit(_simple_rig())
    assert "initial_state" not in config
    assert config["tracks"][0]["plugins"][0] == {
        "name": "comp",
        "type": "lv2",
        "uri": "http://example.org/comp",
    }


def test_emit_invalid_spec_exits_rather_than_writing_a_bad_config():
    bad = RigSpec(tracks=[TrackSpec(name="t", plugins=[PluginSpec(name="p", type="lv2")])])
    with pytest.raises(SystemExit):
        emit(bad)


def test_emit_known_spec_and_state_produces_expected_initial_state():
    state = {
        "processors": {
            "comp": {
                "parameters": {"Threshold": 0.421875, "Ratio": 0.25},
                "bypassed": False,
            }
        }
    }
    config = emit(_simple_rig(), state)
    assert config["initial_state"] == [
        {
            "processor": "comp",
            "bypassed": False,
            "parameters": {"Threshold": 0.421875, "Ratio": 0.25},
        }
    ]


def test_emit_rounds_parameter_values_to_six_decimal_places():
    state = {"processors": {"comp": {"parameters": {"X": 1 / 3}}}}
    config = emit(_simple_rig(), state)
    assert config["initial_state"][0]["parameters"]["X"] == 0.333333


def test_emit_initial_state_uses_dict_form_not_list_of_name_value_objects():
    """Sushi's own shipped example configs use {name: value} dicts for both
    parameters and properties inside initial_state — not a list of
    {"name", "value"} objects, which is what the brief §2.4 example and the
    original prototype both assumed, and which Sushi 1.3.0 actually refuses
    to load ("Failed to load the initial processor states.", exit 7)."""
    state = {
        "processors": {
            "comp": {
                "parameters": {"Threshold": 0.5},
                "properties": {"filename": "x.wav"},
            }
        }
    }
    config = emit(_simple_rig(), state)
    entry = config["initial_state"][0]
    assert isinstance(entry["parameters"], dict)
    assert isinstance(entry["properties"], dict)


def test_emit_warns_and_skips_state_for_processor_not_in_spec(capsys):
    """A processor in captured state but not in the spec means the spec and
    the live session have diverged — must warn, never silently resolve."""
    state = {
        "processors": {
            "comp": {"parameters": {"X": 0.5}},
            "ghost_processor": {"parameters": {"Y": 0.1}},
        }
    }
    config = emit(_simple_rig(), state)
    processors_in_output = {e["processor"] for e in config["initial_state"]}
    assert processors_in_output == {"comp"}
    assert "ghost_processor" in capsys.readouterr().err


def test_emit_passes_meta_through_verbatim():
    rig = _simple_rig(name="acoustic_chorus", version="1.2", status="wip")
    config = emit(rig)
    assert config["_meta"] == {"name": "acoustic_chorus", "version": "1.2", "status": "wip"}


def test_emit_omits_meta_key_when_absent():
    config = emit(_simple_rig())
    assert "_meta" not in config


def test_emit_passes_midi_osc_cv_control_through_unchanged():
    rig = _simple_rig()
    rig.midi = {"cc_mappings": [{"cc_number": 7}]}
    rig.osc = {"enable_all_processor_outputs": True}
    rig.cv_control = {"foo": "bar"}
    config = emit(rig)
    assert config["midi"] == rig.midi
    assert config["osc"] == rig.osc
    assert config["cv_control"] == rig.cv_control


def test_emit_state_with_program_and_properties():
    state = {
        "processors": {
            "comp": {
                "program": 3,
                "properties": {"filename": "kick.wav"},
            }
        }
    }
    config = emit(_simple_rig(), state)
    entry = config["initial_state"][0]
    assert entry["program"] == 3
    assert entry["properties"] == {"filename": "kick.wav"}


# --- captured chain order ---------------------------------------------------


def _three_plugin_rig():
    """A rig whose yaml order is deliberately different from what the captured
    states below ask for, so an assertion can't pass by accident."""
    from sushi_rig.spec import PluginSpec, RigSpec, TrackSpec

    return RigSpec(
        host={"samplerate": 48000},
        tracks=[
            TrackSpec(
                name="board",
                plugins=[
                    PluginSpec(name="compressor", type="lv2", uri="urn:a"),
                    PluginSpec(name="overdrive", type="lv2", uri="urn:b"),
                    PluginSpec(name="reverb", type="lv2", uri="urn:c"),
                ],
            )
        ],
    )


def _plugin_names(config):
    return [p["name"] for p in config["tracks"][0]["plugins"]]


def test_captured_order_overrides_the_yaml_order():
    """Pedal order is a tonal decision, so a session reordered live and then
    saved has to keep that order — otherwise the save writes the new values
    under the old order and silently loses the change."""
    rig = _three_plugin_rig()
    state = {"tracks": {"board": ["reverb", "compressor", "overdrive"]}}
    assert _plugin_names(emit(rig, state)) == ["reverb", "compressor", "overdrive"]


def test_state_without_a_tracks_key_leaves_order_alone():
    """A state file written before order was captured is not an instruction to
    reorder."""
    rig = _three_plugin_rig()
    state = {"processors": {"compressor": {"parameters": {}}}}
    assert _plugin_names(emit(rig, state)) == ["compressor", "overdrive", "reverb"]


def test_captured_order_naming_an_unknown_processor_warns_and_skips(capsys):
    rig = _three_plugin_rig()
    state = {"tracks": {"board": ["reverb", "ghost", "compressor", "overdrive"]}}
    names = _plugin_names(emit(rig, state))
    assert names == ["reverb", "compressor", "overdrive"]
    assert "ghost" in capsys.readouterr().err


def test_plugin_missing_from_the_captured_order_is_kept_not_dropped(capsys):
    """A stale capture must not silently remove a plugin the spec asks for —
    that would change the rig rather than just its order."""
    rig = _three_plugin_rig()
    state = {"tracks": {"board": ["reverb", "compressor"]}}
    names = _plugin_names(emit(rig, state))
    assert set(names) == {"compressor", "overdrive", "reverb"}
    assert names[:2] == ["reverb", "compressor"]
    assert names[-1] == "overdrive"
    assert "overdrive" in capsys.readouterr().err


def test_emit_does_not_mutate_the_rig_spec():
    """emit is called more than once in some flows; a function that quietly
    reorders its own input would make the second call disagree with the
    first."""
    rig = _three_plugin_rig()
    before = [p.name for p in rig.tracks[0].plugins]
    emit(rig, {"tracks": {"board": ["reverb", "overdrive", "compressor"]}})
    assert [p.name for p in rig.tracks[0].plugins] == before


def _write_rig(tmp_path):
    path = tmp_path / "rig.yaml"
    path.write_text(
        "meta:\n  name: t\n  version: '1.0'\n"
        "tracks:\n  - name: board\n    channels: 2\n    plugins:\n"
        "      - name: reverb\n        type: lv2\n        uri: urn:x\n"
    )
    return path


def test_a_tracks_bypass_is_never_written_to_initial_state(tmp_path):
    """Sushi cascades a track's bypass to every processor on it, so one
    `"bypassed": false` on the track silently undoes every per-plugin flag in
    the same file. Reported as bypass simply not being restored on load; the
    parameter values and chain order in that same file all applied correctly,
    which is what disguised it.

    Isolated against a running Sushi: with the track entry present the plugin
    flags are wiped whether it comes first or last, and removing only that key
    makes them apply."""
    rig = RigSpec.load(_write_rig(tmp_path))
    state = {"processors": {
        "board": {"bypassed": False, "parameters": {"gain": 0.8}},
        "reverb": {"bypassed": True, "parameters": {}},
    }}
    entries = {e["processor"]: e for e in emit(rig, state)["initial_state"]}
    assert "bypassed" not in entries["board"], "a track's bypass must not be written"
    assert entries["board"]["parameters"] == {"gain": 0.8}, "its parameters still are"
    assert entries["reverb"]["bypassed"] is True, "a plugin's bypass still is"
