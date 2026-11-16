"""The rig bin (#129): a rig file deleted from the web UI is moved, not gone.

`POST /rigs/{name}/delete` moves `config/rigs/<name>.yaml` to
`config/rigs/.deleted/<name>-<stamp>.yaml`, and `POST /rigs/{name}/restore`
moves it back (agent.py's rig bin block does the locking and the answers;
this module does the names and the moves).

**Out of every list of rigs.** Everything that lists the rigs globs
`config/rigs/*.yaml` and nothing deeper (`midi_listener.program_order`, so
`/rigs`, the picker and the Program Change numbers; `render.rigs_to_check`,
so the sound references; `tests/rig_fixtures.committed`), and the bin is a
folder beside the files, so a binned file is in none of them. The bin
ignores itself (a `.gitignore` of `*` inside it, written with it, beside the
line in the repository's own `.gitignore`), as `.sushi-rig/` does, so a
binned copy is never committed by a `git add config/rigs`.

**Not a git action.** Nothing is committed or staged: a committed rig that
is deleted shows in `git status` as deleted, and `git checkout HEAD --
config/rigs/<name>.yaml` brings back the committed version as well as the
bin can.

**Names.** A rig's name here is a plain name: one file name of
config/rigs/, without its `.yaml`, never a path. No `/` or `\\`, no `..`, no
leading dot (which would be the bin itself, or a file hidden from the
player), no control character. A bin entry is checked the same way and must
be one of this rig's (`ENTRY`), so neither route can name a file outside
config/rigs/ or its bin. The `stamp` is the UTC second the file was binned;
two deletes of one name in a second get `-2`, `-3`, as `save.unused_path`
names archives, the name claimed by creating it so nothing is ever replaced.
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from pathlib import Path

BIN = ".deleted"
STAMP = "%Y%m%dT%H%M%SZ"
ENTRY = re.compile(r"^(?P<rig>.+)-(?P<stamp>\d{8}T\d{6}Z)(?:-(?P<n>[2-9]|[1-9][0-9]+))?\.yaml$")
# The longest name a rig file can have on the filesystems the rig runs on is
# 255 bytes; its bin entry adds the stamp and a few digits.
MAX_NAME = 200
IGNORE = "# sushi-rig's own: rig files deleted from the web UI (rigbin.py). Never committed.\n*\n"


class BinRefused(ValueError):
    """A delete or restore that must not happen (409), with its `code`:
    `name` (not a plain name), `entry` (a bin entry that is not this rig's),
    `gone` (nothing in the bin to restore), `taken` (a file of that name is
    there again)."""

    def __init__(self, reason: str, code: str):
        super().__init__(reason)
        self.code = code


def plain(name: object, what: str = "a rig's name") -> str:
    """`name` if it is a plain file name, else `BinRefused("name")`."""
    if not isinstance(name, str) or not name:
        raise BinRefused(f"{what} must be given, as text", "name")
    if ("/" in name or "\\" in name or ".." in name or name.startswith(".")
            or any(ord(c) < 32 or ord(c) == 127 for c in name) or len(name.encode()) > MAX_NAME):
        raise BinRefused(f"{name!r} is not a plain name: {what} is a file name of config/rigs/, with no '/', "
                         f"no '..', no leading dot, and at most {MAX_NAME} bytes", "name")
    return name


def rigs_dir(root: Path) -> Path:
    return Path(root) / "config" / "rigs"


def bin_dir(root: Path) -> Path:
    return rigs_dir(root) / BIN


def rig_path(root: Path, name: str) -> Path:
    return rigs_dir(root) / f"{plain(name)}.yaml"


def entries(root: Path, name: str) -> list[Path]:
    """Rig `name`'s files in the bin, newest first (by stamp, then by the
    number a second delete in the same second was given)."""
    folder = bin_dir(root)
    if not folder.is_dir():
        return []
    found = []
    for path in folder.iterdir():
        match = ENTRY.fullmatch(path.name)
        if match and match["rig"] == name and path.is_file():
            found.append((match["stamp"], int(match["n"] or 1), path))
    return [path for _, _, path in sorted(found, reverse=True)]


def entry_path(root: Path, name: str, entry: object) -> Path:
    """The bin file `entry` names, if it is rig `name`'s and is there."""
    entry = plain(entry, "a bin entry")
    match = ENTRY.fullmatch(entry)
    if not match or match["rig"] != name:
        raise BinRefused(f"{entry} is not one of {name}'s files in config/rigs/{BIN}/", "entry")
    path = bin_dir(root) / entry
    if not path.is_file():
        raise BinRefused(f"config/rigs/{BIN}/{entry} is not there any more, so {name} cannot be put back "
                         "from it", "gone")
    return path


