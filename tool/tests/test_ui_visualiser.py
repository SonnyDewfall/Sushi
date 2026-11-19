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
LAZY = ("visualiser.js", "visualiser-presets.js", "butterchurn.min.js")


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
    assert re.findall(r'name: "([^"]+)"', text) == ["Embers", "Tide", "Bloom"]
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
                                  "/ui/vendor/butterchurn-2.6.7/butterchurn.min.js"])
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
    default = {"on": True, "idle": 10, "input": None, "channel": None}
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


def test_an_input_is_said_with_its_channel_and_how_it_was_chosen(blank):
    out = rigjs(blank, """return [R.inputWords({deviceId: 'a', label: 'Audient iD14 Multichannel', channel: 0, how: 'guitar'}),
      R.inputWords({deviceId: 'default', label: '', channel: null, how: 'default'}), R.inputWords({deviceId: 'b', label: 'Cam', channel: 1, how: 'chosen'}),
      R.inputWords(null), R.softwareRenderer('Mesa Intel(R) Xe Graphics (TGL GT2)'), R.softwareRenderer(null),
      R.pointerMoved(null, {x: 1, y: 1}), R.pointerMoved({x: 1, y: 1}, {x: 1, y: 1}), R.pointerMoved({x: 1, y: 1}, {x: 1, y: 2})];""")
    assert out == ["Audient iD14 Multichannel, left channel (the machine file's guitar-in)",
                   "the browser's default input (the browser's choice)", "Cam, right channel", "no input yet",
                   False, False, True, False, True]


def test_each_preset_has_the_four_shapes_and_waves_butterchurn_draws(blank):
    out = blank.evaluate("""async () => { const P = await import('/ui/visualiser-presets.js');
      return P.PRESETS.map((p) => [p.name, p.shapes.length, p.waves.length, typeof p.frame_eqs_str, typeof p.baseVals]); }""")
    assert out == [[name, 4, 4, "string", "object"] for name in ("Embers", "Tide", "Bloom")]


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
        self.page.on("pageerror", lambda error: self.errors.append(str(error)))
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
    state = v.off()
    assert state["lost"] == 1 and "context lost" in state["error"] and state["session"] is None
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
