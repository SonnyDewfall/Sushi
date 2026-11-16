"""The shared half of the plugin research renders (#153, #155).

`render()` runs one plugin over a signal in `host.c`, a bench host that loads
a plugin as Sushi 1.3.0 does (its README says how far that goes). `guitar()`
is the test signal: this repository holds no DI recording, so it is
synthesised. Everything is 48 kHz float.

Needs gcc, liblilv-dev, lv2-dev and numpy. Nothing here needs a Sushi.
"""

from __future__ import annotations

import atexit
import json
import os
import shutil
import struct
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

SR = 48000
HERE = Path(__file__).resolve().parent
_WORK = Path(tempfile.mkdtemp(prefix="plugin-research-"))
atexit.register(shutil.rmtree, _WORK, True)
_HOST: Path | None = None


def host() -> Path:
    """Build host.c once per process, into a temporary folder."""
    global _HOST
    if _HOST is None:
        flags = subprocess.run(["pkg-config", "--cflags", "--libs", "lilv-0"], check=True,
                               capture_output=True, text=True).stdout.split()
        out = _WORK / "host"
        subprocess.run(["gcc", "-O2", "-o", str(out), str(HERE / "host.c"), *flags], check=True)
        _HOST = out
    return _HOST


@dataclass
class Render:
    uri: str
    out: np.ndarray | None          # frames x channels, or None when it did not run
    info: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.out is not None

    @property
    def cpu_percent(self) -> float:
        """Time inside run() as a share of the audio's own length, one core."""
        return 100.0 * self.info["run_seconds"] / (self.info["frames"] / SR)

    @property
    def latency_port(self) -> float | None:
        outputs = self.info.get("outputs", {})
        for name in ("latency", "LATENCY", "lat", "Latency"):
            if name in outputs:
                return outputs[name]
        return None


def render(uri: str, x: np.ndarray, controls: dict[str, float] | None = None, repeats: int = 1) -> Render:
    """Run `uri` over `x` (frames, or frames x channels; the track's width is
    x's), with `controls` by port symbol in the port's own units. The time is
    the fastest of `repeats` renders; the output is the first's."""
    x = np.asarray(x, dtype=np.float32)
    if x.ndim == 1:
        x = x[:, None]
    channels = x.shape[1]
    src, dst = _WORK / "in.f32", _WORK / "out.f32"
    src.write_bytes(np.ascontiguousarray(x).tobytes())
    args = [str(host()), uri, str(src), str(dst), str(channels)]
    args += [f"{k}={v}" for k, v in (controls or {}).items()]
    first: Render | None = None
    best = None
    for _ in range(repeats):
        report = _WORK / "report.json"
        report.unlink(missing_ok=True)
        done = subprocess.run(args, capture_output=True, text=True, env={**os.environ, "HOST_JSON": str(report)})
        text = report.read_text() if report.exists() else ""
        try:
            info = json.loads(text)
        except ValueError:
            info = {"error": f"no report (exit {done.returncode})", "partial": text}
        info["stderr"] = (done.stdout + done.stderr)[-2000:]
        if done.returncode != 0:
            return Render(uri, None, info)
        y = np.frombuffer(dst.read_bytes(), dtype=np.float32).reshape(-1, channels).astype(np.float64)
        if first is None:
            first = Render(uri, y, info)
        best = info["run_seconds"] if best is None else min(best, info["run_seconds"])
    first.info["run_seconds"] = best
    return first


# --- signals ----------------------------------------------------------------

def _string(f0: float, duration: float, *, level: float = 1.0, bend: tuple[float, float, float] | None = None,
            seed: int = 0) -> np.ndarray:
    """One plucked note: decaying, slightly stretched partials shaped by a
    pluck near the bridge and a pickup, and a short noise transient. `bend` is
    (start s, length s, semitones): a bend up and held."""
    n = int(duration * SR)
    t = np.arange(n) / SR
    ratio = np.ones(n)
    if bend is not None:
        start, length, semis = bend
        ramp = np.clip((t - start) / length, 0, 1)
        ratio = 2 ** (semis * (0.5 - 0.5 * np.cos(np.pi * ramp)) / 12)
    out = np.zeros(n)
    inharmonicity = 1.2e-4 if f0 < 150 else 0.6e-4
    k = 1
    while k * f0 * 1.02 < 7000:
        fk = k * f0 * np.sqrt(1 + inharmonicity * k * k)
        amp = abs(np.sin(np.pi * k * 0.13)) * abs(np.sin(np.pi * k * 0.24)) / k ** 0.9
        tau = 2.2 / (1 + 0.012 * fk ** 0.9)
        phase = 2 * np.pi * np.cumsum(fk * ratio) / SR
        out += amp * np.exp(-t / tau) * np.sin(phase + 0.7 * k)
        k += 1
    noise = np.random.default_rng(seed).standard_normal(n) * np.exp(-t / 0.004)
    noise = np.convolve(noise, np.ones(6) / 6, mode="same")
    out += 0.15 * noise
    attack = np.clip(t / 0.0015, 0, 1)
    return level * out * attack


