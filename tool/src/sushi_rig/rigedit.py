"""A new rig, and a channel added or re-wired, as rig files: the decisions
behind the web UI's New rig screen and the rack's channel edits (agent.py's
rig files block, which does the reading, the compiling and the writing).

Phase 2's words for the screen: "a 'new rig' flow that asks how many channels,
mono or stereo for each, and which input each listens on, and refuses past the
8-channel ceiling with the reason". Everything here is pure, over dicts as
`parse_rig` reads a rig file and over a loaded `MachineSpec`.

## Nothing here decides whether a rig is valid

The compiler does. A request is checked here for its *shape* only -- that a
name is text, that `from` names a rig and a channel -- and every rule about
what a rig may be (the ceiling, names, widths, roles, `mono_to_stereo`) is
left to `rig.compile_rig_draft`, which compiles and wires a draft exactly as
`up --rig` will. A second copy of a rule here would be a second answer to
drift from the first, and the page would believe whichever it read. Even what
the page shows as a hint comes from the compiler: `ceiling` asks `check_rig`
for the first channel count it refuses, and `role_options` asks the patchbay
whether each role can feed a mono and a stereo channel, in its own words.

## Roles are read, never written

The machine file is hand-authored and names hardware (machine.py); nothing
here writes it. A role the player needs and does not have is said, with the
lines to add (`machine_hint`), and the player edits the file.

Which roles are inputs and which outputs, the machine file does not say: a
role is a list of ports, and a port's direction is PipeWire's knowledge. Two
ports could be a stereo input or the monitors. So the direction shown is read
from the role's name, `-in` or `-out`, as every committed machine file and
`patchbay.GUITAR_ROLE` and `MONITOR_ROLE` name them, and a role named neither
way is offered as either. It decides only which picker lists a role; the
compiler still decides whether a channel can use it.
"""

from __future__ import annotations

import copy
from typing import Any

from .rigfile import KIND, SCHEMA_VERSION, TARGET_SHAPES, Channel, Master, Rig, check_rig

_CHANNEL_KEYS = ("name", "input", "width", "from")
_FROM_KEYS = ("rig", "channel")
_PRESET_KEYS = ("preset",)
_NEW_KEYS = ("name", "channels")
_INPUT_KEYS = ("input", "width")
_PROBE = "probe"


class EditRequestError(ValueError):
    """A request whose shape is wrong (400): a value that is not text, a key
    nobody reads. Refused before any file is read, and never a rule about
    what a rig may be, which is the compiler's (409)."""


def _text(body: dict[str, Any], key: str, where: str) -> str:
    value = body.get(key)
    if not isinstance(value, str) or not value.strip():
        raise EditRequestError(f"{where}: {key!r} must be text, not {value!r}")
    return value


def _only(body: Any, keys: tuple[str, ...], where: str) -> dict[str, Any]:
    """`body` if it is an object holding only `keys`. A key beside them is
    refused, as save's and undo's bodies are: a request ignored while
    looking understood is how the wrong file gets written."""
    if not isinstance(body, dict):
        raise EditRequestError(f"{where} must be a JSON object, not {body!r}")
    extra = sorted(set(body) - set(keys))
    if extra:
        raise EditRequestError(f"{where}: {', '.join(map(repr, extra))} is not read here "
                               f"(it takes {', '.join(keys)})")
    return body


def channel_request(body: Any, where: str = "the channel") -> dict[str, Any]:
    """`{"name", "input", "width", "from"}` from one channel of a request,
    `from` None, `{"rig", "channel"}` (a rig's channel) or `{"preset"}` (a
    channel preset of config/channels/, #127); `EditRequestError` for a
    shape that is wrong. Values are not judged: "wide" is text, and the
    compiler says what a width may be. With a preset to start from, the
    width may be left out (None here): the preset's preferred width is
    taken, and the compiler still decides whether the input can give it."""
    body = _only(body, _CHANNEL_KEYS, where)
    source = body.get("from")
    if isinstance(source, dict) and "preset" in source:
        source = _only(source, _PRESET_KEYS, f"{where}: from")
        source = {"preset": _text(source, "preset", f"{where}: from")}
    elif source is not None:
        source = _only(source, _FROM_KEYS, f"{where}: from")
        source = {key: _text(source, key, f"{where}: from") for key in _FROM_KEYS}
    keys = ("name", "input") if source and "preset" in source and "width" not in body else ("name", "input", "width")
    out: dict[str, Any] = {key: _text(body, key, where) for key in keys}
    out.setdefault("width", None)
    out["from"] = source
    return out


