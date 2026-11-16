"""Deleting a rig file from the web UI, and Undo (#129): POST
/rigs/{name}/delete and POST /rigs/{name}/restore (agent.py's rig bin block,
rigbin.py's names and moves).

Against rig files in a temporary checkout, in a git repository: copies of
three frozen rigs (rig_fixtures.py; config/rigs/ itself changes with every
save) and one more, never committed. The running rig is a state file and
`describe_rig`'s answer held in memory (`World`). Nothing here compiles a
rig or reaches Sushi: a delete only moves a file.

NOT shown: any of it on the rig machine, or with a Program Change from a
real pedal after a delete. That the numbers move is shown here through
`midi_listener.program_order`, the function a Program Change counts through.
"""

from __future__ import annotations

import asyncio
import copy
import shutil
import subprocess
from datetime import datetime, timezone

import pytest

pytest.importorskip("aiohttp")
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402
from yarl import URL  # noqa: E402

from sushi_rig import midi_listener, rigbin  # noqa: E402
from sushi_rig.agent import Agent, Switching, bin_refusal, programs_deleted  # noqa: E402
from sushi_rig.render import rigs_to_check  # noqa: E402

import rig_fixtures  # noqa: E402
from rig_fixtures import FROZEN as RIGS  # noqa: E402

COMMITTED_RIGS = ("accustomed", "electric-chorus-2", "guitar_vocal")
LOOSE = "zz-sketch"  # a rig made since the last commit: sorts last


@pytest.fixture(autouse=True)
def _agents_release_their_pools(monkeypatch):
    made = []
    original = Agent.__init__

    def recording(self, *args, **kwargs):
        original(self, *args, **kwargs)
        made.append(self)

    monkeypatch.setattr(Agent, "__init__", recording)
    yield
    for agent in made:
        agent._executor.shutdown(wait=True, cancel_futures=True)


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@t.invalid",
                           "-c", "commit.gpgsign=false", *args],
                          check=True, capture_output=True, text=True).stdout


@pytest.fixture
def root(tmp_path):
    """Three committed rigs and one untracked, in a git repository with no
    .gitignore of its own: the bin must keep itself out of git."""
    rigs = tmp_path / "config" / "rigs"
    rigs.mkdir(parents=True)
    for name in COMMITTED_RIGS:
        shutil.copy(RIGS / f"{name}.yaml", rigs / f"{name}.yaml")
    git(tmp_path, "init", "-q")
    git(tmp_path, "add", "config")
    git(tmp_path, "commit", "-q", "-m", "the rigs")
    shutil.copy(RIGS / "electric-delay.yaml", rigs / f"{LOOSE}.yaml")
    return tmp_path


class World:
    """The state file and `describe_rig`'s answer: rig `rig` up (None: no rig)."""

    def __init__(self, root, rig="guitar_vocal"):
        self.rig = rig
        self.state = {} if rig is None else {
            "supervisor_pid": 10, "pgid": 10, "mode": "headless", "config_name": rig,
            "config": f"/run/rigs/{rig}.json", "rig": f"config/rigs/{rig}.yaml",
            "started_at": "2026-11-16T10:00:00+00:00", "children": {"sushi": 11}}

    def described(self):
        if self.rig is None:
            return {"verdict": "absent", "running": False, "message": "No rig is running."}
        return {"verdict": "running", "running": True, "config_name": self.rig, "mode": "headless",
                "rig": self.state["rig"], "config": self.state["config"], "message": f"{self.rig} (headless mode)"}

    def switching(self):
        return Switching(read_state=lambda: copy.deepcopy(self.state),
                         read_config=lambda path: pytest.fail("a delete reads no compiled config"),
                         write_state=lambda state: pytest.fail("a delete writes no state file"),
                         write_config=lambda name, compiled: pytest.fail("nor a compiled config"),
                         compile=lambda name, root, machine: pytest.fail("nor compiles a rig"))


class _NoSushi:
    def __getattr__(self, name):
        raise AssertionError(f"a rig bin route reached Sushi ({name})")


async def _never(args):
    raise AssertionError(f"no lifecycle command is run by a delete: {args}")


def bin_agent(root, rig="guitar_vocal"):
    world = World(root, rig)
    return Agent(root=root, describe=world.described, switching=world.switching(), run=_never, live=_NoSushi(),
                 library=lambda: {"plugins": [], "problems": []},
                 midi=midi_listener.World(plan=lambda described, root: midi_listener.Off("no pedals here")))


