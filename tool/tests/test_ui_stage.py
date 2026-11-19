"""The stage's controls for a phone (#182): the lock holds every volume,
each volume has −0.5 dB and +0.5 dB buttons, and the rig is chosen from a
dropdown.

Two layers, as test_ui.py has them: rig.js's decisions imported into a bare
page (`volumeNudged`, `rigSelect`, `rigSelected`, `lockRefusal`), then the
stage driven in Chromium against a real agent over the fake rig of
`ui_fake.py`, at a phone's widths (320 and 390) and a laptop's (1280, at
#stage). The phone is Chromium with a mouse: no touch screen and no real
phone is involved, so a thumb's press is a pointer's here.
"""

from __future__ import annotations

import re
import time

import pytest

pytest.importorskip("aiohttp")

from test_ui import Page, blank, browser, expect, rigjs, served, until  # noqa: E402,F401
from test_ui_knobs import rest_on  # noqa: E402
from ui_fake import Served, UiLifecycle  # noqa: E402

from sushi_rig.rigfile import level_to_gain  # noqa: E402

WIDTHS = [320, 390, 1280]
INF = float("-inf")


# --------------------------------------------------------------------------- #
# The decisions, pure (rig.js)
# --------------------------------------------------------------------------- #

def test_a_step_is_exactly_half_a_db_and_keeps_each_volumes_top(blank):
    out = rigjs(blank, """
      const n = (db, d, ...rest) => { const r = R.volumeNudged(db, d, ...rest); return [r.db, !!r.unchanged]; };
      const M = R.MASTER_VOLUME_MAX_DB;
      return {
        step: R.VOLUME_STEP_DB, hold: R.VOLUME_HOLD_MS, repeat: R.VOLUME_REPEAT_MS, max: R.VOLUME_REPEAT_MAX,
        channel: [n(-6, 1), n(-6, -1), n(-6.3, 1), n(-6.4, 1), n(5.8, 1), n(6, 1), n(10, 1), n(10, -1)],
        ends: [n(-60, -1), n(-Infinity, 1), n(-Infinity, -1), n(-80, 1), n(-80, -1), n(null, 1)],
        master: [n(-0.5, 1, M, true), n(-0.2, 1, M, true), n(0, 1, M, true), n(0, -1, M, true),
                 n(6, 1, M, true), n(6, -1, M, true), n(0.3, -1, M, true)],
        // The wheel's notch, for comparison: held to whole dB near one.
        wheel: R.volumeStepped(-6.4, 1).db,
        // What Sushi's 32-bit floats read back as (review of PR #184): 0 dB
        // is -0.0000029, +6 is 6.0000004, -6.3 is -6.2999954.
        f32: [n(-2.86102294921875e-06, 1, M, true), n(-2.86102294921875e-06, -1, M, true),
              n(6.000000476837158, 1), n(-6.299995422363281, 1)],
        tenTaps: (() => { let db = -6.3; for (let i = 0; i < 10; i++) db = R.volumeNudged(db + 3e-6, 1).db; return db; })(),
      };""")
    assert out["step"] == 0.5 and out["hold"] == 400 and out["repeat"] == 100 and out["max"] == 12
    assert out["channel"] == [[-5.5, False], [-6.5, False], [-5.8, False], [-5.9, False],
                              [6, False], [6, True], [10, True], [9.5, False]]
    assert out["wheel"] == -6, "a wheel notch snaps to whole dB; a button never does"
    # At the top as Sushi reads it back, + sends nothing; steps start from
    # the level to 0.1 dB, so they land on the grid and never drift.
    assert out["f32"] == [[0, True], [-0.5, False], [6, True], [-5.8, False]]
    assert out["tenTaps"] == -1.3, "ten taps from -6.3, each read back a little off, land on -1.3 exactly"
    assert out["ends"] == [[INF, False], [-60, False], [INF, True], [-79.5, False], [INF, False], [None, True]]
    # The master's 0 dB cap (#137): + stops at it, and over it (a rig from
    # before the cap) + does nothing and - goes straight to 0, as the fader does.
    assert out["master"] == [[0, False], [0, False], [0, True], [-0.5, False],
                             [6, True], [0, False], [0, False]]


