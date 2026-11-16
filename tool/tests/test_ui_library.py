"""The Library drawer (#130): adding a plugin to a chain, taking one out and
moving one, from the rack, through the agent's live routes.

Two layers, as test_ui.py has them: rig.js's decisions imported into a bare
page, then the rack driven in Chromium against a real agent over the fake rig
of `ui_fake.py` (which says what in it is observed and what invented), with
the library the agent builds from the repository's manifest and port table
over the captured bundles in `fixtures/lv2` (`ui_fake.fixture_library`).

What stands in for what. "Another device" is a request made from outside any
page (`post`), which is all a page can see of one. An add "in flight" is the
fake's add held on an event until the test lets it go. Nothing here is a real
Sushi, a phone or a touch screen: a tap is a click, and "a phone" is a
viewport 390 px wide.
"""

from __future__ import annotations

import copy
import re
import threading

import pytest

pytest.importorskip("aiohttp")

from test_ui import Page, blank, browser, expect, post, rigjs, served, slider, unsaved_count, until  # noqa: E402,F401
from test_ui_tabs import settled, shown, tab, unit  # noqa: E402
from ui_fake import GXTS9, MDA_DELAY, Served, UiRig, fixture_library  # noqa: E402

MVERB = "http://distrho.sf.net/plugins/MVerb"
DARC_MONO = "http://gareus.org/oss/lv2/darc#mono"
CHORUS = "http://guitarix.sourceforge.net/plugins/gx_chorus_stereo#_chorus_stereo"
NAM = "http://github.com/mikeoliphant/neural-amp-modeler-lv2"


def library_page(browser, served, width=1280, height=900):
    page = Page(browser, served.url + "/#rack", width=width, height=height)
    page.page.wait_for_selector(".unit")
    settled(page.page)
    return page


def unlock(p):
    lock = p.locator("button.lock")
    lock.click()
    lock.click()
    expect(lock).to_have_text("Lock off · editing")


def slot(p, label):
    return p.get_by_role("button", name=label, exact=True)


def drawer(p):
    return p.locator(".drawer-sheet")


def entry(p, uri):
    return p.locator(f'.lib-entry[data-uri="{uri}"]')


def adds(p):
    """The bodies of every POST /live/add the page makes, as it makes them."""
    sent = []
    p.on("request", lambda r: sent.append(r.post_data_json) if r.method == "POST" and r.url.endswith("/live/add") else None)
    return sent


def mark_rows(p):
    """Mark every row now on the page: a row rebuilt or put back loses it."""
    p.evaluate("() => document.querySelectorAll('.unit').forEach((e) => { e.dataset.seen = e.dataset.unit; e.__kept = true; })")


def rows_kept(p):
    return p.evaluate("() => [...document.querySelectorAll('.unit')].filter((e) => e.__kept).map((e) => e.dataset.unit)")


# --------------------------------------------------------------------------- #
# The decisions, pure (rig.js)
# --------------------------------------------------------------------------- #

LIBRARY = """
  const L = {kinds: [{id: 'drive', label: 'Drive'}, {id: 'mod', label: 'Mod'}, {id: 'delay', label: 'Delay'},
                     {id: 'reverb', label: 'Reverb'}, {id: 'dynamics', label: 'Dynamics'}, {id: 'eq', label: 'EQ'}],
    plugins: [
      {uri: 'u:ts', name: 'GxTubeScreamer', bundle: 'gxts9.lv2', kind: 'drive', audio: {in: 1, out: 1}, addable: true},
      {uri: 'u:dly', name: 'MDA Delay', bundle: 'mda.lv2', kind: 'delay', audio: {in: 2, out: 2}, addable: true},
      {uri: 'u:dub', name: 'MDA DubDelay', bundle: 'mda.lv2', kind: 'delay', audio: {in: 2, out: 2}, addable: true},
      {uri: 'u:oct', name: 'Rubber Band Mono Pitch Shifter', bundle: 'rubberband.lv2', kind: null, audio: {in: 1, out: 1}, addable: true},
      {uri: 'u:amp', name: 'neural-amp-modeler-lv2', bundle: null, kind: null, audio: null, addable: false, why_not: 'the amp runs in Carla'},
      {uri: 'u:odd', name: 'Odd', bundle: 'odd.lv2', kind: 'not-a-kind', audio: {in: 2, out: 2}, addable: true},
      {uri: 'u:comp', name: 'a compressor', bundle: 'darc.lv2', kind: 'dynamics', audio: {in: 2, out: 2}, addable: true},
    ]};
"""

MODEL = """
  const model = (tracks, plugins = null) => R.rackModel({tracks, processors: {}, parameter_info: {}}, plugins, null);
  const P = (uri, width) => ({uri, uid: null, type: 'lv2', role: null, width});
"""


def test_the_drawer_groups_by_the_plugins_own_kind_in_its_order_then_other(blank):
    out = rigjs(blank, LIBRARY + """
      return R.libraryGroups(L).map((g) => [g.label, g.entries.map((e) => e.name)]);""")
    assert out == [
        ["Drive", ["GxTubeScreamer"]],
        ["Delay", ["MDA Delay", "MDA DubDelay"]],
        ["Dynamics", ["a compressor"]],
        ["Other", ["neural-amp-modeler-lv2", "Odd", "Rubber Band Mono Pitch Shifter"]],
    ], "the library's order of kinds, empty ones left out, by name within each; no kind or one not known is Other"


def test_the_search_wants_every_word_in_the_name_the_bundle_or_the_kind(blank):
    out = rigjs(blank, LIBRARY + """
      const groups = R.libraryGroups(L);
      const find = (q) => groups.flatMap((g) => g.entries.filter((e) => R.libraryMatch(e, g.label, q)).map((e) => e.name));
      return [find(''), find('delay'), find('DUB'), find('mda delay'), find('dynamics'), find('darc'), find('other pitch'),
              find('delay  reverb'), find('  ')];""")
    everything = ["GxTubeScreamer", "MDA Delay", "MDA DubDelay", "a compressor", "neural-amp-modeler-lv2", "Odd",
                  "Rubber Band Mono Pitch Shifter"]
    assert out == [everything, ["MDA Delay", "MDA DubDelay"], ["MDA DubDelay"], ["MDA Delay", "MDA DubDelay"],
                   ["a compressor"], ["a compressor"], ["Rubber Band Mono Pitch Shifter"], [], everything]


def test_an_entry_shows_its_bundle_its_mono_badge_and_why_it_cannot_be_added(blank):
    out = rigjs(blank, LIBRARY + "return [L.plugins[0], L.plugins[1], L.plugins[4]].map(R.libraryEntry);")
    assert [(e["name"], e["bundle"], e["mono"], e["width"], e["addable"], e["why"]) for e in out] == [
        ("GxTubeScreamer", "gxts9.lv2", True, "mono", True, None),
        ("MDA Delay", "mda.lv2", False, "stereo", True, None),
        ("neural-amp-modeler-lv2", "not in plugins/", False, None, False, "the amp runs in Carla"),
    ]
    assert rigjs(blank, "return R.MONO_TITLE;") == "mono: processes the left side and its output is copied to both"


