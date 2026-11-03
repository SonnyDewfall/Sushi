# gx_detune_ reads past two arrays and renders NaN: the reproduction

Written 2026-11-03 for the null-test flake (MANIFEST.md, the entry of that
date). This folder holds what is needed to report it upstream to
brummer10/guitarix; whether to file it is Sonny's call, and it has **not been
filed**. Everything below is against guitarix's V0.46.0 tag, which is what
Ubuntu 24.04's guitarix-lv2 0.46.0+dfsg-1 packages and the rig and CI run.

## What happens

`http://guitarix.sourceforge.net/plugins/gx_detune_#_detune_` with DETUNE at
+12 semitones (electric-octave-up's `octave_up`) renders NaN on some loads,
from a point part-way through to the end, and only a new instance clears it.
With the same input and settings, other loads are finite, and even those
differ from each other bit for bit.

## Why

In `src/LV2/gx_detune.lv2/detune.cc`, `smbPitchShift::mem_alloc()` allocates

```c++
fpb = new float[fftFrameSize2];
expect = new float[fftFrameSize2];
```

and `PitchShift()` reads both at index `fftFrameSize2`, one past the end, in
every hop: in the analysis loop (`for (k = 0; k <= fftFrameSize2; k++)`:
`tmp -= expect[k]`, `tmp = fpb[k] + tmp*freqPerBin2`) and in the synthesis loop
(`for (k = 0; k <= fftFrameSize2; k++)`:
`tmp = ((gSynFreq[k] - fpb[k]) * freqPerBin1) + expect[k]`).

Those four bytes past each array are whatever the heap left there. Logged at
allocation, they were usually 0, and sometimes half of a heap pointer or
something like malloc's own bookkeeping, which change from process to
process. The
synthesis value is added to `gSumPhase[fftFrameSize2]` every hop. When it is
NaN or Inf, or large enough that the running phase overflows float, the
Nyquist bin's phase is NaN; once that bin has any magnitude
(`gSynMagn[fftFrameSize2] != 0`), `cos`/`sin` of it put NaN into `fftw_in`,
the inverse FFT spreads it over the frame, and `gOutputAccum` keeps it. The
output is NaN from that hop to the end.

A shift of exactly 2 writes that bin (`index = k*pitchShift` reaches
`fftFrameSize2` at `k = fftFrameSize2/2`). A shift below 2, an octave down, or
no shift never does, and never shows the NaN; electric_board's two detunes run
at no shift.

Separately, both FFT plans are made with `FFTW_MEASURE`, which chooses an
algorithm by timing candidates. Two processes can choose differently and round
differently, so the output is not reproducible bit for bit even when nothing
is read out of bounds. That is most of the level spread: alone, the octave up
differs from itself by up to 0.064 dB in peak and the octave down by 0.32 dB
(CI, 40 renders each), which inside electric-octave-up's chain is within its
0.01 dB tolerance.

## Evidence

**valgrind 3.22**, one second of a sweep through the plugin alone (`host.c`):

```
Invalid read of size 4
   at 0x485CF05: ??? (in /usr/lib/lv2/gx_detune.lv2/gx_detune.so)
   by 0x485C709: ??? (in /usr/lib/lv2/gx_detune.lv2/gx_detune.so)
 Address 0x6912780 is 0 bytes after a block of size 1,024 alloc'd
   at 0x48485C3: operator new[](unsigned long)
   by 0x485B95F: ??? (in /usr/lib/lv2/gx_detune.lv2/gx_detune.so)
```

Four such reads, two in each of two 1,024-byte blocks: the first two `new[]`
in `mem_alloc`, `fpb` and `expect` (`fftFrameSize2` is 256 at the default
quality).

**The bench**, `repro.sh` on an x86-64 Ubuntu 24.04 machine, the plugin alone
an octave up, a 5 s sweep at 48 kHz in 64-frame blocks, summed over batches:

| | renders | NaN | distinct outputs |
|---|---|---|---|
| as it is | 210 | 2 | most of them |
| `fpb`, `expect` padded with 0 (`pad_fpb_expect.c`) | 360 | 0 | many |
| planned with `FFTW_ESTIMATE` (`fftw_estimate.c`) | 140 | 6 | many |
| padded with 0 and `FFTW_ESTIMATE` | 140 | 0 | **1** |
| padded with NaN | 3 | 3, from frame 2240 | |
| padded with 1e37 | 3 | 3, from frame 11200 | |
| padded with 1e36 | 3 | 3, from frame 92352 | |

1e35 gave no NaN within the five seconds: the larger the stray value, the
sooner the phase overflows.

**Under Sushi 1.3.0**, in the rig's CI (`tool/tests/test_render_spread.py`,
997dd73): the octave up alone was NaN in 1 of 40 renders, electric-octave-up's
v1 config in 1 of 40; every other plugin of its chain alone rendered the same
40 times out of 40, and the octave down never NaN. Every NaN recorded, there,
on the bench and in CI's history (frames 2240, 2496, 3520, 12736, 12992,
15296, 20416, 39104, 45248, 57536, 112832, 202688), started on a frame 192
past a multiple of 256: a hop boundary of the pitch shifter, whose hop is 64
samples at a quarter of 48 kHz and whose first FFT frame completes in the
32nd 64-frame block.

