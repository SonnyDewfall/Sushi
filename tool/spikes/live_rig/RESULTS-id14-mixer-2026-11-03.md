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

## Still open

- **`Alt Spkr`, `Cue A` and `Cue B` are untried.** One of them may carry the computer's output
  *and* sit under the monitor knob, which would be better than DAW Mix. `Main Mix` was the
  likeliest and it was silent, so this is not a promising line.
- **Outputs 3 and 4** (`engine_bus 1`) are untested — nothing was patched to them.
- **Whether a reboot behaves differently from a replug.** Expected to be the same or worse.
- MixiD logs the routing grid's row but not the column, so the terminal does not record which
  source was selected. It also cannot read device state back, so the grid reflects nothing.

## Patches to MixiD

In `~/src/MixiD`, pushed to `github.com/SonnyDewfall/MixiD`:

- `loud-usb-failures` — control transfers bounded to 1 s (upstream passes `0`, which libusb reads
  as wait-forever: an unanswered request froze the UI with nothing printed, which read as a
  crash), each failure named by `wValue`/`wIndex`, and a failed open reported instead of silently
  leaving the UI looking connected. Proposed upstream as
  [TheOnlyJoey/MixiD#31](https://github.com/TheOnlyJoey/MixiD/pull/31).
- `rig/id14-mki-interface0` — the above plus the interface-0 default and `MIXID_IFACE`. Kept out
  of the PR: it would take the DFU path's benefit away from devices where it works.
