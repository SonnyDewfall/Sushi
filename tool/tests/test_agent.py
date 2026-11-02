"""The rig agent (#29), first slice: lifecycle and observation over HTTP.

The routes are tested against fakes for everything that touches the world --
the state file, the examination, the child process -- so these say what the
agent *decides*: what runs for a request, what is refused before anything
runs, what one failure does to the next request. The runner the agent uses by
default is its own supervisor, whose lifecycle is tested with real (if fake)
child processes in test_supervisor.py, because a runner that only ever met a
fake would be the mock agreeing with itself.
"""

from __future__ import annotations

import asyncio
import sys
import threading
from dataclasses import replace
from pathlib import Path

import pytest

aiohttp = pytest.importorskip("aiohttp")
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from sushi_rig import agent as agent_module  # noqa: E402
from sushi_rig.live import decide_move, plan_move  # noqa: E402
from sushi_rig.agent import (  # noqa: E402
    Agent,
    AgentError,
    Live,
    Outcome,
    StaleUndo,
    bypass_request,
    check_bypass,
    check_move,
    chain_after_move,
    check_parameter,
    check_undo_move,
    check_undo_state,
    lifecycle_arguments,
    log_entry,
    move_request,
    parameter_request,
    pick_undo,
    trim_log,
    undo_inverse,
    undo_request,
)
from sushi_rig.doctor import FAULT, OK, UNCHECKED, Check  # noqa: E402

DOWN = {"verdict": "absent", "running": False, "message": "No rig is running."}
UP = {
    "verdict": "running", "running": True, "config_name": "electric_board",
    "mode": "panel", "message": "electric_board (panel mode), up 2m",
}


class FakeRunner:
    """Records what the agent asked to run and answers as it is told."""

    def __init__(self, outcome=Outcome(True, 0, "started\n", "")):
        self.calls = []
        self.outcome = outcome

    async def __call__(self, args):
        self.calls.append(list(args))
        return self.outcome


@pytest.fixture(autouse=True)
def _agents_release_their_pools(monkeypatch):
    """Each Agent owns a pool of Sushi-call threads; left alive after a test
    they make a later test that forks (the supervisor's) fork a threaded
    process, which Python warns about."""
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
    """One request against a served agent, as (status, json)."""

    async def go():
        async with TestClient(TestServer(agent.app())) as client:
            response = await client.request(method, path, **kwargs)
            return response.status, await response.json()

    return asyncio.run(go())


# --------------------------------------------------------------------------- #
# What runs for a request
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("command, body, expected", [
    ("up", {"config_name": "guitar_vocal"}, ["up", "guitar_vocal"]),
    ("up", {"config_name": "electric_board", "headless": True, "tuner": False, "amp": False},
     ["up", "electric_board", "--headless", "--no-tuner", "--no-amp"]),
    ("up", {"config_name": "electric_board", "amp_model": "Some Amp.nam"},
     ["up", "electric_board", "--amp-model", "Some Amp.nam"]),
    ("down", None, ["down"]),
    ("down", {"force": True}, ["down", "--force"]),
    ("restart", {"anything": "ignored"}, ["restart"]),
])
def test_each_request_is_one_fixed_command(command, body, expected):
    assert lifecycle_arguments(command, body) == expected


@pytest.mark.parametrize("body", [
    {"config_name": "../etc/passwd"},
    {"config_name": "config/x"},
    {"config_name": ""},
    {"config_name": "-rf"},
    {"config_name": "guitar_vocal\n"},
    {"config_name": 7},
    {"config_name": "electric_board", "headless": "yes"},
    {"config_name": "electric_board", "amp_model": ""},
    {"config_name": "electric_board", "amp_model": "--help"},
    {"config_name": "electric_board", "amp_model": "/etc/passwd"},
    {"config_name": "electric_board", "amp_model": "../../.env"},
    {"config_name": "electric_board", "amp_model": "amp/models/M.nam"},
    {"config_name": "electric_board", "amp_model": "M.nam\n"},
    ["not", "an", "object"],
    [],
    0,
    "",
])
def test_an_argument_that_is_not_what_it_claims_is_refused(body):
    """A name is a name, not a path or a flag; a flag is a boolean. An amp
    model is the name of a file in amp/models, which `up` resolves there, not
    a path for a LAN client to read the machine with."""
    with pytest.raises(AgentError):
        lifecycle_arguments("up", body)


def test_an_amp_model_name_may_look_like_one():
    assert lifecycle_arguments("up", {"config_name": "electric_board", "amp_model": "Marshall JCM800 (crunch) v2.nam"})[-1] == \
        "Marshall JCM800 (crunch) v2.nam"


def test_a_rig_of_channels_is_asked_for_by_flag():
    """`--rig` makes the name config/rigs/<name>.yaml; the name check is the
    same, so a path cannot reach the CLI through it either."""
    assert lifecycle_arguments("up", {"config_name": "guitar_vocal", "rig": True}) == \
        ["up", "guitar_vocal", "--rig"]
    assert lifecycle_arguments("up", {"config_name": "guitar_vocal", "rig": False}) == \
        ["up", "guitar_vocal"]
    with pytest.raises(AgentError):
        lifecycle_arguments("up", {"config_name": "../x", "rig": True})
    with pytest.raises(AgentError):
        lifecycle_arguments("up", {"config_name": "guitar_vocal", "rig": "yes"})


def test_only_the_three_lifecycle_commands_exist():
    with pytest.raises(AgentError):
        lifecycle_arguments("shell", {})


# --------------------------------------------------------------------------- #
# The routes
# --------------------------------------------------------------------------- #

def test_health_reports_the_agent_and_the_rig_in_one_answer():
    ticks = iter([100.0, 103.5])
    agent = Agent(describe=lambda: dict(UP), clock=lambda: next(ticks))
    status, body = call(agent, "GET", "/health")
    assert status == 200
    assert body["ok"] is True
    assert body["rig"] == UP
    assert body["agent"]["uptime_seconds"] == 3.5
    assert body["agent"]["busy"] is None
    assert body["agent"]["last_error"] is None


def test_rig_is_what_status_would_say_as_data():
    assert call(Agent(describe=lambda: dict(DOWN)), "GET", "/rig") == (200, DOWN)


def test_up_runs_the_cli_and_reports_what_it_said():
    runner = FakeRunner()
    agent = Agent(describe=lambda: dict(UP), run=runner)
    status, body = call(agent, "POST", "/rig/up", json={"config_name": "guitar_vocal", "headless": True})
    assert status == 200
    assert runner.calls == [["up", "guitar_vocal", "--headless"]]
    assert body["ok"] is True
    assert body["command"] == ["up", "guitar_vocal", "--headless"]
    assert body["output"] == "started\n"
    assert "reason" not in body
    assert body["rig"] == UP, "the answer carries the rig as it is after the command"


@pytest.mark.parametrize("body", [None, {}, {"headless": True}])
def test_up_without_a_config_name_is_refused(body):
    """A stray body-less POST must not start a rig nobody asked for."""
    with pytest.raises(AgentError, match="config_name is required: the name of a file in config/"):
        lifecycle_arguments("up", body)


def test_an_up_with_no_body_is_a_bad_request_and_runs_nothing():
    runner = FakeRunner()
    status, body = call(Agent(describe=lambda: dict(DOWN), run=runner), "POST", "/rig/up")
    assert status == 400
    assert "config_name is required" in body["reason"]
    assert runner.calls == []


def test_a_down_with_no_body_uses_the_defaults():
    runner = FakeRunner()
    status, _ = call(Agent(describe=lambda: dict(DOWN), run=runner), "POST", "/rig/down")
    assert status == 200
    assert runner.calls == [["down"]]


def test_a_bad_argument_is_refused_before_anything_runs():
    runner = FakeRunner()
    agent = Agent(describe=lambda: dict(DOWN), run=runner)
    status, body = call(agent, "POST", "/rig/up", json={"config_name": "../x"})
    assert status == 400
    assert "invalid config name" in body["reason"]
    assert runner.calls == []


def test_a_body_that_is_not_json_is_refused():
    runner = FakeRunner()
    agent = Agent(describe=lambda: dict(DOWN), run=runner)
    status, body = call(agent, "POST", "/rig/up", data=b"{not json",
                        headers={"Content-Type": "application/json"})
    assert status == 400
    assert "not JSON" in body["reason"]
    assert runner.calls == []


def test_a_command_the_cli_refuses_is_a_conflict_and_is_remembered():
    """`up` on a running rig, `restart` with none: the CLI's own words come
    back, and health carries them until something else goes wrong."""
    refused = Outcome(False, 1, "", "a rig is already running: electric_board (panel mode), up 2m.\n")
    agent = Agent(describe=lambda: dict(UP), run=FakeRunner(refused))
    status, body = call(agent, "POST", "/rig/up", json={"config_name": "electric_board"})
    assert status == 409
    assert body["ok"] is False
    assert body["reason"].startswith("a rig is already running")

    _, health = call(agent, "GET", "/health")
    assert health["ok"] is True, "the agent is fine; the rig refused"
    assert health["agent"]["last_error"]["command"] == ["up", "electric_board"]
    assert health["agent"]["last_error"]["message"].startswith("a rig is already running")


def test_a_success_clears_the_last_error():
    """Otherwise one refused request is reported by a healthy agent forever."""
    refused = Outcome(False, 1, "", "a rig is already running: electric_board.\n")
    runner = FakeRunner(refused)
    agent = Agent(describe=lambda: dict(UP), run=runner)
    status, _ = call(agent, "POST", "/rig/up", json={"config_name": "electric_board"})
    assert status == 409
    _, health = call(agent, "GET", "/health")
    assert health["agent"]["last_error"] is not None

    runner.outcome = Outcome(True, 0, "stopped\n", "")
    status, _ = call(agent, "POST", "/rig/down")
    assert status == 200
    _, health = call(agent, "GET", "/health")
    assert health["agent"]["last_error"] is None


def test_stopping_says_so_and_whether_a_command_was_running(capsys):
    """SIGTERM stops aiohttp silently; this line is how a log tells a handled
    stop from a kill. TestServer runs on_shutdown when it closes, as run_app
    does on a signal."""
    agent = Agent(describe=lambda: dict(DOWN))
    app = agent.app()
    app.on_shutdown.append(agent.announce_stopping)

    async def go():
        async with TestClient(TestServer(app)):
            pass

    asyncio.run(go())
    assert "sushi-rig agent stopping (no lifecycle command running)" in capsys.readouterr().out

    agent.busy = "up"
    asyncio.run(agent.announce_stopping())
    assert "sushi-rig agent stopping ('up' still running)" in capsys.readouterr().out


def test_one_lifecycle_command_at_a_time():
    """`up` and `down` racing is a second rig on top of a running one."""
    started, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def slow(args):
        calls.append(list(args))
        started.set()
        await release.wait()
        return Outcome(True, 0, "", "")

    agent = Agent(describe=lambda: dict(DOWN), run=slow)

    async def go():
        async with TestClient(TestServer(agent.app())) as client:
            first = asyncio.ensure_future(client.post("/rig/up", json={"config_name": "electric_board"}))
            await started.wait()
            second = await client.post("/rig/down")
            assert second.status == 409
            assert "busy: 'up' is still running" in (await second.json())["reason"]
            health = await (await client.get("/health")).json()
            assert health["agent"]["busy"] == "up"
            release.set()
            assert (await first).status == 200
            # And the lock is released: the next one runs.
            assert (await client.post("/rig/down")).status == 200

    asyncio.run(go())
    assert calls == [["up", "electric_board"], ["down"]]


def test_a_command_the_agent_gave_up_on_says_what_to_read_next():
    """The front process was killed; what it started may well be running."""
    abandoned = Outcome(False, None, "", "'up electric_board' did not finish within 120s, "
                        "so the agent stopped waiting for it. What it started, if anything, "
                        "is still running: read /rig.")
    agent = Agent(describe=lambda: dict(UP), run=FakeRunner(abandoned))
    status, body = call(agent, "POST", "/rig/up", json={"config_name": "electric_board"})
    assert status == 504
    assert "read /rig" in body["reason"]
    assert body["rig"]["running"] is True, "and the answer already carries it"


def test_a_failure_the_route_did_not_expect_is_json_and_remembered():
    """A plain-text 500 and a log line is what aiohttp does on its own; a
    phone would see neither."""
    def broken():
        raise OSError("state file unreadable")

    agent = Agent(describe=broken)
    status, body = call(agent, "GET", "/rig")
    assert status == 500
    assert body == {"ok": False, "status": 500, "reason": "OSError: state file unreadable"}
    agent.describe = lambda: dict(DOWN)
    _, health = call(agent, "GET", "/health")
    assert health["agent"]["last_error"]["message"] == "OSError: state file unreadable"
    assert health["agent"]["last_error"]["command"] == ["GET", "/rig"]


def test_a_body_that_is_not_text_is_a_bad_request_not_a_crash():
    runner = FakeRunner()
    agent = Agent(describe=lambda: dict(DOWN), run=runner)
    status, body = call(agent, "POST", "/rig/up", data=b"\xff\xfe\x00{",
                        headers={"Content-Type": "application/json"})
    assert status == 400
    assert "not JSON" in body["reason"]
    assert runner.calls == []


def test_a_lifecycle_success_does_not_forget_an_unrelated_crash():
    """A broken /doctor says nothing about the rig coming up, so a `down` that
    works must not clear it; only a lifecycle failure is superseded."""
    def broken():
        raise OSError("pw-dump missing")

    agent = Agent(describe=lambda: dict(DOWN), doctor=broken, run=FakeRunner())
    status, _ = call(agent, "GET", "/doctor")
    assert status == 500
    status, _ = call(agent, "POST", "/rig/down")
    assert status == 200
    _, health = call(agent, "GET", "/health")
    assert health["agent"]["last_error"]["message"] == "OSError: pw-dump missing"


def test_a_route_that_does_not_exist_is_json_too():
    status, body = call(Agent(describe=lambda: dict(DOWN)), "GET", "/shell")
    assert status == 404
    assert body["ok"] is False


def test_doctor_serves_the_checks_as_data():
    checks = [
        Check("pw-jack", OK, "pw-jack found"),
        Check("quantum", FAULT, "node.force-quantum is 2048", "ask for 256"),
        Check("clock-driver", UNCHECKED, "no PipeWire", "start the rig"),
    ]
    agent = Agent(describe=lambda: dict(DOWN), doctor=lambda: checks)
    status, body = call(agent, "GET", "/doctor")
    assert status == 200
    assert body["ok"] is False
    assert (body["faults"], body["unchecked"]) == (1, 1)
    assert body["checks"][1] == {
        "name": "quantum", "state": "fault",
        "message": "node.force-quantum is 2048", "remedy": "ask for 256",
    }


# --------------------------------------------------------------------------- #
# Live operations: the decisions
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("body, expected", [
    ({"processor": "reverb", "parameter": "Mix", "value": 0.25}, ("reverb", "Mix", 0.25)),
    ({"processor": "reverb", "parameter": "Mix", "value": 0}, ("reverb", "Mix", 0.0)),
    ({"processor": "reverb", "parameter": "Mix", "value": 1}, ("reverb", "Mix", 1.0)),
])
def test_a_parameter_request_is_a_name_a_name_and_a_normalised_number(body, expected):
    result = parameter_request(body)
    assert result == expected
    assert isinstance(result[2], float)