def test_the_lock_holds_the_stages_volumes_and_its_switch_and_nothing_else_new(blank):
    out = rigjs(blank, """
      return {vol: R.lockRefusal(true, 'stage_volume'), sw: R.lockRefusal(true, 'stage_switch'),
        open: [R.lockRefusal(false, 'stage_volume'), R.lockRefusal(false, 'stage_switch')],
        knob: R.lockRefusal(true, 'parameter'), bypass: R.lockRefusal(true, 'bypass'), record: R.lockRefusal(true, 'record')};""")
    assert out["vol"].startswith("Locked: the stage's volumes do not move while locked")
    assert out["sw"] == "Locked: switching rigs is off while locked. Unlock to switch."
    assert out["open"] == [None, None]
    assert out["knob"] is None and out["bypass"] is None and out["record"] is None


def test_the_dropdown_shows_the_running_rig_and_choosing_another_switches(blank):
    out = rigjs(blank, """
      const up = (name, busy = null) => ({ok: true, body: {rig: {running: true, config_name: name}, agent: {busy}}});
      const down = {ok: true, body: {rig: {running: false}, agent: {busy: null}}};
      const rigs = {ok: true, body: {rigs: ['clean', 'board', 'hall']}};
      const sel = (health, chosen = null, locked = false, pending = null) =>
        R.rigSelect(R.rigPicker(health, rigs, chosen, pending), health, locked, R.rigStatus(health, null).title);
      const running = sel(up('board'), 'board');
      const chosenElsewhere = sel(up('board'), 'hall');
      const locked = sel(up('board'), 'board', true);
      const busy = sel(up('board', 'switch'), 'board');
      const mine = sel(up('board'), 'board', false, {op: 'switch', name: 'hall'});
      const none = sel(down);
      const picked = sel(down, 'clean');
      const outside = sel(up('v1-board'));
      const gone = sel(null);
      return {running, chosenElsewhere, locked, busy, mine, none, picked, outside, gone,
        switch: R.rigSelected(running, 'hall'), same: R.rigSelected(running, 'board'),
        offSwitch: R.rigSelected(locked, 'hall'), start: R.rigSelected(none, 'clean'),
        fromOutside: R.rigSelected(outside, 'clean'), empty: R.rigSelected(none, '')};""")
    r = out["running"]
    assert r["value"] == "board" and r["prompt"] is None and r["disabled"] is False and r["why"] is None
    assert r["title"] == "board", "the whole name, for when it is cut short"
    assert r["options"] == [{"value": "clean", "label": "clean"}, {"value": "board", "label": "board"},
                            {"value": "hall", "label": "hall"}]
    assert [a["op"] for a in r["actions"]] == ["stop"], "choosing is the switch; Stop stays"
    assert out["chosenElsewhere"]["value"] == "board", "the running rig is shown, whatever the rack chose"
    assert out["locked"]["disabled"] is True and out["locked"]["why"].startswith("Locked: switching rigs")
    assert out["locked"]["title"] == "board. Locked: switching rigs is off while locked. Unlock to switch."
    assert out["busy"]["disabled"] is True and out["busy"]["why"] == "switch is running: wait for it to finish."
    assert out["mine"]["disabled"] is True and out["mine"]["why"].startswith("Switching to hall…")
    assert out["none"]["value"] == "" and out["none"]["prompt"] == "Rig down", "it stands in the screen's title"
    assert [(a["op"], a["disabled"]) for a in out["none"]["actions"]] == [("start", True)]
    assert out["picked"]["value"] == "clean" and out["picked"]["prompt"] is None
    assert [(a["label"], a["disabled"]) for a in out["picked"]["actions"]] == [("Start clean", False)]
    assert out["outside"]["value"] == "" and out["outside"]["prompt"] == "v1-board (not in config/rigs/)"
    assert out["gone"]["disabled"] is True and "not answering" in out["gone"]["why"]
    assert out["gone"]["prompt"] == "Agent not answering"
    assert out["switch"] == {"op": "switch", "name": "hall"}
    assert out["same"] is None and out["offSwitch"] is None and out["empty"] is None
    assert out["start"] == {"op": "choose", "name": "clean"}
    assert out["fromOutside"] == {"op": "switch", "name": "clean"}


