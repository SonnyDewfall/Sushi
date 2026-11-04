# iD14 control protocol: Windows capture results, 2026-11-04

Captured on Windows 11 with Audient driver 5.1.0 and iD panel 4.3.19, using USBPcap 1.5.4 and
Wireshark 4.6.9. **Nothing here has been tested on Linux yet.** Section 1 is what was observed.
Sections 4 and 5 are inference and are labelled as such.

## Files

| File | What it is |
|---|---|
| `id14-panel.pcapng` | Capture 1, filtered to the iD14: panel launch and every control below |
| `id14-connect.pcapng` | Capture 2, whole hub: USB plug-in, then panel launch 27 s later |
| `id14-capture-log.csv` | UTC timestamp of each action in Capture 1 |
| `id14-state-before.xml` | The panel's saved state before the run |
| `id14-system-panel.png`, `id14-mixer.png` | The panel windows in their normal state |
| `id14-panel-rescue.pcapng` | Capture 1 unfiltered (3.3 GB). Not needed unless the filter is in doubt |

The iD14 is device 8 in Capture 1 and device 14 in Capture 2.

**Reading the log:** each `set` row is written after its 1.2 s wait, so a value's USB write comes
just *before* its row. A press row is written straight after the press, so its write comes just
after. The `CORRECTION` row and the `ERROR` rows mark actions that did not happen. The DAW presses at
22:30:48 and 22:31:03 only opened a warning dialog; the real DAW Thru writes are at 22:38:35 and
22:38:47.

## 1. Observed protocol

All writes are `bmRequestType` 0x21, `bRequest` 0x01, reads are 0xA1, with
`wValue = (selector << 8) | channel` and `wIndex = (entity << 8) | 0`.

**Levels** are signed 8.8 fixed-point dB, low byte first, 2 bytes on the wire. Confirmed against
values set and read back in the panel:

| dB | 0 | -5 | -10 | -20 | -30 | -40 | -60 | -80 | off (-128) |
|---|---|---|---|---|---|---|---|---|---|
| bytes | `00 00` | `00 FB` | `00 F6` | `00 EC` | `00 E2` | `00 D8` | `00 C4` | `00 B0` | `00 80` |

### Routing: entity 0x33, selector 0x06, 1 byte

| Output | Channel | Main Mix | Cue Mix | DAW Thru |
|---|---|---|---|---|
| Main L | 0 | `14` | `10` | `00` |
| Main R | 1 | `15` | `11` | `01` |
| Headphones L | 2 | `18` | `10` | `02` |
| Headphones R | 3 | `19` | `11` | `03` |

### Monitor controls: entity 0x36

| Selector | Bytes | Function | Evidence |
|---|---|---|---|
| 0x00 | 1 | Mono | Toggled; also set by the iD button |
| 0x01 | 1 | Mono Mode: C `00`, L `01`, R `02` | Toggled |
| 0x03 | 1 | Polarity | Toggled |
| 0x04 | 1 | Speaker mute | Toggled |
| 0x05 | 1 | Dim on/off, **by earlier notes only** | Only `00` seen at launch. Not toggled |
| 0x06 | 2 | **Dim level, not volume** | Swept |
| 0x07 | 1 | Talkback | Toggled |
| 0x08 | 1 | Talkback source: external `03`, internal `ff` | Toggled |
| 0x0c | 1 | Alt, **by earlier notes only** | Only `00` seen at launch |
| 0x10 | 2 | Unknown | `0000` at launch in Capture 1 |
| 0x12 | 2 | **Main volume** | Swept; reads back the encoder position too |
| 0x17 (ch 1) | 2 | Unknown | `0000` at launch |

### Headphones and output levels

| Entity / selector / channel | Bytes | Function | Evidence |
|---|---|---|---|
| 0x0A / 0x01 / 3, 4 | 1 | Headphone mute | Panel writes both channels |
| 0x0A / 0x02 / 3 | 2 | **Headphone volume** | Read only: follows the encoder, -128 to -32 to -97 dB. Writing it, and channel 4, not tested |
| 0x0C / 0x02 / 1, 2 | 2 | Main Mix master, by position | `0000` at launch. Not swept |
| 0x0C / 0x02 / 3, 4 | 2 | Cue master | Swept. Range read: min -127, max 0, step 1 dB as reported by GET RANGE (`0100 0081 0000 0001`) |

### Mixer matrix: entity 0x3C, selector 0x01, 2 bytes

14 inputs at 4 channels each. Channel = `input × 4 + output`, where output is 0 Main L, 1 Main R,
2 Cue L, 3 Cue R. Inputs: 0–1 Mic, 2–9 Digital, 10–13 DAW 1–4. Confirmed by sweeps for DAW 1–4; the
rest follows the pattern.

