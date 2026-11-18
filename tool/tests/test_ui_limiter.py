"""The master's limiter and the master volume's top on the page (#137).

Two layers, as test_ui.py has them: rig.js's decisions imported into a bare
page, then the rack driven in Chromium against a real agent over the fake rig
of `ui_fake.py`, whose master ends with the compiler's limiter as every
compiled master does. Nothing here is a real Sushi or the rig.
"""

from __future__ import annotations

import pytest

pytest.importorskip("aiohttp")

from test_ui import blank, browser, expect, rigjs, served, until  # noqa: E402,F401
from test_ui_tabs import rack_page, tab  # noqa: E402
from ui_fake import Served  # noqa: E402

from sushi_rig.rigfile import MASTER_GAIN_MAX, level_to_gain  # noqa: E402


def test_the_master_stops_at_unity_and_a_channel_still_reaches_plus_six(blank):
    out = rigjs(blank, """
      const max = R.MASTER_VOLUME_MAX_DB;
      const p = (t, m) => { const r = R.parseVolume(t, m); return r.ok ? [R.volumeText(r.db), r.clamped] : 'refused'; };
      return {max, channel: R.VOLUME_MAX_DB,
        typed: [p('3', max), p('0', max), p('-6', max), p('3')],
        clamp: [R.volumeClamp(4, max), R.volumeClamp(4), R.volumeClamp(-Infinity, max)],
        slider: [R.volumeSlider(3, max), R.volumeOfSlider(5, max), R.volumeSlider(3)],
        steps: [R.volumeStepped(0, 1, false, null, max).db, R.volumeStepped(-0.5, 4, false, null, max).db,
                R.volumeStepped(3, -1, false, null, max).db, R.volumeStepped(3, 1, false, null, max).db,
                R.volumeStepped(0, 1).db]};""")
    assert out["max"] == 0 and out["channel"] == 6
    assert out["typed"] == [["0.0 dB", True], ["0.0 dB", False], ["−6.0 dB", False], ["3.0 dB", False]]
    assert out["clamp"] == [0, 4, float("-inf")]
    assert out["slider"] == [0, 0, 3]
    # Up from the top stays; a master read over it (a rig from before the
    # cap) steps down toward it and never up, as a fader outside its range does.
    assert out["steps"] == [0, 0, 2.5, 3, 0.5]


def test_over_the_masters_top_a_step_up_sends_nothing_and_a_step_down_goes_to_the_top(blank):
    """A rig started before the cap, its master at +6 dB (PR #172's review:
    one notch up used to send the clamped top, 6 dB down)."""
    out = rigjs(blank, """
      const max = R.MASTER_VOLUME_MAX_DB;
      return {up: R.volumeStepped(6, 1, false, null, max, true), down: R.volumeStepped(6, -1, false, null, max, true),
        fine: R.volumeStepped(6, -1, true, null, max, true), at: R.volumeStepped(0, 1, false, null, max, true),
        channel: R.volumeStepped(10, 1), note: R.overTopNote(6, max), none: R.overTopNote(0, max)};""")
    assert out["up"] == {"db": 6, "held": None, "unchanged": True}
    assert out["down"]["db"] == 0 and out["fine"]["db"] == 0 and "unchanged" not in out["down"]
    assert out["at"]["db"] == 0 and "unchanged" not in out["at"], "at the top, a step up is the top"
    assert out["channel"]["unchanged"] is True, "a channel over its range alike sends nothing going up"
    assert out["note"].startswith("at 6.0 dB, over the 0.0 dB it now stops at") and out["none"] is None


