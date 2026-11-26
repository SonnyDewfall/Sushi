"""The top of the page in three zones (#245): the rig's status, read only;
the activity, what happened and what waits for an answer, with Undo, pinned
at the top once the header has scrolled away; and the controls (the master,
the Lock, Stage and Help).

The activity model is rig.js's (`activityView`, `activitySeen`,
`activityRemember`, `activityBusy`), tested here pure, in a bare page as the
rest of rig.js is. Then the zones in Chromium, against the fake rig: where
each zone is, that the activity bar stays in view, that a question is
answered from it for the strip it was asked for, and the stage's.

Not tested here: a real phone, and Safari (sticky positioning is seen in
headless Chromium only).
"""

from __future__ import annotations

import re

import pytest

pytest.importorskip("aiohttp")
from playwright.sync_api import expect  # noqa: E402

from ui_fake import Served  # noqa: E402

from test_ui import Page, blank, browser, rigjs, served, until  # noqa: E402,F401


# --------------------------------------------------------------------------- #
# The activity model, pure (rig.js)
# --------------------------------------------------------------------------- #

def test_the_bar_shows_the_newest_question_and_the_last_results_and_counts_the_rest(blank):
    out = rigjs(blank, """
      const item = (id, n) => ({id, n, ok: true, title: id});
      const items = [item('a', 1), item('b', 4), item('c', 2), item('d', 3)];
      const asks = [{key: 'stop', n: 5, q: 'Stop?'}, {key: 'save-restart', n: 6, q: 'Restart?'}];
      const past = [{id: 'old', n: 0, title: 'old'}, {id: 'c', n: 2, title: 'c, standing again'}];
      const ids = (v) => ({ask: v.ask && v.ask.key, busy: v.busy, shown: v.shown.map((i) => i.id),
        asks: v.more.asks.map((a) => a.key), items: v.more.items.map((i) => i.id), past: v.more.past.map((p) => p.id), count: v.count});
      return {
        wide: ids(R.activityView(items, [], [], {wide: true})),
        wideAsking: ids(R.activityView(items, asks, past, {wide: true})),
        narrow: ids(R.activityView(items, [], [], {wide: false})),
        narrowAsking: ids(R.activityView(items, asks, [], {wide: false})),
        busy: ids(R.activityView(items, [], [], {wide: false, busy: 'Restarting electric_board…'})),
        idle: ids(R.activityView([], [], [], {wide: true})),
      };""")
    # A laptop: the last three results, newest first.
    assert out["wide"] == {"ask": None, "busy": None, "shown": ["b", "d", "c"], "asks": [], "items": ["a"], "past": [],
                           "count": 1}
    # The newest question only; an older one waits in "+n", answerable there.
    # The past (`history`) is in "+n" too, less what stands again.
    assert out["wideAsking"] == {"ask": "save-restart", "busy": None, "shown": ["b", "d"], "asks": ["stop"],
                                 "items": ["c", "a"], "past": ["old"], "count": 4}
    # A phone: the latest only, and beside a question or a busy line none.
    assert out["narrow"]["shown"] == ["b"] and out["narrow"]["count"] == 3
    assert out["narrowAsking"]["shown"] == [] and out["narrowAsking"]["count"] == 5
    assert out["busy"]["busy"] == "Restarting electric_board…" and out["busy"]["shown"] == []
    assert out["idle"] == {"ask": None, "busy": None, "shown": [], "asks": [], "items": [], "past": [], "count": 0}


