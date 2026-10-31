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
from typing import Callable


# Shutdown escalation. SIGINT first because that is what lets Sushi unhook its
# JACK ports cleanly rather than leaving them held; the later signals exist for
# anything that ignores the polite one.
SHUTDOWN_STEPS: tuple[tuple[signal.Signals, float], ...] = (
    (signal.SIGINT, 3.0),
    (signal.SIGTERM, 3.0),
    (signal.SIGKILL, 2.0),
)

def pid_alive(pid: int) -> bool:
    """Whether a pid exists, without caring whether we may signal it.

    Ids of 1 or below are never a rig's process and are answered False without
    asking: `kill(0, 0)` addresses the caller's own group and succeeds, and the
    pids come from a state file anyone could have edited (see `pgid_alive`).
    """
    if pid <= 1:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Exists, owned by someone else. Still alive for our purposes.
        return True
    return True


def pgid_alive(pgid: int) -> bool:
    """Whether a process group has any member left.

    Ids of 1 or below are never a rig's group, and are answered False without
    asking. `killpg(0, 0)` signals the *caller's own* group and so always
    succeeds -- a state file holding 0 would read as a live rig and then `down`
    would signal the shell that ran it -- and a negative id is not a group at
    all but an OSError nobody expects here. Pgid 1 is init's.
    """
    if pgid <= 1:
        return False
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def stop_group(
    pgid: int,
    steps: tuple[tuple[signal.Signals, float], ...] = SHUTDOWN_STEPS,
    *,
    poll: Callable[[], object] | None = None,
    interval: float = 0.1,
) -> bool:
    """Signal a process group through `steps` until it is gone; whether it is.

    Each step sends its signal and then gives the group that long to disappear,
    so SIGINT really gets its chance to let Sushi release its JACK ports before
    anything harsher is sent. Stops at the first signal after which the group is
    gone. One implementation because there were three copies of this loop and
    they had drifted apart.

    `poll`, if given, is called on every tick before the group is checked. It is
    for a caller whose own child leads the group: a child that has exited is a
    zombie until waited for, and a zombie still counts as a group member, so
    without reaping it the whole escalation runs against a dead process. Pass
    `process.poll` or a `waitpid(..., WNOHANG)`.

    A group that vanishes between steps (ProcessLookupError) is gone, not an
    error. `PermissionError` propagates: whether a group that is not ours is a
    message (`down`) or something to shrug off (a failed-start cleanup) is the
    caller's decision. A group id that is not a real one, or is the caller's own
    group, is refused with ValueError rather than signalled; `killpg(0, ...)` is
    the shell that ran us.
    """
    if pgid <= 1 or pgid == os.getpgrp():
        raise ValueError(f"refusing to signal process group {pgid}")
    for sig, wait in steps:
        try:
            os.killpg(pgid, sig)
        except ProcessLookupError:
            return True
        deadline = time.monotonic() + wait
        while time.monotonic() < deadline:
            if poll is not None:
                poll()
            if not pgid_alive(pgid):
                return True
            time.sleep(interval)
        if poll is not None:
            poll()
        if not pgid_alive(pgid):
            return True
    return not pgid_alive(pgid)


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
