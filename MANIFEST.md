# Work manifest

What is being worked on, why, and what's been learned along the way. The live
backlog of what's left to do now lives in
[GitHub Issues](https://github.com/SonnyDewfall/Sushi/issues) — this file is
the narrative record: decisions, defects found and fixed, and facts settled
empirically about Sushi and its ecosystem. Work items below that still have
open issues link to them rather than duplicating the task description.

> Not to be confused with `plugin-manifest.txt`, which is the list of available
> LV2 plugin URIs.

**Status:** 🟢 done · 🟡 in progress · ⚪ not started · 🔵 ongoing

---

## 1. 🟡 Shared understanding — README and manifest

Establish what the project is, how it is shaped, and what is in flight, so the
next session (human or model) can pick it up cold.

- [x] Read the implementation brief and prototype; audit the live environment
- [x] `README.md` — goal, architecture, workflow, layout, environment
- [x] `MANIFEST.md` — this file
- [x] Smoke-tested every tracked config against the real Sushi 1.3.0 — surfaced
      the defects recorded below
- [x] Restructured into a two-zone monorepo: rig at root, tool under `tool/`.
      Split `config/` (patchbay → `Patchbay/`, inventory → `plugin-manifest.txt`,
      leaving only Sushi JSON), and moved the prototype + examples under `tool/`.
      Full package layout (`tool/src/sushi_rig/`, `pyproject.toml`, `tool/tests/`)
      lands with item 2.

**Done when** someone unfamiliar can clone, read the README, and run the rig.

---

## 2. 🟡 Modelling → config workflow

The core of the project: a stable loop from *tweaking sound by ear* to *a
committed Sushi config*.

Build order (deliberately reordered from the brief's dependency-safe §10 order
to reach a working tweak-by-ear loop as fast as possible — see the plan at
`Project_files` history / chat for the tradeoff discussion):

| Phase | Module | Depends on | Offline-testable |
|---|---|---|---|
| A | environment + package scaffold | — | — |
| B1 | `dump.py` — fix the 1.3.0 trailing-text parse break | — | ✅ |
| B2 | `panel.py` — OSC faders, using `osc_path` verbatim | — | ✅ |
| C1 | `live.py capture` | elkpy | ❌ |
| C2 | `spec.py` — rig.yaml model + validation | PyYAML | ✅ |
| C3 | `emit.py` — spec + state → Sushi JSON | PyYAML | ✅ |
| D | prove the loop on the real rig; fix the defect — 🟢 `acoustic_chorus` ([#1](https://github.com/SonnyDewfall/Sushi/issues/1) for `acoustic_reverb`) | elkpy | ❌ |
| E1 | `verify.py` — [#2](https://github.com/SonnyDewfall/Sushi/issues/2) | — | ✅ |
| E2 | `probe.py` — LV2 metadata catalogue — [#3](https://github.com/SonnyDewfall/Sushi/issues/3) | lilv | ❌ |
| E3 | config test suite — [#4](https://github.com/SonnyDewfall/Sushi/issues/4) | — | mixed |

A working single-file prototype of all these already exists at
`tool/prototype_reference.py`. Treat it as reference for data shapes and emit logic, not
as the target structure — it is being split into `tool/src/sushi_rig/` with
`pyproject.toml` and `tool/tests/`.

- [x] Environment ready: `python3-pip`, `python3-venv`, `python3-lilv`,
      `lv2-dev`, `lilv-utils`, and `liblilv-dev` installed system-wide (the
      unversioned `liblilv-0.so` symlink lilv's ctypes binding needs ships in
      `liblilv-dev`, not `lv2-dev` — tripped this up initially). `tool/.venv`
      created with `--system-site-packages` so it sees `python3-lilv`; `pyyaml`
      and `elkpy` installed into it and both import cleanly on Python 3.14 —
      the brief's predicted 3.14 risk didn't materialise.
- [x] All open questions from the brief (§7) settled empirically against a live
      Sushi 1.3.0 — see *Open questions* below. Two more elkpy-vs-brief
      mismatches found along the way, on top of the ones already known:
      `create_processor_on_track`'s real argument names, and the correct
      synchronous wait pattern for graph-editing calls. Nothing left blocking
      `live.py`, `probe.py`, or `panel.py`.
- [x] Package scaffold: `tool/pyproject.toml` (src layout, `pytest` dev group,
      `sushi-rig` console script), `tool/src/sushi_rig/`, `tool/tests/`.
      Renamed `tool/sushi_rig.py` → `tool/prototype_reference.py` — its old name
      collided with the new package's import name and, since `tool/` sits on
      `sys.path` at runtime, silently shadowed it. Left in place as reference
      until item 2 phase D deletes it.
- [x] **B1 `dump.py`** — fixed break, parses with `JSONDecoder().raw_decode`
      from the first `{`, ignoring the trailing `Parameter dump completed -
      exiting.` line. `collect_parameter_info` added alongside the prototype's
      `collect_parameter_names`, keeping each parameter's full record
      (`osc_path` included) rather than just its name.
- [x] **B2 `panel.py`** — rebuilt to use each parameter's `osc_path` from the
      dump verbatim instead of constructing one from the name.
- [x] **B2 revisited: `panel.py`'s session schema verified against real
      open-stage-control 1.31.1**, not just against the brief's description.
      Installed it (`.deb` from openstagecontrol.ammd.net — apt/snap don't
      package it; note its `_apt` sandbox needs the `.deb` somewhere
      world-traversable, `$HOME` at `750` blocks it, copy to `/tmp` first),
      then actually loaded generated panels into it and watched them render.
      Two real defects found this way, neither visible from reading docs:
      - `sendPort` on the root object **is not a real property** — the
        prototype invented it. The actual send target is set via
        open-stage-control's own `--send ip:port` CLI flag at launch.
        Removed; `cli.py panel` now prints the matching launch command
        instead.
      - `layout: "grid"` on a container **collapses every fader inside it to
        a sliver** — confirmed by isolating one fader (renders correctly at
        its set height alone) against the same fader inside a grid container
        (collapses to near-nothing). Switched to plain flow layout (`layout`
        left unset — `"default"` is already open-stage-control's own
        default), which was confirmed to wrap same-sized widgets correctly
        at full height.
      Final result — generated from the live `acoustic_chorus_fx.json` dump,
      loaded into real open-stage-control, all 4 tabs and all faders
      rendered and were confirmed draggable (watched a handle move on drag).
- [x] 13 offline tests passing (`cd tool && source .venv/bin/activate && pytest`),
      run against the real `dump.example.json` fixture plus a synthetic
      differently-nested one for the generic-walk claim.
- [x] **C1 `live.py capture`** — fixed a real API break found only by running
      it: `AudioGraphController` has no `get_tracks` in elkpy 1.2.0, the real
      method is `get_all_tracks()`. Also fixed a second, more consequential
      bug: `capture` was pulling in every parameter Sushi reports, including
      non-automatable (read-only) ones like `"Latency OUT"` and the various
      `*_meter`/`*_visibility` params — baking one of those into
      `initial_state` makes Sushi **refuse to load the config at all**
      (exit 7, not a silent no-op). `capture` now filters on
      `ParameterInfo.automatable`. `push()` also written (kwarg-name fixes
      from the earlier session, plus the `.is_set()` poll loop), though the
      chosen workflow (launch Sushi on a full config, tweak, capture, emit)
      doesn't require it for the core loop.
- [x] **C2 `spec.py`, C3 `emit.py`** — ported and tested; 36 offline tests
      passing in total.
- [x] **A schema bug bigger than anything found so far**: `initial_state`'s
      `parameters` and `properties` are **dicts** of `{name: value}`, not a
      list of `{"name", "value"}` objects. Both the brief §2.4's own example
      and the prototype used the list form — confirmed wrong against Sushi's
      *own* shipped example configs (extracted from inside the AppImage,
      `usr/share/config_files/*.json`, five different files, unanimous). The
      list form doesn't error loudly on its own shape; it fails with the same
      "Failed to load the initial processor states." Sushi gives for a
      genuinely bad parameter name, which is what made this take real
      bisection to find rather than a quick read of an error message. Fixed
      in `emit.py`.
- [x] **The full loop proven against a live Sushi 1.3.0**, not just unit
      tested: `emit` → config loads → `capture` → `emit` with real captured
      state → **that config also loads**, with the captured values actually
      applied on startup (set `Ratio` to a deliberately non-default `0.9`
      normalised, restarted Sushi from the baked config, read back `0.9`
      normalised / `90.1` real-world — matching `1 + 0.9×99` on the `[1,100]`
      domain exactly).
- [x] **Phase D done for `acoustic_chorus`, for real, on real hardware
      (2026-09-13):** two hardware-only bugs found and fixed along the way —
      see *Known defects*, `panel.py` fader defaults and `logScale`. Once
      those were fixed, tweaked by ear through a real guitar + audio
      interface, captured, emitted, and adopted the naming convention in the
      same pass: `config/acoustic_chorus.json` (renamed from the never-working
      `acoustic_chorus_fx.json`), `config/src/acoustic_chorus.yaml`,
      `config/archive/acoustic_chorus/v1.0.json`, `start-rig.sh` updated.
      `acoustic_reverb_fx.json` still needs the same pass.

**Future direction for phase D:** the tweak-by-ear step currently runs as an
interactive session — launch Sushi, open Open Stage Control, tweak, then a
person (or Claude) manually runs `sushi-rig capture` at the point the sound is
right. Backlog moved to GitHub Issues (2026-09-13) — see
[#10](https://github.com/SonnyDewfall/Sushi/issues/10).

---

## 3. Headless runs and a config test suite

Backlog moved to GitHub Issues (2026-09-13) — see
[#4](https://github.com/SonnyDewfall/Sushi/issues/4). The real
`--dump-plugins` fixture this depends on already exists at
`tool/examples/dump.example.json`, and the smoke test itself is runnable by
hand today (no new dependencies):

```bash
export LV2_PATH="$HOME/Sushi/plugins/lv2:$HOME/Sushi/plugins:/usr/lib/lv2"
for c in config/*.json; do ./sushi --dump-plugins -c "$c" >/dev/null 2>&1 \
  && echo "ok   $c" || echo "FAIL $c"; done
```

---

## 4. 🟡 Config library structure and versioning

- [x] Physically separated the kinds of file that were mixed in `config/`:
      patchbay routing → `Patchbay/`, plugin inventory → `plugin-manifest.txt`,
      leaving `config/` holding only Sushi JSON configs.
- [x] Naming and versioning convention agreed (below). Applied for real to
      the first config in item 2 phase D (2026-09-13): `acoustic_chorus`, all
      three layers — source (`config/src/acoustic_chorus.yaml`), live config
      (`config/acoustic_chorus.json`, `_meta` populated), and an archived
      `v1.0` snapshot.
- [ ] Remaining three configs (`acoustic_reverb_fx.json` as part of
      [#1](https://github.com/SonnyDewfall/Sushi/issues/1); `empty.json`,
      `fx.json` as [#5](https://github.com/SonnyDewfall/Sushi/issues/5))
      still use the old names — tracked on GitHub, not here.

### Naming

Shared `<instrument>_<character>` stem across the hand-authored source, the
deployable config, and any archived snapshot:

```
config/src/acoustic_chorus.yaml            hand-authored source
config/acoustic_chorus.json                emitted, deployable — what start-rig.sh loads
config/archive/acoustic_chorus/v1.2.json   frozen snapshot
```

Utility/test configs get a `test_` prefix (`test_passthrough.json`, currently
`empty.json`). Live configs drop the redundant `_fx` suffix
(`acoustic_chorus_fx.json` → `acoustic_chorus.json`).

### Versioning — three layers, each doing a different job

1. **Git** is the version of record for the project and for every change to a
   sound. Live config *filenames* stay stable forever — `start-rig.sh` never
   needs editing on a tonal change, and `git diff` on the JSON is exactly how
   parameter drift becomes visible.
2. **`MAJOR.MINOR` in a metadata header**, so a config self-identifies without
   consulting git log: **MAJOR** bumps when the plugin chain changes (added,
   removed, reordered); **MINOR** bumps when only parameter values changed.
3. **`config/archive/<name>/v<MAJOR>.<MINOR>.json`** — a deliberate, named
   snapshot taken when a tone is worth keeping before moving on. Since the
   emitted JSON carries the full chain plus `initial_state`, an archived file
   alone reproduces that exact tone.

### Metadata header

A top-level `_meta` block — confirmed empirically that Sushi 1.3.0 tolerates an
unrecognised top-level key (exit 0, no schema complaint). Only stable fields;
deliberately no timestamp or commit hash, which would diff on every emit and
defeat layer 1:

```json
{
  "_meta": {
    "name": "acoustic_chorus",
    "version": "1.2",
    "source": "config/src/acoustic_chorus.yaml",
    "description": "Acoustic guitar — compressor, EQ, chorus, light reverb",
    "status": "wip"
  },
  "host_config": { ... }
}
```

Archived copies additionally carry `archived_at`. `emit` copies `_meta` through
verbatim from the YAML source; nothing bumps the version automatically — that's
a deliberate act when a change is worth calling MAJOR or MINOR.

Sources live at `config/src/<name>.yaml` — separate from the generated JSON
they produce, sharing a stem so the pairing is obvious, without changing where
`start-rig.sh` looks for the configs it loads.

---

## 5. Portability to other machines

Backlog moved to GitHub Issues (2026-09-13) — see
[#6](https://github.com/SonnyDewfall/Sushi/issues/6). Folds in two open
defects (`start-rig.sh`'s `LV2_PATH` and the duplicate LSP versions) —
see *Known defects* below for the diagnosis; the fix is tracked on the issue.

---

## 6. Grow the rig

Backlog moved to GitHub Issues (2026-09-13) — see
[#7](https://github.com/SonnyDewfall/Sushi/issues/7): an open-ended place to
hang specific plugin/track/instrument additions as they come up.

Constraint worth knowing up front regardless of what lands there: **Sushi
does not support the LV2 `patch:` extension.** Any plugin whose state lives
outside control ports — convolution reverbs, sample players, sfz loaders,
anything taking a file path — cannot be configured from a JSON config at all.
`probe` (issue [#3](https://github.com/SonnyDewfall/Sushi/issues/3)) should
flag these, because discovering it late changes the shape of the rig.

---

## 7. Choose which parameters a panel actually shows

Requested by the user (2026-09-13). Backlog moved to GitHub Issues — see
[#8](https://github.com/SonnyDewfall/Sushi/issues/8).

## 8. Manage tracks and plugin chains from outside (or inside) the panel

Requested by the user (2026-09-13). Backlog moved to GitHub Issues — see
[#9](https://github.com/SonnyDewfall/Sushi/issues/9). Depends on #7 landing
first, since both touch how the panel is generated from the rig spec.

---

## Open questions

Blocking item 2, steps 4–6. Each has a command that settles it; none should be
guessed.

### Settled

**Sushi names LV2 parameters from `lv2:name`, not the port symbol.** Spaces and
capitalisation are preserved verbatim — `"Attack threshold"`, `"Band gain 1.6K"`,
`"Show pre-mix overlay"`. No symbol-to-name mapping layer is needed. Internal
plugins differ from LV2 in that `name` and `label` diverge (Freeverb reports
name `room_size`, label `Room Size`); for LV2 the two were identical throughout.
*(2026-09-13, via `--dump-plugins` on Sushi 1.3.0.)*

**`--dump-plugins` output shape on 1.3.0** is `{"plugins": [{name, label,
parameters: [{name, label, osc_path, id}]}]}` — followed on **stdout** by the
literal line `Parameter dump completed - exiting.`. Captured verbatim as
`tool/examples/dump.example.json`.

> ⚠️ This breaks the prototype. `sushi_rig.py:dump_plugins()` handles *leading*
> log lines but not a *trailing* message, so both `json.loads` paths raise
> `Extra data`. `verify` and `panel` cannot work on 1.3.0 until it parses with
> `JSONDecoder().raw_decode()` from the first `{`. First thing to fix in item 2.

**`osc_path` is provided per parameter**, with spaces replaced by underscores
(`/parameter/compressor_mono/Show_pre-mix_overlay`). `panel.py` must use these
verbatim rather than constructing paths from names — the prototype constructs
them and would produce addresses that never match.

**`get_parameter_value` is normalised; `get_parameter_value_in_domain` is
real-world.** elkpy's own docstring on `get_parameter_value_in_domain` claims it
returns "the normalised value" — that is backwards. Proven against a live Sushi
1.3.0 (dummy frontend, `compressor_mono`'s `Ratio`, domain `[1.0, 100.0]`):
`get_parameter_value` returned `0.030303`, `get_parameter_value_in_domain`
returned `4.0`, and `get_parameter_value_as_string` agreed (`"4.000000"`). The
`sushi_rpc.proto` message `ParameterInfo` carries `min_domain_value` /
`max_domain_value`, confirming "domain" means real-world plugin units.
`capture` must use `get_parameter_value` — which is what the prototype already
does; this was a risk in the prototype's design, not a bug in it.
*(2026-09-13, elkpy on Sushi API 1.2.0.)*

**Sushi ignores the LV2 `pprops:logarithmic` hint and normalises every control
port linearly against `[min, max]`.** Checked four log-tagged ports on the real
compressor (`Ratio` 1–100, `Attack time` 0–2000, `High-pass filter frequency`
10–20000, `Knee` 0.063–1.0) — in every case, measured normalised value equalled
`(domain_value − min) / (max − min)` to 4+ decimal places. `probe.py`'s planned
warning-not-guess stance for logarithmic ports (brief §2.3) can be relaxed: the
existing linear `normalise()` helper in the prototype is correct as-is, no
special-casing needed. *(2026-09-13, empirical, same session as above.)*

**Toggled ports are exposed as ordinary `FLOAT` parameters** (`ParameterType.FLOAT
= 3`, not a distinct `BOOL`), domain `[0, 1]`, normalisation therefore identity.
Confirmed on `compressor_mono`'s `Enabled`. Not exposed as a two-value
enumeration — the brief's suspicion was unfounded. *(2026-09-13)*

**`create_processor_on_track`'s real signature** (this elkpy version, source-read
in `audiographcontroller.py:525`):

```python
create_processor_on_track(name, uid, path, processor_type, track_id, before_processor, add_to_back)
```

Two argument names differ from the prototype's call: `processor_type` (not
`plugin_type`) and `before_processor` (not `before_processor_id`) — the
prototype's `push()` would raise `TypeError` immediately. `PluginType` enum
members (`INTERNAL`, `LV2`, `VST2X`, `VST3X`) do match what the prototype
assumes. *(2026-09-13)*

**Graph-editing calls return a `SushiCommandResponse` (an `asyncio.Event`
subclass with `.id`, `.error`, `.result`), not the brief's `ElkpyEvent` shape
(`.sushi_id`, `.data`, `.params`)** — API drift between elkpy versions, exactly
as §2.8 warned. Its own docstring is explicit about the sync-context contract:
*"await it in an asynchronous program, or check `.is_set()` on it in a
[synchronous one]"*. The prototype's `_wait()` helper calls `.wait()` directly
and, on seeing an awaitable back, just returns — it never actually blocks in
sync code, so `push()` races exactly as brief §2.8 warned it might. `live.py`
needs a real poll loop on `.is_set()` with a timeout. *(2026-09-13, source-read
+ docstring)*

All open questions from the brief are now settled. No more empirical checks
block `live.py`, `probe.py`, or `panel.py`.

---

## Known defects

### 🟢 The amp was never running: Carla needs `pw-jack` too (2026-10-26)

Reported as "NAM may not be working correctly" on a machine set up that day.
It was not working at all, and had never started: `sushi-rig status` showed
`amp pid 39178 (DEAD)`, and `amp.log` held the whole story —

```
Cannot connect to server socket err = No such file or directory
jack server is not running or cannot be started
libjack.so.0 loaded successfully!
Engine failed to initialize, possible reasons:
Failed to create new JACK client
```

Sushi is launched as `pw-jack ./sushi`; Carla was launched as plain `carla`.
`pw-jack` is an `LD_PRELOAD` shim that puts PipeWire's `libjack.so.0` — which
lives in `/usr/lib/x86_64-linux-gnu/pipewire-0.3/jack/`, a directory the
dynamic loader does not search — in front of the system one. Without it Carla
loads the real JACK client library, finds no JACK server (there is no `jackd`
on this machine, and no `ld.so.conf.d` entry redirecting it), and exits.

**Why it survived until a new machine:** the previous rig machine had a `jackd`
for the system libjack to find. This is the same class of hidden portability
break that `LV2_PATH` is deliberately kept narrow to prevent — a dependency
satisfied by the machine rather than by the rig, invisible until the machine
changes. Fixed by launching the amp as `pw-jack carla -n <project>`.

Verified by running `pw-jack carla -n /run/user/1000/sushi-rig/amp.carxp` by
hand before changing anything: the `NAM` JACK client appeared, and Carla logged
`Creating symlink for '.../amp/models/Neve 2.nam'` — the model loads fine, and
always would have.

### 🟢 A child that started and then died was reported as a knob problem (2026-10-26)

Found alongside the above, and the reason it was mistaken for "NAM sounds
wrong" rather than "NAM is absent". The supervisor only caught `OSError` from
`Popen`, which means *the binary does not exist*. A child that execs
successfully and exits a second later — Carla, for want of JACK — left the
supervisor printing `READY`.

The one warning that did appear came from `_replay_amp` timing out on Carla's
OSC port, worded "amp parameters not restored". To a musician that reads as
*your knobs are at their defaults*, not *you have no amp and your guitar is
running dry*. Silent degradation wearing a misleading label, which is worse
than silence (principle 8).

Fixed by polling every child's `poll()` before reporting `READY`, naming the
exit status and the log, and saying plainly for the amp that there is no amp in
the signal path. The parameter replay is now skipped when the amp is already
dead, so the real fault is not buried under a consequence of it.

### 🟢 The rig inherited a 2048-sample quantum and locked itself to it (2026-10-26)

Reported as "high latency — perceptible delay while playing". Measured, it was
**42.69 ms through Sushi alone**, and about **149 ms round trip** by PipeWire's
own declared device latencies. Nothing in the rig set the buffer size, so it
took whatever the desktop happened to be at, which was `clock.max-quantum`.

Three separate facts, each worth keeping:

**PipeWire's quantum is sticky once a JACK client has it.** Sushi runs under
`pw-jack`, and PipeWire's JACK layer sets `node.lock-quantum = True`. Sushi
therefore pins whatever the graph was at *the moment it started* and holds it
for its whole life. Removing the node that caused the large quantum did not
bring it back down — confirmed directly: cutting the offending links moved the
driver but left the quantum at 2048. Startup order decides the buffer size, and
nothing later can undo it.

**The tuner was dragging a webcam into the rig's clock domain.** `fmit` is a
pipewire-alsa client with no explicit target, so it auto-connects to the
*default source* — on this machine a Logitech BRIO webcam. `fmit` is also wired
to the Audient by the patchbay, so it bridged the two devices into one driver
group, and `pw-dump` showed every rig node, the interface and Sushi included,
carrying `node.driver-id = 63` — the webcam. A guitar rig clocked by a webcam.

**The fix is for the rig to state the number.** `PIPEWIRE_QUANTUM` is now set
per-child on Sushi and the amp (`rig.QUANTUM`, 256), for the same reason
`LV2_PATH` is set explicitly: a value this audible belongs in the repository,
not in the machine's current mood. Deliberately *not* set on the tuner or the
panel, which do no DSP.

Measured after the change, on this machine, with the full `electric-chorus`
chain and the amp:

| | before (quantum 2048) | after (quantum 256) |
|---|---|---|
| graph block | 42.7 ms | 5.3 ms |
| Sushi in → Sushi out | 2049 samples / 42.69 ms | 257 samples / 5.35 ms |
| ALSA period / headroom | 512 / 512 | 128 / 128 |
| round trip, computed | ≈149 ms | ≈21 ms |
| total DSP load | — | 7–8%, no xruns |

**The plugin chain costs nothing measurable.** Port-to-port through Sushi is
exactly one quantum (+1 sample of correlation resolution) at both buffer sizes,
so the whole delay is buffering, not DSP. **Nor does the amp**: NAM in → NAM
out is 256 samples, Sushi in → Sushi out is 257, and NAM in → *Sushi* out — the
two in series — is still 257. PipeWire schedules both inside one graph cycle,
so inserting the amp in front of Sushi adds no latency at all. Worth knowing
before anyone trades it away for a shorter chain.

The remaining ≈21 ms is PipeWire's device buffering (one quantum plus 128
samples of headroom in each direction, both of which PipeWire scales from the
quantum automatically) plus the interface's own converters. 128 and 64 both run
here; below about 256 the headroom dominates and the gain gets small.

> **Round trip is computed, not measured.** Measuring it needs a physical cable
> from an interface output back to an input, and there is none on this machine.
> Two traps found while looking for one: `pw-record --target <id>` **silently
> ignores the target** and auto-connects to the default source, so a capture
> that looks like the interface can be the webcam picking the room up through
> the monitors — which is exactly what produced a convincing false positive
> here. Link the ports by hand with `pw-link` and verify with `pw-link -l`.
> And measure *differentially*: record the output monitor and the returning
> input as two channels of one stream, so the software start offset cancels
> instead of swamping the figure.

### 🟢 NAM was invisible to the check meant to catch its absence (2026-10-26)

Found while writing up the session above, not by it failing: **the one
component no package provides was the one component `sushi-rig plugins check`
did not check.**

- `plugin-manifest.txt` listed 54 URIs and NAM's was not among them.
- `config_uris()` walked `config["tracks"][*]["plugins"][*]["uri"]`, and the
  amp lives in the top-level `_amp` section, which records `"plugin": "nam"`
  and carries no `uri` key at all.

So NAM fell through both halves of the "needed" definition. Deleting
`plugins/neural_amp_modeler.lv2` left `check` reporting *"54 required plugin
URI(s) all resolve"* and exiting 0 — the silent portability break the whole
`plugins/` layout exists to prevent, in the single place it would hurt most,
since NAM is the one bundle `plugins assemble` can never rebuild.

Fixed in three parts:

1. NAM's URI added to `plugin-manifest.txt` (now 55).
2. `amp.PLUGIN_URIS` maps the short name a config records to the real URI, and
   `config_uris()` resolves `_amp` through it. The amp belongs there because
   the supervisor hands Carla the *same* `LV2_PATH` as everything else — if NAM
   is not in `plugins/`, the rig has no amp. An amp plugin the tool has no URI
   for raises rather than being skipped, since quietly not requiring an amp the
   config asks for is the very failure this widening prevents.
3. `ci/Dockerfile` waives the URI by name, because no distro packages NAM and
   `assemble` fails on a URI with no provider. **The waiver is the point, not a
   workaround**: `--waive` reports on every run, so "NAM is the thing you carry
   by hand" became something the build says out loud. The image has no amp —
   which was already true, and is now stated.

Verified both ways against a symlink tree with the bundle withheld (`plugins/`
itself untouched): without the waiver it exits 1 naming the URI, with it exits
0 and prints `waived, not provided: ...`.

### 🟢 The tuner clocked the rig off a webcam (2026-10-26)

`fmit` reaches PipeWire through the ALSA compatibility layer, which
auto-connects a capture stream to the **default source** — on this machine a
Logitech BRIO webcam. The patchbay also wires `fmit` to the interface, so the
tuner bridged the two devices into a single driver group: `pw-dump` showed the
Audient and Sushi both carrying `node.driver-id = 63`, the webcam. The audio
path was resampled to a webcam's clock and inherited its xruns.

Fixed by giving `fmit` `PIPEWIRE_PROPS='{ node.autoconnect = false }'`, which
puts its routing where every other connection in this rig already lives — the
patchbay. Verified: the Audient is now the driver (`sushi-rig top` says
`[clocked by alsa_input.usb-Audient...]`), the webcam has no `driver-id` at
all, and `fmit` joins the rig's group only once the patchbay wires it.

**Two consequences worth knowing.**

An unconnected `fmit` negotiates the single `input_MONO` port it actually
wants, instead of the stereo pair the default source imposed on it. So the
saved patchbay's `ALSA Capture [fmit]:input_FL` / `input_FR` entries no longer
match anything and the tuner comes up unwired.

That failure is silent — a tuner showing nothing looks exactly like a tuner
that cannot hear the guitar — so `_supervise` now checks it. The supervisor
asks `pw-link -l -i` which of the tuner's input ports have no source (after a
bounded settle, since qpwgraph wires as nodes appear) and warns by name:

```
warning: the tuner is running but nothing is connected to
alsa_capture.fmit:input_MONO — wire the guitar's input to it in qpwgraph and
save .../Patchbay/rig.qpwgraph, or the tuner will show nothing
```

### 🟠 qpwgraph saves to the *derived* patchbay, not the repository's

Found while writing the re-wiring instructions above, and a trap for anyone
editing routing with the amp running.

With an amp configured, `up` launches `qpwgraph -a
/run/user/1000/sushi-rig/rig-amp.qpwgraph`, the amp patchbay derived at launch
(see `amp.amp_patchbay`). So **qpwgraph's Save button writes to `/run`** — a
file regenerated from `Patchbay/rig.qpwgraph` on every launch and wiped on
reboot. A change made the obvious way looks saved, survives until the next
`restart`, and then silently reverts.

Not fixed. Three ways round it today, none obvious from the UI:

- `sushi-rig up --no-amp`, which loads `Patchbay/rig.qpwgraph` directly, then
  wire, save, and bring the amp back;
- **Save As** onto `Patchbay/rig.qpwgraph`;
- edit the XML by hand with the rig down.

Worth a real fix: either derive the amp patchbay somewhere that saves back, or
have `up` notice the derived file changed and offer to fold it in. Related to
the hand-wiring being uncommitted and unreproducible more generally.

### 🟢 `sushi-rig top` could never show the quantum it is built to explain (2026-10-26)

On a real rig `top` printed `quantum 0 @ 0 Hz` and no block time. `pw-top`
states the quantum and rate on the *driver* row only; every follower reads 0,
and Sushi is always a follower. `format_sample` read them off the rig's own
first node, so the one display built to explain latency could never show the
number that causes it — during the investigation above it reported `0 @ 0 Hz`
while the rig sat at 42.7 ms per block.

It went unnoticed because the test fixture gave every row a quantum, including
the followers. A fixture that was *tidier than reality*, encoding a guess
rather than a discovered fact. It now matches real output, and the parser
associates each node with the driver it runs under.

`top` also names the driving device now — `[clocked by <node>]`. The wrong
driver has no symptom other than latency, and nothing in the tool said so; with
this line the webcam above would have been obvious at a glance.


### 🟢 `panel.py` defaulted every fader to 0.5 normalised — real clipping on real hardware

Found by testing with an actual guitar and audio interface, not in review.
Touching almost any fader on the compressor, chorus or EQ tabs caused a sudden,
dramatic gain increase into clipping — including faders with no obvious
connection to level, because the parameters affected were graphic EQ band
gains, not anything obviously labelled "output".

Root cause, confirmed with real numbers: many LSP parameters described as
"gain" are **linear amplitude multipliers with wide domains** — every graphic
EQ band gain has domain `[~0.016, ~63]`; `Input gain`, `Output gain` and
`Makeup gain` on the compressor and chorus have domain `[0, 1000]`. Their real
default sits near the *bottom* of that range (`~1.0`, i.e. unity gain). The
panel generator hardcoded every fader's starting value to `0.5` normalised —
on a `[0, 1000]` domain that's real-world `500`: **500x amplification**,
instantly clipping, the moment that fader's value was touched or sent.

Fixed by sourcing each fader's starting value from `live.get_live_parameter_info`
— the parameter's actual current value in the running Sushi, captured over
gRPC at panel-generation time — rather than a guessed constant. Verified
against a live instance: `Input gain`/`Output gain` now default to `0.001`,
`Makeup gain` to `0.000999`, matching unity gain exactly, and confirmed
visually in real open-stage-control that these faders now sit near the bottom
of their track rather than at the dangerous midpoint. Read-only meter/
visibility parameters (which were also present as draggable-but-meaningless
faders) are now excluded from the panel entirely, using the same
`automatable` filter `capture()` already applied.

**Takeaway for the future:** anything that puts a number in front of Sushi and
lets a value be set needs to reason about the parameter's actual domain, not
just its normalised range. `0.0–1.0` is uniform; what a plugin actually does
with that range is very much not.

### 🟢 Same root cause, second symptom: those faders were still unusable to drag

Reported immediately after the fix above shipped — the *starting* values were
now safe, but "something funky" remained on the frequency-band gain faders
specifically. Same root cause as the defect above, different consequence:
Sushi normalises every port **linearly** regardless of domain (a separate
confirmed finding — it ignores LV2's `pprops:logarithmic` hint entirely), so
on a `[~0.016, ~63]` domain the entire musically useful ±12dB range occupies
the **bottom ~6% of the fader's travel**. Barely anything happens for most of
a drag, then a small movement near the bottom swings wildly — annoying rather
than dangerous now that the starting point is safe, but still not usable for
actually dialling in a tone.

Fixed with open-stage-control's own `logScale` fader property, applied to any
fader whose real-world domain spans `10x` or more
(`LOG_SCALE_DOMAIN_THRESHOLD` in `panel.py`). Verified two ways before
trusting it: read the actual client-side implementation
(`mapToScale()` in open-stage-control's `client/index.js`, not just its docs)
to confirm `logScale` only reshapes how *drag distance* maps to the widget's
own `[0,1]` output — the value Sushi receives is unaffected, still a plain
float in `[0,1]`, interpreted exactly as before. Then dragged two otherwise-
identical faders the same pixel distance: the plain one read back `0.25`, the
`logScale` one `0.09` — confirming it genuinely redistributes resolution
toward the low end rather than just changing a visual label.

Also added, at the same time and for the same reason (letting the operator
see what they're about to send rather than only feel it): a small read-only
value readout paired with every fader, via open-stage-control's `@{widgetId}`
live-reference syntax. A bare shared `id` between the fader and a
differently-typed widget — which the docs describe as an equivalent "clone"
mechanism — was tried first and did not visibly update; `@{...}` did.
Needed `"label": false` on the readout to stop it from falling back to
showing its own (long, processor-qualified) `id` as a label, which had been
overlapping the row above it.

### 🟡 The plugin settings in `acoustic_reverb_fx.json` do nothing (chorus fixed)

Affects `config/acoustic_reverb_fx.json` only now — `acoustic_chorus_fx.json`
was fixed for real on 2026-09-13 (see below) and renamed to
`config/acoustic_chorus.json` in the process. `acoustic_reverb_fx.json` still
has the exact defect described below and needs the same treatment: author
`config/src/acoustic_reverb.yaml`, tweak by ear through a generated panel,
capture, emit.


Both plugins carry a `properties` block *inside the plugin entry*, holding
real-world values:

```json
{ "name": "compressor_mono", "type": "lv2",
  "properties": { "Attack threshold": -30.0, "Ratio": 4.0, "Knee": -6.00 } }
```

That is not a mechanism Sushi implements. Startup parameter values belong in a
**top-level `initial_state` array**, and every value must be **normalised
0.0–1.0** — `-30.0 dB` is not a value Sushi can interpret.

Proven, not inferred: replacing the block with a fabricated parameter name
(`"Totally Fake Parameter": 999999.0`) still exits **0**, while the same
fabricated name inside `initial_state` exits **7**. `initial_state` is
validated; `properties` here is silently discarded.

The parameter *names* are all correct and do exist on the plugins — only the
placement and the value domain are wrong. This is exactly the "loads cleanly,
sounds wrong" failure the whole project exists to prevent, and it is sitting in
the rig that `start-rig.sh` launches today.

**`acoustic_chorus` fixed and proven, end to end, on real hardware
(2026-09-13):** authored `config/src/acoustic_chorus.yaml` (D1); ran it via
`start-rig.sh` with a real guitar and audio interface through the panel/logScale
tooling above, tweaked by ear, and captured the result once the sound was
right (D2); emitted with that captured `initial_state`, verified it loads
(`--dump-plugins`, exit 0), and adopted the naming convention in the same
pass — written as `config/acoustic_chorus.json` (dropping the dead `_fx`
suffix), `start-rig.sh` updated to load it, the old broken file removed
(nothing in it ever worked, so nothing was lost), and a `v1.0` snapshot saved
to `config/archive/acoustic_chorus/v1.0.json`.

**`acoustic_reverb_fx.json` still needs the same treatment** — tracked as
[#1](https://github.com/SonnyDewfall/Sushi/issues/1). Values can be computed
directly from each port's domain (`probe`, or `get_parameter_value_in_domain`
live) without the caution the brief urged around logarithmic ports, since
Sushi's linear normalisation is now confirmed regardless of that hint.

### 🟠 `start-rig.sh` does not put the bundled plugins on `LV2_PATH`

The script sets `LV2_PATH="$HOME/Sushi/plugins:…"`, but `LV2_PATH` entries must
be directories *containing* `.lv2` bundles. Only `plugins/lsp-plugins.lv2` sits
at that level. The 262 bundles in `plugins/lv2/` are never seen — lilv treats
`plugins/lv2` as a single malformed bundle and logs
`Error reading …/plugins/lv2/manifest.ttl` on every launch.

The rig works anyway, because the fallback `/usr/lib/lv2` is also on the path —
which means the "portable plugins directory" is not actually supplying anything
except the newer LSP build. Portability here is currently illusory — fix
tracked as [#6](https://github.com/SonnyDewfall/Sushi/issues/6).

### 🟠 Two LSP versions are installed simultaneously

`plugins/lsp-plugins.lv2` is v0.26; `plugins/lv2/lsp-plugins.lv2` and
`/usr/lib/lv2/lsp-plugins.lv2` are v0.22. lilv resolves this by silently
preferring 0.26. Which build a config resolves against therefore depends on
`LV2_PATH` ordering — precisely the version drift the catalogue diff in
[#6](https://github.com/SonnyDewfall/Sushi/issues/6) is meant to catch.

Also note `plugins/lv2/` is a byte-identical copy of `/usr/lib/lv2/` (262 of
262 bundles, no extras either way), so it is ~358 MB of pure redundancy that is
shadowed on the current path anyway.

### 🟡 Sushi requires `LV2_PATH` to be set explicitly

With `LV2_PATH` unset, Sushi loads no LV2 plugins at all and exits 4
(`Failed to load tracks from the Json config file`) — even though `lv2ls` finds
648 plugins using lilv's built-in defaults. Running `./sushi -c <config>` by
hand, outside `start-rig.sh`, fails for this reason and the error does not
mention `LV2_PATH`.

### 🟢 qpwgraph's silent exit: two causes, not the one assumed (issue #14)

qpwgraph fails to start by exiting with status 2 within a second, writing
nothing — no error, no crash, an empty log. The patchbay then never
auto-connects, so Sushi runs with no audio in or out while looking perfectly
healthy.

The standing explanation was that the ~50% rate was simply how often a qpwgraph
happened to be running already, since it is single-instance. That was
incomplete, and its fix — kill, then wait for the process to disappear — could
not work on its own. Two causes were found by bisecting against the real thing:

1. **The lock socket survives SIGKILL.** qpwgraph is single-instance via
   `/tmp/qpwgraph:<user>@<host>`. Kill one and the file stays; the next instance
   exits immediately. Waiting for the *process* to go does nothing about the
   *socket*. Proved directly: SIGKILL, relaunch → dies; delete the socket first
   → the identical command survives. It also explains what the old theory could
   not — why retrying never helped, and why the failure rate tracked how the
   previous instance had exited, since a clean quit removes the socket and a
   kill does not.

2. **The desktop session manager.** qpwgraph registers over ICE, and from a
   detached session (which the supervisor runs in) that registration makes Qt
   quit silently. Isolated by A/B: same command, same environment, new session →
   dies; `SESSION_MANAGER` removed from that same new session → lives.

Both are fixed at source in `tool/src/sushi_rig/rig.py`. Neither accounts for
all of it: with both applied and no socket present it still failed
intermittently, tracking nothing the supervisor controls. Rather than
reverse-engineer someone else's GUI app further, `start_qpwgraph` verifies the
process is still alive after launching and retries up to three times, clearing
the socket before each. Five consecutive up/down cycles then connected the
patchbay every time with no warning. Issue #14 stays open: the underlying
flakiness is upstream's, and this makes it either work or say so loudly.

### 🟢 The shell scripts were replaced by a supervisor

`start-rig.sh`, `start-rig-and-panel.sh` and `stop-rig.sh` are gone, replaced by
`sushi-rig up` / `down` / `status`. Every lifecycle failure logged here was the
same underlying bug: identifying processes by *pattern* rather than by
*identity*. `killall sushi` missed `sushi.bin`; `pkill -f "sushi-rig listen"`
matched the command line of the shell invoking it and killed its own caller;
`stop-rig.sh` had no pattern for open-stage-control and routinely left it and
its Electron helpers running.

The supervisor puts every child in one process group and signals that group by
the id recorded when it was created, so a process is reachable regardless of
what it renames itself to or whether anyone remembered a pattern for it. The
`$HOME/Sushi` hard-coding and the `LV2_PATH` defects below also go with it: the
checkout root is now resolved from the package's own location.

### 🟢 `stop-rig.sh` never actually stopped Sushi

Reported by the user (2026-09-13): "doesn't seem to work". `killall` matches a
process's short `comm` name, but Sushi runs as an AppImage — the process
actually doing the work is `sushi.bin`, re-exec'd from a randomly-named mount
(`/tmp/.mount_sushiXXXXXX/usr/bin/sushi.bin`), not `sushi`, which is just the
wrapper. `killall -INT sushi` (and the force-kill `killall -9 sushi` after it)
therefore never touched the real process, leaving it running and holding the
JACK ports open — exactly the AppImage process-naming trap this session hit
repeatedly while testing (`pgrep`/`killall` against `sushi` matching nothing,
`pgrep -f "sushi.bin"` matching correctly). Fixed by switching those two lines
to `pkill -f sushi.bin`, which matches on the full command line rather than
the truncated comm name. Verified directly: launched a real instance, ran the
new kill command, confirmed via `ps` that `sushi.bin` was gone.

---

## Decisions

**Plugin binaries are not tracked in git.** `plugins/` is an exact copy of
`/usr/lib/lv2` (262 of 262 bundles). Tracking it meant ~358 MB of third-party
GPL binaries in a public repo, reproducible from a package manager in seconds.
Configs address plugins by URI, so `plugin-manifest.txt` is the artefact
worth versioning. *(2026-09-13 — purged from history; repo went from 141 MB to
under 1 MB.)*

**One monorepo, two zones — not two repos.** The rig (personal, machine-specific
configs and scripts, at the root) and the tool (reusable config-generating
software, under `tool/`) are separate concerns but share one workflow, so they
share one repo while this is a personal project. If the tool needs to be
operationalised independently later, it can be split out then. *(2026-09-13)*

**Open Stage Control, not a bespoke web GUI.** Generated OSC fader panels close
the loop with far less code and match the brief. A custom UI — and any
patchbay-style graph view — is deferred until the workflow is proven.
*(2026-09-13)*

**Sushi is the authoring host, not just the deployment target.** Rejected
converting from a desktop host session format; see README for the three
compounding reasons. *(Pre-dates this repo — from the implementation brief.)*

**Two personas frame the backlog: rig builder and rig user.** Rig builder
models sound and generates configs (everything built so far serves this one).
Rig user runs the headless rig and switches between configs at runtime —
defining this persona surfaced a real gap: nothing in the backlog covered
runtime config-switching itself, only building and deploying configs. Added
as [#11](https://github.com/SonnyDewfall/Sushi/issues/11). *(2026-09-13)*

**`selftest` ships its decisions before its capture.** What counts as silent, as
linear, as the wrong buffer size, and the harmonic measurement all of that rests
on, are pure and tested against signals whose answer is known by construction —
plus one functional test that renders a passthrough through a real Sushi and
asserts it reads as linear, which is the amp failure's exact shape. Injecting
into the live PipeWire graph and capturing back out of it is deliberately a
second piece of work: it can only be verified on the rig machine, and this
project has shipped enough "I have not run this" to know how that ends. Two of
the three thresholds come from the 2026-10-26 figures above (1 % THD against
NAM's 4.06 %, a one-to-two-block window against 257-at-256 and 2049-at-2048);
the third,
`MAX_NOISE_PERCENT`, has nothing measured behind it and says so where it is
defined. [#49](https://github.com/SonnyDewfall/Sushi/issues/49) also assumed
`signals.py` and `analysis.py` between them already had everything needed —
they did not, because neither had any harmonic measurement at all, which is
most of what this adds. *(2026-10-30)*

**`doctor` reads the request; `selftest` measures the result.** Issue #48 asks
`doctor` for "the quantum the rig will actually get". A `pw-dump` cannot give
that: the running quantum lives in PipeWire's settings metadata, and what the
node carries is `node.force-quantum` — the value Sushi *asked* for. So the two
commands split along that line, which turns out to be the useful one anyway.
`doctor` checks that the rig asked for the right thing and that nothing
overrode it, which is a setup question with a remedy; `selftest` measures the
delay that actually comes out, which is a sound question with a number. Each
says which it is, because reporting a request as though it were a result is the
near-enough this project keeps paying for. The same split sends the dead amp to
`selftest`: a pw-dump does not carry the config, so nothing in `doctor` can
know whether an amp *ought* to be running, while "is anything being modelled"
is measurable. *(2026-10-30)*

**Two nodes in that capture carry `node.driver = True`.** Worth recording
because it is a trap with a plausible wrong answer. "Which node is clocking the
rig" looks like a search for the node flagged as a driver, and in
`tests/fixtures/pw-dump-rig.json` — a healthy rig — both the Audient and the
Logitech webcam carry that flag, because each drives its own group. A check
written that way finds two and has nothing to choose between them. The rig's
clock is whatever Sushi's own `node.driver-id` points at, followed from Sushi
outwards, and the webcam being a driver in the healthy capture is now a test.
*(2026-10-30)*

**A take carries its offset rather than compensating for it.** #55 left the
choice open. Compensating means writing silence ahead of a stem, which needs the
offset before the first sample is recorded — and before a take the only number
available is an assumption (one quantum) or an earlier measurement. Carried, the
offset is measured from the take itself, after it, and written beside audio
nothing has touched; if it is ever wrong, the number in `take.yaml` is corrected
instead of every stem. The number is the truth and the sentence above it is for
reading: `take check` recomputes the sentence from the number and flags the old
one as stale, because the first version printed the stored sentence and would
have had someone slide a corrected stem by the old amount. The cost is that stems do not line up by themselves in a
DAW, so the file states the fix in milliseconds near its top. Compensation can
be added at finish time later if that friction matters: the number is already
there. *(2026-10-30)*

**Today's routing cannot record separate processed stems.** Found while
designing #55. `guitar_vocal.json` sends both tracks to engine bus 0 — that is
how Sushi makes the monitor mix — so no port anywhere carries the processed
guitar or the processed vocal on its own. A "processed guitar" tap would hold
the vocal too, which #57 rules out. The fix is routing, not recording: one
engine output bus per track, with the monitor mix made by the patchbay instead,
since PipeWire sums several links into one input port. That touches
`patchbay generate` and the machine file's `monitor-out`, so it belongs to #57's
design rather than to the take folder. In the meantime `take.start_take` refuses
two stems captured from one port, so the routing that ships cannot produce a
take whose file names lie about what is in them. The dry taps are unaffected —
they come straight off the interface. *(2026-10-30)*
