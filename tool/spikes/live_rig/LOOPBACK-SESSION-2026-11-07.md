# Loopback session: where the stereo goes wrong (7 Nov 2026)

> **Run on 12 Nov 2026.** The results are in RESULTS-loopback-2026-11-12.md.
> This hand-over is kept as written. Its `idctl` steps refer to a tool that was
> removed afterwards: the README beside this file says why and where it is in
> history.

A hand-over for a local Claude session on the rig laptop
(`sonny-dewfall-XPS-9320`), with Sonny there, the Audient iD14 and a loopback
cable. Written by the cloud session working on SonnyDewfall/Sushi.

## Rules

- **Work on `claude/ui-tests`.** Never commit to `master`. Put the results in
  `tool/spikes/live_rig/RESULTS-loopback-<date>.md` and push them to this
  branch. Do not merge or open a pull request.
- **The rig code under test is master's.** This branch had master merged in at
  `bc70fbf`, so its `tool/src` is master's, which is #131 at `c494625`.
- **Report what happened, not what should have.** A failure, a step that could
  not run, or a number that looks wrong is a result.
- **Change one variable at a time.** Part A3 is a controlled test. Don't
  combine its runs.
- **Kill what you start, by pid.** Never `pkill -f` a pattern. Never leave the
  rig, a recorder or a tone running at the end of a step.
- **The routing values are deliberately not Audient's.** Source 0x03 is dead
  on this unit, so `idctl`'s DAW routing is `00 01 00 01`. Don't "correct" it.

## What this session answers

| # | Question | Where |
|---|---|---|
| Q1 | Is Sushi's own master output balanced, left and right? | A2, no cable |
| Q2 | Does the wiring between Sushi and the iD14 stay stereo across a restart, including the restart after adding a channel? | A3, no cable |
| Q3 | On the stereo delay, what does each side actually carry? | A2 |
| Q4 | Does a stereo signal leave both iD14 outputs and come back equal? | B2, cable |
| Q5 | Does the routing come back after an unplug, with and without `idctl apply daw`? | B3, cable |
| Q6 | What is the round-trip latency? | B4, cable |

### What Sonny has reported

1. After a restart to add a channel, the sound goes back to mono, meaning left
   only. Remapping in qpwgraph fixes it. Sometimes it happens with no restart
   he knows of.
2. With the wiring left that way, on a stereo effect (`electric-spacey-delay`),
   the right ear gets "a quieter, slightly delayed version, as if some effects
   are missing".
3. Recordings had the guitar panned hard left on the master. That was before
   the agent was restarted onto #119, which is when the guitar became centred.

### What the cloud session checked

**The code, without the laptop:**
- A restart, from the UI or `sushi-rig restart`, brings the rig back from its
  rig file with `--rig` and the same machine file. It does not fall back to a
  v1 config (`rig.restart_arguments`).
- The patchbay generated for a one-channel rig (`electric-clean`) and a
  two-channel rig (`guitar_vocal`) both wire the master the same way:
  - `sushi:audio_output_0` → `Audient iD14:playback_FL`
  - `sushi:audio_output_1` → `Audient iD14:playback_FR`
- Every mono plugin gets its copy to stereo, and a mono input feeds both halves
  of its channel.

**So the code is stereo, and something on the laptop is changing the links
when Sushi's ports come back.**

**The suspects, in order:**
1. **A second qpwgraph.** For example, one Sonny opened himself, with a saved
   patchbay that is *activated*, possibly in *exclusive* mode. It re-applies
   its own saved links whenever Sushi's ports reappear.
2. **WirePlumber or pipewire-jack auto-connecting Sushi's outputs in port
   order.** Outputs 2 and up are the channels' stems, not the master.
3. **A stale agent.** The agent compiles rigs with the code it started with, so
   it must be restarted after every `git pull`.

Report 2 fits suspect 1 or 2: the right side could be hearing a stem or a
partial path, not the master.

## Part A: no cable (plug the loopback cable out entirely)

Normal listening level on headphones is fine for this part.

### A1. What is running

```bash
cd ~/Sushi && git fetch origin && git checkout claude/ui-tests && git merge --ff-only origin/claude/ui-tests
git log --oneline -1                                   # expect bc70fbf or later
curl -s localhost:8029/health | python3 -m json.tool | grep -E '"config_name"|"rig"|"started_at"|"pid"'
ps -o pid,lstart,cmd -C python3 | grep -i "sushi-rig agent"   # when the agent started
git log -1 --format=%cd                                # compare: the agent must be newer than the pull
pgrep -a qpwgraph; pgrep -a sushi; pgrep -a carla
cat ~/.config/rncbc.org/qpwgraph.conf 2>/dev/null | grep -iE "patchbay|exclusive|activated|autopin|path"
```

