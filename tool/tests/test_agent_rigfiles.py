"""The New rig screen's routes: GET /machine/roles, GET /rigs/{name}/channels,
POST /rigs/new, POST /rigs/{name}/channels/add and
POST /rigs/{name}/channels/{channel}/input (agent.py's rig files block,
rigedit.py's decisions).

Against real rig files in a temporary checkout -- copies of the committed
config/rigs and of the committed machine file, in a git repository -- and the
real compiler and patchbay, through `rig.compile_rig_draft`: every refusal
here is the compiler's own words, not a copy of its rules. The running rig
is a state file and `describe_rig`'s answer held in memory (`World`), its
compiled config the real compile of its file. Nothing here is Sushi: no rig
is started, and the running rig's chains are never read.

NOT shown: any of it against a live Sushi. That a rig written here comes up
as written is what `up --rig` would show; here it is shown to compile and
wire as `up --rig` compiles and wires, which is the same code.
"""

from __future__ import annotations

import asyncio
import copy
import difflib
import shutil
import socket
import subprocess

import pytest
import yaml

pytest.importorskip("aiohttp")
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from sushi_rig import midi_listener, rigedit  # noqa: E402
from sushi_rig.agent import Agent, Switching, rigfile_refusal, unsaved_entries  # noqa: E402
from sushi_rig.paths import checkout_root  # noqa: E402
from sushi_rig.rig import RigError, compile_named_rig, compile_rig_draft  # noqa: E402

RIGS = checkout_root() / "config" / "rigs"
MACHINE = checkout_root() / "machines" / "sonny-dewfall-XPS-9320.yaml"
# A role of one port that does not declare `mono_to_stereo`, and a pair:
# added to the copy of the committed machine file, never to it.
EXTRA_ROLES = """\
  solo-in: ["Audient iD14:capture_AUX2"]
  pair-in: ["Audient iD14:capture_AUX4", "Audient iD14:capture_AUX5"]
"""


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
                          check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def root(tmp_path):
    """A checkout: three committed rigs and the machine file, with two more
    roles, committed in a git repository. The machine file sits where this
    host's would (`machine_path`), for the routes run with no rig up."""
    rigs = tmp_path / "config" / "rigs"
    rigs.mkdir(parents=True)
    for name in ("guitar_vocal", "electric-chorus-2", "accustomed"):
        shutil.copy(RIGS / f"{name}.yaml", rigs / f"{name}.yaml")
    machines = tmp_path / "machines"
    machines.mkdir()
    text = MACHINE.read_text().replace("  monitor-out:", EXTRA_ROLES + "  monitor-out:", 1)
    assert "solo-in" in text
    (machines / f"{socket.gethostname()}.yaml").write_text(text)
    git(tmp_path, "init", "-q")
    git(tmp_path, "add", "config", "machines")
    git(tmp_path, "commit", "-q", "-m", "the rigs")
    return tmp_path


def machine_file(root):
    return root / "machines" / f"{socket.gethostname()}.yaml"


class World:
    """The state file and `describe_rig`'s answer: rig `rig` of config/rigs
    up headless (None: nothing up), compiled against the machine file. Its
    running config is the real compile of its file as it was at the start."""

    def __init__(self, root, rig="guitar_vocal", mode="headless"):
        self.root = root
        self.rig = rig
        self.state = {}
        if rig is not None:
            self.state = {
                "supervisor_pid": 10, "pgid": 10, "mode": mode, "config_name": rig,
                "config": f"/run/rigs/{rig}.json", "rig": f"config/rigs/{rig}.yaml",
                "started_at": "2026-11-06T10:00:00+00:00", "amp_requested": False, "amp_model": None,
                "machine": str(machine_file(root)), "children": {"sushi": 11},
            }
            self.config = compile_named_rig(rig, root, machine_file(root))

    def described(self):
        if self.rig is None:
            return {"verdict": "absent", "running": False, "message": "No rig is running."}
        return {"verdict": "running", "running": True, "config_name": self.rig, "mode": self.state["mode"],
                "rig": self.state["rig"], "config": self.state["config"], "children": {},
                "message": f"{self.rig} (headless mode), up 2m"}

    def switching(self):
        return Switching(read_state=lambda: copy.deepcopy(self.state),
                         read_config=lambda path: copy.deepcopy(self.config),
                         write_state=lambda state: pytest.fail("the rig files routes write no state file"),
                         write_config=lambda name, compiled: pytest.fail("nor a compiled config"),
                         compile=lambda name, root, machine: pytest.fail("nor compile through a switch"))


