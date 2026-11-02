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

import pytest

aiohttp = pytest.importorskip("aiohttp")
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from sushi_rig import agent as agent_module  # noqa: E402
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


INFO = {
    "reverb": {
        "Mix": {"automatable": True, "value": 0.3, "min_domain_value": 0.0, "max_domain_value": 1.0},
        "Latency OUT": {"automatable": False, "value": 0.0, "min_domain_value": 0.0, "max_domain_value": 1.0},
    },
    "main": {"gain": {"automatable": True, "value": 0.8, "min_domain_value": 0.0, "max_domain_value": 1.0}},
}


def test_a_parameter_the_rig_lacks_or_cannot_set_is_refused_naming_what_it_has():
    assert check_parameter(INFO, "reverb", "Mix") == 0.3, "and says what it replaces"
    with pytest.raises(AgentError, match=r"no processor 'delay'.*\['main', 'reverb'\]"):
        check_parameter(INFO, "delay", "Mix")
    with pytest.raises(AgentError, match=r"no parameter 'Tone'.*'Latency OUT', 'Mix'"):
        check_parameter(INFO, "reverb", "Tone")
    with pytest.raises(AgentError, match="read-only"):
        check_parameter(INFO, "reverb", "Latency OUT")


STATE = {
    "processors": {
        "main": {"parameters": {"gain": 0.8}},
        "comp": {"parameters": {}, "bypassed": False},
        "drive": {"parameters": {}, "bypassed": True},
        "reverb": {"parameters": {}},
    },
    "tracks": {"main": ["comp", "drive", "reverb"]},
}


def test_bypass_is_for_a_processor_of_the_rig_not_a_track():
    assert check_bypass(STATE, "drive") is True
    assert check_bypass(STATE, "comp") is False
    assert check_bypass(STATE, "reverb") is None, "Sushi would not say: unknown, not False"
    with pytest.raises(AgentError, match="is a track"):
        check_bypass(STATE, "main")
    with pytest.raises(AgentError, match="no processor 'nope'"):
        check_bypass(STATE, "nope")


@pytest.mark.parametrize("processor, direction, would_change", [
    ("drive", "up", True), ("drive", "down", True),
    ("comp", "up", False), ("reverb", "down", False),
])
def test_a_move_at_the_end_of_the_chain_is_not_a_change(processor, direction, would_change):
    chain, changes = check_move(STATE, processor, direction)
    assert chain == ["comp", "drive", "reverb"]
    assert changes is would_change


def test_a_move_of_what_is_on_no_track_is_refused():
    for name in ("main", "nope"):
        with pytest.raises(AgentError, match="on any track"):
            check_move(STATE, name, "up")


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
    """A Sushi that records what it was asked and answers from a state."""

    def __init__(self):
        self.calls = []
        self.threads = set()
        self.info = {p: {n: dict(v) for n, v in ps.items()} for p, ps in INFO.items()}
        self.state = {
            "processors": {p: dict(v) for p, v in STATE["processors"].items()},
            "tracks": {t: list(c) for t, c in STATE["tracks"].items()},
        }
        self.fail = None  # an exception to raise from every call
        self.fail_on = None  # ... or only from this one
        self.move_says = None  # what `move` reports, if not "moved ..."
        self.during = None  # called inside set_parameter

    def _enter(self, name, *args):
        self.calls.append((name, *args))
        self.threads.add(threading.get_ident())
        if self.fail is not None and self.fail_on in (None, name):
            raise self.fail

    def capture(self):
        self._enter("capture")
        return self.state

    def parameter_info(self):
        self._enter("parameter_info")
        return self.info

    def set_parameter(self, processor, parameter, value):
        self._enter("set_parameter", processor, parameter, value)
        if self.during:
            self.during()
        self.info[processor][parameter]["value"] = value

    def set_bypass(self, processor, bypassed):
        self._enter("set_bypass", processor, bypassed)
        self.state["processors"][processor]["bypassed"] = bypassed

    def move(self, processor, direction):
        self._enter("move", processor, direction)
        if self.move_says:
            return self.move_says
        chain = self.state["tracks"]["main"]
        i = chain.index(processor)
        chain.insert(i + direction, chain.pop(i))
        return f"moved {processor} {'earlier' if direction < 0 else 'later'}"

    def writes(self):
        return [c for c in self.calls if c[0] in ("set_parameter", "set_bypass", "move")]


def live_agent(fake=None, **kwargs):
    fake = fake or FakeLive()
    kwargs.setdefault("describe", lambda: dict(UP))
    return Agent(live=fake, **kwargs), fake


def test_live_serves_the_running_rig_with_each_parameters_range():
    agent, fake = live_agent()
    status, body = call(agent, "GET", "/live")
    assert status == 200
    assert body["ok"] is True
    assert body["processors"] == STATE["processors"]
    assert body["tracks"] == STATE["tracks"]
    assert body["parameter_info"]["reverb"]["Mix"] == INFO["reverb"]["Mix"], \
        "range and unit come with it"
    assert fake.writes() == []
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


def test_a_move_sushi_did_not_make_is_a_bad_gateway_and_not_logged():
    agent, fake = live_agent()
    fake.move_says = "no processor named 'drive' on any track"
    status, body = call(agent, "POST", "/live/move", json={"processor": "drive", "direction": "down"})
    assert status == 502
    assert "did not happen" in body["reason"] and "no processor named" in body["reason"]
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


def test_a_missing_elkpy_exits_the_process_in_live_py_but_is_an_error_here():
    """`_controller` calls sys.exit; raised in an executor thread that would
    end the event loop, and the agent with it."""
    agent, fake = live_agent()
    fake.fail = SystemExit("elkpy not found. Install it into the tool's venv")
    status, body = call(agent, "GET", "/live")
    assert status == 502
    assert "elkpy not found" in body["reason"]
    assert call(agent, "GET", "/rig")[0] == 200, "and the agent is still serving"
    fake.fail = None
    assert call(agent, "GET", "/live")[0] == 200


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
