"""The visualiser (#176): a screensaver on the laptop that moves with the
guitar. After a spell with no pointer, touch or key on the page, while a rig
runs and the window is wide enough, butterchurn (vendored) fills the window,
hearing the guitar through the browser's own capture; any input puts the
page back and reaches nothing under it.

Three layers, as test_ui.py has them: the vendored files and the routes
with no browser; rig.js's decisions in a bare page; then the page driven in
Chromium against a real agent over `ui_fake.py`'s rig.

What stands in for what. The input is Chromium's fake capture device
(`--use-fake-device-for-media-stream`, a beep), not PipeWire and not the
interface; the browser's permission prompt is answered by
`--use-fake-ui-for-media-stream`, so the prompt itself is never seen. This
Chromium has no GPU, so WebGL is SwiftShader, which the visualiser refuses
(it would take the CPU Sushi needs): the tests that need it to run rename
the renderer (`GPU`, an init script), and the one that checks the refusal
does not. A tap is Playwright's touchscreen. Nothing here is the rig, a real
GPU, a real PipeWire or anybody's eyes on what is drawn.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import re
import time

import pytest

pytest.importorskip("aiohttp")
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from sushi_rig.paths import checkout_root  # noqa: E402
from sushi_rig.patchbay import GUITAR_ROLE  # noqa: E402
from sushi_rig.ui import UI_DIR  # noqa: E402

from test_ui import blank, browser, rigjs, served, ui_agent  # noqa: E402,F401
from ui_fake import UiRig  # noqa: E402

VENDOR = UI_DIR / "vendor" / "butterchurn-2.6.7"
SHA256 = {
    "butterchurn.min.js": "4e67421bc18d48fac4a6ff4e69e2778f770737fbdd4e323438da154085b4818d",
    "LICENSE": "e5c204814d7119c939d9f111691e0c2008607d09c2f4679691e6dc5a10f24b98",
}
LAZY = ("visualiser.js", "visualiser-presets.js", "visualiser-eq.js", "butterchurn.min.js")
MILKDROP = ["Embers", "Tide", "Bloom", "Tunnel", "Shutter", "Smoke", "Mirror", "Storm", "Eclipse"]
EQ = ["Bars", "Curve", "Aurora", "Mono bars"]
LOOKS = MILKDROP + EQ


# --------------------------------------------------------------------------- #
# The vendored library, and the routes (no browser)
# --------------------------------------------------------------------------- #

def test_butterchurn_is_vendored_at_its_pinned_release_with_its_licence():
    """Byte for byte the npm package's files (SOURCE.txt says which and from
    where), so it runs offline at a venue and a change to it is seen."""
    for name, digest in SHA256.items():
        assert hashlib.sha256((VENDOR / name).read_bytes()).hexdigest() == digest, name
    licence = (VENDOR / "LICENSE").read_text(encoding="utf-8")
    assert licence.startswith("MIT License") and "Jordan Berg" in licence
    source = (VENDOR / "SOURCE.txt").read_text(encoding="utf-8")
    for said in ("butterchurn 2.6.7", "butterchurn-2.6.7.tgz", *SHA256.values(), "ecma-proposal-math-extensions 0.0.2"):
        assert said in source, said
    assert "No preset pack" in source, "why there is no third-party preset pack is said"


def test_butterchurn_fetches_nothing():
    bundle = (VENDOR / "butterchurn.min.js").read_text(encoding="utf-8")
    assert not re.search(r"https?://", bundle) and "XMLHttpRequest" not in bundle and "fetch(" not in bundle


def test_the_page_loads_butterchurn_from_its_vendored_folder_only():
    text = (UI_DIR / "visualiser.js").read_text(encoding="utf-8")
    assert re.findall(r'"/ui/vendor/[^"]+"', text) == ['"/ui/vendor/butterchurn-2.6.7/butterchurn.min.js"']
    for name in ("index.html", "app.js", "screensaver.js"):
        assert "butterchurn.min.js" not in (UI_DIR / name).read_text(encoding="utf-8"), name


def test_the_presets_are_the_rigs_own():
    """No preset pack is shipped (SOURCE.txt): the presets file is the only
    one, and it names no MilkDrop preset author's work."""
    text = (UI_DIR / "visualiser-presets.js").read_text(encoding="utf-8")
    assert re.findall(r'name: "([^"]+)"', text) == MILKDROP
    assert not list(UI_DIR.rglob("*presets*.json")) and not list(UI_DIR.rglob("butterchurnPresets*"))


def test_the_guitar_role_the_page_listens_for_is_the_patchbays():
    text = (UI_DIR / "rig.js").read_text(encoding="utf-8")
    assert re.search(r'guitarRole: "([^"]+)"', text).group(1) == GUITAR_ROLE


def get(path):
    agent = ui_agent(live=UiRig())

    async def go():
        async with TestClient(TestServer(agent.app())) as client:
            response = await client.get(path, allow_redirects=False)
            return response.status, response.headers.get("Content-Type", ""), response.headers.get("Cache-Control"), await response.read()
    try:
        return asyncio.run(go())
    finally:
        agent._executor.shutdown(wait=True, cancel_futures=True)


@pytest.mark.parametrize("path", ["/ui/screensaver.js", "/ui/visualiser.js", "/ui/visualiser-presets.js",
                                  "/ui/visualiser-eq.js", "/ui/vendor/butterchurn-2.6.7/butterchurn.min.js"])
def test_the_visualisers_files_are_served_as_scripts_and_asked_again(path):
    status, kind, cache, body = get(path)
    assert status == 200 and "javascript" in kind and cache == "no-cache" and body


def test_the_vendored_licence_is_served_beside_the_library():
    status, _, _, body = get("/ui/vendor/butterchurn-2.6.7/LICENSE")
    assert status == 200 and body.startswith(b"MIT License")


@pytest.mark.parametrize("path", ["/ui/vendor/", "/ui/vendor/butterchurn-2.6.7/", "/ui/vendor/../__init__.py"])
def test_the_vendor_folder_is_no_listing(path):
    status, _, _, body = get(path)
    assert status in (403, 404) and b"butterchurn.min.js" not in body and b"import" not in body


def test_the_page_loads_the_screensaver_and_not_the_visualiser():
    status, _, _, body = get("/")
    assert status == 200 and b'src="/ui/screensaver.js"' in body
    assert not any(name.encode() in body for name in LAZY)


# --------------------------------------------------------------------------- #
# The decisions, pure (rig.js)
# --------------------------------------------------------------------------- #

def test_the_idle_time_is_whole_seconds_within_bounds(blank):
    out = rigjs(blank, """return [R.visualiserIdle(10), R.visualiserIdle('25'), R.visualiserIdle(1), R.visualiserIdle(99999),
      R.visualiserIdle(7.6), R.visualiserIdle(''), R.visualiserIdle('ten'), R.visualiserIdle(null), R.visualiserIdle(NaN)];""")
    assert out == [10, 25, 3, 600, 8, None, None, None, None]


def test_the_settings_kept_are_read_without_trusting_them(blank):
    out = rigjs(blank, """const r = (t) => R.visualiserSettings(t);
      return [r(null), r('not json'), r('[1]'), r('{"on": false, "idle": 30}'), r('{"on": "no", "idle": "x"}'),
              r('{"input": {"deviceId": "abc", "label": "iD14"}, "channel": 1}'), r('{"input": {"deviceId": ""}, "channel": 2}')];""")
    default = {"on": True, "idle": 10, "input": None, "channel": None, "look": "random"}
    assert out[:3] == [default] * 3
    assert out[3] == {**default, "on": False, "idle": 30}
    assert out[4] == default, "only false turns it off"
    assert out[5] == {**default, "input": {"deviceId": "abc", "label": "iD14"}, "channel": 1}
    assert out[6] == default


def test_the_gate_says_why_in_the_order_the_player_can_do_least_about(blank):
    out = rigjs(blank, """
      const ok = {on: true, reducedMotion: false, secure: true, media: true, webgl: true, renderer: 'Mesa Intel(R) Graphics (ADL GT2)',
                  permission: 'granted', width: 1280, running: true};
      const g = (over) => R.visualiserGate({...ok, ...over}).why;
      return {ok: R.visualiserGate(ok), off: g({on: false, reducedMotion: true}), reduced: g({reducedMotion: true, running: false}),
              phone: g({secure: false, width: 390}), nomedia: g({media: false}), nogl: g({webgl: false}),
              soft: g({renderer: 'ANGLE (Google, Vulkan 1.3.0 (SwiftShader Device (Subzero)), SwiftShader driver)'}),
              llvm: g({renderer: 'llvmpipe (LLVM 17.0.6, 256 bits)'}), denied: g({permission: 'denied'}),
              prompt: R.visualiserGate({...ok, permission: 'prompt'}).ok, narrow: g({width: 899, running: false}),
              wide: R.visualiserGate({...ok, width: 900}).ok, down: g({running: false}), unasked: R.visualiserGate({...ok, webgl: undefined, renderer: undefined, permission: null}).ok};""")
    assert out["ok"] == {"ok": True, "why": None}
    assert out["off"] == "Off."
    assert "reduced motion" in out["reduced"]
    assert "rig's own laptop" in out["phone"] and out["nomedia"] == out["phone"]
    assert "WebGL 2" in out["nogl"]
    assert "software" in out["soft"] and "SwiftShader" in out["soft"] and "software" in out["llvm"]
    assert "site settings" in out["denied"]
    assert out["prompt"] is True, "not asked yet is not refused: the browser asks when it starts"
    assert "900 px" in out["narrow"] and out["wide"] is True
    assert out["down"] == "Starts only while a rig is running."
    assert out["unasked"] is True, "what has not been asked yet does not stop it"


def test_what_stopped_it_is_said_before_anything_but_off(blank):
    out = rigjs(blank, """const ok = {on: true, secure: true, media: true, width: 1280, running: true};
      return [R.visualiserGate({...ok, stopped: 'Stopped: glitched.', running: false}).why,
              R.visualiserGate({...ok, on: false, stopped: 'Stopped: glitched.'}).why];""")
    assert out == ["Stopped: glitched.", "Off."]


def test_a_rise_in_xruns_while_it_listens_is_a_reason_to_stop(blank):
    out = rigjs(blank, """const t = R.visualiserTripped;
      return [t({count: 2, run: 'a'}, {count: 2, run: 'a'}), t({count: 2, run: 'a'}, {count: 3, run: 'a'}),
              t(null, {count: 3, run: 'a'}), t({count: 9, run: 'a'}, {count: 0, run: 'b'}), t({count: 1, run: 'a'}, null),
              t({count: 1, run: 'a'}, {count: null, run: 'a'})];""")
    assert out[0] is None and out[4] is None and out[5] is None
    assert out[1].startswith("Stopped: the rig glitched") and "from 2 to 3" in out[1] and "ticked again" in out[1]
    assert out[2] == out[3] == "rebase", "none yet, or another run: the count is taken again, not compared"
    assert "tap" not in out[1]
    tapped = rigjs(blank, "return R.visualiserTripped({count: 2, run: 'a'}, {count: 3, run: 'a'}, true);")
    assert ("Its tap on the rig's output was let go with it: the agent stops that now, unless another page "
            "is listening to it.") in tapped


