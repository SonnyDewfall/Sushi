"""A channel of another rig loaded onto a channel of the running rig
through the agent (#208): POST /rig/channels/<channel>/load {"from": {"rig",
"channel"}}.

Against real files in a temporary checkout: the frozen rigs
(rig_fixtures.frozen_checkout), their machine file, and the real compiler
(`rig.compile_named_rig`, what `up --rig` runs). The running rig is
`test_agent.RigLive`, a fake Sushi loaded from that compile, which the
agent's real live switch (`_switch_held`, #112) drives, so "the rig plays
it" is a reading of the fake after the load, not a word in an answer.

NOT shown: any of it against a live Sushi.
"""

from __future__ import annotations

import asyncio
import copy
import subprocess

import pytest
import yaml

pytest.importorskip("aiohttp")
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from sushi_rig import midi_listener, rigfile  # noqa: E402
from sushi_rig.agent import Agent, AgentError, Switching, channel_load_request  # noqa: E402
from sushi_rig.rig import compile_named_rig  # noqa: E402

from rig_fixtures import FROZEN_MACHINE, frozen_checkout  # noqa: E402
from test_agent import FakeRunner, RigLive  # noqa: E402

MACHINE = f"machines/{FROZEN_MACHINE.name}"
GIT = ["-c", "user.name=load test", "-c", "user.email=load@test.invalid", "-c", "commit.gpgsign=false"]


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
    return subprocess.run(["git", "-C", str(root), *GIT, *args], check=True, capture_output=True,
                          text=True).stdout


@pytest.fixture
def root(tmp_path):
    checkout = frozen_checkout(tmp_path / "checkout")
    git(checkout, "init", "-q")
    git(checkout, "add", "config", "machines")
    git(checkout, "commit", "-q", "-m", "the frozen rigs")
    return checkout


class World:
    """The state file and the compiled configs of rig `rig` up headless
    from its file, compiled for real; with no amp unless `amp`."""

    def __init__(self, root, rig="guitar_vocal", amp=False):
        self.root = root
        self.machine = root / MACHINE
        self.state = {
            "supervisor_pid": 10, "pgid": 10, "mode": "headless", "config_name": rig,
            "config": f"/run/rigs/{rig}.json", "rig": f"config/rigs/{rig}.yaml",
            "started_at": "2026-11-06T10:00:00+00:00", "amp_requested": amp,
            "amp_model": None, "amp_model_requested": None, "children": {"sushi": 11},
            "machine": str(self.machine),
        }
        compiled = compile_named_rig(rig, root, self.machine)
        if amp:
            self.state["amp_model"] = compiled["_amp"]["model"]
        elif "_amp" in compiled:
            # A run with no amp: the switch then compares no amp either side.
            pass
        self.configs = {self.state["config"]: compiled}

    def write_config(self, name, compiled):
        path = f"/run/rigs/{name}.json"
        self.configs[path] = copy.deepcopy(compiled)
        return path

    def write_state(self, state):
        self.state = copy.deepcopy(state)

    def described(self):
        return {"verdict": "running", "running": True, "config_name": self.state["config_name"],
                "mode": self.state["mode"], "rig": self.state["rig"], "config": self.state["config"],
                "children": {"sushi": {"pid": 11, "alive": True}},
                "message": f"{self.state['config_name']} (headless mode), up 2m"}

    def switching(self):
        return Switching(read_state=lambda: copy.deepcopy(self.state), write_state=self.write_state,
                         read_config=lambda path: copy.deepcopy(self.configs[path]),
                         write_config=self.write_config)


def make_agent(root, rig="guitar_vocal", amp=False):
    world = World(root, rig, amp)
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


def rig_file(root, name):
    return root / "config" / "rigs" / f"{name}.yaml"


def load(agent, channel, rig, source, **extra):
    return call(agent, "POST", f"/rig/channels/{channel}/load", json={"from": {"rig": rig, "channel": source}, **extra})


def plugins_of(fake, track):
    return [n for n in fake.chains[track] if not n.endswith("_to_stereo") and not n.endswith("_send")]


def with_midi(root, rig, controls):
    """Rig `rig` of the checkout with a MIDI map for the frozen machine
    file's controller, written as the tool writes a rig file."""
    path = rig_file(root, rig)
    rigfile.write_rig(path, {**yaml.safe_load(path.read_text()),
                             "midi": {"controller": "mpd218", "controls": controls}})


def another(root, name, rig="guitar_vocal", change=None):
    """A copy of rig `rig` as config/rigs/<name>.yaml, `change(raw)` made to
    it first: another rig to load a channel from."""
    raw = yaml.safe_load(rig_file(root, rig).read_text())
    raw["name"] = name
    if change:
        change(raw)
    rigfile.write_rig(rig_file(root, name), raw)


# --------------------------------------------------------------------------- #
# The request's shape
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("body", [None, [], {"from": "accustomed"}, {"from": {"rig": "a"}},
                                  {"from": {"rig": "a", "channel": "b", "x": 1}}, {"from": {"rig": "", "channel": "b"}},
                                  {"from": {"rig": "a", "channel": 3}}, {"from": {"rig": "a", "channel": "b"}, "x": 1},
                                  {"from": {"rig": "a", "channel": "b"}, "discard": "yes"}, {"preset": "crunch"},
                                  {"from": {"rig": "a", "channel": "b"}, "rig": ""},
                                  {"from": {"rig": "a", "channel": "b"}, "rig": 3}])
def test_a_load_names_a_rig_and_a_channel_and_nothing_else(body):
    with pytest.raises(AgentError):
        channel_load_request(body)
    assert channel_load_request({"from": {"rig": "a", "channel": "b"}}) == (
        {"rig": "a", "channel": "b"}, False, False, None)
    assert channel_load_request({"from": {"rig": "a", "channel": "b"}, "discard": True, "drop_midi": True,
                                 "rig": "r"}) == ({"rig": "a", "channel": "b"}, True, True, "r")


