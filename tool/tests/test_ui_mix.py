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
import re
import urllib.request

import pytest

pytest.importorskip("aiohttp")

from sushi_rig.library import PROFILES_DIR, load_profiles  # noqa: E402
from sushi_rig.paths import checkout_root  # noqa: E402
from sushi_rig.profile import driven_value  # noqa: E402
from sushi_rig.ui import panel_profile  # noqa: E402

from rig_fixtures import committed_that, frozen, frozen_names  # noqa: E402
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


def path(values, m0, m, drives_=None):
    """Where a turn from `values` (shown at m0) to m takes the levels: each
    from where it is to its curve's end, at the curve's own pace from m0
    (rig.js `mixPath`), worked out here without it."""
    out = {}
    for d in drives_ or drives():
        C = lambda x: driven_value(d, x)  # noqa: E731
        end = C(1) if m >= m0 else C(0)
        span = end - C(m0)
        s = (0 if m == m0 else 1) if abs(span) < 1e-12 else (C(m) - C(m0)) / span
        L = values[d["parameter"]]
        out[d["parameter"]] = L + (end - L) * min(1.0, max(0.0, s))
    return out


def hall_rig(levels=None) -> UiRig:
    rig = UiRig()
    board = rig.chains["board"]
    board.insert(board.index("board_send"), "hall")
    rig.info["hall"] = info_of(probe_ports(FIXTURES / "probe_MaGigaverb.json"), {**SAVED, **(levels or {})})
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
# A hall saved off the curve: the first turn does not jump (review of PR #159)
# --------------------------------------------------------------------------- #

# The plugin's own defaults, for a hall saved with none of the three.
DEFAULTS = {"dry": 1.0, "early": 0.25, "tail": 0.25}


def _halls(raw) -> list[tuple[float, float, float]]:
    """Every MaGigaverb's (dry, early, tail) in a rig as read."""
    found = []
    chains = [c.get("chain") or [] for c in raw.get("channels") or []] + [(raw.get("master") or {}).get("chain") or []]
    for entry in (e for chain in chains for e in chain):
        if entry.get("uri") == MAGIGAVERB:
            p = entry.get("parameters") or {}
            found.append(tuple(float(p.get(k, DEFAULTS[k])) for k in ("dry", "early", "tail")))
    return found


def _read(path):
    import yaml

    return _halls(yaml.safe_load(path.read_text()))


# Every distinct saved hall: the committed rigs' (whatever they hold today,
# read as the rule says, `committed_that`) and the frozen rigs' (which are
# known to hold three that are off the curve).
COMMITTED_HALLS = sorted({h for _, halls in committed_that(_read) for h in halls})
FROZEN_HALLS = sorted({h for name in frozen_names() for h in _read(frozen(name))})


# The most a 1 % step moves each level anywhere on the curve.
STEEPEST = {k: max(abs(targets((i + 1) / 100)[k] - targets(i / 100)[k]) for i in range(100))
            for k in ("dry", "early", "tail")}


def first_steps(page, halls):
    """For each hall: where it is shown, and the levels after the first step
    up and the first step down, as the page works them out (rig.js)."""
    return rigjs(page, """
      const entry = arg.profile.parameters.find((p) => p.name === 'mix');
      const mix = R.makeMix(entry, arg.info);
      return arg.halls.map(([dry, early, tail]) => {
        const a = R.mixAnchor(mix, {dry, early, tail});
        const at = (v) => Object.fromEntries(R.mixPath(mix, a, v).map((t) => [t.parameter, t.value]));
        const up = R.stepped(mix, a.m0, 1), down = R.stepped(mix, a.m0, -1);
        return {m0: a.m0, still: at(a.m0), up, down, toUp: at(up), toDown: at(down), ends: [at(0), at(1)]};
      });""", {**mix_of(page), "halls": [list(h) for h in halls]})


