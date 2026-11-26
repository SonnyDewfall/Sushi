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
from pathlib import Path
from typing import Callable


# Shutdown escalation. SIGINT first because that is what lets Sushi unhook its
# JACK ports cleanly rather than leaving them held; the later signals exist for
# anything that ignores the polite one.
SHUTDOWN_STEPS: tuple[tuple[signal.Signals, float], ...] = (
    (signal.SIGINT, 3.0),
    (signal.SIGTERM, 3.0),
    (signal.SIGKILL, 2.0),
)

# The signals a stop sends that a process can ignore. SIGKILL cannot be.
STOP_SIGNALS: tuple[signal.Signals, ...] = (signal.SIGINT, signal.SIGTERM)


def default_stop_signals() -> None:
    """Put SIGINT and SIGTERM back to their defaults, in a child between fork
    and exec: the `preexec_fn` `stop_signals_preexec` gives (#236)."""
    for sig in STOP_SIGNALS:
        signal.signal(sig, signal.SIG_DFL)


def stop_signals_preexec() -> Callable[[], None] | None:
    """The `preexec_fn` for a child the rig starts and stops: one that puts
    SIGINT and SIGTERM back to their defaults if this process ignores
    either, else None (#236).

    "Ignored" survives exec, and a process started with SIGINT ignored -- an
    `up` or `verify` run from a script or as a background job of a
    non-interactive shell, or pytest started that way -- passed it to every
    child. Each then sat through the SIGINT step of a stop (3 s of
    `SHUTDOWN_STEPS`, 2.5 s of a verification's) and died of the next signal,
    its clean exit on SIGINT never run. A handled signal is reset to its
    default by exec, so only "ignored" needs this; Popen's `restore_signals`
    restores SIGPIPE, SIGXFZ and SIGXFSZ, not these.

    None wherever neither is ignored, so nothing runs between fork and exec
    there: the running agent is such a place, since aiohttp handles both
    (`run_app`'s `handle_signals`), and a fork from its threads is meant to
    run no Python (docs/reference/agent.md, "How the group is held"). Where
    one is ignored, the function calls `signal.signal` and nothing else.
    `signal.getsignal` reads the disposition from any thread.
    """
    if any(signal.getsignal(sig) == signal.SIG_IGN for sig in STOP_SIGNALS):
        return default_stop_signals
    return None


def ignores(pid: int, sig: signal.Signals) -> bool | None:
    """Whether a process ignores `sig` (/proc/<pid>/status `SigIgn`); None
    for one that is a zombie, gone or unreadable, which ignores nothing."""
    try:
        with open(f"/proc/{pid}/status") as status:
            fields = dict(line.split(":", 1) for line in status if ":" in line)
        if fields.get("State", "").strip().startswith("Z"):
            return None
        return bool(int(fields["SigIgn"].strip(), 16) & (1 << (int(sig) - 1)))
    except (OSError, KeyError, ValueError):
        return None


def group_members(pgid: int) -> list[int]:
    """Every process in group `pgid`, from /proc: what is left when a group
    would not stop, said by pid. Empty where /proc cannot be read."""
    members = []
    try:
        entries = os.listdir("/proc")
    except OSError:
        return []
    for entry in entries:
        if not entry.isdigit():
            continue
        try:
            with open(f"/proc/{entry}/stat") as stat:
                # "pid (comm) state ppid pgrp ...": comm may hold spaces and
                # parentheses, so split after the last ')'.
                fields = stat.read().rsplit(")", 1)[1].split()
        except (OSError, IndexError):
            continue
        if len(fields) > 2 and fields[2] == str(pgid):
            members.append(int(entry))
    return sorted(members)


def group_ignores(pgid: int, sig: signal.Signals) -> bool:
    """Whether every member of the group that could act on a signal ignores
    `sig`, and there is one: then waiting after sending it is waiting for
    nothing. Zombies are left out (they go when reaped, whatever is sent),
    and a group of zombies alone is not said to ignore anything."""
    seen = [ignores(pid, sig) for pid in group_members(pgid)]
    live = [said for said in seen if said is not None]
    return bool(live) and all(live)

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

    A step whose signal every member left ignores (`group_ignores`, read
    from /proc on each tick) is not waited out: the next is sent at once. A
    child the rig starts has SIGINT at its default (`default_stop_signals`),
    so this is for a process that ignores it itself, or one started before
    that (#236).

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
            if sig in STOP_SIGNALS and group_ignores(pgid, sig):
                break  # nothing left in it will act on this one (#236)
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


def threads_of(pid: int) -> tuple[tuple[str, int, int], ...]:
    """One process's threads as `(name, policy, rt_priority)`: fields 2, 41
    and 40 of each /proc/<pid>/task/<tid>/stat. Empty when there are none to
    read (the process has gone)."""
    threads = []
    for stat in sorted(Path(f"/proc/{pid}/task").glob("*/stat"), key=lambda p: (len(p.parent.name), p.parent.name)):
        try:
            text = stat.read_text()
        except OSError:
            continue
        name = text[text.find("(") + 1:text.rfind(")")]
        fields = text[text.rfind(")") + 2:].split()
        # After the name, field 3 is first: policy is field 41, rt_priority 40.
        if len(fields) > 38:
            threads.append((name, int(fields[38]), int(fields[37])))
    return tuple(threads)


def stop_recorded(pid: int, started: int | None, comm: str,
                  steps: tuple[tuple[signal.Signals, float], ...] = SHUTDOWN_STEPS) -> list[int]:
    """Stop a process this one did not start, named by a record: pid `pid`,
    started at `started` (clock ticks since boot, `/proc/<pid>/stat` field
    22), whose name (`comm`) is `comm`. `[pid]` if it is still there
    afterwards, else `[]`; `ValueError` when the process at `pid` is not that
    one, and then nothing is signalled.

    A pidfd is opened first and every signal goes through it: once it is
    open the pid cannot come to name another process, so the identity read
    after it holds for the signals that follow (review of PR #200: by pid
    alone, a pid reused between the check and the kill would be another
    process's death)."""
    import select

    if pid <= 1:
        raise ValueError(f"refusing to signal pid {pid}")
    try:
        fd = os.pidfd_open(pid)
    except ProcessLookupError:
        return []
    try:
        try:
            text = Path(f"/proc/{pid}/stat").read_text()
            name = text[text.find("(") + 1:text.rfind(")")]
            fields = text[text.rfind(")") + 2:].split()
            state, now_started = fields[0], int(fields[19])
        except (OSError, IndexError, ValueError):
            return []
        if state == "Z":
            return []
        if now_started != started or name != comm:
            raise ValueError(f"pid {pid} is {name!r}, started at {now_started}, not the {comm!r} started at "
                             f"{started} that was recorded: not signalled")
        poller = select.poll()
        poller.register(fd, select.POLLIN)
        for sig, wait in steps:
            try:
                signal.pidfd_send_signal(fd, sig)
            except ProcessLookupError:
                return []
            if sig in STOP_SIGNALS and ignores(pid, sig):
                wait = 0  # it ignores this one: on to the next (#236)
            if poller.poll(int(wait * 1000)):
                return []  # readable: it has exited
        return [pid]
    finally:
        os.close(fd)