def test_each_place_sends_after_the_unit_above_it_and_the_start_sends_first(blank):
    out = rigjs(blank, MODEL + LIBRARY + """
      const m = model({board: ['od', 'od_to_stereo', 'dly', 'board_send'], master: ['master_return', 'lim']});
      const [board, master] = m.tracks;
      const calls = (t) => R.addSlots(t).map((s) => [s.label, s.index, R.addCall(t.name, s, L.plugins[1], null).json]);
      return {board: calls(board), master: calls(master), empty: R.addSlots(model({aux: ['aux_send']}).tracks[0]).map((s) => s.label),
              named: R.addCall('board', R.addSlots(board)[1], L.plugins[1], 'mda_delay_2').json, none: R.addSlots(null)};""")
    assert out["board"] == [
        ["Add at the start…", 0, {"track": "board", "uri": "u:dly", "first": True}],
        ["Add after od…", 1, {"track": "board", "uri": "u:dly", "after": "od"}],
        ["Add after dly…", 2, {"track": "board", "uri": "u:dly", "after": "dly"}],
    ], "after the plugin shown, never its copy or the send"
    assert out["master"] == [
        ["Add at the start…", 0, {"track": "master", "uri": "u:dly", "first": True}],
        ["Add after lim…", 1, {"track": "master", "uri": "u:dly", "after": "lim"}],
    ], "the return is not a place to add after: the agent keeps it first"
    assert out["empty"] == ["Add at the start…"]
    assert out["named"] == {"track": "board", "uri": "u:dly", "name": "mda_delay_2", "after": "od"}
    assert out["none"] == []


def test_a_name_is_the_agents_own_when_free_and_numbered_when_not(blank):
    out = rigjs(blank, LIBRARY + """
      const live = (tracks) => ({tracks});
      const [ts, dly] = [L.plugins[0], L.plugins[1]];
      return [
        R.addName(dly, live({board: ['od', 'board_send']})),
        R.addName(dly, live({board: ['mda_delay', 'board_send'], vocal: ['mda_delay_2', 'vocal_send']})),
        R.addName(ts, live({board: ['gxtubescreamer_to_stereo', 'board_send']})),
        R.addName({...dly, name: 'board'}, live({board: ['board_send']})),
        R.addName({...dly, name: '!!!'}, live({board: ['board_send']})),
        R.addName({...dly, name: 'x'.repeat(80)}, live({board: ['x'.repeat(64), 'board_send']})),
        R.addName({...dly, name: 'Fold to stereo'}, live({})),
        R.deriveName('L/C/R Delay'), R.deriveName('  --Ünïcode--  '), R.deriveName('a'.repeat(63) + ' b'),
      ];""")
    assert out[0] == {"name": None, "shown": "mda_delay"}, "free: the agent names it, as it would"
    assert out[1] == {"name": "mda_delay_3", "shown": "mda_delay_3"}, "free across the whole rig, not the track alone"
    assert out[2] == {"name": "gxtubescreamer_2", "shown": "gxtubescreamer_2"}, "a mono plugin's copy needs its name free too"
    assert out[3] == {"name": "board_2", "shown": "board_2"}, "a track's name is taken"
    assert out[4] == {"name": "plugin", "shown": "plugin"}, "nothing to derive: a name the agent will take"
    assert out[5] == {"name": "x" * 62 + "_2", "shown": "x" * 62 + "_2"}, "never past 64 characters"
    assert out[6] == {"name": "fold", "shown": "fold"}, "the compiler's suffix is not the player's to use: sent, since the agent would refuse it"
    # agent.derive_name's own cases (test_agent_edits.py), the same here.
    assert out[7:] == ["l_c_r_delay", "n_code", "a" * 63]


def test_the_name_rule_never_gives_a_name_the_agent_refuses(blank):
    out = rigjs(blank, LIBRARY + """
      const re = new RegExp(R.NAME_PATTERN);
      const live = {tracks: {board: ['to_stereo', 'board_send']}};
      return ['To Stereo', 'x_to_stereo', '', '_', '9 Lives', 'a'.repeat(70)].map((n) => R.addName({...L.plugins[0], name: n}, live))
        .map((a) => [a.shown, re.test(a.shown) && !a.shown.endsWith('_to_stereo')]);""")
    assert all(ok for _, ok in out), out


def test_the_collapse_is_said_before_a_mono_plugin_goes_after_a_stereo_one(blank):
    out = rigjs(blank, MODEL + LIBRARY + """
      const plugins = {verb: P('u:v', 'stereo'), od: P('u:o', 'mono'), od_to_stereo: {role: 'stereo'}, eq: P('u:e', 'stereo')};
      const t = model({board: ['verb', 'od', 'od_to_stereo', 'eq', 'board_send']}, plugins).tracks[0];
      const [start, afterVerb, afterOd, afterEq] = R.addSlots(t);
      const [ts, dly] = [L.plugins[0], L.plugins[1]];
      const w = (slot, e, track = t) => R.addWarning(track, slot, e, 'new');
      const lone = model({board: ['od', 'od_to_stereo', 'eq', 'board_send']}, plugins).tracks[0];
      return [w(afterVerb, ts), w(afterEq, ts), w(afterOd, ts), w(start, ts), w(afterVerb, dly),
              w(R.addSlots(lone)[0], dly, lone), w(afterOd, dly), w(afterVerb, L.plugins[4]), w(start, dly)];""")
    assert out[0] == {"warn": True, "text": "after verb: this collapses its stereo width"}, "the rack's own words"
    assert out[1] == {"warn": True, "text": "after eq: this collapses its stereo width"}
    assert out[2] is None, "after a mono plugin, nothing stereo is left to collapse"
    assert out[3] is None, "first, nothing comes before it"
    assert out[4] is None, "a stereo plugin after a stereo one"
    assert out[5] == {"warn": True, "text": "od comes after it and collapses its stereo width"}, \
        "a stereo plugin put in front of a mono one has its width collapsed there"
    assert out[6] is None, "the od already follows verb's width: nothing new is collapsed"
    assert out[7] is None, "width not known (not in the ports table): not guessed"
    assert out[8] is None, "in front of verb, which the od already follows: nothing new collapsed"


