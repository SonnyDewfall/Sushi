# Hear-v2 results — 2026-11-01, sonny-dewfall-XPS-9320

Run against `tool/spikes/live_rig/HEAR-V2.md` on branch `claude/hear-v2`, with
the Audient iD14 and a guitar plugged in. Sonny played every step that says
"play"; what he heard is quoted in his words.

Step 5 (profiling) was deliberately not started.

## 0. Machine and setup

```
$ hostname
sonny-dewfall-XPS-9320
$ uname -a
Linux sonny-dewfall-XPS-9320 7.0.0-42-generic #42-Ubuntu SMP PREEMPT_DYNAMIC Thu Oct 15 08:06:44 UTC 2026 x86_64 GNU/Linux
$ pipewire --version
pipewire
Compiled with libpipewire 1.6.2
Linked with libpipewire 1.6.2
```

Same machine as the 2026-10-31 laptop tests, with the interface now attached.

```
$ git log --oneline -1 origin/master
077fe9b Merge pull request #92 from SonnyDewfall/claude/verify-property-names
$ git log --oneline -1          # claude/hear-v2
cc6fd45 Hear-v2 handover: record the MIDI pedals' facts while the interface is plugged in
```

### Working tree before starting (not part of the handover)

The checkout was on `claude/laptop-tests` with three uncommitted generated
configs in the tree (`config/edge.json` added, `config/electric-chorus.json`
and `config/electric-delay.json` modified). Two of those three differ between
that branch and `claude/hear-v2`, so carrying them across would have mixed
last night's generated output into tonight's. They were copied aside and
stashed, not discarded:

```
$ git stash list
stash@{0}: On claude/laptop-tests: pre-hear-v2 laptop-tests generated configs (sonny, 2026-11-01)
```

Copies also sit outside the repository in this session's scratchpad. Nothing
was committed on `claude/laptop-tests`.

### Install

```
$ cd tool && source .venv/bin/activate && pip install -e ".[dev,live,audio,agent]"
Successfully installed aiohappyeyeballs-2.7.1 aiohttp-3.14.5 aiosignal-1.4.0 \
  frozenlist-1.8.0 multidict-7.2.1 propcache-0.5.4 sushi-rig-0.1.0 yarl-1.25.1
```

`aiohttp` and its dependencies were new — the `agent` extra had not been
installed on this machine before, which fits #89 never having run here.

### doctor, with the rig down

```
$ sushi-rig doctor

5 check(s) could not be run. That is not a pass — most of these need PipeWire and a running rig.
       ok  machine-file: sonny-dewfall-XPS-9320.yaml is valid
unchecked  clock-driver: Sushi is not in the graph, so nothing is clocking it yet
           -> run `sushi-rig up` and check again
unchecked  one-clock: Sushi is not in the graph
           -> run `sushi-rig up` and check again
unchecked  quantum: Sushi is not in the graph
           -> run `sushi-rig up` and check again
unchecked  rate: Sushi is not in the graph
           -> run `sushi-rig up` and check again
unchecked  patchbay-unchecked: 2 node(s) rig.qpwgraph names are started by the rig itself, so their absence says nothing while the rig is down
           -> run `sushi-rig up` and check again to cover those links
       ok  pw-jack: present at /usr/bin/pw-jack
       ok  jackd: no jackd is running, so PipeWire's JACK layer is the only one in play
       ok  carla: present at /usr/bin/carla
       ok  fuse2: ./sushi is not an AppImage, so FUSE is not needed
       ok  disk: 200.8 GB free at /home/sonny-dewfall/sushi-rig-takes: about 72.6 hours of a 4-stem take of guitar_vocal at 48000 Hz in float32
       ok  amp-models: 2 model(s), all trained at 48000 Hz

7 check(s) passed
```

No FAULT. The five unchecked are all the rig being down, which is the expected
shape of `doctor` before `up`.

### The interface is there, and nothing of the rig is

```
$ ps aux | grep -E "sushi|carla|fmit|open-stage|sushi_rig" | grep -v grep
(none)
$ sushi-rig status
No rig is running.
$ pw-link -o | grep -i audient
alsa_output.usb-Audient_Audient_iD14-00.analog-surround-40:monitor_FL
alsa_output.usb-Audient_Audient_iD14-00.analog-surround-40:monitor_FR
alsa_output.usb-Audient_Audient_iD14-00.analog-surround-40:monitor_RL
alsa_output.usb-Audient_Audient_iD14-00.analog-surround-40:monitor_RR
alsa_input.usb-Audient_Audient_iD14-00.multichannel-input:capture_AUX0
  ... capture_AUX1 through capture_AUX9
```

**Result: step 0 passed with no faults.**

## 1. Baseline: today's rig, today's way

```
$ sushi-rig up electric_board --headless
warning: the tuner is running but PipeWire lists no alsa_capture.fmit ports, so its wiring could not be checked — the node may have been renamed
Rig up: electric_board (headless mode)
  fmit       pid 9370
  qpwgraph   pid 9380
  sushi      pid 9407

Logs: /run/user/1000/sushi-rig/logs
Stop with: sushi-rig down
```

The rig came up. The tuner warning is **a false alarm caused by a startup
race**, and the rig was right to be loud about it rather than silent — but the
thing it warns about is not true. See "The tuner warning" below.

```
$ sushi-rig status
electric_board (headless mode), up 4s
  config     /home/sonny-dewfall/Sushi/config/electric_board.json
  group      9369
  fmit       pid 9370
  qpwgraph   pid 9380
  sushi      pid 9407
  logs       /run/user/1000/sushi-rig/logs
```

```
$ sushi-rig top --once
electric_board (headless mode)
quantum 256 @ 48000 Hz — 5.3 ms per block  [clocked by alsa_input.usb-Audient_Audient_iD14-00.multichannel-input]

  node            busy  load                             xruns
  sushi          1.2ms  █████░░░░░░░░░░░░░░░░░░░   22%   0
  ----------------------------------------------------------
  total          1.2ms  █████░░░░░░░░░░░░░░░░░░░   22%
```

Worth noting against last night: on the laptop with no interface the clock
driver was the laptop's own device and `doctor` could only report the quantum
Sushi asked for. Here `top` names the **iD14 as the clock driver**, at
quantum 256 @ 48000 Hz, 5.3 ms per block, and the nine-plugin chain idles at
22% of it. Zero xruns at rest.

### Which ports are linked to what

The handover's command for this step is wrong, and was fixed:

```
$ pw-link -l | grep -i sushi        # as written in HEAR-V2.md
  |<- sushi:audio_output_0
  |<- sushi:audio_output_1
  |-> sushi:audio_input_1
  ...
```

`pw-link -l` prints a node name on its own line and then its links indented
beneath it. Grepping for `sushi` keeps the indented link lines but throws away
the node header that says *what the link is to* — so the step's own question,
"which ports are linked to what", is exactly what the output cannot answer.
The six bare `|<-`/`|->` lines above are links belonging to the iD14's nodes,
with the iD14 filtered out. Replaced with an awk that carries the current node
header onto each of its link lines:

```
$ pw-link -l | awk '/^[^ ]/{node=$0} /\|[<-]/{print node" \t"$0}' | grep -i sushi
alsa_output...analog-surround-40:playback_FL    |<- sushi:audio_output_0
alsa_output...analog-surround-40:playback_FR    |<- sushi:audio_output_1
alsa_input...multichannel-input:capture_AUX0    |-> sushi:audio_input_1
alsa_input...multichannel-input:capture_AUX0    |-> sushi:audio_input_0
alsa_input...multichannel-input:capture_AUX1    |-> sushi:audio_input_2
alsa_input...multichannel-input:capture_AUX1    |-> sushi:audio_input_3
sushi:audio_output_0    |-> alsa_output...analog-surround-40:playback_FL
sushi:audio_output_1    |-> alsa_output...analog-surround-40:playback_FR
sushi:audio_input_0     |<- alsa_input...multichannel-input:capture_AUX0
sushi:audio_input_1     |<- alsa_input...multichannel-input:capture_AUX0
sushi:audio_input_2     |<- alsa_input...multichannel-input:capture_AUX1
sushi:audio_input_3     |<- alsa_input...multichannel-input:capture_AUX1
```

