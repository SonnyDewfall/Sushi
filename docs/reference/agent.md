# The agent

One process that owns the rig: it starts and stops it, changes it while it
plays, saves it, records it, and serves the [web UI](web-ui.md). Everything the
web UI does, it does through the routes on this page. For how to use the rig,
read the [user guides](../guide/README.md); this page is the detail.

## The routes

```bash
sushi-rig agent                      # serve the rig over HTTP on 127.0.0.1:8029
sushi-rig agent install-service      # or run it as a systemd user service (prints the systemctl commands)
curl localhost:8029/health           # the agent and the rig in one answer, crashed children included
curl localhost:8029/rig              # `status` as data
curl localhost:8029/doctor           # `doctor` as data
curl localhost:8029/                 # the web UI (its files are under /ui/)
curl localhost:8029/rigs             # the rigs of config/rigs/ a start or switch can name
curl localhost:8029/rig/load         # one pw-top sample of the rig's nodes: load, xruns, the block budget
curl -X POST localhost:8029/rig/up -d '{"config_name": "guitar_vocal", "rig": true, "headless": true}'
curl -X POST localhost:8029/rig/down
curl -X POST localhost:8029/rig/restart                             # refused (409 unsaved) if it would drop changes no save has written
curl -X POST localhost:8029/rig/restart -d '{"discard": true}'      # ... so this drops them, as `sushi-rig restart --discard` does
curl -X POST localhost:8029/rig/switch -d '{"config_name": "electric-octave-up"}'   # live where it can be (#93)
curl localhost:8029/live             # the running rig's parameters, bypass and chains, with ranges
curl -X POST localhost:8029/live/parameter -d '{"processor": "reverb", "parameter": "Mix", "value": 0.4}'
curl -X POST localhost:8029/live/bypass -d '{"processor": "drive", "bypassed": true}'
curl -X POST localhost:8029/live/move -d '{"processor": "drive", "direction": "up"}'
curl localhost:8029/live/plugins     # which plugin each running processor is, with its role and width
curl localhost:8029/profiles         # the component profiles of tool/examples/profiles/
curl localhost:8029/live/log         # what was changed through /live, newest last
curl -X POST localhost:8029/live/undo   # put back what the newest change not yet undone replaced
curl -X POST localhost:8029/live/undo -d '{"skip": true}'   # give up on the newest one that cannot be
curl localhost:8029/amp              # the amp as last SENT to Carla: its model, each parameter
curl localhost:8029/amp/models       # amp/models, each model's rate and whether it can be played
curl -X POST localhost:8029/amp/parameter -d '{"name": "Output Lvl", "value": -3}'   # dB, clamped and said
curl -X POST localhost:8029/amp/model -d '{"model": "Some Model.nam"}'   # restarts Carla alone: the sound stops for a moment
curl -X POST localhost:8029/rig/save                                # the running rig into its own rig file, in place (S4)
curl -X POST localhost:8029/rig/save -d '{"name": "electric-board-2"}'   # save as a new rig, which becomes the running one
curl localhost:8029/rigs/electric_board/saves                       # the points a rig can be reverted to: its saves, and its file's commits
curl -X POST localhost:8029/rigs/electric_board/revert -d '{"save": "<an id from saves>"}'   # write one back and play it; "discard": true drops unsaved work
curl localhost:8029/record           # recording, idle, or why recording is off (S7)
curl -X POST localhost:8029/record/start -d '{"name": "riff-idea"}'   # a take; no name is take-<date>-<time>
curl -X POST localhost:8029/record/stop                              # finish it: take check's verdict in the answer
curl localhost:8029/takes            # the takes, newest first, each with take check's verdict
curl localhost:8029/machine/roles    # the machine file's roles: ports, the widths each can feed, who uses each
curl localhost:8029/rigs/guitar_vocal/channels                      # a rig file's channels, as the file has them
curl -X POST localhost:8029/rigs/new -d '{"name": "two-guitars", "channels": [{"name": "clean", "input": "guitar-in", "width": "mono"}, {"name": "dirty", "input": "guitar-in", "width": "stereo", "from": {"rig": "electric_board", "channel": "board"}}]}'
curl -X POST localhost:8029/rigs/guitar_vocal/channels/add -d '{"name": "keys", "input": "vocal-in", "width": "stereo"}'
curl -X POST localhost:8029/rigs/guitar_vocal/channels/vocal/input -d '{"input": "guitar-in"}'   # or "width", or both

curl localhost:8029/health | jq .agent.midi   # the pedals: listening, or why not, and what was last heard
```

