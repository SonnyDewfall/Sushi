"""Where the rig's files live.

Separated because everything else needs these and none of it needs the
supervisor. The one rule worth stating: paths are resolved from the package's
own location, never from `$HOME`. An earlier shell script did `cd "$HOME/Sushi"`,
which meant running it from a worktree silently drove the *main* checkout.
"""

from __future__ import annotations

import os
from pathlib import Path


def checkout_root() -> Path:
    """The rig checkout this package was installed from.

    Resolved from the package's own location, never from `$HOME`. The old
    `start-rig.sh` did `cd "$HOME/Sushi"`, which meant running it from a
    worktree silently drove the *main* checkout's config and tool — and the same
    assumption, baked into `LV2_PATH`, is the logged defect where a rig started
    outside the script finds no plugins at all.

    src/sushi_rig/rig.py -> src/sushi_rig -> src -> tool -> <root>
    """
    return Path(__file__).resolve().parents[3]


def runtime_dir() -> Path:
    """Where the state file and logs live.

    `XDG_RUNTIME_DIR` is tmpfs and is cleared when the user logs out, so a
    machine that crashed with a rig up cannot come back claiming one is still
    running. The `~/.cache` fallback is for systems without it, and is the one
    case where a stale file can outlive a reboot — which the liveness check
    below handles anyway.
    """
    base = os.environ.get("XDG_RUNTIME_DIR")
    root = Path(base) if base else Path.home() / ".cache"
    return root / "sushi-rig"


def state_path() -> Path:
    return runtime_dir() / "rig.json"


def log_dir() -> Path:
    return runtime_dir() / "logs"


# --- pure logic -------------------------------------------------------------