def test_adding_is_off_with_the_reason_and_browsing_never_is(blank):
    out = rigjs(blank, MODEL + """
      const up = {ok: true, body: {rig: {running: true}, agent: {}}};
      const t = model({board: ['od', 'board_send']}).tracks[0];
      const [start, afterOd] = R.addSlots(t);
      const gone = {...afterOd, after: 'dly'};
      return [
        R.addBlocked({health: up, track: t, slot: afterOd, locked: false}),
        R.addBlocked({health: up, track: t, slot: start, locked: true}),
        R.addBlocked({health: {ok: true, body: {rig: {running: false}, agent: {}}}, track: null, slot: null, locked: false}),
        R.addBlocked({health: {ok: true, body: {rig: {running: true}, agent: {busy: 'switch'}}}, track: t, slot: start, locked: false}),
        R.addBlocked({health: {ok: false}, track: t, slot: start, locked: false}),
        R.addBlocked({health: up, track: null, slot: start, locked: false}),
        R.addBlocked({health: up, track: t, slot: gone, locked: false}),
      ];""")
    assert out[0] is None
    assert out[1].startswith("Locked: adding a plugin changes the rig's structure")
    assert out[2] == "No rig is running, so nothing can be added: start one under RIGS."
    assert out[3] == "switch is running: adding waits until it is done."
    assert "not answering" in out[4]
    assert "not on the rig now" in out[5]
    assert out[6] == "dly is no longer on board: close this and choose another place."


def test_only_a_players_plugin_is_offered_a_remove(blank):
    out = rigjs(blank, MODEL + """
      const m = model({board: ['od', 'od_to_stereo', 'board_send'], master: ['master_return', 'lim']});
      const all = m.tracks.flatMap((t) => [...t.units, t.send, t.ret].filter(Boolean));
      return [all.map((u) => [u.name, R.removable(u)]), R.removeCall('od')];""")
    assert out[0] == [["od", True], ["board_send", False], ["lim", True], ["master_return", False]]
    assert out[1] == {"path": "/live/remove", "json": {"processor": "od"}}


# --------------------------------------------------------------------------- #
# The drawer, in the browser
# --------------------------------------------------------------------------- #

@pytest.fixture
def lib():
    with Served(library=fixture_library) as served:
        yield served


def test_the_drawer_lists_the_library_by_kind_and_the_search_narrows_it_in_place(browser, lib):
    page = library_page(browser, lib)
    p = page.page
    slot(p, "Add after overdrive…").click()
    expect(drawer(p)).to_be_visible()
    expect(drawer(p).locator(".drawer-where")).to_have_text("Add after overdrive on board")
    expect(drawer(p).locator(".lib-group h3")).to_have_text(["Drive", "Mod", "Delay", "Reverb", "Dynamics", "EQ", "Other"])
    expect(p.locator('.lib-group[data-kind="drive"] .lib-name')).to_have_text(["GxTubeScreamer"])
    expect(p.locator('.lib-group[data-kind="mod"] .lib-name')).to_have_text(["GxChorus-Stereo"])
    expect(p.locator('.lib-group[data-kind="delay"] .lib-name')).to_have_text(["MDA Delay"])
    expect(p.locator('.lib-group[data-kind="reverb"] .lib-name')).to_have_text(["MVerb"])
    expect(p.locator('.lib-group[data-kind="dynamics"] .lib-name')).to_have_text(
        ["x42-comp - Dynamic Compressor Mono", "x42-comp - Dynamic Compressor Stereo"])
    expect(p.locator('.lib-group[data-kind="eq"] .lib-name')).to_have_text(["3 Band EQ"])
    # Every other plugin of the manifest: no kind its bundle declares, or no bundle read.
    expect(p.locator('.lib-group[data-kind="other"] .lib-entry')).to_have_count(55 - 7)
    # Its bundle, and the mono badge with the rack's own tooltip.
    ts = entry(p, GXTS9)
    expect(ts.locator(".lib-bundle")).to_have_text("gxts9.lv2")
    expect(ts.locator(".mono-badge")).to_have_attribute("title", "mono: processes the left side and its output is copied to both")
    expect(entry(p, MDA_DELAY).locator(".mono-badge")).to_have_count(0)
    expect(entry(p, "urn:ardour:a-reverb").locator(".lib-bundle")).to_have_text("not in plugins/")
    # The search has the focus as the drawer opens, and narrows in place.
    search = p.get_by_role("searchbox", name="Search the library")
    expect(search).to_be_focused()
    first = entry(p, MVERB).element_handle()
    p.keyboard.type("compressor")
    # The x42s by name and kind; guitarix's, whose bundle is not among the
    # captured ones, by its URI's last part, under Other.
    expect(p.locator(".lib-entry:visible .lib-name")).to_have_text(
        ["x42-comp - Dynamic Compressor Mono", "x42-comp - Dynamic Compressor Stereo", "_compressor"])
    expect(p.locator(".lib-group:visible h3")).to_have_text(["Dynamics", "Other"])
    search.fill("reverb")
    # MVerb by its kind, a-reverb by its name: its bundle is not captured, so
    # it is under Other, not called a reverb for its name.
    expect(p.locator(".lib-entry:visible .lib-name")).to_have_text(["MVerb", "a-reverb"])
    expect(p.locator(".lib-group:visible h3")).to_have_text(["Reverb", "Other"])
    search.fill("nothing like this")
    expect(p.locator(".lib-nothing")).to_be_visible()
    search.fill("")
    expect(p.locator(".lib-entry:visible")).to_have_count(55)
    assert first.evaluate("(e) => e.isConnected"), "the list was not rebuilt by the search"
    # Escape closes it and gives the focus back to the place it was opened from.
    p.keyboard.press("Escape")
    expect(drawer(p)).to_be_hidden()
    expect(slot(p, "Add after overdrive…")).to_be_focused()
    page.close()


def test_an_add_from_the_drawer_lands_in_its_place_and_no_other_row_is_rebuilt(browser, lib):
    page = library_page(browser, lib)
    p = page.page
    unlock(p)
    mark_rows(p)
    held = threading.Event()
    real_add = lib.rig.add

    def slow_add(connection, addition):
        held.wait(10)
        return real_add(connection, addition)

    lib.rig.add = slow_add
    slot(p, "Add after overdrive…").click()
    entry(p, MDA_DELAY).click()
    expect(drawer(p)).to_be_hidden()
    working = slot(p, "Adding mda_delay…")
    expect(working).to_have_attribute("aria-busy", "true")
    expect(working).to_be_focused()
    expect(shown(p)).to_have_text(["overdrive", "octave", "delay"]), "nothing is drawn before the agent confirms"
    held.set()
    expect(shown(p)).to_have_text(["overdrive", "mda_delay", "octave", "delay"])
    assert lib.rig.chains["board"] == ["overdrive", "mda_delay", "octave", "delay", "board_send"]
    expect(slot(p, "Add after overdrive…")).to_have_attribute("aria-busy", "false")
    assert rows_kept(p) == ["overdrive", "octave", "delay"], "every row that was there is the row that was there"
    # Drawn once, as what it is: its plugin known from the add, before /live/plugins says.
    expect(unit(p, "mda_delay")).to_contain_text("MDA Delay · no profile: every parameter")
    expect(unit(p, "mda_delay").get_by_role("slider")).to_have_count(6)
    # It has its own place to add after, and is live work: one unsaved change, and Undo names it.
    expect(slot(p, "Add after mda_delay…")).to_be_visible()
    unsaved_count(p, 1)
    expect(p.locator("aside.changes button.undo")).to_have_text("Undo · add mda_delay")
    page.close()


