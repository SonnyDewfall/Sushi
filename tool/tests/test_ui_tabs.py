"""The rack's tabs and its minimised rows (#135): the centre column shows one
channel's chain at a time behind a tab per channel and Master, the amp in the
tab of the channel it is in front of, and a bypassed plugin is a slim row
that opens when it is switched on or tapped.

Two layers, as test_ui.py has them: rig.js's decisions imported into a bare
page, then the rack driven in Chromium against a real agent over the fake
rig of `ui_fake.py` (which says what in it is observed and what invented).

What stands in for what. "A pedal" here is the fake Sushi's bypass changed
under the agent (`pedal`), which is all a page can see of one: the pedal's
own path, the MIDI listener to Sushi, is test_agent_midi.py's. "Another
device" is a second page, each `Page` being a browser context of its own
with its own storage, or a request made from outside any page (`post`).
Nothing here is a real Sushi, a phone or a touch screen: a tap is a click.
"""

from __future__ import annotations

import re
import time

import pytest

pytest.importorskip("aiohttp")

from rig_fixtures import frozen  # noqa: E402
from test_ui import Page, blank, browser, expect, fmit_crash, post, rigjs, served, slider, unsaved_count, until  # noqa: E402,F401
from test_ui_knobs import boxes, copies_rig  # noqa: E402
from test_ui_knobs import rack_page as drawn_rack  # noqa: E402
from ui_fake import Served, UiLifecycle, UiRig, describe_up, rig_of, strip  # noqa: E402

MINIMISED = re.compile(r"\bminimised\b")
MARKED = re.compile(r"\bmarked\b")
OPEN = re.compile(r"\bopen\b")


def settled(p):
    """Once the profiles and the plugins' names have arrived and been drawn.
    Both are fetched beside the first poll, and a row is rebuilt when either
    gives it another name or its profile's controls: a test that holds on to
    a row's knob or field starts after that, or it holds one from before."""
    p.wait_for_function("""() => ['/profiles', '/library'].every((path) =>
      performance.getEntriesByType('resource').some((r) => r.name.endsWith(path) && r.responseEnd > 0))""")
    p.evaluate("() => new Promise((done) => requestAnimationFrame(() => setTimeout(done, 50)))")


def rack_page(browser, served, width=1280):
    page = drawn_rack(browser, served, width=width)
    settled(page.page)
    return page


def tab(p, name):
    return p.get_by_role("tab", name=name, exact=True)


def unit(p, name):
    return p.locator(f'.unit[data-unit="{name}"]')


def name_of(p, name):
    """A unit's name, which is its open control: a button."""
    return unit(p, name).get_by_role("button", name=name, exact=True)


def shown(p):
    """The names of the plugins drawn, in order."""
    return p.locator(".unit button.name")


def pedal(served, name, bypassed):
    """A bypass made at Sushi by something that is not a page (module
    docstring): the next poll is the first any page hears of it."""
    with served.rig.lock:
        served.rig.bypassed[name] = bypassed


def is_selected(p, name):
    expect(tab(p, name)).to_have_attribute("aria-selected", "true")


def boxes_of(p, selector):
    """The boxes of what `selector` matches, in order."""
    return p.evaluate("""(selector) => [...document.querySelectorAll(selector)].map((e) => {
      const b = e.getBoundingClientRect(); return [Math.round(b.x), Math.round(b.y), Math.round(b.width), Math.round(b.height)]; })""",
                      selector)


# --------------------------------------------------------------------------- #
# The decisions, pure (rig.js)
# --------------------------------------------------------------------------- #

MODEL = "const model = (tracks) => R.rackModel({tracks, processors: {}, parameter_info: {}}, null, null);"


def test_the_tabs_are_the_channels_in_the_rigs_order_then_master(blank):
    out = rigjs(blank, MODEL + """
      const two = R.rackTabs(model({board: ['od', 'board_send'], vocal: ['vocal_send'], master: ['master_return']}));
      const one = R.rackTabs(model({guitar: ['guitar_send'], master: ['master_return']}));
      const v1 = R.rackTabs(model({main: ['od']}));
      const early = R.rackTabs(model({master: ['master_return'], keys: ['keys_send']}));
      return {two, one, v1, early, none: R.rackTabs(null),
              open: [R.openTab(two, 'vocal'), R.openTab(two, 'keys'), R.openTab(two, null), R.openTab([], 'vocal')]};""")
    assert out["two"] == [{"id": "board", "label": "board", "master": False}, {"id": "vocal", "label": "vocal", "master": False},
                          {"id": "master", "label": "Master", "master": True}]
    assert [t["label"] for t in out["one"]] == ["guitar", "Master"], "one channel still has its tab and Master"
    assert [t["label"] for t in out["v1"]] == ["main"], "a v1 run has no master to show"
    assert [t["label"] for t in out["early"]] == ["keys", "Master"], "Master is last wherever Sushi lists it"
    assert out["none"] == []
    assert out["open"] == ["vocal", "board", "board", None], "a tab that is not there opens the first channel"


def test_the_remembered_tab_is_this_rigs_and_anything_else_is_nothing(blank):
    out = rigjs(blank, """
      const kept = R.tabToRemember('guitar_vocal', 'vocal');
      return [R.rememberedTab(kept, 'guitar_vocal'), R.rememberedTab(kept, 'electric_board'), R.rememberedTab(null, 'guitar_vocal'),
              R.rememberedTab('not json', 'guitar_vocal'), R.rememberedTab('"vocal"', 'guitar_vocal'),
              R.rememberedTab(R.tabToRemember(null, 'vocal'), null), R.rememberedTab('{"rig": "guitar_vocal", "tab": 3}', 'guitar_vocal')];""")
    assert out == ["vocal", None, None, None, None, None, None]


def test_the_arrows_move_between_tabs_round_the_ends(blank):
    out = rigjs(blank, """
      const tabs = [{id: 'board'}, {id: 'vocal'}, {id: 'master'}];
      const k = (from, key) => R.tabKey(tabs, from, key);
      return [k('board', 'ArrowRight'), k('master', 'ArrowRight'), k('board', 'ArrowLeft'), k('vocal', 'ArrowLeft'),
              k('vocal', 'Home'), k('vocal', 'End'), k('vocal', 'Enter'), k('gone', 'ArrowRight'), R.tabKey([], 'x', 'Home')];""")
    assert out == ["vocal", "board", "master", "board", "board", "master", None, None, None]


