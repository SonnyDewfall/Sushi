"""The state file: what is running, so `down`, `status` and `restart` can find it.

Kept apart from the supervisor because reading it is most of what the other
commands do, and none of that needs to know how a rig is started.
"""

from __future__ import annotations

import json
from typing import Any, Callable

from .paths import state_path


def read_state() -> dict[str, Any] | None:
    """The recorded rig, or None for no file, an unreadable one or a corrupt one.

    Valid JSON that is not an object (a hand-edited `[]`, say) counts as corrupt:
    every caller indexes into the result, and a list would raise from deep
    inside `down` instead of reading as "nothing recorded".
    """
    try:
        state = json.loads(state_path().read_text())
    except (OSError, ValueError):
        return None
    return state if isinstance(state, dict) else None


def write_state(state: dict[str, Any]) -> None:
    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2) + "\n")


def clear_state() -> None:
    try:
        state_path().unlink()
    except FileNotFoundError:
        pass


def is_real_id(value: Any) -> bool:
    """Whether a recorded pid or pgid could possibly name a rig process.

    The state file is trusted as *identity* -- `down` signals whatever group it
    names -- so a value that merely parses is not enough. 0 is "my own group"
    to `killpg`, a negative number is a different call entirely, and 1 is init.
    `bool` is an `int` in Python, so `true` in a hand-edited file is refused
    explicitly rather than read as pid 1.
    """
    return isinstance(value, int) and not isinstance(value, bool) and value > 1


def liveness(
    state: dict[str, Any] | None,
    pid_alive: Callable[[int], bool],
    pgid_alive: Callable[[int], bool],
) -> str:
    """What the state file amounts to, as one of five words.

    - `absent`: no state file, nothing recorded.
    - `invalid`: it does not describe a rig this code could have started, so
      nothing in it may be trusted. That includes ids that are not real ones
      and a `pgid` that is not the `supervisor_pid`: `_supervise` calls `setsid`
      and so always leads a group of its own number, which makes the two equal
      by construction. A file where they differ was edited or is damaged.
    - `gone`: the group has no members left; a leftover file.
    - `orphaned`: the supervisor is dead but its group still has members. These
      are this rig's leftovers after a crash -- a group number cannot be handed
      to anything else while any member lives, so there is no reuse to fear --
      and recovering from exactly this is what `down` and `restart` are for.
    - `running`: the supervisor and its group are both alive.

    Pure, taking the probes as arguments, so the decision that guards `killpg`
    is a test rather than a claim.
    """
    if not state:
        return "absent"
    pid, pgid = state.get("supervisor_pid"), state.get("pgid")
    if not (is_real_id(pid) and is_real_id(pgid)) or pid != pgid:
        return "invalid"
    if not pgid_alive(pgid):
        return "gone"
    return "running" if pid_alive(pid) else "orphaned"
