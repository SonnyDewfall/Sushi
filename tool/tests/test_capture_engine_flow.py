"""`CaptureEngine`'s sequence of calls, against a hand-written stand-in for JACK.

Read this for what it is. The stand-in is a few dozen lines of Python written
from JACK-Client's documented shape, not a capture of the real thing, so it
proves the engine's own logic (what it calls, how many times, what it does when
a read comes back empty) and nothing about JACK or PipeWire. Where the real
library behaves otherwise, these pass and the rig run fails. That is why each
test here is about a decision the engine makes, and the engine's `capture.py`
docstring still lists the live behaviour as unverified.

Its own module because it needs numpy (the pump allocates its buffer).
"""

from __future__ import annotations

import sys
import types

import pytest

np = pytest.importorskip("numpy")

from sushi_rig import capture  # noqa: E402
from sushi_rig.take import DRY, Stem  # noqa: E402

SOURCE = "Audient iD14:capture_AUX0"
PLAN = ((Stem("guitar", DRY, SOURCE), 1),)


class FakeJack:
    """Just enough of `jack` for `CaptureEngine`. `visible_after` is how many
    reads of a port's connections come back empty before the link shows, which
    is what a registry that updates asynchronously would do."""

    class JackError(Exception):
        pass

    def __init__(self, blocksize=256, visible_after=0):
        self.blocksize, self.visible_after = blocksize, visible_after
        self.activations = 0
        self.clients = []
        self.on_activate = []  # called with the client, to play things that happen at activation
        self.on_connect = []   # called after each connect, to play a cycle between links
        outer = self

        class Port:
            def __init__(self, name):
                self.name = name
                self.reads = 0

            def get_array(self):
                return np.zeros(outer.blocksize, dtype=np.float32)

        class Ports(list):
            def register(self, short):
                port = Port(f"sushi-rig-capture:{short}")
                self.append(port)
                return port

        class Client:
            def __init__(self, name, no_start_server=False):
                self.name, self.inports, self.links = name, Ports(), {}
                self.samplerate = 48000
                self.closed = False
                outer.clients.append(self)

            @property
            def blocksize(self):
                return outer.blocksize

            def get_ports(self, **kwargs):
                return [types.SimpleNamespace(name=SOURCE)]

            def activate(self):
                outer.activations += 1
                for hook in outer.on_activate:
                    hook(self)

            def deactivate(self, ignore_errors=True):
                pass

            def close(self, ignore_errors=True):
                self.closed = True

            def connect(self, source, port):
                self.links[port.name] = source
                for hook in outer.on_connect:
                    hook(self)

            def get_all_connections(self, port):
                port.reads += 1
                if port.reads <= outer.visible_after or port.name not in self.links:
                    return []
                return [types.SimpleNamespace(name=self.links[port.name])]

            def set_process_callback(self, fn): self.process = fn
            def set_xrun_callback(self, fn): pass
            def set_blocksize_callback(self, fn): self.blocksize_cb = fn
            def set_shutdown_callback(self, fn): pass

        self.Client = Client

    def install(self, monkeypatch):
        module = types.ModuleType("jack")
        module.Client, module.JackError = self.Client, self.JackError
        monkeypatch.setitem(sys.modules, "jack", module)


def engine(**kwargs):
    return capture.CaptureEngine(48000, 256, settle_s=0.0, link_retry_s=0.0, **kwargs)


def test_a_link_that_shows_up_a_moment_late_is_not_a_refused_capture(monkeypatch):
    """Under pipewire-jack the client's view of the graph may lag a connect().
    Whether it does is unverified; a good start must not depend on it not."""
    FakeJack(visible_after=3).install(monkeypatch)
    e = engine()
    e.start(PLAN, lambda block: len(block))
    assert e.stop()["error"] == ""


def test_a_link_that_never_shows_is_refused_after_trying(monkeypatch):
    FakeJack(visible_after=10**6).install(monkeypatch)
    e = engine()
    with pytest.raises(capture.CaptureError, match="should be fed by Audient iD14:capture_AUX0 alone"):
        e.start(PLAN, lambda block: len(block))


def counting_sleep(monkeypatch):
    """Records every wait the engine asks for, without making it."""
    waits = []
    monkeypatch.setattr(capture.time, "sleep", waits.append)
    return waits


