"""The last of #139 on the page: Broken (the canvas's S9), and on the record
bar the meters, the free space and hours of takes left, and a take's stems
to play or save.

Two layers, as test_ui.py has them: rig.js's decisions imported into a bare
page (`brokenView`, `spaceView`, `hoursLeft`, `meterStep`, `meterView`,
`takeChoices`, `takeFileUrl`), then the stage and the rack in Chromium
against a real agent over the fake rig of `ui_fake.py`.

What stands in for what. The rig's children, their crashes and the
lifecycle commands are the fake's (`UiLifecycle`, the supervisor's crash
record as `exit_record` writes it). The meters' level source is
`ui_fake.Playing`: a thread pushing made-up blocks at the pace a quantum
would come, into the real recorder (`RecorderCore`, `Recorder`, its ring,
writer and meter), so what is measured is the recorder's own measuring,
of levels a test sets. The free space is `doctor.free_bytes` stood in where
a test needs a number; elsewhere it is this machine's disk. The stems
played are real takes' files, served by the agent's real route, in
Chromium's own audio element. Nothing here is PipeWire, JACK or a sound card.
"""

from __future__ import annotations

import re
import time
from pathlib import Path

import pytest

pytest.importorskip("aiohttp")
pytest.importorskip("numpy")

from test_ui import Page, blank, browser, expect, rigjs, served, until  # noqa: E402,F401
from ui_fake import Playing, Served, UiLifecycle  # noqa: E402

from sushi_rig import doctor  # noqa: E402
from sushi_rig.supervisor import exit_record  # noqa: E402

RUN = {"running": True, "pgid": 4242, "started_at": "2026-11-26T20:00:00+00:00", "config_name": "lead",
       "log_dir": "/run/user/1000/sushi-rig/logs"}


def crash(name="amp", pgid=4242, started_at="2026-11-26T20:00:00+00:00", log="/run/user/1000/sushi-rig/logs/amp.log"):
    return exit_record(name, 8302, -11, at="2026-11-26T21:08:22+00:00", phase="running", config_name="lead",
                       log=Path(log) if log else None, pgid=pgid, started_at=started_at)


def health(rig=RUN, **agent):
    return {"ok": True, "body": {"rig": rig, "agent": {"supervisor": {"crashed": [], "problems": []}, **agent}}}


# --------------------------------------------------------------------------- #
# Broken, pure
# --------------------------------------------------------------------------- #

def test_a_healthy_rig_a_rig_down_a_busy_one_and_no_agent_are_not_broken(blank):
    out = rigjs(blank, "return arg.map(([h, l]) => R.brokenView(h, l));", [
        [health(), {"ok": True}],
        [health({"running": False, "verdict": "absent"}), None],
        [health(busy="restart", supervisor={"crashed": [crash()]}), None],
        [{"ok": False, "reason": "the agent did not answer"}, None],
        [None, None],
    ])
    assert out == [None] * 5


def test_a_child_of_this_run_that_exited_is_broken_with_its_log_and_restart_and_stop(blank):
    view = rigjs(blank, "return R.brokenView(arg, {ok: true});", health(supervisor={"crashed": [crash()]}))
    assert view["word"] == "Amp exited" and view["more"] == 0
    assert view["log"] == "/run/user/1000/sushi-rig/logs/amp.log"
    assert "the guitar through the amp is silent" in view["title"] and "Log: /run/user/1000/sushi-rig/logs/amp.log" in view["title"]
    assert view["actions"] == [{"op": "restart", "name": "lead", "label": "Restart", "primary": True},
                               {"op": "stop", "name": "lead", "label": "Stop", "primary": False}]


def test_a_crash_of_an_earlier_run_is_not_this_rig_broken(blank):
    view = rigjs(blank, "return R.brokenView(arg, {ok: true});",
                 health(supervisor={"crashed": [crash(pgid=999), crash(started_at="2026-11-25T20:00:00+00:00")]}))
    assert view is None


