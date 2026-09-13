# Work manifest

What is being worked on, in what order, and why. Updated as work lands.

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

## 2. ⚪ Modelling → config workflow

The core of the project: a stable loop from *tweaking sound by ear* to *a
committed Sushi config*.

Build order from brief §10 — each step depends on the one before:

| Step | Module | Depends on | Offline-testable |
|---|---|---|---|
| 1 | `spec.py` — rig.yaml model + validation | PyYAML | ✅ |
| 2 | `emit.py` — spec + state → Sushi JSON | PyYAML | ✅ |
| 3 | `dump.py` / `verify.py` — cross-check names against Sushi | — | ✅ |
| 4 | `probe.py` — LV2 metadata catalogue | lilv | ❌ |
| 5 | `live.py` — push and capture over gRPC | elkpy | ❌ |
| 6 | `panel.py` — Open Stage Control faders | — | ✅ |

**Stop after step 3.** The open questions below must be answered against the
real Sushi 1.3.0 install before `live.py` and `panel.py` are built on an
assumption about them.

A working single-file prototype of all six already exists at
`tool/sushi_rig.py`. Treat it as reference for data shapes and emit logic, not
as the target structure — the first task here is splitting it into the module
layout above under `tool/src/sushi_rig/`, with `pyproject.toml` and `tool/tests/`.

**Blocked on:** `lilv` and `elkpy` are not installed (steps 4–5 only; steps 1–3
need neither).

---

## 3. ⚪ Headless runs and a config test suite

Every config in `config/` should be provably loadable without plugging in a
guitar, so edits can be made with confidence.

- Smoke test: `sushi --dump-plugins -c <config>` over every tracked config —
  exercises the full load path, needs no audio hardware, exits immediately
- `verify` over any config carrying `initial_state`, to catch parameter-name
  drift
- Offline render regression: `sushi -o -i input.wav -c rig.json`, hashing the
  output. A short committed input file makes this a cheap guard against silent
  parameter drift
- Unit tests per brief §8, driven from committed fixtures — priority order is
  spec validation, emit, dump parsing, verify, panel

- [x] Real `--dump-plugins` output captured as `tool/examples/dump.example.json` —
      the brief's highest-value offline fixture. Everything in the offline group
      can be tested against it.

The smoke test is available today and needs no new dependencies:

```bash
export LV2_PATH="$HOME/Sushi/plugins/lv2:$HOME/Sushi/plugins:/usr/lib/lv2"
for c in config/*.json; do ./sushi --dump-plugins -c "$c" >/dev/null 2>&1 \
  && echo "ok   $c" || echo "FAIL $c"; done
```

Worth wiring up first — it is what surfaced the defects below.

---

## 4. ⚪ Config library structure and versioning

- [x] Physically separated the kinds of file that were mixed in `config/`:
      patchbay routing → `Patchbay/`, plugin inventory → `plugin-manifest.txt`,
      leaving `config/` holding only Sushi JSON configs.

Remaining: a convention for naming and versioning rig variants, and where the
hand-authored `rig.yaml` sources sit relative to their emitted JSON.

Open: whether `rig.yaml` sources live alongside their emitted JSON (e.g.
`config/*.yaml` next to `config/*.json`) or in a parallel tree, and whether
emitted configs are committed at all or regenerated on demand. Committing them
is probably right — they are the deployable artefact, and diffing them is how
parameter drift becomes visible.

---

## 5. ⚪ Portability to other machines

The rig currently hard-codes `$HOME/Sushi` and depends on an untracked 358 MB
`plugins/` directory copied from the system LV2 install.

- Derive paths from the script location rather than `$HOME`
- Reconstruct `plugins/` from `plugin-manifest.txt` plus a package
  install, rather than by copying binaries
- Capture a plugin catalogue per machine and diff them — brief §5.3 exists
  specifically to catch version drift between the authoring machine and the
  target before it produces a wrong-sounding rig

---

## 6. 🔵 Grow the rig

Ongoing, once the loop above is stable: more plugins, more tracks, other
instruments.

Constraint worth knowing up front: **Sushi does not support the LV2 `patch:`
extension.** Any plugin whose state lives outside control ports — convolution
reverbs, sample players, sfz loaders, anything taking a file path — cannot be
configured from a JSON config at all. `probe` should flag these, because
discovering it late changes the shape of the rig.

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

### Still open

| Question | How to settle it |
|---|---|
| How does Sushi normalise logarithmic ports? | Set a log port to 0.5 over gRPC, read back the formatted value, compare against the probed range |
| Which elkpy getter returns the normalised value? | `python3 -m pydoc elkpy.parametercontroller` |
| What are `create_processor_on_track`'s real argument names? | `python3 -m pydoc elkpy.audiographcontroller` — they have moved between releases |
| Do toggled and integer ports normalise as expected? | Confirm Sushi does not expose toggled ports as two-value enumerations |

---

## Known defects

### 🔴 The plugin settings in both acoustic configs do nothing

Affects `config/acoustic_reverb_fx.json` and `config/acoustic_chorus_fx.json`
— every `properties` block in `config/` is of this form.


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

**Fix requires knowing each port's range to normalise against, so it is blocked
on `probe` (item 2 step 4) or a live `capture` (step 5).** Do not hand-convert:
LSP gain ports are frequently logarithmic, and the brief is explicit that
guessing those is worse than refusing.

### 🟠 `start-rig.sh` does not put the bundled plugins on `LV2_PATH`

The script sets `LV2_PATH="$HOME/Sushi/plugins:…"`, but `LV2_PATH` entries must
be directories *containing* `.lv2` bundles. Only `plugins/lsp-plugins.lv2` sits
at that level. The 262 bundles in `plugins/lv2/` are never seen — lilv treats
`plugins/lv2` as a single malformed bundle and logs
`Error reading …/plugins/lv2/manifest.ttl` on every launch.

The rig works anyway, because the fallback `/usr/lib/lv2` is also on the path —
which means the "portable plugins directory" is not actually supplying anything
except the newer LSP build. Relevant to item 5: portability here is currently
illusory.

### 🟠 Two LSP versions are installed simultaneously

`plugins/lsp-plugins.lv2` is v0.26; `plugins/lv2/lsp-plugins.lv2` and
`/usr/lib/lv2/lsp-plugins.lv2` are v0.22. lilv resolves this by silently
preferring 0.26. Which build a config resolves against therefore depends on
`LV2_PATH` ordering — precisely the version drift the catalogue diff in item 5
is meant to catch.

Also note `plugins/lv2/` is a byte-identical copy of `/usr/lib/lv2/` (262 of
262 bundles, no extras either way), so it is ~358 MB of pure redundancy that is
shadowed on the current path anyway.

### 🟡 Sushi requires `LV2_PATH` to be set explicitly

With `LV2_PATH` unset, Sushi loads no LV2 plugins at all and exits 4
(`Failed to load tracks from the Json config file`) — even though `lv2ls` finds
648 plugins using lilv's built-in defaults. Running `./sushi -c <config>` by
hand, outside `start-rig.sh`, fails for this reason and the error does not
mention `LV2_PATH`.

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
