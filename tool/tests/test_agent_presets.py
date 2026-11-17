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
    agent, world, fake, _ = make_agent(root)
    save_as(agent, "vocal", "voice")
    before = rig_file(root, "guitar_vocal").read_bytes()
    call(agent, "POST", "/live/parameter", json={"processor": "guitar_send", "parameter": "gain",
                                                  "value": rigfile.level_to_gain(-3)})
    status, body = load(agent, "guitar", "voice")
    assert status == 409 and body["code"] == "unsaved" and "guitar_send gain" in body["reason"], body
    assert rig_file(root, "guitar_vocal").read_bytes() == before, "nothing was written"
    status, body = load(agent, "guitar", "voice", discard=True)
    assert status == 200 and body["discarded"] is True, body
    assert plugins_of(fake, "guitar") == ["guitar_dynamics", "guitar_deess", "guitar_eq", "guitar_reverb",
                                          "guitar_limiter"]


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
    raw["channels"][0]["chain"][4]["name"] = "vocal_delay"   # the guitar's delay, named as a vocal's would be
    rigfile.write_rig(rig_file(root, "clash"), raw)
    agent, world, fake, _ = make_agent(root, rig="clash")
    save_as(agent, "guitar", "with-delay")
    before = rig_file(root, "clash").read_bytes()
    status, body = load(agent, "vocal", "with-delay")
    assert status == 409 and body["code"] == "rig_problem" and "vocal_delay" in body["reason"], body
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
                                 "why": "the preset's chain has no guitar_overdrive (nor the same plugin under "
                                        "another name)"}]
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
