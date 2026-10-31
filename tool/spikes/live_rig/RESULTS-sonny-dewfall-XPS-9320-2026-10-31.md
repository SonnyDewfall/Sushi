# Laptop test results — sonny-dewfall-XPS-9320, 2026-10-31

Run by a local Claude session on the rig laptop, following
`tool/spikes/live_rig/README.md` on branch `claude/laptop-tests`.
No Audient iD14 and no guitar were connected, as expected.

This **is** the rig machine (`sonny-dewfall-XPS-9320`), so step 6 applies.

## 0. What machine is this

| | |
|---|---|
| hostname | `sonny-dewfall-XPS-9320` |
| kernel | `Linux 7.0.0-42-generic #42-Ubuntu SMP PREEMPT_DYNAMIC Thu Oct 15 08:06:44 UTC 2026 x86_64` |
| distro | Ubuntu 26.04.1 LTS (resolute) |
| nproc | 16 |
| CPU | 12th Gen Intel(R) Core(TM) i7-1260P |
| PipeWire | 1.6.2 (compiled and linked both 1.6.2) |

`pw-metadata -n settings 0`:

```
clock.rate          48000
clock.allowed-rates [ 48000 ]
clock.quantum       1024
clock.min-quantum   32
clock.max-quantum   2048
clock.force-quantum 0
clock.force-rate    0
```

Tools present — all of them, so steps 5 and 6 both apply:

```
/usr/bin/pw-jack  /usr/bin/pw-link  /usr/bin/pw-top
/usr/bin/qpwgraph /usr/bin/open-stage-control  /usr/bin/carla
```

Linux with PipeWire and `pw-jack`, so nothing was stopped at step 0.

## 1. Set up

The machine was already provisioned; almost nothing needed installing.

- Every apt dependency was already present: `liblilv-dev 0.26.2-1`,
  `lilv-utils 0.26.2-1`, `lv2-dev 1.18.10-3`, `python3-lilv 0.26.2-1`,
  `pipewire-jack 1.6.2-1ubuntu1.2`. **No `sudo apt install` was run** — the
  list was verified with `dpkg -l` instead, and `import lilv` works.
- `tool/.venv` already existed (Python 3.14.4, `--system-site-packages`), with
  `sushi-rig 0.1.0` installed editable, plus `numpy 2.5.4`, `pytest 9.1.1`,
  `elkpy 1.3.0`, `python-osc 1.10.2` from system site-packages.
- **The one thing missing was `JACK-Client`.** Installed into the venv:
  `JACK-Client 0.5.7` (with `cffi 2.1.1`, `pycparser 3.12`).
  `pw-jack python -c "import jack"` then worked.
- Sushi was already in place: `./sushi` is a symlink to
  `/home/sonny-dewfall/opt/sushi/squashfs-root/AppRun` (an extracted AppImage,
  so FUSE is not involved). `./sushi --version`:

```
Version 1.3.0
Build options enabled: vst3, lv2, jack, rpc control, ableton link
Audio buffer size in frames: 64
Git commit: 08f24e9cd47ce2f95c827db19350593b023cebe9
```

### A note on the working tree

The checkout had **uncommitted work in progress** when this session started:
`config/edge.json` (new, staged) and modifications to
`config/electric-chorus.json` and `config/electric-delay.json`. That work is
not mine and was left exactly as found — it is still uncommitted, with the
same bytes and the same staged/unstaged state. It is **not** in any commit
pushed by this session. It does, however, change the test results, which is
why step 2 reports two numbers.

## 2. The suites, and doctor

### The suites

Run as `cd tool && PYTHONPATH=src python -m pytest -rs`.

