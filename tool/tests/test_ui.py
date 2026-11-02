"""The web UI, v0 (#31): the rack (S1) and the stage screen (S5).

Three layers, in this order. The Python decisions (`ui.panel_profile`) and the
agent's UI routes, with no browser. Then `rig.js`'s decisions, imported into a
bare page and called, since the rig machine has no Node and the UI is plain
files: Chromium is the one JavaScript engine this suite can count on having.
Then the two screens, driven in Chromium against a real agent over the fake
rig in `ui_fake.py`, which says what in it is observed and what invented.

Not shown here: any of it against a real Sushi. The fake answers as
`agent.Live` is specified, and `live.read_live_plugins` is read from elkpy
1.3.0, not run.
"""

from __future__ import annotations

import asyncio
import copy
import re
from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("aiohttp")
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from sushi_rig.agent import Agent, pick_undo, UndoBarrier  # noqa: E402
from sushi_rig.library import load_profiles, PROFILES_DIR  # noqa: E402
from sushi_rig.paths import checkout_root  # noqa: E402
from sushi_rig.ui import UI_DIR, panel_profile, panel_profiles  # noqa: E402

from ui_fake import Served, UiRig  # noqa: E402

GXTS9 = "http://guitarix.sourceforge.net/plugins/gxts9#ts9sim"


def gxts9_profile():
    profiles, problems = load_profiles(checkout_root() / PROFILES_DIR)
    assert problems == []
    return profiles[GXTS9]


# --------------------------------------------------------------------------- #
# The profile, flattened for the browser (pure)
# --------------------------------------------------------------------------- #

def test_a_profile_is_flattened_in_the_order_the_panel_lays_it_out():
    flat, problems = panel_profile(gxts9_profile())
    assert problems == []
    assert flat["uri"] == GXTS9 and flat["name"] == "GxTubeScreamer" and flat["file"] == "gxts9.yaml"
    assert flat["groups"] == [{"id": "dirt", "label": "Drive and tone"}, {"id": "output", "label": "Output"}]
    # Groups in the profile's order, then `order` in each; BYPASS has no group and goes last.
    assert [(p["name"], p["group"], p["visibility"]) for p in flat["parameters"]] == [
        ("Drive", "dirt", "primary"), ("Tone", "dirt", "primary"),
        ("Level", "output", "primary"), ("BYPASS", None, "hidden"),
    ]
    level = flat["parameters"][2]
    assert (level["min"], level["max"], level["unit"], level["scale"], level["control"], level["phone"]) == \
        (-20.0, 4.0, None, "linear", "fader", 1), "the plugin declares no unit, so none is invented"


def test_what_a_profile_does_not_say_is_named_not_guessed():
    flat, problems = panel_profile({
        "_file": "odd.yaml", "plugin": {"uri": "urn:odd"},
        "groups": [{"id": "a", "label": "A"}, {"label": "no id"}],
        "parameters": [
            {"name": "Late", "group": {"value": "a"}, "order": {"value": 2}},
            {"name": "Early", "group": {"value": "a"}, "order": {"value": 1}, "visibility": {"value": "advanced"}},
            {"name": "Stray", "group": {"value": "b"}},
            {"group": {"value": "a"}},
            "not a mapping",
        ],
    })
    assert [p["name"] for p in flat["parameters"]] == ["Early", "Late", "Stray"]
    assert flat["parameters"][1]["visibility"] == "primary", "no visibility is shown, not hidden"
    assert flat["parameters"][1]["label"] == "Late", "no label is the name"
    assert flat["groups"][-1] == {"id": "b", "label": "b"}
    assert problems == [
        "odd.yaml: a group with no id is left out: {'label': 'no id'}",
        "odd.yaml: Stray names group 'b', which groups does not define",
        "odd.yaml: parameter 4 has no name and is left out",
        "odd.yaml: parameter 5 has no name and is left out",
    ]


def test_the_profiles_answer_keeps_the_loaders_problems_first():
    answer = panel_profiles({GXTS9: gxts9_profile()}, ["profile x.yaml cannot be loaded: bad"])
    assert list(answer["profiles"]) == [GXTS9]
    assert answer["problems"] == ["profile x.yaml cannot be loaded: bad"]


# --------------------------------------------------------------------------- #
# The agent's UI routes (no browser)
# --------------------------------------------------------------------------- #

