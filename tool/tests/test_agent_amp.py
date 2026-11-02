"""The agent's amp: GET /amp, GET /amp/models, POST /amp/parameter and
/amp/model, and undo of each, against a fake Carla (`carla_fake.py`).

What these pin is what the agent decides and records: what it knows of the
amp (what it last sent, never what Carla holds), when it refuses, what it
logs and what undo puts back. Carla is `FakeCarla` (in memory) or `OscCarla`
(a real UDP and TCP listener, reached by the agent's own sender). The model
change is shown here against a stand-in supervisor, for what the agent does
around it; the Carla-only restart itself, with real processes in a held
group, is in test_supervisor.py.

NOT shown: any of it against a real Carla. Whether Carla applies a value,
how long a restart silences the amp, and what Carla makes of a TCP
connection opened and closed per send are all unverified.
"""

from __future__ import annotations

import asyncio
import copy
import json
from pathlib import Path

import pytest

pytest.importorskip("aiohttp")
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from sushi_rig import amp_live  # noqa: E402
from sushi_rig.agent import MODEL_NAME, Agent, Switching  # noqa: E402
from sushi_rig.supervisor import AmpRestart, Outcome  # noqa: E402

from carla_fake import FakeCarla, OscCarla  # noqa: E402


@pytest.fixture(autouse=True)
def _agents_release_their_pools(monkeypatch):
    made = []
    original = Agent.__init__

    def recording(self, *args, **kwargs):
        original(self, *args, **kwargs)
        made.append(self)

    monkeypatch.setattr(Agent, "__init__", recording)
    yield
    for agent in made:
        agent._executor.shutdown(wait=True, cancel_futures=True)


def call(agent, method, path, **kwargs):
    async def go():
        async with TestClient(TestServer(agent.app())) as client:
            response = await client.request(method, path, **kwargs)
            return response.status, await response.json()

    return asyncio.run(go())


def calls(agent, *requests):
    """Several requests against one served agent, in order, as (status, json)."""
    async def go():
        out = []
        async with TestClient(TestServer(agent.app())) as client:
            for method, path, kwargs in requests:
                response = await client.request(method, path, **kwargs)
                out.append((response.status, await response.json()))
        return out

    return asyncio.run(go())


def nam(path: Path, name: str, rate=48000, tiers=1):
    """A `.nam` as describe_model reads one: invented, with no real weights."""
    layer = {"channels": 8}
    if tiers > 1:
        model = {"architecture": "SlimmableContainer", "config": {"submodels": [
            {"model": {"architecture": "WaveNet", "config": {"layers": [layer]}, "weights": [0.0] * (4 * (i + 1))}}
            for i in range(tiers)]}}
    else:
        model = {"architecture": "WaveNet", "config": {"layers": [layer]}, "weights": [0.0] * 4}
    if rate is not None:
        model["sample_rate"] = rate
    model["metadata"] = {"name": name, "gear_make": "Test", "gear_type": "amp"}
    path.write_text(json.dumps(model))
    return path


@pytest.fixture
def root(tmp_path):
    """A checkout with three models: two at the rig's rate, one not."""
    models = tmp_path / "amp" / "models"
    models.mkdir(parents=True)
    nam(models / "First.nam", "First", tiers=2)
    nam(models / "Second.nam", "Second")
    nam(models / "Slow.nam", "Slow", rate=44100)
    (models / "Broken.nam").write_text("not json")
    return tmp_path


CONFIG = {"tracks": [], "_amp": {"plugin": "nam", "model": "amp/models/First.nam",
                                 "parameters": {"Input Lvl": -2.0, "Output Lvl": 6.0}}}


class World:
    """The state file, the running config, `describe_rig`'s answer and Carla."""

    def __init__(self, root, mode="headless", amp=True, alive=True, config=None):
        self.root = root
        self.state = {
            "supervisor_pid": 10, "pgid": 10, "mode": mode, "config_name": "board",
            "config": "/run/rigs/board.json", "started_at": "2026-11-02T10:00:00+00:00",
            "amp_model": "amp/models/First.nam" if amp else None, "amp_requested": True,
            "amp_model_requested": None, "children": {"sushi": 11, **({"amp": 12} if amp else {})},
        }
        self.config = copy.deepcopy(CONFIG if config is None else config)
        self.running = True
        self.alive = alive
        self.carla = FakeCarla()

    def described(self):
        if not self.running:
            return {"verdict": "absent", "running": False, "message": "No rig is running."}
        return {"verdict": "running", "running": True, "config_name": "board", "mode": self.state["mode"],
                "children": {n: {"pid": p, "alive": self.alive if n == "amp" else True}
                             for n, p in self.state["children"].items()},
                "message": "board (headless mode), up 2m"}

    def switching(self):
        return Switching(read_state=lambda: dict(self.state), read_config=lambda path: copy.deepcopy(self.config),
                         write_state=lambda state: self.state.update(state))