def test_the_amp_is_in_the_tab_of_the_channel_that_takes_the_guitar(blank):
    out = rigjs(blank, """
      const tabs = [{id: 'vocal', master: false}, {id: 'guitar', master: false}, {id: 'master', master: true}];
      const file = (channels) => ({ok: true, body: {channels}});
      const both = file([{name: 'vocal', input: 'vocal-in'}, {name: 'guitar', input: 'guitar-in'}]);
      return [R.GUITAR_ROLE, R.ampTab(tabs, both, true), R.ampTab(tabs, null, true), R.ampTab(tabs, null, false),
              R.ampTab(tabs, {ok: false, reason: 'unreadable'}, true), R.ampTab(tabs, file([{name: 'vocal', input: 'vocal-in'}]), true),
              R.ampTab(tabs, file([{name: 'bass', input: 'guitar-in'}]), true), R.ampTab([], both, true),
              R.ampTab([{id: 'master', master: true}], both, true)];""")
    assert out[0] == "guitar-in", "patchbay.GUITAR_ROLE"
    assert out[1] == "guitar", "the channel the file plays from the guitar's input, not the first"
    assert out[2] is None, "a file still to be read: the card waits, it is not drawn in one tab then another"
    assert out[3] == "vocal", "no rig file (a v1 run): the first channel"
    assert out[4] == "vocal" and out[5] == "vocal", "a file unread, or with no channel on the guitar: the first channel"
    assert out[6] == "vocal", "a channel the file names that is not playing is not a tab"
    assert out[7] is None and out[8] == "master"


def test_a_tab_says_what_is_behind_it_that_is_said_nowhere_else(blank):
    out = rigjs(blank, MODEL + """
      const m = model({board: ['od', 'delay', 'board_send'], vocal: ['verb', 'vocal_send'], master: ['master_return']});
      const tabs = R.rackTabs(m);
      const up = (over) => ({state: 'up', down: false, model: {problem: null}, ...over});
      return [
        R.tabMarks(tabs, m, {}),
        R.tabMarks(tabs, m, {failed: ['p\\u0000delay\\u0000Feedback', 'b\\u0000verb', 'p\\u0000vocal_send\\u0000gain'], amp: up({}), ampOn: 'board'}),
        R.tabMarks(tabs, m, {amp: up({down: true}), ampOn: 'board'}),
        R.tabMarks(tabs, m, {amp: {state: 'failed', why: 'Carla did not answer'}, ampOn: 'vocal'}),
        R.tabMarks(tabs, m, {amp: up({model: {problem: 'the model is not a file'}}), ampOn: 'board', failed: ['a\\u0000Input Lvl']}),
        R.tabMarks(tabs, m, {amp: {state: 'none', why: 'no amp'}, ampOn: 'board'}),
      ];""")
    quiet = {"board": None, "vocal": None, "master": None}
    assert out[0] == quiet
    assert out[1] == {"board": "A change to delay was refused.", "vocal": "A change to verb was refused.", "master": None}, \
        "a send's volume is on its strip, which no tab hides"
    assert out[2] == {**quiet, "board": "Carla is not running: restart the rig to bring the amp back."}
    assert out[3] == {**quiet, "vocal": "The amp could not be read: Carla did not answer"}
    assert out[4] == {**quiet, "board": "the model is not a file A change to the amp was refused."}
    assert out[5] == quiet, "a rig with no amp is not a problem"


def test_a_row_is_slim_when_bypassed_and_opens_by_its_switch_or_a_tap(blank):
    out = rigjs(blank, """
      const st = (expanded, peeked) => ({expanded, peeked});
      return {
        rows: [R.unitRow(true, st(false, false)), R.unitRow(true, st(true, true)), R.unitRow(true, st(true, false)),
               R.unitRow(false, st(false, false)), R.unitRow(false, st(true, false)), R.unitRow(null, st(false, false))],
        taps: [R.unitTapped(true, st(false, false)), R.unitTapped(true, st(true, true)), R.unitTapped(true, st(false, true)),
               R.unitTapped(false, st(false, false)), R.unitTapped(false, st(true, false)), R.unitTapped(null, st(false, false))],
        seen: [R.bypassSeen(true, st(false, false), false), R.bypassSeen(true, st(true, false), false),
               R.bypassSeen(true, st(false, false), true), R.bypassSeen(false, st(true, true), false),
               R.bypassSeen(false, st(false, true), false), R.bypassSeen(null, st(false, true), false)],
        left: R.tabLeft(['overdrive', 'octave', 'delay'], ['octave']),
      };""")
    row = lambda minimised, all_, opened: {"minimised": minimised, "all": all_, "opened": opened}  # noqa: E731
    assert out["rows"] == [
        row(True, False, False),    # bypassed: the slim row
        row(False, True, True),     # bypassed and tapped open: all its controls
        row(True, True, False),     # bypassed, left showing all when it was on: slim, and they are there under it
        row(False, False, False),   # on: its main controls
        row(False, True, True),     # on and opened: all of them
        row(False, False, False),   # Sushi did not say: drawn as on, nothing hidden on a guess
    ]
    st = lambda expanded, peeked: {"expanded": expanded, "peeked": peeked}  # noqa: E731
    assert out["taps"] == [st(True, True), st(False, False), st(False, False), st(True, False), st(False, False), st(True, False)]
    assert out["seen"] == [
        st(False, False),  # switched off: minimised
        st(True, False),   # off while showing all: minimised, and showing all again when it comes back on
        st(False, True),   # off under the player's hand: it stays open
        st(True, False),   # switched on while peeked into: stays as it was, no longer a peek
        st(False, False), st(False, False),
    ]
    assert out["left"] == {"expanded": ["overdrive", "delay"], "peeked": []}, "the tab changed: what was peeked into closes"


# --------------------------------------------------------------------------- #
# The tabs, in Chromium
# --------------------------------------------------------------------------- #

@pytest.fixture
def rack(browser, served):
    page = rack_page(browser, served)
    yield page.page
    page.close()


def test_each_tab_shows_its_own_chain_and_nothing_of_another(rack):
    expect(rack.get_by_role("tab")).to_have_text(["board", "vocal", "Master"])
    is_selected(rack, "board")
    expect(shown(rack)).to_have_text(["overdrive", "octave", "delay"])
    tab(rack, "vocal").click()
    is_selected(rack, "vocal")
    expect(tab(rack, "board")).to_have_attribute("aria-selected", "false")
    expect(shown(rack)).to_have_text(["vocal_delay"])
    expect(rack.locator(".unit")).to_have_count(1)
    expect(rack.get_by_role("slider", name="overdrive Drive")).to_have_count(0)
    tab(rack, "Master").click()
    expect(rack.locator(".unit")).to_have_count(0)
    expect(rack.locator(".track-head .hint")).to_have_text("no plugins")
    # The strips are the mix: all of them, whatever tab is open.
    expect(rack.locator("nav.channels .strip")).to_have_count(3)
    expect(rack.locator("nav.channels").get_by_role("slider")).to_have_count(2)


