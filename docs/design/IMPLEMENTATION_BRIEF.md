# sushi-rig — Implementation Brief

A handover document for implementing this project in Claude Code. It covers what
the tool does and why the architecture is shaped this way, the domain facts that
are easy to get wrong, the target repo layout, and the empirical checks to run
instead of assuming.

Read the whole brief before writing code. Several sections contradict what a
reasonable first guess would be.

---

## 1. What this is for

The goal is a headless LV2 audio rig running on Elk Audio OS, where Sushi (Elk's
plugin host) loads a JSON configuration file describing tracks, plugin chains,
routing and initial parameter values. Hand-authoring that JSON for 10–30 plugins
is impractical: parameter values are normalised floats addressed by name, so the
file is unreadable and unverifiable by eye.

The tool closes that loop. A human-authored YAML file describes the rig
structurally. The tool builds that rig inside a running Sushi, exposes its
parameters as faders, and then reads the tweaked state back out and bakes it into
a deployable Sushi config.

### 1.1 The central architectural decision

**Sushi is both the deployment target and the authoring host.** Do not build a
converter from a DAW or desktop host session format.

The obvious approach — configure plugins in Carla, Ardour or Zrythm, then convert
the session file to Sushi JSON — fails for three compounding reasons:

1. Those hosts expose LV2 parameters by **port symbol** (`gain`, `threshold`).
   Sushi's `initial_state` block addresses parameters by the **name Sushi
   assigns them**, which is derived from the plugin's TTL metadata and is not
   guaranteed to equal the symbol. Every plugin needs a symbol-to-name map.
2. Those hosts store **real-world values** (-12.0 dB, 440 Hz). Sushi normalises
   every parameter to 0.0–1.0 across all plugin formats. Converting requires
   each port's min/max, and correct handling of logarithmic and enumerated
   ports.
3. Both mappings are **silently version-fragile**. A plugin update that renames
   a port or reorders parameters produces a config that loads without error and
   sounds wrong.

Authoring in Sushi eliminates all three. The values you read back are already
normalised and already named the way Sushi names them, because the same wrapper
code produced both. There is no translation layer to maintain.

The cost of this decision: Sushi is headless, so plugins' own graphical
interfaces are unavailable. Configuration happens through generated faders. That
was an accepted trade-off when scoping this.

### 1.2 The workflow the tool supports

```
rig.yaml ──emit──> rig.json ──verify──> (names confirmed)
                      │
                      ├──panel──> panel.json ──> Open Stage Control faders
                      │
                   sushi -j -c rig.json  (or: push, into a running instance)
                      │
                   [tweak by ear]
                      │
                   capture ──> state.json
                      │
rig.yaml + state.json ──emit──> rig.json (now with initial_state)
```

Steps 5–6 are the loop the user lives in. `verify` is cheap and should run after
every `emit`.

---

## 2. Domain facts that must be respected

These are established from the Elk documentation and Sushi's changelog. Do not
rederive them; several are counter-intuitive.

### 2.1 LV2 in Sushi is Linux-only

Sushi builds natively for macOS (since 1.0) and Windows (since 1.2), but **LV2
support is excluded from both**. Authoring must happen on Linux. Requires a
build with `SUSHI_WITH_LV2=ON`.

### 2.2 Plugins are addressed by URI, never by path

LV2 forbids referencing plugins by filesystem path. The config uses a `uri`
member instead of `path`:

```json
{ "name": "comp", "type": "lv2", "uri": "http://calf.sourceforge.net/plugins/Compressor" }
```

Plugin discovery happens through the `LV2_PATH` environment variable. `lv2ls`
lists every URI visible to the LV2 machinery. **If a URI is absent from `lv2ls`
output, Sushi cannot load it**, regardless of what is present on disk. This is
the single most common failure and worth a preflight check in the tool.

### 2.3 All parameter values are normalised 0.0–1.0

