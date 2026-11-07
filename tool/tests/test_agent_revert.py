"""Revert to a previous save (2026-11-06): the snapshots every save keeps
(saves.py), GET /rigs/<name>/saves and POST /rigs/<name>/revert.

Against real rig files in a temporary checkout with a real git repository:
`ui_fake.RIG_FILE` committed, a machine file, and the real compile
(`rig.compile_named_rig`, what `up --rig` runs), so what a revert writes is
what a restart would play. The running rig is `test_agent.RigLive`, a fake
Sushi loaded from that compile, which the agent's real live switch
(`_switch_held`, #112) drives step by step, so "the rig plays the reverted
file" is a reading of the fake after the switch, not a word in an answer.

NOT shown: any of it against a live Sushi. The live switch's own tests say
what of Sushi it rests on.
"""

from __future__ import annotations

import asyncio
import copy
import json
import os
import subprocess

import pytest
import yaml

pytest.importorskip("aiohttp")
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from sushi_rig import midi_listener, rigfile, saves  # noqa: E402
from sushi_rig.agent import Agent, AgentError, Switching, revert_refusal, revert_request  # noqa: E402
from sushi_rig.rig import compile_named_rig  # noqa: E402

from test_agent import FakeRunner, RigLive  # noqa: E402
from ui_fake import MACHINE_FILE, RIG_FILE  # noqa: E402


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


GIT = ["-c", "user.name=revert test", "-c", "user.email=revert@test.invalid", "-c", "commit.gpgsign=false"]


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *GIT, *args], check=True, capture_output=True,
                          text=True).stdout


class World:
    """The state file and the compiled configs, for electric_board up
    headless from its file, with no amp; the compile is the real one."""

    def __init__(self, root):
        self.root = root
        self.machine = root / "machines" / "box.yaml"
        self.state = {
            "supervisor_pid": 10, "pgid": 10, "mode": "headless", "config_name": "electric_board",
            "config": "/run/rigs/electric_board.json", "rig": "config/rigs/electric_board.yaml",
            "started_at": "2026-11-06T10:00:00+00:00", "amp_requested": False, "amp_model": None,
            "amp_model_requested": None, "children": {"sushi": 11}, "machine": str(self.machine),
        }
        self.configs = {self.state["config"]: compile_named_rig("electric_board", root, self.machine)}

    def write_config(self, name, compiled):
        path = f"/run/rigs/{name}.json"
        self.configs[path] = copy.deepcopy(compiled)
        return path

    def write_state(self, state):
        self.state = copy.deepcopy(state)

    def described(self):
        return {"verdict": "running", "running": True, "config_name": self.state["config_name"],
                "mode": "headless", "rig": self.state["rig"], "config": self.state["config"],
                "children": {"sushi": {"pid": 11, "alive": True}},
                "message": "electric_board (headless mode), up 2m"}

    def switching(self):
        return Switching(read_state=lambda: copy.deepcopy(self.state), write_state=self.write_state,
                         read_config=lambda path: copy.deepcopy(self.configs[path]),
                         write_config=self.write_config)


@pytest.fixture
def root(tmp_path):
    rigs = tmp_path / "config" / "rigs"
    rigs.mkdir(parents=True)
    (rigs / "electric_board.yaml").write_text(RIG_FILE)
    (rigs / "vocal_only.yaml").write_text(RIG_FILE.replace("name: electric_board", "name: vocal_only"))
    (tmp_path / "machines").mkdir()
    (tmp_path / "machines" / "box.yaml").write_text(MACHINE_FILE)
    git(tmp_path, "init", "-q")
    git(tmp_path, "add", "config", "machines")
    git(tmp_path, "commit", "-q", "-m", "the rigs")
    return tmp_path


def make_agent(root):
    world = World(root)
    fake = RigLive(world.configs[world.state["config"]])
    runner = FakeRunner()
    agent = Agent(root=root, describe=world.described, switching=world.switching(), live=fake, run=runner,
                  midi=midi_listener.World(plan=lambda described, root: midi_listener.Off("no pedals here")))
    return agent, world, fake, runner


def calls(agent, *requests):
    async def go():
        out = []
        async with TestClient(TestServer(agent.app())) as client:
            for method, path, kwargs in requests:
                response = await client.request(method, path, **kwargs)
                out.append((response.status, await response.json()))
        return out

    return asyncio.run(go())


def call(agent, method, path, **kwargs):
    return calls(agent, (method, path, kwargs))[0]


def rig_bytes(root, name="electric_board"):
    return (root / "config" / "rigs" / f"{name}.yaml").read_bytes()


def master_gain(db):
    return rigfile.level_to_gain(db)


# --------------------------------------------------------------------------- #
# The decisions, pure
# --------------------------------------------------------------------------- #