def test_each_result_and_question_keeps_the_place_and_time_it_was_first_seen(blank):
    out = rigjs(blank, """
      const a = {id: 'a', ok: true, title: 'Saved electric_board'};
      const b = {id: 'b', ok: false, title: 'Not removed', said: 'busy', channel: 'vocal'};
      const one = R.activitySeen(null, [a], [{key: 'stop'}], 1000, 0);
      const two = R.activitySeen(one.seen, [a, b], [{key: 'stop'}], 2000, one.counter, one.times);
      const three = R.activitySeen(two.seen, [b], [], 3000, two.counter, two.times);
      // `a` back again (drawn on the rack only, and the rack shown again): its time is its own.
      const four = R.activitySeen(three.seen, [a, b], [{key: 'stop'}], 4000, three.counter, three.times);
      const t = (s, id) => { const x = s.seen.items.get(id); return [x.n, x.at]; };
      return {
        one: t(one, 'a'), twoA: t(two, 'a'), twoB: t(two, 'b'),
        stop: [one.seen.asks.get('stop'), two.seen.asks.get('stop')],
        gone: three.gone, againA: t(four, 'a'), againStop: four.seen.asks.get('stop'),
      };""")
    assert out["one"] == [1, 1000]
    assert out["twoA"] == [1, 1000] and out["twoB"] == [3, 2000], "the same result keeps its number and time"
    assert out["stop"][0] == out["stop"][1] == {"n": 2, "at": 1000}, "a question open keeps its own"
    # What went is kept by its words, for the "+n" list, never its buttons.
    assert out["gone"] == [{"id": "a", "ok": True, "title": "Saved electric_board", "said": None, "detail": None,
                            "channel": None, "elsewhere": False, "n": 1, "at": 1000}]
    assert out["againA"] == [1, 1000]
    assert out["againStop"] == {"n": 4, "at": 4000}, "a question asked again is a new question"


def test_the_past_is_one_entry_a_result_newest_first_and_bounded(blank):
    out = rigjs(blank, """
      const many = Array.from({length: 30}, (_, i) => ({id: 'r' + i, n: i, title: 't' + i}));
      const kept = R.activityRemember([], many);
      const again = R.activityRemember(kept, [{id: 'r29', n: 29, title: 'once'}]);
      return {count: kept.length, first: kept[0].id, last: kept[kept.length - 1].id, keep: R.ACTIVITY_KEEP,
              again: again.filter((h) => h.id === 'r29').length};""")
    assert out == {"count": 20, "first": "r29", "last": "r10", "keep": 20, "again": 1}


def test_what_is_running_is_said_in_a_word_or_two(blank):
    out = rigjs(blank, """
      return [R.activityBusy({pending: {op: 'switch', name: 'clean'}}),
              R.activityBusy({agentBusy: 'up'}),
              R.activityBusy({saving: true}),
              R.activityBusy({undoing: true, saving: true}),
              R.activityBusy({amp: 'amp/models/Edge.nam'}),
              R.activityBusy({removing: 'vocal'}),
              R.activityBusy({loading: 'board'}),
              R.activityBusy({})];""")
    assert out == ["Switching to clean…", "up is running", "Saving…", "Undoing…", "Loading Edge…",
                   "Removing vocal…", "Loading onto board…", None]


# --------------------------------------------------------------------------- #
# The zones, in Chromium
# --------------------------------------------------------------------------- #

def unlock(p):
    lock = p.locator("button.lock")
    lock.click()
    lock.click()
    expect(lock).to_have_text("Lock off · editing")


def test_the_racks_top_is_status_then_controls_then_the_activity_with_undo(browser, served):
    page = Page(browser, served.url + "/#rack", width=1440, height=900)
    p = page.page
    p.wait_for_selector(".unit")
    status = p.locator("header.bar").get_by_role("group", name="Status")
    controls = p.locator("header.bar").get_by_role("group", name="Controls")
    activity = p.get_by_role("region", name="Activity")
    expect(status.locator(".rig-name")).to_have_text("electric_board")
    expect(status.locator(".status")).to_have_text("Up 12 min · sound not measured")
    expect(status.locator(".tile .k")).to_have_text(["LOAD", "XRUNS", "AMP"])
    expect(controls.get_by_role("group", name="Master volume")).to_be_visible()
    expect(controls.locator("button.lock")).to_be_visible()
    expect(controls.get_by_role("link", name="Stage")).to_be_visible()
    expect(controls.get_by_role("link", name="Help: the user guide")).to_be_visible()
    # Undo acts on the activity, so it is there; nothing else is, idle.
    expect(activity.locator("button.undo")).to_be_visible()
    expect(activity).to_have_class(re.compile(r"\bidle\b"))
    # On a laptop the status and the controls are one row, the activity under it.
    s, c, a = status.bounding_box(), controls.bounding_box(), activity.bounding_box()
    assert abs((s["y"] + s["height"] / 2) - (c["y"] + c["height"] / 2)) < 12, (s, c)
    assert a["y"] >= max(s["y"] + s["height"], c["y"] + c["height"]) - 1
    page.close()