def amp_agent(root, world=None, supervisor=None, **kwargs):
    world = world or World(root)
    extra = {"supervisor": supervisor} if supervisor is not None else {}
    agent = Agent(root=root, describe=world.described, switching=world.switching(),
                  carla=world.carla.carla(), run=_runner, **extra, **kwargs)
    return agent, world


async def _runner(args):
    return Outcome(True, 0, "", "")


def by_name(answer):
    return {p["name"]: p for p in answer["parameters"]}


# --------------------------------------------------------------------------- #
# GET /amp: what the agent last sent, and saying so
# --------------------------------------------------------------------------- #

def test_the_amp_is_what_up_replayed_until_something_is_sent_and_says_it_is_not_carlas(root):
    agent, world = amp_agent(root)
    status, body = call(agent, "GET", "/amp")
    assert status == 200, body
    assert body["known"] == "sent" and "not what Carla holds" in body["known_note"]
    params = by_name(body)
    assert list(params) == ["Input Lvl", "Output Lvl", "Quality"], "Carla's order"
    assert params["Input Lvl"] == {"name": "Input Lvl", "value": -2.0, "min": -20.0, "max": 20.0, "unit": "dB",
                                   "panel": True, "source": "replayed"}
    assert params["Output Lvl"]["value"] == 6.0 and params["Output Lvl"]["panel"] is True
    assert params["Quality"]["value"] is None and params["Quality"]["source"] is None, \
        "unstated, so never sent: unknown, not a guessed default"
    assert params["Quality"]["panel"] is False and params["Quality"]["unit"] is None
    model = body["model"]
    assert model["stored"] == "amp/models/First.nam" and model["path"] == str(root / "amp/models/First.nam")
    assert model["name"] == "First" and model["sample_rate"] == 48000 and model["tiers"] == 2
    assert model["description"].startswith("First  (") and "matches the rig" in model["description"]
    assert body["carla"] == {"pid": 12, "alive": True} and body["held"] is False
    assert world.carla.sent == [], "a read sends nothing"


def test_a_config_value_up_would_not_send_is_not_claimed_as_sent(root):
    config = {"_amp": {"plugin": "nam", "model": "amp/models/First.nam",
                       "parameters": {"Input Lvl": "loud", "Output Lvl": 99.0, "Gain": 1.0}}}
    agent, world = amp_agent(root, World(root, config=config))
    params = by_name(call(agent, "GET", "/amp")[1])
    assert params["Input Lvl"]["value"] is None, "replay refuses it, so it was not sent"
    assert params["Output Lvl"]["value"] == 20.0, "replay clamps, so this is what was sent"


@pytest.mark.parametrize("change, status, code, said", [
    (lambda w: setattr(w, "running", False), 409, "no_rig", "no rig is running"),
    (lambda w: (w.state["children"].pop("amp"), w.state.update(amp_requested=False)), 409, "no_amp", "--no-amp"),
    (lambda w: (w.state["children"].pop("amp"), w.state.update(amp_model=None)), 409, "no_amp", "no _amp"),
    (lambda w: w.state["children"].pop("amp"), 409, "no_amp", "Carla did not start"),
])
def test_no_amp_to_read_is_refused_with_the_reason(root, change, status, code, said):
    world = World(root)
    change(world)
    agent, _ = amp_agent(root, world)
    got, body = call(agent, "GET", "/amp")
    assert (got, body["code"]) == (status, code) and said in body["reason"]


def test_with_the_panel_running_the_amp_is_the_panels_and_says_so(root):
    agent, world = amp_agent(root, World(root, mode="panel"))
    status, body = call(agent, "GET", "/amp")
    assert status == 200 and body["known"] == "panel" and "panel's faders" in body["known_note"]


def test_a_carla_that_died_is_shown_and_not_written_to(root):
    agent, world = amp_agent(root, World(root, alive=False))
    status, body = call(agent, "GET", "/amp")
    assert status == 200 and body["carla"]["alive"] is False
    status, body = call(agent, "POST", "/amp/parameter", json={"name": "Output Lvl", "value": 1.0})
    assert (status, body["code"]) == (502, "carla_down") and "/rig/restart" in body["reason"]
    assert world.carla.sent == []


