"""Measurements on audio, against synthetic ground truth.

Each test builds a signal whose answer is known by construction — shifted by N
samples, scaled by exactly 1 dB, filtered by a biquad whose response has a
closed form — and checks the measurement recovers it. None of them uses a
recording of the thing being tested. Skipped when the optional `audio` extra
(numpy) is not installed.
"""

from __future__ import annotations

import math

import pytest

np = pytest.importorskip("numpy")

from sushi_rig import analysis, signals  # noqa: E402

SR = 48000


def delayed(x, samples):
    """x shifted later by `samples`, same length, zero-filled at the start."""
    out = np.zeros_like(x)
    out[samples:] = x[:len(x) - samples]
    return out


def gain_db(x, db):
    return x * 10 ** (db / 20)


def peaking_eq_coefficients(sr, f0, gain_db_, q):
    """RBJ Audio EQ Cookbook peaking filter, as (b, a)."""
    a_ = 10 ** (gain_db_ / 40)
    w0 = 2 * math.pi * f0 / sr
    alpha = math.sin(w0) / (2 * q)
    b = [1 + alpha * a_, -2 * math.cos(w0), 1 - alpha * a_]
    a = [1 + alpha / a_, -2 * math.cos(w0), 1 - alpha / a_]
    return [c / a[0] for c in b], [c / a[0] for c in a]


def biquad_magnitude_db(b, a, f, sr):
    """The exact response of the biquad at f, straight from its coefficients."""
    z = np.exp(-1j * 2 * math.pi * f / sr)
    h = (b[0] + b[1] * z + b[2] * z ** 2) / (a[0] + a[1] * z + a[2] * z ** 2)
    return 20 * math.log10(abs(h))


def apply_biquad(x, b, a):
    """Run a biquad over x. The impulse response is found by recursion and then
    applied by FFT convolution; a pure-Python sample loop over seconds of audio
    would make these tests slow."""
    ir = np.zeros(8192)
    x1 = x2 = y1 = y2 = 0.0
    for n in range(len(ir)):
        xn = 1.0 if n == 0 else 0.0
        yn = b[0] * xn + b[1] * x1 + b[2] * x2 - a[1] * y1 - a[2] * y2
        x2, x1, y2, y1 = x1, xn, y1, yn
        ir[n] = yn
    size = 1 << (len(x) + len(ir)).bit_length()
    return np.fft.irfft(np.fft.rfft(x, size) * np.fft.rfft(ir, size), size)[:len(x)]


# --------------------------------------------------------------------------- #
# Levels
# --------------------------------------------------------------------------- #

def test_peak_and_rms_of_a_known_sine():
    x = signals.sine(SR, 1.0, 1000, level_dbfs=-6.0)
    assert analysis.peak_dbfs(x) == pytest.approx(-6.0, abs=1e-6)
    # RMS of a sine is peak / sqrt(2): 3.01 dB below.
    assert analysis.rms_dbfs(x) == pytest.approx(-6.0 - 3.0103, abs=1e-3)


def test_full_scale_square_is_zero_dbfs_both_ways():
    x = np.tile([[1.0], [-1.0]], (100, 1))
    assert analysis.peak_dbfs(x) == 0.0
    assert analysis.rms_dbfs(x) == pytest.approx(0.0)


def test_silence_is_minus_infinity_not_a_floor():
    z = signals.silence(SR, 0.1)
    assert analysis.peak_dbfs(z) == -math.inf
    assert analysis.rms_dbfs(z) == -math.inf


def test_one_dimensional_input_is_mono_and_empty_input_is_refused():
    assert analysis.peak_dbfs(np.array([0.5, -0.25])) == pytest.approx(-6.0206, abs=1e-3)
    with pytest.raises(ValueError):
        analysis.peak_dbfs(np.array([]))


# --------------------------------------------------------------------------- #
# Safety checks
# --------------------------------------------------------------------------- #

def test_a_healthy_signal_has_no_findings():
    assert analysis.safety_findings(signals.pink_noise(SR, 1.0, -12.0), SR) == []


def test_nan_is_found_with_its_position():
    x = signals.sine(SR, 0.1, 440)
    x[123, 0] = np.nan
    finding = analysis.check_finite(x)
    assert finding.check == "non_finite"
    assert "1 NaN" in finding.message and "frame 123" in finding.message


