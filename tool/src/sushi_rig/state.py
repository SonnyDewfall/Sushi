"""The state file: what is running, so `down`, `status` and `restart` can find it.

Kept apart from the supervisor because reading it is most of what the other
commands do, and none of that needs to know how a rig is started.
"""

from __future__ import annotations

import json
from typing import Any, Callable

from .paths import state_path


def read_state() -> dict[str, Any] | None:
    try:
        return json.loads(state_path().read_text())
    except (OSError, ValueError):
        return None


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
    pid = state.get("supervisor_pid")
    if not isinstance(pid, int):
        return True
    return not pid_alive(pid)