def calls(agent, *requests, hold_lock=False):
    async def go():
        out = []
        async with TestClient(TestServer(agent.app())) as client:
            if hold_lock:
                await agent._lock.acquire()
                agent.busy = "up"
            try:
                for method, path, kwargs in requests:
                    response = await client.request(method, URL(path, encoded=True), **kwargs)
                    out.append((response.status, await response.json()))
            finally:
                if hold_lock:
                    agent._lock.release()
        return out

    return asyncio.run(go())


def call(agent, method, path, **kwargs):
    return calls(agent, (method, path, kwargs))[0]


def files_of(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*")
            if p.is_file() and ".git" not in p.parts}


def binned(root):
    folder = root / "config" / "rigs" / ".deleted"
    return sorted(p.name for p in folder.iterdir() if p.name != ".gitignore") if folder.is_dir() else []


# --------------------------------------------------------------------------- #
# The move
# --------------------------------------------------------------------------- #

def test_a_delete_moves_the_file_into_the_bin_and_out_of_rigs(root):
    agent = bin_agent(root)
    before = (root / "config/rigs/accustomed.yaml").read_bytes()
    status, body = call(agent, "POST", "/rigs/accustomed/delete")
    assert status == 200, body
    assert body["how"] == "deleted" and body["committed"] is False
    entry = body["binned"]["entry"]
    assert rigbin.ENTRY.fullmatch(entry)["rig"] == "accustomed"
    assert body["binned"]["file"] == f"config/rigs/.deleted/{entry}"
    assert not (root / "config/rigs/accustomed.yaml").exists()
    assert (root / "config/rigs/.deleted" / entry).read_bytes() == before, "moved byte for byte"
    assert body["undo"] == {"path": "/rigs/accustomed/restore", "json": {"entry": entry}}
    status, listed = call(agent, "GET", "/rigs")
    assert listed["rigs"] == ["electric-chorus-2", "guitar_vocal", LOOSE]


def test_the_bin_is_in_no_list_of_rigs(root):
    """`/rigs` and the Program Change table (`program_order`), and the
    sound references' list of rigs (`render.rigs_to_check`), see none of
    it, nor do the tests over every committed rig (`rig_fixtures.committed`)."""
    agent = bin_agent(root)
    call(agent, "POST", "/rigs/accustomed/delete")
    call(agent, "POST", "/rigs/electric-chorus-2/delete")
    assert len(binned(root)) == 2
    expected = ["guitar_vocal", LOOSE]
    assert call(agent, "GET", "/rigs")[1]["rigs"] == expected
    assert midi_listener.program_order(root) == expected
    assert midi_listener.programs_table(midi_listener.program_order(root)) == [
        {"program": 0, "rig": "guitar_vocal"}, {"program": 1, "rig": LOOSE}]
    assert [p.stem for p in rigs_to_check(root)] == expected
    original = rig_fixtures.COMMITTED
    rig_fixtures.COMMITTED = root / "config" / "rigs"
    try:
        assert [p.stem for p in rig_fixtures.committed()] == expected
    finally:
        rig_fixtures.COMMITTED = original


def test_nothing_is_committed_and_the_bin_keeps_itself_out_of_git(root):
    agent = bin_agent(root)
    head = git(root, "rev-parse", "HEAD")
    call(agent, "POST", "/rigs/accustomed/delete")
    call(agent, "POST", f"/rigs/{LOOSE}/delete")
    assert git(root, "rev-parse", "HEAD") == head
    assert git(root, "status", "--porcelain", "--untracked-files=all").splitlines() == [
        " D config/rigs/accustomed.yaml"], "a deleted committed rig, and no bin, in git status"
    assert (root / "config/rigs/.deleted/.gitignore").read_text().splitlines()[-1] == "*"


def test_a_committed_rig_says_git_checkout_brings_it_back_too(root):
    agent = bin_agent(root)
    status, body = call(agent, "POST", "/rigs/accustomed/delete")
    assert body["git"] == {"committed": True, "same": True, "restore": "git checkout HEAD -- config/rigs/accustomed.yaml"}
    assert "`git checkout HEAD -- config/rigs/accustomed.yaml` also brings back" in body["note"]
    git(root, "checkout", "HEAD", "--", "config/rigs/accustomed.yaml")
    assert (root / "config/rigs/accustomed.yaml").read_bytes() == (RIGS / "accustomed.yaml").read_bytes()