def test_on_guitar_vocal_the_guitars_tab_has_the_amp_and_its_chain_and_the_vocals_its_own(browser):
    """The issue's first "done": the frozen guitar_vocal, laid out by the real
    compiler (`rig_of`), with the fake's amp."""
    guitar = ["guitar_compressor", "guitar_overdrive", "guitar_eq", "guitar_chorus", "guitar_delay", "guitar_room"]
    vocal = ["vocal_dynamics", "vocal_deess", "vocal_eq", "vocal_reverb", "vocal_limiter"]
    with Served(rig_of(frozen("guitar_vocal")), lifecycle=UiLifecycle(up="guitar_vocal"), amp=True) as served:
        page = Page(browser, served.url + "/#rack")
        p = page.page
        expect(p.get_by_role("tab")).to_have_text(["guitar", "vocal", "Master"])
        is_selected(p, "guitar")
        amp = p.locator(".amp-card")
        expect(amp).to_be_visible()
        expect(amp.locator(".amp-model")).to_have_text("Clean Room")
        expect(shown(p)).to_have_text(guitar)
        assert amp.bounding_box()["y"] < unit(p, guitar[0]).bounding_box()["y"], "the amp is in front of the chain"
        tab(p, "vocal").click()
        expect(shown(p)).to_have_text(vocal)
        expect(amp).to_be_hidden()
        expect(p.get_by_role("slider", name="amp Output Lvl")).to_have_count(0)
        tab(p, "Master").click()
        expect(p.locator(".unit")).to_have_count(0)
        expect(amp).to_be_hidden()
        tab(p, "guitar").click()
        expect(amp).to_be_visible()
        expect(p.get_by_role("slider", name="amp Output Lvl")).to_have_attribute("aria-valuetext", "−3.0 dB")
        page.close()


def test_the_tabs_are_a_tablist_the_arrows_move_and_the_panel_is_named_by_its_tab(rack):
    expect(rack.get_by_role("tablist")).to_have_count(1)
    tabs = rack.get_by_role("tablist").get_by_role("tab")
    expect(tabs).to_have_count(3)
    assert tabs.evaluate_all("(els) => els.map((e) => e.tabIndex)") == [0, -1, -1], "one stop in the tab order: the open tab"
    expect(rack.get_by_role("tabpanel", name="board")).to_be_visible()
    assert rack.get_by_role("tabpanel").get_attribute("aria-labelledby") == tab(rack, "board").get_attribute("id")
    assert tab(rack, "board").get_attribute("aria-controls") == rack.get_by_role("tabpanel").get_attribute("id")
    tab(rack, "board").focus()
    for key, opened in (("ArrowRight", "vocal"), ("ArrowRight", "Master"), ("ArrowRight", "board"), ("ArrowLeft", "Master"),
                        ("Home", "board"), ("End", "Master"), ("ArrowLeft", "vocal")):
        rack.keyboard.press(key)
        is_selected(rack, opened)
        expect(tab(rack, opened)).to_be_focused()
        expect(rack.get_by_role("tabpanel", name=opened)).to_be_visible()
    expect(shown(rack)).to_have_text(["vocal_delay"])
    assert tabs.evaluate_all("(els) => els.map((e) => e.tabIndex)") == [-1, 0, -1]


def test_a_strip_opens_its_tab_and_the_open_tabs_strip_is_marked(rack, served):
    strips = rack.locator("nav.channels .strip")
    marked = rack.locator("nav.channels .strip.open")
    expect(marked).to_have_attribute("data-channel", "board")
    opener = lambda name: rack.locator("nav.channels").get_by_role("button", name=name, exact=True)  # noqa: E731
    expect(opener("board")).to_have_attribute("aria-current", "true")
    opener("vocal").click()
    is_selected(rack, "vocal")
    expect(shown(rack)).to_have_text(["vocal_delay"])
    expect(marked).to_have_count(1)
    expect(marked).to_have_attribute("data-channel", "vocal")
    expect(opener("vocal")).to_have_attribute("aria-current", "true")
    assert opener("board").get_attribute("aria-current") is None
    # A strip's fader is the volume and nothing else: moving the board's
    # does not take the middle column away from the vocal's chain.
    fader = strips.first.get_by_role("slider", name="board volume")
    fader.focus()
    rack.keyboard.press("ArrowDown")
    until(lambda: abs(served.rig.info["board_send"]["gain"]["value"] - 119.5 / 144) < 1e-6)
    fader.click()
    is_selected(rack, "vocal")
    # A tap on the strip itself, off its name, opens it too.
    strips.first.click(position={"x": 200, "y": 14})
    is_selected(rack, "board")
    expect(marked).to_have_attribute("data-channel", "board")
    # The master's strip opens Master: one behaviour, the tab's.
    opener("master").click()
    is_selected(rack, "Master")
    expect(marked).to_have_attribute("data-channel", "master")
    # And the tabs mark the strips the same way.
    tab(rack, "vocal").click()
    expect(marked).to_have_attribute("data-channel", "vocal")


def test_the_open_tab_is_remembered_in_this_browser_for_this_rig(browser):
    from test_ui import switch_to

    with Served(save=True) as served:
        page = rack_page(browser, served)
        p = page.page
        tab(p, "vocal").click()
        p.reload()
        p.wait_for_selector(".unit")
        is_selected(p, "vocal")
        expect(shown(p)).to_have_text(["vocal_delay"])
        # Per device: another browser has its own, and starts on the first channel.
        other = rack_page(browser, served)
        is_selected(other.page, "board")
        other.close()
        # The rig changes (a switch to a rig with the same channels' names):
        # the first channel, not the tab the rig before was left on.
        switch_to(served, "second_board")
        expect(p.locator(".rig-name")).to_have_text("second_board")
        is_selected(p, "board")
        expect(shown(p)).to_have_text(["overdrive", "octave", "delay"])
        p.reload()
        p.wait_for_selector(".unit")
        is_selected(p, "board")
        tab(p, "Master").click()
        p.reload()
        p.wait_for_selector(".strip .volume")
        is_selected(p, "Master")
        # A remembered channel the rig no longer has: the first channel.
        p.evaluate("() => localStorage.setItem('sushi-rig.tab', JSON.stringify({rig: 'second_board', tab: 'keys'}))")
        p.reload()
        p.wait_for_selector(".unit")
        is_selected(p, "board")
        page.close()


def test_the_page_works_with_no_storage_and_remembers_nothing(browser, served):
    page = Page.__new__(Page)
    page.page = browser.new_page(viewport={"width": 1280, "height": 900})
    page.errors = []
    page.page.on("pageerror", lambda error: page.errors.append(str(error)))
    page.page.add_init_script(
        "Object.defineProperty(window, 'localStorage', { get() { throw new DOMException('denied', 'SecurityError'); } });")
    p = page.page
    p.goto(served.url + "/#rack")
    p.wait_for_selector(".unit")
    assert p.evaluate("() => { try { localStorage.getItem('x'); return 'readable'; } catch (e) { return e.name; } }") == "SecurityError"
    is_selected(p, "board")
    tab(p, "vocal").click()
    is_selected(p, "vocal")
    expect(shown(p)).to_have_text(["vocal_delay"])
    p.reload()
    p.wait_for_selector(".unit")
    is_selected(p, "board")
    page.close()