- **If the agent started before the pull, restart it before anything else.**
  Record that you did, because it changes what the rest can mean.
- **Record every qpwgraph process with its command line.** The rig's own runs
  as `qpwgraph -a <generated file> -m`. Anything else is a suspect.
- **Record qpwgraph's own settings:** the last patchbay, whether it is
  activated, and whether it is exclusive. They can apply to the rig's qpwgraph
  too.

### A2. Sushi's master, recorded inside the computer (Q1, Q3)

A take records the master's own output (`master.wav`) before it reaches
PipeWire's links, so it measures Sushi alone.

1. Start `electric-clean` from the UI.
2. In qpwgraph, check that both master outputs reach FL and FR. If they don't,
   fix them by hand and note that you had to.
3. Press **Record**, play for about 15 seconds, then stop. The stop answer
   names the take's folder.
4. Do the same for `electric-spacey-delay`.

For each take, measure the left and right RMS and peak of `master.wav`, in
dBFS. The stems are float32 WAV, which Python's `wave` module can't read, so
use `soundfile` if it is installed, or read the data chunk with numpy. For the
delay take, also correlate left against right.

| Rig | Expected |
|---|---|
| `electric-clean` (mono chain) | L − R within ±0.5 dB, correlation ≈ 1 (the same signal on both sides) |
| `electric-spacey-delay` | both sides carry the dry guitar at about equal level, with the delay spreading across them, so correlation is below 1 but both sides are clearly loud |

**If `master.wav` is balanced but Sonny hears one side, the fault is after
Sushi.** That points to the links (A3) or the iD14 (B2).

### A3. The wiring across restarts: a controlled test (Q2)

Run these as four separate runs, one change each. Before each run and after
it, snapshot the links:

```bash
pw-link -l > /tmp/links-<run>-<before|after>.txt
diff /tmp/links-<run>-before.txt /tmp/links-<run>-after.txt
grep -A4 -E '^sushi:audio_output_[0-9]+' /tmp/links-<run>-after.txt
```

The four runs, each starting from a correctly wired rig:

1. **Restart only:** a one-channel rig, Restart in the UI, nothing edited.
2. **Add a channel, then restart:** add a vocal channel on `vocal-in` in the
   UI, then restart.
3. **Run 2 with no other qpwgraph open.** Close any qpwgraph Sonny opened
   himself, check that `pgrep -a qpwgraph` shows only the rig's, then repeat
   run 2.
4. **Restart from the command line:** `sushi-rig restart`, to see whether the
   UI's path differs.

For each run, record:
- whether the master's two outputs reach FL and FR afterwards;
- every other link from `sushi:audio_output_*`, and where it goes;
- whether Sonny hears both sides;
- any warning the restart printed about links it could not make;
- the generated patchbay the rig's qpwgraph loaded (the file after `-a` in its
  command line), and whether it lists both master links.

**The answer we want:** which run first loses the right side, and what made the
link that took its place, or what removed the link to FR.
- If only runs with a second qpwgraph fail, that is the cause. The fix is to
  close it, or deactivate its patchbay.
- If run 3 also fails, record what links `sushi:audio_output_*` to the iD14
  apart from the rig's qpwgraph. `pw-dump` with the link ids from
  `pw-link -lI` names the client that created each link.

## Part B: with the cable, measuring only

### B0. Safety, before the cable goes in

- **Monitors off.** Switch them off at the speakers, or unplug the speaker
  cables.
- **Headphones off your ears.**
- **Both iD14 input gains fully down.**
- **48V phantom OFF on both inputs.** Phantom power into a line output can
  damage it.
- **The iD14 on DAW routing** (`idctl apply daw`, or MixiD's DAW Mix for
  channels 1 and 2). Main Mix feeds the inputs back to the outputs inside the
  device, which makes a feedback loop.
- **The rig down** (`sushi-rig down`, or Stop in the UI) for all of Part B.
  The guitar input would feed the looped output back into itself. Sushi through
  the cable is deliberately not tested. A2 and A3 cover Sushi, and Part B
  covers the interface.
