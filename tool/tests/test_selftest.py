"""What a selftest probe means, against signals whose answer is known.

The decisions in `selftest` are pure, so almost all of this builds a response by
construction — a copy 6 dB down, a copy delayed by exactly 257 samples, a copy
through a nonlinearity whose harmonics have a closed form — and checks the
finding that comes out.

The one exception is at the end: two tests render a passthrough through a real
Sushi and run the decisions over what comes back. They are what stops this from
being a suite that proves its own arithmetic. A passthrough is the right thing
to render, because "passes audio, adds nothing" is the exact shape of the amp
failure the module exists for.

The closed forms for `squared` and `cubed` are derived in test_analysis.py,
where the measurement itself is tested.
"""

from __future__ import annotations

import json
import math

import pytest

np = pytest.importorskip("numpy")

from sushi_rig import selftest, signals  # noqa: E402
from sushi_rig.paths import checkout_root  # noqa: E402

SR = 48000
ROOT = checkout_root()
SUSHI = ROOT / "sushi"

# Sushi's own internal passthrough and nothing else, so this needs none of the
# rig's LV2 plugins and renders wherever the binary does. Written by the test
# rather than taken from config/: a fixture for "unity gain, no harmonics"
# should not be something a later tone change can quietly edit.
PASSTHROUGH_CONFIG = {
    "host_config": {"samplerate": SR},
    "tracks": [
        {
            "name": "main",
            "channels": 2,
            "inputs": [{"engine_bus": 0, "track_bus": 0}],
            "outputs": [{"engine_bus": 0, "track_bus": 0}],
            "plugins": [
                {
                    "uid": "sushi.testing.passthrough",
                    "name": "passthrough",
                    "type": "internal",
                }
            ],
        }
    ],
}

needs_sushi = pytest.mark.skipif(
    not SUSHI.exists(), reason="needs the Sushi binary"
)


def sine(frequency_hz=220.0, level_dbfs=-6.0, duration_s=1.0):
    return signals.sine(SR, duration_s, frequency_hz, level_dbfs=level_dbfs)[:, 0]


def squared(x, a):
    """x + a*x**2: THD is a*A/2 for a sine of amplitude A."""
    return x + a * x ** 2


def probe(injected, returned, name="amp"):
    return selftest.Probe(
        name=name, injected=injected, returned=returned, sample_rate=SR
    )


def checks(findings):
    return [f.check for f in findings]


# --------------------------------------------------------------------------- #
# Levels
# --------------------------------------------------------------------------- #

def test_levels_report_both_sides_and_the_gain_between_them():
    injected = sine()
    levels = selftest.measure_levels(probe(injected, injected * 10 ** (-6 / 20)))
    assert levels.injected_peak_dbfs == pytest.approx(-6.0, abs=0.01)
    assert levels.injected_rms_dbfs == pytest.approx(-9.01, abs=0.01)
    assert levels.gain_db == pytest.approx(-6.0, abs=0.01)
    assert selftest.level_findings(levels) == []


def test_an_injection_that_never_arrived_is_reported_as_the_tests_own_problem():
    """The worst thing this could do is call a healthy rig silent because the
    probe was wired wrong."""
    levels = selftest.measure_levels(probe(signals.silence(SR, 1.0)[:, 0], sine()))
    findings = selftest.level_findings(levels)
    assert checks(findings) == ["nothing-injected"]
    assert "says nothing about the rig" in findings[0].message


def test_a_component_passing_nothing_is_found():
    levels = selftest.measure_levels(probe(sine(), signals.silence(SR, 1.0)[:, 0]))
    assert checks(selftest.level_findings(levels)) == ["silent"]
    assert levels.gain_db == float("-inf")


def test_a_probe_that_injected_nothing_and_returned_nothing_names_the_cause_only():
    """Both are true and only one is worth acting on; reporting both would bury
    which."""
    silent = signals.silence(SR, 1.0)[:, 0]
    levels = selftest.measure_levels(probe(silent, silent))
    assert checks(selftest.level_findings(levels)) == ["nothing-injected"]


def test_a_quiet_but_working_component_is_not_called_silent():
    """A chain is allowed to have 40 dB of loss in it."""
    injected = sine()
    levels = selftest.measure_levels(probe(injected, injected * 10 ** (-40 / 20)))
    assert selftest.level_findings(levels) == []


