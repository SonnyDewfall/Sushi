"""The web UI (#31, v0.1 #111): the rack (S1), with Save (S4) and the takes (S8), and
the stage screen (S5), with the record bar (S7).

The agent serves the page at `/` and its files under `/ui/` (`/ui/` itself
redirects to `/`), and the page reads and changes the rig through the
agent's own routes, nothing else.

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
    GET /live          once a second, and only while /health says a rig is up and no
                       lifecycle command is running (`readsLive` in rig.js): tracks,
                       chains, values, ranges, bypass
    GET /live/log      once a second: what is unsaved, and what undo would take back
    GET /rig/load      every 2 s while a rig is up: pw-top's load and xruns for the
                       LOAD and XRUNS tiles. Slower than /live because each is a
                       pw-top run of about a second on the rig
    GET /rigs          config/rigs/, for the picker: until it answers, and again
                       after every start, stop or switch
    GET /live/plugins  which plugin each processor is: again whenever the processors'
                       names change or the rig does (its name, its start, a rig
                       switch in the log), since a rig of the same names can hold
                       another plugin
    GET /profiles      the profiles, flattened by `panel_profile`: beside the poll,
                       never holding it up, until it answers (asked again every
                       10 s while it fails; a profile that would not load is a
                       problem shown, not a failure)
    GET /library       the plugins' names, the same way
    GET /amp           with /live, and only when /live is asked: the amp as last
                       sent to Carla, for the amp card and the stage's amp
    GET /amp/models    amp/models for the amp card's picker: once, and again after
                       a model change
    GET /record        with /live, and only when /live is asked: whether a take is
                       recording, its name and elapsed time, or why recording is off
    GET /takes         every 10 s, and at once when a take starts or stops: the takes
                       and `take check`'s verdict on each

and sends POST /live/parameter, /live/bypass, /live/move and /live/undo as
they happen, POST /amp/parameter from the amp's faders, POST /amp/model from
its picker, POST /rig/up, /rig/down and /rig/switch from the rig picker, and
POST /rig/save from the rack's Save and Save as…, and POST /record/start and
/record/stop from the record bar. A
refusal's `reason` is shown on screen; nothing is reverted without saying
why.

## The amp

The amp runs in Carla, not Sushi, and what the agent knows of it is what it
last sent (amp_live.py), so the card says "values are what was last sent,
not read back", and a value never sent is "not sent", not a default. The
rack's card (above the channels, since the amp is in front of the board)
has the model and what it declares, faders for the panel's two parameters,
Quality shown and not on a fader (amp.PANEL_PARAMETERS says why), and a
model picker that is a two-step change: choose, then a confirm that says the
sound cuts out for a moment, since a model change restarts Carla. The lock
refuses a model change, as it refuses structure. On the stage, only the
model's name and the output level, and only while the lock is off. With the
panel running the faders and the picker are off and say why: the panel owns
the amp then.

/live is not asked while the rig is down (#111): it can only answer 503
then, and a 503 a second was a console error a second, 110 of them in one
session, burying any real failure. A rig that is down is shown as a state
(muted, not red); a /live failure while the rig is up is still shown as the
failure it is.

## Start, stop and switch (#111)

A picker lists config/rigs/ (the rack's left column, as the mockups' S1 has
it; on the stage screen only while the lock is off, since a Stop within a
thumb's reach on stage is what the lock is for). A start is always a rig of
channels and headless, because this page replaces the OSC panel and a rig run
with its panel can only be switched by restart. Stop takes a second tap
within eight seconds, because it cuts the sound. While a lifecycle command
runs, from this page or another, nothing is offered (the agent would answer
409 busy) and what is running is said. A switch says how it was made, live
or by restart, and for a restart why.

## Save (S4)

Below the unsaved changes in the rack, never on the stage screen. Save
writes the running rig's own file (POST /rig/save) and is offered whenever
a rig that can be saved from here is up, not only while the unsaved list
has something: that list is what the agent saw, so with nothing seen it
says "no changes seen since" when. A v1 run, or one with its panel, is not
offered and is told how to save instead. Save as… asks for a name, checked before sending by the
rule the agent saves by (`checkRigName`, save.NAME_PATTERN), and the new rig
becomes the running one. The agent writes and never commits: the answer is
shown with the file written, that it is not committed and the commit it
sits on, or the agent's reason for refusing. The unsaved list counts from
the newest save in the log (`unsavedChanges`), so a change or an undo after
it is unsaved again; undo passes over a save, as the agent's does.

## Recording (S7, S8)

The stage screen's record bar, under the rig's state, as the mockups' Record
frame has it: a take name, a big Record and a big Stop, and while a take
runs its name and elapsed time in red. Two buttons, each enabled when it
would be acted on, and whether a take runs is GET /record's answer, never
the page's: a page reloaded mid-take, or a second one, shows the take
running (#57's reason for two buttons). Both work with the lock on, since
recording is a stage action, and Stop takes no confirm, since stopping a
take loses nothing. The name field shows the agent's default for the next
take (GET /record's `default_name`, `<rig>-<date>-take-<nn>`, #134), which
is the agent's to work out, never the page's, so two pages show the same
one. Record on an untouched field sends no name, and the agent names the
take by its count at that moment: the name shown, unless a take was made
since, and then the next number. Entering the field selects a default, so
typing replaces it: once the press that gave the focus is over, not in the
focus event, where WebKit undoes it (untested on WebKit: only Chromium is
here). Only entering selects: a rebuild that hands the focus back puts the
caret or selection where it was. Only what the player typed is kept
(`takeTyped`), through polls, a rig switch and finished takes, until it is
recorded with or cleared, and a field left empty shows the default again. A name is checked
before sending (`checkTakeName`, the rule take.check_name keeps); a
name already in the takes list says the earlier take will move to the
archive. The elapsed time is the recorder's (`elapsed`, by its clock),
counted on by this page's clock between answers (`elapsedNow`), so it keeps
moving if a poll is slow and a phone with a wrong clock still shows the
take's length. Why recording is off is said on the bar: no rig, the panel
running (whose record bar owns it), a v1 run, the recorder missing or
crashed, or the recorder's own reason.

The rack lists the takes beside Save (`takeRows`): each one's name, length
and `take check`'s verdict, a take with problems marked and its first problem
said, one cut off saying so, and the one being recorded as "Recording now"
rather than as unfinished. The mockup's meters and its Open button are not
built: nothing measures levels yet, and a take is a folder on the rig.
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