def test_a_committed_rig_changed_since_says_only_the_bin_has_the_changes(root):
    path = root / "config/rigs/accustomed.yaml"
    path.write_text(path.read_text() + "# a comment since the commit\n")
    status, body = call(bin_agent(root), "POST", "/rigs/accustomed/delete")
    assert body["git"]["committed"] is True and body["git"]["same"] is False
    assert "without the changes made since, which only the bin keeps" in body["note"]


def test_a_rig_never_committed_says_the_bin_is_its_only_copy(root):
    status, body = call(bin_agent(root), "POST", f"/rigs/{LOOSE}/delete")
    assert status == 200
    assert body["git"] == {"committed": False, "same": None, "restore": None}
    assert "the bin holds the only copy" in body["note"]


def test_outside_a_checkout_what_git_holds_is_said_as_not_known(tmp_path):
    rigs = tmp_path / "config" / "rigs"
    rigs.mkdir(parents=True)
    shutil.copy(RIGS / "accustomed.yaml", rigs / "accustomed.yaml")
    status, body = call(bin_agent(tmp_path, rig=None), "POST", "/rigs/accustomed/delete")
    assert status == 200 and body["git"] is None
    assert "git could not be read here" in body["git_note"]


def test_two_deletes_of_one_name_in_one_second_never_replace_each_other(tmp_path):
    rigs = tmp_path / "config" / "rigs"
    rigs.mkdir(parents=True)
    now = datetime(2026, 11, 16, 10, 15, 2, tzinfo=timezone.utc)
    made = []
    for text in ("first\n", "second\n", "third\n"):
        (rigs / "a-b.yaml").write_text(text)
        made.append(rigbin.move_to_bin(tmp_path, "a-b", now))
    assert [p.name for p in made] == ["a-b-20261116T101502Z.yaml", "a-b-20261116T101502Z-2.yaml",
                                      "a-b-20261116T101502Z-3.yaml"]
    assert [p.read_text() for p in rigbin.entries(tmp_path, "a-b")] == ["third\n", "second\n", "first\n"]
    assert rigbin.entries(tmp_path, "a") == [], "a-b's entries are not a's"


# --------------------------------------------------------------------------- #
# Program Change numbers
# --------------------------------------------------------------------------- #

def test_a_delete_says_which_program_numbers_move(root):
    agent = bin_agent(root)
    status, body = call(agent, "POST", "/rigs/electric-chorus-2/delete")
    assert body["programs"]["moved"] == [{"rig": "guitar_vocal", "from": 2, "to": 1}, {"rig": LOOSE, "from": 3, "to": 2}]
    assert body["programs"]["before"][1] == {"program": 1, "rig": "electric-chorus-2"}
    assert body["programs"]["after"] == [{"program": 0, "rig": "accustomed"}, {"program": 1, "rig": "guitar_vocal"},
                                         {"program": 2, "rig": LOOSE}]
    assert "guitar_vocal moves from program 2 to 1" in body["programs"]["note"]


def test_a_delete_of_the_last_rig_moves_no_number_and_says_none(root):
    status, body = call(bin_agent(root), "POST", f"/rigs/{LOOSE}/delete")
    assert status == 200 and "programs" not in body


def test_programs_deleted_is_none_unless_a_number_moves():
    assert programs_deleted(["a", "b"], ["a"], "b") is None
    moved = programs_deleted(["a", "b", "c"], ["b", "c"], "a")
    assert moved["moved"] == [{"rig": "b", "from": 1, "to": 0}, {"rig": "c", "from": 2, "to": 1}]
    assert "moves 2 rigs down one" in moved["note"]


# --------------------------------------------------------------------------- #
# The dry run: the confirm's question
# --------------------------------------------------------------------------- #

def test_a_dry_run_moves_nothing_and_answers_as_the_delete_would(root):
    agent = bin_agent(root)
    before = files_of(root)
    status, body = call(agent, "POST", "/rigs/accustomed/delete", json={"dry_run": True})
    assert status == 200 and body["how"] == "dry_run"
    assert files_of(root) == before
    assert "binned" not in body and "undo" not in body
    assert [m["rig"] for m in body["programs"]["moved"]] == ["electric-chorus-2", "guitar_vocal", LOOSE]
    assert body["git"]["committed"] is True and body["git"]["same"] is True


