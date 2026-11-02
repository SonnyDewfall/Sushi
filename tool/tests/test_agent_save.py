"""Saving the running rig from the agent (S4): POST /rig/save, and what it
does to the operation log, undo and the state file.

Against real rig files in a temporary checkout (`ui_fake.RIG_FILE`, which is
`ui_fake.UiRig`'s rig written as one) and a fake capture: `UiRig` stands in
for Sushi, as `agent.Live` sees it, and the agent captures it with the
`snapshot` every live read uses. The fold into the file is `save.save_rig`
and `rigfile.write_rig`, run for real, so a comment surviving is a comment
that survived `write_rig`. The amp is the agent's own record of what it sent
(amp_live.py) over `carla_fake.FakeCarla`.

NOT shown: any of it against a live Sushi. Whether a capture of a real rig
folds back into its file as these do rests on `capture` and `fold_state`,
each tested in its own file and neither run here against Sushi.
"""

from __future__ import annotations

import asyncio
import copy
import subprocess
import threading
import time

import pytest
import yaml

pytest.importorskip("aiohttp")
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from sushi_rig import agent as agent_module, midi_listener  # noqa: E402
from sushi_rig.agent import Agent, Switching, pick_undo, save_refusal, save_request  # noqa: E402
from sushi_rig.agent import AgentError, saved_as_state  # noqa: E402

from carla_fake import FakeCarla  # noqa: E402
from ui_fake import RIG_FILE, UiRig  # noqa: E402


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


AMP_CONFIG = {"tracks": [], "_amp": {"plugin": "nam", "model": "amp/models/First.nam",
                                     "parameters": {"Input Lvl": -2.0, "Output Lvl": 6.0}}}


class World:
    """The state file, `describe_rig`'s answer and the running config, for a
    rig of channels up headless from config/rigs/electric_board.yaml, with
    an amp unless `amp` is False."""

    def __init__(self, root, *, mode="headless", amp=True, rig="config/rigs/electric_board.yaml"):
        self.root = root
        self.state = {
            "supervisor_pid": 10, "pgid": 10, "mode": mode, "config_name": "electric_board",
            "config": "/run/rigs/electric_board.json", "started_at": "2026-11-02T10:00:00+00:00",
            "amp_model": "amp/models/First.nam" if amp else None, "amp_requested": amp,
            "amp_model_requested": None, "children": {"sushi": 11, **({"amp": 12} if amp else {})},
        }
        if rig is not None:
            self.state["rig"] = rig
        self.running = True
        self.compiled: list = []
        self.written: list = []
        self.refuse_compile: str | None = None

    def described(self):
        if not self.running:
            return {"verdict": "absent", "running": False, "message": "No rig is running."}
        return {"verdict": "running", "running": True, "config_name": self.state["config_name"],
                "mode": self.state.get("mode"), "rig": self.state.get("rig"), "config": self.state["config"],
                "children": {n: {"pid": p, "alive": True} for n, p in self.state["children"].items()},
                "message": f"{self.state['config_name']} ({self.state.get('mode')} mode), up 2m"}

    def compile(self, name, root, machine):
        self.compiled.append((name, root))
        if self.refuse_compile:
            raise RuntimeError(self.refuse_compile)
        return {"tracks": [], "_meta": {"source": f"config/rigs/{name}.yaml"}}

    def write_config(self, name, compiled):
        self.written.append(name)
        return f"/run/rigs/{name}.json"

    def switching(self):
        return Switching(read_state=lambda: copy.deepcopy(self.state),
                         read_config=lambda path: copy.deepcopy(AMP_CONFIG),
                         write_state=lambda state: setattr(self, "state", copy.deepcopy(state)),
                         compile=self.compile, write_config=self.write_config)


@pytest.fixture
def root(tmp_path):
    rigs = tmp_path / "config" / "rigs"
    rigs.mkdir(parents=True)
    (rigs / "electric_board.yaml").write_text(RIG_FILE)
    return tmp_path