def test_a_model_that_cannot_be_read_is_said_in_the_answer(root):
    world = World(root)
    world.state["amp_model"] = "amp/models/Gone.nam"
    agent, _ = amp_agent(root, world)
    body = call(agent, "GET", "/amp")[1]
    assert body["model"]["stored"] == "amp/models/Gone.nam" and body["model"]["description"] is None
    assert "amp model not found" in body["model"]["problem"]


def test_the_amp_is_read_again_for_each_run_of_the_rig(root):
    """A restart replays the config: what was sent before it is gone."""
    agent, world = amp_agent(root)
    first, second = calls(agent, ("POST", "/amp/parameter", {"json": {"name": "Output Lvl", "value": 1.0}}),
                          ("GET", "/amp", {}))
    assert by_name(second[1])["Output Lvl"] == {**by_name(second[1])["Output Lvl"], "value": 1.0, "source": "sent"}
    world.state["started_at"] = "2026-11-02T11:00:00+00:00"
    assert by_name(call(agent, "GET", "/amp")[1])["Output Lvl"]["value"] == 6.0


# --------------------------------------------------------------------------- #
# GET /amp/models
# --------------------------------------------------------------------------- #

def test_the_models_are_listed_with_their_rate_and_the_ones_at_another_refused(root):
    agent, world = amp_agent(root)
    status, body = call(agent, "GET", "/amp/models")
    assert status == 200 and body["rate"] == 48000
    models = {m["model"]: m for m in body["models"]}
    assert list(models) == ["Broken.nam", "First.nam", "Second.nam", "Slow.nam"]
    assert models["First.nam"]["usable"] and models["First.nam"]["playing"] and models["First.nam"]["tiers"] == 2
    assert models["Second.nam"]["usable"] and not models["Second.nam"]["playing"]
    assert not models["Slow.nam"]["usable"] and "44100 Hz" in models["Slow.nam"]["why"]
    assert not models["Broken.nam"]["usable"] and "not valid JSON" in models["Broken.nam"]["why"]


def test_a_model_with_no_rate_is_listed_usable_and_warned_of_as_doctor_does(root):
    nam(root / "amp" / "models" / "Old.nam", "Old", rate=None)
    agent, _ = amp_agent(root)
    old = next(m for m in call(agent, "GET", "/amp/models")[1]["models"] if m["model"] == "Old.nam")
    assert old["usable"] and "declares no sample rate" in old["warning"]


def test_a_model_is_read_once_until_its_file_changes(root):
    shelf = amp_live.ModelShelf()
    seen = []
    shelf.describe = lambda path: seen.append(path) or {"name": "x"}
    path = root / "amp" / "models" / "Second.nam"
    shelf.read(path), shelf.read(path)
    assert len(seen) == 1
    nam(path, "Second again", tiers=3)
    shelf.read(path)
    assert len(seen) == 2


# --------------------------------------------------------------------------- #
# POST /amp/parameter
# --------------------------------------------------------------------------- #

def test_a_parameter_is_sent_to_carla_logged_and_remembered(root):
    agent, world = amp_agent(root)
    status, body = call(agent, "POST", "/amp/parameter", json={"name": "Output Lvl", "value": 3.5})
    assert status == 200, body
    assert world.carla.sent == [("/Carla/0/set_parameter_value", [1, 3.5])]
    assert world.carla.asked == 1, "Carla was asked whether it answers first"
    assert (body["op"], body["args"], body["previous"], body["clamped"]) == (
        "amp", {"name": "Output Lvl", "value": 3.5}, 6.0, None)
    (entry,) = call(agent, "GET", "/live/log")[1]["log"]
    assert entry["op"] == "amp" and entry["previous"] == 6.0


def test_a_value_out_of_range_is_clamped_and_said(root):
    agent, world = amp_agent(root)
    status, body = call(agent, "POST", "/amp/parameter", json={"name": "Input Lvl", "value": 35})
    assert status == 200
    assert world.carla.values() == {"Input Lvl": 20.0}
    assert body["asked"] == 35 and body["args"]["value"] == 20.0
    assert body["clamped"] == "Input Lvl 35 is outside -20 to 20 dB, so 20 was sent"


