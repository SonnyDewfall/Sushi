#!/usr/bin/env python3
"""Intern the X atoms Carla's UI bridge expects to already exist.

Why: a bare Xvfb has no window manager, and a window manager is normally what
creates `UTF8_STRING` and `WM_DELETE_WINDOW`. carla-bridge-lv2-x11 asks for
them with XInternAtom(only_if_exists=True), gets 0 back, and passes 0 to
XChangeProperty, which the server rejects:

    X Error of failed request:  BadAtom (invalid Atom parameter)
      Major opcode of failed request:  18 (X_ChangeProperty)

The window is then never created. `carla-single` dies the same way. Found by
LD_PRELOADing a shim over XInternAtom and reading what it was asked for.

Interning them from any client fixes it, but atoms vanish when the X server
resets, which Xvfb does when its last client disconnects. So Xvfb MUST be
started with -noreset (run_sweep.sh does).

Usage: DISPLAY=:99 prime_x.py
"""

from Xlib import display

ATOMS = [
    "UTF8_STRING", "WM_DELETE_WINDOW", "WM_PROTOCOLS",
    "_NET_WM_NAME", "_NET_WM_PID", "_NET_WM_ICON",
    "_NET_WM_WINDOW_TYPE", "_NET_WM_WINDOW_TYPE_NORMAL", "_NET_WM_WINDOW_TYPE_DIALOG",
]

d = display.Display()
for name in ATOMS:
    d.intern_atom(name, False)
d.sync()
print("interned", len(ATOMS), "atoms")
