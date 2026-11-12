# Loopback session results (12 Nov 2026)

Against `tool/spikes/live_rig/LOOPBACK-SESSION-2026-11-07.md`, on
`sonny-dewfall-XPS-9320`, branch `claude/ui-tests` at `76f3faf`.

**Status: part-way. Parts A2, A3 and B3 have not run yet.** Sonny asked to focus on
what is needed to get the rig's stereo right, and the cable that is plugged in turns
out to carry one side only, so what Part B can answer is narrower than the brief
assumed. Written as the session goes so nothing is lost.

## Answers so far

| # | Question | Answer |
|---|---|---|
| Q1 | Is Sushi's own master output balanced? | **not yet measured** (A2 needs the cable out) |
| Q2 | Does the wiring stay stereo across a restart? | **not yet measured** (A3) |
| Q3 | On the stereo delay, what does each side carry? | **not yet measured** (A2) |
| Q4 | Does a stereo signal leave both iD14 outputs and come back equal? | **half answered.** Output 1 passes audio at the hardware level, measured. Output 2 cannot be measured with the cable as wired — see B1. |
| Q5 | Does the routing come back after an unplug? | **not yet measured** (B3) |
| Q6 | What is the round-trip latency? | **not answered. The instrument returned a physically impossible figure** — see B4. Not reported as a latency. |

## New result not in the brief: `idctl` replaces MixiD for the per-session step

The autoconnect work that landed on this branch (`idctl.c`, `id14_profile.c`) had never
been run against the hardware. It was, here, and it works. **The GUI is no longer needed
to set the routing.**

```
$ make -f idctl.mk
cc -O2 -g -std=gnu11 -Wall -Wextra -Werror -I/usr/include/libusb-1.0 -o idctl idctl.c id14_profile.c -lusb-1.0
```

Clean under `-Wall -Wextra -Werror`. `make -f idctl.mk check` passes all of it — 21 CLI
cases and 17 connect-script cases, including the teardown and retrigger guards.

The dry run matches the brief's table exactly, `00 01 00 01`:

```
$ ./idctl --dry-run apply daw
 1/4  entity 0x33 selector 0x06 channel 0x00  bytes 00     route Main L <- DAW 1
 2/4  entity 0x33 selector 0x06 channel 0x01  bytes 01     route Main R <- DAW 2
 3/4  entity 0x33 selector 0x06 channel 0x02  bytes 00     route HP L <- DAW 1
 4/4  entity 0x33 selector 0x06 channel 0x03  bytes 01     route HP R <- DAW 2
```

And against the device:

```
$ ./idctl apply daw
opened 2708:0002 at 3-1.2.1
claimed interface 0
 1/4 ... route Main L <- DAW 1: ok
 2/4 ... route Main R <- DAW 2: ok
 3/4 ... route HP L <- DAW 1: ok
 4/4 ... route HP R <- DAW 2: ok
teardown: reset LIBUSB_SUCCESS / LIBUSB_TRANSFER_COMPLETED, release LIBUSB_SUCCESS / LIBUSB_TRANSFER_COMPLETED, attach LIBUSB_ERROR_BUSY
EXIT=0
```

**The card survived**, which was the headline risk in the brief for this tool:

```
$ aplay -l | grep -i id14
card 1: iD14 [Audient iD14], device 0: USB Audio [USB Audio]
$ wpctl status | grep -i id14
 │      95. Audient iD14                        [alsa]
 │  *  103. Audient iD14 Analog Surround 4.0    [vol: 0.18]
 │     102. Audient iD14 Multichannel           [vol: 1.00]
```

`attach LIBUSB_ERROR_BUSY` on the last teardown step is **not** a failure: the kernel had
already re-bound `snd-usb-audio` itself by that point, so there was nothing left to
attach. The node numbers changed (95/96 → 103/102), i.e. PipeWire rebuilt the device,
which is the expected cost.

Worth noting against the memory note that says the per-session step is "launch MixiD,
Connect, click four cells, quit from the window button": that is now
`./idctl apply daw`, no GUI, and it leaves the card in place.

## A1. What is running

**Nothing was.** No rig, no agent, no qpwgraph, no Carla, nothing listening on 8029:

```
$ curl -s --max-time 5 localhost:8029/health     # no output
$ ps -o pid,lstart,cmd -C python3 | grep -i sushi-rig   # no output
$ pgrep -a qpwgraph; pgrep -a sushi; pgrep -a carla     # no output
$ ss -ltnp | grep 8029                                  # no output
```

So the brief's "if the agent started before the pull, restart it" did not apply — there
was no agent to be stale. One has to be started for Part A.

### qpwgraph's own settings — suspect 1 is live

```
$ grep -A9 '\[Patchbay\]' ~/.config/rncbc.org/qpwgraph.conf
[Patchbay]
Activated=true
AutoDisconnect=false
AutoPin=true
Dir=/run/user/1000/sushi-rig
Exclusive=false
Path=/run/user/1000/sushi-rig/rig-amp.qpwgraph
QueryQuit=true
RecentFiles=/run/user/1000/sushi-rig/rig-amp.qpwgraph
```

Reading these against the brief's suspects:

- **`Activated=true` with a remembered `Path`.** A qpwgraph launched *without* `-a` —
  from the desktop, say — loads and activates that remembered patchbay. That is
  precisely suspect 1, and it is armed right now.
- **But `Exclusive=false` and `AutoDisconnect=false`.** So an activated patchbay only
  *adds* links; it does not remove ones it doesn't list. **A second qpwgraph can
  therefore explain an extra or wrong link, but it cannot on its own explain a
  missing link to FR.** That distinction matters for A3 and narrows what suspect 1 can
  account for: report 2 (a quieter, delayed right side) fits it; report 1 (right side
  gone entirely) does not, unless something else removes the link.
- `Path` points at the *runtime* file, which did not exist at session start
  (`/run/user/1000/sushi-rig/` was absent). A remembered patchbay whose file is gone is
  another thing to watch at restart.

### Three tracked patchbay files disagree with each other

```
$ git ls-files Patchbay/
Patchbay/Default.qpwgraph
Patchbay/Test_1.qpwgraph
Patchbay/rig.qpwgraph
```

They do not wire the master the same way:

| File | Master wiring | iD14 node profile |
|---|---|---|
| `rig.qpwgraph` | `audio_output_0`→`playback_FL`, `audio_output_1`→`playback_FR` | `Audient iD14 Analog Surround 4.0` |
| `Default.qpwgraph` | `audio_output_0..3`→`playback_AUX0..3` | `Audient iD14 Pro` |
| `Test_1.qpwgraph` | `audio_output_0..3`→`playback_AUX0..3` | `Audient iD14 Pro` |

`rig.qpwgraph` is the one the code uses as the v1 base (`amp.py:626`) and it is correct
stereo. The other two target a **different PipeWire profile** of the same device, where
the ports are `playback_AUX0..3` rather than `FL`/`FR`.

The device is currently on `Analog Surround 4.0`, so `FL`/`FR` exist and the `AUX` names
do not. **If either of those two files is ever what qpwgraph has activated, none of its
master links can be made at all** — the ports it names are not in the graph. That is a
concrete mechanism for a silent or half-silent master, and it is worth checking which
file Sonny last opened by hand.

### Routing not installed on this laptop

```
$ ls /etc/udev/rules.d/ | grep -iE 'audient|id14'
84-audient.rules
```

Only the old `plugdev` rule. The auto-routing rule from `ID14-AUTO-ROUTING-2026-11-04.md`
is **not installed**, and there is no `id14-route` unit. So routing does not survive a
replug yet, and B3 will be run in its "rule not installed" form.

## B0/B1. The cable: stereo lead, single-channel socket

Sonny reported "a stereo loopback cable from the speaker output into the DI input", and
confirmed it is the main speaker output, not the headphone socket. **Both of those are
correct.** What the loop can carry was then established by measurement.

Safety state: rig down (nothing was running at all), routing on DAW (so no Main Mix
feedback path inside the device), tones at -40 dBFS.

```
$ python3 -I quiet_sweep.py sweep.wav -40
sweep.wav: -40.0 dBFS (amplitude 327/32767), 1.0s per channel, 0.5s gaps
order: out 1 220Hz, out 2 330Hz, out 3 440Hz, out 4 550Hz
$ arecord -D plughw:1,0 -c 10 -f S16_LE -r 48000 -d 9 loop-iface.wav &
$ sleep 1; aplay -D plughw:1,0 sweep.wav
```