def save_agent(root, world=None, rig=None):
    world = world or World(root)
    rig = rig or UiRig()
    agent = Agent(root=root, describe=world.described, switching=world.switching(), live=rig,
                  carla=FakeCarla().carla(), run=_never,
                  # No pedals: a save-as has the listener follow the renamed rig.
                  midi=midi_listener.World(plan=lambda described, root: midi_listener.Off("no pedals here")))
    return agent, world, rig


async def _never(args):
    raise AssertionError(f"no lifecycle command is run by a save: {args}")


def calls(agent, *requests):
    """Requests against one served agent, in order, as (status, json)."""
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


def read(root, name="electric_board"):
    return (root / "config" / "rigs" / f"{name}.yaml").read_text()


def chain_entry(text, name):
    raw = yaml.safe_load(text)
    for channel in raw["channels"]:
        for entry in channel["chain"]:
            if entry["name"] == name:
                return entry
    raise AssertionError(f"{name} is not in the file")


# --------------------------------------------------------------------------- #
# The decisions, pure
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("body, name", [(None, None), ({}, None), ({"name": None}, None),
                                        ({"name": "board-two"}, "board-two")])
def test_a_save_names_its_file_or_saves_the_running_one(body, name):
    assert save_request(body) == name


@pytest.mark.parametrize("body, said", [
    ({"name": "Board Two"}, "invalid config name"),
    ({"name": "../hall"}, "invalid config name"),
    ({"name": 3}, "invalid config name"),
    ({"name": "x", "commit": True}, "must be empty"),
    (["x"], "must be empty"),
])
def test_a_name_save_would_refuse_is_refused_before_anything_is_read(body, said):
    with pytest.raises(AgentError, match=said):
        save_request(body)


def test_what_cannot_be_saved_from_here_is_refused_with_a_code_and_the_way_that_can(root):
    world = World(root)
    assert save_refusal(world.state, world.described()) is None
    world.running = False
    assert save_refusal(world.state, world.described())[1] == "no_rig"
    world = World(root, mode="panel")
    reason, code = save_refusal(world.state, world.described())
    assert code == "panel" and "does not know what the amp is at" in reason
    world = World(root, rig=None)
    reason, code = save_refusal(world.state, world.described())
    assert code == "v1" and "sushi-rig save <name> --rig" in reason and "not a rig of channels" in reason
    del world.state["mode"]
    assert save_refusal(world.state, world.described())[1] == "panel", "a state file that does not say is the panel's"


def test_a_save_as_renames_the_run_and_keeps_everything_else(root):
    state = World(root).state
    after = saved_as_state(state, "board-two", "/run/rigs/board-two.json")
    assert (after["config_name"], after["rig"], after["config"]) == (
        "board-two", "config/rigs/board-two.yaml", "/run/rigs/board-two.json")
    assert after["started_at"] == state["started_at"] and after["children"] == state["children"]
    assert "saved_as_at" in after and "switched_at" not in after, "a save-as is not a switch"
    assert saved_as_state({**state, "switched_at": "then"}, "x", "c")["switched_at"] == "then"


def test_undo_passes_over_a_save_to_the_change_before_it():
    log = [{"op": "parameter", "args": {}, "previous": 0.1},
           {"op": "save", "args": {"file": "config/rigs/a.yaml"}, "previous": "config/rigs/a.yaml"}]
    assert pick_undo(log, 4) == (4, log[0])
    assert pick_undo(log[1:], 0) is None, "a save alone is nothing to undo"
    undone = log + [{"op": "undo", "args": {}, "previous": 0.2, "undoes": 4}]
    assert pick_undo(undone, 4) is None


# --------------------------------------------------------------------------- #
# POST /rig/save into the running rig's file
# --------------------------------------------------------------------------- #

