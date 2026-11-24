"""One Undo for everything (#209): the agent's history of what can be put back.

The page has one Undo button. It steps back the newest change, whatever it
was, and pressed again goes further back, to `LIMIT` steps. This module is
the record it reads: a list of entries, oldest first, each saying what was
done (`label`, a few words the page shows: "remove delay") and what is
needed to reverse it. The agent (`agent.py`'s "One Undo" block) does the
putting back; everything here is pure, so the order, the depth and the
merging of a fader's drag are tested with no agent.

**Kinds.** An entry's `kind` names the agent method that reverses it
(`Agent._UNDOERS`), so a new kind of action is one record call where it
happens and one method:

- `live`: a change made through /live, the amp or a pedal (a knob, a fader,
  a bypass, a channel's mute, which is its send bypassed (#210), a move, a
  plugin added or removed, an amp parameter or model). It holds
  the index of its entry in the agent's live log (`indices`, several for a
  fader's drag, see below) and is reversed by the live undo that was always
  there (`POST /live/undo`), which checks the rig is still as the change
  left it.
- `file`: a write of a rig file (a channel added, an input changed, a pedal
  assigned, a Save over the running rig's file, a revert, a channel load, a
  new rig). It
  holds the file's bytes before the write (`before`, None for a file that
  did not exist) and a digest of what was written (`after`), so the undo
  puts the old bytes back only while the file is still as the write left
  it. `apply` says what the forward change did to the running rig, which
  the undo does again: `switch` (it was switched into the file: a revert,
  a channel load), `bindings` (the agent's pedal bindings followed the file
  live), `restart` (it waits for a restart) or `none` (another rig's file,
  or a Save, which changed nothing that plays).
- `bin`: a rig deleted into the bin (rigbin.py); reversed by the restore.
- `none`: a change recorded so Undo does not skip past it as if it had not
  happened, which cannot be put back (`why`): a Save as, which makes a new
  rig the running one.

**A fader's drag is one step.** A knob or fader sends a change every few
tens of milliseconds while it moves, each its own live log entry. Changes
to the same control that follow one another within `MERGE_S` are one
history entry, undone together (newest first, each by the live undo), so
one press puts back one gesture.

**What cannot be undone** is said, not hidden: `Agent` answers an entry it
cannot reverse (the file changed since, the bin entry gone, a knob turned
from the panel) with `cant_undo` and drops it, so the next press goes on to
the one before, as the live undo's `skip` always did.

**A rig switch ends the live entries.** What was done before it was done to
another rig (the live log's barrier, `agent.pick_undo`), so they are dropped
from here when a switch is recorded (`drop_live`); file and bin entries
stay, since a file is where it is whatever runs.

**Per agent, in memory.** The history survives a page reload, and every
page of the agent shares it, but not an agent restart, as the live log
does not.
"""

from __future__ import annotations

import hashlib
from typing import Any, Callable

from .rigfile import SEND_SUFFIX

# How far back Undo goes: the newest LIMIT entries are kept.
LIMIT = 50
# Changes to one control this close together (seconds) are one step.
MERGE_S = 2.0

# The live log's ops that are changes Undo reverses, and those of them a
# drag sends many of. The rest (a save, a switch, a take, an undo, a skip's
# marker) are not entries here.
LIVE_OPS = ("parameter", "parameters", "bypass", "move", "add", "remove", "amp", "amp_model")
DRAGGED = ("parameter", "parameters", "amp")


def digest(data: bytes | None) -> str | None:
    """What a file held, compared without keeping it twice."""
    return None if data is None else hashlib.sha256(data).hexdigest()


