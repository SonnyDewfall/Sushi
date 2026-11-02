"""`live.close_controller`: an elkpy connection lets go of its file
descriptors when it is closed, not when the garbage collector next runs.

Found on the rig machine on 2026-11-02: the agent, connecting once a second
under the web UI's poll of /live, died with "OSError: [Errno 24] Too many open
files". elkpy 1.3.0's own close() stops its notification loop without closing
it. The test against real elkpy runs where elkpy is installed (the container
job installs the `live` extra; the fast job does not) and needs no Sushi: with
nothing listening, the controller still builds its loop and thread.

`close_controller` reads elkpy's private attributes, checked against 1.3.0
only. If a later elkpy renames them, the fake-object tests below still pass,
and the real-elkpy test is the one that says so.
"""

from __future__ import annotations

import asyncio
import contextlib
import gc
import io
import os
import socket
import threading

import pytest

from sushi_rig import live


def _open_fds() -> int:
    return len(os.listdir("/proc/self/fd"))


def _unused_address() -> str:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return f"127.0.0.1:{s.getsockname()[1]}"


def test_a_real_elkpy_connection_closed_here_leaves_no_descriptors_behind():
    pytest.importorskip("elkpy")
    address = _unused_address()
    cycles = 15
    gc.collect()
    gc.disable()  # what the agent sees between collections: nothing frees a cycle
    try:
        before = _open_fds()
        for _ in range(cycles):
            with contextlib.redirect_stdout(io.StringIO()):  # elkpy prints on connect
                controller = live.connect(address)
            live.close_controller(controller)
            del controller
        after = _open_fds()
    finally:
        gc.enable()
    # elkpy's own close() alone left about three per cycle here (45 after 15);
    # a few may stay with gRPC's own shared machinery, which does not grow.
    assert after - before <= 4, f"{after - before} descriptors left after {cycles} connections"


class _FakeNotifications:
    """The shape of elkpy 1.3.0's NotificationController in a synchronous
    context: a loop run forever in a thread of its own, with a task on it."""

    def __init__(self):
        self._async = False
        self._parent = None
        self.loop = asyncio.new_event_loop()
        self.tasks = []
        self.notification_thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.notification_thread.start()
        self.tasks.append(asyncio.run_coroutine_threadsafe(asyncio.sleep(3600), self.loop))

    def close(self):
        for task in self.tasks:
            task.cancel()
        if not self._async:
            self.loop.call_soon_threadsafe(self.loop.stop)


class _FakeSub:
    def __init__(self, parent):
        self._parent = parent
        self.stub = object()


class _FakeController:
    def __init__(self):
        self.notifications = _FakeNotifications()
        self.notifications._parent = self
        self.parameters = _FakeSub(self)
        self.closed = 0

    def close(self):
        self.closed += 1
        self.notifications.close()


def test_the_loop_is_stopped_and_closed_and_the_cycle_broken():
    controller = _FakeController()
    notifications = controller.notifications
    live.close_controller(controller)
    assert controller.closed == 1
    assert not notifications.notification_thread.is_alive()
    assert notifications.loop.is_closed()
    assert vars(controller.parameters) == {}, "the sub-controller no longer points back"
    notifications.close()  # elkpy's __del__ does this again: it must do nothing now


def test_a_thread_that_will_not_stop_is_left_with_its_loop(monkeypatch):
    controller = _FakeController()
    notifications = controller.notifications
    release = threading.Event()
    running = notifications.notification_thread
    stuck = threading.Thread(target=release.wait, daemon=True)
    stuck.start()
    notifications.notification_thread = stuck
    monkeypatch.setattr(live, "CLOSE_TIMEOUT", 0.05)
    try:
        live.close_controller(controller)
        assert not notifications.loop.is_closed(), "not closed from under a thread still in it"
    finally:
        release.set()
        loop = notifications.loop  # already asked to stop, by elkpy's close
        running.join(timeout=2)
        pending = [task for task in asyncio.all_tasks(loop) if not task.done()]
        for task in pending:
            task.cancel()
        loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        loop.close()


def test_a_controller_with_no_notification_thread_is_only_closed():
    class Plain:
        closed = False

        def close(self):
            self.closed = True

    plain = Plain()
    live.close_controller(plain)
    assert plain.closed