def call(agent, method, path):
    async def go():
        async with TestClient(TestServer(agent.app())) as client:
            response = await client.request(method, path)
            body = await response.read()
            return response.status, response.headers.get("Content-Type", ""), body
    try:
        return asyncio.run(go())
    finally:
        agent._executor.shutdown(wait=True, cancel_futures=True)


def ui_agent(**kwargs):
    kwargs.setdefault("describe", lambda: {"verdict": "running", "running": True, "config_name": "x"})
    return Agent(root=checkout_root(), **kwargs)


def test_the_page_and_its_files_are_served_and_nothing_beside_them():
    status, kind, body = call(ui_agent(live=UiRig()), "GET", "/")
    assert status == 200 and kind.startswith("text/html") and b'src="/ui/app.js"' in body
    status, kind, _ = call(ui_agent(live=UiRig()), "GET", "/ui/rig.js")
    assert status == 200 and "javascript" in kind, "a module script must be served as JavaScript"
    for path in ("/ui/", "/ui/../__init__.py", "/ui/%2e%2e/__init__.py", "/ui/../../agent.py", "/ui/__init__.py"):
        status, _, body = call(ui_agent(live=UiRig()), "GET", path)
        assert status in (403, 404) and b"import" not in body, path


def test_profiles_are_served_flattened_with_no_rig_needed():
    import json

    status, _, body = call(ui_agent(live=UiRig(), describe=lambda: {"verdict": "absent", "running": False}), "GET", "/profiles")
    answer = json.loads(body)
    assert status == 200 and answer["ok"] is True and answer["problems"] == []
    assert [p["name"] for p in answer["profiles"][GXTS9]["parameters"]] == ["Drive", "Tone", "Level", "BYPASS"]


def test_which_plugin_each_processor_is_comes_with_the_compilers_roles():
    import json

    status, _, body = call(ui_agent(live=UiRig()), "GET", "/live/plugins")
    processors = json.loads(body)["processors"]
    assert status == 200
    assert processors["overdrive"] == {"track": "board", "uri": GXTS9, "uid": None, "type": "lv2", "role": None}
    assert {n: p["role"] for n, p in processors.items() if p["role"]} == {
        "board_send": "send", "vocal_send": "send", "master_return": "return"}


def test_a_live_that_cannot_say_which_plugin_is_501_not_an_empty_answer():
    import json

    class Mute(UiRig):
        plugins = None

    status, _, body = call(ui_agent(live=Mute()), "GET", "/live/plugins")
    assert status == 501 and "cannot say which plugin" in json.loads(body)["reason"]


def test_which_plugin_is_refused_while_the_rig_is_changing():
    agent = ui_agent(live=UiRig())

    async def go():
        async with TestClient(TestServer(agent.app())) as client:
            async with agent._lock:  # what a running lifecycle command holds
                agent.busy = "restart"
                response = await client.get("/live/plugins")
                return response.status, (await response.json())["code"]

    try:
        assert asyncio.run(go()) == (409, "busy")
    finally:
        agent._executor.shutdown(wait=True, cancel_futures=True)
    assert agent.live.calls == [], "Sushi was not asked"


def test_the_session_is_read_for_each_processors_plugin_and_track():
    """The stand-in session has elkpy 1.3.0's `SessionState` shape, as
    test_agent_edits.py's does; what Sushi fills it with is not known here."""
    pytest.importorskip("elkpy")
    from types import SimpleNamespace as NS

    from sushi_rig.live import read_live_plugins

    def plugin(name, uid, path, kind):
        return NS(name=name, uid=uid, path=path, type=NS(type=kind))

    session = NS(tracks=[NS(name="guitar", processors=[
        plugin("drive", "", GXTS9, 4), plugin("guitar_send", "sushi.testing.send", "", 1)])])
    controller = NS(session=NS(get_session=lambda: session))
    assert read_live_plugins(controller=controller) == {
        "drive": {"track": "guitar", "uri": GXTS9, "uid": None, "type": "lv2"},
        "guitar_send": {"track": "guitar", "uri": None, "uid": "sushi.testing.send", "type": "internal"},
    }


