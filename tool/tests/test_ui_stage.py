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
        step: R.VOLUME_STEP_DB, hold: R.VOLUME_HOLD_MS, repeat: R.VOLUME_REPEAT_MS,
        channel: [n(-6, 1), n(-6, -1), n(-6.3, 1), n(-6.4, 1), n(5.8, 1), n(6, 1), n(10, 1), n(10, -1)],
        ends: [n(-60, -1), n(-Infinity, 1), n(-Infinity, -1), n(-80, 1), n(-80, -1), n(null, 1)],
        master: [n(-0.5, 1, M, true), n(-0.2, 1, M, true), n(0, 1, M, true), n(0, -1, M, true),
                 n(6, 1, M, true), n(6, -1, M, true), n(0.3, -1, M, true)],
        // The wheel's notch, for comparison: held to whole dB near one.
        wheel: R.volumeStepped(-6.4, 1).db,
      };""")
    assert out["step"] == 0.5 and out["hold"] == 400 and out["repeat"] == 100
    assert out["channel"] == [[-5.5, False], [-6.5, False], [-5.8, False], [-5.9, False],
                              [6, False], [6, True], [10, True], [9.5, False]]
    assert out["wheel"] == -6, "a wheel notch snaps to whole dB; a button never does"
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
        R.rigSelect(R.rigPicker(health, rigs, chosen, pending), health, locked);
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
    assert r["options"] == [{"value": "clean", "label": "clean"}, {"value": "board", "label": "board · up now"},
                            {"value": "hall", "label": "hall"}]
    assert [a["op"] for a in r["actions"]] == ["stop"], "choosing is the switch; Stop stays"
    assert out["chosenElsewhere"]["value"] == "board", "the running rig is shown, whatever the rack chose"
    assert out["locked"]["disabled"] is True and out["locked"]["why"].startswith("Locked: switching rigs")
    assert out["busy"]["disabled"] is True and out["busy"]["why"] == "switch is running: wait for it to finish."
    assert out["mine"]["disabled"] is True and out["mine"]["why"].startswith("Switching to hall…")
    assert out["none"]["value"] == "" and out["none"]["prompt"] == "Choose a rig…"
    assert [(a["op"], a["disabled"]) for a in out["none"]["actions"]] == [("start", True)]
    assert out["picked"]["value"] == "clean" and out["picked"]["prompt"] is None
    assert [(a["label"], a["disabled"]) for a in out["picked"]["actions"]] == [("Start clean", False)]
    assert out["outside"]["value"] == "" and out["outside"]["prompt"] == "v1-board (not in config/rigs/)"
    assert out["gone"]["disabled"] is True and "not answering" in out["gone"]["why"]
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
        # The keys: a disabled fader cannot even be focused.
        slider.focus()
        for key in ("ArrowUp", "PageDown", "Home", "End"):
            p.keyboard.press(key)
        # A value typed: the field is read-only.
        field.click()
        p.keyboard.type("-20")
        p.keyboard.press("Enter")
        # The step buttons, forced past their being off.
        group.get_by_role("button", name=f"{name} up 0.5 dB").click(force=True)
        group.get_by_role("button", name=f"{name} down 0.5 dB").click(force=True)
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
    until(lambda: abs(gain(served, "vocal_send") - level_to_gain(-6.5)) < 1e-9)
    page.close()


@pytest.mark.parametrize("width", WIDTHS)
def test_a_step_button_moves_exactly_half_a_db_and_the_master_stops_at_0(browser, served, width):
    page = stage_at(browser, served, width)
    p = page.page
    unlock(p)
    vocal = volume(p, "vocal volume")
    field = vocal.get_by_role("textbox", name="vocal volume value")
    vocal.get_by_role("button", name="vocal volume up 0.5 dB").click()
    until(lambda: abs(gain(served, "vocal_send") - level_to_gain(-5.5)) < 1e-9)
    expect(field).to_have_value("−5.5 dB")
    down = vocal.get_by_role("button", name="vocal volume down 0.5 dB")
    down.click()
    down.click()
    down.click()
    until(lambda: abs(gain(served, "vocal_send") - level_to_gain(-7.0)) < 1e-9)
    expect(field).to_have_value("−7.0 dB")
    # Off the half-dB grid, a step is still exactly half a dB, never snapped.
    field.click()
    p.keyboard.type("-6.3")
    p.keyboard.press("Enter")
    until(lambda: abs(gain(served, "vocal_send") - level_to_gain(-6.3)) < 1e-9)
    vocal.get_by_role("button", name="vocal volume up 0.5 dB").click()
    until(lambda: abs(gain(served, "vocal_send") - level_to_gain(-5.8)) < 1e-9)
    expect(field).to_have_value("−5.8 dB")
    # The master: + at 0 dB sends nothing; - is half a dB.
    master = volume(p, "Master volume")
    expect(master.get_by_role("textbox", name="Master volume value")).to_have_value("0.0 dB")
    sent = len(gain_sets(served))
    master.get_by_role("button", name="Master volume up 0.5 dB").click()
    time.sleep(0.6)
    assert len(gain_sets(served)) == sent, "nothing over the master's 0 dB"
    master.get_by_role("button", name="Master volume down 0.5 dB").click()
    until(lambda: abs(gain(served, "master") - level_to_gain(-0.5)) < 1e-9)
    master.get_by_role("button", name="Master volume up 0.5 dB").click()
    until(lambda: abs(gain(served, "master") - level_to_gain(0.0)) < 1e-9)
    # A channel keeps its +6 dB top.
    board = volume(p, "board volume")
    board_field = board.get_by_role("textbox", name="board volume value")
    board_field.click()
    p.keyboard.type("5.8")
    p.keyboard.press("Enter")
    until(lambda: abs(gain(served, "board_send") - level_to_gain(5.8)) < 1e-9)
    board.get_by_role("button", name="board volume up 0.5 dB").click()
    until(lambda: abs(gain(served, "board_send") - level_to_gain(6.0)) < 1e-9)
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
        until(lambda: abs(gain(served, "master") - level_to_gain(0.0)) < 1e-9)
        expect(field).to_have_value("0.0 dB")
        page.close()


def test_a_held_step_button_repeats_at_a_steady_rate_and_stops_when_let_go(browser, served):
    page = stage_at(browser, served, 390)
    p = page.page
    unlock(p)
    vocal = volume(p, "vocal volume")
    button = vocal.get_by_role("button", name="vocal volume down 0.5 dB")
    box = centred(button)
    p.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    p.mouse.down()
    time.sleep(0.25)
    assert gain_sets(served) == [], "a press is not a step until it is a tap or a hold"
    time.sleep(1.25)
    p.mouse.up()
    time.sleep(0.3)  # the last send in flight lands
    db = (gain(served, "vocal_send") * 144) - 120
    steps = (-6.0 - db) / 0.5
    assert abs(steps - round(steps)) < 1e-6, f"whole steps of 0.5 dB: {db}"
    # Held 1.5 s: a first step at 0.4 s, then one each 0.1 s, about 12; the
    # page coalesces sends, so the fake's count of calls is not the count.
    assert 6 <= round(steps) <= 13, steps
    held = gain(served, "vocal_send")
    time.sleep(0.6)
    assert gain(served, "vocal_send") == held, "let go, it stops"
    expect(vocal.get_by_role("textbox", name="vocal volume value")).to_have_value(f"−{-db:.1f} dB")
    # A press that becomes a scroll (pointercancel) moves nothing.
    button.dispatch_event("pointerdown", {"pointerId": 7, "button": 0, "isPrimary": True})
    button.dispatch_event("pointercancel", {"pointerId": 7, "isPrimary": True})
    time.sleep(0.7)
    assert gain(served, "vocal_send") == held
    page.close()


@pytest.mark.parametrize("width", WIDTHS)
def test_the_dropdown_is_off_while_locked_and_switches_rigs_once_unlocked(browser, served, width):
    page = stage_at(browser, served, width)
    p = page.page
    rig = p.locator("header.stage-head").get_by_role("combobox", name="Rig")
    expect(rig).to_be_visible()
    expect(rig).to_be_disabled()
    expect(rig).to_have_value("electric_board")
    expect(rig.locator("option:checked")).to_have_text("electric_board · up now")
    assert rig.get_attribute("title") == "Locked: switching rigs is off while locked. Unlock to switch."
    expect(p.locator(".stage-rigs .rig-lock svg")).to_be_visible()
    expect(p.get_by_role("button", name=re.compile("^(Stop|Start|Switch to) "))).to_have_count(0)
    # The list it replaces is gone.
    expect(p.locator(".stage-rigs .rig")).to_have_count(0)
    unlock(p)
    expect(rig).to_be_enabled()
    expect(p.locator(".stage-rigs .rig-lock")).to_have_count(0)
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
        p.wait_for_selector(".stage-rigs select")
        unlock(p)
        rig = p.get_by_role("combobox", name="Rig")
        expect(rig.locator("option:checked")).to_have_text("Choose a rig…")
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
        '.stage-screen button, .stage-screen a, .stage-screen select, .stage-screen input[type=range]')]
      .filter((e) => e.getClientRects().length)
      .map((e) => [e.getAttribute('aria-label') || e.textContent.trim(), e.getBoundingClientRect().width, e.getBoundingClientRect().height])""")
    small = [s for s in sizes if s[1] < 44 or s[2] < 44]
    assert small == [], small
    steps = [s for s in sizes if s[0].endswith(" 0.5 dB")]
    assert len(steps) == 6, steps  # the master's two and each channel's
    # Each fader keeps some travel between its buttons.
    travel = p.evaluate("""() => [...document.querySelectorAll('.stage-screen .vol-row input[type=range]')]
      .map((e) => e.getBoundingClientRect().width)""")
    assert len(travel) == 3 and min(travel) >= 120, travel
    page.close()