So, on the real interface: the guitar on the iD14's `capture_AUX0` feeds Sushi
inputs 0 **and** 1 (one mono source to both track channels), `capture_AUX1`
feeds inputs 2/3, and Sushi's outputs 0/1 go to the iD14's `playback_FL/FR` —
the monitors. This is the layout step 2 says the v2 rig should reproduce.

### The tuner warning

The warning says PipeWire listed no `alsa_capture.fmit` ports. By the time the
rig had finished coming up, it did, and the tuner was wired:

```
$ pw-link -l | awk '/^[^ ]/{node=$0} /\|[<-]/{print node" \t"$0}' | grep -i fmit
alsa_input...multichannel-input:capture_AUX0    |-> alsa_capture.fmit:input_MONO
alsa_capture.fmit:input_MONO                    |<- alsa_input...multichannel-input:capture_AUX0
```

Re-running the rig's own check against the live graph:

```
$ python3 -c "
from sushi_rig.qpwgraph import unconnected_inputs
from sushi_rig.rig import TUNER_NODE
print('TUNER_NODE =', TUNER_NODE)
print('unconnected_inputs now ->', repr(unconnected_inputs(TUNER_NODE)))
"
TUNER_NODE = alsa_capture.fmit
unconnected_inputs now -> []
```

`[]` is "present and nothing unwired". At `up` time the same call returned
`None`, which is "the node never appeared in the listing at all".
`unconnected_inputs` waits `PATCHBAY_SETTLE = 3.0` seconds for the node to
appear and then gives up; on this machine fmit took longer than that to
register its ALSA capture node with PipeWire. The tuner is fine, and both
branches of that check behaved as written — the settle window is just too
short for fmit here.

Not fixed: `qpwgraph.py` is outside `tool/spikes/live_rig/`, which this
handover says not to change. Recorded as a result instead. It is a nuisance
rather than a danger (it warns, the rig still comes up, and it errs toward
the loud side, which is the right direction for principle 8), but it cries
wolf on every `up` on this machine, which is how a loud failure stops being
heard.

### Why the baseline rig was changed, with Sonny's reason

Sonny played the minute on `electric_board` and stopped the step:

> "Is there a way we can try step one with a different config? I would prefer
> we use electric-chorus-2 as that is something I am more familiar with. This
> tone has too much reverb."

That is both a preference and a finding, and the finding checks out.
`electric_board.json` has **no `initial_state` key at all**:

```
$ python3 -c "
import json; c=json.load(open('config/electric_board.json'))
print('top-level keys:', list(c.keys()))
print('initial_state:', repr(c.get('initial_state','(absent)')))
"
top-level keys: ['_meta', 'host_config', 'tracks']
initial_state: '(absent)'
```

So every one of its nine plugins comes up unbypassed at the plugin's own
defaults — including **both** reverbs, `room` and `hall`, running together.
`electric-chorus-2` sets bypass explicitly, and only four of the nine are
live:

```
  compressor   bypassed=False
  overdrive    bypassed=True
  octave_down  bypassed=True
  octave_up    bypassed=True
  eq           bypassed=False
  chorus       bypassed=False
  delay        bypassed=True
  room         bypassed=False  {'Blend': 0.15, 'Room Size': 0.41, 'Enable': 1.0}
  hall         bypassed=True   {'damping': 0.12, 'revtime': 0.01, 'roomsize': 0.29}
```

Sonny's ears found a real property of `electric_board`, not a matter of taste.
Recorded, not fixed: `config/` is outside this handover's scope.

Switching baselines was checked before it was done, because step 2's whole
question is "is it the same?" and that needs a matched v1/v2 pair:

- `config/electric-chorus-2.json` and `config/rigs/electric-chorus-2.yaml`
  both exist.
- Both hold the same nine plugins in the same order, with the same parameters
  and bypass states.
- Both carry the **same amp**: `_amp` in the JSON and `amp:` in the YAML are
  identical — `nam`, `amp/models/Neve 2.nam`, Input Lvl 0.0, Output Lvl 6.0,
  Quality 1.0.

`electric-chorus-2` is therefore a better subject than the handover's
`electric_board`, not a compromise: it is a tone Sonny knows well, so "is it
the same?" is a question he can answer sharply, and because it carries an amp
it exercises step 2b's question — a v2 rig bringing up Carla and NAM — in the
same breath. Step 2b's `electric-clean` is still run as written.

### Step 1 baseline, as actually measured: electric-chorus-2

```
$ sushi-rig down
Stopping electric_board (headless mode)...
Rig offline.
$ ps -p 9370,9380,9407 -o pid,cmd
    PID CMD
(all three gone)
$ sushi-rig status
No rig is running.
```

```
$ sushi-rig up electric-chorus-2 --headless
Rig up: electric-chorus-2 (headless mode)
  fmit       pid 11261
  qpwgraph   pid 11332
  sushi      pid 11376
  amp        pid 11377
```

No tuner warning on this `up`. Same machine, same patchbay, one `up` later —
which is consistent with the race explanation above and not with a renamed
node.

```
$ sushi-rig status
electric-chorus-2 (headless mode), up 9s
  config     /home/sonny-dewfall/Sushi/config/electric-chorus-2.json
  group      11260
  fmit       pid 11261
  qpwgraph   pid 11332
  sushi      pid 11376
  amp        pid 11377
  amp model  amp/models/Neve 2.nam
  logs       /run/user/1000/sushi-rig/logs
```

`amp model` is named in `status`, so this is not the silent-amp failure the
README lists among the expensive ones.

```
$ sushi-rig top --once        # idle
electric-chorus-2 (headless mode)
quantum 256 @ 48000 Hz — 5.3 ms per block  [clocked by alsa_input.usb-Audient_Audient_iD14-00.multichannel-input]

  node            busy  load                             xruns
  NAM            1.2ms  ██████░░░░░░░░░░░░░░░░░░   23%   0
  Carla            4us  ░░░░░░░░░░░░░░░░░░░░░░░░    0%   0
  sushi          119us  ░░░░░░░░░░░░░░░░░░░░░░░░    2%   0
  ----------------------------------------------------------
  total          1.3ms  ██████░░░░░░░░░░░░░░░░░░   25%
```

The amp is the expensive part: NAM alone is 23% of the 5.3 ms block, Sushi's
nine-plugin chain 2%. Zero xruns at rest on all three nodes.

The signal path, with the amp in it:

```
$ pw-link -l | awk '/^[^ ]/{node=$0} /\|[<-]/{print node" \t"$0}' | grep -iE "sushi|NAM|Carla"
alsa_input...capture_AUX0    |-> NAM:Input
NAM:Input                    |<- alsa_input...capture_AUX0
NAM:Output                   |-> sushi:audio_input_0
NAM:Output                   |-> sushi:audio_input_1
sushi:audio_input_0          |<- NAM:Output
sushi:audio_input_1          |<- NAM:Output
sushi:audio_input_2          |<- alsa_input...capture_AUX1
sushi:audio_input_3          |<- alsa_input...capture_AUX1
sushi:audio_output_0         |-> alsa_output...playback_FL
sushi:audio_output_1         |-> alsa_output...playback_FR
```