@pytest.mark.parametrize("payload, said", [
    ({"name": "Gain", "value": 1}, "one of the amp's parameters"),
    ({"name": "Output Lvl"}, "'value' is required"),
    ({"name": "Output Lvl", "value": "loud"}, "must be a number"),
    ({"name": "Output Lvl", "value": True}, "must be a number"),
    ({"name": "Output Lvl", "value": None}, "must be a number"),
    (["Output Lvl", 1], "JSON object"),
])
def test_a_request_that_is_not_a_parameter_and_a_number_is_refused_before_carla(root, payload, said):
    agent, world = amp_agent(root)
    status, body = call(agent, "POST", "/amp/parameter", json=payload)
    assert status == 400 and said in body["reason"]
    assert world.carla.sent == [] and world.carla.asked == 0


def test_nan_is_refused_not_clamped_to_the_loudest_setting(root):
    agent, world = amp_agent(root)
    status, body = call(agent, "POST", "/amp/parameter", data='{"name": "Output Lvl", "value": NaN}',
                        headers={"content-type": "application/json"})
    assert status == 400 and world.carla.sent == []


def test_a_carla_that_does_not_answer_is_502_and_nothing_is_recorded(root):
    agent, world = amp_agent(root)
    world.carla.answering = False
    (sent, read, log, health) = calls(
        agent, ("POST", "/amp/parameter", {"json": {"name": "Output Lvl", "value": 3.0}}),
        ("GET", "/amp", {}), ("GET", "/live/log", {}), ("GET", "/health", {}))
    assert (sent[0], sent[1]["code"]) == (502, "carla_not_answering")
    assert "Carla is not answering" in sent[1]["reason"] and world.carla.sent == []
    assert by_name(read[1])["Output Lvl"]["value"] == 6.0, "still what was last sent"
    assert log[1]["log"] == []
    assert "Carla is not answering" in health[1]["agent"]["last_error"]["message"]


def test_a_send_that_fails_is_502_and_not_recorded(root):
    agent, world = amp_agent(root)
    world.carla.fail = OSError("network is unreachable")
    status, body = call(agent, "POST", "/amp/parameter", json={"name": "Output Lvl", "value": 3.0})
    assert status == 502 and "network is unreachable" in body["reason"]
    assert call(agent, "GET", "/live/log")[1]["log"] == []


def test_with_the_panel_running_the_agent_writes_nothing(root):
    agent, world = amp_agent(root, World(root, mode="panel"))
    status, body = call(agent, "POST", "/amp/parameter", json={"name": "Output Lvl", "value": 3.0})
    assert (status, body["code"]) == (409, "panel") and "run the rig headless" in body["reason"]
    assert world.carla.sent == [] and world.carla.asked == 0


def test_a_parameter_is_refused_while_a_lifecycle_command_runs(root):
    agent, world = amp_agent(root)

    async def go():
        async with TestClient(TestServer(agent.app())) as client:
            async with agent._lock:
                agent.busy = "up"
                response = await client.post("/amp/parameter", json={"name": "Output Lvl", "value": 3.0})
                return response.status, await response.json()

    status, body = asyncio.run(go())
    assert (status, body["code"]) == (409, "busy") and world.carla.sent == []


def test_the_agents_own_sender_reaches_a_real_osc_port(root):
    """`Carla.at`, the agent's default, against a UDP and TCP listener: the
    message that arrives is Carla's address with the index and the value,
    and once the TCP side is gone the agent says Carla is not answering."""
    with OscCarla() as carla:
        world = World(root)
        agent = Agent(root=root, describe=world.described, switching=world.switching(), run=_runner,
                      carla=amp_live.Carla.at(port=carla.port))
        status, body = call(agent, "POST", "/amp/parameter", json={"name": "Output Lvl", "value": -3.5})
        assert status == 200, body
        assert carla.got.wait(2)
        assert carla.received == [("/Carla/0/set_parameter_value", [1, -3.5])]
        assert carla.connected.wait(2), "asked over TCP whether Carla answers"
        carla.stop_answering()
        status, body = call(agent, "POST", "/amp/parameter", json={"name": "Output Lvl", "value": 0.0})
        assert (status, body["code"]) == (502, "carla_not_answering")


# --------------------------------------------------------------------------- #
# Undo of a parameter
# --------------------------------------------------------------------------- #

def test_undo_puts_back_what_was_last_sent_before_the_change(root):
    agent, world = amp_agent(root)
    _, undone = calls(agent, ("POST", "/amp/parameter", {"json": {"name": "Output Lvl", "value": 3.0}}),
                      ("POST", "/live/undo", {}))
    status, body = undone
    assert status == 200, body
    assert body["op"] == "undo" and body["undoes"] == 0
    assert body["args"] == {"of": "amp", "name": "Output Lvl", "value": 6.0} and body["previous"] == 3.0
    assert body["state"] == {"name": "Output Lvl", "value": 6.0}
    assert world.carla.values() == {"Output Lvl": 6.0}
    status, again = call(agent, "POST", "/live/undo")
    assert (status, again["code"]) == (409, "nothing_to_undo")


