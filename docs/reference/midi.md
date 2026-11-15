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
  mpd218:
    port: "MPD218:MPD218 Port A"
    channel: all                 # or 0 to 15, as Sushi counts it
    controls:
      knob1: {cc: 3}
      knob2: {cc: 9}
      knob3: {cc: 12}
      pad1: {note: 36}

# config/rigs/<name>.yaml
midi:
  controller: mpd218
  controls:
  - {control: knob1, blend: [clean, lead]}        # one pedal fades clean out as lead comes in
  - {control: knob2, parameter: lead/hall/Mix}     # a sweep, 0 to 1 normalised, or range: [a, b]
  - {control: knob3, mute: lead}                   # a whole channel, native
  - {control: pad1, bypass: lead/drive}            # one plugin: the agent's, Sushi has no CC-to-bypass; a CC here is a switch
```

Blend, parameter and mute compile into Sushi's own `cc_mappings` and run in
the engine with no Python in the path; a blend is two mappings on the two
channels' sends with their ranges reversed, each channel's own level being
its loudest. Bypass, a pad on a mute, and rig switching by Program Change go
through the agent, which reads them from the compiled config's `_midi_agent` and
opens the controller's port itself while the rig is up (`sushi-rig agent`, with
the `midi` extra; see [the agent](agent.md#what-it-needs-and-what-it-refuses)): so they work only with the agent running.
To switch rigs from the pedal, add `programs: rigs` to `midi:`; program 0 is the
first rig in `config/rigs/` by file name, and every rig you switch to needs it
too for the next switch to work. A blended channel's
send gain is a pedal position, so SAVE does not write it back as the level.
A rig with a map refuses to compile to a config without its controller, since
a rig running without its pedals would be the silent failure.

## Not yet verified on the rig

No committed rig carries a map yet, and nothing has been heard from a real
pedal through the agent. The machine file's MPD218 entry is from `aseqdump` on
the rig (2026-11-01): its pads send notes on channel 0, a knob sends CC 3 over
its full travel, and a held pad also sends aftertouch. Still to verify on the
rig: that Sushi counts MIDI channels from 0, and the name PipeWire
gives Sushi's MIDI input so the patchbay can link the pedal to it. For the
agent's own listener, which counts channels from 0 as ALSA does: that the
machine file's `port` is exactly how the sequencer names the pedal (client,
`:`, port, as `aseqdump -l`'s two name columns), that a footswitch sends what
it is written to (and which way round its CC is), and that the agent hears the
pedal while PipeWire and Sushi are also subscribed to it.

## What the agent does with a pedal

What a rig's MIDI map asks of the agent (#34, #93) — a bypass, a
pad on a mute, Program Change rig switching; Sushi does the sweeps, blends
and CC mutes itself — the agent serves while the rig is up, by opening the
controller's port itself (the port the machine file names, through the ALSA
sequencer). A pad press (note on) toggles its plugin's bypass, exactly as
`POST /live/bypass` would; a CC bound to a bypass is a switch, the effect on at
64 and above and bypassed below, acting only when it crosses 64, so an
expression pedal or a chattering switch does nothing between crossings; a pad
on a mute toggles the channel's `mute`. Releases and aftertouch (an MPD218 pad
sends it while held) do nothing. A Program Change on the controller switches
rigs: program *n* is the *n*th file of `config/rigs/*.yaml` sorted by file name,
counted from 0 (a pedal may display it as *n*+1), and `GET /rig` lists the
order as `programs` while the running rig switches by them. The switch is
[`POST /rig/switch`'s](agent.md#switching-rigs-live): live where it can be, otherwise `down` then
`up --rig` with the mode, tuner and amp choices `restart` would keep (not an
`--amp-model` override, which belonged to the rig being left), about five
seconds of silence; and the next rig switches on only if its own map says
`programs: rigs`. The rig switched to is compiled first, and one that
would not is refused with the running rig left playing; if its `up` fails
anyway, the rig that was playing is brought back. `/live/undo` stops at a rig
switch: what came before it was done to the other rig. A Program Change for the rig already playing does
nothing; one past the last rig is refused and said. Each change is in
`/live/log` with `source: "midi"` and the control that made it. The listener
starts when a rig comes up through the agent (the CLI's `up` included, now
that it asks the agent), or is already up when the agent starts, and stops
before `down`, `restart` or a switch; a rig brought up with `up --direct` while
the agent runs is not heard until the agent is restarted.
`/health` shows it under `agent.midi`: `listening`, `off` with the reason, or
`failed` with the problem — a port that will not open is said once, naming the
port, the controller and what ALSA does list, and not retried until the next
`up` or `restart`. `heard` and `last` (the last message and what it did) are
what to read when a pedal seems to do nothing. Channels are counted from 0 there,
as `aseqdump` prints them. **Not yet heard from a real pedal**: see
[the list above](#not-yet-verified-on-the-rig).
