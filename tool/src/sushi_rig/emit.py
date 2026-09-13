"""rig.yaml (+ captured state) -> Sushi JSON config.

Refuses to emit on any spec problem (brief §6, emit.py). When `state` is
supplied, builds `initial_state` — never the `events` list, which the brief
(§2.4) says Sushi's own examples have moved away from since 1.3.0.

A processor present in captured state but absent from the spec means the spec
and the live session have diverged; that is surfaced as a warning and skipped,
never silently resolved.

`parameters` and `properties` inside each `initial_state` entry are **dicts**
of `{name: value}`, not a list of `{"name", "value"}` objects — confirmed
against Sushi's own shipped example configs (`usr/share/config_files/` inside
the AppImage), which is the authoritative source over the brief §2.4, whose
list-of-objects example does not match what Sushi 1.3.0 actually accepts. A
config built the list way fails to load with "Failed to load the initial
processor states." (exit 7), even when every parameter name and value is
otherwise correct — proven empirically before this was fixed.
"""

from __future__ import annotations

import sys
from typing import Any

from .spec import RigSpec


def emit(rig: RigSpec, state: dict[str, Any] | None = None) -> dict[str, Any]:
    problems = rig.validate()
    if problems:
        sys.exit("rig spec invalid:\n  " + "\n  ".join(problems))

    config: dict[str, Any] = {}
    if rig.meta:
        config["_meta"] = dict(rig.meta)
    config["host_config"] = dict(rig.host)
    config["tracks"] = [t.to_sushi() for t in rig.tracks]
    if rig.midi:
        config["midi"] = rig.midi
    if rig.osc:
        config["osc"] = rig.osc
    if rig.cv_control:
        config["cv_control"] = rig.cv_control

    if state:
        initial_state = _build_initial_state(rig, state)
        if initial_state:
            config["initial_state"] = initial_state

    return config


def _build_initial_state(rig: RigSpec, state: dict[str, Any]) -> list[dict[str, Any]]:
    known = set(rig.plugin_names()) | {t.name for t in rig.tracks}
    initial_state = []
    for processor, values in state.get("processors", {}).items():
        if processor not in known:
            print(
                f"warning: captured state for {processor!r}, which is not in the "
                "rig spec — skipping",
                file=sys.stderr,
            )
            continue

        entry: dict[str, Any] = {"processor": processor}
        if values.get("bypassed") is not None:
            entry["bypassed"] = values["bypassed"]
        if values.get("program") is not None:
            entry["program"] = values["program"]
        if values.get("parameters"):
            # Rounded to ~6dp: full float repr makes the diff unreadable and
            # the precision is meaningless against a normalised 0.0-1.0 range.
            entry["parameters"] = {
                name: round(float(val), 6) for name, val in values["parameters"].items()
            }
        if values.get("properties"):
            entry["properties"] = dict(values["properties"])
        initial_state.append(entry)

    return initial_state
