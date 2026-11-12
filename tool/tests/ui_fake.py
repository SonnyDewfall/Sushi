"""A rig for the web UI to be driven against (#31), and a way to serve it.

`UiRig` is a Sushi in the shape of `agent.Live`, holding a small rig in
memory: two channels and a master, as the compiler builds them (a send at the
end of each channel, the master's return first on the master). It changes as
it is asked, so a browser driving the agent sees its own changes come back on
the next poll. `Served` runs a real agent over it on a real port, for
Playwright; run this file to serve it by hand (`python tests/ui_fake.py`),
which is how the README's screenshots were taken.

What is observed and what is invented (README, principle 1: a fixture is a
captured artefact). Observed: every plugin parameter's name and range, read
here from the repository's captured plugin data -- the overdrive's from
`fixtures/probe_gxts9.json` (lilv on the real bundle), the delays' from
`fixtures/lv2/mda.lv2/Delay.ttl` and the octave's from
`fixtures/lv2/rubberband.lv2/lv2-rubberband.ttl`, each port's `lv2:name`,
minimum, maximum and default. A track's gain, pan and mute and a send's gain,
-120 to +24 dB, are as rigfile.py records them from Sushi 1.3.0's source.
Invented: the rig itself (which plugins, in which order, which are bypassed),
the starting values away from the defaults, and two things this fixture
cannot know of Sushi: that it lists an output control port (the octave's
`latency`) as a read-only parameter under its `lv2:name`, which matches the
shape of `test_agent.py`'s "Latency OUT" but is not captured; and that the
session names an LV2 plugin's URI in `path`, which `live.read_live_plugins`
assumes and nobody has seen.

Adding and removing plugins (#32): `UiRig` takes the agent's add and remove
as `agent.Live` sends them, a plugin and its copy to stereo together, and an
added plugin starts at its captured defaults (`PORTS`: the overdrive, delay
and octave; any other comes in with no parameters). `Served(library=
fixture_library)` gives the agent a real library to add from, the
repository's manifest and port table over the captured bundles. Invented:
that a processor added live starts at its port defaults and un-bypassed,
which is what the agent assumes of Sushi and nobody has seen.

Start, stop and switch (#111), `UiLifecycle`: the agent's own routes, lock
and busy, over a stand-in for what they run. Invented: the whole of what a
lifecycle step does (it flips the rig up or down and records the call), and
the answer of a switch (`switch_held`), shaped as `Agent._switch_held`
builds one -- `how`, `why` as `RestartReason.as_dict` gives it, `seconds` --
but decided by the test, not by `rigfile.diff`. GET /rig/load answers with
`fixtures/pw-top-b-n2.txt`, which is test_top.py's sample of `pw-top -b -n 2`
(said there to be real output, trimmed) copied as it is.

The amp, `UiAmp` (`Served(amp=True)`): the agent's real amp routes over a
`carla_fake.FakeCarla`, a state file and compiled config held in memory, and
a stand-in for the supervisor's Carla-only restart. Invented: the models
(`.nam` files written to a temporary amp/models with made-up names and no
weights, two at the rig's rate and one not), the starting levels, and what a
restart does (it records the call and says the new model plays). Nothing
here is Carla.

Saving, `UiSave` (`Served(save=True)`): the agent's real POST /rig/save over
a checkout in a temporary folder -- `config/rigs/electric_board.yaml`
(`RIG_FILE`, `UiRig`'s rig written as a rig file, with a comment to keep),
the repository's profiles linked in, and a git repository with that file
committed -- and the state file held in memory. Invented: the rig file (it
is `UiRig`'s invention again), the state file, and what compiling the saved
rig gives (a stand-in that records the name: the real compile needs a
machine file this fixture does not have).

Recording, `UiRecorder` (`Served(record=True)`, which brings `UiSave`'s
checkout with it): the agent's real /record and /takes routes over the
recorder's real `RecorderCore`, reached in this process (`CoreLink`, through
`recorder_process.parse_request`, so a request the socket would refuse is
refused here too), planning each take with `transport.record_session` from
real files: `RIG_FILE`, the config `emit_compiled` makes of it (what `up
--rig` would load), and a machine file (`MACHINE_FILE`). The takes are real
folders, real stems and real take.yaml in a temporary folder. Invented: the
machine file's port names, the engine (`Pushing`: forty blocks of a level per
column, pushed at RECORD, so every take is 0.21 s however long it ran), and
the clock the recorder times a take by (`FakeClock`, which a test moves), so
the elapsed time is the test's to set. Nothing here is JACK or PipeWire.

The New rig screen and the rack's channel edits (`Served(save=True)` again):
the agent's real rig files routes over `UiSave`'s checkout, which has a
machine file (`MACHINE_FILE`, invented ports) that the state file names, as
`up --rig` records the one it compiled against, and a running config that is
the real compile of `RIG_FILE` (`compile_rig_draft`, what `up --rig` would
load), which a live switch and an edit's restart reasons diff against.
Invented: what a restart does (`UiLifecycle`: a new start time, the running
config compiled again from the file, and a channel the file gained since
coming up as an empty track with its send on `UiRig`, which is how the
compiler builds one; plugins a copied chain brings are not put on it).
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import json
import threading
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sushi_rig import midi_listener
from sushi_rig.agent import Agent, Switching
from sushi_rig.supervisor import AmpRestart
from sushi_rig.library import IRI, Literal, parse_turtle
from sushi_rig.live import LiveError, decide_move
from sushi_rig.paths import checkout_root
from sushi_rig.rigfile import LEVEL_RANGE, RETURN_UID, SEND_UID, level_to_gain
from sushi_rig.stereo import COPY_UID
from sushi_rig.supervisor import Outcome

FIXTURES = Path(__file__).parent / "fixtures"
LV2 = "http://lv2plug.in/ns/lv2core#"
RDF_TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"

GXTS9 = "http://guitarix.sourceforge.net/plugins/gxts9#ts9sim"
MDA_DELAY = "http://drobilla.net/plugins/mda/Delay"
RUBBERBAND = "http://breakfastquay.com/rdf/lv2-rubberband#mono"


def ttl_ports(path: Path, plugin: str) -> list[dict]:
    """A plugin's control ports from its Turtle, in index order:
    `{"name", "min", "max", "default", "output"}`."""
    triples = parse_turtle(path.read_text(encoding="utf-8"), path.resolve().as_uri())
    by_subject: dict = {}
    for s, p, o in triples:
        by_subject.setdefault(s, []).append((p, o))
    ports = []
    for p, port in by_subject.get(IRI(plugin), []):
        if p != LV2 + "port":
            continue
        facts = by_subject.get(port, [])
        kinds = {o for q, o in facts if q == RDF_TYPE}
        if IRI(LV2 + "ControlPort") not in kinds:
            continue
        value = {q.rsplit("#", 1)[-1]: o for q, o in facts if isinstance(o, Literal)}
        number = lambda key, default: float(value[key]) if key in value else default  # noqa: E731
        ports.append({
            "index": int(value["index"]), "name": str(value["name"]),
            "min": number("minimum", 0.0), "max": number("maximum", 1.0),
            "default": number("default", number("minimum", 0.0)),
            "output": IRI(LV2 + "OutputPort") in kinds,
        })
    assert ports, f"no control ports for {plugin} in {path}"
    return sorted(ports, key=lambda port: port["index"])


def probe_ports(path: Path) -> list[dict]:
    """`probe`'s parameters as the same shape (probe lists input ports only)."""
    data = json.loads(path.read_text(encoding="utf-8"))
    return [{"name": p["name"], "min": p["min"], "max": p["max"], "default": p["default"], "output": False}
            for p in data["parameters"]]


