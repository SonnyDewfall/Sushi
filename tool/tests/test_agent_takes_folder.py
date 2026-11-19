"""The takes folder from the agent (#189): `/health`'s `agent.takes`, and
POST /takes/open, which opens it in this machine's file manager for the
rack's Open folder on the laptop.

The agent is served by aiohttp's test server on 127.0.0.1, so every request
here is from a loopback peer unless `from_peer` says otherwise: a middleware
in front of the agent's own, which hands the agent the request as one from
that address, as a phone on the LAN would send it. The openers are stand-ins:
small Python programs that write down what they were given. Nothing here
opens a folder on a desktop; neither `xdg-open` nor `gio open` is run.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
import time

import pytest

aiohttp = pytest.importorskip("aiohttp")
from aiohttp import web  # noqa: E402
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from sushi_rig import agent as agent_module  # noqa: E402
from sushi_rig.agent import Agent, folder_opener, open_folder  # noqa: E402
from sushi_rig.supervisor import Outcome  # noqa: E402

DOWN = {"verdict": "absent", "running": False, "message": "No rig is running."}
PHONE = "192.168.1.23"


@pytest.fixture(autouse=True)
def _agents_release_their_pools(monkeypatch):
    """As test_agent.py's: each Agent's pool of Sushi-call threads is shut."""
    made = []
    original = Agent.__init__

    def recording(self, *args, **kwargs):
        original(self, *args, **kwargs)
        made.append(self)

    monkeypatch.setattr(Agent, "__init__", recording)
    yield
    for agent in made:
        agent._executor.shutdown(wait=True, cancel_futures=True)


async def _ran(args):
    return Outcome(True, 0, "", "")


def make_agent(tmp_path, made=True):
    takes = tmp_path / "takes"
    if made:
        takes.mkdir()
    return Agent(describe=lambda: dict(DOWN), run=_ran, takes_dir=takes)


def recording_opener(tmp_path, then="", exit_code=0):
    """An opener that writes the arguments it was given to a file, then runs
    `then` (Python) and exits `exit_code`; the file it writes, read back."""
    seen = tmp_path / "opener-saw.json"
    script = (f"import json, sys, time\nopen({str(seen)!r}, 'w').write(json.dumps(sys.argv[1:]))\n"
              f"{then}\nsys.exit({exit_code})\n")
    return [sys.executable, "-c", script], seen


def from_peer(app, address):
    """`app` with every request handed on as from `address`."""
    @web.middleware
    async def peer(request, handler):
        return await handler(request.clone(remote=address))

    app.middlewares.insert(0, peer)
    return app


def send(agent, method, path, peer=None, **kwargs):
    async def go():
        app = agent.app() if peer is None else from_peer(agent.app(), peer)
        async with TestClient(TestServer(app)) as client:
            response = await client.request(method, path, **kwargs)
            return response.status, await response.json()

    return asyncio.run(go())


# --------------------------------------------------------------------------- #
# /health
# --------------------------------------------------------------------------- #

def test_health_names_the_takes_folder_and_whether_this_peer_may_open_it(tmp_path):
    agent = make_agent(tmp_path, made=False)
    _, here = send(agent, "GET", "/health")
    _, phone = send(agent, "GET", "/health", peer=PHONE)
    folder = str((tmp_path / "takes").resolve())
    assert here["agent"]["takes"] == {"dir": folder, "can_open": True}
    assert phone["agent"]["takes"] == {"dir": folder, "can_open": False}, "a phone gets the path, not the button"


# --------------------------------------------------------------------------- #
# POST /takes/open
# --------------------------------------------------------------------------- #

def test_open_runs_the_opener_on_the_takes_folder_and_nothing_else(tmp_path):
    agent = make_agent(tmp_path)
    opener, seen = recording_opener(tmp_path)
    agent.folder_opener = lambda: opener
    status, body = send(agent, "POST", "/takes/open", json={})
    folder = str((tmp_path / "takes").resolve())
    assert status == 200 and body["ok"] is True and body["folder"] == folder
    assert json.loads(seen.read_text()) == [folder], "one argument, the agent's own folder: no shell, no options"
    assert body["said"] == f"{os.path.basename(sys.executable)} -c {opener[2]} opened {folder}"


@pytest.mark.parametrize("peer", [PHONE, "10.0.0.5", "::ffff:192.168.1.23", "fe80::1"])
def test_open_is_refused_for_a_peer_that_is_not_this_machine(tmp_path, peer):
    agent = make_agent(tmp_path)
    opener, seen = recording_opener(tmp_path)
    agent.folder_opener = lambda: opener
    status, body = send(agent, "POST", "/takes/open", peer=peer, json={})
    assert (status, body["code"]) == (403, "not_local")
    assert f"this came from {peer}" in body["reason"]
    assert str((tmp_path / "takes").resolve()) in body["reason"], "the path is still said"
    assert not seen.exists(), "nothing was run"


@pytest.mark.parametrize("body", [{"path": "/etc"}, {"folder": "/"}, ["/tmp"], "x"])
def test_open_takes_no_path_from_the_request(tmp_path, body):
    agent = make_agent(tmp_path)
    opener, seen = recording_opener(tmp_path)
    agent.folder_opener = lambda: opener
    status, said = send(agent, "POST", "/takes/open", json=body)
    assert status == 400 and "must be empty" in said["reason"]
    assert not seen.exists()


def test_with_no_opener_installed_it_says_so_plainly_and_where_the_takes_are(tmp_path):
    agent = make_agent(tmp_path)
    agent.folder_opener = lambda: None
    status, body = send(agent, "POST", "/takes/open")
    assert (status, body["code"]) == (409, "no_opener")
    assert body["reason"] == (f"nothing here opens a folder: neither xdg-open nor gio open is installed. "
                              f"The takes are in {(tmp_path / 'takes').resolve()}")


def test_before_the_first_take_there_is_no_folder_to_open(tmp_path):
    agent = make_agent(tmp_path, made=False)
    opener, seen = recording_opener(tmp_path)
    agent.folder_opener = lambda: opener
    status, body = send(agent, "POST", "/takes/open")
    assert (status, body["code"]) == (409, "no_folder")
    assert "made by the first take" in body["reason"]
    assert not seen.exists() and not (tmp_path / "takes").exists(), "the folder is not made here"


def test_an_opener_that_fails_is_said_with_what_it_said(tmp_path):
    agent = make_agent(tmp_path)
    opener, _ = recording_opener(tmp_path, then="sys.stderr.write('cannot open display\\n')", exit_code=4)
    agent.folder_opener = lambda: opener
    status, body = send(agent, "POST", "/takes/open")
    assert (status, body["code"]) == (502, "open_failed")
    assert "it exited 4: cannot open display" in body["reason"]


def test_the_loop_is_not_held_while_the_opener_runs(tmp_path):
    """/health answers while an open is still waiting for its opener."""
    agent = make_agent(tmp_path)
    opener, _ = recording_opener(tmp_path, then="time.sleep(0.8)")
    agent.folder_opener = lambda: opener
    done = []

    async def go():
        async with TestClient(TestServer(agent.app())) as client:
            async def ask(method, path):
                response = await client.request(method, path)
                done.append((path, response.status))

            opening = asyncio.ensure_future(ask("POST", "/takes/open"))
            await asyncio.sleep(0.2)
            await ask("GET", "/health")
            await opening

    asyncio.run(go())
    assert done == [("/health", 200), ("/takes/open", 200)]


def test_an_opener_still_running_is_left_to_run_and_reaped_after(tmp_path):
    pid_file = tmp_path / "pid"
    script = f"import os, time\nopen({str(pid_file)!r}, 'w').write(str(os.getpid()))\ntime.sleep(0.6)\n"
    folder = tmp_path / "takes"
    folder.mkdir()
    assert open_folder([sys.executable, "-c", script], folder, wait=0.2) == {"exited": None}
    [reaper] = [t for t in threading.enumerate() if t.name == "takes-folder-opener"]
    reaper.join(10)
    assert not reaper.is_alive()
    pid = int(pid_file.read_text())
    deadline = time.monotonic() + 5
    with pytest.raises(ProcessLookupError):  # waited for: gone, not a zombie
        while time.monotonic() < deadline:
            os.kill(pid, 0)
            time.sleep(0.05)


def test_the_opener_is_xdg_open_else_gio_open_else_none():
    on = lambda *there: (lambda name: f"/usr/bin/{name}" if name in there else None)  # noqa: E731
    assert folder_opener(on("xdg-open", "gio")) == ["/usr/bin/xdg-open"]
    assert folder_opener(on("gio")) == ["/usr/bin/gio", "open"]
    assert folder_opener(on()) is None
    assert agent_module.FOLDER_OPENERS == (("xdg-open",), ("gio", "open"))
