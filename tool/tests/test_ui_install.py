"""The page's own fonts and its home-screen install (#139).

The fonts: the three files and their licences are the ones fonts/README.md
names (by checksum), and in Chromium, with every request to anything but the
agent refused, the stage, the rack and a guide page are drawn in them: each
`FontFace` loaded from the agent, `document.fonts.check` true, and the font
Chromium actually drew each kind of text with (DevTools' platform fonts for
a node) the bundled one. The install: the manifest is served as one and is
what Chromium's own parser reads without an error, its icons are PNGs at the
sizes it says, Chromium finds nothing in the way of installing the page, and
nothing the agent answers is kept: there is no service worker, no cache in
CacheStorage, the API answers `no-store` and the page's files `no-cache`.

What stands in for what: headless Chromium on 127.0.0.1 is the phone. It
is not a phone's browser: whether Android's Chrome or iOS's Safari installs
the page from the rig's address, and opens it without its bars, is not shown
here (Chrome asks for HTTPS before it installs, and the rig serves plain
HTTP on the LAN; web-ui.md says so).
"""

from __future__ import annotations

import hashlib
import json
import re
import struct

import pytest

pytest.importorskip("aiohttp")

from sushi_rig.ui import UI_DIR  # noqa: E402

from test_ui import call, ui_agent, browser, served  # noqa: E402,F401
from ui_fake import UiRig  # noqa: E402

FONTS = UI_DIR / "fonts"
# (file, the family the CSS names, the family the font's own name table gives,
# which is what Chromium reports it drew with).
FAMILIES = [
    ("newsreader/Newsreader.woff2", "Newsreader", "Newsreader 16pt"),
    ("instrument-sans/InstrumentSans.woff2", "Instrument Sans", "Instrument Sans"),
    ("geist-mono/GeistMono.woff2", "Geist Mono", "Geist Mono"),
]


# --------------------------------------------------------------------------- #
# The fonts' files
# --------------------------------------------------------------------------- #

def test_each_font_and_licence_is_the_file_the_readme_names():
    readme = (FONTS / "README.md").read_text(encoding="utf-8")
    named = dict((path, digest) for digest, path in re.findall(r"^\s+([0-9a-f]{64})  (\S+)$", readme, re.M))
    on_disk = {path.relative_to(FONTS).as_posix() for path in FONTS.rglob("*") if path.is_file()} - {"README.md"}
    assert set(named) == on_disk, "every file here is named in the README, and nothing else"
    for path, digest in named.items():
        assert hashlib.sha256((FONTS / path).read_bytes()).hexdigest() == digest, path
    for path, _, _ in FAMILIES:
        assert (FONTS / path).read_bytes()[:4] == b"wOF2", path
        licence = (FONTS / path).parent / "OFL.txt"
        assert "SIL Open Font License, Version 1.1" in licence.read_text(encoding="utf-8"), licence


def test_the_fonts_are_served_as_fonts():
    for path, _, _ in FAMILIES:
        status, kind, body = call(ui_agent(live=UiRig()), "GET", f"/ui/fonts/{path}")
        assert status == 200 and kind == "font/woff2", (path, kind)
        assert body == (FONTS / path).read_bytes()


@pytest.mark.parametrize("sheet", ["style.css", "guide.css"])
def test_each_sheet_declares_the_three_fonts_from_the_agents_own_files(sheet):
    css = (UI_DIR / sheet).read_text(encoding="utf-8")
    faces = re.findall(r"@font-face\s*\{([^}]*)\}", css)
    declared = {re.search(r"font-family:\s*\"?([^\";]+)\"?;", face).group(1): face for face in faces}
    assert set(declared) == {family for _, family, _ in FAMILIES}
    for path, family, _ in FAMILIES:
        assert f'url("fonts/{path}")' in declared[family]
    assert "http" not in " ".join(faces) and "not bundled" not in css


# --------------------------------------------------------------------------- #
# The manifest and its icons
# --------------------------------------------------------------------------- #

def png_size(data: bytes) -> tuple[int, int]:
    assert data[:8] == b"\x89PNG\r\n\x1a\n" and data[12:16] == b"IHDR", "a PNG"
    return struct.unpack(">II", data[16:24])


def css_colour(name):
    css = (UI_DIR / "style.css").read_text(encoding="utf-8")
    return re.search(rf"--{name}:\s*(#[0-9A-Fa-f]{{6}});", css).group(1).upper()


