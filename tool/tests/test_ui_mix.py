"""One knob that drives several parameters (#154): a profile's `controls`,
here MaGigaverb's Mix, which sets its dry level against its early
reflections and tail.

Three layers, as test_ui.py has them: the profile flattened for the browser
(pure Python); rig.js's decisions imported into a bare page; and the rack
driven in Chromium against a real agent over the fake rig of `ui_fake.py`,
with a hall added to its board. The hall's ports are MaGigaverb's as probe
read them (`fixtures/probe_MaGigaverb.json`); its values are the rigs' own
saved hall (dry 0.92, early 0.16, tail 0.08), which are not on the Mix's
curve, so the page has to show the nearest point and send nothing until the
knob is turned. Nothing here is a real Sushi.
"""

from __future__ import annotations

import json
import math
import urllib.request

import pytest

pytest.importorskip("aiohttp")

from sushi_rig.library import PROFILES_DIR, load_profiles  # noqa: E402
from sushi_rig.paths import checkout_root  # noqa: E402
from sushi_rig.profile import driven_value  # noqa: E402
from sushi_rig.ui import panel_profile  # noqa: E402

from test_ui import Page, blank, browser, expect, post, rigjs, served, slider, until  # noqa: E402,F401
from ui_fake import FIXTURES, MAGIGAVERB, RIG_FILE, Served, UiRig, info_of, probe_ports  # noqa: E402

# The rigs' hall as saved (normalised; dry, early and tail run 0 to 1, so
# these are their real values too), and roomsize and revtime as real values.
SAVED = {"damping": 0.12, "revtime": 0.1 + 0.01 * 359.9, "roomsize": 0.1 + 0.29 * 299.9, "spread": 0.0,
         "bandwidth": 0.12, "tail": 0.08, "dry": 0.92, "early": 0.16}


def hall_profile():
    profiles, problems = load_profiles(checkout_root() / PROFILES_DIR)
    assert problems == []
    return profiles[MAGIGAVERB]


def drives():
    (mix,) = hall_profile()["controls"]
    return mix["drives"]["value"]


def targets(m, drives_=None):
    """What the Mix sets each level to at travel m, by profile.py's own curves."""
    return {d["parameter"]: driven_value(d, m) for d in (drives_ or drives())}


def nearest(values):
    """The travel nearest `values` (least squares), on a fine grid: what the
    page must show, worked out here without it."""
    held = drives()
    grid = [i / 20000 for i in range(20001)]
    return min(grid, key=lambda m: sum((t - values[p]) ** 2 for p, t in targets(m, held).items()))


def hall_rig() -> UiRig:
    rig = UiRig()
    board = rig.chains["board"]
    board.insert(board.index("board_send"), "hall")
    rig.info["hall"] = info_of(probe_ports(FIXTURES / "probe_MaGigaverb.json"), SAVED)
    rig.bypassed["hall"] = False
    rig.classes["hall"] = {"uri": MAGIGAVERB, "uid": None, "type": "lv2"}
    return rig


# The fake's rig file with the hall in it, at the saved values, so a save
# has a hall to write into.
RIG_FILE_HALL = RIG_FILE.replace("""  - name: delay
    uri: http://drobilla.net/plugins/mda/Delay
""", """  - name: delay
    uri: http://drobilla.net/plugins/mda/Delay
  - name: hall
    uri: http://distrho.sf.net/plugins/MaGigaverb
    parameters:
      damping: 0.12
      revtime: 0.01
      roomsize: 0.29
      spread: 0.0
      bandwidth: 0.12
      tail: 0.08
      dry: 0.92
      early: 0.16
""")
assert RIG_FILE_HALL != RIG_FILE


def hall_calls(served):
    return [c for c in served.rig.calls if c[0] == "set_parameter" and c[1] == "hall"]


def live_log(served):
    with urllib.request.urlopen(served.url + "/live/log", timeout=10) as response:
        return json.loads(response.read())["log"]


# --------------------------------------------------------------------------- #
# The profile, flattened for the browser (pure)
# --------------------------------------------------------------------------- #

