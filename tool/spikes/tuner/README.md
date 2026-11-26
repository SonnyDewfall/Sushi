# A tuner inside the rig (#58): what Sushi 1.3.0 can host, and reading it

Whether the tuner can move from `fmit`, an external process, into Sushi as an
LV2 plugin on the guitar channel; whether the page can read what it detects,
how fast, and what it costs. Run 2026-11-26 in a container, not on the rig:
CI's rig image (`ci/Dockerfile`, built here with the real Sushi 1.3.0 release
AppImage, sha256 `04f0d3b0…50f3`) plus a JACK server (`Dockerfile` here), on
a 4-core Xeon at 2.1 GHz. Sushi ran on JACK's dummy driver, which keeps real
time with no hardware, at 48 kHz and a 256 period (the rig's quantum), and
once at 128.

## What it found

### 1. Candidates: two tuners in the rig's packages, one Sushi can host

None of these is in `plugin-manifest.txt` or `plugins/`, so none is in the
rig today; all four come with packages the rig already installs
(`/usr/lib/lv2`). `sushi-rig probe` lists only input ports, so the outputs
below are lilv's (`lv2info`).

| Plugin | Package | Outputs (control) | `probe` | Under Sushi 1.3.0 |
|---|---|---|---|---|
| `gareus.org/oss/lv2/tuna#one` (x42 Instrument Tuner) | x42-plugins 20230915 | Signal Level (RMS), Detected Frequency 0–10000, Detected Octave, Detected Note 0–12, Cent ±50, Note Stability, StrobeToUi | NOT fully drivable: 2 atom ports | **Sushi aborts** as soon as it hears a note (below) |
| `…/tuna#two` (the same with a spectrum) | x42-plugins | the same | the same | not run; same ports, same SysEx port |
| `guitarix.sourceforge.net/plugins/gxtuner#tuner` (GxTuner) | guitarix-lv2 0.46.0 | FREQ 0–1000 Hz, MAXL | NOT fully drivable: 1 atom port | **works**: everything measured below |
| `gareus.org/oss/lv2/fat1` (x42-Autotune) | x42-plugins | Pitch Error ±1, Pitch Bend, Note Mask, Note Set, latency | NOT fully drivable | not run: a pitch corrector that reports latency, not a tuner |

`probe`'s "NOT fully drivable" is its rule that an atom port may carry state
(`probe.is_config_drivable`). For these two it does not: gxtuner's is a MIDI
out (notes only while its `MIDI_ON` is 1; default 0), tuna's a SysEx and GUI
out, and neither has `patch:writable` or `state:interface`. gxtuner is mono
(1 in, 1 out), so the compiler would put its copy to stereo after it
(`stereo.py`; it would need its line in `plugin-ports.txt`). Its bundle links
libfftw3f, libzita-resampler and libsigc-2.0, and it imports
`pthread_create` and semaphores: its pitch tracking runs on a thread of its
own, in Sushi's process.

**Why tuna kills Sushi.** Sushi passes each MIDI event a plugin writes
through `midi::to_midi_data_byte(data, size)` (`midi_decoder.h`), which copies
into a 4-byte `MidiDataByte` on the stack with the loop bound
`std::max(size, 4)`: a longer event overruns it. tuna writes MIDI Tuning
Standard SysEx when it detects a note. Measured: on JACK with a 110 Hz sine,
Sushi exited with SIGABRT, "\*\*\* stack smashing detected \*\*\*", before
the first gRPC call answered (`results/tuna.json`); on the dummy frontend
under gdb it aborted the same way, and the smashed frame held
`f0 7f 7f 08 02 00 01 1c 1b 80 01 f7` with a size of 12 beside it: a
real-time single-note tuning change, 12 bytes into 4. The code is the same
in Sushi 1.4.0 (read, not run). So no plugin that writes MIDI events longer
than 4 bytes can be hosted, and none of tuna's controls (read in its TTL)
turns its SysEx off.

### 2. The reading can be read: Sushi lists output ports as parameters

Read in 1.3.0's source, then measured. `LV2_Wrapper::_register_parameters`
registers every control port, output ones as `Direction::OUTPUT`, so gRPC
lists them with `automatable` false (gxtuner: `FREQ` id 0, `MAXL` id 5).
`parameter_value` and `parameter_value_in_domain` read the port's current
value straight from the plugin's buffer, so `get_parameter_value_in_domain`
gives hertz. Sushi also emits a parameter change for output ports from the
audio thread (`_deliver_outputs_from_plugin`, at `CONTROL_OUTPUT_REFRESH_RATE`
30 Hz a port; read, not measured: the counter is shared by all of a
plugin's output ports and fires every 25th call, so with 5 or 25 output ports
one port would get every event), and the dispatcher turns those into gRPC parameter
notifications, at most one a parameter every 40 ms
(`MAX_PARAMETER_UPDATE_INTERVAL`) and only when the value changed. Both paths
are the same in 1.4.0's source.

Measured with gxtuner, cents worked out from `FREQ` against A440 (steady
sines at −12 dBFS peak, read at 20 Hz for 2 s each, in three runs:
`results/gxtuner-256-a`, `-b` and `-128`):

| Played | Read | Cents read |
|---|---|---|
| 110 Hz (A2) | 110.000 | 0.00 |
| 110 × 2^(5/1200) = 110.318 | 110.318 | +4.99 to +5.01 |
| 110 × 2^(−20/1200) = 108.737 | 108.737 | −20.00 (run a had one read at −5.21) |
| 82.407 (E2, the low string) | 82.407 | 0.00 |
| E2 +12 cents | 82.980 | +12.00 |
| 329.63 (E4) | 329.626–329.630 | −0.01 to +0.02 |
| silence | 0.0 | (no signal) |

On a sine this is far finer than a tuner needs: the spread over thousands of
reads at +5 cents was 0.003 cents. A real string is not a sine (below).

### 3. How fast: about 11 readings a second, 85–175 ms behind

- **The plugin's own rate.** The value read changes 11.2–11.3 times a second
  however fast it is polled (100 Hz or 4000 Hz). Reading faster than about
  12 Hz buys nothing.
- **Latency**, from the JACK period that took a step between 110 Hz and +5
  cents to the first reading within 1 cent that stays there, polled at 100 Hz
  on a held connection: in the two runs that recorded each step (120 steps),
  every one between 83 and 175 ms, median 126–131 ms, as a saw-tooth: a fixed
  ~85 ms of analysis plus up to one ~89 ms update. Through notifications the
  same steps took 96–201 ms, median 139–145 ms. Of four earlier runs (40
  steps each, not recording each step), three had a slowest step of 0.5–1.3
  s (510, 636 and 1259 ms, the last polled flat out with 66 xruns); how many
  such steps, and why, was not found.
- **Read rate and its cost** on one held connection (`results/gxtuner-256-b.json`;
  the probe's own idle is 1.9% of a core, mostly its sine generator, and
  Sushi's 4.3%):

  | Reads/s | A read (median, p99) | This process | Sushi |
  |---|---|---|---|
  | 10 | 0.83, 1.38 ms | 2.3% | 4.6% |
  | 20 | 0.67, 1.03 ms | 2.7% | 4.8% |
  | 30 | 0.59, 0.86 ms | 3.2% | 5.0% |
  | 100 | 0.50, 0.85 ms | 5.0% | 6.3% |
  | ~4000 (flat out) | 0.23, 0.44 ms | 70.8% | 47.5% |

  On a fresh connection a read, as the agent's `Live` does per request, a
  read took 3.4–4.3 ms and cost this process 6.5% of a core at 10 Hz and
  13.6% at 30 Hz.
- **Steadiness.** In the rates phase (runs a and b), off-pitch reads (over 1
  cent from +5) appeared only in measurements that also counted xruns, and
  not in every one of those; none in a measurement without one. This
  container's JACK runs without realtime scheduling.

### 4. What it costs: under half a percent of a chunk, and about nothing bypassed

Sushi's own timings (`--timing-statistics`, averages over 10 s; elkpy
returns them as (average, max, min) whatever its docstrings say), in
microseconds per 64-frame chunk, whose budget is 1333 us, with the rig's
compressor after the tuner for scale:

| | Tuner | Compressor | Track | Sushi's CPU |
|---|---|---|---|---|
| no tuner (`results/none.json`, 3 × 10 s) | — | 3.5–3.7 | 3.7–3.9 | 3.6–3.9% |
| tuner running (3 runs) | 4.3–5.0 (0.32–0.37%) | 3.1–4.9 | 7.6–9.9 | 4.0–4.4% |
| tuner bypassed (3 runs) | 0.19–0.27 (0.02%) | 3.3–4.1 | 3.7–4.6 | 3.5–3.7% |

So bypassed, the tuner's share of the audio thread is Sushi's bypass copy,
about 0.2 us a chunk, and Sushi's CPU, its tracking thread included, is at
its level with no tuner within the noise of these runs. Not zero, but
nothing to choose by. The maxima, in `results/`, are not quoted: in a
container without realtime scheduling they say more about the host than
about the plugin. The level out equalled the level in (−15.05 dBFS RMS)
with the tuner running and bypassed: it passes the guitar unchanged.

`sushi-rig top` could not be run here: it reads `pw-top`, and there is no
PipeWire in the container.

**Bypassed means stale.** A bypassed gxtuner holds its last reading: moved
from 110 Hz to +5 cents while bypassed, it still read 110.000. Taken out of
bypass it read the stale value, then a wrong one (−15 to +35 cents), and was
right after 179–191 ms in three runs. Whatever shows the reading has to know
the tuner is engaged, and throw away the first quarter second after engaging.

## The recommendation: the plugin, bypassed except while tuning

Against the issue's goals (no external process, headless, no patchbay entry)
and the cost measured above:

- **A tuner plugin on the guitar channel, bypassed except while tuning:
  recommended, with gxtuner.** No process, no patchbay entry, no new node or
  link in the graph; the reading comes over the gRPC connection the agent
  already makes, and the tuner is part of the compiled config, so `verify`
  covers it. Bypassed it costs about nothing, so on these numbers there is
  no reason to leave it out of any rig with a guitar channel. It is the compiler's to add (first in the
  channel's chain, with its copy to stereo), not the player's.
- **A dedicated tuning state: not needed.** It would cost a rig switch to
  spend what a bypassed plugin already does not spend, and take the player's
  chain away while tuning.
- **A detector outside Sushi, on a tap: the fallback.** The recorder reads
  samples only during a take (it opens no JACK client until RECORD), and the
  visualiser's tap carries only the master, so either would need a new
  client and link while tuning, made by code; and a pitch detector would be
  ours to write and test. Its one real advantage: it could tap the dry
  string. A plugin on the channel hears what Sushi hears, which with the amp
  on is the amp's output (`amp.amp_patchbay` puts it in front of the guitar
  input; `fmit`'s tap was left on the dry port on purpose). Whether gxtuner
  tracks a string through the rig's amp models is the first thing to measure
  on the rig; if it does not, this is the way.

**What the build needs, from what was measured:**

- gxtuner's URI in `plugin-manifest.txt` and its ports in
  `plugin-ports.txt`; `probe` or its profile has to accept its MIDI-out atom
  port; `MIDI_ON` pinned at 0.
- The agent engages the tuner (`set_processor_bypass_state`) while a page is
  tuning and bypasses it again after: on a lease, like the record meters
  (`METER_LEASE_S`), so a page that goes away leaves it bypassed.
- The reading on one connection held while tuning, at about 10 Hz (the
  plugin gives 11), not a connection a read; the notification stream gives
  the same readings a little later and is an equal choice.
- The page reads it as it reads the record meters (#254): its own route,
  asked ten times a second only while the tuner is shown, never through
  `/live` or the 1 s poll. The answer says whether it is engaged and whether
  the reading is fresh (the first ~250 ms after engaging is not).

**What the page shows**, in its style. A **Tune** toggle in the stage's
header (title: "Engage the guitar channel's tuner; the chain keeps
playing"). While on, the note in one large word (**A2**), cents as a signed
word (**+5c**) and a bar from −50 to +50 with the centre marked, green within
±2 cents and amber outside; the title carries the hertz, the reference
(A440) and "from the guitar channel, after the amp" when the amp is on.
**No signal** when the frequency reads 0, **Settling** for the first quarter
second. Off when the toggle is turned off, when the page leaves the stage,
or when the lease runs out. Whether to mute the channel while tuning is
Sonny's to decide; the plugin passes the guitar unchanged either way.

## What was not measured here

- The rig: PipeWire's JACK, realtime scheduling, the interface. The xruns
  here are this container's; the rig's own `sushi-rig top` was not run.
- A real string: harmonics, a pluck's decay, the low E's weak fundamental,
  noise; and the amp in front of the channel (the image has no NAM).
- Sushi on JACK here needed a stand-in for ALSA's sequencer, preloaded into
  Sushi alone (`alsa_seq_standin.c`): its standalone factory opens the
  sequencer for MIDI and fails without `/dev/snd/seq`, which a kernel with no
  ALSA does not have. MIDI plays no part in what was measured; on the rig the
  real sequencer is there.
- The agent and the page: the probe reads through the same `connect_quietly`
  and elkpy the agent uses, in a process of its own.
- tuna#two and fat1 were not run; the cause of the three slow steps (510,
  636 and 1259 ms) was not found.

## Running it

```bash
# CI's rig image, built as rig-container.yml builds it (ci/Dockerfile), then this one
docker build -t sushi-rig-ci-jackd tool/spikes/tuner
docker run --rm -v "$PWD/tool/spikes/tuner:/spike:ro" -w /rig sushi-rig-ci-jackd bash -c '
  gcc -shared -fPIC -O2 -o /tmp/seq.so /spike/alsa_seq_standin.c &&
  python3 /spike/probe.py --preload /tmp/seq.so --plugin gxtuner'
# the no-tuner baseline, and tuna's abort
#   ... python3 /spike/probe.py --preload /tmp/seq.so --plugin none
#   ... python3 /spike/probe.py --preload /tmp/seq.so --plugin tuna --phases ports,reading
```

On a machine with `/dev/snd/seq` and jackd, drop `--preload`. `--period`
sets the JACK period, `--steps`, `--hold` and `--latency-poll-hz` the latency
phase, `--rates` the rates phase, `--phases` which run, `--json` writes the
report, `--keep` keeps Sushi's and jackd's logs. A full run takes about four
minutes and prints one JSON report; `results/` holds the runs quoted here
(`gxtuner-256-a`, `-b`, `-maxpoll`, `-128`, `-lat-c`, `-lat-d`, `none`,
`tuna`). Run a was made before the probe read elkpy's timings in their real
order, so its `dsp` maxima are its minima; its averages, and the rest of it,
are as measured.
Every process it starts (jackd, Sushi, its JACK client) it stops by its own
handle. pytest never collects this folder (`testpaths = ["tests"]`).

Building CI's image here needed the session proxy's CA in each stage (git and
pip go through it); `ci/Dockerfile` itself was not changed.
