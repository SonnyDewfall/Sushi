# Takes

What a recording is on disk, how to check one, and the engine that captures
it. To make one, see [Recording](../guide/recording.md).

A recording is a folder, not loose WAVs: the stems, a copy of the config that
was live when it was made (amp included), a copy of the v2 rig file it was
compiled from when there was one, and a `take.yaml` saying how the stems line
up and which commit the library was at. Because the rig is in the folder, a
take can be re-amped through the exact rig that made it months later. Takes go
in `~/sushi-rig-takes/`, outside the checkout, so `git clean -x` cannot delete
them.

```
~/sushi-rig-takes/riff-idea/
    take.yaml         how the stems line up, what made them, and the library commit
    config.json       the rig, as it was live: what ran
    rig.yaml          the v2 rig it was compiled from: what was meant (v2 rigs only)
        guitar.wav        the guitar channel after its chain, before its volume
    guitar-dry.wav    what went in: the interface's input, before the amp
    master.wav        the mix as it reached the monitors
```

For a rig of channels each channel gives two stems and the master one, in the
rig's order (`transport.py` says why these ports). **A channel's stem is taken
after its chain and before its `level:`**, the send's gain, so it is the channel
alone at the level its chain left it, whatever its fader was at; neither
channel's stem holds the other. The dry tap is the physical input, not Sushi's
input, because with the amp running Sushi's input is the amp's output, and the
dry signal is what re-amping needs. `master.wav` is the master track's output:
the only stem with the mix, the channel volumes, the master chain and the master
volume in it. Two channels that play from one input share one dry tap, named
for the role (`guitar-in-dry.wav`). A v1 run (a rig.yaml of tracks) cannot be
recorded from the web UI: its routing sums every track into the monitor bus, so
there is no stem of one track to take.

The processed stems are a block later than the dry ones — 5.35 ms at the rig's
quantum. They are written exactly as captured and `take.yaml` says how far to
slide them, rather than being shifted on write.

```bash
sushi-rig take check ~/sushi-rig-takes/riff-idea       # whole, and how it lines up
sushi-rig verify ~/sushi-rig-takes/riff-idea/config.json  # the rig that made it
```

```bash
sushi-rig take repair ~/sushi-rig-takes/riff-idea      # after the rig was killed mid-take
```

The recorder writes float32, so a chain pushed past 0 dBFS is kept as it was
rather than clipped, and it rewrites each stem's header after every block, so a
take cut off by a crash is readable after `take repair`. `complete: true` in
`take.yaml` means the take recorded what it was asked to: a take whose capture
failed (the engine or the recorder did not start, or the writer stopped) is
written `complete: false` beside the failure, and `take check` says "nothing was
recorded" of one that holds no audio. A take written before 2026-11-02 could say
`complete: true` of a failed capture; `take check` names that contradiction. If its writer ever
falls behind, the stems lose the same frames together and stay aligned, and
`take.yaml` lists where audio was lost.

## The capture engine

The capture engine (#53, `tool/src/sushi_rig/capture.py`) is the part that takes
audio from PipeWire: a JACK client with one input port per column of a take,
each linked to the port the take names, copying every block into the recorder.
A take names its ports as the machine file does, by PipeWire alias; at each
RECORD the engine matches each against the JACK ports its client lists, by
name or alias (`capture.match_sources`), so `Audient iD14:capture_AUX0` is found
as `Audient iD14 Multichannel:capture_AUX0`. Before that match, the plan itself
resolves each machine-file port to the name a JACK client sees, read off
`pw-dump` (`transport.jack_port`), which is the path the rig's first take
recorded through; the engine's own match by alias is the second line, and that
pipewire-jack lists the PipeWire alias among a port's JACK aliases is expected
and not yet seen on the rig (`pw-jack jack_lsp -A` would show it). A name no port carries, or more than
one does, is refused with every port and its aliases listed, and `take.yaml`
records what was linked under `capture.engine.sources`.

**It has run against a live PipeWire: the first take that captured audio was
recorded on the rig on 2026-11-06** (`take-20261106-203817`, electric-chorus-2,
five columns, 24.6 s, `take check` ok, 0 lost frames and 0 xruns; MANIFEST and
`capture.py` have the numbers, including a slowest callback of 10.2 ms against
a 5.3 ms budget that lost nothing and is not explained). Still not verified:
eight columns (a two-channel rig; that take was five) and a stereo dry tap.
The listener constructs an engine for a `--rig` run with its panel (#57), and
`sushi-rig recorder` for a headless one, which is how the web UI records
([the agent](agent.md#recording-from-the-web-ui)); either says why it has none
rather than doing nothing. A
Python callback was measured on the rig to keep up at quantums of 128 and 256,
under load, with room to spare (the numbers are in `capture.py`), so this is the
engine and not a stopgap before a native helper. It needs the `[capture]` extra
(`pip install -e ".[capture]"`, plus `libjack-jackd2-0`) and has to be started
under `pw-jack`. A stereo stem is captured from the consecutive ports starting at
the one `take.yaml` names (`sushi:audio_output_0` and `_1`); a stem whose port
has no number to count from is refused rather than guessed at.

A take also says if a port went silent. The recorder counts, per port, any block
of exact zeros that follows a block with signal (#81): a dropped link looks
exactly like that, with nothing lost and no xrun, and one was seen on the rig,
256 samples of zeros on three of six ports. `take.yaml` lists them under
`capture.silent_blocks` with the time of the first, and `take check` names each
with the port it came from. A noise gate looks the same, so it is evidence, not a
verdict. The engine's own `xruns`, block size changes and uncopyable blocks are
under `capture.engine`.

The panel's own record bar is described with [the v1 workflow](v1-workflow.md#the-panels-record-bar).