def info_of(ports: list[dict], values: dict[str, float] | None = None) -> dict[str, dict]:
    """Sushi's parameter info for those ports: each value normalised, at its
    default unless `values` gives a real one."""
    out = {}
    for port in ports:
        real = (values or {}).get(port["name"], port["default"])
        span = port["max"] - port["min"]
        out[port["name"]] = {
            "automatable": not port["output"],
            "value": (real - port["min"]) / span if span else 0.0,
            "min_domain_value": port["min"], "max_domain_value": port["max"],
        }
    return out


def strip(level_db: float = 0.0) -> dict[str, dict]:
    low, high = LEVEL_RANGE
    return {
        "gain": {"automatable": True, "value": level_to_gain(level_db), "min_domain_value": low, "max_domain_value": high},
        "pan": {"automatable": True, "value": 0.5, "min_domain_value": -1.0, "max_domain_value": 1.0},
        "mute": {"automatable": True, "value": 0.0, "min_domain_value": 0.0, "max_domain_value": 1.0},
    }


def send(level_db: float) -> dict[str, dict]:
    low, high = LEVEL_RANGE
    return {"gain": {"automatable": True, "value": level_to_gain(level_db), "min_domain_value": low, "max_domain_value": high}}


# The plugins an add can put on the fake rig, by URI: their control ports, as
# captured (see the module's docstring). Anything else comes in with none.
PORTS = {
    GXTS9: lambda: probe_ports(FIXTURES / "probe_gxts9.json"),
    MDA_DELAY: lambda: ttl_ports(FIXTURES / "lv2" / "mda.lv2" / "Delay.ttl", MDA_DELAY),
    RUBBERBAND: lambda: ttl_ports(FIXTURES / "lv2" / "rubberband.lv2" / "lv2-rubberband.ttl", RUBBERBAND),
}


def fixture_library():
    """The library the agent builds, over the repository's manifest and port
    table and the captured bundles in `fixtures/lv2` (`library.read_library`
    with that as its plugins folder), so an add from the page or a test can
    name a real URI: the delay and octave are read from their bundles, the
    rest fall back to their URI as on a machine without plugins/."""
    from sushi_rig.library import read_library

    return read_library(checkout_root(), plugins_dir=FIXTURES / "lv2")