Since Sushi 0.10.1, parameter values are always normalised, across every plugin
format. `initial_state` values, gRPC `SetParameterValue`, OSC parameter messages
and MIDI CC range limits are all in the 0.0–1.0 domain, not plugin units.

Consequences for implementation:

- `capture` records normalised values and does no conversion.
- `emit` writes normalised values and does no conversion.
- `probe` stores real-world ranges purely so a human can interpret the numbers.
- Any real-world-to-normalised conversion helper assumes linear mapping, which
  holds for plain control ports. **Logarithmic and enumerated ports must warn
  rather than guess.** Getting these silently wrong is worse than refusing.

elkpy exposes both domains — `get_parameter_value` for normalised and a
domain-value variant alongside it. Confirm which is which on the installed
version before relying on either (see §7).

### 2.4 Prefer `initial_state` over `events`

The config supports two ways to set startup parameter values: a timed `events`
list, and an `initial_state` block. `initial_state` is the documented
recommendation, and as of Sushi 1.3.0 all the shipped example configs were
migrated to it. Use `initial_state` exclusively.

Shape:

```json
"initial_state": [
  {
    "processor": "gtr_comp",
    "bypassed": false,
    "program": 3,
    "parameters": [ { "name": "Threshold", "value": 0.421875 } ],
    "properties": [ { "name": "filename", "value": "kick.wav" } ]
  }
]
```

`program` and `bypassed` are optional. `properties` are string properties, and
are separate from parameters.

### 2.5 Track and processor names share one namespace

Sushi addresses processors by name. Track names must be unique, plugin names must
be unique, and a plugin may not share a name with a track. Validate this before
emitting or pushing — the failure mode otherwise is a confusing runtime error.

### 2.6 The LV2 `patch:` extension is not supported

Sushi does not implement `patch:`. Any plugin whose state lives outside control
ports — sfz loaders, convolution reverbs, sample players, anything with a file
path parameter — **cannot be configured from a JSON config at all**. There is no
workaround at the config level.

Such plugins need binary state set over gRPC at runtime, using the processor
state save/reload added in Sushi 1.0. That is a different mechanism with a
different lifecycle, and it is out of scope for the initial build.

The tool should identify these plugins during `probe` and report them clearly,
because discovering it late changes the shape of the rig. Detection heuristic:
the plugin declares `state:interface` or has `patch:writable` properties in its
TTL, and its interesting settings do not appear as control ports.

### 2.7 Sushi's own introspection is the authoritative name source

```bash
sushi --dump-plugins -c rig.json > dump.json
```

This starts Sushi with the dummy frontend, prints the name, label, ID and OSC
path for every hosted plugin's parameters as JSON to stdout, and exits
immediately. No audio hardware needed.

This is the ground truth for parameter naming and should be treated as such
throughout. Do not hardcode assumptions about whether Sushi uses `lv2:name` or
the port symbol — resolve it from the dump.

Two practical notes: Sushi may emit log lines before the JSON payload, so the
parser should locate the start of the JSON rather than assuming the stream begins
with it; and the dump's exact structure has shifted between Sushi versions, so
walk it generically for dicts carrying a `name` and a `parameters` list rather
than binding to a fixed schema.

### 2.8 Interfaces and defaults

| Interface | Default | Notes |
|---|---|---|
| gRPC | `localhost:51051` | Full control API, split into sub-controllers |
| OSC | UDP `24024` | Parameter set/notify; paths from `--dump-plugins` |
| Audio frontend | — | `-j` JACK, `-a` Portaudio, `-o` offline, `-r` Raspa (on-device) |

The gRPC API supports runtime graph editing (`create_track`,
`create_processor_on_track`, `delete_processor_from_track`) — added in 0.11 — so
the rig can be built into a running instance without a restart.

