# String machine and synth lead

A rig for playing with synth-like sounds from the guitar, made from the
research of #166:

- **string-machine**: play chords, picked, and each one comes out as a
  string ensemble, the swirling sound of a 1970s string machine, with an
  octave up underneath and a long hall around it.

These are places to start, not finished sounds. Nothing here has been heard on
the rig yet. Every figure below comes from renders on a computer, through the
same engine the rig runs, with a made-up guitar signal, and every setting is a
guess to turn from.

To start one: on the rack, under **RIGS**, pick **string-machine** and click
**Start** or **Switch to**. It needs a plugin built on the rig machine first (see the reference's
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
  it swirls. 100 % is the string machine; lower is a gentler chorus; 0 % is a
  still, slightly hollow copy.
- **The ensemble's Mix**: 100 % is the ensemble alone, as the rig starts.
  Turn it down to bring the plain guitar in under it, up to as loud as the
  ensemble at 0 %. The pick gets more defined; the strings less so.
- **The hall's Size** (on **hall** and **octave_hall**): how long it rings,
  1 to 8 s. It starts at 5 s (57 %).
- **The hall's Mix**: 0 % is no hall, 50 % the guitar and the hall equal,
  100 % the hall alone, a wash with no attack at all. It starts at 35 %.
- **Swells**: click the round button on **swell** and **octave_swell** to
  switch them on. A note played after a gap then fades in over about half a
  second, like a bowed string. A note played while the last one still rings
  is not swelled: the gate is already open. **Swell** sets how slowly it
  opens, and **Threshold** how quiet the guitar has to get before the next
  note fades in again. If notes cut off early, lower the threshold.
- **More** on the ensemble: **Model** switches its delay to a plain digital
  one, at a fifth of the CPU, and the **Fast** and **Slow** rates and depths
  shape the swirl itself.

The two channels' plugins have the same names but for `octave_` in front, and
each has its own knobs: turn both to keep the two halves alike, or set them
apart on purpose (a longer hall on the octave, say).

Each channel is also a preset, **string-ensemble** and **string-octave**, to
put on any rig ([Channel presets](channel-presets.md)). The octave preset is
meant beside a channel of the plain guitar on the same input.
