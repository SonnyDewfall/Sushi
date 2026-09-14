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

A save bar (name input, save button, status readout) sits at root level,
outside the tabs container, so it's visible regardless of which plugin tab is
open. Root defaults to left-to-right flow and does not wrap on a 100%-width
child the way a real flexbox would — confirmed against real open-stage-
control: the tabs panel rendered at `x:1024`, entirely off a 1024px viewport,
instead of wrapping below the save bar. `"layout": "vertical"` on root fixes
it, stacking its children top-to-bottom instead.

The name input and the save button are **two independent messages, not one**.
The obvious design — carry the name via the button's `preArgs`, referencing
the input's live value with `@{...}` — was tried first and does not work:
verified with an isolated two-widget test panel and a raw OSC listener that
only ever printed the button's own tap value (`1.0`), never the input's text,
regardless of what was typed or how the value was committed. `preArgs`
apparently isn't re-evaluated per send for a button in this version, contrary
to what the advanced-syntax docs describe. Confirmed instead, with the same
isolated test: an `input` widget with its own `address`/`target` sends its
*own* value correctly (a real string) on blur/commit. So the input reports its
value directly to the listener at `NAME_ADDRESS`, which remembers it; the
button carries no payload at all and just triggers a save with whatever name
was last received. `target`/`ignoreDefaults` on both keeps every message
routed to the listener only, never at Sushi's own parameter port. `mode:
"tap"` on the button fires once per press; `toggle` would also fire a second
message on release.
"""

from __future__ import annotations

import sys
from typing import Any

from .dump import collect_parameter_info
from .listen import DEFAULT_LISTEN_PORT, NAME_ADDRESS, SAVE_ADDRESS, STATUS_ADDRESS
from .save import NAME_PATTERN

# A fader's value is always normalised 0-1; this is about the real-world
# domain that 0-1 maps onto. Above this ratio, linear drag response
# concentrates all musically useful values into a sliver of the fader's
# travel — see the module docstring's EQ-band-gain example.
LOG_SCALE_DOMAIN_THRESHOLD = 10.0

# The outer save-bar container needs to be taller than its own children's
# declared height — confirmed against real open-stage-control: each child
# widget renders with a ~30px offset above its own content (room for a label
# row, even on the "text" readout with label:false), so a container sized to
# exactly match its children's height clips them. 70/40 was the smallest gap
# that stopped the input/button/status from overflowing the bar visibly.
SAVE_BAR_HEIGHT = 70
SAVE_BAR_WIDGET_HEIGHT = 40

# A flat {type: "root", ...} file with no `version` field reads as version
# "0.0.0" to open-stage-control — below its lowest migration threshold — which
# pops a "session was created with an older version" warning on every load.
# That migration step is also what wraps a bare root object into the
# `{content: root}` shape the loader actually expects internally; declaring a
# `version` high enough to skip the warning skips that wrapping too, and the
# loader crashes on load ("Cannot read properties of undefined (reading
# 'type')") because `content` is never populated — confirmed on real
# open-stage-control, not inferred from source alone. So both parts of the
# fix are required together: declare `content` ourselves (see the wrapping
# return below) *and* declare `version` high enough to skip the warning.
# Bump this if the schema is re-verified against a newer open-stage-control
# release.
SCHEMA_VERSION = "1.31.1"


def build_osc_panel(
    dump: Any,
    live_info: dict[str, dict[str, dict]],
    listener_port: int = DEFAULT_LISTEN_PORT,
) -> dict[str, Any]:
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

            # A "." in a widget id breaks the "@{id}" live-value binding used
            # below (it reads as "undefined") — confirmed against real
            # open-stage-control on the EQ tab's "1.6K"/"2.5K" band-gain
            # params, the only ones with a "." in their name. `id` is purely
            # an internal open-stage-control reference — real OSC traffic
            # uses `address` below, untouched — so it's safe to sanitize.
            fader_id = f"{processor}/{param_name}".replace(".", "_")
            domain_max = live.get("max_domain_value", 1.0)
            # `fader` has no `label` property in real open-stage-control 1.31.1
            # — confirmed by inspecting a live widget's own resolved `props`,
            # which simply doesn't include the key, so a "label" here is
            # silently dropped rather than shown. The parameter name has to
            # go in the readout text below instead, which does render (its
            # `value` becomes visible content, not a caption prop) — the
            # only reason this looked fine before was that faders were never
            # actually checked for a visible label, only for the warning
            # dialog and the save bar.
            fader: dict[str, Any] = {
                "type": "fader",
                "id": fader_id,
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
                    "wrap": True,
                    "value": f"{param_name}\n@{{{fader_id}}}",
                    "interaction": False,
                    "width": 90,
                    "height": 40,
                }
            )
        if widgets:
            tabs.append({"type": "tab", "id": processor, "label": processor, "widgets": widgets})

    save_bar = {
        "type": "panel",
        "id": "save_bar",
        "width": "100%",
        "height": SAVE_BAR_HEIGHT,
        "widgets": [
            {
                "type": "input",
                "id": "config_name",
                "label": "name",
                "value": "",
                "validation": NAME_PATTERN.pattern,
                "address": NAME_ADDRESS,
                "target": [f"127.0.0.1:{listener_port}"],
                "ignoreDefaults": True,
                "width": 220,
                "height": SAVE_BAR_WIDGET_HEIGHT,
            },
            {
                "type": "button",
                "id": "save_button",
                "label": "SAVE",
                "mode": "tap",
                "address": SAVE_ADDRESS,
                "target": [f"127.0.0.1:{listener_port}"],
                "ignoreDefaults": True,
                "width": 90,
                "height": SAVE_BAR_WIDGET_HEIGHT,
            },
            {
                "type": "text",
                "id": "save_status",
                "label": False,
                "address": STATUS_ADDRESS,
                "interaction": False,
                "width": 320,
                "height": SAVE_BAR_WIDGET_HEIGHT,
            },
        ],
    }

    root = {
        "type": "root",
        "id": "sushi-rig",
        # Root defaults to left-to-right flow ("default" layout does not
        # wrap on a 100%-width child the way a real flexbox would — confirmed
        # against real open-stage-control: the tabs panel rendered at
        # x:1024, immediately to the right of the save bar, entirely off the
        # 1024px viewport, rather than wrapping below it). "vertical" stacks
        # root's own two children (save_bar, tabs) top-to-bottom instead.
        "layout": "vertical",
        "widgets": [
            save_bar,
            {
                "type": "panel",
                "id": "tabs",
                "width": "100%",
                "height": "100%",
                "tabs": tabs,
            },
        ],
    }

    # Wrapped under `content` alongside `version` — see SCHEMA_VERSION above
    # for why the flat root object can't be returned directly.
    return {"version": SCHEMA_VERSION, "content": root}
