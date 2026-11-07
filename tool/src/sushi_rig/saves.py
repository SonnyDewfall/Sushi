"""What a rig file was at each save, and at each commit: the revert points of
the web UI's "Revert to a previous save" (2026-11-06).

The player asked for one view of history and only one: to put a rig back as
an earlier save had it. So every successful save the agent makes (POST
/rig/save, its save-as included) keeps a snapshot of the file exactly as it
was written, and the file's own git history is listed beside them, read-only.
A revert writes one of those back over the file, byte for byte.

**Where the snapshots live: `<checkout>/.sushi-rig/saves/<rig>/`.** A snapshot
is a version of a file of this checkout, so it is kept with the checkout:
found by any agent run from it, and two worktrees keep apart, as their rig
files are. Not the runtime dir (`paths.runtime_dir`), which is tmpfs and
cleared when the player logs out, so a reboot would forget every save; and
not the user's state dir, which would have to be keyed by the checkout's
path and would outlive a checkout deleted or moved. The directory ignores
itself (a `.gitignore` of `*` inside it, written with it, beside the line in
the repository's own `.gitignore`), so `git status` stays clean in any
checkout -- a temporary one, or one on a branch older than this -- and a
save's "what git says of the file" is never muddied by its own snapshot.

**How many: the `KEEP` (50) written last, per rig.** Older ones are deleted
as a new one is written, by the order they were written in (a `seq` in each
account), never by time: a rig's clock can run behind its last save, and
the save just kept is never the one deleted. A rig file is a few
kilobytes, so the bound is about what a list on a phone can usefully show,
not about disk; and the commits are there for anything older.

Each snapshot is two files: `<id>.yaml`, the file's bytes as the save wrote
them, and `<id>.json`, what the save said (when, which rig, how, and the
commit it sat on as `take.library_state` and `save.file_git_state` gave it).
The id is the save's UTC time to the microsecond, unique per rig and never
made from anything a request sends: a revert names an id, which is
checked against `ID_PATTERN` and looked up, never joined to a path as given.

Git is read with a timeout (`GIT_TIMEOUT`) and never written: a commit point
is `git show <sha>:<path>`, and committing stays the player's.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Kept per rig: see the module docstring.
KEEP = 50
# Under the checkout: see the module docstring.
SAVES_DIR = Path(".sushi-rig") / "saves"
# What a snapshot id is (`new_id`), and a commit's revert point.
ID_PATTERN = re.compile(r"^\d{8}T\d{6}\.\d{6}Z(?:-\d{1,3})?$")
COMMIT_PATTERN = re.compile(r"^commit:([0-9a-f]{40})$")
# Each git call's limit, as `take.library_state` gives its own.
GIT_TIMEOUT = 10.0
# How many of the file's commits are listed: the same bound as the saves.
COMMITS = KEEP


class SaveNotFound(LookupError):
    """A revert point that is not there (404)."""


def saves_root(root: Path) -> Path:
    return Path(root) / SAVES_DIR


def rig_dir(root: Path, rig: str) -> Path:
    """Where rig `rig`'s snapshots are. `rig` is a name `/rigs` lists (the
    caller looks it up first), never a path a request made up."""
    return saves_root(root) / rig


def new_id(now: datetime, taken: set[str]) -> str:
    """A snapshot's id from the time it was saved, unique among `taken`
    (two saves in one microsecond get `-1`, `-2`). Pure."""
    base = now.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    if base not in taken:
        return base
    n = 1
    while f"{base}-{n}" in taken:
        n += 1
    return f"{base}-{n}"


def to_prune(order: dict[str, int], keep: int = KEEP, new: str | None = None) -> list[str]:
    """Which snapshots go so that the `keep` written last stay, the first
    written first: `order` is each id's write sequence (`seq`), never its
    time, and `new`, the one just written, never goes. On a rig whose clock
    is behind its last save (no RTC, before NTP), an id from the clock sorts
    oldest, and pruning by id deleted the save as it was kept (review,
    2026-11-07). Pure."""
    ordered = sorted((sid for sid in order if sid != new), key=lambda sid: (order[sid], sid))
    room = keep - (1 if new is not None else 0)
    return ordered[:max(0, len(ordered) - room)]


def _ignore_itself(root: Path) -> None:
    """The snapshots' directory ignores itself, so `git status` stays clean
    in a checkout whose own .gitignore does not name it."""
    top = Path(root) / SAVES_DIR.parts[0]
    top.mkdir(parents=True, exist_ok=True)
    ignore = top / ".gitignore"
    if not ignore.exists():
        ignore.write_text("# sushi-rig's own: snapshots of saved rig files (saves.py). Never committed.\n*\n")


def _atomic(path: Path, data: bytes) -> None:
    scratch = path.with_name(path.name + ".tmp")
    try:
        scratch.write_bytes(data)
        os.replace(scratch, path)
    except BaseException:
        scratch.unlink(missing_ok=True)
        raise


def keep(root: Path, rig: str, data: bytes, said: dict[str, Any], now: datetime | None = None,
         limit: int = KEEP) -> dict[str, Any]:
    """Keep `data`, the bytes a save wrote into rig `rig`'s file, as a
    snapshot, with `said` (the save's own account: `file`, `how`, `library`),
    and prune that rig's to the newest `limit`. The point it became, as
    `points` lists it. The bytes first and the account last, so a snapshot
    half-written by a crash has no account and is not listed."""
    now = now or datetime.now(timezone.utc)
    _ignore_itself(root)
    where = rig_dir(root, rig)
    where.mkdir(parents=True, exist_ok=True)
    taken = {p.stem for p in where.glob("*.json")} | {p.stem for p in where.glob("*.yaml")}
    sid = new_id(now, taken)
    order = _write_order(where)
    meta = {"id": sid, "kind": "save", "rig": rig, "at": now.astimezone(timezone.utc).isoformat(),
            "seq": max(order.values(), default=0) + 1,
            "bytes": len(data), **{k: v for k, v in said.items() if k in ("file", "how", "library", "saved_as")}}
    _atomic(where / f"{sid}.yaml", data)
    _atomic(where / f"{sid}.json", json.dumps(meta, indent=2).encode())
    order[sid] = meta["seq"]
    for old in to_prune(order, limit, new=sid):
        for suffix in (".json", ".yaml"):
            (where / f"{old}{suffix}").unlink(missing_ok=True)
    return meta


def _write_order(where: Path) -> dict[str, int]:
    """Each snapshot's write sequence, by id: its account's `seq`, or 0 for
    one without (or unreadable), which then goes first."""
    order = {}
    for meta_path in where.glob("*.json"):
        try:
            seq = json.loads(meta_path.read_text()).get("seq")
        except (OSError, ValueError, AttributeError):
            seq = None
        order[meta_path.stem] = seq if isinstance(seq, int) and not isinstance(seq, bool) else 0
    return order


def snapshots(root: Path, rig: str) -> tuple[list[dict[str, Any]], list[str]]:
    """Rig `rig`'s snapshots, newest first, and what could not be read (each
    said, never dropped silently): `(points, problems)`."""
    where = rig_dir(root, rig)
    points, problems = [], []
    if not where.is_dir():
        return points, problems
    order = _write_order(where)
    for meta_path in sorted(where.glob("*.json"), key=lambda p: (order.get(p.stem, 0), p.stem), reverse=True):
        if not ID_PATTERN.match(meta_path.stem):
            continue
        try:
            meta = json.loads(meta_path.read_text())
        except (OSError, ValueError) as exc:
            problems.append(f"{meta_path.name} could not be read: {type(exc).__name__}: {exc}")
            continue
        if not (where / f"{meta_path.stem}.yaml").exists():
            problems.append(f"{meta_path.stem}: its account is there and the file it kept is not")
            continue
        points.append({**meta, "id": meta_path.stem, "kind": "save"})
    return points, problems


def snapshot_bytes(root: Path, rig: str, sid: str) -> bytes:
    """The bytes snapshot `sid` of rig `rig` kept, or `SaveNotFound`."""
    if not ID_PATTERN.match(sid):
        raise SaveNotFound(f"{sid!r} is not a save's id")
    path = rig_dir(root, rig) / f"{sid}.yaml"
    if not path.is_file():
        raise SaveNotFound(f"{rig} has no save {sid}: it may have been pruned (the newest {KEEP} are kept)")
    return path.read_bytes()


def _git(root: Path, *args: str, text: bool = True, timeout: float = GIT_TIMEOUT) -> Any:
    """One read-only git call in `root`, without the index lock, or None
    when git cannot answer there within `timeout`."""
    env = {**os.environ, "GIT_OPTIONAL_LOCKS": "0"}
    try:
        done = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=text, check=False,
                              timeout=timeout, env=env)
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout if done.returncode == 0 else None


def _relative(root: Path, path: Path) -> str | None:
    try:
        return Path(path).resolve().relative_to(Path(root).resolve()).as_posix()
    except (OSError, ValueError):
        return None


def parse_log(out: str) -> list[dict[str, Any]]:
    """`git log --format=%H%x1f%cI%x1f%s` as revert points, newest first as
    git gives them. Pure."""
    points = []
    for line in out.splitlines():
        parts = line.split("\x1f")
        if len(parts) != 3 or not re.fullmatch(r"[0-9a-f]{40}", parts[0]):
            continue
        sha, at, subject = parts
        points.append({"id": f"commit:{sha}", "kind": "commit", "sha": sha, "at": at, "subject": subject})
    return points


def commits(root: Path, path: Path, limit: int = COMMITS) -> tuple[list[dict[str, Any]], str | None]:
    """The commits that changed rig file `path`, newest first, and why there
    are none when git cannot say: `(points, note)`. Read-only."""
    rel = _relative(root, path)
    if rel is None:
        return [], "the rig file is not inside the checkout, so git has no history of it"
    top = _git(root, "rev-parse", "--show-toplevel")
    if top is None or _relative(Path(top.strip()), Path(root)) != ".":
        return [], "the checkout is not a git repository here, so there are no commits to list"
    out = _git(root, "log", f"-n{limit}", "--format=%H%x1f%cI%x1f%s", "--", rel)
    if out is None:
        return [], "git log could not be read"
    return parse_log(out), None


def commit_bytes(root: Path, path: Path, sha: str) -> bytes:
    """Rig file `path` as commit `sha` has it, or `SaveNotFound`."""
    rel = _relative(root, path)
    if rel is None or not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise SaveNotFound(f"no commit {sha!r} of that file")
    # Only a commit that changed this file is a revert point, as listed.
    listed = _git(root, "log", "--format=%H", "--", rel)
    if listed is None or sha not in listed.split():
        raise SaveNotFound(f"commit {sha[:7]} did not change {rel}, so it is not one of its revert points")
    data = _git(root, "show", f"{sha}:{rel}", text=False)
    if data is None:
        raise SaveNotFound(f"commit {sha[:7]} has no {rel} (it deleted it)")
    return data


def restore(path: Path, data: bytes) -> None:
    """Write `data` over rig file `path`, whole or not at all, keeping the
    file's permissions: a revert's write. Plain bytes, not
    `rigfile.write_rig`, which merges into the file that is there and so
    keeps that file's comments, not the snapshot's (the route says why)."""
    import shutil

    path = Path(path)
    scratch = path.with_name(path.name + ".tmp")
    try:
        scratch.write_bytes(data)
        if path.exists():
            shutil.copymode(path, scratch)
        os.replace(scratch, path)
    except BaseException:
        scratch.unlink(missing_ok=True)
        raise


def merged(saves: list[dict[str, Any]], committed: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Saves and commits in one list, newest first by time. Pure."""
    def when(point: dict[str, Any]) -> float:
        try:
            moment = datetime.fromisoformat(str(point.get("at")))
        except ValueError:
            return float("-inf")
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        return moment.timestamp()

    return sorted(saves + committed, key=when, reverse=True)


def kept_as(current: bytes | None, saves: list[dict[str, Any]], read) -> str | None:
    """The newest save whose bytes are the file as it is now (`read(id)`
    gives a save's bytes), or None: whether a revert loses the file as it
    stands, or it is kept to come back to."""
    if current is None:
        return None
    for point in saves:
        try:
            if read(point["id"]) == current:
                return point["id"]
        except (OSError, SaveNotFound):
            continue
    return None
