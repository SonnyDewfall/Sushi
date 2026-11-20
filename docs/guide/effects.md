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

## Before the first time

The four come from Ubuntu's `guitarix-lv2` package, and the rig plays only
the plugins in its own plugins folder. If that folder was made before the
pedals arrived, copy them in once, in a terminal on the rig machine:

```bash
sudo apt install guitarix-lv2
cd ~/Sushi
sushi-rig plugins add
```

`plugins add` copies whatever the rigs need and the folder lacks, from
wherever Ubuntu installed it, and leaves the rest alone. It needs every
plugin any rig uses, so if it stops saying the neural amp
(`http://github.com/mikeoliphant/neural-amp-modeler-lv2`) has no provider,
because the amp is carried into the folder by hand and is not there yet, run
it as `sushi-rig plugins add --waive http://github.com/mikeoliphant/neural-amp-modeler-lv2`. Until then, starting
or switching to a rig with a pedal in it is refused: the box says which
plugin is missing and what to run, and whatever was playing keeps playing. (The
detail is in [Plugins and portability](../reference/plugins.md).)

## Try one

The quickest way to hear all four: under **RIGS**, pick the rig **pedals**
and click **Start** or **Switch to**. It has the four in a row on the guitar,
after the amp: the tape echo and the shimmer on, the tremolo and the flanger
off. Click a pedal's round button to bring it in or take it out.

To hear one on its own, make a rig of its own, started from the pedal's
preset:

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
  the whole sound gets a little louder as the echoes come up.
- **Repeats**: how many times each echo comes back. It stops short of where
  the echoes would ring on by themselves: with all three heads on, the
  plugin's own knob a third further on keeps going for twenty seconds and
  more after you stop playing.
- **Tempo**: the echo time, as a tempo in BPM. It does not follow any clock.
- **Head 1**, **Head 2**, **Head 3**: each is 0 (off) or 1 (on). Head 1 echoes
  an eighth note later (250 ms at 120 BPM), Head 2 a quarter note and Head 3
  a dotted quarter. With every head at 0 there is no echo at all.
- **More**: **Level**, the echo's output: from about 12 dB quieter than the
  guitar (0 %) to as loud as it (100 %).

**How loud it can get.** Every knob the page shows is bounded so that, with
all three heads on and Mix, Repeats and Level at 100 %, the echo is at most
4 dB louder than the guitar with the echo switched off, and its peaks at
most 12 dB higher (measured on a test sweep: +3.1 dB and +10.9 dB). The
plugin's own **Output** and **Input** knobs are not shown, and nothing on
the page goes past them: Output reaches about 78 dB louder at its top, and
Input raises the guitar and the echoes together (about 12 dB at its top).
Those limits are the page's only. A preset or rig file typed by hand can
still set them to anything, and nothing stops it yet.

**Shimmer** (Gxshimmizita): a reverb whose tail rises (or falls) in pitch.

- **Mix**: how much reverb against the dry guitar. 100 % is not "all
  reverb": the reverb is much louder than the guitar, so Mix stops where the
  whole sound is at most 6 dB louder than the guitar alone, whatever the
  other knobs say (peaks at most 17 dB higher; measured on a test sweep,
  with both decays at their longest: +5.9 dB and +16.2 dB). At the plugin's
  own decays, Mix 100 % is about 3.5 dB louder. The preset has Mix at
  100 %. It does not run away: however long the decay or high the Pitch
  mix, the tail dies away once you stop playing.
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

- **The shimmer is louder on one side.** It is a stereo reverb, and with a
  test sweep its right side comes out about 3 dB louder than its left
  (about 1 dB with the guitar signal). The rig's own checks allow a stereo
  reverb's two sides to be up to 4 dB apart, and every other plugin 1 dB, so
  a rig you save and commit with the shimmer in it passes them: anywhere on
  its Mix the sides measured at most 3.8 dB apart. One way past it: a
  shimmer added from the Library starts beyond Mix (below), and turned part
  way down from there, with both decays at their shortest, its sides
  measured 4.1 dB apart; a committed rig saved like that fails the check,
  which says so. A side that goes silent still fails, reverb or not.
- **The tape echo, added from the Library, has no echoes and is quieter.**
  At the plugin's own settings every head is off, Mix and Repeats are at
  0 % and Level at 62 %, about 5 dB under the guitar. Turn **Head 1** to 1,
  raise **Mix** and **Repeats**, and under **More** turn **Level** to 100 %.
  Its preset already has Head 1 on, Mix at 50 %, Repeats and Level at 100 %.
- **The shimmer, added from the Library, is loud.** It starts at the
  plugin's own settings, which are wetter than Mix's 100 %: about 11 dB
  louder than the guitar. Mix shows 100 % with a note that it is off its
  curve. Turn Mix down first: from there it comes down to the dry guitar,
  and once it is back on its curve it stays inside the 6 dB. Or start from
  its preset, which has Mix at 100 %. (The Library cannot yet set a plugin's
  starting values; that is why.)

For the files behind this: the rig is `config/rigs/pedals.yaml`, with its
sound reference `config/sound/pedals.yaml`; the presets are
`config/channels/tremolo.yaml`, `flanger.yaml`, `tape-echo.yaml` and
`shimmer.yaml`; and the knob layouts are
the profiles under `tool/examples/profiles/` ([Plugins and
portability](../reference/plugins.md)).
