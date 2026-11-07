"""Setting values in the web UI, the volumes and history (2026-11-06): knobs
that can be set (a long drag, fine with shift, the wheel, a value typed,
whole numbers that hold), the master's volume and each channel's, the rack
as a mixing desk, the one sign of unsaved work on Save, and "Revert to a
previous save".

Two layers, as test_ui.py has them: rig.js's decisions imported into a bare
page, then the screens driven in Chromium against a real agent over the
fake rig of `ui_fake.py` (which says what in it is observed and what
invented). Nothing here is a real Sushi; the agent's own routes for saves
and revert are test_agent_revert.py's, against real rig files.
"""

from __future__ import annotations

import re
import time

import pytest

pytest.importorskip("aiohttp")

from test_ui import Page, Watched, blank, browser, expect, post, rigjs, served, slider, unsaved_count, until  # noqa: E402,F401
from ui_fake import Served, UiLifecycle, describe_up  # noqa: E402

LEVEL = "{automatable: true, value: 0, min_domain_value: -20, max_domain_value: 4}"  # gxts9's Level, no unit


# --------------------------------------------------------------------------- #
# The decisions, pure (rig.js)
# --------------------------------------------------------------------------- #

def test_a_full_sweep_is_a_long_drag_and_shift_is_a_tenth(blank):
    out = rigjs(blank, """
      return [R.DRAG_PIXELS, R.dragPosition(0.5, -32), R.dragPosition(0.5, -32, true), R.dragPosition(0.5, 32),
              R.dragPosition(0, -R.DRAG_PIXELS), R.dragPosition(0.9, -100), R.dragPosition(0, 10)];""")
    assert out[0] >= 300, "a full sweep needs a long, deliberate drag"
    assert out[1] == pytest.approx(0.6) and out[2] == pytest.approx(0.51) and out[3] == pytest.approx(0.4)
    assert out[4] == 1 and out[5] == 1 and out[6] == 0, "clamped to the ends"


def test_whole_numbers_hold_a_drag_for_a_little_further_than_they_catch_it(blank):
    out = rigjs(blank, f"""
      const c = R.makeControl('Level', {LEVEL}, null);
      const at = (d, held = null) => {{ const s = R.sticky(c, R.fromDisplay(c, d), held); return [R.displayOf(c, s.v), s.held]; }};
      return [at(-3.1), at(-3.2), at(-2.75, -3), at(-2.6, -3), at(-3.14), at(-3.16)];""")
    assert out[0] == [pytest.approx(-3), -3], "within 0.15 of a whole number: on it"
    assert out[1] == [pytest.approx(-3.2), None], "outside the band: as dragged"
    assert out[2] == [pytest.approx(-3), -3], "held on past the band, to 0.35"
    assert out[3] == [pytest.approx(-2.6), None], "and let go after"
    assert out[4][1] == -3 and out[5][1] is None


def test_what_snaps_is_whole_display_units_and_a_normalised_parameter_its_hundredths(blank):
    out = rigjs(blank, """
      const mk = (min, max, e) => R.makeControl('x', {automatable: true, value: 0, min_domain_value: min, max_domain_value: max}, e);
      const drive = mk(0, 1, null), send = mk(-120, 24, {unit: 'dB'}), gain = mk(0.001, 10, {scale: 'db'});
      const pct = mk(0, 100, {unit: '%'}), cents = mk(-100, 100, {integer: true}), tog = mk(0, 1, {scale: 'toggle'});
      const s = (c, v) => R.formatValue(c, R.sticky(c, v).v);
      return {grids: [drive, send, gain, pct, cents, tog].map(R.snapGrid),
              drive: [s(drive, 0.6249), s(drive, 0.996), s(drive, 0.004)],
              send: s(send, R.fromDisplay(send, -6.12)), pct: s(pct, 0.4988),
              gain: s(gain, R.fromDisplay(gain, -6.1))};""")
    assert out["grids"] == [0.01, 1, 1, 1, None, None]
    assert out["drive"] == ["0.62", "1.00", "0.00"], "0.01 steps, and near 0 or 1 nothing special"
    assert out["send"] == "−6 dB" and out["pct"] == "50 %"
    assert out["gain"] == "−6.0 dB", "a dB scale snaps in dB"


def test_the_wheel_steps_by_a_round_one_percent_and_a_tenth_with_shift(blank):
    out = rigjs(blank, f"""
      const mk = (min, max, e) => R.makeControl('x', {{automatable: true, value: 0, min_domain_value: min, max_domain_value: max}}, e);
      const level = R.makeControl('Level', {LEVEL}, null), send = mk(-120, 24, {{unit: 'dB'}}), tone = mk(100, 1000, null);
      const cents = mk(-100, 100, {{integer: true}}), f = mk(20, 20000, {{scale: 'log'}});
      const show = (c, v) => R.formatValue(c, v);
      return {{steps: [level, send, tone, cents, f].map(R.niceStep),
        level: [show(level, R.stepped(level, R.fromDisplay(level, -3), 1)), show(level, R.stepped(level, R.fromDisplay(level, -3.13), 1)),
                show(level, R.stepped(level, R.fromDisplay(level, -3.13), -1)), show(level, R.stepped(level, R.fromDisplay(level, -3), 1, true))],
        send: show(send, R.stepped(send, R.fromDisplay(send, -6), 10)), cents: show(cents, R.stepped(cents, 0.5, 1, true)),
        log: R.positionOf(f, R.stepped(f, R.valueAt(f, 0.5), 1)), ends: show(level, R.stepped(level, 1, 3))}};""")
    assert out["steps"] == [0.2, 1, 5, None, None]
    assert out["level"] == ["−2.8", "−3.0", "−3.2", "−3.0"], "off the grid, the first step lands on it; fine is a tenth"
    assert out["send"] == "4 dB" and out["cents"] == "1", "an integer steps by one even fine"
    assert out["log"] == pytest.approx(0.51) and out["ends"] == "4.0"