def test_the_preset_routes_are_gone(root):
    agent, world, fake, _ = make_agent(root)
    assert call(agent, "GET", "/presets")[0] == 404
    assert call(agent, "POST", "/rig/channels/guitar/save-as", json={"name": "crunch"})[0] in (404, 405)
    status, body = call(agent, "POST", "/rig/channels/guitar/load", json={"preset": "crunch"})
    assert status == 400, body


# --------------------------------------------------------------------------- #
# Loading a channel onto a channel of the running rig
# --------------------------------------------------------------------------- #

def test_a_channel_of_the_running_rig_loads_onto_another_live_with_names_of_its_own(root):
    """The guitar's chain on the vocal's input: each processor named for its
    channel, by the live switch, no restart."""
    agent, world, fake, runner = make_agent(root)
    before = rig_file(root, "guitar_vocal").read_text()
    status, body = load(agent, "vocal", "guitar_vocal", "guitar")
    assert status == 200, body
    assert body["how"] == "live", body
    assert runner.calls == [], "nothing was restarted"
    expected = ["vocal_compressor", "vocal_overdrive", "vocal_eq", "vocal_chorus", "vocal_delay", "vocal_room"]
    assert plugins_of(fake, "vocal") == expected, "the rig plays it: a reading of the fake"
    assert plugins_of(fake, "guitar") == ["guitar_compressor", "guitar_overdrive", "guitar_eq", "guitar_chorus",
                                          "guitar_delay", "guitar_room"], "the other channel is as it was"
    assert "vocal_compressor_to_stereo" in fake.chains["vocal"], "the mono compressor has its copy"
    written = yaml.safe_load(rig_file(root, "guitar_vocal").read_text())
    assert [p["name"] for p in written["channels"][1]["chain"]] == expected
    assert written["channels"][1]["input"] == "vocal-in" and written["channels"][1]["width"] == "mono"
    assert written["channels"][0] == yaml.safe_load(before)["channels"][0]
    text = rig_file(root, "guitar_vocal").read_text()
    assert text.startswith("# Guitar and vocals as a v2 rig (#30)"), "the file's comments are kept"
    assert "_to_stereo" not in text
    assert body["names"]["compressor"] == "vocal_compressor"
    assert body["from"] == {"rig": "guitar_vocal", "channel": "guitar", "file": "config/rigs/guitar_vocal.yaml",
                            "plugins": 6, "git": "unchanged"}
    entry = body["entry"]
    assert entry["op"] == "rig" and entry["args"]["load"] == {"rig": "guitar_vocal", "channel": "guitar", "onto": "vocal"}
    # The load is a baseline: nothing unsaved after it, and undo stops at it.
    status, undo = call(agent, "POST", "/live/undo")
    assert status == 409, undo
    assert world.state["rig"] == "config/rigs/guitar_vocal.yaml"


def test_a_load_is_undone_by_the_one_undo_the_file_back_and_the_rig_switched_into_it(root):
    """#209: POST /undo after a load puts the rig file's bytes back and
    switches the running rig into it, as the undo of a revert does, and the
    pedals' listener follows the rig after (#235's review)."""
    agent, world, fake, runner = make_agent(root)
    path = rig_file(root, "guitar_vocal")
    before = path.read_bytes()
    vocal = plugins_of(fake, "vocal")
    status, body = load(agent, "vocal", "accustomed", "board")
    assert status == 200 and body["how"] == "live", body
    assert path.read_bytes() != before and plugins_of(fake, "vocal") != vocal
    status, state = call(agent, "GET", "/undo")
    assert status == 200, state
    assert state["next"]["label"] == "load accustomed / board" and state["next"]["act"] == "load", state
    syncs, real = [], agent._midi_sync

    async def counting():
        syncs.append(1)
        await real()

    agent._midi_sync = counting
    status, body = call(agent, "POST", "/undo", json={"seq": state["next"]["seq"], "restart": False})
    assert status == 200, body
    assert body["said"] == "Undone: load accustomed / board" and body["how"] == "switched", body
    assert path.read_bytes() == before, "the file as it was before the load, byte for byte"
    assert plugins_of(fake, "vocal") == vocal, "the rig plays it again: a reading of the fake"
    assert runner.calls == [], "nothing was restarted"
    assert syncs, "the switch stopped the pedals' listener: it follows the rig after"


def test_a_loads_undo_waits_for_unsaved_work_as_the_load_did(root):
    """#235's review: a live change that could not be undone still plays, so
    the load's Undo, which switches, is held `unsaved` until `discard`."""
    agent, world, fake, runner = make_agent(root)
    path = rig_file(root, "guitar_vocal")
    before = path.read_bytes()
    assert load(agent, "vocal", "accustomed", "board")[0] == 200
    loaded = path.read_bytes()
    turned = next(iter(fake.values["guitar_overdrive"]))
    status, body = call(agent, "POST", "/live/parameter",
                        json={"processor": "guitar_overdrive", "parameter": turned, "value": 0.25})
    assert status == 200, body
    fake.values["guitar_overdrive"][turned] = 0.75  # moved since from the panel: not undoable
    status, body = call(agent, "POST", "/undo", json={})
    assert status == 409 and body["code"] == "cant_undo", body
    status, body = call(agent, "POST", "/undo", json={})
    assert status == 409 and body["code"] == "unsaved", body
    assert path.read_bytes() == loaded, "nothing put back"
    status, body = call(agent, "POST", "/undo", json={"discard": True})
    assert status == 200 and body["how"] == "switched", body
    assert path.read_bytes() == before


