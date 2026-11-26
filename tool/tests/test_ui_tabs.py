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


def test_the_amp_is_in_the_tab_of_the_channel_the_running_rig_feeds_from_the_guitar(blank):
    """The running rig decides (GET /machine/roles's `used_by`, read from the
    running config), and the file only when that cannot say: Change input
    writes the file, and the amp is in front of the channel it was until
    the restart (review of #135, finding 4)."""
    out = rigjs(blank, """
      const tabs = [{id: 'vocal', master: false}, {id: 'guitar', master: false}, {id: 'master', master: true}];
      const file = (channels) => ({ok: true, body: {channels}});
      const roles = (usedBy, over = {}) => ({ok: true, body: {running: {rig: 'gv'}, wiring_note: null,
        roles: [{name: 'vocal-in', used_by: ['vocal']}, {name: 'guitar-in', used_by: usedBy}], ...over}});
      const fileSaysVocal = file([{name: 'vocal', input: 'guitar-in'}, {name: 'guitar', input: 'vocal-in'}]);
      const fileSaysGuitar = file([{name: 'vocal', input: 'vocal-in'}, {name: 'guitar', input: 'guitar-in'}]);
      return [R.GUITAR_ROLE,
        R.ampTab(tabs, roles(['guitar']), fileSaysVocal, true),
        R.ampTab(tabs, roles([]), fileSaysGuitar, true),
        R.ampTab(tabs, roles(['guitar']), null, true),
        R.ampTab(tabs, null, fileSaysGuitar, true),
        R.ampTab(tabs, {ok: false, reason: 'no machine file'}, fileSaysGuitar, true),
        R.ampTab(tabs, roles([], {running: null}), fileSaysGuitar, true),
        R.ampTab(tabs, roles([], {wiring_note: 'the running config could not be read'}), fileSaysGuitar, true),
        R.ampTab(tabs, {ok: true, body: {running: {rig: 'gv'}, wiring_note: null, roles: []}}, fileSaysGuitar, true),
        R.ampTab(tabs, {ok: false, reason: 'x'}, null, true),
        R.ampTab(tabs, {ok: false, reason: 'x'}, {ok: false, reason: 'unreadable'}, true),
        R.ampTab(tabs, {ok: false, reason: 'x'}, file([{name: 'bass', input: 'guitar-in'}]), true),
        R.ampTab(tabs, null, null, false),
        R.ampTab([], roles(['guitar']), null, true),
        R.ampTab([{id: 'master', master: true}], null, null, false)];""")
    assert out[0] == "guitar-in", "patchbay.GUITAR_ROLE"
    assert out[1] == "guitar", "the running rig's wiring, whatever the file now says"
    assert out[2] == "vocal", "a run with nothing on the guitar's input: the first channel, not the file's word"
    assert out[3] == "guitar", "the running rig said: the file is not waited for"
    assert out[4] is None, "the running rig still to answer: the card waits, it is not drawn in one tab then another"
    assert out[5] == "guitar" and out[6] == "guitar" and out[7] == "guitar" and out[8] == "guitar", \
        "the running rig cannot say (no answer, no running rig of channels, its config unread, no such role): the file"
    assert out[9] is None, "and the file still to answer: the card waits"
    assert out[10] == "vocal" and out[11] == "vocal", "a file unread, or naming a channel not playing: the first channel"
    assert out[12] == "vocal", "no rig file (a v1 run): the first channel, nothing waited for"
    assert out[13] is None and out[14] == "master"


