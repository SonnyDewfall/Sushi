"""Capture the current live sound and write it into a rig file.

This is the core the brief for issue #10 asks for: a deterministic bridge from
"the sound is right now" to "a config file on disk", callable from anywhere —
the CLI (`sushi-rig save <name>`) is a thin caller around `save_any`, and the
agent's `POST /rig/save` (the web UI's Save) calls `save_rig` with a capture
it made itself under its live lock. Per the issue #12 decision, this bridge
was meant to outlive whatever live tool is used for exploration, and it has:
the Open Stage Control panel's OSC listener, its first caller, is retired
(#139, #218).

Composes existing pieces rather than reimplementing them: `live.capture()` for
the live state, `rigfile.fold_state` and `write_rig` for the rig file. This
module's own job is the naming policy around that. It once also wrote v1
configs, `config/<name>.json`, archiving the one replaced under
`config/archive/`; v1 configs are retired (#218), and `config/archive/` is
kept as it was left.
"""

from __future__ import annotations

import copy
import re
from pathlib import Path
from typing import Any

from .amp import CONFIG_KEY as AMP_KEY, write_amp
from .live import DEFAULT_GRPC_ADDRESS, capture

# The name becomes a filename, and arrives from a request (the agent's save
# as, a channel's name). This is the actual security boundary: it blocks `../`,
# absolute paths, hidden files and anything not plain lowercase/digits/-/_.
# The page's own check (which reuses this same pattern, rig.js
# `checkRigName`) is UX, not a substitute for this.
NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


class SaveError(ValueError):
    """A save request that must not proceed — bad name, unreadable rig, etc.

    Callers decide how to surface this: the CLI exits with the message, the
    agent answers it. Either way the failure must be visible, never silent.
    """


def validate_name(name: str) -> str:
    if not isinstance(name, str) or not NAME_PATTERN.fullmatch(name):
        raise SaveError(
            f"invalid config name {name!r}: must match {NAME_PATTERN.pattern} "
            "(lowercase letters, digits, '-', '_'; starts with a letter or digit; "
            "max 64 chars)"
        )
    return name


def save_any(
    name: str,
    rig_path: Path,
    address: str = DEFAULT_GRPC_ADDRESS,
) -> Path:
    """Save the running rig into the rig of channels at `rig_path`
    (`save_rig`): what `sushi-rig save` runs. A rig.yaml of tracks, from
    which a save once wrote a v1 config, `config/<name>.json`, is refused:
    v1 configs are retired (#218). Read once, for the file's `kind`."""
    import yaml

    from .spec import looks_like_rig_file

    rig_path = Path(rig_path)
    if not rig_path.is_file():
        raise SaveError(f"rig source not found: {rig_path}")
    try:
        raw = yaml.safe_load(rig_path.read_text())
    except (OSError, yaml.YAMLError) as exc:
        raise SaveError(f"cannot read {rig_path}: {exc}") from exc
    if not looks_like_rig_file(raw):
        raise SaveError(
            f"{rig_path} is a rig.yaml of tracks, and a save from one wrote a v1 config, "
            "config/<name>.json; v1 configs are retired. Save the rig of channels it runs as, "
            "config/rigs/<name>.yaml"
        )
    return save_rig(name, rig_path, address, raw=raw)