# --------------------------------------------------------------------------- #
# Delay
# --------------------------------------------------------------------------- #

def delayed(x, samples):
    out = np.zeros_like(x)
    out[samples:] = x[:len(x) - samples]
    return out


def test_a_known_delay_is_recovered_in_samples_and_milliseconds():
    stimulus = signals.pink_noise(SR, 1.0, seed=3)[:, 0]
    delay = selftest.measure_delay(probe(stimulus, delayed(stimulus, 257)))
    assert delay.samples == 257
    assert delay.ms == pytest.approx(5.354, abs=0.001)


def test_one_block_of_delay_is_what_the_quantum_asked_for():
    """Port to port through Sushi measured 257 samples at a quantum of 256."""
    delay = selftest.Delay(name="sushi", sample_rate=SR, samples=257)
    assert selftest.delay_findings(delay, expected_quantum=256) == []


def test_the_desktops_two_thousand_sample_quantum_is_found():
    """The failure this exists for: a fresh machine inherited 2048 and ran at
    42.69 ms with nothing saying so."""
    delay = selftest.Delay(name="sushi", sample_rate=SR, samples=2049)
    findings = selftest.delay_findings(delay, expected_quantum=256)
    assert checks(findings) == ["quantum-not-as-asked"]
    assert "8.0 blocks" in findings[0].message
    assert "restarting the rig" in findings[0].message, "the quantum is sticky"


def test_no_quantum_to_compare_against_leaves_the_delay_merely_reported():
    """An offline render has an internal block size of its own and is not in a
    PipeWire graph at all."""
    delay = selftest.Delay(name="sushi", sample_rate=SR, samples=2049)
    assert selftest.delay_findings(delay, expected_quantum=None) == []


def test_a_silent_response_has_no_delay_rather_than_a_delay_of_zero():
    stimulus = signals.pink_noise(SR, 1.0, seed=4)[:, 0]
    delay = selftest.measure_delay(probe(stimulus, signals.silence(SR, 1.0)[:, 0]))
    assert delay.samples is None
    assert delay.ms is None
    assert checks(selftest.delay_findings(delay, expected_quantum=256)) == ["no-correlation"]


def test_a_quantum_that_is_not_a_block_size_is_refused():
    delay = selftest.Delay(name="sushi", sample_rate=SR, samples=257)
    with pytest.raises(ValueError, match="positive block size"):
        selftest.delay_findings(delay, expected_quantum=0)


# --------------------------------------------------------------------------- #
# Nonlinearity
# --------------------------------------------------------------------------- #

def test_an_amp_adding_no_harmonics_is_found_however_healthy_it_looks():
    """The headline failure: NAM with no model loaded passes audio, is louder
    than its input, and adds nothing."""
    injected = sine()
    nonlinearity = selftest.measure_nonlinearity(
        probe(injected, injected * 10 ** (5.19 / 20))
    )
    findings = selftest.nonlinearity_findings(nonlinearity)
    assert checks(findings) == ["linear"]
    assert "no model loaded" in findings[0].message


def test_an_amp_that_is_modelling_something_passes():
    injected = sine()
    nonlinearity = selftest.measure_nonlinearity(probe(injected, squared(injected, 0.16)))
    assert nonlinearity.returned.thd_percent == pytest.approx(4.0, rel=0.01)
    assert selftest.nonlinearity_findings(nonlinearity) == []


def test_the_nam_measurement_from_the_session_reads_as_an_amp_that_is_working():
    """The one pair of real figures there is, so the thresholds are pinned
    against it rather than against something invented: a 220 Hz sine measured
    -23.01 dBFS RMS and 0.00 % going into NAM, -17.82 dBFS and 4.06 % coming
    out (2026-10-26, MANIFEST.md).
    """
    amplitude = 10 ** (-23.01 / 20) * math.sqrt(2)
    gain = 10 ** ((-17.82 + 23.01) / 20)
    injected = signals.sine(SR, 1.0, 220.0, level_dbfs=20 * math.log10(amplitude))[:, 0]
    # a chosen so that a*(gain*amplitude)/2 is 4.06 %.
    nonlinearity = selftest.measure_nonlinearity(
        probe(injected, squared(injected * gain, 0.0812 / (gain * amplitude)))
    )
    assert nonlinearity.injected.fundamental_dbfs == pytest.approx(-23.01, abs=0.01)
    assert nonlinearity.injected.thd_percent < 0.01
    assert nonlinearity.returned.thd_percent == pytest.approx(4.06, rel=0.01)
    assert selftest.nonlinearity_findings(nonlinearity) == []


