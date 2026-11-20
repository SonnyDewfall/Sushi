"""The one guard in front of every route that changes something (#161).

`Agent._same_site_only`, a middleware: a POST, PUT, PATCH or DELETE that a
browser says came from another site (`cross_site_refusal`), or any request,
a GET or a page load included, sent to a name this agent does not answer to
(`rebinding_refusal`, DNS rebinding), is 403 with `code: "cross_site"`
before any route sees it. The names it answers to are any IP, localhost,
this machine's hostname or its `.local` name, the bound `--host`, and a name
given with `--allow-host`, which is also trusted as an `Origin` behind a
reverse proxy that rewrites `Host` (review of PR #174). By those, a request
with neither `Origin` nor `Sec-Fetch-Site` (curl, the CLI's `agent_client`)
is allowed, as is the agent's own page.

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
from aiohttp import web  # noqa: E402
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from sushi_rig import agent_client  # noqa: E402
from sushi_rig.agent import (  # noqa: E402
    STATE_CHANGING,
    Agent,
    AgentError,
    allowed_host_name,
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


@pytest.mark.parametrize("headers", REFUSED.values(), ids=REFUSED.keys())
def test_opening_the_takes_folder_is_refused_from_elsewhere_and_runs_nothing(headers, tmp_path):
    """POST /takes/open (#189) runs a program on this machine's desktop: the
    guard refuses it before the route, so its opener is never asked for,
    even from the loopback peer the route itself allows."""
    (tmp_path / "takes").mkdir()
    served, runner = agent(takes_dir=tmp_path / "takes")
    asked = []
    served.folder_opener = lambda: asked.append("opener") or None
    status, body = send(served, "POST", "/takes/open", headers)
    assert (status, body["code"]) == (403, "cross_site"), body
    assert asked == [] and runner.calls == []
    status, body = send(served, "POST", "/takes/open", own_origin="127.0.0.1")
    assert (status, body["code"]) == (409, "no_opener"), "the agent's own page reaches the route"
    assert asked == ["opener"]


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
    "curl by a LAN address": {"Host": "192.168.1.20:{port}"},
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
    served, runner = agent(bound="rig.lan")
    assert send(served, "POST", "/rig/down", own_origin="rig.lan")[0] == 200
    assert runner.calls == [["down"]]


# --- reads, and the page itself (review of PR #174) -------------------------

# What a browser sends on each kind of GET, beside Host. Chromium sends the
# first, nothing at all, to a plain-http name other than localhost, which a
# rebound name and a phone's `.local` both are (test_ui_cross_site.py).
GETS = {
    "no header (plain http by a name)": {},
    "a fetch": {"Sec-Fetch-Site": "same-origin", "Sec-Fetch-Mode": "cors", "Sec-Fetch-Dest": "empty"},
    "a page load": {"Sec-Fetch-Site": "none", "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "document",
                    "Sec-Fetch-User": "?1"},
    "a fetch with Origin": {"Origin": "http://{name}:{port}", "Sec-Fetch-Mode": "cors"},
    "a script": {"Sec-Fetch-Dest": "script"},
}
READS = ["/health", "/rigs", "/doctor", "/live/log", "/takes", "/presets", "/rig", "/", "/ui/app.js",
         "/help/", "/help/recording", "/help/img/rack.png",
         # The visualiser's files (#176): the static route's, as app.js is.
         "/ui/screensaver.js", "/ui/visualiser.js", "/ui/visualiser-presets.js",
         "/ui/vendor/butterchurn-2.6.7/butterchurn.min.js", "/machine/roles"]


@pytest.mark.parametrize("kind", GETS, ids=GETS.keys())
@pytest.mark.parametrize("path", READS)
def test_a_rebound_page_can_read_nothing(path, kind):
    """Under DNS rebinding the page is same-origin, so it could read every
    answer: each GET a browser sends to the rebound name is the guard's 403."""
    served, _ = agent()
    headers = {k: v.replace("{name}", "rebound.example") for k, v in GETS[kind].items()}
    status, body = send(served, "GET", path, {"Host": "rebound.example:{port}", **headers})
    assert status == 403 and body["code"] == "cross_site", body
    assert "rebound.example" in body["reason"], body


@pytest.mark.parametrize("name", ["127.0.0.1", "localhost", f"{HOSTNAME}.local", "192.168.1.20", "[::1]"])
@pytest.mark.parametrize("kind", GETS, ids=GETS.keys())
def test_the_agents_own_page_loads_and_reads_by_any_name_it_answers_to(name, kind):
    served, _ = agent()
    for path in ("/", "/ui/app.js", "/health", "/rig", "/help/", "/help/recording",
                 "/ui/screensaver.js", "/ui/visualiser.js", "/ui/visualiser-presets.js",
                 "/ui/vendor/butterchurn-2.6.7/butterchurn.min.js", "/machine/roles"):
        headers = {k: v.replace("{name}", name) for k, v in GETS[kind].items()}
        status, _ = send_raw(served, "GET", path, {"Host": f"{name}:{{port}}", **headers})
        # /machine/roles answers 409 here, past the guard: this agent has no
        # machine file to read (the visualiser then uses the browser's input).
        assert status == (409 if path == "/machine/roles" else 200), (path, status)


def test_curl_and_the_cli_by_a_name_the_agent_answers_to_and_no_other():
    """No Origin and no Sec-Fetch-*: curl, the CLI's `status`. Held to the
    names as a browser is, since a rebound page's GET carries nothing more
    (review of PR #174); a name of the operator's is `--allow-host`'s."""
    served, runner = agent(bound="rig.lan")
    for host in ("127.0.0.1:{port}", "localhost:{port}", "rig.lan:{port}", f"{HOSTNAME}.local:{{port}}"):
        assert send_raw(served, "GET", "/health", {"Host": host})[0] == 200, host
    assert send(served, "POST", "/rig/down", {"Host": "rig.lan:{port}"})[0] == 200
    status, body = send(served, "GET", "/health", {"Host": "rebound.example:{port}"})
    assert status == 403 and "--allow-host rebound.example" in body["reason"], body
    status, body = send(served, "POST", "/rig/down", {"Host": "rebound.example:{port}"})
    assert status == 403 and body["code"] == "cross_site", body
    assert runner.calls == [["down"]]


def test_another_sites_link_to_the_page_still_opens_it():
    """A cross-site GET is not refused: a link to the rig's page from a notes
    app or a bookmark page is one. Its answer has no CORS header, so a page
    of that site cannot read it."""
    served, _ = agent()
    status, headers = send_raw(served, "GET", "/", {
        "Host": "192.168.1.20:{port}", "Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Dest": "document"})
    assert status == 200 and "Access-Control-Allow-Origin" not in headers
    status, headers = send_raw(served, "GET", "/rig", {"Sec-Fetch-Site": "cross-site",
                                                        "Origin": "https://example.invalid"})
    assert status == 200 and "Access-Control-Allow-Origin" not in headers


def send_raw(agent, method, path, headers):
    """As `send`, answering (status, the response's headers)."""

    async def go():
        async with TestClient(TestServer(agent.app())) as client:
            port = client.server.port
            sent = {k: v.format(port=port) for k, v in headers.items()}
            response = await client.request(method, path, headers=sent)
            await response.read()
            return response.status, response.headers

    return asyncio.run(go())


# --- behind a reverse proxy (review of PR #174) ------------------------------

PROXIED = {"Host": "127.0.0.1:{port}", "Origin": "https://rig.ts.net", "Sec-Fetch-Site": "same-origin"}


def test_a_proxy_that_rewrites_host_works_once_its_name_is_allowed():
    """nginx's default `proxy_pass` sends `Host: 127.0.0.1:8029`, and the
    browser's `Origin` is the proxy's name: allowed with `--allow-host`, on
    any scheme and port, and the page's reads go through as well."""
    served, runner = agent()
    status, body = send(served, "POST", "/rig/down", PROXIED)
    assert status == 403 and body["code"] == "cross_site", body
    served, runner = agent(allow_hosts=["rig.ts.net"])
    assert send(served, "POST", "/rig/down", PROXIED)[0] == 200
    assert send(served, "POST", "/rig/down", {**PROXIED, "Origin": "http://rig.ts.net:8443"})[0] == 200
    assert send_raw(served, "GET", "/health", {"Host": "127.0.0.1:{port}", "Sec-Fetch-Site": "same-origin"})[0] == 200
    assert runner.calls == [["down"], ["down"]]


@pytest.mark.parametrize("headers", [
    {**PROXIED, "Origin": "https://other.ts.net"},         # another name of the same tailnet
    {**PROXIED, "Sec-Fetch-Site": "cross-site"},           # the browser says another site
    {**PROXIED, "Origin": "http://localhost:3000"},        # another program's page on this machine
    {**PROXIED, "Origin": "null"},
], ids=["another name", "cross-site", "another local port", "null"])
def test_behind_a_proxy_only_the_allowed_names_origin_is_trusted(headers):
    served, runner = agent(allow_hosts=["rig.ts.net"])
    status, body = send(served, "POST", "/rig/down", headers)
    assert status == 403 and body["code"] == "cross_site", body
    assert runner.calls == []


def test_the_bound_name_is_not_trusted_as_an_origin_whatever_the_host():
    """`--host` is the address the agent listens on, not a proxy's name."""
    served, _ = agent(bound="rig.lan")
    status, body = send(served, "POST", "/rig/down", {**PROXIED, "Origin": "http://rig.lan:8029"})
    assert status == 403, body


def test_a_proxy_that_passes_host_through_works_by_its_name():
    served, runner = agent(allow_hosts=["rig.ts.net"])
    assert send(served, "POST", "/rig/down", {"Host": "rig.ts.net", "Origin": "https://rig.ts.net",
                                                "Sec-Fetch-Site": "same-origin"})[0] == 200


# --- --allow-host takes a name alone (review of PR #174) ---------------------

@pytest.mark.parametrize("value, why", [
    ("rig.vpn:8029", "has a port"), ("http://rig.ts.net", "has a scheme"), ("https://rig.ts.net:443", "has a scheme"),
    ("rig.ts.net/", "is not a host name"), ("rig ts", "is not a host name"), ("", "is not a host name"),
    ("[::1]", "is an IP address"), ("192.168.1.20", "is an IP address"), ("::1", "is an IP address"),
    ("-rig", "is not a host name"), ("rig..net", "is not a host name"),
])
def test_an_allowed_host_with_a_port_or_a_scheme_is_refused_not_ignored(value, why):
    with pytest.raises(ValueError, match=why):
        allowed_host_name(value)
    with pytest.raises(AgentError, match=why):
        Agent(describe=lambda: dict(DOWN), allow_hosts=[value])


@pytest.mark.parametrize("value, name", [
    ("Rig.TS.net.", "rig.ts.net"), ("rig", "rig"), ("rig_box.lan", "rig_box.lan"), (" rig.vpn ", "rig.vpn"),
])
def test_an_allowed_host_is_compared_as_the_guard_compares_names(value, name):
    assert allowed_host_name(value) == name


@pytest.mark.parametrize("value", ["rig.vpn:8029", "http://rig.ts.net"])
def test_sushi_rig_agent_refuses_an_allowed_host_with_a_port_or_scheme(value, monkeypatch, capsys):
    from sushi_rig import agent as agent_module
    from sushi_rig import cli

    monkeypatch.setattr(agent_module, "serve", lambda *a, **k: pytest.fail("served with a bad --allow-host"))
    with pytest.raises(SystemExit) as exited:
        cli.main(["agent", "--allow-host", value])
    assert exited.value.code == 2
    err = capsys.readouterr().err
    assert "--allow-host" in err and value in err and "alone" in err, err


# --- every route ------------------------------------------------------------

def every_route(app):
    """(method, path) for each registered route, its placeholders filled:
    `*` stands for POST, a static folder for a file in it."""
    found = []
    for route in app.router.routes():
        resource = route.resource
        path = re.sub(r"\{[^}]+\}", "x", resource.canonical)
        if isinstance(resource, web.StaticResource):
            path += "/app.js"
        found.append((route.method if route.method != "*" else "POST", path))
    return found


def test_every_registered_route_is_behind_the_guard():
    """Walks the app as the agent builds it, so a route added later is
    covered or this fails. Every route of every method, GET and HEAD
    included, sent a request by a rebound name, with a browser's headers or
    none, answers the guard's 403; every POST, PUT, PATCH and DELETE also
    refuses another site's page. Nothing runs."""
    served, runner = agent()
    routes = every_route(served.app())
    writes = [(m, p) for m, p in routes if m in STATE_CHANGING]
    paths = {path for _, path in writes}
    # Not vacuous: the routes #160 and #162 guarded by hand, and others.
    assert {"/rigs/x/delete", "/rigs/x/restore", "/rig/channels/x/save-as", "/rig/channels/x/load",
            "/rig/up", "/rig/down", "/live/parameter", "/rig/save", "/record/start", "/takes/open",
            "/rig/midi/assign"} <= paths, paths  # and #192's pedal assignment
    assert len(writes) >= 24, writes  # as of #161
    assert {("GET", "/health"), ("HEAD", "/health"), ("GET", "/"), ("GET", "/ui/app.js")} <= set(routes)

    async def go():
        async with TestClient(TestServer(served.app())) as client:
            port = client.server.port
            rebound = {
                "rebound fetch": {"Host": f"rebound.example:{port}", "Origin": f"http://rebound.example:{port}",
                                  "Sec-Fetch-Site": "same-origin"},
                "rebound page load": {"Host": f"rebound.example:{port}", "Sec-Fetch-Mode": "navigate",
                                      "Sec-Fetch-Dest": "document", "Sec-Fetch-Site": "none"},
                # What Chromium sends a plain-http name on a GET: Host alone.
                "rebound, Host alone": {"Host": f"rebound.example:{port}"},
            }
            cross_site = {
                "cross-site": {"Sec-Fetch-Site": "cross-site"},
                "origin": {"Origin": "https://example.invalid"},
            }
            answers = {}
            for method, path in routes:
                cases = {**rebound, **cross_site} if method in STATE_CHANGING else rebound
                for case, headers in cases.items():
                    kwargs = {"json": {}} if method in STATE_CHANGING else {}
                    response = await client.request(method, path, headers=headers, **kwargs)
                    code = None if method == "HEAD" else (await response.json()).get("code")
                    answers[(method, path, case)] = (response.status, code if method != "HEAD" else "cross_site")
            return answers

    answers = asyncio.run(go())
    unguarded = {key: said for key, said in answers.items() if said != (403, "cross_site")}
    assert unguarded == {}, unguarded
    assert runner.calls == []


# Every GET route as of #161, each read only. A GET is held to the rebinding
# check alone: another site's page may still send one (it cannot read the
# answer). A route that changes something must not be a GET.
GET_ROUTES = {
    "/", "/ui", "/ui/", "/ui/app.js", "/health", "/rig", "/doctor", "/live", "/live/log", "/library",
    "/profiles", "/live/plugins", "/rigs", "/rig/load", "/amp", "/amp/models", "/record", "/takes",
    "/machine/roles", "/rigs/x/channels", "/rigs/x/saves", "/rigs/bin", "/presets",
    "/help", "/help/", "/help/x", "/help/img/x",  # the user guide (#167)
    "/midi",  # the pedals and the running rig's assignments (#191): reads two files and lists config/rigs/
}


def test_the_get_routes_are_the_ones_reviewed_as_changing_nothing():
    """A new GET fails this until it is added above, after checking that it
    reads only: the cross-site check is not in front of a GET."""
    served, _ = agent()
    gets = {path for method, path in every_route(served.app()) if method == "GET"}
    assert gets == GET_ROUTES, (gets - GET_ROUTES, GET_ROUTES - gets)


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
    ("rebound.example/127.0.0.1", False), ("[::1", False), ("", False),
])
def test_only_a_host_this_agent_answers_to_is_allowed(host, allowed):
    assert (rebinding_refusal(host, own_names()) is None) is allowed