def save_rig(
    name: str,
    rig_path: Path,
    address: str = DEFAULT_GRPC_ADDRESS,
    amp_model: str | None = None,
    amp_parameters: dict[str, float] | None = None,
    raw: dict[str, Any] | None = None,
    state: dict[str, Any] | None = None,
    edits: dict[str, Any] | None = None,
) -> Path:
    """Capture the running Sushi's state and write it into a rig of channels,
    `<name>.yaml` beside `rig_path` (#30's last slice, the half SAVE is).

    The rig file is the source, so under its own name it is updated in place
    through `write_rig` and keeps its comments. A new name is a new file; a
    name that already belongs to another rig file is refused, because
    updating that file in place would turn it into this rig and there is no
    archive to get it back from: the file is in git, and git is the archive
    (principle 5). The compiled config is not written, since `up --rig`
    compiles the file at every start.

    A run without the amp (`--no-amp`, or an amp that failed to start) says
    nothing about the amp, so the file's `amp` is left as it is. A parameter `amp_parameters` lacks is written as the
    plugin's default (`amp.write_amp`), as the listener's SAVE always has.

    `state` is a capture already made, which the agent passes (`POST
    /rig/save`): it captures on its own connection, under its live lock, so
    no change lands between the capture and the log entry that records the
    save. Without one, Sushi at `address` is captured here.

    `edits` is the agent's account of the plugins it added and removed live
    (`agent.live_edits`, #32), which lets `fold_state` write a chain's
    plugins as they now run: an added one gets an entry, a removed one's
    goes. Without it -- the CLI's `sushi-rig save`, which has no log -- a chain whose plugins are not the file's is
    refused, as a capture of a different rig or an edit nothing here saw: a
    plugin removed live used to be kept in the file with nothing said.

    Under the source's own name the file is the one the rig runs from, so
    the name is not checked against `NAME_PATTERN` and the file's own `name:`
    is kept: a rig file called `Guitar.Vocal.yaml` compiles, and must save in
    place. Only a name chosen for a new file is checked, since it becomes a
    file name.
    """
    import yaml

    from .rigfile import RigFileError, fold_state, write_rig
    from .spec import looks_like_rig_file

    rig_path = Path(rig_path)
    in_place = name == rig_path.stem and rig_path.suffix == ".yaml"
    if not in_place:
        validate_name(name)  # before the name is made a path
    out_path = rig_path.with_name(f"{name}.yaml")
    if raw is None:
        if not rig_path.is_file():
            raise SaveError(f"rig source not found: {rig_path}")
        try:
            raw = yaml.safe_load(rig_path.read_text())
        except yaml.YAMLError as exc:
            raise SaveError(f"cannot read {rig_path}: {exc}") from exc
    if not looks_like_rig_file(raw):
        raise SaveError(f"{rig_path} is not a rig of channels")

    if out_path != rig_path and out_path.exists():
        raise SaveError(
            f"a rig called {name} already exists at {out_path}; saving over it would "
            f"turn it into {rig_path.stem}. Choose another name, or run that rig and save it"
        )

    state = capture(address) if state is None else copy.deepcopy(state)
    # Captured properties are for checking a rig, not for writing one: which
    # of a plugin's properties may be set at load is not known from the
    # listing, and a value read back as "" would be baked into the file
    # (rigfile.merged_state).
    for values in state.get("processors", {}).values():
        values.pop("properties", None)
    try:
        folded = fold_state(raw, state, edits)
    except RigFileError as exc:
        raise SaveError("; ".join(exc.problems)) from exc

    if not in_place:
        folded["name"] = name
    if amp_model is not None:
        # The amp rides in the rig file as it does in a config: the values the
        # listener last sent, since Carla cannot be asked. See amp.py.
        folded["amp"] = write_amp({}, amp_model, amp_parameters or {})[AMP_KEY]

    try:
        write_rig(out_path, folded)
    except RigFileError as exc:
        raise SaveError("; ".join(exc.problems)) from exc
    return out_path


def file_git_state(root: Path, path: Path) -> dict[str, Any] | None:
    """What git says of one file of the checkout at `root`, beside what
    `take.library_state` says of the checkout: `{"file", "others"}`, or None
    when git cannot be read there. `file` is "untracked" (a new rig a save-as
    wrote), "changed" (staged or not), "unchanged" (as HEAD has it) or
    "ignored"; `others` is whether any other tracked file has uncommitted
    changes. `library_state`'s `dirty` cannot say either: it is the whole
    checkout's, and blind to untracked files. Called after the caller checked
    `root` is a checkout's top (`library_state` not None).

    Two git calls at most, each with `library_state`'s ten-second limit, and
    without the index lock, which a save must not take from the player's git.
    """
    import os
    import subprocess

    env = {**os.environ, "GIT_OPTIONAL_LOCKS": "0"}

    def git(*args: str) -> str | None:
        try:
            done = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True,
                                  check=False, timeout=10, env=env)
        except (OSError, subprocess.SubprocessError):
            return None
        return done.stdout if done.returncode == 0 else None

    try:
        rel = Path(path).resolve().relative_to(Path(root).resolve()).as_posix()
    except (OSError, ValueError):
        return None
    status = git("status", "--porcelain=v1", "-z", "--untracked-files=all")
    if status is None:
        return None
    fields = status.split("\0")
    mine, others, i = None, False, 0
    while i < len(fields):
        record = fields[i]
        i += 1
        if len(record) < 4:
            continue
        code, name = record[:2], record[3:]
        if code[0] in "RC":
            i += 1  # a rename or copy is followed by the path it came from
        if name == rel:
            mine = "untracked" if code == "??" else "changed"
        elif code != "??":
            others = True
    if mine is None:
        listed = git("ls-files", "--", rel)
        mine = None if listed is None else "unchanged" if listed.strip() else "ignored"
    return {"file": mine, "others": others}


def _claim(path: Path, *, directory: bool = False) -> Path:
    """Create `path` empty, failing with FileExistsError if anything is there.
    Creating is the check, so two processes cannot both be told a name is free."""
    if directory:
        path.mkdir()
    else:
        path.open("x").close()
    return path


def unused_path(base: Path, suffix: str = "", *, directory: bool = False) -> Path:
    """`base + suffix`, or `base-2 + suffix`, `base-3`, ... : the first that was
    free, and is now claimed as an empty file (or directory) for the caller to
    fill. Archives use it so nothing saved is replaced. A one-second stamp is not
    unique, and checking `exists()` then writing races: the CLI's save and the
    listener's save button, in the same second, were both told the name was free.
    """
    path, n = base.with_name(base.name + suffix), 2
    while True:
        try:
            return _claim(path, directory=directory)
        except FileExistsError:
            path, n = base.with_name(f"{base.name}-{n}{suffix}"), n + 1