def live_label(entry: dict[str, Any]) -> str:
    """A live log entry in two or three words, for the button and the
    notice after it ("Undone: remove delay")."""
    op, args = entry.get("op"), entry.get("args") or {}
    if op == "parameter":
        return f"{args.get('processor')} {args.get('parameter')}"
    if op == "parameters":
        return f"{args.get('processor')}"
    if op == "bypass":
        processor = str(args.get("processor"))
        if processor.endswith(SEND_SUFFIX):
            # A channel's mute is its send bypassed (#210): said as the mute.
            return f"{'mute' if args.get('bypassed') else 'unmute'} {processor[: -len(SEND_SUFFIX)]}"
        return f"{'bypass' if args.get('bypassed') else 'enable'} {processor}"
    if op == "move":
        return f"move {args.get('processor')}"
    if op == "add":
        return f"add {args.get('name')}"
    if op == "remove":
        return f"remove {args.get('processor')}"
    if op == "amp":
        return f"amp {args.get('name')}"
    if op == "amp_model":
        return "amp model"
    return str(op)


def _target(entry: dict[str, Any]) -> tuple | None:
    """What a dragged change moves, for merging: None for one never merged."""
    op, args = entry.get("op"), entry.get("args") or {}
    if op == "parameter":
        return (op, args.get("processor"), args.get("parameter"))
    if op == "parameters":
        return (op, args.get("processor"), tuple(sorted(args.get("parameters") or {})))
    if op == "amp":
        return (op, args.get("name"))
    return None


class History:
    """The entries, oldest first. `clock` is monotonic seconds, for merging."""

    def __init__(self, limit: int = LIMIT, clock: Callable[[], float] | None = None,
                 merge_s: float = MERGE_S) -> None:
        import time

        self.limit = limit
        self.merge_s = merge_s
        self.clock = clock or time.monotonic
        self.entries: list[dict[str, Any]] = []
        self._seq = 0

    def push(self, kind: str, label: str, **data: Any) -> dict[str, Any]:
        """Record one undoable action, newest; the oldest goes past `limit`."""
        self._seq += 1
        entry = {"seq": self._seq, "kind": kind, "label": label, "at": self.clock(), **data}
        self.entries.append(entry)
        if len(self.entries) > self.limit:
            del self.entries[: len(self.entries) - self.limit]
        return entry

    def push_live(self, index: int, logged: dict[str, Any]) -> dict[str, Any] | None:
        """Record live log entry `logged` (at live log index `index`), merged
        into the newest entry when it is the same control moved again within
        `merge_s`. Ops that are not changes are not recorded (None)."""
        op = logged.get("op")
        if op not in LIVE_OPS:
            return None
        target = _target(logged)
        newest = self.entries[-1] if self.entries else None
        now = self.clock()
        if (target is not None and newest is not None and newest["kind"] == "live"
                and newest.get("target") == list(target) and now - newest["at"] <= self.merge_s):
            newest["indices"].append(index)
            newest["at"] = now
            return newest
        return self.push("live", live_label(logged), op=op, indices=[index],
                         target=list(target) if target is not None else None)

    def newest(self) -> dict[str, Any] | None:
        return self.entries[-1] if self.entries else None

    def drop(self, entry: dict[str, Any]) -> None:
        """Take `entry` out: it was undone, or cannot be."""
        self.entries = [e for e in self.entries if e["seq"] != entry["seq"]]

    def drop_live(self) -> None:
        """A rig switch: the live entries were another rig's (see the module)."""
        self.entries = [e for e in self.entries if e["kind"] != "live"]

    def forget_index(self, index: int) -> None:
        """Live log entry `index` was undone some other way (`POST /live/undo`
        itself): it is no longer a step here."""
        for entry in list(self.entries):
            if entry["kind"] == "live" and index in entry["indices"]:
                entry["indices"].remove(index)
                if not entry["indices"]:
                    self.drop(entry)


def shown(entry: dict[str, Any] | None) -> dict[str, Any] | None:
    """An entry as `GET /undo` gives it: what the page needs and nothing it
    must not keep (a file's bytes)."""
    if entry is None:
        return None
    out = {"seq": entry["seq"], "kind": entry["kind"], "label": entry["label"]}
    if entry["kind"] == "live":
        out["op"] = entry.get("op")
        out["steps"] = len(entry.get("indices") or [])
    if entry.get("act"):
        out["act"] = entry["act"]
    if entry.get("rig"):
        out["rig"] = entry["rig"]
    if entry["kind"] == "none":
        out["why"] = entry.get("why")
    return out