Get requests over gRPC are synchronous; set requests are scheduled
asynchronously. elkpy's graph-editing calls return an `ElkpyEvent` (an
`asyncio.Event` subclass) that is set when Sushi confirms the operation, carrying
`.sushi_id`, `.data` and — for processor creation — `.params`. Await or wait on
it before issuing dependent calls, or processor creation will race against track
creation.

elkpy runs its notification monitoring on an asyncio event loop. In a synchronous
program it starts one on a separate thread. In an async program, instantiate
`SushiController` **inside** the running loop or you get two competing loops.

---

## 3. Repo layout

```
sushi-rig/
├── README.md
├── pyproject.toml
├── IMPLEMENTATION_BRIEF.md          # this file
├── src/
│   └── sushi_rig/
│       ├── __init__.py
│       ├── cli.py                   # argparse entry point, thin
│       ├── spec.py                  # rig.yaml model + validation
│       ├── probe.py                 # lilv -> plugin catalogue
│       ├── emit.py                  # spec + state -> Sushi JSON
│       ├── live.py                  # elkpy: push and capture
│       ├── dump.py                  # --dump-plugins parsing
│       ├── verify.py                # cross-check config against dump
│       └── panel.py                 # Open Stage Control panel generation
├── examples/
│   ├── rig.example.yaml
│   └── dump.example.json            # captured real dump, for offline tests
└── tests/
    ├── test_spec.py
    ├── test_emit.py
    ├── test_dump.py
    ├── test_verify.py
    └── fixtures/
```

A working single-file prototype exists (`sushi_rig.py`) and should be supplied
alongside this brief. Treat it as reference for the data shapes and the emit
logic, not as the target structure — it needs splitting into the modules above,
proper packaging, and a real test suite.

### 3.1 Dependency boundaries

Keep the heavy, platform-specific dependencies isolated so the majority of the
codebase remains testable on any machine:

| Module | Depends on | Testable offline |
|---|---|---|
| `spec`, `emit`, `dump`, `verify`, `panel` | PyYAML only | Yes, fully |
| `probe` | lilv Python bindings | No — needs an LV2 installation |
| `live` | elkpy, grpcio | No — needs a running Sushi |

`probe` and `live` must import their dependencies lazily inside the functions
that use them, with a clear error message on `ImportError`, so that `emit` and
`verify` work on a machine with neither installed. This matters: the offline
modules are where the logic lives and where the tests earn their keep.

---

## 4. Initial setup

```bash
mkdir sushi-rig && cd sushi-rig
git init
python3 -m venv .venv && source .venv/bin/activate

# Runtime deps
pip install pyyaml elkpy
pip install --group dev pytest ruff mypy   # or list under [dependency-groups]

# System deps (Debian/Ubuntu)
sudo apt install python3-lilv lv2-dev lilv-utils jackd2
```

`python3-lilv` provides the lilv Python bindings, which are the only sane way to
read LV2 metadata. If they are unavailable on the target distribution, the
fallback is `lv2info -p <uri>` plus `rdflib` over the plugin's `.ttl` — workable
but considerably more fiddly, and worth avoiding.

Note that the venv will not see system `python3-lilv` unless created with
`--system-site-packages`. Either create it that way, or run `probe` outside the
venv, or install `lilv` from source into the venv. Pick one and document it in
the README — this trips people up.

### 4.1 Preflight verification

Confirm the environment before writing any tool code. Each of these should
succeed:

```bash
lv2ls | head                                  # LV2 plugins discoverable
echo $LV2_PATH                                # non-empty
python3 -c "import lilv; w=lilv.World(); w.load_all(); print(len(list(w.get_all_plugins())))"
sushi --version                               # Sushi on PATH, built with LV2
python3 -c "import elkpy; print(elkpy.__file__)"
```

Then confirm Sushi can actually load an LV2 plugin, using the LV2 examples in
Sushi's own `misc/config_files/` directory. Do this before building anything —
if Sushi cannot load a plugin from a known-good config, the problem is
environmental and the tool will not help.

