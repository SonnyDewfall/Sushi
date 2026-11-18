# Synth research: thick, synth-like and string-machine sounds from a guitar (#166)

Written 2026-11-17. Nothing here was heard, on the rig or anywhere: every
figure comes from rendering offline. Unlike #153 and #155, the renders ran
in **Sushi 1.3.0 itself** (its AppImage, offline frontend, `--timing-statistics`
for CPU), on a stereo track built as the rig's compiler builds a channel,
with Ubuntu 24.04's packages and Rubber Band 4.0 (#164) in place of 3.3.0, on
an x86-64 container (Xeon at 2.1 GHz). The guitar is #153's synthesised one
(`../plugin_research/bench.py`); the repository holds no DI recording. No rig
file, no tool code and no config was changed.

## In plain English

Most of this can be done with things that are already packaged for Ubuntu or
built into Sushi. It also needs Rubber Band 4.0, which is already coming for
the octave (#164), and one small new build. The most synth-like route needs
one more download.

- **The single biggest step is a string-ensemble chorus**, the swirling,
  wide effect that makes a string machine sound like a string machine. The
  best one found is the chorus out of an open-source string-machine synth,
  "string-machine chorus". It is a small build. It works on chords exactly
  as it does on single notes, adds about 4 ms, and costs little. If you want
  nothing new installed, Sushi has its own chorus (brickworks) that is
  nearly as thick, though it is not stereo.
- **Stacking octaves** (an octave up, or up and down, under your notes) makes
  it bigger and more organ- or synth-like. Rubber Band 4.0, already being
  adopted for the octave pedal, does the octave up cleanly on chords. The
  octave down blurs chords a little. The octave arrives about 67 ms after
  you pick, so it suits pads more than fast playing. One catch was found:
  Rubber Band's own wet/dry knob also delays your dry guitar by about 45 ms.
  The dry has to go round it.
- **The most synth-like option is to turn the guitar into MIDI** and play a
  real software synth with it. That is possible on this rig. Sushi passes MIDI
  from one plugin to the next one on the same channel; this was tested in
  Sushi, not just read. A free plugin, GuitarMidi-LV2, can follow chords: it
  found all six notes of an open E major and the three of a power chord. A
  single note sounds about 50 ms after the pick (the low E string about
  160 ms), and it sometimes picks a wrong note. It needs its own download.
  This is the most "it's a synthesiser now" sound, and the least like the
  guitar you play.
- **Slow attack (volume swell).** The packaged gates swell nicely when you
  start from silence, but not when you play notes back to back. Only the
  MIDI-and-synth route swells on every note.
- **Infinite sustain / freeze.** Sushi's own reverb has a freeze switch that
  holds a chord for as long as it is on. It measured flat for six seconds,
  stayed stable, and needs nothing installed.
- **Ring modulators** make clangy, bell-like, out-of-tune sounds. They are
  not "thick", and they are only musical on one key.

Three chains were built and measured: a **string machine**, a **synth pad**
and a **synth lead**. Each is under "The chains" below, with what each needs.

## The answers

Ranked for "thick, synth-like or string-synth-like", best first. *Measured*
means rendered in Sushi here; *judged* means reasoned from the numbers or
the source and not tested.

| # | approach | the pick | chords | added delay | CPU % | from |
|---|---|---|---|---|---|---|
| 1 | string-machine ensemble | **string-machine chorus** (`http://jpcima.sdf1.org/lv2/string-machine-chorus`), 1 in / 2 out | yes: linear (chord residual -141 dB) | 199 samples (4 ms) | 1.4 | build (DPF; the repository BSL-1.0, the chorus plugin GPL-2.0-or-later) |
| 1b | the same, nothing to install | **Sushi's brickworks chorus** (`sushi.brickworks.chorus`) | yes: linear (-140 dB) | 0 | 0.2 | Sushi 1.3.0 |
| 2 | octave stacking | **Rubber Band Live 4.0**, octave up, wet only, beside the dry (send and return) | up: clean (junk -27 dB on chords, dry -31); down: smeared (-13) | 3,203 samples (67 ms) for the octave; the dry not at all | 4 to 5 each | build (#164) |
| 3 | guitar to MIDI to a synth | **GuitarMidi-LV2 3.2** into mda JX10 or string-machine | yes, mostly: E major 6/6, E5 3/3, A triad 2/3 + 2 wrong | about 50 ms to the right note (E2: 157) | 3.9 + synth (JX10 under 1.5, string-machine up to 16) | its author's .deb; string-machine a build |
| 4 | freeze | **Sushi freeverb** with `freeze` on | yes: linear | 0 | 0.3 | Sushi 1.3.0 |
| 5 | envelope filter, for a lead | **Calf Envelope Filter** or fomp Auto-wah (#155) after a fuzz | the fuzz does not (below) | 0 | 1.0 / 0.1 | calf-plugins / fomp |
| 6 | swell | **abGate**, 500 ms attack | yes, from silence only | the swell itself (396 ms after a gap; none legato) | 0.04 | abgate (#155) |
| 7 | fuzz, the synth-lead core | **Sushi's brickworks fuzz**, or GxBigMuffPi (#155) | single notes only (junk on chords -23 dB) | 0 | 0.8 / 0.6 | Sushi 1.3.0 / guitarix-lv2 |
| - | ring and frequency modulation | not recommended | no: inharmonic on everything (junk about -5 dB) | 0 | 0.04 to 0.5 | packaged |

What decides the order. A string machine's sound is the ensemble chorus
first. Octave layers are second; they are what turn one note into an organ or
synth register. MIDI is the most synth-like, but it is the least reliable on
guitar and needs the most new parts. Freeze, swell and filters are touches
on top. The ranking is judgement on measured figures. None of it is from
listening.

## What Sushi 1.3.0 does with MIDI from a plugin

Read in Sushi's source at the 1.3.0 tag, then tested with the AppImage
(`midi_synth.py` part 1). The tracker is the spike's own (`pitch2midi.c`).
The receiver is a probe (`midiprobe.c`) that plays every note it receives as
a sine at a frequency that names the note.

- **An LV2 plugin's MIDI output goes to the next processor on the same track,
  in the same 64-frame chunk.** `LV2_Wrapper::_process_midi_output` turns
  each MIDI event in an atom output into a Sushi event
  (`output_midi_event_as_internal`). The track caches it
  (`Track::send_event`) and hands it to the next processor before that one
  runs (`Track::_process_plugins`). *Measured:* tracker, then probe: MIDI
  arrives.
- **An internal plugin passes it on; an LV2 plugin does not.**
  `InternalPlugin::process_event` passes keyboard events through unchanged.
  `LV2_Wrapper` queues them for the plugin's own MIDI input and drops what it
  cannot use. *Measured:* through `mono_summing` (which the compiler puts
  after every one-output plugin) and through brickworks chorus, MIDI
  arrives. Through DISTRHO 3 Band EQ, GxChorus-Stereo or a synth, nothing
  arrives. **So the tracker and the synth must be next to each other**, with
  only internal plugins between them.
- **MIDI does not cross to another track.** Events nobody took leave the track
  for the engine (`_process_output_events`), which sends them to MIDI outputs
  only. *Measured:* a probe on a second track got nothing; the same probe sent
  a note by the config sounded.
- **Timing inside the chunk is lost.** Each event is given offset 0
  (`output_midi_event_as_internal(..., 0)`). *Measured:* an A3 plucked at
  sample 24,023 started the probe at sample 25,536, which is 399 x 64, a chunk
  boundary. That costs at most 1.3 ms.
- **Atom buffers are 4,096 bytes**, whatever a port asks for. Sushi ignores
  `rsz:minimumSize` (`lv2_port.cpp`, `midi_buffer_size()`). GuitarMidi's debug
  port asks for 40,000. Its forge checks capacity (`guitarmidi.cpp`), so the
  debug data is dropped and nothing else is affected. It ran.
- **A synth on a channel replaces the guitar.** A synth has no audio input.
  GuitarMidi-LV2 has no audio output, so the guitar is gone after it too.
  To keep some dry guitar, the chains here use Sushi's own send and return
  inside the track (`sushi.testing.send` passes its input on and adds it
  into the return it names; the return replaces the audio at its place). A
  send ahead of its return on one track is heard in the same chunk
  (`ReturnPlugin::send_audio`).
- **Sushi's LFO plugin cannot drive another plugin's parameter.** Its output
  is a parameter that reaches a CV output or a controller
  (`maybe_output_cv_value`); there is no parameter-to-parameter link. An LFO
  filter must have its LFO inside the plugin. *Read, not tested.*
- **Sushi 1.3.0's internal plugins** (from the AppImage's strings and
  `internal_processor_factory.cpp`): 22 brickworks (bitcrusher, cab_sim,
  chorus, clip, comb_delay, compressor, dist, drive, eq, flanger, fuzz,
  highpass, multi_filter, noise_gate, notch, phaser, ring_mod, saturation,
  simple_synth, tremolo, vibrato, wah), and the testing set (freeverb with
  freeze, lfo, transposer, arpeggiator, step sequencer, send, return,
  mono_summing, sample player and others). Every brickworks plugin's
  parameters are knobs; their ranges and warps are in `sushirun.INTERNAL`.

## Guitar to MIDI

`midi_synth.py` part 2. Each tracker feeds the probe; the render is read back
as a piano roll and scored against the notes `bench.guitar()` played.

- **right note, median ms**: from the pluck to the right note sounding,
  median of the seven single notes;
- **right** and **wrong**: share of each single note's time with the right
  note on, and with only a wrong one on;
- **quick run**: of eight quarter-second notes, how many hold the right note
  most of the time;
- **chords**: notes found (held through most of the chord) out of those
  played, and others held.

"At real time" means the spike's pacer (`pacer.c`) is first on the track.
The pacer sleeps until the wall clock catches up, so Sushi's offline
frontend runs at live speed instead of as fast as it can.

| tracker | single notes | right note, median ms | E2 ms | right | wrong | quick run | chords | CPU % | two renders |
|---|---|---|---|---|---|---|---|---|---|
| spike mono tracker | 7/7 | 31 | 33 | 92% | 2% | 8/8 | none (it is monophonic) | 1.2 | identical |
| GuitarMidi-LV2 3.2, unpaced | 6/7 | 267 | never | 68% | 8% | 3/8 | E major 3/6, triad 0/3, E5 0/3 | 5.6 | differ |
| **GuitarMidi-LV2 3.2, at real time** | 7/7 | **50** | 157 | 92% | 2% | 8/8 | **E major 6/6, triad 2/3 +2 wrong, E5 3/3 +1 wrong** | 3.9 | differ |
| GuitarMidi-LV2 3.2, input gain 12 dB, at real time | 6/7 | 50 | never | 53% | 0% | 8/8 | E major 6/6, triad 0/3, E5 3/3 | 3.9 | differ |

- **GuitarMidi-LV2** (geraldmwangi/GuitarMidi-LV2, LGPL per its LICENSE) runs a filter
  bank in `run()` and a neural network (TensorFlow Lite, 37 notes, E2 to E5,
  standard tuning) on a thread of its own. It is not packaged by Ubuntu; its
  author publishes a 24.04 `.deb` (v3.2, 4.5 MB with its model). Building it
  means building TensorFlow Lite from a submodule, which was not tried.
  - *Measured:* rendered faster than real time, it does badly (267 ms,
    3/8; an earlier unpaced run gave 137 ms). *Judged:* its network falls
    behind audio that arrives faster than live. *Measured:* paced, it finds every single
    note, the open E major and the power chord. It finds two of the triad's
    three notes and adds two wrong ones there. A note change in the quick run takes
    about 55 ms, so each quarter-second note is right for about 78 % of its
    length.
  - Its sustained notes are clean (2 % wrong). The bend comes out as wrong
    notes, as it does from the spike tracker.
  - Two renders of the same input differ, as a separate thread makes
    expected.
  - Velocity is always 127 (its README).
  - The lowest string is slowest (157 ms). At 12 dB of input gain instead
    of its 28 it lost E2 entirely, so its gain wants setting against the
    rig's real level.
  - *Judged:* on the rig the thread gets real time, as here when paced; the
    paced figures are the ones to expect. A real guitar is not this
    synthetic one, and the network is made for a clean real guitar in
    standard tuning (its README).
  - Its `AGENTS.md` says the author funds it through a commercial VST3 port
    (ArtiString). It asks that a port of its engine to other platforms be
    discussed with the author first. Using it on Linux, as here, is what it
    asks people to do.
- **The spike's mono tracker** (`pitch2midi.c`, YIN every 128 samples on a
  24 kHz copy) was written to test routing with a known tracker. It is not a
  candidate. It shows what a plain monophonic tracker can do: every single
  note right within 27 to 33 ms, deterministic, nothing on chords.
- **Nothing else was found** that turns audio into MIDI as an LV2 plugin
  and is packaged for 24.04. Searched: Ubuntu's package list for LV2 and
  aubio, and forum threads for GuitarMidi-LV2, PiPitch, note2midi, aubio
  and gxtuner.
  - `mda Tracker` is a pitch-tracking oscillator, not MIDI. It **passes its
    input through unchanged** whatever its settings, here as in #153.
  - `swh freqTracker` outputs a frequency as audio, which nothing here can
    use.
  - PiPitch (Raspberry Pi 5, Zynthian) and aubio-based note2midi were not
    found packaged and were not built.

**Synths.** `midi_synth.py` part 3. Each synth was sent A3 by the config
itself (its own response), then driven by each tracker over the guitar
(CPU, and the stress signal).

| synth | from | its attack ms | level dBFS | spike tracker + synth CPU %, stress | GuitarMidi + synth CPU %, stress |
|---|---|---|---|---|---|
| Sushi brickworks simple_synth (mono) | Sushi 1.3.0 | 0 | -12.1 | 1.7, ok | |
| mda JX10 (poly; in the manifest) | mda-lv2 | 2 | -19.1 | 2.9, ok | 4.2, ok |
| string-machine (poly, its own ensemble) | build | 12 | -19.4 | 7.4, ok | 20.0, ok |
| Calf Monosynth | calf-plugins | 2 | -22.9 | 2.8, ok | |
| DISTRHO Nekobi | dpf-plugins-lv2 | silent | | | |
| mda DX10 (FM; in the manifest) | mda-lv2 | 0 | -22.9 | 2.7, ok | |

- Pluck to synth note is the tracker's figure plus the synth's attack: about
  50 to 60 ms with GuitarMidi, 31 to 43 with the spike tracker.
- string-machine costs the most. It is polyphonic and its CPU grows with
  held notes: 20 % of a core with GuitarMidi holding chords, against 7 %
  for one note at a time.
- Nekobi made no sound in Sushi, even sent a note by the config; it was not
  chased.
- Calf Monosynth and Organ keep `state:interface`, which `sushi-rig probe`
  treats as not drivable.

## The survey

`survey.py`. One candidate at a time, in Sushi. Columns:

- **onset**: of a 500 Hz burst, samples.
- **CPU**: Sushi's average for the candidate's processors, % of one core.
- **level**: output against input, dB.
- **junk notes / chords**: energy on no harmonic of the notes or their
  sub-octaves, dB. The dry guitar has -33.6 and -31.2.
- **residual E maj / triad**: what is left of a chord's render after the sum
  of its strings' renders is taken away, dB. Below -100 is linear.
- **repeat**: two renders compared.
- **smear**: energy spread off each partial of a steady note, dB. The dry
  note is -123: higher is thicker.
- **sub / up**: the sub-octave's partials' share, dB, and how much more
  energy is on the octave above than in the dry note, dB.
- **rise gap / legato**: pluck to 90 % of the note's peak, ms, after silence
  and played legato. Dry is 5 and 3.
- **tail**: s past the input's end.
- **L/R**: correlation of the outputs.
- **hold**: for a freeze, the level 6 to 8 s after the switch against just
  after it, dB.
- **stress**: about 70 s of the guitar, full-scale noise, a DC step, a 30 Hz
  sine and silence, checked for NaN, runaway, and ringing after 9 s of
  silence.

The full definitions are in `measures.py`.

| approach | candidate | from | onset | CPU % | level dB | junk notes | junk chords | residual E maj | residual triad | repeat | smear | sub | up | rise gap ms | rise legato ms | tail s | L/R | hold dB | stress |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| octave | Rubber Band Live 4.0, octave up, half wet | build (#164) | 2111 | 4.36 | -3.6 | -35.3 | -27.1 | -1.7 | -3.6 | same | -68.2 | -60.0 | +2.1 | 75 | 76 | 0.1 | 1.00 |  | ok |
| octave | Rubber Band Live 4.0, octave down, half wet | build (#164) | 2111 | 5.07 | -3.7 | -31.6 | -13.0 | 1.5 | -1.6 | same | -22.5 | -6.4 | -4.2 | 98 | 99 | 0.1 | 1.00 |  | ok |
| octave | gx_oc_2_, dry and octave 1 (in the manifest) | guitarix-lv2 | 0 | 0.23 | +9.4 | -31.0 | -12.5 | -8.5 | -7.1 | same | -47.8 | -13.9 | -0.2 | 5 | 4 | 4.0 | 1.00 |  | still -52 dBFS after 9 s of silence |
| octave | mda SubSynth, divide, dry under it (manifest) | mda-lv2 | 0 | 0.05 | +18.6 | -23.1 | -2.7 | 5.6 | 4.9 | same | -22.7 | -0.1 | -19.2 | 5 | 3 | 0.0 | 1.00 |  | ok |
| octave | MOD Drop, octave down, wet | build (#153) | 652 | 1.15 | -2.7 | -27.1 | -7.0 | 1.9 | 3.2 | same | -15.5 | -2.7 | -26.1 | 25 | 20 | 0.0 | 1.00 |  | ok |
| ensemble | **string-machine chorus (Solina ensemble), wet** | build | 199 | 1.40 | -2.7 | -7.3 | -7.2 | -140.9 | -140.3 | same | -2.7 | -45.3 | -1.1 | 25 | 11 | 0.0 | 0.27 |  | ok |
| ensemble | string-machine chorus, dry and wet equal | build | 0 | 1.38 | -0.9 | -9.5 | -9.3 | -141.4 | -141.1 | same | -4.4 | -47.8 | -0.8 | 49 | 10 | 0.0 | 0.53 |  | ok |
| ensemble | Calf Multi Chorus, 6 voices | calf-plugins | 0 | 0.91 | +1.0 | -31.7 | -34.7 | -139.6 | -140.2 | same | -7.9 | -47.3 | +0.8 | 13 | 11 | 0.0 | 0.20 |  | ok |
| ensemble | swh Multivoice Chorus, 6 voices | swh-lv2 | 0 | 0.45 | +5.9 | -10.6 | -12.4 | -2.8 | -4.7 | -2.5 | -4.5 | -28.0 | -2.7 | 170 | 171 | 0.2 | 1.00 |  | ok |
| ensemble | fomp Triple chorus | fomp | 319 | 0.13 | +2.7 | -31.5 | -31.6 | -141.2 | -140.6 | same | -8.1 | -49.1 | -0.0 | 9 | 10 | 0.0 | -0.09 |  | ok |
| ensemble | ZynChorus (in the manifest), defaults | zynaddsubfx-lv2 | 0 | 0.20 | -4.6 | -32.2 | -29.9 | -142.6 | -142.7 | same | -11.9 | -46.1 | -1.0 | 5 | 26 | 0.0 | 0.65 |  | ok |
| ensemble | GxChorus-Stereo (the rig's chorus), deeper | guitarix-lv2 | 0 | 0.20 | +1.9 | -17.4 | -14.3 | -144.3 | -143.4 | same | -5.8 | -48.5 | +0.2 | 10 | 8 | 0.0 | 0.66 |  | ok |
| ensemble | **Sushi brickworks chorus** | Sushi 1.3.0 | 0 | 0.21 | +4.0 | -18.3 | -7.9 | -140.0 | -140.4 | same | -2.2 | -59.2 | +1.7 | 31 | 11 | 0.1 | 1.00 |  | ok |
| ensemble | mda Detune (manifest) | mda-lv2 | 40 | 0.11 | +4.8 | -7.0 | -7.1 | -139.8 | -139.2 * | same | -0.1 | -66.9 | -1.4 | 17 | 16 | 0.0 | 0.02 |  | ok |
| swell | GxSlowGear, 400 ms up, threshold 0.5 | build | 1692 | 0.05 | -6.4 | -10.2 | -31.1 | -0.3 | -3.6 | same | -123.2 | -48.5 | -0.0 | 253 | 63 | 0.0 | 1.00 |  | ok |
| swell | GxSlowGear, 400 ms up, threshold 1 (default) | build | 1692 | 0.06 | -10.6 | -9.7 | -14.0 | -0.0 | -0.3 | same | -123.2 | -28.1 | -0.8 | 187 | 163 | 0.0 | 1.00 |  | ok |
| swell | **abGate, 500 ms attack** | abgate | 1679 | 0.04 | -1.2 | -33.8 | -31.5 | -21.3 | -31.5 | same | -123.2 | -59.1 | -0.0 | 396 | 4 | 0.0 | 1.00 |  | ok |
| swell | Sushi brickworks noise gate, 0.5 s attack | Sushi 1.3.0 | 1073 | 0.11 | -1.5 | -29.7 | -30.3 | -0.4 | -10.5 | same | -123.2 | -36.8 | -0.0 | 61 | 4 | 0.0 | 1.00 |  | ok |
| swell | Calf Transient Designer, attack cut | calf-plugins | 0 | 0.51 | +1.5 | -20.2 | -13.9 | -2.8 | -2.4 | same | -81.9 | -60.9 | -0.2 | 0 | 2 | 0.0 | 1.00 |  | ok |
| swell | mda Transient, attack cut (manifest) | mda-lv2 | 0 | 0.23 | -1.7 | -34.1 | -30.8 | -10.3 | -13.2 | same | -79.3 | -56.3 | -0.0 | 5 | 3 | 0.0 | 1.00 |  | ok |
| swell | ZamGate, 500 ms attack | zam-plugins | 1691 | 0.71 | -3.0 | -33.8 | -31.5 | -9.3 | -14.9 | same | -123.2 | -61.3 | -0.1 | 396 | 4 | 0.0 | 1.00 |  | ok |
| freeze | **Sushi freeverb, freeze** | Sushi 1.3.0 | 0 | 0.27 | +9.2 | -12.6 | -17.9 | -136.0 | -136.0 | same | -50.1 | -23.4 | -0.5 | 6 | 15 | 3.8 | 0.57 | 0.5 (dry -60) | ok |
| freeze | ZamGrains, freeze | zam-plugins | 0 | 0.23 | -0.0 | -33.6 | -31.2 | -143.3 | -142.7 | same | -54.2 | -55.8 | -0.0 | 5 | 4 | 0.0 | 1.00 | -0.1 (dry -51) | ok |
| freeze | GxSustainer | guitarix-lv2 | 0 | 0.46 | +0.1 | -33.7 | -34.3 | 6.7 | -2.5 | same | -122.7 | -72.3 | -0.1 | 5 | 4 | 0.0 | 1.00 |  | ok |
| freeze | swh GVerb, 30 s | swh-lv2 | 0 | 0.97 | +7.2 | -9.9 | -15.4 | -135.3 | -134.7 | same | -30.9 | -17.0 | +1.0 | 151 | 259 | 4.0 | 0.23 |  | still -22 dBFS after 9 s of silence |
| freeze | Calf Reverb, 15 s | calf-plugins | 0 | 0.86 | +8.2 | -5.0 | -11.8 | -126.8 | -126.7 | same | -11.9 | -18.1 | -4.2 | 6 | 76 | 4.0 | 0.38 |  | still -37 dBFS after 9 s of silence |
| filter | fomp Auto-wah (#155's pick) | fomp | 0 | 0.10 | -2.6 | -39.1 | -16.3 | 1.2 | -18.3 | same | -77.6 | -46.4 | -0.3 | 6 | 5 | 0.0 | 1.00 |  | ok |
| filter | Calf Envelope Filter, activation 16 | calf-plugins | 0 | 1.01 | -4.8 | -17.0 | -15.3 | 4.4 | 3.3 | same | -122.9 | -67.9 | +0.2 | 6 | 3 | 0.0 | 1.00 |  | ok |
| filter | mda RezFilter, envelope and LFO (manifest) | mda-lv2 | 0 | 0.15 | +4.8 | -7.7 | -4.3 | 1.7 | 3.1 | same | -49.9 | -35.5 | -8.9 | 161 | 158 | 2.1 | 1.00 |  | ok |
| filter | ZynDynamicFilter, LFO up | zynaddsubfx-lv2 | 0 | 0.29 | -1.2 | -31.5 | -31.0 | -22.1 | -27.9 | same | -42.8 | -50.6 | +0.0 | 5 | 4 | 0.0 | 1.00 |  | ok |
| filter | ZynAlienWah | zynaddsubfx-lv2 | 0 | 0.10 | +2.1 | -15.6 | -10.7 | -141.4 | -140.6 | same | -7.2 | -53.9 | -0.1 | 5 | 78 | 0.0 | 0.97 |  | ok |
| ring | mda RingMod (manifest), 440 Hz | mda-lv2 | 0 | 0.14 | -3.0 | -40.3 | -45.3 | -144.9 | -144.1 | same | -0.0 | -4.1 | -0.0 | 5 | 3 | 0.0 | 1.00 |  | ok |
| ring | Sushi brickworks ring mod, 440 Hz, half | Sushi 1.3.0 | 0 | 0.16 | -4.4 | -4.9 | -5.0 | -144.8 | -144.4 | same | -9.7 | -42.2 | -1.7 | 6 | 6 | 0.0 | 1.00 |  | ok |
| ring | swh Ringmod with LFO, 220 Hz, half | swh-lv2 | 0 | 0.04 | -4.6 | -5.4 | -5.1 | -144.8 | -144.2 | same | -6.5 | -29.8 | -1.5 | 10 | 7 | 0.0 | 1.00 |  | ok |
| ring | swh Bode frequency shifter, +5 Hz | swh-lv2 | 66 | 0.46 | -6.6 | -1.9 | -0.5 | -135.4 | -134.8 | same | -0.0 | -56.4 | -4.6 | 7 | 6 | 0.0 | 0.16 |  | ok |
| ring | swh Harmonic generator, 2nd to 5th | swh-lv2 | 0 | 0.05 | -10.1 | -33.6 | -30.3 | -24.3 | -25.0 | same | -123.2 | -56.4 | -0.0 | 5 | 3 | 0.0 | 1.00 |  | ok |
| other | Sushi brickworks fuzz, full | Sushi 1.3.0 | 0 | 0.84 | +5.4 | -34.9 | -23.0 | -6.1 | -8.3 | same | -122.5 | -73.5 | +0.6 | 5 | 3 | 0.0 | 1.00 |  | ok |
| other | GxBigMuffPi (#155's fuzz) | guitarix-lv2 | 7 | 0.60 | +8.2 | -33.7 | -32.7 | -14.0 | -16.8 | same | -121.8 | -78.8 | -0.2 | 6 | 6 | 2.0 | 1.00 |  | ok |
| other | Gxshimmizita, shimmer up an octave | guitarix-lv2 | 0 | 2.96 | +12.1 | -5.6 | -6.9 | -145.9 | -144.9 | same | -15.1 | -24.0 | -0.4 | 247 | 84 | 2.0 | -0.05 |  | peak +20 dBFS |

Every candidate here loaded and ran in Sushi 1.3.0, its parameters set as
knobs from the config.

What the renders show, by approach:

- **Octave stacking.**
  - Rubber Band Live 4.0 up is the only clean octave on chords (junk -27 dB
    against the dry -31); down it smears chords (-13). The chord residual is
    high for both, because a phase vocoder does not keep each partial's
    phase; here, as in #153, junk is the figure to read.
  - **Its `wetdry` knob delays the dry by 2,142 samples (44.6 ms).** At
    `wetdry` 1, all dry, the output is the input moved 2,142 samples (best
    correlation 1.00; a one-off render). At 0.5 the best match is at 2,142
    (`chains.py`, `dry_timing`) and the burst's onset at 2,111. So a blend made in
    the plugin puts the whole guitar 45 ms late. The chains here use it
    fully wet (`wetdry` 0) beside a dry path. This matters for #164 if
    electric-octave-up will blend inside the plugin.
  - The dividers (gx_oc_2_, mda SubSynth) and MOD Drop are clean only on
    single notes (chords -12.5, -2.7, -7.0), as #153 found.
  - **gx_oc_2_ leaves a DC offset** of 0.0024 (-52 dBFS) on its output after
    the guitar stops. It is in electric-chorus and electric-clean, bypassed
    in both.
- **Ensemble.** All the chorus-type plugins here are linear (residual -126 to
  -145 dB): they treat a chord exactly as they treat single notes. Their
  junk figures are high only because detuned copies are off the harmonic
  grid. That is the thickness, not intermodulation; smear says the same
  thing directly.
  - string-machine chorus spreads the most of the stereo ones (smear -2.7,
    L/R 0.27), with a 4 ms BBD delay.
  - Calf Multi Chorus is the widest (L/R 0.20) and less spread (-7.9). It
    has Calf's library behind it (#155).
  - Sushi's brickworks chorus is as spread (-2.2) but mono (L/R 1.00), and
    needs nothing.
  - mda Detune moves everything off pitch (smear -0.1); it is a detuner, not
    an ensemble.
  - swh Multivoice Chorus is not linear (residual -2.8, and two renders
    differ: it is random) and swells by itself (170 ms).
- **Swell.**
  - The gates (abGate, ZamGate, GxSlowGear, Sushi's noise gate) open slowly
    only from silence: 250 to 400 ms after a gap, 4 ms legato. A note
    played while the last still rings is not swelled. GxSlowGear at its
    default threshold also swells legato (163 ms) but closes on a note as
    it decays: 41 dB down 0.6 s in, measured on the swell signal.
  - Calf Transient Designer, attack cut, lets the first 5 ms of the pick
    through (-15 dB against dry) and then dips (-85 dB at 50 ms) before
    recovering. That is a click and a swell, not a swell.
  - Sushi's noise gate's `attack` is its detector's, not a fade-in. At
    threshold -30 and attack 1 s it never opened.
  - Only a synth's own attack, through MIDI, swells every pick.
- **Freeze.**
  - Sushi's freeverb `freeze`, switched on 0.6 s after an E major strum by an
    event in the config, held the level within 0.5 dB six seconds later; the
    dry strum had fallen 60 dB.
  - ZamGrains' freeze held it within 0.1 dB, but plays grains of the buffer
    (smear -54) and is plain pass-through when off.
  - Long reverbs (GVerb 30 s, Calf 15 s) are still ringing 9 s after the
    stress signal ends, as they should. GxSustainer is a compressor-like
    sustainer: no tail of its own.
- **Filters.** fomp Auto-wah and Calf Envelope Filter sweep on the pick
  (#155). mda RezFilter (already in the manifest) does envelope and LFO
  together, darker (up -8.9) and with a slow rise (161 ms). ZynAlienWah is a
  linear LFO filter, wide (smear -7.2). ZynDynamicFilter is level-dependent (residual -22) and was
  not looked at further.
- **Ring and frequency.** With a fixed carrier, every note comes out mostly
  off its own harmonics (junk -2 to -5 dB) except where the carrier happens
  to fit. mda RingMod's figures (junk -40, smear 0, sub -4) suggest that at
  `freq` 0.275 it is not a 440 Hz carrier. It was not chased. A ring
  modulator tuned to the note needs a tracker, and mda Tracker, which would
  be one, does nothing. swh's harmonic generator adds harmonics to single
  notes and intermodulates chords (residual -24).
- **Other.**
  - Sushi's brickworks fuzz squares single notes cleanly (junk -34.9) and
    intermodulates chords (-23.0): it is the synth-lead core.
  - **Gxshimmizita runs away**: on the stress signal its output reached
    +20 dBFS (#155 ranked it ninth). On the guitar alone it was fine.

## The chains

`chains.py`. Each chain is one stereo track. The dry and wet paths are
Sushi's send and return inside it. Columns are the survey's, with the
tracker's figures for the MIDI chains taken from part 2 above.

| chain | onset | CPU % | level dB | junk notes | junk chords | residual E maj | smear | rise gap ms | rise legato ms | tail s | L/R | stress |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| **string machine** | 595 | 5.95 | -6.3 | -6.8 | -5.7 | 0.8 | -2.9 | 503 | 20 | 2.6 | 0.26 | ok |
| string machine, no shifter | 553 | 1.83 | -5.1 | -6.5 | -7.4 | -5.1 | -3.0 | 534 | 20 | 2.6 | 0.27 | ok |
| **synth pad (MIDI)** | 0 | 17.56 | +12.2 | -4.8 | -4.6 | 3.4 | -51.8 | 554 | 423 | 4.0 | 0.29 | ok |
| synth pad (audio) | 688 | 7.64 | +1.1 | -7.4 | -7.6 | 2.1 | -2.7 | 434 | 30 | 3.4 | 0.19 | ok |
| **synth lead (audio)** | 30 | 4.54 | -6.4 | -18.1 | -17.9 | 5.3 | -67.0 | 8 | 6 | 0.9 | 1.00 | ok |
| synth lead (MIDI) | 2482 | 13.91 | +5.9 | -14.3 | -11.7 | 7.1 | 0.0 | 214 | 84 | 4.0 | 1.00 | ok |

The chains, in order on the track (the compiler's `_to_stereo` copies not
listed):

- **String machine** (recommended): GxCompressor (ratio 4, -30 dB) → send
  the dry to `mix` (-3 dB) → Rubber Band Live 4.0, octave up, wet only → send
  it to `mix` (-6 dB) → return `mix` → abGate (500 ms attack, -45 dB) →
  string-machine chorus (wet -3 dB) → GxZita_rev1 (5 s). Everything works on
  chords. The dry guitar is not delayed: with the dry sent round Rubber Band,
  the output's best match with the dry is at lag 0, against 2,142 samples
  for Rubber Band's own blend (`chains.py`, `dry_timing`). The octave comes 67 ms behind it. It swells from
  silence (503 ms after a gap), not legato (20 ms). It is stereo (L/R 0.26)
  at 6 % of a core. The ensemble's spread is the thickness: smear -2.9 dB,
  against the dry guitar's -123. Without the octave ("no shifter") it costs
  1.8 % and needs nothing but the ensemble built.
- **Synth pad (MIDI)** (the most synth-like): the pacer (measurement only) →
  send the dry to `mix` (-12 dB) → GuitarMidi-LV2 → string-machine synth
  (attack 0.4 s, release 1.5 s) → send it to `mix` → return `mix` →
  GxZita_rev1.
  - Notes as in part 2: about 50 ms to the right note, chords mostly right.
    The synth's own attack makes it swell on every note, legato too (423 ms):
    the one chain that does.
  - 17.6 % of a core, most of it the synth holding chords.
  - Its junk figures count the synth's own detuned oscillators and the notes
    the tracker gets wrong together. Read part 2 for the notes.
- **Synth pad (audio)**: as the string machine, with Rubber Band an octave
  down too, in its own parallel path (send to a second return, shift, send
  to `mix`), and Sushi's freeverb before the hall, so a freeze switch is
  there to map. The sub-octave is present (sub -6.5 dB, against -25.7
  without). The octave down smears chords as in the survey. 7.6 %.
- **Synth lead (audio)** (recommended for leads): GxCompressor → Sushi's
  brickworks fuzz (full) → Calf Envelope Filter (activation 8, resonance 6,
  300 Hz to 2.5 kHz) → GxEchoCat.
  - 30 samples of delay, 4.5 % of a core. Single notes only: junk -18 dB
    across the board, the echo's overlapping repeats included.
  - The synth-lead sound of a fuzz into a resonant envelope filter.
    Everything in it is packaged or in Sushi.
- **Synth lead (MIDI)**: the pacer → GuitarMidi-LV2 → Calf Monosynth (glide
  40 ms, legato) → GxEchoCat.
  - Its sound is a synth's, 52 ms (2,482 samples) behind the pick.
  - GuitarMidi is polyphonic and the Monosynth plays one note of what it
    sends, so a chord is one note.
  - 13.9 %.

What would make these real on the rig (none of it done):

- **Plugins**:
  - string-machine chorus and string-machine (built from source with no
    dependencies beyond lv2-dev and a C++ compiler; `build_extras.sh` builds
    everything it makes in about 30 s here);
  - GuitarMidi-LV2 (the author's `.deb`, or TensorFlow Lite from source);
  - Rubber Band 4.0 (#164);
  - abgate (#155), calf-plugins for the Envelope Filter and Monosynth, and
    guitarix-lv2 (already in CI).
  - Each needs its `plugin-manifest.txt` and `plugin-ports.txt` lines:
    string-machine chorus `1 2`, string-machine `0 2`, GuitarMidi `1 0`.
- **The compiler**, which today knows none of this:
  - a send and return inside a channel, for a dry path beside a shifter or a
    synth;
  - a plugin with no audio input (a synth) or no audio output (GuitarMidi)
    on a channel;
  - a rule that nothing but internal plugins sits between a tracker and its
    synth;
  - a parameter to switch freeverb's freeze from a MIDI pedal, which the MIDI
    map (#34) may already allow.
  - *Judged*, not checked against the compiler's tests.

## How it is measured

- **In Sushi.** `sushirun.py` writes a config like the rig compiler's
  (stereo track, guitar on both halves, `mono_summing` after each
  one-output LV2 plugin, settings normalised as Sushi reads them: LV2 by
  port range under the port's `lv2:name`, internal plugins by their
  preprocessor) and runs the AppImage offline. CPU is Sushi's own
  `timings.txt` average.
- **Cross-check against #153's bench host.** GxBigMuffPi, string-machine
  chorus and Rubber Band Live, rendered by `host.c` and by Sushi, agree to
  within 2.4e-7 (float rounding). The bench host's figures in #153 and #155
  stand.
- **The rig's LV2 path** is Ubuntu's `/usr/lib/lv2` with `rubberband.lv2`
  swapped for 4.0's (`sushirun.lv2_farm`), plus the builds.
- **CPU** varies by up to half between runs in this container (Rubber Band
  up: 4.2 to 6.2 %), and the rig (an XPS 9320, `machines/`) is a different
  machine. Read the figures against each other.
- **Sushi's offline frontend failed about 2.5 % of runs**, with no plugin
  loaded as often as with one. It aborted in gRPC's server at shutdown
  (`server_cc.cc:241 Check failed: grpc_server_request_registered_call`,
  exit -6), or wrote an empty file and exited 0. `sushirun.run` runs a
  failed render once more and reports it. In the full survey one failed
  twice (mda Detune's triad residual); that cell (*) is from a rerun of that
  candidate alone. *Judged:*
  a race in the offline frontend's shutdown, not something the realtime rig
  meets.
- **Swell, chords and freeze** have their own signals (`measures.py`); the
  freeze is switched by a `parameter_change` event in the config.

## Reproducing

```bash
# the packaged plugins: as ../plugin_research/README.md, plus
sudo apt-get install abgate calf-plugins zam-plugins fomp python3-lilv python3-numpy
# MOD's shifters and Rubber Band 4.0
tool/spikes/plugin_research/build_extras.sh /tmp/extras
# string-machine and its chorus, GxSlowGear, GuitarMidi-LV2's package, the spike's plugins
tool/spikes/synth_research/build_extras.sh /tmp/synth-extras
export SUSHI_APPIMAGE=/path/to/squashfs-root/AppRun
A="--extras /tmp/synth-extras --old-extras /tmp/extras --farm /tmp/synth-lv2"
python3.12 tool/spikes/synth_research/survey.py $A [--only TEXT] [--wav DIR]   # about 20 min
python3.12 tool/spikes/synth_research/midi_synth.py $A [--wav DIR]            # about 6 min
python3.12 tool/spikes/synth_research/chains.py $A [--only TEXT] [--wav DIR]  # about 15 min: the MIDI chains run at real time
```

The scripts:

- `sushirun.py`: configs, the AppImage, `timings.txt`, the LV2 path.
- `measures.py`: the measures.
- `survey.py`, `midi_synth.py`, `chains.py`: the three surveys.
- `build_extras.sh`: what no package provides.
- `pitch2midi.c`, `midiprobe.c`, `pacer.c` (with their `.ttl`): the spike's
  own tracker, probe and pacer, measuring tools and not candidates.

`../plugin_research/` provides the guitar, the spectra and the pitch tracker
(`bench.py`), `inspect_plugins.py` for each plugin's ports and features, and
Rubber Band 4.0.

## Not done

- **Nothing was heard**, and the guitar is synthetic. Trackers in
  particular may do better or worse on a real pickup. Every setting is a guess, not tuned by
  ear.
- **Not run on the rig or in realtime Sushi.** Everything ran in the offline
  frontend. GuitarMidi was paced to real time by a plugin, not by an audio
  device. Its thread may behave differently under the rig's scheduling.
- **Not tried:**
  - building GuitarMidi-LV2 or PiPitch from source;
  - other synths packaged for 24.04 (synthv1, padthv1, amsynth, so-synth,
    setBfree, ZynAddSubFX, whose parameters are 16 generic slots and which
    needs the worker), and the string-machine synth's presets;
  - a vocoder (mda Vocoder needs a carrier on its second input);
  - MOD's 2Voices (two shifted voices, no dry);
  - Rubber Band's formant option;
  - slowmo.lv2;
  - a ring modulator tuned by a tracker.
- The chains' parallel paths use Sushi's send and return, which a rig file
  cannot express today.