Neither `aplay` nor `arecord` reported the device busy, so PipeWire had released it and
these reached the hardware. `pactl` is not installed on this machine, so the brief's
`pactl suspend-sink`/`suspend-source` steps could not be run as written; they turned out
not to be needed.

All ten inputs, not just the two the brief asks for:

```
input 1: 9.0s, block floor -79.8 dBFS
   1.00- 2.05s  rms   -26.0 dBFS  pitch 220Hz -> out 1
input 2: 9.0s, block floor -81.4 dBFS
  no bursts above the floor -- nothing came through the loop
input 3..10: block floor -120.0 dBFS
  no bursts above the floor -- nothing came through the loop
```

### A wrong first reading, and the correction

This was first read as "the output end must be in Output 1, because the headphone tones
(440/550 Hz) arrived nowhere". **That inference was invalid**, and the reason is worth
recording because it is a trap this routing sets.

`quiet_sweep.py` labels its four tones by *stream* channel — out 1..4 = 220/330/440/550
Hz. But the DAW routing applied in this very session is `00 01 00 01`, which points
**both** the mains **and** the headphones at DAW 1 and DAW 2. So under this routing the
headphone socket carries 220 and 330 Hz, and the 440/550 Hz tones are routed to nothing
at all. That is the whole point of the routing fix. The sweep's channel labels therefore
stop naming physical outputs the moment this routing is in force, and "no 440 Hz" says
nothing whatever about the headphone socket.

### Which socket, established properly

Two discriminating tests, each changing one output's source and re-running the same
sweep, so the returned *pitch* names the socket.

**Test 1 — point headphone L at DAW 3 (440 Hz), change nothing else:**

```
$ ./idctl set 0x33 6 2 1 2
 1/1  entity 0x33 selector 0x06 channel 0x02  bytes 02     set: ok
input 1:  1.00- 2.05s  rms -26.0 dBFS  pitch 220Hz -> out 1
```

Unchanged, same pitch and the same level to 0.1 dB. **So the cable is not on the
headphones** — had it been, input 1 would have turned into 440 Hz.

**Test 2 — point Main L at DAW 2 (330 Hz), change nothing else:**

```
$ ./idctl set 0x33 6 0 1 1
 1/1  entity 0x33 selector 0x06 channel 0x00  bytes 01     set: ok
input 1:  2.50- 3.55s  rms -26.0 dBFS  pitch 330Hz -> out 2
```

