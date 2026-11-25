# Shaping a sound

This is how you change what the rig sounds like: pick a channel, open a plugin,
turn its knobs, switch it off and on, add a plugin or take one out, change its
place in the chain, set the amp, and take a change back. All of it is on the
**rack**, the main page.

A **plugin** is one effect: a compressor, an overdrive, a delay. A channel's
plugins sit in a row, in the order the sound passes through them; that row is
its **chain**. The page calls a plugin's box a "unit".

![The middle of the rack: three tabs, board, vocal and Master, with board open; under them the amp's card with its model, Input Lvl and Output Lvl faders and Model list, then the board's chain: a dashed Add at the start button, the overdrive opened to show all its knobs in groups and, at its foot, Remove overdrive (locked), a dashed Add after overdrive button, the bypassed octave as a slim row with only its name and its round button, Add after octave, and the delay](img/shaping-a-sound.png)

Every change is heard at once. None of it is kept until you save: see
[Saving and going back](saving.md).

## Pick the channel to work on

The middle of the rack shows one channel's chain at a time. Above it is a row
of tabs: one for each channel, named as the channel is, then **Master**, the
chain the finished mix passes through. **Master** ends with a line about the
rig's limiter, which is always last: anything you add to the master goes in
front of it (see [Mixing](mixing.md#what-each-one-changes)).

- Click a tab to see that channel's plugins. Nothing from another channel is
  on the screen with them.
- Or click a channel's name in its strip on the left: it opens the same tab.
  The strip called **master** opens **Master**. The strip of the chain you are
  looking at has an amber edge.
- With the keyboard: click a tab, then use the left and right arrow keys, or
  Home and End.

The strips on the left stay where they are whichever tab is open, so every
volume is always in reach. Moving a fader does not change the tab.

Changing tab changes only what you see: every channel keeps playing, and it
works while the page is **Locked**. The page remembers the tab you were on, on
that device, so a reload comes back to it. When you switch to another rig it
opens on the first channel.

A rig with one channel still has two tabs: the channel's and **Master**.

## Open a plugin and turn a knob

1. Open the plugin's channel's tab. Each row shows the plugin's name, a few of
   its knobs and, on the right, a round bypass button.
2. Click the plugin's name. The row opens to show all its controls, in groups.
   Click the name again to close it.
3. Change a knob in any of these ways:
   - **Drag** it up or down. Hold Shift, or put a second finger down, to move
     ten times more finely.
   - **Type**: click the number beside the knob, type a value and press Enter.
     Escape puts the old value back.
   - **Scroll** with the wheel or trackpad, after resting the pointer on the
     knob for a moment.
   - **Keys**: click the knob, then use the arrow keys for a step, Page Up and
     Page Down for ten, Home and End for the ends.

Some controls are switches, which you click, and some are lists, which you pick
from.

Values are shown in each plugin's own range. Each reverb has a **Mix**: how
much reverb you hear against the dry guitar. The room's runs from 0 (dry) to 1
(only the room). The hall's runs from 0 % to 100 % and moves three of the
plugin's own levels together; **Undo** takes a turn of it back as it does any
knob's. Even at 100 % the hall keeps a smeared copy of the guitar, which the
plugin has no control for. A hall saved before Mix existed may not be on its
curve: Mix shows the nearest setting, nothing changes until you turn it, and
then each level moves on from where it was, reaching the curve's own levels
at 0 % and 100 %. The shimmer reverb and the tape echo have a Mix too, of
their own kind: see [Tremolo, flanger, tape echo and shimmer](effects.md).

A plugin
that says **no profile: every parameter** under its name has not been tidied up
for the page yet: it shows every control it has, in a plain list.

## Switch a plugin off and on

Click the round button at the right of the plugin's row. This is **bypass**: it
switches the effect off without removing it. The button glows when the effect is
on.

A bypassed plugin shrinks to a slim, dimmed row: its name, its **mono** mark if
it has one, and the round button. It keeps its place in the chain, so the
order still reads as the path the sound takes. Switch it on and it opens again.

The same happens whoever does the switching: a pedal, or another phone or
laptop. Within a second the row shrinks or opens on every device.

### Set up a plugin without switching it on

Click a slim row's name, or anywhere on the row except its round button. It
opens with all its controls, still bypassed and still dimmed, so you can set it
before you bring it in. Click the name again to shrink it. It also shrinks when
you change tab.

This works while the page is **Locked**.

If a pedal bypasses a plugin while you are dragging one of its knobs or typing
a value into it, its row stays open under your hand. Click its name when you
are done.

## Add a plugin

Between the plugins of a chain are dashed buttons: **Add at the start…** above
the first plugin, and under each plugin one that names it, such as
**Add after overdrive…**. Pick the one where the new plugin should go.

1. Unlock the page: click **Locked**, then **Tap again to unlock**. (See
   [Playing live](playing-live.md#the-lock).)
2. Click **Add at the start…**, or **Add after** and the plugin it should
   follow. The **LIBRARY** opens over the page, saying where the plugin will
   go, for example **Add after overdrive on board**.
3. Find the plugin. The library is in groups by what each plugin does:
   **Drive**, **Mod**, **Delay**, **Reverb**, **Dynamics**, **EQ**, and
   **Other** for the rest. Or type into the box at the top
   (**Search by name or kind**): a word from the plugin's name, or a group's
   name such as "reverb".
4. Click the plugin. The library closes, the button you started from reads
   **Adding** and the plugin's name for a moment, and then the plugin is in
   the chain, switched on, at its own starting settings. You hear it at once.

![The LIBRARY, opened from the button after the delay: its title Add after delay on board, the Close button and the search box, then the plugins in groups, each with its name and its folder: under Drive, GxTubeScreamer with its mono mark and an amber line saying "after delay: this collapses its stereo width"; GxChorus-Stereo under Mod, MDA Delay under Delay, MVerb under Reverb; under Dynamics the x42 compressor's mono version, with the same amber line, and its stereo one; and EQ](img/library.png)

Each plugin in the library shows its name, and under it the folder it comes
from on the rig. A **mono** mark means it works on one side only, as on the
rack. If adding it where you chose would flatten a stereo plugin's width, an
amber line under it says so before you add it, for example
"after delay: this collapses its stereo width". Put it earlier in the chain to
avoid that.

The page gives the new plugin a name of its own, from its name in the library:
"MDA Delay" becomes **mda_delay**, and a second one **mda_delay_2**.

To close the library without adding anything, click **Close**, press Escape, or
click outside it.

While the page is **Locked** you can still open the library and look through
it, but every plugin in it is greyed out, and a line at the top says the lock
is on. With no rig running, the middle of the rack has
**Browse the library…** instead, to look through it the same way.

## Take a plugin out

1. Unlock the page.
2. Click the plugin's name to open its row. (A slim, bypassed row opens the
   same way.)
3. At the foot of the row, click **Remove**. It goes at once, with no
   question. (While the page is locked it reads **Remove (locked)**, and
   clicking it only says **Locked**.)

The plugin and its settings leave the chain. **Undo** puts it back where it
was, with the settings it had.

## Move a plugin along its chain

1. Unlock the page.
2. Click **▲** or **▼** at the left of the plugin's row to move it one place
   earlier or later.

While the page is locked those two buttons show a padlock. A slim, bypassed row
does not show them: open it first.

A plugin with the **mono** mark has a hidden partner that copies its sound to
both sides. It always moves with it, and is added and taken out with it, so
you never see it.

## The short dip when the chain changes

When you add, take out or move a plugin, the whole rig goes quiet for a
moment, a few hundredths of a second, and comes back at the level it was.
Changing a chain while it plays can make a pop; the dip hides it. Switching to
another rig does the same once, while its plugins change.

- Knobs, bypass and the volumes never dip.
- The **MASTER** fader does not move during the dip, and a dip never counts as
  a change: Undo and Save do not see it.
- If you move **MASTER** during a dip, your new level is the one you end up
  with.

The dip has been tried only against a stand-in for the rig, not heard on the
rig itself.

## When the rig is too busy to add a plugin

Before a plugin goes in, the rig checks how hard the computer is working, the
figure the stage screen shows as **LOAD**. Above 80 %, one more plugin could
make the sound drop out, so the add is refused, and the line beside the Add
button says how busy the rig is. Switch off or take out a plugin you are not
using, then add again. If the load cannot be read, the plugin is added
anyway.

## Set the amp

The **AMP** card is at the top of one tab: the channel the guitar plays
through, because the amp comes before that channel's effects. It is not on the
other tabs. If the rig has no amp, the card says so. If you change that
channel's input, the card stays where it is until the rig restarts and plays
the change.

- **Input Lvl** and **Output Lvl** are faders, in dB. Drag them, or type a
  value in the box beside them.
- **Quality** is shown but cannot be changed here. It trades computer effort
  for accuracy, not tone.
- **Model** changes which amp is playing:
  1. Unlock the page.
  2. Pick a model from the **Model** list.
  3. The card asks **Restart amp?**: click **Yes**, or **No**.

  The sound cuts out for a moment while the amp restarts (a little over a second
  on the rig); the rest of the rig keeps running. In the list, **(playing)**
  marks the current model and **(cannot be played here)** marks one made for a
  different sample rate.

The values are what the page last sent the amp: it cannot be asked what it is
set to.

If the card says **Amp not wired**, the amp is running but the guitar is not
going through it, so you hear it dry. Hover over the words to see which link
is missing. Restart the rig; if it still says so, check the patchbay and the
machine file. The same words, or **No amp** when the amp did not start, show
at the top of the page after a start, a restart or a switch of rig. Hover over
them for what the rig said.

If the rig was started without its amp, the card says so and offers **Amp
on**:

1. Unlock the page.
2. Click **Amp on**.
3. The card asks **Restart?**: click **Yes**, or **No**.

The whole rig restarts, so the sound cuts out for a few seconds. Switching to
another rig also brings the amp back if that rig has one. Going back to a
saved version, loading a channel or Undo keep the amp off.

Amp on, and the **Amp not wired** check, have not yet been tried on the rig
itself, only against a stand-in.

On the stage screen, with the lock off, you get the amp's model name and
**Output Lvl** only.

## Take a change back

**Undo** is at the top of the rack and of the stage screen, beside the lock.
Hover over it (or long-press on a phone) to see what it would take back, for
example **Undo overdrive Drive**. Click it to put that change back; beside it
the page says **Undone:** and the change. Click again for the one before, up to
50 changes back.

- It takes back knobs, bypasses, moves, plugins added or taken out, volumes and
  the amp's levels, newest first. One drag of a knob or fader is one change. A
  plugin added goes again; a plugin taken out comes back where it was, with
  its settings.
- It also takes back a change of the amp's model. That restarts the amp, so the
  sound cuts out for a moment, just as the change did.
- On the rack it takes back the rest too: a rig deleted, a channel added, an
  input changed, a pedal assigned, a **Save** (the file goes back to how it
  was, and the changes count as unsaved again) and a **Revert**. Where the rig
  plays that change only after a restart, the page says **restart** and
  offers **Restart**.
- On the stage screen it takes back only what you play with (knobs, faders,
  bypasses): the rest waits for the rack.
- While the page is **Locked**, it does not take back what the lock stops you
  doing: adding, removing or moving a plugin, the amp's model, a pedal.
- It remembers until the agent restarts, so reloading the page keeps it, and
  your phone and laptop share it.
- There is no redo. A **Save as** cannot be taken back: delete the new rig
  instead.
- If something cannot be taken back (it has changed since, or a rig of that
  name is there again), hovering over **Undo** says **Can't undo** and why.
  Clicking it then gives up on that change, and the next click goes on to the
  one before.

Undo has not yet been tried on the rig itself, only against a stand-in.

Adding, taking out and moving plugins count as unsaved changes on **Save**,
the same as a knob: see [Saving and going back](saving.md).

## Small things you will notice

- A small **mono** mark beside a plugin's name means the plugin works on one
  side only and its output is copied to both. If the mark is amber, the plugin
  sits after a stereo one and flattens its width; hover over it to see which.
  Putting mono plugins before stereo ones avoids that.
- A greyed knob marked **read-only** is something the plugin reports, not
  something you can set.

## If something goes wrong

- **A knob shows "refused:" under it, and a box appears starting
  "Not changed:".** The agent would not make the change; the reason is in the
  box and in the knob's tooltip.
- **A red dot on a tab.** Something behind that tab needs a look: a change
  there was refused, or something is wrong with the amp. Hover over the tab to read what,
  or open it. The boxes at the top of the page are above the tabs and show
  whichever tab is open.
- **A plugin you expected is not on the screen.** It is on another channel's
  tab, or it is bypassed and so a slim row: look for its name.
- **A box headed "Not moved:" and the plugin's name, with a reason starting
  "Locked:".** You tried to move a plugin while the page is locked. Unlock it
  and try again.
- **A red line starting "Not added:" beside an Add button.** The rig would not
  take that plugin; the line says why. Click **×** to clear it. If it says
  "the rig is using" and a percentage, the rig is too busy for one more: see
  [When the rig is too busy to add a plugin](#when-the-rig-is-too-busy-to-add-a-plugin).
- **The sound went away after a plugin was added, taken out or moved, and
  MASTER reads −∞.** The dip could not put the level back. Set **MASTER**
  again.
- **A line starting "Not removed:", or "Locked:", at the foot of a row.** The
  plugin was not taken out; the line says why. If it says "Locked:", unlock
  the page and try again.
- **The library says a plugin "is no longer on" the chain.** Someone took out,
  from another phone or laptop, the plugin you were adding after. Close the
  library and choose another place.
- **The library says "The rig changed since the library was opened".** The rig
  was switched or restarted, here or from another device, while it was open.
  Close it and open it again from the chain: it adds only to the rig it was
  opened on.
- **A plugin in the library is greyed out with "Cannot be added:" under it.**
  It cannot go in a chain at all, for the reason given: the amp, for one, has
  its own card.
- **The Model list is greyed out, with a line starting "Locked:" under it.**
  The same: unlock the page to change the amp's model.
- **A box starts "Amp model not changed:".** The model did not load. The box
  says whether the previous model was brought back; if it was not, see
  [When something's wrong](troubleshooting.md#a-part-of-the-rig-has-stopped).
- **Everything in the middle is replaced by "No rig to show".** No rig is
  running. Start one: see [Getting started](getting-started.md).
