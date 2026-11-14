# When something's wrong

What to check when there is no sound, what to do after an update, and how to ask
the rig what it thinks is wrong.

One habit helps with all of it: **read the box**. When the rig refuses to do
something, or a part of it stops, the page says so in a box near the top, in
plain words, usually with what to do next.

## No sound

Work down this list. Stop at the first thing that is wrong.

1. **Is a rig up?** Look at the top left of the page. If it reads **Rig down**,
   start one ([Getting started](getting-started.md)). If it reads
   **Agent not answering**, see the next section but one.
2. **Is there a box near the top?** If a part of the rig has stopped, it says
   which. See [A part of the rig has stopped](#a-part-of-the-rig-has-stopped).
3. **Are the volumes up?** Check **MASTER** in the header and each channel's
   **VOLUME**. Silence shows as **−∞** ([Mixing](mixing.md)).
4. **Is an effect swallowing the sound?** Bypass the channel's plugins one at a
   time ([Shaping a sound](shaping-a-sound.md)).
5. **Is it the interface?** Check its monitor knob, the cables, and that the
   guitar's input gain is up. Sound from one side only is its own problem: see
   [The audio interface](audio-interface.md).
6. **Did the start warn you?** The line that appears when a rig starts ends
   with **Warnings:** if a connection between the interface and the rig could
   not be made, or the amp did not come up. Stop the rig and start it again,
   and read that line.
7. **Ask the rig.** Run `sushi-rig doctor` (below).

One rig has a known fault: **electric-octave-up** can go silent, or make a burst
of noise, some seconds after starting, because of a bug in its octave plugin.
Stopping and starting the rig clears it. This was measured away from the rig, on
one or two starts in a hundred; it has not been seen on the rig itself.

## After an update

When the project is updated (after a `git pull`), **the agent keeps running the
old code until it is restarted.** The page picks up its new files as soon as you
reload it, so until the agent is restarted the two can disagree, and buttons may
fail or behave oddly.

To restart cleanly:

1. Save anything you want to keep ([Saving and going back](saving.md)).
2. Stop the rig: under **RIGS**, **Stop** and its name, then **Stop now**.
3. In the terminal where the agent is running, press Ctrl+C. Then start it
   again:

   ```bash
   sushi-rig agent
   ```

   (Add `--host 0.0.0.0` if you use your phone, as in
   [Getting started](getting-started.md#use-your-phone-as-well).)
4. Reload the page, and start the rig again.

You can restart the agent without stopping the rig, and the rig keeps playing.
But the new agent starts with an empty memory: **Undo** has nothing to take
back, the number on **Save** starts again from nothing, and it cannot change
the amp's model until the rig has been started from it. Stopping and starting
the rig as well, as above, avoids all of that.

## The page says "Agent not answering"

The agent has stopped, or the laptop is not reachable from the device you are
on. The rig itself may still be playing.

- On the laptop, start the agent again: `sushi-rig agent`.
- On a phone, check you are on the same network as the laptop, and that the
  agent was started with `--host 0.0.0.0`.

## A part of the rig has stopped

The rig is several programs working together: the effects engine, the amp, the
tuner, the connections between them, and the recorder. If one stops, a box
appears saying which one **exited**, what that means for the sound, and where
its log is. **Nothing restarts by itself.** That is on purpose: a rig that
quietly patched itself up could be running wrong without you knowing.

To bring it back, restart the rig. Either:

- on the page, **Stop** the rig and **Start** it again (save first: stopping
  drops unsaved changes); or
- in a terminal, run `sushi-rig restart`. It refuses if you have unsaved
  changes, and tells you how to go ahead anyway.

A restart takes about five seconds.

## Crackles and dropouts

Look at **LOAD** and **XRUNS** at the top of the page.

- **LOAD** is how much of its time budget the rig is using. As it nears 100%
  the sound breaks up.
- **XRUNS** counts dropouts since you opened the page. **rising** means they
  are happening now.

Run `sushi-rig doctor`: it checks the things that have caused this before, such
as another device setting the pace for the sound system. For a closer look at
which part of the rig is using the time, run `sushi-rig top` in a terminal.

## Ask the rig: `doctor`

`doctor` checks whether this laptop is fit to run the rig, and for every
problem it finds, says what to do about it. Run it in a terminal, ideally with
a rig playing, because many of its checks need one:

```bash
sushi-rig doctor
```

Each line starts with one of three words:

- **ok**: checked, and fine.
- **FAULT**: something is wrong. The line under it, starting `->`, says what to
  do.
- **unchecked**: it could not look. That is not a pass. The line under it says
  what it needs in order to check.

Two more commands that help:

- `sushi-rig status` says what is running, and where each part's log is.
- `sushi-rig plugins check` says whether every effect the rigs use can be found.

`doctor` does not check the interface's volume fix
([The audio interface](audio-interface.md)), and nothing checks the sound
itself: that is why the page always says **sound not measured**.

## Other things you may see

- **A box starting "Not changed:", "Not moved:", "Not saved", "Not started:" or
  "Not switched to".** The agent refused. The reason follows, in the box.
- **"busy", or a line saying something "is running".** The rig is in the middle
  of starting, stopping or switching. Wait for it to finish.
- **The middle of the rack reads "The rig is changing".** The same: wait.
- **"Recording is off".** See [Recording](recording.md).
- **The page shows "The page failed to draw the rig".** Reload the page. If
  this started after an update, restart the agent as in
  [After an update](#after-an-update).

If none of this helps, the detail is in the [reference](../reference/README.md),
starting with [Environment](../reference/environment.md) for the laptop and
[The agent](../reference/agent.md) for what the page's messages mean.