def new_rig_request(body: Any) -> tuple[str, list[dict[str, Any]]]:
    """`(name, channels)` for `POST /rigs/new`, or `EditRequestError`. The
    name is not checked against the naming rule here: that is a refusal
    (409), said by `save.validate_name`, as the brief has it."""
    body = _only(body, _NEW_KEYS, "the body of a new rig")
    name = _text(body, "name", "the new rig")
    channels = body.get("channels")
    if not isinstance(channels, list):
        raise EditRequestError(f"the new rig: 'channels' must be a list of channels, not {channels!r}")
    return name, [channel_request(c, f"channel {i + 1}") for i, c in enumerate(channels)]


def input_request(body: Any) -> dict[str, str]:
    """`{"input"?, "width"?}` for a channel's input change: at least one."""
    body = _only(body, _INPUT_KEYS, "the body of an input change")
    out = {key: _text(body, key, "the input change") for key in _INPUT_KEYS if key in body}
    if not out:
        raise EditRequestError("an input change names the 'input', the 'width' or both")
    return out


# --------------------------------------------------------------------------- #
# The files, as dicts
# --------------------------------------------------------------------------- #

def source_channel(raw: Any, rig: str, channel: str) -> dict[str, Any]:
    """What `from` copies of `channel` in rig file `raw`: its `chain` and,
    if it has one, its `level`. `LookupError` saying why when there is no
    such channel. The chain is copied as written, settings and all, so the
    new channel starts with that channel's sound; its strip is not (a
    channel's strip is held at unity, rigfile.py), and neither is a MIDI map
    naming it, which belongs to the rig it came from."""
    channels = raw.get("channels") if isinstance(raw, dict) else None
    for entry in channels if isinstance(channels, list) else []:
        if isinstance(entry, dict) and entry.get("name") == channel:
            out: dict[str, Any] = {"chain": copy.deepcopy(entry.get("chain") or [])}
            if "level" in entry:
                out["level"] = entry["level"]
            return out
    names = [e.get("name") for e in channels or [] if isinstance(e, dict)]
    raise LookupError(f"{rig} has no channel called {channel} (it has: {', '.join(map(str, names)) or 'none'})")


def channel_entry(request: dict[str, Any], copied: dict[str, Any] | None = None) -> dict[str, Any]:
    """A channel as the rig files write one: name, input, width, its level
    if one was copied, and its chain, empty unless copied."""
    entry: dict[str, Any] = {"name": request["name"], "input": request["input"], "width": request["width"]}
    if copied and "level" in copied:
        entry["level"] = copied["level"]
    entry["chain"] = copy.deepcopy(copied["chain"]) if copied else []
    return entry


def new_rig(name: str, channels: list[dict[str, Any]], output: str) -> dict[str, Any]:
    """A rig file's contents: the channels, and a master on `output` with an
    empty chain, as `convert` writes every rig's master (patchbay.MONITOR_ROLE)."""
    return {
        "kind": KIND, "schema_version": SCHEMA_VERSION, "name": name,
        "channels": copy.deepcopy(channels),
        "master": {"output": output, "chain": []},
    }


def new_rig_header(when: str, copied: dict[str, str]) -> str:
    """The comment a new rig file starts with: what made it, and where each
    copied chain came from (`{channel: "config/rigs/x.yaml's board"}`), since
    the file is the record and the request is gone once it is written."""
    lines = [f"# Made on the web UI's New rig screen, {when}: its channels, and a master",
             "# that the channels' sends mix into. Not committed by sushi-rig: that is yours."]
    for channel, source in copied.items():
        lines.append(f"# {channel}: its chain and level copied from {source}.")
    return "\n".join(lines) + "\n\n"