def check_first_steps(halls, steps):
    db = lambda a, b: abs(20 * math.log10(b / a)) if a > 0 and b > 0 else (0.0 if a == b else math.inf)  # noqa: E731
    assert halls, "no hall to check"
    for (dry, early, tail), s in zip(halls, steps):
        was = {"dry": dry, "early": early, "tail": tail}
        where = f"the hall at dry {dry}, early {early}, tail {tail}"
        assert s["still"] == pytest.approx(was, abs=1e-9), f"{where}: shown where it is, nothing moved"
        assert s["ends"][0] == pytest.approx(targets(0), abs=1e-9) and s["ends"][1] == pytest.approx(targets(1), abs=1e-9), \
            f"{where}: 0 and 100 % are still the curve's own"
        up, down = s["toUp"], s["toDown"]
        assert up["dry"] <= dry and up["early"] >= early and up["tail"] >= tail, f"{where}: up raised dry or lowered a wet"
        assert down["dry"] >= dry and down["early"] <= early and down["tail"] <= tail, f"{where}: down raised a wet"
        assert up == pytest.approx(path(was, s["m0"], s["up"]), abs=1e-6)
        assert down == pytest.approx(path(was, s["m0"], s["down"]), abs=1e-6)
        # Small: no level by more than one 1 % step of the curve moves it
        # where the curve is steepest (`STEEPEST`), and dry by under 0.2 dB.
        # Not by the curve's own step at m0: a hall saved at dry 0.81 has
        # 0.19 to go to reach 1.0 at 0 %, where the curve at 16 % has 0.03,
        # so every step down must move dry more than the curve there would.
        for moved in (up, down):
            for k in was:
                assert abs(moved[k] - was[k]) <= STEEPEST[k] + 1e-9, (where, k, moved[k], was[k])
            assert db(dry, moved["dry"]) < 0.2, f"{where}: dry moved {db(dry, moved['dry']):.2f} dB in one step"


def test_the_first_step_from_each_committed_hall_moves_each_level_one_way_and_a_little(blank):
    check_first_steps(COMMITTED_HALLS, first_steps(blank, COMMITTED_HALLS))


def test_the_first_step_from_each_frozen_hall_moves_each_level_one_way_and_a_little(blank):
    assert (0.81, 0.16, 0.09) in FROZEN_HALLS and (0.81, 0.16, 0.11) in FROZEN_HALLS, "the off-curve ones"
    check_first_steps(FROZEN_HALLS, first_steps(blank, FROZEN_HALLS))


def test_a_curve_the_page_does_not_know_is_not_taken_for_linear(blank):
    out = rigjs(blank, """
      let threw = null;
      try { R.curveAt('exponential', 0.5); } catch (e) { threw = String(e.message); }
      const entry = {name: 'mix', drives: [{parameter: 'dry', curve: 'exponential', at_0: 1, at_1: 0}]};
      const mix = R.makeMix(entry, arg.info);
      return {threw, missing: mix.missing, notes: mix.notes};""", mix_of(blank))
    assert out["threw"].startswith('unknown curve "exponential"')
    assert out["missing"] is True and out["notes"] == ['not drawn: dry\'s curve "exponential" is not a curve this page knows']


def test_a_turn_back_and_forth_goes_back_through_the_same_levels(blank):
    out = rigjs(blank, """
      const entry = arg.profile.parameters.find((p) => p.name === 'mix');
      const mix = R.makeMix(entry, arg.info);
      const a = R.mixAnchor(mix, {dry: 0.81, early: 0.16, tail: 0.09});
      const at = (v) => R.mixPath(mix, a, v).map((t) => t.value);
      const there = at(0.6), back = at(a.m0);
      return {there, back, on: R.onMixPath(mix, a, 0.6, Object.fromEntries(R.mixPath(mix, a, 0.6).map((t) => [t.parameter, t.value])))};""",
                mix_of(blank))
    assert out["back"] == pytest.approx([0.81, 0.16, 0.09], abs=1e-12)
    assert out["on"] is True


# --------------------------------------------------------------------------- #
# The page's sender, and the log's view of a batched change (pure)
# --------------------------------------------------------------------------- #

def test_a_value_asked_for_while_a_failed_send_was_in_flight_is_still_sent(blank):
    out = rigjs(blank, """
      const posted = [], failed = [];
      let release;
      const post = (call) => {
        posted.push(call.value);
        if (posted.length === 1) return new Promise((r) => { release = () => r({ok: false, reason: 'no'}); });
        return Promise.resolve({ok: true});
      };
      const s = new R.Sender(post, {onFailure: (k, r) => failed.push(r.reason)});
      s.change('k', 1, (v) => ({value: v}));
      s.change('k', 2, (v) => ({value: v}));   // while the first is in flight
      release();
      await new Promise((r) => setTimeout(r, 20));
      return {posted, failed, shown: s.displayed('k', 0, 0)};""")
    assert out == {"posted": [1, 2], "failed": ["no"], "shown": 2}, "the failure said, the last value sent"


def test_a_failure_with_nothing_newer_waiting_shows_the_rig_again(blank):
    out = rigjs(blank, """
      const s = new R.Sender(() => Promise.resolve({ok: false, reason: 'no'}), {});
      s.change('k', 1, (v) => ({value: v}));
      await new Promise((r) => setTimeout(r, 20));
      return [s.busy('k'), s.displayed('k', 0.5, 0)];""")
    assert out == [False, 0.5]


