# Commands

Everything `sushi-rig` does from a terminal. `sushi-rig <command> --help` has
each command's options. With the agent running, most of what a player needs is
on the web UI instead ([user guides](../guide/README.md)).

| Command | What it does | More |
|---|---|---|
| `agent` | serve the rig, and the web UI, over HTTP on 127.0.0.1:8029 | [The agent](agent.md) |
| `agent --phone` | the same, on the network, printing the address a phone opens | [The agent](agent.md#from-a-phone) |
| `agent install-service` | write the agent's systemd user unit and enable it (`--phone` for the network) | [The agent](agent.md#as-a-service) |
| `up`, `down`, `restart`, `status`, `top` | start, stop and watch the rig | below |
| `doctor` | whether this machine will run the rig, and what to do if not | [Environment](environment.md#preflight) |
| `plugins assemble`, `plugins add`, `plugins check` | build `plugins/` from installed packages, add to it what the manifest gained, and prove it is self-contained | [Plugins](plugins.md) |
| `probe` | what an LV2 plugin, or a `.nam` amp model, declares about itself | [Plugins](plugins.md#choosing-a-plugin) |
| `profile` | component profiles: how one plugin is presented, checked against `probe` | the `plugin-profile` skill under `.claude/skills/` |
| `verify` | check a config loads, names things that exist, and applies what it says | below |
| `nulltest` | render a config offline and check its sound against its reference | [Tests and CI](testing.md) |
| `emit`, `convert` | compile a rig file to a Sushi config; write a v1 config (a retired one, `config/retired/v1/`) as a rig of channels | [The rig file](rig-file.md) |
| `machine check`, `patchbay check`, `patchbay generate` | the machine file and the patchbay made from it | [The machine file](machine-file.md) |
| `take check`, `take repair` | check a take's folder, or mend one cut off by a crash | [Takes](takes.md) |
| `probes` | where selftest would inject and listen for a rig | [Environment](environment.md#selftest-and-its-probes) |
| `save` | capture the running Sushi into a rig file of channels, without the agent | [The v1 workflow](v1-workflow.md#saving) |
| `push`, `capture` | build a rig.yaml in a running Sushi; read its parameter state (`panel` and `listen`, the Open Stage Control panel's, are retired, and so are the v1 configs) | [The v1 workflow](v1-workflow.md) |
| `recorder` | the process a rig of channels starts so the web UI can record; not run by hand | [The agent](agent.md#recording-from-the-web-ui) |

## Running the rig

```bash
sushi-rig up electric-clean # a rig of channels, config/rigs/<name>.yaml: what the web UI starts
sushi-rig up                # the same, for config/rigs/electric_board.yaml
sushi-rig status            # what is running, and where its logs are
sushi-rig top               # live CPU load per component, and whether xruns are climbing
sushi-rig restart           # stop and bring the same rig straight back
sushi-rig down              # stop everything, and verify it stopped
```

`up` also takes `--no-amp`, `--no-tuner`, and `--amp-model "Some Model.nam"` to
audition a different amp capture without editing the config. It starts no
control panel: the web page, served by `sushi-rig agent`, is the rig's control
surface, and every run is what used to be called headless (`--headless` is
still taken, and changes nothing).

`up --no-visualiser-tap` means the rig's output is never offered to the web
page's visualiser (#195). `up` itself never starts the visualiser's tap: the
agent starts it on demand, a `pw-loopback` in the rig's group (the child
`vistap`) offering the rig's output to the browser as the input **Sushi rig
output**, when the page's visualiser is about to listen (`POST
/visualiser/tap`), holds it while the page renews its lease, and stops it by
its pid a few minutes after the last page let it go (at once on an xrun
trip), when the agent stops, or with the group on `down`. "The rig's output"
is Sushi's master: the master track's outputs, engine bus 0, after the master
chain, the master limiter (#137) and the master volume, which is what the
monitors are sent. Its two nodes are followers in Sushi's cycle while it runs,
and the cycle waits for them; even with a realtime thread it was "too slow",
a cycle not finished in time, from once in 20 s to five times in 15 s when
measured in a container, which is why it runs only while a visualiser
listens. One with no
realtime thread is refused. `restart` and a switch keep the choice, from the
state file's `visualiser_tap_requested`; through the agent it is
`"visualiser_tap": false` on `POST /rig/up`.

`up <name>` runs a rig of channels, `config/rigs/<name>.yaml` (#30): the
rig is compiled at start into the runtime directory, never into `config/`, and
its patchbay is generated from the rig and the machine file rather than loaded
from `Patchbay/rig.qpwgraph`. The committed patchbay describes the v1 layout,
and on `guitar_vocal` it would feed the guitar into input 1, the vocal channel
under v2, and wire the microphone to inputs the rig does not read, with the rig
looking healthy throughout; a v2 rig therefore always brings its own. It took
`--rig` while v1 configs, `config/<name>.json`, ran too; they are retired
(#218, `config/retired/v1/`), so a rig of channels is all `up` runs, and
`--rig` is still taken and changes nothing. The monitor path was heard to be the
same on 2026-11-01 (two rigs, amp included), and a two-channel rig
(`guitar_vocal`) was measured through a loopback on 2026-11-12, each input
reaching both sides equally
(`tool/spikes/live_rig/RESULTS-loopback-2026-11-12.md`). `restart` brings a v2
rig back as one, and `status` names the rig file. A state file an older `up`
wrote for a v1 config is brought back as the rig of channels of its name.
After any `up`, the supervisor checks that every link in the patchbay it loaded was actually made,
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
`--no-tuner` / `--no-visualiser-tap` choices, read from the state file rather than from you, since
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
out of a randomly-named AppImage mount), Carla and the recorder are all group
members, so none of them need a pattern to match
and none get left behind. It then checks, and tells you if anything survived.

Running `./sushi -c <config>` by hand, outside these commands, needs `LV2_PATH`
set: see [Plugins](plugins.md#lv2_path).

## Checking a config: `verify`

`verify` answers the question that has caught this project out twice: a config
can load cleanly, name only things that exist, and still not do what it says.
Both times the cause was one key in `initial_state` silently overriding another,
and both times it looked like a save bug rather than a load bug.

```bash
sushi-rig verify config/rigs/electric-chorus-2.yaml  # a rig file: compiled as `up` compiles it, then checked
sushi-rig verify config/retired/v1/electric-clean.json  # any Sushi config .json, a retired v1 one included
sushi-rig verify --all                        # every rig of config/rigs/, about six seconds each
sushi-rig verify --quick                      # skip the check that starts Sushi
```

Sushi loads a compiled config, never the YAML, so a rig file from `config/rigs/`
is compiled first, for this machine and the way `up <name>` compiles it,
and that is what is checked; the compiled file's path is printed. `--all`
compiles each rig as `nulltest` renders it, with no MIDI map (which needs
this machine's file), so it checks the same on any machine; until the v1
configs were retired (#218) it checked `config/*.json`. A YAML file
from anywhere else, or a file that is not JSON, is refused with a sentence
saying what to pass, not a traceback. A compiled rig is also held to ending
with the master's limiter at the compiler's settings (#137,
`verify.limiter_problems`), and the deep check then shows Sushi applied them.

The deep check loads the config into a throwaway Sushi — `--dummy`, so no audio
hardware, no JACK — and compares the running rig against the file, naming every
parameter, bypass and chain-order difference. It uses a free port and its own
process group, so it is safe to run while you are playing.

The two cheap checks run automatically after `emit`. The deep one stays
explicit: five seconds on every save would be felt while dialling in a tone.

## Checking a config loads

No guitar, no audio interface, no extra dependencies needed:

```bash
./sushi --dump-plugins -c config/electric_board.json
```

This starts Sushi with the dummy frontend, prints every hosted plugin's
parameters as JSON, and exits. It is the authoritative source for parameter
names, and the cheapest way to know a config is loadable.