def test_each_place_sends_its_after_and_the_start_sends_first_through_the_page(browser, lib):
    page = library_page(browser, lib)
    p = page.page
    unlock(p)
    sent = adds(p)
    for label, uri, chain in (
        ("Add at the start…", MDA_DELAY, ["mda_delay", "overdrive", "octave", "delay", "board_send"]),
        ("Add after delay…", MVERB, ["mda_delay", "overdrive", "octave", "delay", "mverb", "board_send"]),
        ("Add after overdrive…", CHORUS, ["mda_delay", "overdrive", "gxchorus_stereo", "octave", "delay", "mverb", "board_send"]),
    ):
        slot(p, label).click()
        entry(p, uri).click()
        until(lambda chain=chain: lib.rig.chains["board"] == chain)
        expect(shown(p)).to_have_text([n for n in chain if n != "board_send"])
    tab(p, "Master").click()
    slot(p, "Add at the start…").click()
    entry(p, MDA_DELAY).click()
    until(lambda: lib.rig.chains["master"] == ["master_return", "mda_delay_2"])
    expect(p.locator("#t-master .unit button.name")).to_have_text(["mda_delay_2"])
    slot(p, "Add after mda_delay_2…").click()
    entry(p, MVERB).click()
    until(lambda: lib.rig.chains["master"] == ["master_return", "mda_delay_2", "mverb_2"])
    assert sent == [
        {"track": "board", "uri": MDA_DELAY, "first": True},
        {"track": "board", "uri": MVERB, "after": "delay"},
        {"track": "board", "uri": CHORUS, "after": "overdrive"},
        {"track": "master", "uri": MDA_DELAY, "name": "mda_delay_2", "first": True},
        {"track": "master", "uri": MVERB, "name": "mverb_2", "after": "mda_delay_2"},
    ], "a free name is left to the agent; a taken one is numbered, never refused"
    page.close()


def test_a_mono_plugin_comes_with_its_hidden_copy_and_its_remove_takes_both(browser, lib):
    page = library_page(browser, lib)
    p = page.page
    unlock(p)
    tab(p, "vocal").click()
    slot(p, "Add at the start…").click()
    entry(p, GXTS9).click()
    until(lambda: lib.rig.chains["vocal"] == ["gxtubescreamer", "gxtubescreamer_to_stereo", "vocal_delay", "vocal_send"])
    expect(p.locator("#t-vocal .unit button.name")).to_have_text(["gxtubescreamer", "vocal_delay"]), "its copy is not shown"
    expect(unit(p, "gxtubescreamer").locator(".mono-badge")).to_have_text("mono")
    expect(p.locator(".add-slot")).to_have_count(3), "no place to add after the copy: what follows it goes past it"
    unsaved_count(p, 1)
    unit(p, "gxtubescreamer").get_by_role("button", name="gxtubescreamer", exact=True).click()
    unit(p, "gxtubescreamer").get_by_role("button", name="Remove gxtubescreamer…").click()
    expect(unit(p, "gxtubescreamer").locator(".row-said")).to_have_text(
        "Take gxtubescreamer out of the chain? Undo puts it back with its settings.")
    assert lib.rig.chains["vocal"][0] == "gxtubescreamer", "one tap removes nothing"
    unit(p, "gxtubescreamer").get_by_role("button", name="Remove gxtubescreamer", exact=True).click()
    until(lambda: lib.rig.chains["vocal"] == ["vocal_delay", "vocal_send"])
    expect(p.locator("#t-vocal .unit button.name")).to_have_text(["vocal_delay"])
    unsaved_count(p, 0)  # added and taken out again is no change
    page.close()


def test_the_collapse_warning_is_shown_before_a_mono_plugin_follows_a_stereo_one(browser, lib):
    page = library_page(browser, lib)
    p = page.page
    unlock(p)
    # The delay is stereo (plugin-ports.txt: 2 out): a mono plugin after it collapses its width.
    slot(p, "Add after delay…").click()
    expect(entry(p, GXTS9).locator(".lib-note")).to_have_text("after delay: this collapses its stereo width")
    expect(entry(p, GXTS9).locator(".lib-note")).to_have_class(re.compile(r"\bwarn\b"))
    expect(entry(p, DARC_MONO).locator(".lib-note")).to_have_text("after delay: this collapses its stereo width")
    expect(entry(p, MDA_DELAY).locator(".lib-note")).to_have_text("")
    expect(entry(p, MVERB).locator(".lib-note")).to_have_text("")
    p.keyboard.press("Escape")
    # At the start nothing comes before it.
    slot(p, "Add at the start…").click()
    expect(entry(p, GXTS9).locator(".lib-note")).to_have_text("")
    p.keyboard.press("Escape")
    # And once it is added, the rack's badge says the same.
    slot(p, "Add after delay…").click()
    entry(p, GXTS9).click()
    until(lambda: lib.rig.chains["board"][3:5] == ["gxtubescreamer", "gxtubescreamer_to_stereo"])
    expect(unit(p, "gxtubescreamer").locator(".mono-badge")).to_have_attribute("title", "after delay: this collapses its stereo width")
    page.close()


def test_locked_the_drawer_opens_to_browse_and_adding_and_removing_are_refused_in_place(browser, lib):
    page = library_page(browser, lib)
    p = page.page
    expect(p.locator("button.lock")).to_have_text("Locked")
    slot(p, "Add after overdrive…").click()
    expect(drawer(p)).to_be_visible()
    expect(drawer(p).locator(".drawer-why")).to_contain_text("Locked: adding a plugin changes the rig's structure")
    expect(entry(p, MDA_DELAY)).to_be_disabled()
    expect(p.locator(".lib-entry:enabled")).to_have_count(0)
    p.get_by_role("button", name="Close the library").click()
    expect(drawer(p)).to_be_hidden()
    unit(p, "delay").get_by_role("button", name="delay", exact=True).click()
    unit(p, "delay").get_by_role("button", name="Remove delay… (locked)").click()
    expect(unit(p, "delay").locator(".row-said")).to_contain_text("Locked: removing a plugin changes the rig's structure")
    expect(unit(p, "delay").get_by_role("button", name="Remove delay", exact=True)).to_be_hidden()
    assert lib.rig.writes() == []
    page.close()