def test_the_worst_is_the_word_and_the_rest_are_counted_and_in_the_title(blank):
    agent = {"supervisor": {"crashed": [crash("fmit", log=None), crash("amp")]},
             "warnings": ["1 link(s) in electric_board.qpwgraph were not made"]}
    view = rigjs(blank, "return R.brokenView(arg, {ok: false, reason: 'connection refused'});", health(**agent))
    assert view["word"] == "Sushi unreachable" and view["more"] == 3
    words = [line.split(":")[0] for line in view["title"].split("\n\n")]
    assert words == ["Sushi unreachable", "Amp exited", "Links not made", "Tuner exited", "Log"]
    assert view["log"] == "/run/user/1000/sushi-rig/logs/amp.log", "the first fault that has a log names it"
    sushi = rigjs(blank, "return R.brokenView(arg, {ok: true}).word;", health(supervisor={"crashed": [crash("fmit"), crash("sushi")]}))
    assert sushi == "Sushi exited"


@pytest.mark.parametrize("agent, word", [
    ({"warnings": ["NO AMP: amp did not start: carla exited"]}, "No amp"),
    ({"amp_wiring": {"wired": False, "problems": ["NAM:Input is not linked"]}}, "Amp not wired"),
    ({"warnings": ["2 link(s) in x.qpwgraph were not made"]}, "Links not made"),
])
def test_the_amp_down_and_links_not_made_are_broken_in_their_status_words(blank, agent, word):
    view = rigjs(blank, "return R.brokenView(arg, {ok: true});", health(**agent))
    assert view["word"] == word and view["log"] == "/run/user/1000/sushi-rig/logs", "the run's logs, where no file is named"
    assert [a["op"] for a in view["actions"]] == ["restart", "stop"]


def test_a_failed_start_is_broken_until_something_starts_and_offers_to_start_it_again(blank):
    down = {"running": False, "verdict": "absent"}
    failed = {"kind": "lifecycle", "command": ["up", "lead", "--rig"], "at": "t", "message": "error: no machine file (test)"}
    out = rigjs(blank, "return arg.map((h) => R.brokenView(h, null));", [
        health(down, last_error=failed),
        health(down, last_error={**failed, "command": ["restart"]}),
        health(down, last_error={**failed, "kind": "live"}),
        health(down, last_error={**failed, "command": ["down"]}),
    ])
    assert out[0]["word"] == "Start failed" and "no machine file (test)" in out[0]["title"]
    assert out[0]["actions"] == [{"op": "start", "name": "lead", "label": "Start again", "primary": True}]
    assert out[0]["log"] is None, "a rig that never came up has no run's logs to name"
    assert out[1]["word"] == "Restart failed" and out[1]["actions"] == [], "no name to start again: the picker starts one"
    assert out[2] is None and out[3] is None


# --------------------------------------------------------------------------- #
# The free space and the hours of takes, pure
# --------------------------------------------------------------------------- #

def test_hours_left_are_rounded_down_and_said_in_a_word_or_two(blank):
    out = rigjs(blank, "return arg.map((s) => R.hoursLeft(s));", [147600, 7200, 7199, 3599, 59, 0, -1, None])
    assert out == ["41 h", "2 h", "119 min", "59 min", "0 min", "0 min", None, None]


def test_the_space_is_a_word_or_two_with_the_detail_in_the_title(blank):
    rate = 5 * 48000 * 4
    out = rigjs(blank, "return arg.map((s) => R.spaceView(s));", [
        {"free_bytes": 147600 * rate, "bytes_per_second": rate, "seconds_left": 147600, "dir": "/t", "problem": None},
        {"free_bytes": 3000 * rate, "bytes_per_second": rate, "seconds_left": 3000, "dir": "/t", "problem": None},
        {"free_bytes": 480 * rate, "bytes_per_second": rate, "seconds_left": 480, "dir": "/t", "problem": None},
        {"free_bytes": None, "bytes_per_second": rate, "seconds_left": None, "dir": "/t",
         "problem": "/t is a link to /media/x, which does not exist"},
        {"free_bytes": 10 ** 9, "bytes_per_second": None, "seconds_left": None, "dir": "/t", "problem": None},
        None,
    ])
    assert out[0]["text"] == "41 h left" and out[0]["tone"] == ""
    assert out[0]["title"] == ("142 GB free at /t: about 41 h 0 min of takes at 0.96 MB a second: "
                               "the stems a take records now, in float32")
    assert (out[1]["text"], out[1]["tone"]) == ("50 min left", "warn")
    assert (out[2]["text"], out[2]["tone"]) == ("8 min left", "over")
    assert out[3] == {"text": "Space unknown", "tone": "over", "title": "/t is a link to /media/x, which does not exist"}
    assert out[4] == {"text": "1.0 GB free", "tone": "", "title": "1.0 GB free at /t"}
    assert out[5] is None