def test_ids_sort_as_times_and_never_collide():
    from datetime import datetime, timezone

    now = datetime(2026, 11, 6, 10, 0, 0, 123456, tzinfo=timezone.utc)
    first = saves.new_id(now, set())
    assert first == "20261106T100000.123456Z" and saves.ID_PATTERN.match(first)
    assert saves.new_id(now, {first}) == first + "-1"
    assert saves.new_id(now, {first, first + "-1"}) == first + "-2"


def test_the_newest_fifty_are_kept_and_the_oldest_go_first():
    ids = [f"2026110{d}T0000{n:02d}.000000Z" for d in range(1, 4) for n in range(20)]
    gone = saves.to_prune(ids, 50)
    assert len(gone) == 10 and gone == sorted(ids)[:10]
    assert saves.to_prune(ids[:5], 50) == []
    assert saves.KEEP == 50


def test_git_log_is_read_as_commit_points():
    sha = "a" * 40
    out = f"{sha}\x1f2026-11-05T09:00:00+00:00\x1fTune the board\nnot a line\n"
    assert saves.parse_log(out) == [{"id": f"commit:{sha}", "kind": "commit", "sha": sha,
                                    "at": "2026-11-05T09:00:00+00:00", "subject": "Tune the board"}]


def test_saves_and_commits_are_one_list_newest_first():
    points = saves.merged([{"id": "s1", "at": "2026-11-06T10:00:00+00:00"}],
                          [{"id": "c1", "at": "2026-11-06T11:00:00+01:00"}, {"id": "c2", "at": "2026-11-07T00:00:00Z"}])
    assert [p["id"] for p in points] == ["c2", "s1", "c1"]


@pytest.mark.parametrize("body, said", [
    ({}, "must be a revert point"), ({"save": "../x"}, "must be a revert point"),
    ({"save": "commit:abc"}, "must be a revert point"), ({"save": "20261106T100000.123456Z", "go": 1}, "the body"),
    ({"save": "20261106T100000.123456Z", "discard": "yes"}, "true or false"), ([], "the body"),
])
def test_a_revert_names_a_point_by_its_shape_and_nothing_else(body, said):
    with pytest.raises(AgentError, match=said):
        revert_request(body)


def test_a_revert_point_is_a_save_or_a_commit_and_discard_is_explicit():
    assert revert_request({"save": "20261106T100000.123456Z"}) == ("20261106T100000.123456Z", False)
    assert revert_request({"save": "commit:" + "f" * 40, "discard": True}) == ("commit:" + "f" * 40, True)


def test_a_revert_waits_for_unsaved_work_as_an_edit_does():
    state = {"rig": "config/rigs/electric_board.yaml", "mode": "headless", "started_at": "2026-11-06T10:00:00+00:00"}
    change = {"op": "parameter", "args": {"processor": "overdrive", "parameter": "Drive"},
              "at": "2026-11-06T10:05:00+00:00"}
    assert revert_refusal(state, []) is None
    reason, code = revert_refusal(state, [change])
    assert code == "unsaved" and "1 change" in reason and '{"discard": true}' in reason
    saved = {"op": "save", "args": {}, "at": "2026-11-06T10:06:00+00:00"}
    assert revert_refusal(state, [change, saved]) is None, "a save since: nothing to lose"
    assert revert_refusal({**state, "mode": "panel"}, [])[1] == "panel"


# --------------------------------------------------------------------------- #
# Snapshots, on the filesystem
# --------------------------------------------------------------------------- #

def test_a_snapshot_keeps_the_bytes_and_the_checkout_stays_clean(root):
    data = rig_bytes(root)
    point = saves.keep(root, "electric_board", data, {"file": "config/rigs/electric_board.yaml", "how": "updated"})
    assert saves.snapshot_bytes(root, "electric_board", point["id"]) == data
    assert (root / ".sushi-rig" / ".gitignore").read_text().splitlines()[-1] == "*"
    assert git(root, "status", "--porcelain", "--untracked-files=all") == "", "git status stays clean"
    listed, problems = saves.snapshots(root, "electric_board")
    assert [p["id"] for p in listed] == [point["id"]] and problems == []


def test_only_the_newest_snapshots_are_kept(root):
    from datetime import datetime, timedelta, timezone

    start = datetime(2026, 11, 6, tzinfo=timezone.utc)
    for n in range(7):
        saves.keep(root, "electric_board", f"n: {n}\n".encode(), {}, now=start + timedelta(seconds=n), limit=5)
    listed, _ = saves.snapshots(root, "electric_board")
    assert len(listed) == 5
    assert saves.snapshot_bytes(root, "electric_board", listed[0]["id"]) == b"n: 6\n", "newest first"
    assert saves.snapshot_bytes(root, "electric_board", listed[-1]["id"]) == b"n: 2\n"
    assert len(list((root / ".sushi-rig" / "saves" / "electric_board").iterdir())) == 10


