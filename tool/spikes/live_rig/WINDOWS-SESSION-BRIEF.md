# What to capture in a Windows session

Two unrelated questions, both of which Windows can answer and Linux cannot. Do them in either
order; the iD14 capture is the one that unblocks real work, so if you only have time for one, do
that. Budget about 45 minutes for the iD14 part and 15 for the Nanocore.

Background for each is in `RESULTS-id14-mixer-2026-11-03.md` and
`RESULTS-nanocore-2026-11-03.md` alongside this file. You do not need to read them to follow this.

---

# Part 1 — the iD14's control protocol (the important one)

## Why

On Linux, three of the iD14's four outputs pass nothing until its internal mixer routing is set,
and that routing resets on every USB connect. MixiD can set the routing for the **main outputs**,
which is why your monitors work. Two things are still broken:

- **The headphone output comes back mono.** Rows 5–6 give the left side alone on both `Main Mix`
  and `DAW Mix`. It is not the headphones — two pairs behave the same.
- **There is no working volume control on the device.** Both ALSA feature-unit controls measured
  flat to 0.1 dB, and the monitor knob does nothing in the routing that works. PipeWire's software
  volume is all there is.

Everything known about the protocol was inferred from a device that answers in single bytes.
Audient's own panel knows the real selectors. One capture of it working settles both questions.

## Setup

1. **Install Wireshark** and, in the installer's component list, **tick USBPcap**. It is not
   selected by default. Reboot afterwards — USBPcap installs a driver.
2. **Run Wireshark as Administrator.** USBPcap interfaces do not appear otherwise.
3. Plug in the iD14. Install and launch Audient's iD control panel once to confirm it works, then
   **close it again** — step 2 of the capture needs its launch.
4. If it is easy, unplug other USB devices from the same hub. It keeps the capture readable.

## Finding the right interface

The capture list will show `USBPcap1`, `USBPcap2`, … one per root hub, not one per device. To find
which hub the iD14 is on, open each one's options and look at the device list — the iD14 should be
named. **Select only the iD14** if the dialog lets you; capture the whole hub only if it does not.

Note the iD14's **device address** from the device list. You will want it for filtering.

## The capture

The method matters more than the speed. **One control at a time, with about five seconds of doing
nothing between each.** The pauses are what makes the log readable — they turn it into clearly
separated bursts instead of one stream you cannot attribute. Keep a numbered list as you go.

Do not play any audio during this. Audio streaming generates enormous isochronous traffic that
will bury the control transfers and make the file huge.

1. **Start the capture.** Wait 5 s doing nothing.
2. **Launch the Audient panel.** ← *the single most valuable moment in the whole capture.* This is
   the initialisation that Windows does at every launch and Linux never does. Wait 10 s.
3. **Monitor volume knob** — turn slowly from minimum to maximum, pause, then back down. Wait 5 s.
4. **Mono** on, wait 2 s, off. Wait 5 s.
5. **Dim** on, wait 2 s, off. Wait 5 s.
6. **Polarity / phase** on, wait 2 s, off. Wait 5 s.
7. **Speaker mute** on, wait 2 s, off. Wait 5 s.
8. **Headphone volume** — minimum to maximum, then back. Wait 5 s.
9. **Headphone routing** — set the headphone source to each option in turn, pausing 5 s after each:
   `Main Mix`, `Alt Spkr`, `Cue A`, `Cue B`, `DAW Mix`. ← *this is what answers the mono headphone
   problem.* Note which one the panel has it on normally.
10. **Main output routing** — the same five, pausing 5 s after each. Wait 5 s.
11. **Stop the capture** and save as `.pcapng`.

While you are in the panel, also take **a screenshot of the routing matrix in its normal working
state**, so we know what the target configuration actually is. MixiD cannot read device state back,
so we have never seen what "correct" looks like.

## Trimming it before you send it

The file may be large. In Wireshark, apply this display filter:

