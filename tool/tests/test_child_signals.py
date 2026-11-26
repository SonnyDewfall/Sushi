"""Every child the rig starts has SIGINT and SIGTERM at their defaults, and a
stop does not wait out a signal that nothing left in the group will act on
(#236).

A process started with SIGINT ignored (an agent run from a script, a service
wrapper or a background job of a non-interactive shell, and pytest started
that way) passed the ignored SIGINT to every child: exec keeps an "ignored"
disposition. Each then sat through the SIGINT step of a stop (3 s of
`SHUTDOWN_STEPS`, 2.5 s of a verification's) and died of the next signal.
Here the test process itself ignores SIGINT and SIGTERM for the length of a
test, and each of the rig's ways of starting a process is asked what its
child got. Real processes, killed by pid at the end of each test.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time

import pytest

from sushi_rig import procs, qpwgraph, rig, supervisor, verify

SLEEP = ["sleep", "30"]  # not a Python, which would set its own SIGINT handler


@pytest.fixture
def stop_signals_ignored():
    """This process ignores SIGINT and SIGTERM, as an agent started from a
    script with them ignored does. Put back after the test."""
    before = {sig: signal.signal(sig, signal.SIG_IGN) for sig in procs.STOP_SIGNALS}
    try:
        yield
    finally:
        for sig, handler in before.items():
            signal.signal(sig, handler)


@pytest.fixture
def started():
    """Every Popen a test starts, killed and reaped by pid at its end."""
    made: list[subprocess.Popen] = []
    yield made
    for proc in made:
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=5)


def _ignored(pid: int) -> set[str]:
    return {sig.name for sig in procs.STOP_SIGNALS if procs.ignores(pid, sig)}


def test_the_stand_in_reproduces_the_inheritance(stop_signals_ignored, started):
    """What each spawn below undoes: a plain Popen passes both on."""
    started.append(subprocess.Popen(SLEEP))
    assert _ignored(started[-1].pid) == {"SIGINT", "SIGTERM"}


def test_the_forked_supervisors_children_start_with_the_defaults(stop_signals_ignored, started, tmp_path):
    for log in (None, tmp_path / "child.log"):
        started.append(rig.spawn_child(SLEEP, cwd=tmp_path, env=dict(os.environ), log=log))
        assert _ignored(started[-1].pid) == set()


def test_the_agents_children_and_its_holder_start_with_the_defaults(stop_signals_ignored, started, tmp_path):
    holder = supervisor._spawn(SLEEP, cwd=tmp_path, env=dict(os.environ), log=None, process_group=0)
    started.append(holder)
    started.append(supervisor._spawn(SLEEP, cwd=tmp_path, env=dict(os.environ), log=tmp_path / "c.log",
                                     process_group=holder.pid))
    assert os.getpgid(started[-1].pid) == holder.pid, "still put in the held group"
    assert [_ignored(proc.pid) for proc in started] == [set(), set()]


def test_qpwgraph_starts_with_the_defaults(stop_signals_ignored, started, tmp_path, monkeypatch):
    monkeypatch.setattr(qpwgraph, "stop_existing_qpwgraph", lambda: None)  # it would pkill qpwgraph
    proc = qpwgraph.start_qpwgraph({"command": SLEEP}, tmp_path, dict(os.environ), tmp_path, settle=0.0)
    assert proc is not None
    started.append(proc)
    assert _ignored(proc.pid) == set()


def test_a_verification_sushi_starts_with_the_defaults(stop_signals_ignored, tmp_path):
    """A stand-in Sushi that says what it was given and exits, which
    `capture_headless` reports with its output."""
    fake = tmp_path / "sushi"
    fake.write_text("#!/bin/sh\nexec grep SigIgn /proc/self/status\n")
    fake.chmod(0o755)
    with pytest.raises(verify.VerifyError, match="SigIgn") as said:
        verify.capture_headless(tmp_path / "c.json", str(fake), tmp_path)
    mask = int(str(said.value).split("SigIgn:")[1].split()[0], 16)
    assert not mask & (1 << (signal.SIGINT - 1)) and not mask & (1 << (signal.SIGTERM - 1)), hex(mask)


def test_a_spawn_that_cannot_exec_is_still_an_oserror(stop_signals_ignored, tmp_path):
    """`start_child` reads OSError as "could not be started"."""
    with pytest.raises(FileNotFoundError):
        rig.spawn_child(["/nonexistent/sushi"], cwd=tmp_path, env=dict(os.environ), log=None)


def test_the_forked_supervisor_itself_honours_sigint(stop_signals_ignored):
    """The group's leader, forked from an `up` that ignores SIGINT, dies of
    it as one started from a terminal does: KeyboardInterrupt, then
    `_fork_supervisor`'s `os._exit`. A real fork, running what
    `_supervise` runs after its `setsid`."""
    pid = os.fork()
    if pid == 0:  # pragma: no cover - the child
        try:
            rig._honour_stop_signals()
            os.kill(os.getpid(), signal.SIGSTOP)
            time.sleep(30)
        finally:
            os._exit(3)
    try:
        os.waitpid(pid, os.WUNTRACED)
        assert _ignored(pid) == set()
        os.kill(pid, signal.SIGINT)
        os.kill(pid, signal.SIGCONT)
        _, status = os.waitpid(pid, 0)
        assert os.waitstatus_to_exitcode(status) == 3, "KeyboardInterrupt, then its finally"
    finally:
        try:
            os.kill(pid, signal.SIGKILL)
            os.waitpid(pid, 0)
        except (ProcessLookupError, ChildProcessError):
            pass


@pytest.mark.parametrize("handler", [signal.SIG_DFL, lambda *_: None], ids=["default", "handled"])
def test_where_nothing_is_ignored_nothing_runs_between_fork_and_exec(handler, monkeypatch, tmp_path):
    """The running agent handles SIGINT and SIGTERM (aiohttp's `run_app`), and
    a fork from its threads is meant to run no Python: its spawns pass no
    `preexec_fn`, and an exec resets what is handled anyway."""
    before = {sig: signal.signal(sig, handler) for sig in procs.STOP_SIGNALS}
    try:
        assert procs.stop_signals_preexec() is None
        seen = []
        monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: seen.append(k.get("preexec_fn")))
        supervisor._spawn(SLEEP, cwd=tmp_path, env={}, log=None, process_group=0)
        rig.spawn_child(SLEEP, cwd=tmp_path, env={}, log=None)
        assert seen == [None, None]
    finally:
        for sig, old in before.items():
            signal.signal(sig, old)