def test_the_ui_files_are_plain_and_need_no_build():
    """The UI runs from a checkout (sushi_rig/ui/__init__.py): no bundler's
    imports, nothing fetched from the network."""
    for name in ("index.html", "app.js", "rig.js", "style.css"):
        text = (UI_DIR / name).read_text(encoding="utf-8")
        assert "http://" not in text.replace("http://www.w3.org/2000/svg", "") and "https://" not in text, name
    for name in ("app.js", "rig.js"):
        for line in (UI_DIR / name).read_text(encoding="utf-8").splitlines():
            if line.startswith("import "):
                assert line.endswith('from "./rig.js";'), line


# --------------------------------------------------------------------------- #
# The browser: rig.js's decisions, then the two screens
# --------------------------------------------------------------------------- #

try:
    from playwright.sync_api import expect, sync_playwright
except ImportError:  # the [ui-test] extra; the tests above need none of it
    expect = sync_playwright = None


@pytest.fixture(scope="module")
def browser():
    # Without the package the browser tests skip, as the lilv and Sushi ones
    # do where those are absent, and the container job (which installs the
    # extra and fails on any skip) is where they must run. A Chromium that
    # will not start is a failure, not a skip: a UI suite that quietly did not
    # run is the silent pass principle 8 is against.
    if sync_playwright is None:
        pytest.skip("the browser tests need playwright: pip install -e 'tool[ui-test]', "
                    "then python -m playwright install chromium")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        yield browser
        browser.close()


@pytest.fixture
def served():
    with Served() as served:
        yield served


class Page:
    """A page and the JavaScript errors it raised: any is a failure."""

    def __init__(self, browser, url, width=1280, height=900):
        self.page = browser.new_page(viewport={"width": width, "height": height})
        self.errors = []
        self.page.on("pageerror", lambda error: self.errors.append(str(error)))
        self.page.goto(url)

    def close(self):
        self.page.close()
        assert self.errors == []


@pytest.fixture
def blank(browser, served):
    """A page of the agent's origin with nothing drawn, to import rig.js into."""
    page = Page(browser, served.url + "/profiles")
    yield page.page
    page.close()


def rigjs(page, body, arg=None):
    """Run `body` (a function body with `R`, rig.js, and `arg`) in the page."""
    return page.evaluate(f"async (arg) => {{ const R = await import('/ui/rig.js'); {body} }}", arg)


# -- values -------------------------------------------------------------------

def test_values_are_shown_in_the_plugins_own_range_and_never_as_0_to_1(blank):
    shown = rigjs(blank, """
      const info = (min, max, value) => ({automatable: true, value, min_domain_value: min, max_domain_value: max});
      const show = (i, e) => R.formatValue(R.makeControl('x', i, e), i.value);
      return [
        show(info(-20, 4, 0.7083333), {label: 'Level'}),            // gxts9 Level at -3
        show(info(100, 1000, 0.4222222), null),                      // Tone at 480: spans 900, no decimals
        show(info(0, 1, 0.62), null),
        show(info(-100, 100, 0.5), {integer: true}),                 // Cents: integer, and never "-0"
        show(info(-120, 24, 120 / 144), {unit: 'dB'}),               // a send at 0 dB
        show(info(-120, 24, 114 / 144), {unit: 'dB'}),
        show(info(0.001, 10, (1 - 0.001) / (10 - 0.001)), {scale: 'db'}), // a linear gain of 1 is 0 dB
        show(info(0, 1, 1), {scale: 'toggle', toggled: true}),
        show(info(0, 3, 1 / 3), {scale: 'enum', scale_points: [{value: 0, label: 'Smooth'}, {value: 1, label: 'Crisp'}]}),
        show({automatable: true, value: 0.3}, null),                 // Sushi gave no range
        R.formatValue(R.makeControl('x', undefined, {label: 'Gone'}), null),
      ];""")
    assert shown == ["−3.0", "480", "0.62", "0", "0 dB", "−6 dB", "0.0 dB", "on", "Crisp", "0.300", "—"]


def test_a_log_scale_drags_by_ratio_and_lands_back_where_it_was(blank):
    out = rigjs(blank, """
      const c = R.makeControl('f', {automatable: true, value: 0, min_domain_value: 20, max_domain_value: 20000}, {scale: 'log'});
      const v = R.valueAt(c, 0.5);
      const zero = R.makeControl('g', {automatable: true, value: 0, min_domain_value: 0, max_domain_value: 10}, {scale: 'log'});
      const step = R.makeControl('s', {automatable: true, value: 0, min_domain_value: -12, max_domain_value: 12}, {integer: true});
      return {real: R.realOf(c, v), back: R.positionOf(c, v), zero: [zero.scale, zero.notes],
              rounded: R.realOf(step, R.valueAt(step, 0.52)), step: R.stepOf(step)};""")
    assert abs(out["real"] - 632.4555) < 1e-3, "halfway from 20 Hz to 20 kHz is their geometric mean"
    assert abs(out["back"] - 0.5) < 1e-9
    assert out["zero"] == ["linear", ["log scale over a range reaching zero: drawn linear"]]
    assert out["rounded"] == 0 and abs(out["step"] - 1 / 24) < 1e-12