def test_a_save_writes_the_live_values_into_the_running_rigs_file_and_keeps_its_comments(root):
    agent, world, rig = save_agent(root)
    before = copy.deepcopy(world.state)
    (status, changed), (status_save, saved) = calls(
        agent,
        ("POST", "/live/parameter", {"json": {"processor": "overdrive", "parameter": "Drive", "value": 0.5}}),
        ("POST", "/rig/save", {}),
    )
    assert status == 200 and status_save == 200, saved
    assert (saved["file"], saved["how"], saved["rig"], saved["previous"]) == (
        "config/rigs/electric_board.yaml", "updated", "electric_board", "config/rigs/electric_board.yaml")
    assert saved["committed"] is False and saved["library"] is None, "a folder that is not a checkout says no commit"
    text = read(root)
    assert text.startswith("# The fake rig of ui_fake.UiRig, as a rig file.")
    assert "# the drive: a comment the save keeps" in text
    assert chain_entry(text, "overdrive")["parameters"]["Drive"] == 0.5
    assert ("snapshot",) in rig.calls, "captured through the agent's own connection"
    assert world.state == before, "a save in place changes nothing in the state file"
    assert world.compiled == [] and world.written == []


def test_the_amp_saved_is_what_the_agent_last_sent_carla(root):
    agent, world, _ = save_agent(root)
    (status, _), (_, saved) = calls(
        agent,
        ("POST", "/amp/parameter", {"json": {"name": "Output Lvl", "value": 3.5}}),
        ("POST", "/rig/save", {}),
    )
    assert status == 200
    amp = yaml.safe_load(read(root))["amp"]
    # Input from the config's `_amp`, as `up` replayed it; Output as sent;
    # Quality never sent, so the plugin's default (`amp.write_amp`).
    assert amp == {"plugin": "nam", "model": "amp/models/First.nam",
                   "parameters": {"Input Lvl": -2.0, "Output Lvl": 3.5, "Quality": 1.0}}
    assert saved["amp"]["saved"] is True and saved["amp"]["known"] == "sent"
    assert saved["entry"]["args"]["amp"] is True


def test_a_run_without_the_amp_leaves_the_files_amp_alone(root):
    (root / "config" / "rigs" / "electric_board.yaml").write_text(
        RIG_FILE + "amp:\n  plugin: nam\n  model: amp/models/Kept.nam\n  parameters: {Output Lvl: 1.0}\n")
    agent, _, _ = save_agent(root, World(root, amp=False))
    status, saved = call(agent, "POST", "/rig/save")
    assert status == 200
    assert yaml.safe_load(read(root))["amp"]["model"] == "amp/models/Kept.nam"
    assert saved["amp"]["saved"] is False and "left as it was" in saved["amp"]["why"]


def test_a_save_is_in_the_log_with_its_file_and_git_state(root):
    agent, _, _ = save_agent(root)
    (_, saved), (_, log) = calls(agent, ("POST", "/rig/save", {}), ("GET", "/live/log", {}))
    (entry,) = log["log"]
    assert entry["op"] == "save" and entry == saved["entry"]
    assert entry["args"] == {"file": "config/rigs/electric_board.yaml", "rig": "electric_board",
                             "how": "updated", "library": None, "amp": True}
    assert entry["previous"] == "config/rigs/electric_board.yaml"


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@t.invalid",
                           "-c", "commit.gpgsign=false", *args],
                          check=True, capture_output=True, text=True).stdout.strip()


def test_the_file_is_written_never_committed_and_the_answer_says_what_it_sits_on(root):
    git(root, "init", "-q")
    git(root, "add", "config")
    git(root, "commit", "-q", "-m", "the rig")
    head = git(root, "rev-parse", "HEAD")
    agent, _, _ = save_agent(root)
    status, saved_as = call(agent, "POST", "/rig/save", json={"name": "board-two"})
    assert status == 200 and saved_as["committed"] is False
    # A new file is untracked, which `library_state` does not count as dirty:
    # the page says "tracked files" for that reason.
    assert saved_as["library"] == {"commit": head, "dirty": False}
    assert git(root, "status", "--porcelain") == "?? config/rigs/board-two.yaml"
    assert git(root, "rev-parse", "HEAD") == head, "nothing was committed"


