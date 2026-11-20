# Playing live

The **stage** screen is the page for playing: everything you might reach for
mid-song is big, and everything that could cut the sound by accident is put out
of reach. This guide covers that screen, switching between rigs, the lock,
pedals, and the visualiser.

A screen as narrow as a phone's opens the stage screen by default. To reach it
from a phone, see
[Getting started](getting-started.md#use-your-phone-as-well), and away from
home [Playing out](playing-out.md). On the laptop,
click **Stage** at the top of the rack; **Rack**, at the bottom of the stage
screen, takes you back.

<img src="img/stage-phone.png" alt="The stage screen at a phone's width, locked: the rig's name as a dropdown beside the Locked button, the master volume on one row with its value, a lock, and its −0.5 and +0.5 buttons either side of the fader, the load, xruns and amp tiles, the Record bar with the next take's name, each channel's volume on one row the same way, and a card for each plugin with a round bypass button" width="320">

## What is on the stage screen

From the top:

- **The rig's name**, beside the **Locked** button. It is also a dropdown of
  your rigs, for switching: see [Switch to another rig](#switch-to-another-rig).
  A name too long for the screen ends in **…**. Under it, whether the rig is up
  and for how long. With no rig up it says **Rig down**.
- **MASTER**: the overall volume. See [Mixing](mixing.md).
- **LOAD**: how much of its time budget the rig is using. Near 100% the sound
  starts to break up, and the tile says **over budget**.
- **XRUNS**: how many times the sound has dropped out since you opened the
  page. If it says **rising**, it is dropping out now.
- **AMP**: the amp model that is playing, or **none**.
- **Record**: see [Recording](recording.md).
- **CHANNELS**: a volume for each channel, under the channel's name.
- **A card for each plugin** (each effect), grouped under its channel's name.

Every volume is one row: its name with its level in dB under it, then a
**−0.5** button, the fader and a **+0.5** button. Tap the level to type one.
A tap on a button moves the volume by exactly half a dB, which is easier on a
phone than nudging the fader. Hold a button and it keeps stepping, about ten
steps a second, for up to 6 dB; let go and hold again to go further. It stops
as soon as your finger slides off the button or you switch to another app.
**MASTER** stops at 0 dB, as its fader does, and a channel's volume at +6 dB.
If the master is above 0 dB because the rig was started before that limit,
**+0.5** does nothing and **−0.5** brings it straight down to 0 dB. The volumes
only move while the page is unlocked: see [The lock](#the-lock).

## Turn an effect on or off

Each card has a round button on its right. Tap it to **bypass** the effect,
which switches it off without removing it. Tap again to switch it back on. The
button glows when the effect is on, and the whole card dims when it is bypassed.

To reach an effect's main knobs, tap its name. The card opens; tap the name
again to close it. For every control an effect has, use the rack: see
[Shaping a sound](shaping-a-sound.md).

## The lock

The page starts **Locked**. On the stage screen that holds every volume and
puts stopping and switching the rig out of reach, so a stray thumb cannot cut
the sound or change a level mid-song. The rack keeps its **Stop** and
**Switch to** buttons, and its volumes, even while locked: the lock does not
protect you there.

While locked you can still turn knobs, bypass effects and record. What the lock
takes away:

- on the stage screen, every volume: **MASTER** and each channel's volume do
  not move by dragging, tapping, the mouse wheel, the keyboard, a typed value
  or the **−0.5** and **+0.5** buttons. Each shows a small lock beside its
  name, and its fader and buttons are greyed out;
- on the stage screen, switching rigs (the rig's name shows no dropdown arrow
  and does nothing when tapped), **Start** and **Stop**, and the amp;
- on the rack, moving a plugin up or down its chain, and changing the amp's
  model.

To unlock:

1. Tap **Locked**. It changes to **Tap again to unlock**.
2. Tap it again within four seconds. It now reads **Lock off · editing**.

To lock again, tap **Lock off · editing** once.

Two things to know. Each browser remembers its own lock, so unlocking the laptop
does not unlock the phone. And it is only this page's lock: it does not stop a
pedal, or another device that is unlocked, from changing the rig.

## Switch to another rig

On the stage screen:

1. Unlock. The rig's name at the top gets a dropdown arrow.
2. Tap the rig's name and choose the rig you want; the one playing is already
   selected. There is no second question: choosing a different rig switches
   to it at once.

While the switch runs, the name cannot be tapped and a line under the tiles
says what is happening. With no rig up, choosing a rig does not start it: tap
**Start** and its name, under the tiles. **Stop** is there instead while a rig
is up, and asks again before it stops the sound.

On the rack the list of rigs is always there, under **RIGS** on the right: tap
the rig you want, then **Switch to** and its name.

A line then tells you how it went. **Switched to … live** means the change was
made without restarting anything: on the rig this took about a second or less,
with no click or gap heard. **Switched to … by restart**, followed by the
reason, means the rig had to stop and start again, which is about five seconds
of silence. That happens when the two rigs differ in ways that cannot be changed
on the fly: different channels, a different amp model, or an amp on one and not
the other.

**A switch throws away unsaved changes without asking.** If you have turned
knobs you want to keep, save first: see [Saving and going back](saving.md).

If a take is recording when you switch, the take is finished first (not yet
tried on the rig).

## Pedals

A rig can be set up to answer a MIDI pedal or pad controller: a pad that switches
one effect on and off, an expression pedal that sweeps a knob or fades from one
channel to another, and program changes that switch rigs. This is written into
the rig's file and the machine's file by hand; the page has no screen for it.
The how is in [MIDI](../reference/midi.md).

If a pedal is fading a channel, that channel's **VOLUME** says
**driven by** and the pedal's name, and cannot be moved from the page.

Not yet tried on the rig: none of the saved rigs has a pedal set up, and nothing
has been played through a real pedal. Only the pad controller's own messages
have been checked.

## The visualiser

Leave the laptop alone for a few seconds while a rig is running and the page
turns into a visualisation that moves with your playing, like a screensaver.
Touch the screen, move the mouse or press a key and the page comes straight
back. That touch or key does nothing else: it will not press the button under
your finger.

It only runs on the rig's own laptop, in a browser open at
`http://localhost:8029/` (or `http://127.0.0.1:8029/`), in a window at least
900 pixels wide. Phones do not get it: they are for working the rig.

**The first time it starts, the browser asks to use the microphone.** That is
how the page hears your guitar: it listens to the guitar's input on the audio
interface beside the rig, so it adds no delay to your sound. It is not quite
nothing to the rig, though: while it listens, PipeWire does a little work for
it in the same moment as the rig's own (see below). Choose **Allow**: if the browser offers to allow it only
this time, choose the one that keeps it for this site instead, and in Firefox
tick **Remember this decision** first. Then it will not ask again. Whatever input the question offers, the page then
picks the guitar's own, from the rig's machine file.

The line at the very bottom of the page holds its settings:

- **Visualiser**: untick it to turn it off. This browser remembers.
- **after … s without a touch**: how long to wait, 3 to 600 seconds. 10 to start with.
- **listening to**: **automatic** (the guitar's input) or another input, and
  which channel of it.
- **Show now**: try it straight away.
- **look**: **random** to begin with: the looks come in a random order, each
  for half a minute to a minute, never the same one twice running, and each a
  little different in colour and movement every time it comes on. **random,
  monochrome** keeps to the black-and-white and one-colour ones; **random,
  equalisers** to the four that show the spectrum. Pick one by name to keep it
  on, say while you work on a sound.

If it does not start, the same line says why: no rig running, the window too
narrow, the browser told not to use the microphone (allow it again in the
browser's site settings for this page), the computer asking for reduced
motion, or a browser that would draw it without the graphics card. That last
would take the processor the rig needs, so it stays off.

**If the rig glitches while it is up, it stops.** If XRUNS rises while the
visualiser is listening, it goes, and the line says "Stopped: the rig
glitched while the visualiser was listening". It stays off until you reload
the page or tick **Visualiser** again. The glitch may not have been its
doing, but it is not worth finding out on stage.

**Check once that PipeWire can keep up with it.** Run `sushi-rig doctor` and
look for `pulse-realtime`. "pipewire-pulse runs realtime" is what you want. If it
says pipewire-pulse runs no thread at realtime priority, either keep the
visualiser off while you play or do what the line under it says. You can
check by hand too: `ps -L -o tid,cls,rtprio,comm -p "$(pgrep -x pipewire-pulse)"`
should show a thread (`pw-data-loop`) with `FF` in the `CLS` column.

### The looks

Thirteen, all made for the rig. **Embers**, **Tide** and **Bloom** are the
soft, colourful ones. **Tunnel**, **Shutter**, **Smoke**, **Mirror**, **Storm**
and **Eclipse** are stark: black and white or a single colour, with hard
edges, deep zooms and smoke. Some of them flash on a loud attack, never more
than twice in any second. **Bars**, **Curve**, **Aurora** and **Mono bars**
are like an equaliser's display, and show what frequencies you are playing.

### Reading the equaliser looks

Bars, Curve and Aurora all use the same frequency scale and the same colours,
so once you learn them, they read the same in all three. Low notes are on the left and
high ones on the right, and each octave takes the same width: there are eight,
from almost an octave below the low E on the left to 12 kHz on the right. There are no
numbers on screen. Where a colour is lit, and how bright, tells you what is sounding:

- **deep red**: the lowest notes: the low E and drop tunings, boom and thump.
- **red**: the low strings' fundamentals.
- **orange**: low-mids, mud, and the high strings' open notes.
- **yellow**: boxiness, and notes high up the neck.
- **green**: the mids: honk and nasal tones.
- **cyan**: upper mids: bite, where a guitar cuts through a band.
- **blue**: presence: pick attack and edge, where fizz starts.
- **violet**: fizz, hiss and air.

In **Bars**, each colour is a block of six bars with a wider gap between
blocks. A small cap above each bar holds its peak for a moment, then falls
back slowly. **Curve** is a single glowing line, with faint lines at each
octave's edge. In **Aurora** a note you hold builds up a haze in its colour
that stays a few seconds after it stops, so a build-up you keep hitting, such
as mud in orange, shows as a glow that will not go. A picked note flickers
and is gone. Faint marks along the bottom show where each octave starts.
**Mono bars** is Bars in one colour, without the colour map.

The display rescales to the loudest part, so it reads the same whatever your
level is. It shows which parts are louder than the others, not how loud you are.

## If something goes wrong

- **A box appears at the top saying something "exited".** A part of the rig has
  stopped. The box says which part and what it means for the sound. Nothing
  restarts by itself: see [When something's wrong](troubleshooting.md#a-part-of-the-rig-has-stopped).
- **XRUNS is rising, or the sound crackles.** See
  [When something's wrong](troubleshooting.md#crackles-and-dropouts).
- **A box starts "Not switched to".** The agent refused, or the switch failed,
  and the box gives the reason. The rig that was playing is left playing where
  that is possible.
- **The top reads "Agent not answering".** The agent has stopped. The rig itself
  may well still be playing. Start the agent again: see
  [Getting started](getting-started.md).