def test_a_channel_of_another_rig_loads_live_with_its_settings_and_bypass(root):
    """accustomed's board onto guitar_vocal's vocal: its chain as accustomed's
    file has it, settings, bypass and all, under the vocal's names."""
    agent, world, fake, runner = make_agent(root)
    source = yaml.safe_load(rig_file(root, "accustomed").read_text())["channels"][0]
    status, body = load(agent, "vocal", "accustomed", "board")
    assert status == 200 and body["how"] == "live", body
    assert runner.calls == []
    written = yaml.safe_load(rig_file(root, "guitar_vocal").read_text())["channels"][1]
    assert [p["name"] for p in written["chain"]] == [f"vocal_{p['name']}" for p in source["chain"]]
    for copied, original in zip(written["chain"], source["chain"]):
        assert {k: v for k, v in copied.items() if k != "name"} == {k: v for k, v in original.items() if k != "name"}
    assert plugins_of(fake, "vocal") == [p["name"] for p in written["chain"]]
    assert fake.bypassed["vocal_overdrive"] is True and fake.bypassed["vocal_compressor"] is False
    assert (written["input"], written["width"]) == ("vocal-in", "mono"), "the vocal's own input and width"
    assert body["from"]["file"] == "config/rigs/accustomed.yaml" and body["from"]["plugins"] == 9
    assert rig_file(root, "accustomed").read_text() == git(root, "show", "HEAD:config/rigs/accustomed.yaml"), \
        "the source rig is not touched"


def test_a_channel_whose_settings_alone_differ_sets_them_live(root):
    """A copy of guitar_vocal with the guitar's overdrive turned, its EQ
    bypassed and its level down: loaded onto the guitar, the same plugins,
    so no structure changes, only what differs is set."""
    agent, world, fake, runner = make_agent(root)
    turned = next(iter(fake.values["guitar_overdrive"]))

    def change(raw):
        guitar = raw["channels"][0]
        guitar["chain"][1]["parameters"] = {turned: 0.125}
        guitar["chain"][2]["bypassed"] = True
        guitar["level"] = -4.0

    another(root, "gv-turned", change=change)
    status, body = load(agent, "guitar", "gv-turned", "guitar")
    assert status == 200 and body["how"] == "live", body
    assert [w[0] for w in fake.writes() if w[0] in ("add", "remove", "move")] == [], "no structure changed"
    assert fake.values["guitar_overdrive"][turned] == pytest.approx(0.125)
    assert fake.bypassed["guitar_eq"] is True
    assert fake.values["guitar_send"]["gain"] == pytest.approx(rigfile.level_to_gain(-4.0), abs=1e-6)
    written = yaml.safe_load(rig_file(root, "guitar_vocal").read_text())["channels"][0]
    assert written["level"] == -4.0 and written["chain"][2]["bypassed"] is True


def test_a_load_a_knob_mapping_follows_is_a_restart_that_says_why(root):
    """electric_board names its plugins without their channel's prefix, so
    accustomed's board loaded onto it renames every one: a pedal on the
    overdrive follows it, and since a knob's mapping is Sushi's, loaded only
    with the config, the load is a restart, which it says."""
    with_midi(root, "electric_board", [{"control": "pad1", "bypass": "board/overdrive"},
                                       {"control": "knob1", "parameter": "board/overdrive/Drive"}])
    agent, world, fake, runner = make_agent(root, rig="electric_board")
    status, body = load(agent, "board", "accustomed", "board")
    assert status == 200, body
    assert body["followed"] == [{"control": "pad1", "from": "board/overdrive", "to": "board/board_overdrive"},
                                {"control": "knob1", "from": "board/overdrive/Drive", "to": "board/board_overdrive/Drive"}]
    written = yaml.safe_load(rig_file(root, "electric_board").read_text())
    assert written["midi"]["controls"] == [{"control": "pad1", "bypass": "board/board_overdrive"},
                                           {"control": "knob1", "parameter": "board/board_overdrive/Drive"}]
    assert all(p["name"].startswith("board_") for p in written["channels"][0]["chain"])
    assert body["how"] == "restart" and "midi" in [w["code"] for w in body["why"]], body
    assert runner.calls and runner.calls[0][0] == "down"


def test_the_source_rigs_midi_map_does_not_come_with_it(root):
    """accustomed maps a pad and a knob onto its board; loaded onto the
    vocal, none of that comes into guitar_vocal, whose own map (the mute
    pad) is all it keeps."""
    with_midi(root, "accustomed", [{"control": "pad1", "bypass": "board/overdrive"},
                                   {"control": "knob1", "parameter": "board/compressor/RATIO"},
                                   {"control": "pad3", "mute": "board"}])
    agent, world, fake, runner = make_agent(root)
    status, body = load(agent, "vocal", "accustomed", "board")
    assert status == 200, body
    assert "midi" not in yaml.safe_load(rig_file(root, "guitar_vocal").read_text()), "a rig with no map gains none"
    with_midi(root, "guitar_vocal", [{"control": "pad2", "mute": "vocal"}])
    agent, world, fake, runner = make_agent(root)
    status, body = load(agent, "guitar", "accustomed", "board")
    assert status == 200, body
    written = yaml.safe_load(rig_file(root, "guitar_vocal").read_text())
    assert written["midi"]["controls"] == [{"control": "pad2", "mute": "vocal"}]
    assert (body["followed"], body["dropped"]) == ([], [])