def test_with_no_rig_running_the_library_can_be_browsed_and_nothing_added(browser):
    with Served(library=fixture_library, describe=lambda: {"verdict": "absent", "running": False,
                                                           "message": "No rig is running."}) as served:
        page = Page(browser, served.url + "/#rack")
        p = page.page
        expect(p.locator(".rig-name")).to_have_text("Rig down")
        p.get_by_role("button", name="Browse the library…").click()
        expect(drawer(p).locator(".drawer-where")).to_have_text("Look through what can be added.")
        expect(drawer(p).locator(".drawer-why")).to_have_text("No rig is running, so nothing can be added: start one under RIGS.")
        expect(entry(p, MDA_DELAY)).to_be_visible()
        expect(p.locator(".lib-entry:enabled")).to_have_count(0)
        assert served.rig.writes() == []
        page.close()


def test_the_open_drawer_turns_its_adds_off_while_the_rig_restarts_and_stays_off_for_the_new_run(browser, lib):
    """While a command runs the agent's routes are busy: the drawer, open
    already, says so and lists everything, off. The rig that comes back is
    another run, and the drawer was opened for the one before: it says so,
    and adds nothing until it is opened again (review of #130). The entry
    that had the focus when it was turned off gave it to the search, and
    Escape still closes the drawer."""
    page = library_page(browser, lib)
    p = page.page
    unlock(p)
    slot(p, "Add after overdrive…").click()
    entry(p, MDA_DELAY).focus()
    lib.lifecycle.delay = 2.0
    restart = threading.Thread(target=post, args=(lib, "/rig/restart", {}))
    restart.start()
    try:
        expect(drawer(p).locator(".drawer-why")).to_have_text("restart is running: adding waits until it is done.")
        expect(p.locator(".lib-entry:enabled")).to_have_count(0)
        expect(p.get_by_role("searchbox", name="Search the library")).to_be_focused()
    finally:
        restart.join(10)
    expect(drawer(p).locator(".drawer-why")).to_have_text(
        "The rig changed since the library was opened (it is now electric_board): close this, and open it again from the chain.",
        timeout=8000)
    expect(p.locator(".lib-entry:enabled")).to_have_count(0)
    p.keyboard.press("Escape")
    expect(drawer(p)).to_be_hidden()
    expect(slot(p, "Add after overdrive…")).to_be_focused()
    slot(p, "Add after overdrive…").click()
    expect(entry(p, MDA_DELAY)).to_be_enabled()
    expect(drawer(p).locator(".drawer-why")).to_be_hidden()
    page.close()


def test_a_plugin_the_agent_refuses_says_its_reason_in_place_and_sushi_is_not_touched(browser):
    def library():
        built = copy.deepcopy(fixture_library())
        for e in built["plugins"]:
            if e["uri"] == MVERB:
                e["audio"] = None  # as if plugin-ports.txt did not list it
        return built

    with Served(library=library) as served:
        page = library_page(browser, served)
        p = page.page
        unlock(p)
        slot(p, "Add after delay…").click()
        # The amp is listed, off, with the library's reason; nothing to send.
        expect(entry(p, NAM)).to_be_disabled()
        expect(entry(p, NAM).locator(".lib-note")).to_contain_text("Cannot be added: the amp runs in Carla")
        entry(p, MVERB).click()
        said = p.locator('.add-slot[data-slot="delay"] .add-said')
        expect(said).to_contain_text("Not added: MVerb")
        expect(said).to_contain_text("is not in plugin-ports.txt")
        expect(said).to_have_attribute("role", "alert")
        assert served.rig.writes() == [] and not any(c[0] == "walk" for c in served.rig.calls), \
            "refused from the manifest, before Sushi is asked anything"
        expect(shown(p)).to_have_text(["overdrive", "octave", "delay"])
        p.locator('.add-slot[data-slot="delay"]').get_by_role("button", name="Dismiss").click()
        expect(said).to_be_hidden()
        page.close()


def test_undo_takes_an_add_out_with_its_copy_and_puts_a_remove_back_with_its_settings(browser, lib):
    page = library_page(browser, lib)
    p = page.page
    unlock(p)
    undo = p.locator("aside.changes button.undo")
    # An add, undone: the plugin and its copy go.
    slot(p, "Add after overdrive…").click()
    entry(p, DARC_MONO).click()
    name = "x42_comp_dynamic_compressor_mono"
    until(lambda: lib.rig.chains["board"][1:3] == [name, name + "_to_stereo"])
    expect(undo).to_have_text(f"Undo · add {name}")
    undo.click()
    until(lambda: lib.rig.chains["board"] == ["overdrive", "octave", "delay", "board_send"])
    expect(shown(p)).to_have_text(["overdrive", "octave", "delay"])
    unsaved_count(p, 0)
    # A remove, undone: back where it was, with the value it had, not its default.
    feedback = slider(p, "delay Feedback")
    feedback.focus()
    for _ in range(5):
        p.keyboard.press("ArrowUp")
    until(lambda: abs(lib.rig.info["delay"]["Feedback"]["value"] - 0.40) < 1e-6)
    unit(p, "delay").get_by_role("button", name="delay", exact=True).click()
    unit(p, "delay").get_by_role("button", name="Remove delay…").click()
    unit(p, "delay").get_by_role("button", name="Remove delay", exact=True).click()
    until(lambda: lib.rig.chains["board"] == ["overdrive", "octave", "board_send"])
    expect(shown(p)).to_have_text(["overdrive", "octave"])
    expect(undo).to_have_text("Undo · remove delay")
    undo.click()
    until(lambda: lib.rig.chains["board"] == ["overdrive", "octave", "delay", "board_send"])
    expect(shown(p)).to_have_text(["overdrive", "octave", "delay"])
    expect(slider(p, "delay Feedback")).to_have_attribute("aria-valuetext", "0.40")
    assert abs(lib.rig.info["delay"]["Feedback"]["value"] - 0.40) < 1e-6
    unsaved_count(p, 1)  # the Feedback turned, back with it
    page.close()