# -- what a card shows ----------------------------------------------------------

def test_a_profiled_card_shows_its_groups_and_says_what_the_profile_did_not_foresee(blank, served):
    profile = rigjs(blank, "return (await (await fetch('/profiles')).json()).profiles[arg];", GXTS9)
    info = copy.deepcopy(served.rig.info["overdrive"])
    del info["Tone"]
    info["Extra"] = {"automatable": True, "value": 0.1, "min_domain_value": 0, "max_domain_value": 1}
    panel = rigjs(blank, """
      const p = R.unitPanel(arg.info, arg.profile);
      const names = (cs) => cs.map((c) => [c.name, c.label, c.notes]);
      return {mode: p.mode, groups: p.groups.map((g) => [g.label, names(g.controls)]),
              advanced: names(p.advanced), missing: names(p.missing)};""", {"info": info, "profile": profile})
    assert panel == {
        "mode": "profile",
        "groups": [["Drive and tone", [["Drive", "Drive", []]]], ["Output", [["Level", "Level", []]]]],
        "advanced": [["Extra", "Extra", ["not in the profile"]]],
        "missing": [["Tone", "Tone", []]],
    }, "BYPASS is hidden as the profile says; Tone is missing from the plugin, and said"


def test_a_card_without_a_profile_shows_every_parameter_read_only_ones_too(blank, served):
    panel = rigjs(blank, """
      const p = R.unitPanel(arg, null);
      return [p.mode, p.groups[0].controls.map((c) => [c.name, c.readOnly])];""", served.rig.info["octave"])
    assert panel == ["plain", [["latency", True], ["Cents", False], ["Semitones", False], ["Octaves", False],
                               ["Crispness", False], ["Formant Preserving", False], ["Wet-Dry Mix", False]]]


def test_the_rack_keeps_the_send_and_return_apart_by_role_or_by_name(blank, served):
    live = served.rig.snapshot(served.rig.connect())
    plugins = served.rig.plugins(None)
    out = rigjs(blank, """
      const shape = (m) => m.tracks.map((t) => [t.name, t.units.map((u) => u.name), t.send && t.send.name, t.ret && t.ret.name]);
      return [shape(R.rackModel(arg.live, arg.plugins, {})), shape(R.rackModel(arg.live, null, null)),
              R.channelLevels(R.rackModel(arg.live, arg.plugins, {}), arg.live)];""", {"live": live, "plugins": plugins})
    expected = [["board", ["overdrive", "octave", "delay"], "board_send", None],
                ["vocal", ["vocal_delay"], "vocal_send", None],
                ["master", [], None, "master_return"]]
    assert out[0] == expected and out[1] == expected
    assert out[2] == [{"channel": "board", "text": "0 dB"}, {"channel": "vocal", "text": "−6 dB"}]


# -- unsaved, and what undo would take ------------------------------------------

STARTED = datetime(2026, 11, 2, 12, 0, 0, 500000, tzinfo=timezone.utc)


def at(seconds):
    return (STARTED.replace(microsecond=0) + timedelta(seconds=seconds)).isoformat(timespec="seconds")


def entry(op, args, previous, seconds=10, **marks):
    return {"at": at(seconds), "op": op, "args": args, "previous": previous, **marks}


def unsaved(page, log, live, first=0):
    return rigjs(page, "return R.unsavedChanges(arg.log, arg.live, arg.rig);",
                 {"log": {"log": log, "first": first}, "live": live, "rig": {"started_at": STARTED.isoformat()}})


