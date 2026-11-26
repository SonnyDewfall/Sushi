# When something's wrong

What to check when there is no sound, what to do after an update, and how to ask
the rig what it thinks is wrong.

One habit helps with all of it: **read the top of the page**. When the rig
refuses to do something, the bar under the header says so; when a part of it
stops, a box at the top left says so, beside the rig's name. Each is a word
or two; hover over it for the rest, usually with what to do next.

## No sound

Work down this list. Stop at the first thing that is wrong.

1. **Is a rig up?** Look at the top left of the page. If it reads **Rig down**,
   start one ([Getting started](getting-started.md)). If it reads
   **Agent not answering**, see
   [The page says "Agent not answering"](#the-page-says-agent-not-answering).
2. **Is there a red box beside the rig's name?** If a part of the rig has stopped, it says
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

## After an update

When the project is updated (after a `git pull`), **the agent keeps running the
old code until it is restarted.** The page picks up its new files as soon as you
reload it, so until the agent is restarted the two can disagree, and buttons may
fail or behave oddly.

To restart cleanly:

1. Save anything you want to keep ([Saving and going back](saving.md)).
2. Stop the rig: under **RIGS**, **Stop** and its name, then **Yes**.
3. In the terminal where the agent is running, press Ctrl+C. Then start it
   again:

   ```bash
   sushi-rig agent
   ```

   (Use `sushi-rig agent --phone` if you use your phone, as in
   [Getting started](getting-started.md#use-your-phone-as-well).)
4. Reload the page, and start the rig again.

You can restart the agent without stopping the rig, and the rig keeps playing.
But the new agent did not start that rig, and it shows:

- It does not watch the rig. If a part of the rig stops, **no box appears** to
  tell you.
- **Undo** has nothing to take back (it remembers only what this agent saw),
  and the number on **Save** starts again from nothing.
- It cannot change the amp's model.

All of that lasts until the rig is next started from the new agent. Stopping
and starting the rig as well, as above, avoids it.

## The page says "Agent not answering"

The agent has stopped, or the laptop is not reachable from the device you are
on. The rig itself may still be playing.

- On the laptop, start the agent again: `sushi-rig agent`.
- On a phone, check you are on the same network as the laptop, and that the
  agent was started with `--phone`. Then see [Playing out](playing-out.md#if-the-phone-cannot-reach-the-laptop).

## The page says "refused: … is not a name this agent answers to"

The page opens with that line instead of the rig, or a change says it. The
agent only answers a page opened by the laptop's address
(`http://192.168.1.20:8029/`), `localhost`, the laptop's name, or its name
with `.local`. This stops a web page elsewhere from reading or changing your
rig.

- Open the page by the laptop's address instead: `sushi-rig agent --phone`
  prints it, and the rack page shows it.
- If you reach the laptop by another name, such as a VPN's or a proxy's,
  start the agent with that name allowed, the name alone with no `http://`
  or port: `sushi-rig agent --phone --allow-host <that name>`.

## A part of the rig has stopped

The rig is several programs working together: the effects engine, the amp, the
tuner, the connections between them, and the recorder. If one stops, a box
appears beside the rig's name saying which one **exited**; hover over it for
what that means for the sound, and where its log is. **Nothing restarts by itself.** That is on purpose: a rig that
quietly patched itself up could be running wrong without you knowing.

The page turns red at the top, and a red band under it says what is wrong in
a word or two (**Amp exited**, **Sushi unreachable**, **No amp**, **Links not
made**, **Start failed**), with the log's path under it. Hover over the words
for the whole story. The band's buttons are the fix:

- **Restart** restarts the rig from its file. It asks **Restart?** first,
  since the sound stops while it comes back, and works while the page is
  Locked. If you have unsaved changes it says so, and offers to restart
  anyway.
- **Stop** stops the rig, after **Stop?**.
- **Start again**, after a start that failed, tries the same rig again.

In a terminal, `sushi-rig restart` does the same as **Restart**. It refuses if
you have unsaved changes, and tells you how to go ahead anyway.

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

- **"Not changed:", "Not moved:", "Not saved", "Not started:" or
  "Not switched to" in the bar at the top.** The agent refused. The reason
  follows, in a few words; hover for all of it.
- **A box saying plugins "are not in" a plugins folder, or "cannot be
  loaded from" it.** The rig loads a plugin the rig's own plugins folder
  does not have yet, or has a broken copy of, so it was not started,
  switched to, restarted, reverted or given another rig's channel: whatever was
  playing keeps playing, and a rig file is left as it was. The box names
  each one and says what to run, usually `sushi-rig plugins add` in a
  terminal on the rig machine, or a script under `scripts/` for a plugin no
  package has. Run it, then try again.
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