def test_inf_is_found_and_counted_separately_from_nan():
    x = signals.sine(SR, 0.1, 440, channels=2)
    x[5, 1] = np.inf
    x[9, 0] = -np.inf
    x[7, 0] = np.nan
    finding = analysis.check_finite(x)
    assert "1 NaN and 2 Inf" in finding.message


def test_non_finite_is_reported_alone_because_the_other_numbers_would_be_garbage():
    x = signals.sine(SR, 0.1, 440)
    x[10, 0] = np.nan
    findings = analysis.safety_findings(x, SR)
    assert [f.check for f in findings] == ["non_finite"]


def test_dc_offset_is_found_when_added_to_a_clean_signal():
    clean = signals.sine(SR, 1.0, 1000, level_dbfs=-12.0)
    assert analysis.check_dc_offset(clean, threshold_dbfs=-60.0) is None
    finding = analysis.check_dc_offset(clean + 0.05, threshold_dbfs=-60.0)  # 0.05 = -26 dBFS
    assert finding.check == "dc_offset"
    assert "-26.0 dBFS" in finding.message


def test_dc_offset_on_one_channel_only_names_that_channel():
    x = signals.sine(SR, 1.0, 1000, level_dbfs=-12.0, channels=2)
    x[:, 1] += 0.1
    assert "channel 1" in analysis.check_dc_offset(x, -60.0).message


def test_peak_over_ceiling_is_strict_and_reports_the_measurement():
    x = signals.sine(SR, 0.1, 440, level_dbfs=-1.0)
    assert analysis.check_peak_ceiling(x, ceiling_dbfs=-1.0 + 1e-6) is None
    finding = analysis.check_peak_ceiling(x, ceiling_dbfs=-3.0)
    assert finding.check == "peak_over_ceiling"
    assert "-1.00 dBFS" in finding.message and "-3.00 dBFS" in finding.message


def test_overs_above_zero_dbfs_are_caught_at_the_default_ceiling():
    assert analysis.safety_findings(gain_db(signals.sine(SR, 0.5, 440, 0.0), 3.0), SR)[0].check == "peak_over_ceiling"


def test_whole_signal_silence_is_found_but_a_quiet_signal_is_not():
    assert analysis.check_silence(signals.silence(SR, 0.5), SR, -80.0).check == "silence"
    assert analysis.check_silence(signals.sine(SR, 0.5, 440, -60.0), SR, -80.0) is None


