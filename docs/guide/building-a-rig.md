# Building a rig

This is how you make a new rig, add a channel to the one that is playing,
change which input a channel listens to, remove a channel, and delete a rig
you no longer play.

A **rig** is one saved setup. A **channel** is one input with its own effects: a
guitar, a microphone. A rig has between one and three channels, and they all mix
into one **master** that goes to the monitors.

These screens are on the rack only, and work whether or not the page is
locked, except **Remove**, which needs the page unlocked.

## Make a new rig

1. On the rack, under **RIGS** on the right, click **New rig…**. The
   **New rig** screen opens.
2. Type a name under **Rig name**, such as `two-guitars`. Names are lowercase
   letters, digits, `-` and `_`. The line under the box tells you the file it
   will write, or what is wrong with the name.
3. Fill in **Channel 1**:
   - **Channel 1 name**: what to call it, such as `guitar`. This name is also
     the name of its file in a recording.
   - **Input**: where it listens. Each choice is an input's name and the socket
     behind it, such as `guitar-in` or `vocal-in`.
   - **Width**: **mono** for one socket, **stereo** for a pair. If an input
     cannot give one of them, that choice is greyed out and a line under the
     channel says why.
   - **Start from**: leave it at **Nothing: an empty chain** for a channel with
     no effects, or pick a channel of another rig, listed as `rig / channel`,
     to copy its effects and its volume
     ([Use a channel from another rig](channel-from-another-rig.md)).
4. For another channel, click **Add channel** and fill it in the same way.
   Each channel then has a button to take it out again, **Remove channel 1**,
   **Remove channel 2** and so on. The count beside **CHANNELS** shows
   how many you have, of at most three.
5. Click **Create** and the rig's name.

A box headed **Made** confirms the file, and offers a button to play it:
**Start** and its name if nothing is running, **Switch to** and its name if a
rig is. **Back to the rack**, at the top right, leaves the screen.

