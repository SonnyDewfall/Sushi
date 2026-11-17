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
    assert out["links"]["presets"]["href"] == "/help/channel-presets?from=stage"
    assert out["links"]["lock"] == {"href": "/help/playing-live?from=stage#the-lock", "label": "About the lock",
                                    "aria": "About the lock, in the user guide"}
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
    expect(p.locator('.lock-note [data-help="lock"]')).to_have_attribute("href", "/help/playing-live?from=stage#the-lock")
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


def test_the_new_rig_screen_and_a_presets_panel_link_to_their_guides(browser):
    with Served(save=True) as served:
        page = Watched(browser, served, "/#rack")
        p = page.page
        p.wait_for_selector(".unit")
        p.get_by_role("button", name="Presets for board").click()
        group = p.get_by_role("group", name="Presets for board")
        expect(group.locator('[data-help="presets"]')).to_have_attribute("href", "/help/channel-presets?from=rack")
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