def test_a_tab_says_what_is_behind_it_that_is_said_nowhere_else(blank):
    out = rigjs(blank, MODEL + """
      const m = model({board: ['od', 'delay', 'board_send'], vocal: ['verb', 'vocal_send'], master: ['master_return']});
      const tabs = R.rackTabs(m);
      const up = (over) => ({state: 'up', down: false, model: {problem: null}, ...over});
      const card = (answer, models) => R.ampCard({ok: true, body: {known: 'sent', model: {stored: 'amp/models/x.nam', ...answer},
        parameters: [], held: true, carla: {alive: true}}}, models);
      return [
        R.tabMarks(tabs, m, {}),
        R.tabMarks(tabs, m, {failed: ['p\u0000delay\u0000Feedback', 'b\u0000verb', 'p\u0000vocal_send\u0000gain'], amp: up({}), ampOn: 'board'}),
        R.tabMarks(tabs, m, {amp: up({down: true}), ampOn: 'board'}),
        R.tabMarks(tabs, m, {amp: {state: 'failed', why: 'Carla did not answer'}, ampOn: 'vocal'}),
        R.tabMarks(tabs, m, {amp: up({model: {problem: 'the model is not a file'}}), ampOn: 'board', failed: ['a\u0000Input Lvl']}),
        R.tabMarks(tabs, m, {amp: {state: 'none', why: 'no amp'}, ampOn: 'board'}),
        R.tabMarks(tabs, m, {amp: card({warning: 'made at 44100 Hz, the rig runs at 48000'}, {ok: true, body: {models: []}}), ampOn: 'board'}),
        R.tabMarks(tabs, m, {amp: card({}, {ok: false, reason: 'amp/models is not a folder'}), ampOn: 'board'}),
        R.tabMarks(tabs, m, {amp: up({down: true}), ampOn: null}),
        card({}, {ok: true, body: {models: []}}).listProblem,
      ];""")
    quiet = {"board": None, "vocal": None, "master": None}
    carla = "Carla is not running: restart the rig to bring the amp back."
    assert out[0] == quiet
    assert out[1] == {"board": "A change to delay was refused.", "vocal": "A change to verb was refused.", "master": None}, \
        "a send's volume is on its strip, which no tab hides"
    assert out[2] == {**quiet, "board": carla}
    assert out[3] == {**quiet, "vocal": "The amp could not be read: Carla did not answer"}
    assert out[4] == {**quiet, "board": "the model is not a file A change to the amp was refused."}
    assert out[5] == quiet, "a rig with no amp is not a problem"
    # Every fault the card can show (review of #135, finding 7).
    assert out[6] == {**quiet, "board": "made at 44100 Hz, the rig runs at 48000"}, "the model's rate warning"
    assert out[7] == {**quiet, "board": "The amp's models could not be listed: amp/models is not a folder"}
    assert out[8] == {"board": carla, "vocal": carla, "master": None}, \
        "the amp's tab not known yet: its fault on every channel's tab, hidden by none"
    assert out[9] is None


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
        expect(p.locator(".rack .empty")).to_have_attribute("title", "The rig is changing; the rack comes back when it is up.", timeout=4000)
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
    alert = rack.locator(".activity .problem.refused")
    expect(alert).to_contain_text("Not changed: delay · Feedback")
    tab(rack, "vocal").click()
    expect(shown(rack)).to_have_text(["vocal_delay"])
    # It is in the activity zone, above the tabs, not in one; and the tab it is about says so.
    expect(alert).to_be_visible()
    expect(alert).to_have_attribute("title", re.compile(r"Sushi would not set Feedback \(test\)"))
    assert rack.locator(".tab-panel .problem, .tabs .problem").count() == 0
    assert rack.locator(".activity").bounding_box()["y"] < rack.get_by_role("tablist").bounding_box()["y"]
    expect(tab(rack, "board")).to_have_class(MARKED)
    expect(tab(rack, "board")).to_have_attribute("title", "A change to delay was refused.")
    expect(tab(rack, "board").locator(".tab-mark")).to_be_visible()
    expect(tab(rack, "vocal")).not_to_have_class(MARKED)
    # A child of the rig that exits, with the board's tab closed.
    up = served.state["describe"]()
    served.agent.supervisor.crashed.append(fmit_crash(pgid=up["pgid"], started_at=up["started_at"]))
    expect(rack.locator("header .problem.agent", has_text="The tuner (fmit) exited")).to_be_visible()
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
        expect(amp.locator(".missing")).to_have_attribute("title", "Carla is not running: restart the rig to bring the amp back.")
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
        p.get_by_role("button", name="Undo bypass vocal_delay").click()
        until(lambda: served.rig.bypassed["vocal_delay"] is False)
        unsaved_count(p, 1)
        expect(p.get_by_role("button", name="Undo overdrive Drive")).to_be_visible()
        # Save writes the board's change from the Master tab.
        p.get_by_role("button", name="Save to electric_board").click()
        expect(p.locator(".activity .save-result.ok")).to_contain_text("Saved electric_board")
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
        expect(p.locator(".rack .empty")).to_have_text("No rig")
        expect(p.locator(".rack .empty")).to_have_attribute(
            "title", "Nothing is running in Sushi. Choose one under RIGS to start it.")
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
        rig.chains["master"] = ["master_return", "master_limiter"]  # as compiled (#137)
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


LONG = ["acoustic_guitar_and_looper", "backing_vocals_left_and_right", "keyboards_and_synth_pads"]


@pytest.mark.parametrize("font", ["", "* { font-family: 'DejaVu Sans', 'Liberation Sans', sans-serif !important; letter-spacing: .08em; }"],
                         ids=["page fonts", "a wider fallback font"])
@pytest.mark.parametrize("width", [390, 320])
def test_at_phone_width_long_channels_tabs_do_not_make_the_page_scroll_sideways(browser, served, font, width):
    """Strips, then the tabs and the open chain, then the files; and six
    tabs, three of them channels named as long as names get (24 to 29
    characters), wrap rather than widen the page, at a phone's 390 px and a
    small one's 320."""
    for name in LONG:
        served.rig.add_track(name)
    page = Page(browser, served.url + "/#rack", width=width, height=844)
    p = page.page
    p.wait_for_selector(".strip .volume")
    p.wait_for_selector(".unit")
    if font:
        p.add_style_tag(content=font)
    expect(p.get_by_role("tab")).to_have_text(["board", "vocal", *LONG, "Master"])
    tops = [p.locator(sel).bounding_box()["y"] for sel in ("nav.channels", ".tabs", ".tab-panel", ".files")]
    assert tops == sorted(tops), tops
    for name in ("board", *LONG, "Master"):
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
    until(lambda: -1 <= p.get_by_role("tablist").bounding_box()["y"] < 200)  # at its top, give or take a sub-pixel
    page.close()


# --------------------------------------------------------------------------- #
# Review of #135: what a late or failing answer, a lost pointer or a pedal
# must not do
# --------------------------------------------------------------------------- #

def amp_rack(browser, served, **size):
    page = Page(browser, served.url + "/#rack", **size)
    page.page.wait_for_selector(".amp-card .fader", state="visible")
    settled(page.page)
    return page


def failing_live(p):
    """/live answers as a Sushi that stopped answering would (a 502 with the
    agent's reason), until the returned function is called."""
    on = {"fail": True}

    def live(route):
        if on["fail"]:
            route.fulfill(status=502, json={"ok": False, "reason": "Sushi did not answer (test)"})
        else:
            route.continue_()

    p.route(lambda url: url.endswith("/live"), live)
    return lambda: on.update(fail=False)


