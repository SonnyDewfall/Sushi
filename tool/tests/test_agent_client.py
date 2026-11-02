"""The CLI's choice between the agent and acting directly (#29).

`up`, `down`, `restart` and `status` ask the agent when it is running and act
as they always have when it is not, saying in one line which. Both ways are
pinned here through `cli._main` itself, with the agent and the direct path
stood in; the client is then run for real against a served agent (its routes
real, its runner a fake), against a port with nothing on it, and against a
port with something on it that is not the agent.
"""

from __future__ import annotations

import argparse
import asyncio
import http.server
import os
import socket
import threading
from pathlib import Path

import pytest

from sushi_rig import agent_client, cli, rig
from sushi_rig.agent_client import Absent, Unusable


def namespace(**over):
    values = dict(config_name="guitar_vocal", rig=False, headless=False, tuner=True,
                  amp=True, amp_model=None, force=False)
    values.update(over)
    return argparse.Namespace(**values)


@pytest.fixture(autouse=True)
def _private_runtime(runtime):
    """A private runtime dir, so an agent.json of a real agent is never read."""
    return runtime


HEALTH = {"ok": True, "agent": {"supervisor": {"holds": None, "crashed": [], "note": None}},
          "rig": {"verdict": "absent", "running": False, "message": "No rig is running."}}


# --------------------------------------------------------------------------- #
# The decisions, pure
# --------------------------------------------------------------------------- #

def test_each_command_becomes_the_route_and_body_the_cli_would_have_run():
    assert agent_client.lifecycle_request("up", namespace()) == ("/rig/up", {
        "config_name": "guitar_vocal", "rig": False, "headless": False, "tuner": True, "amp": True})
    assert agent_client.lifecycle_request(
        "up", namespace(rig=True, headless=True, tuner=False, amp=False, amp_model="M.nam"),
    ) == ("/rig/up", {"config_name": "guitar_vocal", "rig": True, "headless": True,
                      "tuner": False, "amp": False, "amp_model": "M.nam"})
    assert agent_client.lifecycle_request("down", namespace(force=True)) == ("/rig/down", {"force": True})
    assert agent_client.lifecycle_request("restart", namespace()) == ("/rig/restart", {})


def test_the_answer_is_printed_as_the_cli_would_have_printed_it():
    ok = {"ok": True, "output": "Rig up: x (panel mode)\n", "warnings": ["the tuner is deaf"]}
    assert agent_client.lifecycle_result(200, ok) == (
        0, "Rig up: x (panel mode)\n", "warning: the tuner is deaf\n")
    refused = {"ok": False, "output": "", "reason": "a rig is already running: x"}
    assert agent_client.lifecycle_result(409, refused) == (1, "", "a rig is already running: x\n")
    code, _, problem = agent_client.lifecycle_result(400, {"ok": False, "reason": "invalid amp model 'a/b'"})
    assert code == 1 and "invalid amp model" in problem and "--direct" in problem
    assert agent_client.lifecycle_result(500, ["nonsense"])[0] == 1


def test_status_through_the_agent_says_what_status_says_and_the_crashes_too():
    running = {
        "verdict": "running", "running": True, "message": "board (headless mode), up 5s",
        "config": "/c/board.json", "rig": None, "pgid": 4000, "amp_model": None,
        "log_dir": "/logs", "children": {"sushi": {"pid": 4002, "alive": True},
                                         "fmit": {"pid": 4001, "alive": False}},
    }
    health = {"rig": running, "agent": {"supervisor": {"crashed": [
        {"name": "fmit", "pid": 4001, "returncode": 3, "signal": None, "at": "T",
         "log": "/logs/fmit.log"}], "note": None}}}
    health["agent"]["supervisor"]["problems"] = [{"kind": "holder_lost", "message": "the group holder died"}]
    text = agent_client.status_text(health)
    assert text.splitlines()[: len(rig.status_lines(running))] == rig.status_lines(running)
    assert "  CRASHED    fmit (pid 4001) exited 3 at T — see /logs/fmit.log" in text
    assert "  PROBLEM    the group holder died" in text


