"""The octave pedal (#164): Rubber Band 4.0's Live shifter, built from a pinned
source (`scripts/build-rubberband4.sh`) and loaded under Sushi.

What CI's container checks here, rendering the plugin alone on a stereo track
fed as the compiler feeds a mono channel, through the real Sushi 1.3.0 and the
rig's own plugins/ (`LV2_PATH` set to it alone):

- plugins/'s `rubberband.lv2` is 4.0.0's, from the pinned commit, and every
  Rubber Band URI the manifest lists resolves to it, not to Ubuntu's 3.3.0;
- an octave up, and down, is in tune on five notes across the neck, finite
  throughout, and up leaves almost nothing of the note it was played at;
- how late the octave comes out, and that the plugin's own Wet-Dry Mix
  delays its dry signal too, which is why `config/rigs/electric-octave.yaml`
  blends with a second channel rather than with that knob.

No test here reads `config/rigs/` (the #133 rule): the renders build their own
one-plugin config. The measurements go into a table printed at the end of the
run (`octave_table`), since a passing test shows its numbers nowhere else.
"""

from __future__ import annotations

import importlib.util
import json
import re
import subprocess
from pathlib import Path

import pytest

from sushi_rig.paths import checkout_root
from sushi_rig.plugins import read_manifest
from sushi_rig.profile import dump_profile, load_profile, validate

import octave_table

ROOT = checkout_root()
SUSHI = ROOT / "sushi"
PLUGINS = ROOT / "plugins"
HERE = Path(__file__).parent
PROFILE = HERE.parent / "examples" / "profiles" / "rubberband-live.yaml"
BUILD_SCRIPT = ROOT / "scripts" / "build-rubberband4.sh"

LIVE = "http://breakfastquay.com/rdf/lv2-rubberband#livemono"
RUBBER_BAND = "http://breakfastquay.com/rdf/lv2-rubberband#"
SR = 48000

# Sushi normalises every parameter linearly over its range: Octaves runs -2 to
# 2, so +1 is 0.75 and -1 is 0.25; Semitones and Cents at 0.5 are no shift.
UP = {"Octaves": 0.75, "Semitones": 0.5, "Cents": 0.5, "Wet-Dry Mix": 0.0}
DOWN = {**UP, "Octaves": 0.25}

# E2, A2, G3, E4, A4: the low E string to the top of a guitar's usual range.
NOTES = (82.41, 110.0, 196.0, 329.63, 440.0)

# In tune: within 5 cents of the octave once the shifter has settled. #153
# measured 0 cents on every note offline; 5 is a tenth of a semitone, a
# player's "in tune", and well clear of a plugin that is not shifting at all
# (1200 cents out).
IN_TUNE_CENTS = 5.0
# What may be left of the note itself, an octave up, below the octave.
LEFT_OF_THE_NOTE_DB = -30.0
# The octave's lag. #153 measured 3,203 samples (67 ms) offline at 48 kHz;
# electric-octave-up's gx_detune_ was 65 ms. Longer than this is a change worth
# failing on: a player feels it.
MOST_LAG_S = 0.075

needs_sushi = pytest.mark.skipif(
    not SUSHI.exists() or not PLUGINS.is_dir(),
    reason="needs the Sushi binary and the rig's plugins",
)
needs_lilv = pytest.mark.skipif(
    importlib.util.find_spec("lilv") is None or not PLUGINS.is_dir(),
    reason="needs lilv (python3-lilv) and the rig's plugins/",
)


def _record(request, what: str, value: str) -> None:
    octave_table.rows(request.config).append((what, value))


# --- measuring (pure) -------------------------------------------------------


def peak_hz(np, signal, lo: float, hi: float) -> float:
    """The strongest frequency between `lo` and `hi`, to a fraction of a bin:
    a Hann window, a long zero-padded FFT, and a parabola through the log
    magnitudes either side of the peak."""
    n = 1 << 20
    spectrum = np.abs(np.fft.rfft(signal * np.hanning(len(signal)), n))
    freqs = np.fft.rfftfreq(n, 1 / SR)
    band = np.flatnonzero((freqs > lo) & (freqs < hi))
    k = band[np.argmax(spectrum[band])]
    a, b, c = np.log(spectrum[k - 1:k + 2] + 1e-30)
    return (k + 0.5 * (a - c) / (a - 2 * b + c)) * SR / n


def level_at_db(np, signal, hz: float) -> float:
    """The signal's level at `hz`, in dB of an arbitrary reference: compare
    two of these from the same signal, never one alone."""
    t = np.arange(len(signal)) / SR
    w = np.hanning(len(signal))
    return 20 * np.log10(abs(np.sum(signal * w * np.exp(-2j * np.pi * hz * t))) + 1e-30)


