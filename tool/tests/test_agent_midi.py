"""The agent's MIDI wiring (#34, #93): a fake port, the fake Sushi, the real routes.

The decisions are pinned in test_midi_listener.py; these say what the agent
does with them. A pad pressed on a fake port reaches the fake Sushi through
the path `POST /live/bypass` takes and lands in the live log; a Program
Change runs the lifecycle as `POST /rig/restart` does, through the runner;
the listener follows `up`, `down` and `restart`; and nothing a port does
takes the agent down.

The port is fake and the listener's thread is real, so the hand-over from
the thread to the event loop is exercised. Nothing here has heard a pedal.
"""

from __future__ import annotations

import asyncio
import queue
import threading
import time

import pytest

aiohttp = pytest.importorskip("aiohttp")
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from sushi_rig import agent as agent_module  # noqa: E402
from sushi_rig.agent import Agent, AgentError, switch_steps  # noqa: E402
from sushi_rig.midi_listener import Off, Plan, PortError, World, parse_bindings  # noqa: E402
from test_agent import UP, FakeLive, FakeRunner  # noqa: E402

PAD_BYPASS = {"control": "pad1", "note": 36, "channel": "all", "bypass": "main/drive"}
PAD_MUTE = {"control": "pad2", "note": 38, "channel": "all", "mute": "main"}
PAD_UNKNOWN = {"control": "pad3", "note": 40, "channel": "all", "bypass": "main/reverb"}
SWITCH = {"control": "stomp", "cc": 80, "channel": "all", "bypass": "main/comp"}
PROGRAMS = {"programs": "rigs", "port": 0, "channel": "all"}
RIGS = ["clean", "guitar_vocal", "lead"]
PORT_NAME = "MPD218:MPD218 Port A"

RIG_UP = dict(UP, config_name="guitar_vocal", rig="config/rigs/guitar_vocal.yaml")
STATE = {"config_name": "guitar_vocal", "mode": "headless", "rig": "config/rigs/guitar_vocal.yaml",
         "tuner_requested": False, "amp_requested": True, "amp_model_requested": "Other.nam"}


class FakePort:
    def __init__(self):
        self.inbox: queue.Queue = queue.Queue()
        self.closed = threading.Event()

    def read(self, timeout):
        try:
            item = self.inbox.get(timeout=timeout)
        except queue.Empty:
            return None
        if isinstance(item, Exception):
            raise item
        return item

    def close(self):
        self.closed.set()

    def send(self, *data):
        self.inbox.put(bytes(data))


class FakeWorld:
    """The listener's view of the world: what the running rig asks for, and a
    port per open. `refuse` makes opening fail."""

    def __init__(self, raw=None, plan=True):
        self.raw = raw if raw is not None else [PAD_BYPASS, PAD_MUTE, PAD_UNKNOWN, SWITCH, PROGRAMS]
        self.opened: list[str] = []
        self.ports: list[FakePort] = []
        self.refuse = None
        self.state = dict(STATE)
        self.has_plan = plan

    def plan(self, described, root):
        if not described.get("running"):
            return Off("no rig is running")
        if not self.has_plan:
            return Off(f"{described.get('config_name')} has no MIDI bindings for the agent")
        return Plan(rig=described.get("config_name"), controller="mpd218", port=PORT_NAME,
                    bindings=parse_bindings(self.raw))

    def open_port(self, name):
        self.opened.append(name)
        if self.refuse:
            raise self.refuse
        port = FakePort()
        self.ports.append(port)
        return port

    def world(self):
        return World(open_port=self.open_port, plan=self.plan,
                     read_state=lambda: dict(self.state), rigs=lambda root: list(RIGS))


class Rig:
    """What `describe` says, settable as the runner brings rigs up and down."""

    def __init__(self, described):
        self.described = dict(described)

    def __call__(self):
        return dict(self.described)


class SwitchingRunner(FakeRunner):
    """A runner whose `down` takes the rig away and whose `up` brings back the one named."""

    def __init__(self, rig):
        super().__init__()
        self.rig = rig

    async def __call__(self, args):
        outcome = await super().__call__(args)
        if outcome.ok and args[0] == "down":
            self.rig.described = {"verdict": "absent", "running": False, "message": "No rig is running."}
        elif outcome.ok and args[0] == "up":
            self.rig.described = dict(RIG_UP, config_name=args[1], rig=f"config/rigs/{args[1]}.yaml")
        return outcome


def midi_agent(world=None, described=RIG_UP, runner=None):
    world = world or FakeWorld()
    rig = Rig(described)
    runner = runner or SwitchingRunner(rig)
    fake = FakeLive()
    fake.info["main"]["mute"] = {"automatable": True, "value": 0.0,
                                 "min_domain_value": 0.0, "max_domain_value": 1.0}
    agent = Agent(describe=rig, run=runner, live=fake, midi=world.world())
    return agent, fake, world, runner, rig


