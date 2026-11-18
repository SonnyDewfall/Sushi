"""#166: one candidate at a time, per approach, rendered in the real Sushi 1.3.0.

    python3.12 tool/spikes/synth_research/survey.py --extras DIR --old-extras DIR2 [--wav OUT] [--only TEXT]

DIR is what `build_extras.sh DIR` made (string-machine, GuitarMidi, the spike's
tracker and probe); DIR2 is what `../plugin_research/build_extras.sh DIR2` made
(MOD's shifters in DIR2/lv2, Rubber Band 4.0 in DIR2/rb4/lv2). Every render
runs with Ubuntu's /usr/lib/lv2 in which Rubber Band 3.3.0 is replaced by 4.0,
as #164 would leave the rig.

Each candidate is a chain of one or two processors on a stereo track built as
the rig's compiler builds a channel (`sushirun.build`), rendered four times:
the guitar of bench.guitar() and four seconds of silence; a steady held note
(measures.steady); a 500 Hz burst; and the stress signal (measures.stress).
The columns are measures.py's. CPU is Sushi's own `--timing-statistics`
average for the candidate's processors, as a share of real time on one core of
this container, and is the guitar render's. Freeze candidates also get an E
major strum with the freeze switched on 0.6 s after it, by an event in the
config: **hold** is the level 6 to 8 s later against the level just after the
switch, dB (0 is a perfect freeze).

Settings are musical guesses, written next to each; they are not tuned.
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

G = "http://guitarix.sourceforge.net/plugins/"
C = "http://calf.sourceforge.net/plugins/"
MDA = "http://drobilla.net/plugins/mda/"
SWH = "http://plugin.org.uk/swh-plugins/"
ZYN = "http://zynaddsubfx.sourceforge.net/fx#"
RB = "http://breakfastquay.com/rdf/lv2-rubberband#livemono"
BW = "sushi.brickworks."

# (approach, name, where it comes from, chain, options)
CANDIDATES = [
    ("octave", "Rubber Band Live 4.0, octave up, half wet", "build (#164)",
     [Plug("rb_up", RB, {"octaves": 1, "wetdry": 0.5})], {}),
    ("octave", "Rubber Band Live 4.0, octave down, half wet", "build (#164)",
     [Plug("rb_down", RB, {"octaves": -1, "wetdry": 0.5})], {}),
    ("octave", "gx_oc_2_, dry and octave 1 (in the manifest)", "guitarix-lv2",
     [Plug("oc2", G + "gx_oc_2_#_oc_2_", {"DIRECT": 0.6, "OCTAVE1": 0.6, "OCTAVE2": 0.0})], {}),
    ("octave", "mda SubSynth, divide, dry under it (manifest)", "mda-lv2",
     [Plug("subsynth", MDA + "SubSynth", {"type": 0.4, "level": 0.3, "tune": 1.0, "dry_mix": 1.0, "thresh": 0.3})], {}),
    ("octave", "MOD Drop, octave down, wet", "build (#153)",
     [Plug("drop", "http://moddevices.com/plugins/mod-devel/Drop", {"Step": -12, "Gain": 0, "Fidelity": 1})], {}),

    ("ensemble", "string-machine chorus (Solina ensemble), wet", "build",
     [Plug("solina", "http://jpcima.sdf1.org/lv2/string-machine-chorus", {})], {}),
    ("ensemble", "string-machine chorus, dry and wet equal", "build",
     [Plug("solina", "http://jpcima.sdf1.org/lv2/string-machine-chorus", {"dry_gain": -6.0, "wet_gain": -6.0})], {}),
    ("ensemble", "Calf Multi Chorus, 6 voices", "calf-plugins",
     [Plug("multichorus", C + "MultiChorus", {"voices": 6, "mod_rate": 0.3, "mod_depth": 6.0, "amount": 1.0, "dry": 0.5})], {}),
    ("ensemble", "swh Multivoice Chorus, 6 voices", "swh-lv2",
     [Plug("mvchorus", SWH + "multivoiceChorus", {"voices": 6, "detune": 2.0, "voice_spread": 1.0})], {}),
    ("ensemble", "fomp Triple chorus", "fomp",
     [Plug("triple", "http://drobilla.net/plugins/fomp/triple_chorus", {"delay": 5.0, "mod_amp_1": 2.0, "mod_amp_2": 1.0})], {}),
    ("ensemble", "ZynChorus (in the manifest), defaults", "zynaddsubfx-lv2",
     [Plug("zynchorus", ZYN + "Chorus", {})], {}),
    ("ensemble", "GxChorus-Stereo (the rig's chorus), deeper", "guitarix-lv2",
     [Plug("gxchorus", G + "gx_chorus_stereo#_chorus_stereo", {"level": 0.7, "delay": 0.02, "depth": 0.3, "freq": 0.6})], {}),
    ("ensemble", "Sushi brickworks chorus", "Sushi 1.3.0",
     [Plug("bwchorus", BW + "chorus", {"rate": 0.6, "amount": 0.6})], {}),
    ("ensemble", "mda Detune (manifest)", "mda-lv2",
     [Plug("detune", MDA + "Detune", {"detune": 0.4, "mix": 0.9})], {}),

    ("swell", "GxSlowGear, 400 ms up, threshold 0.5", "build",
     [Plug("slowgear", G + "gx_slowgear_#_slowgear_", {"UPTIME": 400.0, "DOWNTIME": 50.0, "TRESHOLD": 0.5})], {}),
    ("swell", "GxSlowGear, 400 ms up, threshold 1 (default)", "build",
     [Plug("slowgear", G + "gx_slowgear_#_slowgear_", {"UPTIME": 400.0, "DOWNTIME": 50.0, "TRESHOLD": 1.0})], {}),
    ("swell", "abGate, 500 ms attack", "abgate",
     [Plug("abgate", "http://hippie.lt/lv2/gate", {"switch": 1, "threshold": -45, "attack": 500, "hold": 50, "decay": 400})], {}),
    ("swell", "Sushi brickworks noise gate, 0.5 s attack", "Sushi 1.3.0",
     [Plug("bwgate", BW + "noise_gate", {"threshold": -40, "ratio": 1.0, "attack": 0.5, "release": 0.2})], {}),
    ("swell", "Calf Transient Designer, attack cut", "calf-plugins",
     [Plug("transient", C + "TransientDesigner", {"attack_boost": -1.0, "attack_time": 400.0, "release_boost": 0.5})], {}),
    ("swell", "mda Transient, attack cut (manifest)", "mda-lv2",
     [Plug("mdatransient", MDA + "Transient", {"attack": 0.0, "att_hold": 0.8, "release": 0.6})], {}),
    ("swell", "ZamGate, 500 ms attack", "zam-plugins",
     [Plug("zamgate", "urn:zamaudio:ZamGate", {"att": 500.0, "rel": 300.0, "thr": -40.0})], {}),

    ("freeze", "Sushi freeverb, freeze", "Sushi 1.3.0",
     [Plug("freeverb", "sushi.testing.freeverb", {"dry": 1.0, "wet": 0.5, "room_size": 0.9, "damp": 0.3, "width": 1.0})],
     {"freeze": ("freeverb", "freeze")}),
    ("freeze", "ZamGrains, freeze", "zam-plugins",
     [Plug("grains", "urn:zamaudio:ZamGrains", {"grains": 8, "time": 300.0, "gs": 1.0, "ps": 1.0})],
     {"freeze": ("grains", "freeze")}),
    ("freeze", "GxSustainer", "guitarix-lv2",
     [Plug("susta", G + "gx_susta_#_susta_", {"SUSTAIN": 0.8, "VOLUME": 0.4})], {}),
    ("freeze", "swh GVerb, 30 s", "swh-lv2",
     [Plug("gverb", SWH + "gverb", {"revtime": 30.0, "roomsize": 200.0, "drylevel": 0.0, "taillevel": -10.0})], {}),
    ("freeze", "Calf Reverb, 15 s", "calf-plugins",
     [Plug("calfverb", C + "Reverb", {"decay_time": 15.0, "amount": 0.6, "dry": 1.0, "room_size": 5})], {}),

    ("filter", "fomp Auto-wah (#155's pick)", "fomp",
     [Plug("autowah", "http://drobilla.net/plugins/fomp/autowah", {"mix": 1.0})], {}),
    ("filter", "Calf Envelope Filter, activation 16", "calf-plugins",
     [Plug("envfilter", C + "EnvelopeFilter", {"gain": 16.0})], {}),
    ("filter", "mda RezFilter, envelope and LFO (manifest)", "mda-lv2",
     [Plug("rezfilter", MDA + "RezFilter", {"freq": 0.3, "res": 0.75, "env_vcf": 0.85, "lfo_vcf": 0.6, "lfo_rate": 0.3})], {}),
    ("filter", "ZynDynamicFilter, LFO up", "zynaddsubfx-lv2",
     [Plug("zyndyn", ZYN + "DynamicFilter", {"lfodepth": 90, "lfofreq": 60})], {}),
    ("filter", "ZynAlienWah", "zynaddsubfx-lv2",
     [Plug("alienwah", ZYN + "AlienWah", {})], {}),

    ("ring", "mda RingMod (manifest), 440 Hz", "mda-lv2",
     [Plug("ringmod", MDA + "RingMod", {"freq": 0.275, "fine": 0.0})], {}),
    ("ring", "Sushi brickworks ring mod, 440 Hz, half", "Sushi 1.3.0",
     [Plug("bwring", BW + "ring_mod", {"frequency": 440.0, "amount": 0.5})], {}),
    ("ring", "swh Ringmod with LFO, 220 Hz, half", "swh-lv2",
     [Plug("swhring", SWH + "ringmod_1i1o1l", {"depthp": 1.0, "freq": 220.0, "sin": 1.0})], {}),
    ("ring", "swh Bode frequency shifter, +5 Hz", "swh-lv2",
     [Plug("bode", SWH + "bodeShifter", {"shift": 5.0})], {}),
    ("ring", "swh Harmonic generator, 2nd to 5th", "swh-lv2",
     [Plug("harmgen", SWH + "harmonicGen", {"mag_1": 1.0, "mag_2": 0.5, "mag_3": 0.3, "mag_4": 0.2, "mag_5": 0.1})], {}),

    ("other", "Sushi brickworks fuzz, full", "Sushi 1.3.0",
     [Plug("bwfuzz", BW + "fuzz", {"fuzz": 1.0, "gain": 0.5})], {}),
    ("other", "GxBigMuffPi (#155's fuzz)", "guitarix-lv2",
     [Plug("muff", G + "gx_bmp_#_bmp_", {"SUSTAIN": 0.8, "TONE": 0.4, "VOLUME": 0.4})], {}),
    ("other", "Gxshimmizita, shimmer up an octave", "guitarix-lv2",
     [Plug("shimmer", G + "gx_shimmizita_#_shimmizita_", {"SHIFT": 2.0, "T60M": 6.0, "T60DS": 6.0, "DRYWET": 0.5})], {}),
]


def strum() -> np.ndarray:
    """An E major strum at 0.2 s, ringing, then silence: 10 s."""
    x = np.zeros(10 * SR)
    for i, name in enumerate(["E2", "B2", "E3", "G#3", "B3", "E4"]):
        s = bench._string(bench.NOTE[name], 9.0, level=0.75, seed=40 + i)
        at = int((0.2 + 0.012 * i) * SR)
        x[at:at + len(s)] += s[:len(x) - at]
    return x * 10 ** (-10 / 20) / np.max(np.abs(x))


def cpu(r: sr.Result, chain: list[Plug]) -> float:
    names = [p.name for p in chain if p.name != "pacer"] + [p.name + "_to_stereo" for p in chain]
    return sum(r.timings[n][0] for n in names if n in r.timings)


def survey_one(chain: list[Plug], opts: dict, lv2: str, x: np.ndarray, events, keep: Path | None = None) -> dict:
    m: dict = {}
    cfg = sr.build(chain, lv2)
    r = sr.run(cfg, np.concatenate([x, np.zeros(4 * SR)]), lv2, keep=keep)
    if not r.ok:
        lines = [l for l in r.log.splitlines() if "error" in l.lower() or "fail" in l.lower() or "warning" in l.lower()]
        return {"error": (lines[-1] if lines else r.log[-300:]).strip()}
    y = r.out
    m["out"] = y
    m["cpu"] = cpu(r, chain)
    burst = bench.tone_burst(500.0)
    rb = sr.run(cfg, burst, lv2)
    onset = bench.onset_delay(burst, ms.mono(rb.out), 0.5) if rb.ok else None
    m["onset"] = None if onset is None else max(0, int(onset))
    delay = m["onset"] or 0
    m["level"] = ms.level(x, y)
    m["junk_notes"], m["junk_chords"] = ms.junk(y, events, delay)
    m["sub"], m["up"] = ms.octave_shares(x, y, events, delay)
    sw, marks = ms.swell_signal()
    rs = sr.run(cfg, sw, lv2)
    m["rise_gap"], m["rise_legato"] = ms.swell_rise(rs.out, marks) if rs.ok else (float("nan"),) * 2
    res = []
    for chord in ms.CHORDS:
        whole, parts = ms.chord_parts(chord)
        rc = sr.run(cfg, whole, lv2)
        rp = [sr.run(cfg, part, lv2) for part in parts]
        ok = rc.ok and all(q.ok for q in rp)
        res.append(ms.residual(rc.out, [q.out for q in rp]) if ok else float("nan"))
        if chord == "E major (open)":
            again = sr.run(cfg, whole, lv2)
            m["repeat"] = ms.repeat_difference(rc.out, again.out) if again.ok and rc.ok else float("nan")
    m["residual_emaj"], m["residual_triad"] = res
    m["tail"] = ms.tail(np.concatenate([x, np.zeros(4 * SR)]), y)
    m["lr"] = ms.lr(y)
    st = sr.run(cfg, ms.steady(), lv2)
    m["smear"] = ms.smear(st.out) if st.ok else float("nan")
    sx = ms.stress()
    ss = sr.run(cfg, sx, lv2)
    m["sane"] = ms.sane(ss.out) if ss.ok else "did not run: " + ss.log[-200:]
    if "freeze" in opts:
        proc, param = opts["freeze"]
        plug = next(p for p in chain if p.name == proc)
        if not plug.internal:   # Sushi names an LV2 parameter by its port's lv2:name
            param = sr._lv2_ports(plug.uri, lv2)["controls"][param][2]
        sx = strum()
        cfgf = sr.build(chain, lv2, events=[{"time": 0.8, "type": "parameter_change",
                                             "data": {"plugin_name": proc, "parameter_name": param, "value": 1.0}}])
        rf = sr.run(cfgf, sx, lv2)
        if rf.ok:
            env = bench.envelope(rf.out, 20)
            at = float(np.sqrt(np.mean(env[int(0.85 * SR):int(1.05 * SR)] ** 2)))
            late = float(np.sqrt(np.mean(env[int(6 * SR):int(8 * SR)] ** 2)))
            dry_late = float(np.sqrt(np.mean(bench.envelope(sx, 20)[int(6 * SR):int(8 * SR)] ** 2)))
            m["hold"] = 20 * np.log10(max(late, 1e-9) / max(at, 1e-9))
            m["hold_dry"] = 20 * np.log10(max(dry_late, 1e-9) / max(at, 1e-9))
            m["freeze_out"] = rf.out
        else:
            m["hold"] = None
    return m


def fmt(v, spec="{:.1f}"):
    if v is None or (isinstance(v, float) and not np.isfinite(v)):
        return ""
    return spec.format(v)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--extras", type=Path, required=True)
    ap.add_argument("--old-extras", type=Path, required=True)
    ap.add_argument("--farm", type=Path, required=True, help="a scratch folder for the LV2 links")
    ap.add_argument("--wav", type=Path)
    ap.add_argument("--only")
    args = ap.parse_args(argv)
    lv2 = sr.lv2_farm(args.farm, extra=[args.extras / "lv2", args.old_extras / "lv2"],
                      replace={"rubberband.lv2": args.old_extras / "rb4" / "lv2" / "rubberband.lv2"})
    x, events = bench.guitar()
    if args.wav:
        args.wav.mkdir(parents=True, exist_ok=True)
        bench.write_wav(args.wav / "00-input.wav", x)
    # the dry guitar, through an empty track, as the reference row
    dry_junk = ms.junk(x, events)
    dry_smear = ms.smear(ms.steady())
    print(f"Dry guitar: junk notes {dry_junk[0]:.1f} dB, chords {dry_junk[1]:.1f} dB; smear {dry_smear:.1f} dB")
    sw, marks = ms.swell_signal()
    print(f"Dry swell signal: rise after a gap {ms.swell_rise(sw, marks)[0]:.0f} ms, legato {ms.swell_rise(sw, marks)[1]:.0f} ms")
    print("| approach | candidate | from | onset | CPU % | level dB | junk notes | junk chords | residual E maj "
          "| residual triad | repeat | smear | sub | up | rise gap ms | rise legato ms | tail s | L/R | hold dB | stress |")
    print("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for i, (kind, name, source, chain, opts) in enumerate(CANDIDATES):
        if args.only and args.only.lower() not in name.lower() and args.only.lower() != kind:
            continue
        m = survey_one(chain, opts, lv2, x, events)
        if "error" in m:
            print(f"| {kind} | {name} | {source} | not rendered: {m['error']} |", flush=True)
            continue
        lr = fmt(m["lr"], "{:.2f}")
        hold = fmt(m.get("hold")) + (f" (dry {m['hold_dry']:.0f})" if m.get("hold") is not None else "")
        print(f"| {kind} | {name} | {source} | {fmt(m['onset'], '{}')} | {m['cpu']:.2f} | {m['level']:+.1f} "
              f"| {m['junk_notes']:.1f} | {m['junk_chords']:.1f} | {fmt(m['residual_emaj'])} "
              f"| {fmt(m['residual_triad'])} "
              f"| {'same' if m['repeat'] == float('-inf') else fmt(m['repeat'])} "
              f"| {m['smear']:.1f} | {m['sub']:.1f} | {m['up']:+.1f} "
              f"| {fmt(m['rise_gap'], '{:.0f}')} | {fmt(m['rise_legato'], '{:.0f}')} | {m['tail']:.1f} | {lr} | {hold} "
              f"| {m['sane']} |", flush=True)
        if args.wav:
            safe = "".join(ch if ch.isalnum() else "-" for ch in name).strip("-")[:60]
            bench.write_wav(args.wav / f"{i + 1:02d}-{kind}-{safe}.wav", m["out"])
            if "freeze_out" in m:
                bench.write_wav(args.wav / f"{i + 1:02d}-{kind}-{safe}-freeze.wav", m["freeze_out"])
    if sr.RETRIES:
        print(f"\nRenders that failed once and were run again: {len(sr.RETRIES)}")
        for why in sr.RETRIES:
            print(f"- {why}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
