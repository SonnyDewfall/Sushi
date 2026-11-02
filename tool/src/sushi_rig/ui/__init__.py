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

    GET /health        once a second: up or down, which rig since when, the last error, MIDI
    GET /live          once a second: tracks, chains, values, ranges, bypass
    GET /live/log      once a second: what is unsaved, and what undo would take back
    GET /live/plugins  which plugin each processor is: again whenever the processors'
                       names change or the rig does (its name, its start, a rig
                       switch in the log), since a rig of the same names can hold
                       another plugin
    GET /profiles      the profiles, flattened by `panel_profile`: beside the poll,
                       never holding it up, until it answers (asked again every
                       10 s while it fails; a profile that would not load is a
                       problem shown, not a failure)
    GET /library       the plugins' names, the same way

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

# Judgements the panel reads (`order` only to sort, here), and the facts the
# browser uses. Not the range: the browser takes min and max from Sushi's
# parameter_info, which is what Sushi normalises over, so a profile's
# restated range would be a second answer it does not honour, and is not sent.
_JUDGEMENTS = ("visibility", "group", "order", "label", "description", "scale", "control", "phone")
_SENT = ("label", "group", "visibility", "scale", "control", "phone", "description")
_FACTS = ("unit", "integer", "toggled", "scale_points")


class PanelError(ValueError):
    """A profile the panel cannot lay out at all: `panel_profiles` reports it
    by file and serves the others."""


def _judged(entry: dict[str, Any], key: str) -> Any:
    """A judgement's value: `{value, provenance}` in the file, the value here.
    Provenance is for review, not for the panel."""
    judgement = entry.get(key)
    return judgement.get("value") if isinstance(judgement, dict) else None


def panel_profile(profile: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """A profile as the browser draws it, and the problems met on the way.

    `{"uri", "name", "file", "groups": [{"id", "label"}], "parameters": [...]}`,
    each parameter `{"name", "label", "group", "visibility", "scale",
    "control", "phone", "description", "unit", "integer", "toggled",
    "scale_points"}`, sorted as the panel lays them out: by their group's
    place in `groups` (ungrouped last), then `order`, then the file's order.
    `name` is the one Sushi uses for the parameter (the profile's `name` is the
    port's `lv2:name`, which is how Sushi addresses it: profile.py). No range:
    see `_FACTS`.

    The profiles are loaded, not validated (validation needs probe's facts,
    which need lilv), so this takes nothing on trust. A parameter that is not
    a mapping with a name is left out and named in the problems. One with no
    `visibility` is shown as primary: hiding a control on a guess is the worse
    mistake, since a hidden control cannot be found to be wrong. A group a
    parameter names that `groups` does not define goes after the defined ones,
    under its id. A group that is not a name at all is `PanelError`: the
    layout cannot be decided, and the whole profile is reported, not guessed.
    Pure.
    """
    problems: list[str] = []
    plugin = profile.get("plugin") if isinstance(profile.get("plugin"), dict) else {}
    where = profile.get("_file") or plugin.get("uri") or "a profile"
    for key in ("groups", "parameters"):
        if profile.get(key) is not None and not isinstance(profile.get(key), list):
            raise PanelError(f"{where}: {key} is not a list: {profile.get(key)!r}")
    groups = []
    for group in profile.get("groups") or []:
        if isinstance(group, dict) and isinstance(group.get("id"), str):
            groups.append({"id": group["id"], "label": group.get("label") or group["id"]})
        else:
            problems.append(f"{where}: a group with no id is left out: {group!r}")
    rank = {group["id"]: i for i, group in enumerate(groups)}

    placed = []
    for position, entry in enumerate(profile.get("parameters") or []):
        if not isinstance(entry, dict) or not isinstance(entry.get("name"), str) or not entry["name"]:
            problems.append(f"{where}: parameter {position + 1} has no name and is left out")
            continue
        facts = entry.get("facts") if isinstance(entry.get("facts"), dict) else {}
        judged = {key: _judged(entry, key) for key in _JUDGEMENTS}
        group = judged["group"]
        if group is not None and not isinstance(group, str):
            raise PanelError(f"{where}: {entry['name']}'s group is not a name: {group!r}")
        if group is not None and group not in rank:
            problems.append(f"{where}: {entry['name']} names group {group!r}, which groups does not define")
            groups.append({"id": group, "label": group})
            rank[group] = len(groups) - 1
        flat: dict[str, Any] = {"name": entry["name"], **{key: judged[key] for key in _SENT}}
        flat["label"] = flat["label"] or entry["name"]
        flat["visibility"] = flat["visibility"] or "primary"
        for key in _FACTS:
            flat[key] = facts.get(key)
        order = judged["order"]
        order = order if isinstance(order, (int, float)) and not isinstance(order, bool) else float("inf")
        placed.append((group, order, position, flat))
    # Ranked once every group is known: an undefined one met later extends
    # `groups`, and the ungrouped go after all of them.
    placed.sort(key=lambda p: (rank[p[0]] if p[0] is not None else len(groups), p[1], p[2]))
    return {
        "uri": plugin.get("uri"),
        "name": plugin.get("name"),
        "file": profile.get("_file"),
        "groups": groups,
        "parameters": [flat for *_, flat in placed],
    }, problems


def panel_profiles(profiles: dict[str, dict[str, Any]], problems: list[str]) -> dict[str, Any]:
    """`/profiles`'s answer from `library.load_profiles`'s: every profile by
    URI, flattened, and every problem, the loader's first. A profile that
    cannot be laid out is a problem naming its file, and the rest are served:
    one bad file must not take every card's profile with it. Pure."""
    out: dict[str, Any] = {}
    problems = list(problems)
    for uri, profile in profiles.items():
        try:
            flat, found = panel_profile(profile)
        except PanelError as exc:
            problems.append(f"{exc}; this profile is not used")
            continue
        out[uri] = flat
        problems += found
    return {"profiles": out, "problems": problems}
