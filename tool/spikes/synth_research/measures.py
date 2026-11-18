"""The measures the synth research (#166) applies to every render.

Built on `../plugin_research/bench.py` (its guitar, its pitch tracker, its
spectra), so a figure here means what the same figure meant for #153 and #155.
Every function takes the dry guitar `x` (mono), its events, and a render `y`
(frames, or frames x channels; channel 0 is read where one channel is needed).

- **junk**, single notes and chords: the share of the output's energy that is
  on no harmonic of the notes played *or of their sub-octaves* (the grid is the
  harmonics of f/2, so an octave up or down is not junk), dB. A linear effect
  (a chorus, a filter, a reverb) leaves it where the dry guitar has it; an
  effect that makes intermodulation (a fuzz, a ring modulator, a tracker that
  guesses wrong) raises it, and raises it most on chords. Read against the
  dry row: that is the guitar's own pluck noise and decay.
- **smear**: over a held, perfectly steady 120 Hz note, how much of each
  partial's energy (harmonics 2 to 10, within 5 %) lies outside 0.25 % of it,
  dB. A dry note scores far down; detuned copies (a chorus, an ensemble)
  spread the lines and raise it. The thickness a string machine is known for
  is mostly this.
- **sub** and **up**: on single notes, the share of energy on the odd
  multiples of f/2 (a sub-octave's own partials) and on the octave above's
  partials that the note did not already have (odd multiples of f... are the
  note's own, so: the 2f grid's share minus the dry note's), dB.
- **residual**: whether a chord comes out as the sum of its strings: each
  string of a chord is rendered alone and the renders summed, and what is left
  of the chord's own render after that sum is taken away is given against it,
  dB (`residual`). Below -100 dB the effect is linear: it treats any chord
  exactly as it treats single notes. A shifter, fuzz or tracker that mixes
  notes leaves what it mixed; a level-dependent effect (a gate, a compressor,
  an envelope filter) leaves its dependence on level, which is not harm. A
  phase vocoder leaves a lot even when it is clean, since it does not keep
  each partial's phase, so for shifters junk on chords is the figure to read.
- **rise**, after a gap and legato (`swell_signal`): median ms from each pluck
  to 90 % of the output's peak over that note, for four notes each followed by
  silence and four played legato on one string. A dry pluck is a few ms; a
  swell is the point of the effect, and a swell that only opens from silence
  shows it here.
- **tail**: how long the output lasts after the input has stopped, to
  -80 dBFS, s.
- **L/R**: correlation of the two outputs, 1 being mono.
- **onset**: when a switched-on 500 Hz burst first reaches -20 dB of its later
  level in the output, samples (bench.onset_delay).
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "plugin_research"))
import bench  # noqa: E402
from bench import SR  # noqa: E402


def mono(y: np.ndarray) -> np.ndarray:
    y = np.asarray(y, dtype=np.float64)
    return y[:, 0] if y.ndim > 1 else y


def _junk(y: np.ndarray, start: float, stop: float, freqs: list[float], rel: float) -> float:
    f, p = bench.spectrum(y, start, stop)
    band = (f >= 30) & (f <= 5000)
    f, p = f[band], p[band]
    allowed = bench.harmonics([fr / 2 for fr in freqs])
    total = p.sum()
    return bench.db((total - bench.energy_near(f, p, allowed, rel=rel)) / max(total, 1e-30))


def junk(y: np.ndarray, events: list[bench.Event], delay: int = 0) -> tuple[float, float]:
    """(single notes, median; chords, median of the three), dB."""
    y = mono(y)
    y = np.concatenate([y[delay:], np.zeros(delay)])
    notes = [_junk(y, e.onset + 0.15, e.onset + 0.75, e.freqs, 0.012) for e in events if e.kind == "note"]
    chords = [_junk(y, e.onset + 0.2, e.onset + 1.4, e.freqs, 0.006) for e in events if e.kind == "chord"]
    return float(np.median(notes)), float(np.median(chords))


def octave_shares(x: np.ndarray, y: np.ndarray, events: list[bench.Event], delay: int = 0) -> tuple[float, float]:
    """(sub, up) on single notes, dB: the sub-octave's own partials, and how
    much more of the energy sits on the 2f grid than in the dry note."""
    y = np.concatenate([mono(y)[delay:], np.zeros(delay)])
    subs, ups = [], []
    for e in (e for e in events if e.kind == "note"):
        f0 = e.freqs[0]
        a, b = e.onset + 0.15, e.onset + 0.75
        shares = []
        for sig in (x, y):
            f, p = bench.spectrum(sig, a, b)
            band = (f >= 30) & (f <= 5000)
            f, p = f[band], p[band]
            tot = max(p.sum(), 1e-30)
            odd_sub = [k * f0 / 2 for k in range(1, 400, 2) if k * f0 / 2 <= 5000]
            even = [k * f0 for k in range(2, 400, 2) if k * f0 <= 5000]
            shares.append((bench.energy_near(f, p, odd_sub) / tot, bench.energy_near(f, p, even) / tot))
        subs.append(bench.db(shares[1][0]))
        ups.append(bench.db(shares[1][1]) - bench.db(shares[0][1]))
    return float(np.median(subs)), float(np.median(ups))


def steady(seconds: float = 6.0) -> np.ndarray:
    """A held 120 Hz note with exact harmonics, as pedals.steady()."""
    t = np.arange(int(seconds * SR)) / SR
    out = np.zeros(len(t))
    for k in range(1, 50):
        out += np.sin(2 * np.pi * k * 120.0 * t + k) / k
    return 0.3 * out / np.max(np.abs(out))


def smear(y: np.ndarray) -> float:
    """Over seconds 1.5 to 5.5 of a steady() render: energy within 5 % of
    harmonics 2..10 but outside 0.25 % of them, as a share of that within 5 %, dB."""
    y = mono(y)[int(1.5 * SR):int(5.5 * SR)]
    win = np.hanning(len(y))
    p = np.abs(np.fft.rfft(y * win)) ** 2
    f = np.fft.rfftfreq(len(y), 1 / SR)
    core = wide = 0.0
    for k in range(2, 11):
        c = 120.0 * k
        wide += p[np.abs(f - c) <= 0.05 * c].sum()
        core += p[np.abs(f - c) <= 0.0025 * c].sum()
    return bench.db(max(wide - core, 1e-30) / max(wide, 1e-30))


def tail(x: np.ndarray, y: np.ndarray) -> float:
    end_in = np.nonzero(np.abs(x) > 1e-3)[0][-1]
    env = bench.envelope(np.asarray(y), 10)
    above = np.nonzero(env > 1e-4)[0]
    return float((above[-1] - end_in) / SR) if len(above) and above[-1] > end_in else 0.0


def lr(y: np.ndarray) -> float | None:
    y = np.asarray(y)
    if y.ndim < 2 or y.shape[1] < 2:
        return None
    a, b = y[:, 0], y[:, 1]
    if np.std(a) == 0 or np.std(b) == 0:
        return float("nan")
    return float(np.corrcoef(a, b)[0, 1])


def level(x: np.ndarray, y: np.ndarray) -> float:
    y = mono(y)
    n = min(len(x), len(y))
    return bench.db(np.mean(y[:n] ** 2) / np.mean(x[:n] ** 2))


def stress() -> np.ndarray:
    """About 70 s to shake out NaN and runaway feedback: the guitar three
    times, a full-scale noise burst, a DC step, a full-scale 30 Hz sine, and
    ten seconds of silence."""
    x, _ = bench.guitar()
    rng = np.random.default_rng(3)
    parts = [x, x * 2.5, x * 0.1, rng.standard_normal(2 * SR) * 0.7,
             np.concatenate([np.full(SR, 0.5), np.zeros(SR)]),
             0.99 * np.sin(2 * np.pi * 30 * np.arange(2 * SR) / SR), np.zeros(10 * SR)]
    return np.clip(np.concatenate(parts), -1, 1)


def sane(y: np.ndarray) -> str:
    """'ok', or what went wrong: NaN/inf, or a peak over +20 dBFS, or still
    ringing at the end of the stress signal's silence."""
    y = np.asarray(y)
    if not np.all(np.isfinite(y)):
        i = int(np.argmax(~np.isfinite(y).reshape(len(y), -1).all(axis=1)))
        return f"NaN/inf from {i / SR:.2f} s"
    peak = float(np.max(np.abs(y)))
    if peak > 10:
        return f"peak {20 * np.log10(peak):+.0f} dBFS"
    last = mono(y)[-SR:]
    if np.sqrt(np.mean(last ** 2)) > 1e-3:
        return f"still {20 * np.log10(np.sqrt(np.mean(last ** 2))):.0f} dBFS after 9 s of silence"
    return "ok"