async def _never(args):
    raise AssertionError(f"no lifecycle command is run by a rig file edit: {args}")


def rig_agent(root, world=None):
    world = world if world is not None else World(root)
    agent = Agent(root=root, describe=world.described, switching=world.switching(), run=_never,
                  live=_NoSushi(), library=lambda: {"plugins": [], "problems": []},
                  midi=midi_listener.World(plan=lambda described, root: midi_listener.Off("no pedals here")))
    return agent, world


class _NoSushi:
    """The rig files routes never reach Sushi: any call is a failure."""

    def __getattr__(self, name):
        raise AssertionError(f"a rig file route reached Sushi ({name})")


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


def rig_text(root, name):
    return (root / "config" / "rigs" / f"{name}.yaml").read_text()


def rig_doc(root, name):
    return yaml.safe_load(rig_text(root, name))


def changed_lines(before, after):
    return [line for line in difflib.unified_diff(before.splitlines(), after.splitlines(), lineterm="", n=0)
            if line[:1] in "+-" and not line.startswith(("+++", "---"))]


def files_of(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*")
            if p.is_file() and ".git" not in p.parts}


def mono(name, role="guitar-in", **extra):
    return {"name": name, "input": role, "width": "mono", **extra}


def stereo(name, role="guitar-in", **extra):
    return {"name": name, "input": role, "width": "stereo", **extra}


# --------------------------------------------------------------------------- #
# The compiler's answer, and nothing beside it
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("name", ["guitar_vocal", "electric-chorus-2", "accustomed"])
def test_a_draft_compiles_to_exactly_what_up_rig_compiles_its_file_to(root, name):
    """One path: a rig not yet written is compiled as `up --rig` compiles it
    once it is, so what the routes accept is what starts."""
    raw = rig_doc(root, name)
    assert compile_rig_draft(raw, root, machine_file(root)) == compile_named_rig(name, root, machine_file(root))


def test_a_draft_is_wired_too_so_a_role_the_machine_lacks_is_refused_before_up_would():
    """`compile_named_rig` alone accepts a channel on a role the machine file
    does not have; the patchbay refuses it at `up`. The draft is refused now."""
    raw = rigedit.new_rig("t", [rigedit.channel_entry(mono("a", "nope-in"))], "monitor-out")
    with pytest.raises(RigError, match="role 'nope-in' feeds a track but the machine file does not define it"):
        compile_rig_draft(raw, checkout_root(), MACHINE)


def test_the_ceiling_is_the_compilers_refusal_of_one_more_channel():
    assert rigedit.ceiling() == {
        "channels": 3,
        "why": "4 channels need 10 outputs — 2 for the master and 2 for each channel's stem — and Sushi "
               "has 8, so a rig has at most 3 channels"}


# --------------------------------------------------------------------------- #
# GET /machine/roles
# --------------------------------------------------------------------------- #

def test_the_roles_are_the_machine_files_with_what_the_patchbay_says_each_can_feed(root):
    agent, _ = rig_agent(root)
    status, answer = call(agent, "GET", "/machine/roles")
    assert status == 200
    roles = {r["name"]: r for r in answer["roles"]}
    assert list(roles) == ["guitar-in", "vocal-in", "solo-in", "pair-in", "monitor-out"]
    guitar = roles["guitar-in"]
    assert (guitar["ports"], guitar["mono_to_stereo"], guitar["direction"]) == (
        ["Audient iD14:capture_AUX0"], True, "input")
    assert {w: v["ok"] for w, v in guitar["widths"].items()} == {"mono": True, "stereo": True}
    solo = roles["solo-in"]
    assert (solo["mono_to_stereo"], solo["widths"]["mono"]["ok"], solo["widths"]["stereo"]["ok"]) == (False, True, False)
    assert "declare it in the machine file: solo-in: {ports: ['Audient iD14:capture_AUX2'], " \
           "mono_to_stereo: true}" in solo["widths"]["stereo"]["why"]
    pair = roles["pair-in"]
    assert (pair["widths"]["mono"]["ok"], pair["widths"]["stereo"]["ok"]) == (False, True)
    monitors = roles["monitor-out"]
    assert (monitors["direction"], monitors["master"]["ok"]) == ("output", True)
    assert roles["guitar-in"]["master"]["ok"] is False, "one port cannot take a stereo mix"
    assert answer["output"] == "monitor-out"
    assert answer["ceiling"]["channels"] == 3
    assert answer["machine"] == f"machines/{socket.gethostname()}.yaml"