# --------------------------------------------------------------------------- #
# The choice, both ways, through the CLI
# --------------------------------------------------------------------------- #

@pytest.fixture
def direct(monkeypatch):
    """The direct path stood in, recording what it was asked to do."""
    calls = []
    monkeypatch.setattr(rig, "up", lambda name, **kw: calls.append(("up", name, kw)) or 0)
    monkeypatch.setattr(rig, "down", lambda force=False: calls.append(("down", force)) or 0)
    monkeypatch.setattr(rig, "restart", lambda: calls.append(("restart",)) or 0)
    monkeypatch.setattr(rig, "status", lambda: calls.append(("status",)) or 0)
    return calls


def nothing_listening(base, *args, **kwargs):
    raise Absent(f"nothing is listening at {base}")


def test_with_no_agent_running_each_command_acts_directly_and_says_so(direct, monkeypatch, capsys):
    monkeypatch.setattr(agent_client, "probe", nothing_listening)
    monkeypatch.setattr(agent_client, "ask", lambda *a, **k: pytest.fail("asked an absent agent"))
    assert cli._main(["up", "guitar_vocal", "--headless", "--no-amp"]) == 0
    assert cli._main(["down", "--force"]) == 0
    assert cli._main(["restart"]) == 0
    assert cli._main(["status"]) == 0
    assert direct == [
        ("up", "guitar_vocal", {"headless": True, "tuner": True, "amp": False, "amp_model": None, "rig": False}),
        ("down", True), ("restart",), ("status",),
    ]
    said = capsys.readouterr().err.splitlines()
    assert len(said) == 4 and len(set(said)) == 1
    assert said[0].startswith("sushi-rig: no agent is running at http://127.0.0.1:8029 (the default; "
                              "no agent has written ")
    assert said[0].endswith("agent.json), so acting directly")


def test_with_the_agent_running_each_command_asks_it_and_says_so(direct, monkeypatch, capsys):
    asked = []

    def ask(base, method, path, body=None, timeout=None):
        asked.append((method, path, body))
        return 200, {"ok": True, "output": f"did {path}\n", "warnings": []}

    monkeypatch.setattr(agent_client, "probe", lambda base, timeout=None: HEALTH)
    monkeypatch.setattr(agent_client, "ask", ask)
    assert cli._main(["up", "guitar_vocal", "--rig", "--no-tuner"]) == 0
    assert cli._main(["down"]) == 0
    assert cli._main(["restart"]) == 0
    assert cli._main(["status"]) == 0
    assert direct == [], "nothing was done directly"
    assert asked == [
        ("POST", "/rig/up", {"config_name": "guitar_vocal", "rig": True, "headless": False,
                             "tuner": False, "amp": True}),
        ("POST", "/rig/down", {"force": False}),
        ("POST", "/rig/restart", {}),
    ], "status reads the /health the probe already fetched"
    printed = capsys.readouterr()
    assert printed.err.splitlines() == [
        "sushi-rig: asking the agent at http://127.0.0.1:8029"] * 4
    assert printed.out == "did /rig/up\ndid /rig/down\ndid /rig/restart\nNo rig is running.\n"


def test_a_refusal_from_the_agent_is_the_clis_failure(direct, monkeypatch, capsys):
    monkeypatch.setattr(agent_client, "probe", lambda base, timeout=None: HEALTH)
    monkeypatch.setattr(agent_client, "ask", lambda *a, **k: (409, {
        "ok": False, "output": "", "reason": "busy: 'up' is still running"}))
    assert cli._main(["down"]) == 1
    assert "busy: 'up' is still running" in capsys.readouterr().err
    assert direct == []


def test_direct_never_asks_and_says_so(direct, monkeypatch, capsys):
    monkeypatch.setattr(agent_client, "probe", lambda *a, **k: pytest.fail("probed with --direct"))
    assert cli._main(["up", "x", "--direct"]) == 0
    assert cli._main(["status", "--direct"]) == 0
    assert [c[0] for c in direct] == ["up", "status"]
    assert capsys.readouterr().err.splitlines() == [
        "sushi-rig: --direct, so acting without asking the agent"] * 2


