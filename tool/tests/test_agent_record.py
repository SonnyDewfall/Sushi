"""Recording from the agent (S7, S8): GET /record, POST /record/start and
/record/stop, GET /takes, and what a rig stop, restart, switch or model change
does to a take that is running.

The recorder is real: `recorder_process.RecorderCore`, reached in this process
through `ui_fake.CoreLink` (every request through `parse_request`, as its
socket takes it), planning each take with `transport.record_session` from real
files in a temporary checkout (a two-channel rig, the config `up --rig` would
compile, a machine file) and writing real takes. One test runs it as a real
process over its real socket. The engine is stood in (`ui_fake.Pushing`), and
so are Sushi (`ui_fake.UiRig`), Carla (`carla_fake.FakeCarla`) and the
lifecycle commands (`World.run`).

NOT run: a take captured for real. The engine has never run against a live
PipeWire, and nothing here is JACK (recorder_process.py, capture.py).
"""

from __future__ import annotations

import asyncio
import copy
import json
import re
import sys
from datetime import datetime
from pathlib import Path

import pytest

pytest.importorskip("aiohttp")
pytest.importorskip("numpy")
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from sushi_rig import midi_listener  # noqa: E402
from sushi_rig.agent import (  # noqa: E402
    Agent, AgentError, Switching, pick_undo, record_refusal, record_request, recorder_run, take_rig_name,
)
from sushi_rig.recorder_process import RecorderCore, RecorderLink  # noqa: E402
from sushi_rig.rigfile import load_rig  # noqa: E402
from sushi_rig.supervisor import AmpRestart, Outcome  # noqa: E402
from sushi_rig.take import check_name, check_take, list_takes, load_take, start_take, take_summary  # noqa: E402

from carla_fake import FakeCarla  # noqa: E402
from test_agent_amp import nam  # noqa: E402
from test_record_session import MACHINE, TWO_CHANNELS, config_of  # noqa: E402
from ui_fake import CoreLink, FakeClock, Pushing, UiRig  # noqa: E402


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


@pytest.fixture
def root(tmp_path):
    """A checkout: the rig of channels `duo`, the config `up --rig` compiles
    from it (with an amp), a machine file, and two amp models."""
    (tmp_path / "config" / "rigs").mkdir(parents=True)
    (tmp_path / "config" / "rigs" / "duo.yaml").write_text(TWO_CHANNELS)
    (tmp_path / "machines").mkdir()
    (tmp_path / "machines" / "box.yaml").write_text(MACHINE)
    config = config_of(load_rig(tmp_path / "config" / "rigs" / "duo.yaml"))
    config["_meta"] = {"source": "config/rigs/duo.yaml"}
    config["_amp"] = {"plugin": "nam", "model": "amp/models/First.nam",
                      "parameters": {"Input Lvl": -2.0, "Output Lvl": 6.0}}
    (tmp_path / "run" / "rigs").mkdir(parents=True)
    (tmp_path / "run" / "rigs" / "duo.json").write_text(json.dumps(config))
    models = tmp_path / "amp" / "models"
    models.mkdir(parents=True)
    nam(models / "First.nam", "First")
    nam(models / "Second.nam", "Second")
    return tmp_path


class World:
    """The state file, `describe_rig`'s answer and the lifecycle commands for
    `duo`, up headless as `up --rig --headless` starts it, with a recorder
    child. `run` records each lifecycle command and does what it says to the
    rig's being up."""

    def __init__(self, root, *, mode="headless", rig="config/rigs/duo.yaml", amp=True, recorder=True):
        self.root = root
        self.state = {
            "supervisor_pid": 10, "pgid": 10, "mode": mode, "config_name": "duo",
            "config": str(root / "run" / "rigs" / "duo.json"), "machine": str(root / "machines" / "box.yaml"),
            "started_at": "2026-11-02T10:00:00+00:00", "amp_model": "amp/models/First.nam" if amp else None,
            "amp_requested": amp, "amp_model_requested": None,
            "children": {"sushi": 11, **({"amp": 12} if amp else {}), **({"recorder": 13} if recorder else {})},
        }
        if rig is not None:
            self.state["rig"] = rig
        self.running = True
        self.alive = {"recorder": True}
        self.ran: list = []
        self.refuse_compile: str | None = None  # why the next rig a switch names does not compile

    def described(self):
        if not self.running:
            return {"verdict": "absent", "running": False, "message": "No rig is running."}
        return {"verdict": "running", "running": True, "config_name": self.state["config_name"],
                "mode": self.state.get("mode"), "rig": self.state.get("rig"), "config": self.state["config"],
                "children": {n: {"pid": p, "alive": self.alive.get(n, True)} for n, p in self.state["children"].items()},
                "message": f"{self.state['config_name']} ({self.state.get('mode')} mode), up 2m"}

    def switching(self):
        def compile(name, root, machine):
            if self.refuse_compile:
                raise RuntimeError(self.refuse_compile)
            return config_of(load_rig(self.root / "config" / "rigs" / "duo.yaml"))

        return Switching(read_state=lambda: copy.deepcopy(self.state),
                         read_config=lambda path: json.loads(Path(path).read_text()),
                         write_state=lambda state: self.state.update(state), compile=compile,
                         write_config=lambda name, compiled: str(self.root / "run" / "rigs" / f"{name}.json"))

    async def run(self, args):
        self.ran.append(list(args))
        if args[0] == "up" and self.running:
            # As `up` refuses: rig.prepare_launch, before anything starts.
            return Outcome(False, 1, "", "error: a rig is already running: duo (headless mode)\n")
        if args[0] == "down":
            self.running = False
        elif args[0] == "up":
            self.running = True
        return Outcome(True, 0, f"{args[0]} done\n", "")


