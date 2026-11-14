# The v1 workflow and the Open Stage Control panel

This is the older way of working, and it still runs: `sushi-rig up` without
`--headless` starts a generated [Open Stage Control](https://openstagecontrol.ammd.net/)
panel and a save listener beside the rig. The rig is now played and shaped from
the web UI instead ([user guides](../guide/README.md)), which starts rigs
headless; the two do not mix on one run, because with the panel running the
panel owns the amp and its SAVE (see [the agent](agent.md#saving-the-running-rig)).

```
rig.yaml ──emit──> rig.json ──verify──> names confirmed against Sushi
                      │
                      ├──panel──> panel.json ──> Open Stage Control faders
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

**Two modes, for two ways of using the rig.** `sushi-rig up` by default gives
you the panel and the save listener behind it: tweak, type a name, press save,
and `config/<name>.json` appears with the current sound baked in (issue #10).
`--headless` skips both and just runs the rig, which is what the web UI starts.

## Save from the panel

The tweak → capture → emit loop at the bottom of the diagram is where the time
goes. `sushi-rig up` runs an OSC listener (`sushi-rig listen`) alongside Sushi,
and the generated panel carries a name field and a save button that trigger that
loop directly. `sushi-rig save <name> --rig <rig.yaml>` on the CLI does the same
thing without the panel, since the save button is just one caller of that same
capture-and-write step.
The listener acts on unauthenticated UDP — it writes config files and starts
recordings on receipt — so it binds loopback only; `--host` with any other
address exposes both to the network.

SAVE writes from the rig source `up` found for the running config: its
`_meta.source`, or `config/src/<name>.yaml`, or `config/rigs/<name>.yaml`. A
config with none has no rig to save from, `up` says so, and SAVE answers with
why rather than writing a guess. A capture that names processors the source
does not have is refused for the same reason: it is a capture of a different
rig.

On a rig of channels (`up --rig <name>`, with its panel), the panel's SAVE folds
the live capture back into the rig file itself, in place and keeping its
comments (a new name writes a new file), and never writes a compiled config into
`config/`. A value the save did not change keeps the form the file wrote it in,
so `1.0` stays `1.0` and one knob turned is one line in `git diff`; a float the
save changes to a whole number is still written as a float. A v1 run whose
source is a rig of channels has SAVE off, since saving the rig would leave the
v1 config behind; it says to use `--rig`. The panel's SAVE and `sushi-rig save`
refuse a plugin added or removed live: only the agent's save has the log that
explains one.

A captured session overrides the yaml in two ways, not one: parameter **values**
(via `initial_state`) and plugin **order**. Pedal order is a tonal decision, so
the panel can reorder the chain live and a save keeps it. The consequence worth
knowing: the yaml's plugin order and a saved config's order can drift apart,
exactly as their values already do. The yaml declares where a rig starts; a
capture records where it got to.

The two cheap checks of [`verify`](commands.md#checking-a-config-verify) run
automatically after `emit` and after the panel's SAVE.

## The panel's record bar

The panel's record bar (#57) is a take name, RECORD and STOP, with its own
status line. Like the save bar, the name is its own message and the buttons
carry nothing, and the listener decides whether a take is running, so a panel
reloaded mid-take cannot show it stopped. A take without a name is called
`take-<date>-<time>`; recording again under a name moves the earlier take to
`_archive/`, and the status line says so. What a take holds is in
[Takes](takes.md).

To check the record bar's layout on the rig, with the rig up, write a panel with
the bar over the one `up` made and reload it in open-stage-control; a reorder
keeps the bar, because the listener keeps whatever the panel it replaces had:

```bash
sushi-rig panel config/guitar_vocal.json --record-bar -o /tmp/sushi-rig-panel.json
```
