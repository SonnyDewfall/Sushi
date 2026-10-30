"""Null tests: has the rig's sound changed?

All of this is decisions — a pair of fingerprints in, a list of differences out
— so it runs with no Sushi, no plugins and no audio. The render itself is the
effect, and is verified by running it in the CI container.

The signals here are synthetic rather than captured, which is safe only because
nothing below asserts anything about how the *rig* sounds. They exercise the
comparison, and a real render would tell us nothing extra about that.
"""

from __future__ import annotations

import pytest

np = pytest.importorskip("numpy")

from sushi_rig import nulltest, signals  # noqa: E402
from sushi_rig.analysis import BandEnergy  # noqa: E402
from sushi_rig.nulltest import (  # noqa: E402
    PROVISIONAL,
    Tolerance,
    compare,
    fingerprint,
    floor_between,
    from_dict,
    to_dict,
)

SR = 48000
STIMULUS = "log_sweep 20-20000 1.0s -20dBFS"


def _sweep(duration=1.0, level=-20.0):
    return signals.log_sweep(SR, duration, level_dbfs=level)


def _fp(samples, config="electric-clean", stimulus=STIMULUS, against=None):
    return fingerprint(
        samples, SR, config=config, stimulus=stimulus, stimulus_samples=against
    )


# --- the thing it is for ----------------------------------------------------


def test_the_same_render_twice_reports_no_change():
    """The property the whole safety net rests on. If an unchanged render ever
    reported a difference, every real one would be ignored inside a week."""
    sweep = _sweep()
    assert compare(_fp(sweep), _fp(sweep)) == []


def test_a_quieter_render_is_caught():
    """The cheapest real regression: the refactor drops a gain stage."""
    findings = compare(_fp(_sweep()), _fp(_sweep() * 0.5))
    assert any(f.check == "rms-changed" for f in findings)
    assert any("-6." in f.message for f in findings), "should state how far it moved"


def test_a_tone_change_names_the_band_that_moved():
    """A binary diff of two WAVs says "different". This has to say *where*,
    because that is what makes a failure actionable."""
    sweep = _sweep()
    spectrum = np.fft.rfft(sweep, axis=0)
    freqs = np.arange(spectrum.shape[0]) * SR / sweep.shape[0]
    spectrum[(freqs >= 2500) & (freqs < 8000)] *= 0.25  # pull the presence band down
    dulled = np.fft.irfft(spectrum, n=sweep.shape[0], axis=0)

    findings = compare(_fp(sweep), _fp(dulled))
    bands = [f.message for f in findings if f.check == "band-changed"]
    assert bands, "a 12 dB cut in one band must be reported"
    assert any("2500" in m for m in bands), f"should name the band: {bands}"


def test_added_latency_is_caught_even_though_the_tone_is_identical():
    """`null_test` documents that alignment hides exactly this: a chain that
    gained 20 ms nulls perfectly once aligned. So delay is fingerprinted
    separately rather than left to the spectrum, which would not move at all."""
    sweep = _sweep()
    delayed = np.roll(sweep, 480, axis=0)  # 10 ms at 48 kHz

    ref = _fp(sweep, against=sweep)
    test = _fp(delayed, against=sweep)
    findings = compare(ref, test)

    assert any(f.check == "delay-changed" for f in findings)
    assert any("+480" in f.message for f in findings)


# --- refusing to compare things that are not comparable ---------------------


def test_a_different_stimulus_is_refused_rather_than_compared():
    """Comparing a sweep against a sine produces six confident band differences
    that mean nothing. Better to say so than to report noise as findings."""
    findings = compare(
        _fp(_sweep(), stimulus="log_sweep 20-20000 1.0s -20dBFS"),
        _fp(signals.sine(SR, 1.0, 440.0), stimulus="sine 440Hz 1.0s -20dBFS"),
    )
    assert [f.check for f in findings] == ["stimulus-differs"]


def test_a_truncated_render_is_caught_rather_than_trimmed():
    """A render missing its tail is a real failure — a chain that died halfway.
    Silently comparing the overlap would pass it."""
    findings = compare(_fp(_sweep(1.0)), _fp(_sweep(0.5)))
    assert [f.check for f in findings] == ["length-differs"]


def test_a_rate_change_is_caught():
    ref = _fp(_sweep())
    test = fingerprint(_sweep(), 44100, config="electric-clean", stimulus=STIMULUS)
    assert any(f.check == "rate-differs" for f in compare(ref, test))


