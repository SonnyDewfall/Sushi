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
    POST /rig/up        {"config_name" (required), "rig", "headless", "tuner", "amp", "amp_model"}
    POST /rig/down      {"force"}
    POST /rig/restart   {"discard"?} -- refused (409 unsaved) if it would drop changes no save wrote
    POST /rig/switch    {"config_name" (required)} -- to a rig of config/rigs, live where it can be (#93)
    GET  /live          the running rig's parameters, bypass and chains, with each parameter's range
    POST /live/parameter  {"processor", "parameter", "value"}   value is normalised, 0 to 1
    POST /live/bypass   {"processor", "bypassed"}
    POST /live/move     {"processor", "direction": "up" | "down"}
    GET  /live/log      the changes made through /live, newest last (the last 500), and
                        `first`, the index of the oldest one held
    POST /live/undo     {} -- put back what the newest change not yet undone replaced;
                        {"skip": true} -- give up on the newest one that cannot be
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
    GET  /machine/roles the machine file's roles: ports, widths each can feed, which tracks use each
    GET  /rigs/{name}/channels   a rig file's channels: name, input, width, level, plugins
    POST /rigs/new      {"name", "channels": [{"name", "input", "width", "from"?}]} -- a new rig file
    POST /rigs/{name}/channels/add              {"name", "input", "width", "from"?}
    POST /rigs/{name}/channels/{channel}/input  {"input"?, "width"?} -- each written, never committed

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
change is in the live log with `source: "midi"`. The
listener starts when a rig comes up (and at the agent's own start, for a rig
already up) and stops before anything takes the rig down. A port that will
not open is a named problem in `/health` (`agent.midi`), not a retry loop, and
nothing the port does reaches the HTTP side. The decisions are in
`midi_listener.py`; the wiring is the MIDI section at the end of `Agent`.

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
real recorder process, with the engine stood in.

## Local-first, so localhost first

The CLI binds it to 127.0.0.1:8029 unless told otherwise. `--host 0.0.0.0`
opens it to the LAN, and until the permission split the issue describes exists
(local: everything; remote: start, stop, load, parameters) that is the
operator's call to make, not a default.

## The library and chain edits (#32)

    GET  /library       every plugin in plugin-manifest.txt: uri, name, ports, profile and its groups
    POST /live/add      {"track", "uri", "name"?, "after"?}
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

## The web UI (#31)

    GET  /              the UI's page; its files are under /ui/ (`sushi_rig/ui/static/`),
                        and /ui and /ui/ themselves redirect here (#111: the folder is not a page)
    GET  /profiles      every profile, flattened for the browser (`ui.panel_profile`)
    GET  /live/plugins  {processor: {"track", "uri", "uid", "type", "role"}}, from Sushi's session
    GET  /rigs          the rigs of config/rigs/ a start or a switch can name
    GET  /rig/load      one pw-top sample of the running rig's nodes: load, xruns, the budget

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
import dataclasses
import functools
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
from .live import DEFAULT_GRPC_ADDRESS, ElkpyMissing, LiveError, Move
from .live import is_send, protected_processor
from .rig import describe_rig
from .save import NAME_PATTERN
from .midi_listener import MidiError
from .rig import describe_rig, restart_arguments
from .supervisor import Outcome, Supervisor
from . import rigfile  # the live switch (#93)
from .rig import RigError, compile_named_rig
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
    return ["restart"]


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
    if op not in ("parameter", "bypass", "move", "add", "remove"):
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
    if entry["op"] == "parameter":
        left = args["value"]
        same = (isinstance(current, (int, float)) and not isinstance(current, bool)
                and math.isclose(current, left, rel_tol=0.0, abs_tol=PARAMETER_TOLERANCE))
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
    bad request it may succeed later, against a different rig."""


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
    after = body.get("after")
    if after is not None and (not isinstance(after, str) or not after):
        raise AgentError(f"'after' must name a processor on the track, or be left out, not {after!r}")
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
                      stereo.copy_name(name) if audio.get("out") == 1 else None)



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
    track's send when it has one; `after` the send is refused, because the
    send carries the channel to the master and whatever follows it is not
    heard. The master's return is first on its track and stays first: an add
    is always after something or at the back.
    """
    chains, ids = reading.get("chains", {}), reading.get("ids", {})
    uids = reading.get("uids") or {}
    track, name, after = request.track, request.name, request.after
    if track not in chains:
        raise Conflict(f"the running rig has no track {track!r}: it has {sorted(chains)}")
    if name in chains:
        raise Conflict(f"{name!r} is a track of the running rig; give the processor another name")
    holder = _track_of(chains, name)
    if holder is not None:
        what = "give a 'name'" if not request.named else "give another name"
        raise Conflict(f"{name!r} is already on the rig, on track {holder!r}: {what}")
    if request.copy is not None and (request.copy in chains or _track_of(chains, request.copy) is not None):
        raise Conflict(f"{request.copy!r}, the name of {name!r}'s copy to stereo, is already on the rig")
    chain = list(chains[track])
    sends = [index for index, held in enumerate(chain) if is_send(track, held, uids.get(held))]
    if after is not None:
        if after not in chain:
            raise Conflict(f"there is no processor {after!r} on track {track!r}: it has {chain}")
        if chain.index(after) in sends:
            raise Conflict(
                f"nothing goes after {after!r}: the send must stay last, since it is what "
                "carries the channel to the master")
        index = _unit_end(chain, after, uids)
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
    ) -> None:
        self.root = root
        self.log_limit = log_limit
        self.describe = describe
        self.doctor = doctor or self._doctor
        self.supervisor = supervisor or Supervisor()
        # A lifecycle step is CLI arguments (`lifecycle_arguments`), run by the
        # supervisor in this process (#29); a test stands in a runner instead.
        self.run = run or self.supervisor.run
        self.live = live or Live.at()
        self.clock = clock
        self.started = clock()
        self.started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.last_error: dict[str, Any] | None = None
        self.busy: str | None = None  # the lifecycle command running, if one is
        self._lock = asyncio.Lock()  # lifecycle: one command at a time
        self._live_lock = asyncio.Lock()  # live: one change at a time; waits, not 409. Reads do not take it
        self.log: list[dict[str, Any]] = []  # operations made through /live, newest last
        self._trimmed = 0  # entries dropped from the front, so log[i] has index _trimmed + i
        # Sushi calls run here (see LIVE_WORKERS), not in the loop's executor.
        self._executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=LIVE_WORKERS, thread_name_prefix="sushi-live")
        self._in_flight: set = set()  # calls to Sushi that have not returned
        self._abandoned: set = set()  # ... of which the agent has stopped waiting for these
        # The library (#32): built on first use and kept; see `_library_now`.
        self._build_library = library or self._read_library
        self._library: dict[str, Any] | None = None
        self._library_lock = asyncio.Lock()
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

    @property
    def stuck_live_calls(self) -> int:
        """Calls to Sushi the agent gave up waiting for that have not returned."""
        return len(self._abandoned)

    def _doctor(self) -> list:
        from .doctor import run
        from .paths import checkout_root

        # The crashes this agent saw, so a node of the rig's own that is
        # absent is said as the crash it is, not as a rig that is down.
        return run(self.root or checkout_root(), crashed=list(self.supervisor.crashed))

    # ----------------------------------------------------------------- routes

    def app(self):
        app = web.Application(middlewares=[self._json_errors])
        app.router.add_get("/health", self.health)
        app.router.add_get("/rig", self.rig)
        app.router.add_get("/doctor", self.doctor_route)
        for command in LIFECYCLE:
            app.router.add_post(f"/rig/{command}", self.lifecycle)
        app.router.add_get("/live", self.live_state)
        app.router.add_get("/live/log", self.live_log)
        app.router.add_post("/live/parameter", self.live_parameter)
        app.router.add_post("/live/bypass", self.live_bypass)
        app.router.add_post("/live/move", self.live_move)
        app.router.add_post("/live/undo", self.live_undo)
        self._edit_routes(app)
        self._switch_routes(app)  # the live switch (#93)
        self._ui_routes(app)  # the web UI (#31): see its block
        self._amp_routes(app)  # the amp: see its block
        self._save_routes(app)  # saving the running rig (S4): see its block
        self._record_routes(app)  # recording from the web UI (S7): see its block
        self._rigfile_routes(app)  # the New rig screen's rig files: see its block
        self._revert_routes(app)  # revert to a previous save: see its block
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
                "midi": self._midi_status(),
                # The rig this agent holds, and its children that exited with
                # no `down` asking them to: what a phone reads after a crash.
                "supervisor": self.supervisor.describe(rig),
            },
            "rig": rig,
        })

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
            answer["warnings"] = [
                line[len("warning: "):] for line in outcome.stderr.splitlines()
                if line.startswith("warning: ")
            ]
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
                outcome, args = await self._lifecycle_steps(steps, fallback)
            finally:
                self.busy = None
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
        async with self._connected(request) as controller:
            rig = await self._sushi(request, self.live.snapshot, controller, reading=True)
        return web.json_response({
            "ok": True,
            "processors": rig.get("processors", {}),
            "tracks": rig.get("tracks", {}),
            "parameter_info": rig.get("parameter_info", {}),
        })

    async def live_log(self, request):
        return web.json_response({
            "ok": True, "limit": self.log_limit, "first": self._trimmed, "log": list(self.log),
        })

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
        """Set one parameter and return the value it replaced."""
        reading = await self._sushi(
            request, self.live.parameter, controller, processor, parameter, reading=True)
        previous = check_parameter(reading, processor, parameter)
        if expect:
            expect(previous)
        if not dry_run:
            await self._sushi(
                request, self.live.set_parameter, controller, processor, parameter, value, reading=False)
        return previous

    async def _apply_bypass(
        self, request, controller, processor: str, bypassed: bool,
        *, expect: Callable[[Any], None] | None = None, dry_run: bool = False,
    ) -> bool | None:
        """Bypass or un-bypass one processor and return the state it replaced."""
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
        *, expect: Callable[[Any], None] | None = None, dry_run: bool = False,
    ) -> tuple[Move, Move | None]:
        """Move one processor a step; `(planned, moved)`, where `moved` is None
        if nothing was sent (already at that end, or a dry run). One walk, one
        decision (`live.decide_move`), and the command sent is the one decided
        on: success is what Sushi confirmed, not a word in a status line, and a
        processor found already at that end only now is "unchanged", not a
        failure. `planned.chain` is the chain before."""
        planned = await self._sushi(
            request, self.live.plan_move, controller, processor, DIRECTIONS[direction], reading=True)
        check_move(planned)
        if expect:
            expect(planned.chain)
        if planned.outcome == "unchanged" or dry_run:
            return planned, None
        return planned, await self._sushi(request, self.live.send_move, controller, planned, reading=False)

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
        return web.json_response({"ok": True, "changed": True, "message": moved.message, **entry})

    # How undo puts each op back: (previous, state) -- what the undo replaced,
    # and the state it set. `args` is `undo_inverse`'s, the route's own words.

    async def _undo_parameter(self, request, controller, undone, args, dry_run):
        previous = await self._apply_parameter(
            request, controller, args["processor"], args["parameter"], args["value"],
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
            await self._sushi(request, self.live.remove, controller, check_remove(reading, addition.name),
                              reading=False)
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
        self._refuse_while_busy()  # before the fast path: busy is the truer answer
        picked = self._pick_undo()
        if picked is None:
            raise _conflict("nothing to undo", "nothing_to_undo")
        if picked[1]["op"] in amp_live.OPS:
            # Carla's, not Sushi's: no connection to Sushi, the amp's own lock
            # (and for a model, the lifecycle's), see the amp's block.
            return await self._undo_amp(request, picked[1]["op"], skip)
        async with self._connected_for_a_change(request) as controller:
            picked = self._pick_undo()  # the log may have changed while waiting
            if picked is None:
                raise _conflict("nothing to undo", "nothing_to_undo")
            index, undone = picked
            if undone["op"] in amp_live.OPS:
                raise _conflict("the newest change became one of the amp's while this undo waited: "
                                "undo again", "changed")
            try:
                _, args = undo_inverse(undone)
                previous, state = await {
                    "parameter": self._undo_parameter, "bypass": self._undo_bypass,
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
        return web.json_response({"ok": True, **entry, "undone": undone, "state": state})

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

        Body `{"track", "uri", "name"?, "after"?}`. Refused (400) before Sushi is
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
        async with self._connected_for_a_change(request) as controller:
            try:
                addition = await self._apply_add(request, controller, lambda reading: check_add(reading, asked))
            except Conflict as exc:
                raise web.HTTPConflict(reason=str(exc))
            defaults, note = await self._read_defaults(request, controller, addition)
            entry = self._record("add", {
                "track": addition.track, "uri": addition.uri, "name": addition.name,
                "after": addition.after, "copy": addition.copy, "chain": list(addition.expected),
                "defaults": defaults,
            }, list(addition.chain))
        answer = {
            "ok": True, **entry, "chain": addition.expected,
            "message": f"added {addition.name} to {addition.track}",
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
            "message": f"removed {removal.processor} from {removal.track}",
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
        await self._sushi(request, self.live.add, controller, addition, reading=False)
        if after_send is not None:
            await after_send(addition)
        after = await self._sushi(request, self.live.walk, controller, reading=False)
        what = f"added {addition.name!r} to {addition.track!r}"
        self._confirm(request, after, addition.track, addition.expected, what)
        return addition

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
        """`{"rigs": [names]}`: config/rigs/*.yaml by name, sorted as a Program
        Change counts them, read now (off the loop: it lists a folder). Which
        is up is /health's to say; the page reads it there."""
        rigs = await asyncio.to_thread(midi_listener.program_order, self._midi_root())
        return web.json_response({"ok": True, "rigs": rigs})

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

            def finished(task) -> None:
                if self._load_run is task:
                    self._load_run = None
                if not task.cancelled() and task.exception() is None:  # retrieves it either way
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
        report = top_module.load_report(top_module.parse_pw_top(output), described.get("children"))
        return web.json_response({
            "ok": True, "at": datetime.now(timezone.utc).isoformat(timespec="seconds"), **report})

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
        (`live.read_live_plugins`), with `role` "send" or "return" for the
        compiler's (`live.protected_processor`, the one rule for them) and None
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
                role = "send" if is_send(track, name, plugin.get("uid")) else "return"
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
    # /rig (`_rig_answer`), `_run_lifecycle` (`_midi_stop`, `_midi_sync`) and
    # `serve` (`midi_adopt`, `midi_release`).
    #
    # Threading: the listener's thread reads the port and hands each message to
    # the loop (`call_soon_threadsafe`); everything below runs on the loop. A
    # generation number, bumped by every stop, tags each listener and each
    # action it caused, so a message that was on its way when the rig went
    # down is dropped rather than acted on against the rig that comes up next.

    def _midi_init(self, world: midi_listener.World | None) -> None:
        self._midi_world = world or midi_listener.World()
        self._listener: midi_listener.Listener | None = None
        self._midi_plan: midi_listener.Plan | None = None
        self._midi_generation = 0
        self._midi_switches: dict[str, bool] = {}  # each CC switch's last state, for `dispatch`
        # One MIDI action at a time, in the order they were played: two toggles
        # in quick succession must reach Sushi in that order, and a switch's
        # off-then-on must not arrive on-then-off. Not the live lock: that
        # serialises only the read-decide-set, and connections are made outside
        # it, so two actions could otherwise overtake each other while connecting.
        self._midi_lock = asyncio.Lock()
        self._midi_tasks: set = set()
        self._midi_released = False  # set by `midi_release`: nothing starts a listener after it
        self._midi: dict[str, Any] = {
            "state": "off", "reason": "the agent has not looked for a rig yet",
            "rig": None, "controller": None, "port": None, "bindings": [], "programs": False,
            "heard": 0, "last": None, "problem": None, "last_error": None,
        }

    def _midi_root(self) -> Path:
        from .paths import checkout_root

        return self.root or checkout_root()

    def _midi_status(self) -> dict[str, Any]:
        """The listener, for /health: `state` is off (with the `reason`),
        opening, listening, or failed (with the `problem`). `heard` counts every
        message the port delivered since it opened, aftertouch included, and
        `last` is the last one the listener reads and what it did with it: what
        to look at when a pedal seems to do nothing."""
        listener = self._listener
        return {**self._midi, "heard": listener.heard if listener is not None else self._midi["heard"]}

    async def _rig_answer(self) -> dict[str, Any]:
        """/rig: `describe_rig`, and when the running rig's map switches rigs by
        Program Change, which number is which rig (`programs`), read now, as a
        Program Change reads it (off the loop: it lists a folder)."""
        described = self.describe()
        plan = self._midi_plan
        if plan is not None and plan.bindings.programs is not None:
            rigs = await asyncio.to_thread(self._midi_world.rigs, self._midi_root())
            described["programs"] = midi_listener.programs_table(rigs)
        return described

    def _midi_say(self, message: str) -> None:
        print(f"sushi-rig agent: MIDI: {message}", file=sys.stderr, flush=True)

    def _midi_off(self, reason: str) -> None:
        self._midi.update(state="off", reason=reason, rig=None, controller=None, port=None,
                          bindings=[], programs=False, problem=None)

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
        rig with nothing for the agent; the one its files describe otherwise.
        A listener already serving that same plan is left alone. Never raises:
        whatever goes wrong is the listener's problem, in /health, and not the
        lifecycle command's. Does nothing once the agent has released MIDI."""
        if self._midi_released:
            return
        try:
            plan = await asyncio.to_thread(self._midi_world.plan, self.describe(), self._midi_root())
        except Exception as exc:  # noqa: BLE001 -- the reason goes to /health and stderr
            await self._midi_stop("the rig's files could not say what to serve")
            detail = str(exc) if isinstance(exc, MidiError) else f"{type(exc).__name__}: {exc}"
            self._midi_fail(f"MIDI is off for the running rig: {detail}")
            return
        if isinstance(plan, midi_listener.Off):
            await self._midi_stop(plan.reason)
            self._midi_off(plan.reason)
            return
        if plan == self._midi_plan and self._midi["state"] in ("opening", "listening"):
            return
        await self._midi_stop("replaced")
        if not self._midi_released:  # released while the plan was being read
            self._midi_start(plan)

    def _midi_start(self, plan: midi_listener.Plan) -> None:
        loop = asyncio.get_running_loop()
        generation = self._midi_generation
        self._midi_plan = plan
        self._midi_switches = {}
        self._midi.update(
            state="opening", reason=None, rig=plan.rig, controller=plan.controller, port=plan.port,
            bindings=[b.describe() for b in plan.bindings.controls],
            programs=plan.bindings.programs is not None,
            heard=0, last=None, problem=None, last_error=None,
        )
        self._listener = midi_listener.Listener(
            plan.port, self._midi_world.open_port,
            post=loop.call_soon_threadsafe,
            on_open=functools.partial(self._midi_opened, generation),
            on_message=functools.partial(self._midi_heard, generation),
            on_failure=functools.partial(self._midi_failed, generation),
        )
        self._listener.start()

    async def _midi_stop(self, reason: str) -> None:
        """Stop the listener, if there is one, and drop what it handed over
        that has not been acted on. Waits (off the loop) for its thread to let
        go of the port; a thread stuck in ALSA past that is said and left, as a
        daemon, rather than holding up a `down`."""
        self._midi_generation += 1
        self._midi_switches = {}
        listener, self._listener = self._listener, None
        if listener is None:
            return
        self._midi_plan = None
        self._midi["heard"] = listener.heard
        if not await asyncio.to_thread(listener.stop, 2 * midi_listener.POLL_SECONDS + 1.0):
            self._midi_say(f"the listener on {listener.port_name!r} did not stop; left to end with the agent")
        self._midi_off(reason)

    def _midi_opened(self, generation: int) -> None:
        if generation == self._midi_generation:
            self._midi.update(state="listening")

    def _midi_failed(self, generation: int, exc: BaseException, opened: bool) -> None:
        if generation != self._midi_generation:
            return
        plan = self._midi_plan
        doing = "stopped reading" if opened else "could not open"
        who = f"the port of controller {plan.controller!r}, which {plan.rig}'s MIDI map is written for" if plan else "the port"
        self._midi_fail(
            f"{doing} MIDI port {self._midi['port']!r}, {who}: {exc}. Bypass and rig switching "
            "from the pedals are off until the rig is next brought up or restarted"
        )

    def _midi_heard(self, generation: int, message: midi_listener.Message) -> None:
        """A message from the listener, on the loop: decided now, in the order
        played, and acted on in a task of its own, one at a time.

        A Program Change is decided in its task instead, because deciding it
        lists config/rigs/, which is file access and not for the loop. The
        switch state `dispatch` returns is not kept here: `_midi_act` commits
        it once the action has gone through."""
        plan = self._midi_plan
        if generation != self._midi_generation or plan is None:
            return
        try:
            if message.kind == "program":
                if plan.bindings.programs is None:
                    action = None
                else:
                    action = message  # decided in `_midi_act`, off the loop
            else:
                # A switch's crossing is recorded as it is decided, so the values
                # that follow on the same side (a pedal sends several) are not
                # crossings too; `_midi_forget` takes it back if the action is
                # dropped or fails, so the next value plays it again.
                action, self._midi_switches = midi_listener.dispatch(message, plan.bindings, self._midi_switches)
        except Exception as exc:  # noqa: BLE001 -- a loop callback's error would reach only the loop's log
            self._midi["last_error"] = {
                "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "message": f"{message.describe()}: {type(exc).__name__}: {exc}",
            }
            self._midi_say(self._midi["last_error"]["message"])
            return
        self._midi_last(message, action)
        if action is None:
            return
        if len(self._midi_tasks) >= MIDI_PENDING:
            # Sushi is not keeping up (a stuck call holds each action up to
            # LIVE_TIMEOUT): a pedal's presses queued behind it would all land
            # at once, long after they were played.
            self._midi_error(action, f"{len(self._midi_tasks)} MIDI actions are still waiting on Sushi, so this one was dropped")
            self._midi_forget(action)
            return
        task = asyncio.ensure_future(self._midi_act(generation, action))
        self._midi_tasks.add(task)
        task.add_done_callback(self._midi_tasks.discard)

    def _midi_last(self, message: midi_listener.Message, action: Any) -> None:
        if action is message:
            done = "deciding which rig"
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

    async def _midi_act(self, generation: int, action: Any) -> None:
        async with self._midi_lock:
            if generation != self._midi_generation:
                return  # the listener that heard it has been stopped since
            if isinstance(action, midi_listener.Message):  # a Program Change, to decide
                message, plan = action, self._midi_plan
                try:
                    rigs = await asyncio.to_thread(self._midi_world.rigs, self._midi_root())
                except Exception as exc:  # noqa: BLE001 -- said, the agent carries on
                    self._midi["last_error"] = {
                        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                        "message": f"{message.describe()}: reading config/rigs/ failed: {type(exc).__name__}: {exc}"}
                    self._midi_say(self._midi["last_error"]["message"])
                    return
                if generation != self._midi_generation or plan is None:
                    return
                action, _ = midi_listener.dispatch(message, plan.bindings, {}, rigs, plan.rig)
                if self._midi["last"] and self._midi["last"]["action"] == "deciding which rig":
                    self._midi_last(message, action)
                if action is None:
                    return
            # What `_sushi` and the live lock read off a request, for a press.
            origin = _MidiOrigin("MIDI", getattr(action, "control", None) or f"program {action.program}")
            try:
                if isinstance(action, midi_listener.Refused):
                    raise MidiError(action.reason)
                if isinstance(action, (midi_listener.ToggleBypass, midi_listener.SetBypass)):
                    await self._midi_bypass(origin, action)
                elif isinstance(action, midi_listener.ToggleMute):
                    await self._midi_mute(origin, action)
                elif isinstance(action, midi_listener.SelectRig):
                    await self._midi_switch(action)
            except web.HTTPException as exc:  # what a route would have answered
                self._midi_error(action, exc.reason)
                self._midi_forget(action, generation)
            except (AgentError, MidiError) as exc:
                self._midi_error(action, str(exc))
                self._midi_forget(action, generation)
            except Exception as exc:  # noqa: BLE001 -- said, with its type; the agent carries on
                self._midi_error(action, f"{type(exc).__name__}: {exc}")
                self._midi_forget(action, generation)

    def _midi_forget(self, action: Any, generation: int | None = None) -> None:
        """Take back the crossing a CC switch's dropped or failed action
        recorded, so the next value on that side plays it again. Only while
        the switch still holds this action's state (a later crossing stands)
        and the listener that heard it is still the one listening."""
        if not isinstance(action, midi_listener.SetBypass):
            return
        if generation is not None and generation != self._midi_generation:
            return
        if self._midi_switches.get(action.control) == (not action.bypassed):
            self._midi_switches = {k: v for k, v in self._midi_switches.items() if k != action.control}

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
        """`POST /live/parameter`'s path, on the track's `mute`."""
        track = action.track
        async with self._connected_for_a_change(origin) as controller:
            reading = await self._sushi(origin, self.live.parameter, controller, track, "mute", reading=True)
            previous = check_parameter(reading, track, "mute")
            value = midi_listener.next_mute(previous)
            await self._sushi(origin, self.live.set_parameter, controller, track, "mute", value, reading=False)
            self._midi_logged(
                self._record("parameter", {"processor": track, "parameter": "mute", "value": value}, previous),
                action.control)

    async def _midi_switch(self, action: Any) -> None:
        """Switch to rig `action.rig` by the path `POST /rig/switch` takes
        (`_switch`, #93): live where the two rigs' configs allow it, and
        otherwise the restart this used to be, `down` then `up --rig` with the
        options `restart` would bring back, with the previous rig brought back
        if its `up` fails. A rig that would not compile is refused before
        anything is touched. The log entry is marked as MIDI's."""
        origin = _MidiOrigin("MIDI", f"program {action.program}")
        # A restart instead of a live switch is said on stderr by `_switch`,
        # and is in the log entry's `why`.
        answer = await self._switch(
            origin, action.rig, program=action.program, mark=lambda entry: self._midi_logged(entry, None))
        if not answer["ok"]:
            raise MidiError(f"'{' '.join(answer['command'])}' failed: {answer['reason']}")

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
        running rig as it was (`code`: `busy`, `no_rig`, `rig_problem`), or a
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
        return web.json_response(answer, status=status)

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

    async def _switch_held(self, origin, name, program, mark) -> dict[str, Any]:
        started = self.clock()
        described = self.describe()
        if not described.get("running"):
            raise SwitchRefused(
                f"no rig is running to switch from ({described.get('message')}): POST /rig/up", "no_rig")
        world = self.switching
        state = await asyncio.to_thread(world.read_state) or {}
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
        try:
            running = await asyncio.to_thread(world.read_config, described.get("config"))
            # As the live attempt will see the amp: none at all for a run
            # with no amp, so its `_amp` alone never forces a restart.
            decided = rigfile.diff(running, upcoming, root=self._amp_root(),
                                   amp_off=state.get("amp_requested") is False)
        except Exception as exc:  # noqa: BLE001 -- unreadable is a reason to restart, said
            running = None
            decided = rigfile.NeedsRestart((rigfile.RestartReason(
                "unreadable", f"the running rig's compiled config could not be read: {type(exc).__name__}: {exc}"),))
        blockers = state_blockers(state)
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
            tried = await self._switch_live(origin, name, running, upcoming, state)
            failure = tried.pop("failed", None)  # a RestartReason, said under `why` below
            answer.update(tried)
            if tried["ok"]:
                entry = self._record("rig", {
                    "rig": name, "how": "live", **({"program": program} if program is not None else {}),
                    "plan": tried["counts"]}, previous)
                if mark:
                    mark(entry)
                return {**answer, "how": "live", "entry": entry,
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

    async def _switch_live(
        self, origin, name: str, running: dict[str, Any], upcoming: dict[str, Any], state: dict[str, Any],
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
        # run with no amp (`--no-amp`) has none: no Carla runs and a
        # restart into the next rig would start none, so neither rig's `_amp`
        # is a step or a reason (`amp_off`). One whose Carla should be there and is not
        # keeps its steps, so the first fails and the switch restarts into the
        # next rig, Carla with it.
        amp = self._amp_for(state, running)
        amp_off = state.get("amp_requested") is False
        try:
            async with self._connected(origin) as controller:
                async with self._live_lock:
                    before = await self._sushi(origin, self.live.snapshot, controller, reading=True)
                    plan = rigfile.diff(running, upcoming, {**before, "amp": {"parameters": amp.sent()}},
                                        root=self._amp_root(), amp_off=amp_off)
                    if isinstance(plan, rigfile.NeedsRestart):
                        reason = plan.reasons[0]
                        detail = "; ".join(f"{r.code}: {r.detail}" for r in plan.reasons)
                        return {**progress, "ok": False, "failed": rigfile.RestartReason(reason.code, detail)}
                    progress["plan"] = [step.as_dict() for step in plan.steps]
                    progress["counts"] = plan.counts()
                    for step in plan.steps:
                        await self._switch_step(origin, controller, step)
                        progress["applied"] += 1
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
            planned, moved = await self._apply_move(origin, controller, args["processor"], args["direction"])
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
            await self._sushi(origin, self.live.add, controller, addition, reading=False)
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

    def _amp_routes(self, app) -> None:
        app.router.add_get("/amp", self.amp_route)
        app.router.add_get("/amp/models", self.amp_models_route)
        app.router.add_post("/amp/parameter", self.amp_parameter_route)
        app.router.add_post("/amp/model", self.amp_model_route)

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
            raise self._amp_http(exc)
        key = (amp.run, amp.model)
        if self._amp_model_view is None or self._amp_model_view[0] != key:
            model = await asyncio.to_thread(amp_live.model_view, amp.model, self._amp_root(), self._amp_shelf)
            self._amp_model_view = (key, model)
        view = amp_live.amp_view(amp, state, self._amp_model_view[1])
        view["carla"] = (described.get("children") or {}).get("amp")
        view["held"] = self._amp_held()
        return web.json_response(view)

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

    async def _undo_amp(self, request, op: str, skip: bool):
        """Undo the newest change, an amp one (`pick_undo` found it): a
        parameter goes back to what was last sent before it, through the
        route's own call; a model goes back by restarting Carla on the one
        before. Stale (409) unless the amp is still as the change left it, by
        what was last sent: amp_live.undo_check. The rest as `live_undo`."""
        if op == "amp_model":
            index, undone = self._pick_undo()
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
                    # The adds and removes the agent made since the rig came
                    # up, so the file is written with the plugins it now
                    # runs (#32): read here, under the live lock, so no edit
                    # lands between it and the capture.
                    edits = live_edits(self.log, since=state.get("started_at"))
                    out, library, written = await self._save_write(
                        request, target, source, captured, model, parameters, edits)
                    created = out != source
                    shown, was = self._save_shown(out), self._save_shown(source)
                    note = (f"{shown} is a new file, written from nothing: the comments stay in {was}"
                            if created else f"{shown} was updated in place, with its comments")
                    answer = {
                        "ok": True, "file": shown, "path": str(out), "rig": out.stem,
                        "how": "new" if created else "updated", "previous": was,
                        "note": note + "; it is not committed", "committed": False, "library": library,
                        "amp": amp_said,
                    }
                    if created:
                        failure = await self._save_as_running(state, out.stem)
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
                    answer["entry"] = self._record("save", {
                        "file": shown, "rig": out.stem, "how": answer["how"], "library": library,
                        "amp": amp_said["saved"],
                        # What the next save starts from (`live_edits`): the
                        # plugins added live this one wrote, with their defaults.
                        "added": edits["added"],
                    }, was)
                    # Kept only now that the save has wholly happened, so a
                    # revert point is always a save that was logged.
                    answer["snapshot"] = await self._keep_snapshot(
                        request, out.stem, written,
                        {"file": shown, "how": answer["how"], "library": library,
                         **({"saved_as": was} if created else {})})
        return answer

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

    async def _save_as_running(self, state: dict[str, Any], name: str) -> str | None:
        """After a save-as wrote rig `name`: make it the running rig, as a live
        switch records the rig it switched to (`saved_as_state`), from
        `state`, the state file as read under the locks; None when done, or
        what failed. The new file is compiled as `up --rig` would compile it
        and written where `up` writes a run's config, so the next live switch
        diffs from it and `restart` brings it back. Called holding the amp's
        lock, so the amp's record moves to the renamed run in the same step:
        the run is renamed, not restarted, and what the agent sent Carla
        carries over rather than being seeded again from the config."""
        try:
            compiled = await asyncio.to_thread(self.switching.compile, name, self._midi_root(), state.get("machine"))
            path = await asyncio.to_thread(self.switching.write_config, name, compiled)
            after = saved_as_state(state, name, path)
            await asyncio.to_thread(self.switching.write_state, after)
        except Exception as exc:  # noqa: BLE001 -- said by the caller, with the file that was written
            return f"{type(exc).__name__}: {exc}"
        if self._amp is not None and self._amp.run == amp_live.run_of(state):
            self._amp.run = amp_live.run_of(after)
        return None

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
        return {"described": described, "state": state, "running": running, "machine": machine_file}

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
        input change, which edits the file and not the running rig."""
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
        answer = await self._rigfile_locked(request, lambda run: self._write_new_rig(request, run, name, channels))
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
            return rigedit.with_channel(raw, rigedit.channel_entry(change, copied)), {
                "op": "add_channel", "channel": change["name"], "input": change["input"],
                "width": change["width"], "from": source}

        answer = await self._rigfile_locked(request, lambda run: self._write_edit(request, run, path, edit))
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

    def _rigfile_copy(self, change: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
        """`(copied, said)` for a channel's `from`: its chain and level from
        that rig of config/rigs as the file is now, and where it came from,
        or `(None, None)`. Read from the file as it stands, not from git's
        commit of it: a tone saved and not yet committed is the one the
        player hears, and copying an older one would be the silent kind of
        wrong. What git says of that file is in the answer (`from`)."""
        from . import rigedit
        from .save import file_git_state

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
            copied = rigedit.source_channel(raw, source["rig"], source["channel"])
        except LookupError as exc:
            raise RigFileRefused(f"{change['name']} cannot start from it: {exc}", "no_source") from exc
        except Exception as exc:  # noqa: BLE001 -- a source that will not read is said
            raise RigFileRefused(f"{path.name} could not be read to start {change['name']} from: "
                                 f"{type(exc).__name__}: {exc}", "no_source") from exc
        state = file_git_state(root, path) or {}
        return copied, {"rig": source["rig"], "channel": source["channel"], "file": self._save_shown(path),
                        "plugins": len(copied["chain"]), "git": state.get("file")}

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
                    "programs": programs_moved(programs_before, midi_listener.program_order(root), name)}

        try:
            done = await self._rigfile_thread(request, path, work)
        except RigFileError as exc:
            raise RigFileRefused("nothing was written: " + "; ".join(exc.problems), "rig_problem") from exc
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
            raw = yaml.safe_load(path.read_text())
            if not isinstance(raw, dict):
                raise RigFileRefused(f"{path.name} is not a rig file", "rig_problem")
            edited, change = edit(raw, run)
            compiled = self._rigfile_compile(edited, run)
            try:
                write_rig(path, edited)
            except RigFileError as exc:
                raise RigFileRefused("nothing was written: " + "; ".join(exc.problems), "rig_problem") from exc
            return {"change": change, "compiled": compiled}

        if running:
            async with self._live_lock:
                refusal = rigfile_refusal(run["state"], self.log)
                if refusal is not None:
                    raise RigFileRefused(*refusal)
                done = await self._rigfile_thread(request, path, work)
        else:
            done = await self._rigfile_thread(request, path, work)
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
        `panel`, `rig_problem`; 409 `not_switched` (or 504 if a command was
        abandoned) when the file was written and the rig could not be made to
        play it, `written: true` and the reason saying so.

        Not run against a live Sushi: shown against the fakes of the tests.
        """
        from . import saves

        try:
            point, discard = revert_request(await self._json_body(request))
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
                    await self._rigfile_thread(request, path, lambda: self._revert_compile(data, run))
                    if running:
                        async with self._live_lock:
                            refusal = revert_refusal(run["state"], self.log)
                            if refusal is not None and not discard:
                                raise RigFileRefused(*refusal)
                            await self._rigfile_thread(request, path, lambda: saves.restore(path, data))
                    else:
                        await self._rigfile_thread(request, path, lambda: saves.restore(path, data))
                except RigFileRefused as exc:
                    error = _conflict(" ".join(str(exc).split()), exc.code)
                    if exc.extra:
                        error.agent_extra = exc.extra
                    raise error
                shown = self._save_shown(path)
                answer = {"ok": True, "rig": name, "file": shown, "point": said, "running": running,
                          "written": True, "discarded": bool(discard and running)}
                if not running:
                    answer["note"] = (f"{shown} is {said['what']} again; {name} is not the running rig, so it "
                                      "plays as reverted when it is next started or switched to")
                else:
                    answer.update(await self._revert_switch(request, name, said))
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

    def _revert_compile(self, data: bytes, run: dict[str, Any]) -> dict[str, Any]:
        """The point compiled as `up --rig` would compile it, or the refusal."""
        import yaml

        try:
            raw = yaml.safe_load(data.decode("utf-8"))
        except (UnicodeDecodeError, yaml.YAMLError) as exc:
            raise RigFileRefused(" ".join(f"nothing was written: it is not YAML: {exc}".split()),
                                 "rig_problem") from exc
        if not isinstance(raw, dict):
            raise RigFileRefused("nothing was written: it is not a rig file", "rig_problem")
        return self._rigfile_compile(raw, run)

    async def _revert_switch(self, request, name: str, said: dict[str, Any]) -> dict[str, Any]:
        """The running rig into its reverted file, by the live switch, held
        under the lifecycle lock the caller holds. What it says, as a
        switch's answer does, and `ok: false` with `code: "not_switched"`
        when the rig was not made to play the file that is now written."""
        def mark(entry: dict[str, Any]) -> None:
            entry["args"]["revert"] = said["id"]

        try:
            switched = await self._switch_held(request, name, None, mark)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 -- the file is written whatever went wrong: said, with `written`
            # A refusal (`SwitchRefused`) or anything else the switch raised:
            # the file is reverted either way, and the answer must say so
            # rather than be a bare 500 (review, 2026-11-07).
            why = str(exc) if isinstance(exc, SwitchRefused) else f"{type(exc).__name__}: {exc}"
            reason = " ".join((f"{name}'s file was reverted ({said['what']}), but the rig was not switched to it: "
                               f"{why}. It keeps playing as it was, unless the switch got part-way; restart it to play "
                               "the reverted file").split())
            self._remember(["revert", name], reason, kind="lifecycle")
            return {"ok": False, "code": "not_switched", "reason": reason, "written": True}
        out = {k: switched.get(k) for k in ("how", "why", "seconds", "plan", "applied", "counts", "entry",
                                            "state", "take", "abandoned", "half_switched") if k in switched}
        if not switched.get("ok"):
            reason = (f"{name}'s file was reverted ({said['what']}), but the rig did not come up playing it: "
                      f"{switched.get('reason')}")
            return {**out, "ok": False, "code": "not_switched", "reason": " ".join(reason.split())}
        return out

    # ======================================================================= #
    # (end of revert to a previous save)
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
        # The player's clock, for the day in a take's default name; local
        # time, as the name is for the player to recognise. A test moves it.
        self.take_clock: Callable[[], datetime] = datetime.now
        self._record_lock = asyncio.Lock()
        self._takes_cache: dict[str, Any] = {}  # GET /takes: each take's summary, by what is in its folder
        self._takes_lock = threading.Lock()  # the cache is read and written in worker threads

    def _record_routes(self, app) -> None:
        app.router.add_get("/record", self.record_route)
        app.router.add_post("/record/start", self.record_start_route)
        app.router.add_post("/record/stop", self.record_stop_route)
        app.router.add_get("/takes", self.takes_route)

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

    # ======================================================================= #
    # (end of recording from the web UI)
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


async def _ui_cache_control(request, response) -> None:
    """`Cache-Control: no-cache` on the page (`/`) and its files (`/ui/...`),
    set as each response is prepared, so the static route's own answers,
    304s included, carry it too."""
    if request.path == "/" or request.path.startswith("/ui/"):
        response.headers["Cache-Control"] = UI_CACHE_CONTROL


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
    headless, tuner and amp choices `restart` would bring back
    (`rig.restart_arguments`), so a switch keeps how the rig is being run.
    Not the amp model override: that was chosen for the rig being left, and
    the next one's own `_amp` says its model. `AgentError` for a rig name that
    is not one. Pure.

    The machine file the run was started against goes with it (`--machine`),
    as `Switching.compile` already compiled the next rig against it: until
    2026-11-06 every switch by restart dropped it, and `up` wired the next
    rig against this host's file instead, which need not be the one it was
    checked against."""
    _, options = restart_arguments(state)
    machine = options.get("machine") or state.get("machine")
    up = lifecycle_arguments("up", {
        "config_name": rig, "rig": True,
        "headless": options["headless"], "tuner": options["tuner"], "amp": options["amp"],
    }, machine=_absolute_machine(machine))
    return [["down"], up]


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
    with `code` (`no_rig`, `rig_problem`) by the route, and said in /health
    by the Program Change path."""

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
    `switched_at` says when; `started_at` stays the supervisor's. Pure."""
    return {
        **state, "config_name": name, "config": str(config), "rig": f"config/rigs/{name}.yaml",
        "switched_at": datetime.now(timezone.utc).isoformat(),
    }


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
        if (protected_processor(track, held, uids.get(held)) and not is_send(track, held, uids.get(held))
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
    gives, writing the next one's where `up` would, and the state file. The
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
        if entry.get("op") in ("rig", "save"):
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


def saved_as_state(state: dict[str, Any], name: str, config: Any) -> dict[str, Any]:
    """The state file after a save-as to rig `name`, whose compiled config is
    at `config`: `switched_state`'s record (the same supervisor, children and
    options, now naming the new file), saying when it was saved as rather
    than switched. Pure."""
    after = {**switched_state(state, name, config), "saved_as_at": datetime.now(timezone.utc).isoformat()}
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


def revert_request(body: Any) -> tuple[str, bool]:
    """`(point, discard)` from a revert's body, or `AgentError` (400): `save`,
    a snapshot's id or `commit:<sha>` as `GET /rigs/{name}/saves` lists
    them (checked by shape here, looked up by the route), and `discard`,
    true or absent. Anything beside them is refused, as a save's body is."""
    from . import saves

    if not isinstance(body, dict) or not set(body) <= {"save", "discard"}:
        raise AgentError(f'the body of a revert is {{"save": "<id>"}}, with "discard": true to drop unsaved '
                         f"work, not {body!r}")
    point = body.get("save")
    if not isinstance(point, str) or not (saves.ID_PATTERN.match(point) or saves.COMMIT_PATTERN.match(point)):
        raise AgentError(f"'save' must be a revert point's id, as GET /rigs/<name>/saves lists them, not {point!r}")
    discard = body.get("discard", False)
    if not isinstance(discard, bool):
        raise AgentError(f"'discard' must be true or false, not {discard!r}")
    return point, discard


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


def describe_change(entry: dict[str, Any]) -> str:
    """One log entry in a few words, for a refusal that names it."""
    op, args = entry.get("op"), entry.get("args") or {}
    if op == "parameter":
        return f"{args.get('processor')} {args.get('parameter')}"
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


def serve(host: str, port: int, root: Path | None = None) -> int:
    """Run the agent until interrupted."""
    if web is None:
        raise AgentError("aiohttp is not installed; pip install -e tool[agent]")

    import os

    from .agent_client import agent_url, remove_address, write_address

    agent = Agent(root=root)
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
    app.on_cleanup.append(agent.supervisor.release)  # stop watching; the rig plays on
    app.on_cleanup.append(agent.release_workers)
    app.on_cleanup.append(unsay_where)
    print(f"sushi-rig agent {VERSION} on http://{host}:{port}  (Ctrl+C to stop)", flush=True)
    web.run_app(app, host=host, port=port, print=None, handle_signals=True)
    return 0