async def until(condition, timeout=3.0):
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > deadline:
            raise AssertionError("timed out waiting")
        await asyncio.sleep(0.01)


async def settle(agent):
    """Let every MIDI action that has been handed over finish."""
    await asyncio.sleep(0.05)
    while agent._midi_tasks:
        await asyncio.gather(*list(agent._midi_tasks), return_exceptions=True)


def served(agent, test):
    """Run `test(client)` against a served agent, then stop its listener and pool."""

    async def go():
        try:
            async with TestClient(TestServer(agent.app())) as client:
                return await test(client)
        finally:
            await agent.midi_release()
            agent._executor.shutdown(wait=True, cancel_futures=True)

    return asyncio.run(go())


async def health(client):
    return (await (await client.get("/health")).json())["agent"]["midi"]


async def log(client):
    return (await (await client.get("/live/log")).json())["log"]


async def started(agent, world, client):
    """The listener for the rig that is up, as the agent finds it at start."""
    await agent.midi_adopt()
    await until(lambda: world.ports and agent._midi["state"] == "listening")
    return world.ports[-1]


# --------------------------------------------------------------------------- #
# Bypass and mute, through the live path
# --------------------------------------------------------------------------- #

def test_a_pad_toggles_a_bypass_through_the_live_path_and_is_logged_as_midi():
    agent, fake, world, runner, _ = midi_agent()

    async def test(client):
        port = await started(agent, world, client)
        assert world.opened == [PORT_NAME]
        port.send(0x90, 36, 100)  # pad1 down: drive is bypassed, so it comes in
        port.send(0x80, 36, 0)    # pad1 up: nothing
        await until(lambda: fake.writes())
        await settle(agent)
        assert fake.writes() == [("set_bypass", "drive", False)]
        assert ("bypass", "drive") in fake.calls, "read first, as POST /live/bypass does"
        port.send(0x90, 36, 90)
        await until(lambda: len(fake.writes()) == 2)
        await settle(agent)
        assert fake.writes()[-1] == ("set_bypass", "drive", True)
        entries = await log(client)
        assert [(e["op"], e["args"], e["previous"], e["source"], e["control"]) for e in entries] == [
            ("bypass", {"processor": "drive", "bypassed": False}, True, "midi", "pad1"),
            ("bypass", {"processor": "drive", "bypassed": True}, False, "midi", "pad1"),
        ]
        status = await health(client)
        assert status["heard"] == 3
        assert status["last"]["message"] == "note on 36 (velocity 90) on channel 0"
        assert status["last"]["action"] == "pad1: toggle the bypass of drive"
        assert fake.one_connection_per_request_all_closed()
        assert runner.calls == []

    served(agent, test)


def test_a_pad_on_a_mute_toggles_the_tracks_mute_parameter():
    agent, fake, world, *_ = midi_agent()

    async def test(client):
        port = await started(agent, world, client)
        port.send(0x90, 38, 100)
        await until(lambda: fake.writes())
        await settle(agent)
        assert fake.writes() == [("set_parameter", "main", "mute", 1.0)]
        (entry,) = await log(client)
        assert entry["op"] == "parameter" and entry["previous"] == 0.0 and entry["source"] == "midi"

    served(agent, test)


def test_a_cc_switch_sets_bypass_only_when_it_crosses_64():
    agent, fake, world, *_ = midi_agent()

    async def test(client):
        port = await started(agent, world, client)
        # comp starts engaged (not bypassed). 127 says "on": the same, so no
        # write; then 100 is no transition; 20 crosses, so it is bypassed.
        for value in (127, 100, 20, 10):
            port.send(0xB0, 80, value)
        await until(lambda: fake.writes())
        await settle(agent)
        assert fake.writes() == [("set_bypass", "comp", True)]
        assert [e["args"] for e in await log(client)] == [{"processor": "comp", "bypassed": True}]

    served(agent, test)


def test_aftertouch_and_unbound_messages_touch_nothing():
    agent, fake, world, *_ = midi_agent()

    async def test(client):
        port = await started(agent, world, client)
        for data in ([0xD0, 70], [0xA0, 36, 70], [0x90, 41, 100], [0xB0, 3, 90]):
            port.send(*data)
        await until(lambda: agent._midi_status()["heard"] == 4)
        await settle(agent)
        assert fake.calls == [] and fake.opened == []
        assert await log(client) == []

    served(agent, test)