def test_a_band_silent_on_one_side_is_worded_for_a_human():
    """`band_differences_db` returns +/-inf here, and "moved +inf dB" reads like
    a bug in the tool rather than a finding about the rig."""
    loud = [BandEnergy(20.0, 80.0, -30.0)]
    silent = [BandEnergy(20.0, 80.0, float("-inf"))]
    ref = nulltest.Fingerprint("c", STIMULUS, SR, 100, -6.0, -20.0, tuple(loud), None)
    test = nulltest.Fingerprint("c", STIMULUS, SR, 100, -6.0, -20.0, tuple(silent), None)

    (finding,) = [f for f in compare(ref, test) if f.check == "band-changed"]
    assert "silent in one render" in finding.message
    assert "inf" not in finding.message


def test_a_render_full_of_nan_is_reported_as_broken_not_as_six_band_changes():
    broken = _sweep()
    broken[1000:2000] = np.nan
    assert nulltest.render_findings(broken, SR), "NaN must be caught before comparing"
    assert nulltest.render_findings(_sweep(), SR) == []


# --- tolerance is measured, not assumed -------------------------------------


def test_the_provisional_tolerance_says_it_is_not_measured():
    """A reference still carrying this has never been calibrated, and the file
    has to admit that rather than look authoritative."""
    assert "not measured" in PROVISIONAL.note


def test_the_floor_between_two_runs_is_the_difference_it_observed():
    """What --calibrate records. No margin is folded in: deciding how much
    headroom to allow is a judgement that should be visible in the file."""
    sweep = _sweep()
    nudged = sweep * (10 ** (0.02 / 20))  # 0.02 dB louder, uniformly

    floor = floor_between(_fp(sweep), _fp(nudged))
    assert floor.level_db == pytest.approx(0.02, abs=0.005)
    assert floor.band_db == pytest.approx(0.02, abs=0.005)
    assert "measured" in floor.note


def test_a_difference_inside_a_calibrated_tolerance_is_not_a_finding():
    """The point of calibrating: a chorus that differs from itself by 0.3 dB per
    band must not report that as a change in the rig's sound."""
    sweep = _sweep()
    jittery = sweep * (10 ** (0.2 / 20))
    tolerance = Tolerance(band_db=0.5, level_db=0.5, delay_samples=0, note="measured")
    assert compare(_fp(sweep), _fp(jittery), tolerance) == []
    assert compare(_fp(sweep), _fp(jittery), PROVISIONAL) != []


# --- the reference file -----------------------------------------------------


def test_a_reference_round_trips():
    fp = _fp(_sweep(), against=_sweep())
    tolerance = Tolerance(0.05, 0.02, 1, "measured over two runs")

    back_fp, back_tol = from_dict(to_dict(fp, tolerance))

    assert back_fp.config == fp.config
    assert back_fp.stimulus == fp.stimulus
    assert back_fp.frames == fp.frames
    assert back_fp.delay_samples == fp.delay_samples
    assert [b.level_dbfs for b in back_fp.bands] == pytest.approx(
        [round(b.level_dbfs, 4) for b in fp.bands]
    )
    assert back_tol == tolerance


def test_a_round_tripped_reference_still_compares_clean():
    """Rounding for legibility must not make a reference disagree with the very
    render it was written from."""
    sweep = _sweep()
    fp = _fp(sweep)
    back, tol = from_dict(to_dict(fp, Tolerance(0.001, 0.001, 0, "measured")))
    assert compare(back, _fp(sweep), tol) == []


def test_an_unknown_schema_version_is_refused():
    """This module exists to support the claim "the sound has not changed". A
    half-understood reference cannot support it, so it is refused rather than
    read optimistically."""
    raw = to_dict(_fp(_sweep()), PROVISIONAL)
    raw["schema_version"] = 99
    with pytest.raises(ValueError, match="99"):
        from_dict(raw)


def test_the_written_reference_is_readable():
    """It is committed and reviewed by eye, so the numbers have to be short
    enough to read and the bands labelled with their frequencies."""
    out = to_dict(_fp(_sweep(), against=_sweep()), PROVISIONAL)
    assert out["measured"]["bands"][0]["low_hz"] == 20.0
    assert all(
        len(str(b["level_dbfs"]).split(".")[-1]) <= 4
        for b in out["measured"]["bands"]
    )


def test_delay_is_measurable_on_a_stereo_render():
    """The bug this closes: `estimate_delay` refuses multichannel input rather
    than silently reducing it, so with both signals passed through whole the
    delay could never be measured on any real render — every one is at least
    stereo. Found by rendering through a passthrough and fingerprinting the
    result; neither this module's tests nor render.py's would have caught it,
    because each half was correct on its own."""
    sweep = _sweep()                      # (frames, 2)
    delayed = np.roll(sweep, 240, axis=0)
    fp = _fp(delayed, against=sweep)
    assert fp.delay_samples == 240


def test_delay_still_works_on_a_mono_render():
    """A mono render is not special-cased away by the stereo fix."""
    mono = signals.log_sweep(SR, 1.0, level_dbfs=-20.0, channels=1)
    fp = _fp(np.roll(mono, 100, axis=0), against=mono)
    assert fp.delay_samples == 100
