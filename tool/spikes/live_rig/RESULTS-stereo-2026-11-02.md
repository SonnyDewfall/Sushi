# Where the right channel is lost — 2026-11-02

Machine: sonny-dewfall-XPS-9320, Audient iD14, electric guitar.
Code under test: `claude/stereo-out` at `fabbc12`.
Results branch: `claude/ui-tests`.

**Answer: the right channel is not lost in the code. It is lost after the
interface's output.** With Sushi stopped and not running at all, PipeWire
alone cannot get sound to the right monitor. Every measurement of the branch
came out correct.

---

## Step 1 — THE CODE IS THE BRANCH'S — PASSED

```
$ cd ~/Sushi && git status -sb && git log --oneline -1
## claude/stereo-out...origin/claude/stereo-out
fabbc12 Put a mono plugin's output on both channels, in the compiler

$ tool/.venv/bin/python -c "import sushi_rig, sushi_rig.stereo; print(sushi_rig.__file__)"
/home/sonny-dewfall/Sushi/tool/src/sushi_rig/__init__.py
/home/sonny-dewfall/Sushi/tool/src/sushi_rig/stereo.py
```

No ImportError, and both modules resolve inside `~/Sushi/tool/src`. The
editable install follows the checkout.

### The checkout is one commit behind origin — and it does not matter

```
$ git log --oneline fabbc12..origin/claude/stereo-out
6f44261 The rigs' references, and the copy to stereo after review
```

Checked rather than assumed, because "the laptop runs something different"
was the brief's first hypothesis. The diff to `stereo.py` is **review
hardening only**:

- `read_ports` takes ASCII digits via a regex instead of `str.isdigit`
  (which accepts "²" and "٣" and then crashes `int`)
- `load_ports` wraps `OSError`/`UnicodeDecodeError` in `PortsError`
- a docstring paragraph on undoing a failed two-command add/remove/move

None of that can turn a silent channel into a sounding one. And the commit
message says its references came "from CI's candidates **for fabbc12**" — the
very commit this laptop runs. So CI and the laptop agree on the commit, and
the hypothesis is dead.

---

## Step 2 — THE INTERFACE'S RIGHT OUTPUT — **FAILED. This is the fault.**

Rig stopped (`sushi-rig down` -> "Rig offline."), so Sushi was not running.

```
$ speaker-test -D pipewire -c 2 -t wav -l 1
Playback device is pipewire
Stream parameters are 48000Hz, S16_LE, 2 channels
 0 - Front Left
 1 - Front Right
```

Asked whether "Front Right" came from the right speaker:

> **"No, just heard front left."**

Pushed further, because a four-output surround profile on a two-output
interface is worth ruling out before blaming hardware:

```
$ speaker-test -D pipewire -c 4 -t wav -l 1
 0 - Front Left
 1 - Front Right
 3 - Rear Right
 2 - Rear Left
```

> **"I just head front left."**

Nothing reaches the right speaker on **any** of the four positions, with
Sushi out of the path entirely. Per the brief, that is where this stops: the
fault is the interface, the monitors, the cables or the headphones.

### What was ruled out while here

The ALSA mixer shows no left/right imbalance — the pairs are matched, and the
only difference is between front and rear:

```
$ amixer -c 1
Simple mixer control 'Speaker',0
  Playback channels: Front Left - Front Right - Rear Left - Rear Right
  Front Left:  Playback 77  [61%] [-50.00dB]
  Front Right: Playback 77  [61%] [-50.00dB]
  Rear Left:   Playback 101 [80%] [-26.00dB]
  Rear Right:  Playback 101 [80%] [-26.00dB]
```

So nothing in software has muted or attenuated the right channel.

### Worth passing on: the device profile

```
$ wpctl inspect 95
    api.alsa.path = "surround40:1"
    audio.channels = "4"
    audio.position = "[ FL, FR, RL, RR ]"
    device.profile.description = "Analog Surround 4.0"
    device.profile.name = "analog-surround-40"
  * node.description = "Audient iD14 Analog Surround 4.0"
```

