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

**The agent's second slice: live operations (#29).** `/live` reads the running
rig (`live.capture` and `get_live_parameter_info`, so a client has each
parameter's range) and changes it: `POST /live/parameter`, `/live/bypass` and
`/live/move`, with `GET /live/log`. A parameter is set by processor and
parameter name to a normalised 0 to 1 value, and nothing else until the
profiles bring real units. The decisions are pure and tested directly: the
shape of a request is refused before Sushi is contacted at all; whether the
rig has the processor and parameter, whether the parameter is writable (a meter
is not), and whether a name is a track where a pedal was meant (a track's
bypass cascades to everything on it) are decided from what Sushi reports,
before anything is set; a move already at the end of its chain is
`changed: false` and never sent. Live calls are `409 busy` while a lifecycle
command runs but do not take its lock, and are serialised among themselves so
each logged `previous` is the value that change replaced. A rig not up is
`503`; Sushi gone or refusing mid-change is `502` with the reason, remembered in
`last_error` until a live change next works. Each change is appended to an
in-memory log (`at`, `op`, `args`, `previous`; last 500, newest last): the
source undo will use, not built yet, and lost when the agent stops. One thing
found on the way: `live._controller` exits the process when elkpy is missing,
and a SystemExit raised in an executor thread ends the event loop, so the agent
turns it into an error before it leaves the thread. `live.py` gained
`set_live_parameter` and `set_live_bypass`; its other functions are unchanged.
**Not run against a live Sushi:** no Sushi was available, so the two new elkpy
calls (`get_parameter_id` with `set_parameter_value`, and
`set_processor_bypass_state`) were read from elkpy 1.3.0's source, not
exercised; only the unreachable path ran for real, `capture` against a closed
port giving elkpy's `SushiUnavailableError`. Run a parameter, a bypass and a
move against `up` before relying on them. *(2026-11-02)*
