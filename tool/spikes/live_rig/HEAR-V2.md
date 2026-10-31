# Hear the v2 monitor path, and drive the rig through the agent

Handover for a local Claude session on the rig machine
(`sonny-dewfall-XPS-9320`), with the Audient iD14 and a guitar plugged in.
Written 2026-10-31 by the cloud session working on SonnyDewfall/Sushi, without
a machine to run it on: expect to fix small things, and record every fix.

Read the repository's `README.md` (its nine quality principles) first. The
rules that matter most here:

- **Work on the branch `claude/hear-v2`**, never on `master`. Push results
  there. Do not merge, and do not open a pull request.
- **Change nothing outside `tool/spikes/live_rig/`** except the generated
  configs this asks for, which are not to be committed.
- **Kill what you start, by pid.** Never `pkill -f` a pattern. `sushi-rig down`
  stops the rig by its recorded process group; the agent you stop by the pid
  you recorded when you started it. Check with `ps` that it is gone.
- **Report what happened, not what should have.** A sound that is not the
  same, a number that looks wrong, a step that could not run: each is a
  result. Do not rerun until something passes without saying so.
- **Ears decide, so Sonny plays.** Where a step says "play", that is Sonny
  through the guitar, and the result is what Sonny heard, in Sonny's words.

Results go in `tool/spikes/live_rig/RESULTS-hear-v2-<date>.md`, one section
per step below, with the commands run and their output.

## What this is for

Phase 2 changed how a rig reaches the speakers. A v1 config is one track whose
output goes straight to outputs 0/1. A v2 rig (`config/rigs/*.yaml`) compiles
to one track per channel, each with its own output pair (the stems, for
recording), plus a master track fed by sends from every channel, on outputs
0/1. What you hear is the master.

