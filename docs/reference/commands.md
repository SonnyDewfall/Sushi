# Commands

Everything `sushi-rig` does from a terminal. `sushi-rig <command> --help` has
each command's options. With the agent running, most of what a player needs is
on the web UI instead ([user guides](../guide/README.md)).

| Command | What it does | More |
|---|---|---|
| `agent` | serve the rig, and the web UI, over HTTP on 127.0.0.1:8029 | [The agent](agent.md) |
| `agent install-service` | write the agent's systemd user unit and enable it | [The agent](agent.md#as-a-service) |
| `up`, `down`, `restart`, `status`, `top` | start, stop and watch the rig | below |
| `doctor` | whether this machine will run the rig, and what to do if not | [Environment](environment.md#preflight) |
| `plugins assemble`, `plugins check` | build `plugins/` from installed packages, and prove it is self-contained | [Plugins](plugins.md) |
| `probe` | what an LV2 plugin, or a `.nam` amp model, declares about itself | [Plugins](plugins.md#choosing-a-plugin) |
| `profile` | component profiles: how one plugin is presented, checked against `probe` | the `plugin-profile` skill under `.claude/skills/` |
| `verify` | check a config loads, names things that exist, and applies what it says | below |
| `nulltest` | render a config offline and check its sound against its reference | [Tests and CI](testing.md) |
| `emit`, `convert` | compile a rig file to a Sushi config; write a v1 config as a rig of channels | [The rig file](rig-file.md) |
| `machine check`, `patchbay check`, `patchbay generate` | the machine file and the patchbay made from it | [The machine file](machine-file.md) |
| `take check`, `take repair` | check a take's folder, or mend one cut off by a crash | [Takes](takes.md) |
| `probes` | where selftest would inject and listen for a rig | [Environment](environment.md#selftest-and-its-probes) |
| `save`, `panel`, `listen`, `push`, `capture` | the v1 loop: capture a running Sushi and write a config, with or without the OSC panel | [The v1 workflow](v1-workflow.md) |
| `recorder` | the process a headless rig starts so the web UI can record; not run by hand | [The agent](agent.md#recording-from-the-web-ui) |

## Running the rig

```bash
sushi-rig up --rig electric-clean --headless   # a rig of channels, config/rigs/<name>.yaml: what the web UI starts
sushi-rig up                # v1: tuner, patchbay, Sushi, the amp, and a generated panel
sushi-rig up --headless     # the same rig with no panel — for playing, not tweaking
sushi-rig up electric-clean # any config/<name>.json; defaults to electric_board
sushi-rig status            # what is running, and where its logs are
sushi-rig top               # live CPU load per component, and whether xruns are climbing
sushi-rig restart           # stop and bring the same rig straight back
sushi-rig down              # stop everything, and verify it stopped
```

`up` also takes `--no-amp`, `--no-tuner`, and `--amp-model "Some Model.nam"` to
audition a different amp capture without editing the config.

`up --rig <name>` runs a rig of channels, `config/rigs/<name>.yaml` (#30): the
rig is compiled at start into the runtime directory, never into `config/`, and
its patchbay is generated from the rig and the machine file rather than loaded
from `Patchbay/rig.qpwgraph`. The committed patchbay describes the v1 layout,
and on `guitar_vocal` it would feed the guitar into input 1, the vocal channel
under v2, and wire the microphone to inputs the rig does not read, with the rig
looking healthy throughout; a v2 rig therefore always brings its own. On the
command line `--rig` is opt-in while a v1 config of the same name exists; the
web UI always starts rigs of channels. The monitor path was heard to be the
same on 2026-11-01 (two rigs, amp included), and a two-channel rig
(`guitar_vocal`) was measured through a loopback on 2026-11-12, each input
reaching both sides equally
(`tool/spikes/live_rig/RESULTS-loopback-2026-11-12.md`). `restart` brings a v2
rig back as one, and `status` names the rig file. After any `up`, v1 or v2, the
supervisor checks that every link in the patchbay it loaded was actually made,
and warns naming the pairs that were not: a channel whose source never reached
it is silent while everything looks healthy, and that is said rather than
discovered.

`up` starts everything and hands the prompt back — the rig is detached, so it
survives closing the terminal. Child output goes to log files rather than your
terminal; `status` prints the paths. It refuses to start a second rig on top of
a running one. With the agent running, `up`, `down`, `restart` and `status` go
through it, and say so in one line; see
[the agent](agent.md#the-cli-asks-the-agent-when-it-is-running).

**Recovery is the answer to failure, not self-healing**
([principle 9](../design/principles.md)). `restart` stops the rig and brings
back the same one — same config, same mode, and the same `--no-amp` /
`--no-tuner` choices, read from the state file rather than from you, since
after a crash you may not remember them, and for a rig of channels the machine
file it was started against. Measured end to end at around five seconds.
Through the agent, a restart that would drop changes it saw and no save has
written is refused; `restart --discard` says to drop them. With Sushi gone it is
never refused: there is nothing left to keep.

`top` answers the question that predicts trouble: how close to the block deadline
each component is running. It is not total CPU — 100% means a node used its whole
time budget for one block and the rig will drop audio. Xruns are reported as a
change rather than a total, since the cumulative count never resets.

`down` signals the rig's **process group**: `sushi.bin` (Sushi re-execs itself
out of a randomly-named AppImage mount), open-stage-control's Electron helpers
and the listener are all group members, so none of them need a pattern to match
and none get left behind. It then checks, and tells you if anything survived.

Running `./sushi -c <config>` by hand, outside these commands, needs `LV2_PATH`
set: see [Plugins](plugins.md#lv2_path).

## Checking a config: `verify`

`verify` answers the question that has caught this project out twice: a config
can load cleanly, name only things that exist, and still not do what it says.
Both times the cause was one key in `initial_state` silently overriding another,
and both times it looked like a save bug rather than a load bug.

```bash
sushi-rig verify config/electric-clean.json   # loads, names exist, and it applies
sushi-rig verify config/rigs/electric-chorus-2.yaml  # a rig file: compiled as `up --rig` compiles it, then checked
sushi-rig verify --all                        # every config, about six seconds each
sushi-rig verify --quick                      # skip the check that starts Sushi
```

Sushi loads a compiled config, never the YAML, so a rig file from `config/rigs/`
is compiled first, for this machine and the way `up --rig <name>` compiles it,
and that is what is checked; the compiled file's path is printed. A YAML file
from anywhere else, or a file that is not JSON, is refused with a sentence
saying what to pass, not a traceback.

The deep check loads the config into a throwaway Sushi — `--dummy`, so no audio
hardware, no JACK — and compares the running rig against the file, naming every
parameter, bypass and chain-order difference. It uses a free port and its own
process group, so it is safe to run while you are playing.

The two cheap checks run automatically after `emit` and after the panel's SAVE.
The deep one stays explicit: five seconds on every SAVE would be felt while
dialling in a tone.

## Checking a config loads

No guitar, no audio interface, no extra dependencies needed:

```bash
./sushi --dump-plugins -c config/electric_board.json
```

This starts Sushi with the dummy frontend, prints every hosted plugin's
parameters as JSON, and exits. It is the authoritative source for parameter
names, and the cheapest way to know a config is loadable.
