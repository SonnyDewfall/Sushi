"""An amp for a rig whose file has none (#246's cause 1), through the agent:
`POST /amp/add {"model"}`, GET /amp's offer of it (`add_amp`), and a channel
loaded with its rig's amp (`POST /rig/channels/<channel>/load {"with_amp":
true}`).

Against real files in a temporary checkout, as test_agent_channel_load.py's
are: the frozen rigs, their machine file and the real compiler. The running
rig is `test_agent.RigLive`, driven by the agent's real switch, and `up` and
`down` are `FakeRunner`'s, so "the rig restarts with the amp" is what the
agent asked to run, not a Carla that came up.

NOT shown: any of it against a live Sushi or Carla. Whether Carla starts on
the model written, and how long the restart silences the rig, are not
tested here.
"""

from __future__ import annotations

import pytest
import yaml

pytest.importorskip("aiohttp")

from sushi_rig import rigedit  # noqa: E402
from sushi_rig.agent import AgentError, channel_load_request, load_with_amp  # noqa: E402
from sushi_rig.amp import DEFAULTS  # noqa: E402
from sushi_rig.rig import compile_named_rig  # noqa: E402

from test_agent_amp import nam  # noqa: E402
from test_agent_channel_load import (  # noqa: E402,F401
    _agents_release_their_pools, call, git, load, make_agent, rig_file, root,
)

MODEL = "Test Amp.nam"  # invented, in the checkout's amp/models


@pytest.fixture
def models(root):
    """The checkout with a model at the rig's rate, and one that is not."""
    folder = root / "amp" / "models"
    folder.mkdir(parents=True, exist_ok=True)
    nam(folder / MODEL, "Test Amp")
    nam(folder / "Other Rate.nam", "Other Rate", rate=44100)
    return root


def add(agent, model=MODEL, **extra):
    return call(agent, "POST", "/amp/add", json={"model": model, **extra})


def up_call(runner):
    return next(c for c in runner.calls if c[:1] == ["up"])


# --------------------------------------------------------------------------- #
# The pure parts: the request, and the file with an amp
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("body", [None, [], {}, {"model": ""}, {"model": 3}, {"model": "a", "x": 1},
                                  {"model": "a", "rig": ""}, {"model": "a", "discard": "yes"}])
def test_an_amp_added_names_a_model_and_nothing_else(body):
    with pytest.raises(rigedit.EditRequestError):
        rigedit.amp_request(body)
    assert rigedit.amp_request({"model": "a"}) == {"model": "a", "rig": None, "discard": False}
    assert rigedit.amp_request({"model": "a", "rig": "r", "discard": True}) == {
        "model": "a", "rig": "r", "discard": True}


def test_a_new_amp_is_nam_at_its_defaults_and_goes_above_the_channels():
    amp = rigedit.new_amp("amp/models/x.nam")
    assert amp == {"plugin": "nam", "model": "amp/models/x.nam", "parameters": DEFAULTS}
    raw = {"kind": "rig", "name": "r", "host": {}, "channels": [{"name": "c"}], "master": {}}
    edited = rigedit.with_amp(raw, amp)
    assert list(edited) == ["kind", "name", "host", "amp", "channels", "master"]
    assert "amp" not in raw, "the document given is left as it is"
    assert list(rigedit.with_amp({"name": "r"}, amp)) == ["name", "amp"]
    assert rigedit.has_amp(edited) and not rigedit.has_amp(raw) and not rigedit.has_amp(None)
    assert rigedit.has_amp({"amp": {}}), "an amp: there and wrong is the compiler's, never written over"


def test_a_load_may_ask_for_the_source_rigs_amp():
    assert channel_load_request({"from": {"rig": "a", "channel": "b"}, "with_amp": True}) == (
        {"rig": "a", "channel": "b"}, False, False, None)
    assert load_with_amp({"from": {"rig": "a", "channel": "b"}, "with_amp": True}) is True
    assert load_with_amp({"from": {"rig": "a", "channel": "b"}}) is False
    with pytest.raises(AgentError, match="'with_amp' must be true or false"):
        channel_load_request({"from": {"rig": "a", "channel": "b"}, "with_amp": "yes"})


# --------------------------------------------------------------------------- #
# GET /amp's offer
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("asked", [True, False])
def test_a_rig_whose_file_has_no_amp_is_offered_one(root, asked):
    """No `amp:` in the file is another case than a run with no amp (#247's
    Amp on): whether or not the run asked for the amp, GET /amp's 409
    `no_amp` says `add_amp`, for the rig `rig` names."""
    agent, world, _, _ = make_agent(root)
    world.state["amp_requested"] = asked
    status, body = call(agent, "GET", "/amp")
    assert (status, body["code"]) == (409, "no_amp"), body
    assert body["add_amp"] is True and body["amp_on"] is False and body["rig"] == "guitar_vocal", body