def test_the_rigs_output_is_asked_for_unless_another_input_was_chosen_by_hand(blank):
    out = rigjs(blank, """const w = R.visualiserWantsTap;
      return [w({input: null}), w(null), w({input: {deviceId: 'x', label: 'Sushi rig output'}}),
              w({input: {deviceId: 'y', label: 'Audient iD14 Multichannel'}}),
              w({input: {deviceId: 'z', label: 'Renamed'}}, 'Renamed')];""")
    assert out == [True, True, True, False, True]


def test_the_taps_name_the_page_knows_is_the_agents():
    from sushi_rig import vistap

    text = (UI_DIR / "rig.js").read_text(encoding="utf-8")
    assert re.search(r'tapLabel: "([^"]+)"', text).group(1) == vistap.SOURCE_LABEL


def test_idle_and_frames_are_timed_as_said(blank):
    out = rigjs(blank, """return [R.idleDue(0, 9999, 10), R.idleDue(0, 10000, 10), R.idleDue(5000, 7999, 3),
      R.frameDue(null, 5), R.frameDue(0, 16.7), R.frameDue(0, 33.4), R.frameDue(0, 31.5), R.frameDue(0, 30, 60)];""")
    assert out == [False, True, False, True, False, True, True, True]


def test_the_drawing_buffer_is_the_window_or_capped(blank):
    out = rigjs(blank, """return [R.drawSize(1280, 800), R.drawSize(1920, 1080), R.drawSize(3840, 2160), R.drawSize(0, 0), R.drawSize(1000.4, 700.6)];""")
    assert out[0] == {"width": 1280, "height": 800}
    assert out[1]["width"] * out[1]["height"] <= 1280 * 800 and abs(out[1]["width"] / out[1]["height"] - 16 / 9) < 0.01
    assert out[2] == out[1] or out[2]["width"] * out[2]["height"] <= 1280 * 800
    assert out[3] == {"width": 1, "height": 1} and out[4] == {"width": 1000, "height": 701}


def test_the_guitar_is_found_from_the_machine_files_role(blank):
    """Ports as the real machine file names them (machines/sonny-dewfall-XPS-9320.yaml)."""
    out = rigjs(blank, """
      const role = (name, ports) => ({name, ports});
      const g = (roles) => R.guitarInput(roles);
      return [g([role('vocal-in', ['Audient iD14:capture_AUX1']), role('guitar-in', ['Audient iD14:capture_AUX0'])]),
              g([role('guitar-in', ['Audient iD14:capture_AUX1'])]), g([role('guitar-in', ['USB Audio:capture_FR'])]),
              g([role('guitar-in', ['Box:capture_AUX4'])]), g([role('guitar-in', ['Box:capture_MONO'])]),
              g([role('vocal-in', ['x:capture_AUX0'])]), g(null), g([role('guitar-in', ['no colon'])]), g([role('guitar-in', [])])];""")
    assert out[0] == {"device": "Audient iD14", "channel": 0, "port": "Audient iD14:capture_AUX0"}
    assert [o and o["channel"] for o in out[1:5]] == [1, 1, None, None], "only the first two reach the browser: else both"
    assert out[5:] == [None] * 4


def test_the_input_is_the_one_chosen_then_the_guitars_then_the_browsers(blank):
    out = rigjs(blank, """
      const devices = [{deviceId: 'default', label: 'Default'}, {deviceId: 'cam', label: 'BRIO Ultra HD Webcam Analog Stereo'},
        {deviceId: 'mon', label: 'Monitor of Audient iD14 Analog Surround 4.0'}, {deviceId: 'id14', label: 'Audient iD14 Multichannel'},
        {deviceId: 'id14x', label: 'Audient iD140 Other'}];
      const guitar = {device: 'Audient iD14', channel: 0};
      const settings = (input, channel = null) => ({input, channel});
      return [R.pickInput(devices, settings({deviceId: 'cam', label: 'old name'}, 1), guitar),
              R.pickInput(devices, settings({deviceId: 'gone', label: 'x'}), guitar),
              R.pickInput(devices, settings(null), {device: 'Audient iD140', channel: null}),
              R.pickInput(devices, settings(null), null),
              R.pickInput(devices.slice(1, 3), settings(null), {device: 'Nothing', channel: 0}),
              R.pickInput([], settings(null), guitar), R.pickInput([{deviceId: '', label: ''}], null, null)];""")
    assert out[0] == {"deviceId": "cam", "label": "BRIO Ultra HD Webcam Analog Stereo", "channel": 1, "how": "chosen"}
    assert out[1] == {"deviceId": "id14", "label": "Audient iD14 Multichannel", "channel": 0, "how": "guitar"}, \
        "never the monitor, never a longer name that only starts the same"
    assert out[2]["deviceId"] == "id14x"
    assert out[3] == {"deviceId": "default", "label": "Default", "channel": None, "how": "default"}
    assert out[4]["deviceId"] == "cam" and out[4]["how"] == "default"
    assert out[5] == out[6] == {"deviceId": None, "label": "", "channel": None, "how": "default"}


def test_the_rigs_output_comes_before_the_guitar_and_a_choice_made_here_before_both(blank):
    """#195: the visualiser tap's source, by the name /machine/roles gives
    it, both channels; then the guitar; then the browser's default."""
    out = rigjs(blank, """
      const tap = {deviceId: 'tap', label: 'Sushi rig output'};
      const devices = [{deviceId: 'default', label: 'Default'}, {deviceId: 'id14', label: 'Audient iD14 Multichannel'},
        {deviceId: 'cam', label: 'BRIO Ultra HD Webcam Analog Stereo'}, tap, {deviceId: 'x', label: 'Sushi rig output (old)'}];
      const guitar = {device: 'Audient iD14', channel: 0};
      const output = {node: 'sushi_rig_output', label: 'Sushi rig output'};
      const settings = (input, channel = null) => ({input, channel});
      return [R.pickInput(devices, settings(null), guitar, output),
              R.pickInput(devices, settings({deviceId: 'cam', label: 'c'}, 1), guitar, output),
              R.pickInput(devices.filter((d) => d.deviceId !== 'tap'), settings(null), guitar, output),
              R.pickInput(devices, settings(null), guitar),
              R.pickInput(devices.filter((d) => d.deviceId !== 'tap'), settings(null), null, output),
              R.pickInput(devices, settings(null), guitar, {label: ''})];""")
    assert out[0] == {"deviceId": "tap", "label": "Sushi rig output", "channel": None, "how": "output"}
    assert out[1]["deviceId"] == "cam" and out[1]["how"] == "chosen", "a choice made by hand still wins"
    assert out[2]["deviceId"] == "id14" and out[2]["how"] == "guitar", "a rig without its tap: the guitar"
    assert out[3]["how"] == "guitar", "with nothing said of the tap, as before"
    assert out[4]["deviceId"] == "default" and out[4]["how"] == "default", "never a name that only starts the same"
    assert out[5]["how"] == "guitar"


def test_an_input_is_said_with_its_channel_and_how_it_was_chosen(blank):
    out = rigjs(blank, """return [R.inputWords({deviceId: 'a', label: 'Audient iD14 Multichannel', channel: 0, how: 'guitar'}),
      R.inputWords({deviceId: 'default', label: '', channel: null, how: 'default'}), R.inputWords({deviceId: 'b', label: 'Cam', channel: 1, how: 'chosen'}),
      R.inputWords(null), R.softwareRenderer('Mesa Intel(R) Xe Graphics (TGL GT2)'), R.softwareRenderer(null),
      R.pointerMoved(null, {x: 1, y: 1}), R.pointerMoved({x: 1, y: 1}, {x: 1, y: 1}), R.pointerMoved({x: 1, y: 1}, {x: 1, y: 2})];""")
    assert out == ["Audient iD14 Multichannel, left channel (the machine file's guitar-in)",
                   "the browser's default input (the browser's choice)", "Cam, right channel", "no input yet",
                   False, False, True, False, True]
    said = rigjs(blank, "return R.inputWords({deviceId: 't', label: 'Sushi rig output', channel: null, how: 'output'});")
    assert said == "Sushi rig output (the rig's output: the master, after its limiter)"


def test_each_preset_has_the_four_shapes_and_waves_butterchurn_draws(blank):
    """At either end of its variation's bounds: the form butterchurn 2.6.7
    takes, as the first three presets had it, a shader a body or nothing."""
    out = blank.evaluate(r"""async () => { const P = await import('/ui/visualiser-presets.js');
      const at = (look, end) => Object.fromEntries(Object.entries(look.vary).map(([k, r]) => [k, r[end]]));
      return P.LOOKS.flatMap((look) => [0, 1].map((end) => { const p = look.make(at(look, end));
        return [look.name, p.shapes.length, p.waves.length, typeof p.frame_eqs_str, typeof p.pixel_eqs_str, typeof p.baseVals,
                [p.warp, p.comp].every((g) => g === '' || /^shader_body \{[\s\S]*ret = [\s\S]*\}$/.test(g))]; })); }""")
    assert out == [[name, 4, 4, "string", "string", "object", True] for name in MILKDROP for _ in range(2)]


# --------------------------------------------------------------------------- #
# The looks (#196): their order, their variations, the equaliser's mapping
# --------------------------------------------------------------------------- #

def test_the_looks_named_on_the_line_are_the_looks_the_files_draw(blank):
    """rig.js names them (the settings line offers them before either file is
    loaded); visualiser-presets.js and visualiser-eq.js draw them."""
    out = blank.evaluate("""async () => { const R = await import('/ui/rig.js');
      const P = await import('/ui/visualiser-presets.js'); const Q = await import('/ui/visualiser-eq.js');
      return {looks: R.VISUALISER_LOOKS, milkdrop: P.LOOKS.map((l) => l.name), eq: Object.keys(Q.EQ_LOOKS),
              eqMono: Object.entries(Q.EQ_LOOKS).filter(([, l]) => l.mono).map(([n]) => n)}; }""")
    assert [l["name"] for l in out["looks"]] == LOOKS
    assert out["milkdrop"] == [l["name"] for l in out["looks"] if l["kind"] == "milkdrop"] == MILKDROP
    assert out["eq"] == [l["name"] for l in out["looks"] if l["kind"] == "eq"] == EQ
    assert out["eqMono"] == ["Mono bars"]
    mono = [l["name"] for l in out["looks"] if l["mono"]]
    assert len([n for n in mono if n in MILKDROP]) == 6, "most of the new butterchurn looks are monochrome"


def test_the_look_choices_are_random_three_ways_then_each_look(blank):
    out = rigjs(blank, """return {choices: R.lookChoices().map((c) => c.value),
      kept: [R.lookChoice('Curve'), R.lookChoice('random-eq'), R.lookChoice('curve'), R.lookChoice(null), R.lookChoice(3)],
      pools: ['random', 'random-mono', 'random-eq', 'Aurora', 'nonsense'].map((c) => R.lookPool(c)),
      settings: [R.visualiserSettings('{"look": "Mono bars"}').look, R.visualiserSettings('{"look": "Glitter"}').look,
                 R.visualiserSettings('{"look": ["Bars"]}').look]};""")
    assert out["choices"] == ["random", "random-mono", "random-eq", *LOOKS]
    assert out["kept"] == ["Curve", "random-eq", "random", "random", "random"]
    assert out["pools"][0] == LOOKS
    assert out["pools"][1] == ["Tunnel", "Shutter", "Smoke", "Mirror", "Storm", "Eclipse", "Mono bars"]
    assert out["pools"][2] == EQ
    assert out["pools"][3] == ["Aurora"] and out["pools"][4] == LOOKS
    assert out["settings"] == ["Mono bars", "random", "random"]