class FakeSupervisor:
    """A supervisor holding the rig, for a model change (`restart_amp`) and
    for what /health says crashed."""

    class Held:
        ready, stopping = True, False

    def __init__(self, world):
        self.world = world
        self.held = self.Held()
        self.crashed, self.problems, self.restarts = [], [], []

    async def restart_amp(self, project, values, *, model, override):
        self.restarts.append(model)
        self.world.state.update(amp_model=model, amp_model_requested=override)
        return AmpRestart(True, pid=99)

    def describe(self, rig=None):
        return {"holds": None, "crashed": list(self.crashed), "problems": [], "note": None}


def record_agent(root, world=None, *, link=None, clock=None):
    world = world or World(root)
    clock = clock or FakeClock()
    link = link or CoreLink(RecorderCore(engine=Pushing, clock=clock, say=lambda message: None))
    supervisor = FakeSupervisor(world)
    agent = Agent(root=root, describe=world.described, switching=world.switching(), live=UiRig(),
                  carla=FakeCarla().carla(), run=world.run, supervisor=supervisor, recorder=link,
                  takes_dir=root / "takes",
                  midi=midi_listener.World(plan=lambda described, root: midi_listener.Off("no pedals here")))
    return agent, world, link, clock


def calls(agent, *requests, between=None):
    """Requests against one served agent, in order, as (status, json).
    `between(i)` runs after the i-th, on the loop: a clock to move."""
    async def go():
        out = []
        async with TestClient(TestServer(agent.app())) as client:
            for i, (method, path, kwargs) in enumerate(requests):
                response = await client.request(method, path, **kwargs)
                out.append((response.status, await response.json()))
                if between:
                    between(i)
        return out

    return asyncio.run(go())


def call(agent, method, path, **kwargs):
    return calls(agent, (method, path, kwargs))[0]


START = ("POST", "/record/start", {"json": {"name": "riff-idea"}})
UNNAMED = ("POST", "/record/start", {"json": {}})
STOP = ("POST", "/record/stop", {})
STATUS = ("GET", "/record", {})

# The agent's clock for a take's default name (#134): local, and a test's to move.
EVENING = datetime(2026, 11, 12, 21, 58)


def on_the_day(agent, now=EVENING):
    """`agent` with its take clock held at `now`; the list's one item is the
    clock's reading, for a test to change."""
    held = [now]
    agent.take_clock = lambda: held[0]
    return held


# --------------------------------------------------------------------------- #
# The decisions, pure
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("body, name", [(None, None), ({}, None), ({"name": None}, None), ({"name": ""}, None),
                                        ({"name": "riff-idea"}, "riff-idea")])
def test_a_start_names_its_take_or_leaves_it_to_the_agent(body, name):
    assert record_request(body) == name


def test_the_rig_in_a_takes_default_name_is_the_running_rigs_file_name():
    assert take_rig_name({"rig": "config/rigs/electric-clean.yaml"}) == "electric-clean"
    assert take_rig_name({"rig": "/abs/elsewhere/duo.yaml"}) == "duo"


@pytest.mark.parametrize("body, said", [
    ({"name": "Riff Idea"}, "invalid take name 'Riff Idea'"),
    ({"name": "../x"}, "invalid take name"),
    ({"name": "riff", "rig": "x"}, "the body of a record must be empty"),
    (["riff"], "the body of a record must be empty"),
])
def test_a_name_the_take_rule_refuses_is_refused_before_anything_is_asked(body, said):
    with pytest.raises(AgentError, match=re.escape(said)):
        record_request(body)


def test_what_cannot_be_recorded_from_here_is_refused_with_a_code_and_why(root):
    world = World(root)
    assert record_refusal(world.state, world.described()) is None
    reason, code = record_refusal({**world.state, "mode": "panel"}, world.described())
    assert code == "panel" and "whose record bar owns recording" in reason and "panel's RECORD" in reason
    no_rig = {k: v for k, v in world.state.items() if k != "rig"}
    reason, code = record_refusal(no_rig, world.described())
    assert code == "v1" and reason.startswith("duo is a v1 config, not a rig of channels started with --rig")
    assert "up --rig" in reason
    world.running = False
    assert record_refusal(world.state, world.described())[1] == "no_rig"


def test_the_recorder_is_given_the_runs_files_as_absolute_paths(root):
    run = recorder_run({"rig": "config/rigs/duo.yaml", "config": "run/x.json", "machine": None},
                       root, root / "takes")
    assert run == {"root": str(root.resolve()), "rig": str(root / "config" / "rigs" / "duo.yaml"),
                   "config": str(root / "run" / "x.json"), "machine": None,
                   "takes_dir": str((root / "takes").resolve())}


def test_undo_passes_over_a_takes_start_and_stop_to_the_change_before_them():
    log = [
        {"op": "parameter", "args": {"processor": "drive", "parameter": "Gain", "value": 0.4}, "previous": 0.2},
        {"op": "record", "args": {"action": "start", "name": "riff"}, "previous": None},
        {"op": "record", "args": {"action": "stop", "name": "riff"}, "previous": None},
    ]
    assert pick_undo(log, 0)[0] == 0


# --------------------------------------------------------------------------- #
# The routes
# --------------------------------------------------------------------------- #