def test_the_activity_stays_in_view_once_the_header_has_scrolled_away(browser, served):
    page = Page(browser, served.url + "/#rack", width=1280, height=600)
    p = page.page
    p.wait_for_selector(".unit")
    p.get_by_role("button", name="Move delay up (locked)").click()
    refused = p.locator(".activity .problem.refused")
    expect(refused).to_contain_text("Not moved: delay")
    p.evaluate("() => scrollTo(0, document.documentElement.scrollHeight)")
    p.wait_for_function("() => scrollY > 200")
    box = p.locator(".activity").bounding_box()
    assert abs(box["y"]) <= 1, box
    assert p.locator("header.bar").bounding_box()["y"] < -50, "the header has gone"
    expect(refused).to_be_in_viewport()
    page.close()


def test_a_question_is_answered_from_the_bar_for_the_strip_it_was_asked_for(browser):
    with Served(save=True) as served:
        page = Page(browser, served.url + "/#rack", width=1280, height=700)
        p = page.page
        p.wait_for_selector(".unit")
        unlock(p)
        p.get_by_role("button", name="Change vocal's input").click()
        p.get_by_label("vocal input").select_option("keys-in")
        p.get_by_role("button", name="Apply").click()
        bar = p.locator(".activity")
        result = bar.locator(".rigfile-result")
        expect(result.locator("strong")).to_have_text("Changed vocal's input")
        expect(result.locator(".act-chan")).to_have_text("vocal")
        result.get_by_role("button", name="Restart").click()
        ask = bar.locator(".act-ask")
        expect(ask.locator(".act-chan")).to_have_text("vocal")
        expect(ask.locator(".confirm .q")).to_have_text("Restart?")
        # The strip it is about is marked in place.
        expect(p.locator(".strip.asking")).to_have_attribute("data-channel", "vocal")
        # Far down the page, the question is still in view, and answered there;
        # its chip shows the strip it is about.
        p.get_by_role("tab", name="board").click()
        p.evaluate("() => scrollTo(0, document.documentElement.scrollHeight)")
        expect(ask).to_be_in_viewport()
        ask.locator(".act-chan").click()
        expect(p.locator(".strip[data-channel='vocal']")).to_be_in_viewport()
        expect(p.get_by_role("tab", name="vocal")).to_have_attribute("aria-selected", "true")
        p.evaluate("() => scrollTo(0, document.documentElement.scrollHeight)")
        calls = len(served.lifecycle.calls)
        ask.get_by_role("button", name="Yes").click()
        until(lambda: ["restart"] in served.lifecycle.calls[calls:])
        expect(bar.locator(".notice strong")).to_have_text("Restarted electric_board")
        expect(p.locator(".strip.asking")).to_have_count(0)
        page.close()


def test_at_phone_width_the_top_is_two_rows_and_the_status_a_tap_away(browser, served):
    page = Page(browser, served.url + "/#rack", width=390, height=844)
    p = page.page
    p.wait_for_selector(".unit")
    header = p.locator("header.bar")
    activity = p.locator(".activity")
    # The name and its dot; the words are the dot's title.
    expect(header.locator(".rig-name")).to_be_visible()
    expect(header.locator(".status .status-text")).to_be_hidden()
    expect(header.locator(".status-slot")).to_have_attribute("title", "Up 12 min · sound not measured")
    expect(header.locator(".tile").first).to_be_hidden()
    expect(header.get_by_role("group", name="Master volume")).to_be_visible()
    expect(header.locator("button.lock")).to_be_visible()
    # Stage and Help are at the foot, as on the stage.
    expect(p.locator(".rack-nav").get_by_role("link", name="Stage")).to_be_visible()
    h, a = header.bounding_box(), activity.bounding_box()
    assert h["height"] <= 64 and a["height"] <= 64, (h, a)
    header.locator(".status-slot").click()
    expect(header.locator(".status .status-text")).to_be_visible()
    expect(header.locator(".tile").first).to_be_visible()
    width = p.evaluate("() => [document.documentElement.scrollWidth, innerWidth]")
    assert width[0] <= width[1]
    page.close()