def test_unsaved_is_what_differs_from_what_the_first_change_replaced(blank, served):
    live = served.rig.snapshot(served.rig.connect())  # Drive 0.62, delay on, board as built
    drive = lambda v, was, s=10: entry("parameter", {"processor": "overdrive", "parameter": "Drive", "value": v}, was, s)  # noqa: E731
    # Moved and moved back: nothing unsaved.
    assert unsaved(blank, [drive(0.7, 0.62), drive(0.62, 0.7)], live) == {"items": [], "partial": False}
    # Moved, then a second time: one change, from the first value.
    log = [drive(0.5, 0.40), drive(0.62, 0.5, 11),
           entry("bypass", {"processor": "delay", "bypassed": True}, True, 12),
           entry("bypass", {"processor": "delay", "bypassed": False}, True, 13, source="midi")]
    assert unsaved(blank, log, live)["items"] == [
        {"kind": "parameter", "processor": "overdrive", "parameter": "Drive", "from": 0.40, "to": 0.62, "source": None},
        {"kind": "bypass", "processor": "delay", "from": True, "to": False, "source": "midi"},
    ]
    # An undo is a change like the others.
    log = [drive(0.7, 0.62), entry("undo", {"of": "parameter", "processor": "overdrive", "parameter": "Drive", "value": 0.62}, 0.7, 11, undoes=0)]
    assert unsaved(blank, log, live)["items"] == []


def test_unsaved_leaves_out_another_rigs_changes_and_says_when_the_log_is_short(blank, served):
    live = served.rig.snapshot(served.rig.connect())
    drive = entry("parameter", {"processor": "overdrive", "parameter": "Drive", "value": 0.62}, 0.1)
    before_start = {**drive, "at": at(-60)}
    switch = entry("rig", {"rig": "electric_board", "program": 2}, "guitar_vocal")
    assert unsaved(blank, [before_start], live)["items"] == [], "a restart since reloaded the file"
    assert unsaved(blank, [drive, switch], live)["items"] == [], "before the switch was another rig"
    # The same second as the start counts: the log's `at` is to the second.
    assert len(unsaved(blank, [{**drive, "at": at(0)}], live)["items"]) == 1
    assert unsaved(blank, [drive], live, first=40)["partial"] is True
    assert unsaved(blank, [before_start, drive], live, first=40)["partial"] is False


def test_a_reordered_chain_is_unsaved_until_it_is_put_back(blank, served):
    rig = served.rig
    before = list(rig.chains["board"])
    rig.chains["board"][1:3] = ["delay", "octave"]
    moved = entry("move", {"processor": "delay", "direction": "up"}, before)
    items = unsaved(blank, [moved], rig.snapshot(rig.connect()))["items"]
    assert items == [{"kind": "chain", "track": "board", "from": before, "to": rig.chains["board"], "source": None}]
    rig.chains["board"] = before
    assert unsaved(blank, [moved], rig.snapshot(rig.connect()))["items"] == []


@pytest.mark.parametrize("log, first", [
    ([], 0),
    ([entry("parameter", {"processor": "a", "parameter": "x", "value": 1}, 0)], 0),
    ([entry("parameter", {}, 0), entry("bypass", {}, False), entry("undo", {"of": "bypass"}, True, undoes=8)], 7),
    ([entry("move", {}, []), entry("stale", {"of": "move"}, None, stale=0), entry("parameter", {}, 0), entry("undo", {}, 0, undoes=2)], 0),
    ([entry("parameter", {}, 0), entry("rig", {"rig": "b"}, "a")], 0),
    ([entry("rig", {"rig": "b"}, "a"), entry("bypass", {}, True)], 3),
])
def test_the_undo_the_page_offers_is_the_one_the_agent_would_make(blank, log, first):
    try:
        expected = pick_undo(log, first)
        expected = None if expected is None else {"index": expected[0]}
    except UndoBarrier:
        expected = {"barrier": True}
    got = rigjs(blank, """const n = R.nextUndo(arg);
      return n === null ? null : n.barrier ? {barrier: true} : {index: n.index};""", {"log": log, "first": first})
    assert got == expected


# -- the rig's state, and the lock -------------------------------------------------

def test_an_up_rig_is_never_shown_as_measured_and_down_is_said(blank):
    states = rigjs(blank, """
      const health = (rig, agent = {}) => ({ok: true, body: {rig, agent}});
      const up = {running: true, config_name: 'electric_board', uptime_seconds: 4320};
      return [
        R.rigStatus(health(up), {ok: true}),
        R.rigStatus(health(up), {ok: false, reason: 'Sushi went away'}),
        R.rigStatus(health({running: false, message: 'No rig is running.'}), {ok: false}),
        R.rigStatus(health(up, {busy: 'restart'}), {ok: false}),
        R.rigStatus({ok: false, reason: 'the agent did not answer'}, null),
      ].map((s) => [s.tone, s.title, s.detail, s.measured]);""")
    assert states == [
        ["up", "electric_board", "Up 1 h 12 min · sound not measured", False],
        ["down", "electric_board", "Up, but Sushi did not answer: Sushi went away", False],
        ["down", "Rig down", "No rig is running.", False],
        ["busy", "electric_board", "restart is running", False],
        ["down", "Agent not answering", "the agent did not answer", False],
    ]