def test_the_roles_say_which_tracks_of_the_running_rig_use_each(root):
    agent, _ = rig_agent(root)
    _, answer = call(agent, "GET", "/machine/roles")
    used = {r["name"]: r["used_by"] for r in answer["roles"]}
    assert used == {"guitar-in": ["guitar"], "vocal-in": ["vocal"], "solo-in": [], "pair-in": [],
                    "monitor-out": ["master"]}
    assert answer["running"] == {"rig": "guitar_vocal", "file": "config/rigs/guitar_vocal.yaml"}
    agent, _ = rig_agent(root, World(root, rig=None))
    _, answer = call(agent, "GET", "/machine/roles")
    assert answer["running"] is None and all(r["used_by"] == [] for r in answer["roles"])


def test_a_machine_file_that_does_not_validate_is_said_not_listed_half(root):
    machine_file(root).write_text("schema_version: 1\nroles:\n  guitar-in: []\n")
    agent, _ = rig_agent(root, World(root, rig=None))
    status, answer = call(agent, "GET", "/machine/roles")
    assert (status, answer["code"]) == (409, "machine")
    assert "role 'guitar-in': has no ports" in answer["reason"]


# --------------------------------------------------------------------------- #
# GET /rigs/{name}/channels
# --------------------------------------------------------------------------- #

def test_a_rigs_channels_are_listed_as_its_file_has_them(root):
    agent, _ = rig_agent(root)
    status, answer = call(agent, "GET", "/rigs/guitar_vocal/channels")
    assert status == 200 and answer["running"] is True and answer["file"] == "config/rigs/guitar_vocal.yaml"
    assert [(c["name"], c["input"], c["width"], len(c["plugins"])) for c in answer["channels"]] == [
        ("guitar", "guitar-in", "mono", 6), ("vocal", "vocal-in", "mono", 5)]
    _, other = call(agent, "GET", "/rigs/accustomed/channels")
    assert other["running"] is False
    status, missing = call(agent, "GET", "/rigs/nope/channels")
    assert status == 404 and "no rig called 'nope'" in missing["reason"]


# --------------------------------------------------------------------------- #
# POST /rigs/new
# --------------------------------------------------------------------------- #

def test_a_new_rig_of_two_channels_on_one_input_is_written_and_compiles(root):
    """Two channels may share an input: one port fanned to two engine inputs."""
    agent, _ = rig_agent(root)
    machine_before = machine_file(root).read_bytes()
    status, answer = call(agent, "POST", "/rigs/new", json={
        "name": "two-guitars", "channels": [mono("clean"), stereo("dirty")]})
    assert status == 200, answer
    assert (answer["file"], answer["how"], answer["committed"], answer["offer"]) == (
        "config/rigs/two-guitars.yaml", "new", False, "switch")
    doc = rig_doc(root, "two-guitars")
    assert doc["channels"] == [
        {"name": "clean", "input": "guitar-in", "width": "mono", "chain": []},
        {"name": "dirty", "input": "guitar-in", "width": "stereo", "chain": []}]
    assert doc["master"] == {"output": "monitor-out", "chain": []}
    assert rig_text(root, "two-guitars").startswith("# Made on the web UI's New rig screen, ")
    compiled = compile_named_rig("two-guitars", root, machine_file(root))
    assert [t["name"] for t in compiled["tracks"]] == ["clean", "dirty", "master"]
    assert answer["library"]["file"] == "untracked" and answer["library"]["commit"] == git(root, "rev-parse", "HEAD")
    assert git(root, "status", "--porcelain") == "?? config/rigs/two-guitars.yaml", "nothing committed"
    assert machine_file(root).read_bytes() == machine_before, "the machine file is never written"
    _, rigs = call(agent, "GET", "/rigs")
    assert "two-guitars" in rigs["rigs"]


def test_with_no_rig_up_the_new_rig_is_offered_to_start_on_this_machines_file(root):
    agent, _ = rig_agent(root, World(root, rig=None))
    status, answer = call(agent, "POST", "/rigs/new", json={"name": "solo", "channels": [mono("guitar")]})
    assert (status, answer["offer"], answer["running"]) == (200, "start", False)


