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

`rig.yaml` is the only hand-authored file. Everything below it is generated, and
`verify` runs after every `emit` — it is cheap and it catches the failure mode
that matters.

The tweak → capture → emit loop at the bottom is where the time actually goes.

---

## Repository layout

This is a **monorepo with two zones**: the *rig* (personal, machine-specific —
the configs and scripts you run daily) at the root, and the *tool* (reusable
software that generates the configs) under `tool/`. They share one workflow, so
they share one repo; if the tool ever needs to stand alone, it can be split out
then.

```
.                               ── RIG (root): what you run daily
├── start-rig.sh                launch tuner + patchbay + Sushi
├── stop-rig.sh                 interrupt, settle, force-kill
├── config/                     Sushi configs — the deployable artefacts
│   ├── acoustic_reverb_fx.json
│   ├── acoustic_chorus_fx.json   current rig (what start-rig.sh launches)
│   ├── fx.json                   minimal internal-reverb rig
│   └── empty.json                passthrough, for verifying the audio path
├── Patchbay/                   qpwgraph sessions
│   ├── rig.qpwgraph              routing loaded at startup
│   ├── Default.qpwgraph
│   └── Test_1.qpwgraph
├── plugin-manifest.txt         every LV2 URI available on this machine
│
├── tool/                       ── TOOL: generates the configs above
│   ├── pyproject.toml
│   ├── src/sushi_rig/            spec, emit, dump, verify, probe, live, panel, cli
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
./start-rig.sh     # tuner, patchbay, and Sushi on the current config
./stop-rig.sh      # clean shutdown, releases JACK ports
```

`start-rig.sh` sets `LV2_PATH`, kills any stale instances, launches `fmit`
(tuner) and `qpwgraph` (patchbay, restoring saved connections), then starts
Sushi under PipeWire's JACK shim.

To run a different rig, change the config path on the last line of
`start-rig.sh`.

> **`LV2_PATH` must be set, or Sushi loads no LV2 plugins at all.** It exits 4
> with `Failed to load tracks from the Json config file` — an error that never
> mentions `LV2_PATH`. `lv2ls` is not a proxy for this: it finds 648 plugins
> using lilv's defaults while Sushi finds none. Running `./sushi -c <config>`
> by hand, outside the script, hits this every time:
>
> ```bash
> export LV2_PATH="$HOME/Sushi/plugins/lv2:$HOME/Sushi/plugins:/usr/lib/lv2"
> ```

### Checking a config loads

No guitar, no audio interface, no extra dependencies needed:

```bash
./sushi --dump-plugins -c config/acoustic_reverb_fx.json
```

This starts Sushi with the dummy frontend, prints every hosted plugin's
parameters as JSON, and exits. It is the authoritative source for parameter
names, and the cheapest way to know a config is loadable.

## Environment

Verified on this machine:

| | Status |
|---|---|
| Sushi 1.3.0 | ✅ built with `vst3, lv2, jack, rpc control, ableton link` |
| LV2 plugins discoverable | ✅ 648 via `lv2ls` |
| PipeWire / JACK, qpwgraph, fmit | ✅ |
| `lilv` Python bindings (system) | ✅ `python3-lilv` + `liblilv-dev` |
| `elkpy` (in `tool/.venv`) | ✅ imports cleanly on Python 3.14 |

Sushi's LV2 support is **Linux-only** — it is excluded from the macOS and Windows
builds. Authoring has to happen here.

To set this up from scratch:

```bash
sudo apt install python3-pip python3-venv python3-lilv lv2-dev lilv-utils liblilv-dev
cd tool && python3 -m venv --system-site-packages .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

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

`plugins/` is **not tracked in git**. It is a byte-for-byte copy of the system
LV2 installation (262 of 262 bundles match `/usr/lib/lv2`), so tracking it would
add ~358 MB of third-party GPL binaries that are reproducible from a package
manager in seconds.

What *is* tracked is `plugin-manifest.txt` — the full list of LV2 URIs
available here. Since Sushi addresses plugins by URI and never by path, that
manifest plus a package install is enough to reconstruct the environment. Making
this reproducible on a fresh machine is work item 5 in
[MANIFEST.md](MANIFEST.md).

Be aware that the rig currently depends on the **system** LV2 installation, not
the bundled directory — `start-rig.sh` puts `plugins/` on `LV2_PATH`, but the
262 bundles live one level down in `plugins/lv2/` and are never seen. The rig
works only because `/usr/lib/lv2` is also on the path. See *Known defects* in
the manifest before relying on `plugins/` for portability.

---

## Where to start reading

1. **[MANIFEST.md](MANIFEST.md)** — current state and what is being worked on.
2. **[IMPLEMENTATION_BRIEF.md](IMPLEMENTATION_BRIEF.md)** — the authoritative
   spec. §2 lists the domain facts that are easy to get wrong; several contradict
   a reasonable first guess.
3. **[tool/examples/rig.example.yaml](tool/examples/rig.example.yaml)** — what a
   rig spec looks like.