def test_a_failed_live_keeps_the_amp_card_where_it_is_and_a_drag_on_its_fader(browser):
    """Finding 1: with Sushi not answering, the card is Carla's and still
    works; on master it stayed, and one failed poll must not take it away
    from under a fader. With no chain ever read, there are no tabs, and the
    card is shown on its own."""
    with Served(amp=True) as served:
        page = amp_rack(browser, served)
        p = page.page
        fader = p.get_by_role("slider", name="amp Output Lvl")
        handle = fader.element_handle()
        box = fader.bounding_box()
        y = box["y"] + box["height"] / 2
        p.mouse.move(box["x"] + box["width"] * 0.3, y)
        p.mouse.down()
        until(lambda: "Output Lvl" in served.amp.carla.values())
        pressed = served.amp.carla.values()["Output Lvl"]
        recover = failing_live(p)
        expect(p.locator(".status")).to_contain_text("Up, but Sushi did not answer", timeout=4000)
        time.sleep(1.2)  # another poll, failing too
        expect(p.locator(".amp-card")).to_be_visible()
        assert fader.bounding_box() == box, "the card did not move"
        assert handle.evaluate("(e) => e.isConnected")
        expect(p.get_by_role("tab")).to_have_text(["board", "vocal", "Master"])  # kept while the rig is up
        p.mouse.move(box["x"] + box["width"] * 0.8, y, steps=5)
        p.mouse.up()
        until(lambda: served.amp.carla.values()["Output Lvl"] > pressed + 5)
        recover()
        expect(p.locator(".unit").first).to_be_visible(timeout=4000)
        page.close()
        # Failing from the first: no chain read, no tabs, the card on its own.
        page = Page.__new__(Page)
        page.page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.errors = []
        page.page.on("pageerror", lambda error: page.errors.append(str(error)))
        failing_live(page.page)
        page.page.goto(served.url + "/#rack")
        expect(page.page.locator(".amp-card .fader").first).to_be_visible(timeout=4000)
        expect(page.page.get_by_role("tab")).to_have_count(0)
        page.close()


def test_a_drag_survives_a_plugin_added_and_moved_while_their_plugins_are_read_slowly(browser):
    """Finding 2, and the container's failure: with /live/plugins slow, the
    new chains are shown with the plugins already known, and a render while
    the answer is out (the load tiles' every two seconds) rebuilds no row."""
    from ui_fake import MDA_DELAY, fixture_library

    with Served(library=fixture_library) as served:
        page = rack_page(browser, served)
        p = page.page
        served.rig.plugins_delay = 3.0
        other = slider(p, "overdrive Drive").element_handle()
        dial = slider(p, "delay Feedback")
        box = dial.locator("svg").bounding_box()
        x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
        p.mouse.move(x, y)
        p.mouse.down()
        p.mouse.move(x, y - 32, steps=4)
        handle = dial.element_handle()
        assert post(served, "/live/add", {"track": "board", "uri": MDA_DELAY, "name": "slap", "after": "overdrive"})["ok"]
        expect(shown(p)).to_have_text(["overdrive", "slap", "octave", "delay"])
        assert post(served, "/live/move", {"processor": "delay", "direction": "up"})["ok"]
        time.sleep(2.5)  # a render from the load tiles while the plugins' answer is out
        assert handle.evaluate("(e) => e.isConnected && document.activeElement === e"), "the same dial, still held"
        assert other.evaluate("(e) => e.isConnected"), "no other row rebuilt either"
        p.mouse.move(x, y - 64, steps=4)
        p.mouse.up()
        until(lambda: abs(served.rig.info["delay"]["Feedback"]["value"] - 0.55) < 1e-6)
        expect(shown(p)).to_have_text(["overdrive", "slap", "delay", "octave"], timeout=8000)
        # The answer, when it comes, names the new plugin and rebuilds its row alone.
        expect(unit(p, "slap")).to_contain_text("no profile", timeout=8000)
        assert other.evaluate("(e) => e.isConnected") and handle.evaluate("(e) => e.isConnected")
        served.rig.plugins_delay = 0.0
        page.close()


def hold(p, *patterns):
    """Requests for `patterns` held unanswered, until the returned function
    lets them go (and every later one through)."""
    held, on = [], {"hold": True}

    def keep(route):
        if on["hold"]:
            held.append(route)
        else:
            route.continue_()

    for pattern in patterns:
        p.route(pattern, keep)

    def release():
        on["hold"] = False
        for route in held:
            route.continue_()
    return release


@pytest.mark.parametrize("late", [0.6, 3.0], ids=["answered in time", "answered past the wait"])
def test_nothing_moves_when_what_places_the_amp_arrives_late(browser, late):
    """Finding 3: the chain is not drawn and then pushed down by the card.
    Held until the answers that place the amp arrive or a bounded wait ends
    (then the first channel, and it stays there for the run)."""
    with Served(amp=True) as served:
        page = Page.__new__(Page)
        page.page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.errors = []
        p = page.page
        p.on("pageerror", lambda error: page.errors.append(str(error)))
        release = hold(p, "**/machine/roles", "**/rigs/*/channels")
        p.goto(served.url + "/#rack")
        expect(p.get_by_role("tab")).to_have_text(["board", "vocal", "Master"])
        seen = []
        deadline = time.monotonic() + late
        while time.monotonic() < deadline:
            seen.append(p.evaluate("""() => { const u = document.querySelector('.unit'); const a = document.querySelector('.amp-card');
              return [u ? Math.round(u.getBoundingClientRect().y) : null, a && !a.hidden ? Math.round(a.getBoundingClientRect().y) : null]; }"""))
            time.sleep(0.1)
        release()
        expect(p.locator(".amp-card .fader").first).to_be_visible(timeout=4000)
        expect(p.locator(".unit").first).to_be_visible()
        settled(p)
        final = [round(unit(p, "overdrive").bounding_box()["y"]), round(p.locator(".amp-card").bounding_box()["y"])]
        time.sleep(1.5)  # the answers in, a poll or so
        assert [round(unit(p, "overdrive").bounding_box()["y"]), round(p.locator(".amp-card").bounding_box()["y"])] == final
        # Before: nothing drawn yet, or (past the wait) drawn where it ends up.
        assert all(s in ([None, None], final) for s in seen), seen
        if late > 2.5:
            assert final in seen, "past the wait the chain was drawn, with the card, in the first channel"
        else:
            assert final not in seen, "in time, nothing was drawn before the answers"
        page.close()