![The New rig screen: the rig name two-guitars, then two channels, each with a name, an Input, a Width and a Start from list, the second copying another rig's chain](img/new-rig.png)

## Add a channel to the rig that is playing

1. Save first if you have unsaved changes: the rig has to restart to play a new
   channel, and the page will refuse the edit until you have saved.
2. On the rack, at the bottom of the left column, click **Add channel**.
3. Fill in **New channel name**, **Input**, **Width** and **Start from**, as
   above.
4. Click **Add** and the channel's name.
5. **Added**, in the bar at the top, confirms it. The channel is now in the rig's file but
   not yet in the sound. Click **Restart** beside it; the bar asks **Restart?**: click
   **Yes**. The sound stops for a few seconds while the rig comes back. **No**
   puts it off; the channel then plays the next time the rig starts.
6. Changed your mind? **Undo**, at the top of the page, takes the channel out
   of the file again.

## Change a channel's input

1. Save first, as above.
2. On the rack, in the channel's strip on the left, click **Change input**.
3. Pick the new **Input**, and the **Width** if that changes too.
4. Click **Apply**, or **Cancel**.
5. Click **Restart**, then **Yes**. **Undo** puts the old input back.

## Remove a channel

1. Unlock the page (the **Lock** button at the top): taking a channel out
   changes the rig, so the lock holds it, as it holds **Load…**.
2. On the rack, in the channel's strip on the left, click **Remove**.
3. The bar at the top asks **Remove?**, and the strip gets a red edge. A line
   in the strip says what goes: the channel,
   how many plugins its chain has and, if any, how many pedal assignments
   name it (**MIDI**). Hover over the line for each one by name.
4. Click **Yes**, or **No** to keep it.
5. The rig has to restart to lose the channel, so the bar asks
   **Restart?** first, and nothing has changed yet. **Yes** removes it; the
   sound stops for a few seconds while the rig comes back. A channel you
   added and have not yet restarted to play is simply taken out of the file,
   with no restart.
6. The strip goes, and **Removed** and the channel's name are shown in the bar
   at the top. **Undo**, at its right end, puts the channel back, with its
   chain and its pedals; that needs a restart, which it asks about.

If a pedal is assigned to the channel (an effect's on/off or a knob on it, its
on/off, its volume, or a blend of it and another channel), the bar says
**Not removed** and names the pedal controls: click **Drop** and the number
**and remove** to take them out of the rig with the channel. With unsaved
changes it offers **Remove anyway**, which drops them. A rig's only channel
cannot be removed: the strip says **Last channel**. If the channel has gone
from the file but the rig could not be made to play it, the bar says
**Not switched**: hover over it for why, then restart the rig.

## Delete a rig

1. On the rack, under **RIGS** on the right, click **Delete** beside the rig.
   The rig that is playing has its **Delete** greyed out; hover over it for
   why: switch to another rig, or stop it, first.

The rig goes at once, with no question: **Undo**, at the top of the page,
puts it back. Beside it the bar says **Undone: delete** and the rig's name.
Undo is there after a reload too, and on any other page of the same agent,
until the agent restarts.

The file is not gone: it is moved into `config/rigs/.deleted/`, a folder the
page, the pedal numbers and git all leave alone. If the rig was committed to
git, `git checkout HEAD -- config/rigs/<name>.yaml` brings it back too.

### Put a deleted rig back by hand

Each deleted rig is one file in `config/rigs/.deleted/`, named after the rig
and the moment it was deleted, in UTC: `clean` deleted at 10:15:02 on 16
November 2026 is `clean-20261116T101502Z.yaml`. A rig deleted twice in the same
second gets `-2`, `-3` before `.yaml`. To put one back, in the checkout on the
rig's laptop:

```sh
ls config/rigs/.deleted/
mv config/rigs/.deleted/clean-20261116T101502Z.yaml config/rigs/clean.yaml
```

Check first that there is no `config/rigs/clean.yaml` already: `mv` would
replace it. Reload the page to see the rig in the picker again. Leave the
folder's `.gitignore` where it is.

## Things that may surprise you

- **Adding a channel or changing an input needs the rig to restart**, which is
  a few seconds of silence. Nothing changes in the sound until then.
  So does **removing** a channel, and putting it back with **Undo**.
- **A new channel with an empty chain has no effects.** Add them on the rack
  once the rig is running ([Shaping a sound](shaping-a-sound.md#add-a-plugin)),
  or use **Start from** to copy another channel's chain.
- **Two channels in one rig cannot both be copied from the same rig's
  channel.** Every plugin in a rig needs a name of its own, and a second copy
  would repeat the first's names. The page refuses it and says so. **Load…**
  on the rack names the plugins for the channel they land on instead
  ([Use a channel from another rig](channel-from-another-rig.md)).
- **Three channels is the most a rig can have.** Asking for a fourth is refused
  with the reason.
- **The inputs on offer come from the machine's own file**, which lists the
  sockets on this laptop's interface. The page never changes that file. If a
  rig needs an input it does not list, the page shows the lines to add to it by
  hand ([The machine file](../reference/machine-file.md)).
- **Nothing here is committed to git.** Each box tells you what git thinks of
  the file; the commit is yours to make. Pushing a new rig leaves the checks
  green, with a warning and a sound reference for it to commit
  ([Saving](saving.md#when-you-push-a-saved-rig)).
- **A new rig can move the other rigs' pedal numbers.** If you switch rigs by
  program change from a pedal, the rigs are numbered in name order, so a new
  name can shift the ones after it. The **Made** box shows a table of which
  moved.
- **Deleting a rig moves the pedal numbers of the rigs after it** down by one,
  as a new rig moves them up. **Undo** moves them back.
- **Undo cannot put a rig back if a rig of that name has been made since**, so
  a new file is never replaced: hover over **Undo** and it says **Can't
  undo**, and why. The deleted file stays in `config/rigs/.deleted/`, to move
  back by hand if you want it
  ([Put a deleted rig back by hand](#put-a-deleted-rig-back-by-hand)).
- **`git clean -x` empties `config/rigs/.deleted/`**, because git is told to
  ignore it. A deleted rig that was never committed is then gone for good.
- **Undo goes back 50 steps, until the agent restarts.** After that the file
  is still in `config/rigs/.deleted/` for anyone to move back.
- The page has no way to rename a rig. A rig is one file in the project's
  `config/rigs/` folder.
- Adding a channel from the page and restarting to play it has been done on the
  rig: a vocal channel was added to a guitar rig that way. Making a whole new
  rig on the **New rig** screen, **Change input**, **Remove** and **Delete** are
  not yet recorded as tried on the rig.

## If something goes wrong

- **"Not deleted"** in the bar at the top. Hover over it for the reason:
  usually it is the rig that is playing, switched to from another page or a
  pedal meanwhile (switch away, then try again), or the agent was busy with
  another command, such as a rig starting (try again in a moment).
- **"Can't undo"** in the bar, beside **Undo**. Hover over **Undo** before pressing it to
  see why: a rig of that name is there again (the deleted one is still in
  `config/rigs/.deleted/`), or the deleted file is no longer there. The next
  press goes on to the change before.
- **"Not made" (on the New rig screen), or "Not added" or "Not changed" (in
  the bar at the top).** The agent checks
  that the rig would really run before it writes anything, and refused. Hover
  over it for the reason; nothing was written.
- **It mentions unsaved changes.** Save, then try again.
- **The restart is refused because of changes since.** The bar offers
  **Restart anyway**, which drops them. Save instead if you want to keep them.
- **The left column says no rig is running**, or that the rig was started from a "v1 config" (before an update).
  Start a rig from the list under **RIGS** first.
