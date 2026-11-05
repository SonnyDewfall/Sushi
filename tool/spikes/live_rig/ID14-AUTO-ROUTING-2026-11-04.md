# iD14 routing without MixiD, and automatically on connect

Hand-over for a local session on the rig laptop (`sonny-dewfall-XPS-9320`), written 2026-11-04 by
the cloud session. It follows [RESULTS-id14-windows-2026-11-04.md](RESULTS-id14-windows-2026-11-04.md),
which is the authority on the protocol. **Read that file first.** The constraints section of
[RESULTS-id14-mixer-2026-11-03.md](RESULTS-id14-mixer-2026-11-03.md) also still stands.

**None of this has run against an iD14.** The cloud machine has no iD14. Everything that could be
tested without one was, and the tests are listed below. Every step here is the first time it has
been tried on the device.

## What is new

| File | What |
|---|---|
| `idctl.c` | `idctl apply [daw\|mainmix]` claims interface 0 **once**, sends every write, and tears down **once**. Each write prints its entity, selector, channel and bytes, then `ok` or the libusb error. The first failure stops the remaining writes, and the teardown still runs. SIGINT, SIGTERM, SIGHUP and SIGQUIT finish the teardown: exit 128 + signal if writes were left unsent, but 0 if the signal came after every write had succeeded. A failed claim hands interface 0 back to snd-usb-audio if it was left without a driver. Numbers are parsed strictly (any C base, nothing else in the argument), and anything malformed is refused with exit 2 before the device is touched. `--dry-run` opens nothing. `idctl route` now means this device's three real sources. |
| `id14_profile.c/.h` | What gets written, as plain C with no libusb |
| `test_id14_profile.c`, `test_idctl_cli.sh`, `test_id14_route_on_connect.sh` | The tests. `idctl-apply-*.expected` are the exact dry-run listings |
| `idctl.mk` | `make -f idctl.mk check` builds and runs all of them |
| `85-id14-route.rules`, `id14-route@.service`, `id14-route-on-connect.sh` | Runs `idctl apply daw` on every connect. Only `daw`: the script refuses any other profile |

**The profiles:**

- **`daw`**, the default and what the rig uses. Routing channels 0–3 are written `00 01 00 01`,
  so all four outputs take DAW Thru and the headphones take the same stream pair as the mains.
  This is exactly what MixiD's DAW Mix on rows 1–4 sends today.
- **`mainmix`**, for open item 2 (the monitor knob on the mains). It sends 64 writes in this
  order:
  1. all 56 matrix crosspoints `00 80` (silent);
  2. Main Mix master 0x0C/0x02 channels 1 and 2 `00 00`;
  3. DAW 1 → Main L (0x28) and DAW 2 → Main R (0x2D) `00 00`;
  4. headphones `00 01` (as in `daw`);
  5. mains `14 15` (Main Mix), last.

  Until step 5 the mains are not on Main Mix, so nothing before it can be heard through them.
  Within the matrix, everything is silenced before anything is raised.

**Source `0x03` is never sent.** It is dead on this unit. The tests fail if any route sends it.

**If the claim fails** (for example `LIBUSB_ERROR_BUSY` because MixiD is connected), idctl prints
`claim failed: …` and then one of these, and exits 1 without writing anything:

- `after the failed claim: snd-usb-audio is still bound to interface 0`: the card was never
  touched. This is the normal case.
- `after the failed claim: interface 0 had no kernel driver; reattach <result>`: libusb detached
  the driver and then failed the claim, so idctl handed the interface back. `LIBUSB_SUCCESS`
  means the card is back. `LIBUSB_ERROR_BUSY` means another program holds the interface: libusb
  reports "no kernel driver" when MixiD has it too. The attach takes nothing from MixiD.

On connect, the same case appears in the journal as idctl's lines, followed by
`idctl apply daw failed (exit 1) and the card was never detached`.

## Rules for this session

- **Branch `claude/ui-tests`.** Never master, and never open a PR. Before pushing, run
  `git fetch origin claude/ui-tests`. If origin has moved, `git merge origin/claude/ui-tests`
  and then push. Never rebase or force-push. The cloud session pushes this branch too.
- **Test tones come from `quiet_sweep.py` at a stated dBFS**, starting at −40 and never above
  −25. **Never use `speaker-test`**: it plays near full scale, and on 2026-11-03 that was
  painfully loud.
- **Never `kill -9` idctl, and never `pkill -f` anything.** A killed claim costs the sound card.
  Ctrl-C and SIGTERM are handled (idctl finishes its teardown first), but do not test that on
  the device unless it is asked for. Kill by recorded pid.
