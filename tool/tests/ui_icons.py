"""The home-screen icons (#139), rasterised afresh from their SVGs.

Not a test: run it by hand after changing an icon's SVG, from `tool/`:

    PYTHONPATH=src python tests/ui_icons.py

`ui/static/icons/icon.svg` (a rounded square, for "any") and
`icon-maskable.svg` (full bleed, the knob inside the maskable safe circle)
are drawn by Chromium (the `ui-test` extra's), each at the size the manifest
and `index.html` name, on a transparent page, so the rounded square's corners
stay transparent. test_ui_install.py checks the files' sizes; it does not
redraw them, since another Chromium antialiases a pixel differently.
"""

from __future__ import annotations

from pathlib import Path

from playwright.sync_api import sync_playwright

from sushi_rig.ui import UI_DIR

ICONS = UI_DIR / "icons"
# (picture, source, size): what manifest.webmanifest and index.html name.
MADE = [
    ("icon-192.png", "icon.svg", 192),
    ("icon-512.png", "icon.svg", 512),
    ("icon-maskable-512.png", "icon-maskable.svg", 512),
    ("apple-touch-icon.png", "icon-maskable.svg", 180),  # iOS rounds the corners itself
]


def main() -> int:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        for name, source, size in MADE:
            page = browser.new_page(viewport={"width": size, "height": size})
            svg = (ICONS / source).read_text(encoding="utf-8").replace(
                "<svg ", f'<svg width="{size}" height="{size}" ', 1)
            page.set_content(f"<!doctype html><style>html,body{{margin:0;background:transparent}}"
                             f"svg{{display:block}}</style>{svg}")
            page.screenshot(path=str(ICONS / name), omit_background=True)
            page.close()
            print(f"{name}: {size} x {size} from {source}")
        browser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
