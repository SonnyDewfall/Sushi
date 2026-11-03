# The Ember Nanocore: measured round-trip latency, and why no setting fixes it

Rig laptop `sonny-dewfall-XPS-9320`, 2026-11-03. Sonny connected an Ember Nanocore as a possible
replacement for the Audient iD14, having tried it on the rig before and found "quite bad latency".
A loopback cable was fitted to it, which made a real measurement possible.

**Headline: the Nanocore has a round-trip latency floor of about 57 ms that no buffer setting
reduces.** That is five to ten times a usable figure for playing through amp models. The earlier
impression was right, and it is not a configuration problem.

The measurement was validated against the one assumption that could have faked it — see "The
instrument was checked before it was trusted". A loopback on the iD14 would allow a direct
comparison rather than an inferred one, but no conclusion here depends on it.

## What the device is

USB `33c3:1301`, "Ember Nanocore", card 4.

| property | value | consequence |
|---|---|---|
| USB speed | **full speed** (12 Mbit/s, USB 1.1) | 1 ms isochronous frames; not itself a latency problem |
| Class | **UAC1** | older control model than the iD14's UAC2 |
| Sample rates | **44100 only** | the rig runs at 48000, so a resampler is forced into the path |
| Format | S16_LE, 16-bit | the iD14 gives 24-bit in S32_LE |
| Playback | 2 ch, endpoint 0x02, **ADAPTIVE** | |
| Capture | 2 ch declared, endpoint 0x81, ASYNC | but see below — the input is really **mono** |
| MIDI | 2 in jacks, 2 out jacks, bulk endpoints | unused here |

It also has two working ALSA mixer controls, which is more than the iD14 ever gave us:

- `PCM`, `pvolume pswitch`, range 0–37, found at 14 = **−23.00 dB**
- `Mic`, `cvolume cswitch`, range 0–37, found at 37 = **0.00 dB** (maximum)

`PCM` reports real dB values and is a playback attenuator, so **this interface has the hardware
volume control the iD14 lacks**. That is the one clear advantage it has, and it does not
compensate for the latency.

## Why it felt bad before anything was measured

Two things were stacked against it, both configuration:

1. **The graph ran at 48000 while the device only does 44100**, so every stream through it was
   resampled. `clock.allowed-rates` was `[ 48000 ]`, so PipeWire could never switch to match.
2. **The quantum was the 1024 default** — 21.3 ms — because nothing forced it down. The prior
   capture work on this machine (`capture-128-idle.json`) had already shown the laptop sustaining
   **128 frames at 48000 for 300 s with zero xruns**, callback p99 709 µs against a 2667 µs
   budget. So the machine was never the limit.

Both were fixable at runtime, with no config file written:

```
pw-metadata -n settings 0 clock.allowed-rates '[ 44100, 48000 ]'
pw-metadata -n settings 0 clock.force-quantum 256
```

The first does work — the graph switched itself to 44100 and granted a 256 quantum. **But fixing
both does not rescue the device**, because the floor measured below sits underneath all of it.
Both settings have since been restored to what they were (`[ 48000 ]`, force-quantum 0).

## The device's ALSA floor is low — the hardware is not the bottleneck

```
PERIOD_SIZE: [45 44100]      PERIODS: [2 1024]      BUFFER_SIZE: [90 88200]
```

A minimum 90-frame buffer is **2.04 ms**. The device happily accepted a 128-frame buffer with zero
xruns. So nothing about its declared capability predicts bad latency. That had to be measured.

## How the round trip was measured

`rtlat.c` and `rtlat_analyse.py` alongside this file.

Playback and capture are opened on the same card and **`snd_pcm_link`'d**, so one start command
runs both off the same clock: output frame N and input frame N are simultaneous. A chirp
(400 Hz → 6 kHz, 2048 samples, Hann-windowed) is emitted at a known absolute output frame; the
capture is dumped raw and correlated against the same chirp offline. The round trip is the
correlation peak minus the emit frame.

Two deliberate choices, both of which were learned the hard way:

- **One duplex process, not `aplay` plus `arecord`.** Separate processes have no common time
  reference, so their start skew swamps the thing being measured.
- **Correlation, not a threshold.** See the dead end below.

Because the arithmetic is in *stream-position* terms, this measures the device, USB transport and
converters, and deliberately **excludes application buffering**. So the figure is a floor, and
PipeWire's quantum lands on top of it.

### The result

At a 384-frame (8.71 ms) buffer, three consecutive runs:

| run | round trip | correlation peak / background |
|---|---|---|
| 1 | 58.75 ms | 142 |
| 2 | 58.89 ms | 148 |
| 3 | 59.16 ms | 141 |

Spread 0.4 ms. Every run had **zero xruns**.

### Buffer size barely moves it

Period size varied, 3 periods throughout:

| period | buffer | round trip |
|---|---|---|
| 64 | 192 (4.35 ms) | 57.51 ms |
| 128 | 384 (8.71 ms) | 59.25 ms |
| 256 | 768 (17.41 ms) | 61.84 ms |
| 512 | 1536 (34.83 ms) | 63.06 ms |

Period count varied, period size fixed at 64:

| periods | buffer | round trip |
|---|---|---|
| 2 | 128 (2.90 ms) | 57.21 ms |
| 4 | 256 (5.80 ms) | 57.39 ms |
| 8 | 512 (11.61 ms) | 57.73 ms |

A **4× buffer increase moved the round trip by 0.52 ms**. An 8× increase in the first table moved
it 5.5 ms. The buffer is essentially not in this path: roughly **57 ms is fixed inside the
device**, and there is no setting on the host that reaches it.

