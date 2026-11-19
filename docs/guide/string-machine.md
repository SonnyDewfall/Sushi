# String machine and synth lead

Two rigs for playing with synth-like sounds from the guitar, made from the
research of #166:

- **string-machine**: play chords, picked, and each one comes out as a
  string ensemble, the swirling sound of a 1970s string machine, with an
  octave up underneath and a long hall around it.
- **synth-lead**: single notes through a fuzz and an envelope filter into a
  tape echo, for a synth lead.

These are places to start, not finished sounds. Nothing here has been heard on
the rig yet. Every figure below comes from renders on a computer, through the
same engine the rig runs, with a made-up guitar signal, and every setting is a
guess to turn from.

To start one: on the rack, under **RIGS**, pick **string-machine** or
**synth-lead** and click **Start** or **Switch to**. They need a plugin
built, and five installed, on the rig machine first (see the reference's
[Plugins](../reference/plugins.md#string-machine-chorus-built-for-the-string-machine)).

## string-machine

The guitar goes to two channels at once. You see them as two strips on the
left and two tabs over the chain:

- **strings** is your guitar: a compressor, a **swell** (off), the
  **ensemble** and a **hall**.
- **octave** is the same guitar an octave up, with nothing of the original
  note left, into a swell (off), an ensemble and a hall of its own. It sits
  3 dB under **strings**, and arrives about 67 ms after you pick: you hear the
  pick on **strings** first, and the octave bloom in behind it.

Chords work: the ensemble treats a chord exactly as it treats one note, and the
octave up stays clean on chords (#166). The pick comes through as it does dry,
because the swells are off.

### What to try

- **How loud the octave is**: the **octave** strip's volume. Pull it down for
  plain strings; push it up for an organ-like stack, or mute it to hear the
  ensemble on your guitar alone.
- **The ensemble's Depth** (on **ensemble** and **octave_ensemble**): how far
  it swirls. 100 % is the string machine; lower is a gentler chorus; 0 %
  stops the swirl.
- **The ensemble's Mix**: 100 % is the ensemble alone, as the rig starts.
  Turn it down to bring the plain guitar in under it, up to as loud as the
  ensemble at 0 %: the attack of each pick should stand out more.
- **The hall's Size** (on **hall** and **octave_hall**): how long it rings,
  1 to 8 s. It starts at 5 s (57 %).
- **The hall's Mix**: 0 % is no hall, 50 % the guitar and the hall equal,
  100 % the hall alone, with none of the guitar's own sound in it. It
  starts at 35 %.
- **Swells**: click the round button on **swell** and **octave_swell** to
  switch them on. A note played after a gap then fades in over about half a
  second, like a bowed string. A note played while the last one still rings
  is not swelled: the gate is already open. **Swell** sets how slowly it
  opens, and **Threshold** how quiet the guitar has to get before the next
  note fades in again. If notes cut off early, lower the threshold.
- **More** on the ensemble: **Level**, its own level from -24 to 0 dB (88 %,
  -3 dB, in the rig); **Model**, which switches its delay to a plain digital
  one at a fifth of the CPU; and the **Fast** and **Slow** rates and depths,
  which shape the swirl itself.

The two channels' plugins have the same names but for `octave_` in front, and
each has its own knobs: turn both to keep the two halves alike, or set them
apart on purpose (a longer hall on the octave, say).

Each channel is also a preset, **string-ensemble** and **string-octave**, to
put on any rig ([Channel presets](channel-presets.md)). The octave preset is
meant beside a channel of the plain guitar on the same input.

## synth-lead

One channel: a **compressor**, a **fuzz** (GxBigMuffPi), an envelope **filter**
(Calf's Envelope Filter) and an **echo** (GxEchoCat, the tape echo of
[the pedals](effects.md)). Pick single notes: a fuzz turns a chord into a
buzzing mess, which can be fun, but it is not a chord any more. Its attack
is the pick's: the renders put it under 10 ms behind the guitar.

### What to try

- **Fuzz**: **Sustain** is how much fuzz, **Tone** dark to bright, and
  **Volume** how loud: at 0 it is silent.
- **Filter**: it opens with each pick and closes as the note dies, the
  "wow".
  - **Sensitivity**: how far your playing opens it. Higher opens it further
    on softer playing. 45 % in the rig.
  - **Lower** and **Upper**: where the sweep starts and ends, in Hz. 300 to
    2,500 in the rig; try a higher Upper for a brighter top.
  - **Resonance**: the peak on the sweep. Higher is more vocal and more
    synth-like. 50 % in the rig.
  - **Mode**: the filter's shape. The rig has a 24 dB low-pass, the classic
    synth filter. A band-pass (#166's choice, and the plugin's own) is more
    vocal, but much quieter on the low strings: in the renders a low E came
    out 29 dB under a high E, against 6 dB with the low-pass.
  - **Level**: its output, at 70 % in the rig. At 100 % the lead is about as
    loud as the guitar across the neck (4 dB over it on the renders' guitar).
    For more than that, after switching to a band-pass say, raise the fuzz's
    **Volume** or the channel's fader instead: the filter's own gain knobs
    reach 64 times, and are not on the card. A filter added from the
    Library starts past Level's top, at the plugin's own setting, about
    10 dB louder: turn Level down to bring it onto the knob.
  - Under **More**: **Attack** and **Release**, how fast it opens and closes;
    **Response**, the sweep's curve; and **Mix**, the filter against the
    plain fuzz.
- **Echo**: **Tempo** and the heads set the echo's timing, **Mix** how loud
  the echoes are and **Repeats** how many come back (see
  [the tape echo](effects.md)).

The channel is also the preset **synth-lead**.