def test_a_load_with_unsaved_work_is_refused_and_allowed_with_discard(root):
    """Discarded means gone, on a channel the load leaves alone too (review
    of #162): the knob turned on the vocal is put back to what the file
    gives it, by the switch's own steps."""
    agent, world, fake, _ = make_agent(root)
    before = rig_file(root, "guitar_vocal").read_bytes()
    was = fake.values["vocal_eq"]["High"]
    for body in ({"processor": "vocal_eq", "parameter": "High", "value": 0.987},
                 {"processor": "guitar_send", "parameter": "gain", "value": rigfile.level_to_gain(-3)}):
        status, said = call(agent, "POST", "/live/parameter", json=body)
        assert status == 200, said
    status, body = call(agent, "POST", "/live/bypass", json={"processor": "vocal_deess", "bypassed": True})
    assert status == 200, body
    status, body = load(agent, "guitar", "guitar_vocal", "vocal")
    assert status == 409 and body["code"] == "unsaved" and "vocal_deess" in body["reason"], body
    assert "loading a channel writes the rig file" in body["reason"]
    assert rig_file(root, "guitar_vocal").read_bytes() == before, "nothing was written"
    status, body = load(agent, "guitar", "guitar_vocal", "vocal", discard=True)
    assert status == 200 and body["discarded"] is True and body["how"] == "live", body
    assert plugins_of(fake, "guitar") == ["guitar_dynamics", "guitar_deess", "guitar_eq", "guitar_reverb",
                                          "guitar_limiter"]
    assert fake.values["vocal_eq"]["High"] == pytest.approx(was), "the discarded knob is put back"
    assert fake.bypassed["vocal_deess"] is False, "and the discarded bypass"
    restored = {(s["processor"], s.get("parameter", "bypass")) for s in body["discard"]["restored"]}
    assert restored == {("vocal_eq", "High")}
    assert body["discard"]["changes"] == 3 and "put back each value" in body["discard"]["note"]


def test_a_revert_with_discard_puts_back_what_was_dropped_on_a_channel_it_leaves_alone(root):
    """The committed file states no value for the vocal's EQ, so the diff
    alone would leave the knob where the discarded work put it."""
    agent, world, fake, _ = make_agent(root)
    was = fake.values["vocal_eq"]["High"]
    call(agent, "POST", "/live/parameter", json={"processor": "vocal_eq", "parameter": "High", "value": 0.987})
    sha = git(root, "rev-parse", "HEAD").strip()
    status, body = call(agent, "POST", "/rigs/guitar_vocal/revert", json={"save": f"commit:{sha}", "discard": True})
    assert status == 200 and body["how"] == "live", body
    assert fake.values["vocal_eq"]["High"] == pytest.approx(was)
    assert body["discard"]["restored"][0]["processor"] == "vocal_eq"


def test_a_discarded_structural_edit_is_a_restart_that_says_so(root):
    agent, world, fake, runner = make_agent(root)
    fake.chains["guitar"].remove("guitar_delay")  # removed live, and logged as the route logs it
    agent._record("remove", {"processor": "guitar_delay"}, {"track": "guitar", "chain": []})
    status, body = load(agent, "vocal", "accustomed", "board", discard=True)
    assert status == 200, body
    assert body["how"] == "restart" and "drifted" in [w["code"] for w in body["why"]], body
    assert body["discard"]["structural"] is True and "restarted from its file" in body["discard"]["note"]
    assert runner.calls and runner.calls[0][0] == "down"


def test_a_discarded_value_sushi_did_not_report_is_a_restart():
    from sushi_rig.agent import discard_plan

    plan = rigfile.SwitchPlan(())
    entries = [{"op": "parameter", "args": {"processor": "eq", "parameter": "High", "value": 0.9}, "previous": None}]
    upcoming = {"tracks": [{"name": "a", "plugins": [{"name": "eq"}]}], "initial_state": []}
    out = discard_plan(plan, entries, upcoming, {"processors": {"eq": {"parameters": {"High": 0.9}}}})
    assert isinstance(out, rigfile.NeedsRestart) and out.reasons[0].code == "discarded"
    stated = {**upcoming, "initial_state": [{"processor": "eq", "parameters": {"High": 0.25}}]}
    out = discard_plan(plan, entries, stated, {"processors": {"eq": {"parameters": {"High": 0.9}}}})
    assert [s.args for s in out.steps] == [
        {"processor": "eq", "parameter": "High", "value": 0.25, "restores": "discarded"}]


def test_a_processor_the_plan_replaces_is_not_given_the_old_instances_value():
    """Re-review of #162: the next rig has guitar_delay but states no
    `time`, so the switch removes it and adds it fresh, at the plugin's
    default; the value from before the discarded turn was the old
    instance's, and is not set on the new one."""
    from sushi_rig.agent import discard_plan

    step = rigfile.SwitchStep
    plan = rigfile.SwitchPlan((
        step("remove", {"processor": "guitar_delay"}),
        step("add", {"track": "guitar", "name": "guitar_delay", "uri": "urn:x:delay", "before": None}),
        step("bypass", {"processor": "guitar_delay", "bypassed": True}),
        step("bypass", {"processor": "guitar_delay", "bypassed": False}),
    ))
    discarded = [{"op": "parameter", "args": {"processor": "guitar_delay", "parameter": "time", "value": 0.8},
                  "previous": 0.5},
                 {"op": "bypass", "args": {"processor": "guitar_delay", "bypassed": True}, "previous": False}]
    upcoming = {"tracks": [{"name": "guitar", "plugins": [{"name": "guitar_delay"}]}], "initial_state": []}
    reading = {"processors": {"guitar_delay": {"parameters": {"time": 0.8}, "bypassed": True}}}
    out = discard_plan(plan, discarded, upcoming, reading)
    assert out.steps == plan.steps, "nothing restored onto the fresh instance"