# --- chords: does the effect treat a chord as the sum of its notes? -----------

CHORDS = {"E major (open)": ["E2", "B2", "E3", "G#3", "B3", "E4"],
          "A major triad": ["A2", "C#3", "E3"]}


def chord_parts(chord: str, seconds: float = 3.0) -> tuple[np.ndarray, list[np.ndarray]]:
    """A strummed chord at 0.1 s, and each of its strings alone, exactly as
    they sound inside it (same pluck, same time), all at -10 dBFS's scale."""
    notes = CHORDS[chord]
    n = int(seconds * SR)
    parts = []
    for i, name in enumerate(notes):
        s = bench._string(bench.NOTE[name], seconds - 0.2, level=0.75, seed=60 + i)
        x = np.zeros(n)
        at = int((0.1 + 0.012 * i) * SR)
        x[at:at + len(s)] += s[:n - at]
        parts.append(x)
    total = sum(parts)
    scale = 10 ** (-10 / 20) / np.max(np.abs(total))
    return total * scale, [p * scale for p in parts]


def residual(y_chord: np.ndarray, y_parts: list[np.ndarray]) -> float:
    """What is left of the chord's render after the renders of its notes are
    taken away, against the chord's render, dB (0.3 to 2.6 s, channel 0).
    A linear effect, however it modulates, leaves nothing (the figure is the
    float rounding, below -100 dB); a shifter, fuzz or ring modulator that
    mixes notes with each other leaves what it mixed. Level-dependent effects
    (a gate, a compressor) leave their dependence on level."""
    a, b = int(0.3 * SR), int(2.6 * SR)
    yc = mono(y_chord)[a:b]
    ys = sum(mono(p)[a:b] for p in y_parts)
    return bench.db(np.sum((yc - ys) ** 2) / max(np.sum(yc ** 2), 1e-30))


