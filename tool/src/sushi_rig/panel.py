"""Generate an Open Stage Control panel from a `--dump-plugins` dump.

One tab per processor, one fader per parameter. Addresses come from each
parameter's `osc_path` in the dump, used verbatim — Sushi replaces spaces with
underscores in these paths, so constructing an address from the parameter name
instead would silently produce one that never matches.
"""

from __future__ import annotations

from typing import Any

from .dump import collect_parameter_info


def build_osc_panel(dump: Any, osc_port: int = 24024) -> dict[str, Any]:
    """Build a tabbed Open Stage Control panel structure: one tab per processor."""
    tabs = []
    for processor, params in sorted(collect_parameter_info(dump).items()):
        widgets = []
        for i, (param_name, info) in enumerate(sorted(params.items())):
            address = info.get("osc_path")
            if not address:
                # No osc_path in the dump for this parameter — skip rather than
                # guess at an address that may not match what Sushi listens on.
                continue
            widgets.append(
                {
                    "type": "fader",
                    "id": f"{processor}/{param_name}",
                    "label": param_name,
                    "address": address,
                    "range": {"min": 0, "max": 1},
                    "default": 0.5,
                    "width": 90,
                    "height": 260,
                    "left": 10 + (i % 8) * 100,
                    "top": 10 + (i // 8) * 280,
                }
            )
        if widgets:
            tabs.append(
                {"type": "tab", "id": processor, "label": processor, "widgets": widgets}
            )

    return {
        "type": "root",
        "id": "sushi-rig",
        "sendPort": str(osc_port),
        "widgets": [{"type": "panel", "id": "tabs", "tabs": tabs}],
    }
