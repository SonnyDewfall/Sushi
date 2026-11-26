# The v1 workflow

This is the older way of working: a hand-written rig.yaml of tracks, compiled
to `config/<name>.json`, run with `sushi-rig up <name>` (without `--rig`),
tweaked, and saved back with `sushi-rig save`. The rig is now played and
shaped from the web UI instead ([user guides](../guide/README.md)), from rigs
of channels in `config/rigs/` ([the rig file](rig-file.md)).

The Open Stage Control panel that this loop was built around, and the OSC
listener behind its SAVE, reorder, amp and record buttons, are retired (#139,
#218): `up` starts no panel and no listener, and every run is what was called
headless. What each of the panel's jobs is now is [below](#the-open-stage-control-panel-retired).

```
rig.yaml ──emit──> rig.json ──verify──> names confirmed against Sushi
                      │
                   sushi -j -c rig.json
                      │
                   [tweak by ear]
                      │
                   capture ──> state.json
                      │
rig.yaml + state.json ──emit──> rig.json  (now carrying initial_state)
```

Hand-authored: `rig.yaml` (or, for the v2 rigs, `config/rigs/*.yaml`), the
per-machine `machines/<hostname>.yaml` that names physical ports, and any
component profile under `tool/examples/profiles/`. Everything below the rig file
in the diagram is generated.
[`tool/examples/rig.example.yaml`](../../tool/examples/rig.example.yaml) is an
annotated rig.yaml of this kind.

## Saving

`sushi-rig save <name> --rig <rig.yaml>` captures the running Sushi and writes
`config/<name>.json` with the current sound baked in (issue #10), archiving
the one it replaces under `config/archive/<name>/`. Given a rig of channels
(`--rig config/rigs/<name>.yaml`), it folds the capture back into that file
instead, in place and keeping its comments, and never writes a compiled config
into `config/`: what the web UI's Save does, without the agent's log. A value
the save did not change keeps the form the file wrote it in, so `1.0` stays
`1.0` and one knob turned is one line in `git diff`. A capture that names
processors the source does not have is refused: it is a capture of a
different rig. `sushi-rig save` refuses a plugin added or removed live: only
the agent's save has the log that explains one.

A captured session overrides the yaml in two ways, not one: parameter **values**
(via `initial_state`) and plugin **order**. Pedal order is a tonal decision, so
a save keeps the order it finds. The consequence worth knowing: the yaml's
plugin order and a saved config's order can drift apart, exactly as their
values already do. The yaml declares where a rig starts; a capture records
where it got to.

The two cheap checks of [`verify`](commands.md#checking-a-config-verify) run
automatically after `emit`.

## The Open Stage Control panel (retired)

Until #139 and #218, `sushi-rig up` without `--headless` generated an
[Open Stage Control](https://openstagecontrol.ammd.net/) panel from the live
rig (`sushi-rig panel`), started `open-stage-control` to serve it on port 8080,
and ran an OSC listener (`sushi-rig listen`) beside the rig for its buttons.
The page replaced it, and it was retired with "fix forwards if stuff is
missing". Each of its jobs, now:

| The panel did | Now |
|---|---|
| A fader for every automatable parameter | A card per plugin on the rack. A profile's hidden parameters are behind the card's **All** toggle, drawn plainly in the plugin's own units ([the web UI](web-ui.md)) |
| Log-scale drag where a range was wide | A knob on a log scale where its range is positive and wider than 10×, for a plugin with no profile as for a profile's `log` ([the web UI](web-ui.md)) |
| Bypass (the **Active** tickbox) | The card's bypass switch |
| Move a plugin earlier or later | The card's move buttons (`POST /live/move`) |
| The amp's two levels | The amp card ([the agent](agent.md#the-amp)) |
| SAVE, with a name | **Save** and **Save as…** (`POST /rig/save`), or `sushi-rig save` |
| The record bar | The record bar (`POST /record/start`, `/record/stop`), through `sushi-rig recorder` |
| Ran without the agent (`up`) | `sushi-rig up --direct` still runs a rig with no agent; it is then controlled from nothing but the CLI and Sushi's own MIDI, until an agent is started, which adopts it |
| Wrote to Sushi over OSC, past the agent | Gone: every change goes through the agent, so Undo, the unsaved count and the pushed changes see it |

`up --headless`, and `"headless": true` in `POST /rig/up`, are still taken so
that commands written before keep working, and change nothing;
`"headless": false` is refused. A state file written by an older `up` with
`"mode": "panel"` is read as any other: nothing refuses for it.