def test_the_lock_refuses_structure_and_lets_sound_through(blank):
    out = rigjs(blank, """return [R.lockRefusal(true, 'move'), R.lockRefusal(true, 'add'),
      R.lockRefusal(true, 'parameter'), R.lockRefusal(true, 'bypass'), R.lockRefusal(false, 'move')];""")
    assert out[0].startswith("Locked: reordering") and out[1].startswith("Locked: adding")
    assert out[2:] == [None, None, None]


def test_changes_are_sent_one_at_a_time_and_the_newest_wins(blank):
    out = rigjs(blank, """
      const sent = [], replies = [], failures = [];
      let seq = 0;
      const post = (body) => new Promise((resolve) => { sent.push(body); replies.push(resolve); });
      const s = new R.Sender(post, {pollSeq: () => seq, onFailure: (k, r) => failures.push([k, r.reason])});
      const tick = () => new Promise((r) => setTimeout(r, 0));
      s.change('k', 0.1, (v) => v); s.change('k', 0.2, (v) => v); s.change('k', 0.3, (v) => v);
      const shownWhileSending = s.displayed('k', 0.9, 0);
      replies[0]({ok: true}); await tick();         // 0.1 confirmed; 0.3 goes next, 0.2 never
      replies[1]({ok: true}); await tick();         // 0.3 confirmed at poll 0
      const shownAfterAnOldPoll = s.displayed('k', 0.1, 0);
      seq = 1;
      const shownAfterANewPoll = s.displayed('k', 0.3, 1);
      s.change('j', 0.5, (v) => v);
      replies[2]({ok: false, reason: 'Sushi refused it'}); await tick();
      return {sent, shownWhileSending, shownAfterAnOldPoll, shownAfterANewPoll, failures,
              shownAfterFailure: s.displayed('j', 0.25, 1), busy: s.busy('j')};""")
    assert out == {
        "sent": [0.1, 0.3, 0.5], "shownWhileSending": 0.3, "shownAfterAnOldPoll": 0.3,
        "shownAfterANewPoll": 0.3, "failures": [["j", "Sushi refused it"]],
        "shownAfterFailure": 0.25, "busy": False,
    }


# -- the rack (S1) ------------------------------------------------------------------

@pytest.fixture
def rack(browser, served):
    page = Page(browser, served.url + "/#rack")
    page.page.wait_for_selector(".unit")
    yield page.page
    page.close()


def slider(page, name):
    return page.get_by_role("slider", name=name, exact=True)


def until(predicate, timeout=5.0):
    """Wait for something the fake rig records, which the page cannot see."""
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError("not within %ss" % timeout)


def test_the_rack_shows_every_chain_as_a_row_of_cards_with_the_profiles_controls(rack):
    tracks = rack.locator("section.track")
    expect(tracks).to_have_count(3)
    expect(rack.locator("section.track h2")).to_have_text(["board", "vocal", "master"])
    expect(rack.locator("#t-board .unit button.name")).to_have_text(["overdrive", "octave", "delay"])
    overdrive = rack.locator('.unit[data-unit="overdrive"]')
    expect(overdrive.get_by_role("slider")).to_have_count(3)
    for name, text in (("Drive", "0.62"), ("Tone", "480"), ("Level", "−3.0")):
        expect(slider(rack, f"overdrive {name}")).to_have_attribute("aria-valuetext", text)
    expect(overdrive).to_contain_text("GxTubeScreamer · profile")
    expect(rack.get_by_role("slider", name="overdrive BYPASS")).to_have_count(0)
    # No profile: every parameter, in a plain list, the read-only one said so.
    expect(rack.locator('.unit[data-unit="delay"]')).to_contain_text("no profile: every parameter")
    expect(rack.locator('.unit[data-unit="delay"]').get_by_role("slider")).to_have_count(6)
    expect(slider(rack, "octave latency")).to_have_attribute("aria-readonly", "true")
    # The send is the channel's level, in dB.
    expect(slider(rack, "vocal_send Level")).to_have_attribute("aria-valuetext", "−6 dB")


