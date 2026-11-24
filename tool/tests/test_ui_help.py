"""The Help links on the page (#167), and the guide they open.

Two layers, as test_ui.py has them: rig.js's `helpLink` imported into a bare
page, each of its links then fetched from the agent and its heading looked
for; then the screens driven in Chromium against a real agent over
`ui_fake.py`'s fake rig, serving the checkout's own docs/guide/: the rack's
Help to the guide and back to the rack, the stage's at a phone's width to
its guide and back to the stage, and a feature's link to its heading.

What stands in for what: the rig is `ui_fake.UiRig`; the phone is
Chromium at 390 by 844. Nothing here is a real phone or the rig.
"""

from __future__ import annotations

import re

import pytest

pytest.importorskip("aiohttp")

from test_ui import Watched, blank, browser, expect, rigjs, served  # noqa: E402,F401
from ui_fake import Served  # noqa: E402

PHONE = {"width": 390, "height": 844}


def ids_of(page, url):
    """The ids on the page at `url`, fetched by the browser itself."""
    return page.evaluate("""async (url) => {
      const r = await fetch(url); const text = await r.text();
      const doc = new DOMParser().parseFromString(text, 'text/html');
      return {status: r.status, ids: [...doc.querySelectorAll('[id]')].map((e) => e.id)};
    }""", url)


# --------------------------------------------------------------------------- #
# The decisions, pure (rig.js), and each link against the served guide
# --------------------------------------------------------------------------- #

def test_each_help_link_names_a_page_and_heading_the_served_guide_has(blank):
    out = rigjs(blank, """
      const keys = Object.keys(R.HELP);
      return {keys, links: Object.fromEntries(keys.map((k) => [k, R.helpLink(k, 'stage')])),
        rack: R.helpLink('record', 'rack'), newrig: R.helpLink('newRig', 'newrig'), none: R.helpLink('record', 'other'),
        unknown: R.helpLink('nothing', 'stage')};""")
    assert out["links"]["guide"] == {"href": "/help/?from=stage", "label": "Help", "aria": "Help: the user guide"}
    assert out["links"]["stage"] == {"href": "/help/playing-live?from=stage", "label": "Help", "aria": "Help: playing live"}
    assert out["links"]["record"]["href"] == "/help/recording?from=stage"
    assert out["links"]["loadChannel"]["href"] == "/help/channel-from-another-rig?from=stage"
    assert out["links"]["takes"] == {"href": "/help/recording?from=stage#find-your-takes", "label": "About takes",
                                     "aria": "About takes, in the user guide"}
    assert out["rack"]["href"] == "/help/recording?from=rack"
    assert out["newrig"]["href"] == "/help/building-a-rig?from=new-rig#make-a-new-rig"
    assert out["none"]["href"] == "/help/recording", "a screen the guide cannot go back to is not carried"
    assert out["unknown"] is None
    for key, link in out["links"].items():
        path, _, anchor = link["href"].partition("#")
        said = ids_of(blank, path)
        assert said["status"] == 200, (key, link)
        if anchor:
            assert anchor in said["ids"], f"{key}: {path} has no heading #{anchor}"


# --------------------------------------------------------------------------- #
# The screens
# --------------------------------------------------------------------------- #

def test_the_racks_help_opens_the_guide_and_its_way_back_returns_to_the_rack(browser, served):
    page = Watched(browser, served, "/#rack")
    p = page.page
    p.wait_for_selector(".unit")
    help_link = p.locator("header.bar").get_by_role("link", name="Help: the user guide")
    expect(help_link).to_have_text("Help")
    expect(help_link).to_have_attribute("href", "/help/?from=rack")
    help_link.click()
    expect(p.locator("main.guide h1")).to_have_text("User guides")
    assert p.url.endswith("/help/?from=rack")
    # A guide page and back: the way back still leads to the rack.
    p.locator("main.guide").get_by_role("link", name="Recording").first.click()
    expect(p.locator("main.guide h1")).to_have_text("Recording")
    expect(p.locator("main.guide img").first).to_have_js_property("complete", True)
    assert p.locator("main.guide img").first.evaluate("(img) => img.naturalWidth") > 0, "the picture loaded"
    p.locator(".guide-bar").get_by_role("link", name="← Back to the rig").click()
    p.wait_for_selector(".rack-screen .unit")
    assert p.url.endswith("/#rack")
    assert page.errors == [], page.errors
    page.close()


