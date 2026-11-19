"""The takes on the page (#189): no list and no polling, one line naming the
takes folder, Open folder on the laptop itself, and a stop that names the
take, its folder and `take check`'s verdict.

Two layers, as test_ui.py has them: rig.js's decisions imported into a bare
page, then the rack and the stage driven in Chromium against a real agent
over the fake of `ui_fake.py` with a recorder (`Served(record=True)`), whose
takes are real folders in a temporary checkout.

What stands in for what. The fake agent is served on 127.0.0.1, so the page
is "on this machine"; "a phone" is `Served(peer=...)`, every request handed
to the agent as from a LAN address, which is all the agent can tell of one.
The file manager's opener is a stand-in: a small Python program that writes
down what it was given. Nothing here runs `xdg-open` or `gio open`, or opens
a folder on a desktop. The 15 s an opened notice stays is passed on
Playwright's fake clock.
"""

from __future__ import annotations

import json
import re
import sys

import pytest

pytest.importorskip("aiohttp")

from test_ui import (  # noqa: E402,F401
    FIRST, Watched, blank, browser, expect, no_sideways_scroll, record_served, rigjs, served,
    stage_page,
)
from ui_fake import Served  # noqa: E402

PHONE = "192.168.1.23"


def opener_into(tmp_path, then="", exit_code=0):
    """A stand-in opener: writes its arguments to a file, runs `then`, exits."""
    seen = tmp_path / "opener-saw.json"
    script = (f"import json, sys\nopen({str(seen)!r}, 'w').write(json.dumps(sys.argv[1:]))\n"
              f"{then}\nsys.exit({exit_code})\n")
    return [sys.executable, "-c", script], seen


def rack(browser, served, width=1280, clock=False):
    page = Watched.__new__(Watched)
    page.asked, page.console, page.errors = [], [], []
    page.page = browser.new_page(viewport={"width": width, "height": 900})
    page.page.on("pageerror", lambda error: page.errors.append(str(error)))
    page.page.on("request", lambda r: page.asked.append(r.url[len(served.url):]))
    if clock:
        page.page.clock.install()
    page.page.goto(served.url + "/#rack")
    page.page.wait_for_selector("section.takes h2")
    return page


def takes_dir(served):
    return str(served.recorder.takes.resolve())


# --------------------------------------------------------------------------- #
# The decisions, pure (rig.js)
# --------------------------------------------------------------------------- #

def test_the_takes_line_names_the_folder_and_offers_open_only_on_this_machine(blank):
    out = rigjs(blank, """
      const health = (takes) => ({ok: true, body: {agent: {takes}, rig: {}}});
      const here = health({dir: '/home/p/sushi-rig-takes', can_open: true});
      return {
        here: R.takesFolder(here),
        opening: R.takesFolder(here, {pending: true}).button,
        phone: R.takesFolder(health({dir: '/home/p/sushi-rig-takes', can_open: false})),
        older: R.takesFolder(health(undefined)),
        down: R.takesFolder({ok: false, reason: 'x'}),
        opened: R.openFolderOutcome({ok: true, body: {said: 'xdg-open opened /home/p/sushi-rig-takes'}}),
        refused: R.openFolderOutcome({ok: false, status: 409, reason: 'nothing here opens a folder', code: 'no_opener'}),
        call: R.OPEN_FOLDER_CALL,
      };""")
    assert out["here"] == {"dir": "/home/p/sushi-rig-takes", "button": {"label": "Open folder", "disabled": False},
                           "result": None}
    assert out["opening"] == {"label": "Opening…", "disabled": True}
    assert out["phone"]["dir"] == "/home/p/sushi-rig-takes" and out["phone"]["button"] is None, "the path, no button"
    assert out["older"]["dir"] is None and out["older"]["button"] is None, "an agent that does not say offers nothing"
    assert out["down"]["button"] is None
    assert out["opened"] == {"ok": True, "title": "Opened the takes folder", "text": "xdg-open opened /home/p/sushi-rig-takes"}
    assert out["refused"] == {"ok": False, "title": "Not opened", "reason": "nothing here opens a folder", "code": "no_opener"}
    assert out["call"] == {"path": "/takes/open", "json": {}}