NOTE = {"E2": 82.41, "A2": 110.0, "B2": 123.47, "C#3": 138.59, "D3": 146.83, "E3": 164.81, "F#3": 185.0,
        "G3": 196.0, "G#3": 207.65, "A3": 220.0, "B3": 246.94, "C#4": 277.18, "D#4": 311.13, "E4": 329.63,
        "A4": 440.0}


@dataclass
class Event:
    kind: str            # "note", "run", "bend", "chord"
    name: str
    onset: float         # seconds
    freqs: list[float]   # the notes, Hz
    length: float


def guitar() -> tuple[np.ndarray, list[Event]]:
    """About 22 s of plucked guitar, peaking at -10 dBFS, and what is where.

    Single notes up the neck (E2 to A4), a run of eighth notes, a bend, an
    E major open chord strummed, an A major triad with no doubled octaves
    (the one the chord figures are measured on) and an E5 power chord."""
    events: list[Event] = []
    total = int(22.5 * SR)
    x = np.zeros(total)

    def place(sig: np.ndarray, at: float) -> None:
        i = int(at * SR)
        x[i:i + len(sig)] += sig[:total - i]

    at, seed = 0.25, 1
    for name in ("E2", "A2", "D3", "G3", "B3", "E4", "A4"):
        place(_string(NOTE[name], 1.5, seed=seed), at)
        events.append(Event("note", name, at, [NOTE[name]], 1.5))
        at += 1.5
        seed += 1
    for name in ("E3", "F#3", "G#3", "A3", "B3", "C#4", "D#4", "E4"):
        place(_string(NOTE[name], 0.25, seed=seed) * np.hanning(int(0.25 * SR) * 2)[int(0.25 * SR):] ** 0.2, at)
        events.append(Event("run", name, at, [NOTE[name]], 0.25))
        at += 0.25
        seed += 1
    at += 0.3
    place(_string(NOTE["A3"], 2.0, bend=(0.4, 0.25, 2.0), seed=seed), at)
    events.append(Event("bend", "A3 bent to B3", at, [NOTE["A3"]], 2.0))
    at += 2.2
    for chord, notes, length in (("E major (open)", ["E2", "B2", "E3", "G#3", "B3", "E4"], 2.6),
                                 ("A major triad (A2 C#3 E3)", ["A2", "C#3", "E3"], 2.6),
                                 ("E5 power chord", ["E2", "B2", "E3"], 2.2)):
        for i, name in enumerate(notes):
            place(_string(NOTE[name], length, level=0.75, seed=seed + i), at + 0.012 * i)
        events.append(Event("chord", chord, at, [NOTE[n] for n in notes], length))
        at += length + 0.2
        seed += 10
    x = x[:int((at + 0.5) * SR)]
    x *= 10 ** (-10 / 20) / np.max(np.abs(x))
    return x, events


def tone_burst(freq: float = 500.0, at: float = 0.5, length: float = 0.6, total: float = 1.6) -> np.ndarray:
    """A sine switched on at `at` with no ramp, for timing an onset."""
    t = np.arange(int(total * SR)) / SR
    return 0.3 * np.sin(2 * np.pi * freq * (t - at)) * ((t >= at) & (t < at + length))


# --- measurement --------------------------------------------------------------

def envelope(x: np.ndarray, smooth_ms: float = 2.0) -> np.ndarray:
    x = np.abs(np.asarray(x, dtype=np.float64))
    if x.ndim > 1:
        x = x.max(axis=1)
    n = max(1, int(smooth_ms * SR / 1000))
    return np.convolve(x, np.ones(n) / n, mode="same")


def onset_delay(x: np.ndarray, y: np.ndarray, at: float, threshold_db: float = -20.0) -> float | None:
    """Samples from `at` (a burst's onset in x) to the first point where y's
    envelope reaches `threshold_db` below its own level 0.3 s later."""
    env = envelope(y)
    i0 = int(at * SR)
    steady = np.median(env[i0 + int(0.3 * SR): i0 + int(0.4 * SR)])
    if steady <= 1e-6:
        return None
    hits = np.nonzero(env[i0 - 10:] >= steady * 10 ** (threshold_db / 20))[0]
    return float(hits[0] - 10) if len(hits) else None


