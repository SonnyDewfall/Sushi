# Tremolo, flanger, tape echo and shimmer

Four effects you can put on any channel, like stomp boxes on a board: a
**tremolo**, a **flanger**, a **tape echo** and a **shimmer** reverb, whose
tail climbs in pitch. Each is a plugin like the others on the rack, with its
own knobs, and each has a channel preset of its own, so you can hear it on
its own in a few clicks. (The foot pedals of a MIDI controller are another
thing: see [Playing live](playing-live.md).)

None of the four has been heard on the rig yet. Everything below about how they
sound comes from renders on a computer, through the same engine the rig runs,
with a made-up guitar signal.

## Try one

The quickest way is a rig of its own, started from the pedal's preset:

1. On the rack, under **RIGS**, click **New rig…**.
2. Type a name, such as `try-shimmer`.
3. Under **Channel 1**, pick `guitar-in` as the **Input**, **mono** as the
   **Width**, and under **Start from**, in **Channel presets**, pick
   `preset shimmer`. The others are `tremolo`, `flanger` and `tape-echo`.
4. Click **Create**, then **Start** or **Switch to** and its name.

You now hear the guitar into that one pedal. A new rig has no amp, so it is
the guitar as it comes in; to hear a pedal after the amp, add it to a rig
that has one, from the Library (below). Click the pedal's round button to
switch it off and on, and compare.

To put a pedal on the rig you are playing, beside what is already there,
click one of the dashed **Add** buttons in the channel's chain and pick it
from the **LIBRARY**: **GxTremolo** and **GxFlanger** under **Mod**,
**GxEchoCat** under **Delay**, **Gxshimmizita** under **Reverb** (see [Add a
plugin](shaping-a-sound.md#add-a-plugin)). It arrives at the plugin's own
settings, not the preset's: for the echo that means no echo at all until
you turn **Head 1** to 1 and raise **Mix**.

You can also load a pedal's preset onto a channel ([Channel
presets](channel-presets.md)), but that replaces the channel's whole chain
with the one pedal.

## What the knobs do

Open a pedal's card on the rack to see its knobs. The ones you would reach
for while playing are at the top; the rest are under **More**. Values are in
the plugin's own range.

**Tremolo** (GxTremolo): the volume pulses.

- **Speed**: pulses a second, in Hz. Around 2 to 10 is a guitar tremolo.
- **Depth**: how far each pulse dips: with Dry/Wet at 100, about 5 dB at
  0.5 and 11 dB at 0.7.
- **Dry/Wet**: 0 is the plain guitar, 100 the tremolo alone. The plugin
  starts at 50, which makes every pulse much shallower; the preset has it
  at 100.
- **More**: **Wave**, triangle or sine.

**Flanger** (GxFlanger): a sweeping, whooshing comb.

- **Speed**: how fast the sweep goes up and down, in Hz.
- **Width**: how far it sweeps. 0 stops the sweep.
- **Feedback**: more gives a sharper, ringing sweep. It runs from -0.99 to
  0.99, and the plugin starts it at -0.707.
- **Dry/Wet**: 0 is the plain guitar. 100, where it starts, is the flanger.
- **More**: **Delay**, where the sweep sits, and **Mix**, which the plugin
  does not explain (it changes the sweep only a little).

**Tape echo** (GxEchoCat): an old tape machine with three playback heads.

- **Mix**: how loud the echoes are. The guitar itself stays where it is, so
  the whole sound gets louder as the echoes come up: about 1 dB at 40 %, 6 dB
  at 100 % with Repeats at 0.35.
- **Repeats**: how many times each echo comes back.
- **Tempo**: the echo time, as a tempo in BPM. It does not follow any clock.
- **Head 1**, **Head 2**, **Head 3**: each is 0 (off) or 1 (on). Head 1 echoes
  an eighth note later (250 ms at 120 BPM), Head 2 a quarter note and Head 3
  a dotted quarter. With every head at 0 there is no echo at all.
- **More**: **Input**, how hard the tape is driven. Raising it makes
  everything louder, the guitar too, about 6 dB from 0.25 to 0.5. And
  **Level**, the echo's output: at 67 % the guitar comes out as loud as it
  went in, and the knob runs from about 12 dB quieter (0 %) to 6 dB louder
  (100 %).

The plugin's own **Output** knob is not shown; Level sets it, inside that
safe range. Shown as itself it would be dangerous: at its top it is about
78 dB louder than at 1.

**Shimmer** (Gxshimmizita): a reverb whose tail rises (or falls) in pitch.

- **Mix**: how much reverb against the dry guitar. It stops short of "all
  reverb" on purpose: the reverb is much louder than the guitar, so even
  this range gets about 8 dB louder from 0 % to 100 %. Start low.
- **Shift**: which way the tail moves in pitch, and how fast. 0 is a plain
  reverb; at 2, with Pitch mix at 1, a note's tail climbs about five
  semitones in a second and a half.
- **Pitch mix**: how much of the tail goes through the pitch shifter. At 0
  there is no shimmer, whatever Shift says.
- **Decay**: how long the tail rings.
- **More**: **Low decay**, **Crossover** and **Damping** (the tail's low end
  and how it darkens), **Mod depth** and **Mod speed** (a wobble in the
  shift; at depth 0 there is none), and three controls the plugin does not
  explain: **Mode**, **Control** and **Envelope**.

## Things that may surprise you

- **The shimmer is wider on one side.** It is a stereo reverb, and with a
  test sweep its right side comes out about 3 dB louder than its left (about 1 dB
  with the guitar signal). The rig's own checks treat sides more
  than 1 dB apart as a fault, so **a rig file in `config/rigs/` with the
  shimmer in it fails the test suite** (`tests/test_stereo.py`, one of the
  slow tests the CI build runs), even with the shimmer switched off, and
  with it on the sound check (`nulltest`) fails too. Trying it, and saving
  it, are fine: it is the tests that object, once the rig is committed. That
  is one reason the four come as presets and not as a committed rig. Whether
  a stereo reverb should be held to 1 dB is an open question (#165).
- **The tape echo, added from the Library, has no echoes and is quieter.**
  At the plugin's own settings every head is off, Mix is at 0 % and Level at
  41 %, about 5 dB under the guitar. Turn **Head 1** to 1, raise **Mix**,
  and under **More** turn **Level** to 67 %. Its preset already has Head 1
  on, Mix at 40 % and Level at 67 %.

For the files behind this: the presets are `config/channels/tremolo.yaml`,
`flanger.yaml`, `tape-echo.yaml` and `shimmer.yaml`, and the knob layouts are
the profiles under `tool/examples/profiles/` ([Plugins and
portability](../reference/plugins.md)).