def test_the_manifest_is_served_as_one_and_installs_the_stage():
    status, kind, body = call(ui_agent(live=UiRig()), "GET", "/ui/manifest.webmanifest")
    assert status == 200 and kind == "application/manifest+json"
    manifest = json.loads(body)
    assert manifest["display"] == "standalone"
    assert manifest["start_url"] == "/#stage" and manifest["scope"] == "/"
    assert manifest["start_url"].startswith(manifest["scope"])
    assert manifest["theme_color"].upper() == manifest["background_color"].upper() == css_colour("ground")
    assert manifest["name"] and manifest["short_name"]
    icons = {(icon["sizes"], icon.get("purpose", "any")): icon for icon in manifest["icons"]}
    assert set(icons) == {("192x192", "any"), ("512x512", "any"), ("512x512", "maskable")}
    for (sizes, _), icon in icons.items():
        assert icon["type"] == "image/png"
        status, kind, data = call(ui_agent(live=UiRig()), "GET", "/ui/" + icon["src"])
        assert status == 200 and kind == "image/png", icon
        assert "%dx%d" % png_size(data) == sizes, icon


def test_the_page_links_the_manifest_its_colour_and_the_icons():
    status, _, body = call(ui_agent(live=UiRig()), "GET", "/")
    page = body.decode("utf-8")
    assert '<link rel="manifest" href="/ui/manifest.webmanifest">' in page
    assert f'<meta name="theme-color" content="{css_colour("ground")}">' in page
    for href, size in [("/ui/icons/apple-touch-icon.png", (180, 180))]:
        assert f'href="{href}"' in page
        status, kind, data = call(ui_agent(live=UiRig()), "GET", href)
        assert status == 200 and png_size(data) == size
    status, kind, _ = call(ui_agent(live=UiRig()), "GET", "/ui/icons/icon.svg")
    assert status == 200 and kind == "image/svg+xml"


def test_the_maskable_icon_fills_its_square_and_the_rounded_one_has_clear_corners():
    """A maskable icon is cut to any shape, so it has no transparent edge;
    the "any" icons are a rounded square, so their corners are clear."""
    import zlib

    def corner_alpha(name):
        data = (UI_DIR / "icons" / name).read_bytes()
        width, _ = png_size(data)
        colour = data[25]  # IHDR colour type: 6 RGBA, 2 RGB
        if colour == 2:
            return 255
        assert colour == 6 and data[24] == 8
        chunks, at = [], 8
        while at < len(data):
            length = struct.unpack(">I", data[at:at + 4])[0]
            if data[at + 4:at + 8] == b"IDAT":
                chunks.append(data[at + 8:at + 8 + length])
            at += 12 + length
        row = zlib.decompress(b"".join(chunks))[:1 + 4 * width]
        # The first pixel of the first row is stored as it is under every
        # filter: each predicts it from neighbours that are not there, zero.
        assert row[0] in range(5)
        return row[4]

    assert corner_alpha("icon-maskable-512.png") == 255
    assert corner_alpha("apple-touch-icon.png") == 255
    assert corner_alpha("icon-192.png") == 0 and corner_alpha("icon-512.png") == 0


# --------------------------------------------------------------------------- #
# The fonts in Chromium, with nothing but the agent reachable
# --------------------------------------------------------------------------- #

def offline_page(browser, served, url, **viewport):  # noqa: F811
    """A page whose every request to anywhere but the agent is refused, and
    recorded. (page, refused, fonts asked for: path -> (status, type))."""
    context = browser.new_context(viewport=viewport or {"width": 1280, "height": 900})
    refused, fonts = [], {}

    def only_the_agent(route):
        if route.request.url.startswith(served.url + "/"):
            route.continue_()
        else:
            refused.append(route.request.url)
            route.abort("internetdisconnected")

    context.route("**/*", only_the_agent)
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))

    def seen(response):
        if "/ui/fonts/" in response.url:
            fonts[response.url[len(served.url):]] = (response.status, response.headers.get("content-type"))

    page.on("response", seen)
    page.goto(served.url + url)
    return context, page, refused, fonts, errors


def drawn_with(page, selector):
    """The fonts Chromium drew `selector`'s first element's text with:
    [(family, custom)], from DevTools' CSS.getPlatformFontsForNode.

    The page redraws a region when a poll brings something new (a push can
    bring one at any moment), and a node just drawn has no fonts until it is
    laid out: so it is asked again, the node found afresh, until it has. A
    node drawn over between finding it and asking for its fonts is gone
    ("Could not find node with given id", seen in CI on 2026-11-26 twice):
    that is asked again too, the same way."""
    from playwright.sync_api import Error as PlaywrightError

    devtools = page.context.new_cdp_session(page)
    devtools.send("DOM.enable")
    devtools.send("CSS.enable")
    used = []
    for _ in range(40):
        root = devtools.send("DOM.getDocument")["root"]["nodeId"]
        node = devtools.send("DOM.querySelector", {"nodeId": root, "selector": selector})["nodeId"]
        assert node, f"nothing matches {selector}"
        try:
            used = devtools.send("CSS.getPlatformFontsForNode", {"nodeId": node})["fonts"]
        except PlaywrightError as exc:
            if "Could not find node with given id" not in str(exc):
                raise
            used = []
        if used:
            break
        page.wait_for_timeout(50)
    devtools.detach()
    return [(font["familyName"], font["isCustomFont"]) for font in used]