**As the working tree actually stands (with Sonny's uncommitted configs):**

```
9 failed, 1130 passed in 84.06s
```

**With those three config files stashed, i.e. the branch as committed:**

```
1 failed, 1135 passed in 87.84s
```

**There were no skips in either run.** `-rs` reported none: every test in the
suite ran on this machine. (A detached worktree at the same commit *did* skip
24, but that tree lacks the machine's local `plugins/` and `amp/` assets, so
that number says nothing about this machine and is not reported as a result.)

So 8 of the 9 failures belong to the uncommitted config work, not to the
branch:

```
FAILED tests/test_configs.py::test_config_loads_and_names_things_that_exist[electric-chorus.json]
FAILED tests/test_configs.py::test_config_loads_and_names_things_that_exist[electric-delay.json]
FAILED tests/test_configs.py::test_config_actually_applies_what_it_says[electric-chorus.json]
FAILED tests/test_configs.py::test_config_actually_applies_what_it_says[electric-delay.json]
FAILED tests/test_convert.py::test_every_tone_has_a_v2_rig_that_holds_everything_it_did[edge]
FAILED tests/test_convert.py::test_every_tone_has_a_v2_rig_that_holds_everything_it_did[electric-chorus]
FAILED tests/test_convert.py::test_every_tone_has_a_v2_rig_that_holds_everything_it_did[electric-delay]
FAILED tests/test_render.py::test_every_channel_of_every_config_in_the_repository_renders
```

Two observations on those, offered only because they are cheap to pass on:

- `test_every_channel_of_every_config_in_the_repository_renders` fails as
  `assert 13 == 12` — it hard-codes the number of configs in `config/`, so
  **adding any config file fails it**. `edge.json` is the thirteenth.
- `config/electric-chorus.json` and `config/electric-delay.json` are both
  exactly 3841 bytes in the working tree. Worth a look: they may have been
  made identical by accident.

### The one real failure

```
FAILED tests/test_doctor.py::test_the_command_runs_end_to_end_and_names_every_check
```

This fails on the committed branch too, and it is a **test bug, not a code
bug**. The patchbay check is three-way — `doctor.py` emits `patchbay-stale`
(FAULT, line 463), `patchbay-unchecked` (UNCHECKED, line 476) or
`patchbay-nodes` (OK, line 483) — but the test asserts the OK name
unconditionally:

```python
assert "patchbay-nodes" in printed.out
```

On this machine, without the iD14, the check correctly takes the `stale`
branch, so `patchbay-nodes` never appears and the assertion fails. The test
therefore only passes on a machine whose patchbay is already clean.

The two lines immediately above it get this right for the graph checks:

```python
# Either the graph was read and the four questions answered, or it was not
# and that was said. Both are honest; silence would not be.
assert "pipewire-graph" in printed.out or "clock-driver" in printed.out
```

The patchbay assertion wants the same treatment. **Not fixed here**, because
the README limits this session to `tool/spikes/live_rig/`.

### doctor

`PYTHONPATH=tool/src python -m sushi_rig.cli doctor`, exit code **1**:

```
5 check(s) could not be run. That is not a pass — most of these need PipeWire and a running rig.

1 of 13 check(s) failed. Each line above says what to do.
       ok  machine-file: sonny-dewfall-XPS-9320.yaml is valid
unchecked  clock-driver: Sushi is not in the graph, so nothing is clocking it yet
           -> run `sushi-rig up` and check again
unchecked  one-clock: Sushi is not in the graph
           -> run `sushi-rig up` and check again
unchecked  quantum: Sushi is not in the graph
           -> run `sushi-rig up` and check again
unchecked  rate: Sushi is not in the graph
           -> run `sushi-rig up` and check again
    FAULT  patchbay-stale: rig.qpwgraph names 2 node(s) that are not in the graph ('Audient iD14 Analog Surround 4.0', 2 link(s), 'Audient iD14 Multichannel', 5 link(s)) — qpwgraph keeps those links and never connects them
           -> regenerate it with `sushi-rig patchbay generate`, which writes the node names from the live graph rather than from whatever the device was called last time
unchecked  patchbay-unchecked: 2 node(s) rig.qpwgraph names are started by the rig itself, so their absence says nothing while the rig is down
           -> run `sushi-rig up` and check again to cover those links
       ok  pw-jack: present at /usr/bin/pw-jack
       ok  jackd: no jackd is running, so PipeWire's JACK layer is the only one in play
       ok  carla: present at /usr/bin/carla
       ok  fuse2: ./sushi is not an AppImage, so FUSE is not needed
       ok  disk: 200.9 GB free at /home/sonny-dewfall/sushi-rig-takes: about 72.6 hours of a 4-stem take of guitar_vocal at 48000 Hz in float32
       ok  amp-models: 2 model(s), all trained at 48000 Hz
```

7 ok, 5 unchecked, 1 FAULT. The FAULT is the expected one: the iD14 is not
plugged in, so the two nodes `rig.qpwgraph` names for it are absent. Doctor
naming that, rather than passing quietly, is the result the README predicted.

## 3. The capture engine

### The bug that had to be fixed first (and what it hid)

The very first run, at 20 seconds, recorded **twenty seconds of silence while
reporting `xruns` 0, `lost_frames` 0 and no dropouts**. The report:

```
"quantum": 1024, "blocks": 0, "short_blocks": 3751,
"blocksize_changes": 2, "callback_us": null,
"recorder": {"frames": 0, "lost_frames": 0, "dropouts": [], "error": ""},
"continuity": { ... "jumps": null, "note": "silent: never linked?" ... }
```

`3751 × 256 / 48000 = 20.005 s`, so the callback *was* running, at the 256
quantum that was asked for. The cause: the script read

```python
sr, q = client.samplerate, client.blocksize
```

**before** the client was activated. `PIPEWIRE_QUANTUM` is applied to a node
only once it starts, so before activation `blocksize` is the server's current
default — `clock.quantum` 1024 on this machine. Every buffer was then sized
for 1024, the callback took its `frames != q` branch on all 3751 real
256-frame blocks, and `recorder.push` was never called. Measured directly:

```
before activate: blocksize 1024
blocksize callback: 256 / 1024 / 256      (it churns, then settles)
after activate:  blocksize 256
frames seen in callback: [256]
```

This is a principle-8 failure in the spike itself: a silent one. Every health
number the report prints was truthful and every one of them was 0, because
nothing had been offered to the recorder in the first place.

### The fix

Activate once with no process callback, let the quantum settle, read what was
actually granted, then stop again before anything is allocated. The report now
also carries `quantum_asked` and `quantum_before_activate`, so a run at the
wrong quantum is visible rather than silent. The RT callback is unchanged.

### The three runs

All three were run with `pw-jack` from `tool/spikes/live_rig`,
`PYTHONPATH=../../src`, venv active, **on the rebased script** (see the note on
the rebase below). Firefox and the Claude desktop app were open throughout;
nothing else, and no video call.

| | 256 idle | 256 under load | 128 idle |
|---|---|---|---|
| asked | `256/48000` | `256/48000` | `128/48000` |
| **quantum granted** | **256** | **256** | **128** |
| seconds | 600 | 300 | 300 |
| load processes | 0 | 16 (`nproc`) | 0 |
| budget_us | 5333.3 | 5333.3 | 2666.7 |
| blocks | 112215 | 56253 | 112507 |
| **xruns** | **0** | **0** | **0** |
| short_blocks | 0 | 0 | 0 |
| blocksize_changes | 0 | 0 | 0 |
| callback p50 µs | 270.6 | 124.4 | 303.7 |
| callback p99 µs | 607.4 | 212.2 | 708.9 |
| **callback p99.9 µs** | **861.9** | **292.5** | **888.7** |
| callback max µs | 1343.5 | 851.1 | 1204.9 |
| recorder frames | 28727040 | 14400768 | 14400896 |
| **recorder lost_frames** | **0** | **0** | **0** |
| recorder dropouts | none | none | none |
| recorder error | empty | empty | empty |
| **continuity jumps** | **257 on 3 of 6 columns** | **0** on all 6 | **0** on all 6 |

**The timing question is answered, and the answer is yes.** xruns 0,
`lost_frames` 0, `error` empty and `p99.9` at 16%, 5% and 33% of budget, in
every run. A Python callback keeps up at a 256 quantum with an order of
magnitude of headroom, and gets *faster* under full load, not slower.

**But the 256 idle run does not meet the README's pass condition**, which is 0
jumps on every column of every stem. It is recorded as the failure it is.

### The 257 jumps: what actually happened

Not a dropout, and **not a recorder bug**. Read straight out of the audio:

- All 257 jumps are at consecutive file indices from **9965823** — one
  contiguous region, 207.6 s into a 600 s take, not 257 scattered events.
- The affected columns hold **exactly 256 samples of pure zero** there.
- The counter resumes at **528896** immediately after a last good value of
  **528639**: an advance of 257 over 257 sample positions. **Nothing was lost
  and nothing was shifted** — 256 samples were replaced by silence *in place*.
- Arithmetic check on the whole column: 539 zero samples = 256 leading silence
  (before its port was linked) + 27 counter wraps + the 256 of the gap.
- Three of the six columns were hit (block columns 0, 1 and 3); the other
  three — including `guitar-dry`, written from the same pushed block — are
  **perfectly continuous through the same region**, stepping 528639, 528640,
  528641 … with no interruption at all.

That last point is what settles it. All six columns travel in one `block`
array into one `Recorder.push`. If the recorder had lost or mishandled
anything, all six would show it. Three do and three do not, so the silence was
already there when the callback read the ports: **three of the six JACK input
ports delivered a block of zeros for one cycle** while the other three
delivered the real signal.

So `xruns` 0 and `lost_frames` 0 are both *correct*. Nothing was late and
nothing was dropped; the data itself arrived as silence. The README asks
whether the jumps line up with the recorder's reported dropouts, and says that
if they do not, that is a recorder bug and the most valuable thing this session
can find. **They do not line up, and it is not a recorder bug** — the evidence
above rules the recorder out. The loss is upstream of it, in PipeWire's
delivery to a subset of links.

It is **intermittent**: the same 600 s run on the same machine earlier in this
session (the pre-rebase script, same wiring, same quantum) reported 0 jumps on
all six columns, and both 300 s runs are clean. One plausible trigger, not
confirmed: another client entering or leaving the graph forces a
re-negotiation, and for one cycle some links have no valid buffer. Firefox and
the desktop app were open and could have started a stream at any time. Nothing
in this session pins that down, and it is offered as a hypothesis, not a
finding.

**Why it matters anyway.** A take can acquire a silent 5 ms hole with *every*
health indicator green — no xrun, no dropout, no lost frame, no error. That is
precisely the silent-degradation shape principle 8 exists to catch, and the
recorder's own accounting cannot see it, because by its own lights nothing went
wrong. Whatever the cause turns out to be, the capture engine needs a check
that reads the audio, not only the counters — which is exactly what
`continuity()` is, and why it earned its place.

One difference from the earlier pre-rebase runs worth recording: that first
load run showed a single callback `max` of **5773.5 µs**, just over the 5333.3
µs budget, with no xrun. It did not recur — the committed load run's max is
851.1 µs. Both are reported; neither threshold was touched.

### pw-top

`pw-top -b` was sampled twice during each run. It reports the spike as a
**follower**, so its row carries no quantum, rate or busy figures at all:

```
S   ID  QUANT   RATE    WAIT    BUSY   W/Q   B/Q  ERR FORMAT  NAME
C  159      0      0    ---     ---   ---   ---     0         sushi-rig-capture-spike
```

This is exactly the trap principle 1 describes for `test_top.py`'s fixture:
quantum and rate live on the driver row alone. **No busy figure could be read
for the spike.** The one useful column is `ERR`, which stayed `0` in all six
samples — including through the 256-sample silent gap, which is further
evidence that nothing xran.

Load averages at sampling: 0.72 (256 idle), 7.40 (256 load), 9.26 (128 idle —
still decaying from the load run that had just ended; no burner processes
remained, which was verified).

## 4. A v2 rig in a live Sushi

Run three times. **All three reports are identical, value for value.**

| Check | Result | |
|---|---|---|
| `send_costs_nothing` | `true` | master, guitar and vocal all at lag 256 |
| `port_to_port_samples` | **256** | not the 257 measured before |
| `separate_channels` | `true` | B in the guitar stem: 0.0029 |
| `pipewire_sums_two_links` | **`false`** | see below |
| `all_gains_as_expected` | **`false`** | see below |

Measured gains (identical in all three runs):

| | A | B | expected A | expected B |
|---|---|---|---|---|
| guitar_l / guitar_r | 1.0 | 0.0029 | 1.0 | 0.0 |
| vocal_l / vocal_r | **0.2529** | **0.2521** | 1.0 | 1.0 |
| master_l / master_r | **1.2529** | **0.255** | 2.0 | 1.0 |

`send_costs_nothing` passing is the result the step was for: **the monitor path
costs nothing live**, as it did offline. The master is exactly as late as the
stems, so Sushi is running every track on one thread.

`port_to_port_samples` is **256, not 257**. Recorded as asked; it is one
sample less than the earlier measurement, and consistent across all runs.

### Why the gains are wrong — and it is not PipeWire

The master row is arithmetically consistent throughout
(`master = guitar + vocal`, e.g. `1.2529 = 1.0 + 0.2529`), so the send is
faithful. The anomaly is the vocal channel alone, and the vocal channel is the
one whose Sushi input has **two** PipeWire links into it.

An attenuation to 0.25 was the obvious reading, and it is wrong. The vocal
stem's RMS is 0.0706 against 0.1412 for A+B, but its **peak is 0.6127 against
0.6264** — a crest factor far too high for a signal scaled by a quarter.
Looking at the samples settles it:

```
vocal zero samples: 143936/191744 = 75.1%
  samples   0.. 63 of each quantum: zero   0.0%   gain 1.0000
  samples  64..127 of each quantum: zero 100.0%   gain 0.0000
  samples 128..191 of each quantum: zero 100.0%   gain 0.0000
  samples 192..255 of each quantum: zero 100.0%   gain 0.0000
over the 47808 non-zero samples: gain vs (A+B) = 1.0000
```

**The sum is perfect — for the first 64 frames of each quantum. The remaining
192 frames are exact zeros.** The 0.25 was 64/256 all along.

Varying the quantum shows the surviving run is always 64, never a fraction of
the quantum:

| quantum | guitar (1 link in) | vocal (2 links in) |
|---|---|---|
| 256 | full, gain 1.0000 | first **64** valid, 75.1% zero, gain 0.248 |
| 128 | full, gain 1.0000 | first **64** valid, 50.2% zero, gain 0.499 |
| 64 | full, gain 1.0000 | first **64** valid, 0.3% zero, **gain 0.9998** |

64 is Sushi's compile-time chunk: `./sushi --version` says "Audio buffer size
in frames: 64", and the rig's own `sushi.log` says `using block size: 64`. **At
a quantum of 64 the fault disappears completely.**

Two controls place the fault with Sushi, not PipeWire:

- **Within one node**, N links into one input port sum at unity across the
  whole quantum, for N = 1, 2, 3 and 4 (gains 1.0027, 1.0006, 1.0077 …).
- **Across nodes**, with no Sushi at all — two plain JACK clients, two output
  ports of one into a single input port of the other — mixing is also perfect:
  1, 2 and 3 links give exactly 0.25, 0.5 and 0.75 from constant-0.25 sources,
  with **zero** truncation across the full 256-frame quantum.

So **`probes.py`'s hypothesis is correct: PipeWire does sum two links into one
input, at unity.** What fails is Sushi: when more than one link feeds one of
its JACK input ports, only `AUDIO_CHUNK_SIZE` (64) frames of the mixed buffer
reach its processing, and the rest of the quantum arrives as silence. With a
single link into the same port the full quantum arrives intact, at every
quantum tested.

Sushi's node is not the cause by way of running at a different rate —
`pw-dump` shows it locked to the quantum it was given:

```
node 159 sushi
   node.force-quantum = 256
   node.lock-quantum = True
```

**The mechanism inside Sushi's JACK frontend was not determined** — that would
need its source, which is not in this repository. What is established is the
behaviour, its exact shape, that it is reproducible, and that it is Sushi's and
not PipeWire's.

**Why this matters beyond the spike:** `probes.py` relies on summing two links
into one Sushi input. It will do so correctly only at a quantum of 64. At the
rig's 256 it will read every summed probe at a quarter of its true level —
silently, because the signal is present and correctly aligned, just not for
most of each block. Summing upstream into a single link, or probing at quantum
64, both avoid it.

### sushi.log

All three runs' logs contain **only** the two-line copyright banner. Nothing
about threads, xruns or ports — at its default log level Sushi says nothing
that bears on any of these checks.

## 5. The record bar

`sushi-rig panel … --record-bar` **succeeded**:

```
wrote /tmp/record-bar-panel.json
open with: open-stage-control --load /tmp/record-bar-panel.json --send 127.0.0.1:24024
```

(It prints `ELKPY: Sushi API match! -> 1.2.0` against a Sushi reporting 1.3.0 —
noted, not investigated.)

**The layout is right: the RECORD/STOP bar stacks directly under the SAVE bar,
with the tabs below both.** It did not land anywhere else. The panel file says
so structurally — the root is `layout: vertical` with three children in order:

| order | panel | height |
|---|---|---|
| 1 | `save_bar` — name, SAVE, status | 70 |
| 2 | `record_bar` — take, RECORD, STOP, status | 70 |
| 3 | `tabs` — `guitar_send`, `vocal_send` | 100% |

and the rendered panel confirms it: `record-bar-panel.jpg`, captured from
open-stage-control's own web UI on `127.0.0.1:8080`.

**The screenshot is of the OSC web UI, not of the desktop window.** This
machine is a Wayland session with no screenshot tool installed
(`gnome-screenshot`, `import`, `scrot`, `grim`, `spectacle`, `xwd`, `ffmpeg`
are all absent), and GNOME refuses the Shell D-Bus screenshot API to
unsandboxed callers:

```
Error: GDBus.Error:org.freedesktop.DBus.Error.AccessDenied: Screenshot is not allowed
```

Since open-stage-control serves the identical panel over HTTP, that was
rendered and captured instead. Nothing was installed to work around this.

open-stage-control was stopped by pid, then Sushi by writing to the FIFO
holding its stdin; both were verified gone.

### A second script bug: `--hold` cannot be driven non-interactively

The first attempt at `--hold` died with an unhandled traceback as soon as its
stdin closed:

```
File "live_paths.py", line 193, in main
  input("Sushi is up with the live_paths rig. Press Enter to stop it. ")
EOFError: EOF when reading a line
```

The `finally` block still stopped Sushi correctly, so nothing was left running
— but the exception escaped *after* it, so **`args.out.write_text` never ran
and no report was written at all**. Worked around here by holding the FIFO's
write end open with a long-lived `sleep`, rather than by changing the script,
since a terminal user never hits this. `live-paths-hold.json` was then written
normally, with the same five check values as the other three runs.

## 6. On the rig machine

This is `sonny-dewfall-XPS-9320`, so this step applies.

### The amp's real port names

`sushi-rig up --headless --no-tuner` — the v1 rig as it runs today — brought up
**qpwgraph and Sushi only, no Carla**, and `pw-link -o | grep -i nam` matched
nothing. That is not a fault: the default config is `electric_board`, Carla is
started only `if amp_project is not None`, and `amp_project` comes from a
config's `_amp` section, which `electric_board` does not have. Ten configs do
(`accustomed`, `electric-clean`, `hard-feelings`, …), all on
`amp/models/Neve 2.nam`.

So the question was put to a config that does have an amp — `electric-clean`,
chosen to avoid the three files left uncommitted in the working tree:

```
Rig up: electric-clean (headless mode)
  qpwgraph   pid 30240
  sushi      pid 30258
  amp        pid 30259
```

```
$ pw-link -o | grep -i nam
NAM:Output
$ pw-link -i | grep -i nam
NAM:Input
NAM:events-in
```

**`probes.py`'s assumption is correct: the ports really are `NAM:Input` and
`NAM:Output`.** There is also a MIDI port, `NAM:events-in`, which `probes.py`
does not name and does not need.

### qpwgraph and foreign links

```
$ grep -i exclusive ~/.config/rncbc.org/qpwgraph.conf
Exclusive=false
```

with `AutoDisconnect=false` and `AutoPin=true` beside it. So qpwgraph is not in
exclusive mode and **does leave links that are not in its file alone**, which
is what `probes.py` assumes.

Confirmed live rather than only from the file: a link the patchbay does not
contain, `NAM:Output -> sushi:audio_input_7`, was created with the rig up and
was **still present 15 seconds later**. It was then removed, and the rig taken
down with `sushi-rig down`.

## Every fix made to the scripts

Both are in `tool/spikes/live_rig/` only; nothing outside it was changed.

1. **`capture_engine.py` — read the quantum after activation, not before.**
   Because `PIPEWIRE_QUANTUM` reaches a node only once it starts, the old read
   returned 1024 for runs that ran at 256, sized every buffer wrongly, and
   produced 20 seconds of silence with every health number reading 0. The
   script now activates once with no process callback, waits for the blocksize
   to settle, reads it, and deactivates before allocating. Added
   `quantum_asked` and `quantum_before_activate` to the report so a wrong
   quantum can never again be invisible. The RT callback is untouched.

2. **`live_paths.py` — the same read, moved for the same reason.** Here it was
   not fatal, but it reported `"quantum": 1024` for a run at 256, which makes
   `port_to_port_samples` meaningless: 257 is the expected answer *at 256*.
   Activating first also lets the quantum stop churning before the measurement
   window opens.

No thresholds were tuned, and nothing was re-run to turn a failure into a pass.
The two `as_expected: false` rows in step 4 are left exactly as measured.

## What could not run, and why

- **No busy or quantum figures from `pw-top`** for either spike: both are
  followers, and `pw-top` puts those columns on the driver row only. `ERR`
  was readable and was 0 throughout.
- **No desktop screenshot**: Wayland, no screenshot tool installed, and GNOME
  denies the Shell D-Bus screenshot API to unsandboxed callers. The panel was
  captured from open-stage-control's own web UI instead.
- **Nothing involving the Audient iD14 or a guitar**, as expected. This is what
  doctor's one FAULT is about, and the reason four of its checks are
  `unchecked`.
- **The mechanism inside Sushi** behind the 64-frame truncation: the behaviour
  is pinned down precisely, but explaining it needs Sushi's source.

## Still open, for the cloud session

1. **A take can gain a silent hole with every counter green.** One 256-sample
   block of pure silence landed on 3 of 6 columns at 207.6 s, with 0 xruns, 0
   lost frames, no dropout and no error, and the timing perfectly continuous
   across it. Ruled out as a recorder bug here, but the cause upstream is not
   identified and it is intermittent — it appeared in one of two 600 s runs.
   This is the one to chase next: it is the failure mode a recorder can least
   afford, and only reading the audio catches it.
2. **The 64-frame truncation is the other big one.** It silently breaks any Sushi
   input fed by more than one link at any quantum above 64 — `probes.py`'s
   central assumption in practice, even though the assumption about *PipeWire*
   is correct. Worth an upstream report, and worth deciding whether probes sum
   upstream or run at quantum 64.
3. **`port_to_port_samples` is 256, not 257.** Reproducible across six runs.
4. **`test_doctor.py` asserts `patchbay-nodes` unconditionally** and so passes
   only where the patchbay is already clean. Left unfixed under the README's
   scope rule.
5. **`test_render.py` hard-codes the number of configs** (`assert len(names)
   == 12`), so any new config fails it.
6. **`--hold` loses its report on EOF**, and both spikes could record the
   observed quantum in every report as a matter of course.

## A note on the rebase, and a second author on `capture_engine.py`

While this session was running, the cloud session pushed `14f6252` to the same
branch, touching the same file:

> *Laptop tests: a short block keeps the counter continuous; losses and
> repeats apart*

It makes three changes, all orthogonal to the quantum fix above and all kept:

- `continuity()` now counts losses and repeats **separately**, so a duplicated
  sample can no longer cancel a lost one, and reports `leading_silence`,
  `repeated_samples` and `first_jumps_at_file_index`.
- The short-block branch now zeroes the output and advances the counter, so a
  short block cannot leave a stale buffer that reads as a jump the ring never
  lost.
- A docstring note naming the callback's small per-block allocations as the
  first suspect if it misses deadlines.

Worth saying plainly: that commit **mitigates** the `frames != q` branch, but
the root cause was the early `blocksize` read fixed here. With the quantum read
after activation, `short_blocks` is 0 in every run and that branch never fires.
The two fixes are complementary — theirs makes the short-block path honest if
it is ever taken, this one stops it being taken for the wrong reason.

This session's commit was **rebased** onto `14f6252` rather than merged, so the
branch stays linear and nothing was merged. The two changes did not conflict.

**The capture runs below were then re-run from scratch on the rebased script**,
so every number in step 3 comes from the file as committed, with their
corrected `continuity()` doing the checking, not the version this session
started from.

## A note on what is committed

- The five `sushi-logs/*.log` files are committed with `git add -f`, because
  the repository's `.gitignore` excludes `*.log` as "Runtime logs". The README
  asks for them explicitly, so they are here rather than silently dropped —
  but it is a deliberate override of a project rule, and they are all five
  byte-identical and hold nothing but the two-line copyright banner, which is
  quoted in step 4 anyway. Drop them if the rule should win.
- `record-bar-panel.jpg` is the open-stage-control web UI, not a desktop
  window; see step 5 for why.
- **Nothing outside `tool/spikes/live_rig/` is in this commit.** The
  uncommitted `config/` work described in step 1 was left exactly as found.
