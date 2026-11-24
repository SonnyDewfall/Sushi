"""Assigning pedal controls in the rack (#192): a badge beside an effect's
on/off and by a channel's name, each opening a panel of selects; the Pedals
section under the strips, saying what each control does on this rig, with
its "heard" light and the device's state.

Two layers, as test_ui.py has them: rig.js's decisions imported into a bare
page, then the rack driven in Chromium against a real agent over the fake of
`ui_fake.py` with pedals (`Served(save=True, pedals=True)`): the agent's real
POST /rig/midi/assign writes the fake checkout's rig file through the real
compiler, and the agent's real MIDI listener reads `ui_fake.UiPedals`' fake
port, into which a test sends what the FBV would.

What stands in for what: Sushi is `ui_fake.UiRig`, the board is bytes put on
a queue, and a restart is `UiLifecycle`'s (the running config compiled again
from the file). Nothing here is the rig machine, a phone or a pedal.
"""

from __future__ import annotations

import pytest
import yaml

pytest.importorskip("aiohttp")

from test_ui import Page, blank, browser, expect, rigjs, served, until  # noqa: E402,F401
from ui_fake import Served  # noqa: E402

PHONE = {"width": 390, "height": 844}


@pytest.fixture
def pedals_served():
    with Served(save=True, pedals=True) as s:
        yield s


def rack(browser, s, **size):
    page = Page(browser, s.url + "/#rack", **size)
    page.page.wait_for_selector(".pedal-badge")
    return page


def unlock(p):
    lock = p.locator("button.lock")
    lock.click()
    lock.click()
    expect(lock).to_have_text("Lock off · editing")


def midi_of(s):
    return yaml.safe_load((s.save.root / "config" / "rigs" / "electric_board.yaml").read_text()).get("midi")


def badge(p, target):
    return p.locator(f'.pedal-badge[aria-label^="Pedal for {target}:"]')


def open_panel(p, target):
    badge(p, target).first.click()
    panel = p.get_by_role("group", name=f"Pedal for {target}")
    expect(panel).to_be_visible()
    return panel


def summary(p, control):
    return p.locator(f'section.pedals .pedal-row[data-control="{control}"] .does')


# --------------------------------------------------------------------------- #
# The decisions, pure (rig.js)
# --------------------------------------------------------------------------- #

FBV = {"name": "fbv", "type": "pedal", "port": "FBV", "channel": 0, "controls": [
    {"name": "a", "sends": {"cc": 20}, "kind": "switch", "behaviour": "momentary", "range": None, "job": "previous-rig", "takes": []},
    {"name": "b", "sends": {"cc": 21}, "kind": "switch", "behaviour": "momentary", "range": None, "job": "next-rig", "takes": []},
    {"name": "c", "sends": {"cc": 22}, "kind": "switch", "behaviour": "momentary", "range": None, "job": None, "takes": ["bypass", "mute"]},
    {"name": "d", "sends": {"cc": 23}, "kind": "switch", "behaviour": "momentary", "range": None, "job": None, "takes": ["bypass", "mute"]},
    {"name": "pedal", "sends": {"cc": 7}, "kind": "expression", "behaviour": None, "range": [0, 127], "job": None,
     "takes": ["volume", "parameter", "blend"]}]}
MIDI = {"ok": True, "status": 200, "body": {"ok": True, "controllers": [FBV], "rig": "electric_board",
        "assignments": {"controller": "fbv", "port": 0, "programs": None, "controls": [
            {"control": "c", "bypass": "board/overdrive"}, {"control": "pedal", "volume": "vocal"}]}}}


def test_only_the_controls_that_can_take_a_target_are_offered_and_a_job_never(blank):
    out = rigjs(blank, """
      const b = R.pedalBoard(arg);
      return {
        controller: b.controller,
        onoff: R.pedalOptions(b, 'bypass', 'board/delay').map((o) => [o.value, o.label]),
        mute: R.pedalOptions(b, 'mute', 'vocal').map((o) => o.value),
        volume: R.pedalOptions(b, 'volume', 'board').map((o) => [o.value, o.label]),
        on: [R.pedalOn(b, 'bypass', 'board/overdrive'), R.pedalOn(b, 'volume', 'vocal'), R.pedalOn(b, 'mute', 'vocal')],
      };""", MIDI)
    assert out["controller"] == "fbv"
    assert out["onoff"] == [["", "none"], ["c", "C (now board/overdrive on/off)"], ["d", "D"]]
    assert out["mute"] == ["", "c", "d"], "the switches only, never A or B, which step rigs"
    assert out["volume"] == [["", "none"], ["pedal", "pedal (now vocal volume)"]]
    assert out["on"] == ["c", "pedal", None]