def test_a_channel_that_leaves_the_rig_leaves_its_tab_for_the_first(browser, served):
    served.rig.add_track("keys")
    page = rack_page(browser, served)
    p = page.page
    expect(p.get_by_role("tab")).to_have_text(["board", "vocal", "keys", "Master"])
    tab(p, "keys").click()
    is_selected(p, "keys")
    with served.rig.lock:
        del served.rig.chains["keys"]
    expect(p.get_by_role("tab")).to_have_text(["board", "vocal", "Master"])
    is_selected(p, "board")
    expect(shown(p)).to_have_text(["overdrive", "octave", "delay"])
    page.close()


def test_the_tabs_stay_while_a_command_runs_as_the_strips_do(browser):
    import threading
    import urllib.request

    with Served(save=True) as served:
        page = rack_page(browser, served)
        p = page.page
        tab(p, "vocal").click()
        handle = tab(p, "vocal").element_handle()
        served.lifecycle.delay = 2.0

        def busy():
            request = urllib.request.Request(served.url + "/rig/up", data=b'{"config_name": "electric_board", "rig": true}',
                                             headers={"Content-Type": "application/json"}, method="POST")
            try:
                urllib.request.urlopen(request, timeout=10).read()
            except Exception:  # noqa: BLE001 -- refused (a rig is up): only the busy time matters
                pass

        thread = threading.Thread(target=busy)
        thread.start()
        expect(p.locator(".rack .empty")).to_have_text("The rig is changing; the rack comes back when it is up.", timeout=4000)
        expect(p.get_by_role("tab")).to_have_text(["board", "vocal", "Master"])
        assert handle.evaluate("(e) => e.isConnected && e.getAttribute('aria-selected') === 'true'"), "the same tab, still open"
        thread.join()
        served.lifecycle.delay = 0
        expect(shown(p)).to_have_text(["vocal_delay"])
        is_selected(p, "vocal")
        page.close()


# --------------------------------------------------------------------------- #
# Minimised rows
# --------------------------------------------------------------------------- #

def test_a_bypassed_plugin_is_a_slim_row_its_name_its_switch_and_its_mono_badge(browser, served):
    copies_rig(served)  # the overdrive is mono, after a stereo reverb
    pedal(served, "overdrive", True)
    page = rack_page(browser, served)
    p = page.page
    expect(shown(p)).to_have_text(["verb", "overdrive", "octave", "delay"])
    row = unit(p, "overdrive")
    expect(row).to_have_class(MINIMISED)
    expect(name_of(p, "overdrive")).to_have_attribute("aria-expanded", "false")
    expect(row.locator(".mono-badge")).to_be_visible()
    expect(row.locator(".mono-badge")).to_have_text("mono")
    expect(row.get_by_role("button", name="Turn on overdrive")).to_be_visible()
    # And nothing else of it: no knob, no move buttons, no line under the name.
    expect(row.get_by_role("slider")).to_have_count(0)
    expect(row.get_by_role("textbox")).to_have_count(0)
    expect(row.get_by_role("button")).to_have_count(2)
    expect(row.locator(".sub")).to_be_hidden()
    slim, full = row.bounding_box(), unit(p, "delay").bounding_box()
    assert slim["height"] <= 50 < full["height"], (slim, full)
    assert slim["width"] == full["width"]
    # Its name is in the column the other rows' names are in.
    assert abs(name_of(p, "overdrive").bounding_box()["x"] - name_of(p, "delay").bounding_box()["x"]) <= 12
    # A row that is on is as it was.
    expect(unit(p, "delay")).not_to_have_class(MINIMISED)
    expect(unit(p, "delay").get_by_role("slider")).to_have_count(6)
    page.close()


def test_switching_a_plugin_off_minimises_it_and_on_opens_it_in_its_place(rack, served):
    row = unit(rack, "overdrive")
    expect(row).not_to_have_class(MINIMISED)
    rack.get_by_role("button", name="Bypass overdrive").click()
    expect(row).to_have_class(MINIMISED)
    expect(row.get_by_role("slider")).to_have_count(0)
    until(lambda: served.rig.bypassed["overdrive"] is True)
    expect(shown(rack)).to_have_text(["overdrive", "octave", "delay"])
    time.sleep(1.2)  # a poll: it stays as it is
    expect(row).to_have_class(MINIMISED)
    rack.get_by_role("button", name="Turn on overdrive").click()
    expect(row).not_to_have_class(MINIMISED)
    expect(slider(rack, "overdrive Drive")).to_be_visible()
    until(lambda: served.rig.bypassed["overdrive"] is False)
    # The octave came bypassed: switching it on opens it.
    expect(unit(rack, "octave")).to_have_class(MINIMISED)
    rack.get_by_role("button", name="Turn on octave").click()
    expect(unit(rack, "octave")).not_to_have_class(MINIMISED)
    expect(slider(rack, "octave Octaves")).to_be_visible()
    expect(shown(rack)).to_have_text(["overdrive", "octave", "delay"])


def test_a_bypass_from_a_pedal_or_another_device_minimises_the_row_on_every_page(browser, served):
    one, two = rack_page(browser, served), rack_page(browser, served)
    a, b = one.page, two.page
    for p in (a, b):
        expect(unit(p, "delay")).not_to_have_class(MINIMISED)
    pedal(served, "delay", True)
    for p in (a, b):
        expect(unit(p, "delay")).to_have_class(MINIMISED)
        expect(p.get_by_role("button", name="Turn on delay")).to_be_visible()
    # Switched on from another device: it opens here.
    b.get_by_role("button", name="Turn on delay").click()
    until(lambda: served.rig.bypassed["delay"] is False)
    for p in (a, b):
        expect(unit(p, "delay")).not_to_have_class(MINIMISED)
        expect(slider(p, "delay Feedback")).to_be_visible()
    # And off from there, through the agent as a page's switch goes.
    post(served, "/live/bypass", {"processor": "overdrive", "bypassed": True})
    for p in (a, b):
        expect(unit(p, "overdrive")).to_have_class(MINIMISED)
    pedal(served, "octave", False)
    for p in (a, b):
        expect(unit(p, "octave")).not_to_have_class(MINIMISED)
    one.close()
    two.close()