# --------------------------------------------------------------------------- #
# The meters, pure
# --------------------------------------------------------------------------- #

def test_a_meter_shows_the_peak_holds_it_and_falls_back(blank):
    out = rigjs(blank, """
      const stems = (a, b) => [{file: 'board.wav', channels: 2, peaks_dbfs: a}, {file: 'board-dry.wav', channels: 1, peaks_dbfs: b}];
      const M = R.METER;
      const s0 = R.meterStep(null, stems([-12, -20], [-30]), 0);
      const s1 = R.meterStep(s0, stems([-40, -40], [null]), 500);
      const s2 = R.meterStep(s1, stems(null, null), 1000);
      const s3 = R.meterStep(s2, stems([-40, -40], [-30]), M.holdMs + 100);
      return {M, s0, s1, s2, s3, v0: R.meterView(s0[0], 0), v3: R.meterView(s3[0], M.holdMs + 100)};""")
    meter = out["M"]
    assert meter == {"floorDb": -60, "clipDbfs": -0.1, "holdMs": 1500, "fallDbPerS": 24, "clipMs": 3000}
    board = [s[0] for s in (out["s0"], out["s1"], out["s2"], out["s3"])]
    assert [b["label"] for b in board] == ["board"] * 4 and out["s0"][1]["label"] == "board-dry"
    assert board[0]["db"] == -12, "a stem is its loudest channel"
    assert board[1]["db"] == pytest.approx(-24), "half a second later it has fallen 12 dB, not to the new -40"
    assert board[1]["hold"] == -12 and board[2]["hold"] == -12, "the hold stays for holdMs"
    assert board[2]["db"] == pytest.approx(-36), "nothing measured: it keeps falling"
    assert board[3]["hold"] == pytest.approx(-40), "after holdMs the hold comes down to what is there"
    assert out["s1"][1]["db"] == pytest.approx(-42), "a silent channel is the floor, falling from where it was"
    assert out["v0"] == {"label": "board", "fill": 80, "hold": 80, "clip": False,
                         "title": "board.wav: peak −12.0 dBFS (−12.0, −20.0), held −12.0"}
    assert out["v3"]["fill"] == pytest.approx(33.3) and out["v3"]["hold"] == pytest.approx(33.3)


def test_a_meter_goes_red_at_a_clip_and_stays_red_long_enough_to_be_seen(blank):
    out = rigjs(blank, """
      const one = (p) => [{file: 'vocal.wav', channels: 1, peaks_dbfs: [p]}];
      const a = R.meterStep(null, one(-0.05), 0);
      const b = R.meterStep(a, one(-30), 1000);
      const c = R.meterStep(b, one(-30), R.METER.clipMs + 1);
      const d = R.meterStep(null, one(-0.2), 0);
      const e = R.meterStep(null, one(3.5), 0);
      return [R.meterView(a[0], 0), R.meterView(b[0], 1000), R.meterView(c[0], R.METER.clipMs + 1),
              R.meterView(d[0], 0), R.meterView(e[0], 0)];""")
    assert [v["clip"] for v in out] == [True, True, False, False, True]
    assert out[0]["title"].endswith("· clipped") and out[4]["fill"] == 100, "over full scale: the bar is full"


# --------------------------------------------------------------------------- #
# Takes, pure
# --------------------------------------------------------------------------- #

