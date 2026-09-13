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

A safe starting value isn't sufficient on its own for these same wide-domain
parameters — also found on real hardware. Sushi normalises every port
linearly (confirmed separately — it ignores the LV2 `pprops:logarithmic`
hint), so on a domain like a graphic EQ band's `[~0.016, ~63]`, the entire
musically useful ±12dB range occupies the bottom ~6% of the fader's linear
travel: barely anything happens for most of the drag, then a small movement
near the bottom swings wildly. `LOG_SCALE_DOMAIN_THRESHOLD` below controls
which faders get `logScale: true` to compensate.

`logScale` only changes how *drag distance* maps to the widget's own `value`
within its declared `range` (`{0, 1}` here) — confirmed by reading
open-stage-control's own client source (`mapToScale()` in `client/index.js`),
not just its docs, and then dragging two otherwise-identical faders the same
pixel distance: the plain one read back `0.25`, the `logScale` one `0.09` for
the same movement. The value Sushi receives is still a plain float in
`[0, 1]`, interpreted exactly as before — this only redistributes how much
drag corresponds to how much of that range, giving fine control near the
(usually near-zero) sane starting point and coarser control toward the
extreme.

Each fader is paired with a small read-only `text` widget showing its current
value, bound with the `@{widgetId}` live-reference syntax (verified against
real open-stage-control — a bare shared `id` between differently-typed
widgets, which the docs describe as an equivalent "clone" mechanism, was
tried first and did *not* visibly update; `@{...}` did). So the operator can
see what value they're actually about to send, not just feel it.
"""

from __future__ import annotations

import sys
from typing import Any

from .dump import collect_parameter_info

# A fader's value is always normalised 0-1; this is about the real-world
# domain that 0-1 maps onto. Above this ratio, linear drag response
# concentrates all musically useful values into a sliver of the fader's
# travel — see the module docstring's EQ-band-gain example.
LOG_SCALE_DOMAIN_THRESHOLD = 10.0


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

            fader_id = f"{processor}/{param_name}"
            domain_max = live.get("max_domain_value", 1.0)
            fader: dict[str, Any] = {
                "type": "fader",
                "id": fader_id,
                "label": param_name,
                "address": address,
                "range": {"min": 0, "max": 1},
                "default": round(live["value"], 6),
                "width": 90,
                "height": 220,
            }
            if domain_max >= LOG_SCALE_DOMAIN_THRESHOLD:
                fader["logScale"] = True
            widgets.append(fader)
            widgets.append(
                {
                    "type": "text",
                    "id": f"{fader_id}/readout",
                    "label": False,
                    "value": f"@{{{fader_id}}}",
                    "interaction": False,
                    "width": 90,
                    "height": 20,
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