# --------------------------------------------------------------------------- #
# Refusals
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("dry_run", [False, True])
def test_the_running_rig_is_refused_and_left_where_it_is(root, dry_run):
    agent = bin_agent(root, rig="guitar_vocal")
    before = files_of(root)
    status, body = call(agent, "POST", "/rigs/guitar_vocal/delete", json={"dry_run": dry_run})
    assert status == 409 and body["code"] == "running"
    assert "guitar_vocal is the running rig" in body["reason"] and "Switch to another rig" in body["reason"]
    assert files_of(root) == before


def test_with_no_rig_running_any_rig_may_go(root):
    status, body = call(bin_agent(root, rig=None), "POST", "/rigs/guitar_vocal/delete")
    assert status == 200


def test_the_running_rig_is_the_one_health_names_not_the_config_name(root):
    """`/health`'s `rig` is the state file's rig file: a run whose config
    is called something else still holds that file."""
    run = {"described": {"running": True, "rig": "config/rigs/accustomed.yaml", "config_name": "other"}, "state": {}}
    assert bin_refusal(run, root / "config/rigs/accustomed.yaml", root)[1] == "running"
    assert bin_refusal(run, root / "config/rigs/guitar_vocal.yaml", root) is None
    v1 = {"described": {"running": True, "rig": None, "config_name": "accustomed"}, "state": {}}
    assert bin_refusal(v1, root / "config/rigs/accustomed.yaml", root) is None, "a v1 run holds no rig file"


@pytest.mark.parametrize("path", [
    "/rigs/%2E%2E/delete", "/rigs/..%2Fmachines%2Fx/delete", "/rigs/.deleted/delete", "/rigs/.hidden/delete",
    "/rigs/a..b/delete", "/rigs/a%5Cb/delete", "/rigs/%2Fetc%2Fpasswd/delete",
    "/rigs/%2E%2E/restore", "/rigs/.deleted/restore", "/rigs/..%2Fx/restore", "/rigs/a%5Cb/restore",
])
def test_a_name_that_is_not_plain_is_refused_with_nothing_touched(root, path):
    agent = bin_agent(root, rig=None)
    before = files_of(root)
    status, body = call(agent, "POST", path)
    assert status == 409 and body["code"] == "name", body
    assert files_of(root) == before


def test_a_rig_rigs_does_not_list_is_not_found(root):
    status, body = call(bin_agent(root), "POST", "/rigs/nothing-here/delete")
    assert status == 404


def test_a_body_of_the_wrong_shape_is_400(root):
    agent = bin_agent(root, rig=None)
    assert call(agent, "POST", "/rigs/accustomed/delete", json={"dry_run": "yes"})[0] == 400
    assert call(agent, "POST", "/rigs/accustomed/delete", json={"force": True})[0] == 400
    assert call(agent, "POST", "/rigs/accustomed/delete", json=["dry_run"])[0] == 400
    assert call(agent, "POST", "/rigs/accustomed/restore", json={"entry": 3})[0] == 400
    assert (root / "config/rigs/accustomed.yaml").is_file()


def test_a_delete_waits_for_no_lifecycle_command_it_is_refused_busy(root):
    agent = bin_agent(root, rig=None)
    [(status, body)] = calls(agent, ("POST", "/rigs/accustomed/delete", {}), hold_lock=True)
    assert status == 409 and body["code"] == "busy"
    assert (root / "config/rigs/accustomed.yaml").is_file()


# --------------------------------------------------------------------------- #
# Undo
# --------------------------------------------------------------------------- #

def test_undo_puts_the_file_back_as_it_was(root):
    agent = bin_agent(root)
    before = files_of(root)
    (_, deleted), (status, body) = calls(agent, ("POST", "/rigs/accustomed/delete", {}),
                                         ("POST", "/rigs/accustomed/restore", {"json": {"entry": None}}))
    assert status == 400, "an entry is text"
    status, body = call(agent, "POST", deleted["undo"]["path"], json=deleted["undo"]["json"])
    assert status == 200, body
    assert body["how"] == "restored" and body["file"] == "config/rigs/accustomed.yaml"
    assert body["from"] == deleted["binned"]["file"] and body["committed"] is False
    assert [m["rig"] for m in body["programs"]["moved"]] == ["electric-chorus-2", "guitar_vocal", LOOSE]
    after = files_of(root)
    after.pop("config/rigs/.deleted/.gitignore")
    assert after == before
    assert call(agent, "GET", "/rigs")[1]["rigs"] == ["accustomed", "electric-chorus-2", "guitar_vocal", LOOSE]


