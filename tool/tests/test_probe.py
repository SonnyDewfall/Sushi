import json
import subprocess
from pathlib import Path

import pytest

from sushi_rig.probe import (
    is_config_drivable,
    parameter_units,
    scale_points_from,
    summarise,
    version_from_literals,
)

pytest_plugins = ()

REPO = Path(__file__).resolve().parents[2]

# The rig's plugin folder is machine-wide and untracked, living beside the main
# checkout rather than inside whichever worktree this is — the same path the
# start scripts put on LV2_PATH. Tests that need it skip when it is absent.
RIG_PLUGINS = Path.home() / "Sushi" / "plugins"

lilv = pytest.importorskip("lilv", reason="lilv is a system package (python3-lilv)")


# --- the drivability rule: pure, no LV2 install needed ---------------------


def test_plugin_with_only_control_ports_is_drivable():
    assert is_config_drivable(atom_ports=0, patch_writables=[], state_interface=False)


def test_atom_ports_make_a_plugin_not_drivable():
    """Atom ports are the usual transport for patch: messages, which Sushi
    does not support."""
    assert not is_config_drivable(
        atom_ports=2, patch_writables=[], state_interface=False
    )


def test_patch_writables_make_a_plugin_not_drivable():
    """A patch:writable property is something the plugin expects to be *set* —
    for a model loader, the model path."""
    assert not is_config_drivable(
        atom_ports=0, patch_writables=["urn:example:irfile"], state_interface=False
    )


def test_state_interface_alone_makes_a_plugin_not_drivable():
    """The case that matters, and the reason atom ports alone are not enough:
    zeroconvolv is an impulse-response convolver declaring no atom ports and no
    patch:writable, yet it is entirely driven by an IR file. Only
    state:interface reveals it. Judging by atom ports would mark an IR loader
    as fully configurable — confirmed against the real plugin."""
    assert not is_config_drivable(
        atom_ports=0, patch_writables=[], state_interface=True
    )


def test_summarise_flags_only_the_non_drivable():
    described = [
        {
            "uri": "urn:a",
            "name": "Clean",
            "class": "Reverb Plugin",
            "audio_in": 2,
            "audio_out": 2,
            "config_drivable": True,
            "parameters": [{}, {}],
        },
        {
            "uri": "urn:b",
            "name": "Loader",
            "class": "Reverb Plugin",
            "audio_in": 2,
            "audio_out": 2,
            "config_drivable": False,
            "parameters": [],
        },
    ]
    out = summarise(described).splitlines()
    assert "STATE" not in out[0]
    assert "STATE" in out[1]


def test_summarise_tolerates_a_plugin_declaring_no_class():
    """Plugin class comes from the LV2 ontology, which is only present if the
    spec bundles are on LV2_PATH. A missing class is a metadata gap, not a
    reason to crash a scan."""
    described = [
        {
            "uri": "urn:a",
            "name": "Unclassed",
            "class": None,
            "audio_in": 1,
            "audio_out": 1,
            "config_drivable": True,
            "parameters": [],
        }
    ]
    assert "Unclassed" in summarise(described)


# --- version, units and scale points: the decisions, pure -------------------


def test_version_joins_minor_and_micro():
    assert version_from_literals(["43"], ["0"]) == "43.0"


def test_version_is_omitted_unless_both_parts_are_declared():
    assert version_from_literals([], ["0"]) is None
    assert version_from_literals(["43"], []) is None


def test_version_is_omitted_when_declared_twice():
    """None of the installed plugins does this. If one did, neither value would
    be its version, and picking one would be a guess."""
    assert version_from_literals(["1", "2"], ["0"]) is None


def test_version_is_omitted_when_not_an_integer():
    assert version_from_literals(["43.0"], ["0"]) is None


def test_scale_point_values_are_floats_in_value_order():
    """Integer literals must still come out as floats. lilv's own float
    conversion refuses them, and rubberband's crispness points are integers."""
    points = scale_points_from(
        [("2", "2. Two sources"), ("0", "0. Smooth"), ("0.5", "half")]
    )
    assert points == [
        {"value": 0.0, "label": "0. Smooth"},
        {"value": 0.5, "label": "half"},
        {"value": 2.0, "label": "2. Two sources"},
    ]


def test_scale_point_with_a_non_numeric_value_is_left_out():
    assert scale_points_from([("loud", "Loud"), ("1", "One")]) == [
        {"value": 1.0, "label": "One"}
    ]


