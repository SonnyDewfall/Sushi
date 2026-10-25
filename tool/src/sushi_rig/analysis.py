"""Measurements on rendered audio, as pure functions on numpy arrays.

What this enables: once the rig's audio can be rendered offline to WAV files,
these are the questions a test asks of the result — is it safe (finite, no DC,
no overs, not accidentally silent), did the chain change (null test of v1
against v2, or of today's render against an approved reference), and does it
behave (an echo at 375 ms, +6 dB at 1 kHz, the same spectral balance). The
stimuli and file I/O are in `signals`.

What is NOT here: the renderer. Nothing in this module starts Sushi, and
Sushi's offline-mode command-line flags have not been verified yet, so none are
guessed at. These functions take arrays and return numbers or findings; they are
verified against synthetic ground truth (a signal delayed by a known number of
samples, a filter with a known response) rather than against a real render.

Conventions (shared with `signals`): audio is `(frames, channels)` float64, and a
1-D array is read as mono. 0 dBFS is amplitude 1.0. `rms_dbfs` is the plain RMS,
so a full-scale sine reads -3.01 dBFS. Silence is `-inf`, not a made-up floor —
a floor would be a number that looks like a measurement.

Decisions here return values, and checks return `Finding`s instead of raising:
a failed safety check is a result for the caller's test to assert on or print,
not an error in this module. Genuinely invalid *use* — mismatched lengths, a
frequency above Nyquist, too little signal to measure from — raises
`ValueError`, because a measurement quietly made on the wrong input is worse
than none.

Deliberately not implemented: loudness (ITU-R BS.1770 / LUFS). It needs K-weighting
filters whose coefficients depend on the sample rate and gated block averaging,
and the only convincing test is against the published EBU Tech 3341/3342
reference signals, which are not available here. A loudness number that has not
been checked against them is worse than no number, so it is left out rather than
shipped untested. Likewise absent: true-peak (inter-sample) measurement —
`peak_dbfs` is the sample peak, and can read lower than what a DAC will emit.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Sequence

from sushi_rig._numpy import require_numpy

if TYPE_CHECKING:
    import numpy as np


# --------------------------------------------------------------------------- #
# Plumbing
# --------------------------------------------------------------------------- #

def _frames(signal) -> "np.ndarray":
    """Coerce to float64 (frames, channels); reject the empty and the 3-D."""
    np = require_numpy()
    arr = np.asarray(signal, dtype=np.float64)
    if arr.ndim == 1:
        arr = arr[:, None]
    if arr.ndim != 2 or arr.shape[0] == 0 or arr.shape[1] == 0:
        raise ValueError(f"signal must be non-empty (frames,) or (frames, channels); got shape {arr.shape}")
    return arr


def _mono(signal, what: str) -> "np.ndarray":
    """1-D float64. Multichannel is refused rather than silently reduced."""
    arr = _frames(signal)
    if arr.shape[1] != 1:
        raise ValueError(
            f"{what} must be mono, got {arr.shape[1]} channels — pick one with signal[:, 0]"
        )
    return arr[:, 0]


def _power_to_dbfs(power: float) -> float:
    np = require_numpy()
    if power != power:
        return float("nan")
    if power <= 0.0:
        return float("-inf")
    return float(10.0 * np.log10(power))


def _amplitude_to_dbfs(amplitude: float) -> float:
    np = require_numpy()
    if amplitude != amplitude:
        return float("nan")
    if amplitude <= 0.0:
        return float("-inf")
    return float(20.0 * np.log10(amplitude))


# --------------------------------------------------------------------------- #
# Levels
# --------------------------------------------------------------------------- #

def peak_dbfs(signal) -> float:
    """Sample peak over all channels, in dBFS. `-inf` for digital silence.

    NaN in the input gives NaN out; run `check_finite` first if that can happen.
    """
    np = require_numpy()
    return _amplitude_to_dbfs(float(np.max(np.abs(_frames(signal)))))


def rms_dbfs(signal) -> float:
    """RMS over all samples of all channels, in dBFS. `-inf` for digital silence."""
    np = require_numpy()
    return _power_to_dbfs(float(np.mean(_frames(signal) ** 2)))


# --------------------------------------------------------------------------- #
# Safety checks
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class Finding:
    """One thing a safety check found wrong.

    `check` is a stable identifier tests can match on; `message` is for the human
    reading a failing run, and says what was measured against what.
    """
    check: str
    message: str

    def __str__(self) -> str:
        return f"{self.check}: {self.message}"


def check_finite(signal) -> Finding | None:
    """NaN or Inf anywhere. A plugin that blows up usually shows here first."""
    np = require_numpy()
    arr = _frames(signal)
    bad = ~np.isfinite(arr)
    if not bad.any():
        return None
    nan = int(np.isnan(arr).sum())
    inf = int(bad.sum()) - nan
    frame, channel = (int(i) for i in np.argwhere(bad)[0])
    return Finding(
        "non_finite",
        f"{nan} NaN and {inf} Inf samples; the first is at frame {frame}, channel {channel}",
    )


def check_dc_offset(signal, threshold_dbfs: float) -> Finding | None:
    """Mean of any channel above `threshold_dbfs`. A steady offset eats headroom
    and thumps when it changes; a healthy guitar chain has essentially none."""
    np = require_numpy()
    arr = _frames(signal)
    worst = _amplitude_to_dbfs(float(np.max(np.abs(arr.mean(axis=0)))))
    if worst <= threshold_dbfs:
        return None
    channel = int(np.argmax(np.abs(arr.mean(axis=0))))
    return Finding(
        "dc_offset",
        f"DC offset {worst:.1f} dBFS on channel {channel} is above the {threshold_dbfs:.1f} dBFS threshold",
    )


def check_peak_ceiling(signal, ceiling_dbfs: float) -> Finding | None:
    """Sample peak above `ceiling_dbfs`. With the default ceiling of 0 dBFS this
    is "would clip a fixed-point output"; a lower ceiling checks headroom."""
    peak = peak_dbfs(signal)
    if peak <= ceiling_dbfs:
        return None
    return Finding("peak_over_ceiling", f"peak {peak:.2f} dBFS is above the {ceiling_dbfs:.2f} dBFS ceiling")


def check_silence(signal, sample_rate: int, threshold_dbfs: float,
                  window_s: float | None = None) -> Finding | None:
    """Silence where there should be signal.

    With `window_s=None` a channel is silent if its RMS over the *whole* signal
    is below the threshold (a dead chain, a muted track, a disconnected input).
    With a window, a channel is silent if *any* window of that length, at any
    offset, is below it (a dropout partway through, a plugin that stopped).
    Each channel is judged separately, so one dead side of a stereo pair is found.

    Window energies come from a running sum of squares, which is exact to about
    1e-11 in power: fine for thresholds above roughly -100 dBFS, and the reason
    not to ask for much lower.
    """
    np = require_numpy()
    arr = _frames(signal)
    n = arr.shape[0]
    window = n if window_s is None else round(window_s * sample_rate)
    if window < 1 or window > n:
        raise ValueError(
            f"silence window of {window} samples does not fit in a {n}-sample signal"
        )
    running = np.concatenate([np.zeros((1, arr.shape[1])), np.cumsum(arr ** 2, axis=0)])
    mean_power = np.maximum((running[window:] - running[:-window]) / window, 0.0)  # (positions, channels)
    limit = 10.0 ** (threshold_dbfs / 10.0)
    quiet = mean_power < limit
    channels = [int(c) for c in np.nonzero(quiet.any(axis=0))[0]]
    if not channels:
        return None
    first = int(np.nonzero(quiet[:, channels[0]])[0][0])
    where = "its whole length" if window_s is None else f"a {window_s:g} s window starting at {first / sample_rate:.3f} s"
    return Finding(
        "silence",
        f"channel(s) {channels} fall below {threshold_dbfs:.1f} dBFS RMS over {where}",
    )


def safety_findings(signal, sample_rate: int, *, ceiling_dbfs: float = 0.0,
                    dc_threshold_dbfs: float = -50.0,
                    silence_threshold_dbfs: float | None = -80.0,
                    silence_window_s: float | None = None) -> list[Finding]:
    """Every safety check, in one list. Empty means nothing was found.

    If the signal is not finite, that is the only finding returned: the other
    measurements would be NaN or meaningless, and reporting them too would bury
    the real problem. Pass `silence_threshold_dbfs=None` when silence is the
    expected result (a muted rig), so the check does not fail the right answer.

    The defaults are starting points, not facts about guitars: 0 dBFS is where
    fixed-point output clips, -50 dBFS (about 0.3 %) is well above any DC a sane
    chain produces and well below anything audible as a thump, -80 dBFS is
    inaudible. A test with a reason to differ should say so.
    """
    nonfinite = check_finite(signal)
    if nonfinite:
        return [nonfinite]
    checks = [
        check_dc_offset(signal, dc_threshold_dbfs),
        check_peak_ceiling(signal, ceiling_dbfs),
    ]
    if silence_threshold_dbfs is not None:
        checks.append(check_silence(signal, sample_rate, silence_threshold_dbfs, silence_window_s))
    return [c for c in checks if c is not None]


# --------------------------------------------------------------------------- #
# Delay and null
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class DelayPeak:
    """One path from reference to test. `lag_samples` > 0 means the test signal
    contains the reference that many samples *later*. `gain` is the impulse-response
    value at that lag: 1.0 is a unity-gain copy, 0.5 a copy 6 dB down, and a
    negative value a polarity-inverted one."""
    lag_samples: int
    gain: float


def _impulse_response_estimate(reference: "np.ndarray", test: "np.ndarray") -> "np.ndarray":
    """Regularised deconvolution of `test` by `reference`, for lags
    -(len(ref)-1) .. len(test)-1.

    Why deconvolve rather than plain cross-correlation: correlating with a
    coloured stimulus smears every path by the stimulus's own autocorrelation.
    Pink noise, the natural stimulus here, has an autocorrelation with slow
    wiggles that put sidelobes at about a tenth of a peak — enough to be
    mistaken for echoes. Dividing by the reference's power spectrum removes the
    colouring, so each path is a sharp peak whose height is its actual gain.

    The regulariser stops bins the stimulus barely excited (above a band-limited
    sweep, say) from being amplified into noise. Computed by FFT: the direct sum
    is O(n*m) and a 10 s render is 480 000 samples.
    """
    np = require_numpy()
    n = len(reference) + len(test) - 1  # linear, not circular, so nothing wraps into view
    size = 1 << (n - 1).bit_length()
    x = np.fft.rfft(reference, size)
    y = np.fft.rfft(test, size)
    power = np.abs(x) ** 2
    full = np.fft.irfft(np.conj(x) * y / (power + 1e-3 * power.mean()), size)
    # Negative lags wrap to the end of the circular result.
    return np.concatenate([full[size - (len(reference) - 1):], full[:len(test)]])


def find_delays(reference, test, *, max_lag_samples: int | None = None, max_peaks: int = 1,
                min_relative_height: float = 0.1,
                min_separation_samples: int = 32) -> list[DelayPeak]:
    """Where in `test` does `reference` appear? Strongest peak first.

    One peak answers "how late is this signal" (latency, a delay line's time).
    Several answer "what echoes does it contain": a 375 ms delay on a noise
    stimulus shows as a peak at lag 0 and another at 375 ms, with the echo's
    `gain` read straight off.

    Works on any broadband stimulus (noise, a sweep, an impulse). It does not on
    a pure sine, which has no unique alignment. Accuracy is to the sample; there
    is no sub-sample interpolation, because a render's latency is a whole number
    of samples anyway.

    Lags at which less than half the reference overlaps the test signal are not
    searched, so a delay of more than half the length cannot be found.

    A peak is a local maximum of |gain| — not merely a large value, since the
    shoulders of a peak are large too — at least `min_relative_height` times the
    strongest, and at least `min_separation_samples` from a stronger one.
    """
    np = require_numpy()
    ref = _mono(reference, "reference")
    sig = _mono(test, "test")
    if max_peaks < 1:
        raise ValueError(f"max_peaks must be at least 1, got {max_peaks}")
    corr = _impulse_response_estimate(ref, sig)
    lags = np.arange(-(len(ref) - 1), len(sig))

    # At lag k only part of the reference still lies inside the test signal, so
    # the estimate reads low by that fraction (a copy delayed by a fifth of the
    # length reads 0.8). Undo it, so `gain` means what it says — but only where
    # at least half the reference overlaps: with a handful of samples the
    # correction would amplify noise into a "peak".
    overlap = np.minimum(len(ref), len(sig) - lags) - np.maximum(0, -lags)
    keep = overlap * 2 >= len(ref)
    if max_lag_samples is not None:
        keep &= np.abs(lags) <= max_lag_samples
    corr = corr[keep] * (len(ref) / overlap[keep])
    lags = lags[keep]
    if len(lags) == 0:
        return []
    magnitude = np.abs(corr)

    # Local maxima within +/- separation. Padding with -1 lets the edges compete.
    sep = max(1, min_separation_samples)
    padded = np.concatenate([np.full(sep, -1.0), magnitude, np.full(sep, -1.0)])
    neighbourhood_max = np.lib.stride_tricks.sliding_window_view(padded, 2 * sep + 1).max(axis=1)
    candidates = np.nonzero((magnitude == neighbourhood_max) & (magnitude > 0))[0]
    if len(candidates) == 0:
        return []
    candidates = candidates[np.argsort(-magnitude[candidates], kind="stable")]
    floor = min_relative_height * magnitude[candidates[0]]

    chosen: list[int] = []
    for i in candidates:
        if magnitude[i] < floor or len(chosen) == max_peaks:
            break
        if all(abs(int(lags[i]) - int(lags[j])) >= sep for j in chosen):
            chosen.append(int(i))
    return [DelayPeak(int(lags[i]), float(corr[i])) for i in chosen]


def estimate_delay(reference, test, *, max_lag_samples: int | None = None) -> DelayPeak:
    """The single strongest alignment of `reference` within `test`. See `find_delays`.

    Raises if nothing correlates (either signal silent, or nothing within
    `max_lag_samples`), rather than returning lag 0 as if that had been measured.
    """
    peaks = find_delays(reference, test, max_lag_samples=max_lag_samples, max_peaks=1)
    if not peaks:
        raise ValueError("no correlation between the signals within the allowed lag (is either one silent?)")
    return peaks[0]


@dataclass(frozen=True)
class NullResult:
    """The outcome of subtracting one signal from another.

    `residual_dbfs` is the RMS of (reference - test) over the compared frames.
    `null_depth_db` is how far below the reference that is — the number that
    survives a change of overall level; `inf` when the signals are identical.
    `lag_samples` is the alignment that was applied (0 when not aligning).
    """
    residual_dbfs: float
    reference_dbfs: float
    null_depth_db: float
    lag_samples: int
    compared_frames: int


def null_test(reference, test, *, align: bool = False,
              max_lag_samples: int | None = None) -> NullResult:
    """Subtract `test` from `reference` and report what is left.

    Without `align` the two must be the same shape: a null test that quietly
    trims one signal would pass a render that is missing its last second.
    With `align`, the time offset is found by finding the strongest
    path between the channel mixdowns and the overlap is compared. Alignment only corrects *time*; a gain
    difference stays in the residual, which is the point.

    `align` can hide a real change — a chain that has gained 20 ms of latency
    will null perfectly once aligned. Check `lag_samples` where latency matters.
    """
    np = require_numpy()
    ref = _frames(reference)
    sig = _frames(test)
    if ref.shape[1] != sig.shape[1]:
        raise ValueError(f"channel counts differ: {ref.shape[1]} vs {sig.shape[1]}")

    lag = 0
    if align:
        lag = estimate_delay(ref.sum(axis=1), sig.sum(axis=1), max_lag_samples=max_lag_samples).lag_samples
    elif ref.shape != sig.shape:
        raise ValueError(
            f"lengths differ: {ref.shape[0]} vs {sig.shape[0]} frames; pass align=True to compare the overlap"
        )

    # test[n + lag] corresponds to reference[n].
    ref_start, sig_start = max(0, -lag), max(0, lag)
    frames = min(ref.shape[0] - ref_start, sig.shape[0] - sig_start)
    if frames < 1:
        raise ValueError(f"no overlap between the signals at lag {lag}")
    a = ref[ref_start:ref_start + frames]
    b = sig[sig_start:sig_start + frames]
    residual = _power_to_dbfs(float(np.mean((a - b) ** 2)))
    level = _power_to_dbfs(float(np.mean(a ** 2)))
    return NullResult(residual, level, level - residual, lag, frames)


# --------------------------------------------------------------------------- #
# Frequency response
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class ResponsePoint:
    """Gain at one frequency. `coherence` (0-1) says how much of the output at
    that frequency is explained by the input: near 1 is a trustworthy linear
    measurement, low means noise, distortion or time-variation got in."""
    frequency_hz: float
    gain_db: float
    coherence: float


def frequency_response(stimulus, response, sample_rate: int, frequencies_hz: Sequence[float],
                       *, segment_length: int = 8192) -> list[ResponsePoint]:
    """Gain of a system at chosen frequencies, from its input and output signals.

    The H1 estimator: H(f) = Pxy / Pxx, the cross-spectrum of input and output
    over the input's power spectrum, averaged over Hann-windowed, 50 %-overlapping
    segments (Welch). Averaging is what makes it robust: noise at the output that
    is unrelated to the input averages away, instead of landing in the answer as
    it does in a single Y/X division. Use a broadband stimulus that is steady
    over time, such as `signals.pink_noise`. A sweep is the wrong tool here: each
    segment sees only part of it, so most frequencies get little energy.

    Latency much longer than a small fraction of `segment_length` biases the
    result low, because input and output segments then no longer line up. Remove
    bulk latency first (`estimate_delay`) or use a longer segment.

    Gain at a requested frequency is interpolated between FFT bins (resolution
    sample_rate / segment_length), in dB. Frequencies where the stimulus had no
    energy come back as NaN gain with zero coherence — "not measured" — not as a
    number.
    """
    np = require_numpy()
    x = _mono(stimulus, "stimulus")
    y = _mono(response, "response")
    if len(x) != len(y):
        raise ValueError(f"stimulus and response differ in length: {len(x)} vs {len(y)} samples")
    hop = segment_length // 2
    if segment_length < 16 or len(x) < segment_length + 3 * hop:
        # One or two segments make coherence trivially 1 and the average meaningless.
        raise ValueError(
            f"{len(x)} samples is too short for four averaged segments of {segment_length}; "
            f"use more signal or a smaller segment_length"
        )
    nyquist = sample_rate / 2
    freqs = np.asarray(frequencies_hz, dtype=np.float64)
    if freqs.size == 0 or np.any(freqs < 0) or np.any(freqs > nyquist):
        raise ValueError(f"frequencies_hz must be non-empty and within 0..{nyquist} Hz")

    window = np.hanning(segment_length + 1)[:-1]  # periodic Hann, correct for spectral averaging
    view = np.lib.stride_tricks.sliding_window_view
    xs = np.fft.rfft(view(x, segment_length)[::hop] * window, axis=1)
    ys = np.fft.rfft(view(y, segment_length)[::hop] * window, axis=1)
    pxx = np.mean(np.abs(xs) ** 2, axis=0)
    pyy = np.mean(np.abs(ys) ** 2, axis=0)
    pxy = np.mean(np.conj(xs) * ys, axis=0)

    measured = pxx > 1e-12 * pxx.max()
    with np.errstate(divide="ignore", invalid="ignore"):
        gain_db = np.where(measured, 20 * np.log10(np.maximum(np.abs(pxy / pxx), 1e-12)), np.nan)
        coherence = np.where(measured & (pyy > 0), np.abs(pxy) ** 2 / (pxx * pyy), 0.0)
    bin_freqs = np.arange(len(pxx)) * sample_rate / segment_length
    gains = np.interp(freqs, bin_freqs, gain_db)
    cohs = np.interp(freqs, bin_freqs, coherence)
    return [ResponsePoint(float(f), float(g), float(c)) for f, g, c in zip(freqs, gains, cohs)]


# --------------------------------------------------------------------------- #
# Spectral balance
# --------------------------------------------------------------------------- #

# Roughly: low end, low mids, mids, upper mids, presence, air. Coarse on purpose:
# the question is "is this tone still the same shape", not a fine spectrum.
DEFAULT_BAND_EDGES_HZ = (20.0, 80.0, 250.0, 800.0, 2500.0, 8000.0, 20000.0)


@dataclass(frozen=True)
class BandEnergy:
    """RMS level of the part of a signal between `low_hz` (inclusive) and `high_hz`."""
    low_hz: float
    high_hz: float
    level_dbfs: float


def band_energies(signal, sample_rate: int,
                  edges_hz: Sequence[float] = DEFAULT_BAND_EDGES_HZ) -> list[BandEnergy]:
    """Energy in each band between consecutive `edges_hz`, as an RMS level in dBFS.

    From one Hann-windowed FFT of the whole signal, scaled so that the sum of all
    bands is the signal's mean-square power (Parseval) — a 0 dBFS in-band sine
    therefore reads -3.01 dBFS, the same as `rms_dbfs`. Multichannel signals are
    averaged by power. Energy outside the outermost edges (including DC) is not
    counted in any band.
    """
    np = require_numpy()
    arr = _frames(signal)
    edges = list(edges_hz)
    if len(edges) < 2 or any(b <= a for a, b in zip(edges, edges[1:])):
        raise ValueError("edges_hz must be at least two strictly increasing frequencies")
    if edges[0] < 0 or edges[-1] > sample_rate / 2:
        raise ValueError(f"edges_hz must lie within 0..{sample_rate / 2} Hz (Nyquist)")

    n = arr.shape[0]
    window = np.hanning(n + 1)[:-1]
    spectrum = np.fft.rfft(arr * window[:, None], axis=0)
    power = np.abs(spectrum) ** 2 / (n * np.sum(window ** 2))
    power[1:] *= 2  # one-sided: fold in the mirrored negative-frequency half...
    if n % 2 == 0:
        power[-1] /= 2  # ...except Nyquist, which has no mirror (DC, row 0, is already single)
    power = power.mean(axis=1)
    freqs = np.arange(len(power)) * sample_rate / n
    return [
        BandEnergy(lo, hi, _power_to_dbfs(float(power[(freqs >= lo) & (freqs < hi)].sum())))
        for lo, hi in zip(edges, edges[1:])
    ]


def band_differences_db(reference: Sequence[BandEnergy], test: Sequence[BandEnergy]) -> list[float]:
    """Per band, `test` minus `reference` in dB, for a tone-balance comparison.

    Bands that are silent in both read 0 (no change); silent in only one read
    +/-inf, which is a real difference and should fail a tolerance check.
    """
    if [(b.low_hz, b.high_hz) for b in reference] != [(b.low_hz, b.high_hz) for b in test]:
        raise ValueError("band layouts differ; compute both with the same edges_hz")
    out = []
    for r, t in zip(reference, test):
        if r.level_dbfs == t.level_dbfs:
            out.append(0.0)  # also covers -inf == -inf, where subtraction would give NaN
        else:
            out.append(t.level_dbfs - r.level_dbfs)
    return out