def test_moves_work_on_every_unit_shown_and_never_part_a_mono_plugin_from_its_copy(browser, lib):
    page = library_page(browser, lib)
    p = page.page
    unlock(p)
    slot(p, "Add after overdrive…").click()
    entry(p, GXTS9).click()
    pair = ["gxtubescreamer", "gxtubescreamer_to_stereo"]
    until(lambda: lib.rig.chains["board"] == ["overdrive", *pair, "octave", "delay", "board_send"])
    expect(shown(p)).to_have_text(["overdrive", "gxtubescreamer", "octave", "delay"])

    def board():
        return lib.rig.chains["board"]

    # The mono plugin down past the (slim, bypassed) octave, then the delay: its copy with it each time.
    p.get_by_role("button", name="Move gxtubescreamer down", exact=True).click()
    until(lambda: board() == ["overdrive", "octave", *pair, "delay", "board_send"])
    p.get_by_role("button", name="Move gxtubescreamer down", exact=True).click()
    until(lambda: board() == ["overdrive", "octave", "delay", *pair, "board_send"])
    expect(p.get_by_role("button", name="Move gxtubescreamer down", exact=True)).to_be_disabled()
    # Past it from the other side: the delay up past the pair, as one.
    p.get_by_role("button", name="Move delay down", exact=True).click()
    until(lambda: board() == ["overdrive", "octave", *pair, "delay", "board_send"])
    # The slim octave, opened, moves too; and the first unit up is not offered.
    unit(p, "octave").get_by_role("button", name="octave", exact=True).click()
    p.get_by_role("button", name="Move octave down", exact=True).click()
    until(lambda: board() == ["overdrive", *pair, "octave", "delay", "board_send"])
    expect(p.get_by_role("button", name="Move overdrive up", exact=True)).to_be_disabled()
    p.get_by_role("button", name="Move overdrive down", exact=True).click()
    until(lambda: board() == [*pair, "overdrive", "octave", "delay", "board_send"])
    expect(shown(p)).to_have_text(["gxtubescreamer", "overdrive", "octave", "delay"])
    # On Master too, where the return stays first.
    tab(p, "Master").click()
    slot(p, "Add at the start…").click()
    entry(p, MDA_DELAY).click()
    slot(p, "Add after mda_delay…").click()
    entry(p, MVERB).click()
    until(lambda: lib.rig.chains["master"] == ["master_return", "mda_delay", "mverb"])
    expect(p.get_by_role("button", name="Move mda_delay up", exact=True)).to_be_disabled()
    p.get_by_role("button", name="Move mverb up", exact=True).click()
    until(lambda: lib.rig.chains["master"] == ["master_return", "mverb", "mda_delay"])
    for chain in lib.rig.chains.values():
        for i, name in enumerate(chain):
            if name.endswith("_to_stereo"):
                assert chain[i - 1] == name[: -len("_to_stereo")], chain
    page.close()


def test_another_device_adding_and_removing_moves_nothing_in_the_open_drawer(browser, lib):
    page = library_page(browser, lib)
    p = page.page
    unlock(p)
    slot(p, "Add after delay…").click()
    search = p.get_by_role("searchbox", name="Search the library")
    p.keyboard.type("mda")
    list_el = p.locator(".drawer-list")
    list_el.evaluate("(e) => { e.scrollTop = 40; }")
    before = {
        "entries": p.evaluate("() => [...document.querySelectorAll('.lib-entry')].map((e) => { e.__kept = true; return e.dataset.uri; })"),
        "scroll": list_el.evaluate("(e) => e.scrollTop"),
        "box": drawer(p).bounding_box(),
    }
    handle = search.element_handle()
    # Above the place, and elsewhere on the chain, from another device.
    assert post(lib, "/live/add", {"track": "board", "uri": MDA_DELAY, "name": "slap", "after": "overdrive"})["ok"]
    assert post(lib, "/live/remove", {"processor": "octave"})["ok"]
    until(lambda: p.evaluate("() => [...document.querySelectorAll('.unit button.name')].map((e) => e.textContent).join(',')")
          == "overdrive,slap,delay")
    p.wait_for_timeout(300)  # a poll more, drawn
    assert handle.evaluate("(e) => e.isConnected && document.activeElement === e"), "the search keeps the focus"
    expect(search).to_have_value("mda")
    assert p.evaluate("() => [...document.querySelectorAll('.lib-entry')].every((e) => e.__kept)"), "no entry was rebuilt"
    assert list_el.evaluate("(e) => e.scrollTop") == before["scroll"]
    assert drawer(p).bounding_box() == before["box"]
    expect(drawer(p).locator(".drawer-where")).to_have_text("Add after delay on board")
    expect(entry(p, MDA_DELAY)).to_be_enabled()
    # The place it was opened for, taken away from elsewhere: said, and nothing can be added there.
    assert post(lib, "/live/remove", {"processor": "delay"})["ok"]
    expect(drawer(p).locator(".drawer-why")).to_have_text("delay is no longer on board: close this and choose another place.")
    expect(p.locator(".lib-entry:enabled")).to_have_count(0)
    assert handle.evaluate("(e) => e.isConnected"), "still the same search"
    page.close()


def test_another_devices_add_and_remove_keep_a_drag_under_way_and_rebuild_no_row(browser, lib):
    page = library_page(browser, lib)
    p = page.page
    mark_rows(p)
    dial = slider(p, "delay Feedback")
    box = dial.locator("svg").bounding_box()
    x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    p.mouse.move(x, y)
    p.mouse.down()
    p.mouse.move(x, y - 32, steps=4)
    handle = dial.element_handle()
    assert post(lib, "/live/add", {"track": "board", "uri": GXTS9, "name": "grit", "first": True})["ok"]
    expect(shown(p)).to_have_text(["grit", "overdrive", "octave", "delay"])
    assert post(lib, "/live/remove", {"processor": "octave"})["ok"]
    expect(shown(p)).to_have_text(["grit", "overdrive", "delay"])
    assert handle.evaluate("(e) => e.isConnected && document.activeElement === e"), "the same dial, still held"
    p.mouse.move(x, y - 64, steps=4)
    p.mouse.up()
    expect(slider(p, "delay Feedback")).to_have_attribute("aria-valuetext", "0.55")
    until(lambda: abs(lib.rig.info["delay"]["Feedback"]["value"] - 0.55) < 1e-6)
    assert rows_kept(p) == ["overdrive", "delay"]
    page.close()


@pytest.mark.parametrize("width", [390, 320])
def test_at_a_phones_width_the_drawer_is_a_sheet_over_the_page_with_no_sideways_scroll(browser, lib, width):
    page = library_page(browser, lib, width=width, height=760)
    p = page.page
    unlock(p)
    wide = "() => document.documentElement.scrollWidth"
    assert p.evaluate(wide) <= width, "the rack with its places to add"
    slot(p, "Add after overdrive…").click()
    box = drawer(p).bounding_box()
    assert (round(box["x"]), round(box["width"])) == (0, width), "the whole width: a sheet, not a column"
    assert abs(box["y"] + box["height"] - 760) < 1, "from the bottom of the screen"
    assert box["height"] <= 0.9 * 760
    assert p.evaluate(wide) <= width
    over = p.evaluate("""() => [...document.querySelectorAll('.drawer-sheet *')].filter((e) => {
      const b = e.getBoundingClientRect(); return b.width && (b.right > innerWidth + 0.5 || b.left < -0.5); }).length""")
    assert over == 0, "nothing in it past the screen's edge"
    # Its list scrolls inside it, and an add from it works at this width.
    entry(p, "http://distrho.sf.net/plugins/3BandEQ").scroll_into_view_if_needed()
    entry(p, "http://distrho.sf.net/plugins/3BandEQ").click()
    until(lambda: lib.rig.chains["board"][1] == "3_band_eq")
    expect(shown(p)).to_have_text(["overdrive", "3_band_eq", "octave", "delay"])
    page.close()