def test_a_takes_files_are_asked_for_by_name_escaped(blank):
    out = rigjs(blank, "return [R.takeFileUrl('riff', 'board.wav'), R.takeFileUrl('riff', 'board.wav', true), "
                       "R.takeFileUrl('a b', '../x?.wav')];")
    assert out == ["/takes/riff/board.wav", "/takes/riff/board.wav?download=1", "/takes/a%20b/..%2Fx%3F.wav"]


def test_the_takes_offered_are_each_take_with_its_stems_newest_first(blank):
    answer = {"ok": True, "body": {"takes": [
        {"name": "now", "take": True, "seconds": 3, "complete": False, "problems": [], "stems": ["board.wav"], "folder": "/t/now"},
        {"name": "riff", "take": True, "seconds": 64, "complete": True, "problems": [], "stems": ["board.wav", "master.wav"],
         "folder": "/t/riff"},
        {"name": "cut", "take": True, "seconds": 5, "complete": False, "problems": ["never finished"], "stems": [], "folder": "/t/cut"},
        {"name": "junk", "take": False, "problems": ["no take.yaml"], "stems": [], "folder": "/t/junk"},
    ]}}
    out = rigjs(blank, "return R.takeChoices(arg, 'now');", answer)
    assert [t["text"] for t in out] == ["now · 0:03 · recording", "riff · 1:04", "cut · 0:05 · problems"]
    assert out[1]["stems"] == [
        {"file": "board.wav", "label": "board", "src": "/takes/riff/board.wav", "save": "/takes/riff/board.wav?download=1"},
        {"file": "master.wav", "label": "master", "src": "/takes/riff/master.wav", "save": "/takes/riff/master.wav?download=1"}]
    assert out[2]["title"] == "/t/cut · never finished"
    assert rigjs(blank, "return R.takeChoices({ok: false, reason: 'x'});") == []


# --------------------------------------------------------------------------- #
# Broken, in Chromium
# --------------------------------------------------------------------------- #

def page_at(browser, served, screen, width=None):
    size = {"width": width or (390 if screen == "stage" else 1280), "height": 844 if screen == "stage" else 900}
    page = Page(browser, f"{served.url}/#{screen}", **size)
    page.page.wait_for_selector(".card" if screen == "stage" else ".unit")
    return page


def crash_the_amp(served):
    up = served.state["describe"]()
    served.agent.supervisor.crashed.append(crash(pgid=up["pgid"], started_at=up["started_at"]))


@pytest.mark.parametrize("screen", ["stage", "rack"])
def test_a_crashed_amp_is_broken_on_both_screens_with_its_fix_working_locked(browser, screen):
    with Served() as served:
        page = page_at(browser, served, screen)
        p = page.page
        band = p.locator("section.broken")
        expect(band).to_be_hidden()
        crash_the_amp(served)
        expect(band).to_be_visible()
        expect(band.locator(".broken-word")).to_have_text("Amp exited")
        expect(band.locator(".broken-word")).to_have_attribute("title", re.compile(re.escape("the guitar through the amp is silent")))
        expect(band.locator(".broken-log")).to_have_text("/run/user/1000/sushi-rig/logs/amp.log")
        expect(p.locator(".screen")).to_have_class(re.compile(r"\bis-broken\b"))
        # The status zone still says it too, in its own words.
        expect(p.locator("header .problem.agent", has_text="exited")).to_have_count(1)
        expect(p.locator("button.lock")).to_have_text("Locked")
        # Restart asks first, in the activity zone, and works locked.
        band.get_by_role("button", name="Restart").click()
        ask = p.locator(".activity [data-ask]")
        expect(ask).to_contain_text("Restart?")
        assert served.lifecycle.calls == []
        ask.get_by_role("button", name="Yes").click()
        until(lambda: served.lifecycle.calls == [["restart"]])
        # A restart is a new run: the crash is the run before's, and Broken goes.
        expect(band).to_be_hidden()
        expect(p.locator(".screen")).not_to_have_class(re.compile(r"\bis-broken\b"))
        assert page.errors == []
        page.close()


