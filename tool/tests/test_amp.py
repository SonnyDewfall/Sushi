"""The amp that runs outside Sushi, and how it travels in a config.

None of this needs Carla, NAM or audio hardware. What it pins is the knowledge
that was established empirically against the real thing — Carla's plugin and
parameter numbering, the shape of a project file, and the fact that Sushi
tolerates an `_amp` key in its own config — so that none of it has to be
rediscovered by ear.
"""

from __future__ import annotations

import json

import pytest

from sushi_rig.amp import (
    CONFIG_KEY,
    DEFAULTS,
    MODEL_PROPERTY,
    NAM_URI,
    PANEL_PARAMETERS,
    PARAMETER_INDEX,
    AmpError,
    carla_project,
    clamp,
    parameter_message,
    read_amp,
    relative_model,
    resolve_model,
    write_amp,
)


# --- the Carla project ------------------------------------------------------


def test_project_carries_the_model_as_atom_path_custom_data():
    """This is the only way the model can be set: Sushi cannot do it, and Carla
    persists an LV2 plugin's patch state as CustomData typed atom:Path."""
    xml = carla_project("/rig/amp/models/Marshall.nam")
    assert NAM_URI in xml
    assert MODEL_PROPERTY in xml
    assert "http://lv2plug.in/ns/ext/atom#Path" in xml
    assert "/rig/amp/models/Marshall.nam" in xml


def test_a_model_path_with_xml_characters_is_escaped():
    """Model names come from a public library and are entirely arbitrary — a
    stray ampersand would otherwise produce a project file Carla cannot parse,
    and the failure would look like 'the amp did not start'."""
    xml = carla_project("/rig/amp/models/Vox AC30 <Top Boost> & Bright.nam")
    assert "&amp;" in xml and "&lt;" in xml and "&gt;" in xml
    assert "<Top Boost>" not in xml


def test_project_is_valid_xml():
    from xml.etree import ElementTree

    ElementTree.fromstring(carla_project("/rig/m.nam"))


# --- Carla's numbering ------------------------------------------------------


def test_parameter_message_uses_carlas_own_indices():
    """Carla numbers parameters over control ports only, in port order — NOT by
    LV2 port index, which for NAM is 4, 5 and 6. Established against a running
    Carla."""
    address, args = parameter_message("Input Lvl", 6.0)
    assert address == "/Carla/0/set_parameter_value"
    assert args == [0, 6.0]
    assert parameter_message("Output Lvl", -3.0)[1][0] == 1
    assert parameter_message("Quality", 1.0)[1][0] == 2


def test_plugin_is_numbered_from_zero():
    """Carla said so itself: id 1 came back 'invalid plugin id', id 0 was
    accepted."""
    assert parameter_message("Input Lvl", 0.0)[0] == "/Carla/0/set_parameter_value"


def test_an_unknown_parameter_is_refused_rather_than_guessed():
    with pytest.raises(AmpError):
        parameter_message("Presence", 1.0)


def test_values_are_clamped_to_what_the_plugin_declares():
    """Carla clamps too, but doing it here means the value remembered for saving
    is the value that actually took effect."""
    assert clamp("Input Lvl", 50.0) == 20.0
    assert clamp("Input Lvl", -50.0) == -20.0
    assert clamp("Quality", 2.0) == 1.0
    assert parameter_message("Output Lvl", 99.0)[1][1] == 20.0


# --- the config section -----------------------------------------------------


def test_amp_rides_in_the_sushi_config():
    """One file describes the whole rig. Sushi ignores unknown top-level keys —
    confirmed by loading a config carrying this one, which it accepted without
    comment — so the amp does not need a sidecar."""
    config = write_amp({"tracks": []}, "amp/models/M.nam", {"Input Lvl": 3.0})
    assert config[CONFIG_KEY]["model"] == "amp/models/M.nam"
    assert config[CONFIG_KEY]["parameters"]["Input Lvl"] == 3.0
    assert read_amp(config)["model"] == "amp/models/M.nam"


def test_unset_parameters_fall_back_to_the_plugins_defaults():
    config = write_amp({}, "m.nam", {"Input Lvl": 3.0})
    assert config[CONFIG_KEY]["parameters"]["Output Lvl"] == DEFAULTS["Output Lvl"]
    assert config[CONFIG_KEY]["parameters"]["Quality"] == DEFAULTS["Quality"]