- **One variable at a time.** If something fails, record it and stop that step. Do not try a
  second change on top.
- **Close MixiD first.** Two programs claiming interface 0 is not a test anyone wants.

## 1. Build

From the Sushi checkout:

```bash
git fetch origin claude/ui-tests && git checkout claude/ui-tests && git merge --ff-only origin/claude/ui-tests
cd tool/spikes/live_rig
sudo apt install libusb-1.0-0-dev     # if not already installed
make -f idctl.mk check
```

Expect `814 checks, 0 failures`, then 42 `ok` lines and no `FAIL`. Record the output. The build
uses `-Wall -Wextra -Werror`, so a warning on this compiler stops the build. If that happens,
record the warning.

## 2. Dry run

```bash
./idctl --dry-run apply daw
./idctl --dry-run apply mainmix | tail -8
```

`daw` should print exactly:

```
profile daw
dry run: 4 write(s), device not opened
 1/4  entity 0x33 selector 0x06 channel 0x00  bytes 00     route Main L <- DAW 1
 2/4  entity 0x33 selector 0x06 channel 0x01  bytes 01     route Main R <- DAW 2
 3/4  entity 0x33 selector 0x06 channel 0x02  bytes 00     route HP L <- DAW 1
 4/4  entity 0x33 selector 0x06 channel 0x03  bytes 01     route HP R <- DAW 2
```

The full `mainmix` listing is in `idctl-apply-mainmix.expected`. Audio should not drop during a
dry run, because nothing is opened.

## 3. Baseline, from a fresh connect

Unplug the iD14, wait 5 s, and plug it back in. The routing is now the device's own default. Then
record:

```bash
aplay -l | grep -i -A2 audient                      # card number N
for d in /sys/bus/usb/devices/*; do [ "$(cat "$d/idVendor" 2>/dev/null)" = 2708 ] && echo "${d##*/}"; done
                                                    # the device, e.g. 3-1.2.1 -- written <dev> below
ls /proc/asound/cardN/                              # expect pcm0p and pcm0c among them
wpctl status | grep -i id14
python3 quiet_sweep.py /tmp/sweep.wav -40           # 220 / 330 / 440 / 550 Hz on stream channels 1-4
aplay -D hw:N,0 /tmp/sweep.wav
```

If `aplay` says the device is busy, PipeWire has it open. Stop whatever is playing, wait 5 s
(PipeWire suspends an idle device and closes it), and try again. Write down which tones come out
of which monitor and which headphone side. From 2026-11-03, expect only output 1.

## 4. `idctl apply daw` by hand

Monitors at a normal low level. The plugdev rule lets this run as Sonny's user:

```bash
./idctl apply daw; echo "exit $?"
```

The prediction (untested):

```
profile daw
opened 2708:0002 at <dev>
claimed interface 0
 1/4  entity 0x33 selector 0x06 channel 0x00  bytes 00     route Main L <- DAW 1: ok
 ...
 4/4  ...: ok
teardown: reset LIBUSB_SUCCESS, release LIBUSB_SUCCESS, attach LIBUSB_ERROR_BUSY
exit 0
```

`attach LIBUSB_ERROR_BUSY` is expected and harmless. With auto-detach on, the release has already
re-bound snd-usb-audio, so the attach finds the interface taken. That is reasoning from libusb's
source, not something observed, so **record the teardown line exactly**.

Then check, and record each:

1. `aplay -l` still lists the iD14, and `ls /proc/asound/cardN/` shows the same `pcm*` entries as
   in step 3. Allow about 10 s. If they are missing, go to **Recovery**.
2. `wpctl status` still shows the iD14 sink, and it is still the default.
3. `aplay -D hw:N,0 /tmp/sweep.wav`. Expected:
   - 220 Hz on the **left monitor and the left headphone**;
   - 330 Hz on the **right monitor and the right headphone**;
   - 440 Hz and 550 Hz **nowhere**, since `daw` routes no output to stream channels 3–4.

   Check with headphones on, then off. **Both pairs should be stereo.**
4. Play some ordinary stereo audio through PipeWire at a low desktop volume (the rig, or a
   browser). It should be stereo on both pairs.
5. The monitor knob: expected to change the headphones and not the mains, because DAW Thru
   bypasses the monitor section. Record what it does.

If step 4's writes all print `ok` but the outputs are as in step 3, routing did not take. Record
it and stop.

## 5. Automatic on connect

