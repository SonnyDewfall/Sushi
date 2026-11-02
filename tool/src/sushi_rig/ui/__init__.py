"""The web UI, v0 (#31): the rack (S1) and the stage screen (S5).

The agent serves the files in `static/` at `/` and `/ui/`, and the page
reads and changes the rig through the agent's own routes, nothing else.

## Plain files, no build step and no framework

HTML, CSS and JavaScript modules, written as they are served. The rig machine
has no Node, and the UI must run from a checkout: a `git pull` and an agent
restart are the whole deployment, with nothing to compile, install or keep in
step. A framework would also be a moving part the size of the rest of the UI
(principle 3), and the screens are small enough to do without: two screens, a
poll, a few kinds of control. The fonts the mockups name are not bundled yet,
so the page falls back to the system's serif, sans and mono, and needs no
network (the UX doc's rule 6).

## Where the decisions are

Decisions are pure and tested (principle 1), in two places:

- here, `panel_profile`: what a profile says about each control, flattened
  for the browser and checked by pytest without one;
- `static/rig.js`: everything decided from what the agent answers -- a value in real
  units, which controls a card shows, what is unsaved, the rig's state line,
  whether the lock refuses an edit. No DOM in it, so `test_ui.py` runs its
  functions in a bare page and asserts on what they return.

`static/app.js` is the DOM and the network: polling, sending, drawing.

## What the page reads

    GET /health        the agent and the rig: up or down, uptime, the last error, MIDI
    GET /live          once a second: tracks, chains, values, ranges, bypass
    GET /live/log      once a second: what is unsaved, and what undo would take back
    GET /live/plugins  which plugin each processor is, when the processors change
    GET /profiles      the profiles, flattened by `panel_profile`, once

and sends POST /live/parameter, /live/bypass, /live/move and /live/undo as
they happen. A refusal's `reason` is shown on screen; nothing is reverted
without saying why.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

#: The folder the agent serves: the page's files and nothing else, so this
#: module's own source is not among them.
UI_DIR = Path(__file__).parent / "static"

# Judgements the panel reads, and the facts it needs to show a value.
_JUDGEMENTS = ("visibility", "group", "order", "label", "description", "scale", "control", "phone")
_FACTS = ("min", "max", "unit", "integer", "toggled", "enumeration", "scale_points")


def _judged(entry: dict[str, Any], key: str) -> Any:
    """A judgement's value: `{value, provenance}` in the file, the value here.
    Provenance is for review, not for the panel."""
    judgement = entry.get(key)
    return judgement.get("value") if isinstance(judgement, dict) else None


def panel_profile(profile: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """A profile as the browser draws it, and the problems met on the way.

    `{"uri", "name", "file", "groups": [{"id", "label"}], "parameters": [...]}`,
    each parameter `{"name", "label", "group", "order", "visibility", "scale",
    "control", "phone", "description", "unit", "integer", "toggled",
    "enumeration", "scale_points", "min", "max"}`, sorted as the panel lays
    them out: by their group's place in `groups`, then `order`, then the file's
    order. `name` is the one Sushi uses for the parameter (the profile's `name`
    is the port's `lv2:name`, which is how Sushi addresses it: profile.py).

    The profiles are loaded, not validated (validation needs probe's facts,
    which need lilv), so this takes nothing on trust. A parameter that is not
    a mapping with a name is left out and named in the problems. One with no
    `visibility` is shown as primary: hiding a control on a guess is the worse
    mistake, since a hidden control cannot be found to be wrong. A group a
    parameter names that `groups` does not define goes last, under its id.
    Pure.
    """
    problems: list[str] = []
    where = profile.get("_file") or (profile.get("plugin") or {}).get("uri") or "a profile"
    plugin = profile.get("plugin") if isinstance(profile.get("plugin"), dict) else {}
    groups = []
    for group in profile.get("groups") or []:
        if isinstance(group, dict) and isinstance(group.get("id"), str):
            groups.append({"id": group["id"], "label": group.get("label") or group["id"]})
        else:
            problems.append(f"{where}: a group with no id is left out: {group!r}")
    rank = {group["id"]: i for i, group in enumerate(groups)}

    parameters = []
    for position, entry in enumerate(profile.get("parameters") or []):
        if not isinstance(entry, dict) or not isinstance(entry.get("name"), str) or not entry["name"]:
            problems.append(f"{where}: parameter {position + 1} has no name and is left out")
            continue
        facts = entry.get("facts") if isinstance(entry.get("facts"), dict) else {}
        flat: dict[str, Any] = {"name": entry["name"]}
        for key in _JUDGEMENTS:
            flat[key] = _judged(entry, key)
        flat["label"] = flat["label"] or entry["name"]
        flat["visibility"] = flat["visibility"] or "primary"
        for key in _FACTS:
            flat[key] = facts.get(key)
        group = flat["group"]
        if group is not None and group not in rank:
            problems.append(f"{where}: {entry['name']} names group {group!r}, which groups does not define")
            groups.append({"id": group, "label": group})
            rank[group] = len(groups) - 1
        order = flat["order"]
        sort_key = (
            rank.get(group, len(groups) + 1) if group is not None else len(groups) + 1,
            order if isinstance(order, (int, float)) and not isinstance(order, bool) else float("inf"),
            position,
        )
        parameters.append((sort_key, flat))
    parameters.sort(key=lambda pair: pair[0])
    return {
        "uri": plugin.get("uri"),
        "name": plugin.get("name"),
        "file": profile.get("_file"),
        "groups": groups,
        "parameters": [flat for _, flat in parameters],
    }, problems


def panel_profiles(profiles: dict[str, dict[str, Any]], problems: list[str]) -> dict[str, Any]:
    """`/profiles`'s answer from `library.load_profiles`'s: every profile by
    URI, flattened, and every problem, the loader's first. Pure."""
    out: dict[str, Any] = {}
    problems = list(problems)
    for uri, profile in profiles.items():
        flat, found = panel_profile(profile)
        out[uri] = flat
        problems += found
    return {"profiles": out, "problems": problems}