class UiRig:
    """The rig, as `agent.Live` sees Sushi. `refuse` maps a processor (or a
    `(processor, parameter)`) to a reason Sushi gives for refusing a change
    to it, raised as `LiveError`, which the agent answers 502 with."""

    def __init__(self):
        gxts9 = probe_ports(FIXTURES / "probe_gxts9.json")
        delay = ttl_ports(FIXTURES / "lv2" / "mda.lv2" / "Delay.ttl", MDA_DELAY)
        octave = ttl_ports(FIXTURES / "lv2" / "rubberband.lv2" / "lv2-rubberband.ttl", RUBBERBAND)
        self.chains = {
            "board": ["overdrive", "octave", "delay", "board_send"],
            "vocal": ["vocal_delay", "vocal_send"],
            "master": ["master_return"],
        }
        self.info = {
            "board": strip(), "vocal": strip(), "master": strip(),
            "overdrive": info_of(gxts9, {"Drive": 0.62, "Tone": 480.0, "Level": -3.0}),
            "octave": info_of(octave, {"Octaves": -1.0}),
            "delay": info_of(delay, {"Feedback": 0.35, "FX Mix": 0.3}),
            "vocal_delay": info_of(delay),
            "board_send": send(0.0), "vocal_send": send(-6.0),
            "master_return": {},
        }
        self.bypassed = {"overdrive": False, "octave": True, "delay": False, "vocal_delay": False,
                         "board_send": False, "vocal_send": False, "master_return": False}
        self.classes = {
            "overdrive": {"uri": GXTS9, "uid": None, "type": "lv2"},
            "octave": {"uri": RUBBERBAND, "uid": None, "type": "lv2"},
            "delay": {"uri": MDA_DELAY, "uid": None, "type": "lv2"},
            "vocal_delay": {"uri": MDA_DELAY, "uid": None, "type": "lv2"},
            "board_send": {"uri": None, "uid": SEND_UID, "type": "internal"},
            "vocal_send": {"uri": None, "uid": SEND_UID, "type": "internal"},
            "master_return": {"uri": None, "uid": RETURN_UID, "type": "internal"},
        }
        self.refuse: dict = {}
        self.calls: list = []
        self.lock = threading.Lock()  # the agent calls from its worker threads
        self._next = 0

    # -- the shape of agent.Live --------------------------------------------
    def connect(self):
        with self.lock:
            self._next += 1
            return self._next

    def disconnect(self, connection):
        pass

    def _ids(self):
        names = list(self.chains) + [p for chain in self.chains.values() for p in chain]
        return {name: i for i, name in enumerate(names)}

    def _uids(self):
        return {name: c["uid"] for name, c in self.classes.items()}

    def snapshot(self, connection):
        with self.lock:
            self.calls.append(("snapshot",))
            processors = {}
            for name, params in self.info.items():
                entry = {"parameters": {n: p["value"] for n, p in params.items() if p["automatable"]}}
                if name not in self.chains:
                    entry["bypassed"] = self.bypassed.get(name)
                processors[name] = entry
            return {"processors": processors, "tracks": copy.deepcopy(self.chains),
                    "parameter_info": copy.deepcopy(self.info)}

    def parameter(self, connection, processor, parameter):
        with self.lock:
            params = self.info.get(processor)
            value = None
            if params is not None and parameter in params and params[parameter]["automatable"]:
                value = params[parameter]["value"]
            return {"chains": copy.deepcopy(self.chains), "value": value,
                    "parameters": None if params is None else {n: {"automatable": p["automatable"]} for n, p in params.items()}}

    def bypass(self, connection, processor):
        with self.lock:
            return {"chains": copy.deepcopy(self.chains), "bypassed": self.bypassed.get(processor)}

    def _refused(self, *keys):
        for key in keys:
            if key in self.refuse:
                raise LiveError(self.refuse[key])

    def set_parameter(self, connection, processor, parameter, value):
        with self.lock:
            self.calls.append(("set_parameter", processor, parameter, value))
            self._refused((processor, parameter), processor)
            self.info[processor][parameter]["value"] = value

    def set_bypass(self, connection, processor, bypassed):
        with self.lock:
            self.calls.append(("set_bypass", processor, bypassed))
            self._refused(processor)
            self.bypassed[processor] = bypassed

    def plan_move(self, connection, processor, direction):
        with self.lock:
            return decide_move(copy.deepcopy(self.chains), self._ids(), processor, direction, self._uids())

    def send_move(self, connection, move):
        with self.lock:
            self.calls.append(("move", move.processor, move.direction))
            chain = next(c for c in self.chains.values() if move.processor in c)
            i = chain.index(move.processor)
            chain.insert(i + move.direction, chain.pop(i))
            return replace(move, outcome="moved")

    def walk(self, connection, uids=False):
        with self.lock:
            reading = {"chains": copy.deepcopy(self.chains), "ids": self._ids()}
            if uids:
                reading["uids"] = self._uids()
            return reading

    def plugins(self, connection):
        with self.lock:
            self.calls.append(("plugins",))
            return {name: {"track": track, **self.classes[name]}
                    for track, chain in self.chains.items() for name in chain}

    # -- the chain edits (#32): an add puts the plugin in at its defaults, as
    # the captured ports give them; a remove takes it out, copy and all.
    def removal(self, connection, processor):
        with self.lock:
            on_a_chain = any(processor in chain for chain in self.chains.values())
            return {"chains": copy.deepcopy(self.chains), "ids": self._ids(), "uids": self._uids(),
                    "plugin": copy.deepcopy(self.classes.get(processor)) if on_a_chain else None}

    def add(self, connection, addition):
        with self.lock:
            self.calls.append(("add", addition.name, addition.uri, addition.track))
            self._refused(addition.name)
            ids = {i: name for name, i in self._ids().items()}
            chain = self.chains[addition.track]
            at = len(chain) if addition.add_to_back else chain.index(ids[addition.before_id])
            ports = PORTS.get(addition.uri)
            added = [(addition.name, info_of(ports()) if ports else {},
                      {"uri": addition.uri, "uid": None, "type": "lv2"})]
            if addition.copy:
                added.append((addition.copy, {}, {"uri": None, "uid": COPY_UID, "type": "internal"}))
            for offset, (name, info, plugin) in enumerate(added):
                chain.insert(at + offset, name)
                self.info[name], self.bypassed[name], self.classes[name] = info, False, plugin

    def remove(self, connection, removal):
        with self.lock:
            self.calls.append(("remove", removal.processor))
            self._refused(removal.processor)
            for name in ([removal.copy] if removal.copy else []) + [removal.processor]:
                self.chains[removal.track].remove(name)
                for held in (self.info, self.bypassed, self.classes):
                    held.pop(name, None)

    def add_track(self, name):
        """A channel the rig file gained, as a restart brings it up: a track
        with only its send, in front of the master, as the compiler builds one."""
        with self.lock:
            send_name = f"{name}_send"
            tracks = list(self.chains.items())
            self.chains = dict(tracks[:-1] + [(name, [send_name])] + tracks[-1:])
            self.info[name], self.info[send_name] = strip(), send(0.0)
            self.bypassed[send_name] = False
            self.classes[send_name] = {"uri": None, "uid": SEND_UID, "type": "internal"}

    def values(self, connection, processor):
        with self.lock:
            params = self.info[processor]
            return {"parameters": {n: p["value"] for n, p in params.items() if p["automatable"]},
                    "bypassed": self.bypassed.get(processor)}

    def writes(self):
        return [c for c in self.calls if c[0] in ("set_parameter", "set_bypass", "move", "add", "remove")]


