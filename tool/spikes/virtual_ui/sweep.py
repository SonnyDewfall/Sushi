#!/usr/bin/env python3
"""Sweep every control port of an LV2 plugin whose GUI is showing, and locate
each control on screen by diffing screenshots.

Assumes `carla_ui_host.py URI` is already running on $DISPLAY (run_sweep.sh
arranges all of that). For each control input port it:

  1. sets the parameter over Carla's OSC (the same message the rig's panel
     sends: /Carla/0/set_parameter_value <index> <value>),
  2. polls the window until the pixels change and then stop changing,
  3. keeps that frame, and
  4. diffs every frame against the all-defaults baseline.

The pixels that move when one parameter moves ARE that control. The union of
the diffs across several values is the control's footprint.

Output (--out DIR): rest.png, <symbol>_<step>.png frames, diff_<symbol>.png
(the footprint in red over the resting frame) and boxes.json.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image
from pythonosc.udp_client import SimpleUDPClient
from scipy import ndimage
from Xlib import X, display
from Xlib.error import BadMatch


# ---------------------------------------------------------------- ports ----

def lv2_control_ports(uri: str) -> list[dict]:
    """Control input ports in Carla's numbering.

    Carla numbers parameters over a plugin's control ports only, in port
    order (see tool/src/sushi_rig/amp.py). lv2info is the cheapest source of
    names and ranges that needs no plugin loading code of our own.
    """
    out = subprocess.run(["lv2info", uri], capture_output=True, text=True, check=True).stdout
    ports = []
    for block in re.split(r"\n\s*Port \d+:\n", out)[1:]:
        if "lv2core#ControlPort" not in block:
            continue
        field = lambda key: (re.search(rf"{key}:\s+(.*)", block) or [None, None])[1]  # noqa: E731
        ports.append(
            {
                "index": len(ports),
                "symbol": field("Symbol"),
                "name": field("Name"),
                "input": "lv2core#InputPort" in block,
                "min": float(field("Minimum") or 0),
                "max": float(field("Maximum") or 1),
                "default": float(field("Default") or 0),
                "toggled": "lv2core#toggled" in block,
                "enabled_designation": "lv2core#enabled" in block,
            }
        )
    return ports


# --------------------------------------------------------------- screen ----

class Screen:
    """Fast window capture over Xlib. ~1 ms for a 260x360 window, against
    ~47 ms for spawning ImageMagick's `import` (measured)."""

    def __init__(self, window_id: int):
        self.d = display.Display()
        self.win = self.d.create_resource_object("window", window_id)
        g = self.win.get_geometry()
        self.w, self.h = g.width, g.height

    def grab(self) -> np.ndarray:
        try:
            raw = self.win.get_image(0, 0, self.w, self.h, X.ZPixmap, 0xFFFFFFFF)
        except BadMatch:
            # The window was resized (or is not mapped yet) since we measured
            # it; asking for pixels outside it is a BadMatch. Measure again.
            g = self.win.get_geometry()
            self.w, self.h = g.width, g.height
            raw = self.win.get_image(0, 0, self.w, self.h, X.ZPixmap, 0xFFFFFFFF)
        a = np.frombuffer(raw.data, dtype=np.uint8).reshape(self.h, self.w, 4)
        return a[:, :, [2, 1, 0]].copy()  # BGRX -> RGB


def child_windows(window_id: int) -> list[dict]:
    """Sub-windows of the plugin window, in window-local coordinates.

    GTK2 (guitarix) gives each knob its own X window, which is an independent
    check on the diff boxes. Toolkits that draw everything into one surface
    (DPF/OpenGL) have none, so this is a cross-check, not a method.
    """
    d = display.Display()
    out = []

    def walk(w, ox, oy):
        for c in w.query_tree().children:
            g = c.get_geometry()
            if (g.width, g.height) != (1, 1):
                out.append({"x": ox + g.x, "y": oy + g.y, "w": g.width, "h": g.height})
            walk(c, ox + g.x, oy + g.y)

    walk(d.create_resource_object("window", window_id), 0, 0)
    return out