REFUSALS = [
    ("the ceiling", {"name": "four", "channels": [mono("a"), mono("b"), mono("c", "vocal-in"), mono("d", "vocal-in")]},
     "rig_problem", "so a rig has at most 3 channels"),
    ("an unknown role", {"name": "keys", "channels": [mono("keys", "keys-in")]},
     "rig_problem", "role 'keys-in' feeds a track but the machine file does not define it"),
    ("stereo on a one-port role without mono_to_stereo", {"name": "wide", "channels": [stereo("solo", "solo-in")]},
     "rig_problem", "solo-in: {ports: ['Audient iD14:capture_AUX2'], mono_to_stereo: true}"),
    ("mono on a pair", {"name": "narrow", "channels": [mono("pair", "pair-in")]},
     "rig_problem", "role 'pair-in' has 2 port(s) but this track routes it to 1 engine channel(s)"),
    ("a width that is not one", {"name": "odd", "channels": [{"name": "a", "input": "guitar-in", "width": "wide"}]},
     "rig_problem", "width must be one of mono, stereo (got 'wide')"),
    ("a duplicate channel name", {"name": "twice", "channels": [mono("a"), mono("a", "vocal-in")]},
     "rig_problem", "channel 'a' appears twice"),
    ("an invalid channel name", {"name": "caps", "channels": [mono("Lead Guitar")]},
     "rig_problem", "channel 'Lead Guitar': a name must match"),
    ("a channel name the compiler owns", {"name": "owned", "channels": [mono("master")]},
     "rig_problem", "a name the compiler gives the master track"),
    ("no channels", {"name": "empty", "channels": []}, "rig_problem", "channels must be a list of at least one"),
    ("an invalid rig name", {"name": "Two Guitars", "channels": [mono("a")]}, "name", "invalid config name"),
    ("a path for a name", {"name": "../escape", "channels": [mono("a")]}, "name", "invalid config name"),
    ("a rig that exists", {"name": "accustomed", "channels": [mono("a")]},
     "taken", "a rig called accustomed already exists (config/rigs/accustomed.yaml)"),
    ("a from naming no rig", {"name": "copy", "channels": [mono("a", **{"from": {"rig": "nope", "channel": "x"}})]},
     "no_source", "there is no rig called 'nope'"),
    ("a from naming no channel", {"name": "copy", "channels": [mono("a", **{"from": {"rig": "accustomed", "channel": "x"}})]},
     "no_source", "accustomed has no channel called x (it has: board)"),
    ("two copies of one chain", {"name": "copies", "channels": [
        mono("a", **{"from": {"rig": "accustomed", "channel": "board"}}),
        mono("b", **{"from": {"rig": "accustomed", "channel": "board"}})]},
     "rig_problem", "duplicate name"),
]


@pytest.mark.parametrize("what, body, code, said", REFUSALS, ids=[r[0] for r in REFUSALS])
def test_a_new_rig_the_compiler_refuses_is_409_with_its_reason_and_nothing_is_written(root, what, body, code, said):
    agent, _ = rig_agent(root)
    before = files_of(root)
    status, answer = call(agent, "POST", "/rigs/new", json=body)
    assert (status, answer["code"]) == (409, code), answer
    assert said in answer["reason"], answer["reason"]
    assert files_of(root) == before, "nothing written, the machine file least of all"
    assert agent.busy is None


def test_a_missing_role_is_said_with_what_to_add_to_the_machine_file(root):
    agent, _ = rig_agent(root)
    _, answer = call(agent, "POST", "/rigs/new", json={"name": "keys", "channels": [stereo("keys", "keys-in")]})
    hint = answer["machine_hint"]
    assert hint.startswith(f"machines/{socket.gethostname()}.yaml has no role keys-in. sushi-rig does not "
                           "edit the machine file")
    assert '  keys-in: ["<device>:capture_AUX0", "<device>:capture_AUX1"]   # left, then right' in hint
    assert "keys-in" not in machine_file(root).read_text()


@pytest.mark.parametrize("body, said", [
    (["a"], "must be a JSON object"),
    ({"name": "x"}, "'channels' must be a list"),
    ({"name": "x", "channels": [mono("a")], "commit": True}, "'commit' is not read here"),
    ({"name": 3, "channels": []}, "'name' must be text"),
    ({"name": "x", "channels": [{"name": "a", "input": "guitar-in"}]}, "'width' must be text"),
    ({"name": "x", "channels": [{**mono("a"), "level": 3}]}, "'level' is not read here"),
    ({"name": "x", "channels": [mono("a", **{"from": "accustomed"})]}, "from must be a JSON object"),
])
def test_a_body_of_the_wrong_shape_is_400_before_anything_is_read(root, body, said):
    agent, _ = rig_agent(root)
    before = files_of(root)
    status, answer = call(agent, "POST", "/rigs/new", json=body)
    assert status == 400 and said in answer["reason"], answer
    assert files_of(root) == before