# --------------------------------------------------------------------------- #
# The stage
# --------------------------------------------------------------------------- #

def stage_at(browser, served, width):
    page = Page(browser, served.url + "/#stage", width=width, height=844)
    page.page.wait_for_selector(".card")
    return page


def unlock(p):
    lock = p.locator("button.lock")
    lock.click()
    lock.click()
    expect(lock).to_have_text("Lock off · editing")


def volume(p, name):
    """A stage volume's group: "Master volume", "board volume", "vocal volume"."""
    return p.locator(".stage-screen").get_by_role("group", name=name, exact=True)


def centred(locator):
    """`locator` scrolled to the middle of the screen, clear of the sticky
    nav at the bottom, and its box: a pointer moved there lands on it."""
    locator.evaluate("(e) => e.scrollIntoView({block: 'center'})")
    box = locator.bounding_box()
    hit = locator.evaluate(f"(e) => e.contains(document.elementFromPoint({box['x'] + box['width'] / 2}, {box['y'] + box['height'] / 2}))")
    assert hit, "nothing covers it"
    return box


def gain(served, processor):
    return served.rig.info[processor]["gain"]["value"]


def gain_sets(served):
    return [c for c in served.rig.calls if c[0] == "set_parameter" and c[2] == "gain"]


VOLUMES = [("Master volume", "master"), ("board volume", "board_send"), ("vocal volume", "vocal_send")]


