"""The synth lead (#177): a compressor, GxBigMuffPi, Calf's Envelope Filter and
GxEchoCat (whose profile and tests are the pedals', #165, test_pedals.py).

The fast half needs nothing installed: GxBigMuffPi and Calf's Envelope Filter
are in the inventory with the ports lilv reads; both profiles are clean
against their probes, frozen in `fixtures/`; and the filter's gains, linear to
x64, are hidden behind bounded controls.

The slow half, under Sushi: every setting the synth lead gives the filter,
its Mode included, is an ordinary control port, and Sushi applies them. The fuzz's Volume is a level from silence up. The rig
itself is not read (the #133 rule): the chains are written out below.
"""

from __future__ import annotations

import pytest

from sushi_rig import stereo
from sushi_rig.plugins import read_manifest
from sushi_rig.profile import dump_profile, load_profile, validate

from synth_renders import HERE, PLUGINS, ROOT, SUSHI, compiled, normalised, padded, probe, render, rms_db, string

PROFILES = HERE.parent / "examples" / "profiles"
FUZZ = "http://guitarix.sourceforge.net/plugins/gx_bmp_#_bmp_"
FILTER = "http://calf.sourceforge.net/plugins/EnvelopeFilter"
COMPRESSOR = "http://guitarix.sourceforge.net/plugins/gx_compressor#_compressor"
ECHO = "http://guitarix.sourceforge.net/plugins/gxechocat#echocat"

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


def test_the_filter_is_drivable_and_its_profile_is_clean_and_canonical():
    """Probe used to call it not drivable for its two atom ports; both are
    lv2:connectionOptional, which no longer counts (probe.is_config_drivable,
    review of PR #186), and Sushi sets its every control (below). Its one
    warning is its version, which lilv does not read (the pin is the
    package's)."""
    facts = probe("calf-envelope-filter")
    assert facts["uri"] == FILTER
    assert (facts["config_drivable"], facts["atom_ports"], facts["optional_atom_ports"]) == (True, 2, 2)
    path = PROFILES / "calf-envelope-filter.yaml"
    prof = load_profile(path)
    assert [f.code for f in validate(prof, facts)] == ["unverified-version"]
    assert dump_profile(prof) == path.read_text()


def test_the_filters_gains_are_hidden_and_its_level_is_bounded():
    """Input and Output Gain run linear from 1/64 to 64: the synth lead's 0.4
    is 0.6 % of the knob and 10 % of it is +24 dB (review of PR #186). Both
    are hidden; Level drives Output Gain from 0.05 to 0.55, where the lead
    is about as loud as the guitar (the slow test below holds it there; the
    plugin's own 2 measured +14.8 dB on #153's guitar, 0.55 +4.3, as #173
    keeps the echo's top near the guitar's level). Activation and Resonance
    are linear too, and are bounded the same way."""
    prof = load_profile(PROFILES / "calf-envelope-filter.yaml")
    hidden = {p["name"] for p in prof["parameters"] if p["visibility"]["value"] == "hidden"}
    assert {"Input Gain", "Output Gain", "Activation", "Resonance", "Bypass", "Sidechain"} == hidden
    drives = {c["id"]: [(d["parameter"], d["curve"], d["at_0"], d["at_1"]) for d in c["drives"]["value"]]
              for c in prof["controls"]}
    assert drives == {"sensitivity": [("Activation", "cosine", 0.5, 32.0)],
                      "resonance": [("Resonance", "cosine", 0.708, 12.0)],
                      "level": [("Output Gain", "linear", 0.05, 0.55)]}
    default = next(p["facts"]["default"] for p in prof["parameters"] if p["name"] == "Output Gain")
    assert default == 2.0 > 0.55, "added from the library it is past Level's top, as the shimmer is past Mix's"


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
    # Mode: the synth lead's 24 dB low-pass against the plugin's own band-pass,
    # on a low E, where the two differ most (measured on the whole lead: 25
    # dB apart).
    low_e = padded(np, string(np, 82.41))
    lowpass = render(np, tmp_path, "lowpass", compiled(_filter(Mode=1.0)), low_e)[:, 0]
    bandpass = render(np, tmp_path, "bandpass", compiled(_filter(Mode=7.0)), low_e)[:, 0]
    assert rms_db(np, lowpass) - rms_db(np, bandpass) > 10, "Mode did nothing"


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