def test_quality_is_saved_even_though_it_is_not_on_the_panel():
    """It trades CPU against accuracy rather than shaping tone, so it gets no
    fader — but a config that did not restore it would come back subtly
    different."""
    assert "Quality" not in PANEL_PARAMETERS
    assert "Quality" in write_amp({}, "m.nam", {})[CONFIG_KEY]["parameters"]
    assert "Quality" in PARAMETER_INDEX


def test_no_amp_means_no_key_rather_than_an_empty_one():
    """A config for a rig with no amp should look like one."""
    config = write_amp({CONFIG_KEY: {"model": "old.nam"}, "tracks": []}, None, {})
    assert CONFIG_KEY not in config
    assert read_amp(config) is None


def test_a_config_without_an_amp_reads_as_none():
    assert read_amp({"tracks": []}) is None
    assert read_amp({CONFIG_KEY: "not a dict"}) is None


# --- model paths ------------------------------------------------------------


def test_models_inside_the_checkout_are_stored_relative(tmp_path):
    """An absolute path bakes in one machine's home directory, which is exactly
    the portability problem issue #6 is about."""
    model = tmp_path / "amp" / "models" / "M.nam"
    model.parent.mkdir(parents=True)
    model.touch()
    assert relative_model(model, tmp_path) == "amp/models/M.nam"


def test_models_outside_the_checkout_stay_absolute(tmp_path):
    """Better an honest absolute path than a chain of `..` that breaks the
    moment the config moves."""
    outside = tmp_path.parent / "elsewhere.nam"
    assert relative_model(outside, tmp_path) == str(outside)


def test_a_relative_model_resolves_against_the_checkout(tmp_path):
    model = tmp_path / "amp" / "models" / "M.nam"
    model.parent.mkdir(parents=True)
    model.touch()
    assert resolve_model("amp/models/M.nam", tmp_path) == model


def test_a_missing_model_is_refused_loudly(tmp_path):
    """A Carla started with a model that is not there comes up looking perfectly
    healthy and processes silence — the most confusing failure this rig can
    produce, so it must not be allowed to happen quietly."""
    with pytest.raises(AmpError, match="not found"):
        resolve_model("amp/models/gone.nam", tmp_path)


# --- patchbay rewiring ------------------------------------------------------


BASE_PATCHBAY = """<!DOCTYPE patchbay>
<patchbay name="rig" version="0.9.9">
 <items>
  <item node-type="pipewire" port-type="pipewire-audio">
   <output node="iface" port="iface:capture_AUX0"/>
   <input node="sushi" port="sushi:audio_input_0"/>
  </item>
  <item node-type="pipewire" port-type="pipewire-audio">
   <output node="iface" port="iface:capture_AUX0"/>
   <input node="sushi" port="sushi:audio_input_1"/>
  </item>
  <item node-type="pipewire" port-type="pipewire-audio">
   <output node="iface" port="iface:capture_AUX1"/>
   <input node="sushi" port="sushi:audio_input_2"/>
  </item>
  <item node-type="pipewire" port-type="pipewire-audio">
   <output node="sushi" port="sushi:audio_output_0"/>
   <input node="iface" port="iface:playback_AUX0"/>
  </item>
  <item node-type="pipewire" port-type="pipewire-audio">
   <output node="iface" port="iface:capture_AUX0"/>
   <input node="fmit" port="fmit:input"/>
  </item>
 </items>
</patchbay>
"""


def _links(xml):
    import re
    return [
        (m.group(1), m.group(2))
        for m in re.finditer(
            r'<output node="[^"]*" port="([^"]*)"/>\s*<input node="[^"]*" port="([^"]*)"/>',
            xml,
        )
    ]


def test_the_guitar_goes_through_the_amp_instead_of_straight_in():
    from sushi_rig.amp import amp_patchbay

    links = _links(amp_patchbay(BASE_PATCHBAY))
    assert ("iface:capture_AUX0", "NAM:Input") in links
    assert ("NAM:Output", "sushi:audio_input_0") in links
    assert ("NAM:Output", "sushi:audio_input_1") in links
    assert ("iface:capture_AUX0", "sushi:audio_input_0") not in links, "no dry path"
    assert ("iface:capture_AUX0", "sushi:audio_input_1") not in links