def describe_up(started: datetime, name: str = "electric_board", amp_model: str | None = None,
                amp_alive: bool = True):
    """`describe_rig`'s answer for rig `name`: up since `started`, with a
    Carla when `amp_model` names what it plays."""
    def describe():
        return {
            "verdict": "running", "running": True, "config_name": name, "mode": "headless",
            "pgid": 4242, "started_at": started.isoformat(), "uptime_seconds": (datetime.now(timezone.utc) - started).total_seconds(),
            "amp_model": amp_model, "rig": name,
            "children": {"amp": {"pid": 4244, "alive": amp_alive}} if amp_model else {},
            "message": f"{name} (headless mode)",
        }
    return describe


def nam(path: Path, name: str, rate: int = 48000) -> None:
    """An invented `.nam`, as `amp.describe_model` reads one: no weights."""
    path.write_text(json.dumps({
        "architecture": "WaveNet", "sample_rate": rate, "config": {"layers": [{"channels": 8}]}, "weights": [],
        "metadata": {"name": name, "gear_make": "Invented", "gear_type": "amp"},
    }))


class UiAmp:
    """The amp a page sees through the agent's real routes: a state file and
    a compiled config in memory, invented models in a temporary amp/models,
    a `FakeCarla`, and the supervisor's part of a model change (`held`,
    `restart_amp`, `describe`). `restart_result`, when set, is what the next
    restart answers instead of success. `delay` holds a restart that long."""

    MODEL = "amp/models/Clean Room.nam"

    def __init__(self, lifecycle: "UiLifecycle"):
        import tempfile

        from carla_fake import FakeCarla

        self.lifecycle = lifecycle
        self._dir = tempfile.TemporaryDirectory(prefix="ui-amp-")
        self.root = Path(self._dir.name)
        models = self.root / "amp" / "models"
        models.mkdir(parents=True)
        # Each model calls itself something other than its file, as the rig's
        # do ("Neve 2.nam" says "Rupert Tape 1 Silk RED Tape rupert Tape 1"):
        # with the two the same, the rack titling a model by its metadata and
        # the stage by its file looked like one name (2026-11-02).
        nam(models / "Clean Room.nam", "Invented Twin Clean Room Mic")
        nam(models / "Edge.nam", "Invented Edge Drive 2")
        nam(models / "Slow Tape.nam", "Invented Slow Tape Deck", rate=44100)
        self.carla = FakeCarla()
        self.model = self.MODEL
        self.override = None
        self.config = {"tracks": [], "_amp": {"plugin": "nam", "model": self.MODEL,
                                              "parameters": {"Input Lvl": 0.0, "Output Lvl": -3.0, "Quality": 1.0}}}
        self.mode = "headless"
        self.alive = True
        self.held = type("Held", (), {"ready": True, "stopping": False})()
        self.restarts: list = []
        self.restart_result: AmpRestart | None = None
        self.delay = 0.0
        self.crashed: list = []
        self.problems: list = []

    def state(self):
        rig = self.lifecycle.rig
        return {
            "supervisor_pid": 4242, "pgid": 4242, "mode": self.mode, "config_name": rig,
            "config": f"/fake/{rig}.json", "started_at": self.lifecycle.started.isoformat(),
            "amp_model": self.model, "amp_requested": True, "amp_model_requested": self.override,
            "children": {"sushi": 4243, "amp": 4244},
        }

    def switching(self) -> Switching:
        return Switching(read_state=self.state, read_config=lambda path: copy.deepcopy(self.config))

    def describe(self):
        if self.lifecycle.rig is None:
            return self.lifecycle.describe()
        return describe_up(self.lifecycle.started, self.lifecycle.rig, self.model, self.alive)()

    async def restart_amp(self, project, values, *, model, override):
        self.restarts.append({"model": model, "override": override, "values": dict(values)})
        await asyncio.sleep(self.delay)
        if self.restart_result is not None:
            result, self.restart_result = self.restart_result, None
            return result
        self.model, self.override = model, override
        return AmpRestart(True, pid=5000 + len(self.restarts))

    def close(self):
        self._dir.cleanup()