def test_a_scroll_moves_every_notch_and_lands_on_the_whole_numbers_it_crosses(blank):
    """Stateless: each notch starts from the value as it is, so a slow
    scroll leaves a whole number (review, 2026-11-07: a 1-10 Ratio at 4.0 in
    0.05 steps snapped back to 4.0 on every notch)."""
    out = rigjs(blank, """
      const mk = (min, max, e) => R.makeControl('x', {automatable: true, value: 0, min_domain_value: min, max_domain_value: max}, e);
      const ratio = mk(1, 10, null), db = mk(-3, 3, {unit: 'dB'}), mix = mk(0, 1, {unit: 'dB'});
      const run = (c, d, n, fine = false) => { let v = R.fromDisplay(c, d), out = [];
        for (let i = 0; i < n; i++) { v = R.scrolled(c, v, 1, fine); out.push(R.displayOf(c, v)); } return out; };
      return {ratio: run(ratio, 4, 3), down: R.displayOf(ratio, R.scrolled(ratio, R.fromDisplay(ratio, 4), -1)),
              crosses: run(db, -1.07, 3), zero: run(mix, 0, 2), fine: run(db, -1.02, 3, true),
              steps: [R.niceStep(ratio), R.niceStep(db)]};""")
    assert out["steps"] == [0.05, 0.05]
    assert out["ratio"] == [pytest.approx(4.05), pytest.approx(4.1), pytest.approx(4.15)], "it leaves 4.0"
    assert out["down"] == pytest.approx(3.95)
    assert out["crosses"] == [pytest.approx(-1.05), pytest.approx(-1.0), pytest.approx(-0.95)], "lands on -1 as it passes"
    assert out["zero"] == [pytest.approx(0.01), pytest.approx(0.02)], "it leaves 0, the whole number it starts on"
    assert out["fine"] == [pytest.approx(-1.015), pytest.approx(-1.01), pytest.approx(-1.005)], "fine never snaps"


def test_the_wheel_is_the_controls_only_when_meant(blank):
    out = rigjs(blank, """
      const t = (o) => R.takesWheel({settable: true, focused: false, now: 10000, enteredAt: 9000, scrolledAt: 0, ...o});
      return [R.WHEEL_REST_MS, t({}), t({settable: false}), t({settable: false, focused: true}), t({focused: true, enteredAt: 9990}),
              t({enteredAt: 9900}), t({scrolledAt: 9800}), t({scrolledAt: 9600}), t({enteredAt: null})];""")
    assert out[0] == 300
    assert out[1:] == [True, False, False, True, False, False, True, False], \
        "rested and not scrolling: yes; not settable: never; focused: yes; just arrived or the page just scrolled: no"


def test_wheel_events_are_whole_notches_and_a_trackpads_add_up(blank):
    out = rigjs(blank, """
      let rest = 0, got = [];
      for (const dy of [-30, -30, -30, -30]) { const r = R.wheelNotches(rest, dy); rest = r.rest; got.push(r.notches); }
      return [R.wheelNotches(0, -100), R.wheelNotches(0, 100), R.wheelNotches(0, -53), R.wheelNotches(0, -3, 1),
              R.wheelNotches(0, -300), got];""")
    assert out[0]["notches"] == 1 and out[1]["notches"] == -1, "up the page is up"
    assert out[2]["notches"] == 1, "a coarse notch under 100 px is still one"
    assert out[3]["notches"] == 1, "three lines are a notch"
    assert out[4]["notches"] == 3
    assert out[5] == [0, 0, 0, 1], "a trackpad's small deltas add up to a notch"


@pytest.mark.parametrize("typed, shown", [
    ("-3", "−3.0"), ("−3.5", "−3.5"), (" -3,5 ", "−3.5"), ("+2", "2.0"), ("99", "4.0"), ("-inf", "−20.0"),
    ("3.33", "3.3"),
])
def test_a_typed_value_is_read_leniently_clamped_and_exact(blank, typed, shown):
    out = rigjs(blank, f"""
      const c = R.makeControl('Level', {LEVEL}, null);
      const r = R.parseTyped(c, arg);
      return [r.ok, r.ok ? R.formatValue(c, r.v) : r.why, r.ok ? R.displayOf(c, r.v) : null];""", typed)
    assert out[0] is True and out[1] == shown
    if typed == "3.33":
        assert out[2] == pytest.approx(3.33), "typed values are exact, not snapped"


def test_typed_values_take_their_unit_choices_and_refuse_what_is_not_one(blank):
    out = rigjs(blank, """
      const mk = (min, max, e) => R.makeControl('x', {automatable: true, value: 0, min_domain_value: min, max_domain_value: max}, e);
      const send = mk(-120, 24, {unit: 'dB'}), gain = mk(0.001, 10, {scale: 'db'}), hz = mk(20, 20000, {unit: 'Hz', scale: 'log'});
      const pick = mk(0, 3, {scale: 'enum', scale_points: [{value: 0, label: 'Smooth'}, {value: 1, label: 'Crisp'}]});
      const cents = mk(-100, 100, {integer: true});
      const f = (c, t) => { const r = R.parseTyped(c, t); return r.ok ? R.formatValue(c, r.v) : 'refused: ' + r.why; };
      return [f(send, '-6 dB'), f(send, '-6dB'), f(send, '-6 db'), f(gain, '0 dB'), f(gain, '-inf'), f(hz, '440 Hz'),
              f(pick, 'crisp'), f(cents, '12.6'), f(send, 'loud'), f(send, ''), f(send, '1e1')];""")
    assert out[:8] == ["−6 dB", "−6 dB", "−6 dB", "0.0 dB", "−60.0 dB", "440 Hz", "Crisp", "13"], \
        "-inf on a gain whose range stops at 0.001 is clamped there"
    assert out[8] == "refused: “loud” is not a value in dB" and out[9] == "refused: Type a value."
    assert out[10] == "10 dB"


def test_a_volume_runs_from_off_to_plus_six_and_typed_as_db(blank):
    out = rigjs(blank, """
      const c = R.masterControl({parameter_info: {master: {gain: {automatable: true, value: 0.5, min_domain_value: -120, max_domain_value: 24}}}});
      const p = (t) => { const r = R.parseVolume(t); return r.ok ? R.volumeText(r.db) : 'refused'; };
      return {range: [R.VOLUME_MIN_DB, R.VOLUME_MAX_DB, R.VOLUME_OFF_SLIDER],
        db: [R.volumeDb(c, R.volumeValueOf(c, -6)), R.volumeDb(c, 0) === -Infinity, R.volumeValueOf(c, -Infinity)],
        text: [R.volumeText(0), R.volumeText(-6), R.volumeText(-Infinity), R.volumeText(null)],
        typed: [p('-6'), p('−6 dB'), p('-inf'), p('mute'), p('-80'), p('12'), p('loud')],
        slider: [R.volumeSlider(-Infinity), R.volumeSlider(-80), R.volumeSlider(3), R.volumeOfSlider(-61), R.volumeOfSlider(-60.2)],
        steps: [R.volumeStepped(-Infinity, 1).db, R.volumeStepped(-60, -1).db, R.volumeStepped(-3, 1).db,
                R.volumeStepped(-3, 1, true).db, R.volumeStepped(6, 1).db],
        outside: [R.volumeStepped(10, 1).db, R.volumeStepped(10, -1).db, R.volumeStepped(-80, 1).db,
                  R.volumeStepped(-80, -1).db, R.volumeSlider(-80), R.volumeSlider(10)],
        sticky: [R.volumeSticky(-2.9).db, R.volumeSticky(-2.7, -3).db, R.volumeSticky(-2.6, -3).db]};""")
    assert out["range"] == [-60, 6, -61]
    assert out["db"][0] == pytest.approx(-6) and out["db"][1] is True and out["db"][2] == 0
    assert out["text"] == ["0.0 dB", "−6.0 dB", "−∞ dB", "—"]
    assert out["typed"] == ["−6.0 dB", "−6.0 dB", "−∞ dB", "−∞ dB", "−60.0 dB", "6.0 dB", "refused"]
    assert out["slider"] == [-61, -60, 3, float("-inf"), -60], "only -∞ sits on the -∞ notch"
    assert out["steps"] == [-60, float("-inf"), -2.5, pytest.approx(-2.95), 6], "below -60 is off"
    assert out["sticky"] == [-3, -3, -2.6]
    # Outside the fader's range a step never moves against its direction
    # (review, 2026-11-07): up from +10 stays, down steps toward the range;
    # up from -80 steps toward it, down is -∞; the fader shows each at its end.
    assert out["outside"] == [10, 9.5, -79.5, float("-inf"), -60, 6]


