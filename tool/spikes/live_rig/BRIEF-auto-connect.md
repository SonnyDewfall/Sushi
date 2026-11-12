# Brief: apply the iD14's routing automatically on connect

For a cloud session. **Branch from `claude/ui-tests` in `github.com/SonnyDewfall/Sushi`**, e.g.
`claude/id14-auto-connect`. Do not commit to `master`.

## Read this first

**You will not be able to test any of this.** The Audient iD14 is attached to a physical laptop
(`sonny-dewfall-XPS-9320`) that you have no access to. Everything here must be written to be
reviewed and then verified by hand on that machine. So:

- Make it compile. `gcc -O2 -Wall -Wextra` clean.
- Do not claim anything works. Say what is untested, which is all of the runtime behaviour.
- Prefer boring, inspectable code over clever code. Someone will be debugging this against
  hardware with no debugger and a device that disappears when you get it wrong.
- Where you have to guess, write the guess down in a comment and in your summary.

## The problem

Three of the iD14's four analogue outputs pass nothing until the device's internal mixer routing
is set, and **the routing resets every time the device loses USB power**. Audient's Windows panel
re-applies it at every launch; nothing on Linux does. Today the fix is to launch a GUI (MixiD) and
click four cells after every reconnect.

The goal is to make that automatic: plug the interface in, routing applies itself, no GUI.

Background, in `tool/spikes/live_rig/`: `RESULTS-id14-windows-2026-11-04.md` is the protocol and
how it was established; `RESULTS-id14-mixer-2026-11-03.md` is the earlier work, with three
conclusions struck through as wrong. You need neither to do this job, but read the first if
anything below seems arbitrary.

## Exactly what has to be written to the device

Four USB control transfers. These are UAC2 class requests on the AudioControl interface, not
vendor opcodes.

```
bmRequestType 0x21   bRequest 0x01 (SET_CUR)   wLength 1
wIndex  = (entity << 8) | interface  = 0x3300    (entity 0x33, interface 0)
wValue  = (selector << 8) | channel  = 0x06NN    (selector 0x06, channel NN)
```

| wValue | channel | physical output | data byte | meaning |
|---|---|---|---|---|
| `0x0600` | 0 | Main L | `0x00` | takes stream channel 1 |
| `0x0601` | 1 | Main R | `0x01` | takes stream channel 2 |
| `0x0602` | 2 | Headphone L | `0x00` | takes stream channel 1 |
| `0x0603` | 3 | Headphone R | `0x01` | takes stream channel 2 |

**Only channels 0–3 exist.** Do not write channels 4 or 5; they are not present on this unit.

**The headphone values are deliberately not Audient's.** Their panel sends `0x02`/`0x03` there,
which is DAW 3+4 — correct for a four-output DAW rig. On this unit **source `0x03` is accepted,
acknowledged, and passes no audio**, verified on an output shown working with `0x02` moments
before. Feeding the headphones from stream channels 1 and 2, the same pair as the mains, means
ordinary stereo reaches both pairs. Do not "correct" these to match Audient.

Device is USB `2708:0002`.

## Deliverables

All under `tool/spikes/live_rig/autoconnect/` unless noted.

### 1. A headless writer

`idctl.c` already exists one directory up and has the protocol, the claim path and the right
teardown, but it claims and releases per invocation. Either add a batch mode to it or write a
small dedicated tool — your call, but say which and why. A dedicated tool is probably cleaner here
since this one runs unattended.

It must:

- Claim interface 0 **once**, do all four writes, release once.
- Call `libusb_set_auto_detach_kernel_driver(dev, 1)` **before** claiming. Claiming interface 0
  detaches `snd-usb-audio`, so the card leaves ALSA while held.
- Tear down in exactly this order, which is what MixiD does and what demonstrably returns the card
  to ALSA with its PCM devices intact:
  ```c
  libusb_reset_device(dev);
  libusb_release_interface(dev, 0);
  libusb_attach_kernel_driver(dev, 0);
  ```
  Getting this wrong leaves interface 0 with no driver and the card vanishes from `aplay -l` until
  a physical replug. That is the single worst failure mode here.
- Exit non-zero with a clear message on any failure, naming the `wValue`/`wIndex` that failed.
- Be idempotent. It will be run more than once.
- Do nothing and exit 0 if the device is absent.

### 2. Handle the races, because there are three

**The kernel driver may not have bound yet.** udev fires when the USB device enumerates, which can
be before `snd-usb-audio` is ready. Retry with a bounded backoff — something like six attempts over
ten seconds — rather than a fixed sleep. Give up cleanly.

