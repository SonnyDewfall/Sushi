"""Generate an Open Stage Control panel from a `--dump-plugins` dump.

One tab per processor, one fader per parameter. Addresses come from each
parameter's `osc_path` in the dump, used verbatim — Sushi replaces spaces with
underscores in these paths, so constructing an address from the parameter name
instead would silently produce one that never matches.

The session schema here was verified against real open-stage-control 1.31.1
(loaded into it directly, not inferred from docs alone): `root`, `panel` and
`tab` are all containers taking a `widgets` array (`root`/`panel` can use
`tabs` instead); `fader` takes `range`, `default`, `label`, `address`. There is
no `sendPort` property on `root` — the prototype this was ported from invented
it; the actual send target is set via open-stage-control's own `--send`
CLI flag (`ip:port`) at launch time, not from the session file. Manual
`left`/`top` pixel placement on every fader (also inherited from the
prototype) rendered as a tiny, near-unusable panel with no visible size on the
containers.

`layout: "grid"` on a container collapses every fader inside it down to a
sliver — confirmed by isolating a single fader (renders correctly at its set
height on its own) from the same fader inside a `layout: "grid"` container
(collapses). `layout: "default"` (plain flow — also the documented default,
so it can simply be omitted) wraps same-width widgets left-to-right, top-to-
bottom exactly like a flexbox, and was confirmed to render two side-by-side
faders at their full set height. That's what's used here instead.

`live_info` (from `live.get_live_parameter_info`) is required, not optional.
An earlier version hardcoded every fader's starting value to `0.5` normalised,
which is catastrophic for the many LSP parameters that are linear amplitude
multipliers with wide domains — every graphic EQ band gain (domain up to
`~63`), and `Input gain`/`Output gain`/`Makeup gain` on the compressor and
chorus (domain up to `1000`) all have their real default sitting near the
*bottom* of the range. `0.5` normalised there is `500x` amplification: a
fader that looks like a normal, safe control instantly clips the moment its
value is touched or sent. Confirmed on real hardware, not just in theory.
`live_info` also carries `automatable`, so read-only meter/visibility
parameters are dropped from the panel entirely rather than presented as
draggable controls that do nothing useful and, as above, might not be so
harmless to send a value to.
"""

from __future__ import annotations

import sys
from typing import Any

from .dump import collect_parameter_info


def build_osc_panel(dump: Any, live_info: dict[str, dict[str, dict]]) -> dict[str, Any]:
    """Build a tabbed Open Stage Control panel structure: one tab per processor."""
    tabs = []
    for processor, params in sorted(collect_parameter_info(dump).items()):
        live_params = live_info.get(processor)
        if live_params is None:
            print(
                f"warning: {processor!r} not found in the live rig — skipping its tab. "
                "Is the panel being generated against the same config that's running?",
                file=sys.stderr,
            )
            continue

        widgets = []
        for param_name, info in sorted(params.items()):
            address = info.get("osc_path")
            if not address:
                # No osc_path in the dump for this parameter — skip rather than
                # guess at an address that may not match what Sushi listens on.
                continue
            live = live_params.get(param_name)
            if live is None:
                print(
                    f"warning: {processor}.{param_name!r} not found live — skipping",
                    file=sys.stderr,
                )
                continue
            if not live["automatable"]:
                # Read-only (meters, "Latency OUT") — not a control to expose.
                continue
            widgets.append(
                {
                    "type": "fader",
                    "id": f"{processor}/{param_name}",
                    "label": param_name,
                    "address": address,
                    "range": {"min": 0, "max": 1},
                    "default": round(live["value"], 6),
                    "width": 90,
                    "height": 220,
                }
            )
        if widgets:
            tabs.append({"type": "tab", "id": processor, "label": processor, "widgets": widgets})

    return {
        "type": "root",
        "id": "sushi-rig",
        "widgets": [
            {
                "type": "panel",
                "id": "tabs",
                "width": "100%",
                "height": "100%",
                "tabs": tabs,
            }
        ],
    }