@pytest.mark.parametrize("body", [
    None, [], "x", 3,
    {"parameter": "Mix", "value": 0.5},
    {"processor": "reverb", "value": 0.5},
    {"processor": "reverb", "parameter": "Mix"},
    {"processor": "", "parameter": "Mix", "value": 0.5},
    {"processor": 7, "parameter": "Mix", "value": 0.5},
    {"processor": "reverb", "parameter": ["Mix"], "value": 0.5},
    {"processor": "reverb", "parameter": "Mix", "value": "0.5"},
    {"processor": "reverb", "parameter": "Mix", "value": None},
    {"processor": "reverb", "parameter": "Mix", "value": True},
    {"processor": "reverb", "parameter": "Mix", "value": 1.0001},
    {"processor": "reverb", "parameter": "Mix", "value": -0.0001},
    {"processor": "reverb", "parameter": "Mix", "value": 12},
    {"processor": "reverb", "parameter": "Mix", "value": float("nan")},
    {"processor": "reverb", "parameter": "Mix", "value": float("inf")},
])
def test_a_parameter_request_that_is_not_that_is_refused(body):
    with pytest.raises(AgentError):
        parameter_request(body)


def test_bypass_and_move_requests():
    assert bypass_request({"processor": "drive", "bypassed": False}) == ("drive", False)
    assert move_request({"processor": "drive", "direction": "down"}) == ("drive", "down")
    for body in [{"processor": "drive"}, {"processor": "drive", "bypassed": 1},
                 {"processor": "drive", "bypassed": "true"}, {"bypassed": True}, None]:
        with pytest.raises(AgentError):
            bypass_request(body)
    for body in [{"processor": "drive"}, {"processor": "drive", "direction": "left"},
                 {"processor": "drive", "direction": -1}, {"processor": "drive", "direction": "UP"},
                 {"direction": "up"}, []]:
        with pytest.raises(AgentError):
            move_request(body)


# The rig the live tests run against: one track "main" carrying comp, drive and
# reverb. `INFO` is what each has to set; `BYPASS` what Sushi says of each
# (None: it would not say).
CHAINS = {"main": ["comp", "drive", "reverb"]}
INFO = {
    "reverb": {
        "Mix": {"automatable": True, "value": 0.3, "min_domain_value": 0.0, "max_domain_value": 1.0},
        "Latency OUT": {"automatable": False, "value": 0.0, "min_domain_value": 0.0, "max_domain_value": 1.0},
    },
    "main": {"gain": {"automatable": True, "value": 0.8, "min_domain_value": 0.0, "max_domain_value": 1.0}},
    "comp": {}, "drive": {},
}
BYPASS = {"comp": False, "drive": True, "reverb": None}


def parameter_reading(processor, parameter):
    """What `live.read_live_parameter` would say of `INFO`."""
    parameters = INFO.get(processor)
    value = None
    if parameters is not None and parameter in parameters and parameters[parameter]["automatable"]:
        value = parameters[parameter]["value"]
    return {
        "chains": CHAINS,
        "parameters": None if parameters is None else
        {n: {"automatable": p["automatable"]} for n, p in parameters.items()},
        "value": value,
    }


def test_a_parameter_the_rig_lacks_or_cannot_set_is_refused_naming_what_it_has():
    assert check_parameter(parameter_reading("reverb", "Mix"), "reverb", "Mix") == 0.3, \
        "and says what it replaces"
    assert check_parameter(parameter_reading("main", "gain"), "main", "gain") == 0.8, \
        "a track has parameters too"
    with pytest.raises(AgentError, match=r"no processor 'delay'.*\['comp', 'drive', 'main', 'reverb'\]"):
        check_parameter(parameter_reading("delay", "Mix"), "delay", "Mix")
    with pytest.raises(AgentError, match=r"no parameter 'Tone'.*'Latency OUT', 'Mix'"):
        check_parameter(parameter_reading("reverb", "Tone"), "reverb", "Tone")
    with pytest.raises(AgentError, match="read-only"):
        check_parameter(parameter_reading("reverb", "Latency OUT"), "reverb", "Latency OUT")


def bypass_reading(processor):
    return {"chains": CHAINS, "bypassed": BYPASS.get(processor)}


def test_bypass_is_for_a_processor_of_the_rig_not_a_track():
    assert check_bypass(bypass_reading("drive"), "drive") is True
    assert check_bypass(bypass_reading("comp"), "comp") is False
    assert check_bypass(bypass_reading("reverb"), "reverb") is None, \
        "Sushi would not say: unknown, not False"
    with pytest.raises(AgentError, match="is a track"):
        check_bypass(bypass_reading("main"), "main")
    with pytest.raises(AgentError, match="no processor 'nope'"):
        check_bypass(bypass_reading("nope"), "nope")


def test_the_hint_for_a_bypass_names_only_what_can_be_bypassed():
    """The track is refused one line above; offering it would send the player
    straight back into the refusal."""
    with pytest.raises(AgentError) as caught:
        check_bypass(bypass_reading("nope"), "nope")
    assert str(caught.value).endswith("it has ['comp', 'drive', 'reverb']")


IDS = {"main": 1, "comp": 2, "drive": 3, "reverb": 4}


def planned(processor, direction):
    """What a walk of `CHAINS` decides for a request, as the agent gets it."""
    return decide_move(CHAINS, IDS, processor, agent_module.DIRECTIONS[direction])


@pytest.mark.parametrize("processor, direction, outcome", [
    ("drive", "up", "planned"), ("drive", "down", "planned"),
    ("comp", "up", "unchanged"), ("reverb", "down", "unchanged"),
])
def test_a_move_at_the_end_of_the_chain_is_not_a_change(processor, direction, outcome):
    move = planned(processor, direction)
    check_move(move)  # not a refusal
    assert move.outcome == outcome
    assert move.chain == ["comp", "drive", "reverb"]


def test_a_move_of_what_is_on_no_track_is_refused():
    for name in ("main", "nope"):
        with pytest.raises(AgentError, match="on any track"):
            check_move(planned(name, "up"))


def test_the_names_a_refusal_offers_are_the_rigs_with_or_without_its_tracks():
    from sushi_rig.agent import _on_the_rig

    chains = {"main": ["drive", "comp"], "aux": ["comp", "delay"]}
    assert _on_the_rig(chains) == ["aux", "comp", "delay", "drive", "main"]
    assert _on_the_rig(chains, with_tracks=False) == ["comp", "delay", "drive"]


def test_a_log_entry_always_has_the_same_four_keys():
    assert log_entry("bypass", {"processor": "drive", "bypassed": True}, None, "t") == {
        "at": "t", "op": "bypass", "args": {"processor": "drive", "bypassed": True}, "previous": None,
    }


def test_the_log_keeps_the_newest_in_place():
    log = list(range(10))
    trim_log(log, 4)
    assert log == [6, 7, 8, 9]
    trim_log(log, 4)
    assert log == [6, 7, 8, 9]
    trim_log(log, 0)
    assert log == []


# --------------------------------------------------------------------------- #
# Undo (#29): the decisions, pure
# --------------------------------------------------------------------------- #

def _entry(op, args, previous, **extra):
    return {**log_entry(op, args, previous, "t", **extra)}


def test_the_newest_change_not_already_undone_is_picked_with_its_index():
    a = _entry("parameter", {"processor": "reverb", "parameter": "Mix", "value": 0.6}, 0.3)
    b = _entry("bypass", {"processor": "drive", "bypassed": False}, True)
    c = _entry("move", {"processor": "drive", "direction": "up"}, ["comp", "drive", "reverb"])
    assert pick_undo([a, b, c], 0) == (2, c)
    assert pick_undo([a, b, c], 10) == (12, c), "the index counts entries trimmed away too"
    undo_c = _entry("undo", {}, None, undoes=2)
    assert pick_undo([a, b, c, undo_c], 0) == (1, b), "an undone entry is passed over"
    undo_b = _entry("undo", {}, None, undoes=1)
    assert pick_undo([a, b, c, undo_c, undo_b], 0) == (0, a)


def test_an_entry_marked_stale_is_passed_over_like_an_undone_one():
    a = _entry("parameter", {"processor": "reverb", "parameter": "Mix", "value": 0.6}, 0.3)
    b = _entry("move", {"processor": "drive", "direction": "up"}, ["comp", "drive", "reverb"])
    stale_b = _entry("stale", {"of": "move"}, None, stale=1)
    assert pick_undo([a, b, stale_b], 0) == (0, a)
    assert pick_undo([a, b, stale_b, _entry("stale", {"of": "parameter"}, None, stale=0)], 0) is None


def test_a_rig_switch_is_a_barrier_nothing_before_it_is_picked():
    """A switch by Program Change brings up another rig, which may have
    processors of the same names: an older entry undone after it would change
    the new rig. Newer entries are still picked."""
    from sushi_rig.agent import UndoBarrier

    a = _entry("bypass", {"processor": "drive", "bypassed": False}, True)
    switch = _entry("rig", {"rig": "lead", "program": 2}, "guitar_vocal")
    b = _entry("bypass", {"processor": "drive", "bypassed": True}, False)
    assert pick_undo([a, switch, b], 0) == (2, b)
    with pytest.raises(UndoBarrier, match="rig switch"):
        pick_undo([a, switch], 0)
    with pytest.raises(UndoBarrier, match="rig switch"):
        pick_undo([a, switch, b, _entry("undo", {}, None, undoes=2)], 0)


def test_there_is_nothing_to_pick_in_an_empty_log_or_one_of_undos_only():
    assert pick_undo([], 0) is None
    a = _entry("parameter", {"processor": "reverb", "parameter": "Mix", "value": 0.6}, 0.3)
    assert pick_undo([a, _entry("undo", {}, None, undoes=0)], 0) is None
    # The entry an undo undid has been trimmed away; the undo, newer, remains.
    assert pick_undo([_entry("undo", {}, None, undoes=0)], 1) is None


def test_an_undo_is_the_inverse_of_the_entry_through_the_routes_own_arguments():
    parameter = _entry("parameter", {"processor": "reverb", "parameter": "Mix", "value": 0.6}, 0.3)
    assert undo_inverse(parameter) == (
        "parameter", {"processor": "reverb", "parameter": "Mix", "value": 0.3})
    bypass = _entry("bypass", {"processor": "drive", "bypassed": False}, True)
    assert undo_inverse(bypass) == ("bypass", {"processor": "drive", "bypassed": True})
    up = _entry("move", {"processor": "drive", "direction": "up"}, ["comp", "drive", "reverb"])
    assert undo_inverse(up) == ("move", {"processor": "drive", "direction": "down"})
    down = _entry("move", {"processor": "drive", "direction": "down"}, ["comp", "drive", "reverb"])
    assert undo_inverse(down)[1]["direction"] == "up"


def test_an_entry_whose_previous_is_not_known_is_refused_not_guessed():
    unknown = _entry("bypass", {"processor": "reverb", "bypassed": True}, None)
    with pytest.raises(StaleUndo, match="did not say what reverb was before"):
        undo_inverse(unknown)


def test_an_entry_of_an_op_the_agent_does_not_know_is_refused():
    with pytest.raises(AgentError, match="cannot undo"):
        undo_inverse(_entry("rename", {}, "x"))


@pytest.mark.parametrize("chain, processor, direction, expected", [
    (["comp", "drive", "reverb"], "drive", "up", ["drive", "comp", "reverb"]),
    (["comp", "drive", "reverb"], "drive", "down", ["comp", "reverb", "drive"]),
    (["comp", "drive", "reverb"], "comp", "up", None),
    (["comp", "drive", "reverb"], "reverb", "down", None),
    (["comp", "drive", "reverb"], "delay", "up", None),
])
def test_where_a_chain_stands_after_a_move(chain, processor, direction, expected):
    assert chain_after_move(chain, processor, direction) == expected
    assert chain == ["comp", "drive", "reverb"], "the chain given is not changed"


def test_a_move_is_undone_only_where_the_chain_still_is_what_the_move_left():
    up = _entry("move", {"processor": "drive", "direction": "up"}, ["comp", "drive", "reverb"])
    check_undo_move(up, ["drive", "comp", "reverb"])  # not a refusal
    for now in (["comp", "drive", "reverb"], ["drive", "reverb", "comp"], ["drive", "comp"], None):
        with pytest.raises(AgentError, match="no longer where the move left it"):
            check_undo_move(up, now)


@pytest.mark.parametrize("body", [None, {}, {"skip": False}])
def test_an_undo_request_is_empty_or_says_no_skip(body):
    assert undo_request(body) is False


def test_an_undo_request_may_ask_to_skip_the_newest_entry_it_cannot_undo():
    assert undo_request({"skip": True}) is True


@pytest.mark.parametrize("body", [
    {"index": 3}, [], "undo", 3, {"processor": "drive"},
    {"skip": "yes"}, {"skip": 1}, {"skip": True, "index": 1},
])
def test_an_undo_request_that_says_anything_is_refused_not_ignored(body):
    """It undoes the newest and nothing else; a body that asks for more would
    otherwise be answered as if it had been understood."""
    with pytest.raises(AgentError, match="empty"):
        undo_request(body)


@pytest.mark.parametrize("entry, current", [
    (_entry("parameter", {"processor": "reverb", "parameter": "Mix", "value": 0.6}, 0.3), 0.6),
    (_entry("parameter", {"processor": "reverb", "parameter": "Mix", "value": 0.6}, 0.3), 0.6000000238418579),
    (_entry("bypass", {"processor": "drive", "bypassed": False}, True), False),
])
def test_a_parameter_or_bypass_is_undone_only_where_the_change_left_it(entry, current):
    check_undo_state(entry, current)  # not a refusal


@pytest.mark.parametrize("entry, current", [
    (_entry("parameter", {"processor": "reverb", "parameter": "Mix", "value": 0.6}, 0.3), 0.7),
    (_entry("parameter", {"processor": "reverb", "parameter": "Mix", "value": 0.6}, 0.3), None),
    (_entry("bypass", {"processor": "drive", "bypassed": False}, True), True),
    (_entry("bypass", {"processor": "drive", "bypassed": False}, True), None),
])
def test_a_value_changed_since_is_a_stale_undo_and_so_is_one_nobody_can_read(entry, current):
    with pytest.raises(StaleUndo, match="no longer as the change left it"):
        check_undo_state(entry, current)


@pytest.mark.parametrize("chain", [["a"], ["a", "b"], ["a", "b", "c"], ["a", "b", "c", "d"]])
@pytest.mark.parametrize("direction", ["up", "down"])
def test_the_opposite_step_always_puts_a_chain_back(chain, direction):
    """Why the undo of a move needs no "nothing to move" branch: wherever a
    one-step move was possible, its opposite from the result is possible and
    restores the chain, so a chain that passed `check_undo_move` always plans."""
    for processor in chain:
        after = chain_after_move(chain, processor, direction)
        if after is None:
            continue
        opposite = agent_module.OPPOSITE[direction]
        assert plan_move(after, processor, agent_module.DIRECTIONS[opposite]) is not None
        assert chain_after_move(after, processor, opposite) == chain


# --------------------------------------------------------------------------- #
# Live operations: the routes, against a fake Sushi
# --------------------------------------------------------------------------- #

