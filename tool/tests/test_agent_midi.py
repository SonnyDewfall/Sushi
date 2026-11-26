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
import json
import queue
import threading
import time

import pytest

aiohttp = pytest.importorskip("aiohttp")
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from sushi_rig import agent as agent_module  # noqa: E402
from sushi_rig.agent import Agent, AgentError, Switching, switch_steps  # noqa: E402
from sushi_rig.rig import RigError  # noqa: E402
from sushi_rig.midi_listener import Off, Plan, PortError, World, parse_bindings  # noqa: E402
from test_agent import UP, FakeLive, FakeRunner  # noqa: E402
from rig_fixtures import whole_plugins  # noqa: E402

PAD_BYPASS = {"control": "pad1", "note": 36, "channel": "all", "bypass": "main/drive"}
PAD_MUTE = {"control": "pad2", "note": 38, "channel": "all", "mute": "main"}
PAD_UNKNOWN = {"control": "pad3", "note": 40, "channel": "all", "bypass": "main/reverb"}
SWITCH = {"control": "stomp", "cc": 80, "channel": "all", "bypass": "main/comp"}
PROGRAMS = {"programs": "rigs", "port": 0, "channel": "all"}
RIGS = ["clean", "guitar_vocal", "lead"]
PORT_NAME = "MPD218:MPD218 Port A"