@pytest.mark.parametrize("width", WIDTHS)
def test_locked_no_stage_volume_takes_a_drag_tap_wheel_key_typed_value_or_step(browser, served, width):
    page = stage_at(browser, served, width)
    p = page.page
    expect(p.locator("button.lock")).to_have_text("Locked")
    before = {proc: gain(served, proc) for _, proc in VOLUMES}
    for name, _ in VOLUMES:
        group = volume(p, name)
        slider = group.get_by_role("slider", name=name)
        field = group.get_by_role("textbox", name=f"{name} value")
        expect(slider).to_be_disabled()
        expect(field).not_to_be_editable()
        for which in ("down", "up"):
            expect(group.get_by_role("button", name=f"{name} {which} 0.5 dB")).to_be_disabled()
        # It looks locked: the lock's icon on it, and says why.
        expect(group.locator(".vol-lock svg")).to_be_visible()
        assert group.get_attribute("title").startswith("Locked: the stage's volumes do not move")
        # A tap and a drag across the fader.
        box = centred(slider)
        p.mouse.click(box["x"] + box["width"] * 0.2, box["y"] + box["height"] / 2)
        p.mouse.move(box["x"] + box["width"] * 0.9, box["y"] + box["height"] / 2)
        p.mouse.down()
        p.mouse.move(box["x"] + box["width"] * 0.1, box["y"] + box["height"] / 2, steps=8)
        p.mouse.up()
        # The wheel, resting on it.
        rest_on(p, box)
        p.mouse.wheel(0, 100)
        p.mouse.wheel(0, -300)
        # The keys: a disabled fader cannot take the focus, so no key reaches
        # it; and its own key handler refuses them as well.
        slider.focus()
        assert not slider.evaluate("(e) => document.activeElement === e")
        for key in ("ArrowUp", "PageDown", "Home", "End"):
            slider.dispatch_event("keydown", {"key": key, "bubbles": True, "cancelable": True})
        # A value typed: the field is read-only, and a value put into it and
        # committed anyway is refused in place with the lock's reason.
        field.click()
        p.keyboard.type("-20")
        expect(field).not_to_have_value("-20")
        field.evaluate("(e) => { e.value = '-20'; e.dispatchEvent(new Event('input')); }")
        p.keyboard.press("Enter")
        expect(field).to_have_class(re.compile(r"\bbad\b"))
        assert field.get_attribute("title").startswith("Locked: the stage's volumes")
        p.keyboard.press("Escape")
        # A tap on each step button, where a thumb would land: it is off.
        for which in ("down", "up"):
            b = centred(group.get_by_role("button", name=f"{name} {which} 0.5 dB"))
            p.mouse.click(b["x"] + b["width"] / 2, b["y"] + b["height"] / 2)
    time.sleep(0.8)  # anything sent would have reached the fake by now
    assert gain_sets(served) == []
    assert {proc: gain(served, proc) for _, proc in VOLUMES} == before
    expect(volume(p, "vocal volume").get_by_role("textbox", name="vocal volume value")).to_have_value("−6.0 dB")
    # Knobs and bypass still work locked, as before.
    p.get_by_role("button", name="Bypass delay").click()
    until(lambda: served.rig.bypassed["delay"] is True)
    # Unlocked, the same fader moves and the icon goes.
    unlock(p)
    vocal = volume(p, "vocal volume")
    expect(vocal.locator(".vol-lock")).to_be_hidden()
    expect(vocal.get_by_role("slider", name="vocal volume")).to_be_enabled()
    vocal.get_by_role("button", name="vocal volume down 0.5 dB").click()
    until(lambda: abs(gain(served, "vocal_send") - level_to_gain(-6.5)) < 1e-6)
    page.close()


@pytest.mark.parametrize("width", WIDTHS)
def test_a_step_button_moves_exactly_half_a_db_and_the_master_stops_at_0(browser, served, width):
    page = stage_at(browser, served, width)
    p = page.page
    unlock(p)
    vocal = volume(p, "vocal volume")
    field = vocal.get_by_role("textbox", name="vocal volume value")
    vocal.get_by_role("button", name="vocal volume up 0.5 dB").click()
    until(lambda: abs(gain(served, "vocal_send") - level_to_gain(-5.5)) < 1e-6)
    expect(field).to_have_value("−5.5 dB")
    down = vocal.get_by_role("button", name="vocal volume down 0.5 dB")
    down.click()
    down.click()
    down.click()
    until(lambda: abs(gain(served, "vocal_send") - level_to_gain(-7.0)) < 1e-6)
    expect(field).to_have_value("−7.0 dB")
    # Off the half-dB grid, a step is still exactly half a dB, never snapped.
    field.click()
    p.keyboard.type("-6.3")
    p.keyboard.press("Enter")
    until(lambda: abs(gain(served, "vocal_send") - level_to_gain(-6.3)) < 1e-6)
    vocal.get_by_role("button", name="vocal volume up 0.5 dB").click()
    until(lambda: abs(gain(served, "vocal_send") - level_to_gain(-5.8)) < 1e-6)
    expect(field).to_have_value("−5.8 dB")
    # The master: + at 0 dB sends nothing; - is half a dB.
    master = volume(p, "Master volume")
    expect(master.get_by_role("textbox", name="Master volume value")).to_have_value("0.0 dB")
    sent = len(gain_sets(served))
    master.get_by_role("button", name="Master volume up 0.5 dB").click()
    time.sleep(0.6)
    assert len(gain_sets(served)) == sent, "nothing over the master's 0 dB"
    master.get_by_role("button", name="Master volume down 0.5 dB").click()
    until(lambda: abs(gain(served, "master") - level_to_gain(-0.5)) < 1e-6)
    master.get_by_role("button", name="Master volume up 0.5 dB").click()
    until(lambda: abs(gain(served, "master") - level_to_gain(0.0)) < 1e-6)
    # A channel keeps its +6 dB top.
    board = volume(p, "board volume")
    board_field = board.get_by_role("textbox", name="board volume value")
    board_field.click()
    p.keyboard.type("5.8")
    p.keyboard.press("Enter")
    until(lambda: abs(gain(served, "board_send") - level_to_gain(5.8)) < 1e-6)
    board.get_by_role("button", name="board volume up 0.5 dB").click()
    until(lambda: abs(gain(served, "board_send") - level_to_gain(6.0)) < 1e-6)
    expect(board_field).to_have_value("6.0 dB")
    sent = len(gain_sets(served))
    board.get_by_role("button", name="board volume up 0.5 dB").click()
    time.sleep(0.6)
    assert len(gain_sets(served)) == sent, "nothing over a channel's +6 dB"
    # The value stays in view beside its buttons.
    for name, _ in VOLUMES:
        expect(volume(p, name).get_by_role("textbox", name=f"{name} value")).to_be_visible()
    page.close()