def test_a_channel_a_blend_drives_is_said_by_its_control(blank):
    out = rigjs(blank, """
      const a = {ok: true, body: {blended: {board: 'expression'}}};
      return [R.blendedBy(a, 'board'), R.blendedBy(a, 'vocal'), R.blendedBy(null, 'board'), R.blendedBy({ok: false}, 'board')];""")
    assert out == ["expression", None, None, None]


def test_the_revert_list_says_when_and_how_long_ago(blank):
    out = rigjs(blank, """
      const now = Date.parse('2026-11-06T12:00:00Z');
      const answer = {ok: true, body: {rig: 'electric_board', keep: 50, kept_in: '.sushi-rig/saves/electric_board', running: true,
        current: {kept_as: 's2', git: 'changed'}, git_note: null, problems: [],
        points: [{id: 's2', kind: 'save', at: '2026-11-06T11:55:00Z', how: 'updated'},
                 {id: 's1', kind: 'save', at: '2026-11-06T09:00:00Z', how: 'updated'},
                 {id: 'commit:' + 'a'.repeat(40), kind: 'commit', sha: 'a'.repeat(40), at: '2026-11-03T12:00:00Z', subject: 'Tune it'}]}};
      const rows = R.revertRows(answer, now, (iso) => iso.slice(11, 16));
      return {rows: rows.rows.map((r) => [r.title, r.sub, r.current]), notes: rows.notes,
        first: R.revertConfirm(rows.rows[1], answer, 0, 'electric_board', (iso) => iso.slice(11, 16)),
        unsaved: R.revertConfirm(rows.rows[1], answer, 2, 'electric_board'),
        changed: R.revertConfirm(rows.rows[2], {ok: true, body: {...answer.body, current: {kept_as: null, git: 'changed'}}}, 0, 'electric_board'),
        call: [R.revertCall('electric_board', 's1'), R.revertCall('electric_board', 's1', true)],
        ago: [R.agoText('2026-11-06T11:59:30Z', now), R.agoText('2026-11-06T10:00:00Z', now), R.agoText('x', now)]};""")
    assert out["rows"] == [["Saved 11:55", "5 min ago", True], ["Saved 09:00", "3 h ago", False],
                           ["Commit aaaaaaa", "Tune it · 3 days ago", False]]
    assert out["notes"] == ["The newest 50 saves of electric_board are kept, in .sushi-rig/saves/electric_board/, which git ignores."]
    first = out["first"]
    assert first["title"] == "Revert electric_board to the save of 09:00 (3 h ago)?"
    assert first["lost"] == ["Nothing of the file: as it is now it is kept as the save of 11:55, so you can come back to it."]
    assert first["go"] == "Revert now" and first["discard"] is False and "live where it can be" in first["plays"]
    assert out["unsaved"]["lost"][0] == "2 unsaved changes playing now, made since the last save."
    assert out["unsaved"]["go"] == "Revert, dropping 2 unsaved changes" and out["unsaved"]["discard"] is True
    assert out["changed"]["lost"] == ["The file as it is now: it is in no save or commit (it changed since, by an edit or by hand), so it is lost."]
    assert out["call"] == [{"path": "/rigs/electric_board/revert", "json": {"save": "s1"}},
                           {"path": "/rigs/electric_board/revert", "json": {"save": "s1", "discard": True}}]
    assert out["ago"] == ["just now", "2 h ago", None]


def test_a_reverts_answer_says_how_the_rig_was_made_to_play_it(blank):
    out = rigjs(blank, """
      const lib = {commit: 'b'.repeat(40), file: 'unchanged', others: false};
      const ok = (body) => R.revertOutcome('electric_board', {ok: true, body});
      const base = {file: 'config/rigs/electric_board.yaml', point: {what: 'as the save of 09:00 wrote it'}, library: lib};
      return [ok({...base, running: true, how: 'live', seconds: 0.4}), ok({...base, running: true, how: 'restart', seconds: 6.2,
        why: [{code: 'wiring', detail: 'board is wired to another role'}], discarded: true}), ok({...base, running: false}),
        R.revertOutcome('electric_board', {ok: false, reason: 'busy', code: 'unsaved', body: {}}),
        R.revertOutcome('electric_board', {ok: false, reason: 'no', code: 'not_switched', body: {written: true}})];""")
    assert out[0]["text"] == ("config/rigs/electric_board.yaml is as the save of 09:00 wrote it again, byte for byte. "
                              "The rig was switched to it live in 0.4 s: Sushi was not restarted.")
    assert "by restart in 6.2 s, because wiring: board is wired to another role. The unsaved changes were dropped." in out[1]["text"]
    assert "is not running: it plays as reverted when it is next started" in out[2]["text"]
    assert out[2]["commit"].startswith("Nothing to commit: the file is as commit bbbbbbb has it.")
    assert (out[3]["ok"], out[3]["code"], out[3]["title"]) == (False, "unsaved", "Not reverted: electric_board")
    assert out[4]["title"] == "Reverted the file, but electric_board is not playing it"


@pytest.mark.parametrize("rig, said", [
    ({"running": False}, "No rig is running: start a rig of channels from the picker to add a channel or change an input, or use New rig…"),
    ({"running": True, "config_name": "guitar_vocal", "rig": None},
     "guitar_vocal is a v1 config (started with `sushi-rig up` without `--rig`), which has no rig file to edit: "
     "start it from the picker, or make a rig with New rig…"),
])
def test_the_inputs_say_why_they_cannot_be_edited(blank, rig, said):
    out = rigjs(blank, "return R.wiringView({ok: true, body: {rig: arg, agent: {}}}, null, null);", rig)
    assert out == {"blocked": said}


