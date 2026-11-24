"""Pushed changes (#139) between two pages in Chromium.

A change made on one page shows on another in well under a second, and it
is the push that brings it: the watching page's clock is stopped
(Playwright's `clock.pause_at`) once it has drawn, so its one-second poll,
a timer, never fires again: the poll made infinitely long. Whatever that
page shows after is what a `changed` event made it read. Then: the stream
comes back after the agent restarts, and a page of another site cannot
open one.

What stands in for what: both pages are Chromium on 127.0.0.1, against a
real agent over `ui_fake.py`'s rig (which says what in it is invented);
not two phones, not a pedal (test_agent_events.py pushes a log entry and a
`busy` as a pedal's change makes them). The agent's stream is tested
without a browser in test_agent_events.py.
"""

from __future__ import annotations

import http.server
import re
import threading
import time

import pytest

pytest.importorskip("aiohttp")

from test_ui import browser, expect, served, slider, until  # noqa: E402,F401
from ui_fake import Served  # noqa: E402

from sushi_rig import agent_client  # noqa: E402

PHONE = {"width": 390, "height": 844}


class Watching:
    """A page whose poll never fires once it has drawn: its clock is
    installed before it loads and paused after (`freeze`). `asked` is every
    request it made, by path, from then on. Playwright's sync API hands a
    page's events to Python only inside one of its own calls, so every wait
    here is the page's `wait_for_timeout` (Playwright's own timer, not the
    page's stopped clock), never `time.sleep`."""

    def __init__(self, browser, served, url):  # noqa: F811
        self.served = served
        self.context = browser.new_context(viewport=PHONE)
        self.context.clock.install()
        self.page = self.context.new_page()
        self.errors = []
        self.asked = []
        self.page.on("pageerror", lambda error: self.errors.append(str(error)))
        self.page.on("request", lambda r: self.asked.append(r.url.split("/", 3)[-1]))
        self.page.goto(served.url + url)

    def freeze(self):
        now = self.page.evaluate("() => Date.now()")
        self.page.clock.pause_at(now + 1)
        self.wait(500)  # a poll already reading when the clock stopped ends
        self.asked.clear()

    def polls(self):
        return self.asked.count("health")

    def wait(self, ms):
        self.page.wait_for_timeout(ms)

    def until(self, predicate, timeout=5.0):
        deadline = time.monotonic() + timeout
        while not predicate():
            if time.monotonic() > deadline:
                raise AssertionError("not within %ss" % timeout)
            self.wait(50)

    def close(self):
        self.context.close()
        assert self.errors == [], self.errors


def frozen(watching, seconds=2.5):
    """Prove the watching page's poll is stopped: over `seconds` of real
    time, more than two of its seconds, it asks nothing."""
    watching.wait(seconds * 1000)
    assert watching.asked == [], f"the poll should be stopped: {watching.asked}"


def test_a_change_on_one_page_shows_on_another_in_well_under_a_second(browser, served):  # noqa: F811
    watching = Watching(browser, served, "/#stage")
    watching.page.wait_for_selector(".stage-screen .card")
    watcher = watching.page
    watcher.locator('.card[data-unit="overdrive"] button.open-card').click()
    tone = slider(watcher, "overdrive Tone")
    was = int(tone.get_attribute("aria-valuetext"))
    watching.freeze()
    frozen(watching)

    turning = browser.new_page(viewport={"width": 1280, "height": 900})
    turning.goto(served.url + "/#rack")
    turning.wait_for_selector(".unit")

    # A bypass, tapped on the other page.
    began = time.monotonic()
    turning.get_by_role("button", name="Bypass delay").click()
    expect(watcher.locator('.card[data-unit="delay"]')).to_have_class(re.compile(r"\bbypassed\b"), timeout=1000)
    bypass_took = time.monotonic() - began

    # A knob, turned with the keyboard on the other page. The watching page's
    # clock is moved on 0.6 s first, past the half-second a push waits after
    # the last that read the rig, and short of its one-second poll.
    watching.page.clock.run_for(600)
    assert watching.polls() == 1, "the bypass's read, and no poll"
    knob = slider(turning, "overdrive Tone")
    knob.focus()
    began = time.monotonic()
    turning.keyboard.press("PageUp")
    expect(knob).not_to_have_attribute("aria-valuetext", str(was))
    turned = knob.get_attribute("aria-valuetext")
    expect(tone).to_have_attribute("aria-valuetext", turned, timeout=1000)
    knob_took = time.monotonic() - began

    assert bypass_took < 1.0 and knob_took < 1.0, (bypass_took, knob_took)
    # Each read was the push's: the watching page's clock has not moved since it was stopped.
    assert watching.polls() >= 2
    still = watcher.evaluate("() => Date.now()")
    watching.wait(300)
    assert watcher.evaluate("() => Date.now()") == still
    print(f"pushed: a bypass in {bypass_took * 1000:.0f} ms, a knob in {knob_took * 1000:.0f} ms")
    turning.close()
    watching.close()