PipeWire is driving the iD14 as a **four-output surround device**. That is
not the cause here — a misrouted right monitor would have sounded on one of
the two Rear announcements, and nothing did — but it is why `playback_RL` and
`playback_RR` exist, and it is worth knowing when reading
`machines/sonny-dewfall-XPS-9320.yaml`, which pairs `playback_FL` and
`playback_FR` positionally.

### What is left to try, physically

Swap the two monitor cables at the interface. If the silence follows the
cable, it is the cable; if it stays on the right, it is that monitor; if it
moves to the left, it is the interface's output.

---

## Step 3 — THE COMPILED CONFIG HAS THE COPIES — PASSED

Rig restarted with `sushi-rig up --rig electric-chorus-2 --headless`.

```
$ sushi-rig status
electric-chorus-2 (headless mode), up 6s
  config     /run/user/1000/sushi-rig/rigs/electric-chorus-2.json
  ...

$ grep -o '"name": "[^"]*_to_stereo"' /run/user/1000/sushi-rig/rigs/electric-chorus-2.json
"name": "compressor_to_stereo"
"name": "overdrive_to_stereo"
"name": "octave_down_to_stereo"
"name": "octave_up_to_stereo"

$ grep -c mono_summing /run/user/1000/sushi-rig/rigs/electric-chorus-2.json
4

$ curl -s http://localhost:8029/live | grep -o '"[A-Za-z0-9_]*_to_stereo"' | sort -u
"compressor_to_stereo"
"octave_down_to_stereo"
"octave_up_to_stereo"
"overdrive_to_stereo"
```

One copy per mono plugin — compressor, the TS9 overdrive, and both detunes —
present in the compiled config and in the live view. The rig running on this
laptop is compiled by the branch, with the copies.

---

## Step 5 — THE LINKS — PASSED, and one of the brief's expectations is wrong

Run before step 4 because it needs no playing. Note that
`pw-link -l | grep ...` as written cannot answer this: grep keeps the
indented link lines and throws away the node header that names the far end.
The listing below carries the header onto each line:

```
$ pw-link -l | awk '/^[^ ]/{node=$0} /\|[<->]/{print node" \t"$0}' | grep -iE 'sushi|nam|id14'
alsa_output.usb-Audient_Audient_iD14-00.analog-surround-40:playback_FL 	  |<- sushi:audio_output_0
alsa_output.usb-Audient_Audient_iD14-00.analog-surround-40:playback_FR 	  |<- sushi:audio_output_1
alsa_capture.fmit:input_MONO 	  |<- alsa_input.usb-Audient_Audient_iD14-00.multichannel-input:capture_AUX0
sushi:audio_input_0 	  |<- NAM:Output
NAM:Input 	  |<- alsa_input.usb-Audient_Audient_iD14-00.multichannel-input:capture_AUX0
```

Outputs are exactly as expected: `audio_output_0` -> `playback_FL`,
`audio_output_1` -> `playback_FR`.

The patchbay the rig loaded, in full:

```xml
<!DOCTYPE patchbay>
<patchbay name="rig" version="0.9.9">
 <items>
  <item node-type="pipewire" port-type="pipewire-audio">
   <output node="NAM" port="NAM:Output"/>
   <input node="sushi" port="sushi:audio_input_0"/>
  </item>
<item node-type="pipewire" port-type="pipewire-audio">
   <output node="sushi" port="sushi:audio_output_0"/>
   <input node="Audient iD14 Analog Surround 4.0" port="Audient iD14:playback_FL"/>
  </item>
  <item node-type="pipewire" port-type="pipewire-audio">
   <output node="sushi" port="sushi:audio_output_1"/>
   <input node="Audient iD14 Analog Surround 4.0" port="Audient iD14:playback_FR"/>
  </item>
  <item node-type="pipewire" port-type="pipewire-audio">
   <output node="Audient iD14 Multichannel" port="Audient iD14:capture_AUX0"/>
   <input node="PipeWire ALSA [fmit]/ALSA Capture [fmit]" port="ALSA Capture [fmit]:input_MONO"/>
  </item>
   <item node-type="pipewire" port-type="pipewire-audio">
   <output node="Audient iD14 Multichannel" port="Audient iD14:capture_AUX0"/>
   <input node="NAM" port="NAM:Input"/>
  </item>
</items>
</patchbay>
```