def test_something_on_the_port_that_is_not_the_agent_is_refused_and_nothing_is_done(direct, monkeypatch, capsys):
    def stuck(base, timeout=None):
        raise Unusable(f"{base} accepted the connection but did not answer within 2s")

    monkeypatch.setattr(agent_client, "probe", stuck)
    with pytest.raises(SystemExit) as exit_:
        cli._main(["up", "x"])
    assert "did not answer within 2s. Nothing was done." in str(exit_.value)
    assert "'sushi-rig up --direct' acts without it" in str(exit_.value)
    assert direct == []


def test_an_agent_that_vanishes_mid_request_is_not_taken_for_an_absent_one(direct, monkeypatch):
    monkeypatch.setattr(agent_client, "probe", lambda base, timeout=None: HEALTH)
    monkeypatch.setattr(agent_client, "ask", nothing_listening)
    with pytest.raises(SystemExit, match="went away before it answered"):
        cli._main(["up", "x"])
    assert direct == [], "acting directly could stack a second rig on the agent's"


# --------------------------------------------------------------------------- #
# The client, for real
# --------------------------------------------------------------------------- #

def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_a_port_with_nothing_on_it_is_absent():
    with pytest.raises(Absent):
        agent_client.probe(f"http://127.0.0.1:{free_port()}")


def test_a_port_that_accepts_and_never_answers_is_unusable():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)  # connections queue; nothing ever reads them
        port = listener.getsockname()[1]
        with pytest.raises(Unusable, match="did not answer within 0.3s"):
            agent_client.probe(f"http://127.0.0.1:{port}", timeout=0.3)


def test_a_web_server_that_is_not_the_agent_is_unusable():
    class Page(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"<html>hello</html>")

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Page)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        with pytest.raises(Unusable, match="not with JSON"):
            agent_client.probe(f"http://127.0.0.1:{server.server_port}")
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_the_cli_against_a_served_agent(monkeypatch, capsys, direct):
    """The real routes and the real client, over a real socket; the agent's
    runner is a fake, so nothing starts."""
    pytest.importorskip("aiohttp")
    from aiohttp import web

    from sushi_rig.agent import Agent, Outcome

    calls = []

    async def runner(args):
        calls.append(list(args))
        return Outcome(True, 0, "Stopping x (headless mode)...\nRig offline.\n", "")

    agent = Agent(describe=lambda: dict(HEALTH["rig"]), run=runner)
    port = free_port()
    ready = threading.Event()
    loop = asyncio.new_event_loop()

    def serve():
        asyncio.set_event_loop(loop)
        site_runner = web.AppRunner(agent.app())
        loop.run_until_complete(site_runner.setup())
        loop.run_until_complete(web.TCPSite(site_runner, "127.0.0.1", port).start())
        ready.set()
        loop.run_forever()
        loop.run_until_complete(site_runner.cleanup())
        # Its worker threads too: one left alive makes a later test's real
        # fork (test_rig's supervisor) a fork of a threaded process.
        loop.run_until_complete(loop.shutdown_default_executor())
        loop.close()

    thread = threading.Thread(target=serve)
    thread.start()
    assert ready.wait(5)
    # As the agent's serve() says where it listens: an address beside the
    # state file, on a port that is not the default.
    agent_client.write_address(f"http://127.0.0.1:{port}", os.getpid())
    try:
        assert cli._main(["down"]) == 0
        assert cli._main(["status"]) == 0
    finally:
        loop.call_soon_threadsafe(loop.stop)
        thread.join(5)
        agent._executor.shutdown(wait=True)
    assert calls == [["down"]]
    assert direct == []
    printed = capsys.readouterr()
    assert printed.out == "Stopping x (headless mode)...\nRig offline.\nNo rig is running.\n"
    assert printed.err.count(f"asking the agent at http://127.0.0.1:{port}") == 2