def test_the_quantum_is_read_once_not_once_per_take(monkeypatch):
    """Reading it costs a wait on the caller's thread, which in the listener is
    the one OSC thread: a pause on every RECORD. And the first take uses one
    client, not a probe and a capture."""
    jack = FakeJack()
    jack.install(monkeypatch)
    waits = counting_sleep(monkeypatch)
    e = capture.CaptureEngine(48000, 256, settle_s=0.123, link_retry_s=0.0)
    e.start(PLAN, lambda block: len(block))
    e.stop()
    assert (len(jack.clients), jack.activations) == (1, 1)
    e.start(PLAN, lambda block: len(block))
    e.stop()
    assert (len(jack.clients), jack.activations) == (2, 2)
    assert waits.count(0.123) == 1, "the settle is paid by the first take only"


def test_prepare_pays_the_wait_ahead_of_the_first_take_on_a_client_of_its_own(monkeypatch):
    jack = FakeJack()
    jack.install(monkeypatch)
    waits = counting_sleep(monkeypatch)
    e = capture.CaptureEngine(48000, 256, settle_s=0.123, link_retry_s=0.0)
    e.prepare()
    probe = jack.clients[0]
    assert probe.closed and probe.name != e.client_name, "a name of its own, so no -01 rename"
    e.start(PLAN, lambda block: len(block))
    assert jack.clients[1].name == e.client_name
    assert waits.count(0.123) == 1, "and the take does not wait again"
    e.stop()


def test_nothing_reaches_the_recorder_before_every_link_has_settled(monkeypatch):
    """The process callback is live from activation, but the first block pushed
    must be the first with every column linked: otherwise the take opens with
    silence and columns linked on different cycles sit a quantum apart."""
    jack = FakeJack(visible_after=2)
    jack.install(monkeypatch)
    pushed = []
    e = engine()

    def a_cycle(client):
        client.process(256)

    jack.on_activate.append(a_cycle)
    jack.on_connect.append(a_cycle)
    e.start(PLAN, lambda block: pushed.append(len(block)) or len(block))
    assert pushed == [], "cycles ran at activation and between links; none was pushed"
    jack.clients[0].process(256)
    assert pushed == [256], "the gate is open once start returns"
    account = e.stop()
    assert account["gated_cycles"] == 2 and account["blocks"] == 1


def test_a_start_refused_at_the_links_has_fed_the_recorder_nothing(monkeypatch):
    jack = FakeJack(visible_after=10**6)
    jack.install(monkeypatch)
    pushed = []
    jack.on_connect.append(lambda client: client.process(256))
    e = engine()
    with pytest.raises(capture.CaptureError):
        e.start(PLAN, lambda block: pushed.append(1) or len(block))
    assert pushed == []


def test_the_start_up_quantum_transient_does_not_flag_a_clean_take(monkeypatch):
    jack = FakeJack()
    jack.install(monkeypatch)
    jack.on_activate.append(lambda client: [client.blocksize_cb(n) for n in (256, 1024, 256)])
    e = engine()
    e.start(PLAN, lambda block: len(block))
    account = e.stop()
    assert account["quantum_changes"] == 0


def test_a_wrong_quantum_is_refused_after_activation_and_read_again_next_time(monkeypatch):
    jack = FakeJack(blocksize=1024)
    jack.install(monkeypatch)
    pushed = []
    jack.on_activate.append(lambda client: client.process(1024))
    e = engine()
    with pytest.raises(capture.CaptureError, match="granted a quantum of 1024"):
        e.start(PLAN, lambda block: pushed.append(1) or len(block))
    assert pushed == []
    jack.blocksize = 256
    e.start(PLAN, lambda block: len(block))  # not stuck on the old answer
    e.stop()


@pytest.mark.parametrize("fails", ["plan", "jack"])
def test_a_start_that_fails_early_does_not_hand_back_the_last_takes_account(monkeypatch, fails):
    FakeJack().install(monkeypatch)
    e = engine()
    e.start(PLAN, lambda block: len(block))
    e.stop()
    assert e.stop() is not None, "a finished take's account is kept for its own stop"
    if fails == "plan":
        with pytest.raises(capture.CaptureError):
            e.start(((Stem("guitar", DRY, SOURCE), 0),), lambda block: len(block))
    else:
        monkeypatch.setitem(sys.modules, "jack", None)
        with pytest.raises(capture.CaptureUnavailable):
            e.start(PLAN, lambda block: len(block))
    assert e.stop() is None, "no account at all, not an empty one the take would keep as `engine: {}`"
