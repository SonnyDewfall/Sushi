# MIDI: pedals and controllers

How a pedal or pad controller is described, what a rig can map to it, and which
half of that Sushi does and which the agent does. For what it is like to play
with, see [Playing live](../guide/playing-live.md#pedals).

## The map

A rig can carry its MIDI map (#34). The machine file describes the
controller, since pedals vary by machine and venue, and the rig names its
controls:

```yaml
# machines/<hostname>.yaml
controllers:
  mpd218:                        # no type: each control is what it sends
    port: "MPD218:MPD218 Port A"
    channel: all                 # or 0 to 15, as Sushi counts it
    controls:
      knob1: {cc: 3}
      knob2: {cc: 9}
      knob3: {cc: 12}
      pad1: {note: 36}
  fbv:                           # a device with a model (#191): see below
    type: pedal
    port: "FBV Express Mk II:FBV Express Mk II MIDI 1"
    channel: 0
    controls:
      a: {cc: 20, kind: switch, behaviour: momentary, job: previous-rig}
      b: {cc: 21, kind: switch, behaviour: momentary, job: next-rig}
      c: {cc: 22, kind: switch, behaviour: momentary}
      d: {cc: 23, kind: switch, behaviour: momentary}
      pedal: {cc: 7, kind: expression, range: [0, 127]}

# config/rigs/<name>.yaml, on the MPD218
midi:
  controller: mpd218
  controls:
  - {control: knob1, blend: [clean, lead]}        # one pedal fades clean out as lead comes in
  - {control: knob2, parameter: lead/hall/Mix}     # a sweep, 0 to 1 normalised, or range: [a, b]
  - {control: knob3, mute: lead}                   # a whole channel, native
  - {control: pad1, bypass: lead/drive}            # one plugin: the agent's, Sushi has no CC-to-bypass; a CC here is a switch

# config/rigs/<name>.yaml, on the FBV
midi:
  controller: fbv
  controls:
  - {control: c, bypass: lead/drive}               # C turns the drive on and off
  - {control: d, mute: vocal}                      # D turns the vocal channel on and off
  - {control: pedal, volume: lead}                 # the pedal is lead's volume: silent at the heel, its level at the toe
```

Blend, parameter, volume and a latching switch's mute compile into Sushi's own
`cc_mappings` and run in the engine with no Python in the path; a blend is two
mappings on the two channels' sends with their ranges reversed, each channel's
own level being its loudest. Bypass, a pad on a mute, a momentary footswitch on
a mute, rig stepping and rig switching by Program Change go through the agent,
which reads them from the compiled config's `_midi_agent` (rig stepping from the
machine file) and opens the controller's port itself while the rig is up
(`sushi-rig agent`, with the `midi` extra; see
[the agent](agent.md#what-it-needs-and-what-it-refuses)): so they work only with
the agent running. To switch rigs by Program Change, add `programs: rigs` to
`midi:`; program 0 is the first rig in `config/rigs/` by file name, and every
rig you switch to needs it too for the next switch to work. A pedal's channel
volume (a blend's or a `volume`) is a pedal position, so SAVE does not write it
back as the level. A rig with a map refuses to compile to a config without its
controller, since a rig running without its pedals would be the silent failure.

Sushi maps a CC by its number and channel on its MIDI input, not by which
device sent it. Two controllers patched into Sushi must not send the same CC on
overlapping channels, or one's knob moves the other's mapping. The agent reads
each controller's port on its own and has no such clash.

## The device model (#191)

A controller with a `type` (today only `pedal`) describes each of its controls,
and a rig's map is checked against that description when it compiles. A
controller without one (the MPD218's entry) is read as it always was.

| Key | On | What |
|---|---|---|
| `kind` | every control | `switch` (a footswitch) or `expression` (a pedal that sweeps) |
| `behaviour` | a switch | `momentary`: it sends 64 or more when pressed, and less when let go, or one value per press. `latching`: it alternates its value on each press |
| `range` | an expression pedal | `[heel, toe]`, what it sends at either end, whole numbers 0 to 127; `[0, 127]` if left out |
| `job` | a switch | `previous-rig` or `next-rig`: the device's own job, on every rig (below) |

What a control may be given in a rig's `midi:`:

| Control | May take |
|---|---|
| a switch | `bypass: channel/plugin` (an effect on or off), `mute: channel` (a channel on or off) |
| an expression pedal | `volume: channel`, `parameter: channel/plugin/parameter`, `blend: [channel, channel]` |
| a switch with a `job` | nothing: it steps rigs on every rig |
| an untyped CC (the MPD218's knob) | any of them |
| an untyped note (a pad) | `bypass`, `mute` |

A rig map that names a control the device does not have, gives a switch a
volume or a parameter, gives the pedal a bypass or a mute, or names a control
with a job, is refused when it compiles (`up`, a switch, the new-rig screen),
naming the control and what it can take.

**A momentary switch toggles on each press.** A switch that sends 127 when
pressed and 0 when let go, read as "on at 64 and above", would be on only while
held. So a momentary switch's press (any value of 64 or more) toggles its
effect's bypass, or its channel's mute, and its release does nothing. Because no
state is kept between presses, this also serves a switch that sends one value
per press (FBV Control's "Single"), as long as that value is 64 or more. A
latching switch is followed: its effect is on at 64 and above and bypassed
below, acting only when the value crosses 64. A latching switch's mute stays
Sushi's own mapping, muted from about half way up.

**Sushi's own CC mute follows the value.** A mute in `cc_mappings` is the
track's `mute` parameter driven as `min + (max - min) * value / 127` in absolute
mode, as for any parameter (from 1.3.0's `midi_dispatcher.cpp` and
`json_configurator.cpp`, read on 2026-11-01 for #34). It has no toggle. So a
momentary footswitch on a mute is compiled for the agent instead, which reads
the track's `mute` and sets the other value. Neither has been heard on the
rig.

## Previous and next rig, on every rig

A switch whose `job` is `previous-rig` or `next-rig` steps through the rigs on
every rig, whatever the rig's own map says, including a rig with no `midi:` at
all. On the FBV that is A and B.

- **The order** is `config/rigs/*.yaml` by file name, as Program Change counts
  (`GET /midi` lists it as `rigs`).
- **It stops at the first and last rig.** It does not wrap round, so a stray
  press on the last rig cannot jump to the first. A press past either end is
  refused, in `/health` (`agent.midi.last_error`) and on the agent's stderr.
  A press on a rig outside that order, such as a v1 config, is refused the same
  way.
- **It switches by `POST /rig/switch`'s path**: live where it can be, otherwise a
  restart, about five seconds of silence. The log entry is `op: "rig"`,
  `source: "midi"`, with the switch's `control`.
- **A press while a switch is running is refused, not queued.** That holds for
  a step or a Program Change while any rig switch, `up`, `down` or `restart` is
  running. For most of a switch the ports are closed, and a press is not heard
  at all; a press that is heard is refused and said. A guard against a double
  tap is not built (`midi_listener._press` and `Agent._midi_switch_refusal` are
  where it would go).

The agent listens to every controller with a job on its port, on every rig it
runs. A rig whose map is on another controller is two ports: the MPD218's map,
and the FBV's A and B. A port that will not open is `failed` in `/health`,
naming it, while the others still serve. **So a machine file describing a pedal
that is not plugged in shows the pedals as failed on every rig.**

## A pedal on a channel's volume

`volume: <channel>` is one mapping on the channel's send, parameter `gain`, the
same gain a channel's `level` sets:

- **Heel**: silent, the send at its bottom, -120 dB.
- **Toe**: the channel's own `level` from the rig file, as compiled. Never above
  it.
- **The travel is linear in dB**, as Sushi's mapping is linear in the send's
  normalised gain, which spans -120 to +24 dB. With a channel at 0 dB, half way
  is about -60 dB, three quarters -30 dB and nine tenths -12 dB, so the audible
  part of the sweep is the last third. That is a known limit. A curve would need
  the agent in the path, or a gain plugin with a taper.
- **The pedal's `range`** is what it sends; Sushi scales 0 to 127. A pedal whose
  toe stops short of 127 stops short of the level by as much, and never goes
  above it. A pedal whose heel sends more than 0 is not quite silent at the heel:
  at 5, about -115 dB.
- **On the master: refused.** The master ends in its limiter, and its own gain
  comes after that and stops at 0 dB (#172, #137). A pedal riding it would be a
  second master volume beside the page's capped one. A pedal on a parameter of
  a plugin in the master's chain is allowed, as any effect's parameter is; the
  limiter cannot be named.
- **One driver per channel.** A channel's volume driven by both a blend and a
  `volume`, or by two controls, is refused.

**The top is the rig file's level, as the rig was compiled.** The page shows a
pedalled channel's volume as **driven by** the control and does not let it be
moved, as for a blend. If the level is changed some other way while the rig
runs, through `POST /live/parameter` on the send's `gain`, the next movement of
the pedal puts it back on the pedal's scale, whose top is still the compiled
level. SAVE does not write the pedal's position into the file. To change the top,
change `level:` in the rig file and bring the rig up again, or switch to it.

## What the page will use (#192)

`GET /midi` is what the rack's pedal assignments will read. It changes nothing.

```bash
curl localhost:8029/midi
```

```json
{"ok": true, "machine": "sonny-dewfall-XPS-9320.yaml", "machine_problem": null,
 "controllers": [{"name": "fbv", "type": "pedal", "port": "FBV Express Mk II:FBV Express Mk II MIDI 1", "channel": 0,
   "controls": [
     {"name": "a", "sends": {"cc": 20}, "kind": "switch", "behaviour": "momentary", "range": null, "job": "previous-rig", "takes": []},
     {"name": "c", "sends": {"cc": 22}, "kind": "switch", "behaviour": "momentary", "range": null, "job": null, "takes": ["bypass", "mute"]},
     {"name": "pedal", "sends": {"cc": 7}, "kind": "expression", "behaviour": null, "range": [0, 127], "job": null,
      "takes": ["volume", "parameter", "blend"]}]}],
 "targets": {"bypass": "channel/plugin", "mute": "a channel name", "volume": "a channel name, not the master", "...": "..."},
 "rig": "electric-clean", "rig_problem": null,
 "assignments": {"controller": "fbv", "port": 0, "programs": null,
   "controls": [{"control": "c", "bypass": "lead/drive"}, {"control": "pedal", "volume": "lead"}]},
 "rigs": ["accustomed", "clean", "..."], "listening": "listening"}
```

- `controllers` lists every controller in the machine file the running rig was
  compiled against, or this host's file when no rig is up. A control with a
  `job` takes nothing.
- `assignments` is the running rig file's `midi:`, as the file says it, or
  null when it has none.
- `rigs` is the order a step counts through.
- `listening` is `/health`'s `agent.midi.state`.
- A machine file that will not read is `machine_problem`, with no controllers.

Writing an assignment is #192's; whatever it writes is the rig file's `midi:`,
compiled and refused as above.

## When the FBV arrives

The FBV Express MkII's entry in `machines/example.yaml` and
`machines/sonny-dewfall-XPS-9320.yaml` is unverified: every line of it is marked
`# unverified: check with aseqdump when the pedal arrives`. Nothing of the board
has been seen on the rig. Check it before relying on it:

1. Plug it in by USB and list the sequencer's ports:

   ```bash
   aseqdump -l
   ```

   The board is a class-compliant USB MIDI device with one MIDI In and four MIDI
   Out ports, by Line 6's guide. Its controls send on **Out port 1**. FBV
   Control talks to the board on port 4, which is not the one to read. The
   machine file's `port` is the client name, `:`, and the first port's name,
   from the two name columns, for example `FBV Express Mk II:FBV Express Mk II
   MIDI 1`. That example is the Linux USB-MIDI driver's usual naming, and is a
   guess.
2. Watch that port:

   ```bash
   aseqdump -p "FBV Express Mk II:FBV Express Mk II MIDI 1"   # or the client:port numbers aseqdump -l shows
   ```

   - Press each of A, B, C and D once slowly, then twice quickly.
     - 127 on the press and 0 on the release is **momentary**.
     - One message a press is FBV Control's **Single**: momentary here, if the
       value is 64 or more.
     - 127 and 0 on alternate presses is **Toggle**: `behaviour: latching`.
   - Note each switch's CC. 20 to 23 in the file are placeholders, since the
     factory numbers were not found.
   - Sweep the pedal from heel to toe. Note its CC (7 is a guess, from Line 6's
     Shortboard examples) and the values at either end: they are its `range`.
   - If the pedal has a toe switch or a wah mode, click it and sweep again:
     a second CC (Line 6's examples use 11 for wah) means two modes. Describe
     the one you play.
   - Note the channel, which aseqdump counts from 0, as the machine file does.
   - Note what the Tap/Tuner switch sends. It is not described yet.
3. Write what you saw into the machine file and take the `unverified` marks
   off. Check it with `sushi-rig machine check`.

The board does not take MIDI from the host over USB (Line 6's guide), so its
LEDs show only what it sends, never the rig's state. Nothing here sends to it.

## Not yet verified on the rig

No committed rig carries a map yet, and nothing has been heard from a real
pedal through the agent. The FBV entry is from Line 6's documentation, read
second-hand, and placeholders: [above](#when-the-fbv-arrives). The machine
file's MPD218 entry is from `aseqdump` on the rig (2026-11-01): its pads send
notes on channel 0, a knob sends CC 3 over its full travel, and a held pad also
sends aftertouch. Still to verify on the rig:

- that Sushi counts MIDI channels from 0;
- the name PipeWire gives Sushi's MIDI input, so the patchbay can link the pedal
  to it;
- for the agent's own listener, which counts channels from 0 as ALSA does:
  - that the machine file's `port` is exactly how the sequencer names the pedal
    (client, `:`, port, as `aseqdump -l`'s two name columns);
  - that a footswitch sends what it is written to, and which way round its CC
    is;
  - that the agent hears the pedal while PipeWire and Sushi are also subscribed
    to it;
- that a pedal on a volume sounds as described, and how long a step that
  restarts takes.

## What the agent does with a pedal

What a rig's MIDI map asks of the agent (#34, #93, #191) — a bypass, a pad or a
momentary footswitch on a mute, Program Change rig switching — and the
machine's rig-stepping switches, the agent serves while the rig is up, by
opening each controller's port itself (the port the machine file names, through
the ALSA sequencer). Sushi does the sweeps, blends, volumes and latching CC
mutes itself.

- A pad press (note on) toggles its plugin's bypass, exactly as
  `POST /live/bypass` would; a pad on a mute toggles the channel's `mute`.
- A momentary footswitch's press toggles its bypass or mute; its release does
  nothing.
- A latching CC bound to a bypass, and an untyped controller's CC (the
  MPD218's), is a switch: the effect on at 64 and above and bypassed below,
  acting only when it crosses 64, so an expression pedal or a chattering
  switch does nothing between crossings.
- Releases and aftertouch (an MPD218 pad sends it while held) do nothing.
- A step goes one rig back or on, as above.
- A Program Change on the controller switches rigs: program *n* is the *n*th
  file of `config/rigs/*.yaml` sorted by file name, counted from 0 (a pedal may
  display it as *n*+1), and `GET /rig` lists the order as `programs` while the
  running rig switches by them. A Program Change for the rig already playing
  does nothing; one past the last rig is refused and said.

A switch, by Program Change or step, is
[`POST /rig/switch`'s](agent.md#switching-rigs-live): live where it can be,
otherwise `down` then `up --rig` with the mode, tuner and amp choices `restart`
would keep (not an `--amp-model` override, which belonged to the rig being
left), about five seconds of silence. Switched by Program Change, the next rig
switches on only if its own map says `programs: rigs`; the rig-stepping switches
work on every rig. The rig switched to is compiled first, and one that would not
is refused with the running rig left playing; if its `up` fails anyway, the rig
that was playing is brought back. `/live/undo` stops at a rig switch: what came
before it was done to the other rig. Each change is in `/live/log` with
`source: "midi"` and the control that made it.

The listener starts when a rig comes up through the agent (the CLI's `up`
included, now that it asks the agent), or is already up when the agent starts,
and stops before `down`, `restart` or a switch, and starts again after; a rig
brought up with `up --direct` while the agent runs is not heard until the agent
is restarted. `/health` shows it under `agent.midi`: `listening`, `off` with the
reason, or `failed` with the problem. `feeds` gives each port's controller,
state and problem; `controller` and `port` are the first's, the rig map's when
it has one. A port that will not open is said once, naming the port, the
controller and what ALSA does list, and not retried until the next `up` or
`restart`. `heard` and `last` (the last message and what it did) are what to
read when a pedal seems to do nothing. Channels are counted from 0 there, as
`aseqdump` prints them. **Not yet heard from a real pedal**: see
[the list above](#not-yet-verified-on-the-rig).