- **Tones at −40 dBFS first.** Go louder only in steps you record.

### B1. Which cable, which jacks

Record exactly what the cable is and where each end goes. It decides what can
be tested:

| Cable | What it can test |
|---|---|
| Two cables: Output 1 → Input 1, Output 2 → Input 2 | everything below |
| One insert/Y lead: headphone out (TRS) → tip to Input 1, ring to Input 2 | stereo through the headphone pair (outputs 3 and 4) |
| One cable only | one side at a time. Run B2 twice, with the output end moved from Output 1 to Output 2 and nothing else changed |
| A mono jack in the stereo headphone socket | left only. **Not a stereo test.** Four "still mono" readings on 3 Nov came from exactly this; see RESULTS-id14-mixer-2026-11-03.md |

### B2. The interface alone, Sushi stopped (Q4)

Take PipeWire off the device, so the test reaches the hardware directly:

```bash
cd ~/Sushi/tool/spikes/live_rig
aplay -l | grep -i id14                       # the card number, here assumed 1
pactl list short sinks | grep -i id14; pactl list short sources | grep -i id14
pactl suspend-sink <the iD14 sink> 1; pactl suspend-source <the iD14 source> 1
python3 quiet_sweep.py /tmp/sweep.wav -40      # out 1..4 at 220/330/440/550 Hz
arecord -D plughw:1,0 -c 10 -f S16_LE -r 48000 -d 9 /tmp/loop-iface.wav & rec=$!
sleep 1; aplay -D plughw:1,0 /tmp/sweep.wav; wait $rec
python3 bursts.py /tmp/loop-iface.wav 1; python3 bursts.py /tmp/loop-iface.wav 2
pactl suspend-sink <the iD14 sink> 0; pactl suspend-source <the iD14 source> 0
```

- **Expected with two cables:** input 1 hears `out 1` (220 Hz) and input 2
  hears `out 2` (330 Hz), each at about the same level within 0.5 dB, with no
  other outputs. A burst on the wrong input, or an extra one, is a result.
- **If `aplay` or `arecord` says the device is busy,** PipeWire still holds it.
  Record that, and run with the rig and any audio apps closed.
- **If either side is missing,** run `idctl apply daw` and repeat once. Record
  both readings.

### B3. Unplug and replug (Q5)

1. With B2 passing, unplug the iD14's USB cable, wait 5 seconds, and plug it
   back in.
2. If the auto-routing udev rule from ID14-AUTO-ROUTING-2026-11-04.md is
   **not** installed, run B2 again straight away. We expect left only or
   silence. Then run `idctl apply daw` and B2 again. We expect both sides.
3. If the rule **is** installed, run B2 straight after the replug. We expect
   both sides with nothing done by hand. Also record
   `journalctl -u 'id14-route@*' --since -2min`.
4. Repeat once with the iD14 on a direct USB port rather than through the dock.
   It's on 3-1.2.1 behind a hub; see the "saved for later" list in the work
   plan.

### B4. The round-trip latency (Q6)

Build and run `rtlat` as the results that used it describe (see
RESULTS-nanocore-2026-11-03.md and the header of `rtlat.c`). Use the iD14's
real channel counts: 4 out and 10 in.

```bash
gcc -O2 -o /tmp/rtlat rtlat.c -lasound -lm
/tmp/rtlat hw:1,0 128 2 -20 /tmp/rt 48000 4 10 S32_LE    # prints emit_at and the trigger skew
python3 -I rtlat_analyse.py /tmp/rt <emit_at> 48000 10 S32_LE [--skew-frames N]
```

- **Report:** the round trip in frames and ms, the peak-to-background ratio,
  and the trigger skew.
- **Repeat at period 256.**
- **A weak peak** (a ratio under about 10) means the figure shouldn't be
  trusted; say so rather than rerunning until it looks good.
- **Stop at −20 dBFS.** Don't go above it.

## When you are done

- **Put the cable away and turn the monitors back on.** If the rig's routing
  for normal playing needs it, put it back with `idctl apply daw`.
- **Write `RESULTS-loopback-<date>.md`** with an answer to each of Q1–Q6 at
  the top, then the evidence for each:
  - the commands;
  - the output, trimmed but not edited;
  - the link snapshots that differ.
- **Commit to `claude/ui-tests`** with these trailers:

  ```
  Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_019Pcg2R4ZAgHxiixbiur9hj
  ```

  Then push.