@pytest.mark.parametrize("width", WIDTHS)
def test_on_a_master_over_its_top_plus_does_nothing_and_minus_goes_to_0(browser, width):
    with Served() as served:
        served.rig.info["master"]["gain"]["value"] = level_to_gain(6.0)  # a rig from before the cap
        page = stage_at(browser, served, width)
        p = page.page
        unlock(p)
        master = volume(p, "Master volume")
        field = master.get_by_role("textbox", name="Master volume value")
        expect(field).to_have_value("6.0 dB")
        expect(master).to_contain_text("over the top")
        master.get_by_role("button", name="Master volume up 0.5 dB").click()
        time.sleep(0.6)
        assert gain_sets(served) == []
        master.get_by_role("button", name="Master volume down 0.5 dB").click()
        until(lambda: abs(gain(served, "master") - level_to_gain(0.0)) < 1e-6)
        expect(field).to_have_value("0.0 dB")
        page.close()


def settled(served, ceiling=5.0, quiet=0.5):
    """Wait until the fake has had no new gain for `quiet` seconds (within
    `ceiling`), and say how many it had: the page sends one at a time and
    the newest wins, so the last step lands when it lands."""
    deadline = time.monotonic() + ceiling
    count, since = len(gain_sets(served)), time.monotonic()
    while time.monotonic() < deadline:
        time.sleep(0.05)
        now = len(gain_sets(served))
        if now != count:
            count, since = now, time.monotonic()
        elif time.monotonic() - since >= quiet:
            return count
    raise AssertionError(f"gains still arriving after {ceiling}s")


def press(p, locator):
    box = centred(locator)
    p.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    p.mouse.down()


def vocal_db(served):
    return round(gain(served, "vocal_send") * 144 - 120, 4)


def held_page(browser, served):
    page = stage_at(browser, served, 390)
    unlock(page.page)
    return page, volume(page.page, "vocal volume").get_by_role("button", name="vocal volume down 0.5 dB")


def test_a_held_step_button_repeats_at_a_steady_rate_and_stops_when_let_go(browser, served):
    page, button = held_page(browser, served)
    p = page.page
    press(p, button)
    time.sleep(0.25)
    assert gain_sets(served) == [], "a press is not a step until it is a tap or a hold"
    time.sleep(0.75)
    p.mouse.up()
    settled(served)
    db = vocal_db(served)
    steps = (-6.0 - db) / 0.5
    assert steps == round(steps), f"whole steps of 0.5 dB: {db}"
    # Held 1 s: a first step at 0.4 s, then one each 0.1 s, about 7; the
    # page coalesces sends, so the fake's count of calls is not the count.
    assert 4 <= steps <= 8, steps
    count = len(gain_sets(served))
    time.sleep(0.6)
    assert len(gain_sets(served)) == count, "let go, it stops"
    expect(volume(p, "vocal volume").get_by_role("textbox", name="vocal volume value")).to_have_value(f"−{-db:.1f} dB")
    # A press that becomes a scroll (pointercancel) moves nothing.
    button.dispatch_event("pointerdown", {"pointerId": 7, "button": 0, "isPrimary": True})
    button.dispatch_event("pointercancel", {"pointerId": 7, "isPrimary": True})
    time.sleep(0.7)
    assert len(gain_sets(served)) == count
    page.close()