def test_the_random_order_never_shows_a_look_twice_running_and_shows_them_all(blank):
    out = rigjs(blank, """const runs = {};
      for (const choice of ['random', 'random-mono', 'random-eq']) {
        const rng = R.seededRandom(11); const pool = R.lookPool(choice); let last = null; const seen = []; let repeats = 0;
        for (let i = 0; i < 3000; i++) { const next = R.nextLook(pool, last, rng); if (next === last) repeats++; seen.push(next); last = next; }
        runs[choice] = {repeats, every: pool.every((n) => seen.includes(n)), only: seen.every((n) => pool.includes(n)),
          least: Math.min(...pool.map((n) => seen.filter((x) => x === n).length))};
      }
      const a = R.seededRandom(5), b = R.seededRandom(5), c = R.seededRandom(6);
      const draw = (r) => Array.from({length: 5}, r);
      return {runs, same: JSON.stringify(draw(a)) === JSON.stringify(draw(b)), other: JSON.stringify(draw(R.seededRandom(5))) !== JSON.stringify(draw(c)),
              held: [R.nextLook(['Bars'], 'Bars', Math.random), R.nextLook([], null, Math.random)]};""")
    for choice, run in out["runs"].items():
        assert run["repeats"] == 0 and run["every"] and run["only"], (choice, run)
        assert run["least"] > 3000 / 13 / 2, (choice, run)
    assert out["same"] and out["other"], "a seed gives the same numbers, another seed others"
    assert out["held"] == ["Bars", None]


def test_each_look_is_shown_for_a_jittered_time_within_bounds(blank):
    out = rigjs(blank, """const rng = R.seededRandom(3); const s = Array.from({length: 2000}, () => R.lookSeconds(rng));
      return {min: Math.min(...s), max: Math.max(...s), distinct: new Set(s).size, bounds: [R.VISUALISER.lookMin, R.VISUALISER.lookMax],
              ends: [R.lookSeconds(() => 0), R.lookSeconds(() => 0.9999999)]};""")
    assert out["bounds"] == [30, 60]
    assert 30 <= out["min"] < 31 and 59 < out["max"] <= 60 and out["distinct"] > 250
    assert out["ends"] == [30, 60]


def test_each_look_is_varied_within_its_own_bounds(blank):
    """Every look's colour and motion, as it declares their bounds, drawn 300
    times: always within them, and not the same each time."""
    out = blank.evaluate("""async () => { const R = await import('/ui/rig.js');
      const P = await import('/ui/visualiser-presets.js'); const Q = await import('/ui/visualiser-eq.js');
      const looks = [...P.LOOKS.map((l) => [l.name, l.vary]), ...Object.entries(Q.EQ_LOOKS).map(([n, l]) => [n, l.vary])];
      const rng = R.seededRandom(9);
      return looks.map(([name, vary]) => { const drawn = Array.from({length: 300}, () => R.lookVariation(vary, rng));
        return [name, Object.keys(vary), Object.entries(vary).map(([k, [lo, hi]]) =>
          [k, drawn.every((v) => v[k] >= lo && v[k] <= hi), new Set(drawn.map((v) => v[k])).size > 100])]; }); }""")
    assert [name for name, _, _ in out] == LOOKS
    for name, keys, checks in out:
        assert all(ok and varied for _, ok, varied in checks), (name, checks)
        if name in MILKDROP:
            assert "speed" in keys and ({"hue"} & set(keys)), (name, keys)
    assert dict((n, k) for n, k, _ in out)["Bars"] == [], "the coloured equaliser looks keep their colour map"


def test_a_flash_on_a_loud_attack_comes_under_three_times_a_second(blank):
    """Every look's equations, as butterchurn runs them (each a `new
    Function` over `a`, kept from frame to frame), driven with the loudest
    possible attack on every frame at 30 frames a second for 20 s, and again
    with butterchurn's clock running at twice the wall's (its clock is its
    estimate of the frame rate): no second has three flashes, and a flash is
    gone within four frames. The looks that flash do flash."""
    out = blank.evaluate("""async () => { const P = await import('/ui/visualiser-presets.js');
      const run = (look, clock) => {
        const p = look.make(Object.fromEntries(Object.entries(look.vary).map(([k, r]) => [k, (r[0] + r[1]) / 2])));
        const init = new Function('a', `${p.init_eqs_str} return a;`), frame = new Function('a', `${p.frame_eqs_str} return a;`);
        const level = (v) => ({bass: v, mid: v, treb: v, bass_att: 1, mid_att: 1, treb_att: 1});
        let a = init({...p.baseVals, time: 0, frame: 0, fps: 30, aspectx: 1, aspecty: 1.5, ...level(1)});
        const onsets = []; let longest = 0, lit = 0;
        for (let i = 0; i < 600; i++) {
          Object.assign(a, {time: i / 30 * clock, frame: i, ...level(i % 2 ? 0.2 : 6)});
          for (let q = 1; q <= 32; q++) a[`q${q}`] = 0;
          a = frame(a);
          const f = a.q1 || 0;
          if (f >= 0.999) { onsets.push(i); lit = 0; }
          if (f > 0.1) { lit++; longest = Math.max(longest, lit); } else lit = 0;
        }
        const worst = Math.max(0, ...onsets.map((s) => onsets.filter((t) => t >= s && t < s + 30).length));
        return {onsets: onsets.length, worst, longest};
      };
      return P.LOOKS.map((look) => [look.name, run(look, 1), run(look, 2)]); }""")
    flashing = {"Tunnel", "Shutter", "Smoke", "Mirror", "Storm", "Eclipse"}
    for name, wall, fast in out:
        for r in (wall, fast):
            assert r["worst"] <= 2, (name, r)
            assert r["longest"] <= 4, (name, r)
        assert (wall["onsets"] > 0) == (name in flashing), (name, wall)
        if name in flashing:
            assert wall["onsets"] >= 20, ("as often as allowed: once every 16 frames or 0.55 s", name, wall)


def test_the_equaliser_axis_is_eight_octaves_of_the_same_width(blank):
    out = rigjs(blank, """const E = R.VISUALISER_EQ;
      return {low: E.low, high: E.high, x: [R.eqX(E.low), R.eqX(750), R.eqX(12000), R.eqX(20), R.eqX(20000), R.eqX(0), R.eqX(-5)],
        octave: [R.eqX(200) - R.eqX(100), R.eqX(3000) - R.eqX(1500)],
        which: [R.eqOctave(20), R.eqOctave(46.875), R.eqOctave(82.4), R.eqOctave(93.7), R.eqOctave(110), R.eqOctave(300),
                R.eqOctave(500), R.eqOctave(1000), R.eqOctave(2000), R.eqOctave(4000), R.eqOctave(8000), R.eqOctave(12000), R.eqOctave(16000)],
        colours: R.EQ_COLOURS.map((c) => c.name)};""")
    assert out["low"] == 46.875 and out["high"] == 12000
    assert out["x"] == [0, 0.5, 1, 0, 1, 0, 0]
    assert all(abs(d - 1 / 8) < 1e-12 for d in out["octave"]), "an octave is an eighth of the width wherever it is"
    assert out["which"] == [0, 0, 0, 0, 1, 2, 3, 4, 5, 6, 7, 7, 7], "the low E in the first, 12 kHz in the last"
    assert out["colours"] == ["deep red", "red", "orange", "yellow", "green", "cyan", "blue", "violet"]


