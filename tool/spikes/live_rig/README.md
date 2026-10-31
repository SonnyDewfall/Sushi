# Laptop tests: what can be proven without the interface or a guitar

Handover for a local Claude session on a Linux laptop, written 2026-10-31 by
the cloud session working on SonnyDewfall/Sushi. Sonny has the laptop but not
the Audient iD14 or a guitar. None of this needs either: PipeWire plays the
test signals, and Sushi's own ports stand in for the interface.

Read the repository's `README.md` (its nine quality principles) before
starting. The rules that matter most here:

- **Work on the branch `claude/laptop-tests`**, never on `master`. Push your
  results there. Do not merge, and do not open a pull request.
- **Change nothing outside `tool/spikes/live_rig/`** except the results file.
  The two scripts here were written without a machine to run them on. Fixing
  them is expected; record every fix, and why, in the results.
- **Kill what you start, by pid.** Never `pkill -f` a pattern: it has matched
  the shell running it before now. The scripts stop their own Sushi; anything
  you start by hand, stop in the same step.
- **Report what happened, not what should have.** A failing check, a step that
  could not run, a number that looks wrong: each is a result. Do not tune a
  threshold or rerun until something passes without saying so.

## 0. What machine is this

Record, in the results file:

```bash
hostname; uname -a; lsb_release -a 2>/dev/null; nproc; grep -m1 "model name" /proc/cpuinfo
pipewire --version; pw-metadata -n settings 0 2>/dev/null
which pw-jack pw-link pw-top qpwgraph open-stage-control carla 2>&1
```

**Stop here if it is not Linux with PipeWire and `pw-jack`.** Report what it
is: nothing below runs without them. If the hostname is
`sonny-dewfall-XPS-9320`, it is the rig machine, and step 6 applies.

## 1. Set up

```bash
git clone https://github.com/SonnyDewfall/Sushi.git && cd Sushi   # or fetch, if cloned
git fetch origin claude/laptop-tests && git checkout claude/laptop-tests

sudo apt install python3-venv python3-pip python3-lilv lilv-utils liblilv-dev lv2-dev pipewire-jack
cd tool && python3 -m venv --system-site-packages .venv && source .venv/bin/activate
pip install -e ".[dev,live,audio]" JACK-Client
cd ..
```

Sushi 1.3.0, if `./sushi` is not already at the repository root:

```bash
# The release asset CI uses (.github/workflows/rig-container.yml)
curl -LO https://github.com/elk-audio/sushi/releases/download/1.3.0/sushi-linux-x86_64_1_3_0.zip
unzip sushi-linux-x86_64_1_3_0.zip -d sushi-dl && find sushi-dl -name '*.AppImage'
chmod +x <that AppImage> && ln -sf "$PWD/<that AppImage>" sushi
./sushi --version     # if this fails on FUSE: ./sushi --appimage-extract, and use squashfs-root/AppRun
```

## 2. The suites, and doctor

```bash
cd tool && PYTHONPATH=src python -m pytest -rs 2>&1 | tail -30   # everything this machine can run
cd .. && sushi-rig doctor                                        # or: PYTHONPATH=tool/src python -m sushi_rig.cli doctor
```

Record the pass/fail/skip counts, every skip's reason, and doctor's whole
output. Faults about the interface, the patchbay or amp models are expected
without the iD14; doctor saying so is itself a result.

## 3. The capture engine (the most important test)

The open question in Phase 2: can a Python callback feed the recorder at a 256
quantum without losing audio? Run from `tool/`, venv active:

```bash
cd tool/spikes/live_rig
export PYTHONPATH=../../src
PIPEWIRE_QUANTUM=256/48000 pw-jack python capture_engine.py --seconds 600 --out capture-256-idle.json
PIPEWIRE_QUANTUM=256/48000 pw-jack python capture_engine.py --seconds 300 --load "$(nproc)" --out capture-256-load.json
PIPEWIRE_QUANTUM=128/48000 pw-jack python capture_engine.py --seconds 300 --out capture-128-idle.json
```

