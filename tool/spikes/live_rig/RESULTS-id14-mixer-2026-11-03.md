# The iD14's silent outputs: cause, fix, and the step it now needs every session

Rig laptop `sonny-dewfall-XPS-9320`, 2026-11-03. Follows
[RESULTS-stereo-2026-11-02.md](RESULTS-stereo-2026-11-02.md), which cleared the repo and ended
with the interface itself as the only remaining suspect.

## What was wrong

**Three of the Audient iD14's four outputs were passing nothing.** Output 1 worked. Outputs 2, 3
and 4 were silent, or so far down as to be inaudible at a normal monitor level.

The cause is the interface's **internal mixer routing**: each output selects its source from
Main Mix / Alt Spkr / Cue A / Cue B / DAW Mix. On Windows, Audient's control panel sets this at
every launch. On Linux nothing sets it, and the device's default feeds only output 1.

It is not a hardware fault, not the cables, not the amp, and nothing in this repo.

## How that was established

Each step removed a layer for good, so none of it needs repeating:

| Claim | Evidence |
|---|---|
| Not the repo's code | Sushi's own output measured **L-R = 0.0 dB** while playing; the rig's offline render matched CI exactly (-16.11/-16.13) |
| Not WirePlumber state | Saved `channelVolumes` identical for FL and FR: `[0.002970, 0.002970, 0.046650, 0.046650]` |
| Not the ALSA mixer | `Speaker` equal across all four channels (76/76/76/76) throughout |
| **Not PipeWire at all** | The decisive test: `speaker-test -D hw:1,0 -c 4` and `aplay -D hw:1,0`, bypassing PipeWire entirely, still gave one output |
| Not the hardware | Balanced on other laptops, and on this laptop booted to Windows |
| The device's mixer | Two routing writes through MixiD brought outputs 1 and 2 live, twice, reproducibly |

The confirming measurement was a generated 4-channel file, one tone per physical output at a
known level, played straight to `hw:1,0` — see `quiet_sweep.py` alongside this file. Rising pitch
per output (220/330/440/550 Hz) so the channels are distinguishable by ear, 20 ms fades, and an
amplitude set in dBFS rather than left at full scale.

## The step it needs every session

**The routing does not persist.** It is lost on every USB disconnect, which is why the Windows
trip left nothing behind. Measured directly: both outputs live at -40 dBFS before a replug,
nothing at -40 dBFS and left-only at -25 dBFS after it, with nothing else changed.

So after every connect:

1. `cd ~/src/MixiD/Release && ./MixiD` (branch `rig/id14-mki-interface0`)
2. Press **Connect**. Audio drops — it claims interface 0 exclusively and the iD14 leaves
   PipeWire until it is released.
3. **Routing** from the menu, then **DAW Mix** for **Channel 1** and **Channel 2** (Main L and
   Main R). Watch the terminal: a successful write prints nothing, a failure names its
   `wValue`/`wIndex`.
4. Quit **from the window's own close button.** Only a clean exit reattaches `snd-usb-audio`.

## Constraints found the hard way

- **It must be interface 0.** MixiD's HEAD (`052e166`) sends the requests to the spare DFU
  interface so playback continues; the firmware on this MKI never answers there — the first
  request times out and the endpoint returns `LIBUSB_ERROR_IO` for everything after. The local
  branch defaults to interface 0; `MIXID_IFACE` overrides.
- **Killing MixiD costs you the sound card.** Interface 0 is left with no driver and the iD14
  disappears from `aplay -l`. Rebind without power-cycling:
  `echo -n '3-1.2.1:1.0' | sudo tee /sys/bus/usb/drivers/snd-usb-audio/bind`
- **The volume controls do not work on this unit**, on either interface. `wValue=0x1200` fails
  with `LIBUSB_ERROR_IO` every time. Routing (`wValue` 0x0600-0x0605) is the only thing that lands.
- **DAW Mix bypasses both of the device's own level stages.** The monitor knob does nothing —
  tested by sweeping it through its whole range against a looping -40 dBFS tone — and ALSA's
  `Speaker` control sits at -51 dB while attenuating nothing. Both are stages inside the
  interface, and this source routes around them.