def test_a_choice_that_moves_a_control_asks_first_naming_what_it_is_taken_from(blank):
    out = rigjs(blank, """
      const b = R.pedalBoard(arg);
      return {
        move: R.pedalChoice(b, 'mute', 'vocal', 'c'),
        fresh: R.pedalChoice(b, 'mute', 'vocal', 'd'),
        off: R.pedalChoice(b, 'bypass', 'board/overdrive', ''),
        same: R.pedalChoice(b, 'bypass', 'board/overdrive', 'c'),
        asked: R.pedalChoice(R.pedalBoard({ok: true, body: {controllers: [arg.body.controllers[0], {...arg.body.controllers[0], name: 'fbv2'}], assignments: null}}, 'fbv2'), 'mute', 'vocal', 'd', 'fbv2'),
      };""", MIDI)
    assert out["move"]["confirm"] == "C is on board/overdrive on/off. Move it to vocal on/off?"
    assert out["move"]["moveLabel"] == "Move C" and out["move"]["call"] == {"path": "/rig/midi/assign",
                                                                         "json": {"control": "c", "mute": "vocal"}}
    assert out["fresh"]["confirm"] is None and out["fresh"]["call"]["json"] == {"control": "d", "mute": "vocal"}
    assert out["off"]["call"]["json"] == {"control": "c", "none": True} and out["off"]["confirm"] is None
    assert out["same"] is None, "nothing changes, nothing is sent"
    assert out["asked"]["call"]["json"] == {"control": "d", "mute": "vocal", "controller": "fbv2"}


def test_the_lights_and_states(blank):
    out = rigjs(blank, """
      const health = (seen, state = 'listening', extra = {}) => ({ok: true, body: {agent: {midi: {
        state, feeds: [{controller: 'fbv', state, required: false, problem: extra.problem || null}], seen: {fbv: seen}}}}});
      const b = R.pedalBoard(arg);
      const h1 = health({c: {count: 2, value: 0, ago: 30}});
      const h2 = health({c: {count: 4, value: 0, ago: 0.2}, pedal: {count: 1, value: 90, ago: 0.1}});
      const first = R.pedalLights({}, h1, 'fbv', 1000);
      const second = R.pedalLights(first, h2, 'fbv', 2000);
      const third = R.pedalLights(second, h2, 'fbv', 4000);
      return {
        old: R.pedalSummary(b, first, 1000).find((r) => r.control === 'c').lit,
        rows: R.pedalSummary(b, second, 2000).map((r) => [r.name, r.does, r.lit, r.value]),
        later: R.pedalSummary(b, third, 4000).map((r) => r.lit),
        states: ['listening', 'absent', 'waiting', 'failed'].map((s) =>
          R.pedalDeviceState(health({}, s, {problem: 'it broke'}), 'fbv').text),
        down: R.pedalDeviceState({ok: true, body: {agent: {midi: {state: 'off', reason: 'no rig is running', feeds: []}}}}, 'fbv').text,
        locked: R.lockRefusal(true, 'pedal'), open: R.lockRefusal(false, 'pedal'),
      };""", MIDI)
    assert out["old"] is False, "a count first seen long after it was heard does not light"
    assert out["rows"] == [["A", "previous rig", False, None], ["B", "next rig", False, None],
                           ["C", "board/overdrive on/off", True, None], ["D", "nothing on this rig", False, None],
                           ["pedal", "vocal volume", True, 90]]
    assert out["later"] == [False] * 5, "a light goes out once nothing more is heard"
    assert out["states"] == ["listening", "not connected", "waiting: this rig needs it, and it is not plugged in", "it broke"]
    assert out["down"] == "not listened to: no rig is running"
    assert "assigning a pedal" in out["locked"] and out["open"] is None