def roles_answer(used_by):
    """GET /machine/roles as the agent answers it for a running rig of
    channels: the shape is rigedit.role_options's, the ports invented."""
    role = lambda name, used: {"name": name, "ports": [f"iface:{name}"], "mono_to_stereo": False, "direction": "input",  # noqa: E731
                               "widths": {"mono": {"ok": True, "why": None}, "stereo": {"ok": False, "why": "one port"}},
                               "master": {"ok": False, "why": "an input"}, "used_by": used}
    return {"ok": True, "machine": "machines/fake.yaml", "output": "monitor-out", "ceiling": None, "wiring_note": None,
            "running": {"rig": "electric_board", "file": "config/rigs/electric_board.yaml"},
            "roles": [role("guitar-in", used_by), role("vocal-in", [c for c in ("board", "vocal") if c not in used_by])]}


def test_the_amps_tab_is_the_running_rigs_and_moves_only_with_the_run(browser):
    """Finding 4: the rig's file says the guitar is on the board, the running
    rig plays it into the vocal: the card is in the vocal's tab. A new run
    that plays it into the board moves it there."""
    with Served(amp=True) as served:
        page = Page.__new__(Page)
        page.page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.errors = []
        p = page.page
        p.on("pageerror", lambda error: page.errors.append(str(error)))
        said = {"used_by": ["vocal"]}
        p.route("**/machine/roles", lambda route: route.fulfill(json=roles_answer(said["used_by"])))
        p.route("**/rigs/*/channels", lambda route: route.fulfill(json={
            "ok": True, "rig": "electric_board", "file": "config/rigs/electric_board.yaml", "blended": [], "blended_note": None,
            "channels": [{"name": "board", "input": "guitar-in", "width": "mono", "level": 0.0, "plugins": []},
                         {"name": "vocal", "input": "vocal-in", "width": "mono", "level": 0.0, "plugins": []}]}))
        p.goto(served.url + "/#rack")
        expect(p.locator(".unit").first).to_be_visible()
        amp = p.locator(".amp-card")
        expect(amp).to_be_hidden()
        tab(p, "vocal").click()
        expect(amp).to_be_visible()
        expect(p.get_by_role("slider", name="amp Output Lvl")).to_be_visible()
        # The file is edited (Change input) and read again: the running rig still says vocal.
        time.sleep(1.2)
        expect(amp).to_be_visible()
        # A restart that plays the guitar into the board: a new run.
        said["used_by"] = ["board"]
        from datetime import datetime, timezone
        served.lifecycle.started = datetime.now(timezone.utc).replace(microsecond=0)
        expect(amp).to_be_hidden(timeout=5000)
        tab(p, "board").click()
        expect(amp).to_be_visible()
        page.close()


def test_a_drag_ends_cleanly_when_its_pointer_is_lost_and_a_reorder_keeps_the_dragged_row(rack, served):
    """Finding 5: a knob dragged while the focus is elsewhere (a second
    finger on a strip's fader) is still in use, and a reorder from another
    device does not take it out of the page; and a drag whose pointer is lost
    ends, so the next press is a drag of its own and not a "second finger"."""
    dial = slider(rack, "delay Feedback")
    box = dial.locator("svg").bounding_box()
    x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    rack.mouse.move(x, y)
    rack.mouse.down()
    rack.mouse.move(x, y - 32, steps=4)
    expect(dial).to_have_attribute("aria-valuetext", "0.45")
    handle = dial.element_handle()
    rack.locator("nav.channels").get_by_role("slider", name="vocal volume").focus()  # the focus elsewhere
    assert post(served, "/live/move", {"processor": "delay", "direction": "up"})["ok"]
    expect(shown(rack)).to_have_text(["overdrive", "delay", "octave"])
    assert handle.evaluate("(e) => e.isConnected && !!e.dataset.dragging"), "the dragged row was not taken out of the page"
    rack.mouse.move(x, y - 64, steps=4)
    rack.mouse.up()
    expect(dial).to_have_attribute("aria-valuetext", "0.55")
    assert handle.evaluate("(e) => e.dataset.dragging") is None, "released: the drag is over"
    # A pedal's bypass now minimises it: nothing holds it open.
    pedal(served, "delay", True)
    expect(unit(rack, "delay")).to_have_class(MINIMISED)
    pedal(served, "delay", False)
    expect(unit(rack, "delay")).not_to_have_class(MINIMISED)
    # The pointer lost mid-drag (the browser taking it): the drag ends with it.
    box = dial.locator("svg").bounding_box()
    x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    rack.mouse.move(x, y)
    rack.mouse.down()
    rack.mouse.move(x, y - 32, steps=4)
    expect(dial).to_have_attribute("aria-valuetext", "0.65")
    dial.evaluate("(e) => e.releasePointerCapture(Number(e.dataset.dragging))")
    rack.mouse.move(x, y - 48, steps=2)  # the browser says the capture is lost as the pointer next moves
    assert dial.evaluate("(e) => e.dataset.dragging") is None, "the drag ended with its pointer"
    expect(dial).to_have_attribute("aria-valuetext", "0.65")  # and that move set nothing
    rack.mouse.up()
    # A press of its own: a full-speed drag from where it is, not a fine one from a stale height.
    rack.mouse.move(x, y)
    rack.mouse.down()
    rack.mouse.move(x, y - 32, steps=4)
    rack.mouse.up()
    expect(dial).to_have_attribute("aria-valuetext", "0.75")


