"""`set_live_parameter` and `set_live_bypass` against a stand-in controller:
that they look names up to ids, set the value as a float, and close the
connection however it goes. That elkpy answers these calls as written here is
not shown by this file: it was read from elkpy 1.3.0, and no Sushi was
available when they were added."""

from __future__ import annotations

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

    def set_parameter_value(self, pid, param_id, value):
        self.calls.append(("set_parameter_value", pid, param_id, value))

    def set_processor_bypass_state(self, pid, state):
        self.calls.append(("set_processor_bypass_state", pid, state))

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
