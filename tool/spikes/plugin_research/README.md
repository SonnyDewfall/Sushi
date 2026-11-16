# Plugin research: an octave that works (#153), and pedals to add (#155)

Written 2026-11-16. Nothing here was heard on the rig, and nothing ran in
Sushi. Every figure comes from rendering a plugin offline in `host.c`, a
bench host that loads a plugin with exactly the host features Sushi 1.3.0
offers (below), on Ubuntu 24.04 and its packages, on an x86-64 container
(Xeon at 2.1 GHz). The guitar is synthesised: the repository holds no DI
recording. No rig file, no tool code and no manifest was changed.

## The answers

**#153.** Nothing packaged for Ubuntu 24.04 is good enough. The one
candidate that does everything today's octave does, without its NaN, is
**Rubber Band 4.0's Live shifter** (`http://breakfastquay.com/rdf/lv2-rubberband#livemono`),
and it has to be built: Ubuntu 24.04 packages Rubber Band 3.3.0, which has
no Live shifter. Its latency port says **3,421 samples** an octave up (4,190
down); a 500 Hz burst comes out after **3,203 samples (67 ms)** at 48 kHz.
That is about today's: gx_detune_ at the settings electric-octave-up uses
reports 1,984 and its octave arrives 3,142 samples (65 ms) after the dry
note. It is the cleanest octave up here, on single notes and on chords,
deterministic, and clean under valgrind; down, only the slower R3 and
gx_detune_ itself are cleaner on chords. If "works" has to mean *low latency* as well, nothing
here does it on chords: the fastest clean single-note shifter, MOD's Drop
(octave down only, also built from source), takes 652 samples (14 ms) and
smears chords.

**#155.** Ranked shortlist, best first; each was rendered.

| # | pedal | for | `plugin-manifest.txt` line | CI image package |
|---|---|---|---|---|
| 1 | GxBigMuffPi | fuzz: the rig has none; three knobs, no latency, mono | `http://guitarix.sourceforge.net/plugins/gx_bmp_#_bmp_` | guitarix-lv2 (already there) |
| 2 | fomp Auto-wah | the only auto-wah here that opens on the pick and closes as the note dies, at this level | `http://drobilla.net/plugins/fomp/autowah` | fomp (new; no dependencies) |
| 3 | GxTremolo | tremolo: an 11 dB swing at the rate asked, no latency, mono | `http://guitarix.sourceforge.net/plugins/gx_tremolo#_tremolo` | guitarix-lv2 |
| 4 | GxZita_rev1-Stereo | the long, ambient reverb the rig lacks (RT60 to 8 s), no atom ports, 0.4 % CPU | `http://guitarix.sourceforge.net/plugins/gx_zita_rev1_stereo#_zita_rev1_stereo` | guitarix-lv2 |
| 5 | abGate | a noise gate, for NAM at gain and the fuzz: hiss after the notes went to silence | `http://hippie.lt/lv2/gate` | abgate (new; pulls Qt 5 for its GUI) |
| 6 | GxPhaser | phaser: three knobs, mono, no latency | `http://guitarix.sourceforge.net/plugins/gx_phaser#_phaser` | guitarix-lv2 |
| 7 | mda Leslie | rotary speaker, the vibrato the rig lacks; already in the manifest | `http://drobilla.net/plugins/mda/Leslie` (present) | mda-lv2 (already there) |
| 8 | GxFlanger | flanger: the deepest sweep of the mono ones, 0.07 % CPU | `http://guitarix.sourceforge.net/plugins/gx_flanger#_flanger` | guitarix-lv2 |
| 9 | Gxshimmizita | shimmer: a reverb whose tail climbs in pitch | `http://guitarix.sourceforge.net/plugins/gx_shimmizita_#_shimmizita_` | guitarix-lv2 |
| 10 | GxEchoCat | tape echo with three heads, beside the rig's clean delays | `http://guitarix.sourceforge.net/plugins/gxechocat#echocat` | guitarix-lv2 |

Each also needs its line in `plugin-ports.txt` (inputs, outputs, URI):
`1 1` for all but GxZita_rev1-Stereo, Gxshimmizita and mda Leslie, which are
`2 2`. Seven of the ten come from guitarix-lv2, which CI already installs.
No looper made the list: the one packaged, guitarix's `gx_livelooper_`, keeps
its loops through `state:interface`, which `sushi-rig probe` treats as not
drivable, and it was not tried.

## What Sushi 1.3.0 hosts

Read from its source at the 1.3.0 tag (`src/library/lv2/`), not run:

- **Host features**, and only these: urid map and unmap, log, worker
  schedule, state makePath, options (sample rate, min and max block 64,
  sequence size 4096), state loadDefaultState, and the three buf-size ones
  (powerOf2, fixed, bounded); and lv2core isLive by name. A plugin requiring
  any other feature is refused at load (`_check_for_required_features`).
  `inspect_plugins.py` checks this for any URI; every plugin in the tables
  passes.