def test_a_save_into_a_tracked_file_says_the_checkout_is_dirty(root):
    git(root, "init", "-q")
    git(root, "add", "config")
    git(root, "commit", "-q", "-m", "the rig")
    head = git(root, "rev-parse", "HEAD")
    agent, _, _ = save_agent(root)
    _, (_, saved) = calls(
        agent,
        ("POST", "/live/parameter", {"json": {"processor": "overdrive", "parameter": "Drive", "value": 0.4}}),
        ("POST", "/rig/save", {}),
    )
    assert saved["library"] == {"commit": head, "dirty": True}, "the file itself is the change"
    assert saved["entry"]["args"]["library"] == saved["library"]
    assert git(root, "rev-parse", "HEAD") == head, "nothing was committed"
    assert git(root, "status", "--porcelain") == "M config/rigs/electric_board.yaml"


# --------------------------------------------------------------------------- #
# Save as
# --------------------------------------------------------------------------- #

def test_a_save_as_is_a_new_file_beside_the_rig_and_becomes_the_running_rig(root):
    agent, world, rig = save_agent(root)
    first = calls(
        agent,
        ("POST", "/live/parameter", {"json": {"processor": "overdrive", "parameter": "Drive", "value": 0.3}}),
        ("POST", "/rig/save", {"json": {"name": "board-two"}}),
        ("GET", "/health", {}),
        ("GET", "/rigs", {}),
    )
    (_, saved), (_, health), (_, rigs) = first[1:]
    assert (saved["file"], saved["how"], saved["rig"], saved["recorded"]) == (
        "config/rigs/board-two.yaml", "new", "board-two", True)
    assert chain_entry(read(root, "board-two"), "overdrive")["parameters"]["Drive"] == 0.3
    assert "name: board-two" in read(root, "board-two")
    # A new file is written from nothing (`rigfile.write_rig`): the comments
    # stay in the source, which is untouched, and the answer says so.
    assert "a comment the save keeps" not in read(root, "board-two")
    assert "comments stay in config/rigs/electric_board.yaml" in saved["note"]
    assert read(root) == RIG_FILE, "the source is untouched"
    # The state file names it, so restart brings it back and /health says it.
    assert (world.state["rig"], world.state["config_name"], world.state["config"]) == (
        "config/rigs/board-two.yaml", "board-two", "/run/rigs/board-two.json")
    assert world.compiled == [("board-two", root)] and world.written == ["board-two"]
    assert health["rig"]["config_name"] == "board-two" and health["rig"]["rig"] == "config/rigs/board-two.yaml"
    assert rigs["rigs"] == ["board-two", "electric_board"]
    from sushi_rig.rig import restart_arguments
    assert restart_arguments(world.state)[0] == "board-two" and restart_arguments(world.state)[1]["rig"]
    # And the next plain save writes there.
    rig.info["overdrive"]["Drive"]["value"] = 0.9
    status, again = call(agent, "POST", "/rig/save")
    assert status == 200 and again["file"] == "config/rigs/board-two.yaml" and again["how"] == "updated"
    assert chain_entry(read(root, "board-two"), "overdrive")["parameters"]["Drive"] == 0.9
    assert read(root) == RIG_FILE


def test_a_save_as_keeps_what_the_agent_sent_the_amp_as_sent(root):
    """The run is renamed, not restarted: the amp is not seeded again from the
    config as if `up` had replayed it."""
    agent, _, _ = save_agent(root)
    answers = calls(
        agent,
        ("POST", "/amp/parameter", {"json": {"name": "Output Lvl", "value": 3.5}}),
        ("POST", "/rig/save", {"json": {"name": "board-two"}}),
        ("GET", "/amp", {}),
    )
    out = next(p for p in answers[2][1]["parameters"] if p["name"] == "Output Lvl")
    assert (out["value"], out["source"]) == (3.5, "sent")


