"""The cross-site guard (#161) as Chromium meets it.

test_agent_cross_site.py writes the headers a browser would send by hand;
here Chromium sends its own, against a real agent over `ui_fake.py`'s fake
on 127.0.0.1 (`Served`). Chromium is started with `--host-resolver-rules`
mapping three names to 127.0.0.1, which is what DNS does for each in life:

- `rig-box.local`, this machine's mDNS name (the hostname is set to
  `rig-box` for the test), as a phone on the LAN would load the page;
- `rebound.example`, an attacker's name after DNS rebinding: the page the
  browser holds is of that name, and its requests reach the agent;
- `attacker.example`, a page of another site, served on another port.

What stands in for what: the names are the resolver rules, not DNS or
mDNS; the phone is this Chromium; nothing here is the rig machine, a pedal
or a MIDI controller.
"""

from __future__ import annotations

import http.server
import threading

import pytest

pytest.importorskip("aiohttp")

from test_ui import browser, served  # noqa: E402,F401

HOSTNAME = "rig-box"
NAMES = ("rig-box.local", "rebound.example", "attacker.example")


@pytest.fixture(autouse=True)
def _this_machine_is_rig_box(monkeypatch):
    import socket

    monkeypatch.setattr(socket, "gethostname", lambda: HOSTNAME)


@pytest.fixture(scope="module")
def mapped(browser):  # noqa: F811
    """A Chromium of the same Playwright, with the three names on 127.0.0.1."""
    rules = ", ".join(f"MAP {name} 127.0.0.1" for name in NAMES)
    mapped = browser.browser_type.launch(args=[f"--host-resolver-rules={rules}"])
    yield mapped
    mapped.close()


def post_from(page, path):
    """POST `path` from the page as the web UI's own `request` sends one:
    a relative URL, JSON, no mode. Answers (status, the agent's JSON)."""
    return page.evaluate("""async (path) => {
        const response = await fetch(path, {method: "POST", cache: "no-store",
            headers: {"content-type": "application/json"}, body: "{}"});
        return [response.status, await response.json()];
    }""", path)


def port_of(served):  # noqa: F811
    return int(served.url.rsplit(":", 1)[1])


def test_the_agents_own_page_by_ip_may_change_the_rig(mapped, served):  # noqa: F811
    page = mapped.new_page()
    page.goto(served.url + "/#stage")
    status, body = post_from(page, "/rig/down")
    assert status == 200, body
    assert served.lifecycle.calls == [["down"]]
    page.close()


def test_the_agents_own_page_by_the_machines_local_name_may_change_the_rig(mapped, served):  # noqa: F811
    """A phone on the LAN that loads http://rig-box.local:8029/."""
    page = mapped.new_page()
    page.goto(f"http://{HOSTNAME}.local:{port_of(served)}/#stage")
    status, body = post_from(page, "/rig/down")
    assert status == 200, body
    assert served.lifecycle.calls == [["down"]]
    page.close()


def test_a_page_of_a_rebound_name_is_served_but_may_change_nothing(mapped, served):  # noqa: F811
    """The page loads (a GET is not guarded, and it has no CORS header for
    another site to read it by), but Chromium sends the rebound name as
    Host and Origin, `Sec-Fetch-Site: same-origin`, and every write is 403."""
    page = mapped.new_page()
    page.goto(f"http://rebound.example:{port_of(served)}/#stage")
    for path in ("/rig/down", "/live/undo", "/rigs/electric_board/delete", "/rig/save"):
        status, body = post_from(page, path)
        assert status == 403 and body["code"] == "cross_site", (path, body)
        assert "rebound.example" in body["reason"], body
    assert served.lifecycle.calls == []
    page.close()


@pytest.fixture
def attacker(served):  # noqa: F811
    """A page of another site on another port of this machine, which posts
    to the agent as the issue's example does: `mode: "no-cors"`, no
    preflight, and a form beside it."""
    target = served.url + "/rig/down"
    html = f"""<!doctype html><title>elsewhere</title>
<form id="form" method="POST" action="{target}" target="sink"><input name="x" value="1"></form>
<iframe name="sink"></iframe>
<script>
  window.sent = fetch("{target}", {{method: "POST", mode: "no-cors", body: "{{}}"}}).then(() => "sent", (e) => String(e));
</script>""".encode()

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(html)))
            self.end_headers()
            self.wfile.write(html)

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://attacker.example:{server.server_address[1]}/"
    server.shutdown()
    server.server_close()
    thread.join(10)


def test_another_sites_page_reaches_the_agent_and_is_refused(mapped, served, attacker):  # noqa: F811
    """Chromium sends the no-cors fetch and the form (Playwright sees each
    answer, which the page cannot), and the agent answers each 403 with
    nothing run: shown arriving, so a refusal is not a request that never left."""
    page = mapped.new_page()
    with page.expect_response(lambda r: r.url == served.url + "/rig/down") as fetched:
        page.goto(attacker)
    assert fetched.value.status == 403
    assert page.evaluate("window.sent") == "sent"
    with page.expect_response(lambda r: r.url == served.url + "/rig/down") as submitted:
        page.evaluate("document.getElementById('form').submit()")
    assert submitted.value.status == 403
    assert served.lifecycle.calls == []
    page.close()
