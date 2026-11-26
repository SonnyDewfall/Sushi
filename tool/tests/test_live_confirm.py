"""A command is confirmed by Sushi's notice or by reading the change back,
and a notice that comes before its command is registered is not lost (#241).

elkpy 1.3.0 loses a command's confirmation two ways (live.py, "A command's
confirmation"): the notice comes before the caller's thread has registered the
command's response, and elkpy drops it; or it comes before the notification
thread's subscription is up, and Sushi never sends it. The second was seen
against Sushi 1.3.0 (10 first commands in 150 on a fresh connection, the probe
in tool/spikes/live_remove_track/); the first is read from elkpy's source.

Two kinds of stand-in. The first half needs nothing installed: a thread that
does what elkpy's `NotificationController.update_command_responses` does with
a notice (its matching, copied from 1.3.0), and stand-in controllers whose
responses never set. The second half runs the real elkpy against a gRPC
server in this process that plays Sushi, and makes each loss happen on
purpose: it says a command was done before answering it, or opens its notice
stream late. That half needs elkpy (the container job installs it, the fast
job does not), as `test_live_close.py`'s real-elkpy test does. Neither is a
real Sushi.
"""

from __future__ import annotations

import contextlib
import io
import queue
import re
import threading
import time
from concurrent import futures
from pathlib import Path
from types import SimpleNamespace

import pytest

from sushi_rig import live

SUCCESS, ERROR = 1, 3  # CommandResponseStatus


# --------------------------------------------------------------------------- #
# The notice that comes first
# --------------------------------------------------------------------------- #

class _Response:
    """elkpy's SushiCommandResponse as `_wait` and elkpy's thread use it."""

    def __init__(self):
        self.error = False
        self._set = threading.Event()

    def set(self):
        self._set.set()

    def is_set(self):
        return self._set.is_set()


def _notice(responses, request_id: int, status: int) -> None:
    """elkpy 1.3.0's `update_command_responses`, for one notice."""
    if ev := responses.get(request_id):
        if status == SUCCESS:
            ev.set()
        elif status == ERROR:
            ev.error = True
            ev.set()
        responses.pop(request_id)


def test_elkpys_own_dict_drops_a_notice_that_comes_before_its_command():
    """What the stand-in reproduces: the loss, with what elkpy uses."""
    responses: dict = {}
    _notice(responses, 5, SUCCESS)
    response = _Response()
    responses[5] = response  # validate_command_response, after the RPC returned
    assert not response.is_set(), "elkpy's own dict loses it"


@pytest.mark.parametrize("status, error", [(SUCCESS, False), (ERROR, True)])
def test_a_notice_that_comes_first_confirms_its_command_when_it_is_registered(status, error):
    responses = live.Confirmations()
    _notice(responses, 5, status)
    response = _Response()
    responses[5] = response
    assert response.is_set() and response.error is error
    assert 5 not in responses, "answered, so nothing is left waiting"


def test_a_notice_in_the_usual_order_is_matched_as_elkpy_matches_it():
    responses = live.Confirmations()
    response = _Response()
    responses[5] = response
    assert responses.get(5) is response
    _notice(responses, 5, SUCCESS)
    assert response.is_set() and 5 not in responses


@pytest.mark.parametrize("status, error", [(SUCCESS, False), (ERROR, True)])
def test_a_notice_the_thread_holds_while_its_command_is_registered_still_arrives(status, error):
    """The thread has `get` the id (nothing registered yet), the command is
    registered, then the thread sets what it got."""
    responses = live.Confirmations()
    early = responses.get(5)
    response = _Response()
    responses[5] = response
    assert not response.is_set()
    if status == ERROR:
        early.error = True
    early.set()
    responses.pop(5)
    assert response.is_set() and response.error is error


def test_notices_for_other_clients_commands_are_kept_only_so_far():
    """Every subscriber hears every client's notices."""
    responses = live.Confirmations()
    for request_id in range(live.EARLY_NOTICES_KEPT * 3):
        _notice(responses, request_id, SUCCESS)
    assert len(responses._early) == live.EARLY_NOTICES_KEPT
    newest = live.EARLY_NOTICES_KEPT * 3 - 1
    response = _Response()
    responses[newest] = response
    assert response.is_set(), "the newest are the ones kept"


