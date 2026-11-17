"""The one guard in front of every route that changes something (#161).

`Agent._same_site_only`, a middleware: a POST, PUT, PATCH or DELETE that a
browser says came from another site (`cross_site_refusal`), or that a browser
sent to a name this agent does not answer to (`rebinding_refusal`, DNS
rebinding), is 403 with `code: "cross_site"` before any route sees it. A
request with neither `Origin` nor `Sec-Fetch-Site` (curl, the CLI's
`agent_client`) is allowed, as is the agent's own page by any IP, localhost,
this machine's hostname or its `.local` name, or a name given with
`--allow-host`.

The browser itself is test_ui_cross_site.py's; here the headers a browser
would send are written by hand, and the agent is served by aiohttp's test
server on 127.0.0.1. Nothing here is the rig machine, a phone, a pedal or a
MIDI controller.
"""

from __future__ import annotations

import asyncio
import re

import pytest

aiohttp = pytest.importorskip("aiohttp")
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from sushi_rig import agent_client  # noqa: E402
from sushi_rig.agent import (  # noqa: E402
    STATE_CHANGING,
    Agent,
    cross_site_refusal,
    own_names,
    rebinding_refusal,
)
from sushi_rig.supervisor import Outcome  # noqa: E402

DOWN = {"verdict": "absent", "running": False, "message": "No rig is running."}
HOSTNAME = "rig-box"


class Runner:
    """Records the lifecycle steps asked for: a refused request asks none."""

    def __init__(self):
        self.calls = []

    async def __call__(self, args):
        self.calls.append(list(args))
        return Outcome(True, 0, "down\n", "")


@pytest.fixture(autouse=True)
def _this_machine_is_rig_box(monkeypatch):
    """The hostname the guard reads, the same on every machine the tests run on."""
    import socket

    monkeypatch.setattr(socket, "gethostname", lambda: HOSTNAME)


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


def agent(**kwargs):
    runner = Runner()
    return Agent(describe=lambda: dict(DOWN), run=runner, **kwargs), runner


def send(agent, method, path, headers=None, own_origin=None):
    """One request, as (status, json). `own_origin` is a host to send as both
    `Host` and `Origin` with this server's port, as a browser that loaded the
    page from that name would; `headers` may name `{port}`."""

    async def go():
        async with TestClient(TestServer(agent.app())) as client:
            port = client.server.port
            sent = {k: v.format(port=port) for k, v in (headers or {}).items()}
            if own_origin is not None:
                sent.setdefault("Host", f"{own_origin}:{port}")
                sent.setdefault("Origin", f"http://{own_origin}:{port}")
                sent.setdefault("Sec-Fetch-Site", "same-origin")
            response = await client.request(method, path, headers=sent)
            return response.status, await response.json()

    return asyncio.run(go())


# --- refused ----------------------------------------------------------------

REFUSED = {
    "another site's page": {"Sec-Fetch-Site": "cross-site"},
    "another site's Origin": {"Origin": "https://example.invalid"},
    "another port of this machine": {"Origin": "http://127.0.0.1:1"},
    "a sandboxed or file page": {"Origin": "null"},
    "a file page": {"Origin": "file://"},
    "a form from another site": {"Origin": "https://example.invalid", "Sec-Fetch-Site": "cross-site",
                                 "Content-Type": "application/x-www-form-urlencoded"},
    # DNS rebinding: the attacker's name, now resolving to this machine,
    # sent as Host and Origin alike, and the browser calls it same-origin.
    "a rebound name": {"Host": "rebound.example:{port}", "Origin": "http://rebound.example:{port}",
                       "Sec-Fetch-Site": "same-origin"},
    "a rebound name, no Origin": {"Host": "rebound.example:{port}", "Sec-Fetch-Site": "same-origin"},
    "a rebound name spelt like this machine": {"Host": f"{HOSTNAME}.example:{{port}}",
                                               "Origin": f"http://{HOSTNAME}.example:{{port}}"},
    "localhost under another name": {"Host": "localhost.example:{port}",
                                     "Origin": "http://localhost.example:{port}"},
    "an IP inside a name": {"Host": "127.0.0.1.nip.io:{port}", "Origin": "http://127.0.0.1.nip.io:{port}"},
}


@pytest.mark.parametrize("headers", REFUSED.values(), ids=REFUSED.keys())
def test_a_write_a_browser_sent_from_elsewhere_is_refused_before_the_route(headers):
    served, runner = agent()
    status, body = send(served, "POST", "/rig/down", headers)
    assert status == 403, body
    assert body["code"] == "cross_site" and body["reason"].startswith("refused"), body
    assert runner.calls == [], "nothing ran"


