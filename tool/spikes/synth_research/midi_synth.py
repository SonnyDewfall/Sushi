"""#166: guitar-to-MIDI into a synth, in the real Sushi 1.3.0.

    python3.12 tool/spikes/synth_research/midi_synth.py --extras DIR --old-extras DIR2 --farm SCRATCH [--wav OUT]

Three questions, each answered by renders:

1. **Can Sushi route it?** A plugin's MIDI output reaching the next plugin on
   the track, through an internal plugin, through an LV2 effect, across to
   another track. The tracker is the spike's own (pitch2midi.c); what arrives
   is read by the probe (midiprobe.c), which plays every note it is sent as a
   sine at 300 + 100 x (note - 24) Hz.
2. **How well does each tracker name the notes?** The guitar of
   bench.guitar() through each tracker into the probe, read back as a piano
   roll: per single note, the delay from the pluck to the right note sounding,
   and how much of the note's time the right note, a wrong note, or nothing
   sounds; per chord, how many of its notes are found and how many others.
3. **What does it cost and sound like into a synth?** Each tracker into real
   synths: the delay from the pluck to the synth's note (its output reaching
   -20 dB of its level over the note), CPU (Sushi's own timings), NaN.

Trackers: the spike's mono tracker, and GuitarMidi-LV2 v3.2 (polyphonic, a
neural network on a filter bank; the author's package). Nothing else that
turns audio into MIDI was found packaged for Ubuntu 24.04 (README).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import measures as ms  # noqa: E402
import sushirun as sr  # noqa: E402
from measures import SR, bench  # noqa: E402
from sushirun import Plug  # noqa: E402

TRACK = "urn:sushi-rig:spike:pitch2midi"
GM = "http://github.com/geraldmwangi/GuitarMidi-LV2"
PROBE = "urn:sushi-rig:spike:midiprobe"
LOW, HIGH = 24, 119

PACER = "urn:sushi-rig:spike:pacer"

# "at real time": the spike's pacer first on the track, so that Sushi's
# offline frontend runs no faster than live (pacer.c). GuitarMidi-LV2 runs its
# neural network on a thread of its own, which an unpaced render outruns.
TRACKERS = [
    ("spike mono tracker", [Plug("tracker", TRACK, {"threshold": -40})]),
    ("spike mono tracker, at real time", [Plug("pacer", PACER), Plug("tracker", TRACK, {"threshold": -40})]),
    ("GuitarMidi-LV2 3.2, defaults, unpaced", [Plug("tracker", GM, {})]),
    ("GuitarMidi-LV2 3.2, defaults, at real time", [Plug("pacer", PACER), Plug("tracker", GM, {})]),
    ("GuitarMidi-LV2 3.2, input gain 12 dB, at real time", [Plug("pacer", PACER), Plug("tracker", GM, {"gain": 12.0})]),
]

SYNTHS = [
    ("Sushi brickworks simple_synth (mono)", "Sushi 1.3.0",
     Plug("synth", "sushi.brickworks.simple_synth",
          {"volume": -12.0, "filter_cutoff": 3000.0, "attack": 0.01, "release": 0.3, "sustain": 0.8, "decay": 0.3})),
    ("mda JX10 (poly, in the manifest)", "mda-lv2", Plug("synth", "http://drobilla.net/plugins/mda/JX10", {})),
    ("string-machine (poly)", "build", Plug("synth", "http://jpcima.sdf1.org/lv2/string-machine-mk2", {})),
    ("Calf Monosynth", "calf-plugins", Plug("synth", "http://calf.sourceforge.net/plugins/Monosynth", {})),
    ("DISTRHO Nekobi (TB-303-like, mono)", "dpf-plugins-lv2", Plug("synth", "http://distrho.sf.net/plugins/Nekobi", {})),
    ("mda DX10 (FM, in the manifest)", "mda-lv2", Plug("synth", "http://drobilla.net/plugins/mda/DX10", {})),
]


def midi_note(f: float) -> int:
    return int(round(69 + 12 * np.log2(f / 440.0)))


def piano_roll(y: np.ndarray, hop: int = 64, n: int = 2048) -> tuple[np.ndarray, np.ndarray]:
    """(frame centre times s, notes x frames of 0/1) from a probe render: a
    note is on where its sine is above half the probe's level."""
    y = ms.mono(y)
    win = np.hanning(n)
    starts = np.arange(0, len(y) - n, hop)
    freqs = 300.0 + 100.0 * (np.arange(LOW, HIGH + 1) - LOW)
    t = np.arange(n) / SR
    # one complex exponential per note: a DFT at exactly those frequencies
    basis = np.exp(-2j * np.pi * np.outer(freqs, t)) * win
    full = 0.02 * win.sum() / 2
    frames = np.lib.stride_tricks.sliding_window_view(y, n)[::hop][:len(starts)]
    amp = np.abs(frames @ basis.T).T / full
    return (starts + n / 2) / SR, amp > 0.5


