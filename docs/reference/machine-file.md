# The machine file

`machines/<hostname>.yaml` is the one file that names physical ports. Everything
else addresses a **role** (`guitar-in`, `vocal-in`, `monitor-out`), so a rig
file works on another machine with another interface.

```yaml
# machines/sonny-dewfall-XPS-9320.yaml, without its comments
schema_version: 1
interface: Audient iD14
roles:
  guitar-in:
    ports: ["Audient iD14:capture_AUX0"]
    mono_to_stereo: true
  vocal-in:
    ports: ["Audient iD14:capture_AUX1"]
    mono_to_stereo: true
  monitor-out: ["Audient iD14:playback_FL", "Audient iD14:playback_FR"]
controllers:            # MIDI controllers: see the MIDI page
  mpd218:
    port: "MPD218:MPD218 Port A"
    channel: all
    controls:
      knob1: {cc: 3}
      pad1: {note: 36}
  fbv:                  # checked with aseqdump, 2026-11-22
    type: pedal
    port: "FBV Express Mk II:FBV Express Mk II MIDI 1"
    channel: 0
    controls:
      a: {cc: 20, kind: switch, behaviour: single, job: previous-rig}
      b: {cc: 21, kind: switch, behaviour: single, job: next-rig}
      c: {cc: 22, kind: switch, behaviour: single}
      d: {cc: 23, kind: switch, behaviour: single}
      pedal: {cc: 7, kind: expression, range: [12, 127]}
      wah: {cc: 11, kind: expression, range: [12, 127]}
      toe: {cc: 43, kind: switch, behaviour: latching}
```

Ports are written as qpwgraph shows them, `<client>:<port>`; the device's ALSA
*profile* is deliberately absent, and the live graph supplies it.
`machines/example.yaml` is a pattern to copy for a new machine (its port names
were written without the rig and want checking against a live graph), and
`machines/loopback-test.yaml` is for the loopback test in
`tool/spikes/live_rig/`. The web UI reads the roles for its input pickers and
**never writes this file**: a role a rig needs and the file lacks is said with
the lines to add ([the agent](agent.md#new-rigs-and-channel-edits)). A
controller is described under `controllers:`; see [MIDI](midi.md).

## Controllers

Each entry under `controllers:` is a MIDI device: its `port` as `aseqdump -l`
lists it (client, `:`, port), its `channel` (0 to 15, or `all`), and its
`controls`, each of which sends one `cc` or one `note`. A controller with a
`type` (`pedal`, #191) describes each control as well:

- `kind`: `switch` or `expression`;
- a switch's `behaviour`: `momentary` (the press, a rise through 64, toggles),
  `single` (every message is a press) or `latching` (followed);
- an expression pedal's `range`: `[heel, toe]`, what it sends, `[0, 127]` if left
  out. Informational, except that the agent silences a pedalled volume at or
  below the heel;
- a switch's `job`: `previous-rig` or `next-rig`, which steps through
  `config/rigs/` on every rig, whatever the rig's own map says. A controller
  with a job needs its `port`, since the agent reads it.

`sushi-rig machine check` refuses a control that is not one: a type, kind or
behaviour it does not know, a note on a pedal or on a latching switch, a range
that is not a heel below a toe, a job given twice, and a model on a controller
without a type. What a rig may assign each control, and how the FBV Express
MkII's entry was checked with `aseqdump`, and how another board is, is in [MIDI](midi.md#the-device-model-191). A pedal
that is not plugged in is "not connected", not a fault, unless the running rig's
map is written for it; it is picked up when it is plugged in.

## Ports by role, not by device name

`Patchbay/rig.qpwgraph` names ALSA device *profiles*, so changing interface (or
profile) silently orphans it: qpwgraph keeps the dead links and nothing reports
them. Three commands address that (issue #54):

**`machines/<hostname>.yaml` is committed, deliberately.** It is the one
hand-authored file naming physical ports, and it is tracked for the same reason
the patchbay is: setting up a second machine proved it the most expensive thing
to recreate, and the next machine should start from a worked example rather than
from scratch. The hostname in the name is what keeps two machines' files from
colliding. Nothing in `.gitignore` excludes them, and nothing should — a
`machines/` entry added later on the assumption that these are per-machine
scratch would quietly undo this.

```bash
sushi-rig patchbay check                      # saved links whose node is not in the live graph
sushi-rig machine check [machines/x.yaml]     # validate a machine file on its own
sushi-rig patchbay generate rig.yaml -o /tmp/p.qpwgraph   # from machines/<hostname>.yaml
```

`patchbay check` sorts every node the saved file names into three states.
**Missing** (nothing live answers to that name) is the only one that exits 1, so
exit 1 means *a node is definitely gone*, not *something is wrong*. **Not
running** is a node the rig starts itself, absent because the rig is down: it is
listed, it does not fail, and it is not a pass for those links either — run
`sushi-rig up` and check again to cover them. **Present** needs no comment.

> Run it with the rig **up**. The first real run was made with the rig down and
> reported `sushi` and the tuner as gone alongside one genuinely dead profile —
> three findings where there was one. That was the tool's fault, and the two
> states above are the fix.

`generate` builds a patchbay from the machine file's roles and the `source:` /
`sink:` a track names, plus a tuner tap on the guitar role. It prints to stdout
or the path you give. **`Patchbay/rig.qpwgraph` is generated** — from
`config/src/guitar_vocal.yaml` and `machines/sonny-dewfall-XPS-9320.yaml` — and
a test asserts the committed file is byte-for-byte what the command produces, so
editing it in qpwgraph's GUI fails the build. It is the v1 layout's patchbay:
a rig of channels (`up --rig`, the web UI) never loads it and brings its own,
generated at start from the rig and the machine file. Regenerate instead:

```bash
sushi-rig patchbay generate config/src/guitar_vocal.yaml -o Patchbay/rig.qpwgraph
```

A role with one port feeding a stereo bus is an error unless the role *declares*
it means to feed both halves:

```yaml
roles:
  guitar-in:
    ports: ["Audient iD14:capture_AUX0"]
    mono_to_stereo: true
    monitor-out: ["Audient iD14:playback_FL", "Audient iD14:playback_FR"]
```

That is stated rather than inferred from the port count because the saved
patchbay shows both wirings: the dead `Pro` profile duplicates one input across a
bus, and the live `Multichannel` profile wires one input per channel. It is also
what centres a mono guitar — one port into both halves of the bus. Without it
the guitar came out of the left speaker only.