@pytest.mark.parametrize("screen, width", [("stage", 390), ("rack", 1280), ("rack", 390)])
def test_broken_coming_moves_nothing_on_the_page_and_sits_at_the_foot_in_view(browser, screen, width):
    """A fader under a finger must stay where it is when the rig breaks
    (test_ui_tabs.py's finding 1): the band is held at the foot, over the
    page and above the screens' bar, and in the flow only at the page's end."""
    with Served() as served:
        page = page_at(browser, served, screen, width=width)
        p = page.page
        target = p.locator(".record-bar" if screen == "stage" else ".unit").first
        # Measured from the activity zone's foot: the crash's own box in the
        # status zone (#245) can make the header a row taller on a phone,
        # which is the status zone's, and was so before the band.
        gap = "(e) => e.getBoundingClientRect().top - document.querySelector('.activity').getBoundingClientRect().bottom"
        before = target.evaluate(gap)
        crash_the_amp(served)
        band = p.locator("section.broken")
        expect(band).to_be_visible()
        assert target.evaluate(gap) == before, "nothing came between the activity zone and the page"
        order = p.evaluate("() => [...document.querySelector('.screen').children].map((e) => e.className.split(' ')[0])")
        assert order[-2:] == ["broken", "stage-nav"], order
        box = band.bounding_box()
        height = p.evaluate("() => innerHeight")
        nav = p.locator(".stage-nav").filter(has=p.locator("a.here"))
        foot = nav.bounding_box()["y"] if nav.is_visible() else height
        assert box["y"] >= 0 and box["y"] + box["height"] <= foot + 1, (box, foot, height)
        band.get_by_role("button", name="Restart").click()
        expect(p.locator(".activity [data-ask]")).to_contain_text("Restart?")
        page.close()


def test_stop_from_broken_asks_and_a_no_changes_nothing(browser):
    with Served() as served:
        page = page_at(browser, served, "stage")
        p = page.page
        crash_the_amp(served)
        band = p.locator("section.broken")
        band.get_by_role("button", name="Stop").click()
        ask = p.locator(".activity [data-ask]")
        expect(ask).to_contain_text("Stop?")
        ask.get_by_role("button", name="No").click()
        expect(p.locator(".activity [data-ask]")).to_have_count(0)
        assert served.lifecycle.calls == []
        band.get_by_role("button", name="Stop").click()
        p.locator(".activity [data-ask]").get_by_role("button", name="Yes").click()
        until(lambda: served.lifecycle.calls == [["down"]])
        expect(band).to_be_hidden()
        page.close()


def test_a_failed_start_is_broken_and_start_again_runs_it_once_more(browser):
    world = UiLifecycle(up=None)
    world.refuse_up = "no machine file for this host (test)"
    with Served(lifecycle=world) as served:
        page = Page(browser, served.url + "/#rack")
        p = page.page
        p.wait_for_selector(".rigs .rig")
        band = p.locator("section.broken")
        expect(band).to_be_hidden()
        p.locator(".rig", has_text="electric-delay").click()
        p.get_by_role("button", name="Start electric-delay").click()
        expect(band.locator(".broken-word")).to_have_text("Start failed")
        expect(band.locator(".broken-word")).to_have_attribute("title", re.compile(re.escape("no machine file for this host (test)")))
        expect(band.get_by_role("button")).to_have_text(["Start again"])
        world.refuse_up = None
        band.get_by_role("button", name="Start again").click()
        until(lambda: world.calls == [["up", "electric-delay"], ["up", "electric-delay"]])
        expect(band).to_be_hidden()
        page.close()


def test_sushi_not_answering_is_broken(browser):
    from ui_fake import UiRig

    rig = UiRig()

    def refuse(*_):
        raise __import__("sushi_rig.live", fromlist=["LiveError"]).LiveError("Sushi stopped answering (test)")

    rig.connect = refuse
    with Served(rig) as served:
        page = Page(browser, served.url + "/#stage", width=390, height=844)
        band = page.page.locator("section.broken")
        expect(band.locator(".broken-word")).to_have_text("Sushi unreachable")
        expect(band.locator(".broken-word")).to_have_attribute("title", re.compile(re.escape("Sushi stopped answering (test)")))
        expect(band.get_by_role("button")).to_have_text(["Restart", "Stop"])
        page.close()


