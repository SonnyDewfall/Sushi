# Channel presets

A **channel preset** is one channel's sound, kept under a name of its own,
apart from any rig: its effects, each with its settings and whether it is on,
and the channel's volume. You can then put that sound on any channel of any
rig, or start a new channel or a whole new rig from it. Two examples:

- **Guitar and vocal:** your favourite guitar sound on input 1 and a vocal
  sound on input 2, recorded together.
- **Two guitars:** two guitar sounds on two inputs, for two players, or a
  guitar and a bass.

A preset does not say which socket it plays from. You choose that when you
use it.

Each preset is one file, `config/channels/<name>.yaml`, which you can read and
edit by hand like a rig file.

These controls are on the rack only.

## Save a channel as a preset

1. On the rack, in the channel's strip on the left, click **Preset…**, beside
   **Change input**. A panel opens in the strip.
2. Type a name under **Save … as**, such as `crunch`. Names are lowercase
   letters, digits, `-` and `_`. The line under the box tells you the file it
   will write, or what is wrong with the name.
3. Click **Save**, the channel's name, **as** and the preset's name, such as
   **Save lead as crunch**.

A box headed **Saved … as …** confirms the file. The preset is the channel as
it sounds now, including knobs you have turned and plugins you have added or
removed since the last save. Saving a preset does not save the rig: the
number on **Save** stays as it was.

If a preset of that name exists, the box says so and offers **Replace** and
the name. Replacing keeps any comments you wrote in its file.

Saving a preset works while the page is **Locked**.

## Load a preset onto a channel

1. Save the rig first if you have unsaved changes (see below).
2. Unlock the page: loading a preset changes the rig, so the lock refuses it.
3. In the channel's strip, click **Preset…**.
4. Under **Load onto …**, pick the preset.
5. Click **Load**, the preset's name, **onto** and the channel's name, such as
   **Load crunch onto lead**.

The channel's effects and volume are replaced by the preset's. Its input
stays the same. A box headed **Loaded … onto …** says whether the rig changed
without stopping ("Played live, without a restart") or had to restart, and
why. Where it can, the change is made while the rig plays; where it cannot,
the sound stops for a few seconds while the rig restarts.

Loading writes the rig's file, the same way a save does: the rig's file now
holds the preset's effects for that channel, with a comment above them naming
the preset. Nothing is committed to git.

### Unsaved changes

Loading replaces what is playing with the rig's file, so changes you have not
saved would be lost. The page refuses the load and says so. Either click
**Save** first, or click **Load anyway, dropping the unsaved changes**.
Dropped means gone, on every channel: each knob and switch you changed goes
back to what the rig's file says. If you had added, removed or moved a
plugin, the rig restarts from its file to put that back, and the box says so.

## Start a channel, or a whole rig, from a preset

When you add a channel or make a new rig
([Building a rig](building-a-rig.md)), the **Start from** list has a group
called **Channel presets** at the top:

1. Pick the channel's **Input** as usual.
2. Under **Start from**, pick a preset (each is listed as `preset` and its
   name, with how many plugins it has).
3. The **Width** changes to the one the preset was saved with. If the input
   cannot give that width, the line under the channel says why; choose the
   other width.
4. Click **Add** (or **Create**).

You can start two channels from the same preset, in one rig or in two: each
channel gets its own copy.

## Things that may surprise you

- **The plugins get the channel's name in front.** Every plugin in a rig needs
  a name of its own, so a preset's `compressor` is called `lead_compressor` on
  a channel called `lead`, and `vocal_compressor` on `vocal`. That is how two
  channels can play the same preset. The names in the preset's own file have
  no channel name.
- **Loading copies the preset, it does not link to it.** Editing the preset
  later changes nothing in a rig that used it. Load it again to bring the
  change in.
- **The amp is not part of a preset.** The amp runs in front of the
  `guitar-in` input only, one for the whole rig. A preset saved from a guitar
  channel with the amp on remembers that it was played through the amp, and
  when you load it onto a channel with no amp in front of it (a second guitar
  on another input, or a rig without an amp), the box says "… has no amp".
  The rig cannot yet give each guitar channel an amp of its own.
- **The channel keeps its width.** Loading onto an existing channel does not
  change mono to stereo or back: the width is the input's. The box says when
  the preset was saved from a channel of the other width.
- **Pedals follow the plugin.** If a pedal or knob on your MIDI controller
  works a plugin on that channel, and the preset has the same plugin, the
  mapping is moved to its new name. If the preset does not have it, the load
  is refused and the box lists each mapping, what it did and why it would
  work nothing. **Load, dropping … MIDI mapping** takes them out of the rig's
  file and loads. Moving or dropping a mapping for a knob or a pedal sweep
  makes the load a restart.
- **Undo does not take a load back.** Undo stops at the load, as it does at a
  rig switch. To go back, use **Revert…** ([Saving and going
  back](saving.md)): the rig's file as it was before the load is kept there.
- **Nothing is committed to git**, neither the preset nor the rig's file. The
  boxes say so.
- Saving and loading presets has not yet been tried on the rig itself, only
  against a stand-in.

## If something goes wrong

- **A box headed "Not saved: … as …".** The reason is in the box. A name
  another preset has is offered as **Replace**.
- **A box headed "Not loaded: … onto …".** Nothing was changed. It gives the
  reason: unsaved changes (offered as a second button), MIDI mappings that
  would work nothing (also offered), a preset file that cannot be read, or a
  plugin name that another channel of the rig already uses.
- **The Load button is greyed out.** The page is locked (the line under it
  says so), or no preset is chosen.
- **A preset is listed as "cannot be loaded".** Its file has a mistake, or
  names a plugin the rig does not know. Hover over it to see why, and fix the
  file in `config/channels/`.
