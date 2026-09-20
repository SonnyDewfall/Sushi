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