def test_the_stage_has_the_same_activity_zone_with_undo_and_stop_asked_there(browser, served):
    page = Page(browser, served.url + "/#stage", width=390, height=520)
    p = page.page
    p.wait_for_selector(".card")
    activity = p.get_by_role("region", name="Activity")
    expect(activity.locator("button.undo")).to_be_visible()
    expect(p.locator(".stage-head button.undo")).to_have_count(0)
    unlock(p)
    p.get_by_role("button", name="Stop electric_board").click()
    ask = activity.get_by_role("group", name="Stop?")
    expect(ask.get_by_role("button")).to_have_text(["Yes", "No"])
    p.evaluate("() => scrollTo(0, document.documentElement.scrollHeight)")
    assert p.locator("header.stage-head").bounding_box()["y"] < -50, "the header has gone"
    assert abs(activity.bounding_box()["y"]) <= 1
    ask.get_by_role("button", name="No").click()
    expect(activity.get_by_role("group", name="Stop?")).to_have_count(0)
    assert served.lifecycle.calls == []
    page.close()


def test_results_beyond_the_bars_room_are_in_more_and_answerable_there(browser):
    with Served(save=True) as served:
        page = Page(browser, served.url + "/#rack", width=1280, height=900)
        p = page.page
        p.wait_for_selector(".unit")
        bar = p.locator(".activity")
        # Four refusals, locked: the bar shows the last three, "+1" the first.
        for unit, way in (("delay", "up"), ("overdrive", "down"), ("delay", "up"), ("overdrive", "down")):
            p.get_by_role("button", name=f"Move {unit} {way} (locked)").click()
        expect(bar.locator(".act-results .act-item")).to_have_count(3)
        more = bar.get_by_role("button", name="1 more")
        expect(more).to_have_attribute("aria-expanded", "false")
        listed = bar.locator(".act-list")
        expect(listed).to_be_hidden()
        more.click()
        expect(listed).to_be_visible()
        expect(listed.locator(".problem.refused strong")).to_have_text("Not moved: delay")
        listed.get_by_role("button", name="Dismiss").click()
        expect(bar.locator(".problem.refused")).to_have_count(3)
        # Dismissed, it is in the past: its words, with no buttons (PR 2 of #245).
        expect(bar.get_by_role("button", name="1 more")).to_be_visible()
        past = listed.locator(".act-item.past")
        expect(past.locator("strong")).to_have_text("Not moved: delay")
        expect(past.get_by_role("button")).to_have_count(0)
        assert served.rig.writes() == []
        page.close()


# --------------------------------------------------------------------------- #
# The past, and changes made elsewhere (#242's pushes)
# --------------------------------------------------------------------------- #

