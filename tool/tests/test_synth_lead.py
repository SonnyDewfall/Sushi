"""The synth lead (#177): a compressor, GxBigMuffPi, Calf's Envelope Filter and
GxEchoCat (whose profile and tests are the pedals', #165, test_pedals.py).

The fast half needs nothing installed: GxBigMuffPi and Calf's Envelope Filter
are in the inventory with the ports lilv reads; GxBigMuffPi's profile is clean
against its probe; and Calf's Envelope Filter has no profile, because probe,
frozen in `fixtures/probe_calf-envelope-filter.json`, calls it not drivable
from a config (two atom ports) and the validator then refuses any control.

The slow half, under Sushi, shows probe's call is cautious here: every
setting the synth lead gives the filter is an ordinary control port, and
Sushi applies them. The fuzz's Volume is a level from silence up. The rig
itself is not read (the #133 rule): the chains are written out below.
"""

from __future__ import annotations

import pytest

from sushi_rig import stereo
from sushi_rig.library import load_profiles
from sushi_rig.plugins import read_manifest
from sushi_rig.profile import dump_profile, load_profile, validate

from synth_renders import HERE, PLUGINS, ROOT, SUSHI, compiled, normalised, padded, probe, render, rms_db, string

PROFILES = HERE.parent / "examples" / "profiles"
FUZZ = "http://guitarix.sourceforge.net/plugins/gx_bmp_#_bmp_"
FILTER = "http://calf.sourceforge.net/plugins/EnvelopeFilter"

needs_sushi = pytest.mark.skipif(not SUSHI.exists() or not PLUGINS.is_dir(),
                                 reason="needs the Sushi binary and the rig's plugins")


@pytest.mark.parametrize("uri, ports", [(FUZZ, (1, 1)), (FILTER, (4, 2))], ids=["fuzz", "filter"])
def test_each_plugin_is_in_the_manifest_and_the_port_table(uri, ports):
    """Calf's four inputs are its two sides and two side-chain inputs, all
    optional; the compiler's two-channel track feeds the first two."""
    assert uri in read_manifest((ROOT / "plugin-manifest.txt").read_text())
    found = stereo.load_ports(ROOT)[uri]
    assert (found.inputs, found.outputs) == ports


def test_the_fuzzs_profile_is_clean_canonical_and_shows_its_three_knobs():
    path = PROFILES / "gxbigmuffpi.yaml"
    prof = load_profile(path)
    assert validate(prof, probe("gxbigmuffpi")) == []
    assert dump_profile(prof) == path.read_text()
    assert [p["label"]["value"] for p in prof["parameters"] if p["visibility"]["value"] == "primary"] == \
        ["Sustain", "Tone", "Volume"]


def test_the_filter_has_no_profile_because_probe_calls_it_not_drivable():
    """The validator refuses any parameter or control for a plugin probe says
    a config cannot drive, so a profile of it could only hide its knobs. The
    rack shows every one of them instead. Should probe's rule change, this is
    the test that says a profile can be written."""
    facts = probe("calf-envelope-filter")
    assert facts["uri"] == FILTER
    assert (facts["config_drivable"], facts["atom_ports"], facts["patch_writables"], facts["state_interface"]) == \
        (False, 2, [], False)
    profiles, problems = load_profiles(PROFILES)
    assert problems == [] and FILTER not in profiles


# --- under Sushi --------------------------------------------------------------

def _filter(**real) -> list[dict]:
    values = normalised(probe("calf-envelope-filter"),
                        {"Activation": 8.0, "Resonance": 6.0, "Lower": 300.0, "Mix": 1.0, **real})
    return [{"name": "filter", "uri": FILTER, "parameters": values}]


@needs_sushi
@pytest.mark.slow
def test_sushi_sets_the_filters_controls(tmp_path):
    """Each of the synth lead's settings is a control port, and Sushi applies
    them: the same note through a sweep that tops out at 500 Hz and one that
    tops out at 8 kHz differ by far more than nothing, and Mix 0 is the note
    as it went in."""
    np = pytest.importorskip("numpy")
    x = padded(np, string(np, 220.0))
    low = render(np, tmp_path, "low", compiled(_filter(Upper=500.0)), x)[:, 0]
    high = render(np, tmp_path, "high", compiled(_filter(Upper=8000.0, Lower=4000.0)), x)[:, 0]
    assert abs(rms_db(np, low) - rms_db(np, high)) > 6, "Upper and Lower did nothing"
    dry = render(np, tmp_path, "dry", compiled(_filter(Mix=0.0, **{"Output Gain": 1.0})), x)[:, 0]
    assert rms_db(np, dry - x) < rms_db(np, x) - 60, "Mix 0 is not the dry note"


def _fuzz(volume: float) -> list[dict]:
    # GxBigMuffPi's three run 0 to 1, so their normalised values are their own.
    return [{"name": "fuzz", "uri": FUZZ, "parameters": {"SUSTAIN": 0.8, "TONE": 0.4, "VOLUME": volume}}]


@needs_sushi
@pytest.mark.slow
def test_the_fuzzs_volume_runs_from_silence(tmp_path):
    np = pytest.importorskip("numpy")
    x = padded(np, string(np, 110.0))
    silent = render(np, tmp_path, "silent", compiled(_fuzz(0.0)), x)
    half = render(np, tmp_path, "half", compiled(_fuzz(0.5)), x)
    assert np.max(np.abs(silent)) < 1e-6
    assert rms_db(np, half) > rms_db(np, x) - 6, "Volume 0.5 is far quieter than the guitar"