def test_a_new_rig_can_start_a_channel_from_another_rigs_channel(root):
    agent, _ = rig_agent(root)
    source = rig_doc(root, "guitar_vocal")["channels"][1]
    status, answer = call(agent, "POST", "/rigs/new", json={"name": "vocal-only", "channels": [
        mono("voice", "vocal-in", **{"from": {"rig": "guitar_vocal", "channel": "vocal"}})]})
    assert status == 200, answer
    assert answer["channels"][0]["from"] == {"rig": "guitar_vocal", "channel": "vocal",
                                             "file": "config/rigs/guitar_vocal.yaml", "plugins": 5,
                                             "git": "unchanged"}
    channel = rig_doc(root, "vocal-only")["channels"][0]
    assert channel["chain"] == source["chain"]
    assert "# voice: its chain and level copied from config/rigs/guitar_vocal.yaml's vocal." in rig_text(root, "vocal-only")
    compile_named_rig("vocal-only", root, machine_file(root))


def test_a_copied_channel_brings_its_level(root):
    text = rig_text(root, "accustomed").replace("  width: mono\n", "  width: mono\n  level: -4.5\n", 1)
    (root / "config" / "rigs" / "accustomed.yaml").write_text(text)
    agent, _ = rig_agent(root)
    status, answer = call(agent, "POST", "/rigs/new", json={"name": "quieter", "channels": [
        mono("board", **{"from": {"rig": "accustomed", "channel": "board"}})]})
    assert status == 200, answer
    assert rig_doc(root, "quieter")["channels"][0]["level"] == -4.5
    assert answer["channels"][0]["from"]["git"] == "changed", "copied from the file as it stands, said as such"


def test_a_new_rig_is_refused_while_a_lifecycle_command_runs(root):
    agent, _ = rig_agent(root)
    before = files_of(root)

    async def go():
        async with TestClient(TestServer(agent.app())) as client:
            async with agent._lock:
                agent.busy = "restart"
                response = await client.post("/rigs/new", json={"name": "x", "channels": [mono("a")]})
                return response.status, await response.json()

    status, answer = asyncio.run(go())
    assert (status, answer["code"]) == (409, "busy") and files_of(root) == before


# --------------------------------------------------------------------------- #
# POST /rigs/{name}/channels/add and /channels/{channel}/input: minimal diffs
# --------------------------------------------------------------------------- #

COMMITTED = ["guitar_vocal", "electric-chorus-2", "accustomed"]


@pytest.mark.parametrize("rig", COMMITTED)
def test_a_channel_added_to_a_committed_rig_changes_only_its_own_lines(root, rig):
    agent, _ = rig_agent(root, World(root, rig=None))
    before = rig_text(root, rig)
    status, answer = call(agent, "POST", f"/rigs/{rig}/channels/add", json=stereo("keys", "vocal-in"))
    assert status == 200, answer
    changed = changed_lines(before, rig_text(root, rig))
    assert [line[0] for line in changed] == ["+"] * 4, changed
    assert [line[1:].strip().lstrip("- ") for line in changed] == [
        "name: keys", "input: vocal-in", "width: stereo", "chain: []"]
    assert (answer["how"], answer["takes_effect"], answer["running"]) == ("updated", "next start", False)
    assert answer["library"]["file"] == "changed"
    compile_named_rig(rig, root, machine_file(root))


@pytest.mark.parametrize("rig", COMMITTED)
def test_an_input_change_on_a_committed_rig_changes_only_that_line(root, rig):
    agent, _ = rig_agent(root, World(root, rig=None))
    first = rig_doc(root, rig)["channels"][0]["name"]
    before = rig_text(root, rig)
    status, answer = call(agent, "POST", f"/rigs/{rig}/channels/{first}/input", json={"input": "solo-in"})
    assert status == 200, answer
    changed = changed_lines(before, rig_text(root, rig))
    assert [line.split(":")[0].strip() for line in changed] == ["-    input" if rig == "guitar_vocal" else "-  input",
                                                                  "+    input" if rig == "guitar_vocal" else "+  input"]
    assert changed[1].endswith("input: solo-in")
    assert answer["change"] == {"op": "input", "channel": first, "was": {"input": "guitar-in", "width": "mono"},
                                "input": "solo-in"}
    # And the width with it: two lines, nothing else.
    before = rig_text(root, rig)
    status, answer = call(agent, "POST", f"/rigs/{rig}/channels/{first}/input",
                          json={"input": "vocal-in", "width": "stereo"})
    assert status == 200, answer
    assert [line.strip().lstrip("+-").strip() for line in changed_lines(before, rig_text(root, rig))] == [
        "input: solo-in", "width: mono", "input: vocal-in", "width: stereo"]