def test_a_hold_whose_release_is_never_heard_stops_at_six_db(browser, served):
    page, button = held_page(browser, served)
    p = page.page
    press(p, button)
    time.sleep(2.4)  # twice as long as twelve steps take
    settled(served)
    assert vocal_db(served) == -12.0, "twelve steps of 0.5 dB, then nothing"
    count = len(gain_sets(served))
    time.sleep(0.5)
    assert len(gain_sets(served)) == count
    p.mouse.up()
    page.close()


ENDS = {
    "the window losing the focus": "window.dispatchEvent(new Event('blur'))",
    "the page hidden": """Object.defineProperty(document, 'visibilityState', {value: 'hidden', configurable: true});
                          document.dispatchEvent(new Event('visibilitychange'))""",
}


@pytest.mark.parametrize("how", ["the window losing the focus", "the page hidden", "slid off the button",
                                 "the lock engaged", "the region drawn again"])
def test_a_hold_stops_sending_however_the_press_is_lost(browser, served, how):
    page, button = held_page(browser, served)
    p = page.page
    handle = button.element_handle()
    press(p, button)
    until(lambda: len(gain_sets(served)) >= 2, timeout=3)  # it is repeating
    if how in ENDS:
        p.evaluate(ENDS[how])
    elif how == "slid off the button":
        box = button.bounding_box()
        p.mouse.move(box["x"] + box["width"] / 2, box["y"] - 60, steps=3)
    elif how == "the lock engaged":
        # A second hand on the Lock, the first still holding the button.
        lock = p.locator("button.lock")
        lock.focus()
        p.keyboard.press("Enter")
        expect(lock).to_have_text("Locked")
    else:
        # The channel's range changes under the hand: the levels are drawn
        # again, and the button held is no longer on the page.
        served.rig.info["vocal_send"]["gain"]["max_domain_value"] = 18.0
        until(lambda: not handle.evaluate("(e) => e.isConnected"), timeout=4)
    count = settled(served)
    time.sleep(0.6)
    assert len(gain_sets(served)) == count, f"still sending after {how}"
    if how != "the region drawn again":  # that one changes the gain's range under it
        assert vocal_db(served) > -12.0, "it stopped before the hold's own limit"
    p.mouse.up()
    page.close()


def test_the_lock_engaged_mid_drag_stops_the_fader_sending(browser, served):
    page = stage_at(browser, served, 390)
    p = page.page
    unlock(p)
    slider = volume(p, "vocal volume").get_by_role("slider", name="vocal volume")
    box = centred(slider)
    y = box["y"] + box["height"] / 2
    p.mouse.move(box["x"] + box["width"] * 0.8, y)
    p.mouse.down()
    p.mouse.move(box["x"] + box["width"] * 0.6, y, steps=4)
    until(lambda: len(gain_sets(served)) >= 1)
    lock = p.locator("button.lock")
    lock.focus()
    p.keyboard.press("Enter")
    expect(lock).to_have_text("Locked")
    count = settled(served)
    p.mouse.move(box["x"] + box["width"] * 0.1, y, steps=8)
    p.mouse.up()
    time.sleep(0.6)
    assert len(gain_sets(served)) == count, "the drag sent nothing once locked"
    page.close()


