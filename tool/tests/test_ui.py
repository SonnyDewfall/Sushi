"""The web UI, v0 (#31): the rack (S1), its Save (S4) and takes (S8), and the
stage screen (S5) with its record bar (S7).

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
from pathlib import Path
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
    assert (level["unit"], level["scale"], level["control"], level["phone"]) == \
        (None, "linear", "fader", 1), "the plugin declares no unit, so none is invented"


def test_only_what_the_browser_reads_is_sent_and_no_range():
    """The range comes from Sushi's parameter_info, which is what Sushi
    normalises over; a profile's restated min and max, `order` (used here to
    sort) and `enumeration` would be a contract the page does not honour."""
    flat, _ = panel_profile(gxts9_profile())
    assert set(flat["parameters"][0]) == {
        "name", "label", "group", "visibility", "scale", "control", "phone", "description",
        "unit", "integer", "toggled", "scale_points"}


def test_ungrouped_parameters_go_last_in_their_own_order():
    """Ranked once every group is known: an undefined group met between two
    ungrouped parameters must not split them by file position."""
    flat, problems = panel_profile({
        "_file": "u.yaml", "groups": [{"id": "a", "label": "A"}],
        "parameters": [
            {"name": "U1", "order": {"value": 5}},
            {"name": "X", "group": {"value": "x"}},
            {"name": "U2", "order": {"value": 1}},
        ],
    })
    assert [p["name"] for p in flat["parameters"]] == ["X", "U2", "U1"]
    assert problems == ["u.yaml: X names group 'x', which groups does not define"]


@pytest.mark.parametrize("bad, said", [
    ({"parameters": [{"name": "Drive", "group": {"value": ["dirt"]}}]}, "Drive's group is not a name: ['dirt']"),
    ({"plugin": "not a mapping", "parameters": [{"name": "Drive", "group": {"value": {"a": 1}}}]}, "group is not a name"),
    ({"parameters": 3}, "parameters is not a list: 3"),
    ({"groups": "dirt"}, "groups is not a list: 'dirt'"),
])
def test_a_profile_that_cannot_be_laid_out_is_named_and_the_rest_served(bad, said):
    answer = panel_profiles({GXTS9: gxts9_profile(), "urn:bad": {"_file": "bad.yaml", **bad}}, [])
    assert list(answer["profiles"]) == [GXTS9]
    assert len(answer["problems"]) == 1
    assert answer["problems"][0].startswith("bad.yaml: ") and said in answer["problems"][0]
    assert answer["problems"][0].endswith("this profile is not used")


def test_a_non_mapping_plugin_does_not_stop_a_profile_being_laid_out():
    flat, problems = panel_profile({"_file": "p.yaml", "plugin": ["x"], "parameters": [{"name": "A"}]})
    assert (flat["uri"], [p["name"] for p in flat["parameters"]], problems) == (None, ["A"], [])


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
    kwargs.setdefault("root", checkout_root())
    return Agent(**kwargs)


def test_the_page_and_its_files_are_served_and_nothing_beside_them():
    status, kind, body = call(ui_agent(live=UiRig()), "GET", "/")
    assert status == 200 and kind.startswith("text/html") and b'src="/ui/app.js"' in body
    status, kind, _ = call(ui_agent(live=UiRig()), "GET", "/ui/rig.js")
    assert status == 200 and "javascript" in kind, "a module script must be served as JavaScript"
    for path in ("/ui/../__init__.py", "/ui/%2e%2e/__init__.py", "/ui/../../agent.py", "/ui/__init__.py"):
        status, _, body = call(ui_agent(live=UiRig()), "GET", path)
        assert status in (403, 404) and b"import" not in body, path


@pytest.mark.parametrize("path", ["/ui/", "/ui"])
def test_the_ui_folder_itself_sends_the_browser_to_the_page(path):
    """#111: /ui/ typed by hand answered a JSON 403, which read as a broken
    UI. The folder is not a page; the page is /. A 302, not a 301, which a
    browser would keep for good."""
    async def go():
        async with TestClient(TestServer(ui_agent(live=UiRig()).app())) as client:
            response = await client.get(path, allow_redirects=False)
            return response.status, response.headers.get("Location")
    assert asyncio.run(go()) == (302, "/")


def test_the_rigs_a_start_or_switch_can_name_are_config_rigs():
    """The picker's list: config/rigs/ by name, in program order, whether or
    not the running rig's MIDI map switches rigs (which is when /rig lists
    them as `programs`). Which one is up is /health's to say."""
    import json

    status, _, body = call(ui_agent(live=UiRig()), "GET", "/rigs")
    answer = json.loads(body)
    # By file name, as a Program Change counts them: "electric-chorus-2.yaml"
    # sorts before "electric-chorus.yaml", though its name sorts after.
    names = [p.stem for p in sorted((checkout_root() / "config" / "rigs").glob("*.yaml"), key=lambda p: p.name)]
    assert status == 200 and answer == {"ok": True, "rigs": names} and "electric_board" in names


def profiles_root(tmp_path, *extra):
    """A checkout root whose profiles are gxts9's and `extra`, (name, text) pairs."""
    import shutil

    folder = tmp_path / PROFILES_DIR
    folder.mkdir(parents=True)
    shutil.copy(checkout_root() / PROFILES_DIR / "gxts9.yaml", folder / "gxts9.yaml")
    for name, text in extra:
        (folder / name).write_text(text, encoding="utf-8")
    return tmp_path


BAD_PROFILE = """schema_version: 1
plugin: {uri: 'urn:bad', name: Bad}
parameters:
  - name: Drive
    group: {value: [dirt], provenance: draft}
"""


def test_one_bad_profile_file_is_a_problem_not_a_500(tmp_path):
    import json

    root = profiles_root(tmp_path, ("bad.yaml", BAD_PROFILE))
    status, _, body = call(ui_agent(live=UiRig(), root=root), "GET", "/profiles")
    answer = json.loads(body)
    assert status == 200 and list(answer["profiles"]) == [GXTS9]
    assert answer["problems"] == ["bad.yaml: Drive's group is not a name: ['dirt']; this profile is not used"]


def test_the_ui_files_are_in_the_package_data_a_wheel_carries():
    """`add_static` serves them from the installed package, so a non-editable
    install must carry every one: each file must match a package-data glob.
    (A wheel built from this tree was checked to hold them, 2026-11-02.)"""
    import fnmatch

    tomllib = pytest.importorskip("tomllib")
    config = tomllib.loads((checkout_root() / "tool" / "pyproject.toml").read_text(encoding="utf-8"))
    globs = config["tool"]["setuptools"]["package-data"]["sushi_rig"]
    package = UI_DIR.parent.parent
    files = [path.relative_to(package).as_posix() for path in UI_DIR.iterdir() if path.is_file()]
    assert sorted(files) == ["ui/static/app.js", "ui/static/index.html", "ui/static/rig.js", "ui/static/style.css"]
    assert [f for f in files if not any(fnmatch.fnmatch(f, g) for g in globs)] == []


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


def test_a_move_of_a_processor_since_removed_keeps_the_chain_it_started_from(blank):
    """[a, b, c]; c moved up, then removed. The move's own `previous` is the
    baseline, not the chain the remove found."""
    live = {"tracks": {"board": ["a", "b"]}, "processors": {}, "parameter_info": {}}
    log = [entry("move", {"processor": "c", "direction": "up"}, ["a", "b", "c"]),
           entry("remove", {"processor": "c"}, {"track": "board", "chain": ["a", "c", "b"], "uri": None, "uid": None, "type": "lv2"}, 11)]
    assert unsaved(blank, log, live)["items"] == [
        {"kind": "chain", "track": "board", "from": ["a", "b", "c"], "to": ["a", "b"], "source": None}]