def test_a_channel_added_with_a_copied_chain_brings_its_entries_and_comments_stay(root):
    agent, _ = rig_agent(root, World(root, rig=None))
    before = rig_text(root, "guitar_vocal")
    status, answer = call(agent, "POST", "/rigs/guitar_vocal/channels/add", json=mono(
        "board", **{"from": {"rig": "accustomed", "channel": "board"}}))
    # accustomed's board has plugins named as guitar_vocal's guitar has none:
    # the compiler decides whether the names clash, and here they do not.
    assert status == 200, answer
    after = rig_text(root, "guitar_vocal")
    assert all(line in after for line in before.splitlines()), "every line of the file is kept, comments too"
    assert [p["name"] for p in rig_doc(root, "guitar_vocal")["channels"][2]["chain"]] == \
        [p["name"] for p in rig_doc(root, "accustomed")["channels"][0]["chain"]]
    compile_named_rig("guitar_vocal", root, machine_file(root))


@pytest.mark.parametrize("path, body, code, said", [
    ("/rigs/guitar_vocal/channels/add", mono("third", "solo-in"), None, None),
    ("/rigs/guitar_vocal/channels/add", stereo("keys", "solo-in"), "rig_problem", "mono_to_stereo: true"),
    ("/rigs/guitar_vocal/channels/add", mono("guitar", "vocal-in"), "rig_problem", "channel 'guitar' appears twice"),
    ("/rigs/guitar_vocal/channels/add", mono("keys", "keys-in"), "rig_problem", "does not define it"),
    ("/rigs/guitar_vocal/channels/vocal/input", {"width": "stereo", "input": "solo-in"}, "rig_problem", "mono_to_stereo"),
    ("/rigs/guitar_vocal/channels/vocal/input", {"input": "monitor-out"}, "rig_problem",
     "role 'monitor-out' has 2 port(s) but this track routes it to 1 engine channel(s)"),
    ("/rigs/guitar_vocal/channels/drums/input", {"input": "vocal-in"}, "no_channel",
     "guitar_vocal has no channel called drums (it has: guitar, vocal)"),
])
def test_an_edit_the_compiler_refuses_writes_nothing(root, path, body, code, said):
    agent, _ = rig_agent(root, World(root, rig=None))
    before = files_of(root)
    status, answer = call(agent, "POST", path, json=body)
    if code is None:
        assert status == 200, answer
        return
    assert (status, answer["code"]) == (409, code), answer
    assert said in answer["reason"], answer["reason"]
    assert files_of(root) == before


def test_the_ceiling_is_refused_on_an_add_with_the_compilers_arithmetic(root):
    agent, _ = rig_agent(root, World(root, rig=None))
    status, _ = call(agent, "POST", "/rigs/guitar_vocal/channels/add", json=mono("third", "vocal-in"))
    assert status == 200
    before = files_of(root)
    status, answer = call(agent, "POST", "/rigs/guitar_vocal/channels/add", json=mono("fourth", "vocal-in"))
    assert (status, answer["code"]) == (409, "rig_problem")
    assert "4 channels need 10 outputs" in answer["reason"] and files_of(root) == before


@pytest.mark.parametrize("path, body", [
    ("/rigs/nope/channels/add", mono("a")),
    ("/rigs/nope/channels/a/input", {"input": "guitar-in"}),
    ("/rigs/..%2Fescape/channels/add", mono("a")),
])
def test_a_rig_the_folder_does_not_list_is_404(root, path, body):
    agent, _ = rig_agent(root)
    status, answer = call(agent, "POST", path, json=body)
    assert status == 404 and "no rig called" in answer["reason"]