def test_what_an_assignment_said(blank):
    out = rigjs(blank, """
      const ok = (body) => ({ok: true, status: 200, body: {ok: true, file: 'config/rigs/r.yaml', rig: 'r', ...body}});
      return {
        live: R.pedalOutcome('C: vocal on/off', ok({how: 'live', change: {control: 'c', moved: {said: 'bypass board/drive'},
          replaced: [{control: 'd', mute: 'vocal'}], controller_set: true, controller: 'fbv'}})),
        restart: R.pedalOutcome('pedal: vocal volume', ok({how: 'restart', why: [{code: 'midi', detail: 'the cc_mappings differ'}],
          change: {control: 'pedal', replaced: []}})),
        no: R.pedalOutcome('pedal: board/overdrive on/off', {ok: false, status: 409, code: 'rig_problem', reason: 'not a bypass', body: {}}),
      };""")
    assert out["live"]["text"] == ("Wrote config/rigs/r.yaml in place, keeping its comments. C came off bypass board/drive. "
                                   "D came off it. The rig's map is now written for fbv. It plays now.")
    assert out["live"]["offer"] is None
    assert "It plays when r restarts (the cc_mappings differ)" in out["restart"]["text"]
    assert out["restart"]["offer"] == {"op": "restart", "name": "r", "label": "Restart r to play it"}
    assert out["no"] == {"ok": False, "title": "Not assigned: pedal: board/overdrive on/off", "reason": "not a bypass",
                         "code": "rig_problem", "controllers": None}


# --------------------------------------------------------------------------- #
# The rack, against the agent
# --------------------------------------------------------------------------- #

def test_a_switch_is_assigned_to_an_effects_on_off_from_its_row_and_plays_now(browser, pedals_served):
    s = pedals_served
    page = rack(browser, s)
    p = page.page
    expect(summary(p, "c")).to_have_text("nothing on this rig")
    unlock(p)
    panel = open_panel(p, "overdrive")
    onoff = panel.get_by_label("overdrive on/off")
    expect(onoff.locator("option")).to_have_text(["none", "C", "D"])
    onoff.select_option("c")
    expect(summary(p, "c")).to_have_text("board/overdrive on/off")
    expect(badge(p, "overdrive")).to_have_text("C")
    expect(p.locator("section.pedals .rigfile-result")).to_contain_text("It plays now.")
    assert midi_of(s) == {"controller": "fbv", "controls": [{"control": "c", "bypass": "board/overdrive"}]}
    s.pedals.press("c")
    until(lambda: s.rig.bypassed["overdrive"] is True)
    page.close()


def test_a_control_in_use_is_moved_only_after_a_confirm_naming_where_it_was(browser, pedals_served):
    s = pedals_served
    page = rack(browser, s)
    p = page.page
    unlock(p)
    open_panel(p, "overdrive").get_by_label("overdrive on/off").select_option("c")
    expect(summary(p, "c")).to_have_text("board/overdrive on/off")
    panel = open_panel(p, "vocal")
    mute = panel.get_by_label("vocal on/off")
    expect(mute.locator("option")).to_have_text(["none", "C (now board/overdrive on/off)", "D"])
    mute.select_option("c")
    confirm = panel.get_by_role("group", name="Confirm the move")
    expect(confirm).to_contain_text("C is on board/overdrive on/off. Move it to vocal on/off?")
    expect(mute).to_have_value("c")  # the control picked, while it is asked about
    confirm.get_by_role("button", name="Keep it").click()
    expect(confirm).to_have_count(0)
    expect(mute).to_have_value("")  # kept: none, as it was
    assert midi_of(s)["controls"] == [{"control": "c", "bypass": "board/overdrive"}], "kept: nothing written"
    mute.select_option("c")
    panel.get_by_role("button", name="Move C").click()
    expect(summary(p, "c")).to_have_text("vocal on/off")
    expect(badge(p, "overdrive")).to_have_text("")
    expect(badge(p, "vocal")).to_have_text("C")
    assert midi_of(s)["controls"] == [{"control": "c", "mute": "vocal"}]
    page.close()


def test_none_takes_a_control_off_and_the_map_goes_with_the_last(browser, pedals_served):
    s = pedals_served
    page = rack(browser, s)
    p = page.page
    unlock(p)
    open_panel(p, "overdrive").get_by_label("overdrive on/off").select_option("c")
    expect(summary(p, "c")).to_have_text("board/overdrive on/off")
    open_panel(p, "overdrive").get_by_label("overdrive on/off").select_option("")
    expect(summary(p, "c")).to_have_text("nothing on this rig")
    assert midi_of(s) is None
    page.close()


def test_locked_the_panel_shows_the_assignment_and_assigns_nothing(browser, pedals_served):
    s = pedals_served
    page = rack(browser, s)
    p = page.page
    expect(p.locator("button.lock")).to_have_text("Locked")
    panel = open_panel(p, "overdrive")
    onoff = panel.get_by_label("overdrive on/off")
    expect(onoff).to_be_disabled()
    expect(panel).to_contain_text("Locked: assigning a pedal")
    assert midi_of(s) is None
    unlock(p)
    expect(onoff).to_be_enabled()
    page.close()