def test_a_turned_knob_is_heard_at_once_and_marked_unsaved(rack, served):
    drive = slider(rack, "overdrive Drive")
    drive.focus()
    for _ in range(10):
        rack.keyboard.press("ArrowDown")
    expect(drive).to_have_attribute("aria-valuetext", "0.52")
    until(lambda: abs(served.rig.info["overdrive"]["Drive"]["value"] - 0.52) < 1e-9)
    expect(drive).to_have_class(re.compile(r"\bunsaved\b"))
    expect(drive).to_contain_text("was 0.62")
    changes = rack.locator("aside.changes")
    expect(changes.locator("h2")).to_have_text("1 unsaved change")
    expect(changes.locator(".change")).to_have_text("board · overdrive · Drive0.62 to 0.52")


def test_a_knob_turns_by_dragging_with_a_pointer(rack, served):
    box = slider(rack, "overdrive Drive").locator("svg").bounding_box()
    x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    rack.mouse.move(x, y)
    rack.mouse.down()
    rack.mouse.move(x, y - 50, steps=5)  # a quarter of a full sweep, upwards
    rack.mouse.up()
    expect(slider(rack, "overdrive Drive")).to_have_attribute("aria-valuetext", "0.87")
    until(lambda: abs(served.rig.info["overdrive"]["Drive"]["value"] - 0.87) < 1e-6)


def test_a_refused_change_shows_the_agents_reason_and_the_rigs_value(rack, served):
    served.rig.refuse[("delay", "Feedback")] = "Sushi would not set Feedback (test)"
    feedback = slider(rack, "delay Feedback")
    feedback.focus()
    rack.keyboard.press("ArrowUp")
    alert = rack.locator(".problem.refused")
    expect(alert).to_contain_text("Not changed: delay · Feedback")
    expect(alert).to_contain_text("Sushi would not set Feedback (test)")
    expect(feedback).to_contain_text("refused: Sushi would not set Feedback (test)")
    # Back to the rig's value, with the reason beside it: not a silent revert.
    expect(feedback).to_have_attribute("aria-valuetext", "0.35")
    # The agent remembered it too, and says so in the same glance.
    expect(rack.locator(".problem.agent")).to_contain_text("Sushi would not set Feedback (test)")
    assert served.rig.info["delay"]["Feedback"]["value"] == 0.35


def test_bypass_and_undo_are_one_tap_each(rack, served):
    rack.get_by_role("button", name="Bypass overdrive").click()
    expect(rack.get_by_role("button", name="Turn on overdrive")).to_be_visible()
    until(lambda: served.rig.bypassed["overdrive"] is True)
    changes = rack.locator("aside.changes")
    expect(changes.locator(".change")).to_have_text("board · overdriveon to bypassed")
    undo = changes.get_by_role("button", name="Undo · bypass overdrive")
    undo.click()
    until(lambda: served.rig.bypassed["overdrive"] is False)
    expect(changes.locator("h2")).to_have_text("0 unsaved changes")
    expect(rack.locator(".notice")).to_contain_text("Undone: bypass overdrive")


def test_reordering_is_refused_while_locked_and_works_once_unlocked_deliberately(rack, served):
    rack.get_by_role("button", name="Move delay up (locked)").click()
    expect(rack.locator(".problem.refused")).to_contain_text("Locked: reordering changes the rig's structure")
    assert served.rig.writes() == []
    lock = rack.locator("button.lock")
    lock.click()
    expect(lock).to_have_text("Tap again to unlock")
    assert served.rig.writes() == [], "one tap does not unlock"
    lock.click()
    expect(lock).to_have_text("Lock off · editing")
    rack.get_by_role("button", name="Move delay up", exact=True).click()
    until(lambda: served.rig.chains["board"] == ["overdrive", "delay", "octave", "board_send"])
    expect(rack.locator("#t-board .unit button.name")).to_have_text(["overdrive", "delay", "octave"])
    expect(rack.locator("aside.changes .change")).to_have_text(
        "board · chainoverdrive, octave, delay, board_send to overdrive, delay, octave, board_send")