### The instrument was checked before it was trusted

**Relative timing.** Two chirps were emitted a known 4410 frames (100 ms) apart in one run.
Measured separation: **4404 frames — an error of 6 frames, 0.14 ms.** So the frame arithmetic and
the correlation are sound to far finer than the effect being reported.

**The one assumption that could have faked the whole result.** The arithmetic requires that output
frame 0 and input frame 0 are the same instant, which is what `snd_pcm_link` is for. But link
reporting success only means the two substreams share a trigger call — an isochronous stream still
begins on a USB frame boundary, and nothing in the API guarantees both directions pick the same
one. If they did not, the skew would be a constant offset in every figure here.

So `rtlat.c` now reads each stream's `trigger_htstamp` and reports the difference. On the iD14,
same `snd-usb-audio` driver, 128-frame period:

```
linked=1
trigger_skew_us=0.6      trigger_skew_frames=0.0
delay_play_frames=333    delay_cap_frames=64    delay_sum_ms=8.27
```

**Skew is zero to sub-microsecond.** Output frame N and input frame N are simultaneous, so no
offset needs subtracting and the Nanocore's figure stands as measured.

Two further reasons to believe it, independent of that run:

- **A boundary mismatch could not be large enough anyway.** Full-speed USB frames are 1 ms, so the
  worst conceivable start disagreement on the Nanocore is ~1 ms — around 44 frames. Overturning a
  57 ms reading would need an offset of ~45 ms, which is two orders of magnitude more than the
  mechanism can produce.
- **The driver's own delay figures agree in shape.** On the iD14 the driver accounts for 8.27 ms of
  buffer and URB queue. That is the scale a healthy interface's host-side latency sits at, and it
  is nowhere near 57 ms — so on the Nanocore the remainder is inside the device, not in ALSA.

## A measurement that could not work

The first attempt detected the burst by thresholding absolute sample value, with the threshold set
to eight times a noise floor measured over the first eight periods. It produced three mutually
contradictory results, including **a round trip of −4.01 ms**, which is impossible. Two separate
faults:

- The floor was measured over a window where the capture stream was still delivering digital
  silence, so the floor read 0 and the threshold collapsed to 64 — tripped instantly by the first
  real noise, giving an arrival *before* the emission.
- In another run the floor read 611, putting the threshold at 4952 while the burst's own peak was
  only 3277. **The threshold sat above the signal**, so the burst could never be detected and the
  run reported "never arrived".

Correlation has no threshold to get wrong and tolerates this input's noise, which is substantial:
at `Mic` 0 dB the idle input measures **−56.5 dBFS RMS, −29.9 dBFS peak**.

## The input is mono, so this loopback cannot test stereo either

`amixer` reports `Mic` as `cvolume-joined`, `Capture channels: Mono`, and a 1 s idle capture
measured **L and R identical to 0.1 dB** — one mono input duplicated to both channels.

This is the same blind spot as the iD14's loopback, which was a mono jack in a stereo socket and
silently invalidated four readings there (see `RESULTS-id14-mixer-2026-11-03.md`). The cause is
different — the socket there, the converter here — but the consequence is identical: **a channel
comparison through this loop is meaningless.** It is valid for latency and level, which is all it
was used for above.

## What this means for the rig

- **Not usable for playing live.** A 57 ms floor is felt as a distinct slap behind the fingers.
  Comfortable is under 10 ms, tolerable under about 15.
- **The 44100-only limit is a second, independent problem.** The rig is specified at 48000
  (`IMPLEMENTATION_BRIEF.md`), so using the Nanocore forces a resampler permanently into the path
  — cost and quality, for no gain.
- **It does have the volume control the iD14 lacks** (`PCM`, −23 dB and real). If a hardware
  attenuator is wanted, that is worth remembering, but it does not offset the latency.
- **The iD14 remains the better interface for this rig.** Its open problems — headphone stereo and
  a device-side level control — are both unsolved, but neither is latency, and latency is the one
  that cannot be worked around.

## What is left to confirm

The start-skew check above was run on the iD14, because the Nanocore had been unplugged by then.
It establishes that `snd_pcm_link` synchronises streams on this driver, and the 1 ms frame-boundary
bound rules out a large offset on any full-speed device. Neither leaves room for the ~45 ms error
that would be needed to change the conclusion.

Still, one run would close it properly. **With the Nanocore plugged back in**, repeat it on the
device itself and check two numbers: `trigger_skew_frames` should be 0, and `delay_sum_ms` should
come out near the iD14's 8 ms. If it does, the remaining ~49 ms is unambiguously inside the device
and nothing is left to argue about. `rtlat.c` takes the rate, channel counts and format as
arguments, so one binary covers both interfaces:

```
gcc -O2 -o rtlat rtlat.c -lasound -lm
```

```
./rtlat hw:4,0 128 3 -12 /tmp/nc 44100 2 2 S16_LE > /tmp/nc.meta && python3 -I rtlat_analyse.py /tmp/nc "$(grep ^emit_at= /tmp/nc.meta | cut -d= -f2)" 44100
```

Note the card number: the Nanocore came back as card 4 last time, but USB cards renumber, so check
`/proc/asound/cards` first.

One thing that would be worth measuring if a 3.5 mm loopback cable turns up: **the iD14's own round
trip**, for a direct comparison rather than an inferred one. It needs its routing set first or its
outputs pass nothing and the chirp never returns — see `RESULTS-id14-mixer-2026-11-03.md`, rows 1
and 2 to `DAW Mix`. That is a nice-to-have, not a dependency of anything above.