def test_undo_of_a_value_never_sent_before_is_stale_and_can_be_skipped(root):
    agent, world = amp_agent(root)
    _, undo, skip, after = calls(
        agent, ("POST", "/amp/parameter", {"json": {"name": "Quality", "value": 0.0}}),
        ("POST", "/live/undo", {}), ("POST", "/live/undo", {"json": {"skip": True}}), ("POST", "/live/undo", {}))
    assert (undo[0], undo[1]["code"]) == (409, "stale") and "nothing had been sent" in undo[1]["reason"]
    assert '{"skip": true}' in undo[1]["reason"]
    assert skip[0] == 200 and skip[1]["op"] == "stale" and skip[1]["skipped"]["op"] == "amp"
    assert after[1]["code"] == "nothing_to_undo"
    assert world.carla.values() == {"Quality": 0.0}, "nothing was sent by the refusal or the skip"


def test_undo_compares_with_what_was_last_sent_and_refuses_a_value_changed_since(root):
    """A restart replays the config, so what the change sent is no longer
    what was last sent: stale. (A fader on a panel would not be seen: the
    agent writes nothing while the panel runs.)"""
    agent, world = amp_agent(root)
    call(agent, "POST", "/amp/parameter", json={"name": "Output Lvl", "value": 3.0})
    world.state["started_at"] = "2026-11-02T11:00:00+00:00"
    status, body = call(agent, "POST", "/live/undo")
    assert (status, body["code"]) == (409, "stale") and "no longer as the change left it" in body["reason"]


def test_a_skip_of_an_amp_change_that_can_be_undone_is_refused(root):
    agent, world = amp_agent(root)
    _, skip = calls(agent, ("POST", "/amp/parameter", {"json": {"name": "Output Lvl", "value": 3.0}}),
                    ("POST", "/live/undo", {"json": {"skip": True}}))
    assert (skip[0], skip[1]["code"]) == (409, "not_stale")


def test_an_undo_carla_does_not_answer_is_502_and_the_entry_stays(root):
    agent, world = amp_agent(root)
    call(agent, "POST", "/amp/parameter", json={"name": "Output Lvl", "value": 3.0})
    world.carla.answering = False
    status, body = call(agent, "POST", "/live/undo")
    assert (status, body["code"]) == (502, "carla_not_answering")
    world.carla.answering = True
    assert call(agent, "POST", "/live/undo")[0] == 200


@pytest.mark.parametrize("entry, now, stale", [
    ({"op": "amp", "args": {"name": "Output Lvl", "value": 3.0}, "previous": 6.0}, {"Output Lvl": 3.0}, False),
    ({"op": "amp", "args": {"name": "Output Lvl", "value": 3.0}, "previous": 6.0}, {"Output Lvl": 2.0}, True),
    ({"op": "amp", "args": {"name": "Output Lvl", "value": 3.0}, "previous": 6.0}, {}, True),
    ({"op": "amp", "args": {"name": "Output Lvl", "value": 3.0}, "previous": None}, {"Output Lvl": 3.0}, True),
    ({"op": "amp_model", "args": {"model": "amp/models/Second.nam"}, "previous": "amp/models/First.nam"},
     "amp/models/Second.nam", False),
    ({"op": "amp_model", "args": {"model": "amp/models/Second.nam"}, "previous": "amp/models/First.nam"},
     "amp/models/First.nam", True),
])
def test_an_amp_entry_is_undone_only_where_the_last_sent_is_what_it_left(root, entry, now, stale):
    if isinstance(now, dict):
        amp = amp_live.AmpState((), "amp/models/First.nam", dict(now))
    else:
        amp = amp_live.AmpState((), now, {})
    if stale:
        with pytest.raises(amp_live.AmpRefused) as refused:
            amp_live.undo_check(entry, amp, root)
        assert refused.value.code == "stale"
    else:
        amp_live.undo_check(entry, amp, root)


def test_the_page_offers_amp_undo_in_the_same_log_as_sushis(root):
    """One log: an amp change and a Sushi change are undone newest first."""
    from sushi_rig.agent import pick_undo

    log = [{"op": "parameter", "args": {}, "previous": 0.1},
           {"op": "amp", "args": {"name": "Output Lvl", "value": 3.0}, "previous": 6.0}]
    assert pick_undo(log, 0) == (1, log[1])


