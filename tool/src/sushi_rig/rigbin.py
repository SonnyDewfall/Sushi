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
be one of this rig's (`ENTRY`: its rig part a plain name, the whole within
`MAX_ENTRY`), so neither route can name a file outside config/rigs/ or its
bin. A symbolic link is never followed: a rig file, the bin or an entry
that is one, or a path that resolves outside config/rigs/ or the bin, is
refused (`link`). The `stamp` is the UTC second the file was binned; two
deletes of one name in a second get `-2`, `-3`.

**Nothing another program writes is replaced or removed** (review of PR
#160). The CLI's `save`, a `git checkout` and an editor write config/rigs/
without any lock of the agent's, so neither move makes a placeholder or
moves over a name: each is a hard link, which fails atomically if the name
is taken, and then the unlink of the old name. A restore links the bin
file to config/rigs/<name>.yaml and unlinks the bin file, which only this
module writes. A delete links the rig file into the bin under a new name
and unlinks the rig file only if the name is still that same file (device
and inode); one replaced in between is left where it is, the link taken
back, and the delete refused (`changed`). What is left is the instant
between that check and the unlink. A crash between the link and the unlink
leaves the file in both places, never in neither.
"""

from __future__ import annotations

import os
import re
import shlex
from datetime import datetime, timezone
from pathlib import Path

BIN = ".deleted"
STAMP = "%Y%m%dT%H%M%SZ"
ENTRY = re.compile(r"^(?P<rig>.+)-(?P<stamp>\d{8}T\d{6}Z)(?:-(?P<n>[2-9]|[1-9][0-9]+))?\.yaml$")
# The longest name a rig file can have on the filesystems the rig runs on is
# 255 bytes; its bin entry adds the stamp and a few digits.
MAX_NAME = 200
# A bin entry: the name, "-", the stamp, a "-n" for a second delete in the
# same second (up to six digits), ".yaml".
MAX_ENTRY = MAX_NAME + len("-20261116T101502Z") + len("-999999") + len(".yaml")
IGNORE = "# sushi-rig's own: rig files deleted from the web UI (rigbin.py). Never committed.\n*\n"


class BinRefused(ValueError):
    """A delete or restore that must not happen (409), with its `code`:
    `name` (not a plain name), `entry` (a bin entry that is not this rig's),
    `gone` (nothing in the bin to restore), `taken` (a file of that name is
    there again)."""

    def __init__(self, reason: str, code: str):
        super().__init__(reason)
        self.code = code


def _plain_text(name: object, what: str, limit: int) -> str:
    if not isinstance(name, str) or not name:
        raise BinRefused(f"{what} must be given, as text", "name")
    if ("/" in name or "\\" in name or ".." in name or name.startswith(".")
            or any(ord(c) < 32 or ord(c) == 127 for c in name) or len(name.encode()) > limit):
        raise BinRefused(f"{name!r} is not a plain name: {what} is a file name of config/rigs/, with no '/', "
                         f"no '..', no leading dot, and at most {limit} bytes", "name")
    return name


def plain(name: object, what: str = "a rig's name") -> str:
    """`name` if it is a plain file name, else `BinRefused("name")`."""
    return _plain_text(name, what, MAX_NAME)


def rigs_dir(root: Path) -> Path:
    return Path(root) / "config" / "rigs"


def bin_dir(root: Path) -> Path:
    return rigs_dir(root) / BIN


def rig_path(root: Path, name: str) -> Path:
    return rigs_dir(root) / f"{plain(name)}.yaml"


def _no_link(path: Path, inside: Path, what: str) -> Path:
    """`path` unless it is a symbolic link or resolves outside `inside`
    (`BinRefused("link")`)."""
    if path.is_symlink():
        raise BinRefused(f"{what} is a symbolic link, which the bin never follows: move it by hand", "link")
    try:
        path.resolve().relative_to(inside.resolve())
    except ValueError as exc:
        raise BinRefused(f"{what} resolves outside {inside.name}/, which the bin never reaches", "link") from exc
    return path


def _bin_folder(root: Path) -> Path:
    folder = bin_dir(root)
    if folder.is_symlink():
        raise BinRefused(f"config/rigs/{BIN} is a symbolic link, which the bin never follows", "link")
    return folder


def parse_entry(entry: object) -> tuple[str, str, int]:
    """`(rig, stamp, n)` of a bin entry's name, or `BinRefused`: `name` for
    one that is not a plain file name or whose rig part is not a plain
    name, `entry` for one not shaped as an entry."""
    entry = _plain_text(entry, "a bin entry", MAX_ENTRY)
    match = ENTRY.fullmatch(entry)
    if not match:
        raise BinRefused(f"{entry} is not a file of config/rigs/{BIN}/ as a delete names one", "entry")
    return plain(match["rig"]), match["stamp"], int(match["n"] or 1)


def listing(root: Path) -> list[dict]:
    """Every entry in the bin, newest first: `{"rig", "entry", "stamp",
    "n"}`. A link, or a name not shaped as an entry, is left out."""
    folder = _bin_folder(root)
    if not folder.is_dir():
        return []
    found = []
    for path in folder.iterdir():
        try:
            rig, stamp, n = parse_entry(path.name)
        except BinRefused:
            continue
        if path.is_symlink() or not path.is_file():
            continue
        found.append({"rig": rig, "entry": path.name, "stamp": stamp, "n": n})
    return sorted(found, key=lambda e: (e["stamp"], e["n"], e["entry"]), reverse=True)


def entries(root: Path, name: str) -> list[Path]:
    """Rig `name`'s files in the bin, newest first (by stamp, then by the
    number a second delete in the same second was given)."""
    return [bin_dir(root) / e["entry"] for e in listing(root) if e["rig"] == name]


def entry_path(root: Path, name: str, entry: object) -> Path:
    """The bin file `entry` names, if it is rig `name`'s and is there."""
    rig, _, _ = parse_entry(entry)
    if rig != name:
        raise BinRefused(f"{entry} is not one of {name}'s files in config/rigs/{BIN}/", "entry")
    folder = _bin_folder(root)
    path = folder / entry
    if not path.is_symlink() and not path.is_file():
        raise BinRefused(f"config/rigs/{BIN}/{entry} is not there any more, so {name} cannot be put back "
                         "from it", "gone")
    return _no_link(path, folder, f"config/rigs/{BIN}/{entry}")


def _ignore_itself(folder: Path) -> None:
    ignore = folder / ".gitignore"
    if not ignore.exists():
        ignore.write_text(IGNORE)


def _link(src: Path, dst: Path) -> bool:
    """A hard link of `src` at `dst`, made atomically; False if anything is
    at `dst` already (a dangling link too). Nothing is created otherwise."""
    try:
        os.link(src, dst, follow_symlinks=False)
    except FileExistsError:
        return False
    return True


def move_to_bin(root: Path, name: str, now: datetime | None = None) -> Path:
    """Move config/rigs/<name>.yaml into the bin; the bin file it became.
    See the module's docstring for why it is a link and an unlink."""
    rigs = rigs_dir(root)
    path = rig_path(root, name)
    if not path.is_symlink() and not path.is_file():
        raise BinRefused(f"there is no rig called {name} in config/rigs/", "gone")
    _no_link(path, rigs, f"config/rigs/{name}.yaml")
    folder = _bin_folder(root)
    folder.mkdir(exist_ok=True)
    _no_link(folder, rigs, f"config/rigs/{BIN}")
    _ignore_itself(folder)
    stamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).strftime(STAMP)
    n = 1
    while True:
        target = folder / f"{name}-{stamp}{'' if n == 1 else f'-{n}'}.yaml"
        if _link(path, target):
            break
        n += 1
    moved = os.stat(target, follow_symlinks=False)
    try:
        now_there = os.stat(path, follow_symlinks=False)
    except FileNotFoundError:
        now_there = None
    if now_there is None or (now_there.st_dev, now_there.st_ino) != (moved.st_dev, moved.st_ino):
        target.unlink()
        raise BinRefused(f"config/rigs/{name}.yaml changed while it was being deleted (another program wrote "
                         "or moved it), so it was left as it is: look at it, then delete it again", "changed")
    os.unlink(path)
    return target


def restore(root: Path, name: str, entry: str | None = None) -> tuple[Path, Path]:
    """Move rig `name`'s bin file back: `entry`, or the newest of its own.
    `(was, path)`: where it came from and the rig file it is again. Refused
    (`taken`) if anything is at config/rigs/<name>.yaml, which is never
    replaced or removed: the bin file is linked there, which fails if the
    name is taken, and only then unlinked from the bin."""
    path = rig_path(root, name)
    if entry is not None:
        was = entry_path(root, name, entry)
    else:
        found = entries(root, name)
        if not found:
            raise BinRefused(f"there is nothing of {name}'s in config/rigs/{BIN}/ to put back", "gone")
        was = found[0]
    try:
        made = _link(was, path)
    except FileNotFoundError as exc:
        raise BinRefused(f"config/rigs/{BIN}/{was.name} went while it was being put back", "gone") from exc
    if not made:
        raise BinRefused(f"config/rigs/{name}.yaml is there again, so the deleted one is not put back over "
                         f"it: rename or delete that one first (the deleted one stays in config/rigs/{BIN}/"
                         f"{was.name})", "taken")
    os.unlink(was)
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
            "restore": f"git checkout HEAD -- {shlex.quote(rel)}"}