def test_a_toggle_sushi_cannot_answer_is_a_named_midi_error_not_a_crash():
    """reverb's bypass state is one Sushi would not give (None): a toggle
    cannot know which way, so it refuses and says so in /health. The agent
    carries on, and the next pad works."""
    agent, fake, world, *_ = midi_agent()

    async def test(client):
        port = await started(agent, world, client)
        port.send(0x90, 40, 100)
        await until(lambda: agent._midi["last_error"])
        status = await health(client)
        assert "pad3" in status["last_error"]["message"]
        assert "would not say" in status["last_error"]["message"]
        assert status["state"] == "listening"
        port.send(0x90, 36, 100)
        await until(lambda: fake.writes())
        await settle(agent)
        assert fake.writes() == [("set_bypass", "drive", False)]

    served(agent, test)


def test_a_sushi_that_fails_mid_change_is_remembered_and_the_listener_lives():
    agent, fake, world, *_ = midi_agent()
    fake.fail = RuntimeError("Sushi went sideways")
    fake.fail_on = "set_bypass"

    async def test(client):
        port = await started(agent, world, client)
        port.send(0x90, 36, 100)
        await until(lambda: agent._midi["last_error"])
        status = await health(client)
        assert "Sushi went sideways" in status["last_error"]["message"]
        assert status["state"] == "listening"
        assert await log(client) == [], "the log holds changes Sushi confirmed"

    served(agent, test)


# --------------------------------------------------------------------------- #
# Program Change: a rig switch, by the lifecycle
# --------------------------------------------------------------------------- #

def test_a_program_change_switches_rigs_with_the_restart_the_agent_runs():
    agent, fake, world, runner, rig = midi_agent()

    async def test(client):
        port = await started(agent, world, client)
        port.send(0xC0, 2)  # the third rig by name: "lead"
        await until(lambda: len(runner.calls) == 2)
        await settle(agent)
        # The same options `restart` would bring back, read from the state
        # file; the amp model override belonged to the last rig and is not kept.
        assert runner.calls == [["down"], ["up", "lead", "--rig", "--headless", "--no-tuner"]]
        assert port.closed.is_set(), "the old rig's listener stopped with its down"
        await until(lambda: len(world.ports) == 2 and agent._midi["state"] == "listening")
        assert agent._midi["rig"] == "lead", "and the new rig's listener came up with its up"
        (entry,) = await log(client)
        assert entry["op"] == "rig" and entry["source"] == "midi"
        assert entry["args"] == {"rig": "lead", "program": 2} and entry["previous"] == "guitar_vocal"

    served(agent, test)


def test_a_program_change_out_of_range_is_refused_and_runs_nothing():
    agent, fake, world, runner, _ = midi_agent()

    async def test(client):
        port = await started(agent, world, client)
        port.send(0xC0, 7)
        await until(lambda: agent._midi["last_error"])
        assert "program 7" in (await health(client))["last_error"]["message"]
        assert runner.calls == []

    served(agent, test)


def test_a_failed_switch_is_a_lifecycle_failure_in_health():
    from sushi_rig.agent import Outcome

    agent, fake, world, runner, _ = midi_agent()
    runner.outcome = Outcome(False, 1, "", "could not stop the running rig")

    async def test(client):
        port = await started(agent, world, client)
        port.send(0xC0, 0)
        await until(lambda: agent._midi["last_error"])
        await settle(agent)
        body = await (await client.get("/health")).json()
        assert body["agent"]["last_error"]["kind"] == "lifecycle"
        assert "could not stop" in body["agent"]["midi"]["last_error"]["message"]
        assert runner.calls == [["down"]], "an up does not follow a down that failed"
        await until(lambda: agent._midi["state"] == "listening")
        assert len(world.ports) == 2, "the rig is still up, so its listener is back"

    served(agent, test)


def test_get_rig_says_which_program_is_which_rig_when_programs_switch_rigs():
    agent, *_ = midi_agent()

    async def test(client):
        await agent.midi_adopt()
        body = await (await client.get("/rig")).json()
        assert body["programs"] == [
            {"program": 0, "rig": "clean"}, {"program": 1, "rig": "guitar_vocal"},
            {"program": 2, "rig": "lead"},
        ]

    served(agent, test)


def test_get_rig_has_no_programs_when_the_rig_does_not_switch_by_them():
    agent, *_ = midi_agent(FakeWorld(raw=[PAD_BYPASS]))

    async def test(client):
        await agent.midi_adopt()
        assert "programs" not in (await (await client.get("/rig")).json())

    served(agent, test)


def test_switch_steps_are_down_then_up_with_what_restart_would_bring_back():
    assert switch_steps(STATE, "lead") == [["down"], ["up", "lead", "--rig", "--headless", "--no-tuner"]]
    assert switch_steps({"config_name": "x", "mode": "panel", "tuner_requested": True,
                         "amp_requested": False}, "clean") == [["down"], ["up", "clean", "--rig", "--no-amp"]]
    with pytest.raises(AgentError):
        switch_steps(STATE, "../etc")


