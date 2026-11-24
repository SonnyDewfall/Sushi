# Use a channel from another rig

Every channel of every rig is there to reuse: its effects, each with its
settings and whether it is on, and the channel's volume. You can load one
onto a channel of the rig you are playing, or start a new channel or a whole
new rig from one. Two examples:

- **Guitar and vocal:** the string machine's `strings` on input 1 and a vocal
  sound on input 2, recorded together.
- **Two guitars:** two guitar sounds on two inputs, for two players, or a
  guitar and a bass.

The lists name each channel as `rig / channel`, such as `string-machine /
strings`. To keep a sound for later, save it in a rig (**Save**, or
**Save as…** for a rig of its own, [Saving and going back](saving.md)).

These controls are on the rack only.

## Load a channel onto a channel

1. Save the rig first if you have unsaved changes (see below).
2. Unlock the page: loading changes the rig, so the lock refuses it.
3. In the channel's strip on the left, click **Load…**, beside **Change
   input**. A panel opens in the strip.
4. Under **From**, pick the `rig / channel` to copy.
5. Click **Load**.

The channel's effects and volume are replaced by the ones you picked. Its
input stays the same. A box headed **Loaded … onto …** says whether the rig
changed without stopping ("Played live") or had to restart, and why. Where it
can, the change is made while the rig plays; where it cannot, the sound stops
for a few seconds while the rig restarts.

Loading writes the rig's file, the same way a save does: the rig's file now
holds the copied effects for that channel, with a comment above them naming
where they came from. The other rig is not changed. Nothing is committed to
git.

### Unsaved changes

Loading replaces what is playing with the rig's file, so changes you have not
saved would be lost. The page refuses the load and says so. Either click
**Save** first, or click **Load anyway**, which drops the unsaved changes.
Dropped means gone, on every channel: each knob and switch you changed goes
back to what the rig's file says. If you had added, removed or moved a
plugin, the rig restarts from its file to put that back, and the box says so.

## Start a channel, or a whole rig, from another rig's channel

When you add a channel or make a new rig
([Building a rig](building-a-rig.md)), the **Start from** list has every
channel of every rig, grouped by rig:

1. Pick the channel's **Input** and **Width** as usual.
2. Under **Start from**, pick the `rig / channel`.
3. Click **Add** (or **Create**).

## Things that may surprise you

- **Loaded plugins get the channel's name in front.** Every plugin in a rig
  needs a name of its own, so `string-machine / strings`' `compressor` is
  called `lead_compressor` when it is loaded onto a channel called `lead`.
  **Start from** names them the same way.
- **It is a copy, not a link.** Editing the other rig later changes nothing
  in a rig that used its channel. Load it again to bring the change in.
- **The other rig's MIDI map does not come with it.** Pedals and knobs set
  up in that rig stay there.
- **The amp is not part of a channel.** The amp runs in front of the
  `guitar-in` input only, one for the whole rig. When a guitar channel that
  plays through the amp in its own rig lands where no amp is in front of it
  (a second guitar on another input, or a rig without an amp), the box says
  "… has no amp". The rig cannot yet give each guitar channel an amp of its
  own.
- **The channel keeps its width.** Loading onto an existing channel does not
  change mono to stereo or back: the width is the input's. The box says when
  the channel you picked is the other width.
- **Your pedals follow the plugin.** If a pedal or knob on your MIDI
  controller works a plugin on the channel you load onto, and the new chain
  has the same plugin, the mapping is moved to its new name. If it does not,
  the load is refused and the box lists each mapping, what it did and why it
  would work nothing. **Drop … and load** takes them out of the
  rig's file and loads. Moving or dropping a mapping for a knob or a pedal
  sweep makes the load a restart.
- **Undo does not take a load back.** Undo stops at the load, as it does at a
  rig switch. To go back, use **Revert…** ([Saving and going
  back](saving.md)): the rig's file as it was before the load is kept there.
- **A plugin that is not installed is refused.** If the channel you picked
  uses a plugin missing from `plugins/`, nothing is written and the box says
  which one, and how to add it.
- Loading a channel from another rig has not yet been tried on the rig
  itself, only against a stand-in.

## If something goes wrong

- **A box headed "Not loaded: … onto …".** Nothing was changed. It gives the
  reason: unsaved changes (offered as a second button), MIDI mappings that
  would work nothing (also offered), a rig file that cannot be read, a plugin
  that is not installed, or a plugin name that another channel of the rig
  already uses.
- **The Load button is greyed out.** The page is locked (the line under it
  says so), or nothing is picked.