# --------------------------------------------------------------------------- #
# Where the agent is: what it wrote, or the default
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("host, port, url", [
    ("127.0.0.1", 8029, "http://127.0.0.1:8029"),
    ("0.0.0.0", 9000, "http://127.0.0.1:9000"),
    ("192.168.1.5", 8029, "http://192.168.1.5:8029"),
    ("::", 8029, "http://[::1]:8029"),
])
def test_an_agent_is_reached_where_it_can_be_from_this_machine(host, port, url):
    assert agent_client.agent_url(host, port) == url


def test_the_address_file_is_trusted_only_while_its_agent_lives():
    good = '{"url": "http://127.0.0.1:9000", "pid": 4242}'
    assert agent_client.address_from(good, lambda pid: True) == (
        "http://127.0.0.1:9000", "written by agent pid 4242")
    assert agent_client.address_from(good, lambda pid: False) == (
        None, "names agent pid 4242, which is gone")
    assert agent_client.address_from("[]", lambda pid: True)[0] is None
    assert agent_client.address_from('{"url": "file:///x", "pid": 4242}', lambda pid: True)[0] is None
    assert agent_client.address_from('{"url": "http://x", "pid": true}', lambda pid: True)[0] is None


def test_the_cli_probes_the_address_a_running_agent_wrote_and_says_so(monkeypatch, capsys, direct):
    agent_client.write_address("http://127.0.0.1:9123", os.getpid())
    probed = []

    def absent(base, *args, **kwargs):
        probed.append(base)
        raise Absent(base)

    monkeypatch.setattr(agent_client, "probe", absent)
    assert cli._main(["status"]) == 0
    assert probed == ["http://127.0.0.1:9123"]
    line = capsys.readouterr().err.strip()
    assert line.startswith("sushi-rig: no agent is running at http://127.0.0.1:9123 (from ")
    assert f"written by agent pid {os.getpid()}), so acting directly" in line


def test_a_stale_address_file_is_said_and_the_default_probed(monkeypatch, capsys, direct):
    agent_client.write_address("http://127.0.0.1:9123", 2 ** 22 + 7)  # beyond any pid in use
    probed = []
    monkeypatch.setattr(agent_client, "probe", lambda base, *a, **k: probed.append(base) or nothing_listening(base))
    assert cli._main(["status"]) == 0
    assert probed == ["http://127.0.0.1:8029"]
    assert "which is gone), so acting directly" in capsys.readouterr().err


def test_an_agent_takes_its_address_away_only_if_it_is_its_own():
    from sushi_rig.paths import agent_path

    agent_client.write_address("http://127.0.0.1:9000", 4242)
    agent_client.remove_address(4243)
    assert agent_path().is_file(), "another agent's address is not erased"
    agent_client.remove_address(4242)
    assert not agent_path().exists()


# --------------------------------------------------------------------------- #
# --amp-model by path, through the agent
# --------------------------------------------------------------------------- #

def test_an_amp_model_path_is_made_absolute_against_the_checkout_as_up_would(tmp_path):
    assert agent_client.model_for_agent("Some Amp.nam") == "Some Amp.nam"
    assert agent_client.model_for_agent("/abs/M.nam") == "/abs/M.nam"
    assert agent_client.model_for_agent("amp/models/M.nam", tmp_path) == str(tmp_path / "amp/models/M.nam")


def test_up_with_an_amp_model_path_goes_through_the_agent(monkeypatch, direct):
    from sushi_rig.paths import checkout_root

    asked = []
    monkeypatch.setattr(agent_client, "probe", lambda base, timeout=None: HEALTH)
    monkeypatch.setattr(agent_client, "ask", lambda base, method, path, body=None, timeout=None: (
        asked.append(body) or (200, {"ok": True, "output": "", "warnings": []})))
    assert cli._main(["up", "x", "--amp-model", "../elsewhere/M.nam"]) == 0
    assert asked[0]["amp_model"] == str(checkout_root() / "../elsewhere/M.nam")