def test_a_change_the_page_did_not_make_is_found_in_the_log_by_its_signature(blank):
    out = rigjs(blank, """
      const e = (op, at, args) => ({op, at, args, previous: null});
      const log = (first, entries) => ({ok: true, first, log: entries});
      const mine = new Set([R.logSignature(e('add', '10:00:01', {name: 'slap', track: 'board'}))]);
      const start = R.elsewhereSeen(null, log(0, [e('rig', '09:00:00', {rig: 'a'})]), mine, 'agent-1');
      const next = R.elsewhereSeen(start.seen, log(0, [e('rig', '09:00:00', {rig: 'a'}),
        e('add', '10:00:01', {name: 'slap', track: 'board'}), e('parameter', '10:00:02', {processor: 'x'}),
        e('move', '10:00:03', {processor: 'delay', direction: 'up'}), e('rig', '10:00:04', {rig: 'clean'})]), mine, 'agent-1');
      const again = R.elsewhereSeen(next.seen, log(2, [e('parameter', '10:00:02', {processor: 'x'}),
        e('move', '10:00:03', {processor: 'delay', direction: 'up'}), e('rig', '10:00:04', {rig: 'clean'})]), mine, 'agent-1');
      const restarted = R.elsewhereSeen(next.seen, log(0, [e('save', '11:00:00', {rig: 'clean'})]), mine, 'agent-2');
      return {
        start: start.found, startSeen: start.seen,
        found: next.found.map((f) => [f.index, f.entry.op]), said: next.found.map((f) => R.elsewhereSaid(f.entry)),
        again: again.found, restarted: restarted.found, restartedSeen: restarted.seen,
        inline: R.answerEntry({ok: true, op: 'move', at: 't', args: {a: 1}, previous: [], chain: []}),
        nested: R.answerEntry({ok: true, how: 'live', entry: {op: 'rig', at: 't', args: {rig: 'b'}}}),
        none: R.answerEntry({ok: true, said: 'Opened'}),
        unread: R.elsewhereSeen(next.seen, null, mine, 'agent-1').seen,
      };""")
    assert out["start"] == [] and out["startSeen"] == {"agent": "agent-1", "next": 1}, "what is there at first is not news"
    # This page's add is known by its signature; a knob is never said.
    assert out["found"] == [[3, "move"], [4, "rig"]]
    assert out["said"] == [{"title": "Moved delay up", "channel": None}, {"title": "Switched to clean", "channel": None}]
    assert out["again"] == [], "each entry is judged once, the log's numbering kept"
    assert out["restarted"] == [] and out["restartedSeen"] == {"agent": "agent-2", "next": 1}, \
        "an agent started again: its log is where the page starts from"
    assert out["inline"] == {"op": "move", "at": "t", "args": {"a": 1}}
    assert out["nested"] == {"op": "rig", "at": "t", "args": {"rig": "b"}}
    assert out["none"] is None
    assert out["unread"] == {"agent": "agent-1", "next": 5}, "an unread log changes nothing"


def test_a_change_from_another_page_is_said_as_made_elsewhere_and_ones_own_are_not(browser, served):
    mine = Page(browser, served.url + "/#rack", width=1280, height=900)
    other = Page(browser, served.url + "/#rack", width=1280, height=900)
    for page in (mine, other):
        page.page.wait_for_selector(".rigs .rig")
    p, q = mine.page, other.page
    p.locator(".rig", has_text="electric-delay").click()
    p.get_by_role("button", name="Switch to electric-delay").click()
    expect(p.locator(".activity .notice strong")).to_have_text("Switched to electric-delay")
    said = q.locator(".activity .elsewhere")
    expect(said.locator("strong")).to_have_text("Switched to electric-delay")
    expect(said.locator(".from")).to_have_text("elsewhere")
    expect(said).to_have_attribute("title", re.compile("^Not from this page"))
    # The page that made it says it as its own, never as elsewhere.
    q.wait_for_timeout(1500)
    expect(p.locator(".activity .from")).to_have_count(0)
    # A knob turned elsewhere is no news: only what changes the rig's shape.
    from test_ui import post

    post(served, "/live/parameter", {"processor": "delay", "parameter": "Feedback", "value": 0.5})
    post(served, "/live/move", {"processor": "delay", "direction": "up"})
    expect(p.locator(".activity .elsewhere strong")).to_have_text("Moved delay up")
    expect(q.locator(".activity .elsewhere strong")).to_have_text(["Moved delay up", "Switched to electric-delay"])
    mine.close()
    other.close()


def test_a_result_gone_is_kept_in_the_past_list_with_its_time(browser, served):
    page = Page(browser, served.url + "/#rack", width=1280, height=900)
    p = page.page
    p.wait_for_selector(".unit")
    p.get_by_role("button", name="Move delay up (locked)").click()
    bar = p.locator(".activity")
    item = bar.locator(".problem.refused")
    expect(item.locator(".t")).to_have_text(re.compile(r"\d\d:\d\d"))
    item.get_by_role("button", name="Dismiss").click()
    expect(bar.locator(".act-results")).to_have_count(0)
    more = bar.get_by_role("button", name="1 more")
    expect(more).to_have_attribute("title", "Not moved: delay")
    more.click()
    past = bar.locator(".act-list .act-item.past")
    expect(past.locator("strong")).to_have_text("Not moved: delay")
    expect(past).to_have_attribute("title", re.compile("^Locked: reordering"))
    p.keyboard.press("Escape")
    expect(bar.locator(".act-list")).to_be_hidden()
    page.close()