def test_a_batched_change_is_unsaved_as_each_parameter(blank):
    out = rigjs(blank, """
      const log = {first: 0, log: [{at: '2026-11-16T10:00:00+00:00', op: 'parameters',
        args: {processor: 'hall', parameters: {dry: 0.7, early: 0.5, tail: 0.25}},
        previous: {dry: 0.92, early: 0.16, tail: 0.08}}]};
      const live = {parameter_info: {hall: {dry: {value: 0.7}, early: {value: 0.5}, tail: {value: 0.08}}}};
      const u = R.unsavedChanges(log, live, null);
      return {items: u.items.map((i) => [i.parameter, i.from, i.to])};""")
    assert out["items"] == [["dry", 0.92, 0.7], ["early", 0.16, 0.5]], "tail is back where it was: not unsaved"


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


def levels(served):
    return {k: served.rig.info["hall"][k]["value"] for k in ("dry", "early", "tail")}


def status_of(served, path_, body):
    import urllib.error

    try:
        return 200, post(served, path_, body)
    except urllib.error.HTTPError as exc:
        return exc.code, f"{exc.reason} {exc.read().decode(errors='replace')}"


SAVED_LEVELS = {"dry": 0.92, "early": 0.16, "tail": 0.08}


def test_a_page_that_loads_shows_the_nearest_mix_and_moves_nothing(browser, hall_served):
    page = hall_page(browser, hall_served)
    p = page.page
    mix = slider(p, "hall Mix")
    shown = round(100 * nearest(SAVED_LEVELS))
    expect(mix).to_have_attribute("aria-valuetext", f"{shown} %")
    expect(p.locator('.unit[data-unit="hall"] .knob[data-control$="mix"] .note')).to_have_text(
        "off Mix's curve: turning moves each level from where it is")
    expect(slider(p, "hall Room size")).to_be_visible()
    expect(slider(p, "hall Reverb time")).to_be_visible()
    for hidden in ("Dry", "Early", "Tail", "dry", "early", "tail"):
        expect(slider(p, f"hall {hidden}")).to_have_count(0)
    # Several polls, and still nothing sent: the levels are as saved.
    p.wait_for_timeout(2500)
    assert hall_calls(hall_served) == []
    assert levels(hall_served) == SAVED_LEVELS
    page.close()


def test_one_step_of_mix_is_one_change_and_one_undo(browser, hall_served):
    page = hall_page(browser, hall_served)
    p = page.page
    m0 = nearest(SAVED_LEVELS)
    expect(slider(p, "hall Mix")).to_have_attribute("aria-valuetext", f"{round(100 * m0)} %")
    slider(p, "hall Mix").focus()
    p.keyboard.press("ArrowUp")
    up = math.floor(100 * m0) + 1
    want = path(SAVED_LEVELS, m0, up / 100)
    expect(slider(p, "hall Mix")).to_have_attribute("aria-valuetext", f"{up} %")
    until(lambda: levels(hall_served) == pytest.approx(want, abs=2e-4))
    assert levels(hall_served)["dry"] < 0.92 and levels(hall_served)["early"] > 0.16, "a little, the way it was turned"
    assert sorted(c[2] for c in hall_calls(hall_served)) == ["dry", "early", "tail"], "one set each"
    entries = [e for e in live_log(hall_served) if e["args"].get("processor") == "hall"]
    assert [e["op"] for e in entries] == ["parameters"], "one change in the log"
    assert entries[0]["previous"] == pytest.approx(SAVED_LEVELS)
    p.wait_for_timeout(1500)
    assert len(hall_calls(hall_served)) == 3, "the poll that follows sends nothing"
    # One undo puts all three back.
    assert status_of(hall_served, "/live/undo", {})[0] == 200
    until(lambda: levels(hall_served) == pytest.approx(SAVED_LEVELS, abs=1e-9))
    expect(slider(p, "hall Mix")).to_have_attribute("aria-valuetext", f"{round(100 * m0)} %")
    assert [e["op"] for e in live_log(hall_served)] == ["parameters", "undo"]
    page.close()


