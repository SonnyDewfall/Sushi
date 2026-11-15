# Takes

What a recording is on disk, how to check one, and the engine that captures
it. To make one, see [Recording](../guide/recording.md).

A recording is a folder, not loose WAVs: the stems, a copy of the config Sushi
loaded for the run (with the amp written into it), a copy of the v2 rig file it
was compiled from when there was one, and a `take.yaml` saying what was
recorded, what is known of how the stems line up, and which commit the library
was at. Because the rig is in the folder, a take can be re-amped through the
rig that made it months later. Takes go in `~/sushi-rig-takes/`, outside the
checkout, so `git clean -x` cannot delete them.

**What the copies hold, and what they do not** (`transport.py`). `config.json`
is the config file Sushi loaded, read at RECORD (a live switch rewrites it under
a running rig), with the amp as the agent last sent it. `rig.yaml` is the rig
file as it stood at RECORD, so a Save made before the take is in it. Neither is
a capture of the running Sushi: **a parameter moved since the rig came up and
not saved is in neither**, where Save would have captured it. A take that must
carry the exact sound wants a Save first.

```
~/sushi-rig-takes/riff-idea/
    take.yaml         what was recorded, what is known of how the stems line up, and the library commit
    config.json       the config Sushi loaded for the run, with the amp
    rig.yaml          the v2 rig file as it stood at RECORD (v2 rigs only)
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

**Offsets are carried, not compensated, and the record button does not measure
them yet.** A dry tap leads its processed stem by the rig's buffering, 257
samples, 5.35 ms, at a quantum of 256 (`take.py`). Stems are written exactly as
captured, never shifted on write; `take.yaml` has a place for each stem's
measured offset (`latency_samples`) and states the consequence as a sentence
under `alignment.note`, with what measured it under `alignment.measured_by`. A
take made by RECORD, from the page or the panel, passes no offsets
(`transport._finish`): its `measured_by` reads "not measured: the record button
does not measure offsets yet" and its note says each processed stem's offset
"was not measured, so they are exactly as captured and may need lining up by
ear". Only a caller of `take.finish_take` that hands it measured offsets gets
the "move them N ms earlier" sentence; nothing in the tool does today. Whether
the figure above holds for a dry tap against what came out of the amp is, in
`transport.py`'s words, a hypothesis to test on the rig.

```bash
sushi-rig take check ~/sushi-rig-takes/riff-idea       # whole, and what it says of how it lines up
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

## Names

A take's name follows the rule rigs and configs share (lowercase letters,
digits, `-` and `_`, starting with a letter or digit, at most 64 characters:
`take.check_name`). Recording under a name already used moves the earlier take
to `_archive/<name>/`, never overwrites it.

A take recorded without a name is named for the rig, the day and a take
number (#134, `take.default_name`): `electric-clean-2026-11-12-take-03` is the
third take of electric-clean on 12 November.

- **The rig** is the running rig's name, which is its rig file's.
- **The date** is the day by the rig machine's own clock, local time, since
  the name is for the player to recognise; `take.yaml` records the moment in
  UTC.
- **The number** is one more than the highest present for that rig and that
  day, counted from the takes folder and `_archive/` (`take.names_used`). So a
  take recorded over keeps its number in the archive, and a gap below the
  highest (a take renamed or deleted) is never filled, which keeps the numbers
  in the order played. It is not a promise that a number is never used twice:
  nothing remembers a take gone from both folders, so removing the newest take
  frees its number and the next take gets it. Two digits, so take 10 sorts
  after take 9, and as many as it needs past 99. Takes are counted by the name
  the rule would give them whatever digits the number is written with, so one
  numbered by hand (`-take-3`) counts, and a name that only looks like one
  does not.
- **A long rig name** is cut to its first 43 characters in its takes' names,
  the same at every take number (64 characters, less the date, `take` and
  room for four digits: `take.rig_in_name`); the take is never refused for it.
  A name the rule for names would refuse is spelt so that it passes:
  lowercase, anything else a separator, a run of separators collapsed to its
  first, and none at either end (`My Rig!` is `my-rig`, `a__b` is `a_b`). Two
  rigs alike in those 43 characters, or alike but for their separators, share
  one run of numbers. Past take 9999 of the longest rig name the default no
  longer fits and is refused by the name rule: that take has to be named by
  hand.
- **It never takes a name a take has** (`take.next_name`). Whatever the count
  said, the name is looked for in the takes folder and in `_archive/`, and the
  number steps on until neither has it: a take recorded without a name never
  moves an existing take to the archive. Only a name typed does that.

The agent works the name out, not the page: `GET /record` offers it while idle
(`default_name`) and the record bar shows it in the name field, so two pages
show the same one, and `POST /record/start` with no name asks the same function
again. The name shown is therefore the name used, unless a take was made in
between (from another page, or midnight passed), when the start gets the next
number instead of a name a take already has. A name typed is used as it is.
Takes named `take-<date>-<time>`, the default before #134, are takes like any
other: listed, checked and archived, and counted towards no rig's number.

How the page's name field behaves, and that selecting the default on a tap is
not yet tried on a real phone, is in [the web UI](web-ui.md); the panel's
record bar names an unnamed take by the same rule
([the v1 workflow](v1-workflow.md#the-panels-record-bar)).

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
a 5.3 ms budget that lost nothing and is not explained). A take of a
two-channel rig, guitar and vocal, has since been recorded from the page and
its `master.wav` listened back (at Sonny's word; MANIFEST, 2026-11-14), but
with no figures kept, so the accounting of an eight-column take is still not
on record. Still not verified: a stereo dry tap.
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