def with_channel(raw: dict[str, Any], entry: dict[str, Any]) -> dict[str, Any]:
    """`raw` with `entry` added as its last channel; `raw` is left as it is."""
    out = copy.deepcopy(raw)
    channels = out.get("channels")
    out["channels"] = (list(channels) if isinstance(channels, list) else []) + [copy.deepcopy(entry)]
    return out


def with_input(raw: dict[str, Any], channel: str, change: dict[str, str]) -> dict[str, Any]:
    """`raw` with channel `channel`'s input or width (or both) set; `LookupError`
    for a channel it does not have."""
    out = copy.deepcopy(raw)
    for entry in out.get("channels") or []:
        if isinstance(entry, dict) and entry.get("name") == channel:
            entry.update(change)
            return out
    names = [e.get("name") for e in out.get("channels") or [] if isinstance(e, dict)]
    raise LookupError(f"{out.get('name')} has no channel called {channel} (it has: "
                      f"{', '.join(map(str, names)) or 'none'})")


def channels_of(raw: Any) -> list[dict[str, Any]]:
    """A rig file's channels as the page lists them: name, input, width,
    level and the names of the plugins in its chain, as the file says (a
    file is not checked here: one that does not compile is still listed,
    and refused when it is used)."""
    out = []
    channels = raw.get("channels") if isinstance(raw, dict) else None
    for entry in channels if isinstance(channels, list) else []:
        if not isinstance(entry, dict):
            continue
        chain = entry.get("chain") if isinstance(entry.get("chain"), list) else []
        out.append({"name": entry.get("name"), "input": entry.get("input"), "width": entry.get("width"),
                    "level": entry.get("level", 0.0),
                    "plugins": [p.get("name") for p in chain if isinstance(p, dict)]})
    return out


# --------------------------------------------------------------------------- #
# A pedal's assignment (#192): the rig file's `midi:`, one control at a time
# --------------------------------------------------------------------------- #
#
# The page assigns a device control to a target on the rack (an effect's
# on/off, a channel's on/off or volume, an effect's parameter) and the agent
# writes the running rig's `midi:` with it. Here, the request's shape and the
# file's contents after it, over the dict `yaml.safe_load` reads; what a
# control may take, and whether the target names anything the rig has, is
# `compile_midi`'s and `check_rig`'s to say, through the compile the agent
# runs before it writes (`rig.compile_rig_draft`).

ASSIGN_BINDINGS = tuple(TARGET_SHAPES)  # blend, parameter, mute, bypass, volume
_ASSIGN_KEYS = ("control", "controller", "none", *ASSIGN_BINDINGS)


def assign_request(body: Any) -> dict[str, Any]:
    """`{"control", "controller", "binding", "target"}` from `POST
    /rig/midi/assign`'s body: `{"control", <binding>: <target>}` to assign,
    `{"control", "none": true}` to take the control off whatever it does,
    and `"controller"` to name the device when the rig's map names none and
    the machine has several. `binding` and `target` are None for `none`.
    Shape only: a target is text (a blend, two texts), and what it must name
    is the compiler's."""
    body = _only(body, _ASSIGN_KEYS, "the body of an assignment")
    control = _text(body, "control", "the assignment")
    controller = _text(body, "controller", "the assignment") if "controller" in body else None
    bound = [key for key in ASSIGN_BINDINGS if key in body]
    if "none" in body:
        if body["none"] is not True or bound:
            raise EditRequestError('the assignment: "none": true takes the control off what it does, alone '
                                   f"(got {({k: body[k] for k in ('none', *bound)})!r})")
        return {"control": control, "controller": controller, "binding": None, "target": None}
    if len(bound) != 1:
        raise EditRequestError(f"the assignment names exactly one of {', '.join(ASSIGN_BINDINGS)}, or "
                               f'"none": true (got {", ".join(bound) or "none of them"})')
    binding = bound[0]
    target = body[binding]
    if binding == "blend":
        if not (isinstance(target, list) and len(target) == 2 and all(isinstance(t, str) and t for t in target)):
            raise EditRequestError(f"the assignment: blend must be {TARGET_SHAPES['blend']}, not {target!r}")
        target = list(target)
    elif not isinstance(target, str) or not target.strip():
        raise EditRequestError(f"the assignment: {binding} must be {TARGET_SHAPES[binding]}, not {target!r}")
    return {"control": control, "controller": controller, "binding": binding, "target": target}


