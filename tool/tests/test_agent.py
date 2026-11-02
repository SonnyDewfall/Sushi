"""The rig agent (#29), first slice: lifecycle and observation over HTTP.

The routes are tested against fakes for everything that touches the world --
the state file, the examination, the child process -- so these say what the
agent *decides*: what runs for a request, what is refused before anything
runs, what one failure does to the next request. One test runs the real CLI
through the real runner, because a runner that only ever met a fake would be
the mock agreeing with itself.
"""

from __future__ import annotations

import asyncio
import sys
import threading
from dataclasses import replace

import pytest

aiohttp = pytest.importorskip("aiohttp")
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from sushi_rig import agent as agent_module  # noqa: E402
from sushi_rig.live import decide_move  # noqa: E402
from sushi_rig.agent import (  # noqa: E402
    Agent,
    AgentError,
    Live,
    Outcome,
    bypass_request,
    check_bypass,
    check_move,
    check_parameter,
    lifecycle_arguments,
    log_entry,
    move_request,
    parameter_request,
    run_cli,
    trim_log,
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
    agent, fake = live_agent()
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
# The runner, for real
# --------------------------------------------------------------------------- #

def test_the_runner_runs_the_real_cli(runtime):
    """`status` with nothing recorded: the one command safe to run here."""
    outcome = asyncio.run(run_cli(["status"]))
    assert outcome.ok, outcome
    assert "No rig is running." in outcome.stdout


def test_a_command_that_hangs_is_killed_and_said_so():
    sleeper = (sys.executable, "-c", "import time; time.sleep(30)")
    outcome = asyncio.run(run_cli(["x"], timeout=0.3, program=sleeper))
    assert not outcome.ok and outcome.abandoned
    assert "did not finish within 0.3s" in outcome.stderr
    assert "still running: read /rig" in outcome.stderr, "killed the CLI, not what it started"


def test_the_cli_the_agent_runs_is_the_installation_it_was_started_from():
    assert agent_module.CLI[0] == sys.executable
    assert agent_module.CLI[1:] == ("-m", "sushi_rig.cli")
