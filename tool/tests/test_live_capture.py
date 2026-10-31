"""`live.capture` against a stand-in controller: what it records of a running
rig. The decisions it makes (programs only without parameters, no track
bypass, properties with the parameters) are pinned here; that elkpy really
answers these calls is verified by `verify` in the container."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from sushi_rig import live


class _Params:
    def __init__(self):
        self.values = {(1, 0): 0.5}
        self.props = {1: [SimpleNamespace(id=4, name="destination_name", label="destination name")],
                      0: []}
        self.prop_values = {(1, 4): "master_return"}

    def get_processor_parameters(self, pid):
        return [SimpleNamespace(id=0, name="gain", automatable=True)] if pid == 1 else []

    def get_parameter_value(self, pid, param_id):
        return self.values[(pid, param_id)]

    def get_processor_properties(self, pid):
        return self.props[pid]

    def get_track_properties(self, tid):
        return self.props[tid]

    def get_property_value(self, pid, prop_id):
        return self.prop_values[(pid, prop_id)]


class _Graph:
    def get_all_tracks(self):
        return [SimpleNamespace(id=0, name="guitar")]

    def get_track_processors(self, tid):
        return [SimpleNamespace(id=1, name="guitar_send")]

    def get_processor_info(self, pid):
        return SimpleNamespace(program_count=0)

    def get_processor_bypass_state(self, pid):
        return False


class _Controller:
    def __init__(self):
        self.audio_graph = _Graph()
        self.parameters = _Params()
        self.programs = SimpleNamespace(get_processor_current_program=lambda pid: 0)
        self.closed = False

    def close(self):
        self.closed = True


@pytest.fixture
def controller(monkeypatch):
    fake = _Controller()
    monkeypatch.setattr(live, "_controller", lambda address: fake)
    return fake


def test_capture_records_a_sends_destination_beside_its_parameters(controller):
    state = live.capture()
    send = state["processors"]["guitar_send"]
    assert send["parameters"] == {"gain": 0.5}
    assert send["properties"] == {"destination_name": "master_return"}
    assert state["processors"]["guitar"]["properties"] == {}
    assert state["tracks"] == {"guitar": ["guitar_send"]}
    assert controller.closed


def test_a_rig_that_cannot_be_asked_about_properties_leaves_the_key_out(controller):
    """Absent, not empty: verify reads absent as unchecked and empty as none."""
    def refuse(pid):
        raise RuntimeError("no such call")

    controller.parameters.get_processor_properties = refuse
    state = live.capture()
    assert "properties" not in state["processors"]["guitar_send"]