def test_a_minimised_row_is_tapped_open_without_switching_it_on_and_closes_with_its_tab(rack, served):
    expect(rack.locator("button.lock")).to_have_text("Locked")  # a view change: allowed locked
    octave = unit(rack, "octave")
    expect(octave).to_have_class(MINIMISED)
    name_of(rack, "octave").click()
    expect(octave).not_to_have_class(MINIMISED)
    expect(name_of(rack, "octave")).to_have_attribute("aria-expanded", "true")
    expect(octave.get_by_role("slider")).to_have_count(7)  # all its controls, to set it up
    expect(rack.get_by_role("button", name="Turn on octave")).to_be_visible()
    assert served.rig.bypassed["octave"] is True and served.rig.writes() == [], "opened, not switched on"
    time.sleep(2.2)  # two polls: it stays open
    expect(octave).not_to_have_class(MINIMISED)
    # Set up while it is off.
    slider(rack, "octave Cents").focus()
    rack.keyboard.press("ArrowUp")
    until(lambda: ("set_parameter", "octave", "Cents") in [c[:3] for c in served.rig.writes()])
    assert served.rig.bypassed["octave"] is True
    # Closed by its name.
    name_of(rack, "octave").click()
    expect(octave).to_have_class(MINIMISED)
    expect(name_of(rack, "octave")).to_have_attribute("aria-expanded", "false")
    # Opened by a tap on the row itself, and closed by the tab changing.
    octave.click(position={"x": 400, "y": 20})
    expect(octave).not_to_have_class(MINIMISED)
    tab(rack, "vocal").click()
    tab(rack, "board").click()
    expect(unit(rack, "octave")).to_have_class(MINIMISED)
    expect(unit(rack, "octave").get_by_role("slider")).to_have_count(0)
    # With the keys: its name is a button, and the keys stay on it.
    name_of(rack, "octave").focus()
    rack.keyboard.press("Enter")
    expect(unit(rack, "octave")).not_to_have_class(MINIMISED)
    expect(name_of(rack, "octave")).to_be_focused()
    # Switched on while open, it stays as it is; and off again, it minimises.
    rack.get_by_role("button", name="Turn on octave").click()
    until(lambda: served.rig.bypassed["octave"] is False)
    expect(unit(rack, "octave").get_by_role("slider")).to_have_count(7)
    rack.get_by_role("button", name="Bypass octave").click()
    expect(unit(rack, "octave")).to_have_class(MINIMISED)


def test_nothing_moves_on_a_poll_and_a_row_changes_size_only_with_its_bypass_or_a_tap(browser):
    """The case #135 adds to test_ui_knobs.py's box comparison: values
    changing from elsewhere, on an open row, a minimised one and a closed
    tab, move nothing; a row tapped open stays as it is through the polls;
    and a bypass from elsewhere changes its own row's size and leaves the
    strips, the tabs, the header and the files where they were."""
    def change(served, processor, parameter, value):
        assert post(served, "/live/parameter", {"processor": processor, "parameter": parameter, "value": value})["ok"]

    with Served(save=True) as served:
        page = rack_page(browser, served)
        p = page.page
        time.sleep(1.2)  # the load tiles and everything read once
        before = boxes(p)
        change(served, "overdrive", "Drive", 0.2)
        change(served, "octave", "Cents", 0.7)        # minimised
        change(served, "vocal_delay", "Feedback", 0.9)  # on a closed tab
        unsaved_count(p, 3)
        expect(slider(p, "overdrive Drive")).to_have_attribute("aria-valuetext", "0.20")
        time.sleep(1.2)  # a poll after the changes
        after = boxes(p)
        assert sorted(after) == sorted(before), "nothing came or went"
        assert {k: (before[k], after[k]) for k in before if before[k] != after[k]} == {}
        # Tapped open: a change of size, the player's. Then it holds.
        name_of(p, "octave").click()
        expect(unit(p, "octave")).not_to_have_class(MINIMISED)
        opened = boxes(p)
        assert opened != after
        change(served, "octave", "Cents", 0.4)
        expect(slider(p, "octave Cents")).to_have_attribute("aria-valuenow", "40")
        time.sleep(2.2)  # two polls
        held = boxes(p)
        assert {k: (opened[k], held[k]) for k in opened if opened[k] != held.get(k)} == {}
        # A bypass from elsewhere: its row's size, and nothing outside the chain.
        still = "header.bar *, .tab, .strip, .strip *, .rigs *, .save *, .undo"
        was, row = boxes_of(p, still), unit(p, "delay").bounding_box()
        pedal(served, "delay", True)
        expect(unit(p, "delay")).to_have_class(MINIMISED)
        assert unit(p, "delay").bounding_box()["height"] < row["height"]
        assert boxes_of(p, still) == was
        assert sorted(boxes(p)) == sorted(held), "hidden, not rebuilt: no element came or went"
        page.close()


# --------------------------------------------------------------------------- #
# What a view change must not interrupt
# --------------------------------------------------------------------------- #

def test_a_knob_drag_survives_a_tab_switch_elsewhere_a_pedal_and_its_neighbours_resizing(browser, served):
    here, there = rack_page(browser, served), rack_page(browser, served)
    p = here.page
    dial = slider(p, "delay Feedback")
    box = dial.locator("svg").bounding_box()
    x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    p.mouse.move(x, y)
    p.mouse.down()
    p.mouse.move(x, y - 32, steps=4)  # a tenth of the sweep
    expect(dial).to_have_attribute("aria-valuetext", "0.45")
    handle = dial.element_handle()
    tab(there.page, "vocal").click()   # another device changes its tab: its own
    pedal(served, "overdrive", True)   # a pedal bypasses the row above: it minimises
    expect(unit(p, "overdrive")).to_have_class(MINIMISED)
    pedal(served, "octave", False)     # and switches the one between on: it opens
    expect(unit(p, "octave")).not_to_have_class(MINIMISED)
    is_selected(p, "board")
    assert dial.locator("svg").bounding_box()["y"] != box["y"], "the row being dragged moved on the page"
    assert handle.evaluate("(e) => e.isConnected && document.activeElement === e"), "the same dial, still held"
    p.mouse.move(x, y - 64, steps=4)   # the drag carries on from where it was
    expect(dial).to_have_attribute("aria-valuetext", "0.55")
    p.mouse.up()
    until(lambda: abs(served.rig.info["delay"]["Feedback"]["value"] - 0.55) < 1e-6)
    here.close()
    there.close()


def test_a_row_being_dragged_or_typed_in_stays_open_when_a_pedal_bypasses_it(rack, served):
    dial = slider(rack, "delay Feedback")
    box = dial.locator("svg").bounding_box()
    x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    rack.mouse.move(x, y)
    rack.mouse.down()
    rack.mouse.move(x, y - 32, steps=4)
    pedal(served, "delay", True)
    expect(rack.get_by_role("button", name="Turn on delay")).to_be_visible()  # the page has heard of it
    row = unit(rack, "delay")
    expect(row).not_to_have_class(MINIMISED)  # not pulled from under the hand
    rack.mouse.move(x, y - 64, steps=4)
    rack.mouse.up()
    expect(dial).to_have_attribute("aria-valuetext", "0.55")
    until(lambda: abs(served.rig.info["delay"]["Feedback"]["value"] - 0.55) < 1e-6)
    # As a row tapped open: it stays until it is closed, by its name.
    time.sleep(1.2)
    expect(row).not_to_have_class(MINIMISED)
    expect(name_of(rack, "delay")).to_have_attribute("aria-expanded", "true")
    name_of(rack, "delay").click()
    expect(row).to_have_class(MINIMISED)
    # A value half typed, the same.
    field = rack.get_by_role("textbox", name="overdrive Drive value", exact=True)
    field.click()
    rack.keyboard.type("0.3")
    pedal(served, "overdrive", True)
    expect(rack.get_by_role("button", name="Turn on overdrive")).to_be_visible()
    expect(unit(rack, "overdrive")).not_to_have_class(MINIMISED)
    expect(field).to_be_focused()
    expect(field).to_have_value("0.3")
    rack.keyboard.press("Enter")
    until(lambda: abs(served.rig.info["overdrive"]["Drive"]["value"] - 0.3) < 1e-6)
    # A knob only focused, turned a while ago, holds nothing open.
    pedal(served, "overdrive", False)
    expect(rack.get_by_role("button", name="Bypass overdrive")).to_be_visible()
    slider(rack, "overdrive Tone").focus()
    pedal(served, "overdrive", True)
    expect(unit(rack, "overdrive")).to_have_class(MINIMISED)