def test_a_stop_names_the_take_its_folder_and_a_broken_take_is_not_ok(blank):
    out = rigjs(blank, """
      return [
        R.recordOutcome("stop", {ok: true, body: {name: "riff", folder: "/t/riff", seconds: 64, stems: [{}, {}], problems: []}}),
        R.recordOutcome("stop", {ok: true, body: {name: "late", folder: "/t/late", seconds: 3, stems: [{}],
          problems: ["the recorder lost audio 1 time(s), 5.3 ms in all"]}}),
        R.recordOutcome("stop", {ok: false, status: 502, reason: "stop failed for cut: no engine", code: "failed",
          body: {name: "cut", folder: "/t/cut", seconds: null, stems: [], problems: []}}),
      ];""")
    assert out[0] == {"ok": True, "title": "Recorded riff", "text": "1:04 · Whole · 2 stems · nothing lost",
                      "reason": None, "folder": "/t/riff"}
    assert out[1]["ok"] is False and out[1]["folder"] == "/t/late"
    assert out[1]["text"] == "0:03 · 1 problem: the recorder lost audio 1 time(s), 5.3 ms in all"
    assert out[2]["ok"] is False and out[2]["reason"] == "stop failed for cut: no engine", "a failed stop says why"


# --------------------------------------------------------------------------- #
# The rack
# --------------------------------------------------------------------------- #

def test_the_rack_names_the_takes_folder_and_lists_no_takes(browser, record_served):
    page = stage_page(browser, record_served)
    page.page.get_by_role("button", name="Record", exact=True).click()
    page.page.get_by_role("button", name="Stop", exact=True).click()
    expect(page.page.locator(".record-result")).to_contain_text(f"Recorded {FIRST}")
    page.close()
    rack_page = rack(browser, record_served)
    p = rack_page.page
    takes = p.locator("section.takes")
    expect(takes.locator("h2")).to_have_text("TAKES")
    expect(takes.locator(".takes-dir")).to_have_text(f"Takes go in {takes_dir(record_served)}")
    expect(takes.get_by_role("button", name="Open folder")).to_be_visible()
    expect(takes).not_to_contain_text(FIRST), "no list of the takes"
    expect(takes.locator("li")).to_have_count(0)
    rack_page.close()


def test_neither_screen_asks_for_the_takes(browser, record_served):
    """No GET /takes from the rack, as it opens, while a take starts and
    stops on another page, nor over the polls after."""
    rack_page = rack(browser, record_served)
    p = rack_page.page
    stage = stage_page(browser, record_served)
    stage.page.get_by_role("button", name="Record", exact=True).click()
    expect(stage.page.locator(".record-bar h2")).to_have_text("Recording")
    stage.page.get_by_role("button", name="Stop", exact=True).click()
    expect(stage.page.locator(".record-result")).to_contain_text(f"Recorded {FIRST}")
    # The rack has seen the take start and stop (its /record follows /live),
    # then a few more polls.
    polls = rack_page.asked.count("/health")
    p.wait_for_function(f"() => performance.getEntriesByType('resource').filter((r) => r.name.endsWith('/health')).length > {polls + 3}")
    assert "/record" in rack_page.asked
    assert "/takes" not in rack_page.asked and "/takes" not in stage.asked
    stage.close()
    rack_page.close()


def test_open_folder_opens_the_takes_folder_on_this_machine(browser, record_served, tmp_path):
    record_served.recorder.takes.mkdir(parents=True, exist_ok=True)
    opener, seen = opener_into(tmp_path)
    record_served.agent.folder_opener = lambda: opener
    rack_page = rack(browser, record_served, clock=True)
    p = rack_page.page
    p.get_by_role("button", name="Open folder").click()
    said = p.locator(".open-result")
    expect(said).to_contain_text("Opened the takes folder")
    expect(said).to_contain_text(f"opened {takes_dir(record_served)}")
    assert json.loads(seen.read_text()) == [takes_dir(record_served)]
    p.clock.run_for(14_000)
    expect(said).to_be_visible()
    p.clock.run_for(1_500)
    expect(said).to_have_count(0), "it goes by itself, as the rig picker's notices that worked do"
    rack_page.close()