@pytest.mark.parametrize("width", WIDTHS)
def test_the_dropdown_is_off_while_locked_and_switches_rigs_once_unlocked(browser, served, width):
    page = stage_at(browser, served, width)
    p = page.page
    rig = p.locator("header.stage-head").get_by_role("combobox", name="Rig")
    expect(rig).to_be_visible()
    expect(rig).to_be_disabled()
    expect(rig).to_have_value("electric_board")
    expect(rig.locator("option:checked")).to_have_text("electric_board")
    assert rig.get_attribute("title") == "electric_board. Locked: switching rigs is off while locked. Unlock to switch."
    # On one row with the Lock button, in the rig's name's place.
    row = p.locator(".stage-head .row")
    expect(row.get_by_role("combobox", name="Rig")).to_be_visible()
    expect(row.locator("button.lock")).to_be_visible()
    expect(p.get_by_role("button", name=re.compile("^(Stop|Start|Switch to) "))).to_have_count(0)
    # The list it replaces is gone.
    expect(p.locator(".stage-rigs .rig")).to_have_count(0)
    unlock(p)
    expect(rig).to_be_enabled()
    assert rig.get_attribute("title") == "electric_board"
    names = rig.locator("option").evaluate_all("(os) => os.map((o) => o.value)")
    assert "electric-delay" in names and "accustomed" in names and "electric_board" in names
    expect(p.get_by_role("button", name="Stop electric_board")).to_be_visible()
    expect(p.get_by_role("button", name=re.compile("^Switch to "))).to_have_count(0)
    rig.select_option("electric-delay")
    expect(p.locator(".notice")).to_have_text(re.compile(
        r"^Switched to electric-delay live in 0\.4 s: Sushi was not restarted\."))
    expect(p.locator(".stage-head .rig-name")).to_have_text("electric-delay")
    expect(rig).to_have_value("electric-delay")
    assert served.lifecycle.calls == [["switch", "electric-delay"]]
    # A restart where it must, said as the list said it.
    served.lifecycle.how["accustomed"] = ("restart", [{"code": "amp", "detail": "the amp's settings differ (test)"}])
    rig.select_option("accustomed")
    expect(p.locator(".notice")).to_have_text(re.compile(
        r"^Switched to accustomed by restart in 6\.2 s, because amp: the amp's settings differ \(test\)\."))
    expect(rig).to_have_value("accustomed")
    # Locked again, it is off again.
    p.locator("button.lock").click()
    expect(rig).to_be_disabled()
    page.close()


def test_while_a_switch_runs_the_dropdown_is_off_and_says_what_is_running(browser, served):
    served.lifecycle.delay = 1.5
    page = stage_at(browser, served, 390)
    p = page.page
    unlock(p)
    rig = p.get_by_role("combobox", name="Rig")
    rig.select_option("electric-delay")
    expect(p.locator(".stage-rigs .rig-busy")).to_have_text("Switching to electric-delay…")
    expect(rig).to_be_disabled()
    expect(p.get_by_role("button", name="Stop electric_board")).to_be_disabled()
    expect(p.locator(".notice")).to_contain_text("Switched to electric-delay live", timeout=8000)
    expect(rig).to_be_enabled()
    expect(p.locator(".stage-rigs .rig-busy")).to_have_count(0)
    page.close()


