"""#155: candidate pedals over the same guitar, one at a time.

    python3 tool/spikes/plugin_research/pedals.py [--wav DIR] [--only TEXT]
        [--rubberband4 DIR]

Each pick runs on a track as wide as its own outputs (one channel, or two
with the guitar in both), as Sushi would run it on a mono or a stereo track,
over the guitar of bench.guitar() and four seconds of silence after it.
The settings are a musical guess, written next to each pick; they are not
tuned.

Printed per pick (the README, under #155, says the same):

- in/out: its audio ports; whether the bench host, which offers exactly
  Sushi 1.3.0's host features, loads it;
- latency: its latency port; the onset of a 500 Hz burst in the output
  (samples to -20 dB of its level, at 48 kHz); and the lag of best
  correlation with the dry guitar (samples; blank below a correlation of
  0.3), which for a filter is its phase, not a delay;
- CPU: time inside run() as a share of real time, one core;
- level: output RMS against input, dB;
- bright: the share of energy above 2 kHz, output against input, dB;
- tail: how long the output lasts past the input's end, to -80 dBFS (s);
- L/R: correlation of the two outputs (1 is mono);
- wobble: over a held 120 Hz note that does not move by itself (steady()),
  the strongest rate (Hz) at which the output's level moves and its swing
  (dB, 5th to 95th percentile), and the same for its spectral centroid
  (swing as % of its mean);
- sweep: how much brighter each single note is just after the pick than
  half a second on (spectral centroid, 20-120 ms against 0.6-1.0 s), output
  against input; above 1 the pedal opens on the pick and closes as the note
  dies, as an envelope filter or auto-wah does.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bench  # noqa: E402
from bench import SR  # noqa: E402

G = "http://guitarix.sourceforge.net/plugins/"
C = "http://calf.sourceforge.net/plugins/"
MDA = "http://drobilla.net/plugins/mda/"
FOMP = "http://drobilla.net/plugins/fomp/"

# (kind, name, uri, controls); controls by port symbol, in the port's units
PICKS = [
    ("fuzz", "GxFuzzFaceJH2", G + "gx_fuzzface_#_fuzzface_", {"FUZZ": 0.7, "LEVEL": 0.5}),
    ("fuzz", "GxBigMuffPi", G + "gx_bmp_#_bmp_", {"SUSTAIN": 0.7, "TONE": 0.5, "VOLUME": 0.5}),
    ("fuzz", "GxFuzzMaster", G + "gx_fumaster_#_fumaster_", {"TONE": 0.5, "VOLUME": 0.3, "WET_DRY": 100}),
    ("fuzz", "GxColorSound Tonebender", G + "gx_cstb_#_cstb_", {"ATTACK": 0.7, "LEVEL": 0.4, "WET_DRY": 100}),
    ("phaser", "GxPhaser", G + "gx_phaser#_phaser", {"dry_wet": 50, "speed": 0.5}),
    ("phaser", "Calf Phaser", C + "Phaser", {"mod_rate": 0.5}),
    ("phaser", "fomp CS Phaser 1 LFO", FOMP + "cs_phaser1_lfo", {"sections": 4, "lfo_freq": 0.5, "mod_gain": 1.0, "out_mix": 0.5}),
    ("phaser", "Invada Phaser (mono in)", "http://invadarecords.com/plugins/lv2/phaser/mono", {"cycle": 2.0}),
    ("flanger", "GxFlanger", G + "gx_flanger#_flanger", {}),
    ("flanger", "Calf Flanger", C + "Flanger", {"mod_rate": 0.3}),
    ("flanger", "mda ThruZero", MDA + "ThruZero", {}),
    ("flanger", "LSP Flanger Mono", "http://lsp-plug.in/plugins/lv2/flanger_mono", {"dry": 1.0, "wet": 1.0, "rate": 0.3}),
    ("tremolo", "GxTremolo", G + "gx_tremolo#_tremolo", {"dry_wet": 100, "depth": 0.7, "freq": 5.0}),
    ("tremolo", "GxTubeTremelo", G + "gxtubetremelo#tubetremelo", {"depth": 0.6, "speed": 5.0}),
    ("tremolo", "Calf Pulsator", C + "Pulsator", {"mode": 0, "amount": 0.7, "timing": 3, "hz": 5.0, "mono": 1, "offset_r": 0.0}),
    ("vibrato", "GxTubeVibrato", G + "gxtubevibrato#tubevibrato", {"depth": 0.5, "speed": 5.0}),
    ("vibrato", "Calf Rotary Speaker", C + "RotarySpeaker", {}),
    ("vibrato", "mda Leslie", MDA + "Leslie", {}),
    ("wah", "GxAutoWah", G + "gxautowah#autowah", {}),
    ("wah", "GxWah, pedal half way", G + "gxautowah#wah", {"fslider1_": 0.5}),
    ("wah", "GxWahwah, auto", G + "gx_colwah_#_colwah_", {"MODE": 1, "WET_DRY": 100}),
    ("wah", "fomp Auto-wah", FOMP + "autowah", {"mix": 1.0}),
    ("envelope", "Calf Envelope Filter", C + "EnvelopeFilter", {"gain": 16}),
    ("envelope", "mda RezFilter", MDA + "RezFilter", {"lfo_vcf": 0.5}),
    ("reverb", "Gxshimmizita", G + "gx_shimmizita_#_shimmizita_", {}),
    ("reverb", "Dragonfly Hall", "https://github.com/michaelwillis/dragonfly-reverb", {}),
    ("reverb", "Dragonfly Hall, long", "https://github.com/michaelwillis/dragonfly-reverb", {"decay": 6.0, "size": 50, "late_level": 40}),
    ("reverb", "Dragonfly Plate", "urn:dragonfly:plate", {}),
    ("reverb", "GxZita_rev1-Stereo", G + "gx_zita_rev1_stereo#_zita_rev1_stereo", {}),
    ("reverb", "GxZita_rev1-Stereo, long", G + "gx_zita_rev1_stereo#_zita_rev1_stereo",
     {"MID_RT60": 6.0, "LOW_RT60": 6.0, "DRY_WET_MIX": -0.3, "IN_DELAY": 40}),
    ("reverb", "Calf Reverb", C + "Reverb", {"decay_time": 3.0}),
    ("delay", "GxEchoCat", G + "gxechocat#echocat", {"head1": 1, "swell": 0.6, "sustain": 0.4}),
    ("delay", "GxTubeDelay", G + "gxtubedelay#tubedelay", {"delay": 350}),
    ("delay", "Calf Vintage Delay", C + "VintageDelay", {}),
    ("delay", "swh Tape Delay", "http://plugin.org.uk/swh-plugins/tapeDelay", {"da_db": 0, "t1d": 1.0, "t1a_db": -6}),
    ("delay", "Gxdigital_delay", G + "gx_digital_delay_#_digital_delay_", {}),
    ("gate", "abGate", "http://hippie.lt/lv2/gate", {"threshold": -45, "switch": 1}),
    ("gate", "Calf Gate", C + "Gate", {}),
    ("other", "mda RingMod", MDA + "RingMod", {}),
    ("harmoniser", "Rubber Band Live 4.0, a fifth up (built)", "http://breakfastquay.com/rdf/lv2-rubberband#livemono",
     {"semitones": 7, "wetdry": 0.5}),
]


def frames(y: np.ndarray, n: int) -> np.ndarray:
    k = len(y) // n
    return y[:k * n].reshape(k, n)


def centroid_series(y: np.ndarray, n: int = 960) -> tuple[np.ndarray, np.ndarray]:
    f = frames(y, n) * np.hanning(n)
    mag = np.abs(np.fft.rfft(f, axis=1))
    freqs = np.fft.rfftfreq(n, 1 / SR)
    power = mag.sum(axis=1)
    cen = (mag * freqs).sum(axis=1) / np.where(power > 0, power, 1)
    rms = np.sqrt(np.mean(frames(y, n) ** 2, axis=1))
    return cen, rms


def steady() -> np.ndarray:
    """Eight seconds of a 120 Hz note (near B2) held at a constant level, its
    partials exact multiples so that nothing beats: anything that moves in
    the output, the pedal moved. (A held chord beats by itself, at 0.4 and
    4.4 Hz for A major, which would read as modulation; and the frames are
    exactly three periods, 25 ms, so the frames themselves do not ripple.)"""
    t = np.arange(8 * SR) / SR
    out = np.zeros(len(t))
    for k in range(1, 50):
        out += np.sin(2 * np.pi * k * 120.0 * t + k) / k
    return 0.3 * out / np.max(np.abs(out))


def wobble(y: np.ndarray) -> tuple[float, float, float, float]:
    """Over seconds 1 to 8 of the steady render: the strongest rate (0.1 to
    15 Hz) at which the level moves and its swing, p95 - p5, in dB; the same
    for the spectral centroid, swing as % of its mean."""
    seg = y[SR:8 * SR]
    cen, rms = centroid_series(seg, 1200)
    rate = SR / 1200
    out = []
    for series in (20 * np.log10(np.maximum(rms, 1e-9)), cen / max(np.mean(cen), 1e-9) * 100):
        d = series - np.mean(series)
        spec = np.abs(np.fft.rfft(d * np.hanning(len(d)), 8 * len(d)))
        fr = np.fft.rfftfreq(8 * len(d), 1 / rate)
        band = (fr >= 0.1) & (fr <= 15)
        swing = float(np.percentile(d, 95) - np.percentile(d, 5))
        out += [float(fr[np.argmax(np.where(band, spec, 0))]), swing]
    return tuple(out)


def measure(x: np.ndarray, events: list[bench.Event], y2: np.ndarray, info: dict) -> dict:
    m: dict = {}
    y = y2[:, 0]
    n = min(len(x), len(y))
    m["level"] = bench.db(np.mean(y[:n] ** 2) / np.mean(x[:n] ** 2))
    fx, px = bench.spectrum(x, 0, n / SR)
    fy, py = bench.spectrum(y, 0, n / SR)
    m["bright"] = bench.db(py[fy > 2000].sum() / py.sum()) - bench.db(px[fx > 2000].sum() / px.sum())
    lag, corr = bench.xcorr_delay(x[:n], y[:n], max_lag=24000)
    m["lag"] = lag if abs(corr) >= 0.3 else None
    m["corr"] = corr
    end_in = np.nonzero(np.abs(x) > 1e-3)[0][-1]
    env = bench.envelope(y2, 10)
    above = np.nonzero(env > 1e-4)[0]
    m["tail"] = (above[-1] - end_in) / SR if len(above) else 0.0
    if y2.shape[1] > 1:
        a, b = y2[:n, 0], y2[:n, 1]
        m["lr"] = float(np.corrcoef(a, b)[0, 1]) if np.std(a) > 0 and np.std(b) > 0 else float("nan")
    else:
        m["lr"] = None
    sweeps = []
    for e in (e for e in events if e.kind == "note"):
        def cen(sig, t0, t1):
            f, pw = bench.spectrum(sig, e.onset + t0, e.onset + t1)
            return float((f * pw).sum() / max(pw.sum(), 1e-30))
        sweeps.append((cen(y, 0.02, 0.12) / cen(y, 0.6, 1.0)) / (cen(x, 0.02, 0.12) / cen(x, 0.6, 1.0)))
    m["sweep"] = float(np.median(sweeps))
    return m


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--wav", type=Path)
    ap.add_argument("--only")
    ap.add_argument("--rubberband4", default="")
    args = ap.parse_args(argv)
    system = os.environ.get("LV2_PATH", "/usr/lib/lv2")
    x, events = bench.guitar()
    x = np.concatenate([x, np.zeros(4 * SR)])
    held, burst = steady(), bench.tone_burst(500.0)
    fl, dl, fc, dc = wobble(held)
    print(f"The held note itself: level {fl:.2f} Hz {dl:.1f} dB, tone {fc:.2f} Hz {dc:.0f}%")
    if args.wav:
        args.wav.mkdir(parents=True, exist_ok=True)
    print("| kind | pick | in/out | latency port | onset | lag | CPU % | level dB | bright dB | tail s | L/R "
          "| level wobble | tone wobble | sweep |")
    print("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for i, (kind, name, uri, controls) in enumerate(PICKS):
        if args.only and args.only.lower() not in name.lower() and args.only.lower() != kind:
            continue
        built = "(built)" in name
        if built and not args.rubberband4:
            print(f"| {kind} | {name} | not rendered: give --rubberband4 |")
            continue
        os.environ["LV2_PATH"] = args.rubberband4 if built else system
        probe = bench.render(uri, x[:SR], controls)
        if not probe.ok and "audio_out" not in probe.info:
            print(f"| {kind} | {name} | not rendered: {probe.info.get('error')} |")
            continue
        width = 2 if probe.info.get("audio_out", 1) >= 2 else 1
        r = bench.render(uri, np.stack([x] * width, 1) if width == 2 else x, controls, repeats=2)
        ports = f"{r.info.get('audio_in')}/{r.info.get('audio_out')}"
        if r.info.get("atom_in") or r.info.get("atom_out"):
            ports += f" +atom {r.info.get('atom_in')}/{r.info.get('atom_out')}"
        if not r.ok:
            why = r.info.get("sushi_refuses") or r.info.get("error")
            print(f"| {kind} | {name} | {ports} | not rendered: {why} |")
            continue
        if r.info["nonfinite_from"] >= 0:
            print(f"| {kind} | {name} | {ports} | NaN or inf from frame {r.info['nonfinite_from']} |")
            continue
        m = measure(x, events, r.out, r.info)
        st = bench.render(uri, np.stack([held] * width, 1) if width == 2 else held, controls)
        fl, dl, fc, dc = wobble(st.out[:, 0]) if st.ok else (float("nan"),) * 4
        bu = bench.render(uri, np.stack([burst] * width, 1) if width == 2 else burst, controls)
        onset = bench.onset_delay(burst, bu.out[:, 0], 0.5) if bu.ok else None
        port = r.latency_port
        lr = "" if m["lr"] is None else f"{m['lr']:.2f}"
        print(f"| {kind} | {name} | {ports} | {'' if port is None else int(port)} | {'' if onset is None else max(0, int(onset))} "
              f"| {'' if m['lag'] is None else m['lag']} "
              f"| {r.cpu_percent:.2f} | {m['level']:+.1f} | {m['bright']:+.1f} | {m['tail']:.1f} | {lr} "
              f"| {fl:.2f} Hz {dl:.1f} dB | {fc:.2f} Hz {dc:.0f}% | {m['sweep']:.2f} |", flush=True)
        if kind == "gate":
            rng = np.random.default_rng(7)
            noisy = x + 10 ** (-66 / 20) * rng.standard_normal(len(x))
            g = bench.render(uri, np.stack([noisy] * width, 1) if width == 2 else noisy, controls)
            quiet = slice(len(x) - 2 * SR, len(x))
            hiss_in = bench.db(np.mean(noisy[quiet] ** 2))
            hiss_out = bench.db(np.mean(g.out[quiet, 0] ** 2))
            print(f"|   | {name}: hiss at -66 dBFS RMS added; in the last 2 s, after the notes: "
                  f"in {hiss_in:.1f}, out {hiss_out:.1f} dBFS |")
        if args.wav:
            safe = "".join(ch if ch.isalnum() else "-" for ch in name).strip("-")
            bench.write_wav(args.wav / f"{i + 1:02d}-{kind}-{safe}.wav", r.out)
    if args.wav:
        bench.write_wav(args.wav / "00-input.wav", x)
    os.environ["LV2_PATH"] = system
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