---

## 5. Data formats

### 5.1 `rig.yaml` — the only hand-authored file

```yaml
host:                        # passed through to host_config verbatim
  samplerate: 48000
  midi_inputs: 1
  tempo: 120
  playing_mode: stopped      # or "playing"
  tempo_sync: internal       # internal | midi | ableton_link
  master_limiter: true
  audio_clip_detection:
    inputs: true
    outputs: true

tracks:
  - name: gtr_in
    channels: 2
    multibus: false          # if true, set `buses`; channels must be even
    thread: 0                # optional explicit core allocation (Sushi 1.3+)
    inputs:
      - engine_channel: 0
        track_channel: 0
    outputs:
      - engine_bus: 0
        track_bus: 0
    plugins:
      - name: gtr_comp
        type: lv2            # lv2 | vst2x | vst3x | internal
        uri: http://calf.sourceforge.net/plugins/Compressor

midi:                        # passed through verbatim
  track_connections: [...]
  cc_mappings: [...]

osc:
  enable_all_processor_outputs: true
```

Design rule: `host`, `midi`, `osc` and `cv_control` pass through to the emitted
config unchanged. Only `tracks` is transformed. This keeps the tool out of the
business of modelling Sushi's entire schema, and means new Sushi config features
work without a tool change.

Routing uses either bus pairs (`engine_bus`/`track_bus`) or individual channels
(`engine_channel`/`track_channel`), not both in one entry.

### 5.2 `state.json` — captured live state

```json
{
  "processors": {
    "gtr_comp": {
      "parameters": { "Threshold": 0.421875, "Ratio": 0.25 },
      "bypassed": false
    },
    "jx10": {
      "parameters": { "Resonance": 0.5 },
      "program": 3
    }
  }
}
```

Keys are Sushi processor names. Values are normalised. `program`, `bypassed` and
`properties` are optional and omitted when unavailable. Tracks appear here
alongside plugins, since tracks also carry parameters (gain, pan).

### 5.3 `catalogue.json` — probe output

```json
{
  "http://calf.sourceforge.net/plugins/Compressor": {
    "name": "Calf Compressor",
    "control_ports": [
      {
        "index": 4,
        "symbol": "threshold",
        "name": "Threshold",
        "direction": "input",
        "min": 0.000977, "max": 1.0, "default": 0.125,
        "toggled": false, "integer": false,
        "logarithmic": true, "enumeration": false,
        "scale_points": [ { "label": "Off", "value": 0.0 } ]
      }
    ],
    "uses_patch_extension": false
  }
}
```

Two purposes. First, drift detection: diff a catalogue captured on the authoring
machine against one captured on the board to catch version mismatches before
they produce a wrong-sounding rig. Second, flagging the ports where
normalisation assumptions break — filter for `logarithmic`, `enumeration`, and
missing ranges.

---

## 6. Module specifications

### `spec.py`

Dataclasses `PluginSpec`, `TrackSpec`, `RigSpec`. Loads and validates `rig.yaml`.

- `RigSpec.load(path) -> RigSpec`
- `RigSpec.validate() -> list[str]` — returns problems, does not raise. Checks:
  name uniqueness across the combined track/plugin namespace; `uri` present for
  `type: lv2`; `path` or `uid` present for VST types; `buses` present and
  `channels` even when `multibus`; routing entries not mixing bus and channel
  forms.
- `TrackSpec.to_sushi()` / `PluginSpec.to_sushi()` — emit the config fragment.

Validation returning a list rather than raising on first problem matters at this
scale: the user wants all twelve naming collisions at once, not one per run.

### `probe.py`

- `probe(uris: list[str] | None) -> dict` — catalogue for the given URIs, or all
  installed plugins when `None`.