def test_broken_fits_a_phone_without_sideways_scroll(browser):
    with Served() as served:
        page = page_at(browser, served, "stage", width=320)
        p = page.page
        crash_the_amp(served)
        expect(p.locator("section.broken")).to_be_visible()
        width = p.evaluate("() => [document.documentElement.scrollWidth, innerWidth]")
        assert width[0] <= width[1], width
        page.close()


# --------------------------------------------------------------------------- #
# The meters, in Chromium
# --------------------------------------------------------------------------- #

@pytest.fixture
def playing():
    """A served rig whose takes play on (`Playing`), at levels a test sets."""
    before = list(Playing.levels)
    with Served(record=True) as served:
        served.recorder.engine = Playing
        yield served
    Playing.levels = before


# Where a meter's bar ends and its hold stands, in % of the scale, from the
# transforms `drawMeters` sets.
FILL = "(e) => 100 * parseFloat(/scaleX\\(([^)]+)\\)/.exec(e.style.transform)[1])"
HOLD = "(e) => parseFloat(/translateX\\(([^)%]+)%\\)/.exec(e.style.transform)[1])"


def recording(browser, served, screen="stage", width=390):
    page = Page(browser, f"{served.url}/#{screen}", width=width, height=844)
    asked = []
    page.page.on("request", lambda r: asked.append(r.url[len(served.url):]))
    page.asked = asked
    page.page.wait_for_selector(".record-bar h2")
    page.page.get_by_role("button", name="Record", exact=True).click()
    expect(page.page.locator(".record-bar")).to_have_class(re.compile(r"\brecording\b"))
    return page


def test_each_stem_has_a_meter_of_its_peak_and_hold_in_dbfs_and_a_clip_goes_red(browser, playing):
    # board (2), board-dry, vocal (2), vocal-dry, master (2): by column.
    Playing.levels = [-12.0, -12.0, -0.05, -30.0, -30.0, None, -6.0]
    page = recording(browser, playing)
    p = page.page
    meters = p.locator(".record-bar .meter")
    expect(meters).to_have_count(5)
    expect(p.locator(".record-bar .meter-label")).to_have_text(["board", "board-dry", "vocal", "vocal-dry", "master"])
    dry = meters.nth(1)
    expect(dry).to_have_class(re.compile(r"\bclip\b"))
    expect(meters.nth(0)).not_to_have_class(re.compile(r"\bclip\b"))
    expect(meters.nth(0)).to_have_attribute("title", re.compile(r"^board\.wav: peak −12\.0 dBFS \(−12\.0, −12\.0\), held −12\.0$"))
    expect(meters.nth(3)).to_have_attribute("title", re.compile(r"peak −∞ dBFS"))
    fill = meters.nth(0).locator(".meter-fill").evaluate(FILL)
    assert fill == pytest.approx(80, abs=0.2), "-12 dBFS is 80% of a -60 to 0 scale"
    clipped = dry.locator(".meter-fill").evaluate("(e) => getComputedStyle(e).backgroundColor")
    plain = meters.nth(0).locator(".meter-fill").evaluate("(e) => getComputedStyle(e).backgroundColor")
    assert clipped == "rgb(224, 81, 75)" and plain != clipped, "red at the clip: the recording red"
    # Quieter: the bar falls, the hold stays where the peak was, then comes down.
    Playing.levels = [-40.0]
    board = meters.nth(0)
    p.wait_for_timeout(700)
    hold = board.locator(".meter-hold").evaluate(HOLD)
    now = board.locator(".meter-fill").evaluate(FILL)
    assert hold == pytest.approx(80, abs=0.2) and now < 70, (hold, now)
    expect(board).to_have_attribute("title", re.compile(r"held −40\.0$"), timeout=4000)
    expect(dry).not_to_have_class(re.compile(r"\bclip\b"), timeout=6000)
    assert page.errors == []
    page.close()