def test_whole_length_silence_ignores_a_dropout_but_a_window_catches_it():
    x = signals.sine(SR, 2.0, 440, -12.0)
    x[SR // 2:SR // 2 + 4800] = 0.0  # 100 ms dropout starting at 0.5 s
    assert analysis.check_silence(x, SR, -80.0) is None
    finding = analysis.check_silence(x, SR, -80.0, window_s=0.05)
    assert finding.check == "silence"
    assert "0.500 s" in finding.message


def test_silence_window_finds_a_dropout_at_any_alignment_and_not_a_shorter_one():
    x = signals.sine(SR, 2.0, 440, -12.0)
    x[1000:1000 + 2400] = 0.0                                   # 50 ms, awkwardly aligned
    assert analysis.check_silence(x, SR, -80.0, window_s=0.05) is not None
    assert analysis.check_silence(x, SR, -80.0, window_s=0.06) is None


def test_silence_on_one_channel_of_a_stereo_pair_is_found():
    x = signals.sine(SR, 0.5, 440, channels=2)
    x[:, 0] = 0.0
    assert "[0]" in analysis.check_silence(x, SR, -80.0).message


def test_silence_check_can_be_switched_off_for_a_rig_that_should_be_silent():
    z = signals.silence(SR, 0.2)
    assert analysis.safety_findings(z, SR) != []
    assert analysis.safety_findings(z, SR, silence_threshold_dbfs=None) == []


def test_silence_window_longer_than_the_signal_is_refused():
    with pytest.raises(ValueError, match="does not fit"):
        analysis.check_silence(signals.sine(SR, 0.1, 440), SR, -80.0, window_s=1.0)


# --------------------------------------------------------------------------- #
# Delay
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("shift", [0, 1, 37, 4800, 17999])
def test_a_known_shift_is_recovered_exactly(shift):
    x = signals.pink_noise(SR, 1.0, -6.0)
    peak = analysis.estimate_delay(x, delayed(x, shift))
    assert peak.lag_samples == shift
    assert peak.gain == pytest.approx(1.0, abs=0.02)


def test_a_signal_that_leads_gives_a_negative_lag():
    x = signals.pink_noise(SR, 1.0, -6.0)
    assert analysis.estimate_delay(delayed(x, 100), x).lag_samples == -100


def test_polarity_inversion_is_found_as_negative_correlation():
    x = signals.pink_noise(SR, 0.5)
    peak = analysis.estimate_delay(x, -delayed(x, 50))
    assert peak.lag_samples == 50 and peak.gain == pytest.approx(-1.0, abs=0.02)


def test_an_echo_at_375_ms_is_found_alongside_the_direct_sound():
    x = signals.pink_noise(SR, 2.0, -12.0)
    echo = round(0.375 * SR)
    wet = x + gain_db(delayed(x, echo), -6.0)
    peaks = analysis.find_delays(x, wet, max_peaks=5, max_lag_samples=SR)
    assert [p.lag_samples for p in peaks] == [0, echo]
    assert [p.gain for p in peaks] == pytest.approx([1.0, 0.5], abs=0.02)  # -6 dB is 0.501


def test_find_delays_works_on_a_sweep_stimulus_too():
    x = signals.log_sweep(SR, 1.0)
    assert analysis.estimate_delay(x, delayed(x, 321)).lag_samples == 321


def test_max_lag_restricts_the_search():
    x = signals.pink_noise(SR, 1.0)
    peaks = analysis.find_delays(x, delayed(x, 5000), max_lag_samples=100, max_peaks=3)
    assert all(abs(p.lag_samples) <= 100 for p in peaks)
    assert analysis.estimate_delay(x, delayed(x, 5000), max_lag_samples=6000).lag_samples == 5000


def test_a_delay_beyond_half_the_signal_is_not_searched():
    # Documented limit: with under half the reference overlapping, the gain
    # correction would amplify noise, so those lags are excluded.
    x = signals.pink_noise(SR, 1.0)
    assert all(p.lag_samples != 30000 for p in analysis.find_delays(x, delayed(x, 30000), max_peaks=5))


def test_correlating_against_silence_raises_instead_of_reporting_lag_zero():
    with pytest.raises(ValueError, match="no correlation"):
        analysis.estimate_delay(signals.pink_noise(SR, 0.1), signals.silence(SR, 0.1))


def test_delay_functions_refuse_multichannel_input():
    with pytest.raises(ValueError, match="mono"):
        analysis.estimate_delay(signals.pink_noise(SR, 0.1, channels=2), signals.pink_noise(SR, 0.1))


# --------------------------------------------------------------------------- #
# Null test
# --------------------------------------------------------------------------- #

def test_identical_signals_null_to_silence():
    x = signals.pink_noise(SR, 0.5, -12.0, channels=2)
    result = analysis.null_test(x, x.copy())
    assert result.residual_dbfs == -math.inf
    assert result.null_depth_db == math.inf
    assert result.compared_frames == len(x)


def test_a_one_db_gain_difference_leaves_the_expected_residual():
    x = signals.pink_noise(SR, 1.0, -6.0)
    result = analysis.null_test(x, gain_db(x, -1.0))
    # residual = x * (1 - 10^(-1/20)), so it sits 20*log10(0.1087) = -19.27 dB below x.
    expected_depth = -20 * math.log10(1 - 10 ** (-1 / 20))
    assert result.null_depth_db == pytest.approx(expected_depth, abs=1e-6)
    assert result.residual_dbfs == pytest.approx(analysis.rms_dbfs(x) - expected_depth, abs=1e-6)
    assert expected_depth == pytest.approx(19.27, abs=0.01)


def test_residual_is_measured_in_dbfs_not_relative_to_the_signal():
    # The same relative error on a quiet signal is a lower absolute residual.
    loud = signals.pink_noise(SR, 0.5, -6.0)
    quiet = gain_db(loud, -30.0)
    a = analysis.null_test(loud, gain_db(loud, -1.0))
    b = analysis.null_test(quiet, gain_db(quiet, -1.0))
    assert b.residual_dbfs == pytest.approx(a.residual_dbfs - 30.0, abs=1e-6)
    assert b.null_depth_db == pytest.approx(a.null_depth_db, abs=1e-6)


def test_unaligned_null_of_a_delayed_copy_is_poor_and_aligned_is_perfect():
    x = signals.pink_noise(SR, 1.0, -12.0)
    y = delayed(x, 240)
    assert analysis.null_test(x, y).null_depth_db < 10
    aligned = analysis.null_test(x, y, align=True)
    assert aligned.lag_samples == 240
    assert aligned.compared_frames == len(x) - 240
    assert aligned.residual_dbfs == -math.inf


def test_alignment_corrects_time_but_leaves_a_gain_difference_in_the_residual():
    x = signals.pink_noise(SR, 1.0, -12.0)
    result = analysis.null_test(x, gain_db(delayed(x, 77), -1.0), align=True)
    assert result.lag_samples == 77
    assert result.null_depth_db == pytest.approx(-20 * math.log10(1 - 10 ** (-1 / 20)), abs=1e-6)


def test_alignment_works_when_the_test_signal_leads():
    x = signals.pink_noise(SR, 0.5, -12.0)
    result = analysis.null_test(delayed(x, 90), x, align=True)
    assert result.lag_samples == -90
    assert result.residual_dbfs == -math.inf


def test_mismatched_lengths_are_refused_unless_aligning():
    x = signals.pink_noise(SR, 0.5)
    with pytest.raises(ValueError, match="lengths differ"):
        analysis.null_test(x, x[:-10])
    assert analysis.null_test(x, x[:-10], align=True).compared_frames == len(x) - 10


def test_mismatched_channel_counts_are_refused():
    with pytest.raises(ValueError, match="channel"):
        analysis.null_test(signals.pink_noise(SR, 0.1, channels=2), signals.pink_noise(SR, 0.1))


# --------------------------------------------------------------------------- #
# Frequency response
# --------------------------------------------------------------------------- #

def test_a_peaking_eq_of_plus_six_db_at_one_khz_is_measured():
    stimulus = signals.pink_noise(SR, 10.0, -12.0)[:, 0]
    b, a = peaking_eq_coefficients(SR, 1000.0, 6.0, q=1.0)
    response = apply_biquad(stimulus, b, a)
    probes = [1000.0, 100.0, 10000.0, 500.0, 2000.0]
    points = analysis.frequency_response(stimulus, response, SR, probes)
    by_freq = {p.frequency_hz: p for p in points}

    assert by_freq[1000.0].gain_db == pytest.approx(6.0, abs=0.1)
    assert abs(by_freq[100.0].gain_db) < 0.5        # far away: ~0 dB
    assert abs(by_freq[10000.0].gain_db) < 0.5
    # And the shoulders agree with the filter's own closed-form response.
    for f in probes:
        assert by_freq[f].gain_db == pytest.approx(biquad_magnitude_db(b, a, f, SR), abs=0.15)
    assert all(p.coherence > 0.99 for p in points)


def test_a_pure_gain_change_is_measured_everywhere_in_the_stimulus_band():
    stimulus = signals.pink_noise(SR, 5.0)[:, 0]
    points = analysis.frequency_response(stimulus, gain_db(stimulus, -9.0), SR, [100, 1000, 10000])
    assert [p.gain_db for p in points] == pytest.approx([-9.0] * 3, abs=1e-6)


def test_uncorrelated_noise_at_the_output_averages_away_and_shows_as_low_coherence():
    stimulus = signals.pink_noise(SR, 10.0, seed=1)[:, 0]
    noisy = stimulus + 0.5 * signals.pink_noise(SR, 10.0, seed=2)[:, 0]  # SNR about 6 dB
    clean_point = analysis.frequency_response(stimulus, stimulus, SR, [1000])[0]
    noisy_point = analysis.frequency_response(stimulus, noisy, SR, [1000])[0]
    assert clean_point.coherence == pytest.approx(1.0)
    assert 0.5 < noisy_point.coherence < 0.95
    assert noisy_point.gain_db == pytest.approx(0.0, abs=0.5)  # H1 stays unbiased by output noise


def test_a_frequency_the_stimulus_never_contained_is_not_measured_rather_than_invented():
    stimulus = signals.sine(SR, 5.0, 1000.0)[:, 0] + 1e-3 * signals.pink_noise(SR, 5.0)[:, 0]
    # Remove everything above 2 kHz from the stimulus so there is truly nothing there.
    spectrum = np.fft.rfft(stimulus)
    spectrum[np.fft.rfftfreq(len(stimulus), 1 / SR) > 2000] = 0
    stimulus = np.fft.irfft(spectrum, len(stimulus))
    point = analysis.frequency_response(stimulus, stimulus, SR, [10000])[0]
    assert math.isnan(point.gain_db) and point.coherence == 0.0


@pytest.mark.parametrize("kwargs,match", [
    (dict(frequencies_hz=[30000]), "within"),
    (dict(frequencies_hz=[]), "within"),
    (dict(frequencies_hz=[1000], segment_length=1 << 20), "too short"),
])
def test_frequency_response_refuses_unmeasurable_requests(kwargs, match):
    x = signals.pink_noise(SR, 2.0)[:, 0]
    with pytest.raises(ValueError, match=match):
        analysis.frequency_response(x, x, SR, **kwargs)


def test_frequency_response_refuses_mismatched_lengths():
    x = signals.pink_noise(SR, 2.0)[:, 0]
    with pytest.raises(ValueError, match="length"):
        analysis.frequency_response(x, x[:-1], SR, [1000])


# --------------------------------------------------------------------------- #
# Band energies
# --------------------------------------------------------------------------- #

def test_a_sine_lands_in_its_band_at_its_rms_level_and_nowhere_else():
    x = signals.sine(SR, 2.0, 1000.0, level_dbfs=-6.0)
    bands = analysis.band_energies(x, SR)
    in_band = [b for b in bands if b.low_hz <= 1000 < b.high_hz]
    assert len(in_band) == 1
    assert in_band[0].level_dbfs == pytest.approx(analysis.rms_dbfs(x), abs=0.01)
    assert all(b.level_dbfs < -70 for b in bands if b is not in_band[0])


def test_sum_of_band_energies_matches_the_signals_power():
    x = signals.pink_noise(SR, 4.0, -12.0, channels=2)
    bands = analysis.band_energies(x, SR, edges_hz=[0.0, 100.0, 1000.0, 10000.0, 24000.0])
    total = 10 * math.log10(sum(10 ** (b.level_dbfs / 10) for b in bands))
    # Not exact: the Hann window weights a finite stretch of pink noise unevenly,
    # and nearly all its energy is in the lowest few bins. 0.3 dB catches a
    # scaling error (a missing factor of 2 is 3 dB) without chasing that.
    assert total == pytest.approx(analysis.rms_dbfs(x), abs=0.3)


def test_pink_noise_has_equal_energy_in_each_octave_band():
    octaves = [125, 250, 500, 1000, 2000, 4000, 8000]
    bands = analysis.band_energies(signals.pink_noise(SR, 8.0), SR, edges_hz=octaves)
    levels = [b.level_dbfs for b in bands]
    assert max(levels) - min(levels) < 0.5


def test_band_differences_show_a_tone_change_in_the_right_band_only():
    stimulus = signals.pink_noise(SR, 8.0, -12.0)[:, 0]
    b, a = peaking_eq_coefficients(SR, 1000.0, 6.0, q=0.7)
    boosted = apply_biquad(stimulus, b, a)
    ref = analysis.band_energies(stimulus, SR)
    diffs = analysis.band_differences_db(ref, analysis.band_energies(boosted, SR))
    middle = [i for i, band in enumerate(ref) if band.low_hz <= 1000 < band.high_hz][0]
    assert diffs[middle] > 3.0
    assert abs(diffs[0]) < 1.0 and abs(diffs[-1]) < 1.0


def test_band_differences_handle_silent_bands_and_mismatched_layouts():
    silent = analysis.band_energies(signals.silence(SR, 0.5), SR)
    loud = analysis.band_energies(signals.pink_noise(SR, 0.5), SR)
    assert analysis.band_differences_db(silent, silent) == [0.0] * len(silent)  # not NaN
    assert analysis.band_differences_db(silent, loud) == [math.inf] * len(silent)
    with pytest.raises(ValueError, match="layouts"):
        analysis.band_differences_db(loud, analysis.band_energies(signals.pink_noise(SR, 0.5), SR, edges_hz=[100, 200]))


def test_band_edges_beyond_nyquist_or_out_of_order_are_refused():
    x = signals.pink_noise(SR, 0.2)
    with pytest.raises(ValueError):
        analysis.band_energies(x, SR, edges_hz=[100, 30000])
    with pytest.raises(ValueError):
        analysis.band_energies(x, SR, edges_hz=[1000, 100])