# `UiRig`'s rig as a rig file of config/rigs, for `UiSave`. The values in it
# are the fake's starting values, so a save with nothing changed writes them
# back; the comment is one a save must keep.
RIG_FILE = f"""\
# The fake rig of ui_fake.UiRig, as a rig file.
kind: rig
schema_version: 1
name: electric_board
channels:
- name: board
  input: guitar-in
  width: mono
  chain:
  - name: overdrive   # the drive: a comment the save keeps
    uri: {GXTS9}
  - name: octave
    uri: {RUBBERBAND}
    bypassed: true
  - name: delay
    uri: {MDA_DELAY}
- name: vocal
  input: vocal-in
  width: mono
  level: -6.0
  chain:
  - name: vocal_delay
    uri: {MDA_DELAY}
master:
  output: monitor-out
  chain: []
"""


class UiSave:
    """What a save reaches beyond Sushi, for the agent's real POST /rig/save:
    a checkout in a temporary folder (`root`) with `RIG_FILE` committed in a
    git repository and the repository's profiles linked in, and the state
    file in memory (`state`), naming that file as `up --rig` does. A save-as
    writes the state file through `write_state`, which the lifecycle's rig
    follows. `compiled` records each rig compiled; `refuse_compile`, when
    set, is the reason the next compile fails. Headless, with no amp."""

    def __init__(self, lifecycle: "UiLifecycle", record: bool = False):
        import socket
        import subprocess
        import tempfile

        self.lifecycle = lifecycle
        self._dir = tempfile.TemporaryDirectory(prefix="ui-save-")
        self.root = Path(self._dir.name)
        rigs = self.root / "config" / "rigs"
        rigs.mkdir(parents=True)
        (rigs / "electric_board.yaml").write_text(RIG_FILE)
        profiles = self.root / "tool" / "examples" / "profiles"
        profiles.parent.mkdir(parents=True)
        profiles.symlink_to(checkout_root() / "tool" / "examples" / "profiles", target_is_directory=True)
        git = ["git", "-C", str(self.root), "-c", "user.name=ui fake", "-c", "user.email=ui@fake.invalid",
               "-c", "commit.gpgsign=false"]
        subprocess.run(git + ["init", "-q"], check=True)
        subprocess.run(git + ["add", "config"], check=True)
        subprocess.run(git + ["commit", "-q", "-m", "the fake rig"], check=True)
        self.commit = subprocess.run(git + ["rev-parse", "HEAD"], check=True, capture_output=True,
                                     text=True).stdout.strip()
        rig = lifecycle.rig or "electric_board"
        self.state = {
            "supervisor_pid": 4242, "pgid": 4242, "mode": "headless", "config_name": rig,
            "config": f"/fake/rigs/{rig}.json", "rig": f"config/rigs/{rig}.yaml",
            "started_at": lifecycle.started.isoformat(), "amp_requested": False, "amp_model": None,
            "children": {"sushi": 4243},
        }
        self.compiled: list = []
        self.refuse_compile: str | None = None
        # The machine file the run was compiled against, as the state file
        # records it, and the running config: the real compile of the file.
        machines = self.root / "machines"
        machines.mkdir(exist_ok=True)
        self.machine = machines / f"{socket.gethostname()}.yaml"
        self.machine.write_text(MACHINE_FILE)
        self.state["machine"] = str(self.machine)
        self.rig: "UiRig | None" = None  # `Served` hands over its rig, for a restart to add tracks to
        self.config = self.compile_running()
        lifecycle.on_restart = self.restarted
        # Recording (`UiRecorder`): a machine file and the config `up --rig`
        # compiles, at real paths the state file names, and a recorder.
        self.recorder = UiRecorder(self.root, self.state) if record else None

    def compile_running(self):
        """The running rig's config, as `up --rig` compiles its file now."""
        import yaml

        from sushi_rig.rig import compile_rig_draft

        path = self.root / self.state["rig"]
        return compile_rig_draft(yaml.safe_load(path.read_text()), self.root, Path(self.state["machine"]))

    def restarted(self):
        """A restart: the run starts again from its file (module docstring)."""
        import yaml

        self.state["started_at"] = self.lifecycle.started.isoformat()
        self.config = self.compile_running()
        raw = yaml.safe_load((self.root / self.state["rig"]).read_text())
        if self.rig is not None:
            for channel in raw["channels"]:
                if channel["name"] not in self.rig.chains:
                    self.rig.add_track(channel["name"])

    def write_state(self, state):
        self.state = copy.deepcopy(state)
        self.lifecycle.rig = state["config_name"]

    def compile(self, name, root, machine):
        self.compiled.append(name)
        if self.refuse_compile:
            raise RuntimeError(self.refuse_compile)
        return {"tracks": [], "_meta": {"source": f"config/rigs/{name}.yaml"}}

    def switching(self) -> Switching:
        return Switching(read_state=lambda: copy.deepcopy(self.state), write_state=self.write_state,
                         compile=self.compile, write_config=lambda name, compiled: f"/fake/rigs/{name}.json",
                         read_config=lambda path: copy.deepcopy(self.config))

    def describe(self):
        if self.lifecycle.rig is None:
            return self.lifecycle.describe()
        return {**describe_up(self.lifecycle.started, self.state["config_name"])(),
                "mode": self.state["mode"], "rig": self.state["rig"], "config": self.state["config"]}

    def close(self):
        self._dir.cleanup()