CHECKS = ["600 20px Newsreader", "700 22px Newsreader", "500 14px Newsreader",
          "400 15px 'Instrument Sans'", "600 15px 'Instrument Sans'",
          "400 13px 'Geist Mono'", "600 26px 'Geist Mono'"]


def assert_in_its_own_fonts(page, served, fonts, refused, selectors):  # noqa: F811
    page.evaluate("() => document.fonts.ready")
    faces = page.evaluate("() => [...document.fonts].map((f) => [f.family.replace(/\"/g, ''), f.status])")
    assert sorted(faces) == sorted([family, "loaded"] for _, family, _ in FAMILIES), faces
    assert page.evaluate("(checks) => checks.map((c) => document.fonts.check(c))", CHECKS) == [True] * len(CHECKS)
    assert fonts == {f"/ui/fonts/{path}": (200, "font/woff2") for path, _, _ in FAMILIES}
    for selector, own in selectors.items():
        assert drawn_with(page, selector) == [(own, True)], selector
    assert refused == [], "nothing was asked of anywhere but the agent"


def test_offline_the_stage_is_drawn_in_its_three_fonts(browser, served):  # noqa: F811
    context, page, refused, fonts, errors = offline_page(browser, served, "/#stage", width=390, height=844)
    page.wait_for_selector(".stage-screen .card")
    assert_in_its_own_fonts(page, served, fonts, refused, {
        ".stage-screen .card .name": "Newsreader 16pt",   # display
        ".stage-screen button.lock": "Instrument Sans",   # body
        ".stage-screen .tile .v": "Geist Mono",           # numbers
    })
    assert errors == []
    context.close()


def test_offline_the_rack_and_a_guide_page_are_drawn_in_their_fonts(browser, served):  # noqa: F811
    context, page, refused, fonts, errors = offline_page(browser, served, "/#rack")
    page.wait_for_selector(".unit")
    assert_in_its_own_fonts(page, served, fonts, refused, {
        ".unit .name": "Newsreader 16pt",
        ".unit .knob .label": "Instrument Sans",
    })
    fonts.clear()
    page.goto(served.url + "/help/")
    page.wait_for_selector(".guide h1")
    page.evaluate("() => document.fonts.ready")
    assert drawn_with(page, ".guide h1") == [("Newsreader 16pt", True)]
    assert drawn_with(page, ".guide p") == [("Instrument Sans", True)]
    assert refused == [] and errors == []
    context.close()


# --------------------------------------------------------------------------- #
# The install, as Chromium reads it
# --------------------------------------------------------------------------- #

def test_chromium_reads_the_manifest_without_an_error_and_finds_the_page_installable(browser, served):  # noqa: F811
    page = browser.new_page(viewport={"width": 390, "height": 844})
    page.goto(served.url + "/#stage")
    page.wait_for_selector(".stage-screen .card")
    devtools = page.context.new_cdp_session(page)
    read = devtools.send("Page.getAppManifest")
    assert read["url"] == served.url + "/ui/manifest.webmanifest"
    assert read["errors"] == []
    said = read["manifest"]
    assert said["display"] == "kStandalone" and said["startUrl"] == served.url + "/#stage"
    assert said["themeColor"] == "rgba(15,16,18,1)"
    # With no service worker: Chromium no longer asks for one.
    assert devtools.send("Page.getInstallabilityErrors")["installabilityErrors"] == []
    page.close()


# --------------------------------------------------------------------------- #
# Nothing the agent answers is kept
# --------------------------------------------------------------------------- #

API = ["/health", "/live", "/live/log", "/undo", "/record", "/amp", "/rigs", "/rig/load", "/profiles",
       "/live/plugins", "/library", "/midi", "/takes"]


def test_no_answer_of_the_agents_is_kept_and_the_pages_files_are_asked_again(browser, served):  # noqa: F811
    page = browser.new_page()
    page.goto(served.url + "/#rack")
    page.wait_for_selector(".unit")
    for path in API:
        response = page.request.get(served.url + path)
        assert response.headers.get("cache-control") == "no-store", (path, response.headers)
    for path in ["/", "/ui/app.js", "/ui/rig.js", "/ui/style.css", "/ui/manifest.webmanifest",
                 "/ui/fonts/geist-mono/GeistMono.woff2", "/ui/icons/icon-192.png"]:
        response = page.request.get(served.url + path)
        assert response.headers.get("cache-control") == "no-cache" and response.headers.get("etag"), path
    # No service worker, so nothing stands between the page and the agent,
    # and nothing put in CacheStorage.
    assert page.evaluate("async () => (await navigator.serviceWorker.getRegistrations()).length") == 0
    assert page.evaluate("async () => (await caches.keys()).length") == 0
    assert "serviceWorker" not in (UI_DIR / "app.js").read_text(encoding="utf-8")
    page.close()