@pytest.mark.parametrize("body, said", [
    ({}, "names the 'input', the 'width' or both"),
    ({"input": "vocal-in", "level": 2}, "'level' is not read here"),
    ({"input": 1}, "'input' must be text"),
])
def test_an_input_change_of_the_wrong_shape_is_400(root, body, said):
    agent, _ = rig_agent(root)
    status, answer = call(agent, "POST", "/rigs/guitar_vocal/channels/vocal/input", json=body)
    assert status == 400 and said in answer["reason"]


def test_an_edit_is_refused_while_a_lifecycle_command_runs(root):
    agent, _ = rig_agent(root)
    before = files_of(root)

    async def go():
        async with TestClient(TestServer(agent.app())) as client:
            async with agent._lock:
                agent.busy = "up"
                out = []
                for path, body in [("/rigs/accustomed/channels/add", mono("keys", "vocal-in")),
                                   ("/rigs/accustomed/channels/board/input", {"input": "vocal-in"})]:
                    response = await client.post(path, json=body)
                    out.append((response.status, (await response.json())["code"]))
                return out

    assert asyncio.run(go()) == [(409, "busy"), (409, "busy")]
    assert files_of(root) == before


# --------------------------------------------------------------------------- #
# The running rig's own file
# --------------------------------------------------------------------------- #

def test_an_edit_of_the_running_rig_takes_effect_at_a_restart_and_says_why(root):
    agent, _ = rig_agent(root)
    status, answer = call(agent, "POST", "/rigs/guitar_vocal/channels/add", json=stereo("keys", "vocal-in"))
    assert status == 200, answer
    assert (answer["running"], answer["takes_effect"], answer["offer"]) == (True, "restart", "restart")
    assert [r["code"] for r in answer["why"]] == ["track_added"]
    assert "keeps playing as it was: the change takes effect when it restarts" in answer["note"]
    assert "track_added: track 'keys'" in answer["note"]
    status, answer = call(agent, "POST", "/rigs/guitar_vocal/channels/vocal/input", json={"input": "solo-in"})
    assert status == 200, answer
    assert {r["code"] for r in answer["why"]} >= {"wiring"}, answer["why"]
    assert agent.busy is None


def test_an_edit_of_another_rig_while_one_runs_says_it_plays_next_time(root):
    agent, _ = rig_agent(root)
    status, answer = call(agent, "POST", "/rigs/accustomed/channels/add", json=mono("keys", "vocal-in"))
    assert (status, answer["running"], answer["takes_effect"], answer["offer"]) == (200, False, "next start", None)
    assert "it plays as edited the next time it is started or switched to" in answer["note"]


def logged(op, args, at="2026-11-06T10:05:00+00:00"):
    return {"at": at, "op": op, "args": args, "previous": None}


def test_unsaved_live_work_on_the_running_rig_refuses_the_edit_and_writes_nothing(root):
    agent, _ = rig_agent(root)
    agent.log.append(logged("parameter", {"processor": "guitar_overdrive", "parameter": "Drive", "value": 0.4}))
    before = files_of(root)
    for path, body in [("/rigs/guitar_vocal/channels/add", mono("keys", "vocal-in")),
                       ("/rigs/guitar_vocal/channels/vocal/input", {"input": "solo-in"})]:
        status, answer = call(agent, "POST", path, json=body)
        assert (status, answer["code"]) == (409, "unsaved"), answer
        assert "1 change made through the agent and not saved (the newest: guitar_overdrive Drive" in answer["reason"]
        assert "Save first" in answer["reason"]
    assert files_of(root) == before
    # Another rig's file is not the running one: its edit strands nothing.
    status, _ = call(agent, "POST", "/rigs/accustomed/channels/add", json=mono("keys", "vocal-in"))
    assert status == 200
    # Once saved, the running rig's file can be edited.
    agent.log.append(logged("save", {"rig": "guitar_vocal"}, at="2026-11-06T10:06:00+00:00"))
    status, answer = call(agent, "POST", "/rigs/guitar_vocal/channels/add", json=mono("keys", "vocal-in"))
    assert status == 200, answer


def test_with_the_panel_running_an_edit_of_the_running_rig_is_refused(root):
    agent, _ = rig_agent(root, World(root, mode="panel"))
    before = files_of(root)
    status, answer = call(agent, "POST", "/rigs/guitar_vocal/channels/add", json=mono("keys", "vocal-in"))
    assert (status, answer["code"]) == (409, "panel") and "whose changes the agent cannot see" in answer["reason"]
    assert files_of(root) == before


