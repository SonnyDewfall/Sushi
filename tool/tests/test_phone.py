"""The phone, end to end (#181): `sushi-rig agent --phone`, the port taken,
`install-service --phone`, `/health`'s `agent.listen` for the page, a phone's
request by IP past the cross-site guard, and the guide's "Playing out".

What is real: the agent processes started here (the CLI, in child
processes, bound on this machine), their sockets, /proc, and the bind that
fails. What stands in: the laptop's Wi-Fi and ethernet (`Interface`s written
out, for `/health`), and the phone (requests that carry the headers a phone's
browser sends a page opened by IP). Not met here: a real phone, a LAN or a
router, and systemd (the unit is files; `systemctl` is never run).
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

aiohttp = pytest.importorskip("aiohttp")
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from sushi_rig import cli, reach, service  # noqa: E402
from sushi_rig.agent import Agent, AgentError, cross_site_refusal, own_names, rebinding_refusal, serve  # noqa: E402
from sushi_rig.paths import checkout_root  # noqa: E402
from sushi_rig.reach import Interface  # noqa: E402

from test_agent_rigbin import bin_agent, root  # noqa: E402,F401

SRC = Path(cli.__file__).resolve().parents[1]
GUIDE = checkout_root() / "docs" / "guide"
LAPTOP = [Interface("wlp0s20f3", "192.168.1.150", "Wi-Fi"), Interface("enx00e04c680001", "192.168.1.147", "ethernet")]


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


def health(agent):
    async def go():
        async with TestClient(TestServer(agent.app())) as client:
            return await (await client.get("/health")).json()

    return asyncio.run(go())


# --------------------------------------------------------------------------- #
# /health's agent.listen, which the page draws
# --------------------------------------------------------------------------- #

def test_an_agent_no_serve_started_says_nothing_of_where_it_listens():
    assert health(Agent(describe=lambda: {"running": False}))["agent"]["listen"] is None


def test_a_localhost_agent_says_so_and_offers_no_url():
    agent = Agent(describe=lambda: {"running": False})
    agent.listening = ("127.0.0.1", 8029)
    agent.listen_interfaces = lambda: pytest.fail("no interface is looked at for localhost")
    assert health(agent)["agent"]["listen"] == {"host": "127.0.0.1", "port": 8029, "network": False, "urls": []}


def test_a_phone_agent_gives_the_url_for_each_interface_and_looks_again_after_a_while():
    ticks = iter([0.0, 1.0, 2.0, 100.0, 100.0])
    agent = Agent(describe=lambda: {"running": False}, clock=lambda: next(ticks))
    agent.listening = ("0.0.0.0", 8029)
    seen = [LAPTOP, [Interface("wlp0s20f3", "10.40.226.17", "Wi-Fi")]]
    asked = []

    def interfaces():
        asked.append(1)
        return seen[len(asked) - 1], []

    agent.listen_interfaces = interfaces
    first = agent.listen_status()
    assert first["network"] is True and first["host"] == "0.0.0.0"
    assert [(u["url"], u["kind"]) for u in first["urls"]] == [
        ("http://192.168.1.150:8029/", "Wi-Fi"), ("http://192.168.1.147:8029/", "ethernet")]
    assert agent.listen_status() == first and len(asked) == 1, "within LISTEN_REFRESH it is not looked at again"
    # The laptop joined the phone's hotspot (the issue's 10.40.226.x).
    assert [u["url"] for u in agent.listen_status()["urls"]] == ["http://10.40.226.17:8029/"]
    assert len(asked) == 2


# --------------------------------------------------------------------------- #
# A phone's request, by IP, past the cross-site guard
# --------------------------------------------------------------------------- #

def as_phone(agent, address, origin=None):
    async def go():
        async with TestClient(TestServer(agent.app())) as client:
            headers = {"Host": f"{address}:8029", "Origin": origin or f"http://{address}:8029",
                       "Sec-Fetch-Site": "same-origin"}
            response = await client.post("/rigs/accustomed/delete", json={"dry_run": True}, headers=headers)
            return response.status, await response.json()

    return asyncio.run(go())


@pytest.mark.parametrize("address", ["192.168.1.150", "192.168.1.147", "10.40.226.17", "172.20.10.2", "10.42.0.1"])
def test_a_page_opened_by_the_laptops_address_may_change_the_rig(root, address):
    """The page a phone opened at `http://<address>:8029/` sends that as its
    Host and Origin. A guard that read Host as a name to trust (#161's,
    PR #174, allows any IP) must let it through; here, the guard of the rig
    bin's routes. A dry run, so nothing is moved."""
    status, body = as_phone(bin_agent(root, rig=None), address)
    assert status == 200 and body["how"] == "dry_run", body


# Every address a phone is told to open: the issue's laptop, the phone's
# hotspot (Android's as the issue had it, an iPhone's) and the laptop's own.
PRINTED = LAPTOP + [Interface("wlp0s20f3", "10.40.226.17", "Wi-Fi"), Interface("wlp0s20f3", "172.20.10.2", "Wi-Fi"),
                    Interface("wlp0s20f3", "10.42.0.1", "Wi-Fi")]


@pytest.mark.parametrize("allowed", [(), ("rig.ts.net",)])
def test_every_url_printed_for_the_phone_passes_the_guard_with_or_without_allow_host(allowed):
    """#161's guard (PR #174) over each URL `--phone` prints: the Host a
    phone's browser sends for it, and the Origin its page then sends."""
    for entry in reach.phone_urls("0.0.0.0", 8029, PRINTED):
        netloc = entry["url"].split("/")[2]
        origin = entry["url"].rstrip("/")
        assert rebinding_refusal(netloc, own_names((*allowed, "0.0.0.0"))) is None, entry
        assert cross_site_refusal({"Origin": origin, "Sec-Fetch-Site": "same-origin"}, netloc, allowed) is None, entry


def ask(agent, *requests):
    async def go():
        async with TestClient(TestServer(agent.app())) as client:
            out = []
            for method, path, headers in requests:
                response = await client.request(method, path, headers=headers,
                                                json={} if method == "POST" else None)
                out.append(response.status)
            return out

    return asyncio.run(go())


def test_an_agent_bound_for_the_phone_answers_its_urls_and_allowed_names_and_refuses_others():
    agent = Agent(describe=lambda: {"running": False}, bound="0.0.0.0", allow_hosts=("rig.ts.net",))
    requests = []
    for entry in reach.phone_urls("0.0.0.0", 8029, PRINTED):
        netloc = entry["url"].split("/")[2]
        requests += [("GET", "/health", {"Host": netloc}),
                     # The page's POST: past the guard to the route, which refuses an empty up (400).
                     ("POST", "/rig/up", {"Host": netloc, "Origin": f"http://{netloc}", "Sec-Fetch-Site": "same-origin"})]
    requests += [("GET", "/health", {"Host": "rig.ts.net:8029"}),
                 ("POST", "/rig/up", {"Host": "rig.ts.net:8029", "Origin": "http://rig.ts.net:8029"}),
                 ("GET", "/health", {"Host": "evil.example:8029"}),
                 ("POST", "/rig/up", {"Host": "192.168.1.150:8029", "Origin": "http://evil.example"})]
    said = ask(agent, *requests)
    assert said[:-4] == [200, 400] * len(PRINTED)
    assert said[-4:] == [200, 400, 403, 403]


def test_another_sites_page_is_still_refused_from_the_phone(root):
    status, body = as_phone(bin_agent(root, rig=None), "192.168.1.150", origin="http://evil.example")
    assert status == 403 and body["reason"].startswith("refused"), body


# --------------------------------------------------------------------------- #
# The CLI: --phone, and the port taken
# --------------------------------------------------------------------------- #

def test_phone_is_the_wildcard_and_refuses_another_host_beside_it(capsys):
    assert cli._agent_host(False, "127.0.0.1") == "127.0.0.1"
    assert cli._agent_host(True, "127.0.0.1") == "0.0.0.0"
    assert cli._agent_host(True, "0.0.0.0") == "0.0.0.0"
    with pytest.raises(SystemExit) as refused:
        cli._agent_host(True, "192.168.1.150")
    assert "--phone listens on every address" in str(refused.value)


def test_serve_says_who_has_the_port_before_it_starts_or_writes_anything(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    with socket.socket() as held:
        held.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        held.bind(("127.0.0.1", 0))
        held.listen()
        port = held.getsockname()[1]
        monkeypatch.setattr(reach, "service_main_pid", lambda: None)
        with pytest.raises(AgentError) as refused:
            serve("0.0.0.0", port, root=tmp_path)
    said = str(refused.value)
    assert said.startswith(f"sushi-rig: cannot listen on 0.0.0.0:{port}: the port is already taken.")
    assert f"(pid {os.getpid()})" in said and f"kill {os.getpid()}" in said
    assert not (tmp_path / "sushi-rig" / "agent.json").exists(), "a refused agent writes no address"


def run_agent(env, *args):
    return subprocess.Popen(
        [sys.executable, "-c", "import sys; from sushi_rig.cli import main; sys.exit(main())", "agent", *args],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env,
    )


def wait_for(port, process, timeout=30.0):
    end = time.monotonic() + timeout
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    while time.monotonic() < end:
        if process.poll() is not None:
            pytest.fail(f"the agent exited: {process.stderr.read()}")
        try:
            with opener.open(f"http://127.0.0.1:{port}/health", timeout=1) as answer:
                return json.loads(answer.read())
        except OSError:
            time.sleep(0.1)
    pytest.fail("the agent did not answer")


def stop(process):
    if process.poll() is None:
        process.send_signal(signal.SIGINT)
        try:
            process.wait(10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(10)
    return process.stdout.read(), process.stderr.read()


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
def env(tmp_path):
    return {**os.environ, "PYTHONPATH": str(SRC), "XDG_RUNTIME_DIR": str(tmp_path), "PYTHONUNBUFFERED": "1"}


def test_the_issue_replayed_a_localhost_agent_then_phone_is_told_who_and_how(env):
    """The issue's traceback, replayed with real agents: one on localhost
    holds the port, and `sushi-rig agent --phone` beside it says which pid,
    that it listens on localhost only, and the command that stops it."""
    port = free_port()
    first = run_agent(env, "--port", str(port))
    try:
        said = wait_for(port, first)
        assert said["agent"]["listen"] == {"host": "127.0.0.1", "port": port, "network": False, "urls": []}
        second = subprocess.run(
            [sys.executable, "-c", "import sys; from sushi_rig.cli import main; sys.exit(main())",
             "agent", "--phone", "--port", str(port)],
            capture_output=True, text=True, env=env, timeout=60,
        )
    finally:
        out, err = stop(first)
    assert second.returncode == 1
    assert "Traceback" not in second.stderr and "Errno" not in second.stderr, second.stderr
    assert f"cannot listen on 0.0.0.0:{port}: the port is already taken." in second.stderr
    assert f"Another sushi-rig agent has it (pid {first.pid}), listening on 127.0.0.1." in second.stderr
    assert "which is why a phone cannot reach it" in second.stderr
    assert f"\n  kill {first.pid}\n" in second.stderr
    assert second.stderr.rstrip().endswith(f"  sushi-rig agent --phone --port {port}")
    assert "Only this machine can open it. For a phone as well: sushi-rig agent --phone" in out


def test_agent_phone_listens_on_the_network_and_prints_where_to_open_it(env):
    port = free_port()
    agent = run_agent(env, "--phone", "--port", str(port))
    try:
        said = wait_for(port, agent)
    finally:
        out, err = stop(agent)
    listen = said["agent"]["listen"]
    assert (listen["host"], listen["port"], listen["network"]) == ("0.0.0.0", port, True)
    lines = out.splitlines()
    assert lines[0].startswith(f"sushi-rig agent ") and lines[0].endswith(f"on http://0.0.0.0:{port}  (Ctrl+C to stop)")
    assert reach.NO_PASSWORD in lines
    found = reach.interfaces()[0]
    for interface in found:  # whatever this machine has, each is printed
        assert any(line.startswith(f"  http://{interface.address}:{port}/") for line in lines), out
    assert [u["url"] for u in listen["urls"]] == [f"http://{i.address}:{port}/" for i in found]
    assert not any("127.0.0.1" in u["url"] for u in listen["urls"])


def test_each_printed_url_opens_the_page_on_a_real_agent_and_allow_host_names_too(env):
    """The URLs as printed, fetched for real from this machine's own
    addresses, past #161's guard: the page, and the page's POST (an empty
    `up`, refused by the route as 400, not by the guard as 403). A name
    given with `--allow-host` beside `--phone` passes; another does not."""
    port = free_port()
    agent = run_agent(env, "--phone", "--port", str(port), "--allow-host", "rig.test")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def status(url, method="GET", headers=None):
        request = urllib.request.Request(url, method=method, headers=headers or {},
                                         data=b"{}" if method == "POST" else None)
        try:
            with opener.open(request, timeout=10) as answer:
                return answer.status
        except urllib.error.HTTPError as exc:
            return exc.code

    try:
        said = wait_for(port, agent)
        urls = [u["url"] for u in said["agent"]["listen"]["urls"]]
        got = [(status(url), status(url + "rig/up", "POST", {"Origin": url.rstrip("/"), "Sec-Fetch-Site": "same-origin",
                                                              "Content-Type": "application/json"}))
               for url in urls]
        named = status(f"http://127.0.0.1:{port}/health", headers={"Host": f"rig.test:{port}"})
        other = status(f"http://127.0.0.1:{port}/health", headers={"Host": f"evil.example:{port}"})
    finally:
        stop(agent)
    assert got == [(200, 400)] * len(urls)
    assert (named, other) == (200, 403)


# --------------------------------------------------------------------------- #
# install-service --phone
# --------------------------------------------------------------------------- #

TEMPLATE = (checkout_root() / "systemd" / service.UNIT_NAME).read_text()


@pytest.fixture
def checkout(tmp_path):
    made = tmp_path / "Sushi"
    (made / "systemd").mkdir(parents=True)
    (made / "systemd" / service.UNIT_NAME).write_text(TEMPLATE)
    program = made / "tool" / ".venv" / "bin" / "sushi-rig"
    program.parent.mkdir(parents=True)
    program.write_text("#!/bin/sh\n")
    return made


def test_install_service_phone_writes_the_unit_listening_on_the_network(checkout, tmp_path):
    home = tmp_path / "home"
    lines = service.install(checkout, env={}, home=home, listen=["--phone"], found=LAPTOP)
    unit = (home / ".config" / "systemd" / "user" / service.UNIT_NAME).read_text()
    assert f"\nExecStart={checkout}/tool/.venv/bin/sushi-rig agent --phone\n" in unit
    assert "on 0.0.0.0:8029" in unit and "127.0.0.1:8029" not in unit
    assert "\nKillMode=process\n" in unit
    assert "  http://192.168.1.150:8029/   Wi-Fi, wlp0s20f3" in lines
    assert reach.NO_PASSWORD in lines

    # Back to localhost: the committed unit as it is, and the restart said.
    again = service.install(checkout, env={}, home=home, listen=[])
    assert (home / ".config" / "systemd" / "user" / service.UNIT_NAME).read_text() == \
        service.unit_text(TEMPLATE, checkout)
    assert f"  systemctl --user restart {service.UNIT_NAME}" in again
    assert "  sushi-rig agent install-service --phone" in again


@pytest.mark.parametrize("given, args", [
    ({}, []),
    ({"phone": True}, ["--phone"]),
    ({"host": "0.0.0.0"}, ["--phone"]),
    ({"host": "127.0.0.1", "port": 8029}, []),
    ({"host": "192.168.1.150"}, ["--host", "192.168.1.150"]),
    ({"phone": True, "port": 8030}, ["--phone", "--port", "8030"]),
])
def test_listen_args_are_nothing_for_the_defaults(given, args):
    assert service.listen_args(**given) == args


def test_install_service_phone_carries_allow_host_into_the_unit(checkout, tmp_path):
    home = tmp_path / "home"
    listen = service.listen_args(phone=True, allow_hosts=["rig.ts.net", "rig.ts.net", "rig.local"])
    assert listen == ["--phone", "--allow-host", "rig.ts.net", "--allow-host", "rig.local"]
    lines = service.install(checkout, env={}, home=home, listen=listen, found=LAPTOP)
    unit = (home / ".config" / "systemd" / "user" / service.UNIT_NAME).read_text()
    assert (f"\nExecStart={checkout}/tool/.venv/bin/sushi-rig agent --phone --allow-host rig.ts.net "
            "--allow-host rig.local\n") in unit
    assert any("rig.ts.net, rig.local as well (--allow-host)" in line for line in lines)


@pytest.mark.parametrize("given", [{"phone": True, "host": "192.168.1.150"}, {"host": "a b"},
                                   {"host": "%h"}, {"port": 70000}, {"allow_hosts": ["a b"]},
                                   {"allow_hosts": ["rig:8029"]}, {"allow_hosts": ["%H"]}])
def test_listen_args_that_disagree_or_systemd_would_expand_are_refused(given):
    with pytest.raises(service.ServiceError):
        service.listen_args(**given)


@pytest.mark.parametrize("argv, listen", [
    (["agent", "install-service", "--phone"], ["--phone"]),
    (["agent", "--phone", "install-service"], ["--phone"]),
    (["agent", "--allow-host", "rig.ts.net", "install-service", "--phone"], ["--phone", "--allow-host", "rig.ts.net"]),
    (["agent", "install-service", "--phone", "--allow-host", "Rig.TS.net."], ["--phone", "--allow-host", "rig.ts.net"]),
])
def test_install_service_phone_reaches_the_unit_either_way_round(argv, listen, monkeypatch, capsys, checkout):
    seen = {}

    def install(root, **kwargs):
        seen.update(kwargs)
        return ["done"]

    monkeypatch.setattr(service, "install", install)
    monkeypatch.setattr("sushi_rig.paths.checkout_root", lambda: checkout)
    assert cli.main(argv) == 0
    assert seen["listen"] == listen


def test_install_service_refuses_an_allow_host_the_agent_would_refuse(capsys):
    with pytest.raises(SystemExit) as refused:
        cli.main(["agent", "install-service", "--phone", "--allow-host", "rig.ts.net:8029"])
    assert refused.value.code == 2
    assert "has a port" in capsys.readouterr().err


# --------------------------------------------------------------------------- #
# The guide
# --------------------------------------------------------------------------- #

def headings(text):
    return [m.group(2) for m in re.finditer(r"^(#{1,6})\s+(.*)$", text, flags=re.MULTILINE)]


def test_playing_out_covers_what_the_issue_lists_and_keeps_the_no_password_warning():
    text = (GUIDE / "playing-out.md").read_text(encoding="utf-8")
    said = headings(text)
    for heading in ("Playing out", "Start the agent for the phone", "At home, on the same Wi-Fi",
                    "On the phone's hotspot", "On a hotspot the laptop makes",
                    "If the phone cannot reach the laptop", "The router keeps Wi-Fi devices apart",
                    "The laptop's firewall", "`http://`, not `https://`", "VPNs and Private Relay",
                    "With the lid closed", "No password"):
        assert heading in said, heading
    for words in ("sushi-rig agent --phone", "172.20.10.", "nmcli device wifi hotspot", "http://10.42.0.1:8029/",
                  "guest network", "sudo ufw allow 8029/tcp", "Private Relay", "HandleLidSwitch=ignore",
                  "Automatic Suspend", "set power_save off", "wifi.powersave = 2", "XRUNS",
                  "install-service --phone", "five minutes", "anyone on the same network"):
        assert words in text, words
    assert "None of this is\nyet tried on a real laptop" in text, "the lid section says it is untested"


def test_getting_started_uses_phone_and_still_warns_there_is_no_password():
    text = (GUIDE / "getting-started.md").read_text(encoding="utf-8")
    section = text[text.index("## Use your phone as well"):text.index("## If something goes wrong")]
    assert "sushi-rig agent --phone" in section and "--host 0.0.0.0" not in section
    assert "There is no password" in section
    assert "(playing-out.md)" in section
