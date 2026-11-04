# What the Windows capture settled, and what it corrects

Captured on the Windows machine 2026-11-04 (Audient driver 5.1.0, panel 4.3.19, USBPcap 1.5.4)
following `WINDOWS-SESSION-BRIEF.md`. The capture came back with a write-up by the Claude session
on that machine; **this file records what was independently re-derived from the raw `.pcapng`
here**, which parts are taken on report, and what it means for the rig.

Supersedes several conclusions in `RESULTS-id14-mixer-2026-11-03.md`. The corrections are at the
end — three of them are mine and were wrong for the same reason.

## The headline

**The iD14 has four analogue outputs in two pairs, and the headphones are outputs 3 and 4 —
routing channels 2 and 3.** MixiD addresses channels 4 and 5 for its "HP L / HP R" rows, which do
not exist on this unit. That is why rows 5–6 did nothing and the headphones stayed as they were.

Audient's own routing page shows it plainly — two rows, `STEREO 1 + 2` and `headphones 3 + 4`, and
only three sources: Main Mix, Cue Mix, DAW Thru. MixiD's six rows and five columns belong to a
larger iD model.

**The fix needs no code: set MixiD's rows 3 and 4 — the ones it labels "Channel 3" and "Channel 4"
— to DAW Mix.** In that column row 3 sends channel 2 value `0x02` and row 4 sends channel 3 value
`0x03`, which is byte-for-byte what Windows sends for headphone DAW Thru. Untested on Linux.

## Verified here from the raw capture

Parsed directly out of `id14-panel.pcapng` with `usbpcap.py` (no tshark on this machine; the
parser is in the scratchpad, and the one subtlety is that each pcapng block ends with a repeated
length field which must be consumed or every block after the first is misaligned).

405,629 class control transfers to the iD14. All writes `bmRequestType` 0x21 / `bRequest` 0x01,
reads 0xA1, `wValue = (selector << 8) | channel`, `wIndex = (entity << 8) | interface`.

### Routing — entity 0x33, selector 0x06, 1 byte, channels 0–3 only

| Output | Channel | Main Mix | Cue Mix | DAW Thru |
|---|---|---|---|---|
| Main L | 0 | `14` | `10` | `00` |
| Main R | 1 | `15` | `11` | `01` |
| **Headphone L** | **2** | `18` | `10` | `02` |
| **Headphone R** | **3** | `19` | `11` | `03` |

Every routing write in the capture, in time order, confirms it: at launch `14 15 18 19`; the mains
swept to Cue (`10 11`) and back, the headphones swept to Cue (`10 11`) and back, the mains to DAW
Thru (`00 01`) and back, the headphones to DAW Thru (`02 03`) and back. **Channels 2 and 3 are
always written as a pair with distinct left and right values.** There is no channel 4 or 5.

### Levels are standard UAC2 volume — signed 8.8 fixed-point dB

Not a proprietary scale. All nine values the knob log asked for decode exactly:

| asked | 0 | −5 | −10 | −20 | −30 | −40 | −60 | −80 | −128 |
|---|---|---|---|---|---|---|---|---|---|
| on the wire | `0000` | `00fb` | `00f6` | `00ec` | `00e2` | `00d8` | `00c4` | `00b0` | `0080` |

Little-endian signed, divided by 256. `0x8000` is −128 dB, which in UAC2 means silence.

### Entity 0x36, the monitor section

Selectors **written** in the capture: 0x00, 0x01, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08, 0x0c, 0x10,
0x12, 0x17. Selectors **polled** about 19 times a second: 0x00, 0x03, 0x04, 0x05, 0x07, 0x0c, 0x12
(20,261 reads each over the session).