# --------------------------------------------------------------------------- #
# The rack and the stage, in Chromium
# --------------------------------------------------------------------------- #

def rack_page(browser, served, width=1280):
    page = Page(browser, served.url + "/#rack", width=width)
    page.page.wait_for_selector(".unit")
    page.page.wait_for_selector(".strip .volume")
    return page


def rest_on(p, box):
    """The pointer on a control's middle, resting: the wheel is the control's
    only once the pointer has rested on it (R.takesWheel)."""
    p.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    time.sleep(0.4)


def drive_value(served):
    return served.rig.info["overdrive"]["Drive"]["value"]


def boxes(p):
    """Every element's box on the rack, by a path of its own, and how many
    there are: what a value changing must leave as it was."""
    return p.evaluate("""() => {
      const path = (e) => { const parts = []; for (; e && e !== document.body; e = e.parentElement) {
        const i = e.parentElement ? [...e.parentElement.children].indexOf(e) : 0; parts.unshift(e.tagName + i); } return parts.join('/'); };
      const out = {};
      for (const e of document.querySelectorAll('#app *')) {
        if (e.closest('svg') && e.tagName !== 'svg') continue;
        const b = e.getBoundingClientRect();
        out[path(e)] = [Math.round(b.x), Math.round(b.y), Math.round(b.width), Math.round(b.height)];
      }
      return out; }""")


def test_nothing_comes_or_goes_or_moves_when_values_change(browser):
    """The player's first request: labels that popped in and out as values
    changed. A knob turned, a level and the master moved, a bypass, the
    Save count going from none to some: every element's box is where it was,
    and no element came or went."""
    with Served(save=True) as served:
        page = rack_page(browser, served)
        p = page.page
        time.sleep(1.2)  # the load tiles and everything read once
        before = boxes(p)
        drive = slider(p, "overdrive Drive")
        drive.focus()
        for _ in range(12):
            p.keyboard.press("ArrowDown")
        p.get_by_role("textbox", name="vocal volume value").fill("-18.5")
        p.keyboard.press("Enter")
        p.get_by_role("textbox", name="Master volume value").fill("-12")
        p.keyboard.press("Enter")
        p.get_by_role("textbox", name="delay Feedback value", exact=True).fill("0.9")
        p.keyboard.press("Enter")
        p.locator("body").click(position={"x": 5, "y": 5})
        until(lambda: abs(drive_value(served) - 0.50) < 1e-9)
        unsaved_count(p, 4)
        expect(slider(p, "overdrive Drive")).to_have_attribute("aria-valuetext", "0.50")
        time.sleep(1.2)  # a poll after the changes
        after = boxes(p)
        assert sorted(after) == sorted(before), "nothing came or went"
        moved = {k: (before[k], after[k]) for k in before if before[k] != after[k]}
        assert moved == {}, moved
        page.close()


def test_a_drag_of_n_pixels_moves_by_n_over_the_sweep_and_shift_by_a_tenth(browser, served):
    page = rack_page(browser, served)
    p = page.page
    box = slider(p, "overdrive Drive").locator("svg").bounding_box()
    x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    p.mouse.move(x, y)
    p.mouse.down()
    p.mouse.move(x, y - 32, steps=4)  # 32 of 320 px: a tenth of the travel
    p.mouse.up()
    expect(slider(p, "overdrive Drive")).to_have_attribute("aria-valuetext", "0.72")
    until(lambda: abs(drive_value(served) - 0.72) < 1e-6)
    # Leaving the knob mid-drag does not drop it: the pointer is captured.
    p.mouse.move(x, y)
    p.mouse.down()
    p.mouse.move(x + 300, y + 64, steps=8)
    p.mouse.up()
    until(lambda: abs(drive_value(served) - 0.52) < 1e-6)
    # Shift: a tenth of the speed, 64 px is 0.02, and no snapping.
    p.mouse.move(x, y)
    p.keyboard.down("Shift")
    p.mouse.down()
    p.mouse.move(x, y - 64, steps=8)
    p.mouse.up()
    p.keyboard.up("Shift")
    expect(slider(p, "overdrive Drive")).to_have_attribute("aria-valuetext", "0.54")
    page.close()


def test_a_drag_holds_a_whole_number_for_a_little_further(browser, served):
    """Level (-20 to +4) at -3.0: 320 px is 24 units, so 4 px up is +0.3,
    still held on -3; 11 px up is +0.825, let go."""
    page = rack_page(browser, served)
    p = page.page
    level = slider(p, "overdrive Level")
    box = level.locator("svg").bounding_box()
    x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    p.mouse.move(x, y)
    p.mouse.down()
    p.mouse.move(x, y - 1)  # +0.075: caught
    p.mouse.move(x, y - 4, steps=2)  # +0.3: held
    expect(level).to_have_attribute("aria-valuetext", "−3.0")
    p.mouse.move(x, y - 11, steps=4)  # +0.825: let go
    expect(level).to_have_attribute("aria-valuetext", "−2.2")
    p.mouse.move(x, y - 26, steps=4)  # +1.95: within 0.15 of -1, caught there
    expect(level).to_have_attribute("aria-valuetext", "−1.0")
    p.mouse.up()
    page.close()


def test_the_wheel_steps_a_knob_a_notch_at_a_time_and_only_scrolls_it(browser, served):
    page = rack_page(browser, served, width=1280)
    p = page.page
    level = slider(p, "overdrive Level")
    box = level.locator("svg").bounding_box()
    rest_on(p, box)
    scroll = p.evaluate("() => document.scrollingElement.scrollTop")
    p.mouse.wheel(0, -100)
    expect(level).to_have_attribute("aria-valuetext", "−2.8")
    p.mouse.wheel(0, -100)
    p.mouse.wheel(0, -100)
    expect(level).to_have_attribute("aria-valuetext", "−2.4")
    p.keyboard.down("Shift")
    p.mouse.wheel(0, 100)
    p.keyboard.up("Shift")
    expect(level).to_have_attribute("aria-valuetext", "−2.4")  # -2.42: shown to a tenth
    assert p.evaluate("() => document.scrollingElement.scrollTop") == scroll, "the page did not scroll"
    until(lambda: abs(served.rig.info["overdrive"]["Level"]["value"] - (-2.42 + 20) / 24) < 1e-6)
    page.close()


def test_the_keys_step_and_go_to_the_ends(browser, served):
    page = rack_page(browser, served)
    p = page.page
    level = slider(p, "overdrive Level")
    level.focus()
    p.keyboard.press("PageUp")
    expect(level).to_have_attribute("aria-valuetext", "−1.0")
    p.keyboard.press("End")
    expect(level).to_have_attribute("aria-valuetext", "4.0")
    p.keyboard.press("Home")
    expect(level).to_have_attribute("aria-valuetext", "−20.0")
    page.close()