def test_restored_values_come_before_the_plans_bypass_offs():
    """A pedal the plan brings in comes in with its parameters set (diff's
    step 7): the restored values go in front of the closing bypass-offs."""
    from sushi_rig.agent import discard_plan

    step = rigfile.SwitchStep
    plan = rigfile.SwitchPlan((
        step("bypass", {"processor": "drive", "bypassed": True}),
        step("parameter", {"processor": "room", "parameter": "Blend", "value": 0.2}),
        step("bypass", {"processor": "drive", "bypassed": False}),
        step("bypass", {"processor": "room", "bypassed": False}),
    ))
    discarded = [{"op": "parameter", "args": {"processor": "drive", "parameter": "Drive", "value": 0.9},
                  "previous": 0.3}]
    upcoming = {"tracks": [{"name": "guitar", "plugins": [{"name": "drive"}, {"name": "room"}]}], "initial_state": []}
    reading = {"processors": {"drive": {"parameters": {"Drive": 0.9}}, "room": {"parameters": {"Blend": 0.5}}}}
    out = discard_plan(plan, discarded, upcoming, reading)
    ops = [(s.op, s.args.get("processor"), s.args.get("bypassed", s.args.get("value"))) for s in out.steps]
    assert ops == [("bypass", "drive", True), ("parameter", "room", 0.2), ("parameter", "drive", 0.3),
                   ("bypass", "drive", False), ("bypass", "room", False)]


def test_the_file_as_it_was_is_kept_for_revert(root):
    agent, world, fake, _ = make_agent(root)
    before = rig_file(root, "guitar_vocal").read_bytes()
    status, body = load(agent, "vocal", "accustomed", "board")
    assert status == 200, body
    kept = body["kept_before"]
    assert kept["how"] == "before loading accustomed / board onto vocal"
    status, listed = call(agent, "GET", "/rigs/guitar_vocal/saves")
    assert any(p["id"] == kept["id"] and p["how"] == kept["how"] for p in listed["points"]), listed
    status, body = call(agent, "POST", "/rigs/guitar_vocal/revert", json={"save": kept["id"]})
    assert status == 200 and body["how"] == "live", body
    assert rig_file(root, "guitar_vocal").read_bytes() == before
    assert plugins_of(fake, "vocal") == ["vocal_dynamics", "vocal_deess", "vocal_eq", "vocal_reverb",
                                         "vocal_limiter"], "the vocal's chain is back"


def test_a_file_a_save_keeps_already_is_not_kept_twice(root):
    agent, world, fake, _ = make_agent(root, rig="electric_board")
    status, save = call(agent, "POST", "/rig/save")
    assert status == 200, save
    status, body = load(agent, "board", "accustomed", "board")
    assert status == 200, body
    assert body["kept_before"] == {"id": save["snapshot"]["id"], "how": "kept by that save already"}


def test_a_load_meant_for_another_rig_than_the_running_one_writes_nothing(root):
    """Review of PR #234: the page names the rig it drew the channel in, so
    a Program Change between its render and the click cannot land the load
    on another rig's channel of the same name."""
    agent, world, fake, _ = make_agent(root)
    before = {name: rig_file(root, name).read_bytes() for name in ("guitar_vocal", "accustomed")}
    status, body = call(agent, "POST", "/rig/channels/vocal/load",
                        json={"from": {"rig": "accustomed", "channel": "board"}, "rig": "electric-chorus"})
    assert status == 409 and body["code"] == "changed", body
    assert body["reason"].startswith("the running rig is guitar_vocal, not electric-chorus, so nothing was loaded")
    assert {name: rig_file(root, name).read_bytes() for name in before} == before and fake.writes() == []
    assert not agent._lock.locked() and agent.busy is None
    status, body = call(agent, "POST", "/rig/channels/vocal/load",
                        json={"from": {"rig": "accustomed", "channel": "board"}, "rig": "guitar_vocal"})
    assert status == 200 and body["rig"] == "guitar_vocal", body


def test_a_rig_or_a_channel_that_is_not_there_is_refused_before_anything_is_written(root):
    agent, world, fake, _ = make_agent(root)
    before = rig_file(root, "guitar_vocal").read_bytes()
    status, body = load(agent, "guitar", "nothing", "board")
    assert status == 409 and body["code"] == "no_source", body
    assert body["reason"] == "there is no rig called 'nothing' in config/rigs/"
    # A rig is one of config/rigs/' names, never a path: each of these names a
    # file a path would reach, and none is read.
    outside = root / "config" / "elsewhere.yaml"
    outside.write_text(rig_file(root, "accustomed").read_text())
    for rig in ("../rigs/guitar_vocal", "../elsewhere", str(outside.with_suffix("")), "accustomed.yaml",
                "accustomed/../accustomed", "./accustomed", "..", "/", "accustomed\u0000"):
        status, body = load(agent, "guitar", rig, "board")
        assert status == 409 and body["code"] == "no_source", (rig, body)
    status, body = load(agent, "guitar", "accustomed", "lead")
    assert status == 409 and body["code"] == "no_source", body
    assert body["reason"] == "accustomed has no channel called lead (it has: board)"
    status, body = load(agent, "bass", "accustomed", "board")
    assert status == 409 and body["code"] == "no_channel" and "guitar, vocal" in body["reason"], body
    rig_file(root, "broken").write_text("kind: rig\nchannels: [\n")
    status, body = load(agent, "guitar", "broken", "board")
    assert status == 409 and body["code"] == "no_source" and "broken.yaml could not be read" in body["reason"], body
    assert rig_file(root, "guitar_vocal").read_bytes() == before
    assert fake.writes() == []


def test_a_chain_the_compiler_refuses_is_refused_in_its_words(root):
    another(root, "unknown", change=lambda raw: raw["channels"][1]["chain"][0].update(
        uri="http://example.invalid/not-in-the-table"))
    agent, world, fake, _ = make_agent(root)
    before = rig_file(root, "guitar_vocal").read_bytes()
    status, body = load(agent, "guitar", "unknown", "vocal")
    assert status == 409 and body["code"] == "rig_problem" and "plugin-ports.txt" in body["reason"], body
    assert rig_file(root, "guitar_vocal").read_bytes() == before and fake.writes() == []