```bash
sudo install -m 0755 idctl /usr/local/bin/idctl
sudo install -D -m 0755 id14-route-on-connect.sh /usr/local/libexec/id14-route-on-connect
sudo install -m 0644 id14-route@.service /etc/systemd/system/id14-route@.service
sudo install -m 0644 85-id14-route.rules /etc/udev/rules.d/85-id14-route.rules
sudo systemctl daemon-reload
sudo udevadm control --reload
```

**First, start the unit by hand.** This runs the same path without a replug:

```bash
sudo systemctl start id14-route@<dev>.service       # blocks for ~5 s, then returns
systemctl status id14-route@<dev>.service --no-pager
journalctl -b -u 'id14-route@*' --no-pager -o short-precise | tail -30
```

Expect these lines, in order:

1. `iD14 connected at … (003/0NN)`
2. `card … has PCMs [pcm0c pcm0p] after 0s`
3. `before the claim: nodes [pcmC1D0c:<inode> pcmC1D0p:<inode>]`
4. idctl's lines
5. `card rebuilt: PCMs [pcm0c pcm0p], new nodes […]`, with inodes that differ from line 3
6. `routing applied: daw; card … back with PCMs […]` The status should be
`inactive (dead)` with `status=0/SUCCESS`. Failures are logged at error priority, which shows red
in `journalctl`.

**Then the replug test, three times.** There is no need to wait between replugs. Every
connection gets a new USB device number (the `003/0NN` in the first line). The loop guard refuses
only a second run for the **same** connection within 15 s, so it cannot block a real replug. If it
does fire, the journal says `refusing: already ran …s ago for this same connection`. Report that
if it ever happens without a manual `systemctl start`. As a backstop, the unit refuses a sixth
start within two minutes. If that ever trips during testing, clear it with
`sudo systemctl reset-failed 'id14-route@*'` and note it.

**Also try one fast replug:** unplug and plug back within about a second. That is the cable-dropout
case, and it must still route.

For each replug:

```bash
# unplug, wait 5 s, plug in, wait 15 s
journalctl -b -u 'id14-route@*' --no-pager -o short-precise | tail -30
journalctl -b -u 'id14-route@*' --no-pager | grep -c 'running:'   # must go up by exactly 1 per plug
udevadm info /sys/bus/usb/devices/<dev> | grep -E 'TAGS|SYSTEMD_WANTS'
aplay -l | grep -i audient; wpctl status | grep -i id14
aplay -D hw:N,0 /tmp/sweep.wav                     # as in step 4.3
```

Record each settle time (the `after Ns` in the `has PCMs` line), whether PipeWire still shows the
iD14 and keeps it as default, and the sweep result. **If the run count goes up by more than one per
plug, that is a loop.** Uninstall at once (below) and report it.

Also check WirePlumber's log around the apply, and quote any errors:
`journalctl --user -b -u wireplumber --since -5min --no-pager | tail -30`.

**Optional:** reboot with the iD14 attached, then read the same journal lines. At boot, nobody is
logged in yet, so PipeWire cannot be in the way.

### What the connect path does, and why

- **Settling.** The script waits until this device's ALSA card lists its PCM devices. The card
  is found by bus and device number in `/proc/asound/card*/usbbus`, and the timeout is 30 s.
  It then waits 3 s more before claiming. If the PCMs never appear, it does not claim, and it
  fails.
- **Racing PipeWire.** The script does not try to beat WirePlumber. It deliberately goes after
  it. The only claim and teardown ever seen to work is MixiD's Connect, against a card PipeWire
  had already had open for a while. Waiting for the PCMs plus 3 s makes the automatic claim look
  as much like that as possible. Losing the PCMs for a moment then looks to WirePlumber like a
  quick unplug and replug, which it handles. Whether 3 s is enough for WirePlumber's own probe is
  a guess. If the iD14 is missing from `wpctl status` after an automatic apply but not after a
  manual one, raise the wait and change nothing else:
  `sudo systemctl edit id14-route@.service`, then add `[Service]` and
  `Environment=ID14_SETTLE_EXTRA=8`.
- **Permission.** The unit is a system unit and runs as root, so it does not need
  `84-audient.rules`. Leave that rule in place: it is what lets idctl and MixiD run by hand.
- **Logging.** Everything goes to the journal, each line prefixed with the device
  (`3-1.2.1: …`), and the instance name carries it too.
- **Only `daw`.** `ID14_PROFILE` set to anything else is refused with exit 2. `mainmix` is
  untested on the device and is for trying by hand with the monitors down. The unit's
  `TimeoutStartSec=90` is also sized for `daw`'s 4 writes, not `mainmix`'s 64.