def score(times: np.ndarray, roll: np.ndarray, events: list[bench.Event]) -> dict:
    out = {"notes": [], "chords": []}
    for e in events:
        want = sorted({midi_note(f) for f in e.freqs})
        a, b = e.onset, e.onset + e.length
        sel = (times >= a) & (times < b)
        if not sel.any():
            continue
        r = roll[:, sel]
        t = times[sel]
        if e.kind == "chord":
            settle = t >= a + 0.3
            found = [w for w in want if r[w - LOW, settle].mean() > 0.5]
            others = [k + LOW for k in range(roll.shape[0])
                      if (k + LOW) not in want and r[k, settle].mean() > 0.2]
            first = [t[np.argmax(r[w - LOW])] - a for w in want if r[w - LOW].any()]
            out["chords"].append({"name": e.name, "want": len(want), "found": len(found), "others": len(others),
                                  "first_ms": 1000 * min(first) if first else None})
            continue
        target = want[0]
        if e.kind == "bend":
            target = midi_note(e.freqs[0])
        right = r[target - LOW]
        wrong = np.delete(r, target - LOW, axis=0).any(axis=0)
        lat = 1000 * (t[np.argmax(right)] - a) if right.any() else None
        out["notes"].append({"kind": e.kind, "name": e.name, "latency_ms": lat,
                             "right": float(right.mean()), "wrong": float((wrong & ~right).mean()),
                             "nothing": float((~right & ~wrong).mean())})
    return out


def summary(s: dict) -> dict:
    notes = [n for n in s["notes"] if n["kind"] == "note"]
    runs = [n for n in s["notes"] if n["kind"] == "run"]
    lats = [n["latency_ms"] for n in notes if n["latency_ms"] is not None]
    return {
        "found": f"{sum(n['latency_ms'] is not None for n in notes)}/{len(notes)}",
        "latency_ms": float(np.median(lats)) if lats else float("nan"),
        "latency_low_ms": next((n["latency_ms"] for n in notes if n["name"] == "E2"), None),
        "right": float(np.mean([n["right"] for n in notes])),
        "wrong": float(np.mean([n["wrong"] for n in notes])),
        "run": f"{sum(n['right'] > 0.3 for n in runs)}/{len(runs)}",
        "chords": "; ".join(f"{c['name'].split(' (')[0]}: {c['found']}/{c['want']}"
                            + (f" +{c['others']} wrong" if c["others"] else "") for c in s["chords"]),
    }


