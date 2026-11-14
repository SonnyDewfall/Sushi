"""The screenshots the user guides show (docs/guide/img/), taken afresh.

Not a test: run it by hand after the page changes, from `tool/`:

    PYTHONPATH=src:tests python tests/guide_shots.py

Each picture is the real page, served by a real agent over the fake rig of
`ui_fake.py` and driven with Playwright (the `ui-test` extra and its
Chromium), at twice the screen's density so text stays sharp on a phone.
Nothing is drawn on or edited afterwards. What the fake invents (the rig, its
plugins, the amp's models, the port names) is in `ui_fake.py`'s docstring;
the takes folder's path would be a temporary one, so no picture includes it.

Kept few on purpose: one a guide, and only where a picture says what the
steps cannot. A guide's text names every control it uses, so a picture that
has aged is an inconvenience and not a wrong instruction.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

from ui_fake import Served, UiLifecycle

OUT = Path(__file__).resolve().parents[2] / "docs" / "guide" / "img"
DESKTOP = {"width": 1280, "height": 640}
PHONE = {"width": 390, "height": 844}
# Long enough for the page's one-second poll to have drawn what was done.
SETTLE = 1.6


def page_of(browser, url, viewport):
    context = browser.new_context(viewport=viewport, device_scale_factor=2)
    page = context.new_page()
    page.goto(url)
    return context, page


def box_of(page, selector):
    box = page.locator(selector).first.bounding_box()
    if box is None:
        raise RuntimeError(f"nothing on the page matches {selector}")
    return box


def span(page, first, last, pad=12):
    """A clip from the top of `first` to the bottom of `last`, as wide as both."""
    a, b = box_of(page, first), box_of(page, last)
    left = min(a["x"], b["x"]) - pad
    right = max(a["x"] + a["width"], b["x"] + b["width"]) + pad
    top = a["y"] - pad
    return {"x": max(0, left), "y": max(0, top), "width": right - max(0, left),
            "height": b["y"] + b["height"] + pad - max(0, top)}


def typed(page, selector, text):
    field = page.locator(selector).first
    field.click()
    field.fill(text)
    field.press("Enter")
    time.sleep(SETTLE)


def shots(browser) -> list[str]:
    made = []

    def shoot(page, name, **how):
        page.screenshot(path=str(OUT / name), **how)
        made.append(name)

    # Getting started: no rig up, one chosen in the picker.
    with Served(lifecycle=UiLifecycle(up=None)) as served:
        context, page = page_of(browser, served.url + "/", {"width": 1280, "height": 900})
        page.wait_for_selector(".rig-list")
        page.click(".rig-list button.rig:has-text('electric-clean')")
        time.sleep(SETTLE)
        shoot(page, "start-a-rig.png")
        context.close()

    with Served(save=True, record=True) as served:
        # The front page and Mixing: the rack as it opens, a rig up.
        context, page = page_of(browser, served.url + "/", DESKTOP)
        page.wait_for_selector(".unit")
        time.sleep(SETTLE)
        shoot(page, "rack.png")

        # Saving and going back: two saves, a change since, and a point chosen.
        drive = "article.unit[data-unit=overdrive] input.value-field"
        typed(page, drive, "0.8")
        page.click("button.save-main")
        time.sleep(SETTLE)
        typed(page, drive, "0.3")
        page.click("button.save-main")
        time.sleep(SETTLE)
        typed(page, drive, "0.5")
        page.click(".save-result button.dismiss")
        page.click("button.revert-open")
        page.wait_for_selector(".revert-point")
        page.locator(".revert-point").nth(1).click()
        time.sleep(SETTLE)
        page.set_viewport_size({"width": 1280, "height": 1100})
        shoot(page, "save-and-revert.png", clip=span(page, ".changes", ".save-note"))
        context.close()

        # Building a rig: the New rig screen, two channels drafted.
        context, page = page_of(browser, served.url + "/#new-rig", {"width": 1280, "height": 900})
        page.wait_for_selector("form.new-rig-form")
        page.fill("input[name=new-rig-name]", "two-guitars")
        page.fill("input[name=ch-name-0]", "clean")
        page.click("button.add-channel")
        page.wait_for_selector("input[name=ch-name-1]")
        page.fill("input[name=ch-name-1]", "dirty")
        page.select_option("select[aria-label='Channel 2 input']", "guitar-in")
        page.select_option("select[aria-label='Channel 2 starts from']", index=1)
        time.sleep(SETTLE)
        shoot(page, "new-rig.png", clip=span(page, "header.bar", "form.new-rig-form"))
        context.close()

        # Recording: the stage's record bar, a take running.
        context, page = page_of(browser, served.url + "/#stage", {"width": 1280, "height": 900})
        page.wait_for_selector("input[name=take-name]")
        page.fill("input[name=take-name]", "riff-idea")
        page.click("button.record-act.rec")
        page.wait_for_selector(".record-bar.recording")
        served.recorder.clock.advance(47)
        time.sleep(SETTLE)
        shoot(page, "recording.png", clip=span(page, "header.stage-head", "section.record-bar"))
        page.click("button.record-act.stop")
        time.sleep(SETTLE)
        context.close()

        # Playing live: the stage screen as a phone opens it, locked.
        context, page = page_of(browser, served.url + "/", PHONE)
        page.wait_for_selector(".card")
        time.sleep(SETTLE)
        shoot(page, "stage-phone.png")
        context.close()

    # Shaping a sound: the amp's card and a plugin opened for all its controls.
    with Served(amp=True) as served:
        context, page = page_of(browser, served.url + "/", {"width": 1280, "height": 1100})
        page.wait_for_selector(".unit")
        page.click("article.unit[data-unit=overdrive] button.name")
        time.sleep(SETTLE)
        shoot(page, "shaping-a-sound.png", clip=span(page, "section.amp-card", "article.unit[data-unit=octave]"))
        context.close()
    return made


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            made = shots(browser)
        finally:
            browser.close()
    for name in made:
        print(OUT / name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