@pytest.mark.parametrize("rate", [48000, 44100])
def test_the_equaliser_bands_share_out_every_bin_once(blank, rate):
    """Bars' bands (6 an octave: two to a third-octave): contiguous, equal in
    width across the display, each in its octave, the long FFT under 750 Hz
    and the short over it, and each FFT bin's power given out exactly once
    between the bands it touches."""
    out = rigjs(blank, """const b = R.eqBands(arg, 6), c = R.eqBands(arg, 24);
      const share = (fft) => { const w = {}; for (const band of b.filter((x) => x.fft === fft)) for (const [k, v] of band.bins) w[k] = (w[k] || 0) + v; return w; };
      return {n: b.length, n24: c.length, first: b[0], last: b[b.length - 1], contiguous: b.every((x, i) => i === 0 || Math.abs(x.lo - b[i - 1].hi) < 1e-9),
        widths: [...new Set(b.map((x) => (x.x1 - x.x0).toFixed(12)))], octaves: b.map((x) => x.octave),
        ffts: b.map((x) => x.fft), long: share('long'), short: share('short'), centres: b.slice(0, 3).map((x) => x.centre)};""", rate)
    assert out["n"] == 48 and out["n24"] == 192
    assert out["first"]["lo"] == 46.875 and abs(out["last"]["hi"] - 12000) < 1e-6 and out["contiguous"]
    assert out["widths"] == [f"{1 / 48:.12f}"]
    assert out["octaves"] == [i // 6 for i in range(48)]
    assert out["ffts"] == ["long"] * 24 + ["short"] * 24, "the split falls on an octave's edge, 750 Hz"
    for fft, size in (("long", 8192), ("short", 4096)):
        weights = {int(k): v for k, v in out[fft].items()}
        df = rate / size
        lo, hi = (46.875, 750) if fft == "long" else (750, 12000)
        inside = [k for k in weights if (k - 0.5) * df >= lo and (k + 0.5) * df <= hi]
        assert inside and all(abs(weights[k] - 1) < 1e-9 for k in inside), fft
        assert all(0 < w <= 1 + 1e-9 for w in weights.values())
    assert out["centres"][0] < 50, "the lowest band is narrower than a bin of the long FFT: it takes a share of one"


def test_a_bands_level_is_its_bins_power_summed(blank):
    out = rigjs(blank, """const band = {bins: [[3, 1], [4, 1], [5, 0.5]]};
      const db = new Float32Array(8).fill(-Infinity);
      const one = R.eqBandDb(Object.assign(db.slice(), {3: 0}), band);
      const two = R.eqBandDb(Object.assign(db.slice(), {3: -3.0103, 4: -3.0103}), band);
      const half = R.eqBandDb(Object.assign(db.slice(), {5: 0}), band);
      return [one, two, half, R.eqBandDb(db, band), R.eqBandDb(Object.assign(db.slice(), {6: 0}), band)];""")
    assert abs(out[0]) < 1e-6 and abs(out[1]) < 1e-3, "two bins at half the power make the power of one"
    assert abs(out[2] - (-3.0103)) < 1e-3, "a bin half in the band gives it half its power"
    assert out[3] == -200 and out[4] == -200, "silence, and a bin outside the band, are nothing"


def test_the_equaliser_reads_quickly_up_slowly_down_and_holds_its_peaks(blank):
    out = rigjs(blank, """const E = R.VISUALISER_EQ; const f = 1 / 30;
      const top = [R.eqTop(-50, -10, f), R.eqTop(-10, -40, 1), R.eqTop(-45, -90, 10), R.eqTop(NaN, -Infinity, f)];
      const level = [R.eqLevel(-10, -10), R.eqLevel(-64, -10), R.eqLevel(-37, -10), R.eqLevel(0, -10), R.eqLevel(-100, -10)];
      let v = 0; const up = R.eqSmooth(v, 1, f); let down = 1; const falls = [];
      for (let i = 0; i < 30; i++) { down = R.eqSmooth(down, 0, f); falls.push(down); }
      let p = {value: 0, held: 0}; const peak = [];
      p = R.eqPeak(p, 0.9, f); peak.push(p.value);
      for (let t = 0; t < 0.7; t += f) p = R.eqPeak(p, 0.1, f); peak.push(p.value);
      for (let t = 0; t < 0.5; t += f) p = R.eqPeak(p, 0.1, f); peak.push(p.value);
      for (let t = 0; t < 5; t += f) p = R.eqPeak(p, 0.1, f); peak.push(p.value);
      p = R.eqPeak(p, 0.95, f); peak.push(p.value);
      let g = 0; for (let i = 0; i < 3; i++) g = R.eqGlow(g, 1, f); const shortNote = g;
      for (let i = 0; i < 90; i++) g = R.eqGlow(g, 1, f); const held = g;
      let h = held; for (let i = 0; i < 30; i++) h = R.eqGlow(h, 0, f);
      return {top, level, up, afterAFrame: falls[0], afterASecond: falls[29], peak, shortNote, held, fadedASecond: h};""")
    assert out["top"][0] == -10, "the top rises to the loudest band at once"
    assert abs(out["top"][1] - (-13)) < 1e-9, "and falls back 3 dB a second"
    assert out["top"][2] == -40 and out["top"][3] == -40, "never under the floor: a quiet input reads over 30 dB at least"
    assert out["level"] == [1, 0, 0.5, 1, 0]
    assert out["up"] > 0.85, "a pick attack shows within a frame"
    assert 0.9 < out["afterAFrame"] < 0.96 and 0.1 < out["afterASecond"] < 0.2, "it falls back over about half a second"
    assert out["peak"][0] == 0.9 and out["peak"][1] == 0.9, "held"
    assert 0.1 < out["peak"][2] < 0.9 and out["peak"][3] == 0.1 and out["peak"][4] == 0.95, "then falls slowly, never under the bar"
    assert out["shortNote"] < 0.1 and out["held"] > 0.85, "the glow builds only on what stays"
    assert out["fadedASecond"] > 0.5, "and lingers"


def test_the_help_says_what_each_colour_is_in_the_order_of_the_map(blank):
    colours = rigjs(blank, "return R.EQ_COLOURS.map((c) => c.name);")
    guide = (checkout_root() / "docs" / "guide" / "playing-live.md").read_text(encoding="utf-8")
    section = guide[guide.index("### Reading the equaliser looks"):]
    section = section[:section.index("\n## ")] if "\n## " in section else section
    at = [section.find(f"**{name}**") for name in colours]
    assert all(i >= 0 for i in at) and at == sorted(at), (colours, at)
    for words in ("mud", "low strings", "pick attack", "fizz"):
        assert words in section, words
    reference = (checkout_root() / "docs" / "reference" / "web-ui.md").read_text(encoding="utf-8")
    assert "Look" in reference and "46.875" in reference


def test_a_blend_of_two_flashing_looks_never_flashes_three_times_a_second(blank):
    """During a blend butterchurn runs both presets' frame equations in one
    frame, with the same `time` and `frame`, and draws both (PR #201's
    review: with a gate of each preset's own, Shutter into Tunnel strummed
    every 0.3 s flashed four times in a second). Every pair of flashing looks,
    each runner keeping its own variables from frame to frame as butterchurn
    does, on strums at several rates and phases: no second holds three
    onsets, counting both."""
    out = blank.evaluate("""async () => { const P = await import('/ui/visualiser-presets.js');
      const mid = (look) => Object.fromEntries(Object.entries(look.vary).map(([k, r]) => [k, (r[0] + r[1]) / 2]));
      const runner = (look, g) => {
        const p = look.make(mid(look));
        const init = new Function('a', `${p.init_eqs_str} return a;`), frame = new Function('a', `${p.frame_eqs_str} return a;`);
        const after = init({...p.baseVals, ...g});
        const user = Object.keys(after).filter((k) => !(k in p.baseVals) && !(k in g));
        let map = Object.fromEntries(user.map((k) => [k, after[k]]));
        return (g2) => { const a = frame({...p.baseVals, ...map, ...g2}); map = Object.fromEntries(user.map((k) => [k, a[k]])); return a.q1 || 0; };
      };
      const flashing = ['Tunnel', 'Shutter', 'Smoke', 'Mirror', 'Storm', 'Eclipse'];
      const by = Object.fromEntries(P.LOOKS.map((l) => [l.name, l]));
      const lv = (v) => ({bass: v, mid: v, treb: v, bass_att: 1, mid_att: 1, treb_att: 1});
      const strum = (i, period) => ((i / 30) % period < 2 / 30 ? 3 : 0.6);
      let worst = 0, total = 0; const where = [];
      for (const from of flashing) for (const to of flashing) { if (from === to) continue;
        for (const [period, offset] of [[0.3, 0], [0.3, 4], [0.28, 5], [0.45, 2], [2 / 30, 3]]) {
          P.resetFlashGate();
          const A = runner(by[from], {time: 0, frame: 0, fps: 30, ...lv(1)}); let B = null; const onsets = [];
          for (let i = 0; i < 180; i++) {
            const g = {time: i / 30, frame: i, fps: 30, aspectx: 1, aspecty: 1.5, ...lv(strum(i + offset, period))};
            const qa = A(g); if (i === 60) B = runner(by[to], g); const qb = B ? B(g) : 0;
            if (i >= 60) { if (qa >= 0.999) onsets.push(i); if (qb >= 0.999) onsets.push(i); }
          }
          const w = Math.max(0, ...onsets.map((s) => onsets.filter((t) => t >= s && t < s + 30).length));
          total += onsets.length; if (w > worst) { worst = w; where.push([from, to, period, offset, onsets.slice(0, 6)]); }
        } }
      return {worst, total, where: where.slice(-1)}; }""")
    assert out["worst"] <= 2, out["where"]
    assert out["total"] > 100, "the looks do flash as often as allowed"


# Choked staccato at `hz` with a choke of `decay` dB a second, its power
# averaged over about 60 ms as the short FFT sees it (PR #201's review's
# model, scratchpad rv201/swing.mjs): the level's swing between the notes,
# once settled.
SWING = """const swing = (hz, decay) => { let level = 0, top = -50; const levels = [];
  for (let i = 0; i < 300; i++) { const since = (i / 30) % (1 / hz); let p = 0;
    for (let k = 0; k < 12; k++) { const s = since - k * 0.005; const db = s < 0 ? -90 : Math.max(-90, -20 - decay * s); p += 10 ** (db / 10); }
    const db = 10 * Math.log10(p / 12); top = R.eqTop(top, db, 1 / 30); level = R.eqSmooth(level, R.eqLevel(db, top), 1 / 30); levels.push(level); }
  const tail = levels.slice(150); return Math.max(...tail) - Math.min(...tail); };"""


def test_choked_staccato_does_not_swing_the_equaliser_bright_to_dark(blank):
    """At 3 to 5 notes a second the bars swung by about half the display,
    bright to near black, with a release of 0.2 s (PR #201's review; 0.55,
    0.44, 0.41 and 0.25 of it in this model): with 0.5 s, half that (0.29,
    0.22, 0.20, 0.12). The equaliser looks are not in the flash limit (they
    never flash the screen): this is what keeps them under it."""
    out = rigjs(blank, SWING + "return [swing(3, 200), swing(4, 300), swing(5, 400), swing(3.5, 120)];")
    assert out[0] < 0.3 and max(out[1:]) < 0.23, out


def test_hum_in_silence_stays_dark_and_the_equaliser_draws_at_half_size(blank):
    """Mains hum at -60 dBFS peak, harmonics and all, read -74 dB in the
    loudest band; the foot never goes under -70 dB, so it is dark however
    long the input has been quiet, and hum ten times as strong shows. The
    canvas is half the buffer; a look slow to draw drops to 15 fps."""
    out = rigjs(blank, """const E = R.VISUALISER_EQ;
      return {bottom: E.bottom, dark: [-40, -30, -10, 0].map((top) => R.eqLevel(-74, top)),
        shows: R.eqLevel(-64, R.eqTop(E.floor, -64, 1 / 30)), quiet: R.eqLevel(-50, -40),
        size: [R.eqDrawSize(1280, 800), R.eqDrawSize(1, 1)],
        fps: [R.eqFps([]), R.eqFps(Array(9).fill(50)), R.eqFps(Array(31).fill(4)), R.eqFps([...Array(16).fill(12), ...Array(15).fill(2)]),
              R.eqFps([...Array(15).fill(12), ...Array(16).fill(2)])]};""")
    assert out["bottom"] == -70 and out["dark"] == [0, 0, 0, 0]
    assert out["shows"] > 0.1 and 0.6 < out["quiet"] < 0.7
    assert out["size"] == [{"width": 640, "height": 400}, {"width": 1, "height": 1}]
    assert out["fps"] == [30, 30, 30, 15, 30]


def test_real_hum_through_the_equaliser_draws_nothing(blank):
    """The same through the page's own drawing: 50 and 60 Hz hum with its
    second and third harmonics, at -60 dBFS peak, into each equaliser look's
    analysers (an AudioContext of the page's, no capture): nothing lit. At
    -40 dBFS peak it is."""
    out = blank.evaluate("""async () => { const Q = await import('/ui/visualiser-eq.js');
      const run = async (freq, peak, name) => {
        const ctx = new AudioContext(); await ctx.resume(); const sum = ctx.createGain();
        for (const [k, a] of [[1, 1], [2, 0.5], [3, 0.3]]) { const o = ctx.createOscillator(); o.frequency.value = freq * k;
          const g = ctx.createGain(); g.gain.value = peak * a; o.connect(g); g.connect(sum); o.start(); }
        const c = document.createElement('canvas'); c.width = 320; c.height = 200;
        const eq = Q.createEq(c, ctx); eq.listen(sum); eq.show(name, {});
        await new Promise((r) => setTimeout(r, 500));
        for (let i = 0; i < 40; i++) eq.render(i * 33.3);
        const loudest = eq.stats().loudest; await ctx.close(); return loudest; };
      const out = {};
      for (const name of Object.keys(Q.EQ_LOOKS)) out[name] = [await run(50, 0.001, name), await run(60, 0.001, name), await run(50, 0.01, name)];
      return out; }""")
    for name, (fifty, sixty, loud) in out.items():
        assert fifty == 0 and sixty == 0, (name, fifty, sixty)
        assert loud > 0.2, (name, loud)


def test_the_guide_says_each_colour_as_rig_js_does_and_each_is_right(blank):
    """Each colour's line in the guide is rig.js's words for it; and where
    the strings' notes fall is checked against the map: the high strings'
    open notes (G3, B3, E4) are orange, not yellow (PR #201's review)."""
    out = rigjs(blank, """return {colours: R.EQ_COLOURS.map((c) => [c.name, c.range]),
      notes: {E2: R.eqOctave(82.4), A2: R.eqOctave(110), D3: R.eqOctave(146.8), G3: R.eqOctave(196), B3: R.eqOctave(246.9),
              E4: R.eqOctave(329.6), E5: R.eqOctave(659.3), E6: R.eqOctave(1318.5)}};""")
    guide = (checkout_root() / "docs" / "guide" / "playing-live.md").read_text(encoding="utf-8")
    for name, words in out["colours"]:
        assert f"- **{name}**: {words}." in guide, name
    names = [n for n, _ in out["colours"]]
    by = dict(out["colours"])
    n = out["notes"]
    assert names[n["E2"]] == "deep red" and "low E" in by["deep red"]
    assert names[n["A2"]] == names[n["D3"]] == "red" and "low strings" in by["red"]
    assert names[n["G3"]] == names[n["B3"]] == names[n["E4"]] == "orange" and "high strings' open notes" in by["orange"]
    assert names[n["E5"]] == "yellow" and "high up the neck" in by["yellow"]


# --------------------------------------------------------------------------- #
# The page
# --------------------------------------------------------------------------- #

# Renames the WebGL renderer, so SwiftShader passes for a GPU (module docstring).
GPU = """(() => { const real = WebGL2RenderingContext.prototype.getParameter;
  WebGL2RenderingContext.prototype.getParameter = function (p) {
    return p === 0x9246 ? 'Test GPU (renamed by the test)' : real.call(this, p); }; })();"""


@pytest.fixture(scope="module")
def media_browser(browser):
    """A Chromium with a fake input and the permission prompt answered,
    from test_ui.py's Playwright (one a thread)."""
    media = browser.browser_type.launch(args=["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream"])
    yield media
    media.close()


class VPage:
    """The rig's page, its settings for the visualiser set before it loads.

    `clock`: Playwright's fake clock, installed before the page loads, so the
    idle timer runs only as a test moves it (`advance`), never by the wall
    clock. Its timers, `performance.now` and animation frames are fake; the
    network, the audio and promises are not, so what follows a timer is
    waited for by condition (`until`)."""

    def __init__(self, browser, served, width=1200, height=800, gpu=True, settings=None, reduced=None, touch=False,
                 url="/", clock=False, init=()):
        options = {"viewport": {"width": width, "height": height}, "has_touch": touch}
        if reduced:
            options["reduced_motion"] = reduced
        self.context = browser.new_context(**options)
        self.clock = clock
        if clock:
            self.context.clock.install()
        if gpu:
            self.context.add_init_script(GPU)
        for script in init:
            self.context.add_init_script(script)
        kept = {"idle": 3, **(settings or {})}
        self.context.add_init_script(f"if (!sessionStorage.getItem('set')) {{ localStorage.setItem('sushi-rig.visualiser', {json.dumps(json.dumps(kept))}); sessionStorage.setItem('set', '1'); }}")
        self.page = self.context.new_page()
        self.errors = []
        self.requested = []
        self.console = []
        self.page.on("pageerror", lambda error: self.errors.append(str(error)))
        self.page.on("console", lambda message: self.console.append((message.type, message.text)))
        self.page.on("request", lambda request: self.requested.append(request.url))
        self.page.goto(served.url + url)
        self.until("() => window.sushiRigVisualiser && window.sushiRigVisualiser().running")

    def state(self):
        return self.page.evaluate("sushiRigVisualiser()")

    def loaded(self):
        """The file names the page has asked for (requests seen from here:
        the fake clock leaves the page's resource timing empty)."""
        return [url.split("?")[0].split("/")[-1] for url in self.requested]

    def advance(self, ms):
        self.page.clock.run_for(ms)

    def until(self, condition, timeout=15000):
        """Until `condition` (a function's source) holds in the page: polled
        from here, so it works with the fake clock, which moves on 50 ms
        while it waits. A generous ceiling: what decides is the condition."""
        deadline = time.monotonic() + timeout / 1000
        while True:
            if self.page.evaluate(f"({condition})()"):
                return
            if time.monotonic() > deadline:
                raise AssertionError(f"not within {timeout} ms: {condition}; {self.state()}")
            if self.clock:
                self.advance(50)
            self.page.wait_for_timeout(20)

    def shown(self):
        """Until it is up, hearing, and has drawn three frames more than when
        asked (a session's count goes on across showings)."""
        before = (self.state()["session"] or {}).get("frames", 0)
        self.until(f"""() => {{ const v = sushiRigVisualiser(); const s = v.session;
          return v.phase === 'shown' && s && s.listening && s.drawing && s.frames > {before + 2}; }}""")
        return self.state()

    def off(self):
        self.until("() => sushiRigVisualiser().phase === 'off'")
        return self.state()

    def refused(self, after=0):
        """Until the idle timer has found the page left and the gate said no
        (more than `after` times): then it was due, and did not start."""
        self.until(f"() => sushiRigVisualiser().attempts > {after} && sushiRigVisualiser().phase === 'off'")
        return self.state()

    def close(self):
        self.context.close()
        assert self.errors == []


@pytest.fixture
def opened(media_browser, served):
    pages = []

    def make(**kwargs):
        page = VPage(media_browser, served, **kwargs)
        pages.append(page)
        return page
    yield make
    for page in pages:
        page.close()


def down(served):
    served.state["describe"] = lambda: {"verdict": "absent", "running": False, "message": "No rig is running."}


def stage_link(p):
    box = p.locator("a.go", has_text="Stage").bounding_box()
    return box["x"] + box["width"] / 2, box["y"] + box["height"] / 2


def test_it_starts_after_the_idle_time_and_only_then_loads_the_drawing(opened):
    """On the fake clock: not at 2.9 s after the last input, and by 3.5 s
    (the timer looks every 0.5 s)."""
    v = opened(clock=True)
    p = v.page
    p.mouse.move(100, 100)
    p.mouse.move(120, 110)
    v.advance(2900)
    assert v.state()["due"] == 0 and v.state()["phase"] == "off"
    assert not any(name in v.loaded() for name in LAZY), "the normal page has not paid for it"
    v.advance(600)
    assert v.state()["due"] >= 1
    state = v.shown()
    assert all(name in v.loaded() for name in LAZY), v.loaded()
    session = state["session"]
    assert session["listening"] and session["drawing"] and session["context"] == "running"
    assert (session["width"], session["height"]) == (1200, 800)
    assert p.locator(".visualiser").is_visible()


def test_input_keeps_it_away(opened):
    v = opened(clock=True)
    for i in range(8):
        v.page.mouse.move(200 + 10 * i, 200)
        v.advance(2500)
    assert v.state()["due"] == 0 and v.state()["phase"] == "off" and not v.state()["loaded"]
    v.advance(3100)
    assert v.state()["due"] >= 1, "and without input it is due"


def test_a_click_that_puts_the_page_back_reaches_nothing_under_it(opened):
    v = opened()
    p = v.page
    p.evaluate("""() => { window.clicks = 0; window.downs = 0;
      document.getElementById('app').addEventListener('click', () => window.clicks++);
      document.getElementById('app').addEventListener('pointerdown', () => window.downs++); }""")
    x, y = stage_link(p)
    p.mouse.move(x, y)
    v.shown()
    p.mouse.click(x, y)
    v.off()
    assert p.evaluate("[location.hash, window.clicks, window.downs]") == ["", 0, 0]
    assert v.state()["session"]["listening"] is False, "the input is let go"
    p.mouse.click(x, y)
    p.wait_for_function("location.hash === '#stage'")


def test_a_tap_that_puts_the_page_back_reaches_nothing_under_it(opened):
    v = opened(touch=True)
    p = v.page
    p.evaluate("() => { window.clicks = 0; document.getElementById('app').addEventListener('click', () => window.clicks++); }")
    x, y = stage_link(p)
    v.shown()
    p.touchscreen.tap(x, y)
    v.off()
    assert p.evaluate("[location.hash, window.clicks]") == ["", 0]
    p.touchscreen.tap(x, y)
    p.wait_for_function("location.hash === '#stage'")


@pytest.mark.parametrize("key", ["Enter", " "])
def test_a_key_that_puts_the_page_back_reaches_nothing_under_it(opened, key):
    v = opened()
    p = v.page
    p.locator("a.go", has_text="Stage").focus() if key == "Enter" else p.locator("#visualiser-on").focus()
    v.shown()
    p.keyboard.press(key)
    v.off()
    assert p.evaluate("location.hash") == ""
    assert p.locator("#visualiser-on").is_checked(), "Space did not untick the box it was on"
    assert v.state()["on"] is True


@pytest.mark.parametrize("lost", ["blur", "hidden"])
def test_a_key_whose_release_goes_elsewhere_does_not_leave_it_catching(opened, lost):
    """Control down puts the page back; its release goes to another window
    (Ctrl+T, Alt+Tab), so it is never seen. Found in review: the invisible
    visualiser then took every click until a reload."""
    v = opened()
    p = v.page
    x, y = stage_link(p)
    v.shown()
    p.keyboard.down("Control")
    v.until("() => sushiRigVisualiser().phase === 'closing' && sushiRigVisualiser().held.length === 1")
    if lost == "blur":
        p.evaluate("() => window.dispatchEvent(new Event('blur'))")
    else:
        p.evaluate("""() => { Object.defineProperty(document, 'hidden', {configurable: true, get: () => true});
          document.dispatchEvent(new Event('visibilitychange')); delete document.hidden; }""")
    v.off()
    p.keyboard.up("Control")
    p.mouse.click(x, y)
    p.wait_for_function("location.hash === '#stage'")


def test_it_lets_go_of_the_page_within_its_ceiling_whatever_is_held(opened):
    """A release that never comes and no blur either: it still goes, by the
    ceiling, and the next click is the page's."""
    v = opened()
    p = v.page
    x, y = stage_link(p)
    v.shown()
    p.keyboard.down("Shift")
    v.until("() => sushiRigVisualiser().phase === 'closing'")
    v.until("() => sushiRigVisualiser().phase === 'off'", timeout=5000)
    # Let go only now (a click with Shift held opens the link in a new window).
    p.keyboard.up("Shift")
    p.mouse.click(x, y)
    p.wait_for_function("location.hash === '#stage'")


def test_moving_the_pointer_puts_the_page_back(opened):
    v = opened()
    v.page.mouse.move(300, 300)
    v.shown()
    v.page.mouse.move(310, 305)
    assert v.off()["session"]["drawing"] is False


# The context's resume held back until the test lets it go (window.letResume).
SLOW_RESUME = """(() => { const real = AudioContext.prototype.resume;
  AudioContext.prototype.resume = function () {
    if (!window.slowResume) return real.call(this);
    return new Promise((done) => { window.letResume = () => real.call(this).then(() => { window.resumed = true; done(); }); });
  }; })();"""


def test_a_stop_while_the_audio_resumes_leaves_nothing_drawing(opened):
    """Found in review: put back while `resume()` was pending, it went on
    drawing, unseen, at 30 frames a second."""
    v = opened(init=[SLOW_RESUME])
    p = v.page
    v.shown()
    p.mouse.move(5, 5)
    assert v.off()["session"]["context"] == "suspended"
    p.evaluate("() => { window.slowResume = true; }")
    v.until("() => sushiRigVisualiser().phase === 'shown' && typeof window.letResume === 'function'")
    p.mouse.move(50, 50)
    v.off()
    p.evaluate("() => window.letResume()")
    v.until("() => window.resumed === true && sushiRigVisualiser().session.context === 'suspended'")
    assert v.state()["session"]["drawing"] is False


# The visualiser's own canvas gets no WebGL, so butterchurn cannot be made;
# every AudioContext made and closed is counted.
NO_GL = """(() => { const real = HTMLCanvasElement.prototype.getContext;
  HTMLCanvasElement.prototype.getContext = function (kind, ...rest) {
    return kind === 'webgl2' && this.closest('.visualiser') ? null : real.call(this, kind, ...rest); };
  window.made = 0; window.unmade = 0; const Real = window.AudioContext;
  window.AudioContext = class extends Real { constructor(...a) { super(...a); window.made++; }
    close() { window.unmade++; return super.close(); } }; })();"""


def test_a_visualiser_that_cannot_be_made_closes_its_audio_and_is_not_tried_again(opened):
    v = opened(init=[NO_GL])
    p = v.page
    v.until("() => sushiRigVisualiser().broken !== null && sushiRigVisualiser().phase === 'off'")
    assert p.evaluate("[window.made, window.unmade]") == [1, 1], "no AudioContext is left open"
    assert "Reload the page" in p.locator(".visualiser-note").inner_text()
    before = v.state()["attempts"]
    state = v.refused(after=before)
    assert state["why"].startswith("Off: butterchurn could not draw here")
    assert p.evaluate("window.made") == 1, "not tried again"
    p.locator("#visualiser-on").uncheck()
    p.locator("#visualiser-on").check()
    v.until("() => window.made === 2 && window.unmade === 2")


def test_a_lost_context_stops_it_and_it_starts_afresh_once(opened):
    v = opened()
    p = v.page
    lose = "() => document.querySelector('.visualiser canvas').getContext('webgl2').getExtension('WEBGL_lose_context').loseContext()"
    v.shown()
    p.evaluate(lose)
    # The loss is an event the browser delivers when it will, and the session
    # disposes of itself before the page hears of it: waited for, as a whole.
    v.until("""() => { const v = sushiRigVisualiser();
      return v.phase === 'off' && v.lost === 1 && v.session === null && (v.error || '').includes('context lost'); }""")
    assert "context lost" in v.page.locator(".visualiser-note").inner_text()
    v.shown()
    p.evaluate(lose)
    v.until("() => sushiRigVisualiser().broken !== null && sushiRigVisualiser().phase === 'off'")
    assert "twice" in v.refused(after=v.state()["attempts"])["why"]


def test_the_off_switch_is_kept_in_this_browser(opened):
    v = opened()
    p = v.page
    p.locator("#visualiser-on").uncheck()
    assert json.loads(p.evaluate("localStorage.getItem('sushi-rig.visualiser')"))["on"] is False
    p.reload()
    v.until("() => window.sushiRigVisualiser && window.sushiRigVisualiser().running")
    assert not p.locator("#visualiser-on").is_checked()
    state = v.refused()
    assert state["why"] == "Off." and not state["loaded"]
    assert "Off." in p.locator(".visualiser-note").inner_text()
    p.locator("#visualiser-on").check()
    v.shown()


def test_the_idle_time_is_set_from_the_page(opened):
    v = opened(settings={"idle": 600}, clock=True)
    p = v.page
    field = p.locator("#visualiser-idle")
    assert field.input_value() == "600"
    field.fill("4")
    field.press("Tab")
    assert json.loads(p.evaluate("localStorage.getItem('sushi-rig.visualiser')"))["idle"] == 4
    v.advance(3900)
    assert v.state()["due"] == 0
    v.advance(600)
    assert v.state()["due"] >= 1
    v.shown()
    p.mouse.move(5, 5)
    v.off()
    field.fill("1")
    field.press("Tab")
    assert field.input_value() == "3", "held to the bounds"


def test_reduced_motion_never_starts_it(opened):
    state = opened(reduced="reduce").refused()
    assert "reduced motion" in state["why"] and not state["loaded"]


def test_a_narrow_window_never_starts_it_and_has_no_line_about_it(opened):
    v = opened(width=800)
    state = v.refused()
    assert "900 px" in state["why"] and not state["loaded"]
    assert not v.page.locator(".visualiser-settings").is_visible()


def test_it_starts_only_while_a_rig_is_running_and_goes_when_it_stops(opened, served):
    v = opened()
    v.shown()
    down(served)
    v.until("() => !sushiRigVisualiser().running")
    state = v.off()
    assert state["session"]["listening"] is False
    state = v.refused(after=state["attempts"])
    assert state["why"] == "Starts only while a rig is running."


def test_a_software_renderer_is_refused(opened):
    v = opened(gpu=False)
    state = v.refused()
    assert "software" in state["why"] and not state["loaded"]
    assert "software" in v.page.locator(".visualiser-note").inner_text()


def test_a_hidden_tab_lets_go_of_the_input_and_a_shown_one_hears_and_draws_again(opened):
    v = opened()
    v.shown()
    hide = """(hidden) => { Object.defineProperty(document, 'hidden', {configurable: true, get: () => hidden});
      Object.defineProperty(document, 'visibilityState', {configurable: true, get: () => hidden ? 'hidden' : 'visible'});
      document.dispatchEvent(new Event('visibilitychange')); }"""
    v.page.evaluate(hide, True)
    v.until("""() => { const s = sushiRigVisualiser().session;
      return !s.drawing && !s.listening && s.context === 'suspended'; }""")
    assert v.state()["phase"] == "shown"
    frames = v.state()["session"]["frames"]
    v.page.evaluate(hide, False)
    v.until(f"() => sushiRigVisualiser().session.listening && sushiRigVisualiser().session.frames > {frames + 3}")


def test_the_page_keeps_polling_under_it(opened):
    v = opened()
    v.shown()
    count = "performance.getEntriesByType('resource').filter((r) => r.name.endsWith('/health')).length"
    before = v.page.evaluate(count)
    v.until(f"() => {count} >= {before + 2}")
    assert v.state()["phase"] == "shown"


def test_xruns_rising_while_it_listens_stop_it_until_it_is_switched_on_again(opened, served):
    """The capture stream is in Sushi's cycle, so a glitch while it listens
    stops it, said on the line, and it does not start again by itself. The
    xruns are pw-top's, as the XRUNS tile reads them: here the fake's sample
    of `pw-top -b -n 2` with sushi's ERR raised."""
    v = opened()
    p = v.page
    v.shown()
    v.until("() => sushiRigVisualiser().session.listening")
    served.pw_top_output = re.sub(r"^(R\s+130(?:\s+\S+){6}\s+)0(\s+\+ sushi)$", r"\g<1>5\2", served.pw_top_output, flags=re.M)
    assert [line.split()[8] for line in served.pw_top_output.splitlines() if line.endswith("+ sushi")] == ["5", "5"]
    state = v.off()
    assert "glitched" in state["tripped"] and "from 0 to 5" in state["tripped"]
    assert state["session"]["listening"] is False
    state = v.refused(after=state["attempts"])
    assert state["why"] == state["tripped"] and "glitched" in p.locator(".visualiser-note").inner_text()
    p.locator("#visualiser-on").uncheck()
    p.locator("#visualiser-on").check()
    assert v.shown()["tripped"] is None


def test_what_it_heard_is_said_on_the_line(opened):
    """The fake agent has no machine file to answer /machine/roles, so the
    browser's own choice, said so."""
    v = opened()
    v.shown()
    assert v.state()["pick"]["how"] == "default"
    v.page.mouse.move(1, 1)
    v.off()
    assert "(the browser's choice)" in v.page.locator(".visualiser-note").inner_text()
    pick = v.page.locator("#visualiser-input")
    pick.focus()
    v.page.wait_for_function("document.querySelectorAll('#visualiser-input option').length > 1")
    value = v.page.evaluate("document.querySelectorAll('#visualiser-input option')[1].value")
    pick.select_option(value)
    v.page.locator("#visualiser-channel").select_option("1")
    kept = json.loads(v.page.evaluate("localStorage.getItem('sushi-rig.visualiser')"))
    assert kept["input"]["deviceId"] == value and kept["channel"] == 1
    v.shown()
    assert v.state()["pick"]["how"] == "chosen" and v.state()["pick"]["channel"] == 1


# The visualiser's tap (#195), driven in the page: the agent's answers to
# POST /visualiser/tap and its release are stood in by Playwright's routing,
# one of Chromium's fake inputs renamed as the tap's source. The fake agent
# has no machine file, so /machine/roles answers 409 throughout (review of
# PR #200: that must not lose the tap).

# Every getUserMedia stream, for a test to end one of its tracks.
STREAMS = """(() => { const real = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
  window.__streams = [];
  navigator.mediaDevices.getUserMedia = async (c) => { const s = await real(c); window.__streams.push(s); return s; }; })();"""


def fake_labels(p):
    return p.evaluate("""async () => (await navigator.mediaDevices.getUserMedia({audio: true})
      .then((s) => { s.getTracks().forEach((t) => t.stop()); return navigator.mediaDevices.enumerateDevices(); }))
      .filter((d) => d.kind === 'audioinput' && d.deviceId !== 'default').map((d) => d.label)""")


class TapAgent:
    """The agent's tap routes, stood in: answers with `label` (or refuses
    with `refuse`), and records each request's path and body."""

    def __init__(self, page, label, refuse=None, renew=0.3, hold=False):
        self.label, self.refuse, self.renew, self.asked = label, refuse, renew, []
        self.hold, self.held = hold, []
        page.route("**/visualiser/tap", self.tap)
        page.route("**/visualiser/tap/release", self.release)

    def answer(self, route):
        route.fulfill(status=200, content_type="application/json", body=json.dumps(
            {"ok": True, "source": {"node": "sushi_rig_output", "label": self.label},
             "lease_s": 30, "renew_s": self.renew}))

    def tap(self, route):
        self.asked.append(("tap", json.loads(route.request.post_data or "{}")))
        if self.hold:
            self.held.append(route)  # answered by the test, late
            return
        if self.refuse:
            route.fulfill(status=409, content_type="application/json",
                          body=json.dumps({"ok": False, "code": "not_realtime", "reason": self.refuse}))
            return
        route.fulfill(status=200, content_type="application/json", body=json.dumps(
            {"ok": True, "source": {"node": "sushi_rig_output", "label": self.label},
             "lease_s": 30, "renew_s": self.renew}))

    def release(self, route):
        self.asked.append(("release", json.loads(route.request.post_data or "{}")))
        route.fulfill(status=200, content_type="application/json", body='{"ok": true}')

    def count(self, kind):
        return sum(1 for k, _ in self.asked if k == kind)

    def wait(self, page, test, seconds=5):
        deadline = time.monotonic() + seconds
        while not test() and time.monotonic() < deadline:
            page.wait_for_timeout(100)
        return test()


def test_the_rigs_output_is_asked_for_heard_renewed_and_given_back(opened):
    v = opened()
    p = v.page
    labels = fake_labels(p)
    agent = TapAgent(p, labels[-1])
    v.shown()
    pick = v.state()["pick"]
    assert (pick["how"], pick["label"], pick["channel"]) == ("output", labels[-1], None), \
        "the tap's source, though /machine/roles answered 409"
    page_id = agent.asked[0][1]["page"]
    assert agent.wait(p, lambda: agent.count("tap") >= 3), "renewed while it listens"
    assert {body["page"] for _, body in agent.asked} == {page_id}, "one page, one lease"
    p.mouse.move(1, 1)
    v.off()
    assert agent.wait(p, lambda: ("release", {"page": page_id, "now": False}) in agent.asked), \
        "given back, to linger"
    renewals = agent.count("tap")
    p.wait_for_timeout(1000)
    assert agent.count("tap") == renewals, "and no longer renewed"
    assert "(the rig's output: the master, after its limiter)" in p.locator(".visualiser-note").inner_text()


def test_a_tap_refused_falls_back_and_says_why(opened):
    v = opened()
    p = v.page
    agent = TapAgent(p, "unused", refuse="the visualiser tap (pid 7) got no realtime thread ...")
    v.shown()
    state = v.state()
    assert state["pick"]["how"] == "default", "no machine file here: the browser's choice"
    assert state["tap"]["holding"] is False and "no realtime thread" in state["tap"]["reason"]
    assert agent.count("tap") == 1 and agent.count("release") == 0


def test_a_trip_while_on_the_rigs_output_lets_the_tap_go_at_once_and_says_so(opened, served):
    v = opened()
    p = v.page
    agent = TapAgent(p, fake_labels(p)[-1])
    v.shown()
    v.until("() => sushiRigVisualiser().session.listening")
    assert v.state()["pick"]["how"] == "output"
    page_id = agent.asked[0][1]["page"]
    served.pw_top_output = re.sub(r"^(R\s+130(?:\s+\S+){6}\s+)0(\s+\+ sushi)$", r"\g<1>5\2", served.pw_top_output, flags=re.M)
    state = v.off()
    assert "Its tap on the rig's output was let go" in state["tripped"]
    assert agent.wait(p, lambda: ("release", {"page": page_id, "now": True}) in agent.asked), \
        "at once, not after the linger"


def test_an_input_that_ends_while_heard_is_chosen_again_and_heard(opened):
    """The tap stopped (or the interface went): the track ends, and the page
    chooses again and hears that, rather than drawing silence."""
    v = opened(init=(STREAMS,))
    p = v.page
    agent = TapAgent(p, fake_labels(p)[-1])
    v.shown()
    before = p.evaluate("window.__streams.length")
    asked = agent.count("tap")
    p.evaluate("""(() => { const s = window.__streams[window.__streams.length - 1];
      s.getAudioTracks()[0].dispatchEvent(new Event('ended')); })()""")
    v.until(f"() => sushiRigVisualiser().reopened === 1 && window.__streams.length > {before}")
    v.until("() => sushiRigVisualiser().session.listening")
    assert agent.count("tap") > asked, "the tap asked for again"
    assert v.state()["phase"] == "shown" and v.state()["pick"]["how"] == "output"


def test_the_rigs_output_chosen_by_hand_keeps_its_lease(opened):
    """Chosen on the line, "Sushi rig output" is still the tap: its lease is
    kept and renewed, not given back at once (review of PR #200)."""
    v = opened()
    p = v.page
    label = fake_labels(p)[-1]
    agent = TapAgent(p, label)
    v.shown()
    p.mouse.move(1, 1)
    v.off()
    pick = p.locator("#visualiser-input")
    pick.focus()
    p.wait_for_function("document.querySelectorAll('#visualiser-input option').length > 1")
    value = p.evaluate(f"""[...document.querySelectorAll('#visualiser-input option')]
      .find((o) => o.textContent === {json.dumps(label)}).value""")
    pick.select_option(value)
    asked = agent.count("tap")
    released = agent.count("release")
    v.shown()
    state = v.state()
    assert (state["pick"]["deviceId"], state["pick"]["how"]) == (value, "output")
    assert agent.wait(p, lambda: agent.count("tap") >= asked + 3), "renewed while it listens"
    assert agent.count("release") == released, "not given back"


def test_an_answer_too_late_for_the_page_is_given_back(opened):
    """The page stopped waiting (9 s, here 0.3) and fell back; the agent may
    still start the tap and give this page the lease, so it is given back
    then and there (review of PR #200)."""
    v = opened(init=("window.sushiRigTapAskMs = 300;",))
    p = v.page
    agent = TapAgent(p, fake_labels(p)[-1], hold=True)
    v.shown()
    assert v.state()["pick"]["how"] == "default" and "did not answer within 0.3 s" in v.state()["tap"]["reason"]
    page_id = agent.asked[0][1]["page"]
    assert agent.wait(p, lambda: ("release", {"page": page_id, "now": False}) in agent.asked), \
        "given back once it gave up waiting"
    for route in agent.held:
        try:
            agent.answer(route)
        except Exception:  # noqa: BLE001 -- the page's request was aborted: nothing to answer
            pass


HIDE = """(hidden) => { Object.defineProperty(document, 'hidden', {configurable: true, get: () => hidden});
  Object.defineProperty(document, 'visibilityState', {configurable: true, get: () => hidden ? 'hidden' : 'visible'});
  document.dispatchEvent(new Event('visibilitychange')); }"""


def test_a_tab_hidden_while_the_agent_answers_neither_listens_nor_renews(opened):
    v = opened()
    p = v.page
    agent = TapAgent(p, fake_labels(p)[-1], hold=True)
    v.until("() => sushiRigVisualiser().phase === 'shown'")
    assert agent.wait(p, lambda: agent.held), "the page asked"
    page_id = agent.asked[0][1]["page"]
    p.evaluate(HIDE, True)
    agent.answer(agent.held.pop())
    assert agent.wait(p, lambda: ("release", {"page": page_id, "now": False}) in agent.asked), "given back"
    p.wait_for_timeout(1000)
    assert agent.count("tap") == 1, "and never renewed"
    state = v.state()
    assert state["session"]["listening"] is False and state["tap"]["holding"] is False

# --------------------------------------------------------------------------- #
# The looks in the page (#196)
# --------------------------------------------------------------------------- #

# Every shader the page compiles and links, checked: a GLSL error is not a
# JavaScript one, and butterchurn does not ask, so without this a look whose
# shader failed would draw nothing and raise nothing.
GL_CHECK = """(() => { window.glErrors = []; const P = WebGL2RenderingContext.prototype;
  const compile = P.compileShader; P.compileShader = function (s) { compile.call(this, s);
    if (!this.getShaderParameter(s, this.COMPILE_STATUS)) window.glErrors.push(this.getShaderInfoLog(s)); };
  const link = P.linkProgram; P.linkProgram = function (p) { link.call(this, p);
    if (!this.getProgramParameter(p, this.LINK_STATUS)) window.glErrors.push('link: ' + this.getProgramInfoLog(p)); }; })();"""


# Every stream the page's captures open, kept, to see that each is let go.
CAPTURES = """(() => { window.captures = []; const real = MediaDevices.prototype.getUserMedia;
  MediaDevices.prototype.getUserMedia = async function (...a) { const s = await real.apply(this, a); window.captures.push(s); return s; }; })();"""


# A test's stand-in for the guitar: getUserMedia answers with a chord of
# saws (an open E minor) through a lowpass, plucked every 0.6 s with a burst
# of noise, from an AudioContext of its own. Test-only, as Chromium's fake
# capture device (a beep a second) is otherwise all the page hears.
CHORD = """(() => {
  navigator.mediaDevices.getUserMedia = async () => {
    const c = new AudioContext(); const out = c.createMediaStreamDestination();
    const lp = c.createBiquadFilter(); lp.type = 'lowpass'; lp.frequency.value = 3500; lp.Q.value = 2;
    const amp = c.createGain(); amp.gain.value = 0; lp.connect(amp); amp.connect(out);
    for (const f of [82.4, 123.5, 164.8, 196, 246.9, 329.6]) { const o = c.createOscillator(); o.type = 'sawtooth'; o.frequency.value = f;
      const g = c.createGain(); g.gain.value = 0.12; o.connect(g); g.connect(lp); o.start(); }
    const n = c.createBufferSource(); const b = c.createBuffer(1, c.sampleRate, c.sampleRate); const d = b.getChannelData(0);
    for (let i = 0; i < d.length; i++) d[i] = Math.random() * 2 - 1; n.buffer = b; n.loop = true;
    const ng = c.createGain(); ng.gain.value = 0; n.connect(ng); ng.connect(out); n.start();
    const pluck = () => { const t = c.currentTime; amp.gain.cancelScheduledValues(t); amp.gain.setValueAtTime(1, t);
      amp.gain.exponentialRampToValueAtTime(0.15, t + 0.5); ng.gain.setValueAtTime(0.25, t); ng.gain.exponentialRampToValueAtTime(0.001, t + 0.06); };
    pluck(); setInterval(pluck, 600); await c.resume(); return out.stream; };
})();"""


def luminance(v):
    """The screen's luminance, 0 to 255, as {mean, sd}, from a screenshot
    decoded in the page (no image library here), with the hint hidden so
    only the drawing counts."""
    v.page.evaluate("() => { const h = document.querySelector('.visualiser-hint'); if (h) h.style.visibility = 'hidden'; }")
    png = base64.b64encode(v.page.screenshot()).decode()
    return v.page.evaluate("""async (png) => { const img = new Image(); img.src = 'data:image/png;base64,' + png; await img.decode();
      const c = document.createElement('canvas'); c.width = 320; c.height = 200; const g = c.getContext('2d');
      g.drawImage(img, 0, 0, 320, 200); const d = g.getImageData(0, 0, 320, 200).data; let s = 0, s2 = 0; const n = d.length / 4;
      for (let i = 0; i < d.length; i += 4) { const y = 0.2126 * d[i] + 0.7152 * d[i + 1] + 0.0722 * d[i + 2]; s += y; s2 += y * y; }
      const mean = s / n; return {mean, sd: Math.sqrt(Math.max(0, s2 / n - mean * mean))}; }""", png)


def seeded(seed):
    return f"window.sushiRigVisualiserSeed = {seed};"


@pytest.mark.parametrize("look", LOOKS)
def test_every_look_loads_and_draws_without_an_error(opened, look):
    """Held by name, on a plucked chord: it comes on, draws, and nothing
    failed: no page error, no shader that did not compile or link, no WebGL
    error on the console; and the screen is not black: some light, and not
    all one shade (PR #201's review: Mirror once went black within seconds
    and only eyes on a screenshot saw it). The equaliser looks draw on their
    own canvas, half the buffer's size, butterchurn's hidden."""
    v = opened(settings={"look": look}, init=[GL_CHECK, CHORD])
    state = v.shown()
    session = state["session"]
    assert session["look"] == look and session["choice"] == look and session["seconds"] is None
    kind = "eq" if look in EQ else "milkdrop"
    assert session["kind"] == kind
    if kind == "eq":
        v.until("() => sushiRigVisualiser().session.eq.drawn > 2")
        eq = v.state()["session"]["eq"]
        assert eq["look"] == look and (eq["width"], eq["height"]) == (600, 400), "half the 1200 by 800 buffer"
    v.page.wait_for_timeout(1500)
    # A strobe fades back through a frame that is one shade: Shutter's comp
    # is mix(l, 1 - l, q1) and its flash halves each frame, so at q1 = 0.5
    # every pixel is mid-grey (mean 127, sd 0.03: CI on PR #202), and Tunnel
    # has a low-contrast frame of the same kind. About one frame in twenty
    # on a plucked chord. Only that, a bright frame of one shade, is
    # forgiven, and looked at again up to four times; a dark frame fails at
    # once, so a look that goes black is caught as before.
    seen = [luminance(v)]
    while seen[-1]["mean"] >= 0.5 and seen[-1]["sd"] < 4 and len(seen) < 5:
        v.page.wait_for_timeout(300)
        seen.append(luminance(v))
    assert seen[-1]["mean"] >= 0.5 and seen[-1]["sd"] >= 4, seen
    assert v.page.evaluate("window.glErrors") == []
    assert v.page.evaluate("""() => [...document.querySelectorAll('.visualiser canvas')].map((c) => c.classList.contains('off'))""") \
        == ([True, False] if kind == "eq" else [False, True])
    assert [m for m in v.console if m[0] == "error" and "GL" in m[1]] == []
    assert [m for m in v.console if "GL_INVALID" in m[1] or "WebGL: " in m[1]] == []


def test_a_broken_shader_is_caught(opened):
    """The check above, shown to work: a look whose shader does not compile
    is seen (a comp with an undeclared name, loaded by hand)."""
    v = opened(settings={"look": "Tunnel"}, init=[GL_CHECK])
    v.shown()
    v.page.evaluate("""async () => { const P = await import('/ui/visualiser-presets.js');
      const B = window.butterchurn.default || window.butterchurn; const c = document.createElement('canvas');
      const viz = B.createVisualizer(new AudioContext(), c, {width: 64, height: 64});
      const p = P.LOOKS[3].make({hue: 0, sat: 0, speed: 1, twist: 0});
      viz.loadPreset({...p, comp: 'shader_body { ret = nothing_here; }'}, 0); }""")
    assert any("nothing_here" in e for e in v.page.evaluate("window.glErrors"))


def test_the_random_order_on_the_clock_never_repeats_and_keeps_its_times(opened):
    """On the fake clock, paused once the page is up so that only the test
    moves it (an installed clock runs on with the wall between calls: PR
    #201's review), seeded: each look's time is between 30 and 60 s; 400 ms
    before it is up the look is still on, and within a frame or two of it
    the next is, never the same one. The long stretch is jumped
    (fast_forward, paused still): run_for over it would draw some 1400 of
    butterchurn's frames a look in software."""
    v = opened(clock=True, init=[seeded(7)])
    v.shown()
    v.page.clock.pause_at((v.page.evaluate("Date.now()") + 1000) / 1000)  # seconds, in Playwright's Python
    v.advance(100)
    shown = []
    for _ in range(8):
        v.until("() => sushiRigVisualiser().session.at !== null")
        session = v.state()["session"]
        assert session["choice"] == "random" and 30 <= session["seconds"] <= 60, session
        name, at, seconds = session["look"], session["at"], session["seconds"]
        left = at + seconds * 1000 - v.page.evaluate("performance.now()")
        v.page.clock.fast_forward(int(left - 400))
        v.advance(300)
        assert v.state()["session"]["look"] == name, "not before its time"
        v.advance(200)
        session = v.state()["session"]
        assert session["look"] != name, "on time"
        assert seconds * 1000 <= session["at"] - at <= seconds * 1000 + 100, (name, seconds, session["at"] - at)
        shown.append(name)
    history = [h["name"] for h in v.state()["session"]["history"]]
    assert history[:8] == shown and all(a != b for a, b in zip(history, history[1:])), history
    assert len(set(history)) >= 5, history
    assert v.state()["phase"] == "shown"


def test_a_look_shown_again_is_not_switched_on_its_first_frame(opened):
    """Put away for longer than its time and shown again, the look on comes
    back for a full time of its own, not for one frame (PR #201's review:
    its time had run on while it was away)."""
    v = opened(clock=True, init=[seeded(4)])
    v.shown()
    first = v.state()["session"]
    v.page.mouse.move(5, 5)
    v.off()
    v.page.clock.fast_forward(int(first["seconds"] * 1000) + 30000)
    state = v.shown()
    session = state["session"]
    assert session["look"] == first["look"] and len(session["history"]) == len(first["history"]), session["history"]
    v.until("() => sushiRigVisualiser().session.at !== null")
    session = v.state()["session"]
    left = session["at"] + session["seconds"] * 1000 - v.page.evaluate("performance.now()")
    assert left > session["seconds"] * 1000 - 2000, "timed afresh"


def test_a_look_held_by_name_stays(opened):
    v = opened(clock=True, settings={"look": "Aurora"})
    v.shown()
    for _ in range(3):
        v.page.clock.fast_forward(50000)
        v.advance(200)
    session = v.state()["session"]
    assert session["look"] == "Aurora" and session["seconds"] is None
    assert [h["name"] for h in session["history"]] == ["Aurora"]
    assert v.state()["phase"] == "shown"


def test_a_random_selection_draws_only_from_its_looks(opened):
    v = opened(clock=True, settings={"look": "random-eq"}, init=[seeded(3)])
    v.shown()
    for _ in range(5):
        session = v.state()["session"]
        v.page.clock.fast_forward(int(session["seconds"] * 1000) + 100)
        v.advance(200)
    history = [h["name"] for h in v.state()["session"]["history"]]
    assert len(history) >= 5 and set(history) <= set(EQ) and all(a != b for a, b in zip(history, history[1:])), history


def test_the_look_is_chosen_on_the_line_and_kept_in_this_browser(opened):
    """After the line's other controls; random until one is picked; the pick
    is kept, comes back on a reload, and is what is shown."""
    v = opened()
    p = v.page
    order = p.evaluate("() => [...document.querySelectorAll('.visualiser-settings input, .visualiser-settings select, .visualiser-settings button')].map((e) => e.id || e.className)")
    assert order == ["visualiser-on", "visualiser-idle", "visualiser-input", "visualiser-channel", "visualiser-now", "visualiser-look"]
    pick = p.locator("#visualiser-look")
    assert pick.input_value() == "random"
    assert p.evaluate("[...document.querySelectorAll('#visualiser-look option')].map((o) => o.value)") == \
        ["random", "random-mono", "random-eq", *LOOKS]
    pick.select_option("Curve")
    assert json.loads(p.evaluate("localStorage.getItem('sushi-rig.visualiser')"))["look"] == "Curve"
    p.reload()
    v.until("() => window.sushiRigVisualiser && window.sushiRigVisualiser().running")
    assert p.locator("#visualiser-look").input_value() == "Curve" and v.state()["look"] == "Curve"
    assert v.shown()["session"]["look"] == "Curve"
    p.mouse.move(3, 3)
    v.off()
    p.locator("#visualiser-look").select_option("Bars")
    assert v.shown()["session"]["look"] == "Bars", "a session already made takes up the new choice"


def test_a_lost_context_on_an_equaliser_look_is_as_on_butterchurn(opened):
    """The 2D canvas's context taken away (its `contextlost`, sent by hand:
    Chromium gives no way to lose a 2D context from a page): it stops, says
    so, lets go of the input and its canvas, starts afresh once, and a
    second loss leaves it off until a reload."""
    v = opened(settings={"look": "Bars"}, init=[CAPTURES])
    p = v.page
    lose = "() => document.querySelector('.visualiser canvas.visualiser-flat').dispatchEvent(new Event('contextlost', {cancelable: true}))"
    v.shown()
    p.evaluate("() => { window.oldGl = document.querySelector('.visualiser canvas:not(.visualiser-flat)'); }")
    assert p.evaluate("window.oldGl.getContext('webgl2').isContextLost()") is False
    p.evaluate(lose)
    v.until("""() => { const v = sushiRigVisualiser();
      return v.phase === 'off' && v.lost === 1 && v.session === null && (v.error || '').includes('context lost'); }""")
    assert "canvas context lost" in p.locator(".visualiser-note").inner_text()
    v.until("() => window.oldGl.getContext('webgl2').isContextLost()", timeout=5000)
    assert p.evaluate("!document.body.contains(window.oldGl)"), \
        "butterchurn's WebGL context is given back at once, not left for the garbage collector (PR #201's review)"
    assert p.evaluate("document.querySelectorAll('.visualiser canvas').length") == 1, "its canvas went with it"
    assert p.evaluate("window.captures.length > 0 && window.captures.every((s) => s.getTracks().every((t) => t.readyState === 'ended'))"), \
        "the input is let go"
    state = v.shown()
    assert state["session"]["look"] == "Bars" and p.evaluate("document.querySelectorAll('.visualiser canvas').length") == 2
    p.evaluate(lose)
    v.until("() => sushiRigVisualiser().broken !== null && sushiRigVisualiser().phase === 'off'")
    assert "twice" in v.refused(after=v.state()["attempts"])["why"]


# The equaliser canvas gets no 2D context: the equaliser looks cannot be
# made. Each visualiser canvas asked for WebGL is kept, in `window.glCanvases`.
NO_2D = """(() => { const real = HTMLCanvasElement.prototype.getContext; window.glCanvases = [];
  HTMLCanvasElement.prototype.getContext = function (kind, ...rest) {
    if (kind === 'webgl2' && this.closest('.visualiser') && !window.glCanvases.includes(this)) window.glCanvases.push(this);
    return kind === '2d' && this.classList.contains('visualiser-flat') ? null : real.call(this, kind, ...rest); }; })();"""


def test_equaliser_looks_that_cannot_be_made_give_back_butterchurns_context(opened):
    """butterchurn was made and the equaliser looks could not be: it is off
    and says so, butterchurn's WebGL context is given back at once (PR #201's
    review), and ticked again it is tried afresh on a new canvas."""
    v = opened(init=[NO_2D])
    p = v.page
    v.until("() => sushiRigVisualiser().broken !== null && sushiRigVisualiser().phase === 'off'")
    p.evaluate("() => { window.firstGl = window.glCanvases[0]; }")
    assert "the equaliser looks could not draw here" in v.state()["broken"]
    assert p.evaluate("window.firstGl.getContext('webgl2').isContextLost()") is True
    assert p.evaluate("!document.body.contains(window.firstGl) && document.querySelectorAll('.visualiser canvas').length === 1")
    p.locator("#visualiser-on").uncheck()
    p.locator("#visualiser-on").check()
    v.until("() => sushiRigVisualiser().attempts > 1 && sushiRigVisualiser().broken !== null && sushiRigVisualiser().phase === 'off'")
    assert "the equaliser looks could not draw here" in v.state()["broken"], "tried again, and failed for the same reason, not a lost canvas"


def test_a_close_while_the_agent_answers_leaves_nothing_renewing(opened):
    """Closed while the agent started the tap: the answer, late, is not this
    page's to hold. Before the ticket it set `holding` and renewed for ever
    (round 3 of PR #200's review)."""
    v = opened()
    p = v.page
    agent = TapAgent(p, fake_labels(p)[-1], hold=True)
    v.until("() => sushiRigVisualiser().phase === 'shown'")
    assert agent.wait(p, lambda: agent.held), "the page asked"
    page_id = agent.asked[0][1]["page"]
    p.mouse.move(1, 1)
    v.off()
    assert agent.wait(p, lambda: ("release", {"page": page_id, "now": False}) in agent.asked), "given back"
    agent.answer(agent.held.pop())
    p.wait_for_timeout(1500)
    assert agent.count("tap") == 1, "never renewed"
    assert v.state()["tap"]["holding"] is False