Offline, in CI, the master is exactly the channel: no delay, bit-exact, both
sides. On the laptop (`live_paths.py`, 2026-10-31) the same held live under
PipeWire with empty chains. What has never happened is **a guitar through a
real chain, through the master, into speakers**. That is step 2. Step 3 is the
other thing merged today without a rig: the agent (#89) running a real `up`.

## 0. Machine and setup

```bash
hostname; uname -a; pipewire --version
cd ~/Sushi            # or wherever the checkout is
git fetch origin claude/hear-v2 master && git checkout claude/hear-v2
git log --oneline -1 origin/master     # record it
cd tool && source .venv/bin/activate && pip install -e ".[dev,live,audio,agent]" && cd ..
sushi-rig doctor      # record the whole output; a FAULT here is a result, not a blocker
```

`doctor` reports the quantum Sushi *asked for*, not the running one. Fine.

## 1. Baseline: today's rig, today's way

```bash
sushi-rig up electric_board --headless
sushi-rig status
sushi-rig top --once       # record: load per node, xruns
pw-link -l | grep -i sushi # record: which ports are linked to what
```

**Play.** A minute of the usual: clean chords, something with the delay and
the hall audible, a few single notes to feel the latency. Write down, in
Sonny's words, what it sounds and feels like. This is the reference for step 2,
so be specific: level, tone, the feel of the delay.

```bash
sushi-rig down
```

## 2. The same rig, compiled from its v2 file

The v2 file is `config/rigs/electric_board.yaml`, converted from the v1 config
and checked to compile back to every plugin and setting. Compile it to a config
`up` can run. The name matters: `up` runs `config/<name>.json`, and the
compiled config says in `_meta.source` which rig file it came from.

```bash
sushi-rig emit config/rigs/electric_board.yaml -o config/electric_board_v2.json
```

`emit` verifies the result loads in `./sushi` and names only parameters the
plugins have. If it refuses, record why and stop here: that is the result.

Look at what it made before running it:

```bash
python3 -c "
import json; c = json.load(open('config/electric_board_v2.json'))
for t in c['tracks']:
    print(t['name'], 'in', t.get('input_busses') or t.get('inputs'), 'out', t.get('output_busses') or t.get('outputs'), [p['name'] for p in t['plugins']])
"
```

Expected: `board` with input engine channel 0 to both track channels, output
engine bus 1 (outputs 2/3), nine plugins and then a `send`; `master` on
engine bus 0 (outputs 0/1) with a `master_return`. The committed patchbay
already links `sushi:audio_output_0/1` to the monitors and `audio_input_0` from
the guitar, which is exactly this layout, so nothing in the patchbay changes.

```bash
sushi-rig up electric_board_v2 --headless
sushi-rig status
sushi-rig top --once       # compare with step 1: one more track, so a little more load
pw-link -l | grep -i sushi # outputs 0/1 to the monitors; outputs 2/3 (the stem) linked to nothing
```

`--headless` is deliberate: the save listener does not yet know how to save a
v2 rig (that is #30's last slice), so it is left out rather than left to
guess.

**Play the same minute.** The question is one question: **is it the same?**
Level, tone, the delay's feel, the latency. The only thing between the chain
and the speakers that was not there in step 1 is Sushi's internal send and
return, which measured as zero delay offline and on the laptop. If anything is
different, say what, and say whether it is better, worse or just different.
If it is the same, say that plainly.

Then, with the rig still up, the one thing the laptop could not test:

```bash
sushi-rig top --once       # after a minute of playing: did xruns climb?
```

```bash
sushi-rig down
```

### 2b. A rig with the amp

`electric_board` has no amp. `electric-clean` does, and its v2 file carries it
(`amp:` in `config/rigs/electric-clean.yaml`). Same steps, both ways:

```bash
sushi-rig up electric-clean --headless        # play; write it down; down
sushi-rig emit config/rigs/electric-clean.yaml -o config/electric-clean_v2.json
sushi-rig up electric-clean_v2 --headless     # the amp should come up as before: check `status` lists it
pw-link -l | grep -iE "sushi|NAM|Carla"       # the amp's ports wired by the guitar role, as today
```

**Play. Same question.** Then `sushi-rig down`.

### 2c. Two channels, if there is a microphone

Optional, and only if a mic is plugged into the iD14's second input. The v2
`guitar_vocal` puts the vocal on Sushi input 1, where the v1 config had it on
inputs 2/3, so the committed patchbay does not fit it. Generate the one that
does and compare, but do not replace the committed file:

```bash
sushi-rig patchbay generate config/rigs/guitar_vocal.yaml -o /tmp/guitar_vocal_v2.qpwgraph
diff Patchbay/rig.qpwgraph /tmp/guitar_vocal_v2.qpwgraph    # record the diff
```

If you want to hear it, link the vocal input by hand after `up` with
`pw-link <the iD14's second capture port> sushi:audio_input_1`, and record
what you did. Guitar centred, vocal centred, both in the monitors, and the two
staying apart. Not required tonight.

## 3. The agent runs a real `up`

#89 merged `sushi-rig agent`, tested only in a container with no rig. It runs
`up`, `down` and `restart` as the CLI in a child process. What is untested is
the whole point: that a rig started through it stays up after the request
returns, and stops when asked.

In one terminal, and **record the pid**:

```bash
sushi-rig agent &
AGENT=$!
echo "agent pid $AGENT"
```

In another:

```bash
curl -s localhost:8029/health | python3 -m json.tool
curl -s -X POST localhost:8029/rig/up -H 'Content-Type: application/json' \
     -d '{"config_name": "electric_board_v2", "headless": true}' | python3 -m json.tool
sushi-rig status                       # the rig is up, from the CLI's point of view too
curl -s localhost:8029/rig | python3 -m json.tool
curl -s localhost:8029/doctor | python3 -c "import json,sys; d=json.load(sys.stdin); print(d['ok'], d['faults'], 'faults', d['unchecked'], 'unchecked')"
curl -s -X POST localhost:8029/rig/up -d '{}'     # expect 409: a rig is already running
```

**Play for a few seconds** through the rig the agent started, so "it is up"
means sound, not a state file. Then:

```bash
curl -s -X POST localhost:8029/rig/restart | python3 -m json.tool    # about five seconds
sushi-rig status
curl -s -X POST localhost:8029/rig/down | python3 -m json.tool
sushi-rig status                       # No rig is running.
kill $AGENT; sleep 1; ps -p $AGENT || echo "agent gone"
```

Record every response. A 504 from `up` means the agent stopped waiting after
120 s; if that happens, `sushi-rig status` says whether the rig came up anyway,
and that is the result.

## 4. Clean up

```bash
sushi-rig status           # nothing running
ps aux | grep -E "sushi|carla|fmit|open-stage|sushi_rig" | grep -v grep   # nothing of the rig's
rm config/electric_board_v2.json config/electric-clean_v2.json   # generated, not for committing
git status                 # only the results file
git add tool/spikes/live_rig/RESULTS-hear-v2-*.md && git commit -m "Hear-v2 results from $(hostname)" && git push origin claude/hear-v2
```

## 5. Separately: the profiling session

Not part of this handover's results file, and needs no interface. Thirteen of
the plugins today's configs use have no profile under `tool/examples/profiles/`
(only `gxts9.yaml` exists). The `plugin-profile` skill in this repository does
one at a time; it needs the plugins and `python3-lilv`, which this machine has.
Start with the ones every electric tone uses:

```
http://guitarix.sourceforge.net/plugins/gx_compressor#_compressor
http://guitarix.sourceforge.net/plugins/gx_chorus_stereo#_chorus_stereo
http://guitarix.sourceforge.net/plugins/gx_detune_#_detune_
http://guitarix.sourceforge.net/plugins/gx_oc_2_#_oc_2_
http://distrho.sf.net/plugins/3BandEQ
http://distrho.sf.net/plugins/MaGigaverb
http://distrho.sf.net/plugins/MVerb
http://plugin.org.uk/swh-plugins/lcrDelay
urn:ardour:a-reverb
http://drobilla.net/plugins/mda/Delay
http://drobilla.net/plugins/mda/Dynamics
http://drobilla.net/plugins/mda/DeEss
http://drobilla.net/plugins/mda/Limiter
```

Each profile is its own small PR from its own branch, which is how the skill
expects to work. That is the long pole for #32 and can run on a different
night from the listening above.