Walk each plugin's ports, keep only `lv2:ControlPort`, and record the fields in
§5.3. lilv surface needed: `World()`, `load_all()`, `get_all_plugins()`;
`plugin.get_uri()`, `get_name()`, `get_num_ports()`, `get_port_by_index(i)`;
`port.get_symbol()`, `get_name()`, `get_range()` (returns default, min, max),
`is_a(ns.lv2.ControlPort)`, `has_property(ns.lv2.toggled)`, `get_scale_points()`.
Logarithmic is `ns.pprops.logarithmic`, from the port-properties namespace rather
than core LV2.

Warn on any requested URI not found, listing it explicitly — a typo'd URI
otherwise produces a silently incomplete catalogue.

### `emit.py`

- `emit(rig: RigSpec, state: dict | None) -> dict`

Validates first and refuses to emit on any problem. Assembles `host_config`,
`tracks`, and the pass-through sections. When `state` is supplied, builds
`initial_state`, skipping with a warning any processor present in the state but
absent from the spec — that means the spec and the captured session have
diverged, which the user needs told about rather than silently resolved.

Round parameter values to ~6 decimal places. Full float repr makes the diffs
unreadable and the precision is meaningless against a normalised range.

### `live.py`

- `push(rig, address, proto) -> None` — creates tracks then processors in a
  running Sushi.
- `capture(address, proto) -> dict` — reads all processors and parameter values.

elkpy surface:

```
SushiController(address, proto_path)
  .audio_graph   get_tracks(), get_track_info(id), get_track_processors(id),
                 get_processor_id(name), get_processor_info(id),
                 get_processor_bypass_state(id), create_track(name, channels),
                 create_processor_on_track(...), delete_processor_from_track(...)
  .parameters    get_processor_parameters(id), get_track_parameters(id),
                 get_parameter_value(proc_id, param_id), set_parameter_value(...)
  .programs      get_processor_programs(id), get_processor_current_program(id)
  .notifications subscribe_to_parameter_updates(cb, blocklist)
  .close()
```

Always `close()` in a `finally` block — the sub-controllers hold gRPC channels
and threads that will hang the process otherwise.

Wait on the `ElkpyEvent` returned by graph-editing calls before dependent calls
(§2.8). Query `program_count` from `get_processor_info` before asking for the
current program; processors without programs will error.

Capture should treat `program` and `bypassed` as optional and tolerate their
absence, since availability varies by plugin and Sushi version. Parameters are
not optional — a processor returning no parameters is worth a warning.

### `dump.py`

- `dump_plugins(config_path, sushi_bin) -> Any` — runs `sushi --dump-plugins`,
  locates and parses the JSON, surfaces stderr on failure.
- `collect_parameter_names(dump) -> dict[str, set[str]]` — walks the structure
  generically (§2.7), indexing parameter names by processor name.

Also worth extracting OSC paths here if present in the dump, since `panel.py`
should prefer real paths from Sushi over constructing them.

### `verify.py`

- `verify(config_path, sushi_bin) -> int` — exit code, 0 on success.

For every `initial_state` entry, confirm the processor appears in the dump and
every referenced parameter name exists on it. On failure, print the available
parameter names so the fix is obvious. A near-miss suggestion (case-insensitive
or fuzzy match against the available names) is a worthwhile addition — the
common error is case or a symbol-versus-name mix-up, and naming the likely
intended parameter saves a round trip.

### `panel.py`

- `build_osc_panel(dump, osc_port) -> dict`

One tab per processor, a fader per parameter, laid out in a grid. Addresses from
the dump where available. Range 0–1, matching Sushi's normalised domain.

Elk's own `elk-examples` repository ships Open Stage Control GUIs alongside its
LV2 and multi-FX configs — worth reading for the panel conventions before
finalising the generated structure.

---

## 7. Open questions to resolve empirically

Resolve each of these with the given command before building on top of it. None
should be guessed.