### The brief expects a link that should not exist

> "the amp's output to BOTH sushi:audio_input_0 and sushi:audio_input_1"

`sushi:audio_input_1` has no incoming link, and that is **correct**. The
compiled config feeds both track channels from engine channel 0:

```json
track: board | channels: 2
  inputs : [{"engine_channel": 0, "track_channel": 0},
            {"engine_channel": 0, "track_channel": 1}]
  outputs: [{"engine_bus": 1, "track_bus": 0}]
```

Sushi does the mono-to-stereo spread itself, as `mono_to_stereo: true` in the
machine file asks. Nothing reads engine channel 1, so linking the amp to
`audio_input_1` would be redundant. This nearly went down as the fault; it is
not one, and the brief's expectation should be corrected.

### The routing worth knowing

```
track: board  | outputs: [{"engine_bus": 1, ...}]   -> sushi:audio_output_2 and _3
track: master | outputs: [{"engine_bus": 0, ...}]   -> sushi:audio_output_0 and _1
  board  plugins: ..., board_send
  master plugins: master_return
  initial_state: {"processor": "board_send", "properties": {"destination_name": "master_return"}}
```

Only outputs 0/1 are wired to the monitors, so everything audible arrives
through the send and return. That made the send/return the prime suspect
going into step 4.

---

## Step 4 — MEASURE SUSHI'S OUTPUTS, LIVE — PASSED. Sushi is innocent.

### Two changes to the brief's script, and why

1. **The guitar input name.** `Audient iD14:capture_AUX0` is a PipeWire
   *port alias*, not a JACK port name; the real one is
   `alsa_input.usb-Audient_Audient_iD14-00.multichannel-input:capture_AUX0`,
   from `pw-link -o | grep -i capture_AUX0` as the brief instructs. (This is
   the same alias-versus-name split that breaks the recorder in #118 — see
   `RESULTS-ui-2026-11-02.md`, finding 1.)
2. **Outputs 2 and 3 added.** The brief measures only `audio_output_0` and
   `_1`, which are the *master*. Measuring the board's own outputs at the
   same time separates "the chain lost a channel" from "the send and return
   lost a channel". Without it, a failure would have been localised only as
   far as "inside Sushi".

Measured over 9 s while Sonny played a sustained chord:

```
tap          port                                                              RMS dBFS  peak dBFS
guitar in    alsa_input...multichannel-input:capture_AUX0                        -240.0     -480.0
amp out      NAM:Output                                                           -20.7       -9.1
board L      sushi:audio_output_2                                                 -17.0       -5.8
board R      sushi:audio_output_3                                                 -17.0       -4.1
master L     sushi:audio_output_0                                                 -17.0       -5.8
master R     sushi:audio_output_1                                                 -17.0       -4.1

board  (track out, engine_bus 1)         L-R =   -0.0 dB
master (to the monitors, engine_bus 0)   L-R =   -0.0 dB
```

**Both pairs are level to within 0.0 dB.** The copies to stereo work, the
chain carries both channels, and the send and return carries both channels.
Whatever is losing the right channel is downstream of `sushi:audio_output_1`.

The differing peaks (-5.8 vs -4.1) with identical RMS are the chorus and
reverbs decorrelating the two channels — the same effect as the offline
render's `corr 0.650`. That is working stereo, not an imbalance.

`guitar in` could not be connected:

```
could not connect alsa_input...multichannel-input:capture_AUX0: Error connecting
'alsa_input...multichannel-input:capture_AUX0' -> 'lr-meter:m0' (22)
```

so its row is the noise floor and means nothing. Recorded rather than
retried: the guitar's presence is established by `amp out` at -20.7 dBFS.

---

## Step 6 — THE SAME RIG RENDERED OFFLINE ON THIS LAPTOP — PASSED

```
$ PYTHONPATH=src python -m pytest -q -s -m slow \
      "tests/test_stereo.py::test_each_rig_through_the_master" -k "electric-chorus-2"
======= left against right: a mono stimulus fed equally to both channels =======

each committed rig through the master (rigfile.heard)
  plugin or rig                                     lilv    L dBFS   R dBFS    corr
  electric-chorus-2 [as compiled]                           -16.11   -16.13   0.650
  electric-chorus-2 [without the copies to stereo]          -16.11  -279.07  -0.012
2 passed, 20 deselected in 0.74s
```

**-16.11 / -16.13, identical to CI.** The laptop's offline render is not
different from CI's.

The control row is the useful one: without the copies the right channel sits
at **-279.07 dBFS**, which is what Sonny's symptom would look like if the
copies were missing. They are not missing — step 3 found all four in the live
config, and step 4 measured both channels live at equal level.

---

## Step 7 — WITH THE AMP OUT OF THE PATH — NOT RUN

It would test whether the loss is in the amp feed, inside Sushi. Step 2 shows
the right speaker gets no sound from PipeWire when **Sushi is not running at
all**, and step 4 shows both of Sushi's output channels at equal level. There
is no path by which removing the amp could change that, and running it would
have cost Sonny another listening pass for an answer already known.

---

# Conclusion

| step | result |
|---|---|
| 1 the code is the branch's | **passed** |
| 2 the interface's right output | **FAILED — the fault is here** |
| 3 compiled config has the copies | **passed** |
| 4 measure Sushi's outputs | **passed** — L-R = 0.0 dB at board and master |
| 5 the links | **passed** — and the brief expects one link that should not exist |
| 6 offline render on this laptop | **passed** — matches CI exactly |
| 7 amp out of the path | not run — the answer is already known |

The brief offered three places the loss could be: the laptop running
different code, something inside Sushi, or something after it. It is the
third, and further out than the patchbay or the links — past the interface's
output altogether.

`claude/stereo-out` at `fabbc12` is doing its job on this machine. Every
check that could have convicted it cleared it, including a live measurement
of the two channels it exists to produce.

### Sonny's words

| step | asked | said |
|---|---|---|
| 2 | did "Front Right" come from the right speaker? | **"No, just heard front left."** |
| 2 | did any of the four positions reach the right speaker? | **"I just head front left."** |
| 7 | — | not asked: step not run |

### One thing for the tool, found in passing

The `warning: '<name>' returned no parameters` spam from `live.py:187`
(reported in `RESULTS-ui-2026-11-02.md`, finding 6) is **five times worse on
this branch**. Every `_to_stereo` copy is a parameterless processor, so each
live capture now prints:

```
warning: 'compressor_to_stereo' returned no parameters
warning: 'overdrive_to_stereo' returned no parameters
warning: 'octave_down_to_stereo' returned no parameters
warning: 'octave_up_to_stereo' returned no parameters
warning: 'master_return' returned no parameters
```

five lines about once a second, all of them describing the normal state of
processors the compiler itself inserted and which are documented as having no
controls. Whatever fixes that warning should know the stereo work multiplies
it.

### Housekeeping

Nothing was left running:

```
$ ps aux | grep -E "sushi|carla|fmit|qpwgraph|recorder" | grep -v grep
(nothing running)
$ ss -ltnp | grep 8029
8029 free
```

No machine file or patchbay file was edited. The only file written is this
one. The checkout is left on `claude/stereo-out`, as it was found.