Guitar on `capture_AUX0` into NAM, NAM's mono output into Sushi inputs 0 and
1, the chain, then outputs 0/1 to the monitors. The amp sits **before** Sushi,
in Carla, which is the architecture the README's "3 against 4" note describes.

### Play: the step 1 reference, in Sonny's words

Sonny played a minute on `electric-chorus-2`. Verbatim:

> "The level is a god volume for a small room at about 80% gain on the
> interface. Tone is nice, the tape saturation is warm but still cleans up well
> with low attack. The chorus is there but subtle and gives a good body to the
> sound and the reverb is a barely noticeable enhancement to the rest of the
> sound. Doesn't feel like there is much latency at all."

**This is the reference for step 2.** The interface gain was left untouched at
about 80% for the rest of the session so the comparison stays honest.

One note for whoever reads this later, which does not change what Sonny heard:
there is no tape plugin in this chain. The warmth he calls "tape saturation"
can only be coming from the NAM amp, whose model is `Neve 2.nam` — a console
preamp model — since the other four live plugins are a compressor, a 3-band
EQ, a stereo chorus and `a-reverb`. Recorded because it matters for step 2: if
that warmth survives the v2 path, the amp survived with it.

### Load under real signal

Two readings were taken while the guitar was actually playing, which is the
thing neither CI nor the laptop could produce.

```
$ sushi-rig top --once        # during the playing
  NAM            1.2ms  ██████░░░░░░░░░░░░░░░░░░   23%   0
  Carla            4us  ░░░░░░░░░░░░░░░░░░░░░░░░    0%   0
  sushi          112us  ░░░░░░░░░░░░░░░░░░░░░░░░    2%   0
  total          1.3ms  ██████░░░░░░░░░░░░░░░░░░   25%
```

```
$ sushi-rig top --once        # after the minute
  NAM            1.4ms  ██████░░░░░░░░░░░░░░░░░░   26%   0
  Carla            3us  ░░░░░░░░░░░░░░░░░░░░░░░░    0%   0
  sushi          118us  ░░░░░░░░░░░░░░░░░░░░░░░░    2%   0
  total          1.5ms  ███████░░░░░░░░░░░░░░░░░   28%
```

**Did xruns climb? No — zero on all three nodes, throughout.** Idle 25%,
playing 25%, after 28%. A neural amp costs the same per block whether the
input is a chord or silence, so the headroom measured at rest is the headroom
available while playing, and 28% of 5.3 ms leaves a lot of it.

Worth putting beside the abandoned `electric_board` run, which reached
**3 xruns** after its minute. The rig that does more work — nine plugins plus
Carla plus NAM — had none. That is one observation of each, not a measured
comparison, and `electric_board`'s load reading also fell from 22% to 4%
between its two samples, which is not explained. Both numbers are recorded as
they came out; neither was re-run to get a tidier one.

## 2. The same rig, compiled from its v2 file

Run with `electric-chorus-2` in place of `electric_board`, for the reasons
recorded in step 1.

```
$ sushi-rig down                 # end of step 1
Stopping electric-chorus-2 (headless mode)...
Rig offline.
$ ps -p 11261,11332,11376,11377 -o pid,cmd
    PID CMD
(all four gone)
$ sushi-rig status
No rig is running.
```

```
$ sushi-rig emit config/rigs/electric-chorus-2.yaml -o config/electric-chorus-2_v2.json
wrote config/electric-chorus-2_v2.json
```

`emit` did not refuse: it loaded the result in `./sushi` and found every
parameter name real.

### What emit made

```
$ python3 -c "
import json; c = json.load(open('config/electric-chorus-2_v2.json'))
for t in c['tracks']:
    print(t['name'], 'in', t.get('input_busses') or t.get('inputs'), 'out', t.get('output_busses') or t.get('outputs'), [p['name'] for p in t['plugins']])
"
board in [{'engine_channel': 0, 'track_channel': 0}, {'engine_channel': 0, 'track_channel': 1}] out [{'engine_bus': 1, 'track_bus': 0}] ['compressor', 'overdrive', 'octave_down', 'octave_up', 'eq', 'chorus', 'delay', 'room', 'hall', 'board_send']
master in [] out [{'engine_bus': 0, 'track_bus': 0}] ['master_return']
```

This is exactly the shape the handover predicted: `board` taking engine
channel 0 into both track channels, out on engine bus 1 (outputs 2/3), nine
plugins then a send; `master` on engine bus 0 (outputs 0/1) with a
`master_return`. And the provenance is recorded:

```
_meta.source = "config/rigs/electric-chorus-2.yaml"
_amp         = {"plugin": "nam", "model": "amp/models/Neve 2.nam",
                "parameters": {"Input Lvl": 0.0, "Output Lvl": 6.0, "Quality": 1.0}}
```

### The only difference is the send and return

Before listening, the two configs were diffed on the thing that decides the
sound — `initial_state` — so that what Sonny is being asked to hear is
isolated to one change:

```
$ python3 -c "...norm(v1) vs norm(v2)..."
only in v1: []
only in v2: ['board_send']
shared processors differing: 0
_amp identical: True
host_config v1: {'samplerate': 48000}  v2: {'samplerate': 48000}
```

Every processor the two configs share — `board`, `compressor`, `overdrive`,
`octave_down`, `octave_up`, `eq`, `chorus`, `delay`, `room`, `hall` — has
**identical** bypass state and identical parameters. The amp is identical. The
samplerate is identical. The chain gains the same two internal processors the
v2 shape is defined by, and nothing else:

```
board / board_send    {"name": "board_send", "type": "internal", "uid": "sushi.testing.send"}
master / master_return {"name": "master_return", "type": "internal", "uid": "sushi.testing.return"}
```

`board_send` carries only `destination_name: master_return` — no gain
override — and the `board` strip keeps v1's `gain: 0.833333, pan: 0.5,
mute: 0.0`. So Sushi's internal send and return are the single variable in
the listening test, which is what step 2 exists to settle.

### Up, on the real interface

```
$ sushi-rig up electric-chorus-2_v2 --headless
Rig up: electric-chorus-2_v2 (headless mode)
  fmit       pid 11772
  qpwgraph   pid 11834
  sushi      pid 11877
  amp        pid 11878
```

```
$ sushi-rig status
electric-chorus-2_v2 (headless mode), up 4s
  config     /home/sonny-dewfall/Sushi/config/electric-chorus-2_v2.json
  group      11771
  fmit       pid 11772
  qpwgraph   pid 11834
  sushi      pid 11877
  amp        pid 11878
  amp model  amp/models/Neve 2.nam
  logs       /run/user/1000/sushi-rig/logs
```

The amp came up from the v2 config with its model named — the v2 path carries
`amp:` through `emit` and into Carla, which is step 2b's question answered
once already.

```
$ sushi-rig top --once        # idle, v2
quantum 256 @ 48000 Hz — 5.3 ms per block  [clocked by alsa_input.usb-Audient_Audient_iD14-00.multichannel-input]

  node            busy  load                             xruns
  NAM            1.1ms  █████░░░░░░░░░░░░░░░░░░░   20%   0
  Carla            5us  ░░░░░░░░░░░░░░░░░░░░░░░░    0%   0
  sushi          114us  ░░░░░░░░░░░░░░░░░░░░░░░░    2%   0
  ----------------------------------------------------------
  total          1.2ms  █████░░░░░░░░░░░░░░░░░░░   22%