def test_idle_says_what_a_take_would_record_and_where(root):
    agent, _, link, _ = record_agent(root)
    status, body = call(agent, "GET", "/record")
    assert status == 200 and body["state"] == "idle"
    assert [s["file"] for s in body["stems"]] == ["guitar.wav", "guitar-dry.wav", "keys.wav", "keys-dry.wav",
                                                  "master.wav"]
    assert body["takes_dir"] == str((root / "takes").resolve())
    (asked,) = link.asked
    assert asked["run"]["rig"] == str(root / "config" / "rigs" / "duo.yaml"), "the state file's rig file"


def test_a_take_from_record_to_stop_is_logged_and_checks_clean(root):
    agent, _, _, clock = record_agent(root)

    def tick(i):
        if i == 0:
            clock.advance(12.5)

    (status, started), (_, now), (_, stopped), (_, log) = calls(
        agent, START, STATUS, STOP, ("GET", "/live/log", {}), between=tick)
    assert status == 200 and started["state"] == "recording" and started["name"] == "riff-idea"
    assert (now["state"], now["name"], now["elapsed"]) == ("recording", "riff-idea", 12.5)
    assert stopped["ok"] and stopped["problems"] == [] and stopped["complete"] is True
    assert check_take(load_take(root / "takes" / "riff-idea")) == []
    assert [e["op"] for e in log["log"]] == ["record", "record"]
    start_args, stop_args = (e["args"] for e in log["log"])
    assert start_args == {"action": "start", "name": "riff-idea", "amp": True, "stems": [
        "guitar.wav", "guitar-dry.wav", "keys.wav", "keys-dry.wav", "master.wav"]}
    assert stop_args == {"action": "stop", "name": "riff-idea", "seconds": stopped["seconds"], "problems": 0,
                         "by": "request"}


# --------------------------------------------------------------------------- #
# A take without a name (#134): <rig>-<date>-take-<nn>, worked out by the agent
# --------------------------------------------------------------------------- #

def test_a_take_without_a_name_is_named_for_the_rig_the_day_and_its_number(root):
    """The issue's first "done": the first unnamed take of `duo` today is
    `-take-01`, the next `-take-02`, and tomorrow's first `-take-01` again."""
    agent, _, link, _ = record_agent(root)
    clock = on_the_day(agent)

    def midnight(i):
        if i == 3:
            clock[0] = datetime(2026, 11, 13, 0, 0, 1)

    (status, first), _, (_, second), _, (_, tomorrow), _, (_, log) = calls(
        agent, UNNAMED, STOP, UNNAMED, STOP, UNNAMED, STOP, ("GET", "/live/log", {}), between=midnight)
    assert status == 200, first
    assert [first["name"], second["name"], tomorrow["name"]] == [
        "duo-2026-11-12-take-01", "duo-2026-11-12-take-02", "duo-2026-11-13-take-01"]
    for name in (first["name"], second["name"], tomorrow["name"]):
        assert check_take(load_take(root / "takes" / name)) == []
    assert [e["args"]["name"] for e in log["log"] if e["args"]["action"] == "start"] == [
        "duo-2026-11-12-take-01", "duo-2026-11-12-take-02", "duo-2026-11-13-take-01"]
    assert [a["name"] for a in link.asked if a["op"] == "start"] == [
        "duo-2026-11-12-take-01", "duo-2026-11-12-take-02", "duo-2026-11-13-take-01"], "the recorder is told the name"
    assert not (root / "takes" / "_archive").exists(), "no take replaced another"


def test_idle_offers_the_name_an_unnamed_take_would_get_and_a_start_uses_it(root):
    """The name shown is the name used: GET /record's `default_name` and a
    start with no name ask one function."""
    agent, _, _, _ = record_agent(root)
    on_the_day(agent)
    (_, idle), (_, started), (_, recording), _, (_, after) = calls(agent, STATUS, UNNAMED, STATUS, STOP, STATUS)
    assert idle["state"] == "idle" and idle["default_name"] == "duo-2026-11-12-take-01"
    assert started["name"] == idle["default_name"]
    assert recording["state"] == "recording" and "default_name" not in recording
    assert after["default_name"] == "duo-2026-11-12-take-02", "after a take, the next number"
    assert after["names"] == ["duo-2026-11-12-take-01"]


def test_two_pages_shown_the_same_name_each_get_a_take_of_their_own(root):
    """Two pages read the same default. One records and stops; the other,
    still showing that name, presses Record: it sends no name, as an
    untouched field does, and gets the next number. Nothing is replaced."""
    agent, _, _, _ = record_agent(root)
    on_the_day(agent)
    (_, one), (_, two), (_, first), (busy, refused), _, (status, second), _ = calls(
        agent, STATUS, STATUS, UNNAMED, UNNAMED, STOP, UNNAMED, STOP)
    assert one["default_name"] == two["default_name"] == "duo-2026-11-12-take-01", "two pages agree"
    assert first["name"] == "duo-2026-11-12-take-01"
    # Pressed while the first still runs: one take at a time, as ever, and said so.
    assert (busy, refused["code"]) == (409, "recording")
    assert refused["reason"] == "already recording duo-2026-11-12-take-01: stop it first"
    assert status == 200 and second["name"] == "duo-2026-11-12-take-02" and not second.get("replaced")
    assert sorted(p.name for p in (root / "takes").iterdir()) == [
        "duo-2026-11-12-take-01", "duo-2026-11-12-take-02"], "two takes, and no archive"


