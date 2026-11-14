# Environment

What the rig machine needs, how to set it up, and the checks that say whether
it will run the rig.

Verified on the rig machine (`sonny-dewfall-XPS-9320`):

| | Status |
|---|---|
| Sushi 1.3.0 | ✅ built with `vst3, lv2, jack, rpc control, ableton link` |
| LV2 plugins discoverable | ✅ every URI in `plugin-manifest.txt` (55), from the bundles in `plugins/`; `sushi-rig plugins check` is the test |
| PipeWire / JACK, qpwgraph, fmit | ✅ |
| `lilv` Python bindings (system) | ✅ `python3-lilv` + `liblilv-dev` |
| `elkpy` (in `tool/.venv`) | ✅ imports cleanly on Python 3.14 |
| `python-osc` (in `tool/.venv`) | ✅ for `sushi-rig listen` (the panel's save button) and the agent's amp control; in the `live` and `agent` extras |

Sushi's LV2 support is **Linux-only** — it is excluded from the macOS and Windows
builds. Authoring has to happen here.

## Setting up from scratch

```bash
sudo apt install python3-pip python3-venv python3-lilv lv2-dev lilv-utils liblilv-dev
cd tool && python3 -m venv --system-site-packages .venv
source .venv/bin/activate
pip install -e ".[dev,live,agent,capture]"   # the tests, Sushi's gRPC, the agent and web UI, recording
pip install -e ".[audio]"   # optional: numpy, for the audio test signals and analysis (#40)

# put `sushi-rig` on PATH, so `up`/`down` work from any directory
mkdir -p ~/.local/bin
ln -sf "$PWD/.venv/bin/sushi-rig" ~/.local/bin/sushi-rig
```

That last step links only the `sushi-rig` entry point, not the venv's `python`
and `pip`. Debian and Ubuntu's stock `~/.profile` already adds `~/.local/bin` to
`PATH` when the directory exists — but it is read at **login**, so an existing
terminal will not see it until you log out and back in. For this shell only:

```bash
export PATH="$HOME/.local/bin:$PATH"
```

The command resolves the rig from its own install location, not your working
directory, so it does the same thing from anywhere.

The extras, from `tool/pyproject.toml` (the package needs Python 3.11 or later):

| Extra | Brings | For |
|---|---|---|
| `live` | elkpy, python-osc | talking to a running Sushi; the panel's save listener |
| `agent` | aiohttp, python-osc | `sushi-rig agent` and the web UI |
| `capture` | JACK-Client, numpy | recording takes; needs `libjack-jackd2-0` and `pw-jack` on the machine too |
| `midi` | alsa-midi | the agent's pedals; needs libasound (`libasound2t64`) |
| `audio` | numpy | offline test signals and analysis |
| `dev` | pytest | the tests |
| `ui-test` | playwright | the web UI's browser tests ([Tests and CI](testing.md)) |

After a pull that adds a dependency, run the `pip install -e` line again.

Untracked but required at run time: Sushi 1.3.0 itself, as `./sushi` at the
repository root (the AppImage, or a symlink to it), and `plugins/`
([Plugins and portability](plugins.md)). The amp needs Carla and the neural amp
modeler's LV2 plugin, the patchbay needs qpwgraph, and the tuner is fmit.
The Audient iD14 needs its volume fix once per machine:
[The audio interface](../guide/audio-interface.md).

Two snags this hit in practice. **`lv2-dev` alone is not enough** —
`python3-lilv`'s ctypes binding looks for the unversioned `liblilv-0.so`, which
is shipped by `liblilv-dev`, not `lv2-dev`; without it, `import lilv` fails with
`OSError: liblilv-0.so: cannot open shared object file`. And a venv will not see
the system `python3-lilv` at all unless created with `--system-site-packages`.

## Preflight

```bash
sushi-rig doctor          # the audio environment: will this machine run the rig
sushi-rig plugins check   # every plugin the rig needs resolves from plugins/ alone
```

`doctor` exits non-zero on any failure and **every failure says what to do**,
not just what is wrong. It replaces five commands a human had to run and then
interpret, and it covers what those did not: the audio environment, which is
where every fault in the 2026-10-26 new-machine session actually was — a dead
amp, a webcam clocking the graph at a 2048 quantum, a tuner dragging that webcam
into the rig's clock domain.

Three states, and the third is the point: a check that *could not run* is
reported as `unchecked`, never as a pass. Most of these questions need PipeWire
and a running rig, so on a bare machine `doctor` tells you what it could not
look at rather than going quiet.

With a rig of channels up, `record-ports` plans a take of it exactly as the
recorder does, from the files the state file names, lists JACK's ports in a
client under `pw-jack` (doctor itself is not under it) and matches them the
way the capture engine does, so doctor fails when RECORD would be refused at
that step and says which stem and port; the line names the JACK port each
machine-file port was found as. Where no JACK client can be opened it says
"needs JACK", unchecked, never green. A node of the rig's own that is absent with the rig
up is said as what it is: the agent's crash record when an agent is running
(the command asks it), else the state file's pid, else a child the rig was
started without; not "the rig is down".

It also checks there is room to record — at least an hour of a take of the
largest rig in `config/`, two stems per channel — and that every `.nam` model
the rig can play, in `amp/models` or wherever a config's `_amp` points, was
trained at the rig's 48 kHz, since a model at the wrong rate changes the tone
without saying so.

What it does not cover yet: whether Carla really starts a JACK client under
`pw-jack` (that needs launching it), `clock.allowed-rates` (that needs
`pw-metadata`), a loopback cable, and the iD14's volume control
([The audio interface](../guide/audio-interface.md)). The
quantum it reports is the one Sushi *asked the graph for*; the delay that
actually results is a measurement, and belongs with `selftest` (issue #49).

## Selftest and its probes

`selftest` itself is half built. What a capture means is decided and tested;
where to capture is too, and can be read before anything is linked:

```bash
sushi-rig probes config/src/guitar_vocal.yaml         # every port each probe links
sushi-rig probes config/src/guitar_vocal.yaml --amp   # as run with an amp model
```

Every half of every track's output is its own probe, so a chain that comes out
of one side only fails on the silent side; with `--amp`, the amp is probed on
its own and through Sushi, both required to add harmonics. What the plan cannot check it lists —
today that includes whether guitar and vocal leak into each other, since both
come out of bus 0. Linking the ports and capturing needs the rig machine.

**A probe never shares an input with the instrument** (#81). It is played
through `selftest_probe`, a copy of the chain under test on a Sushi input and
output the rig leaves free — for `config/rigs/guitar_vocal.yaml`, input 2 and
outputs 6/7; for `electric_board`, input 1 and outputs 4/5 — so the guitar
keeps playing through its own track, the player hears nothing, and the guitar's
noise is not in the measurement. Injecting beside the instrument, as the plan
used to, measured wrong on the rig: Sushi reads only the first 64 frames of
each quantum of an input with two links. The listing's `track` line says what
the copy carries. What the copy cannot see — a fault only the running instance
has, and the send and return into the master — is listed under NOT CHECKED. The
amp cannot be copied, so its probes still go into the amp the guitar plays
into, and say so (`beside guitar-in`): run those with the guitar quiet. A rig
of three channels fills Sushi's eight outputs and leaves no room for the copy;
its chains are listed as not probed rather than probed beside the instrument.
A probe track left behind by a selftest that was killed is inaudible; SAVE
leaves it out with a warning, and `sushi-rig restart` removes it.

## `plugins check`

**If a URI is absent from `lv2ls`, Sushi cannot load it** regardless of what is
on disk. That is the single most common failure in this project, and
`plugins check` is the version of it that fails loudly — it resolves every URI
the rig needs with `LV2_PATH` set to `plugins/` alone.