def test_the_meters_are_read_only_while_a_take_runs_on_the_stage_and_cost_nothing_after(browser, playing):
    page = recording(browser, playing)
    p = page.page
    expect(p.locator(".record-bar .meter")).to_have_count(5)
    p.wait_for_timeout(1000)
    reads = page.asked.count("/record/levels")
    assert 5 <= reads <= 12, f"about ten reads a second while shown, {reads}"
    meter = playing.recorder.core._take.session._recorder.meter
    assert meter.armed()
    # On the rack there is no record bar: no reads, and the recorder stops measuring.
    p.goto(playing.url + "/#rack")
    p.wait_for_selector(".unit")
    page.asked.clear()
    p.wait_for_timeout(2300)
    assert "/record/levels" not in page.asked
    assert not meter.armed(), "no page asking: the writer measures nothing"
    # Back on the stage, then stopped: no more reads.
    p.goto(playing.url + "/#stage")
    expect(p.locator(".record-bar .meter")).to_have_count(5)
    p.get_by_role("button", name="Stop", exact=True).click()
    expect(p.locator(".record-bar")).not_to_have_class(re.compile(r"\brecording\b"))
    expect(p.locator(".record-bar .meter")).to_have_count(0)
    p.wait_for_timeout(300)
    page.asked.clear()
    p.wait_for_timeout(800)
    assert "/record/levels" not in page.asked
    page.close()


def test_a_read_that_fails_fades_the_meters_and_says_nothing(browser, playing):
    page = recording(browser, playing)
    p = page.page
    expect(p.locator(".record-bar .meter")).to_have_count(5)
    p.route("**/record/levels", lambda route: route.fulfill(status=409, json={"ok": False, "reason": "no recorder (test)"}))
    expect(p.locator(".record-bar .meters")).to_have_class(re.compile(r"\bstale\b"))
    expect(p.locator(".activity .problem")).to_have_count(0)
    p.unroute("**/record/levels")
    expect(p.locator(".record-bar .meters")).not_to_have_class(re.compile(r"\bstale\b"))
    page.close()


# --------------------------------------------------------------------------- #
# The free space and the hours of takes, in Chromium
# --------------------------------------------------------------------------- #

def test_the_record_bar_says_the_hours_left_and_the_detail_on_hover(browser, playing, monkeypatch):
    free = {"bytes": 10 ** 12}
    monkeypatch.setattr(doctor, "free_bytes", lambda path: (free["bytes"], ""))
    page = Page(browser, playing.url + "/#stage", width=390, height=844)
    p = page.page
    space = p.locator(".record-bar .record-space")
    expect(space).to_have_text(re.compile(r"^\d+ h left$"))
    import json
    import urllib.request

    body = json.loads(urllib.request.urlopen(playing.url + "/record").read())
    rate = body["space"]["bytes_per_second"]
    assert rate == sum(s["channels"] for s in body["stems"]) * 48000 * 4
    expect(space).to_have_text(f"{10 ** 12 // rate // 3600} h left")
    expect(space).to_have_attribute("title", re.compile(r"^1000 GB free at .*takes: about \d+ h \d+ min of takes at "))
    free["bytes"] = rate * 500
    expect(space).to_have_text("8 min left")
    expect(space).to_have_class(re.compile(r"\btone-over\b"))
    # While recording it is still there, the take's own rate.
    p.get_by_role("button", name="Record", exact=True).click()
    expect(p.locator(".record-bar")).to_have_class(re.compile(r"\brecording\b"))
    expect(space).to_have_text(re.compile(r"^\d min left$"))
    page.close()


# --------------------------------------------------------------------------- #
# Takes: play or save a stem, in Chromium
# --------------------------------------------------------------------------- #