def _lead() -> list[dict]:
    """#166's synth lead with GxBigMuffPi for its fuzz, at the settings the
    guide describes: the compressor at ratio 4 and -30 dB, the fuzz at 0.8,
    0.4 and 0.4, the filter a 24 dB low-pass swept from 300 Hz to 2.5 kHz,
    Activation 8, Resonance 4, Output Gain 0.4, and the pedals rig's tape
    echo (head 1 at 120 BPM, Mix 50 %, Repeats and Level 100 %)."""
    compressor = {"name": "compressor", "uri": COMPRESSOR, "parameters": {
        "RATIO": 0.157895, "KNEE": 0.15, "THRESHOLD": 0.622642, "RELEASE": 0.03, "ATTACK": 0.005, "BYPASS": 1.0}}
    echo = {"name": "echo", "uri": ECHO, "parameters": {
        "Input": 0.25, "Swell": 0.35, "Sustain": 0.3, "Output": 0.2925, "BPM": 0.285714,
        "Head 1": 1.0, "Head 2": 0.0, "Head 3": 0.0}}
    return [compressor, *_fuzz(0.4), *_filter(Upper=2500.0, Resonance=4.0, Mode=1, **{"Output Gain": 0.4}), echo]


@needs_sushi
@pytest.mark.slow
def test_the_lead_is_about_as_loud_as_the_guitar_and_echoes(tmp_path):
    """The chain whole, through the master: finite, its two sides the same
    (everything in it is mono), within 6 dB of the note that went in
    (measured 2026-11-18: -4.4 dB on this G3, -5.9 on a low E and +0.1 on a
    high E, +1.6 on #153's guitar), well under the master's limiter, and
    still sounding after the note, which is the echo. #166's band-pass, which
    this low-pass replaced, had the G3 18 dB down and a low E 25."""
    np = pytest.importorskip("numpy")
    x = padded(np, string(np, 196.0, seconds=1.0), after=2.0)
    y = render(np, tmp_path, "lead", compiled(_lead()), x)
    assert np.max(np.abs(y[:, 0] - y[:, 1])) < 1e-6
    assert abs(rms_db(np, y[:, 0]) - rms_db(np, x)) < 6.0, f"{rms_db(np, y[:, 0]) - rms_db(np, x):+.1f} dB"
    assert 20 * np.log10(np.max(np.abs(y))) < -3.0
    after = slice(int(1.6 * 48000), int(2.4 * 48000))  # from 0.1 s after the note's end
    assert rms_db(np, y[after, 0]) > rms_db(np, x[after]) + 40, "nothing after the note: no echo"


@needs_sushi
@pytest.mark.slow
def test_the_filters_level_at_100_is_no_louder_than_the_guitar(tmp_path):
    """Level's top, worked out from the profile's own curve, on eight notes
    from the low E to the top E through the whole lead: no louder than the
    notes that went in (measured 2026-11-19: -0.5 dB at 0.55, +0.2 at 0.6,
    +1.5 at 0.7, +9.7 at the plugin's own 2), so a Level widened past about
    0.6 fails here (review of PR #186)."""
    from sushi_rig.profile import driven_value

    np = pytest.importorskip("numpy")
    prof = load_profile(PROFILES / "calf-envelope-filter.yaml")
    (level,) = [c for c in prof["controls"] if c["id"] == "level"]
    (drive,) = level["drives"]["value"]
    top = driven_value(drive, 1.0)
    chain = _lead()
    (filter_,) = [e for e in chain if e["name"] == "filter"]
    filter_["parameters"]["Output Gain"] = normalised(probe("calf-envelope-filter"), {"Output Gain": top})["Output Gain"]
    notes = np.concatenate([padded(np, string(np, f, seconds=0.8), before=0.2, after=0.2)
                            for f in (82.41, 110.0, 146.83, 196.0, 246.94, 329.63, 440.0, 659.26)])
    y = render(np, tmp_path, "top", compiled(chain), notes)
    over = rms_db(np, y[:, 0]) - rms_db(np, notes)
    assert over <= 0.0, f"Level at 100 % (Output Gain {top}) is {over:+.1f} dB over the guitar"