def onset(np, signal, start: int, below_peak_db: float = -20.0) -> int:
    """Samples from `start` until the signal first reaches `below_peak_db` of
    its own peak: #153's burst onset."""
    env = np.abs(signal)
    return int(np.argmax(env > env.max() * 10 ** (below_peak_db / 20))) - start


def cents(measured: float, expected: float) -> float:
    import math

    return 1200 * math.log2(measured / expected)


def test_the_measures_find_a_known_pitch_and_onset():
    np = pytest.importorskip("numpy")
    t = np.arange(SR) / SR
    tone = np.sin(2 * np.pi * 220.5 * t) + 0.01 * np.sin(2 * np.pi * 110.25 * t)
    assert abs(cents(peak_hz(np, tone, 150, 300), 220.5)) < 0.1
    assert level_at_db(np, tone, 110.25) - level_at_db(np, tone, 220.5) == pytest.approx(-40, abs=0.5)
    burst = np.zeros(SR)
    burst[1000 + 300:] = np.sin(2 * np.pi * 500 * t[: SR - 1300])
    assert onset(np, burst, 1000) == pytest.approx(300, abs=2)


# --- the profile (pure) -----------------------------------------------------


def test_the_profile_is_clean_against_the_plugin_and_canonical():
    facts = json.loads((HERE / "fixtures" / "probe_rubberband-live.json").read_text())
    assert facts["uri"] == LIVE
    assert validate(load_profile(PROFILE), facts) == []
    assert dump_profile(load_profile(PROFILE)) == PROFILE.read_text()


def test_the_profile_puts_the_octave_first_and_says_what_its_mix_costs():
    params = {p["name"]: p for p in load_profile(PROFILE)["parameters"]}
    primary = [n for n, p in params.items() if p["visibility"]["value"] == "primary"]
    assert primary == ["Octaves"]
    assert params["Octaves"]["phone"]["value"] == 1
    mix = params["Wet-Dry Mix"]
    assert mix["visibility"]["value"] == "advanced"
    assert "late" in mix["description"]["value"], "the knob's catch is said where it is shown"


# --- the build and plugins/ -------------------------------------------------


def pinned_commit() -> str:
    found = re.search(r"^COMMIT=([0-9a-f]{40})$", BUILD_SCRIPT.read_text(), re.M)
    assert found, f"{BUILD_SCRIPT} pins no commit"
    return found.group(1)


def test_the_manifest_lists_the_live_shifter():
    assert LIVE in read_manifest((ROOT / "plugin-manifest.txt").read_text())


BUILD_IT = (f"run scripts/build-rubberband4.sh {PLUGINS} "
            "(docs/reference/plugins.md, Rubber Band 4.0, built for the octave)")


@needs_lilv
@pytest.mark.slow
def test_plugins_holds_rubber_band_4_and_only_it():
    """Ubuntu's rubberband-lv2 3.3.0 declares the same URIs under a bundle of
    the same name. plugins/ must hold 4.0.0's, the pinned build, for every
    one of them: one Rubber Band, not a mixture.

    Slow, as `test_stereo.test_the_port_table_is_what_lilv_reads` is: both
    ask what this machine's plugins/ holds, not what the code does. A
    checkout that has pulled #164 and not yet built Rubber Band (the rig, the
    moment it pulls) keeps a green fast suite, and this fails there, saying
    what to run, wherever the slow tests are: the container job runs them
    all, and the rig's step after the build runs this file. It skips only
    where there is no plugins/ at all, as test_probe and test_library do."""
    from sushi_rig.plugins import plugins_on

    found = plugins_on(str(PLUGINS))
    ours = [u for u in read_manifest((ROOT / "plugin-manifest.txt").read_text()) if u.startswith(RUBBER_BAND)]
    assert LIVE in ours
    missing = [u for u in ours if u not in found]
    assert not missing, f"{PLUGINS} has no {', '.join(missing)}: its Rubber Band is not 4.0.0; {BUILD_IT}"
    bundles = {found[u] for u in ours}
    assert bundles == {PLUGINS / "rubberband.lv2"}, f"Rubber Band's URIs come from {sorted(map(str, bundles))}; {BUILD_IT}"
    stamp = PLUGINS / "rubberband.lv2" / "BUILT-FROM"
    built = stamp.read_text() if stamp.is_file() else ""
    assert f"Rubber Band v4.0.0, commit {pinned_commit()}" in built, \
        f"{PLUGINS / 'rubberband.lv2'} is not the pinned build; {BUILD_IT}"


# --- under Sushi ------------------------------------------------------------