def test_a_rig_whose_file_has_an_amp_is_offered_none_to_add(root):
    agent, world, _, _ = make_agent(root, rig="accustomed")
    status, body = call(agent, "GET", "/amp")
    assert (status, body["code"]) == (409, "no_amp"), body
    assert body["add_amp"] is False and body["amp_on"] is True and body["rig"] == "accustomed", body


def test_a_rig_run_with_its_panel_is_offered_none_to_add(root):
    agent, world, _, _ = make_agent(root)
    world.state["mode"] = "panel"
    status, body = call(agent, "GET", "/amp")
    assert (status, body["add_amp"]) == (409, False), body


def test_the_offer_reads_the_rig_file_once_until_it_changes(root, monkeypatch):
    """#248's review: every page polls GET /amp each second, and the offer
    parsed the rig file on every poll. Kept by the file's path, size and
    mtime: two polls parse it once, and a write makes the next parse it
    again (and say what it now says)."""
    import os

    parsed = []
    real = yaml.safe_load

    def counting(text, *args, **kwargs):
        if isinstance(text, str) and "name: guitar_vocal" in text:
            parsed.append(1)
        return real(text, *args, **kwargs)

    agent, world, _, _ = make_agent(root)  # which compiles the file: counted from here
    monkeypatch.setattr(yaml, "safe_load", counting)
    path = rig_file(root, "guitar_vocal")
    assert call(agent, "GET", "/amp")[1]["add_amp"] is True
    assert call(agent, "GET", "/amp")[1]["add_amp"] is True
    assert len(parsed) == 1, "two polls, one parse"
    stat = path.stat()
    path.write_text(path.read_text().replace("\nchannels:", "\namp: {plugin: nam, model: amp/models/x.nam}\nchannels:", 1))
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
    assert call(agent, "GET", "/amp")[1]["add_amp"] is False, "the file as written"
    assert len(parsed) == 2, "a write is parsed again"
    world.state["mode"] = "panel"
    assert call(agent, "GET", "/amp")[1]["add_amp"] is False and len(parsed) == 2, \
        "the run is asked each time; the file is not read for it"


# --------------------------------------------------------------------------- #
# POST /amp/add
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("asked", [True, False])
def test_an_amp_added_is_written_above_the_channels_and_the_rig_restarts_with_it(models, asked):
    """The file gains `amp:` (NAM, the model, the amp's defaults), its
    comments kept; the rig is switched into it by restart, with the amp even
    for a run started with --no-amp (`asked` False). A revert point keeps
    the file as it was."""
    agent, world, fake, runner = make_agent(models)
    world.state["amp_requested"] = asked
    path = rig_file(models, "guitar_vocal")
    before = path.read_text()
    status, body = add(agent, rig="guitar_vocal")
    assert status == 200, body
    written = yaml.safe_load(path.read_text())
    assert written["amp"] == {"plugin": "nam", "model": f"amp/models/{MODEL}", "parameters": DEFAULTS}
    assert list(written).index("amp") == list(written).index("channels") - 1, "above the channels"
    assert {k: v for k, v in written.items() if k != "amp"} == yaml.safe_load(before)
    assert path.read_text().startswith("# Guitar and vocals as a v2 rig (#30)"), "the file's comments are kept"
    assert body["amp"] == written["amp"] and body["file"] == "config/rigs/guitar_vocal.yaml"
    assert body["how"] == "restart" and "amp" in [w["code"] for w in body["why"]], body
    assert runner.calls[0] == ["down"]
    assert "--no-amp" not in up_call(runner), "the restart brings the amp"
    assert body["entry"]["args"]["amp_added"] == f"amp/models/{MODEL}"
    assert body["kept_before"]["id"], "Revert… goes back"
    assert body["library"]["file"] == "changed", "what git says of the file"


def test_an_amp_added_is_undone_by_the_one_undo_by_restart_again(models):
    agent, world, fake, runner = make_agent(models)
    world.state["amp_requested"] = True
    path = rig_file(models, "guitar_vocal")
    before = path.read_bytes()
    assert add(agent)[0] == 200
    # What `up` left, which `FakeRunner` does not: the running config is the
    # file's, amp and all.
    world.configs[world.state["config"]] = compile_named_rig("guitar_vocal", models, world.machine)
    status, state = call(agent, "GET", "/undo")
    assert state["next"]["label"] == f"add amp {MODEL[:-4]}" and state["next"]["act"] == "add_amp", state
    status, held = call(agent, "POST", "/undo", json={"seq": state["next"]["seq"], "restart": False})
    assert (status, held["code"]) == (409, "restarts"), held
    assert path.read_bytes() != before, "nothing put back before the restart is agreed"
    status, body = call(agent, "POST", "/undo", json={"seq": state["next"]["seq"], "restart": True})
    assert status == 200, body
    assert path.read_bytes() == before, "the file as it was, byte for byte"


@pytest.mark.parametrize("model, code", [("Missing.nam", None), ("Other Rate.nam", "wrong_rate"),
                                         ("../x.nam", None)])
