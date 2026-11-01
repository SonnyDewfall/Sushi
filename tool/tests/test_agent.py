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

import pytest

aiohttp = pytest.importorskip("aiohttp")
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from sushi_rig import agent as agent_module  # noqa: E402
from sushi_rig.agent import (  # noqa: E402
    Agent,
    AgentError,
    Outcome,
    lifecycle_arguments,
    run_cli,
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
