"""Starting qpwgraph, which is harder than it should be.

A self-contained story about one external component. qpwgraph is the patchbay:
without it the rig runs with no audio in or out while looking completely healthy,
which is why this much care is spent on a program that draws boxes and lines.

It fails by exiting within a second with status 2 and writing nothing at all —
no error, no crash, an empty log. Two causes are known and handled here; the
rest is absorbed by verifying it stayed up and trying again. See
`stop_existing_qpwgraph` and `start_qpwgraph`.

This is issue #14, and the flakiness is upstream's. What this module guarantees
is that the patchbay either works or says so.
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from typing import Callable, Any


# How long to wait for a killed qpwgraph to actually exit before starting ours.
QPWGRAPH_EXIT_TIMEOUT = 5.0
# How many times to try starting qpwgraph, and how long to watch each attempt
# before believing it. See `start_qpwgraph`.
# How many times to try starting qpwgraph, and how long to watch each attempt
# before believing it. See `start_qpwgraph`.
QPWGRAPH_ATTEMPTS = 3
QPWGRAPH_SETTLE = 1.5

# How long `wait_for_links` waits for the patchbay's links to appear before
# calling the rest unmade. It polls and returns the moment every link is
# there, so a long limit costs nothing when all is well. 3.0 was too
# short: on a cold start fmit (the tuner) took more than 3 s to register its
# ALSA capture node with PipeWire on sonny-dewfall-XPS-9320 (2026-11-01), so
# `up` warned that PipeWire listed no alsa_capture.fmit ports when the node was
# merely late; the next `up` was fine. The cost lands only on the failure path:
# a tuner that never appears now holds `up` for 10 s before READY, not 3.
PATCHBAY_SETTLE = 10.0

# A bound on each `pw-link` call, so a hung one cannot hold up startup.
PATCHBAY_TIMEOUT = 2.0


class GraphUnreadable(OSError):
    """`pw-link` could not answer: a non-zero exit, so its empty output means
    "unknown", never "nothing is linked"."""


def ports_without_sources(listing: str, node: str) -> list[str]:
    """Input ports of `node` that nothing is connected to, from `pw-link -l`.

    Derived from `node_ports` and `listed_links`, so the listing is parsed in
    one place. Pure, so the decision is pinned by tests while the shelling
    out is verified by running it (quality principle 1).
    """
    sinks = {sink for _, sink in listed_links(listing)}
    return [port for port in node_ports(listing, node) if port not in sinks]


def listed_links(listing: str) -> set[tuple[str, str]]:
    """Every link in a `pw-link -l` listing, as (source port, sink port).

    The listing names a port on its own line and indents one line per link
    under it: `|->` to a sink when the port is a source, `|<-` from a source
    when it is a sink. Both directions are read, so a link appears whichever
    of its two ends the listing happened to print it under. The one parser
    of this format.
    """
    links: set[tuple[str, str]] = set()
    port: str | None = None
    for line in listing.splitlines():
        text = line.strip()
        if not text:
            continue
        if not line[0].isspace():
            port = text
            continue
        if port is None:
            continue
        if text.startswith("|->"):
            links.add((port, text[3:].strip()))
        elif text.startswith("|<-"):
            links.add((text[3:].strip(), port))
    return links


def unmade_links(
    wanted: list, names: dict[str, list[str]], listing: str
) -> list[tuple[str, str]]:
    """The (source, sink) pairs a patchbay asked for that the graph does not
    have, by alias as the patchbay wrote them.

    `wanted` is the patchbay's links (`patchbay.parse_patchbay`), `names` the
    alias -> live-name map (`patchbay.live_port_names`), `listing` the output
    of `pw-link -l`. A link is made if any live name of its source is linked
    to any live name of its sink; an alias with no live name is unmade, since
    a port that is not in the graph cannot be wired. Pure: this is the decision
    behind the warning `_supervise` prints, so it is pinned by tests rather
    than by a rig.

    Why this exists (2026-11-01): a rig can come up with every process healthy
    and a channel's source never reaching it -- the committed patchbay would
    have put guitar_vocal's guitar into the vocal channel and left the
    microphone silent, and nothing checked. The tuner had this check; every
    link does now.
    """
    made = listed_links(listing)
    missing: list[tuple[str, str]] = []
    for link in wanted:
        sources = names.get(link.out_port, [link.out_port])
        sinks = names.get(link.in_port, [link.in_port])
        if not any((a, b) in made for a in sources for b in sinks):
            missing.append((link.out_port, link.in_port))
    return missing


def wait_for_links(
    wanted: list,
    read_names: Callable[[], dict[str, list[str]]],
    settle: float = PATCHBAY_SETTLE,
) -> list[tuple[str, str]]:
    """The patchbay's links still unmade once qpwgraph has had `settle`
    seconds to make them, returning the moment every link is there.

    `read_names` is called on every poll, not once: a node that registers
    during the settle (Carla's NAM on a slow model load, an interface a beat
    late) has no live name until it does, and a map taken before it appeared
    would report its links unmade for ever. Raises `GraphUnreadable` when
    `pw-link` fails, and lets `OSError` and `TimeoutExpired` out, so the
    caller can tell an unwired rig from a question it failed to ask.
    """
    deadline = time.monotonic() + settle
    while True:
        result = subprocess.run(
            ["pw-link", "-l"], capture_output=True, text=True, check=False,
            timeout=PATCHBAY_TIMEOUT,
        )
        if result.returncode != 0:
            raise GraphUnreadable(
                f"pw-link -l exited {result.returncode}: {result.stderr.strip() or 'no output'}"
            )
        missing = unmade_links(wanted, read_names(), result.stdout)
        if not missing or time.monotonic() >= deadline:
            return missing
        time.sleep(1.0)


def node_ports(listing: str, node: str) -> list[str]:
    """Every input port of `node` the listing names, wired or not.

    This exists because an empty `ports_without_sources` means two different
    things — "every port has a source" and "this node is not in the listing at
    all" — and treating the second as the first is a check reporting success
    when it cannot tell. That is the failure this whole module's warning was
    added to prevent, so it must not be the way the warning itself behaves.
    """
    prefix = f"{node}:"
    ports: list[str] = []
    for line in listing.splitlines():
        if not line.strip() or line[0].isspace():
            continue
        text = line.strip()
        if text.startswith(prefix):
            ports.append(text)
    return ports


# How long to wait for Sushi's gRPC to start accepting connections.
def stale_qpwgraph_sockets(paths: list[Path], any_running: bool) -> list[Path]:
    """Which qpwgraph single-instance sockets are safe to remove.

    None of them while a qpwgraph is running — that socket is how the live
    instance is reachable, and deleting it would break a patchbay the user may
    be watching. Once nothing is running, any socket left behind is by
    definition stale.

    Pure, so the "never touch a live instance's socket" rule is pinned by a test
    rather than by care.
    """
    return [] if any_running else list(paths)


def qpwgraph_running() -> bool:
    return subprocess.run(
        ["pgrep", "-x", "qpwgraph"], check=False,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    ).returncode == 0


def stop_existing_qpwgraph(timeout: float = QPWGRAPH_EXIT_TIMEOUT) -> None:
    """Clear the way for our own qpwgraph: stop any instance AND remove the
    lock it leaves behind.

    This is issue #14, and the second half is the part that was missing.

    qpwgraph is single-instance via a Unix socket at
    `/tmp/qpwgraph:<user>@<host>`. That socket **survives SIGKILL** — the
    process dies, the socket file stays, and the next instance exits
    immediately and silently. No error, no crash, nothing in the journal, and an
    empty log file. The patchbay then never auto-connects, so Sushi runs with no
    audio in or out while looking completely healthy.

    Confirmed directly: SIGKILL a running qpwgraph, relaunch, and it dies. Delete
    the socket first and the identical command survives.

    The previous explanation — that the ~50% failure rate was just how often a
    qpwgraph happened to already be running — was incomplete, and its fix
    (kill, then wait for the process to disappear) could not work on its own:
    waiting for the *process* to go does nothing about the *socket* it left.
    It also explains what that theory could not: why retrying never helped, and
    why the failure rate tracked how the previous instance had exited — a clean
    quit removes the socket, a kill does not.
    """
    subprocess.run(["pkill", "-x", "qpwgraph"], check=False,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and qpwgraph_running():
        time.sleep(0.25)

    for socket_path in stale_qpwgraph_sockets(
        sorted(Path("/tmp").glob("qpwgraph:*")), qpwgraph_running()
    ):
        try:
            if socket_path.owner() == os.environ.get("USER", socket_path.owner()):
                socket_path.unlink()
        except (OSError, KeyError):
            # Someone else's socket, or already gone. Not ours to worry about;
            # a qpwgraph that then fails is reported by the caller anyway.
            pass


def start_qpwgraph(
    child: dict[str, Any],
    root: Path,
    env: dict[str, str],
    logs: Path,
    attempts: int = QPWGRAPH_ATTEMPTS,
    settle: float = QPWGRAPH_SETTLE,
) -> subprocess.Popen | None:
    """Start qpwgraph, and keep trying until it actually stays up.

    qpwgraph fails to start far more often than anything else here, and always
    the same way: it exits within a second or so with status 2, writing nothing
    at all — no error, no crash, an empty log. The patchbay then never
    auto-connects, so Sushi runs with no audio in or out while looking perfectly
    healthy. That is issue #14.

    Two causes are known and fixed at source: a lock socket left behind by an
    unclean exit (`stop_existing_qpwgraph`) and the desktop session manager
    (`env_unset` on the child). Neither explains all of it — with both fixed and
    no socket present, it still fails sometimes, and whether it does tracks
    nothing this code controls. It looks like a race inside qpwgraph or
    PipeWire, and it is not worth reverse-engineering someone else's GUI app to
    chase the rest.

    So: verify rather than assume, and retry rather than warn. Every attempt
    clears the socket first, because a failed attempt can leave one of its own.
    An honest bounded retry turns a silently broken patchbay into either a
    working one or a loud message, which is the outcome that actually matters.
    """
    child_env = dict(env)
    for key in child.get("env_unset", ()):
        child_env.pop(key, None)

    for attempt in range(attempts):
        stop_existing_qpwgraph()
        with open(logs / "qpwgraph.log", "wb") as sink:
            try:
                proc = subprocess.Popen(
                    child["command"], cwd=root, env=child_env,
                    stdin=subprocess.DEVNULL, stdout=sink, stderr=sink,
                )
            except OSError:
                return None
        time.sleep(settle)
        if proc.poll() is None:
            return proc
    return None


# --- up ---------------------------------------------------------------------