- **Never loop.** The unit has no restart, and a failure stops it there. The rule matches the
  usb_device, not the sound card: idctl's teardown makes the card rebuild its PCMs, so a rule on
  the sound card would fire on every apply. Two more checks back that up:
  - the script refuses a second run for the same connection (same bus and device number) within
    15 s;
  - the unit refuses a sixth start within 120 s. This catches a loop that re-enumerated the
    device every time, which the script's check would let through.
- **After the teardown,** the script does not trust a listing, because the PCMs from before the
  claim look exactly like rebuilt ones. Before the claim it records the inode of each
  `/dev/snd/pcmC<N>D…` node. It then waits up to 15 s for the same PCMs to be listed with all-new
  nodes. A PCM taken down and rebuilt gets a new device node. There are three outcomes:
  - all-new nodes: success;
  - the old nodes still there: `the PCMs never went away`, exit 4, a failure of its own;
  - anything else: `the card did not come back`, exit 3, with the rebind command for this device.

**Known gap: suspend.** If the laptop suspends and the iD14 loses power, the routing may reset with
no new connect event, so nothing re-applies it. This is untested. If it happens after a resume, run
`idctl apply daw` by hand, and record that it happened.

### The HID mouse (optional)

`85-id14-route.rules` has a second rule, commented out. It unbinds usbhid from the iD14's
interface 4, so the knob stops moving the pointer. To try it, uncomment the line, run
`sudo udevadm control --reload`, and replug. Check that `ls /sys/bus/usb/devices/<dev>:1.4/driver`
is gone, and that the knob no longer moves the pointer. Audio should be unchanged. It probably also
disables Audient's ScrollControl (the knob as a scroll wheel), which nobody here uses. That is an
inference from the interface being the same, not a test. This rule is untested.

## 6. `idctl apply mainmix`, separately, with the monitors right down

This is open item 2: does Main Mix put the monitor knob on the mains? Never from Linux before.

**Before running it:** turn the monitors' own volume (or the amp) **all the way down**. Turn the
iD14's knob fully anticlockwise. Take the headphones off.

```bash
./idctl apply mainmix; echo "exit $?"
```

Expect 64 `ok` lines and exit 0. If a write fails, idctl stops there and tears down. Record which
write failed and its error, and do not go on. Then check `aplay -l` and `ls /proc/asound/cardN/`
as in step 4.1.

Loop the quiet tone, and bring the monitors up slowly:

```bash
for i in 1 2 3 4 5 6 7 8; do aplay -q -D hw:N,0 /tmp/sweep.wav; done
```

Record:

1. **Does the computer's audio reach the mains at all?** On 2026-11-03, "Main Mix carries no
   computer audio" was measured with MixiD's wrong bytes. This is the first try with the right
   ones, plus a written matrix.
2. **Is it stereo?** 220 Hz should be on the left only, and 330 Hz on the right only. Both tones in
   both monitors would be the mono sum held in the panel's saved state. That would mean the matrix
   writes did not land where the write-up says.
3. **Does the iD14's knob now change the mains?** Sweep it through its range against the loop.
   This is the question.
4. With the knob all the way up, is it about as loud as `daw`? It should not be louder: DAW 1 at
   0 dB through a master at 0 dB is meant to be unity. That is reasoning, not a measurement.
5. Headphones (put them on with the knob down): still stereo on 220 and 330 Hz, and still on
   their own level?
6. Can you hear 440 or 550 Hz anywhere? You should not.

**Optional, last, and only if the card has behaved so far:** `./idctl get 0x36 0x12 0 2` with the
knob at two different positions. This is the main volume as the device reads it. Each `get` is a
separate claim, and claims beyond one per session are not known to be safe. Writing 0x12 is not
offered: it failed with `LIBUSB_ERROR_IO` from Linux on 2026-11-03.

## 7. Back to `daw`

Run `./idctl apply daw`. This writes the routing only. The matrix stays as `mainmix` left it,
which nothing uses once the mains are back on DAW Thru. Alternatively, replug, and the service
applies `daw`. Either way, repeat step 4.3's sweep.

## Recovery: the card is gone

If the iD14 is missing from `aplay -l`, or `/proc/asound/cardN/` has no `pcm*` entries, rebind
snd-usb-audio to interface 0. Use the device path from idctl's `opened … at` line, which is
`3-1.2.1` on this laptop's usual port:

```bash
echo -n '3-1.2.1:1.0' | sudo tee /sys/bus/usb/drivers/snd-usb-audio/bind
aplay -l | grep -i audient
```