While each runs, note what else is open (a browser, a video call), and if you
can, watch `pw-top` for the spike's client and write down its busy and
xrun figures.

**Pass**, for each run:

- `xruns` 0;
- `recorder.lost_frames` 0 and `recorder.error` empty;
- `continuity` reports 0 `jumps`, 0 `silent_samples` and 0 `repeated_samples`
  for every column of every stem (a run of exact zeros is silence delivered in
  place, which the counters cannot see and the audio can);
- `callback_us.p99.9` well under `budget_us`.

A failure is not a bug to fix: it is the answer. Record it exactly. Then, if
there is time, find where it breaks. Is it only under `--load`? Only at 128?
Do the jumps line up with the recorder's reported `dropouts`, or not? If they
do not, that is a recorder bug and the most valuable thing this session can
find.

Confirm `quantum` in each report is what was asked for. PipeWire may not grant
it, and a run at another quantum answers a different question.

## 4. A v2 rig in a live Sushi

The new monitor path (channels sending to a master track) was measured only in
Sushi's offline frontend. This runs it live:

```bash
SUSHI="$(git rev-parse --show-toplevel)/sushi" PIPEWIRE_QUANTUM=256/48000 \
  pw-jack python live_paths.py --out live-paths.json
```

It starts Sushi with a two-channel v2 rig with empty chains, so no plugins are
needed. It plays two noise sources into it, and reads every output back in the
same client. **Pass:** every value under `checks` is true.

| Check | What it proves |
|---|---|
| `send_costs_nothing` | The master is exactly as late as the stems live. Offline it was 0 samples; live it depends on Sushi running every track on one thread. |
| `port_to_port_samples` | About one quantum. Measured against this client's own loopback, a different reference path from October's 257 (pw-record), so a one-sample difference is the path, not Sushi. Record whatever it is. |
| `separate_channels` | Noise in the guitar's input is not in the vocal's stem. |
| `pipewire_sums_two_links` | Two links into one input are summed. `probes.py` assumes this. |
| `all_gains_as_expected` | Every path at the gain the docstring's table predicts. |

Run it three times and record all three. Read `sushi.log`, whose path is in
the report, and quote any line about threads, xruns or ports in the results;
`*.log` is gitignored, so do not commit the file itself.

## 5. The record bar (only if `open-stage-control` is installed)

```bash
pw-jack python live_paths.py --hold --out live-paths-hold.json   # prints "config: <path>" first
# in another terminal, with that path:
sushi-rig panel <path> --record-bar -o /tmp/record-bar-panel.json --sushi "$SUSHI"
open-stage-control --load /tmp/record-bar-panel.json     # note its pid; stop it by pid after
```

Screenshot the panel. The question is whether the RECORD/STOP bar stacks
under the SAVE bar, with the tabs below both, or lands somewhere else, as the
save bar once did. Then press Enter in the first terminal to stop Sushi.
If `sushi-rig panel` fails, record its error; that is a result too.

## 6. Only on the rig machine (`sonny-dewfall-XPS-9320`)

With the rig's own setup (plugins, amp models, Carla) already there:

```bash
sushi-rig up --headless --no-tuner      # the v1 rig, as it runs today
pw-link -o | grep -i nam                # the amp's real port names
grep -i exclusive ~/.config/rncbc.org/qpwgraph.conf
sushi-rig down
```

`probes.py` assumes the amp's ports are `NAM:Input` and `NAM:Output`, and that
qpwgraph leaves links that are not in its file alone, which exclusive mode
would not. Record both.

## 7. Report

Write `tool/spikes/live_rig/RESULTS-<hostname>-<date>.md` containing:

- step 0's facts;
- what each step did, including the commands as actually run;
- every check, and whether it passed;
- every number;
- every fix made to the scripts, with the reason;
- anything that could not run, and why.

Commit it, along with the JSON reports and any screenshot, to
`claude/laptop-tests`, and push. Then tell Sonny it is pushed.
The cloud session reads it from there.