def find_window(name_re: str, timeout: float = 20.0) -> int:
    """The id of the top-level window whose title matches `name_re`.

    No window manager runs on the display, so top-level windows are direct
    children of the root.
    """
    d = display.Display()
    deadline = time.time() + timeout
    while time.time() < deadline:
        for w in d.screen().root.query_tree().children:
            try:
                n = w.get_wm_name()
            except Exception:
                continue
            if n and re.search(name_re, n):
                return w.id
        time.sleep(0.1)
    raise SystemExit(f"no window matching {name_re!r} appeared within {timeout}s")


# ---------------------------------------------------------------- sweep ----

def settle(screen: Screen, before: np.ndarray, timeout: float, quiet: float = 0.08):
    """Wait for the GUI to redraw after a change.

    Returns (frame, t_first_change, t_last_change); the times are None if
    nothing changed within `timeout`, which is itself a finding (the GUI did
    not follow, or the value was already showing). We keep polling until no
    pixel has moved for `quiet` seconds, because a redraw may arrive in more
    than one paint.
    """
    t0 = time.perf_counter()
    last, last_change = before, None
    first = None
    while True:
        now = time.perf_counter() - t0
        f = screen.grab()
        if not np.array_equal(f, last):
            if first is None:
                first = now
            last, last_change = f, now
        elif last_change is not None and now - last_change >= quiet:
            return last, first, last_change
        if now > timeout:
            return last, first, last_change
        time.sleep(0.004)


def footprint(frames: list[np.ndarray], base: np.ndarray, thresh: int) -> np.ndarray:
    mask = np.zeros(base.shape[:2], bool)
    for f in frames:
        mask |= np.abs(f.astype(int) - base.astype(int)).max(axis=2) > thresh
    return mask


def boxes_of(mask: np.ndarray, merge: int) -> list[dict]:
    """Connected clusters of changed pixels, merged across gaps of `merge` px
    (a knob pointer sweeping an arc is not one connected blob)."""
    grown = ndimage.binary_dilation(mask, iterations=merge)
    labels, n = ndimage.label(grown)
    out = []
    for i, sl in enumerate(ndimage.find_objects(labels), start=1):
        ys, xs = sl
        px = int((mask & (labels == i)).sum())
        if px:
            # tight box: shrink back to the actual changed pixels
            yy, xx = np.nonzero(mask & (labels == i))
            out.append(
                {"x": int(xx.min()), "y": int(yy.min()),
                 "w": int(xx.max() - xx.min() + 1), "h": int(yy.max() - yy.min() + 1),
                 "pixels": px,
                 # Centre of the footprint. For a knob swept through its range
                 # this is the knob's centre; half the extent is the radius of
                 # the pointer's PATH, not of the knob body.
                 "cx": round(float((xx.min() + xx.max()) / 2), 1),
                 "cy": round(float((yy.min() + yy.max()) / 2), 1)}
            )
    return sorted(out, key=lambda b: -b["pixels"])