If that fails or changes nothing, replug. A replug resets the routing, and with the service
installed it applies `daw` again. Record every time recovery was needed and what preceded it.

## Uninstall

```bash
sudo rm /etc/udev/rules.d/85-id14-route.rules /etc/systemd/system/id14-route@.service \
        /usr/local/libexec/id14-route-on-connect /usr/local/bin/idctl
sudo systemctl daemon-reload && sudo systemctl reset-failed 'id14-route@*'
sudo udevadm control --reload
```

The loop guard's stamps are in `/run/id14-route`, which is cleared at boot.

## What to write down and report

Write `tool/spikes/live_rig/RESULTS-id14-auto-routing-<date>.md` with:

- step 1's output, and any warning;
- for steps 4 and 6, **idctl's whole output**, including the `opened … at` and `teardown:` lines;
- for every sweep, which tone came out of which speaker and headphone side, at what dBFS;
- `aplay -l` and the `pcm*` entries before and after each claim, and whether `wpctl status`
  still had the iD14 as default;
- for each replug, the settle time, the journal lines, and whether the run count rose by exactly
  one;
- the five `mainmix` answers, and especially whether the knob works on the mains;
- every recovery needed, and what came before it;
- anything that did not go as predicted here, quoted as it happened. **A wrong prediction is a
  result.**

Commit it to `claude/ui-tests` and push, after the fetch-and-merge described in the rules. Do not
edit anything else to make it pass. If a script needs fixing, record the fix and why.

## Guesses in this file, collected

- That idctl's one-claim teardown keeps the card in the same way MixiD's does. The calls are
  identical (below). Only the timing differs: idctl tears down within milliseconds of its last
  write, while MixiD sits connected for seconds or minutes.
- The matrix layout (14 inputs × 4 outputs, `input × 4 + output`). This is second-hand from the
  Windows write-up, where only DAW 1–4 were swept. Two things do not fit neatly:
  - the panel's full-matrix launch was **171 writes**, not 56, and the reason is not known;
  - the panel draws its 14th strip (DAW 4) as **talkback**.

  So input 13 may be talkback rather than DAW 4. `mainmix` silences it either way.
- That Main Mix master channels 1 and 2 at `00 00` is unity. The panel wrote that value at launch.
- The settle time, the 3 s PipeWire allowance, and the attach line's `LIBUSB_ERROR_BUSY`.
- That idctl's reset does not re-add the USB device. If it does without re-enumerating, the
  script's guard refuses the second run. If it re-enumerates, the unit's start limit stops it
  after five runs.
- The card-return check rests on two assumptions:
  - when `/proc/asound/cardN` loses its `pcm*` entries, the `/dev/snd/pcmC<N>D…` nodes go too;
  - devtmpfs gives a re-created node a new inode number.

  If the first is wrong, every connect fails with exit 4, `the PCMs never went away`, even
  though the routing was written. If that happens, report it, and do not loosen the check to
  make it pass.

### The claim and teardown, checked against MixiD

Sonny reported that idctl matches MixiD's claim and teardown exactly. Checked against
`driver.h` on MixiD's `rig/id14-mki-interface0` (c1f3957):

| | MixiD `driver_init` / `driver_shutdown` | idctl `open_device` / `close_device` |
|---|---|---|
| open | `libusb_init`, `libusb_open_device_with_vid_pid(2708, id)` | the same |
| claim | `libusb_set_auto_detach_kernel_driver(1)`, then `libusb_claim_interface(0)` | the same |
| teardown | `libusb_reset_device`, `libusb_release_interface(0)`, `libusb_attach_kernel_driver(0)`, `libusb_close`, `libusb_exit` | the same, in the same order |

The calls match. There are three differences, none in the calls themselves:

1. MixiD treats a failed `set_auto_detach` as fatal, while idctl warns and goes on. On Linux it
   does not fail, and if it did, the claim would fail anyway.
2. MixiD reads the routing back right after the claim (`dump_routing`), and every one of those
   reads STALLs. idctl does not read.
3. The time between claim and teardown, described above.

The sequence is kept exactly as it was. idctl now only prints the return code of each teardown
call. The one addition is on the failure path: after a failed claim it hands interface 0 back if
the claim left it without a driver (see **If the claim fails**). MixiD does not do this.

To check this, the cloud session shallow-cloned Sonny's MixiD fork (github.com/SonnyDewfall/mixid)
and read `driver.h`. Nothing in this repository comes from that clone except this comparison.