def test_a_name_another_rig_owns_is_refused_and_that_file_is_left_alone(root):
    other = root / "config" / "rigs" / "hall.yaml"
    other.write_text(RIG_FILE.replace("name: electric_board", "name: hall"))
    agent, world, rig = save_agent(root)
    status, answer = call(agent, "POST", "/rig/save", json={"name": "hall"})
    assert (status, answer["code"]) == (409, "taken")
    assert "a rig called hall already exists (config/rigs/hall.yaml)" in answer["reason"]
    assert "name: hall" in other.read_text() and world.state["config_name"] == "electric_board"
    assert ("snapshot",) not in rig.calls, "refused before Sushi is asked"


def test_saving_as_the_running_rigs_own_name_is_a_save_in_place(root):
    agent, world, _ = save_agent(root)
    status, answer = call(agent, "POST", "/rig/save", json={"name": "electric_board"})
    assert status == 200 and answer["how"] == "updated" and world.compiled == []


def test_a_save_as_that_cannot_become_the_running_rig_says_so_and_keeps_the_file(root):
    agent, world, _ = save_agent(root)
    world.refuse_compile = "the machine file is missing"
    before = copy.deepcopy(world.state)
    status, answer = call(agent, "POST", "/rig/save", json={"name": "board-two"})
    assert status == 500 and answer["ok"] is False and answer["recorded"] is False
    assert "saved as config/rigs/board-two.yaml, but it could not be made the running rig" in answer["reason"]
    assert "the machine file is missing" in answer["reason"]
    assert (root / "config" / "rigs" / "board-two.yaml").is_file()
    assert world.state == before
    _, health = call(agent, "GET", "/health")
    assert "could not be made the running rig" in health["agent"]["last_error"]["message"]


# --------------------------------------------------------------------------- #
# Refusals
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("world_kwargs, code", [({"mode": "panel"}, "panel"), ({"rig": None}, "v1")])
def test_panel_mode_and_a_v1_run_are_refused_and_nothing_is_written(root, world_kwargs, code):
    agent, world, rig = save_agent(root, World(root, **world_kwargs))
    status, answer = call(agent, "POST", "/rig/save")
    assert (status, answer["code"]) == (409, code)
    assert read(root) == RIG_FILE and rig.calls == []


def test_no_rig_is_refused(root):
    agent, world, _ = save_agent(root)
    world.running = False
    status, answer = call(agent, "POST", "/rig/save")
    assert (status, answer["code"]) == (409, "no_rig")


def test_a_save_is_refused_while_a_lifecycle_command_runs(root):
    agent, _, rig = save_agent(root)

    async def go():
        async with TestClient(TestServer(agent.app())) as client:
            async with agent._lock:
                agent.busy = "restart"
                response = await client.post("/rig/save")
                return response.status, await response.json()

    status, answer = asyncio.run(go())
    assert (status, answer["code"]) == (409, "busy") and rig.calls == []


@pytest.mark.parametrize("body", [{"name": "Board Two"}, {"name": "x", "and": "y"}])
def test_a_bad_body_is_400_and_touches_nothing(root, body):
    agent, _, rig = save_agent(root)
    status, _ = call(agent, "POST", "/rig/save", json=body)
    assert status == 400 and rig.calls == [] and read(root) == RIG_FILE


def test_a_plugin_removed_live_is_refused_not_kept_in_the_file_silently(root):
    agent, _, rig = save_agent(root)
    rig.chains["board"].remove("delay")
    status, answer = call(agent, "POST", "/rig/save")
    assert (status, answer["code"]) == (409, "not_saved")
    assert "the running board chain lacks delay, which electric_board.yaml has" in answer["reason"]
    assert read(root) == RIG_FILE