class FakeLive:
    """A Sushi that records what it was asked and answers from a state.

    A request makes one connection and does everything on it: `connect` hands
    out a numbered connection, and every other call takes it and fails the test
    if it is not the open one. `calls` is what was asked of Sushi (not the
    connecting), `opened` and `closed` the connections.
    """

    def __init__(self):
        self.calls = []
        self.opened = []
        self.closed = []
        self.threads = set()
        self.info = {p: {n: dict(v) for n, v in ps.items()} for p, ps in INFO.items()}
        self.chain = list(CHAINS["main"])
        self.bypassed = dict(BYPASS)
        self.fail = None  # an exception to raise from every call
        self.fail_on = None  # ... or only from this one
        self.during = None  # called inside set_parameter
        self.hang_on = None  # a call to block in until `release` is set
        self.hang_times = 1  # ... for this many calls, then it answers at once
        self.thread_names = set()
        self.reached = threading.Event()  # set when the hanging call is in
        self.release = threading.Event()

    def _fail(self, name):
        if self.fail is not None and self.fail_on in (None, name):
            raise self.fail

    def _enter(self, name, connection, *args):
        assert connection in self.opened and connection not in self.closed, \
            f"{name} was not given the request's open connection"
        self.calls.append((name, *args))
        self.threads.add(threading.get_ident())
        self.thread_names.add(threading.current_thread().name)
        self._fail(name)
        self._hang(name)

    def _hang(self, name):
        if name == self.hang_on and self.hang_times > 0:
            self.hang_times -= 1  # the next request is not stuck too
            self.reached.set()
            self.release.wait(10)

    def connect(self):
        self.threads.add(threading.get_ident())
        self.thread_names.add(threading.current_thread().name)
        self._fail("connect")
        self._hang("connect")
        connection = len(self.opened) + 1
        self.opened.append(connection)
        return connection

    def disconnect(self, connection):
        self.closed.append(connection)

    def snapshot(self, connection):
        self._enter("snapshot", connection)
        return {
            "processors": {"reverb": {"parameters": {"Mix": 0.3}}},
            "tracks": {"main": list(self.chain)},
            "parameter_info": self.info,
        }

    def parameter(self, connection, processor, parameter):
        self._enter("parameter", connection, processor, parameter)
        parameters = self.info.get(processor)
        value = None
        if parameters is not None and parameter in parameters and parameters[parameter]["automatable"]:
            value = parameters[parameter]["value"]
        return {
            "chains": {"main": list(self.chain)},
            "parameters": None if parameters is None else
            {n: {"automatable": p["automatable"]} for n, p in parameters.items()},
            "value": value,
        }

    def bypass(self, connection, processor):
        self._enter("bypass", connection, processor)
        return {"chains": {"main": list(self.chain)}, "bypassed": self.bypassed.get(processor)}

    def set_parameter(self, connection, processor, parameter, value):
        self._enter("set_parameter", connection, processor, parameter, value)
        if self.during:
            self.during()
        self.info[processor][parameter]["value"] = value

    def set_bypass(self, connection, processor, bypassed):
        self._enter("set_bypass", connection, processor, bypassed)
        self.bypassed[processor] = bypassed

    def plan_move(self, connection, processor, direction):
        self._enter("plan_move", connection, processor, direction)
        ids = {"main": 1, **{name: 2 + i for i, name in enumerate(self.chain)}}
        return decide_move({"main": list(self.chain)}, ids, processor, direction)

    def send_move(self, connection, move):
        self._enter("move", connection, move.processor, move.direction)
        self.sent = move
        i = self.chain.index(move.processor)
        self.chain.insert(i + move.direction, self.chain.pop(i))
        return replace(move, outcome="moved")

    def writes(self):
        return [c for c in self.calls if c[0] in ("set_parameter", "set_bypass", "move")]

    def one_connection_per_request_all_closed(self):
        return sorted(self.closed) == self.opened


def live_agent(fake=None, **kwargs):
    fake = fake or FakeLive()
    kwargs.setdefault("describe", lambda: dict(UP))
    return Agent(live=fake, **kwargs), fake


def test_live_serves_the_running_rig_with_each_parameters_range():
    agent, fake = live_agent()
    status, body = call(agent, "GET", "/live")
    assert status == 200
    assert body["ok"] is True
    assert body["processors"] == {"reverb": {"parameters": {"Mix": 0.3}}}
    assert body["tracks"] == CHAINS
    assert body["parameter_info"]["reverb"]["Mix"] == INFO["reverb"]["Mix"], \
        "range and unit come with it"
    assert fake.writes() == []
    assert fake.calls == [("snapshot",)], "one read of the rig, not two"
    assert fake.threads and threading.get_ident() not in fake.threads, \
        "gRPC blocks, so it runs off the event loop"


def test_live_with_no_rig_up_is_unavailable_and_says_why():
    elkpy_errors = pytest.importorskip("elkpy.sushierrors")
    agent, fake = live_agent(describe=lambda: dict(DOWN))
    fake.fail = elkpy_errors.SushiUnavailableError("failed to connect", "")
    status, body = call(agent, "GET", "/live")
    assert status == 503
    assert body["ok"] is False
    assert "not reachable" in body["reason"] and "failed to connect" in body["reason"]
    _, health = call(agent, "GET", "/health")
    assert health["agent"]["last_error"] is None, "no rig is a state, not a fault"


def test_live_against_a_real_elkpy_and_no_sushi_is_unavailable(tmp_path):
    """The one place the real classification is met: elkpy's own error for a
    Sushi that is not there, from the real `capture`. In a child process,
    because elkpy leaves gRPC threads behind and a later test that forks (the
    supervisor's) would then be forking a threaded process."""
    pytest.importorskip("elkpy")
    import os
    import socket
    import subprocess
    from pathlib import Path

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    script = (
        "import asyncio, sys\n"
        "from aiohttp.test_utils import TestClient, TestServer\n"
        "from sushi_rig.agent import Agent, Live\n"
        "async def go():\n"
        "    agent = Agent(describe=lambda: {}, live=Live.at(f'localhost:{sys.argv[1]}'))\n"
        "    async with TestClient(TestServer(agent.app())) as client:\n"
        "        r = await client.get('/live')\n"
        "        print(r.status, (await r.json())['reason'])\n"
        "asyncio.run(go())\n"
    )
    done = subprocess.run(
        [sys.executable, "-c", script, str(port)], capture_output=True, text=True, timeout=60,
        env={**os.environ, "PYTHONPATH": str(Path(agent_module.__file__).parents[1])},
    )
    assert done.returncode == 0, done.stderr
    assert "503 Sushi is not reachable" in done.stdout, done.stdout


def test_a_poll_prints_nothing_and_sushi_gone_while_up_is_said_once(tmp_path):
    """#111: elkpy printed three ELKPY: lines on every connection, so a page
    polling /live buried everything the agent said. Against the real elkpy
    and no Sushi, in a child process as the test above is and for its
    reason: polls with no rig up print nothing at all, and with the state
    file saying a rig is up, the one thing that matters -- Sushi is not
    there -- is printed once, not once a poll, and reaches /health."""
    pytest.importorskip("elkpy")
    import json
    import os
    import socket
    import subprocess
    from pathlib import Path

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    script = (
        "import asyncio, json, sys\n"
        "from aiohttp.test_utils import TestClient, TestServer\n"
        "from sushi_rig.agent import Agent, Live\n"
        "rig = {'running': False}\n"
        "async def go():\n"
        "    agent = Agent(describe=lambda: dict(rig), live=Live.at(f'localhost:{sys.argv[1]}'))\n"
        "    out = {}\n"
        "    async with TestClient(TestServer(agent.app())) as client:\n"
        "        async def poll(n):\n"
        "            return [(await client.get('/live')).status for _ in range(n)]\n"
        "        out['down'] = await poll(3)\n"
        "        sys.stdout.write('--- up\\n'); sys.stdout.flush()\n"
        "        sys.stderr.write('--- up\\n'); sys.stderr.flush()\n"
        "        rig.update(running=True, config_name='x')\n"
        "        out['up'] = await poll(3)\n"
        "        out['health'] = (await (await client.get('/health')).json())['agent']['last_error']\n"
        "    with open(sys.argv[2], 'w') as f: json.dump(out, f)\n"
        "asyncio.run(go())\n"
        # Out at once: elkpy 1.3.0's controllers are not released by close()
        # and are torn down at interpreter exit, sometimes with "Event loop is
        # closed" from their __del__ (seen in a full run, not alone). That is
        # elkpy's close, handled apart from this; it is not a poll printing.
        "sys.stdout.flush(); sys.stderr.flush()\n"
        "import os; os._exit(0)\n"
    )
    done = subprocess.run(
        [sys.executable, "-c", script, str(port), str(tmp_path / "out.json")],
        capture_output=True, text=True, timeout=60,
        env={**os.environ, "PYTHONPATH": str(Path(agent_module.__file__).parents[1])},
    )
    assert done.returncode == 0, done.stderr
    out = json.loads((tmp_path / "out.json").read_text())
    assert out["down"] == [503, 503, 503] and out["up"] == [503, 503, 503]
    assert done.stdout == "--- up\n", "nothing on stdout, ELKPY: lines least of all"
    down, up = done.stderr.split("--- up\n")
    assert down == "", "polls with no rig up print nothing"
    said = up.splitlines()
    assert len(said) == 1, up
    assert said[0].startswith("sushi-rig agent: the rig is up but Sushi is not reachable: ")
    assert "ELKPY" not in done.stderr
    assert out["health"]["kind"] == "live"
    assert out["health"]["message"].startswith("the rig is up but Sushi is not reachable")


def test_sushi_gone_while_up_is_said_again_once_it_has_come_back_and_gone_again(capsys):
    elkpy_errors = pytest.importorskip("elkpy.sushierrors")
    agent, fake = live_agent()

    async def go():
        async with TestClient(TestServer(agent.app())) as client:
            statuses = []
            for gone in (True, True, False, True):
                fake.fail = elkpy_errors.SushiUnavailableError("failed to connect (test)", "") if gone else None
                statuses.append((await client.get("/live")).status)
            return statuses

    assert asyncio.run(go()) == [503, 503, 200, 503]
    assert capsys.readouterr().err.count("the rig is up but Sushi is not reachable") == 2


def test_elkpys_chatter_is_dropped_and_anything_else_it_says_reaches_stderr():
    from sushi_rig.live import elkpy_line

    said: set = set()
    for chatter in ("ELKPY: Compiled proto found!", "ELKPY: Synchronous context detected.",
                    "ELKPY: Asyncio context detected.", "ELKPY: Sushi API match! -> 1.2.0",
                    "ELKPY: Could not find a running Sushi at localhost:51051"):
        assert elkpy_line(chatter, said) is None, chatter
    mismatch = "ELKPY: API version mismatch! Sushi = 1.3.0 <-> Elkpy = 1.2.0"
    assert elkpy_line(mismatch, said) == f"sushi-rig: elkpy said: {mismatch}"
    assert elkpy_line(mismatch, said) is None, "true of every connection: said once"
    assert elkpy_line("Some gRPC context", said) == "sushi-rig: elkpy said: Some gRPC context"
    assert elkpy_line("Some gRPC context", said) is not None, "anything else, every time"


def test_elkpys_prints_go_through_the_quiet_print_once_installed(capsys):
    pytest.importorskip("elkpy")
    from elkpy import grpc_gen, notificationcontroller, sushicontroller

    from sushi_rig.live import elkpy_print, quiet_elkpy

    assert quiet_elkpy() is True
    assert grpc_gen.print is sushicontroller.print is notificationcontroller.print is elkpy_print
    grpc_gen.print("ELKPY: Compiled proto found!")
    sushicontroller.print("ELKPY: Sushi API match! -> 1.2.0")
    notificationcontroller.print("a real problem (test)")
    out, err = capsys.readouterr()
    assert out == "" and err == "sushi-rig: elkpy said: a real problem (test)\n"


# --------------------------------------------------------------------------- #
# GET /rig/load (#111): pw-top stood in; tests/test_top.py runs the real call
# against a stand-in executable
# --------------------------------------------------------------------------- #

PW_TOP_SAMPLE = (Path(__file__).parent / "fixtures" / "pw-top-b-n2.txt").read_text(encoding="utf-8")


def load_agent(pw_top, rig=None):
    return Agent(describe=lambda: dict(rig or {**UP, "children": {"sushi": 1, "amp": 2}}), pw_top=pw_top)


def test_the_load_is_the_running_rigs_nodes_from_one_pw_top_sample():
    timeouts = []
    agent = load_agent(lambda timeout: timeouts.append(timeout) or PW_TOP_SAMPLE)
    status, body = call(agent, "GET", "/rig/load")
    assert status == 200 and body["ok"]
    assert [n["name"] for n in body["nodes"]] == ["NAM", "Carla", "sushi"]
    assert body["total_load"] == pytest.approx(0.26) and body["budget"]["block_ms"] == 21.333
    assert body["missing"] == [] and "at" in body
    assert timeouts == [agent_module.LOAD_TIMEOUT], "always with a deadline"


def test_no_load_is_read_with_no_rig_up():
    asked = []
    status, body = call(load_agent(lambda t: asked.append(t), rig=DOWN), "GET", "/rig/load")
    assert (status, body["code"]) == (409, "no_rig")
    assert asked == [], "pw-top is not run for a rig that is not there"


@pytest.mark.parametrize("code, status", [("no_pw_top", 500), ("timeout", 504), ("failed", 502), ("no_sample", 502)])
def test_a_pw_top_that_cannot_be_read_is_refused_loudly_and_said_once(code, status, capsys):
    from sushi_rig.top import LoadError

    failing = {"now": True}

    def pw_top(timeout):
        if failing["now"]:
            raise LoadError(f"pw-top went wrong ({code}, test)", code)
        return PW_TOP_SAMPLE

    agent = load_agent(pw_top)

    async def go():
        async with TestClient(TestServer(agent.app())) as client:
            answers = []
            for flip in (True, True, False, True):
                failing["now"] = flip
                response = await client.get("/rig/load")
                answers.append((response.status, await response.json()))
            health = await (await client.get("/health")).json()
            return answers, health

    answers, health = asyncio.run(go())
    assert [a[0] for a in answers] == [status, status, 200, status]
    assert answers[0][1]["code"] == code and answers[0][1]["reason"] == f"pw-top went wrong ({code}, test)"
    err = capsys.readouterr().err
    # Once when it began, not on the second poll; again after it had worked.
    assert err.count(f"sushi-rig agent: load: pw-top went wrong ({code}, test)") == 2
    assert health["agent"]["last_error"] is None, "a figure polled every 2 s is not the rig's last error"


def test_a_parameter_is_set_by_name_and_logged_with_what_it_replaced():
    agent, fake = live_agent()
    status, body = call(agent, "POST", "/live/parameter",
                        json={"processor": "reverb", "parameter": "Mix", "value": 0.6})
    assert status == 200
    assert fake.writes() == [("set_parameter", "reverb", "Mix", 0.6)]
    assert body["ok"] is True
    assert body["op"] == "parameter"
    assert body["previous"] == 0.3
    assert body["args"] == {"processor": "reverb", "parameter": "Mix", "value": 0.6}
    assert set(body) == {"ok", "at", "op", "args", "previous"}

    status, log = call(agent, "GET", "/live/log")
    assert status == 200
    assert [(e["op"], e["previous"]) for e in log["log"]] == [("parameter", 0.3)]
    assert set(log["log"][0]) == {"at", "op", "args", "previous"}
    assert log["limit"] == 500