# The machine file the fake run was compiled against, and `UiRecorder` plans
# takes against: a role per input `RIG_FILE` plays from, one port each since
# both channels are mono, and for the New rig screen a third input that may
# feed a stereo channel (`mono_to_stereo`) and a pair. The port names are
# invented; guitar-in, with one port and no `mono_to_stereo`, is the role a
# stereo channel is refused on.
MACHINE_FILE = """\
schema_version: 1
roles:
  guitar-in: ["iface:capture_AUX0"]
  vocal-in: ["iface:capture_AUX1"]
  keys-in: {ports: ["iface:capture_AUX2"], mono_to_stereo: true}
  pair-in: ["iface:capture_AUX4", "iface:capture_AUX5"]
  monitor-out: ["iface:playback_FL", "iface:playback_FR"]
"""


class FakeClock:
    """A monotonic clock a test moves (`advance`), for the recorder's elapsed."""

    def __init__(self, now: float = 1000.0):
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class Pushing:
    """A stand-in capture engine: at `start`, pushes `blocks` blocks whose
    columns are the plan's, a different level on each, as the capture engine
    would from PipeWire's ports, and then nothing. `refuse`, when set, is the
    error the next start raises."""

    refuse: "Exception | None" = None

    def __init__(self, rate, quantum, blocks: int = 40):
        self.quantum = quantum
        self.blocks = blocks

    def start(self, stems, push):
        if Pushing.refuse is not None:
            error, Pushing.refuse = Pushing.refuse, None
            raise error
        import numpy as np

        columns = sum(n for _, n in stems)
        block = np.tile(np.arange(1, columns + 1, dtype=np.float32) / 20, (self.quantum, 1))
        for _ in range(self.blocks):
            push(block)

    def stop(self):
        return None


class CoreLink:
    """The agent's link to a `RecorderCore` in this process: each request
    goes through `recorder_process.parse_request` as the socket's would.
    `down`, when set, is why no recorder answers (`RecorderDown`)."""

    def __init__(self, core):
        self.core = core
        self.down: str | None = None
        self.asked: list = []

    def ask(self, request, timeout):
        from sushi_rig.recorder_process import RecorderDown, parse_request

        self.asked.append(request)
        if self.down:
            raise RecorderDown(self.down)
        return self.core.handle(parse_request(json.dumps(request).encode()))


