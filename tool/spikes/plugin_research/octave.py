"""#153: every octave candidate, up and down, over the same guitar.

    python3 tool/spikes/plugin_research/octave.py [--wav DIR] [--only NAME]
        [--extra-lv2 DIR] [--rubberband4 DIR]

Each candidate is set fully wet, where it has a mix, so what is measured is
the octave and not the dry note under it. `--extra-lv2` adds a folder of
bundles built from source, and `--rubberband4` is a folder holding only
Rubber Band 4.0's `rubberband.lv2` (its URIs collide with Ubuntu's 3.3.0, so
those candidates render with LV2_PATH set to it alone); `build_extras.sh DIR`
makes both, as DIR/lv2 and DIR/rb4/lv2. A candidate not installed is listed
as such.

What is printed, per candidate (the README, under #153, says the same):

- latency: the plugin's latency port, and the onset of a 500 Hz burst in
  the output, in samples at 48 kHz;
- attack: how much later than the dry pluck the octave reaches half its
  peak, median over seven single notes (ms);
- pitch: median error from the true octave over those notes (cents), and
  the share of 10 ms frames within 35 cents;
- run: of eight quick notes (0.25 s each), how many land on the octave;
- bend: median error while the A3 is bent a tone and held (cents);
- junk: energy that is not on a harmonic of the note or its octave, as a
  share of all of it (dB; lower is cleaner), single notes and the E major;
- octave present (down: the new sub-harmonics' share of the A major triad;
  up: what is left of the triad's own fundamentals, dB);
- CPU: time inside run() as a share of real time, one core of this machine.
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

GX_DETUNE = "http://guitarix.sourceforge.net/plugins/gx_detune_#_detune_"
GX_OC2 = "http://guitarix.sourceforge.net/plugins/gx_oc_2_#_oc_2_"
RB = "http://breakfastquay.com/rdf/lv2-rubberband#"
RB3_FIXED = "http://breakfastquay.com/rdf/lv2-rubberband-r3#mono"
MAPS = "http://distrho.sf.net/plugins/MaPitchshift"
AMPS = "http://plugin.org.uk/swh-plugins/amPitchshift"
PSHQ = "http://plugin.org.uk/swh-plugins/pitchScaleHQ"
DIVIDER = "http://plugin.org.uk/swh-plugins/divider"
MDA = "http://drobilla.net/plugins/mda/"
MOD = "http://moddevices.com/plugins/mod-devel/"

GX_WET = {"WET": 100, "DRY": 0, "BYPASS": 1, "COMPENSATE": 0}
# (name, direction, uri, controls, where it renders: "system", "extra" or "rb4")
CANDIDATES = [
    ("gx_detune_ (today's), high quality", "up", GX_DETUNE, {**GX_WET, "DETUNE": 12, "OCTAVE": 0, "LATENCY": 0}, "system"),
    ("gx_detune_, realtime", "up", GX_DETUNE, {**GX_WET, "DETUNE": 12, "OCTAVE": 0, "LATENCY": 2}, "system"),
    ("gx_detune_ (today's), high quality", "down", GX_DETUNE, {**GX_WET, "DETUNE": -12, "OCTAVE": 0, "LATENCY": 0}, "system"),
    ("gx_detune_, realtime", "down", GX_DETUNE, {**GX_WET, "DETUNE": -12, "OCTAVE": 0, "LATENCY": 2}, "system"),
    ("gx_oc_2_, octave 1", "down", GX_OC2, {"DIRECT": 0, "OCTAVE1": 1, "OCTAVE2": 0}, "system"),
    ("Rubber Band R2 mono 3.3", "up", RB + "mono", {"octaves": 1, "wetdry": 0}, "system"),
    ("Rubber Band R2 mono 3.3", "down", RB + "mono", {"octaves": -1, "wetdry": 0}, "system"),
    ("Rubber Band R3 mono 3.3 *", "up", RB3_FIXED, {"octaves": 1, "wetdry": 0}, "extra"),
    ("Rubber Band R3 mono 3.3 *", "down", RB3_FIXED, {"octaves": -1, "wetdry": 0}, "extra"),
    ("Rubber Band Live mono 4.0", "up", RB + "livemono", {"octaves": 1, "wetdry": 0}, "rb4"),
    ("Rubber Band Live mono 4.0", "down", RB + "livemono", {"octaves": -1, "wetdry": 0}, "rb4"),
    ("Rubber Band R3 mono 4.0", "up", RB + "r3mono", {"octaves": 1, "wetdry": 0}, "rb4"),
    ("Rubber Band R3 mono 4.0", "down", RB + "r3mono", {"octaves": -1, "wetdry": 0}, "rb4"),
    ("MaPitchshift", "up", MAPS, {"ratio": 2.0}, "system"),
    ("MaPitchshift", "down", MAPS, {"ratio": 0.5}, "system"),
    ("mda Detune, at most", "up", MDA + "Detune", {"detune": 1.0, "mix": 1.0}, "system"),
    ("mda SubSynth, divide", "down", MDA + "SubSynth", {"type": 0.4, "level": 0.3, "tune": 1.0, "dry_mix": 0.0, "thresh": 0.3}, "system"),
    ("mda Tracker, sine", "up", MDA + "Tracker", {"mode": 0.0, "transpose": 0.5 + 12 / 72, "mix": 1.0, "dynamics": 1.0}, "system"),
    ("mda Tracker, sine", "down", MDA + "Tracker", {"mode": 0.0, "transpose": 0.5 - 12 / 72, "mix": 1.0, "dynamics": 1.0}, "system"),
    ("swh amPitchshift", "up", AMPS, {"pitch": 2.0}, "system"),
    ("swh amPitchshift", "down", AMPS, {"pitch": 0.5}, "system"),
    ("swh pitchScaleHQ", "up", PSHQ, {"mult": 2.0}, "system"),
    ("swh pitchScaleHQ", "down", PSHQ, {"mult": 0.5}, "system"),
    ("swh divider", "down", DIVIDER, {"denominator": 2}, "system"),
    ("MOD SuperCapo, fidelity 0", "up", MOD + "SuperCapo", {"Step": 12, "Gain": 0, "Fidelity": 0}, "extra"),
    ("MOD SuperCapo, fidelity 1", "up", MOD + "SuperCapo", {"Step": 12, "Gain": 0, "Fidelity": 1}, "extra"),
    ("MOD SuperCapo, fidelity 2", "up", MOD + "SuperCapo", {"Step": 12, "Gain": 0, "Fidelity": 2}, "extra"),
    ("MOD Drop, fidelity 0", "down", MOD + "Drop", {"Step": -12, "Gain": 0, "Fidelity": 0}, "extra"),
    ("MOD Drop, fidelity 1", "down", MOD + "Drop", {"Step": -12, "Gain": 0, "Fidelity": 1}, "extra"),
    ("MOD Drop, fidelity 2", "down", MOD + "Drop", {"Step": -12, "Gain": 0, "Fidelity": 2}, "extra"),
    ("MOD SuperWhammy at +12", "up", MOD + "SuperWhammy", {"First": 0, "Last": 12, "Step": 1, "Gain": 0, "Fidelity": 1}, "extra"),
    ("MOD SuperWhammy at -12", "down", MOD + "SuperWhammy", {"First": 0, "Last": -12, "Step": 1, "Gain": 0, "Fidelity": 1}, "extra"),
]


def median(xs):
    xs = [x for x in xs if x is not None and np.isfinite(x)]
    return float(np.median(xs)) if xs else float("nan")


def cents(a: float, b: float) -> float:
    return 1200 * np.log2(a / b)


def note_window(e: bench.Event) -> tuple[float, float]:
    return e.onset + 0.15, e.onset + min(0.75, e.length - 0.05)


def attack_delay_ms(x: np.ndarray, y: np.ndarray, e: bench.Event) -> float | None:
    """When y reaches half its peak over the note, against when x does."""
    i0, i1 = int((e.onset - 0.01) * SR), int((e.onset + 0.6) * SR)
    ex, ey = bench.envelope(x[i0:i1]), bench.envelope(y[i0:i1])
    if ey.max() < 1e-5:
        return None
    tx = np.argmax(ex >= 0.5 * ex.max())
    ty = np.argmax(ey >= 0.5 * ey.max())
    return 1000 * (ty - tx) / SR


def measure(x: np.ndarray, events: list[bench.Event], y_raw: np.ndarray, ratio: float, delay: int) -> dict:
    """Timing from y_raw as it came out; everything else from y moved back by
    `delay` samples, so a slow shifter is judged on its notes, not on being late."""
    m: dict = {}
    y = np.concatenate([y_raw[delay:], np.zeros(delay)])
    notes = [e for e in events if e.kind == "note"]
    errs, inside, junk, share, attacks = [], [], [], [], []
    for e in notes:
        a, b = note_window(e)
        fmin = 30.0
        p_in = median(bench.pitch_track(x, a, b, fmin=fmin))
        target = p_in * ratio
        track = bench.pitch_track(y, a, b, fmin=fmin)
        frame_errs = [cents(p, target) if p else None for p in track]
        good = [abs(c) <= 35 for c in frame_errs if c is not None]
        inside.append(sum(good) / max(1, len(frame_errs)))
        errs.append(median([abs(c) if c is not None else None for c in frame_errs]))
        f, p = bench.spectrum(y, a, b)
        band = f <= 5000
        f, p = f[band], p[band]
        total = p.sum()
        base = min(target, p_in)
        junk.append(bench.db((total - bench.energy_near(f, p, bench.harmonics([base]))) / max(total, 1e-30)))
        if ratio < 1:   # the new partials: odd multiples of the sub
            odd = [k * target for k in range(1, 200, 2) if k * target <= 5000]
            share.append(bench.db(bench.energy_near(f, p, odd) / max(total, 1e-30)))
        else:           # what is left of the note: its odd harmonics
            odd = [k * p_in for k in range(1, 200, 2) if k * p_in <= 5000]
            share.append(bench.db(bench.energy_near(f, p, odd) / max(total, 1e-30)))
        attacks.append(attack_delay_ms(x, y_raw, e))
        m.setdefault("per_note", []).append(
            f"{e.name}: in {p_in:.1f} Hz, out {median(track):.1f} Hz, {100 * inside[-1]:.0f}% in tune, "
            f"junk {junk[-1]:.1f} dB, attack {attacks[-1] if attacks[-1] is None else round(attacks[-1])} ms")
    m["pitch_err"] = median(errs)
    m["in_tune"] = float(np.mean(inside))
    m["junk_notes"] = median(junk)
    m["octave_share_notes"] = median(share)
    m["attack_ms"] = median(attacks)

    run = [e for e in events if e.kind == "run"]
    landed = 0
    for e in run:
        a, b = e.onset + 0.07, e.onset + 0.24
        p_in = median(bench.pitch_track(x, a, b, hop_ms=5, fmin=60.0))
        p_out = median(bench.pitch_track(y, a, b, hop_ms=5, fmin=60.0 * min(1, ratio)))
        if np.isfinite(p_out) and abs(cents(p_out, p_in * ratio)) <= 50:
            landed += 1
    m["run"] = f"{landed}/{len(run)}"

    bend = next(e for e in events if e.kind == "bend")
    a, b = bend.onset + 0.1, bend.onset + 1.6
    tin = bench.pitch_track(x, a, b, fmin=60.0)
    tout = bench.pitch_track(y, a, b, fmin=60.0 * min(1, ratio))
    m["bend_err"] = median([abs(cents(po, pi * ratio)) if (po and pi) else None for pi, po in zip(tin, tout)])

    chords = {e.name: e for e in events if e.kind == "chord"}
    e = chords["E major (open)"]
    f, p = bench.spectrum(y, e.onset + 0.2, e.onset + 1.4)
    band = f <= 5000
    f, p = f[band], p[band]
    allowed = bench.harmonics([fr * min(1, ratio) for fr in e.freqs])
    m["junk_chord"] = bench.db((p.sum() - bench.energy_near(f, p, allowed, rel=0.008)) / max(p.sum(), 1e-30))
    e = chords["A major triad (A2 C#3 E3)"]
    f, p = bench.spectrum(y, e.onset + 0.2, e.onset + 1.4)
    band = f <= 5000
    f, p = f[band], p[band]
    if ratio < 1:
        m["triad"] = bench.db(bench.energy_near(f, p, [fr * ratio for fr in e.freqs], rel=0.008) / max(p.sum(), 1e-30))
    else:
        m["triad"] = bench.db(bench.energy_near(f, p, list(e.freqs), rel=0.008) / max(p.sum(), 1e-30))
    m["level_db"] = bench.db(np.mean(y ** 2) / np.mean(x ** 2))
    return m


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--wav", type=Path)
    ap.add_argument("--only")
    ap.add_argument("--notes", action="store_true", help="also print each single note's figures")
    ap.add_argument("--extra-lv2", default="")
    ap.add_argument("--rubberband4", default="")
    args = ap.parse_args(argv)
    system = os.environ.get("LV2_PATH", "/usr/lib/lv2")
    paths = {"system": system, "extra": ":".join(p for p in (system, args.extra_lv2) if p),
             "rb4": args.rubberband4}
    x, events = bench.guitar()
    burst = bench.tone_burst(500.0)
    if args.wav:
        args.wav.mkdir(parents=True, exist_ok=True)
        bench.write_wav(args.wav / "00-input.wav", x)
    print("| candidate | way | latency port | 500 Hz onset | attack ms | pitch err c | in tune | run | bend err c "
          "| junk notes dB | junk E maj dB | odd partials dB | triad dB | level dB | CPU % |")
    print("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for i, (name, way, uri, controls, where) in enumerate(CANDIDATES):
        if args.only and args.only.lower() not in name.lower():
            continue
        if not paths[where]:
            print(f"| {name} | {way} | not rendered: no {where} folder given |")
            continue
        os.environ["LV2_PATH"] = paths[where]
        ratio = 2.0 if way == "up" else 0.5
        r = bench.render(uri, x, controls, repeats=3)
        if not r.ok:
            why = r.info.get("sushi_refuses") or r.info.get("error") or r.info.get("stderr", "")[-200:]
            print(f"| {name} | {way} | not rendered: {why} |")
            continue
        y = r.out[:, 0]
        if np.max(np.abs(y - x)) < 1e-6:
            print(f"| {name} | {way} | passes the input through unchanged |")
            continue
        if r.info["nonfinite_from"] >= 0:
            print(f"| {name} | {way} | NaN or inf from frame {r.info['nonfinite_from']} |")
            continue
        rb = bench.render(uri, burst, controls)
        onset = bench.onset_delay(burst, rb.out[:, 0], 0.5) if rb.ok else None
        m = measure(x, events, y, ratio, max(0, int(onset or 0)))
        port = r.latency_port
        print(f"| {name} | {way} | {'' if port is None else int(port)} | {'' if onset is None else max(0, int(onset))} "
              f"| {m['attack_ms']:.0f} | {m['pitch_err']:.0f} | {100 * m['in_tune']:.0f}% | {m['run']} "
              f"| {m['bend_err']:.0f} | {m['junk_notes']:.1f} | {m['junk_chord']:.1f} | {m['octave_share_notes']:.1f} "
              f"| {m['triad']:.1f} | {m['level_db']:+.1f} | {r.cpu_percent:.2f} |", flush=True)
        if args.notes:
            for line in m["per_note"]:
                print(f"|   | {line} |")
        if args.wav:
            safe = "".join(c if c.isalnum() else "-" for c in f"{name}-{way}").strip("-")
            bench.write_wav(args.wav / f"{i + 1:02d}-{safe}.wav", r.out)
    os.environ["LV2_PATH"] = system
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