def test_an_add_a_move_and_a_remove_from_the_page_are_saved_with_the_files_comments(browser):
    with Served(save=True, library=fixture_library) as served:
        page = library_page(browser, served)
        p = page.page
        unlock(p)
        slot(p, "Add after delay…").click()
        entry(p, MDA_DELAY).click()
        until(lambda: served.rig.chains["board"] == ["overdrive", "octave", "delay", "mda_delay", "board_send"])
        p.get_by_role("button", name="Move mda_delay up", exact=True).click()
        until(lambda: served.rig.chains["board"] == ["overdrive", "octave", "mda_delay", "delay", "board_send"])
        unit(p, "octave").get_by_role("button", name="octave", exact=True).click()
        unit(p, "octave").get_by_role("button", name="Remove octave…").click()
        unit(p, "octave").get_by_role("button", name="Remove octave", exact=True).click()
        until(lambda: served.rig.chains["board"] == ["overdrive", "mda_delay", "delay", "board_send"])
        expect(shown(p)).to_have_text(["overdrive", "mda_delay", "delay"])
        p.get_by_role("button", name="Save to electric_board").click()
        expect(p.locator(".save-result.ok")).to_contain_text("Saved electric_board")
        unsaved_count(p, 0)
        import yaml

        text = (served.save.root / "config" / "rigs" / "electric_board.yaml").read_text()
        chain = yaml.safe_load(text)["channels"][0]["chain"]
        assert [e["name"] for e in chain] == ["overdrive", "mda_delay", "delay"]
        assert chain[1] == {"name": "mda_delay", "uri": MDA_DELAY}, "at its defaults: its name and URI"
        assert "# the drive: a comment the save keeps" in text
        page.close()


# --------------------------------------------------------------------------- #
# Found by review (PR #149)
# --------------------------------------------------------------------------- #

def test_a_name_counts_what_is_in_flight_and_a_clash_says_what_the_player_can_do(blank):
    out = rigjs(blank, LIBRARY + """
      const dly = L.plugins[1];
      const live = {tracks: {board: ['od', 'board_send']}};
      return [R.addName(dly, live, ['mda_delay']), R.addName(dly, live, ['mda_delay', 'mda_delay_2']),
              R.addOutcome('mda_delay_2', {ok: false, code: 'name_taken', reason: "'mda_delay_2' is already on the rig: give another name"}),
              R.addOutcome('x', {ok: false, code: null, reason: 'MVerb is not in plugin-ports.txt'})];""")
    assert out[0] == {"name": "mda_delay_2", "shown": "mda_delay_2"}
    assert out[1] == {"name": "mda_delay_3", "shown": "mda_delay_3"}
    assert "give" not in out[2]["reason"] and "Try again" in out[2]["reason"], "nothing the page cannot act on"
    assert out[3]["reason"] == "Not added: MVerb is not in plugin-ports.txt"


def test_the_same_plugin_added_twice_at_once_from_one_page_is_named_twice(browser, lib):
    page = library_page(browser, lib)
    p = page.page
    unlock(p)
    sent = adds(p)
    held = threading.Event()
    real_add = lib.rig.add
    lib.rig.add = lambda connection, addition: (held.wait(10), real_add(connection, addition))[1]
    slot(p, "Add after overdrive…").click()
    entry(p, MDA_DELAY).click()
    slot(p, "Add after octave…").click()
    entry(p, MDA_DELAY).click()
    expect(slot(p, "Adding mda_delay_2…")).to_be_visible()
    held.set()
    until(lambda: lib.rig.chains["board"] == ["overdrive", "mda_delay", "octave", "mda_delay_2", "delay", "board_send"])
    assert sent == [{"track": "board", "uri": MDA_DELAY, "after": "overdrive"},
                    {"track": "board", "uri": MDA_DELAY, "name": "mda_delay_2", "after": "octave"}]
    expect(shown(p)).to_have_text(["overdrive", "mda_delay", "octave", "mda_delay_2", "delay"])
    expect(p.locator(".add-said:visible")).to_have_count(0)
    page.close()


def test_the_same_plugin_added_at_once_from_two_pages_is_named_again_not_refused(browser, lib):
    pages = [library_page(browser, lib) for _ in range(2)]
    ps = [page.page for page in pages]
    sent = [adds(p) for p in ps]
    held = threading.Event()
    real_add = lib.rig.add
    lib.rig.add = lambda connection, addition: (held.wait(10), real_add(connection, addition))[1]
    for p in ps:
        unlock(p)
        slot(p, "Add after overdrive…").click()
    for p in ps:
        entry(p, MDA_DELAY).click()
    until(lambda: len(sent[0]) + len(sent[1]) >= 2)
    held.set()
    until(lambda: {"mda_delay", "mda_delay_2"} <= set(lib.rig.chains["board"]))
    for p in ps:
        expect(p.locator("#t-board .unit button.name")).to_have_count(5)
        expect(p.locator(".add-said:visible")).to_have_count(0)
    # Neither knew of the other: both sent no name, and the one refused
    # chose again from /live read again, once.
    bodies = sent[0] + sent[1]
    assert sorted(b.get("name", "") for b in bodies) == ["", "", "mda_delay_2"], bodies
    for page in pages:
        page.close()


def test_the_drawer_keeps_the_focus_and_escape_closes_it_from_anywhere(browser, lib):
    page = library_page(browser, lib)
    p = page.page
    unlock(p)
    slot(p, "Add after overdrive…").click()
    inside = "() => document.querySelector('.drawer-sheet').contains(document.activeElement)"
    for _ in range(60):  # past the last entry, round to the first, and on
        p.keyboard.press("Tab")
        assert p.evaluate(inside), p.evaluate("() => document.activeElement.outerHTML.slice(0, 80)")
    close = p.get_by_role("button", name="Close the library")
    close.focus()
    p.keyboard.press("Shift+Tab")
    expect(p.locator(".lib-entry").last).to_be_focused()
    p.keyboard.press("Tab")
    expect(close).to_be_focused()
    # Focus lost to the page however it happened: Escape still closes it.
    p.evaluate("() => document.activeElement.blur()")
    p.keyboard.press("Escape")
    expect(drawer(p)).to_be_hidden()
    expect(slot(p, "Add after overdrive…")).to_be_focused()
    page.close()