def test_a_takes_stem_plays_in_the_browsers_own_audio_and_saves(browser, playing):
    Playing.levels = [-6.0]
    page = recording(browser, playing)
    p = page.page
    p.wait_for_timeout(600)
    p.get_by_role("button", name="Stop", exact=True).click()
    expect(p.locator(".activity .record-result")).to_contain_text("Recorded")
    take = sorted(d for d in playing.recorder.takes.iterdir() if d.is_dir() and not d.name.startswith("_"))[-1]
    p.locator(".record-bar").get_by_role("button", name="Takes").click()
    player = p.locator("section.take-player")
    expect(player).to_be_visible()
    expect(player.locator("select[name=take] option")).to_have_count(1)
    expect(player.locator("select[name=take]")).to_have_value(take.name)
    stems = player.locator(".stem")
    expect(stems.locator(".stem-label")).to_have_text(["board", "board-dry", "vocal", "vocal-dry", "master"])
    audio = stems.first.locator("audio")
    expect(audio).to_have_attribute("src", f"/takes/{take.name}/board.wav")
    # The browser's own element plays it: Chromium reads the float32 WAV the
    # agent serves (in ranges) and knows its length.
    played = audio.evaluate("""async (a) => {
      a.muted = true; a.preload = 'auto'; a.load();
      await new Promise((ok, no) => { a.onloadedmetadata = ok; a.onerror = () => no(a.error && a.error.message); });
      await a.play();
      await new Promise((ok) => setTimeout(ok, 150));
      return {duration: a.duration, time: a.currentTime, error: a.error};
    }""")
    from sushi_rig.signals import wav_info

    info = wav_info(take / "board.wav")
    assert played["error"] is None and played["duration"] == pytest.approx(info.frames / 48000, abs=0.01)
    assert played["time"] > 0, played
    with p.expect_download() as download:
        stems.first.get_by_role("link", name="Save").click()
    saved = download.value
    assert saved.suggested_filename == "board.wav"
    assert Path(saved.path()).read_bytes() == (take / "board.wav").read_bytes()
    assert page.errors == []
    page.close()


def test_takes_on_the_rack_beside_the_folder(browser, playing):
    page = recording(browser, playing)
    p = page.page
    p.get_by_role("button", name="Stop", exact=True).click()
    expect(p.locator(".activity .record-result")).to_contain_text("Recorded")
    p.goto(playing.url + "/#rack")
    p.wait_for_selector(".unit")
    takes = p.locator("section.takes")
    button = takes.get_by_role("button", name="Takes")
    expect(button).to_have_attribute("aria-expanded", "false")
    button.click()
    expect(button).to_have_attribute("aria-expanded", "true")
    expect(p.locator("section.take-player .stem")).to_have_count(5)
    button.click()
    expect(p.locator("section.take-player")).to_be_hidden()
    page.close()


def test_takes_not_read_is_said_in_the_activity_zone(browser, playing):
    page = Page(browser, playing.url + "/#stage", width=390, height=844)
    p = page.page
    p.wait_for_selector(".record-bar h2")
    p.route("**/takes", lambda route: route.fulfill(status=500, json={"ok": False, "reason": "the disk is gone (test)"}))
    p.locator(".record-bar").get_by_role("button", name="Takes").click()
    expect(p.locator(".activity .problem.refused")).to_contain_text("Takes not read")
    page.close()


def test_the_record_bar_with_meters_fits_a_phone_without_sideways_scroll(browser, playing):
    page = recording(browser, playing, width=320)
    p = page.page
    expect(p.locator(".record-bar .meter")).to_have_count(5)
    p.locator(".record-bar").get_by_role("button", name="Takes").click()
    expect(p.locator("section.take-player")).to_be_visible()
    width = p.evaluate("() => [document.documentElement.scrollWidth, innerWidth]")
    assert width[0] <= width[1], width
    page.close()


def test_time_is_measured_not_assumed_here(playing):
    """The fake's Playing pushes at a quantum's pace: a take of a second has
    about a second of frames, so the meters' tests read a take that runs."""
    import json
    import urllib.request

    start = urllib.request.Request(playing.url + "/record/start", data=b"{}", method="POST",
                                   headers={"Content-Type": "application/json"})
    urllib.request.urlopen(start).read()
    time.sleep(1.0)
    stop = urllib.request.Request(playing.url + "/record/stop", data=b"{}", method="POST",
                                  headers={"Content-Type": "application/json"})
    answer = json.loads(urllib.request.urlopen(stop).read())
    assert 0.7 < answer["seconds"] < 1.5, answer["seconds"]
