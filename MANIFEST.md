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

## 1. 🟢 Shared understanding — README and manifest

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

## 2. 🟢 Modelling → config workflow

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

A working single-file prototype of all these existed at
`tool/prototype_reference.py`, as reference for data shapes and emit logic rather
than the target structure; it was split into `tool/src/sushi_rig/` with
`pyproject.toml` and `tool/tests/`. *(Deleted 2026-10-31: the package superseded
it and nothing imported it.)*

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
      until the package superseded it; deleted 2026-10-31, imported by nothing.
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
`tool/examples/dump.example.json`. The smoke test was once a shell loop over
`config/*.json` run with an `LV2_PATH` that included `/usr/lib/lv2` and
`plugins/lv2`. That path is gone, deliberately (it hid the rig not being
self-contained; see the README's *Plugins and portability*). Use
`sushi-rig verify --all` instead: it and the supervisor (`sushi-rig up`) set
`LV2_PATH` to `plugins/` alone (`rig.py`, `verify.py`).

---

## 4. 🟢 Config library structure and versioning

- [x] Physically separated the kinds of file that were mixed in `config/`:
      patchbay routing → `Patchbay/`, plugin inventory → `plugin-manifest.txt`,
      leaving `config/` holding only Sushi JSON configs.
- [x] Naming and versioning convention agreed (below). Applied for real to
      the first config in item 2 phase D (2026-09-13): `acoustic_chorus`, all
      three layers — source (`config/src/acoustic_chorus.yaml`), live config
      (`config/acoustic_chorus.json`, `_meta` populated), and an archived
      `v1.0` snapshot.
- [x] Remaining three configs (`acoustic_reverb_fx.json` as part of
      [#1](https://github.com/SonnyDewfall/Sushi/issues/1); `empty.json`,
      `fx.json` as [#5](https://github.com/SonnyDewfall/Sushi/issues/5))
      still used the old names. *Moot:* all three are in `config/retired/` now,
      so there is nothing left to rename.

### Naming

> **Followed loosely and inconsistently, and dropped (2026-10-31).** Some names
> fit the stem (`acoustic_chorus`, `electric_board`, `guitar_vocal`), but many
> do not (`electric-clean`, `hard-feelings`, `accustomed`, ...), the `test_`
> prefix is unused, and nothing enforces any of it. Config names are free-form.
> The rest of this section records what was once intended.

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

> ⚠️ This breaks the prototype (`prototype_reference.py`, since deleted
> 2026-10-31). `sushi_rig.py:dump_plugins()` handles *leading*
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

Not fixed. *(Updated 2026-10-31: `Patchbay/rig.qpwgraph` is now generated, so
the remedies first listed here — `up --no-amp` then wire and save, **Save As**
onto `Patchbay/rig.qpwgraph`, editing the XML by hand — all write the committed
file directly, and `test_the_committed_patchbay_is_what_generate_produces` fails
on any of them.)* The real remedy: change the routing at its source, in
`machines/<hostname>.yaml` (and the rig file's roles), then regenerate:

```bash
sushi-rig patchbay generate config/src/guitar_vocal.yaml -o Patchbay/rig.qpwgraph
```

(`--machine` defaults to `machines/<this hostname>.yaml`, so the committed
patchbay is regenerated only on the rig machine, `sonny-dewfall-XPS-9320`;
anywhere else the output differs and the byte-for-byte test fails.) qpwgraph is for
looking at a wiring, or trying one out; what survives is what the machine file
says.

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

### 🟢 The plugin settings in `acoustic_reverb_fx.json` do nothing (chorus fixed)

> **Moot (2026-10-31).** `acoustic_reverb_fx.json` is in `config/retired/`: it
> depended on the LSP suite, which left `LV2_PATH`, and the rig was rebuilt on
> `electric_board`. Nothing launches the file, so the fix below is never needed.

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

### 🟢 `start-rig.sh` does not put the bundled plugins on `LV2_PATH`

> **Resolved.** `start-rig.sh` was replaced by the supervisor, and `plugins/` is
> now flat (bundles at the top level, the rest in `plugins/archive/`) with
> `LV2_PATH` set to `plugins/` alone. See the README's *Plugins and portability*.

The script sets `LV2_PATH="$HOME/Sushi/plugins:…"`, but `LV2_PATH` entries must
be directories *containing* `.lv2` bundles. Only `plugins/lsp-plugins.lv2` sits
at that level. The 262 bundles in `plugins/lv2/` are never seen — lilv treats
`plugins/lv2` as a single malformed bundle and logs
`Error reading …/plugins/lv2/manifest.ttl` on every launch.

The rig works anyway, because the fallback `/usr/lib/lv2` is also on the path —
which means the "portable plugins directory" is not actually supplying anything
except the newer LSP build. Portability here is currently illusory — fix
tracked as [#6](https://github.com/SonnyDewfall/Sushi/issues/6).

### 🟢 Two LSP versions are installed simultaneously

> **Moot.** The LSP suite was moved to `plugins/archive/` and is off `LV2_PATH`
> (see `config/retired/README.md`), and `/usr/lib/lv2` is no longer on the path,
> so there is no longer a choice of version to drift.

`plugins/lsp-plugins.lv2` is v0.26; `plugins/lv2/lsp-plugins.lv2` and
`/usr/lib/lv2/lsp-plugins.lv2` are v0.22. lilv resolves this by silently
preferring 0.26. Which build a config resolves against therefore depends on
`LV2_PATH` ordering — precisely the version drift the catalogue diff in
[#6](https://github.com/SonnyDewfall/Sushi/issues/6) is meant to catch.

Also note `plugins/lv2/` is a byte-identical copy of `/usr/lib/lv2/` (262 of
262 bundles, no extras either way), so it is ~358 MB of pure redundancy that is
shadowed on the current path anyway.

### 🟢 Sushi requires `LV2_PATH` to be set explicitly

> **Resolved in tooling.** `sushi-rig up` and `sushi-rig verify` set `LV2_PATH`
> themselves (`rig.py`, `verify.py`). Running `./sushi` by hand still needs it.

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
they come straight off the interface. *(2026-10-30)* *Superseded 2026-10-31: the
monitor mix is made in Sushi, not by the patchbay; see "The monitor mix is made
in Sushi" below.*

### 🟢 Sushi's offline frontend: one stereo bus, and a stack overflow past it (2026-10-30)

Found calibrating #40's references in CI, then settled from Sushi 1.3.0's own
source (`src/audio_frontends/offline_frontend.{h,cpp}`), not inferred:

- **The offline engine has exactly two channels**, fixed at compile time:
  `set_audio_channels(OFFLINE_FRONTEND_CHANNELS, ...)` with
  `OFFLINE_FRONTEND_CHANNELS = 2`. The dummy frontend `verify` uses has ten. So
  a config with a track on engine bus 1 loads under `verify` and is refused
  offline with "Failed to load tracks from the Json config file" — the same
  message an unloadable plugin gives. `guitar_vocal`, whose vocal track is on
  bus 1, cannot be rendered offline by any stimulus.
- **A file wider than two channels overflows Sushi's stack.** It reads
  `AUDIO_CHUNK_SIZE` frames into `float file_buffer[OFFLINE_FRONTEND_CHANNELS *
  AUDIO_CHUNK_SIZE]` and checks only for mono, so a four-channel file writes
  256 floats into 128. CI saw `*** stack smashing detected ***` from a
  four-channel stimulus on `electric_board`, which uses bus 0 alone. Sushi does
  not refuse such a file, so `render_offline` does.

Isolated by rendering two configs at two channel counts in one CI step, because
the obvious reading of the first failure — that a four-channel stimulus would
reach bus 1 — was wrong twice over.

**What it cost, and how it was closed.** At first `guitar_vocal` could not be
null-tested at all, and was reported as NOT RENDERABLE OFFLINE on every run. A
config the offline frontend cannot host is now checked one channel at a time:
each track rendered alone through a copy of the config holding only that track,
moved to bus 0, with its own `initial_state` and MIDI entries and those of any
`pre_track` or `post_track`. Each channel has its own reference,
`config/sound/<config>.<channel>.yaml`. It works for any number of channels,
which matters because rigs will be set up with one, two or more from the UI,
and a failure says *which* chain moved.

A config that *can* be rendered whole still is, under its own name. The first
version split every multi-track config, and review caught what that cost: the
whole is the stronger check, because it fingerprints the mix, and #18's one
input through two parallel chains is only audible as a blend. So the split is
the fallback, and what it does not cover — the summing between channels — is
routing, and `verify`'s to check.

Adding a channel used to mean following a hint to recalibrate the whole
config, which would also have accepted any accidental change in the other
channels as their new sound. `--calibrate --missing` calibrates only what has
no reference.

A channel that needs more than two engine channels on its own is still
unrenderable and reported as such. One that *had* a reference and becomes
unrenderable fails, so a routing mistake cannot quietly drop protection, and
with `--all` a reference nothing produces any more fails as orphaned.

**Not reported upstream**, by decision on 2026-10-30: the stack overflow is
noted here and left for now.

### 🟡 Three configs are one tone under three names (2026-10-30)

`electric-chorus-2`, `electric-chorus-hall` and `hard-feelings` are identical
apart from `_meta`: the same tracks, parameters and amp. Their sound references
are identical to four decimals, which is the null test working rather than
failing. Whether that is intended is for whoever saved them.

### 🟢 One calibration understates a config's spread (2026-10-30)

Eight configs rendered bit-identically, to the 0.0001 dB a reference is written
at, in all three CI calibrations. Two did not: `electric_board` differed from
itself by 0.0025, 0.0007 and 0.0001 dB in a band across the three, and
`electric-octave-up` by 0.0005, 0.0001 and 0.0001. A tolerance taken from one
calibration — which `--calibrate` does — would have been 0.0001 for both and
failed at random.

Nor did three calibrations settle it. Their references first carried four times
the largest spread those three had shown — and the very next CI run measured
`electric-octave-up`'s peak 0.0021 dB from its reference, five times the
tolerance. The peak is a single sample, the most sensitive figure in a
fingerprint, and the first three runs had all shown it steady. So both now carry
0.01 dB for band and level, set by hand and saying so in the file: four times
the worst difference either showed in four calibrations, rather than a fit to a
spread that had already been overtaken once. That is the visible judgement
`floor_between` leaves to whoever commits a reference.

Not explained: the other configs carrying the same octave plugins are
deterministic, so the octave pair alone is not the cause.

**The recorder's core is built before its engine.** #53's ring buffer, writer
thread and files are tested here by feeding them a synthetic source — faster
than real time, with the writer stalled until the ring overflows, with the disk
failing mid-take — which a rig machine cannot do on demand. What is left for
the rig machine is the engine that takes audio from PipeWire and calls `push`,
and with it the biggest open question in Phase 2: whether a Python callback
keeps up at a 256 quantum. If not, the engine becomes a native helper and
nothing else changes. Three decisions, each with its reason in the code:
float32, because a recorder cannot stop mid-take to refuse a sample past full
scale; one ring for every tap, so a dropout costs every stem the same frames
and they stay aligned; and lost frames not replaced with silence, which would
need both threads to agree on positions while running, so `take.yaml` records
where audio was lost instead. Two seconds of headroom is chosen, not measured.

Found by its own test: the streaming writer's header was never valid on disk.
Python's buffer held the last size field back until the next seek, so the file
always had a data size one block stale — and a killed process would have lost
it outright. It flushes after every block now. *(2026-10-31)*

**Record and stop are two buttons, decided by the listener.** #57's record bar
follows the save bar's two hardware findings from the start — the name is its
own message sent as you type, and the buttons carry no payload — and adds a
third rule: the panel holds no idea of its own about whether a take is running.
A toggle would, and a panel reloaded mid-take, or a second one open on a phone,
would then show the opposite of the truth. RECORD while recording and STOP
while stopped are refused with a sentence instead. A reload empties the name
fields, so the listener forgets the names it held — the save bar's too, which
had the same gap — and tells the reloaded panel if a take is still running.
Recording again under a name archives the earlier take, as #55 asks, and says
so on the panel. A second panel's name field is its own; the last one typed
in wins. The bar is off by default
until there is a capture engine, and a listener without one answers RECORD by
saying so rather than ignoring it. Offsets are recorded as not measured: whether
a dry tap can be measured against what came out of an amp is a question for the
rig machine, not a number to write in beforehand. Not yet seen in real
open-stage-control: whether a second fixed bar stacks under the first. *(2026-10-31)*

**selftest's probes are planned from the rig, not written down.** #49 part 2's
decisions — which ports each probe links — come from the rig's own routing,
read by the same function the patchbay generator uses, so the two cannot
disagree on a port. One probe per output half, so a chain silent on one side
fails there; the guitar's own port reaching only one of Sushi's inputs is the
patchbay's to catch, since a probe drives both inputs itself. With the amp on, a second probe from
`NAM:Input` through Sushi catches the amp working and its output never
arriving. Probes add links and never remove them: a selftest killed between
unlinking the guitar and restoring it would leave a rig with no guitar, which is
worse than a little noise in a measurement. Three things this rests on are not
yet seen on the rig: that PipeWire sums a second link into an input, that
qpwgraph leaves links not in its file alone, and that Carla names the amp's
ports `NAM:Input` and `NAM:Output` live. *(2026-10-31)*

**The monitor mix is made in Sushi, and its send and return cost nothing if
the master track comes last.** *Supersedes the 2026-10-30 entry above ("Today's
routing cannot record separate processed stems"), which proposed making the
monitor mix in the patchbay.* Decided by Sonny on 2026-10-31: each channel keeps
its own output so stems exist, and sends a copy to a master track that feeds the
monitors and carries #29's limiter. What that puts in the path you hear was
measured before anything was built on it (`test_send_return.py`, real Sushi
1.3.0, offline, comparing whole stereo renders sample for sample): through a
master track listed *after* the channel, a 0 dB send is the channel exactly —
no delay, both sides, nothing inverted or swapped — and a channel that sends
keeps its own output untouched. A master track listed *before* the channel is
64 samples late, 1.33 ms: one of Sushi's internal chunks, not a PipeWire block.
Both are what return_plugin.cpp says: a send ahead of its return on the same
thread lands in the block about to play. So the master track goes last, and
item 6 makes that `emit`'s job, with a test, rather than this note's. Not yet seen: whether the
live JACK frontend keeps every track on one thread, as it should without
`--multicore-processing`; a send on another thread is always a chunk late. The
cost that stays is outputs: the master takes 2 of Sushi's 8, leaving room for
three stereo stems. *(2026-10-31)*

**A rig is channels and a master; the compiler allocates the rest.** #30's
first layer (`rigfile.py`, `config/rigs/`). A channel is a name, an input role,
mono or stereo, and a chain; a rig compiles to the `RigSpec` everything else
already reads, so `emit`, `verify` and the null tests gained no second path to
Sushi. Decisions, each with its reason in the module: the master on output bus
0 because it is the one bus the offline frontend has, so what is heard is what
renders; the master track last, because a master listed first is 64 samples
late; a send pinned last in its chain and the return first in the master even
when a capture saved them elsewhere, because a moved send makes the monitors
hear a different tone from the stem; a mono input routed to both halves of its
track from one engine channel, which spends one of Sushi's eight inputs instead
of the two the patchbay's duplication spends today; and unknown keys refused,
since `widht: stereo` silently giving a mono channel would be heard, not read.
The ceiling is three channels: the master and three stereo stems fill eight
outputs. Not yet done: the patchbay and probes from this layout, today's tones
converted and null-testing identically, and `up` running one. *(2026-10-31)*

**The patchbay and the probes read v2 rigs.** One loader (`rigfile.spec_of`)
gives them either kind of file as tracks. Two things changed underneath, both
with tests: a route naming one engine channel twice is one port — a mono
channel feeds both halves of its track from engine channel 0, and counted twice
it asked its one-port role for two — and the probes follow sends, so each
channel is probed through its stem and through the master, which is what the
monitors hear. Generated against this machine's file, guitar_vocal links each
input once, the master to the speakers, the tuner to the guitar, and the stems
to nothing. A stereo channel on a one-port role is refused unless the machine
file declares `mono_to_stereo`, which then duplicates the port across two
engine inputs as it does for a v1 bus. Separation is now measurable — the
stems are apart — and still unprobed. *(2026-10-31)*

**Today's tones are v2 rigs too, and held to the configs they replace.** #30's
third slice. A rig file now carries settings — a chain entry's `bypassed`,
`program`, `parameters` and `properties`, a channel's `strip`, the rig's `amp`
and provenance `meta` — because a tone is its settings as much as its chain.
`sushi-rig convert` writes one from a v1 config and refuses to write it unless
it compiles back to every plugin, in order, and every setting the config held.
A single-track config is taken to be the guitar on `guitar-in`, mono, which is
how every one here is played; anything with more tracks has to be told. The
hand-written guitar_vocal rig was checked the same way. Found by its own test:
the converter shared its dicts with the config it read, so editing the rig
edited the config and the agreement check could never fail; it copies now.
Until `up` runs v2 rigs both forms exist, and CI holds them together twice —
by settings in the fast suite and by sound in the container, each rig rendered
through its master against the reference its v1 config was calibrated to.
*(2026-10-31)*

**SAVE refuses rather than guesses which rig it is saving.** Found by the
2026-10-31 audit: `up` chose the save listener's rig.yaml from a config's
`_meta.source` and fell back to electric_board's, and only `save` and `convert`
write that field. Running guitar_vocal and pressing SAVE captured both chains,
`emit` warned for each processor that it was not in electric_board's spec and
skipped it, and the file written under the typed name was electric_board's nine
pedals with no settings, with the panel saying saved and the warnings in
listen.log. Three things changed: the source is the file of the config's own
name under config/src or config/rigs when `_meta.source` is absent, and None
when neither exists, in which case `up` says SAVE is off and the listener
answers SAVE with why; `emit` raises `EmitError` instead of warning when a
capture names processors the spec lacks, because that is a capture of a
different rig; and the listener's handlers catch everything but
KeyboardInterrupt, since a `SystemExit` from library code (elkpy missing, a
spec failing validation at SAVE time) used to end the listener and with it the
save button. *(2026-10-31)*

**`verify` now reads properties, so a send's destination is checked.** Found
by the 2026-10-31 audit: `diff_state` compared parameters, bypass and chain
order and never properties, and `live.capture` recorded none, so a compiled
rig whose send never reached its return passed `verify` with the monitors
silent. #78's description said the slow test read each send's destination
back; it proved only that the config loaded. `capture` now records each
processor's properties through elkpy's property calls, `diff_state` compares
them, and a rig that could not be asked is reported as unchecked rather than
passed over. The slow test in test_rigfile.py now checks what it said it did.
*(2026-10-31)*

**The rig agent exists (#29, first slice).** `sushi-rig agent` serves the
lifecycle and the two readings over HTTP on localhost: `/health`, `/rig`
(`status` as data), `/doctor` (its checks as data), and `POST /rig/up`, `down`,
`restart`. Two refactors fell out of it, both pure: `rig.describe_rig` is what
`status` prints and the agent serves, one answer with two readers; and the
body of the `doctor` command moved to `doctor.run`, which returns the checks
the command prints. The lifecycle commands run as the CLI in a child process
rather than in the agent: `up` forks the supervisor, and a fork from a
multi-threaded process (an event loop plus its executor) inherits whatever
locks another thread held, which Python itself warns about. The honest
consequence is that the supervisor is still the one `up` detaches, not the
agent; the agent becoming the supervisor and a service is the next slice. One
lifecycle command at a time, `409 busy` otherwise; a command the CLI refuses
is `409` with its words, and one the agent gave up waiting for is `504` with
the rig as it stands, since killing `up` does not stop the supervisor it
detached; every request argument is checked before anything runs (a config
name and an amp model are file names, never paths); the real agent was run against this container
(no rig, no PipeWire) and every route answered as the tests say. *(2026-10-31)*


**A take carries the v2 rig it was compiled from and the library commit.**
config.json is what ran; rig.yaml, copied in beside it when the config came
from a v2 rig, is what was meant, and `library` in take.yaml is the commit
the checkout (the library, until #27) was at and whether it was dirty. Written
as null when there is nothing to say, so a take from before these fields and a
take from a v1 config read the same. `take check` finds a rig file that went
missing or is not a rig; it does not compile it, since that would make the
check need the plugins. Nothing builds a `TakePlan` yet outside the tests
(the record bar waits on the capture engine), so these are fields on the plan
for the engine's caller to fill from `resolve_rig_yaml` and `library_state`.
*(2026-10-31)*

**`verify --quick` names a mistyped property on a send or return.** Follow-up
from #88's review: `check_names` covered parameters only, and Sushi refuses a
config whose `initial_state` names a property the plugin lacks with exit 7
and no name. `--dump-plugins` lists parameters and never properties (checked
against the captured dump), so an LV2 plugin's properties can only be checked
live, which `diff_state` does. The internal plugins the compiler writes are
different: a send accepts `destination_name` and a return accepts nothing,
from Sushi 1.3.0's source (`src/plugins/send_plugin.cpp` registers the one,
`return_plugin.cpp` none), so those are checked offline with the usual near
miss, and a destination that names no return in the config is caught too,
which the live check cannot see: the rig echoes back whatever string was
set. A config `--quick` finds fault with is no longer run for the deep check,
which used to lose the names behind Sushi's exit 7. *(2026-10-31)*
from Sushi 1.3.0's source, so those are checked offline with the usual near
miss. *(2026-10-31)*
**A rig file keeps its comments when the tool rewrites it.** Phase 2 decided
that v2 rig files are written with ruamel.yaml, not PyYAML, because `save` will
rewrite a rig file's settings after a capture and `config/rigs/guitar_vocal.yaml`
is a third comment: PyYAML drops every one on a dump, and the reasons a chain is
the way it is would go with them. `rigfile.write_rig` loads the file
round-trip, updates it in place to hold the rig, and dumps it back, so an
unchanged rig is a byte-identical file; a file that does not exist is written
fresh in the layout `convert` always used. Two things found doing it: ruamel
keeps the comment lines between two entries on the entry *before* them, so a
plain delete or insert hands the next entry's comments to the wrong one
(`yamlmerge.py` moves them), and it drops a comment line above a flow-style
entry that has a trailing comment of its own even with no change at all, which
`write_rig` refuses with the comment named rather than lose. Rig files are still
read with PyYAML (YAML 1.1), so what ruamel writes is read back with it and
compared before the file is replaced; strings like `on` and floats like `1e-05`
are written so PyYAML reads them as they were. Review added the refusals: a
file whose layout ruamel cannot reproduce exactly (a hand-wrapped flow entry,
mixed indents) is not rewritten, since an update would re-lay-out lines nobody
touched; an update that leaves a comment written twice or lost, other than with
a removed entry, is not written; and ruamel.yaml is pinned to 0.19 with a test
that asserts the comment slots `yamlmerge.py` relies on. *(2026-10-31)*

**`up --rig` runs a rig of channels, with its own patchbay.** #30's last slice,
first half, unblocked by the hearing on 2026-11-01. The rig is compiled by
`emit_rig` into the runtime directory with `_meta.source` set, so SAVE's
source resolution works and nothing generated lands in `config/`. The patchbay
comes from the rig and the machine file through `patchbay_for`, which
`patchbay generate` now also uses, so the two cannot wire a channel
differently; the amp's patchbay is rewired from that one rather than the
committed file. Why not the committed patchbay: read off the files that night,
on `guitar_vocal` it would feed the guitar into input 1, the vocal channel
under v2, and wire the microphone to inputs 2/3, which the rig does not read,
with every process healthy. A rig whose channels cannot be wired (no machine
file, a port the live graph lacks) is refused rather than brought up
half-wired. Opt-in behind `--rig` while a v1 config of the same name exists;
`restart` keeps the kind and `status` names the file. Not yet: SAVE on a v2
rig, a post-start check that each channel's source reached its input, and the
agent's `rig` flag. *(2026-11-01)*

**Four small fixes from running the agent on the rig machine.** (1) `POST
/rig/up` with no `config_name` is a 400 ("config_name is required: the name of a
file in config/") instead of defaulting to electric_board, so a stray empty
request against an idle agent starts nothing. (2) A successful lifecycle command
clears `/health`'s `last_error`; before, one refused request was reported by a
healthy agent forever. (3) The agent prints "sushi-rig agent stopping" on
SIGTERM/SIGINT, with whether a lifecycle command was running, so a handled stop
can be told from a kill. (4) `PATCHBAY_SETTLE` is 10 s, not 3: fmit's ALSA
capture node took longer than 3 s to appear on a cold start, so `up` warned of a
missing tuner node that was only late. The wait already polled every 0.25 s and
returns as soon as the node is wired, so the common case is no slower.
*(2026-11-01)*

**`up` checks that every link in its patchbay was made.** Until now only the
tuner's input was checked after start; a channel whose source never reached
it came up with every process healthy and no sound, which is what the
committed patchbay would have done to a v2 guitar_vocal (read off the files
on 2026-11-01). The supervisor now reads the patchbay qpwgraph was started
with, maps each alias to the name `pw-link` lists it under (through pw-dump's
`port.alias` and `node.name`, since the two tools name a port differently),
waits the settle for the links to appear, and reports every link not made as
one loud warning naming the pairs. A graph it cannot ask is said to be
unchecked, never passed over, and nothing here can take a playing rig down.
The tuner's own check is folded into this one, since its tap is a link like
any other; a running tuner the patchbay never taps gets its own warning. A
link to a child that is not running (`--no-tuner`) is not waited for, the
alias map is re-read on every poll so a node that registers late is not
reported unmade for ever, and a `pw-link` that fails is unreadable, never
"nothing is linked". *(2026-11-01)*

**A reordered chain keeps each plugin's comments with it.** `yamlmerge` matched a
chain by name only while its order held; once two plugins swapped it fell back
to matching by position, so a comment stayed at its line and ended up beside
the other plugin (`comp  # squash first` came out on drive, reproduced by a
reviewer on 2026-11-01), which the player's SAVE would now do every time the
panel reorders a chain. A reorder is now a move: each plugin takes the comment
lines above it, what is inside it and its trailing comment, while a heading a
blank line sets apart at the top, and what follows the last entry, stay at
their positions. A plugin renamed in the same update
(or beside an insertion or a removal) is still the same plugin when its
`uri` is unique (a same name wins over a same uri). The comments directly above
a first entry that goes with the reorder go with it. It refuses, naming the
entry, a list whose comments sit in a slot it does not read. A comment after a
`|` or `>` scalar is read correctly now (ruamel.yaml stores it as the scalar's
trailing comment, which silently misattached it on remove and insert too), as is
one under a key that has a comment of its own. A blank line goes with the entry
below it. The comment count and the read-back still run after a move. *(2026-11-01)*

**SAVE on a rig of channels writes the rig file, with its comments.** The
other half of #30's last slice. `rigfile.fold_state` is the decision: the
captured order of a track's processors becomes the chain's order, each
plugin's captured bypass, program and parameters land on its entry, a program
with no parameters drops the previous program's, properties are never
written, and a capture naming a processor the rig lacks is refused as a
capture of a different rig. `save.save_rig` captures, folds, sets the amp as
the listener last sent it, and writes through `write_rig`, so the file is
updated in place and the player's comments survive; a new name is a new
file, and there is no archive because the file is in git. The listener tells
the two kinds of source apart by their `kind`, saves into `config/rigs/`,
and checks the save by compiling it beside the state file rather than into
`config/`. `up --rig` hands the rig file to the listener; a v1 run whose
source resolves to a rig of channels keeps SAVE off and says to use `--rig`,
since saving the rig would leave the v1 config behind. Not folded, and said in
the docstring: a channel's strip (held at unity) and the sends' gains, where
a channel's level in the mix lives (#18); a send gain moved live is lost at
save until the file can hold it. Found by review before merging: captured
values are rounded as `emit` rounds them, since a capture's float32 noise
would rewrite every parameter line on every save; `bypassed: false` is
written only where the file said true; a name that already belongs to
another rig file is refused rather than updated into this rig; a run without
the amp leaves the file's amp alone; and the save check compiles into a
scratch directory, not over the running rig's compiled config. The one thing
that stayed open, a per-plugin comment staying at its line when a chain was
reordered from the panel, is closed by the yamlmerge entry above: a
reordered chain now moves each entry with its comments. *(2026-11-01)*

**A channel's level in the mix is `level:` on the channel, in dB.** The #18
answer, made concrete: a strip acts after the send and changes the stem, so
the strip stays at unity and the level is the send's gain, which Sushi 1.3.0
holds normalised over -120 to +24 dB (`send_plugin.cpp`; unity is 120/144,
the 0.833333 every v1 track carries). A channel without a level compiles
exactly as before, so no existing config or null-test reference changes. SAVE
folds a send gain moved live back into the file as dB to a thousandth, below
anything audible and above a float32 capture's noise. This is what the
expression pedal's blend will drive: one CC, two channels' levels, opposite
directions (#34). *(2026-11-02)*

**A rig carries its MIDI map, compiled to Sushi's own mappings (#34).** The
machine file gains `controllers`, each control named and described as what it
sends (`cc` or `note`, the port as PipeWire lists it, the channel as Sushi
counts it or `all`); the rig's `midi:` names a controller and binds its
controls. Blend, parameter and mute compile into `cc_mappings`, verified
against 1.3.0's `json_configurator.cpp` and `midi_dispatcher.cpp`: a CC drives
a parameter as `min + (max - min) * value / 127` in its normalised domain, a
reversed range inverts, a track's `mute` is a parameter, and any string for
the channel is omni. A blend is two mappings on the two channels' sends, each
from its channel's own level down to silence, so the mix is respected. A
note cannot sweep and is refused for one; bypass and Program Change rig
switching are returned for the agent, which Sushi cannot do natively. The
spec still compiles for routing without the controller (the patchbay, the
probes, SAVE's source check need no pedals), but a config does not: a rig
running without its pedals would be silent degradation. The MPD218 is
recorded as this machine's development controller with the facts measured on
2026-11-01. Two hypotheses for the rig session: whether Sushi counts MIDI
channels from 0 (the file passes the number through unchanged), and the name
PipeWire gives Sushi's MIDI input, which the patchbay will need to link the
pedal; a slow test loads a mapped rig in the container's Sushi to settle
what `--dummy` accepts. *(2026-11-02)*

**The MIDI map's follow-ups from review (#103).** `host.midi_inputs` is checked
in `check_rig`, which `compile_rig` runs, as a whole number of 0 or more (an
empty value is none given, as it always was), so a stray string is a named
problem, from a file or a Rig built in code, and not an `int()` traceback from
the compiler. A
machine file whose controller has two controls sending the same cc, or the same
note, is refused naming both, since a rig binding both would fire two mappings
from one pedal; a cc and a note of one number are different messages and stay
allowed. The agent's bindings are `fixed["agent"]`, a key of the compiled state
beside `processors`, no longer a pseudo-processor in that map that two
functions had to pop back out; the config's `_midi_agent` key is unchanged.
The three places that resolved a rig's controller (`up --rig`, `emit`, the
listener's save check) go through one `machine.rig_controller`, which takes a
loaded machine, a path, or nothing for this host's; a loaded `MachineSpec`
remembers the file it came from (`source`) for its messages, and `prepare_rig`
loads the machine file once, passing the loaded spec on. The state
file now records the machine file a rig run was compiled against (`machine`,
as an absolute path, since the listener reads it from another directory;
`status` and the agent's `/rig` show it for a rig run), and
the listener's save check reads it back, so SAVE compiles against the file the
running rig used and not this host's default. `up` has no `--machine` flag, so
today the two are the same file; the record is what keeps them so when it gains
one. A state file from before this change has no `machine` and falls back to the
default. Not run live: the recorded path is tested through the state file and
`up`'s hand-off to the supervisor, not through a real supervisor. The other write path to the blend hazard is closed too:
`merged_state` ignores a captured `gain` on the send of a channel a blend
drives, so `emit --state` with the pedal at toe no longer brings the rig up
with a channel silent; the channel's `level` holds, and `fold_state` and
`merged_state` share one `blended_channels` derivation. An unblended channel's
captured gain still merges. *(2026-11-02)*

**The capture engine is in the tool, with a check for silence that arrives as
data.** #53's engine and #81's silent-block check, moved out of the spike into
`capture.py`. A Python JACK callback is the engine, not a stopgap: the three
rig runs of 31 Oct 2026 (`capture-*.json`) had 0 xruns and 0 lost frames at
quantum 256 idle (p99.9 862 us of 5333), 256 under 16 busy processes (293 us),
and 128 idle (889 us of 2667), and the callback was faster under load than
idle. The module registers one input port per column of a take, refuses a
graph not running at the rate and quantum the take was planned for, refuses a
source port that is not in the graph (naming ports that are), links, then reads
the links back and refuses any port fed by more than its one source, since a
second link would be summed in. The spike's counter source is now a test
fixture. The silent-block check is in the recorder, where every engine's blocks
pass: a block of exact zeros on a column that carried signal in the block
before is counted per column, and `take.yaml` carries it under
`capture.silent_blocks` with the time of the first; `take check` names the stem
and the port. That is the shape of the one thing the runs found: in the 256
idle run three of six ports delivered 256 zeros at 207.6 s, in one of two
identical 600 s runs, with every counter green; the read-back called it 257
jumps and the results file showed it was silence in place. Cause not found.
The check is evidence, not a verdict: a noise gate looks the same, and a port
that never carried signal is not counted, since "went silent" needs a before
(a port that cannot be linked is refused at start instead). It costs about 9 us
a 256 by six block, measured on a development machine, not the rig. Two things
the spike did silently are now counted: it skipped any block whose size was not
the quantum, leaving the take short with nothing saying so (blocks of another
size are now recorded and counted), and an exception in the callback would have
made JACK drop the client and end the capture (it is now held and reported).
Take format: `capture` gains `silent_blocks` (always present, `{}` when watched
and clean, so a take from before the watch differs from a clean one) and
`engine` (the engine's account: xruns, block size changes, skipped frames,
slowest callback against its budget); both additive, so the schema version
stays 1, and `Engine.stop` may return that account. JACK-Client is the
`[capture]` extra, imported only inside `load_jack`, because `import jack` fails
with `OSError` where libjack is absent and every pure part would then be
untestable; `ci.yml` installs libjack and the extra, the container image
already had libjack and now has the extra, and the pure tests run with neither
numpy nor jack importable. The one JACK test checks that the names the engine
calls exist in JACK-Client 0.5.7; the no-server path through `Client(...)` ran
for real, nothing else did. Found while reading: the 1 Nov hear-v2 results do
not mention the capture runs; they are in the 31 Oct file, and so are dated
here. **Not run: the engine against a live PipeWire.** Untested are the order
of activate and connect, the port names PipeWire gives the iD14 and Sushi
through `pw-jack`, that a stereo stem's ports are the consecutive numbers from
the one named (true of Sushi's outputs in `patchbay.py`, an assumption for
anything else), that the listener can run under `pw-jack`, and that a take's
silent blocks line up with what is heard. The listener does not construct an
engine yet, because the ports a take records follow from the machine file and
the rig. Found by review the same day, and fixed: the engine read the graph's
quantum with an activation and a half-second wait on every `start`, on what
will be the listener's one OSC thread, so every RECORD would have stalled the
panel; it is now read once per engine (`prepare`, to be called where a pause is
harmless), and a quantum that changes later shows in the take's `engine`
account instead of being refused up front. The link read-back ran the instant
after `connect`, which under pipewire-jack may be before the client sees its
own link; it now retries for up to half a second, and whether the read is
synchronous there is still unverified. A `push` that raised after the ring had
kept the block was counted as skipped frames, telling the take they were
missing from stems that hold them; a raise in the push is now `push_errors`
and claims no frames. A refused start no longer returns the previous take's
account from `stop`; the account of an engine that failed to start is now kept
under `capture.engine`. (A take that never got a recorder was first
given `silent_blocks: {}` as well; a second review pointed out that `{}` means
watched and found clean, so it now carries no key, and absent reads as "nobody
watched": a take from before the watch, or one whose recorder never started.) The JACK test module skipped on a missing
package but errored at collection on a missing libjack (`importorskip` catches
only `ImportError`); it skips for both, and CI, which has both, still runs it.
The flow of calls in `CaptureEngine` is now tested against a hand-written
stand-in for JACK, which proves its own decisions (what it calls, how often,
what it does when a read is empty) and nothing about JACK itself. A second review found nine
more, all fixed. The start sequence activated the client, and so ran the
process callback, before any link existed, pushing zeros into the recorder:
the take opened with silence, columns linked on different cycles sat a quantum
apart while the take declares no offset, and a refused start had already fed
the recorder. The pump is now gated until every link has been read back; the
gate also hides the block size's start-up transient (256, 1024, 256, plus the
one callback JACK makes on activation), which had been counted as a change
mid-take on clean takes, and xruns before the gate are not the take's. One
client does the whole start, reading the quantum after activation, where the
first take had opened a throwaway probe of the same name first (so two opens
and a possible `-01` rename); `prepare()` stays for a listener that wants the
answer early, on a client named `-probe`. `check_take` now reports blocks that
ran at another size than the take's quantum, which a client granted a size it
was not planned for (a stale cached read, no change event) would otherwise show
as clean. A failed start before the pump existed returned `{}`, which became
`engine: {}` in take.yaml; `stop()` returns None for no account, as the
protocol says, and the account counts every callback exception (`errors`),
keeping the first message. Engine messages name a column as the recorder does
(`guitar-dry.wav`, `guitar.wav[1]`) and the JACK test now checks the keyword and
positional names the engine calls with, not only that the names exist. Still
unverified: all of it against a live PipeWire, and in particular that
`client.blocksize` read after activation plus a fixed wait is settled, and
that a cached read stays true between takes. *(2026-11-02)*

**The agent's second slice: live operations (#29).** `/live` reads the running
rig (`live.read_rig`: `capture` and `get_live_parameter_info` on one connection,
so a client has each parameter's range) and changes it: `POST /live/parameter`,
`/live/bypass` and `/live/move`, with `GET /live/log`. A parameter is set by
processor and parameter name to a normalised 0 to 1 value, and nothing else
until the profiles bring real units. The decisions are pure and tested
directly: the shape of a request is refused before Sushi is contacted at all;
whether the rig has the processor and parameter, whether the parameter is
writable (a meter is not), and whether a name is a track where a pedal was meant
(a track's bypass cascades to everything on it) are decided from a targeted read
(`read_live_parameter`, `read_live_bypass`, `get_live_chains`: the tracks, one
processor's parameter listing and one value, never every parameter of the rig),
before anything is set; a move already at the end of its chain is
`changed: false` and never sent. Live calls are `409 busy` while a lifecycle
command runs, checked when the request gets its turn, but do not take its lock,
and are serialised among themselves so each logged `previous` is the value that
change replaced. A rig not up is `503`; Sushi gone, refusing or not confirming
is `502` with the reason, and **not logged**; elkpy missing is `500`. All but
the `503` are remembered in `last_error` until a live change next works. Each
confirmed change is appended to an in-memory log (`at`, `op`, `args`,
`previous`; last 500, newest last): the source undo will use, not built yet, and
lost when the agent stops. Found on the way and fixed after review:
`live._wait` turned a Sushi error or silence into a stderr warning, so the
agent answered 200 and logged a change that never happened; the setters and
`move_processor` now wait strictly and raise `LiveError` (`push` keeps the
lenient wait it was built with, since it carries on past a slow answer). And
`live._controller` called `sys.exit` when elkpy was missing, which in an executor
thread ends the event loop: it raises `ElkpyMissing` now, `cli.main` turns that
back into the same exit, and the listener reports it as it did a SystemExit.
`live.py` gained `set_live_parameter`, `set_live_bypass` and the reads above;
`capture` and `get_live_parameter_info` take an optional shared connection.
**Not run against a live Sushi:** no Sushi was available, so the elkpy calls the
new functions make (`get_parameter_value` and `get_processor_parameters` on a
chain's ids, `get_parameter_id` with `set_parameter_value`, and
`set_processor_bypass_state`) were read from elkpy 1.3.0's source, not
exercised; only the unreachable path ran for real, `capture` against a closed
port giving elkpy's `SushiUnavailableError`. Run a parameter, a bypass and a
move against `up` before relying on them. *(2026-11-02)*

**The agent's live routes, answering the second review of PR #102 (#29).** Eight
findings, behaviour changing in four places. (1) **A bound on every call to
Sushi.** elkpy's gRPC calls have no deadline, so one hung call held `_live_lock`
for ever and every later `/live` request waited behind it with no 409, 502 or
timeout. `Agent._sushi` now wraps the executor call in `asyncio.wait_for` with
`LIVE_TIMEOUT` (20 s, a guess: four times the 5 s `CONFIRM_TIMEOUT` and well
over a full read of the rig; not measured on a live Sushi); past it the lock is
released and the answer is `504` with a reason, remembered in `last_error`, and,
for a change, saying it may or may not have been applied. The stuck worker
thread cannot be cancelled and stays in its pool until Sushi answers or the
process ends; that is stated beside the constant rather than hidden, and see
the third review below for what the agent does about it. (2) **Reads no longer take the lock.** `GET /live` held `_live_lock` for
a full-rig read, so a polling panel starved fader writes; a read cannot disturb
the `previous` the lock exists to keep true, so only changes take it, and a read
keeps the busy check (409 during a lifecycle command). (3) **One connection per
request**, read and set on the same controller (the pattern `read_rig` already
used): each `SushiController` builds a dozen sub-controllers, makes an RPC and
starts a notification thread, and a request used to build two. Chosen over one
long-lived controller on the `Agent`, which would have to reconnect whenever
Sushi restarts (`up`, `down` and `restart` do that on purpose) and whose failed
reconnection would be state outliving the request that met it; reconnect
logic I could not exercise without a Sushi is not something to add on spec.
`live.py`'s reads and setters take an optional `controller=` (a context manager,
`_connection`, builds and closes one only if none is passed); the `Live` record
the agent injects now has `connect` and `disconnect` and every call takes the
connection first; a connection is not closed after a timeout (a worker is still
inside a call on it), and one that will not close is printed to stderr and
remembered, not swallowed. Using one controller from two executor threads (the
read and the set are separate hops) rests on gRPC channels and elkpy's
`run_coroutine_threadsafe` being thread-safe, as read from elkpy's source: not
run on a live Sushi. `get_live_chains` had no caller left and is gone.
(4) **A move is decided once and nothing matches text.** `live_move` read the
chains, then called `move_processor`, which walked again, planned again (its
answer could differ) and returned a sentence the route prefix-matched with
`"moved "`, so a processor found already at the end only on the second look was
a 502. Now `live.decide_move` (pure, tested) turns one walk into a `Move`
(`planned`, `unchanged`, `absent`, then `moved`), `plan_live_move` reads and
`send_live_move` sends the very command that was planned, and the route maps
the outcome: absent is 400, unchanged is `changed: false`, moved is logged. The
`503` for an unreachable Sushi is kept, because the plan is a read and the send
a change. `move_processor` and `agent.check_move`'s chain arguments are gone;
`Move.message` is the panel's line. (5) That walk is `_walk_chains`, which
`move_processor` had been duplicating by hand. (6) **The listener's move.**
Strictness made a move Sushi applied but confirmed late raise `LiveError`, which
the OSC listener reported as "move failed" without rebuilding the panel, leaving
its tab order stale with no hint. `listen.handle_move` (extracted from the
closure so it can be tested) now rebuilds the panel on a `LiveError` and says
"the move may have happened"; it does not rebuild when nothing was sent
(unchanged, absent), when elkpy is missing, or when the move failed some other
way. Chosen over a lenient move because the point of the strict wait is that
nothing says "moved" of a change Sushi did not confirm, and a rebuild reads the
truth from Sushi either way. (7) `check_bypass`'s inline list of the processors
on chains is `_on_the_rig(chains, with_tracks=False)`. (8) `_name` checks
`key not in body` before reading it. **Not run against a live Sushi:** the
timeout, the shared connection and the late-confirmed move are shown against
fakes and a stand-in controller; the only thing run for real is elkpy against a
closed port, which still gives `503` through the new connect-then-read path.
Run a parameter, a bypass, a move and a read in a loop against `up`, and a
Sushi paused with SIGSTOP, before relying on the 504.

*A third review of the same branch found six more, all in the connection and
timeout code just described.* (a) **A connection abandoned after a 504 was never
closed**, even when the stuck call later returned, so every merely slow request
leaked a controller, its notification thread and its channels. The agent now
keeps the call's future and, when it returns, closes the connection it was using
(or, where the connect itself timed out, the one it returns late), and says how
the call came back: stderr, and `last_error` (as `also` if another failure is
already there). A call that returns late without error was a change that was
applied; the message says so. (b) **`asyncio.TimeoutError` is the builtin
`TimeoutError` on 3.11+**, so a socket or gRPC deadline raised inside the call
was reported as the agent's 20 s timeout and its connection left "abandoned"
though the thread had returned. Told apart now by whether the call's future has
returned: its own error is a 502 with its type, and the connection is closed at
once. (c) **A failing close overwrote `last_error`**, hiding the real reason
for a 502 or 504 from `/health`; it is now added beside it (`also`) and only
becomes `last_error` where there was none. (d) `check_move`'s refusal names what
the rig has, like the other two, which gives `Move.chains` a reader. (e) **The
connection is made before the live lock** in the three change routes, so queued
fader writes construct their controllers while they wait and only read-and-set
is serialised; busy is checked before connecting and again under the lock, and
`previous` is unchanged. (f) **Sushi calls run in a pool of their own**
(`LIVE_WORKERS`, 8), not the loop's default executor that `/doctor` shares, which
a Sushi that never answered used up after a few 504s, after which requests
queued for a worker and were blamed on Sushi though their call never started. A
full pool is a 503 saying how many calls have not returned, and `/health` has
`agent.stuck_live_calls`. Closing a connection goes to the pool without that
count, since refusing it would leak what it was to release. The threads
themselves cannot be reclaimed (a thread inside a gRPC call with no deadline
cannot be interrupted from Python), and the interpreter waits for them at exit,
as it did before. **Still not run against a live Sushi**: the late close, the
pool and the 503 are shown against fakes, with a fake whose calls finish after
the timeout; whether elkpy's `close()` is safe once a call that was running on
it has returned is read from its source (it stops the notification loop and
joins its thread), not run.

*A fourth review found two more in the abandon bookkeeping.* A connect cancelled
in flight (the client went) was not handled like one that timed out, so the
connection it returned late was never closed: both are now. And a call that
returned in the gap between the timeout check (on the loop) and counting it as
abandoned (the done-callback that uncounts it runs in the worker thread) stayed
counted for ever, inflating `stuck_live_calls` and the count in the 503; it is
now counted first and uncounted if it has already returned. The second is shown
with a future that completes from inside the check; neither was run against a
live Sushi. *(2026-11-02)*

**A probe plays through a probe track, never beside the instrument (#81).**
*Supersedes the 2026-10-31 entry above ("selftest's probes are planned from
the rig"), which had probes add a link into the track's own input.* The rig
results of 31 Oct showed why that fails: PipeWire sums two links into one
input at unity, but Sushi 1.3.0 reads only the first 64 frames of each quantum
of a doubly-linked input, so at 256 the probe read back at a quarter of its
level, aligned and with nothing to say so, and the guitar sharing the input
would have been chopped the same way. `live_paths.py`'s own
`pipewire_sums_two_links` check reads `false` in all four committed reports for
this reason. Each probe through Sushi now drives `selftest_probe`, the track
under test with its engine numbers moved to the lowest ones no track uses and a
copy of its chain (sends and returns left off, settings read from the running
processors). For `config/rigs/guitar_vocal.yaml` that is input 2 and outputs
6/7, for `electric_board` input 1 and outputs 4/5; tests pin both, with and
without the amp, and hold every committed rig to probing no port a track uses.
Unlinking the guitar for the probe was the alternative and is rejected: qpwgraph
reconnects a missing link by itself, which would put the second link back
mid-probe without a word, and a run killed between unlink and relink leaves a
silent guitar. The copy's cost is said in the plan rather than hidden: a fault
only the running instance has, the tracks' own outputs, and the send and return
into the master (verify's, #88) are not probed, and with the master's chain
empty the monitor path is not probed twice. The amp cannot be copied — a second
Carla would pass while the running one is dead, #49's own failure — so its
probes still go into the amp the guitar plays into and carry `beside`; the "via
amp" probe links `NAM:Output` into the probe track, the shape of link seen to
survive qpwgraph on the rig. `crowded_ports` is the live check the effect runs
before playing, on both ends of the probe track — an input with a second link,
an output linked to anything but the selftest's own capture — tested against
the `pw-link -l` listing the spike captured. A
three-channel rig has no free output pair and its chains are listed as not
probed. Untested, since no PipeWire or soundcard was available: creating and
removing a track over gRPC in a running Sushi, whether a copied chain sounds as
its source does, and whether Carla reads all of a doubly-linked `NAM:Input`.
The effect itself is still unwritten; selftest.py lists what it owes the plan.
A review the same day found five things, all fixed: the live check covered the
probe track's inputs only, so an output left linked to the monitors would have
played the probe to the player (now both ends); a probe track left by a killed
run made SAVE refuse the capture as "a different rig", losing the save under
the wrong cause (`live.capture` now leaves the track out with a warning naming
it, and `selftest_probe` is a name no rig may take, so nothing real is ever
dropped); the amp-to-monitors path vanished without a word when the master's
chain was empty (now its own NOT CHECKED line); the "via amp" note blamed a lack
of free ports when the guitar track had no inputs (the two causes are now told
apart); and the probe track's routing re-parsed track entries beside
`track_route` (`patchbay.track_entries` is now the one reading of them).
A second review found four more, also fixed: a guitar with no stem whose probe
through the master had no room was still blamed on the missing stem (the amp's
note now gives the cause of the path tried last); a rig was refused over
`<send>_probe`, a name no copy is ever given, since sends and returns are not
copied; the `selftest_probe` name check existed three times, and is now one
function in spec.py, run by `RigSpec.validate` — which every path to a running
rig reaches, a v2 rig through `compile_rig` — and called by the plan for a spec
given to it unvalidated, so `check_rig` no longer has its own copy; and a probe
track's ports are worked out once when it is planned and carried on it, rather
than rebuilt from its routing at every use. The listings are unchanged.

**The agent's undo, `POST /live/undo` (#29).** The newest log entry that is not an
undo and has not been undone is put back through the calls the routes use: a
parameter to `previous`, a bypass to `previous`, a move by the opposite step. The
undo is logged as `op: "undo"` with `undoes: <index>`, `args` (`of` the op, then
what was applied) and `previous` (what it replaced), so a redo can be built from
the entry alone; the answer carries that entry, the entry `undone` and the
`state` set. **The index is a count of entries since the agent started**, not a
position, because `trim_log` drops from the front and a position would change
under the `undoes` pointing at it; the agent keeps how many it has dropped and
`GET /live/log` reports `first`. Ordinary entries keep their four keys; only
undos have `undoes`. A single pass from the newest end is enough because an undo
is always newer than its target: trimming can lose a target and keep its undo, but
never the reverse (tested with `LOG_LIMIT` lowered, which needed `_record` to read
the module's value instead of `trim_log`'s default bound at definition). **Refused
rather than guessed**, as 409 with a reason and nothing logged: nothing to undo
(empty log or only undos; answered without connecting, so no rig need be up); a
`previous` of None (Sushi would not say a bypass state); a processor the rig no
longer has; and a move whose processor is not exactly where the move left it,
checked by `chain_after_move` against the chain read now, since the panel's
listener also moves processors and is not behind the agent's lock. A refused entry
is not skipped to reach an older one. A body is 400. Same busy checks, lock,
`LIVE_TIMEOUT` and 503/502/504 mapping as the other changes. **Not done:** undoing
an undo. **Not run against a live Sushi:** the three undos are shown against the
fake `Live`, so that `set_live_parameter` takes the value back, that moving the
other way is exact on Sushi's real chain, and that the chain read for the check is
still the chain at the moment of the send (it is read under the lock, but the
listener is outside it) are unverified; try each against `up`.

*A review of the same branch found six more.* (1) **The stack could wedge.** The
newest entry being a move the panel's listener has since disturbed (it moves
processors outside the agent's lock) was refused and never skipped, and the route
took no body, so every older change was unreachable. The way out is
`{"skip": true}`, not naming an entry (a named index would let a request undo
out of order): it runs the same checks as an undo without sending, and if the
newest entry is refused records an `op: "stale"` entry with `stale: <index>` and
the reason, so the history stays honest and `pick_undo` passes over it; an entry
that can be undone is refused with `not_stale`, so a skip cannot give up on a
change that would have gone through. (2) **A parameter or bypass undo overwrote
whatever had been set meanwhile**; `check_undo_state` now refuses unless the rig
still has what the change set. The parameter comparison has a tolerance
(`PARAMETER_TOLERANCE`, 1e-4) because Sushi holds a float32 and a value read
back is not bit-equal to the one sent: a guess, not measured, and the first
thing to check on a live Sushi (too tight and every parameter undo is `stale`).
A value Sushi would not say is refused, not assumed equal. (3) **Undo no longer
duplicates the routes**: `_apply_parameter`, `_apply_bypass` and `_apply_move`
are the read-check-set of each, called by the route and by undo with an `expect`
hook (undo's staleness check, run after the existing checks and before sending)
and a `dry_run` (skip); `live_undo` dispatches on `op` once. (4) **Busy is
checked before the "nothing to undo" fast path**, which had reported an empty log
during a lifecycle command. (5) **Statuses and a `code`**: 409 `busy`,
`nothing_to_undo` and `stale` are told apart by a `code` field beside `reason`
(on the busy refusal of every live route too); a processor the rig lost is 400,
as `check_parameter` and `check_bypass` are on the other routes, where it had
been 409 here. (6) **The log limit is `Agent(log_limit=...)`** and not a module
global re-read at call time (the tests now pass it to the constructor);
`trim_log`'s default, dead since, is gone. The unreachable "not planned" branch
after `check_undo_move` is removed, replaced by a test of the fact it leaned on:
wherever a one-step move was possible, its opposite from the result is possible
and restores the chain. Everything here is shown against the fake `Live` only.
*(2026-11-02)*

**The agent serves the pedals Sushi cannot: bypass and rig switching (#34, #93).**
When the running rig's compiled config has `_midi_agent`, the agent opens the
controller's port itself and serves it (`midi_listener.py`, wired in a block at
the end of `Agent`). A pad's note on toggles its plugin's bypass through the
calls `POST /live/bypass` makes (one connection, the live lock, the same read
and check), a CC on a bypass is a switch (the effect on at 64 and above,
bypassed below, acting only when it crosses; the first value heard counts as a
crossing), a pad on a mute toggles the track's `mute` parameter, and a Program
Change selects program *n* = the *n*th rig of `config/rigs/*.yaml` sorted by
file name, brought up by `down` then `up --rig` through the same
`_run_lifecycle` a request uses (the lifecycle route was refactored onto it),
with the options `restart_arguments` reads from the state file less the amp
model override. A Program Change for the rig already playing does nothing; one
past the last rig is refused and said. `GET /rig` lists the order as `programs`
while the rig switches by them. Each change is in the live log with `source:
"midi"` and `control`; a switch is logged as op `rig`, which undo will have to
refuse or handle. The decisions are pure and tested for every shape
(`decode`, `parse_bindings`, `dispatch`, `next_bypass`, `next_mute`,
`program_order`, `find_port`, `plan_for`); aftertouch, releases and unbound
messages do nothing. The listener is a thread that opens, reads and closes the
port and hands messages to the loop; it starts with `up` (and at the agent's
start, for a rig already up) and stops before `down`, `restart` and a switch, and
a generation number drops anything it handed over that was not yet acted on.
A port that will not open, or dies, is a named problem in `/health`'s
`agent.midi` (port, controller, what ALSA lists) and is not retried until the
next `up` or `restart`; MIDI failures never reach `last_error` except a switch's
lifecycle failure, which is one. The library is **alsa-midi** (new `midi` extra,
in both CI installs, with `libasound2t64` on the runner): it reads the ALSA
sequencer the machine file's port names come from, by those names, and has
wheels for CPython 3.10 to 3.14; mido's python-rtmidi had none past 3.12 at
1.5.8. Channels are 0-based in the agent's listener, as ALSA and `aseqdump` give
them. What libasound's decoder makes of each MPD218 event is checked against
`decode` with the real library (test_midi_alsa.py); the wiring is tested with a
fake port and the fake Sushi. **Nothing has been heard from a real pedal**:
`AlsaPort` has never opened a sequencer (none in CI or here), so the port name
as the sequencer gives it, the channel the pedal sends on, a footswitch's CC
and polarity, hearing the pedal alongside PipeWire's and Sushi's own
subscriptions, and the unplug detection (system announcements) are for the rig
session. Not done here: learn mode, a diff-based switch, the patchbay link from
the controller to Sushi's MIDI input, and noticing a rig brought up from the
CLI while the agent is already running. *(2026-11-02)*

**The agent's pedals, answering review of the branch (#34, #93).** Seven
findings. (1) **Undo stopped at a rig switch.** With `/live/undo` in (#107), an
`op: "rig"` entry answered 400, and a skip then undid the previous rig's
changes against the rig now running, whose processors can share their names.
`pick_undo` treats a switch as a barrier (`UndoBarrier`): nothing before it is
undone, skip included, and the answer is 409 `nothing_to_undo` saying a rig
switch is in the way; changes after it undo as before. (2) **A failed switch
left no rig and no pedals.** The rig switched to is now checked to compile
first (`midi_listener.rig_problem`: load, machine file, controller, compile and
emit, with no pw-dump and no Sushi), and refused with the running rig untouched
and the reason in `last_error`; and if its `up` fails anyway, `_run_lifecycle`
runs a fallback, the previous rig's `up` with the same options, and says which
in the reason. (3) **`midi_release` cancels and awaits the actions first, then
stops the listener**, and `_midi_sync` does nothing once released, so a switch
cancelled mid-`up` no longer opens the port again from its finally. (4) **A CC
switch's state is committed when its action went through** (or Sushi was
already there), not when it was decided, so a dropped or failed crossing is
played again by the next value on that side. (5) **Bindings on overlapping
channels clash**: note 36 on "all" and on channel 1 are refused, since
`dispatch` would only ever serve the first. (6) **`up` stops the listener too**,
so one still following a rig that died outside the agent cannot act on the rig
coming up. (7) **The program order is read off the loop**: a Program Change is
decided in its task with the folder listed in a thread, and `/rig` lists it in
one. Each is tested; still nothing heard from a real pedal, and the compile
check cannot see what only `up` finds (the live graph, Sushi loading the rig),
which is what the fallback is for. *(2026-11-02)*

**The library and live chain edits (#32).** `GET /library`, `POST /live/add`
and `POST /live/remove` on the agent, for S3 of the UX story: pick a plugin
from the library, add it to a chain, drag it into place (`/live/move`, already
there), take one out. **The library** (`library.py`) is every URI in
`plugin-manifest.txt`, in its order, with a name, the port count, the bundle,
whether a profile exists and the profile's groups; built on first use and kept
for the agent's life; read from `plugins/` only, never the system's LV2 paths.
lilv is not on every machine the agent runs on, so the bundles' Turtle is read
directly: `parse_turtle` is enough of Turtle 1.1 for LV2 bundles, and a bundle
is read as lilv reads it for these facts (`manifest.ttl`, then the
`rdfs:seeAlso` data files of what it declares `a lv2:Plugin`; the plugin's own
`doap:name`, since a file often names its project too: gxts9's says
"Gxts9sim"). **Checked against lilv**: Ubuntu 24.04's packages for the
manifest's plugins (guitarix-lv2, mda-lv2, rubberband-lv2, x42-plugins,
dpf-plugins-lv2, swh-lv2, zynaddsubfx-lv2, ardour-lv2-plugins) were extracted to
a scratch folder, `sushi-rig plugins assemble` built a `plugins/` from them (54
of 55 URIs; the amp is not packaged, and was waived), and lilv 0.24.22, loaded
from the extracted .debs rather than installed, agreed on every name and port
count. That comparison is now a test, which runs where lilv and `plugins/` are
both present (the container) and skips here. The fixtures are the captured
`.ttl` files of three of those bundles, `mda.lv2` cut down to one data file and
saying so. A name that cannot be read falls back to a profile's restated name,
then the URI's last segment, marked (`name_from`) with a `note`. The amp is
listed `addable: false`: it runs in Carla because Sushi cannot set its model,
and on a Sushi chain it would pass audio with none. **An add** is checked for
shape and against the manifest (400, Sushi untouched), then against a walk of
the chains under the live lock (409 for a track the rig lacks, a name it
already has anywhere, an `after` not on that track, or `after` the send). With
no `name` it is named from the plugin (`derive_name`, to `save.NAME_PATTERN`);
with no `after` it goes in front of the channel's send, never behind it, since
the send carries the channel to the master. **A remove** is 409 for a track, a
send or return (known by name where the compiler puts them and by uid where
Sushi reports one) or what the rig lacks. Its plugin class comes from Sushi's
saved session, the one place the gRPC API names the plugin a processor is
(`ProcessorInfo` has no URI); a remove Sushi cannot name is refused (502)
rather than made unundoable. Each edit is sent as decided, confirmed by reading
the chain back, and logged only if the read-back matches (502 and not logged
otherwise): `add` with the name used and the chain before, `remove` with the
chain before and the plugin's `uri`, `uid` and `type`, so an undo can re-add it,
at its defaults, since parameter values are not recorded. Routes, decisions and
tests are in sections of their own (`test_agent_edits.py`) to keep the merge
with `/live/undo` small; the log format is unchanged. Found on the way: lilv's
Python collection iterator has no `__iter__`, so on Python 3.13 a comprehension
over `world.get_all_plugins()` fails where a `for` loop works. **Not run against
a live Sushi:** `create_processor_on_track(name, uid, path, processor_type,
track_id, before_processor, add_to_back)`, `delete_processor_from_track(processor,
track)` and `session.get_session()` (SaveSession) are read from elkpy 1.3.0's
source, and the tests bind the calls to those signatures; whether Sushi 1.3.0
places a processor as `before_processor` says, shows it in the chain read
straight after confirming, and reports an LV2 plugin's URI in the session's
`PluginClass.path` is not known. Add, move and remove on a rig that is `up`, and
read `/live` after each, before relying on them. Not done from #32 yet: the mute
ramp around an edit and the CPU budget check before an insert. *(2026-11-02)*
*A review of the branch found nine more, seven in the Turtle reader.* (1)
**Blank nodes from two files of a bundle were the same nodes**: each parse
numbered its anonymous nodes from one, so a plugin with two anonymous ports in
each of two data files had two; each file's blank nodes now carry a prefix of
their own (`parse_turtle(blank_prefix=)`). (2) **Tokenising was quadratic**,
counting the line from the start for every token (131 KB took a second, under
the library's lock); the line is now counted as the text is consumed, and a
megabyte with a thousand plugins reads in about a second. (3) A relative
plugins folder crashed the build in `Path.as_uri` (a 500); bundles are resolved
first. (4) A manifest that is not UTF-8 escaped as a 500; it is a
`LibraryError` that names the file. (5) **An add knew the send by name only**,
while a remove also knew it by uid, so an add on a rig whose send had another
name went in behind it, read back as asked, and was logged and inaudible. One
rule now, `live.protected_processor`, by uid and by name, serves the add, the
remove and the move: the walk an add decides on and a move plans on reads the
uids from Sushi's saved session (`read_live_chains(uids=True)`,
`plan_live_move(uids=True)`), and `decide_move` has an outcome `protected` for
a move of a send or return or a swap with one, which `/live/move` refuses (400,
as its other refusals) and sends nothing. That closes the move behind the send
reported above. The panel's listener plans its moves without the session read
and keeps the name rule alone, so a renamed send can still be passed there. The
cost: the agent's add and move now also depend on SaveSession, which is
unverified against Sushi 1.3.0; if it is not answered they fail loudly (502),
and do not fall back to names. (6) A bundle folder with no `manifest.ttl` was
skipped silently, its plugins then "not found in plugins/"; it is a problem
naming the folder. (7) The remove's read-back took `["chains"]` directly, a
`KeyError` 500 for a walk without them; `_confirm` is the one accessor and
returns the chains it confirmed, a 502 otherwise. (8) An unknown escape's
`TurtleError` had no line; it has. (9) `plugins_described` scanned every triple
three times per plugin; it indexes once per bundle. Every name and port count
still agrees with lilv on the assembled `plugins/` (rerun). *(2026-11-02)*

**The record bar records a `--rig` run (#57).** The listener now builds its
session at start (`transport.record_session`) instead of always running
`NoEngine`: a `TakeSession` over a `capture.CaptureEngine` when the run can be
recorded, and otherwise a `NoEngine` whose message names the reason, printed in
the listener's log at start and shown on the panel at the first press.
**Which ports a take records** (`transport.rig_stems`, decided in its module
docstring): for a rig of channels, in the rig's order, each channel's stem (its
track's own output, engine bus 1 + index, which the patchbay links to nothing,
so it is the channel alone, after its chain and before its level), its dry tap
(the machine file's port(s) for its input role: the physical input, because with
the amp running Sushi's input is the amp's output), and then the master
(`sushi:audio_output_0`/`_1`, the only place the mix and the master chain are).
guitar_vocal on this machine is eight columns: `sushi:audio_output_2`/`_3`,
`Audient iD14:capture_AUX0`, `_4`/`_5`, `capture_AUX1`, `_0`/`_1`. Sushi's
ports are spelt by `patchbay.sushi_ports`, which selftest's probes now share
rather than keeping their own copy, from `track_route`; the inputs by
`MachineSpec.role_ports`. A two-port stem must be two consecutively numbered
ports, since that is all `capture.numbered_ports` links; anything else is
refused. The rate is the compiled config's `host_config.samplerate`, the quantum
`rig.QUANTUM`; the take carries the config Sushi loaded, read at start, with the
amp as the listener last sent it (`TakeSession.amend_config`, set by `serve`,
the rule SAVE uses), `rig.yaml` as read at start, and `library_state`.
Parameters moved since `up` are not in the take's config: a take does not
capture Sushi's state as SAVE does. **Each fallback, as the panel words it
after "recording is not available:"**: JACK-Client or libjack missing
(`CaptureUnavailable`'s own sentence, with the pip extra or the package);
no `--rig`; a rig.yaml of tracks (v1), which gets no plan because the routing
that ships sums its tracks into the monitor bus ("run it with `sushi-rig up
--rig <name>`"); no `--panel-config`; a config with no sample rate; a machine
file that cannot be read or does not validate; any problem in the plan (an
input role the machine file lacks, a channel whose width does not match its
role's ports, ports not consecutively numbered, two stems on one port); an
engine that cannot be made; and anything unexpected, all prefixed "no take can
be planned for this run:" where the plan is the cause. `NoEngine` takes a
`reason`; with none it says what it said before. **`up` changes:** the listener
starts under `pw-jack` with `PIPEWIRE_QUANTUM` at the rig's quantum, as Sushi
and the amp do, because the capture engine is a JACK client; and it is given
`--machine`, the file the rig was compiled against, since the state file that
records it is written after the children start. `listen` gains `--machine` and
`--takes-dir`. `CaptureEngine.prepare` is not called at start: the listener
starts before Sushi, so the quantum read then may not be the one Sushi holds;
the first RECORD reads it, half a second once. Tested without JACK or PipeWire
(`test_record_session.py`): the plans for a two-channel rig and guitar_vocal,
that no channel stem is on a port the patchbay links, each fallback's sentence,
that `sushi-rig listen` hands `serve` a `TakeSession` with the engine stood in,
the amp hook, `up`'s listener command, and a whole take through the planned
stems (real recorder, engine stood in) that `take check` passes. **NOT
VERIFIED: no take has been recorded this way.** Nothing ran against a live
PipeWire. Unknown: whether the machine file's port names (as qpwgraph shows
them) are what a JACK client sees through `pw-jack`; whether eight columns keep
up as the spike's six did; whether an output the patchbay leaves unlinked
carries its signal to a capture client; whether `pw-jack` in front of the
listener changes anything else about it. **The check, on the rig:** `sushi-rig
up --rig guitar_vocal`, confirm the listener's log says "RECORD records ...",
press RECORD, play both inputs for ten seconds, press STOP, then `sushi-rig take
check ~/sushi-rig-takes/<name>`, and listen to each stem alone: the guitar stem
must hold no vocal, the dry taps no effects, and the master both. Record the
result here either way. *(2026-11-02)*

*A review of the same branch found nine more.* (1) **Two channels on one input
were refused**, since both dry taps landed on one port; a blend of two chains on
one guitar is a supported rig. They now share one dry tap, recorded once and
named for the role (`guitar-in-dry.wav`), and `take.check_channels` accepts a
dry stem named for one of the take's rig's inputs, at start and in `take check`.
(2) The "RECORD records ..." line went to block-buffered stdout, which the
SIGTERM from `down` could lose; it goes to stderr. (3) The listener's
`PIPEWIRE_QUANTUM` hard-coded 48000 while the plan read the config's rate. The
rate is fixed at 48000 for Sushi too, so it is `rig.RATE` in all three
`env_set`s, and a config at another rate is refused when the plan is made
rather than by the engine at every RECORD. (4) A failure in the amp hook
escaped as "unexpected error"; it is now "cannot record <name>: the config it
would carry could not be made (...)", before anything is written. (5) The rig
file and `library_state` were read once at listener start, so takes after a
SAVE or a commit recorded stale provenance; `TakePlan` carries `rig_path` and
`library_root`, read at each RECORD. (6) `NoEngine`'s default says no take was
planned for this run. (7) The amp rule lived in `serve` as an isinstance patch;
`record_session` now builds every `TakeSession` with it, over the dict the
listener updates in place (`serve` no longer copies `amp_parameters`). (8)
probes call `sushi_ports` directly. (9) The stem outputs are read off the
tracks of the config Sushi loaded, not a fresh compile of the rig file, so they
are the ports running; a channel the loaded config lacks is refused with "the
rig file has changed since `up`, so restart the rig". The channels and their
input roles still come from the rig file, read at listener start: the config
does not say which role a track plays from. Still NOT VERIFIED on the rig; the
check above stands, with a blend rig added if one is to hand. *(2026-11-02)*

**The web UI, v0: the rack and the stage screen (#31).** The agent serves a
page at `/` from `tool/src/sushi_rig/ui/static/`: plain HTML, CSS and JavaScript
modules, no build step and no framework, since the rig machine has no Node and
the UI must run from a checkout (`ui/__init__.py` says so). Two screens from the
"Rig UI mockups v2" canvas and nothing else from it: **the rack** (S1), every
channel a row of plugin cards with knobs from the profile, and **the stage
screen** (S5), one glance and one tap at phone width, with a lock. It polls
`/health`, `/live` and `/live/log` once a second and sends each change as it
happens, one request in flight per control with the newest value next
(`Sender`). Two routes are new, in a block of their own in `agent.py`:
`GET /profiles`, every profile flattened for the browser (`ui.panel_profile`,
no rig needed), and `GET /live/plugins`, which plugin each processor is, from
Sushi's saved session (`live.read_live_plugins`), with the send and return
marked by `live.protected_processor`; `/live` cannot say, since `ProcessorInfo`
names no plugin. `Live` gains a `plugins` field (default None, which
`/live/plugins` answers 501) and `app()` one line; nothing else outside the
block. **Decisions are pure:** `panel_profile` in Python, and in `ui/static/rig.js`
the value in real units (Sushi normalises linearly over the domain, so real =
min + v·(max − min); a profile's `log` or `db` scale changes the drag and the
text, never that), which controls a card shows (the profile's groups; `hidden`
left out; what the profile names and the plugin lacks, and the reverse, each
listed, not dropped), what is unsaved (netted from the log: each key against
what its first change replaced, since the rig's start and the last rig switch),
the undo on offer (the agent's `pick_undo`, tested against it on the same logs),
the state line, and the lock's refusals. `test_ui.py` runs rig.js's functions in
a bare Chromium page and drives both screens against a real agent over a fake
rig (`tests/ui_fake.py`) whose parameter names and ranges are read from the
repository's captured plugin data, and which says what it invents. **Failures
are loud:** a refused change shows the agent's reason in an alert and beside the
knob, and the knob shows the rig's value again; the agent's `last_error` and a
failed MIDI listener are in the same glance. **Said on screen, not built:**
load and xruns read "not measured" (the agent measures neither), so an up rig is
amber, never green (#49); saving (S4); the lock is the page's own, in this
browser's storage and on by default, because the agent has no performance lock
(#29), so another client is not held by it; "unsaved" knows only changes made
through the agent since the rig came up, so the OSC panel's, a MIDI mapping's
inside Sushi, or a `sushi-rig save` since, are not reflected, and the log does
not survive the agent; changes go over HTTP, not the WebSocket S1 names. CI
gets Chromium from `python -m playwright install --with-deps chromium`, in
`ci.yml` and in `ci/Dockerfile` (with the `[ui-test]` extra); without the
package the browser tests skip, and a Chromium that will not start fails them.
**Not run:** any of it against a live Sushi, a phone's own browser, or the
Docker build (no Docker here; the Dockerfile step is untested until CI builds
it). *(2026-11-02)*