def test_from_the_stage_a_down_rig_is_chosen_and_started_and_stop_still_asks(browser):
    with Served(lifecycle=UiLifecycle(up=None)) as served:
        page = Page(browser, served.url + "/#stage", width=390, height=844)
        p = page.page
        p.wait_for_selector(".stage-head select")
        unlock(p)
        rig = p.get_by_role("combobox", name="Rig")
        expect(rig.locator("option:checked")).to_have_text("Rig down")
        expect(p.get_by_role("button", name="Choose a rig to start")).to_be_disabled()
        rig.select_option("electric_board")
        assert served.lifecycle.calls == [], "choosing a rig with none up starts nothing"
        p.get_by_role("button", name="Start electric_board").click()
        expect(p.locator(".notice")).to_contain_text("Started electric_board, headless.", timeout=8000)
        assert served.lifecycle.calls == [["up", "electric_board", "--rig", "--headless"]]
        expect(rig).to_have_value("electric_board")
        p.get_by_role("button", name="Stop electric_board").click()
        expect(p.locator(".stage-rigs .confirm")).to_contain_text("Stop electric_board? The sound stops")
        assert served.lifecycle.calls == [["up", "electric_board", "--rig", "--headless"]], "one tap stops nothing"
        p.get_by_role("button", name="Stop now").click()
        expect(p.locator(".stage-head .rig-name")).to_have_text("Rig down")
        assert served.lifecycle.calls[-1] == ["down"]
        page.close()


@pytest.mark.parametrize("width", WIDTHS)
@pytest.mark.parametrize("locked", [True, False])
def test_the_stage_does_not_scroll_sideways_and_every_target_takes_a_thumb(browser, served, width, locked):
    page = stage_at(browser, served, width)
    p = page.page
    if not locked:
        unlock(p)
        p.locator('.card[data-unit="overdrive"] button.open-card').click()
        p.get_by_role("button", name="Stop electric_board").click()  # the confirm, the widest the region gets
        expect(p.locator(".stage-rigs .confirm")).to_be_visible()
    width_now = p.evaluate("() => [document.documentElement.scrollWidth, innerWidth]")
    assert width_now[0] <= width_now[1], f"sideways scroll at {width}: {width_now}"
    sizes = p.evaluate("""() => [...document.querySelectorAll(
        '.stage-screen button, .stage-screen a, .stage-screen select, .stage-screen input')]
      .filter((e) => e.getClientRects().length)
      .map((e) => [e.getAttribute('aria-label') || e.textContent.trim(), e.getBoundingClientRect().width, e.getBoundingClientRect().height])""")
    small = [s for s in sizes if s[1] < 44 or s[2] < 44]
    assert small == [], small
    steps = [s for s in sizes if s[0].endswith(" 0.5 dB")]
    assert len(steps) == 6, steps  # the master's two and each channel's
    # Each fader keeps some travel between its buttons.
    fields = [s for s in sizes if s[0].endswith(" volume value")]
    assert len(fields) == 3, fields
    travel = p.evaluate("""() => [...document.querySelectorAll('.stage-screen .volume-stage input[type=range]')]
      .map((e) => e.getBoundingClientRect().width)""")
    assert len(travel) == 3 and min(travel) >= 80, travel
    page.close()


@pytest.mark.parametrize("locked", [True, False])
def test_on_a_small_phone_the_header_is_no_taller_and_the_channels_are_on_the_first_screen(browser, served, locked):
    """320 by 568, an iPhone SE: before #182 the header was 240 px and the
    first channel's volume began at 551. Two rows for the dropdown and the
    master pushed it to 659 locked (review of PR #184)."""
    page = Page(browser, served.url + "/#stage", width=320, height=568)
    p = page.page
    p.wait_for_selector(".card")
    if not locked:
        unlock(p)
    head = p.locator("header.stage-head").bounding_box()["height"]
    first = p.locator(".levels .volume").first.bounding_box()["y"]
    if locked:
        # Locked is how the stage is played: unlocked, Start and Stop add a row.
        assert head <= 240, head
        assert first < 568, f"the first channel's volume is below the first screen: {first}"
    # The master is one row, the height of its buttons.
    master = volume(p, "Master volume").bounding_box()["height"]
    assert master <= 48, master
    # The name is cut short with an ellipsis on one line, never broken.
    rig = p.get_by_role("combobox", name="Rig")
    assert rig.bounding_box()["height"] <= 48
    assert rig.evaluate("(e) => getComputedStyle(e).whiteSpace") == "nowrap"
    page.close()
