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

from .rigfile import KIND, SCHEMA_VERSION, Channel, Master, Rig, check_rig

_CHANNEL_KEYS = ("name", "input", "width", "from")
_FROM_KEYS = ("rig", "channel")
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
    `from` None or `{"rig", "channel"}`; `EditRequestError` for a shape that
    is wrong. Values are not judged: "wide" is text, and the compiler says
    what a width may be."""
    body = _only(body, _CHANNEL_KEYS, where)
    out = {key: _text(body, key, where) for key in ("name", "input", "width")}
    source = body.get("from")
    if source is not None:
        source = _only(source, _FROM_KEYS, f"{where}: from")
        source = {key: _text(source, key, f"{where}: from") for key in _FROM_KEYS}
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


def _feeds_problem(machine: Any, role: str, width: str) -> str | None:
    """Why `role` cannot feed a channel of `width`, in the patchbay's words,
    or None: a one-channel rig compiled for its routing alone, and only its
    channel wired, so the answer is about the input and nothing else."""
    from .patchbay import PatchbayError, generate_patchbay, routes_from_rig
    from .rigfile import RigFileError, compile_rig

    rig = Rig(_PROBE, (Channel(_PROBE + "-ch", role, width),), Master(role))
    try:
        spec, _ = compile_rig(rig, routing_only=True)
        feeds, _ = routes_from_rig(spec)
        generate_patchbay(machine.role_ports(), feeds, {}, mono_to_stereo=machine.mono_roles())
    except (RigFileError, PatchbayError) as exc:
        return "; ".join(exc.problems)
    return None


def _takes_master_problem(machine: Any, role: str) -> str | None:
    """Why `role` cannot take the master's output, in the patchbay's words."""
    from .patchbay import PatchbayError, generate_patchbay, routes_from_rig
    from .rigfile import RigFileError, compile_rig

    rig = Rig(_PROBE, (Channel(_PROBE + "-ch", role, "mono"),), Master(role))
    try:
        spec, _ = compile_rig(rig, routing_only=True)
        _, sinks = routes_from_rig(spec)
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
            why = _feeds_problem(machine, role, width)
            widths[width] = {"ok": why is None, "why": why}
        why = _takes_master_problem(machine, role)
        used = sorted(track for track, wired in (wiring or {}).items()
                      if isinstance(wired, dict) and role in (wired.get("source"), wired.get("sink")))
        roles.append({"name": role, "ports": list(ports), "mono_to_stereo": role in mono,
                      "direction": direction_of(role), "widths": widths,
                      "master": {"ok": why is None, "why": why}, "used_by": used})
    return roles


def machine_hint(machine: Any, raw: dict[str, Any], machine_name: str) -> str | None:
    """What to add to the machine file for the roles `raw` names that it
    lacks, or None: the lines, with placeholders for the ports, which only
    the player can know. Words only: whether the rig is refused is the
    patchbay's to say, and this is shown beside that refusal."""
    known = set(machine.role_ports())
    missing = []
    for entry in raw.get("channels") or []:
        if isinstance(entry, dict) and isinstance(entry.get("input"), str) and entry["input"] not in known:
            missing.append((entry["input"], entry.get("width")))
    output = (raw.get("master") or {}).get("output")
    if isinstance(output, str) and output not in known:
        missing.append((output, "output"))
    if not missing:
        return None
    lines = []
    for role, width in dict.fromkeys(missing):
        if width == "output":
            lines.append(f'  {role}: ["<device>:playback_FL", "<device>:playback_FR"]   # left, then right')
        elif width == "stereo":
            lines.append(f'  {role}: ["<device>:capture_AUX0", "<device>:capture_AUX1"]   # left, then right')
        else:
            lines.append(f'  {role}: {{ports: ["<device>:capture_AUX0"], mono_to_stereo: true}}')
    names = ", ".join(dict.fromkeys(role for role, _ in missing))
    return (f"{machine_name} has no role {names}. sushi-rig does not edit the machine file: add, under "
            "roles:, with the ports `pw-link -io` lists for it,\n" + "\n".join(lines))
