# The master limiter: mda/Limiter cannot be the safety limiter (#137)

Written 2026-11-16 for #137's master limiter, which is **not built**: the one
limiter in `plugin-manifest.txt` does not hold a ceiling, and the one on the
machine that does would change every sound reference. Which to do is
Sonny's call. Nothing here was heard on the rig, and nothing ran in Sushi:
the renders are lilv's `lv2apply` (lilv-utils 0.24.22) over Ubuntu 24.04's
mda-lv2 1.2.10, the package CI's image installs.

```bash
python3 tool/spikes/master_limiter/render.py   # numpy, lv2apply and mda-lv2
```

## mda/Limiter

Its own Turtle says it: `rdfs:comment "Threshold (this is not a brickwall
limiter so this is only approximate)"`. Its controls are Thresh, Output,
Release, Attack and Knee (hard or soft), each 0 to 1; it has no latency port.

What it does, from `src/mdaLimiter.cpp` (drobilla's mda-lv2, `main`, read
2026-11-16), hard knee, once a sample:

```c++
lev = (float)(0.5 * g * fabs(ol + or_));       // the level it watches
if (lev > th) g = g - (at * (lev - th));       // over: pull the gain down by a step
else          g = g + (float)(re * (1.0 - g)); // under: let it back toward 1
out = in * trim * g;
```

with `th = 10^(2*Thresh - 2)` (Thresh 0.975 is -1 dBFS), `trim =
10^(2*Output - 1)` (Output 0.5 is exactly 1), `at = 10^(-2*Attack)` and
`gain = 1.0` at instantiation. So:

- **No latency.** Nothing is delayed: the gain is worked out from the sample
  it is applied to.
- **Transparent below its threshold, bit for bit.** While `lev` never passes
  `th`, `g` stays exactly 1.0 (1 + re*(1 - 1) is 1), and `trim` is exactly 1,
  so the output is the input. Rendered: a sweep at -20 dBFS (nulltest's
  stimulus level) and a sine at -1.5 dBFS come out identical at every attack.
  The loudest sound reference in `config/sound/` peaks at -1.69 dBFS
  (`hard-feelings-record.vocal.yaml`), and `lev` is at most the louder
  channel, so **at a -1 dBFS ceiling no reference would change**. (Once it
  has limited, `g` creeps back toward 1 without always reaching it; a
  render of a reference starts from a new instance, so this does not bear
  on them.)
- **No ceiling.** It watches the sum of the channels, not either one: a
  signal on the two sides in opposite phase passes untouched (+6 dBFS in,
  +6.02 out). And the gain moves by a step proportional to the overshoot, with
  nothing to stop it passing zero: on a loud enough input the step is bigger
  than the gain, `g` swings negative and grows, and **the output is louder
  than the input**. Rendered, peak and peak after the first 50 ms, the worst
  of a 440 Hz sine, a 20 Hz to 20 kHz sweep and noise, both channels alike:

  | Attack | +3 dBFS in | +12 | +20 | +26 | +34 | +40 |
  |---|---|---|---|---|---|---|
  | 0 (fastest) | -1.0 / -1.0 | -1.0 / -1.0 | 12.5 / 12.5 | 21.9 / 21.9 | 36.0 / 26.8, louder | 51.7 / 44.0, louder |
  | 0.15 (its default) | -0.1 / -0.1 | 0.1 / -1.0 | 2.6 / 2.6 | 12.6 / 12.6 | 28.4 / 28.4 | 43.8 / 32.7, louder |
  | 0.5 | 1.8 / 1.8 | 5.9 / 2.4 | 6.6 / 0.4 | 8.0 / -0.9 | 8.8 / 8.8 | 20.2 / 16.7 |
  | 1 (slowest) | 2.8 / 2.8 | 9.0 / 6.2 | 14.4 / 6.6 | 17.8 / 6.5 | 20.9 / 5.4 | 23.6 / 3.0 |

  No setting holds -1 dBFS past +12 dBFS of input, and the fast ones fail
  worst exactly where a limiter is for: the old 500x gain bug was +54 dB.
  (`render.py` prints this table; the sine alone looks better than this, the
  sweep and the noise are what break it.)

An inf reaching it would also leave `g` NaN for good (inf - inf), and the
master silent until the rig restarts.

## The alternatives on the machine, none in the manifest

- **x42's dpl (`http://gareus.org/oss/lv2/dpl#stereo`)**, Digital Peak
  Limiter, from x42-plugins, which CI's image already installs (for darc). A
  look-ahead peak limiter, per channel, with a threshold in dB (default -1)
  and an optional true-peak mode: it holds a ceiling. Its look-ahead is its
  latency: `Peaklim::init` (`src/peaklim.cc`, x42/dpl.lv2 master) sets
  `_delay = ceil(1.2e-3 * fsamp / 8) * 8`, **64 samples at 48 kHz, 1.33 ms**.
  Every sound reference records `delay_samples: 0` with a tolerance of 0, so
  **every reference would change**, and the rig's monitoring would be 1.33 ms
  later. It also has atom ports (`control`, `notify`), which lv2apply cannot
  run, so it was not rendered here, and nothing has shown Sushi 1.3.0 hosting
  it. Adding it is a line in each of `plugin-manifest.txt` and
  `plugin-ports.txt` and a `plugins assemble` on the rig.
- **swh's Hard Limiter (`hard_limiter-swh`)**, a clipper, no latency, is
  mono: on the stereo master Sushi would run it on the left channel alone and
  clear the right. The other swh limiters look ahead, as dpl does.
- **Sushi's own `sushi.brickworks.clip`** is oversampled twice
  (`src/plugins/brickworks/clip_plugin.cpp`), so it filters and delays what
  it passes.

A ceiling with no look-ahead that changes nothing below it is a clipper, and
there is no stereo one here.

## Where it would sit, and the master volume

Sushi applies a track's gain after its plugins (`Track::process_audio`:
`_process_plugins`, then `_apply_pan_and_gain`). A limiter last on the master's
chain is therefore ahead of the master volume, which the page sets up to
+6 dB (`rig.js` `VOLUME_MAX_DB`): at +6 the ceiling would be +5 dBFS. Either
the master volume stops at 0 dB while a limiter is there, or the limiter's
threshold follows the volume, or it goes on a track after the master. Also
Sonny's call.

Not checked here: whether PipeWire's conversion of the master's floats to the
interface's integers clips at full scale. If it does, nothing past 0 dBFS
reaches the speakers today, and what a limiter adds is a level held under
full scale rather than clipped at it.