def repeat_difference(y1: np.ndarray, y2: np.ndarray) -> float:
    """Two renders of the same input against each other, dB (-inf: identical)."""
    n = min(len(y1), len(y2))
    d = np.sum((mono(y1)[:n] - mono(y2)[:n]) ** 2)
    return bench.db(d / max(np.sum(mono(y1)[:n] ** 2), 1e-30)) if d > 0 else float("-inf")


# --- swells: after a gap, and legato ------------------------------------------

def swell_signal() -> tuple[np.ndarray, list[tuple[float, float, str]]]:
    """Four notes each muted after 1 s and followed by 0.5 s of silence, then
    four legato on one string (each pluck stops the last): (signal, [(onset,
    length, 'gap'|'legato')])."""
    parts, marks, at = [], [], 0.3
    x = np.zeros(int(11 * SR))
    for name in ("A2", "D3", "G3", "B3"):
        s = bench._string(bench.NOTE[name], 1.0, seed=80)
        s[-int(0.02 * SR):] *= np.linspace(1, 0, int(0.02 * SR))
        i = int(at * SR)
        x[i:i + len(s)] += s
        marks.append((at, 1.0, "gap"))
        at += 1.5
    for name in ("E3", "F#3", "G#3", "A3"):
        s = bench._string(bench.NOTE[name], 0.8, seed=81)
        s[-int(0.005 * SR):] *= np.linspace(1, 0, int(0.005 * SR))
        i = int(at * SR)
        x[i:i + len(s)] += s
        marks.append((at, 0.8, "legato"))
        at += 0.8
    return x * 10 ** (-10 / 20) / np.max(np.abs(x)), marks


def swell_rise(y: np.ndarray, marks) -> tuple[float, float]:
    """Median ms from each pluck to 90 % of the output's peak over that note:
    (after a gap, legato)."""
    y = mono(y)
    out = {"gap": [], "legato": []}
    for at, length, kind in marks:
        seg = bench.envelope(y[int(at * SR):int((at + length) * SR)], 5.0)
        if seg.max() > 1e-6:
            out[kind].append(1000 * np.argmax(seg >= 0.9 * seg.max()) / SR)
    return tuple(float(np.median(v)) if v else float("nan") for v in (out["gap"], out["legato"]))