def test_a_pedals_bypass_moves_the_focus_from_what_it_hides_to_the_rows_name(rack, served):
    """Finding 6: a keyboard or screen reader keeps its place."""
    slider(rack, "overdrive Drive").focus()
    pedal(served, "overdrive", True)
    expect(unit(rack, "overdrive")).to_have_class(MINIMISED)
    expect(name_of(rack, "overdrive")).to_be_focused()
    rack.keyboard.press("Enter")  # and from there, opened again with a key
    expect(unit(rack, "overdrive")).not_to_have_class(MINIMISED)
    # A value clicked into and not typed in: the same.
    rack.get_by_role("textbox", name="delay Feedback value", exact=True).click()
    pedal(served, "delay", True)
    expect(unit(rack, "delay")).to_have_class(MINIMISED)
    expect(name_of(rack, "delay")).to_be_focused()
    # The switch itself stays where it is, and keeps the focus.
    pedal(served, "octave", False)
    expect(rack.get_by_role("button", name="Bypass octave")).to_be_visible()
    rack.get_by_role("button", name="Bypass octave").focus()
    pedal(served, "octave", True)
    expect(unit(rack, "octave")).to_have_class(MINIMISED)
    expect(rack.get_by_role("button", name="Turn on octave")).to_be_focused()


def test_an_amp_whose_models_cannot_be_listed_marks_its_tab(browser):
    """Finding 7: every fault the card shows is on its tab."""
    with Served(amp=True) as served:
        page = Page.__new__(Page)
        page.page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.errors = []
        p = page.page
        p.on("pageerror", lambda error: page.errors.append(str(error)))
        p.route("**/amp/models", lambda route: route.fulfill(status=500, json={"ok": False, "reason": "amp/models is unreadable (test)"}))
        p.goto(served.url + "/#rack")
        expect(p.locator(".amp-card .fader").first).to_be_visible()
        tab(p, "vocal").click()
        expect(tab(p, "board")).to_have_class(MARKED)
        expect(tab(p, "board")).to_have_attribute("title", "The amp's models could not be listed: amp/models is unreadable (test)")
        expect(tab(p, "vocal")).not_to_have_class(MARKED)
        page.close()


# --------------------------------------------------------------------------- #
# Re-review of #135: which run the page is showing
# --------------------------------------------------------------------------- #

def test_the_amps_tab_waits_for_the_new_runs_own_tabs(browser):
    """A restart with a channel more, whose first polls find Sushi not
    answering: the run before's tabs are not kept for it, so the amp is not
    placed against them (it would have gone to the first channel for the
    whole run); it is placed with the new run's own, in the new channel."""
    with Served(amp=True) as served:
        page = Page.__new__(Page)
        page.page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.errors = []
        p = page.page
        p.on("pageerror", lambda error: page.errors.append(str(error)))
        said = {"used_by": ["board"]}
        p.route("**/machine/roles", lambda route: route.fulfill(json=roles_answer(said["used_by"])))
        p.goto(served.url + "/#rack")
        expect(p.locator(".amp-card .fader").first).to_be_visible()
        is_selected(p, "board")
        # The restart: a channel gtr2 on the guitar's input, Sushi not answering yet.
        recover = failing_live(p)
        served.rig.add_track("gtr2")
        said["used_by"] = ["gtr2"]
        from datetime import datetime, timezone
        served.lifecycle.started = datetime.now(timezone.utc).replace(microsecond=0)
        expect(p.get_by_role("tab")).to_have_count(0, timeout=5000)  # the run before's tabs are not this run's
        expect(p.locator(".amp-card")).to_be_visible()  # and with none, the card on its own
        time.sleep(2.5)  # past the hold's bound: nothing was decided against tabs that are not there
        recover()
        expect(p.get_by_role("tab")).to_have_text(["board", "vocal", "gtr2", "Master"], timeout=5000)
        expect(p.locator(".amp-card")).to_be_hidden()
        tab(p, "gtr2").click()
        expect(p.locator(".amp-card .fader").first).to_be_visible()
        page.close()


def test_another_rig_takes_no_input_from_the_rig_before_while_its_plugins_are_read(browser):
    """A switch to another rig whose plugins are slow to read: the chain of
    the rig switched from is not left on the page taking input, and a key
    pressed on its knob in that window reaches neither rig."""
    with Served() as served:
        page = rack_page(browser, served)
        p = page.page
        slider(p, "overdrive Drive").focus()
        served.rig.plugins_delay = 3.0
        writes = len(served.rig.writes())
        assert post(served, "/rig/switch", {"config_name": "accustomed"})["ok"]
        expect(p.locator(".rack .empty")).to_have_text("Reading…", timeout=4000)
        expect(p.get_by_role("slider", name="overdrive Drive")).to_have_count(0)
        for _ in range(3):
            p.keyboard.press("ArrowDown")
        time.sleep(0.5)
        assert served.rig.writes()[writes:] == [], "nothing sent, to the rig switched to or any other"
        # The plugins answered: the chain, drawn once.
        expect(shown(p)).to_have_text(["overdrive", "octave", "delay"], timeout=6000)
        expect(unit(p, "overdrive")).to_contain_text("GxTubeScreamer · profile")
        served.rig.plugins_delay = 0.0
        page.close()


def test_a_change_waiting_to_be_sent_is_dropped_when_the_rig_shown_changes(blank):
    """The Sender sends a control's newest value after the one in flight; a
    value waiting when the page comes to show another rig is not sent to it."""
    out = rigjs(blank, """
      let run = 'rig-a';
      const posted = [];
      let answer = null;
      const sender = new R.Sender((call) => { posted.push([run, call.json.value]); return new Promise((done) => { answer = done; }); },
        {run: () => run});
      const body = (x) => ({path: '/live/parameter', json: {processor: 'overdrive', parameter: 'Drive', value: x}});
      sender.change('k', 0.5, body);   // sent at once
      sender.change('k', 0.4, body);   // waits for the first's answer
      run = 'rig-b';                   // the page now shows another rig
      answer({ok: true});
      await new Promise((done) => setTimeout(done, 20));
      const after = [...posted];
      sender.change('k', 0.3, body);   // a change made on the rig now shown is sent
      await new Promise((done) => setTimeout(done, 20));
      return [after, posted, sender.busy('k')];""")
    assert out[0] == [["rig-a", 0.5]], "the waiting 0.4 was made on rig-a, and is not sent once rig-b is shown"
    assert out[1] == [["rig-a", 0.5], ["rig-b", 0.3]]