def test_a_plugin_added_live_is_refused_with_why(root):
    agent, _, rig = save_agent(root)
    rig.chains["board"].insert(3, "chorus")
    rig.info["chorus"] = {}
    rig.bypassed["chorus"] = False
    status, answer = call(agent, "POST", "/rig/save")
    assert (status, answer["code"]) == (409, "not_saved")
    assert "has chorus, which electric_board.yaml does not" in answer["reason"]
    assert "never which plugins a chain holds" in answer["reason"]
    assert read(root) == RIG_FILE


def test_a_write_past_its_deadline_is_504_and_said(root, monkeypatch):
    from sushi_rig import save as save_module

    real = save_module.save_rig

    def slow(*args, **kwargs):
        time.sleep(0.5)
        return real(*args, **kwargs)

    monkeypatch.setattr(save_module, "save_rig", slow)
    monkeypatch.setattr(agent_module, "SAVE_TIMEOUT", 0.1)
    agent, _, _ = save_agent(root)
    (status, answer), (_, health), (_, log) = calls(
        agent, ("POST", "/rig/save", {}), ("GET", "/health", {}), ("GET", "/live/log", {}))
    assert status == 504 and "did not finish within 0.1s, and may still" in answer["reason"]
    assert "did not finish" in health["agent"]["last_error"]["message"]
    assert log["log"] == [], "a save not known to be done is not logged"
    time.sleep(0.6)  # the write's thread, which cannot be stopped, finishes


# --------------------------------------------------------------------------- #
# Under the live lock; and undo
# --------------------------------------------------------------------------- #

class HeldCapture(UiRig):
    """A Sushi whose capture waits until `go` is set, so a test can send a
    change while a save is capturing."""

    def __init__(self):
        super().__init__()
        self.capturing = threading.Event()
        self.go = threading.Event()

    def snapshot(self, connection):
        self.capturing.set()
        self.go.wait(5)
        return super().snapshot(connection)


def test_a_change_sent_mid_capture_lands_after_the_save_and_is_unsaved(root):
    rig = HeldCapture()
    agent, _, _ = save_agent(root, rig=rig)

    async def go():
        async with TestClient(TestServer(agent.app())) as client:
            save = asyncio.ensure_future(client.post("/rig/save"))
            assert await asyncio.to_thread(rig.capturing.wait, 5)
            change = asyncio.ensure_future(client.post(
                "/live/parameter", json={"processor": "overdrive", "parameter": "Drive", "value": 0.1}))
            await asyncio.sleep(0.3)
            waited = not change.done()
            rig.go.set()
            saved, changed = await save, await change
            log = await (await client.get("/live/log")).json()
            return waited, saved.status, changed.status, log

    waited, saved, changed, log = asyncio.run(go())
    assert waited, "the change waits for the save's live lock"
    assert (saved, changed) == (200, 200)
    assert [e["op"] for e in log["log"]] == ["save", "parameter"]
    assert chain_entry(read(root), "overdrive")["parameters"]["Drive"] == 0.62, "the value before the change"


def test_undo_after_a_save_takes_back_the_change_before_it(root):
    agent, _, rig = save_agent(root)
    answers = calls(
        agent,
        ("POST", "/live/parameter", {"json": {"processor": "overdrive", "parameter": "Drive", "value": 0.5}}),
        ("POST", "/rig/save", {}),
        ("POST", "/live/undo", {}),
        ("POST", "/live/undo", {}),
        ("GET", "/live/log", {}),
    )
    (_, undone), (status, nothing), (_, log) = answers[2:]
    assert undone["undone"]["op"] == "parameter" and undone["undoes"] == 0
    assert abs(rig.info["overdrive"]["Drive"]["value"] - 0.62) < 1e-9, "the rig is back as it was"
    assert chain_entry(read(root), "overdrive")["parameters"]["Drive"] == 0.5, "the save is not undone"
    assert (status, nothing["code"]) == (409, "nothing_to_undo"), "and nothing older is left"
    assert [e["op"] for e in log["log"]] == ["parameter", "save", "undo"]