def test_the_number_counts_archived_takes_and_leaves_gaps_and_other_names_alone(root):
    takes = root / "takes"
    for name in ("duo-2026-11-12-take-01", "duo-2026-11-12-take-03", "take-20261112-142501",
                 "duo-2026-11-11-take-09", "trio-2026-11-12-take-20", "_archive/duo-2026-11-12-take-05/2026-11-12T20-00-00"):
        (takes / name).mkdir(parents=True)
    agent, _, _, _ = record_agent(root)
    on_the_day(agent)
    (_, idle), (_, started), _ = calls(agent, STATUS, UNNAMED, STOP)
    assert idle["default_name"] == "duo-2026-11-12-take-06", "one past the archived 05, and 02 and 04 stay gaps"
    assert started["name"] == "duo-2026-11-12-take-06"
    assert "take-20261112-142501" in idle["names"], "a take named from the time is still a take"


def test_a_name_typed_is_used_as_it_is_and_recording_over_one_still_archives(root):
    agent, _, _, _ = record_agent(root)
    on_the_day(agent)
    again = ("POST", "/record/start", {"json": {"name": "duo-2026-11-12-take-01"}})
    (_, first), _, (status, second), _, (_, idle) = calls(agent, UNNAMED, STOP, again, STOP, STATUS)
    assert first["name"] == "duo-2026-11-12-take-01"
    assert status == 200 and second["name"] == "duo-2026-11-12-take-01" and second["replaced"] is True
    assert (root / "takes" / "_archive" / "duo-2026-11-12-take-01").is_dir()
    assert idle["default_name"] == "duo-2026-11-12-take-02"


def test_an_unnamed_start_never_archives_a_take_even_when_the_listing_comes_back_empty(root, monkeypatch):
    """The only way an unnamed start could move an existing take aside was a
    listing that came back short, so that the count gave a name in use. The
    name is checked against the folders themselves (`take.next_name`)."""
    from sushi_rig import take as take_module

    agent, _, _, _ = record_agent(root)
    on_the_day(agent)
    (_, first), _ = calls(agent, UNNAMED, STOP)
    assert first["name"] == "duo-2026-11-12-take-01"
    monkeypatch.setattr(take_module, "names_used", lambda takes_dir: set())
    (_, idle), (status, second), _ = calls(agent, STATUS, UNNAMED, STOP)
    assert idle["default_name"] == "duo-2026-11-12-take-02"
    assert status == 200 and second["name"] == "duo-2026-11-12-take-02" and not second.get("replaced")
    assert not (root / "takes" / "_archive").exists()


def test_the_default_follows_the_rig_that_is_running(root):
    """After a switch the state file names another rig file, and the next
    unnamed take is that rig's first."""
    (root / "config" / "rigs" / "trio.yaml").write_text(TWO_CHANNELS.replace("name: duo", "name: trio"))
    world = World(root)
    agent, _, _, _ = record_agent(root, world)
    on_the_day(agent)

    def switch(i):
        if i == 2:
            world.state["rig"] = "config/rigs/trio.yaml"

    (_, before), _, _, (_, idle), (status, started), _ = calls(
        agent, STATUS, UNNAMED, STOP, STATUS, UNNAMED, STOP, between=switch)
    assert before["default_name"] == "duo-2026-11-12-take-01"
    assert idle["default_name"] == "trio-2026-11-12-take-01"
    assert status == 200 and started["name"] == "trio-2026-11-12-take-01"


def test_a_rig_with_a_long_name_records_under_a_shortened_default(root):
    """A rig's name may be 64 characters, which leaves no room for the date
    and the number: the default is shortened, and the take is made."""
    long = "a-rig-with-a-name-as-long-as-the-rule-for-names-allows-one-to-be"
    assert len(long) == 64 and check_name(long) == []
    (root / "config" / "rigs" / f"{long}.yaml").write_text(TWO_CHANNELS.replace("name: duo", f"name: {long}"))
    agent, _, _, _ = record_agent(root, World(root, rig=f"config/rigs/{long}.yaml"))
    on_the_day(agent)
    (_, idle), (status, started), _, (_, after) = calls(agent, STATUS, UNNAMED, STOP, STATUS)
    # Cut to 43 characters, whatever the number: room for four digits of it.
    name = "a-rig-with-a-name-as-long-as-the-rule-for-n-2026-11-12-take-01"
    assert idle["default_name"] == name and len(name) == 62 and check_name(name) == []
    assert status == 200 and started["name"] == name, started
    assert check_take(load_take(root / "takes" / name)) == []
    assert after["default_name"] == name[:-2] + "02"


@pytest.mark.parametrize("change, code", [
    (lambda w: setattr(w, "running", False), "no_rig"),
    (lambda w: w.state.update(mode="panel"), "panel"),
    (lambda w: w.state.pop("rig"), "v1"),
])
def test_with_no_rig_to_record_there_is_no_default_and_an_unnamed_start_is_refused(root, change, code):
    world = World(root)
    change(world)
    agent, _, link, _ = record_agent(root, world)
    on_the_day(agent)
    (status, start), (_, now) = calls(agent, UNNAMED, STATUS)
    assert (status, start["code"]) == (409, code)
    assert now["state"] == "unavailable" and "default_name" not in now
    assert link.asked == [] and not (root / "takes").exists()