def test_the_racks_features_link_to_their_guides(browser, served):
    page = Watched(browser, served, "/#rack")
    p = page.page
    p.wait_for_selector(".unit")
    links = p.locator("[data-help]")
    expect(p.locator('[data-help="save"]')).to_have_attribute("href", "/help/saving?from=rack")
    expect(p.locator('[data-help="takes"]')).to_have_attribute("href", "/help/recording?from=rack#find-your-takes")
    expect(p.locator('[data-help="rigs"]')).to_have_attribute("href", "/help/building-a-rig?from=rack")
    seen = links.evaluate_all("(els) => els.map((e) => e.dataset.help)")
    assert set(seen) >= {"guide", "save", "takes", "rigs"}
    # A feature's link opens its page at its heading.
    p.locator('[data-help="takes"]').click()
    expect(p.locator("main.guide h1")).to_have_text("Recording")
    assert p.url.endswith("/help/recording?from=rack#find-your-takes")
    expect(p.locator("#find-your-takes")).to_be_in_viewport()
    page.close()


def test_the_stages_help_at_a_phones_width_is_small_opens_its_guide_and_comes_back_to_the_stage(browser, served):
    # A phone, and no #stage: at phone width the stage screen is the default.
    page = Watched(browser, served, "/", **PHONE)
    p = page.page
    p.wait_for_selector(".card")
    nav = p.locator("nav.stage-nav")
    help_link = nav.get_by_role("link", name="Help: playing live")
    expect(help_link).to_have_attribute("href", "/help/playing-live?from=stage")
    # Unobtrusive: narrower than either screen's tab beside it.
    widths = nav.locator("a").evaluate_all("(els) => els.map((e) => e.getBoundingClientRect().width)")
    assert len(widths) == 3 and widths[2] < widths[0] and widths[2] < widths[1], widths
    # The record bar's link sits in its head, where the clock goes while recording.
    record = p.locator('section.record-bar [data-help="record"]')
    expect(record).to_have_attribute("href", "/help/recording?from=stage")
    help_link.click()
    expect(p.locator("main.guide h1")).to_have_text("Playing live")
    # Readable on a phone: the page itself does not scroll sideways, the
    # picture fits, and the way back stays at the top while reading.
    assert p.evaluate("() => document.documentElement.scrollWidth") <= PHONE["width"]
    box = p.locator("main.guide img").first.bounding_box()
    assert box and box["x"] >= 0 and box["x"] + box["width"] <= PHONE["width"]
    assert p.evaluate("() => parseFloat(getComputedStyle(document.body).fontSize)") >= 16
    p.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
    back = p.locator(".guide-bar .to-rig")
    expect(back).to_be_in_viewport()
    assert back.bounding_box()["height"] >= 44, "a thumb's height to tap"
    back.click()
    p.wait_for_selector(".stage-screen .card")
    assert p.url.endswith("/#stage")
    assert page.errors == [], page.errors
    page.close()


# The stage's layout, at a phone's widths (review of PR #168): a Help link
# must not move what a thumb is reaching for.

def record_layout(p, hide_help=False):
    """The record head's height and the top of Stop, with the head's help
    link taken out of the layout for the measure if `hide_help`, and put back."""
    return p.evaluate("""(hide) => {
      const head = document.querySelector('.record-head');
      const link = head.querySelector('.help-link');
      if (hide && link) link.style.display = 'none';
      const said = {head: head.getBoundingClientRect().height,
                    stop: document.querySelector('.record-act.stop').getBoundingClientRect().y};
      if (link) link.style.display = '';
      return said;
    }""", hide_help)


