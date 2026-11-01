# Second rig session: a rig of channels end to end, and the pedals

Handover for a local Claude session on the rig machine
(`sonny-dewfall-XPS-9320`), with the Audient iD14 and a guitar plugged in, and
a microphone on the iD14's second analogue input if there is one. Written
2026-11-01 by the cloud session, after the first session's results
(`RESULTS-hear-v2-2026-11-01.md`), without a machine to run it on: expect to
fix small things, and record every fix.

Same rules as `HEAR-V2.md`, which read first for the reasons:

- **Work on the branch `claude/hear-v2-save`**, never on `master`. Push results
  there. Do not merge, and do not open a pull request.
- **Change nothing outside `tool/spikes/live_rig/`**, except where a step says
  SAVE writes a rig file, which is the point of the step; commit those changes
  on this branch too, so they can be read.
- **Kill what you start, by pid**, verified against `ps` or `ss`, never from
  `$!` in a compound command. Never `pkill -f`.
- **Report what happened, not what should have.**
- **Ears decide, so Sonny plays.** Write what Sonny heard in Sonny's words.

Results go in `tool/spikes/live_rig/RESULTS-hear-v2-save-<date>.md`, one
section per step, with the commands and their output.

## What this is for

Since the first session, four things merged that only a rig can finish
proving: `up --rig` (#96) compiles a rig of channels at start and generates
its patchbay from the rig and the machine file; after any `up` the supervisor
checks that every link in the loaded patchbay was made (#98); SAVE on a
`--rig` run writes the rig file itself, in place and keeping its comments
(#99); and the agent takes `"rig": true` (#97). None has run against a real
graph. `--rig` stays opt-in until step 2 below has been heard.

## 0. Setup

```bash
cd ~/Sushi
git fetch origin master claude/hear-v2-save && git checkout claude/hear-v2-save
git log --oneline -1 origin/master          # record it
cd tool && source .venv/bin/activate && pip install -e ".[dev,live,audio,agent]" && cd ..
sushi-rig status                            # No rig is running.
```

## 1. The link check, on the rig you know

```bash
sushi-rig up electric-chorus-2 --headless
```

Record every `warning:` line. Expected: none, since the committed patchbay
wires this rig and the first session saw every link made. Then make one link
impossible and watch the check say so:

```bash
sushi-rig down
# Unplug the iD14, or switch it off, then:
sushi-rig up electric-chorus-2 --headless
```

Expected: `up` still comes up (nothing here is fatal), and a warning names the
links that were not made, in the form `Audient iD14:capture_AUX0 ->
sushi:audio_input_0`, with advice ending "wire it in qpwgraph and save
.../rig-amp.qpwgraph". Record the exact text and how long `up` took: the check
waits up to 10 s for links that never come. Plug the interface back in,
`sushi-rig down`, and bring it up once more to see the warning gone.

## 2. A rig of channels, two channels, generated patchbay

This is the hearing `--rig` waits for. Needs the microphone; if there is none,
run it anyway for the patchbay and the link check, and say the vocal was not
heard.

```bash
sushi-rig up --rig guitar_vocal --headless
sushi-rig status                            # should name   rig   config/rigs/guitar_vocal.yaml
pw-link -l | awk '/^[^ ]/{node=$0} /\|[<-]/{print node" \t"$0}' | grep -i sushi
cat /run/user/1000/sushi-rig/rigs/guitar_vocal.qpwgraph    # the patchbay that was generated
```

Expected links: `capture_AUX0 -> sushi:audio_input_0` (guitar), `capture_AUX1
-> sushi:audio_input_1` (vocal), `sushi:audio_output_0/1 -> playback_FL/FR`
(the master), and nothing into inputs 2/3. If anything differs, record it and
stop this step: that is the result.

**Play, and sing.** Guitar centred, vocal centred, both in the monitors, and
the two staying apart: a loud guitar must not move the vocal's gate or
de-esser, and the vocal must not go through the guitar's delay. Sonny's words.

```bash
sushi-rig top --once
sushi-rig down
```

## 3. SAVE on a rig run, from the panel

```bash
sushi-rig up --rig electric-chorus-2        # with the panel this time
```

Record the `up` output: there must be no "SAVE is off" warning. Open the panel
(`http://127.0.0.1:8080`), move one knob on one plugin by an amount you would
notice (the chorus depth, say), note the plugin and value, type the name
`electric-chorus-2` in the save bar and press SAVE. Then:

```bash
git status --short                           # only config/rigs/electric-chorus-2.yaml changed
git diff config/rigs/electric-chorus-2.yaml  # record the whole diff
```

Expected: the one parameter changed, rounded to six decimals, nothing else
in the file touched, every comment where it was, and no new file under
`config/`. Then save under a new name:

```
SAVE as   electric-chorus-2-test
```

```bash
ls config/rigs/                              # electric-chorus-2-test.yaml, new
head -5 config/rigs/electric-chorus-2-test.yaml
git diff --stat                              # the original unchanged since the last save
```

Then prove the saved file is the sound: `sushi-rig down`, `sushi-rig up --rig
electric-chorus-2-test --headless`, **play**, and ask Sonny whether the knob
is where he left it.

Also try the refusal: with the rig up, SAVE as `electric-clean` (a name
another rig owns). The panel's status should read a refusal naming the clash,
and `config/rigs/electric-clean.yaml` must be unchanged.

Known limit, so it is not reported as a surprise: if a plugin is **reordered**
from the panel before SAVE, the chain's order is saved but a comment written
beside a plugin stays at its old line. A fix is on `claude/yaml-reorder`; if
that branch has merged by the time you run this, reorder one plugin before
one of the saves and record whether its comment moved with it.

Commit the saved rig files on this branch with the results, so the diff can be
read later; delete `electric-chorus-2-test.yaml` first if Sonny does not want
it kept.

## 4. The agent, on a rig of channels

```bash
sushi-rig agent &
ss -ltnp | grep 8029                         # take the pid from here, verify with ps
```

```bash
curl -s -X POST localhost:8029/rig/up -H 'Content-Type: application/json' \
     -d '{"config_name": "guitar_vocal", "rig": true, "headless": true}' | python3 -m json.tool
sushi-rig status
curl -s -X POST localhost:8029/rig/down | python3 -m json.tool
kill <agent pid>; ps -p <agent pid> || echo "agent gone"
```

The agent's log should end with "sushi-rig agent stopping (no lifecycle
command running)". Record it.

## 5. The pedals, if they are to hand

As `HEAR-V2.md` step 3b, with the real footswitch and expression pedal, not
the MPD218: `aseqdump -l`, then `aseqdump -p <port>` while pressing each switch
twice and sweeping the pedal end to end. The questions are which message each
control sends (note, CC or Program Change, with its number) and whether each
footswitch is momentary or latching. Start a new capture file per labelled
take; never truncate the one `aseqdump` is writing.

## 6. Clean up

```bash
sushi-rig status                             # nothing running
ps aux | grep -E "sushi|carla|fmit|open-stage|sushi_rig" | grep -v grep
ss -ltnp | grep 8029                          # free
git status --short                            # the results file and the saved rig files only
git add tool/spikes/live_rig/RESULTS-hear-v2-save-*.md config/rigs/ && git commit -m "Hear-v2-save results from $(hostname)" && git push origin claude/hear-v2-save
```