RIG_UP = dict(UP, config_name="guitar_vocal", rig="config/rigs/guitar_vocal.yaml")
STATE = {"config_name": "guitar_vocal", "rig": "config/rigs/guitar_vocal.yaml",
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
        self.bad_rigs: dict[str, str] = {}  # rig name -> why it would not compile
        self.checked: list[str] = []

    def plan(self, described, root):
        if not described.get("running"):
            return Off("no rig is running")
        if not self.has_plan:
            return Off(f"{described.get('config_name')} has no MIDI bindings for the agent")
        return Plan(rig=described.get("config_name"), controller="mpd218", port=PORT_NAME,
                    bindings=parse_bindings(self.raw))

    def open_port(self, name, link=None):
        self.opened.append(name)
        if self.refuse:
            raise self.refuse
        port = FakePort()
        self.ports.append(port)
        return port

    def world(self):
        return World(open_port=self.open_port, plan=self.plan, rigs=lambda root: list(RIGS))

    def compile(self, name, root, machine):
        """A switch compiles the next rig first (#93), and refuses one that
        would not, before anything is taken down."""
        self.checked.append(name)
        if name in self.bad_rigs:
            raise RigError(self.bad_rigs[name])
        return {"tracks": [], "_meta": {"name": name}}

    def switching(self):
        """The compiled configs here say nothing of their wiring, so every
        switch is a restart, as it was before #93: these tests are about the
        Program Change path, and test_agent.py's about switching live."""
        return Switching(compile=self.compile, read_config=lambda path: {"tracks": []},
                         write_config=lambda name, config: f"/run/rigs/{name}.json",
                         read_state=lambda: dict(self.state), write_state=self.state.update,
                         plugins=whole_plugins)


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
    # The channel's send, last in its chain as the compiler puts it: a mute
    # bypasses it (#210).
    fake.chain.append("main_send")
    fake.bypassed["main_send"] = False
    agent = Agent(describe=rig, run=runner, live=fake, midi=world.world(), switching=world.switching())
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


def test_a_pad_on_a_mute_bypasses_the_channels_send_not_the_tracks_mute():
    """#210: the track's own mute acts after its send, so toggling it was
    heard to do nothing in the monitors. The send bypassed is the mute; the
    track's `mute` and the send's `gain` (a volume pedal's) are not touched."""
    agent, fake, world, *_ = midi_agent()

    async def test(client):
        port = await started(agent, world, client)
        port.send(0x90, 38, 100)
        await until(lambda: fake.writes())
        await settle(agent)
        assert fake.writes() == [("set_bypass", "main_send", True)]
        (entry,) = await log(client)
        assert entry["op"] == "bypass" and entry["args"] == {"processor": "main_send", "bypassed": True}
        assert entry["previous"] is False and entry["source"] == "midi"
        port.send(0x90, 38, 100)
        await until(lambda: len(fake.writes()) == 2)
        await settle(agent)
        assert fake.writes()[-1] == ("set_bypass", "main_send", False), "the next press unmutes it"
        assert fake.info["main"]["mute"]["value"] == 0.0

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
        assert runner.calls == [["down"], ["up", "lead", "--no-tuner"]]
        assert port.closed.is_set(), "the old rig's listener stopped with its down"
        await until(lambda: len(world.ports) == 2 and agent._midi["state"] == "listening")
        assert agent._midi["rig"] == "lead", "and the new rig's listener came up with its up"
        (entry,) = await log(client)
        assert entry["op"] == "rig" and entry["source"] == "midi"
        # How it switched, and why not live (#93): the amp override is the
        # state file's reason, and these fake configs say nothing of wiring.
        assert entry["args"] == {"rig": "lead", "program": 2, "how": "restart",
                                 "why": ["amp_override", "wiring_unknown"]}
        assert entry["previous"] == "guitar_vocal"

    served(agent, test)


def test_a_program_change_switches_live_where_the_two_rigs_allow_it():
    """The Program Change path is `POST /rig/switch`'s (#93): between two rigs
    the live operations can turn one into the other, no restart, the rig read
    back checked, the state file naming the next rig, and the pedals moved to
    it. "lead" here compiles to electric-octave-up's config, and the rig up
    is electric-delay's: the same pedals, other settings."""
    from test_agent import RigLive, SwitchWorld, switch_config

    world = FakeWorld()
    switch_world = SwitchWorld("electric-delay")
    switch_world.compile = lambda name, root, machine: switch_config({"lead": "electric-octave-up"}[name])
    runner = FakeRunner()
    fake = RigLive(switch_config("electric-delay"))
    agent = Agent(describe=switch_world.described, run=runner, live=fake, midi=world.world(),
                  switching=switch_world.switching())

    async def test(client):
        port = await started(agent, world, client)
        port.send(0xC0, 2)  # "lead"
        await until(lambda: switch_world.written_states)
        await settle(agent)
        assert runner.calls == [], "switched live, not restarted"
        assert fake.writes(), "through the live operations"
        assert switch_world.state["config_name"] == "lead"
        assert port.closed.is_set(), "the old rig's listener stopped for the switch"
        await until(lambda: len(world.ports) == 2 and agent._midi["state"] == "listening")
        assert agent._midi["rig"] == "lead", "and the next rig's listener came up"
        (entry,) = await log(client)
        assert entry["op"] == "rig" and entry["source"] == "midi"
        assert entry["args"]["how"] == "live" and entry["args"]["program"] == 2
        assert entry["previous"] == "electric-delay"

    served(agent, test)


def test_a_program_change_to_a_rig_file_with_a_yaml_typo_is_refused_and_said(tmp_path):
    """The real compile, through the Program Change path: the switch is
    refused with the file named, in /health and the listener's own error,
    and the pedals stay on the rig that is still playing."""
    (tmp_path / "config" / "rigs").mkdir(parents=True)
    (tmp_path / "config" / "rigs" / "lead.yaml").write_text("kind: [rig\n")
    agent, fake, world, runner, _ = midi_agent()
    agent.root = tmp_path
    agent.switching = Switching(compile=Switching().compile, read_state=lambda: dict(world.state),
                                write_state=world.state.update)

    async def test(client):
        port = await started(agent, world, client)
        port.send(0xC0, 2)  # "lead"
        await until(lambda: agent._midi["last_error"])
        await settle(agent)
        body = await (await client.get("/health")).json()
        assert body["agent"]["last_error"]["kind"] == "lifecycle"
        for message in (body["agent"]["last_error"]["message"], body["agent"]["midi"]["last_error"]["message"]):
            assert "lead.yaml" in message and "Error" in message, message
        assert runner.calls == []
        assert not port.closed.is_set() and agent._midi["state"] == "listening"

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
    assert switch_steps(STATE, "lead") == [["down"], ["up", "lead", "--no-tuner"]]
    no_amp = {"config_name": "x", "tuner_requested": True, "amp_requested": False}
    assert switch_steps(no_amp, "x") == [["down"], ["up", "x", "--no-amp"]]
    # Into another rig the run's --no-amp is not carried (#246): it was, and
    # the owner switched to a rig with an amp and heard none.
    assert switch_steps(no_amp, "clean") == [["down"], ["up", "clean"]]
    with pytest.raises(AgentError):
        switch_steps(STATE, "../etc")


def test_a_switch_by_restart_keeps_the_visualiser_tap_declined():
    """`--no-visualiser-tap` is kept in the state file, as `--no-tuner` is,
    and the next rig comes up without the tap too."""
    off = {"config_name": "x", "tuner_requested": True, "amp_requested": True,
           "visualiser_tap_requested": False}
    assert switch_steps(off, "clean") == [["down"], ["up", "clean", "--no-visualiser-tap"]]
    assert "--no-visualiser-tap" not in switch_steps({**off, "visualiser_tap_requested": True}, "clean")[1]


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


# --------------------------------------------------------------------------- #
# Review of the branch: a switch is a barrier to undo, a failed switch, release,
# switch state, overlapping channels, `up` stops the listener, the loop
# --------------------------------------------------------------------------- #

def test_undo_does_not_walk_past_a_rig_switch():
    """Both rigs can have a "drive"; undoing the old rig's bypass against the
    new one would change a plugin nobody touched since the switch."""
    agent, fake, world, runner, _ = midi_agent()

    async def test(client):
        port = await started(agent, world, client)
        port.send(0x90, 36, 100)                     # a bypass on guitar_vocal
        await until(lambda: fake.writes())
        await settle(agent)
        port.send(0xC0, 2)                           # switch to lead
        await until(lambda: len(runner.calls) == 2)
        await settle(agent)
        for body in (None, {"skip": True}):
            r = await client.post("/live/undo", json=body)
            answer = await r.json()
            assert r.status == 409 and answer["code"] == "nothing_to_undo", answer
            assert "rig switch" in answer["reason"]
        assert fake.writes() == [("set_bypass", "drive", False)], "nothing was put back"
        assert [e["op"] for e in await log(client)] == ["bypass", "rig"], "and nothing marked stale"

    served(agent, test)


def test_a_change_after_a_switch_can_still_be_undone():
    agent, fake, world, runner, _ = midi_agent()

    async def test(client):
        port = await started(agent, world, client)
        port.send(0xC0, 2)
        await until(lambda: len(runner.calls) == 2)
        await settle(agent)
        await until(lambda: agent._midi["state"] == "listening")
        world.ports[-1].send(0x90, 36, 100)
        await until(lambda: fake.writes())
        await settle(agent)
        r = await client.post("/live/undo")
        assert r.status == 200, await r.json()

    served(agent, test)


def test_a_switch_to_a_rig_that_does_not_compile_leaves_the_rig_up():
    world = FakeWorld()
    world.bad_rigs["lead"] = "lead.yaml: midi: 'pad9' is not a control of controller 'mpd218'"
    agent, fake, _, runner, _ = midi_agent(world)

    async def test(client):
        port = await started(agent, world, client)
        port.send(0xC0, 2)
        await until(lambda: agent._midi["last_error"])
        await settle(agent)
        assert runner.calls == [], "the running rig is not taken down for a rig that cannot come up"
        body = await (await client.get("/health")).json()
        assert "pad9" in body["agent"]["last_error"]["message"]
        assert "pad9" in body["agent"]["midi"]["last_error"]["message"]
        assert agent._midi["state"] == "listening" and len(world.ports) == 1
        assert not port.closed.is_set(), "the pedals stay on the rig that is still playing"

    served(agent, test)


def test_a_switch_whose_up_fails_brings_the_previous_rig_back():
    from sushi_rig.agent import Outcome

    rig = Rig(RIG_UP)

    class UpOfLeadFails(SwitchingRunner):
        async def __call__(self, args):
            self.outcome = Outcome(False, 1, "", "Sushi never came up") if args[:2] == ["up", "lead"] \
                else Outcome(True, 0, "", "")
            return await super().__call__(args)

    runner = UpOfLeadFails(rig)
    agent, fake, world, _, _ = midi_agent(runner=runner)
    agent.describe = rig
    runner.rig = rig

    async def test(client):
        port = await started(agent, world, client)
        port.send(0xC0, 2)
        await until(lambda: len(runner.calls) == 3)
        await settle(agent)
        assert runner.calls == [
            ["down"], ["up", "lead", "--no-tuner"],
            ["up", "guitar_vocal", "--no-tuner"],
        ]
        await until(lambda: agent._midi["state"] == "listening")
        assert agent._midi["rig"] == "guitar_vocal", "and its pedals with it"
        body = await (await client.get("/health")).json()
        message = body["agent"]["last_error"]["message"]
        assert "Sushi never came up" in message and "guitar_vocal was brought back" in message

    served(agent, test)


def test_release_during_a_switch_starts_no_listener_after_it():
    """A switch cancelled by the agent stopping runs `_run_lifecycle`'s
    finally; that must not open the port again once the agent has let go."""
    started_up, hang = asyncio.Event(), asyncio.Event()
    rig = Rig(RIG_UP)

    class HangingUp(SwitchingRunner):
        async def __call__(self, args):
            if args[0] == "up":
                started_up.set()
                await hang.wait()
            return await super().__call__(args)

    runner = HangingUp(rig)
    agent, fake, world, _, _ = midi_agent(runner=runner)
    agent.describe = rig

    async def go():
        try:
            async with TestClient(TestServer(agent.app())) as client:
                port = await started(agent, world, client)
                port.send(0xC0, 2)
                await asyncio.wait_for(started_up.wait(), 3)
                rig.described = dict(RIG_UP)  # as if the rig were up again
                await agent.midi_release()
                await asyncio.sleep(0.3)
                assert len(world.opened) == 1, "no listener after release"
                assert agent._listener is None
                await agent._midi_sync()
                assert len(world.opened) == 1, "and a sync after release does nothing"
        finally:
            agent._executor.shutdown(wait=True, cancel_futures=True)

    asyncio.run(go())


def test_a_dropped_switch_action_does_not_count_as_a_crossing(monkeypatch):
    """The switch's state is what Sushi was set to, not what was decided: a
    crossing whose action was dropped (or failed) is played again by the next
    value on that side of 64."""
    agent, fake, world, *_ = midi_agent()

    async def test(client):
        port = await started(agent, world, client)
        monkeypatch.setattr(agent_module, "MIDI_PENDING", 0)
        port.send(0xB0, 80, 0)                      # dropped: nothing waits, nothing set
        await until(lambda: agent._midi["last_error"])
        assert fake.writes() == []
        monkeypatch.setattr(agent_module, "MIDI_PENDING", 16)
        port.send(0xB0, 80, 5)                      # the same side of 64: the crossing again
        await until(lambda: fake.writes())
        await settle(agent)
        assert fake.writes() == [("set_bypass", "comp", True)]

    served(agent, test)


def test_a_burst_on_one_side_of_64_is_one_crossing():
    """A pedal sends several values as it moves: those after the first on the
    same side are not crossings, even before the first one's action has run,
    so they queue nothing and read nothing from Sushi."""
    agent, fake, world, *_ = midi_agent()

    async def test(client):
        port = await started(agent, world, client)
        for value in (20, 15, 10, 5, 0):
            port.send(0xB0, 80, value)
        await until(lambda: agent._midi_status()["heard"] == 5)
        await until(lambda: fake.writes())
        await settle(agent)
        assert fake.writes() == [("set_bypass", "comp", True)]
        assert [c for c in fake.calls if c[0] == "bypass"] == [("bypass", "comp")]

    served(agent, test)


def test_a_failed_switch_action_does_not_count_as_a_crossing():
    agent, fake, world, *_ = midi_agent()
    fake.fail, fake.fail_on = RuntimeError("Sushi went sideways"), "set_bypass"

    async def test(client):
        port = await started(agent, world, client)
        port.send(0xB0, 80, 0)
        await until(lambda: agent._midi["last_error"])
        await settle(agent)
        fake.fail = None
        port.send(0xB0, 80, 5)
        await until(lambda: len(fake.writes()) == 2)
        await settle(agent)
        assert fake.writes()[-1] == ("set_bypass", "comp", True)

    served(agent, test)


def test_up_stops_a_listener_still_following_a_rig_that_died():
    """A rig that died outside the agent leaves its listener running; `up`
    must stop it before starting the next rig, so a press during `up` cannot
    reach the rig coming up."""
    agent, fake, world, runner, rig = midi_agent()

    async def test(client):
        port = await started(agent, world, client)
        rig.described = {"verdict": "gone", "running": False}
        seen = []

        async def watching(args):
            seen.append((agent._listener is None, port.closed.is_set()))
            return await SwitchingRunner.__call__(runner, args)

        agent.run = watching
        r = await client.post("/rig/up", json={"config_name": "guitar_vocal", "rig": True})
        assert r.status == 200
        assert seen == [(True, True)], "stopped before the up ran"

    served(agent, test)


def test_the_program_order_is_read_off_the_loop():
    loop_threads = set()
    agent, fake, world, runner, _ = midi_agent()
    reads = []

    def rigs(root):
        reads.append(threading.get_ident())
        return list(RIGS)

    agent._midi_world = World(open_port=world.open_port, plan=world.plan, rigs=rigs)

    async def test(client):
        loop_threads.add(threading.get_ident())
        port = await started(agent, world, client)
        assert "programs" in await (await client.get("/rig")).json()
        port.send(0xC0, 2)
        await until(lambda: len(runner.calls) == 2)
        await settle(agent)
        assert len(reads) >= 2 and not set(reads) & loop_threads

    served(agent, test)


# --------------------------------------------------------------------------- #
# The pedalboard (#191): A and B step rigs on every rig, a port per controller
# --------------------------------------------------------------------------- #

from sushi_rig import midi_listener  # noqa: E402
from sushi_rig.midi_listener import Bindings, Feed  # noqa: E402

FBV_PORT = "FBV Express Mk II:FBV Express Mk II MIDI 1"
FBV = {
    "port": FBV_PORT, "channel": 0, "type": "pedal",
    "controls": {"a": ("cc", 20), "b": ("cc", 21), "c": ("cc", 22)},
    "model": {
        "a": {"kind": "switch", "behaviour": "momentary", "range": None, "job": "previous-rig"},
        "b": {"kind": "switch", "behaviour": "momentary", "range": None, "job": "next-rig"},
        "c": {"kind": "switch", "behaviour": "momentary", "range": None, "job": None},
    },
}
MOMENTARY_MUTE = {"control": "c", "cc": 22, "channel": 0, "mute": "main", "switch": "momentary"}


class PedalboardWorld(FakeWorld):
    """The MPD218's map on its port, and the FBV's steps (and `fbv_raw`, its
    own map's bindings) on another: two feeds, as `plan_for` makes them for
    a rig mapped on the MPD218 on a machine with the FBV."""

    def __init__(self, raw=None, fbv_raw=(), refuse_port=None, absent_port=None, unavailable=None,
                 fbv_required=False):
        super().__init__(raw=raw)
        self.fbv_raw = list(fbv_raw)
        self.refuse_port = refuse_port
        self.absent_port = absent_port  # opens with its pedal not plugged in, as AlsaPort does
        self.unavailable = unavailable  # MidiUnavailable from opening it: no alsa-midi
        self.fbv_required = fbv_required
        self.named: dict[str, FakePort] = {}
        self.compiling = threading.Event()
        self.hold = None  # a threading.Event a compile waits on

    def plan(self, described, root):
        if not described.get("running"):
            return Off("no rig is running")
        fbv = Bindings(parse_bindings(self.fbv_raw).controls + midi_listener.step_bindings(FBV))
        feeds = (Feed("mpd218", PORT_NAME, parse_bindings(self.raw)),) if self.raw else ()
        return midi_listener.Plan(described.get("config_name"),
                                  feeds=feeds + (Feed("fbv", FBV_PORT, fbv, self.fbv_required),))

    def open_port(self, name, link=None):
        if name == self.refuse_port:
            self.opened.append(name)
            raise PortError(f"cannot subscribe to {name!r}: Operation not permitted")
        if name == self.unavailable:
            self.opened.append(name)
            raise midi_listener.MidiUnavailable("alsa-midi is not installed, so the agent cannot read pedals")
        port = super().open_port(name)
        if name == self.absent_port:
            port.attached = False
            port.absence = f"no MIDI port called {name!r}; ALSA lists: MPD218:MPD218 Port A. Is it plugged in?"
        self.named[name] = port
        return port

    def compile(self, name, root, machine):
        self.compiling.set()
        if self.hold is not None:
            assert self.hold.wait(5), "the test never let the compile go"
        return super().compile(name, root, machine)


def press(port, number):
    port.send(0xB0, number, 127)
    port.send(0xB0, number, 0)


def test_b_steps_to_the_next_rig_by_the_switch_path_and_is_logged_with_its_switch():
    world = PedalboardWorld()
    agent, fake, _, runner, rig = midi_agent(world)

    async def test(client):
        await agent.midi_adopt()
        await until(lambda: FBV_PORT in world.named and agent._midi["state"] == "listening")
        assert world.opened == [PORT_NAME, FBV_PORT], "a port for each controller"
        press(world.named[FBV_PORT], 21)  # b, on guitar_vocal: the next is lead
        await until(lambda: len(runner.calls) == 2)
        await settle(agent)
        assert runner.calls == [["down"], ["up", "lead", "--no-tuner"]]
        (entry,) = await log(client)
        assert entry["op"] == "rig" and entry["source"] == "midi" and entry["control"] == "b"
        assert entry["args"]["rig"] == "lead" and "program" not in entry["args"]
        await until(lambda: agent._midi["state"] == "listening" and agent._midi["rig"] == "lead")

    served(agent, test)


def test_a_step_past_either_end_is_refused_said_and_runs_nothing():
    world = PedalboardWorld()
    agent, fake, _, runner, rig = midi_agent(world, described=dict(RIG_UP, config_name="lead"))

    async def test(client):
        await agent.midi_adopt()
        await until(lambda: FBV_PORT in world.named and agent._midi["state"] == "listening")
        press(world.named[FBV_PORT], 21)  # b on the last rig
        await until(lambda: agent._midi["last_error"])
        await settle(agent)
        assert "lead is the last rig" in (await health(client))["last_error"]["message"]
        assert runner.calls == [], "no wrap round to the first rig"

    served(agent, test)


def test_a_press_while_a_switch_is_running_is_refused_not_queued():
    """The second press arrives while the first switch is still compiling its
    rig: it is refused and said, and once the first is done nothing else
    runs. (Later in a switch the ports are closed, and a press is not heard.)"""
    world = PedalboardWorld()
    world.hold = threading.Event()
    agent, fake, _, runner, rig = midi_agent(world)

    async def test(client):
        await agent.midi_adopt()
        await until(lambda: FBV_PORT in world.named and agent._midi["state"] == "listening")
        fbv = world.named[FBV_PORT]
        press(fbv, 21)
        await until(world.compiling.is_set)
        await asyncio.sleep(midi_listener.DEBOUNCE_SECONDS * 2)  # a second press, not a bounce
        press(fbv, 21)
        await until(lambda: agent._midi["last_error"])
        assert "still running" in agent._midi["last_error"]["message"]
        world.hold.set()
        await until(lambda: len(runner.calls) == 2)
        await settle(agent)
        await asyncio.sleep(0.1)
        await settle(agent)
        assert world.checked == ["lead"], "one switch: the second press was not queued behind it"
        assert runner.calls == [["down"], ["up", "lead", "--no-tuner"]]

    served(agent, test)


def test_a_press_while_a_lifecycle_command_holds_the_rig_is_refused():
    world = PedalboardWorld()
    agent, fake, _, runner, rig = midi_agent(world)

    async def test(client):
        await agent.midi_adopt()
        await until(lambda: FBV_PORT in world.named and agent._midi["state"] == "listening")
        async with agent._lock:
            agent.busy = "restart"
            agent._midi_heard(agent._midi_generation, midi_listener.Message("cc", 0, 21, 127), feed=1)
            agent.busy = None
        assert "busy: 'restart'" in agent._midi["last_error"]["message"]
        await settle(agent)
        assert runner.calls == [] and world.checked == []

    served(agent, test)


def test_each_port_is_served_and_one_that_will_not_open_leaves_the_other():
    """A port that will not open, for a reason other than its pedal being
    absent, is a fault and named; the other port still serves."""
    world = PedalboardWorld(refuse_port=FBV_PORT)
    agent, fake, _, runner, rig = midi_agent(world)

    async def test(client):
        await agent.midi_adopt()
        await until(lambda: agent._midi["state"] == "failed" and PORT_NAME in world.named)
        status = await health(client)
        assert FBV_PORT in status["problem"] and "'fbv'" in status["problem"]
        await until(lambda: agent._midi["feeds"][0]["state"] == "listening")
        world.named[PORT_NAME].send(0x90, 36, 100)  # the MPD218's pad still works
        await until(lambda: fake.writes())
        await settle(agent)
        assert fake.writes() == [("set_bypass", "drive", False)]

    served(agent, test)


def test_a_stepping_pedal_that_is_not_plugged_in_is_not_connected_not_a_problem():
    """Review of PR #199: the laptop has no FBV yet, and every rig showed a
    Pedals problem. A pedal no rig map uses that is not there is `absent`,
    and the rig's own pedal is listening, with no problem to show."""
    world = PedalboardWorld(absent_port=FBV_PORT)
    agent, fake, _, runner, rig = midi_agent(world)

    async def test(client):
        await agent.midi_adopt()
        await until(lambda: [f["state"] for f in agent._midi["feeds"]] == ["listening", "absent"])
        status = await health(client)
        assert status["state"] == "listening" and status["problem"] is None
        fbv = status["feeds"][1]
        assert fbv["controller"] == "fbv" and fbv["required"] is False
        assert fbv["reason"].startswith("not connected: no MIDI port called")
        world.named[PORT_NAME].send(0x90, 36, 100)
        await until(lambda: fake.writes())
        await settle(agent)
        assert fake.writes() == [("set_bypass", "drive", False)]

    served(agent, test)


def test_a_stepping_pedal_alone_and_absent_is_absent_and_said_nowhere_as_a_fault():
    world = PedalboardWorld(raw=[], absent_port=FBV_PORT)
    agent, *_ = midi_agent(world)

    async def test(client):
        await agent.midi_adopt()
        await until(lambda: agent._midi["state"] == "absent")
        status = await health(client)
        assert status["problem"] is None and "no pedal is connected" in status["reason"]
        assert (await (await client.get("/health")).json())["agent"]["last_error"] is None

    served(agent, test)


def test_no_alsa_midi_is_quiet_for_a_pedal_no_rig_map_needs_and_a_fault_for_one_it_does():
    world = PedalboardWorld(raw=[], unavailable=FBV_PORT)
    agent, *_ = midi_agent(world)

    async def test(client):
        await agent.midi_adopt()
        await until(lambda: agent._midi["state"] == "absent")
        assert "alsa-midi is not installed" in agent._midi["feeds"][0]["reason"]
        assert agent._midi["problem"] is None

    served(agent, test)
    world = PedalboardWorld(raw=[], unavailable=FBV_PORT, fbv_required=True)
    agent, *_ = midi_agent(world)

    async def needed(client):
        await agent.midi_adopt()
        await until(lambda: agent._midi["state"] == "failed")
        assert "alsa-midi is not installed" in agent._midi["problem"]

    served(agent, needed)


def test_a_required_pedal_unplugged_is_a_problem_until_it_is_plugged_in_again():
    """Hot-plug (review of PR #199): the listener waits on the port, and the
    agent follows it: waiting, a problem, for the pedal the rig's map is on;
    listening once it is back, with no up or restart."""
    world = PedalboardWorld(raw=[], absent_port=FBV_PORT, fbv_required=True)
    agent, *_ = midi_agent(world)

    async def test(client):
        await agent.midi_adopt()
        await until(lambda: agent._midi["state"] == "failed")
        assert agent._midi["feeds"][0]["state"] == "waiting"
        assert "is not connected" in agent._midi["problem"] and "Waiting for it" in agent._midi["problem"]
        port = world.named[FBV_PORT]
        port.attached = True
        await until(lambda: agent._midi["state"] == "listening")
        assert agent._midi["problem"] is None
        port.absence, port.attached = "the controller's port went away: was it unplugged?", False
        await until(lambda: agent._midi["state"] == "failed")
        assert "went away" in agent._midi["problem"]
        assert world.opened == [FBV_PORT], "the one port, never reopened"

    served(agent, test)


def test_a_momentary_footswitch_on_a_mute_toggles_it_on_each_press():
    """Sushi's own CC mute would follow the value: muted while held."""
    world = PedalboardWorld(raw=[], fbv_raw=[MOMENTARY_MUTE])
    agent, fake, _, runner, rig = midi_agent(world)

    async def test(client):
        await agent.midi_adopt()
        await until(lambda: FBV_PORT in world.named and agent._midi["state"] == "listening")
        assert world.opened == [FBV_PORT]
        fbv = world.named[FBV_PORT]
        press(fbv, 22)
        await until(lambda: fake.writes())
        await settle(agent)
        assert fake.writes() == [("set_bypass", "main_send", True)], "the release did not unmute it"
        press(fbv, 22)
        await until(lambda: len(fake.writes()) == 2)
        await settle(agent)
        assert fake.writes()[-1] == ("set_bypass", "main_send", False)

    served(agent, test)


# The real plan, from real files: a rig with no midi: on a machine with the FBV.

FBV_MACHINE = """\
schema_version: 1
roles:
  guitar-in: {ports: ["Audient iD14:capture_AUX0"]}
controllers:
  fbv:
    type: pedal
    port: "FBV Express Mk II:FBV Express Mk II MIDI 1"
    channel: 0
    controls:
      a: {cc: 20, kind: switch, behaviour: momentary, job: previous-rig}
      b: {cc: 21, kind: switch, behaviour: momentary, job: next-rig}
      c: {cc: 22, kind: switch, behaviour: momentary}
      pedal: {cc: 7, kind: expression, range: [0, 127]}
"""

PLAIN_RIG = """\
kind: rig
schema_version: 1
name: {name}
channels:
- {{name: main, input: guitar-in, width: mono, chain: [{{name: drive, uri: "http://drobilla.net/plugins/mda/Overdrive"}}]}}
master: {{output: monitor-out, chain: []}}
"""


def pedal_files(root, midi=""):
    (root / "config" / "rigs").mkdir(parents=True)
    for name in RIGS:
        (root / "config" / "rigs" / f"{name}.yaml").write_text(PLAIN_RIG.format(name=name) + midi)
    (root / "machines").mkdir()
    (root / "machines" / "box.yaml").write_text(FBV_MACHINE)
    compiled = root / "run" / "rigs" / "guitar_vocal.json"
    compiled.parent.mkdir(parents=True)
    compiled.write_text(json.dumps({"tracks": []}))
    return dict(RIG_UP, config=str(compiled), machine=str(root / "machines" / "box.yaml"))


def test_a_rig_with_no_map_has_its_steps_served_and_the_listener_follows_the_lifecycle(tmp_path):
    """A and B on every rig: the real `plan_for`, reading the machine file, on
    a rig whose file has no midi:. The listener opens with the rig, closes
    for `down`, opens again with `up`, and is replaced by `restart`."""
    described = pedal_files(tmp_path)
    world = FakeWorld()
    rig = Rig(described)

    class Runner(SwitchingRunner):
        async def __call__(self, args):
            outcome = await FakeRunner.__call__(self, args)
            if outcome.ok and args[0] == "down":
                self.rig.described = {"verdict": "absent", "running": False, "message": "No rig is running."}
            elif outcome.ok and args[0] in ("up", "restart"):
                self.rig.described = dict(described)
            return outcome

    runner = Runner(rig)
    agent, fake, _, _, _ = midi_agent(world, runner=runner)
    agent.describe = rig
    agent.root = tmp_path
    agent._midi_world = World(open_port=world.open_port, rigs=lambda root: list(RIGS))

    async def test(client):
        await agent.midi_adopt()
        await until(lambda: agent._midi["state"] == "listening")
        assert world.opened == [FBV_PORT]
        assert agent._midi["controller"] == "fbv" and agent._midi["rig"] == "guitar_vocal"
        assert (await client.post("/rig/down")).status == 200
        assert world.ports[0].closed.is_set()
        assert (await health(client))["state"] == "off"
        r = await client.post("/rig/up", json={"config_name": "guitar_vocal", "rig": True})
        assert r.status == 200
        await until(lambda: len(world.ports) == 2 and agent._midi["state"] == "listening")
        assert (await client.post("/rig/restart")).status == 200
        await until(lambda: len(world.ports) == 3 and agent._midi["state"] == "listening")
        assert world.ports[1].closed.is_set()
        assert world.opened == [FBV_PORT] * 3

    served(agent, test)


def test_get_midi_gives_the_devices_what_each_control_may_take_and_the_rigs_assignments(tmp_path):
    described = pedal_files(tmp_path, midi=(
        "midi:\n  controller: fbv\n  controls:\n"
        "  - {control: c, bypass: main/drive}\n"
        "  - {control: pedal, volume: main}\n"))
    agent, *_ = midi_agent(FakeWorld(plan=False), described=described)
    agent.root = tmp_path

    async def test(client):
        r = await client.get("/midi")
        assert r.status == 200
        body = await r.json()
        assert body["machine"] == "box.yaml" and body["machine_problem"] is None
        (fbv,) = body["controllers"]
        assert fbv["name"] == "fbv" and fbv["type"] == "pedal" and fbv["port"] == FBV_PORT
        controls = {c["name"]: c for c in fbv["controls"]}
        assert controls["a"] == {"name": "a", "sends": {"cc": 20}, "kind": "switch", "behaviour": "momentary",
                                 "range": None, "job": "previous-rig", "takes": []}
        assert controls["c"]["takes"] == ["bypass", "mute"]
        assert controls["pedal"]["takes"] == ["volume", "parameter", "blend"]
        assert controls["pedal"]["range"] == [0, 127]
        assert body["targets"]["volume"] == "a channel name, not the master"
        assert body["rig"] == "guitar_vocal"
        assert body["assignments"] == {"controller": "fbv", "port": 0, "programs": None, "controls": [
            {"control": "c", "bypass": "main/drive"}, {"control": "pedal", "volume": "main"}]}
        assert body["rigs"] == RIGS

    served(agent, test)


def test_get_midi_with_no_rig_up_and_a_machine_file_that_will_not_read(tmp_path):
    pedal_files(tmp_path)
    (tmp_path / "machines" / "box.yaml").write_text(FBV_MACHINE.replace("kind: expression", "kind: knob"))
    agent, *_ = midi_agent(FakeWorld(plan=False), described={
        "verdict": "absent", "running": False, "machine": str(tmp_path / "machines" / "box.yaml")})
    agent.root = tmp_path

    async def test(client):
        body = await (await client.get("/midi")).json()
        assert body["controllers"] == [] and "kind must be switch or expression" in body["machine_problem"]
        assert body["rig"] is None and body["assignments"] is None

    served(agent, test)


# A pedal on a volume (review of PR #199): silent at the heel, by the agent.

SNAP = {"control": "pedal", "cc": 7, "channel": 0, "snap": "main", "heel": 2, "low": 0.5, "high": 0.8}


def snap_agent():
    world = PedalboardWorld(raw=[], fbv_raw=[SNAP], fbv_required=True)
    agent, fake, _, runner, rig = midi_agent(world)
    fake.info["main_send"] = {"gain": {"automatable": True, "value": 0.8,
                                       "min_domain_value": 0.0, "max_domain_value": 1.0}}
    return agent, fake, world


def gains(fake):
    return [c[3] for c in fake.writes() if c[:3] == ("set_parameter", "main_send", "gain")]


def test_a_volume_pedal_at_its_heel_is_silenced_and_above_it_left_to_sushi():
    agent, fake, world = snap_agent()

    async def test(client):
        await agent.midi_adopt()
        await until(lambda: agent._midi["state"] == "listening")
        fbv = world.named[FBV_PORT]
        fbv.send(0xB0, 7, 90)        # above the heel: Sushi's mapping alone
        fbv.send(0xB0, 7, 2)         # at the heel: silent
        await until(lambda: gains(fake))
        await settle(agent)
        assert gains(fake) == [0.0]
        assert await log(client) == [], "a pedal's position is not a change to undo or save"

    served(agent, test)


def test_a_silencing_that_lands_after_the_pedal_left_the_heel_puts_sushis_value_back():
    """The agent and Sushi's mapping must not fight: the pedal comes up off the
    heel while the agent is still silencing it, and the agent finishes on the
    value the mapping gives for where the pedal is now, not on silence."""
    agent, fake, world = snap_agent()
    fake.hang_on = "set_parameter"

    async def test(client):
        await agent.midi_adopt()
        await until(lambda: agent._midi["state"] == "listening")
        fbv = world.named[FBV_PORT]
        fbv.send(0xB0, 7, 0)
        await asyncio.get_running_loop().run_in_executor(None, fake.reached.wait, 3)
        fbv.send(0xB0, 7, 100)
        await until(lambda: agent._midi_pedal.get((0, "pedal"), (None,))[0] == 100)
        fake.release.set()
        await until(lambda: len(gains(fake)) == 2)
        await settle(agent)
        assert gains(fake) == [0.0, pytest.approx(0.5 + 0.3 * 100 / 127)]

    served(agent, test)


# `last` says what became of a press (review of PR #199).

def test_a_refused_step_and_a_refused_program_change_are_what_last_says():
    world = PedalboardWorld()
    agent, fake, _, runner, rig = midi_agent(world, described=dict(RIG_UP, config_name="lead"))

    async def test(client):
        await agent.midi_adopt()
        await until(lambda: FBV_PORT in world.named and agent._midi["state"] == "listening")
        world.named[FBV_PORT].send(0xB0, 21, 127)  # b pressed and held: no release after it
        await until(lambda: agent._midi["last_error"])
        await settle(agent)
        last = (await health(client))["last"]
        assert "refused" in last["action"] and "lead is the last rig" in last["action"], last
        world.named[PORT_NAME].send(0xC0, 7)
        await until(lambda: "program 7" in (agent._midi["last_error"] or {}).get("message", ""))
        await settle(agent)
        last = (await health(client))["last"]
        assert last["message"] == "program change 7 on channel 0"
        assert "refused" in last["action"] and "has no rig" in last["action"], last

    served(agent, test)


def test_a_press_refused_during_a_switch_is_what_last_says():
    world = PedalboardWorld()
    agent, fake, _, runner, rig = midi_agent(world)

    async def test(client):
        await agent.midi_adopt()
        await until(lambda: FBV_PORT in world.named and agent._midi["state"] == "listening")
        async with agent._lock:
            agent.busy = "restart"
            agent._midi_heard(agent._midi_generation, midi_listener.Message("cc", 0, 21, 127), feed=1)
            agent.busy = None
        assert agent._midi["last"]["action"].startswith("refused, busy: 'restart'")

    served(agent, test)


# PR #199's re-review: the snap across a switch, and a silence that landed late.

def test_a_silencing_still_waiting_for_the_last_rig_does_not_hold_up_the_next_rigs():
    """The snap's key is the same control on the next rig: a task still
    waiting on Sushi for the rig before must not stop the next rig's heel
    from being silenced."""
    agent, fake, world = snap_agent()
    fake.hang_on = "set_parameter"

    async def test(client):
        await agent.midi_adopt()
        await until(lambda: agent._midi["state"] == "listening")
        world.named[FBV_PORT].send(0xB0, 7, 0)
        await asyncio.get_running_loop().run_in_executor(None, fake.reached.wait, 3)
        await agent._midi_stop("a switch")   # as a switch does: the next rig, the same pedal
        await agent._midi_sync()
        await until(lambda: agent._midi["state"] == "listening")
        world.named[FBV_PORT].send(0xB0, 7, 0)
        await until(lambda: agent._midi_pedal.get((0, "pedal")) is not None)
        await asyncio.sleep(0.05)
        assert any(key[0] == agent._midi_generation for key in agent._midi_snapping), \
            "the next rig's silencing is under way"
        fake.release.set()
        await until(lambda: len(gains(fake)) == 2)
        await settle(agent)
        assert gains(fake) == [0.0, 0.0]

    served(agent, test)


def test_a_rise_after_the_agent_silenced_the_pedal_sets_sushis_value_again():
    """The silence can land after Sushi has followed a rise whose message the
    agent had not yet read: the agent then believes the pedal is at the heel.
    So the next message above the heel for a pedal it silenced has the agent
    set Sushi's value, once."""
    agent, fake, world = snap_agent()

    async def test(client):
        await agent.midi_adopt()
        await until(lambda: agent._midi["state"] == "listening")
        fbv = world.named[FBV_PORT]
        fbv.send(0xB0, 7, 0)
        await until(lambda: gains(fake) == [0.0])
        await settle(agent)
        fbv.send(0xB0, 7, 90)
        await until(lambda: len(gains(fake)) == 2)
        await settle(agent)
        fbv.send(0xB0, 7, 100)
        await asyncio.sleep(0.1)
        await settle(agent)
        assert gains(fake) == [0.0, pytest.approx(0.5 + 0.3 * 90 / 127)], "once, then Sushi's alone"

    served(agent, test)


def test_an_unreadable_rig_file_is_said_in_health():
    class Unread(PedalboardWorld):
        def plan(self, described, root):
            plan = super().plan(described, root)
            return dataclasses.replace(plan, rig_problem="could not read config/rigs/x.yaml to say which pedal")

    import dataclasses

    world = Unread(raw=[], absent_port=FBV_PORT)
    agent, *_ = midi_agent(world)

    async def test(client):
        await agent.midi_adopt()
        await until(lambda: agent._midi["state"] == "absent")
        assert (await health(client))["rig_problem"].startswith("could not read config/rigs/x.yaml")

    served(agent, test)


# --------------------------------------------------------------------------- #
# #210: a mute the monitors hear, beside a volume pedal on the same channel
# --------------------------------------------------------------------------- #

LATCHING_MUTE = {"control": "c", "cc": 22, "channel": 0, "mute": "main", "switch": "latching"}


def test_a_latching_mute_bypasses_the_send_and_leaves_the_volume_pedals_gain_alone():
    """The toe switch's way: 127 mutes, 0 unmutes, through the send's bypass,
    which the agent serves (Sushi maps no CC to a bypass). The volume pedal
    on the same channel drives the send's gain, which the mute never sets,
    and the pedal's silence at the heel never un-bypasses the send: the two
    do not fight."""
    world = PedalboardWorld(raw=[], fbv_raw=[SNAP, LATCHING_MUTE], fbv_required=True)
    agent, fake, _, runner, rig = midi_agent(world)
    fake.info["main_send"] = {"gain": {"automatable": True, "value": 0.8,
                                       "min_domain_value": 0.0, "max_domain_value": 1.0}}

    async def test(client):
        await agent.midi_adopt()
        await until(lambda: agent._midi["state"] == "listening")
        fbv = world.named[FBV_PORT]
        fbv.send(0xB0, 22, 127)      # the toe: muted
        await until(lambda: fake.writes())
        await settle(agent)
        assert fake.writes() == [("set_bypass", "main_send", True)]
        fbv.send(0xB0, 7, 2)         # the pedal to its heel while muted: its gain, not the bypass
        await until(lambda: gains(fake))
        await settle(agent)
        assert fake.bypassed["main_send"] is True, "the heel's silence did not unmute it"
        assert gains(fake) == [0.0]
        fbv.send(0xB0, 22, 0)        # the toe again: unmuted, the pedal's gain left where it is
        await until(lambda: len(fake.writes()) == 3)
        await settle(agent)
        assert fake.writes()[-1] == ("set_bypass", "main_send", False)
        assert gains(fake) == [0.0] and fake.info["main"]["mute"]["value"] == 0.0

    served(agent, test)


class LinkingWorld(PedalboardWorld):
    """The FBV's feed planned onto Sushi's input 0, as `plan_for` plans it
    (#210), and each open recording the link it was asked for."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.links: list = []

    def plan(self, described, root):
        plan = super().plan(described, root)
        if isinstance(plan, Off):
            return plan
        import dataclasses
        return dataclasses.replace(plan, feeds=tuple(dataclasses.replace(f, sushi_port=0) for f in plan.feeds))

    def open_port(self, name, link=None):
        self.links.append((name, link))
        port = super().open_port(name)
        port.linked, port.link_reason = True, f"linked by the agent: {name} to Sushi's MIDI input {link}"
        return port


def test_a_switch_brings_a_new_listener_that_links_the_pedal_to_the_new_sushi():
    """B steps to the next rig (here by restart: a new Sushi): the listener
    the switch starts after it opens the FBV again, asking for the link, and
    /health says it is linked."""
    world = LinkingWorld(raw=[])
    agent, fake, _, runner, rig = midi_agent(world)

    async def test(client):
        await agent.midi_adopt()
        await until(lambda: FBV_PORT in world.named and agent._midi["state"] == "listening")
        assert world.links == [(FBV_PORT, 0)]
        await until(lambda: agent._midi["feeds"][0].get("linked_to_sushi") is True)
        press(world.named[FBV_PORT], 21)  # b: the next rig
        await until(lambda: len(runner.calls) == 2)
        await settle(agent)
        await until(lambda: agent._midi["state"] == "listening" and agent._midi["rig"] == "lead")
        assert world.links == [(FBV_PORT, 0), (FBV_PORT, 0)], "linked again after the switch"
        await until(lambda: agent._midi["feeds"][0].get("linked_to_sushi") is True)
        feed = (await health(client))["feeds"][0]
        assert feed["linked_to_sushi"] is True and "Sushi's MIDI input 0" in feed["link"], feed

    served(agent, test)