# --------------------------------------------------------------------------- #
# POST /amp/model, against a stand-in supervisor
# --------------------------------------------------------------------------- #

class Held:
    ready, stopping = True, False


class FakeSupervisor:
    """The two things the agent asks a supervisor for a model change: whether
    it holds the rig, and `restart_amp`. `result` is what the restart says."""

    def __init__(self, world, result=None):
        self.world = world
        self.held = Held()
        self.calls = []
        self.result = result or AmpRestart(True, pid=99)
        self.crashed, self.problems = [], []

    async def restart_amp(self, project, values, *, model, override):
        self.calls.append({"project": project, "values": values, "model": model, "override": override})
        if self.result.ok:
            self.world.state.update(amp_model=model, amp_model_requested=override)
        return self.result

    def describe(self, rig=None):
        return {}


def model_agent(root, result=None, world=None):
    world = world or World(root)
    supervisor = FakeSupervisor(world, result)
    agent, world = amp_agent(root, world, supervisor=supervisor)
    return agent, world, supervisor


def test_a_model_change_restarts_carla_alone_with_the_new_project_and_the_values_sent(root, capsys):
    agent, world, supervisor = model_agent(root)
    _, (status, body), (_, amp), (_, log) = calls(
        agent, ("POST", "/amp/parameter", {"json": {"name": "Output Lvl", "value": 3.0}}),
        ("POST", "/amp/model", {"json": {"model": "Second.nam"}}), ("GET", "/amp", {}), ("GET", "/live/log", {}))
    assert status == 200 and body["changed"], body
    (asked,) = supervisor.calls
    assert f"<Value>{root / 'amp/models/Second.nam'}</Value>" in asked["project"]
    assert asked["values"] == {"Input Lvl": -2.0, "Output Lvl": 3.0}, "the knobs as last sent"
    assert (asked["model"], asked["override"]) == ("amp/models/Second.nam", "Second.nam")
    assert body["op"] == "amp_model" and body["args"] == {"model": "amp/models/Second.nam"}
    assert body["previous"] == "amp/models/First.nam"
    assert "The sound stopped" in body["message"] and "rest of the rig played on" in body["message"]
    assert "THE SOUND STOPS while Carla restarts" in capsys.readouterr().err
    assert amp["model"]["name"] == "Second" and by_name(amp)["Output Lvl"]["value"] == 3.0
    assert [e["op"] for e in log["log"]] == ["amp", "amp_model"]


def test_choosing_the_rigs_own_model_again_is_no_override(root):
    """So `restart` and a live switch treat the rig as its own again."""
    world = World(root)
    world.state["amp_model"] = "amp/models/Second.nam"
    agent, world, supervisor = model_agent(root, world=world)
    status, body = call(agent, "POST", "/amp/model", json={"model": "First.nam"})
    assert status == 200 and supervisor.calls[0]["override"] is None


def test_the_model_already_playing_restarts_nothing(root):
    agent, world, supervisor = model_agent(root)
    status, body = call(agent, "POST", "/amp/model", json={"model": "First.nam"})
    assert status == 200 and body["changed"] is False and supervisor.calls == []
    assert call(agent, "GET", "/live/log")[1]["log"] == []


@pytest.mark.parametrize("model, code, said", [
    ("Slow.nam", "wrong_rate", "44100 Hz"),
    ("Broken.nam", "unreadable", "not valid JSON"),
    ("Missing.nam", None, "there is no 'Missing.nam' in amp/models"),
    ("../First.nam", None, "invalid amp model"),
    ("..", None, "the name of a file in amp/models"),
    ("-x.nam", None, "the name of a file in amp/models"),
    ("amp/models/First.nam", None, "an absolute path"),
    (7, None, "the name of a file in amp/models"),
])
def test_a_model_that_is_not_a_playable_file_in_the_library_is_refused_before_carla_stops(root, model, code, said):
    agent, world, supervisor = model_agent(root)
    status, body = call(agent, "POST", "/amp/model", json={"model": model})
    assert status == 400 and said in body["reason"], body
    assert body.get("code") == code
    assert supervisor.calls == []