def test_a_name_another_channel_has_is_the_compilers_refusal(root):
    raw = yaml.safe_load(rig_file(root, "guitar_vocal").read_text())
    raw["name"] = "clash"
    raw["channels"][0]["chain"][4]["name"] = "vocal_room"   # the guitar's delay, named as a vocal's room would be
    rigfile.write_rig(rig_file(root, "clash"), raw)
    agent, world, fake, _ = make_agent(root, rig="clash")
    before = rig_file(root, "clash").read_bytes()
    status, body = load(agent, "vocal", "guitar_vocal", "guitar")
    assert status == 409 and body["code"] == "rig_problem" and "vocal_room" in body["reason"], body
    assert rig_file(root, "clash").read_bytes() == before


def test_a_load_that_would_orphan_a_mapping_is_refused_or_drops_it_saying_what_it_was(root):
    with_midi(root, "guitar_vocal", [{"control": "pad1", "bypass": "guitar/guitar_overdrive"},
                                     {"control": "pad2", "mute": "vocal"}])
    agent, world, fake, runner = make_agent(root)
    before = rig_file(root, "guitar_vocal").read_bytes()
    status, body = load(agent, "guitar", "guitar_vocal", "vocal")
    assert status == 409 and body["code"] == "midi", body
    assert "pad1 (bypass guitar/guitar_overdrive)" in body["reason"]
    assert body["mappings"] == [{"control": "pad1", "binding": "bypass", "target": "guitar/guitar_overdrive",
                                 "why": "the loaded chain has no plugin like guitar_overdrive "
                                        "(http://guitarix.sourceforge.net/plugins/gxts9#ts9sim): it holds "
                                        "guitar_dynamics, guitar_deess, guitar_eq, guitar_reverb, guitar_limiter"}]
    assert rig_file(root, "guitar_vocal").read_bytes() == before
    status, body = load(agent, "guitar", "guitar_vocal", "vocal", drop_midi=True)
    assert status == 200, body
    assert [d["control"] for d in body["dropped"]] == ["pad1"]
    written = yaml.safe_load(rig_file(root, "guitar_vocal").read_text())
    assert written["midi"]["controls"] == [{"control": "pad2", "mute": "vocal"}]
    # A bypass binding is the agent's to serve, not Sushi's: dropping one is
    # no reason to restart (rigfile.diff compares Sushi's cc_mappings).
    assert body["how"] == "live", body


def test_a_guitar_channel_heard_through_the_amp_says_where_it_lands_without_one(root):
    agent, world, fake, _ = make_agent(root)
    status, body = load(agent, "vocal", "accustomed", "board")
    assert status == 200, body
    assert body["amp"] == {"in_front": False, "heard_with": "amp/models/Neve 2.nam",
                           "note": "vocal has no amp: accustomed / board plays through one (Neve 2), and this rig "
                                   "has no amp"}, body


def test_a_channel_of_another_width_leaves_the_target_its_own(root):
    another(root, "wide", change=lambda raw: raw["channels"][1].update(width="stereo"))
    agent, world, fake, _ = make_agent(root)
    status, body = load(agent, "guitar", "wide", "vocal")
    assert status == 200, body
    assert body["width"] == "wide / vocal is stereo; guitar stays mono, as its input (guitar-in) is"
    assert yaml.safe_load(rig_file(root, "guitar_vocal").read_text())["channels"][0]["width"] == "mono"


def test_a_load_refuses_a_rig_that_is_not_one_of_channels_and_a_busy_agent(root):
    agent, world, fake, _ = make_agent(root)
    world.state["mode"] = "panel"
    status, body = load(agent, "guitar", "guitar_vocal", "vocal")
    assert status == 409 and body["code"] == "panel", body
    world.state["mode"] = "headless"

    async def held():
        async with agent._lock:
            agent.busy = "switch"
            async with TestClient(TestServer(agent.app())) as client:
                response = await client.post("/rig/channels/guitar/load",
                                             json={"from": {"rig": "guitar_vocal", "channel": "vocal"}})
                return response.status, await response.json()

    status, body = asyncio.run(held())
    assert status == 409 and body["code"] == "busy", body


def test_a_load_whose_plugins_are_not_all_there_writes_nothing(root, tmp_path, monkeypatch):
    """#188's check (review of PR #198): a load that needs a plugin plugins/
    lacks is refused before the write, with the file as it was and nothing
    played."""
    import shutil

    from rig_fixtures import declaring, fake_lv2_system, stand_in_plugins

    gxts9 = "http://guitarix.sourceforge.net/plugins/gxts9#ts9sim"
    declaring(fake_lv2_system(tmp_path, monkeypatch), "gxts9", gxts9)
    agent, world, fake, runner = make_agent(root)
    shutil.rmtree(root / "plugins")
    stand_in_plugins(root / "plugins", leave_out=(gxts9,))
    before = rig_file(root, "guitar_vocal").read_bytes()
    status, body = load(agent, "vocal", "accustomed", "board")
    assert status == 409 and body["code"] == "plugins_missing", body
    assert body["reason"].startswith(
        "nothing was written: guitar_vocal was not given accustomed / board on vocal: 1 plugin(s)")
    assert gxts9 in body["reason"] and "then load it again: sushi-rig plugins add" in body["reason"]
    assert "written" not in body
    assert rig_file(root, "guitar_vocal").read_bytes() == before, "the file is untouched"
    assert fake.writes() == [] and runner.calls == []


# --------------------------------------------------------------------------- #
# The provenance line in the rig file
# --------------------------------------------------------------------------- #

def provenance_of(root, rig, channel):
    lines = rig_file(root, rig).read_text().splitlines()
    name = next(i for i, line in enumerate(lines) if line.strip() in (f"- name: {channel}", f"name: {channel}"))
    chain = next(i for i in range(name, len(lines)) if lines[i].strip().startswith("chain:"))
    return [line.strip() for line in lines[name:chain] if line.strip().startswith("#")]