# --- version, units and scale points: against the installed plugins -----------
#
# These run on any machine with the system LV2 plugins. Each skips, rather than
# fails, when its plugin is not installed.

GXTS9 = "http://guitarix.sourceforge.net/plugins/gxts9#ts9sim"
EQ3BAND = "http://distrho.sf.net/plugins/3BandEQ"
MABITCRUSH = "http://distrho.sf.net/plugins/MaBitcrush"
COMBO = "http://drobilla.net/plugins/mda/Combo"
RUBBERBAND = "http://breakfastquay.com/rdf/lv2-rubberband#mono"
UNVERSIONED = "http://plugin.org.uk/swh-plugins/amp"


def _describe_installed(uri):
    """describe() of an installed plugin, or skip if it isn't installed.

    probe() exits when a plugin is missing, which is right for the CLI and wrong
    for a test that should skip, so this goes to lilv directly.
    """
    from sushi_rig.probe import _world, describe

    world = _world()
    plugin = world.get_all_plugins().get_by_uri(world.new_uri(uri))
    if plugin is None:
        pytest.skip(f"{uri} is not installed")
    return describe(plugin, world)


def _param(described, name):
    return next(p for p in described["parameters"] if p["name"] == name)


def test_plugin_version_is_read_from_its_own_declaration():
    assert _describe_installed(GXTS9)["version"] == "43.0"


def test_plugin_declaring_no_version_has_no_version_key():
    assert "version" not in _describe_installed(UNVERSIONED)


def test_unit_is_the_symbol_not_the_unit_uri():
    """3BandEQ declares dB on its gains and Hz on its crossovers."""
    eq = _describe_installed(EQ3BAND)
    assert _param(eq, "Low")["unit"] == "dB"
    assert _param(eq, "Low-Mid Freq")["unit"] == "Hz"


def test_integer_ranges_are_read_not_dropped():
    """3BandEQ declares its gains as `lv2:minimum -24`, `lv2:maximum 24`,
    `lv2:default 0` -- integer literals, which lilv's float() refuses. They
    used to come back None."""
    low = _param(_describe_installed(EQ3BAND), "Low")
    assert (low["min"], low["max"], low["default"]) == (-24.0, 24.0, 0.0)


def test_node_float_falls_back_to_the_literal_text():
    from sushi_rig.probe import _node_float

    class IntegerLiteral:
        """Behaves like a lilv integer node: float() refuses, str() is the text."""
        def __float__(self):
            raise TypeError("not a float literal")

        def __str__(self):
            return "-24"

    class NotANumber(IntegerLiteral):
        def __str__(self):
            return "http://example.org/not-a-number"

    assert _node_float(IntegerLiteral()) == -24.0
    assert _node_float(NotANumber()) is None
    assert _node_float(None) is None


def test_port_declaring_no_unit_has_no_unit_key():
    assert "unit" not in _param(_describe_installed(COMBO), "Model")


def test_inline_unit_is_read_from_its_blank_node():
    """MaBitcrush declares its unit inline, as a blank node carrying
    unit:symbol "bits", rather than naming a units: URI. parameter_units()
    used to raise on such a plugin, though its docstring promises it never
    does, so this is pinned against the real plugin."""
    assert _param(_describe_installed(MABITCRUSH), "resolution")["unit"] == "bits"
    assert parameter_units(MABITCRUSH) == {"resolution": "bits"}


def test_describe_and_parameter_units_agree_on_units():
    """They share one lookup, so for every parameter with a unit they must give
    the same answer."""
    uri = EQ3BAND
    described = _describe_installed(uri)
    from_describe = {
        p["name"]: p["unit"] for p in described["parameters"] if "unit" in p
    }
    assert from_describe == parameter_units(uri)
    assert parameter_units(uri)["Low-Mid Freq"] == "Hz"


def test_integer_scale_points_come_back_as_floats_in_order():
    """Rubberband's crispness declares integer-valued points, which lilv's own
    float conversion refuses. Labels are as the plugin states them."""
    crispness = _param(_describe_installed(RUBBERBAND), "Crispness")
    assert crispness["scale_points"] == [
        {"value": 0.0, "label": "0. Smooth"},
        {"value": 1.0, "label": "1. Multitimbral"},
        {"value": 2.0, "label": "2. Two sources"},
        {"value": 3.0, "label": "3. Standard"},
    ]