# --------------------------------------------------------------------------- #
# The listener follows the lifecycle, and never takes the agent down
# --------------------------------------------------------------------------- #

def test_the_listener_starts_with_up_and_stops_with_down():
    world = FakeWorld()
    agent, fake, _, runner, rig = midi_agent(world, described={"verdict": "absent", "running": False})

    async def test(client):
        await agent.midi_adopt()
        assert agent._midi["state"] == "off" and world.opened == []
        r = await client.post("/rig/up", json={"config_name": "guitar_vocal", "rig": True})
        assert r.status == 200
        await until(lambda: agent._midi["state"] == "listening")
        assert world.opened == [PORT_NAME]
        r = await client.post("/rig/down")
        assert r.status == 200
        assert world.ports[0].closed.is_set()
        status = await health(client)
        assert status["state"] == "off" and status["reason"] == "no rig is running"

    served(agent, test)


def test_a_restart_stops_the_listener_and_starts_a_fresh_one():
    agent, fake, world, runner, _ = midi_agent()

    async def test(client):
        await started(agent, world, client)
        assert (await client.post("/rig/restart")).status == 200
        await until(lambda: len(world.ports) == 2 and agent._midi["state"] == "listening")
        assert world.ports[0].closed.is_set()

    served(agent, test)


def test_a_rig_without_agent_bindings_starts_no_listener():
    world = FakeWorld(plan=False)
    agent, *_ = midi_agent(world)

    async def test(client):
        await agent.midi_adopt()
        status = await health(client)
        assert status["state"] == "off"
        assert status["reason"] == "guitar_vocal has no MIDI bindings for the agent"
        assert world.opened == []

    served(agent, test)


def test_a_port_that_will_not_open_is_a_named_problem_and_is_not_retried():
    world = FakeWorld()
    world.refuse = PortError("no MIDI port called 'MPD218:MPD218 Port A'; ALSA lists: Midi Through:Midi Through Port-0")
    agent, fake, *_ = midi_agent(world)

    async def test(client):
        await agent.midi_adopt()
        await until(lambda: agent._midi["state"] == "failed")
        await asyncio.sleep(0.3)
        body = await (await client.get("/health")).json()
        status = body["agent"]["midi"]
        assert "MPD218:MPD218 Port A" in status["problem"] and "Midi Through" in status["problem"]
        assert "mpd218" in status["problem"], "names the controller the rig is written for"
        assert world.opened == [PORT_NAME], "once, not in a loop"
        assert body["agent"]["last_error"] is None, "the rig and the agent are fine; the pedals are not"
        assert (await client.get("/live")).status == 200

    served(agent, test)


def test_a_port_that_dies_mid_set_is_reported():
    agent, fake, world, *_ = midi_agent()

    async def test(client):
        port = await started(agent, world, client)
        port.inbox.put(PortError("the controller went away"))
        await until(lambda: agent._midi["state"] == "failed")
        assert "the controller went away" in (await health(client))["problem"]

    served(agent, test)


def test_bindings_the_agent_cannot_read_are_a_problem_at_start():
    world = FakeWorld(raw=[dict(PAD_BYPASS, note=300)])
    agent, *_ = midi_agent(world)

    async def test(client):
        await agent.midi_adopt()
        status = await health(client)
        assert status["state"] == "failed" and "0 to 127" in status["problem"]
        assert world.opened == []

    served(agent, test)


def test_a_message_from_a_stopped_listener_is_dropped():
    """A pad pressed as `down` begins was handed over by the old listener;
    acted on afterwards it would reach whatever rig is up next."""
    agent, fake, world, *_ = midi_agent()

    async def test(client):
        await started(agent, world, client)
        old = agent._midi_generation
        await agent._midi_stop("a test")
        from sushi_rig.midi_listener import Message

        agent._midi_heard(old, Message("note_on", 0, 36, 100))
        await settle(agent)
        assert fake.calls == []

    served(agent, test)


def test_the_agent_without_midi_wiring_still_serves():
    """The default world is real: with no rig running it opens nothing."""
    agent = Agent(describe=lambda: {"verdict": "absent", "running": False}, run=FakeRunner())

    async def test(client):
        await agent.midi_adopt()
        assert (await health(client))["state"] == "off"

    served(agent, test)


def test_the_module_imports_without_the_midi_extra():
    """`agent` imports `midi_listener`, which must not import alsa-midi until
    a port is opened: the agent runs without the extra, and says so in /health."""
    import ast
    import inspect

    from sushi_rig import midi_listener

    tree = ast.parse(inspect.getsource(midi_listener))
    top = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))]
    names = {a.name for n in top for a in n.names} | {getattr(n, "module", None) for n in top}
    assert "alsa_midi" not in names
    assert agent_module.midi_listener is midi_listener