@pytest.mark.parametrize("width", [320, 390])
def test_the_record_heads_help_link_makes_it_no_taller_and_record_moves_nothing_up(browser, width):
    with Served(record=True) as served:
        page = Watched(browser, served, "/#stage", width=width, height=844)
        p = page.page
        bar = p.locator(".record-bar")
        expect(bar.locator("h2")).to_have_text("Record")
        link = bar.locator('.record-head [data-help="record"]')
        expect(link).to_be_visible()
        assert link.bounding_box()["height"] >= 44, "a thumb's height to tap"
        idle, bare = record_layout(p), record_layout(p, hide_help=True)
        assert idle == bare, f"the link moves the record bar at {width}px: {idle} with it, {bare} without"
        p.get_by_role("button", name="Record", exact=True).click()
        expect(bar).to_have_class(re.compile(r"\brecording\b"))
        expect(bar.locator(".record-elapsed")).to_be_visible()
        expect(link).to_have_count(0)
        recording = record_layout(p)
        # The clock in the link's place: the head is no shorter recording than
        # idle, so the link never makes Record pull what is under it upwards.
        # (The bar's body changes as well, the name field giving way to the
        # take's name, and moves Stop more than the head does, as it did
        # before the link: that is measured above against the bar without it.)
        assert recording["head"] >= idle["head"], (idle, recording)
        p.get_by_role("button", name="Stop", exact=True).click()
        expect(bar.locator("h2")).to_have_text("Record")
        assert record_layout(p)["head"] == idle["head"]
        assert page.errors == [], page.errors
        page.close()


@pytest.mark.parametrize("width", [320, 390])
def test_no_lock_note_stands_above_the_cards_locked_or_not(browser, served, width):
    """The stage's lock note once pushed every card down (82 to 114 px at
    390 with a link in it). #209 took the note out: what the lock holds is
    the Help's to say, and the Lock button's own words. So there is none,
    locked or not, and the button says what it is."""
    page = Watched(browser, served, "/#stage", width=width, height=844)
    p = page.page
    p.wait_for_selector(".card")
    lock = p.locator("button.lock")
    expect(p.locator(".lock-note")).to_have_count(0)
    expect(lock).to_have_attribute("aria-label", "Performance lock is on. Tap twice to unlock structural edits")
    lock.click()
    lock.click()
    expect(lock).to_have_text("Lock off · editing")
    expect(p.locator(".lock-note")).to_have_count(0)
    expect(lock).to_have_attribute("aria-label", "Performance lock is off. Tap to lock")
    page.close()


def test_every_guide_page_fits_a_phones_width(browser, served):
    page = Watched(browser, served, "/help/?from=stage", **PHONE)
    p = page.page
    hrefs = p.locator("main.guide table a").evaluate_all("(els) => els.map((e) => e.getAttribute('href'))")
    assert len(hrefs) >= 10, hrefs
    for href in ["/help/?from=stage", *hrefs]:
        p.goto(served.url + href)
        expect(p.locator("main.guide h1")).to_be_visible()
        wide = p.evaluate("() => document.documentElement.scrollWidth")
        assert wide <= PHONE["width"], f"{href} scrolls sideways: {wide}px"
    assert page.errors == []
    page.close()


def test_the_new_rig_screen_and_a_load_panel_link_to_their_guides(browser):
    with Served(save=True) as served:
        page = Watched(browser, served, "/#rack")
        p = page.page
        p.wait_for_selector(".unit")
        p.get_by_role("button", name="Load a channel onto board").click()
        group = p.get_by_role("group", name="Load onto board")
        expect(group.locator('[data-help="loadChannel"]')).to_have_attribute(
            "href", "/help/channel-from-another-rig?from=rack")
        p.get_by_role("link", name="New rig…").click()
        link = p.locator("header.bar").get_by_role("link", name="Help: building a rig")
        expect(link).to_have_attribute("href", "/help/building-a-rig?from=new-rig#make-a-new-rig")
        link.click()
        expect(p.locator("#make-a-new-rig")).to_be_in_viewport()
        p.locator(".guide-bar .to-rig").click()
        expect(p.locator("h1.rig-name")).to_have_text("New rig")
        assert re.search(r"/#new-rig$", p.url)
        page.close()


def test_the_amp_card_links_to_setting_the_amp(browser):
    with Served(amp=True) as served:
        page = Watched(browser, served, "/#rack")
        p = page.page
        p.wait_for_selector(".amp-card .fader")
        expect(p.locator('.amp-card [data-help="amp"]')).to_have_attribute(
            "href", "/help/shaping-a-sound?from=rack#set-the-amp")
        page.close()