def test_a_snapshot_without_its_file_is_said_not_listed(root):
    point = saves.keep(root, "electric_board", b"x: 1\n", {})
    (root / ".sushi-rig" / "saves" / "electric_board" / f"{point['id']}.yaml").unlink()
    listed, problems = saves.snapshots(root, "electric_board")
    assert listed == [] and "the file it kept is not" in problems[0]
    with pytest.raises(saves.SaveNotFound):
        saves.snapshot_bytes(root, "electric_board", point["id"])


# --------------------------------------------------------------------------- #
# The routes, against the real compile and the live switch
# --------------------------------------------------------------------------- #

def test_every_save_keeps_a_snapshot_of_the_file_as_it_wrote_it(root):
    agent, world, fake, _ = make_agent(root)
    status, body = call(agent, "POST", "/rig/save")
    assert status == 200, body
    assert body["snapshot"]["kept"] is True and body["snapshot"]["keep"] == 50
    assert saves.snapshot_bytes(root, "electric_board", body["snapshot"]["id"]) == rig_bytes(root)
    meta = json.loads((root / ".sushi-rig" / "saves" / "electric_board" / f"{body['snapshot']['id']}.json").read_text())
    assert meta["library"]["commit"] == git(root, "rev-parse", "HEAD").strip(), "the commit it sat on"
    assert meta["rig"] == "electric_board" and meta["how"] == "updated" and meta["at"]
    assert git(root, "status", "--porcelain", "--untracked-files=all").strip() in ("", "M config/rigs/electric_board.yaml")


def test_a_save_as_keeps_its_snapshot_under_the_new_rig(root):
    agent, world, fake, _ = make_agent(root)
    status, body = call(agent, "POST", "/rig/save", json={"name": "board_two"})
    assert status == 200 and body["how"] == "new", body
    listed, _ = saves.snapshots(root, "board_two")
    assert [p["id"] for p in listed] == [body["snapshot"]["id"]]
    assert listed[0]["saved_as"] == "config/rigs/electric_board.yaml"
    assert saves.snapshot_bytes(root, "board_two", listed[0]["id"]) == rig_bytes(root, "board_two")


def test_a_snapshot_that_cannot_be_kept_is_said_and_the_save_stands(root, monkeypatch):
    agent, world, fake, _ = make_agent(root)

    def full(*args, **kwargs):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(saves, "keep", full)
    status, body = call(agent, "POST", "/rig/save")
    assert status == 200 and body["ok"], body
    assert body["snapshot"]["kept"] is False and "No space left" in body["snapshot"]["why"]
    _, health = call(agent, "GET", "/health")
    assert "snapshot for Revert could not be kept" in health["agent"]["last_error"]["message"]


def test_the_saves_are_listed_newest_first_with_the_files_commits(root):
    agent, world, fake, _ = make_agent(root)
    (_, first), _, (_, second), (status, listed) = calls(
        agent, ("POST", "/rig/save", {}),
        ("POST", "/live/parameter", {"json": {"processor": "master", "parameter": "gain", "value": master_gain(-6)}}),
        ("POST", "/rig/save", {}), ("GET", "/rigs/electric_board/saves", {}))
    assert status == 200, listed
    kinds = [p["kind"] for p in listed["points"]]
    assert kinds == ["save", "save", "commit"], "the two saves, then the commit, by time"
    assert [p["id"] for p in listed["points"][:2]] == [second["snapshot"]["id"], first["snapshot"]["id"]]
    commit = listed["points"][2]
    assert commit["sha"] == git(root, "rev-parse", "HEAD").strip() and commit["subject"] == "the rigs"
    assert listed["running"] is True and listed["keep"] == 50 and listed["kept_in"] == ".sushi-rig/saves/electric_board"
    assert listed["current"]["kept_as"] == second["snapshot"]["id"], "the file now is the newest save"