## The agent is the supervisor

The control plane every v2 feature needs: one process that owns the rig.
**The agent is the supervisor.** `POST /rig/up` starts Sushi, the patchbay,
the tuner and the amp, and unless headless the listener and the panel, as one
process group the agent holds; it plans them with the same `plan_children`
and writes the same state file as `up` always has, so `status`, `restart` and
`describe_rig` cannot tell which started a rig. `down` and `restart` act on
what the agent holds. A child that exits with no `down` asking it to is
noticed within a fifth of a second and reported: in `/health` under
`agent.supervisor.crashed` (its name, pid, exit status or the signal that
killed it, when, and its log), on the agent's stderr, and as `CRASHED` in
`sushi-rig status`. Each child is judged on its own, so a group signalled from
outside the agent (`down --direct`, a `kill`) shows its children's exits there
too, by signal. It is **not restarted** ([principle 9](../design/principles.md)): the agent heals nothing
on its own, and the way back is `restart`, which you ask for. What is wrong
beyond a child, `agent.supervisor.problems` says (and `status`, as `PROBLEM`):
the group holder dying on its own (the rig plays on, but `status` reads it as
orphaned and `up` refuses until `down`), or a failed start whose processes
would not stop, named by pid and still held, so `down` can be asked again. A
child that exits during a start that fails is part of that failure's answer,
not a crash, since there is no rig to restart.

**No lifecycle step holds the agent for ever.** `up`, `down` and `restart` are
each given two minutes (`supervisor.LIFECYCLE_TIMEOUT`): past it the answer is `504`, saying what was still running (compiling the
rig, clearing a stale qpwgraph, waiting for Sushi...), and what the step had
started is stopped. The stuck part runs in a thread that cannot be interrupted;
it is left to finish and nothing it does afterwards is kept. `agent.supervisor.doing`
in `/health` says what a step is doing while it runs.

## The CLI asks the agent when it is running

A running agent writes where it
listens to `agent.json` beside the state file (and removes it when it stops),
so `up`, `down`, `restart` and `status` look there first, on whatever `--host`
and `--port` the agent was given, and at 127.0.0.1:8029 when there is no file
or its agent is gone. If one answers, they go through it; if nothing is
listening, they do what they always did, forking their own supervisor. Each
says which in one line on stderr (`asking the agent at ...`, or `no agent is
running at ... (where that address came from), so acting directly`), so stdout
is unchanged. Something there that does not answer as the agent does (an
agent that is stuck, another program) is refused with nothing done, rather
than guessed at; `--direct` on any of the four acts without asking. `up
--amp-model <path>` works through the agent: the CLI makes the path absolute
against the checkout, as `up` always resolved it, and the agent takes a model
by path only from this machine (a loopback peer); from anywhere else a model
is a name in `amp/models/`.

How the group is held, because the obvious ways do not work: the agent cannot
fork a supervisor, since a fork from a threaded server is where deadlocks come
from, and it cannot lead the group itself, since `down` signals the group. So
it starts a tiny **group holder** (a Python that waits for a signal) as the
leader of a new group, and each child straight into that group, with
`subprocess`'s fork-and-exec, which runs no Python in between. The holder is
the state file's `supervisor_pid`, which is why the file reads as before. The
children are the agent's own, which is how it reads a crash's exit status.

## The rig outlives the agent