def xcorr_delay(x: np.ndarray, y: np.ndarray, max_lag: int = 9600) -> tuple[int, float]:
    """The lag of y behind x at which they correlate best, and how well
    (normalised, -1..1): for plugins that keep the waveform."""
    x = np.asarray(x, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64)
    if y.ndim > 1:
        y = y.mean(axis=1)
    if x.ndim > 1:
        x = x.mean(axis=1)
    n = 1 << int(np.ceil(np.log2(len(x) + max_lag)))
    c = np.fft.irfft(np.fft.rfft(y, n) * np.conj(np.fft.rfft(x, n)), n)[:max_lag]
    lag = int(np.argmax(np.abs(c)))
    norm = np.sqrt(np.sum(x * x) * np.sum(y * y)) or 1.0
    return lag, float(c[lag] / norm)


def yin(frame: np.ndarray, fmin: float = 30.0, fmax: float = 1500.0, threshold: float = 0.15) -> float | None:
    """The YIN pitch of one frame, Hz, or None when it finds no period."""
    frame = np.asarray(frame, dtype=np.float64)
    w = len(frame) // 2
    taus = np.arange(int(SR / fmax), int(SR / fmin))
    taus = taus[taus < w]
    # d(tau) = sum (x[j] - x[j+tau])^2 over j < w, by energies and one FFT correlation
    n = 1 << int(np.ceil(np.log2(len(frame) + w)))
    cross = np.fft.irfft(np.fft.rfft(frame, n) * np.conj(np.fft.rfft(frame[:w], n)), n)
    sq = np.concatenate([[0.0], np.cumsum(frame * frame)])
    d = sq[w] + (sq[taus + w] - sq[taus]) - 2 * cross[taus]
    cum = np.cumsum(d) / np.arange(1, len(d) + 1)
    dn = d / np.where(cum > 0, cum, 1)
    below = np.nonzero(dn < threshold)[0]
    if not len(below):
        return None
    i = below[0]
    while i + 1 < len(dn) and dn[i + 1] < dn[i]:
        i += 1
    if 0 < i < len(dn) - 1:
        a, b, c = dn[i - 1], dn[i], dn[i + 1]
        shift = 0.5 * (a - c) / (a - 2 * b + c) if (a - 2 * b + c) != 0 else 0.0
    else:
        shift = 0.0
    return SR / (taus[i] + shift)


def pitch_track(y: np.ndarray, start: float, stop: float, hop_ms: float = 10.0, fmin: float = 30.0) -> list[float | None]:
    frame = int(3 * SR / fmin)
    out = []
    t = start
    while t + frame / SR <= stop:
        i = int(t * SR)
        seg = y[i:i + frame]
        out.append(yin(seg, fmin=fmin) if np.max(np.abs(seg)) > 1e-4 else None)
        t += hop_ms / 1000
    return out


def spectrum(y: np.ndarray, start: float, stop: float) -> tuple[np.ndarray, np.ndarray]:
    seg = y[int(start * SR):int(stop * SR)]
    win = np.hanning(len(seg))
    mag = np.abs(np.fft.rfft(seg * win)) ** 2
    return np.fft.rfftfreq(len(seg), 1 / SR), mag


def energy_near(freqs: np.ndarray, power: np.ndarray, centres: list[float], rel: float = 0.012,
                floor_hz: float = 3.0) -> float:
    mask = np.zeros(len(freqs), bool)
    for c in centres:
        width = max(rel * c, floor_hz)
        mask |= np.abs(freqs - c) <= width
    return float(power[mask].sum())


def harmonics(fundamentals: list[float], top: float = 5000.0) -> list[float]:
    out = []
    for f in fundamentals:
        k = 1
        while k * f <= top:
            out.append(k * f)
            k += 1
    return out


def db(v: float) -> float:
    return 10 * np.log10(max(v, 1e-30))


def write_wav(path: Path, x: np.ndarray) -> None:
    x = np.asarray(x, dtype=np.float32)
    if x.ndim == 1:
        x = x[:, None]
    data = x.tobytes()
    ch = x.shape[1]
    header = (b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVEfmt "
              + struct.pack("<IHHIIHH", 16, 3, ch, SR, SR * ch * 4, ch * 4, 32)
              + b"data" + struct.pack("<I", len(data)))
    path.write_bytes(header + data)
