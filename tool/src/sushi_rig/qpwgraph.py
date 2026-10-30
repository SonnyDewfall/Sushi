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
from typing import Any


# How long to wait for a killed qpwgraph to actually exit before starting ours.
QPWGRAPH_EXIT_TIMEOUT = 5.0
# How many times to try starting qpwgraph, and how long to watch each attempt
# before believing it. See `start_qpwgraph`.
# How many times to try starting qpwgraph, and how long to watch each attempt
# before believing it. See `start_qpwgraph`.
QPWGRAPH_ATTEMPTS = 3
QPWGRAPH_SETTLE = 1.5

# How long to let qpwgraph finish wiring a node before calling it unwired.
PATCHBAY_SETTLE = 3.0

# A bound on each `pw-link` call, so a hung one cannot hold up startup.
PATCHBAY_TIMEOUT = 2.0


def ports_without_sources(listing: str, node: str) -> list[str]:
    """Input ports of `node` that nothing is connected to, from `pw-link -l -i`.

    The listing names a port on its own line and then indents one `|<-` line
    per source, so a port with no indented lines under it is a port with no
    input. Pure, so the parse is pinned by tests while the shelling out is
    verified by running it (quality principle 1).
    """
    prefix = f"{node}:"
    unconnected: list[str] = []
    port: str | None = None
    sources = 0
    for line in listing.splitlines():
        if not line.strip():
            continue
        if line[0].isspace():
            if port is not None and line.strip().startswith("|<-"):
                sources += 1
            continue
        if port is not None and sources == 0:
            unconnected.append(port)
        port = line.strip() if line.strip().startswith(prefix) else None
        sources = 0
    if port is not None and sources == 0:
        unconnected.append(port)
    return unconnected


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


def unconnected_inputs(
    node: str, settle: float = PATCHBAY_SETTLE
) -> list[str] | None:
    """`node`'s input ports that the patchbay has not wired, once it has had a
    chance to — or `None` if `node` never appeared in the listing at all.

    qpwgraph applies its patch as nodes appear, so asking too early reports a
    port that is about to be connected. The wait is bounded and gives up rather
    than blocking startup.

    Lets `OSError` and `TimeoutExpired` out rather than reporting "nothing
    unwired", so the caller can tell a wired rig from a question it failed to
    ask. The caller is the one that decides such a failure is not fatal.
    """
    deadline = time.monotonic() + settle
    while True:
        result = subprocess.run(
            ["pw-link", "-l", "-i"],
            capture_output=True,
            text=True,
            check=False,
            timeout=PATCHBAY_TIMEOUT,
        )
        present = node_ports(result.stdout, node)
        unwired = ports_without_sources(result.stdout, node)
        if present and not unwired:
            return []
        if time.monotonic() >= deadline:
            return unwired if present else None
        time.sleep(0.25)


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