def test_the_hall_profile_lays_out_mix_first_then_the_space_then_the_tone():
    flat, problems = panel_profile(hall_profile())
    assert problems == []
    assert flat["groups"] == [{"id": "mix", "label": "Mix"}, {"id": "space", "label": "Space"},
                              {"id": "tone", "label": "Tone"}]
    laid = [(p["name"], p["label"], p["visibility"]) for p in flat["parameters"]]
    assert laid == [
        ("mix", "Mix", "primary"),
        ("roomsize", "Room size", "primary"), ("revtime", "Reverb time", "primary"),
        ("damping", "Damping", "advanced"), ("bandwidth", "Bandwidth", "advanced"), ("spread", "Spread", "advanced"),
        ("dry", "Dry", "hidden"), ("early", "Early", "hidden"), ("tail", "Tail", "hidden"),
    ]
    mix = flat["parameters"][0]
    assert mix["drives"] == [
        {"parameter": "dry", "curve": "cosine", "at_0": 1.0, "at_1": 0.0},
        {"parameter": "early", "curve": "sine", "at_0": 0.0, "at_1": 0.8},
        {"parameter": "tail", "curve": "sine", "at_0": 0.0, "at_1": 0.4},
    ]
    assert all("drives" not in p for p in flat["parameters"][1:]), "only a control carries drives"


def test_a_control_the_panel_cannot_follow_is_left_out_and_said():
    flat, problems = panel_profile({
        "_file": "p.yaml", "plugin": {"uri": "urn:x"}, "groups": [],
        "parameters": [{"name": "A"}],
        "controls": [{"id": "mix", "drives": {"value": [{"parameter": "A", "curve": "sine"}], "provenance": "draft"}},
                     {"drives": {"value": [], "provenance": "draft"}},
                     {"id": "ok", "drives": {"value": [{"parameter": "A", "curve": "sine", "at_0": 0, "at_1": 1}],
                                             "provenance": "draft"}}],
    })
    assert [p["name"] for p in flat["parameters"]] == ["A", "ok"]
    assert problems == ["p.yaml: control mix has no drives the panel can follow and is left out",
                        "p.yaml: control 2 has no id and is left out"]


# --------------------------------------------------------------------------- #
# The decisions, pure (rig.js)
# --------------------------------------------------------------------------- #

def mix_of(page):
    """rig.js's control for the hall's Mix, made from the profile the agent
    serves and the fake hall's parameter info."""
    return {"profile": rigjs(page, "return (await (await fetch('/profiles')).json()).profiles[arg];", MAGIGAVERB),
            "info": hall_rig().info["hall"]}


def test_the_curves_run_from_0_to_1_and_are_profile_pys_own(blank):
    out = rigjs(blank, "return ['linear', 'sine', 'cosine'].map((c) => [0, 0.25, 0.5, 1].map((m) => R.curveAt(c, m)));")
    from sushi_rig.profile import curve_at

    for curve, row in zip(["linear", "sine", "cosine"], out):
        assert row == pytest.approx([curve_at(curve, m) for m in (0, 0.25, 0.5, 1)], abs=1e-12)
    assert out[1][2] == pytest.approx(math.sqrt(0.5)) and out[2][2] == pytest.approx(1 - math.sqrt(0.5))


def test_mix_is_dry_at_0_wet_at_100_and_equal_power_between(blank):
    out = rigjs(blank, """
      const entry = arg.profile.parameters.find((p) => p.name === 'mix');
      const mix = R.makeMix(entry, arg.info);
      const at = (m) => Object.fromEntries(R.mixTargets(mix, m).map((t) => [t.parameter, t.value]));
      return {shown: [mix.label, mix.unit, R.formatValue(mix, 0.25), mix.missing], steps: [0, 0.25, 0.5, 0.75, 1].map(at)};""",
                mix_of(blank))
    assert out["shown"] == ["Mix", "%", "25 %", False]
    zero, *_, full = out["steps"]
    assert zero == pytest.approx({"dry": 1.0, "early": 0.0, "tail": 0.0})
    assert full == pytest.approx({"dry": 0.0, "early": 0.8, "tail": 0.4}, abs=1e-12)
    for step, m in zip(out["steps"], (0, 0.25, 0.5, 0.75, 1)):
        assert step == pytest.approx(targets(m), abs=1e-12), "the page's curve is the profile's"
        # Equal power: the dry's gain and the wet's (its gain at 100 % being 1) square to 1.
        assert step["dry"] ** 2 + (step["early"] / 0.8) ** 2 == pytest.approx(1.0)
        assert step["early"] == pytest.approx(2 * step["tail"]), "early stays twice the tail"