@pytest.mark.parametrize("log, first", [
    ([], 0),
    ([entry("parameter", {"processor": "a", "parameter": "x", "value": 1}, 0)], 0),
    ([entry("parameter", {}, 0), entry("bypass", {}, False), entry("undo", {"of": "bypass"}, True, undoes=8)], 7),
    ([entry("move", {}, []), entry("stale", {"of": "move"}, None, stale=0), entry("parameter", {}, 0), entry("undo", {}, 0, undoes=2)], 0),
    ([entry("parameter", {}, 0), entry("rig", {"rig": "b"}, "a")], 0),
    ([entry("rig", {"rig": "b"}, "a"), entry("bypass", {}, True)], 3),
    ([entry("parameter", {}, 0), entry("save", {"file": "config/rigs/a.yaml"}, "config/rigs/a.yaml")], 2),
    ([entry("save", {"file": "config/rigs/a.yaml"}, "config/rigs/a.yaml")], 0),
    ([entry("rig", {"rig": "b"}, "a"), entry("save", {}, None)], 0),
    ([entry("parameter", {}, 0), entry("save", {}, None), entry("undo", {}, 0, undoes=5)], 5),
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


# -- saving (S4) -------------------------------------------------------------------

def test_unsaved_counts_from_the_newest_save_and_comes_back_after_it(blank, served):
    live = served.rig.snapshot(served.rig.connect())  # Drive 0.62
    drive = lambda v, was, s: entry("parameter", {"processor": "overdrive", "parameter": "Drive", "value": v}, was, s)  # noqa: E731
    saved = entry("save", {"file": "config/rigs/electric_board.yaml", "how": "updated"}, "config/rigs/electric_board.yaml", 12)
    assert len(unsaved(blank, [drive(0.62, 0.4, 10)], live)["items"]) == 1
    assert unsaved(blank, [drive(0.62, 0.4, 10), saved], live)["items"] == [], "the save wrote it"
    # A change after the save is unsaved, from what the save wrote.
    after = unsaved(blank, [drive(0.62, 0.4, 10), saved, drive(0.62, 0.62, 13)], {**live})["items"]
    assert after == [], "moved to what the file has is nothing to save"
    changed = copy.deepcopy(live)
    changed["parameter_info"]["overdrive"]["Drive"]["value"] = 0.3
    assert unsaved(blank, [drive(0.62, 0.4, 10), saved, drive(0.3, 0.62, 13)], changed)["items"] == [
        {"kind": "parameter", "processor": "overdrive", "parameter": "Drive", "from": 0.62, "to": 0.3, "source": None}]
    # And so is an undo after it: undo passes over the save and puts back
    # what the file does not have.
    rolled = copy.deepcopy(live)
    rolled["parameter_info"]["overdrive"]["Drive"]["value"] = 0.4
    undo = entry("undo", {"of": "parameter", "processor": "overdrive", "parameter": "Drive", "value": 0.4}, 0.62, 14, undoes=0)
    assert unsaved(blank, [drive(0.62, 0.4, 10), saved, undo], rolled)["items"] == [
        {"kind": "parameter", "processor": "overdrive", "parameter": "Drive", "from": 0.62, "to": 0.4, "source": None}]
    # A trimmed log that kept the save has its baseline: nothing is missing.
    assert unsaved(blank, [saved], live, first=40)["partial"] is False


@pytest.mark.parametrize("name", ["electric-board-2", "a", "x" * 64, "Board", "../hall", "-lead", "x" * 65,
                                  "a b", "", "café", "hall\n"])
def test_the_page_checks_a_name_by_the_rule_the_agent_saves_by(blank, name):
    from sushi_rig.save import NAME_PATTERN, SaveError, validate_name

    assert rigjs(blank, "return R.NAME_PATTERN;") == NAME_PATTERN.pattern
    try:
        validate_name(name)
        accepted = True
    except SaveError:
        accepted = False
    problem = rigjs(blank, "return R.checkRigName(arg);", name)
    assert (problem is None) == accepted, problem
    assert rigjs(blank, "return R.checkRigName(arg, arg);", "board") == "board is the running rig: Save writes into it."


def test_save_is_offered_whenever_a_rig_saveable_from_here_is_up(blank):
    """Not only while the unsaved list has something: it is what the agent
    saw, and the panel, pedals mapped in Sushi, or an agent restarted since
    leave the rig differing from its file with nothing listed (review,
    2026-11-02). A v1 run and one with its panel are not offered, with how."""
    def view(rig, busy=None, items=1, pending=None, log=None, agent_started="2026-11-02T09:00:00+00:00"):
        health = {"ok": True, "body": {"rig": rig, "agent": {"busy": busy, "started_at": agent_started}}}
        return rigjs(blank, "return R.saveView(arg.health, {items: new Array(arg.items).fill({})}, arg.pending, arg.log);",
                     {"health": health, "items": items, "pending": pending, "log": log})

    up = {"running": True, "config_name": "electric_board", "rig": "config/rigs/electric_board.yaml",
          "mode": "headless", "started_at": "2026-11-02T10:00:00+00:00"}
    v = view(up)
    assert (v["save"]["label"], v["save"]["disabled"], v["saveAs"]["disabled"]) == ("Save to electric_board", False, False)
    assert "not committed" in v["note"]
    v = view(up, items=0)
    assert not v["save"]["disabled"] and not v["saveAs"]["disabled"], "nothing seen is not nothing to save"
    assert v["note"].startswith("No changes seen since 2026-11-02T10:00:00+00:00. The agent does not see")
    assert view(up, items=0, agent_started="2026-11-02T11:00:00+00:00")["since"] == "2026-11-02T11:00:00+00:00", \
        "an agent started after the rig saw nothing before it"
    log = {"log": [entry("save", {"file": "x"}, None, 30)], "first": 0}
    assert view(up, items=0, log=log)["since"] == at(30)
    v = view(up, busy="restart")
    assert v["save"]["disabled"] and v["saveAs"]["disabled"] and v["note"] == "restart is running: saving waits for it."
    v = view(up, pending={"name": "b"})
    assert v["save"]["disabled"] and v["note"] == "Saving as b…"
    v = view({"running": False})
    assert v["save"]["disabled"] and v["saveAs"]["disabled"] and v["note"] == "No rig is running to save."
    v = view({**up, "rig": None, "config_name": "guitar_vocal"})
    assert v["save"]["disabled"] and v["saveAs"]["disabled"]
    assert v["note"] == ("guitar_vocal is a v1 config, not a rig of channels started with --rig, so it is not saved "
                         "from here. Save it on the rig with: sushi-rig save <name> --rig <its rig.yaml>")
    for mode in ("panel", None):
        v = view({**up, "mode": mode})
        assert v["save"]["disabled"] and v["saveAs"]["disabled"]
        assert "save with the panel's SAVE button" in v["note"]


def test_a_saves_answer_says_what_was_written_and_what_git_says_of_the_file(blank):
    def outcome(name, answer):
        return rigjs(blank, "return R.saveOutcome(arg.name, arg.answer);", {"name": name, "answer": answer})

    lib = {"commit": "0123456789ab", "dirty": True, "file": "changed", "others": False}
    body = {"ok": True, "file": "config/rigs/electric_board.yaml", "rig": "electric_board", "how": "updated",
            "previous": "config/rigs/electric_board.yaml", "library": lib,
            "amp": {"saved": True, "model": "amp/models/Clean Room.nam"}}
    said = outcome(None, {"ok": True, "body": body})
    assert said["title"] == "Saved electric_board"
    assert said["text"].startswith("Wrote config/rigs/electric_board.yaml in place, keeping its comments.")
    assert "as the agent last sent it (Clean Room)" in said["text"]
    tail = " sushi-rig does not commit: that is yours to do."
    assert said["commit"] == "Not committed: changed on top of commit 0123456." + tail
    new = {**body, "file": "config/rigs/b.yaml", "rig": "b", "how": "new",
           "library": {**lib, "dirty": False, "file": "untracked"},
           "amp": {"saved": False, "why": "the running rig has no amp: it was started with --no-amp, so the file's amp was left as it was"}}
    said = outcome("b", {"ok": True, "body": new})
    assert said["title"] == "Saved as b" and "now the running rig" in said["text"]
    assert "without the comments of config/rigs/electric_board.yaml" in said["text"]
    assert "The amp is not saved: the running rig has no amp" in said["text"]
    assert said["commit"] == "Not committed: a new file git does not track yet, on commit 0123456." + tail

    def commit(**over):
        return rigjs(blank, "return R.commitText(arg);", {**lib, **over})

    # The checkout's `dirty` is not the file's: each is said for itself.
    assert commit(file="unchanged", others=True) == \
        "Nothing to commit: the file is as commit 0123456 has it. Other tracked files have uncommitted changes too."
    assert commit(others=True) == \
        "Not committed: changed on top of commit 0123456. Other tracked files have uncommitted changes too." + tail
    assert commit(file="ignored") == "Not committed: git ignores this file (on commit 0123456)."
    assert commit(file=None, others=None).startswith("Not committed, on commit 0123456: whether this file differs")
    assert rigjs(blank, "return R.commitText(null);").startswith("Not committed. Which commit it sits on is not known")
    refused = outcome("hall", {"ok": False, "reason": "a rig called hall already exists", "code": "taken"})
    assert refused == {"ok": False, "title": "Not saved as hall", "reason": "a rig called hall already exists", "code": "taken"}
    half = outcome("b", {"ok": False, "reason": "config/rigs/b.yaml was written ...", "code": None,
                         "body": {"ok": False, "recorded": False, "file": "config/rigs/b.yaml"}})
    assert half["title"] == "Written, but not running: config/rigs/b.yaml", "not \"not saved\": the file is there"


def test_the_last_save_says_what_git_said_then_not_now(blank):
    """From the log entry alone, so it cannot know of a commit since: it says
    "when saved" (review, 2026-11-02)."""
    last = {"file": "config/rigs/a.yaml", "at": at(5), "library": {"file": "changed"}}
    assert rigjs(blank, "return R.lastSaveText(arg);", last) == {
        "time": at(5), "file": "config/rigs/a.yaml", "then": "not committed when saved"}
    assert rigjs(blank, "return R.lastSaveText(arg).then;", {**last, "library": {"file": "unchanged"}}) == \
        "as its commit had it when saved"
    assert rigjs(blank, "return R.lastSaveText(arg).then;", {**last, "library": None}) == "not committed when saved"
    assert rigjs(blank, "return R.lastSaveText(null);") is None


def test_the_last_save_is_the_newest_save_in_the_log(blank):
    log = [entry("save", {"file": "a"}, None, 10), entry("parameter", {}, 0, 11), entry("save", {"file": "b"}, None, 12)]
    assert rigjs(blank, "return R.lastSave(arg);", {"log": log, "first": 0}) == {"file": "b", "at": at(12)}
    assert rigjs(blank, "return R.lastSave(arg);", {"log": log[1:2], "first": 0}) is None
    assert rigjs(blank, "return R.describeEntry(arg);", log[0]) == "save to a"


# -- the rig's state, and the lock -------------------------------------------------

def test_an_up_rig_is_never_shown_as_measured_and_down_is_said(blank):
    states = rigjs(blank, """
      const health = (rig, agent = {}) => ({ok: true, body: {rig, agent}});
      const up = {running: true, config_name: 'electric_board', uptime_seconds: 4320};
      return [
        R.rigStatus(health(up), {ok: true}),
        R.rigStatus(health(up), {ok: false, reason: 'Sushi went away'}),
        R.rigStatus(health({running: false, message: 'No rig is running.'}), null),
        R.rigStatus(health(up, {busy: 'restart'}), null),
        R.rigStatus({ok: false, reason: 'the agent did not answer'}, null),
      ].map((s) => [s.tone, s.title, s.detail, s.measured]);""")
    assert states == [
        ["up", "electric_board", "Up 1 h 12 min · sound not measured", False],
        ["down", "electric_board", "Up, but Sushi did not answer: Sushi went away", False],
        # A rig not running is a state the player chose, not a failure (#111).
        ["off", "Rig down", "No rig is running.", False],
        ["busy", "electric_board", "restart is running", False],
        ["down", "Agent not answering", "the agent did not answer", False],
    ]


def test_live_is_asked_for_only_while_a_rig_is_up_and_still(blank):
    """#111: with no rig up, /live answers 503 every second, and those were
    110 console errors in one session that buried any real one."""
    asks = rigjs(blank, """
      const health = (rig, agent = {}) => ({ok: true, body: {rig, agent}});
      return [
        R.readsLive(health({running: true})),
        R.readsLive(health({running: false})),
        R.readsLive(health({running: true}, {busy: 'up'})),
        R.readsLive({ok: false, reason: 'no agent'}),
        R.readsLive(null),
      ];""")
    assert asks == [True, False, False, False, False]


def test_a_two_state_control_is_on_past_half_its_travel_whatever_its_scale(blank):
    out = rigjs(blank, """
      const info = (v) => ({automatable: true, value: v, min_domain_value: 0, max_domain_value: 1});
      const linear = R.makeControl('m', info(0.3), {control: 'toggle', scale: 'linear'});
      const toggled = R.makeControl('t', info(1), {scale: 'toggle', toggled: true});
      return [R.isOn(linear, 0.3), R.isOn(linear, 0.7), R.isOn(linear, null), R.formatValue(linear, 0.7),
              R.isOn(toggled, 1), R.isOn(toggled, 0)];""")
    assert out == [False, True, False, "0.70", True, False]


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
    expect(changes.locator("h2")).to_have_text("No changes seen")
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
    # From pw-top (fixtures/pw-top-b-n2.txt): sushi 3 % and NAM 23 % of a
    # 1024-sample block at 48 kHz, which Sushi's driver states; 2 xruns
    # before the page looked, so none since.
    expect(stage.locator(".tile")).to_have_text(["LOAD26%of 21.3 ms", "XRUNS0since opened", "AMPnone"])
    expect(stage.locator(".detail-line")).to_have_attribute("title", re.compile(r"^Nothing measures the rig's sound yet"))
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
    # Start, stop and switch are not there to be tapped at all while locked.
    expect(stage.locator(".stage-rigs")).to_be_hidden()
    expect(stage.get_by_role("button", name=re.compile("^Stop "))).to_have_count(0)
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


# -- what the page fetches, and when ----------------------------------------------------

def gxts9_info():
    from ui_fake import FIXTURES, info_of, probe_ports

    return info_of(probe_ports(FIXTURES / "probe_gxts9.json"))


@pytest.mark.parametrize("how", ["config_name", "started_at", "rig switch in the log"])
def test_a_rig_with_the_same_names_and_another_plugin_gets_that_plugins_profile(rack, served, how):
    delay = rack.locator('.unit[data-unit="delay"]')
    expect(delay).to_contain_text("no profile: every parameter")
    with served.rig.lock:  # the agent reads the rig from its worker threads
        served.rig.classes["delay"] = {"uri": GXTS9, "uid": None, "type": "lv2"}
        served.rig.info["delay"] = gxts9_info()
    before = served.state["describe"]
    if how == "config_name":
        served.state["describe"] = lambda: {**before(), "config_name": "electric_board_2", "rig": "electric_board_2"}
    elif how == "started_at":
        served.state["describe"] = lambda: {**before(), "started_at": datetime.now(timezone.utc).isoformat()}
    else:  # a live swap (#93) writes a rig entry and need not restart
        served._loop.call_soon_threadsafe(served.agent._record, "rig", {"rig": "electric_board", "program": 3}, "electric_board")
    expect(delay).to_contain_text("GxTubeScreamer \u00b7 profile")
    expect(delay.get_by_role("slider")).to_have_count(3)
    expect(slider(rack, "delay Drive")).to_have_attribute("aria-valuetext", "0.50")


TOGGLE_PROFILE = """schema_version: 1
plugin: {uri: 'http://drobilla.net/plugins/mda/Delay', name: MDA Delay}
parameters:
  - name: FX Mix
    visibility: {value: primary, provenance: draft}
    scale: {value: linear, provenance: draft}
    control: {value: toggle, provenance: draft}
"""


def test_a_toggle_control_on_a_linear_scale_says_whether_it_is_on(browser, tmp_path):
    with Served(root=profiles_root(tmp_path, ("delay.yaml", TOGGLE_PROFILE))) as served:
        page = Page(browser, served.url + "/#rack")
        switch = page.page.get_by_role("button", name="delay FX Mix", exact=True)
        expect(switch).to_have_attribute("aria-pressed", "false")  # 0.30
        with served.rig.lock:
            served.rig.info["delay"]["FX Mix"]["value"] = 0.7
        expect(switch).to_have_attribute("aria-pressed", "true")
        switch.click()
        until(lambda: served.rig.info["delay"]["FX Mix"]["value"] == 0.0)
        expect(switch).to_have_attribute("aria-pressed", "false")
        page.close()


def advance(page, seconds):
    """Run the page's clock on, a second at a time, letting each poll's
    requests come back in real time between."""
    for _ in range(seconds):
        page.clock.run_for(1000)
        page.wait_for_timeout(120)


def test_profile_problems_are_shown_once_and_not_fetched_for_again(browser, tmp_path):
    with Served(root=profiles_root(tmp_path, ("bad.yaml", BAD_PROFILE))) as served:
        page = Page.__new__(Page)
        page.page = browser.new_page()
        page.errors = []
        asked = []
        page.page.on("request", lambda r: asked.append(r.url) if r.url.endswith("/profiles") else None)
        page.page.clock.install()
        page.page.goto(served.url + "/#rack")
        expect(page.page.locator(".problem.page")).to_contain_text("bad.yaml: Drive's group is not a name")
        advance(page.page, 15)
        assert len(asked) == 1, "a loader problem is not a failed fetch"
        expect(page.page.locator('.unit[data-unit="overdrive"]')).to_contain_text("GxTubeScreamer \u00b7 profile")
        page.close()


def test_profiles_and_names_that_fail_to_arrive_are_asked_for_again_and_hold_nothing_up(browser, served):
    page = Page.__new__(Page)
    page.page = browser.new_page()
    page.errors = []
    calls = {"profiles": 0, "library": 0}
    held = []

    def profiles(route):
        calls["profiles"] += 1
        if calls["profiles"] == 1:
            route.fulfill(status=500, json={"ok": False, "reason": "profiles folder unreadable (test)"})
        else:
            route.continue_()

    def library(route):
        calls["library"] += 1
        if calls["library"] == 1:
            held.append(route)  # the first library build has not finished
        else:
            route.continue_()

    page.page.route("**/profiles", profiles)
    page.page.route("**/library", library)
    page.page.clock.install()
    page.page.goto(served.url + "/#rack")
    # The rig shows while the library is still being built.
    expect(page.page.locator(".unit")).to_have_count(4)
    expect(page.page.locator(".problem.page")).to_contain_text("profiles folder unreadable (test)")
    held[0].abort()
    advance(page.page, 12)
    assert calls == {"profiles": 2, "library": 2}
    expect(page.page.locator('.unit[data-unit="overdrive"]')).to_contain_text("GxTubeScreamer \u00b7 profile")
    expect(page.page.locator(".problem.page")).to_have_count(0)
    page.close()


# --------------------------------------------------------------------------- #
# v0.1 (#111): a down rig is quiet, load from pw-top, start, stop and switch
# --------------------------------------------------------------------------- #

LOAD = {
    "nodes": [
        {"name": "NAM", "child": "amp", "running": True, "load": 0.23, "busy_ms": 4.9, "xruns": 2, "driver": "alsa_in"},
        {"name": "sushi", "child": "sushi", "running": True, "load": 0.03, "busy_ms": 0.675, "xruns": 0, "driver": "alsa_in"},
    ],
    "missing": [], "budget": {"quantum": 256, "rate": 48000, "block_ms": 5.333, "driver": "alsa_in"},
    "drivers": ["alsa_in"], "total_load": 0.26, "warn_at": 0.7,
}


def test_load_and_xruns_are_read_as_top_reads_them_and_unknowns_are_said(blank):
    tiles = rigjs(blank, """
      const ok = (body) => ({ok: true, body});
      const load = arg;
      const show = (t) => t.map((x) => [x.k, x.v, x.s, x.tone]);
      return {
        down: show(R.loadTiles(null, false, null)),
        waiting: show(R.loadTiles(null, true, null)),
        failed: R.loadTiles({ok: false, reason: 'pw-top is not installed'}, true, null).map((x) => [x.v, x.s, x.tone, x.title]),
        fine: show(R.loadTiles(ok(load), true, null)),
        title: R.loadTiles(ok(load), true, null)[0].title,
        // Three seen since the page began, the last sample adding them: climbing.
        rising: show(R.loadTiles(ok(load), true, {seen: {last: {}, count: 3}, added: 3})),
        steady: show(R.loadTiles(ok(load), true, {seen: {last: {}, count: 3}, added: 0})),
        near: show(R.loadTiles(ok({...load, total_load: 0.71}), true, null))[0],
        over: show(R.loadTiles(ok({...load, total_load: 1.2}), true, null))[0],
        unmeasured: R.loadTiles(ok({...load, total_load: null, nodes: [{...load.nodes[1], load: null}]}), true, null)[0],
        split: R.loadTiles(ok({...load, drivers: ['alsa_in', 'Dummy-Driver'], missing: ['fmit']}), true, null)[0],
      };""", LOAD)
    assert tiles["down"] == [["LOAD", "—", "rig down", ""], ["XRUNS", "—", "rig down", ""]]
    assert tiles["waiting"] == [["LOAD", "—", "reading", ""], ["XRUNS", "—", "reading", ""]]
    assert tiles["failed"][0][:3] == ["—", "unreadable", "failed"]
    assert tiles["failed"][0][3] == "pw-top could not be read: pw-top is not installed"
    assert tiles["fine"] == [["LOAD", "26%", "of 5.3 ms", ""], ["XRUNS", "0", "since opened", ""]]
    assert tiles["title"] == "NAM 23%, sushi 3% · quantum 256 @ 48000 Hz · clocked by alsa_in"
    assert tiles["rising"][1] == ["XRUNS", "3", "rising", "warn"]
    assert tiles["steady"][1] == ["XRUNS", "3", "since opened", ""]
    assert tiles["near"] == ["LOAD", "71%", "of 5.3 ms", "warn"]
    assert tiles["over"] == ["LOAD", "120%", "over budget", "over"]
    assert (tiles["unmeasured"]["v"], tiles["unmeasured"]["s"]) == ("—", "not measured"), "never 0 % unmeasured"
    assert "no timing for sushi" in tiles["unmeasured"]["title"]
    assert tiles["split"]["tone"] == "warn"
    assert "split across 2 clocks (alsa_in, Dummy-Driver)" in tiles["split"]["title"]
    assert "pw-top does not list fmit" in tiles["split"]["title"]


def test_xruns_are_counted_per_node_so_one_missing_then_back_adds_nothing(blank):
    """One sum over every node, reset when it fell, read a node missing from
    one sample and back in the next as its whole lifetime count, new and
    rising (found by review)."""
    steps = rigjs(blank, """
      const sample = (...pairs) => ({nodes: pairs.map(([name, xruns]) => ({name, xruns}))});
      const out = [];
      let seen = null;
      for (const body of [
        sample(['NAM', 2], ['sushi', 0]),      // the page's first look: a baseline, nothing new
        sample(['sushi', 0]),                  // NAM missing from this sample
        sample(['NAM', 2], ['sushi', 0]),      // ...and back, as it was: still nothing new
        sample(['NAM', 4], ['sushi', 1]),      // three more: rising
        sample(['NAM', 4], ['sushi', 1]),      // steady
        sample(['NAM', 1], ['sushi', 1]),      // NAM appeared afresh, with one already: one more
      ]) {
        const step = R.xrunsStep(seen, body);
        seen = step.seen;
        out.push([step.seen.count, step.added]);
      }
      return out;""")
    assert steps == [[0, 0], [0, 0], [0, 0], [3, 3], [3, 0], [4, 1]]


def test_a_start_is_a_headless_rig_of_channels_and_a_switch_names_its_rig(blank):
    calls = rigjs(blank, """return [R.lifecycleCall('start', 'electric-delay'), R.lifecycleCall('stop', 'x'),
                                    R.lifecycleCall('switch', 'accustomed')];""")
    assert calls == [
        {"path": "/rig/up", "json": {"config_name": "electric-delay", "rig": True, "headless": True}},
        {"path": "/rig/down", "json": {}},
        {"path": "/rig/switch", "json": {"config_name": "accustomed"}},
    ]


def test_the_picker_offers_what_the_rig_can_take_and_nothing_while_busy(blank):
    views = rigjs(blank, """
      const health = (rig, agent = {}) => ({ok: true, body: {rig, agent}});
      const rigs = {ok: true, body: {rigs: ['accustomed', 'electric-delay', 'electric_board']}};
      const up = {running: true, config_name: 'electric_board'};
      const acts = (v) => v.actions.map((a) => [a.op, a.label, a.disabled]);
      return {
        down: acts(R.rigPicker(health({running: false}), rigs, 'accustomed', null)),
        none: acts(R.rigPicker(health({running: false}), rigs, null, null)),
        gone: acts(R.rigPicker(health({running: false}), rigs, 'deleted-since', null)),
        same: acts(R.rigPicker(health(up), rigs, 'electric_board', null)),
        other: acts(R.rigPicker(health(up), rigs, 'electric-delay', null)),
        mine: R.rigPicker(health(up), rigs, 'electric-delay', {op: 'switch', name: 'electric-delay'}),
        theirs: R.rigPicker(health(up, {busy: 'restart'}), rigs, 'electric-delay', null),
        rows: R.rigPicker(health(up), rigs, 'electric-delay', null).rows,
        unlisted: R.rigPicker(health(up), {ok: false, reason: '500 boom'}, null, null).problem,
        noAgent: R.rigPicker({ok: false, reason: 'x'}, rigs, null, null),
      };""")
    assert views["down"] == [["start", "Start accustomed", False]]
    assert views["none"] == [["start", "Choose a rig to start", True]]
    assert views["gone"] == [["start", "Choose a rig to start", True]], "only a rig config/rigs/ still holds"
    assert views["same"] == [["stop", "Stop electric_board", False]]
    assert views["other"] == [["switch", "Switch to electric-delay", False], ["stop", "Stop electric_board", False]]
    assert views["mine"]["busy"] == "Switching to electric-delay…"
    assert all(a["disabled"] for a in views["mine"]["actions"])
    assert views["theirs"]["busy"] == "restart is running", "another client's command holds this one too"
    assert all(a["disabled"] for a in views["theirs"]["actions"])
    assert views["rows"] == [
        {"name": "accustomed", "up": False, "chosen": False},
        {"name": "electric-delay", "up": False, "chosen": True},
        {"name": "electric_board", "up": True, "chosen": False},
    ]
    assert views["unlisted"] == "config/rigs/ could not be listed: 500 boom"
    assert views["noAgent"]["actions"] == [] and "not answering" in views["noAgent"]["problem"]


def test_what_a_lifecycle_answer_says_how_and_why_a_switch_was_made(blank):
    said = rigjs(blank, """
      const ok = (body) => ({ok: true, status: 200, body});
      const no = (status, body) => ({ok: false, status, body, reason: body.reason, code: body.code});
      return [
        R.lifecycleOutcome('switch', 'electric-delay', ok({how: 'live', seconds: 0.412})),
        R.lifecycleOutcome('switch', 'accustomed', ok({how: 'restart', seconds: 6.2,
          why: [{code: 'amp', detail: 'the amp runs in Carla'}, {code: 'tracks', detail: 'master differs'}]})),
        R.lifecycleOutcome('switch', 'accustomed', no(409, {how: 'restart', why: [{code: 'amp', detail: 'x'}],
          reason: 'up failed; electric_board was brought back'})),
        R.lifecycleOutcome('switch', 'accustomed', no(409, {code: 'busy', reason: "busy: 'up' is still running"})),
        R.lifecycleOutcome('start', 'electric_board', ok({warnings: ['a patchbay link was not made']})),
        R.lifecycleOutcome('start', 'electric_board', no(409, {reason: 'error: a rig is already running'})),
        R.lifecycleOutcome('stop', 'electric_board', ok({})),
      ];""")
    assert said[0] == {"ok": True, "title": "Switched to electric-delay",
                       "text": "Switched to electric-delay live in 0.4 s: Sushi was not restarted."}
    assert said[1]["text"] == ("Switched to accustomed by restart in 6.2 s, because amp: the amp runs in Carla; "
                               "tracks: master differs.")
    assert said[2] == {"ok": False, "title": "Not switched to accustomed (by restart)",
                       "reason": "up failed; electric_board was brought back. It restarted because amp: x"}
    assert said[3] == {"ok": False, "title": "Not switched to accustomed", "reason": "busy: 'up' is still running"}
    assert said[4]["text"] == "Started electric_board, headless. Warnings: a patchbay link was not made"
    assert said[5] == {"ok": False, "title": "Not started: electric_board", "reason": "error: a rig is already running"}
    assert said[6]["text"] == "Stopped electric_board."


class Watched(Page):
    """A page, every request it made by path, and every console error: what
    #111 counted (110 errors while the rig was down)."""

    def __init__(self, browser, served, url, **size):
        self.asked = []
        self.console = []
        self.page = browser.new_page(viewport={"width": size.get("width", 1280), "height": size.get("height", 900)})
        self.errors = []
        self.page.on("pageerror", lambda error: self.errors.append(str(error)))
        self.page.on("console", lambda m: self.console.append(m.text) if m.type == "error" else None)
        self.page.on("request", lambda r: self.asked.append(r.url[len(served.url):]))
        self.page.goto(served.url + url)


def test_a_down_rig_is_a_state_and_the_page_does_not_ask_it_for_anything(browser):
    from ui_fake import UiLifecycle

    with Served(lifecycle=UiLifecycle(up=None)) as served:
        page = Watched(browser, served, "/#rack")
        expect(page.page.locator(".rig-name")).to_have_text("Rig down")
        expect(page.page.locator(".status")).to_have_class(re.compile(r"\btone-off\b"))
        page.page.wait_for_timeout(3500)  # three polls and more
        assert page.asked.count("/health") >= 3
        assert "/live" not in page.asked and "/rig/load" not in page.asked
        assert page.console == [], "a rig that is down is not an error"
        expect(page.page.locator(".problem")).to_have_count(0)
        expect(page.page.locator(".tile").first).to_have_text("LOAD—rig down")
        page.close()


def test_a_live_that_fails_while_the_rig_is_up_is_still_the_error_it_is(browser):
    rig = UiRig()

    def refuse(*_):
        raise __import__("sushi_rig.live", fromlist=["LiveError"]).LiveError("Sushi stopped answering (test)")

    rig.connect = refuse
    with Served(rig) as served:
        page = Watched(browser, served, "/#rack")
        status = page.page.locator(".status")
        expect(status).to_have_text("Up, but Sushi did not answer: Sushi stopped answering (test)")
        expect(status).to_have_class(re.compile(r"\btone-down\b"))
        expect(page.page.locator(".problem.agent")).to_contain_text("Sushi stopped answering (test)")
        assert "/live" in page.asked
        assert any("502" in line for line in page.console), page.console
        page.close()


def rack_page(browser, served):
    page = Watched(browser, served, "/#rack")
    page.page.wait_for_selector(".rigs .rig")
    return page


def test_a_rig_is_started_from_the_picker_headless_and_busy_is_shown_meanwhile(browser):
    from ui_fake import UiLifecycle

    world = UiLifecycle(up=None)
    world.delay = 1.5
    with Served(lifecycle=world) as served:
        page = rack_page(browser, served)
        p = page.page
        start = p.get_by_role("button", name="Choose a rig to start")
        expect(start).to_be_disabled()
        p.locator(".rig", has_text="electric_board").click()
        p.get_by_role("button", name="Start electric_board").click()
        expect(p.locator(".rig-busy")).to_have_text("Starting electric_board…")
        expect(p.get_by_role("button", name="Start electric_board")).to_be_disabled()
        expect(p.locator(".rig-name")).to_have_text("electric_board")  # the agent's busy line meanwhile
        expect(p.locator(".notice")).to_contain_text("Started electric_board, headless.", timeout=8000)
        assert world.calls == [["up", "electric_board", "--rig", "--headless"]]
        expect(p.locator(".rig.up")).to_have_text("electric_boardup now")
        expect(p.locator(".unit")).to_have_count(4)
        expect(p.get_by_role("button", name="Stop electric_board")).to_be_enabled()
        page.close()


def test_a_refused_start_shows_the_agents_reason_and_nothing_starts(browser):
    from ui_fake import UiLifecycle

    world = UiLifecycle(up=None)
    world.refuse_up = "no machine file for this host (test)"
    with Served(lifecycle=world) as served:
        page = rack_page(browser, served)
        p = page.page
        p.locator(".rig", has_text="electric-delay").click()
        p.get_by_role("button", name="Start electric-delay").click()
        alert = p.locator(".problem.refused")
        expect(alert).to_contain_text("Not started: electric-delay")
        expect(alert).to_contain_text("error: no machine file for this host (test)")
        expect(p.locator(".rig-name")).to_have_text("Rig down")
        assert world.calls == [["up", "electric-delay", "--rig", "--headless"]]
        page.close()


def test_stop_takes_a_confirm_because_it_cuts_the_sound(browser, served):
    page = rack_page(browser, served)
    p = page.page
    p.get_by_role("button", name="Stop electric_board").click()
    expect(p.locator(".confirm")).to_contain_text("Stop electric_board? The sound stops until a rig is started again.")
    p.get_by_role("button", name="Cancel").click()
    expect(p.locator(".confirm")).to_have_count(0)
    p.get_by_role("button", name="Stop electric_board").click()
    assert served.lifecycle.calls == [], "one tap stops nothing"
    p.get_by_role("button", name="Stop now").click()
    expect(p.locator(".rig-name")).to_have_text("Rig down")
    expect(p.locator(".notice")).to_contain_text("Stopped electric_board.")
    assert served.lifecycle.calls == [["down"]]
    expect(p.get_by_role("button", name="Start electric_board")).to_be_enabled()
    page.close()


def test_a_switch_made_live_says_so(browser, served):
    page = rack_page(browser, served)
    p = page.page
    p.locator(".rig", has_text="electric-delay").click()
    p.get_by_role("button", name="Switch to electric-delay").click()
    expect(p.locator(".notice")).to_have_text(re.compile(
        r"^Switched to electric-delay live in 0\.4 s: Sushi was not restarted\."))
    expect(p.locator(".rig-name")).to_have_text("electric-delay")
    expect(p.locator(".rig.up")).to_have_text("electric-delayup now")
    assert served.lifecycle.calls == [["switch", "electric-delay"]]
    page.close()


def test_a_switch_made_by_restart_says_why(browser, served):
    served.lifecycle.how["accustomed"] = ("restart", [{"code": "amp", "detail": "the amp's settings differ (test)"}])
    page = rack_page(browser, served)
    p = page.page
    p.locator(".rig", has_text="accustomed").click()
    p.get_by_role("button", name="Switch to accustomed").click()
    expect(p.locator(".notice")).to_have_text(re.compile(
        r"^Switched to accustomed by restart in 6\.2 s, because amp: the amp's settings differ \(test\)\."))
    expect(p.locator(".rig-name")).to_have_text("accustomed")
    page.close()


def test_on_stage_the_picker_is_there_only_once_unlocked(browser, served):
    page = Watched(browser, served, "/", width=390, height=844)
    p = page.page
    p.wait_for_selector(".card")
    expect(p.locator(".stage-rigs")).to_be_hidden()
    lock = p.locator("button.lock")
    lock.click()
    lock.click()
    expect(lock).to_have_text("Lock off · editing")
    expect(p.locator(".stage-rigs .rig")).not_to_have_count(0)
    expect(p.get_by_role("button", name="Stop electric_board")).to_be_visible()
    width = p.evaluate("() => [document.documentElement.scrollWidth, innerWidth]")
    assert width[0] <= width[1], "the picker fits a phone"
    small = p.evaluate("""() => [...document.querySelectorAll('.stage-rigs button')]
      .map((e) => [e.textContent.trim(), e.getBoundingClientRect().height]).filter((s) => s[1] < 44)""")
    assert small == [], small
    lock.click()
    expect(p.locator(".stage-rigs")).to_be_hidden()
    page.close()


def test_a_load_update_leaves_the_lock_button_and_its_focus_alone(browser, served):
    """The tiles change with every sample. In the header's key, they rebuilt
    it every 2 s, Lock button and Stage link included: a click between press
    and release was lost, and so was keyboard focus (found by review)."""
    page = rack_page(browser, served)
    p = page.page
    load = p.locator(".tile").first
    expect(load).to_contain_text("26%")
    lock = p.locator("button.lock")
    lock.focus()
    handle = lock.element_handle()
    served.pw_top_output = served.pw_top_output.replace("0.23    2", "0.53    2")  # NAM's B/Q
    expect(load).to_contain_text("56%", timeout=8000)
    assert handle.evaluate("(e) => e.isConnected && document.activeElement === e"), \
        "the same Lock button, still focused"
    page.close()


def test_the_racks_empty_message_follows_the_rig_through_a_start_that_fails(browser):
    """With no rig the message depends on the tone, which was not in its
    key: "Choose one under RIGS" stayed while a rig started, and "The rig is
    changing" after a start that failed (found by review)."""
    from ui_fake import UiLifecycle

    world = UiLifecycle(up=None)
    world.delay = 1.5
    world.refuse_up = "no machine file for this host (test)"
    with Served(lifecycle=world) as served:
        page = rack_page(browser, served)
        p = page.page
        empty = p.locator(".rack .empty")
        expect(empty).to_contain_text("Choose one under RIGS to start it")
        p.locator(".rig", has_text="electric-delay").click()
        p.get_by_role("button", name="Start electric-delay").click()
        expect(empty).to_have_text("The rig is changing; the rack comes back when it is up.")
        expect(p.locator(".problem.refused")).to_contain_text("Not started: electric-delay", timeout=8000)
        expect(empty).to_contain_text("Choose one under RIGS to start it")
        page.close()


# --------------------------------------------------------------------------- #
# The amp: what was last sent to Carla, faders, and the model picker
# --------------------------------------------------------------------------- #
#
# Against `Served(amp=True)`: the agent's real amp routes over `UiAmp` in
# ui_fake.py (a FakeCarla, invented models, a stand-in for the Carla-only
# restart). Nothing here is Carla, or the restart's silence.

def amp_answer(**over):
    body = {
        "ok": True, "known": "sent", "known_note": "what this agent last sent Carla, not what Carla holds",
        "model": {"stored": "amp/models/Clean Room.nam", "name": "Clean Room", "path": "/x/Clean Room.nam",
                  "description": "Clean Room  (/x)\n  rate      48000 Hz  (matches the rig)", "problem": None,
                  "warning": None},
        "parameters": [
            {"name": "Input Lvl", "value": 0.0, "min": -20.0, "max": 20.0, "unit": "dB", "panel": True, "source": "replayed"},
            {"name": "Output Lvl", "value": -3.0, "min": -20.0, "max": 20.0, "unit": "dB", "panel": True, "source": "sent"},
            {"name": "Quality", "value": None, "min": 0.0, "max": 1.0, "unit": None, "panel": False, "source": None},
        ],
        "carla": {"pid": 4244, "alive": True}, "held": True, "override": None,
    }
    body.update(over)
    return {"ok": True, "status": 200, "body": body}


MODELS = {"ok": True, "status": 200, "body": {"rate": 48000, "models": [
    {"model": "Clean Room.nam", "name": "Clean Room", "usable": True, "playing": True, "why": None},
    {"model": "Edge.nam", "name": "Edge", "usable": True, "playing": False, "why": None},
    {"model": "Slow Tape.nam", "name": "Slow Tape", "usable": False, "playing": False,
     "why": "Slow Tape was trained at 44100 Hz"},
]}}


def test_the_amp_card_has_faders_for_the_panels_parameters_and_quality_shown_beside(blank):
    card = rigjs(blank, "return R.ampCard(arg.amp, arg.models);", {"amp": amp_answer(), "models": MODELS})
    assert card["state"] == "up" and card["known"] == "sent"
    assert [f["name"] for f in card["faders"]] == ["Input Lvl", "Output Lvl"]
    assert card["faders"][0] == {"name": "Input Lvl", "label": "Input Lvl", "min": -20.0, "max": 20.0, "unit": "dB",
                                 "step": 0.1, "disabled": False}
    assert card["shown"] == [{"name": "Quality", "text": "not sent",
                              "note": "not on a fader: it trades CPU for accuracy (a lite or a full path), not tone"}]
    assert card["model"]["name"] == "Clean Room" and "48000 Hz" in card["model"]["description"]
    options = card["picker"]["options"]
    assert [(o["model"], o["usable"], o["label"]) for o in options] == [
        ("Clean Room.nam", False, "Clean Room (playing)"), ("Edge.nam", True, "Edge"),
        ("Slow Tape.nam", False, "Slow Tape (cannot be played here)")]
    assert card["picker"]["canChange"] and card["picker"]["why"] is None
    assert "sound cuts out for a moment" in card["picker"]["warning"]


@pytest.mark.parametrize("over, options, why", [
    ({"known": "panel"}, {}, "The panel is running and owns the amp"),
    ({"carla": {"pid": 4244, "alive": False}}, {}, "Carla is not running"),
    ({"held": False}, {}, "did not start the running rig"),
    ({}, {"locked": True}, "Locked: changing the amp's model cuts the sound"),
    ({}, {"pending": "Edge.nam"}, "Loading Edge"),
])
def test_the_model_picker_says_why_it_cannot_be_used_when_it_cannot(blank, over, options, why):
    card = rigjs(blank, "return R.ampCard(arg.amp, arg.models, arg.options);",
                 {"amp": amp_answer(**over), "models": MODELS, "options": options})
    assert not card["picker"]["canChange"] and why in card["picker"]["why"]
    writable = "known" not in over and "carla" not in over
    assert all(f["disabled"] is not writable for f in card["faders"])


def test_an_amp_card_is_hidden_with_no_rig_and_says_why_with_no_amp(blank):
    states = rigjs(blank, """return [
        R.ampCard(null, null), R.ampCard({ok: false, code: 'no_rig', reason: 'x'}, null),
        R.ampCard({ok: false, code: 'busy', reason: 'x'}, null),
        R.ampCard({ok: false, code: 'no_amp', reason: 'the running rig has no amp: it was started with --no-amp'}, null),
        R.ampCard({ok: false, status: 0, reason: 'the agent did not answer'}, null)];""")
    assert [s["state"] for s in states] == ["hidden", "hidden", "hidden", "none", "failed"]
    assert states[3]["why"].endswith("--no-amp") and states[4]["why"] == "the agent did not answer"


def test_amp_values_are_in_their_own_units_and_one_never_sent_says_so(blank):
    shown = rigjs(blank, """const db = {unit: 'dB', min: -20, max: 20}, q = {unit: null, min: 0, max: 1};
        return [R.ampText(db, -3), R.ampText(db, 0), R.ampText(db, -0.04), R.ampText(q, 1), R.ampText(db, null),
                R.modelName('amp/models/Edge.nam'), R.modelName('/m/x.nam'), R.modelName(null)];""")
    assert shown == ["−3.0 dB", "0.0 dB", "0.0 dB", "1.00", "not sent", "Edge", "x", "none"]


def test_the_stage_amp_is_the_model_and_the_output_level_only(blank):
    view = rigjs(blank, "return [R.stageAmp(arg), R.stageAmp(null)];", amp_answer())
    assert view[0]["name"] == "Clean Room" and view[0]["output"]["name"] == "Output Lvl"
    assert view[1] is None


def test_unsaved_amp_changes_are_what_was_sent_against_what_the_first_replaced(blank, served):
    log = [entry("amp", {"name": "Output Lvl", "value": -1.0}, -3.0),
           entry("amp", {"name": "Output Lvl", "value": 2.0}, -1.0),
           entry("amp", {"name": "Input Lvl", "value": 1.0}, 0.0),
           entry("undo", {"of": "amp", "name": "Input Lvl", "value": 0.0}, 1.0, undoes=2),
           entry("amp_model", {"model": "amp/models/Edge.nam"}, "amp/models/Clean Room.nam")]
    amp = amp_answer(model={"stored": "amp/models/Edge.nam", "name": "Edge"})["body"]
    amp["parameters"][1]["value"] = 2.0
    got = rigjs(blank, "return R.unsavedChanges({log: arg.log, first: 0}, arg.live, null, arg.amp).items;",
                {"log": log, "live": served.rig.snapshot(0), "amp": amp})
    assert [(i["kind"], i.get("name"), i["from"], i["to"]) for i in got] == [
        ("amp", "Output Lvl", -3.0, 2.0), ("amp_model", None, "amp/models/Clean Room.nam", "amp/models/Edge.nam")]
    assert rigjs(blank, "return [R.describeEntry(arg[0]), R.describeEntry(arg[1])];", [log[0], log[4]]) == \
        ["amp · Output Lvl", "amp model Edge"]
    without = rigjs(blank, "return R.unsavedChanges({log: arg.log, first: 0}, arg.live, null).items;",
                    {"log": log, "live": served.rig.snapshot(0)})
    assert without == [], "no amp read: not said either way"


@pytest.fixture
def amp_served():
    with Served(amp=True) as served:
        yield served


def amp_rack(browser, served):
    page = Page(browser, served.url + "/#rack")
    page.page.wait_for_selector(".amp-card .fader")
    return page


def test_the_rack_shows_the_amp_with_its_model_faders_and_quality_not_on_one(browser, amp_served):
    page = amp_rack(browser, amp_served)
    p = page.page
    card = p.locator(".amp-card")
    expect(card.locator(".amp-model")).to_have_text("Clean Room")
    expect(card).to_contain_text("values are what was last sent, not read back")
    expect(card.get_by_role("slider")).to_have_count(2)
    expect(p.get_by_role("slider", name="amp Input Lvl")).to_have_attribute("aria-valuetext", "0.0 dB")
    expect(p.get_by_role("slider", name="amp Output Lvl")).to_have_attribute("aria-valuetext", "−3.0 dB")
    expect(p.get_by_role("slider", name="amp Quality")).to_have_count(0)
    expect(card.locator(".shown")).to_contain_text("Quality1.00")
    expect(card.locator("details.amp-about pre")).to_contain_text("matches the rig")
    expect(p.locator(".tile").last).to_have_text("AMPClean Room")
    page.close()


def test_an_amp_fader_is_sent_to_carla_marked_unsaved_and_undone(browser, amp_served):
    page = amp_rack(browser, amp_served)
    p = page.page
    output = p.get_by_role("slider", name="amp Output Lvl")
    output.focus()
    p.keyboard.press("ArrowRight")  # one step: a tenth of a dB, one change logged
    until(lambda: abs(amp_served.amp.carla.values().get("Output Lvl", 0) - -2.9) < 1e-6)
    expect(output).to_have_attribute("aria-valuetext", "−2.9 dB")
    expect(p.locator(".amp-card .fader.unsaved")).to_contain_text("was −3.0 dB")
    expect(p.locator("aside.changes .change")).to_have_text("amp · Output Lvl−3.0 dB to −2.9 dB")
    undo = p.locator("button.undo")
    expect(undo).to_have_text("Undo · amp · Output Lvl")
    undo.click()
    until(lambda: amp_served.amp.carla.values()["Output Lvl"] == -3.0)
    expect(output).to_have_attribute("aria-valuetext", "−3.0 dB")
    expect(p.locator("aside.changes h2")).to_have_text("No changes seen")
    page.close()


def test_an_amp_change_carla_does_not_answer_shows_the_agents_reason(browser, amp_served):
    page = amp_rack(browser, amp_served)
    p = page.page
    amp_served.amp.carla.answering = False
    p.get_by_role("slider", name="amp Input Lvl").focus()
    p.keyboard.press("ArrowRight")
    expect(p.locator(".problem.refused")).to_contain_text("Carla is not answering")
    expect(p.locator(".amp-card .fader.failed")).to_contain_text("refused: Carla is not answering")
    assert amp_served.amp.carla.sent == []
    page.close()


def test_the_model_is_changed_only_unlocked_and_after_a_confirm_that_warns_of_the_cut(browser, amp_served):
    page = amp_rack(browser, amp_served)
    p = page.page
    picker = p.get_by_role("combobox", name="Amp model")
    expect(picker).to_be_disabled()
    expect(p.locator(".amp-picker")).to_contain_text("Locked: changing the amp's model cuts the sound")
    lock = p.locator("button.lock")
    lock.click()
    lock.click()
    expect(picker).to_be_enabled()
    expect(p.locator(".amp-picker")).to_contain_text("the sound cuts out for a moment")
    # The option's own property: Playwright's to_be_disabled reads an
    # option as enabled whatever its attribute (seen with 1.63).
    expect(picker.locator("option[value='Slow Tape.nam']")).to_have_js_property("disabled", True)
    picker.select_option("Edge.nam")
    confirm = p.get_by_role("group", name="Confirm the model change")
    expect(confirm).to_contain_text("Load Edge? Changing the model restarts the amp: the sound cuts out for a moment")
    assert amp_served.amp.restarts == [], "nothing until the confirm"
    confirm.get_by_role("button", name="Load Edge").click()
    until(lambda: amp_served.amp.restarts)
    (restart,) = amp_served.amp.restarts
    assert restart["model"] == "amp/models/Edge.nam" and restart["override"] == "Edge.nam"
    assert restart["values"] == {"Input Lvl": 0.0, "Output Lvl": -3.0, "Quality": 1.0}
    expect(p.locator(".notice")).to_contain_text("the amp now plays Edge.nam. The sound stopped")
    expect(p.locator(".amp-card .amp-model")).to_have_text("Edge")
    expect(p.locator("aside.changes .change")).to_have_text("amp · modelClean Room to Edge")
    page.close()


def test_a_model_change_that_failed_says_so_and_whether_the_old_one_is_back(browser, amp_served):
    from sushi_rig.supervisor import AmpRestart

    amp_served.amp.restart_result = AmpRestart(False, "Carla exited as it started (status 1)", pid=7, restored=True)
    page = amp_rack(browser, amp_served)
    p = page.page
    lock = p.locator("button.lock")
    lock.click()
    lock.click()
    p.get_by_role("combobox", name="Amp model").select_option("Edge.nam")
    p.get_by_role("button", name="Load Edge").click()
    alert = p.locator(".problem.refused")
    expect(alert).to_contain_text("Amp model not changed: Edge")
    expect(alert).to_contain_text("Carla exited as it started (status 1); the previous model was brought back")
    expect(p.locator(".amp-card .amp-model")).to_have_text("Clean Room")
    page.close()


def test_with_the_panel_running_the_amp_card_writes_nothing_and_says_why(browser, amp_served):
    amp_served.amp.mode = "panel"
    page = amp_rack(browser, amp_served)
    p = page.page
    expect(p.get_by_role("slider", name="amp Output Lvl")).to_be_disabled()
    expect(p.locator(".amp-card")).to_contain_text("the panel's faders may have moved them since")
    expect(p.locator(".amp-picker")).to_contain_text("The panel is running and owns the amp")
    page.close()


def test_on_stage_the_amp_is_its_model_and_output_level_and_only_unlocked(browser, amp_served):
    page = Watched(browser, amp_served, "/", width=390, height=844)
    p = page.page
    p.wait_for_selector(".card")
    expect(p.locator(".stage-amp")).to_be_hidden()
    lock = p.locator("button.lock")
    lock.click()
    lock.click()
    amp = p.locator(".stage-amp")
    expect(amp.locator(".amp-model")).to_have_text("Clean Room")
    expect(amp.get_by_role("slider")).to_have_count(1)
    output = amp.get_by_role("slider", name="amp Output Lvl")
    expect(output).to_have_attribute("aria-valuetext", "−3.0 dB")
    height = output.evaluate("(e) => e.closest('.fader').getBoundingClientRect().height")
    assert height >= 44, "a thumb's target"
    output.focus()
    p.keyboard.press("ArrowLeft")
    until(lambda: abs(amp_served.amp.carla.values().get("Output Lvl", 0) - -3.1) < 1e-6)
    width = p.evaluate("() => [document.documentElement.scrollWidth, innerWidth]")
    assert width[0] <= width[1], "it fits a phone"
    lock.click()
    expect(p.locator(".stage-amp")).to_be_hidden()
    page.close()


def test_a_model_change_in_flight_is_said_as_the_amp_restarting(blank):
    status = rigjs(blank, """return R.rigStatus({ok: true, body: {rig: {running: true, config_name: 'board'},
        agent: {busy: 'amp_model'}}}, null);""")
    assert status["tone"] == "busy" and status["detail"].startswith("The amp is restarting on another model")


def test_the_updaters_stay_flat_as_the_amp_region_is_rebuilt(browser, amp_served):
    """Picking a model, cancelling, a model list arriving: each rebuilds the
    amp region alone, and each used to add its faders' updaters again to a
    list only a rack rebuild emptied (review)."""
    page = amp_rack(browser, amp_served)
    p = page.page
    lock = p.locator("button.lock")
    lock.click()
    lock.click()
    picker = p.get_by_role("combobox", name="Amp model")
    expect(picker).to_be_enabled()
    first = p.evaluate("() => window.sushiRigUpdaters()")
    assert first["amp"] == 2, "one per fader"
    for _ in range(5):
        picker.select_option("Edge.nam")
        p.get_by_role("button", name="Cancel").click()
        expect(p.get_by_role("group", name="Confirm the model change")).to_have_count(0)
    p.wait_for_timeout(1200)  # a poll or so, to rebuild whatever it would
    assert p.evaluate("() => window.sushiRigUpdaters()") == first
    expect(p.get_by_role("slider", name="amp Output Lvl")).to_have_attribute("aria-valuetext", "−3.0 dB")
    page.close()


def test_a_model_copied_in_while_the_page_is_open_shows_in_the_picker(browser, amp_served):
    from ui_fake import nam

    page = Page.__new__(Page)
    page.page = browser.new_page()
    page.errors = []
    asked = []
    page.page.on("request", lambda r: asked.append(r.url) if r.url.endswith("/amp/models") else None)
    page.page.clock.install()
    page.page.goto(amp_served.url + "/#rack")
    p = page.page
    expect(p.locator(".amp-picker option")).to_have_count(4)
    nam(amp_served.amp.root / "amp" / "models" / "Fresh.nam", "Fresh")
    advance(p, 10)
    assert len(asked) == 1, "not asked on every poll"
    advance(p, 22)
    expect(p.locator(".amp-picker option[value='Fresh.nam']")).to_have_count(1)
    assert len(asked) == 2
    page.close()


# -- saving from the rack (S4) ----------------------------------------------------

@pytest.fixture
def save_served():
    with Served(save=True) as served:
        yield served


def save_rack(browser, served):
    page = Page(browser, served.url + "/#rack")
    page.page.wait_for_selector(".unit")
    return page


def rig_file(served, name="electric_board"):
    import yaml

    return yaml.safe_load((served.save.root / "config" / "rigs" / f"{name}.yaml").read_text())


def drive_of(doc):
    return doc["channels"][0]["chain"][0].get("parameters", {}).get("Drive")


def turn_drive_down(page, steps=10):
    drive = slider(page, "overdrive Drive")
    drive.focus()
    for _ in range(steps):
        page.keyboard.press("ArrowDown")
    return drive


def test_save_writes_the_running_rigs_file_in_place_and_says_it_is_not_committed(browser, save_served):
    page = save_rack(browser, save_served)
    p = page.page
    save = p.get_by_role("button", name="Save to electric_board")
    # Offered with nothing seen: the agent does not see every change.
    expect(save).to_be_enabled()
    expect(p.locator("aside.changes h2")).to_have_text("No changes seen")
    expect(p.locator(".save-note")).to_contain_text("No changes seen since")
    turn_drive_down(p)
    expect(p.locator("aside.changes h2")).to_have_text("1 unsaved change")
    save.click()
    result = p.locator(".save-result.ok")
    expect(result).to_contain_text("Saved electric_board")
    expect(result).to_contain_text("Wrote config/rigs/electric_board.yaml in place, keeping its comments.")
    expect(result.locator(".commit")).to_have_text(
        f"Not committed: changed on top of commit {save_served.save.commit[:7]}. "
        "sushi-rig does not commit: that is yours to do.")
    assert abs(drive_of(rig_file(save_served)) - 0.52) < 1e-6
    text = (save_served.save.root / "config" / "rigs" / "electric_board.yaml").read_text()
    assert "# the drive: a comment the save keeps" in text
    # The save is the baseline: nothing seen since it.
    expect(p.locator("aside.changes h2")).to_have_text("No changes seen")
    expect(p.locator(".last-save")).to_contain_text("config/rigs/electric_board.yaml, not committed when saved.")
    expect(slider(p, "overdrive Drive")).not_to_have_class(re.compile(r"\bunsaved\b"))
    page.close()


def test_after_a_save_a_change_is_unsaved_again_and_so_is_an_undo(browser, save_served):
    page = save_rack(browser, save_served)
    p = page.page
    turn_drive_down(p, 1)  # one step, one entry: 0.62 to 0.61
    until(lambda: abs(save_served.rig.info["overdrive"]["Drive"]["value"] - 0.61) < 1e-9)
    p.get_by_role("button", name="Save to electric_board").click()
    expect(p.locator("aside.changes h2")).to_have_text("No changes seen")
    # A change after the save.
    p.get_by_role("button", name="Bypass overdrive").click()
    expect(p.locator("aside.changes h2")).to_have_text("1 unsaved change")
    expect(p.locator("aside.changes .change")).to_have_text("board · overdriveon to bypassed")
    # Undo it, then undo again: past the save, to the knob, which the file has.
    undo = p.locator("button.undo")
    expect(undo).to_have_text("Undo · bypass overdrive")
    undo.click()
    expect(p.locator("aside.changes h2")).to_have_text("No changes seen")
    expect(undo).to_have_text("Undo · overdrive · Drive")
    undo.click()
    until(lambda: abs(save_served.rig.info["overdrive"]["Drive"]["value"] - 0.62) < 1e-9)
    expect(p.locator("aside.changes h2")).to_have_text("1 unsaved change")
    expect(p.locator("aside.changes .change")).to_have_text("board · overdrive · Drive0.61 to 0.62")
    assert abs(drive_of(rig_file(save_served)) - 0.61) < 1e-6, "the save is not undone"
    page.close()


def test_save_as_writes_a_new_rig_which_becomes_the_running_one(browser, save_served):
    page = save_rack(browser, save_served)
    p = page.page
    turn_drive_down(p, 5)
    p.get_by_role("button", name="Save as…").click()
    name = p.get_by_label("New rig name")
    name.fill("board-two")
    expect(p.locator(".save-check")).to_have_text("Writes config/rigs/board-two.yaml, which becomes the running rig.")
    p.get_by_role("button", name="Save as board-two").click()
    result = p.locator(".save-result.ok")
    expect(result).to_contain_text("Saved as board-two")
    expect(result).to_contain_text("Wrote config/rigs/board-two.yaml, a new rig, which is now the running rig.")
    expect(result.locator(".commit")).to_have_text(
        f"Not committed: a new file git does not track yet, on commit {save_served.save.commit[:7]}. "
        "sushi-rig does not commit: that is yours to do.")
    assert abs(drive_of(rig_file(save_served, "board-two")) - 0.57) < 1e-6
    assert drive_of(rig_file(save_served)) is None, "the source is untouched"
    assert save_served.save.state["rig"] == "config/rigs/board-two.yaml"
    assert save_served.save.compiled == ["board-two"]
    # The page follows: the rig's name, the picker, the next plain save.
    expect(p.locator("h1.rig-name")).to_have_text("board-two")
    expect(p.locator(".rig", has_text="board-two")).to_contain_text("up")
    expect(p.get_by_label("New rig name")).to_have_count(0)
    turn_drive_down(p, 1)
    p.get_by_role("button", name="Save to board-two").click()
    expect(p.locator(".save-result.ok")).to_contain_text("Wrote config/rigs/board-two.yaml in place")
    page.close()


def test_a_name_the_rule_refuses_is_said_before_sending_and_a_taken_one_by_the_agent(browser, save_served):
    hall = save_served.save.root / "config" / "rigs" / "hall.yaml"
    hall.write_text("# another rig\n")
    page = save_rack(browser, save_served)
    p = page.page
    p.get_by_role("button", name="Save as…").click()
    name = p.get_by_label("New rig name")
    name.fill("Board Two")
    expect(p.locator(".save-check.bad")).to_contain_text("lowercase letters, digits, '-' and '_'")
    expect(p.locator(".save-as button[type=submit]")).to_be_disabled()
    name.fill("hall")
    p.get_by_role("button", name="Save as hall").click()
    refused = p.locator(".save-result.refused")
    expect(refused).to_contain_text("Not saved as hall")
    expect(refused).to_contain_text("a rig called hall already exists (config/rigs/hall.yaml)")
    assert hall.read_text() == "# another rig\n"
    assert save_served.save.state["rig"] == "config/rigs/electric_board.yaml"
    expect(p.get_by_label("New rig name")).to_have_value("hall"), "the form stays, to choose another"
    page.close()


def test_in_panel_mode_save_is_not_offered_and_says_how_to_save(browser, save_served):
    save_served.save.state["mode"] = "panel"
    page = save_rack(browser, save_served)
    p = page.page
    turn_drive_down(p)
    expect(p.get_by_role("button", name="Save to electric_board")).to_be_disabled()
    expect(p.get_by_role("button", name="Save as…")).to_be_disabled()
    expect(p.locator(".save-note")).to_contain_text("The rig runs with its panel")
    expect(p.locator(".save-note")).to_contain_text("save with the panel's SAVE button")
    assert drive_of(rig_file(save_served)) is None
    page.close()


def test_a_refusal_from_the_agent_is_shown_with_its_reason(browser, save_served):
    """What the page cannot know ahead: a chain changed live (a plugin
    removed), which the agent refuses with why."""
    save_served.rig.chains["board"].remove("delay")
    page = save_rack(browser, save_served)
    p = page.page
    p.get_by_role("button", name="Save to electric_board").click()
    refused = p.locator(".save-result.refused")
    expect(refused).to_contain_text("Not saved")
    expect(refused).to_contain_text("the running rig lacks delay (on board), which electric_board has")
    assert drive_of(rig_file(save_served)) is None
    page.close()


def test_the_stage_has_no_save(browser, save_served):
    page = Watched(browser, save_served, "/", width=390, height=844)
    page.page.wait_for_selector(".card")
    expect(page.page.locator(".save")).to_have_count(0)
    expect(page.page.get_by_role("button", name=re.compile("^Save"))).to_have_count(0)
    page.close()


# --------------------------------------------------------------------------- #
# Recording (S7, S8): the record bar on the stage, the takes in the rack
# --------------------------------------------------------------------------- #

def health_up(busy=None):
    return {"ok": True, "body": {"rig": {"running": True, "config_name": "electric_board"}, "agent": {"busy": busy}}}


STEMS = [{"file": "board.wav"}, {"file": "board-dry.wav"}, {"file": "master.wav"}]


def test_seconds_are_said_as_a_clock(blank):
    assert rigjs(blank, "return [0, 7.9, 107, 3723, -1, null].map(R.clockText);") == \
        ["0:00", "0:07", "1:47", "1:02:03", None, None]
    # What the recorder said, plus how long ago by this page's clock.
    assert rigjs(blank, "return R.elapsedNow({elapsed: 10, at: 1000}, 3500);") == 12.5


def test_the_record_bar_follows_what_the_recorder_says(blank):
    views = rigjs(blank, """
      const up = arg.up, recording = {ok: true, body: {state: "recording", name: "riff", elapsed: 3, stems: arg.stems,
        said: "recording riff"}};
      const idle = {ok: true, body: {state: "idle", stems: arg.stems, names: ["riff"]}};
      return [
        R.recordBar({ok: false, reason: "x"}, null, "", null),
        R.recordBar({ok: true, body: {rig: {running: false}, agent: {}}}, null, "", null),
        R.recordBar(arg.busy, idle, "", null),
        R.recordBar(up, {ok: true, body: {state: "unavailable", reason: "the rig runs with its panel (test)"}}, "", null),
        R.recordBar(up, idle, "", null),
        R.recordBar(up, idle, "Riff Idea", null),
        R.recordBar(up, idle, "riff", null),
        R.recordBar(up, recording, "", null),
        R.recordBar(up, recording, "", "stop"),
        R.recordBar(up, idle, "", "start"),
      ];
    """, {"up": health_up(), "busy": health_up("restart"), "stems": STEMS})
    off = [v for v in views[:4]]
    assert [v["reason"] for v in off] == [
        "The agent is not answering.", "No rig is running to record.", "restart is running: recording waits for it.",
        "the rig runs with its panel (test)"]
    assert all(v["record"]["disabled"] and v["stop"]["disabled"] for v in off), "nothing to press"
    idle = views[4]
    assert (idle["state"], idle["record"], idle["stop"]["disabled"]) == ("idle", {"label": "Record", "disabled": False}, True)
    assert idle["note"] == "No name: the take is named from the time it starts."
    assert idle["stems"] == "3 stems: board.wav, board-dry.wav, master.wav"
    assert views[5]["record"]["disabled"] and views[5]["bad"] and "lowercase letters" in views[5]["note"]
    assert views[6]["note"] == "Will replace the earlier riff, which moves to _archive/riff/."
    rec = views[7]
    assert (rec["state"], rec["title"], rec["name"]) == ("recording", "Recording", "riff")
    assert rec["record"]["disabled"] and not rec["stop"]["disabled"], "two buttons, each what it says"
    assert views[8]["stop"] == {"label": "Stopping…", "disabled": True}
    assert views[9]["record"] == {"label": "Starting…", "disabled": True}


def test_a_takes_verdict_is_take_checks_and_the_one_recording_is_said_as_that(blank):
    rows = rigjs(blank, """
      return R.takeRows({ok: true, body: {takes_dir: "/home/p/sushi-rig-takes", more: 2, archived: 1, exists: true, takes: [
        {name: "riff", take: true, complete: true, seconds: 192.4, stems: ["a.wav", "b.wav"], lost_ms: 0, problems: []},
        {name: "late", take: true, complete: true, seconds: 125, stems: ["a.wav"], lost_ms: 5.3,
         problems: ["the recorder lost audio 1 time(s), 5.3 ms in all — the stems are still aligned"]},
        {name: "cut", take: true, complete: false, seconds: 48, stems: ["a.wav"], lost_ms: 0,
         problems: ["this take was never finished — the rig stopped mid-take", "x"]},
        {name: "now", take: true, complete: false, seconds: 1, stems: [], problems: ["this take was never finished"]},
        {name: "odd", take: false, problems: ["/t/odd is not a take: cannot read take.yaml"]},
      ]}}, "now");
    """)
    assert rows["dir"] == "/home/p/sushi-rig-takes" and (rows["more"], rows["archived"]) == (2, 1)
    by = {r["name"]: r for r in rows["rows"]}
    assert (by["riff"]["len"], by["riff"]["note"], by["riff"]["tone"]) == ("3:12", "Whole · 2 stems · nothing lost", "ok")
    assert by["late"]["note"] == ("1 problem · 5.3 ms of audio lost: the recorder lost audio 1 time(s), 5.3 ms in all "
                                  "— the stems are still aligned")
    assert by["cut"]["note"] == "2 problems: this take was never finished — the rig stopped mid-take"
    assert by["cut"]["tone"] == "warn", "a cut-off take says so rather than looking fine"
    assert (by["now"]["note"], by["now"]["tone"]) == ("Recording now", "live")
    assert by["odd"]["note"] == "Not a take: /t/odd is not a take: cannot read take.yaml"


def test_what_a_start_or_stop_said_is_shown_as_it_was_said(blank):
    said = rigjs(blank, """
      return [
        R.recordOutcome("start", {ok: false, reason: "already recording riff: stop it first", code: "recording", body: {}}),
        R.recordOutcome("start", {ok: true, body: {name: "riff", replaced: true, said: "recording riff — the earlier riff is in _archive/riff/"}}),
        R.recordOutcome("stop", {ok: true, body: {name: "riff", seconds: 64, stems: [{}, {}], problems: []}}),
        R.recordOutcome("stop", {ok: false, reason: "not recording", code: "not_recording", body: {}}),
        R.recordCall("  riff "), R.recordCall(""), R.STOP_CALL,
        R.describeEntry({op: "record", args: {action: "start", name: "riff"}}),
        R.nextUndo({first: 0, log: [{op: "parameter", args: {}}, {op: "record", args: {action: "start"}}, {op: "record", args: {action: "stop"}}]}).index,
      ];
    """)
    assert said[0] == {"ok": False, "title": "Not recording", "reason": "already recording riff: stop it first", "code": "recording"}
    assert said[1] == {"ok": True, "title": "Recording riff", "text": "recording riff — the earlier riff is in _archive/riff/"}
    assert said[2] == {"ok": True, "title": "Recorded riff", "text": "1:04 · Whole · 2 stems · nothing lost"}
    assert said[3]["title"] == "Not stopped"
    assert said[4:7] == [{"path": "/record/start", "json": {"name": "riff"}}, {"path": "/record/start", "json": {}},
                         {"path": "/record/stop", "json": {}}]
    assert said[7] == "record riff" and said[8] == 0, "undo passes over a take"


@pytest.fixture
def record_served():
    with Served(record=True) as served:
        yield served


def stage_page(browser, served, clock=False):
    page = Watched.__new__(Watched)
    page.asked, page.console, page.errors = [], [], []
    page.page = browser.new_page(viewport={"width": 390, "height": 844})
    page.page.on("pageerror", lambda error: page.errors.append(str(error)))
    if clock:
        page.page.clock.install()
    page.page.goto(served.url + "/#stage")
    return page


def test_record_and_stop_are_one_tap_each_and_work_locked(browser, record_served):
    page = stage_page(browser, record_served)
    p = page.page
    expect(p.locator("button.lock")).to_have_text("Locked")
    bar = p.locator(".record-bar")
    expect(bar.locator("h2")).to_have_text("Record")
    expect(bar.locator(".record-stems")).to_have_text(
        "5 stems: board.wav, board-dry.wav, vocal.wav, vocal-dry.wav, master.wav")
    expect(p.get_by_role("button", name="Stop", exact=True)).to_be_disabled()
    p.get_by_label("Take name").fill("riff-idea")
    p.get_by_role("button", name="Record", exact=True).click()
    expect(bar).to_have_class(re.compile(r"\brecording\b"))
    expect(bar.locator("h2")).to_have_text("Recording")
    expect(bar.locator(".take-now .v")).to_have_text("riff-idea")
    expect(p.get_by_role("button", name="Record", exact=True)).to_be_disabled()
    # No confirm: one tap stops it.
    p.get_by_role("button", name="Stop", exact=True).click()
    result = bar.locator(".record-result")
    expect(result).to_contain_text("Recorded riff-idea")
    expect(result).to_contain_text("Whole · 5 stems · nothing lost")
    expect(bar.locator("h2")).to_have_text("Record")
    take = record_served.recorder.takes / "riff-idea"
    assert __import__("sushi_rig.take", fromlist=["check_take"]).check_take(
        __import__("sushi_rig.take", fromlist=["load_take"]).load_take(take)) == []
    page.close()


def test_the_elapsed_time_ticks_and_follows_the_recorders_clock(browser, record_served):
    page = stage_page(browser, record_served, clock=True)
    p = page.page
    clock = record_served.recorder.clock
    p.get_by_role("button", name="Record", exact=True).click()
    elapsed = p.locator(".record-elapsed")
    expect(elapsed).to_have_text("0:00")
    for _ in range(5):  # the recorder's clock and the page's, a second at a time
        clock.advance(1)
        p.clock.run_for(1000)
        p.wait_for_timeout(120)
    expect(elapsed).to_have_text("0:05")
    # The recorder's clock is the truth: a poll brings the page to it.
    clock.advance(60)
    p.clock.run_for(1000)
    p.wait_for_timeout(150)
    expect(elapsed).to_have_text("1:05")
    # With no answer coming (the recorder slow, the network gone), the page
    # counts on from the last one by its own clock.
    held = []
    p.route("**/record", lambda route: held.append(route))
    for _ in range(3):
        p.clock.run_for(1000)
        p.wait_for_timeout(50)
    expect(elapsed).to_have_text("1:08")
    assert held, "the polls were held"
    page.close()


def test_a_page_opened_mid_take_shows_it_running(browser, record_served):
    first = stage_page(browser, record_served)
    first.page.get_by_role("button", name="Record", exact=True).click()
    expect(first.page.locator(".record-bar h2")).to_have_text("Recording")
    second = stage_page(browser, record_served)
    expect(second.page.locator(".record-bar .take-now .v")).to_have_text(re.compile(r"^take-\d{8}-\d{6}$"))
    expect(second.page.get_by_role("button", name="Record", exact=True)).to_be_disabled()
    second.page.get_by_role("button", name="Stop", exact=True).click()
    expect(first.page.locator(".record-bar h2")).to_have_text("Record", timeout=5000)
    first.close()
    second.close()


def test_a_name_the_rule_refuses_is_said_before_sending_and_a_reused_one_warns(browser, record_served):
    page = stage_page(browser, record_served)
    p = page.page
    name = p.get_by_label("Take name")
    name.fill("Riff Idea")
    expect(p.locator("#take-note")).to_have_class(re.compile(r"\bbad\b"))
    expect(p.locator("#take-note")).to_contain_text("lowercase letters, digits")
    expect(p.get_by_role("button", name="Record", exact=True)).to_be_disabled()
    name.fill("riff")
    p.get_by_role("button", name="Record", exact=True).click()
    p.get_by_role("button", name="Stop", exact=True).click()
    expect(p.locator(".record-result")).to_contain_text("Recorded riff")
    p.get_by_label("Take name").fill("riff")
    expect(p.locator("#take-note")).to_have_text("Will replace the earlier riff, which moves to _archive/riff/.")
    p.get_by_role("button", name="Record", exact=True).click()
    expect(p.locator(".record-result")).to_contain_text("the earlier riff is in _archive/riff/")
    p.get_by_role("button", name="Stop", exact=True).click()
    assert (record_served.recorder.takes / "_archive" / "riff").is_dir()
    page.close()


@pytest.mark.parametrize("change, said", [
    (lambda s: s.save.state.update(mode="panel"), "whose record bar owns recording"),
    (lambda s: s.save.state.update(rig=None), "is a v1 config, not a rig of channels"),
    (lambda s: setattr(s.recorder.link, "down", "no recorder is listening (test)"), "the recorder is not answering"),
])
def test_why_recording_is_off_is_said_on_the_bar(browser, record_served, change, said):
    change(record_served)
    page = stage_page(browser, record_served)
    bar = page.page.locator(".record-bar")
    expect(bar.locator("h2")).to_have_text("Recording is off")
    expect(bar.locator(".record-note")).to_contain_text(said)
    expect(page.page.get_by_role("button", name="Record", exact=True)).to_be_disabled()
    expect(page.page.get_by_role("button", name="Stop", exact=True)).to_be_disabled()
    page.close()


def test_a_take_the_agent_refuses_shows_its_reason(browser, record_served):
    from ui_fake import Pushing

    page = stage_page(browser, record_served)
    p = page.page
    Pushing.refuse = RuntimeError("no such port (test)")
    p.get_by_role("button", name="Record", exact=True).click()
    refused = p.locator(".record-result.refused")
    expect(refused).to_contain_text("Not recording")
    expect(refused).to_contain_text("the capture engine did not start: no such port (test)")
    expect(p.locator(".record-bar h2")).to_have_text("Record")
    page.close()


def test_the_stage_with_the_record_bar_still_fits_a_phone(browser, record_served):
    page = stage_page(browser, record_served)
    p = page.page
    p.get_by_role("button", name="Record", exact=True).click()
    expect(p.locator(".record-bar h2")).to_have_text("Recording")
    width = p.evaluate("() => [document.documentElement.scrollWidth, innerWidth]")
    assert width[0] <= width[1]
    sizes = p.evaluate("""() => [...document.querySelectorAll('.record-bar button')]
      .map((e) => [e.textContent.trim(), e.getBoundingClientRect().width, e.getBoundingClientRect().height])""")
    assert all(w >= 44 and hgt >= 44 for _, w, hgt in sizes), sizes
    assert [s for s in sizes if s[0] in ("Record", "Stop")] and all(hgt >= 64 for t, _, hgt in sizes if t in ("Record", "Stop"))
    p.get_by_role("button", name="Stop", exact=True).click()
    page.close()


def test_the_rack_lists_the_takes_with_each_ones_check(browser, record_served):
    import json as json_module

    from sushi_rig.take import DRY, Stem, start_take

    stage = stage_page(browser, record_served)
    stage.page.get_by_label("Take name").fill("riff-idea")
    stage.page.get_by_role("button", name="Record", exact=True).click()
    stage.page.get_by_role("button", name="Stop", exact=True).click()
    expect(stage.page.locator(".record-result")).to_contain_text("Recorded riff-idea")
    config = json_module.loads(Path(record_served.save.state["config"]).read_text())
    start_take(record_served.recorder.takes, "cut-off", config, [Stem("board", DRY, "iface:capture_AUX0")],
               sample_rate=48000, subtype="float32", quantum=256, config_source="test")
    stage.page.get_by_label("Take name").fill("now-playing")
    stage.page.get_by_role("button", name="Record", exact=True).click()
    expect(stage.page.locator(".record-bar h2")).to_have_text("Recording")
    rack = Page(browser, record_served.url + "/#rack")
    p = rack.page
    takes = p.locator("section.takes")
    expect(takes.locator("h2")).to_have_text("TAKES")
    expect(takes.locator("code")).to_have_text(str(record_served.recorder.takes.resolve()))
    riff = takes.locator(".take", has_text="riff-idea")
    expect(riff.locator(".note")).to_have_text("Whole · 5 stems · nothing lost")
    expect(riff.locator(".len")).to_have_text("0:00")
    cut = takes.locator(".take", has_text="cut-off")
    expect(cut).to_have_class(re.compile(r"\bwarn\b"))
    expect(cut.locator(".note")).to_contain_text("this take was never finished")
    expect(takes.locator(".take", has_text="now-playing").locator(".note")).to_have_text("Recording now")
    stage.page.get_by_role("button", name="Stop", exact=True).click()
    expect(takes.locator(".take", has_text="now-playing").locator(".note")).to_have_text(
        "Whole · 5 stems · nothing lost")
    rack.close()
    stage.close()



def test_an_answer_older_than_one_shown_is_dropped(blank):
    said = rigjs(blank, """
      const order = new R.Latest();
      const first = order.issue(), second = order.issue();
      return [order.accept(second), order.accept(first), order.accept(order.issue())];
    """)
    assert said == [True, False, True]


def test_the_stage_never_asks_for_the_takes_and_the_rack_does(browser, record_served):
    page = Watched(browser, record_served, "/#stage", width=390, height=844)
    p = page.page
    p.get_by_role("button", name="Record", exact=True).click()
    p.get_by_role("button", name="Stop", exact=True).click()
    expect(p.locator(".record-result")).to_contain_text("Recorded")
    p.wait_for_timeout(1500)
    assert "/takes" not in page.asked, "the stage lists no takes, so it asks for none"
    p.evaluate("location.hash = '#rack'")
    expect(p.locator("section.takes .take")).to_have_count(1)
    assert "/takes" in page.asked
    page.close()


def test_an_older_takes_answer_never_replaces_a_newer_one(browser, record_served):
    """A periodic /takes still out when a take stops, and answering last:
    what the page shows is the newer list, not the older one."""
    import json as json_module

    page = Page.__new__(Page)
    page.page = browser.new_page()
    page.errors = []
    p = page.page
    p.on("pageerror", lambda error: page.errors.append(str(error)))
    held = []
    stale = {"ok": True, "takes_dir": "/stale", "exists": True, "more": 0, "archived": 0,
             "takes": [{"name": "stale-take", "take": True, "complete": True, "seconds": 1, "stems": [],
                        "lost_ms": 0, "problems": []}]}

    def takes(route):
        if not held:
            held.append(route)  # the first answer is held back, and sent last
        else:
            route.continue_()

    p.route("**/takes", takes)
    p.goto(record_served.url + "/#rack")
    expect(p.locator(".unit").first).to_be_visible()
    stage = stage_page(browser, record_served)
    stage.page.get_by_role("button", name="Record", exact=True).click()
    # The rack sees the take start (its /record poll) and asks for the list
    # again at once, while its first request is still held.
    expect(p.locator("section.takes .take .note")).to_have_text("Recording now", timeout=8000)
    stage.page.get_by_role("button", name="Stop", exact=True).click()
    expect(p.locator("section.takes .take .note")).to_have_text("Whole · 5 stems · nothing lost", timeout=8000)
    held[0].fulfill(status=200, content_type="application/json", body=json_module.dumps(stale))
    p.wait_for_timeout(500)
    expect(p.locator("section.takes .take .n")).to_have_text(re.compile(r"^take-"))
    expect(p.locator("section.takes")).not_to_contain_text("stale-take")
    stage.close()
    page.close()