def test_a_half_typed_value_survives_a_pedal_a_tab_switch_elsewhere_and_its_neighbours_resizing(browser, served):
    here, there = rack_page(browser, served), rack_page(browser, served)
    p = here.page
    field = p.get_by_role("textbox", name="delay Feedback value", exact=True)
    field.click()
    p.keyboard.type("0.9")
    handle = field.element_handle()
    writes = len(served.rig.writes())
    tab(there.page, "Master").click()
    pedal(served, "overdrive", True)
    expect(unit(p, "overdrive")).to_have_class(MINIMISED)
    pedal(served, "octave", False)
    expect(unit(p, "octave")).not_to_have_class(MINIMISED)
    time.sleep(1.2)
    assert handle.evaluate("(e) => e.isConnected && document.activeElement === e"), "the same field, still typed in"
    expect(field).to_have_value("0.9")
    assert len(served.rig.writes()) == writes, "nothing was committed for the player"
    p.keyboard.press("Enter")
    until(lambda: abs(served.rig.info["delay"]["Feedback"]["value"] - 0.9) < 1e-6)
    here.close()
    there.close()


def test_a_drag_and_a_typed_value_survive_a_plugin_added_and_one_moved_from_elsewhere(browser):
    """The chain's rows are put in their new order round the one in use,
    which is never taken out of the page to do it."""
    from ui_fake import MDA_DELAY, fixture_library

    with Served(library=fixture_library) as served:
        page = rack_page(browser, served)
        p = page.page
        dial = slider(p, "delay Feedback")
        box = dial.locator("svg").bounding_box()
        x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
        p.mouse.move(x, y)
        p.mouse.down()
        p.mouse.move(x, y - 32, steps=4)
        handle = dial.element_handle()
        assert post(served, "/live/add", {"track": "board", "uri": MDA_DELAY, "name": "slap", "after": "overdrive"})["ok"]
        expect(shown(p)).to_have_text(["overdrive", "slap", "octave", "delay"])
        assert post(served, "/live/move", {"processor": "delay", "direction": "up"})["ok"]  # the dragged row itself
        expect(shown(p)).to_have_text(["overdrive", "slap", "delay", "octave"])
        assert post(served, "/live/remove", {"processor": "slap"})["ok"]
        expect(shown(p)).to_have_text(["overdrive", "delay", "octave"])
        assert handle.evaluate("(e) => e.isConnected && document.activeElement === e"), "the same dial, still held"
        p.mouse.move(x, y - 64, steps=4)
        p.mouse.up()
        expect(slider(p, "delay Feedback")).to_have_attribute("aria-valuetext", "0.55")
        until(lambda: abs(served.rig.info["delay"]["Feedback"]["value"] - 0.55) < 1e-6)
        # A value half typed, through a neighbour moved past it from elsewhere.
        field = p.get_by_role("textbox", name="overdrive Level value", exact=True)
        field.click()
        p.keyboard.type("-7")
        typed = field.element_handle()
        assert post(served, "/live/move", {"processor": "delay", "direction": "up"})["ok"]
        expect(shown(p)).to_have_text(["delay", "overdrive", "octave"])
        assert typed.evaluate("(e) => e.isConnected && document.activeElement === e")
        expect(field).to_have_value("-7")
        p.keyboard.press("Enter")
        until(lambda: abs(served.rig.info["overdrive"]["Level"]["value"] - 13 / 24) < 1e-6)
        page.close()


def test_a_fader_drag_and_the_save_as_name_survive_a_pedal_and_a_tab_switch(browser):
    with Served(save=True) as served:
        page = rack_page(browser, served)
        p = page.page
        # The name being typed for Save as…
        p.get_by_role("button", name="Save as…").click()
        name = p.locator('input[name="rig-name"]')
        name.click()
        p.keyboard.type("board-2")
        typed = name.element_handle()
        pedal(served, "delay", True)
        expect(unit(p, "delay")).to_have_class(MINIMISED)
        assert typed.evaluate("(e) => e.isConnected && document.activeElement === e")
        expect(name).to_have_value("board-2")
        # A strip's fader, held down through a pedal's bypass and this
        # page's own keys changing the tab.
        fader = p.locator("nav.channels").get_by_role("slider", name="vocal volume")
        box = fader.bounding_box()
        y = box["y"] + box["height"] / 2
        p.mouse.move(box["x"] + box["width"] * 0.6, y)
        p.mouse.down()
        until(lambda: served.rig.info["vocal_send"]["gain"]["value"] != pytest.approx(114 / 144))
        first = served.rig.info["vocal_send"]["gain"]["value"]
        held = fader.element_handle()
        pedal(served, "delay", False)
        expect(unit(p, "delay")).not_to_have_class(MINIMISED)
        pedal(served, "overdrive", True)
        expect(unit(p, "overdrive")).to_have_class(MINIMISED)
        p.mouse.move(box["x"] + box["width"] * 0.3, y, steps=5)
        p.mouse.up()
        until(lambda: served.rig.info["vocal_send"]["gain"]["value"] < first - 0.01)
        assert held.evaluate("(e) => e.isConnected && document.activeElement === e"), "the same fader"
        # A tab chosen rebuilds no strip and not the header's master.
        master = p.get_by_role("slider", name="Master volume").element_handle()
        tab(p, "vocal").click()
        is_selected(p, "vocal")
        assert held.evaluate("(e) => e.isConnected") and master.evaluate("(e) => e.isConnected")
        expect(name).to_have_value("board-2")
        assert typed.evaluate("(e) => e.isConnected")
        page.close()