def test_undo_with_no_entry_puts_back_the_newest(root):
    agent = bin_agent(root, rig=None)
    path = root / "config/rigs/accustomed.yaml"
    path.write_text("older\n")
    rigbin.move_to_bin(root, "accustomed", datetime(2026, 11, 16, 9, 0, tzinfo=timezone.utc))
    path.write_text("newer\n")
    rigbin.move_to_bin(root, "accustomed", datetime(2026, 11, 16, 10, 0, tzinfo=timezone.utc))
    status, body = call(agent, "POST", "/rigs/accustomed/restore")
    assert status == 200 and body["from"] == "config/rigs/.deleted/accustomed-20261116T100000Z.yaml"
    assert path.read_text() == "newer\n"
    assert binned(root) == ["accustomed-20261116T090000Z.yaml"]


def test_undo_survives_a_new_agent_since_it_needs_only_the_bin(root):
    """The page keeps the entry it was given; the agent that answers the
    Undo need not be the one that deleted (a reload, an agent restart)."""
    status, deleted = call(bin_agent(root), "POST", "/rigs/accustomed/delete")
    status, body = call(bin_agent(root), "POST", deleted["undo"]["path"], json=deleted["undo"]["json"])
    assert status == 200 and (root / "config/rigs/accustomed.yaml").is_file()


def test_undo_is_refused_when_a_file_of_that_name_is_there_again(root):
    agent = bin_agent(root)
    status, deleted = call(agent, "POST", "/rigs/accustomed/delete")
    (root / "config/rigs/accustomed.yaml").write_text("a new one, made since\n")
    for json in (deleted["undo"]["json"], None):
        status, body = call(agent, "POST", "/rigs/accustomed/restore", json=json)
        assert status == 409 and body["code"] == "taken", body
        assert "is there again" in body["reason"] and deleted["binned"]["entry"] in body["reason"]
    assert (root / "config/rigs/accustomed.yaml").read_text() == "a new one, made since\n"
    assert binned(root) == [deleted["binned"]["entry"]], "the binned one stays"


def test_undo_is_refused_cleanly_when_the_binned_file_is_gone(root):
    agent = bin_agent(root)
    status, deleted = call(agent, "POST", "/rigs/accustomed/delete")
    (root / "config/rigs/.deleted" / deleted["binned"]["entry"]).unlink()
    before = files_of(root)
    status, body = call(agent, "POST", deleted["undo"]["path"], json=deleted["undo"]["json"])
    assert status == 409 and body["code"] == "gone" and "is not there any more" in body["reason"]
    status, body = call(agent, "POST", "/rigs/accustomed/restore")
    assert status == 409 and body["code"] == "gone"
    assert files_of(root) == before
    assert not (root / "config/rigs/accustomed.yaml").exists(), "no empty file is left where it would go"


@pytest.mark.parametrize("entry, code", [
    ("guitar_vocal-20261116T100000Z.yaml", "entry"),  # another rig's
    ("accustomed.yaml", "entry"),                     # not a bin entry's name
    ("../accustomed-20261116T100000Z.yaml", "name"),
    (".gitignore", "name"),
])
def test_undo_names_only_this_rigs_own_bin_entries(root, entry, code):
    agent = bin_agent(root, rig=None)
    call(agent, "POST", "/rigs/guitar_vocal/delete")
    before = files_of(root)
    status, body = call(agent, "POST", "/rigs/accustomed/restore", json={"entry": entry})
    assert status == 409 and body["code"] == code, body
    assert files_of(root) == before


def test_a_listed_rig_whose_name_a_new_file_could_not_have_can_still_go_and_come_back(root):
    """`/rigs/new`'s naming rule is for a name a new file is given; a delete
    names a file that is there, and one called so compiles and saves."""
    path = root / "config/rigs/Guitar.Vocal.yaml"
    shutil.copy(RIGS / "guitar_vocal.yaml", path)
    agent = bin_agent(root, rig=None)
    status, deleted = call(agent, "POST", "/rigs/Guitar.Vocal/delete")
    assert status == 200 and not path.exists()
    status, body = call(agent, "POST", deleted["undo"]["path"], json=deleted["undo"]["json"])
    assert status == 200 and path.is_file()