def test_the_day_is_the_agents_own_clocks_unless_a_test_holds_it(root):
    """Local time, as the name is for the player to recognise: `datetime.now`
    with no zone, the clock `transport.auto_name` read before #134."""
    agent, _, _, _ = record_agent(root)
    assert agent.take_clock == datetime.now
    before = datetime.now().strftime("%Y-%m-%d")
    _, idle = call(agent, "GET", "/record")
    after = datetime.now().strftime("%Y-%m-%d")
    assert idle["default_name"] in (f"duo-{before}-take-01", f"duo-{after}-take-01")


def test_the_take_carries_the_amp_as_the_agent_last_sent_it_and_the_rig_file_as_it_is(root):
    agent, _, _, _ = record_agent(root)
    (status, _), (_, started), _ = calls(
        agent, ("POST", "/amp/parameter", {"json": {"name": "Output Lvl", "value": 3.5}}), START, STOP)
    assert status == 200
    config = json.loads((root / "takes" / "riff-idea" / "config.json").read_text())
    # Input as `up` replayed it from the config; Output as sent; Quality the
    # plugin's default, as `amp.write_amp` writes one never sent (#117's rule).
    assert config["_amp"] == {"plugin": "nam", "model": "amp/models/First.nam",
                              "parameters": {"Input Lvl": -2.0, "Output Lvl": 3.5, "Quality": 1.0}}
    assert started["amp"]["saved"] is True and started["amp"]["known"] == "sent"
    assert (root / "takes" / "riff-idea" / "rig.yaml").read_text() == TWO_CHANNELS


def test_a_run_with_no_amp_records_a_take_with_none(root):
    agent, _, _, _ = record_agent(root, World(root, amp=False))
    (_, started), _ = calls(agent, START, STOP)
    assert "_amp" not in json.loads((root / "takes" / "riff-idea" / "config.json").read_text())
    assert started["amp"] == {"saved": False, "why": "the running rig has no amp: it was started with --no-amp, "
                                                     "so the take carries no amp"}


def test_undo_passes_over_the_take_to_the_knob_before_it(root):
    agent, _, _, _ = record_agent(root)
    rig = agent.live
    before = rig.info["overdrive"]["Drive"]["value"]
    *_, (status, undone) = calls(
        agent, ("POST", "/live/parameter", {"json": {"processor": "overdrive", "parameter": "Drive", "value": 0.9}}),
        START, STOP, ("POST", "/live/undo", {}))
    assert status == 200 and undone["undone"]["op"] == "parameter", undone
    assert rig.info["overdrive"]["Drive"]["value"] == before
    assert (root / "takes" / "riff-idea").is_dir(), "the take stays made"


# --------------------------------------------------------------------------- #
# Refusals, each with its code
# --------------------------------------------------------------------------- #

def test_record_while_recording_and_stop_while_stopped_are_refused(root):
    agent, _, _, _ = record_agent(root)
    (_, _), (status, again), _, (stop_status, stopped_twice) = calls(
        agent, START, ("POST", "/record/start", {"json": {"name": "second"}}), STOP, STOP)
    assert (status, again["code"]) == (409, "recording")
    assert again["reason"] == "already recording riff-idea: stop it first"
    assert (stop_status, stopped_twice["code"], stopped_twice["reason"]) == (409, "not_recording", "not recording")
    assert not (root / "takes" / "second").exists()


def test_a_bad_name_is_400_and_nothing_is_asked(root):
    agent, _, link, _ = record_agent(root)
    status, body = call(agent, "POST", "/record/start", json={"name": "Riff Idea"})
    assert status == 400 and "invalid take name" in body["reason"]
    assert link.asked == []


@pytest.mark.parametrize("change, code, said", [
    (lambda w: setattr(w, "running", False), "no_rig", "no rig is running to record"),
    (lambda w: w.state.update(mode="panel"), "panel", "whose record bar owns recording"),
    (lambda w: w.state.pop("rig"), "v1", "is a v1 config, not a rig of channels"),
])
def test_what_cannot_be_recorded_from_here_is_refused_and_said_as_unavailable(root, change, code, said):
    world = World(root)
    change(world)
    agent, _, link, _ = record_agent(root, world)
    (status, start), (stop_status, stop), (_, now) = calls(agent, START, STOP, STATUS)
    assert (status, start["code"]) == (409, code) and said in start["reason"]
    assert (stop_status, stop["code"]) == (409, code)
    assert now == {"ok": True, "state": "unavailable", "code": code, "reason": start["reason"]}
    assert link.asked == [], "the recorder is not asked about a run it cannot record"


def test_record_is_refused_while_a_lifecycle_command_runs(root):
    agent, _, _, _ = record_agent(root)

    async def go():
        async with TestClient(TestServer(agent.app())) as client:
            async with agent._lock:
                agent.busy = "restart"
                response = await client.post("/record/start", json={"name": "x"})
                agent.busy = None
                return response.status, await response.json()

    status, body = asyncio.run(go())
    assert (status, body["code"]) == (409, "busy") and "'restart' is still running" in body["reason"]


def test_a_run_started_without_a_recorder_says_to_restart(root):
    agent, _, link, _ = record_agent(root, World(root, recorder=False))
    link.down = "no recorder is listening on /run/recorder.sock (No such file or directory)"
    (status, start), (_, now) = calls(agent, START, STATUS)
    assert (status, start["code"]) == (409, "no_recorder")
    assert start["reason"].startswith("this run has no recorder: it was started before recording")
    assert (now["state"], now["code"]) == ("unavailable", "no_recorder")