@pytest.mark.parametrize("method", ["PUT", "PATCH", "DELETE"])
def test_every_state_changing_method_is_guarded_even_where_no_route_takes_it(method):
    """403, not the 405 the router would give: the guard is in front of it,
    so a route added later under one of these is covered from the start."""
    served, _ = agent()
    status, body = send(served, method, "/rig/down", {"Sec-Fetch-Site": "cross-site"})
    assert status == 403 and body["code"] == "cross_site", body
    status, body = send(served, method, "/rig/down")
    assert status == 405, body


# --- allowed ----------------------------------------------------------------

ALLOWED = {
    "curl, the CLI, the pedal's agent_client": {},
    "typed into the address bar": {"Sec-Fetch-Site": "none"},
    "the agent's own page on this machine": {"Host": "127.0.0.1:{port}", "Origin": "http://127.0.0.1:{port}",
                                             "Sec-Fetch-Site": "same-origin"},
    "a browser that sends Origin and no Sec-Fetch-Site": {"Host": "127.0.0.1:{port}",
                                                          "Origin": "http://127.0.0.1:{port}"},
    "curl with any Host and no browser headers": {"Host": "rebound.example:{port}"},
}


@pytest.mark.parametrize("headers", ALLOWED.values(), ids=ALLOWED.keys())
def test_a_write_from_no_browser_or_the_agents_own_page_is_allowed(headers):
    served, runner = agent()
    status, body = send(served, "POST", "/rig/down", headers)
    assert status == 200, body
    assert runner.calls == [["down"]]


@pytest.mark.parametrize("name", [
    "localhost", "127.0.0.1", "[::1]",
    "192.168.1.20",                 # a phone on the LAN, by the rig's IP
    "10.0.0.7", "[fe80::1]",
    HOSTNAME, f"{HOSTNAME}.local",  # by the machine's name, and its mDNS name
    f"{HOSTNAME.upper()}.LOCAL",    # as a phone's keyboard may write it
    f"{HOSTNAME}.local.",           # the same name, fully qualified
])
def test_the_agents_own_page_by_any_name_it_answers_to_may_write(name):
    served, runner = agent()
    status, body = send(served, "POST", "/rig/down", own_origin=name)
    assert status == 200, body
    assert runner.calls == [["down"]]


def test_a_name_the_operator_allows_may_write_and_no_other():
    """`sushi-rig agent --allow-host rig.tail1234.ts.net`: a tunnel's or a
    VPN's name for the machine, which the guard cannot know of itself."""
    served, runner = agent(allow_hosts=["Rig.Tail1234.ts.net"])
    assert send(served, "POST", "/rig/down", own_origin="rig.tail1234.ts.net")[0] == 200
    status, body = send(served, "POST", "/rig/down", own_origin="other.tail1234.ts.net")
    assert status == 403 and "--allow-host other.tail1234.ts.net" in body["reason"], body
    assert runner.calls == [["down"]]


def test_the_name_the_agent_was_bound_to_may_write():
    """`sushi-rig agent --host rig.lan`: the bound address is a name it answers to."""
    served, runner = agent(allow_hosts=["rig.lan"])
    assert send(served, "POST", "/rig/down", own_origin="rig.lan")[0] == 200
    assert runner.calls == [["down"]]


def test_reads_are_not_guarded():
    """A GET changes nothing, and the guard is not in front of one: another
    site's page can still not read the answer, which has no CORS header."""
    served, _ = agent()

    async def go():
        async with TestClient(TestServer(served.app())) as client:
            response = await client.get("/rig", headers={"Sec-Fetch-Site": "cross-site",
                                                         "Origin": "https://example.invalid"})
            return response.status, response.headers

    status, headers = asyncio.run(go())
    assert status == 200 and "Access-Control-Allow-Origin" not in headers


# --- every route ------------------------------------------------------------

def state_changing_routes(app):
    """(method, path) for each registered route a POST, PUT, PATCH or DELETE
    reaches, its placeholders filled: `*` stands for all of them."""
    found = []
    for route in app.router.routes():
        if route.method not in STATE_CHANGING and route.method != "*":
            continue
        path = re.sub(r"\{[^}]+\}", "x", route.resource.canonical)
        found.append((route.method if route.method != "*" else "POST", path))
    return found


def test_every_registered_state_changing_route_is_behind_the_guard():
    """Walks the app as the agent builds it, so a route added later is
    covered or this fails: each one, sent another site's request and a
    rebound name's, answers the guard's 403 and runs nothing."""
    served, runner = agent()
    routes = state_changing_routes(served.app())
    paths = {path for _, path in routes}
    # Not vacuous: the routes #160 and #162 guarded by hand, and others.
    assert {"/rigs/x/delete", "/rigs/x/restore", "/rig/channels/x/save-as", "/rig/channels/x/load",
            "/rig/up", "/rig/down", "/live/parameter", "/rig/save", "/record/start"} <= paths, paths
    assert len(routes) >= 24, routes  # as of #161

    async def go():
        async with TestClient(TestServer(served.app())) as client:
            port = client.server.port
            answers = {}
            for method, path in routes:
                for case, headers in {
                    "cross-site": {"Sec-Fetch-Site": "cross-site"},
                    "origin": {"Origin": "https://example.invalid"},
                    "rebound": {"Host": f"rebound.example:{port}", "Origin": f"http://rebound.example:{port}",
                                "Sec-Fetch-Site": "same-origin"},
                }.items():
                    response = await client.request(method, path, json={}, headers=headers)
                    answers[(method, path, case)] = (response.status, (await response.json()).get("code"))
            return answers

    answers = asyncio.run(go())
    unguarded = {key: said for key, said in answers.items() if said != (403, "cross_site")}
    assert unguarded == {}, unguarded
    assert runner.calls == []


