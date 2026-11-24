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
      a: {cc: 20, kind: switch, behaviour: single, job: previous-rig}
      b: {cc: 21, kind: switch, behaviour: single, job: next-rig}
      c: {cc: 22, kind: switch, behaviour: single}
      d: {cc: 23, kind: switch, behaviour: single}
      pedal: {cc: 7, kind: expression, range: [20, 127]}   # Vol mode
      wah: {cc: 11, kind: expression, range: [20, 127]}     # Wah mode, by the toe switch
      toe: {cc: 43, kind: switch, behaviour: latching}

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

Blend, parameter and volume compile into Sushi's own `cc_mappings` and run in
the engine with no Python in the path, once the pedal is linked to Sushi's MIDI
input ([below](#linking-the-pedals-to-sushi-210)); a blend is two mappings on
the two channels' sends with their ranges reversed, each channel's own level
being its loudest. Bypass, every mute ([a mute](#a-mute-is-the-channels-send-bypassed-210)),
a volume pedal's silence at the heel, rig stepping and rig switching by Program
Change go through the agent,
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
| `behaviour` | a switch | `momentary`: 64 or more when pressed and less when let go. `single`: one message a press, whatever its value. `latching`: its value alternates on each press |
| `range` | an expression pedal | `[heel, toe]`, what it sends at either end, whole numbers 0 to 127; `[0, 127]` if left out. Informational, but for the heel: see [the volume](#a-pedal-on-a-channels-volume) |
| `job` | a switch | `previous-rig` or `next-rig`: the device's own job, on every rig (below) |

What a control may be given in a rig's `midi:`:

| Control | May take |
|---|---|
| a switch | `bypass: channel/plugin` (an effect on or off), `mute: channel` (a channel on or off) |
| an expression pedal | `volume: channel`, `parameter: channel/plugin/parameter`, `blend: [channel, channel]` |

A parameter is everything after the plugin, as Sushi names it, a slash and all:
`parameter: board/delay/Dry/Wet level` is SWH lcrDelay's mix. A bypass is
`channel/plugin` exactly.
| a switch with a `job` | nothing: it steps rigs on every rig |
| an untyped CC (the MPD218's knob) | any of them |
| an untyped note (a pad) | `bypass`, `mute` |

A rig map that names a control the device does not have, gives a switch a
volume or a parameter, gives the pedal a bypass or a mute, or names a control
with a job, is refused when it compiles (`up`, a switch, the new-rig screen),
naming the control and what it can take.

**A footswitch's press depends on how it sends.** Read as "on at 64 and
above", a switch that sends 127 when pressed and 0 when let go would be on only
while held. So:

- **momentary**: a press is the rise, a value of 64 or more after one below 64.
  A value resent while the switch is held is not a second press.
- **single**: every message is a press, whatever its value (FBV Control's
  "Single", which sends one message a press). FBV Control's names are what it
  calls them, not what comes out: on the FBV Express MkII its "Momentary" sent
  CC *n* = 0 on a press and CC 0 = 0 on release, which is `single` here.
- **latching**: the switch is followed. Its effect is on at 64 and above and
  bypassed below, acting only when the value crosses 64; its channel is
  muted at 64 and above, by the agent, the same way; a step goes on each
  crossing (FBV Control's "Toggle", which alternates 127 and 0).

A momentary or single press toggles its effect's bypass or its channel's mute,
or steps the rig. A press within 40 ms of the last one accepted on the same
switch is a bounce or a resend, and does nothing, and so is a momentary
switch's rise within 40 ms of its release (a release that bounces). A switch described as the
wrong one misbehaves in a way that says which it is: a Toggle switch read as
momentary acts on every other press, and a Single switch read as momentary acts
once and then not again until a release that never comes, unless it sends 0.

### A mute is the channel's send bypassed (#210)

`mute: <channel>` bypasses the channel's send (`<channel>_send`, the last
plugin of every channel, which copies it into the master's return). So the
master, which is what the monitors hear, has nothing of the channel, and the
channel's own output, its stem, still plays.

It used to set the track's `mute` parameter, natively in `cc_mappings` for a
latching switch and through the agent for a press. Sushi 1.3.0 applies a
track's gain, pan and mute after the track's plugins (`track.cpp`), so after
the send: only the stem went silent. On the laptop (2026-11-23) the live log
showed C (cc 22) toggling the octave channel's `mute` 0, 1, 0, 1 and nothing was heard
to change. The compiler had said so all along for a strip
(`rigfile._strip_problems`: "acts after its send").

Why the send's bypass, of the three ways Sushi 1.3.0 could do it:

- **The send bypassed**: no new processor, every channel already has one, and
  a volume pedal on the same channel drives the send's `gain`, which a bypass
  leaves where it is, so the two never fight. Sushi has no CC-to-bypass
  mapping, so every mute is the agent's now (a press toggles it, a latching
  switch or an untyped CC follows its value, muted at 64 and above), and works
  only with the agent running.
- A gain processor of the compiler's before each send: it would be heard, but
  it is a processor in every chain that the save, the live switch, the panel
  and the page would each have to know is the compiler's, as the copies to
  stereo are.
- The master's return: it is the sum of every channel, so it cannot mute one.

(D, on the delay's bypass, toggled the delay and was heard: a plugin's bypass
was never the problem.)

**A bypassed send sends nothing, on a running Sushi; an offline render cannot
show it.** `tests/test_send_return.py` holds both, on Sushi 1.3.0 (the release
AppImage CI uses, run by hand here first, 2026-11-23):

- Running (the dummy frontend, which processes on its own thread): a sine
  played into a channel's send, the master's return recorded by Sushi's own
  `wav_writer`, and the send's bypass set and cleared over gRPC by
  `live.set_live_bypass`, the call the agent's mute makes. The master is the
  channel exactly, then nothing at all while the bypass holds, then the
  channel exactly again; the channel's own output plays throughout.
- Offline (`-o`, how `verify`, `nulltest` and the render tests run): a send
  bypassed in `initial_state` goes on sending at full level for the whole
  render. Not the send: `SendPlugin::set_bypassed` posts a bypass event and
  applies it only when the audio thread receives it (send_plugin.cpp), and
  the offline frontend's blocking loop delivers only the config's own
  `events` (offline_frontend.cpp). An LV2 plugin sets its bypass directly,
  which is why a bypassed plugin renders as bypassed. CI found this on
  8a798de: the first version of the test asked a render to show the mute.

So an offline render of a config with a send bypassed in it hears that
channel; a running rig does not.

The page has no mute of its own to fix: a channel's volume at **-∞** is the
send's gain at its floor, which is heard, and the volume of a channel a pedal
drives is not movable from the page.

## Previous and next rig, on every rig

A switch whose `job` is `previous-rig` or `next-rig` steps through the rigs on
every rig, whatever the rig's own map says, including a rig with no `midi:` at
all. On the FBV that is A and B.

- **The order** is `config/rigs/*.yaml` by file name, as Program Change counts
  (`GET /midi` lists it as `rigs`).
- **It stops at the first and last rig.** It does not wrap round, so a stray
  press on the last rig cannot jump to the first. A press past either end is
  refused, in `/health` (`agent.midi.last_error`, and `last` says so) and on the
  agent's stderr.
- **It counts only from a rig of `config/rigs/`.** A rig running from any other
  file (a v1 config, or `config/src/electric_board.yaml`) is not in the order,
  even where a file of `config/rigs/` has the same name, and a step from it is
  refused and said the same way.
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
and the FBV's A and B.

**A pedal that is not plugged in is not a fault unless the rig needs it.** Each
port in `/health`'s `agent.midi.feeds` is `required` when the running rig's
`midi:` is written for that controller, whether or not its map has anything for
the agent (a map of sweeps alone still needs its pedal), and not otherwise.

- A pedal that is not plugged in, or is unplugged mid-set, leaves its listener
  open and waiting, subscribed to ALSA's announcements. It is picked up as soon
  as it is plugged in again, with no `up`, restart or switch.
- Not required, and not plugged in: the port is `absent`, "not connected", and
  `agent.midi` is not `failed`. With no pedal connected at all it is `absent`.
  So the laptop without its FBV shows no Pedals problem.
- Required, and not plugged in: the port is `waiting`, and `agent.midi` is
  `failed`, saying which pedal the rig is waiting for, until it is plugged in.
- No alsa-midi, or no ALSA sequencer (`/dev/snd/seq`), is the same: `absent`
  for a pedal no rig map needs, and a fault for one it does. The listener ends
  there, since there is nothing to wait on; the next `up` or restart looks
  again.
- Any other port that will not open, or will not read, is `failed`, naming it,
  while the other ports still serve.

## A pedal on a channel's volume

`volume: <channel>` is one mapping on the channel's send, parameter `gain`, the
same gain a channel's `level` sets, and a job for the agent:

- **Toe**: the channel's own `level` from the rig file, as compiled. Never above
  it.
- **The sweep starts 40 dB below the level.** Sushi's mapping is linear in the
  send's normalised gain, which is linear in dB over -120 to +24 dB, so a sweep
  from -120 put half the travel at -60 dB and left only its last third audible.
  From 40 dB down, half way is about -20 dB below the level and three quarters
  about -10 dB.
- **Heel: silent, by the agent.** At or below the pedal's heel (the first of its
  `range`, 0 without one) the agent sets the send to silence, on the port it
  already reads. Refused because the agent is busy (a lifecycle command, a
  rig file edit such as a pedal's assignment), it waits until the agent is
  free and goes again from where the pedal is then; a `down` or a switch ends
  the wait. Above the heel it leaves the send to Sushi's mapping. If the
  pedal leaves the heel while the agent is still silencing it, the agent finishes
  on the value Sushi's mapping gives for where the pedal is, so the two do not
  fight; and the first message above the heel after the agent silenced it has
  the agent set Sushi's value once more, in case its silence landed after
  Sushi had followed that rise. **With the agent not running, the heel is 40 dB down, not silent.**
- **The pedal's `range` is informational, but for the heel.** Sushi scales its
  own 0 to 127 whatever the range says: a mapping scaled to a range would need
  Sushi to clamp a value outside it, which could not be checked here (no MIDI
  reaches Sushi in the container). A pedal whose toe stops short of 127 stops
  short of the level, never above it: a toe at 120 is about 2 dB short.
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

## What the page uses (#192)

`GET /midi` is what the rack's pedal assignments read. It changes nothing.

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
 "rigs": ["accustomed", "clean", "..."], "listening": "listening",
 "behind": {"midi": false, "agent": false, "restart": false}}
```

- `controllers` lists every controller in the machine file the running rig was
  compiled against, or this host's file when no rig is up. A control with a
  `job` takes nothing.
- `assignments` is the running rig file's `midi:`, as the file says it, or
  null when it has none.
- `rigs` is the order a step counts through.
- `listening` is `/health`'s `agent.midi.state`.
- `behind` says whether the running rig's mappings are behind its file
  (#210, [above](#assigning-from-the-rack-192)).
- A machine file that will not read is `machine_problem`, with no controllers.

## Assigning from the rack (#192)

The rack assigns a device's controls to targets on the running rig (see
[Playing live](../guide/playing-live.md#assign-a-pedal-on-the-rack)), through
`POST /rig/midi/assign` ([the agent](agent.md#assigning-a-pedal)). It writes
the rig file's `midi:` as you would by hand, keeping the file's comments,
compiled and refused as above, so a page cannot write a map a compile would
refuse:

- a control takes only what its kind may (`takes`), and one with a job none;
- a target holds one control of a binding, and a control does one thing on a
  rig: assigning one in use moves it, after the page asks;
- the map's `controller` is filled in on the first assignment when the
  machine file describes one device with a type; with several, the page asks
  which;
- the last control taken off takes the map out with it, unless it has
  `programs`.

A footswitch on a bypass or a mute is the agent's, and plays at once, when
what it switches is on the running rig: the running listeners take the edited
file's bindings without stopping, so a press on its way or a heel's silencing
goes on as it was. One whose effect or channel is in the file but not on the
rig as it runs (a channel added to the file and not yet restarted, a plugin
taken out live and not saved) plays after a restart, as an edit of the file by
hand would; the page offers it.

What Sushi maps (a sweep, a volume, a blend) is tried live (#210): elkpy's
`MidiController.connect_cc_to_parameter` and `disconnect_cc` take the running
Sushi's mappings to the edited file's, and Sushi's own list of them
(`get_all_cc_input_connections`) is read back, each mapping with its ranges and
mode (the ranges compared within 1e-6: the API carries them as 32-bit floats),
so a change of ranges alone (a blend turned round, a volume whose channel's
`level:` moved) is a change Sushi is seen to make, and a mapping Sushi holds
with other ranges is not the one asked for. Taken, the answer is `how:
"live"` and the running config records the new `midi` section. Refused, not
confirmed, or not in the list read back, what was done is undone, the call in
flight when it failed included (a connect Sushi may have made before it
refused or timed out is disconnected), and the answer is the restart it always
was, `why` saying `midi_live` and what Sushi said. Sushi's list is then read
once more: if it is not the running config's, or a call timed out (and may
still land), or the list cannot be read, the running config is marked
(`_midi_unconfirmed`, with why), `GET /midi`'s `behind` says `unconfirmed`
and offers the restart, and the next switch restarts (`midi_unconfirmed`)
rather than trust a config that may not say what Sushi maps. Not
tried where the mapping is on a MIDI input Sushi did not open at its start, or
names a processor the running rig does not have. **Unverified on a real Sushi**:
whether 1.3.0 honours these calls on a running rig at all (`live.py`, "A
pedal's mapping, live"); if it does not, every such assignment is the restart.

A restart is refused while the rig holds unsaved changes, and so is an
assignment that needs one, which the restart would drop: the page offers
**Save & assign**, which saves, assigns again and offers the restart. An
assignment that is tried live is not refused for unsaved work: if it falls
back, the restart it offers asks for the save itself.

**The file ahead of the rig.** `GET /midi`'s `behind` compares the running
config with a compile of the rig file as it is now: `{"midi": true, ...}`
when Sushi's mappings differ, `"ranges"` when they are the same mappings and
only their ranges differ (a Save that moved a channel's `level:` moves its
volume's and blends' ranges), `"agent"` when the agent's bindings do,
`"unconfirmed"` (why, or null) when a live change left Sushi's mappings not
known, and `"restart": true` when only a restart brings the file's map in
(null when either cannot be read). The **PEDALS** section offers that restart
whenever it says so, however the file got ahead (a hand edit, a save, an
assignment that could not be live), in two words, **Not playing yet**, or
**Ranges changed** for ranges alone, with **Restart**; the whole of it is
the line's title. A Save does not apply them live.

**The running rig keeps the pedal it started with.** A compiled config with a
map records its controller (`_meta`'s `midi_controller`), and the listener
plans from that, not from the rig file as it is now: a file given a map, or
having its map taken out, for the next restart does not change which pedal
the running rig needs, or turn its A and B off. A config with nothing of a
map in it needs no pedal. One compiled before the controller was recorded
there has its rig file read, as before.

`/health`'s `agent.midi.seen` is the last message from each control, bound or
not (`{controller: {control: {count, value, ago}}}`), which the rack's
**PEDALS** lights read: a check that the board sends what the machine file
says, not a learn.

## When the FBV arrives

The FBV Express MkII's entry in `machines/example.yaml` and
`machines/sonny-dewfall-XPS-9320.yaml` was checked with `aseqdump` on the
laptop on 2026-11-22. Out of the box the board's switches sent MMC transport
SysEx (Record, Stop, Play, Rewind) and its pedal nothing at all, so it was set
up in Line 6's FBV Control, which runs on Windows or Mac only. That setup is
`machines/fbv/sushi-rig-line-6.fbv`, to load back into FBV Control if the board
is ever reset. With it:

- A to D send CC 20 to 23 on channel 1 (0 here), on the first port. FBV
  Control's "Momentary" made each press CC *n* = 0 and each release CC 0 = 0,
  never a 127, so they are `behaviour: single`: any message on the switch's
  own CC is a press, and the release, on CC 0, is bound to nothing.
- The pedal is two, by the toe switch: CC 7 in Vol (`pedal`) and CC 11 in Wah
  (`wah`). Its heel rests at 8 to 13, never 0, and at 14 to 17 just after a
  move, so the range is `[20, 127]`, with a little margin over that, and a
  volume is silenced at 20 or below. A heel of 15 left a foot that had come
  to rest at 16 or 17 about 35 dB down, not silent (the laptop, 2026-11-23,
  #210).
- The toe switch sends CC 43, 127 on one press and 0 on the next, as it flips
  Vol and Wah: `toe`, `behaviour: latching`.

For another board, or this one set up afresh, check it the same way:

1. Plug it in by USB and list the sequencer's ports:

   ```bash
   aseqdump -l
   ```

   The board is a class-compliant USB MIDI device with one MIDI In and four MIDI
   Out ports, by Line 6's guide. Its controls send on **Out port 1**. FBV
   Control talks to the board on port 4, which is not the one to read. The
   machine file's `port` is the client name, `:`, and the first port's name,
   from the two name columns: `FBV Express Mk II:FBV Express Mk II MIDI 1` for
   this board.
2. Watch that port:

   ```bash
   aseqdump -p "FBV Express Mk II:FBV Express Mk II MIDI 1"   # or the client:port numbers aseqdump -l shows
   ```

   - Press each of A, B, C and D once, holding it a second, then twice
     quickly. Count the lines each press prints:
     - two lines a press, 127 when it goes down and 0 when it comes up, is
       `behaviour: momentary`;
     - one line a press, the same value each time, is FBV Control's Single:
       `behaviour: single`;
     - one line a press, 127 and 0 on alternate presses, is FBV Control's
       Toggle: `behaviour: latching`.

     A press that prints CC *n* and a release that prints another CC (this
     board's CC 0) is `single` too: the release is bound to nothing.
   - Note each switch's CC.
   - Sweep the pedal from heel to toe. Note its CC and the values at either
     end, and where the heel rests: that resting value, with a little margin,
     is the heel of its `range`, where the agent silences a volume.
   - Click the toe switch and sweep again: a second CC means two modes, each
     its own expression control (this board's `pedal` and `wah`). The toe
     switch's own message makes it a switch too.
   - Note the channel, which aseqdump counts from 0, as the machine file does.
3. Write what you saw into the machine file and check it with
   `sushi-rig machine check`.

Once the entry is right, `GET /health` shows `agent.midi.feeds` with the FBV
`listening`; unplug it and it goes to `absent` (or `waiting`, on a rig mapped
on it), and plugging it back in brings it back without restarting anything.

The board does not take MIDI from the host over USB (Line 6's guide), so its
LEDs show only what it sends, never the rig's state. Nothing here sends to it.

## Linking the pedals to Sushi (#210)

Sushi's mappings act only on what reaches Sushi's own MIDI input, and nothing
linked the pedal to it: the patchbay links audio alone, and the agent reads the
pedal on a sequencer client of its own. Found on the laptop (2026-11-23): Sushi
1.3.0, started with `-j` as `up` starts it, is ALSA sequencer client **`Sushi`**,
its MIDI input port 0 **`listen:in`** (port 1 `read:out` is its output). Its
client number is given at run time (129 there), so it is linked by name. A
manual `aconnect "FBV Express Mk II:0" "Sushi:0"` made the volume pedal follow,
smoothly, from -35 dB to the top.

The agent links it now, and only where Sushi maps something of the pedal
(#210's review: it linked every pedal it listened to, and a link it made
stayed after an assignment had made it wrong). Each listener it starts (the
rig coming up, a restart, a switch, a re-plan after an assignment)
subscribes Sushi's input to the pedal through alsa-midi, as `aconnect` does,
as soon as both are listed, and again when the pedal is plugged in or a
Sushi starts (from ALSA's announcements, as for a hot-plugged pedal):

- **Which pedal, which input.** The controller the running rig's map is
  written for, when the running config's `cc_mappings` are not empty (a
  volume, a sweep, a blend), goes to the input they name (the map's `port`, 0
  unless it says; the host opens `port + 1` inputs, `compile_midi`). No other
  controller is linked: one whose only bindings are rig steps or the agent's
  (a bypass, a mute, a heel) needs nothing of Sushi's input, and `/health`
  says why. One that sends a CC the running rig's mappings use, on an
  overlapping channel, says that too: Sushi would move that mapping from
  either device, so it must not be linked even by hand.
- **As the plan changes.** An assignment that gives the board its first
  volume or sweep links it; one that takes the last away unlinks it. A
  listener asked for no link, or for another input, takes down the links to
  Sushi that this agent made (it keeps a list: ALSA does not say who made a
  link), and only those; a re-plan that drops a pedal altogether has a port
  opened for it to do the same.
- **A link the agent did not make** (by hand, by `up --direct`, by an agent
  before this one) is never taken down, and is said as linked: `/health`
  reads ALSA's own list of the pedal's links, not the plan. One taken down by
  hand while that Sushi still runs is left down until that Sushi goes
  (whichever ALSA announces first, the link going or Sushi going), or the
  next `up`, restart or switch. The link outlives the agent: it is the
  pedal's and Sushi's, and goes when either does.
- **`/health`**: each of `agent.midi.feeds` has `linked_to_sushi` (what ALSA
  lists), `link_wanted` (whether the plan links it) and `link`, why: "linked
  by the agent: ...", "linked already, not by this agent ...", or "not linked
  ..." (the pedal not plugged in, no `Sushi` listed yet, a clash, nothing of
  the map is Sushi's, what ALSA refused). The agent says each change on its
  stderr. The page's **PEDALS** line says **listening, not linked** while a
  pedal that is to be linked is not, with the reason as its title.
- **With the agent not running** nothing is linked by it, and nothing keeps a
  link across a restart or a replug. `sushi-rig up` with no agent to ask, and
  `up --direct`, link the rig's own pedal once, after Sushi answers, by the
  same rule (`midi_listener.link_once`, printed as `pedals` under the rig's
  summary): without it the rig's sweeps and volumes are silent, which is the
  failure #210 was. Once only, since there is no agent to watch for a
  replug; the agent, started later, leaves that link alone, as not its own.

Seen on the laptop: the names, and that a link made with `aconnect` works.
**Not yet run with the agent making it**: `AlsaPort`'s link has been shown
only against a stand-in for alsa-midi.

## Not yet verified on the rig

No committed rig carries a map yet. The FBV entry is from `aseqdump` on the
laptop (2026-11-22): [above](#when-the-fbv-arrives). The machine file's MPD218
entry is from `aseqdump` on the rig (2026-11-01): its pads send notes on
channel 0, a knob sends CC 3 over its full travel, and a held pad also sends
aftertouch.

Seen on the laptop with the FBV on 2026-11-23 (#210): the agent hears the pedal
while Sushi is also subscribed to it; the machine file's `port` names it; its
footswitches and pedal send what the entry says; Sushi counts a mapping's
channel from 0 (a mapping on channel 0 followed the pedal on the wire's
channel 0); Sushi's MIDI input is ALSA client `Sushi`, port 0 `listen:in`; and
a pedal on a volume, linked by hand, followed smoothly. Still to verify:

- that the agent links the pedal itself, at the start, after a restart and a
  switch, and when it is plugged in (above);
- that the agent's silence at the heel lands after Sushi's floor, as it must,
  and that a foot come to rest at 14 to 17 is now silent (the heel at 20);
- that a mute is heard on the rig: the send bypassed silences the channel in
  the monitors, as it does on a running Sushi with no sound card
  ([a mute](#a-mute-is-the-channels-send-bypassed-210));
- that Sushi takes a pedal's mapping live (an assignment answered `how:
  "live"` for a volume or a sweep, and the pedal following it with no
  restart);
- that a pedal plugged in or unplugged is announced by ALSA as `AlsaPort`
  expects (shown against a stand-in for alsa-midi only);
- how long a step that restarts takes;
- an assignment made on the rack (#192) and heard: one applied live answering
  the next press, and the **PEDALS** lights following a real board.

## What the agent does with a pedal

What a rig's MIDI map asks of the agent (#34, #93, #191) — a bypass, a pad or a
momentary footswitch on a mute, Program Change rig switching — and the
machine's rig-stepping switches, the agent serves while the rig is up, by
opening each controller's port itself (the port the machine file names, through
the ALSA sequencer), and links each to Sushi's MIDI input
([above](#linking-the-pedals-to-sushi-210)). Sushi does the sweeps, blends and
volumes itself.

- A pad press (note on) toggles its plugin's bypass, exactly as
  `POST /live/bypass` would; a pad on a mute toggles the bypass of the
  channel's send ([a mute](#a-mute-is-the-channels-send-bypassed-210)).
- A momentary or single footswitch's press toggles its bypass or mute; a
  momentary one's release does nothing, and a bounce within 40 ms is ignored.
- A pedal on a volume at or below its heel has its channel's send silenced.
- A latching CC bound to a bypass or a mute, and an untyped controller's CC
  (the MPD218's) on either, is a switch: the effect on, or the channel muted,
  at 64 and above and the other way below,
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
reason, `absent` (no pedal connected, and none the rig needs missing), or
`failed` with the problem. `feeds` gives each port's controller, whether the rig
needs it (`required`), its state (`opening`, `listening`, `absent`, `waiting` or
`failed`), its reason and its problem, and whether it is linked to Sushi's
MIDI input (`linked_to_sushi`, and why: `link`); `controller` and `port` are the first's,
the rig map's when it has one; `link_wanted` says whether the plan links it. `rig_problem` says when the running rig's file
could not be read to say which pedal its map of sweeps needs: A and B still
work, but a missing pedal that map needs is then only "not connected". A port that will not open is said once, naming the port, the
controller and what ALSA does list, and not retried until the next `up` or
`restart`; a pedal that is only unplugged is waited for instead (above).
`heard` and `last` (the last message and what it did) are what to
read when a pedal seems to do nothing. Channels are counted from 0 there, as
`aseqdump` prints them. Heard from the FBV on the laptop (2026-11-23); what is
still to hear is [the list above](#not-yet-verified-on-the-rig).