def test_a_second_input_channel_is_not_summed_into_a_mono_amp():
    """NAM is mono. The saved patchbay also wires a second interface channel
    into Sushi's spare inputs, and a naive 'redirect everything feeding Sushi'
    rule summed both physical inputs into one amp input. Caught by generating it
    and reading the result."""
    from sushi_rig.amp import amp_patchbay

    links = _links(amp_patchbay(BASE_PATCHBAY))
    assert ("iface:capture_AUX1", "NAM:Input") not in links
    assert ("iface:capture_AUX1", "sushi:audio_input_2") in links, "left alone"


def test_outputs_and_the_tuner_are_untouched():
    """The tuner wants the dry signal, not the amped one, and Sushi's outputs
    have nothing to do with the amp."""
    from sushi_rig.amp import amp_patchbay

    links = _links(amp_patchbay(BASE_PATCHBAY))
    assert ("sushi:audio_output_0", "iface:playback_AUX0") in links
    assert ("iface:capture_AUX0", "fmit:input") in links


def test_a_patchbay_feeding_nothing_into_sushi_is_left_alone():
    """Better an unchanged patchbay than a guessed one."""
    from sushi_rig.amp import amp_patchbay

    empty = '<!DOCTYPE patchbay>\n<patchbay name="rig">\n <items>\n </items>\n</patchbay>\n'
    assert amp_patchbay(empty) == empty


def test_the_result_is_still_valid_xml():
    from xml.etree import ElementTree

    from sushi_rig.amp import amp_patchbay

    ElementTree.fromstring(
        amp_patchbay(BASE_PATCHBAY).replace("<!DOCTYPE patchbay>", "")
    )


# --- reading a .nam model ---------------------------------------------------


def _a2_model():
    """A NAM A2 container: two WaveNet paths, lite and full."""
    return {
        "version": "0.7.0",
        "architecture": "SlimmableContainer",
        "sample_rate": 48000,
        "metadata": {"name": "Test Amp", "gear_make": "Acme", "gear_type": "amp"},
        "config": {"submodels": [
            {"max_value": 0.5, "model": {
                "architecture": "WaveNet",
                "config": {"layers": [{"channels": 3}]},
                "weights": [0.0] * 1871,
            }},
            {"max_value": 1, "model": {
                "architecture": "WaveNet",
                "config": {"layers": [{"channels": 8}]},
                "weights": [0.0] * 12146,
            }},
        ]},
    }


def _write(tmp_path, payload, name="m.nam"):
    path = tmp_path / name
    path.write_text(json.dumps(payload))
    return path


def test_an_a2_container_reports_its_separate_quality_tiers(tmp_path):
    """A2 is not one model but a container, and NAM's Quality control picks
    which path runs — which is why that control does something on an A2 model
    and nothing at all on an A1 one."""
    from sushi_rig.amp import describe_model

    d = describe_model(_write(tmp_path, _a2_model()))
    assert d["slimmable"] is True
    assert [t["channels"] for t in d["tiers"]] == [[3], [8]]
    assert [t["weights"] for t in d["tiers"]] == [1871, 12146]


def test_an_a1_model_reports_a_single_path(tmp_path):
    """Older models name their architecture directly and carry one set of
    weights, so there is no tier to choose between."""
    from sushi_rig.amp import describe_model

    d = describe_model(_write(tmp_path, {
        "architecture": "WaveNet",
        "sample_rate": 48000,
        "config": {"layers": [{"channels": 16}]},
        "weights": [0.0] * 500,
    }))
    assert d["slimmable"] is False
    assert len(d["tiers"]) == 1
    assert d["tiers"][0]["channels"] == [16]


def test_quality_hints_name_the_setting_that_selects_each_path(tmp_path):
    from sushi_rig.amp import quality_hint

    assert quality_hint(2, 0) == "Quality < 0.5"
    assert quality_hint(2, 1) == "Quality > 0.5"
    assert "no effect" in quality_hint(1, 0), "a single-path model ignores it"


def test_a_sample_rate_mismatch_is_shouted_about(tmp_path):
    """NAM does no resampling. A 44.1 kHz model in a 48 kHz rig plays at the
    wrong pitch and speed, and nothing else warns you."""
    from sushi_rig.amp import describe_model, summarise_model

    payload = _a2_model() | {"sample_rate": 44100}
    out = summarise_model(describe_model(_write(tmp_path, payload)), rig_rate=48000)
    assert "MISMATCH" in out and "44100" in out
    assert "wrong pitch" in out