| Selector | Bytes | Function |
|---|---|---|
| 0x00 | 1 | Mono |
| 0x01 | 1 | Mono mode — C `00`, L `01`, R `02` (the panel's MONO MODE L/C/R) |
| 0x03 | 1 | Polarity |
| 0x04 | 1 | Speaker mute |
| 0x05 | 1 | Dim on/off |
| **0x06** | **2** | **Dim level** — the panel's TRIM SETTINGS / DIM knob, in dB |
| 0x07 | 1 | Talkback |
| 0x08 | 1 | Talkback source — external `03`, internal `ff` |
| 0x0c | 1 | Alt |
| **0x12** | **2** | **Main monitor volume**, in dB — swept, and polled to follow the encoder |

### Other entities

| Entity / selector / channel | Direction | Function |
|---|---|---|
| 0x0A / 0x01 / 3, 4 | written | Headphone mute, both channels |
| 0x0A / 0x02 / 3 | **read only** | Headphone volume, follows the encoder. Windows never writes it |
| 0x0C / 0x02 / 1, 2 | written `0000` | Main Mix master |
| 0x0C / 0x02 / 3, 4 | swept | Cue master |
| 0x3C / 0x01 | written, 2 bytes | Mixer matrix crosspoints |
| 0x3C / 0x00 | polled, `bRequest` 0x03 | Meters |
| 0x29 / 0x01 | polled, 4 bytes | Sample rate (`44AC0000` = 44100) |

### The knob and the iD button send nothing

No control writes and no interrupt traffic originate from the device. It changes its own state and
the panel discovers it by polling. So there is no event to listen for on Linux — a host-side
control has to poll too, or simply not care.

## Taken from the Windows write-up, not re-derived here

Believable and consistent with what I did check, but recorded as second-hand:

- **Matrix addressing**: entity 0x3C selector 0x01, channel = `input × 4 + output`, where output is
  0 Main L, 1 Main R, 2 Cue L, 3 Cue R; inputs 0–1 Mic, 2–9 Digital, 10–13 DAW 1–4. So DAW 1 → Main
  L is channel `0x28`, DAW 2 → Main R is `0x2D`.
- **Pan is baked into the crosspoint values**, so a fader at 0 dB panned centre writes −6 dB to both
  sides. The panel's saved state has DAW 1 and 2 unlinked and centred, which is **a mono sum** — and
  the mixer screenshot does show both faders up and centred.
- **At plug-in the driver writes no routing at all** — only sample rate and interface selection. The
  routing arrives when the panel launches, which matches the manual's instruction to run the app
  once after power-up.
- Cold-start behaviour differed between the two captures in which crosspoints got written; the
  write-up flags this as unexplained and recommends writing everything rather than relying on it.

## What this explains about the Linux symptoms

**Why only DAW Mix ever worked.** MixiD's values for this device are wrong in every column but one:

| MixiD column | What MixiD sends (ch 0) | What the iD14 wants (ch 0) |
|---|---|---|
| Main Mix | `0x1b` | `0x14` |
| Alt Spkr | `0x1d` | — no such source |
| Cue A | `0x19` | `0x10` |
| Cue B | `0x1a` | — no such source |
| **DAW Mix** | **`0x00`** | **`0x00`** ✓ |

In the DAW Mix column the value is just the channel index, which happens to be exactly right. Every
other column sends a value this firmware does not use — hence "Main Mix carries no computer audio".
That was never a property of Main Mix; it was a wrong byte.

**Why the monitor knob does nothing.** DAW Thru bypasses the monitor section by design, and DAW Thru
is the only routing we had working. Main Mix should restore the knob — but Main Mix is fed from the
mixer matrix, and MixiD cannot write the matrix at all.

**Why the headphones were mono.** Channels 4 and 5 do not exist. Rows 5–6 wrote into nothing.

## Corrections to RESULTS-id14-mixer-2026-11-03.md

Three of my conclusions there were wrong, and all three for the same root cause — I was decoding
with MixiD's encoding, which is itself wrong:

1. **"Selector 0x06 holds `0xfff1`, which is 99.95% under MixiD's encoding — the shape of a monitor
   volume at maximum."** Wrong. 0x06 is the **dim level**, and `0xfff1` in the real encoding is
   −0.06 dB, a dim trim sitting at roughly zero. The agreement with "99.95%" was a coincidence of
   applying a linear scale to a logarithmic field.
2. **"MixiD writes volume to the wrong selector — 0x12 reads zero and refuses writes."** Wrong on
   the first half. **0x12 is exactly the right selector.** The `00 00` we read back is a legitimate
   value: **0 dB**, full scale. It was never evidence of a dead control.
3. **"MixiD's encoding: `float_to_u16(v) = -32768 + 32767*v`."** That is MixiD's code, but it does
   not match the device, which wants signed 8.8 dB. A MixiD volume slider would therefore write
   nonsense even on the right selector.

What stands from that file: the entity 0x36 selector list, that routing writes work on interface 0,
that claiming interface 0 costs the card's PCM devices, and that the DFU interface never answers.

## Then tested on Linux, same day — the headphones work

**Solved.** The headphone output gives proper stereo from ordinary audio, through PipeWire, with
a working level control. What it took:

**1. The headphones are rows 3 and 4.** Confirmed by ear. MixiD's "HP L / HP R" rows address
channels 4 and 5, which do not exist; its rows 3 and 4 are routing channels 2 and 3.

**2. Source `0x03` is dead on this unit.** This is the part the Windows capture could not have
told us, because Windows never checked whether audio arrived. Established by elimination, each
step holding everything else constant:

| test | result | what it ruled out |
|---|---|---|
| rows 3+4 → DAW Mix (`0x02`, `0x03`) | left only | — |
| same tone on output 3, then output 4 | first only | the pitch, the level, the test file |
| output 4 driven from source `0x02` | **both ears** | output 4 being dead in hardware |
| output 3 driven from source `0x03` | **silent** | the output; isolates the source byte |

So the firmware **accepts `0x03`, acknowledges the write, and passes nothing**, on an output
proven good moments earlier. `0x02` works on either output. Audient's panel sends `0x02`/`0x03`
because it assumes a four-output DAW rig where the headphones carry DAW 3+4 — it has no reason to
notice.

**3. The fix: point the headphones at stream channels 1 and 2.** The source byte and the output
channel are independent — channel 3 accepted `0x02` quite happily — so there is no obligation to
copy Audient's pairing. Sending `0x00` and `0x01` to channels 2 and 3 feeds the headphones from
the same pair the main outputs take, which is what a laptop playing stereo actually wants. This is
now MixiD's DAW Mix column for rows 3 and 4 in the local branch.

**The monitor knob works on the headphones**, which contradicts what this file predicted a few
sections above. DAW Thru does bypass the monitor section, but **headphone level is a separate
feature unit** (0x0A selector 0x02, not 0x36), and that one is not bypassed. So the headphone path
has a hardware level control after all. There is an audible crackle while turning it; the likely
cause is the device stepping gain in 1 dB increments — the capture's GET RANGE reported a 1 dB
step — but that is unverified.

**Routing cannot be read back.** Every `GET_CUR` on entity 0x33 returns `LIBUSB_ERROR_PIPE`
(STALL), for all six channels. Checking the capture again, there are only `OUT` rows for 0x33 and
never an `IN`: **Audient's panel never reads the routing either.** So MixiD's grid reflecting
nothing is forced by the firmware, not a shortcoming — the panel copes by remembering what it
sent. Worth having tried, and worth recording so nobody tries again.

### The per-session step, updated

Replaces the one in `RESULTS-id14-mixer-2026-11-03.md`. After every USB connect:

1. `cd ~/src/MixiD/Release && ./MixiD` (branch `rig/id14-mki-interface0`)
2. **Connect**
3. Rows **1 and 2** → **DAW Mix** for the monitors; rows **3 and 4** → **DAW Mix** for the
   headphones. Each write now logs its row, column and byte — expect `0x00` and `0x01` on both
   pairs.
4. Quit from the **window's close button**, or the card does not come back.

## What to do next, in order of cost

1. **Headphone stereo, no code.** MixiD, Connect, rows **3 and 4** to **DAW Mix**. If the headphones
   come up stereo, that is the headphone problem solved, and it costs one launch.
2. **Monitor knob, needs matrix writes.** Route mains to Main Mix (`14`/`15`), write the matrix so
   DAW 1 → Main L (`0x28`) and DAW 2 → Main R (`0x2D`) at 0 dB with everything else at `0080`, and
   set 0x0C/0x02 channels 1 and 2 to `0000`. MixiD has no matrix UI, so this needs `idctl` — which
   still costs the sound card on every invocation. **A batch mode that claims interface 0 once and
   does all the writes is now clearly worth building**, because we finally know what to write.
3. **Software headphone volume.** Writing 0x0A/0x02 channels 3 and 4 is untested *even on Windows*,
   where the panel only ever reads it. Speculative.

Nothing in section 1 of this file has been tested on Linux. Keep the monitors down for the first
attempt at item 2: an unwritten matrix with Main Mix routing has not been seen from this host.

## Source files

On the Windows machine's drive at `Box/Workspace`: `id14-panel.pcapng` (54 MB, capture 1, filtered
to the iD14), `id14-connect.pcapng` (159 MB, whole hub, cold plug-in), `id14-panel-rescue.pcapng`
(3.3 GB, unfiltered — not needed), `id14-capture-log.csv`, `id14-state-before.xml`, and the two
panel screenshots. The write-up, the action log and the screenshots are copied in beside this file;
the captures are not, being far too large for the repo.
