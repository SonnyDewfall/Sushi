# The agent

One process that owns the rig: it starts and stops it, changes it while it
plays, saves it, records it, and serves the [web UI](web-ui.md). Everything the
web UI does, it does through the routes on this page. For how to use the rig,
read the [user guides](../guide/README.md); this page is the detail.

## The routes

```bash
sushi-rig agent                      # serve the rig over HTTP on 127.0.0.1:8029
sushi-rig agent --phone              # on 0.0.0.0, printing the URL a phone opens on each Wi-Fi or ethernet address (#181)
sushi-rig agent install-service      # or run it as a systemd user service (prints the systemctl commands)
curl localhost:8029/health           # the agent and the rig in one answer, crashed children included
curl localhost:8029/rig              # `status` as data
curl localhost:8029/doctor           # `doctor` as data
curl localhost:8029/                 # the web UI (its files are under /ui/)
curl localhost:8029/help/            # the user guide, docs/guide/, as HTML (#167); pages at /help/<name>, pictures at /help/img/
curl localhost:8029/rigs             # the rigs of config/rigs/ a start or switch can name, and which is the running rig's
curl localhost:8029/rig/load         # one pw-top sample of the rig's nodes: load, xruns, the block budget
curl -X POST localhost:8029/rig/up -d '{"config_name": "guitar_vocal", "rig": true, "headless": true}'
curl -X POST localhost:8029/rig/down
curl -X POST localhost:8029/rig/restart                             # refused (409 unsaved) if it would drop changes no save has written
curl -X POST localhost:8029/rig/restart -d '{"discard": true}'      # ... so this drops them, as `sushi-rig restart --discard` does
curl -X POST localhost:8029/rig/switch -d '{"config_name": "electric-delay"}'   # live where it can be (#93)
curl localhost:8029/midi             # the machine's MIDI devices, what each control may take, the rig's assignments (#191)
curl -X POST localhost:8029/rig/midi/assign -d '{"control": "c", "bypass": "board/drive"}'   # a pedal control in the running rig's file (#192)
curl -X POST localhost:8029/rig/midi/assign -d '{"control": "c", "none": true}'               # ... and off again
curl localhost:8029/live             # the running rig's parameters, bypass and chains, with ranges
curl -X POST localhost:8029/live/parameter -d '{"processor": "reverb", "parameter": "Mix", "value": 0.4}'
curl -X POST localhost:8029/live/parameters -d '{"processor": "hall", "parameters": {"dry": 0.7, "early": 0.57}}'   # all or none, one change (#154)
curl -X POST localhost:8029/live/bypass -d '{"processor": "drive", "bypassed": true}'
curl -X POST localhost:8029/live/move -d '{"processor": "drive", "direction": "up"}'
curl localhost:8029/live/plugins     # which plugin each running processor is, with its role and width
curl localhost:8029/profiles         # the component profiles of tool/examples/profiles/
curl localhost:8029/live/log         # what was changed through /live, newest last
curl -X POST localhost:8029/live/undo   # put back what the newest change not yet undone replaced
curl -X POST localhost:8029/live/undo -d '{"skip": true}'   # give up on the newest one that cannot be
curl localhost:8029/undo             # what the one Undo would step back next, of any kind (#209)
curl -X POST localhost:8029/undo     # step it back: a live edit, a rig file write, a Save, a revert, a delete
curl -X POST localhost:8029/undo -d '{"seq": 12, "restart": false}'   # the page's press: that step only, and ask before a restart
curl localhost:8029/amp              # the amp as last SENT to Carla: its model, each parameter
curl localhost:8029/amp/models       # amp/models, each model's rate and whether it can be played
curl -X POST localhost:8029/amp/parameter -d '{"name": "Output Lvl", "value": -3}'   # dB, clamped and said
curl -X POST localhost:8029/amp/model -d '{"model": "Some Model.nam"}'   # restarts Carla alone: the sound stops for a moment
curl -X POST localhost:8029/rig/save                                # the running rig into its own rig file, in place (S4)
curl -X POST localhost:8029/rig/save -d '{"name": "electric-board-2"}'   # save as a new rig, which becomes the running one
curl localhost:8029/rigs/electric_board/saves                       # the points a rig can be reverted to: its saves, and its file's commits
curl -X POST localhost:8029/rigs/electric_board/revert -d '{"save": "<an id from saves>"}'   # write one back and play it; "discard": true drops unsaved work, "restart": false refuses one that would restart Sushi
curl localhost:8029/record           # recording, idle, or why recording is off (S7)
curl -X POST localhost:8029/record/start -d '{"name": "riff-idea"}'   # a take; no name is <rig>-<date>-take-<nn>
curl -X POST localhost:8029/record/stop                              # finish it: take check's verdict in the answer
curl localhost:8029/takes            # the takes, newest first, each with take check's verdict
curl -X POST localhost:8029/takes/open   # open the takes folder in this machine's file manager; loopback only (#189)
curl localhost:8029/machine/roles    # the machine file's roles: ports, the widths each can feed, who uses each
curl localhost:8029/rigs/guitar_vocal/channels                      # a rig file's channels, as the file has them
curl -X POST localhost:8029/rigs/new -d '{"name": "two-guitars", "channels": [{"name": "clean", "input": "guitar-in", "width": "mono"}, {"name": "dirty", "input": "guitar-in", "width": "stereo", "from": {"rig": "electric_board", "channel": "board"}}]}'
curl -X POST localhost:8029/rigs/guitar_vocal/channels/add -d '{"name": "keys", "input": "vocal-in", "width": "stereo"}'
curl -X POST localhost:8029/rigs/guitar_vocal/channels/vocal/input -d '{"input": "guitar-in"}'   # or "width", or both
curl -X POST localhost:8029/rigs/electric-delay/delete -d '{"dry_run": true}'   # what deleting it would do, moving nothing
curl -X POST localhost:8029/rigs/electric-delay/delete                           # into config/rigs/.deleted/ (#129); never the running rig
curl -X POST localhost:8029/rigs/electric-delay/restore -d '{"entry": "electric-delay-20261116T101502Z.yaml"}'   # back from the bin; no entry: the newest
curl localhost:8029/rigs/bin             # what is in the bin, newest first, and which agent's bin it is
curl -X POST localhost:8029/rig/channels/vocal/load -d '{"from": {"rig": "string-machine", "channel": "strings"}}'   # writes the rig file, then plays it live where it can
curl -X POST localhost:8029/rig/channels/vocal/remove -d '{"restart": false}'   # the channel out of the running rig (#190): a restart, so refused (409 restarts) until "restart": true

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

`install-service --phone` (or `--host ADDR`, `--port N`) writes the unit with
`sushi-rig agent --phone` in its `ExecStart`, and its `Description` saying
`0.0.0.0`, so the service listens on the network without a `systemctl --user
edit` override; with none of them the unit is the committed one as it is.

## From a phone

`--phone` is `--host 0.0.0.0` (with another `--host` beside it, refused), and
once it listens it prints a `http://<address>:<port>/` for each interface a
phone may be on, labelled `Wi-Fi` (`/sys/class/net/<if>/wireless`) or
`ethernet`, Wi-Fi first; loopback, and Docker's, VMs' and tunnels'
interfaces (under `/sys/devices/virtual`, or named `docker`, `virbr`, `veth`,
`tun`, `wg`, …) are left out (`reach.py`: `socket.if_nameindex` and two
unprivileged `ioctl`s; IPv4 only). `/health`'s `agent.listen` is `{host,
port, network, urls}`, the interfaces looked at again every 5 s, and the
rack's "Agent on …" line draws it.

Before it starts anything, the agent tries the address (`reach.bind_problem`).
When the port is taken it says, instead of a traceback, who has it: the
`sushi-rig-agent` user service (its `/proc/<pid>/cgroup`, or `systemctl --user
show`'s `MainPID`), another agent (its command line, or the pid in
`agent.json`), another program (its pid and name), or a process this user
cannot see; and whether what has it listens on localhost only, which is why
a phone cannot reach it. It ends with the command that stops it. The
[guide's Playing out](../guide/playing-out.md) is the player's side of this.
**Not yet tried from a real phone with these commands**, nor as a service.

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

### Another site's page, and DNS rebinding

One guard, a middleware (`Agent._same_site_only`, #161), stands in front of
every route, those above and any added later, and answers `403` with
`code: "cross_site"` before the route reads anything. It exists because any
web page open in a browser on the rig machine, or on a phone that can reach
the agent, can send
`fetch("http://localhost:8029/rig/down", {method: "POST", mode: "no-cors"})`
with no preflight. It refuses:

- **a write from another site's page**: a `POST`, `PUT`, `PATCH` or
  `DELETE` with `Sec-Fetch-Site: cross-site`, or an `Origin` that is not the
  agent's own scheme, host and port (a page of another port on this
  machine, `Origin: null` from a sandboxed or file page);
  `cross_site_refusal`. A `GET` from another site is not refused, since a
  link to the page from elsewhere is one, and that site cannot read the
  answer, which carries no CORS header. No `GET` route changes anything;
  `test_agent_cross_site.py` lists them, so a new one is reviewed.
- **any request by a name the agent does not answer to** (DNS rebinding),
  reads and page loads included: a page of an attacker's name whose DNS is
  then pointed at this machine is same-origin to the browser, so it could
  read every answer and send every write. The `Host` must be an IP
  address, `localhost`, this machine's hostname (and its first label), that
  label with `.local`, the `--host` it was bound to, or a name given with
  `sushi-rig agent --allow-host NAME` (repeatable; a name alone: a scheme,
  a port or an IP is refused at the command line); `rebinding_refusal`.
  An IP is always allowed because a page loaded by IP has no name to rebind.
  Binding to localhost does not stop rebinding, since localhost is what the
  name is rebound to. The check does not wait for a browser's headers,
  because there are none to wait for: Chromium sends no `Sec-Fetch-*` to a
  plain-http name other than localhost, and no `Origin` on a GET or a page
  load (captured from Chromium 153 on 2026-11-17), so a rebound page's read
  carries only its `Host`.

Allowed: curl, the CLI (`agent_client`, urllib, at the address the agent
wrote, so the bound `--host` or 127.0.0.1) and scripts, by any of those
names; curl pointed at another name (`--resolve`, `/etc/hosts`) is refused
like a browser, and `--allow-host` allows it. The pedals' MIDI listener runs
inside the agent and sends no request at all. The agent's own page is allowed
from this machine and from a phone on the LAN, by the rig's IP or its
`.local` name: its `fetch` calls are same-origin, relative paths.

**Behind a reverse proxy** (a tunnel, Tailscale Serve, nginx), give the
proxy's public name with `--allow-host`. That name is then also trusted as
an `Origin` whatever `Host` says, on any scheme and port, so both kinds of
proxy work: one that passes `Host` through (`proxy_set_header Host $host`),
and one that rewrites it to the address it forwards to (nginx's
`proxy_pass` default sends `Host: 127.0.0.1:8029`). What does not work, or
is not covered:

- a proxy that rewrites `Host` to a name of its own (an upstream or
  container name): that name needs `--allow-host` as well;
- a proxy that rewrites `Host` to 127.0.0.1 and answers any name: the agent
  sees an IP and cannot tell a rebound name from the proxy's, so reads by a
  rebound name of the proxy are not refused (writes still are, by their
  `Origin`). Make the proxy answer only its own name (nginx's
  `server_name`, with a default server that refuses the rest), or pass
  `Host` through;
- a proxy that strips `Origin` and `Sec-Fetch-Site`: writes then look like
  curl's, and another site's page is no longer refused;
- trusting a name trusts every port of it: allow only a name that serves the
  agent and nothing else.

Not covered: a device on the LAN that sends no `Origin`, which is anything
that is not a browser, and a browser too old to send `Origin` on a `POST`.
Stopping that needs authentication, #29's local/remote split. Shown in
Chromium against the agent over the UI's fake (`test_ui_cross_site.py`, with
the names mapped by the resolver rules, not DNS); **not tried on the rig,
from a real phone, behind a real proxy, or with a pedal.**

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

The page's one Undo button is [One Undo](#one-undo) (#209), which steps back
any kind of change and calls this for a live one. `POST /live/undo`, with no body, puts back the newest change that is not an
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

Pick a plugin, add it to a chain, take one out (#32). The web UI does all
three from its Library drawer (#130, [the web UI](web-ui.md)).

```bash
curl localhost:8029/library          # every plugin in plugin-manifest.txt: uri, name, ports, kind, profile, groups
curl -X POST localhost:8029/live/add -d '{"track": "guitar", "uri": "http://guitarix.sourceforge.net/plugins/gxts9#ts9sim"}'
curl -X POST localhost:8029/live/add -d '{"track": "guitar", "uri": "http://drobilla.net/plugins/mda/Delay", "name": "slap", "after": "guitar_overdrive"}'
curl -X POST localhost:8029/live/add -d '{"track": "guitar", "uri": "http://drobilla.net/plugins/mda/Delay", "first": true}'
curl -X POST localhost:8029/live/remove -d '{"processor": "slap"}'
```

`/library` lists the manifest, not the machine: names and port counts come from
the bundles in `plugins/`, read without lilv (and checked against lilv on every
bundle the packages provide), never from the system's LV2 paths. A name that
could not be read is the URI's last segment, marked `name_from: "uri"` with a
`note` saying why; a profile's groups come with its plugin. Each entry's `kind`
(drive, mod, delay, reverb, dynamics, EQ, or null) is read from the plugin's
own LV2 classes in its bundle (`classes`, null where the bundle was not read),
never from its name, and the answer's `kinds` lists them in the order the
drawer groups by (`library.KINDS`). It is built once,
on first use, and kept until the agent restarts. The amp is listed but
`addable: false`: it runs in Carla, and on a Sushi chain it would have no model.
An add needs a manifest URI and a track; with no `name` it is named from the
plugin (`MDA Delay` is `mda_delay`), and with no `after` it goes last but in
front of the channel's send, never behind it, since the send is what reaches the
monitors. `"first": true` in place of `after` puts it at the start of the
chain, which no `after` can say: in front of the first plugin, or of the send
on a channel with none, and on the master behind its return, which stays
first; both together are `400`. The send is known by its uid in Sushi's session as well as by its
name, and `/live/move` now refuses (`400`) to move a send or the return, or a
pedal past one. A bad request is `400` with Sushi untouched; a name the rig already
has, a track it lacks, an `after` not on that track, or a remove of a send,
the master's return, a track or something not there, is `409`; a name taken
(the plugin's, its copy's, or a track's) carries `code: "name_taken"`, for a
client that chooses names to choose again. Both edits are
confirmed by reading the chain back, and one that does not read back as asked
is `502` and not logged. The log entry for an add has the chain before
(`previous`) and in `args` the name used, `first: true` for one put first, its `copy` to stereo (or null), the
`chain` it left, and `defaults`: the plugin's parameter values read straight
after the add, which are what a save compares with. Defaults Sushi will not
give, or do not give in time, leave the add standing and logged, `null`,
with a `note` saying a save will write every value. A remove's entry has the chain before, the plugin's URI, uid and
type from Sushi's saved session, its `copy`, the chain before as `units`
(each plugin with its copy, grouped as the remove grouped it, with Sushi's
uids), and its `parameters` and `bypassed`, read just before the remove (a remove whose values cannot be read
is `502` and removes nothing).

### The master's limiter and the master volume's top

Every compiled rig's master ends with `master_limiter`, mda/Limiter at fixed
settings (#137, option 2; [the rig file](rig-file.md#the-masters-limiter-137)).
The agent treats it as the compiler's, by the one rule a send and the return
go by (`live.protected_processor`): a remove of it is `409`, a move of it or
past it is refused, an add with no place, or after the master's last plugin,
goes in front of it, and one `after` it is `409` (`live.is_tail`); a live
switch never adds behind it. It is never bypassed, and none of its
parameters is set to anything but its fixed value (`400`,
`rigfile.limiter_problem`), through `POST /live/parameter`,
`/live/parameters`, `/live/bypass`, an undo or a switch's step; setting it to
its own value, or bypass off, is allowed, since that puts it right, and is
what a live switch does when Sushi had it otherwise. `GET /live/plugins`
gives it the role `"limiter"`.

A plugin cannot be added live under the compiler's names (`master`,
`master_return`, `master_limiter`; `400`, `agent.add_request`, as `check_rig`
refuses them in a file): on a rig started before the limiter, one added as
`master_limiter` would be protected by its name, and then could be neither
removed, bypassed nor saved.

**The master volume stops at 0 dB**, because Sushi applies the master's gain
after its plugins, the limiter included. A set of the master's `gain` above
`rigfile.MASTER_GAIN_MAX` (120/144 normalised, a float32's worth of slack)
is refused with `400`, as a value outside 0 to 1 is, rather than clamped: a
fader asked for +3 dB that played 0 dB would say one thing and do another.
That holds for the route, `/live/parameters`, an undo (an undo back to a
level over 0 dB, from a rig started before the cap, is refused) and a
switch's step. **The master's pan stays at the centre** (0.5, `rigfile.MASTER_PAN`)
for the same reason: Sushi pans with the gain, after the plugins, and its pan
law lifts the side panned to by up to 3 dB (a +12 dBFS sine through the
limiter came out at +2.0 dBFS on the left with the pan at 0.0, −1.0 at the
centre, measured in Sushi in PR #172's review). Every pan but the centre lifts
one side above unity, so the rule is the centre: any other pan is refused
(`400`, `rigfile.master_pan_problem`) on the same paths, a captured one is put
back in the compiled config, a rig file has no key for it and the page no
control. The dip below never puts more than 0 dB back. A master over it is
there by design on a rig started before the cap from a file whose master
level was above 0, or was set beside the agent; the next dip brings it to
0 dB and says so: `dip.capped` and `master_capped` in the edit's answer (a
live switch's too), a line on stderr, and `agent.master_capped` in `/health`
(`at`, `from_db`, `message`) until a lifecycle command works. A save
writes no more than `level: 0.0`, and a rig file that says more still
loads, compiled at 0 dB. **Not run against a live Sushi**: the refusals are
shown against the fakes; the limiter's sound in Sushi's offline renders
(`tests/test_limiter_sushi.py`).

### The dip around a chain edit

An add, a remove and a move change Sushi's graph while it plays, which can pop
(#137). So each is sent inside a **dip of the master**: the agent reads the
master track's own `gain` (the master volume, #131), sets it to 0 (Sushi's
-120 dB floor), waits (`ramp.settle`), sends the edit and confirms it as
the route always has (an add and a remove from a walk of the chains, a move
from Sushi's strict answer), then reads the gain again and puts the level it
read back. **One set each way**: Sushi 1.3.0 smooths a track's gain itself
(a one-pole filter with a 20 ms rise, stepped every 64 samples and ramped
across each, read from its source), so the level falls about 0.95 dB a
millisecond, is 30 dB down when the edit goes in, and is back within 1 dB about
20 ms after the second set. The wait is the 32 ms the smoother takes to fall
30 dB, plus one block of the rig, since Sushi takes an event at the start of
the block after it arrives: the block pw-top last measured (any `GET /rig/load`,
or the load read before an add, keeps it until a lifecycle command), and
1024 samples at 48 kHz where none has been measured, since the rig's own
captured pw-top sample ran at 1024 though the rig asks for 256. So the wait is
about 37 ms at 256 and 53 ms at 1024 or unmeasured (the answer's
`dip.wait_ms`), and the master is more than 1 dB down for about 57 or 73 ms
plus the edit's own commands, which have not been timed on the rig.
The undo of an add, a remove or a move dips as the route does, and so does the
remove that takes back a plugin an undo could not set as it was; parameters, bypasses and
the volumes never dip. A live switch dips once, around all its structural steps
(below).

The dip is no change of the rig's: it is **never logged**, Undo never offers it,
and a save, which captures under the live lock the dip holds, never sees it;
`GET /live`, which does not wait for the lock, gives the master's `gain` as the
level the dip will put back, when a dip was under way as its read began or as
it ended, or ran within it. The level comes back when the edit fails part-way
or raises, too. **A master volume set during the dip is not lost**: one through
the agent (the page's MASTER fader is `POST /live/parameter`) waits for the live
lock and lands after the level is back, its `previous` the player's level; one
that reaches Sushi beside the agent (a MIDI mapping Sushi serves itself, the v1
panel's OSC) is seen when the agent reads the gain again, since it is neither
the floor nor the level from before, and is left as it is. Not seen: a change in
the instant between that read and the agent's set, or one to exactly the floor
or exactly the level it started at.

The answer to each edit carries `dip`: `made`, `ms` (how long the master was
down as the agent timed its two sets), `kept` (a level someone else set during
it, left), or `made: false` with `why` (a rig with no master track, a master
already at its floor, a gain Sushi will not read: the edit goes ahead). A gain
Sushi will not turn down stops the edit (502), after an attempt to put the level
back. A level that cannot be put back after an edit that worked leaves the edit
standing and logged, with `dip.restored: false` and a `warning` saying the rig
may be silent, printed on stderr and in `/health`: in `last_error` (kind
`master`), which a later change may replace, and in `agent.master_down` (`at`,
`message`, and `level`, the level the dip meant to put back), which stays until
the master volume is set through the agent (by the page, an undo or a
live switch), a later dip puts a level back, or a
lifecycle command succeeds. **A save while it stands** writes `level` as the
master's, not the floor, and says so in the answer's `master`; once the player
has set the master again, a save writes theirs. After an edit that failed, the
answer's reason says both, as the edit's own error: a 504 stays a 504 with the
call it left running, so its connection, a pedal's included, is closed only
once that call returns. **Not run against a live Sushi**: the order of the
calls and every failure above are shown against the fakes only, and nothing of
it has been heard.

### The load before an add

Before an add, the agent reads the load `GET /rig/load` reads, from the same
shared pw-top sample, so an add while a page polls costs no second run (#137).
Over 80 % of the block (`top.ADD_BUDGET`, the rig's `total_load`, the figure
the page's LOAD tile shows) the add is refused, `409` with `code:
"over_budget"`, the figure in its reason ("the rig is using 86% of its
21.333 ms block, over the 80% a live add may start from...") and `load`
beside it, before Sushi is asked anything. Every add, by any path, drops the
sample held and the pw-top run under way, so the next reading is of the rig
with it in; an add that waited for the live lock while another went in reads
the load again once it has the lock (a fresh pw-top run, about a second, which
other live changes wait behind), so two queued adds cannot both pass on one
reading. A load that cannot be read, for any reason (pw-top missing, failing,
slow or not runnable, a state file that cannot be read, a sample that cannot be
parsed, or nothing of the rig's measured), refuses nothing: the add's answer
says why in `load.why`, and the agent prints a warning; `load.measured` and
`load.budget` are there when it was read. Only `POST /live/add` is checked; an undo that
puts a removed plugin back, and a live switch's adds, are not. 80 % is a margin,
not a prediction: what the next plugin costs is not known until it runs. Not
run on the rig.

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
rig-stepping footswitch (#191), or a
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
refused (`409 rig_problem`) with the running one left playing, and so is one
that loads a plugin `plugins/` lacks (`409 plugins_missing`, #188), with the
words `sushi-rig up` refuses it in: each plugin, where it is instead, and the
command that puts it there ([Plugins](plugins.md)). A revert or a channel
load that would switch the running rig into such a file is refused
the same way before the file is written. `POST /rig/up` refuses such a rig
(`409`), through `up`'s own check, before anything is started, and
`POST /rig/restart` before the running rig is stopped, so it plays on. Run on the rig on
2026-11-02 between electric-chorus-2 and electric-delay: both switches were
live, in about 0.4 to 1.2 seconds, with no pops or clicks heard
(`tool/spikes/live_rig/RESULTS-ui-2026-11-02.md`, step 7); switches by restart
were measured through a loopback on 2026-11-12
(`tool/spikes/live_rig/RESULTS-loopback-2026-11-12.md`). The steps of a live
switch that add, remove or move a plugin, or set the amp, are not recorded as
run on the rig. Those that add, remove or move are made inside **one dip of the
master** (#137, [the dip](#the-dip-around-a-chain-edit)), from before the first
to after the bypass that follows the last add; the parameters, which may set
the next rig's master volume, come after the level is back. A switch that fails
part-way puts the level back before it restarts. Its answer carries `dip`.

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
tracked files are changed (`take.library_state`, `save.file_git_state`). **A Save as whose file waits for a restart** (a pedal assigned to a volume,
or a channel added to the file, #192) makes the running rig the new file's
by name only: the config recorded for the run stays the one Sushi loaded,
renamed (`renamed_running`), since a compile of the new file would claim
cc_mappings, MIDI inputs or tracks Sushi never loaded, which the pedals'
listener and the next live switch read. The answer's `restart` says why,
as `rigfile.diff` gives it, and the note says it plays at the restart. A
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
committed is the one you hear; the answer says what git says of that file,
read just before the lifecycle lock is taken, so before anything is written). Each plugin is named for the new
channel as a load names it (`<channel>_<base>`, below), so two channels may
start from one channel, and a rig's channel from that rig's own.
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
and `--host 0.0.0.0` stays your call. **On the rig**: a channel added from the
page and played after the restart it offers has been done (a vocal channel on
electric-chorus-2, at Sonny's word; MANIFEST, 2026-11-14). `POST /rigs/new` and
a channel's input change are not recorded as run against a live Sushi: the
rigs they write are shown to compile and wire as `up --rig` would.

## Assigning a pedal

What the rack's pedal badges call (#192). `POST /rig/midi/assign` writes one
control of the device the running rig's map is for into that rig's file's
`midi:` (the shape is [MIDI](midi.md#the-device-model-191)'s):

```bash
curl -X POST localhost:8029/rig/midi/assign -d '{"control": "c", "bypass": "board/drive"}'   # C turns the drive on and off
curl -X POST localhost:8029/rig/midi/assign -d '{"control": "d", "mute": "vocal"}'           # D, the vocal channel
curl -X POST localhost:8029/rig/midi/assign -d '{"control": "pedal", "volume": "lead"}'      # the pedal rides lead's volume
curl -X POST localhost:8029/rig/midi/assign -d '{"control": "pedal", "parameter": "lead/hall/Mix"}'
curl -X POST localhost:8029/rig/midi/assign -d '{"control": "pedal", "blend": ["clean", "lead"]}'
curl -X POST localhost:8029/rig/midi/assign -d '{"control": "c", "none": true}'              # C does nothing on this rig
```

- **One control, one thing.** The control's own entry is changed in place,
  keeping its comment; a control not in the map yet is added at its end. A
  control that did something else is moved (the answer's `change.moved` says
  from what), and another control with the same binding on the same target
  is taken off it (`change.replaced`): choosing C for an effect D switched
  means C instead of D. `none` takes the control's entry out; a map left with
  no controls and no `programs` is taken out whole, so the rig no longer
  waits for a pedal it does not use. The map's list is matched by `control`
  when the file is written, so a control taken out of the middle takes its
  own comment and leaves the others theirs.
- **Which device.** The map's own `controller`, which an assignment's
  `controller` must match (`409 controller`). With none, the one device in
  the machine file with a `type` is written in (`change.controller_set`);
  several is `409 which_controller` with their names in `controllers`, and
  the assignment again with `controller` writes it.
- **The compiler decides.** The edited file goes through
  `rig.compile_rig_draft` against the run's machine file, as a channel edit
  does, and nothing is written unless it passes: `409 rig_problem` with
  `compile_midi`'s and `check_rig`'s own words for a switch given a volume, a
  pedal given a bypass, A or B (a job on every rig), a control the device
  does not have, a target the rig file does not have (a plugin added live
  and not saved is one), the master's volume, or two drivers of one volume.
  `400` for a body of the wrong shape: not exactly one binding or `none`, a
  target that is not text (a blend, two), a key nobody reads.
- **Live where only the agent's bindings change.** Sushi loads its
  `cc_mappings` with the config. An assignment whose compile leaves the
  running config's `midi` section as it is (a footswitch on a bypass or a
  mute: [the agent's own](midi.md#what-the-agent-does-with-a-pedal)),
  and every one of whose bindings names a processor the running rig has
  (its config's tracks and plugins, less those removed and plus those added
  through the agent since the last save or switch: `assignment_not_live`),
  is applied at once: the running config's `_midi_agent`, and the controller
  in its `_meta`, are replaced by the edited file's (written whole, through a
  scratch file and a rename), and the running listeners take the new plan in
  place (`_midi_swap`): nothing stops, so a press queued or a heel's
  silencing in flight goes on, and a switch's state is kept where its
  binding did not change. The answer is `how: "live"`.
- **Sushi's mappings tried live (#210).** One whose only difference is
  Sushi's `midi` section (the pedal on a volume, a parameter or a blend) is
  written, then applied with elkpy's `connect_cc_to_parameter` and
  `disconnect_cc` on one connection under the live lock (`Live.connect_cc`,
  `disconnect_cc`, `cc_connections`; `Agent._midi_cc_live`), and Sushi's own
  list read back: every mapping added there, every one removed gone. Then
  the running config records the new `midi` section beside the bindings, and
  the answer is `how: "live"`. Not tried where `live_cc_plan` says it cannot
  be (a mapping on a MIDI input Sushi did not open at its start, or on a
  processor the running rig does not have), nor where the agent's Sushi
  connection lacks the calls. Refused, unconfirmed or not in the list read
  back: undone as far as Sushi lets it, and the restart below, with `why`
  `midi_live` saying what happened. **Unverified on a real Sushi**: if 1.3.0
  does not honour the calls, every such assignment is the restart, as before.
- Any other (a target in the file but not on the running rig,
  `not_running`; anything `rigfile.diff` must restart for) changes what
  Sushi maps or names what it does not run: `how: "restart"`,
  `offer: "restart"` and `why`, `rigfile.diff`'s reasons as a channel edit
  gives them (`midi`, and `host` when the rig had no MIDI input before), and
  until that restart the pedals do what they did. The listener keeps
  planning from the running config, not the edited file
  ([MIDI](midi.md#assigning-from-the-rack-192)). If the write outlasts the
  answer's deadline (`504`), the listener follows what it left once it is
  over (holding the lifecycle lock as `busy: "MIDI"`); a write that finds the
  run changed by then (a restart or a switch since: the state file's
  `started_at`, `config` or `rig` not as read under the lock) writes nothing
  and is not followed. The running config and the state file are written
  whole: to a scratch file of their own, flushed, and renamed over, keeping
  the file's permissions.
- **Nothing to change is not a write.** `none` for a control the map does
  not have, and an assignment the map already says, answer `how:
  "unchanged"` with nothing written (and never `which_controller`); `none`
  for a control no device the map could be for has is `409 no_control`.
  That restart reloads the file, so an assignment that needs it is refused
  while the rig holds unsaved changes (`409 unsaved`) or runs with its panel
  (`409 panel`), as a channel edit is ([above](#new-rigs-and-channel-edits));
  one that plays at once loses nothing and is not, nor is one tried live
  (above): if it falls back, the restart it offers is itself refused until
  the work is saved or dropped. The page answers `unsaved` with **Save &
  assign** (#210): the save, then the same assignment, whose answer offers
  the restart.
- **Behind the file.** `GET /midi`'s `behind` is `map_behind` of the running
  config and a compile of the rig file as it is now (`midi`, `agent`, and
  `restart` when Sushi's half differs), which the rack's PEDALS section
  offers a restart for (#210).
- **Only the running rig**, from `config/rigs/` (`409 no_rig` otherwise).
  Under the lifecycle lock (`busy: "rig file edit"`) and the live lock, as
  the channel edits are; behind [the one guard](#another-sites-page-and-dns-rebinding).
  Never committed: the answer gives what git says of the file. Not in
  `/live/log`; the one Undo puts the file back ([One Undo](#one-undo)), and
  the map with it as the assignment applied it: the agent's bindings and
  Sushi's mappings live where the assignment's were (`_undo_bindings`, by
  the same `_map_plan` and `_map_cc_live`), else a restart offered.
  `assignments` in the answer is the file's `midi:` after it.

**Linked.** A listener links its pedal to Sushi's MIDI input, by the ALSA
names (client `Sushi`), when Sushi maps something of it (the rig's map's
controller, with cc_mappings), at its start, when the pedal is plugged in and
when a Sushi starts; a link already there is left as it is, and one this
agent made that the plan no longer asks for is taken down, never one it did
not make. `/health`'s `agent.midi.feeds` say `linked_to_sushi` (from ALSA's
list), `link_wanted` and `link` (why), and each change is a line on stderr:
[MIDI](midi.md#linking-the-pedals-to-sushi-210). Without the agent no link is
kept; `up` with no agent (and `--direct`) links the rig's own pedal once, by
the same rule.

**Heard.** `/health`'s `agent.midi.seen` is the last message from each
control of each device the listener reads, bound to anything or not:
`{controller: {control: {count, value, ago}}}`, `ago` in seconds on the
agent's clock. Which control a message is from is the machine file's word
(its CC or note and the controller's channel); a message no control sends is
not counted there (`last` still says it). Kept across re-plans and restarts
of the listener, while the agent runs. A dictionary lookup on the agent's
loop per message: nothing near the audio. It is what the rack's lights read,
and is a check that the board sends, not a learn.

**Not run on the rig**: no board has been plugged in. Tested against the
agent's real route, the real compiler and `write_rig` on copies of a rig file
in a temporary checkout, and the agent's real listener on a fake port
(`tool/tests/test_agent_midi_assign.py`).

## Deleting a rig file

What the rack picker's **Delete** calls (#129); the one Undo puts it back
([One Undo](#one-undo), #209). `POST
/rigs/<name>/delete` moves `config/rigs/<name>.yaml` to
`config/rigs/.deleted/<name>-<stamp>.yaml` (`rigbin.py`; the stamp is the UTC
second, `-2`, `-3` for a second delete in the same second). Everything that
lists the rigs globs `config/rigs/*.yaml` and nothing deeper, so the bin is
in none of them: `/rigs` and the picker, the Program Change numbers
(`program_order`), the sound references' list (`render.rigs_to_check`, so
`nulltest --all`), and the tests over every committed rig
(`rig_fixtures.committed`). The bin ignores itself (a `.gitignore` of `*`
written with it) and is in the repository's `.gitignore` too. **Nothing is
committed or staged**: a committed rig deleted shows in `git status` as
deleted, and `git checkout HEAD -- config/rigs/<name>.yaml` brings its
committed version back as well as Undo does. **Because the bin is ignored,
`git clean -x` (or `-X`) empties it**: a rig in the bin that was never
committed is then gone for good.

- **Nothing another program writes is replaced or removed.** The CLI's
  `save`, a `git checkout` and an editor write `config/rigs/` without any
  lock of the agent's, so neither route makes a placeholder or moves over a
  name: each move is a hard link, which fails atomically if the name is
  taken, then the unlink of the old name. A delete unlinks the rig file only
  if it is still the file it linked (device and inode): one replaced in
  between is left as it is and the delete refused (`409 changed`). A crash
  between the two leaves the file in both places, never in neither.
- **Refused for the running rig** (`409 running`): the rig `/health` names
  (`rig.rig`, the state file's rig), whose file a restart reloads and the next
  save writes. Switch away or stop it first. With no rig running, or a v1 run
  (which holds no rig file), any rig may go. Checked under the lifecycle lock
  (`busy: "rig file edit"`, as the rig files routes take it), so no start,
  switch, save-as or revert lands between the check and the move; while one
  runs the delete is `409 busy`. `GET /rigs` gives the same rig as `running`
  (`running_rig_listed`), which the page marks.
- **Plain names only** (`409 name`): a name with `/` or `\`, `..`, a leading
  dot or a control character, or longer than 200 bytes, is refused before
  anything is read, however it was spelt in the URL (`%2F`, `%2E%2E`); a bin
  entry is that name, the stamp and the suffix, and its rig part is held to
  the same rule. A plain name `/rigs` does not list is 404. The rule is not
  `/rigs/new`'s `validate_name`, which is for a name a new file is given: a
  delete names a file that exists, and a rig whose name that rule would
  refuse (`Guitar.Vocal.yaml` compiles) can still be deleted.
- **Symbolic links are never followed** (`409 link`): a rig file, the bin or
  a bin entry that is one, or that resolves outside `config/rigs/` (or the
  bin), is refused, and `GET /rigs/bin` lists a bin that is a link as empty,
  with `problem`.
- **Only this agent's page, or no browser at all** (`403 cross_site`): as
  every write is, by the guard in front of them all ([Another site's page, and DNS
  rebinding](#another-sites-page-and-dns-rebinding)).
- **The answer** has `binned` (`entry`, the bin file's name, and `file`),
  `undo` (the restore call that puts it back), `agent` (which bin this is,
  as `GET /rigs/bin` says), `committed: false`, `git` (whether HEAD has the
  file, whether the bytes binned are HEAD's, and the shell-quoted `git
  checkout` that brings HEAD's back; `null` with `git_note` when git cannot
  be read or does not answer within `SAVE_TIMEOUT`, read outside the lock),
  `programs` when a number moves (`programs_deleted`: each later rig down
  one, `moved` as `{rig, from, to}` with the tables before and after), and
  `note`. `{"dry_run": true}` gives the same answer, and the same refusals,
  with nothing moved and no lock taken (the page's confirm showed it until
  #209; the page asks no confirm now).

`POST /rigs/<name>/restore {"entry"?}` is what the one Undo does for a
delete: it moves the bin file
`entry` back to `config/rigs/<name>.yaml`, or with no entry the newest of
`name`'s in the bin. Refused (409): `taken` when anything is at that name
again (never replaced; the binned one stays), `gone` when the bin file is not
there (removed by hand, or put back already) or `name` has nothing in the
bin, `entry` for an entry that is not `name`'s, `name` for a name or entry
that is not plain, `link` as above. The answer has `from`, `programs` when
numbers move back, and `note`.

`GET /rigs/bin` is the bin, read only, newest first: each entry's `rig`,
`entry`, `file`, `deleted_at` and `back` (a rig of that name is in
`config/rigs/` again, so a restore would be `taken`), and `agent`, a hash of
this machine's name and the checkout's path, the same across the agent's
restarts. The page no longer reads it: until #209 it offered an Undo of each
delete for 15 s (#189), and before that kept the entries in its own storage,
`sushi-rig.binned`, which it now removes and never reads. The one Undo
reaches a delete while it is among its 50 steps and the agent has not
restarted; after that the file is moved back by hand: `config/rigs/.deleted/<name>-<stamp>.yaml` to
`config/rigs/<name>.yaml`, or `POST /rigs/<name>/restore` with its entry. A revert's saves (`.sushi-rig/saves/<name>/`) are
not touched by a delete, so a rig made later with the same name lists them.
Nothing of this is recorded as run on the rig machine.

## One Undo

What the page's one Undo button calls (#209). The agent keeps a history of
what can be put back (`history.py`), newest last, **50 steps deep**, in its
memory: every page of the agent shares it and a reload keeps it, **an agent
restart clears it** (as it clears the live log). `GET /undo` says what the
next step back is (`next`: `seq`, its number; `label`, a few words such
as "delete clean"; `kind`; `op` for a live one, `act` for another; `can`,
and `why` when it cannot be), with `depth` and `limit`. `POST /undo` takes
it back. Its body may be empty, or name the step the page showed (`seq`:
another page's step, or a change landing since, is **409 `changed`**, with
`next`, and nothing is undone; the live undo is told which log entry it is
for, and refuses `changed` too if a newer one landed between its picks),
`discard` and `restart` (below):

- **`live`**: a change made through `/live`, the amp or a pedal (a
  footswitch's bypass, and a channel's mute, its send bypassed: "mute
  vocal"), by its index in the live log, undone by the live undo (`POST /live/undo`'s own code, with its
  checks that the rig is still as the change left it). Changes to one control
  within two seconds of each other are **one step** (a drag sends dozens).
  A switch (`op: "rig"`) drops the live steps, which were another rig's;
  `POST /live/undo` itself still works, and what it undoes leaves the history.
- **`file`**: a write of a rig file: a channel added, an input changed, a
  pedal assigned, a Save over the running rig's own file, a revert, a
  channel loaded from another rig, a channel removed, a new rig. The bytes before the write are kept, and a digest of what it wrote;
  the undo puts the bytes back whole (`saves.restore`) only while the file is
  still as the write left it, and a new rig goes into the bin. Then it does
  again what the write did to the running rig: a revert's (and a channel
  load's) **switch** into the file put back (`_revert_switch`; live work not
  saved since is dropped only when the Undo says `discard`, below); a pedal's
  **bindings**, live where an assignment's would be, Sushi's mappings too
  where only they differ (#210, by the assignment's own `_map_cc_live`); an edit that waited for
  a **restart** needs none while the rig runs as it did when it was written,
  since the file is what plays again (`how: "file"`), and offers one
  (`how: "restart"`, `offer: "restart"`) if the rig restarted since. A
  Save's undo also withdraws its log entry (`withdrawn`), so what it wrote
  is unsaved again, and gives back the chain edits it had folded in. Another
  rig's file: `how: "next start"`.
  What a step does to the running rig is **decided at the Undo**, not at
  the write: a file that is the running rig's now, in a run other than the
  write's (restarted, or switched to since), plays what was written, so
  putting it back offers a restart whatever the write did then. A switch
  back (a revert's, a channel load's) is **held as a revert is**: with
  unsaved live work (one a press could not undo still plays) it is **409
  `unsaved`** (or `panel`), the step kept, until `"discard": true`; with
  `"restart": false`, one that would restart Sushi (`rigfile.diff` and the
  state's blockers, as the switch decides it, and the unsaved work a
  `discard` drops: a plugin it added, removed or moved, or a value whose
  before Sushi did not report, which `discard_plan` cannot put back live)
  is **409 `restarts`** with
  `why`, until `"restart": true`. After the switch the MIDI listener follows
  the rig, as after a revert. The page sends `restart: false` and asks
  "Drop changes?" or "Restart?" before sending again.
- **`bin`**: a delete, put back by the bin's restore.
- **`none`**: a Save as, recorded so Undo does not step past it as if it had
  not happened, which it cannot take back (the new rig is the running one).

A step that cannot be put back (the file changed since, the bin entry gone
or its name taken, a value changed from the panel, a live change whose log
entries were trimmed past the live log's limit, "too old", a Save as) is
**409 `cant_undo`**, with `skipped` and `next`, and is dropped: the next
press goes on to the one before; a press never passes over a step to undo
an older one. A drag of which only some could be put back is "Partly
undone". `up`, `down` and `restart` end the live steps, as a switch does:
they were made to the run that was up. One that changed no run (an `up`
refused for a rig already running, a restart that failed before taking the
rig down) leaves them. `busy`, and what Sushi or a write answers (502, 503, 504), leave
it to be tried again. `nothing_to_undo` with none left. One undo at a time;
a file's takes the lifecycle lock (`busy: "undo"`) and, for the running
rig's file, the live lock. **For a new kind of action**, record it where it
happens: a rig file write with `_history_file(path, before, after, label,
run=run, apply=...)` after the write, anything else with a `kind` whose
reversing method is in `Agent._UNDOERS`. A channel load (`POST
/rig/channels/{channel}/load`, #208) records its write with
`apply="switch"`, label "load accustomed / board"; a channel's removal
(#190) likewise, label "remove channel vocal", `act: "remove_channel"`
(`apply="none"` for a channel that was not yet playing). Putting a removed
channel back is always a restart: the live operations make no tracks. Not run against a live Sushi or on the rig.

## A channel from another rig

Any channel of any rig in `config/rigs/` loaded onto a channel of the
running rig (#208, which replaced #127's channel presets): its chain, each
plugin's settings and bypass, and its `level`, read from that rig's file as
it stands. What a load does to a chain and to a MIDI map is
`channelcopy.py`'s; whether the result is a valid rig is, as always, the
compiler's (`rig.compile_rig_draft`). The page lists the channels from
`GET /rigs` and `GET /rigs/<rig>/channels`, as "start from" does.

- **`POST /rig/channels/<channel>/load {"from": {"rig", "channel"},
  "rig"?, "discard"?, "drop_midi"?}`**: the channel's chain and level replaced by
  the source's, each plugin named for the channel (`processor_name`:
  `lead_compressor` on `lead`, the source channel's prefix taken off and
  this one's put on, always, so two base names never become one name), its
  input, width and strip its own. **The source rig's MIDI map is not
  copied.** Like a revert: **the rig file is written** (in place, comments
  kept, the old chain and the comments inside it replaced whole,
  `write_rig(replace_chains=...)`, the comments directly above its `chain:`
  kept, with [the provenance line](rig-file.md#a-channel-copied-from-another-rig)
  above it), the file as it was is kept as a revert point once the write
  has happened, unless a save already keeps it (`kept_before`), and then
  **the running rig is switched to its file** by the live switch: live
  where `rigfile.diff` can do it, by restart where not, the answer saying
  `how` and `why`. Its `op: "rig"` entry carries `args.load` and is the
  baseline: nothing is unsaved after it and undo stops there. Refused, each
  before anything is written: `no_rig`, `panel`, `v1`, `changed` (`rig`
  given, the rig the page drew, and another running by the time the lock
  is won: a Program Change between), `no_source` (no such
  rig in config/rigs/, which is looked up by name and never made a path; no
  such channel in it; a file that will not read), `no_channel`, `name`,
  `rig_problem` (the compiler's words, a name another channel has among
  them), `plugins_missing` (a plugin plugins/ lacks, #188's check),
  `unsaved` while the rig holds live work no save has written (unless
  `"discard": true`), and **`midi`**. A mapping of the running rig's
  `midi:` onto that channel's plugins **follows its plugin**, known by its
  URI: kept where the new chain has it under the same name, moved to its
  new name where it is the one plugin of that URI (`followed`; so two
  plugins that trade names keep their pedals), among several of one URI to
  the one of the same base name or else the one in the same place, and
  otherwise orphaned: the load is refused with each mapping and why, naming
  what the new chain holds there (`mappings`), or with `"drop_midi": true`
  they are taken out of the file and said (`dropped`). A knob's mapping is
  Sushi's `cc_mappings`, loaded only with the config, so one followed or
  dropped makes the load a restart; a pedal's bypass is the agent's, and
  stays live. The answer's `from` names the source file and what git says
  of it (read just before the lifecycle lock is taken, so as it was copied
  even from the running rig's own file; `null` if git does not answer
  within the save's timeout), `amp` says whether the amp is in front of the channel (and when a
  guitar channel heard through the amp in its own rig lands where none
  is), and `width` when the source is the other width. **Discarded work is
  put back** (`discard_plan`, shared with a revert with `discard`): the
  switch's diff sets only what the two configs differ in, so each parameter
  and bypass the dropped entries set is also set, to the file's value or,
  where the file states none, to the value it had before them; a discarded
  add, remove or move leaves chains the running config lacks, which the
  switch refuses as `drifted`, so that is a restart, and the answer's
  `discard` says which, with what was put back. A load whose switch fails is
  remembered in `/health` as the load (`command: ["load", ...]`), and its
  answer's `midi_note` says the pedals follow the rig as it runs (its
  compiled config), not the file. It refuses a request from another site's
  page (`403 cross_site`), as every write does ([Another site's page, and
  DNS rebinding](#another-sites-page-and-dns-rebinding)).
- **"Start from"** (`from: {"rig", "channel"}` in `POST /rigs/new` and
  `POST /rigs/<rig>/channels/add`, #125, above) copies a channel's chain
  as a load does, each plugin named for the new channel, so two copies of
  one channel in one rig compile.

**The amp is not part of a channel.** It is one Carla instance patched in
front of `guitar-in` (`amp.amp_patchbay`), so it is in front of every
channel on that input in a rig with an amp, and of nothing else; an amp per
channel is not built. **Not run against a live Sushi**: shown against
`test_agent.RigLive` loaded from the real compile, and the real live switch
(`tests/test_agent_channel_load.py`).

## Removing a channel

A channel taken out of the running rig (#190): its chain and its send with
it, and the controls of the rig's MIDI map that name it once that is said
yes to. Made as a load is made (above): the rig file written, then the
running rig switched to it. What goes and what names the channel is
`rigedit.py`'s (`without_channel`, `midi_naming`); whether what is left is a
rig, the compiler's.

- **`POST /rig/channels/<channel>/remove {"rig"?, "discard"?, "drop_midi"?,
  "restart"?}`**: **the rig file is written** in place, comments kept
  (`write_rig`), compiled first, and the file as it was kept as a revert
  point unless a save keeps it already (`kept_before`). Then **the running
  rig is switched to its file**, which is **a restart**: the agent's own
  Sushi link takes no track out live (below). With `"restart": false` the
  removal is **409 `restarts`** with `why` (`track_removed`, and any other
  reason), nothing written, until `"restart": true` (the page's
  "Restart?"); with no `restart` it restarts, as a load does where it must.
  The listener, and its links to Sushi (#210), follow the rig after
  (`_midi_sync`). Its `op: "rig"` entry carries `args.remove_channel` and is
  the baseline. 200 with `channel`, `removed` (`plugins`, `send`),
  `dropped`, `how`, `why`, `kept_before`, `library`. A switch that fails
  leaves the file written: 409 `not_switched` (or 504), `written: true`,
  with `midi_note`.
- **A channel not yet playing** (added to the file since the rig started,
  so no track of the running config): the file alone is written, `how:
  "file"`. Nothing is switched, so no unsaved work is at stake (none is
  refused over or dropped), no `op: "rig"` is logged (the live steps stay
  Undo's) and no take is stopped; its Undo puts the file back and plays
  nothing (`apply: "none"`).
- **Refused**, each before anything is written: `no_rig`, `panel`, `v1`,
  `changed` (`rig` given and another running), `no_channel`,
  **`last_channel`** (a rig needs one channel), `rig_problem`,
  `plugins_missing`, `unsaved` (unless `"discard": true`), `restarts`, and
  **`midi`**: a parameter or bypass of one of its plugins, its mute, its
  volume, or **a blend** of it and another (a blend needs two channels),
  each in `mappings` with why, until `"drop_midi": true` takes them out of
  the file (`dropped`). `busy` while a lifecycle command runs; `403
  cross_site` from another site's page.
- **One Undo** (#209): one step, `act: "remove_channel"`, the file's bytes
  put back and the rig switched into it, which is a restart, asked about
  first with `"restart": false`.

**Taking a track out live** is built and off. `rigfile.diff(...,
remove_tracks=...)` plans a track the caller names as one `remove_track`
step instead of the `track_removed` restart (never the master; a rig switch
names none), and `live.remove_live_track` takes each processor off, back to
front, then calls elkpy's `delete_track`; the agent's switch takes it where
its `Live` has a `remove_track`. `Live.at`, the agent's own link, has none
(PR #238's review). Against Sushi 1.3.0's release AppImage (dummy frontend,
some nine hundred runs of [a probe](../../tool/spikes/live_remove_track/README.md),
2026-11-24, whose README has the counts) the track, its processors and its send went, and the
master went on with the other channel alone, nothing of the removed one in
it; but not every time. About one command in twenty was applied and never
confirmed (elkpy's notice of an async command, nearly always the first on a
fresh connection), which fails the step and restarts the rig unasked; and a
few times Sushi stopped applying graph changes at `delete_track` on the
emptied track, neither applying nor confirming it, and then ignored SIGTERM.
`delete_track` sent alone, which takes the processors off inside Sushi, was
clean 300 times in 300: the lead for making it live. The tests drive the live path against a stand-in (`test_agent.RigLive`,
`tests/test_agent_channel_remove.py`).

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
take. `POST /record/start {"name"?}` names the take (with no name,
`<rig>-<date>-take-<nn>`, which `GET /record` offers while idle as
`default_name`: [Takes](takes.md#names)), sends the run's files from the state file
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
when a file in its folder changes. The page no longer asks for it (#189: the
rack names the folder instead, and a stop's answer has the take's `folder`
and verdict); it stays for curl and scripts.

`POST /takes/open {}` (#189) opens the takes folder in this machine's file
manager: the rack's **Open folder** on the laptop. **Only for a loopback
peer**, since the folder opens on this machine's desktop: anyone else is
`403 not_local`, with the path in the reason. `/health`'s `agent.takes` is
`{dir, can_open}`, the folder and whether this request's peer may ask, which
is what the page offers the button by. Behind the guard in front of every
write, as all of them are. The folder is always the agent's own takes
folder, never one from the request, whose body must be empty (`400`
otherwise). It runs `xdg-open`, or else `gio open`, on the folder with no
shell, in its own session, off the loop: `409 no_opener` when neither is
installed, `409 no_folder` before the first take makes the folder, `502
open_failed` with what the opener said when it exits non-zero, and `200`
with `said` otherwise (an opener still running after 5 s is left to run, and
reaped when it ends). Behind a reverse proxy on this machine every request
looks local, as `is_local` says of the amp's model by path, so a phone
through such a proxy would get the button and open the folder on the
laptop's screen; nothing else. **Not run on a desktop**: the tests run
stand-in openers, and whether a systemd user service's agent can reach the
desktop session (its `DISPLAY` or `WAYLAND_DISPLAY`) is not known.

The machine file names the guitar's port by its PipeWire alias
(`Audient iD14:capture_AUX0`, what the patchbay matches), and a JACK client
sees it as `Audient iD14 Multichannel:capture_AUX0`, so each port a take names
is resolved to the name JACK sees before the take is planned
(`transport.jack_port`), and the capture engine then finds it among the ports
its own JACK client lists, by name or by JACK alias; the machine file stays as
it is, since the patchbay needs the alias. **A take was captured on the rig on
2026-11-06** (electric-chorus-2, five columns, 24.6 s, `take check` ok; see
[Takes](takes.md#the-capture-engine)). A take of a two-channel rig, guitar and
vocal, has since been recorded from the page and its `master.wav` listened
back (at Sonny's word; MANIFEST, 2026-11-14), with no figures kept. Still not
run on the rig: a stereo dry tap, the engine's own match by JACK alias, which
is the second line when the first resolution cannot read the graph, and a
stop, switch or model change made while a take is recording.