def test_save_change_save_and_revert_to_the_first_plays_the_first(root):
    """The test the request names: the file is the first save's, byte for
    byte, and the running rig's values are the first save's, by the live
    switch, with nothing restarted."""
    agent, world, fake, runner = make_agent(root)
    status, first = call(agent, "POST", "/rig/save")
    assert status == 200, first
    first_bytes = rig_bytes(root)
    first_values = copy.deepcopy(fake.values)
    # A change that a save writes: the master's level and a channel's.
    for processor, gain in (("master", master_gain(-6)), ("vocal_send", master_gain(-12))):
        status, body = call(agent, "POST", "/live/parameter",
                            json={"processor": processor, "parameter": "gain", "value": gain})
        assert status == 200, body
    status, second = call(agent, "POST", "/rig/save")
    assert status == 200, second
    written = yaml.safe_load(rig_bytes(root))
    assert written["master"]["level"] == -6.0 and written["channels"][1]["level"] == -12.0
    assert rig_bytes(root) != first_bytes

    status, body = call(agent, "POST", "/rigs/electric_board/revert", json={"save": first["snapshot"]["id"]})
    assert status == 200, body
    assert body["how"] == "live", body
    assert runner.calls == [], "nothing was restarted: a level is a live step"
    assert rig_bytes(root) == first_bytes, "the file is the first save's, byte for byte"
    assert rig_bytes(root) == saves.snapshot_bytes(root, "electric_board", first["snapshot"]["id"])
    for processor in ("master", "vocal_send"):
        assert fake.values[processor]["gain"] == pytest.approx(first_values[processor]["gain"], abs=1e-6)
    assert body["point"]["kind"] == "save" and body["running"] is True
    entry = body["entry"]
    assert entry["op"] == "rig" and entry["args"]["revert"] == first["snapshot"]["id"]
    # The revert is a baseline: nothing is unsaved after it, and undo stops at it.
    status, undo = call(agent, "POST", "/live/undo")
    assert status == 409, undo


def test_a_revert_with_unsaved_work_is_refused_and_allowed_with_discard(root):
    agent, world, fake, _ = make_agent(root)
    _, first = call(agent, "POST", "/rig/save")
    before = rig_bytes(root)
    call(agent, "POST", "/live/parameter", json={"processor": "master", "parameter": "gain", "value": master_gain(-3)})
    status, body = call(agent, "POST", "/rigs/electric_board/revert", json={"save": first["snapshot"]["id"]})
    assert status == 409 and body["code"] == "unsaved", body
    assert "master gain" in body["reason"]
    assert fake.values["master"]["gain"] == pytest.approx(master_gain(-3)), "nothing was changed"
    status, body = call(agent, "POST", "/rigs/electric_board/revert",
                        json={"save": first["snapshot"]["id"], "discard": True})
    assert status == 200 and body["discarded"] is True, body
    assert rig_bytes(root) == before
    assert fake.values["master"]["gain"] == pytest.approx(master_gain(0))


def test_a_revert_to_a_commit_writes_the_committed_file(root):
    agent, world, fake, _ = make_agent(root)
    call(agent, "POST", "/live/parameter", json={"processor": "master", "parameter": "gain", "value": master_gain(-6)})
    call(agent, "POST", "/rig/save")
    assert rig_bytes(root) != RIG_FILE.encode()
    sha = git(root, "rev-parse", "HEAD").strip()
    status, body = call(agent, "POST", "/rigs/electric_board/revert", json={"save": f"commit:{sha}"})
    assert status == 200, body
    assert rig_bytes(root) == RIG_FILE.encode(), "the committed file, comments and all"
    assert body["library"]["file"] == "unchanged"
    assert fake.values["master"]["gain"] == pytest.approx(master_gain(0))


def test_a_rig_that_is_not_running_is_reverted_in_its_file_alone(root):
    agent, world, fake, runner = make_agent(root)
    point = saves.keep(root, "vocal_only", b"kind: rig\n", {})  # not a rig any more: refused, nothing written
    status, body = call(agent, "POST", "/rigs/vocal_only/revert", json={"save": point["id"]})
    assert status == 409 and body["code"] == "rig_problem", body
    assert rig_bytes(root, "vocal_only") == RIG_FILE.replace("name: electric_board", "name: vocal_only").encode()
    good = RIG_FILE.replace("name: electric_board", "name: vocal_only").replace("level: -6.0", "level: -9.0")
    point = saves.keep(root, "vocal_only", good.encode(), {})
    status, body = call(agent, "POST", "/rigs/vocal_only/revert", json={"save": point["id"]})
    assert status == 200 and body["running"] is False and "not the running rig" in body["note"], body
    assert rig_bytes(root, "vocal_only") == good.encode()
    assert fake.writes() == [] and runner.calls == []


def test_a_point_that_is_not_there_is_404_and_a_rig_that_is_not_one_too(root):
    agent, *_ = make_agent(root)
    status, body = call(agent, "POST", "/rigs/electric_board/revert", json={"save": "20261106T100000.123456Z"})
    assert status == 404 and "may have been pruned" in body["reason"]
    status, body = call(agent, "POST", "/rigs/electric_board/revert", json={"save": "commit:" + "0" * 40})
    assert status == 404
    assert call(agent, "GET", "/rigs/nope/saves")[0] == 404


def test_a_reverted_file_keeps_its_permissions(root):
    agent, *_ = make_agent(root)
    _, first = call(agent, "POST", "/rig/save")
    path = root / "config" / "rigs" / "electric_board.yaml"
    os.chmod(path, 0o640)
    status, body = call(agent, "POST", "/rigs/electric_board/revert", json={"save": first["snapshot"]["id"]})
    assert status == 200, body
    assert (path.stat().st_mode & 0o777) == 0o640