- **PipeWire's own volume still works**, because it scales the samples before they reach the USB
  cable, where nothing in the device can route around it. `0.05` was inaudible and `0.40` was
  not. So the desktop slider and the volume keys are the working level control on this rig, and
  the hardware knob is not. **Set a ceiling there.** A full-scale stream reaching an unattenuated
  sink is what made this painfully loud once already; generate test tones at a stated dBFS
  (`quiet_sweep.py`) rather than using `speaker-test`, which plays near full scale.
- **`Main Mix` carries no computer audio** — it is the device's internal monitor mix, which
  Audient's software populates and nothing on Linux does, so selecting it gives silence. It was
  the obvious candidate for keeping the knob alive, and it does not work. `DAW Mix` is the only
  source found that passes the laptop's output.
- **Needs a udev rule**, or MixiD cannot open the device and says nothing about it:
  `SUBSYSTEM=="usb", ATTR{idVendor}=="2708", MODE="0660", GROUP="plugdev"` in
  `/etc/udev/rules.d/84-audient.rules`. `plugdev`, not `audio`, on this machine.
- **The iD14 registers as a mouse.** Its HID interface appears as `mouse4` with relative-axis
  events, so the knob moves the pointer. Unbind with
  `echo -n '3-1.2.1:1.4' | sudo tee /sys/bus/usb/drivers/usbhid/unbind`; nothing we use needs it.

## Every routing combination tried

Rows 1-2 are the main outputs, 5-6 the headphones. Results by ear, except where noted.

| rows | source | result |
|---|---|---|
| 1, 2 | Main Mix | **silence** -- carries no computer audio at all |
| 1, 2 | **DAW Mix** | **stereo, working.** This is the fix for the monitors |
| 5, 6 | Main Mix | headphones left only |
| 5, 6 | DAW Mix | headphones left only |

So the monitors are solved and the headphone output is not. Both headphone attempts gave
the left side alone, with the right carrying nothing. **Not the headphones** -- two
different pairs behave the same, and the right driver is fine on another source.

Untried on rows 5-6: `Alt Spkr`, `Cue A`, `Cue B`. Picking among them is guesswork,
which is why the Windows capture below is the better next move than three more passes.

What the pattern suggests: `DAW Mix` taps the computer's channels directly, which is why
it works on the main pair and why nothing in the device attenuates it. `Main Mix` is the
device's own bus, which Audient's software populates and Linux does not -- empty on the
main rows, half-fed on the headphone rows.

### A measurement that could not work

The loopback was a **mono jack in a stereo headphone socket**, so it carried the tip
alone and could only ever see the left side. Four "still mono" readings were taken
through it before that was noticed; none of them was evidence about stereo, since a
perfectly working stereo output would have measured identically. The loop is still valid
for **level** on one channel, which is what the control sweeps below rest on. Testing
stereo needs an insert lead splitting tip and ring to two inputs, or ears.

## The control protocol, as far as it is known

These are UAC2 class requests on the AudioControl interface, not vendor opcodes:
`bmRequestType` 0x21/0xA1, `bRequest` 0x01 (SET_CUR) or 0x81 (GET_CUR),
`wValue` = (selector << 8) | channel, `wIndex` = (entity << 8) | interface.
`idctl.c` alongside this file sends them without a GUI, so a change can sit inside a
measurement loop; `bursts.py` names which output arrived by the pitch that came back.

**Entity 0x36 is the master section, and it both reads and writes.** Every write below
was confirmed by reading the value back. Channel number is ignored — 0, 1 and 2 all
return the same value.

| selector | bytes | at rest | what MixiD calls it |
|---|---|---|---|
| 0x00 | 1 | 00 | Mono |
| 0x01 | 1 | 00 | — |
| 0x03 | 1 | 00 | Phase |
| 0x04 | 1 | 00 | Speaker Mute |
| 0x05 | 1 | 00 | Dim |
| **0x06** | **2** | **f1 ff** | — |
| 0x07 | 1 | 00 | Talkback |
| 0x08 | 1 | 00 | — |
| 0x0c | 1 | 00 | Alt |
| 0x12 | 2 | 00 00 | *speaker volume* |
| 0x15 | 1 | 00 | — |

