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


def test_a_property_whose_value_cannot_be_read_is_recorded_as_none(controller, capsys):
    def refuse(pid, prop_id):
        raise RuntimeError("NOT_FOUND")

    controller.parameters.get_property_value = refuse
    state = live.capture()
    assert state["processors"]["guitar_send"]["properties"] == {"destination_name": None}


def test_a_listing_that_fails_is_said_on_stderr(controller, capsys):
    controller.parameters.get_processor_properties = lambda pid: (_ for _ in ()).throw(RuntimeError("x"))
    live.capture()
    assert "properties could not be listed" in capsys.readouterr().err


def test_a_probe_track_left_running_is_left_out_and_said(controller, capsys):
    """A selftest killed mid-probe leaves its track in Sushi. It is no part of
    any rig, so a capture leaves it out and says why, rather than SAVE refusing
    the capture as "a different rig" and losing the save (found by review)."""
    from sushi_rig.spec import PROBE_TRACK

    tracks = [SimpleNamespace(id=0, name="guitar"), SimpleNamespace(id=7, name=PROBE_TRACK)]
    controller.audio_graph.get_all_tracks = lambda: tracks
    asked = []
    original = controller.audio_graph.get_track_processors
    controller.audio_graph.get_track_processors = lambda tid: asked.append(tid) or original(tid)
    state = live.capture()
    assert PROBE_TRACK not in state["tracks"] and PROBE_TRACK not in state["processors"]
    assert asked == [0]
    err = capsys.readouterr().err
    assert f"{PROBE_TRACK!r} is still in Sushi" in err
    assert "selftest that did not finish" in err


def test_a_save_made_with_a_probe_left_running_still_folds(controller):
    """The path the review found: SAVE of a rig of channels after a killed
    selftest. The capture carries nothing of the probe, so the fold is the
    rig's own and nothing names a 'different rig'."""
    from sushi_rig.rigfile import fold_state
    from sushi_rig.spec import PROBE_TRACK

    controller.audio_graph.get_all_tracks = lambda: [
        SimpleNamespace(id=0, name="guitar"), SimpleNamespace(id=7, name=PROBE_TRACK)]
    raw = {
        "kind": "rig", "schema_version": 1, "name": "t",
        "channels": [{"name": "guitar", "input": "guitar-in", "width": "mono", "chain": []}],
        "master": {"output": "monitor-out", "chain": []},
    }
    assert fold_state(raw, live.capture())["channels"][0]["name"] == "guitar"


def test_the_masters_return_having_no_parameters_is_not_a_warning_at_every_poll(controller, capsys, monkeypatch):
    """On the rig on 2026-11-02, `warning: 'master_return' returned no
    parameters` came about once a second under the web UI's poll and buried
    the crash and the refused take. The compiler's return has none by design;
    another processor with none is said once, not at every capture."""
    monkeypatch.setattr(live, "_SAID_NO_PARAMETERS", set())
    tracks = [SimpleNamespace(id=0, name="guitar"), SimpleNamespace(id=5, name="master")]
    chains = {0: [SimpleNamespace(id=1, name="guitar_send"), SimpleNamespace(id=2, name="fuzz")],
              5: [SimpleNamespace(id=6, name="master_return")]}
    controller.audio_graph.get_all_tracks = lambda: tracks
    controller.audio_graph.get_track_processors = lambda tid: chains[tid]
    params = controller.parameters
    params.props.update({2: [], 5: [], 6: []})
    gain = params.get_processor_parameters
    params.get_processor_parameters = lambda pid: [SimpleNamespace(id=0, name="gain", automatable=True)] \
        if pid in (0, 5) else gain(pid)
    params.values.update({(0, 0): 0.7, (5, 0): 0.7})
    for _ in range(3):
        live.capture()
    err = capsys.readouterr().err
    assert "master_return" not in err
    assert err.count("'fuzz' on 'guitar' returned no parameters") == 1, err