## Running it

Needs gcc, `liblilv-dev`, `lv2-dev`, python3 with numpy, and guitarix-lv2.

```
./repro.sh 100
```

It builds the host and the two controls in a temporary directory, writes the
sweep, renders the plugin `RENDERS` times as it is, with each cause taken
away and with both, then three times each with a known value past the end,
and prints one line per variant: renders, NaN renders, distinct outputs, and
the frames the NaN started at. It removes everything it made.

- `host.c`: a minimal LV2 host for one plugin, run as Sushi's offline
  frontend runs one: 64-frame blocks, flush-to-zero on, urid:map, the worker
  run at once, min and max block length 64. Ports by symbol or name, in their
  own units, or normalised as Sushi does with `NORM=1`.
- `pad_fpb_expect.c`: `LD_PRELOAD`; every `new[]` of exactly 1,024 bytes gets
  64 bytes past its end holding `GX_PAST_END` (0 if unset).
- `fftw_estimate.c`: `LD_PRELOAD`; every `fftwf_plan_dft_1d` is planned with
  `FFTW_ESTIMATE`.

## Suggested fix upstream

```c++
fpb = new float[fftFrameSize2 + 1];
expect = new float[fftFrameSize2 + 1];
```

and fill index `fftFrameSize2` in the loops that fill them
(`k <= fftFrameSize2`). For output that is the same from one load to the next,
plan with `FFTW_ESTIMATE`, or import wisdom, rather than `FFTW_MEASURE`.

Three smaller faults valgrind reports in the same file:

- `latency` is used uninitialised by `mem_alloc()` on the first `activate`
  (`switch(int(latency))`); it is only ever read from its port in
  `PitchShift()`. ("Conditional jump or move depends on uninitialised
  value(s)", twice, from activate.)
- `PitchShift()` computes `fSlow0 = 0.01 * wet` and `fSlow1 = 0.01 * dry`
  before reading `wet` and `dry` from their ports, so the first block uses
  uninitialised values and every later block the previous block's. (The host
  writes uninitialised bytes of the output.)
- `mem_free()` releases the `new[]` arrays with `delete`, not `delete[]`
  ("Mismatched free() / delete / delete []", eight times).

## What the rig does about it

The rig cannot fix the plugin, and the one setting that keeps the Nyquist bin
unwritten, DETUNE a tenth of a cent under the octave, moves electric-octave-up
by 0.15 dB in level and 0.47 dB in peak (CI, 40 of 40 renders outside its
reference), so no config is changed. The null tests render such a NaN once
more and say "retried after a NaN" every time (`render.measure`,
`render.detune_nan`). On the rig, an octave-up that goes silent mid-set needs
`sushi-rig restart` (README).