def assignment_of(entry: Any) -> tuple[str, Any] | None:
    """`(binding, target)` of one entry of a rig file's `midi: controls`, or
    None for one that is not a mapping or binds nothing (the compiler says
    what is wrong with it)."""
    if not isinstance(entry, dict):
        return None
    bound = [key for key in ASSIGN_BINDINGS if key in entry]
    return (bound[0], entry[bound[0]]) if len(bound) == 1 else None


def said_assignment(binding: str | None, target: Any) -> str:
    """An assignment in words, as the log and the answers say it: "bypass
    lead/drive", "blend clean, lead", "nothing"."""
    if binding is None:
        return "nothing"
    return f"{binding} {', '.join(target) if isinstance(target, list) else target}"


def pick_controller(current: Any, asked: str | None, devices: list[dict[str, Any]]) -> tuple[str | None, Any]:
    """`(name, None)`: the controller an assignment is written for, or
    `(None, (reason, code, extra))` when it cannot be told.

    The rig's map's own `controller` when it has one, and an `asked` one
    must be it (`controller`): a rig's map is written for one device. With
    none, `asked` if the machine file has it, else the one device with a
    `type` (#191) when there is exactly one; several is a question for the
    player (`which_controller`, with their names in `controllers`), and none
    a refusal (`controller`), since an untyped controller's controls say
    nothing of what they are. `devices` is `MachineSpec.devices()`."""
    names = [d["name"] for d in devices]
    if isinstance(current, str) and current:
        if asked is not None and asked != current:
            return None, (f"this rig's MIDI map is written for {current}, not {asked}: a rig's map is for one "
                          f"controller", "controller", {})
        return current, None
    if asked is not None:
        if asked not in names:
            return None, (f"the machine file has no controller called {asked!r} (it has: "
                          f"{', '.join(names) or 'none'})", "controller", {})
        return asked, None
    typed = [d["name"] for d in devices if d.get("type")]
    if len(typed) == 1:
        return typed[0], None
    if typed:
        return None, (f"the machine file has {len(typed)} devices ({', '.join(typed)}) and this rig's map names "
                      "none: say which, as controller", "which_controller", {"controllers": typed})
    return None, ("the machine file describes no device with a type (a pedal, #191), so which controller to "
                  f"write the rig's map for is not known: name one, as controller (it has: "
                  f"{', '.join(names) or 'none'})", "controller", {})


def midi_of(raw: Any) -> dict[str, Any] | None:
    """A rig file's `midi:` as the file has it, or None."""
    midi = raw.get("midi") if isinstance(raw, dict) else None
    return copy.deepcopy(midi) if isinstance(midi, dict) else None


def unassigned_noop(raw: dict[str, Any], current: Any, control: str,
                    devices: list[dict[str, Any]]) -> tuple[dict[str, Any], Any] | None:
    """For `none` on a control the map does not have: `(change, None)`,
    nothing to write, when the device has that control; `(None, (reason,
    "no_control", {}))` when no device the map could be for has it. None when
    the map has the control, and taking it off is a change. The device is
    the map's controller, or with none any device of the machine file: a
    no-op is never a question of which (#192's review)."""
    midi = raw.get("midi") if isinstance(raw.get("midi"), dict) else {}
    entries = midi.get("controls") if isinstance(midi.get("controls"), list) else []
    if any(isinstance(e, dict) and e.get("control") == control for e in entries):
        return None
    pool = [d for d in devices if not current or d["name"] == current]
    names = sorted({c["name"] for d in pool for c in d.get("controls") or []})
    if control not in names:
        where = f"controller {current}" if current else "the machine file's devices"
        return None, (f"{where} {'has' if current else 'have'} no control called {control!r} (there are: "
                      f"{', '.join(names) or 'none'}), so there is nothing to take off", "no_control", {})
    return ({"op": "midi", "control": control, "controller": current or None, "binding": None, "target": None,
             "was": None, "moved": None, "replaced": [], "controller_set": False}, None)