def test_the_take_name_on_the_stage_survives_a_pedal_and_a_tab_switch_on_the_rack(browser):
    """The stage is not #135's, and must not feel it: a name half typed for
    a take stays through a pedal's bypass and another device's tabs."""
    with Served(record=True) as served:
        stage = Page(browser, served.url + "/#stage", width=390, height=844)
        p = stage.page
        p.wait_for_selector(".card")
        name = p.get_by_label("Take name")
        name.click()
        p.keyboard.type("riff")
        typed = name.element_handle()
        rack = rack_page(browser, served)
        tab(rack.page, "vocal").click()
        pedal(served, "delay", True)
        expect(p.get_by_role("button", name="Turn on delay")).to_be_visible()
        pedal(served, "octave", False)
        expect(p.get_by_role("button", name="Bypass octave")).to_be_visible()
        assert typed.evaluate("(e) => e.isConnected && document.activeElement === e")
        expect(name).to_have_value("riff")
        expect(p.locator(".card.minimised")).to_have_count(0)  # the stage's cards are as they were
        rack.close()
        stage.close()


def test_the_updaters_stay_flat_as_tabs_are_chosen_and_rows_opened(rack):
    first = rack.evaluate("() => window.sushiRigUpdaters()")
    assert first["rack"] > 0
    for _ in range(3):
        for name in ("vocal", "Master", "board"):
            tab(rack, name).click()
        name_of(rack, "octave").click()
        name_of(rack, "octave").click()
        name_of(rack, "overdrive").click()
        name_of(rack, "overdrive").click()
    rack.wait_for_timeout(1200)  # a poll, to rebuild whatever it would
    assert rack.evaluate("() => window.sushiRigUpdaters()") == first


# --------------------------------------------------------------------------- #
# Nothing that matters is behind a closed tab
# --------------------------------------------------------------------------- #

def test_a_refusal_and_a_crash_are_seen_whatever_tab_is_open(rack, served):
    served.rig.refuse[("delay", "Feedback")] = "Sushi would not set Feedback (test)"
    slider(rack, "delay Feedback").focus()
    rack.keyboard.press("ArrowUp")
    alert = rack.locator(".problem.refused")
    expect(alert).to_contain_text("Not changed: delay · Feedback")
    tab(rack, "vocal").click()
    expect(shown(rack)).to_have_text(["vocal_delay"])
    # The box is above the tabs, not in one; and the tab it is about says so.
    expect(alert).to_be_visible()
    expect(alert).to_contain_text("Sushi would not set Feedback (test)")
    assert rack.locator(".tab-panel .problem, .tabs .problem").count() == 0
    assert rack.locator(".problems").bounding_box()["y"] < rack.get_by_role("tablist").bounding_box()["y"]
    expect(tab(rack, "board")).to_have_class(MARKED)
    expect(tab(rack, "board")).to_have_attribute("title", "A change to delay was refused.")
    expect(tab(rack, "board").locator(".tab-mark")).to_be_visible()
    expect(tab(rack, "vocal")).not_to_have_class(MARKED)
    # A child of the rig that exits, with the board's tab closed.
    up = served.state["describe"]()
    served.agent.supervisor.crashed.append(fmit_crash(pgid=up["pgid"], started_at=up["started_at"]))
    expect(rack.locator(".problem.agent", has_text="The tuner (fmit) exited")).to_be_visible()
    # A change that goes through takes the mark off.
    del served.rig.refuse[("delay", "Feedback")]
    tab(rack, "board").click()
    slider(rack, "delay Feedback").focus()
    rack.keyboard.press("ArrowUp")
    expect(tab(rack, "board")).not_to_have_class(MARKED)


def test_an_amp_that_stops_is_said_on_its_tab_while_another_is_open(browser):
    with Served(amp=True) as served:
        page = Page(browser, served.url + "/#rack")
        p = page.page
        p.wait_for_selector(".amp-card .fader")
        tab(p, "vocal").click()
        amp = p.locator(".amp-card")
        expect(amp).to_be_hidden()
        expect(tab(p, "board")).not_to_have_class(MARKED)
        widths = boxes_of(p, ".tab")
        served.amp.alive = False
        expect(tab(p, "board")).to_have_class(MARKED)
        expect(tab(p, "board")).to_have_attribute("title", "Carla is not running: restart the rig to bring the amp back.")
        assert boxes_of(p, ".tab") == widths, "the mark has a place of its own: no tab moved"
        tab(p, "board").click()
        expect(amp).to_contain_text("Carla is not running: restart the rig to bring the amp back.")
        page.close()


# --------------------------------------------------------------------------- #
# Structure, the files, and the rigs that are not two channels and an amp
# --------------------------------------------------------------------------- #

def test_a_plugin_added_moved_or_removed_lands_in_its_own_tab(browser):
    from ui_fake import MDA_DELAY, fixture_library

    with Served(library=fixture_library) as served:
        page = rack_page(browser, served)
        p = page.page
        assert post(served, "/live/add", {"track": "master", "uri": MDA_DELAY, "name": "bus_delay"})["ok"]
        assert post(served, "/live/add", {"track": "vocal", "uri": MDA_DELAY, "name": "slap"})["ok"]
        unsaved_count(p, 2)  # counted with their tabs closed
        expect(shown(p)).to_have_text(["overdrive", "octave", "delay"])
        tab(p, "Master").click()
        expect(shown(p)).to_have_text(["bus_delay"])
        tab(p, "vocal").click()
        expect(shown(p)).to_have_text(["vocal_delay", "slap"])
        # Moved from the page, on the tab that is open.
        lock = p.locator("button.lock")
        lock.click()
        lock.click()
        expect(lock).to_have_text("Lock off · editing")
        expect(p.get_by_role("button", name="Move vocal_delay up", exact=True)).to_be_disabled()
        p.get_by_role("button", name="Move slap up", exact=True).click()
        until(lambda: served.rig.chains["vocal"] == ["slap", "vocal_delay", "vocal_send"])
        expect(shown(p)).to_have_text(["slap", "vocal_delay"])
        expect(p.get_by_role("button", name="Move slap up", exact=True)).to_be_disabled()
        expect(p.get_by_role("button", name="Move vocal_delay down", exact=True)).to_be_disabled()
        is_selected(p, "vocal")
        # Removed from elsewhere, on a tab that is closed and on the one open.
        assert post(served, "/live/remove", {"processor": "bus_delay"})["ok"]
        assert post(served, "/live/remove", {"processor": "vocal_delay"})["ok"]
        expect(shown(p)).to_have_text(["slap"])
        tab(p, "Master").click()
        expect(p.locator(".unit")).to_have_count(0)
        tab(p, "board").click()
        expect(shown(p)).to_have_text(["overdrive", "octave", "delay"])
        page.close()