def test_ten_steps_of_mix_cost_no_more_undos_than_ten_steps_of_a_knob(browser, hall_served):
    page = hall_page(browser, hall_served)
    p = page.page
    slider(p, "hall Mix").focus()
    for _ in range(10):
        p.keyboard.press("ArrowUp")
    m0 = nearest(SAVED_LEVELS)
    expect(slider(p, "hall Mix")).to_have_attribute("aria-valuetext", f"{math.floor(100 * m0) + 10} %")
    until(lambda: levels(hall_served) == pytest.approx(path(SAVED_LEVELS, m0, (math.floor(100 * m0) + 10) / 100), abs=2e-4))
    mixes = len([e for e in live_log(hall_served) if e["op"] == "parameters"])
    slider(p, "overdrive Drive").focus()
    for _ in range(10):
        p.keyboard.press("ArrowUp")
    expect(slider(p, "overdrive Drive")).to_have_attribute("aria-valuetext", "0.72")
    until(lambda: abs(hall_served.rig.info["overdrive"]["Drive"]["value"] - 0.72) < 1e-9)
    drives_ = len([e for e in live_log(hall_served) if e["op"] == "parameter"])
    # The sender coalesces while a send is in flight, for both alike: each
    # costs at most one entry a step, and Mix one entry where a knob has one.
    assert 1 <= mixes <= 10 and 1 <= drives_ <= 10
    for _ in range(mixes):
        assert status_of(hall_served, "/live/undo", {})[0] == 200
        # Every undo leaves the three as one of the steps left them, never part-way.
        got = levels(hall_served)
        assert any(got == pytest.approx(path(SAVED_LEVELS, m0, (math.floor(100 * m0) + k) / 100), abs=2e-4)
                   for k in range(0, 11)) or got == pytest.approx(SAVED_LEVELS)
    page.close()


def test_a_level_sushi_refuses_part_way_puts_back_the_ones_already_set(browser, hall_served):
    hall_served.rig.refuse[("hall", "early")] = "Sushi would not set early (test)"
    page = hall_page(browser, hall_served)
    p = page.page
    slider(p, "hall Mix").focus()
    p.keyboard.press("ArrowUp")
    refused = p.locator(".problem.refused")
    expect(refused).to_contain_text("Not changed: hall · mix")
    expect(refused).to_have_attribute("title", re.compile(re.escape("Sushi would not set early (test)")))
    expect(refused).to_have_attribute("title", re.compile(re.escape("put back dry as it was")))
    until(lambda: [c[2] for c in hall_calls(hall_served)] == ["dry", "early", "dry"])
    assert levels(hall_served) == pytest.approx(SAVED_LEVELS, abs=1e-12), "all or none: none"
    assert [e for e in live_log(hall_served) if e["args"].get("processor") == "hall"] == [], "nothing logged"
    expect(slider(p, "hall Mix")).to_have_attribute("aria-valuetext", f"{round(100 * nearest(SAVED_LEVELS))} %")
    page.close()


def test_the_batched_route_refuses_what_it_cannot_do_before_setting_anything(hall_served):
    bad = [
        ({"processor": "hall"}, "'parameters' is required"),
        ({"processor": "hall", "parameters": {}}, "'parameters' is required"),
        ({"processor": "hall", "parameters": {"dry": 2}}, "from 0 to 1"),
        ({"processor": "hall", "parameters": {"dry": 0.5, "wet": 0.5}}, "has no parameter 'wet'"),
        ({"processor": "nowhere", "parameters": {"dry": 0.5}}, "no processor 'nowhere'"),
    ]
    for body, said in bad:
        status, reason = status_of(hall_served, "/live/parameters", body)
        assert status == 400 and said in reason, (body, status, reason)
    assert hall_calls(hall_served) == [] and levels(hall_served) == SAVED_LEVELS


def test_a_batched_change_changed_since_is_not_undone_over(hall_served):
    assert status_of(hall_served, "/live/parameters", {"processor": "hall", "parameters": {"dry": 0.5, "early": 0.4}})[0] == 200
    hall_served.rig.info["hall"]["early"]["value"] = 0.3  # moved since, not through the agent
    status, reason = status_of(hall_served, "/live/undo", {})
    assert status == 409 and "no longer as the change left it" in reason
    assert levels(hall_served) == {"dry": 0.5, "early": 0.3, "tail": 0.08}, "nothing put back"


def test_a_mix_typed_in_is_saved_as_its_three_levels(browser, hall_served):
    import yaml

    page = hall_page(browser, hall_served)
    p = page.page
    field = p.get_by_role("textbox", name="hall Mix value")
    field.click()
    field.fill("50")
    field.press("Enter")
    want = path(SAVED_LEVELS, nearest(SAVED_LEVELS), 0.5)
    until(lambda: levels(hall_served) == pytest.approx(want, abs=2e-4))
    p.get_by_role("button", name="Save to electric_board").click()
    expect(p.locator(".save-result.ok")).to_contain_text("Saved electric_board")
    doc = yaml.safe_load((hall_served.save.root / "config" / "rigs" / "electric_board.yaml").read_text())
    (hall,) = [e for e in doc["channels"][0]["chain"] if e["name"] == "hall"]
    for k, v in levels(hall_served).items():
        assert hall["parameters"][k] == pytest.approx(v, abs=1e-6)
    assert hall["parameters"]["roomsize"] == pytest.approx(0.29) and hall["parameters"]["revtime"] == pytest.approx(0.01), \
        "what Mix does not drive is as it was"
    page.close()