def test_a_pedal_on_a_parameter_from_the_effects_row_offers_the_restart(browser, pedals_served):
    """A sweep is Sushi's own mapping, loaded with the config: written, and
    the restart offered, which brings it in."""
    s = pedals_served
    page = rack(browser, s)
    p = page.page
    unlock(p)
    panel = open_panel(p, "overdrive")
    sweep = panel.get_by_label("overdrive pedal sweeps")
    # The profile's controls, in its order: not the plugin's own BYPASS, which it hides.
    expect(sweep.locator("option")).to_have_text(["none", "Drive", "Tone", "Level"])
    sweep.select_option("Drive")
    result = p.locator("section.pedals .rigfile-result")
    expect(result).to_contain_text("It plays when electric_board restarts")
    assert midi_of(s)["controls"] == [{"control": "pedal", "parameter": "board/overdrive/Drive"}]
    expect(summary(p, "pedal")).to_have_text("board/overdrive/Drive")
    result.get_by_role("button", name="Restart").click()
    p.locator("section.pedals").get_by_role("group", name="Restart?").get_by_role("button", name="Yes").click()
    until(lambda: ["restart"] in s.lifecycle.calls)
    page.close()


def test_a_pedal_on_a_channels_volume_from_its_strip(browser, pedals_served):
    s = pedals_served
    page = rack(browser, s)
    p = page.page
    unlock(p)
    panel = open_panel(p, "vocal")
    volume = panel.get_by_label("vocal volume")
    expect(volume.locator("option")).to_have_text(["none", "pedal"])
    volume.select_option("pedal")
    expect(summary(p, "pedal")).to_have_text("vocal volume")
    assert midi_of(s)["controls"] == [{"control": "pedal", "volume": "vocal"}]
    # The file says the pedal drives it: the strip's volume says so too.
    expect(p.locator('.strip[data-channel="vocal"] .volume .driven')).to_have_text("driven by pedal")
    page.close()


def test_the_compilers_refusal_is_said_in_the_panel_and_nothing_is_written(browser, pedals_served):
    """The page offers what `takes` allows; what it cannot know is the
    compiler's to refuse, in its words. Here a plugin added live and not
    saved (an echo, put on the fake rig as an add from the library leaves
    it): the rig file's chain has no echo, so C cannot be bound to it."""
    s = pedals_served
    with s.rig.lock:
        s.rig.chains["board"].insert(3, "echo")
        s.rig.info["echo"] = dict(s.rig.info["delay"])
        s.rig.bypassed["echo"] = False
        s.rig.classes["echo"] = dict(s.rig.classes["delay"])
    path = s.save.root / "config" / "rigs" / "electric_board.yaml"
    before = path.read_text()
    page = rack(browser, s)
    p = page.page
    unlock(p)
    panel = open_panel(p, "echo")
    panel.get_by_label("echo on/off").select_option("c")
    expect(panel.get_by_role("alert")).to_contain_text("Not assigned:")
    expect(panel.get_by_role("alert")).to_contain_text("bypass names 'echo', which is not in board's chain")
    assert path.read_text() == before
    expect(summary(p, "c")).to_have_text("nothing on this rig")
    page.close()


def test_the_heard_light_and_the_pedals_value(browser, pedals_served):
    """D is on nothing: its light still comes on when it is pressed, and the
    pedal's row shows its last value. A check that the board sends, not a
    learn: nothing is assigned by it."""
    s = pedals_served
    page = rack(browser, s)
    p = page.page
    d = p.locator('section.pedals .pedal-row[data-control="d"]')
    expect(d).not_to_have_class("pedal-row lit")
    s.pedals.press("d")
    expect(d).to_have_class("pedal-row lit heard-once")
    s.pedals.sweep(90)
    expect(p.locator('section.pedals .pedal-row[data-control="pedal"] .value')).to_have_text("90")
    expect(d).to_have_class("pedal-row heard-once", timeout=6000)  # and out again
    assert midi_of(s) is None
    page.close()


def test_the_device_state_is_said_plainly(browser, pedals_served):
    s = pedals_served
    page = rack(browser, s)
    p = page.page
    device = p.locator("section.pedals .pedal-device")
    expect(device).to_have_text("fbv · listening")
    s.pedals.plugged = False
    expect(device).to_have_text("fbv · not connected")
    unlock(p)
    open_panel(p, "overdrive").get_by_label("overdrive on/off").select_option("c")
    expect(device).to_have_text("fbv · waiting: this rig needs it, and it is not plugged in")
    s.pedals.plugged = True
    expect(device).to_have_text("fbv · listening")
    page.close()