def test_a_recorder_that_crashed_is_said_as_a_crash_not_restarted(root):
    world = World(root)
    world.alive["recorder"] = False
    agent, _, link, _ = record_agent(root, world)
    link.down = "no recorder is listening on /run/recorder.sock (Connection refused)"
    agent.supervisor.crashed.append({"name": "recorder", "pid": 13, "log": "/run/logs/recorder.log"})
    _, now = call(agent, "GET", "/record")
    assert now["state"] == "unavailable" and now["code"] == "no_recorder"
    assert now["reason"] == ("the recorder (pid 13) is not running: it exited (its log: /run/logs/recorder.log), "
                             "which /health shows as a crash. It is not restarted on its own (principle 9): "
                             "POST /rig/restart brings it back")


def test_a_run_no_take_can_be_planned_for_says_why(root):
    (root / "machines" / "box.yaml").write_text(MACHINE.replace("keys-in", "piano-in"))
    agent, _, _, _ = record_agent(root)
    (status, start), (_, now) = calls(agent, START, STATUS)
    assert (status, start["code"]) == (409, "unavailable")
    assert "channel 'keys' plays from 'keys-in', which the machine file gives no ports" in start["reason"]
    assert now["state"] == "unavailable" and now["reason"] == start["reason"]
    assert "default_name" not in now, "no take can be made, so none is named"


def test_a_take_that_did_not_start_is_502_and_its_folder_says_why(root):
    agent, _, _, _ = record_agent(root)
    Pushing.refuse = RuntimeError("no such port (test)")
    status, body = call(agent, "POST", "/record/start", json={"name": "refused"})
    assert (status, body["code"]) == (502, "failed")
    assert body["reason"].endswith("the capture engine did not start: no such port (test)")
    assert agent.last_error["message"] == body["reason"]
    assert any("did not start" in p for p in check_take(load_take(root / "takes" / "refused")))


# --------------------------------------------------------------------------- #
# A rig changed under a take: the take is finished first
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("command", ["down", "restart"])
def test_a_stop_or_restart_finishes_the_take_first_and_says_so(root, command):
    agent, world, _, _ = record_agent(root)
    _, (status, answer), (_, log) = calls(agent, START, ("POST", f"/rig/{command}", {"json": {}}),
                                          ("GET", "/live/log", {}))
    assert status == 200, answer
    assert answer["take"]["name"] == "riff-idea" and answer["take"]["by"] == command
    assert answer["take"]["problems"] == []
    assert world.ran[0][0] == ("down" if command == "down" else "restart")
    assert [(e["args"]["action"], e["args"].get("by")) for e in log["log"]] == [("start", None), ("stop", command)]
    assert load_take(root / "takes" / "riff-idea").complete


def test_a_switch_finishes_the_take_first_and_a_refused_one_leaves_it_running(root):
    agent, world, _, _ = record_agent(root)
    world.refuse_compile = "bad rig (test)"
    _, (refused_status, refused), (_, now) = calls(
        agent, START, ("POST", "/rig/switch", {"json": {"config_name": "other"}}), STATUS)
    assert (refused_status, refused["code"]) == (409, "rig_problem")
    assert now["state"] == "recording", "a switch refused before it began leaves the take alone"
    world.refuse_compile = None
    (status, answer), (_, after) = calls(agent, ("POST", "/rig/switch", {"json": {"config_name": "other"}}), STATUS)
    assert answer["take"]["name"] == "riff-idea" and answer["take"]["by"] == "switch", answer
    assert after["state"] == "idle"
    assert load_take(root / "takes" / "riff-idea").complete


def test_a_model_change_finishes_the_take_first(root):
    agent, _, _, _ = record_agent(root)
    _, (status, answer) = calls(agent, START, ("POST", "/amp/model", {"json": {"model": "Second.nam"}}))
    assert status == 200 and answer["changed"], answer
    assert answer["take"]["name"] == "riff-idea" and answer["take"]["by"] == "amp_model"
    config = json.loads((root / "takes" / "riff-idea" / "config.json").read_text())
    assert config["_amp"]["model"] == "amp/models/First.nam", "the model it was recorded through"


def test_up_with_nothing_running_asks_the_recorder_nothing(root):
    world = World(root)
    world.running = False
    agent, _, link, _ = record_agent(root, world)
    status, answer = call(agent, "POST", "/rig/up", json={"config_name": "duo", "rig": True, "headless": True})
    assert status == 200 and "take" not in answer
    assert link.asked == []


# --------------------------------------------------------------------------- #
# The takes
# --------------------------------------------------------------------------- #

def test_the_takes_are_listed_newest_first_with_take_checks_verdict(root):
    agent, _, _, _ = record_agent(root)
    calls(agent, ("POST", "/record/start", {"json": {"name": "first"}}), STOP,
          ("POST", "/record/start", {"json": {"name": "second"}}), STOP,
          ("POST", "/record/start", {"json": {"name": "first"}}), STOP)
    # A take cut off mid-way, as a rig killed mid-take leaves one: started, never finished.
    config = json.loads((root / "run" / "rigs" / "duo.json").read_text())
    from sushi_rig.take import DRY, Stem

    start_take(root / "takes", "cut-off", config, [Stem("guitar", DRY, "iface:capture_AUX0")],
               sample_rate=48000, subtype="float32", quantum=256, config_source="test")
    (root / "takes" / "not-a-take").mkdir()
    status, body = call(agent, "GET", "/takes")
    assert status == 200 and body["exists"] is True and body["archived"] == 1 and body["more"] == 0
    names = [t["name"] for t in body["takes"]]
    assert set(names) == {"first", "second", "cut-off", "not-a-take"}
    assert names.index("first") < names.index("second"), "the take recorded last comes first"
    first = next(t for t in body["takes"] if t["name"] == "first")
    assert first["problems"] == [] and first["complete"] and first["seconds"] == round(40 * 256 / 48000, 3)
    assert first["stems"] == ["guitar.wav", "guitar-dry.wav", "keys.wav", "keys-dry.wav", "master.wav"]
    cut = next(t for t in body["takes"] if t["name"] == "cut-off")
    assert cut["complete"] is False and cut["problems"][0].startswith("this take was never finished")
    odd = next(t for t in body["takes"] if t["name"] == "not-a-take")
    assert odd["take"] is False and "is not a take" in odd["problems"][0]