def with_assignment(raw: dict[str, Any], controller: str, request: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """`raw` with `request` (`assign_request`'s) written into its `midi:`,
    and what changed; `raw` is left as it is.

    - The control's own entry is changed in place, so its comment stays
      with it; a control not in the map yet is added at the end.
    - A target holds one control of a binding: another control with the
      same binding and target is taken off it (`replaced`), as choosing C
      for an effect that D switched means C instead of D.
    - The control's previous assignment, when it had another, is in
      `moved`: assigning a control that is in use moves it.
    - `none` takes the control's entry out. A map left with no controls
      and no `programs` is taken out whole, so a rig no longer waits for a
      pedal it does not use.
    - `controller` is written when the map has none (`controller_set`).

    A `range` the entry had is kept only for the same parameter; nothing
    here judges the target, which the compiler does."""
    out = copy.deepcopy(raw)
    midi = out.get("midi") if isinstance(out.get("midi"), dict) else None
    entries = list(midi.get("controls") or []) if midi and isinstance(midi.get("controls"), list) else []
    control, binding, target = request["control"], request["binding"], request["target"]
    index = next((i for i, e in enumerate(entries) if isinstance(e, dict) and e.get("control") == control), None)
    was = assignment_of(entries[index]) if index is not None else None
    said: dict[str, Any] = {
        "op": "midi", "control": control, "controller": controller, "binding": binding, "target": target,
        "was": None if was is None else {was[0]: was[1]}, "moved": None, "replaced": [], "controller_set": False,
    }
    if binding is None:
        if index is not None:
            del entries[index]
    else:
        same = (binding, target)
        if was is not None and was != same:
            said["moved"] = {"control": control, "from": {was[0]: was[1]}, "said": said_assignment(*was)}
        kept = []
        for i, entry in enumerate(entries):
            if i != index and assignment_of(entry) == same:
                said["replaced"].append({"control": entry.get("control"), binding: target})
                continue
            kept.append(entry)
        entries = kept
        written: dict[str, Any] = {"control": control, binding: copy.deepcopy(target)}
        if index is not None and was == same and "range" in raw["midi"]["controls"][index]:
            written["range"] = copy.deepcopy(raw["midi"]["controls"][index]["range"])
        at = next((i for i, e in enumerate(entries) if isinstance(e, dict) and e.get("control") == control), None)
        if at is None:
            entries.append(written)
        else:
            entries[at] = written
    if not entries and not (midi or {}).get("programs"):
        out.pop("midi", None)
        return out, said
    if midi is None:
        midi = {}
        out["midi"] = midi
    if not midi.get("controller"):
        midi["controller"] = controller
        said["controller_set"] = True
        # The controller first, as the files are written (docs/reference/midi.md).
        out["midi"] = midi = {"controller": controller, **{k: v for k, v in midi.items() if k != "controller"}}
    midi["controls"] = entries
    return out, said


# --------------------------------------------------------------------------- #
# The machine's roles, as the compiler answers for them
# --------------------------------------------------------------------------- #

def direction_of(role: str) -> str:
    """"input", "output" or "either", from the role's name (module docstring)."""
    if role.endswith("-in"):
        return "input"
    if role.endswith("-out"):
        return "output"
    return "either"


def ceiling() -> dict[str, Any]:
    """The most channels a rig may have, and the compiler's own reason for
    refusing one more: `{"channels", "why"}`. Found by asking `check_rig`,
    one channel at a time, rather than restating its arithmetic."""
    count = 1
    while count < 64:
        rig = Rig(_PROBE, tuple(Channel(f"c{i}", _PROBE, "mono") for i in range(count + 1)), Master(_PROBE))
        problems = check_rig(rig)
        if problems:
            return {"channels": count, "why": problems[0]}
        count += 1
    return {"channels": count, "why": None}


def _probe(machine: Any, role: str, width: str = "mono", *, feeds: bool) -> str | None:
    """Why `role` cannot feed a channel of `width` (`feeds`), or cannot take
    the master's mix (not `feeds`), in the patchbay's words; None when it
    can. A one-channel rig on that role, compiled for its routing alone, and
    only the side asked about wired, so the answer is about that and nothing
    else."""
    from .patchbay import PatchbayError, generate_patchbay, routes_from_rig
    from .rigfile import RigFileError, compile_rig

    rig = Rig(_PROBE, (Channel(_PROBE + "-ch", role, width),), Master(role))
    try:
        spec, _ = compile_rig(rig, routing_only=True)
        sources, sinks = routes_from_rig(spec)
        if feeds:
            generate_patchbay(machine.role_ports(), sources, {}, mono_to_stereo=machine.mono_roles())
        else:
            generate_patchbay(machine.role_ports(), {}, sinks)
    except (RigFileError, PatchbayError) as exc:
        return "; ".join(exc.problems)
    return None


def role_options(machine: Any, wiring: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Each role of a validated machine file, for the page's pickers:

        {"name", "ports", "mono_to_stereo", "direction",
         "widths": {"mono": {"ok", "why"}, "stereo": {"ok", "why"}},
         "master": {"ok", "why"}, "used_by": [track, ...]}

    `widths` is whether the role can feed a channel of each width and
    `master` whether it can take the mix, each the patchbay's answer.
    `used_by` is the running rig's tracks wired to it (`wiring`, the running
    config's `_wiring`: `{track: {"source", "sink"}}`), the master included."""
    roles = []
    mono = machine.mono_roles()
    for role, ports in machine.role_ports().items():
        widths = {}
        for width in ("mono", "stereo"):
            why = _probe(machine, role, width, feeds=True)
            widths[width] = {"ok": why is None, "why": why}
        why = _probe(machine, role, feeds=False)
        used = sorted(track for track, wired in (wiring or {}).items()
                      if isinstance(wired, dict) and role in (wired.get("source"), wired.get("sink")))
        roles.append({"name": role, "ports": list(ports), "mono_to_stereo": role in mono,
                      "direction": direction_of(role), "widths": widths,
                      "master": {"ok": why is None, "why": why}, "used_by": used})
    return roles


def machine_hint(machine: Any, raw: Any, machine_name: str) -> str | None:
    """What to add to the machine file for the roles `raw` names that it
    lacks, or None: one line per role, with placeholders for the ports,
    which only the player can know. Words only: whether the rig is refused is
    the patchbay's to say, and this is shown beside that refusal.

    One line per role, whatever uses it, since the machine file is a mapping
    and a role written twice keeps only the last (review, 2026-11-06: a role
    used mono and stereo got two lines with one key). The line satisfies every
    use the patchbay would check: a mono channel needs one port, a stereo one
    two ports or one declared `mono_to_stereo`, the master two; so one port
    with `mono_to_stereo` for any mono use, two for stereo or the master
    alone. A role asked to be both a mono input and the master's pair cannot
    be one role, and is said so rather than written.

    `raw` is read as a hand-written file may be, not as a parsed rig: a
    `master: monitor-out` or a channel that is not a mapping is passed over,
    and the compiler's own refusal says what is wrong with it."""
    known = set(machine.role_ports())
    uses: dict[str, set[str]] = {}
    channels = raw.get("channels") if isinstance(raw, dict) else None
    for entry in channels if isinstance(channels, list) else []:
        role = entry.get("input") if isinstance(entry, dict) else None
        if isinstance(role, str) and role and role not in known:
            uses.setdefault(role, set()).add(entry.get("width") if entry.get("width") in ("mono", "stereo") else "mono")
    master = raw.get("master") if isinstance(raw, dict) else None
    output = master.get("output") if isinstance(master, dict) else None
    if isinstance(output, str) and output and output not in known:
        uses.setdefault(output, set()).add("output")
    if not uses:
        return None
    lines = []
    for role, used in uses.items():
        if "output" in used and "mono" in used:
            lines.append(f"  # {role}: used as a mono input and as the master's output, which no one role can be: "
                         "give the channel another role")
        elif "output" in used:
            lines.append(f'  {role}: ["<device>:playback_FL", "<device>:playback_FR"]   # left, then right')
        elif "mono" in used:
            lines.append(f'  {role}: {{ports: ["<device>:capture_AUX0"], mono_to_stereo: true}}')
        else:
            lines.append(f'  {role}: ["<device>:capture_AUX0", "<device>:capture_AUX1"]   # left, then right')
    return (f"{machine_name} has no role {', '.join(uses)}. sushi-rig does not edit the machine file: add, under "
            "roles:, with the ports `pw-link -o` (inputs) and `pw-link -i` (outputs) list,\n" + "\n".join(lines))