def test_the_limiter_is_its_own_line_never_a_unit(blank):
    out = rigjs(blank, """
      const live = {tracks: {board: ['comp', 'board_send'], master: ['master_return', 'room', 'master_limiter']},
                    processors: {}, parameter_info: {master_limiter: {Thresh: {automatable: true, value: 0.975,
                      min_domain_value: 0, max_domain_value: 1}}}};
      const plugins = {master_limiter: {role: 'limiter', uri: 'http://drobilla.net/plugins/mda/Limiter'},
                       master_return: {role: 'return'}, room: {role: null}, comp: {role: null}, board_send: {role: 'send'}};
      const shape = (m) => m.tracks.map((t) => [t.name, t.units.map((u) => u.name), t.limiter && t.limiter.name]);
      const model = R.rackModel(live, plugins, {});
      const bare = R.rackModel(live, null, null);
      const master = model.tracks.find((t) => t.isMaster);
      const old = R.rackModel({tracks: {master: ['master_return']}, processors: {}, parameter_info: {}}, null, null);
      return {model: shape(model), bare: shape(bare),
        roles: [R.roleOf('master', 'master_limiter', null), R.roleOf('board', 'master_limiter', null)],
        slots: R.addSlots(master).map((s) => s.label),
        line: R.limiterLine(master, live), board: R.limiterLine(model.tracks[0], live),
        old: R.limiterLine(old.tracks[0], null),
        bypassed: R.limiterLine({...master, limiter: {...master.limiter, bypassed: true}}, live)};""")
    assert out["model"] == out["bare"] == [["board", ["comp"], None], ["master", ["room"], "master_limiter"]]
    assert out["roles"] == ["limiter", None]
    assert out["slots"] == ["Add at the start…", "Add after room…"], "nothing is offered after the limiter"
    assert out["line"]["present"] is True and out["line"]["text"].startswith("Limiter: mda Limiter at −1.0 dBFS")
    assert "not set from here" in out["line"]["text"]
    assert out["board"] is None
    assert out["old"]["present"] is False and "Restart it to put the limiter in" in out["old"]["text"]
    assert out["bypassed"]["bypassed"] is True and "bypassed" in out["bypassed"]["text"]


def test_the_master_tab_shows_the_limiter_and_the_master_fader_stops_at_unity(browser):
    with Served() as served:
        page = rack_page(browser, served)
        p = page.page
        tab(p, "Master").click()
        line = p.locator(".track .limiter")
        expect(line).to_contain_text("Limiter: mda Limiter at −1.0 dBFS")
        expect(p.locator('.unit[data-unit="master_limiter"]')).to_have_count(0)
        master = p.locator("header.bar").get_by_role("group", name="Master volume")
        fader = master.get_by_role("slider", name="Master volume")
        expect(fader).to_have_attribute("max", "0")
        field = master.get_by_role("textbox", name="Master volume value")
        field.click()
        p.keyboard.type("+4")
        p.keyboard.press("Enter")
        # Typed over the top: held to it, and the agent sent unity, never more.
        until(lambda: abs(served.rig.info["master"]["gain"]["value"] - MASTER_GAIN_MAX) < 1e-6)
        expect(field).to_have_value("0.0 dB")
        field.fill("-6")
        p.keyboard.press("Enter")
        until(lambda: abs(served.rig.info["master"]["gain"]["value"] - level_to_gain(-6.0)) < 1e-6)
        fader.focus()
        p.keyboard.press("End")
        until(lambda: abs(served.rig.info["master"]["gain"]["value"] - MASTER_GAIN_MAX) < 1e-6)
        page.close()


def test_a_master_over_the_top_is_not_turned_down_by_a_step_up(browser):
    hot = level_to_gain(6.0)
    with Served() as served:
        served.rig.info["master"]["gain"]["value"] = hot
        page = rack_page(browser, served)
        p = page.page
        master = p.locator("header.bar").get_by_role("group", name="Master volume")
        field = master.get_by_role("textbox", name="Master volume value")
        expect(field).to_have_value("6.0 dB")
        expect(master).to_contain_text("over the top")
        assert "up does nothing" in master.get_attribute("title")
        fader = master.get_by_role("slider", name="Master volume")
        fader.focus()
        for key in ("ArrowUp", "PageUp", "End"):
            p.keyboard.press(key)
        box = fader.bounding_box()
        p.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
        p.wait_for_timeout(600)  # the wheel waits for a resting pointer
        p.mouse.wheel(0, -100)
        p.wait_for_timeout(800)
        assert served.rig.info["master"]["gain"]["value"] == hot, "nothing was sent"
        assert not [c for c in served.rig.calls if "set_parameter" in str(c[:1]) and "master" in str(c)], \
            served.rig.calls
        fader.focus()
        p.keyboard.press("ArrowDown")
        until(lambda: abs(served.rig.info["master"]["gain"]["value"] - MASTER_GAIN_MAX) < 1e-6)
        expect(field).to_have_value("0.0 dB")
        expect(master).not_to_contain_text("over the top")
        page.close()