def test_a_response_that_is_not_the_sine_that_went_in_is_not_a_distortion_figure():
    """Otherwise a probe pointed at the wrong node reads as a working amp."""
    nonlinearity = selftest.measure_nonlinearity(probe(sine(), sine(1000.0)))
    findings = selftest.nonlinearity_findings(nonlinearity)
    assert checks(findings) == ["not-a-sine"]
    assert "not a distortion figure" in findings[0].message


def test_distortion_already_in_the_stimulus_is_not_attributed_to_the_component():
    injected = squared(sine(), 0.16)
    nonlinearity = selftest.measure_nonlinearity(probe(injected, squared(injected, 0.16)))
    assert checks(selftest.nonlinearity_findings(nonlinearity)) == ["stimulus-distorted"]


def test_each_measurement_reads_as_one_line_carrying_its_numbers():
    """These lines are what a player sees, so they carry the measurement rather
    than a verdict."""
    injected = sine()
    assert "-6.0 dBFS peak" in str(selftest.measure_levels(probe(injected, injected)))
    assert "257 samples (5.35 ms)" in str(
        selftest.Delay(name="sushi", sample_rate=SR, samples=257)
    )
    assert "not measurable" in str(
        selftest.Delay(name="sushi", sample_rate=SR, samples=None)
    )
    assert "220 Hz" in str(
        selftest.measure_nonlinearity(probe(injected, squared(injected, 0.16)))
    )


# --------------------------------------------------------------------------- #
# Against a real Sushi
# --------------------------------------------------------------------------- #

def render_passthrough(tmp_path, stimulus):
    """Push `stimulus` through Sushi's internal passthrough and probe the result.

    Channel 0 of each side, because the measurements refuse multichannel input
    rather than silently reducing it, and both halves of this render are the
    same signal anyway.
    """
    from sushi_rig.render import render_offline

    config = tmp_path / "passthrough.json"
    config.write_text(json.dumps(PASSTHROUGH_CONFIG))
    out = render_offline(
        config, ROOT, tmp_path / "work", stimulus=stimulus, sushi_bin=str(SUSHI)
    )
    return selftest.Probe(
        name="passthrough",
        injected=out["stimulus_samples"][:, 0],
        returned=out["rendered"][:, 0],
        sample_rate=out["sample_rate"],
    )


@needs_sushi
@pytest.mark.slow
def test_a_real_passthrough_passes_audio_and_adds_no_harmonics(tmp_path):
    """The amp failure's shape, rendered rather than constructed: something
    comes out, it is as loud as what went in, and it is still the same sine."""
    from sushi_rig.render import Stimulus

    measured = render_passthrough(
        tmp_path, Stimulus(kind="sine", frequency_hz=220.0, duration_s=1.0)
    )
    levels = selftest.measure_levels(measured)
    assert selftest.level_findings(levels) == [], str(levels)

    nonlinearity = selftest.measure_nonlinearity(measured)
    findings = selftest.nonlinearity_findings(nonlinearity)
    assert checks(findings) == ["linear"], f"{nonlinearity}; {findings}"


@needs_sushi
@pytest.mark.slow
def test_a_delay_is_measurable_through_a_real_render(tmp_path):
    """Whatever the offline frontend's own block size turns out to be, the
    measurement has to come back with a number rather than with nothing — the
    failure mode `measure_delay` reports as `no-correlation`.

    The value is deliberately not asserted: it has never been observed here, and
    a number written in without measuring it is the fixture mistake this project
    already made once.
    """
    from sushi_rig.render import Stimulus

    measured = render_passthrough(tmp_path, Stimulus(duration_s=1.0))
    delay = selftest.measure_delay(measured)
    assert delay.samples is not None, str(delay)
    assert selftest.delay_findings(delay, expected_quantum=None) == []