def test_a_load_names_where_the_chain_came_from_above_it(root):
    agent, world, fake, _ = make_agent(root)
    status, body = load(agent, "vocal", "accustomed", "board")
    assert status == 200 and "provenance_note" not in body, body
    said = provenance_of(root, "guitar_vocal", "vocal")
    assert said[0].startswith("# Chain copied from accustomed / board (config/rigs/accustomed.yaml), 20"), said
    status, body = load(agent, "vocal", "guitar_vocal", "guitar")
    assert status == 200, body
    said = provenance_of(root, "guitar_vocal", "vocal")
    assert said[0].startswith("# Chain copied from guitar_vocal / guitar ") and not any("accustomed" in s for s in said), \
        "the next load replaces the line, never stacks it"
    assert provenance_of(root, "guitar_vocal", "guitar") == [], "the other channel says nothing"


def test_a_comment_directly_above_a_channels_chain_survives_a_load(root):
    """The comment is the channel's, not the chain's (review of #162)."""
    path = rig_file(root, "electric_board")
    text = path.read_text()
    assert "\n  chain:\n" in text
    path.write_text(text.replace("\n  chain:\n", "\n  # The board: keep this note.\n  chain:\n", 1))
    agent, world, fake, _ = make_agent(root, rig="electric_board")
    for _ in range(2):
        status, body = load(agent, "board", "accustomed", "board")
        assert status == 200, body
        said = provenance_of(root, "electric_board", "board")
        assert said[0] == "# The board: keep this note.", said
        assert sum(s.startswith("# Chain copied from accustomed / board ") for s in said) == 1, said


def test_a_channel_whose_names_keep_their_prefix_loads_with_every_name_its_own(root):
    """`eq` beside `guitar_eq` on the guitar: taking the prefixes off would
    make two names one, so none is taken off, and a load prefixes every
    one, so the two stay two (review of #162)."""
    another(root, "both", change=lambda raw: raw["channels"][0]["chain"][3].update(name="eq"))
    agent, world, fake, _ = make_agent(root)
    status, body = load(agent, "guitar", "both", "guitar")
    assert status == 200, body
    names = [p["name"] for p in yaml.safe_load(rig_file(root, "guitar_vocal").read_text())["channels"][0]["chain"]]
    assert names[2:4] == ["guitar_guitar_eq", "guitar_eq"] and len(set(names)) == len(names)
    assert names[0] == "guitar_guitar_compressor"


# --------------------------------------------------------------------------- #
# When the switch fails, and when the write is refused
# --------------------------------------------------------------------------- #

def test_a_load_whose_switch_fails_says_so_as_a_load_and_the_pedals_follow_what_runs(root):
    """The file is written and the rig would not play it: /health names the
    load, not a revert, and the pedals are planned from the compiled config
    the state file names, which is the rig still running (review of #162)."""
    from sushi_rig.rigfile import AGENT_BINDINGS
    from sushi_rig.supervisor import Outcome

    with_midi(root, "electric_board", [{"control": "pad1", "bypass": "board/overdrive"}])
    agent, world, fake, runner = make_agent(root, rig="electric_board")
    planned = []

    def plan(described, root):
        bindings = world.configs[described["config"]].get(AGENT_BINDINGS) or []
        planned.append([b.get("bypass") for b in bindings])
        return midi_listener.Off("recorded by the test")

    agent._midi_world = midi_listener.World(plan=plan)
    runner.outcome = Outcome(False, 1, "", "error: the test's restart fails\n")
    fake.drift = lambda rig: rig.chains["board"].remove("delay")
    status, body = load(agent, "board", "accustomed", "board")
    assert status == 409 and body["code"] == "not_switched" and body["written"] is True, body
    assert "midi_note" in body and "not the file just written" in body["midi_note"]
    assert planned and planned[-1] == ["board/overdrive"], planned
    assert "overdrive" in fake.chains["board"], "the processor the pedal names is the one Sushi has"
    status, health = call(agent, "GET", "/health")
    error = health["agent"]["last_error"]
    assert error and error["command"][0] == "load" and "revert" not in error["command"], error
    assert error["command"][1] == "accustomed / board onto electric_board's board"


def test_no_revert_point_is_kept_for_a_load_whose_write_is_refused(root, monkeypatch):
    from sushi_rig import agent as agent_module, saves

    agent, world, fake, _ = make_agent(root)
    before = rig_file(root, "guitar_vocal").read_bytes()

    def refuse(*args, **kwargs):
        raise rigfile.RigFileError(["refused by the test"])

    monkeypatch.setattr(agent_module.rigfile, "write_rig", refuse)
    status, body = load(agent, "guitar", "guitar_vocal", "vocal")
    assert status == 409 and body["code"] == "rig_problem" and "refused by the test" in body["reason"], body
    assert rig_file(root, "guitar_vocal").read_bytes() == before
    assert saves.snapshots(root, "guitar_vocal")[0] == [], "no point for a load that never happened"
    assert fake.writes() == []


# --------------------------------------------------------------------------- #
# Writes refuse another site's page (#160's guard)
# --------------------------------------------------------------------------- #

def guarded(agent, path, body, headers, own_origin=False):
    async def go():
        async with TestClient(TestServer(agent.app())) as client:
            sent = dict(headers)
            if own_origin:
                sent["Origin"] = f"http://127.0.0.1:{client.server.port}"
            response = await client.post(path, json=body, headers=sent)
            return response.status, await response.json()

    return asyncio.run(go())


@pytest.mark.parametrize("headers", [{"Sec-Fetch-Site": "cross-site"}, {"Origin": "https://example.invalid"},
                                     {"Origin": "http://127.0.0.1:1"}, {"Origin": "null"}])