- **Atom ports** are connected. An input gets MIDI from Sushi's event queue,
  transport position when it changes, and `patch:Get` when an update is
  asked for; an output is read for MIDI only. A plugin that uses an atom port
  for its GUI (all of Calf, LSP, Dragonfly, x42) loads and runs; anything it
  says on it is lost.
- **Parameters are control ports only.** `patch:writable` properties are
  listed internally but never become parameters, so a plugin driven by them
  is not drivable from a rig file (the repository's long-standing finding).
- **The worker** is supported: jobs go to a non-real-time thread, answers
  come back at the start of a later chunk. Requests and answers are copied
  into 1,024-byte slots; a request larger than that is refused, an answer
  larger is copied without a check (`lv2_worker_respond`). The source's own
  comment says only guitarix plugins were tried.
- **State**: the plugin's default state is applied at load
  (`lilv_state_new_from_world`); presets are programs. A plugin's
  `state:interface` is otherwise unused by a rig file.
- **CV ports are not supported** (`case PortType::TYPE_CV: // CV Support not
  yet implemented.`): such a port is left unconnected.
- **Latency is read and not compensated**: the wrapper stores a
  `reportsLatency` port's value, with `// TODO: Introduce latency
  compensation reporting to Sushi`. A shifter's latency is what the player
  hears.
- **Channels**: a plugin gets min(its ports, the track's channels) of each
  (`plugin-ports.txt`). A stereo effect is stereo only on a stereo track; on
  the rig's mono channels it runs with one input and one output.

Found on the way, not fixed:

- **Two URIs in `plugin-manifest.txt` cannot be loaded from Ubuntu 24.04's
  package.** rubberband-lv2 3.3.0's Turtle declares
  `lv2-rubberband#r3mono` and `#r3stereo`, but its library exports
  `lv2-rubberband-r3#mono` and `-r3#stereo`. lilv lists them, so
  `plugins check` passes; instantiating fails with `No plugin
  <...#r3mono> in <file:///usr/lib/lv2/rubberband.lv2/lv2-rubberband.so>`,
  so Sushi would refuse them. Rubber Band 4.0.0's changelog: "Fix incorrect
  URIs in LV2 plugins". No rig uses them.
- **swh's pitchScaleHQ cannot be loaded either**: Ubuntu 24.04's
  `pitch_scale-swh.lv2/plugin-linux.so` has an undefined `fftwf_execute`
  (the library is not linked against FFTW). With FFTW preloaded by hand it
  works (3,840-sample latency port).
- **mda Tracker passes its input through unchanged** whatever its settings,
  in lv2apply and in the bench host alike (largest difference 7e-9).

## How it is measured

- **`host.c`**, a bench host (built by `bench.py`). It refuses what Sushi
  refuses, applies the default state, runs 64-frame chunks with flush-to-zero,
  feeds atom inputs an empty sequence, runs worker jobs at once, maps
  channels as Sushi does, and reports every output control port (the latency
  port) and the time spent inside `run()`. Checked against lilv's
  `lv2apply` on five plugins (gx_oc_2_, gxts9, mda Detune, mda SubSynth, swh
  divider): identical to the bit. lv2apply itself crashes on MaPitchshift and
  pitchScaleHQ.
- **The guitar** (`bench.guitar()`): 22.5 s at 48 kHz, peaking at -10 dBFS.
  Plucked notes from summed partials, slightly stretched as a real string's
  are, shaped by a pluck near the bridge and a pickup (so the second harmonic
  is stronger than the fundamental, as on a bridge pickup), with a short noise
  transient: seven notes E2 to A4, a run of eight quarter-second notes, A3
  bent a tone, an E major open chord, an A major triad with no doubled
  octaves, and an E5 power chord.
- **CPU** is time inside `run()` over the audio's length, the fastest of
  three renders, on one core of this machine. Read the figures against each
  other; the rig's CPU is different.
- **Latency** in samples at 48 kHz: the latency port where there is one, and
  the onset of a 500 Hz burst in the output (to -20 dB of its later level).

## #153: the octave

`octave.py` renders every candidate fully wet over the guitar. Columns:
latency port and burst onset (samples); **attack**, how much later than the
dry pluck the octave reaches half its peak (ms, median of seven notes);
**pitch err** and **in tune**, error from the true octave and share of 10 ms
frames within 35 cents (single notes, after the shifter's own delay is taken
out); **run**, how many of the eight quick notes land on the octave; **bend**,
median error through the bend; **junk**, energy on no harmonic of the note or
its octave, as a share of all (dB, lower is cleaner), on single notes and on
the E major chord (compare within a direction only: the octave-down grid is
twice as dense); **odd partials**, up: what is left of the note's own odd
harmonics (lower is a cleaner octave up), down: the new sub-octave partials'
share (near 0 dB means they dominate); **triad**, up: the triad's own
fundamentals left, down: the triad's sub-octaves' share; level change; CPU.

| candidate | way | latency port | 500 Hz onset | attack ms | pitch err c | in tune | run | bend err c | junk notes dB | junk E maj dB | odd partials dB | triad dB | level dB | CPU % |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| gx_detune_ (today's), high quality | up | 1984 | 3142 | 87 | 0 | 100% | 8/8 | 0 | -39.9 | -9.8 | -43.0 | -72.1 | -0.5 | 0.33 |
| gx_detune_, realtime | up | 0 | 150 | 4 | 1200 | 0% | 0/8 | 1200 | -34.5 | -8.3 | -3.3 | -33.1 | -6.0 | 0.40 |
| gx_detune_ (today's), high quality | down | 1984 | 3581 | 81 | 0 | 100% | 8/8 | 0 | -30.3 | -25.1 | -2.5 | -2.2 | +2.4 | 0.33 |
| gx_detune_, realtime | down | 0 | 147 | 4 | 4 | 40% | 8/8 | 1199 | -3.8 | -1.6 | -8.6 | -13.9 | -1.2 | 0.40 |
| gx_oc_2_, octave 1 | down |  | 0 | 1 | 1 | 57% | 4/8 | 0 | -24.0 | -2.2 | -0.6 | -10.3 | +2.6 | 0.21 |
| Rubber Band R2 mono 3.3 | up | 8192 | 7393 | 157 | 1 | 100% | 8/8 | 2 | -13.0 | -3.0 | -54.0 | -39.7 | -1.8 | 5.51 |
| Rubber Band R2 mono 3.3 | down | 8192 | 8828 | 198 | 2 | 98% | 8/8 | 3 | -4.1 | -3.8 | -5.2 | -5.9 | -1.5 | 2.14 |
| Rubber Band R3 mono 3.3 * | up | 8192 | 6921 | 145 | 0 | 100% | 8/8 | 0 | -39.7 | -30.2 | -47.2 | -81.4 | -0.0 | 8.41 |
| Rubber Band R3 mono 3.3 * | down | 8192 | 10099 | 219 | 0 | 100% | 8/8 | 0 | -30.9 | -30.6 | -2.8 | -3.9 | -0.1 | 3.83 |
| **Rubber Band Live mono 4.0** | up | 3421 | 3203 | 70 | 0 | 100% | 8/8 | 0 | -40.1 | -36.6 | -71.4 | -76.5 | -0.1 | 3.33 |
| **Rubber Band Live mono 4.0** | down | 4190 | 3237 | 80 | 0 | 100% | 8/8 | 0 | -30.7 | -14.1 | -2.8 | -7.6 | -0.5 | 3.72 |
| Rubber Band R3 mono 4.0 | up | 8192 | 6921 | 145 | 0 | 100% | 8/8 | 0 | -39.7 | -30.2 | -47.2 | -81.4 | -0.0 | 7.80 |
| Rubber Band R3 mono 4.0 | down | 8192 | 10099 | 219 | 0 | 100% | 8/8 | 0 | -30.9 | -30.6 | -2.8 | -3.9 | -0.1 | 3.81 |
| MaPitchshift | up |  | 1170 | 48 | 2 | 100% | 8/8 | 1 | -5.3 | -1.3 | -77.8 | -87.7 | -1.3 | 0.18 |
| MaPitchshift | down |  | 975 | 54 | 2 | 96% | 8/8 | 1 | -4.9 | -4.2 | -5.5 | -5.5 | -1.4 | 0.19 |
| mda Detune, at most | up |  | 252 | 12 | 1499 | 0% | 0/8 | 1500 | -0.0 | -0.1 | -24.2 | -11.7 | -1.4 | 0.03 |
| mda SubSynth, divide | down |  | 0 | -7 | 1 | 79% | 8/8 | 2 | -29.6 | -4.7 | -0.0 | -6.9 | +16.8 | 0.02 |
| mda Tracker | both | passes the input through unchanged | | | | | | | | | | | | |
| swh amPitchshift | up | 256 | 253 | 9 | 30 | 63% | 7/8 | 5 | -0.3 | -0.4 | -53.7 | -37.6 | -1.4 | 0.07 |
| swh amPitchshift | down | 256 | 419 | 11 | 215 | 26% | 3/8 | 1493 | -0.1 | -0.9 | -22.1 | -8.4 | -1.5 | 0.04 |
| swh pitchScaleHQ | both | cannot be loaded (above) | | | | | | | | | | | | |
| swh divider | down |  | 10 | 1 | 1201 | 0% | 0/8 | 1201 | -4.5 | -11.0 | -31.1 | -17.0 | -2.4 | 0.01 |
| MOD SuperCapo, fidelity 0 | up |  | 452 | 4 | 1200 | 29% | 2/8 | 1200 | -16.9 | -7.2 | -4.8 | -24.6 | -12.2 | 0.67 |
| MOD SuperCapo, fidelity 1 | up |  | 996 | 10 | 1 | 71% | 7/8 | 1 | -24.1 | -5.8 | -22.8 | -27.4 | -14.2 | 1.41 |
| MOD SuperCapo, fidelity 2 | up |  | 1569 | 15 | 0 | 71% | 8/8 | 0 | -26.6 | -8.3 | -37.8 | -45.4 | -15.0 | 2.35 |
| MOD Drop, fidelity 0 | down |  | 396 | 7 | 1 | 97% | 7/8 | 1 | -15.4 | -4.3 | -3.4 | -8.6 | -2.9 | 0.37 |
| MOD Drop, fidelity 1 | down |  | 652 | 14 | 1 | 100% | 8/8 | 0 | -26.7 | -6.5 | -2.8 | -5.0 | -2.7 | 0.67 |
| MOD Drop, fidelity 2 | down |  | 1415 | 28 | 0 | 100% | 8/8 | 0 | -30.3 | -16.5 | -2.9 | -4.6 | -2.5 | 1.44 |
| MOD SuperWhammy at +12 | up |  | 811 | 9 | 1 | 71% | 8/8 | 0 | -23.5 | -7.2 | -22.9 | -31.0 | -8.2 | 1.12 |
| MOD SuperWhammy at -12 | down |  | 1021 | 22 | 0 | 100% | 8/8 | 0 | -28.3 | -11.1 | -4.3 | -4.4 | -2.6 | 1.07 |

\* Ubuntu's 3.3.0 library under the URIs it really exports (`build_extras.sh`
makes the bundle): what R3 would be if the package loaded. Every candidate
is mono in and out except MaPitchshift (one in, two out) and mda's (two and
two); none has atom ports, CV ports or a required feature Sushi lacks, and
all are knob-shaped. The built ones are mono.

What each one is, in practice:

- **gx_detune_ (today's).** High quality is good on single notes and wide
  on chords (-9.8 dB of junk on the E major: the spectrogram shows the lines
  smeared into bands); its "realtime" setting does not shift an octave up at
  all (the output stays at the note's pitch) and goes to pieces down. Its
  octave arrives about 65 ms after the dry note at electric-octave-up's own
  settings (3,142 samples wet only; the 1 kHz octave of a 500 Hz burst is
  absent at 60 ms and present from about 70). It also NaNs
  (`tool/spikes/gx_detune_nan/`); valgrind here repeated the four invalid
  reads as a check on this bench.
- **gx_oc_2_** is an analogue-style divider, octave down only, no latency.
  It needs a strong fundamental: on E2, A2 and D3 it came out at the note's
  own pitch, where this signal's second harmonic beats the fundamental, as a
  bridge pickup's does; G3 is in tune most of the time and B3 up all of it. On chords it is noise
  (-2.2 dB of junk).
- **Rubber Band R2 (3.3, in the manifest)** tracks well but sits 8,192
  samples (170 ms) late, and smears both ways. **R3** is the cleanest on
  chords down (-30.6 dB) and equally late; under 3.3 it cannot load at all.
- **Rubber Band Live (4.0)**: 100 % in tune, every quick note, no error
  through the bend, the cleanest octave up of all on single notes and on
  chords (-40.1 and -36.6 dB, against today's -39.9 and -9.8: the spectrogram
  keeps each partial a thin line), almost nothing left of the original notes
  (-71 dB). Down it is clean on single notes, wider on chords (-14.1 dB).
  Ten renders each way were identical to the bit; 270 s each way, no NaN;
  valgrind clean. About 3.5 % of a core here, ten times gx_detune_.
- **MaPitchshift** is a delay-line shifter: in tune, but its crossfading
  windows put a flutter on everything (junk -5 dB at its default 100 ms
  window). Tried at 20, 40, 60, 100 and 200 ms: shorter is faster (211
  samples at 20 ms) and worse (junk -2.9 dB, 57 % in tune); none is clean.
- **mda Detune** is a chorus-like detune of at most three semitones; it
  cannot reach an octave.
- **mda SubSynth (divide)** makes a square wave an octave down (its output is
  all odd harmonics of the sub, at +17 dB); with its filter opened up it is
  in tune from D3 to E4 and loses the two lowest notes, as gx_oc_2_ does;
  noise on chords. A bass-synth effect, not an octave pedal.
- **swh amPitchshift and divider** are unusable: nearly all their output is
  off the harmonics (junk -0.3 and -4.5 dB), and the divider stays at the
  note's pitch.
- **MOD's shifters** (moddevices/mod-pitchshifter, built here; wet only, a
  Gain knob, a Fidelity switch). **Drop** at fidelity 1 is the best
  low-latency octave down: 652 samples, 100 % in tune, every quick note,
  single notes clean (-26.7 dB), but chords smeared (-6.5 dB). **SuperCapo**
  and **SuperWhammy** up leave E2 and A2 at their own pitch (a 1,024-sample
  window cannot resolve them) and come out 8 to 15 dB quieter at Gain 0.
  All allocate memory in their first `run()` (and on a Fidelity change), off
  the audio thread's rules once. Valgrind clean (Drop, SuperCapo).

**Recommendation.** Replace gx_detune_ on electric-octave-up's octave up
with **Rubber Band Live mono 4.0** (`...lv2-rubberband#livemono`,
`semitones` 0, `octaves` 1, `wetdry` 0 for the wet; it mixes in itself, as
gx_detune_'s WET and DRY did). It keeps today's feel (the octave about 67 ms
behind the dry note, as now), is cleaner, and does not NaN. The cost is the
build: Ubuntu 24.04 has Rubber Band 3.3.0; 4.0.0 is packaged from Ubuntu
26.04 (Launchpad's publishing history; 24.04 has only 3.3.0, checked with
apt here). Until then it is carried like NAM: built from
`breakfastquay/rubberband` v4.0.0 (`meson setup ... -Dlv2=enabled`, about
30 s here, needing libfftw3-dev and libsamplerate0-dev) in the CI image and
on the rig, its `rubberband.lv2` replacing the 3.3.0 bundle of the same name.
That also makes the manifest's `#r3mono` and `#r3stereo` loadable. Its
`plugin-ports.txt` line is `1 1 http://breakfastquay.com/rdf/lv2-rubberband#livemono`.
The rig's sound reference for electric-octave-up would change.

If the octave has to be quick, there is nothing good enough: MOD Drop at
652 samples is right for single notes an octave down and smears chords, and
nothing quick goes up cleanly.

Not tried: guitarix's own fix (the spike's padding) built from source;
Rubber Band Live's formant option; any shifter in Sushi itself or on the rig.

## #155: the pedals

`pedals.py` renders each pick on a track as wide as its outputs. Columns:
ports (in/out, and atom in/out); latency port; **onset** of a 500 Hz burst
(samples; a gate's is its attack); **lag** of best correlation with the dry
guitar, which for a filter is its phase, not a delay; CPU; level change;
**bright**, the share above 2 kHz against the input's (dB); **tail** past the
input's end (s, to -80 dBFS); **L/R** correlation of a stereo output;
**level wobble** and **tone wobble**, the strongest rate and swing of the
output's level and spectral centroid over a held 120 Hz note that does not
move by itself; **sweep**, how much brighter each note is just after the pick
than half a second on, against the input (above 1: it opens on the pick).

| kind | pick | in/out | latency port | onset | lag | CPU % | level dB | bright dB | tail s | L/R | level wobble | tone wobble | sweep |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| fuzz | GxFuzzFaceJH2 | 1/1 |  | 0 | 0 | 0.42 | +5.9 | +1.0 | 0.0 |  | 0.11 Hz 0.0 dB | 0.11 Hz 0% | 0.98 |
| fuzz | **GxBigMuffPi** | 1/1 |  | 6 | 39 | 0.70 | +7.1 | -14.5 | 2.0 |  | 0.11 Hz 0.0 dB | 6.68 Hz 0% | 0.71 |
| fuzz | GxFuzzMaster | 1/1 |  | 0 | 40 | 0.40 | +18.4 | +1.7 | 1.4 |  | 0.11 Hz 0.0 dB | 0.11 Hz 0% | 0.99 |
| fuzz | GxColorSound Tonebender | 1/1 |  | 0 | 0 | 0.29 | +15.3 | +3.6 | 0.5 |  | 0.11 Hz 0.0 dB | 0.11 Hz 0% | 1.31 |
| phaser | **GxPhaser** | 1/1 |  | 0 | 37 | 0.12 | -3.2 | +0.8 | 0.0 |  | 0.50 Hz 3.1 dB | 1.50 Hz 9% | 1.00 |
| phaser | Calf Phaser | 2/2 +atom 1/1 |  | 0 | 0 | 0.32 | +0.7 | +3.2 | 0.1 | 0.75 | 1.00 Hz 5.3 dB | 0.50 Hz 45% | 1.24 |
| phaser | fomp CS Phaser 1 LFO | 1/1 |  | 0 | 0 | 0.42 | -3.2 | +2.7 | 0.0 |  | 0.50 Hz 2.5 dB | 1.00 Hz 14% | 1.28 |
| phaser | Invada Phaser (mono in) | 1/2 |  | 0 | 0 | 0.20 | +1.6 | +0.3 | 0.0 | 0.60 | 1.00 Hz 10.1 dB | 1.00 Hz 44% | 0.93 |
| flanger | **GxFlanger** | 1/1 |  | 0 | 1 | 0.06 | -1.7 | -0.8 | 0.0 |  | 0.20 Hz 7.5 dB | 0.20 Hz 38% | 1.04 |
| flanger | Calf Flanger | 2/2 +atom 1/1 |  | 0 | 0 | 0.14 | +0.0 | +2.4 | 0.0 | 0.79 | 0.30 Hz 4.7 dB | 0.30 Hz 16% | 1.10 |
| flanger | mda ThruZero | 2/2 |  | 0 | 0 | 0.02 | -2.7 | -0.2 | 0.0 | 1.00 | 0.23 Hz 6.7 dB | 0.21 Hz 29% | 0.92 |
| flanger | LSP Flanger Mono | 1/1 +atom 1/1 |  | 0 | 0 | 0.10 | +6.3 | +0.1 | 0.0 |  | 0.30 Hz 1.7 dB | 0.30 Hz 11% | 1.01 |
| tremolo | **GxTremolo** | 1/1 |  | 0 | 0 | 0.33 | -4.0 | +0.3 | 0.0 |  | 5.00 Hz 11.3 dB | 5.00 Hz 0% | 1.01 |
| tremolo | GxTubeTremelo | 1/1 |  | 0 | 186 | 0.86 | +5.8 | +0.7 | 0.0 |  | 5.00 Hz 7.1 dB | 5.00 Hz 3% | 1.01 |
| tremolo | Calf Pulsator | 2/2 +atom 1/1 |  | 0 | 0 | 0.29 | -3.7 | -0.1 | 0.0 | 1.00 | 2.00 Hz 10.2 dB | 2.00 Hz 0% | 1.02 |
| vibrato | GxTubeVibrato | 1/1 |  | 226 | 399 | 0.62 | +7.4 | +0.7 | 0.0 |  | 10.00 Hz 2.6 dB | 10.00 Hz 2% | 1.01 |
| vibrato | Calf Rotary Speaker | 2/2 +atom 1/1 |  | 0 | 17 | 0.36 | +4.8 | -5.6 | 0.0 | 0.98 | 0.55 Hz 0.5 dB | 0.59 Hz 41% | 0.75 |
| vibrato | **mda Leslie** | 2/2 |  | 0 | 0 | 0.05 | +1.9 | +0.8 | 0.0 | 0.88 | 9.18 Hz 7.2 dB | 9.20 Hz 48% | 1.03 |
| wah | GxAutoWah | 1/1 |  | 38 | 62 | 0.14 | +9.1 | -30.5 | 0.1 |  | 2.96 Hz 0.0 dB | 9.34 Hz 0% | 0.58 |
| wah | GxWah, pedal half way | 1/1 |  | 7 | 46 | 0.04 | +6.6 | -23.6 | 0.0 |  | 0.11 Hz 0.0 dB | 0.00 Hz 0% | 0.61 |
| wah | GxWahwah, auto | 1/1 |  | 0 | 48 | 0.11 | +11.1 | -23.5 | 0.0 |  | 15.00 Hz 0.0 dB | 4.20 Hz 0% | 0.48 |
| wah | **fomp Auto-wah** | 1/1 |  | 0 |  | 0.07 | -2.6 | +14.5 | 0.0 |  | 10.00 Hz 0.0 dB | 10.00 Hz 0% | 2.47 |
| envelope | Calf Envelope Filter | 4/2 +atom 1/1 |  | 0 | 0 | 0.71 | -4.8 | +4.8 | 0.0 | 1.00 | 0.00 Hz 0.0 dB | 0.00 Hz 0% | 2.17 |
| envelope | mda RezFilter | 2/2 |  | 0 | 18 | 0.09 | +5.2 | -25.4 | 0.1 | 1.00 | 0.11 Hz 0.0 dB | 0.00 Hz 0% | 0.97 |
| reverb | **Gxshimmizita** | 2/2 |  | 0 | 1174 | 1.03 | +11.6 | -2.6 | 2.5 | 0.10 | 0.11 Hz 0.1 dB | 0.11 Hz 0% | 0.91 |
| reverb | Dragonfly Hall | 2/2 +atom 1/1 |  | 0 | 1 | 1.83 | -1.6 | -0.0 | 0.9 | 0.81 | 0.91 Hz 0.6 dB | 0.91 Hz 8% | 0.93 |
| reverb | Dragonfly Hall, long | 2/2 +atom 1/1 |  | 0 | 0 | 1.69 | +1.4 | -0.4 | 4.0 | 0.40 | 0.91 Hz 2.6 dB | 0.89 Hz 19% | 0.77 |
| reverb | Dragonfly Plate | 2/2 +atom 1/1 |  | 0 | 0 | 0.23 | -2.1 | +0.2 | 0.2 | 0.97 | 0.11 Hz 0.0 dB | 0.11 Hz 0% | 1.02 |
| reverb | GxZita_rev1-Stereo | 2/2 |  | 0 | 0 | 0.39 | -5.1 | -0.2 | 1.4 | 0.84 | 0.11 Hz 0.0 dB | 0.11 Hz 0% | 0.98 |
| reverb | **GxZita_rev1-Stereo, long** | 2/2 |  | 0 | 0 | 0.47 | -3.1 | +0.1 | 3.6 | 0.89 | 0.11 Hz 0.2 dB | 0.11 Hz 1% | 1.01 |
| reverb | Calf Reverb | 2/2 +atom 1/1 |  | 0 | 0 | 0.57 | +1.3 | -1.0 | 4.0 | 0.88 | 0.50 Hz 0.3 dB | 0.50 Hz 3% | 0.88 |
| delay | **GxEchoCat** | 1/1 |  | 0 | 38 | 2.27 | -2.4 | -0.2 | 0.8 |  | 0.11 Hz 0.0 dB | 0.11 Hz 0% | 0.76 |
| delay | GxTubeDelay | 1/1 |  | 0 | 86 | 0.79 | +12.1 | +0.3 | 1.1 |  | 0.11 Hz 0.0 dB | 0.11 Hz 0% | 0.77 |
| delay | Calf Vintage Delay | 2/2 +atom 1/1 |  | 0 | 0 | 0.15 | -0.0 | +0.3 | 1.1 | 0.99 | 0.11 Hz 0.0 dB | 0.11 Hz 3% | 0.96 |
| delay | swh Tape Delay | 1/1 |  | 0 | 0 | 0.08 | +1.1 | -0.1 | 1.0 |  | 0.11 Hz 0.0 dB | 0.11 Hz 0% | 1.00 |
| delay | Gxdigital_delay | 1/1 +atom 1/0 |  | 0 | 0 | 0.21 | +1.0 | +0.1 | 2.4 |  | 0.11 Hz 0.3 dB | 0.14 Hz 0% | 0.63 |
| gate | **abGate** | 1/1 |  | 144 | 0 | 0.01 | -0.0 | -0.0 | 0.0 |  | 0.00 Hz 0.0 dB | 0.11 Hz 0% | 1.00 |
| gate | Calf Gate | 2/2 +atom 1/1 |  | 0 | 0 | 0.17 | -6.9 | -1.4 | 0.0 | 1.00 | 0.00 Hz 0.0 dB | 0.11 Hz 0% | 1.00 |
| other | mda RingMod | 2/2 |  | 0 |  | 0.13 | -3.0 | +3.5 | 0.0 | 1.00 | 6.25 Hz 0.1 dB | 10.61 Hz 26% | 0.61 |
| harmoniser | Rubber Band Live 4.0, a fifth up (built) | 1/1 | 3078 | 2111 | 2142 | 3.28 | -3.1 | +1.4 | 0.1 |  | 0.41 Hz 2.2 dB | 0.41 Hz 14% | 1.18 |

The held note's own wobble is 0.0 dB and 0 %: what the table shows there,
the pedal did. A rate of 0.11 Hz with no swing means nothing moved. With
-66 dBFS of hiss added, the last two seconds after the notes came out at
digital silence through abGate and at -90 dBFS through Calf Gate.

Every pick loads in the bench host with Sushi's features, has no CV port, and
has all its parameters in control ports. `sushi-rig probe` would still call
some not drivable, because they implement `state:interface`: every LSP
plugin, Dragonfly Hall, Plate and Room, x42's fil4 and dpl, and B.Shapr. For
Dragonfly the state is one key, `preset` (a GUI preset name, from the
library's strings); what LSP keeps in it was not looked at. Calf's DSP library
links FluidSynth, PulseAudio, PipeWire, SDL2 and more into the process that
loads it, for any Calf plugin.

What the renders show, by kind:

- **Fuzz.** All four clip hard with no latency (the lag is the tone filter's
  phase). Big Muff is the dark, sustaining one (-14.5 dB of top at TONE 0.5,
  its tail held two seconds above -80 dBFS); Fuzz Face the mildest (+6 dB);
  FuzzMaster and Tonebender the loudest and brightest. The rig's TS9 is an
  overdrive, so a fuzz is a real gap.
- **Phaser.** All move at the rate asked. GxPhaser is the gentle one (3 dB),
  Invada's the deepest (10 dB, stereo from mono in), Calf's the widest in
  tone (45 %) but stereo, with atom ports and Calf's library.
- **Flanger.** GxFlanger sweeps deepest of the mono ones (7.5 dB, 38 %); mda
  ThruZero (already in the manifest) is close behind; LSP's is shallow at
  these settings and is a `state:interface` plugin.
- **Tremolo.** GxTremolo gives the cleanest, deepest amplitude modulation
  (11.3 dB at exactly 5 Hz, tone untouched); GxTubeTremelo adds tube drive
  (+5.8 dB); Calf Pulsator's rate control did not follow `hz` at timing 3
  (it ran at 2 Hz) and was not chased.
- **Vibrato.** mda Leslie (in the manifest) is a full rotary: level and tone
  move together at 9.2 Hz, with stereo spread. GxTubeVibrato is a pitch
  vibrato, 226 samples late (its delay line); Calf Rotary's default is the
  slow speed, 0.6 Hz.
- **Wah and envelope.** fomp Auto-wah is the one that does it at this level:
  the spectrogram shows a resonance jumping to about 3 kHz on each pick and
  falling as the note decays (sweep 2.47). Calf Envelope Filter does the
  same with its Activation at 16 (sweep 2.17; at its default of 1 it barely
  moves). The guitarix wahs sit dark (-23 to -30 dB of top) with little
  movement at a -10 dBFS input: GxWah wants an expression pedal on MIDI,
  GxAutoWah and GxWahwah more level.
- **Reverb.** GxZita_rev1 at RT60 6 s gives a 3.6 s tail at 0.47 % CPU
  without atom ports or state; Dragonfly Hall long gives 4 s and the widest
  image (L/R 0.40) at 1.7 %, and moves (its modulation). Gxshimmizita's
  SHIFT makes the tail climb continuously rather than stack octaves: a 440 Hz
  note's tail was 7 semitones up two seconds later at SHIFT 2, 14 at 4; at
  its default 0 it is a plain reverb.
- **Delay.** GxEchoCat repeats only with Swell up (its echo level; the
  first render, with it at 0, had no echo); swh Tape Delay and Gxdigital_delay
  give clean, timed repeats; GxEchoCat costs the most CPU of the delays
  (2.3 %).
- **Gate.** abGate silenced the hiss after the notes completely and opened
  on the pick within 144 samples (its 30 ms attack setting reaches -20 dB that
  soon); Calf Gate brought it to -90 dBFS and pulls the level down 6.9 dB
  overall at its defaults.
- **Harmoniser.** Rubber Band Live at a fifth works as a fixed-interval
  harmony, 2,111 samples late; nothing intelligent (key-aware) was tried.

## Reproducing

```bash
# the packaged plugins
sudo apt-get install guitarix-lv2 mda-lv2 dpf-plugins-lv2 swh-lv2 x42-plugins \
    rubberband-lv2 calf-plugins lsp-plugins-lv2 zam-plugins dragonfly-reverb-lv2 \
    invada-studio-plugins-lv2 fomp abgate liblilv-dev lv2-dev python3-numpy
# the built ones (MOD's shifters, Rubber Band 4.0, the R3 bundle)
tool/spikes/plugin_research/build_extras.sh /tmp/extras
python3 tool/spikes/plugin_research/octave.py --extra-lv2 /tmp/extras/lv2 \
    --rubberband4 /tmp/extras/rb4/lv2 [--wav DIR] [--only NAME] [--notes]
python3 tool/spikes/plugin_research/pedals.py --rubberband4 /tmp/extras/rb4/lv2 [--wav DIR]
python3.12 tool/spikes/plugin_research/inspect_plugins.py URI ...   # python3-lilv
```

`octave.py` takes about a minute, `pedals.py` about 45 s. `--wav` writes
every render as a float WAV, the input first, to listen to.

- `host.c`: the bench host. `bench.py`: building it, rendering, the guitar,
  and the measures shared by the two scripts.
- `octave.py`, `pedals.py`: the two surveys, settings beside each candidate.
- `inspect_plugins.py`: a plugin's ports, ranges, features and the two
  verdicts (Sushi loads it; knob-shaped).
- `build_extras.sh`: what no package provides.

Packages installed in this container for this work, beyond what it had
(guitarix-lv2, mda-lv2, dpf-plugins-lv2, x42-plugins, swh-lv2,
ardour-lv2-plugins, lilv-utils, liblilv-dev): rubberband-lv2,
zynaddsubfx-lv2, calf-plugins, lsp-plugins-lv2, zam-plugins,
dragonfly-reverb-lv2, invada-studio-plugins-lv2, fomp, blop-lv2, bshapr,
abgate, eq10q, python3-lilv, python3-numpy; to build, libarmadillo-dev,
libfftw3-dev, python3-mpmath, meson, libsamplerate0-dev; and
python3-matplotlib, to look at spectrograms, which are not kept here.

## Not done

- **Sushi was not run.** Its AppImage was not executed here, so no plugin
  above has been loaded by Sushi 1.3.0. Hosting is the source reading above
  plus the bench host; the first real test is `sushi-rig verify` with a
  plugin added, in CI's container or on the rig.
- **Nothing was heard**, and the guitar is synthetic: pitch trackers in
  particular (gx_oc_2_, mda SubSynth, MOD's) depend on how strong the
  fundamental is, which a real neck pickup would raise.
- **CPU** is this container's, not the rig's.
- Not rendered: gx_livelooper_ (its record and play are switches a render
  with fixed settings cannot press), MOD's Harmonizer and 2Voices
  (key-aware, needs MIDI or a scale), x42's fil4, B.Shapr, ZAM's,
  zynaddsubfx's effects. Settings were musical guesses, not tuned; a
  pedal that measured badly may be set badly.
