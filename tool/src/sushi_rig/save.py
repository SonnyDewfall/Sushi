"""Capture the current live sound and write it as a named, versioned config.

This is the core the brief for issue #10 asks for: a deterministic bridge from
"the sound is right now" to "a config file on disk", callable from anywhere —
the CLI (`sushi-rig save <name>`) and the OSC listener (`listen.py`) are both
thin callers around `save_config`, not separate implementations of it. Per the
issue #12 decision, this bridge is meant to outlive whatever live tool is used
for exploration, so it deliberately doesn't assume Open Stage Control is the
only caller.

Composes existing pieces rather than reimplementing them: `live.capture()` for
the live state, `spec.RigSpec.load()` for the rig structure, `emit.emit()` for
the config. This module's own job is just the file-naming, versioning and
archiving policy around that.
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .emit import emit
from .live import DEFAULT_GRPC_ADDRESS, capture
from .spec import RigSpec

# The name becomes a filename, and — via the OSC listener — arrives over
# unauthenticated UDP. This is the actual security boundary: it blocks `../`,
# absolute paths, hidden files and anything not plain lowercase/digits/-/_.
# A panel's own input validation (which reuses this same pattern — see
# panel.py) is UX, not a substitute for this.
NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


class SaveError(ValueError):
    """A save request that must not proceed — bad name, unreadable rig, etc.

    Callers decide how to surface this: the CLI exits with the message, the
    OSC listener reports it back to the panel's status widget. Either way the
    failure must be visible, never silent.
    """


def validate_name(name: str) -> str:
    if not isinstance(name, str) or not NAME_PATTERN.fullmatch(name):
        raise SaveError(
            f"invalid config name {name!r}: must match {NAME_PATTERN.pattern} "
            "(lowercase letters, digits, '-', '_'; starts with a letter or digit; "
            "max 64 chars)"
        )
    return name


def save_config(
    name: str,
    rig_path: Path,
    out_dir: Path,
    archive_dir: Path,
    address: str = DEFAULT_GRPC_ADDRESS,
) -> Path:
    """Capture the running Sushi's state and write it as `<out_dir>/<name>.json`.

    If a config already exists under that name, it's archived first (see
    `_archive_existing`) rather than overwritten in place.
    """
    validate_name(name)

    rig_path = Path(rig_path)
    if not rig_path.is_file():
        raise SaveError(f"rig source not found: {rig_path}")

    rig = RigSpec.load(rig_path)
    state = capture(address)
    config = emit(rig, state)

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{name}.json"

    if out_path.exists():
        version = _archive_existing(out_path, Path(archive_dir), name)
    else:
        version = "1.0"

    meta = dict(config.get("_meta", {}))
    meta["name"] = name
    meta["version"] = version
    meta["source"] = rig_path.as_posix()
    config["_meta"] = meta

    out_path.write_text(json.dumps(config, indent=2) + "\n")
    return out_path


def _archive_existing(existing_path: Path, archive_dir: Path, name: str) -> str:
    """Snapshot the config currently at `existing_path` before it's replaced.

    Returns the version string to carry forward into the new save — re-saving
    over an existing name never bumps the version automatically (bumps are a
    deliberate act, per the versioning convention), so the new config inherits
    whatever version was already there.

    Archiving to `v<version>.json` could collide with an *earlier* archive of
    the same version, since the version isn't bumped on every save. Rather
    than silently overwrite a previous archive, append a timestamp — nothing
    saved is ever lost, without inventing an automatic version bump.
    """
    existing: dict[str, Any] = json.loads(existing_path.read_text())
    existing_meta = dict(existing.get("_meta", {}))
    version = existing_meta.get("version", "1.0")

    existing_meta["archived_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    existing["_meta"] = existing_meta

    target_dir = archive_dir / name
    target_dir.mkdir(parents=True, exist_ok=True)
    archive_path = target_dir / f"v{version}.json"
    if archive_path.exists():
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")
        archive_path = target_dir / f"v{version}-{timestamp}.json"

    archive_path.write_text(json.dumps(existing, indent=2) + "\n")
    existing_path.unlink()
    print(f"archived previous {name!r} ({version}) -> {archive_path}", file=sys.stderr)
    return version
