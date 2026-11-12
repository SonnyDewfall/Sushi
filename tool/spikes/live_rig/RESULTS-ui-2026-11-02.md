# Live rig test of #113-#118 — 2026-11-02

Machine: sonny-dewfall-XPS-9320, Audient iD14, electric guitar.
Branch: `claude/ui-tests` off `origin/master` at `ce78e31`.
Agent in terminal T1, commands in T2, browser on `http://localhost:8029`.

Sonny plays every PLAY step; his words are quoted verbatim and nothing is
written about how it "should" have sounded.

---

## Step 0 — SETUP — PASSED

```
$ cd ~/Sushi && git fetch origin && git status -sb && git log --oneline -1 origin/master
 * [new branch]      claude/amp-control -> origin/claude/amp-control
 * [new branch]      claude/ui-record   -> origin/claude/ui-record
 * [new branch]      claude/ui-save     -> origin/claude/ui-save
 * [new branch]      claude/ui-v01      -> origin/claude/ui-v01
## master...origin/master [behind 15]
ce78e31 Merge pull request #118 from SonnyDewfall/claude/ui-record
```

The tree was clean, so nothing had to be copied aside. Master was 15 behind:

```
$ git merge --ff-only origin/master
Updating fc589bc..ce78e31
Fast-forward
 40 files changed, 11666 insertions(+), 215 deletions(-)

$ git checkout -b claude/ui-tests origin/master
Switched to a new branch 'claude/ui-tests'
```

`stash@{0}` (pre-hear-v2 generated configs, from the laptop-tests session) was
left untouched, as it has been since 1 Nov.

### Install

```
$ cd tool && source .venv/bin/activate && pip install -e '.[dev,live,audio,agent,capture,midi]'
Successfully installed alsa-midi-1.0.4 ruamel.yaml-0.19.1 sushi-rig-0.1.0
```

