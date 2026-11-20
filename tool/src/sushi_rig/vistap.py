"""The visualiser's tap (#195): the rig's output, offered to the browser as
an input while the page's visualiser listens, and only then.

The page's visualiser hears what the browser can capture (`getUserMedia`,
through pipewire-pulse), and Chromium lists no monitor sources, so what Sushi
plays cannot be captured from the interface's sink. The tap is a real
`Audio/Source` node fed from Sushi's master outputs: "Sushi rig output", which
the browser lists beside the interface's own inputs.

**What "the rig's output" is.** The master track's outputs, engine bus 0
(`rigfile`: the master is always on bus 0, and ends with the limiter, #137),
so the tap carries what the monitors are sent: after every channel, the
sends, the master chain, the limiter and the master volume. For a v1 config,
which has no master track and no limiter, it is engine bus 0, where every v1
config mixes for the monitors. Never a stem, never an input.

**The mechanism: `pw-loopback`, a child of the rig the agent holds.** One
process makes two nodes: a capture stream (`sushi_rig_output_tap`,
`Stream/Input/Audio`) that Sushi's master outputs are linked to with
`pw-link`, and a playback stream given `media.class = Audio/Source`
(`sushi_rig_output`, "Sushi rig output"), which is the source the browser
lists. Chosen over a `support.null-audio-sink` adapter made with `pw-cli`:

- **Its nodes live and die with a process.** They are the client's, so when
  the process goes, by any signal, SIGKILL included, PipeWire removes them:
  there is no node to leave behind. An adapter made by `pw-cli` either goes
  with that `pw-cli` (which would then have to be kept running) or lingers
  in the daemon (`object.linger`) until something remembers to destroy it.
- **So it is the agent's child, in the rig's group.** Started into the group
  the agent holds, as the amp is restarted (`Supervisor.restart_amp`),
  stopped by its pid, and taken by `down` with the group. Nothing new to
  clean up and no second way to stop things.
- **It cannot drive the graph.** Both nodes are streams, and a stream never
  becomes a driver, so it cannot take the clock away from the interface (the
  webcam story in `rig.plan_children`).
- **It is never linked to the interface.** Both nodes are made with
  `node.autoconnect = false`, so the session manager links neither; the only
  links are the two made here and whatever capture the browser opens on the
  source.

**Why only while the visualiser listens (review of PR #200).** Linked to
Sushi, both nodes are followers of Sushi's driver, the interface, every
cycle, and the driver waits for them: a cycle is finished only when they
are. When one is late PipeWire 1.0 logs `client too slow!` beside `graph
not finished`: that cycle was not finished in time, which is a glitch of the
rig's, not a lateness private to the tap. Measured in a container
(`tool/spikes/visualiser_audio/README.md`, "The rig's output"): BUSY 5 to 13
µs for the capture side and about 2 to 3 µs for the source, against a 5333 µs
cycle at 256 frames; and "too slow" about once a second with a normal-priority
data thread, and with a realtime one once in 20 s in one run and five times in
15 s in another (on a busy machine, the stand-in Sushi itself three times in
those 15 s). Once in 20 s is about 360 unfinished cycles in a two-hour set, so
a tap is never there for a set's
length because a rig is: the page asks the agent for it when the visualiser
is about to listen (POST /visualiser/tap), renews a lease while it listens,
and gives it back when it stops. `up` from the command line never starts one.

**The lease** (`Leases`). Each page holds one, for `LEASE` seconds from its
last renewal (the page renews every `RENEW`); two pages share one tap. When
no page holds one, the tap is kept `LINGER` seconds more, so an idle spell
does not change the graph every time, and then stopped by its pid. A page
whose xrun safeguard trips gives its lease back at once, and with no other
page holding one the tap is stopped then, not after the linger: the
safeguard removes the cause it can.

**Realtime or nothing.** `pw-loopback` reads `client.conf`, which loads no
`module-rt`, so it is started with `PIPEWIRE_CONFIG_NAME=client-rt.conf` (the
config PipeWire ships for realtime clients). Once linked, a tap with no
thread at SCHED_FIFO or SCHED_RR is stopped, by its pid, and the page told
why (`realtime_refusal`): it would be late far more often.

**Links.** Made once the tap starts (`link`), with `pw-link`, and read back
from `pw-link -l`. A live switch keeps Sushi and its ports and the master
stays on bus 0, so the links stand; a switch by restart and `restart` take
the tap with the group, and the next request starts one for the new rig.
If the links cannot be made the tap is stopped, by its pid, rather than left
offering a silent source. Kill by pid only, here as everywhere.
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from typing import Any, Callable

from .qpwgraph import PATCHBAY_TIMEOUT, listed_links

# The child's name in the agent's record of the rig, and its log's.
CHILD = "vistap"

# The source the browser lists, and how it names it: Chromium and Firefox show
# a PulseAudio source by its description, which pipewire-pulse takes from
# `node.description` (seen in a container: `pactl list sources` said
# "Description: Sushi rig output").
SOURCE_NODE = "sushi_rig_output"
SOURCE_LABEL = "Sushi rig output"

# The capture side, which Sushi's master outputs are linked to, and its ports
# in pairing order (left, right) as `pw-link` names them.
TAP_NODE = "sushi_rig_output_tap"
TAP_PORTS = (f"{TAP_NODE}:input_FL", f"{TAP_NODE}:input_FR")

# The nodes the tap adds to the graph, for `top.CHILD_NODES`: the capture side
# first, since it is the one Sushi feeds.
NODES = (TAP_NODE, SOURCE_NODE)

# How long `link` waits for the tap's ports (pw-loopback registers them a beat
# after it starts) and for the links to read back. Polled: when all is well it
# returns at the first look.
LINK_SETTLE = 5.0
LINK_POLL = 0.25

# What `pw-loopback` is started with. `client-rt.conf` is PipeWire's own
# config for realtime clients: it loads `module-rt`, which `client.conf`, the
# one `pw-loopback` reads otherwise, does not (see the module docstring).
ENV = {"PIPEWIRE_CONFIG_NAME": "client-rt.conf"}

# The lease (`Leases`), in seconds. A page renews every RENEW and its lease
# lasts LEASE from then, so two renewals can be lost before it runs out; with
# no lease held the tap is kept LINGER more. Chosen, not measured.
LEASE = 30.0
RENEW = 10.0
LINGER = 180.0

# How long, once the tap is linked, its threads are looked at for a realtime
# one: module-rt raises the data thread as it starts, which is before the
# links can be made, so this normally answers at the first look.
REALTIME_WAIT = 1.0

# The scheduling policies /proc reports that are realtime: SCHED_FIFO, SCHED_RR.
REALTIME_POLICIES = {1: "SCHED_FIFO", 2: "SCHED_RR"}


def command() -> list[str]:
    """`pw-loopback` as the rig starts it: two channels, left and right, in one
    node group, the capture side linked by hand and the source never linked
    to anything by the session manager. Pure.

    The short options, as PipeWire 1.0.5's `pw-loopback` takes them: its help
    lists `-m, --channel-map`, but it refuses `--channel-map` ("unrecognized
    option") and exits, which a first run of this against a real PipeWire
    found (2026-11-20)."""
    return [
        "pw-loopback", "-n", SOURCE_NODE, "-g", SOURCE_NODE,
        "-c", "2", "-m", "[ FL FR ]",
        "--capture-props", (
            f"node.name={TAP_NODE} node.description=\"{SOURCE_LABEL} (tap)\" "
            "media.class=Stream/Input/Audio node.autoconnect=false"
        ),
        "--playback-props", (
            # priority.session 0: the lowest, so the session manager prefers
            # any real input as the default source.
            f"node.name={SOURCE_NODE} node.description=\"{SOURCE_LABEL}\" "
            "media.class=Audio/Source node.autoconnect=false priority.session=0"
        ),
    ]


def child() -> dict[str, Any]:
    """The tap as the agent starts it (`rig.start_child`). Optional: the rig
    plays the same without it."""
    return {"name": CHILD, "command": command(), "optional": True, "env_set": dict(ENV)}


def master_ports(config: Any) -> tuple[str, ...]:
    """Sushi's ports that carry the master, from a compiled config: the
    outputs of the track named `rigfile.MASTER`; for a config with none (v1),
    its outputs on engine bus 0, which is where a v1 config mixes for the
    monitors. In channel order, each once, spelled by `patchbay.sushi_ports`.
    Empty when the config names none. Pure."""
    from .patchbay import SUSHI_NODE, bus, channel, sushi_ports
    from .rigfile import MASTER

    tracks = [t for t in (config.get("tracks") if isinstance(config, dict) else None) or []
              if isinstance(t, dict)]
    master = [t for t in tracks if t.get("name") == MASTER]
    places = []
    for track in master or tracks:
        for entry in track.get("outputs") or []:
            if not isinstance(entry, dict):
                continue
            for key, make in (("engine_bus", bus), ("engine_channel", channel)):
                index = entry.get(key)
                if type(index) is int and index >= 0:
                    places.append(make(index))
                    break
    ports = sushi_ports(SUSHI_NODE, "output", tuple(dict.fromkeys(places)))
    if master:
        return ports
    monitor = set(sushi_ports(SUSHI_NODE, "output", (bus(0),)))
    return tuple(port for port in ports if port in monitor)


def tap_links(ports: tuple[str, ...]) -> list[tuple[str, str]] | str:
    """(Sushi port, tap port) for each link to make, or why there is none: the
    master is a stereo pair, left into the tap's left and right into its
    right. Pure."""
    if len(ports) != 2:
        return (f"the running config's master is on {len(ports)} of Sushi's outputs"
                f"{' (' + ', '.join(ports) + ')' if ports else ''}, not a left and right pair")
    return list(zip(ports, TAP_PORTS))


def _problem(why: str) -> str:
    return (f"the visualiser tap could not be linked to Sushi's master outputs, so it was stopped: {why}. "
            "The sound is unaffected; the visualiser listens to the guitar's input instead")


def realtime(threads: Any) -> list[tuple[str, str, int]]:
    """`(name, policy, priority)` of each realtime thread among `threads`, as
    `doctor.threads_of` gives them (`(name, policy, rt_priority)`). Pure."""
    return [(name, REALTIME_POLICIES[policy], prio) for name, policy, prio in threads or ()
            if policy in REALTIME_POLICIES]


REALTIME_FIX = (
    "give PipeWire's clients realtime priority: on Ubuntu, sudo apt install rtkit (it runs with the session), "
    "or a realtime limit for the agent's service where your account's hard limit allows one "
    "(docs/reference/web-ui.md, \"The visualiser\")"
)


def realtime_refusal(threads: Any, pid: int) -> str | None:
    """Why a tap whose threads are `threads` is refused, or None when one of
    them is realtime. Pure."""
    if realtime(threads):
        return None
    names = ", ".join(name for name, _, _ in threads or ()) or "none could be read"
    return (f"the visualiser tap (pid {pid}) got no realtime thread ({names}), so it was stopped: in Sushi's "
            "cycle a normal-priority thread is late far more often, and each time it is a cycle the graph did "
            f"not finish. The sound is unaffected; the visualiser listens to the guitar's input instead. To have "
            f"the tap, {REALTIME_FIX}")


class Leases:
    """Who holds the tap, and when it is to stop. Pure: every method is
    given the time (`now`, monotonic seconds).

    A page's lease lasts `lease` from its last `take`. While none is held
    the tap is due to stop `linger` after the last one ended (`due`); a
    `give_back(..., at_once=True)` with no other lease held makes it due at
    once."""

    def __init__(self, lease: float = LEASE, linger: float = LINGER) -> None:
        self.lease, self.linger = lease, linger
        self.held: dict[str, float] = {}
        self.idle_since: float | None = None
        self.now_due = False

    def _expire(self, now: float) -> None:
        for page, until in list(self.held.items()):
            if until <= now:
                del self.held[page]
                self.idle_since = until if self.idle_since is None else self.idle_since
        if not self.held and self.idle_since is None:
            self.idle_since = now

    def take(self, page: str, now: float) -> None:
        self.held[page] = now + self.lease
        self.idle_since = None
        self.now_due = False

    def give_back(self, page: str, now: float, *, at_once: bool = False) -> None:
        self.held.pop(page, None)
        self._expire(now)
        if not self.held:
            self.idle_since = min(self.idle_since, now) if self.idle_since is not None else now
            self.now_due = self.now_due or at_once

    def pages(self, now: float) -> list[str]:
        self._expire(now)
        return sorted(self.held)

    def due(self, now: float) -> bool:
        """Whether the tap is to be stopped now."""
        if self.pages(now):
            return False
        return self.now_due or (self.idle_since is not None and now - self.idle_since >= self.linger)

    def stops_in(self, now: float) -> float | None:
        """Seconds until it is due, None while a page holds it."""
        if self.pages(now):
            return None
        if self.now_due or self.idle_since is None:
            return 0.0
        return max(0.0, self.linger - (now - self.idle_since))

    def clear(self) -> None:
        self.held.clear()
        self.idle_since = None
        self.now_due = False


def link(
    config: Path, *, run: Callable[..., Any] = subprocess.run, settle: float | None = None,
    sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic,
) -> list[str]:
    """Link the master outputs of the running config (`config`, the compiled
    file Sushi loaded) into the tap, and read the links back: `[]` once
    every link is in `pw-link -l`, else one problem, worded for the page and
    the agent's log, after which the caller stops the tap. Idempotent: a link
    already there is left as it is, so this is also the relink.

    `pw-link a b` for each link not yet made, then `pw-link -l` again, until
    `settle` has passed: the tap's ports appear a beat after `pw-loopback`
    starts, and a `pw-link` before then fails and is tried again. `run`,
    `sleep` and `clock` are the effects, for the tests. `settle` is
    `LINK_SETTLE` when not given, read now rather than when this was defined,
    so a test that shortens it shortens it."""
    settle = LINK_SETTLE if settle is None else settle
    try:
        wanted = tap_links(master_ports(json.loads(Path(config).read_text())))
    except (OSError, ValueError) as exc:
        return [_problem(f"the running config {config} could not be read ({exc})")]
    if isinstance(wanted, str):
        return [_problem(wanted)]
    deadline = clock() + settle
    why = "no answer yet"
    while True:
        try:
            listing = run(["pw-link", "-l"], capture_output=True, text=True, check=False,
                          timeout=PATCHBAY_TIMEOUT)
        except (OSError, subprocess.SubprocessError) as exc:
            return [_problem(f"pw-link could not be run ({exc})")]
        if listing.returncode == 0:
            made = listed_links(listing.stdout)
            missing = [pair for pair in wanted if pair not in made]
            if not missing:
                return []
            why = "not made: " + ", ".join(f"{a} -> {b}" for a, b in missing)
        else:
            missing = wanted
            why = f"pw-link -l exited {listing.returncode}: {(listing.stderr or '').strip() or 'no output'}"
        if clock() >= deadline:
            return [_problem(why)]
        for source, sink in missing:
            try:
                run(["pw-link", source, sink], capture_output=True, text=True, check=False,
                    timeout=PATCHBAY_TIMEOUT)
            except (OSError, subprocess.SubprocessError) as exc:
                return [_problem(f"pw-link could not be run ({exc})")]
        sleep(LINK_POLL)