def test_the_amps_tab_is_kept_for_the_run_when_the_rack_is_left_and_come_back_to(browser):
    """Leaving the rack (for the stage, the New rig screen) and coming back
    makes the rack afresh; the amp's tab, decided for the run, is not decided
    again from a file that has changed since (Change input)."""
    with Served(amp=True) as served:
        page = Page.__new__(Page)
        page.page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.errors = []
        p = page.page
        p.on("pageerror", lambda error: page.errors.append(str(error)))
        unread = {**roles_answer([]), "wiring_note": "the running rig's compiled config could not be read (test)"}
        p.route("**/machine/roles", lambda route: route.fulfill(json=unread))  # the running rig cannot say: the file decides
        file = {"guitar": "vocal"}
        p.route("**/rigs/*/channels", lambda route: route.fulfill(json={
            "ok": True, "rig": "electric_board", "file": "config/rigs/electric_board.yaml", "blended": [], "blended_note": None,
            "channels": [{"name": name, "input": "guitar-in" if name == file["guitar"] else "vocal-in", "width": "mono",
                          "level": 0.0, "plugins": []} for name in ("board", "vocal")]}))
        p.goto(served.url + "/#rack")
        expect(p.locator(".unit").first).to_be_visible()
        tab(p, "vocal").click()
        expect(p.locator(".amp-card .fader").first).to_be_visible()
        # The file is edited, and the page reads it again (the New rig screen reads every rig's).
        file["guitar"] = "board"
        p.evaluate("() => { location.hash = '#new-rig'; }")
        expect(p.locator("form.new-rig-form")).to_be_visible(timeout=5000)
        time.sleep(0.5)
        p.evaluate("() => { location.hash = '#rack'; }")
        expect(p.locator(".unit").first).to_be_visible()
        tab(p, "vocal").click()
        expect(p.locator(".amp-card .fader").first).to_be_visible()
        tab(p, "board").click()
        expect(p.locator(".amp-card")).to_be_hidden()
        page.close()


def test_a_knob_focused_before_its_profile_arrives_keeps_the_focus_and_turns(browser, served):
    """CI on #135 (3.13): the profiles came after a test had focused a knob;
    its row was rebuilt with them, the focus fell to the page's body, and the
    key turned nothing, so the edit that should have been refused for unsaved
    work went through."""
    page = Page.__new__(Page)
    page.page = browser.new_page(viewport={"width": 1280, "height": 900})
    page.errors = []
    p = page.page
    p.on("pageerror", lambda error: page.errors.append(str(error)))
    held = []
    p.route("**/profiles", lambda route: held.append(route))
    p.goto(served.url + "/#rack")
    p.wait_for_selector(".unit")
    drive = slider(p, "overdrive Drive")
    drive.focus()
    before = drive.element_handle()
    expect(unit(p, "overdrive")).to_contain_text("no profile")
    for route in held:
        route.continue_()
    expect(unit(p, "overdrive")).to_contain_text("GxTubeScreamer · profile")
    assert before.evaluate("(e) => !e.isConnected"), "the row was rebuilt, as it must be for its profile's controls"
    expect(slider(p, "overdrive Drive")).to_be_focused()
    p.keyboard.press("ArrowDown")
    until(lambda: abs(served.rig.info["overdrive"]["Drive"]["value"] - 0.61) < 1e-9)
    page.close()


# --------------------------------------------------------------------------- #
# Re-review of a689de9: a log trimmed or unread is no switch; a rebuilt row
# gives the focus back to the same control, whatever its label
# --------------------------------------------------------------------------- #

def test_a_live_switch_is_counted_only_on_a_rig_entry_newer_than_any_seen(blank):
    out = rigjs(blank, """
      const log = (first, ops) => ({first, log: ops.map((op) => ({op}))});
      const steps = [
        [null, log(0, ['parameter', 'rig', 'parameter'])],     // the first log seen: a baseline, no switch
        ['same', log(0, ['parameter', 'rig', 'parameter', 'bypass'])],
        ['trimmed past the rig entry', log(600, ['parameter', 'parameter'])],
        ['not read', null],
        ['a switch', log(600, ['parameter', 'parameter', 'rig'])],
        ['the same switch, seen again', log(601, ['parameter', 'rig', 'parameter'])],
        ['the agent restarted, a switch its first entry', log(0, ['rig'])],
        ['a switch after that', log(0, ['rig', 'parameter', 'rig'])],
      ];
      let seen = null;
      return steps.map(([what, l]) => { seen = R.switchesSeen(seen, l); return [what, seen && seen.count]; });""")
    assert out == [[None, 0], ["same", 0], ["trimmed past the rig entry", 0], ["not read", 0], ["a switch", 1],
                   ["the same switch, seen again", 1], ["the agent restarted, a switch its first entry", 2],
                   ["a switch after that", 3]]
    # A log never read (the first poll's failed) counts nothing either.
    assert rigjs(blank, "return R.switchesSeen(null, null);") is None


def held_drag_across(p, served, between):
    """A drag on the delay's Feedback, with `between()` done in the middle
    and a poll or two after it: the dial must stay, and the drag land."""
    dial = slider(p, "delay Feedback")
    box = dial.locator("svg").bounding_box()
    x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    handle = dial.element_handle()
    calls = sum(1 for c in served.rig.calls if c[0] == "plugins")
    p.mouse.move(x, y)
    p.mouse.down()
    p.mouse.move(x, y - 32, steps=4)
    between()
    held = []
    for _ in range(25):  # 2.5 s: two polls and a load tile, watched for the hold
        held.append(p.locator(".rack .empty").count())
        time.sleep(0.1)
    assert held == [0] * 25, "the chain was never held back"
    assert handle.evaluate("(e) => e.isConnected && !!e.dataset.dragging"), "the dial, still held"
    p.mouse.move(x, y - 64, steps=4)
    p.mouse.up()
    until(lambda: abs(served.rig.info["delay"]["Feedback"]["value"] - 0.55) < 1e-6)
    assert sum(1 for c in served.rig.calls if c[0] == "plugins") == calls, "the plugins were not read again"