def synth_delay(y: np.ndarray, events: list[bench.Event]) -> float:
    # for a chain whose notes start from silence; a synth's release can keep it above
    # the mark across a note change, which reads as 0
    """Median over the single notes of the delay from the pluck until the
    output reaches -20 dB of its own level 0.3 to 0.6 s into the note, ms."""
    env = bench.envelope(y, 2.0)
    out = []
    for e in (e for e in events if e.kind == "note"):
        i0 = int(e.onset * SR)
        steady = np.median(env[i0 + int(0.3 * SR):i0 + int(0.6 * SR)])
        if steady < 1e-5:
            continue
        hit = np.nonzero(env[i0:i0 + int(0.6 * SR)] >= steady * 0.1)[0]
        if len(hit):
            out.append(1000 * hit[0] / SR)
    return float(np.median(out)) if out else float("nan")


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--extras", type=Path, required=True)
    ap.add_argument("--old-extras", type=Path, required=True)
    ap.add_argument("--farm", type=Path, required=True)
    ap.add_argument("--wav", type=Path)
    args = ap.parse_args(argv)
    lv2 = sr.lv2_farm(args.farm, extra=[args.extras / "lv2", args.old_extras / "lv2"],
                      replace={"rubberband.lv2": args.old_extras / "rb4" / "lv2" / "rubberband.lv2"})
    x, events = bench.guitar()
    x = np.concatenate([x, np.zeros(2 * SR)])
    if args.wav:
        args.wav.mkdir(parents=True, exist_ok=True)

    print("## 1. Routing\n")
    probe = Plug("probe", PROBE)
    tracker = Plug("tracker", TRACK, {"threshold": -40})
    cases = [
        ("tracker, then the probe (the compiler's mono_summing between them)", [tracker, probe], None),
        ("tracker, Sushi's brickworks chorus (internal), probe",
         [tracker, Plug("bwchorus", "sushi.brickworks.chorus", {"rate": 0.5, "amount": 0.3}), probe], None),
        ("tracker, DISTRHO 3 Band EQ (an LV2 effect with no MIDI port), probe",
         [tracker, Plug("eq", "http://distrho.sf.net/plugins/3BandEQ"), probe], None),
        ("tracker, GxChorus-Stereo (LV2, no MIDI port, stereo so no mono_summing), probe",
         [tracker, Plug("gxchorus", "http://guitarix.sourceforge.net/plugins/gx_chorus_stereo#_chorus_stereo"), probe],
         None),
        ("tracker, mda JX10 (a synth: takes MIDI, gives none), probe",
         [tracker, Plug("jx10", "http://drobilla.net/plugins/mda/JX10"), probe], None),
        ("tracker on this track, the probe alone on a second track", [tracker], "second"),
    ]
    for name, chain, other in cases:
        extra = None
        if other == "second":
            extra = [{"name": "synthtrack", "channels": 2, "inputs": [],
                      "outputs": [{"engine_bus": 0, "track_bus": 0}],
                      "plugins": [{"name": "probe", "type": "lv2", "uri": PROBE}]}]
            cfg = sr.build(chain, lv2, extra_tracks=extra)
            # the guitar track's own output would mask the probe's; take it nowhere
            cfg["tracks"][0]["outputs"] = []
        else:
            cfg = sr.build(chain, lv2)
        r = sr.run(cfg, x, lv2)
        if not r.ok:
            print(f"- {name}: did not run ({r.log.strip().splitlines()[-1]})")
            continue
        times, roll = piano_roll(r.out)
        frames_on = int(roll.any(axis=0).sum())
        print(f"- {name}: {'MIDI arrives' if frames_on else 'nothing arrives'} "
              f"({100 * frames_on / roll.shape[1]:.0f}% of frames hold a note)")
        if other == "second":
            # the control: the same probe, sent a note by the config itself, is heard
            cfg["events"] = [{"time": 1.0, "type": "note_on", "data": {"plugin_name": "probe", "note": 60, "velocity": 1.0}}]
            r = sr.run(cfg, x, lv2)
            times, roll = piano_roll(r.out)
            print(f"  - control, a note_on sent to that probe by the config: "
                  f"{'heard' if roll[60 - LOW].any() else 'not heard'}")
    # where in a chunk does a note from an LV2 plugin start?
    single = np.zeros(int(2.5 * SR))
    s = bench._string(bench.NOTE["A3"], 1.5, seed=5)
    at = int(0.5 * SR) + 23   # not on a chunk boundary
    single[at:at + len(s)] = s * 0.3
    r = sr.run(sr.build([tracker, probe], lv2), single, lv2)
    first = int(np.nonzero(np.abs(ms.mono(r.out)) > 1e-7)[0][0]) if r.ok else None
    print(f"- an A3 plucked at sample {at}: the probe's first sample is {first}, "
          f"{'on' if first is not None and first % 64 == 0 else 'not on'} a 64-frame chunk boundary "
          f"({first - at} samples, {1000 * (first - at) / SR:.1f} ms, after the pluck)\n")

    print("## 2. Naming the notes\n")
    print("| tracker | single notes found | to the right note, median ms | E2 ms | right | wrong | quick run | chords | CPU % | repeat |")
    print("|---|---|---|---|---|---|---|---|---|---|")
    rolls = {}
    for name, chain in TRACKERS:
        cfg = sr.build(chain + [probe], lv2)
        r = sr.run(cfg, x, lv2)
        if not r.ok:
            print(f"| {name} | did not run: {r.log.strip().splitlines()[-1]} |")
            continue
        again = sr.run(cfg, x, lv2)
        rep = ms.repeat_difference(r.out, again.out) if again.ok else float("nan")
        times, roll = piano_roll(r.out)
        rolls[name] = (times, roll)
        s = summary(score(times, roll, events))
        c = r.timings.get("tracker", (float("nan"),) * 3)
        print(f"| {name} | {s['found']} | {s['latency_ms']:.0f} | {s['latency_low_ms'] if s['latency_low_ms'] is None else round(s['latency_low_ms'])} "
              f"| {100 * s['right']:.0f}% | {100 * s['wrong']:.0f}% | {s['run']} | {s['chords']} | {c[0]:.2f} "
              f"| {'same' if rep == float('-inf') else f'{rep:.1f} dB'} |", flush=True)
        if args.wav:
            bench.write_wav(args.wav / f"probe-{name.split()[0]}.wav", r.out)
    print()
    for name, (times, roll) in rolls.items():
        print(f"{name}, per note:")
        for n in score(times, roll, events)["notes"]:
            lat = "never" if n["latency_ms"] is None else f"{n['latency_ms']:.0f} ms"
            print(f"  {n['kind']:5} {n['name']:14} right note after {lat:>8}; right {100 * n['right']:3.0f}%, "
                  f"wrong {100 * n['wrong']:3.0f}%, nothing {100 * n['nothing']:3.0f}%")
        for c in score(times, roll, events)["chords"]:
            print(f"  chord {c['name']:26} {c['found']}/{c['want']} found, {c['others']} others; "
                  f"first note after {c['first_ms'] if c['first_ms'] is None else round(c['first_ms'])} ms")
    print()

    print("## 3. Into a synth\n")
    print("Each synth sent A3 by the config itself (no tracker): its own attack, the delay from the "
          "note_on to its output reaching -20 dB of its level 0.3 to 0.6 s in, and its level. Then each "
          "tracker into it over the guitar: CPU (Sushi's timings, the pacer left out) and the stress signal.\n")
    print("| synth | from | attack ms | level dBFS | with the spike tracker: CPU %, stress | with GuitarMidi: CPU %, stress |")
    print("|---|---|---|---|---|---|")
    stress = ms.stress()
    for sname, source, synth in SYNTHS:
        quiet = np.zeros(2 * SR)
        cfg = sr.build([synth], lv2, events=[
            {"time": 0.5, "type": "note_on", "data": {"plugin_name": "synth", "note": 57, "velocity": 0.8}},
            {"time": 1.5, "type": "note_off", "data": {"plugin_name": "synth", "note": 57, "velocity": 0.0}}])
        r = sr.run(cfg, quiet, lv2)
        attack, lvl = float("nan"), float("nan")
        if r.ok:
            env = bench.envelope(r.out, 2.0)
            i0 = int(0.5 * SR)
            steady = float(np.median(env[i0 + int(0.3 * SR):i0 + int(0.6 * SR)]))
            if steady > 1e-6:
                hit = np.nonzero(env[i0:] >= 0.1 * steady)[0]
                attack = 1000 * hit[0] / SR if len(hit) else float("nan")
                lvl = 20 * np.log10(steady)
        cells = []
        for tname, chain in (TRACKERS[0], TRACKERS[3]):
            if "GuitarMidi" in tname and "poly" not in sname:
                cells.append("")
                continue
            cfg = sr.build(chain + [synth], lv2)
            rr = sr.run(cfg, x, lv2)
            st = sr.run(sr.build([p for p in chain if p.name != "pacer"] + [synth], lv2), stress, lv2)
            c = sum(rr.timings.get(k, (0,))[0] for k in ("tracker", "tracker_to_stereo", "synth", "synth_to_stereo")) \
                if rr.ok else float("nan")
            cells.append(f"{c:.2f}, {ms.sane(st.out) if st.ok else 'did not run'}")
            if args.wav and rr.ok:
                safe = "".join(ch if ch.isalnum() else "-" for ch in f"{tname}-{sname}")[:70]
                bench.write_wav(args.wav / f"synth-{safe}.wav", rr.out)
        att = "silent" if not np.isfinite(lvl) else f"{attack:.0f}"
        print(f"| {sname} | {source} | {att} | {'' if not np.isfinite(lvl) else f'{lvl:.1f}'} | {cells[0]} | {cells[1]} |",
              flush=True)
    if sr.RETRIES:
        print(f"\nRenders that failed once and were run again: {len(sr.RETRIES)}")
        for why in sr.RETRIES:
            print(f"- {why}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
