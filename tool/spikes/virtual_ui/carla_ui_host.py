#!/usr/bin/env python3.12
"""Host one LV2 plugin in Carla, with its own GUI shown, and serve OSC.

Run with the SYSTEM python (3.12 on Ubuntu 24.04), not /usr/local/bin/python3:
Carla's Python bindings are the distro's, and the 3.11 in /usr/local has no
PyQt5.sip -- but this script only needs `carla_backend`, which is plain ctypes,
so either works. (`carla` the launcher script DOES need the system python.)

Why this exists instead of `carla -n project.carxp` (which is how the rig runs
the amp): `carla -n` never shows plugin GUIs, and there is no OSC message that
does. The full Carla GUI under Xvfb hangs on an "Action in progress" modal. The
engine in libcarla_standalone2.so, driven from Python, has
`show_custom_ui(plugin, True)`, and it still serves the same OSC protocol, so
the sweep script talks to it exactly as the rig talks to production Carla.

Usage:
    carla_ui_host.py URI [--osc-port 22752] [--client-name NAME]

Prints `READY <n-params>` once the GUI has been asked for, then idles until
SIGTERM/SIGINT. Needs DISPLAY, a running JACK server, and Xvfb started with
-noreset plus the atoms from `prime_x.py` (see FINDINGS.md).
"""

from __future__ import annotations

import argparse
import select
import signal
import sys
import time

sys.path.insert(0, "/usr/share/carla")
from carla_backend import (  # noqa: E402
    BINARY_NATIVE,
    ENGINE_OPTION_OSC_ENABLED,
    ENGINE_OPTION_OSC_PORT_TCP,
    ENGINE_OPTION_OSC_PORT_UDP,
    ENGINE_OPTION_PATH_BINARIES,
    ENGINE_OPTION_PATH_RESOURCES,
    ENGINE_OPTION_PREFER_UI_BRIDGES,
    PLUGIN_LV2,
    CarlaHostDLL,
)

LIB = "/usr/lib/carla/libcarla_standalone2.so"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("uri")
    ap.add_argument("--osc-port", type=int, default=22752)
    ap.add_argument("--client-name", default="Carla")
    args = ap.parse_args()

    host = CarlaHostDLL(LIB, False)
    host.set_engine_option(ENGINE_OPTION_PATH_BINARIES, 0, "/usr/lib/carla")
    host.set_engine_option(ENGINE_OPTION_PATH_RESOURCES, 0, "/usr/share/carla/resources")
    # Run the GUI in the separate carla-bridge-lv2-x11 process. It is what
    # puts the plugin's own X11 window on the display.
    host.set_engine_option(ENGINE_OPTION_PREFER_UI_BRIDGES, 1, "")
    host.set_engine_option(ENGINE_OPTION_OSC_ENABLED, 1, "")
    host.set_engine_option(ENGINE_OPTION_OSC_PORT_TCP, args.osc_port, "")
    host.set_engine_option(ENGINE_OPTION_OSC_PORT_UDP, args.osc_port, "")

    if not host.engine_init("JACK", args.client_name):
        print("FAIL engine_init:", host.get_last_error(), flush=True)
        return 1
    if not host.add_plugin(BINARY_NATIVE, PLUGIN_LV2, "", "", args.uri, 0, None, 0):
        print("FAIL add_plugin:", host.get_last_error(), flush=True)
        host.engine_close()
        return 1
    host.show_custom_ui(0, True)
    print(f"READY {host.get_parameter_count(0)}", flush=True)

    stop = False

    def on_signal(*_):
        nonlocal stop
        stop = True

    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)
    # engine_idle() is what pumps the UI bridge's pipe; without it the window
    # is created but parameter changes never reach it.
    stdin_open = True
    while not stop:
        host.engine_idle()
        # Optional in-process control on stdin, for comparing against OSC:
        # "set <index> <value>" or "get <index>".
        if not stdin_open:
            time.sleep(0.01)
            continue
        if select.select([sys.stdin], [], [], 0.01)[0]:
            line = sys.stdin.readline()
            if not line:
                stdin_open = False  # EOF (e.g. </dev/null): keep hosting
                continue
            parts = line.split()
            if parts[:1] == ["set"] and len(parts) == 3:
                host.set_parameter_value(0, int(parts[1]), float(parts[2]))
            elif parts[:1] == ["get"] and len(parts) == 2:
                print(host.get_current_parameter_value(0, int(parts[1])), flush=True)
    host.engine_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