@pytest.mark.parametrize("path, body", [
    ("/live/parameter", {"processor": "reverb", "parameter": "Mix", "value": 1.5}),
    ("/live/parameter", {"processor": "reverb", "parameter": "Mix", "value": "loud"}),
    ("/live/parameter", {"processor": "reverb", "parameter": "Mix", "value": True}),
    ("/live/parameter", {"processor": "reverb", "parameter": "Mix"}),
    ("/live/parameter", {"processor": 4, "parameter": "Mix", "value": 0.5}),
    ("/live/bypass", {"processor": "drive", "bypassed": "yes"}),
    ("/live/bypass", {"processor": "drive"}),
    ("/live/move", {"processor": "drive", "direction": "sideways"}),
    ("/live/move", {"processor": "drive"}),
    ("/live/parameter", [1, 2]),
])
def test_a_badly_shaped_request_is_refused_without_contacting_sushi(path, body):
    agent, fake = live_agent()
    status, answer = call(agent, "POST", path, json=body)
    assert status == 400
    assert answer["ok"] is False and answer["reason"]
    assert fake.calls == [], "not even a read"
    assert call(agent, "GET", "/live/log")[1]["log"] == []


@pytest.mark.parametrize("path", ["/live/parameter", "/live/bypass", "/live/move"])
def test_a_request_with_no_body_or_bad_json_is_refused_without_contacting_sushi(path):
    agent, fake = live_agent()
    assert call(agent, "POST", path)[0] == 400
    status, body = call(agent, "POST", path, data=b"{no",
                        headers={"Content-Type": "application/json"})
    assert status == 400 and "not JSON" in body["reason"]
    assert fake.calls == []


@pytest.mark.parametrize("path, body, why", [
    ("/live/parameter", {"processor": "delay", "parameter": "Mix", "value": 0.5}, "no processor 'delay'"),
    ("/live/parameter", {"processor": "reverb", "parameter": "Tone", "value": 0.5}, "no parameter 'Tone'"),
    ("/live/parameter", {"processor": "reverb", "parameter": "Latency OUT", "value": 0.5}, "read-only"),
    ("/live/bypass", {"processor": "delay", "bypassed": True}, "no processor 'delay'"),
    ("/live/bypass", {"processor": "main", "bypassed": True}, "is a track"),
    ("/live/move", {"processor": "delay", "direction": "up"}, "on any track"),
    ("/live/move", {"processor": "main", "direction": "up"}, "on any track"),
])
def test_what_the_rig_does_not_have_is_refused_before_anything_is_changed(path, body, why):
    agent, fake = live_agent()
    status, answer = call(agent, "POST", path, json=body)
    assert status == 400
    assert why in answer["reason"]
    assert fake.writes() == []
    assert call(agent, "GET", "/live/log")[1]["log"] == []


def test_bypass_is_set_and_logged_with_the_previous_state():
    agent, fake = live_agent()
    status, body = call(agent, "POST", "/live/bypass", json={"processor": "drive", "bypassed": False})
    assert status == 200
    assert fake.writes() == [("set_bypass", "drive", False)]
    assert (body["op"], body["previous"]) == ("bypass", True)
    assert body["args"] == {"processor": "drive", "bypassed": False}
    # Sushi would not say reverb's state: the log says it does not know.
    _, body = call(agent, "POST", "/live/bypass", json={"processor": "reverb", "bypassed": True})
    assert body["previous"] is None


def test_a_move_uses_the_plan_and_logs_the_chain_it_changed():
    agent, fake = live_agent()
    status, body = call(agent, "POST", "/live/move", json={"processor": "drive", "direction": "up"})
    assert status == 200
    assert fake.writes() == [("move", "drive", -1)], "up is earlier in the chain"
    assert body["changed"] is True
    assert body["message"] == "moved drive earlier"
    assert (body["op"], body["previous"]) == ("move", ["comp", "drive", "reverb"])
    assert body["args"] == {"processor": "drive", "direction": "up"}

    status, body = call(agent, "POST", "/live/move", json={"processor": "reverb", "direction": "down"})
    assert (status, body["changed"]) == (200, False)
    assert body["message"] == "reverb is already last in the chain"
    assert fake.writes() == [("move", "drive", -1)], "a no-op never reaches Sushi"
    assert len(call(agent, "GET", "/live/log")[1]["log"]) == 1, "and is not logged"


def test_a_move_is_decided_once_and_the_command_sent_is_the_one_decided_on():
    """Success used to be read off the words `move_processor` returned, after a
    second walk and a second plan whose answer could differ from the first."""
    agent, fake = live_agent()
    status, body = call(agent, "POST", "/live/move", json={"processor": "drive", "direction": "down"})
    assert status == 200 and body["changed"] is True
    assert [c[0] for c in fake.calls] == ["plan_move", "move"], "one decision, one command"
    assert fake.sent.command == (3, 1, 0, True), \
        "sent as planned: drive (3) on main (1), to the back of the chain"
    assert body["message"] == "moved drive later"
    assert call(agent, "GET", "/live/log")[1]["log"][0]["previous"] == ["comp", "drive", "reverb"]


def test_a_processor_found_already_at_the_end_is_unchanged_not_a_failure():
    """The case the second walk made a 502: the processor is where the request
    wants it only when the one walk looks."""
    agent, fake = live_agent()
    fake.chain = ["drive", "comp", "reverb"]
    status, body = call(agent, "POST", "/live/move", json={"processor": "drive", "direction": "up"})
    assert (status, body["changed"]) == (200, False)
    assert body["message"] == "drive is already first in the chain"
    assert body["chain"] == ["drive", "comp", "reverb"]
    assert fake.writes() == []
    assert call(agent, "GET", "/live/log")[1]["log"] == []


@pytest.mark.parametrize("method, path, body", [
    ("GET", "/live", None),
    ("POST", "/live/parameter", {"processor": "reverb", "parameter": "Mix", "value": 0.5}),
    ("POST", "/live/bypass", {"processor": "drive", "bypassed": True}),
    ("POST", "/live/move", {"processor": "drive", "direction": "up"}),
])
def test_live_is_busy_while_a_lifecycle_command_runs_and_does_not_take_its_lock(method, path, body):
    started, release = asyncio.Event(), asyncio.Event()

    async def slow(args):
        started.set()
        await release.wait()
        return Outcome(True, 0, "", "")

    agent, fake = live_agent(run=slow)

    async def go():
        async with TestClient(TestServer(agent.app())) as client:
            lifecycle = asyncio.ensure_future(client.post("/rig/up", json={"config_name": "electric_board"}))
            await started.wait()
            response = await client.request(method, path, json=body)
            assert response.status == 409
            assert "busy: 'up' is still running" in (await response.json())["reason"]
            assert (await client.get("/live/log")).status == 200, "the log is memory, not Sushi"
            release.set()
            assert (await lifecycle).status == 200

    asyncio.run(go())
    assert fake.calls == [], "nothing touched Sushi while a lifecycle command ran"


def test_a_live_change_does_not_hold_the_lifecycle_lock():
    agent, fake = live_agent()
    held = []
    fake.during = lambda: held.append(agent._lock.locked())
    status, _ = call(agent, "POST", "/live/parameter",
                     json={"processor": "reverb", "parameter": "Mix", "value": 0.5})
    assert status == 200
    assert held == [False]


def test_sushi_failing_mid_change_is_a_bad_gateway_with_the_reason_and_is_remembered():
    agent, fake = live_agent()
    fake.fail, fake.fail_on = ValueError("value refused\nby the plugin"), "set_parameter"
    status, body = call(agent, "POST", "/live/parameter",
                        json={"processor": "reverb", "parameter": "Mix", "value": 0.5})
    assert status == 502
    assert body["reason"] == "ValueError: value refused by the plugin", "one line, and no traceback"
    assert "Traceback" not in str(body)
    _, health = call(agent, "GET", "/health")
    assert health["ok"] is True
    assert health["agent"]["last_error"]["kind"] == "live"
    assert health["agent"]["last_error"]["command"] == ["POST", "/live/parameter"]
    assert "value refused" in health["agent"]["last_error"]["message"]
    assert call(agent, "GET", "/live/log")[1]["log"] == [], "a change that failed is not logged"

    fake.fail = None
    assert call(agent, "POST", "/live/parameter",
                json={"processor": "reverb", "parameter": "Mix", "value": 0.5})[0] == 200
    _, health = call(agent, "GET", "/health")
    assert health["agent"]["last_error"] is None, "a change that worked supersedes it"


def test_sushi_gone_between_the_read_and_the_change_is_a_bad_gateway():
    elkpy_errors = pytest.importorskip("elkpy.sushierrors")
    agent, fake = live_agent()
    fake.fail, fake.fail_on = elkpy_errors.SushiUnavailableError("socket closed", ""), "set_bypass"
    status, body = call(agent, "POST", "/live/bypass", json={"processor": "drive", "bypassed": False})
    assert status == 502
    assert "went away" in body["reason"] and "socket closed" in body["reason"]
    assert call(agent, "GET", "/health")[1]["agent"]["last_error"]["kind"] == "live"


def test_a_failing_read_that_is_not_a_missing_rig_is_a_bad_gateway():
    agent, fake = live_agent()
    fake.fail = RuntimeError("proto mismatch")
    status, body = call(agent, "GET", "/live")
    assert status == 502
    assert body["reason"] == "RuntimeError: proto mismatch"


def test_a_missing_elkpy_is_the_agents_install_not_sushi_and_is_not_a_bad_gateway():
    """live.py raises it (it used to exit the process, which in an executor
    thread would end the event loop). It is 500, said with its hint, and the
    agent goes on serving."""
    from sushi_rig.live import ElkpyMissing

    agent, fake = live_agent()
    fake.fail = ElkpyMissing("elkpy not found. Install it into the tool's venv: pip install elkpy")
    for method, path, body in [
        ("GET", "/live", None),
        ("POST", "/live/bypass", {"processor": "drive", "bypassed": True}),
    ]:
        status, answer = call(agent, method, path, json=body)
        assert status == 500
        assert "pip install elkpy" in answer["reason"]
    assert call(agent, "GET", "/rig")[0] == 200, "and the agent is still serving"
    assert call(agent, "GET", "/health")[1]["agent"]["last_error"]["kind"] == "live"
    fake.fail = None
    assert call(agent, "GET", "/live")[0] == 200


@pytest.mark.parametrize("path, body, fails", [
    ("/live/parameter", {"processor": "reverb", "parameter": "Mix", "value": 0.5}, "set_parameter"),
    ("/live/bypass", {"processor": "drive", "bypassed": False}, "set_bypass"),
    ("/live/move", {"processor": "drive", "direction": "up"}, "move"),
])
def test_a_change_sushi_refused_or_did_not_confirm_is_a_bad_gateway_and_not_logged(path, body, fails):
    """live.py raises LiveError where Sushi errors or stays silent (it used
    to warn on stderr and return), so the operation log, which undo will read,
    cannot hold a change that never happened."""
    from sushi_rig.live import LiveError

    agent, fake = live_agent()
    fake.fail, fake.fail_on = LiveError("Sushi did not confirm the command within 5s"), fails
    status, answer = call(agent, "POST", path, json=body)
    assert status == 502
    assert answer["reason"] == "Sushi did not confirm the command within 5s"
    assert call(agent, "GET", "/live/log")[1]["log"] == []
    assert call(agent, "GET", "/health")[1]["agent"]["last_error"]["kind"] == "live"


def test_a_change_queued_behind_a_change_is_busy_if_a_lifecycle_command_started_meanwhile():
    """The busy check is made when the change gets its turn, not when it
    queued: otherwise it reads Sushi mid-restart and a 502 is remembered as a
    live fault. (A read does not queue at all: see below.)"""
    agent, fake = live_agent()
    entered, release = threading.Event(), threading.Event()
    started, finish = asyncio.Event(), asyncio.Event()

    def slow_set():
        entered.set()
        release.wait(5)

    fake.during = slow_set

    async def lifecycle(args):
        started.set()
        await finish.wait()
        return Outcome(True, 0, "", "")

    agent.run = lifecycle

    async def go():
        loop = asyncio.get_running_loop()
        async with TestClient(TestServer(agent.app())) as client:
            change = asyncio.ensure_future(client.post(
                "/live/parameter", json={"processor": "reverb", "parameter": "Mix", "value": 0.5}))
            await loop.run_in_executor(None, entered.wait, 5)
            read = asyncio.ensure_future(client.post(
                "/live/parameter", json={"processor": "reverb", "parameter": "Mix", "value": 0.7}))
            await asyncio.sleep(0.1)  # queued on the live lock
            up = asyncio.ensure_future(client.post("/rig/up", json={"config_name": "electric_board"}))
            await started.wait()
            release.set()
            assert (await change).status == 200
            response = await read
            finish.set()
            await up
            return response.status, await response.json()

    status, body = asyncio.run(go())
    assert status == 409 and "busy: 'up' is still running" in body["reason"]
    assert [c[0] for c in fake.calls].count("parameter") == 1, "the queued change did not read Sushi"
    assert len(fake.opened) == 2, "it connected while it waited, before the lock"
    assert fake.one_connection_per_request_all_closed(), "and closed what it opened"
    assert agent.last_error is None


def test_live_changes_are_serialised_so_each_previous_is_what_it_replaced():
    agent, fake = live_agent()

    async def go():
        async with TestClient(TestServer(agent.app())) as client:
            posts = [
                client.post("/live/parameter",
                            json={"processor": "reverb", "parameter": "Mix", "value": v})
                for v in (0.1, 0.2, 0.3, 0.4)
            ]
            for response in await asyncio.gather(*posts):
                assert response.status == 200

    asyncio.run(go())
    log = agent.log
    assert log[0]["previous"] == 0.3
    for before, after in zip(log, log[1:]):
        assert after["previous"] == before["args"]["value"]


def test_a_read_is_not_queued_behind_a_change():
    """A panel polling GET /live must not make fader writes wait on a full-rig
    read, nor the read wait on a write: a read cannot disturb the `previous` the
    lock exists to keep true."""
    agent, fake = live_agent()
    entered, release = threading.Event(), threading.Event()
    fake.during = lambda: (entered.set(), release.wait(5))

    async def go():
        loop = asyncio.get_running_loop()
        async with TestClient(TestServer(agent.app())) as client:
            change = asyncio.ensure_future(client.post(
                "/live/parameter", json={"processor": "reverb", "parameter": "Mix", "value": 0.5}))
            await loop.run_in_executor(None, entered.wait, 5)
            try:
                response = await asyncio.wait_for(client.get("/live"), 2)
                status = response.status
            except asyncio.TimeoutError:
                status = "queued behind the change"
            finally:
                release.set()
            assert (await change).status == 200
            return status

    assert asyncio.run(go()) == 200


def test_a_change_waits_for_a_change_but_not_for_a_read():
    """The other direction: while a read is in Sushi a change goes ahead."""
    agent, fake = live_agent()
    fake.hang_on = "snapshot"

    async def go():
        loop = asyncio.get_running_loop()
        async with TestClient(TestServer(agent.app())) as client:
            read = asyncio.ensure_future(client.get("/live"))
            await loop.run_in_executor(None, fake.reached.wait, 5)
            try:
                response = await asyncio.wait_for(client.post(
                    "/live/parameter", json={"processor": "reverb", "parameter": "Mix", "value": 0.5}), 2)
                status = response.status
            except asyncio.TimeoutError:
                status = "queued behind the read"
            finally:
                fake.release.set()
            assert (await read).status == 200
            return status

    assert asyncio.run(go()) == 200