def test_the_run_is_compiled_against_its_own_machine_file(root):
    """The state file's machine, not this host's: a rig started against
    another machine file is edited against that one."""
    other = root / "machines" / "stage.yaml"
    other.write_text(machine_file(root).read_text().replace("solo-in", "stage-in"))
    world = World(root)
    world.state["machine"] = str(other)
    agent, _ = rig_agent(root, world)
    status, answer = call(agent, "POST", "/rigs/guitar_vocal/channels/add", json=mono("keys", "solo-in"))
    assert (status, answer["code"]) == (409, "rig_problem")
    assert "role 'solo-in' feeds a track but the machine file does not define it" in answer["reason"]
    status, _ = call(agent, "POST", "/rigs/guitar_vocal/channels/add", json=mono("keys", "stage-in"))
    assert status == 200


# --------------------------------------------------------------------------- #
# The decisions, pure
# --------------------------------------------------------------------------- #

def test_unsaved_is_every_change_since_the_last_save_switch_or_start():
    log = [logged("parameter", {"processor": "a"}, at="2026-11-06T09:00:00+00:00"),
           logged("save", {}, at="2026-11-06T10:01:00+00:00"),
           logged("record", {}, at="2026-11-06T10:02:00+00:00"),
           logged("bypass", {"processor": "b"}, at="2026-11-06T10:03:00+00:00"),
           logged("undo", {"of": "bypass"}, at="2026-11-06T10:04:00+00:00")]
    assert [e["op"] for e in unsaved_entries(log, since="2026-11-06T10:00:00+00:00")] == ["bypass", "undo"], \
        "counted, not netted out: a Save answers it and loses nothing"
    assert unsaved_entries(log[:3]) == []
    assert unsaved_entries([logged("parameter", {})], since="2026-11-06T11:00:00+00:00") == [], \
        "before the run's start was another run"
    assert unsaved_entries(log + [logged("rig", {"rig": "x"})]) == []


def test_an_edit_waits_for_a_save_and_names_the_newest_change():
    state = {"rig": "config/rigs/board.yaml", "mode": "headless", "started_at": "2026-11-06T10:00:00+00:00"}
    assert rigfile_refusal(state, []) is None
    reason, code = rigfile_refusal(state, [logged("add", {"name": "slap", "track": "board"})])
    assert code == "unsaved" and "(the newest: slap added to board" in reason
    assert rigfile_refusal({**state, "mode": "panel"}, [])[1] == "panel"


def test_a_request_is_checked_for_its_shape_only_and_values_are_the_compilers():
    assert rigedit.channel_request({"name": "Lead", "input": "x", "width": "wide"}) == {
        "name": "Lead", "input": "x", "width": "wide", "from": None}
    assert rigedit.input_request({"width": "stereo"}) == {"width": "stereo"}
    with pytest.raises(rigedit.EditRequestError, match="from: 'channel' must be text"):
        rigedit.channel_request({"name": "a", "input": "x", "width": "mono", "from": {"rig": "r"}})


def test_a_role_is_an_input_or_an_output_by_its_name_or_either():
    assert [rigedit.direction_of(r) for r in ("guitar-in", "monitor-out", "aux")] == ["input", "output", "either"]


def test_a_save_after_an_edit_keeps_the_channel_the_running_rig_does_not_have_yet():
    """Why an edit of the running rig waits for a save, and why that is about
    the restart and not the file: a capture says nothing of a track the rig
    does not have, so the save that follows an edit keeps the new channel,
    copied chain and all. It is the restart the edit offers that would drop
    unsaved work."""
    from sushi_rig.rigfile import fold_state

    from ui_fake import MDA_DELAY, RIG_FILE

    raw = yaml.safe_load(RIG_FILE)
    capture = {"processors": {"overdrive": {"bypassed": False, "parameters": {"Drive": 0.5}}},
               "tracks": {"board": ["overdrive", "octave", "delay", "board_send"],
                          "vocal": ["vocal_delay", "vocal_send"], "master": ["master_return"]}}
    keys = {"name": "keys", "input": "keys-in", "width": "stereo",
            "chain": [{"name": "keys_delay", "uri": MDA_DELAY, "parameters": {"Feedback": 0.2}}]}
    folded = fold_state(rigedit.with_channel(raw, keys), capture)
    assert folded["channels"][2] == keys
    assert folded["channels"][0]["chain"][0]["parameters"] == {"Drive": 0.5}, "and the live change is saved"
