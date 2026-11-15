# sushi-rig

A headless guitar rig built on [Sushi](https://github.com/elk-audio/sushi), Elk
Audio's plugin host. Guitar goes in through an audio interface, through a neural
amp and a chain of LV2 plugins, and back out, with no DAW in the signal path.
You play it, shape it, save it and record it from a web page on a laptop or a
phone.

![The rack: the master volume in the header, a strip for each channel on the left, the amp and each channel's plugins in the middle, and the rigs and Save on the right](docs/guide/img/rack.png)

## What it is

- **A rig** is one saved setup: its channels (a guitar, a microphone), the
  effects on each, and the amp. Each rig is one readable file in
  `config/rigs/`, which the rig itself writes when you save.
- **The agent** (`sushi-rig agent`) is the one program that owns the rig. It
  starts and stops it, changes it while it plays, saves it, records it, and
  serves the page.
- **The page** has two screens: the **rack**, laid out like a mixing desk, for
  shaping a sound; and the **stage**, for playing, which is what a phone-sized
  screen opens.
- **`sushi-rig`**, the Python tool under `tool/`, is everything else: the
  compiler that turns a rig file into what Sushi loads, the checks, the
  recorder, and the commands.

## What you need

- A Linux machine with PipeWire and its JACK layer (`pw-jack`). Sushi's LV2
  support is Linux-only.
- An audio interface, described in `machines/<hostname>.yaml`. The rig's own is
  an Audient iD14, which needs [a one-time volume fix](docs/guide/audio-interface.md).
- Sushi 1.3.0, as `./sushi` at the root of this checkout, and the plugins in
  `plugins/`. Neither is tracked here.
- For the amp, Carla and the neural amp modeler's LV2 plugin; qpwgraph for the
  connections; fmit for the tuner.
- Python 3.11 or later.

Setting all of that up from scratch is in
[Environment](docs/reference/environment.md), and
[Plugins and portability](docs/reference/plugins.md).

## Start it

```bash
sushi-rig doctor     # once, or when in doubt: is this machine fit to run the rig?
sushi-rig agent      # leave this running
```

Then open <http://127.0.0.1:8029/>, click a rig under **RIGS**, and click
**Start**. The first guide, [Getting started](docs/guide/getting-started.md),
walks through it, including how to reach it from a phone.

After a `git pull`, stop the agent and start it again: it keeps running the
code it was started with.

## User guides

For playing the rig. Plain steps, a few minutes each.

| Guide | What it covers |
|---|---|
| [Getting started](docs/guide/getting-started.md) | Start the agent, open the page, start a rig |
| [Playing live](docs/guide/playing-live.md) | The stage screen, switching rigs, the lock, pedals |
| [Shaping a sound](docs/guide/shaping-a-sound.md) | Plugins, knobs, bypass, the amp, undo |
| [Mixing](docs/guide/mixing.md) | Channel volumes and the master |
| [Saving and going back](docs/guide/saving.md) | Save, Save as, Revert |
| [Building a rig](docs/guide/building-a-rig.md) | A new rig, channels and inputs |
| [Recording](docs/guide/recording.md) | Takes, what is in a take's folder, names |
| [The audio interface](docs/guide/audio-interface.md) | The iD14's volume fix, and one side going quiet |
| [When something's wrong](docs/guide/troubleshooting.md) | No sound, restarting the agent after an update, `doctor` |

[The guides' index](docs/guide/README.md) has the few words they all use.

## Reference

For looking things up.

| Page | What is in it |
|---|---|
| [The rig file](docs/reference/rig-file.md) | `config/rigs/<name>.yaml`, and what the compiler makes of it |
| [The machine file](docs/reference/machine-file.md) | Which physical ports play which role |
| [Commands](docs/reference/commands.md) | Every `sushi-rig` command |
| [The agent](docs/reference/agent.md) | Its routes and rules |
| [The web UI](docs/reference/web-ui.md) | How the page behaves, control by control |
| [MIDI](docs/reference/midi.md) | Pedals and controllers |
| [Takes](docs/reference/takes.md) | What a recording is on disk |
| [Plugins and portability](docs/reference/plugins.md) | `plugins/`, the manifest, choosing a plugin |
| [Environment](docs/reference/environment.md) | Setting up a machine, and `doctor` |
| [Tests and CI](docs/reference/testing.md) | Running the suites; the rule about `config/rigs/` |
| [The v1 workflow](docs/reference/v1-workflow.md) | The older Open Stage Control panel and its loop |
| [Repository layout](docs/reference/repository-layout.md) | What each folder is for |

## Design

For why it is built this way: [the problem and the architectural
decision](docs/design/problem-and-decision.md), the
[quality principles](docs/design/principles.md),
[where v2 is going](docs/design/v2.md),
[what this project owns](docs/design/ownership.md), and the
[original brief](docs/design/IMPLEMENTATION_BRIEF.md), kept as history. They are
indexed in [docs/design](docs/design/README.md).

## What changed, and when

[MANIFEST.md](MANIFEST.md) is the dated record: what was done, what was found
on the rig, and what was decided. The backlog is in
[GitHub Issues](https://github.com/SonnyDewfall/Sushi/issues).