def save_diff(path: Path, base: np.ndarray, mask: np.ndarray, boxes: list[dict]) -> None:
    img = (base * 0.35 + 255 * 0.65).astype(np.uint8)  # washed-out resting frame
    img[mask] = (255, 0, 0)
    im = Image.fromarray(img)
    from PIL import ImageDraw
    dr = ImageDraw.Draw(im)
    for b in boxes[:1]:
        dr.rectangle([b["x"] - 2, b["y"] - 2, b["x"] + b["w"] + 1, b["y"] + b["h"] + 1], outline=(0, 90, 255))
    im.save(path)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("uri")
    ap.add_argument("--window", default=r"\(GUI\)", help="regex on the window title")
    ap.add_argument("--osc-port", type=int, default=22752)
    ap.add_argument("--out", type=Path, default=Path("sweep-out"))
    # 25, not 9: guitarix's Level knob moves its pointer non-linearly with the
    # value, and at 9 steps the footprint missed the whole right-hand side.
    ap.add_argument("--steps", type=int, default=25, help="values per control, min..max inclusive")
    ap.add_argument("--timeout", type=float, default=1.5, help="max wait for a redraw, seconds")
    ap.add_argument("--thresh", type=int, default=24, help="per-channel change that counts")
    ap.add_argument("--merge", type=int, default=14, help="px gap that still joins pixels into one cluster")
    ap.add_argument("--skip", default="", help="comma list of port symbols to skip")
    args = ap.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    t_start = time.perf_counter()
    ports = [p for p in lv2_control_ports(args.uri) if p["input"]]
    skip = set(filter(None, args.skip.split(",")))
    osc = SimpleUDPClient("127.0.0.1", args.osc_port)

    def send(p, v):
        osc.send_message("/Carla/0/set_parameter_value", [p["index"], float(v)])

    wid = find_window(args.window)
    screen = Screen(wid)
    print(f"window 0x{wid:x} {screen.w}x{screen.h}")

    # Resting state: everything at its default, GUI quiet.
    for p in ports:
        send(p, p["default"])
    time.sleep(0.5)
    base = screen.grab()
    Image.fromarray(base).save(args.out / "rest.png")

    report = {"uri": args.uri, "window": {"id": hex(wid), "w": screen.w, "h": screen.h},
              "x_child_windows": child_windows(wid), "controls": []}
    for p in ports:
        if p["symbol"] in skip:
            continue
        values = [p["min"] + (p["max"] - p["min"]) * i / (args.steps - 1) for i in range(args.steps)]
        if p["toggled"]:
            values = [p["min"], p["max"]]
        t_ctl = time.perf_counter()
        frames, first_ms, last_ms = [], [], []
        cur = base
        for i, v in enumerate(values):
            send(p, v)
            frame, t1, t2 = settle(screen, cur, args.timeout)
            if t1 is not None:
                first_ms.append(round(t1 * 1000))
                last_ms.append(round(t2 * 1000))
            frames.append(frame)
            Image.fromarray(frame).save(args.out / f"{p['symbol']}_{i}.png")
            cur = frame
        send(p, p["default"])
        settle(screen, cur, args.timeout)  # put it back; the next control diffs against `base`
        mask = footprint(frames, base, args.thresh)
        boxes = boxes_of(mask, args.merge)
        save_diff(args.out / f"diff_{p['symbol']}.png", base, mask, boxes)
        entry = {
            "symbol": p["symbol"], "name": p["name"], "carla_index": p["index"],
            "values": [round(v, 4) for v in values],
            "changed_pixels": int(mask.sum()),
            "box": boxes[0] if boxes else None,
            "extra_boxes": boxes[1:],
            "redraw_first_change_ms": first_ms,
            "redraw_last_change_ms": last_ms,
            "seconds": round(time.perf_counter() - t_ctl, 2),
        }
        report["controls"].append(entry)
        b = entry["box"]
        print(f"{p['symbol']:<10} {p['name']:<8} "
              + (f"box x={b['x']} y={b['y']} w={b['w']} h={b['h']} centre=({b['cx']},{b['cy']}) ({entry['changed_pixels']} px)" if b else "NO CHANGE")
              + f"  redraw first/last-change ms: {first_ms[:1]}/{last_ms[:1]}"
              + (f"  [+{len(boxes) - 1} more clusters]" if len(boxes) > 1 else ""))
    lat = sorted(ms for c in report["controls"] for ms in c["redraw_first_change_ms"])
    if lat:
        report["redraw_ms"] = {"n": len(lat), "min": lat[0], "median": lat[len(lat) // 2],
                               "p95": lat[int(len(lat) * 0.95)], "max": lat[-1]}
        print("redraw latency (OSC send -> first changed frame), ms:", report["redraw_ms"])
    report["sweep_seconds"] = round(time.perf_counter() - t_start, 2)
    (args.out / "boxes.json").write_text(json.dumps(report, indent=2))
    print(f"sweep took {report['sweep_seconds']}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