def test_a_notice_and_its_registration_racing_on_two_threads_always_meet():
    responses = live.Confirmations()
    rounds, lost = 2000, []
    for request_id in range(rounds):
        response = _Response()
        go = threading.Barrier(2)

        def notify(rid=request_id):
            go.wait()
            _notice(responses, rid, SUCCESS)

        thread = threading.Thread(target=notify)
        thread.start()
        go.wait()
        responses[request_id] = response
        thread.join()
        if not response.is_set():
            lost.append(request_id)
    assert lost == []


# --------------------------------------------------------------------------- #
# The notice that never comes: the change read back
# --------------------------------------------------------------------------- #

class _Never:
    """A response whose notice never comes."""
    error = False

    def is_set(self):
        return False


def test_a_change_sushi_holds_is_confirmed_by_reading_it_back_without_waiting_out_the_timeout():
    looks = []
    started = time.monotonic()
    live._wait(_Never(), timeout=5, interval=0.005, strict=True,
               applied=lambda: (looks.append(1), len(looks) >= 2)[1])
    took = time.monotonic() - started
    assert len(looks) == 2
    assert took < live.READBACK_AFTER + 2 * live.READBACK_EVERY + 0.5, took


def test_a_change_sushi_does_not_hold_by_the_deadline_is_not_applied_not_done():
    with pytest.raises(live.LiveError, match="shows it was not applied"):
        live._wait(_Never(), timeout=0.1, interval=0.005, strict=True, applied=lambda: False)


@pytest.mark.parametrize("applied", [lambda: None, lambda: 1 / 0])
def test_a_reading_that_cannot_tell_leaves_it_may_or_may_not(applied):
    with pytest.raises(live.LiveError, match="may or may not"):
        live._wait(_Never(), timeout=0.1, interval=0.005, strict=True, applied=applied)


def test_a_notice_that_says_it_failed_is_a_failure_whatever_a_reading_says():
    failed = SimpleNamespace(is_set=lambda: True, error=True)
    with pytest.raises(live.LiveError, match="reported an error"):
        live._wait(failed, strict=True, applied=lambda: True)


def test_a_notice_that_comes_is_not_read_back():
    done = SimpleNamespace(is_set=lambda: True, error=False)
    live._wait(done, strict=True, applied=lambda: pytest.fail("read back a confirmed command"))


class _Rig:
    """A stand-in elkpy controller whose commands are carried out and never
    confirmed (`apply`), or neither, and that reads back what it holds."""

    def __init__(self, apply: bool = True):
        self.apply = apply
        self.audio_graph = self.parameters = self.midi_controller = self
        self.bypassed = {7: False}
        self.values = {(7, 3): 0.25}
        self.chains = {1: [2, 3, 7], 9: [11]}
        self.names = {2: "comp", 3: "drive", 7: "reverb", 11: "delay", 1: "main", 9: "aux"}
        self.cc: list = []
        self.closed = False

    def _do(self, change):
        if self.apply:
            change()
        return _Never()

    # reads
    def get_processor_id(self, name):
        return {v: k for k, v in self.names.items()}[name]

    def get_parameter_id(self, pid, name):
        return 3

    def get_parameter_value(self, pid, param):
        return self.values[(pid, param)]

    def get_processor_bypass_state(self, pid):
        return self.bypassed[pid]

    def get_all_tracks(self):
        return [SimpleNamespace(id=t, name=self.names[t]) for t in self.chains]

    def get_track_processors(self, tid):
        return [SimpleNamespace(id=p, name=self.names[p]) for p in self.chains[tid]]

    def get_cc_input_connections_for_processor(self, pid):
        return [c for c in self.cc if c.processor_id == pid]

    # commands
    def set_parameter_value(self, pid, param, value):
        return self._do(lambda: self.values.__setitem__((pid, param), value))

    def set_processor_bypass_state(self, pid, state):
        return self._do(lambda: self.bypassed.__setitem__(pid, state))

    def move_processor_on_track(self, pid, src, dst, before, back):
        def move():
            chain = [p for p in self.chains[src] if p != pid]
            chain.insert(len(chain) if back else chain.index(before), pid)
            self.chains[src] = chain
        return self._do(move)

    def create_processor_on_track(self, name, uid, path, processor_type, track_id, before_processor, add_to_back):
        def create():
            self.names[40] = name
            self.chains[track_id].append(40)
        return self._do(create)

    def delete_processor_from_track(self, pid, tid):
        return self._do(lambda: self.chains[tid].remove(pid))

    def delete_track(self, tid):
        return self._do(lambda: self.chains.pop(tid))

    def connect_cc_to_parameter(self, pid, param, channel, port, cc, lo, hi, relative):
        return self._do(lambda: self.cc.append(SimpleNamespace(
            processor_id=pid, parameter_id=param, channel=channel, port=port, cc_number=cc,
            min_range=lo, max_range=hi, relative_mode=relative)))

    def disconnect_cc(self, pid, param, channel, port, cc, lo, hi, relative):
        return self._do(lambda: self.cc.clear())

    def close(self):
        self.closed = True