Input 1 followed it: a different pitch, **and a different arrival time** (2.50 s rather
than 1.00 s, since DAW 2's tone is the second burst in the sweep), at the same level.

**Conclusion: input 1 is fed by Output 1 and by nothing else.** It tracks whatever Main
L is pointed at, and no content of Main R ever reaches it.

Routing was restored with `./idctl apply daw` after each test (4 writes ok).

### Why a stereo cable still cannot loop stereo here

Nothing is wrong with the cable, and nothing is wrong with the interface. The iD14's
main speaker outputs are **two separate jacks, one per channel** — Output 1 is Main L,
Output 2 is Main R. On a balanced TRS output the tip and ring carry the *hot and cold of
one signal*, not left and right. So a stereo lead in Output 1 is carrying Main L twice,
in opposite polarity; the right channel is on a different socket that the cable is not
in. The measurements above are exactly that, and nothing else fits them.

The destination compounds it: the DI input is a single mono input, so even two arriving
signals would sum into one.

**What this loop can and cannot answer:**

- It **can** confirm that one output passes audio at the hardware level, with PipeWire
  out of the path. Output 1 does: -40 dBFS sent, -26.0 dBFS returned, the +14 dB being
  the DI input's own gain.
- It **cannot** compare the two outputs, which is what Q4 asks. That needs the signal
  to reach two inputs.

**To loop stereo, two cables are needed**: Output 1 -> Input 1 and Output 2 -> Input 2.
Alternatively a TRS-to-dual-TS insert lead from the headphone socket into Inputs 1 and 2,
which is the brief's "insert/Y lead" row and the 3.5 mm lead that could not be found on
3 Nov. With one cable into one mono input, the brief's "One cable only" row applies: one
output at a time, Output 2 measurable only by moving the output end and changing nothing
else.

This is the mistake the brief warns about in its B1 table — four "still mono" readings on
3 Nov came from reading a one-sided loop as a stereo test — so no stereo conclusion is
drawn from it here.

## B4. Round-trip latency: the instrument returned an impossible figure

Ran as the brief asks, at both period sizes. The runs themselves were clean — no
xruns, and the linked trigger skew was essentially zero, which is the check the
measurement depends on:

```
$ gcc -O2 -o rtlat rtlat.c -lasound -lm
$ ./rtlat hw:1,0 128 2 -20 rt128 48000 4 10 S32_LE
linked=1   trigger_skew_us=0.7   trigger_skew_frames=0.0
delay_play_frames=205  delay_cap_frames=64  delay_sum_ms=5.60
emit_at=36096  frames_captured=96000  xruns=0
$ ./rtlat hw:1,0 256 2 -20 rt256 48000 4 10 S32_LE
linked=1   trigger_skew_us=0.6   trigger_skew_frames=0.0
delay_play_frames=433  delay_cap_frames=32  delay_sum_ms=9.69
emit_at=36096  frames_captured=96000  xruns=0
```

```
$ python3 -I rtlat_analyse.py rt128 36096 48000 10 S32_LE
  L: round trip       8.0 frames =    0.17 ms   peak/background    126.0  ok
  R: round trip   11691.0 frames =  243.56 ms   peak/background      6.2  WEAK -- do not trust
$ python3 -I rtlat_analyse.py rt256 36096 48000 10 S32_LE
  L: round trip      29.0 frames =    0.60 ms   peak/background   1754.1  ok
  R: round trip   -8970.0 frames = -186.88 ms   peak/background      7.3  WEAK -- do not trust
```

The R column is input 2, which the cable does not feed — those are correlation noise at
ratio ~6, correctly flagged weak, and carry no meaning.

**The L figures are the problem. 8 frames (0.17 ms) is not a possible analogue round
trip**: it would require the iD14's DAC, the cable, its ADC and two USB traversals to
cost essentially nothing, when the device's own `snd_pcm_delay` reports 5.60 ms of
buffering in the same run. The two runs also disagree with each other by 21 frames.

The peak is strong, not weak, so the analyser's own "do not trust" guard does not fire.
This is a different failure from the one that guard was built for, so it was checked
directly rather than taken at face value — the capture was searched for *all* its
correlation peaks, not just the largest:

```
period 128, input 1:
    frame   36104  ratio     123.3  offset       +8 =    +0.17 ms
    frame   23255  ratio       6.3  offset   -12841 =  -267.52 ms
    frame   16553  ratio       6.2  offset   -19543 =  -407.15 ms
period 256, input 1:
    frame   36125  ratio    1715.4  offset      +29 =    +0.60 ms
    frame   38904  ratio       6.1  offset    +2808 =   +58.50 ms
    frame   47962  ratio       5.7  offset   +11866 =  +247.21 ms
```

There is exactly **one** real arrival, at +8/+29 frames; everything else sits at the
~6 noise ratio. So there is no second, later arrival that is the "real" one — the chirp
genuinely appears in the capture almost immediately in stream-position terms.

**Conclusion: this is a fault in the measurement's arithmetic on this device, not an
iD14 latency of 0.17 ms, and no latency figure is being reported from it.** Two things
make that the honest reading rather than a remarkable result:

- 0.17 ms is below what the hardware can physically do.
- The same instrument returned ~57 ms on the Nanocore, which is implausibly *high* for
  a USB interface at a 128-frame period. One device reading far too low and another far
  too high points at the shared assumption — that a `snd_pcm_link`'d pair makes output
  frame N and input frame N simultaneous — rather than at either device.

That assumption should be validated against a known delay before any figure from
`rtlat` is quoted, including the Nanocore's 57 ms, which this casts doubt on. Q6 is
open, and the level was kept at −20 dBFS as instructed.

## Still to run

- **A2, A3** — the rig's own stereo and the restart behaviour. These need the cable
  **out**: with the speaker output looped into the DI input and an amp sim running, the
  rig's own output feeds back into its input. That is the brief's B0 warning, and it is
  why Part A and this cable cannot be in place at the same time.
- **B3** — replug, in the "rule not installed" form.
- **B2 on output 2** — needs the cable's output end moved from Output 1 to Output 2.

## Later the same day: two cables, and what the right channel's fault actually is

Two mono cables on the back panel, Output 1 -> Input 1 and Output 2 -> Input 2,
48V off, rig down. Measured with `stereo_check.py` (this directory), which plays
a different pitch on every output at once; every run is in
`stereo-check-log.jsonl`.

**On a freshly plugged iD14, with no routing written, ALSA's `Speaker Playback
Volume` attenuates the right output only. The left output ignores it.** PipeWire
drives that control from its sink volume, so any sink volume under 100% turns
the right side down and leaves the left alone. At 18%, where it was, that is
44 dB: "left only".

Fresh replug, nothing applied, ALSA direct. "Loss" is received minus sent:

| `Speaker Playback Volume` (4 values, dB) | sent | left loss | right loss |
|---|---|---|---|
| -44, -44, -44, -44 (as PipeWire left it) | -20 | -37.6 | -80.6 |
| -22, -22, -22, -22 | -42 | -37.6 | -58.6 |
| 0, 0, 0, 0 | -64 | -37.9 | -36.7 |
| 0, -44, -44, -44 | -42 | -37.7 | at the floor |
| -44, 0, -44, -44 | -42 | -37.6 | -36.3 |
| -44, -44, 0, 0 | -20 | -37.6 | -80.6 |

The left's loss never moves (it is the monitor knob at about half, plus the
input gain). The right's is the same figure plus the control's second value,
dB for dB. The first, third and fourth values do nothing to either output.

The same through PipeWire, still with no routing written:

| PipeWire sink volume | left | right | verdict |
|---|---|---|---|
| 18% | -58.3 | -101.2 | left only |
| 50% | -57.6 | -74.3 | unbalanced, 16.7 dB |
| 100% | -57.6 | -56.3 | PASS, stereo |

So there are two ways to stereo, and they are not equivalent:

- **Sink volume at 100% (control at 0 dB), default routing.** Stereo within
  1.3 dB, and the monitor knob still sets the level.
- **`idctl apply daw`.** Stereo within 1.3 dB (measured before the replug), but
  the same tone came back 52 dB louder than before the apply, with the control
  still reading -44 dB: this routing bypasses the volume control, and by the
  level, the knob's attenuation too. Whether the knob does anything at all in
  this routing was not tested.

Also settled: a USB reset does not clear the routing (`idctl`'s own teardown
resets the device and stereo held after it); a replug does. `sudo` is not
available to this session, so unbind/rebind was not tried.

Not yet tested: the rig itself through the loop, and whether this is the whole
of "sometimes no stereo when I start the rig".

### The fix chosen: PipeWire does the volume in software

`51-id14-soft-volume.conf` (this directory, installed to
`~/.config/wireplumber/wireplumber.conf.d/`) sets `api.alsa.soft-mixer` on the
iD14, so PipeWire never writes `Speaker Playback Volume`; the control was set to
0 dB by hand. Default routing, no `idctl`, through PipeWire at -14 dBFS:

| sink volume | hardware control | left | right | verdict |
|---|---|---|---|---|
| 18% | 127 (0 dB) | -96.5 | -95.1 | PASS |
| 50% | 127 (0 dB) | -69.7 | -68.3 | PASS |
| 100% | 127 (0 dB) | -51.6 | -50.3 | PASS |

Not yet tested: what the hardware control reads after a replug, now that
PipeWire no longer sets it.

### After a replug with the rule in place: the control comes back at -44 dB

Replugged with `api.alsa.soft-mixer` set. `Speaker Playback Volume` read
83,83,83,83 (-44 dB) and both layers were left-only again, right 42.7 dB under.
PipeWire did not write it (the rule held: sink at 100%, control at 83). The
value is the system's saved ALSA state: `/var/lib/alsa/asound.state` has
`state.iD14` with `Speaker Playback Volume` at 83, and udev's
`90-alsa-restore.rules` runs `alsactl restore` on every connect.

Setting the control back to 127 gave stereo again at once. To make it stick,
the saved state has to hold 127, which needs root:

    sudo alsactl store

(run while the control reads 127). The state is also stored at shutdown, so it
would correct itself at the next clean shutdown with the control at 127.

### After `sudo alsactl store` and another replug: stereo with nothing done by hand

Sonny ran `sudo alsactl store` with the control at 127, then replugged. The
saved state now holds 127 and the control came back at 127,127,127,127. No
`idctl`, no `amixer`, sink volume 100%, sent at -20 dBFS:

| layer | left | right | verdict |
|---|---|---|---|
| iD14 alone | -62.6 | -61.3 | PASS |
| through PipeWire | -62.6 | -61.3 | PASS |

So the interface's stereo now rests on two things on this laptop: the
WirePlumber rule (PipeWire never writes the control) and the saved ALSA state
(the control restored at 0 dB on connect). Neither is in the repository's
power to install; both are written down in `51-id14-soft-volume.conf`.

## The rig itself through the loop (Q1, Q2)

Started with `machines/loopback-test.yaml`, whose guitar and vocal inputs are
two generated signals (`stereo_check.py tones`), not the interface's inputs, so
the loop cannot feed the rig. `stereo_check.py rig` checks Sushi's links, then
records the interface's two inputs. All `--direct --headless --no-tuner
--no-amp`, default routing, software volume at 100%.

| Run | Links | Guitar L−R | Vocal L−R | Verdict |
|---|---|---|---|---|
| guitar_vocal, first start | FL, FR | -0.5 dB | -0.9 dB | PASS |
| guitar_vocal, `restart` ×4 | FL, FR each time | -0.5 to -0.6 | -0.9 to -1.0 | PASS ×4 |
| electric-clean, cold start ×3 | FL, FR each time | +0.3 | (one channel) | PASS ×3 |
| guitar_vocal, cold start after electric-clean ×3 | FL, FR each time | -0.4 to -0.5 | -0.9 to -1.0 | PASS ×3 |

The bare loop is -1.3 dB left minus right, so the rig is balanced to within a
decibel of the cables. Stereo was not lost once in eleven starts.

Each plugin of guitar_vocal's two chains alone on a channel, and an empty
chain, also came back within 0.1 dB of the bare loop, MaGigaverb within 1.2:
the copy to stereo after a one-output plugin does its job in the live engine,
and a mono input does reach both halves of its channel.

**A trap, recorded so it is not fallen into again.** The first version of this
test sent one steady pitch per input and read guitar_vocal as 5.6 dB (guitar)
and 15.4 dB (vocal) left-heavy, inside Sushi, with correct links. One plugin at
a time put it on MVerb (17 dB) and a-reverb (6 dB). It was the stimulus: a
stereo reverb's two sides differ at any single frequency. With eight pitches
per input spread over five octaves, the same plugins and the same rig are
balanced, which is what the offline sweep in `tests/test_stereo.py` finds.
The single-pitch rows are still in the log, labelled without "8 pitches".

Two things about the test rather than the rig: qpwgraph does not make the
patchbay's links from the tone streams (`up` warns of 2 links not made), so
`rig` makes those two itself with `pw-link`; and Sushi runs as `sushi.bin`.

Not covered: starting through the agent and the web UI, the amp (Carla) in the
path, a second qpwgraph open on the desktop, and the real machine file's
inputs.

## The agent's routes, the amp, and a second qpwgraph

Same loop and signals. The rig was started `--direct` with
`machines/loopback-test.yaml`, then `sushi-rig agent` was started, which took
that machine file from the state file and carried it through every switch and
restart (checked in `/health` each time). Switches were `POST /rig/switch`, the
route the web UI calls; the UI itself was not clicked.

| Run | Links | Guitar L−R | Vocal L−R | Verdict |
|---|---|---|---|---|
| electric-clean with the amp (Neve 2), cold start, direct | FL, FR | +0.3 dB | – | PASS |
| agent: switch electric-clean → guitar_vocal (by restart) | FL, FR | -0.6 | -1.0 | PASS |
| agent: switch guitar_vocal → electric-clean with the amp | FL, FR | +0.3 | – | PASS |
| agent: `sushi-rig restart`, electric-clean with the amp | FL, FR | +0.3 | – | PASS |
| guitar_vocal, a second `qpwgraph -m` launched beside the rig's | FL, FR | -0.4 | -1.0 | PASS |
| the same, restarted through the agent | FL, FR | -0.6 | -1.0 | PASS |

On the one-channel rig with the amp, the vocal's pitches read about -90 dBFS on
both sides, 29 dB under the guitar: products of the amp's distortion landing on
them, not a vocal channel. The verdict line counts it as a second input; it is
not one.

**The second qpwgraph did not stay.** Launched with no `-a` while the rig's own
was up, with `qpwgraph.conf` saying `Activated=true`, `Exclusive=false` and a
remembered `Path`: four seconds later only the rig's qpwgraph was in the process
list, and the second had printed nothing. Why it left was not established. The
links did not change. So suspect 1 of the 7 Nov brief could not be made to
happen here.

**Found on the way: the amp is wired from the host's machine file, not the
run's.** `amp.prepare` calls `guitar_port(root)`, which reads
`machines/<hostname>.yaml` whatever `--machine` the run was given
(`amp.py`, `guitar_port`). With `--machine machines/loopback-test.yaml` the
guitar's port is the tone stream, the host file's `capture_AUX0` feeds nothing
in the generated patchbay, and `rig-amp.qpwgraph` came out with no link to or
from `NAM` at all: the amp ran, unwired, and the guitar would have gone in dry.
On this laptop's own machine file the two agree, so it does not bite in normal
use. For these runs `stereo_check.py rig` made the amp's two links itself, as
`amp_patchbay` would have.

Across the session: 17 starts, restarts and switches of the rig, by the command
line and by the agent, with and without the amp, and stereo was not lost once.
The one cause of "left only" found today is the interface's volume control.

### Fixed: the amp is wired from the run's machine file

`amp.prepare` and `guitar_port` now take the run's machine file, and `rig.up`
passes the one the rig was compiled against. Started again with
`--machine machines/loopback-test.yaml`, `rig-amp.qpwgraph` holds
`stereo-check-guitar:output_MONO -> NAM:Input` and
`NAM:Output -> sushi:audio_input_0`, and qpwgraph made the second of those
itself before the test touched anything. Through the loop: guitar +0.3 dB left
minus right, PASS. `tests/test_amp.py` and `tests/test_rig.py` cover it.

## Note added later on 12 Nov 2026, after review: how to read four logged verdicts

Nothing above and nothing in `stereo-check-log.jsonl` has been changed. This is
about what four of its lines mean.

**Lines 71, 75, 77 and 83 of the log** (`rig`, at 18:18:32, 18:19:39, 18:20:02
and 18:43:23: the four runs of one-channel electric-clean with the amp) say
"PASS: 2 rig input(s), each on both sides equally". **It is one input.** The
second is the amp's distortion of the guitar's pitches landing on the vocal's
(2 × 310 − 190 = 430 Hz, and five more of the eight), at -90 to -93 dBFS, 28 to
29 dB under the guitar. There was no vocal channel in that rig. The paragraph
under the table in "The agent's routes, the amp, and a second qpwgraph" says so
already; the verdict lines themselves do not. The guitar's left-minus-right in
those lines stands.

**The same four PASSes say nothing about whether the rig wired its own amp.**
`rig` made the amp's two links itself whenever `NAM:Input` was in the graph.
Lines 71, 75 and 77 were measured before the machine-file fix, with no link to
or from `NAM` in `rig-amp.qpwgraph`: they are a PASS of an amp wired by the
test. Line 83 is after the fix, and the text above records that qpwgraph had
made `NAM:Output -> sushi:audio_input_0` before the test touched anything, but
the verdict would have read the same had it not.

`stereo_check.py rig` was changed after the review so that neither can happen
again: the caller names the rig's inputs (`--inputs`), an input not named is
never counted, and the amp's links are made only when `--wire-amp` asks, with
the verdict then saying so. The two sets of pitches were changed as well (the
tool says why), so levels in the lines above are not pitch for pitch what a
run from here on reads; such runs carry `expected` and `pitches_hz` in the
log. **None of the changed tool has been run on the interface yet.**
