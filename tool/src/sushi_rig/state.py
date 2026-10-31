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


def is_stale(state: dict[str, Any] | None, pid_alive: Callable[[int], bool]) -> bool:
    """Whether a recorded rig is gone and its state file can be ignored.

    No state is not stale state — there is simply nothing there. A state file
    naming a supervisor that no longer exists is stale, and `up` clears it and
    carries on rather than refusing forever after a crash.
    """
    if not state:
        return False
    if not (_is_real_id(state.get("supervisor_pid")) and _is_real_id(state.get("pgid"))):
        return True
    return not pid_alive(state["supervisor_pid"])


def _is_real_id(value: Any) -> bool:
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
    - `invalid`: it names no usable supervisor or group, so nothing in it may be
      signalled.
    - `gone`: the supervisor and the group are both dead; a leftover file.
    - `orphaned`: the supervisor is dead but the group still has members. Either
      they are the rig's children, left behind by a supervisor that was killed,
      or the number has since been handed to an unrelated group. Nothing here
      can tell which, so `down` must not signal it.
    - `running`: the supervisor is alive *and* its group exists. Both are
      required: the supervisor is what owns the group id (see `_supervise`), so
      a live group without it is not evidence of this rig.

    Pure, taking the probes as arguments, so the decision that guards `killpg`
    is a test rather than a claim.
    """
    if not state:
        return "absent"
    pid, pgid = state.get("supervisor_pid"), state.get("pgid")
    if not (_is_real_id(pid) and _is_real_id(pgid)):
        return "invalid"
    if pid_alive(pid):
        return "running" if pgid_alive(pgid) else "gone"
    return "orphaned" if pgid_alive(pgid) else "gone"