def test_the_stream_comes_back_after_the_agent_restarts(browser):  # noqa: F811
    """The agent stops and starts again on its port, with a change made
    meanwhile (the delay bypassed): the page, its poll stopped, opens its
    stream again by itself, reads the rig as it opens (so it shows what it
    missed), and is pushed the next change."""
    from ui_fake import UiRig

    first = Served()
    port = int(first.url.rsplit(":", 1)[1])
    watching = Watching(browser, first, "/#stage")
    delay = watching.page.locator('.card[data-unit="delay"]')
    watching.page.wait_for_selector(".stage-screen .card")
    expect(delay).not_to_have_class(re.compile(r"\bbypassed\b"))
    watching.freeze()
    assert len(first.agent.events.streams) == 1
    first.close()  # the agent stops: the stream ends, and the page tries again
    watching.wait(300)

    meanwhile = UiRig()
    meanwhile.bypassed["delay"] = True
    with Served(rig=meanwhile, port=port) as second:
        watching.until(lambda: len(second.agent.events.streams) == 1, timeout=15.0)
        # On the stream's return the page read the rig at once, its poll still stopped.
        expect(delay).to_have_class(re.compile(r"\bbypassed\b"), timeout=1000)
        assert watching.polls() >= 1
        changer = browser.new_page(viewport=PHONE)
        changer.goto(second.url + "/#stage")
        changer.wait_for_selector(".stage-screen .card")
        changer.get_by_role("button", name="Turn on delay").click()
        expect(delay).not_to_have_class(re.compile(r"\bbypassed\b"), timeout=1000)
        changer.close()
        watching.close()


def test_a_page_in_the_background_gives_its_stream_up_and_takes_it_again(browser, served):  # noqa: F811
    page = browser.new_page(viewport=PHONE)
    page.goto(served.url + "/#stage")
    page.wait_for_selector(".stage-screen .card")
    until(lambda: len(served.agent.events.streams) == 1)
    hidden = """(state) => {
      Object.defineProperty(document, 'visibilityState', {configurable: true, get: () => state});
      document.dispatchEvent(new Event('visibilitychange'));
    }"""
    page.evaluate(hidden, "hidden")
    until(lambda: len(served.agent.events.streams) == 0, timeout=3.0)
    page.evaluate(hidden, "visible")
    until(lambda: len(served.agent.events.streams) == 1, timeout=3.0)
    page.close()


