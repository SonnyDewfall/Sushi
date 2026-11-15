# The rig file

A rig is one file, `config/rigs/<name>.yaml`: a list of channels, each a name,
an input role, `mono` or `stereo`, and a chain, plus a master that says where
the mix goes. No engine bus or channel number is written by hand. The web UI's
Save, Save as, New rig and channel edits write these files
([Saving and going back](../guide/saving.md), [Building a rig](../guide/building-a-rig.md));
they can also be edited by hand.

```yaml
kind: rig
schema_version: 1
name: guitar_vocal
description: ...                # optional, as are meta: and host:
amp:                            # optional: the neural amp, in front of the board
  plugin: nam
  model: amp/models/Neve 2.nam
  parameters: {Input Lvl: 0.0, Output Lvl: 6.0, Quality: 1.0}   # in the amp's own units
channels:
  - name: guitar
    input: guitar-in            # a role in the machine file
    width: mono                 # what the input is; the chain is always stereo
    chain:
      - name: guitar_compressor
        uri: http://guitarix.sourceforge.net/plugins/gx_compressor#_compressor
        bypassed: false
        parameters: {RATIO: 0.07, THRESHOLD: 0.8}   # Sushi's normalised 0 to 1
    level: -3.0                 # optional, dB: the channel's volume in the mix
master:
  output: monitor-out           # a role in the machine file
  chain: []
  level: -3.0                   # optional, dB: the master volume
midi: ...                       # optional: see the MIDI page
```

A chain entry carries its settings (`bypassed`, `program`, `parameters`,
`properties`), a channel may carry a `strip` (gain, pan, mute), and the rig its
`amp`. A channel's name becomes its track's, its stem's file name in a take and
its send's prefix, so it follows the naming rule takes and rigs share
(lowercase letters, digits, `-` and `_`); `master`, `master_return` and
`<channel>_send` belong to the compiler. The roles are the
[machine file](machine-file.md)'s; the `midi:` section is in [MIDI](midi.md).

## Levels

A channel's level in the mix is `level:` on the channel, in dB, 0 being unity.
It is the send's gain: the stem is unaffected, which is why it is not a strip
(a strip acts after the send and would change the stem). This is where the
expression pedal's blend between two channels lives (#34): one CC driving two
channels' levels in opposite directions. SAVE writes a level moved live back
as dB to a thousandth.

The master's volume is `level:` on `master`, in dB, compiled to the master
track's own gain as a channel's `level:` is to its send's. Unity writes nothing.

## What `emit` makes of it

```bash
sushi-rig emit config/rigs/guitar_vocal.yaml -o /tmp/gv.json
```

`emit` reads either kind of file. For this kind it allocates everything: one
engine input per mono channel (feeding both halves of its stereo chain), each
channel's own output for its stem, a send at the end of each chain, and a
master track — last, on output bus 0, with its own chain for the limiter — that
is all the monitors hear. A rig has at most three channels, because the master
and three stereo stems fill Sushi's eight outputs; more is refused with the
arithmetic.