class UiRecorder:
    """The recorder a page reaches through the agent (module docstring):
    writes a machine file and the compiled config into `root`, names both in
    `state` (UiSave's state file, in memory), and makes takes in
    `root/takes` through a real `RecorderCore` over `Pushing`."""

    def __init__(self, root: Path, state: dict):
        from sushi_rig.recorder_process import RecorderCore
        from sushi_rig.rigfile import compile_rig, emit_compiled, load_rig

        machines = root / "machines"
        machines.mkdir(exist_ok=True)
        (machines / "fake.yaml").write_text(MACHINE_FILE)
        rig = load_rig(root / "config" / "rigs" / "electric_board.yaml")
        compiled = emit_compiled(rig, *compile_rig(rig))
        compiled.setdefault("_meta", {})["source"] = "config/rigs/electric_board.yaml"
        run = root / "run"
        run.mkdir(exist_ok=True)
        (run / "electric_board.json").write_text(json.dumps(compiled, indent=2))
        state.update(config=str(run / "electric_board.json"), machine=str(machines / "fake.yaml"))
        state["children"] = {**state["children"], "recorder": 4245}
        self.takes = root / "takes"
        self.clock = FakeClock()
        self.core = RecorderCore(engine=Pushing, clock=self.clock, say=lambda message: None)
        self.link = CoreLink(self.core)


class UiLifecycle:
    """What `up`, `down` and a rig switch do, for the agent's real routes to
    run (see the module docstring for what is invented). `run` is the agent's
    runner: `up` brings rig `args[1]` up unless `refuse_up` says why not, and
    `down` takes it down. `switch_held` stands in the body of a switch, after
    the agent has taken its lock and set `busy`: `how` (rig name to "live"
    or ("restart", [why...])) decides each one, live by default. `delay`
    holds every command that long, so a page can be seen busy. Its methods
    run on the agent's loop."""

    def __init__(self, up: str | None = "electric_board"):
        self.rig = up
        self.started = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(minutes=12)
        self.refuse_up: str | None = None
        self.how: dict = {}
        self.delay = 0.0
        self.calls: list = []
        self.agent: Agent | None = None
        self.on_restart = None  # `UiSave.restarted`, when there is a rig file

    def describe(self):
        if self.rig is None:
            return {"verdict": "absent", "running": False, "message": "No rig is running."}
        return describe_up(self.started, self.rig)()

    async def run(self, args):
        self.calls.append(list(args))
        await asyncio.sleep(self.delay)
        if args[0] == "up":
            if self.refuse_up:
                return Outcome(False, 1, "", f"error: {self.refuse_up}\n")
            if self.rig is not None:
                return Outcome(False, 1, "", f"error: a rig is already running ({self.rig}); 'sushi-rig down' first\n")
            self.rig, self.started = args[1], datetime.now(timezone.utc).replace(microsecond=0)
            return Outcome(True, 0, f"{self.rig} up (headless mode)\n", "")
        if args[0] == "down":
            if self.rig is None:
                return Outcome(False, 1, "", "error: no rig is running\n")
            self.rig = None
            return Outcome(True, 0, "down\n", "")
        if args[0] == "restart":
            if self.rig is None:
                return Outcome(False, 1, "", "error: no rig to restart\n")
            self.started = datetime.now(timezone.utc).replace(microsecond=0)
            if self.on_restart:
                self.on_restart()
            return Outcome(True, 0, f"{self.rig} restarted (headless mode)\n", "")
        return Outcome(False, 2, "", f"error: the fake does not know {args[0]!r}\n")

    async def switch_held(self, origin, name, program, mark):
        self.calls.append(["switch", name])
        await asyncio.sleep(self.delay)
        previous = self.rig
        how = self.how.get(name, "live")
        answer = {"ok": True, "rig": name, "previous": previous, "seconds": 0.4 if how == "live" else 6.2}
        if how == "live":
            answer["how"] = "live"
            entry = self.agent._record("rig", {"rig": name, "how": "live"}, previous)
        else:
            _, why = how
            self.started = datetime.now(timezone.utc).replace(microsecond=0)
            answer.update(how="restart", why=why, command=["up", name, "--rig", "--headless"], output="")
            entry = self.agent._record("rig", {"rig": name, "how": "restart", "why": [w["code"] for w in why]}, previous)
        self.rig = name
        return {**answer, "entry": entry, "state": self.describe()}