def test_mix_reads_back_where_it_was_set_and_the_saved_hall_at_the_nearest_point(blank):
    out = rigjs(blank, """
      const entry = arg.profile.parameters.find((p) => p.name === 'mix');
      const mix = R.makeMix(entry, arg.info);
      const back = [0, 0.13, 0.5, 0.87, 1].map((m) => {
        const set = Object.fromEntries(R.mixTargets(mix, m).map((t) => [t.parameter, t.value]));
        return R.mixPosition(mix, set);
      });
      return {back, saved: R.mixPosition(mix, {dry: 0.92, early: 0.16, tail: 0.08}),
              unknown: R.mixPosition(mix, {dry: 0.92, early: null, tail: 0.08}), limit: R.MIX_OFF_CURVE};""",
                mix_of(blank))
    for got, m in zip(out["back"], (0, 0.13, 0.5, 0.87, 1)):
        assert got["v"] == pytest.approx(m, abs=1e-6) and got["off"] < 1e-6
    expected = nearest({"dry": 0.92, "early": 0.16, "tail": 0.08})
    assert out["saved"]["v"] == pytest.approx(expected, abs=1e-4)
    assert out["saved"]["off"] > out["limit"], "the saved hall is off the curve, and is said to be"
    assert out["unknown"] is None


def test_the_hall_card_shows_mix_first_room_size_and_reverb_time_and_hides_the_levels(blank):
    out = rigjs(blank, """
      const p = R.unitPanel(arg.info, arg.profile);
      const names = (cs) => cs.map((c) => c.name);
      return {groups: p.groups.map((g) => [g.label, names(g.controls)]), advanced: names(p.advanced), missing: names(p.missing),
              summary: R.summaryOf({name: 'hall', panel: p}, {parameter_info: {hall: arg.info}})};""", mix_of(blank))
    assert out["groups"] == [["Mix", ["mix"]], ["Space", ["roomsize", "revtime"]]]
    assert out["advanced"] == ["damping", "bandwidth", "spread"]
    assert out["missing"] == []
    shown = round(100 * nearest({"dry": 0.92, "early": 0.16, "tail": 0.08}))
    assert out["summary"] == f"Mix {shown} % · Room size 87 · Reverb time 4"


def test_a_mix_whose_parameters_the_plugin_lacks_is_missing_not_drawn(blank):
    info = hall_rig().info["hall"]
    del info["early"]
    out = rigjs(blank, """
      const p = R.unitPanel(arg.info, arg.profile);
      return [p.missing.map((c) => [c.name, c.notes]), p.groups.map((g) => g.label)];""",
                {"profile": mix_of(blank)["profile"], "info": info})
    assert out == [[["mix", ["drives early, which the running plugin lacks"]]], ["Space"]]


# --------------------------------------------------------------------------- #
# The rack, in the browser
# --------------------------------------------------------------------------- #

@pytest.fixture
def hall_served():
    with Served(hall_rig(), save=True, rig_file=RIG_FILE_HALL) as served:
        yield served


def hall_page(browser, served):
    page = Page(browser, served.url + "/#rack")
    page.page.wait_for_selector('.unit[data-unit="hall"]')
    return page