def test_a_value_is_typed_with_enter_escape_puts_it_back_and_nonsense_is_refused_in_place(browser, served):
    page = rack_page(browser, served)
    p = page.page
    field = p.get_by_role("textbox", name="overdrive Level value")
    field.click()
    assert p.evaluate("() => [document.activeElement.selectionStart, document.activeElement.selectionEnd]") == [0, 4], \
        "a click selects what is there"
    p.keyboard.type("-7.25 dB")
    p.keyboard.press("Enter")
    until(lambda: abs(served.rig.info["overdrive"]["Level"]["value"] - (-7.25 + 20) / 24) < 1e-6)
    expect(field).to_have_value("−7.3")  # sent exact, not snapped; shown to a tenth, as the knob shows it
    expect(slider(p, "overdrive Level")).to_have_attribute("aria-valuetext", "−7.3")
    writes = len(served.rig.writes())
    field.fill("loud")
    p.keyboard.press("Enter")
    expect(field).to_have_class(re.compile(r"\bbad\b"))
    expect(field).to_have_attribute("aria-invalid", "true")
    expect(field).to_have_attribute("title", "“loud” is not a value")
    expect(field).to_have_value("loud")
    time.sleep(0.3)
    assert len(served.rig.writes()) == writes, "nothing was sent"
    p.keyboard.press("Escape")
    expect(field).to_have_value("−7.3")
    expect(field).not_to_have_class(re.compile(r"\bbad\b"))
    # Clamped to the range; blur commits.
    field.fill("99")
    p.locator("body").click(position={"x": 5, "y": 5})
    until(lambda: served.rig.info["overdrive"]["Level"]["value"] == 1)
    expect(field).to_have_value("4.0")
    page.close()


def test_a_focused_field_is_not_overwritten_by_a_poll(browser, served):
    page = rack_page(browser, served)
    p = page.page
    field = p.get_by_role("textbox", name="overdrive Level value")
    field.click()
    p.keyboard.type("-5")
    post(served, "/live/parameter", {"processor": "overdrive", "parameter": "Level", "value": 0.5})
    time.sleep(2.2)  # two polls
    expect(field).to_have_value("-5")
    p.keyboard.press("Escape")
    expect(field).to_have_value("−8.0")
    page.close()


def test_the_amp_fader_takes_a_typed_value_and_the_wheel(browser):
    with Served(amp=True) as served:
        page = Page(browser, served.url + "/#rack")
        p = page.page
        field = p.get_by_role("textbox", name="amp Output Lvl value")
        expect(field).to_have_value("−3.0 dB")
        field.click()
        p.keyboard.type("-6.5")
        p.keyboard.press("Enter")
        until(lambda: abs(served.amp.carla.values().get("Output Lvl", 0) - -6.5) < 1e-6)
        fader = p.get_by_role("slider", name="amp Output Lvl")
        box = fader.bounding_box()
        rest_on(p, box)
        p.mouse.wheel(0, -100)
        # Off the 0.2 dB grid, the first notch lands on it.
        until(lambda: served.amp.carla.values().get("Output Lvl", 0) == -6.4)
        expect(fader).to_have_attribute("aria-valuetext", "−6.4 dB")
        page.close()


def test_the_master_volume_is_on_the_racks_header_and_saved_as_the_masters_level(browser):
    import yaml

    with Served(save=True) as served:
        page = rack_page(browser, served)
        p = page.page
        master = p.locator("header.bar").get_by_role("group", name="Master volume")
        field = master.get_by_role("textbox", name="Master volume value")
        expect(field).to_have_value("0.0 dB")
        field.click()
        p.keyboard.type("-9")
        p.keyboard.press("Enter")
        until(lambda: abs(served.rig.info["master"]["gain"]["value"] - 111 / 144) < 1e-6)
        unsaved_count(p, 1)
        expect(p.locator("button.undo")).to_have_text("Undo · master · gain")
        # The wheel, a notch at a time, and down to off.
        fader = master.get_by_role("slider", name="Master volume")
        box = fader.bounding_box()
        rest_on(p, box)
        p.mouse.wheel(0, -100)
        expect(field).to_have_value("−8.5 dB")
        p.get_by_role("button", name="Save to electric_board").click()
        expect(p.locator(".save-result.ok")).to_contain_text("Saved electric_board")
        saved = yaml.safe_load((served.save.root / "config" / "rigs" / "electric_board.yaml").read_text())
        assert saved["master"]["level"] == -8.5
        field.fill("mute")
        p.keyboard.press("Enter")
        until(lambda: served.rig.info["master"]["gain"]["value"] == 0)
        expect(field).to_have_value("−∞ dB")
        expect(fader).to_have_value("-61")
        p.locator("button.undo").click()
        until(lambda: abs(served.rig.info["master"]["gain"]["value"] - 111.5 / 144) < 1e-6)
        page.close()


def test_the_master_and_each_channels_volume_work_on_the_stage_while_locked(browser, served):
    page = Page(browser, served.url + "/#stage", width=390, height=844)
    p = page.page
    p.wait_for_selector(".card")
    expect(p.locator("button.lock")).to_have_text("Locked")
    master = p.locator("header.stage-head").get_by_role("group", name="Master volume")
    expect(master).to_be_visible()
    field = master.get_by_role("textbox", name="Master volume value")
    field.click()
    p.keyboard.type("-3")
    p.keyboard.press("Enter")
    until(lambda: abs(served.rig.info["master"]["gain"]["value"] - 117 / 144) < 1e-6)
    vocal = p.get_by_role("textbox", name="vocal volume value")
    expect(vocal).to_have_value("−6.0 dB")
    rest_on(p, p.locator(".levels").get_by_role("slider", name="vocal volume").bounding_box())
    p.mouse.wheel(0, 100)
    until(lambda: abs(served.rig.info["vocal_send"]["gain"]["value"] - 113.5 / 144) < 1e-6)
    expect(vocal).to_have_value("−6.5 dB")
    width = p.evaluate("() => [document.documentElement.scrollWidth, innerWidth]")
    assert width[0] <= width[1], "it fits a phone"
    # No confirm was asked for, and the lock is still on.
    expect(p.locator(".confirm")).to_have_count(0)
    expect(p.locator("button.lock")).to_have_text("Locked")
    page.close()


