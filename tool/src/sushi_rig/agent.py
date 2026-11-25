"""The rig agent (#29): one local-first process that owns the rig.

Control reaches the rig from three places today: the panel speaks OSC to Sushi,
`listen` handles SAVE and reorder, the CLI speaks gRPC. Every v2 feature needs
one place that owns the rig instead, and this is its first slice: the lifecycle
and the two things worth reading about a rig, served over HTTP on localhost.

    GET  /health        the agent and the rig, in one answer: `agent.supervisor` is the rig
                        the agent holds and its children that crashed
    GET  /rig           what is running, if anything (`describe_rig`), and which
                        Program Change selects which rig when the rig's map switches rigs
    GET  /doctor        `doctor`'s checks, as data
    POST /rig/up        {"config_name" (required), "rig", "headless", "tuner", "amp", "amp_model",
                        "visualiser_tap"}
    POST /rig/down      {"force"}
    POST /rig/restart   {"discard"?} -- refused (409 unsaved) if it would drop changes no save wrote
    POST /rig/switch    {"config_name" (required)} -- to a rig of config/rigs, live where it can be (#93)
    GET  /midi          the machine's MIDI devices, what each control may take, the running rig's assignments (#191)
    POST /rig/midi/assign  {"control", <binding>: <target>} or {"control", "none": true}, "controller"? --
                        a pedal control in the running rig's file's midi: (#192); live where only the agent's
                        bindings change, else at the next restart; written, never committed
    GET  /live          the running rig's parameters, bypass and chains, with each parameter's range
    POST /live/parameter  {"processor", "parameter", "value"}   value is normalised, 0 to 1
    POST /live/parameters {"processor", "parameters": {name: value, ...}} -- several of one
                        processor's at once, all or none, one log entry, one undo (#154)
    POST /live/bypass   {"processor", "bypassed"}
    POST /live/move     {"processor", "direction": "up" | "down"}
    GET  /live/log      the changes made through /live, newest last (the last 500), and
                        `first`, the index of the oldest one held
    POST /live/undo     {} -- put back what the newest change not yet undone replaced;
                        {"skip": true} -- give up on the newest one that cannot be
    GET  /undo          what the page's one Undo would step back next (#209): `next` {label, kind, can, why}
    POST /undo          {} -- step back the newest change of any kind: a live edit, a rig file write
                        (a channel, an input, a pedal, a Save over a file, a revert, a new rig), a
                        rig deleted into the bin; 409 `cant_undo` drops one that cannot be
    GET  /amp           the amp as last sent to Carla: its model and each parameter (`known: "sent"`)
    GET  /amp/models    amp/models, each model's rate and whether it can be played
    POST /amp/parameter {"name", "value"}   value in the parameter's own unit (dB), clamped and said
    POST /amp/model     {"model"} -- restarts Carla alone, so THE SOUND STOPS for a moment
    POST /rig/save      {"name"?} -- the running rig into its own rig file, or as a new one (S4);
                        written, never committed
    GET  /record        the recorder: idle (and the name a take without one would get), recording (name, started, elapsed, stems), or unavailable and why
    POST /record/start  {"name"?} -- a take of the running rig (S7); no name is <rig>-<date>-take-<nn>
    POST /record/stop   {} -- finish it; the answer says `take check`'s verdict
    GET  /takes         the takes, newest first: name, when, length, stems, `take check`'s verdict
    POST /takes/open    {} -- open the takes folder in this machine's file manager; loopback peers only (#189)
    GET  /machine/roles the machine file's roles: ports, widths each can feed, which tracks use each
    POST /visualiser/tap  {"page"} -- start, or renew this page's lease on, the visualiser's tap on the
                        rig's output (#195); 200 once linked and realtime, else 409 and the page falls back.
                        Loopback peers only
    POST /visualiser/tap/release  {"page", "now"?} -- give the lease back; `now` stops it at once
    GET  /rigs/{name}/channels   a rig file's channels: name, input, width, level, plugins
    POST /rigs/new      {"name", "channels": [{"name", "input", "width", "from"?}]} -- a new rig file
    POST /rigs/{name}/channels/add              {"name", "input", "width", "from"?}
    POST /rigs/{name}/channels/{channel}/input  {"input"?, "width"?} -- each written, never committed
    POST /rigs/{name}/delete    {"dry_run"?} -- into config/rigs/.deleted/ (#129); refused (409) for the running rig
    POST /rigs/{name}/restore   {"entry"?} -- Undo: back from the bin; refused if a file of that name is there
    GET  /rigs/bin      the bin, newest first, and which agent's it is (the page checks a kept Undo against it)
    GET  /help/         the user guide, docs/guide/, as HTML (#167); /help/{page}, /help/img/{name}

## A fixed command set, never a shell

Each route maps to one lifecycle command with arguments the agent builds itself
(`lifecycle_arguments`), in the CLI's own words: a config name is a name, not a
path, and a flag is a boolean. Nothing a request carries is passed through as
text.

## The agent is the supervisor

The agent starts the rig's children itself and holds their process group
(`supervisor.py`): `up` starts what `plan_children` plans, as `up`'s forked
supervisor would, and writes the same state file, so `status` and
`describe_rig` read it unchanged. A child that exits while the rig is up is
noticed and reported in `/health` (`agent.supervisor.crashed`), and is not
restarted: principle 9, recovery is `restart`, asked for. It is not done by
forking, as `up` does, because a fork from this threaded process inherits
whatever locks another thread held at that instant; processes are started with
`subprocess`'s fork-and-exec, which runs no Python in between. No step holds
the lifecycle lock for longer than `supervisor.LIFECYCLE_TIMEOUT`: past it the
answer is 504, saying what was still running, and what it started is stopped.

The rig outlives the agent. A rig running when the agent starts -- one `up`
started with no agent, or an earlier agent's -- is not held, since its exit
statuses are not this process's to read, but `down` and `restart` act on it
through the state file as the CLI does. The CLI's `up`, `down`, `restart` and
`status` ask the agent when it is running (`agent_client.py`), so a rig started
from the CLI is held by the agent too, unless `--direct` said otherwise.

One lifecycle operation at a time: a second request while one is running is
answered `409 busy`, because `up` and `down` racing is a second rig on top of a
running one, which is what takes a live session down.

## Live operations talk to Sushi, and never start or stop it

`/live` reads and changes the rig that is running, over gRPC, through the
functions in `live.py` (names, not ids). A request is checked in two stages:
its shape and types first, with no Sushi involved (a value that is not a number
from 0 to 1 is refused having touched nothing); then what the running rig has,
from a read that costs what the request names -- the tracks and chains, that one
processor's parameters and the one value -- and not a walk of every parameter
of the rig, which a fader sending twenty changes a second could not afford. A
processor or parameter the rig lacks, a read-only parameter, a track where a
pedal is meant: refused (400) from that read, before anything is set. Values are
normalised, 0 to 1, for now: real units come with the profiles.

They refuse (`409 busy`) while a lifecycle command runs, checked when the
request gets its turn, because a rig half up or half down is not one to be
changing; they do not take the lifecycle lock, so a `down` can still begin while
one is in flight and will make it fail as `502`. They are serialised among
themselves, so the "previous value" each change records is the value it actually
replaced; a read (`GET /live`) takes no part in that, since it cannot disturb a
`previous`, so a panel polling it does not make fader writes wait. Each request
makes one connection to Sushi, does its reading and changing on it, and closes
it. No call to Sushi may take longer than `LIVE_TIMEOUT`: past it the agent
stops waiting, releases the lock and answers `504`, though it cannot stop the
call (see the constant); the call's connection is closed if it returns. Sushi
calls run in a pool of their own (`LIVE_WORKERS`), and when stuck calls fill it
a request is `503`, not queued; `/health` counts them. A rig that is not up is
`503`. Sushi going away, refusing, or not
confirming a change in time is `502` with the reason, and then nothing is logged:
the log holds changes Sushi confirmed. elkpy not being installed is `500`, the
agent's own install and not Sushi's doing. All but the `503` are remembered in
`last_error` until a live change next works.

Every change that succeeded is appended to the operation log, which is where
undo comes from. It lives in this process and is lost when the agent stops.

## Undo reads the log and writes to it

`POST /live/undo` takes the newest entry that is not an undo and has not been
undone, and applies its `previous` through the same functions the routes use:
`set_live_parameter`, `set_live_bypass`, a move the opposite way, and for a
chain edit (#32) the other edit: an add is undone by the remove route's path,
a remove by the add route's, with the values the remove recorded set after. It is a
change like the others (same busy check, same lock, same `LIVE_TIMEOUT`, same
503/502/504/409), and is logged like one: `op: "undo"`, `undoes: <index>`, `args`
what was applied and `previous` what that replaced, so a redo can be built from
the entry alone.

An entry's index is how many entries came before it since the agent started, not
its place in the list: the list is trimmed at the front, and a position would
change under the `undoes` that refer to it. `GET /live/log` says the index of the
first entry it holds (`first`). An undo is always newer than what it undoes, so
trimming can drop an entry and keep its undo but never the reverse, and one
backward pass over the log finds the next entry to undo.

Refused rather than guessed, with nothing applied or logged. A 409 carries a
`code` beside its `reason` so a client need not read prose: `busy` (a lifecycle
command is running, checked first); `nothing_to_undo` (an empty log, or one with
nothing left); `stale` (the rig is not as the entry assumes: a move's processor
is not exactly where the move left it, or a parameter or bypass is no longer at
the value the change set, or Sushi never said what to put back -- an undo must
not overwrite what was changed meanwhile). A processor the rig no longer has is
400, as the other routes answer it. A refused entry is not skipped to reach an
older one: that would undo something the caller did not ask about.

That would wedge the stack, though: the panel's listener moves processors
outside the agent's lock, so the newest entry can become impossible to undo for
good. `{"skip": true}` is the way out. It asks the same question of the newest
entry without sending anything, and if the entry is refused records an
`op: "stale"` entry (`stale: <index>`, with the reason), after which the next undo
goes on to the one before. An entry that can be undone is not skipped (409
`not_stale`). Skip was chosen over naming an entry to undo because a named index
would let a request undo out of order. Any other body is 400, since a body that
asked for more must not be answered as if understood.

A rig switch (`op: "rig"`, by `/rig/switch` or a Program Change) is a barrier: nothing
before it is undone (409 `nothing_to_undo`, saying so), since it was done to
the rig that was up then. Out of scope: undoing an undo (a redo). The entries carry what it needs; it is
not built. Also not verified against a live Sushi: it is shown against the fake.

## MIDI: the bindings Sushi cannot serve (#34, #93)

When the running rig's compiled config carries `_midi_agent`, the agent opens
the controller's port itself and serves it: a pad toggles a plugin's bypass
(and a CC switches it) through the calls `POST /live/bypass` makes, a pad on
a mute toggles a track's `mute` parameter, and a Program Change switches
rigs by the path `POST /rig/switch` takes: live where it can be, otherwise
`down` and then `up --rig` with the options `restart` would bring back. Each
change is in the live log with `source: "midi"`. A momentary or single
footswitch (#191) toggles its bypass or mute on each press, and a pedal on a
channel's volume is silenced at its heel. A pedal that is not plugged in is
waited for, and is a problem only where the rig's own map needs it. A machine file's switch whose
job is `previous-rig` or `next-rig` steps through config/rigs/ by that same
switch path on every rig, map or no map, stopping at either end; a press
while a switch or a lifecycle command runs is refused, not queued. Each
controller is a port of its own, with a listener of its own. The
listener starts when a rig comes up (and at the agent's own start, for a rig
already up) and stops before anything takes the rig down. A port that will
not open is a named problem in `/health` (`agent.midi`), not a retry loop, and
nothing the port does reaches the HTTP side. `GET /midi` gives the devices,
what each control may take, and the running rig's assignments, for the rack.
The decisions are in `midi_listener.py`; the wiring is the MIDI section at the
end of `Agent`.

## Switching rigs live (#93)

`POST /rig/switch {"config_name"}`, and a Program Change, switch the running
rig to another of config/rigs without restarting Sushi where the two allow
it: `rigfile.diff` of the two compiled configs says which processors to
remove, move and add and which parameters and bypasses to set, and which of
the amp's parameters to send Carla, in an order it explains, or every reason
it cannot (the amp's model, the tracks, cc_mappings, the host, a program...). The plan is applied under the lifecycle lock and the
live lock on one connection, through the read-check-set the live routes use,
then the rig is read back and checked against the next config as
`verify.diff_state` checks one, and against every value the plan set
(`rigfile.unapplied`: the strip and send defaults and un-bypasses the config
does not state). Only then are the compiled config and the
state file written, so `restart`, `/rig` and the listener follow the next
rig. A restart reason, a step that fails, or a reading back that is not the
next rig is a restart into the next rig (`down`, `up --rig`, and the
previous rig back if that `up` fails), said in the answer (`how`, `why`) and
on stderr. If steps were applied live and that restart's `down` then
fails, the rig is half-switched -- part of the next rig in Sushi, the
previous one in the state file -- and that is what /health and the answer
say, naming both; nothing further is tried. A rig run with its panel restarts: the panel and its SAVE
listener were started for the rig being left. One `op: "rig"` entry is
logged either way, and undo stops at it. Not run against a live Sushi and
not heard: shown against the fake only, and the time a live switch takes is
not measured.

## The amp (Carla's, not Sushi's)

The neural amp runs in Carla (amp.py), so its routes reach Carla's OSC, not
Sushi: what the agent holds of it is what it last sent, seeded per run from
the running config's `_amp` as `up` replays it, and every answer says so.
`POST /amp/parameter` sends one parameter once Carla's OSC port takes a
connection (502 and nothing recorded if it does not), and logs it (op
"amp"); `POST /amp/model` restarts Carla alone in the held group
(`Supervisor.restart_amp`), never Sushi, and records the model in the state
file so `restart` keeps it. Undo reaches both, checking each against what was
last sent, which is all a stale check can compare with here. With the panel
running, the panel owns the amp and the agent writes nothing (409 `panel`);
a rig switch sets the amp's parameters live, as steps of its plan, and
restarts only for a model or an amp on one side. Decisions in amp_live.py,
whose docstring says what is and is not known; wiring in the amp's block at
the end of `Agent`. Not run against a real Carla.

## Saving the running rig (S4)

`POST /rig/save` is the web UI's Save: the OSC listener's SAVE (listen.py)
runs only beside the panel, and the web UI starts rigs headless, so without
it a sound made from the page could not be kept. It captures the running
Sushi on the agent's own connection, under the live lock and the amp's, and
folds the capture into the running rig's file with `save.save_rig`, which
keeps the file's comments; the amp is what the agent last sent Carla
(amp_live.py), and a run with no amp leaves the file's `amp` alone. With a
`name` it is "save as": a new `config/rigs/<name>.yaml` beside the running
rig's file (another rig's name is refused, `taken`), which then becomes the
running rig, recorded in the state file as a live switch records one, so
`restart`, the next save, /health and /rigs follow it. Refused in panel mode
(`panel`: the agent does not know the amp's values then, and the panel has
its own SAVE) and for a v1 run (`v1`: `sushi-rig save` saves a config).

The file is written and NOT committed: committing in the player's checkout
is the player's call. The answer and the log entry (`op: "save"`, with the
file and `library`, the commit and dirty state from `take.library_state`)
say what it sits on. Undo passes over a save to the change before it, and
the page's unsaved list counts from the newest save.

Plugins added and removed live are saved too (#32): the save hands
`save_rig` the log's adds and removes since the rig came up (`live_edits`),
and `rigfile.fold_state` writes an added plugin's entry and drops a removed
one's. That is how a plugin added live is told from a capture of another
rig: the log explains the difference, and what it does not explain is
refused as before. Not run against a live
Sushi: shown against a fake capture and real rig files.

## New rigs and channel edits (the New rig screen)

A rig file is made or edited from the page, never from a hand on YAML: `POST
/rigs/new` writes a new `config/rigs/<name>.yaml` (its master on
`patchbay.MONITOR_ROLE`, its chains empty unless a channel's `from` copies
another rig's channel), `POST /rigs/{name}/channels/add` adds a channel and
`.../{channel}/input` changes a channel's input or width, both in place
through `rigfile.write_rig`, so a comment stays and only the changed lines
change. Nothing about what a rig may be is decided here or in the page:
each draft goes through `rig.compile_rig_draft`, the parse, compile and
offline wiring `up --rig` runs, against the run's machine file (the state
file's, or this host's with no rig of channels up), and a refusal is 409
with its words. The wiring matters: a role the machine file lacks and a
stereo channel on one port without `mono_to_stereo` are the patchbay's
refusals, which compiling alone does not make. The machine file is never
written; a missing role comes back with the lines to add (`machine_hint`).
`GET /machine/roles` gives the pickers what the patchbay says each role can
feed and the ceiling as the compiler refuses one more channel.

An edit of the running rig's own file takes effect at a restart, and the
answer says so with `rigfile.diff`'s reasons, as a switch gives them, and
offers it. It is refused while the agent saw changes since the last save
(`unsaved`): the restart that plays the edit reloads the file and would drop
them, so Save comes first. The restart itself refuses the same way
(`restart_refusal`, unless `{"discard": true}`), for a change made after the
edit and for every other restart; and it, like every switch by restart
(`switch_steps`), is started against the run's machine file, the one the
draft was checked against. A new rig that moves other rigs' Program Change
numbers says so (`programs_moved`). What git says of a written file is read
after the lifecycle lock is let go, within the write's deadline. With the panel running (`panel`) the agent cannot
see the panel's changes at all, so it refuses too. Each write takes the
lifecycle lock (`busy`), and the live lock for the running rig, so neither a
restart nor a live change comes between the check and the write. A remote
caller is treated as `/rig/save` treats one: no differently. Shown against
real rig files and the real compiler; not run against a live Sushi, and no
rig made here has been started on the rig.

## Recording from the web UI (S7)

The panel's record bar is the listener's, and the listener runs only beside
the panel; the page starts rigs headless. So a headless rig of channels gets
a recorder of its own, `sushi-rig recorder`, a child of the rig started with
it under `pw-jack` (recorder_process.py says why a child, and why a Unix
socket), and the agent asks it. `POST /record/start` names the take and
sends the run's files from the state file as it is now, and the amp as the
agent last sent it (`_take_amp`, the amp a save writes, #117); the recorder
plans the take then, with `transport.record_session`, so the take's config
is the one Sushi loaded for the rig running now (a live switch rewrites it)
and its rig.yaml the rig file as it is at RECORD (#110). `POST /record/stop` finishes it and answers
`take check`'s verdict. Whether a take runs is the recorder's to say, never
the page's (the reason #57 made RECORD and STOP two buttons). Each start and
stop is in the live log (`op: "record"`), and undo passes over them.

A take started without a name is `<rig>-<date>-take-<nn>` (#134,
`take.default_name`): the running rig, the day by this machine's clock, and
one past the highest number present for that rig that day, the archive
counted, stepped on past any name a take has (`take.next_name`). The
agent works it out, not the page, so two pages always agree: `GET /record`
offers it while idle (`default_name`), for the record bar to show, and a
start with no name asks the same function (`_default_take_name`) again,
under the record lock. So the name shown is the name used, unless a take
was made between the two, by another page or another day, and then the
start gets the next number rather than a name a take already has.

Refused (409, with a `code`): `no_rig`; `panel` (the listener's record bar
owns recording); `v1` (a take records each channel's own output, which only
a rig of channels has); `recording` and `not_recording`; `busy`;
`no_recorder` (a run started before the recorder existed, or one whose
recorder crashed, which /health shows and nothing restarts); `unavailable`
(the recorder cannot plan a take for this run, with its reason). A take tried
that did not start is 502, its folder saying why.

**A stop, restart, switch (by request or Program Change) or model change
while recording finishes the take first** (`_finish_take`), and the
command's answer carries the stop's (`take`); the stop is logged with `by`
naming the command. Finished, not refused: the command is the player's, made
on purpose, and a refusal would leave a pedal's Program Change doing nothing
mid-set. A switch or model change refused before it began leaves the take
running, and an `up` never finishes one: it refuses a rig that is running.
A start holds the amp's lock until the take has started, so a fader or model
change asked for meanwhile waits, and the take names the amp that played. A recorder that cannot be asked is said, and the command goes on:
`down` must always be possible, and its SIGINT finishes the take anyway.

`GET /takes` lists the takes folder (`take.list_takes`) off the loop: each
take's length, stems, whether it was finished, audio lost, and `take
check`'s problems. **Not run against a live PipeWire: no take has been
captured through this path.** Shown against the recorder's real core and a
real recorder process, with the engine stood in. The page no longer reads
it (#189: the rack names the folder instead, and a stop's answer carries
the take's folder and verdict); it stays for curl and anything else that
lists the takes.

`POST /takes/open` (#189) opens the takes folder in this machine's file
manager, for the rack's Open folder on the laptop: for a loopback peer only
(a phone is refused, `not_local`, and is shown the path), with the folder
the agent's own and never one from the request, by `xdg-open` or else `gio
open` with no shell, in a thread. `/health`'s `agent.takes` gives the
folder and whether this peer may ask (`can_open`). **Not run on a desktop**:
only against stand-in openers.

## Local-first, so localhost first

The CLI binds it to 127.0.0.1:8029 unless told otherwise. `--host 0.0.0.0`
opens it to the LAN, and until the permission split the issue describes exists
(local: everything; remote: start, stop, load, parameters) that is the
operator's call to make, not a default. `--phone` (#181) is that call said
as what it is for: it binds 0.0.0.0 and prints the URL a phone opens on each
Wi-Fi or ethernet address (`reach.py`), which `/health`'s `agent.listen`
gives the page too. A port already taken is said before anything starts, with
who has it and the command that stops it (`reach.port_in_use_message`).

## The library and chain edits (#32)

    GET  /library       every plugin in plugin-manifest.txt: uri, name, ports, profile and its groups
    POST /live/add      {"track", "uri", "name"?, "after"? | "first"?}
    POST /live/remove   {"processor"}

What S3 of the UX story needs: pick a plugin from the library, add it to a
chain, drag it into place (`/live/move`), take one out. The library is built
once, on first use, and kept for the agent's life (`library.read_library`:
the manifest, the bundles in `plugins/` read without lilv, the profiles); it
is the manifest's, never the system's LV2 paths. An add is checked in the same
two stages as every live change: the request's shape and that the URI is in
the manifest and can be hosted by Sushi (400, Sushi untouched); then, from a
walk of the chains under the live lock, that the track exists, the name is
free and `after` is on that track (409: a conflict with the rig as it is,
which the next read may change). It goes in before the track's send unless
told otherwise, never after it: the send is what carries a channel to the
master, and audio placed after it is not heard. A remove of a send, a return
or a track is 409, as is one of what the rig does not have, and a move of a
send or return, or past one, is refused too: one rule (`live.protected_processor`)
knows them, by the uid Sushi's session reports and by the compiler's names. Each is confirmed
by reading the chain back, not by Sushi's acknowledgement alone, and an edit
whose read-back is not what was asked is 502 and is not logged, as a change
Sushi did not confirm is not. An add reads the plugin's values straight
after it (its defaults, for a save) and a remove reads them just before (for
its undo), with `live.read_live_values`. Both are undone through each
other's path (`_apply_add`, `_apply_remove`): an add's undo is refused as
stale unless its track is as the add left it, and a remove's goes back after
the same predecessor or is refused, never put elsewhere (`place_undo_remove`).
Not run against a live Sushi: see `live.py`.

A mono plugin's copy to stereo (`stereo.py`, `<plugin>_to_stereo`) is the
compiler's too, and goes with its plugin: an add of a plugin with one output
(the library's `audio`, from `plugin-ports.txt`) adds its copy straight after
it in the same operation, and one the table does not list is refused (400);
a remove takes the copy first, then the plugin; a move steps the pair as one
past its neighbour, and past a neighbour and its copy as one, so nothing is
ever placed between them; the copy alone is never moved, removed or
bypassed, and `/live/plugins` gives it the role "stereo", which the page
does not show. Each of these is two of Sushi's commands where one was, and
none has been run against a live Sushi.

## Safety (#137): the master's dip, and the load before an add

An add, a remove and a move, their undos, and the structural steps of a live
switch (once, around them all) are sent inside a dip of the master track's
own gain: down to Sushi's floor, a wait (`ramp.settle`), the edit and its
read-back, and the level read before put back, also when the edit fails or
raises (`Agent._dip`). Its timing and what it may overwrite are `ramp.py`'s
to say. It is never logged and sets nothing but Sushi's gain; `GET /live`
shows the level it will put back while one is under way. A `POST /live/add`
first reads the load the page's LOAD tile reads, from the same shared pw-top
sample, and is refused (409 `over_budget`, the figure in the reason) over
`top.ADD_BUDGET` of the block; a load that cannot be read is said in the
answer and refuses nothing (`top.add_budget`).

## Safety (#137, option 2): the master's limiter and the master volume's top

Every compiled rig's master ends with `master_limiter`, mda/Limiter at the
compiler's fixed settings (`rigfile.LIMITER`). It is the compiler's as a send
is (`live.protected_processor`): never removed or moved, nothing is moved past
it, and an add with no place, or after the master's last plugin, goes in front
of it (`live.is_tail`), never after. It is never bypassed, and its parameters
are never set to anything but what they are fixed at (`rigfile.limiter_problem`,
400), by any path: the routes, an undo, a switch's step. `/live/plugins` gives
it the role "limiter", which the page shows as one read-only line. The master
volume stops at 0 dB, since Sushi applies the master's gain after the limiter:
a set of the master's gain above it is refused (400, `rigfile.master_gain_problem`),
as a value outside 0 to 1 is, through the route, `/live/parameters`, an undo
and a switch's step; and the dip never puts back more than 0 dB.

## The web UI (#31)

    GET  /              the UI's page; its files are under /ui/ (`sushi_rig/ui/static/`),
                        and /ui and /ui/ themselves redirect here (#111: the folder is not a page)
    GET  /profiles      every profile, flattened for the browser (`ui.panel_profile`)
    GET  /live/plugins  {processor: {"track", "uri", "uid", "type", "role"}}, from Sushi's session
    GET  /rigs          the rigs of config/rigs/ a start or a switch can name
    GET  /rig/load      one pw-top sample of the running rig's nodes: load, xruns, the budget
    GET  /events        the pushed changes (#139): a server-sent event stream; see below

The page polls the routes above and sends the live changes itself; the agent
adds only what the browser could not get: the files, the profiles (files, no
rig needed) and which plugin each processor is, which `/live` does not say
because only Sushi's session does (`live.read_live_plugins`). The routes are
in a block of their own at the end of the routes, before the MIDI section.

`/rigs` lists config/rigs/ by name, as a Program Change counts them
(`midi_listener.program_order`), because `/rig`'s `programs` exists only
while a MIDI map switches rigs, and the page's picker needs the list always.
`/rig/load` is `sushi-rig top`'s reading (`top.read_pw_top`, `load_report`)
as data, for the stage screen's LOAD and XRUNS: one `pw-top -b -n 2` off the
loop with a deadline (`LOAD_TIMEOUT`), killed past it, and shared: one run
at a time, its output answering every request for `LOAD_SHARED` seconds, so
pages polling do not keep pw-top running beside the rig. A pw-top that is
missing (500), fails or prints fewer than two samples (502) or overruns (504) is
refused with a `code` and the reason, and said once on stderr when it starts
or its reason changes; it is not `last_error`, which a figure polled every
two seconds would keep displacing. No rig up is 409 `no_rig`. Nothing
measures sound, so it is not here.

## Pushed changes (#139): GET /events

So a knob turned on one device moves on another without waiting for a poll,
the agent pushes each change it makes or sees to every open page, as
server-sent events: `text/event-stream`, a `hello`, then a `changed` event
(`{"seq", "what"}`) whenever what /health, /live and the log answer may have
changed. Server-sent events and not a WebSocket: the push is one way (the
page's changes go as they did, over HTTP), it is aiohttp's plain streamed
answer behind the same middlewares, the cross-site guard included, and it
needs no new dependency. The event names the change and the page polls at
once; why it carries no state, what publishes, and how a slow client and a
flood of changes are kept from holding anything up, is `events.py`'s. The
poll stays at its second for what the agent cannot see. The stream is the one
read the cross-site check is asked of (`GUARDED_READS`). Every stream ends
when the agent stops (on shutdown, before aiohttp waits for its handlers).

Every answer that sets no `Cache-Control` of its own is `no-store`
(`API_CACHE_CONTROL`), so nothing the agent says is kept by a browser, a
proxy or a home-screen install; the page's files stay `no-cache`, asked again
against their ETag. The page is installable (`ui/static/manifest.webmanifest`)
with no service worker, so nothing stands between it and the agent.

## Quiet, except for what matters (#111)

elkpy prints three `ELKPY:` lines on every connection, which at one
connection per poll buried the agent's own output; they are dropped from the
first connection on (`connect_quietly`, `live.quiet_elkpy`, which says why
each one may be). The one of them that mattered, Sushi not being found, is
said by the agent instead, from the call that fails: a read that finds no
Sushi while the state file says a rig is up is a fault, so it is in /health
(as `last_error`, or beside a lifecycle failure's, which it does not
displace) and printed once, from when the outage began until Sushi answers
again or the rig is down, when /health stops saying it. With no rig up it is
still the 503 it was, neither remembered nor printed, since nothing is wrong.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import contextlib
import copy
import dataclasses
import functools
import json
import math
import re
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Sequence

from . import library as library_module
from . import live as live_module
from . import midi_listener
from . import top as top_module  # GET /rig/load (#111)
from . import amp_live  # the amp: GET /amp, POST /amp/parameter and /amp/model
from . import stereo  # the compiler's copy to stereo after a mono plugin
from . import ramp  # the master's dip around a live structural edit (#137)
from . import history as history_module  # One Undo (#209): the history POST /undo steps back through
from . import events as events_module  # pushed changes (#139): GET /events
from .live import DEFAULT_GRPC_ADDRESS, ElkpyMissing, LiveError, Move
from .live import is_send, is_tail, protected_processor
from .rig import describe_rig
from .save import NAME_PATTERN
from .midi_listener import MidiError
from .rig import describe_rig, restart_arguments
from .supervisor import Outcome, Supervisor
from . import rigfile  # the live switch (#93)
from .rigfile import (  # the limiter, the top and the centred pan (#137)
    limiter_problem, master_gain, master_gain_problem, master_pan_problem,
)
from .rig import RigError, compile_named_rig, plugins_folder, plugins_refusal
from .state import read_state, write_state
from .verify import diff_state
from .recorder_process import (  # recording from the web UI (S7)
    START_TIMEOUT, STATUS_TIMEOUT, STOP_TIMEOUT, RecorderDown, RecorderTimeout,
)

try:
    from aiohttp import web
except ImportError:  # the `agent` extra is optional; `serve` says so
    web = None  # type: ignore[assignment]

VERSION = "0.1"

# How long one blocking call to Sushi (connecting, a read, a change) may take
# before the agent stops waiting for it and answers 504. elkpy's gRPC calls
# carry no deadline of their own, so a Sushi that accepts the connection and
# then stops answering would hold the live lock, and with it every /live
# request, for ever. The longest thing a call legitimately does is a full read
# of the rig (hundreds of RPCs at about a millisecond each on loopback) or a
# change waiting `live.CONFIRM_TIMEOUT` (5 s) for Sushi to confirm; this is
# four times that. A guess, not a measurement: no live Sushi was to hand.
#
# What the timeout does not do: stop the call. A thread in a blocking gRPC call
# cannot be cancelled from Python (there is no way to interrupt a thread inside
# C code that holds no deadline, and `Future.cancel` only reaches a call that
# has not started), so the worker stays stuck until Sushi answers or the process
# ends: its thread cannot be reclaimed, only waited for. The agent stops
# waiting and lets the next request through, and when the call does come back
# (slowly, rather than never) closes the connection it was using; `/health`
# reports how many calls are in that state (`stuck_live_calls`).
LIVE_TIMEOUT = 20.0

# How many calls to Sushi may be in flight at once, stuck ones included. Sushi
# calls run in a pool of this size that is the agent's own, not the loop's
# default executor: the default has min(32, cpus + 4) workers and is shared
# with /doctor, so a Sushi that never answers used up every worker after a
# handful of 504s, after which each request waited for a worker and was
# reported as Sushi not answering though its call had not started. A request
# makes its calls one after another, so this is also the number of requests
# that can be in Sushi at once; past it a request is refused (503) rather than
# queued, since a queue behind stuck threads would wait for ever.
LIVE_WORKERS = 8

# A config name names config/<name>.json and an amp model names
# amp/models/<name>. `up` checks each file exists, but a name with a separator
# in it would be looked up outside those folders, so both are refused here
# first: letters, digits and a few marks, no separators, and never a leading
# '-' that would read as a flag. `fullmatch`, because `$` matches before a
# trailing newline. The one exception is an amp model by absolute path from
# this machine (`is_local`), which is what the CLI's --amp-model has always
# taken; from anywhere else, a model is a name.
CONFIG_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")
MODEL_NAME = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_ .,()'+-]*")

LIFECYCLE = ("up", "down", "restart")
# How often a heel's silencing refused as busy looks again for the agent
# to be free (#192's re-review): a guess, short against a song, long against
# the loop.
MIDI_FREE_POLL = 0.05

# How long GET /rig/load gives `pw-top -b -n 2`, which samples twice and is
# read for the second (see `top.parse_pw_top`), at what should be pw-top's
# one-second refresh: so about a second. Past it pw-top is killed and the
# answer is 504. A guess with room in it, not a measurement: no pw-top was to
# hand where this was written.
LOAD_TIMEOUT = 5.0

# How long one pw-top sample answers GET /rig/load for every page asking:
# under the page's 2 s poll, so one page still gets a fresh sample each time,
# and two pages share one run instead of keeping pw-top running.
LOAD_SHARED = 1.5

# The operation log keeps this many entries, newest last.
LOG_LIMIT = 500

# "up" the chain is earlier in it, as the panel's move buttons are: the
# direction `live.decide_move` takes is negative for earlier, positive for later.
DIRECTIONS = {"up": -1, "down": 1}


class AgentError(Exception):
    """A request the agent will not act on, with the reason."""


Runner = Callable[[Sequence[str]], Awaitable[Outcome]]


def is_local(remote: str | None) -> bool:
    """Whether a request came from this machine: a loopback peer. Pure.

    Not proof of who sent it -- any local process, a browser page posting to
    localhost, or a reverse proxy on this machine all look local -- so it is
    used only to allow what the CLI on this machine could do anyway without
    the agent (an amp model by path), never to widen what reaches the rig."""
    if not remote:
        return False
    return remote == "::1" or remote.startswith("127.") or remote.startswith("::ffff:127.")


def cross_site_refusal(headers: Any, host: str | None, trusted: Any = ()) -> str | None:
    """Why a request a browser sent from another site must be refused, or
    None. Pure over the request's headers, its `Host` and `trusted`: the
    agent's middleware (`Agent._same_site_only`, #161) asks it of every
    POST, PUT, PATCH and DELETE, with `rebinding_refusal` after it.

    Any web page open on the rig machine (or on a phone that can reach the
    agent) can POST to it: `fetch(url, {method: "POST", mode: "no-cors"})`
    needs no preflight. A browser says where such a request came from:
    `Sec-Fetch-Site: cross-site`, and an `Origin` that is not the agent's own
    scheme, host and port (a page of another port on this machine is
    "same-site" but not this origin). Refused: either of those, and `Origin:
    null` (a sandboxed or file page). Allowed: neither header (curl, the
    CLI, `agent_client`) and this origin (the page the agent serves, from
    this machine or a phone on the LAN). Not by itself a defence against DNS
    rebinding: a page of an attacker's name that then resolves to this
    machine sends that name as both `Host` and `Origin`, and passes here.
    That is `rebinding_refusal`'s, which reads whether `Host` is a name
    this agent answers to.

    `trusted` is the operator's `--allow-host` names (review of PR #174):
    an `Origin` of one of them is this agent's whatever `Host` says, on any
    scheme and port, because a reverse proxy in front of the agent may
    rewrite `Host` to the address it forwards to (nginx's `proxy_pass`
    sends `127.0.0.1:8029` by default) while the browser's `Origin` is the
    proxy's name. Only those names: a page of `localhost:3000`, another
    program on this machine, is still not this origin."""
    site = (headers.get("Sec-Fetch-Site") or "").strip().lower()
    if site == "cross-site":
        return "refused: a page of another site sent this (Sec-Fetch-Site: cross-site)"
    origin = headers.get("Origin")
    if origin is None:
        return None
    origin = origin.strip()
    if origin.lower() == "null":
        return "refused: a page with no origin (Origin: null) sent this"

    def where(scheme: str, netloc: str) -> tuple[str, int | None]:
        from urllib.parse import urlsplit

        parts = urlsplit(f"{scheme}://{netloc}")
        try:
            port = parts.port
        except ValueError:
            return (netloc.lower(), None)
        return ((parts.hostname or "").lower(), port or {"http": 80, "https": 443}.get(scheme))

    from urllib.parse import urlsplit

    sent = urlsplit(origin)
    if sent.scheme not in ("http", "https") or not sent.netloc:
        return f"refused: Origin {origin!r} is not this agent's"
    try:
        sent_name = _bare_name(sent.hostname or "")
    except ValueError:
        sent_name = ""
    if sent_name and sent_name in {_bare_name(name) for name in trusted}:
        return None
    if not host:
        return f"refused: Origin {origin!r} is not this agent's"
    if where(sent.scheme, sent.netloc) != where(sent.scheme, host):
        return f"refused: a page of {origin} sent this, not a page of this agent ({host})"
    return None


# The methods that change something: what `cross_site_refusal` is asked of
# (#161). Another site's page may still GET (a link to the page from
# elsewhere is a cross-site GET), and cannot read the answer, which has no
# CORS header; no GET route changes anything (test_agent_cross_site.py lists
# them). Every request, a GET included, is held to `rebinding_refusal`.
STATE_CHANGING = frozenset({"POST", "PUT", "PATCH", "DELETE"})

# The reads `cross_site_refusal` is asked of too (#139): the pushed changes'
# stream, which another site's page could otherwise hold open, one of
# `events.MAX_STREAMS`, for as long as it liked. Its answer would be unreadable
# to that page (no CORS header), but the stream is a connection kept, not a
# reply sent once.
GUARDED_READS = frozenset({"/events"})


_HOST_NAME = re.compile(r"[a-z0-9_](?:[a-z0-9_-]*[a-z0-9_])?(?:\.[a-z0-9_](?:[a-z0-9_-]*[a-z0-9_])?)*")


def allowed_host_name(value: str) -> str:
    """One `--allow-host` value as the guard compares it, or `ValueError`
    saying why not (review of PR #174: `rig.vpn:8029` and `http://rig.ts.net`
    were taken and never matched). A host name alone: no scheme, port or
    path, since the name is allowed on every scheme and port, and no IP
    address, which is allowed already and would be trusted as an `Origin`
    on every port of it (`cross_site_refusal`). Lower case, any trailing
    dot gone."""
    import ipaddress

    raw = value.strip()
    if "://" in raw:
        raise ValueError(f"{value!r} has a scheme: give the host name alone (rig.ts.net, not "
                         "http://rig.ts.net); it is allowed on every scheme and port")
    try:
        ipaddress.ip_address(raw.strip("[]"))
    except ValueError:
        pass
    else:
        raise ValueError(f"{value!r} is an IP address, which is always allowed: --allow-host "
                         "takes a host name")
    name = _bare_name(raw)
    if re.fullmatch(r"[^:]+:\d*", name):
        raise ValueError(f"{value!r} has a port: give the host name alone ({name.split(':')[0]}, not "
                         f"{raw}); it is allowed on every port")
    if not _HOST_NAME.fullmatch(name) or len(name) > 253:
        raise ValueError(f"{value!r} is not a host name (letters, digits, '-', '_' and dots, "
                         "such as rig.ts.net)")
    return name


def _bare_name(name: str) -> str:
    """A host name as compared: lower case, no trailing dot (`rig.local.` is
    `rig.local`, fully qualified)."""
    return name.strip().lower().rstrip(".")


def own_names(allowed: Any = ()) -> frozenset[str]:
    """The names this agent answers to, beside any IP address:
    `localhost`, this machine's hostname (and, when that is qualified, its
    first label), that label with `.local` (its mDNS name, as a phone on the
    LAN loads the page), and `allowed` (the address `--host` bound, and each
    `--allow-host`: a tunnel's or a VPN's name for the machine). Reads the
    hostname each time, which is a system call, not a DNS lookup."""
    import socket

    names = {"localhost"}
    try:
        full = _bare_name(socket.gethostname())
    except OSError:
        full = ""
    if full:
        short = full.split(".", 1)[0]
        names |= {full, short, f"{short}.local"}
    names |= {_bare_name(name) for name in allowed if name and _bare_name(name)}
    return frozenset(names)


def rebinding_refusal(host: str | None, names: Any) -> str | None:
    """Why a request sent to a name this agent does not answer to must be
    refused, or None. Pure over the `Host` header: `names` is `own_names()`.

    DNS rebinding (#161): a page of an attacker's name, which then resolves
    to this machine, is same-origin with the agent to the browser, so it
    sends that name as `Host` and `Origin` alike and `cross_site_refusal`
    passes it. The browser cannot be made to send another `Host` than the
    name it resolved, so the agent refuses a `Host` that is not one of its
    own: an IP address (a page loaded by IP was loaded from that address,
    and there is no name to rebind) or one of `names`.

    Asked of every request, whatever its method and headers (review of PR
    #174). A rebound page could read every GET's answer, and nothing in its
    request says a browser sent it: Chromium 153 sends no `Sec-Fetch-*` to
    an origin that is not potentially trustworthy (plain http by a name
    other than localhost, as a rebound one is), and no `Origin` on a
    same-origin GET or a page load (captured 2026-11-17). So curl and the
    CLI are held to it too, and pass by the names they use: the CLI asks at
    the address the agent wrote (the bound `--host`, or 127.0.0.1). No
    `Host` at all is allowed, since a browser always sends one."""
    import ipaddress
    from urllib.parse import urlsplit

    if host is None:
        return None
    if not host or any(c in host for c in "@/?#\\ "):
        return f"refused: no usable Host ({host!r})"
    try:
        name = urlsplit(f"http://{host}").hostname
    except ValueError:
        name = None
    if not name:
        return f"refused: no usable Host ({host!r})"
    name = _bare_name(name)
    try:
        ipaddress.ip_address(name)
        return None
    except ValueError:
        pass
    if name in names:
        return None
    return (f"refused: this was sent to {name}, which is not a name this agent answers to "
            f"(an IP, localhost, this machine's hostname or its .local name); a name of the "
            f"operator's for this machine is allowed with `sushi-rig agent --allow-host {name}`")


def lifecycle_arguments(
    command: str, body: dict[str, Any] | None, *, local: bool = False, machine: str | None = None,
) -> list[str]:
    """The CLI arguments for one lifecycle request, or `AgentError`.

    Pure, and the whole of the request's influence on what runs: anything not
    read here is ignored.

    `machine` is never from a request: it is the machine file a run was
    started against, from the state file, which a switch's `up --rig`
    carries (`switch_steps`) so the next rig is wired on the same roles it
    was compiled against. An absolute path on one line, or `AgentError`.

    `discard` is a restart's: `{"discard": true}` lets it drop unsaved live
    work, which it otherwise refuses (`restart_refusal`). It changes no
    argument, since the refusal is the agent's, made before anything runs.
    """
    if body is None:
        body = {}
    if not isinstance(body, dict):
        raise AgentError("the request body must be a JSON object")
    if command not in LIFECYCLE:
        raise AgentError(f"unknown command {command!r}")

    def flag(name: str, default: bool) -> bool:
        value = body.get(name, default)
        if not isinstance(value, bool):
            raise AgentError(f"{name!r} must be true or false, not {value!r}")
        return value

    if command == "up":
        # Required, with no default: against an idle agent a stray body-less
        # POST would otherwise start a rig nobody asked for (found live,
        # 2026-11-01). `down` and `restart` act on a rig that exists; `up`
        # makes one, so it must be told which.
        if "config_name" not in body:
            raise AgentError("config_name is required: the name of a file in config/")
        name = body["config_name"]
        if not isinstance(name, str) or not CONFIG_NAME.fullmatch(name) or ".." in name:
            raise AgentError(f"invalid config name {name!r}: letters, digits, '_', '-' and '.'")
        args = ["up", name]
        if flag("rig", False):
            # A rig of channels, config/rigs/<name>.yaml, compiled at start
            # with its own patchbay (#30, #96). The same name check applies:
            # it names a file in config/rigs/, never a path.
            args.append("--rig")
        if flag("headless", False):
            args.append("--headless")
        if not flag("tuner", True):
            args.append("--no-tuner")
        if not flag("amp", True):
            args.append("--no-amp")
        if not flag("visualiser_tap", True):
            # The rig's output offered to the browser (#195, vistap.py): on
            # unless asked not to be, as on the CLI.
            args.append("--no-visualiser-tap")
        model = body.get("amp_model")
        if model is not None:
            if local and isinstance(model, str) and "/" in model:
                # `sushi-rig up --amp-model <path>` on this machine, through
                # the agent: the CLI takes a path to audition a model kept
                # anywhere, and would read it itself with no agent running.
                # Absolute, since the agent's working directory is not the
                # caller's (the CLI resolves a relative one first), and one
                # line, since it becomes one argument.
                if not model.startswith("/") or any(c in model for c in "\n\r\0"):
                    raise AgentError(f"invalid amp model path {model!r}: an absolute path, on one line")
            elif not isinstance(model, str) or not MODEL_NAME.fullmatch(model) or ".." in model:
                raise AgentError(
                    f"invalid amp model {model!r}: the name of a file in amp/models, not a path"
                    + ("" if local else " (a path is taken only from this machine)")
                )
            args += ["--amp-model", model]
        if machine is not None:
            if not isinstance(machine, str) or not machine.startswith("/") or any(c in machine for c in "\n\r\0"):
                raise AgentError(f"invalid machine file {machine!r}: an absolute path, on one line")
            args += ["--machine", machine]
        return args
    if command == "down":
        return ["down", "--force"] if flag("force", False) else ["down"]
    flag("discard", False)  # checked for its type; read by the route
    # `{"amp": true}` (#246): the restart brings the amp back on a run
    # started with --no-amp, which every restart and switch of the same rig
    # otherwise keeps (`rig.restart_arguments`). False is the default, a
    # restart as the run was asked for.
    return ["restart", "--amp"] if flag("amp", False) else ["restart"]


# --------------------------------------------------------------------------- #
# Live operations: the decisions, pure
# --------------------------------------------------------------------------- #

def _object(body: Any) -> dict[str, Any]:
    if not isinstance(body, dict):
        raise AgentError("the request body must be a JSON object")
    return body


def _name(body: dict[str, Any], key: str) -> str:
    if key not in body:
        raise AgentError(f"{key!r} is required")
    value = body[key]
    if not isinstance(value, str) or not value:
        raise AgentError(f"{key!r} must be a non-empty string, not {value!r}")
    return value


def parameter_request(body: Any) -> tuple[str, str, float]:
    """`(processor, parameter, value)` from a request body, or `AgentError`.

    The value is in Sushi's normalised domain, 0 to 1, and nothing else yet:
    real-world units (dB, Hz) come with the profiles. A bool is not a number
    here, though Python says it is, and NaN and infinity are refused by the
    range check rather than passed on to Sushi.
    """
    body = _object(body)
    processor, parameter = _name(body, "processor"), _name(body, "parameter")
    if "value" not in body:
        raise AgentError("'value' is required: a number from 0 to 1")
    value = body["value"]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AgentError(f"'value' must be a number from 0 to 1, not {value!r}")
    if not (math.isfinite(value) and 0.0 <= value <= 1.0):
        raise AgentError(f"'value' must be from 0 to 1 (normalised), not {value!r}")
    return processor, parameter, float(value)


#: The most parameters one POST /live/parameters sets: a profile's control
#: drives a handful, and the walk back on a failure is one set each.
MAX_PARAMETERS = 16


def parameters_request(body: Any) -> tuple[str, dict[str, float]]:
    """`(processor, {parameter: value})` from a request body, or `AgentError`:
    one processor and from one to `MAX_PARAMETERS` of its parameters, each
    value as `parameter_request` takes one, in the order given."""
    body = _object(body)
    processor = _name(body, "processor")
    values = body.get("parameters")
    if not isinstance(values, dict) or not values:
        raise AgentError("'parameters' is required: an object of parameter names and values from 0 to 1")
    if len(values) > MAX_PARAMETERS:
        raise AgentError(f"'parameters' names {len(values)}; at most {MAX_PARAMETERS} in one change")
    out: dict[str, float] = {}
    for name, value in values.items():
        _, parameter, number = parameter_request({"processor": processor, "parameter": name, "value": value})
        out[parameter] = number
    return processor, out


def bypass_request(body: Any) -> tuple[str, bool]:
    body = _object(body)
    processor = _name(body, "processor")
    if "bypassed" not in body:
        raise AgentError("'bypassed' is required: true or false")
    if not isinstance(body["bypassed"], bool):
        raise AgentError(f"'bypassed' must be true or false, not {body['bypassed']!r}")
    return processor, body["bypassed"]


def move_request(body: Any) -> tuple[str, str]:
    """`(processor, direction)`, the direction as the word it came as."""
    body = _object(body)
    processor = _name(body, "processor")
    direction = body.get("direction")
    if not isinstance(direction, str) or direction not in DIRECTIONS:
        raise AgentError(f"'direction' must be \"up\" or \"down\", not {direction!r}")
    return processor, direction


def _on_the_rig(chains: dict[str, list[str]], with_tracks: bool = True) -> list[str]:
    """Every processor on a chain, by name, and with `with_tracks` the tracks
    too: what a refusal offers, sorted. A refusal names only what the request
    could have meant -- a bypass cannot mean a track."""
    return sorted({
        name for track, chain in chains.items()
        for name in ([track, *chain] if with_tracks else chain)
    })


def check_parameter(reading: dict[str, Any], processor: str, parameter: str) -> float:
    """The parameter's current normalised value, or `AgentError` if the running
    rig has no such processor or parameter, or it is read-only (a meter).

    `reading` is `read_live_parameter`'s answer: the chains, that one
    processor's parameters (None if it has none because it does not exist) and
    the one value. The names offered in a refusal are the rig's own, so a typo
    can be seen.
    """
    parameters = reading.get("parameters")
    if parameters is None:
        raise AgentError(
            f"the running rig has no processor {processor!r}: it has "
            f"{_on_the_rig(reading.get('chains', {}))}"
        )
    if parameter not in parameters:
        raise AgentError(
            f"{processor!r} has no parameter {parameter!r}: it has {sorted(parameters)}"
        )
    if not parameters[parameter].get("automatable", True):
        raise AgentError(f"{processor!r} parameter {parameter!r} is read-only")
    return reading["value"]


def _refuse_fixed(processor: str, values: dict[str, Any]) -> None:
    """`AgentError` (400) for a set the rig must never take (#137): the
    master's gain above the master volume's top, its pan anywhere but the
    centre (Sushi's pan law lifts the side panned to past the limiter), or
    the master's limiter set to anything but its fixed settings. Pure."""
    for parameter, value in values.items():
        why = limiter_problem(processor, parameter, value)
        if why is None and (processor, parameter) == (ramp.MASTER, ramp.GAIN):
            why = master_gain_problem(value)
        if why is None and (processor, parameter) == (ramp.MASTER, "pan"):
            why = master_pan_problem(value)
        if why:
            raise AgentError(why)


def check_bypass(reading: dict[str, Any], processor: str) -> bool | None:
    """The processor's current bypass state (None where Sushi would not say),
    or `AgentError` if it is not a processor of the running rig. A track is
    refused: its bypass cascades to every processor on it (see `live.capture`),
    which is not what a request to bypass one pedal means.

    `reading` is `read_live_bypass`'s answer.
    """
    chains = reading.get("chains", {})
    if processor in chains:
        raise AgentError(
            f"{processor!r} is a track, not a processor on one: bypassing a track "
            "silences everything on it"
        )
    on_chains = _on_the_rig(chains, with_tracks=False)
    if processor not in on_chains:
        raise AgentError(f"the running rig has no processor {processor!r}: it has {on_chains}")
    if stereo.is_copy(processor):
        # Bypassed, it passes channel 0 alone and the right channel is silent
        # again: bypass its plugin instead, which keeps the copy's work.
        raise AgentError(
            f"{processor!r} is {stereo.source_of(processor)!r}'s copy to stereo, the compiler's: "
            f"bypass {stereo.source_of(processor)!r} instead")
    return reading.get("bypassed")


def check_move(move: Move) -> None:
    """`AgentError` if the move's processor is on no track. A processor already
    at that end of its chain is not an error and not a change: `move.outcome`
    says which, from `live.decide_move`, the one place that decides it.
    """
    if move.outcome == "absent":
        raise AgentError(
            f"the running rig has no processor {move.processor!r} on any track: it has "
            f"{_on_the_rig(move.chains, with_tracks=False)}"
        )
    if move.outcome == "protected":
        # A send or return is, or would be swapped with, the one moved (#32).
        raise Conflict(move.message)


def log_entry(
    op: str, args: dict[str, Any], previous: Any, at: str,
    undoes: int | None = None, stale: int | None = None,
) -> dict[str, Any]:
    """One operation-log entry. `previous` is what the change replaced, or None
    where it is not known; it is always present so an undo reads one shape.

    `undoes` is the index (see `pick_undo`) of the entry an undo put back, and
    `stale` that of an entry a skip gave up on. Each is a key of its own kind of
    entry alone: ordinary entries keep their four.
    """
    entry = {"at": at, "op": op, "args": args, "previous": previous}
    if undoes is not None:
        entry["undoes"] = undoes
    if stale is not None:
        entry["stale"] = stale
    return entry


def trim_log(log: list, limit: int) -> None:
    """Drop the oldest entries, in place, until `limit` remain."""
    if len(log) > limit:
        del log[: len(log) - limit]


# --------------------------------------------------------------------------- #
# Undo: the decisions, pure
# --------------------------------------------------------------------------- #

OPPOSITE = {"up": "down", "down": "up"}

# How far a parameter read back may be from the value an undo expects to find
# before it counts as changed meanwhile. Sushi holds a float32 and a value read
# back differs from the one sent in the seventh digit; this is far above that
# and far below a hand on a fader. A guess: no live Sushi was to hand.
PARAMETER_TOLERANCE = 1e-4


class StaleUndo(AgentError):
    """An entry that cannot be put back because the rig is no longer as the
    entry assumes, or never told us what to put back. The undo route answers it
    409 (`code: "stale"`), and a skip may give up on the entry. Other
    `AgentError`s keep the status the other routes give them."""


def undo_request(body: Any) -> bool:
    """Whether the request asks to skip, or `AgentError` for anything else than
    an empty body or `{"skip": true}` (or false). The route undoes the newest
    change and nothing else; a body naming an index or a processor would be
    ignored, and a request ignored while looking understood is how the wrong
    thing gets undone."""
    if body is None:
        return False
    if isinstance(body, dict) and set(body) <= {"skip"} and isinstance(body.get("skip", False), bool):
        return body.get("skip", False)
    raise AgentError(
        f"the body of an undo must be empty, or {{\"skip\": true}}, not {body!r}: "
        "it undoes the newest change")


class CantUndo(AgentError):
    """A step of the one Undo's history (#209) that cannot be put back:
    POST /undo answers it 409 `cant_undo` and drops the step."""


class UndoHeld(AgentError):
    """A step of the one Undo's history (#209) that can be put back, but not
    without the page's word: 409 with `code` (`unsaved`, `panel`: live work
    it would drop, answered with `"discard": true`; `restarts`: the switch
    would restart Sushi, answered with `"restart": true`). The step stays."""

    def __init__(self, reason: str, code: str, extra: dict[str, Any] | None = None) -> None:
        super().__init__(reason)
        self.code = code
        self.extra = extra or {}


def one_undo_request(body: Any) -> dict[str, Any]:
    """What a `POST /undo` body asks (#209), or `AgentError` (400). Every key
    is optional: `seq`, the step the page showed (`GET /undo`'s
    `next.seq`), refused as `changed` when the newest is another; `discard`,
    true to drop unsaved live work a switch back would drop; `restart`,
    false to have a switch back that would restart Sushi asked about first
    (`restarts`), true once it has been. Absent, `restart` restarts where it
    must, as a revert from curl does."""
    if body is None:
        body = {}
    if not isinstance(body, dict) or not set(body) <= {"seq", "discard", "restart"}:
        raise AgentError(f'the body of an undo is {{}}, or {{"seq": <n>, "discard"?: true, "restart"?: '
                         f"true or false}}, not {body!r}: it steps back the newest change")
    seq = body.get("seq")
    if seq is not None and (not isinstance(seq, int) or isinstance(seq, bool)):
        raise AgentError(f"'seq' must be the step's number, as GET /undo gives it, not {seq!r}")
    for key in ("discard", "restart"):
        if key in body and not isinstance(body[key], bool):
            raise AgentError(f"'{key}' must be true or false, not {body[key]!r}")
    return {"seq": seq, "discard": body.get("discard", False), "restart": body.get("restart")}


class UndoBarrier(AgentError):
    """A rig switch stands between the undo and anything older: answered 409
    `nothing_to_undo` with this reason."""


def pick_undo(log: list[dict[str, Any]], first: int) -> tuple[int, dict[str, Any]] | None:
    """`(index, entry)` of the newest entry that is not an undo or a skip and
    has been neither undone nor marked stale, or None. `first` is the index of
    `log[0]`.

    An index counts every entry recorded since the agent started, so it is
    stable as the log is trimmed at the front, which a position in the list is
    not. An undo or a stale marker is recorded after what it names, so a single
    pass from the newest end meets each before its target; and trimming, which
    drops the oldest, can lose a target and keep its marker but never the
    reverse, so an entry that is still here is never mistaken for one not yet
    dealt with.

    A save (`op: "save"`, `POST /rig/save`) is passed over: the file it
    wrote stays written, and undo goes on to the change before it, which
    leaves the rig differing from the file again (the page's unsaved list
    says so). So is a take's start or stop (`op: "record"`, S7): it changed
    nothing on the rig, and the take it made stays made.

    A rig switch (`op: "rig"`, by `/rig/switch` or a Program Change, live or
    by restart, and one that failed) is a barrier, raising
    `UndoBarrier`: what came before it was done to another rig, and the rig now
    up may have processors of the same names, so undoing it would change one
    nobody touched since. It is neither undone nor skipped past.
    """
    handled: set[int] = set()
    for position in range(len(log) - 1, -1, -1):
        entry = log[position]
        if entry["op"] == "rig":
            raise UndoBarrier(
                f"nothing to undo: a rig switch to {entry['args'].get('rig')!r} is in the way, and "
                "what came before it was done to the rig that was up then")
        if entry["op"] == "undo":
            handled.add(entry["undoes"])
        elif entry["op"] == "stale":
            handled.add(entry["stale"])
        elif entry["op"] in ("save", "record"):
            continue  # a save or a take is not undone; the change before it is
        elif first + position not in handled:
            return first + position, entry
    return None


def undo_inverse(entry: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """`(op, args)` that puts an entry back, in the arguments the route for
    that op takes, or `StaleUndo` if it cannot be done without guessing.

    A parameter goes back to its previous value and a bypass to its previous
    state. A move goes back by the opposite step, which is exact because a move
    is a step: the processor swapped places with its neighbour. Where `previous`
    is None Sushi would not say what it was, so there is nothing to put back.

    An add is undone by a remove of what it added, by the name it used; a
    remove by an add of the plugin it recorded, at its old place and with its
    values (`place_undo_remove` decides where, from the rig as it is then).
    The op returned is the route's whose path the undo takes, so an add's
    undo is `remove`; the undo's log entry names the op it undid (`of`).
    """
    op, args, previous = entry["op"], entry["args"], entry["previous"]
    if op not in ("parameter", "parameters", "bypass", "move", "add", "remove"):
        raise AgentError(f"cannot undo an entry of op {op!r}")
    if op == "add":
        return "remove", {"processor": args["name"]}
    if op == "remove":
        if not isinstance(previous, dict) or "parameters" not in previous:
            raise StaleUndo(
                f"cannot undo the remove of {args['processor']!r}: the log did not record its "
                "values, so it would come back at the plugin's defaults")
        return "add", {"track": previous.get("track"), "uri": previous.get("uri"),
                       "name": args["processor"], "parameters": dict(previous["parameters"] or {}),
                       "bypassed": previous.get("bypassed")}
    if previous is None:
        raise StaleUndo(
            f"cannot undo the {op} of {args['processor']!r}: Sushi did not say what "
            f"{args['processor']} was before it, so there is nothing to put back")
    if op == "parameter":
        return op, {"processor": args["processor"], "parameter": args["parameter"], "value": previous}
    if op == "parameters":
        unknown = sorted(k for k in args["parameters"] if not isinstance((previous or {}).get(k), (int, float)))
        if not isinstance(previous, dict) or unknown:
            raise StaleUndo(
                f"cannot undo the change of {args['processor']!r}: Sushi did not say what "
                f"{', '.join(unknown) or 'its parameters'} were before it, so there is nothing to put back")
        return op, {"processor": args["processor"],
                    "parameters": {k: previous[k] for k in args["parameters"]}}
    if op == "bypass":
        return op, {"processor": args["processor"], "bypassed": previous}
    return op, {"processor": args["processor"], "direction": OPPOSITE[args["direction"]]}


def chain_after_move(chain: list[str], processor: str, direction: str) -> list[str] | None:
    """The chain as a one-step move leaves it, or None if the processor is not
    on it or already at that end (such a move is never logged). A step swaps the
    processor with its neighbour, which is all the arithmetic undo needs. A
    plugin and its copy to stereo are one unit, and swap with a neighbouring
    unit (`stereo.step_units`), as `live.decide_move` moves them."""
    if processor not in chain:
        return None
    return stereo.step_units(chain, processor, DIRECTIONS[direction])


def check_undo_move(entry: dict[str, Any], current: list[str] | None) -> None:
    """`StaleUndo` unless `current` -- the chain the processor is on now, None
    if it is on none -- is exactly what the move left. Undoing a move whose chain
    has changed since would move the processor one step in a chain the entry knows
    nothing about, which is a different change from the one asked for."""
    args = entry["args"]
    expected = chain_after_move(entry["previous"], args["processor"], args["direction"])
    if current != expected:
        raise StaleUndo(
            f"cannot undo the move of {args['processor']!r}: it is no longer where the move "
            f"left it (the chain is now {current}, the move left {expected})")


def check_undo_state(entry: dict[str, Any], current: Any) -> None:
    """`StaleUndo` unless a parameter or bypass is still as the change left it
    (`current` is what the rig says now), so an undo never overwrites a value
    changed meanwhile -- by the panel, or by a later change another undo will
    reach first. A value Sushi would not say (None) cannot be confirmed, and is
    refused rather than assumed."""
    args = entry["args"]
    close = lambda now, left: (isinstance(now, (int, float)) and not isinstance(now, bool)  # noqa: E731
                               and math.isclose(now, left, rel_tol=0.0, abs_tol=PARAMETER_TOLERANCE))
    if entry["op"] == "parameters":
        left = args["parameters"]
        same = isinstance(current, dict) and all(close(current.get(k), v) for k, v in left.items())
    elif entry["op"] == "parameter":
        left = args["value"]
        same = close(current, left)
    else:
        left = args["bypassed"]
        same = isinstance(current, bool) and current == left
    if not same:
        raise StaleUndo(
            f"cannot undo the {entry['op']} of {args['processor']!r}: it is no longer as the "
            f"change left it (it is now {current!r}, the change left {left!r})")


def check_undo_add(entry: dict[str, Any], chains: dict[str, list[str]]) -> None:
    """`StaleUndo` unless the track an add went on is exactly as the add left
    it (`args.chain`, its read-back), as a move's undo requires of a move:
    removing the plugin from a chain that has changed since would be an edit
    of a chain the entry knows nothing about. Its copy to stereo is in that
    chain, so a copy gone astray is a chain that changed."""
    args = entry["args"]
    left = args.get("chain")
    if not isinstance(left, list):
        raise StaleUndo(
            f"cannot undo the add of {args.get('name')!r}: the log did not record where it left it")
    current = chains.get(args["track"])
    if current != left:
        raise StaleUndo(
            f"cannot undo the add of {args['name']!r}: it is no longer where the add left it "
            f"(track {args['track']!r} is now {current}, the add left {left})")


def place_undo_remove(entry: dict[str, Any], reading: dict[str, Any]) -> "Addition":
    """Where the plugin a remove took out goes back, as an `Addition` the add
    route's own command sends, or `StaleUndo` saying why its place is gone.

    `reading` is the add's: `live.read_live_chains(uids=True)`. Its place is
    after the unit that came before it in the chain the remove found (its
    `previous.units`, that chain as the remove grouped it with Sushi's uids;
    `previous.chain` grouped by name for an entry without them), with its
    copy to stereo after it if it had one, or first on the track if it was
    first. That predecessor is looked for on
    the track as it is now; if it has gone, or the track has, or the name or
    its copy's is taken, the undo is refused rather than put somewhere else:
    another place is a different chain from the one undone. What follows the
    predecessor now is not checked: the plugin goes straight after it, as it
    was. Only an LV2 plugin can be put back, since that is all an add makes
    (a stray copy to stereo, removed as a repair, is not put back).
    """
    args, previous = entry["args"], entry["previous"] or {}
    name, track = args["processor"], previous.get("track")
    uri = previous.get("uri")
    if not uri or previous.get("type") not in (None, "lv2"):
        raise StaleUndo(
            f"cannot undo the remove of {name!r}: it was not an LV2 plugin (uid {previous.get('uid')!r}), "
            "and only an LV2 plugin is put back")
    before = list(previous.get("chain") or [])
    # As the remove grouped it, with Sushi's uids (`units`); from names alone
    # only for an entry that does not say.
    recorded = previous.get("units")
    grouped = ([list(u) for u in recorded] if isinstance(recorded, list) and recorded
               else stereo.units(before))
    index = next((i for i, unit in enumerate(grouped) if unit[0] == name), None)
    if index is None:
        raise StaleUndo(f"cannot undo the remove of {name!r}: the chain it recorded does not hold it")
    copy_name = previous.get("copy") or (grouped[index][1] if len(grouped[index]) > 1 else None)
    after = grouped[index - 1][0] if index > 0 else None
    chains, ids = reading.get("chains", {}), reading.get("ids", {})
    if track not in chains:
        raise StaleUndo(f"cannot undo the remove of {name!r}: the rig has no track {track!r} now")
    chain = list(chains[track])
    if after is not None and after not in chain:
        raise StaleUndo(
            f"cannot undo the remove of {name!r}: it went after {after!r}, which is no longer on "
            f"{track!r} (it is now {chain}), and it is not put anywhere else")
    asked = AddRequest(track, uri, name, after, True, copy_name)
    try:
        addition = check_add(reading, asked)
    except Conflict as exc:
        raise StaleUndo(f"cannot undo the remove of {name!r}: {exc}") from exc
    if after is None:
        # First on the track, as it was: in front of whatever is first now.
        added = [name] + ([copy_name] if copy_name else [])
        addition = dataclasses.replace(
            addition, expected=added + chain,
            before_id=ids[chain[0]] if chain else 0, add_to_back=not chain)
    return addition


# A page's id for the visualiser tap's lease (#195): the page makes one up.
TAP_PAGE = re.compile(r"[A-Za-z0-9_-]{1,64}")


def _conflict(reason: str, code: str):
    """A 409 that carries a `code` beside its `reason`, so a client tells busy,
    nothing to undo and stale apart without reading prose."""
    exc = web.HTTPConflict(reason=reason)
    exc.agent_code = code
    return exc


def sushi_unreachable(exc: BaseException) -> bool:
    try:
        from elkpy.sushierrors import SushiUnavailableError
    except ImportError:
        return False
    return isinstance(exc, SushiUnavailableError)


# =========================================================================== #
# The library and chain edits (#32): the decisions, pure
# =========================================================================== #

class Conflict(AgentError):
    """A request the running rig cannot take as it is now (409): a name it
    already has, a track it lacks, a send that must stay where it is. Unlike a
    bad request it may succeed later, against a different rig. `code`, where
    given, goes in the answer beside the reason, for a client to act on:
    "name_taken" for an add whose name (or its copy's) the rig already has,
    which a client choosing names can choose again (#130)."""

    def __init__(self, reason: str, code: str | None = None):
        super().__init__(reason)
        self.code = code


class Unconfirmed(AgentError):
    """Sushi did not give what an edit needs or did not do what it was asked,
    as read back (502)."""


def derive_name(plugin_name: str) -> str:
    """A processor name from a plugin's name: lower case, every run of anything
    but a-z and 0-9 one '_', at most 64 characters (`save.NAME_PATTERN`, which
    a saved rig's names must match). "" when nothing is left, which
    `add_request` refuses rather than inventing a name."""
    name = re.sub(r"[^a-z0-9]+", "_", plugin_name.lower()).strip("_")
    return name[:64].rstrip("_")


@dataclass(frozen=True)
class AddRequest:
    """What an add asks for, checked for shape. `named` is whether the client
    chose `name`, which only changes what a clash tells it to do."""

    track: str
    uri: str
    name: str
    after: str | None
    named: bool
    # The copy to stereo that goes in with it (`stereo.py`), for a plugin
    # with one output: added in the same operation, straight after it.
    copy: str | None = None
    # At the start of the track's chain (`"first": true`), where `after`
    # cannot put it: there is nothing before the first plugin to follow
    # (#130, the Library drawer's slot at the start of a chain).
    first: bool = False


def add_request(body: Any, library: dict[str, Any]) -> AddRequest:
    """An `AddRequest` from a request body, or `AgentError` (400).

    `library` is `library.read_library`'s answer. The URI must be one of its
    entries -- the manifest is the list of what the rig may load -- and one
    Sushi can host (`addable`; the amp is not). With no `name`, the plugin's
    name as the library has it, made a config name (`derive_name`); a given
    name must already be one, since it is what a save will write.
    """
    body = _object(body)
    track, uri = _name(body, "track"), _name(body, "uri")
    entries = {entry["uri"]: entry for entry in library.get("plugins", [])}
    entry = entries.get(uri)
    if entry is None:
        raise AgentError(
            f"{uri!r} is not in plugin-manifest.txt, which lists every plugin the rig "
            f"may load: GET /library lists its {len(entries)}")
    if not entry.get("addable", True):
        raise AgentError(f"{entry['name']} cannot be added to a Sushi chain: {entry.get('why_not')}")
    named = body.get("name") is not None
    if named:
        name = body["name"]
        if not isinstance(name, str) or not NAME_PATTERN.fullmatch(name):
            raise AgentError(f"'name' must match {NAME_PATTERN.pattern}, not {name!r}")
    else:
        name = derive_name(entry["name"])
        if not NAME_PATTERN.fullmatch(name):
            raise AgentError(
                f"no processor name can be made from {entry['name']!r}: give it a 'name' "
                f"matching {NAME_PATTERN.pattern}")
    reserved = stereo.reserved_problem("processor", name)
    if reserved:
        raise AgentError(reserved)
    if name in (rigfile.MASTER, rigfile.RETURN, rigfile.LIMITER):
        # The compiler's names, refused as `check_rig` refuses them (#137,
        # PR #172's review): on a rig from before the limiter, a plugin added
        # as `master_limiter` would be protected by its name, so it could not
        # be removed or bypassed, and a save could not write it.
        raise AgentError(
            f"{name!r} is a name the compiler gives the master track, its return or its limiter: "
            "give the plugin another name")
    after = body.get("after")
    if after is not None and (not isinstance(after, str) or not after):
        raise AgentError(f"'after' must name a processor on the track, or be left out, not {after!r}")
    first = body.get("first", False)
    if not isinstance(first, bool):
        raise AgentError(f"'first' must be true or false, or be left out, not {first!r}")
    if first and after is not None:
        raise AgentError("give 'after' or 'first', not both: 'first' puts it at the start of the chain")
    # Which plugins are mono is the compiler's table (plugin-ports.txt), read
    # into the library: the same answer `up --rig` gives, so a plugin added
    # live is what a restart of the saved rig would be.
    audio = entry.get("audio")
    if not isinstance(audio, dict):
        raise AgentError(
            f"{entry['name']} ({uri}) is not in {stereo.PORTS_FILE}, so whether it is mono is not known "
            f"and it is not added: `sushi-rig probe {uri}` prints its audio ports for the table")
    if audio.get("out") == 0:
        raise AgentError(f"{entry['name']} has no audio output: on a track it would silence everything after it")
    return AddRequest(track, uri, name, after, named,
                      stereo.copy_name(name) if audio.get("out") == 1 else None, first)



def _track_of(chains: dict[str, list[str]], processor: str) -> str | None:
    return next((track for track, chain in chains.items() if processor in chain), None)


@dataclass(frozen=True)
class Addition:
    """An add decided on: where it goes, and the command that puts it there.

    `chain` is the track's before, `expected` after. Sushi places a processor
    "in front of" another or "at the back", so `before_id` is the id of the one
    it goes in front of, or 0 with `add_to_back`; both from the reading the
    decision was made on, as a move's are.
    """

    track: str
    name: str
    uri: str
    after: str | None
    chain: list[str]
    expected: list[str]
    track_id: int
    before_id: int
    add_to_back: bool
    # Its copy to stereo, added straight after it in the same operation
    # (`AddRequest.copy`); in `expected` already.
    copy: str | None = None


def _unit_end(chain: list[str], name: str, uids: dict[str, Any]) -> int:
    """The index just past `name`'s unit: past its copy to stereo when it
    has one, so nothing is placed between a plugin and its copy."""
    for unit in stereo.units(chain, uids):
        if name in unit:
            return chain.index(unit[-1]) + 1
    return chain.index(name) + 1


def check_add(reading: dict[str, Any], request: AddRequest) -> Addition:
    """Where `request` goes on the running rig, or `Conflict` (409).

    `reading` is `live.read_live_chains(uids=True)`'s: `{"chains", "ids",
    "uids"}`. The send is known by `live.is_send`, by its uid and its name, as a
    remove and a move know it: by name alone, an add on a rig whose send was
    called something else went in behind it, was confirmed, logged, and not
    heard (found by review). The name must
    be free across the whole rig -- Sushi's processor names are one namespace,
    tracks included. With no `after` it goes last, but in front of the
    track's send when it has one, or the master's limiter (#137); `after` the
    send is refused, because the send carries the channel to the master and
    whatever follows it is not heard, and after the limiter, which stays the
    last thing the monitors' signal passes. The master's return is first on its track and stays first: an add
    is always after something or at the back, and one asked to go `first`
    goes in front of the first processor that is not the return, which on a
    channel is its first plugin, or its send when it has none.
    """
    chains, ids = reading.get("chains", {}), reading.get("ids", {})
    uids = reading.get("uids") or {}
    track, name, after = request.track, request.name, request.after
    if track not in chains:
        raise Conflict(f"the running rig has no track {track!r}: it has {sorted(chains)}")
    if name in chains:
        raise Conflict(f"{name!r} is a track of the running rig; give the processor another name", "name_taken")
    holder = _track_of(chains, name)
    if holder is not None:
        what = "give a 'name'" if not request.named else "give another name"
        raise Conflict(f"{name!r} is already on the rig, on track {holder!r}: {what}", "name_taken")
    if request.copy is not None and (request.copy in chains or _track_of(chains, request.copy) is not None):
        raise Conflict(f"{request.copy!r}, the name of {name!r}'s copy to stereo, is already on the rig", "name_taken")
    chain = list(chains[track])
    # What ends the chain and stays last: a channel's send, or the master's
    # limiter (#137, `live.is_tail`).
    sends = [index for index, held in enumerate(chain) if is_tail(track, held, uids.get(held))]
    if after is not None:
        if after not in chain:
            raise Conflict(f"there is no processor {after!r} on track {track!r}: it has {chain}")
        if chain.index(after) in sends:
            if is_send(track, after, uids.get(after)):
                raise Conflict(
                    f"nothing goes after {after!r}: the send must stay last, since it is what "
                    "carries the channel to the master")
            raise Conflict(
                f"nothing goes after {after!r}: the master's limiter stays last, since it is the "
                "safety net on what the monitors hear (#137)")
        index = _unit_end(chain, after, uids)
    elif request.first:
        # Past the processors at the head that are the compiler's and not a
        # tail: the master's return (`protected_processor`). A send or the
        # limiter is last, and on a track with no plugins the add goes in
        # front of it.
        index = 0
        while (index < len(chain) and index not in sends
               and protected_processor(track, chain[index], uids.get(chain[index])) is not None):
            index += 1
    else:
        index = sends[0] if sends else len(chain)
    before = chain[index] if index < len(chain) else None
    added = [name] + ([request.copy] if request.copy else [])
    return Addition(
        track=track, name=name, uri=request.uri, after=after, chain=chain,
        expected=chain[:index] + added + chain[index:],
        track_id=ids[track], before_id=ids[before] if before is not None else 0,
        add_to_back=before is None, copy=request.copy,
    )


def remove_request(body: Any) -> str:
    """The processor a remove names, or `AgentError` (400)."""
    return _name(_object(body), "processor")


@dataclass(frozen=True)
class Removal:
    """A remove decided on. `plugin` is `{"uri", "uid", "type"}` as Sushi's
    session gave it, which with `track` and `chain` is what an undo needs to
    put the processor back where it was."""

    processor: str
    track: str
    chain: list[str]
    expected: list[str]
    plugin: dict[str, Any]
    processor_id: int
    track_id: int
    # Its copy to stereo, removed with it, first (`stereo.py`): one left
    # behind would follow whatever came before, folding a stereo plugin.
    # `copy_before_id` is what followed the copy (None: the back), where it
    # goes back if the plugin's own remove fails.
    copy: str | None = None
    copy_id: int | None = None
    copy_before_id: int | None = None
    # The chain before, as units (`stereo.units` with the session's uids):
    # logged, so an undo groups it as the remove did rather than again from
    # names alone (review of #32).
    units: tuple[tuple[str, ...], ...] = ()


def check_remove(reading: dict[str, Any], processor: str) -> Removal:
    """What removing `processor` from the running rig is, or `Conflict` (409)
    for a track, a send or return, or a processor the rig does not have; or
    `Unconfirmed` (502) when Sushi would not say which plugin it is.

    `reading` is `live.read_live_removal`'s: `{"chains", "ids", "plugin"}`.
    A remove that could not say what it removed could not be undone, so it is
    not made: refusing is the loud failure, removing anyway the silent one.
    """
    chains, ids = reading.get("chains", {}), reading.get("ids", {})
    if processor in chains:
        raise Conflict(f"{processor!r} is a track, not a processor on one")
    track = _track_of(chains, processor)
    if track is None:
        raise Conflict(
            f"the running rig has no processor {processor!r}: it has "
            f"{_on_the_rig(chains, with_tracks=False)}")
    plugin = reading.get("plugin")
    uid = (plugin or {}).get("uid") or (reading.get("uids") or {}).get(processor)
    protected = protected_processor(track, processor, uid)
    if protected and stereo.is_copy(processor, uid):
        unit = next(u for u in stereo.units(list(chains[track]), reading.get("uids") or {}) if processor in u)
        if unit[0] != processor:
            raise Conflict(f"{protected}, removed with {stereo.source_of(processor)!r}: remove that instead")
        # A copy with no plugin before it: a stray, left by a two-command edit
        # that failed half way and could not be undone. Removing it is the
        # repair, so it is allowed (review, 2026-11-02).
    elif protected:
        raise Conflict(f"{protected}: the compiler's, and never removed")
    if not plugin or not (plugin.get("uri") or plugin.get("uid")):
        raise Unconfirmed(
            f"Sushi could not say which plugin {processor!r} is (its session lists no "
            "URI or uid for it), so it is not removed: the log could not say how to "
            "put it back")
    chain = list(chains[track])
    grouped = stereo.units(chain, reading.get("uids") or {})
    unit = next(u for u in grouped if u[0] == processor)
    copy = unit[1] if len(unit) > 1 else None
    after = chain.index(unit[-1]) + 1
    return Removal(
        processor=processor, track=track, chain=chain,
        expected=[name for name in chain if name not in unit],
        plugin={key: plugin.get(key) for key in ("uri", "uid", "type")},
        processor_id=ids[processor], track_id=ids[track],
        copy=copy, copy_id=ids[copy] if copy else None,
        copy_before_id=ids[chain[after]] if copy and after < len(chain) else None,
        units=tuple(tuple(u) for u in grouped),
    )


# Where a request keeps the call it stopped waiting for when it answers a
# timeout as something other than the 504 (`_keep_pending`), so `_connected`
# still closes the connection only once that call returns.
# A typed key where aiohttp has them (3.11 on), so it does not warn; the
# agent extra allows 3.9, where a string key is what there is.
KEPT_PENDING: Any = (web.RequestKey("sushi_rig.agent.pending", object)
                     if web is not None and hasattr(web, "RequestKey") else "sushi_rig.agent.pending")

# Where a request keeps what the master's dip around its edit did (`Agent._dip`),
# for the route's answer: the dip is never logged, so the answer is where it
# is said. The same typed-key rule as above.
DIP_SAID: Any = (web.RequestKey("sushi_rig.agent.dip", dict)
                 if web is not None and hasattr(web, "RequestKey") else "sushi_rig.agent.dip")


def _dip_said(request: Any) -> dict[str, Any] | None:
    """What the dip around this request's edit did, if one was tried."""
    try:
        return request.get(DIP_SAID)
    except (AttributeError, TypeError):  # not an aiohttp request (a pedal's origin)
        return None


def _dip_answer(request: Any) -> dict[str, Any]:
    """`{"dip": ...}` for an edit's answer, beside its log entry and never in
    it, and `warning` when the master could not be put back; `{}` where no
    dip was tried (a move that sent nothing)."""
    said = _dip_said(request)
    if said is None:
        return {}
    answer: dict[str, Any] = {"dip": dict(said)}
    if said.get("restored") is False:
        answer["warning"] = said["why"]
    elif said.get("capped"):
        # The master came back quieter than it went down (#137): said beside
        # the edit's answer, not only inside `dip`.
        answer["master_capped"] = said["capped"]["message"]
    return answer


def _keep_pending(request: Any, exc: BaseException) -> None:
    """Keep a timed-out call's future on the request (the first one: one
    connection, closed when it returns), for an `except` that handles a 504
    and answers otherwise."""
    pending = getattr(exc, "pending", None)
    if pending is None:
        return
    try:
        request.setdefault(KEPT_PENDING, pending)
    except (AttributeError, TypeError):  # not an aiohttp request (a pedal's origin)
        pass


def _kept_pending(request: Any) -> Any:
    try:
        return request.get(KEPT_PENDING)
    except (AttributeError, TypeError):
        return None


# What a call to Sushi that failed part-way through a sequence of them is
# answered as (`Agent._sushi`): refused or failed (502), not answering in
# time (504), or a pool of calls that never returned (503). The steps after
# an add catch all three, since each leaves the plugin on the rig.
SUSHI_FAILED = (502, 503, 504)


def removed_record(removal: Removal, values: dict[str, Any]) -> dict[str, Any]:
    """A remove's `previous`: where the processor was (`track`, the `chain`
    before), which plugin it was (`uri`, `uid`, `type`, from Sushi's
    session), its `copy` to stereo (or None), and how it was set
    (`parameters`, `bypassed`, read just before the remove): what an undo
    needs to put it back as it was."""
    return {"track": removal.track, "chain": list(removal.chain), "units": [list(u) for u in removal.units],
            **removal.plugin, "copy": removal.copy,
            "parameters": dict(values.get("parameters") or {}), "bypassed": values.get("bypassed")}


def confirm_chain(chains: dict[str, list[str]], track: str, expected: list[str], what: str) -> None:
    """`Unconfirmed` (502) unless `track`'s chain, read back after an edit, is
    `expected`. Sushi's acknowledgement says it carried out a command; the
    read-back says the command did what the agent meant, and only that is
    logged. A chain someone else changed in between (the panel's listener is
    not behind the agent's lock) fails here too, which is the honest answer."""
    if track not in chains:
        raise Unconfirmed(f"Sushi confirmed it {what}, but the rig read back has no track {track!r}")
    if chains[track] != expected:
        raise Unconfirmed(
            f"Sushi confirmed it {what}, but {track!r} read back {chains[track]}, not "
            f"{expected}: read /live before trying again")


# =========================================================================== #
# (end of the library and chain edits' decisions)
# =========================================================================== #


def connect_quietly(address: str) -> Any:
    """`live.connect`, with elkpy's per-connection prints silenced first
    (`live.quiet_elkpy`, which does its work once and never raises). Here,
    on the first connection, rather than at the agent's start, so an agent
    that never reaches Sushi never imports elkpy."""
    live_module.quiet_elkpy()
    return live_module.connect(address)


@dataclass(frozen=True)
class Live:
    """Everything the live routes need from Sushi, each a blocking call. A
    request makes one connection (`connect`), does all its reading and changing
    on it, and closes it (`disconnect`): each connection builds a dozen
    sub-controllers, makes an RPC and starts a notification thread, so a read
    and a change on separate ones paid that twice for nothing. Every other
    call takes the connection first.

    The reads are targeted -- they cost what the request names, not a walk of
    every parameter of the rig -- and their answers are what the `check_*`
    functions decide on. Defaults to `live.py`.

    One connection per request, not one held for the agent's life: a held one
    would need reconnecting when Sushi restarts (which `up`, `down` and
    `restart` do on purpose) and the failure to reconnect would be state that
    outlives the request that met it. Not run against a live Sushi.
    """

    connect: Callable[[], Any]
    disconnect: Callable[[Any], None]
    snapshot: Callable[[Any], dict[str, Any]]  # capture + "parameter_info"
    parameter: Callable[[Any, str, str], dict[str, Any]]  # (processor, parameter) -> reading
    bypass: Callable[[Any, str], dict[str, Any]]  # (processor) -> reading
    plan_move: Callable[[Any, str, int], Move]  # decides, sends nothing
    set_parameter: Callable[[Any, str, str, float], None]
    set_bypass: Callable[[Any, str, bool], None]
    send_move: Callable[[Any, Move], Move]  # sends a planned move
    # -- the chain edits (#32) --
    walk: Callable[[Any], dict[str, Any]]  # {"chains", "ids"}: an add's reading, and every read-back
    removal: Callable[[Any, str], dict[str, Any]]  # (processor) -> the walk plus its "plugin"
    add: Callable[[Any, Addition], None]  # sends a decided add
    remove: Callable[[Any, Removal], None]  # sends a decided remove
    # (processor) -> {"parameters", "bypassed"}: read before a remove, for its
    # undo, and after an add, the plugin's defaults, for a save
    values: Callable[[Any, str], dict[str, Any]]
    # -- the web UI (#31): which plugin each processor is; None where it cannot say --
    plugins: Callable[[Any], dict[str, Any]] | None = None
    # -- a pedal's Sushi mapping applied live (#210), each a cc_mappings entry;
    # None (or a stand-in without them) is "only at a restart". UNVERIFIED
    # on a real Sushi: live.py's "A pedal's mapping, live".
    connect_cc: Callable[[Any, dict[str, Any]], None] | None = None
    disconnect_cc: Callable[[Any, dict[str, Any]], None] | None = None
    cc_connections: Callable[[Any], list[dict[str, Any]]] | None = None
    # -- a channel removed from the running rig (#190): (track) -> None, the
    # track and everything on it; None is "only at a restart". `at` gives
    # None: see there, and live.py's `remove_live_track`.
    remove_track: Callable[[Any, str], None] | None = None

    @classmethod
    def at(cls, address: str = DEFAULT_GRPC_ADDRESS) -> "Live":
        m = live_module
        return cls(
            connect=functools.partial(connect_quietly, address),  # #111: elkpy's prints
            disconnect=m.close_controller,  # not c.close(): see close_controller
            snapshot=lambda c: m.read_rig(address, c),
            parameter=lambda c, proc, name: m.read_live_parameter(proc, name, address, c),
            bypass=lambda c, proc: m.read_live_bypass(proc, address, c),
            # uids: a send is known by Sushi's uid as well as its name (#32).
            plan_move=lambda c, proc, direction: m.plan_live_move(proc, direction, address, c, uids=True),
            set_parameter=lambda c, proc, name, value: m.set_live_parameter(proc, name, value, address, c),
            set_bypass=lambda c, proc, state: m.set_live_bypass(proc, state, address, c),
            send_move=lambda c, move: m.send_live_move(move, address, c),
            # -- the chain edits (#32) --
            walk=lambda c, uids=False: m.read_live_chains(address, c, uids=uids),
            removal=lambda c, proc: m.read_live_removal(proc, address, c),
            add=functools.partial(_add_with_copy, address),
            remove=functools.partial(_remove_with_copy, address),
            values=lambda c, proc: m.read_live_values(proc, address, c),
            plugins=lambda c: m.read_live_plugins(address, c),  # the web UI (#31)
            connect_cc=lambda c, mapping: m.connect_live_cc(mapping, address, c),  # #210
            disconnect_cc=lambda c, mapping: m.disconnect_live_cc(mapping, address, c),
            cc_connections=lambda c: m.read_live_cc(address, c),
            # A channel's removal is a restart, asked first (#190, PR #238's
            # review). Live, through `m.remove_live_track`, it was run against
            # Sushi 1.3.0 some nine hundred times (the probe in
            # tool/spikes/live_remove_track/) and mostly works, but not always: 3 runs in
            # 300 had Sushi stop at `delete_track`, neither applying nor
            # confirming it, then ignore SIGTERM; and a command sent soon
            # after connecting can be applied and never confirmed (elkpy's
            # notice), which fails the step and restarts the rig unasked.
            # The path stays, for a stand-in Sushi in the tests and for a
            # Sushi that can be shown to do it every time.
            remove_track=None,
        )


def _add_with_copy(address: str, controller: Any, addition: "Addition") -> None:
    """Add the plugin, then its copy to stereo in the same place, so the copy
    lands straight after it: in front of the same processor, or at the back
    after it. Two of Sushi's commands, each strict; the read-back that
    follows confirms both or neither. If the copy cannot be added, the plugin
    is removed again, so a plugin is never left without its copy; if that
    fails too, the error says what the chain holds (review, 2026-11-02)."""
    m = live_module
    m.add_live_processor(addition.name, addition.uri, addition.track_id, addition.before_id,
                         addition.add_to_back, address, controller)
    if not addition.copy:
        return
    try:
        m.add_live_copy(addition.copy, addition.track_id, addition.before_id, addition.add_to_back,
                        address, controller)
    except Exception as exc:  # noqa: BLE001 -- undone, then said
        try:
            m.remove_live_processor_named(addition.name, addition.track_id, address, controller)
        except Exception as again:  # noqa: BLE001
            raise LiveError(
                f"added {addition.name!r} but not its copy to stereo {addition.copy!r} ({exc}), and "
                f"removing {addition.name!r} again failed too ({again}): {addition.name!r} is on "
                f"{addition.track!r} without its copy, so the right channel is silent after it. "
                f"Remove {addition.name!r}, or restart the rig") from again
        raise LiveError(
            f"added {addition.name!r} but not its copy to stereo {addition.copy!r} ({exc}), so "
            f"{addition.name!r} was removed again: nothing was added") from exc


def _remove_with_copy(address: str, controller: Any, removal: "Removal") -> None:
    """Remove the copy to stereo first, then its plugin: the moment between
    has the right channel silent, which is what the rig had before the copy
    existed, rather than a lone copy folding whatever stereo came before. If
    the plugin cannot then be removed, the copy is put back where it was;
    if that fails too, the error says what the chain holds."""
    m = live_module
    if removal.copy_id is None:
        m.remove_live_processor(removal.processor_id, removal.track_id, address, controller)
        return
    m.remove_live_processor(removal.copy_id, removal.track_id, address, controller)
    try:
        m.remove_live_processor(removal.processor_id, removal.track_id, address, controller)
    except Exception as exc:  # noqa: BLE001 -- undone, then said
        try:
            m.add_live_copy(removal.copy, removal.track_id, removal.copy_before_id or 0,
                            removal.copy_before_id is None, address, controller)
        except Exception as again:  # noqa: BLE001
            raise LiveError(
                f"removed {removal.processor!r}'s copy to stereo {removal.copy!r} but not "
                f"{removal.processor!r} ({exc}), and putting the copy back failed too ({again}): "
                f"{removal.processor!r} is on {removal.track!r} without its copy, so the right channel "
                f"is silent after it. Remove {removal.processor!r} again, or restart the rig") from again
        raise LiveError(
            f"removed {removal.processor!r}'s copy to stereo {removal.copy!r} but not "
            f"{removal.processor!r} ({exc}), so the copy was put back: nothing was removed") from exc


class Agent:
    """The routes and the one piece of state they share: whether a lifecycle
    command is running, and the last thing that went wrong (until a lifecycle
    command next succeeds).

    Everything that touches the world is injectable, so the routes are tested
    without a rig, a PipeWire or a child process: `describe` answers for the
    state file, `doctor` for the examination, `supervisor` for the rig's
    children and `run` for one lifecycle step (the supervisor's own runner
    unless given), `live` for Sushi's gRPC, `pw_top` for GET /rig/load's
    pw-top run, `recorder` for the recorder's socket (anything with
    `recorder_process.RecorderLink`'s `ask`) and `takes_dir` for where takes go.
    """

    def __init__(
        self,
        *,
        root: Path | None = None,
        describe: Callable[[], dict[str, Any]] = describe_rig,
        doctor: Callable[[], list] | None = None,
        run: Runner | None = None,
        supervisor: Supervisor | None = None,
        live: Live | None = None,
        clock: Callable[[], float] = time.monotonic,
        log_limit: int = LOG_LIMIT,
        library: Callable[[], dict[str, Any]] | None = None,
        midi: midi_listener.World | None = None,
        switching: "Switching | None" = None,
        pw_top: Callable[[float], str] | None = None,
        carla: "amp_live.Carla | None" = None,
        recorder: Any = None,
        takes_dir: Path | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        allow_hosts: Sequence[str] = (),
        bound: str | None = None,
    ) -> None:
        self.root = root
        # Pushed changes (#139): the open /events streams. First, since
        # setting `busy` below already publishes.
        self.events = events_module.Hub()
        # Names a browser may reach this agent by beside its own (#161,
        # `own_names`): each `--allow-host`, which is also an `Origin` the
        # agent trusts behind a proxy (`cross_site_refusal`), and the address
        # `--host` bound, which is not. A value with a port or a scheme is
        # refused here, as at the command line (`allowed_host_name`).
        try:
            self.allow_hosts = tuple(allowed_host_name(name) for name in allow_hosts)
        except ValueError as exc:
            raise AgentError(f"--allow-host {exc}") from exc
        self.bound = bound
        self.log_limit = log_limit
        self.describe = describe
        self.doctor = doctor or self._doctor
        self.supervisor = supervisor or Supervisor()
        # A crash the supervisor sees is pushed to the open pages (#139).
        self.supervisor.changed = lambda: self.events.publish("supervisor")
        # A lifecycle step is CLI arguments (`lifecycle_arguments`), run by the
        # supervisor in this process (#29); a test stands in a runner instead.
        self.run = run or self.supervisor.run
        self.live = live or Live.at()
        self.clock = clock
        self.started = clock()
        self.started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.last_error: dict[str, Any] | None = None
        self.busy = None  # the lifecycle command running, if one is (a property: a change is pushed)
        self._lock = asyncio.Lock()  # lifecycle: one command at a time
        self._live_lock = asyncio.Lock()  # live: one change at a time; waits, not 409. Reads do not take it
        self.log: list[dict[str, Any]] = []  # operations made through /live, newest last
        # The log's adds, removes, their undos, and the saves and switches that
        # bound them: what a save needs (`live_edits`), kept apart from the log
        # so trimming the log for the page never loses a plugin taken out
        # hours of knob moves ago. Cut at each save or switch, so it holds only
        # the running rig's own chain edits since then.
        self._chain_log: list[dict[str, Any]] = []
        self._trimmed = 0  # entries dropped from the front, so log[i] has index _trimmed + i
        # One Undo (#209): what the page's Undo button steps back through,
        # newest last; see history.py and the "One Undo" block.
        self.history = history_module.History(clock=clock)
        self._undo_lock = asyncio.Lock()  # one Undo at a time
        # Sushi calls run here (see LIVE_WORKERS), not in the loop's executor.
        self._executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=LIVE_WORKERS, thread_name_prefix="sushi-live")
        self._in_flight: set = set()  # calls to Sushi that have not returned
        self._abandoned: set = set()  # ... of which the agent has stopped waiting for these
        # The library (#32): built on first use and kept; see `_library_now`.
        self._build_library = library or self._read_library
        self._library: dict[str, Any] | None = None
        self._library_lock = asyncio.Lock()
        # GET /live's read of Sushi in flight, shared (#139,
        # `_live_shared_read`): (events.seq as it began, its task).
        self._live_shared: tuple | None = None
        self._midi_init(midi)  # the MIDI listener's state: see the MIDI section
        self.switching = switching or Switching()  # the live switch (#93): see its section
        self._amp_init(carla)  # the amp: see its block
        self._record_init(recorder, takes_dir)  # recording (S7): see its block
        # #111: what the agent says in place of elkpy's dropped prints (see
        # "Quiet"; they are dropped on the first connection, `connect_quietly`):
        # a Sushi gone while the rig is up, said once an outage, and the
        # /health record of it, which is cleared when Sushi answers again.
        self._said_unreachable: str | None = None
        self._unreachable: dict[str, Any] | None = None
        self._said_load: str | None = None
        # GET /rig/load's one effect, `pw-top -b -n 2` with a deadline; a
        # test stands in its output (or its `LoadError`).
        self.pw_top = pw_top or top_module.read_pw_top
        self._load_run: asyncio.Future | None = None  # the pw-top run in flight, shared
        self._load_last: tuple[float, str] | None = None  # (clock, output) of the last that worked
        # Bumped by every structural add (#137, `_load_stale`): a sample, or a
        # run, from before one says nothing of the load with it in.
        self._load_gen = 0
        # The master's dip around a structural edit (#137, `_dip`): the wait
        # after the gain goes down, which a test stands in, and the level the
        # dip will put back while one is under way, which GET /live shows.
        self.sleep = sleep
        self._dipping: float | None = None
        self._dips = 0  # dips begun, so a read can tell one began and ended under it
        self._dip_left: float | None = None  # the level the last dip left the master at
        # A master a dip could not put back (#137, review of PR #150): kept
        # apart from `last_error`, which any later change replaces or clears,
        # until a dip puts a level back, the master volume is set through the
        # agent, or a lifecycle command succeeds. `{"at", "message", "level"}`,
        # `level` the one the dip meant to put back, which a save writes.
        self._master_down: dict[str, Any] | None = None
        # The last time a dip brought a master over the master volume's top
        # down to it (#137, PR #172's review): `{"at", "from_db", "message"}`,
        # in /health until a lifecycle command works (a rig started from a
        # file is at 0 dB at most).
        self._master_capped: dict[str, Any] | None = None
        # The rig's block as pw-top last measured it, in seconds, for the
        # dip's wait (`ramp.settle`); kept from every sample read, since a
        # sample itself is dropped after an add (`_load_last`).
        self._block_s: float | None = None

    @property
    def stuck_live_calls(self) -> int:
        """Calls to Sushi the agent gave up waiting for that have not returned."""
        return len(self._abandoned)

    def _doctor(self) -> list:
        from .doctor import run
        from .paths import checkout_root

        # The crashes this agent saw, so a node of the rig's own that is
        # absent is said as the crash it is, not as a rig that is down.
        return run(self.root or checkout_root(), crashed=list(self.supervisor.crashed),
                   tap=self.supervisor.tap_report())

    # ----------------------------------------------------------------- routes

    def app(self):
        # The guard inside `_json_errors`, so its 403 is JSON as every refusal
        # is; `_push_changes` inside the guard, so a refused write pushes nothing.
        app = web.Application(middlewares=[self._json_errors, self._same_site_only, self._push_changes])
        self._events_routes(app)  # pushed changes (#139): GET /events, see its block
        app.router.add_get("/health", self.health)
        app.router.add_post("/visualiser/tap", self.visualiser_tap_route)
        app.router.add_post("/visualiser/tap/release", self.visualiser_tap_release_route)
        app.router.add_get("/rig", self.rig)
        app.router.add_get("/doctor", self.doctor_route)
        for command in LIFECYCLE:
            app.router.add_post(f"/rig/{command}", self.lifecycle)
        app.router.add_get("/live", self.live_state)
        app.router.add_get("/live/log", self.live_log)
        app.router.add_post("/live/parameter", self.live_parameter)
        app.router.add_post("/live/parameters", self.live_parameters)
        app.router.add_post("/live/bypass", self.live_bypass)
        app.router.add_post("/live/move", self.live_move)
        app.router.add_post("/live/undo", self.live_undo)
        self._edit_routes(app)
        self._switch_routes(app)  # the live switch (#93)
        self._midi_routes(app)  # GET /midi, the pedals and their assignments (#191)
        self._ui_routes(app)  # the web UI (#31): see its block
        self._amp_routes(app)  # the amp: see its block
        self._save_routes(app)  # saving the running rig (S4): see its block
        self._record_routes(app)  # recording from the web UI (S7): see its block
        self._rigfile_routes(app)  # the New rig screen's rig files: see its block
        self._revert_routes(app)  # revert to a previous save: see its block
        self._bin_routes(app)  # deleting a rig file into the bin, and Undo (#129): see its block
        self._undo_routes(app)  # One Undo for everything (#209): see its block
        self._channel_load_routes(app)  # a channel from another rig (#208): see its block
        self._channel_remove_routes(app)  # a channel removed from the running rig (#190): see its block
        self._help_routes(app)  # the user guide at /help/ (#167): see its block
        return app

    async def health(self, request):
        rig = self.describe()
        if not rig.get("running"):
            self._unreachable_over()  # a rig that is down has no Sushi to miss (#111)
        return web.json_response({
            "ok": True,
            "agent": {
                "version": VERSION,
                "started_at": self.started_at,
                "uptime_seconds": round(self.clock() - self.started, 1),
                "busy": self.busy,
                "stuck_live_calls": self.stuck_live_calls,
                "last_error": self.last_error,
                # A master a dip could not put back (#137): until it is put right.
                "master_down": self._master_down,
                # A master over 0 dB a dip brought down to it (#137).
                "master_capped": self._master_capped,
                "midi": self._midi_status(),
                # What `up` warned of as the running rig came up (#246), and
                # whether its amp is wired (`_amp_wiring`): an amp that is
                # missing or unwired looks healthy by every other measure.
                "warnings": self._run_warnings(rig),
                # The last look, without waiting on a new one (`wait`).
                "amp_wiring": await self._amp_wiring(rig, wait=False),
                # The rig this agent holds, and its children that exited with
                # no `down` asking them to: what a phone reads after a crash.
                "supervisor": self.supervisor.describe(rig),
                # Where it listens, and the URLs a phone opens (#181): what
                # the rack's "Agent on" line says.
                "listen": self.listen_status(),
                # The takes folder, for the rack's line naming it (#189), and
                # whether this request came from this machine, the one peer
                # POST /takes/open serves: only then is Open folder offered.
                "takes": self._takes_folder(request),
            },
            "rig": rig,
        })

    # Where `serve` bound this agent (#181), set by it before the app starts:
    # None for an Agent no `serve` started (the tests'), which `/health`
    # says as `listen: null`. The interfaces are looked at again when
    # `listen_status` is asked after LISTEN_REFRESH seconds, since joining
    # the phone's hotspot changes the address while the agent runs.
    listening: tuple[str, int] | None = None
    LISTEN_REFRESH = 5.0
    _listen_seen: tuple[float, list] | None = None
    # What finds the interfaces (`reach.interfaces` when None); the tests'
    # stand-in for a laptop's Wi-Fi and ethernet.
    listen_interfaces: Callable[[], tuple[list, list]] | None = None

    def listen_status(self) -> dict | None:
        """`{host, port, network, urls}` for `/health`, or None when no
        `serve` said where it listens. `urls` is `reach.phone_urls`: one
        `{url, interface, address, kind}` for each address a phone may open,
        empty when the agent listens on localhost alone."""
        from . import reach

        if self.listening is None:
            return None
        host, port = self.listening
        network = reach.is_network(host)
        found: list = []
        if network:
            now = self.clock()
            if self._listen_seen is None or now - self._listen_seen[0] >= self.LISTEN_REFRESH:
                self._listen_seen = (now, (self.listen_interfaces or reach.interfaces)()[0])
            found = self._listen_seen[1]
        return {"host": host, "port": port, "network": network,
                "urls": reach.phone_urls(host, port, found)}

    async def rig(self, request):
        return web.json_response(await self._rig_answer())

    async def doctor_route(self, request):
        from .doctor import FAULT, UNCHECKED

        # pw-dump and the environment probes block; off the loop they go.
        checks = await asyncio.get_running_loop().run_in_executor(None, self.doctor)
        return web.json_response({
            "ok": not any(c.state == FAULT for c in checks),
            "faults": sum(c.state == FAULT for c in checks),
            "unchecked": sum(c.state == UNCHECKED for c in checks),
            "checks": [c.as_dict() for c in checks],
        })

    def _tap_page(self, request: Any, body: Any, keys: set) -> str:
        """The page a tap request is for, after the checks both tap routes
        make: a loopback peer (403 `not_local`: the visualiser runs only on
        the rig's own laptop), and a body of `keys` with a `page` that is a
        short id (400)."""
        if not is_local(request.remote):
            refused = web.HTTPForbidden(reason=(
                "refused: the visualiser's tap is for a page open on this machine, and this came from "
                f"{request.remote or 'an address not known'}"))
            refused.agent_code = "not_local"
            raise refused
        if not isinstance(body, dict) or not set(body) <= keys or not isinstance(body.get("page"), str) \
                or not TAP_PAGE.fullmatch(body["page"]):
            raise web.HTTPBadRequest(reason=(
                f"the body must be {{\"page\": \"<id>\"}} (letters, digits, '-' and '_', at most 64), with "
                f"nothing else but {', '.join(sorted(keys - {'page'})) or 'nothing'}, not {body!r}"))
        return body["page"]

    async def visualiser_tap_route(self, request):
        """`POST /visualiser/tap {"page"}` (#195): start the visualiser's tap
        on the rig's output (`vistap`), or renew this page's lease on the one
        running, which lasts `lease_s` from now; the page asks again every
        `renew_s` while it listens. 200 with the `source` the browser lists
        once it is linked and realtime; 409 with `code` (`not_held`, `off`,
        `did_not_start`, `not_linked`, `not_realtime`) and why, and the page
        listens to the guitar's input instead."""
        page = self._tap_page(request, await self._json_body(request), {"page"})
        answer = await self.supervisor.tap(page)
        if not answer.ok:
            raise _conflict(answer.reason or "the visualiser tap was refused", answer.code or "refused")
        return web.json_response({"ok": True, "source": answer.source, "lease_s": answer.lease,
                                  "renew_s": answer.renew})

    async def visualiser_tap_release_route(self, request):
        """`POST /visualiser/tap/release {"page", "now"?}` (#195): the page
        gives its lease back (it stopped listening). The tap is kept a while
        for the next idle spell (`vistap.LINGER`), unless `now` (its xrun
        safeguard tripped) and no other page holds it: then it is stopped."""
        body = await self._json_body(request)
        page = self._tap_page(request, body, {"page", "now"})
        now = body.get("now", False)
        if not isinstance(now, bool):
            raise web.HTTPBadRequest(reason=f"'now' must be true or false, not {now!r}")
        return web.json_response({"ok": True, **(await self.supervisor.tap_release(page, at_once=now))})

    async def lifecycle(self, request):
        command = request.path.rsplit("/", 1)[-1]
        body = await self._json_body(request)
        try:
            args = lifecycle_arguments(command, body, local=is_local(request.remote))
        except AgentError as exc:
            raise web.HTTPBadRequest(reason=str(exc))

        # A restart reloads the rig's file, so live work no save has written
        # is dropped by it: refused (409 `unsaved`) unless the body says
        # `{"discard": true}`. Checked under the lifecycle lock, once no live
        # change is in flight (`_run_lifecycle`'s `guard`).
        guard = None
        if command == "restart" and not (isinstance(body, dict) and body.get("discard") is True):
            guard = self._restart_guard
        outcome, args, take = await self._run_lifecycle(command, [args], origin=request, guard=guard)
        answer: dict[str, Any] = {
            "ok": outcome.ok,
            "command": args,
            "output": outcome.stdout,
            "rig": self.describe(),
        }
        if take is not None:
            answer["take"] = take  # finished before the command ran (`_finish_take`)
        if outcome.ok:
            # What `up` said on stderr as it came up -- a patchbay link not
            # made, an amp that died -- which the CLI printed and a phone
            # would otherwise never see.
            answer["warnings"] = up_warnings(outcome.stderr)
            return web.json_response(answer)
        # Failures, one body shape (`reason` says why, as every error here
        # does). The command refused or failed -- "a rig is already running",
        # "no rig to restart", Sushi never came up -- which is a conflict with
        # the rig's state, 409. A runner that gave up waiting (none does now
        # the supervisor is in this process, but a runner may) is 504, and the
        # rig in the answer is what to read next.
        answer["reason"] = outcome.reason
        return web.json_response(answer, status=504 if outcome.abandoned else 409)

    async def _run_lifecycle(
        self, command: str, steps: list[list[str]], fallback: list[str] | None = None, *,
        origin: Any = None, guard: "Callable[[], Awaitable[tuple[str, str] | None]] | None" = None,
    ) -> tuple[Outcome, list[str], dict[str, Any] | None]:
        """Run lifecycle commands in order under the lifecycle lock, stopping
        at the first that fails: one for a route, `down` then `up` for a rig
        switched by Program Change, which therefore takes exactly the path a
        request does. Answers what the last command run said, and its
        arguments; a failure is remembered for /health.

        `fallback` is run when a step after the first fails, which for a rig
        switch is the `up` of the rig that `down` took away: the player gets
        the old rig back rather than silence. The failure is still what is
        answered and remembered, with what the fallback did beside it.

        The MIDI listener is stopped before every command, `up` too (a
        listener can still be following a rig that died outside the agent, and
        a pad pressed while `up` runs must not reach the rig coming up), and
        afterwards follows the rig as it now is, whatever happened.

        A take recording from the web UI is finished first for a `down` or
        `restart` (`_finish_take`), and its stop answered as the third value,
        None when none ran.

        `guard`, when given, is asked once the lock is held and `busy` set (so
        no live change can start) and any live change already in flight has
        landed (the live lock, taken and let go): a `(reason, code)` it
        answers is a 409 with nothing run, the take and the listener left as
        they were. A restart's check for unsaved work (`restart_refusal`).
        """
        self._refuse_while_busy()
        ran, outcome = None, None
        async with self._lock:
            self.busy = command
            try:
                if guard is not None:
                    async with self._live_lock:
                        pass
                    refusal = await guard()
                    if refusal is not None:
                        raise _conflict(*refusal)
                # Only a command that takes the rig down: `up` refuses a rig
                # that is running, so finishing a take for it would end the
                # take for nothing. A `down` or `restart` with no rig up has
                # nothing recording, which `_finish_take` sees for itself.
                take = (await self._finish_take(origin or _MidiOrigin("POST", f"/rig/{command}"), command)
                        if command in ("down", "restart") else None)
                await self._midi_stop(f"stopped while '{command}' runs")
                ran = _run_identity(self.describe())
                outcome, args = await self._lifecycle_steps(steps, fallback)
            finally:
                self.busy = None
                if ran is not None and ((outcome is not None and outcome.ok) or _run_identity(self.describe()) != ran):
                    # The one Undo's live steps (#209) were made to the rig
                    # that was up: after a down, an up or a restart, what is
                    # up is another run, which they must not be undone on.
                    # A command that did nothing (an `up` refused for a rig
                    # already running, a restart that failed before taking
                    # the rig down) leaves the run, and them, as they were.
                    self.history.drop_live()
                await self._midi_sync()
        return outcome, args, take

    async def _lifecycle_steps(
        self, steps: list[list[str]], fallback: list[str] | None = None,
    ) -> tuple[Outcome, list[str]]:
        """`_run_lifecycle`'s commands, its fallback and what is remembered of
        them, for a caller already holding the lifecycle lock with the MIDI
        listener stopped: `_run_lifecycle` itself, and a rig switch (#93) that
        restarts after deciding it cannot switch live."""
        restored = ""
        for step, args in enumerate(steps):
            outcome = await self.run(args)
            if not outcome.ok:
                if fallback is not None and step > 0:
                    back = await self.run(fallback)
                    restored = (f"; {fallback[1]} was brought back" if back.ok else
                                f"; bringing {fallback[1]} back failed too: {back.reason}")
                break
        if outcome.ok:
            # A rig started or stopped afresh: no master is left down by a
            # dip, and the block pw-top measured belonged to the run before.
            self._master_back()
            self._master_capped = None
            self._block_s = None
            # What `up` warned of as this run came up, kept for /health
            # (`agent.warnings`, #246) while it is the run up: a switch by
            # Program Change, and every other path into a start, has no page
            # to answer, and an amp that did not start or is not wired was
            # said once, on a stream nobody reads.
            if args[:1] in (["up"], ["restart"]):
                self._start_warnings = (_warned_run(self.describe()), up_warnings(outcome.stderr))
        if outcome.ok and (self.last_error or {}).get("kind") == "lifecycle":
            # A later success supersedes the old failure: left in place,
            # a healthy agent reports a refused request forever and
            # /health cannot be trusted to say "nothing is wrong now".
            # Only a lifecycle failure, though: an unexpected error from
            # /doctor or /rig says nothing about the rig coming up, so a
            # `down` that works must not forget it.
            self.last_error = None
        if not outcome.ok:
            self._remember(args, outcome.reason + restored, kind="lifecycle")
            if restored:
                outcome = Outcome(False, outcome.returncode, outcome.stdout, outcome.stderr.strip() + restored)
        return outcome, args

    # ------------------------------------------------------------------- live

    async def _json_body(self, request) -> Any:
        """The request's JSON, or None if it sent no body."""
        if not request.can_read_body:
            return None
        try:
            return await request.json()
        except ValueError as exc:  # JSONDecodeError, or bytes that are not text
            raise web.HTTPBadRequest(reason=f"the request body is not JSON: {exc}")

    def _refuse_while_busy(self) -> None:
        if self._lock.locked():
            raise _conflict(f"busy: '{self.busy}' is still running", "busy")

    def _submit(self, fn: Callable[..., Any], *args: Any) -> concurrent.futures.Future:
        """Start one blocking call in the Sushi pool, or refuse (503) if the
        pool is full of calls that have not returned."""
        if len(self._in_flight) >= LIVE_WORKERS:
            raise web.HTTPServiceUnavailable(reason=(
                f"{len(self._in_flight)} calls to Sushi have not returned "
                f"({self.stuck_live_calls} of them for longer than {LIVE_TIMEOUT:g}s): "
                "Sushi is not answering, so no further call is made until some do"
            ))
        future = self._executor.submit(fn, *args)
        self._in_flight.add(future)
        # In the worker thread, as soon as the call returns; a set discard.
        future.add_done_callback(lambda f: (self._in_flight.discard(f), self._abandoned.discard(f)))
        return future

    def _abandon(self, future) -> None:
        """Count a call the agent has stopped waiting for as stuck.

        The done-callback that discards it runs in the worker thread, and this
        on the loop, so the call may return before, during or after: add first,
        then look, and discard if it has already returned. (A check first and an
        add after left a call that returned in between counted for ever.) Either
        order of the two discards is harmless, since a discard of what is not
        there does nothing.
        """
        self._abandoned.add(future)
        if future.done():
            self._abandoned.discard(future)

    async def _sushi(self, request, fn: Callable[..., Any], *args: Any, reading: bool) -> Any:
        """Run one blocking Sushi call off the loop, and say how it failed.

        - No answer within `LIVE_TIMEOUT` is 504, whatever was asked: the
          caller's lock is released, the stuck call is not stopped (see
          `LIVE_TIMEOUT`), and for a change it is not known whether it was
          applied. The exception carries `pending`, the call's future, for
          whoever holds the connection it is using.
        - A pool full of such calls is 503 (see `LIVE_WORKERS`).
        - No Sushi to connect to is 503 when a request is only being read
          (nothing has changed), and 502 once a change is under way.
        - Sushi refusing, or not confirming (`LiveError`), or failing any other
          way is 502, with the reason. That includes a `TimeoutError` raised by
          the call itself (a socket or gRPC deadline): on Python 3.11+ it is
          the same class as `asyncio.TimeoutError`, so it is told from the
          agent's own timeout by whether the call has returned.
        - elkpy not installed (`ElkpyMissing`) is not Sushi's doing: 500.
        Everything but the 503s is remembered for /health until a live change
        next works.
        """
        where = [request.method, request.path]
        future = self._submit(fn, *args)
        try:
            try:
                result = await asyncio.wait_for(asyncio.wrap_future(future), LIVE_TIMEOUT)
                if fn is not self.live.connect:
                    # Sushi answered: the outage, if there was one, is over.
                    # Not on a connect: elkpy's succeeds with no Sushi there
                    # (it prints, and carries on), so it says nothing.
                    self._unreachable_over()
                return result
            except asyncio.TimeoutError:
                if future.done() and not future.cancelled():
                    # It returned (perhaps in this instant), so this is its own
                    # result or its own error, not our deadline.
                    return future.result()
                reason = (
                    f"Sushi did not answer within {LIVE_TIMEOUT:g}s"
                    + ("" if reading else ", so the change may or may not have been applied")
                    + ": read /live before trying again"
                )
                self._remember(where, reason, kind="live")
                timeout = web.HTTPGatewayTimeout(reason=reason)
                if not future.cancelled():  # a call still running, not one that never started
                    self._abandon(future)
                    timeout.pending = future
                raise timeout
            except asyncio.CancelledError as exc:
                if not future.done():  # the client went; the call did not
                    self._abandon(future)
                    exc.pending = future
                raise
        except web.HTTPException:
            raise
        except ElkpyMissing as exc:
            self._remember(where, str(exc), kind="live")
            raise web.HTTPInternalServerError(reason=str(exc))
        except LiveError as exc:  # our own words, not an elkpy type's
            self._remember(where, str(exc), kind="live")
            raise web.HTTPBadGateway(reason=str(exc))
        except Exception as exc:  # noqa: BLE001 -- said, with its type, never swallowed
            message = " ".join(f"{type(exc).__name__}: {exc}".split())  # one line
            if sushi_unreachable(exc):
                if reading:
                    self._unreachable_while_up(where, message)
                    raise web.HTTPServiceUnavailable(
                        reason=f"Sushi is not reachable, so no rig is up to read or change: {message}"
                    )
                message = f"Sushi went away during the change: {message}"
            self._remember(where, message, kind="live")
            raise web.HTTPBadGateway(reason=message)

    def _unreachable_while_up(self, where: list[str], message: str) -> None:
        """A read that found no Sushi. Nothing is wrong when no rig is up (a
        page asking anyway is told 503, and that is all); with the state
        file saying a rig is running it is a fault -- Sushi died or stopped
        listening -- and elkpy's own line about it is dropped (`quiet_elkpy`),
        so it is said here: remembered for /health, and printed once until a
        call to Sushi works again, not once a poll."""
        try:
            up = bool(self.describe().get("running"))
        except Exception:  # noqa: BLE001 -- an unreadable state file: say it, to be safe
            up = True
        if not up:
            self._unreachable_over()
            return
        reason = f"the rig is up but Sushi is not reachable: {message}"
        if self._said_unreachable != message:
            self._said_unreachable = message
            print(f"sushi-rig agent: {reason}", file=sys.stderr, flush=True)
        record = self._unreachable
        if record is None:  # a new outage, with its own time
            record = {"kind": "live", "command": where,
                      "at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "message": reason}
        else:  # the same outage: when it began stays, what it says follows
            record["message"] = reason
        self._unreachable = record
        current = self.last_error
        if current is not None and current is not record and current.get("kind") == "lifecycle":
            # A lifecycle failure says why the rig is as it is, which this
            # does not displace (found by review): said beside it instead.
            also = current.setdefault("also", [])
            also[:] = [m for m in also if not m.startswith("the rig is up but Sushi is not reachable")]
            also.append(reason)
        else:
            self.last_error = record

    def _unreachable_over(self) -> None:
        """The outage `_unreachable_while_up` recorded is over -- Sushi
        answered, or the rig is down -- so /health stops saying it, and the
        next one is news again, with its own time. Another failure
        remembered since is left alone."""
        record, self._unreachable, self._said_unreachable = self._unreachable, None, None
        if record is None:
            return
        current = self.last_error
        if current is record:
            self.last_error = None
        elif current is not None and record["message"] in current.get("also", []):
            current["also"].remove(record["message"])
            if not current["also"]:
                del current["also"]

    @contextlib.asynccontextmanager
    async def _connected(self, request):
        """One connection to Sushi for the whole of a request: opened here,
        closed when the request is done, however it ended.

        After a timeout or a cancellation a worker thread is still inside a call
        on the connection, and closing it from under the call is not ours to
        do. It is closed when that call returns, if it ever does (see
        `LIVE_TIMEOUT`); where it was the connect that timed out, the
        connection the call returns late is closed the same way.

        A close that fails does not change the answer to the request -- what it
        was asked has been done -- but is not silent: it is printed and
        remembered for /health, beside any failure the request had.
        """
        try:
            controller = await self._sushi(request, self.live.connect, reading=True)
        except (web.HTTPGatewayTimeout, asyncio.CancelledError) as exc:
            # The connect is still running (it timed out, or the client went);
            # the connection it returns late is nobody's, so close it then.
            self._close_when_it_returns(request, getattr(exc, "pending", None), None)
            raise
        pending = None
        try:
            yield controller
        except (web.HTTPGatewayTimeout, asyncio.CancelledError) as exc:
            pending = getattr(exc, "pending", None)
            raise
        finally:
            if pending is None:
                pending = _kept_pending(request)  # a timeout answered as something else
            if pending is not None:
                self._close_when_it_returns(request, pending, controller)
            else:
                await self._close(request, controller)

    async def _close(self, request, controller) -> None:
        try:
            # Straight to the pool, not through `_submit`'s count: a close is
            # short, and refusing one because the pool is full of the stuck
            # calls it follows would leak the connection it was to release.
            await asyncio.wait_for(
                asyncio.wrap_future(self._executor.submit(self.live.disconnect, controller)),
                LIVE_TIMEOUT)
        except Exception as exc:  # noqa: BLE001 -- said, not raised over the answer
            self._close_failed(request, exc)

    def _close_failed(self, request, exc: BaseException) -> None:
        message = " ".join(
            f"closing the connection to Sushi failed ({request.method} {request.path}): "
            f"{type(exc).__name__}: {exc}".split())
        print(f"sushi-rig agent: {message}", file=sys.stderr, flush=True)
        self._remember_too(request, message)

    def _remember_too(self, request, message: str) -> None:
        """Remember a live problem without displacing one already remembered:
        /health keeps the reason the request failed, with this beside it."""
        if self.last_error is None:
            self._remember([request.method, request.path], message, kind="live")
        else:
            self.last_error.setdefault("also", []).append(message)

    def _close_when_it_returns(self, request, pending, controller) -> None:
        """The call that timed out (or was cancelled) is still running; when it
        returns, close the connection it was using (`controller`, or what it
        returned if it was the connect) and say how it came back."""
        if pending is None:  # the call never started: nothing is inside the connection
            if controller is not None:
                task = asyncio.ensure_future(self._close(request, controller))
                task.add_done_callback(lambda t: t.cancelled() or t.exception())
            return
        loop = asyncio.get_running_loop()
        started = self.clock()

        def returned_late() -> None:
            error = pending.exception()
            held = controller if controller is not None else (None if error else pending.result())
            how = f"failed with {type(error).__name__}: {error}" if error else "returned normally"
            message = (
                f"the call behind {request.method} {request.path} returned after the 504, "
                f"{self.clock() - started:.1f}s later, and {how}"
                + ("; if it was a change, it was applied" if not error and controller is not None else "")
            )
            print(f"sushi-rig agent: {message}", file=sys.stderr, flush=True)
            self._remember_too(request, message)
            if held is not None:
                task = asyncio.ensure_future(self._close(request, held))
                task.add_done_callback(lambda t: t.cancelled() or t.exception())

        def hand_to_loop(_future) -> None:  # in the worker thread
            try:
                loop.call_soon_threadsafe(returned_late)
            except RuntimeError:  # the loop has gone with the agent
                pass

        pending.add_done_callback(hand_to_loop)

    @contextlib.asynccontextmanager
    async def _connected_for_a_change(self, request):
        """A connection, then the live lock: the connection is made *outside*
        the lock, so a queue of fader writes each constructs its controller (a
        dozen sub-controllers, an RPC, a thread) while waiting rather than in
        turn, and only the read-decide-set that `previous` depends on is
        serialised. Busy is checked before connecting, so nothing connects to a
        Sushi that is coming up or going down, and again when the lock is won,
        since a lifecycle command may have started while this one waited.
        """
        self._refuse_while_busy()
        async with self._connected(request) as controller:
            async with self._live_lock:
                self._refuse_while_busy()
                yield controller

    def _record(self, op: str, args: dict[str, Any], previous: Any, **marks: int) -> dict[str, Any]:
        """Append one entry (`marks`: `undoes` or `stale`, see `log_entry`)."""
        entry = log_entry(
            op, args, previous, datetime.now(timezone.utc).isoformat(timespec="seconds"), **marks)
        self.log.append(entry)
        self.events.publish("log")  # a pedal's change too, which sends no request (#139)
        self._history_follows(entry, self._trimmed + len(self.log) - 1)
        if op in ("add", "remove", "rig", "save") or (op == "undo" and args.get("of") in ("add", "remove")):
            self._chain_log.append(entry)
            if op in ("rig", "save"):
                del self._chain_log[:-1]
        before = len(self.log)
        trim_log(self.log, self.log_limit)
        self._trimmed += before - len(self.log)
        if (self.last_error or {}).get("kind") == "live":
            self.last_error = None  # a change that worked supersedes a live failure
        return entry

    async def live_state(self, request):
        """The whole running rig. Takes no part in `_live_lock`: a read changes
        nothing, so it cannot disturb the `previous` a change records, and a
        panel polling this must not make fader writes queue behind it. It is
        refused while a lifecycle command runs, as the changes are. A read made
        mid-change sees the rig before or after it, as any read of a live rig
        does.
        """
        self._refuse_while_busy()
        return web.json_response(await self._live_shared_read(request))

    async def _live_shared_read(self, request) -> dict[str, Any]:
        """GET /live's answer, one read of Sushi shared by every request that
        comes while it runs (#139): a push brings every open page's poll at
        once, and a read on the rig is hundreds of Sushi calls, so N pages
        would otherwise be N reads side by side. Only a read begun since the
        latest change is joined (`events.seq`, which every write, log entry
        and command's end moves), so a request after a change never gets a
        reading from before it; and only while it runs, never once done, so
        what the agent cannot see (Sushi gone or back, the panel's changes)
        is read afresh by the next request, as before. A read that fails
        fails each request sharing it, as it would have alone."""
        shared = self._live_shared
        if shared is not None and shared[0] == self.events.seq and not shared[1].done():
            return await asyncio.shield(shared[1])
        task = asyncio.ensure_future(self._live_read(request))
        # Retrieved here: a failure is each sharer's to raise, not the loop's to log.
        task.add_done_callback(lambda done: done.cancelled() or done.exception())
        self._live_shared = (self.events.seq, task)
        return await asyncio.shield(task)

    async def _live_read(self, request) -> dict[str, Any]:
        # Whether a dip was under way as the read began, as it ended, or began
        # and ended while it ran (#137): a snapshot reads the gain somewhere
        # in between, so any of the three may have read the floor (review of
        # PR #150: the dip ending before the snapshot returned showed -inf).
        before, dips_before = self._dipping, self._dips
        async with self._connected(request) as controller:
            rig = await self._sushi(request, self.live.snapshot, controller, reading=True)
        processors = self._hide_dip(rig.get("processors", {}), before, dips_before)
        return {
            "ok": True,
            "processors": processors,
            "tracks": rig.get("tracks", {}),
            "parameter_info": rig.get("parameter_info", {}),
        }

    def _hide_dip(self, processors: Any, before: float | None, dips_before: int) -> Any:
        """`processors` with the master's gain as the level a dip puts back,
        where a dip was under way at either end of the read, or ran within it:
        the master volume is the player's, and the dip is no change of the
        rig's, so a fader polling GET /live must not flick to -inf and back.
        The level is the dip's that is under way, else the one the last dip
        left (`_dip_left`); a dip that could not put its level back leaves
        none, and the floor read is the truth."""
        during = self._dipping
        if during is None and before is None and self._dips == dips_before:
            return processors
        held = during if during is not None else self._dip_left
        master = processors.get(ramp.MASTER) if isinstance(processors, dict) else None
        if held is None or not isinstance(master, dict) or not isinstance(master.get("parameters"), dict):
            return processors
        return {**processors, ramp.MASTER: {**master, "parameters": {**master["parameters"], ramp.GAIN: held}}}

    async def live_log(self, request):
        return web.json_response({
            "ok": True, "limit": self.log_limit, "first": self._trimmed, "log": list(self.log),
        })

    # The master's dip around a structural edit (#137): see `ramp.py` for why,
    # how long and where the level goes back to. Here are its effects.

    @contextlib.asynccontextmanager
    async def _dip(self, request, controller, chains: Any):
        """Turn the master down, wait `ramp.settle`, run the body (the edit and
        its read-back), and put the master back, also when the body fails or
        raises. Yields a dict of what was done, kept on the request for the
        answer (`DIP_SAID`): `made`, and `why` when no dip was made; `ms`, how
        long the master was down as the agent timed its two sets; `kept`, a
        level someone else set during the dip, left as it was; `restored`
        false and `why` when the level could not be put back.

        Never logged, never undoable, never folded into a save: only Sushi's
        own gain parameter is set, with the live lock held, and nothing is
        recorded. No dip on a rig with no master track (a v1 config), or a
        master already at the floor, or one whose gain Sushi will not read;
        the edit goes ahead and the answer says why. A gain that cannot be
        turned down stops the edit (502 or 504, as any change Sushi fails),
        after an attempt to put the level back, since it may have been set."""
        said: dict[str, Any] = {"made": False}
        try:
            request.setdefault(DIP_SAID, said)
        except (AttributeError, TypeError):  # a pedal's origin: nothing answers it
            pass
        if not isinstance(chains, dict) or ramp.MASTER not in chains:
            said["why"] = "the rig has no master track"
            yield said
            return
        reading = await self._sushi(
            request, self.live.parameter, controller, ramp.MASTER, ramp.GAIN, reading=True)
        try:
            level = check_parameter(reading, ramp.MASTER, ramp.GAIN)
        except AgentError as exc:
            level, said["why"] = None, f"the master's volume could not be read: {exc}"
        if "why" not in said and (isinstance(level, bool) or not isinstance(level, (int, float))
                                  or not math.isfinite(level)):
            level, said["why"] = None, f"Sushi gave the master's volume as {level!r}"
        if level is None:
            yield said
            return
        if not ramp.worth_dipping(level):
            said["why"] = "the master is already at its floor"
            yield said
            return
        # Never more than the master volume's top put back (#137). A level over
        # it is a rig started before the cap from a file that said more (by
        # design: the file's level was its gain), or one set beside the agent;
        # either way it is brought down to the top here, and said so once it
        # is (`_undip`, `capped`).
        asked = float(level)
        level = master_gain(asked)
        self._dipping = level
        self._dips += 1
        started = self.clock()
        try:
            await self._sushi(
                request, self.live.set_parameter, controller, ramp.MASTER, ramp.GAIN, ramp.FLOOR,
                reading=False)
            said["made"] = True
            wait = ramp.settle(self._block_s)
            said["wait_ms"] = round(wait * 1000, 1)
            await self.sleep(wait)
            yield said
        except BaseException as exc:
            problem = await self._undip(request, controller, level, said, started, asked=asked)
            if problem and isinstance(exc, web.HTTPException):
                # The edit's failure and the master's both, in one answer: a
                # silent master after a failed add must not read as the add's
                # problem alone. The same class, so a 504 is still one, and
                # the call it left running stays with it (`pending`, which
                # `_connected` closes the connection after, a pedal's origin
                # included) and on the request.
                _keep_pending(request, exc)
                combined = type(exc)(reason=f"{exc.reason}; and {problem}")
                for kept in ("pending", "agent_code", "agent_extra"):
                    if hasattr(exc, kept):
                        setattr(combined, kept, getattr(exc, kept))
                raise combined from exc
            raise
        else:
            await self._undip(request, controller, level, said, started, asked=asked)
        finally:
            self._dipping = None

    async def _undip(self, request, controller, level: float, said: dict[str, Any], started: float,
                     *, asked: float | None = None) -> str | None:
        """Read the master's gain again and put `level` back, unless someone
        else set it during the dip (`ramp.restore_to`). Never raises: returns
        what went wrong, or None, and says it in `said`, on stderr and in
        /health, since a master left down is a silent rig. A master that was
        over the master volume's top before the dip (`asked`), or was set over
        it during one, comes back at the top, and that is said too, in `said`
        (`capped`), on stderr and in /health (`master_capped`): the player
        hears the rig get quieter, and must be told why (#137)."""
        now = None
        try:
            reading = await self._sushi(
                request, self.live.parameter, controller, ramp.MASTER, ramp.GAIN, reading=False)
            now = reading.get("value") if isinstance(reading, dict) else None
        except web.HTTPException as exc:
            _keep_pending(request, exc)
        target = ramp.restore_to(level, now)
        if target is None and isinstance(now, (int, float)) and now > rigfile.MASTER_GAIN_MAX:
            # Someone's level, but over the master volume's top (#137): it
            # is brought down to it rather than kept.
            target = rigfile.MASTER_GAIN_MAX
        if target is None:
            said["kept"] = now
            said["ms"] = round((self.clock() - started) * 1000, 1)
            self._dip_left, self._master_down = float(now), None  # someone's level is on it
            return None
        try:
            await self._sushi(
                request, self.live.set_parameter, controller, ramp.MASTER, ramp.GAIN, target, reading=False)
        except web.HTTPException as exc:
            _keep_pending(request, exc)
            problem = (f"the master volume, turned down for the edit, could not be put back to where it "
                       f"was ({exc.reason}): the rig may be silent, so set the master volume again")
            said.update(restored=False, why=problem)
            print(f"sushi-rig agent: {problem}", file=sys.stderr, flush=True)
            self._remember([request.method, request.path], problem, kind="master")
            # Kept apart from `last_error` too (see `_master_down`): a silent
            # master is said in /health until it is put right.
            self._master_down = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                 "message": problem, "level": level}
            self._dip_left = None
            return problem
        self._master_back()
        self._dip_left = target
        said["ms"] = round((self.clock() - started) * 1000, 1)
        over = max((v for v in (asked, now) if isinstance(v, (int, float)) and not isinstance(v, bool)
                    and math.isfinite(v)), default=None)
        if over is not None and over > rigfile.MASTER_GAIN_MAX + rigfile.MASTER_GAIN_SLACK \
                and abs(target - rigfile.MASTER_GAIN_MAX) <= rigfile.MASTER_GAIN_SLACK:
            from_db = rigfile.gain_to_level(over)
            message = (f"the master volume was at {from_db:+g} dB, over the 0 dB it now stops at, and the "
                       "dip around this edit brought it back at 0 dB: the master's gain comes after its "
                       "limiter, so above unity it would lift what the limiter let through (#137). The rig "
                       "was most likely started before the cap, from a file whose master level was above 0")
            said["capped"] = {"from_db": from_db, "to_db": 0.0, "message": message}
            self._master_capped = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                   "from_db": from_db, "message": message}
            print(f"sushi-rig agent: {message}", file=sys.stderr, flush=True)
        return None

    def _master_back(self) -> None:
        """A master put right: a dip put its level back, the player set it
        through the agent, or a lifecycle command worked."""
        self._master_down = None
        if (self.last_error or {}).get("kind") == "master":
            self.last_error = None

    # The read-check-set of each change, shared by its route and by undo, so an
    # undo is checked by the very code a change is. `expect` is called with the
    # value (or chain) read, after the existing checks and before anything is
    # sent, and may raise (undo uses it to refuse a rig that has moved on);
    # `dry_run` stops short of sending, to ask whether the change would go
    # through (a skip). Both raise `AgentError` for what the rig lacks.

    async def _apply_parameter(
        self, request, controller, processor: str, parameter: str, value: float,
        *, expect: Callable[[Any], None] | None = None, dry_run: bool = False,
    ) -> float:
        """Set one parameter and return the value it replaced. The master
        volume's top and the limiter's fixed settings (#137) are refused
        here, before Sushi is asked anything, so that every path that sets a
        parameter (the routes, an undo, a switch's step) is held to them."""
        _refuse_fixed(processor, {parameter: value})
        reading = await self._sushi(
            request, self.live.parameter, controller, processor, parameter, reading=True)
        previous = check_parameter(reading, processor, parameter)
        if expect:
            expect(previous)
        if not dry_run:
            await self._sushi(
                request, self.live.set_parameter, controller, processor, parameter, value, reading=False)
            if (processor, parameter) == (ramp.MASTER, ramp.GAIN):
                # The master volume set by the route, an undo or a switch's
                # step: whatever a dip left, the level is the player's again.
                self._master_back()
        return previous

    async def _apply_parameters(
        self, request, controller, processor: str, values: dict[str, float],
        *, expect: Callable[[Any], None] | None = None, dry_run: bool = False,
    ) -> dict[str, float]:
        """Set several of one processor's parameters, all or none, and return
        the values they replaced. Every one is read and checked before any
        is set, so a name the plugin lacks or a read-only one is refused
        with nothing changed. If Sushi then refuses one, or does not answer,
        the ones already set are put back to what they were, in reverse, and
        the error is raised in its own class (502, or 504 with its
        `pending`), saying what was put back; one that cannot be put back is
        said too. One that did not answer may or may not have been applied,
        and is left as it is, said; after one, nothing more is sent through
        the stuck connection. What is left changed this way is not logged,
        and the answer says to read /live. Held to the master volume's top and
        the limiter's settings as `_apply_parameter` is (#137)."""
        _refuse_fixed(processor, values)
        previous: dict[str, float] = {}
        for parameter in values:
            reading = await self._sushi(
                request, self.live.parameter, controller, processor, parameter, reading=True)
            previous[parameter] = check_parameter(reading, processor, parameter)
        if expect:
            expect(previous)
        if dry_run:
            return previous
        done: list[str] = []
        for parameter, value in values.items():
            try:
                await self._sushi(
                    request, self.live.set_parameter, controller, processor, parameter, value, reading=False)
            except web.HTTPException as exc:
                back, stuck = [], []
                pending = getattr(exc, "pending", None)
                if pending is not None:
                    # A call still running on this connection: putting back
                    # through it would queue behind the stuck call and hold
                    # the live lock for another LIVE_TIMEOUT a parameter,
                    # which `_sushi` releases a 504 to avoid (re-review of
                    # PR #159). Nothing is put back; the player reads /live.
                    stuck = [f"{name} (not tried: Sushi did not answer)" for name in reversed(done)]
                else:
                    undo_order = list(reversed(done))
                    for index, name in enumerate(undo_order):
                        try:
                            await self._sushi(request, self.live.set_parameter, controller, processor, name,
                                              previous[name], reading=False)
                            back.append(name)
                        except web.HTTPException as again:
                            stuck.append(f"{name} ({again.reason})")
                            pending = getattr(again, "pending", None)
                            if pending is not None:
                                # This put-back is the stuck call now: the
                                # rest are not tried, and the connection is
                                # held until it returns.
                                stuck += [f"{rest} (not tried: Sushi did not answer)"
                                          for rest in undo_order[index + 1:]]
                                break
                said = [f"{processor} {parameter}: {exc.reason}"]
                if back:
                    said.append(f"put back {', '.join(reversed(back))} as {'it was' if len(back) == 1 else 'they were'}")
                if stuck:
                    said.append(f"could not put back {', '.join(stuck)}: read /live")
                if not done:
                    said.append("nothing else had been set")
                raised = type(exc)(reason="; ".join(said))
                if pending is not None:
                    raised.pending = pending
                raise raised from exc
            done.append(parameter)
        if (ramp.MASTER, ramp.GAIN) in {(processor, name) for name in values}:
            # Only once every one is set: a batch that fails part-way puts the
            # master back where it was, and a master a dip left down is still
            # down (re-review of PR #159).
            self._master_back()
        return previous

    async def _apply_bypass(
        self, request, controller, processor: str, bypassed: bool,
        *, expect: Callable[[Any], None] | None = None, dry_run: bool = False,
    ) -> bool | None:
        """Bypass or un-bypass one processor and return the state it replaced.
        The master's limiter is never bypassed (#137); un-bypassing it is
        allowed, since that puts it right."""
        why = limiter_problem(processor, bypassed=bypassed)
        if why:
            raise AgentError(why)
        reading = await self._sushi(request, self.live.bypass, controller, processor, reading=True)
        previous = check_bypass(reading, processor)
        if expect:
            expect(previous)
        if not dry_run:
            await self._sushi(
                request, self.live.set_bypass, controller, processor, bypassed, reading=False)
        return previous

    async def _apply_move(
        self, request, controller, processor: str, direction: str,
        *, expect: Callable[[Any], None] | None = None, dry_run: bool = False, dip: bool = True,
    ) -> tuple[Move, Move | None]:
        """Move one processor a step; `(planned, moved)`, where `moved` is None
        if nothing was sent (already at that end, or a dry run). One walk, one
        decision (`live.decide_move`), and the command sent is the one decided
        on: success is what Sushi confirmed, not a word in a status line, and a
        processor found already at that end only now is "unchanged", not a
        failure. `planned.chain` is the chain before. The move is sent inside
        the master's dip (`_dip`), unless `dip` is false: a live switch dips
        once around all its structural steps instead."""
        planned = await self._sushi(
            request, self.live.plan_move, controller, processor, DIRECTIONS[direction], reading=True)
        check_move(planned)
        if expect:
            expect(planned.chain)
        if planned.outcome == "unchanged" or dry_run:
            return planned, None
        async with (self._dip(request, controller, planned.chains) if dip else contextlib.nullcontext()):
            moved = await self._sushi(request, self.live.send_move, controller, planned, reading=False)
        return planned, moved

    async def live_parameter(self, request):
        """Set one parameter, by processor and parameter name.

        `value` is Sushi's normalised domain, a number from 0 to 1; real units
        come with the profiles. The shape is refused (400) before Sushi is
        contacted; whether the running rig has the processor and parameter, and
        whether the parameter is writable, is decided from what Sushi reports
        before anything is set.
        """
        try:
            processor, parameter, value = parameter_request(await self._json_body(request))
        except AgentError as exc:
            raise web.HTTPBadRequest(reason=str(exc))
        async with self._connected_for_a_change(request) as controller:
            try:
                previous = await self._apply_parameter(request, controller, processor, parameter, value)
            except AgentError as exc:
                raise web.HTTPBadRequest(reason=str(exc))
            entry = self._record(
                "parameter", {"processor": processor, "parameter": parameter, "value": value}, previous,
            )
        return web.json_response({"ok": True, **entry})

    async def live_parameters(self, request):
        """Set several of one processor's parameters as one change (#154): a
        profile's control that drives several, such as the hall's Mix. All or
        none (`_apply_parameters`), one log entry (`op: "parameters"`, its
        `previous` each value it replaced), undone as one, and saved as any
        parameter is, from what Sushi holds. Refusals as POST /live/parameter's.
        """
        try:
            processor, values = parameters_request(await self._json_body(request))
        except AgentError as exc:
            raise web.HTTPBadRequest(reason=str(exc))
        async with self._connected_for_a_change(request) as controller:
            try:
                previous = await self._apply_parameters(request, controller, processor, values)
            except AgentError as exc:
                raise web.HTTPBadRequest(reason=str(exc))
            entry = self._record("parameters", {"processor": processor, "parameters": values}, previous)
        return web.json_response({"ok": True, **entry})

    async def live_bypass(self, request):
        """Bypass or un-bypass one processor (not a track) by name."""
        try:
            processor, bypassed = bypass_request(await self._json_body(request))
        except AgentError as exc:
            raise web.HTTPBadRequest(reason=str(exc))
        async with self._connected_for_a_change(request) as controller:
            try:
                previous = await self._apply_bypass(request, controller, processor, bypassed)
            except AgentError as exc:
                raise web.HTTPBadRequest(reason=str(exc))
            entry = self._record("bypass", {"processor": processor, "bypassed": bypassed}, previous)
        return web.json_response({"ok": True, **entry})

    async def live_move(self, request):
        """Move one processor a step earlier ("up") or later ("down") in its
        track's chain. A processor already at that end is `changed: false`,
        and is neither sent to Sushi nor logged.
        """
        try:
            processor, direction = move_request(await self._json_body(request))
        except AgentError as exc:
            raise web.HTTPBadRequest(reason=str(exc))
        async with self._connected_for_a_change(request) as controller:
            try:
                planned, moved = await self._apply_move(request, controller, processor, direction)
            except AgentError as exc:
                raise web.HTTPBadRequest(reason=str(exc))
            if moved is None:
                return web.json_response({
                    "ok": True, "changed": False, "chain": planned.chain,
                    "message": planned.message,
                })
            entry = self._record(
                "move", {"processor": processor, "direction": direction}, planned.chain)
        return web.json_response({"ok": True, "changed": True, "message": moved.message, **entry,
                                  **_dip_answer(request)})

    # How undo puts each op back: (previous, state) -- what the undo replaced,
    # and the state it set. `args` is `undo_inverse`'s, the route's own words.

    async def _undo_parameter(self, request, controller, undone, args, dry_run):
        previous = await self._apply_parameter(
            request, controller, args["processor"], args["parameter"], args["value"],
            expect=lambda current: check_undo_state(undone, current), dry_run=dry_run)
        return previous, args

    async def _undo_parameters(self, request, controller, undone, args, dry_run):
        previous = await self._apply_parameters(
            request, controller, args["processor"], args["parameters"],
            expect=lambda current: check_undo_state(undone, current), dry_run=dry_run)
        return previous, args

    async def _undo_bypass(self, request, controller, undone, args, dry_run):
        previous = await self._apply_bypass(
            request, controller, args["processor"], args["bypassed"],
            expect=lambda current: check_undo_state(undone, current), dry_run=dry_run)
        return previous, args

    async def _undo_move(self, request, controller, undone, args, dry_run):
        # Where the move left the chain, the opposite step is always possible
        # and restores it (tested: `chain_after_move`), so `check_undo_move`
        # passing is what stands between this and an "unchanged" that would be
        # logged as an undo having done something.
        planned, _ = await self._apply_move(
            request, controller, args["processor"], args["direction"],
            expect=lambda chain: check_undo_move(undone, chain), dry_run=dry_run)
        return planned.chain, {"processor": args["processor"], "chain": undone["previous"]}

    async def _undo_add(self, request, controller, undone, args, dry_run):
        # The add's own read-back is the chain it left; anything else on that
        # track since is a chain the entry knows nothing about.
        removal, values = await self._apply_remove(
            request, controller, args["processor"],
            expect=lambda chains: check_undo_add(undone, chains), dry_run=dry_run)
        previous = None if dry_run else removed_record(removal, values)
        return previous, {"processor": removal.processor, "track": removal.track, "chain": removal.expected}

    async def _undo_remove(self, request, controller, undone, args, dry_run):
        # Put back bypassed, set, then given the bypass it had: a plugin put
        # back is never heard at its defaults while its values arrive one by
        # one (a drive or a gain at its default can be a loud jump, review of
        # #32). Sushi creates a processor un-bypassed and takes no bypass with
        # the add, so it is bypassed by the very next command; the moment
        # between is one command long (and, for a mono plugin, its copy's add).
        sent: list["Addition"] = []

        async def bypass_first(addition: "Addition") -> None:
            sent.append(addition)
            await self._sushi(request, self.live.set_bypass, controller, addition.name, True, reading=False)

        try:
            addition = await self._apply_add(
                request, controller, lambda reading: place_undo_remove(undone, reading), dry_run=dry_run,
                after_send=None if dry_run else bypass_first)
            args["after"] = addition.after
            if dry_run:
                return list(addition.chain), {"track": addition.track, "chain": addition.expected}
            for parameter, value in args["parameters"].items():
                await self._sushi(request, self.live.set_parameter, controller, addition.name, parameter,
                                  float(value), reading=False)
            # A bypass the remove could not read comes back on: what a
            # processor made live is, and what silence would hide.
            bypassed = args["bypassed"] if isinstance(args["bypassed"], bool) else False
            await self._sushi(request, self.live.set_bypass, controller, addition.name, bypassed, reading=False)
        except web.HTTPException as exc:
            if not sent or exc.status not in SUSHI_FAILED:
                raise
            await self._take_back(request, controller, sent[0], exc)
        return list(addition.chain), {"track": addition.track, "chain": addition.expected}

    async def _take_back(self, request, controller, addition: "Addition", exc: "web.HTTPException") -> None:
        """A plugin an undo put back whose bypass, read-back or values then
        failed -- refused (502), not answered in time (504) or a pool of
        stuck calls (503) -- is removed again, so an undo never leaves a plugin
        half set and unlogged; 502 either way, saying what the track holds. A
        call still running keeps the connection open until it returns
        (`_keep_pending`)."""
        _keep_pending(request, exc)
        what = f"put {addition.name!r} back on {addition.track!r}"
        try:
            reading = await self._sushi(request, self.live.removal, controller, addition.name, reading=False)
            removal = check_remove(reading, addition.name)
            # A structural edit like any other: inside a dip of its own (#137,
            # review of PR #150), the add's having ended with the failure.
            async with self._dip(request, controller, reading.get("chains") if isinstance(reading, dict) else None):
                await self._sushi(request, self.live.remove, controller, removal, reading=False)
        except (web.HTTPException, AgentError) as again:
            if isinstance(again, web.HTTPException):
                _keep_pending(request, again)
            said = getattr(again, "reason", None) or str(again)
            self._unconfirmed(request, (
                f"{what} but could not set it as it was ({exc.reason}), and removing it again failed "
                f"too ({said}): {addition.name!r} is on {addition.track!r}, perhaps bypassed and with some "
                f"of its values at the plugin's defaults, and the log does not have it. Remove it, or "
                f"restart the rig"))
        self._unconfirmed(request, (
            f"{what} but could not set it as it was ({exc.reason}), so it was removed again: "
            f"nothing was put back, and {addition.track!r} is as it was"))

    async def live_undo(self, request):
        """Put back what the newest change not yet undone replaced (`pick_undo`),
        through the same read-check-set the routes make, and log the undo.

        Body: none, or `{"skip": true}`. Statuses and their `code`:

        - 409 `busy`: a lifecycle command is running (checked first, so it is
          never reported as anything else).
        - 409 `nothing_to_undo`: an empty log, or one with nothing left. A
          conflict with the log's state rather than a missing resource, so 409
          and not 404; said without a connection, since no rig need be up.
        - 409 `stale`: the rig is no longer as the entry assumes (a move's
          processor is elsewhere, a parameter or bypass was changed meanwhile,
          the track an add went on is not as the add left it, the plugin a
          remove took out has lost its place), or Sushi never said what to put
          back. Nothing is applied or logged, and the entry is not skipped to
          reach an older one.
        - 400, no code: what the other routes answer for the same refusal, such
          as a processor the rig no longer has.
        - 502: what the add and remove routes answer 502 (a read-back that is
          not what was sent), and an undo of a remove whose bypass, read-back
          or values Sushi refused, failed or did not answer in time, after
          which the plugin is removed again (`_take_back`).

        An add is undone by removing what it added, its copy to stereo with
        it, through the remove route's own path (`_apply_remove`); a remove by
        adding the plugin back after the same predecessor, through the add
        route's (`_apply_add`, placed by `place_undo_remove`), bypassed by the
        next command, then set to every parameter value the remove recorded,
        and only then given the bypass it had, so it is not heard at its
        defaults. The undo's
        entry is shaped as the route's whose path it took (`args` what was
        applied, `previous` what that replaced: for an add's undo the removed
        plugin's record, for a remove's the chain before), with `of` the op it
        undid.

        The target is chosen under the live lock, from the log as it is then:
        two undos queued together undo two entries, not one twice.

        Skip is the way out of a refusal. The panel's listener moves processors
        outside the agent's lock, so the newest entry can become impossible to
        undo for good, and with no way past it every older change would be
        unreachable. `{"skip": true}` asks the same question of the newest
        entry without sending anything, and if it is refused, records an
        `op: "stale"` entry (`stale: <index>`, the reason in `args`) after which
        the next call goes on to the one before. The history stays honest: the
        entry is marked, not removed. An entry that *can* be undone is not
        skipped (409 `not_stale`): giving up on a change that would go through
        is the one thing skip must not do by mistake. Chosen over targeting an
        entry by index, which would let a request undo out of order.

        An amp change (`op` "amp" or "amp_model") is undone by the amp's own
        path, `_undo_amp`, with the same codes: what it was last sent is what
        "still as the change left it" is checked against, since Carla cannot
        be read back.

        Undoing an undo is not supported: an undo is passed over, and a redo is
        for later. Not run against a live Sushi.
        """
        try:
            skip = undo_request(await self._json_body(request))
        except AgentError as exc:
            raise web.HTTPBadRequest(reason=str(exc))
        return await self._live_undo(request, skip)

    async def _live_undo(self, request, skip: bool, expect: int | None = None):
        """`live_undo`'s work, once the body is read: also what `POST /undo`
        (#209) calls for a live entry of its history, with `expect`, the log
        index it picked: a newer change landing before either pick here is
        refused (409 `changed`), not undone in its place."""
        self._refuse_while_busy()  # before the fast path: busy is the truer answer
        picked = self._pick_undo()
        if picked is None:
            raise _conflict("nothing to undo", "nothing_to_undo")
        self._expect_pick(picked, expect)
        if picked[1]["op"] in amp_live.OPS:
            # Carla's, not Sushi's: no connection to Sushi, the amp's own lock
            # (and for a model, the lifecycle's), see the amp's block.
            return await self._undo_amp(request, picked[1]["op"], skip, expect)
        async with self._connected_for_a_change(request) as controller:
            picked = self._pick_undo()  # the log may have changed while waiting
            if picked is None:
                raise _conflict("nothing to undo", "nothing_to_undo")
            self._expect_pick(picked, expect)
            index, undone = picked
            if undone["op"] in amp_live.OPS:
                raise _conflict("the newest change became one of the amp's while this undo waited: "
                                "undo again", "changed")
            try:
                _, args = undo_inverse(undone)
                previous, state = await {
                    "parameter": self._undo_parameter, "parameters": self._undo_parameters,
                    "bypass": self._undo_bypass,
                    "move": self._undo_move, "add": self._undo_add, "remove": self._undo_remove,
                }[undone["op"]](request, controller, undone, args, skip)
            except AgentError as exc:
                if not skip:
                    hint = (f"{exc}; to give up on this entry and go on to the one before it, "
                            'send {"skip": true}')
                    if isinstance(exc, StaleUndo):
                        raise _conflict(hint, "stale")
                    if isinstance(exc, Unconfirmed):
                        self._unconfirmed(request, hint)
                    raise web.HTTPBadRequest(reason=hint)
                entry = self._record("stale", {"of": undone["op"], "reason": str(exc)}, None, stale=index)
                return web.json_response({"ok": True, **entry, "skipped": undone})
            if skip:
                raise _conflict(
                    "the newest change can be undone as it stands: undo it, "
                    "a skip is only for one that cannot be", "not_stale")
            entry = self._record("undo", {"of": undone["op"], **args}, previous, undoes=index)
        # `state` is what was set and Sushi confirmed, not read back again.
        return web.json_response({"ok": True, **entry, "undone": undone, "state": state,
                                  **_dip_answer(request)})

    @staticmethod
    def _expect_pick(picked: tuple[int, dict[str, Any]], expect: int | None) -> None:
        """409 `changed` when the live undo's pick is not the entry the one
        Undo (#209) asked for: a change landed since, and is not to be undone
        in its place."""
        if expect is not None and picked[0] != expect:
            raise _conflict("a change landed while this undo waited: press Undo again", "changed")

    def _pick_undo(self) -> tuple[int, dict[str, Any]] | None:
        """`pick_undo` on the agent's log, a rig switch in the way answered as
        409 `nothing_to_undo` with the reason (skip included: a switch is not
        an entry that could not be undone, it is the end of what can be)."""
        try:
            return pick_undo(self.log, self._trimmed)
        except UndoBarrier as exc:
            raise _conflict(str(exc), "nothing_to_undo")

    async def release_workers(self, app=None) -> None:
        """Stop handing Sushi calls to the pool. Calls still inside Sushi are
        not interrupted (they cannot be); the interpreter waits for them at exit."""
        self._executor.shutdown(wait=False, cancel_futures=True)

    async def announce_stopping(self, app=None) -> None:
        """Say on stdout that the agent is stopping, and whether it was
        mid-command. SIGTERM and SIGINT stop aiohttp cleanly and silently, so
        afterwards "stopped cleanly" and "killed mid-request" looked the same
        in the log (2026-11-01). A line here means the stop was handled; its
        absence, with the process gone, means it was killed outright.
        """
        doing = f"'{self.busy}' still running" if self.busy else "no lifecycle command running"
        print(f"sushi-rig agent stopping ({doing})", flush=True)

    def _remember(self, command: list[str] | None, reason: str, kind: str = "request") -> None:
        self.last_error = {
            "kind": kind,
            "command": command,
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "message": reason,
        }

    @web.middleware if web else (lambda f: f)
    async def _json_errors(self, request, handler):
        """Every error is JSON with a `reason`: aiohttp's own 404, and
        anything a route did not expect, which is also remembered for
        /health rather than left as a plain-text 500 and a log line."""
        try:
            return await handler(request)
        except web.HTTPException as exc:
            if exc.status < 400:
                raise
            body = {"ok": False, "status": exc.status, "reason": exc.reason}
            if getattr(exc, "agent_code", None):
                body["code"] = exc.agent_code
            # What a reason cannot carry: an HTTP reason is one line, and a
            # refusal's advice may be several (the rig files block's
            # `machine_hint`, lines of YAML to add to the machine file).
            body.update(getattr(exc, "agent_extra", None) or {})
            return web.json_response(body, status=exc.status)
        except Exception as exc:  # noqa: BLE001 -- said, remembered, not swallowed
            reason = f"{type(exc).__name__}: {exc}"
            self._remember([request.method, request.path], reason)
            return web.json_response({"ok": False, "status": 500, "reason": reason}, status=500)

    @web.middleware if web else (lambda f: f)
    async def _same_site_only(self, request, handler):
        """403 `cross_site`, before any route reads the body or touches
        anything (#161), for a POST, PUT, PATCH or DELETE a browser sent from
        another site's page (`cross_site_refusal`), and for any request, a
        GET or a page load included, sent to a name this agent does not
        answer to (`rebinding_refusal`: under DNS rebinding the page is
        same-origin, and could read what it may not change). One guard for
        every route, so a route added later is covered without a word of its
        own; a 403 here comes before the router's 404 and 405 too. Allowed:
        a request with neither `Origin` nor `Sec-Fetch-Site` (curl, the CLI,
        `agent_client`) by any name the agent answers to, and the agent's
        own page by any of them. The pedals' MIDI listener is in this process
        and sends no request. Not stopped: a device on the LAN that sends no
        `Origin`, which is a question of authentication (#29's permission
        split)."""
        # The header itself: with none, aiohttp's `request.host` falls back
        # to the address the request arrived on (3.14 here; older releases,
        # `socket.getfqdn()`), which is a name no browser sent.
        host = request.headers.get("Host")
        reason = None
        if request.method in STATE_CHANGING or request.path in GUARDED_READS:
            reason = cross_site_refusal(request.headers, host, self.allow_hosts)
        if reason is None:
            names = own_names((*self.allow_hosts, self.bound) if self.bound else self.allow_hosts)
            reason = rebinding_refusal(host, names)
        if reason is not None:
            refused = web.HTTPForbidden(reason=reason)
            refused.agent_code = "cross_site"
            raise refused
        return await handler(request)

    # ======================================================================= #
    # Pushed changes (#139): GET /events, and what publishes to it
    # ======================================================================= #
    #
    # The decisions are `events.py`'s (what is pushed and why, the gap, the
    # heartbeat, slow clients); this serves the stream and calls `publish`.
    # Outside this block: `self.events` in `__init__`, `_push_changes` in the
    # middlewares, one line in `_record`, one in `_midi_heard`, and the
    # supervisor's `changed`.

    def _events_routes(self, app) -> None:
        app.router.add_get("/events", self.events_route)
        app.on_startup.append(self._events_open)
        # Before aiohttp waits for its handlers to finish: each stream ends.
        app.on_shutdown.append(self._events_close)

    async def _events_open(self, app=None) -> None:
        # The loop a `publish` from a worker thread is handed to, known from
        # the start; and an agent served again after a stop (the tests') opens.
        self.events.bind()

    async def _events_close(self, app=None) -> None:
        self.events.close()

    @property
    def busy(self) -> str | None:
        """The lifecycle command running, if one is: in /health, and pushed
        to the open pages as it ends (#139), a pedal's switch included.

        Not as it starts. While `busy` is set a page draws no chain (it does
        not read /live then), and most commands are a rig file written in a
        fraction of a second: pushing the start made every open page drop
        its chain and draw it again for each (and the page that made the
        change lose its focus with it). A long one (a start, a switch) shows
        at the next poll, within the second, as before."""
        return self._busy

    @busy.setter
    def busy(self, value: str | None) -> None:
        ended = value is None and getattr(self, "_busy", None) is not None
        self._busy = value
        if ended:
            self.events.publish("busy")

    @web.middleware if web else (lambda f: f)
    async def _push_changes(self, request, handler):
        """Every POST, PUT, PATCH and DELETE, once answered, pushed to the open
        pages (#139), whatever the answer: a refusal can change /health's
        `last_error`. Not the visualiser's tap lease, renewed every few
        seconds and shown on no page."""
        try:
            return await handler(request)
        finally:
            if request.method in STATE_CHANGING and not request.path.startswith("/visualiser/"):
                self.events.publish(request.path)

    async def events_route(self, request):
        """`GET /events`: `text/event-stream`, a `hello` and then a `changed`
        event (`{"seq", "what"}`) whenever what /health, /live and the log
        answer may have changed (`events.py` says when, and why an event
        names the change rather than carrying it). 503 `too_many_streams`
        past `events.MAX_STREAMS`, or `events.MAX_PER_PEER` from one
        address, or `stopping` while the agent stops. Behind the cross-site
        check as a write is (`GUARDED_READS`). Its socket gives up on a peer
        gone without closing within about 30 s (`events.hold_to_a_deadline`)."""
        why = self.events.refusal(request.remote)
        stream = self.events.open(request.remote) if why is None else None
        if stream is None:
            refused = web.HTTPServiceUnavailable(reason=(
                "the agent is stopping" if why == "stopping" else
                f"refused: {events_module.MAX_STREAMS} streams are open already, or "
                f"{events_module.MAX_PER_PEER} from {request.remote}; the page polls instead"))
            refused.agent_code = why or "too_many_streams"
            raise refused
        response = web.StreamResponse(headers={
            "Content-Type": "text/event-stream", "Cache-Control": "no-store",
            # A proxy in front (nginx) must pass each event on, not gather them.
            "X-Accel-Buffering": "no",
        })
        try:
            if request.transport is not None:
                events_module.hold_to_a_deadline(request.transport.get_extra_info("socket"))
            await response.prepare(request)
            await self._events_send(response, events_module.opening(self.events.seq))
            quiet = 0.0
            while not stream.closing:
                try:
                    await asyncio.wait_for(stream.wake.wait(), events_module.LOOK)
                except TimeoutError:
                    if request.transport is None or request.transport.is_closing():
                        break  # the page has gone
                    quiet += events_module.LOOK
                    if quiet >= events_module.HEARTBEAT:
                        await self._events_send(response, events_module.HEARTBEAT_LINE)
                        quiet = 0.0
                    continue
                if stream.closing:
                    break
                seq = self.events.seq
                await self._events_send(response, events_module.event(
                    "changed", {"seq": seq, "what": stream.take()}, seq))
                quiet = 0.0
                # What changes meanwhile waits, and goes in the next event.
                await asyncio.sleep(events_module.GAP)
        except ConnectionError:
            pass  # the page went: its stream ends here
        except TimeoutError:
            # A page that stopped reading: its connection is dropped with what
            # is still unsent, so finishing the answer cannot wait on it again.
            if request.transport is not None:
                request.transport.abort()
        finally:
            self.events.drop(stream)
        return response

    @staticmethod
    async def _events_send(response, data: bytes) -> None:
        """One write, given `events.WRITE_TIMEOUT`: a client that does not
        read holds up its own stream for that long, and nothing else."""
        await asyncio.wait_for(response.write(data), events_module.WRITE_TIMEOUT)

    # ======================================================================= #
    # (end of pushed changes)
    # ======================================================================= #

    # ======================================================================= #
    # The library and chain edits (#32): GET /library, POST /live/add|remove
    # ======================================================================= #

    def _edit_routes(self, app) -> None:
        app.router.add_get("/library", self.library_route)
        app.router.add_post("/live/add", self.live_add)
        app.router.add_post("/live/remove", self.live_remove)

    def _read_library(self) -> dict[str, Any]:
        from .paths import checkout_root

        return library_module.read_library(self.root or checkout_root())

    async def _library_now(self) -> dict[str, Any]:
        """The library, built on first use and kept for the agent's life: the
        manifest, `plugins/` and the profiles change when the checkout does,
        and the agent is restarted with it. Built off the loop (it reads a
        hundred files or so), once however many requests ask at the same
        time. A build that fails is 500 with the reason and is not kept, so
        the next request tries again."""
        if self._library is not None:
            return self._library
        async with self._library_lock:
            if self._library is None:
                try:
                    built = await asyncio.get_running_loop().run_in_executor(None, self._build_library)
                except library_module.LibraryError as exc:
                    self._remember(["library"], str(exc))
                    raise web.HTTPInternalServerError(reason=str(exc))
                self._library = built
        return self._library

    async def library_route(self, request):
        """Every plugin in the manifest, for the Library screen to pick from.
        Needs no rig: it is files, not Sushi."""
        return web.json_response({"ok": True, **(await self._library_now())})

    async def live_add(self, request):
        """Add a plugin from the library to a track of the running rig.

        Body `{"track", "uri", "name"?, "after"?}`, or `"first": true` in place
        of `after` to put it at the start of the chain. Refused (400) before Sushi is
        contacted for a bad shape, a URI not in the manifest or one Sushi cannot
        host; refused (409) from a walk of the chains for a track the rig lacks,
        a name it already has or an `after` not on the track. Sent as decided,
        then confirmed from a second walk. Logged as op "add" with `previous`
        the track's chain before, and in `args` what was done: the name
        actually used (an undo removes it by that), its `copy` to stereo (or
        null), the `chain` it left (an undo checks the plugin is still there)
        and `defaults`, the plugin's parameter values read straight after it
        was added, which a save compares with so it writes only the values
        that differ. Defaults Sushi would not give are null, and said in the
        answer's `note`: the add stands, and a save then writes every value.
        """
        body = await self._json_body(request)
        library = await self._library_now()
        try:
            asked = add_request(body, library)
        except AgentError as exc:
            raise web.HTTPBadRequest(reason=str(exc))
        # The CPU check (#137): busy first, so a rig coming up or going down
        # is `busy` and not a load read off a rig in between.
        self._refuse_while_busy()
        read_at = self._load_gen
        load, over = await self._load_before_add()
        self._refuse_over_budget(load, over)
        async with self._connected_for_a_change(request) as controller:
            if self._load_gen != read_at:
                # Another add went in while this one waited for the lock: the
                # reading above is of the rig without it, so it is read again
                # (a fresh pw-top run, the sample having been dropped), and an
                # add queued behind another cannot pass on the first's reading.
                load, over = await self._load_before_add()
                self._refuse_over_budget(load, over)
            try:
                addition = await self._apply_add(request, controller, lambda reading: check_add(reading, asked))
            except Conflict as exc:
                raise _conflict(str(exc), exc.code) if exc.code else web.HTTPConflict(reason=str(exc))
            defaults, note = await self._read_defaults(request, controller, addition)
            # `first` only where it was asked, so the log says what was done
            # and every other add's entry keeps its shape.
            entry = self._record("add", {
                "track": addition.track, "uri": addition.uri, "name": addition.name,
                "after": addition.after, **({"first": True} if asked.first else {}), "copy": addition.copy,
                "chain": list(addition.expected), "defaults": defaults,
            }, list(addition.chain))
        answer = {
            "ok": True, **entry, "chain": addition.expected,
            "message": f"added {addition.name} to {addition.track}", **_dip_answer(request), "load": load,
        }
        if note:
            answer["note"] = note
        return web.json_response(answer)

    async def live_remove(self, request):
        """Remove one processor from the running rig, by name.

        Body `{"processor"}`. Refused (409) for a track, a channel's send, the
        master's return, or a processor the rig lacks; 502 if Sushi will not
        say which plugin it is, since the log must be able to put it back.
        Its parameter values and bypass are read just before the remove;
        confirmed from a walk afterwards. Logged as op "remove" with `previous`
        `{"track", "chain", "uri", "uid", "type", "copy", "parameters",
        "bypassed", "units"}`: where it was, what it was and how it was set,
        and the chain as the remove grouped it, which is what an undo puts
        back.
        """
        try:
            processor = remove_request(await self._json_body(request))
        except AgentError as exc:
            raise web.HTTPBadRequest(reason=str(exc))
        async with self._connected_for_a_change(request) as controller:
            try:
                removal, values = await self._apply_remove(request, controller, processor)
            except Conflict as exc:
                raise web.HTTPConflict(reason=str(exc))
            except Unconfirmed as exc:
                self._unconfirmed(request, str(exc))
            entry = self._record("remove", {"processor": removal.processor}, removed_record(removal, values))
        return web.json_response({
            "ok": True, **entry, "chain": removal.expected,
            "message": f"removed {removal.processor} from {removal.track}", **_dip_answer(request),
        })

    # The read-decide-send-confirm of an add and a remove, shared by their
    # routes and by undo, as `_apply_parameter` and the others are: an undo
    # of a remove is an add and an undo of an add a remove, checked by the
    # very code the routes run. `decide` (an add's) and `expect` (a remove's)
    # may raise; `dry_run` stops before anything is sent.

    async def _apply_add(
        self, request, controller, decide: Callable[[dict[str, Any]], "Addition"], *, dry_run: bool = False,
        after_send: Callable[["Addition"], Awaitable[None]] | None = None,
    ) -> "Addition":
        """Walk the chains with the uids, decide the add (`decide`, which
        raises for what the rig cannot take), send it, and confirm it from a
        second walk (502 if it does not read back as decided). `after_send`
        runs straight after the add is sent, before the read-back: an undo
        bypasses the plugin there, so it is not heard at its defaults."""
        reading = await self._sushi(request, self.live.walk, controller, True, reading=True)
        addition = decide(reading)
        if dry_run:
            return addition
        # Sent, and confirmed, inside the master's dip (#137, `_dip`).
        async with self._dip(request, controller, reading.get("chains") if isinstance(reading, dict) else None):
            try:
                await self._sushi(request, self.live.add, controller, addition, reading=False)
            finally:
                self._load_stale()  # sent, so perhaps on the rig: the load read before is not its
            if after_send is not None:
                await after_send(addition)
            after = await self._sushi(request, self.live.walk, controller, reading=False)
            what = f"added {addition.name!r} to {addition.track!r}"
            self._confirm(request, after, addition.track, addition.expected, what)
        return addition

    @staticmethod
    def _refuse_over_budget(load: dict[str, Any], over: str | None) -> None:
        """409 `over_budget`, the load beside its reason, when `over` says why."""
        if over:
            refused = _conflict(over, "over_budget")
            refused.agent_extra = {"load": load}
            raise refused

    async def _read_defaults(self, request, controller, addition: "Addition") -> tuple[dict | None, str | None]:
        """`(defaults, note)`: the added plugin's parameter values, read just
        after the add confirmed it, which are the plugin's defaults (a
        processor added live starts at them: rigfile.py's list of what is
        assumed of Sushi). Sushi refusing, failing or not answering that read
        in time does not undo the add, which is on the rig and confirmed: the
        defaults are None and `note` says so, and a call still running keeps
        the connection open until it returns (`_keep_pending`)."""
        try:
            values = await self._sushi(request, self.live.values, controller, addition.name, reading=False)
        except web.HTTPException as exc:
            # Refused, failed, or not answered in time: the add is on the rig
            # and confirmed either way, so it is logged; an unlogged plugin
            # would be one undo and a save know nothing of (review, #32).
            if exc.status not in SUSHI_FAILED:
                raise
            _keep_pending(request, exc)
            return None, (f"its parameters could not be read after it was added ({exc.reason}), so a "
                          "save will write every value it has, not only those that differ from its defaults")
        parameters = values.get("parameters") if isinstance(values, dict) else None
        if not isinstance(parameters, dict):
            return None, ("Sushi gave no parameters for it after it was added, so a save will write every "
                          "value it has, not only those that differ from its defaults")
        return dict(parameters), None

    async def _apply_remove(
        self, request, controller, processor: str, *,
        expect: Callable[[dict[str, list[str]]], None] | None = None, dry_run: bool = False,
    ) -> tuple["Removal", dict[str, Any] | None]:
        """`(removal, values)`: read what removing `processor` is (with Sushi's
        session), let `expect` see the chains first (an undo refuses a rig that
        moved on before anything else is said), decide (`check_remove`, which
        raises `Conflict` or `Unconfirmed`), read the processor's values and
        bypass, send the remove and confirm it from a walk. `values` is None
        on a dry run, which sends and reads nothing more."""
        reading = await self._sushi(request, self.live.removal, controller, processor, reading=True)
        if expect:
            expect(reading.get("chains") or {} if isinstance(reading, dict) else {})
        removal = check_remove(reading, processor)
        if dry_run:
            return removal, None
        values = await self._sushi(request, self.live.values, controller, processor, reading=True)
        if not isinstance(values, dict) or not isinstance(values.get("parameters"), dict):
            self._unconfirmed(
                request, f"Sushi gave no parameters for {processor!r}, so it is not removed: the log could "
                "not say how to put it back")
        # Sent, and confirmed, inside the master's dip (#137, `_dip`).
        async with self._dip(request, controller, reading.get("chains") if isinstance(reading, dict) else None):
            await self._sushi(request, self.live.remove, controller, removal, reading=False)
            after = await self._sushi(request, self.live.walk, controller, reading=False)
            what = f"removed {removal.processor!r} from {removal.track!r}"
            chains = self._confirm(request, after, removal.track, removal.expected, what)
            if _track_of(chains, removal.processor) is not None:
                self._unconfirmed(request, f"Sushi confirmed it {what}, but it is still on the rig")
        return removal, {"parameters": dict(values["parameters"]), "bypassed": values.get("bypassed")}

    def _confirm(
        self, request, reading: Any, track: str, expected: list[str], what: str,
    ) -> dict[str, list[str]]:
        """The chains of a read-back, confirmed (`confirm_chain`), or 502. The
        one place a read-back's chains are taken from it, so a reading with
        none is a 502 like any other read-back that is wrong, not a KeyError's
        500 (found by review)."""
        chains = reading.get("chains") if isinstance(reading, dict) else None
        if not isinstance(chains, dict):
            self._unconfirmed(request, f"Sushi confirmed it {what}, but the read-back had no chains")
        try:
            confirm_chain(chains, track, expected, what)
        except Unconfirmed as exc:
            self._unconfirmed(request, str(exc))
        return chains

    def _unconfirmed(self, request, reason: str) -> None:
        """502, remembered for /health, and nothing logged: the log holds edits
        the rig was read back to have."""
        self._remember([request.method, request.path], reason, kind="live")
        raise web.HTTPBadGateway(reason=reason)

    # ======================================================================= #
    # (end of the library and chain edits' routes)
    # ======================================================================= #
    # ======================================================================= #
    # The web UI (#31): GET /, /ui/*, /profiles, /live/plugins
    # ======================================================================= #
    #
    # Kept in one block so it merges past other work on this file; outside it
    # the UI adds only `Live.plugins`, the call in `app` and a section of the
    # module docstring. The decisions are `sushi_rig.ui`'s and
    # `ui/static/rig.js`'s; this serves files and answers two reads.

    def _ui_routes(self, app) -> None:
        from .ui import UI_DIR

        app.router.add_get("/", self.ui_page)
        # Before the static route, which would answer the folder itself with a
        # JSON 403 (#111: typed by hand on the rig laptop, it read as broken).
        app.router.add_get("/ui", self.ui_redirect)
        app.router.add_get("/ui/", self.ui_redirect)
        # aiohttp's static route refuses paths that leave the folder and does
        # not follow symlinks out of it; no listing, so /ui/ is not a browser.
        app.router.add_static("/ui/", UI_DIR, show_index=False)
        app.router.add_get("/profiles", self.profiles_route)
        app.router.add_get("/live/plugins", self.live_plugins)
        app.router.add_get("/rigs", self.rigs_route)
        app.router.add_get("/rig/load", self.rig_load)
        app.on_response_prepare.append(_ui_cache_control)

    async def ui_redirect(self, request):
        """/ui and /ui/ are where the page's files are, not a page: send the
        browser to the page. 302, not 301, which a browser keeps for good."""
        raise web.HTTPFound("/")

    async def rigs_route(self, request):
        """`{"rigs": [names], "running"}`: config/rigs/*.yaml by name, sorted
        as a Program Change counts them, read now (off the loop: it lists a
        folder), and which of them is the running rig's file
        (`running_rig_listed`, the rig the bin refuses to delete), or null.
        Whether a rig is up, and its state, is /health's to say."""
        root = self._midi_root()
        rigs = await asyncio.to_thread(midi_listener.program_order, root)
        try:
            running = running_rig_listed(await self._rigfile_run(), root, rigs)
        except Exception:  # noqa: BLE001 -- the list is still worth giving; null is "not known" here
            running = None
        return web.json_response({"ok": True, "rigs": rigs, "running": running})

    async def _pw_top_sample(self) -> str:
        """One pw-top run's output, shared: a request while one runs waits
        for that one, and one within `LOAD_SHARED` of the last that worked
        gets its output. Each run is about a second of pw-top on the rig's
        CPU while it plays, and every page polls every two seconds: without
        this, two pages kept one running nearly all the time (found by
        review). A failure is shared by those waiting for it, not kept.
        Shielded, so a client that goes does not cancel the run the others
        wait for."""
        if self._load_last is not None and self.clock() - self._load_last[0] < LOAD_SHARED:
            return self._load_last[1]
        if self._load_run is None:
            run = asyncio.ensure_future(asyncio.to_thread(self.pw_top, LOAD_TIMEOUT))
            gen = self._load_gen

            def finished(task) -> None:
                if self._load_run is task:
                    self._load_run = None
                if not task.cancelled() and task.exception() is None:  # retrieves it either way
                    if gen == self._load_gen:  # not a run from before an add since (#137)
                        self._load_last = (self.clock(), task.result())

            run.add_done_callback(finished)
            self._load_run = run
        return await asyncio.shield(self._load_run)

    async def rig_load(self, request):
        """One pw-top sample of the running rig (`top.load_report`); see the
        module docstring's web UI section for how it refuses."""
        described = self.describe()
        if not described.get("running"):
            raise _conflict("no rig is running, so there is no load to read", "no_rig")
        try:
            output = await self._pw_top_sample()
        except top_module.LoadError as exc:
            if self._said_load != str(exc):
                self._said_load = str(exc)
                print(f"sushi-rig agent: load: {exc}", file=sys.stderr, flush=True)
            status = {"no_pw_top": web.HTTPInternalServerError, "timeout": web.HTTPGatewayTimeout}
            error = status.get(exc.code, web.HTTPBadGateway)(reason=str(exc))
            error.agent_code = exc.code
            raise error
        self._said_load = None
        report = top_module.load_report(top_module.parse_pw_top(output), self._load_children(described))
        self._know_block(report)
        return web.json_response({
            "ok": True, "at": datetime.now(timezone.utc).isoformat(timespec="seconds"), **report})

    def _load_children(self, described: dict[str, Any]) -> dict[str, Any] | None:
        """The rig's children for a load reading: the state file's, and the
        visualiser's tap while the agent runs one (#195), which the state
        file never lists, so its nodes, which are in Sushi's cycle, count in
        LOAD and XRUNS for as long as it runs and no longer."""
        children = described.get("children")
        tap = self.supervisor.tap_children() if hasattr(self.supervisor, "tap_children") else {}
        left = described.get("visualiser_tap")
        if not tap and isinstance(left, dict) and left.get("alive"):
            # One no agent holds (an earlier agent's, left running): still in
            # Sushi's cycle, so still counted.
            tap = {"vistap": {"pid": left.get("pid"), "alive": True}}
        if not tap:
            return children
        return {**(children if isinstance(children, dict) else {}), **tap}

    def _know_block(self, report: dict[str, Any]) -> None:
        """Keep the block pw-top measured, for the dip's wait (`ramp.settle`)."""
        block_ms = (report.get("budget") or {}).get("block_ms")
        if isinstance(block_ms, (int, float)) and not isinstance(block_ms, bool) and block_ms > 0:
            self._block_s = block_ms / 1000

    def _load_stale(self) -> None:
        """A structural add was sent (#137): the sample held, and a pw-top run
        under way, are of the rig without it, so neither answers again. The
        run in flight still answers those already waiting for it."""
        self._load_gen += 1
        self._load_last = None
        self._load_run = None

    async def _load_before_add(self) -> tuple[dict[str, Any], str | None]:
        """`top.add_budget`'s `(load, refusal)` for a live add (#137): the
        reading GET /rig/load gives, from the same shared sample
        (`_pw_top_sample`), so an add made while a page polls costs no
        second pw-top. A pw-top that cannot be read, or a rig not running by
        the state file, is said in `load` and refuses nothing. The first reading is
        taken off the lock (a sample takes about a second, and nothing it
        reads is the chain's); an add that waited for the lock while another
        went in reads again under it, and live changes queue behind that run."""
        try:
            described = self.describe()  # as GET /rig/load reads it
            if not described.get("running"):
                return top_module.add_budget(None, "the state file says no rig is running")
            output = await self._pw_top_sample()
            report = top_module.load_report(top_module.parse_pw_top(output), self._load_children(described))
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 -- whatever stops the reading, the add goes ahead, said
            # Not only pw-top's own failures (`LoadError`): a pw-top that
            # cannot be run (PermissionError), a state file that cannot be
            # read, a sample this parser trips on. A check that cannot be
            # made must not stop every add (review of PR #150).
            why = str(exc) if isinstance(exc, top_module.LoadError) else f"{type(exc).__name__}: {exc}"
            why = " ".join(why.split()) or type(exc).__name__
            print(f"sushi-rig agent: warning: the load before an add could not be read ({why}); "
                  "the add goes ahead unchecked", file=sys.stderr, flush=True)
            return top_module.add_budget(None, why)
        self._know_block(report)
        return top_module.add_budget(report)

    async def ui_page(self, request):
        from .ui import UI_DIR

        return web.FileResponse(UI_DIR / "index.html")

    def _read_profiles(self) -> dict[str, Any]:
        from .paths import checkout_root
        from .ui import panel_profiles

        directory = (self.root or checkout_root()) / library_module.PROFILES_DIR
        return panel_profiles(*library_module.load_profiles(directory))

    async def profiles_route(self, request):
        """Every profile under tool/examples/profiles/, flattened for the
        browser (`ui.panel_profile`), and the problems loading them met. Needs
        no rig. Read on every request, not kept as the library is: it is a
        handful of small files, the page asks once, and a profile being written
        then shows on a reload without restarting the agent."""
        answer = await asyncio.to_thread(self._read_profiles)
        return web.json_response({"ok": True, **answer})

    async def live_plugins(self, request):
        """Which plugin each processor on the rig is, from Sushi's session
        (`live.read_live_plugins`), with `role` "send", "return" or "limiter"
        (#137) for the compiler's (`live.protected_processor`, the one rule for them) and None
        for a plugin. A read: busy, 503 and 502 as `/live` answers them, and
        no live lock. 501 from a `Live` that cannot say, rather than an answer
        the page would take for a rig of unknown plugins."""
        plugins = getattr(self.live, "plugins", None)
        if plugins is None:
            raise web.HTTPNotImplemented(
                reason="this agent's connection to Sushi cannot say which plugin each processor is")
        self._refuse_while_busy()
        async with self._connected(request) as controller:
            found = await self._sushi(request, plugins, controller, reading=True)
        # Each LV2 plugin's width, from the compiler's own table
        # (plugin-ports.txt, #119), so the page can mark a mono plugin and
        # warn where one follows a stereo one, without a file of its own.
        try:
            from .paths import checkout_root

            ports = await asyncio.to_thread(stereo.load_ports, checkout_root())
            ports_note = None
        except stereo.PortsError as exc:
            ports, ports_note = {}, "; ".join(exc.problems)
        processors = {}
        for name, plugin in (found or {}).items():
            track = plugin.get("track") or ""
            role = None
            if stereo.is_copy(name, plugin.get("uid")):
                role = "stereo"  # the compiler's copy to stereo: the page does not show it
            elif protected_processor(track, name, plugin.get("uid")):
                role = ("send" if is_send(track, name, plugin.get("uid"))
                        else "limiter" if is_tail(track, name, plugin.get("uid")) else "return")
            processors[name] = {**plugin, "role": role, "width": plugin_width(plugin, ports)}
        return web.json_response({"ok": True, "processors": processors, "ports_note": ports_note})

    # ======================================================================= #
    # (end of the web UI's routes)
    # ======================================================================= #
    # =================================================================== MIDI
    #
    # The MIDI listener's wiring (#34, #93). The decisions are midi_listener.py's;
    # this block only connects them to the routes' own machinery, and is kept
    # in one place so it merges past other work on this file. Outside it the
    # agent calls in at: __init__ (`_midi_init`), /health (`_midi_status`),
    # /rig (`_rig_answer`), `_run_lifecycle` (`_midi_stop`, `_midi_sync`),
    # `app` (`_midi_routes`: GET /midi, #191) and `serve` (`midi_adopt`,
    # `midi_release`).
    #
    # Threading: the listener's thread reads the port and hands each message to
    # the loop (`call_soon_threadsafe`); everything below runs on the loop. A
    # generation number, bumped by every stop, tags each listener and each
    # action it caused, so a message that was on its way when the rig went
    # down is dropped rather than acted on against the rig that comes up next.

    def _midi_init(self, world: midi_listener.World | None) -> None:
        self._midi_world = world or midi_listener.World()
        # One listener a port (#191): the rig's map's controller, and any other
        # the machine file has stepping rigs, are separate ports.
        self._listeners: list[midi_listener.Listener] = []
        self._midi_plan: midi_listener.Plan | None = None
        self._midi_generation = 0
        # Each CC switch's last state, a dict a port, for `dispatch`.
        self._midi_switches: dict[int, dict[str, bool]] = {}
        # One MIDI action at a time, in the order they were played: two toggles
        # in quick succession must reach Sushi in that order, and a switch's
        # off-then-on must not arrive on-then-off. Not the live lock: that
        # serialises only the read-decide-set, and connections are made outside
        # it, so two actions could otherwise overtake each other while connecting.
        self._midi_lock = asyncio.Lock()
        self._midi_tasks: set = set()
        # A rig switch a pedal asked for that has not finished (#191): a second
        # press meanwhile is refused, not queued.
        self._midi_switch_pending = False
        # A pedal on a volume (#191): its latest value and a count of its
        # messages, by (port, control), and the silencings running, so a
        # burst at the heel is one task that ends on the pedal's last value.
        self._midi_pedal: dict[tuple[int, str], tuple[int, int]] = {}
        # Keyed by (generation, port, control), so a silencing still waiting
        # on Sushi for the rig before a switch never holds up the next rig's.
        self._midi_snapping: set[tuple[int, int, str]] = set()
        # The pedals the agent last set silent: the next message above the
        # heel for one has the agent set Sushi's value, in case its silence
        # landed after Sushi had already followed that rise.
        self._midi_silenced: set[tuple[int, int, str]] = set()
        self._midi_released = False  # set by `midi_release`: nothing starts a listener after it
        # The last message from each control of each controller (#192), bound
        # or not: `{controller: {control: (count, value, clock)}}`, for the
        # page's "heard" light. Kept across listeners, so a light does not go
        # out because an assignment re-planned the listener.
        self._midi_seen: dict[str, dict[str, tuple[int, int, float]]] = {}
        # Why each port's pedal is not plugged in, by feed, while it is not:
        # said again if a swapped plan changes whether the rig needs it.
        self._midi_absence: dict[int, str] = {}
        # The ports the plans since the rig came up asked to link to Sushi
        # (#210's review): one a re-plan drops has its agent link taken down.
        self._midi_links: set[str] = set()
        self._midi: dict[str, Any] = {
            "state": "off", "reason": "the agent has not looked for a rig yet",
            "rig": None, "controller": None, "port": None, "bindings": [], "programs": False,
            "feeds": [], "heard": 0, "last": None, "problem": None, "last_error": None, "rig_problem": None,
        }

    @property
    def _listener(self) -> midi_listener.Listener | None:
        """The first port's listener, or None when none is running."""
        return self._listeners[0] if self._listeners else None

    def _midi_root(self) -> Path:
        from .paths import checkout_root

        return self.root or checkout_root()

    def _midi_status(self) -> dict[str, Any]:
        """The listener, for /health: `state` is off (with the `reason`),
        opening, listening, or failed (with the `problem`). `heard` counts every
        message the ports delivered since they opened, aftertouch included, and
        `last` is the last one the listener reads and what it did with it: what
        to look at when a pedal seems to do nothing. `feeds` is each port
        (#191): its controller, port, state and problem; `controller` and
        `port` are the first's, the rig's map's when it has one."""
        now = self.clock() if self._midi_seen else 0.0  # the clock is read only when there is an age to give
        seen = {controller: {control: {"count": count, "value": value, "ago": round(max(0.0, now - at), 1)}
                             for control, (count, value, at) in controls.items()}
                for controller, controls in self._midi_seen.items()}
        if self._listeners:
            return {**self._midi, "heard": sum(listener.heard for listener in self._listeners), "seen": seen}
        return {**self._midi, "seen": seen}

    async def _rig_answer(self) -> dict[str, Any]:
        """/rig: `describe_rig`, and when the running rig's map switches rigs by
        Program Change, which number is which rig (`programs`), read now, as a
        Program Change reads it (off the loop: it lists a folder)."""
        described = self.describe()
        plan = self._midi_plan
        if plan is not None and plan.programs:
            rigs = await asyncio.to_thread(self._midi_world.rigs, self._midi_root())
            described["programs"] = midi_listener.programs_table(rigs)
        return described

    def _midi_say(self, message: str) -> None:
        print(f"sushi-rig agent: MIDI: {message}", file=sys.stderr, flush=True)

    def _midi_off(self, reason: str) -> None:
        self._midi.update(state="off", reason=reason, rig=None, controller=None, port=None,
                          bindings=[], programs=False, feeds=[], problem=None, rig_problem=None)

    def _midi_fail(self, problem: str) -> None:
        self._midi.update(state="failed", reason=None, problem=problem)
        self._midi_say(problem)

    async def midi_adopt(self, app=None) -> None:
        """At the agent's start: listen for a rig already up, which `up` from
        the CLI leaves running whether or not an agent was there to see it."""
        await self._midi_sync()

    async def midi_release(self, app=None) -> None:
        """At the agent's end: cancel the actions in flight and wait for them,
        then stop the listener. In that order, and with `_midi_released` set
        first, because a switch cancelled inside `_run_lifecycle` runs its
        finally, whose `_midi_sync` would otherwise start a fresh listener
        after this one had stopped the old."""
        self._midi_released = True
        for task in list(self._midi_tasks):
            task.cancel()
        await asyncio.gather(*list(self._midi_tasks), return_exceptions=True)
        await self._midi_stop("the agent is stopping")

    async def _midi_sync(self) -> None:
        """Make the listener match the rig as it now is: none for no rig, or a
        rig with nothing for the agent; the one its files describe otherwise,
        which on a machine with a rig-stepping pedal (#191) is every rig.
        A listener already serving that same plan is left alone. Never raises:
        whatever goes wrong is the listener's problem, in /health, and not the
        lifecycle command's. Does nothing once the agent has released MIDI."""
        if self._midi_released:
            return
        described = self.describe()
        try:
            plan = await asyncio.to_thread(self._midi_world.plan, described, self._midi_root())
        except Exception as exc:  # noqa: BLE001 -- the reason goes to /health and stderr
            await self._midi_stop("the rig's files could not say what to serve")
            detail = str(exc) if isinstance(exc, MidiError) else f"{type(exc).__name__}: {exc}"
            self._midi_fail(f"MIDI is off for the running rig: {detail}")
            return
        running = bool(described.get("running"))
        if isinstance(plan, midi_listener.Off):
            await self._midi_stop(plan.reason)
            self._midi_off(plan.reason)
            await self._midi_unlink_left(None, running)
            return
        if plan == self._midi_plan and self._midi["state"] in ("opening", "listening"):
            return
        if self._midi_swappable(plan):
            self._midi_swap(plan)
            return
        await self._midi_stop("replaced")
        await self._midi_unlink_left(plan, running)
        if not self._midi_released:  # released while the plan was being read
            self._midi_start(plan)

    async def _midi_unlink_left(self, plan: midi_listener.Plan | None, running: bool) -> None:
        """Take down the agent's own links of a pedal the rig no longer has a
        feed for (#210's review), the rig still running: a re-plan that
        drops a port leaves no listener of it to do so, as the next listener
        of a port it keeps does (`AlsaPort._links`). A port opened with no
        link asked for takes down what this process made of it, and nothing
        else. With the rig down, Sushi and its links are gone."""
        keep = {feed.port for feed in plan.feeds} if plan is not None else set()
        left = sorted(self._midi_links - keep) if running else []
        self._midi_links = ({feed.port for feed in plan.feeds if feed.sushi_port is not None}
                            if plan is not None and running else set())
        for port in left:
            await asyncio.to_thread(self._midi_unlink, port)

    def _midi_unlink(self, port_name: str) -> None:
        """Off the loop: open `port_name` asking for no link, and close it."""
        try:
            opened = self._midi_world.open_port(port_name)
        except Exception as exc:  # noqa: BLE001 -- said; the link, if any, stays
            self._midi_say(f"could not open {port_name!r} to take the agent's link to Sushi down: {exc}")
            return
        try:
            self._midi_say(f"{port_name}: {getattr(opened, 'link_reason', None) or 'looked at, no link asked for'}")
        finally:
            try:
                opened.close()
            except Exception:  # noqa: BLE001 -- a port closing changes no link
                pass

    def _midi_swappable(self, plan: midi_listener.Plan) -> bool:
        """Whether `plan` can take the running listeners' place without
        them stopping (#192's review): the same ports for the same
        controllers, in the same order, each listener still running. A pedal
        assigned live changes bindings, not ports."""
        old = self._midi_plan
        if old is None or len(self._listeners) != len(old.feeds) or self._midi["state"] in ("off", "failed"):
            return False
        if any(not listener.thread.is_alive() for listener in self._listeners):
            return False
        # The same Sushi input to link to, too (#210): a feed whose link
        # changes is a new listener, which links as it opens.
        ports = lambda p: [(f.controller, f.port, f.sushi_port) for f in p.feeds]  # noqa: E731
        return ports(plan) == ports(old)

    def _midi_swap(self, plan: midi_listener.Plan) -> None:
        """Serve `plan` on the listeners already running, as the next message
        reads its bindings (`_midi_heard`): nothing stops, the generation is
        the same, so a press queued or a heel's silencing in flight goes on
        as it was. What is kept of each control (a switch's last state, the
        pedal's last value, a silencing) is kept only where its binding did
        not change. `required` follows the plan, a pedal not plugged in going
        from not connected to waiting, or back."""
        old, self._midi_plan = self._midi_plan, plan
        generation = self._midi_generation
        for index, (was, now) in enumerate(zip(old.feeds, plan.feeds)):
            before = {b.control: b for b in was.bindings.controls}
            after = {b.control: b for b in now.bindings.controls}
            changed = {c for c in before.keys() | after.keys() if before.get(c) != after.get(c)}
            switches = self._midi_switches.get(index)
            if switches:
                self._midi_switches[index] = {c: v for c, v in switches.items() if c not in changed}
            for control in changed:
                self._midi_pedal.pop((index, control), None)
                self._midi_silenced.discard((generation, index, control))
            entry = self._midi["feeds"][index]
            if not entry.get("linked_to_sushi") and now.sushi_port is None and now.link_reason:
                entry["link"] = now.link_reason
            if entry["required"] != now.required:
                entry["required"] = now.required
                absence = self._midi_absence.get(index)
                if absence is not None and entry["state"] in ("absent", "waiting"):
                    self._midi_absent(generation, index, absence)
        self._midi.update(
            rig=plan.rig, controller=plan.controller, port=plan.port, programs=plan.programs,
            bindings=[b.describe() for feed in plan.feeds for b in feed.bindings.controls],
            rig_problem=plan.rig_problem,
        )
        self._midi_feed_state()

    def _midi_start(self, plan: midi_listener.Plan) -> None:
        loop = asyncio.get_running_loop()
        generation = self._midi_generation
        self._midi_plan = plan
        self._midi_switches = {}
        self._midi_absence = {}
        self._midi.update(
            state="opening", reason=None, rig=plan.rig, controller=plan.controller, port=plan.port,
            bindings=[b.describe() for feed in plan.feeds for b in feed.bindings.controls],
            programs=plan.programs,
            feeds=[{"controller": feed.controller, "port": feed.port, "required": feed.required,
                    "state": "opening", "problem": None, "reason": None,
                    "linked_to_sushi": False, "link_wanted": feed.sushi_port is not None,
                    "link": feed.link_reason if feed.sushi_port is None else "not linked yet: opening the port"}
                   for feed in plan.feeds],
            heard=0, last=None, problem=None, last_error=None, rig_problem=plan.rig_problem,
        )
        if plan.rig_problem:
            self._midi_say(plan.rig_problem)
        for index, feed in enumerate(plan.feeds):
            self._listeners.append(midi_listener.Listener(
                feed.port, self._midi_world.open_port,
                post=loop.call_soon_threadsafe,
                on_open=functools.partial(self._midi_opened, generation, index),
                on_message=functools.partial(self._midi_heard, generation, feed=index),
                on_failure=functools.partial(self._midi_failed, generation, index),
                on_absent=functools.partial(self._midi_absent, generation, index),
                link=feed.sushi_port,
                on_link=functools.partial(self._midi_linked, generation, index),
            ))
        for listener in self._listeners:
            listener.start()

    async def _midi_stop(self, reason: str) -> None:
        """Stop the listeners, if there are any, and drop what they handed over
        that has not been acted on. Waits (off the loop) for their threads to
        let go of the ports; a thread stuck in ALSA past that is said and left,
        as a daemon, rather than holding up a `down`."""
        self._midi_generation += 1
        self._midi_switches = {}
        self._midi_pedal = {}
        self._midi_silenced = set()
        listeners, self._listeners = self._listeners, []
        if not listeners:
            return
        self._midi_plan = None
        self._midi["heard"] = sum(listener.heard for listener in listeners)
        for listener in listeners:
            if not await asyncio.to_thread(listener.stop, 2 * midi_listener.POLL_SECONDS + 1.0):
                self._midi_say(f"the listener on {listener.port_name!r} did not stop; left to end with the agent")
        self._midi_off(reason)

    def _midi_feed_state(self) -> None:
        """The listener's state from its ports' (#191): failed if any port
        failed, or a port the rig's own map needs is not plugged in (the
        others still serve); opening while any is; listening once one is and
        the rest are listening or not connected; absent when no pedal at all
        is plugged in and none the rig needs is missing. A port no rig map
        needs that is not plugged in is `absent`, "not connected", and not a
        problem: the machine's rig-stepping pedal on a laptop without it."""
        feeds = self._midi["feeds"]
        problems = [f["problem"] for f in feeds if f["state"] in ("failed", "waiting")]
        if problems:
            self._midi.update(state="failed", reason=None, problem="; ".join(problems))
        elif any(f["state"] == "opening" for f in feeds):
            self._midi.update(state="opening", problem=None)
        elif any(f["state"] == "listening" for f in feeds):
            self._midi.update(state="listening", reason=None, problem=None)
        else:
            absent = "; ".join(f"{f['controller']} ({f['port']}): {f['reason']}" for f in feeds)
            self._midi.update(state="absent", reason=f"no pedal is connected: {absent}", problem=None)

    def _midi_opened(self, generation: int, feed: int = 0) -> None:
        if generation == self._midi_generation:
            self._midi_absence.pop(feed, None)
            self._midi["feeds"][feed].update(state="listening", problem=None, reason=None)
            self._midi_feed_state()

    def _midi_linked(self, generation: int, feed: int, linked: bool, reason: str) -> None:
        """Port `feed`'s pedal is linked to Sushi's MIDI input, or not (#210):
        `/health`'s `linked_to_sushi` and `link`, and a line on stderr for
        each change, the log to read when a sweep does nothing."""
        if generation != self._midi_generation:
            return
        entry = self._midi["feeds"][feed]
        plan = self._midi_plan
        planned = plan.feeds[feed] if plan is not None and feed < len(plan.feeds) else None
        # Not linked, and not to be: the plan's why (#210's review) is what
        # to read, the port's own words going to stderr.
        shown = (planned.link_reason if not linked and planned is not None and planned.sushi_port is None
                 and planned.link_reason and reason == midi_listener.NOT_ASKED else reason)
        if (entry.get("linked_to_sushi"), entry.get("link")) != (linked, shown):
            entry.update(linked_to_sushi=linked, link=shown)
            self._midi_say(f"{entry['controller']}: {reason}")

    def _midi_who(self, feed: int) -> tuple[str, str]:
        plan = self._midi_plan
        port = plan.feeds[feed].port if plan else self._midi["port"]
        controller = plan.feeds[feed].controller if plan else None
        rig = plan.running if plan else "the running rig"
        who = (f"the port of controller {controller!r}, which {rig}'s MIDI map or the machine file's "
               "rig stepping is written for" if plan else "the port")
        return port, who

    def _midi_absent(self, generation: int, feed: int, reason: str) -> None:
        """Port `feed`'s pedal is not plugged in, or was unplugged (#191): its
        listener waits for it. Not connected (`absent`) for a pedal the rig's
        map does not use; `waiting`, a problem, for one it does."""
        if generation != self._midi_generation:
            return
        self._midi_absence[feed] = reason  # what to say again if the plan's `required` changes
        entry = self._midi["feeds"][feed]
        if not entry["required"]:
            entry.update(state="absent", reason=f"not connected: {reason}", problem=None)
        else:
            port, who = self._midi_who(feed)
            problem = (f"MIDI port {port!r}, {who}, is not connected: {reason}. Waiting for it: "
                       "plug it in and it is picked up")
            entry.update(state="waiting", reason=reason, problem=problem)
            self._midi_say(problem)
        self._midi_feed_state()

    def _midi_failed(self, generation: int, feed: int, exc: BaseException, opened: bool) -> None:
        if generation != self._midi_generation:
            return
        entry = self._midi["feeds"][feed]
        if not entry["required"] and isinstance(exc, (midi_listener.PortAbsent, midi_listener.MidiUnavailable)):
            # Nothing the rig's map needs: a machine that cannot read MIDI at
            # all, or a pedal not there, is quiet for a pedal it does not use.
            entry.update(state="absent", reason=f"not connected: {exc}", problem=None)
            self._midi_feed_state()
            return
        port, who = self._midi_who(feed)
        doing = "stopped reading" if opened else "could not open"
        problem = (f"{doing} MIDI port {port!r}, {who}: {exc}. Bypass and rig switching "
                   "from the pedals on it are off until the rig is next brought up or restarted")
        entry.update(state="failed", problem=problem)
        self._midi_say(problem)
        self._midi_feed_state()

    def _midi_heard(self, generation: int, message: midi_listener.Message, feed: int = 0) -> None:
        """A message from port `feed`'s listener, on the loop: decided now, in
        the order played, and acted on in a task of its own, one at a time.

        A Program Change is decided in its task instead, because deciding it
        lists config/rigs/, which is file access and not for the loop; so is a
        rig-stepping press (#191). The switch state `dispatch` returns is not
        kept here: `_midi_act` commits it once the action has gone through."""
        plan = self._midi_plan
        if generation != self._midi_generation or plan is None:
            return
        bindings = plan.feeds[feed].bindings
        # What is pushed (#139): "midi" when /health's MIDI changes (a named
        # control's count, its light; a Program Change, which is decided
        # after), for which a page reads /health alone; and "live" too when
        # the message moves a parameter (a CC Sushi maps: a sweep, a volume,
        # a blend; or a heel's snap below), whose change is in /live, and
        # which nothing else would push: Sushi moves it with no write here.
        if self._midi_saw(plan.feeds[feed], message) or message.kind == "program":
            self.events.publish("midi")
        if plan.feeds[feed].moves_sushi(message):
            self.events.publish("live")
        try:
            if message.kind == "program":
                if bindings.programs is None:
                    action = None
                else:
                    action = message  # decided in `_midi_act`, off the loop
            else:
                # A switch's crossing is recorded as it is decided, so the values
                # that follow on the same side (a pedal sends several) are not
                # crossings too; `_midi_forget` takes it back if the action is
                # dropped or fails, so the next value plays it again.
                action, self._midi_switches[feed] = midi_listener.dispatch(
                    message, bindings, self._midi_switches.get(feed, {}), now=time.monotonic())
                pedal = midi_listener.snap_binding(message, bindings)
                if pedal is not None:
                    self.events.publish("live")  # a heel's snap writes the send's gain, logging nothing
                    key = (feed, pedal.control)
                    self._midi_pedal[key] = (message.value, self._midi_pedal.get(key, (0, 0))[1] + 1)
                    if message.value > pedal.heel and (generation, *key) in self._midi_silenced:
                        # Above the heel after the agent silenced it: Sushi has
                        # set its value, unless the silence landed after that.
                        action = midi_listener.Snap(pedal.control, pedal.target, pedal)
        except Exception as exc:  # noqa: BLE001 -- a loop callback's error would reach only the loop's log
            self._midi["last_error"] = {
                "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "message": f"{message.describe()}: {type(exc).__name__}: {exc}",
            }
            self._midi_say(self._midi["last_error"]["message"])
            self.events.publish("midi")
            return
        self._midi_last(message, action)
        if action is None:
            return
        if isinstance(action, midi_listener.Snap):
            self._midi_snap_start(generation, feed, action)
            return
        switching = action is message or isinstance(action, midi_listener.StepRig)
        if switching:
            refusal = self._midi_switch_refusal()
            if refusal is not None:
                self._midi_error(action, refusal)
                self._midi_decided(f"refused, {refusal}")
                return
        if len(self._midi_tasks) >= MIDI_PENDING:
            # Sushi is not keeping up (a stuck call holds each action up to
            # LIVE_TIMEOUT): a pedal's presses queued behind it would all land
            # at once, long after they were played.
            dropped = f"{len(self._midi_tasks)} MIDI actions are still waiting on Sushi, so this one was dropped"
            self._midi_error(action, dropped)
            self._midi_decided(f"dropped, {dropped}")
            self._midi_forget(action, feed=feed)
            return
        if switching:
            self._midi_switch_pending = True
        said = self._midi["last"]
        task = asyncio.ensure_future(self._midi_act(generation, action, feed, switching, said))
        self._midi_tasks.add(task)
        task.add_done_callback(self._midi_tasks.discard)

    def _midi_saw(self, feed: midi_listener.Feed, message: midi_listener.Message) -> bool:
        """Count `message` against the control it came from (#192), bound or
        not: a dict lookup and a tuple, on the loop, nothing near the audio.
        Whether it was counted: a message of no control the machine file
        names changes no light, and is not pushed (#139)."""
        control = feed.control_of(message)
        if control is None:
            return False
        controls = self._midi_seen.setdefault(feed.controller, {})
        count = controls.get(control, (0, 0, 0.0))[0]
        controls[control] = (count + 1, message.value, self.clock())
        return True

    def _midi_decided(self, said: str, deciding: bool = False) -> None:
        """What became of the last message, once it is known (#191): a rig
        switch refused or dropped, a step or a Program Change decided, so
        `/health`'s `last` does not stay at "deciding which rig". `deciding`
        for a decision made later, in its task: only while `last` is still
        the message waiting on it, not a newer one."""
        last = self._midi["last"]
        if last is not None and (not deciding or last["action"].endswith("deciding which rig")):
            if last["action"] != said:
                self.events.publish("midi")  # /health's `midi.last` (#139)
            last["action"] = said

    def _midi_snap_start(self, generation: int, feed: int, action: Any) -> None:
        """Silence a pedalled volume at the heel (#191), in one task a pedal:
        a burst of messages at the heel is read by the task already running."""
        key = (generation, feed, action.control)
        if key in self._midi_snapping:
            return
        self._midi_snapping.add(key)
        task = asyncio.ensure_future(self._midi_snap(generation, feed, action))
        self._midi_tasks.add(task)
        task.add_done_callback(self._midi_tasks.discard)

    async def _midi_snap(self, generation: int, feed: int, action: Any) -> None:
        """Set the send's gain to what the pedal's latest value says: silent at
        or below the heel, which Sushi's mapping cannot be (it stops at its
        floor), and otherwise exactly what Sushi's mapping gives, so that a
        silencing that lands after the pedal has left the heel is put right
        rather than fighting the mapping. Again until no message came while
        it was setting. Not in the live log: a pedal's position is not a change
        to undo or to save."""
        key, binding = (feed, action.control), action.binding
        mine = (generation, feed, action.control)
        origin = _MidiOrigin("MIDI", action.control)
        try:
            while generation == self._midi_generation:
                value, count = self._midi_pedal.get(key, (binding.heel, 0))
                gain = binding.gain_at(value)
                try:
                    async with self._connected_for_a_change(origin) as controller:
                        await self._sushi(origin, self.live.set_parameter, controller, action.processor, "gain",
                                          gain, reading=False)
                except web.HTTPConflict as exc:
                    # Refused while a lifecycle command or a rig file edit (a
                    # pedal's assignment, #192) holds the agent: the pedal is
                    # still where it is, so once it is free this goes again,
                    # from its latest value. A down or a switch moves the
                    # generation on, which ends it.
                    if getattr(exc, "agent_code", None) != "busy" or generation != self._midi_generation:
                        raise
                    await self._midi_until_free(generation)
                    if not self._midi_still_bound(feed, binding):
                        # The plan swapped while it waited (an assignment
                        # applied live): this pedal's heel is not this
                        # binding's any longer, and the new plan's own
                        # messages decide what it does.
                        self._midi_silenced.discard(mine)
                        return
                    continue
                if generation == self._midi_generation:
                    if gain == 0.0:
                        self._midi_silenced.add(mine)
                    else:
                        self._midi_silenced.discard(mine)
                if self._midi_pedal.get(key, (value, count))[1] == count:
                    break
        except web.HTTPException as exc:
            self._midi_error(action, exc.reason)
        except (AgentError, MidiError) as exc:
            self._midi_error(action, str(exc))
        except Exception as exc:  # noqa: BLE001 -- said, with its type; the agent carries on
            self._midi_error(action, f"{type(exc).__name__}: {exc}")
        finally:
            self._midi_snapping.discard(mine)

    def _midi_still_bound(self, feed: int, binding: Any) -> bool:
        """Whether the running plan still has `binding` for its control on
        port `feed` (`_midi_swap` can change it within a generation)."""
        plan = self._midi_plan
        if plan is None or feed >= len(plan.feeds):
            return False
        return any(b == binding for b in plan.feeds[feed].bindings.controls if b.control == binding.control)

    async def _midi_until_free(self, generation: int) -> None:
        """Wait, without taking it, until the lifecycle lock is free, or the
        listener of `generation` has been stopped. Polled: a heel's silencing
        waits on it rarely, and briefly."""
        while self._lock.locked() and generation == self._midi_generation:
            await asyncio.sleep(MIDI_FREE_POLL)

    def _midi_switch_refusal(self) -> str | None:
        """Why a pedal's rig switch (a Program Change, or a step, #191) cannot
        start now, or None: one already asked for and not done, or any
        lifecycle command or switch holding the lock. Refused and said, never
        queued, so presses during a switch's silence do not pile up into more
        switches after it. The one place a guard on rig-switching presses (a
        double tap) would go."""
        if self._midi_switch_pending:
            return "a rig switch from the pedals is still running: press again once it is done"
        if self._lock.locked():
            return f"busy: '{self.busy}' is still running, so the rig is not switched: press again once it is done"
        return None

    def _midi_last(self, message: midi_listener.Message, action: Any) -> None:
        if action is message:
            done = "deciding which rig"
        elif isinstance(action, midi_listener.StepRig):
            done = f"{action.describe()}: deciding which rig"
        elif action is None:
            done = "nothing (not bound, a release, or no change)"
        else:
            done = action.describe()
        self._midi["last"] = {
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "message": message.describe(), "action": done,
        }

    def _midi_error(self, action: Any, message: str) -> None:
        """An action that could not be done: there is no client to answer, so
        it is said on stderr and kept in /health (`agent.midi.last_error`)."""
        text = f"{action.describe()}: {message}"
        self._midi["last_error"] = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "message": text}
        self._midi_say(text)
        self.events.publish("midi")  # /health's `midi.last_error` (#139)

    async def _midi_act(self, generation: int, action: Any, feed: int = 0, switching: bool = False,
                        said: dict[str, Any] | None = None) -> None:
        try:
            await self._midi_act_locked(generation, action, feed, said)
        finally:
            if switching:
                self._midi_switch_pending = False

    async def _midi_act_locked(self, generation: int, action: Any, feed: int,
                               said: dict[str, Any] | None = None) -> None:
        async with self._midi_lock:
            if generation != self._midi_generation:
                # The listener that heard it has been stopped since (a rig
                # coming down, or replaced): not done, and said so.
                dropped = "dropped: the listener that heard it was stopped before it was done"
                if said is not None:
                    said["action"] = f"{said['action']}, {dropped}"
                self._midi_error(action, dropped)
                return
            if isinstance(action, (midi_listener.Message, midi_listener.StepRig)):  # a rig, to decide
                plan = self._midi_plan
                try:
                    rigs = await asyncio.to_thread(self._midi_world.rigs, self._midi_root())
                except Exception as exc:  # noqa: BLE001 -- said, the agent carries on
                    said = action.describe()
                    self._midi["last_error"] = {
                        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                        "message": f"{said}: reading config/rigs/ failed: {type(exc).__name__}: {exc}"}
                    self._midi_say(self._midi["last_error"]["message"])
                    self._midi_decided(f"{said}: reading config/rigs/ failed", deciding=True)
                    return
                if generation != self._midi_generation or plan is None:
                    return
                if isinstance(action, midi_listener.StepRig):
                    action = midi_listener.step_to(action, rigs, plan.rig, plan.running)
                    self._midi_decided(action.describe(), deciding=True)
                else:
                    message = action
                    action, _ = midi_listener.dispatch(message, plan.feeds[feed].bindings, {}, rigs, plan.rig)
                    self._midi_decided("nothing (the rig already playing)" if action is None
                                       else action.describe(), deciding=True)
                if action is None:
                    return
            # What `_sushi` and the live lock read off a request, for a press.
            origin = _MidiOrigin("MIDI", getattr(action, "control", None) or f"program {action.program}")
            try:
                if isinstance(action, midi_listener.Refused):
                    raise MidiError(action.reason)
                if isinstance(action, (midi_listener.ToggleBypass, midi_listener.SetBypass)):
                    await self._midi_bypass(origin, action)
                elif isinstance(action, (midi_listener.ToggleMute, midi_listener.SetMute)):
                    await self._midi_mute(origin, action)
                elif isinstance(action, (midi_listener.SelectRig, midi_listener.StepTo)):
                    await self._midi_switch(action)
            except web.HTTPException as exc:  # what a route would have answered
                self._midi_error(action, exc.reason)
                self._midi_forget(action, generation, feed)
            except (AgentError, MidiError) as exc:
                self._midi_error(action, str(exc))
                self._midi_forget(action, generation, feed)
            except Exception as exc:  # noqa: BLE001 -- said, with its type; the agent carries on
                self._midi_error(action, f"{type(exc).__name__}: {exc}")
                self._midi_forget(action, generation, feed)

    def _midi_forget(self, action: Any, generation: int | None = None, feed: int = 0) -> None:
        """Take back the crossing a CC switch's dropped or failed action
        recorded, so the next value on that side plays it again. Only while
        the switch still holds this action's state (a later crossing stands)
        and the listener that heard it is still the one listening."""
        if not isinstance(action, (midi_listener.SetBypass, midi_listener.SetMute)):
            return
        if generation is not None and generation != self._midi_generation:
            return
        switches = self._midi_switches.get(feed, {})
        level = action.muted if isinstance(action, midi_listener.SetMute) else not action.bypassed
        if switches.get(action.control) == level:
            self._midi_switches[feed] = {k: v for k, v in switches.items() if k != action.control}

    def _midi_logged(self, entry: dict[str, Any], control: str | None) -> None:
        """Mark a live-log entry as MIDI's. `_record` returns the entry it
        appended, so this marks the one in the log: no await comes between."""
        entry["source"] = "midi"
        if control is not None:
            entry["control"] = control

    async def _midi_bypass(self, origin: "_MidiOrigin", action: Any) -> None:
        """`POST /live/bypass`'s path: one connection, the live lock, the same
        read and the same check, then the set. A toggle sets the opposite of
        what Sushi reports now; a switch sets its own state, and does nothing
        when the plugin is already there (which is not logged, as a move
        that changes nothing is not)."""
        processor = action.processor
        want = action.bypassed if isinstance(action, midi_listener.SetBypass) else None
        async with self._connected_for_a_change(origin) as controller:
            reading = await self._sushi(origin, self.live.bypass, controller, processor, reading=True)
            previous = check_bypass(reading, processor)
            bypassed = midi_listener.next_bypass(previous, want)
            if bypassed == previous:
                return
            await self._sushi(origin, self.live.set_bypass, controller, processor, bypassed, reading=False)
            self._midi_logged(
                self._record("bypass", {"processor": processor, "bypassed": bypassed}, previous), action.control)

    async def _midi_mute(self, origin: "_MidiOrigin", action: Any) -> None:
        """A channel's mute (#210): its send bypassed, by the bypass path
        (`_midi_bypass`), so what the monitors hear loses the channel. Not the
        track's own `mute`: Sushi applies that after the track's plugins, so
        after the send, and it silenced only the channel's stem (heard on the
        laptop, 2026-11-23). The send's `gain`, which a volume pedal drives,
        is not touched, so the two do not fight. The channel's stem, its own
        output after the send, still plays."""
        send = action.track + rigfile.SEND_SUFFIX
        if isinstance(action, midi_listener.SetMute):
            await self._midi_bypass(origin, midi_listener.SetBypass(action.control, send, action.muted))
        else:
            await self._midi_bypass(origin, midi_listener.ToggleBypass(action.control, send))

    async def _midi_switch(self, action: Any) -> None:
        """Switch to rig `action.rig` by the path `POST /rig/switch` takes
        (`_switch`, #93): live where the two rigs' configs allow it, and
        otherwise the restart this used to be, `down` then `up --rig` with the
        options `restart` would bring back, with the previous rig brought back
        if its `up` fails. A rig that would not compile is refused before
        anything is touched. The log entry is marked as MIDI's, and for a
        step (#191) with the footswitch that asked."""
        if isinstance(action, midi_listener.StepTo):
            origin, program, control = _MidiOrigin("MIDI", action.control), None, action.control
        else:
            origin, program, control = _MidiOrigin("MIDI", f"program {action.program}"), action.program, None
        # A restart instead of a live switch is said on stderr by `_switch`,
        # and is in the log entry's `why`.
        answer = await self._switch(
            origin, action.rig, program=program, mark=lambda entry: self._midi_logged(entry, control))
        if not answer["ok"]:
            raise MidiError(f"'{' '.join(answer['command'])}' failed: {answer['reason']}")

    def _midi_routes(self, app) -> None:
        app.router.add_get("/midi", self.midi_route)
        app.router.add_post("/rig/midi/assign", self.midi_assign_route)

    async def midi_route(self, request):
        """`GET /midi` (#191): the machine's MIDI devices as the rack's pedal
        assignments (#192) need them, and the running rig's assignments.

        `controllers`: each controller of the machine file the run was
        compiled against (this host's with no rig up), `{name, type, port,
        channel, controls}`, each control `{name, sends, kind, behaviour,
        range, job, takes}` -- `takes` is what a rig's map may bind it to
        (`rigfile.takes`: none for a control with a job, which steps rigs on
        every rig). `targets` is each binding's target as the rig file writes
        it. `rig` is the running rig file's name or None, `assignments` its
        `midi:` section as the file says it (None without one), `rigs` the
        order a step and a Program Change count through, and `listening` the
        listener's state (`/health`'s `agent.midi`). A machine file that will
        not read is `machine_problem`, with no controllers; a rig file that
        will not, `rig_problem`. A read: it changes nothing and takes no lock."""
        described = self.describe()
        answer = await asyncio.to_thread(self._midi_model, described)
        answer["listening"] = self._midi_status()["state"]
        return web.json_response({"ok": True, **answer})

    def _midi_model(self, described: dict[str, Any]) -> dict[str, Any]:
        from .machine import MachineError, MachineSpec, machine_path

        root = self._midi_root()
        machine_file = Path(described["machine"]) if described.get("machine") else machine_path(root)
        answer: dict[str, Any] = {
            "machine": machine_file.name, "machine_problem": None, "controllers": [],
            "targets": dict(rigfile.TARGET_SHAPES), "rig": None, "assignments": None, "rig_problem": None,
            "rigs": midi_listener.program_order(root), "behind": None,
        }
        try:
            machine = MachineSpec.load(machine_file)
            problems = machine.validate()
            if problems:
                raise MachineError("; ".join(problems))
        except MachineError as exc:
            answer["machine_problem"] = " ".join(f"{machine_file.name}: {exc}".split())
        else:
            for device in machine.devices():
                model = machine.controller(device["name"]).get("model") or {}
                for control in device["controls"]:
                    (kind,) = control["sends"]
                    control["takes"] = list(rigfile.takes(kind, model.get(control["name"])))
                answer["controllers"].append(device)
        source = described.get("rig") if described.get("running") else None
        if isinstance(source, str) and source:
            answer["rig"] = Path(source).stem
            try:
                rig = rigfile.load_rig(root / source)
            except Exception as exc:  # noqa: BLE001 -- said; the devices are still worth answering
                answer["rig_problem"] = " ".join(f"{source}: {type(exc).__name__}: {exc}".split())
            else:
                answer["behind"] = self._midi_behind(described, root / source, machine_file)
                if rig.midi is not None:
                    answer["assignments"] = {
                        "controller": rig.midi.controller, "port": rig.midi.port, "programs": rig.midi.programs,
                        "controls": [
                            {"control": c.control, c.binding: list(c.target) if isinstance(c.target, tuple)
                             else c.target, **({"range": list(c.range)} if c.range != (0.0, 1.0) else {})}
                            for c in rig.midi.controls
                        ],
                    }
        return answer

    def _midi_behind(self, described: dict[str, Any], path: Path, machine_file: Path) -> dict[str, Any] | None:
        """Whether the running rig's pedal mappings are behind its file
        (#210): `map_behind` of the running config and a compile of the file
        as it is now, with `restart` true when Sushi's half is behind (only a
        restart loads cc_mappings, where they could not be made live). None
        where either cannot be read: a file that will not compile is
        `rig_problem`'s, said beside it. Off the loop (`midi_route`)."""
        import yaml

        from .rig import compile_rig_draft

        try:
            compiled = compile_rig_draft(yaml.safe_load(path.read_text()), self._midi_root(), machine_file)
            running = self.switching.read_config(described.get("config"))
        except Exception:  # noqa: BLE001 -- not known, and said as not known
            return None
        behind = map_behind(running, compiled)
        if behind is not None:
            # A live change that could not be confirmed (#210's review):
            # what Sushi maps is not known, and a restart is what makes it so.
            behind["unconfirmed"] = running.get(rigfile.MIDI_UNCONFIRMED) or None
            behind["restart"] = behind["midi"] or bool(behind["unconfirmed"])
        return behind

    # ------------------------------------------------- assigning a pedal (#192)
    #
    # POST /rig/midi/assign writes one control of the running rig's file's
    # `midi:` (rigedit.with_assignment), as the rack's channel edits write
    # their file: under the lifecycle lock (`_rigfile_locked`, busy "rig file
    # edit"), compiled first against the run's machine file and refused with
    # the compiler's words, nothing written; then written in place with its
    # comments (`rigfile.write_rig`), and never committed.
    #
    # Live or at the restart, as Sushi allows. Sushi loads its cc_mappings only
    # with the config (rigfile.RESTART_REASONS' "midi"), so an assignment
    # whose compile leaves the running config's `midi` section as it is (a
    # switch on a bypass, a momentary switch on a mute: the agent's bindings
    # alone) is applied now: the running config's `_midi_agent` is replaced
    # with the edited file's and the listener re-planned (`_midi_sync`). Any
    # other (a pedal on a volume or a parameter, a latching switch on a mute)
    # changes what Sushi maps, and plays after the restart the answer offers,
    # refused first while the rig holds unsaved work that restart would drop
    # (`rigfile_refusal`), as the channel edits are. Not in the live log and
    # not undone by Undo, as a channel added to the file is not: assign
    # again to change it.

    async def midi_assign_route(self, request):
        """`POST /rig/midi/assign {"control", <binding>: <target>}` (a binding
        of `rigfile.TARGET_SHAPES`: `bypass`, `mute`, `volume`, `parameter`,
        `blend`), or `{"control", "none": true}`, with `"controller"` when
        the rig's map names none and the machine has several devices.

        200 with `how`: `live` (the listener serves it now), or `restart`
        (`offer: "restart"`, `why`, as a channel edit's); `change` says what
        was written (`was`, `moved`: the control's previous assignment,
        `replaced`: another control taken off the same target,
        `controller_set`), and `assignments`, the file's `midi:` after it.
        409 with nothing written: `no_rig`, `machine`, `controller` or
        `which_controller` (with `controllers` to choose from),
        `rig_problem` (the compiler's refusal, `compile_midi`'s words for a
        control that cannot take that target), and for a change that needs a
        restart, `unsaved` and `panel` (`rigfile_refusal`). 400 for a body of
        the wrong shape."""
        from . import rigedit

        try:
            asked = rigedit.assign_request(await self._json_body(request))
        except rigedit.EditRequestError as exc:
            raise web.HTTPBadRequest(reason=str(exc))
        answer = await self._rigfile_locked(request, lambda run: self._write_assignment(request, run, asked))
        return web.json_response(await self._rigfile_answer(answer))

    async def _write_assignment(self, request, run: dict[str, Any], asked: dict[str, Any]) -> dict[str, Any]:
        """The assignment, compiled, written and applied (`midi_assign_route`)."""
        import yaml

        from . import rigedit
        from .machine import MachineError, MachineSpec
        from .rigfile import RigFileError, write_rig

        path = run["running"]
        if path is None:
            raise RigFileRefused("no rig of config/rigs/ is running: a pedal is assigned on the running rig",
                                 "no_rig")
        machine_file = run["machine"]
        described, state = run["described"], run["state"]
        loop = asyncio.get_running_loop()
        finished = asyncio.Event()  # set when `work` is over, whatever became of the wait for it
        outcome: dict[str, Any] = {}  # what `work` found, for `_midi_sync_when`
        # Read under the live lock, so no live change lands between the check
        # for unsaved work and the write, as `_write_edit` does.
        async with self._live_lock:
            refusal = rigfile_refusal(state, self.log)
            edits = live_edits(self._chain_log, since=state.get("started_at"))

            def work() -> dict[str, Any]:
                try:
                    return decide_and_write()
                finally:
                    loop.call_soon_threadsafe(finished.set)

            def decide_and_write() -> dict[str, Any]:
                try:
                    machine = MachineSpec.load(machine_file)
                except MachineError as exc:
                    raise RigFileRefused(str(exc), "machine") from exc
                problems = machine.validate()
                if problems:
                    raise RigFileRefused(f"{machine_file.name}: " + "; ".join(problems), "machine")
                before = path.read_bytes()  # for Undo (#209)
                raw = yaml.safe_load(before.decode("utf-8"))
                if not isinstance(raw, dict):
                    raise RigFileRefused(f"{path.name} is not a rig file", "rig_problem")
                current = raw.get("midi", {}).get("controller") if isinstance(raw.get("midi"), dict) else None
                if asked["binding"] is None:
                    noop = rigedit.unassigned_noop(raw, current, asked["control"], machine.devices())
                    if noop is not None:
                        if noop[1] is not None:
                            raise RigFileRefused(*noop[1])
                        return {"change": noop[0], "unchanged": True, "assignments": rigedit.midi_of(raw)}
                controller, refused = rigedit.pick_controller(current, asked["controller"], machine.devices())
                if refused is not None:
                    raise RigFileRefused(*refused)
                edited, change = rigedit.with_assignment(raw, controller, asked)
                if edited == raw:
                    return {"change": change, "unchanged": True, "assignments": rigedit.midi_of(raw)}
                compiled = self._rigfile_compile(edited, run)
                try:
                    running = self.switching.read_config(described.get("config"))
                except Exception as exc:  # noqa: BLE001 -- said: unreadable is a restart, as a switch says it
                    running, unread = None, f"{type(exc).__name__}: {exc}"
                else:
                    unread = None
                # Only Sushi's mappings differ (#210): tried live, after the
                # write (`_map_plan`). Not refused for unsaved work first:
                # if it is live there is no restart to lose it, and if it
                # falls back, the restart offered is itself refused until
                # the work is saved or dropped (`restart_refusal`).
                not_live, cc_plan = self._map_plan(running, compiled, edits)
                if not_live and cc_plan is None and refusal is not None:
                    raise RigFileRefused(*refusal)
                # The run as it was read under the locks, still the one up:
                # a write the route stopped waiting for (504) may get here
                # after a restart or a switch, and must not write over that
                # run's config or the rig file under it (#192's re-review).
                now = self.switching.read_state() or {}
                if run_changed(state, now):
                    outcome["stale"] = True
                    raise RigFileRefused(
                        "nothing was written: the rig was restarted or switched while this waited; assign "
                        "again on the rig that is up now", "changed")
                try:
                    problems = write_rig(path, edited)
                except RigFileError as exc:
                    raise RigFileRefused("nothing was written: " + "; ".join(exc.problems), "rig_problem") from exc
                if problems:
                    change["provenance_note"] = "; ".join(problems)
                if not not_live:
                    # Only the agent's bindings differ, and every one of them
                    # names what the rig has: the running config keeps saying
                    # what Sushi loaded, with the edited file's bindings and
                    # the controller they are read from.
                    unrecorded = record(running_with_map(running, compiled, midi=False))
                    if unrecorded:
                        not_live = [unrecorded]
                return {"change": change, "compiled": compiled, "not_live": not_live, "unread": unread,
                        "assignments": rigedit.midi_of(edited), "cc_plan": cc_plan, "running": running,
                        "before": before, "after": path.read_bytes()}  # before and after: for Undo (#209)

            record = functools.partial(self._record_running, path, state, outcome)

            try:
                done = await self._rigfile_thread(request, path, work)
            except web.HTTPGatewayTimeout:
                # The thread cannot be stopped and may still write the running
                # config: once it is over, the listener follows what it left.
                self._midi_sync_when(finished, outcome)
                raise
        if done.get("cc_plan") is not None and not outcome.get("stale"):
            done["not_live"] = await self._map_cc_live(request, path, state, outcome, done["running"],
                                                       done["compiled"], done["cc_plan"], done["not_live"])
        shown = self._save_shown(path)
        change = done["change"]
        what = f"{change['control']}: {rigedit.said_assignment(change['binding'], change['target'])}"
        answer = {"ok": True, "rig": path.stem, "file": shown, "how": "updated", "committed": False,
                  "change": change, "running": True, "path": path, "assignments": done["assignments"]}
        if done.get("unchanged"):
            answer.update(how="unchanged", takes_effect=None, offer=None, why=[],
                          note=(f"nothing to change: {what} is what {shown} already says, so it was not "
                                "written and the rig is as it was"))
            return answer
        label = "pedal" if change["control"] == "pedal" else f"pedal {change['control']}"  # not "pedal pedal"
        self._history_file(path, done["before"], done["after"], label, run=run,
                           apply="restart" if done["not_live"] else "bindings", act="pedal")
        if not done["not_live"]:
            await self._midi_sync()
            how = ("Sushi took the pedal's mapping live (not yet heard on a real Sushi: if it does not answer, "
                   "restart the rig)" if done.get("cc_plan") is not None
                   else "only the agent's bindings changed, and the listener serves them")
            answer.update(how="live", takes_effect="now", offer=None, why=[],
                          note=(f"{shown} was updated in place, with its comments; it is not committed. {what} "
                                f"plays now: {how}"))
            return answer
        why = [r for r in done["not_live"] if r["code"] != "midi"]
        why += await self._rigfile_restart_why(run, done["compiled"])
        if done["unread"] and not why:
            why = [{"code": "unreadable", "detail": done["unread"]}]
        answer.update(
            how="restart", takes_effect="restart", offer="restart", why=why,
            note=" ".join((
                f"{shown} was updated in place, with its comments; it is not committed. {what} plays when "
                f"{path.stem} restarts ({'; '.join(r['code'] + ': ' + r['detail'] for r in why) or 'a restart reloads the file'}); "
                "until then the pedals do what they did").split()))
        return answer

    # A pedal's map into the running rig, shared by an assignment and its
    # Undo (#209): what can be live (`_map_plan`), the running config
    # recording it (`_record_running`), and Sushi's mappings made live
    # (`_map_cc_live`, by `_midi_cc_live`).

    def _map_plan(self, running: dict[str, Any] | None, compiled: dict[str, Any],
                  edits: dict[str, Any] | None) -> tuple[list[dict[str, str]], tuple | None]:
        """`(not_live, cc_plan)` for taking the running rig's pedal map to
        `compiled`'s: `assignment_not_live`'s reasons, and, where the only
        one is Sushi's mappings (#210), the live plan for them
        (`live_cc_plan`), tried after the write through elkpy where this
        agent's Sushi connection can, a restart if that fails. Pure but for
        asking what the connection can do."""
        not_live = assignment_not_live(running, compiled, edits)
        cc_plan = None
        if [r["code"] for r in not_live] == ["midi"] and self._midi_cc_livable():
            planned = live_cc_plan(running, compiled, edits)
            if isinstance(planned, tuple):
                cc_plan = planned
            else:
                not_live.append({"code": "midi_live", "detail": planned})
        return not_live, cc_plan

    def _record_running(self, path: Path, state: dict[str, Any], outcome: dict[str, Any],
                        updated: dict[str, Any]) -> dict[str, str] | None:
        """Write `updated` as the running config, and point the state file
        at it; a reason when that failed (the file is written; the restart
        plays it). Off the loop."""
        try:
            written = self.switching.write_config(path.stem, updated)
            now = self.switching.read_state() or state
            # Again, just before: a switch landing since the check the caller
            # made must not have its run pointed at this rig's config.
            if run_changed(state, now):
                outcome["stale"] = True
            elif str(written) != str(now.get("config")):
                self.switching.write_state({**now, "config": str(written)})
        except Exception as exc:  # noqa: BLE001 -- the file is written; the restart plays it
            return {"code": "unrecorded",
                    "detail": (f"the file is written, but recording the running rig's bindings "
                               f"failed: {type(exc).__name__}: {exc}")}
        return None

    async def _map_cc_live(self, origin, path: Path, state: dict[str, Any], outcome: dict[str, Any],
                           running: dict[str, Any], compiled: dict[str, Any], cc_plan: tuple,
                           not_live: list[dict[str, str]]) -> list[dict[str, str]]:
        """Sushi's mappings, live (#210), the file already written: the
        running config records them only once Sushi has taken them. What is
        not live after it (`not_live` with why, or [])."""
        was = [m for m in ((running.get("midi") or {}).get("cc_mappings") or []) if isinstance(m, dict)]
        failure, unknown = await self._midi_cc_live(origin, *cc_plan, was=was)
        if failure is None:
            unrecorded = await asyncio.to_thread(
                self._record_running, path, state, outcome, running_with_map(running, compiled, midi=True))
            return [unrecorded] if unrecorded else []
        not_live = [*not_live, {"code": "midi_live", "detail": failure}]
        if unknown:
            # Sushi may hold a mapping the running config does not say: the
            # next switch restarts instead of trusting it.
            unrecorded = await asyncio.to_thread(
                self._record_running, path, state, outcome, {**running, rigfile.MIDI_UNCONFIRMED: unknown})
            if unrecorded:
                not_live.append(unrecorded)
        return not_live

    def _midi_cc_livable(self) -> bool:
        """Whether this agent's Sushi connection can map a CC live (#210):
        `Live.connect_cc` and its siblings, which a stand-in may lack."""
        return all(callable(getattr(self.live, name, None))
                   for name in ("connect_cc", "disconnect_cc", "cc_connections"))

    async def _midi_cc_live(self, origin, removes: list[dict[str, Any]], adds: list[dict[str, Any]],
                            was: list[dict[str, Any]] | None = None) -> tuple[str | None, str | None]:
        """Take the running Sushi's CC mappings from what the running config
        says (`was`, its cc_mappings) to what the edited file compiles to
        (#210), on one connection under the live lock: each of `removes`
        disconnected, each of `adds` connected, then Sushi's own list read
        back and checked, ranges and mode too (`cc_disagreement`).

        `(None, None)` when it is done. Otherwise `(why, unknown)`: what was
        done is undone as far as Sushi lets it, the call that was in flight
        when it failed (a connect Sushi may have made before it timed out or
        refused to say) is taken back too, best effort, and the list is read
        once more. `unknown` is None when Sushi then holds what `was` says,
        else why its mappings are not known, for the caller to mark the
        running config with (`rigfile.MIDI_UNCONFIRMED`), so the next switch
        restarts rather than trust it. A call that timed out leaves them not
        known whatever the read says: it may still land.

        UNVERIFIED on a real Sushi (live.py's "A pedal's mapping, live"):
        the fake rig cannot show that Sushi 1.3.0 honours these calls. The
        read-back is what keeps a call Sushi accepts and ignores from being
        answered as live."""
        done: list[tuple[str, dict[str, Any]]] = []
        unknown: list[str] = []

        def said(mapping: dict[str, Any]) -> str:
            return f"cc {mapping.get('cc_number')} to {mapping.get('plugin_name')}'s {mapping.get('parameter_name')}"

        async def call(fn, controller, mapping):
            try:
                await self._sushi(origin, fn, controller, mapping, reading=False)
            except web.HTTPGatewayTimeout:
                unknown.append(f"{said(mapping)} timed out, and may still land")
                raise

        try:
            async with self._connected(origin) as controller:
                async with self._live_lock:
                    flight: tuple[str, dict[str, Any]] | None = None
                    try:
                        for mapping in removes:
                            flight = ("removing", mapping)
                            await call(self.live.disconnect_cc, controller, mapping)
                            done.append(("removed", mapping))
                        for mapping in adds:
                            flight = ("adding", mapping)
                            await call(self.live.connect_cc, controller, mapping)
                            done.append(("added", mapping))
                        flight = None
                        held = await self._sushi(origin, self.live.cc_connections, controller, reading=True)
                        wrong = cc_disagreement(held or [], adds, removes)
                        if wrong:
                            raise MidiError("Sushi's own list of its CC mappings does not show the change ("
                                            + "; ".join(wrong) + ")")
                    except BaseException:
                        back = list(reversed(done))
                        if flight is not None:
                            back.insert(0, ("added" if flight[0] == "adding" else "removed", flight[1]))
                        for what, mapping in back:
                            undo = self.live.connect_cc if what == "removed" else self.live.disconnect_cc
                            try:
                                await self._sushi(origin, undo, controller, mapping, reading=False)
                            except Exception as again:  # noqa: BLE001 -- said; the restart puts it right
                                self._midi_say(f"undoing a live CC mapping ({said(mapping)}) failed: {again}")
                        try:
                            held = await self._sushi(origin, self.live.cc_connections, controller, reading=True)
                        except Exception as again:  # noqa: BLE001 -- not known, and said so
                            unknown.append(f"Sushi's list could not be read back ({again})")
                        else:
                            wrong = cc_disagreement(held or [], was or [], [], exact=True)
                            if wrong:
                                unknown.append("after the undo, Sushi's list is not the running config's ("
                                               + "; ".join(wrong) + ")")
                        raise
        except web.HTTPException as exc:
            failure = exc.reason
        except Exception as exc:  # noqa: BLE001 -- the restart is the fallback, said with why
            failure = str(exc) if isinstance(exc, (AgentError, MidiError, LiveError)) else f"{type(exc).__name__}: {exc}"
        else:
            return None, None
        failure = " ".join(f"Sushi did not take the pedal's mapping live ({failure})".split())
        self._midi_say(failure)
        said_unknown = "; ".join(unknown) or None
        if said_unknown:
            self._midi_say(f"Sushi's CC mappings are not known now: {said_unknown}; the next switch restarts")
        return failure, said_unknown

    def _midi_sync_when(self, finished: asyncio.Event, outcome: dict[str, Any]) -> None:
        """Once `finished` is set, under the lifecycle lock, make the listener
        follow the running config as it is then (a write the route stopped
        waiting for), unless `outcome` says it found another run and wrote
        nothing. A task of the MIDI block's, so `midi_release` ends it."""
        async def follow() -> None:
            await finished.wait()
            if outcome.get("stale"):
                return  # a later command has made another run: nothing of this one's to follow
            async with self._lock:
                self.busy = "MIDI"
                try:
                    await self._midi_sync()
                finally:
                    self.busy = None

        task = asyncio.ensure_future(follow())
        self._midi_tasks.add(task)
        task.add_done_callback(self._midi_tasks.discard)

    # ======================================================================= #
    # Switching rigs live (#93): POST /rig/switch, and the Program Change path
    # ======================================================================= #
    #
    # Kept in one block so it merges past other work on this file. Outside it
    # the agent calls in at: __init__ (`switching`), `app` (`_switch_routes`),
    # and `_midi_switch`, which is this path with a Program Change's marks.

    def _switch_routes(self, app) -> None:
        app.router.add_post("/rig/switch", self.switch_route)

    async def switch_route(self, request):
        """`POST /rig/switch {"config_name"}`: switch the running rig to rig
        `config_name` of config/rigs, live where `rigfile.diff` says it can
        be and by restart where not (`_switch`). 200 with `how` ("live" or
        "restart") and, for a restart, `why`; 409 for a refusal that left the
        running rig as it was (`code`: `busy`, `no_rig`, `rig_problem`,
        `plugins_missing`), or a
        restart that failed (with `reason`, and the previous rig brought back
        if it could be); 504 if the agent gave up waiting on a command."""
        try:
            name = switch_request(await self._json_body(request))
        except AgentError as exc:
            raise web.HTTPBadRequest(reason=str(exc))
        try:
            answer = await self._switch(request, name)
        except SwitchRefused as exc:
            raise _conflict(str(exc), exc.code)
        status = 200 if answer["ok"] else (504 if answer.get("abandoned") else 409)
        return web.json_response({**answer, **_dip_answer(request)}, status=status)

    async def _switch(
        self, origin, name: str, *, program: int | None = None,
        mark: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        """Switch the running rig to rig `name`, and answer what happened.

        Under the lifecycle lock throughout (`busy: "switch"`), so no `up`,
        `down` or live change can come between the reading the plan is made
        from and the state file that records the result; live changes asked
        for meanwhile are `409 busy`, as during a restart.

        1. Refused (`SwitchRefused`), with the running rig and its pedals left
           as they were: no rig up, or a next rig that does not compile (what
           the MIDI switch has always checked first).
        2. Decided without Sushi: `rigfile.diff` of the running rig's compiled
           config and the next one's, with what only the state file can say
           (`state_blockers`). A restart reason goes straight to 5.
        3. Applied live (`_switch_live`), on one connection under the live
           lock: a reading first, from which the plan is made again
           (`diff(..., live=reading)`, so a value turned meanwhile is put
           right and a chain changed meanwhile is a restart), then each step
           through the same read-check-set the routes use, then a reading
           checked against the next config as `verify.diff_state` does.
        4. Only then are the compiled config and the state file written, so
           `restart`, `/rig` and the listener follow the next rig; and one
           `op: "rig"` entry is logged, a barrier to undo.
        5. Otherwise a restart into the next rig, `down` then `up --rig`, with
           the running rig brought back if that `up` fails: the MIDI switch's
           path and its fallback. A live switch that failed part-way restarts
           too, into the next rig, so nothing is left half-switched.

        The MIDI listener is stopped once the switch is decided on, and
        follows the rig as it is afterwards, whatever happened.
        """
        self._refuse_while_busy()
        async with self._lock:
            self.busy = "switch"
            try:
                return await self._switch_held(origin, name, program, mark)
            finally:
                self.busy = None
                await self._midi_sync()

    async def _switch_held(self, origin, name, program, mark, discarded: list[dict[str, Any]] | None = None,
                           remove_tracks: tuple[str, ...] = (), amp_on: bool = False) -> dict[str, Any]:
        """The switch into rig `name`, under the lifecycle lock the caller
        holds. `amp_on` (#246): the switch brings the amp, as if the run had
        not been started with --no-amp, for a rig file just given one (an
        amp added, a channel loaded with its rig's amp) into the same rig,
        which `keeps_no_amp` would otherwise keep without."""
        started = self.clock()
        described = self.describe()
        if not described.get("running"):
            raise SwitchRefused(
                f"no rig is running to switch from ({described.get('message')}): POST /rig/up", "no_rig")
        world = self.switching
        state = await asyncio.to_thread(world.read_state) or {}
        if amp_on and state.get("amp_requested") is False:
            # As the switch sees the run only: the `up` it restarts into
            # writes the state file afresh, with the amp asked for.
            state = {**state, "amp_requested": True}
        try:
            upcoming = await asyncio.to_thread(world.compile, name, self._midi_root(), state.get("machine"))
        except Exception as exc:  # noqa: BLE001 -- every failure to compile is a refusal, said
            # `compile_named_rig` raises only `RigError`, but the seam is
            # injectable and a YAML or file error that escaped here was a bare
            # 500 with nothing in /health (found by review): refused, with the
            # running rig left playing, whatever it was.
            why = str(exc) if isinstance(exc, RigError) else f"{type(exc).__name__}: {exc}"
            # One line: a YAML error, and the compile's own list of problems,
            # come with newlines, which an HTTP reason cannot carry (aiohttp
            # raised over it, a 500 again: found testing the real compile).
            reason = " ".join(f"not switched to {name}, which would not come up: {why}".split())
            self._remember(["switch", name], reason, kind="lifecycle")
            raise SwitchRefused(reason, "rig_problem") from exc
        previous = switching_from(state, described)
        # Every plugin the next rig loads must be in plugins/, live or by
        # restart: a live switch would fail at the first it lacks, and a
        # restart would take the rig down for an `up` that refuses it. Asked
        # here, before anything is touched, in the words `up` uses. The amp
        # is the restart's `up` to warn of, as it is for any start.
        root = self._midi_root()
        refusal, _ = await asyncio.to_thread(
            plugins_refusal, upcoming, root, name, amp=False, switch_from=previous or "the rig",
            folder=world.plugins(root))
        if refusal:
            # One line: an HTTP reason cannot carry a newline.
            reason = " ".join(refusal.split())
            self._remember(["switch", name], reason, kind="lifecycle")
            raise SwitchRefused(reason, "plugins_missing")
        try:
            running = await asyncio.to_thread(world.read_config, described.get("config"))
            # As the live attempt will see the amp: none at all for a run
            # with no amp, so neither rig's `_amp` is a step or a reason. A
            # next rig that should bring the amp back is `amp_blockers`'s.
            decided = rigfile.diff(running, upcoming, root=self._amp_root(),
                                   amp_off=state.get("amp_requested") is False,
                                   remove_tracks=self._removable(remove_tracks))
        except Exception as exc:  # noqa: BLE001 -- unreadable is a reason to restart, said
            running = None
            decided = rigfile.NeedsRestart((rigfile.RestartReason(
                "unreadable", f"the running rig's compiled config could not be read: {type(exc).__name__}: {exc}"),))
        # A run with no amp stays without one into the same rig, and only
        # there (`keeps_no_amp`, #246): into another rig with an amp, the amp
        # comes back by a restart, as it does for a Carla that should be
        # running and is not, or is not wired.
        blockers = state_blockers(state) + await self._amp_switch_blockers(
            state, described, running, upcoming, amp_off=keeps_no_amp(state, name))
        if blockers:
            decided = rigfile.NeedsRestart(tuple(blockers) + (
                decided.reasons if isinstance(decided, rigfile.NeedsRestart) else ()))

        # Decided on, so a take recording from the web UI is finished before
        # the rig under it changes, live or by restart (`_finish_take`).
        take = await self._finish_take(origin, "switch")
        await self._midi_stop("stopped while a rig switch runs")
        answer: dict[str, Any] = {"rig": name, "previous": previous}
        if take is not None:
            answer["take"] = take
        if isinstance(decided, rigfile.SwitchPlan):
            tried = await self._switch_live(origin, name, running, upcoming, state, discarded, remove_tracks)
            failure = tried.pop("failed", None)  # a RestartReason, said under `why` below
            answer.update(tried)
            if tried["ok"]:
                entry = self._record("rig", {
                    "rig": name, "how": "live", **({"program": program} if program is not None else {}),
                    "plan": tried["counts"]}, previous)
                if mark:
                    mark(entry)
                # No `up` ran, so nothing new was warned of: the run's own
                # start's warnings are in /health (`agent.warnings`).
                return {**answer, "how": "live", "entry": entry, "warnings": [],
                        "seconds": round(self.clock() - started, 3), "state": self.describe()}
            reasons = [failure]
        else:
            reasons = list(decided.reasons)

        why = [reason.as_dict() for reason in reasons]
        said = "; ".join(f"{r.code}: {r.detail}" for r in reasons)
        print(f"sushi-rig agent: switching to {name} by restart, not live: {said}", file=sys.stderr, flush=True)
        steps = switch_steps(state, name)
        outcome, args = await self._lifecycle_steps(steps, fallback_arguments(state))
        entry = self._record("rig", {
            "rig": name, "how": "restart", **({"program": program} if program is not None else {}),
            "why": [r.code for r in reasons], **({} if outcome.ok else {"ok": False})}, previous)
        if mark:
            mark(entry)
        answer.update({
            "ok": outcome.ok, "how": "restart", "why": why, "command": args, "output": outcome.stdout,
            # What `up` said on stderr as the next rig came up (#246): an amp
            # that did not start or exited, one not wired, a link not made.
            # Kept only from stdout, these were lost, and a Carla that failed
            # came up silent after every switch by restart.
            "warnings": up_warnings(outcome.stderr) if outcome.ok else [],
            "entry": entry, "seconds": round(self.clock() - started, 3), "state": self.describe(),
        })
        if not outcome.ok:
            answer["reason"] = outcome.reason
            answer["abandoned"] = outcome.abandoned
            if answer.get("applied") and args[:1] == ["down"]:
                # Steps were applied live and the restart could not even take
                # the rig down: Sushi holds part of the next rig while the
                # state file names the previous one, a rig that is neither
                # and that `restart` would bring back as the wrong one. Said
                # as that, in /health over `down`'s own failure, and in the
                # answer. Not recovered by guessing: what to do needs a look.
                half = (
                    f"the rig is HALF-SWITCHED: {answer['applied']} of {len(answer.get('plan') or [])} steps "
                    f"of the switch from {previous} to {name} were applied live, then the restart into "
                    f"{name} could not take it down ({outcome.reason}). Sushi holds part of {name} while "
                    f"the state file still names {previous}: read /live, then bring the rig down and up "
                    "by hand"
                )
                print(f"sushi-rig agent: {half}", file=sys.stderr, flush=True)
                self._remember(["switch", name], half, kind="lifecycle")
                answer.update(reason=half, half_switched=True)
        return answer

    async def _amp_switch_blockers(
        self, state: dict[str, Any], described: dict[str, Any], running: dict[str, Any] | None,
        upcoming: dict[str, Any], *, amp_off: bool,
    ) -> list[rigfile.RestartReason]:
        """`amp_blockers` for a switch of the running rig into `upcoming`
        (#246), with the wiring looked at (`_amp_wiring`, only while Carla is
        alive). Not from a rig whose config has no amp, run with the amp
        asked for: `rigfile.diff` restarts for the amp then, as `amp`."""
        if running is None:
            return []
        if not (running.get("_amp") or {}).get("model") and state.get("amp_requested") is not False:
            return []
        return amp_blockers(state, described, upcoming, await self._amp_wiring_decided(described), amp_off=amp_off)

    async def _switch_live(
        self, origin, name: str, running: dict[str, Any], upcoming: dict[str, Any], state: dict[str, Any],
        discarded: list[dict[str, Any]] | None = None, remove_tracks: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        """Apply the plan and verify it; `{"ok", "plan", "applied", "counts"}`,
        and `failed` (a `RestartReason`) when it did not go through. Never
        raises for what Sushi does: every failure is a reason to restart."""
        progress = {"plan": [], "applied": 0, "counts": {}}

        def failed(code: str, detail: str) -> dict[str, Any]:
            done = progress["applied"]
            where = (f" after {done} of {len(progress['plan'])} steps had been applied"
                     if progress["plan"] else "")
            return {**progress, "ok": False, "failed": rigfile.RestartReason(code, detail + where)}

        # The amp as the agent last sent it, which is what the plan compares
        # the next rig's `_amp` with (`rigfile.diff`'s `live["amp"]`). A rig
        # run with no amp (`--no-amp`) has none: no Carla runs, so neither
        # rig's `_amp` is a step or a reason (`amp_off`). A switch that
        # should bring the amp back, into another rig with one (#246), never
        # gets here: the decision restarts for it (`amp_blockers`), as it
        # does for a Carla that should be there and is not. One that dies
        # between the two keeps its steps, so the first fails and the switch
        # restarts into the next rig, Carla with it.
        amp = self._amp_for(state, running)
        amp_off = state.get("amp_requested") is False
        try:
            async with self._connected(origin) as controller:
                async with self._live_lock:
                    before = await self._sushi(origin, self.live.snapshot, controller, reading=True)
                    plan = rigfile.diff(running, upcoming, {**before, "amp": {"parameters": amp.sent()}},
                                        root=self._amp_root(), amp_off=amp_off,
                                        remove_tracks=self._removable(remove_tracks))
                    if isinstance(plan, rigfile.SwitchPlan) and discarded:
                        # Work dropped on purpose (a revert or a load with
                        # discard): what it touched goes back too, where
                        # the diff alone would leave it (#127, review).
                        plan = discard_plan(plan, discarded, upcoming, before)
                    if isinstance(plan, rigfile.NeedsRestart):
                        reason = plan.reasons[0]
                        detail = "; ".join(f"{r.code}: {r.detail}" for r in plan.reasons)
                        return {**progress, "ok": False, "failed": rigfile.RestartReason(reason.code, detail)}
                    progress["plan"] = [step.as_dict() for step in plan.steps]
                    progress["counts"] = plan.counts()
                    # One dip of the master (#137, `_dip`) around every step
                    # that changes the graph, which a plan puts together
                    # (`ramp.switch_span`): from the first to the last, the
                    # bypass after each add inside it. A step that fails
                    # leaves the dip with the level put back, before the
                    # restart the failure leads to.
                    span = ramp.switch_span([step.op for step in plan.steps])
                    async with contextlib.AsyncExitStack() as dipped:
                        for index, step in enumerate(plan.steps):
                            if span and index == span[0]:
                                await dipped.enter_async_context(
                                    self._dip(origin, controller, (before or {}).get("tracks")))
                            await self._switch_step(origin, controller, step)
                            progress["applied"] += 1
                            if span and index == span[1]:
                                await dipped.aclose()
                    after = await self._sushi(origin, self.live.snapshot, controller, reading=True)
            # What the next config states, and everything the plan set beyond
            # that (strip and send defaults, un-bypasses): each against what
            # was meant, so an acknowledged set Sushi did not apply is caught.
            problems = [str(p) for p in diff_state(upcoming, after)] + rigfile.unapplied(plan, after)
        except asyncio.CancelledError:
            raise
        except web.HTTPException as exc:
            return failed("failed", f"Sushi: {exc.reason}")
        except Exception as exc:  # noqa: BLE001 -- AgentError and the rest: a restart, said
            return failed("failed", f"{type(exc).__name__}: {exc}")
        if problems:
            shown = "; ".join(problems[:5]) + (f" (and {len(problems) - 5} more)" if len(problems) > 5 else "")
            return {**failed("unverified", f"the rig read back is not the next rig: {shown}"),
                    "verification": problems}
        try:
            path = await asyncio.to_thread(self.switching.write_config, name, upcoming)
            now = await asyncio.to_thread(self.switching.read_state) or state
            after_state = switched_state(now, name, path)
            await asyncio.to_thread(self.switching.write_state, after_state)
        except Exception as exc:  # noqa: BLE001 -- a rig the state file does not name would restart as the old one
            return failed("failed", f"the rig switched, but recording it failed: {type(exc).__name__}: {exc}")
        # The same Carla, sent the next rig's values by the steps: what the
        # agent sent carries over to the next rig's run rather than being
        # taken from its config as if `up` had replayed it.
        if self._amp is amp:
            amp.run = amp_live.run_of(after_state)
        return {**progress, "ok": True}

    def _removable(self, tracks: tuple[str, ...]) -> tuple[str, ...]:
        """The tracks of `tracks` a live switch may take out (#190): all of
        them where this agent's Sushi link can remove a track
        (`Live.remove_track`), none where it cannot, so the plan restarts
        rather than fail at the step."""
        return tuple(tracks) if getattr(self.live, "remove_track", None) is not None else ()

    async def _switch_step(self, origin, controller, step: rigfile.SwitchStep) -> None:
        """One step of a plan, through what the route for it uses: the same
        reads and checks (`check_remove`, `_apply_move`, ...) and each chain
        edit confirmed by reading the chain back. `AgentError` (or an HTTP
        error from `_sushi`) when the rig is not as the plan expects."""
        args = step.args
        if step.op == "parameter":
            await self._apply_parameter(origin, controller, args["processor"], args["parameter"], args["value"])
        elif step.op == "bypass":
            await self._apply_bypass(origin, controller, args["processor"], args["bypassed"])
        elif step.op == "move":
            # Dipped with the switch's other structural steps, not on its own.
            planned, moved = await self._apply_move(origin, controller, args["processor"], args["direction"],
                                                    dip=False)
            if moved is None:
                raise Unconfirmed(f"{args['processor']!r} was already at that end of {planned.chain}, "
                                  "so the chain is not the one the plan was made from")
        elif step.op == "remove":
            reading = await self._sushi(origin, self.live.removal, controller, args["processor"], reading=True)
            removal = check_remove(reading, args["processor"])
            await self._sushi(origin, self.live.remove, controller, removal, reading=False)
            after = await self._sushi(origin, self.live.walk, controller, reading=False)
            confirm_chain((after or {}).get("chains") or {}, removal.track, removal.expected,
                          f"removed {removal.processor!r} from {removal.track!r}")
        elif step.op == "remove_track":
            # A channel removed from the running rig (#190): the track, its
            # chain and its send, then a read-back that it is gone.
            track = args["track"]
            reading = await self._sushi(origin, self.live.walk, controller, reading=True)
            if track not in ((reading or {}).get("chains") or {}):
                raise Unconfirmed(f"there is no track {track!r} to remove, so the rig is not the one the plan "
                                  "was made from")
            try:
                await self._sushi(origin, self.live.remove_track, controller, track, reading=False)
            finally:
                self._load_stale()  # as an add or a remove through the routes (#137)
            after = await self._sushi(origin, self.live.walk, controller, reading=False)
            if track in ((after or {}).get("chains") or {}):
                raise Unconfirmed(f"track {track!r} was removed and reads back still there")
        elif step.op == "amp":
            # The call POST /amp/parameter makes, with its checks: Carla must
            # answer, or the step fails and the switch restarts.
            try:
                await self._amp_set(origin, args["name"], args["value"])
            except amp_live.AmpRefused as exc:
                raise Unconfirmed(f"the amp's {args['name']} was not set: {exc}") from exc
        elif step.op == "add":
            reading = await self._sushi(origin, self.live.walk, controller, True, reading=True)
            addition = place_switch_add(reading, args["track"], args["name"], args["uri"], args["before"],
                                        args.get("copy"))
            try:
                await self._sushi(origin, self.live.add, controller, addition, reading=False)
            finally:
                self._load_stale()  # as an add through the route (#137)
            after = await self._sushi(origin, self.live.walk, controller, reading=False)
            confirm_chain((after or {}).get("chains") or {}, addition.track, addition.expected,
                          f"added {addition.name!r} to {addition.track!r}")
        else:
            raise AgentError(f"a switch step of op {step.op!r} is not one the agent makes")

    # ======================================================================= #
    # (end of switching rigs live)
    # ======================================================================= #
    # ======================================================================= #
    # The amp: GET /amp, GET /amp/models, POST /amp/parameter, POST /amp/model
    # ======================================================================= #
    #
    # Kept in one block so it merges past other work on this file. Outside it
    # the agent calls in at: __init__ (`_amp_init`), `app` (`_amp_routes`),
    # `live_undo` (`_undo_amp`) and the live switch (`_amp_for`, `_amp_set`).
    # The decisions are amp_live.py's, and its docstring says what the agent
    # can know of Carla and what it cannot.
    #
    # Locks: a parameter takes the amp's own lock (`_amp_lock`), so the value
    # each change records as `previous` is the one it replaced; a model takes
    # the lifecycle lock first (`busy: "amp_model"`), since it stops and starts
    # a child of the rig, then the amp's. A switch holds the lifecycle and live
    # locks and takes the amp's for each amp step. Always in that order.

    def _amp_init(self, carla: "amp_live.Carla | None") -> None:
        self.carla = carla or amp_live.Carla.at()
        self._carla_run = None  # the Carla (its pid) the UDP client was used with
        self._carla_probe: tuple | None = None  # (run, clock) of the last probe that answered
        self._amp: amp_live.AmpState | None = None  # the running rig's, seeded per run
        self._amp_lock = asyncio.Lock()
        self._amp_shelf = amp_live.ModelShelf()
        # GET /amp's model view for one (run, model): polled every second by
        # every page, it is read once per run and model, not per poll.
        self._amp_model_view: tuple[tuple, Any] | None = None
        # The last look at the amp's links (#246, `_amp_wiring`): (the run
        # and Carla's pid, clock, `amp_live.wiring_said`'s answer).
        self._amp_wiring_seen: tuple[tuple, float, dict[str, Any]] | None = None
        # The look in flight, shared by every caller of its Carla run: (that
        # key, its task).
        self._amp_wiring_look: tuple[tuple, "asyncio.Future"] | None = None
        # What the last start's `up` warned of, and the run it is of
        # (`_warned_run`): /health's `agent.warnings` while that run is up.
        self._start_warnings: tuple[tuple, list[str]] | None = None
        # Whether the running rig's file has no `amp:` (#246, `_amp_addable`),
        # by (its path, size and mtime): GET /amp's offer, polled each second.
        self._amp_addable_seen: tuple[tuple, bool] | None = None

    def _amp_routes(self, app) -> None:
        app.router.add_get("/amp", self.amp_route)
        app.router.add_get("/amp/models", self.amp_models_route)
        app.router.add_post("/amp/parameter", self.amp_parameter_route)
        app.router.add_post("/amp/model", self.amp_model_route)
        app.router.add_post("/amp/add", self.amp_add_route)

    def _amp_root(self) -> Path:
        from .paths import checkout_root

        return self.root or checkout_root()

    @staticmethod
    def _amp_http(exc: "amp_live.AmpRefused", hint: str = ""):
        """The HTTP error for a refusal, carrying its `code`."""
        kind = {400: web.HTTPBadRequest, 409: web.HTTPConflict, 500: web.HTTPInternalServerError,
                502: web.HTTPBadGateway}.get(exc.status, web.HTTPConflict)
        error = kind(reason=" ".join((str(exc) + hint).split()))
        if exc.code:
            error.agent_code = exc.code
        return error

    def _amp_for(self, state: dict[str, Any], config: dict[str, Any] | None) -> "amp_live.AmpState":
        """The amp state of the run `state` describes: the one held, or a new
        seed from `config` (the running rig's compiled config) when the run
        is another, since a restart or a switch by restart replayed a config."""
        if self._amp is None or self._amp.run != amp_live.run_of(state):
            self._amp = amp_live.seed(state, config)
        return self._amp

    def _read_running_config(self, state: dict[str, Any]) -> dict[str, Any] | None:
        try:
            return self.switching.read_config(state.get("config"))
        except Exception:  # noqa: BLE001 -- no config to seed from: every value is then unknown, and said so
            return None

    async def _amp_now(self, *, writing: bool) -> tuple["amp_live.AmpState", dict[str, Any], dict[str, Any]]:
        """`(amp, state, described)` for the running rig, or `AmpRefused`."""
        described = self.describe()
        state = await asyncio.to_thread(self.switching.read_state) or {}
        problem = amp_live.amp_problem(state, described, writing=writing)
        if problem is not None:
            raise problem
        if self._amp is None or self._amp.run != amp_live.run_of(state):
            config = await asyncio.to_thread(self._read_running_config, state)
            self._amp_for(state, config)
        return self._amp, state, described

    async def _amp_send(
        self, origin, amp: "amp_live.AmpState", name: str, value: float, described: dict[str, Any],
    ) -> None:
        """Send one parameter to Carla, and only once it is sent, record it.

        Carla answering is a TCP connection to its OSC port, made when
        `amp_live.needs_probe` says (the first send to this Carla run, after
        a failure, at most every `PROBE_EVERY` otherwise; see amp_live's
        docstring): refused, it is 502 `carla_not_answering` and nothing is
        sent or recorded. A send that raises is 502 too, and a python-osc that
        is not installed 500; each is remembered for /health. A new Carla run
        (its pid, from `described`) gets a new UDP client."""
        from .amp import CARLA_OSC_PORT, AmpError, parameter_message

        address, payload = parameter_message(name, value)
        where = [origin.method, origin.path]
        run = ((described.get("children") or {}).get("amp") or {}).get("pid")
        if run != self._carla_run:
            await asyncio.to_thread(self.carla.forget)
            self._carla_run, self._carla_probe = run, None
        if amp_live.needs_probe(self._carla_probe, run, self.clock()):
            if not await asyncio.to_thread(self.carla.answering):
                self._carla_probe = None
                reason = (f"Carla is not answering on its OSC port ({CARLA_OSC_PORT}), so {name} was not sent "
                          "and the amp is where it was")
                self._remember(where, reason, kind="live")
                raise amp_live.AmpRefused(reason, 502, "carla_not_answering")
            self._carla_probe = (run, self.clock())
        try:
            await asyncio.to_thread(self.carla.send, address, payload)
        except AmpError as exc:
            self._remember(where, str(exc), kind="live")
            raise amp_live.AmpRefused(str(exc), 500) from exc
        except OSError as exc:
            self._carla_probe = None
            await asyncio.to_thread(self.carla.forget)
            reason = f"sending {name} to Carla failed: {type(exc).__name__}: {exc}"
            self._remember(where, reason, kind="live")
            raise amp_live.AmpRefused(reason, 502, "carla_not_answering") from exc
        amp.values[name] = payload[1]
        amp.sources[name] = amp_live.SENT

    async def _amp_set(self, origin, name: str, value: float) -> tuple[float | None, float]:
        """`(previous, sent)`: one parameter set, under the amp's lock, with
        the checks a write makes. What a route, an undo and a switch's step
        share."""
        async with self._amp_lock:
            amp, _, described = await self._amp_now(writing=True)
            previous = amp.values.get(name)
            await self._amp_send(origin, amp, name, value, described)
            return previous, amp.values[name]

    async def amp_route(self, request):
        """The amp as the agent last sent it (amp_live.amp_view): the model,
        each parameter with its range, unit and whether the panel puts it on
        a fader, and `known`. 409 `no_rig` or `no_amp`, and `busy` while a
        lifecycle command runs, as /live is."""
        self._refuse_while_busy()
        try:
            amp, state, described = await self._amp_now(writing=False)
        except amp_live.AmpRefused as exc:
            error = self._amp_http(exc)
            if exc.code == "no_amp":
                error.agent_extra = await self._amp_on_offer()
            raise error
        key = (amp.run, amp.model)
        if self._amp_model_view is None or self._amp_model_view[0] != key:
            model = await asyncio.to_thread(amp_live.model_view, amp.model, self._amp_root(), self._amp_shelf)
            self._amp_model_view = (key, model)
        view = amp_live.amp_view(amp, state, self._amp_model_view[1])
        view["carla"] = (described.get("children") or {}).get("amp")
        view["held"] = self._amp_held()
        view["wiring"] = await self._amp_wiring(described)
        return web.json_response(view)

    async def _amp_on_offer(self) -> dict[str, Any]:
        """For GET /amp's 409 `no_amp` (#246): `amp_on` true when the run was
        started with --no-amp and the running rig's config has an amp, which
        `POST /rig/restart {"amp": true}` brings; `add_amp` true when the
        running rig's file has no `amp:` at all, which `POST /amp/add` gives
        it; `rig` names the rig either is for. A run started with --no-amp
        kept it through every restart and switch, and a rig with no `amp:`
        could get one only by hand, with nothing on the page for either."""
        state = await asyncio.to_thread(self.switching.read_state) or {}
        # `add_amp` (#246's cause 1): the running rig's own file has no
        # `amp:`, which is another case than a run with no amp, and which
        # neither Amp on nor a restart can mend: a rig made on the New rig
        # screen, or given its channels by loads. `POST /amp/add` writes
        # one. Only for a rig of channels the agent can write (`save_refusal`).
        add = await asyncio.to_thread(self._amp_addable, state, self.describe())
        if state.get("amp_requested") is not False:
            has = False
        else:
            config = await asyncio.to_thread(self._read_running_config, state)
            has = bool(((config or {}).get("_amp") or {}).get("model"))
        rig = switching_from(state, self.describe()) if has or add else None
        return {"amp_on": has, "add_amp": add, "rig": rig}

    def _amp_addable(self, state: dict[str, Any], described: dict[str, Any]) -> bool:
        """Whether the running rig's file has no `amp:` and is one `POST
        /amp/add` would write: a rig of channels, run headless. A file that
        will not read is not offered one. Off the loop.

        Every page polls GET /amp each second, so the file's answer is kept
        by its path, size and `st_mtime_ns` (`_amp_addable_seen`), and an
        unchanged file is not read and parsed again (#248's review). Whether
        the run is one the agent can write (`save_refusal`, over the state
        file and `described`) is asked each time: it changes without the
        file changing."""
        from . import rigedit

        if save_refusal(state, described) is not None:
            return False
        path = rig_file_of(state, self._midi_root())
        try:
            stat = path.stat()
        except OSError:
            return False
        key = (str(path), stat.st_size, stat.st_mtime_ns)
        seen = self._amp_addable_seen
        if seen is not None and seen[0] == key:
            return seen[1]
        try:
            import yaml

            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 -- not offered: the add would refuse it, and say why
            raw = None
        addable = isinstance(raw, dict) and not rigedit.has_amp(raw)
        self._amp_addable_seen = (key, addable)
        return addable

    async def _amp_wiring(self, described: dict[str, Any], *, fresh: bool = False,
                          wait: bool = True) -> dict[str, Any] | None:
        """Whether the running rig's amp is wired (#246): `{"wired",
        "problems"}` from Carla's `links` (`amp_live.read_links`), or None
        when there is no live Carla to ask about, or no way to tell. One look
        per Carla run (its pid) and `amp_live.WIRING_EVERY`, off the loop,
        shared by GET /amp, /health and a switch's decision.

        After an amp starts, by `up`, a restart or a model change, Carla can
        run with the link from the guitar into NAM, or from NAM into Sushi,
        not made: the guitar goes in dry while everything looks healthy, and
        `up` warned of it once.

        One look at a time (#246's review): every page polls /health and
        GET /amp each second, and once a look is old each poll started its
        own `pw-dump` and `pw-link -l` and waited on them. A look in flight
        is shared (`_amp_wiring_look`), shielded so that a caller that goes
        away does not cancel it for the rest. `fresh` does not take the kept
        look, for a decision that would restart on it; `wait` False (/health)
        answers the last look of this Carla run, if there is one, and lets a
        new one run behind it."""
        child = (described.get("children") or {}).get("amp") if described.get("running") else None
        if not child or child.get("alive") is not True:
            return None
        key = (described.get("pgid"), described.get("started_at"), described.get("config"), child.get("pid"))
        seen = self._amp_wiring_seen
        kept = seen if seen is not None and seen[0] == key else None
        if kept is not None and not fresh and self.clock() - kept[1] < amp_live.WIRING_EVERY:
            return kept[2]
        look = self._amp_wiring_look
        if look is None or look[0] != key or look[1].done():
            look = (key, asyncio.ensure_future(self._amp_wiring_once(key)))
            self._amp_wiring_look = look
        if not wait and kept is not None:
            return kept[2]
        return await asyncio.shield(look[1])

    async def _amp_wiring_once(self, key: tuple) -> dict[str, Any] | None:
        """One look at the amp's links for the Carla run `key`, kept for the
        next callers (`_amp_wiring_seen`). Never raises."""
        try:
            said = await asyncio.to_thread(self.carla.links)
        except Exception as exc:  # noqa: BLE001 -- a look that fails is "cannot tell", said
            said = amp_live.wiring_said(None, checked=False,
                                        why=f"the amp's links could not be looked at: {type(exc).__name__}: {exc}")
        if said is not None:
            self._amp_wiring_seen = (key, self.clock(), said)
        return said

    async def _amp_wiring_decided(self, described: dict[str, Any]) -> dict[str, Any] | None:
        """The wiring a restart is decided on (#246's review): the kept look,
        unless it says not wired, which is looked at again first. A look
        taken in the seconds after a start, before qpwgraph made the links,
        would otherwise make a live switch a restart for ten seconds."""
        wiring = await self._amp_wiring(described)
        if wiring is not None and wiring.get("wired") is False:
            wiring = await self._amp_wiring(described, fresh=True)
        return wiring

    def _amp_restarted_warnings(self, problems: list[str]) -> None:
        """The start's warnings after Carla was restarted alone and plays
        (`Supervisor.restart_amp`): the amp's own dropped (`amp_warning`),
        since they were of the Carla that is gone, and what the restart said
        of its links (`problems`) in their place. The run is the same, so
        nothing else would drop them."""
        if self._start_warnings is None:
            return
        run, warnings = self._start_warnings
        self._start_warnings = (run, [w for w in warnings if not amp_warning(w)] + problems)

    def _run_warnings(self, described: dict[str, Any]) -> list[str]:
        """What `up` warned of as the rig that is up now came up, or [] when
        the warnings kept are another run's (#246)."""
        if self._start_warnings is None or not described.get("running"):
            return []
        run, warnings = self._start_warnings
        return list(warnings) if run == _warned_run(described) else []

    def _amp_held(self) -> bool:
        """Whether this agent holds the running rig, which a model change needs."""
        held = getattr(self.supervisor, "held", None)
        return held is not None and held.ready and not held.stopping

    async def amp_models_route(self, request):
        """amp/models, for the picker (amp_live.library): each model by file
        name, its rate, and whether it can be played here. Needs no rig."""
        state = await asyncio.to_thread(self.switching.read_state) or {}
        current = state.get("amp_model") if self.describe().get("running") else None
        models = await asyncio.to_thread(amp_live.library, self._amp_root(), self._amp_shelf, current)
        from .amp import RIG_SAMPLE_RATE

        return web.json_response({"ok": True, "rate": RIG_SAMPLE_RATE, "models": models})

    async def amp_parameter_route(self, request):
        """`POST /amp/parameter {"name", "value"}`: send one parameter to
        Carla and log it (op "amp", `previous` what was last sent, None if
        nothing was). A value outside the range is clamped, and the answer
        says so (`clamped`). 400 for a name the amp lacks or a value that is
        not a number; 409 `busy`, `no_rig`, `no_amp`, `panel`; 502
        `carla_down` or `carla_not_answering`, with nothing recorded."""
        try:
            name, asked, sent = amp_live.parameter_request(await self._json_body(request))
        except amp_live.AmpRefused as exc:
            raise self._amp_http(exc)
        self._refuse_while_busy()
        try:
            previous, sent = await self._amp_set(request, name, sent)
        except amp_live.AmpRefused as exc:
            raise self._amp_http(exc)
        entry = self._record("amp", {"name": name, "value": sent}, previous)
        return web.json_response({"ok": True, **entry, "asked": asked,
                                  "clamped": amp_live.clamped_said(name, asked, sent)})

    async def amp_model_route(self, request):
        """`POST /amp/model {"model"}`: play another model, by restarting
        Carla alone (`Supervisor.restart_amp`). **The sound stops** while
        Carla stops and starts; the answer and stderr say so. Refused before
        anything stops: 400 for a model that is not a name in amp/models (a
        path, from this machine only: `MODEL_NAME`), cannot be read, or was
        trained at another rate (`wrong_rate`); 409 `busy`, `no_rig`,
        `no_amp`, `panel`, and `not_held` for a rig this agent did not start;
        502 `carla_down`. A restart that fails is 502 with `restored`: whether
        the previous model was brought back. The state file records the
        model as `--amp-model` would, so `restart` keeps it (and a live
        switch then restarts: `state_blockers`). Logged as op "amp_model"."""
        root = self._amp_root()
        try:
            override, path = amp_live.model_request(
                await self._json_body(request), local=is_local(request.remote), root=root,
                name_pattern=MODEL_NAME)
        except amp_live.AmpRefused as exc:
            raise self._amp_http(exc)
        described_model = await asyncio.to_thread(self._amp_shelf.read, path)
        if "error" in described_model:
            raise self._amp_http(amp_live.AmpRefused(described_model["error"], 400, "unreadable"))
        wrong = amp_live.rate_problem(described_model)
        if wrong:
            raise self._amp_http(amp_live.AmpRefused(wrong, 400, "wrong_rate"))
        answer = await self._change_model(request, override, path)
        return web.json_response(answer, status=200 if answer["ok"] else 502)

    async def amp_add_route(self, request):
        """`POST /amp/add {"model", "rig"?, "discard"?}` (#246's cause 1):
        give the running rig, whose file has no `amp:`, an amp, and play it.

        The rig file is written first, in place with `write_rig` (comments
        kept), with `amp:` above its channels: NAM, `model` (a file in
        amp/models, as POST /amp/model takes one) and every parameter at the
        amp's default (`rigedit.new_amp`). The running rig is then switched
        into the file, as a channel load is (`_revert_switch`): always by
        restart, since Carla comes with the rig (`rigfile.diff`'s `amp`), and
        with the amp even for a run started with --no-amp (`amp_on`). The
        file as it was is kept for Revert…, and the one Undo puts it back and
        switches the rig into it, by restart again.

        Refused before anything is written: 400 for a body of the wrong shape
        or a model that is not one (`model_request`), cannot be read
        (`unreadable`) or was trained at another rate (`wrong_rate`); 409
        `busy`, `no_rig`, `panel`, `v1` (as a save); `changed` when `rig` is
        not the running rig; `has_amp` when the file has an `amp:` already
        (a model is changed by POST /amp/model); `rig_problem`,
        `plugins_missing`; and `unsaved` while the rig holds live work no
        save has written, unless `"discard": true`, since the restart drops it.

        200 with `file`, `amp` (what was written), `how` and `why` (the
        switch's), `warnings` (what the restart's `up` warned of: an amp that
        did not start is said there), `kept_before` and `library`. 409
        `not_switched` (or 504) when the file was written and the rig could
        not be made to play it, `written: true`.

        Not run against a live Sushi or Carla: shown against the fakes of the
        tests."""
        from . import rigedit

        try:
            asked = rigedit.amp_request(await self._json_body(request))
        except rigedit.EditRequestError as exc:
            raise web.HTTPBadRequest(reason=str(exc))
        try:
            override, model_path = amp_live.model_request(
                {"model": asked["model"]}, local=is_local(request.remote), root=self._amp_root(),
                name_pattern=MODEL_NAME)
        except amp_live.AmpRefused as exc:
            raise self._amp_http(exc)
        described_model = await asyncio.to_thread(self._amp_shelf.read, model_path)
        if "error" in described_model:
            raise self._amp_http(amp_live.AmpRefused(described_model["error"], 400, "unreadable"))
        wrong = amp_live.rate_problem(described_model)
        if wrong:
            raise self._amp_http(amp_live.AmpRefused(wrong, 400, "wrong_rate"))
        amp = rigedit.new_amp(amp_live.stored_model(override))
        self._refuse_while_busy()
        answer: dict[str, Any]
        path: Path | None = None
        async with self._lock:
            self.busy = "amp add"
            try:
                run = await self._rigfile_run()
                refusal = save_refusal(run["state"], run["described"])
                if refusal is not None:
                    raise _conflict(*refusal)
                path = run["running"]
                if asked["rig"] is not None and path.stem != asked["rig"]:
                    raise _conflict(f"the running rig is {path.stem}, not {asked['rig']}, so no amp was added: "
                                    "look at the rig and add it again", "changed")
                try:
                    planned = await self._rigfile_thread(request, path, lambda: self._plan_amp_add(path, amp, run))
                    async with self._live_lock:
                        refusal = load_refusal(run["state"], self.log, doing="adding the amp", again="add it")
                        if refusal is not None and not asked["discard"]:
                            raise RigFileRefused(*refusal)
                        discarded = (unsaved_entries(self.log, since=run["state"].get("started_at"))
                                     if refusal is not None else [])
                        kept = await self._rigfile_thread(request, path, lambda: self._write_planned(
                            path, planned, f"before the amp was added ({Path(amp['model']).stem})", "the amp"))
                except RigFileRefused as exc:
                    error = _conflict(" ".join(str(exc).split()), exc.code)
                    if exc.extra:
                        error.agent_extra = exc.extra
                    raise error
                self._history_file(path, planned["before"], planned["after"], f"add amp {Path(amp['model']).stem}",
                                   run=run, apply="switch", act="add_amp")
                answer = {"ok": True, "rig": path.stem, "file": self._save_shown(path), "amp": amp,
                          "written": True, "discarded": bool(discarded), "kept_before": kept}
                said = {"id": "add_amp", "what": f"with an amp ({Path(amp['model']).stem})"}

                def mark(entry: dict[str, Any]) -> None:
                    entry["args"]["amp_added"] = amp["model"]

                answer.update(await self._revert_switch(
                    request, path.stem, said, mark=mark, done="given an amp", discarded=discarded,
                    command=["amp add", f"{Path(amp['model']).stem} onto {path.stem}"], amp_on=True))
                if discarded:
                    answer["discard"] = discard_said(discarded, answer)
            finally:
                self.busy = None
                if path is not None:
                    await self._midi_sync()  # the switch stopped the listener; it follows the rig now
        await self._rigfile_git_after(answer, path)
        if answer["ok"]:
            return web.json_response(answer)
        return web.json_response(answer, status=504 if answer.get("abandoned") else 409)

    def _plan_amp_add(self, path: Path, amp: dict[str, Any], run: dict[str, Any]) -> dict[str, Any]:
        """The running rig's file with `amp` added (`rigedit.with_amp`),
        compiled before anything is written, or the refusal: `has_amp` for a
        file with an `amp:` already, and the compiler's. Off the loop."""
        import yaml

        from . import rigedit

        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise RigFileRefused(f"{path.name} is not a rig file", "rig_problem")
        if rigedit.has_amp(raw):
            raise RigFileRefused(f"nothing was written: {path.stem} has an amp already (its file's amp:): "
                                 "change its model on the amp's card instead", "has_amp")
        edited = rigedit.with_amp(raw, amp)
        compiled = self._rigfile_compile(edited, run)
        self._rigfile_plugins(compiled, path.stem, run, "given an amp", "add it again")
        return {"raw": edited, "compiled": compiled}

    def _write_planned(self, path: Path, planned: dict[str, Any], how: str, what: str) -> dict[str, Any]:
        """Write `planned["raw"]` over rig file `path` in place (`write_rig`,
        comments kept), and keep the file as it was for Revert… once it is
        written (`_keep_for_revert`), as a load does; the bytes before and
        after go in `planned`, for the one Undo (#209). An amp added's and a
        channel removal's (`_write_removal`) one path; a load's is its own
        (`_write_load`: `replace_chains` and the provenance line). Off the
        loop, under the live lock."""
        from . import saves

        root = self._midi_root()
        rig = path.stem
        before = path.read_bytes()
        kept, _ = saves.snapshots(root, rig)
        kept_as = saves.kept_as(before, kept, lambda sid: saves.snapshot_bytes(root, rig, sid))
        try:
            rigfile.write_rig(path, planned["raw"])
        except rigfile.RigFileError as exc:
            raise RigFileRefused("nothing was written: " + "; ".join(exc.problems), "rig_problem") from exc
        planned["before"], planned["after"] = before, path.read_bytes()
        return self._keep_for_revert(path, before, kept_as, how, what)

    async def _change_model(self, origin, override: str, path: Path, *, undoing: tuple | None = None,
                            skip: bool = False) -> dict[str, Any]:
        """Change the model under the lifecycle lock and the amp's, and log
        it: the route's, and an undo's (`undoing`, `(index, entry)`: checked
        stale first, logged as its undo; with `skip`, only checked)."""
        from .amp import carla_project, read_amp

        self._refuse_while_busy()
        async with self._lock:
            self.busy = "amp_model"
            try:
                async with self._amp_lock:
                    try:
                        amp, state, _ = await self._amp_now(writing=True)
                    except amp_live.AmpRefused as exc:
                        if undoing is not None and skip and exc.code in ("no_amp", "panel"):
                            return self._amp_skip(*undoing, exc)
                        raise self._amp_http(exc)
                    root = self._amp_root()
                    if undoing is not None:
                        picked = self._pick_undo()  # the log may have moved on while this waited
                        if picked is None or picked[0] != undoing[0]:
                            raise _conflict("the newest change changed while this undo waited: undo again",
                                            "changed")
                        refused = None
                        try:
                            amp_live.undo_check(undoing[1], amp, root)
                        except amp_live.AmpRefused as exc:
                            refused = exc
                        if skip:
                            if refused is None:
                                raise _conflict("the newest change can be undone as it stands: undo it, "
                                                "a skip is only for one that cannot be", "not_stale")
                            return self._amp_skip(*undoing, refused)
                        if refused is not None:
                            raise self._amp_http(refused, '; to give up on this entry and go on to the one '
                                                          'before it, send {"skip": true}')
                    stored = amp_live.stored_model(override)
                    if amp_live.same_model(amp.model, stored, root):
                        return {"ok": True, "changed": False, "model": stored,
                                "message": f"the amp already plays {Path(stored).name}: nothing was restarted"}
                    if self.supervisor is None or not self._amp_held():
                        raise self._amp_http(amp_live.AmpRefused(
                            "this agent did not start the running rig (it was started with 'up --direct', or "
                            "by an agent before this one), so it cannot restart its Carla alone: restart the "
                            "rig through the agent first", 409, "not_held"))
                    # The rig's own config names its own model; choosing that
                    # one again is no override, so `restart` and a switch
                    # treat the rig as their own again.
                    config = await asyncio.to_thread(self._read_running_config, state)
                    own = (read_amp(config or {}) or {}).get("model")
                    recorded = None if amp_live.same_model(own, stored, root) else override
                    # The take's config names the model it was recorded
                    # through, so a take running is finished first.
                    take = await self._finish_take(origin, "amp_model")
                    started = self.clock()
                    print(f"sushi-rig agent: changing the amp's model to {path.name}: THE SOUND STOPS while "
                          "Carla restarts", file=sys.stderr, flush=True)
                    result = await self.supervisor.restart_amp(
                        carla_project(path), dict(amp.values), model=stored, override=recorded)
                    seconds = round(self.clock() - started, 3)
                    if result.ok or result.restored:
                        # Carla plays again, the new model or the old: what
                        # the start warned of the amp is no longer so (#246's
                        # review), and the run's `started_at` is unchanged
                        # (`rig.amp_restarted_state`), so it would stay.
                        self._amp_restarted_warnings(list(result.problems))
                    if not result.ok:
                        if result.restored is None and result.pid is None:
                            # Refused before Carla was touched, each with its own code
                            # (`AmpRestart.code`): nothing changed, nothing to remember.
                            raise self._amp_http(amp_live.AmpRefused(result.reason, 409, result.code))
                        said = (f"the amp's model was not changed to {path.name}: {result.reason}"
                                + ("; the previous model was brought back" if result.restored else ""))
                        print(f"sushi-rig agent: {said}", file=sys.stderr, flush=True)
                        self._remember([origin.method, origin.path], said, kind="lifecycle")
                        return {"ok": False, "status": 502, "code": result.code, "reason": said,
                                "restored": result.restored, "problems": list(result.problems),
                                "seconds": seconds, **({"take": take} if take is not None else {})}
                    previous = amp.model
                    amp.model = stored
                    if undoing is None:
                        entry = self._record("amp_model", {"model": stored}, previous)
                    else:
                        entry = self._record("undo", {"of": "amp_model", "model": stored}, previous,
                                             undoes=undoing[0])
                    return {"ok": True, "changed": True, **entry, "seconds": seconds, "pid": result.pid,
                            "problems": list(result.problems), **({"take": take} if take is not None else {}),
                            "message": f"the amp now plays {path.name}. The sound stopped for {seconds:g} s "
                                       "while Carla restarted; the rest of the rig played on"}
            finally:
                self.busy = None

    def _amp_skip(self, index: int, undone: dict[str, Any], exc: Exception) -> dict[str, Any]:
        entry = self._record("stale", {"of": undone["op"], "reason": str(exc)}, None, stale=index)
        return {"ok": True, **entry, "skipped": undone}

    async def _undo_amp(self, request, op: str, skip: bool, expect: int | None = None):
        """Undo the newest change, an amp one (`pick_undo` found it): a
        parameter goes back to what was last sent before it, through the
        route's own call; a model goes back by restarting Carla on the one
        before. Stale (409) unless the amp is still as the change left it, by
        what was last sent: amp_live.undo_check. The rest as `live_undo`."""
        if op == "amp_model":
            index, undone = self._pick_undo()
            self._expect_pick((index, undone), expect)
            from .amp import AmpError, resolve_model

            previous = undone.get("previous")
            root = self._amp_root()
            try:
                if not previous:
                    raise AmpError("the model before it is not known")
                path = resolve_model(previous, root)
            except AmpError as exc:
                refused = amp_live.AmpRefused(f"cannot undo the model change: {str(exc).splitlines()[0]}",
                                              409, "stale")
                if skip:
                    return web.json_response(self._amp_skip(index, undone, refused))
                raise self._amp_http(refused)
            override = amp_live.override_of(previous, root)
            answer = await self._change_model(request, override, path, undoing=(index, undone), skip=skip)
            if answer.get("ok") and answer.get("op") == "undo":
                answer.update(undone=undone, state={"model": answer["args"]["model"]})
            return web.json_response(answer, status=200 if answer["ok"] else 502)
        async with self._amp_lock:
            self._refuse_while_busy()
            picked = self._pick_undo()
            if picked is None:
                raise _conflict("nothing to undo", "nothing_to_undo")
            self._expect_pick(picked, expect)
            index, undone = picked
            if undone["op"] != "amp":
                raise _conflict("the newest change changed while this undo waited: undo again", "changed")
            name, value = undone["args"]["name"], undone["previous"]
            try:
                amp, _, described = await self._amp_now(writing=True)
                amp_live.undo_check(undone, amp, self._amp_root())
            except amp_live.AmpRefused as exc:
                if skip and exc.code in ("stale", "no_amp", "panel"):
                    return web.json_response(self._amp_skip(index, undone, exc))
                hint = '; to give up on this entry and go on to the one before it, send {"skip": true}'
                raise self._amp_http(exc, hint if exc.code == "stale" else "")
            if skip:
                raise _conflict("the newest change can be undone as it stands: undo it, "
                                "a skip is only for one that cannot be", "not_stale")
            try:
                previous = amp.values.get(name)
                await self._amp_send(request, amp, name, value, described)
            except amp_live.AmpRefused as exc:
                raise self._amp_http(exc)
            entry = self._record("undo", {"of": "amp", "name": name, "value": amp.values[name]}, previous,
                                 undoes=index)
        return web.json_response({"ok": True, **entry, "undone": undone,
                                  "state": {"name": name, "value": amp.values[name]}})

    # ======================================================================= #
    # (end of the amp)
    # ======================================================================= #

    # ======================================================================= #
    # Saving the running rig (S4): POST /rig/save
    # ======================================================================= #
    #
    # Kept in one block so it merges past other work on this file. Outside it
    # the agent calls in at `app` (`_save_routes`) and `pick_undo`, which
    # passes over a save. The decisions are the pure functions under "Saving
    # the running rig" below and save.py's `save_rig`, which writes the file.
    #
    # Locks, in the agent's one order (lifecycle, live, amp): a save into the
    # running rig's own file takes the live lock and the amp's, so no change
    # to Sushi or to the amp lands between the capture and the log entry that
    # says what was saved; a save-as takes the lifecycle lock first (`busy:
    # "save"`), since it renames the running rig in the state file as a live
    # switch does, and a `down` must not clear the state file under it.

    def _save_routes(self, app) -> None:
        app.router.add_post("/rig/save", self.save_route)

    async def save_route(self, request):
        """`POST /rig/save {"name"?}`: capture the running rig and write it into
        a rig file of config/rigs, and log it (`op: "save"`).

        No name, or the running rig's own: into the running rig's file, in
        place, keeping its comments (`save.save_rig`). Another name: "save
        as", a new `config/rigs/<name>.yaml` beside it, which then becomes the
        running rig (compiled config and state file, as a live switch records
        one), so `restart`, the next plain save, /health and /rigs follow it.
        The file is written and NOT committed: committing in the player's
        checkout is the player's call, so the answer says the commit it sits on
        and whether the checkout is dirty (`take.library_state`).

        The amp is what the agent last sent Carla (amp_live.py); a run with no
        amp leaves the file's `amp` as it is.

        200 with `file`, `how` ("updated" or "new"), `rig` (running now),
        `committed: false`, `library`, `amp` and the log `entry`. Refusals:

        - 400: a name `save.validate_name` refuses, or a body with anything
          but `name` in it.
        - 409 `busy`: a lifecycle command is running; `no_rig`; `panel`: the
          rig runs with its panel, whose faders reach the amp through the
          listener, so the agent does not know the amp's values, and the
          panel has its own SAVE; `v1`: a config not started with `--rig`,
          which the CLI's `sushi-rig save` saves; `taken`: the name is
          another rig's file; `changed`: the running rig changed while this
          waited; `not_saved`: `save_rig` refused (a capture naming a
          plugin the file lacks and the log did not add, or lacking one the
          log did not remove: another rig's, as far as anything here
          knows), with its reason.

        The plugins the agent added and removed since the rig came up are
        handed to the save (`live_edits`, read under the live lock with the
        capture), so the file is written with the chains the rig now runs
        (#32): `rigfile.fold_state` says how.
        - 503/502/504 from the capture, as /live answers them; 504 too if the
          write is not done within `SAVE_TIMEOUT` (it may still finish).
        - 500, `ok: false` and `recorded: false`, with the save's answer
          beside the reason, if a save-as wrote its file but could not make
          it the running rig: nothing is logged, so the page's unsaved list
          stays as it was, and the reason says the file is there.

        A save is logged only once it has wholly happened, under the same
        locks as its capture, so the log's `op: "save"` is always a rig whose
        file holds what was playing then.

        Not run against a live Sushi: shown against the fake capture.
        """
        try:
            asked = save_request(await self._json_body(request))
        except AgentError as exc:
            raise web.HTTPBadRequest(reason=str(exc))
        self._refuse_while_busy()
        _, source, target = await self._save_target(asked)  # refused early, before any lock
        if target == source:
            answer = await self._save_held(request, asked, source, held=False)
            return web.json_response(answer)
        self._refuse_while_busy()  # no await since the last one: nothing can have started
        async with self._lock:
            self.busy = "save"
            try:
                answer = await self._save_held(request, asked, source, held=True)
            finally:
                self.busy = None
                await self._midi_sync()  # the listener's plan names the rig, which is renamed
        return web.json_response(answer, status=200 if answer["ok"] else 500)

    async def _save_target(self, asked: str | None) -> tuple[dict[str, Any], Path, Path]:
        """`(state, source, target)`: the state file, the running rig's file and
        the file a save writes, or the refusal (409 with its code)."""
        described = self.describe()
        state = await asyncio.to_thread(self.switching.read_state) or {}
        refusal = save_refusal(state, described)
        if refusal is not None:
            raise _conflict(*refusal)
        source = rig_file_of(state, self._midi_root())
        target = source.with_name(f"{asked or source.stem}.yaml")
        if target != source and await asyncio.to_thread(target.exists):
            raise _conflict(
                f"a rig called {target.stem} already exists ({self._save_shown(target)}): saving over it "
                f"would turn it into {source.stem}, and git is its only copy. Choose another name, "
                "or run that rig and save it", "taken")
        return state, source, target

    async def _save_held(self, request, asked: str | None, source: Path, *, held: bool) -> dict[str, Any]:
        """Capture, write, (for a save-as) make the new file the running rig,
        and log the save: all under the live lock and the amp's, so no change
        to Sushi or the amp lands between the capture and the entry, and an
        amp request never meets a state file that names the new rig while
        the amp's record still belongs to the old one. `held` says the caller
        holds the lifecycle lock (a save-as), so busy is not checked again.
        The state file is read once more when the locks are won (a switch or
        a restart may have come between), and that reading is the one used."""
        if not held:
            self._refuse_while_busy()
        async with self._connected(request) as controller:
            async with self._live_lock:
                if not held:
                    self._refuse_while_busy()
                state, now, target = await self._save_target(asked)
                if now != source:
                    raise _conflict(
                        f"the running rig changed to {now.stem} while this save waited, so nothing was "
                        "saved: look at the rig and save again", "changed")
                async with self._amp_lock:
                    model, parameters, amp_said = await self._save_amp()
                    captured = await self._sushi(request, self.live.snapshot, controller, reading=True)
                    captured, master_said = self._save_master(captured)
                    # The adds and removes the agent made since the rig came
                    # up, so the file is written with the plugins it now
                    # runs (#32): read here, under the live lock, so no edit
                    # lands between it and the capture.
                    edits = live_edits(self._chain_log, since=state.get("started_at"))
                    kept: dict[str, Any] = {}
                    out, library, written = await self._save_write(
                        request, target, source, captured, model, parameters, edits, kept)
                    created = out != source
                    shown, was = self._save_shown(out), self._save_shown(source)
                    note = (f"{shown} is a new file, written from nothing: the comments stay in {was}"
                            if created else f"{shown} was updated in place, with its comments")
                    answer = {
                        "ok": True, "file": shown, "path": str(out), "rig": out.stem,
                        "how": "new" if created else "updated", "previous": was,
                        "note": note + "; it is not committed", "committed": False, "library": library,
                        "amp": amp_said,
                        **({"master": master_said} if master_said else {}),
                    }
                    if created:
                        failure, pending = await self._save_as_running(state, out.stem)
                        if pending:
                            # Sushi runs what it loaded: the new file's MIDI
                            # map, channels or host take effect at a restart.
                            answer["restart"] = {"why": pending, "note": " ".join((
                                f"{out.stem} runs as {source.stem} was loaded until it restarts: "
                                + "; ".join(r["code"] + ": " + r["detail"] for r in pending)).split())}
                            answer["note"] += f"; {answer['restart']['note']}"
                        if failure is not None:
                            reason = " ".join((
                                f"{shown} was written with the sound that was playing, but the rig could not "
                                f"be made to run as {out.stem} ({failure}): it still runs as {source.stem}, and "
                                f"restart brings that back. Nothing is logged as saved, so the unsaved list is "
                                f"as it was. The file stays, so saving as {out.stem} again is refused as taken: "
                                f"start {out.stem} to play it, or delete it").split())
                            print(f"sushi-rig agent: {reason}", file=sys.stderr, flush=True)
                            self._remember(["save", out.stem], reason, kind="lifecycle")
                            return {**answer, "ok": False, "reason": reason, "recorded": False}
                        answer["recorded"] = True
                    chain_before = list(self._chain_log)  # what a Save's Undo gives back (#209)
                    answer["entry"] = self._record("save", {
                        "file": shown, "rig": out.stem, "how": answer["how"], "library": library,
                        "amp": amp_said["saved"],
                        # What the next save starts from (`live_edits`): the
                        # plugins added live this one wrote, with their defaults.
                        "added": edits["added"],
                    }, was)
                    if created:
                        self.history.push("none", f"save as {out.stem}", rig=out.stem, act="save_as",
                                          why="Save as makes a new rig")
                    else:
                        self._history_file(out, kept.get("before"), written, f"save {out.stem}", act="save",
                                           run={"state": state}, save_entry=answer["entry"],
                                           chain_before=chain_before)
                    # Kept only now that the save has wholly happened, so a
                    # revert point is always a save that was logged.
                    answer["snapshot"] = await self._keep_snapshot(
                        request, out.stem, written,
                        {"file": shown, "how": answer["how"], "library": library,
                         **({"saved_as": was} if created else {})})
        return answer

    def _save_master(self, captured: Any) -> tuple[Any, str | None]:
        """A capture with the master's gain as the level a dip meant to put
        back, where a dip could not (#137, `_master_down`) and the master is
        still at the floor it left: the floor is the dip's, not the player's,
        and a save must never write it as the rig's master volume. A master
        set to anything else since is the player's, and saved as it is."""
        down = self._master_down
        processors = captured.get("processors") if isinstance(captured, dict) else None
        master = processors.get(ramp.MASTER) if isinstance(processors, dict) else None
        parameters = master.get("parameters") if isinstance(master, dict) else None
        gain = parameters.get(ramp.GAIN) if isinstance(parameters, dict) else None
        if down is None or not isinstance(gain, (int, float)) or not ramp.same(gain, ramp.FLOOR):
            return captured, None
        fixed = {**captured, "processors": {**processors, ramp.MASTER: {
            **master, "parameters": {**parameters, ramp.GAIN: down["level"]}}}}
        return fixed, ("the master volume is at -inf because a dip around a chain edit could not put it "
                       "back; the file has the level it had before that dip, not -inf")

    async def _keep_snapshot(self, request, rig: str, data: bytes, said: dict[str, Any]) -> dict[str, Any]:
        """Keep a save's bytes as a revert point (saves.py), off the loop.
        The save has happened whatever this does, so a snapshot that cannot
        be kept is said in the answer and in /health, loudly, and never
        turns the save into a failure: `{"kept": true, "id"}` or `{"kept":
        false, "why"}`."""
        from . import saves

        try:
            meta = await asyncio.wait_for(
                asyncio.to_thread(saves.keep, self._midi_root(), rig, data, said), SAVE_TIMEOUT)
        except Exception as exc:  # noqa: BLE001 -- said, the save stands
            why = " ".join(f"the save was written, but its snapshot for Revert could not be kept: "
                           f"{type(exc).__name__}: {exc}".split())
            print(f"sushi-rig agent: {why}", file=sys.stderr, flush=True)
            self._remember([request.method, request.path], why, kind="live")
            return {"kept": False, "why": why}
        return {"kept": True, "id": meta["id"], "at": meta["at"], "keep": saves.KEEP}

    async def _save_amp(self) -> tuple[str | None, dict[str, float] | None, dict[str, Any]]:
        """`(model, parameters, said)`: the amp as the agent last sent it, for
        `save_rig`, or `(None, None, ...)` for a run with no amp, which leaves
        the file's `amp` alone. Called holding the amp's lock."""
        try:
            amp, _, _ = await self._amp_now(writing=False)
        except amp_live.AmpRefused as exc:
            if exc.code == "no_amp":
                return None, None, {"saved": False, "why": f"{exc}, so the file's amp was left as it was"}
            raise self._amp_http(exc)
        if not amp.model:
            return None, None, {"saved": False, "why": "the state file does not say which model the amp "
                                "runs, so the file's amp was left as it was"}
        return amp.model, dict(amp.values), {
            "saved": True, "model": amp.model, "parameters": dict(amp.values),
            "known": amp_live.KNOWN_SENT, "note": amp_live.KNOWN_NOTE[amp_live.KNOWN_SENT]}

    async def _save_write(
        self, request, target: Path, source: Path, captured: dict[str, Any],
        model: str | None, parameters: dict[str, float] | None, edits: dict[str, Any] | None = None,
        kept: dict[str, Any] | None = None,
    ) -> tuple[Path, dict[str, Any] | None, bytes]:
        """`save_rig`, then `take.library_state` and the file's own git state
        (`save.file_git_state`), off the loop, within `SAVE_TIMEOUT`; refusals as `save_route` says. The thread cannot be
        stopped past the deadline (see `LIVE_TIMEOUT`), so a 504 says the
        file may still be written. Also the bytes written, read back at once
        in the same thread, for the save's snapshot (saves.py)."""
        from .save import SaveError, file_git_state, save_rig
        from .take import library_state

        root = self._midi_root()

        def write() -> tuple[Path, dict[str, Any] | None, bytes]:
            if kept is not None:  # what the file held, for Undo (#209)
                kept["before"] = target.read_bytes() if target.exists() else None
            out = save_rig(target.stem, source, amp_model=model, amp_parameters=parameters, state=captured,
                           edits=edits)
            written = out.read_bytes()
            library = library_state(root)
            if library is not None:
                # `dirty` is the whole checkout's; what the file itself is,
                # and whether anything else is uncommitted, is git's to say.
                library.update(file_git_state(root, out) or {"file": None, "others": None})
            return out, library, written

        try:
            return await self._write_off_loop(request, target, write, again="saving again")
        except SaveError as exc:
            raise _conflict(" ".join(f"not saved: {exc}".split()), "not_saved")

    async def _write_off_loop(self, request, path: Path, work: Callable[[], Any], *, again: str) -> Any:
        """`work()`, which writes rig file `path`, off the loop within
        `SAVE_TIMEOUT`: the one shape of a save's write and the rig files
        routes' (`_rigfile_thread`). The thread cannot be stopped past the
        deadline (see `LIVE_TIMEOUT`), so the 504 says the file may still be
        written; an OSError is a 500 naming the file; both are remembered
        for /health. Anything else `work` raises is the caller's to answer.
        `again` is what not to do before looking ("saving again")."""
        where = [request.method, request.path]
        try:
            return await asyncio.wait_for(asyncio.to_thread(work), SAVE_TIMEOUT)
        except asyncio.TimeoutError:
            reason = (f"writing {self._save_shown(path)} did not finish within {SAVE_TIMEOUT:g}s, and may "
                      f"still: look at the file before {again}")
            self._remember(where, reason, kind="live")
            raise web.HTTPGatewayTimeout(reason=reason)
        except OSError as exc:
            reason = " ".join(f"writing {self._save_shown(path)} failed: {type(exc).__name__}: {exc}".split())
            self._remember(where, reason, kind="live")
            raise web.HTTPInternalServerError(reason=reason)

    async def _save_as_running(self, state: dict[str, Any], name: str) -> tuple[str | None, list[dict[str, str]]]:
        """After a save-as wrote rig `name`: make it the running rig, as a live
        switch records the rig it switched to (`saved_as_state`), from
        `state`, the state file as read under the locks; None when done, or
        what failed. The new file is compiled as `up --rig` would compile it
        and written where `up` writes a run's config, so the next live switch
        diffs from it and `restart` brings it back. Called holding the amp's
        lock, so the amp's record moves to the renamed run in the same step:
        the run is renamed, not restarted, and what the agent sent Carla
        carries over rather than being seeded again from the config.

        The second value is why the new file differs from what Sushi runs in
        what only a restart loads (`rigfile.diff`'s reasons), [] when it does
        not. Then the config recorded for the run is the running one, renamed
        (`renamed_running`): a file carrying a pedal or a channel written for
        the next restart (#192) must not have the running config claim
        mappings, inputs or tracks Sushi never loaded, which the listener and
        the next live switch read (PR #202's re-review)."""
        try:
            compiled = await asyncio.to_thread(self.switching.compile, name, self._midi_root(), state.get("machine"))
            pending: list[dict[str, str]] = []
            try:
                running = await asyncio.to_thread(self.switching.read_config, state.get("config"))
            except Exception:  # noqa: BLE001 -- no running config to keep: the new compile stands for it
                running = None
            if isinstance(running, dict):
                decided = rigfile.diff(running, compiled, root=self._amp_root(),
                                       amp_off=state.get("amp_requested") is False)
                if isinstance(decided, rigfile.NeedsRestart):
                    pending = [reason.as_dict() for reason in decided.reasons]
                    compiled = renamed_running(running, compiled)
            path = await asyncio.to_thread(self.switching.write_config, name, compiled)
            after = saved_as_state(state, name, path)
            await asyncio.to_thread(self.switching.write_state, after)
        except Exception as exc:  # noqa: BLE001 -- said by the caller, with the file that was written
            return f"{type(exc).__name__}: {exc}", []
        if self._amp is not None and self._amp.run == amp_live.run_of(state):
            self._amp.run = amp_live.run_of(after)
        return None, pending

    def _save_shown(self, path: Path) -> str:
        """A rig file as the player reads it: relative to the checkout."""
        try:
            return Path(path).resolve().relative_to(Path(self._midi_root()).resolve()).as_posix()
        except ValueError:
            return str(path)

    # ======================================================================= #
    # (end of saving the running rig)
    # ======================================================================= #

    # ======================================================================= #
    # Rig files (the New rig screen): GET /machine/roles, GET /rigs/{name}/channels,
    # POST /rigs/new, POST /rigs/{name}/channels/add,
    # POST /rigs/{name}/channels/{channel}/input
    # ======================================================================= #
    #
    # Kept in one block, as save's is. Outside it the agent calls in at `app`
    # (`_rigfile_routes`) and `_json_errors` (`agent_extra`, for a refusal's
    # advice on lines of its own). The decisions are rigedit.py's and the
    # pure functions under "Rig files" at the end of this module; every rule
    # about what a rig may be is the compiler's, asked through
    # `rig.compile_rig_draft` with the run's machine file.
    #
    # Each write takes the lifecycle lock (`busy: "rig file edit"`), so a
    # restart, switch or save-as cannot come between the reading of the state
    # file (which rig runs, on which machine file) and the write, and two
    # writes to one file cannot interleave; an edit of the running rig's own
    # file takes the live lock too, so no change lands between the check for
    # unsaved work and the write. The order is the agent's one: lifecycle,
    # then live.
    #
    # Remote callers are treated as `/rig/save` treats them: not at all
    # differently. A write needs no loopback peer, because Save, which writes
    # the same files, needs none, and `is_local` is used only to allow what
    # the CLI on this machine could do anyway. The agent binds to localhost;
    # `--host 0.0.0.0` is the operator's call (#29's permission split).

    def _rigfile_routes(self, app) -> None:
        app.router.add_get("/machine/roles", self.machine_roles_route)
        app.router.add_post("/rigs/new", self.new_rig_route)
        app.router.add_get("/rigs/{name}/channels", self.rig_channels_route)
        app.router.add_post("/rigs/{name}/channels/add", self.add_channel_route)
        app.router.add_post("/rigs/{name}/channels/{channel}/input", self.channel_input_route)

    async def _rigfile_run(self) -> dict[str, Any]:
        """What the rig files routes need of the run, read now: `described`,
        the state file, the running rig's file (None unless a rig of channels
        is up) and the run's machine file -- the one the running rig was
        compiled against (the state file's `machine`), or this machine's
        when no rig of channels is up."""
        from .machine import machine_path

        described = self.describe()
        state = await asyncio.to_thread(self.switching.read_state) or {}
        root = self._midi_root()
        running = None
        if described.get("running") and isinstance(state.get("rig"), str) and state["rig"]:
            running = rig_file_of(state, root)
        machine = state.get("machine") if running is not None else None
        machine_file = Path(machine) if isinstance(machine, str) and machine else machine_path(root)
        if not machine_file.is_absolute():
            machine_file = root / machine_file
        # Whether the amp is wired (#246), for a switch into the edited file
        # to ask "Restart?" over (`_would_restart`), as the switch decides.
        wiring = await self._amp_wiring_decided(described)
        return {"described": described, "state": state, "running": running, "machine": machine_file,
                "wiring": wiring}

    def _rigfile_path(self, name: str) -> Path:
        """config/rigs/<name>.yaml for a rig `/rigs` lists, or 404: a name in
        the URL is looked up among the files, never made a path."""
        root = self._midi_root()
        if name not in midi_listener.program_order(root):
            raise web.HTTPNotFound(reason=f"there is no rig called {name!r} in config/rigs/")
        return root / "config" / "rigs" / f"{name}.yaml"

    async def machine_roles_route(self, request):
        """`GET /machine/roles`: the machine file's roles, for the New rig
        screen's pickers (`rigedit.role_options`: ports, `mono_to_stereo`,
        the widths each can feed and whether it can take the master, as the
        patchbay answers), which tracks of the running rig use each (from the
        running config's `_wiring`), the role a new rig's master goes to,
        and the channel ceiling with the compiler's reason. Read only: the
        machine file is never written here. 409 `machine` when it cannot be
        read or does not validate, with why."""
        from . import rigedit
        from .machine import MachineError, MachineSpec
        from .patchbay import MONITOR_ROLE

        run = await self._rigfile_run()
        machine_file = run["machine"]

        def read() -> dict[str, Any]:
            try:
                machine = MachineSpec.load(machine_file)
            except MachineError as exc:
                raise RigFileRefused(str(exc), "machine") from exc
            problems = machine.validate()
            if problems:
                raise RigFileRefused(f"{machine_file.name}: " + "; ".join(problems), "machine")
            wiring, why = None, None
            if run["running"] is not None:
                try:
                    wiring = self.switching.read_config(run["described"].get("config")).get(rigfile.WIRING)
                except Exception as exc:  # noqa: BLE001 -- said, not raised: the roles are still worth listing
                    why = f"the running rig's compiled config could not be read: {type(exc).__name__}: {exc}"
            return {"roles": rigedit.role_options(machine, wiring if isinstance(wiring, dict) else None),
                    "wiring_note": why}

        try:
            found = await asyncio.to_thread(read)
        except RigFileRefused as exc:
            raise _conflict(" ".join(str(exc).split()), exc.code)
        running = run["running"]
        return web.json_response({
            "ok": True, "machine": self._save_shown(machine_file), **found,
            "output": MONITOR_ROLE, "ceiling": rigedit.ceiling(),

            "running": None if running is None else {"rig": running.stem, "file": self._save_shown(running)},
            "note": ("which roles are inputs and which outputs is read from their names (-in, -out): the "
                     "machine file does not say. Whether a channel can use a role is the compiler's answer, "
                     "given when the rig is written"),
        })

    async def rig_channels_route(self, request):
        """`GET /rigs/{name}/channels`: a rig file's channels as the file has
        them (`rigedit.channels_of`), for "start from" and for the rack's
        input change, which edits the file and not the running rig; and
        `amp`, whether the file has an `amp:`, for a load with amp (#246)."""
        from . import rigedit

        path = self._rigfile_path(request.match_info["name"])

        def read() -> Any:
            import yaml

            return yaml.safe_load(path.read_text())

        try:
            raw = await asyncio.to_thread(read)
        except Exception as exc:  # noqa: BLE001 -- a file that will not read is said, as a switch says it
            raise _conflict(" ".join(f"{path.name} could not be read: {type(exc).__name__}: {exc}".split()),
                            "rig_problem")
        run = await self._rigfile_run()
        # The channels a MIDI blend drives, and by which control (#34): their
        # volume follows the pedal, so the page shows it and does not set it.
        try:
            blended = rigfile.blend_controls(rigfile.parse_rig(raw))
            blended_note = None
        except Exception as exc:  # noqa: BLE001 -- the channels are still worth listing
            blended = {}
            blended_note = " ".join(f"{path.name} does not parse ({type(exc).__name__}: {exc}), so which "
                                    "channels a blend drives is not known".split())
        return web.json_response({
            "ok": True, "rig": path.stem, "file": self._save_shown(path),
            "running": run["running"] is not None and run["running"].resolve() == path.resolve(),
            "channels": rigedit.channels_of(raw), "blended": blended, "blended_note": blended_note,
            # Whether the file has an `amp:` (#246): a strip's Load… offers
            # "Load with amp" from a rig with one onto a running rig with none.
            "amp": rigedit.has_amp(raw),
        })

    async def new_rig_route(self, request):
        """`POST /rigs/new {"name", "channels": [{"name", "input", "width",
        "from"?}]}`: write a new `config/rigs/<name>.yaml`, its master on
        `patchbay.MONITOR_ROLE` (as `convert` writes every rig's), its chains
        empty unless a channel's `from: {"rig", "channel"}` copies one.

        Compiled and wired with the run's machine file before anything is
        written (`rig.compile_rig_draft`), and refused with the compiler's
        reason (409), nothing written: `name` (the naming rule,
        `save.validate_name`), `taken` (a rig of that name exists),
        `no_source` (a `from` that names no rig or channel), `rig_problem`
        (the compiler's or the patchbay's own words: the ceiling, a duplicate
        or invalid channel name, a width or a role the machine cannot give;
        with `machine_hint`, the lines to add to the machine file, when a
        role is missing). 400 for a body of the wrong shape.

        200 with `file`, `rig`, `how: "new"`, `committed: false`, `library`
        (the commit and what git says of the file, as a save's), and `offer`:
        `start` with no rig up, `switch` with one; and `programs` when the new
        file moves other rigs' Program Change numbers (`programs_moved`)."""
        from . import rigedit

        try:
            name, channels = rigedit.new_rig_request(await self._json_body(request))
        except rigedit.EditRequestError as exc:
            raise web.HTTPBadRequest(reason=str(exc))
        self._refuse_while_busy()
        source_git = await self._sources_git_before([c["from"] for c in channels])
        answer = await self._rigfile_locked(request, lambda run: self._write_new_rig(request, run, name, channels))
        self._sources_git_fill([c["from"] for c in answer["channels"]], source_git)
        answer["offer"] = "switch" if answer["run"]["described"].get("running") else "start"
        answer["takes_effect"] = "start"
        answer["note"] = (f"{answer['file']} is a new rig, not running and not committed: "
                          f"{'switch to' if answer['offer'] == 'switch' else 'start'} it to play it")
        return web.json_response(await self._rigfile_answer(answer))

    async def add_channel_route(self, request):
        """`POST /rigs/{name}/channels/add {"name", "input", "width",
        "from"?}`: add a channel to the end of an existing rig file, in
        place, keeping its comments (`rigfile.write_rig`), so the file
        changes by the channel's own lines and nothing else. Refused as
        `/rigs/new` refuses, nothing written; and for the running rig, as
        `_rigfile_refusal` says (`unsaved`, `panel`). For the running rig the
        answer says it takes effect at the next restart, and why
        (`rigfile.diff`'s reasons, as a switch gives them), and offers it."""
        from . import rigedit

        try:
            change = rigedit.channel_request(await self._json_body(request))
        except rigedit.EditRequestError as exc:
            raise web.HTTPBadRequest(reason=str(exc))
        path = self._rigfile_path(request.match_info["name"])

        def edit(raw: dict[str, Any], run: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
            copied, source = self._rigfile_copy(change)
            said = {"op": "add_channel", "channel": change["name"], "input": change["input"],
                    "width": change["width"], "from": source}
            return rigedit.with_channel(raw, rigedit.channel_entry(change, copied)), said

        self._refuse_while_busy()
        source_git = await self._sources_git_before([change["from"]])
        answer = await self._rigfile_locked(request, lambda run: self._write_edit(request, run, path, edit))
        self._sources_git_fill([answer["change"].get("from")], source_git)
        return web.json_response(await self._rigfile_answer(answer))

    async def channel_input_route(self, request):
        """`POST /rigs/{name}/channels/{channel}/input {"input"?, "width"?}`:
        set a channel's input role, its width, or both, in place, keeping the
        file's comments. Refused and answered as `add_channel_route` is."""
        from . import rigedit

        try:
            change = rigedit.input_request(await self._json_body(request))
        except rigedit.EditRequestError as exc:
            raise web.HTTPBadRequest(reason=str(exc))
        path = self._rigfile_path(request.match_info["name"])
        channel = request.match_info["channel"]

        def edit(raw: dict[str, Any], run: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
            try:
                edited = rigedit.with_input(raw, channel, change)
            except LookupError as exc:
                raise RigFileRefused(str(exc), "no_channel") from exc
            before = next(e for e in raw["channels"] if isinstance(e, dict) and e.get("name") == channel)
            return edited, {"op": "input", "channel": channel,
                            "was": {"input": before.get("input"), "width": before.get("width")}, **change}

        answer = await self._rigfile_locked(request, lambda run: self._write_edit(request, run, path, edit))
        return web.json_response(await self._rigfile_answer(answer))

    async def _rigfile_locked(self, request, write: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]):
        """Run `write(run)` under the lifecycle lock, `busy: "rig file
        edit"`, with the run read once the lock is won; a `RigFileRefused`
        is its 409."""
        self._refuse_while_busy()
        async with self._lock:
            self.busy = "rig file edit"
            try:
                run = await self._rigfile_run()
                try:
                    answer = await write(run)
                except RigFileRefused as exc:
                    error = _conflict(" ".join(str(exc).split()), exc.code)
                    if exc.extra:
                        error.agent_extra = exc.extra
                    raise error
            finally:
                self.busy = None
        answer["run"] = run
        return answer

    async def _rigfile_answer(self, answer: dict[str, Any]) -> dict[str, Any]:
        """The answer, with what git says of the file read now, outside the
        lifecycle lock (`_rigfile_git_after`), and without what was only for
        the route (`run`, `path`)."""
        await self._rigfile_git_after(answer, answer["path"])
        return {k: v for k, v in answer.items() if k not in ("run", "path")}

    def _rigfile_copy(self, change: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        """`(copied, said)` for a channel's `from`: its chain and level from
        that rig of config/rigs as the file is now, and where it came from,
        or `(None, None)`. Read from the file as it stands, not from git's
        commit of it: a tone saved and not yet committed is the one the
        player hears, and copying an older one would be the silent kind of
        wrong.

        The chain is copied as a load copies it (`channelcopy.source_of`,
        `loaded_chain`): each plugin named for the new channel, so two
        channels may start from one channel, and a channel of a rig may
        start from that rig's own channel (#208, as a preset could, #127).
        A name past the naming rule is `name`; a clash is the compiler's.
        What git says of the source file (`said["git"]`) is read before the
        lifecycle lock is taken (`_sources_git_before`), never under it."""
        from . import channelcopy

        source = change.get("from")
        if not source:
            return None, None
        root = self._midi_root()
        if source["rig"] not in midi_listener.program_order(root):
            raise RigFileRefused(f"there is no rig called {source['rig']!r} in config/rigs/ to start "
                                 f"{change['name']} from", "no_source")
        path = root / "config" / "rigs" / f"{source['rig']}.yaml"
        import yaml

        try:
            raw = yaml.safe_load(path.read_text())
            copied_from = channelcopy.source_of(raw, source["rig"], source["channel"])
        except LookupError as exc:
            raise RigFileRefused(f"{change['name']} cannot start from it: {exc}", "no_source") from exc
        except Exception as exc:  # noqa: BLE001 -- a source that will not read is said
            raise RigFileRefused(f"{path.name} could not be read to start {change['name']} from: "
                                 f"{type(exc).__name__}: {exc}", "no_source") from exc
        bad = channelcopy.name_problems(change["name"], copied_from)
        if bad:
            raise RigFileRefused(f"nothing was written: {'; '.join(bad)}", "name")
        copied: dict[str, Any] = {"chain": channelcopy.loaded_chain(copied_from, change["name"])}
        if copied_from.level:
            copied["level"] = copied_from.level
        return copied, {"rig": source["rig"], "channel": source["channel"], "file": self._save_shown(path),
                        "plugins": len(copied["chain"]), "git": None}

    async def _sources_git_before(self, refs: list[dict[str, Any] | None]) -> list[Any]:
        """What git says of each `from`'s rig file (`{"rig", "channel"}` or
        None), read before the lifecycle lock is taken and so before
        anything is written: a copy from the rig it lands in is said as it
        was copied, never as the write made it (re-review of PR #234), and a
        hung git keeps no lifecycle route busy (review, 2026-11-06). Within
        `SAVE_TIMEOUT`; past it, or for a rig not among config/rigs/' names
        (never made a path as given), `None`, not known. A moment before
        the copy: a save landing between is not seen."""
        from .save import file_git_state

        if not any(refs):
            return [None] * len(refs)
        root = self._midi_root()

        def read() -> list[Any]:
            names = set(midi_listener.program_order(root))
            return [(file_git_state(root, root / "config" / "rigs" / f"{r['rig']}.yaml") or {}).get("file")
                    if r and r.get("rig") in names else None for r in refs]

        try:
            return await asyncio.wait_for(asyncio.to_thread(read), SAVE_TIMEOUT)
        except asyncio.TimeoutError:
            return [None] * len(refs)

    @staticmethod
    def _sources_git_fill(sources: list[dict[str, Any] | None], states: list[Any]) -> None:
        """Each copy's `from` given its `git`, as `_sources_git_before` read it."""
        for source, state in zip(sources, states):
            if source:
                source["git"] = state

    def _rigfile_compile(self, raw: dict[str, Any], run: dict[str, Any]) -> dict[str, Any]:
        """`rig.compile_rig_draft` against the run's machine file, or the
        refusal: the compiler's reason, and the lines to add to the machine
        file when a role is missing (`rigedit.machine_hint`)."""
        from . import rigedit
        from .machine import MachineSpec
        from .rig import compile_rig_draft

        try:
            return compile_rig_draft(raw, self._midi_root(), run["machine"])
        except RigError as exc:
            extra = {}
            try:
                machine = MachineSpec.load(run["machine"])
                hint = rigedit.machine_hint(machine, raw, self._save_shown(run["machine"]))
            except Exception:  # noqa: BLE001 -- advice, never a reason to lose the refusal it is beside
                hint = None
            if hint:
                extra["machine_hint"] = hint
            raise RigFileRefused(f"nothing was written: {exc}", "rig_problem", extra) from exc

    def _rigfile_git(self, path: Path) -> dict[str, Any] | None:
        """What git says of the file written, as a save says it."""
        from .save import file_git_state
        from .take import library_state

        root = self._midi_root()
        library = library_state(root)
        if library is not None:
            library.update(file_git_state(root, path) or {"file": None, "others": None})
        return library

    async def _rigfile_git_after(self, answer: dict[str, Any], path: Path) -> dict[str, Any]:
        """`answer` with `library`, what git says of the file: read once the
        lifecycle lock is let go, and within `SAVE_TIMEOUT`, since git is
        nothing a lifecycle command should wait on (review, 2026-11-06: a
        hung git under the lock left every lifecycle route answering busy).
        Past the deadline it is `null`, which the page says as not known,
        with `library_note` saying why; the file is written either way."""
        try:
            answer["library"] = await asyncio.wait_for(asyncio.to_thread(self._rigfile_git, path), SAVE_TIMEOUT)
        except asyncio.TimeoutError:
            answer["library"] = None
            answer["library_note"] = (f"git did not answer within {SAVE_TIMEOUT:g}s, so what it says of "
                                      f"{self._save_shown(path)} is not known; the file is written")
        return answer

    async def _rigfile_thread(self, request, path: Path, work: Callable[[], Any]) -> Any:
        """`work()` off the loop, as a save's write is (`_write_off_loop`)."""
        return await self._write_off_loop(request, path, work, again="trying again")

    async def _write_new_rig(self, request, run: dict[str, Any], name: str, channels: list[dict[str, Any]]):
        """The new rig's file, compiled before it is written (`new_rig_route`)."""
        from . import rigedit
        from .patchbay import MONITOR_ROLE
        from .rigfile import RigFileError, write_rig
        from .save import SaveError, validate_name

        root = self._midi_root()
        try:
            validate_name(name)
        except SaveError as exc:
            raise RigFileRefused(str(exc), "name") from exc
        path = root / "config" / "rigs" / f"{name}.yaml"

        def work() -> dict[str, Any]:
            if path.exists():
                raise RigFileRefused(f"a rig called {name} already exists ({self._save_shown(path)}): "
                                     "choose another name, or add channels to that one", "taken")
            programs_before = midi_listener.program_order(root)
            entries, sources, said = [], [], {}
            for change in channels:
                copied, source = self._rigfile_copy(change)
                entries.append(rigedit.channel_entry(change, copied))
                sources.append(source)
                if source:
                    said[change["name"]] = f"{source['file']}'s {source['channel']}"
            raw = rigedit.new_rig(name, entries, MONITOR_ROLE)
            self._rigfile_compile(raw, run)
            path.parent.mkdir(parents=True, exist_ok=True)
            try:
                # Claimed first, so a file of that name made since the check
                # above is never overwritten: `write_rig` then fills the
                # empty file it finds as it would write a new one.
                path.open("x").close()
            except FileExistsError as exc:
                raise RigFileRefused(f"a rig called {name} was made while this was being checked: "
                                     "choose another name", "taken") from exc
            try:
                when = datetime.now(timezone.utc).date().isoformat()
                write_rig(path, raw, header=rigedit.new_rig_header(when, said))
            except BaseException:
                path.unlink(missing_ok=True)
                raise
            return {"sources": sources,
                    "programs": programs_moved(programs_before, midi_listener.program_order(root), name),
                    "after": path.read_bytes()}  # for Undo (#209)

        try:
            done = await self._rigfile_thread(request, path, work)
        except RigFileError as exc:
            raise RigFileRefused("nothing was written: " + "; ".join(exc.problems), "rig_problem") from exc
        self._history_file(path, None, done["after"], f"new rig {name}", run=run, act="new_rig")
        return {"ok": True, "rig": name, "file": self._save_shown(path), "how": "new", "committed": False,
                "running": False, "path": path, **({"programs": done["programs"]} if done["programs"] else {}),
                "channels": [{"name": c["name"], "input": c["input"], "width": c["width"], "from": source}
                             for c, source in zip(channels, done["sources"])]}

    async def _write_edit(self, request, run: dict[str, Any], path: Path, edit) -> dict[str, Any]:
        """An edit of rig file `path` (`edit(raw, run)` gives the file's
        contents after it and what changed), compiled before it is written,
        written in place with `write_rig`, and for the running rig's file,
        refused while the rig holds work the edit would strand
        (`rigfile_refusal`), under the live lock so none lands meanwhile."""
        import yaml

        from .rigfile import RigFileError, write_rig

        running = run["running"] is not None and run["running"].resolve() == path.resolve()

        def work() -> dict[str, Any]:
            before = path.read_bytes()  # for Undo (#209)
            raw = yaml.safe_load(before.decode("utf-8"))
            if not isinstance(raw, dict):
                raise RigFileRefused(f"{path.name} is not a rig file", "rig_problem")
            edited, change = edit(raw, run)
            compiled = self._rigfile_compile(edited, run)
            try:
                write_rig(path, edited)
            except RigFileError as exc:
                raise RigFileRefused("nothing was written: " + "; ".join(exc.problems), "rig_problem") from exc
            return {"change": change, "compiled": compiled, "before": before, "after": path.read_bytes()}

        if running:
            async with self._live_lock:
                refusal = rigfile_refusal(run["state"], self.log)
                if refusal is not None:
                    raise RigFileRefused(*refusal)
                done = await self._rigfile_thread(request, path, work)
        else:
            done = await self._rigfile_thread(request, path, work)
        change = done["change"]
        label = (f"add channel {change.get('channel')}" if change.get("op") == "add_channel"
                 else f"input {change.get('channel')}" if change.get("op") == "input" else f"edit {path.stem}")
        self._history_file(path, done["before"], done["after"], label, run=run,
                           apply="restart" if running else "none",
                           act="channel" if change.get("op") == "add_channel" else "input")
        answer = {"ok": True, "rig": path.stem, "file": self._save_shown(path), "how": "updated",
                  "committed": False, "change": done["change"], "running": running, "path": path}
        shown = answer["file"]
        if not running:
            answer.update(takes_effect="next start", offer=None,
                          note=(f"{shown} was updated in place, with its comments; it is not committed. It is "
                                "not the running rig: it plays as edited the next time it is started or "
                                "switched to"))
            return answer
        why = await self._rigfile_restart_why(run, done["compiled"])
        since = run["state"].get("started_at")
        answer.update(
            takes_effect="restart", offer="restart", why=why,
            note=" ".join((
                f"{shown} was updated in place, with its comments; it is not committed. {path.stem} is the "
                "running rig, and it keeps playing as it was: the change takes effect when it restarts "
                f"({'; '.join(r['code'] + ': ' + r['detail'] for r in why) or 'a restart reloads the file'}). "
                "A restart stops the sound for a few seconds and brings the rig back from its file. The "
                f"agent saw no unsaved change since {since or 'it started'}; a change it cannot see (the "
                "panel, or before the agent started) is not in the file").split()))
        return answer

    async def _restart_guard(self) -> tuple[str, str] | None:
        """`restart_refusal` for the run as it is now, for `/rig/restart`
        (`lifecycle`): the restart the rig files routes offer after an edit,
        the picker's and the CLI's alike, all of which reload the file."""
        state = await asyncio.to_thread(self.switching.read_state) or {}
        return restart_refusal(state, self.describe(), self.log)

    async def _rigfile_restart_why(self, run: dict[str, Any], compiled: dict[str, Any]) -> list[dict[str, str]]:
        """Why the running rig needs a restart to become the edited file: what
        `rigfile.diff` says of the running config against the file's, as a
        switch's answer says it. An edit that diff could make live is still
        a restart here, since no live switch goes to the rig already up."""
        described, state = run["described"], run["state"]
        try:
            running = await asyncio.to_thread(self.switching.read_config, described.get("config"))
            decided = rigfile.diff(running, compiled, root=self._amp_root(),
                                   amp_off=state.get("amp_requested") is False)
        except Exception as exc:  # noqa: BLE001 -- unreadable is a reason, said
            return [{"code": "unreadable",
                     "detail": f"the running rig's compiled config could not be read: {type(exc).__name__}: {exc}"}]
        if isinstance(decided, rigfile.NeedsRestart):
            return [reason.as_dict() for reason in decided.reasons]
        return []

    # ======================================================================= #
    # (end of the rig files)
    # ======================================================================= #

    # ======================================================================= #
    # The rig bin (#129): POST /rigs/{name}/delete, POST /rigs/{name}/restore
    # ======================================================================= #
    #
    # Kept in one block, as the rig files' is, and taking the same lifecycle
    # lock through `_rigfile_locked` (`busy: "rig file edit"`), so no start,
    # switch, save-as or revert comes between reading which rig runs and the
    # move. The names and the moves are rigbin.py's; whether a rig may be
    # deleted is `bin_refusal`'s, below with the rig files' pure decisions.
    # Nothing here is a git action: what git holds of the file is read, never
    # written, outside the lock and within `SAVE_TIMEOUT`, as a save's is.

    def _bin_routes(self, app) -> None:
        app.router.add_get("/rigs/bin", self.bin_route)
        app.router.add_post("/rigs/{name}/delete", self.delete_rig_route)
        app.router.add_post("/rigs/{name}/restore", self.restore_rig_route)

    def _bin_agent(self) -> str:
        """Which rig machine's bin this is: two agents served at one origin
        (a tunnel, a port reused) are two bins. This machine's name and the
        checkout's path, hashed: the same across the agent's restarts, and
        nothing private. Until #189 the page kept its Undos under it across
        reloads; it keeps none now, and this stays for curl and scripts."""
        import hashlib
        import socket

        seed = f"{socket.gethostname()}\0{Path(self._midi_root()).resolve()}"
        return hashlib.sha256(seed.encode()).hexdigest()[:16]

    async def bin_route(self, request):
        """`GET /rigs/bin`: what is in the bin, newest first, read only:
        each entry's `rig`, `entry` (the name a restore takes), `file`,
        `deleted_at` (from its stamp) and `back` (a rig of that name is in
        config/rigs/ again, so a restore would be refused `taken`); and
        `agent`, which bin this is (`_bin_agent`). The page asks it with
        /rigs, and offers an Undo only while its entry is here and its rig is
        not back. A bin that is a symbolic link is listed as empty, with
        `problem` saying why."""
        from . import rigbin

        root = self._midi_root()

        def read() -> tuple[list[dict], list[str], str | None]:
            try:
                found = rigbin.listing(root)
            except rigbin.BinRefused as exc:
                return [], [], str(exc)
            return found, midi_listener.program_order(root), None

        found, listed, problem = await asyncio.to_thread(read)
        return web.json_response({
            "ok": True, "agent": self._bin_agent(), "problem": problem,
            "entries": [{"rig": e["rig"], "entry": e["entry"], "file": f"config/rigs/{rigbin.BIN}/{e['entry']}",
                         "deleted_at": datetime.strptime(e["stamp"], rigbin.STAMP).replace(
                             tzinfo=timezone.utc).isoformat(), "back": e["rig"] in listed} for e in found],
        })

    async def _bin_body(self, request, keys: dict[str, type]) -> dict[str, Any]:
        """The body, `{}` for none; 400 for one that is not an object, a key
        nobody reads or a value of the wrong type."""
        body = await self._json_body(request)
        if body is None:
            return {}
        if not isinstance(body, dict):
            raise web.HTTPBadRequest(reason="the body must be a JSON object")
        unknown = sorted(set(body) - set(keys))
        if unknown:
            raise web.HTTPBadRequest(reason=f"nobody reads {', '.join(unknown)}: the keys are {', '.join(keys) or 'none'}")
        for key, kind in keys.items():
            if key in body and not isinstance(body[key], kind):
                raise web.HTTPBadRequest(reason=f"{key} must be {'true or false' if kind is bool else 'text'}")
        return body

    def _bin_name(self, request) -> str:
        from . import rigbin

        name = request.match_info["name"]
        try:
            return rigbin.plain(name)
        except rigbin.BinRefused as exc:
            raise _conflict(str(exc), exc.code)

    async def _bin_git(self, root: Path, path: Path, now_at: Path | None = None) -> tuple[dict | None, str | None]:
        """`rigbin.committed_copy`, off the loop and within `SAVE_TIMEOUT`:
        `(git, None)`, or `(None, why)` when it is not known."""
        from . import rigbin

        try:
            found = await asyncio.wait_for(asyncio.to_thread(rigbin.committed_copy, root, path, now_at), SAVE_TIMEOUT)
        except asyncio.TimeoutError:
            return None, f"git did not answer within {SAVE_TIMEOUT:g}s, so what it holds of {self._save_shown(path)} is not known"
        if found is None:
            return None, f"git could not be read here, so whether {self._save_shown(path)} is committed is not known"
        return found, None

    async def delete_rig_route(self, request):
        """`POST /rigs/{name}/delete {"dry_run"?}`: move rig `name`'s file
        into the bin, `config/rigs/.deleted/<name>-<stamp>.yaml`, out of
        `/rigs`, the picker and the Program Change numbers (rigbin.py).

        Refused (409) for the running rig (`running`: the rig `/health`
        names, the state file's, which a restart and the next save would
        write; switch away first) and for a name that is not plain (`name`:
        no `/`, no `..`, no leading dot); 404 for a rig `/rigs` does not
        list. `{"dry_run": true}` is the page's confirm: the same refusals
        and the same answer, with nothing moved and no lock taken.

        200 with `file`, `how` ("deleted" or "dry_run"), `binned` (`entry`,
        the bin file's name, and `file`), `undo` (the restore call that puts
        it back), `committed: false` (nothing is ever committed), `git`
        (`rigbin.committed_copy`: whether HEAD has the file, whether the
        bytes binned are HEAD's, and the `git checkout` that brings HEAD's
        back) or `git_note` saying why not known, `programs` when a Program
        Change number moves (`programs_deleted`), and `note`."""
        from . import rigbin

        body = await self._bin_body(request, {"dry_run": bool})
        name = self._bin_name(request)
        root = self._midi_root()
        path = rigbin.rig_path(root, name)
        if name not in await asyncio.to_thread(midi_listener.program_order, root):
            raise web.HTTPNotFound(reason=f"there is no rig called {name!r} in config/rigs/")
        if body.get("dry_run"):
            run = await self._rigfile_run()
            refusal = bin_refusal(run, path, root)
            if refusal is not None:
                raise _conflict(*refusal)
            before = await asyncio.to_thread(midi_listener.program_order, root)
            answer = {"ok": True, "rig": name, "file": self._save_shown(path), "how": "dry_run", "committed": False,
                      "programs": programs_deleted(before, [r for r in before if r != name], name)}
            answer["git"], note = await self._bin_git(root, path)
        else:
            def work(run: dict[str, Any]) -> dict[str, Any]:
                refusal = bin_refusal(run, path, root)
                if refusal is not None:
                    raise RigFileRefused(*refusal)
                before = midi_listener.program_order(root)
                try:
                    binned = rigbin.move_to_bin(root, name)
                except rigbin.BinRefused as exc:
                    raise RigFileRefused(str(exc), exc.code) from exc
                return {"binned": binned,
                        "programs": programs_deleted(before, midi_listener.program_order(root), name)}

            done = await self._rigfile_locked(
                request, lambda run: self._rigfile_thread(request, path, lambda: work(run)))
            binned = done["binned"]
            self.history.push("bin", f"delete {name}", rig=name, entry=binned.name, act="delete")
            answer = {"ok": True, "rig": name, "file": self._save_shown(path), "how": "deleted", "committed": False,
                      "binned": {"entry": binned.name, "file": self._save_shown(binned)},
                      "undo": {"path": f"/rigs/{name}/restore", "json": {"entry": binned.name}},
                      "programs": done["programs"]}
            answer["git"], note = await self._bin_git(root, path, binned)
        if note:
            answer["git_note"] = note
        answer["agent"] = self._bin_agent()
        if answer["programs"] is None:
            del answer["programs"]
        answer["note"] = bin_note(answer)
        return web.json_response(answer)

    async def restore_rig_route(self, request):
        """`POST /rigs/{name}/restore {"entry"?}`: put rig `name`'s file
        back from the bin: `entry` (the bin file's name a delete answered
        with), or with none the newest of `name`'s there. What survives a
        reload is the bin itself: the page keeps the entry it was given, and
        a restore needs nothing else of the agent.

        Refused (409): `taken` (a file of that name is there again; it is
        never replaced, and the binned one stays), `gone` (the bin file is
        not there, or there is nothing of `name`'s), `entry` (an entry that
        is not `name`'s), `name` (not a plain name). 200 with `file`, `how:
        "restored"`, `from` (the bin file it was), `committed: false`,
        `programs` when a number moves (`programs_moved`), and `note`."""
        from . import rigbin

        body = await self._bin_body(request, {"entry": str})
        name = self._bin_name(request)
        root = self._midi_root()
        path = rigbin.rig_path(root, name)

        def work() -> dict[str, Any]:
            before = midi_listener.program_order(root)
            try:
                was, _ = rigbin.restore(root, name, body.get("entry"))
            except rigbin.BinRefused as exc:
                raise RigFileRefused(str(exc), exc.code) from exc
            return {"was": was, "programs": programs_moved(before, midi_listener.program_order(root), name)}

        done = await self._rigfile_locked(request, lambda run: self._rigfile_thread(request, path, work))
        answer = {"ok": True, "rig": name, "file": self._save_shown(path), "how": "restored", "committed": False,
                  "from": self._save_shown(done["was"])}
        if done["programs"]:
            answer["programs"] = done["programs"]
        answer["note"] = (f"{answer['file']} is back from {answer['from']}, as it was when deleted; nothing was "
                          "committed. It is listed in /rigs and the Program Change numbers again")
        return web.json_response(answer)

    # ======================================================================= #
    # (end of the rig bin)
    # ======================================================================= #

    # ======================================================================= #
    # Revert to a previous save (2026-11-06): GET /rigs/{name}/saves,
    # POST /rigs/{name}/revert
    # ======================================================================= #
    #
    # Kept in one block, as the rig files' is. Outside it the agent calls in
    # at `app` (`_revert_routes`) and `_save_held` (`_keep_snapshot`, which
    # keeps each save's bytes). What a revert point is, where it is kept and
    # for how long is saves.py's; whether a revert must wait is
    # `revert_refusal`'s, below with the rig files' pure decisions.
    #
    # A revert takes the lifecycle lock (`busy: "revert"`) for all of it: the
    # write and, for the running rig, the live switch into the reverted file
    # (`_switch_held`, the body of POST /rig/switch, #112), so nothing can
    # come between the file being written and the rig being made to play
    # it. The check for unsaved work and the write are under the live lock
    # too, as an edit of the running rig's file is (`_write_edit`).

    def _revert_routes(self, app) -> None:
        app.router.add_get("/rigs/{name}/saves", self.saves_route)
        app.router.add_post("/rigs/{name}/revert", self.revert_route)

    async def saves_route(self, request):
        """`GET /rigs/{name}/saves`: rig `name`'s revert points, newest first
        (`points`): every snapshot a save from here kept (`kind: "save"`,
        the newest `saves.KEEP`) and every commit that changed its file
        (`kind: "commit"`, from `git log`, read-only, with a timeout). Also
        whether the file as it is now is kept by one of them (`current`), so
        the page's confirm can say whether a revert loses it, whether it is
        the running rig, and what could not be read (`problems`,
        `git_note`), each said rather than dropped. 504 if git does not
        answer within `SAVE_TIMEOUT`."""
        from . import saves
        from .save import file_git_state

        name = request.match_info["name"]
        path = self._rigfile_path(name)
        root = self._midi_root()

        # The snapshots need no git, so they are read apart from it: a slow
        # git leaves the saves listed and the commits out, said in
        # `git_note`, rather than a 504 with nothing (review, 2026-11-07).
        def local() -> dict[str, Any]:
            kept, problems = saves.snapshots(root, name)
            current = path.read_bytes() if path.exists() else None
            same = saves.kept_as(current, kept, lambda sid: saves.snapshot_bytes(root, name, sid))
            return {"saves": kept, "problems": problems, "kept_as": same}

        def from_git() -> dict[str, Any]:
            committed, note = saves.commits(root, path)
            git = file_git_state(root, path) if note is None else None
            return {"commits": committed, "git_note": note, "git": (git or {}).get("file")}

        found = await asyncio.to_thread(local)
        try:
            found.update(await asyncio.wait_for(asyncio.to_thread(from_git), SAVE_TIMEOUT))
        except asyncio.TimeoutError:
            found.update(commits=[], git=None, git_note=(
                f"git did not answer within {SAVE_TIMEOUT:g}s, so the file's commits are left out; the saves are all here"))
        found["current"] = {"kept_as": found.pop("kept_as"), "git": found.pop("git")}
        run = await self._rigfile_run()
        return web.json_response({
            "ok": True, "rig": name, "file": self._save_shown(path),
            "running": run["running"] is not None and run["running"].resolve() == path.resolve(),
            "points": saves.merged(found["saves"], found["commits"]),
            "saves": len(found["saves"]), "commits": len(found["commits"]),
            "keep": saves.KEEP, "kept_in": (saves.SAVES_DIR / name).as_posix(),
            "current": found["current"], "git_note": found["git_note"], "problems": found["problems"],
        })

    async def revert_route(self, request):
        """`POST /rigs/{name}/revert {"save": <id>, "discard"?: true}`: write
        revert point `save` (a snapshot's id, or `commit:<sha>`, as `GET
        /rigs/{name}/saves` lists them) back over rig `name`'s file, and, if
        that is the running rig, make the rig play it.

        The file is written with a plain, whole-file write of the point's
        bytes (`saves.restore`), not `rigfile.write_rig`: `write_rig` merges a
        document into the file that is there, keeping *that* file's comments
        and layout, so it cannot give back the snapshot's own; a revert is
        the earlier file, byte for byte. It is compiled first, against the
        run's machine file, and refused with the compiler's reason, nothing
        written, if it will not compile now (`rig_problem`: a plugin gone
        from the manifest since, a role gone from the machine file).

        For the running rig: refused while it holds live work no save has
        written (409 `unsaved`, as an edit of its file is, #125), since the
        revert replaces what is playing, unless `{"discard": true}`. Then
        the live switch into the reverted file (`_switch_held`, #112): live
        where `rigfile.diff` says it can be (values, bypasses, a plugin added,
        removed or moved), by restart where it must be (a channel or the amp
        differs), the answer saying which and why, as a switch's does; its
        `op: "rig"` entry in the log is the new baseline, so nothing is
        unsaved after it and undo stops there.

        200 with `file`, `point` (what was written), `running`, `library`
        (what git says of the file now) and, for the running rig, the
        switch's `how`, `why` and `seconds`. 404 for a point that is not
        there; 400 for a body of the wrong shape; 409 `busy`, `unsaved`,
        `panel`, `rig_problem`; 409 `restarts` with `why`, nothing written,
        when `"restart": false` was sent and the switch would restart Sushi
        (the page's "Restart?", #209: a revert is otherwise one tap, since the
        one Undo puts it back, but a restart cuts the sound); 409
        `not_switched` (or 504 if a command was
        abandoned) when the file was written and the rig could not be made to
        play it, `written: true` and the reason saying so.

        Not run against a live Sushi: shown against the fakes of the tests.
        """
        from . import saves

        try:
            point, discard, restart = revert_request(await self._json_body(request))
        except AgentError as exc:
            raise web.HTTPBadRequest(reason=str(exc))
        name = request.match_info["name"]
        path = self._rigfile_path(name)
        root = self._midi_root()
        self._refuse_while_busy()
        answer: dict[str, Any]
        running = False
        async with self._lock:
            self.busy = "revert"
            try:
                run = await self._rigfile_run()
                running = run["running"] is not None and run["running"].resolve() == path.resolve()
                try:
                    data, said = await asyncio.wait_for(
                        asyncio.to_thread(self._revert_point, root, name, path, point), SAVE_TIMEOUT)
                except saves.SaveNotFound as exc:
                    raise web.HTTPNotFound(reason=str(exc))
                except asyncio.TimeoutError:
                    raise web.HTTPGatewayTimeout(reason=f"{point} was not read within {SAVE_TIMEOUT:g}s")
                try:
                    before = await asyncio.to_thread(lambda: path.read_bytes() if path.exists() else None)
                    compiled = await self._rigfile_thread(
                        request, path, lambda: self._revert_compile(data, run, name if running else None))
                    discarded: list[dict[str, Any]] = []
                    if running:
                        async with self._live_lock:
                            refusal = revert_refusal(run["state"], self.log)
                            if refusal is not None and not discard:
                                raise RigFileRefused(*refusal)
                            if discard:
                                discarded = unsaved_entries(self.log, since=run["state"].get("started_at"))
                            if restart is False:
                                restarts = await asyncio.to_thread(self._would_restart, compiled, run, discarded)
                                if restarts:
                                    raise RigFileRefused(
                                        f"nothing was written: reverting {name} restarts Sushi: "
                                        + "; ".join(f"{r['code']}: {r['detail']}" for r in restarts)
                                        + '; revert with "restart": true to go ahead', "restarts",
                                        {"why": restarts})
                            await self._rigfile_thread(request, path, lambda: saves.restore(path, data))
                    else:
                        await self._rigfile_thread(request, path, lambda: saves.restore(path, data))
                except RigFileRefused as exc:
                    error = _conflict(" ".join(str(exc).split()), exc.code)
                    if exc.extra:
                        error.agent_extra = exc.extra
                    raise error
                shown = self._save_shown(path)
                self._history_file(path, before, data, f"revert {name}", run=run,
                                   apply="switch" if running else "none", act="revert")
                answer = {"ok": True, "rig": name, "file": shown, "point": said, "running": running,
                          "written": True, "discarded": bool(discard and running)}
                if not running:
                    answer["note"] = (f"{shown} is {said['what']} again; {name} is not the running rig, so it "
                                      "plays as reverted when it is next started or switched to")
                else:
                    answer.update(await self._revert_switch(request, name, said, discarded=discarded))
                    if discarded:
                        answer["discard"] = discard_said(discarded, answer)
            finally:
                self.busy = None
                if running:
                    await self._midi_sync()  # the switch stopped the listener; it follows the rig now
        await self._rigfile_git_after(answer, path)
        if answer["ok"]:
            return web.json_response(answer)
        return web.json_response(answer, status=504 if answer.get("abandoned") else 409)

    def _revert_point(self, root: Path, name: str, path: Path, point: str) -> tuple[bytes, dict[str, Any]]:
        """The bytes of revert point `point` and what it is, for the answer."""
        from . import saves

        match = saves.COMMIT_PATTERN.match(point)
        if match:
            sha = match.group(1)
            data = saves.commit_bytes(root, path, sha)
            return data, {"id": point, "kind": "commit", "sha": sha, "what": f"as commit {sha[:7]} has it"}
        data = saves.snapshot_bytes(root, name, point)
        kept, _ = saves.snapshots(root, name)
        meta = next((p for p in kept if p["id"] == point), {"id": point})
        return data, {"id": point, "kind": "save", "at": meta.get("at"),
                      "what": f"as the save of {meta.get('at') or point} wrote it"}

    def _revert_compile(self, data: bytes, run: dict[str, Any], playing: str | None = None) -> dict[str, Any]:
        """The point compiled as `up --rig` would compile it, or the refusal.
        `playing`, the running rig's name when the point is its file, is
        checked for plugins too (`_rigfile_plugins`): the switch that follows
        would refuse it, after the file was written."""
        import yaml

        try:
            raw = yaml.safe_load(data.decode("utf-8"))
        except (UnicodeDecodeError, yaml.YAMLError) as exc:
            raise RigFileRefused(" ".join(f"nothing was written: it is not YAML: {exc}".split()),
                                 "rig_problem") from exc
        if not isinstance(raw, dict):
            raise RigFileRefused("nothing was written: it is not a rig file", "rig_problem")
        compiled = self._rigfile_compile(raw, run)
        if playing is not None:
            self._rigfile_plugins(compiled, playing, run, "reverted", "revert it again")
        return compiled

    def _rigfile_plugins(self, compiled: dict[str, Any], name: str, run: dict[str, Any], done: str,
                         again: str) -> None:
        """`RigFileRefused` (`plugins_missing`) when the running rig `name`,
        about to be switched into `compiled` from its edited file (a revert,
        a channel load), loads a plugin plugins/ lacks: refused before the
        file is written, in the words a switch is refused in, since the
        switch after the write would refuse it and leave the file changed
        and the rig not (review of PR #198). Off the loop."""
        root = self._midi_root()
        refusal, _ = plugins_refusal(
            compiled, root, name, amp=False, folder=self.switching.plugins(root),
            words=(f"nothing was written: {name} was not {done}",
                   f"Nothing was changed: {name} plays on, and its file is as it was.", again))
        if refusal:
            raise RigFileRefused(" ".join(refusal.split()), "plugins_missing")

    async def _revert_switch(self, request, name: str, said: dict[str, Any], *,
                             mark: Callable[[dict[str, Any]], None] | None = None,
                             done: str = "reverted", discarded: list[dict[str, Any]] | None = None,
                             command: list[str] | None = None,
                             remove_tracks: tuple[str, ...] = (), amp_on: bool = False) -> dict[str, Any]:
        """The running rig into its reverted file, by the live switch, held
        under the lifecycle lock the caller holds. What it says, as a
        switch's answer does, and `ok: false` with `code: "not_switched"`
        when the rig was not made to play the file that is now written.
        A channel load (#208) comes this way too, with its own `mark` for
        the log entry and its own word for what was `done`, and a channel
        removed (#190), with the track it may take out live
        (`remove_tracks`, `rigfile.diff`'s), and an amp put on the file
        (#246: `amp_on`, an amp added or a channel loaded with its rig's amp),
        which the switch brings even into a run started with --no-amp."""
        if mark is None:
            def mark(entry: dict[str, Any]) -> None:
                entry["args"]["revert"] = said["id"]

        held: dict[str, Any] = {}
        if remove_tracks:
            held["remove_tracks"] = remove_tracks
        if amp_on:
            held["amp_on"] = True
        try:
            switched = await self._switch_held(request, name, None, mark, discarded, **held)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 -- the file is written whatever went wrong: said, with `written`
            # A refusal (`SwitchRefused`) or anything else the switch raised:
            # the file is reverted either way, and the answer must say so
            # rather than be a bare 500 (review, 2026-11-07).
            why = str(exc) if isinstance(exc, SwitchRefused) else f"{type(exc).__name__}: {exc}"
            reason = " ".join((f"{name}'s file was {done} ({said['what']}), but the rig was not switched to it: "
                               f"{why}. It keeps playing as it was, unless the switch got part-way; restart it to play "
                               "the file as it is now").split())
            self._remember(command or ["revert", name], reason, kind="lifecycle")
            return {"ok": False, "code": "not_switched", "reason": reason, "written": True}
        out = {k: switched.get(k) for k in ("how", "why", "seconds", "plan", "applied", "counts", "entry",
                                            "state", "take", "abandoned", "half_switched", "warnings")
               if k in switched}
        if not switched.get("ok"):
            reason = " ".join((f"{name}'s file was {done} ({said['what']}), but the rig did not come up "
                               f"playing it: {switched.get('reason')}").split())
            # In /health as what was asked (a revert, a load), over the
            # restart's own failure, which is said within it.
            self._remember(command or ["revert", name], reason, kind="lifecycle")
            return {**out, "ok": False, "code": "not_switched", "reason": reason}
        return out

    # ======================================================================= #
    # (end of revert to a previous save)
    # ======================================================================= #

    # ======================================================================= #
    # One Undo (#209): GET /undo, POST /undo
    # ======================================================================= #
    #
    # Kept in one block. The record is history.py's (pure: the entries, the
    # depth, a fader's drag as one step); here is the putting back. Outside
    # it the agent calls in at: __init__ (`history`, `_undo_lock`), `app`
    # (`_undo_routes`), `_record` (`_history_follows`: every live change,
    # and every switch, which ends the live ones), and each route that
    # writes a rig file or the bin, once its write is done (`_history_file`,
    # or `history.push` for a delete and a Save as).
    #
    # A new kind of action is easy to add: record it where it happens. A
    # write of a rig file needs no method of its own:
    # `self._history_file(path, before, after, label, run=run, apply=...)`
    # once the file is written, `apply` saying what the write did to the
    # running rig ("switch" for one then switched into the file, as a revert
    # and a channel load are; "restart" for one that waits for a restart;
    # "bindings" for a pedal's; "none" otherwise). Anything else is a `kind`
    # with its reversing method named in `_UNDOERS`.
    #
    # Locks, in the agent's one order: `_undo_lock` (one Undo at a time),
    # then what the step takes: the live undo its own; a file's the
    # lifecycle lock (`busy: "undo"`) and, for the running rig's file, the
    # live lock, as an edit of it does.

    _UNDOERS = {"live": "_undo_live_step", "file": "_undo_file_step", "bin": "_undo_bin_step"}

    def _undo_routes(self, app) -> None:
        app.router.add_get("/undo", self.undo_state_route)
        app.router.add_post("/undo", self.undo_route)

    def _history_follows(self, entry: dict[str, Any], index: int) -> None:
        """The history after live log entry `entry` (at `index`) was
        recorded: a change is a step; an undo or a skip (by either route)
        takes its target out; a switch ends the live steps."""
        op = entry.get("op")
        if op == "rig":
            self.history.drop_live()
        elif op == "undo":
            self.history.forget_index(entry["undoes"])
        elif op == "stale":
            self.history.forget_index(entry["stale"])
        else:
            self.history.push_live(index, entry)

    def _history_file(self, path: Path, before: bytes | None, after: bytes | None, label: str, *,
                      run: dict[str, Any] | None = None, apply: str = "none", **extra: Any) -> None:
        """Record a write of rig file `path`: `before`, its bytes before (None
        when there was no file), `after`, what was written. A write that
        changed nothing is not a step."""
        if before == after:
            return
        state = (run or {}).get("state") or {}
        self.history.push("file", label, path=str(path), rig=Path(path).stem, before=before,
                          after=history_module.digest(after), apply=apply,
                          run={k: state.get(k) for k in ("started_at", "config", "rig")}, **extra)

    async def undo_state_route(self, request):
        """`GET /undo`: what the next press of Undo would step back (`next`:
        `label`, `kind`, `op` for a live one, `act` for another (what was
        done: `channel`, `input`, `pedal`, `save`, `save_as`, `revert`,
        `new_rig`, `delete`, `load`, `remove_channel`, `add_amp`; the page's lock reads it), `can` and `why` when it cannot
        be), `depth` (steps held) and `limit`. `next` is null with nothing
        to undo. A live step's `can` is true until it is tried: whether the
        rig is still as the change left it is Sushi's to say."""
        return web.json_response({"ok": True, **(await self._undo_next())})

    async def _undo_next(self) -> dict[str, Any]:
        entry = self.history.newest()
        out: dict[str, Any] = {"depth": len(self.history.entries), "limit": self.history.limit,
                               "kept": "until the agent restarts"}
        if entry is None:
            return {**out, "next": None}
        shown = history_module.shown(entry)
        why = await asyncio.to_thread(self._undo_blocked, entry)
        shown.update(can=why is None, why=why)
        return {**out, "next": shown}

    def _undo_blocked(self, entry: dict[str, Any]) -> str | None:
        """Why `entry` cannot be put back, found without changing anything, or
        None. Off the loop: a file is read."""
        from . import rigbin

        kind = entry["kind"]
        if kind == "none":
            return entry.get("why") or "it cannot be undone"
        if kind not in self._UNDOERS:
            return f"nothing undoes a {kind}"
        if kind == "file":
            path = Path(entry["path"])
            try:
                now = path.read_bytes() if path.exists() else None
            except OSError as exc:
                return f"{path.name} could not be read: {type(exc).__name__}: {exc}"
            if history_module.digest(now) != entry["after"]:
                return f"{path.name} has changed since"
        if kind == "bin":
            root = self._midi_root()
            if rigbin.rig_path(root, entry["rig"]).exists():
                return f"a rig called {entry['rig']} is there again"
            if not (rigbin.bin_dir(root) / entry["entry"]).exists():
                return f"{entry['entry']} is no longer in the bin"
        return None

    def _cant_undo(self, entry: dict[str, Any], why: str):
        """Drop `entry`, so the next press goes on to the one before, and
        answer 409 `cant_undo` with it (`skipped`) and why."""
        self.history.drop(entry)
        error = _conflict(" ".join(f"can't undo {entry['label']}: {why}".split()), "cant_undo")
        error.agent_extra = {"skipped": history_module.shown(entry)}
        return error

    async def undo_route(self, request):
        """`POST /undo {"seq"?, "discard"?, "restart"?}` (`one_undo_request`):
        step back the newest change in the history, whatever it was, by the
        step its kind takes (`_UNDOERS`).

        200 with `undone` (the step), `said` ("Undone: remove delay", or
        "Partly undone: ..." for a drag of which some could not be put back),
        `how` (`live`; `file`, the file put back and nothing to play;
        `restart`, with `offer: "restart"`, where the rig plays the change
        until it restarts: the forward edit waited for one, or the rig has
        restarted or switched since the write, so it plays what was written;
        `next start`, another rig's file; `switched`, the running rig
        switched back into its file, as a revert is; `restored`, a rig back
        from the bin), and `next`, what the press after would do.

        409 `nothing_to_undo`; 409 `changed` when `seq` is not the newest
        step's (another page, or a change since, came first: `next` says what
        is newest now); 409 `cant_undo` when the step cannot be put back (the
        file changed since, the bin entry gone, a value moved since from
        elsewhere, a live change too old for the live log, a Save as): the
        step is dropped and `skipped` says which, so the next press goes on.
        409 `unsaved` or `panel` when a switch back would drop live work no
        save holds, as a revert is refused (answer with `"discard": true`),
        and `restarts` when it would restart Sushi and `"restart": false`
        was sent (`why` says why; answer with `"restart": true`): the step
        stays. 409 `busy`, and what Sushi or a write answers (502, 503,
        504), leave the step where it is, to be tried again."""
        try:
            asked = one_undo_request(await self._json_body(request))
        except AgentError as exc:
            raise web.HTTPBadRequest(reason=str(exc))
        self._refuse_while_busy()
        async with self._undo_lock:
            entry = self.history.newest()
            if asked["seq"] is not None and (entry is None or entry["seq"] != asked["seq"]):
                newest = f"the newest is now {entry['label']}" if entry is not None else "there is nothing to undo now"
                error = _conflict(f"the step shown is no longer the newest ({newest}): press Undo again", "changed")
                error.agent_extra = await self._undo_next()
                raise error
            if entry is None:
                raise _conflict("nothing to undo", "nothing_to_undo")
            why = await asyncio.to_thread(self._undo_blocked, entry)
            said: dict[str, Any] = {}
            if why is None:
                try:
                    said = await getattr(self, self._UNDOERS[entry["kind"]])(request, entry, asked)
                except CantUndo as exc:
                    why = str(exc)
                except UndoHeld as exc:
                    error = _conflict(" ".join(f"{entry['label']} is not undone yet: {exc}".split()), exc.code)
                    error.agent_extra = {"step": history_module.shown(entry), **exc.extra}
                    raise error
            if why is not None:
                error = self._cant_undo(entry, why)
                error.agent_extra.update(await self._undo_next())  # what the next press would do, for the button
                raise error
            self.history.drop(entry)
            partial = said.pop("partial", False)
            answer = {"ok": True, "undone": history_module.shown(entry),
                      "said": f"{'Partly undone' if partial else 'Undone'}: {entry['label']}", **said}
            answer.update(await self._undo_next())
            return web.json_response(answer)

    async def _undo_live_step(self, request, entry: dict[str, Any], asked: dict[str, Any]) -> dict[str, Any]:
        """A live step: each of its log entries, newest first, by the live
        undo (`_live_undo`), which checks each is still as it was left, and
        is told which entry it is for (`expect`), so a change landing between
        the pick here and its own is refused (`changed`), not undone in its
        place. One that cannot be is skipped as `{"skip": true}` skips it,
        and the rest are tried; `CantUndo` when none could be, or when the
        live log no longer holds them (trimmed past its limit, or undone
        another way): never a silent step on to an older one. A refusal that
        may pass (busy, Sushi not answering) is raised as it is, the rest
        kept. `partial` when some of a drag was put back and some was not."""
        undone, refused = 0, []
        while entry["indices"] and self.history.newest() is entry:
            try:
                picked = pick_undo(self.log, self._trimmed)
            except UndoBarrier as exc:
                raise CantUndo(str(exc)) from exc
            if picked is None or picked[0] not in entry["indices"]:
                break  # no longer the live log's newest: too old, or undone another way
            try:
                response = await self._live_undo(request, False, expect=picked[0])
            except web.HTTPException as exc:
                if not (exc.status == 400 or getattr(exc, "agent_code", None) == "stale"):
                    raise
                refused.append(exc.reason)
                await self._live_undo(request, True, expect=picked[0])  # given up on: the next is the one before
                continue
            if response.status >= 400:
                import json

                reason = (json.loads(response.text) or {}).get("reason") or f"{response.status}"
                raise web.HTTPBadGateway(reason=reason)
            undone += 1
        if undone:
            return {"how": "live", "partial": bool(refused or entry["indices"])}
        if refused:
            raise CantUndo(refused[0].split("; to give up on")[0])
        raise CantUndo("too old: the live log no longer holds it")

    async def _undo_bin_step(self, request, entry: dict[str, Any], asked: dict[str, Any]) -> dict[str, Any]:
        """A delete: the rig back from the bin, as `POST /rigs/{name}/restore`
        puts it back."""
        from . import rigbin

        root = self._midi_root()
        name = entry["rig"]
        path = rigbin.rig_path(root, name)

        def work() -> None:
            try:
                rigbin.restore(root, name, entry["entry"])
            except rigbin.BinRefused as exc:
                raise CantUndo(str(exc)) from exc

        self._refuse_while_busy()
        async with self._lock:
            self.busy = "undo"
            try:
                await self._rigfile_thread(request, path, work)
            finally:
                self.busy = None
        return {"how": "restored", "rig": name}

    async def _undo_file_step(self, request, entry: dict[str, Any], asked: dict[str, Any]) -> dict[str, Any]:
        """A write of a rig file: its bytes before put back, whole, while it
        is still as the write left it (a file that was new goes into the
        bin instead, rigbin.py), then what the write did to the running rig
        done again (`apply`, see the block's head), as the rig is now:

        - `switch`: the running rig switched into the file put back, as a
          revert is (`_revert_switch`), and the pedals follow it after
          (`_midi_sync`), as a revert's do. Held as a revert is held: live
          work no save holds is not dropped without `discard` (`UndoHeld`,
          `unsaved`), and with `"restart": false` a switch that would
          restart Sushi is asked about first (`restarts`).
        - `bindings`: the pedal map follows the file live where the
          assignment's would (`_undo_bindings`: the agent's bindings, and
          Sushi's mappings where only they differ, #210), else `restart`.
        - `restart`: nothing to do while the rig runs as it did when the
          write was made (it never played it: the file is what plays again).
        - `none`: a Save's (the file is what played before; the save's log
          entry is withdrawn, so what it wrote counts as unsaved again), or
          another rig's file (`next start`).

        Decided at the Undo, not the write: a file that is the running rig's
        now, in a run other than the write's (restarted, or switched to
        since), plays what was written, so putting it back waits for a
        restart, offered, whatever the write itself did."""
        from . import rigbin, saves

        path = Path(entry["path"])
        name = path.stem
        root = self._midi_root()
        self._refuse_while_busy()
        switched_at_all = False
        async with self._lock:
            self.busy = "undo"
            try:
                run = await self._rigfile_run()
                running = run["running"] is not None and run["running"].resolve() == path.resolve()
                same_run = not run_changed(entry.get("run") or {}, run["state"])
                apply = entry.get("apply") or "none"
                if running and not same_run and apply in ("none", "restart"):
                    apply = "restart"  # the run now was started from what was written

                def put_back() -> None:
                    now = path.read_bytes() if path.exists() else None
                    if history_module.digest(now) != entry["after"]:
                        raise CantUndo(f"{path.name} has changed since")
                    if entry["before"] is None:
                        if running:
                            raise CantUndo(f"{name} is the running rig")
                        try:
                            rigbin.move_to_bin(root, name)
                        except rigbin.BinRefused as exc:
                            raise CantUndo(str(exc)) from exc
                    else:
                        saves.restore(path, entry["before"])

                if not running:
                    await self._rigfile_thread(request, path, put_back)
                    return {"how": "next start", "rig": name}
                if apply == "switch":
                    try:
                        compiled = await self._rigfile_thread(
                            request, path, lambda: self._revert_compile(entry["before"], run, name))
                    except RigFileRefused as exc:
                        raise CantUndo(str(exc)) from exc
                    async with self._live_lock:
                        refusal = revert_refusal(run["state"], self.log)
                        if refusal is not None and not asked["discard"]:
                            raise UndoHeld(refusal[0].replace("a revert replaces", "undoing it replaces")
                                           .replace("revert with discard", "undo with discard"), refusal[1])
                        discarded = (unsaved_entries(self.log, since=run["state"].get("started_at"))
                                     if asked["discard"] else [])
                        if asked["restart"] is False:
                            restarts = await asyncio.to_thread(self._would_restart, compiled, run, discarded)
                            if restarts:
                                raise UndoHeld(f"switching {name} back restarts Sushi: "
                                               + "; ".join(f"{r['code']}: {r['detail']}" for r in restarts),
                                               "restarts", {"why": restarts})
                        await self._rigfile_thread(request, path, put_back)

                    def mark(logged: dict[str, Any]) -> None:
                        logged["args"]["undo"] = entry["label"]

                    switched_at_all = True
                    switched = await self._revert_switch(
                        request, name, {"id": "undo", "what": "as it was before"}, mark=mark, done="put back",
                        discarded=discarded, command=["undo", name])
                    if not switched.get("ok", True):
                        # The file is back whatever the switch did: a restart plays it.
                        return {"how": "restart", "offer": "restart", "rig": name, "reason": switched.get("reason")}
                    return {"how": "switched", "rig": name,
                            **{k: switched[k] for k in ("why", "seconds") if k in switched},
                            **({"discard": discard_said(discarded, switched)} if discarded else {})}
                async with self._live_lock:
                    await self._rigfile_thread(request, path, put_back)
                    if apply == "none" and entry.get("save_entry") is not None and same_run:
                        self._withdraw_save(entry)
                if apply == "bindings":
                    return await self._undo_bindings(request, path, run)
                if apply == "restart" and not same_run:
                    return {"how": "restart", "offer": "restart", "rig": name}
                return {"how": "file", "rig": name}
            finally:
                self.busy = None
                if switched_at_all:
                    await self._midi_sync()  # the switch stopped the listener; it follows the rig now

    def _would_restart(self, compiled: dict[str, Any], run: dict[str, Any],
                       discarded: list[dict[str, Any]] | None = None,
                       remove_tracks: tuple[str, ...] = ()) -> list[dict[str, str]]:
        """Why a switch of the running rig into `compiled` would restart
        Sushi rather than switch live, as `_switch_held` and `_switch_live`
        decide it, or [] for a live one: `rigfile.diff` of the two configs,
        the state's blockers, and the unsaved work `discarded` drops
        (`discard_plan`'s rule, re-review of PR #235): a plugin it added,
        removed or moved leaves chains the running config lacks, which the
        live plan refuses as `drifted`, and a value whose before Sushi did
        not report cannot be put back live. Off the loop.

        Not known before the switch is tried, so not said: a chain moved from
        outside the agent (the panel), which the plan reads from Sushi, and a
        live switch that fails part-way. Both still restart."""
        state = run["state"]
        reasons = [r.as_dict() for r in state_blockers(state)]
        try:
            running = self.switching.read_config(run["described"].get("config"))
            # The running rig's own file, so a run with no amp keeps none
            # (`keeps_no_amp`: `amp_blockers` says nothing for it); a Carla
            # that should run and is not, or is not wired, is a restart
            # (#246), asked first as any is. The wiring as `_rigfile_run`
            # looked at it: this runs off the loop. From a running config
            # with no amp, `rigfile.diff` says `amp` itself.
            if ((running or {}).get("_amp") or {}).get("model"):
                reasons += [r.as_dict() for r in amp_blockers(
                    state, run["described"], compiled, run.get("wiring"),
                    amp_off=state.get("amp_requested") is False)]
            decided = rigfile.diff(running, compiled, root=self._amp_root(),
                                   amp_off=state.get("amp_requested") is False,
                                   remove_tracks=self._removable(remove_tracks))
        except Exception as exc:  # noqa: BLE001 -- unreadable is a restart, as the switch says it
            return reasons + [{"code": "unreadable", "detail": f"the running rig's compiled config could not be "
                                                               f"read: {type(exc).__name__}: {exc}"}]
        if isinstance(decided, rigfile.NeedsRestart):
            reasons += [r.as_dict() for r in decided.reasons]
        elif discarded:
            _, _, structural = _touched(discarded)
            if structural:
                reasons.append({"code": "discarded", "detail": (
                    "the unsaved work being dropped added, removed or moved a plugin, which cannot be put "
                    "back live")})
            else:
                # The values to put back, by the switch's own rule; with no
                # reading of Sushi each is a step, which is all that is asked.
                planned = discard_plan(decided, discarded, compiled, {})
                if isinstance(planned, rigfile.NeedsRestart):
                    reasons += [r.as_dict() for r in planned.reasons]
        return reasons

    def _withdraw_save(self, entry: dict[str, Any]) -> None:
        """A Save's Undo, the file put back: its log entry is no baseline any
        more (`since_baseline`), and the chain edits it cut from the chain
        log are given back, so the next save folds them in again."""
        saved = entry["save_entry"]
        saved["withdrawn"] = True
        if any(e is saved for e in self._chain_log):
            after = [e for e in self._chain_log[next(i for i, e in enumerate(self._chain_log) if e is saved) + 1:]]
            self._chain_log = list(entry.get("chain_before") or []) + after

    async def _undo_bindings(self, request, path: Path, run: dict[str, Any]) -> dict[str, Any]:
        """The running rig's pedal map after its file was put back, by the
        path an assignment takes (`_map_plan`): the agent's bindings live
        where they can be, and Sushi's mappings live too where only they
        differ (#210, `_map_cc_live`: a pedal a sweep was made live for is
        taken off it live), else the answer offers a restart."""
        import yaml

        state, outcome = run["state"], {}

        def work() -> dict[str, Any]:
            try:
                compiled = self._rigfile_compile(yaml.safe_load(path.read_text()), run)
                running = self.switching.read_config(run["described"].get("config"))
            except Exception as exc:  # noqa: BLE001 -- the file is back; the restart plays it
                return {"not_live": [{"code": "unreadable", "detail": f"{type(exc).__name__}: {exc}"}], "cc_plan": None}
            not_live, cc_plan = self._map_plan(running, compiled,
                                               live_edits(self._chain_log, since=state.get("started_at")))
            if not not_live:
                unrecorded = self._record_running(path, state, outcome, running_with_map(running, compiled, midi=False))
                not_live = [unrecorded] if unrecorded else []
            return {"not_live": not_live, "cc_plan": cc_plan, "running": running, "compiled": compiled}

        async with self._live_lock:
            done = await self._rigfile_thread(request, path, work)
        not_live = done["not_live"]
        if done["cc_plan"] is not None and not outcome.get("stale"):
            # Outside the live lock: `_midi_cc_live` takes it itself.
            not_live = await self._map_cc_live(request, path, state, outcome, done["running"], done["compiled"],
                                               done["cc_plan"], not_live)
        if not_live:
            return {"how": "restart", "offer": "restart", "rig": path.stem, "why": not_live}
        await self._midi_sync()
        return {"how": "live", "rig": path.stem}

    # ======================================================================= #
    # (end of One Undo)
    # ======================================================================= #

    # ======================================================================= #
    # A channel from another rig (#208): POST /rig/channels/{channel}/load
    # ======================================================================= #
    #
    # Kept in one block, as save's and the rig files' are. Outside it the agent
    # calls in at `app` (`_channel_load_routes`). What a load does to a chain
    # and a MIDI map is channelcopy.py's, pure; what a rig may be is still the
    # compiler's. The source is a channel of a rig in config/rigs/, read from
    # its file as it stands, as "start from" reads one (`_rigfile_copy`).

    def _channel_load_routes(self, app) -> None:
        app.router.add_post("/rig/channels/{channel}/load", self.load_channel_route)

    async def load_channel_route(self, request):
        """`POST /rig/channels/{channel}/load {"from": {"rig", "channel"},
        "rig"?, "discard"?, "drop_midi"?, "with_amp"?}`: load channel `from.channel` of rig
        `from.rig` (config/rigs/) onto the running rig's channel `channel`:
        its chain replaced by the source's, settings and bypass as that file
        has them, each plugin named for the channel
        (`channelcopy.processor_name`), and its level set to the source's;
        its input and width stay its own, and the source rig's MIDI map is
        not copied. Nor its amp, unless `"with_amp": true` (#246): then the
        source rig's `amp:`, model and parameters, is written into a running
        rig whose file has none, in the same write, and the switch into it is
        a restart that brings the amp (refused as `no_amp` from a rig with
        no amp, `has_amp` onto one with an amp already).

        The rig file is written first, in place with `write_rig` (comments
        kept), and the running rig is then switched to it by the live switch
        (#112), as a revert is: live where `rigfile.diff` says it can be
        (plugins removed, added and moved, parameters and bypasses set), by
        restart where it must be, the answer saying which and why; its `op:
        "rig"` entry is the new baseline, so nothing is unsaved after it and
        undo stops there. The file as it was is kept as a revert point
        (`saves.keep`) unless a save already keeps it, so Revert… goes back.

        Refused before anything is written, each a 409 with its reason:
        `no_rig`, `panel`, `v1` (as a save); `changed`, when `rig` is given
        and is not the running rig (the page sends the rig it drew); `no_source` (no such rig in
        config/rigs/, no such channel in it, or a file that will not read);
        `no_channel`; `name` (a name a load makes that the naming rule
        refuses); `midi`, when a mapping of the rig's MIDI map would point at
        nothing (`mappings`: each, with what it was and why), unless
        `"drop_midi": true`, which drops them and says so (`dropped`), a
        mapping onto the same plugin under its new name following it
        (`followed`); `rig_problem`, the compiler's own words (a name another
        channel has); `plugins_missing` (#188's check, a plugin plugins/
        lacks); and `unsaved` while the rig holds live work no save has
        written, unless `"discard": true`.

        200 with `file`, `from`, `channel`, `how`, `why`, `names` (each base
        name and what it is called here), `followed`, `dropped`, `amp`
        (`channelcopy.amp_note`), `amp_copied` (`{"from", "model",
        "parameters"}` for a load with amp, else null), `width`,
        `kept_before`, `library`. 409
        `not_switched` (or 504) when the file was written and the rig could
        not be made to play it, `written: true`.

        Not run against a live Sushi: shown against the fakes of the tests."""
        body = await self._json_body(request)
        try:
            source, discard, drop_midi, meant = channel_load_request(body)
        except AgentError as exc:
            raise web.HTTPBadRequest(reason=str(exc))
        with_amp = load_with_amp(body)
        channel = request.match_info["channel"]
        name = f"{source['rig']} / {source['channel']}"
        self._refuse_while_busy()
        source_git = await self._sources_git_before([source])
        answer: dict[str, Any]
        path: Path | None = None
        async with self._lock:
            self.busy = "channel load"
            try:
                run = await self._rigfile_run()
                refusal = save_refusal(run["state"], run["described"])
                if refusal is not None:
                    raise _conflict(*refusal)
                path = run["running"]
                if meant is not None and path.stem != meant:
                    raise _conflict(f"the running rig is {path.stem}, not {meant}, so nothing was loaded: look at "
                                    "the rig and load again", "changed")
                try:
                    planned = await self._rigfile_thread(
                        request, path, lambda: self._plan_load(path, source, channel, drop_midi, run, with_amp))
                    async with self._live_lock:
                        refusal = load_refusal(run["state"], self.log)
                        if refusal is not None and not discard:
                            raise RigFileRefused(*refusal)
                        dropped_work = refusal is not None
                        discarded = (unsaved_entries(self.log, since=run["state"].get("started_at"))
                                     if dropped_work else [])
                        kept = await self._rigfile_thread(request, path, lambda: self._write_load(path, planned))
                except RigFileRefused as exc:
                    error = _conflict(" ".join(str(exc).split()), exc.code)
                    if exc.extra:
                        error.agent_extra = exc.extra
                    raise error
                # One Undo (#209): the file put back and the rig switched into
                # it, as a revert's is, whatever the switch below does.
                self._history_file(path, planned["before"], planned["after"],
                                   f"load {name}{' with amp' if with_amp else ''}", run=run,
                                   apply="switch", act="load")
                shown = self._save_shown(path)
                answer = {
                    "ok": True, "rig": path.stem, "file": shown, "from": planned["from"], "channel": channel,
                    "written": True, "discarded": dropped_work,
                    "names": planned["names"], "followed": planned["followed"], "dropped": planned["dropped"],
                    "amp": planned["amp"], "amp_copied": planned["amp_copied"], "width": planned["width"],
                    "kept_before": kept,
                    **({"provenance_note": "; ".join(planned["provenance_problems"])}
                       if planned.get("provenance_problems") else {}),
                }
                said = {"id": f"load:{name}", "what": f"with {name} loaded onto {channel}"}

                def mark(entry: dict[str, Any]) -> None:
                    entry["args"]["load"] = {"rig": source["rig"], "channel": source["channel"], "onto": channel,
                                             **({"with_amp": True} if with_amp else {})}

                # With the amp (#246), the switch is a restart (Carla comes
                # with the rig), and brings the amp even into a run started
                # with --no-amp (`amp_on`).
                answer.update(await self._revert_switch(request, path.stem, said, mark=mark,
                                                        done="written", discarded=discarded,
                                                        command=["load", f"{name} onto {path.stem}'s {channel}"],
                                                        amp_on=with_amp))
                if discarded:
                    answer["discard"] = discard_said(discarded, answer)
                if not answer.get("ok"):
                    # The file is the load's and the rig may not be: the
                    # pedals are served from the compiled config the state
                    # file names, which is what is running, never the file.
                    answer["midi_note"] = (
                        "the pedals' bindings follow the rig as it runs (its compiled config, "
                        f"{run['described'].get('config')}), not the file just written: restart the rig to "
                        "play the file, and its pedals with it")
            finally:
                self.busy = None
                if path is not None:
                    await self._midi_sync()  # the switch stopped the listener; it follows the rig now
        await self._rigfile_git_after(answer, path)
        self._sources_git_fill([answer.get("from")], source_git)
        if answer["ok"]:
            return web.json_response(answer)
        return web.json_response(answer, status=504 if answer.get("abandoned") else 409)

    def _read_source(self, rig: str, channel: str) -> tuple[Any, Path, Any]:
        """Channel `channel` of rig `rig` of config/rigs/, as its file is now
        (`channelcopy.source_of`), that file and its document; or the refusal,
        `no_source`. The rig is looked up among config/rigs/' names, never
        made a path as given."""
        import yaml

        from . import channelcopy

        root = self._midi_root()
        if rig not in midi_listener.program_order(root):
            raise RigFileRefused(f"there is no rig called {rig!r} in config/rigs/", "no_source")
        path = root / "config" / "rigs" / f"{rig}.yaml"
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
            return channelcopy.source_of(raw, rig, channel), path, raw
        except LookupError as exc:
            raise RigFileRefused(str(exc), "no_source") from exc
        except Exception as exc:  # noqa: BLE001 -- a source that will not read is said
            raise RigFileRefused(f"{path.name} could not be read: {type(exc).__name__}: {exc}", "no_source") from exc

    def _plan_load(self, path: Path, source_ref: dict[str, str], channel: str, drop_midi: bool,
                   run: dict[str, Any], with_amp: bool = False) -> dict[str, Any]:
        """What loading `source_ref` onto `channel` writes, decided and
        compiled before anything is written, or the refusal
        (`RigFileRefused`). Off the loop.

        `with_amp` (#246): the source rig's `amp:` too, model and parameters
        as its file has them (`channelcopy.rig_amp`), written into a running
        rig whose file has none (`rigedit.with_amp`). Refused, nothing
        written, as `no_amp` when the source rig has no amp and `has_amp`
        when the running rig's file has one."""
        import yaml

        from . import channelcopy, rigedit

        source, source_path, source_raw = self._read_source(source_ref["rig"], source_ref["channel"])
        name = source.name
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise RigFileRefused(f"{path.name} is not a rig file", "rig_problem")
        channels = raw.get("channels") if isinstance(raw.get("channels"), list) else []
        index = next((i for i, c in enumerate(channels) if isinstance(c, dict) and c.get("name") == channel), None)
        if index is None:
            names = [c.get("name") for c in channels if isinstance(c, dict)]
            raise RigFileRefused(f"{path.stem} has no channel called {channel} (it has: "
                                 f"{', '.join(map(str, names)) or 'none'})", "no_channel")
        old = channels[index]
        entry = channelcopy.loaded_channel(old, source)
        bad = channelcopy.name_problems(channel, source)
        if bad:
            raise RigFileRefused(f"nothing was written: {'; '.join(bad)}", "name")
        copied_amp = None
        if with_amp:
            copied_amp = channelcopy.rig_amp(source_raw)
            if copied_amp is None:
                raise RigFileRefused(f"nothing was written: {source.rig} has no amp to load with {name}: "
                                     "load it without", "no_amp")
            if rigedit.has_amp(raw):
                raise RigFileRefused(f"nothing was written: {path.stem} has an amp already (its file's amp:), "
                                     f"and a load brings {source.rig}'s only onto a rig with none: load it "
                                     "without", "has_amp")
        edited = copy.deepcopy(raw)
        edited["channels"][index] = entry
        if copied_amp is not None:
            edited = rigedit.with_amp(edited, copied_amp)
        midi, followed, orphaned = channelcopy.midi_after_load(raw.get("midi"), channel, old.get("chain") or [],
                                                               entry["chain"])
        dropped: list[dict[str, Any]] = []
        if "midi" in raw:
            edited["midi"] = midi
        if orphaned:
            if not drop_midi:
                count = f"{len(orphaned)} MIDI mapping{'s' if len(orphaned) != 1 else ''}"
                raise RigFileRefused(
                    f"nothing was written: loading {name} onto {channel} would leave {count} of {path.stem}'s MIDI "
                    f"map pointing at nothing: {'; '.join(channelcopy.mapping_said(o) for o in orphaned)}. Load it "
                    'dropping them ({"drop_midi": true}), or edit the midi: section by hand first',
                    "midi", {"mappings": orphaned})
            edited["midi"] = channelcopy.drop_orphaned(midi, orphaned)
            dropped = orphaned
        compiled = self._rigfile_compile(edited, run)
        self._rigfile_plugins(compiled, path.stem, run, f"given {name} on {channel}", "load it again")
        # The amp in front of the channel once loaded: the one copied, which
        # the switch brings, or the rig's own as it runs.
        rig_amp = copied_amp["model"] if copied_amp is not None else running_amp(run["state"], raw)
        width = None
        if source.width != old.get("width"):
            width = (f"{name} is {source.width}; {channel} stays {old.get('width')}, as its input "
                     f"({old.get('input')}) is")
        return {
            "raw": edited, "source": source,
            "names": {e["name"]: n["name"] for e, n in zip(source.chain, entry["chain"])},
            "followed": followed, "dropped": dropped, "width": width,
            "amp": channelcopy.amp_note(source, channel, str(old.get("input")), rig_amp),
            "amp_copied": ({"from": source.rig, "model": copied_amp["model"],
                            "parameters": copy.deepcopy(copied_amp.get("parameters"))}
                           if copied_amp is not None else None),
            "from": {"rig": source.rig, "channel": source.channel, "file": self._save_shown(source_path),
                     "plugins": len(source.chain), "git": None},  # read before the lock
            "name": name, "channel": channel,
        }

    def _write_load(self, path: Path, planned: dict[str, Any]) -> dict[str, Any] | None:
        """Write a planned load into the rig file, and keep the file as it was
        for Revert… when no save keeps it already, only once the write has
        happened (review of #162: a point kept before a write that then
        refused was a revert point for a load that never was); what was
        kept, or None. Off the loop, under the live lock."""
        from . import saves

        root = self._midi_root()
        rig = path.stem
        before = path.read_bytes()
        kept, _ = saves.snapshots(root, rig)
        kept_as = saves.kept_as(before, kept, lambda sid: saves.snapshot_bytes(root, rig, sid))
        source = planned["source"]
        try:
            planned["provenance_problems"] = rigfile.write_rig(
                path, planned["raw"], replace_chains=(planned["channel"],),
                provenance={planned["channel"]: self._provenance(source.rig, source.channel)})
        except rigfile.RigFileError as exc:
            raise RigFileRefused("nothing was written: " + "; ".join(exc.problems), "rig_problem") from exc
        planned["before"], planned["after"] = before, path.read_bytes()  # for the one Undo (#209)
        return self._keep_for_revert(path, before, kept_as,
                                     f"before loading {planned['name']} onto {planned['channel']}", "the load")

    def _keep_for_revert(self, path: Path, before: bytes, kept_as: str | None, how: str,
                         what: str) -> dict[str, Any]:
        """Keep `before`, rig file `path` as it was before `what` was written
        over it, as a revert point (`saves.keep`), unless a save keeps those
        bytes already (`kept_as`); what was kept. A point that could not be
        kept is said, never raised: the write is done. Off the loop."""
        from . import saves

        if kept_as is not None:
            return {"id": kept_as, "how": "kept by that save already"}
        try:
            meta = saves.keep(self._midi_root(), path.stem, before, {"file": self._save_shown(path), "how": how})
        except Exception as exc:  # noqa: BLE001 -- written; said, as a save's lost snapshot is
            why = " ".join(f"{what} was written, but the file as it was could not be kept for Revert: "
                           f"{type(exc).__name__}: {exc}".split())
            print(f"sushi-rig agent: {why}", file=sys.stderr, flush=True)
            return {"kept": False, "how": how, "why": why}
        return {"id": meta["id"], "at": meta["at"], "how": meta["how"]}

    def _provenance(self, rig: str, channel: str) -> str:
        """The provenance comment a load leaves above the channel's chain in
        the rig file (`rigfile.provenance_line`)."""
        return rigfile.provenance_line(rig, channel, f"config/rigs/{rig}.yaml",
                                       datetime.now(timezone.utc).date().isoformat())

    # ======================================================================= #
    # (end of a channel from another rig)
    # ======================================================================= #

    # ======================================================================= #
    # A channel removed from the running rig (#190):
    # POST /rig/channels/{channel}/remove
    # ======================================================================= #
    #
    # Kept in one block, as a load's is, and made as a load is made: the rig
    # file written in place (comments kept), then the running rig switched
    # into it (`_revert_switch`), live where the switch can take the track
    # out (`rigfile.diff`'s `remove_tracks`, `Live.remove_track`, which the
    # agent's own Sushi link does not offer: `Live.at`), by restart where it
    # cannot. Outside it the agent calls in at `app`
    # (`_channel_remove_routes`). What goes, and what in the MIDI map names
    # the channel, is rigedit.py's, pure; what a rig may be is the compiler's.
    # Locks as a load's: lifecycle (`busy: "channel remove"`), then live.

    def _channel_remove_routes(self, app) -> None:
        app.router.add_post("/rig/channels/{channel}/remove", self.remove_channel_route)

    async def remove_channel_route(self, request):
        """`POST /rig/channels/{channel}/remove {"rig"?, "discard"?,
        "drop_midi"?, "restart"?}` (`rigedit.remove_request`): take channel
        `channel` out of the running rig, its chain and its send with it, and
        every control of the rig's MIDI map that names it once that is said
        yes to (`drop_midi`).

        The rig file is written first, in place with `write_rig` (comments
        kept), compiled before it is (the compiler's refusal, nothing
        written), and the file as it was is kept as a revert point
        (`saves.keep`) unless a save keeps it already. The running rig is
        then switched into it as a load's is (`_revert_switch`), which, with
        the agent's own Sushi link, is a restart: `Live.at` removes no track
        live (see there). Given a link that can (`Live.remove_track`, a
        stand-in in the tests), the last channel of the file is taken out
        live; a channel in front of another (whose engine input then moves)
        and a MIDI map Sushi loads only at the start restart all the same.
        The answer says which and why. Its `op: "rig"` entry is the new
        baseline. The pedals' listener, and its links to Sushi, follow the
        rig after (`_midi_sync`, #210). A channel the running rig has no
        track for (added to the file since it started) is written out of the
        file alone, `how: "file"`: no unsaved work is at stake, nothing is
        switched, and its Undo plays nothing.

        Refused before anything is written, each a 409 with its reason:
        `no_rig`, `panel`, `v1` (as a save); `changed`, when `rig` is given
        and is not the running rig; `no_channel`; `last_channel`, the rig's
        only channel (a rig needs one); `midi`, when controls of the MIDI
        map name the channel (`mappings`: each, with why; a blend's too,
        since a blend needs two channels), unless `"drop_midi": true`, which
        takes them out and says so (`dropped`); `rig_problem`, the compiler's
        own words; `plugins_missing`; `unsaved` while the rig holds live work
        no save has written, unless `"discard": true`; and `restarts`, with
        `why`, when `"restart": false` was sent and the switch would restart
        Sushi (the page's "Restart?", as a revert's).

        200 with `file`, `channel`, `removed` (`plugins`, the chain's names,
        and `send`), `dropped`, `how`, `why`, `kept_before`, `library`. 409
        `not_switched` (or 504) when the file was written and the rig could
        not be made to play it, `written: true`. One Undo (#209) step, `act:
        "remove_channel"`: the file put back, and the rig switched into it.

        Not run against a live Sushi but for `remove_live_track` itself (the
        probe in tool/spikes/live_remove_track/): shown against the fakes of
        the tests."""
        from . import rigedit

        try:
            asked = rigedit.remove_request(await self._json_body(request))
        except rigedit.EditRequestError as exc:
            raise web.HTTPBadRequest(reason=str(exc))
        channel = request.match_info["channel"]
        self._refuse_while_busy()
        answer: dict[str, Any]
        path: Path | None = None
        switched = False
        async with self._lock:
            self.busy = "channel remove"
            try:
                run = await self._rigfile_run()
                refusal = save_refusal(run["state"], run["described"])
                if refusal is not None:
                    raise _conflict(*refusal)
                path = run["running"]
                if asked["rig"] is not None and path.stem != asked["rig"]:
                    raise _conflict(f"the running rig is {path.stem}, not {asked['rig']}, so nothing was removed: "
                                    "look at the rig and remove it again", "changed")
                discarded: list[dict[str, Any]] = []
                try:
                    planned = await self._rigfile_thread(
                        request, path, lambda: self._plan_removal(path, channel, asked["drop_midi"], run))
                    async with self._live_lock:
                        if planned["playing"]:
                            refusal = load_refusal(run["state"], self.log, f"removing {channel}", "remove it")
                            if refusal is not None and not asked["discard"]:
                                raise RigFileRefused(*refusal)
                            discarded = (unsaved_entries(self.log, since=run["state"].get("started_at"))
                                         if refusal is not None else [])
                            if asked["restart"] is False:
                                restarts = await asyncio.to_thread(self._would_restart, planned["compiled"], run,
                                                                   discarded, (channel,))
                                if restarts:
                                    raise RigFileRefused(
                                        f"nothing was written: removing {channel} restarts Sushi: "
                                        + "; ".join(f"{r['code']}: {r['detail']}" for r in restarts)
                                        + '; remove it with "restart": true to go ahead', "restarts",
                                        {"why": restarts})
                        kept = await self._rigfile_thread(request, path, lambda: self._write_removal(path, planned))
                except RigFileRefused as exc:
                    error = _conflict(" ".join(str(exc).split()), exc.code)
                    if exc.extra:
                        error.agent_extra = exc.extra
                    raise error
                answer = {
                    "ok": True, "rig": path.stem, "file": self._save_shown(path), "channel": channel,
                    "written": True, "discarded": bool(discarded),
                    "removed": {"plugins": planned["plugins"], "send": planned["send"]},
                    "dropped": planned["dropped"], "kept_before": kept,
                }
                if not planned["playing"]:
                    # In the file and not a track of the running rig (added
                    # since it started, waiting for a restart): nothing plays
                    # it, so the file alone is written. No unsaved work is at
                    # stake and none is refused over, nothing is switched, so
                    # the live steps stay Undo's and no take is stopped; its
                    # Undo puts the file back and plays nothing (`apply:
                    # "none"`).
                    self._history_file(path, planned["before"], planned["after"], f"remove channel {channel}",
                                       run=run, apply="none", act="remove_channel")
                    answer["how"] = "file"
                else:
                    # One Undo (#209): the file put back and the rig switched
                    # into it, as a load's is, whatever the switch below does.
                    self._history_file(path, planned["before"], planned["after"], f"remove channel {channel}",
                                       run=run, apply="switch", act="remove_channel")

                    def mark(entry: dict[str, Any]) -> None:
                        entry["args"]["remove_channel"] = channel

                    switched = True
                    answer.update(await self._revert_switch(
                        request, path.stem, {"id": f"remove:{channel}", "what": f"without {channel}"}, mark=mark,
                        done="written", discarded=discarded, command=["remove", f"{channel} from {path.stem}"],
                        remove_tracks=(channel,)))
                    if discarded:
                        answer["discard"] = discard_said(discarded, answer)
                    if not answer.get("ok"):
                        # As a load's: the pedals follow what runs, not the file.
                        answer["midi_note"] = (
                            "the pedals' bindings follow the rig as it runs (its compiled config, "
                            f"{run['described'].get('config')}), not the file just written: restart the rig to "
                            "play the file, and its pedals with it")
            finally:
                self.busy = None
                if switched:
                    await self._midi_sync()  # the switch stopped the listener; it follows the rig now
        await self._rigfile_git_after(answer, path)
        if answer["ok"]:
            return web.json_response(answer)
        return web.json_response(answer, status=504 if answer.get("abandoned") else 409)

    def _plan_removal(self, path: Path, channel: str, drop_midi: bool, run: dict[str, Any]) -> dict[str, Any]:
        """What removing `channel` from rig file `path` writes, decided and
        compiled before anything is written, or the refusal
        (`RigFileRefused`). Off the loop."""
        import yaml

        from . import channelcopy, rigedit

        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise RigFileRefused(f"{path.name} is not a rig file", "rig_problem")
        try:
            edited, entry = rigedit.without_channel(raw, channel)
        except LookupError as exc:
            raise RigFileRefused(str(exc), "no_channel") from exc
        if not [c for c in edited.get("channels") or [] if isinstance(c, dict)]:
            raise RigFileRefused(f"nothing was written: {channel} is {path.stem}'s only channel, and a rig needs "
                                 "one: add another first, or delete the rig", "last_channel")
        named = rigedit.midi_naming(raw.get("midi"), channel)
        if named and not drop_midi:
            count = f"{len(named)} control{'s' if len(named) != 1 else ''}"
            raise RigFileRefused(
                f"nothing was written: {count} of {path.stem}'s MIDI map name{'s' if len(named) == 1 else ''} "
                f"{channel}: {'; '.join(rigedit.named_said(n) for n in named)}. Remove it dropping them too "
                '({"drop_midi": true}), or edit the midi: section first', "midi", {"mappings": named})
        if named:
            edited["midi"] = channelcopy.drop_orphaned(edited.get("midi"), named)
        compiled = self._rigfile_compile(edited, run)
        self._rigfile_plugins(compiled, path.stem, run, "changed", "remove it again")
        chain = entry.get("chain") if isinstance(entry.get("chain"), list) else []
        # Written out of the file alone only when nothing that plays can be
        # this channel: it is no track of the running rig, and every channel
        # track of the running rig is still a channel of the file. A channel
        # renamed by hand while it plays (`vocal` running, `vox` in the file)
        # is a track the file no longer names, so it goes through the switch,
        # which asks (re-review of PR #238).
        try:
            running = self.switching.read_config(run["described"].get("config"))
            tracks = {t.get("name") for t in running.get("tracks") or [] if isinstance(t, dict)} - {rigfile.MASTER}
            filed = {c.get("name") for c in raw.get("channels") or [] if isinstance(c, dict)}
            playing = channel in tracks or not tracks <= filed
        except Exception:  # noqa: BLE001 -- not known: switched, which plays whatever it is
            playing = True
        return {"raw": edited, "compiled": compiled, "channel": channel, "dropped": named, "playing": playing,
                "plugins": [p.get("name") for p in chain if isinstance(p, dict)],
                "send": f"{channel}{rigfile.SEND_SUFFIX}"}

    def _write_removal(self, path: Path, planned: dict[str, Any]) -> dict[str, Any]:
        """Write a planned removal into the rig file, then keep the file as it
        was for Revert… (`_write_planned`, as an amp added is); what was kept.
        Off the loop, under the live lock."""
        return self._write_planned(path, planned, f"before removing {planned['channel']}",
                                   f"the removal of {planned['channel']}")

    # ======================================================================= #
    # (end of a channel removed from the running rig)
    # ======================================================================= #

    # ======================================================================= #
    # Recording from the web UI (S7): GET /record, POST /record/start|stop,
    # GET /takes
    # ======================================================================= #
    #
    # Kept in one block, as the amp's and save's are. Outside it the agent
    # calls in at: __init__ (`_record_init`), `app` (`_record_routes`),
    # `_run_lifecycle`, `_switch_held` and `_change_model` (`_finish_take`, so
    # a take is finished before the rig under it changes), and `pick_undo`,
    # which passes over a record entry. The recorder is a child of the rig
    # (recorder_process.py says why); the agent asks it over its socket.
    #
    # Locks: a start or a stop takes `_record_lock`, so the agent sends one
    # at a time and a lifecycle command waiting to finish the take goes after
    # a start in flight, never between its plan and its folder. A lifecycle
    # command takes it inside the lifecycle lock, and a model change inside
    # the amp's; a start takes the amp's lock first and holds both until the
    # take has started, so the amp a take names is the one playing when it
    # starts. Always lifecycle, then amp, then record: no two locks are ever
    # taken in both orders. The cost: a fader moved during a start waits for
    # it (the start's own deadline, `START_TIMEOUT`, bounds that).

    def _record_init(self, recorder: Any, takes_dir: Path | None) -> None:
        from .recorder_process import RecorderLink
        from .take import default_takes_dir

        self.recorder = recorder or RecorderLink()
        self.takes_dir = Path(takes_dir) if takes_dir is not None else default_takes_dir()
        # The takes folder as /health and POST /takes/open name it, resolved
        # once here: a filesystem call, never made on the loop per request.
        self._takes_dir_shown = Path(self.takes_dir).resolve()
        # The player's clock, for the day in a take's default name; local
        # time, as the name is for the player to recognise. A test moves it.
        self.take_clock: Callable[[], datetime] = datetime.now
        self._record_lock = asyncio.Lock()
        self._takes_cache: dict[str, Any] = {}  # GET /takes: each take's summary, by what is in its folder
        self._takes_lock = threading.Lock()  # the cache is read and written in worker threads
        # What POST /takes/open runs, before the folder (`folder_opener`); a test stands one in.
        self.folder_opener: Callable[[], list[str] | None] = folder_opener

    def _record_routes(self, app) -> None:
        app.router.add_get("/record", self.record_route)
        app.router.add_post("/record/start", self.record_start_route)
        app.router.add_post("/record/stop", self.record_stop_route)
        app.router.add_get("/takes", self.takes_route)
        app.router.add_post("/takes/open", self.takes_open_route)

    async def _record_state(self) -> tuple[dict[str, Any], dict[str, Any], "tuple[str, str] | None"]:
        """`(state, described, refusal)`: the state file, `describe_rig`'s
        answer, and why no take can be recorded from here, or None."""
        described = self.describe()
        state = await asyncio.to_thread(self.switching.read_state) or {}
        return state, described, record_refusal(state, described)

    async def _default_take_name(self, state: dict[str, Any]) -> str:
        """What a take of the running rig started now without a name is
        called (`take.next_name`: `<rig>-<date>-take-<nn>`), counted from the
        takes folder as it is now, off the loop. For a run `record_refusal`
        lets through, so `state` names its rig file. The one function GET
        /record's offer and a start with no name both ask."""
        from .take import next_name

        return await asyncio.to_thread(next_name, self.takes_dir, take_rig_name(state), self.take_clock())

    async def _ask_recorder(self, request: dict[str, Any], timeout: float) -> dict[str, Any]:
        """One request to the recorder, off the loop. `RecorderDown` and
        `RecorderTimeout` are the caller's to say."""
        return await asyncio.to_thread(self.recorder.ask, request, timeout)

    def _recorder_missing(self, state: dict[str, Any], described: dict[str, Any], exc: Exception) -> str:
        """Why no recorder answers, in the player's words: never started for
        this run, crashed (and not restarted, principle 9), or not answering."""
        children = state.get("children") or {}
        if "recorder" not in children:
            return ("this run has no recorder: it was started before recording from the web UI existed. "
                    "Restart the rig (POST /rig/restart) to record from here")
        crashed = next((c for c in reversed(self.supervisor.crashed) if c.get("name") == "recorder"), None)
        child = (described.get("children") or {}).get("recorder") or {}
        if crashed is not None or child.get("alive") is False:
            pid = crashed["pid"] if crashed else child.get("pid", children.get("recorder"))
            log = f" (its log: {crashed['log']})" if crashed and crashed.get("log") else ""
            return (f"the recorder (pid {pid}) is not running: it exited{log}, which /health shows as a crash. "
                    "It is not restarted on its own (principle 9): POST /rig/restart brings it back")
        return f"the recorder is not answering: {exc}"

    async def record_route(self, request):
        """`GET /record`: `state` is always one of `recording` (`name`,
        `started_at`, `elapsed` in seconds as the recorder counts it,
        `stems`), `idle` (`stems` a take would record now; `names`, the
        takes already in the folder, so a page can say a name will replace
        one; and `default_name`, what a take started now without a name
        would be called, for the record bar to show: `_default_take_name`)
        or `unavailable` (`reason`, and a `code`: `no_rig`, `panel`,
        `v1`, `no_recorder`, `recorder_timeout`, or none when the recorder
        cannot plan a take, with its reason). `doing` while a start or stop is
        under way. 200 for each of those, since unavailable is a state; a
        recorder that refuses the question itself is answered with the status
        its code means (`RECORD_CODES`), and one that answers without a
        state is 502, never passed on."""
        state, described, refused = await self._record_state()
        if refused is not None:
            reason, code = refused
            return web.json_response({"ok": True, "state": "unavailable", "code": code, "reason": reason})
        run = recorder_run(state, self._midi_root(), self.takes_dir)
        try:
            answer = await self._ask_recorder({"op": "status", "run": run}, STATUS_TIMEOUT)
        except RecorderDown as exc:
            return web.json_response({"ok": True, "state": "unavailable", "code": "no_recorder",
                                      "reason": self._recorder_missing(state, described, exc)})
        except RecorderTimeout as exc:
            return web.json_response({"ok": True, "state": "unavailable", "code": "recorder_timeout",
                                      "reason": str(exc)})
        if not answer.get("ok"):
            raise self._recorder_refused(request, answer)
        if answer.get("state") not in RECORD_STATES:
            reason = f"the recorder answered a status without a state it may have: {answer!r}"
            self._remember([request.method, request.path], reason, kind="live")
            raise web.HTTPBadGateway(reason=" ".join(reason.split())[:500])
        if answer["state"] == "idle":
            from .take import take_names

            answer = {**answer, "names": await asyncio.to_thread(take_names, self.takes_dir),
                      "default_name": await self._default_take_name(state)}
        return web.json_response(answer)

    async def record_start_route(self, request):
        """`POST /record/start {"name"?}`: start a take. No name is
        `<rig>-<date>-take-<nn>` (`_default_take_name`, what GET /record
        offered), worked out here under the record lock and after the
        refusals, so two starts never get one number and a run that cannot
        be recorded is refused as it always was; a name is
        checked as a take's is (`take.check_name`), 400 otherwise. A take
        under a name already used moves the earlier one to `_archive/`, and
        the answer says so (`replaced`, `said`).

        The take's config is the one Sushi loaded for this run (the state
        file's, which a live switch rewrites) with the amp as the agent last
        sent it (`_take_amp`), and its rig.yaml the rig file as it is now:
        both read at this RECORD, by the recorder. The amp's lock is held from
        reading the amp until the take has started (and the record lock
        inside it, the order a model change takes them in), so no model
        change or fader move lands between the amp the take names and the
        take: one asked for meanwhile waits for the start, and a model change
        then finishes the take it would otherwise misdescribe.

        200 with `name`, `started_at`, `stems`, `said`, `amp` and the log
        `entry` (`op: "record"`, `action: "start"`). Refusals: 409 `busy`;
        `no_rig`; `panel` (the listener's record bar owns recording); `v1`;
        `recording` (a take is running: stop it first); `no_recorder`;
        `unavailable` (no take can be planned for this run, with why). 502
        `failed` for a take tried that did not start (its folder says why),
        504 `recorder_timeout` when the recorder does not answer within
        `START_TIMEOUT`."""
        try:
            name = record_request(await self._json_body(request))
        except AgentError as exc:
            raise web.HTTPBadRequest(reason=str(exc))
        self._refuse_while_busy()
        async with self._amp_lock:
            async with self._record_lock:
                self._refuse_while_busy()  # a lifecycle command may have begun while this waited
                state, described, refused = await self._record_state()
                if refused is not None:
                    raise _conflict(*refused)
                if name is None:
                    name = await self._default_take_name(state)
                amp, amp_said = await self._take_amp()
                run = recorder_run(state, self._midi_root(), self.takes_dir)
                answer = await self._recorder_call(
                    request, state, described, {"op": "start", "name": name, "run": run, "amp": amp},
                    START_TIMEOUT)
                entry = self._record("record", {
                    "action": "start", "name": name, "stems": [s["file"] for s in answer.get("stems") or []],
                    "amp": amp_said["saved"], **({"replaced": True} if answer.get("replaced") else {})}, None)
        return web.json_response({**answer, "amp": amp_said, "entry": entry})

    async def record_stop_route(self, request):
        """`POST /record/stop`: finish the running take. 200 with `name`,
        `seconds`, `problems` (`take check`'s, empty when whole), `said` and
        the log `entry` (`op: "record"`, `action: "stop"`); 409
        `not_recording`, `no_rig`, `panel`, `v1`, `no_recorder`. A stop the
        recorder could not make cleanly, or could not read back, is 502
        `failed` with the reason (`stopped: true` says the take did stop),
        and is logged: the take is over either way. Not refused while a
        lifecycle command runs: that command finishes the take itself, and
        this then answers `not_recording`. No confirm: stopping a take loses
        nothing."""
        body = await self._json_body(request)
        if body not in (None, {}):
            raise web.HTTPBadRequest(reason=f"the body of a stop must be empty, not {body!r}")
        async with self._record_lock:
            state, described, refused = await self._record_state()
            if refused is not None:
                raise _conflict(*refused)
            answer = await self._stop_take(request, state, described, "request")
        return web.json_response(answer, status=200 if answer["ok"] else 502)

    def _recorder_refused(self, origin, answer: dict[str, Any]):
        """The HTTP error a recorder's refusal means (`RECORD_CODES`; 502 for
        `failed` and anything unknown, which is remembered for /health)."""
        code = answer.get("code") or "failed"
        reason = " ".join(str(answer.get("reason") or "the recorder refused, and did not say why").split())
        status = RECORD_CODES.get(code, web.HTTPBadGateway)
        if status in (web.HTTPBadGateway, web.HTTPInternalServerError):
            self._remember([origin.method, origin.path], reason, kind="live")
        error = status(reason=reason)
        error.agent_code = code
        return error

    async def _recorder_call(self, origin, state, described, ask: dict[str, Any], timeout: float) -> dict[str, Any]:
        """A start or a stop: its answer when it worked; otherwise the HTTP
        refusal its `code` means (`_recorder_refused`), or 409 `no_recorder`,
        or 504 `recorder_timeout`. A stop that ran and failed (`stopped`) is
        answered, not raised: the take is over and is logged."""
        where = [origin.method, origin.path]
        try:
            answer = await self._ask_recorder(ask, timeout)
        except RecorderDown as exc:
            raise _conflict(self._recorder_missing(state, described, exc), "no_recorder")
        except RecorderTimeout as exc:
            reason = (f"{exc}: it may still be doing it. Read GET /record before trying again")
            self._remember(where, reason, kind="live")
            error = web.HTTPGatewayTimeout(reason=reason)
            error.agent_code = "recorder_timeout"
            raise error
        if answer.get("ok") or (ask["op"] == "stop" and answer.get("stopped") and answer.get("name")):
            return answer
        raise self._recorder_refused(origin, answer)

    async def _stop_take(self, origin, state, described, by: str) -> dict[str, Any]:
        """Stop the running take and log it; held under `_record_lock`."""
        answer = await self._recorder_call(origin, state, described, {"op": "stop", "by": by}, STOP_TIMEOUT)
        entry = self._record("record", {
            "action": "stop", "name": answer["name"], "seconds": answer.get("seconds"),
            "problems": len(answer.get("problems") or []), "by": by,
            **({} if answer.get("ok") else {"ok": False})}, None)
        if not answer.get("ok"):
            self._remember([origin.method, origin.path], " ".join(str(answer.get("reason")).split()), kind="live")
        return {**answer, "entry": entry}

    async def _finish_take(self, origin, why: str) -> dict[str, Any] | None:
        """Before `why` (a stop, restart, switch or model change) changes the
        rig under a take: finish the take cleanly, log it (`by: why`), and
        return the stop's answer for the command's own answer, or None when
        no take was running. Called holding the lifecycle lock, and only once
        the command is going ahead: after its own refusals (a switch's, a
        model change's), and never for `up`, which takes no rig down and
        refuses when one is running (`_run_lifecycle`).

        Finished, not refused: the command is the player's, made on purpose,
        and the take ends whole either way; refusing would leave a pedal's
        Program Change doing nothing mid-set. A recorder that cannot be asked
        (none, or not answering) is said, not a reason to refuse: `down`
        must always be possible, and its SIGINT finishes a take the recorder
        still holds (recorder_process.serve)."""
        async with self._record_lock:
            state, described, refused = await self._record_state()
            if refused is not None:
                return None  # no take of the agent's can be running: the listener's, if any, is the panel's
            try:
                return await self._stop_take(origin, state, described, why)
            except web.HTTPException as exc:
                if getattr(exc, "agent_code", None) in ("not_recording", "no_recorder"):
                    return None
                said = f"a take may still be recording: it could not be finished before {why}: {exc.reason}"
                print(f"sushi-rig agent: {said}", file=sys.stderr, flush=True)
                return {"ok": False, "reason": said}

    async def _take_amp(self) -> tuple[dict[str, Any] | None, dict[str, Any]]:
        """`(amp, said)`: the amp a take's config carries, as the recorder
        takes it (`recorder_process.parse_amp`), and what the answer says of
        it. The agent's record of what it last sent Carla (amp_live.py), as a
        save writes it (#117): `{"model": None}` for a run with no amp, so
        the take carries none; None for an amp whose model the state file
        does not say, so the config keeps the `_amp` it was compiled with
        rather than a guess. Called holding the amp's lock, which the caller
        keeps until the take has started."""
        try:
            amp, _, _ = await self._amp_now(writing=False)
        except amp_live.AmpRefused as exc:
            if exc.code == "no_amp":
                return {"model": None, "parameters": {}}, {
                    "saved": False, "why": f"{exc}, so the take carries no amp"}
            raise self._amp_http(exc)
        if not amp.model:
            return None, {"saved": False, "why": "the state file does not say which model the amp runs, "
                                                 "so the take carries the amp the rig was compiled with"}
        return {"model": amp.model, "parameters": dict(amp.values)}, {
            "saved": True, "model": amp.model, "parameters": dict(amp.values),
            "known": amp_live.KNOWN_SENT, "note": amp_live.KNOWN_NOTE[amp_live.KNOWN_SENT]}

    async def takes_route(self, request):
        """`GET /takes`: the takes in the takes folder, newest first, each
        with its name, when, length, stems, whether it was finished, audio
        lost and `take check`'s verdict (`take.list_takes`), read off the
        loop. No rig is needed: takes are files. Each take's summary is kept
        until a file in its folder changes (`_takes_cache`), so a page asking
        again re-checks only the takes that moved, not every stem header of
        every take, on the machine playing the audio."""
        from .take import list_takes

        def listing() -> dict[str, Any]:
            with self._takes_lock:  # two pages asking at once share the cache one at a time
                return list_takes(self.takes_dir, cache=self._takes_cache)

        listed = await asyncio.to_thread(listing)
        return web.json_response({"ok": True, **listed})

    def _takes_folder(self, request) -> dict[str, Any]:
        """`/health`'s `agent.takes`: `dir`, the takes folder, absolute, and
        `can_open`, whether POST /takes/open would serve this peer."""
        return {"dir": str(self._takes_dir_shown), "can_open": is_local(request.remote)}

    async def takes_open_route(self, request):
        """`POST /takes/open {}` (#189): open the takes folder in this
        machine's file manager, for the rack's Open folder on the laptop.

        Only for a loopback peer (`is_local`): the folder opens on this
        machine's desktop, so a phone on the LAN is refused (403
        `not_local`) and shown the path instead. Behind the guard in front
        of every write, as any route is (`_same_site_only`). The folder is
        always `takes_dir`, never a path from the request, whose body must
        be empty. It runs the first of `FOLDER_OPENERS` installed
        (`folder_opener`) with no shell, in a thread (`open_folder`), so the
        loop never waits on it.

        200 with `folder`, `opener` and `said`; 409 `no_folder` before the
        first take makes it, 409 `no_opener` when neither xdg-open nor gio
        is installed, 502 `open_failed` when the opener exits non-zero, with
        what it said. **Not run on a desktop**: only against stand-in
        openers."""
        if not is_local(request.remote):
            refused = web.HTTPForbidden(reason=(
                f"refused: only a page open on this machine can open its takes folder, and this came from "
                f"{request.remote or 'an address not known'}. The takes are in {self._takes_dir_shown}"))
            refused.agent_code = "not_local"
            raise refused
        body = await self._json_body(request)
        if body not in (None, {}):
            raise web.HTTPBadRequest(reason=f"the body of an open must be empty, not {body!r}: "
                                            "the folder is always the takes folder")
        folder = self._takes_dir_shown
        if not await asyncio.to_thread(folder.is_dir):
            raise _conflict(f"there are no takes yet: {folder} is made by the first take", "no_folder")
        opener = await asyncio.to_thread(self.folder_opener)
        if opener is None:
            names = " nor ".join(" ".join(o) for o in FOLDER_OPENERS)
            raise _conflict(f"nothing here opens a folder: neither {names} is installed. "
                            f"The takes are in {folder}", "no_opener")
        name = " ".join([Path(opener[0]).name, *opener[1:]])
        done = await asyncio.to_thread(open_folder, opener, folder)
        if done["exited"] not in (None, 0):
            said = done.get("said") or "it said nothing"
            error = web.HTTPBadGateway(reason=" ".join(
                f"{name} could not open {folder}: it exited {done['exited']}: {said}".split()))
            error.agent_code = "open_failed"
            raise error
        said = (f"asked {name} to open {folder}" if done["exited"] is None
                else f"{name} opened {folder}")
        return web.json_response({"ok": True, "folder": str(folder), "opener": name, "said": said})

    # ======================================================================= #
    # (end of recording from the web UI)
    # ======================================================================= #
    # ======================================================================= #
    # The user guide (#167): GET /help/, /help/{page}, /help/img/{name}
    # ======================================================================= #
    #
    # docs/guide/ as HTML, rendered by `guide.py`, which says what is read,
    # how, and what can be served. Kept in one block so it merges past other
    # work on this file; outside it only the call in `app`. GETs only, and
    # nothing here reads or changes the rig.

    # The guide's folder; None is the checkout's docs/guide/ (`guide.guide_dir`).
    # A test points it at a folder of its own.
    guide_dir: Path | None = None

    def _help_routes(self, app) -> None:
        app.router.add_get("/help", self.help_redirect)
        app.router.add_get("/help/", self.help_page)
        app.router.add_get("/help/img/{name}", self.help_image)
        app.router.add_get("/help/{page}", self.help_page)

    def _guide(self) -> Path:
        from . import guide

        return self.guide_dir or guide.guide_dir()

    async def help_redirect(self, request):
        """/help is the guide's folder: send the browser to its front page,
        keeping where it was opened from. 302, as /ui is."""
        raise web.HTTPFound("/help/" + (f"?{request.query_string}" if request.query_string else ""))

    async def help_page(self, request):
        """A page of the guide as HTML (`guide.render_page`), read now, off
        the loop. `?from=` is the screen it was opened from, for the way back
        (`guide.back_from`: anything else is ignored). A page the guide has
        not is an HTML 404 with the way back, not the JSON one, since a
        person reads it."""
        from . import guide

        name = request.match_info.get("page")
        back = guide.back_from(request.query.get("from"))
        if name is None:
            name = guide.INDEX
        elif (page := guide.canonical(name)) is not None:
            # A page's file name (README.md, recording.md) or another case of
            # its name, typed by hand or copied from the checkout.
            raise web.HTTPFound(guide.page_href(page, back))
        folder = self._guide()
        made = await asyncio.to_thread(guide.render_page, folder, name, back)
        status = 200
        if made is None:
            made, status = await asyncio.to_thread(guide.not_found, folder, name, back), 404
        return web.Response(text=made, status=status, content_type="text/html", charset="utf-8",
                            headers={"Cache-Control": UI_CACHE_CONTROL})

    async def help_image(self, request):
        """A picture of the guide's img/ (`guide.image_file`), or 404."""
        from . import guide

        path = await asyncio.to_thread(guide.image_file, self._guide(), request.match_info["name"])
        if path is None:
            raise web.HTTPNotFound(reason="the guide has no such picture")
        return web.FileResponse(path, headers={"Cache-Control": UI_CACHE_CONTROL})

    # ======================================================================= #
    # (end of the user guide)
    # ======================================================================= #


# A pending MIDI action past this many is dropped (and said), not queued.
MIDI_PENDING = 16


@dataclass(frozen=True)
class _MidiOrigin:
    """Where a MIDI action came from, in the two fields `Agent._sushi` and the
    live lock read off a request for their messages: "MIDI" and the control."""

    method: str
    path: str


# The web UI's files are asked again, against their ETag, on every load: with
# no Cache-Control a browser may keep the app.js it had before the agent was
# updated and run that against the new agent (2026-11-06, on the rig). An
# unchanged file is a 304, so this costs one round trip a file.
UI_CACHE_CONTROL = "no-cache"


# The types of the UI's files Python's `mimetypes` may not know, whatever the
# machine's /etc/mime.types says (#139): 3.11 has neither. A manifest served
# as anything else is not read by a browser as one.
UI_TYPES = {".webmanifest": "application/manifest+json", ".woff2": "font/woff2"}


# Every other answer of the agent's (#139): never kept, by the browser, a
# proxy or anything installed with the page. A route that says otherwise (the
# guide's pages, a stream) keeps its own.
API_CACHE_CONTROL = "no-store"


async def _ui_cache_control(request, response) -> None:
    """`Cache-Control: no-cache` on the page (`/`) and its files (`/ui/...`),
    set as each response is prepared, so the static route's own answers,
    304s included, carry it too; and the type of a file `UI_TYPES` names.
    Any other answer that sets none is `no-store` (`API_CACHE_CONTROL`)."""
    if request.path == "/" or request.path.startswith("/ui/"):
        response.headers["Cache-Control"] = UI_CACHE_CONTROL
        suffix = request.path[request.path.rfind("."):] if "." in request.path.rsplit("/", 1)[-1] else ""
        if suffix in UI_TYPES and 200 <= response.status < 300:
            response.headers["Content-Type"] = UI_TYPES[suffix]
    elif "Cache-Control" not in response.headers:
        response.headers["Cache-Control"] = API_CACHE_CONTROL


def plugin_width(plugin: dict[str, Any], ports: dict[str, Any]) -> str | None:
    """"mono" or "stereo" for an LV2 plugin the ports table lists, by its
    outputs (one is mono, as `stereo.py` decides where a copy goes); None
    for anything else, which the page says nothing about. Pure."""
    said = ports.get(plugin.get("uri")) if plugin.get("type") in (None, "lv2") else None
    if said is None:
        return None
    return "mono" if said.outputs == 1 else "stereo" if said.outputs >= 2 else None


def switch_steps(state: dict[str, Any], rig: str) -> list[list[str]]:
    """The lifecycle commands that switch the running rig (`state`, the state
    file) to rig `rig` of config/rigs: `down`, then `up --rig` with the
    headless, tuner, amp and visualiser tap choices `restart` would bring back
    (`rig.restart_arguments`), so a switch keeps how the rig is being run.
    Not the amp model override: that was chosen for the rig being left, and
    the next one's own `_amp` says its model. `AgentError` for a rig name that
    is not one. Pure.

    Nor `--no-amp`, for a switch to another rig (`keeps_no_amp`, #246): a
    run started with the amp off kept it off through every switch after,
    and an owner who switched to a rig with an amp heard none, with nothing
    on the page to turn it back on. A switch into the same rig (a revert, a
    channel loaded or removed, an Undo, the `up` that brings a rig back
    after a failed switch) keeps it, as `restart` does.

    The machine file the run was started against goes with it (`--machine`),
    as `Switching.compile` already compiled the next rig against it: until
    2026-11-06 every switch by restart dropped it, and `up` wired the next
    rig against this host's file instead, which need not be the one it was
    checked against."""
    _, options = restart_arguments(state)
    machine = options.get("machine") or state.get("machine")
    up = lifecycle_arguments("up", {
        "config_name": rig, "rig": True,
        "headless": options["headless"], "tuner": options["tuner"],
        "amp": options["amp"] or not keeps_no_amp(state, rig),
        "visualiser_tap": options["visualiser_tap"],
    }, machine=_absolute_machine(machine))
    return [["down"], up]


def keeps_no_amp(state: dict[str, Any], rig: str) -> bool:
    """Whether switching the run `state` describes into rig `rig` keeps it
    without an amp: only a run started with --no-amp (`amp_requested`
    False), and only into the rig it is running (`switching_from`), so that
    a revert, a channel loaded or removed, or an Undo does not bring Carla
    back on a rig the player took it off. A switch to another rig drops the
    request (#246). Pure."""
    return state.get("amp_requested") is False and rig == switching_from(state, {})


# What `up` says of the amp itself (rig.startup_exit_problem, amp.prepare's
# "THE AMP IS NOT WIRED", a link to or from NAM not made, `start_children`'s
# replay): the warnings that end with the Carla they were said of.
_AMP_WARNING = re.compile(
    r"NO AMP|^amp did not start|^amp exited|amp model not found|^amp parameters not restored|AMP IS NOT WIRED"
    r"|NAM:(?:Input|Output)", re.I)


def amp_warning(line: str) -> bool:
    """Whether a warning of `up`'s is about the amp (`_AMP_WARNING`), as
    rig.js's `warningsTag` reads one. Pure."""
    return bool(_AMP_WARNING.search(line))


def up_warnings(stderr: str) -> list[str]:
    """The warnings `up` said on stderr as a rig came up (`warning: ...`
    lines; `Supervisor._start` writes one per problem): a patchbay link not
    made, an amp that did not start or exited, an amp not wired. What a
    lifecycle answer and a switch's carry (#246). Pure."""
    return [line[len("warning: "):] for line in (stderr or "").splitlines() if line.startswith("warning: ")]


def _absolute_machine(machine: Any) -> str | None:
    """The state file's machine file as `up --machine` takes it, or None."""
    if not isinstance(machine, str) or not machine:
        return None
    return machine if machine.startswith("/") else str(Path(machine).resolve())



# =========================================================================== #
# Switching rigs live (#93): the decisions, pure, and the world they reach
# =========================================================================== #

class SwitchRefused(AgentError):
    """A switch not made, with the running rig left as it was: answered 409
    with `code` (`no_rig`, `rig_problem`, `plugins_missing`) by the route,
    and said in /health by the Program Change path."""

    def __init__(self, reason: str, code: str):
        super().__init__(reason)
        self.code = code


# Why a switch restarts, beyond the configs' differences (`rigfile.RESTART_REASONS`):
# what only the state file, or the attempt itself, can say.
SWITCH_RESTART_REASONS = {
    "panel": "the rig runs with its panel and SAVE listener, which were started for the running "
             "rig: switched live, SAVE would write the next rig's sound into the previous rig's file",
    "amp_override": "the amp runs a model given with --amp-model or chosen with POST /amp/model, "
                    "which belonged to the rig being left; the next rig's own amp comes with a restart",
    "failed": "a step of the live switch failed, so it restarts into the next rig rather than "
              "leave one half-switched",
    "unverified": "the rig read back after the live switch is not the next rig",
    "amp_down": "the next rig has an amp and the running rig's Carla is not running, so a live switch "
                "would leave it with none; a restart brings Carla back",
    "amp_unwired": "the next rig has an amp and the running rig's Carla runs with its links not made, so "
                   "the guitar goes in dry; a restart starts the patchbay and Carla again",
}


def switch_request(body: Any) -> str:
    """The rig a switch names, or `AgentError` (400). A name of a file in
    config/rigs, checked as `up`'s is: a name, never a path."""
    body = _object(body) if body is not None else {}
    if "config_name" not in body:
        raise AgentError("config_name is required: the name of a rig in config/rigs/")
    return lifecycle_arguments("up", {"config_name": body["config_name"], "rig": True})[1]


def state_blockers(state: dict[str, Any]) -> list[rigfile.RestartReason]:
    """What, in how the running rig is being run, rules a live switch out
    (`SWITCH_RESTART_REASONS`). Pure."""
    from .rig import runs_panel

    found = []
    if runs_panel(state):
        found.append(rigfile.RestartReason("panel", SWITCH_RESTART_REASONS["panel"]))
    if state.get("amp_model_requested") and state.get("amp_requested", True):
        found.append(rigfile.RestartReason(
            "amp_override", f"the amp runs {state['amp_model_requested']!r}, given with --amp-model"))
    return found


def amp_blockers(
    state: dict[str, Any], described: dict[str, Any], upcoming: dict[str, Any] | None,
    wiring: dict[str, Any] | None, *, amp_off: bool,
) -> list[rigfile.RestartReason]:
    """Why the amp rules a live switch into `upcoming` out (#246): an amp
    that should run for the next rig and is not alive, or not wired. Pure.

    A live switch keeps the Carla that runs. Most rigs share a model and
    knobs, so the plan has no amp step at all and nothing touched Carla: a
    dead one, or one whose links are gone, survived the switch while the
    guitar played dry. So the switch restarts when:

    - the next rig has an `_amp` and the switch does not keep the run
      without one (`amp_off`, `keeps_no_amp`), and
    - Carla is not running (`described`'s amp child absent or not alive:
      `amp_down`), or runs with its links not made (`wiring`, `wired`
      False: `amp_unwired`). A wiring that could not be looked at is no
      reason: the rig plays on as it was.

    Only when the running rig's own config has an `_amp` too: from a rig
    with none, `rigfile.diff` already restarts for the amp (`_amp_plan`), and
    says so as `amp`."""
    if amp_off or not ((upcoming or {}).get("_amp") or {}).get("model"):
        return []
    child = (described.get("children") or {}).get("amp")
    if child is None or child.get("alive") is not True:
        if state.get("amp_requested") is False:
            said = "the running rig was started with --no-amp, and the next rig has an amp"
        elif child is None:
            said = "the running rig's Carla did not start (see the warnings of the up that started it)"
        else:
            said = f"the running rig's Carla (pid {child.get('pid')}) is not running"
        return [rigfile.RestartReason("amp_down", said)]
    if wiring is not None and wiring.get("wired") is False:
        said = "; ".join(wiring.get("problems") or []) or "its links are not made"
        return [rigfile.RestartReason("amp_unwired", f"Carla runs, but the amp is not wired: {said}")]
    return []


def switching_from(state: dict[str, Any], described: dict[str, Any]) -> str | None:
    """The name of the rig a switch leaves: a rig of channels by its file,
    a v1 config by its name."""
    rig = state.get("rig") or described.get("rig")
    if isinstance(rig, str) and rig:
        return Path(rig).stem
    return state.get("config_name") or described.get("config_name")


def fallback_arguments(state: dict[str, Any]) -> list[str] | None:
    """The `up` that brings the running rig back if a switch's `up` fails:
    a rig of channels as the MIDI switch has always brought it back
    (`switch_steps`), a v1 config as `restart` would. Pure."""
    rig = state.get("rig")
    if isinstance(rig, str) and rig:
        return switch_steps(state, Path(rig).stem)[1]
    name, options = restart_arguments(state)
    try:
        return lifecycle_arguments("up", {"config_name": name, **options})
    except AgentError:
        return None


def switched_state(state: dict[str, Any], name: str, config: Any) -> dict[str, Any]:
    """The state file after a live switch to rig `name`, whose compiled config
    is at `config`: the same supervisor, group, children and options, now
    naming the next rig, so `restart`, `status` and the listener follow it.
    `switched_at` says when; `started_at` stays the supervisor's. Pure.

    A run started with --no-amp that switched to another rig no longer asks
    for no amp (`keeps_no_amp`, #246): the next restart or switch brings the
    amp, where that rig has one. Into the same rig it keeps it."""
    after = {
        **state, "config_name": name, "config": str(config), "rig": f"config/rigs/{name}.yaml",
        "switched_at": datetime.now(timezone.utc).isoformat(),
    }
    if state.get("amp_requested") is False and not keeps_no_amp(state, name):
        after["amp_requested"] = True
    return after


def place_switch_add(
    reading: dict[str, Any], track: str, name: str, uri: str, before: str | None,
    copy: str | None = None,
) -> Addition:
    """Where a switch's add goes: in front of `before`, or at the back, as
    `rigfile.diff` decided and Sushi's own API places a processor. `Conflict`
    when the rig is not as the plan expects: no such track, the name taken,
    `before` not on it, or the back of a chain that ends in its send. Pure.

    Not `check_add`, which places "after" a processor or before the send:
    the plan says what each add goes in front of, which also reaches the front
    of a chain. The URI is the next rig's, not checked against the manifest:
    the rig file is the authority here, as it is for `up`."""
    chains, ids = reading.get("chains", {}), reading.get("ids", {})
    uids = reading.get("uids") or {}
    if track not in chains:
        raise Conflict(f"the running rig has no track {track!r} to add {name!r} to")
    if name in chains or _track_of(chains, name) is not None:
        raise Conflict(f"{name!r} is already on the rig")
    chain = list(chains[track])
    if before is None:
        if any(is_send(track, held, uids.get(held)) for held in chain):
            raise Conflict(f"{name!r} would go behind {track!r}'s send, where it is not heard")
        if any(is_tail(track, held, uids.get(held)) for held in chain):
            raise Conflict(f"{name!r} would go behind the master's limiter, which stays last (#137)")
        index = len(chain)
    elif before not in chain:
        raise Conflict(f"{before!r}, which {name!r} goes in front of, is not on {track!r}: it has {chain}")
    else:
        index = chain.index(before)
    # Never in front of the master's return, which is first on its track and
    # stays first (`check_add`'s invariant, by `protected_processor`'s rule):
    # ahead of it, a processor would get no channel's signal (found by review:
    # `before: "master_return"` was accepted).
    for held in chain[index:]:
        if (protected_processor(track, held, uids.get(held)) and not is_tail(track, held, uids.get(held))
                and not stereo.is_copy(held, uids.get(held))):
            raise Conflict(f"{name!r} would go in front of {held!r}, the master's return, which stays first")
    if copy is not None and (copy in chains or _track_of(chains, copy) is not None):
        raise Conflict(f"{copy!r}, {name!r}'s copy to stereo, is already on the rig")
    return Addition(
        track=track, name=name, uri=uri, after=chain[index - 1] if index else None, chain=chain,
        expected=chain[:index] + [name] + ([copy] if copy else []) + chain[index:], track_id=ids[track],
        before_id=ids[before] if before is not None else 0, add_to_back=before is None, copy=copy,
    )


def _read_compiled(path: Any) -> dict[str, Any]:
    import json

    config = json.loads(Path(path).read_text())
    if not isinstance(config, dict):
        raise ValueError(f"{path} is not a config")
    return config


def _write_compiled(name: str, compiled: dict[str, Any]) -> Path:
    from .paths import runtime_dir
    from .rig import write_compiled

    return write_compiled(name, compiled, runtime_dir())


@dataclass(frozen=True)
class Switching:
    """What a switch reaches outside the agent and Sushi, so its tests can
    stand each in: compiling the next rig (`RigError` with the reason),
    reading the running rig's compiled config by the path the state file
    gives, writing the next one's where `up` would, the state file, and
    where the rig's plugins are, which the next rig's must all be in
    (`rig.plugins_refusal`): the folder `up` gives Sushi, under the root. The
    one seam for the state file and for compiling a rig to switch to, for the
    route and the Program Change alike: `midi_listener.World` no longer has
    its own (found by review: a test's `World(read_state=...)` had stopped
    steering the switch while looking as if it did)."""

    compile: Callable[[str, Path, "str | None"], dict[str, Any]] = (
        lambda name, root, machine: compile_named_rig(name, root, Path(machine) if machine else None))
    read_config: Callable[[Any], dict[str, Any]] = _read_compiled
    write_config: Callable[[str, dict[str, Any]], Any] = _write_compiled
    read_state: Callable[[], "dict[str, Any] | None"] = read_state
    write_state: Callable[[dict[str, Any]], None] = write_state
    plugins: Callable[[Path], Path] = plugins_folder

# =========================================================================== #
# Saving the running rig (S4): the decisions, pure
# =========================================================================== #

# How long `POST /rig/save` gives the write of the rig file and the git read
# beside it (`take.library_state`, three git calls of up to 10 s each, which
# on a small checkout take milliseconds). The capture before it is a Sushi
# call, held to `LIVE_TIMEOUT`. A guess with room in it, not a measurement.
SAVE_TIMEOUT = 20.0


def save_request(body: Any) -> str | None:
    """The name a save asks for (a save-as), None for the running rig's own
    file, or `AgentError` (400). The name is checked as `save.validate_name`
    checks it, since it becomes a file name; a key beside it is refused, as
    undo's body is, because a request ignored while looking understood is
    how the wrong file gets written."""
    from .save import SaveError, validate_name

    if body is None:
        return None
    if not isinstance(body, dict) or not set(body) <= {"name"}:
        raise AgentError(f'the body of a save must be empty, or {{"name": "<rig>"}}, not {body!r}')
    name = body.get("name")
    if name is None:
        return None
    try:
        return validate_name(name)
    except SaveError as exc:
        raise AgentError(str(exc)) from exc


def _touched(entries: list[dict[str, Any]]) -> tuple[dict[tuple[str, str], Any], dict[str, Any], bool]:
    """What log entries set, each with the value it had before the first of
    them: `(values, bypasses, structural)`, `values` keyed by `(processor,
    parameter)` and `bypasses` by processor, the earliest `previous` (None
    where Sushi did not say), and whether any of them added, removed or
    moved a plugin. An undo counts as what it applied. Pure."""
    values: dict[tuple[str, str], Any] = {}
    bypasses: dict[str, Any] = {}
    structural = False
    for entry in entries:
        op, args, previous = entry.get("op"), entry.get("args") or {}, entry.get("previous")
        if op == "undo":
            op = args.get("of")
        if op in ("add", "remove", "move"):
            structural = True
        elif op == "parameter" and isinstance(args.get("processor"), str) and isinstance(args.get("parameter"), str):
            values.setdefault((args["processor"], args["parameter"]), previous)
        elif op == "parameters" and isinstance(args.get("processor"), str) and isinstance(args.get("parameters"), dict):
            for name in args["parameters"]:
                values.setdefault((args["processor"], name),
                                  previous.get(name) if isinstance(previous, dict) else None)
        elif op == "bypass" and isinstance(args.get("processor"), str):
            bypasses.setdefault(args["processor"], previous)
    return values, bypasses, structural


def discard_plan(plan: "rigfile.SwitchPlan", discarded: list[dict[str, Any]], upcoming: dict[str, Any],
                 reading: dict[str, Any]) -> "rigfile.SwitchPlan | rigfile.NeedsRestart":
    """`plan`, a live switch into a rig file, with the unsaved work being
    discarded put back (#127, review): `rigfile.diff` sets only what differs
    between the two configs, and leaves a value neither states as the rig has
    it, so a knob turned and then discarded on a channel the switch does not
    change kept playing, and the switch's baseline hid it. Pure.

    Each parameter and bypass the `discarded` entries set (`_touched`) is set
    to the next config's value where it states one, else to the value it had
    before the first of them (the rig's as its file gave it), unless the plan
    sets it already or the rig (`reading`) holds that value; a processor the
    next rig does not have is left to the plan, which removes it. A value
    whose before Sushi did not report cannot be put back live: that is a
    restart (`discarded`, the agent's reason like `failed` and `unverified`,
    not one of `rigfile.diff`'s, so not in `RESTART_REASONS`). A discarded add, remove or move leaves chains the
    running config does not have, which `diff` against the rig refuses as
    `drifted` before this is reached, so it restarts, and the restart reloads
    the file. A processor the plan adds (a replace: removed, then added
    fresh) is left to the plan, since the value from before belongs to the
    instance removed. The steps go in front of the plan's closing
    bypass-offs, parameters then bypasses, marked `"restores": "discarded"`."""
    values, bypasses, _ = _touched(discarded)
    stated: dict[str, dict[str, Any]] = {}
    for entry in upcoming.get("initial_state") or []:
        if isinstance(entry, dict) and isinstance(entry.get("processor"), str):
            stated[entry["processor"]] = entry
    names = {p.get("name") for t in upcoming.get("tracks") or [] for p in t.get("plugins") or []}
    names |= {t.get("name") for t in upcoming.get("tracks") or []}
    planned_values = {(s.args.get("processor"), s.args.get("parameter")) for s in plan.steps if s.op == "parameter"}
    planned_bypass = {s.args.get("processor") for s in plan.steps if s.op == "bypass"}
    # A processor the plan adds is a fresh instance, at the plugin's
    # defaults and then the next config's values: what the old one had
    # before the discarded work is not its to take (re-review of #162).
    added = {s.args.get("name") for s in plan.steps if s.op == "add"}
    added |= {s.args.get("copy") for s in plan.steps if s.op == "add" and s.args.get("copy")}
    live = reading.get("processors") or {}
    extra_values, extra_bypass, unknown = [], [], []
    for (processor, parameter), before in sorted(values.items()):
        if processor not in names or processor in added or (processor, parameter) in planned_values:
            continue
        target = ((stated.get(processor) or {}).get("parameters") or {}).get(parameter, before)
        if not isinstance(target, (int, float)) or isinstance(target, bool):
            unknown.append(f"{processor}'s {parameter}")
            continue
        now = ((live.get(processor) or {}).get("parameters") or {}).get(parameter)
        if isinstance(now, (int, float)) and abs(now - target) <= rigfile.APPLIED_TOLERANCE:
            continue
        extra_values.append(rigfile.SwitchStep("parameter", {"processor": processor, "parameter": parameter,
                                                             "value": float(target), "restores": "discarded"}))
    for processor, before in sorted(bypasses.items()):
        if processor not in names or processor in added or processor in planned_bypass:
            continue
        target = (stated.get(processor) or {}).get("bypassed", before)
        if not isinstance(target, bool):
            unknown.append(f"{processor}'s bypass")
            continue
        if (live.get(processor) or {}).get("bypassed") == target:
            continue
        extra_bypass.append(rigfile.SwitchStep("bypass", {"processor": processor, "bypassed": target,
                                                          "restores": "discarded"}))
    if unknown:
        return rigfile.NeedsRestart((rigfile.RestartReason(
            "discarded", f"the unsaved work being dropped set {', '.join(unknown)}, whose value before it Sushi "
                         "did not report"),))
    # In front of the plan's closing bypass-offs (diff's step 7): a pedal
    # comes in only once its parameters are what it plays with.
    steps = list(plan.steps)
    cut = len(steps)
    while cut > 0 and steps[cut - 1].op == "bypass" and steps[cut - 1].args.get("bypassed") is False:
        cut -= 1
    return rigfile.SwitchPlan(tuple(steps[:cut]) + tuple(extra_values) + tuple(extra_bypass) + tuple(steps[cut:]))


def discard_said(discarded: list[dict[str, Any]], answer: dict[str, Any]) -> dict[str, Any] | None:
    """What became of the unsaved work a revert or a load dropped, for the
    answer: how many changes, whether any was structural, how the switch put
    it back (`live`: the values it set, by the switch's own steps; or a
    restart, which reloads the file), and the values it set. Pure."""
    if not discarded:
        return None
    _, _, structural = _touched(discarded)
    restored = [s for s in answer.get("plan") or [] if isinstance(s, dict) and s.get("restores") == "discarded"]
    how = answer.get("how")
    return {"changes": len(discarded), "structural": structural, "how": how, "restored": restored,
            "note": (f"{len(discarded)} unsaved change{'s' if len(discarded) != 1 else ''} dropped: "
                     + ("the live switch put back each value they set" if how == "live" else
                        "the rig restarted from its file, which drops them"
                        + (" (a plugin added, removed or moved cannot be put back live)" if structural else "")))}


def channel_load_request(body: Any) -> tuple[dict[str, str], bool, bool, str | None]:
    """`(source, discard, drop_midi, rig)` from a load's body, or
    `AgentError` (400): `from`, `{"rig", "channel"}`, each text (the rig is
    looked up in config/rigs/ by the route, never made a path as given);
    `discard`, `drop_midi` and `with_amp`, true or absent (`with_amp` is
    read by `load_with_amp`); `rig`, the rig the page
    meant to load onto, text or absent (review of PR #234: a Program Change
    between the page's render and the click must not land the load on
    another rig's channel of the same name). Anything beside them is
    refused."""
    keys = {"from", "discard", "drop_midi", "rig", "with_amp"}
    shape = ('the body of a load is {"from": {"rig": "<rig>", "channel": "<channel>"}}, with "rig": "<rig>" for '
             'the rig it is meant for, "discard": true to drop unsaved work, "drop_midi": true to drop MIDI '
             'mappings it would orphan and "with_amp": true to bring the source rig\'s amp')
    if not isinstance(body, dict) or not set(body) <= keys:
        raise AgentError(f"{shape}, not {body!r}")
    source = body.get("from")
    if not isinstance(source, dict) or set(source) != {"rig", "channel"} or not all(
            isinstance(source[k], str) and source[k].strip() for k in ("rig", "channel")):
        raise AgentError(f"{shape}, not {body!r}")
    rig = body.get("rig")
    if rig is not None and not (isinstance(rig, str) and rig.strip()):
        raise AgentError(f"'rig' must name the rig the load is for, not {rig!r}")
    flags = []
    for key in ("discard", "drop_midi", "with_amp"):
        value = body.get(key, False)
        if not isinstance(value, bool):
            raise AgentError(f"{key!r} must be true or false, not {value!r}")
        flags.append(value)
    return {"rig": source["rig"], "channel": source["channel"]}, flags[0], flags[1], rig


def load_with_amp(body: Any) -> bool:
    """Whether a load's body asks for the source rig's amp too (`"with_amp":
    true`, #246), once `channel_load_request` has checked its shape. Pure."""
    return isinstance(body, dict) and body.get("with_amp") is True


def load_refusal(state: dict[str, Any], log: list[dict[str, Any]], doing: str = "loading a channel",
                 again: str = "load") -> tuple[str, str] | None:
    """`(reason, "unsaved")` when loading a channel onto the running rig must
    wait, or None. Pure. A revert's rule (`revert_refusal`, panel aside,
    which the route has refused already): the load writes the rig file and
    plays it, so live work no save has written would be lost by it. A
    channel's removal (#190) is held by the same rule, in its own words
    (`doing`, and the verb to do it `again` with)."""
    unsaved = unsaved_entries(log, since=state.get("started_at"))
    if not unsaved:
        return None
    name = Path(str(state.get("rig") or "the rig")).stem
    newest = unsaved[-1]
    count = f"{len(unsaved)} change{'s' if len(unsaved) != 1 else ''}"
    return (f"{name} is running with {count} made through the agent and not saved (the newest: "
            f"{describe_change(newest)}, at {newest.get('at')}), and {doing} writes the rig file and "
            f'plays it: Save first, or {again} with discard ({{"discard": true}}) to drop them', "unsaved")


def running_amp(state: dict[str, Any], raw: Any) -> str | None:
    """The model of the amp in front of `guitar-in` for the running rig, or
    None when no amp runs (`--no-amp`, or a rig with none). Pure over the
    state file and the rig file's document."""
    if state.get("amp_requested") is False:
        return None
    amp = raw.get("amp") if isinstance(raw, dict) else None
    model = state.get("amp_model") or (amp.get("model") if isinstance(amp, dict) else None)
    return model if isinstance(model, str) and model else None


def _log_time(stamp: Any) -> str | None:
    """An ISO time as the log writes its `at` (UTC, to the second:
    `_record`), or None. Log times in that one form compare as strings, so
    `since` is put into it once and no entry's time is parsed."""
    try:
        when = datetime.fromisoformat(str(stamp))
    except ValueError:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def since_baseline(log: list[dict[str, Any]], since: Any = None) -> tuple[int, dict[str, Any] | None]:
    """`(start, stop)`: where in the log the running rig's own history begins
    -- after the newest save or rig switch, and after `since`, the run's
    start (the state file's `started_at`: a restart reloaded the file) --
    and the save or switch entry that ended the walk, None if none did.
    One walk back from the newest entry, so a caller under the live lock pays
    for the entries since the last save, not the whole log, and no time but
    `since` is parsed (review of #32). The one walk `live_edits` and
    `unsaved_entries` share. Pure."""
    cut = _log_time(since) if since is not None else None
    start = len(log)
    while start > 0:
        entry = log[start - 1]
        at = entry.get("at")
        if cut is not None and isinstance(at, str) and at < cut:
            return start, None
        # A save Undo took back (#209: its file is as before it again) is
        # no baseline: what came before it is unsaved once more.
        if entry.get("op") in ("rig", "save") and not entry.get("withdrawn"):
            return start, entry
        start -= 1
    return start, None


def live_edits(log: list[dict[str, Any]], since: Any = None) -> dict[str, Any]:
    """Which plugins the log says were added to and removed from the running
    rig: what `rigfile.fold_state` needs to write a chain's plugins, and not
    only their values, into its rig file. Pure over the log.

        {"added": {name: {"track", "uri", "defaults"}}, "removed": {name: track}}

    Read from the newest save or rig switch on, and from `since`, the run's
    start (the state file's `started_at`: a restart reloaded the file, so
    what came before it is not on the rig), by `since_baseline`, the one walk
    this shares with `unsaved_entries`. Before a switch was another rig; before
    a save is in the file, except what the next save needs of it, which the
    save's entry carries (`args.added`, the plugins added live it wrote, with
    their defaults) and this starts from.

    Then oldest first, so each name ends as its last edit left it. An add is
    `added`, with the defaults it read; a remove takes the name out of
    `added` and puts it in `removed`; an undo counts as what it applied (an
    add's undo is a remove, a remove's an add, with the defaults of an
    earlier add of that plugin under that name, if those are known). A name
    can be in both: taken out of the file and added again, which `fold_state`
    tells by its track and URI.

    This only says what the agent saw. `fold_state` takes it as the
    explanation of a capture that differs from the file, never as a change
    to make: a plugin the capture does not hold is not written whatever the
    log says, and one it holds that is neither the file's nor here is
    refused as a capture of a different rig."""
    start, stop = since_baseline(log, since)
    seed = ((stop.get("args") or {}).get("added") or {}) if stop is not None and stop.get("op") == "save" else {}
    added: dict[str, dict[str, Any]] = {
        name: dict(said) for name, said in seed.items() if isinstance(said, dict)}
    removed: dict[str, str] = {}
    defaults: dict[tuple[str, str], Any] = {(name, said.get("uri")): said.get("defaults")
                                             for name, said in added.items()}
    for entry in log[start:]:
        op, args, previous = entry.get("op"), entry.get("args") or {}, entry.get("previous")
        if op == "undo":
            op = {"add": "remove", "remove": "add"}.get(args.get("of"))  # what it applied
            if op == "add" and isinstance(args.get("name"), str):
                key = (args["name"], args.get("uri"))
                added[args["name"]] = {"track": args.get("track"), "uri": args.get("uri"),
                                       "defaults": defaults.get(key)}
                continue
        if op == "add" and isinstance(args.get("name"), str):
            defaults[(args["name"], args.get("uri"))] = args.get("defaults")
            added[args["name"]] = {"track": args.get("track"), "uri": args.get("uri"),
                                   "defaults": args.get("defaults")}
        elif op == "remove" and isinstance(args.get("processor"), str) and isinstance(previous, dict):
            added.pop(args["processor"], None)
            removed[args["processor"]] = previous.get("track")
    return {"added": added, "removed": removed}


def save_refusal(state: dict[str, Any], described: dict[str, Any]) -> tuple[str, str] | None:
    """`(reason, code)` for a running rig the agent will not save, or None.
    Pure over the state file and `describe_rig`'s answer."""
    from .rig import runs_panel

    if not described.get("running"):
        return (f"no rig is running to save ({described.get('message') or 'nothing is up'})", "no_rig")
    if runs_panel(state):
        return ("the rig runs with its panel, whose faders reach the amp through the listener, so the agent "
                "does not know what the amp is at and would save a guess: save from the panel's SAVE button, "
                "or run the rig headless", "panel")
    rig = state.get("rig")
    if not isinstance(rig, str) or not rig:
        name = state.get("config_name") or described.get("config_name") or "the running config"
        return (f"{name} is a v1 config (config/{name}.json), not a rig of channels started with --rig: "
                "a save from here writes a rig file in place, keeping its comments, and a v1 config is "
                "saved as a new config/<name>.json from the rig.yaml it was built from, with the old one "
                f"archived. Save it on the rig with `sushi-rig save <name> --rig <its rig.yaml>`", "v1")
    return None


def rig_file_of(state: dict[str, Any], root: Path) -> Path:
    """The running rig's file: the state file's `rig`, which `up --rig` and a
    live switch write relative to the checkout (`config/rigs/<name>.yaml`)."""
    path = Path(state["rig"])
    return path if path.is_absolute() else Path(root) / path


def renamed_running(running: dict[str, Any], compiled: dict[str, Any]) -> dict[str, Any]:
    """The running config, as the config of the run renamed by a save-as
    whose file differs from it in what only a restart loads: everything Sushi
    loaded (tracks, host, cc_mappings) and the agent serves from it
    (`_midi_agent`, the controller in `_meta`) as it runs, under the new
    file's name and source. Pure."""
    from .rigfile import MIDI_CONTROLLER

    out = copy.deepcopy(running)
    meta = {k: v for k, v in (compiled.get("_meta") or {}).items() if k != MIDI_CONTROLLER}
    if MIDI_CONTROLLER in (running.get("_meta") or {}):
        meta[MIDI_CONTROLLER] = running["_meta"][MIDI_CONTROLLER]
    out["_meta"] = meta
    return out


def saved_as_state(state: dict[str, Any], name: str, config: Any) -> dict[str, Any]:
    """The state file after a save-as to rig `name`, whose compiled config is
    at `config`: `switched_state`'s record (the same supervisor, children and
    options, now naming the new file), saying when it was saved as rather
    than switched. Pure."""
    after = {**switched_state(state, name, config), "saved_as_at": datetime.now(timezone.utc).isoformat()}
    if "amp_requested" in state:
        # The same rig under a new name, not a switch: a run with no amp
        # keeps none (`switched_state` drops it for a switch, #246).
        after["amp_requested"] = state["amp_requested"]
    if "switched_at" in state:
        after["switched_at"] = state["switched_at"]
    else:
        after.pop("switched_at")
    return after


# --------------------------------------------------------------------------- #
# Recording from the web UI (S7): the decisions, pure
# --------------------------------------------------------------------------- #

# What each refusal the recorder gives is answered with. `failed` (a take
# tried that did not start, or an unexpected error) is 502, the default.
RECORD_CODES = {
    "recording": web.HTTPConflict if web else None,
    "not_recording": web.HTTPConflict if web else None,
    "unavailable": web.HTTPConflict if web else None,
    "name": web.HTTPBadRequest if web else None,
    "bad_request": web.HTTPInternalServerError if web else None,  # the agent's own request: a bug
}


# What GET /record's `state` may be; a recorder that says anything else is a
# bug answered 502, not passed to a page that draws from it.
RECORD_STATES = ("recording", "idle", "unavailable")


def record_request(body: Any) -> str | None:
    """The name a start asks for, None for one the agent is to make
    (`take.default_name`), or `AgentError` (400): a name the take rule
    refuses (`take.check_name`, the rule a config's name keeps), or a body
    with anything but `name`."""
    from .take import check_name

    if body is None:
        return None
    if not isinstance(body, dict) or not set(body) <= {"name"}:
        raise AgentError(f'the body of a record must be empty, or {{"name": "<take>"}}, not {body!r}')
    name = body.get("name")
    if name is None or name == "":
        return None
    problems = check_name(name)
    if problems:
        raise AgentError(problems[0])
    return name


def record_refusal(state: dict[str, Any], described: dict[str, Any]) -> tuple[str, str] | None:
    """`(reason, code)` for a run the agent does not record, or None. Pure
    over the state file and `describe_rig`'s answer, in the order a player
    would fix them: no rig, then the panel (whose listener records, and
    says why for a v1 run itself), then a v1 run."""
    from .rig import runs_panel

    if not described.get("running"):
        return (f"no rig is running to record ({described.get('message') or 'nothing is up'})", "no_rig")
    if runs_panel(state):
        return ("the rig runs with its panel, whose record bar owns recording (the listener records the "
                "take): record from the panel's RECORD, or start the rig from here (headless) to record "
                "from here", "panel")
    rig = state.get("rig")
    if not isinstance(rig, str) or not rig:
        name = state.get("config_name") or described.get("config_name") or "the running config"
        return (f"{name} is a v1 config, not a rig of channels started with --rig: a take records each "
                "channel's own output beside its dry input, and only a rig of channels gives each channel "
                "an output of its own (a v1 rig sums its tracks into the monitor bus). Start it as a rig of "
                "channels (up --rig) to record", "v1")
    return None


def take_rig_name(state: dict[str, Any]) -> str:
    """The running rig's name as a take's default name carries it: its rig
    file's, which is what the picker and a save call it. For a state file
    `record_refusal` lets through, which names a rig file. Pure."""
    return Path(state["rig"]).stem


def recorder_run(state: dict[str, Any], root: Path, takes_dir: Path) -> dict[str, Any]:
    """The run's files as the recorder plans a take from them
    (`recorder_process.Run`), from the state file as it is now: a live
    switch and a save-as rewrite it. Absolute, since the recorder's working
    directory is not the agent's."""
    def absolute(value: Any) -> str | None:
        if not isinstance(value, str) or not value:
            return None
        path = Path(value)
        return str(path if path.is_absolute() else Path(root) / path)

    return {
        "root": str(Path(root).resolve()),
        "rig": str(rig_file_of(state, root)) if isinstance(state.get("rig"), str) and state["rig"] else None,
        "config": absolute(state.get("config")),
        "machine": absolute(state.get("machine")),
        "takes_dir": str(Path(takes_dir).resolve()),
    }


# What opens a folder in the desktop's file manager (#189), tried in this
# order: each a program and the arguments before the folder. `xdg-open` is
# the freedesktop one every desktop provides; `gio open` is GNOME's, there
# when xdg-utils is not.
FOLDER_OPENERS: tuple[tuple[str, ...], ...] = (("xdg-open",), ("gio", "open"))
# How long a POST /takes/open waits for the opener to say how it went. Both
# hand the folder to the file manager and return at once; one that does not
# (an opener that runs the file manager itself) is left running, reaped by a
# thread of its own, and the answer says it was asked.
OPEN_WAIT = 5.0


def folder_opener(which: Callable[[str], str | None] | None = None) -> list[str] | None:
    """The first of `FOLDER_OPENERS` installed here, as the argument list
    before the folder (the program by its full path), or None. `which` is
    `shutil.which`, which a test replaces."""
    import shutil

    which = which or shutil.which
    for program, *args in FOLDER_OPENERS:
        found = which(program)
        if found:
            return [found, *args]
    return None


def open_folder(opener: Sequence[str], folder: Path, wait: float = OPEN_WAIT) -> dict[str, Any]:
    """Run `opener` on `folder` and say how it went: `{"exited": code,
    "said": its stderr, last part}`, or `{"exited": None}` if it was still
    running after `wait` seconds. Blocking: the agent calls it in a thread.

    No shell: the arguments are a list, and the folder is the agent's own
    (`Agent.takes_dir`), absolute, so it cannot be read as an option. Its
    own session, so it is not in the agent's process group and outlives the
    agent; nothing of it is read but what it writes to stderr, kept in a
    file, so an opener that runs on never blocks on a full pipe. One still
    running after `wait` is waited for by a daemon thread, so it never
    lingers as a zombie."""
    import subprocess
    import tempfile

    with tempfile.TemporaryFile() as err:
        child = subprocess.Popen([*opener, str(folder)], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                 stderr=err, start_new_session=True, close_fds=True)
        try:
            code = child.wait(timeout=wait)
        except subprocess.TimeoutExpired:
            threading.Thread(target=child.wait, name="takes-folder-opener", daemon=True).start()
            return {"exited": None}
        err.seek(0)
        said = " ".join(err.read()[-2000:].decode("utf-8", "replace").split())
    return {"exited": code, "said": said}


# =========================================================================== #
# Rig files (the New rig screen): the decisions, pure
# =========================================================================== #

class RigFileRefused(AgentError):
    """A rig file the agent will not write (409), with its `code`, and
    `extra` for the answer beside the reason (`machine_hint`)."""

    def __init__(self, reason: str, code: str, extra: dict[str, Any] | None = None):
        super().__init__(reason)
        self.code = code
        self.extra = extra or {}


# Log entries that did not change the running rig: a save (the file has
# what came before it), a rig switch (what came before was another rig's), a
# take's start or stop, and a skip's marker.
_NOT_LIVE_CHANGES = ("save", "rig", "record", "stale")


def unsaved_entries(log: list[dict[str, Any]], since: Any = None) -> list[dict[str, Any]]:
    """The log's changes to the running rig that no save has written: every
    entry after the newest save or rig switch, and after `since` (the run's
    start: a restart reloaded the file), that is not a save, a switch, a
    take or a skip's marker. Pure over the log, through `since_baseline`, the
    walk `live_edits` takes.

    Counted, not netted out: a knob turned and turned back is two entries,
    though the rig is as the file has it. That errs on the side of keeping
    work: the refusal it causes is answered by a Save, which writes what is
    playing, and nothing is lost by it. And only what the agent saw: the
    panel's changes and anything before the agent started are not here."""
    start, _ = since_baseline(log, since)
    return [entry for entry in log[start:] if entry.get("op") not in _NOT_LIVE_CHANGES]


def rigfile_refusal(state: dict[str, Any], log: list[dict[str, Any]]) -> tuple[str, str] | None:
    """`(reason, code)` when an edit of the running rig's own file must wait,
    or None. Pure over the state file and the log.

    An edit of the file the rig runs from takes effect only at a restart,
    which loads the file: so unsaved live work (knobs, bypasses, plugins
    added or moved through the agent since the last save) would be dropped
    by the very restart the edit offers. Save itself would still work after
    the edit -- a capture says nothing of a track the rig does not have yet,
    so `fold_state` keeps the new channel, copied chain and all
    (test_agent_rigfiles.py) -- so this is about the restart, not the file.
    Refused until it is saved, rather than written with a warning: a refusal
    costs one tap on Save, and a warning would be read once and the work
    lost at the restart the same answer offers. `unsaved`, naming
    the newest change. With the panel running (`panel`), the agent cannot
    see the panel's changes at all, so whether there is work to lose cannot
    be known here."""
    from .rig import runs_panel

    name = Path(str(state.get("rig") or "the rig")).stem
    if runs_panel(state):
        return (f"{name} runs with its panel, whose changes the agent cannot see, so it cannot tell whether "
                "the rig holds unsaved work that a restart into the edited file would drop: save from the "
                "panel's SAVE and stop the rig, or run it headless, then edit it", "panel")
    unsaved = unsaved_entries(log, since=state.get("started_at"))
    if unsaved:
        newest = unsaved[-1]
        what = describe_change(newest)
        count = f"{len(unsaved)} change{'s' if len(unsaved) != 1 else ''}"
        return (f"{name} is running with {count} made through the agent and not saved (the newest: {what}, "
                f"at {newest.get('at')}). The edit takes effect only when the rig restarts from its file, "
                "and that restart would drop them: Save first (it writes what is playing into the file), "
                "then make the edit", "unsaved")
    return None


def _warned_run(described: dict[str, Any]) -> tuple:
    """The run a start's warnings are of: its group and start, which a live
    switch keeps (the same Carla plays on) and a restart changes. Pure."""
    return (bool(described.get("running")), described.get("pgid"), described.get("started_at"))


def _run_identity(described: dict[str, Any]) -> tuple:
    """Which run `describe_rig`'s answer is of: up or not, its rig and when it
    started, so a lifecycle command that changed nothing is told from one
    that did (re-review of PR #235). Pure."""
    return (bool(described.get("running")), described.get("config_name"), described.get("started_at"),
            described.get("pgid"))


def run_changed(before: dict[str, Any], now: dict[str, Any]) -> bool:
    """Whether the state file `now` is of another run than `before`: a
    restart (`started_at`) or a switch (`config`, `rig`) since. Pure."""
    return any(now.get(key) != before.get(key) for key in ("started_at", "config", "rig"))


def assignment_not_live(running: dict[str, Any] | None, compiled: dict[str, Any],
                        edits: dict[str, Any]) -> list[dict[str, str]]:
    """Why a pedal assignment (#192) cannot be applied to the running rig
    now, as `{code, detail}` reasons, or [] when it can. Pure.

    Live only when the edited file's compile leaves Sushi's `midi` section
    as the running config has it (Sushi loads cc_mappings only with the
    config), and every binding the agent would serve names a processor the
    running rig has: the running config's tracks and plugins, less those
    removed and plus those added through the agent since the last save or
    switch (`edits`, `live_edits`'). A channel added to the file and not yet
    restarted, or a plugin taken out live, is not there to switch (#192's
    review): those wait for the restart."""
    from .rigfile import AGENT_BINDINGS, SEND_SUFFIX

    if not isinstance(running, dict):
        return [{"code": "unreadable", "detail": "the running rig's compiled config could not be read"}]
    reasons = []
    if (running.get("midi") or {}) != (compiled.get("midi") or {}):
        reasons.append({"code": "midi", "detail": "the cc_mappings (Sushi's midi section) differ"})
    on = _on_the_running_rig(running, edits)
    missing = []
    for binding in compiled.get(AGENT_BINDINGS) or []:
        if not isinstance(binding, dict):
            continue
        if isinstance(binding.get("bypass"), str):
            name = binding["bypass"].split("/")[-1]
        elif isinstance(binding.get("mute"), str):
            name = binding["mute"] + SEND_SUFFIX  # a mute bypasses the send (#210)
        elif isinstance(binding.get("snap"), str):
            name = binding["snap"] + SEND_SUFFIX
        else:
            continue
        if name not in on and name not in missing:
            missing.append(name)
    if missing:
        reasons.append({"code": "not_running", "detail": (
            f"{', '.join(missing)} {'is' if len(missing) == 1 else 'are'} in the file but not on the running rig "
            "(added to the file since it started, or taken out live): the restart plays the file")})
    return reasons


def _on_the_running_rig(running: dict[str, Any], edits: dict[str, Any] | None) -> set:
    """The tracks and processors the running config has, less those removed
    and plus those added live since (`live_edits`). Pure."""
    on = set()
    for track in running.get("tracks") or []:
        if isinstance(track, dict):
            on.add(track.get("name"))
            on.update(p.get("name") for p in track.get("plugins") or [] if isinstance(p, dict))
    on -= set((edits or {}).get("removed") or {})
    on |= set((edits or {}).get("added") or {})
    return on


# Sushi's gRPC carries a mapping's ranges as float32 (sushi_rpc.proto's
# MidiCCConnection), and a compiled config rounds them to 6 places: a range
# read back is the same one within this.
CC_RANGE_TOLERANCE = 1e-6


def cc_identity(mapping: dict[str, Any]) -> tuple:
    """What makes a cc_mapping the one it is, but for its ranges: the
    parameter, the CC, Sushi's input, the channel (from 0, or "all") and the
    mode. Pure."""
    channel = mapping.get("channel")
    return (mapping.get("plugin_name"), mapping.get("parameter_name"), mapping.get("cc_number"),
            mapping.get("port", 0), channel if isinstance(channel, int) and not isinstance(channel, bool) else "all",
            "relative" if mapping.get("mode") == "relative" else "absolute")


def cc_same(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """Whether two cc_mappings are one, ranges within `CC_RANGE_TOLERANCE`.
    One without its ranges is the same as nothing. Pure."""
    if cc_identity(a) != cc_identity(b):
        return False
    try:
        return all(abs(float(a[k]) - float(b[k])) <= CC_RANGE_TOLERANCE for k in ("min_range", "max_range"))
    except (KeyError, TypeError, ValueError):
        return False


def cc_disagreement(held: list[dict[str, Any]], adds: list[dict[str, Any]], removes: list[dict[str, Any]],
                    *, exact: bool = False) -> list[str]:
    """Where Sushi's own list (`held`, `live.read_live_cc`'s) does not show a
    live change, each said (#210's review): an add not there with its
    ranges, a remove still there, ranges and all (one that is also an add,
    which a change of nothing but rounding is, is not counted). With
    `exact`, also every mapping `held` has that `adds` does not: the list
    is to be `adds` and nothing else. Pure."""
    def what(m: dict[str, Any]) -> str:
        return (f"cc {m.get('cc_number')} to {m.get('plugin_name')}'s {m.get('parameter_name')} "
                f"[{m.get('min_range')}, {m.get('max_range')}]")

    found = [f"{what(m)} not there" for m in adds if not any(cc_same(m, h) for h in held)]
    found += [f"{what(m)} still there" for m in removes
              if not any(cc_same(m, a) for a in adds) and any(cc_same(m, h) for h in held)]
    if exact:
        found += [f"{what(h)} there and not in it" for h in held if not any(cc_same(h, m) for m in adds)]
    return found


def live_cc_plan(running: dict[str, Any], compiled: dict[str, Any], edits: dict[str, Any] | None,
                 ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]] | str:
    """How to take the running Sushi's cc_mappings to the edited file's live
    (#210): `(removes, adds)`, each a list of cc_mappings entries, or why it
    cannot be done live. Pure.

    Not live where Sushi did not open the MIDI input a mapping is on (it
    opens `midi_inputs` only at start, 1 without the key: Sushi's port 0
    was there for a rig with none, the laptop, 2026-11-23), or where a
    mapping's processor is not on the running rig."""
    def key(mapping: dict[str, Any]) -> str:
        return json.dumps(mapping, sort_keys=True)

    was = [m for m in ((running.get("midi") or {}).get("cc_mappings") or []) if isinstance(m, dict)]
    now = [m for m in ((compiled.get("midi") or {}).get("cc_mappings") or []) if isinstance(m, dict)]
    removes = [m for m in was if key(m) not in {key(n) for n in now}]
    adds = [m for m in now if key(m) not in {key(n) for n in was}]
    opened = (running.get("host_config") or {}).get("midi_inputs") or 1
    beyond = sorted({m.get("port", 0) for m in adds if not isinstance(m.get("port", 0), int)
                     or m.get("port", 0) >= opened}, key=str)
    if beyond:
        return (f"the map is on Sushi's MIDI input {', '.join(map(str, beyond))}, and the running Sushi opened "
                f"{opened} at its start")
    on = _on_the_running_rig(running, edits)
    missing = sorted({str(m.get("plugin_name")) for m in adds} - {str(n) for n in on})
    if missing:
        return f"{', '.join(missing)} is not on the running rig, so Sushi cannot map a CC to it until it restarts"
    return removes, adds


def running_with_map(running: dict[str, Any], compiled: dict[str, Any], *, midi: bool) -> dict[str, Any]:
    """The running config as it is after an assignment applied live (#192,
    #210): what Sushi loaded, with the edited file's bindings for the agent
    and the controller they are read from, and, with `midi`, the cc_mappings
    now made live as its `midi` section (the host stays what Sushi opened).
    Pure."""
    from .rigfile import AGENT_BINDINGS, MIDI_CONTROLLER

    updated = copy.deepcopy(running)
    updated.pop(AGENT_BINDINGS, None)
    if compiled.get(AGENT_BINDINGS):
        updated[AGENT_BINDINGS] = copy.deepcopy(compiled[AGENT_BINDINGS])
    meta = dict(updated.get("_meta") or {}) if isinstance(updated.get("_meta"), dict) else {}
    meta.pop(MIDI_CONTROLLER, None)
    if MIDI_CONTROLLER in (compiled.get("_meta") or {}):
        meta[MIDI_CONTROLLER] = compiled["_meta"][MIDI_CONTROLLER]
    updated["_meta"] = meta
    if midi:
        updated.pop("midi", None)
        if compiled.get("midi"):
            updated["midi"] = copy.deepcopy(compiled["midi"])
        # Sushi opens one MIDI input without `midi_inputs`; a file that now
        # asks for no more than it opened is what runs, and says so, so the
        # next switch does not read a host change that is not one.
        host = updated.get("host_config") if isinstance(updated.get("host_config"), dict) else None
        wanted = (compiled.get("host_config") or {}).get("midi_inputs")
        if host is not None and isinstance(wanted, int) and wanted <= (host.get("midi_inputs") or 1):
            updated["host_config"] = {**host, "midi_inputs": max(wanted, host.get("midi_inputs") or 1)}
    return updated


def map_behind(running: dict[str, Any] | None, compiled: dict[str, Any] | None) -> dict[str, Any] | None:
    """Whether the running rig's pedal mappings are behind its file (#210):
    `{"midi": bool, "agent": bool, "ranges": bool}` saying which half
    differs between the running config and a compile of the file as it is
    now, or None where either could not be read. Pure: Sushi's cc_mappings
    (`midi`, and `ranges` when they differ only in their ranges,
    `cc_ranges_only`), and the agent's bindings (`agent`, which an
    assignment applies at once, so it differs only after an edit by hand,
    or in a volume's heel binding's gains when the level moved)."""
    from .rigfile import AGENT_BINDINGS

    if not isinstance(running, dict) or not isinstance(compiled, dict):
        return None
    midi = (running.get("midi") or {}) != (compiled.get("midi") or {})
    return {"midi": midi,
            "agent": (running.get(AGENT_BINDINGS) or []) != (compiled.get(AGENT_BINDINGS) or []),
            "ranges": midi and cc_ranges_only(running, compiled)}


def cc_ranges_only(running: dict[str, Any], compiled: dict[str, Any]) -> bool:
    """Whether the two configs' cc_mappings are the same mappings, one for
    one, and differ only in their ranges (#210's review): a Save that moved
    a channel's `level:` moves its volume's and blends' ranges with it, and
    that is not a new assignment to wait for. Pure."""
    def mappings(config: dict[str, Any]) -> list[dict[str, Any]]:
        return [m for m in ((config.get("midi") or {}).get("cc_mappings") or []) if isinstance(m, dict)]

    was, now = mappings(running), mappings(compiled)
    return sorted(map(repr, map(cc_identity, was))) == sorted(map(repr, map(cc_identity, now)))


def restart_refusal(
    state: dict[str, Any], described: dict[str, Any], log: list[dict[str, Any]],
) -> tuple[str, str] | None:
    """`(reason, "unsaved")` when a restart would drop live work the agent saw
    and no save has written, or None. Pure over the state file,
    `describe_rig`'s answer and the log.

    A restart reloads the rig's file, so what was turned since the last save
    is gone after it, with nothing said: the hole the web UI's restart offer
    after an edit opened (review, 2026-11-06), and the picker's and the CLI's
    plain restart had all along. `{"discard": true}` (`sushi-rig restart
    --discard`) says to drop it.

    Not refused, because there is nothing to keep or no way to know: no rig
    up (the restart says so itself); Sushi gone (`describe_rig` says its
    process is not alive), where the work went with it and a restart is the
    recovery principle 9 asks for, which must not wait on a Save that cannot
    capture anything; and a rig run with its panel, whose changes the agent
    cannot see, so refusing would refuse every panel run's restart for
    nothing it knows of. Counted, not netted out, as `unsaved_entries` says."""
    from .rig import runs_panel

    if not described.get("running"):
        return None
    sushi = (described.get("children") or {}).get("sushi")
    if isinstance(sushi, dict) and sushi.get("alive") is False:
        return None
    if runs_panel(state):
        return None
    unsaved = unsaved_entries(log, since=state.get("started_at"))
    if not unsaved:
        return None
    name = state.get("config_name") or described.get("config_name") or "the rig"
    newest = unsaved[-1]
    count = f"{len(unsaved)} change{'s' if len(unsaved) != 1 else ''}"
    return (f"{name} has {count} made through the agent and not saved (the newest: "
            f"{describe_change(newest)}, at {newest.get('at')}), and a restart reloads its file without "
            "them: Save first, or restart with discard (sushi-rig restart --discard, or "
            '{"discard": true}) to drop them', "unsaved")


def revert_request(body: Any) -> tuple[str, bool, bool | None]:
    """`(point, discard, restart)` from a revert's body, or `AgentError`
    (400): `save`, a snapshot's id or `commit:<sha>` as `GET
    /rigs/{name}/saves` lists them (checked by shape here, looked up by the
    route), `discard`, true or absent, and `restart`: false to be refused
    (`restarts`) rather than restart Sushi, true or absent to restart where
    the switch must (#209). Anything beside them is refused, as a save's
    body is."""
    from . import saves

    if not isinstance(body, dict) or not set(body) <= {"save", "discard", "restart"}:
        raise AgentError(f'the body of a revert is {{"save": "<id>"}}, with "discard": true to drop unsaved '
                         f"work, not {body!r}")
    point = body.get("save")
    if not isinstance(point, str) or not (saves.ID_PATTERN.match(point) or saves.COMMIT_PATTERN.match(point)):
        raise AgentError(f"'save' must be a revert point's id, as GET /rigs/<name>/saves lists them, not {point!r}")
    discard = body.get("discard", False)
    if not isinstance(discard, bool):
        raise AgentError(f"'discard' must be true or false, not {discard!r}")
    restart = body.get("restart")
    if restart is not None and not isinstance(restart, bool):
        raise AgentError(f"'restart' must be true or false, not {restart!r}")
    return point, discard, restart


def revert_refusal(state: dict[str, Any], log: list[dict[str, Any]]) -> tuple[str, str] | None:
    """`(reason, code)` when a revert of the running rig must wait, or None.
    Pure over the state file and the log. The rule an edit of its file has
    (`rigfile_refusal`): a revert replaces what is playing with the file, so
    unsaved live work would be lost by it (`unsaved`), and with the panel
    running the agent cannot see the panel's (`panel`). Worded for a revert,
    and answered with `{"discard": true}` rather than a restart."""
    from .rig import runs_panel

    name = Path(str(state.get("rig") or "the rig")).stem
    if runs_panel(state):
        return (f"{name} runs with its panel, whose changes the agent cannot see, so it cannot tell whether "
                "a revert would drop unsaved work: save from the panel's SAVE first, or revert with discard "
                '({"discard": true}) to drop whatever it holds', "panel")
    unsaved = unsaved_entries(log, since=state.get("started_at"))
    if not unsaved:
        return None
    newest = unsaved[-1]
    count = f"{len(unsaved)} change{'s' if len(unsaved) != 1 else ''}"
    return (f"{name} is running with {count} made through the agent and not saved (the newest: "
            f"{describe_change(newest)}, at {newest.get('at')}), and a revert replaces what is playing with "
            'the file: Save first, or revert with discard ({"discard": true}) to drop them', "unsaved")


def programs_moved(before: list[str], after: list[str], made: str) -> dict[str, Any] | None:
    """What a new rig file does to the Program Change numbers, or None when
    nothing moves: `{"moved": [{"rig", "from", "to"}], "before", "after",
    "note"}`, the tables as `midi_listener.programs_table` gives them. Pure.

    Program n is the nth file of config/rigs by file name
    (`midi_listener.program_order`), so a new file sorting before others
    moves each of them up one, and a pedal preset set for one rig then
    selects another, with nothing said (review, 2026-11-06). Said whenever a
    number moves, for every rig, not only those whose map switches by
    Program Change: which presets a player has set is not in any file. The
    rule itself is not changed here."""
    moved = [{"rig": rig, "from": n, "to": after.index(rig)}
             for n, rig in enumerate(before) if rig in after and after.index(rig) != n]
    if not moved:
        return None
    first = moved[0]
    return {
        "moved": moved,
        "before": midi_listener.programs_table(before), "after": midi_listener.programs_table(after),
        "note": (f"Program Change numbers follow config/rigs by file name, so {made} (program "
                 f"{after.index(made)}) moves {len(moved)} rig{'s' if len(moved) != 1 else ''} up one: program "
                 f"{first['from']}, which was {first['rig']}, is now {after[first['from']]}. A pedal preset set for "
                 "a number now selects the rig listed against it after"),
    }


def programs_deleted(before: list[str], after: list[str], deleted: str) -> dict[str, Any] | None:
    """What deleting rig `deleted` does to the Program Change numbers, or
    None when nothing moves (it sorted last): `programs_moved`'s shape,
    each rig after it one down. Pure."""
    moved = [{"rig": rig, "from": n, "to": after.index(rig)}
             for n, rig in enumerate(before) if rig in after and after.index(rig) != n]
    if not moved:
        return None
    first = moved[0]
    return {
        "moved": moved,
        "before": midi_listener.programs_table(before), "after": midi_listener.programs_table(after),
        "note": (f"Program Change numbers follow config/rigs by file name, so deleting {deleted} (program "
                 f"{before.index(deleted)}) moves {len(moved)} rig{'s' if len(moved) != 1 else ''} down one: "
                 f"{first['rig']} moves from program {first['from']} to {first['to']}. A pedal preset set for a "
                 "number then selects the rig listed against it after"),
    }


def running_rig_file(run: dict[str, Any], root: Path) -> Path | None:
    """The running rig's file, as `/health` names it (`describe_rig`'s
    `rig`, the state file's), or None with no rig of channels up. The one
    notion of "the running rig" the bin refuses and `/rigs` marks."""
    described = run["described"]
    rig = described.get("rig") or run["state"].get("rig")
    if not described.get("running") or not isinstance(rig, str) or not rig:
        return None
    return rig_file_of({"rig": rig}, root)


def running_rig_listed(run: dict[str, Any], root: Path, rigs: list[str]) -> str | None:
    """Which of `rigs` (config/rigs/ by name) is the running rig's file,
    or None: what `/rigs` gives as `running`, so the page marks the row the
    agent would refuse to delete, never a guess from a basename."""
    running = running_rig_file(run, root)
    if running is None:
        return None
    folder = (Path(root) / "config" / "rigs").resolve()
    try:
        resolved = running.resolve()
    except OSError:
        return None
    if resolved.parent != folder or resolved.suffix != ".yaml" or resolved.stem not in rigs:
        return None
    return resolved.stem


def bin_refusal(run: dict[str, Any], path: Path, root: Path) -> tuple[str, str] | None:
    """Why rig file `path` may not be deleted now, as `(reason, code)`, or
    None. The running rig's file is refused (`running`): the rig `/health`
    names (`describe_rig`'s `rig`, the state file's), whose file a restart
    reloads and the next save writes. With no rig running, any rig may go."""
    running = running_rig_file(run, root)
    if running is None or running.resolve() != Path(path).resolve():
        return None
    return (f"{path.stem} is the running rig: a restart reloads its file and the next save writes it, so it "
            "cannot be deleted while it runs. Switch to another rig, or stop it, first", "running")


def bin_note(answer: dict[str, Any]) -> str:
    """What a delete's answer (or its dry run) says, in a sentence or two."""
    shown, name = answer["file"], answer["rig"]
    if answer["how"] == "dry_run":
        said = (f"Deleting {name} moves {shown} into config/rigs/.deleted/, out of /rigs, the picker and the "
                "Program Change numbers; Undo puts it back. Nothing is committed.")
    else:
        said = (f"{shown} is now {answer['binned']['file']}: out of /rigs, the picker and the Program Change "
                f"numbers. Nothing was committed; POST {answer['undo']['path']} puts it back.")
    git = answer.get("git")
    if git is None:
        said += f" {answer.get('git_note') or 'What git holds of it is not known'}."
    elif not git["committed"]:
        said += " It is not committed, so the bin holds the only copy."
    else:
        said += f" It is committed, so `{git['restore']}` also brings back the committed version"
        said += (", without the changes made since, which only the bin keeps." if git["same"] is False else ".")
    return said


def describe_change(entry: dict[str, Any]) -> str:
    """One log entry in a few words, for a refusal that names it."""
    op, args = entry.get("op"), entry.get("args") or {}
    if op == "parameter":
        return f"{args.get('processor')} {args.get('parameter')}"
    if op == "parameters":
        return f"{args.get('processor')} {', '.join(args.get('parameters') or {})}"
    if op == "bypass":
        return f"bypass {args.get('processor')}"
    if op == "move":
        return f"{args.get('processor')} moved {args.get('direction')}"
    if op == "add":
        return f"{args.get('name')} added to {args.get('track')}"
    if op == "remove":
        return f"{args.get('processor')} removed"
    if op == "amp":
        return f"amp {args.get('name')}"
    if op == "amp_model":
        return "the amp's model"
    if op == "undo":
        return "an undo"
    return str(op)


def serve(host: str, port: int, root: Path | None = None, allow_hosts: Sequence[str] = ()) -> int:
    """Run the agent until interrupted. A browser may reach it by any IP,
    localhost, this machine's hostname and `.local` name, `host` and each of
    `allow_hosts` (`own_names`, #161)."""
    if web is None:
        raise AgentError("aiohttp is not installed; pip install -e tool[agent]")

    # Bound before anything is started or written (#181), and these very
    # sockets served: an agent that cannot listen says who has the port and
    # how to stop it, not a traceback, and has adopted no rig's MIDI and
    # written no address file on its way out. No moment between a check and
    # the bind for another program to take the port, and `--port 0`'s real
    # port is the one recorded and printed (review of PR #185).
    from . import reach

    sockets, problem = reach.bind_sockets(host, port)
    if problem is not None:
        raise AgentError(reach.bind_message(host, port, problem))
    port = sockets[0].getsockname()[1]
    try:
        return _serve_on(host, port, sockets, root, allow_hosts)
    finally:
        for sock in sockets:
            sock.close()


def _serve_on(host: str, port: int, sockets: list, root: Path | None, allow_hosts: Sequence[str]) -> int:
    """`serve`, on the sockets it bound: `port` is the port they hold."""
    from . import reach

    import os

    from .agent_client import agent_url, remove_address, write_address

    agent = Agent(root=root, allow_hosts=allow_hosts, bound=host)
    app = agent.app()

    # Where this agent listens, beside the state file, so the CLI finds it on
    # whatever --host and --port it was given; taken away at the end.
    async def say_where(app) -> None:
        write_address(agent_url(host, port), os.getpid())

    async def unsay_where(app) -> None:
        remove_address(os.getpid())

    app.on_startup.append(say_where)
    app.on_startup.append(agent.midi_adopt)  # MIDI: a rig already up has its pedals served
    app.on_shutdown.append(agent.announce_stopping)
    app.on_cleanup.append(agent.midi_release)  # MIDI: before the pool its actions use
    # A visualiser tap an earlier agent left running (it died uncleanly):
    # stopped by its recorded pid before this one serves (#195).
    app.on_startup.append(agent.supervisor.tidy_orphan_tap)
    app.on_cleanup.append(agent.supervisor.release)  # stop watching; the rig plays on
    app.on_cleanup.append(agent.release_workers)
    app.on_cleanup.append(unsay_where)
    agent.listening = (host, port)
    print(f"sushi-rig agent {VERSION} on http://{host}:{port}  (Ctrl+C to stop)", flush=True)
    for line in reach.listening_lines(host, port, reach.interfaces()[0] if reach.is_network(host) else []):
        print(line, flush=True)
    try:
        web.run_app(app, sock=sockets, print=None, handle_signals=True)
    except OSError as exc:
        # Not the port: the sockets were bound above. Something of the
        # agent's own start or stop (an address file it cannot write, a
        # MIDI port), said as itself rather than as a port problem.
        where = f" ({exc.filename})" if getattr(exc, "filename", None) else ""
        raise AgentError(f"sushi-rig: the agent stopped on an error of its own, not the port's: "
                         f"{type(exc).__name__}: {exc.strerror or exc}{where}") from exc
    return 0