Two new dependencies arrived with this merge: `ruamel.yaml` (the
comment-preserving save of #117) and `alsa-midi`.

### Doctor

```
$ sushi-rig doctor
5 check(s) could not be run. That is not a pass - most of these need PipeWire and a running rig.
       ok  machine-file: sonny-dewfall-XPS-9320.yaml is valid
unchecked  clock-driver: Sushi is not in the graph, so nothing is clocking it yet
           -> run `sushi-rig up` and check again
unchecked  one-clock: Sushi is not in the graph
unchecked  quantum: Sushi is not in the graph
unchecked  rate: Sushi is not in the graph
unchecked  patchbay-unchecked: 2 node(s) rig.qpwgraph names are started by the rig itself, so their absence says nothing while the rig is down
       ok  pw-jack: present at /usr/bin/pw-jack
       ok  jackd: no jackd is running, so PipeWire's JACK layer is the only one in play
       ok  carla: present at /usr/bin/carla
       ok  fuse2: ./sushi is not an AppImage, so FUSE is not needed
       ok  disk: 200.4 GB free at /home/sonny-dewfall/sushi-rig-takes: about 72.4 hours of a 4-stem take of guitar_vocal at 48000 Hz in float32
       ok  amp-models: 2 model(s), all trained at 48000 Hz

7 check(s) passed
```

Doctor refuses to call the five unrunnable checks a pass, and says so in its
first line. That is principle 8 behaving.

### Nothing to stop

```
$ ss -ltnp | grep 8029
nothing listening on 8029

$ ps aux | grep -E "sushi|carla|fmit|qpwgraph|recorder" | grep -v grep
none

$ sushi-rig down --direct
sushi-rig: --direct, so acting without asking the agent
No rig is running.
```

No agent and no rig were running, so no pid had to be killed.

### Agent

T1:

```
$ source .venv/bin/activate && sushi-rig agent
sushi-rig agent 0.1 on http://127.0.0.1:8029  (Ctrl+C to stop)
```

Exactly the expected line. **No `ELKPY:` lines at startup**, and none appeared
later in the whole session either — the log spam seen on 1 Nov is gone (#114).

```
$ ss -ltnp | grep 8029
LISTEN 0 128 127.0.0.1:8029 0.0.0.0:* users:(("sushi-rig",pid=8047,fd=6))
$ ps -p 8047 -o pid,ppid,stat,etime,cmd
   8047    7961 Sl+  00:20 .../python3 .../sushi-rig agent
```

Agent pid for the rest of this file: **8047**.

Note, not a fault: `sushi-rig --version` is not a flag; the version only
appears in the agent's banner.

---

## Step 1 — DOWN STATE (#115) — PASSED

```
$ curl -s -o /dev/null -w '%{http_code} %{redirect_url}\n' http://localhost:8029/ui/
302 http://localhost:8029/
```

As specified. On 1 Nov this same URL returned a `403` with a JSON body; #115
fixed it.

```
$ curl -s http://localhost:8029/health | python3 -m json.tool
{
    "ok": true,
    "agent": {
        "version": "0.1",
        "started_at": "2026-11-02T20:28:16+00:00",
        "uptime_seconds": 29.6,
        "busy": null,
        "stuck_live_calls": 0,
        "last_error": null,
        "midi": {
            "state": "off",
            "reason": "no rig is running",
            "rig": null, "controller": null, "port": null,
            "bindings": [], "programs": false, "heard": 0,
            "last": null, "problem": null, "last_error": null
        },
        "supervisor": {"holds": null, "crashed": [], "problems": [], "note": null}
    },
    "rig": {"verdict": "absent", "running": false, "message": "No rig is running."}
}
```

`last_error` is `null` on a fresh agent. (Whether it still fails to clear
after a handled error, flagged on 1 Nov, is not re-tested here.)

### Both pages

Both `/` and `/#stage` show a grey down state: heading "Rig down", "No rig is
running.", a `Locked` badge, and LOAD / XRUNS tiles reading **`rig down`**
under the hover text "Read from pw-top while a rig is up". AMP reads `none`.
The record panel says "Recording is off / No rig is running to record."

**Console over 35 s on both tabs: completely empty.** Not one message, let
alone an error.

Network, stage tab, 51 requests in that window, every one `200`:

```
GET http://localhost:8029/live/log → 200 OK
GET http://localhost:8029/health   → 200 OK
  (repeating, ~2 per second)
```

This is the second thing fixed from 1 Nov: the page used to poll `/live`,
which answered `503` with a connection-refused body whenever no rig was up,
and devtools filled with hundreds of red lines for a state that is not an
error. It now polls `/live/log` and `/health` and they both answer `200`.

Two small notes, neither a failure:

- `/` opens on **Stage**, not the rack. The brief calls `A/` "the rack";
  the rack is at `A/#rack`.
- On the stage page while the rig is down there is no rig picker at all, so a
  rig can only be started from `#rack`. The page's own lock text explains it:
  "start, stop and switch are hidden" while locked.

---

## Step 2 — START FROM THE UI (#113, #115) — PASSED

On `#rack`, RIGS lists eleven rigs: accustomed, electric-chorus-2,
electric-chorus-hall, electric-chorus, electric-clean, electric-delay,
electric-octave-up, electric-spacey-delay, electric_board, guitar_vocal,
hard-feelings.

Before a rig is picked the start button reads **"Choose a rig to start"** and
is inert; picking electric-chorus-2 changes it to **"Start electric-chorus-2"**.
So Start is visible while locked after all — it is the *stage* screen that
hides it, not the lock.

Timed in the page itself, from the click on Start:

| moment | elapsed |
|---|---|
| busy state shown: "The rig is changing; the rack comes back when it is up." | **178 ms** |
| rack back with the nine-plugin BOARD, title `electric-chorus-2 · sushi-rig` | **5317 ms** |

So: busy within a fifth of a second, chains at 5.3 s against the brief's
"about 5 s".

```
$ sushi-rig status
sushi-rig: asking the agent at http://127.0.0.1:8029
electric-chorus-2 (headless mode), up 5s
  config     /run/user/1000/sushi-rig/rigs/electric-chorus-2.json
  rig        config/rigs/electric-chorus-2.yaml
  machine    /home/sonny-dewfall/Sushi/machines/sonny-dewfall-XPS-9320.yaml
  group      8301
  fmit       pid 8302
  qpwgraph   pid 8316
  recorder   pid 8462
  sushi      pid 8463
  amp        pid 8464
  amp model  amp/models/Neve 2.nam
  logs       /run/user/1000/sushi-rig/logs
```

It asks the agent rather than reading the filesystem, and the child list now
includes **recorder pid 8462**, which is new with #118.

### PLAY (step 2)

Asked: does it sound like last night's electric-chorus-2?

> **"Yeah sounds great"**

That is his whole answer, recorded as given. He did not say in so many words
whether it matched last night, and was not pressed on it.

---

## Step 3 — FILE HANDLES STAY FLAT (#114) — PASSED

```
$ P=$(ss -ltnp | grep 8029 | grep -o 'pid=[0-9]*' | cut -d= -f2); ls /proc/$P/fd | wc -l
P=8047
fd count: 14
at: 2026-11-02T20:50:16+00:00
```

The 14 break down as: stdin/stdout/stderr on `/dev/pts/1`, one
`anon_inode:[eventpoll]`, nine sockets, and one oddity —

```
103 -> /usr/lib/claude-desktop/v8_context_snapshot.bin
```

That is a descriptor **inherited from the process that spawned the shell**
(the Claude desktop app's terminal), not something sushi-rig opened. It is
not a sushi-rig fault, but it is worth knowing that an agent started from a
terminal inherits whatever its parent left open: an fd-count check that
expects a fixed number will read differently depending on how the agent was
launched.

Second reading, with both pages open throughout, recorded below after the
ten minutes had elapsed.

---

## Step 4 — LOAD AND XRUNS (#115) — PASSED

### The open question is settled: follower rows show numbers, not `---`

In `pw-top -b -n 2` the **first** iteration prints `---` for every node,
including the followers. The **second** iteration has real figures for every
*running* node — NAM, Carla, sushi and fmit all show WAIT, BUSY, W/Q and B/Q:

```
R   96    256  48000   1.6ms   1.4us  0.30  0.00    6   S32LE 10 48000 alsa_input.usb-Audient_Audient_iD14-00.multichannel-input
R   95      0      0  14.8us  40.1us  0.00  0.01    0    S32LE 4 48000  + alsa_output.usb-Audient_Audient_iD14-00.analog-surround-40
R  194   2048  22050  81.6us  30.9us  0.02  0.01    0    S16LE 1 22050  + alsa_capture.fmit
R  199    256  48000  46.4us 100.1us  0.01  0.02    0                   + sushi
R  221    256  48000  81.4us   2.5us  0.02  0.00    1                   + Carla
R  223    256  48000  53.0us   1.3ms  0.01  0.25    7                   + NAM
```

The `+` prefix marks them as followers of driver 96 (the iD14 input), and
they are followers *with numbers*. The rows that stay `---` in the second
pass are the **suspended** nodes (`S` in the first column): the webcams, the
Røde mic, the Logitech, Dummy-Driver, Midi-Bridge. So `---` means "not
running", not "is a follower".

This is also why the command needs `-n 2`: with `-n 1` every row would read
`---` and the output would look like total failure.

### The two sources agree

Idle, seconds apart:

```
$ sushi-rig top --once
electric-chorus-2 (headless mode)
quantum 256 @ 48000 Hz — 5.3 ms per block  [clocked by alsa_input.usb-Audient_Audient_iD14-00.multichannel-input]

  node            busy  load                             xruns
  NAM            1.2ms  ██████░░░░░░░░░░░░░░░░░░   23%   7
  Carla            3us  ░░░░░░░░░░░░░░░░░░░░░░░░    0%   1
  sushi          131us  ░░░░░░░░░░░░░░░░░░░░░░░░    2%   0
  ----------------------------------------------------------
  total          1.3ms  ██████░░░░░░░░░░░░░░░░░░   25%
```

Stage tiles at the same moment:

| tile | value | tooltip |
|---|---|---|
| LOAD | `29%` then `33%` then `26%` of 5.3 ms | `NAM 30%, Carla 0%, sushi 3% · quantum 256 @ 48000 Hz · clocked by alsa_input...iD14...multichannel-input · pw-top does not list fmit` |
| XRUNS | `8` since opened | `8 in all since each node appeared (pw-top never resets them): NAM 7, Carla 1, sushi 0` |
| AMP | `Neve 2` | `amp/models/Neve 2.nam` |

Same structure, same components, different sampling instants. The tile's
tooltip volunteers **"pw-top does not list fmit"** rather than quietly
dropping it — an absence named instead of hidden.

### Under signal

Sampled every 4 s for two minutes across the play (24 samples, full log kept
in the session scratchpad). Total load by sample:

```
25 26 25 26 24 29 28 25 20 26 22 28 27 25 26 23 25 25 23 25  (%)
```

Range 20-29%, and **NAM's xrun column never moved off 7 for the whole
window**. No new xrun was produced by playing. Load does not visibly track
whether a note is sounding, which is what you would expect of a NAM
convolution that runs on every block regardless of input.

### PLAY (step 4)

Asked for about 30 s of playing, and whether anything stood out.

> **"I'm done"**

No observation offered, and none invented here.

### "Sound" still reads not measured

```
Up 22 min · sound not measured
```

with the hover text:

> "Nothing measures the rig's sound yet (#49). Load and xruns say whether the
> rig keeps up with its blocks, not whether it sounds right."

As specified, and it says *why* rather than just going blank.

### Raw `pw-top -b -n 2`

Captured again at the timestamp shown, after the play, unedited:

```
$ date -Is && pw-top -b -n 2
2026-11-02T20:54:31+00:00
S   ID  QUANT   RATE    WAIT    BUSY   W/Q   B/Q  ERR FORMAT           NAME 
C   30      0      0    ---     ---   ---   ---     0                  Dummy-Driver
C   31      0      0    ---     ---   ---   ---     0                  Freewheel-Driver
C   52      0      0    ---     ---   ---   ---     0                  Midi-Bridge
C   55      0      0    ---     ---   ---   ---     0                  bluez_midi.server
C   58      0      0    ---     ---   ---   ---     0                  alsa_input.usb-046d_Logitech_BRIO_C256FA3E-03.analog-stereo
C   95      0      0    ---     ---   ---   ---     0                  alsa_output.usb-Audient_Audient_iD14-00.analog-surround-40
C   96      0      0    ---     ---   ---   ---     0                  alsa_input.usb-Audient_Audient_iD14-00.multichannel-input
C   34      0      0    ---     ---   ---   ---     0                  alsa_output.usb-R__DE_Microphones_R__DE_NT-USB_Mini_EBBF286C-00.analog-stereo
C   47      0      0    ---     ---   ---   ---     0                  alsa_input.usb-R__DE_Microphones_R__DE_NT-USB_Mini_EBBF286C-00.mono-fallback
C  119      0      0    ---     ---   ---   ---     0                  v4l2_input.pci-0000_00_0d.0-usb-0_1.2.4.3_1.0
C  121      0      0    ---     ---   ---   ---     0                  v4l2_input.pci-0000_00_0d.0-usb-0_1.2.4.3_1.0.3
C  123      0      0    ---     ---   ---   ---     0                  v4l2_input.pci-0000_00_05.0
C  125      0      0    ---     ---   ---   ---     0                  v4l2_input.pci-0000_00_05.0.2
C  127      0      0    ---     ---   ---   ---     0                  v4l2_input.pci-0000_00_05.0.3
C  129      0      0    ---     ---   ---   ---     0                  v4l2_input.pci-0000_00_05.0.4
C  131      0      0    ---     ---   ---   ---     0                  v4l2_input.pci-0000_00_05.0.5
C  133      0      0    ---     ---   ---   ---     0                  v4l2_input.pci-0000_00_05.0.6
C  135      0      0    ---     ---   ---   ---     0                  v4l2_input.pci-0000_00_05.0.7
C  137      0      0    ---     ---   ---   ---     0                  v4l2_input.pci-0000_00_05.0.8
C  139      0      0    ---     ---   ---   ---     0                  v4l2_input.pci-0000_00_05.0.9
C  141      0      0    ---     ---   ---   ---     0                  v4l2_input.pci-0000_00_05.0.10
C  143      0      0    ---     ---   ---   ---     0                  v4l2_input.pci-0000_00_05.0.11
C  145      0      0    ---     ---   ---   ---     0                  v4l2_input.pci-0000_00_05.0.12
C  147      0      0    ---     ---   ---   ---     0                  v4l2_input.pci-0000_00_05.0.13
C  149      0      0    ---     ---   ---   ---     0                  v4l2_input.pci-0000_00_05.0.14
C  151      0      0    ---     ---   ---   ---     0                  v4l2_input.pci-0000_00_05.0.15
C  153      0      0    ---     ---   ---   ---     0                  v4l2_input.pci-0000_00_05.0.16
C  155      0      0    ---     ---   ---   ---     0                  v4l2_input.pci-0000_00_05.0.17
C  157      0      0    ---     ---   ---   ---     0                  v4l2_input.pci-0000_00_05.0.18
C  159      0      0    ---     ---   ---   ---     0                  v4l2_input.pci-0000_00_05.0.19
C  161      0      0    ---     ---   ---   ---     0                  v4l2_input.pci-0000_00_05.0.20
C  163      0      0    ---     ---   ---   ---     0                  v4l2_input.pci-0000_00_05.0.21
C  165      0      0    ---     ---   ---   ---     0                  v4l2_input.pci-0000_00_05.0.22
C  167      0      0    ---     ---   ---   ---     0                  v4l2_input.pci-0000_00_05.0.23
C  169      0      0    ---     ---   ---   ---     0                  v4l2_input.pci-0000_00_05.0.24
C  171      0      0    ---     ---   ---   ---     0                  v4l2_input.pci-0000_00_05.0.25
C  173      0      0    ---     ---   ---   ---     0                  v4l2_input.pci-0000_00_05.0.26
C  175      0      0    ---     ---   ---   ---     0                  v4l2_input.pci-0000_00_05.0
C  177      0      0    ---     ---   ---   ---     0                  v4l2_input.pci-0000_00_05.0.2
C  179      0      0    ---     ---   ---   ---     0                  v4l2_input.pci-0000_00_05.0.3
C  181      0      0    ---     ---   ---   ---     0                  v4l2_input.pci-0000_00_05.0.4
C  183      0      0    ---     ---   ---   ---     0                  v4l2_input.pci-0000_00_05.0.5
C  185      0      0    ---     ---   ---   ---     0                  v4l2_input.pci-0000_00_05.0.6
C  194      0      0    ---     ---   ---   ---     0                  alsa_capture.fmit
C  199      0      0    ---     ---   ---   ---     0                  sushi
C  221      0      0    ---     ---   ---   ---     0                  Carla
C  223      0      0    ---     ---   ---   ---     0                  NAM
S   ID  QUANT   RATE    WAIT    BUSY   W/Q   B/Q  ERR FORMAT           NAME 
I   30      0      0   0.0us   0.0us  ???   ???     0                  Dummy-Driver
S   31      0      0    ---     ---   ---   ---     0                  Freewheel-Driver
S   52      0      0    ---     ---   ---   ---     0                  Midi-Bridge
S   55      0      0    ---     ---   ---   ---     0                  bluez_midi.server
S   58      0      0    ---     ---   ---   ---     0                  alsa_input.usb-046d_Logitech_BRIO_C256FA3E-03.analog-stereo
R   96    256  48000   1.8ms   2.1us  0.34  0.00    6   S32LE 10 48000 alsa_input.usb-Audient_Audient_iD14-00.multichannel-input
R   95      0      0  54.1us  55.6us  0.01  0.01    0    S32LE 4 48000  + alsa_output.usb-Audient_Audient_iD14-00.analog-surround-40
R  194   2048  22050  46.0us  37.1us  0.01  0.01    0    S16LE 1 22050  + alsa_capture.fmit
R  199    256  48000  39.1us 109.2us  0.01  0.02    0                   + sushi
R  221    256  48000  66.4us   3.0us  0.01  0.00    1                   + Carla
R  223    256  48000  40.6us   1.5ms  0.01  0.28    7                   + NAM
S   34      0      0    ---     ---   ---   ---     0                  alsa_output.usb-R__DE_Microphones_R__DE_NT-USB_Mini_EBBF286C-00.analog-stereo
S   47      0      0    ---     ---   ---   ---     0                  alsa_input.usb-R__DE_Microphones_R__DE_NT-USB_Mini_EBBF286C-00.mono-fallback
S  119      0      0    ---     ---   ---   ---     0                  v4l2_input.pci-0000_00_0d.0-usb-0_1.2.4.3_1.0
S  121      0      0    ---     ---   ---   ---     0                  v4l2_input.pci-0000_00_0d.0-usb-0_1.2.4.3_1.0.3
S  123      0      0    ---     ---   ---   ---     0                  v4l2_input.pci-0000_00_05.0
S  125      0      0    ---     ---   ---   ---     0                  v4l2_input.pci-0000_00_05.0.2
S  127      0      0    ---     ---   ---   ---     0                  v4l2_input.pci-0000_00_05.0.3
S  129      0      0    ---     ---   ---   ---     0                  v4l2_input.pci-0000_00_05.0.4
S  131      0      0    ---     ---   ---   ---     0                  v4l2_input.pci-0000_00_05.0.5
S  133      0      0    ---     ---   ---   ---     0                  v4l2_input.pci-0000_00_05.0.6
S  135      0      0    ---     ---   ---   ---     0                  v4l2_input.pci-0000_00_05.0.7
S  137      0      0    ---     ---   ---   ---     0                  v4l2_input.pci-0000_00_05.0.8
S  139      0      0    ---     ---   ---   ---     0                  v4l2_input.pci-0000_00_05.0.9
S  141      0      0    ---     ---   ---   ---     0                  v4l2_input.pci-0000_00_05.0.10
S  143      0      0    ---     ---   ---   ---     0                  v4l2_input.pci-0000_00_05.0.11
S  145      0      0    ---     ---   ---   ---     0                  v4l2_input.pci-0000_00_05.0.12
S  147      0      0    ---     ---   ---   ---     0                  v4l2_input.pci-0000_00_05.0.13
S  149      0      0    ---     ---   ---   ---     0                  v4l2_input.pci-0000_00_05.0.14
S  151      0      0    ---     ---   ---   ---     0                  v4l2_input.pci-0000_00_05.0.15
S  153      0      0    ---     ---   ---   ---     0                  v4l2_input.pci-0000_00_05.0.16
S  155      0      0    ---     ---   ---   ---     0                  v4l2_input.pci-0000_00_05.0.17
S  157      0      0    ---     ---   ---   ---     0                  v4l2_input.pci-0000_00_05.0.18
S  159      0      0    ---     ---   ---   ---     0                  v4l2_input.pci-0000_00_05.0.19
S  161      0      0    ---     ---   ---   ---     0                  v4l2_input.pci-0000_00_05.0.20
S  163      0      0    ---     ---   ---   ---     0                  v4l2_input.pci-0000_00_05.0.21
S  165      0      0    ---     ---   ---   ---     0                  v4l2_input.pci-0000_00_05.0.22
S  167      0      0    ---     ---   ---   ---     0                  v4l2_input.pci-0000_00_05.0.23
S  169      0      0    ---     ---   ---   ---     0                  v4l2_input.pci-0000_00_05.0.24
S  171      0      0    ---     ---   ---   ---     0                  v4l2_input.pci-0000_00_05.0.25
S  173      0      0    ---     ---   ---   ---     0                  v4l2_input.pci-0000_00_05.0.26
S  175      0      0    ---     ---   ---   ---     0                  v4l2_input.pci-0000_00_05.0
S  177      0      0    ---     ---   ---   ---     0                  v4l2_input.pci-0000_00_05.0.2
S  179      0      0    ---     ---   ---   ---     0                  v4l2_input.pci-0000_00_05.0.3
S  181      0      0    ---     ---   ---   ---     0                  v4l2_input.pci-0000_00_05.0.4
S  183      0      0    ---     ---   ---   ---     0                  v4l2_input.pci-0000_00_05.0.5
S  185      0      0    ---     ---   ---   ---     0                  v4l2_input.pci-0000_00_05.0.6
```

---

## Step 3, second reading — the count is NOT flat

Ten minutes later, both pages open the whole time, nothing else touched:

```
=== second reading ===
at: 2026-11-02T21:00:16+00:00
P=8047
fd count: 19
```

**14 at 20:50:16, 19 at 21:00:16.** The brief expected "roughly the same
number (13-ish)". Five more descriptors in ten idle minutes is not that, so
it was worth finding out what the five are before calling it anything.

```
$ ss -tanp | grep "pid=8047,"
LISTEN 0 128 127.0.0.1:8029 0.0.0.0:*
ESTAB  0 0   127.0.0.1:8029 127.0.0.1:59764
ESTAB  0 0   127.0.0.1:8029 127.0.0.1:56440
ESTAB  0 0   127.0.0.1:8029 127.0.0.1:52516
ESTAB  0 0   127.0.0.1:8029 127.0.0.1:47620
ESTAB  0 0   127.0.0.1:8029 127.0.0.1:45062
ESTAB  0 0   127.0.0.1:8029 127.0.0.1:45058
ESTAB  0 0   127.0.0.1:8029 127.0.0.1:45042
ESTAB  0 0   127.0.0.1:8029 127.0.0.1:42436
ESTAB  0 0   127.0.0.1:8029 127.0.0.1:34842
ESTAB  0 0   127.0.0.1:8029 127.0.0.1:34828
ESTAB  0 0   127.0.0.1:8029 127.0.0.1:34812

  11 ESTAB
   1 LISTEN
```

Every added socket is an **inbound** HTTP keep-alive connection *to* the
agent's own port 8029, from the two browser tabs. The local address is 8029
on every line; none is an outbound connection.

**There is not one connection to 51051.** That is the thing #114 was about:
elkpy connections to Sushi's gRPC port being opened per poll and never
closed. On this evidence that leak is gone — the agent holds no connection to
Sushi at all between polls.

What is growing instead is inbound keep-alives from the browser. The rate
rules out a per-request leak: the two pages make roughly two requests a
second, so a per-request leak would have added about 1,200 descriptors in
those ten minutes, not five.

### Verdict: flat, with transients — PASSED

Sampled every 30 s for six minutes, pages idle:

```
21:01:00  fds=30  estab=13  to_sushi_51051=2
21:01:30  fds=18  estab=10  to_sushi_51051=0
21:02:00  fds=24  estab=10  to_sushi_51051=0
21:02:31  fds=18  estab=12  to_sushi_51051=0
21:03:01  fds=18  estab=10  to_sushi_51051=0
21:03:31  fds=18  estab=10  to_sushi_51051=0
21:04:01  fds=19  estab=11  to_sushi_51051=0
21:04:31  fds=19  estab=11  to_sushi_51051=0
21:05:01  fds=18  estab=12  to_sushi_51051=0
21:05:31  fds=32  estab=10  to_sushi_51051=2
21:06:01  fds=18  estab=12  to_sushi_51051=0
21:06:32  fds=18  estab=10  to_sushi_51051=0
21:07:02  fds=18  estab=10  to_sushi_51051=0
```

The steady state is **18-19**, held for six minutes with no upward drift.
The 14 at 20:50 was taken moments after the rig came up, before the two
browser tabs had settled their keep-alive pools; 18-19 is where it sits with
two pages open.

The two spikes are the interesting part. At 21:01:00 and 21:05:31 the count
jumps to 30 and 32, and in **exactly those two samples** there are two
connections to **51051** — the agent talking to Sushi. Thirty seconds later
both the connections and the extra dozen descriptors are gone.

That is the shape you want: open, use, close. An elkpy conversation costs
about a dozen descriptors while it happens and gives every one of them back.
Before #114 they were kept.

So step 3 passes on the property it exists to test — the count does not grow
— while the brief's "13-ish" is simply the wrong number for two open browser
tabs. A single tab, or none, would sit lower.

Worth saying plainly: had the second reading been taken as a bare number and
compared with "13-ish", this would have been written down as a failure. It
is not one.

---

## Step 5 — CRASH SHOWS, NOT HEALED (#113) — PASSED

The tuner was identified from status and confirmed with `ps` before anything
was killed, and killed by that pid alone:

```
$ sushi-rig status
  ...
  fmit       pid 8302
  ...
$ ps -p 8302 -o pid,ppid,stat,cmd --no-headers
   8302    8047 RLl  fmit

$ kill 8302          # at 2026-11-02T21:08:22+00:00
$ ps -p 8302 ...
gone: no such process
```

Within 1.5 seconds:

```
$ curl -s http://localhost:8029/health | grep -A8 crashed
"crashed": [{"name": "fmit", "pid": 8302, "returncode": -15,
             "signal": "SIGTERM", "at": "2026-11-02T21:08:22+00:00",
             "phase": "running", "config_name": "electric-chorus-2",
             "log": "/run/user/1000/sushi-rig/logs/fmit.log"}]
```

and the supervisor's `holds.children` has `"fmit": {"pid": 8302, "running": false, "returncode": -15}`
while every other child stays `running: true`.

```
$ sushi-rig status
electric-chorus-2 (headless mode), up 37m 21s
  ...
  fmit       pid 8302 (DEAD)
  qpwgraph   pid 8316
  recorder   pid 8462
  sushi      pid 8463
  amp        pid 8464
  ...
  CRASHED    fmit (pid 8302) killed by SIGTERM at 2026-11-02T21:08:22+00:00 — see /run/user/1000/sushi-rig/logs/fmit.log
```

Nothing restarted it, then or later:

```
$ ps -p 8302 ...        ->  still gone at 2026-11-02T21:08:52+00:00
$ pgrep -a fmit         ->  no fmit process anywhere
```

The agent's own line in T1 is the best thing in this step:

> `sushi-rig agent: fmit (pid 8302) was killed by SIGTERM while it was up in
> electric-chorus-2; its log is /run/user/1000/sushi-rig/logs/fmit.log. Not
> restarted: the agent heals nothing on its own (principle 9). POST
> /rig/restart brings the rig back.`

What died, how, where its log is, that nothing will be done about it, why
not, and what the operator can do instead — in one line.

### Finding 1: the crash is invisible in the UI

Neither screen shows it. On `#stage` the header still reads
`electric-chorus-2 / Locked / Up 37 min · sound not measured` with LOAD,
XRUNS and AMP unchanged; on `#rack` a text search for crash / fmit / tuner /
DEAD over the whole rendered page returns **false**. The only sign is a
stale toast reading "Started electric-chorus-2, headless."

So a child of the rig died, `/health` knows, `status` knows, the terminal
knows — and the two pages a player actually looks at say nothing. Against
principle 8 that is the wrong way round: the person watching the screen is
the one who needs to be told. (The brief only required `/health` and
`status` to report it, and both do; this is noted as a gap, not a failure of
the step.)

### Finding 2: new log spam has replaced the old log spam

T1 is filling with this, over and over:

```
warning: 'master_return' returned no parameters
```

It is the agent's own line, not Sushi's — `tool/src/sushi_rig/live.py:187`:

```python
param_infos = controller.parameters.get_processor_parameters(proc_id)
if not param_infos:
    print(f"warning: {proc_name!r} returned no parameters", file=sys.stderr)
```

The loop runs over every track *and* every plugin on every live capture.
`master_return` is a rig's master return track (`rigfile.py:81`,
`RETURN = "master_return"`); on this rig it carries no plugins — the rack
shows `master · 0 plugins` — so it legitimately has no parameters, and the
warning fires on every capture, roughly once a second with pages open.

This matters because of what it did here: the genuine crash line, the one
line in the whole session that reported a process dying, arrived with ten of
these above it and nineteen below it within seconds. The `ELKPY:` spam that
#114 removed has been replaced by a different constant warning, and it is a
warning about a condition that is normal.

It appears in no log file — `grep -c master_return` over every file in
`/run/user/1000/sushi-rig/logs/` returns 0 for all five. It goes to the
agent's stderr only.

Not fixed here: the brief says to change no file outside the results.

### PLAY (step 5)

Asked: does the guitar still sound with the tuner dead?

> **"Yes"**

The audio path is untouched by fmit's death, which is the point — the tuner
is a convenience, not part of the chain.

---

## Step 6 — AMP (#116) — PASSED

### `/amp`

```
$ curl -s http://localhost:8029/amp | python3 -m json.tool
{
    "ok": true,
    "known": "sent",
    "known_note": "what this agent last sent Carla, not what Carla holds: Carla cannot be asked",
    "model": {
        "stored": "amp/models/Neve 2.nam",
        "name": "Rupert Tape 1 Silk RED Tape rupert Tape 1",
        "path": "/home/sonny-dewfall/Sushi/amp/models/Neve 2.nam",
        "description": "...
  gear      rupert tape 542 / outboard
  by        oswaldallicock
  format    SlimmableContainer  (NAM A2 — Quality selects a tier)
  rate      48000 Hz  (matches the rig)
  2 path(s):
    [0] WaveNet  channels=3         1,871 weights   Quality < 0.5
    [1] WaveNet  channels=8        12,146 weights   Quality > 0.5
  The full path has 6.5x the parameters of the lite one....",
        "sample_rate": 48000, "tiers": 2, "problem": null, "warning": null
    },
    "parameters": [
        {"name": "Input Lvl",  "value": 0.0, "min": -20.0, "max": 20.0, "unit": "dB", "panel": true,  "source": "replayed"},
        {"name": "Output Lvl", "value": 6.0, "min": -20.0, "max": 20.0, "unit": "dB", "panel": true,  "source": "replayed"},
        {"name": "Quality",    "value": 1.0, "min": 0.0,   "max": 1.0,  "unit": null, "panel": false, "source": "replayed"}
    ],
    "override": null,
    "carla": {"pid": 8464, "alive": true},
    "held": true
}
```

`"known": "sent"` as the brief predicted, and it does not pretend otherwise:
**"what this agent last sent Carla, not what Carla holds: Carla cannot be
asked"**. That is the honest form of a value it cannot verify.

Each parameter carries a `source`. Initially all three read `replayed` —
re-sent from the config at startup. Only a value the agent has since sent in
anger becomes `sent`. The provenance of every number is on the number.

### `/amp/models`

```
$ curl -s http://localhost:8029/amp/models | python3 -m json.tool
{
    "ok": true, "rate": 48000,
    "models": [
        {"model": "Neve 1.nam", "playing": false, "name": "Rupert Neve Design RNDI-M",
         "sample_rate": 48000, "usable": true, "why": null, "warning": null,
         "architecture": "SlimmableContainer", "tiers": 2,
         "gear": "Rupert Neve Designs RNDI-M / studio"},
        {"model": "Neve 2.nam", "playing": true,  "name": "Rupert Tape 1 Silk RED Tape rupert Tape 1",
         "sample_rate": 48000, "usable": true, "why": null, "warning": null,
         "architecture": "SlimmableContainer", "tiers": 2,
         "gear": "rupert tape 542 / outboard"}
    ]
}
```

`usable` comes with a `why` for when it is false, and `rate` is given at the
top so a model at the wrong rate can be explained rather than just rejected.

### Finding 3: the two screens name the same model differently

The stage tile reads **`Neve 2`** (the filename stem, with
`amp/models/Neve 2.nam` as its tooltip). The rack's amp card reads
**`Rupert Tape 1 Silk RED Tape rupert Tape 1`** — the name embedded in the
NAM file. Both are correct and they are the same model, but a player glancing
between the two screens has no way to know that. `/amp` returns both, as
`model.stored` and `model.name`.

### The Output fader, moved on the rack while playing

Driven 6.0 dB -> 0.0 dB -> 6.0 dB in 0.5 dB steps, 300 ms apart: 25 discrete
changes over 10.0 s. The readout tracked every step (`6.0 dB`, `5.5 dB`,
`5.0 dB` ... and back), and `/amp` afterwards showed
`Output Lvl = 6.0 dB | source: sent` — the one parameter touched had changed
provenance, the other two still `replayed`.

**PLAY.** Asked whether the change was audible and whether it followed the fader:

> **"It got quieter and then louder as expected - no crackles or artefactikng"**

Twenty-five stepped parameter sends in ten seconds and no zipper noise.

### `POST /amp/parameter`

```
$ curl -s -X POST http://localhost:8029/amp/parameter \
       -H 'content-type: application/json' -d '{"name": "Output Lvl", "value": -6}'
{
    "ok": true,
    "at": "2026-11-02T22:10:07+00:00",
    "op": "amp",
    "args": {"name": "Output Lvl", "value": -6.0},
    "previous": 6.0,
    "asked": -6.0,
    "clamped": null
}
```

It reports `previous`, `asked` and `clamped` — so a request that silently hit
a limit would be visible instead of reading as a plain success.

**The rack fader followed a change it did not make**, within 2.5 s, and
annotated itself:

```
fader value: -6        shown: −6.0 dB        note: "was 6.0 dB"
```

### The lock refuses the model change until it is told twice

While locked, the model `<select>` carries `disabled` and the note reads:

> "Locked: changing the amp's model cuts the sound while the amp restarts,
> which the lock refuses. Unlock to change it."

Two taps on the lock (`Performance lock is on. Tap twice to unlock structural
edits`) and the button becomes `Lock off · editing` / `Performance lock is
off. Tap to lock`, the select is enabled, and the same note changes from a
refusal to an explanation:

> "Changing the model restarts the amp: the sound cuts out for a moment while
> it does. The rest of the rig keeps playing."

The currently playing model is `disabled` in its own list and labelled
`(playing)`, so it cannot be re-selected into a pointless restart.

Choosing the other model does not act: it raises a confirm group
(`role="group"`, `aria-label="Confirm the model change"`) with the
consequence spelled out and a `danger`-classed button beside a Cancel:

```html
<p>Load Rupert Neve Design RNDI-M? Changing the model restarts the amp: the
sound cuts out for a moment while it does. The rest of the rig keeps playing.</p>
<button class="rig-act danger">Load Rupert Neve Design RNDI-M</button>
<button class="rig-act">Cancel</button>
```

### Timing the silence

`/amp` polled ten times a second across the change; confirm at epoch
1793657510.947:

```
  -17.26s  stored=amp/models/Neve 2.nam carla_pid=8464   alive=True out=-6.0
   +0.06s  ERR HTTPError: HTTP Error 409: Conflict
   +1.26s  stored=amp/models/Neve 1.nam carla_pid=293332 alive=True out=-6.0
```

| moment | from confirm |
|---|---|
| `GET /amp` answers **409 Conflict** — busy, refusing to report mid-change | +0.06 s |
| new Carla, pid **293332**, alive, `Neve 1.nam` loaded | **+1.26 s** |
| rack card shows the new model | +1.42 s |

**About 1.3 s of amp restart.** The 409 is worth a note: a *read* answering
409 while the agent is busy is unusual (503 would be the conventional
choice), but it is at least explicit — a poller gets an error for one sample
rather than a stale or half-true answer.

### Output held its value — the question this step exists to ask

```
$ curl -s http://localhost:8029/amp
model: amp/models/Neve 1.nam / Rupert Neve Design RNDI-M
carla: {'pid': 293332, 'alive': True}
  Input Lvl = 0.0 dB | source: replayed
  Output Lvl = -6.0 dB | source: sent
  Quality = 1.0  | source: replayed
```

**−6.0 dB survived a full Carla kill-and-reload.** It did not revert to the
6.0 dB in the config, and it is still marked `sent`, not `replayed`.

The graph agrees — NAM is processing again, with its xrun counters reset to
zero because it is a new process:

```
R  199    256  48000  91.5us 111.6us  0.02  0.02    0    + sushi
R  221    256  48000  42.2us   5.4us  0.01  0.00    0    + Carla
R  223    256  48000  23.2us   1.3ms  0.00  0.24    0    + NAM
```

And the crash record is not wiped by unrelated activity — `status` still
carries `fmit pid 8302 (DEAD)` and the `CRASHED` line after the amp restarted
underneath it.

### PLAY (step 6)

Asked three things: how the new model sounds, whether it is still quieter,
and how long the silence felt.

> **"Sounds great"**
>
> **"Still quieter as expected, model is correct."**
>
> **"Like 1-2 secs of silence"**

His "1-2 secs" against the measured 1.26 s from confirm to a live Carla: the
instrument and the clock agree. And "still quieter as expected" is the
listening half of the `-6.0 dB` that `/amp` reports — the value did not just
survive in the API, it survived in the air.

### Back to the original model

```
confirm label: "Load Rupert Tape 1 Silk RED Tape rupert Tape 1"
  +0.01s  amp/models/Neve 1.nam pid=293332 alive=true out=-6
  +0.12s  HTTP 409
  +1.18s  amp/models/Neve 2.nam pid=301100 alive=true out=-6
```

**1.18 s**, against 1.26 s in the other direction. Two model changes, both
about 1.2 s, both with the same one-sample 409 while busy.

Output Lvl was then restored to 6.0 over HTTP, so that step 8's save diff
would show only the knob step 8 turns and not a -6.0 dB left behind by this
step:

```
{"ok": true, "previous": -6.0, "asked": 6.0, "clamped": null}
```

Live state now matches `config/rigs/electric-chorus-2.yaml` exactly:

```
amp:
  plugin: nam
  model: amp/models/Neve 2.nam
  parameters:
    Input Lvl: 0.0
    Output Lvl: 6.0
    Quality: 1.0
```

---

## Step 7 — LIVE SWITCH (#112, #115) — PASSED

### Both answers: `live`

`/live/log`, both switches:

```json
{"at": "2026-11-02T22:15:23+00:00", "op": "rig",
 "args": {"rig": "electric-delay", "how": "live",
          "plan": {"move": 3, "parameter": 18, "amp": 1, "bypass": 1}},
 "previous": "electric-chorus-2"}

{"at": "2026-11-02T22:16:34+00:00", "op": "rig",
 "args": {"rig": "electric-chorus-2", "how": "live",
          "plan": {"bypass": 1, "move": 3, "parameter": 18, "amp": 1}},
 "previous": "electric-delay"}
```

The **why** is the `plan`: it does not merely claim to be live, it says what
it did instead of restarting — 3 processor moves, 18 parameter changes, 1 amp
change, 1 bypass. The two plans are identical, which is what A->B and B->A
between the same pair should look like.

And the claim is checkable. Every pid is the same before and after **both**
switches:

| | before | after both |
|---|---|---|
| group | 8301 | 8301 |
| qpwgraph | 8316 | 8316 |
| recorder | 8462 | 8462 |
| sushi | 8463 | 8463 |
| amp | 301100 | 301100 |

Nothing was restarted. The `CRASHED fmit` record also survived both.

### Timing

| switch | time |
|---|---|
| to electric-delay | under ~1.2 s — it had already finished by the first poll |
| back to electric-chorus-2 | **683 ms** |

During the second, `/health` showed `busy: "switch"` at +11 ms and
`busy: null` at +683 ms, so the agent names the operation it is in the middle
of rather than just being unresponsive.

### Finding 4: the switch acts on the first click, with no confirmation

Clicking "Switch to electric-delay" started the switch immediately — the
section went straight to "switch is running". There is no confirm group, in
contrast to the amp model change, which raises a `danger` button and a Cancel.

On reflection this reads as a considered distinction rather than an
oversight: the amp change confirms because it **costs you sound for a
second**; a live switch does not. It is recorded here because it has a
practical consequence — there is no pause between the click and the act, so
there is no moment in which to start playing. The first switch therefore
happened in silence, and only the second was heard.

### PLAY (step 7)

Asked about tone change, pops or clicks at the seam, and whether it felt
instant:

> **"No crackles or artefacting, very clean."**

Worth putting next to the amp fader result: that was 25 sends spread over
10 s. This was **23 changes (3 moves + 18 parameters + 1 amp + 1 bypass) in
under 700 ms**, applied to a graph with a guitar going through it, and it
came out clean.

---

## Step 8 — SAVE (#117) — PASSED on comments, FAILED on "only that value changed"

### Turning one knob

Knobs are `div.knob[role="slider"]` with `tabindex="0"` and full ARIA
(`aria-valuemin/max/now/valuetext`) — keyboard-operable, so this was done
with six real `Down` key presses on the focused chorus Level knob, not by
poking the DOM.

`chorus Level: 0.91 -> 0.85`, and the knob labelled itself `was 0.91`.

The unsaved-changes panel then read:

```
1 unsaved change
made through the agent since the rig came up
board · chorus · Level
0.91 to 0.85
[Undo · chorus · Level]  [Save to electric-chorus-2]  [Save as…]

Save writes config/rigs/electric-chorus-2.yaml in place, keeping its
comments. It is not committed: that is yours to do.
```

**It counts net difference, not operations.** By this point the session had
sent two amp model changes and three Output Lvl changes, all of which had
returned to their starting values. The panel says *1* unsaved change, and
names it.

### Save

```
Saved electric-chorus-2
Wrote config/rigs/electric-chorus-2.yaml in place, keeping its comments.
The amp is saved as the agent last sent it (Neve 2): Carla cannot be read back.
Not committed: changed on top of commit ce78e31. sushi-rig does not commit:
that is yours to do.
```

It names the commit it diverged from, and repeats the Carla caveat at the
point where it matters most — the moment a value it cannot verify is written
to disk.

### Comments: kept

The file's two real comments are untouched:

```
# Converted from config/electric-chorus-2.json by `sushi-rig convert`, and checked to compile
# back to every plugin and setting it held. Edit this file, not that one.
```

(Note for anyone repeating this: `grep -c '#'` on these files counts five
plugin URIs containing `#_compressor`, `#ts9sim` and so on. The real comment
count is 2, not 7. My first measurement made that mistake.)

### Finding 5: a one-knob save rewrites sixteen lines

```
$ git diff --stat config/rigs/electric-chorus-2.yaml
 config/rigs/electric-chorus-2.yaml | 32 ++++++++++++++++----------------
 1 file changed, 16 insertions(+), 16 deletions(-)
```

Fifteen of those sixteen are not mine:

```
-      BYPASS: 1.0          +      BYPASS: 1          (x5)
-      OCTAVE: 0.0          +      OCTAVE: 0          (x2)
-      LATENCY: 0.0         +      LATENCY: 0         (x2)
-      COMPENSATE: 0.0      +      COMPENSATE: 0      (x2)
-      Drive: 0.0           +      Drive: 0
-      Dry/Wet level: 0.0   +      Dry/Wet level: 0
-      Enable: 1.0          +      Enable: 1
-      spread: 0.0          +      spread: 0
-      Level: 0.91          +      Level: 0.85        <- the only one I asked for
```

Every float whose value is a whole number is rewritten without its `.0`.

**It is a type change, not just a cosmetic one.** In YAML `1.0` is a float
and `1` is an int:

```
$ python3 -c "import yaml; print(type(yaml.safe_load('x: 1.0')['x']).__name__, type(yaml.safe_load('x: 1')['x']).__name__)"
float int
```

### How bad is it: measured, not guessed

Both versions parsed and every leaf compared:

```
leaf values: 112 -> 112

REAL value changes: 1
  channels[0].chain[5].parameters.Level: 0.91 -> 0.85

type-only changes (same number, float -> int): 15
  channels[0].chain[0].parameters.BYPASS: 1.0 (float) -> 1 (int)
  ... 14 more
```

So the save is **semantically correct**: nothing was added, nothing lost,
and exactly the one value I changed is different. Any consumer that coerces
to float sees an identical rig.

And it is a **one-time normalisation per file**. Saving a second time with
nothing changed produced a byte-identical file:

```
before second save: bf5c2a78dfbb1e26ff22136fe63fd501
after second save:  bf5c2a78dfbb1e26ff22136fe63fd501
```

So the cost is paid once per rig file, and every save after that gives a
clean one-line diff. That matters for how urgent this is — but on its first
save, a player who nudges one knob and runs `git diff` is shown thirty-two
changed lines and has to find their knob among them. For a project whose
whole argument is that the file is the thing you read, that is worth fixing.

### Save as…

The form validates live, with an `aria-live="polite"` check line and a
disabled submit:

| typed | check line | submit |
|---|---|---|
| `ui test save` | "A rig's name is lowercase letters, digits, '-' and '_', starting with a letter or digit, at most 64 characters: it becomes config/rigs/<name>.yaml." | disabled |
| `electric-chorus-2` | "electric-chorus-2 is the running rig: Save writes into it." | disabled |
| `ui-test-save` | "Writes config/rigs/ui-test-save.yaml, which becomes the running rig." | **enabled** |

It refuses to let Save-as shadow the running rig, and it states the
consequence — *which becomes the running rig* — before you commit to it.

```
Saved as ui-test-save
Wrote config/rigs/ui-test-save.yaml, a new rig, which is now the running rig.
It starts without the comments of config/rigs/electric-chorus-2.yaml, which
keeps them. The amp is saved as the agent last sent it (Neve 2): Carla cannot
be read back.
Not committed: a new file git does not track yet, on commit ce78e31. Other
tracked files have uncommitted changes too. sushi-rig does not commit: that
is yours to do.
```

Every claim in that message checks out:

```
$ ls -l config/rigs/ui-test-save.yaml
-rw-rw-r-- 1 sonny-dewfall sonny-dewfall 2815 Nov  2 22:20 config/rigs/ui-test-save.yaml

$ curl -s http://localhost:8029/health | grep -o -i '[^,]*ui-test-save[^,]*'
 "reason": "ui-test-save has no MIDI bindings for the agent"
 "supervisor": {"holds": {"config_name": "ui-test-save"
 "config_name": "ui-test-save"
 "config": "/run/user/1000/sushi-rig/rigs/ui-test-save.json"
 "rig": "config/rigs/ui-test-save.yaml"
 "message": "ui-test-save (headless mode)
```

It is the running rig, the title bar reads `ui-test-save · sushi-rig`, and
the picker offers `Stop ui-test-save`. The new file has **0** comments
against the original's **2** — exactly as warned, and the original keeps
them.

### Cleanup

```
$ git checkout -- config/rigs/electric-chorus-2.yaml
$ rm config/rigs/ui-test-save.yaml
$ git status -s
?? tool/spikes/live_rig/RESULTS-ui-2026-11-02.md
```

Only this results file remains. Switching back to electric-chorus-2 from the
picker took **439 ms**, the chorus Level knob reads `0.91` again, and
`ui-test-save` has dropped out of the rig list now that its file is gone.

---

## Step 9 — RECORD (#118) — FAILED. Recording does not work on this machine.

The stage screen was locked (it already was — the lock is per page, and only
the rack tab had been unlocked in step 6, which matches the page's own words:
"This is the page's lock: the agent does not enforce one yet").

Take name `ui-take-1`, Record pressed. The panel went `Starting…` and then,
461 ms later:

```
Not recording
record failed: cannot record ui-take-1: the capture engine did not start:
no port called 'Audient iD14:capture_AUX0' (for board-dry.wav) is in the graph;
output ports in the graph include: Audient iD14 Analog Surround 4.0:monitor_FL,
Audient iD14 Analog Surround 4.0:monitor_FR, Audient iD14 Analog Surround 4.0:monitor_RL,
Audient iD14 Analog Surround 4.0:monitor_RR, Audient iD14 Multichannel:capture_AUX0,
Audient iD14 Multichannel:capture_AUX1, Audient iD14 Multichannel:capture_AUX2,
Audient iD14 Multichannel:capture_AUX3 …
```

The same text is in `/health` under `agent.last_error`, with
`command: ["POST", "/record/start"]` and `kind: "live"`.

### Finding 6 (the important one): two port resolvers disagree

The machine file names the guitar input by **port alias**:

```yaml
guitar-in:
  ports: ["Audient iD14:capture_AUX0"]
  mono_to_stereo: true
```

That alias is real and present in the live graph:

```
$ pw-dump | (filter ports whose port.alias contains iD14 and capture_AUX0)
{'alias': 'Audient iD14:capture_AUX0', 'name': 'capture_AUX0', 'node_id': 96, 'dir': 'out'}
matches: 1
```

`patchbay.py` resolves machine-file ports **by alias**, and documents it —
using this very string as its worked example:

```python
def live_port_nodes(dump: str) -> dict[str, list[LivePort]]:
    """Port alias -> the ports that carry it, with their node, direction and
    `pw-link` name.

    A list because nothing makes an alias unique: `Audient iD14:capture_AUX0` is
    the same under every ALSA profile of the device ...
    The one reader of ports in the dump: everything
    that needs a port's node or its `pw-link` name derives from this.
    """
```

The recorder does not go through it. `capture.py:258`:

```python
present = set(available)
absent = [c for c in columns if c.source not in present]
```

a plain set-membership test against JACK display names
(`Audient iD14 Multichannel:capture_AUX0`). So the docstring's claim to be
"the one reader of ports in the dump" is no longer true — #118 added a
second reader that resolves the same names a different way.

**The machine file cannot satisfy both.** `capture.py`'s own comment above
that code says

> "the name PipeWire gives a device through JACK is not the one its own tools
> print, and the fix is to copy the right one"

— but copying the JACK name into `machines/*.yaml` would leave the patchbay
with no matching **alias** (`patchbay.py:728`: "no live port with alias …"),
breaking the wiring that currently works. One file, two mutually exclusive
requirements.

This is also why only `board-dry.wav` failed: the other two stems tap
`sushi:audio_output_2` and `sushi:audio_output_0`, Sushi's own ports, whose
names and aliases coincide. Only the **dry tap** needs a hardware input, and
only the dry tap is broken.

### What a failed take leaves behind

Not nothing, and not an empty directory:

```
$ ls -la ~/sushi-rig-takes/ui-take-1/
-rw-rw-r--  58    board-dry.wav
-rw-rw-r--  58    board.wav
-rw-rw-r--  5535  config.json
-rw-rw-r--  58    master.wav
-rw-rw-r--  3004  rig.yaml
-rw-rw-r--  1705  take.yaml
```

Three 58-byte stubs — bare WAV headers with no frames.

**`take check` catches it, loudly, and exits non-zero:**

```
$ sushi-rig take check ~/sushi-rig-takes/ui-take-1
the rig that made it: `sushi-rig verify /home/.../ui-take-1/config.json`
  the recorder stopped early: the capture engine did not start: no port called
  'Audient iD14:capture_AUX0' (for board-dry.wav) is in the graph; ... — the
  stems hold what was written before it
ui-take-1: The offset of board.wav and master.wav was not measured, so they are
  exactly as captured and may need lining up by ear.
ui-take-1: 1 problem(s)
(exit 1)
```

And `take.yaml` preserves the failure inside the artefact itself —
`frames: 0`, `lost_frames: 0`, and the whole error string. A take that went
wrong carries the reason with it rather than looking like a quiet short
recording. It also records `library: {commit: ce78e311..., dirty: false}`, so
the take knows which version of the tool made it.

### Finding 7: a failed take is marked `complete: true`

```yaml
name: ui-take-1
complete: true
recorded_at: '2026-11-02T22:22:22+00:00'
capture:
  frames: 0
  error: 'the capture engine did not start: ...'
```

`complete: true` beside `frames: 0` and a fatal `error`. Whatever `complete`
is meant to mean — that the file was finalised, probably, rather than that
the capture succeeded — anything filtering takes on it will treat this as a
good one. `take check` disagrees, and is right.

### Finding 8: doctor passes while recording is broken

With the rig up, doctor reports **11 passed, 1 unchecked** and says nothing
about the recorder:

```
       ok  machine-file: sonny-dewfall-XPS-9320.yaml is valid
       ok  clock-driver: the rig is clocked by Audient iD14 Multichannel, which the machine file names
       ok  one-clock: sushi, Carla, NAM are all in driver group 96
       ok  quantum: Sushi asked for 256 samples (5.33 ms a block) and locked it ...
       ok  rate: Sushi asked for 48000 Hz
unchecked  patchbay-unchecked: 1 node(s) rig.qpwgraph names are started by the rig itself ...
       ok  pw-jack / jackd / carla / fuse2 / disk / amp-models
11 check(s) passed
```

Note `clock-driver` **does** name the device correctly as "Audient iD14
Multichannel" — so doctor already knows the display name the recorder needs
and the alias the machine file uses, and still has no check that the
recorder's stem ports resolve. A player would run doctor, see 11 green, and
discover at the end of a take that nothing was captured.

### Finding 9: doctor blames the wrong thing for its one unchecked item

```
unchecked  patchbay-unchecked: 1 node(s) rig.qpwgraph names are started by the
           rig itself, so their absence says nothing while the rig is down
           -> run `sushi-rig up` and check again to cover those links
```

The rig **is** up. The absent node is `fmit`, which step 5 killed. The check
is right to stay unchecked, but its explanation and its suggested fix are
both wrong in this state — it tells you to start a rig that is already
running, rather than that a node it expected has crashed. It was "2 node(s)"
with the rig down in step 0 and is "1 node(s)" now, so it is counting
correctly; only the words are wrong.

### Not run

The rest of step 9 depends on a working recorder and was not attempted:

- the 20 s take and its elapsed-time display
- listening to each stem on its own (each channel's stem, a dry tap with no
  effects, and the master)
- `ui-take-2`, and stopping the **rig** mid-record to see whether the take is
  finished and listed before the rig goes down

No workaround was tried. Making this pass would mean editing
`machines/sonny-dewfall-XPS-9320.yaml`, which the brief forbids, and which
would break the patchbay as shown above.

The failed take was left in place at `~/sushi-rig-takes/ui-take-1` as a
reproduction.

---

## Step 10 — STOP AND CLEAN UP — PASSED

### Stop from the picker

The Stop button raises a confirm, as the amp model change does — and as the
live switch does **not**:

```
Stop electric-chorus-2? The sound stops until a rig is started again.
[Stop now (danger)]  [Cancel]
```

The rule across the whole UI is consistent: **confirm when it costs sound.**
Stop and amp-model change confirm; a live switch, which costs nothing, acts
on the first click.

Stopped in **531 ms**, back to the grey down state with LOAD and XRUNS
reading `—` / `rig down`.

```
$ sushi-rig status
sushi-rig: asking the agent at http://127.0.0.1:8029
No rig is running.
  CRASHED    fmit (pid 8302) killed by SIGTERM at 2026-11-02T21:08:22+00:00 — see /run/user/1000/sushi-rig/logs/fmit.log
```

### Refinement to Finding 1

With the rig down, the rack shows:

```
Last error (POST /record/start)
record failed: cannot record ui-take-1: the capture engine did not start: ...
```

So the UI **does** have a prominent error surface, and the record failure is
in it. The gap in Finding 1 is narrower than it first looked: the page
renders `agent.last_error` but not `agent.supervisor.crashed`. A failed
*command* is shown; a *child process dying* is not. That is the one to fix —
the crash is the more serious of the two and the only one with no path to
the screen.

Also worth a note: the `CRASHED fmit` line now sits under "No rig is
running." The rig that fmit belonged to is gone, and the record outlives it.
Keeping it is defensible, but "No rig is running" immediately above a crash
report from a rig that no longer exists reads oddly.

### Stopping the agent

Ctrl-C in T1:

```
^Csushi-rig agent stopping (no lifecycle command running)
```

**This is new and good.** On 1 Nov the agent died on SIGTERM with no line at
all, which was flagged then. It now says it is stopping *and* whether it was
in the middle of a lifecycle command — so a Ctrl-C during an `up` would be
distinguishable from a Ctrl-C at idle.

### Final checks — all clean

```
$ ps -p 8047 -o pid,stat,cmd --no-headers
gone: agent pid 8047 no such process

$ ss -ltnp | grep 8029
nothing listening on 8029

$ ps aux | grep -E "sushi|carla|fmit|qpwgraph|recorder" | grep -v grep
(no output — nothing left running)
```

Nothing of the rig is left. Every process started tonight was stopped by the
pid it was started under; `pkill -f` was never used.

---

# Summary

| step | result |
|---|---|
| 0 SETUP | **passed** |
| 1 DOWN STATE (#115) | **passed** |
| 2 START FROM THE UI (#113, #115) | **passed** |
| 3 FILE HANDLES (#114) | **passed** — flat at 18-19, not the "13-ish" expected |
| 4 LOAD AND XRUNS (#115) | **passed** — and settles the follower-row question |
| 5 CRASH SHOWS, NOT HEALED (#113) | **passed** |
| 6 AMP (#116) | **passed** |
| 7 LIVE SWITCH (#112, #115) | **passed** |
| 8 SAVE (#117) | **partly failed** — comments kept, but 15 unrelated lines rewritten |
| 9 RECORD (#118) | **failed** — cannot record at all on this machine |
| 10 STOP AND CLEAN UP | **passed** |

### Timings asked for

| what | time |
|---|---|
| Start pressed -> busy shown | 178 ms |
| Start pressed -> rack with chains (step 2) | **5.32 s** |
| amp model Neve 2 -> Neve 1 (step 6) | **1.26 s** |
| amp model Neve 1 -> Neve 2 | **1.18 s** |
| Sonny's estimate of the silence | *"Like 1-2 secs"* |
| switch to electric-delay (step 7) | under ~1.2 s |
| switch back to electric-chorus-2 | **683 ms** |
| switch back after the save test (step 8) | 439 ms |
| Stop from the picker (step 10) | 531 ms |

### Sonny's words, in order

| step | what was asked | what he said |
|---|---|---|
| 2 | does it sound like last night's electric-chorus-2? | "Yeah sounds great" |
| 4 | 30 s of playing, anything notable? | "I'm done" |
| 5 | does the guitar still sound with the tuner dead? | "Yes" |
| 6a | audible? does it follow the fader? | "It got quieter and then louder as expected - no crackles or artefactikng" |
| 6b | does the new model sound right? | "Sounds great" |
| 6b | still quieter? | "Still quieter as expected, model is correct." |
| 6b | how long was the silence? | "Like 1-2 secs of silence" |
| 7 | pops or clicks during a live switch? | "No crackles or artefacting, very clean." |
| 9 | — | *never asked: recording failed before any take existed* |

### The findings, worst first

1. **Recording is impossible on this machine (#118).** `capture.py` resolves
   stem ports by JACK display name; `patchbay.py` resolves the same
   machine-file entries by **port alias** and documents itself as "the one
   reader of ports in the dump". The machine file cannot satisfy both, so
   either recording works or the wiring does. Only the dry tap is affected.
2. **Doctor passes while recording is broken.** 11 green, nothing about the
   recorder, though `clock-driver` already knows both names involved.
3. **A failed take is marked `complete: true`** beside `frames: 0` and a
   fatal error. `take check` correctly exits 1; the flag disagrees with it.
4. **A one-knob save rewrites 16 lines**, 15 of them `X.0` -> `X`, a YAML
   float-to-int change. Semantically identical (112 values in, 112 out, one
   real change) and a one-time cost per file, but the first save of any rig
   buries the player's actual edit.
5. **A child crash never reaches the screen.** `agent.last_error` is rendered
   prominently; `agent.supervisor.crashed` is not rendered at all.
6. **New log spam replaces the old.** `warning: 'master_return' returned no
   parameters` about once a second from `live.py:187`, for the normal state
   of a master return with no plugins. It buried the fmit crash notice, and
   it buried the record failure.
7. **Doctor's unchecked item blames the wrong cause** — "while the rig is
   down", suggesting `sushi-rig up`, when the rig is up and a node has
   crashed.
8. **The two screens name the same amp model differently** — `Neve 2` on
   stage (filename stem), `Rupert Tape 1 Silk RED Tape rupert Tape 1` on the
   rack (the NAM file's internal name).
9. **`sushi-rig verify <rig>.yaml` throws a raw JSONDecodeError traceback.**
   It wants a compiled `config.json`; given a rig file it fails with a Python
   stack trace rather than saying so.
10. **A live switch acts on the first click, with no confirmation.** Recorded
    as deliberate (confirm costs sound, a live switch does not), but it means
    there is no moment in which to start playing before it happens.

### What went well, and is worth not losing

- The agent's crash line names what died, how, where its log is, that nothing
  will be done, why not, and what to do instead — in one sentence.
- `/amp`'s `known_note`: "what this agent last sent Carla, not what Carla
  holds: Carla cannot be asked".
- `POST /amp/parameter` returning `previous`, `asked` and `clamped`.
- The live switch reporting `how: "live"` with the **plan** that proves it,
  checkable against unchanged pids.
- The unsaved-changes panel counting *net* difference, not operations.
- Save-as validation refusing the running rig's name and stating the
  consequence before you act.
- Save messages naming the commit saved on top of, and that sushi-rig does
  not commit.
- `take.yaml` preserving the failure inside the artefact, with the library
  commit that made it.
- The `/live` 503 flood and the `ELKPY:` spam from 1 Nov are both gone.
- The agent now prints a shutdown line on Ctrl-C.

### Left behind on purpose

`~/sushi-rig-takes/ui-take-1` — the failed take, kept as a one-command
reproduction:

```
$ sushi-rig take check ~/sushi-rig-takes/ui-take-1
ui-take-1: 1 problem(s)      (exit 1)
```