```

The handover expected "one more track, so a little more load". **Measured, it
is not more**: v2 idle is 22% against v1's 25%, and Sushi's own share is
114 µs against 119 µs. The honest reading is not that the extra track is
free but that its cost is **smaller than NAM's sample-to-sample variation**,
which wandered between 20% and 26% across the five readings taken tonight on
an unchanged chain. Sushi's own figure — the part the extra track, send and
return actually land on — moved by 5 µs out of ~115 µs. An extra track, a
send and a return cost something too small for `top` to separate from the
amp's noise.

### Which ports are linked to what

```
$ pw-link -l | awk '/^[^ ]/{node=$0} /\|[<-]/{print node" \t"$0}' | grep -iE "sushi|NAM|Carla"
alsa_input...capture_AUX0    |-> NAM:Input
NAM:Output                   |-> sushi:audio_input_0
NAM:Output                   |-> sushi:audio_input_1
sushi:audio_input_2          |<- alsa_input...capture_AUX1
sushi:audio_input_3          |<- alsa_input...capture_AUX1
sushi:audio_output_0         |-> alsa_output...playback_FL
sushi:audio_output_1         |-> alsa_output...playback_FR
```

Outputs 0/1 to the monitors. Outputs 2/3 do not appear — because that awk
lists only ports that *have* a link, and the stem has none. They exist:

```
$ pw-link -o | grep "^sushi:"
sushi:audio_output_0
sushi:audio_output_1
sushi:audio_output_2
sushi:audio_output_3
sushi:audio_output_4 ... through sushi:audio_output_7
```

So "outputs 0/1 to the monitors, outputs 2/3 (the stem) linked to nothing" is
confirmed exactly as the handover predicted, and **the committed patchbay
needed no change** — the same `rig.qpwgraph` that wired v1 wired v2.

### Play: is it the same?

Sonny played the same minute, at the same interface gain, on the v2-compiled
rig. Asked the one question the step asks, verbatim:

> "Yes it sounds and feels exactly the same."

**That is the result of this handover.** A guitar, through a real nine-plugin
chain, through an amp in Carla, through Sushi's internal send and return, out
of the master track into the monitors, is indistinguishable by ear from the
same chain going straight to the outputs — level, tone, the amp's warmth, the
chorus, the reverb and the latency all included.

Offline in CI the master is bit-exact with the channel; on the laptop that
held live under PipeWire with empty chains. This was the case neither could
reach: real plugins, a real amp, a real interface, real ears. It holds.

### Did xruns climb?

```
$ sushi-rig top --once        # during the playing
  NAM            1.6ms  ███████░░░░░░░░░░░░░░░░░   29%   0
  Carla            9us  ░░░░░░░░░░░░░░░░░░░░░░░░    0%   0
  sushi          148us  █░░░░░░░░░░░░░░░░░░░░░░░    3%   0
  total          1.8ms  ████████░░░░░░░░░░░░░░░░   32%
```

```
$ sushi-rig top --once        # after the minute
  NAM            1.2ms  █████░░░░░░░░░░░░░░░░░░░   22%   0
  Carla            4us  ░░░░░░░░░░░░░░░░░░░░░░░░    0%   0
  sushi           96us  ░░░░░░░░░░░░░░░░░░░░░░░░    2%   0
  total          1.3ms  ██████░░░░░░░░░░░░░░░░░░   24%
```

**No. Zero xruns on every node, through both minutes of playing and after.**

The peak of the night was 32% of a 5.3 ms block, mid-performance on the v2
rig — two thirds of the block still spare. Sushi's own share peaked at 148 µs.

Every `top` reading taken tonight, for the record:

| rig | moment | NAM | sushi | total | xruns |
|---|---|---|---|---|---|
| electric_board (v1, no amp) | idle | — | 1.2 ms / 22% | 22% | 0 |
| electric_board (v1, no amp) | after playing | — | 225 µs / 4% | 4% | **3** |
| electric-chorus-2 (v1) | idle | 23% | 119 µs / 2% | 25% | 0 |
| electric-chorus-2 (v1) | during playing | 23% | 112 µs / 2% | 25% | 0 |
| electric-chorus-2 (v1) | after playing | 26% | 118 µs / 2% | 28% | 0 |
| electric-chorus-2_v2 | idle | 20% | 114 µs / 2% | 22% | 0 |
| electric-chorus-2_v2 | during playing | 29% | 148 µs / 3% | 32% | 0 |
| electric-chorus-2_v2 | after playing | 22% | 96 µs / 2% | 24% | 0 |

The only xruns of the night belong to the one rig with **no amp** and both
reverbs wide open, and that same rig is the one whose load reading fell from
22% to 4% between two samples. Neither is explained here, and neither was
chased — `electric_board` was abandoned for Sonny's rig before the oddity
appeared. Flagged rather than resolved: **`electric_board` is the only
configuration tonight that produced an xrun, and it did so while reporting
less load than at idle.** Worth a look on a night when that rig is the
subject.

### 2b. A rig with the amp: electric-clean

Run as the handover writes it, even though `electric-chorus-2` had already
shown a v2 rig bringing up Carla and NAM. `electric-clean`'s chain is eight
plugins with compressor, EQ and `room` live and chorus, octave, overdrive,
delay and hall bypassed.

Its amp differs from `electric-chorus-2`'s in one setting, which matters
below: **`Output Lvl: -0.4`**, where `electric-chorus-2` has `6.0`.

#### v1, today's way

```
$ sushi-rig up electric-clean --headless
Rig up: electric-clean (headless mode)
  fmit       pid 12192
  qpwgraph   pid 12253
  sushi      pid 12318
  amp        pid 12319

$ sushi-rig status
electric-clean (headless mode), up 0s
  config     /home/sonny-dewfall/Sushi/config/electric-clean.json
  amp model  amp/models/Neve 2.nam
```

```
$ sushi-rig top --once        # idle
  NAM            962us  ████░░░░░░░░░░░░░░░░░░░░   18%   0
  sushi           79us  ░░░░░░░░░░░░░░░░░░░░░░░░    1%   0
  total          1.0ms  █████░░░░░░░░░░░░░░░░░░░   19%
$ sushi-rig top --once        # during playing
  NAM            1.3ms  ██████░░░░░░░░░░░░░░░░░░   24%   0
  sushi           74us  ░░░░░░░░░░░░░░░░░░░░░░░░    1%   0
  total          1.4ms  ██████░░░░░░░░░░░░░░░░░░   25%
$ sushi-rig top --once        # after
  NAM            1.2ms  ██████░░░░░░░░░░░░░░░░░░   23%   0
  sushi          103us  ░░░░░░░░░░░░░░░░░░░░░░░░    2%   0
  total          1.3ms  ██████░░░░░░░░░░░░░░░░░░   25%
```

**Play, v1.** Sonny, verbatim:

> "It is still relatively quiet on 90% gain. NO effects other than a sight
> reverb and a bit of overdrive from the amp. No noticeable latency."

Both halves of that match the config and neither is a fault:

- **Quiet at 90% gain** is the `-0.4` amp output above, against the `6.0`
  that made `electric-chorus-2` a good level at 80%. Sonny had to add
  interface gain and was still below the earlier rig.
- **"No effects other than a slight reverb"** is the bypass list: chorus,
  octave, overdrive, delay and hall are all bypassed, leaving compressor, EQ
  and `room`. The drive he hears therefore cannot be the `overdrive` plugin,
  which is off — it is NAM, which is what he says.

```
$ sushi-rig down
Stopping electric-clean (headless mode)...
Rig offline.
$ ps -p 12192,12253,12318,12319 -o pid,cmd
    PID CMD
(all four gone)
```

#### v2, compiled from the rig file

```
$ sushi-rig emit config/rigs/electric-clean.yaml -o config/electric-clean_v2.json
wrote config/electric-clean_v2.json
```

```
board   in engine_channel 0 -> track_channel 0 and 1   out engine_bus 1
        ['compressor','eq','chorus','octave','overdrive','delay','hall','room','board_send']
master  in []                                          out engine_bus 0
        ['master_return']