**Does Sushi name LV2 parameters from `lv2:name` or the port symbol?**
Run `probe` and `--dump-plugins` on the same plugin and compare. This determines
whether any symbol-to-name mapping is needed at all. `capture` is insulated
either way because it reads names from Sushi, but `cc_mappings` and
`cv_control` entries in a hand-authored `rig.yaml` reference parameter names
directly and will need whichever convention holds.

**How does Sushi normalise logarithmic ports?**
Set a known parameter on a logarithmic port to 0.5 over gRPC, then read back the
formatted value (`get_parameter_value_as_string` or the equivalent) and compare
against the port's range from `probe`. Calf plugins use logarithmic frequency
and time ports heavily, so this will come up immediately in practice.

**Which elkpy getter returns the normalised value?**
elkpy exposes normalised, domain and formatted variants. Confirm with
`python3 -m pydoc elkpy.parametercontroller` on the installed version rather
than trusting the names.

**What are `create_processor_on_track`'s actual parameter names?**
They have changed across elkpy releases. Check
`python3 -m pydoc elkpy.audiographcontroller`.

**What is the exact shape of `--dump-plugins` output on the installed version?**
Capture a real dump early and commit it to `examples/dump.example.json` as a
test fixture. This is the highest-value artefact for offline testing.

**Do integer and toggled ports normalise as expected?**
A toggled port has min 0 and max 1, so normalisation is identity — but confirm
Sushi does not expose it as a two-value enumeration with different semantics.

---

## 8. Test strategy

Everything in the offline group (§3.1) should have real unit tests, driven from
committed fixtures. Priority order:

1. **Spec validation** — duplicate names across the shared namespace, missing
   `uri` on an LV2 plugin, multibus without `buses`, mixed routing forms. Assert
   on the returned problem list, not on exceptions.
2. **Emit** — a known spec plus known state produces exactly the expected JSON.
   Include the divergence case (state referencing an unknown processor) and
   assert the warning.
3. **Dump parsing** — against the real captured dump fixture, plus a
   deliberately differently-nested synthetic one, to confirm the generic walk
   holds.
4. **Verify** — a passing config, a wrong-case parameter name, an entirely
   unknown processor. Inject the dump via a fixture rather than invoking Sushi.
5. **Panel** — structural assertions on tab and widget counts and address format.

For the hardware-dependent paths, integration checks rather than unit tests:
`verify` against a real Sushi build is itself the integration test for emit, and
`sushi -o -i input.wav -c rig.json` renders a config offline, which gives a
regression check that a config still sounds as intended. Committing a short
input file and hashing the rendered output is a cheap guard against silent
parameter drift.

---

## 9. Out of scope for the initial build

- Binary or opaque plugin state over gRPC (needed for `patch:`-dependent
  plugins, §2.6). Different mechanism, different lifecycle.
- Any conversion from DAW or desktop host session formats (§1.1). Deliberately
  excluded.
- A bespoke web GUI. Open Stage Control covers the need; revisit only if a
  patchbay-style graph view becomes necessary.
- Deployment and provisioning onto the Elk board.
- Sushi's session state save/reload as a persistence format. It exists (added in
  1.0) and is worth knowing about, but the deployable artefact here is a JSON
  config, not a session blob.

---

## 10. Build order

1. `spec.py` with full validation, and its tests. Everything depends on this.
2. `emit.py` with tests. At this point `rig.yaml -> rig.json` works end to end
   with no external dependencies.
3. `dump.py` and `verify.py`. Capture a real dump and commit it as a fixture.
   **Resolve the naming question in §7 here** — before building anything that
   assumes an answer.
4. `probe.py`. Run it across the intended plugin set and review the output for
   logarithmic, enumerated and rangeless ports, and for `patch:` users.
5. `live.py`. `capture` before `push` — it is simpler, read-only, and exercises
   the same connection code.
6. `panel.py`, then the full loop.

Stop after step 3 and confirm the naming question. If the answer is unexpected,
it is much cheaper to absorb before `live.py` and `panel.py` are built on an
assumption.
