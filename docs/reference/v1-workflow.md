# The v1 workflow (retired)

This was the older way of working: a hand-written rig.yaml of tracks, compiled
to `config/<name>.json`, run with `sushi-rig up <name>`, tweaked on the Open
Stage Control panel, and saved back from the panel's SAVE. It is retired
(#139, #218): the rig is played and shaped from the web UI
([user guides](../guide/README.md)), from rigs of channels in `config/rigs/`
([the rig file](rig-file.md)), and `sushi-rig up <name>` runs
`config/rigs/<name>.yaml`. This page says what each part became, and is kept
as the record of how it worked.

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

## The v1 configs (retired)

`config/*.json` were the v1 configs, one tone each, built from the rig.yaml
sources in `config/src/` and saved over by the panel's SAVE (each earlier
version archived under `config/archive/<name>/`). Every one was converted to a
rig of channels of the same name (#30, `sushi-rig convert`), and the web UI ran
only those. With the panel gone, nothing needed the v1 path, and `--rig`, which
chose a rig of channels, became what `up` always does. Each part, now:

| The v1 path did | Now |
|---|---|
| `up <name>` ran `config/<name>.json` | `up <name>` runs `config/rigs/<name>.yaml`; `--rig` is still taken and changes nothing; `"rig": false` in `POST /rig/up` is refused |
| `restart` brought a v1 run back as one | A run is a rig of channels; a state file an older `up` wrote for a v1 config is brought back as the rig of channels of its name |
| `sushi-rig save <name> --rig <rig.yaml>` wrote `config/<name>.json` and archived the one before | `sushi-rig save <name> --rig config/rigs/<name>.yaml` folds the capture into the rig file, in place with its comments; a rig.yaml of tracks is refused |
| `verify --all`, `plugins check`, `doctor` read `config/*.json` | They read every rig of `config/rigs/`, compiled as Sushi loads it (`verify.config_to_check`) |
| `nulltest --all` held each v1 config to `config/sound/<name>.v1-mono.yaml` | It holds the rigs and the frozen rigs; a v1 config left in `config/` would still be held to its reference |

The ten v1 configs, their three sources and their sound references were moved,
not deleted, to `config/retired/v1/` ([its README](../../config/retired/README.md)).
`sushi-rig verify` still takes one by its path, `convert` still turns one into
a rig of channels, and `emit`, `push`, `probes` and `patchbay generate` still
read a rig.yaml of tracks (the annotated
[`tool/examples/rig.example.yaml`](../../tool/examples/rig.example.yaml) is one).
`config/archive/` is left as the panel's saves left it. A run an older `up`
started from a v1 config, still playing at the upgrade, is refused a save and
a take (`409 v1`) until it is restarted.

A captured session overrode the yaml in two ways, not one: parameter **values**
(via `initial_state`) and plugin **order**, since pedal order is a tonal
decision. The same holds for a rig of channels: a save writes the values,
bypasses and order it finds.

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

## Saving

`sushi-rig save <name> --rig config/rigs/<name>.yaml` captures the running
Sushi and folds the capture into that rig file, in place and keeping its
comments (a new name writes a new rig file beside it): what the web UI's Save
does, without the agent's log. A value the save did not change keeps the form
the file wrote it in, so `1.0` stays `1.0` and one knob turned is one line in
`git diff`. A capture that names processors the file does not have is refused:
it is a capture of a different rig. `sushi-rig save` refuses a plugin added or
removed live: only the agent's save has the log that explains one.