_meta.source: config/rigs/electric-clean.yaml
_amp identical: True
_amp: {"plugin":"nam","model":"amp/models/Neve 2.nam",
       "parameters":{"Input Lvl":0.0,"Output Lvl":-0.4,"Quality":1.0}}

only in v1: []
only in v2: ['board_send']
shared processors differing: 0
```

Same story as `electric-chorus-2`: identical but for the send and the return,
the `-0.4` amp output carried through intact.

```
$ sushi-rig up electric-clean_v2 --headless
Rig up: electric-clean_v2 (headless mode)
  fmit       pid 12660
  qpwgraph   pid 12721
  sushi      pid 12764
  amp        pid 12765

$ sushi-rig status
electric-clean_v2 (headless mode), up 0s
  config     /home/sonny-dewfall/Sushi/config/electric-clean_v2.json
  amp model  amp/models/Neve 2.nam
```

**`status` lists the amp, which is what this step asked.** The amp came up as
before from the compiled v2 config.

```
$ sushi-rig top --once        # idle
  NAM            1.1ms  █████░░░░░░░░░░░░░░░░░░░   22%   0
  sushi           81us  ░░░░░░░░░░░░░░░░░░░░░░░░    2%   0
  total          1.2ms  ██████░░░░░░░░░░░░░░░░░░   24%
```

The amp's ports wired by the guitar role, exactly as in v1:

```
$ pw-link -l | awk '/^[^ ]/{node=$0} /\|[<-]/{print node" \t"$0}' | grep -iE "sushi|NAM|Carla"
alsa_input...capture_AUX0    |-> NAM:Input
NAM:Output                   |-> sushi:audio_input_0
NAM:Output                   |-> sushi:audio_input_1
sushi:audio_output_0         |-> alsa_output...playback_FL
sushi:audio_output_1         |-> alsa_output...playback_FR
```

**Play, v2. Same question.** Sonny, verbatim:

> "Yes it sounds exactly the same."

```
$ sushi-rig top --once        # during playing, v2
  NAM            1.2ms  ██████░░░░░░░░░░░░░░░░░░   23%   0
  sushi           77us  ░░░░░░░░░░░░░░░░░░░░░░░░    1%   0
  total          1.3ms  ██████░░░░░░░░░░░░░░░░░░   24%
$ sushi-rig top --once        # after, v2
  NAM            1.3ms  ██████░░░░░░░░░░░░░░░░░░   25%   0
  sushi           86us  ░░░░░░░░░░░░░░░░░░░░░░░░    2%   0
  total          1.4ms  ██████░░░░░░░░░░░░░░░░░░   27%
```

Zero xruns again. v1 and v2 of `electric-clean`, at the same points:

| moment | v1 total | v2 total | v1 sushi | v2 sushi |
|---|---|---|---|---|
| idle | 19% | 24% | 79 µs | 81 µs |
| during playing | 25% | 24% | 74 µs | 77 µs |
| after | 25% | 27% | 86 µs | 86 µs |

Sushi's own share is within a few µs at every point, which is the same
conclusion as `electric-chorus-2`: the extra track, send and return are
too cheap for `top` to distinguish from NAM's variation.

```
$ sushi-rig down
Stopping electric-clean_v2 (headless mode)...
Rig offline.
$ ps -p 12660,12721,12764,12765 -o pid,cmd
    PID CMD
(all four gone)
$ sushi-rig status
No rig is running.
```

**Step 2b result: the same, both ways, with the amp.** Two different rigs,
two different amp settings, four plays — and Sonny could not tell the v2
path from the v1 path in either.

### 2c. Two channels — the patchbay diff only

Optional, and the listening half was **not done**: it needs a microphone on
the iD14's second analogue input, which was not confirmed as plugged in, and
the handover says it is not required tonight. No input was linked by hand and
the committed patchbay was not replaced.

The recorded half needs no microphone and was done. The generated file was
written to this session's scratchpad rather than `/tmp` or the repository:

```
$ sushi-rig patchbay generate config/rigs/guitar_vocal.yaml -o <scratchpad>/guitar_vocal_v2.qpwgraph
wrote <scratchpad>/guitar_vocal_v2.qpwgraph

$ diff Patchbay/rig.qpwgraph <scratchpad>/guitar_vocal_v2.qpwgraph
9,16d8
<    <output node="Audient iD14 Multichannel" port="Audient iD14:capture_AUX0"/>
<    <input node="sushi" port="sushi:audio_input_1"/>
<   </item>
<   <item node-type="pipewire" port-type="pipewire-audio">
<    <output node="Audient iD14 Multichannel" port="Audient iD14:capture_AUX1"/>
<    <input node="sushi" port="sushi:audio_input_2"/>
<   </item>
<   <item node-type="pipewire" port-type="pipewire-audio">
18c10
<    <input node="sushi" port="sushi:audio_input_3"/>
---
>    <input node="sushi" port="sushi:audio_input_1"/>
```

Side by side, as links:

| | committed (v1), 7 links | generated (v2), 5 links |
|---|---|---|
| guitar | AUX0 -> input_0 **and** input_1 | AUX0 -> input_0 |
| vocal | AUX1 -> input_2 **and** input_3 | AUX1 -> input_1 |
| monitors | output_0/1 -> playback_FL/FR | output_0/1 -> playback_FL/FR |
| tuner | AUX0 -> fmit input_MONO | AUX0 -> fmit input_MONO |

The reason for the shape change is in the machine file: both `guitar-in` and
`vocal-in` declare `mono_to_stereo: true`, and under v1 the patchbay was what
performed that widening — one port wired into both halves of a bus. Under v2
each channel is its own track that takes **one engine channel into both of its
track channels internally** (visible in step 2's `board`: `engine_channel 0 ->
track_channel 0 and 1`). So the widening moved from the patchbay into Sushi,
and the patchbay needs one link per source instead of two.

#### A consequence worth flagging

This was **not heard** — it is read off the two files above, and is recorded
as a prediction, not an observation. But it is the kind this project's
principle 8 is about.

Running the v2 `guitar_vocal` against the **committed** patchbay would not
simply fail to wire the vocal. The committed file links `AUX0 -> input_1`, and
in the v2 layout `input_1` is the **vocal channel's** input. So:

- the guitar would arrive on input_0 **and** input_1, and be processed twice —
  once by its own chain, and once by the vocal chain's gate, de-esser, EQ,
  MVerb and limiter;
- the microphone, on AUX1, would be wired to inputs 2 and 3, which the v2 rig
  does not read at all, so **the vocal would be silent**.

A rig that comes up, makes sound, and has a dead microphone and a guitar
running through a vocal processor is exactly the silent degradation the README
says every serious bug here has been. Nothing in tonight's run would catch it,
because `up` checks that Sushi loads and that the tuner is wired, not that
each channel's source reaches the channel that wants it. Worth a check of its
own before `guitar_vocal` is used live — and worth noting that the handover
was right to say the committed patchbay does not fit, and right to say not to
replace it.

## 3. The agent runs a real `up`

Run with `electric-chorus-2_v2` rather than `electric_board_v2`, to stay with
the rig Sonny can judge by ear and because step 2 established it.

### Starting the agent, and a fix to record

The handover says:

```bash
sushi-rig agent &
AGENT=$!
echo "agent pid $AGENT"
```

Typed at an interactive prompt that is right. Run non-interactively it is a
trap, and it caught me: in a compound command, `&` binds to the whole
`&&` chain before it, so the variable assignments ran *inside* the
backgrounded subshell and `$!` in the foreground named **the wrapping shell,
not the agent**. The first pid I recorded, 13877, was a `/bin/bash -c`.

Killing that pid would have left the agent running and holding port 8029 —
precisely the failure the "kill what you start, by pid" rule exists to
prevent. The fix is to take the pid from the thing that actually holds the
port, and verify it:

```
$ ss -ltnp | grep 8029
LISTEN 0  128  127.0.0.1:8029  0.0.0.0:*  users:(("sushi-rig",pid=13880,fd=6))

