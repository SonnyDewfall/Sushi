# Design

Why the rig is built the way it is. None of this is needed to play it (the
[user guides](../guide/README.md) are for that) or to look something up (the
[reference](../reference/README.md)).

| Page | What it answers |
|---|---|
| [The problem, and the architectural decision](problem-and-decision.md) | Why Sushi's config is hard to author, and why Sushi is both where a sound is built and where it runs |
| [Quality principles](principles.md) | What "good" means here: the nine principles a change is argued against, and where they disagree |
| [Where this is going: v2](v2.md) | The direction: a live-first rig that writes its own readable files |
| [What this project owns](ownership.md) | What other Sushi tools do, and why this one keeps a file as the source of truth |
| [The implementation brief](IMPLEMENTATION_BRIEF.md) | The brief the project started from |

## About the brief

`IMPLEMENTATION_BRIEF.md` is kept as history, not as a spec: **where it and the
code disagree, the code is right.** Two known cases: the `initial_state` shape
it describes does not load in Sushi 1.3.0 (see the docstring of `emit.py`), and
its sketch of elkpy's API is wrong in places (see the docstring of `live.py`).
Its §2 lists domain facts that are easy to get wrong; several contradict a
reasonable first guess. The file paths inside it are as they were when it was
written. The single-file prototype it refers to was deleted on 2026-10-31; git
history has it.

What was done, found and decided since, in date order, is in
[MANIFEST.md](../../MANIFEST.md).