def test_the_rack_is_a_mixing_desk_strips_left_chains_middle_files_right(browser, served):
    page = rack_page(browser, served)
    p = page.page
    strips = p.locator("nav.channels .strip")
    expect(strips.locator(".strip-name")).to_have_text(["board", "vocal", "master"])
    # The master's strip leads to its chain; its volume is the header's.
    master = p.locator('nav.channels .strip-master')
    expect(master.get_by_role("link", name="master")).to_have_attribute("href", "#t-master")
    expect(master).to_contain_text("Its volume is the master's, in the header.")
    expect(master.get_by_role("slider")).to_have_count(0)
    board = strips.first
    expect(board.get_by_role("textbox", name="board volume value")).to_have_value("0.0 dB")
    expect(board.get_by_role("slider", name="board volume")).to_be_visible()
    files = p.locator(".files")
    for what in (files.get_by_role("button", name=re.compile("^Stop ")), files.get_by_role("link", name="New rig…"),
                 files.get_by_role("button", name=re.compile("^Save to ")), files.get_by_role("button", name="Revert…"),
                 files.locator(".takes")):
        expect(what).to_be_visible()
    left = p.locator("nav.channels").bounding_box()
    middle = p.locator(".rack-main").bounding_box()
    right = files.bounding_box()
    assert left["x"] < middle["x"] < right["x"] and middle["width"] > max(left["width"], right["width"])
    # Locked, and the volume still moves: it is not structure.
    expect(p.locator("button.lock")).to_have_text("Locked")
    field = board.get_by_role("textbox", name="board volume value")
    field.click()
    p.keyboard.type("-4")
    p.keyboard.press("Enter")
    until(lambda: abs(served.rig.info["board_send"]["gain"]["value"] - 116 / 144) < 1e-6)
    page.close()


def test_at_phone_width_the_desk_stacks_strips_then_chains_then_files(browser, served):
    page = Page(browser, served.url + "/#rack", width=390, height=844)
    p = page.page
    p.wait_for_selector(".strip .volume")
    tops = [p.locator(sel).bounding_box()["y"] for sel in ("nav.channels", ".rack-main", ".files")]
    assert tops == sorted(tops)
    width = p.evaluate("() => [document.documentElement.scrollWidth, innerWidth]")
    assert width[0] <= width[1]
    page.close()


def test_a_channel_a_blend_drives_says_so_and_does_not_fight_the_pedal(browser):
    with Served(save=True) as served:
        path = served.save.root / "config" / "rigs" / "electric_board.yaml"
        path.write_text(path.read_text() + "midi:\n  controller: pedalboard\n  controls:\n"
                        "  - control: expression\n    blend: [board, vocal]\n")
        for screen in ("rack", "stage"):
            page = Page(browser, served.url + f"/#{screen}")
            p = page.page
            volume = p.locator(".strips, .levels").get_by_role("group", name="board volume")
            expect(volume).to_contain_text("driven by expression")
            expect(volume.get_by_role("slider")).to_be_disabled()
            field = volume.get_by_role("textbox")
            expect(field).to_have_attribute("readonly", "")
            writes = len(served.rig.writes())
            rest_on(p, volume.get_by_role("slider").bounding_box())
            p.mouse.wheel(0, -100)
            time.sleep(0.3)
            assert len(served.rig.writes()) == writes, "the pedal's, not the page's"
            page.close()


def test_the_inputs_say_why_with_no_rig_and_new_rig_is_there(browser):
    with Served(lifecycle=UiLifecycle(up=None)) as served:
        page = Page(browser, served.url + "/#rack")
        p = page.page
        expect(p.locator(".strips")).to_contain_text("CHANNELS AND INPUTS")
        expect(p.locator(".inputs-why")).to_have_text(
            "No rig is running: start a rig of channels from the picker to add a channel or change an input, or use New rig…")
        expect(p.get_by_role("link", name="New rig…")).to_be_visible()
        master = p.get_by_role("group", name="Master volume")
        expect(master.get_by_role("slider")).to_be_disabled()
        expect(master.get_by_role("textbox")).to_have_value("—")
        page.close()


def test_the_inputs_say_why_for_a_v1_run_and_new_rig_is_there(browser):
    lifecycle = UiLifecycle(up="guitar_vocal")
    describe = describe_up(lifecycle.started, "guitar_vocal")

    def v1():
        return {**describe(), "rig": None}

    with Served(lifecycle=lifecycle, describe=v1) as served:
        page = Page(browser, served.url + "/#rack")
        p = page.page
        expect(p.locator(".inputs-why")).to_have_text(
            "guitar_vocal is a v1 config (started with `sushi-rig up` without `--rig`), which has no rig file to edit: "
            "start it from the picker, or make a rig with New rig…")
        expect(p.get_by_role("link", name="New rig…")).to_be_visible()
        page.close()


def test_revert_is_refused_with_unsaved_work_and_allowed_dropping_it(browser):
    """Save, change, save, change; revert to the first: the page counts the
    unsaved change and names it in the confirm, the agent writes the first
    save back byte for byte, and the result says how the rig was switched."""
    from sushi_rig import saves

    with Served(save=True) as served:
        page = rack_page(browser, served)
        p = page.page
        save = p.get_by_role("button", name="Save to electric_board")
        save.click()
        expect(p.locator(".save-result.ok")).to_contain_text("Saved electric_board")
        rig_path = served.save.root / "config" / "rigs" / "electric_board.yaml"
        first = rig_path.read_bytes()
        p.get_by_role("textbox", name="Master volume value").fill("-6")
        p.keyboard.press("Enter")
        unsaved_count(p, 1)
        save.click()
        unsaved_count(p, 0)
        assert rig_path.read_bytes() != first
        p.get_by_role("textbox", name="Master volume value").fill("-9")
        p.keyboard.press("Enter")
        unsaved_count(p, 1)
        p.get_by_role("button", name="Revert…").click()
        rows = p.locator(".revert-point")
        expect(rows).to_have_count(3)  # two saves and the commit
        expect(rows.nth(0)).to_contain_text("the file now")
        expect(rows.nth(2)).to_contain_text("Commit")
        rows.nth(1).click()
        confirm = p.locator(".revert-confirm")
        expect(confirm).to_contain_text("Revert electric_board to the save of")
        expect(confirm).to_contain_text("1 unsaved change playing now, made since the last save.")
        expect(confirm).to_contain_text("kept as the save of")
        # The agent decides too: asked without discard, it refuses.
        status = p.evaluate("""async (id) => (await fetch('/rigs/electric_board/revert',
          {method: 'POST', headers: {'content-type': 'application/json'}, body: JSON.stringify({save: id})})).status""",
                            rows.nth(1).get_attribute("data-point"))
        assert status == 409
        assert rig_path.read_bytes() != first, "refused: nothing written"
        confirm.get_by_role("button", name="Revert, dropping 1 unsaved change").click()
        result = p.locator(".save-result.ok")
        expect(result).to_contain_text("Reverted electric_board")
        expect(result).to_contain_text("byte for byte")
        expect(result).to_contain_text("switched to it live")
        expect(result).to_contain_text("The unsaved changes were dropped.")
        assert rig_path.read_bytes() == first
        listed, _ = saves.snapshots(served.save.root, "electric_board")
        assert saves.snapshot_bytes(served.save.root, "electric_board", listed[-1]["id"]) == first
        unsaved_count(p, 0)  # the revert is the new baseline
        page.close()