def test_a_rig_with_no_device_shows_nothing_new(browser, served):
    page = Page(browser, served.url + "/#rack")
    p = page.page
    p.wait_for_selector(".unit")
    expect(p.locator("section.pedals")).to_be_hidden()
    expect(p.locator(".pedal-badge")).to_have_count(0)
    page.close()


@pytest.mark.parametrize("font", ["", "* { font-family: 'DejaVu Sans', 'Liberation Sans', sans-serif !important; letter-spacing: .08em; }"],
                         ids=["page fonts", "a wider fallback font"])
def test_at_phone_width_the_badges_and_panels_fit(browser, pedals_served, font):
    s = pedals_served
    page = rack(browser, s, **PHONE)
    p = page.page
    if font:
        p.add_style_tag(content=font)
    unlock(p)
    open_panel(p, "overdrive").get_by_label("overdrive on/off").select_option("c")
    expect(summary(p, "c")).to_have_text("board/overdrive on/off")
    open_panel(p, "overdrive")
    open_panel(p, "vocal")
    for b in p.locator(".pedal-badge").all():
        box = b.bounding_box()
        assert box["height"] >= 44 and box["width"] >= 44, box
    wide = p.evaluate("() => [...document.querySelectorAll('#app *')].filter((e) => e.getBoundingClientRect().right > innerWidth + 0.5).map((e) => e.className)")
    assert wide == [], wide
    assert p.evaluate("() => document.documentElement.scrollWidth <= innerWidth")
    page.close()


def test_the_badge_names_its_panel_and_has_the_focus_back_after_an_assignment(browser, pedals_served):
    s = pedals_served
    page = rack(browser, s)
    p = page.page
    unlock(p)
    button = badge(p, "overdrive")
    button.click()
    panel = p.get_by_role("group", name="Pedal for overdrive")
    expect(panel).to_be_visible()
    controls = button.get_attribute("aria-controls")
    assert controls and p.locator(f"#{controls}").locator(".pedal-panel").count() == 1
    onoff = panel.get_by_label("overdrive on/off")
    onoff.focus()
    onoff.select_option("c")
    expect(summary(p, "c")).to_have_text("board/overdrive on/off")
    expect(panel).to_have_count(0)
    expect(badge(p, "overdrive")).to_be_focused()
    page.close()


def test_a_light_is_said_in_words_too(browser, pedals_served):
    s = pedals_served
    page = rack(browser, s)
    p = page.page
    said = p.locator('section.pedals .pedal-row[data-control="d"] .heard-said')
    expect(said).to_have_text("not heard yet")
    s.pedals.press("d")
    expect(said).to_have_text("heard just now")
    expect(said).to_have_text("heard before", timeout=6000)
    page.close()


def test_the_panel_says_the_locks_own_words(browser, pedals_served):
    s = pedals_served
    page = rack(browser, s)
    p = page.page
    words = rigjs(p, "return R.lockRefusal(true, 'pedal');")
    assert "assigning a pedal" in words
    panel = open_panel(p, "overdrive")
    expect(panel.locator(".lock-says")).to_have_text(words)
    page.close()


def test_a_save_as_whose_file_waits_for_a_restart_says_so_and_offers_it(browser, monkeypatch):
    """The pedal assigned to a volume (for the restart), then Save as: the
    page says the rig plays as it was loaded until it restarts, and offers
    the restart, behind a confirm, as an edit of the file does."""
    from pathlib import Path

    import ui_fake
    from sushi_rig import agent_client
    from sushi_rig.rig import compile_named_rig

    def compile_(self, name, root, machine):
        self.compiled.append(name)
        return compile_named_rig(name, Path(root), Path(machine) if machine else None)

    monkeypatch.setattr(ui_fake.UiSave, "compile", compile_)
    with Served(save=True, pedals=True) as s:
        page = rack(browser, s)
        p = page.page
        status, answer = agent_client.ask(s.url, "POST", "/rig/midi/assign", {"control": "pedal", "volume": "board"})
        assert status == 200 and answer["how"] == "restart", answer
        p.get_by_role("button", name="Save as…").click()
        p.get_by_label("New rig name").fill("foo")
        p.get_by_role("button", name="Save as foo").click()
        result = p.locator(".save-result.ok")
        expect(result).to_contain_text("Saved as foo")
        expect(result).to_contain_text("It plays as it was loaded until it restarts (")
        result.get_by_role("button", name="Restart").click()
        p.locator("section.save").get_by_role("group", name="Restart?").get_by_role("button", name="Yes").click()
        until(lambda: ["restart"] in s.lifecycle.calls)
        page.close()