def test_a_load_from_another_site_is_refused_with_nothing_written(root, headers):
    agent, world, fake, _ = make_agent(root)
    before = rig_file(root, "guitar_vocal").read_bytes()
    status, said = guarded(agent, "/rig/channels/guitar/load", {"from": {"rig": "accustomed", "channel": "board"}},
                           headers)
    assert status == 403 and said["reason"].startswith("refused"), said
    assert rig_file(root, "guitar_vocal").read_bytes() == before and fake.writes() == []


@pytest.mark.parametrize("headers, own", [({}, False), ({"Sec-Fetch-Site": "same-origin"}, True)])
def test_curl_and_the_agents_own_page_may_load(root, headers, own):
    agent, world, fake, _ = make_agent(root)
    status, body = guarded(agent, "/rig/channels/guitar/load", {"from": {"rig": "accustomed", "channel": "board"}},
                           headers, own)
    assert status == 200, body


# --------------------------------------------------------------------------- #
# "Start from" copies as a load does (review of PR #234)
# --------------------------------------------------------------------------- #

def test_a_new_rig_of_two_channels_from_one_channel_compiles_with_names_of_their_own(root):
    agent, world, fake, _ = make_agent(root)
    status, body = call(agent, "POST", "/rigs/new", json={"name": "two-guitars", "channels": [
        {"name": "left", "input": "guitar-in", "width": "mono", "from": {"rig": "guitar_vocal", "channel": "guitar"}},
        {"name": "right", "input": "vocal-in", "width": "mono",
         "from": {"rig": "guitar_vocal", "channel": "guitar"}}]})
    assert status == 200, body
    raw = yaml.safe_load(rig_file(root, "two-guitars").read_text())
    left, right = raw["channels"]
    gv = yaml.safe_load(rig_file(root, "guitar_vocal").read_text())["channels"][0]["chain"]
    assert [p["name"] for p in left["chain"]] == [p["name"].replace("guitar_", "left_", 1) for p in gv]
    assert [p["name"] for p in right["chain"]] == [p["name"].replace("guitar_", "right_", 1) for p in gv]
    assert [{k: v for k, v in p.items() if k != "name"} for p in left["chain"]] == [
        {k: v for k, v in p.items() if k != "name"} for p in gv], "settings and all, as written"
    compile_named_rig("two-guitars", root, root / MACHINE)
    said = {c["name"]: c["from"] for c in body["channels"]}
    assert said["left"]["rig"] == "guitar_vocal" and said["left"]["plugins"] == len(gv)


def test_a_channel_added_from_its_own_rigs_channel_has_names_of_its_own(root):
    agent, world, fake, _ = make_agent(root)
    status, body = call(agent, "POST", "/rigs/guitar_vocal/channels/add", json={
        "name": "spare", "input": "vocal-in", "width": "mono", "from": {"rig": "guitar_vocal", "channel": "guitar"}})
    assert status == 200, body
    raw = yaml.safe_load(rig_file(root, "guitar_vocal").read_text())
    guitar, _, spare = raw["channels"]
    assert [p["name"] for p in spare["chain"]] == [p["name"].replace("guitar_", "spare_", 1) for p in guitar["chain"]]
    assert body["change"]["from"]["channel"] == "guitar"
    assert body["change"]["from"]["git"] == "unchanged", "the source as copied, not as the add wrote it"
    compile_named_rig("guitar_vocal", root, root / MACHINE)


def test_a_start_from_whose_names_would_be_too_long_writes_nothing(root):
    agent, world, fake, _ = make_agent(root)
    long = "a" * 60
    status, body = call(agent, "POST", "/rigs/new", json={"name": "long", "channels": [
        {"name": long, "input": "guitar-in", "width": "mono", "from": {"rig": "guitar_vocal", "channel": "guitar"}}]})
    assert status == 409 and body["code"] == "name", body
    assert not rig_file(root, "long").exists()


def test_git_of_the_source_is_read_outside_the_lifecycle_lock(root, monkeypatch):
    """Review of PR #234: what git says of the file a chain was copied from
    is never read under the lock (2026-11-06); and before the write, so it
    says what was copied (its re-review)."""
    import sushi_rig.save as save

    agent, world, fake, _ = make_agent(root)
    held = []
    real = save.file_git_state
    monkeypatch.setattr(save, "file_git_state", lambda r, p: held.append((p.name, agent._lock.locked())) or real(r, p))
    status, body = load(agent, "vocal", "accustomed", "board")
    assert status == 200 and body["from"]["git"] == "unchanged", body
    status, body = call(agent, "POST", "/rigs/new", json={"name": "solo", "channels": [
        {"name": "a", "input": "guitar-in", "width": "mono", "from": {"rig": "accustomed", "channel": "board"}}]})
    assert status == 200 and body["channels"][0]["from"]["git"] == "unchanged", body
    status, body = call(agent, "POST", "/rigs/solo/channels/add", json={
        "name": "b", "input": "vocal-in", "width": "mono", "from": {"rig": "accustomed", "channel": "board"}})
    assert status == 200 and body["change"]["from"]["git"] == "unchanged", body
    sources = [locked for name, locked in held if name == "accustomed.yaml"]
    assert len(sources) == 3 and not any(sources), held


def test_a_git_that_does_not_answer_leaves_the_sources_git_not_known(root, monkeypatch):
    import threading

    import sushi_rig.agent as agent_module
    import sushi_rig.save as save

    agent, world, fake, _ = make_agent(root)
    release = threading.Event()
    monkeypatch.setattr(agent_module, "SAVE_TIMEOUT", 0.5)
    monkeypatch.setattr(save, "file_git_state", lambda r, p: release.wait(5) and None)
    try:
        status, body = load(agent, "vocal", "accustomed", "board")
    finally:
        release.set()
    assert status == 200 and body["from"]["git"] is None, body
    assert rig_file(root, "guitar_vocal").read_text().count("accustomed / board") == 1, "the load is written"
    assert not agent._lock.locked()
