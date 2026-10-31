"""Process and port probes, against real processes and real sockets.

These are effects, so they are checked against the thing itself where a test
can make one cheaply: a child that has really exited, a group that really
existed, a socket that is really bound. `PermissionError` cannot be had
honestly (the tests may run as root), so that one branch is a stand-in.

Not covered here, because it needs a rig: that `SHUTDOWN_STEPS` empties a real
Sushi/Electron tree. That is verified by running `sushi-rig down` by hand.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time

import pytest

from sushi_rig import procs


def _exited_child(**kwargs) -> int:
    """The pid of a process that has run and been reaped, so it does not exist."""
    child = subprocess.Popen([sys.executable, "-c", "pass"], **kwargs)
    child.wait()
    return child.pid


def test_pid_alive_is_true_for_ourselves_and_false_for_an_exited_process():
    assert procs.pid_alive(os.getpid())
    assert not procs.pid_alive(_exited_child())


def test_pid_alive_counts_a_process_we_may_not_signal_as_alive(monkeypatch):
    """Owned by someone else is still *there*, which is the question."""
    def refuse(pid, sig):
        raise PermissionError

    monkeypatch.setattr(os, "kill", refuse)
    assert procs.pid_alive(1234)


def test_pgid_alive_is_true_for_our_group_and_false_for_one_that_is_gone():
    assert procs.pgid_alive(os.getpgrp())
    assert not procs.pgid_alive(_exited_child(start_new_session=True))


def test_pgid_alive_is_false_for_ids_that_are_not_a_rigs_group(monkeypatch):
    """`killpg(0, 0)` succeeds -- it is *our own* group -- so a state file
    holding 0 read as a live rig and `down` then signalled its own shell. -5
    raised OSError instead of answering. 1 is init's. None of them may even be
    asked about."""
    monkeypatch.setattr(os, "killpg", lambda *a: pytest.fail("probed a nonsense id"))
    for pgid in (0, 1, -1, -5):
        assert procs.pgid_alive(pgid) is False


def test_pgid_alive_counts_a_group_we_may_not_signal_as_alive(monkeypatch):
    def refuse(pgid, sig):
        raise PermissionError

    monkeypatch.setattr(os, "killpg", refuse)
    assert procs.pgid_alive(4321)


@pytest.fixture
def listener():
    """A socket really listening on a free port; closed when the test is done."""
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen()
    yield sock
    sock.close()


def test_port_is_open_sees_a_real_listener_and_then_not(listener):
    port = listener.getsockname()[1]
    assert procs.port_is_open("127.0.0.1", port)
    listener.close()
    assert not procs.port_is_open("127.0.0.1", port)


def test_wait_for_grpc_returns_as_soon_as_the_port_answers(listener):
    started = time.monotonic()
    assert procs.wait_for_grpc("127.0.0.1", listener.getsockname()[1], timeout=5.0)
    assert time.monotonic() - started < 2.0


def test_wait_for_grpc_gives_up_on_a_dead_start_even_though_the_port_answers(listener):
    """The reason `still_starting` exists: a leftover Sushi already held the
    port, so a port-only check passed at once while the rig's own Sushi had
    exited. Here the port is genuinely open and the answer must still be no,
    straight away, rather than after the timeout."""
    started = time.monotonic()
    assert not procs.wait_for_grpc(
        "127.0.0.1", listener.getsockname()[1], timeout=5.0,
        still_starting=lambda: False,
    )
    assert time.monotonic() - started < 2.0


def test_wait_for_grpc_times_out_when_nothing_ever_listens():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()  # a port that was free a moment ago, and now has no listener
    assert not procs.wait_for_grpc("127.0.0.1", port, timeout=0.4)


def test_wait_for_grpc_keeps_waiting_while_our_process_is_alive_and_the_port_is_not_up(listener):
    """still_starting True is not an answer: only the port can say yes."""
    port = listener.getsockname()[1]
    listener.close()
    assert not procs.wait_for_grpc(
        "127.0.0.1", port, timeout=0.4, still_starting=lambda: True,
    )
