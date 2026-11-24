"""Channel presets through the agent (#127): GET /presets, Save channel as…
(POST /rig/channels/<channel>/save-as), loading one onto a channel of the
running rig (POST /rig/channels/<channel>/load) and starting a channel from
one (`from: {"preset"}` in POST /rigs/new and /rigs/<rig>/channels/add).

Against real files in a temporary checkout: the frozen rigs
(rig_fixtures.frozen_checkout), their machine file, presets written by the
routes themselves, and the real compiler (`rig.compile_named_rig`, what `up
--rig` runs). The running rig is `test_agent.RigLive`, a fake Sushi loaded
from that compile, which the agent's real capture and real live switch
(`_switch_held`, #112) read and drive, so "the rig plays the preset" is a
reading of the fake after the load, not a word in an answer.

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

from sushi_rig import midi_listener, presets, rigfile  # noqa: E402
from sushi_rig.agent import Agent, AgentError, Switching, preset_save_request  # noqa: E402
from sushi_rig.rig import compile_named_rig  # noqa: E402

from rig_fixtures import FROZEN_MACHINE, frozen_checkout  # noqa: E402
from test_agent import FakeRunner, RigLive  # noqa: E402

MACHINE = f"machines/{FROZEN_MACHINE.name}"
GIT = ["-c", "user.name=preset test", "-c", "user.email=preset@test.invalid", "-c", "commit.gpgsign=false"]


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


def preset_file(root, name):
    return root / "config" / "channels" / f"{name}.yaml"


def rig_file(root, name):
    return root / "config" / "rigs" / f"{name}.yaml"


# --------------------------------------------------------------------------- #
# The request's shape
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("body", [None, [], {"name": "Bad"}, {"name": "ok", "extra": 1}, {"name": "ok", "replace": "yes"},
                                  {"name": "../rigs/x"}])
def test_a_channel_save_names_a_preset_and_nothing_else(body):
    with pytest.raises(AgentError):
        preset_save_request(body)
    assert preset_save_request({"name": "crunch"}) == ("crunch", False)
    assert preset_save_request({"name": "crunch", "replace": True}) == ("crunch", True)


# --------------------------------------------------------------------------- #
# Save channel as…
# --------------------------------------------------------------------------- #

def test_a_channel_is_saved_as_it_plays_with_base_names_and_no_stereo_copies(root):
    agent, world, fake, runner = make_agent(root)
    assert any(stereo_copy.endswith("_to_stereo") for stereo_copy in fake.chains["guitar"]), \
        "the running chain has the compiler's copies, which a preset never stores"
    status, body = call(agent, "POST", "/live/parameter",
                        json={"processor": "guitar_overdrive", "parameter": next(iter(fake.values["guitar_overdrive"])),
                              "value": 0.25})
    assert status == 200, body
    log_before = len(agent.log)
    status, body = call(agent, "POST", "/rig/channels/guitar/save-as", json={"name": "gv-guitar"})
    assert status == 200, body
    assert body["how"] == "new" and body["file"] == "config/channels/gv-guitar.yaml" and body["committed"] is False
    assert body["library"]["file"] == "untracked"
    text = preset_file(root, "gv-guitar").read_text()
    assert "_to_stereo" not in text and "input:" not in text
    assert text.startswith("# Channel preset gv-guitar: saved from guitar_vocal's guitar on the web UI, ")
    preset = presets.load_preset(preset_file(root, "gv-guitar"))
    assert preset.plugins == ["compressor", "overdrive", "eq", "chorus", "delay", "room"]
    assert preset.width == "mono" and preset.saved_from == {"rig": "guitar_vocal", "channel": "guitar"}
    assert preset.heard_through_amp is None, "a run with no amp"
    turned = next(iter(fake.values["guitar_overdrive"]))
    assert preset.chain[1]["parameters"][turned] == 0.25, "the value as it plays, not as the file had it"
    assert body["preset"]["name"] == "gv-guitar" and body["preset"]["ok"] is True
    # A copy: nothing of the rig changed, nothing logged, and the knob is still unsaved.
    assert len(agent.log) == log_before and fake.writes()[-1][0] == "set_parameter"
    assert runner.calls == []
    status, listed = call(agent, "GET", "/presets")
    assert status == 200 and [p["name"] for p in listed["presets"]] == ["gv-guitar"]


def test_a_plugin_removed_live_is_not_in_the_preset_and_the_level_is(root):
    agent, world, fake, _ = make_agent(root)
    # A remove as POST /live/remove makes and logs one (RigLive's `values`
    # is its table, not the route's read, so the route is not driven here;
    # test_agent_edits.py drives it): the plugin gone from the rig, and the
    # log saying so, which is what a save and a channel save fold from.
    fake.chains["vocal"].remove("vocal_reverb")
    agent._record("remove", {"processor": "vocal_reverb"}, {"track": "vocal", "chain": []})
    status, body = call(agent, "POST", "/live/parameter",
                        json={"processor": "vocal_send", "parameter": "gain", "value": rigfile.level_to_gain(-7.5)})
    assert status == 200, body
    status, body = call(agent, "POST", "/rig/channels/vocal/save-as", json={"name": "voice"})
    assert status == 200, body
    preset = presets.load_preset(preset_file(root, "voice"))
    assert preset.plugins == ["dynamics", "deess", "eq", "limiter"]
    assert preset.level == -7.5


def test_a_guitar_channel_saved_with_the_amp_running_says_so(root):
    # guitar_vocal with electric-chorus-2's amp: a guitar on guitar-in and a
    # vocal on vocal-in, the amp in front of the first only.
    amp = yaml.safe_load(rig_file(root, "electric-chorus-2").read_text())["amp"]
    raw = yaml.safe_load(rig_file(root, "guitar_vocal").read_text())
    rigfile.write_rig(rig_file(root, "guitar_vocal"), {**raw, "amp": amp})
    agent, world, fake, _ = make_agent(root, rig="guitar_vocal", amp=True)
    status, body = call(agent, "POST", "/rig/channels/guitar/save-as", json={"name": "guitar"})
    assert status == 200, body
    assert presets.load_preset(preset_file(root, "guitar")).heard_through_amp == "amp/models/Neve 2.nam"
    assert body["preset"]["heard_through_amp"] == "amp/models/Neve 2.nam"
    status, body = call(agent, "POST", "/rig/channels/vocal/save-as", json={"name": "voice"})
    assert status == 200, body
    assert presets.load_preset(preset_file(root, "voice")).heard_through_amp is None, "vocal-in: no amp in front"
    agent, *_ = make_agent(root, rig="electric-chorus-2", amp=False)
    status, body = call(agent, "POST", "/rig/channels/board/save-as", json={"name": "board-dry", "replace": False})
    assert status == 200, body
    assert presets.load_preset(preset_file(root, "board-dry")).heard_through_amp is None, "run with --no-amp"


def test_a_preset_of_that_name_is_taken_unless_replaced_in_place_with_its_comments(root):
    agent, world, fake, _ = make_agent(root)
    status, body = call(agent, "POST", "/rig/channels/guitar/save-as", json={"name": "crunch"})
    assert status == 200, body
    path = preset_file(root, "crunch")
    path.write_text(path.read_text().replace("chain:\n", "description: my crunch\n# The chain, kept.\nchain:\n"))
    before = path.read_text()
    status, body = call(agent, "POST", "/rig/channels/vocal/save-as", json={"name": "crunch"})
    assert status == 409 and body["code"] == "taken", body
    assert path.read_text() == before, "nothing was written"
    status, body = call(agent, "POST", "/rig/channels/vocal/save-as", json={"name": "crunch", "replace": True})
    assert status == 200 and body["how"] == "updated", body
    text = path.read_text()
    assert "# The chain, kept." in text and text.startswith("# Channel preset crunch: saved from guitar_vocal's guitar")
    preset = presets.load_preset(path)
    assert preset.description == "my crunch" and preset.plugins[0] == "dynamics"
    assert preset.saved_from == {"rig": "guitar_vocal", "channel": "vocal"}


def test_a_channel_the_rig_lacks_and_a_rig_that_cannot_be_saved_are_refused(root):
    agent, world, fake, _ = make_agent(root)
    status, body = call(agent, "POST", "/rig/channels/bass/save-as", json={"name": "bass"})
    assert status == 409 and body["code"] == "no_channel" and "guitar, vocal" in body["reason"], body
    assert not preset_file(root, "bass").exists()
    world.state["mode"] = "panel"
    status, body = call(agent, "POST", "/rig/channels/guitar/save-as", json={"name": "crunch"})
    assert status == 409 and body["code"] == "panel", body
    world.state.update(mode="headless", rig=None)
    status, body = call(agent, "POST", "/rig/channels/guitar/save-as", json={"name": "crunch"})
    assert status == 409 and body["code"] == "v1", body
    status, body = call(agent, "POST", "/rig/channels/guitar/save-as", json={"name": "No"})
    assert status == 400, body
    assert not (root / "config" / "channels").exists() or not any((root / "config" / "channels").iterdir())


def test_a_capture_of_another_rig_saves_nothing(root):
    agent, world, fake, _ = make_agent(root)
    fake.chains["guitar"].insert(0, "stranger")
    fake.plugins["stranger"] = {"uri": "http://example.invalid/x", "uid": None, "type": "lv2"}
    fake.values["stranger"], fake.bypassed["stranger"], fake.properties["stranger"] = {}, False, {}
    status, body = call(agent, "POST", "/rig/channels/guitar/save-as", json={"name": "crunch"})
    assert status == 409 and body["code"] == "not_saved" and "stranger" in body["reason"], body
    assert not preset_file(root, "crunch").exists()


# --------------------------------------------------------------------------- #
# Loading a preset onto a channel of the running rig
# --------------------------------------------------------------------------- #

def save_as(agent, channel, name, **extra):
    status, body = call(agent, "POST", f"/rig/channels/{channel}/save-as", json={"name": name, **extra})
    assert status == 200, body
    return body


def load(agent, channel, preset, **extra):
    return call(agent, "POST", f"/rig/channels/{channel}/load", json={"preset": preset, **extra})


def plugins_of(fake, track):
    return [n for n in fake.chains[track] if not n.endswith("_to_stereo") and not n.endswith("_send")]


def test_a_preset_loads_onto_another_channel_live_with_names_of_its_own(root):
    """The guitar's sound on the vocal's input: two channels from one sound,
    each processor named for its channel, by the live switch, no restart."""
    agent, world, fake, runner = make_agent(root)
    save_as(agent, "guitar", "gv-guitar")
    before = rig_file(root, "guitar_vocal").read_text()
    status, body = load(agent, "vocal", "gv-guitar")
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
    entry = body["entry"]
    assert entry["op"] == "rig" and entry["args"]["preset"] == {"name": "gv-guitar", "channel": "vocal"}
    # The load is a baseline: nothing unsaved after it, and undo stops at it.
    status, undo = call(agent, "POST", "/live/undo")
    assert status == 409, undo
    assert world.state["rig"] == "config/rigs/guitar_vocal.yaml"


def test_a_load_is_undone_by_the_one_undo_the_file_back_and_the_rig_switched_into_it(root):
    """#209: POST /undo after a load puts the rig file's bytes back and
    switches the running rig into it, as the undo of a revert does."""
    agent, world, fake, runner = make_agent(root)
    save_as(agent, "guitar", "gv-guitar")
    path = rig_file(root, "guitar_vocal")
    before = path.read_bytes()
    vocal = plugins_of(fake, "vocal")
    status, body = load(agent, "vocal", "gv-guitar")
    assert status == 200 and body["how"] == "live", body
    assert path.read_bytes() != before
    status, state = call(agent, "GET", "/undo")
    assert status == 200 and state["next"]["label"] == "load gv-guitar" and state["next"]["act"] == "load", state
    syncs, real = [], agent._midi_sync

    async def counting():
        syncs.append(1)
        await real()

    agent._midi_sync = counting
    status, body = call(agent, "POST", "/undo", json={})
    assert status == 200, body
    assert body["said"] == "Undone: load gv-guitar" and body["how"] == "switched", body
    assert path.read_bytes() == before, "the file as it was before the load, byte for byte"
    assert plugins_of(fake, "vocal") == vocal, "the rig plays it again: a reading of the fake"
    assert runner.calls == [], "nothing was restarted"
    assert syncs, "the switch stopped the pedals' listener: it follows the rig after (#235's review)"


def test_a_preset_loaded_back_onto_its_channel_sets_what_differs(root):
    agent, world, fake, runner = make_agent(root)
    save_as(agent, "guitar", "gv-guitar")
    turned = next(iter(fake.values["guitar_overdrive"]))
    path = preset_file(root, "gv-guitar")
    doc = yaml.safe_load(path.read_text())
    doc["chain"][1]["parameters"] = {turned: 0.125}
    doc["chain"][2]["bypassed"] = True
    doc["level"] = -4.0
    presets.write_preset(path, doc)
    status, body = load(agent, "guitar", "gv-guitar")
    assert status == 200 and body["how"] == "live", body
    assert [w[0] for w in fake.writes() if w[0] in ("add", "remove", "move")] == [], "the same plugins: no structure changed"
    assert fake.values["guitar_overdrive"][turned] == pytest.approx(0.125)
    assert fake.bypassed["guitar_eq"] is True
    assert fake.values["guitar_send"]["gain"] == pytest.approx(rigfile.level_to_gain(-4.0), abs=1e-6)
    written = yaml.safe_load(rig_file(root, "guitar_vocal").read_text())["channels"][0]
    assert written["level"] == -4.0 and written["chain"][2]["bypassed"] is True


def test_a_load_with_unsaved_work_is_refused_and_allowed_with_discard(root):
    """Discarded means gone, on a channel the load leaves alone too (review
    of #162): the knob turned on the vocal is put back to what the file
    gives it, by the switch's own steps, not left playing under a baseline
    that says nothing is unsaved."""
    agent, world, fake, _ = make_agent(root)
    save_as(agent, "vocal", "voice")
    before = rig_file(root, "guitar_vocal").read_bytes()
    was = fake.values["vocal_eq"]["High"]
    for body in ({"processor": "vocal_eq", "parameter": "High", "value": 0.987},
                 {"processor": "guitar_send", "parameter": "gain", "value": rigfile.level_to_gain(-3)}):
        status, said = call(agent, "POST", "/live/parameter", json=body)
        assert status == 200, said
    status, body = call(agent, "POST", "/live/bypass", json={"processor": "vocal_deess", "bypassed": True})
    assert status == 200, body
    status, body = load(agent, "guitar", "voice")
    assert status == 409 and body["code"] == "unsaved" and "vocal_deess" in body["reason"], body
    assert rig_file(root, "guitar_vocal").read_bytes() == before, "nothing was written"
    status, body = load(agent, "guitar", "voice", discard=True)
    assert status == 200 and body["discarded"] is True and body["how"] == "live", body
    assert plugins_of(fake, "guitar") == ["guitar_dynamics", "guitar_deess", "guitar_eq", "guitar_reverb",
                                          "guitar_limiter"]
    assert fake.values["vocal_eq"]["High"] == pytest.approx(was), "the discarded knob is put back"
    assert fake.bypassed["vocal_deess"] is False, "and the discarded bypass"
    # The bypass the diff puts back itself (a bypass neither file states is
    # un-bypassed); the knob neither states only the discard does.
    restored = {(s["processor"], s.get("parameter", "bypass")) for s in body["discard"]["restored"]}
    assert restored == {("vocal_eq", "High")}
    assert body["discard"]["changes"] == 3 and "put back each value" in body["discard"]["note"]
    status, health = call(agent, "GET", "/health")
    assert status == 200


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
    save_as(agent, "vocal", "voice")
    fake.chains["guitar"].remove("guitar_delay")  # removed live, and logged as the route logs it
    agent._record("remove", {"processor": "guitar_delay"}, {"track": "guitar", "chain": []})
    status, body = load(agent, "vocal", "voice", discard=True)
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


def test_the_file_as_it_was_is_kept_for_revert(root):
    agent, world, fake, _ = make_agent(root)
    save_as(agent, "guitar", "gv-guitar")
    before = rig_file(root, "guitar_vocal").read_bytes()
    status, body = load(agent, "vocal", "gv-guitar")
    assert status == 200, body
    kept = body["kept_before"]
    assert kept["how"] == "before loading channel preset gv-guitar onto vocal"
    status, listed = call(agent, "GET", "/rigs/guitar_vocal/saves")
    assert any(p["id"] == kept["id"] and p["how"] == kept["how"] for p in listed["points"]), listed
    status, body = call(agent, "POST", "/rigs/guitar_vocal/revert", json={"save": kept["id"]})
    assert status == 200 and body["how"] == "live", body
    assert rig_file(root, "guitar_vocal").read_bytes() == before
    assert plugins_of(fake, "vocal") == ["vocal_dynamics", "vocal_deess", "vocal_eq", "vocal_reverb",
                                         "vocal_limiter"], "the vocal's chain is back"



def test_a_file_a_save_keeps_already_is_not_kept_twice(root):
    agent, world, fake, _ = make_agent(root, rig="electric_board")
    save_as(agent, "board", "board")
    status, save = call(agent, "POST", "/rig/save")
    assert status == 200, save
    status, body = load(agent, "board", "board")
    assert status == 200, body
    assert body["kept_before"] == {"id": save["snapshot"]["id"], "how": "kept by that save already"}


def test_what_cannot_be_loaded_is_refused_before_anything_is_written(root):
    agent, world, fake, _ = make_agent(root)
    before = rig_file(root, "guitar_vocal").read_bytes()
    status, body = load(agent, "guitar", "nothing")
    assert status == 409 and body["code"] == "no_preset", body
    save_as(agent, "vocal", "voice")
    status, body = load(agent, "bass", "voice")
    assert status == 409 and body["code"] == "no_channel", body
    preset_file(root, "broken").write_text("kind: channel\nschema_version: 1\nname: broken\nwidth: mono\nchain: [x]\n")
    status, body = load(agent, "guitar", "broken")
    assert status == 409 and body["code"] == "preset_problem", body
    doc = yaml.safe_load(preset_file(root, "voice").read_text())
    doc["chain"][0]["uri"] = "http://example.invalid/not-in-the-table"
    presets.write_preset(preset_file(root, "unknown"), {**doc, "name": "unknown"})
    status, body = load(agent, "guitar", "unknown")
    assert status == 409 and body["code"] == "preset_problem" and "plugin-ports.txt" in body["reason"], body
    status, body = load(agent, "guitar", "../rigs/guitar_vocal")
    assert status == 400, body
    assert rig_file(root, "guitar_vocal").read_bytes() == before
    assert fake.writes() == []


def test_a_name_another_channel_has_is_the_compilers_refusal(root):
    raw = yaml.safe_load(rig_file(root, "guitar_vocal").read_text())
    raw["name"] = "clash"
    raw["channels"][0]["chain"][4]["name"] = "vocal_room"   # the guitar's delay, named as a vocal's room would be
    rigfile.write_rig(rig_file(root, "clash"), raw)
    agent, world, fake, _ = make_agent(root, rig="clash")
    save_as(agent, "guitar", "with-delay")
    before = rig_file(root, "clash").read_bytes()
    status, body = load(agent, "vocal", "with-delay")
    assert status == 409 and body["code"] == "rig_problem" and "vocal_room" in body["reason"], body
    assert rig_file(root, "clash").read_bytes() == before


def with_midi(root, rig, controls):
    """Rig `rig` of the checkout with a MIDI map for the frozen machine
    file's controller, written as the tool writes a rig file."""
    path = rig_file(root, rig)
    rigfile.write_rig(path, {**yaml.safe_load(path.read_text()),
                             "midi": {"controller": "mpd218", "controls": controls}})


def test_a_load_that_would_orphan_a_mapping_is_refused_or_drops_it_saying_what_it_was(root):
    with_midi(root, "guitar_vocal", [{"control": "pad1", "bypass": "guitar/guitar_overdrive"},
                                     {"control": "pad2", "mute": "vocal"}])
    agent, world, fake, runner = make_agent(root)
    save_as(agent, "vocal", "voice")
    before = rig_file(root, "guitar_vocal").read_bytes()
    status, body = load(agent, "guitar", "voice")
    assert status == 409 and body["code"] == "midi", body
    assert "pad1 (bypass guitar/guitar_overdrive)" in body["reason"]
    assert body["mappings"] == [{"control": "pad1", "binding": "bypass", "target": "guitar/guitar_overdrive",
                                 "why": "the preset's chain has no plugin like guitar_overdrive "
                                        "(http://guitarix.sourceforge.net/plugins/gxts9#ts9sim): it holds "
                                        "guitar_dynamics, guitar_deess, guitar_eq, guitar_reverb, guitar_limiter"}]
    assert rig_file(root, "guitar_vocal").read_bytes() == before
    status, body = load(agent, "guitar", "voice", drop_midi=True)
    assert status == 200, body
    assert [d["control"] for d in body["dropped"]] == ["pad1"]
    written = yaml.safe_load(rig_file(root, "guitar_vocal").read_text())
    assert written["midi"]["controls"] == [{"control": "pad2", "mute": "vocal"}]
    # A bypass binding is the agent's to serve, not Sushi's: dropping one is
    # no reason to restart (rigfile.diff compares Sushi's cc_mappings).
    assert body["how"] == "live", body


def test_a_mapping_follows_its_plugin_to_its_new_name_and_the_restart_says_why(root):
    """electric_board names its plugins without their channel's prefix, so
    its own sound loaded back renames every one: a pedal on the overdrive
    follows it, and since a knob's mapping is Sushi's, loaded only with the
    config, the load is a restart, which it says."""
    with_midi(root, "electric_board", [{"control": "pad1", "bypass": "board/overdrive"},
                                       {"control": "knob1", "parameter": "board/overdrive/Drive"}])
    agent, world, fake, runner = make_agent(root, rig="electric_board")
    save_as(agent, "board", "board")
    status, body = load(agent, "board", "board")
    assert status == 200, body
    assert body["followed"] == [{"control": "pad1", "from": "board/overdrive", "to": "board/board_overdrive"},
                                {"control": "knob1", "from": "board/overdrive/Drive", "to": "board/board_overdrive/Drive"}]
    written = yaml.safe_load(rig_file(root, "electric_board").read_text())
    assert written["midi"]["controls"] == [{"control": "pad1", "bypass": "board/board_overdrive"},
                                           {"control": "knob1", "parameter": "board/board_overdrive/Drive"}]
    assert all(p["name"].startswith("board_") for p in written["channels"][0]["chain"])
    assert body["how"] == "restart" and "midi" in [w["code"] for w in body["why"]], body
    assert runner.calls and runner.calls[0][0] == "down"


def test_a_preset_heard_through_the_amp_says_where_it_lands_without_one(root):
    amp = yaml.safe_load(rig_file(root, "electric-chorus-2").read_text())["amp"]
    raw = yaml.safe_load(rig_file(root, "guitar_vocal").read_text())
    rigfile.write_rig(rig_file(root, "guitar_vocal"), {**raw, "amp": amp})
    agent, world, fake, _ = make_agent(root, amp=True)
    save_as(agent, "guitar", "guitar")
    status, body = load(agent, "vocal", "guitar")
    assert status == 200, body
    assert body["amp"]["in_front"] is False and "vocal has no amp" in body["amp"]["note"], body
    assert "guitar-in only" in body["amp"]["note"]


def test_a_preset_of_another_width_leaves_the_channel_its_own(root):
    agent, world, fake, _ = make_agent(root)
    save_as(agent, "vocal", "voice")
    doc = yaml.safe_load(preset_file(root, "voice").read_text())
    presets.write_preset(preset_file(root, "voice"), {**doc, "width": "stereo"})
    status, body = load(agent, "guitar", "voice")
    assert status == 200, body
    assert "guitar stays mono" in body["width"]
    assert yaml.safe_load(rig_file(root, "guitar_vocal").read_text())["channels"][0]["width"] == "mono"


def test_a_load_refuses_a_rig_that_is_not_one_of_channels_and_a_busy_agent(root):
    agent, world, fake, _ = make_agent(root)
    save_as(agent, "vocal", "voice")
    world.state["mode"] = "panel"
    status, body = load(agent, "guitar", "voice")
    assert status == 409 and body["code"] == "panel", body
    world.state["mode"] = "headless"

    async def held():
        async with agent._lock:
            agent.busy = "switch"
            async with TestClient(TestServer(agent.app())) as client:
                response = await client.post("/rig/channels/guitar/load", json={"preset": "voice"})
                return response.status, await response.json()

    status, body = asyncio.run(held())
    assert status == 409 and body["code"] == "busy", body


# --------------------------------------------------------------------------- #
# A channel from a preset: Add channel and New rig (#125's machinery)
# --------------------------------------------------------------------------- #

def test_a_new_rig_of_two_channels_from_one_preset_compiles_with_names_of_their_own(root):
    agent, world, fake, _ = make_agent(root)
    save_as(agent, "guitar", "gv-guitar")
    status, body = call(agent, "POST", "/rigs/new", json={"name": "two-guitars", "channels": [
        {"name": "left", "input": "guitar-in", "from": {"preset": "gv-guitar"}},
        {"name": "right", "input": "vocal-in", "width": "mono", "from": {"preset": "gv-guitar"}}]})
    assert status == 200, body
    raw = yaml.safe_load(rig_file(root, "two-guitars").read_text())
    left, right = raw["channels"]
    assert left["width"] == "mono", "the preset's preferred width, the request having none"
    assert [p["name"] for p in left["chain"]][:2] == ["left_compressor", "left_overdrive"]
    assert [p["name"] for p in right["chain"]][:2] == ["right_compressor", "right_overdrive"]
    assert "input" not in yaml.safe_load(preset_file(root, "gv-guitar").read_text())
    compile_named_rig("two-guitars", root, root / MACHINE)
    text = rig_file(root, "two-guitars").read_text()
    assert "# left: its chain and level copied from channel preset gv-guitar (config/channels/gv-guitar.yaml)." in text
    said = {c["name"]: c["from"] for c in body["channels"]}
    assert said["left"]["preset"] == "gv-guitar" and said["left"]["amp"]["in_front"] is False


def test_the_second_guitar_is_told_it_has_no_amp(root):
    amp = yaml.safe_load(rig_file(root, "electric-chorus-2").read_text())["amp"]
    raw = yaml.safe_load(rig_file(root, "guitar_vocal").read_text())
    rigfile.write_rig(rig_file(root, "guitar_vocal"), {**raw, "amp": amp})
    agent, world, fake, _ = make_agent(root, amp=True)
    save_as(agent, "guitar", "guitar")
    # A rig of one guitar on guitar-in, with the amp, gains a second guitar on vocal-in.
    raw = yaml.safe_load(rig_file(root, "electric-chorus-2").read_text())
    status, body = call(agent, "POST", "/rigs/electric-chorus-2/channels/add",
                        json={"name": "second", "input": "vocal-in", "from": {"preset": "guitar"}})
    assert status == 200, body
    note = body["change"]["from"]["amp"]
    assert note["in_front"] is False and "second has no amp" in note["note"] and "guitar-in only" in note["note"]
    written = yaml.safe_load(rig_file(root, "electric-chorus-2").read_text())
    assert written["channels"][0] == raw["channels"][0]
    assert written["channels"][1]["name"] == "second" and written["channels"][1]["chain"][0]["name"] == "second_compressor"
    # On guitar-in itself the amp is in front of it, and nothing is said.
    status, body = call(agent, "POST", "/rigs/electric-chorus-hall/channels/add",
                        json={"name": "first", "input": "guitar-in", "from": {"preset": "guitar"}})
    assert status == 200, body
    assert body["change"]["from"]["amp"] == {"in_front": True, "saved_with": "amp/models/Neve 2.nam", "note": None}


def test_the_width_refusal_applies_to_a_preset_as_to_any_channel(root):
    agent, world, fake, _ = make_agent(root)
    save_as(agent, "vocal", "voice")
    doc = yaml.safe_load(preset_file(root, "voice").read_text())
    presets.write_preset(preset_file(root, "voice"), {**doc, "width": "stereo"})
    machine = root / MACHINE
    machine.write_text(machine.read_text().replace("    mono_to_stereo: true\n", "", 1))  # guitar-in: one port, no fan-out
    world.state["machine"] = str(machine)
    status, body = call(agent, "POST", "/rigs/new", json={"name": "wide", "channels": [
        {"name": "keys", "input": "guitar-in", "from": {"preset": "voice"}}]})
    assert status == 409 and body["code"] == "rig_problem" and "mono_to_stereo" in body["reason"], body
    assert not rig_file(root, "wide").exists()


def test_a_preset_that_is_not_there_or_will_not_load_is_refused_for_a_new_channel(root):
    agent, world, fake, _ = make_agent(root)
    before = rig_file(root, "electric_board").read_bytes()
    status, body = call(agent, "POST", "/rigs/electric_board/channels/add",
                        json={"name": "keys", "input": "vocal-in", "width": "mono", "from": {"preset": "nothing"}})
    assert status == 409 and body["code"] == "no_preset", body
    status, body = call(agent, "POST", "/rigs/electric_board/channels/add",
                        json={"name": "keys", "input": "vocal-in", "from": {"preset": "Bad/name"}})
    assert status == 409 and body["code"] == "no_preset", body
    status, body = call(agent, "POST", "/rigs/electric_board/channels/add",
                        json={"name": "keys", "input": "vocal-in", "from": {"preset": "x", "rig": "y"}})
    assert status == 400, body
    status, body = call(agent, "POST", "/rigs/electric_board/channels/add",
                        json={"name": "keys", "input": "vocal-in", "from": {"rig": "electric_board", "channel": "board"}})
    assert status == 400, "a rig's channel still needs its width"
    assert rig_file(root, "electric_board").read_bytes() == before


# --------------------------------------------------------------------------- #
# The provenance line in the rig file
# --------------------------------------------------------------------------- #

def provenance_of(root, rig, channel):
    lines = rig_file(root, rig).read_text().splitlines()
    name = next(i for i, line in enumerate(lines) if line.strip() in (f"- name: {channel}", f"name: {channel}"))
    chain = next(i for i in range(name, len(lines)) if lines[i].strip().startswith("chain:"))
    return [line.strip() for line in lines[name:chain] if line.strip().startswith("#")]


def test_every_copy_from_a_preset_names_it_above_the_chain(root):
    agent, world, fake, _ = make_agent(root)
    save_as(agent, "guitar", "gv-guitar")
    save_as(agent, "vocal", "voice")
    status, body = load(agent, "vocal", "gv-guitar")
    assert status == 200 and "provenance_note" not in body, body
    said = provenance_of(root, "guitar_vocal", "vocal")
    assert said[0].startswith("# From channel preset gv-guitar (config/channels/gv-guitar.yaml), 20"), said
    status, body = load(agent, "vocal", "voice")
    assert status == 200, body
    said = provenance_of(root, "guitar_vocal", "vocal")
    assert said[0].startswith("# From channel preset voice ") and not any("gv-guitar" in s for s in said), \
        "the next load replaces the line, never stacks it"
    assert provenance_of(root, "guitar_vocal", "guitar") == [], "the other channel says nothing"
    status, body = call(agent, "POST", "/rigs/electric_board/channels/add",
                        json={"name": "keys", "input": "vocal-in", "from": {"preset": "voice"}})
    assert status == 200 and "provenance_note" not in body["change"], body
    assert provenance_of(root, "electric_board", "keys")[0].startswith("# From channel preset voice ")
    status, body = call(agent, "POST", "/rigs/new", json={"name": "duo", "channels": [
        {"name": "a", "input": "guitar-in", "from": {"preset": "voice"}},
        {"name": "b", "input": "vocal-in", "width": "mono"}]})
    assert status == 200, body
    assert provenance_of(root, "duo", "a")[0].startswith("# From channel preset voice ")
    assert provenance_of(root, "duo", "b") == []
    compile_named_rig("duo", root, root / MACHINE)


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
def test_a_save_or_a_load_from_another_site_is_refused_with_nothing_written(root, headers):
    agent, world, fake, _ = make_agent(root)
    save_as(agent, "vocal", "voice")
    before = (rig_file(root, "guitar_vocal").read_bytes(), preset_file(root, "voice").read_bytes())
    for path, body in (("/rig/channels/guitar/save-as", {"name": "stolen"}),
                       ("/rig/channels/guitar/load", {"preset": "voice"})):
        status, said = guarded(agent, path, body, headers)
        assert status == 403 and said["reason"].startswith("refused"), said
    assert (rig_file(root, "guitar_vocal").read_bytes(), preset_file(root, "voice").read_bytes()) == before
    assert not preset_file(root, "stolen").exists() and fake.writes() == []


@pytest.mark.parametrize("headers, own", [({}, False), ({"Sec-Fetch-Site": "same-origin"}, True)])
def test_curl_and_the_agents_own_page_may_save_and_load(root, headers, own):
    agent, world, fake, _ = make_agent(root)
    status, body = guarded(agent, "/rig/channels/vocal/save-as", {"name": "voice"}, headers, own)
    assert status == 200, body
    status, body = guarded(agent, "/rig/channels/guitar/load", {"preset": "voice"}, headers, own)
    assert status == 200, body


def test_a_channel_whose_names_keep_their_prefix_saves_and_loads_back(root):
    """`eq` beside `guitar_eq` on the guitar: taking the prefixes off would
    make two names one, so none is taken off, and a load prefixes every
    one, so the two stay two (review of #162: `guitar_eq` was not prefixed
    again, `eq` became `guitar_eq`, and the load back was a duplicate)."""
    raw = yaml.safe_load(rig_file(root, "guitar_vocal").read_text())
    raw["channels"][0]["chain"][3]["name"] = "eq"   # the chorus, named so
    rigfile.write_rig(rig_file(root, "guitar_vocal"), raw)
    agent, world, fake, _ = make_agent(root)
    body = save_as(agent, "guitar", "both")
    assert body["preset"]["ok"] is True, body
    assert presets.load_preset(preset_file(root, "both")).plugins[2:4] == ["guitar_eq", "eq"]
    status, body = load(agent, "guitar", "both")
    assert status == 200, body
    names = [p["name"] for p in yaml.safe_load(rig_file(root, "guitar_vocal").read_text())["channels"][0]["chain"]]
    assert names[2:4] == ["guitar_guitar_eq", "guitar_eq"] and len(set(names)) == len(names)
    assert names[0] == "guitar_guitar_compressor"


def test_a_comment_directly_above_a_channels_chain_survives_a_load(root):
    """The comment is the channel's, not the chain's (review of #162: the
    whole-chain replace dropped it with the chain)."""
    path = rig_file(root, "electric_board")
    text = path.read_text()
    assert "\n  chain:\n" in text
    path.write_text(text.replace("\n  chain:\n", "\n  # The board: keep this note.\n  chain:\n", 1))
    agent, world, fake, _ = make_agent(root, rig="electric_board")
    save_as(agent, "board", "board")
    for _ in range(2):
        status, body = load(agent, "board", "board")
        assert status == 200, body
        said = provenance_of(root, "electric_board", "board")
        assert said[0] == "# The board: keep this note.", said
        assert sum(s.startswith("# From channel preset board ") for s in said) == 1, said


def test_a_load_whose_switch_fails_says_so_as_a_load_and_the_pedals_follow_what_runs(root):
    """The file is written and the rig would not play it (the live switch
    finds the rig changed under it, and the restart fails): /health names
    the load, not a revert, and the pedals are planned from the compiled
    config the state file names, which is the rig still running, not from
    the file just written (review of #162)."""
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
    save_as(agent, "board", "board")
    runner.outcome = Outcome(False, 1, "", "error: the test's restart fails\n")
    fake.drift = lambda rig: rig.chains["board"].remove("delay")
    status, body = load(agent, "board", "board")
    assert status == 409 and body["code"] == "not_switched" and body["written"] is True, body
    assert "midi_note" in body and "not the file just written" in body["midi_note"]
    assert planned and planned[-1] == ["board/overdrive"], planned
    assert "overdrive" in fake.chains["board"], "the processor the pedal names is the one Sushi has"
    status, health = call(agent, "GET", "/health")
    error = health["agent"]["last_error"]
    assert error and error["command"][0] == "load" and "revert" not in error["command"], error


def test_no_revert_point_is_kept_for_a_load_whose_write_is_refused(root, monkeypatch):
    from sushi_rig import agent as agent_module, saves

    agent, world, fake, _ = make_agent(root)
    save_as(agent, "vocal", "voice")
    before = rig_file(root, "guitar_vocal").read_bytes()

    def refuse(*args, **kwargs):
        raise rigfile.RigFileError(["refused by the test"])

    monkeypatch.setattr(agent_module.rigfile, "write_rig", refuse)
    status, body = load(agent, "guitar", "voice")
    assert status == 409 and body["code"] == "rig_problem" and "refused by the test" in body["reason"], body
    assert rig_file(root, "guitar_vocal").read_bytes() == before
    assert saves.snapshots(root, "guitar_vocal")[0] == [], "no point for a load that never happened"
    assert fake.writes() == []


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


def test_a_load_whose_plugins_are_not_all_there_writes_nothing(root, tmp_path, monkeypatch):
    """Review of PR #198: a load wrote the rig file, then the switch into it
    refused it for a plugin plugins/ lacked, and answered 409 `not_switched`
    with `written: true`. Now refused before the write, with the file as it
    was and nothing played."""
    import shutil

    from rig_fixtures import declaring, fake_lv2_system, stand_in_plugins

    gxts9 = "http://guitarix.sourceforge.net/plugins/gxts9#ts9sim"
    declaring(fake_lv2_system(tmp_path, monkeypatch), "gxts9", gxts9)
    agent, world, fake, runner = make_agent(root)
    save_as(agent, "guitar", "gv-guitar")
    shutil.rmtree(root / "plugins")
    stand_in_plugins(root / "plugins", leave_out=(gxts9,))
    before = rig_file(root, "guitar_vocal").read_bytes()
    status, body = load(agent, "vocal", "gv-guitar")
    assert status == 409 and body["code"] == "plugins_missing", body
    assert body["reason"].startswith(
        "nothing was written: guitar_vocal was not given preset gv-guitar on vocal: 1 plugin(s)")
    assert gxts9 in body["reason"] and "then load it again: sushi-rig plugins add" in body["reason"]
    assert "written" not in body
    assert rig_file(root, "guitar_vocal").read_bytes() == before, "the file is untouched"
    assert fake.writes() == [] and runner.calls == []