**`libusb_reset_device` can re-trigger udev.** A USB reset may cause re-enumeration, which can fire
the same rule again and loop. Guard against it: a lock file, a short cooldown keyed on the device's
bus/address, or a check for a recent successful run. Pick one, and explain the choice. Do not
assume it cannot happen.

**MixiD may be holding the device.** If interface 0 is already claimed, the tool must fail
gracefully and exit non-zero without touching anything — never force it.

Note for your own reasoning: the reset does **not** wipe the routing just written. That is
established empirically — MixiD does the same thing and the routing survives its exit.

### 3. udev rule

The device presents several interfaces, so a naive rule fires several times. Match the device, not
the interfaces:

```
ACTION=="add", SUBSYSTEM=="usb", ENV{DEVTYPE}=="usb_device", \
  ATTR{idVendor}=="2708", ATTR{idProduct}=="0002", \
  TAG+="systemd", ENV{SYSTEMD_WANTS}="id14-routing.service"
```

**Do not use `RUN+=`** to run the writer directly. udev kills long-running `RUN` processes (around
30 seconds) and runs them in a constrained environment; with a retry loop this tool will exceed
that. Hand off to systemd.

There is an existing rule at `/etc/udev/rules.d/84-audient.rules` granting `plugdev` access to
`2708:*`. Leave it alone; add a separate file.

### 4. systemd unit

A `Type=oneshot` service that runs the writer. Running as root is acceptable and simplest. State
in the README why you chose system scope over user scope.

### 5. Install script

`install.sh` and matching uninstall, both idempotent, both printing what they are about to do.
Copies the binary, the rule and the unit into place, runs `udevadm control --reload-rules` and
`systemctl daemon-reload`. Must not need editing before use.

### 6. README

A short one in the same directory: what it does, how to install, how to check it worked, how to
remove it, and **an explicit list of what has never been run against hardware**.

## Constraints

- Change nothing outside `tool/spikes/live_rig/autoconnect/` except adding one line to
  `tool/spikes/live_rig/README.md` pointing at it.
- No new runtime dependencies beyond `libusb-1.0`, which is already used.
- Do not modify `idctl.c` beyond what the job needs; if you add a batch mode there, keep the
  existing subcommands working.
- Do not touch the MixiD clone. Different repository, and the GUI path stays as the fallback.

## Acceptance criteria

1. Builds clean under `gcc -O2 -Wall -Wextra`.
2. A dry-run mode (`--dry-run` or similar) prints the exact transfers without opening the device,
   so the values can be reviewed without hardware.
3. The install script is readable end to end by someone who will run it as root on their own
   laptop.
4. Your summary states plainly which parts are untested, and lists every assumption you made about
   udev or systemd behaviour that you could not verify.

## How it will be verified on the rig

Write this into the README as the acceptance test, because whoever runs it will not have this
brief to hand.

1. `./id14-apply-routing --dry-run` — check the four transfers match the table above.
2. Install. Unplug the iD14, wait five seconds, plug it back in.
3. `aplay -l` must still list the iD14 with its PCM device. **If the card is missing, the teardown
   is wrong** — that is the headline failure and needs a replug to recover.
4. `systemctl status id14-routing` — one clean oneshot run, no loop. Check `journalctl` for repeat
   firings, which would mean the reset is re-triggering udev.
5. Play a stereo test and confirm both the monitors and the headphones give left and right:
   ```
   python3 -I ../quiet_sweep.py /tmp/t.wav -25 && aplay -D hw:1,0 /tmp/t.wav
   ```
   That file puts a different tone on each of the four outputs. Expect 220 Hz and 330 Hz on the
   monitors; with this routing the headphones carry the same first two tones, not the third and
   fourth. Keep the level low — `quiet_sweep.py` defaults to −40 dBFS for a reason.
6. Confirm the routing cannot be read back: every `GET_CUR` on entity 0x33 stalls with
   `LIBUSB_ERROR_PIPE`. This is expected, and is why there is no verification step that asks the
   device what it holds. Audient's own panel never reads it either.

## What not to do

- Do not add a daemon, a tray app, or anything that runs continuously. This is four control
  transfers on a hotplug event.
- Do not try to read the routing back to verify. It stalls. Confirmed on hardware and in a capture
  of Audient's own panel.
- Do not widen the udev rule to all `2708:*` devices. Other iD models have different channel maps
  and these values would be wrong, and possibly silently wrong.
- Do not handle the mixer matrix, monitor volume, or the Main Mix path. Separate problem, not yet
  solved, and out of scope here.
