"""Processes, process groups and port readiness.

The rig is a set of external processes, and this is everything needed to ask
whether one is alive, whether a port is answering, and how to stop a whole group
without resorting to pattern-matching a command line.

That last point is the reason this module exists. Every lifecycle failure the
project has had came from identifying processes by *pattern* rather than by
*identity*: `killall sushi` never matched the AppImage's re-exec'd `sushi.bin`,
and `pkill -f "sushi-rig listen"` matched the command line of the shell invoking
it. Signalling a recorded process group is immune to both.
"""

from __future__ import annotations

import os
import signal
import socket
import time


# Shutdown escalation. SIGINT first because that is what lets Sushi unhook its
# JACK ports cleanly rather than leaving them held; the later signals exist for
# anything that ignores the polite one.
SHUTDOWN_STEPS: tuple[tuple[signal.Signals, float], ...] = (
    (signal.SIGINT, 3.0),
    (signal.SIGTERM, 3.0),
    (signal.SIGKILL, 2.0),
)

# How long to wait for a killed qpwgraph to actually exit before starting ours.
def pid_alive(pid: int) -> bool:
    """Whether a pid exists, without caring whether we may signal it."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Exists, owned by someone else. Still alive for our purposes.
        return True
    return True


def pgid_alive(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def port_is_open(host: str, port: int, timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def wait_for_grpc(
    host: str,
    port: int,
    timeout: float,
    still_starting: Callable[[], bool] | None = None,
) -> bool:
    """Block until Sushi's gRPC accepts a connection, or give up.

    The panel is generated from the *live* rig, so asking for one before Sushi
    is listening fails in a way that looks like a tool bug rather than a timing
    problem.

    `still_starting` reports whether *our* Sushi is alive. Without it this waits
    for a port rather than for a process, and a port is not proof of anything:
    caught in testing, where a Sushi left over from an earlier start already
    held 51051, so the check passed instantly and the rig was declared up while
    its own Sushi had already exited. It also turns "the config is broken" from
    a full timeout into an immediate, accurate failure.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if still_starting is not None and not still_starting():
            return False
        if port_is_open(host, port):
            return True
        time.sleep(0.25)
    return False
