"""#166: whole chains, a "string machine", a "synth pad" and a "synth lead",
rendered and measured in the real Sushi 1.3.0.

    python3.12 tool/spikes/synth_research/chains.py --extras DIR --old-extras DIR2 --farm SCRATCH [--wav OUT]

Each chain is one stereo track, built as the rig's compiler builds a channel
(sushirun.build), with the measures of survey.py (survey_one) and, for the
chains that go through MIDI, the delay from the pluck to the synth's note
(midi_synth.synth_delay). The parallel paths (a dry guitar beside an octave
or a synth) are Sushi's own send and return plugins inside the one track:
`sushi.testing.send` passes its input on and adds it into the return it names;
`sushi.testing.return` replaces the audio at its place with what was sent to
it. A send ahead of its return on the same track is heard in the same chunk
(return_plugin.cpp `send_audio`), so the dry path is not delayed. The rig's
compiler does not let a rig file use them today (README: what the rig would
need).

The chains through GuitarMidi-LV2 start with the spike's pacer, which makes
the offline render run at real time (midi_synth.py says why); the pacer is not
counted in CPU and is not part of the chain on a rig.

Settings are musical guesses, not tuned; they were not listened to.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import midi_synth  # noqa: E402
import sushirun as sr  # noqa: E402
import survey  # noqa: E402
from measures import SR, bench  # noqa: E402
from sushirun import Plug  # noqa: E402

G = "http://guitarix.sourceforge.net/plugins/"
RB = "http://breakfastquay.com/rdf/lv2-rubberband#livemono"
SEND, RETURN = "sushi.testing.send", "sushi.testing.return"


def send(name: str, to: str, gain_db: float = 0.0) -> Plug:
    return Plug(name, SEND, {"gain": gain_db}, {"destination_name": to})


COMPRESSOR = Plug("compressor", G + "gx_compressor#_compressor",
                  {"RATIO": 4.0, "THRESHOLD": -30.0, "KNEE": 3.0, "ATTACK": 0.005, "RELEASE": 0.3})
SWELL = Plug("swell", "http://hippie.lt/lv2/gate",
             {"switch": 1, "threshold": -45.0, "attack": 500.0, "hold": 50.0, "decay": 400.0})
PACER = Plug("pacer", "urn:sushi-rig:spike:pacer")   # GuitarMidi needs real time (midi_synth.py)
HALL = Plug("hall", G + "gx_zita_rev1_stereo#_zita_rev1_stereo",
            {"MID_RT60": 5.0, "LOW_RT60": 5.0, "DRY_WET_MIX": -0.3, "IN_DELAY": 40.0})

CHAINS = [
    ("string machine",
     "compressor; dry and Rubber Band 4.0 an octave up (wet) in parallel; a swell from "
     "silence (abGate, 500 ms attack); the string-machine Solina ensemble; a long hall",
     [COMPRESSOR,
      send("dry_to_mix", "mix", -3.0),
      Plug("octave_up", RB, {"octaves": 1, "wetdry": 0.0}),
      send("up_to_mix", "mix", -6.0),
      Plug("mix", RETURN),
      SWELL,
      Plug("ensemble", "http://jpcima.sdf1.org/lv2/string-machine-chorus", {"wet_gain": -3.0, "dry_gain": -60.0}),
      HALL],
     False),
    ("string machine, no shifter",
     "the same without the octave: compressor, swell, Solina ensemble, hall (nothing to build but the ensemble)",
     [COMPRESSOR,
      SWELL,
      Plug("ensemble", "http://jpcima.sdf1.org/lv2/string-machine-chorus", {"wet_gain": -3.0, "dry_gain": -60.0}),
      HALL],
     False),
    ("synth pad (MIDI)",
     "a little dry guitar beside GuitarMidi-LV2 (polyphonic guitar-to-MIDI) playing the string-machine "
     "synth (its own ensemble, slow attack); a long hall",
     [PACER,
      send("dry_to_mix", "mix", -12.0),
      Plug("tracker", "http://github.com/geraldmwangi/GuitarMidi-LV2", {}),
      Plug("synth", "http://jpcima.sdf1.org/lv2/string-machine-mk2", {"env_attack": 0.4, "env_release": 1.5}),
      send("synth_to_mix", "mix", 0.0),
      Plug("mix", RETURN),
      HALL],
     True),
    ("synth pad (audio)",
     "no MIDI: dry, Rubber Band 4.0 an octave down and an octave up in parallel; swell; Solina ensemble; "
     "Sushi's freeverb (its freeze is a switch for later); hall",
     [COMPRESSOR,
      send("dry_to_mix", "mix", -3.0),
      send("dry_to_down", "down_in", 0.0),
      Plug("octave_up", RB, {"octaves": 1, "wetdry": 0.0}),
      send("up_to_mix", "mix", -8.0),
      Plug("down_in", RETURN),
      Plug("octave_down", RB, {"octaves": -1, "wetdry": 0.0}),
      send("down_to_mix", "mix", -4.0),
      Plug("mix", RETURN),
      SWELL,
      Plug("ensemble", "http://jpcima.sdf1.org/lv2/string-machine-chorus", {"wet_gain": -3.0, "dry_gain": -60.0}),
      Plug("freeverb", "sushi.testing.freeverb", {"dry": 1.0, "wet": 0.25, "room_size": 0.85, "damp": 0.4, "width": 1.0}),
      HALL],
     False),
    ("synth lead (audio)",
     "compressor; Sushi's brickworks fuzz (a square-ish wave from a single note); Calf Envelope Filter "
     "(the sweep on each pick); a tape echo",
     [COMPRESSOR,
      Plug("fuzz", "sushi.brickworks.fuzz", {"fuzz": 1.0, "gain": 0.4}),
      Plug("filter", "http://calf.sourceforge.net/plugins/EnvelopeFilter",
           {"gain": 8.0, "res": 6.0, "upper": 2500.0, "lower": 300.0, "mix": 1.0}),
      Plug("echo", G + "gxechocat#echocat", {"head1": 1, "swell": 0.4, "sustain": 0.3, "bpm": 100})],
     False),
    ("synth lead (MIDI)",
     "GuitarMidi-LV2 into Calf Monosynth (a mono synth with glide); a tape echo",
     [PACER,
      Plug("tracker", "http://github.com/geraldmwangi/GuitarMidi-LV2", {}),
      Plug("synth", "http://calf.sourceforge.net/plugins/Monosynth", {"portamento": 40.0, "legato": 1}),
      Plug("echo", G + "gxechocat#echocat", {"head1": 1, "swell": 0.4, "sustain": 0.3, "bpm": 100})],
     True),
]


def dry_timing(lv2: str, x: np.ndarray) -> list[str]:
    """Where the dry guitar lands: Rubber Band's own blend against a dry path
    sent round it. The lag at which the output best matches the input, samples."""
    import measures as ms
    cases = [("Rubber Band Live 4.0, octave up, wetdry 0.5", [Plug("up", RB, {"octaves": 1, "wetdry": 0.5})]),
             ("dry sent round Rubber Band (wet only), both returned",
              [send("dry", "mix"), Plug("up", RB, {"octaves": 1, "wetdry": 0.0}), send("wet", "mix"), Plug("mix", RETURN)])]
    out = []
    for name, chain in cases:
        r = sr.run(sr.build(chain, lv2), x, lv2)
        lag, corr = bench.xcorr_delay(x, ms.mono(r.out), max_lag=9600)
        out.append(f"- {name}: best match with the dry guitar at a lag of {lag} samples (correlation {corr:.2f})")
    return out


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--extras", type=Path, required=True)
    ap.add_argument("--old-extras", type=Path, required=True)
    ap.add_argument("--farm", type=Path, required=True)
    ap.add_argument("--wav", type=Path)
    ap.add_argument("--only")
    args = ap.parse_args(argv)
    lv2 = sr.lv2_farm(args.farm, extra=[args.extras / "lv2", args.old_extras / "lv2"],
                      replace={"rubberband.lv2": args.old_extras / "rb4" / "lv2" / "rubberband.lv2"})
    x, events = bench.guitar()
    if args.wav:
        args.wav.mkdir(parents=True, exist_ok=True)
        bench.write_wav(args.wav / "00-input.wav", x)
    print("| chain | onset | pluck to note ms | CPU % | level dB | junk notes | junk chords | residual E maj | smear "
          "| rise gap ms | rise legato ms | tail s | L/R | stress |")
    print("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for name, what, chain, uses_midi in CHAINS:
        if args.only and args.only.lower() not in name.lower():
            continue
        m = survey.survey_one(chain, {}, lv2, x, events)
        if "error" in m:
            print(f"| {name} | not rendered: {m['error']} |", flush=True)
            continue
        delay = midi_synth.synth_delay(m["out"], events)
        f = survey.fmt
        print(f"| {name} | {f(m['onset'], '{}')} | {delay:.0f} | {m['cpu']:.2f} | {m['level']:+.1f} "
              f"| {m['junk_notes']:.1f} | {m['junk_chords']:.1f} | {f(m['residual_emaj'])} | {m['smear']:.1f} "
              f"| {f(m['rise_gap'], '{:.0f}')} | {f(m['rise_legato'], '{:.0f}')} | {m['tail']:.1f} "
              f"| {f(m['lr'], '{:.2f}')} | {m['sane']} |", flush=True)
        if args.wav:
            safe = "".join(ch if ch.isalnum() else "-" for ch in name).strip("-")
            bench.write_wav(args.wav / f"chain-{safe}.wav", m["out"])
    print()
    for line in dry_timing(lv2, x):
        print(line)
    print()
    for name, what, chain, _ in CHAINS:
        print(f"- **{name}**: {what}. Processors: {', '.join(p.name for p in chain)}.")
    if sr.RETRIES:
        print(f"\nRenders that failed once and were run again: {len(sr.RETRIES)}")
        for why in sr.RETRIES:
            print(f"- {why}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