def after_a_live_switch(browser, served):
    """The rack, after a live switch has put an `op: "rig"` entry in the log."""
    assert post(served, "/rig/switch", {"config_name": "accustomed"})["ok"]
    page = rack_page(browser, served)
    expect(page.page.locator(".rig-name")).to_have_text("accustomed")
    return page


def test_a_log_trimmed_past_the_rig_switch_is_no_switch(browser, served):
    """The log keeps its last 500 entries, so knob turns trim the rig
    switch's entry away: that is not another rig."""
    page = after_a_live_switch(browser, served)
    p = page.page
    trim = {"on": False}

    def log(route):
        if not trim["on"]:
            route.continue_()
            return
        body = route.fetch().json()
        last = max(i for i, e in enumerate(body["log"]) if e["op"] == "rig")
        route.fulfill(json={**body, "first": (body.get("first") or 0) + last + 1, "log": body["log"][last + 1:]})

    p.route("**/live/log", log)
    held_drag_across(p, served, lambda: trim.update(on=True))
    page.close()


def test_one_unread_log_is_no_switch(browser, served):
    page = after_a_live_switch(browser, served)
    p = page.page
    fail = {"left": 0}

    def log(route):
        if fail["left"]:
            fail["left"] -= 1
            route.fulfill(status=500, json={"ok": False, "reason": "the log could not be read (test)"})
        else:
            route.continue_()

    p.route("**/live/log", log)
    held_drag_across(p, served, lambda: fail.update(left=1))
    page.close()


ROW_CONTROLS = [
    ("overdrive", "the dial", lambda p: slider(p, "overdrive Drive")),
    ("overdrive", "a value field", lambda p: p.get_by_role("textbox", name="overdrive Drive value", exact=True)),
    ("overdrive", "the switch", lambda p: unit(p, "overdrive").locator("button.bypass")),
    ("overdrive", "a move button", lambda p: unit(p, "overdrive").locator('button.step[data-focus="down"]')),
    ("overdrive", "the name", lambda p: name_of(p, "overdrive")),
    ("slim", "the switch", lambda p: unit(p, "overdrive").locator("button.bypass")),
    ("slim", "the name", lambda p: name_of(p, "overdrive")),
]


@pytest.mark.parametrize("row, what, find", ROW_CONTROLS, ids=[f"{r} row, {w}" for r, w, _ in ROW_CONTROLS])
def test_a_row_rebuilt_gives_the_focus_back_to_the_same_control(browser, served, row, what, find):
    """Each kind of control a row has, in a row that is on and a slim one:
    its profile arrives after it has the focus, the row is rebuilt, and the
    focus is on the same control of the new row, found by what it is, not by
    a label that an updater writes after (the switch's) or that its state
    changes ("Bypass X", "Turn on X")."""
    page = Page.__new__(Page)
    page.page = browser.new_page(viewport={"width": 1280, "height": 900})
    page.errors = []
    p = page.page
    p.on("pageerror", lambda error: page.errors.append(str(error)))
    held = []
    p.route("**/profiles", lambda route: held.append(route))
    p.goto(served.url + "/#rack")
    p.wait_for_selector(".unit")
    expect(unit(p, "overdrive")).to_contain_text("no profile")
    if row == "slim":
        pedal(served, "overdrive", True)
        expect(unit(p, "overdrive")).to_have_class(MINIMISED)
    control = find(p)
    control.focus()
    before = control.element_handle()
    key = before.evaluate("(e) => e.dataset.focus")
    key = key or before.evaluate("(e) => e.getAttribute('aria-label')")  # PROBE
    for route in held:
        route.continue_()
    expect(unit(p, "overdrive")).to_contain_text("GxTubeScreamer · profile")
    assert before.evaluate("(e) => !e.isConnected"), "the row was rebuilt"
    assert p.evaluate("() => document.activeElement.dataset.focus") == key
    assert p.evaluate("() => document.activeElement.closest('.unit').dataset.unit") == "overdrive"
    page.close()


def test_the_first_chain_is_drawn_with_its_profiles_so_its_rows_are_final(browser, served):
    """CI on #135 (3.11 and 3.13, a different test each time): a test took
    hold of a knob as soon as the first row was drawn, the profiles came a
    moment later and every row was drawn again, and the knob it held was
    gone. The first chain now waits for the profiles' first answer."""
    page = Page.__new__(Page)
    page.page = browser.new_page(viewport={"width": 1280, "height": 900})
    page.errors = []
    p = page.page
    p.on("pageerror", lambda error: page.errors.append(str(error)))
    release = hold(p, "**/profiles")
    p.goto(served.url + "/#rack")
    expect(p.get_by_role("tab")).to_have_text(["board", "vocal", "Master"])
    time.sleep(0.8)
    assert p.locator(".unit").count() == 0, "no row drawn before the profiles have answered"
    release()
    p.wait_for_selector(".unit")
    dial = slider(p, "overdrive Drive").element_handle()
    expect(unit(p, "overdrive")).to_contain_text("GxTubeScreamer · profile")
    time.sleep(2.2)  # polls, the load tiles, the names
    assert dial.evaluate("(e) => e.isConnected"), "the row first drawn is the row that stays"
    assert slider(p, "overdrive Drive").locator("svg").bounding_box() is not None
    page.close()