$ ps -p 13880 -o pid,ppid,stat,etime,cmd
    PID    PPID STAT     ELAPSED CMD
  13880   13877 S          00:25 .../python3 .../sushi-rig agent
```

**Agent pid: 13880** (13877 is the shell wrapper, its parent). Recorded in
the scratchpad so later commands could not guess it.

```
$ cat <scratchpad>/agent.log
sushi-rig agent 0.1 on http://127.0.0.1:8029  (Ctrl+C to stop)
```

`HEAR-V2.md` has been amended to say that the pid must be verified against the
listener rather than taken from `$!`.

### /health, with no rig

```
$ curl -s localhost:8029/health | python3 -m json.tool
{
    "ok": true,
    "agent": {
        "version": "0.1",
        "started_at": "2026-11-01T22:36:22+00:00",
        "uptime_seconds": 30.6,
        "busy": null,
        "last_error": null
    },
    "rig": {
        "verdict": "absent",
        "running": false,
        "message": "No rig is running."
    }
}
```

### The up — the thing that was untested

```
$ time curl -s -X POST localhost:8029/rig/up -H 'Content-Type: application/json' \
       -d '{"config_name": "electric-chorus-2_v2", "headless": true}' | python3 -m json.tool
{
    "ok": true,
    "command": ["up", "electric-chorus-2_v2", "--headless"],
    "output": "Rig up: electric-chorus-2_v2 (headless mode)\n  fmit       pid 13930\n  qpwgraph   pid 14001\n  sushi      pid 14049\n  amp        pid 14050\n...",
    "rig": {
        "verdict": "running", "running": true,
        "config_name": "electric-chorus-2_v2", "mode": "headless",
        "pgid": 13929,
        "children": {"fmit": {"pid": 13930, "alive": true},
                     "qpwgraph": {"pid": 14001, "alive": true},
                     "sushi": {"pid": 14049, "alive": true},
                     "amp": {"pid": 14050, "alive": true}},
        "amp_model": "amp/models/Neve 2.nam",
        "message": "electric-chorus-2_v2 (headless mode), up 0s"
    }
}

real	0m4.433s
```

**4.4 seconds, no 504.** The whole four-child rig, amp included, came up
through an HTTP request.

### It stayed up after the request returned

This is the point of the step, and it is checked from a *different process*
than the agent — the CLI, which reads the state file:

```
$ sushi-rig status
electric-chorus-2_v2 (headless mode), up 5s
  config     /home/sonny-dewfall/Sushi/config/electric-chorus-2_v2.json
  group      13929
  fmit       pid 13930
  qpwgraph   pid 14001
  sushi      pid 14049
  amp        pid 14050
  amp model  amp/models/Neve 2.nam

$ ps -p 13930,14001,14049,14050 -o pid,stat,etime,cmd
  13930 SLl  00:09  fmit
  14001 Sl   00:07  qpwgraph -a /run/user/1000/sushi-rig/rig-amp.qpwgraph -m
  14049 SLl  00:06  .../sushi.bin -j -c .../config/electric-chorus-2_v2.json
  14050 SLl  00:06  .../python3 /usr/share/carla/carla ... -n /run/user/1000/sushi-rig/amp.carxp
```

All four children alive, the process group intact, and the CLI agrees with
`GET /rig` (`uptime_seconds: 5.388633`). **A rig started through the agent
outlives the request.**

Incidentally visible above: for an amp rig the patchbay qpwgraph loads is the
generated `/run/user/1000/sushi-rig/rig-amp.qpwgraph`, not the committed
`Patchbay/rig.qpwgraph` — the amp's ports have to be wired in too.

### /doctor through the agent, with the rig up

```
$ curl -s localhost:8029/doctor | python3 -c "import json,sys; d=json.load(sys.stdin); print(d['ok'], d['faults'], 'faults', d['unchecked'], 'unchecked')"
True 0 faults 0 unchecked
```

Every check, with its state (the field is `state`, not `status`):

```
      ok  machine-file: sonny-dewfall-XPS-9320.yaml is valid
      ok  clock-driver: the rig is clocked by Audient iD14 Multichannel, which the machine file names
      ok  one-clock: sushi, Carla, NAM are all in driver group 46
      ok  quantum: Sushi asked for 256 samples (5.33 ms a block) and locked it, so a restart is the only way to change it
      ok  rate: Sushi asked for 48000 Hz
      ok  patchbay-nodes: all 7 link(s) in rig.qpwgraph name a node that exists
      ok  pw-jack: present at /usr/bin/pw-jack
      ok  jackd: no jackd is running, so PipeWire's JACK layer is the only one in play
      ok  carla: present at /usr/bin/carla
      ok  fuse2: ./sushi is not an AppImage, so FUSE is not needed
      ok  disk: 200.7 GB free
      ok  amp-models: 2 model(s), all trained at 48000 Hz
```

**All twelve checks pass with nothing unchecked** — against step 0's seven
passed and five unchecked with the rig down. The five that could not run then
all ran now, and `one-clock` confirms the thing that matters most for an amp
rig: **sushi, Carla and NAM are in one driver group (46)**, so the amp is not
on a second clock.

### Starting a second rig is refused

The handover's command omits `Content-Type`; it was run as written *and* with
the header, to see whether the refusal depended on it. It does not:

```
$ curl -s -w "\nHTTP_STATUS=%{http_code}\n" -X POST localhost:8029/rig/up -d '{}'
{"ok": false, "command": ["up", "electric_board"], "output": "",
 "rig": {... "config_name": "electric-chorus-2_v2", ... "uptime_seconds": 31.44 ...},
 "reason": "a rig is already running: electric-chorus-2_v2 (headless mode), up 31s.\nRun 'sushi-rig down' first — starting a second rig on top of a running one is what takes the live session down."}
HTTP_STATUS=409
```

**409 both ways**, as expected. Three things worth keeping:

- the refusal **did not disturb the running rig** — all four children still
  alive, uptime still climbing across both attempts;
- the reason names the rig that is already up and what to do, which is
  principle 7 behaving;
- with an empty body the agent defaulted `config_name` to **`electric_board`**
  (see `"command": ["up", "electric_board"]`). It refused before running
  anything, so nothing came of it here, but a default that names a real
  config means a stray `POST /rig/up -d '{}'` against an *idle* agent would
  start a rig nobody asked for. Flagged, not changed: the agent is outside
  this handover's scope.

### Play through the rig the agent started

Sonny played through it. Verbatim:

> "Yup, sounds good"

So "it is up" means sound, not a state file — which is what this step set out
to establish.

```
$ sushi-rig top --once        # playing, agent-started rig
  NAM            1.2ms  █████░░░░░░░░░░░░░░░░░░░   22%   0
  sushi          100us  ░░░░░░░░░░░░░░░░░░░░░░░░    2%   0
  total          1.3ms  ██████░░░░░░░░░░░░░░░░░░   24%
```

```
$ curl -s localhost:8029/health
agent ok: True | busy: None
rig verdict: running | electric-chorus-2_v2 (headless mode), up 1m 42s
last_error: {'command': ['up', 'electric_board'], 'at': '2026-11-01T22:37:28+00:00',
             'message': "a rig is already running: ..."}