```
usb.transfer_type == 2 && usb.device_address == N
```

replacing `N` with the iD14's address. That leaves only control transfers to the iD14, which is all
we need. Then **File → Export Specified Packets → All packets (Displayed)** and save that. It
should be small. Zip it if it is not.

## What I will do with it

Each control is a 9-byte control transfer with `bmRequestType` 0x21, `bRequest` 0x01, a `wValue`
of `(selector << 8) | channel` and a `wIndex` of `(entity << 8) | interface`. That maps straight
onto the table we built by probing, which currently reads:

| selector on entity 0x36 | bytes | what we think it is |
|---|---|---|
| 0x00 | 1 | Mono |
| 0x03 | 1 | Phase |
| 0x04 | 1 | Speaker Mute |
| 0x05 | 1 | Dim |
| **0x06** | **2** | unknown, holds `0xfff1` — best guess at the real volume |
| 0x07 | 1 | Talkback |
| 0x0c | 1 | Alt |
| 0x12 | 2 | *what MixiD writes volume to, and it reads back zero* |

The capture tells us which selector actually carries volume, what the headphone rows need that we
are not sending, and the full launch-time initialisation sequence. That is the difference between
guessing and knowing.

---

# Part 2 — is the Nanocore's latency the hardware or Linux?

## Why

Measured on Linux over your loopback cable: **round-trip latency ~57 ms, and changing the buffer
barely moves it** (a 4× buffer increase moved it 0.5 ms). That is five to ten times a usable figure
for playing through amp models, and it is not a setting — the ALSA buffer is almost entirely out of
that path.

What that measurement cannot tell us is *whose* fault it is. On Linux the device appears as
full-speed USB 1.1, UAC1, **44100 Hz only**, 16-bit. If Windows gets a much better figure, the
hardware is fine and Linux's handling of it is the problem, which would be worth chasing. If
Windows is just as bad, the device is simply slow and we drop it.

## What to do

You have a loopback cable that fits the Nanocore — that is all this needs.

1. Download **RTL Utility** (Oblique Audio, free). It is the standard round-trip latency tester:
   it plays a pulse, hears it back through the loopback, and reports the true round trip.
2. Plug the loopback from the Nanocore's output to its input, as you had it here.
3. **Check what driver you are using.** If Ember supply an ASIO driver, use it — that is the
   device's real capability. If not, use WASAPI in exclusive mode. Note which one you used; it
   changes the answer and we need to know which we are comparing.
4. Set the buffer to **128 samples** if the driver allows it, to match the Linux measurement.
5. Run RTL Utility and note the round trip it reports.
6. Repeat at 256 and at whatever the smallest allowed buffer is.

## Also worth thirty seconds

**Does Windows offer it at 48 kHz?** Windows Settings → System → Sound → the Nanocore → device
properties → supported formats. On Linux it declares 44100 only, which forces a resampler into the
rig's path permanently, since the rig runs at 48000. If Windows offers 48 kHz, the device has a
capability Linux is not reaching and that is worth knowing too.

## What to write down

| | |
|---|---|
| Driver used | ASIO / WASAPI exclusive / other |
| Buffer size | |
| Round trip reported | ms |
| Same at 256 | ms |
| Sample rates Windows offers | |

## How to read the answer

- **Round trip 10–20 ms** → the hardware is fine and Linux is mishandling it. Worth another look,
  because the Nanocore has one thing the iD14 lacks: a real hardware volume control (`PCM`, genuine
  dB steps, which the iD14's feature units refuse to provide).
- **Round trip 45–60 ms** → the device is inherently slow. Drop it for the rig and keep the iD14.

---

# Getting it back

- The iD14 `.pcapng` (filtered as above) and the panel screenshot.
- The Nanocore table filled in.

Anything shared works — the capture should be small once filtered. If the unfiltered file is the
only one you end up with and it is hundreds of megabytes, say so before sending it and I will give
you a tighter filter.
