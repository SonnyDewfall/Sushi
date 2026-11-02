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

Start, stop and switch (#111), `UiLifecycle`: the agent's own routes, lock
and busy, over a stand-in for what they run. Invented: the whole of what a
lifecycle step does (it flips the rig up or down and records the call), and
the answer of a switch (`switch_held`), shaped as `Agent._switch_held`
builds one -- `how`, `why` as `RestartReason.as_dict` gives it, `seconds` --
but decided by the test, not by `rigfile.diff`. GET /rig/load answers with
`fixtures/pw-top-b-n2.txt`, which is test_top.py's sample of `pw-top -b -n 2`
(said there to be real output, trimmed) copied as it is.
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
from sushi_rig.agent import Agent
from sushi_rig.library import IRI, Literal, parse_turtle
from sushi_rig.live import LiveError, decide_move
from sushi_rig.paths import checkout_root
from sushi_rig.rigfile import LEVEL_RANGE, RETURN_UID, SEND_UID, level_to_gain
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

    def writes(self):
        return [c for c in self.calls if c[0] in ("set_parameter", "set_bypass", "move")]


def describe_up(started: datetime, name: str = "electric_board"):
    """`describe_rig`'s answer for rig `name`: up since `started`."""
    def describe():
        return {
            "verdict": "running", "running": True, "config_name": name, "mode": "headless",
            "started_at": started.isoformat(), "uptime_seconds": (datetime.now(timezone.utc) - started).total_seconds(),
            "amp_model": None, "rig": name, "children": {},
            "message": f"{name} (headless mode)",
        }
    return describe


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
                 lifecycle: UiLifecycle | None = None):
        self.rig = rig or UiRig()
        self.lifecycle = lifecycle or UiLifecycle()
        self.state = {"describe": describe or self.lifecycle.describe}
        # `root` is where the profiles are read from (tool/examples/profiles/ under it).
        self.agent = Agent(root=root or checkout_root(), live=self.rig, describe=lambda: self.state["describe"](),
                           library=lambda: {"plugins": [], "problems": []}, run=self.lifecycle.run,
                           # No pedals: the MIDI listener follows a rig after every
                           # lifecycle command, and this rig has no compiled config.
                           midi=midi_listener.World(plan=lambda described, root: midi_listener.Off("the fake rig has no pedals")),
                           pw_top=lambda timeout: (FIXTURES / "pw-top-b-n2.txt").read_text(encoding="utf-8"))
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

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Serve the web UI against a rig held in memory.")
    parser.add_argument("--port", type=int, default=8029)
    parser.add_argument("--down", action="store_true", help="start with no rig up, to start one from the page")
    args = parser.parse_args()
    with Served(port=args.port, lifecycle=UiLifecycle(up=None if args.down else "electric_board")) as served:
        print(f"the web UI against a fake rig: {served.url}/  (Ctrl+C to stop)", flush=True)
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