def test_the_page_and_its_files_are_revalidated_on_every_load(served):
    import urllib.request

    for path in ("/", "/ui/app.js", "/ui/rig.js", "/ui/style.css"):
        with urllib.request.urlopen(served.url + path) as response:
            assert response.headers["Cache-Control"] == "no-cache", path
            assert response.headers["ETag"], path
    with urllib.request.urlopen(served.url + "/health") as response:
        assert response.headers["Cache-Control"] is None, "only the UI's files"


# --------------------------------------------------------------------------- #
# The compiler's copies to stereo, and the mono badge
# --------------------------------------------------------------------------- #

MVERB = "http://distrho.sf.net/plugins/MVerb"  # 2 2 in plugin-ports.txt: stereo


def copies_rig(served):
    """The fake board with a stereo reverb first and the compiler's copy to
    stereo after the overdrive (gxts9, 1 1 in plugin-ports.txt), as `up
    --rig` builds a mono plugin's: invented, as the rest of the fake rig is."""
    from sushi_rig.stereo import COPY_UID

    rig = served.rig
    rig.chains["board"] = ["verb", "overdrive", "overdrive_to_stereo", "octave", "delay", "board_send"]
    for name, plugin in (("verb", {"uri": MVERB, "uid": None, "type": "lv2"}),
                         ("overdrive_to_stereo", {"uri": None, "uid": COPY_UID, "type": "internal"})):
        rig.info[name], rig.bypassed[name], rig.classes[name] = {}, False, plugin


def test_rig_js_knows_a_copy_by_its_uid_its_suffix_or_its_role_whatever_role_comes_with_it(blank):
    out = rigjs(blank, """return [
      R.roleOf('board', 'comp_to_stereo', {role: null}), R.roleOf('board', 'odd', {role: null, uid: R.COPY_UID}),
      R.roleOf('board', 'odd', {role: 'stereo'}), R.roleOf('board', 'comp', {role: null}),
      R.roleOf('board', 'board_send', null), R.roleOf('board', 'comp_to_stereo', null)];""")
    assert out == ["stereo", "stereo", "stereo", None, "send", "stereo"]


def test_the_mono_note_says_mono_and_warns_after_a_stereo_plugin(blank):
    out = rigjs(blank, """
      const u = (name, mono, width) => ({name, mono, width});
      const chain = [u('verb', false, 'stereo'), u('comp', true, 'mono'), u('drive', true, 'mono'), u('eq', false, null), u('late', true, 'mono')];
      return chain.map((_, i) => R.monoNote(chain, i));""")
    assert out[0] is None and out[3] is None
    assert out[1] == {"badge": "mono", "warn": True, "title": "after verb: this collapses its stereo width"}
    assert out[2] == {"badge": "mono", "warn": False,
                      "title": "mono: processes the left side and its output is copied to both"}, \
        "nothing stereo left to collapse once a mono plugin has"
    assert out[4]["warn"] is False


def test_live_plugins_says_each_plugins_width_from_the_ports_table(served):
    import json
    import urllib.request

    copies_rig(served)
    with urllib.request.urlopen(served.url + "/live/plugins") as response:
        processors = json.loads(response.read())["processors"]
    assert processors["verb"]["width"] == "stereo" and processors["overdrive"]["width"] == "mono"
    assert processors["delay"]["width"] == "stereo"  # mda Delay, 2 2
    assert processors["board_send"]["width"] is None, "not an LV2 plugin the table lists: not said"
    assert processors["overdrive_to_stereo"]["role"] == "stereo"


@pytest.mark.parametrize("screen", ["rack", "stage"])
def test_a_copy_given_a_null_role_is_nowhere_and_its_plugin_is_marked_mono(browser, served, screen):
    """/live/plugins is made to say the copy is a plain plugin (role null, no
    uid): it is still recognised by its reserved suffix, shown nowhere on
    either screen, and its plugin carries the badge, a warning here since a
    stereo reverb comes first."""
    import json

    copies_rig(served)
    page = Page(browser, served.url + f"/#{screen}", width=1280 if screen == "rack" else 390)
    p = page.page

    def null_role(route):
        response = route.fetch()
        body = response.json()
        body["processors"]["overdrive_to_stereo"].update(role=None, uid=None)
        route.fulfill(response=response, body=json.dumps(body))

    p.route("**/live/plugins", null_role)
    p.reload()
    p.wait_for_selector(".unit" if screen == "rack" else ".card")
    expect(p.locator(f'[data-unit="verb"]')).to_have_count(1)
    expect(p.get_by_text("overdrive_to_stereo")).to_have_count(0)
    expect(p.locator('[data-unit="overdrive_to_stereo"]')).to_have_count(0)
    assert "overdrive_to_stereo" not in p.locator("#app").inner_text()
    badge = p.locator('[data-unit="overdrive"] .mono-badge')
    expect(badge).to_have_text("mono")
    expect(badge).to_have_attribute("title", "after verb: this collapses its stereo width")
    expect(badge).to_have_class(re.compile(r"\bwarn\b"))
    expect(p.locator('[data-unit="verb"] .mono-badge')).to_have_count(0)
    page.close()


def test_a_mono_plugin_with_nothing_stereo_before_it_is_marked_mono_quietly(browser, served):
    copies_rig(served)
    served.rig.chains["board"].remove("verb")
    page = rack_page(browser, served)
    badge = page.page.locator('[data-unit="overdrive"] .mono-badge')
    expect(badge).to_have_attribute("title", "mono: processes the left side and its output is copied to both")
    expect(badge).not_to_have_class(re.compile(r"\bwarn\b"))
    page.close()


# --------------------------------------------------------------------------- #
# Review of the branch (2026-11-07)
# --------------------------------------------------------------------------- #