def test_a_request_with_no_host_at_all_is_no_browsers():
    """HTTP/1.0 with no Host: a browser always sends one, so it is not read."""
    assert rebinding_refusal(None, own_names()) is None


def test_the_refusal_says_how_to_allow_a_name_of_the_operators():
    reason = rebinding_refusal("rig.vpn:8029", own_names())
    assert reason.startswith("refused") and "--allow-host rig.vpn" in reason, reason


def test_cross_site_refusal_is_unchanged_by_the_host_check():
    """Still pure over Origin, Sec-Fetch-Site and Host: whether the name is
    this agent's is `rebinding_refusal`'s, the middleware asks both."""
    assert cross_site_refusal({"Origin": "http://rebound.example:8029"}, "rebound.example:8029") is None
    assert rebinding_refusal("rebound.example:8029", own_names()) is not None


def test_sushi_rig_agent_passes_each_allowed_name_to_the_agent(monkeypatch):
    from sushi_rig import agent as agent_module
    from sushi_rig import cli

    served = []
    monkeypatch.setattr(agent_module, "serve", lambda host, port, allow_hosts=(): served.append(
        (host, port, list(allow_hosts))) or 0)
    assert cli.main(["agent", "--host", "0.0.0.0", "--allow-host", "rig.vpn", "--allow-host", "rig.lan"]) == 0
    assert cli.main(["agent"]) == 0
    assert served == [("0.0.0.0", 8029, ["rig.vpn", "rig.lan"]), ("127.0.0.1", 8029, [])]
