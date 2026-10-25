#!/usr/bin/env python3
"""Draw every located control from a sweep onto the resting screenshot.

Usage: overlay.py SWEEP_DIR OUT.png [--palette]

Reads SWEEP_DIR/rest.png and SWEEP_DIR/boxes.json (written by sweep.py), draws
each control's box and centre, and labels it with its port name. --palette
quantises to 64 colours so a GL screenshot stays small enough to commit.
"""

import argparse
import json
from pathlib import Path

from PIL import Image, ImageDraw

ap = argparse.ArgumentParser()
ap.add_argument("sweep_dir", type=Path)
ap.add_argument("out", type=Path)
ap.add_argument("--palette", action="store_true")
args = ap.parse_args()

report = json.loads((args.sweep_dir / "boxes.json").read_text())
im = Image.open(args.sweep_dir / "rest.png").convert("RGB")
d = ImageDraw.Draw(im)
colours = [(255, 60, 60), (255, 200, 0), (0, 220, 255), (255, 0, 255), (120, 255, 80), (255, 140, 0)]
for i, c in enumerate(report["controls"]):
    b = c["box"]
    if not b:
        continue
    col = colours[i % len(colours)]
    d.rectangle([b["x"] - 1, b["y"] - 1, b["x"] + b["w"], b["y"] + b["h"]], outline=col)
    cx, cy = b["cx"], b["cy"]
    d.line([cx - 3, cy, cx + 3, cy], fill=col)
    d.line([cx, cy - 3, cx, cy + 3], fill=col)
    d.text((b["x"], max(0, b["y"] - 11)), c["name"], fill=col)
if args.palette:
    im = im.quantize(64)
im.save(args.out, optimize=True)
print("wrote", args.out)