def test_a_field_left_untouched_never_sends_its_stale_text(browser, served):
    """Focused, the field is not written by a poll; left without typing, it
    shows the value as it is now and sends nothing (it sent its old text,
    undoing the change made meanwhile)."""
    page = rack_page(browser, served)
    p = page.page
    field = p.get_by_role("textbox", name="overdrive Level value")
    field.click()
    post(served, "/live/parameter", {"processor": "overdrive", "parameter": "Level", "value": 0.5})  # -8.0, from elsewhere
    time.sleep(1.2)
    expect(field).to_have_value("−3.0")  # not overwritten while it has the focus
    writes = len(served.rig.writes())
    p.locator("body").click(position={"x": 5, "y": 5})
    expect(field).to_have_value("−8.0")
    time.sleep(0.4)
    assert len(served.rig.writes()) == writes and served.rig.info["overdrive"]["Level"]["value"] == 0.5
    # Enter with nothing typed is the same: the value now, nothing sent.
    field.click()
    post(served, "/live/parameter", {"processor": "overdrive", "parameter": "Level", "value": 0.25})  # -14.0
    time.sleep(1.2)
    p.keyboard.press("Enter")
    expect(field).to_have_value("−14.0")
    time.sleep(0.4)
    assert served.rig.info["overdrive"]["Level"]["value"] == 0.25
    # The wheel on the dial while the field has the focus: the scroll stands.
    field.click()
    rest_on(p, slider(p, "overdrive Level").locator("svg").bounding_box())
    p.mouse.wheel(0, -100)
    expect(field).to_have_value("−13.8")
    p.locator("body").click(position={"x": 5, "y": 5})
    time.sleep(0.4)
    assert served.rig.info["overdrive"]["Level"]["value"] == pytest.approx((-13.8 + 20) / 24)
    page.close()


def test_a_scroll_down_the_rack_passes_over_knobs_and_a_rested_one_turns(browser, served):
    page = Page(browser, served.url + "/#rack", width=1280, height=600)
    p = page.page
    p.wait_for_selector(".strip .volume")
    p.evaluate("""() => { window.prevented = []; window.addEventListener('wheel',
      (e) => window.prevented.push(e.defaultPrevented), {passive: true}); }""")
    dial = slider(p, "overdrive Drive").locator("svg").bounding_box()
    # Start scrolling just above the knob, so it passes under the pointer.
    p.mouse.move(dial["x"] + dial["width"] / 2, dial["y"] - 30)
    time.sleep(0.4)
    before = p.evaluate("() => document.scrollingElement.scrollTop")
    for _ in range(4):
        p.mouse.wheel(0, 40)
        time.sleep(0.05)
    time.sleep(0.3)
    assert p.evaluate("() => document.scrollingElement.scrollTop") > before, "the page scrolled"
    assert p.evaluate("() => window.prevented") == [False] * 4, "no wheel was taken from the page"
    assert served.rig.writes() == [], "and nothing on the rig was turned"
    # Deliberate: the pointer rests on a dial, then scrolls it.
    rest_on(p, slider(p, "overdrive Drive").locator("svg").bounding_box())
    p.mouse.wheel(0, -100)
    expect(slider(p, "overdrive Drive")).to_have_attribute("aria-valuetext", "0.63")
    assert p.evaluate("() => window.prevented.at(-1)") is True
    page.close()


def test_a_wheel_over_a_knobs_label_or_text_is_the_pages(browser, served):
    page = rack_page(browser, served)
    p = page.page
    p.evaluate("""() => { window.prevented = []; window.addEventListener('wheel',
      (e) => window.prevented.push(e.defaultPrevented), {passive: true}); }""")
    for part in (".label", ".value-field"):
        rest_on(p, p.locator(f'.unit[data-unit="overdrive"] .knob[data-control$="Drive"] {part}').bounding_box())
        p.mouse.wheel(0, -100)
    time.sleep(0.3)
    assert p.evaluate("() => window.prevented") == [False, False]
    assert served.rig.writes() == []
    page.close()


def test_a_blended_faders_wheel_is_the_pages(browser):
    with Served(save=True) as served:
        path = served.save.root / "config" / "rigs" / "electric_board.yaml"
        path.write_text(path.read_text() + "midi:\n  controller: pedalboard\n  controls:\n"
                        "  - control: expression\n    blend: [board, vocal]\n")
        page = rack_page(browser, served)
        p = page.page
        volume = p.locator(".strips").get_by_role("group", name="board volume")
        expect(volume).to_contain_text("driven by expression")
        p.evaluate("""() => { window.prevented = []; window.addEventListener('wheel',
          (e) => window.prevented.push(e.defaultPrevented), {passive: true}); }""")
        rest_on(p, volume.get_by_role("slider").bounding_box())
        p.mouse.wheel(0, -100)
        time.sleep(0.3)
        assert p.evaluate("() => window.prevented") == [False], "not settable: never taken"
        page.close()


def test_a_strips_fader_keeps_its_focus_and_element_through_a_busy_agent(browser):
    """What changed around the strips (the agent busy, an edit's answer, a
    form opened) rebuilt them, ending a drag and taking the focus."""
    import threading
    import urllib.request

    with Served(save=True) as served:
        page = rack_page(browser, served)
        p = page.page
        fader = p.locator("nav.channels").get_by_role("slider", name="vocal volume")
        fader.focus()
        handle = fader.element_handle()
        served.lifecycle.delay = 2.5

        def busy():
            request = urllib.request.Request(served.url + "/rig/up", data=b'{"config_name": "electric_board", "rig": true}',
                                             headers={"Content-Type": "application/json"}, method="POST")
            try:
                urllib.request.urlopen(request, timeout=10).read()
            except Exception:  # noqa: BLE001 -- refused (a rig is up): only the busy time matters
                pass

        thread = threading.Thread(target=busy)
        thread.start()
        expect(p.locator(".rig-busy")).to_contain_text("up is running", timeout=4000)
        p.locator("nav.channels").get_by_role("button", name="Add channel").click()  # the foot changes too
        assert handle.evaluate("(e) => e.isConnected"), "the same fader element"
        handle.focus()
        thread.join()
        served.lifecycle.delay = 0
        p.keyboard.press("ArrowUp")
        until(lambda: abs(served.rig.info["vocal_send"]["gain"]["value"] - 114.5 / 144) < 1e-6)
        assert handle.evaluate("(e) => e.isConnected && document.activeElement === e")
        page.close()


def test_a_copy_known_only_by_its_uid_is_no_chain_change(blank):
    out = rigjs(blank, """
      const log = {log: [{op: 'add', args: {track: 'board', name: 'odd'}, previous: ['comp', 'board_send'], at: '2026-11-07T10:00:00Z'}], first: 0};
      const live = {tracks: {board: ['comp', 'odd', 'board_send']}, processors: {}, parameter_info: {}};
      const plugins = {odd: {uid: R.COPY_UID, role: null}};
      return [R.unsavedChanges(log, live, null, null, plugins).items, R.unsavedChanges(log, live, null, null, null).items.map((i) => i.kind)];""")
    assert out[0] == [], "a copy by its uid is the compiler's: no change"
    assert out[1] == ["added"], "known by nothing, it is a plugin, as before"