def test_a_path_is_taken_from_this_machine_only(root, tmp_path):
    outside = nam(tmp_path / "Elsewhere.nam", "Elsewhere")
    with pytest.raises(amp_live.AmpRefused, match="taken only from this machine"):
        amp_live.model_request({"model": str(outside)}, local=False, root=root, name_pattern=MODEL_NAME)
    assert amp_live.model_request({"model": str(outside)}, local=True, root=root, name_pattern=MODEL_NAME) == \
        (str(outside), outside)
    agent, world, supervisor = model_agent(root)
    status, body = call(agent, "POST", "/amp/model", json={"model": str(outside)})  # the test client is local
    assert status == 200 and supervisor.calls[0]["override"] == str(outside)


@pytest.mark.parametrize("change, code", [
    (lambda w: w.state.update(mode="panel"), "panel"),
    (lambda w: w.state["children"].pop("amp"), "no_amp"),
    (lambda w: setattr(w, "running", False), "no_rig"),
])
def test_a_model_change_is_refused_where_a_parameter_is(root, change, code):
    world = World(root)
    change(world)
    agent, world, supervisor = model_agent(root, world=world)
    status, body = call(agent, "POST", "/amp/model", json={"model": "Second.nam"})
    assert (status, body["code"]) == (409, code) and supervisor.calls == []


def test_a_model_change_needs_a_rig_this_agent_holds(root):
    agent, world = amp_agent(root)  # the real supervisor, holding nothing
    status, body = call(agent, "POST", "/amp/model", json={"model": "Second.nam"})
    assert (status, body["code"]) == (409, "not_held") and "up --direct" in body["reason"]


def test_a_model_change_is_busy_while_a_lifecycle_command_runs_and_busy_itself(root):
    agent, world, supervisor = model_agent(root)
    seen = {}

    async def slow(project, values, *, model, override):
        await asyncio.sleep(0.2)
        return AmpRestart(True, pid=99)

    supervisor.restart_amp = slow

    async def go():
        async with TestClient(TestServer(agent.app())) as client:
            change = asyncio.ensure_future(client.post("/amp/model", json={"model": "Second.nam"}))
            await asyncio.sleep(0.05)
            seen["health"] = await (await client.get("/health")).json()
            other = await client.post("/rig/down")
            knob = await client.post("/amp/parameter", json={"name": "Output Lvl", "value": 1.0})
            seen["other"] = (other.status, (await other.json())["code"])
            seen["knob"] = (knob.status, (await knob.json())["code"])
            return (await change).status

    assert asyncio.run(go()) == 200
    assert seen["health"]["agent"]["busy"] == "amp_model"
    assert seen["other"] == (409, "busy") and seen["knob"] == (409, "busy")


def test_a_restart_that_failed_says_whether_the_previous_model_came_back(root, capsys):
    result = AmpRestart(False, "Carla exited during startup (status 1) — see amp.log", pid=98, restored=True)
    agent, world, supervisor = model_agent(root, result)
    (status, body), (_, health), (_, log) = calls(
        agent, ("POST", "/amp/model", {"json": {"model": "Second.nam"}}), ("GET", "/health", {}),
        ("GET", "/live/log", {}))
    assert status == 502 and body["restored"] is True
    assert "the previous model was brought back" in body["reason"]
    assert "Carla exited during startup" in health["agent"]["last_error"]["message"]
    assert log["log"] == [], "nothing changed, so nothing is logged"
    assert call(agent, "GET", "/amp")[1]["model"]["name"] == "First"


def test_undo_of_a_model_change_restarts_carla_on_the_one_before(root):
    agent, world, supervisor = model_agent(root)
    _, (status, body) = calls(agent, ("POST", "/amp/model", {"json": {"model": "Second.nam"}}),
                              ("POST", "/live/undo", {}))
    assert status == 200, body
    assert body["op"] == "undo" and body["args"] == {"of": "amp_model", "model": "amp/models/First.nam"}
    assert body["undone"]["op"] == "amp_model" and body["state"] == {"model": "amp/models/First.nam"}
    assert [c["model"] for c in supervisor.calls] == ["amp/models/Second.nam", "amp/models/First.nam"]
    assert supervisor.calls[1]["override"] is None, "the rig's own model again"


def test_undo_of_a_model_change_is_stale_once_the_model_changed_again(root):
    world = World(root)
    agent, world, supervisor = model_agent(root, world=world)
    call(agent, "POST", "/amp/model", json={"model": "Second.nam"})
    world.state["amp_model"] = "amp/models/First.nam"  # a restart that dropped it
    world.state["started_at"] = "2026-11-02T11:00:00+00:00"
    status, body = call(agent, "POST", "/live/undo")
    assert (status, body["code"]) == (409, "stale") and len(supervisor.calls) == 1
    status, body = call(agent, "POST", "/live/undo", json={"skip": True})
    assert status == 200 and body["op"] == "stale"