class Served:
    """A real agent over `rig` on 127.0.0.1, in a thread of its own, until
    `close`. `url` is where it answers. `lifecycle` is what its lifecycle and
    switch routes run (`UiLifecycle`, a rig up by default); `describe`, when
    given, answers for the state file instead of it."""

    def __init__(self, rig: UiRig | None = None, port: int = 0, describe=None, root: Path | None = None,
                 lifecycle: UiLifecycle | None = None, amp: bool = False, save: bool = False,
                 record: bool = False, library=None):
        if amp and (save or record):
            raise ValueError("the fake has an amp or a rig file to save (and record), not both yet")
        self.rig = rig or UiRig()
        self.lifecycle = lifecycle or UiLifecycle()
        # What GET /rig/load's pw-top prints; a test may change it.
        self.pw_top_output = (FIXTURES / "pw-top-b-n2.txt").read_text(encoding="utf-8")
        # The amp (`UiAmp`), when asked for: the rig then has a Carla.
        self.amp = UiAmp(self.lifecycle) if amp else None
        # A rig file to save into (`UiSave`), when asked for.
        self.save = UiSave(self.lifecycle, record=record) if save or record else None
        if self.save:
            self.save.rig = self.rig
        # The recorder (`UiRecorder`), when asked for, beside the checkout.
        self.recorder = self.save.recorder if self.save else None
        own = self.amp.describe if self.amp else self.save.describe if self.save else self.lifecycle.describe
        self.state = {"describe": describe or own}
        extra = {}
        if self.amp:
            extra = {"switching": self.amp.switching(), "carla": self.amp.carla.carla()}
        if self.save:
            extra = {"switching": self.save.switching()}
            root = root or self.save.root
        if self.recorder:
            extra.update(recorder=self.recorder.link, takes_dir=self.recorder.takes)
        # `root` is where the profiles are read from (tool/examples/profiles/
        # under it) and where the picker's rigs are listed from (config/rigs/).
        # With none given it is a checkout of the frozen rigs (rig_fixtures.py)
        # and the checkout's own profiles: the browser tests click rigs by
        # name, and the names in config/rigs/ are the owner's to change.
        self._frozen = None
        if root is None:
            import tempfile

            from rig_fixtures import frozen_checkout

            self._frozen = tempfile.TemporaryDirectory(prefix="ui-rigs-")
            root = frozen_checkout(Path(self._frozen.name) / "checkout")
            profiles = root / "tool" / "examples" / "profiles"
            profiles.parent.mkdir(parents=True)
            profiles.symlink_to(checkout_root() / "tool" / "examples" / "profiles", target_is_directory=True)
        self.agent = Agent(root=root, live=self.rig, describe=lambda: self.state["describe"](),
                           library=library or (lambda: {"plugins": [], "problems": []}), run=self.lifecycle.run,
                           # No pedals: the MIDI listener follows a rig after every
                           # lifecycle command, and this rig has no compiled config.
                           midi=midi_listener.World(plan=lambda described, root: midi_listener.Off("the fake rig has no pedals")),
                           pw_top=lambda timeout: self.pw_top_output, **extra)
        if self.amp:
            # The amp's models are the invented ones; the profiles stay the checkout's.
            self.agent._amp_root = lambda: self.amp.root
            # The supervisor's part of a model change, stood in; /health's
            # account of what it holds stays the real (empty) one.
            self.agent._amp_held = lambda: self.amp.held.ready
            self.agent.supervisor.restart_amp = self.amp.restart_amp
        self.lifecycle.agent = self.agent
        self.agent._switch_held = self.lifecycle.switch_held
        self._loop = asyncio.new_event_loop()
        self._ready = threading.Event()
        self._port = port
        self._thread = threading.Thread(target=self._run, name="ui-agent", daemon=True)
        self._thread.start()
        if not self._ready.wait(10):
            raise RuntimeError("the agent did not start serving within 10 s")

    def _run(self):
        from aiohttp import web

        asyncio.set_event_loop(self._loop)

        async def start():
            self._runner = web.AppRunner(self.agent.app())
            await self._runner.setup()
            site = web.TCPSite(self._runner, "127.0.0.1", self._port)
            await site.start()
            self.url = "http://127.0.0.1:%d" % site._server.sockets[0].getsockname()[1]

        self._loop.run_until_complete(start())
        self._ready.set()
        self._loop.run_forever()

    def close(self):
        if self._loop.is_closed():
            return

        async def stop():
            await self._runner.cleanup()

        asyncio.run_coroutine_threadsafe(stop(), self._loop).result(10)
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(10)
        self._loop.close()
        self.agent._executor.shutdown(wait=True, cancel_futures=True)
        if self.amp:
            self.amp.close()
        if self.recorder:
            self.recorder.core.close()  # a take a test left running: its writer thread ends with it
        if self._frozen:
            self._frozen.cleanup()
        if self.save:
            self.save.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Serve the web UI against a rig held in memory.")
    parser.add_argument("--port", type=int, default=8029)
    parser.add_argument("--down", action="store_true", help="start with no rig up, to start one from the page")
    parser.add_argument("--amp", action="store_true", help="give the rig an amp, with invented models")
    parser.add_argument("--save", action="store_true", help="give the rig a rig file to save, in a temporary checkout")
    parser.add_argument("--record", action="store_true", help="and a recorder, whose takes go in that checkout")
    args = parser.parse_args()
    with Served(port=args.port, lifecycle=UiLifecycle(up=None if args.down else "electric_board"), amp=args.amp,
                save=args.save, record=args.record) as served:
        print(f"the web UI against a fake rig: {served.url}/  (Ctrl+C to stop)", flush=True)
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