def test_a_rig_that_is_down_looks_down(browser):
    rig = UiRig()

    def refuse(*_):
        raise __import__("sushi_rig.live", fromlist=["LiveError"]).LiveError("no Sushi on 127.0.0.1:51051")

    rig.connect = refuse
    with Served(rig, describe=lambda: {"verdict": "absent", "running": False, "message": "No rig is running."}) as served:
        page = Page(browser, served.url + "/#rack")
        expect(page.page.locator(".rig-name")).to_have_text("Rig down")
        expect(page.page.locator(".status")).to_have_text("No rig is running.")
        expect(page.page.locator(".empty")).to_contain_text("nothing is running in Sushi")
        expect(page.page.locator(".unit")).to_have_count(0)
        page.close()


def test_an_agent_that_stops_answering_is_said_within_a_poll(browser):
    served = Served()
    page = Page(browser, served.url + "/#rack")
    page.page.wait_for_selector(".unit")
    served.close()
    expect(page.page.locator(".rig-name")).to_have_text("Agent not answering")
    page.close()


# -- the stage screen (S5) ------------------------------------------------------------

@pytest.fixture
def stage(browser, served):
    # A phone, and no #stage: at phone width the stage screen is the default.
    page = Page(browser, served.url + "/", width=390, height=844)
    page.page.wait_for_selector(".card")
    yield page.page
    page.close()


def test_one_glance_says_which_rig_whether_it_is_up_and_what_is_not_measured(stage):
    expect(stage.locator(".stage-head .rig-name")).to_have_text("electric_board")
    expect(stage.locator(".stage-head .detail-line")).to_have_text("Up 12 min · sound not measured")
    # Up and unmeasured: the up colour, never the measured one (#49).
    expect(stage.locator(".stage-head .dot")).to_have_class("dot tone-up")
    expect(stage.locator(".tile")).to_have_text(["LOAD—not measured", "XRUNS—not measured", "AMPnone"])
    expect(stage.locator(".level")).to_have_text(["board0 dB", "vocal−6 dB"])
    expect(stage.locator(".card .name")).to_have_text(["overdrive", "octave", "delay", "vocal_delay"])
    expect(stage.locator('.card[data-unit="overdrive"] .vals')).to_have_text("Drive 0.62 · Level −3.0 · Tone 480")
    expect(stage.locator(".problem")).to_have_count(0)


def test_the_stage_fits_a_phone_and_every_target_takes_a_thumb(stage):
    width = stage.evaluate("() => [document.documentElement.scrollWidth, innerWidth]")
    assert width[0] <= width[1], "no sideways scrolling at phone width"
    sizes = stage.evaluate("""() => [...document.querySelectorAll('.stage-screen button, .stage-screen a')]
      .map((e) => [e.getAttribute('aria-label') || e.textContent.trim(), e.getBoundingClientRect().width, e.getBoundingClientRect().height])""")
    small = [s for s in sizes if s[1] < 44 or s[2] < 44]
    assert small == [], small
    bypass = stage.get_by_role("button", name="Bypass overdrive").bounding_box()
    assert bypass["width"] >= 56 and bypass["height"] >= 56


def test_on_stage_one_tap_bypasses_and_one_opens_the_knobs_lock_on(stage, served):
    expect(stage.locator("button.lock")).to_have_text("Locked")
    expect(stage.locator(".lock-note")).to_contain_text("Add, remove and reorder are off while locked")
    stage.get_by_role("button", name="Bypass delay").click()
    until(lambda: served.rig.bypassed["delay"] is True)
    expect(stage.locator('.card[data-unit="delay"]')).to_have_class(re.compile(r"\bbypassed\b"))
    stage.locator('.card[data-unit="overdrive"] button.open-card').click()
    tone = slider(stage, "overdrive Tone")
    tone.focus()
    stage.keyboard.press("PageUp")
    until(lambda: abs(served.rig.info["overdrive"]["Tone"]["value"] - (0.4222222222 + 0.1)) < 1e-6)
    expect(tone).to_have_attribute("aria-valuetext", "570")


def test_the_stage_shows_the_agents_last_error(stage, served):
    served.rig.refuse["octave"] = "Sushi did not confirm the bypass (test)"
    stage.get_by_role("button", name="Turn on octave").click()
    expect(stage.locator(".problem.agent")).to_contain_text("Sushi did not confirm the bypass (test)")
    expect(stage.locator(".problem.refused")).to_contain_text("Not changed: octave")