@pytest.mark.parametrize("method, path, body", [
    ("GET", "/live", None),
    ("POST", "/live/parameter", {"processor": "reverb", "parameter": "Mix", "value": 0.5}),
    ("POST", "/live/bypass", {"processor": "drive", "bypassed": False}),
    ("POST", "/live/move", {"processor": "drive", "direction": "up"}),
    ("POST", "/live/parameter", {"processor": "delay", "parameter": "Mix", "value": 0.5}),  # refused
])
def test_a_request_makes_one_connection_does_everything_on_it_and_closes_it(method, path, body):
    """A read and a change used to make a connection each: a dozen
    sub-controllers, an RPC and a notification thread, twice. (The fake fails a
    call that is not given the request's open connection.)"""
    agent, fake = live_agent()
    status, _ = call(agent, method, path, json=body)
    assert status in (200, 400)
    assert fake.opened == [1]
    assert fake.one_connection_per_request_all_closed()
    assert fake.calls, "and it was used"


def test_a_connection_is_closed_when_the_request_fails_too():
    agent, fake = live_agent()
    fake.fail, fake.fail_on = ValueError("refused"), "set_bypass"
    assert call(agent, "POST", "/live/bypass", json={"processor": "drive", "bypassed": False})[0] == 502
    assert fake.one_connection_per_request_all_closed() and fake.opened == [1]


def test_a_connection_that_will_not_close_is_said_not_swallowed(capsys):
    agent, fake = live_agent()

    def broken(connection):
        raise OSError("socket already gone")

    fake.disconnect = broken
    status, body = call(agent, "POST", "/live/bypass", json={"processor": "drive", "bypassed": False})
    assert status == 200, "the change was made; the answer says so"
    assert "closing the connection to Sushi failed (POST /live/bypass): OSError: socket already gone" \
        in capsys.readouterr().err
    last = agent.last_error
    assert last["kind"] == "live" and "socket already gone" in last["message"]


@pytest.mark.parametrize("method, path, body, hangs_in, applied_unknown", [
    ("POST", "/live/parameter", {"processor": "reverb", "parameter": "Mix", "value": 0.5}, "set_parameter", True),
    ("POST", "/live/move", {"processor": "drive", "direction": "up"}, "move", True),
    ("POST", "/live/bypass", {"processor": "drive", "bypassed": False}, "bypass", False),
    ("GET", "/live", None, "snapshot", False),
])
def test_a_call_to_sushi_that_never_returns_is_a_504_and_frees_the_lock(
    monkeypatch, method, path, body, hangs_in, applied_unknown
):
    """One hung gRPC call used to hold the live lock for ever: every later
    /live request waited behind it with no 409, 502 or timeout. The agent now
    stops waiting. The stuck worker thread cannot be cancelled and is not
    stopped; that is stated in `LIVE_TIMEOUT`, and shown here by the call still
    being inside Sushi when the next request is answered."""
    monkeypatch.setattr(agent_module, "LIVE_TIMEOUT", 0.3)
    agent, fake = live_agent()
    fake.hang_on = hangs_in

    async def go():
        async with TestClient(TestServer(agent.app())) as client:
            try:
                first = await client.request(method, path, json=body)
                stuck = (first.status, await first.json())
                # The lock is free and the stuck call is still inside Sushi:
                # the next request is answered, on a connection of its own.
                inside = not fake.release.is_set()
                second = await asyncio.wait_for(client.get("/live"), 5)
                log = await (await client.get("/live/log")).json()
                closed_meanwhile = list(fake.closed)
                return stuck, inside, second.status, log, closed_meanwhile
            finally:
                fake.release.set()
                # The late close is done on this loop: keep it until it has.
                await asyncio.get_running_loop().run_in_executor(
                    None, _until, lambda: sorted(fake.closed) == [1, 2])

    (status, answer), inside, second, log, closed_meanwhile = asyncio.run(go())
    assert status == 504
    assert "did not answer within 0.3s" in answer["reason"]
    assert ("may or may not have been applied" in answer["reason"]) is applied_unknown
    assert inside and second == 200
    assert log["log"] == [], "a change nobody saw confirmed is not logged"
    assert agent.last_error["kind"] == "live" and "did not answer" in agent.last_error["message"]
    assert len(fake.opened) == 2 and closed_meanwhile == [2], \
        "the abandoned request's connection is not closed from under its stuck call"
    assert _until(lambda: sorted(fake.closed) == [1, 2]), "but when the call returns, it is"


def _until(condition, seconds=3.0):
    """Poll for something a worker thread does after the request is answered."""
    import time

    end = time.monotonic() + seconds
    while not condition() and time.monotonic() < end:
        time.sleep(0.01)
    return condition()


def _timed_out(agent, fake, method, path, body, *, then=None, settled=None):
    """One request that times out, the stuck call still inside Sushi; `then`
    runs on the loop before the call is let go, and the loop is kept until the
    call has returned and `settled` holds (the late close runs on it)."""

    async def go():
        async with TestClient(TestServer(agent.app())) as client:
            try:
                response = await client.request(method, path, json=body)
                result = (response.status, await response.json())
                if then:
                    await then(client)
                return result
            finally:
                fake.release.set()
                await asyncio.get_running_loop().run_in_executor(
                    None, _until, settled or (lambda: agent.stuck_live_calls == 0))
                await asyncio.sleep(0.2)

    return asyncio.run(go())


@pytest.mark.parametrize("hangs_in", ["bypass", "set_bypass", "connect"])
def test_a_connection_abandoned_after_a_504_is_closed_when_the_stuck_call_does_return(
    monkeypatch, capsys, hangs_in
):
    """A slow call (not only a stuck-for-ever one) used to leak its controller,
    notification thread and gRPC channels for the agent's life: nothing closed
    the connection once the call came back. Where the call that timed out is the
    connect itself, the connection it returns late is nobody's and is closed
    too. What came back is said."""
    monkeypatch.setattr(agent_module, "LIVE_TIMEOUT", 0.2)
    agent, fake = live_agent()
    fake.hang_on = hangs_in
    status, _ = _timed_out(agent, fake, "POST", "/live/bypass", {"processor": "drive", "bypassed": False},
                           settled=lambda: fake.closed == fake.opened != [])
    assert status == 504
    assert _until(lambda: len(fake.opened) == 1 and fake.closed == fake.opened), \
        f"opened {fake.opened}, closed {fake.closed}"
    said = []
    assert _until(lambda: said.append(capsys.readouterr().err) or "returned after the 504" in "".join(said))
    assert _until(lambda: agent.stuck_live_calls == 0)


def test_a_late_return_is_said_in_health(monkeypatch):
    monkeypatch.setattr(agent_module, "LIVE_TIMEOUT", 0.2)
    agent, fake = live_agent()
    fake.hang_on = "set_bypass"
    seen = []

    async def then(client):
        fake.release.set()
        assert await asyncio.get_running_loop().run_in_executor(
            None, _until, lambda: fake.closed == fake.opened)
        seen.append((await (await client.get("/health")).json())["agent"])

    status, _ = _timed_out(agent, fake, "POST", "/live/bypass",
                           {"processor": "drive", "bypassed": False}, then=then)
    assert status == 504
    last = seen[0]["last_error"]
    assert "returned after the 504" in last["message"] or "returned after the 504" in str(last.get("also"))
    assert seen[0]["stuck_live_calls"] == 0


def test_a_timeout_error_from_the_call_itself_is_not_the_agents_timeout():
    """`asyncio.TimeoutError` is the builtin on 3.11+, so a socket or gRPC
    deadline raised inside the call looked like the agent's 20 s wall clock:
    misreported as 504, and its connection treated as abandoned though the
    thread had already returned."""
    agent, fake = live_agent()
    fake.fail, fake.fail_on = TimeoutError("deadline exceeded"), "set_bypass"
    status, body = call(agent, "POST", "/live/bypass", json={"processor": "drive", "bypassed": False})
    assert status == 502
    assert body["reason"] == "TimeoutError: deadline exceeded"
    assert fake.closed == [1], "closed at once: nothing is still inside it"
    assert agent.stuck_live_calls == 0


def test_a_close_that_fails_does_not_overwrite_the_failure_that_came_first(capsys):
    agent, fake = live_agent()
    fake.fail, fake.fail_on = ValueError("value refused"), "set_bypass"

    def broken(connection):
        raise OSError("socket already gone")

    fake.disconnect = broken
    assert call(agent, "POST", "/live/bypass", json={"processor": "drive", "bypassed": False})[0] == 502
    last = agent.last_error
    assert "value refused" in last["message"], "the reason /health should show"
    assert any("socket already gone" in m for m in last["also"]), "and the close failure beside it"
    assert "socket already gone" in capsys.readouterr().err


def test_a_move_of_what_the_rig_lacks_names_what_it_has():
    agent, fake = live_agent()
    status, body = call(agent, "POST", "/live/move", json={"processor": "delay", "direction": "up"})
    assert status == 400
    assert "['comp', 'drive', 'reverb']" in body["reason"]


def test_a_queued_change_connects_while_it_waits_so_the_lock_covers_only_read_and_set():
    """Constructing a controller (a dozen sub-controllers, an RPC, a thread)
    used to happen inside the lock, so every queued fader write paid it in
    turn."""
    agent, fake = live_agent()
    entered, release = threading.Event(), threading.Event()
    fake.during = lambda: (entered.set(), release.wait(5))

    async def go():
        loop = asyncio.get_running_loop()
        async with TestClient(TestServer(agent.app())) as client:
            first = asyncio.ensure_future(client.post(
                "/live/parameter", json={"processor": "reverb", "parameter": "Mix", "value": 0.5}))
            await loop.run_in_executor(None, entered.wait, 5)
            second = asyncio.ensure_future(client.post(
                "/live/parameter", json={"processor": "reverb", "parameter": "Mix", "value": 0.6}))
            connected = await loop.run_in_executor(None, _until, lambda: len(fake.opened) == 2)
            release.set()
            statuses = [(await first).status, (await second).status]
            return connected, statuses

    connected, statuses = asyncio.run(go())
    assert connected, "the second request connected while the first held the lock"
    assert statuses == [200, 200]
    assert [e["previous"] for e in agent.log] == [0.3, 0.5], "and previous is still what it replaced"
    assert fake.one_connection_per_request_all_closed()


def test_a_sushi_that_is_stuck_for_good_fills_a_bounded_pool_of_its_own_and_is_then_refused(monkeypatch):
    """The default executor has min(32, cpus + 4) workers and /doctor shares
    it: a stuck Sushi exhausted it after a handful of 504s, after which every
    request queued for a worker and was reported as Sushi not answering though
    its call never started. Now Sushi has its own, and a full one is a 503 that
    says so."""
    monkeypatch.setattr(agent_module, "LIVE_TIMEOUT", 0.2)
    monkeypatch.setattr(agent_module, "LIVE_WORKERS", 2)
    agent, fake = live_agent()
    fake.hang_on, fake.hang_times = "bypass", 2
    body = {"processor": "drive", "bypassed": False}

    async def go():
        async with TestClient(TestServer(agent.app())) as client:
            try:
                answers = []
                for _ in range(3):
                    response = await client.post("/live/bypass", json=body)
                    answers.append((response.status, (await response.json())["reason"]))
                health = (await (await client.get("/health")).json())["agent"]
                doctor = await client.get("/doctor")
                fake.release.set()
                recovered = await loop_until(lambda: not agent._in_flight)
                after = await client.post("/live/bypass", json=body)
                return answers, health, doctor.status, recovered, after.status
            finally:
                fake.release.set()

    async def loop_until(condition):
        return await asyncio.get_running_loop().run_in_executor(None, _until, condition)

    answers, health, doctor, recovered, after = asyncio.run(go())
    assert [a[0] for a in answers] == [504, 504, 503]
    assert "2 calls to Sushi" in answers[2][1] and "not answering" in answers[2][1]
    assert health["stuck_live_calls"] == 2
    assert doctor == 200, "/doctor does not share the pool"
    assert recovered and after == 200, "and it recovers when the calls return"
    assert fake.thread_names and all(n.startswith("sushi-live") for n in fake.thread_names)


