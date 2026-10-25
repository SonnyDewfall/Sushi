#!/usr/bin/env python3
"""Approach (a): jalv.gtk3 on JACK under Xvfb. Records what it can and cannot do.

Two experiments against the TS9, on an already-running Xvfb and JACK:

  1. Runtime control. jalv.gtk3 is started with stdin on a FIFO and given
     `fslider2_ = 1.0` (the console syntax of plain `jalv`). Result: the GUI
     does not change by a single pixel. The GUI build has no console reader
     (the strings in the binary have none, and `jalv.gtk3 --help` lists only
     `-c/--control` which applies at start-up).
  2. Start-up control. `-c fslider2_=1.0` DOES change the GUI, so one state can
     be shown per jalv.gtk3 process. The time to bring one up is printed.

Usage: DISPLAY=:99 try_jalv.py
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sweep import Screen, find_window  # noqa: E402

URI = "http://guitarix.sourceforge.net/plugins/gxts9#ts9sim"


def changed(a: np.ndarray, b: np.ndarray) -> int:
    return int((np.abs(a.astype(int) - b.astype(int)).max(axis=2) > 24).sum())


def start(extra: list[str], stdin) -> tuple[subprocess.Popen, Screen, float]:
    t0 = time.perf_counter()
    p = subprocess.Popen(["jalv.gtk3", "-m", "-n", "ts9j", *extra, URI],
                         stdin=stdin, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    s = Screen(find_window("GxTubeScreamer", 15))
    while s.h < 100:  # the window exists before GTK has sized it
        time.sleep(0.02)
        s = Screen(find_window("GxTubeScreamer", 15))
    # A -c value reaches the GUI a moment after the window appears: at 0.3 s
    # the first run of this script saw no change at all. 2 s is comfortable.
    time.sleep(2.0)
    return p, s, time.perf_counter() - t0


def main() -> int:
    # 1. runtime control over stdin
    fifo = os.path.join(tempfile.mkdtemp(), "ctl")
    os.mkfifo(fifo)
    keep = os.open(fifo, os.O_RDWR)  # hold the FIFO open so the reader never sees EOF
    p, s, dt = start([], open(fifo))
    before = s.grab()
    for line in ("fslider2_ = 1.0\n", "fslider2_=0.0\n", "fslider0_ = 4.0\n"):
        os.write(keep, line.encode())
        time.sleep(1.0)
    after = s.grab()
    print(f"stdin console: {changed(before, after)} pixels changed after 3 commands (window {s.w}x{s.h})")
    p.terminate(); p.wait(); os.close(keep)

    # 2. start-up control
    p, s, dt0 = start([], subprocess.DEVNULL)
    rest = s.grab()
    p.terminate(); p.wait()
    p, s, dt1 = start(["-c", "fslider2_=1.0"], subprocess.DEVNULL)
    drive = s.grab()
    p.terminate(); p.wait()
    print(f"-c at start-up: {changed(rest, drive)} pixels changed; one process up and showing the value in {dt0:.2f}s / {dt1:.2f}s (incl. a 2 s settle)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
