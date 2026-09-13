# Work manifest

What is being worked on, in what order, and why. Updated as work lands.

> Not to be confused with `config/Plugin manifest.txt`, which is the list of
> available LV2 plugin URIs.

**Status:** 🟢 done · 🟡 in progress · ⚪ not started · 🔵 ongoing

---

## 1. 🟡 Shared understanding — README and manifest

Establish what the project is, how it is shaped, and what is in flight, so the
next session (human or model) can pick it up cold.

- [x] Read the implementation brief and prototype; audit the live environment
- [x] `README.md` — goal, architecture, workflow, layout, environment
- [x] `MANIFEST.md` — this file
- [ ] Reconcile the brief's target layout (`src/sushi_rig/`) with the current
      flat repo — deferred until item 2 starts, since the layout only matters
      once the tooling is split out

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
`Project_files/sushi_rig.py`. Treat it as reference for data shapes and emit
logic, not as the target structure.

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

**Highest-value artefact to capture early:** a real `--dump-plugins` output
committed as a test fixture. Everything offline can be tested against it.

---

## 4. ⚪ Config library structure and versioning

Currently `config/` mixes deployable rigs, a passthrough test config, patchbay
routing and the plugin manifest in one flat directory. Needs separating, with a
convention for naming and versioning rig variants.

Open: whether `rig.yaml` sources live alongside their emitted JSON or in a
parallel tree, and whether emitted configs are committed at all or regenerated
on demand. Committing them is probably right — they are the deployable
artefact, and diffing them is how parameter drift becomes visible.

---

## 5. ⚪ Portability to other machines

The rig currently hard-codes `$HOME/Sushi` and depends on an untracked 358 MB
`plugins/` directory copied from the system LV2 install.

- Derive paths from the script location rather than `$HOME`
- Reconstruct `plugins/` from `config/Plugin manifest.txt` plus a package
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

| Question | How to settle it |
|---|---|
| Does Sushi name LV2 parameters from `lv2:name` or the port symbol? | Run `probe` and `--dump-plugins` on the same plugin, compare |
| How does Sushi normalise logarithmic ports? | Set a log port to 0.5 over gRPC, read back the formatted value, compare against the probed range |
| Which elkpy getter returns the normalised value? | `python3 -m pydoc elkpy.parametercontroller` |
| What are `create_processor_on_track`'s real argument names? | `python3 -m pydoc elkpy.audiographcontroller` — they have moved between releases |
| Exact shape of `--dump-plugins` on 1.3.0? | Capture one and commit it as a fixture |
| Do toggled and integer ports normalise as expected? | Confirm Sushi does not expose toggled ports as two-value enumerations |

---

## Decisions

**Plugin binaries are not tracked in git.** `plugins/` is an exact copy of
`/usr/lib/lv2` (262 of 262 bundles). Tracking it meant ~358 MB of third-party
GPL binaries in a public repo, reproducible from a package manager in seconds.
Configs address plugins by URI, so `config/Plugin manifest.txt` is the artefact
worth versioning. *(2026-09-13 — purged from history; repo went from 141 MB to
under 1 MB.)*

**Open Stage Control, not a bespoke web GUI.** Generated OSC fader panels close
the loop with far less code and match the brief. A custom UI — and any
patchbay-style graph view — is deferred until the workflow is proven.
*(2026-09-13)*

**Sushi is the authoring host, not just the deployment target.** Rejected
converting from a desktop host session format; see README for the three
compounding reasons. *(Pre-dates this repo — from the implementation brief.)*
