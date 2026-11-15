# What this project owns (and what it doesn't)

Several projects already drive Sushi — [`elk-audio/sushi-gui`](https://github.com/elk-audio/sushi-gui)
(official, Qt, live parameter and graph editing),
[`scraporchestra`](https://github.com/OlivierSch755/scraporchestra) (web app,
project manager for Elk Pi), and Sushi's own native session save/restore. They
were evaluated deliberately rather than ignored.

They all share one assumption: **live Sushi state is the source of truth.** You
drive a UI, state lives in the engine, and persistence dumps whatever is
currently loaded.

This project inverts that. **A rig file is the source of truth** (hand-authored
in v1; in v2 written by the rig's own Save, and still readable), and configs are
versioned, readable, diffable artefacts. That's not a UI
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

Where those projects do something well, the intention is to borrow rather than
reinvent — Sushi's native `SaveSession`/`RestoreSession` for runtime switching,
and `sushi-gui`'s live-session-to-config derivation for the cases where no
`rig.yaml` exists yet. **Neither is done yet**: the agent asks Sushi for its
session (`SaveSession`) only to learn which plugin each running processor is
(`live.read_live_plugins`), nothing calls `RestoreSession`, and what this rig
writes is still derived from `capture`. Both are tracked in
[issue #11](https://github.com/SonnyDewfall/Sushi/issues/11); rationale for the
division of labour is in
[issue #12](https://github.com/SonnyDewfall/Sushi/issues/12).
