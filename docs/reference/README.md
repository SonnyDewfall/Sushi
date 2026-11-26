# Reference

The detail, for looking things up. To play the rig, start with the
[user guides](../guide/README.md); for why it is built this way, see the
[design pages](../design/README.md).

| Page | What is in it |
|---|---|
| [The rig file](rig-file.md) | `config/rigs/<name>.yaml`: channels, chains, levels, the master, and what the compiler makes of them |
| [The machine file](machine-file.md) | `machines/<hostname>.yaml`: which physical ports play which role, and the patchbay made from it |
| [Commands](commands.md) | every `sushi-rig` command, with `up`, `down`, `restart`, `status`, `top` and `verify` in full |
| [The agent](agent.md) | the agent's routes and rules: live changes, undo, the amp, switching, saving, new rigs, recording |
| [The web UI](web-ui.md) | how the page behaves control by control, what is not built, and how to run it against a fake rig |
| [MIDI](midi.md) | the controller in the machine file, the map in the rig file, and what the agent does with a pedal |
| [Takes](takes.md) | what a take's folder holds, `take check` and `take repair`, and the capture engine |
| [Plugins and portability](plugins.md) | `plugins/`, the manifest, `LV2_PATH`, choosing a plugin with `probe`, a known plugin fault |
| [Environment](environment.md) | what the machine needs, setting up from scratch, `doctor` and the other preflight checks |
| [Dependencies](dependencies.md) | every outside project the rig runs on or is built with: the version used, where it is pinned, how to check upstream for a newer one, and the faults worked around |
| [Tests and CI](testing.md) | running the suites, the rule about `config/rigs/` and the frozen fixtures, the two workflows |
| [The v1 workflow](v1-workflow.md) | retired: the v1 configs and the Open Stage Control panel, and what each part, and each of the panel's jobs, is now |
| [Repository layout](repository-layout.md) | what each folder is for |

The dated record of what was done and found is [MANIFEST.md](../../MANIFEST.md).
