# The audio interface

The rig plays through an Audient iD14. This guide covers the one-time fix that
keeps both of its outputs working, and what to do if the sound goes to one side.

## How it is plugged in

- The guitar goes into the interface's first input, and a microphone into its
  second. The rig knows these as `guitar-in` and `vocal-in`.
- The monitors go on the main outputs, left and right.
- The interface's own knobs still do their jobs: the big knob sets how loud the
  monitors are, and the input gains and phantom power are set on the interface,
  not in the rig.

## Why it needs a fix

On this iD14, the laptop's volume setting reaches a control inside the
interface that turns down the **right** output only. The left ignores it. So
with the laptop's volume at anything less than full, the right side got quieter
while the left did not; at a low setting the right all but vanished. That is
what "left only" was.

The fix has two parts. The first tells the laptop to do its volume itself, on
both sides alike, and to leave the interface's control alone. The second sets
that control to full and makes the setting stick.

This was measured on 12 Nov 2026 with cables from the interface's outputs back
into its inputs. With the fix in place, both sides came back level at each
volume tried, and stayed level through seventeen starts, restarts and switches of the
rig.

## Do the fix (once on each laptop)

1. Copy the rule into place and restart the part of the system that reads it:

   ```bash
   mkdir -p ~/.config/wireplumber/wireplumber.conf.d
   cp tool/spikes/live_rig/51-id14-soft-volume.conf ~/.config/wireplumber/wireplumber.conf.d/
   systemctl --user restart wireplumber
   ```

   Run the `cp` line from the project's folder. The rule is written for
   WirePlumber 0.5.
2. Find the interface's card number. In the output of this, look for the line
   naming the iD14; it starts `card` and a number:

   ```bash
   aplay -l
   ```

3. Set the interface's control to full, using that number in place of `1`:

   ```bash
   amixer -c 1 cset name='Speaker Playback Volume' 127,127,127,127
   ```

4. Make it stick. Without this, the old value comes back every time the
   interface is plugged in:

   ```bash
   sudo alsactl store
   ```

After this, the laptop's volume control works normally on both sides, and the
interface's big knob still sets the monitor level.

## If one side goes quiet

1. Check the control still reads full, using the card number as above:

   ```bash
   amixer -c 1 cget name='Speaker Playback Volume'
   ```

   The values should be `127,127,127,127`. If they are lower, repeat steps 3
   and 4 of the fix.
2. Check the rule is still where step 1 of the fix put it:

   ```bash
   ls ~/.config/wireplumber/wireplumber.conf.d/
   ```

   You should see `51-id14-soft-volume.conf`. If not, repeat step 1.
3. Rule out the obvious: a cable, a monitor that is off, the pan or balance on
   a mixer after the interface.
4. If both of those are right and one side is still quiet, run
   `sushi-rig doctor` and see [When something's wrong](troubleshooting.md).

## Things that may surprise you

- **Do not use the iD14's "DAW" routing** (the DAW mix in Audient's own mixer
  program). That routing goes around the volume control altogether: in the same
  test, a tone came back 52 dB louder with it on. That is a hazard to ears and
  speakers.
- **The fix is on the laptop, not in the project.** A fresh laptop, or a fresh
  install, needs it done again. The project cannot do it for you, and
  `sushi-rig doctor` does not check for it.
- This is what was found on this particular iD14 and this laptop. A different
  interface may not need it, and its sockets would need describing to the rig
  instead: see [The machine file](../reference/machine-file.md).

The measurements behind all of this are in
`tool/spikes/live_rig/RESULTS-loopback-2026-11-12.md`.