@pytest.fixture
def elsewhere(served):  # noqa: F811
    """A page of another site, on another port of this machine, that opens
    the agent's stream itself."""
    target = served.url + "/events"
    html = f"""<!doctype html><title>elsewhere</title>
<script>
  window.said = [];
  const source = new EventSource("{target}");
  source.onopen = () => window.said.push("open");
  source.onmessage = () => window.said.push("message");
  source.addEventListener("changed", () => window.said.push("changed"));
  source.onerror = () => window.said.push("error " + source.readyState);
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
    yield f"http://127.0.0.1:{server.server_address[1]}/"
    server.shutdown()
    server.server_close()
    thread.join(5)


def test_another_sites_page_cannot_open_the_stream(browser, served, elsewhere):  # noqa: F811
    """Chromium sends the request (its Origin is the other site's) and the
    agent's guard refuses it before the route opens a stream: none is ever
    opened, which a stream opened and then dropped by the browser (it has
    no CORS header to read it by) would not show."""
    opened = []
    real_open = served.agent.events.open
    served.agent.events.open = lambda: opened.append(1) or real_open()
    page = browser.new_page()
    failed = []
    page.on("requestfailed", lambda r: failed.append(r.url))
    page.goto(elsewhere)
    page.wait_for_function("() => window.said.some((s) => s.startsWith('error'))")
    page.wait_for_timeout(1500)  # past the stream's `retry`: a refusal is not tried again
    assert failed == [served.url + "/events"]
    assert page.evaluate("() => window.said") == ["error 2"], "closed for good (readyState 2)"
    assert opened == [] and served.agent.events.streams == set()
    page.close()


def rig_reads(served):  # noqa: F811
    return sum(1 for call in served.rig.calls if call == ("snapshot",))


def test_a_burst_of_pedal_presses_and_changes_costs_a_bounded_number_of_rig_reads(browser):  # noqa: F811
    """Three pages open while, for two seconds each, a footswitch is pressed
    twenty times a second, then a knob is set twenty times a second from
    elsewhere. Each push is read, but: a press (its light) is a read of
    /health alone, so the rig is read only by the pages' second's poll; and
    a change reads the rig at most twice a second a page more, and pages
    reading together share one read of Sushi. Before (#242's review) each
    press and each change was a whole poll, back to back on every page."""
    with Served(save=True, pedals=True) as s:
        pages = []
        for _ in range(3):
            page = browser.new_page(viewport=PHONE)
            page.goto(s.url + "/#stage")
            page.wait_for_selector(".stage-screen .card")
            pages.append(page)
        until(lambda: len(s.agent.events.streams) == 3)
        clock = pages[0]

        before, began = rig_reads(s), time.monotonic()
        for _ in range(40):
            s.pedals.press("d")
            clock.wait_for_timeout(50)
        lights = rig_reads(s) - before
        took = time.monotonic() - began
        # The second's poll alone: a page polls once a second, and one more
        # read each is room for a poll at either end.
        assert lights <= len(pages) * (took + 2), (lights, took)

        before, began = rig_reads(s), time.monotonic()
        for n in range(40):
            agent_client.ask(s.url, "POST", "/live/parameter",
                             {"processor": "overdrive", "parameter": "Tone", "value": 0.3 + n * 0.01})
            clock.wait_for_timeout(50)
        changes = rig_reads(s) - before
        took = time.monotonic() - began
        # The poll, and at most two reads a second from pushes, a page.
        assert changes <= len(pages) * (3 * took + 2), (changes, took)
        print(f"rig reads: {lights} over {len(pages)} pages for 40 presses, {changes} for 40 changes")
        # And the last change shows everywhere.
        for page in pages:
            page.locator('.card[data-unit="overdrive"] button.open-card').click()
            expect(slider(page, "overdrive Tone")).to_have_attribute("aria-valuenow", "69")
        for page in pages:
            page.close()


def test_a_mapped_pedal_swept_reads_the_rig_and_a_footswitch_only_health(browser):  # noqa: F811
    """The expression pedal, once the rig maps it (a volume, here, played
    after the restart the assignment asks for), moves a parameter through
    Sushi's own mapping, with no write of the agent's: its push names "live"
    and the watching page reads the rig. Footswitch D, on nothing, is a light
    only: the page reads /health and not the rig. The watching page's poll is
    stopped, so each read is the push's."""
    with Served(save=True, pedals=True) as s:
        assert agent_client.ask(s.url, "POST", "/rig/midi/assign", {"control": "pedal", "volume": "vocal"})[0] == 200
        assert agent_client.ask(s.url, "POST", "/rig/restart", {})[0] == 200
        until(lambda: any(f.sushi_ccs for f in s.agent._midi_plan.feeds))
        watching = Watching(browser, s, "/#stage")
        watching.page.wait_for_selector(".stage-screen .card")
        watching.freeze()
        frozen(watching, seconds=1.5)

        reads = rig_reads(s)
        s.pedals.press("d")
        watching.until(lambda: "health" in watching.asked)
        watching.wait(500)
        assert "live" not in watching.asked and rig_reads(s) == reads, watching.asked

        watching.asked.clear()
        s.pedals.sweep(90)
        watching.until(lambda: "live" in watching.asked)
        assert rig_reads(s) > reads
        watching.close()
