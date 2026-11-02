"""`set_live_parameter` and `set_live_bypass` against a stand-in controller:
that they look names up to ids, set the value as a float, and close the
connection however it goes. That elkpy answers these calls as written here is
not shown by this file: it was read from elkpy 1.3.0, and no Sushi was
available when they were added."""

from __future__ import annotations

import sys

import pytest

from sushi_rig import live


class _Controller:
    def __init__(self):
        self.calls = []
        self.closed = False
        self.audio_graph = self
        self.parameters = self

    def get_processor_id(self, name):
        self.calls.append(("get_processor_id", name))
        return {"reverb": 7}[name]

    def get_parameter_id(self, pid, name):
        self.calls.append(("get_parameter_id", pid, name))
        return {"Mix": 3}[name]

    response = None  # what a setter hands back; None is a Sushi that answered at once

    def set_parameter_value(self, pid, param_id, value):
        self.calls.append(("set_parameter_value", pid, param_id, value))
        return self.response

    def set_processor_bypass_state(self, pid, state):
        self.calls.append(("set_processor_bypass_state", pid, state))
        return self.response

    def close(self):
        self.closed = True


@pytest.fixture
def controller(monkeypatch):
    fake = _Controller()
    monkeypatch.setattr(live, "_controller", lambda address: fake)
    return fake


def test_a_parameter_is_set_by_the_ids_its_names_resolve_to(controller):
    live.set_live_parameter("reverb", "Mix", 1, "somewhere:1")
    assert controller.calls == [
        ("get_processor_id", "reverb"),
        ("get_parameter_id", 7, "Mix"),
        ("set_parameter_value", 7, 3, 1.0),
    ]
    assert isinstance(controller.calls[-1][-1], float)
    assert controller.closed


def test_bypass_is_set_on_the_processor_the_name_resolves_to(controller):
    live.set_live_bypass("reverb", True)
    assert controller.calls == [
        ("get_processor_id", "reverb"), ("set_processor_bypass_state", 7, True),
    ]
    assert controller.closed


@pytest.mark.parametrize("call", [
    lambda: live.set_live_parameter("nope", "Mix", 0.5),
    lambda: live.set_live_parameter("reverb", "nope", 0.5),
    lambda: live.set_live_bypass("nope", True),
])
def test_an_unknown_name_propagates_and_the_connection_is_still_closed(controller, call):
    with pytest.raises(KeyError):
        call()
    assert controller.closed


# --------------------------------------------------------------------------- #
# A change Sushi did not confirm is an error, not a warning
# --------------------------------------------------------------------------- #

class _Response:
    """Stands in for elkpy's SushiCommandResponse: an event Sushi sets, with
    `error` true if what it set it for was a failure."""

    def __init__(self, set_=True, error=False):
        self._set, self.error = set_, error

    def is_set(self):
        return self._set


def test_a_strict_wait_raises_on_an_error_and_on_silence():
    live._wait(_Response(), strict=True)
    with pytest.raises(live.LiveError, match="reported an error"):
        live._wait(_Response(error=True), strict=True)
    with pytest.raises(live.LiveError, match="did not confirm"):
        live._wait(_Response(set_=False), timeout=0.05, interval=0.01, strict=True)


def test_a_lenient_wait_still_only_warns(capsys):
    """`push` builds a whole rig with it and carries on past a slow answer."""
    live._wait(_Response(error=True))
    live._wait(_Response(set_=False), timeout=0.05, interval=0.01)
    err = capsys.readouterr().err
    assert "Sushi reported an error" in err and "not confirmed" in err


@pytest.mark.parametrize("response", [_Response(error=True), _Response(set_=False)])
@pytest.mark.parametrize("call", [
    lambda: live.set_live_parameter("reverb", "Mix", 0.5),
    lambda: live.set_live_bypass("reverb", True),
])
def test_a_setter_raises_when_sushi_does_not_confirm(controller, monkeypatch, response, call):
    monkeypatch.setattr(live, "CONFIRM_TIMEOUT", 0.05)
    controller.response = response
    with pytest.raises(live.LiveError):
        call()
    assert controller.closed


def test_a_missing_elkpy_is_an_exception_with_the_hint_not_an_exit(monkeypatch):
    """A library module that exits the process takes the agent's executor
    thread, and with it the event loop, down."""
    monkeypatch.setitem(sys.modules, "elkpy", None)
    monkeypatch.delitem(sys.modules, "elkpy.sushicontroller", raising=False)
    with pytest.raises(live.ElkpyMissing, match="pip install elkpy") as caught:
        live._controller("localhost:1")
    assert isinstance(caught.value, live.LiveError)


def test_a_move_is_only_reported_when_sushi_confirmed_it(monkeypatch):
    """`move_processor` said "moved ..." whatever Sushi answered, and the agent
    and the listener both act on that word."""
    from types import SimpleNamespace

    class Graph:
        def get_all_tracks(self):
            return [SimpleNamespace(id=1, name="main")]

        def get_track_processors(self, tid):
            return [SimpleNamespace(id=2, name="comp"), SimpleNamespace(id=3, name="drive")]

        def move_processor_on_track(self, *args):
            return fake.response

    fake = _Controller()
    fake.audio_graph = Graph()
    monkeypatch.setattr(live, "_controller", lambda address: fake)
    monkeypatch.setattr(live, "CONFIRM_TIMEOUT", 0.05)

    fake.response = _Response()
    assert live.move_processor("drive", -1) == "moved drive earlier"
    for response in (_Response(error=True), _Response(set_=False)):
        fake.response = response
        with pytest.raises(live.LiveError):
            live.move_processor("drive", -1)
        assert fake.closed