def test_a_connect_cancelled_while_in_flight_has_its_late_connection_closed():
    """The client going while the connect is running cancels the handler; the
    connection the connect then returns used to be nobody's, and leaked."""
    from types import SimpleNamespace

    agent, fake = live_agent()
    fake.hang_on = "connect"
    request = SimpleNamespace(method="GET", path="/live")

    async def go():
        async def use():
            async with agent._connected(request):
                pytest.fail("connected")

        task = asyncio.ensure_future(use())
        await asyncio.get_running_loop().run_in_executor(None, fake.reached.wait, 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert agent.stuck_live_calls == 1
        fake.release.set()
        assert await asyncio.get_running_loop().run_in_executor(
            None, _until, lambda: fake.opened == [1] and fake.closed == [1])
        await asyncio.sleep(0.1)

    asyncio.run(go())
    assert agent.stuck_live_calls == 0


def test_a_call_that_returns_between_the_timeout_check_and_the_abandoning_is_not_counted_stuck(
    monkeypatch
):
    """The done-callback runs in the worker thread and the abandoning on the
    loop; a call that returned in the gap between them stayed in the abandoned
    set for ever, inflating `stuck_live_calls` and the count in the 503."""
    import concurrent.futures

    monkeypatch.setattr(agent_module, "LIVE_TIMEOUT", 0.1)

    class Racy(concurrent.futures.Future):
        armed = False

        def done(self):
            was = super().done()
            if self.armed and not was:
                self.armed = False
                self.set_result(None)  # it returns now: after the check, before the add
            return was

    future = Racy()

    class Pool:
        def submit(self, fn, *args):
            future.set_running_or_notify_cancel()  # a worker has it: it cannot be cancelled
            return future

        def shutdown(self, **kwargs):
            pass

    real_wait_for = asyncio.wait_for

    async def arming(awaitable, timeout):
        try:
            return await real_wait_for(awaitable, timeout)
        except asyncio.TimeoutError:
            future.armed = True
            raise

    monkeypatch.setattr(agent_module.asyncio, "wait_for", arming)
    agent, fake = live_agent()
    agent._executor = Pool()
    status, _ = call(agent, "GET", "/live")
    assert status == 504
    assert agent.stuck_live_calls == 0
    assert not agent._in_flight


def test_a_connect_that_never_returns_is_a_504_too(monkeypatch):
    monkeypatch.setattr(agent_module, "LIVE_TIMEOUT", 0.2)
    agent, fake = live_agent()
    release = threading.Event()
    real = fake.connect
    fake.connect = lambda: (release.wait(5), real())[1]
    try:
        status, body = call(agent, "GET", "/live")
    finally:
        release.set()
    assert status == 504 and "did not answer" in body["reason"]


def test_the_log_is_the_last_500_newest_last():
    agent, fake = live_agent()

    async def go():
        async with TestClient(TestServer(agent.app())) as client:
            for i in range(505):
                response = await client.post(
                    "/live/parameter",
                    json={"processor": "reverb", "parameter": "Mix", "value": i / 1000})
                assert response.status == 200
            return await (await client.get("/live/log")).json()

    body = asyncio.run(go())
    log = body["log"]
    assert len(log) == 500
    assert log[0]["args"]["value"] == 5 / 1000, "the five oldest were dropped"
    assert log[-1]["args"]["value"] == 504 / 1000, "the newest is last"
    assert log[-1]["previous"] == 503 / 1000


# --------------------------------------------------------------------------- #
# Undo (#29): the route, against a fake Sushi
# --------------------------------------------------------------------------- #

def _seeded():
    """An agent with a parameter, a bypass and a move in its log, in that order
    (entries 0, 1, 2), and the fake's calls cleared so a test sees only the
    undo's."""
    agent, fake = live_agent()
    assert call(agent, "POST", "/live/parameter",
                json={"processor": "reverb", "parameter": "Mix", "value": 0.6})[0] == 200
    assert call(agent, "POST", "/live/bypass", json={"processor": "drive", "bypassed": False})[0] == 200
    assert call(agent, "POST", "/live/move", json={"processor": "drive", "direction": "up"})[0] == 200
    fake.calls.clear()
    return agent, fake


def test_a_move_is_undone_by_moving_the_other_way_and_the_chain_is_the_answer():
    agent, fake = _seeded()
    assert fake.chain == ["drive", "comp", "reverb"]
    status, body = call(agent, "POST", "/live/undo")
    assert status == 200
    assert fake.writes() == [("move", "drive", 1)], "down: the opposite of the move undone"
    assert fake.chain == ["comp", "drive", "reverb"]
    assert body["undone"]["op"] == "move"
    assert body["undone"]["args"] == {"processor": "drive", "direction": "up"}
    assert body["state"] == {"processor": "drive", "chain": ["comp", "drive", "reverb"]}
    assert (body["op"], body["undoes"]) == ("undo", 2)


def test_a_bypass_is_undone_to_the_state_it_replaced():
    agent, fake = _seeded()
    call(agent, "POST", "/live/undo")  # the move
    fake.calls.clear()
    status, body = call(agent, "POST", "/live/undo")
    assert status == 200
    assert fake.writes() == [("set_bypass", "drive", True)]
    assert fake.bypassed["drive"] is True
    assert body["undone"]["op"] == "bypass"
    assert body["state"] == {"processor": "drive", "bypassed": True}
    assert body["undoes"] == 1


def test_a_parameter_is_undone_to_the_value_it_replaced():
    agent, fake = _seeded()
    call(agent, "POST", "/live/undo")
    call(agent, "POST", "/live/undo")
    fake.calls.clear()
    status, body = call(agent, "POST", "/live/undo")
    assert status == 200
    assert fake.writes() == [("set_parameter", "reverb", "Mix", 0.3)]
    assert fake.info["reverb"]["Mix"]["value"] == 0.3
    assert body["undone"]["op"] == "parameter"
    assert body["state"] == {"processor": "reverb", "parameter": "Mix", "value": 0.3}
    assert body["undoes"] == 0


def test_with_everything_undone_there_is_nothing_to_undo():
    agent, fake = _seeded()
    for _ in range(3):
        assert call(agent, "POST", "/live/undo")[0] == 200
    fake.calls.clear()
    status, body = call(agent, "POST", "/live/undo")
    assert status == 409 and body["reason"] == "nothing to undo"
    assert fake.calls == [] and fake.writes() == []
    assert len(call(agent, "GET", "/live/log")[1]["log"]) == 6, "and a refusal is not logged"


def test_an_empty_log_has_nothing_to_undo_and_does_not_contact_sushi():
    agent, fake = live_agent()
    status, body = call(agent, "POST", "/live/undo")
    assert (status, body["reason"]) == (409, "nothing to undo")
    assert fake.opened == [] and fake.calls == [], "no rig need be up to be told so"


def test_the_undo_is_logged_with_what_it_undid_and_what_it_replaced():
    agent, fake = live_agent()
    call(agent, "POST", "/live/parameter", json={"processor": "reverb", "parameter": "Mix", "value": 0.6})
    status, body = call(agent, "POST", "/live/undo")
    assert status == 200
    log = call(agent, "GET", "/live/log")[1]
    assert log["first"] == 0
    original, undo = log["log"]
    assert set(original) == {"at", "op", "args", "previous"}, "ordinary entries keep their shape"
    assert set(undo) == {"at", "op", "args", "previous", "undoes"}
    assert undo["op"] == "undo" and undo["undoes"] == 0
    # What was applied, in the route's own words, and what it replaced: enough
    # for a redo to be built from this entry alone.
    assert undo["args"] == {
        "of": "parameter", "processor": "reverb", "parameter": "Mix", "value": 0.3}
    assert undo["previous"] == 0.6
    assert {k: body[k] for k in undo} == undo, "the answer carries the entry"


def test_undos_are_never_themselves_undone():
    """Undoing an undo is out of scope (a redo is built from `undoes` later):
    a second request goes on to the next older change, and never re-applies
    the first undo."""
    agent, fake = _seeded()
    call(agent, "POST", "/live/undo")
    status, body = call(agent, "POST", "/live/undo")
    assert body["undone"]["op"] == "bypass" and body["undoes"] == 1


def test_a_move_whose_chain_has_changed_since_is_refused_and_nothing_is_sent():
    agent, fake = _seeded()
    fake.chain = ["comp", "reverb", "drive"]  # the panel moved it meanwhile
    status, body = call(agent, "POST", "/live/undo")
    assert status == 409
    assert "no longer where the move left it" in body["reason"]
    assert "['comp', 'reverb', 'drive']" in body["reason"], "and says where it is"
    assert fake.writes() == []
    assert fake.chain == ["comp", "reverb", "drive"]
    assert len(call(agent, "GET", "/live/log")[1]["log"]) == 3, "a refusal is not logged"
    # Refused, not skipped: the older changes are not undone behind its back.
    assert fake.bypassed["drive"] is False


def test_a_move_of_a_processor_since_removed_is_refused_as_the_move_route_would():
    agent, fake = _seeded()
    fake.chain = ["comp", "reverb"]
    status, body = call(agent, "POST", "/live/undo")
    assert status == 400 and "on any track" in body["reason"] and fake.writes() == []


def test_a_change_whose_previous_sushi_would_not_say_is_refused():
    agent, fake = live_agent()
    call(agent, "POST", "/live/bypass", json={"processor": "reverb", "bypassed": True})
    status, body = call(agent, "POST", "/live/undo")
    assert (status, body["code"]) == (409, "stale")
    assert "did not say what reverb was before" in body["reason"]
    assert fake.writes() == [("set_bypass", "reverb", True)], "only the original change"


def test_a_processor_the_rig_no_longer_has_is_the_status_the_bypass_route_gives_it():
    agent, fake = _seeded()
    call(agent, "POST", "/live/undo")  # the move
    fake.bypass = lambda connection, processor: {"chains": {"main": ["comp"]}, "bypassed": None}
    status, body = call(agent, "POST", "/live/undo")
    assert status == 400 and "no processor 'drive'" in body["reason"]
    assert "skip" in body["reason"], "and says the way out"


def test_an_undo_with_a_body_is_refused_before_sushi_is_contacted():
    agent, fake = _seeded()
    status, body = call(agent, "POST", "/live/undo", json={"index": 0})
    assert status == 400 and "empty" in body["reason"]
    assert fake.calls == []


def test_the_log_trimmed_past_an_undone_entry_still_finds_the_next_to_undo():
    agent, fake = live_agent(log_limit=3)
    for value in (0.4, 0.5, 0.6):  # entries 0, 1, 2 with previous 0.3, 0.4, 0.5
        call(agent, "POST", "/live/parameter",
             json={"processor": "reverb", "parameter": "Mix", "value": value})
    assert call(agent, "POST", "/live/undo")[1]["undoes"] == 2
    log = call(agent, "GET", "/live/log")[1]  # entry 0 trimmed: 1, 2, undo(2)
    assert log["first"] == 1 and [e.get("undoes") for e in log["log"]] == [None, None, 2]

    status, body = call(agent, "POST", "/live/undo")
    assert (status, body["undoes"], body["state"]["value"]) == (200, 1, 0.4)
    log = call(agent, "GET", "/live/log")[1]  # 2, undo(2), undo(1): entry 1 itself has gone
    assert log["first"] == 2 and [e.get("undoes") for e in log["log"]] == [None, 2, 1]
    # Entry 2 is undone and so is entry 1 (its entry trimmed, its undo kept), and
    # entry 0 is gone: nothing is left. The undo of 2 is kept beside entry 2, so
    # entry 2 is not offered again for being the only ordinary entry in the log.
    status, body = call(agent, "POST", "/live/undo")
    assert (status, body["reason"]) == (409, "nothing to undo")


def test_an_undo_whose_target_was_trimmed_away_does_not_resurrect_an_older_change():
    agent, fake = live_agent(log_limit=2)
    for value in (0.4, 0.5):
        call(agent, "POST", "/live/parameter",
             json={"processor": "reverb", "parameter": "Mix", "value": value})
    assert call(agent, "POST", "/live/undo")[1]["undoes"] == 1
    log = call(agent, "GET", "/live/log")[1]  # entry 0 trimmed: entry 1, undo(1)
    assert log["first"] == 1 and len(log["log"]) == 2
    fake.calls.clear()
    status, body = call(agent, "POST", "/live/undo")
    assert (status, body["reason"]) == (409, "nothing to undo")
    assert fake.writes() == [], "entry 0 is neither undone nor offered: it is not in the log"


@pytest.mark.parametrize("fails", ["parameter", "set_parameter"])
def test_an_undo_sushi_refused_or_did_not_confirm_is_a_bad_gateway_and_not_logged(fails):
    from sushi_rig.live import LiveError

    agent, fake = live_agent()
    call(agent, "POST", "/live/parameter", json={"processor": "reverb", "parameter": "Mix", "value": 0.6})
    fake.fail, fake.fail_on = LiveError("Sushi did not confirm the command within 5s"), fails
    status, answer = call(agent, "POST", "/live/undo")
    assert status == 502 and answer["reason"] == "Sushi did not confirm the command within 5s"
    assert len(call(agent, "GET", "/live/log")[1]["log"]) == 1
    assert call(agent, "GET", "/health")[1]["agent"]["last_error"]["kind"] == "live"
    # And the undo is still there to try again once Sushi answers.
    fake.fail = None
    assert call(agent, "POST", "/live/undo")[0] == 200


def test_an_undo_is_busy_while_a_lifecycle_command_runs():
    started, release = asyncio.Event(), asyncio.Event()

    async def slow(args):
        started.set()
        await release.wait()
        return Outcome(True, 0, "", "")

    agent, fake = live_agent(run=slow)
    call(agent, "POST", "/live/bypass", json={"processor": "drive", "bypassed": False})
    fake.calls.clear()

    async def go():
        async with TestClient(TestServer(agent.app())) as client:
            lifecycle = asyncio.ensure_future(client.post("/rig/up", json={"config_name": "electric_board"}))
            await started.wait()
            response = await client.post("/live/undo")
            assert response.status == 409
            assert "busy: 'up' is still running" in (await response.json())["reason"]
            release.set()
            assert (await lifecycle).status == 200

    asyncio.run(go())
    assert fake.calls == [], "nothing touched Sushi while a lifecycle command ran"
    assert len(agent.log) == 1


def test_an_undo_that_sushi_never_answers_is_a_504_and_frees_the_lock(monkeypatch):
    monkeypatch.setattr(agent_module, "LIVE_TIMEOUT", 0.3)
    agent, fake = live_agent()
    call(agent, "POST", "/live/parameter", json={"processor": "reverb", "parameter": "Mix", "value": 0.6})
    fake.hang_on = "set_parameter"

    async def go():
        async with TestClient(TestServer(agent.app())) as client:
            try:
                first = await client.post("/live/undo")
                stuck = (first.status, await first.json())
                second = await asyncio.wait_for(client.get("/live"), 5)
                return stuck, second.status
            finally:
                fake.release.set()
                await asyncio.get_running_loop().run_in_executor(
                    None, _until, lambda: sorted(fake.closed) == [2, 3])

    (status, answer), second = asyncio.run(go())
    assert status == 504 and "may or may not have been applied" in answer["reason"]
    assert second == 200
    assert len(agent.log) == 1, "an undo nobody saw confirmed is not logged"



def test_the_log_limit_is_the_agents_own_and_defaults_to_500():
    agent, _ = live_agent(log_limit=7)
    assert call(agent, "GET", "/live/log")[1]["limit"] == 7
    assert call(live_agent()[0], "GET", "/live/log")[1]["limit"] == 500


def test_a_value_changed_meanwhile_is_not_overwritten_by_an_undo():
    agent, fake = _seeded()
    call(agent, "POST", "/live/undo")  # the move
    call(agent, "POST", "/live/undo")  # the bypass
    fake.info["reverb"]["Mix"]["value"] = 0.9  # the panel turned the knob
    fake.calls.clear()
    status, body = call(agent, "POST", "/live/undo")
    assert (status, body["code"]) == (409, "stale")
    assert "no longer as the change left it" in body["reason"]
    assert fake.writes() == [] and fake.info["reverb"]["Mix"]["value"] == 0.9


def test_a_bypass_changed_meanwhile_is_not_overwritten_by_an_undo():
    agent, fake = _seeded()
    call(agent, "POST", "/live/undo")  # the move
    fake.bypassed["drive"] = True  # the panel switched it back on
    fake.calls.clear()
    status, body = call(agent, "POST", "/live/undo")
    assert (status, body["code"]) == (409, "stale") and fake.writes() == []


def test_a_stale_entry_does_not_wedge_the_undo_stack():
    """The panel's listener moves processors outside the agent's lock, so a
    move can become impossible to undo; without a way out, every older change
    would be unreachable for good."""
    agent, fake = _seeded()
    fake.chain = ["comp", "reverb", "drive"]
    status, body = call(agent, "POST", "/live/undo")
    assert (status, body["code"]) == (409, "stale")
    assert 'skip' in body["reason"], "the refusal says the way out"
    assert call(agent, "POST", "/live/undo")[0] == 409, "and asking again is no way out"

    status, body = call(agent, "POST", "/live/undo", json={"skip": True})
    assert status == 200 and fake.writes() == [], "marked, nothing sent to Sushi"
    assert (body["op"], body["stale"]) == ("stale", 2)
    assert body["skipped"]["op"] == "move"
    assert "no longer where the move left it" in body["args"]["reason"]
    log = call(agent, "GET", "/live/log")[1]["log"]
    assert [e["op"] for e in log] == ["parameter", "bypass", "move", "stale"], "the history says so"
    assert log[-1]["stale"] == 2

    status, body = call(agent, "POST", "/live/undo")  # now the one before
    assert status == 200 and body["undone"]["op"] == "bypass" and body["undoes"] == 1
    assert fake.writes() == [("set_bypass", "drive", True)]


def test_only_an_entry_that_cannot_be_undone_may_be_skipped():
    agent, fake = _seeded()
    status, body = call(agent, "POST", "/live/undo", json={"skip": True})
    assert (status, body["code"]) == (409, "not_stale")
    assert "can be undone" in body["reason"]
    assert fake.writes() == []
    assert len(call(agent, "GET", "/live/log")[1]["log"]) == 3, "nothing marked"


def test_an_entry_with_no_previous_can_be_skipped_too():
    agent, fake = live_agent()
    call(agent, "POST", "/live/bypass", json={"processor": "reverb", "bypassed": True})
    call(agent, "POST", "/live/bypass", json={"processor": "drive", "bypassed": False})
    call(agent, "POST", "/live/undo")  # drive back to True
    assert call(agent, "POST", "/live/undo")[1]["code"] == "stale"  # reverb: Sushi would not say
    status, body = call(agent, "POST", "/live/undo", json={"skip": True})
    assert status == 200 and body["stale"] == 0
    assert call(agent, "POST", "/live/undo")[1]["code"] == "nothing_to_undo"


def test_a_skip_has_nothing_to_skip_in_an_empty_log():
    agent, fake = live_agent()
    status, body = call(agent, "POST", "/live/undo", json={"skip": True})
    assert (status, body["code"], body["reason"]) == (409, "nothing_to_undo", "nothing to undo")
    assert fake.opened == []


def test_skipping_waits_on_busy_and_a_sushi_that_fails_like_any_change():
    from sushi_rig.live import LiveError

    agent, fake = _seeded()
    fake.chain = ["comp", "reverb", "drive"]
    fake.fail, fake.fail_on = LiveError("gone"), "plan_move"
    assert call(agent, "POST", "/live/undo", json={"skip": True})[0] == 502
    assert len(call(agent, "GET", "/live/log")[1]["log"]) == 3, "a skip is judged on what Sushi says"


def test_nothing_to_undo_is_said_with_a_code_and_busy_wins_over_it():
    started, release = asyncio.Event(), asyncio.Event()

    async def slow(args):
        started.set()
        await release.wait()
        return Outcome(True, 0, "", "")

    agent, fake = live_agent(run=slow)

    async def go():
        async with TestClient(TestServer(agent.app())) as client:
            lifecycle = asyncio.ensure_future(client.post("/rig/up", json={"config_name": "electric_board"}))
            await started.wait()
            response = await client.post("/live/undo")  # an empty log
            answer = (response.status, await response.json())
            release.set()
            assert (await lifecycle).status == 200
            return answer

    status, body = asyncio.run(go())
    assert status == 409 and body["code"] == "busy"
    assert body["reason"] == "busy: 'up' is still running", "not 'nothing to undo'"
    status, body = call(agent, "POST", "/live/undo")
    assert (status, body["code"], body["reason"]) == (409, "nothing_to_undo", "nothing to undo")

# --------------------------------------------------------------------------- #
# The runner
# --------------------------------------------------------------------------- #

def test_the_agent_runs_the_lifecycle_itself_through_its_supervisor():
    """No CLI child process any more (#29): a request's steps go to the
    supervisor the agent holds the rig with, and /health reports it."""
    agent = Agent(describe=lambda: dict(DOWN))
    assert agent.run == agent.supervisor.run
    _, health = call(agent, "GET", "/health")
    assert health["agent"]["supervisor"] == {"holds": None, "crashed": [], "problems": [], "note": None}


def test_a_successful_command_carries_its_warnings_for_a_phone_to_read():
    said = Outcome(True, 0, "Rig up: x (headless mode)\n",
                   "warning: amp exited during startup (status 1)\nwarning: 2 link(s) were not made\n")
    agent = Agent(describe=lambda: dict(UP), run=FakeRunner(said))
    status, body = call(agent, "POST", "/rig/up", json={"config_name": "x", "headless": True})
    assert status == 200
    assert body["warnings"] == ["amp exited during startup (status 1)", "2 link(s) were not made"]


# --------------------------------------------------------------------------- #
# Switching rigs live (#93): POST /rig/switch, against a fake Sushi that holds a rig
# --------------------------------------------------------------------------- #
#
# `RigLive` is `FakeLive` holding a whole rig, loaded from a compiled config of
# config/rigs/ as Sushi would load it, so a switch can be played end to end:
# the reading, each step through the routes' own calls, the read-back. Its
# plugins' parameters are every one any rig states for that plugin, starting
# at 0.5 where the config states nothing: invented, since no Sushi was to hand,
# and only ever compared with what the configs state.

import copy as _copy  # noqa: E402
from pathlib import Path as _Path  # noqa: E402

from sushi_rig import rigfile as _rigfile  # noqa: E402
from sushi_rig.agent import Switching, place_switch_add, state_blockers, switched_state  # noqa: E402
from sushi_rig.rig import RigError  # noqa: E402
from sushi_rig.verify import diff_state  # noqa: E402

_ROOT = _Path(__file__).resolve().parents[2]
_COMPILED = {path.stem: _rigfile.emit_rig(_rigfile.load_rig(path))
             for path in sorted((_ROOT / "config" / "rigs").glob("*.yaml"))}
# Every parameter any rig states for a plugin: what the fake gives that plugin.
_PLUGIN_PARAMETERS: dict = {}
for _config in _COMPILED.values():
    _uris = {p["name"]: p.get("uri") for t in _config["tracks"] for p in t["plugins"]}
    for _entry_ in _config.get("initial_state", []):
        if _uris.get(_entry_["processor"]):
            _PLUGIN_PARAMETERS.setdefault(_uris[_entry_["processor"]], set()).update(
                _entry_.get("parameters") or {})


def switch_config(name):
    return _copy.deepcopy(_COMPILED[name])


class RigLive(FakeLive):
    """A Sushi holding the rig `config` loads, and answering every call the
    live routes and a switch make from it."""

    def __init__(self, config):
        super().__init__()
        self.tracks = [t["name"] for t in config["tracks"]]
        self.chains = {t["name"]: [p["name"] for p in t["plugins"]] for t in config["tracks"]}
        self.plugins = {p["name"]: {"uri": p.get("uri"), "uid": p.get("uid"), "type": p.get("type")}
                        for t in config["tracks"] for p in t["plugins"]}
        self.ids = {}
        for name in self.tracks + list(self.plugins):
            self.ids[name] = len(self.ids) + 1
        self.values = {track: dict(_rigfile.UNITY_STRIP) for track in self.tracks}
        for name, plugin in self.plugins.items():
            self.values[name] = self._defaults(plugin)
        self.properties = {name: ({"destination_name": "master_return"} if p["uid"] == _rigfile.SEND_UID else {})
                           for name, p in self.plugins.items()}
        self.bypassed = {name: False for name in self.plugins}
        for entry in config.get("initial_state", []):
            self.values[entry["processor"]].update(entry.get("parameters") or {})
            if "bypassed" in entry:
                self.bypassed[entry["processor"]] = entry["bypassed"]
        self.ignore = None  # (processor, parameter or "bypassed") whose set is confirmed and not applied
        self.drift = None  # called once, at the first reading, to change the rig under the agent

    @staticmethod
    def _defaults(plugin):
        if plugin["uid"] == _rigfile.SEND_UID:
            return {"gain": _rigfile.level_to_gain(0.0)}
        return {name: 0.5 for name in sorted(_PLUGIN_PARAMETERS.get(plugin["uri"], ()))}

    def _track_of(self, name):
        return next((t for t, c in self.chains.items() if name in c), None)

    def snapshot(self, connection):
        self._enter("snapshot", connection)
        if self.drift:
            self.drift, drift = None, self.drift
            drift(self)
        return self.reading()

    def reading(self):
        """What a snapshot would read now, for a test to look at afterwards."""
        processors = {t: {"parameters": dict(self.values[t]), "properties": {}} for t in self.tracks}
        for name in self.plugins:
            processors[name] = {"parameters": dict(self.values[name]), "bypassed": self.bypassed[name],
                                "properties": dict(self.properties[name])}
        return {"processors": processors, "tracks": {t: list(c) for t, c in self.chains.items()},
                "parameter_info": {}}

    def parameter(self, connection, processor, parameter):
        self._enter("parameter", connection, processor, parameter)
        values = self.values.get(processor)
        return {"chains": {t: list(c) for t, c in self.chains.items()},
                "parameters": None if values is None else {n: {"automatable": True} for n in values},
                "value": None if values is None else values.get(parameter)}

    def bypass(self, connection, processor):
        self._enter("bypass", connection, processor)
        return {"chains": {t: list(c) for t, c in self.chains.items()}, "bypassed": self.bypassed.get(processor)}

    def set_parameter(self, connection, processor, parameter, value):
        self._enter("set_parameter", connection, processor, parameter, value)
        if self.ignore != (processor, parameter):
            self.values[processor][parameter] = value

    def set_bypass(self, connection, processor, bypassed):
        self._enter("set_bypass", connection, processor, bypassed)
        if self.ignore != (processor, "bypassed"):
            self.bypassed[processor] = bypassed

    def _uids(self):
        return {name: p["uid"] for name, p in self.plugins.items()}

    def plan_move(self, connection, processor, direction):
        self._enter("plan_move", connection, processor, direction)
        return decide_move({t: list(c) for t, c in self.chains.items()}, dict(self.ids), processor,
                           direction, self._uids())

    def send_move(self, connection, move):
        self._enter("move", connection, move.processor, move.direction)
        chain = self.chains[self._track_of(move.processor)]
        i = chain.index(move.processor)
        chain.insert(i + move.direction, chain.pop(i))
        return replace(move, outcome="moved")

    def walk(self, connection, uids=False):
        self._enter("walk", connection, uids)
        reading = {"chains": {t: list(c) for t, c in self.chains.items()}, "ids": dict(self.ids)}
        if uids:
            reading["uids"] = self._uids()
        return reading

    def removal(self, connection, processor):
        self._enter("removal", connection, processor)
        reading = {"chains": {t: list(c) for t, c in self.chains.items()}, "ids": dict(self.ids),
                   "uids": self._uids()}
        reading["plugin"] = dict(self.plugins[processor]) if processor in self.plugins else None
        return reading

    def add(self, connection, addition):
        self._enter("add", connection, addition.name, addition.track)
        by_id = {i: n for n, i in self.ids.items()}
        chain = self.chains[addition.track]
        if addition.add_to_back:
            chain.append(addition.name)
        else:
            chain.insert(chain.index(by_id[addition.before_id]), addition.name)
        self.ids[addition.name] = max(self.ids.values()) + 1
        self.plugins[addition.name] = {"uri": addition.uri, "uid": None, "type": "lv2"}
        self.values[addition.name] = self._defaults(self.plugins[addition.name])
        self.properties[addition.name] = {}
        self.bypassed[addition.name] = False

    def remove(self, connection, removal):
        self._enter("remove", connection, removal.processor)
        self.chains[removal.track].remove(removal.processor)
        for table in (self.plugins, self.values, self.properties, self.bypassed, self.ids):
            table.pop(removal.processor, None)

    def writes(self):
        return [c for c in self.calls if c[0] in ("set_parameter", "set_bypass", "move", "add", "remove")]


HEADLESS = {"supervisor_pid": 4242, "pgid": 4242, "mode": "headless", "config_name": "electric-delay",
            "config": "/run/rigs/electric-delay.json", "rig": "config/rigs/electric-delay.yaml",
            "tuner_requested": True, "amp_requested": True, "amp_model_requested": None,
            "machine": "/m/box.yaml", "children": {"sushi": 4243}}


class SwitchWorld:
    """The state file, the compiled configs and what was written, for a
    switch: a dict of rig name to compiled config stands in for config/rigs."""

    def __init__(self, running="electric-delay", state=None):
        self.state = dict(state or HEADLESS, config_name=running, config=f"/run/rigs/{running}.json",
                          rig=f"config/rigs/{running}.yaml")
        self.configs = {f"/run/rigs/{running}.json": switch_config(running)}
        self.written_configs: list = []
        self.written_states: list = []
        self.compiled: list = []
        self.bad: dict = {}

    def compile(self, name, root, machine):
        self.compiled.append((name, machine))
        if name in self.bad:
            raise RigError(self.bad[name])
        return switch_config(name)

    def write_config(self, name, config):
        path = f"/run/rigs/{name}.json"
        self.written_configs.append(name)
        self.configs[path] = config
        return path

    def write_state(self, state):
        self.written_states.append(dict(state))
        self.state = dict(state)

    def switching(self):
        return Switching(compile=self.compile, read_config=lambda path: _copy.deepcopy(self.configs[path]),
                         write_config=self.write_config, read_state=lambda: dict(self.state),
                         write_state=self.write_state)

    def described(self):
        return {"verdict": "running", "running": True, "config_name": self.state["config_name"],
                "config": self.state["config"], "rig": self.state["rig"], "mode": self.state["mode"],
                "message": f"{self.state['config_name']} ({self.state['mode']} mode), up 2m"}


def switch_agent(running="electric-delay", state=None, runner=None):
    world = SwitchWorld(running, state)
    fake = RigLive(switch_config(running))
    runner = runner or FakeRunner()
    agent = Agent(describe=world.described, run=runner, live=fake, switching=world.switching())
    return agent, fake, world, runner


def test_a_switch_the_live_operations_can_make_is_made_live_and_verified():
    """electric-delay to electric-octave-up: the same pedals, other settings.
    No restart; the rig read back is the next rig's config; the state file and
    the compiled config name it only afterwards; one log entry."""
    agent, fake, world, runner = switch_agent("electric-delay")
    status, body = call(agent, "POST", "/rig/switch", json={"config_name": "electric-octave-up"})
    assert status == 200, body
    assert body["ok"] and body["how"] == "live" and body["rig"] == "electric-octave-up"
    assert body["previous"] == "electric-delay"
    assert runner.calls == [], "nothing was restarted"
    assert diff_state(switch_config("electric-octave-up"), fake.reading()) == []
    assert world.compiled == [("electric-octave-up", "/m/box.yaml")], "compiled against the run's machine"
    assert world.written_configs == ["electric-octave-up"]
    (state,) = world.written_states
    assert (state["config_name"], state["rig"], state["config"]) == (
        "electric-octave-up", "config/rigs/electric-octave-up.yaml", "/run/rigs/electric-octave-up.json")
    assert state["pgid"] == 4242 and state["children"] == {"sushi": 4243}, "the same supervisor"
    assert body["applied"] == len(body["plan"]) > 0
    (entry,) = call(agent, "GET", "/live/log")[1]["log"]
    assert entry["op"] == "rig" and entry["args"]["how"] == "live"
    assert entry["args"]["rig"] == "electric-octave-up" and entry["previous"] == "electric-delay"
    assert fake.one_connection_per_request_all_closed() and len(fake.opened) == 1


def test_a_switch_that_removes_adds_and_moves_reaches_the_next_rigs_chains():
    """electric-chorus to electric-delay: two removed, three added (the delay
    a different plugin under the same name), two moved."""
    agent, fake, world, runner = switch_agent("electric-chorus")
    status, body = call(agent, "POST", "/rig/switch", json={"config_name": "electric-delay"})
    assert status == 200 and body["how"] == "live", body
    expected = {t["name"]: [p["name"] for p in t["plugins"]] for t in switch_config("electric-delay")["tracks"]}
    assert fake.chains == expected
    assert fake.plugins["delay"]["uri"] == "http://plugin.org.uk/swh-plugins/lcrDelay"
    ops = [c[0] for c in fake.writes()]
    assert {"remove", "add", "move"} <= set(ops)
    assert ops.index("add") > max(i for i, op in enumerate(ops) if op in ("remove", "move"))
    assert diff_state(switch_config("electric-delay"), fake.reading()) == []
    assert runner.calls == []


def test_a_switch_the_live_operations_cannot_make_restarts_and_says_why():
    """accustomed to electric-chorus-2: the amp's settings differ, and the amp
    runs in Carla. A restart, the MIDI switch's: down, then up --rig with the
    options restart would keep. Sushi is not touched; the state file is the
    restart's to write."""
    agent, fake, world, runner = switch_agent("accustomed")
    status, body = call(agent, "POST", "/rig/switch", json={"config_name": "electric-chorus-2"})
    assert status == 200 and body["ok"] and body["how"] == "restart", body
    assert [r["code"] for r in body["why"]] == ["amp"]
    assert runner.calls == [["down"], ["up", "electric-chorus-2", "--rig", "--headless"]]
    assert fake.calls == [] and fake.opened == []
    assert world.written_states == [] and world.written_configs == []
    (entry,) = call(agent, "GET", "/live/log")[1]["log"]
    assert entry["op"] == "rig" and entry["args"] == {"rig": "electric-chorus-2", "how": "restart", "why": ["amp"]}


def test_a_switch_whose_reading_back_is_not_the_next_rig_restarts_into_it():
    """A value Sushi confirmed and did not apply: verification catches it, and
    rather than leave a rig that is neither, the switch restarts into the next
    one. The state file is not written by the switch."""
    agent, fake, world, runner = switch_agent("electric-delay")
    plan = _rigfile.diff(switch_config("electric-delay"), switch_config("electric-octave-up"))
    step = next(s for s in plan.steps if s.op == "parameter")
    processor, parameter = step.args["processor"], step.args["parameter"]
    fake.ignore = (processor, parameter)
    status, body = call(agent, "POST", "/rig/switch", json={"config_name": "electric-octave-up"})
    assert status == 200 and body["ok"] and body["how"] == "restart", body
    (why,) = body["why"]
    assert why["code"] == "unverified" and f"{processor}: {parameter} did not apply" in why["detail"]
    assert any(f"{processor}: {parameter}" in problem for problem in body["verification"])
    assert runner.calls == [["down"], ["up", "electric-octave-up", "--rig", "--headless"]]
    assert world.written_states == [] and world.written_configs == []
    (entry,) = call(agent, "GET", "/live/log")[1]["log"]
    assert entry["args"]["how"] == "restart" and entry["args"]["why"] == ["unverified"]


def test_a_switch_that_fails_part_way_restarts_into_the_next_rig():
    agent, fake, world, runner = switch_agent("electric-chorus")
    fake.fail, fake.fail_on = RuntimeError("Sushi said no"), "add"
    status, body = call(agent, "POST", "/rig/switch", json={"config_name": "electric-delay"})
    assert status == 200 and body["how"] == "restart", body
    (why,) = body["why"]
    assert why["code"] == "failed" and "Sushi said no" in why["detail"]
    assert f"after {body['applied']} of {len(body['plan'])} steps" in why["detail"]
    assert runner.calls == [["down"], ["up", "electric-delay", "--rig", "--headless"]]
    assert fake.one_connection_per_request_all_closed()


def test_a_rig_changed_since_it_came_up_is_planned_from_the_reading_or_restarted():
    """A knob turned during the last song is put right live; a chain changed
    (a pedal added from the panel) is a restart, since the plan would place by
    positions that are not there."""
    agent, fake, world, runner = switch_agent("electric-delay")
    fake.drift = lambda rig: rig.values["compressor"].update(RATIO=0.99)
    status, body = call(agent, "POST", "/rig/switch", json={"config_name": "electric-octave-up"})
    assert status == 200 and body["how"] == "live", body
    assert fake.values["compressor"]["RATIO"] != 0.99

    agent, fake, world, runner = switch_agent("electric-delay")
    fake.drift = lambda rig: rig.chains["board"].insert(0, "slap")
    status, body = call(agent, "POST", "/rig/switch", json={"config_name": "electric-octave-up"})
    assert status == 200 and body["how"] == "restart", body
    assert [r["code"] for r in body["why"]] == ["drifted"] and body["applied"] == 0
    assert fake.writes() == [], "nothing applied to a rig the plan was not made for"


def test_a_rig_run_with_its_panel_or_an_amp_override_restarts():
    """The panel's SAVE listener was started for the running rig's file; the
    amp override belonged to the rig being left. Both said, beside the rest."""
    agent, fake, world, runner = switch_agent("electric-delay", dict(HEADLESS, mode="panel"))
    status, body = call(agent, "POST", "/rig/switch", json={"config_name": "electric-octave-up"})
    assert body["how"] == "restart" and [r["code"] for r in body["why"]] == ["panel"], body
    assert runner.calls[1] == ["up", "electric-octave-up", "--rig"]
    assert [r.code for r in state_blockers(dict(HEADLESS, amp_model_requested="X.nam"))] == ["amp_override"]
    assert state_blockers(dict(HEADLESS, amp_model_requested="X.nam", amp_requested=False)) == []


def test_a_switch_to_a_rig_that_does_not_compile_is_refused_with_the_rig_left_playing():
    agent, fake, world, runner = switch_agent()
    world.bad["electric-octave-up"] = "electric-octave-up.yaml: chain entry 3 has no name"
    status, body = call(agent, "POST", "/rig/switch", json={"config_name": "electric-octave-up"})
    assert status == 409 and body["code"] == "rig_problem" and "chain entry 3" in body["reason"]
    assert runner.calls == [] and fake.calls == [] and call(agent, "GET", "/live/log")[1]["log"] == []
    assert call(agent, "GET", "/health")[1]["agent"]["last_error"]["kind"] == "lifecycle"


@pytest.mark.parametrize("body", [None, {}, {"config_name": "../x"}, {"config_name": 3}, ["electric-delay"]])
def test_a_switch_request_names_a_rig_and_nothing_else(body):
    agent, fake, world, runner = switch_agent()
    status, answer = call(agent, "POST", "/rig/switch", **({} if body is None else {"json": body}))
    assert status == 400, answer
    assert runner.calls == [] and fake.calls == [] and world.compiled == []


def test_a_switch_with_no_rig_up_is_refused():
    agent, fake, world, runner = switch_agent()
    agent.describe = lambda: dict(DOWN)
    status, body = call(agent, "POST", "/rig/switch", json={"config_name": "electric-octave-up"})
    assert status == 409 and body["code"] == "no_rig"
    assert runner.calls == [] and world.compiled == []


def test_a_switch_is_busy_while_a_lifecycle_command_runs_and_live_changes_are_busy_during_one():
    agent, fake, world, runner = switch_agent()

    async def go():
        async with TestClient(TestServer(agent.app())) as client:
            await agent._lock.acquire()
            agent.busy = "up"
            try:
                r = await client.post("/rig/switch", json={"config_name": "electric-octave-up"})
                return r.status, await r.json()
            finally:
                agent.busy = None
                agent._lock.release()

    status, body = asyncio.run(go())
    assert status == 409 and body["code"] == "busy" and world.compiled == []

    agent, fake, world, runner = switch_agent()
    seen = {}

    def during(rig):  # at the first reading, a live change asks to come in
        seen["busy"] = agent.busy

    fake.drift = during
    status, body = call(agent, "POST", "/rig/switch", json={"config_name": "electric-octave-up"})
    assert status == 200 and seen["busy"] == "switch"


def test_undo_stops_at_a_live_switch():
    agent, fake, world, runner = switch_agent("electric-delay")
    agent.log.append(log_entry("bypass", {"processor": "eq", "bypassed": True}, False, "t"))
    call(agent, "POST", "/rig/switch", json={"config_name": "electric-octave-up"})
    status, body = call(agent, "POST", "/live/undo")
    assert status == 409 and body["code"] == "nothing_to_undo" and "rig switch" in body["reason"]


def test_a_switch_whose_restart_fails_brings_the_previous_rig_back_and_says_so():
    class UpOfNextFails(FakeRunner):
        async def __call__(self, args):
            self.calls.append(list(args))
            if args[:2] == ["up", "electric-chorus-2"]:
                return Outcome(False, 1, "", "Sushi never came up")
            return Outcome(True, 0, "", "")

    runner = UpOfNextFails()
    agent, fake, world, _ = switch_agent("accustomed", runner=runner)
    status, body = call(agent, "POST", "/rig/switch", json={"config_name": "electric-chorus-2"})
    assert status == 409 and not body["ok"] and body["how"] == "restart"
    assert runner.calls[-1] == ["up", "accustomed", "--rig", "--headless"]
    assert "Sushi never came up" in body["reason"] and "accustomed was brought back" in body["reason"]
    (entry,) = call(agent, "GET", "/live/log")[1]["log"]
    assert entry["op"] == "rig" and entry["args"]["ok"] is False, "a barrier still: the rig was restarted"


def test_the_state_after_a_live_switch_names_the_next_rig_and_keeps_the_rest():
    after = switched_state(HEADLESS, "lead", "/run/rigs/lead.json")
    assert (after["config_name"], after["rig"], after["config"]) == ("lead", "config/rigs/lead.yaml",
                                                                   "/run/rigs/lead.json")
    assert {k: v for k, v in after.items() if k not in ("config_name", "rig", "config", "switched_at")} == \
        {k: v for k, v in HEADLESS.items() if k not in ("config_name", "rig", "config")}


def test_an_add_goes_in_front_of_what_follows_it_and_never_behind_a_send():
    reading = {"chains": {"board": ["comp", "board_send"]}, "ids": {"board": 1, "comp": 2, "board_send": 3},
               "uids": {"board_send": _rigfile.SEND_UID}}
    addition = place_switch_add(reading, "board", "eq", "urn:eq", "board_send")
    assert (addition.expected, addition.before_id, addition.add_to_back) == (["comp", "eq", "board_send"], 3, False)
    front = place_switch_add(reading, "board", "eq", "urn:eq", "comp")
    assert front.expected == ["eq", "comp", "board_send"] and front.after is None
    from sushi_rig.agent import Conflict
    for track, name, before in [("board", "eq", None), ("nope", "eq", "comp"), ("board", "comp", "board_send"),
                                ("board", "eq", "drive")]:
        with pytest.raises(Conflict):
            place_switch_add(reading, track, name, "urn:eq", before)



# Review of the branch (#93): what the first version let through.

def test_a_rig_file_with_a_yaml_typo_is_refused_as_a_rig_problem_not_a_500(tmp_path):
    """The real compile, on a file that is not YAML: refused with the file
    named, said in /health, the running rig untouched."""
    (tmp_path / "config" / "rigs").mkdir(parents=True)
    (tmp_path / "config" / "rigs" / "typo.yaml").write_text("kind: [rig\nname: typo\n")
    world = SwitchWorld("electric-delay")
    world.compile = Switching().compile
    fake, runner = RigLive(switch_config("electric-delay")), FakeRunner()
    agent = Agent(root=tmp_path, describe=world.described, run=runner, live=fake, switching=world.switching())
    status, body = call(agent, "POST", "/rig/switch", json={"config_name": "typo"})
    assert status == 409 and body["code"] == "rig_problem", body
    assert "typo.yaml" in body["reason"] and "Error" in body["reason"]
    error = call(agent, "GET", "/health")[1]["agent"]["last_error"]
    assert error["kind"] == "lifecycle" and "typo.yaml" in error["message"]
    assert runner.calls == [] and fake.calls == []


@pytest.mark.parametrize("drift, ignored", [
    (lambda rig: rig.values["board_send"].update(gain=0.2), ("board_send", "gain")),
    (lambda rig: rig.bypassed.update(board_send=True), ("board_send", "bypassed")),
], ids=["a send's gain put back to unity", "an un-bypass the next rig leaves unstated"])
def test_a_set_sushi_acknowledged_and_did_not_apply_is_caught_even_where_the_config_states_nothing(
        drift, ignored):
    """The next config states neither, so `diff_state` alone passed them; the
    plan set them, and is checked against its own targets."""
    agent, fake, world, runner = switch_agent("electric-delay")
    fake.drift, fake.ignore = drift, ignored
    status, body = call(agent, "POST", "/rig/switch", json={"config_name": "electric-octave-up"})
    assert status == 200 and body["how"] == "restart", body
    (why,) = body["why"]
    assert why["code"] == "unverified" and f"{ignored[0]}: " in why["detail"]
    assert any(p.startswith(f"{ignored[0]}: ") and "reads back" in p for p in body["verification"])
    assert runner.calls == [["down"], ["up", "electric-octave-up", "--rig", "--headless"]]
    assert world.written_states == []


def test_a_switch_that_fails_part_way_and_cannot_be_taken_down_is_said_to_be_half_switched():
    """Sushi holds part of the next rig and the state file names the previous
    one: said as that, naming both, in the answer and /health. No recovery is
    guessed at: nothing else is run."""
    runner = FakeRunner(Outcome(False, 1, "", "could not stop the running rig"))
    agent, fake, world, _ = switch_agent("electric-chorus", runner=runner)
    fake.fail, fake.fail_on = RuntimeError("Sushi said no"), "add"
    status, body = call(agent, "POST", "/rig/switch", json={"config_name": "electric-delay"})
    assert status == 409 and not body["ok"] and body["half_switched"] is True, body
    assert body["applied"] > 0
    for said in (body["reason"], call(agent, "GET", "/health")[1]["agent"]["last_error"]["message"]):
        assert "HALF-SWITCHED" in said and "from electric-chorus to electric-delay" in said
        assert "could not stop the running rig" in said and "state file still names electric-chorus" in said
    assert runner.calls == [["down"]], "nothing guessed after the down failed"
    assert world.written_states == []


def test_a_down_that_fails_before_anything_was_applied_is_not_half_switched():
    runner = FakeRunner(Outcome(False, 1, "", "could not stop the running rig"))
    agent, fake, world, _ = switch_agent("accustomed", runner=runner)
    status, body = call(agent, "POST", "/rig/switch", json={"config_name": "electric-chorus-2"})
    assert status == 409 and "half_switched" not in body and "HALF" not in body["reason"]


def test_an_add_never_goes_in_front_of_the_masters_return():
    from sushi_rig.agent import Conflict

    reading = {"chains": {"master": ["master_return", "limiter"]},
               "ids": {"master": 1, "master_return": 2, "limiter": 3},
               "uids": {"master_return": _rigfile.RETURN_UID}}
    with pytest.raises(Conflict, match="master's return"):
        place_switch_add(reading, "master", "eq", "urn:eq", "master_return")
    renamed = {**reading, "chains": {"master": ["ret", "limiter"]}, "ids": {"master": 1, "ret": 2, "limiter": 3},
               "uids": {"ret": _rigfile.RETURN_UID}}
    with pytest.raises(Conflict, match="master's return"):
        place_switch_add(renamed, "master", "eq", "urn:eq", "ret")
    assert place_switch_add(reading, "master", "eq", "urn:eq", "limiter").expected == \
        ["master_return", "eq", "limiter"]
    assert place_switch_add(reading, "master", "eq", "urn:eq", None).expected == \
        ["master_return", "limiter", "eq"]


# --------------------------------------------------------------------------- #
# An amp model by path, from this machine only
# --------------------------------------------------------------------------- #

def test_an_amp_model_path_is_taken_from_this_machine_and_refused_from_elsewhere():
    body = {"config_name": "x", "amp_model": "/home/p/models/M.nam"}
    assert lifecycle_arguments("up", body, local=True) == ["up", "x", "--amp-model", "/home/p/models/M.nam"]
    with pytest.raises(AgentError, match="a path is taken only from this machine"):
        lifecycle_arguments("up", body)
    for bad in ("amp/models/M.nam", "/x\n--headless"):
        with pytest.raises(AgentError):
            lifecycle_arguments("up", {"config_name": "x", "amp_model": bad}, local=True)


@pytest.mark.parametrize("remote, local", [
    ("127.0.0.1", True), ("127.0.1.1", True), ("::1", True), ("::ffff:127.0.0.1", True),
    ("192.168.1.20", False), ("10.0.0.2", False), ("::ffff:192.168.1.2", False), (None, False), ("", False),
])
def test_only_a_loopback_peer_is_this_machine(remote, local):
    assert agent_module.is_local(remote) is local


def test_the_cli_on_this_machine_can_name_a_model_by_path():
    runner = FakeRunner()
    agent = Agent(describe=lambda: dict(UP), run=runner)
    status, _ = call(agent, "POST", "/rig/up", json={"config_name": "x", "amp_model": "/m/M.nam"})
    assert status == 200, "a TestClient is a loopback peer"
    assert runner.calls == [["up", "x", "--amp-model", "/m/M.nam"]]