| Input | Main L, R | Cue L, R |
|---|---|---|
| DAW 1 | 0x28, 0x29 | 0x2A, 0x2B |
| DAW 2 | 0x2C, 0x2D | 0x2E, 0x2F |
| DAW 3 | 0x30, 0x31 | 0x32, 0x33 |
| DAW 4 | 0x34, 0x35 | 0x36, 0x37 |

Pan is applied in these values, so a fader at 0 dB panned centre writes -6 dB (`00 FA`) to both
sides. Hard left writes `00 00` to L and `00 80` to R. Pan 0.25 writes L -2.5 dB (`83 FD`) and
R -12 dB (`FE F3`).

**Current panel state is a mono sum.** DAW 1 and 2 are unlinked and panned centre, so each feeds
both sides at -6 dB. Pressing the channel link sends true stereo: DAW 1 to Main L only, DAW 2 to
Main R only, each at 0 dB. It was put back afterwards.

### Hardware controls send nothing

The encoder and iD button produced **no control writes and no interrupt traffic** from the iD14. The
device changes its own state and the panel sees it by polling, about 19 times a second:
`0x36` selectors 0x00, 0x03, 0x04, 0x05, 0x07, 0x0c, 0x12; `0x0A` selectors 0x01, 0x02; meters on
`0x3C` selector 0x00 with `bRequest` 0x03.

## 2. What happens at connect (Capture 2)

**Plug-in, driver only:** standard enumeration, then only `0x29/0x01` (sample rate, `44AC0000`,
44100 Hz) and interface selection. **No routing, no mixer writes, no monitor writes** in the 27 s
before the panel opened. This matches the manual, page 34: launch the app once after power-up, and
the setting persists after it quits.

**Panel launch, in order:**

```
0x36/0x08      = 03          talkback source
0x36/0x17 ch1  = 0000        unknown
0x01/0x00      = 00000000    unknown
0x33/0x06 ch0-3 = 14 15 18 19   routing
0x36/0x08      = ff, then 03, with writes to DAW 4's crosspoints 0x34-0x37
```

## 3. Unknowns worth remembering

- **Crosspoints at cold start.** Capture 2's launch wrote only DAW 4's crosspoints, leaving the Main
  Mix ones (0x28 and so on) unwritten. Capture 1's launch wrote the whole matrix, 171 writes, framed
  by `0x01/0x00 = 1` and `= 0`. The reason for the difference is not known. A Linux init should write
  everything rather than rely on either.
- **What survives a power-off.** Capture 2 was a true cold start: USB power only, no 12 V supply, so
  unplugging removed power. `0x36/0x12` still read -126 dB when the panel first polled it, close to
  the -124 dB seen at the end of Capture 1, with no write to it in between. So main volume looks to be
  stored in the device, unless the encoder was moved in between. Since the panel left the Main Mix
  crosspoints unwritten after this power-off, they may be stored too, or they may have reset to
  defaults. Audio output was not checked after Capture 2, so neither is confirmed.
- **Not captured:** Dim and Alt being switched, talkback assigned to other inputs, digital input
  format, clock source, and sample-rate changes.
- `0x36/0x10`, `0x36/0x17` and `0x01/0x00` have unknown functions.

## 4. Likely explanations (inference, not tested)

- **Mono headphones.** The two sides need separate writes: channel 2 = `18`, channel 3 = `19`. Check
  what MixiD's rows 5–6 actually send. If it does not write channel 3 with `19`, that fits the
  symptom. The matrix is the next suspect, since Main R needs DAW 2 to reach 0x2D.
- **Dead monitor knob.** The manual says DAW Thru bypasses the monitor section, so a working route
  that is really `00`/`01` would ignore the knob. Main Mix (`14`/`15`) should bring it back. If the
  knob stays dead, this explanation is wrong.
- **Flat ALSA volume.** Headphone volume is on feature unit 0x0A at channel 3, so check which unit
  and channels the ALSA controls write. A route that bypasses the unit would also measure flat.
- **`0x12` reading back zero on Linux.** It is the right selector and reads back correctly on
  Windows, so zero may be a real 0 dB (`00 00`). Moving the encoder and re-reading will tell.

## 5. Suggested first Linux test

Keep the speakers low first: DAW Thru and an unset matrix can behave unexpectedly, and these values
have never been sent from Linux. Write in this order:

1. `0x36/0x12` = `00 C4` (main volume -60 dB).
2. Matrix: all 56 channels to `00 80`, then 0x28 = `00 00` and 0x2D = `00 00`.
3. `0x0C/0x02` ch 1, 2 = `00 00`.
4. `0x33/0x06` ch 0–3 = `14 15 18 19`.

Then check:

- Is the headphone output stereo with a left/right test signal?
- Does the monitor knob change the level on the main outputs?
- Does `0x36/0x12` follow the encoder, and `0x0A/0x02` ch 3 in headphone mode?

If the headphones are still mono, compare against MixiD's writes next. If all of that works, also
try writing `0x0A/0x02` on channels 3 and 4 as a software headphone volume.