def test_open_folder_with_no_opener_installed_says_so_and_stays(browser, record_served):
    record_served.recorder.takes.mkdir(parents=True, exist_ok=True)
    record_served.agent.folder_opener = lambda: None
    rack_page = rack(browser, record_served, clock=True)
    p = rack_page.page
    p.get_by_role("button", name="Open folder").click()
    said = p.locator(".open-result")
    expect(said).to_contain_text("Not opened")
    expect(said).to_contain_text("neither xdg-open nor gio open is installed")
    expect(said).to_contain_text(f"The takes are in {takes_dir(record_served)}")
    expect(said).to_have_attribute("role", "alert")
    p.clock.run_for(60_000)
    expect(said).to_be_visible(), "a refusal stays until it is dismissed"
    said.get_by_role("button", name="Dismiss").click()
    expect(said).to_have_count(0)
    rack_page.close()


def test_a_phone_sees_the_takes_folder_and_no_open_button(browser, tmp_path):
    with Served(record=True, peer=PHONE) as phone:
        phone.recorder.takes.mkdir(parents=True, exist_ok=True)
        opener, seen = opener_into(tmp_path)
        phone.agent.folder_opener = lambda: opener
        rack_page = rack(browser, phone, width=390)
        p = rack_page.page
        expect(p.locator(".takes-dir")).to_have_text(f"Takes go in {takes_dir(phone)}")
        expect(p.locator("section.takes").get_by_role("button")).to_have_count(0)
        no_sideways_scroll(p)
        # Asked anyway, from the page: the agent refuses a peer that is not this machine.
        answer = p.evaluate("() => fetch('/takes/open', {method: 'POST', headers: {'content-type': 'application/json'}, "
                            "body: '{}'}).then(async (r) => [r.status, await r.json()])")
        assert answer[0] == 403 and answer[1]["code"] == "not_local", answer
        assert not seen.exists(), "nothing was run"
        rack_page.close()


# --------------------------------------------------------------------------- #
# A take stopping
# --------------------------------------------------------------------------- #

def test_a_stop_says_the_take_its_folder_and_its_verdict(browser, record_served):
    page = stage_page(browser, record_served)
    p = page.page
    p.get_by_label("Take name").fill("riff-idea")
    p.get_by_role("button", name="Record", exact=True).click()
    p.get_by_role("button", name="Stop", exact=True).click()
    result = p.locator(".record-result")
    expect(result.locator("strong")).to_have_text("Recorded riff-idea")
    expect(result).to_contain_text("Whole · 5 stems · nothing lost")
    expect(result.locator(".take-folder")).to_have_text(str(record_served.recorder.takes.resolve() / "riff-idea"))
    expect(result).to_have_class(re.compile(r"\bok\b"))
    no_sideways_scroll(p)
    page.close()


def test_a_broken_take_is_flagged_when_it_stops(browser, record_served):
    """The stop's answer as the agent gives it for a take `take check` finds
    problems in (here the recorder's real answer, with a problem added on its
    way to the page): marked, as an alert, with the first problem said."""
    page = stage_page(browser, record_served)
    p = page.page

    def broken(route):
        response = route.fetch()
        body = response.json()
        body["problems"] = ["the recorder lost audio 2 time(s), 10.7 ms in all — the stems are still aligned"]
        route.fulfill(response=response, json=body)

    p.route("**/record/stop", broken)
    p.get_by_role("button", name="Record", exact=True).click()
    p.get_by_role("button", name="Stop", exact=True).click()
    result = p.locator(".record-result")
    expect(result.locator("strong")).to_have_text(f"Recorded {FIRST}")
    expect(result).to_have_class(re.compile(r"\brefused\b"))
    expect(result).to_have_attribute("role", "alert")
    expect(result).to_contain_text("1 problem: the recorder lost audio 2 time(s), 10.7 ms in all")
    expect(result.locator(".take-folder")).to_have_text(str(record_served.recorder.takes.resolve() / FIRST))
    page.close()

