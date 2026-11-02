"""`set_live_parameter` and `set_live_bypass` against a stand-in controller:
that they look names up to ids, set the value as a float, and close the
connection however it goes. That elkpy answers these calls as written here is
not shown by this file: it was read from elkpy 1.3.0, and no Sushi was
available when they were added."""

from __future__ import annotations

import sys
from types import SimpleNamespace

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


class _MoveGraph:
    """Two tracks, so a wrong track id would show; records the one command."""

    def __init__(self, fake):
        self.fake = fake
        self.moves = []

    def get_all_tracks(self):
        return [SimpleNamespace(id=1, name="main"), SimpleNamespace(id=9, name="aux")]

    def get_track_processors(self, tid):
        if tid == 9:
            return [SimpleNamespace(id=7, name="delay")]
        return [SimpleNamespace(id=2, name="comp"), SimpleNamespace(id=3, name="drive"),
                SimpleNamespace(id=4, name="reverb")]

    def move_processor_on_track(self, *args):
        self.moves.append(args)
        return self.fake.response


@pytest.fixture
def move_controller(monkeypatch):
    fake = _Controller()
    fake.audio_graph = _MoveGraph(fake)
    monkeypatch.setattr(live, "_controller", lambda address: fake)
    monkeypatch.setattr(live, "CONFIRM_TIMEOUT", 0.05)
    return fake


def test_a_move_is_only_reported_when_sushi_confirmed_it(move_controller):
    """The agent and the listener both act on `outcome == "moved"`."""
    fake = move_controller
    fake.response = _Response()
    move = live.move_live_processor("drive", -1)
    assert move.outcome == "moved" and move.message == "moved drive earlier"
    assert move.chain == ["comp", "drive", "reverb"], "the chain it was on before"
    assert fake.audio_graph.moves == [(3, 1, 1, 2, False)], \
        "processor, source track, destination track, before comp, not at the back"
    for response in (_Response(error=True), _Response(set_=False)):
        fake.response = response
        with pytest.raises(live.LiveError):
            live.move_live_processor("drive", -1)
        assert fake.closed


def test_a_move_that_is_a_no_op_or_has_no_target_sends_nothing(move_controller):
    fake = move_controller
    unchanged = live.move_live_processor("comp", -1)
    assert (unchanged.outcome, unchanged.message) == ("unchanged", "comp is already first in the chain")
    assert live.move_live_processor("reverb", 1).message == "reverb is already last in the chain"
    absent = live.move_live_processor("main", 1)
    assert (absent.outcome, absent.chain) == ("absent", None), "a track is not a processor"
    assert absent.message == "no processor named 'main' on any track"
    assert fake.audio_graph.moves == []


def test_the_last_step_later_is_the_back_of_the_chain(move_controller):
    move_controller.response = _Response()
    live.move_live_processor("drive", 1)
    assert move_controller.audio_graph.moves == [(3, 1, 1, 0, True)]


def test_a_planned_move_is_sent_as_planned_without_walking_again(move_controller):
    fake = move_controller
    fake.response = _Response()
    planned = live.plan_live_move("drive", -1)
    assert planned.outcome == "planned" and fake.audio_graph.moves == [], "planning sends nothing"
    fake.audio_graph.get_all_tracks = lambda: pytest.fail("walked again")
    sent = live.send_live_move(planned)
    assert sent.outcome == "moved" and fake.audio_graph.moves == [(3, 1, 1, 2, False)]


@pytest.mark.parametrize("processor, direction", [("comp", -1), ("nope", 1)])
def test_only_a_planned_move_can_be_sent(move_controller, processor, direction):
    with pytest.raises(ValueError, match="planned"):
        live.send_live_move(live.plan_live_move(processor, direction))
    assert move_controller.audio_graph.moves == []


def test_a_move_on_the_callers_controller_walks_once_and_leaves_it_open(move_controller):
    fake = move_controller
    fake.response = _Response()
    walks = []
    real = fake.audio_graph.get_all_tracks
    fake.audio_graph.get_all_tracks = lambda: (walks.append(1), real())[1]
    live.move_live_processor("drive", -1, controller=fake)
    assert len(walks) == 1, "one walk, one decision, one command"
    assert not fake.closed


def test_a_setter_on_the_callers_controller_uses_it_and_leaves_it_open(controller, monkeypatch):
    monkeypatch.setattr(live, "_controller", lambda address: pytest.fail("built a second connection"))
    live.set_live_parameter("reverb", "Mix", 0.5, controller=controller)
    live.set_live_bypass("reverb", True, controller=controller)
    assert len(controller.calls) == 5
    assert not controller.closed
