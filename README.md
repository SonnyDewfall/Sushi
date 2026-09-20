# sushi-rig

A headless guitar rig built on [Sushi](https://github.com/elk-audio/sushi), Elk
Audio's plugin host. Guitar goes in through an audio interface, through a chain
of LV2 plugins, and back out — with no DAW in the signal path and no GUI at
runtime.

The rig is described by a JSON config that Sushi loads at startup. This
repository holds those configs, the patchbay routing that feeds them, and the
tooling that produces them.

---

## The problem this solves

Sushi's config format is a poor authoring surface. Plugin parameters are
addressed **by the name Sushi assigns them** — derived from the plugin's TTL
metadata, not necessarily the port symbol — and every value is **normalised to
0.0–1.0** regardless of the plugin's real units. A reverb decay of 2.4 seconds
appears in the file as `0.32`.

For one plugin that is merely awkward. For a 10–30 plugin chain it is
unworkable: the file cannot be read, reviewed or corrected by eye, and a wrong
value produces a config that loads cleanly and sounds wrong.

## The architectural decision

**Sushi is both the deployment target and the authoring host.**

The obvious alternative — dial the sound in using a desktop host like Carla or
Ardour, then convert the session file to Sushi JSON — was considered and
rejected. It fails three ways at once:

| | Desktop host | Sushi |
|---|---|---|
| Parameter addressing | port symbol (`gain`) | Sushi's assigned name (`Gain`) |
| Parameter values | real-world (`-12.0 dB`) | normalised (`0.35`) |
| Version drift | silent — renamed ports still "work" | caught by Sushi's own introspection |

Each gap needs a translation layer, and both layers break silently when a plugin
updates. Authoring inside Sushi removes them entirely: the values read back are
already normalised and already named the way Sushi names them, because the same
wrapper code produced both.

The cost: Sushi is headless, so plugins' own GUIs are unavailable. Parameters are
tweaked through generated [Open Stage Control](https://openstagecontrol.ammd.net/)
faders instead. That was an accepted trade-off when scoping the project.

## Quality principles

What "good" means here. These are meant to be **usable in a review** — a change
should be arguable against them, and an audit should be able to fail one.

**1. Isolate decisions from effects.** Unit-test every decision; verify effects
against the real thing.

Most of the value in this codebase is decisions — what to start, where a plugin
should move to, which OSC message means what. Those are kept pure and pinned by
tests. The effects around them, mostly glue to external processes, are verified
by running them.

This replaced "all code covered by unit tests", which was the wrong target here.
Large parts of this project supervise processes, spawn Carla and send OSC; a unit
test for those means mocking everything and proves only that the mocks were
called. Meanwhile the tests that have genuinely earned their place encode
*discovered facts* — that Sushi's `/bypass` accepts an int only, that a `program`
overrides `parameters`, that Carla numbers plugins from zero. None of them came
from chasing coverage.

**2. Every feature is covered by a functional test.** Verified by driving the
real thing, not by a human saying it worked.

**3. Architectural simplicity wherever possible.** Few moving parts, a shape you
can hold in your head. This is about architecture, not line count — see 5.

**4. Customise external components as little as possible.** Prefer configuration
and composition over modification, and record every workaround with what it works
around.

**5. Annotated for human and machine readers.** Comments say *why*, not what: the
constraint that forced the shape, the thing that was tried and failed, the
behaviour that is undiscoverable from the API. Much of this project's hardest-won
knowledge is unobvious and expensive to rediscover.

Some files here are a third comment, and that does not violate 3. Comments are
not moving parts.

**6. Modular and composable wherever possible.** One job per module, and
composition over coupling.

**7. An intuitive and simple user experience.** The rig is played by a musician,
not operated by an engineer. A failure should be understandable without reading
the source.

**8. Failures must be loud.** Prefer a loud failure to silent degradation.

Every serious bug this project has had was silent. qpwgraph exiting with no error
and no log. A track's bypass flag wiping every plugin's. A preset index quietly
overwriting the parameters saved beside it. An amp running with no model loaded,
processing silence while looking perfectly healthy. Not one announced itself, and
each cost hours.

**9. Fail loudly, recover instantly.** The rig may die — it must come straight
back with the state it had.

This is deliberately *recovery*, not self-healing. A rig that quietly compensates
is a rig running at half quality on stage with nobody aware, which puts it
straight at odds with 8. It works because state lives in config files and is not
changed at runtime during performance, only while building a configuration.

It also settles a design question: the supervisor deliberately does **not**
restart children. Under this principle that is right, and the effort belongs in
making restart fast and faithful instead.

### Where these disagree

They are not all compatible, and pretending otherwise would make them useless in
a review.

**3 against 4.** Running the neural amp in Carla, rather than adding `patch:`
support to Sushi's LV2 wrapper, follows 4 — but it buys a smaller *diff* at the
cost of a more complex *architecture*. Both are "simplicity" and they point
different ways. Say which won and why.

**8 against 9.** Resolved above, by making 9 about recovery. The shape to copy is
`start_qpwgraph` in `rig.py`: retry a few times, then report loudly and leave the
rig usable.

## What this project owns (and what it doesn't)

Several projects already drive Sushi — [`elk-audio/sushi-gui`](https://github.com/elk-audio/sushi-gui)
(official, Qt, live parameter and graph editing),
[`scraporchestra`](https://github.com/OlivierSch755/scraporchestra) (web app,
project manager for Elk Pi), and Sushi's own native session save/restore. They
were evaluated deliberately rather than ignored.

They all share one assumption: **live Sushi state is the source of truth.** You
drive a UI, state lives in the engine, and persistence dumps whatever is
currently loaded.

This project inverts that. **A hand-authored `rig.yaml` is the source of truth**,
and configs are versioned, readable, diffable artefacts. That's not a UI
preference — it's what makes a tone reviewable, revertable and reproducible.

The difference is not academic. Deriving structure from a live session is
*lossy*: `sushi-gui` reconstructs internal plugins as
`uid = "sushi.testing." + name`, so a plugin named `internal_reverb` comes back
as `sushi.testing.internal_reverb` rather than `sushi.testing.freeverb` — a
config that will not load. A live session records **observed state, not
intent**. It cannot know what you meant; a source file can.

So the split is:

| | Tool | Why |
|---|---|---|
| **Exploration** — trying plugins, building chains | `sushi-gui` and other live tools | Fast and live. Hand-editing YAML to audition a plugin is friction worth removing, and not friction worth *building* a solution for |
| **Persistence** — this tone is good, keep it | This project | Determinism and version control. Nothing surveyed produces readable, git-diffable, versioned configs |

The bridge between them is `capture` → versioned config. Everything here is
built to keep that bridge tool-agnostic, so adopting a better live editor later
costs nothing.

Where those projects do something well, this one borrows rather than reinvents —
Sushi's native `SaveSession`/`RestoreSession` for runtime switching, and
`sushi-gui`'s live-session-to-config derivation for the cases where no
`rig.yaml` exists yet. Full rationale in
[issue #12](https://github.com/SonnyDewfall/Sushi/issues/12).

## The workflow

```
rig.yaml ──emit──> rig.json ──verify──> names confirmed against Sushi
                      │
                      ├──panel──> panel.json ──> Open Stage Control faders
                      │
                   sushi -j -c rig.json
                      │
                   [tweak by ear]
                      │
                   capture ──> state.json
                      │
rig.yaml + state.json ──emit──> rig.json  (now carrying initial_state)
```

`rig.yaml` is the only hand-authored file; everything below it is generated.

`verify` answers the question that has caught this project out twice: a config
can load cleanly, name only things that exist, and still not do what it says.
Both times the cause was one key in `initial_state` silently overriding another,
and both times it looked like a save bug rather than a load bug.

```bash
sushi-rig verify config/electric-clean.json   # loads, names exist, and it applies
sushi-rig verify --all                        # every config, about six seconds each
sushi-rig verify --quick                      # skip the check that starts Sushi
```

The deep check loads the config into a throwaway Sushi — `--dummy`, so no audio
hardware, no JACK — and compares the running rig against the file, naming every
parameter, bypass and chain-order difference. It uses a free port and its own
process group, so it is safe to run while you are playing.

The two cheap checks run automatically after `emit` and after the panel's SAVE.
The deep one stays explicit: five seconds on every SAVE would be felt while
dialling in a tone.

A captured session overrides the yaml in two ways, not one: parameter **values**
(via `initial_state`) and plugin **order**. Pedal order is a tonal decision, so
the panel can reorder the chain live and a save keeps it. The consequence worth
knowing: the yaml's plugin order and a saved config's order can drift apart,
exactly as their values already do. The yaml declares where a rig starts; a
capture records where it got to.

The tweak → capture → emit loop at the bottom is where the time actually goes.
`sushi-rig up` runs an OSC listener (`sushi-rig listen`) alongside Sushi, and
the generated panel carries a name field and a save button that trigger that
loop directly — type a name, tweak, press save, and `config/<name>.json`
appears with the current sound baked in. `sushi-rig save <name>` on the CLI
does the same thing without the panel, since the save button is just one
caller of that same underlying capture-and-write step (see issue #10).

---

## Repository layout

This is a **monorepo with two zones**: the *rig* (personal, machine-specific —
the configs and scripts you run daily) at the root, and the *tool* (reusable
software that generates the configs) under `tool/`. They share one workflow, so
they share one repo; if the tool ever needs to stand alone, it can be split out
then.

```
.                               ── RIG (root): what you run daily
├── config/                     Sushi configs — the deployable artefacts
│   ├── electric_board.json       current rig (what `sushi-rig up` launches)
│   ├── empty.json                passthrough, for verifying the audio path
│   ├── src/electric_board.yaml   hand-authored source for electric_board.json
│   ├── archive/                  auto-written version snapshots,
│   │                             config/archive/<name>/vX.Y.json
│   └── retired/                  hand-retired configs, kept for recovery only
├── Patchbay/                   qpwgraph sessions
│   ├── rig.qpwgraph              routing loaded at startup
│   ├── Default.qpwgraph
│   └── Test_1.qpwgraph
├── plugin-manifest.txt         every LV2 URI this rig can load (see Plugins below)
│
├── tool/                       ── TOOL: generates the configs above
│   ├── pyproject.toml
│   ├── src/sushi_rig/            spec, emit, dump, probe, live, panel, listen,
│   │                             save, rig, amp, top, cli
│   ├── tests/
│   ├── examples/
│   │   ├── rig.example.yaml       annotated rig spec
│   │   └── dump.example.json      real --dump-plugins output, test fixture
│   └── prototype_reference.py    single-file prototype; superseded by src/, kept for reference
│
├── IMPLEMENTATION_BRIEF.md     authoritative spec — read before writing code
├── MANIFEST.md                 what is being worked on and what is next
└── README.md
```

Untracked but required at runtime: `Sushi-x86_64.AppImage` (symlinked as
`sushi`) and `plugins/`. See [Plugins and portability](#plugins-and-portability).

`tool/prototype_reference.py` (previously `sushi_rig.py`) is the original
single-file prototype, kept as reference for data shapes and emit logic while
`tool/src/sushi_rig/` is built out module by module per §3 of the implementation
brief — tracked as work item 2 in [MANIFEST.md](MANIFEST.md). It will be deleted
once the package supersedes it.

---

## Running the rig

```bash
sushi-rig up                # tuner, patchbay, Sushi, the amp, and a generated panel
sushi-rig up --headless     # the same rig with no panel — for playing, not tweaking
sushi-rig up electric-clean # any config/<name>.json; defaults to electric_board
sushi-rig status            # what is running, and where its logs are
sushi-rig top               # live CPU load per component, and whether xruns are climbing
sushi-rig restart           # stop and bring the same rig straight back
sushi-rig down              # stop everything, and verify it stopped
```

`up` also takes `--no-amp`, `--no-tuner`, and `--amp-model "Some Model.nam"` to
audition a different amp capture without editing the config.

`up` starts everything and hands the prompt back — the rig is detached, so it
survives closing the terminal. Child output goes to log files rather than your
terminal; `status` prints the paths. It refuses to start a second rig on top of
a running one.

**Two modes, for two ways of using the rig.** The default gives you the Open
Stage Control panel and the save listener behind it, which is what you want when
dialling in a tone: tweak, type a name, press save, and `config/<name>.json`
appears with the current sound baked in (issue #10). `--headless` skips both and
just runs the rig.

**Recovery is the answer to failure, not self-healing** (principle 9). `restart`
stops the rig and brings back the same one — same config, same mode, and the same
`--no-amp` / `--no-tuner` choices, read from the state file rather than from you,
since after a crash you may not remember them. Measured end to end at around five
seconds.

`top` answers the question that predicts trouble: how close to the block deadline
each component is running. It is not total CPU — 100% means a node used its whole
time budget for one block and the rig will drop audio. Xruns are reported as a
change rather than a total, since the cumulative count never resets.

`down` signals the rig's **process group**, which is why it can be trusted where
the old `stop-rig.sh` could not: `sushi.bin` (Sushi re-execs itself out of a
randomly-named AppImage mount), open-stage-control's Electron helpers and the
listener are all group members, so none of them need a pattern to match and none
get left behind. It then checks, and tells you if anything survived.

> Replaced `start-rig.sh`, `start-rig-and-panel.sh` and `stop-rig.sh`. Those
> used bash as a supervisor and identified processes by command-line pattern,
> which is how `killall sushi` came to miss Sushi entirely, how a shutdown could
> kill its own shell, and how open-stage-control was routinely left running.

> **`LV2_PATH` must be set, or Sushi loads no LV2 plugins at all.** It exits 4
> with `Failed to load tracks from the Json config file` — an error that never
> mentions `LV2_PATH`. `lv2ls` is not a proxy for this: it finds plugins using
> lilv's own defaults while Sushi finds none. Running `./sushi -c <config>` by
> hand, outside the scripts, hits this every time:
>
> ```bash
> export LV2_PATH="$HOME/Sushi/plugins"
> ```
>
> That single path is deliberate and complete — see
> [Plugins and portability](#plugins-and-portability). Do not add
> `/usr/lib/lv2`: it would let a config load against system plugins that
> `plugins/` doesn't carry, which is exactly the silent portability break the
> current layout exists to prevent.

### Choosing a plugin

Before a plugin goes into a rig, `probe` answers what you'd otherwise only
find out by trying it:

```bash
sushi-rig probe                 # everything on LV2_PATH, one line each
sushi-rig probe <uri>           # full detail for one plugin
sushi-rig probe <uri> --json    # same, machine-readable
sushi-rig probe amp/models/X.nam  # a NAM amp model: architecture, quality tiers,
                                  # and whether its sample rate matches the rig
```

It reads the plugin's own TTL through lilv, so every value is the plugin
author's declaration rather than our inference:

```
GxTubeScreamer  (Distortion Plugin)
  uri     http://guitarix.sourceforge.net/plugins/gxts9#ts9sim
  audio   1 in / 1 out
  config  drivable — all state lives in control ports
  4 parameter(s):
    Level                          [-20.0, 4.0]             default=-16.0
    Tone                           [100.0, 1000.0]          default=400.0
    Drive                          [0.0, 1.0]               default=0.5
    BYPASS                         [0.0, 1.0]               default=1.0  toggled
```

Two things there are invisible everywhere downstream. **Real-world ranges** —
Tone is 100–1000 Hz, which in a Sushi config appears only as a normalised
`0.0–1.0`. And **`config drivable`**, which is the question that actually
decides whether a plugin can be used at all.

A plugin is drivable only if all of its state lives in control ports. If it
keeps state elsewhere — a file, a model, an impulse response — it reaches it
through the LV2 `patch:`/`state:` extensions, which **Sushi does not support**.
Such a plugin loads without complaint and then silently ignores the part you
cared about. `probe` checks three signals for this (atom ports,
`patch:writable`, `state:interface`), because any one alone is insufficient:
`zeroconvolv` declares no atom ports and no `patch:writable` yet is entirely
driven by an IR file, and only `state:interface` reveals it.

Note that `probe` reads and reports — it doesn't write a catalogue or decide
which parameters are worth showing. Those are judgment calls, and keeping them
out of generated data is what makes the generated half safe to regenerate.

### Checking a config loads

No guitar, no audio interface, no extra dependencies needed:

```bash
./sushi --dump-plugins -c config/electric_board.json
```

This starts Sushi with the dummy frontend, prints every hosted plugin's
parameters as JSON, and exits. It is the authoritative source for parameter
names, and the cheapest way to know a config is loadable.

## Environment

Verified on this machine:

| | Status |
|---|---|
| Sushi 1.3.0 | ✅ built with `vst3, lv2, jack, rpc control, ableton link` |
| LV2 plugins discoverable | ✅ 41 via `lv2ls`, from the six bundles in `plugins/` |
| PipeWire / JACK, qpwgraph, fmit | ✅ |
| `lilv` Python bindings (system) | ✅ `python3-lilv` + `liblilv-dev` |
| `elkpy` (in `tool/.venv`) | ✅ imports cleanly on Python 3.14 |
| `python-osc` (in `tool/.venv`) | ✅ for `sushi-rig listen` — the panel's save button |

Sushi's LV2 support is **Linux-only** — it is excluded from the macOS and Windows
builds. Authoring has to happen here.

To set this up from scratch:

```bash
sudo apt install python3-pip python3-venv python3-lilv lv2-dev lilv-utils liblilv-dev
cd tool && python3 -m venv --system-site-packages .venv
source .venv/bin/activate
pip install -e ".[dev,live]"

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

If you already have a `tool/.venv` from before `python-osc` was added, re-run
that last `pip install -e ".[dev,live]"` to pick it up — `sushi-rig up`
launches `sushi-rig listen`, which needs it.

Two snags this hit in practice. **`lv2-dev` alone is not enough** —
`python3-lilv`'s ctypes binding looks for the unversioned `liblilv-0.so`, which
is shipped by `liblilv-dev`, not `lv2-dev`; without it, `import lilv` fails with
`OSError: liblilv-0.so: cannot open shared object file`. And a venv will not see
the system `python3-lilv` at all unless created with `--system-site-packages`.

The brief's predicted risk — Python 3.14 being too new for `elkpy` — did not
materialise; it installs and imports cleanly.

### Preflight

Run before trusting anything downstream:

```bash
lv2ls | wc -l                                  # plugins discoverable
echo $LV2_PATH                                 # should be non-empty
./sushi --version                              # must list lv2
python3 -c "import lilv; print('ok')"
python3 -c "import elkpy; print('ok')"
```

**If a URI is absent from `lv2ls`, Sushi cannot load it** regardless of what is
on disk. That is the single most common failure in this project, and it is worth
checking first every time.

## Plugins and portability

`plugins/` is **not tracked** — it holds third-party GPL binaries that a package
manager reproduces in seconds, and tracking it would add hundreds of MB for no
benefit.

It is structured deliberately:

```
plugins/
├── 3BandEQ.lv2       the bundles this rig actually uses, at the top level
├── gx_chorus.lv2     where LV2_PATH looks
├── gx_compressor.lv2
├── gx_oc_2.lv2
├── gxts9.lv2
├── mda.lv2
└── archive/          everything else — off the path, ignored by lilv
```

**`LV2_PATH` is set to `plugins/` and nothing else** — no `/usr/lib/lv2`
fallback. That is the whole point: a missing or renamed plugin now fails loudly
at startup instead of silently resolving against the system copy and hiding the
fact that the rig is not actually self-contained.

`plugin-manifest.txt` means **every LV2 URI this rig can load** — 41 of them,
from six bundles. Since Sushi addresses plugins by URI and never by path, that
manifest plus those six bundles is the whole dependency set.

`plugins/archive/manifest.ttl` is an intentionally empty LV2 manifest. `archive/`
sits inside a directory on `LV2_PATH`, and lilv probes every subdirectory there
for one; without it, every startup logs a spurious "failed to open
.../archive/manifest.ttl". An empty manifest parses fine and declares nothing,
so lilv reads it and stays quiet — which keeps a real plugin-loading error
visible instead of buried in known noise.

> **This replaces a long-standing defect.** `plugins/` used to be a flat copy of
> the system LV2 tree with all 262 bundles one level *down* in `plugins/lv2/`,
> where `LV2_PATH` never looked. The rig worked only because `/usr/lib/lv2` was
> also on the path, so portability was never actually being tested.

---

## Where to start reading

1. **[MANIFEST.md](MANIFEST.md)** — current state and what is being worked on.
2. **[IMPLEMENTATION_BRIEF.md](IMPLEMENTATION_BRIEF.md)** — the authoritative
   spec. §2 lists the domain facts that are easy to get wrong; several contradict
   a reasonable first guess.
3. **[tool/examples/rig.example.yaml](tool/examples/rig.example.yaml)** — what a
   rig spec looks like.