def test_no_takes_folder_is_an_empty_list_that_says_so(root):
    agent, _, _, _ = record_agent(root)
    _, body = call(agent, "GET", "/takes")
    assert (body["exists"], body["takes"]) == (False, [])


def test_a_list_past_its_limit_counts_the_rest(tmp_path):
    for i in range(3):
        (tmp_path / f"t{i}").mkdir()
    listed = list_takes(tmp_path, limit=2)
    assert len(listed["takes"]) == 2 and listed["more"] == 1


def test_lost_audio_is_in_the_summary_and_the_verdict(root):
    agent, _, _, _ = record_agent(root)
    calls(agent, START, STOP)
    folder = root / "takes" / "riff-idea"
    import yaml

    meta = yaml.safe_load((folder / "take.yaml").read_text())
    meta["capture"].update(lost_frames=480, dropouts=[{"at_frame": 100, "frames": 480}])
    (folder / "take.yaml").write_text(yaml.safe_dump(meta))
    summary = take_summary(folder)
    assert summary["lost_ms"] == 10.0
    assert any("the recorder lost audio 1 time(s), 10.0 ms in all" in p for p in summary["problems"])


# --------------------------------------------------------------------------- #
# The agent and a real recorder process, over its socket
# --------------------------------------------------------------------------- #

def test_a_take_through_the_agent_and_a_real_recorder_passes_take_check(root, tmp_path):
    import os
    import subprocess
    import tempfile
    import time

    from test_recorder_process import SRC, Process

    with tempfile.TemporaryDirectory(prefix="rec-", dir="/tmp") as folder:
        socket_path = Path(folder) / "recorder.sock"
        process = Process(socket_path, tmp_path / "recorder.log")
        try:
            process.ready()
            agent, _, _, _ = record_agent(root, link=RecorderLink(socket_path))

            def wait(i):
                if i == 0:
                    time.sleep(0.4)

            (status, started), (_, now), (_, stopped) = calls(agent, START, STATUS, STOP, between=wait)
            assert status == 200, started
            assert now["state"] == "recording" and now["elapsed"] >= 0.3
            assert stopped["ok"] and stopped["problems"] == [], stopped
        finally:
            process.kill()
    checked = subprocess.run([sys.executable, "-m", "sushi_rig.cli", "take", "check",
                              str(root / "takes" / "riff-idea")],
                             env={**os.environ, "PYTHONPATH": str(SRC)}, capture_output=True, text=True)
    assert checked.returncode == 0, checked.stdout + checked.stderr


# --------------------------------------------------------------------------- #
# Review of S7: each finding, pinned
# --------------------------------------------------------------------------- #

def test_an_up_refused_for_a_running_rig_leaves_the_take_recording(root):
    """`up` takes no rig down, and refuses one that is running: finishing the
    take for it would end the take for nothing."""
    agent, world, _, _ = record_agent(root)
    _, (status, answer), (_, now), (_, log) = calls(
        agent, START, ("POST", "/rig/up", {"json": {"config_name": "duo", "rig": True, "headless": True}}),
        STATUS, ("GET", "/live/log", {}))
    assert status == 409 and "a rig is already running" in answer["reason"] and "take" not in answer
    assert (now["state"], now["name"]) == ("recording", "riff-idea")
    assert [e["args"]["action"] for e in log["log"]] == ["start"]
    calls(agent, STOP)


class SlowLink(CoreLink):
    """A recorder whose start waits for `go`, so a test can act while a
    start is in flight; `entered` is set once it is."""

    def __init__(self, core):
        super().__init__(core)
        import threading

        self.entered, self.go = threading.Event(), threading.Event()

    def ask(self, request, timeout):
        if request["op"] == "start":
            self.entered.set()
            assert self.go.wait(10)
        return super().ask(request, timeout)