_MAPPING = {"plugin_name": "reverb", "parameter_name": "Mix", "channel": 0, "port": 0,
            "cc_number": 11, "min_range": 0.0, "max_range": 1.0, "mode": "absolute"}


def _commands(rig):
    def move(c):
        planned = live.plan_live_move("drive", -1, controller=c)
        return live.send_live_move(planned, controller=c)

    def disconnect(c):
        rig.cc.append(SimpleNamespace(processor_id=7, parameter_id=3, channel=1, port=0, cc_number=11,
                                      min_range=0.0, max_range=1.0, relative_mode=False))
        return live.disconnect_live_cc(_MAPPING, controller=c)

    return {
        "parameter": lambda c: live.set_live_parameter("reverb", "Mix", 0.75, controller=c),
        "bypass": lambda c: live.set_live_bypass("reverb", True, controller=c),
        "move": move,
        "add": lambda c: live.add_live_processor("chorus", "urn:x", 1, 0, True, controller=c),
        "copy": lambda c: live.add_live_copy("chorus_copy", 1, 0, True, controller=c),
        "remove": lambda c: live.remove_live_processor(3, 1, controller=c),
        "remove by name": lambda c: live.remove_live_processor_named("drive", 1, controller=c),
        "remove track": lambda c: live.remove_live_track("aux", controller=c),
        "cc on": lambda c: live.connect_live_cc(_MAPPING, controller=c),
        "cc off": disconnect,
    }


@pytest.mark.parametrize("which", list(_commands(_Rig())))
def test_every_strict_command_applied_but_never_confirmed_is_done(which, monkeypatch):
    monkeypatch.setattr(live, "CONFIRM_TIMEOUT", 3.0)
    rig = _Rig(apply=True)
    started = time.monotonic()
    _commands(rig)[which](rig)
    took = time.monotonic() - started
    assert took < 2.0, f"read back, not waited out: {took:.2f}s"


@pytest.mark.parametrize("which", list(_commands(_Rig())))
def test_every_strict_command_neither_applied_nor_confirmed_is_not_done(which, monkeypatch):
    monkeypatch.setattr(live, "CONFIRM_TIMEOUT", 0.1)
    rig = _Rig(apply=False)
    with pytest.raises(live.LiveError, match="did not confirm"):
        _commands(rig)[which](rig)


def test_a_parameter_held_next_to_the_value_asked_cannot_be_called_not_applied(monkeypatch):
    """A stepped parameter may be held at its step: not "not applied"."""
    monkeypatch.setattr(live, "CONFIRM_TIMEOUT", 0.3)
    rig = _Rig(apply=False)
    rig.values[(7, 3)] = 0.5
    with pytest.raises(live.LiveError, match="may or may not"):
        live.set_live_parameter("reverb", "Mix", 0.45, controller=rig)