```

`busy: None` while the rig runs — the agent holds nothing open after the
request returns.

**A small flag:** `last_error` still carried the deliberate 409 from a minute
earlier, alongside `ok: true`. It is a record rather than a live fault, but it
does not clear on subsequent success, so anything built on `last_error` as a
health signal would read a healthy agent as unhealthy indefinitely.

### restart

```
$ time curl -s -X POST localhost:8029/rig/restart | python3 -m json.tool
{
    "ok": true,
    "command": ["restart"],
    "output": "Restarting electric-chorus-2_v2...\nStopping electric-chorus-2_v2 (headless mode)...\nRig offline.\nRig up: electric-chorus-2_v2 (headless mode)\n  fmit       pid 14313\n  qpwgraph   pid 14374\n  sushi      pid 14420\n  amp        pid 14421\n...",
    "rig": {
        "verdict": "running", "running": true, "pgid": 14312,
        "children": {"fmit": {"pid": 14313, "alive": true},
                     "qpwgraph": {"pid": 14374, "alive": true},
                     "sushi": {"pid": 14420, "alive": true},
                     "amp": {"pid": 14421, "alive": true}},
        "amp_model": "amp/models/Neve 2.nam",
        "message": "electric-chorus-2_v2 (headless mode), up 0s"
    }
}

real	0m4.648s
```

**4.6 seconds**, against the handover's "about five". The old group was
replaced, not added to:

```
$ ps -p 13930,14001,14049,14050 -o pid,stat,cmd     # the pre-restart children
    PID STAT CMD
(all four old children gone)

$ ps -p 14313,14374,14420,14421 -o pid,stat,etime,cmd
  14313 SLl  00:12  fmit
  14374 Sl   00:10  qpwgraph -a /run/user/1000/sushi-rig/rig-amp.qpwgraph -m
  14420 SLl  00:09  .../sushi.bin -j -c .../config/electric-chorus-2_v2.json
  14421 SLl  00:09  .../carla ... -n /run/user/1000/sushi-rig/amp.carxp

$ sushi-rig status
electric-chorus-2_v2 (headless mode), up 8s
  group      14312
  amp model  amp/models/Neve 2.nam

$ curl -s localhost:8029/doctor | ...
True 0 faults 0 unchecked
```

No orphans: nothing from the old group survived to hold a port or clock the
interface, and `doctor` still passed every check.

**And it came back audibly.** Process tables prove processes returned, not
that sound did — the distinction the README's silent-amp bug is about — so
Sonny was asked to strum:

> "Yes, sound's back"

**Principle 9's claim — "it must come straight back with the state it had" —
holds here by ear as well as by pid**, in 4.6 seconds.

### down, and the agent stopped by pid

```
$ time curl -s -X POST localhost:8029/rig/down | python3 -m json.tool
{
    "ok": true,
    "command": ["down"],
    "output": "Stopping electric-chorus-2_v2 (headless mode)...\nRig offline.\n",
    "rig": {"verdict": "absent", "running": false, "message": "No rig is running."}
}

real	0m0.474s

$ sushi-rig status
No rig is running.
$ ps -p 14313,14374,14420,14421 -o pid,stat,cmd
    PID STAT CMD
(all four gone)
```

Then the agent, by the pid recorded when it was started — not by pattern:

```
$ AGENT=$(cat <scratchpad>/agent.pid); echo "killing recorded agent pid: $AGENT"
killing recorded agent pid: 13880
$ kill 13880
$ ps -p 13880 -o pid,stat,cmd
    PID STAT CMD
agent gone
$ ps -p 13877 -o pid,stat,cmd          # the shell wrapper, also mine
wrapper gone
$ ss -ltnp | grep 8029
(8029 free)
```

**Step 3 result: the agent does what #89 claimed and could not prove in a
container.** A rig started over HTTP comes up in 4.4 s, outlives the request,
makes sound, reports itself consistently through both the agent and the CLI,
restarts faithfully in 4.6 s with the amp intact and audible, stops in 0.47 s,
and leaves nothing behind.

Minor note: `kill` produced no shutdown line in the agent's log — the last
line remains its startup banner. Not a fault, and the process did exit, but a
clean SIGTERM handler that logged would make "did the agent stop, or was it
killed mid-request?" answerable after the fact.

## 3b. The pedals

`aseqdump` and `amidi` are both already installed, so the handover's
`alsa-utils` note was not needed.

```
$ aseqdump -l
 Port    Client name                      Port name
  0:0    System                           Timer
  0:1    System                           Announce
 14:0    Midi Through                     Midi Through Port-0
 36:0    MPD218                           MPD218 Port A

$ amidi -l
Dir Device    Name
IO  hw:5,0,0  MPD218 Port A
```

**No footswitch and no expression pedal are attached.** The only MIDI device
on the machine is an **Akai MPD218** at client 36, port A — a pad controller
with pads and knobs, not a footswitch or an expression pedal.

The MPD218 is not a usable stand-in for this step. What 3b exists to record is
which CC or Program Change each *pedal* control sends and whether each
footswitch is momentary or latching; pad and knob messages from a different
class of device would answer none of that, and writing them down as "the
pedals' facts" would be a fixture neater than reality — the mistake
principle 1 names explicitly.

### 3b, continued: the MPD218 as a stand-in

Sonny asked to use the MPD218 in place of the pedals — knobs for the
expression pedal, pads for the footswitches. Done, with the limits stated
below.

```
$ cat /proc/asound/cards | grep -A1 MPD218
 5 [MPD218         ]: USB-Audio - MPD218
                      Akai MPD218 at usb-0000:00:14.0-1.3, full speed
```

```
$ nohup aseqdump -p 36:0 > <scratchpad>/midi-capture.log 2>&1 &
$ ps -p 16031 -o pid,ppid,stat,cmd
    PID    PPID STAT CMD
  16031   16029 S    aseqdump -p 36:0
```

**aseqdump pid: 16031**, verified against `ps` rather than taken from `$!`,
after the lesson in step 3.

#### A fix to record: do not truncate a file a running process holds open

The first capture was cleared with `: > midi-capture.log` to separate Sonny's
labelled sequence from some earlier presses. `aseqdump` still held the file
open at its old offset, so the next write landed past the truncation point and
the kernel filled the gap with NUL bytes. `grep` then saw a binary file and
reported nothing at all, while `sort | uniq -c` on the same file happily
showed the events — which is how the problem was noticed:

```
$ grep -nE "Note (on|off)" midi-capture.log
(nothing)
$ head -3 midi-capture.log | cat -A
^@^@^@^@^@^@^@^@ ... (hundreds of NULs)
```

Recovered with `tr -d '\000'`. The right move is to start a new file rather
than truncate one a live process is writing to. Nothing was lost.

#### Pads — what a momentary switch looks like on the wire

Six presses, labelled by Sonny as his footswitch simulation:

```
 36:0   Note on                 0, note 41, velocity 75
 36:0   Note off                0, note 41, velocity 0
 36:0   Note on                 0, note 40, velocity 91
 36:0   Note off                0, note 40, velocity 0
 36:0   Note on                 0, note 38, velocity 98
 36:0   Note off                0, note 38, velocity 0
 36:0   Note on                 0, note 36, velocity 111
 36:0   Note off                0, note 36, velocity 0
 36:0   Note on                 0, note 36, velocity 116
 36:0   Note off                0, note 36, velocity 0
 36:0   Note on                 0, note 36, velocity 101
 36:0   Note off                0, note 36, velocity 0