def test_a_page_that_loads_shows_the_nearest_mix_and_moves_nothing(browser, hall_served):
    page = hall_page(browser, hall_served)
    p = page.page
    mix = slider(p, "hall Mix")
    shown = round(100 * nearest({"dry": 0.92, "early": 0.16, "tail": 0.08}))
    expect(mix).to_have_attribute("aria-valuetext", f"{shown} %")
    expect(p.locator('.unit[data-unit="hall"] .knob[data-control$="mix"] .note')).to_have_text(
        "between Mix's settings: shown at the nearest")
    expect(slider(p, "hall Room size")).to_be_visible()
    expect(slider(p, "hall Reverb time")).to_be_visible()
    for hidden in ("Dry", "Early", "Tail", "dry", "early", "tail"):
        expect(slider(p, f"hall {hidden}")).to_have_count(0)
    # Several polls, and still nothing sent: the levels are as saved.
    p.wait_for_timeout(2500)
    assert hall_calls(hall_served) == []
    assert {k: hall_served.rig.info["hall"][k]["value"] for k in ("dry", "early", "tail")} == \
        {"dry": 0.92, "early": 0.16, "tail": 0.08}
    page.close()


def test_turning_mix_sets_its_three_levels_through_the_agent_and_undo_puts_them_back(browser, hall_served):
    page = hall_page(browser, hall_served)
    p = page.page
    shown = round(100 * nearest({"dry": 0.92, "early": 0.16, "tail": 0.08}))
    expect(slider(p, "hall Mix")).to_have_attribute("aria-valuetext", f"{shown} %")
    slider(p, "hall Mix").focus()
    p.keyboard.press("ArrowUp")
    # The step lands on the next whole percent up, and all three levels follow it.
    up = math.floor(100 * nearest({"dry": 0.92, "early": 0.16, "tail": 0.08})) + 1
    want = targets(up / 100)
    expect(slider(p, "hall Mix")).to_have_attribute("aria-valuetext", f"{up} %")
    until(lambda: all(abs(hall_served.rig.info["hall"][k]["value"] - v) < 1e-9 for k, v in want.items()))
    assert sorted(c[2] for c in hall_calls(hall_served)) == ["dry", "early", "tail"], "one set each"
    entries = [e for e in live_log(hall_served) if e.get("op") == "parameter" and e["args"]["processor"] == "hall"]
    assert sorted(e["args"]["parameter"] for e in entries) == ["dry", "early", "tail"], \
        "each is a parameter change of the agent's own, logged as a knob's is"
    p.wait_for_timeout(1500)
    assert len(hall_calls(hall_served)) == 3, "the poll that follows sends nothing"
    # Undo takes the three back, one entry each.
    for _ in range(3):
        post(hall_served, "/live/undo", {})
    until(lambda: {k: hall_served.rig.info["hall"][k]["value"] for k in ("dry", "early", "tail")} ==
          {"dry": 0.92, "early": 0.16, "tail": 0.08})
    expect(slider(p, "hall Mix")).to_have_attribute("aria-valuetext", f"{shown} %")
    page.close()


def test_a_mix_typed_in_is_saved_as_its_three_levels(browser, hall_served):
    import yaml

    page = hall_page(browser, hall_served)
    p = page.page
    field = p.get_by_role("textbox", name="hall Mix value")
    field.click()
    field.fill("50")
    field.press("Enter")
    want = targets(0.5)
    until(lambda: all(abs(hall_served.rig.info["hall"][k]["value"] - v) < 1e-9 for k, v in want.items()))
    p.get_by_role("button", name="Save to electric_board").click()
    expect(p.locator(".save-result.ok")).to_contain_text("Saved electric_board")
    doc = yaml.safe_load((hall_served.save.root / "config" / "rigs" / "electric_board.yaml").read_text())
    (hall,) = [e for e in doc["channels"][0]["chain"] if e["name"] == "hall"]
    for k, v in want.items():
        assert hall["parameters"][k] == pytest.approx(v, abs=1e-6)
    assert hall["parameters"]["roomsize"] == pytest.approx(0.29) and hall["parameters"]["revtime"] == pytest.approx(0.01), \
        "what Mix does not drive is as it was"
    page.close()