def test_a_move_reads_back_as_moved_only_where_it_was_sent():
    rig = _Rig()
    assert live._moved(rig, 3, 1, 2, False) is False, "drive is after comp, not before it"
    rig.chains[1] = [3, 2, 7]
    assert live._moved(rig, 3, 1, 2, False) is True
    assert live._moved(rig, 7, 1, 0, True) is True
    assert live._moved(rig, 2, 1, 0, True) is False
    assert live._moved(rig, 99, 1, 0, True) is False


# --------------------------------------------------------------------------- #
# The real elkpy against a gRPC server that plays Sushi
# --------------------------------------------------------------------------- #

class _FakeSushi:
    """The calls a bypass needs, Sushi's way: a command answered
    ASYNC_RESPONSE with an id, its notice on the async command stream.

    `notice_first` sends the notice and lets it land before answering the
    command (loss 1); `subscribe_after` holds each notice stream that long
    before it is subscribed, and a notice with no subscriber is gone, as
    Sushi's are (loss 2); `apply` False is a command neither carried out nor
    confirmed."""

    def __init__(self, proto, rpc, *, notice_first=False, subscribe_after=0.0, apply=True):
        import grpc

        self.proto, self.notice_first, self.subscribe_after, self.apply = proto, notice_first, subscribe_after, apply
        self.bypassed = False
        self.subscribers: list[queue.Queue] = []
        self.subscriptions = 0  # made, ever: a closed connection's may not have gone yet
        self.delivered = threading.Condition()
        self.sent: set[int] = set()  # the notices some stream has handed on
        self.next_id = 100
        self.lock = threading.Lock()
        version = re.search(r'\[default\s*=\s*"([^"]+)"\]', (_elkpy_rpc() / "api_version.proto").read_text())
        self.version = version.group(1)
        self.server = grpc.server(futures.ThreadPoolExecutor(max_workers=8))
        fake = self

        class System(rpc.SystemControllerServicer):
            def GetSushiApiVersion(self, request, context):
                return proto.GenericStringValue(value=fake.version)

        class Graph(rpc.AudioGraphControllerServicer):
            def GetProcessorId(self, request, context):
                return proto.ProcessorIdentifierResponse(status=fake._status(SUCCESS), id=7)

            def GetProcessorBypassState(self, request, context):
                return proto.BoolResponse(status=fake._status(SUCCESS), value=fake.bypassed)

            def SetProcessorBypassState(self, request, context):
                return fake._command(lambda: setattr(fake, "bypassed", request.value))

        class Notices(rpc.NotificationControllerServicer):
            def SubscribeToAsyncCommandUpdates(self, request, context):
                time.sleep(fake.subscribe_after)
                mine: queue.Queue = queue.Queue()
                with fake.lock:
                    fake.subscribers.append(mine)
                    fake.subscriptions += 1
                try:
                    while context.is_active():
                        try:
                            notice = mine.get(timeout=0.02)
                        except queue.Empty:
                            continue
                        yield notice
                        with fake.delivered:
                            fake.sent.add(notice.request_id)
                            fake.delivered.notify_all()
                finally:
                    with fake.lock:
                        fake.subscribers.remove(mine)

        rpc.add_SystemControllerServicer_to_server(System(), self.server)
        rpc.add_AudioGraphControllerServicer_to_server(Graph(), self.server)
        rpc.add_NotificationControllerServicer_to_server(Notices(), self.server)
        self.address = f"127.0.0.1:{self.server.add_insecure_port('127.0.0.1:0')}"
        self.server.start()

    def _status(self, status):
        return self.proto.CommandStatus(status=status)

    def subscribed(self) -> int:
        with self.lock:
            return self.subscriptions

    def _notify(self, request_id):
        notice = self.proto.AsyncCommandResponse(status=self._status(SUCCESS), request_id=request_id)
        with self.lock:
            listeners = list(self.subscribers)
        for listener in listeners:
            listener.put(notice)

    def _command(self, change):
        with self.lock:
            request_id, self.next_id = self.next_id, self.next_id + 1
        if not self.apply:
            return self.proto.CommandResponse(status=self._status(2), id=request_id)
        change()
        if self.notice_first:
            with self.delivered:
                self._notify(request_id)
                self.delivered.wait_for(lambda: request_id in self.sent, timeout=2)
            time.sleep(0.1)  # elkpy's thread has it before the caller reads the answer
        else:
            threading.Timer(0.05, self._notify, (request_id,)).start()
        return self.proto.CommandResponse(status=self._status(2), id=request_id)  # ASYNC_RESPONSE

    def stop(self):
        self.server.stop(0).wait(5)