def test_fractional_scale_points_are_sorted_by_value():
    model = _param(_describe_installed(COMBO), "Model")
    values = [p["value"] for p in model["scale_points"]]
    assert values == sorted(values)
    assert model["scale_points"][0] == {
        "value": 0.0,
        "label": "D.I. (flat frequency response)",
    }


def test_port_without_scale_points_has_no_scale_points_key():
    assert all(
        "scale_points" not in p for p in _describe_installed(EQ3BAND)["parameters"]
    )


# --- against the real installed plugins ------------------------------------

needs_rig_plugins = pytest.mark.skipif(
    not RIG_PLUGINS.is_dir(),
    reason="the rig's plugins/ folder is not present (it is untracked)",
)


SUSHI_RIG = REPO / "tool" / ".venv" / "bin" / "sushi-rig"


def _probe_cli(*args):
    """Run probe through the CLI with LV2_PATH restricted to the rig's own
    plugin folder — the configuration that actually matters, since that
    restriction is what the portability guarantee rests on."""
    return subprocess.run(
        [str(SUSHI_RIG), "probe", *args],
        capture_output=True,
        text=True,
        env={
            "PATH": "/usr/bin:/bin",
            "LV2_PATH": str(RIG_PLUGINS),
            "HOME": str(Path.home()),
        },
    )


@needs_rig_plugins
def test_probe_reports_a_known_plugin_exactly():
    """MDA Ambience measured by hand: 4 control params, 2 in, 2 out, no atom
    ports. If probe disagrees with the plugin's own TTL, everything built on
    it is wrong."""
    from sushi_rig.probe import probe

    d = probe("http://drobilla.net/plugins/mda/Ambience")[0]
    assert len(d["parameters"]) == 4
    assert (d["audio_in"], d["audio_out"]) == (2, 2)
    assert d["atom_ports"] == 0
    assert d["config_drivable"]
    assert {p["symbol"] for p in d["parameters"]} == {
        "size",
        "hf_damp",
        "mix",
        "output",
    }


@needs_rig_plugins
def test_probe_reads_real_world_ranges_not_normalised_ones():
    """The whole point: Sushi's config shows a normalised 0.0-1.0 value, but
    the plugin declares the real domain. GxTubeScreamer's Tone is 100-1000 Hz,
    and that fact is invisible anywhere downstream of Sushi."""
    from sushi_rig.probe import probe

    d = probe("http://guitarix.sourceforge.net/plugins/gxts9#ts9sim")[0]
    # Matched on name, not symbol: this plugin's symbol for Tone is
    # "fslider1_". That gap between symbol and human name is the same one
    # that makes Sushi's parameter naming unguessable, and the reason probe
    # reports both.
    tone = next(p for p in d["parameters"] if p["name"] == "Tone")
    assert tone["symbol"] != "Tone"
    assert (tone["min"], tone["max"]) == (100.0, 1000.0)


@needs_rig_plugins
def test_every_plugin_in_the_shipped_rig_is_config_drivable():
    """A plugin that keeps state outside its control ports cannot be captured
    into a config, so the save/restore loop would silently lose part of the
    sound. This is the invariant the rig's plugin set has to hold."""
    from sushi_rig.probe import probe

    rig = json.loads((REPO / "config" / "electric_board.json").read_text())
    uris = [p["uri"] for t in rig["tracks"] for p in t["plugins"]]
    assert uris, "electric_board declares no plugins"
    for uri in uris:
        assert probe(uri)[0]["config_drivable"], f"{uri} is not config-drivable"


@needs_rig_plugins
def test_probe_cli_lists_only_what_the_rig_can_load():
    """With LV2_PATH restricted to plugins/, probe must not see the archived
    bundles — that restriction is what makes the rig portable."""
    result = _probe_cli()
    assert result.returncode == 0
    assert "lsp-plug.in" not in result.stdout
    assert "mda/Ambience" in result.stdout


@needs_rig_plugins
def test_probe_cli_errors_helpfully_on_an_invisible_plugin():
    """An LSP plugin is still installed system-wide but deliberately off the
    rig's path. The error should point at LV2_PATH rather than claim the
    plugin doesn't exist."""
    result = _probe_cli("http://lsp-plug.in/plugins/lv2/compressor_mono")
    assert result.returncode != 0
    assert "LV2_PATH" in result.stderr
