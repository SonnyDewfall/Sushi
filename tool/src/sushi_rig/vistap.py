"""The visualiser's tap (#195): the rig's output, offered to the browser as
an input.

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

**The mechanism: `pw-loopback`, a child of the rig.** One process makes two
nodes: a capture stream (`sushi_rig_output_tap`, `Stream/Input/Audio`) that
Sushi's master outputs are linked to with `pw-link`, and a playback stream
given `media.class = Audio/Source` (`sushi_rig_output`, "Sushi rig output"),
which is the source the browser lists. Chosen over a
`support.null-audio-sink` adapter made with `pw-cli`, for four reasons:

- **Its nodes live and die with a process.** They are the client's, so when
  the process goes, by any signal, SIGKILL included, PipeWire removes them:
  there is no node to leave behind. An adapter made by `pw-cli` either goes
  with that `pw-cli` (which would then have to be kept running) or lingers
  in the daemon (`object.linger`) until something remembers to destroy it,
  which is the stray node this must never leave.
- **So it is a child like any other.** Planned by `rig.plan_children`,
  started into the rig's process group, recorded in the state file, stopped
  by `down` with the group, watched by the agent's supervisor, and kept or
  left out by `restart` and a switch through the state file
  (`visualiser_tap_requested`). Nothing new to clean up and no second way to
  stop things.
- **It cannot drive the graph.** Both nodes are streams, and a stream never
  becomes a driver, so it cannot take the clock away from the interface (the
  webcam story in `plan_children`). A null-audio-sink adapter is a driver
  candidate unless told otherwise.
- **It is never linked to the interface.** Both nodes are made with
  `node.autoconnect = false`, so the session manager links neither; the only
  links are the two made here, Sushi's master outputs into the tap's inputs,
  and whatever capture the browser opens on the source. The tap is a branch
  off Sushi's outputs: Sushi's links to the interface are untouched, and
  nothing is added to the audio path or its latency.

**Its cost is in Sushi's cycle.** Both nodes are in one node group
(`node.group`), linked to Sushi, so they are followers of Sushi's driver, the
interface, every cycle the rig runs, whether or not a browser is listening;
and the cycle is finished only when they are. Measured in a container with
PipeWire 1.0.5 at the rig's 256 frames (`tool/spikes/visualiser_audio/README.md`,
"The rig's output"): BUSY about 4 to 8 µs for the capture side and 2 to 4 µs
for the source, against a 5333 µs cycle. What makes that safe is the data
thread being realtime, and `pw-loopback` reads `client.conf`, which loads no
`module-rt`: its data thread ran at normal priority, and the tap was "too
slow" about once a second. So it is started with
`PIPEWIRE_CONFIG_NAME=client-rt.conf` (the config PipeWire ships for
realtime clients), which gave its data thread SCHED_FIFO and took that to one
in twenty seconds, against the stand-in Sushi's own. `sushi-rig doctor`'s
`visualiser-tap` says whether it got realtime on this machine, and its nodes
are among the rig's in `top.CHILD_NODES`, so the page's XRUNS count, and so
the visualiser's xrun safeguard, counts the tap's xruns too.

**Links.** Made once Sushi answers (`link`, from `rig.start_children`), with
`pw-link`, and read back from `pw-link -l`. A live switch keeps Sushi and its
ports and the master stays on bus 0, so the links stand; a switch by restart,
and `restart`, start a new tap and link it again. If they cannot be made the
tap is stopped, by its pid, rather than left offering a silent source the
page would choose first. Kill by pid only, here as everywhere.
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from typing import Any, Callable

from .qpwgraph import PATCHBAY_TIMEOUT, listed_links

# The state file's name for the child, and what its log is called.
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


def command() -> list[str]:
    """`pw-loopback` as the rig starts it: two channels, left and right, in one
    node group, the capture side linked by hand and the source never linked
    to anything by the session manager. Pure."""
    return [
        "pw-loopback", "--name", SOURCE_NODE, "--group", SOURCE_NODE,
        "--channels", "2", "--channel-map", "[ FL FR ]",
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
    """The tap as `rig.plan_children` plans it. Optional: the rig plays the
    same without it."""
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


def link(
    config: Path, *, run: Callable[..., Any] = subprocess.run, settle: float = LINK_SETTLE,
    sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic,
) -> list[str]:
    """Link the master outputs of the running config (`config`, the compiled
    file Sushi loaded) into the tap, and read the links back: `[]` once
    every link is in `pw-link -l`, else one problem, worded for the start's
    warnings, after which the caller stops the tap. Idempotent: a link
    already there is left as it is, so this is also the relink.

    `pw-link a b` for each link not yet made, then `pw-link -l` again, until
    `settle` has passed: the tap's ports appear a beat after `pw-loopback`
    starts, and a `pw-link` before then fails and is tried again. `run`,
    `sleep` and `clock` are the effects, for the tests."""
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