def test_a_matching_sample_rate_says_so(tmp_path):
    from sushi_rig.amp import describe_model, summarise_model

    out = summarise_model(describe_model(_write(tmp_path, _a2_model())), rig_rate=48000)
    assert "MISMATCH" not in out and "matches the rig" in out


def test_a_float_sample_rate_is_read_as_a_rate(tmp_path):
    """Models from different toolchains write 48000.0 rather than 48000."""
    from sushi_rig.amp import describe_model

    assert describe_model(
        _write(tmp_path, _a2_model() | {"sample_rate": 48000.0})
    )["sample_rate"] == 48000


def test_something_that_is_not_a_model_is_refused_clearly(tmp_path):
    from sushi_rig.amp import AmpError, describe_model

    broken = tmp_path / "broken.nam"
    broken.write_text("{not json")
    with pytest.raises(AmpError, match="not valid JSON"):
        describe_model(broken)
    with pytest.raises(AmpError, match="architecture"):
        describe_model(_write(tmp_path, {"hello": "world"}, "wrong.nam"))
    with pytest.raises(AmpError, match="cannot read"):
        describe_model(tmp_path / "absent.nam")


# --- preparing the amp for a run --------------------------------------------


def _rig_tree(tmp_path, model_name="M.nam"):
    (tmp_path / "amp" / "models").mkdir(parents=True)
    (tmp_path / "amp" / "models" / model_name).write_text(json.dumps({
        "architecture": "WaveNet", "sample_rate": 48000,
        "config": {"layers": [{"channels": 8}]}, "weights": [0.0] * 10,
    }))
    (tmp_path / "Patchbay").mkdir()
    (tmp_path / "Patchbay" / "rig.qpwgraph").write_text(BASE_PATCHBAY)
    return tmp_path


def test_a_config_with_no_amp_prepares_nothing(tmp_path):
    """The supervisor's signal to start no amp at all."""
    from sushi_rig.amp import prepare

    config = tmp_path / "c.json"
    config.write_text(json.dumps({"tracks": []}))
    assert prepare(config, tmp_path, tmp_path / "run") == {}


def test_preparing_writes_a_project_and_a_patchbay(tmp_path):
    """Both are generated per run — the project because the model changes with
    the config, the patchbay so it cannot drift from the saved one."""
    from sushi_rig.amp import prepare

    root = _rig_tree(tmp_path)
    config = root / "c.json"
    config.write_text(json.dumps({"_amp": {"model": "amp/models/M.nam",
                                           "parameters": {"Input Lvl": 3.0}}}))
    out = prepare(config, root, root / "run")
    assert out["project"].read_text().count("neural-amp-modeler") >= 1
    assert "NAM:Input" in out["patchbay"].read_text()
    assert out["values"] == {"Input Lvl": 3.0}


def test_an_override_model_wins_over_the_config(tmp_path):
    """--amp-model is for auditioning without editing the config."""
    from sushi_rig.amp import prepare

    root = _rig_tree(tmp_path, "Other.nam")
    (root / "amp" / "models" / "M.nam").write_text(
        (root / "amp" / "models" / "Other.nam").read_text())
    config = root / "c.json"
    config.write_text(json.dumps({"_amp": {"model": "amp/models/M.nam", "parameters": {}}}))
    assert prepare(config, root, root / "run", "Other.nam")["model"] == "amp/models/Other.nam"


def test_an_override_works_on_a_config_that_has_never_had_an_amp(tmp_path):
    """Trying a model on a rig with no amp section is a reasonable thing to
    want, and refusing would be surprising."""
    from sushi_rig.amp import prepare

    root = _rig_tree(tmp_path)
    config = root / "c.json"
    config.write_text(json.dumps({"tracks": []}))
    assert prepare(config, root, root / "run", "M.nam")["model"] == "amp/models/M.nam"


def test_a_missing_model_still_refuses_loudly(tmp_path):
    """A Carla started without its model processes silence while looking
    healthy — the loudness has to survive the move into prepare()."""
    from sushi_rig.amp import AmpError, prepare

    root = _rig_tree(tmp_path)
    config = root / "c.json"
    config.write_text(json.dumps({"_amp": {"model": "amp/models/gone.nam", "parameters": {}}}))
    with pytest.raises(AmpError, match="not found"):
        prepare(config, root, root / "run")