A rig of channels can be made, and a channel added or moved to another
input, from the web UI's New rig screen and the rack
([the agent](agent.md#new-rigs-and-channel-edits)). Those write these files, in place and keeping their
comments, and only once the rig compiles and wires against the machine file
as `up --rig` would compile and wire it; the machine file itself is never
written, and a role it lacks is said with what to add.

## A mono plugin on a stereo channel

A guitar is one input, and reaches both monitors the same, as a DAW puts a
mono source on a stereo track: the machine file's `mono_to_stereo` (or the
compiler's mono input) feeds it to both halves of its channel, every channel
is a stereo track, and the master goes out on both monitors. A plugin with
one audio output used to undo that. Sushi 1.3.0 runs a plugin on a track
with the lesser of its own ports and the track's channels, and then
**clears every channel the plugin did not write** (its `track.cpp`), so from
the first mono plugin on, the right channel is silent; bypassing the plugin
changes nothing, since a bypassed plugin keeps its channel count. Every
guitar chain here starts with the Guitarix compressor, which is mono, as are
the Tube Screamer and both octavers. Measured in CI's container (2026-11-02): seven of
the eleven rigs had nothing on the right at all, and the other four only
what a later reverb or delay fed across, 5 to 18 dB down. That is "I only get
the left channel out."

So **the compiler puts a mono plugin's output back on both channels.** After
every plugin with one output it inserts `<plugin>_to_stereo`: Sushi's own
internal `sushi.testing.mono_summing`, which writes the sum of its inputs to
every output. Straight after a mono plugin the right channel has just been
cleared, so the sum is the left channel, exactly, on both sides. It is never
put after a stereo plugin, where it would fold the image to mono and add
6 dB. Measured: with it, every rig's left and right are within 0.3 dB for a
mono source, and CI holds them within 1 dB (`tests/test_stereo.py`).

The copy is the compiler's, like the sends and the return: it is never in a
rig file and never saved, the web UI does not show it, and it is never moved,
removed or bypassed by itself. It goes with its plugin: a move steps the
plugin and its copy as one past their neighbour (and past a neighbour and
its copy as one), a remove takes both, a live add of a mono plugin adds its
copy in the same operation, a rig switch adds and removes them with their
plugins, and undo of a move knows the pair. A name ending in `_to_stereo` is
refused for anything else.

Sushi does not report a plugin's channel counts (neither `--dump-plugins` nor
gRPC's processor info carries them), so the compiler reads them from
`plugin-ports.txt`, committed beside the manifest and checked against lilv in
CI. A plugin the table does not list is refused by name, with how to add it:
guessing would be the silent right channel again, or a stereo plugin folded.

What this does not do:

- **The v1 configs still lose the right channel.** `config/*.json`, which
  `sushi-rig up <name>` without `--rig` runs, are left as they were; their
  null-test references are `config/sound/<name>.v1-mono.yaml`, named for
  what they are. `up --rig`, the agent's switches and the web UI run rigs of
  channels, and are stereo.
- On a **stereo** channel a mono plugin hears the left input alone, and its
  copy puts that on both sides. No committed rig has a stereo channel.
- A plugin with no audio output would silence everything after it, and is
  refused.
- Moving, adding or removing a mono plugin is two of Sushi's commands; for
  the moment between them the right channel can be silent, or a stereo
  plugin's image folded. If the second fails, the first is undone; if that
  fails too, the agent says what the chain holds, and a copy left with no
  plugin before it may then be moved or removed, which is the repair.
- The patchbay and SAVE's check of which rig file describes the running rig
  compile for routing only, without the copies, and need no
  `plugin-ports.txt`; everything whose chains Sushi loads (`up --rig`,
  `emit`, the renders, the probes) has the copies and refuses a plugin the
  table lacks.
- **Measured on the rig, not judged by ear.** Offline renders in CI's container
  hold it, and a loopback on the rig on 2026-11-12 found each plugin of
  guitar_vocal's chains, alone on a channel, within 0.1 dB between the sides
  (MaGigaverb within 1.2), so the copy does its job in the live engine
  (`tool/spikes/live_rig/RESULTS-loopback-2026-11-12.md`).

The patchbay and selftest's probes read these files too:

```bash
sushi-rig patchbay generate config/rigs/guitar_vocal.yaml -o /tmp/rig.qpwgraph
sushi-rig probes config/rigs/guitar_vocal.yaml --amp
```

## Rigs converted from v1 configs

Every tone in `config/*.json` has a rig of the same name, first written by
`convert`, which refuses to write one that does not compile back to every
plugin and setting the config held:

```bash
sushi-rig convert config/electric-clean.json            # -> config/rigs/electric-clean.yaml
sushi-rig convert config/x.json --input vocal=vocal-in:mono   # more than one track
```

The rig in `config/rigs/` is the one to edit, and is no longer compared with
the config it came from: rigs are saved into from the web UI, and some have
moved on (electric-chorus-2 has a vocal channel its v1 config lacks; `clean`
and `hard-feelings-record` have no v1 config at all). The conversions as they
were first made are held to their v1 configs in the tests' frozen fixtures
([Tests and CI](testing.md)). Nor do the two sound the same: a rig puts a mono
plugin's output on both channels and the v1 config does not (see
[A mono plugin on a stereo channel](#a-mono-plugin-on-a-stereo-channel)), so
each has a sound reference of its own, the rig's `config/sound/<name>.yaml`
(`<name>.<channel>.yaml` for a rig of more than one channel) and the v1
config's `<name>.v1-mono.yaml`.

Rig files keep their comments when the tool rewrites them: `write_rig` updates
the file in place (comments, blank lines and key order stay), and `convert
--force` on an existing file does the same.

Each input is linked once, the master's outputs go to the monitors, and the
stems are linked to nothing. The probes follow each channel through its stem
and, through its send, out of the master. This is what `up --rig` and the web
UI run.

## v1: a rig.yaml of tracks

The older form, `config/src/*.yaml`, which `emit` also reads and the v1
configs in `config/*.json` are built from
([the v1 workflow](v1-workflow.md)). There a channel is a role, a chain and an engine bus. `config/src/guitar_vocal.yaml`
is the first rig with two: a guitar on bus 0 and a vocal on bus 1, summing into
one monitor pair.

Adding a third is a declaration, not an edit in five places — which is the
actual deliverable of issue #18. Give the track a `source:` role, put the role
in the machine file, and regenerate the patchbay. In particular the amp and the
tuner follow the **`guitar-in` role**, not an input index: both used to take
whatever fed `sushi:audio_input_0` to be the guitar, which is a position, and
positions move. Put the vocal channel on bus 0 and the amp would have been
reprofiling the singer.

Plugin names carry their channel (`guitar_eq`, `vocal_eq`) because Sushi's
processor names are global rather than per track — two plugins called `eq` are
refused outright — and because those names are the panel's tab labels, so the
prefix is what says which channel a tab belongs to.