def _elkpy_rpc() -> Path:
    from elkpy import sushicontroller

    return Path(sushicontroller.__file__).parent / "rpc"


@pytest.fixture
def sushi():
    pytest.importorskip("elkpy")
    from elkpy import grpc_gen

    with contextlib.redirect_stdout(io.StringIO()):
        proto, rpc = grpc_gen.modules_from_proto(str(_elkpy_rpc() / "sushi_rpc.proto"))
    made = []

    def make(**how):
        fake = _FakeSushi(proto, rpc, **how)
        made.append(fake)
        return fake

    yield make
    for fake in made:
        fake.stop()


@contextlib.contextmanager
def _connected(fake, *, subscribed=True):
    before = fake.subscribed()
    with contextlib.redirect_stdout(io.StringIO()):  # elkpy prints on connect
        controller = live.connect(fake.address)
    try:
        if subscribed:  # the stream is up: what a long-lived connection has
            deadline = time.monotonic() + 5
            while fake.subscribed() == before:
                assert time.monotonic() < deadline, "elkpy never subscribed"
                time.sleep(0.01)
        yield controller
    finally:
        live.close_controller(controller)


def _settles(response, seconds=1.0) -> bool:
    deadline = time.monotonic() + seconds
    while not response.is_set() and time.monotonic() < deadline:
        time.sleep(0.01)
    return response.is_set()


def test_real_elkpy_confirms_a_command_as_usual(sushi):
    fake = sushi()
    with _connected(fake) as c:
        started = time.monotonic()
        live.set_live_bypass("reverb", True, controller=c)
        assert time.monotonic() - started < live.READBACK_AFTER, "by its notice, not a reading"
    assert fake.bypassed is True


def test_real_elkpy_loses_a_notice_that_comes_before_its_command_unless_it_is_kept(sushi):
    fake = sushi(notice_first=True)
    with _connected(fake) as c:
        c.command_responses = {}  # elkpy's own
        lost = c.audio_graph.set_processor_bypass_state(7, True)
        assert not _settles(lost, 0.5), "the stand-in reproduces the loss"
    fake.bypassed = False
    with _connected(fake) as c:
        assert isinstance(c.command_responses, live.Confirmations)
        kept = c.audio_graph.set_processor_bypass_state(7, True)
        assert kept.is_set(), "confirmed by the notice that came first"
        assert not kept.error


def test_real_elkpy_loses_a_first_notice_sent_before_it_subscribed(sushi):
    fake = sushi(subscribe_after=0.4)
    with _connected(fake, subscribed=False) as c:
        assert fake.subscribed() == 0
        response = c.audio_graph.set_processor_bypass_state(7, True)
        assert not _settles(response, 0.8), "the stand-in reproduces the loss"
        assert fake.subscribed() == 1 and fake.bypassed is True, "applied all the same"


def test_a_first_command_applied_before_elkpy_subscribed_is_done_not_failed(sushi):
    fake = sushi(subscribe_after=0.4)
    with _connected(fake, subscribed=False) as c:
        started = time.monotonic()
        live.set_live_bypass("reverb", True, controller=c)
        took = time.monotonic() - started
    assert fake.bypassed is True
    assert took < live.READBACK_AFTER + live.READBACK_EVERY + 0.5, f"read back at once, not after {took:.2f}s"


def test_a_first_command_neither_applied_nor_confirmed_is_not_done(sushi, monkeypatch):
    monkeypatch.setattr(live, "CONFIRM_TIMEOUT", 0.6)
    fake = sushi(subscribe_after=0.2, apply=False)
    with _connected(fake, subscribed=False) as c:
        with pytest.raises(live.LiveError, match="shows it was not applied"):
            live.set_live_bypass("reverb", True, controller=c)
    assert fake.bypassed is False
