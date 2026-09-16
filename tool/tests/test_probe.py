import json
import subprocess
from pathlib import Path

import pytest

from sushi_rig.probe import is_config_drivable, summarise

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