def test_undo_save_and_the_unsaved_count_are_as_they_were_whatever_tab_is_open(browser):
    from test_ui import drive_of, rig_file

    with Served(save=True) as served:
        page = rack_page(browser, served)
        p = page.page
        drive = slider(p, "overdrive Drive")
        drive.focus()
        for _ in range(10):
            p.keyboard.press("ArrowDown")
        until(lambda: abs(served.rig.info["overdrive"]["Drive"]["value"] - 0.52) < 1e-9)
        unsaved_count(p, 1)
        tab(p, "vocal").click()
        unsaved_count(p, 1)  # the board's change, with the board's tab closed
        p.get_by_role("button", name="Bypass vocal_delay").click()
        unsaved_count(p, 2)
        tab(p, "Master").click()
        unsaved_count(p, 2)
        # Undo takes back the newest change, on a tab that is not open.
        p.get_by_role("button", name="Undo · bypass vocal_delay").click()
        until(lambda: served.rig.bypassed["vocal_delay"] is False)
        unsaved_count(p, 1)
        expect(p.get_by_role("button", name="Undo · overdrive · Drive")).to_be_visible()
        # Save writes the board's change from the Master tab.
        p.get_by_role("button", name="Save to electric_board").click()
        expect(p.locator(".save-result.ok")).to_contain_text("Saved electric_board")
        unsaved_count(p, 0)
        assert drive_of(rig_file(served)) == pytest.approx(0.52)
        is_selected(p, "Master")
        # Revert is offered as before.
        expect(p.get_by_role("button", name="Revert…")).to_be_enabled()
        page.close()


def test_a_rig_with_no_amp_says_so_in_the_first_channels_tab(rack):
    amp = rack.locator(".amp-card")
    expect(amp).to_contain_text("the running rig has no amp")
    assert amp.bounding_box()["y"] < unit(rack, "overdrive").bounding_box()["y"]
    tab(rack, "vocal").click()
    expect(amp).to_be_hidden()
    expect(shown(rack)).to_have_text(["vocal_delay"])


def test_a_rig_that_is_down_has_no_tabs_and_says_so(browser):
    with Served(lifecycle=UiLifecycle(up=None)) as served:
        page = Page(browser, served.url + "/#rack")
        p = page.page
        expect(p.locator(".rack .empty")).to_have_text(
            "No rig to show: nothing is running in Sushi. Choose one under RIGS to start it.")
        expect(p.get_by_role("tab")).to_have_count(0)
        expect(p.locator(".tabs")).to_be_hidden()
        expect(p.get_by_role("tabpanel")).to_have_count(0)
        expect(p.locator(".amp-card")).to_be_hidden()
        # The files are still there: the rigs of config/rigs, to start one.
        expect(p.locator(".rig", has_text="electric_board")).to_be_visible()
        expect(p.locator(".inputs-why")).to_contain_text("No rig is running")
        # Started, its tabs come.
        p.locator(".rig", has_text="electric_board").click()
        p.get_by_role("button", name="Start electric_board").click()
        expect(p.get_by_role("tab")).to_have_text(["board", "vocal", "Master"], timeout=8000)
        is_selected(p, "board")
        page.close()


def one_track(name, plugins, master=False):
    """The fake rig cut down to one track of `plugins` (and a master)."""
    rig = UiRig()
    rig.chains = {name: plugins + ([f"{name}_send"] if master else [])}
    if master:
        rig.chains["master"] = ["master_return"]
        rig.info[f"{name}_send"] = rig.info["board_send"]
        rig.classes[f"{name}_send"] = rig.classes["board_send"]
        rig.bypassed[f"{name}_send"] = False
    keep = set(rig.chains) | {p for chain in rig.chains.values() for p in chain}
    rig.info[name] = strip()
    for held in (rig.info, rig.bypassed, rig.classes):
        for gone in [k for k in held if k not in keep]:
            del held[gone]
    return rig


def test_a_v1_run_has_a_tab_per_track_and_no_master(browser):
    lifecycle = UiLifecycle(up="guitar_vocal")
    describe = describe_up(lifecycle.started, "guitar_vocal")
    with Served(one_track("main", ["overdrive", "octave", "delay"]), lifecycle=lifecycle,
                describe=lambda: {**describe(), "rig": None}) as served:
        page = Page(browser, served.url + "/#rack")
        p = page.page
        p.wait_for_selector(".unit")
        expect(p.get_by_role("tab")).to_have_text(["main"])
        is_selected(p, "main")
        expect(shown(p)).to_have_text(["overdrive", "octave", "delay"])
        expect(unit(p, "octave")).to_have_class(MINIMISED)
        expect(p.locator(".amp-card")).to_contain_text("no amp")  # no file to place it by: the first track's tab
        expect(p.locator(".inputs-why")).to_contain_text("guitar_vocal is a v1 config")
        expect(p.locator("nav.channels .strip")).to_have_count(0)
        page.close()


def test_a_rig_of_one_channel_still_has_its_tab_and_master(browser):
    with Served(one_track("guitar", ["overdrive", "delay"], master=True)) as served:
        page = rack_page(browser, served)
        p = page.page
        expect(p.get_by_role("tab")).to_have_text(["guitar", "Master"])
        is_selected(p, "guitar")
        expect(shown(p)).to_have_text(["overdrive", "delay"])
        tab(p, "Master").click()
        expect(p.locator(".unit")).to_have_count(0)
        expect(p.locator("nav.channels .strip.open")).to_have_attribute("data-channel", "master")
        page.close()


@pytest.mark.parametrize("font", ["", "* { font-family: 'DejaVu Sans', 'Liberation Sans', sans-serif !important; letter-spacing: .08em; }"],
                         ids=["page fonts", "a wider fallback font"])
def test_at_phone_width_three_channels_tabs_do_not_make_the_page_scroll_sideways(browser, served, font):
    """Strips, then the tabs and the open chain, then the files; and four
    tabs (three channels and Master) wrap rather than widen the page."""
    served.rig.add_track("keyboards")
    page = Page(browser, served.url + "/#rack", width=390, height=844)
    p = page.page
    p.wait_for_selector(".strip .volume")
    p.wait_for_selector(".unit")
    if font:
        p.add_style_tag(content=font)
    expect(p.get_by_role("tab")).to_have_text(["board", "vocal", "keyboards", "Master"])
    tops = [p.locator(sel).bounding_box()["y"] for sel in ("nav.channels", ".tabs", ".tab-panel", ".files")]
    assert tops == sorted(tops), tops
    for name in ("board", "keyboards", "Master"):
        tab(p, name).click()
        is_selected(p, name)
        wide = p.evaluate("() => [...document.querySelectorAll('#app *')].filter((e) => e.getBoundingClientRect().right > innerWidth + 0.5).map((e) => e.className)")
        assert wide == [], wide
        width = p.evaluate("() => [document.documentElement.scrollWidth, innerWidth]")
        assert width[0] <= width[1], width
    assert all(b[3] >= 44 for b in boxes_of(p, ".tab")), "each tab takes a thumb"
    # A strip tapped takes the page to its chain, below the strips.
    p.evaluate("() => scrollTo(0, 0)")
    p.locator("nav.channels").get_by_role("button", name="vocal", exact=True).click()
    is_selected(p, "vocal")
    until(lambda: 0 <= p.get_by_role("tablist").bounding_box()["y"] < 200)
    page.close()
