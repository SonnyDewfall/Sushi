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