def _render(np, tmp_path, name: str, params: dict, mono) -> "np.ndarray":
    """`mono` through the Live shifter alone, as Sushi renders it offline."""
    from sushi_rig import signals
    from sushi_rig.analysis import check_finite
    from sushi_rig.render import sushi_argv
    from sushi_rig.verify import sushi_env

    work = tmp_path / name
    work.mkdir()
    config = {
        "host_config": {"samplerate": SR},
        "tracks": [{
            "name": "channel", "channels": 2,
            "inputs": [{"engine_channel": 0, "track_channel": 0}, {"engine_channel": 0, "track_channel": 1}],
            "outputs": [{"engine_bus": 0, "track_bus": 0}],
            "plugins": [{"name": "octave", "type": "lv2", "uri": LIVE}],
        }],
        "initial_state": [{"processor": "octave", "parameters": params}],
    }
    (work / "config.json").write_text(json.dumps(config))
    signals.write_wav(work / "in.wav", np.stack([mono, mono], axis=1), SR, subtype="float32")
    result = subprocess.run(
        sushi_argv(work / "config.json", work / "in.wav", work / "out.wav", str(SUSHI)),
        cwd=ROOT, env=sushi_env(ROOT), capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, \
        f"{(result.stdout + result.stderr).strip()}\n(if plugins/ has no Live shifter: {BUILD_IT})"
    out = signals.read_wav(work / "out.wav").samples
    assert check_finite(out) is None, f"{name}: {check_finite(out)}"
    return out[:, 0]


def _note(np, f0: float):
    """Half a second of silence, two seconds of a plucked-string-like tone
    (five harmonics at 1/h), half a second of silence."""
    t = np.arange(2 * SR) / SR
    tone = sum((0.3 / h) * np.sin(2 * np.pi * f0 * h * t) for h in range(1, 6))
    return np.concatenate([np.zeros(SR // 2), tone, np.zeros(SR // 2)])


def _settled(np, y):
    """A second of the output, from 0.75 s after the note began: past the
    shifter's lag (about 70 ms) and its attack."""
    start = SR // 2 + 3 * SR // 4
    return y[start:start + SR]


@needs_sushi
@pytest.mark.slow
@pytest.mark.parametrize("f0", NOTES)
def test_an_octave_up_is_in_tune_and_leaves_little_of_the_note(f0, tmp_path, request):
    np = pytest.importorskip("numpy")
    y = _settled(np, _render(np, tmp_path, f"up-{f0:g}", UP, _note(np, f0)))
    error = cents(peak_hz(np, y, 1.5 * f0, 2.5 * f0), 2 * f0)
    left = level_at_db(np, y, f0) - level_at_db(np, y, 2 * f0)
    _record(request, f"up, {f0:g} Hz", f"{error:+.2f} cents from {2 * f0:g} Hz; the note itself {left:.1f} dB below")
    assert abs(error) < IN_TUNE_CENTS
    assert left < LEFT_OF_THE_NOTE_DB


@needs_sushi
@pytest.mark.slow
@pytest.mark.parametrize("f0", NOTES[1:])
def test_an_octave_down_is_in_tune(f0, tmp_path, request):
    np = pytest.importorskip("numpy")
    y = _settled(np, _render(np, tmp_path, f"down-{f0:g}", DOWN, _note(np, f0)))
    error = cents(peak_hz(np, y, 0.35 * f0, 0.65 * f0), f0 / 2)
    _record(request, f"down, {f0:g} Hz", f"{error:+.2f} cents from {f0 / 2:g} Hz")
    assert abs(error) < IN_TUNE_CENTS


def _burst(np):
    """A 500 Hz burst at 0.3, half a second in: #153's latency stimulus."""
    x = np.zeros(2 * SR)
    t = np.arange(SR // 2) / SR
    x[SR // 2:SR] = 0.3 * np.sin(2 * np.pi * 500 * t)
    return x


@needs_sushi
@pytest.mark.slow
def test_the_octave_comes_out_about_67_ms_late(tmp_path, request):
    np = pytest.importorskip("numpy")
    lag = onset(np, _render(np, tmp_path, "lag", UP, _burst(np)), SR // 2)
    _record(request, "octave up's lag (500 Hz burst)", f"{lag} samples, {1000 * lag / SR:.1f} ms")
    assert 0 < lag <= MOST_LAG_S * SR


@needs_sushi
@pytest.mark.slow
def test_its_own_mix_delays_the_dry_signal_too(tmp_path, request):
    """Wet-Dry Mix at 1 is the dry alone, and it comes out late: the plugin
    delays its dry to line up with the shifted signal (its source,
    RubberBandLivePitchShifter.cpp, reads the dry from a delay line of the
    shifter's start delay). So blending there makes the whole guitar late,
    and the rig blends with a channel of its own. If this ever fails, that
    choice can be revisited."""
    np = pytest.importorskip("numpy")
    late = onset(np, _render(np, tmp_path, "dry", {**UP, "Wet-Dry Mix": 1.0}, _burst(np)), SR // 2)
    _record(request, "its own Mix at 1 (the dry alone)", f"{late} samples, {1000 * late / SR:.1f} ms")
    assert late > 0.02 * SR