def _ignore_itself(folder: Path) -> None:
    ignore = folder / ".gitignore"
    if not ignore.exists():
        ignore.write_text(IGNORE)


def _claim(path: Path) -> bool:
    """Create `path` empty; False if anything is there (a dangling link too)."""
    try:
        path.open("x").close()
    except FileExistsError:
        return False
    return True


def move_to_bin(root: Path, name: str, now: datetime | None = None) -> Path:
    """Move config/rigs/<name>.yaml into the bin; the bin file it became."""
    path = rig_path(root, name)
    if not path.is_file():
        raise BinRefused(f"there is no rig called {name} in config/rigs/", "gone")
    folder = bin_dir(root)
    folder.mkdir(exist_ok=True)
    _ignore_itself(folder)
    stamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).strftime(STAMP)
    n = 1
    while True:
        target = folder / f"{name}-{stamp}{'' if n == 1 else f'-{n}'}.yaml"
        if _claim(target):
            break
        n += 1
    try:
        os.replace(path, target)
    except BaseException:
        target.unlink(missing_ok=True)
        raise
    return target


def restore(root: Path, name: str, entry: str | None = None) -> tuple[Path, Path]:
    """Move rig `name`'s bin file back: `entry`, or the newest of its own.
    `(was, path)`: where it came from and the rig file it is again. Refused
    (`taken`) if a file of that name is there again, which is never
    replaced: the place is claimed by creating it, and only then moved
    over, so a file made meanwhile is not lost."""
    path = rig_path(root, name)
    if entry is not None:
        was = entry_path(root, name, entry)
    else:
        found = entries(root, name)
        if not found:
            raise BinRefused(f"there is nothing of {name}'s in config/rigs/{BIN}/ to put back", "gone")
        was = found[0]
    if not _claim(path):
        raise BinRefused(f"config/rigs/{name}.yaml is there again, so the deleted one is not put back over "
                         f"it: rename or delete that one first (the deleted one stays in config/rigs/{BIN}/"
                         f"{was.name})", "taken")
    try:
        os.replace(was, path)
    except FileNotFoundError as exc:
        path.unlink(missing_ok=True)
        raise BinRefused(f"config/rigs/{BIN}/{was.name} went while it was being put back", "gone") from exc
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return was, path


def committed_copy(root: Path, path: Path, now_at: Path | None = None, timeout: float = 10) -> dict | None:
    """What git's HEAD holds of rig file `path`, read without writing
    anything: `{"committed", "same", "restore"}`. `committed` is whether
    HEAD has the file; `same` whether the bytes at `now_at` (the rig file
    itself unless given: after a delete, its bin file) are the committed
    ones, None when not known; `restore` the command, run in the checkout,
    that brings the committed version back. None when `root` is not a
    checkout's top or git does not answer within `timeout` a call."""
    import subprocess

    from .take import library_state

    if library_state(root) is None:
        return None
    env = {**os.environ, "GIT_OPTIONAL_LOCKS": "0"}

    def git(*args: str) -> str | None:
        try:
            done = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True,
                                  check=False, timeout=timeout, env=env)
        except (OSError, subprocess.SubprocessError):
            return None
        return done.stdout.strip() if done.returncode == 0 else None

    try:
        rel = Path(path).relative_to(Path(root)).as_posix()
    except ValueError:
        return None
    listed = git("ls-tree", "HEAD", "--", rel)
    if listed is None:
        return None
    fields = listed.split()
    if len(fields) < 3:
        return {"committed": False, "same": None, "restore": None}
    hashed = git("hash-object", "--", str(now_at or path))
    return {"committed": True, "same": None if hashed is None else hashed == fields[2],
            "restore": f"git checkout HEAD -- {rel}"}