def test_the_override_is_recorded_as_up_takes_it_and_put_back_by_name_or_path(root):
    assert amp_live.stored_model("Second.nam") == "amp/models/Second.nam"
    assert amp_live.stored_model("/m/x.nam") == "/m/x.nam"
    assert amp_live.override_of("amp/models/Second.nam", root) == "Second.nam"
    assert amp_live.override_of("/m/x.nam", root) == "/m/x.nam"
    assert amp_live.override_of("models/x.nam", root) == str(root / "models/x.nam")


# --------------------------------------------------------------------------- #
# The decisions around a Carla-only restart, pure
# --------------------------------------------------------------------------- #

def test_the_amps_project_is_read_off_the_plan_as_plan_children_builds_it(tmp_path):
    from sushi_rig.rig import amp_project_of, plan_children

    config = tmp_path / "c.json"
    with_amp = plan_children(tmp_path, config, None, headless=True, tuner=False, amp_project=tmp_path / "amp.carxp")
    assert amp_project_of(with_amp) == tmp_path / "amp.carxp"
    assert amp_project_of(plan_children(tmp_path, config, None, headless=True, tuner=False)) is None


def test_the_state_after_a_model_change_is_what_restart_brings_back(tmp_path):
    from sushi_rig.rig import amp_restarted_state, restart_arguments

    state = {"config_name": "board", "children": {"sushi": 1, "amp": 2}, "amp_model": "amp/models/A.nam",
             "amp_model_requested": None, "amp_requested": True, "mode": "headless", "rig": "config/rigs/board.yaml"}
    after = amp_restarted_state(state, model="amp/models/B.nam", override="B.nam", pid=7)
    assert after["children"] == {"sushi": 1, "amp": 7} and state["children"]["amp"] == 2, "a copy"
    assert (after["amp_model"], after["amp_model_requested"]) == ("amp/models/B.nam", "B.nam")
    assert restart_arguments(after) == ("board", {"headless": True, "tuner": False, "amp": True,
                                                  "amp_model": "B.nam", "rig": True})


def test_a_planned_child_is_started_with_its_own_environment_and_log(tmp_path):
    from sushi_rig.rig import start_child

    asked = {}

    def spawn(command, *, cwd, env, log):
        asked.update(command=command, cwd=cwd, env=env, log=log)
        return "proc"

    child = {"name": "amp", "command": ["pw-jack", "carla", "-n", "p"], "optional": True,
             "env_set": {"PIPEWIRE_QUANTUM": "512/48000"}}
    assert start_child(child, root=tmp_path, env={"PIPEWIRE_QUANTUM": "64/48000", "X": "1"}, logs=tmp_path,
                       spawn=spawn) == ("proc", tmp_path / "amp.log")
    assert asked["command"] == ["pw-jack", "carla", "-n", "p"]
    assert asked["env"] == {"PIPEWIRE_QUANTUM": "512/48000", "X": "1"}, "the child's own quantum wins"


def test_a_seed_is_what_replay_sends_and_a_run_is_its_group_start_and_config():
    state = {"pgid": 5, "started_at": "t", "config": "/c.json", "amp_model": "amp/models/A.nam"}
    seeded = amp_live.seed(state, CONFIG)
    assert seeded.run == (5, "t", "/c.json") and seeded.model == "amp/models/A.nam"
    assert seeded.sent() == {"Input Lvl": -2.0, "Output Lvl": 6.0, "Quality": None}
    assert seeded.sources == {"Input Lvl": "replayed", "Output Lvl": "replayed"}
    assert amp_live.seed(state, None).sent() == {"Input Lvl": None, "Output Lvl": None, "Quality": None}
    assert amp_live.seed(state, {"_amp": {"parameters": "garbage"}}).values == {}


@pytest.mark.parametrize("asked, sent, said", [
    (3.0, 3.0, None), (-25, -20.0, "Output Lvl -25 is outside -20 to 20 dB, so -20 was sent"),
])
def test_a_parameter_request_is_clamped_and_the_clamp_said(asked, sent, said):
    name, got_asked, got_sent = amp_live.parameter_request({"name": "Output Lvl", "value": asked})
    assert (name, got_asked, got_sent) == ("Output Lvl", asked, sent)
    assert amp_live.clamped_said(name, got_asked, got_sent) == said