def test_the_plugins_names_arriving_late_change_a_rows_text_and_nothing_else(browser):
    """The plugin's name under a unit's is the library's, which may answer
    after the rows are drawn: it is written in place, not by a rebuild."""
    from ui_fake import fixture_library

    with Served(library=fixture_library) as served:
        page = Page.__new__(Page)
        page.page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.errors = []
        p = page.page
        p.on("pageerror", lambda error: page.errors.append(str(error)))
        release = hold(p, "**/library")
        p.goto(served.url + "/#rack")
        p.wait_for_selector(".unit")
        sub = unit(p, "delay").locator(".sub")
        before = sub.inner_text()
        dial = slider(p, "delay Feedback").element_handle()
        release()
        expect(sub).not_to_have_text(before, timeout=5000)
        assert dial.evaluate("(e) => e.isConnected"), "the row was not rebuilt for its text"
        page.close()



def test_a_switch_in_a_restarted_agents_log_is_counted_however_its_entries_are_numbered(blank):
    """An agent started again numbers its log from 0 and keeps Sushi
    running (the rig's start, from the state file, is the same). Its start
    (/health's agent.started_at) says it is another agent: any rig entry in
    its log happened since the page last saw the one before."""
    out = rigjs(blank, """
      const log = (first, ops) => ({first, log: ops.map((op) => ({op}))});
      const many = (n, at) => [...Array(n)].map((_, i) => (i === at ? 'rig' : 'parameter'));
      const seen40 = R.switchesSeen(null, log(0, many(41, -1)), 'agent-1');
      return {
        // The log unread while the agent was down: nothing changes, its start included.
        down: R.switchesSeen(seen40, null, 'agent-2'),
        // Back, with a switch as its first entry, below the 40 seen.
        first: R.switchesSeen(R.switchesSeen(seen40, null, 'agent-2'), log(0, ['rig']), 'agent-2').count,
        // Back with more entries than were seen, the switch among the first 40.
        past: R.switchesSeen(seen40, log(0, many(60, 10)), 'agent-2').count,
        // A new agent with no switch in its log: none.
        none: R.switchesSeen(seen40, log(0, many(5, -1)), 'agent-2').count,
        // The same agent, the same 60: the switch at 10 is not newer than the 40 seen... and is counted once.
        again: R.switchesSeen(R.switchesSeen(seen40, log(0, many(60, 10)), 'agent-2'), log(0, many(61, 10)), 'agent-2').count,
      };""")
    assert out["down"]["agent"] == "agent-1" and out["down"]["count"] == 0, "an unread log keeps what was seen"
    assert out["first"] == 1
    assert out["past"] == 1
    assert out["none"] == 0
    assert out["again"] == 1


def test_a_switch_after_the_agent_restarted_is_read_as_another_rig(browser, served):
    """Through the page: /health names another agent start, and its log has a
    switch as its first entry, numbered below what the page had seen. The
    page reads the plugins again, as for any other rig."""
    for i in range(41):  # a log of 41 entries seen first: the new agent's entry 0 is below them
        assert post(served, "/live/parameter", {"processor": "delay", "parameter": "Feedback", "value": 0.3 + i / 1000})["ok"]
    page = rack_page(browser, served)
    p = page.page
    restarted = {"on": False}

    def health(route):
        body = route.fetch().json()
        if restarted["on"]:
            body["agent"]["started_at"] = "2099-01-01T00:00:00+00:00"
        route.fulfill(json=body)

    def log(route):
        if not restarted["on"]:
            route.continue_()
            return
        route.fulfill(json={"ok": True, "first": 0, "log": [{"op": "rig", "args": {"rig": "electric_board", "how": "live"},
                                                             "previous": "electric_board", "at": "2099-01-01T00:00:01+00:00"}]})

    p.route("**/health", health)
    p.route("**/live/log", log)
    # Waited for with the page's own clock: a route that fetches is served
    # only while this thread is in a Playwright call, which time.sleep is not.
    p.wait_for_timeout(1200)
    calls = sum(1 for c in served.rig.calls if c[0] == "plugins")
    restarted["on"] = True
    for _ in range(50):
        if sum(1 for c in served.rig.calls if c[0] == "plugins") > calls:
            break
        p.wait_for_timeout(100)
    assert sum(1 for c in served.rig.calls if c[0] == "plugins") > calls, "the plugins were read again"
    expect(shown(p)).to_have_text(["overdrive", "octave", "delay"])
    page.close()


def test_a_profile_arriving_under_a_drag_waits_for_the_drag_to_end(browser, served):
    """Profiles later than the first draw's wait rebuild a row with its
    profile's controls; one with a knob being dragged is left as it is until
    the drag ends, so the drag lands, and then the row takes its profile."""
    page = Page.__new__(Page)
    page.page = browser.new_page(viewport={"width": 1280, "height": 900})
    page.errors = []
    p = page.page
    p.on("pageerror", lambda error: page.errors.append(str(error)))
    release = hold(p, "**/profiles")
    p.goto(served.url + "/#rack")
    expect(unit(p, "overdrive")).to_contain_text("no profile", timeout=5000)  # past the wait, plain
    dial = slider(p, "overdrive Drive")
    box = dial.locator("svg").bounding_box()
    x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    handle = dial.element_handle()
    p.mouse.move(x, y)
    p.mouse.down()
    p.mouse.move(x, y - 32, steps=4)
    release()
    time.sleep(1.5)  # the profiles in, a poll
    assert handle.evaluate("(e) => e.isConnected && !!e.dataset.dragging"), "the dragged row was left as it was"
    expect(unit(p, "overdrive")).to_contain_text("no profile")
    p.mouse.move(x, y - 64, steps=4)
    p.mouse.up()
    until(lambda: abs(served.rig.info["overdrive"]["Drive"]["value"] - 0.82) < 1e-6)
    expect(unit(p, "overdrive")).to_contain_text("GxTubeScreamer · profile")
    expect(slider(p, "overdrive Drive")).to_have_attribute("aria-valuetext", "0.82")
    page.close()
