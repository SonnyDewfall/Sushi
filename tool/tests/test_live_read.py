"""The targeted reads behind the agent's live routes, against a stand-in
controller: that each costs what the request names, whatever the size of the
rig, and says what the decisions need. That a real Sushi answers the calls this
makes is not shown here (read from elkpy 1.3.0, not run)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from sushi_rig import live

PARAMS_PER_PROCESSOR = 60


class _Controller:
    """Two tracks of three processors each, every processor with many parameters;
    records each call so a test can say what was *not* asked."""

    def __init__(self):
        self.calls = []
        self.closed = False
        self.audio_graph = self
        self.parameters = self
        self.tracks = {"main": ["comp", "drive", "reverb"], "aux": ["delay", "eq", "gate"]}
        self.next_id = 1
        self.ids = {}
        for track, chain in self.tracks.items():
            self.ids[track] = self._new()
            for name in chain:
                self.ids[name] = self._new()
        self.bypass = {"comp": False, "drive": True}

    def _new(self):
        self.next_id += 1
        return self.next_id

    def get_all_tracks(self):
        self.calls.append("get_all_tracks")
        return [SimpleNamespace(id=self.ids[t], name=t) for t in self.tracks]

    def get_track_processors(self, tid):
        self.calls.append("get_track_processors")
        track = next(t for t in self.tracks if self.ids[t] == tid)
        return [SimpleNamespace(id=self.ids[n], name=n) for n in self.tracks[track]]

    def get_processor_parameters(self, pid):
        self.calls.append(("get_processor_parameters", pid))
        return [SimpleNamespace(id=i, name=f"p{i}", automatable=i != 1)
                for i in range(PARAMS_PER_PROCESSOR)]

    def get_parameter_value(self, pid, param_id):
        self.calls.append(("get_parameter_value", pid, param_id))
        return 0.25

    def get_processor_bypass_state(self, pid):
        self.calls.append(("get_processor_bypass_state", pid))
        name = next(n for n, i in self.ids.items() if i == pid)
        if name not in self.bypass:
            raise RuntimeError("no state")
        return self.bypass[name]

    def close(self):
        self.closed = True


def _parameter_calls(controller, since=0):
    return [c for c in controller.calls[since:] if isinstance(c, tuple)]


@pytest.fixture
def controller(monkeypatch):
    fake = _Controller()
    monkeypatch.setattr(live, "_controller", lambda address: fake)
    return fake


def test_the_chains_are_the_tracks_and_nothing_else(controller):
    assert live.get_live_chains() == {
        "main": ["comp", "drive", "reverb"], "aux": ["delay", "eq", "gate"],
    }
    assert controller.calls == ["get_all_tracks", "get_track_processors", "get_track_processors"]
    assert controller.closed


def test_one_parameter_costs_one_listing_and_one_value_not_the_rigs_parameters(controller):
    reading = live.read_live_parameter("drive", "p7")
    assert reading["value"] == 0.25
    assert reading["chains"]["main"] == ["comp", "drive", "reverb"]
    assert set(reading["parameters"]) == {f"p{i}" for i in range(PARAMS_PER_PROCESSOR)}
    assert reading["parameters"]["p1"] == {"automatable": False}
    assert _parameter_calls(controller) == [
        ("get_processor_parameters", controller.ids["drive"]),
        ("get_parameter_value", controller.ids["drive"], 7),
    ]
    assert controller.closed


def test_a_track_has_parameters_to_set_too(controller):
    assert live.read_live_parameter("main", "p3")["value"] == 0.25


def test_a_parameter_that_is_read_only_or_absent_is_not_read(controller):
    assert live.read_live_parameter("drive", "p1")["value"] is None, "a meter: not automatable"
    assert live.read_live_parameter("drive", "nope")["value"] is None
    assert not any(c[0] == "get_parameter_value" for c in _parameter_calls(controller))


def test_an_unknown_processor_reads_no_parameters_at_all(controller):
    reading = live.read_live_parameter("nope", "p3")
    assert reading["parameters"] is None and reading["value"] is None
    assert _parameter_calls(controller) == []


def test_a_bypass_read_is_the_chains_and_the_one_state(controller):
    reading = live.read_live_bypass("drive")
    assert reading == {"chains": {"main": ["comp", "drive", "reverb"], "aux": ["delay", "eq", "gate"]},
                       "bypassed": True}
    assert _parameter_calls(controller) == [("get_processor_bypass_state", controller.ids["drive"])]
    assert controller.closed


def test_a_bypass_state_sushi_would_not_give_is_none_and_a_track_is_not_asked(controller):
    assert live.read_live_bypass("reverb")["bypassed"] is None
    assert live.read_live_bypass("nope")["bypassed"] is None
    before = len(controller.calls)
    assert live.read_live_bypass("main")["bypassed"] is None
    assert _parameter_calls(controller, before) == []


def test_the_rig_is_read_on_one_connection(monkeypatch):
    """/live used to open one connection for `capture` and another for the
    parameter info."""
    opened = []
    fake = _Controller()
    fake.parameters = SimpleNamespace(
        get_processor_parameters=lambda pid: [SimpleNamespace(id=0, name="g", automatable=True)],
        get_parameter_value=lambda pid, param: 0.5,
        get_processor_properties=lambda pid: [],
        get_track_properties=lambda pid: [],
    )
    fake.audio_graph = SimpleNamespace(
        get_all_tracks=fake.get_all_tracks,
        get_track_processors=fake.get_track_processors,
        get_processor_bypass_state=lambda pid: False,
        get_processor_info=lambda pid: SimpleNamespace(program_count=0),
    )
    fake.programs = SimpleNamespace(get_processor_current_program=lambda pid: 0)
    monkeypatch.setattr(live, "_controller", lambda address: opened.append(address) or fake)
    rig = live.read_rig("somewhere:1")
    assert len(opened) == 1
    assert set(rig) == {"processors", "tracks", "parameter_info"}
    assert rig["parameter_info"]["drive"]["g"]["value"] == 0.5
    assert rig["processors"]["drive"]["parameters"] == {"g": 0.5}
    assert fake.closed


def test_a_caller_that_passes_a_connection_keeps_it_open():
    fake = _Controller()
    fake.parameters = SimpleNamespace(
        get_processor_parameters=lambda pid: [], get_parameter_value=lambda *a: 0.0)
    live.get_live_parameter_info(controller=fake)
    assert fake.closed is False