def test_a_model_that_is_not_one_is_refused_before_anything_is_written(models, model, code):
    agent, world, fake, runner = make_agent(models)
    before = rig_file(models, "guitar_vocal").read_bytes()
    status, body = add(agent, model=model)
    assert status == 400, body
    if code:
        assert body["code"] == code, body
    assert rig_file(models, "guitar_vocal").read_bytes() == before and runner.calls == []


def test_a_rig_with_an_amp_already_is_refused_one(models):
    agent, world, fake, runner = make_agent(models, rig="accustomed")
    before = rig_file(models, "accustomed").read_bytes()
    status, body = add(agent)
    assert (status, body["code"]) == (409, "has_amp"), body
    assert rig_file(models, "accustomed").read_bytes() == before and runner.calls == []


def test_an_amp_for_another_rig_than_the_running_one_is_refused(models):
    agent, world, fake, runner = make_agent(models)
    status, body = add(agent, rig="accustomed")
    assert (status, body["code"]) == (409, "changed"), body
    assert runner.calls == []


def test_an_amp_added_waits_for_unsaved_work_unless_it_is_dropped(models):
    """The restart that plays the amp reloads the file, so live work no save
    has written would go with it: refused `unsaved`, nothing written, until
    `discard`."""
    agent, world, fake, runner = make_agent(models)
    path = rig_file(models, "guitar_vocal")
    before = path.read_bytes()
    call(agent, "POST", "/live/parameter", json={"processor": "vocal_eq", "parameter": "High", "value": 0.987})
    status, body = add(agent)
    assert (status, body["code"]) == (409, "unsaved"), body
    assert "adding the amp" in body["reason"] and path.read_bytes() == before and runner.calls == []
    status, body = add(agent, discard=True)
    assert status == 200 and body["discarded"] is True, body
    assert yaml.safe_load(path.read_text())["amp"]["model"] == f"amp/models/{MODEL}"


def test_a_rig_run_with_its_panel_is_refused_an_amp(models):
    agent, world, fake, runner = make_agent(models)
    world.state["mode"] = "panel"
    status, body = add(agent)
    assert (status, body["code"]) == (409, "panel"), body


# --------------------------------------------------------------------------- #
# A channel loaded with its rig's amp
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("asked", [True, False])
def test_a_load_with_amp_copies_the_source_rigs_amp_and_restarts_with_it(root, asked):
    """accustomed's board onto guitar_vocal's guitar, with accustomed's
    `amp:` (model and parameters) in the same write; the switch is a restart
    that brings the amp, even for a run started with --no-amp."""
    agent, world, fake, runner = make_agent(root)
    world.state["amp_requested"] = asked
    source = yaml.safe_load(rig_file(root, "accustomed").read_text())["amp"]
    status, body = load(agent, "guitar", "accustomed", "board", with_amp=True)
    assert status == 200, body
    written = yaml.safe_load(rig_file(root, "guitar_vocal").read_text())
    assert written["amp"] == source
    assert list(written).index("amp") == list(written).index("channels") - 1
    assert [p["name"] for p in written["channels"][0]["chain"]][0] == "guitar_compressor", "the load's own work"
    assert body["amp_copied"] == {"from": "accustomed", "model": source["model"], "parameters": source["parameters"]}
    assert body["amp"]["in_front"] is True and body["amp"]["note"] is None, "heard through the amp it was"
    assert body["how"] == "restart" and "amp" in [w["code"] for w in body["why"]], body
    assert "--no-amp" not in up_call(runner)
    assert body["entry"]["args"]["load"]["with_amp"] is True
    status, state = call(agent, "GET", "/undo")
    assert state["next"]["label"] == "load accustomed / board with amp", state


def test_a_load_without_amp_stays_as_it_was(root):
    agent, world, fake, runner = make_agent(root)
    status, body = load(agent, "guitar", "accustomed", "board")
    assert status == 200 and body["amp_copied"] is None, body
    assert "amp" not in yaml.safe_load(rig_file(root, "guitar_vocal").read_text())


def test_a_load_with_amp_from_a_rig_with_none_or_onto_one_with_one_is_refused(root):
    agent, world, fake, runner = make_agent(root)
    before = rig_file(root, "guitar_vocal").read_bytes()
    status, body = load(agent, "vocal", "guitar_vocal", "guitar", with_amp=True)
    assert (status, body["code"]) == (409, "no_amp"), body
    assert rig_file(root, "guitar_vocal").read_bytes() == before
    agent, world, fake, runner = make_agent(root, rig="accustomed")
    before = rig_file(root, "accustomed").read_bytes()
    status, body = load(agent, "board", "electric-clean", "board", with_amp=True)
    assert (status, body["code"]) == (409, "has_amp"), body
    assert rig_file(root, "accustomed").read_bytes() == before and runner.calls == []


def test_a_rigs_channels_say_whether_its_file_has_an_amp(root):
    agent, *_ = make_agent(root)
    assert call(agent, "GET", "/rigs/accustomed/channels")[1]["amp"] is True
    assert call(agent, "GET", "/rigs/guitar_vocal/channels")[1]["amp"] is False