def test_the_focus_goes_somewhere_sensible_when_what_it_came_from_has_gone(browser):
    from ui_fake import UiLifecycle

    with Served(library=fixture_library) as served:
        page = library_page(browser, served)
        p = page.page
        unlock(p)
        # The place's plugin removed from elsewhere while the drawer is open: the place before it.
        slot(p, "Add after octave…").click()
        assert post(served, "/live/remove", {"processor": "octave"})["ok"]
        expect(drawer(p).locator(".drawer-why")).to_have_text("octave is no longer on board: close this and choose another place.")
        p.keyboard.press("Escape")
        expect(slot(p, "Add after overdrive…")).to_be_focused()
        # A remove: to the row that takes its place, or the place there when it was the last.
        unit(p, "overdrive").get_by_role("button", name="overdrive", exact=True).click()
        unit(p, "overdrive").get_by_role("button", name="Remove overdrive…").click()
        unit(p, "overdrive").get_by_role("button", name="Remove overdrive", exact=True).click()
        until(lambda: served.rig.chains["board"] == ["delay", "board_send"])
        expect(unit(p, "delay").get_by_role("button", name="delay", exact=True)).to_be_focused()
        unit(p, "delay").get_by_role("button", name="delay", exact=True).click()
        unit(p, "delay").get_by_role("button", name="Remove delay…").click()
        unit(p, "delay").get_by_role("button", name="Remove delay", exact=True).click()
        until(lambda: served.rig.chains["board"] == ["board_send"])
        expect(slot(p, "Add at the start…")).to_be_focused()
        page.close()

    world = UiLifecycle(up=None)
    with Served(library=fixture_library, lifecycle=world) as served:
        page = Page(browser, served.url + "/#rack")
        p = page.page
        p.get_by_role("button", name="Browse the library…").click()
        expect(drawer(p)).to_be_visible()
        # A rig comes up meanwhile, from elsewhere: the button it came from is gone.
        assert post(served, "/rig/up", {"config_name": "electric_board", "rig": True, "headless": True})["ok"]
        expect(p.locator(".add-slot")).to_have_count(4, timeout=8000)
        p.keyboard.press("Escape")
        expect(slot(p, "Add at the start…")).to_be_focused()
        page.close()


def test_an_armed_remove_and_a_places_refusal_do_not_outlive_a_switch_to_a_rig_with_the_same_names(browser, lib):
    """`accustomed`, switched to live from elsewhere, is the same fake rig:
    the same tracks and plugin names (review of #130: the confirm armed on
    the rig before came back on the new rig's delay, one tap from removing it)."""
    page = library_page(browser, lib)
    p = page.page
    unlock(p)
    unit(p, "delay").get_by_role("button", name="delay", exact=True).click()
    unit(p, "delay").get_by_role("button", name="Remove delay…").click()
    expect(unit(p, "delay").get_by_role("button", name="Remove delay", exact=True)).to_be_visible()
    assert post(lib, "/rig/switch", {"config_name": "accustomed"})["ok"]
    expect(p.locator(".rig-name")).to_have_text("accustomed")
    expect(unit(p, "delay").get_by_role("button", name="Remove delay…")).to_be_visible(timeout=6000)
    expect(unit(p, "delay").get_by_role("button", name="Remove delay", exact=True)).to_be_hidden()
    expect(unit(p, "delay").locator(".row-said")).to_be_hidden()
    assert lib.rig.chains["board"] == ["overdrive", "octave", "delay", "board_send"]
    page.close()


def test_a_drawer_open_across_a_switch_adds_nothing_to_the_rig_switched_to(browser, lib):
    page = library_page(browser, lib)
    p = page.page
    unlock(p)
    slot(p, "Add after overdrive…").click()
    expect(entry(p, MDA_DELAY)).to_be_enabled()
    assert post(lib, "/rig/switch", {"config_name": "accustomed"})["ok"]
    expect(drawer(p).locator(".drawer-why")).to_have_text(
        "The rig changed since the library was opened (it is now accustomed): close this, and open it again from the chain.")
    expect(p.locator(".lib-entry:enabled")).to_have_count(0)
    assert lib.rig.writes() == []
    p.get_by_role("button", name="Close the library").click()
    slot(p, "Add after overdrive…").click()
    expect(entry(p, MDA_DELAY)).to_be_enabled()
    page.close()


def test_an_add_in_flight_across_a_switch_is_said_as_the_rig_it_was_sent_to(browser, lib):
    """The agent's live switch does not wait for an add Sushi is still
    answering (seen here: the fake's switch is done while the add is held),
    so the page shows the next rig before the add's answer comes. The answer
    is said as electric_board's, not drawn as an add on the rig shown."""
    page = library_page(browser, lib)
    p = page.page
    unlock(p)
    held = threading.Event()
    real_add = lib.rig.add
    lib.rig.add = lambda connection, addition: (held.wait(10), real_add(connection, addition))[1]
    slot(p, "Add after overdrive…").click()
    entry(p, MDA_DELAY).click()
    expect(slot(p, "Adding mda_delay…")).to_be_visible()
    assert post(lib, "/rig/switch", {"config_name": "accustomed"})["ok"]
    expect(p.locator(".rig-name")).to_have_text("accustomed")
    expect(slot(p, "Add after overdrive…")).to_have_attribute("aria-busy", "false", timeout=6000)
    held.set()
    expect(p.locator(".notice")).to_have_text(
        re.compile(r"^Added mda_delay to board on electric_board, which the page no longer shows\."))
    expect(p.locator(".add-said:visible")).to_have_count(0)
    page.close()


def test_a_locked_line_goes_when_the_page_is_unlocked(browser, lib):
    page = library_page(browser, lib)
    p = page.page
    unit(p, "delay").get_by_role("button", name="delay", exact=True).click()
    unit(p, "delay").get_by_role("button", name="Remove delay… (locked)").click()
    expect(unit(p, "delay").locator(".row-said")).to_contain_text("Locked: removing a plugin")
    p.get_by_role("button", name="Move delay up (locked)").click()
    expect(p.locator(".problem.refused")).to_contain_text("Locked: reordering")
    unlock(p)
    expect(unit(p, "delay").locator(".row-said")).to_be_hidden()
    expect(p.locator(".problem.refused")).to_have_count(0)
    expect(unit(p, "delay").get_by_role("button", name="Remove delay…")).to_be_visible()
    page.close()


def test_the_fakes_ids_only_ever_go_up_as_sushis_do():
    rig = UiRig()
    first = rig._ids()
    with rig.lock:
        rig.chains["board"].remove("delay")
        rig._id_of.pop("delay")
    rig.chains["board"].insert(0, "delay")
    again = rig._ids()
    assert again["delay"] > max(first.values()), "a processor added again gets an id never given before"
    assert {k: v for k, v in again.items() if k != "delay"} == {k: v for k, v in first.items() if k != "delay"}