Stopping or restarting the agent leaves the rig
playing: the holder keeps the group and the state file describes it. The next
agent finds it there and can stop or restart it, but does not watch it (its
children are not that agent's to wait on), so a crash in it shows as a `DEAD`
child in `/rig` and `status` rather than in `crashed`, and `/health` says so in
`agent.supervisor.note`. The same holds for a rig started with `--direct` or
before the agent was. What the agent holds in memory goes with it: its log
(so Undo and the count of unsaved changes start again) and the amp as last
sent, which the next agent seeds from the running config. **An agent runs the
code it was started with**: after a `git pull` it must be stopped and started
again to run the new code (the browser re-reads the page's own files; the
agent's Python is read once, when it starts).

## As a service

`sushi-rig agent install-service` writes
`systemd/sushi-rig-agent.service` to `~/.config/systemd/user/` with this
checkout's path filled in, enables it for `graphical-session.target` (by the
link `systemctl --user enable` would make), and prints the `systemctl --user
daemon-reload` and `start` to run; it never runs systemctl itself. The unit's
comments say why each line is what it is: the graphical session because the
patchbay, tuner and panel are desktop programs that inherit the agent's
`DISPLAY`; `KillMode=process` so a service restart does not take the rig with
it. `curl localhost:8029/doctor` checks the machine as the service sees it.
**The unit has not yet been installed on a real machine** (its own header says
so). The agent itself, run from a terminal, has started, switched, saved and
stopped rigs on the rig machine since 2026-11-02
(`tool/spikes/live_rig/RESULTS-ui-2026-11-02.md`).

## What it needs, and what it refuses

Needs `pip install -e tool[agent]` (aiohttp), and `tool[agent,midi]`
(alsa-midi, and libasound) for the pedals; starting a rig through the agent
needs Python 3.11 or later, which is also the package's floor (`requires-python`). One lifecycle command at a
time: a second request while one runs gets `409 busy`, because `up` and `down`
racing is a second rig on top of a running one. `up` must be told which
config (`config_name` is required, so a stray empty POST starts nothing). Every argument is checked
before anything runs — a config name is a name, not a path — and nothing from a
request is passed through as text. A successful `up`'s answer carries its
`warnings` (an unmade patchbay link, an amp that died at start), which the CLI
prints as it always printed them. The agent binds to localhost; `--host 0.0.0.0`
opens it to the LAN, and until the local/remote permission split in #29 exists
that is your call, not a default.

## Live operations

Live operations (`/live`) read and change the rig that is running, by name,
over gRPC. A parameter `value` is Sushi's normalised 0 to 1 for now; real units
come with the profiles. A request is refused with `400` before anything changes
if it is mistyped or out of range, or names a processor or parameter the running
rig does not have (or a read-only one, or a track where a pedal is meant); what
the rig has is read for that one request, not by walking every parameter, so a
fader can send many changes a second. `503` means no rig is up; `502` that Sushi
went away, refused, or did not confirm the change, with the reason, and nothing
is logged for it; `500` that elkpy is not installed; `504` that Sushi did not
answer within 20 seconds (`LIVE_TIMEOUT`), where the agent stops waiting and
frees the next request, but the stuck call itself cannot be cancelled (its
connection is closed when it does return), and a change that timed out may or
may not have been applied: read `/live` before trying again. Calls to Sushi run
in a pool of their own, eight at a time; if a Sushi that never answers has them
all, a request is `503` with the count rather than queued, and
`GET /health` shows `agent.stuck_live_calls`. They are `409 busy` while a lifecycle command runs. Changes are
made one at a time so each `previous` is true; reading `/live` does not wait for
them and they do not wait for it, so a panel can poll it. Each request makes one
connection to Sushi and does its reading and changing on it. A move is decided
once, from one look at the chains, and what is sent is what was decided: a
processor already at that end of its chain is `changed: false`, even if it only
got there since the request arrived. Each change that Sushi confirmed is appended to an
in-memory log of the last 500 (`/live/log`: `at`, `op`, `args`, and the
`previous` value where it was known), which is where undo comes from.

### Undo

`POST /live/undo`, with no body, puts back the newest change that is not an
undo and has not been undone: a parameter to its previous value, a bypass to its
previous state, a move by the opposite step, an add by removing what it added
and a remove by adding the plugin back (both under [The library and chain
edits](#the-library-and-chain-edits) below), each through the same call the route makes, under the same lock and timeout and with the same `503`/`502`/`504`
and `409 busy`. The answer is the undo's log entry (`op: "undo"`, `undoes`: the
index of the entry it put back, `args` what it applied, `previous` what that
replaced), the entry `undone`, and the `state` now. An index counts entries since
the agent started, not places in the list, because the list is trimmed at the
front; `/live/log` says the index of the first it holds (`first`). It refuses
rather than guess, with nothing applied or logged, and a `409` carries a `code`
beside its `reason`: `busy`; `nothing_to_undo` (an empty log or one of undos
only, said without a Sushi to ask); `stale`, when the rig is not as the entry
assumes — a move's processor is not exactly where the move left it, a parameter
or bypass was changed since (so an undo never overwrites a hand on the panel),
the track an add went on is not as the add left it, the place a removed plugin
came from is gone, or Sushi never said what to put back. A processor the rig has lost is `400`, as
on the other routes. A refused entry is not skipped for an older one, so the
way past a stuck one is explicit: `{"skip": true}` checks the newest entry, and
if it is refused marks it `op: "stale"` in the log (it is not removed) so the
next undo goes on to the one before; an entry that can be undone is `409
not_stale`. Any other body is `400`. Undoing an undo (redo) is not built. **Not run against a live Sushi**: shown
against the fake only.

### The library and chain edits

Pick a plugin, add it to a chain, take one out (#32). These are the agent's
routes only: the web UI does not offer adding or removing a plugin yet.

```bash
curl localhost:8029/library          # every plugin in plugin-manifest.txt: uri, name, ports, profile, groups
curl -X POST localhost:8029/live/add -d '{"track": "guitar", "uri": "http://guitarix.sourceforge.net/plugins/gxts9#ts9sim"}'
curl -X POST localhost:8029/live/add -d '{"track": "guitar", "uri": "http://drobilla.net/plugins/mda/Delay", "name": "slap", "after": "guitar_overdrive"}'
curl -X POST localhost:8029/live/remove -d '{"processor": "slap"}'
```

`/library` lists the manifest, not the machine: names and port counts come from
the bundles in `plugins/`, read without lilv (and checked against lilv on every
bundle the packages provide), never from the system's LV2 paths. A name that
could not be read is the URI's last segment, marked `name_from: "uri"` with a
`note` saying why; a profile's groups come with its plugin. It is built once,
on first use, and kept until the agent restarts. The amp is listed but
`addable: false`: it runs in Carla, and on a Sushi chain it would have no model.
An add needs a manifest URI and a track; with no `name` it is named from the
plugin (`MDA Delay` is `mda_delay`), and with no `after` it goes last but in
front of the channel's send, never behind it, since the send is what reaches the
monitors; the send is known by its uid in Sushi's session as well as by its
name, and `/live/move` now refuses (`400`) to move a send or the return, or a
pedal past one. A bad request is `400` with Sushi untouched; a name the rig already
has, a track it lacks, an `after` not on that track, or a remove of a send,
the master's return, a track or something not there, is `409`. Both edits are
confirmed by reading the chain back, and one that does not read back as asked
is `502` and not logged. The log entry for an add has the chain before
(`previous`) and in `args` the name used, its `copy` to stereo (or null), the
`chain` it left, and `defaults`: the plugin's parameter values read straight
after the add, which are what a save compares with. Defaults Sushi will not
give, or do not give in time, leave the add standing and logged, `null`,
with a `note` saying a save will write every value. A remove's entry has the chain before, the plugin's URI, uid and
type from Sushi's saved session, its `copy`, the chain before as `units`
(each plugin with its copy, grouped as the remove grouped it, with Sushi's
uids), and its `parameters` and `bypassed`, read just before the remove (a remove whose values cannot be read
is `502` and removes nothing).

### Undoing an add or a remove

An add is undone by removing what it
added, its copy to stereo with it, through the remove's own path (read,
values, remove, read back), and only while the track is exactly the `chain`
the add left: anything else on it since is a chain the entry knows nothing
about, so that is `409 stale`. A remove is undone by adding the plugin back
through the add's own path, after the unit it followed in the chain before
as the remove grouped it (first on the track if it was first), with its copy
if it had one. It is bypassed by the very next command, so it is not heard at
the plugin's defaults while its values arrive; then every parameter value it
recorded is set, and then the bypass it had (on, if the remove could not
read it). The add's own command takes no bypass, so the moment between the
add and the bypass is one command long. If that place is
gone — the predecessor is no longer on the track, the track is gone, the
name or its copy's is taken — it is `409 stale` with the reason, and it is
not put anywhere else. What follows the predecessor now is not checked: it
goes straight after it. If Sushi refuses, fails or does not answer in time
anything after the add (the bypass, the read-back, a value), the plugin is
removed again and the answer is `502` saying nothing was put back and the
track is as it was (and, if that fails too, what the track holds); a call
still running keeps the connection open until it returns. Only an LV2 plugin is put back: a stray copy to stereo
removed as a repair is not recreated. Each undo is logged in the shape of
what it applied, an add's undo as a remove and a remove's as an add, with
`of` naming the op it undid; undoing an undo is still not built. A program
is not recorded, so a plugin whose state is a program alone comes back at its
default one. **Not yet run against a live Sushi**: that a processor added live
starts at the plugin's defaults and un-bypassed, and the values read and set
around an add and a remove, are shown against the fakes only.

## The amp

The neural amp runs in Carla, not Sushi, and
Carla cannot be read back, so what the agent holds is **what it last sent**:
`GET /amp` answers the model (its path and what the `.nam` declares) and each
parameter with its value, range, unit and whether the panel puts it on a
fader, with `known: "sent"` so nothing can present it as what Carla holds. It
is seeded for each run of the rig from the running config's `_amp`, which `up`
replays, and a parameter the config does not state is `null`, not a guessed
default. `POST /amp/parameter {"name", "value"}` sends one parameter, in its
own unit; a value outside the range is clamped and the answer says so. The
agent opens a TCP connection to Carla's OSC port (Carla serves OSC over TCP and
UDP; the send itself is UDP, which succeeds whether anything listens or not)
on the first send to a Carla, after any failure, and at most every two seconds
otherwise, so a fader's stream of sends costs one: no connection is `502` and
nothing is recorded. Every send also checks Carla's process is alive. That
proves a Carla is listening, not that it applied the value. Each change is in
`/live/log` (op `amp`), and `/live/undo` puts back what was last sent before
it, refusing as `stale` once something else was sent since. `POST /amp/model
{"model"}` plays another model of `amp/models` (a path only from this machine,
as `--amp-model`), refusing one trained at another rate than the rig's as
`doctor` faults it: **the sound stops for a moment**, because Carla is stopped
and started again on a rewritten project, alone, inside the group the agent
holds, with its knobs replayed; Sushi and everything else play on. If the new
model does not come up, the previous one is put back, and the answer says
whether that worked. The state file records the model, so `restart` keeps it.
It needs a rig this agent started (`409 not_held` otherwise), and is refused
while a lifecycle command runs, and while the state file does not name the rig
the agent holds. **With the panel running, the panel owns the amp** (any mode
but `headless`, as a switch and `restart` read it): its faders reach Carla through the listener, which keeps its own
record for SAVE, so the agent says `known: "panel"` and writes nothing
(`409 panel`) rather than keep a second record neither could trust. Run on the rig on 2026-11-02 from the web UI: the
output level followed its fader with no crackle, and a model change was about
1.2 seconds of silence (`tool/spikes/live_rig/RESULTS-ui-2026-11-02.md`,
step 6). In the tests Carla is a stand-in (an in-memory one, and a real UDP and
TCP listener).

## Switching rigs live

`POST /rig/switch {"config_name"}` (and a
Program Change, which takes the same path) turns the running rig into another
rig of `config/rigs/` without restarting Sushi where the live operations can
do it: the processors that differ are removed, moved and added, the
parameters and bypasses that differ are set, the amp's parameters that differ
from what the agent last sent are sent to Carla (the call `POST
/amp/parameter` makes), and the rig is read back and checked against the next
rig's config, and against every value the switch set, before the state file
names it. Carla cannot be read back, so an amp step is checked only by Carla
answering. The
answer says `how`: `live`, or `restart` with `why` -- every reason, by code.
What restarts: a different amp model, or an amp in one rig and not the other
(Carla loads a model only as it starts, and is started, with the patchbay
wired through it, only with the rig), a track added, removed or
reordered or of another shape, a channel on another input, a different host
config or MIDI map (`cc_mappings`, which Sushi loads only with the config), a
program or property to set, a rig run with its panel (whose SAVE listener
belongs to the rig being left) or with `--amp-model` or a model chosen through
`POST /amp/model`, a rig changed by hand
since it came up in a way the plan cannot follow, a step that fails, and a
read-back that is not the next rig. A switch that fails part-way restarts into
the next rig, and brings the previous rig back if that restart fails; if the
restart cannot even take the rig down, `/health` and the answer say the rig is
**half-switched**, naming both rigs, and nothing more is tried: read `/live`
and bring it down and up by hand. Of the eleven rigs as they were on 2026-11-07
(kept under `tool/tests/fixtures/rigs/`), 72 of the 110 ordered pairs are
live (26 while the amp's parameters were a restart); the rest have an amp on
one side only, or other tracks. The rigs in `config/rigs/` have changed since
and will again, so no count of them is kept here or pinned by a test. One `op: "rig"` entry goes in
`/live/log` either way, and undo stops at it. A rig that would not compile is
refused (`409 rig_problem`) with the running one left playing. Run on the rig on
2026-11-02 between electric-chorus-2 and electric-delay: both switches were
live, in about 0.4 to 1.2 seconds, with no pops or clicks heard
(`tool/spikes/live_rig/RESULTS-ui-2026-11-02.md`, step 7); switches by restart
were measured through a loopback on 2026-11-12
(`tool/spikes/live_rig/RESULTS-loopback-2026-11-12.md`). The steps of a live
switch that add, remove or move a plugin, or set the amp, are not recorded as
run on the rig.

## Saving the running rig

`POST /rig/save` is the web UI's Save, since
the panel's SAVE listener runs only beside the panel and the page starts rigs
headless. It captures the running Sushi under the agent's live lock (and the
amp's), so no change lands between the capture and the log entry, and folds it
into the running rig's file with the same `save_rig` the panel's SAVE uses:
in place, keeping the file's comments, a send level moved live written back as
the channel's `level:`. The amp is what the agent last sent Carla; a run with
no amp leaves the file's `amp` alone. `{"name": ...}` is "save as": a new
`config/rigs/<name>.yaml` beside it (another rig's name is refused, `409
taken`), written fresh, so without the source's comments, which then **is the
running rig**: the state file names it as a live switch would, so `restart`
brings it back, the next plain save writes there, and `/health` and `/rigs`
say it. If the new file cannot be made the running rig, the answer is a 500
saying the file was written and the rig still runs under its old name, and
nothing is logged as saved. **Plugins added and removed live are saved** (#32): the agent hands
`save_rig` its log's account of them (`agent.live_edits`: what was added, to
which track, with its URI and defaults, and what was removed, since the
newest save, rig switch or the rig's start, whichever is latest, each name
as its last edit left it, an undo counted as what it applied). It is found
by walking the log back from its newest entry to that point, under the live
lock, so a save costs the entries since the last one; a save's entry carries
the plugins added live it wrote, with their defaults (`args.added`), which
the next save starts from. `rigfile.fold_state` then writes the
chains as they run: a removed plugin's entry goes, with the comment lines
above, inside and beside it (yamlmerge's rule for an entry that moves: the
comment directly above a chain's first entry is that entry's unless a blank
line sets it apart as a heading; this is a chain's rule only, and the comment
above a rig file's first channel stays); an added one gets an entry at its captured
place, written as the rig files write a plugin — `name`, `uri`,
`bypassed: true` only if it is, and `parameters` only for the values that
differ from its defaults — and keeps to that on later saves; a move keeps
working as it did. The compiler's sends, return and copies to stereo never
appear. The log only explains a capture, it never changes one: a processor
the capture names that is neither the file's nor added to that track by the
log, or a plugin of the file missing from its chain with no remove of it in
the log, is still `409 not_saved`, as a capture of a different rig. What the
file holds is known by track and name: a name removed and added back on the
same track with the file's URI (an undone remove) is the file's own entry,
comments and all; with another URI, a new entry in its place; on another
track (removed from one channel, added to the next), an entry on that one. The
panel's SAVE and `sushi-rig save` have no log, so they refuse any plugin added
or removed live, saying only the agent's save can write it. Tested on copies
of committed rigs: an add, a remove and a move saved into
`config/rigs/accustomed.yaml` change exactly those entries. A save in place
writes the file the rig runs from whatever it is called (`Guitar.Vocal.yaml`
compiles, so it saves); only a name chosen for a new file is checked. Refused
in panel mode (`409 panel`: the panel's faders reach the amp through the
listener, so the agent does not know what the amp is at), for a v1 run (`409
v1`: `sushi-rig save` saves a config), and while a lifecycle command runs
(`409 busy`). **The file is written, never committed**: committing in your
checkout is your call, and the answer gives the commit it sits on, what git
says of the file itself (untracked, changed, unchanged) and whether other
tracked files are changed (`take.library_state`, `save.file_git_state`). A
save is `op: "save"` in `/live/log`, logged only once it has wholly happened,
with the file and that state; undo passes over it to the change before, which
leaves the rig unsaved again. Save and Save as were run on the rig on
2026-11-02 (`tool/spikes/live_rig/RESULTS-ui-2026-11-02.md`, step 8; the
fifteen lines a first save rewrote there are what #117 fixed), and rigs saved
from the laptop are committed in `config/rigs/`. **Saving a plugin added or
removed live has not been run against a live Sushi**: shown against a fake
capture and real rig files.

## New rigs and channel edits

What the New rig screen and the rack's channel edits call. `POST /rigs/new` writes
a new `config/rigs/<name>.yaml`: the channels asked for, each a name, an input
role and `mono` or `stereo`, and a master on `monitor-out` (as `convert`
writes every rig's), with empty chains, unless a channel says `from: {rig,
channel}`, which copies that channel's chain and `level:` from that rig's
file as it stands now (not as git last committed it: a tone saved and not yet
committed is the one you hear; the answer says what git says of that file).
`POST /rigs/<rig>/channels/add` adds a channel to the end of a rig file, and
`POST /rigs/<rig>/channels/<channel>/input` sets a channel's input, its
width, or both. Both edit the file in place with `write_rig`, keeping its
comments: tested on copies of the committed rigs, an add changes exactly the
channel's four lines and an input change exactly its line (two with the
width). **The compiler decides, not the agent and not the page**: every
draft goes through `rig.compile_rig_draft`, which parses, compiles and wires
it offline exactly as `up --rig` does, against the run's machine file (the
one the running rig was compiled against, or this host's with no rig of
channels up), and nothing is written unless it passes. A refusal is `409`
with the compiler's or the patchbay's own words and a `code`: `rig_problem`
(more than three channels, with the arithmetic; a channel name used twice or
not a name; a width the role cannot give; a role the machine file lacks),
`name` (the naming rule, `save.validate_name`), `taken`, `no_source` (a
`from` naming no rig or channel), `no_channel`. The wiring is the part that
knows the roles: a stereo channel on a one-port role without
`mono_to_stereo`, a mono one on a pair and an unknown role are refused by
`patchbay_for`, which compiling alone does not call, so `compile_named_rig`
on its own would accept a rig `up` then refuses. **The machine file is never
written**: a role you need and do not have is said with the lines to add
under `roles:` (`machine_hint`), and you add them. Two channels may share an
input (one port fanned to two engine inputs). `GET /machine/roles` gives the
page each role's ports, `mono_to_stereo`, the widths it can feed and whether
it can take the master (each the patchbay's answer, with its reason), which
of the running rig's tracks use it, and the ceiling as the compiler refuses
a fourth channel. Which roles are inputs and which outputs is read from
their names (`-in`, `-out`), since the machine file does not say.

**An edit of the running rig's own file** takes effect when the rig
restarts from it: the answer says so, with `rigfile.diff`'s reasons as a
switch gives them (`track_added`, `wiring`, `track`), and the page offers the
restart. It is **refused while the rig holds changes the agent saw and no
save has written** (`409 unsaved`, naming the newest): the restart that plays
the edit reloads the file and would drop them. Save, then edit. (Save
itself would still work after an edit: a capture says nothing of a track the
rig does not have yet, so the file keeps the new channel. It is the restart
that would lose the work.) Counted, not netted out, so a knob turned and turned back still asks
for a Save, which loses nothing. With the panel running it is refused too
(`panel`): the agent cannot see the panel's changes. Another rig's file can
be edited whatever is playing. A write is refused while a lifecycle command
runs (`409 busy`, as every route is), and holds the lifecycle lock itself
and, for the running rig, the live lock, so no restart, switch or live change
lands between the check and the write. **Never committed**: the answer gives the commit and
what git says of the file, as a save's does, read once the lifecycle lock is
let go and within the write's deadline: a git that does not answer leaves
`library: null` with `library_note` saying so, never a rig stuck busy. A new
rig whose name sorts before others **moves their Program Change numbers**
(program *n* is the *n*th file by name): the answer then has `programs`,
which rigs moved from which number to which, and the page shows the table,
since a pedal preset set for one rig would select another. The order rule
itself is unchanged.

**The machine file goes with the run.** A draft is checked against the
machine file the running rig was started against (the state file's
`machine`), and the restart and the switch that play it are started against it too: `restart` passes it to `up` (`up --machine`,
which the CLI takes with `--direct` only, since the agent takes no path from
a request), and every switch by restart carries it (`switch_steps`); this host's file
(`machines/<hostname>.yaml`) need not be the file the rig was checked or
compiled against.

### A restart refuses to drop unsaved work

Every `POST /rig/restart` (the
page's restart after an edit, the picker's, the CLI's `sushi-rig restart`
through the agent) reloads the rig's file, so it is refused, `409 unsaved`
naming the newest change, while the agent saw changes since the last save;
`{"discard": true}` (`sushi-rig restart --discard`, or the page's second
tap, "Restart anyway, dropping them") restarts anyway. Not refused when
there is nothing to keep or no way to know: Sushi's process is gone (the
work went with it, and recovery must not wait on a Save), or the rig runs
with its panel, whose changes the agent cannot see. A stop and a switch to
another rig still drop unsaved work, as they always have. Remote callers are treated as
`POST /rig/save` treats them, no differently: a write needs no loopback peer,
since Save writes the same files without one; the agent binds to localhost,
and `--host 0.0.0.0` stays your call. **Not recorded as run against a live Sushi**: the
rigs these write are shown to compile and wire as `up --rig` would, and
MANIFEST's last word (2026-11-06) is that no rig made this way had been started
on the rig. Rigs with a new name and an added channel have been committed from
the laptop since (2c36874, 612e179); the record does not say which screen made
them.

## Recording from the web UI

The panel's record bar belongs to its
listener, and a headless rig has none, so `up --rig --headless` (what the page
starts, and `--direct` too) starts **`sushi-rig recorder`** in the listener's
place: a child of the rig, in its process group, under `pw-jack` with the rig's
quantum, because the capture engine is a JACK client and the agent is not under
`pw-jack` and must not be. The agent asks it over a Unix socket beside the state
file (`recorder.sock`, mode 0600), so an agent started later, or one that never
held the rig, finds it too. A child rather than started at the first RECORD:
it is then supervised like Sushi (a crash is in `/health`, under
`agent.supervisor.crashed`, and is **not restarted**), it stops with the rig
(`down`'s SIGINT finishes a running take), and it opens no JACK client until a
take. `POST /record/start {"name"?}` sends the run's files from the state file
as it is now and the amp as the agent last sent it (the amp a save writes), and
the recorder plans the take then with `transport.record_session`, the
listener's own: the same eight columns for guitar_vocal, the same refusals, a
take whose config is the one Sushi loaded for the rig running now (a live
switch rewrites it) and whose rig.yaml is the rig file at RECORD.
`POST /record/stop` answers `take check`'s verdict. Each start and stop is in
`/live/log` (`op: "record"`), and undo passes over them. Refused, each with a
`code`: no rig, the panel running (its record bar owns recording), a v1 run (a
take records each channel's own output, which only a rig of channels has), a
take already running, none running, a run with no recorder or one whose
recorder crashed, and a run the recorder cannot plan a take for, with its
reason. **A stop, restart, rig switch or model change while recording finishes
the take first**, and its answer carries the take's; finished rather than
refused, because the command is yours and made on purpose, and a refused
Program Change would be a pedal doing nothing mid-set (an `up`, which refuses
a running rig, leaves the take alone). The amp a take names is the one playing
when it starts: a fader or model change asked for during a start waits for it.
`GET /takes` lists the takes folder: each take's length, stems, whether it is
complete, audio lost, and `take check`'s problems, a take checked again only
when a file in its folder changes; only the rack asks for it.

The machine file names the guitar's port by its PipeWire alias
(`Audient iD14:capture_AUX0`, what the patchbay matches), and a JACK client
sees it as `Audient iD14 Multichannel:capture_AUX0`, so each port a take names
is resolved to the name JACK sees before the take is planned
(`transport.jack_port`), and the capture engine then finds it among the ports
its own JACK client lists, by name or by JACK alias; the machine file stays as
it is, since the patchbay needs the alias. **A take was captured on the rig on
2026-11-06** (electric-chorus-2, five columns, 24.6 s, `take check` ok; see
[Takes](takes.md#the-capture-engine)). Still not run on the rig: a take of
eight columns (a two-channel rig), a stereo dry tap, and the engine's own
match by JACK alias, which is the second line when the first resolution
cannot read the graph.