def test_no_amp_change_lands_between_the_amp_a_take_names_and_its_start(root):
    """A fader moved and a model changed while a start is in flight wait for
    it: the take names the amp that was playing when it started, the fader
    is sent after, and the model change finishes the take it would
    otherwise misdescribe."""
    link = SlowLink(RecorderCore(engine=Pushing, clock=FakeClock(), say=lambda message: None))
    agent, _, _, _ = record_agent(root, link=link)
    order = []

    async def go():
        async with TestClient(TestServer(agent.app())) as client:
            async def post(path, body, label):
                response = await client.post(path, json=body)
                order.append(label)
                return response.status, await response.json()

            start = asyncio.ensure_future(post("/record/start", {"name": "raced"}, "start"))
            while not link.entered.is_set():
                await asyncio.sleep(0.01)
            fader = asyncio.ensure_future(post("/amp/parameter", {"name": "Output Lvl", "value": 1.5}, "fader"))
            model = asyncio.ensure_future(post("/amp/model", {"model": "Second.nam"}, "model"))
            await asyncio.sleep(0.3)
            assert order == [], "nothing passed the start while it was in flight"
            link.go.set()
            return await start, await fader, await model

    (start_status, started), (fader_status, _), (model_status, changed) = asyncio.run(go())
    assert start_status == 200 and order[0] == "start", order
    assert fader_status in (200, 409), "sent after the start, or refused as busy behind the model change"
    assert model_status == 200 and changed["take"]["name"] == "raced" and changed["take"]["by"] == "amp_model"
    config = json.loads((root / "takes" / "raced" / "config.json").read_text())
    assert config["_amp"]["model"] == "amp/models/First.nam", "the model playing while it recorded"
    assert config["_amp"]["parameters"]["Output Lvl"] == 6.0, "the fader as it was at RECORD"
    assert started["amp"]["model"] == "amp/models/First.nam"


class Answers(CoreLink):
    """A recorder that answers `answer` to a status, whatever it is."""

    def __init__(self, core, answer):
        super().__init__(core)
        self.answer = answer

    def ask(self, request, timeout):
        if request["op"] == "status":
            return self.answer
        return super().ask(request, timeout)


@pytest.mark.parametrize("answer, status, code", [
    ({"ok": False, "code": "bad_request", "reason": "status does not take verbose"}, 500, "bad_request"),
    ({"ok": False, "code": "failed", "reason": "the recorder failed unexpectedly: KeyError"}, 502, "failed"),
    ({"ok": True}, 502, None),
    ({"ok": True, "state": "dancing"}, 502, None),
])
def test_a_status_the_recorder_refuses_or_gives_no_state_is_not_passed_on(root, answer, status, code):
    core = RecorderCore(engine=Pushing, clock=FakeClock(), say=lambda message: None)
    agent, _, _, _ = record_agent(root, link=Answers(core, answer))
    got, body = call(agent, "GET", "/record")
    assert got == status and body.get("code") == code and "state" not in body, body


def test_a_recorder_too_busy_to_answer_is_a_timeout_not_a_crash(root):
    from sushi_rig.recorder_process import RecorderTimeout

    class Busy:
        def ask(self, request, timeout):
            raise RecorderTimeout("the recorder on /run/recorder.sock did not take the connection within 3s: it is busy")

    agent, _, _, _ = record_agent(root, link=Busy())
    (_, now), (status, start) = calls(agent, STATUS, START)
    assert (now["state"], now["code"]) == ("unavailable", "recorder_timeout") and "it is busy" in now["reason"]
    assert (status, start["code"]) == (504, "recorder_timeout")


def test_a_stop_that_cannot_be_read_back_is_logged_as_the_stop_it_was(root, monkeypatch):
    from sushi_rig import take as take_module

    agent, _, _, _ = record_agent(root)
    calls(agent, START)
    monkeypatch.setattr(take_module, "take_summary", lambda folder: (_ for _ in ()).throw(OSError("gone (test)")))
    (status, stopped), (_, now), (_, log) = calls(agent, STOP, STATUS, ("GET", "/live/log", {}))
    assert status == 502 and stopped["stopped"] is True and stopped["name"] == "riff-idea"
    assert stopped["reason"] == "riff-idea stopped, but could not be read back: OSError: gone (test)"
    assert now["state"] == "idle"
    assert log["log"][-1]["args"] == {"action": "stop", "name": "riff-idea", "seconds": None, "problems": 1,
                                      "by": "request", "ok": False}


def test_a_take_is_checked_again_only_when_its_folder_changes(root, monkeypatch):
    from sushi_rig import take as take_module

    agent, _, _, _ = record_agent(root)
    calls(agent, START, STOP, ("POST", "/record/start", {"json": {"name": "second"}}), STOP)
    checked = []
    real = take_module.take_summary
    monkeypatch.setattr(take_module, "take_summary", lambda folder: checked.append(Path(folder).name) or real(folder))
    calls(agent, ("GET", "/takes", {}), ("GET", "/takes", {}), ("GET", "/takes", {}))
    assert sorted(checked) == ["riff-idea", "second"], "each once, then from the cache"
    (root / "takes" / "second" / "take.yaml").write_text(
        (root / "takes" / "second" / "take.yaml").read_text() + "# touched\n")
    _, (_, listed) = calls(agent, ("GET", "/takes", {}), ("GET", "/takes", {}))
    assert sorted(checked) == ["riff-idea", "second", "second"]
    assert [t["name"] for t in listed["takes"]][0] == "second"


def test_an_idle_status_names_the_takes_already_there(root):
    agent, _, _, _ = record_agent(root)
    _, _, (_, now) = calls(agent, START, STOP, STATUS)
    assert now["state"] == "idle" and now["names"] == ["riff-idea"]


def test_each_route_reads_the_rig_once_inside_its_lock(root):
    agent, _, _, _ = record_agent(root)
    seen = []
    real = agent._record_state

    async def counted():
        seen.append(agent._record_lock.locked())
        return await real()

    agent._record_state = counted
    calls(agent, START)
    assert seen == [True], "a start"
    seen.clear()
    calls(agent, STOP)
    assert seen == [True], "a stop"
    calls(agent, START)
    seen.clear()
    calls(agent, ("POST", "/rig/down", {"json": {}}))
    assert seen == [True], "a down finishing the take"