**MixiD writes volume to the wrong selector.** It uses 0x12, which reads zero and
refuses writes. Selector **0x06** holds `0xfff1`, and under MixiD's own encoding
(`-32768 + 32767 * volume`) that is 99.95% — the shape and resting value of a monitor
volume at maximum. Not yet confirmed to affect audio.

No other entity answers a read at all: 0x0a, 0x0b, 0x0c, 0x28, 0x32, 0x33, 0x37 and
0x3c were all scanned across selectors 0x00-0x20 and returned nothing. So the vendor
extension units are the whole control surface, and 0x36 is the only one that talks.

**Getting a write in without losing the card is the unsolved part.** Three routes, all
closed:

- **The DFU interface** gives empty reads and timeouts. These are *interface-targeted*
  class requests, so the recipient has to be the AudioControl interface; the DFU
  interface is not it. MixiD's `052e166` is simply wrong for this device, and an earlier
  retraction of that conclusion in this file was itself wrong.
- **Not claiming the interface** gives `LIBUSB_ERROR_IO`: the kernel owns interface 0
  and will not pass userspace control transfers to it.
- **Claiming interface 0** works, but detaches `snd-usb-audio`, and the card keeps its
  shell while losing every PCM device — `/proc/asound/card1/` holds only `id`, `usbbus`,
  `usbid`, `usbmixer`. A plain reattach does not rebuild them and nor did
  `libusb_reset_device`; it takes a replug, which resets the routing. MixiD survives the
  same trick, so the difference is probably that it claims **once** per session while
  `idctl` claims per invocation. A batch mode that claims once is the obvious next try.

## Still open

- **The headphone output has no working source yet.** `Alt Spkr`, `Cue A` and `Cue B` remain
  untried on rows 5-6; `Main Mix` and `DAW Mix` both give the left side alone.
- **No level control was found anywhere.** Both ALSA feature-unit controls are inert in the
  default routing *and* with DAW Mix active — measured, eight conditions each time, flat to
  0.1 dB. Entity 0x36 selector 0x06 is still the best candidate but writing it needs interface
  0, which costs the card. PipeWire's own volume works and is all there is for now.
- **Outputs 3 and 4** (`engine_bus 1`) are untested — nothing was patched to them.
- **Whether a reboot behaves differently from a replug.** Expected to be the same or worse.
- MixiD logs the routing grid's row but not the column, so the terminal does not record which
  source was selected. It also cannot read device state back, so the grid reflects nothing.
- **The Windows capture would settle most of this.** Everything above is inference from a
  device that answers in single bytes; Audient's own panel knows the real selectors. Wireshark
  with USBPcap on the iD14, then one control at a time with a pause between each -- monitor
  volume, mono on and off, headphone volume, each routing selection -- and note the order. Each
  control is a 9-byte control transfer, so the log maps straight onto the table above and tells
  us which selector carries volume, which carries stereo, and how the panel sets things at
  launch. An hour of Windows time is worth more than another day of probing here.

## Patches to MixiD

In `~/src/MixiD`, pushed to `github.com/SonnyDewfall/MixiD`:

- `loud-usb-failures` — control transfers bounded to 1 s (upstream passes `0`, which libusb reads
  as wait-forever: an unanswered request froze the UI with nothing printed, which read as a
  crash), each failure named by `wValue`/`wIndex`, and a failed open reported instead of silently
  leaving the UI looking connected. Proposed upstream as
  [TheOnlyJoey/MixiD#31](https://github.com/TheOnlyJoey/MixiD/pull/31).
- `rig/id14-mki-interface0` — the above plus the interface-0 default and `MIXID_IFACE`. Kept out
  of the PR: it would take the DFU path's benefit away from devices where it works.