def test_no_route_guards_itself_any_longer():
    """The per-route `_same_origin_only` of #160 and #162 is the middleware now."""
    assert not hasattr(Agent, "_same_origin_only")


# --- the clients that have no browser ---------------------------------------

def test_the_clis_own_client_still_changes_the_rig():
    """`agent_client.ask`, what `sushi-rig up|down|restart` and the rest go
    through: urllib, no Origin, no Sec-Fetch-Site."""
    served, runner = agent()

    async def go():
        async with TestClient(TestServer(served.app())) as client:
            base = f"http://127.0.0.1:{client.server.port}"
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(None, agent_client.ask, base, "POST", "/rig/down", {})

    status, body = asyncio.run(go())
    assert status == 200 and body["ok"] is True, body
    assert runner.calls == [["down"]]


# --- the pure decisions -----------------------------------------------------

def test_own_names_are_localhost_the_hostname_its_local_name_and_those_given():
    assert own_names(["Rig.LAN.", "0.0.0.0", ""]) == {
        "localhost", HOSTNAME, f"{HOSTNAME}.local", "rig.lan", "0.0.0.0"}


def test_a_fully_qualified_hostname_gives_its_short_name_and_local_name_too(monkeypatch):
    import socket

    monkeypatch.setattr(socket, "gethostname", lambda: "Rig-Box.home.arpa")
    assert own_names() == {"localhost", "rig-box.home.arpa", "rig-box", "rig-box.local"}


@pytest.mark.parametrize("host, allowed", [
    ("127.0.0.1:8029", True), ("127.0.0.1", True), ("[::1]:8029", True), ("[::ffff:127.0.0.1]:8029", True),
    ("192.168.1.20:8029", True), ("localhost:8029", True), ("LOCALHOST.:8029", True),
    (f"{HOSTNAME}:8029", True), (f"{HOSTNAME}.local:8029", True),
    ("rebound.example:8029", False), ("sub.localhost:8029", False), ("127.0.0.1.nip.io", False),
    ("127.0.0.1@rebound.example", False), ("rebound.example@127.0.0.1", False),
    ("rebound.example/127.0.0.1", False), ("[::1", False), ("", False), (None, False),
])
def test_a_browser_is_allowed_only_a_host_this_agent_answers_to(host, allowed):
    browser = {"Origin": "http://anything", "Sec-Fetch-Site": "same-origin"}
    assert (rebinding_refusal(browser, host, own_names()) is None) is allowed


def test_with_no_browser_headers_the_host_is_not_read():
    """curl may be pointed at any name (`--resolve`, /etc/hosts); only a
    browser can be made to send a request it did not mean to."""
    assert rebinding_refusal({}, "rebound.example:8029", own_names()) is None
    assert rebinding_refusal({}, None, own_names()) is None


def test_the_refusal_says_how_to_allow_a_name_of_the_operators():
    reason = rebinding_refusal({"Origin": "http://rig.vpn:8029"}, "rig.vpn:8029", own_names())
    assert reason.startswith("refused") and "--allow-host rig.vpn" in reason, reason


def test_cross_site_refusal_is_unchanged_by_the_host_check():
    """Still pure over Origin, Sec-Fetch-Site and Host: whether the name is
    this agent's is `rebinding_refusal`'s, the middleware asks both."""
    assert cross_site_refusal({"Origin": "http://rebound.example:8029"}, "rebound.example:8029") is None
    assert rebinding_refusal({"Origin": "http://rebound.example:8029"}, "rebound.example:8029",
                             own_names()) is not None


def test_sushi_rig_agent_passes_each_allowed_name_to_the_agent(monkeypatch):
    from sushi_rig import agent as agent_module
    from sushi_rig import cli

    served = []
    monkeypatch.setattr(agent_module, "serve", lambda host, port, allow_hosts=(): served.append(
        (host, port, list(allow_hosts))) or 0)
    assert cli.main(["agent", "--host", "0.0.0.0", "--allow-host", "rig.vpn", "--allow-host", "rig.lan"]) == 0
    assert cli.main(["agent"]) == 0
    assert served == [("0.0.0.0", 8029, ["rig.vpn", "rig.lan"]), ("127.0.0.1", 8029, [])]