```

Facts, for the MPD218:

- pads send **Note on / Note off on channel 0**, not CC and not Program
  Change. Pads seen: notes 36, 38, 40, 41;
- **press is Note on with a real velocity, release is Note off with velocity
  0** — a strict alternation, one pair per press;
- **note 36 was pressed three times and produced three identical on/off
  pairs** (velocities 111, 116, 101). Nothing alternates between presses, so
  the pad is **momentary** — which is what a pad is built to be;
- velocity varies with how hard it is hit, so anything treating a pad as a
  switch must ignore velocity rather than threshold it;
- **holding a pad also emits channel aftertouch** — 19 messages across the
  capture, values 0 to 125. A footswitch mapping listening for notes will
  receive pressure data on the same port that it has to ignore.

#### Knob — what a continuous sweep looks like

```
$ grep "controller 3," midi-clean.log | sed -E 's/.*value ([0-9]+)/\1/'
12 11 10 9 8 6 5 3 1 0 1 3 5 7 10 12 16 19 22 24 27 29 31 33 35 37 39 42 44 47 49
52 55 57 60 63 65 68 70 72 74 76 78 80 82 84 87 89 91 93 95 96 98 99 100 101 102
103 104 105 106 107 108 109 110 112 113 115 116 118 120 121 122 124 126 127 126
125 124 123 122 121 120 119 118 117 116 115 114 113 112 111 110 109
```

- the knob sends **Control change, controller 3, channel 0**;
- it reaches **both ends, 0 and 127** — no dead zone at either extreme, which
  is the thing worth knowing before scaling an expression input;
- **94 messages for one full sweep and back**, with a **maximum step of 4**
  between consecutive messages. So it is dense enough to feel continuous but
  does not emit every value, and a full-speed sweep does not flood the port;
- the sequence is monotonic within each direction and reverses cleanly.

One stray message, worth keeping because a mapping has to tolerate it:

```
17  36:0   Control change          0, controller 3, value 1
18  36:0   Control change          0, controller 3, value 0
19  36:0   Control change          0, controller 12, value 11
20  36:0   Control change          0, controller 3, value 1
```

**A second controller (12) emitted a single message in the middle of the
sweep.** Whether a knob was brushed or the device sent it unprompted was not
established. Either way, "the expression pedal is controller N" cannot be
inferred from "controller N moved" — a listener needs to be told which
controller to follow, not discover it.

```
$ kill 16031
$ ps -p 16031 -o pid,stat,cmd
aseqdump gone
$ ps -eo pid,cmd | grep aseqdump | grep -v grep
(none)
```

#### What this step did and did not establish

Established, for the Akai MPD218 at client 36:0: pads are Note on/off with
velocity and aftertouch, knobs are CC with full 0–127 travel at a maximum step
of 4, and a stray controller can appear mid-sweep.

**Not established: whether the real footswitches are momentary or latching.**
That is the question 3b was written to answer, and it is a property of the
pedal's own hardware. A pad is momentary by construction, so the capture above
demonstrates what momentary looks like on the wire — useful for building and
testing against — but says nothing about what Sonny's pedals do. Nor are the
real CC and Program Change numbers known; controller 3 and notes 36–41 are the
MPD218's, and a pedal will send its own.

**This is recorded as MPD218 facts, not as the pedals' facts.** Filing a pad's
behaviour as a pedal's would be precisely the fixture that is neater than
reality which principle 1 warns about, and the handover's `aseqdump` recipe is
now known to work on this machine, so repeating it with the real pedals is a
five-minute job whenever they are to hand.

## 4. Clean up

```
$ sushi-rig status
No rig is running.

$ ps aux | grep -E "sushi|carla|fmit|open-stage|sushi_rig" | grep -v grep
(nothing of the rig)

$ ss -ltnp | grep 8029
(8029 free)

$ ps -eo pid,cmd | grep aseqdump | grep -v grep
(none)
```

Nothing of the rig is left running, the agent's port is free, and no capture
process survives. Every process started tonight was stopped by its recorded
pid; `pkill -f` was never used.

The handover's `rm` names a file this run never created, because step 2 ran on
`electric-chorus-2` rather than `electric_board`:

```
$ ls config/*_v2.json
config/electric-chorus-2_v2.json
config/electric-clean_v2.json
$ ls config/electric_board_v2.json
ls: cannot access 'config/electric_board_v2.json': No such file or directory
$ rm -v config/electric-chorus-2_v2.json config/electric-clean_v2.json
removed 'config/electric-chorus-2_v2.json'
removed 'config/electric-clean_v2.json'
$ ls config/*_v2.json
(none)
```

```
$ git status --short
?? tool/spikes/live_rig/RESULTS-hear-v2-2026-11-01.md
 M tool/spikes/live_rig/HEAR-V2.md
```

Only the results file and `HEAR-V2.md`. Nothing outside
`tool/spikes/live_rig/` is changed, and no generated config is committed.

## Fixes made to HEAR-V2.md

Every one is a command in the file that was wrong on this machine, and each is
described above where it was hit:

1. **Step 1's `pw-link -l | grep -i sushi`** — cannot answer "which ports are
   linked to what", because the grep discards the node headers naming the far
   end of each link. Replaced with an awk that carries the header onto each
   link line.
2. **Step 2's copy of the same command** — same fix, plus a note that outputs
   2/3 *will not appear* in that listing because it shows only ports that have
   a link, with `pw-link -o | grep '^sushi:'` to confirm they exist. Without
   the note, the expected result reads like a missing port.
3. **Step 2b's `grep -iE "sushi|NAM|Carla"`** — same fix.
4. **Step 3's `AGENT=$!`** — names the wrapping shell, not the agent, in any
   compound command. Replaced with taking the pid from `ss -ltnp | grep 8029`
   and verifying it with `ps`. This one mattered: killing the wrong pid would
   have left the agent holding the port.
5. **Step 3b** — added the warning not to truncate a file `aseqdump` holds
   open, with the `tr -d '\000'` recovery.
6. **Step 4's `rm`** — named `electric_board_v2.json`, which need not exist.
   Replaced with `ls config/*_v2.json` then `rm config/*_v2.json`.

Not fixed, because they are outside `tool/spikes/live_rig/` and this handover
says not to touch them — recorded above instead:

- `qpwgraph.py`'s `PATCHBAY_SETTLE = 3.0` is too short for fmit on this
  machine, so `up` warns that the tuner's ports are missing when they are
  merely late (step 1);
- `electric_board.json` has no `initial_state`, so both reverbs come up
  unbypassed (step 1);
- the agent defaults `config_name` to `electric_board` on an empty body, and
  its `last_error` never clears after a success (step 3).

## Summary

**The question this handover existed to answer is answered: yes.** A guitar,
through a real nine-plugin chain and a neural amp in Carla, through Sushi's
internal send and return and out of the master track, is indistinguishable by
ear from the v1 path. Sonny's words, on two different rigs with two different
amp settings:

> "Yes it sounds and feels exactly the same."
> "Yes it sounds exactly the same."

Each was asked against a config diff showing the send and return as the only
variable, so there is nothing else the answer could be about.

**The agent (#89) does what it was merged on.** A rig started over HTTP came
up in 4.4 s, outlived the request, made sound, reported itself consistently
through both the agent and the CLI, passed `doctor` 12/12 with nothing
unchecked, refused a second `up` with 409 without disturbing the running rig,
restarted in 4.6 s with the amp reloaded and **audibly back**, stopped in
0.47 s, and left nothing behind.

Zero xruns across every reading of every rig that has an amp. The night's peak
was 32% of a 5.3 ms block, playing, on the v2 rig.

**What is still open**

- **Step 2c's listening half** was not done: no microphone on the iD14's
  second input. The patchbay diff was recorded, and it predicts — on the files,
  not by ear — that a v2 `guitar_vocal` on the committed patchbay would feed
  the **guitar into the vocal channel** and leave the **microphone silent**,
  with the rig looking healthy throughout. Worth a check of its own.
- **Step 3b's real question** — momentary or latching — needs the actual
  pedals. The MPD218 stood in and its own facts are recorded, clearly as the
  MPD218's and not the pedals'.
- **`electric_board` is the only configuration that produced an xrun** (3),
  and the only one whose load reading fell between samples (22% to 4%).
  Unexplained; it was abandoned for Sonny's rig before the oddity appeared.
- **Step 5, the profiling session, was not started**, as instructed.
