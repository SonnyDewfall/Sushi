"""The recorder the agent owns (S7): `sushi-rig recorder` and its socket.

Three layers. The request, pure (`parse_request`). The recorder's core, in this
process, planning each take with `transport.record_session` from real files
(a two-channel rig, the config `up --rig` compiles from it, a machine file)
and writing real takes, with only the engine stood in (`Pushing`). Then the
process itself: a real Python running `recorder_process.serve` in a process
group of its own, spoken to over its real Unix socket, with a stand-in engine
that pushes blocks from its own thread in real time as the capture engine
would; and the rig's supervisor starting the planned recorder child and
reporting its crash.

NOT run, and not claimed: the capture engine under `pw-jack` against a live
PipeWire. No take has been captured for real through this path
(recorder_process.py, transport.py). Everything a test starts is killed by
its recorded process group, never by pattern.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")

from sushi_rig import recorder_process  # noqa: E402
from sushi_rig.paths import checkout_root  # noqa: E402
from sushi_rig.recorder_process import (  # noqa: E402
    RecorderCore, RecorderDown, RecorderLink, RequestError, Run, ask, parse_request,
)
from sushi_rig.take import check_take, load_take  # noqa: E402

from test_record_session import MACHINE, TWO_CHANNELS, config_of, write  # noqa: E402
from ui_fake import FakeClock, Pushing  # noqa: E402
from sushi_rig.rigfile import load_rig  # noqa: E402

# The processes are started with subprocess's process_group, which is 3.11's.
pytestmark = pytest.mark.skipif(sys.version_info < (3, 11), reason="process_group needs Python 3.11")

SRC = checkout_root() / "tool" / "src"
TESTS = Path(__file__).parent


@pytest.fixture
def duo(tmp_path):
    """A two-channel rig of channels, the config `up --rig` would compile, a
    machine file, and where takes go: as the agent names them in a request."""
    rig_path = write(tmp_path / "config" / "rigs" / "duo.yaml", TWO_CHANNELS)
    machine_path = write(tmp_path / "machines" / "box.yaml", MACHINE)
    config = config_of(load_rig(rig_path))
    config.setdefault("_meta", {})["source"] = "config/rigs/duo.yaml"
    config["_amp"] = {"plugin": "nam", "model": "amp/models/Compiled.nam", "parameters": {"Input Lvl": 0.0}}
    config_path = write(tmp_path / "run" / "rigs" / "duo.json", json.dumps(config))
    return {"root": str(tmp_path), "rig": str(rig_path), "config": str(config_path),
            "machine": str(machine_path), "takes_dir": str(tmp_path / "takes")}


def core(clock=None):
    return RecorderCore(engine=Pushing, clock=clock or FakeClock(), say=lambda message: None,
                        now=lambda: datetime(2026, 11, 2, 21, 0, 0, tzinfo=timezone.utc))


def asked(recorder, request):
    """A request as the socket would hand it over: through `parse_request`."""
    return recorder.handle(parse_request(json.dumps(request).encode()))


AMP = {"model": "amp/models/Sent.nam", "parameters": {"Input Lvl": 2.5, "Output Lvl": -4.0}}


# --------------------------------------------------------------------------- #
# The request, pure
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("line, said", [
    (b"not json", "not JSON"),
    (b'{"op": "shell"}', "a request is"),
    (b'["status"]', "a request is"),
    (b'{"op": "status", "verbose": true}', "status does not take verbose"),
    (b'{"op": "start", "name": "x"}', "start needs the run's files"),
    (b'{"op": "start", "run": {}, "name": 3}', "start needs a take name"),
    (b'{"op": "status", "run": {"rig": "config/rigs/duo.yaml"}}', "run.rig must be an absolute path"),
    (b'{"op": "status", "run": {"shell": "/bin/sh"}}', "run must be a mapping of"),
    (b'{"op": "start", "run": {}, "name": "x", "amp": {"model": "a", "parameters": {"Input Lvl": "loud"}}}',
     "amp.parameters must map names to numbers"),
    (b'{"op": "stop", "by": ""}', "by is what stopped the take"),
])
def test_a_request_is_one_op_and_only_what_that_op_takes(line, said):
    with pytest.raises(RequestError, match=said):
        parse_request(line)


def test_a_run_is_read_back_as_absolute_paths_and_keyed_by_what_is_at_them(duo):
    run = parse_request(json.dumps({"op": "status", "run": duo}).encode())["run"]
    assert run == Run(**duo)
    before = run.key()
    Path(duo["rig"]).write_text(TWO_CHANNELS + "description: edited\n")
    assert run.key() != before, "a rig file rewritten (a SAVE) is a new plan"


# --------------------------------------------------------------------------- #
# The core: one take at a time, planned at its RECORD
# --------------------------------------------------------------------------- #

def test_idle_says_what_a_take_would_record_now(duo):
    answer = asked(core(), {"op": "status", "run": duo})
    assert answer["state"] == "idle"
    assert [(s["file"], s["port"], s["channels"]) for s in answer["stems"]] == [
        ("guitar.wav", "sushi:audio_output_2", 2), ("guitar-dry.wav", "iface:capture_AUX0", 1),
        ("keys.wav", "sushi:audio_output_4", 2), ("keys-dry.wav", "iface:capture_AUX2", 2),
        ("master.wav", "sushi:audio_output_0", 2),
    ]
    assert answer["takes_dir"] == duo["takes_dir"]


def test_a_run_no_take_can_be_planned_for_says_why_before_record(duo, tmp_path):
    v1 = write(tmp_path / "rig.yaml", "tracks: []\n")
    answer = asked(core(), {"op": "status", "run": {**duo, "rig": str(v1)}})
    assert answer["state"] == "unavailable"
    assert answer["reason"].startswith("recording is not available: no take can be planned for this run:")
    assert "rig.yaml of tracks" in answer["reason"]
    refused = asked(core(), {"op": "start", "name": "x", "run": {**duo, "rig": str(v1)}, "amp": None})
    assert refused["code"] == "unavailable" and refused["reason"] == answer["reason"]


def test_without_jack_client_the_reason_says_how_to_get_it(duo):
    """Found once, at the process's start (`main`), and said by every status
    without making an engine; a start that tries says the same."""
    from sushi_rig.capture import CaptureUnavailable

    problem = "JACK-Client is not installed (test): pip install -e \"tool[capture]\""

    def no_jack(rate, quantum):
        raise CaptureUnavailable([problem])

    recorder = RecorderCore(engine=no_jack, say=lambda m: None, engine_problem=problem)
    assert recorder.status(Run(**duo)) == {"ok": True, "state": "unavailable", "doing": None,
                                           "reason": f"recording is not available: {problem}"}
    refused = asked(recorder, {"op": "start", "name": "x", "run": duo, "amp": None})
    assert refused == {"ok": False, "code": "unavailable", "reason": f"recording is not available: {problem}"}


def test_a_status_makes_no_engine_and_one_engine_serves_every_take(duo):
    """A status plans from the files and builds nothing; the engine is made
    by the first start, once per rate and quantum, and kept."""
    made = []

    def engine(rate, quantum):
        made.append((rate, quantum))
        return Pushing(rate, quantum)

    recorder = RecorderCore(engine=engine, clock=FakeClock(), say=lambda m: None)
    for _ in range(3):
        assert asked(recorder, {"op": "status", "run": duo})["state"] == "idle"
    assert made == [], "a status built an engine"
    for name in ("one", "two"):
        asked(recorder, {"op": "start", "name": name, "run": duo, "amp": None})
        asked(recorder, {"op": "stop"})
    assert made == [(48000, 256)]


def test_a_take_from_start_to_stop_is_whole_and_says_how_long(duo):
    clock = FakeClock()
    recorder = core(clock)
    started = asked(recorder, {"op": "start", "name": "riff-idea", "run": duo, "amp": AMP})
    assert started["ok"] and started["state"] == "recording" and started["said"] == "recording riff-idea"
    assert started["replaced"] is False and started["started_at"] == "2026-11-02T21:00:00+00:00"
    clock.advance(107.25)
    now = asked(recorder, {"op": "status", "run": duo})
    assert (now["state"], now["name"], now["elapsed"]) == ("recording", "riff-idea", 107.25)
    stopped = asked(recorder, {"op": "stop"})
    assert stopped["ok"] and stopped["name"] == "riff-idea" and stopped["by"] == "request"
    assert stopped["problems"] == [] and stopped["complete"] is True
    assert stopped["seconds"] == round(40 * 256 / 48000, 3), "the stems' own length, not the clock's"
    assert check_take(load_take(Path(duo["takes_dir"]) / "riff-idea")) == []
    assert asked(recorder, {"op": "status", "run": duo})["state"] == "idle"


def test_record_while_recording_and_stop_while_stopped_are_refused(duo):
    recorder = core()
    assert asked(recorder, {"op": "stop"}) == {"ok": False, "code": "not_recording", "reason": "not recording"}
    assert asked(recorder, {"op": "start", "name": "one", "run": duo, "amp": None})["ok"]
    again = asked(recorder, {"op": "start", "name": "two", "run": duo, "amp": None})
    assert again == {"ok": False, "code": "recording", "reason": "already recording one: stop it first"}
    assert not (Path(duo["takes_dir"]) / "two").exists()
    assert asked(recorder, {"op": "stop"})["name"] == "one"


def test_a_name_the_take_rule_refuses_is_refused_here_too(duo):
    answer = asked(core(), {"op": "start", "name": "Riff Idea", "run": duo, "amp": None})
    assert answer["code"] == "name" and "invalid take name 'Riff Idea'" in answer["reason"]


def test_the_take_carries_the_amp_as_the_agent_sent_it_and_the_rig_file_as_it_is_now(duo):
    recorder = core()
    asked(recorder, {"op": "start", "name": "with-amp", "run": duo, "amp": AMP})
    asked(recorder, {"op": "stop"})
    folder = Path(duo["takes_dir"]) / "with-amp"
    config = json.loads((folder / "config.json").read_text())
    assert config["_amp"]["model"] == "amp/models/Sent.nam"
    assert config["_amp"]["parameters"]["Input Lvl"] == 2.5 and config["_amp"]["parameters"]["Output Lvl"] == -4.0
    assert (folder / "rig.yaml").read_text() == TWO_CHANNELS
    # A save rewrites the rig file while the rig runs: the next take has it.
    Path(duo["rig"]).write_text(TWO_CHANNELS + "description: saved since\n")
    asked(recorder, {"op": "start", "name": "after-save", "run": duo, "amp": AMP})
    asked(recorder, {"op": "stop"})
    assert "saved since" in (Path(duo["takes_dir"]) / "after-save" / "rig.yaml").read_text()


def test_no_amp_ran_is_no_amp_and_an_unknown_model_keeps_the_compiled_one(duo):
    recorder = core()
    asked(recorder, {"op": "start", "name": "no-amp", "run": duo, "amp": {"model": None, "parameters": {}}})
    asked(recorder, {"op": "stop"})
    asked(recorder, {"op": "start", "name": "unknown", "run": duo, "amp": None})
    asked(recorder, {"op": "stop"})
    takes = Path(duo["takes_dir"])
    assert "_amp" not in json.loads((takes / "no-amp" / "config.json").read_text())
    assert json.loads((takes / "unknown" / "config.json").read_text())["_amp"]["model"] == "amp/models/Compiled.nam"


def test_recording_over_a_name_keeps_the_earlier_take_and_says_where(duo):
    recorder = core()
    for _ in range(2):
        answer = asked(recorder, {"op": "start", "name": "riff", "run": duo, "amp": None})
        asked(recorder, {"op": "stop"})
    assert answer["replaced"] is True
    assert answer["said"] == "recording riff — the earlier riff is in _archive/riff/"
    assert len(list((Path(duo["takes_dir"]) / "_archive" / "riff").iterdir())) == 1


def test_a_take_follows_the_run_it_is_asked_for(duo, tmp_path):
    """A live switch rewrites the state file to another rig's config while the
    recorder runs: each request names the run, so the next take is that rig's."""
    solo = write(tmp_path / "config" / "rigs" / "solo.yaml", (
        "kind: rig\nschema_version: 1\nname: solo\nchannels:\n"
        "  - name: guitar\n    input: guitar-in\n    width: mono\nmaster:\n  output: monitor-out\n"))
    solo_config = write(tmp_path / "run" / "rigs" / "solo.json", json.dumps(config_of(load_rig(solo))))
    recorder = core()
    run = {**duo, "rig": str(solo), "config": str(solo_config)}
    asked(recorder, {"op": "start", "name": "solo-take", "run": run, "amp": None})
    stopped = asked(recorder, {"op": "stop"})
    assert [s["file"] for s in stopped["stems"]] == ["guitar.wav", "guitar-dry.wav", "master.wav"]


def test_an_engine_that_will_not_start_is_said_and_the_folder_says_why(duo):
    recorder = core()
    Pushing.refuse = RuntimeError("no such port (test)")
    answer = asked(recorder, {"op": "start", "name": "refused", "run": duo, "amp": None})
    assert answer["code"] == "failed"
    assert answer["reason"] == ("record failed: cannot record refused: the capture engine did not start: "
                                "no such port (test)")
    assert asked(recorder, {"op": "status", "run": duo})["state"] == "idle"
    problems = check_take(load_take(Path(duo["takes_dir"]) / "refused"))
    assert any("capture engine did not start" in p for p in problems)


def test_a_status_never_waits_for_a_stop(duo):
    """A stop drains the writer; a page polling meanwhile is told what is
    being done, at once, not made to wait behind it."""
    import threading

    recorder = core()
    asked(recorder, {"op": "start", "name": "slow", "run": duo, "amp": None})
    gate, entered = threading.Event(), threading.Event()
    session = recorder._take.session
    real_stop = session.stop

    def slow_stop():
        entered.set()
        gate.wait(10)
        return real_stop()

    session.stop = slow_stop
    stopping = threading.Thread(target=lambda: asked(recorder, {"op": "stop"}))
    stopping.start()
    try:
        assert entered.wait(5)
        status = asked(recorder, {"op": "status"})
        assert (status["state"], status["doing"]) == ("idle", "stopping")
    finally:
        gate.set()
        stopping.join(10)


def test_an_unexpected_error_is_an_answer_not_the_end_of_the_recorder(duo, monkeypatch):
    recorder = core()

    def broken(*a, **k):
        raise KeyError("boom")

    recorder._session_for = broken
    answer = asked(recorder, {"op": "start", "name": "x", "run": duo, "amp": None})
    assert answer == {"ok": False, "code": "failed", "reason": "the recorder failed unexpectedly: KeyError: 'boom'"}


def test_close_finishes_a_running_take(duo):
    recorder = core()
    assert recorder.close() is None
    asked(recorder, {"op": "start", "name": "cut", "run": duo, "amp": None})
    finished = recorder.close()
    assert finished["name"] == "cut" and finished["by"] == "the recorder stopping" and finished["complete"]


# --------------------------------------------------------------------------- #
# The child the rig plans
# --------------------------------------------------------------------------- #

def test_a_headless_rig_of_channels_plans_a_recorder_under_pw_jack_in_the_listeners_place(tmp_path):
    from sushi_rig.rig import QUANTUM, RATE, plan_children

    socket_path = tmp_path / "run" / "recorder.sock"
    plan = plan_children(tmp_path, tmp_path / "run" / "duo.json", "config/rigs/duo.yaml", headless=True,
                         tuner=False, recorder_socket=socket_path)
    names = [c["name"] for c in plan]
    assert names == ["qpwgraph", "recorder", "sushi"]
    recorder = plan[1]
    assert recorder["command"][0] == "pw-jack" and recorder["command"][2:] == ["recorder", "--socket", str(socket_path)]
    assert recorder["env_set"] == {"PIPEWIRE_QUANTUM": f"{QUANTUM}/{RATE}"}, "the quantum Sushi runs at"
    assert recorder["optional"] is True, "a rig that cannot record still plays"
    # In panel mode the listener records, and there is no recorder.
    panel = plan_children(tmp_path, tmp_path / "run" / "duo.json", "config/rigs/duo.yaml", headless=False,
                          tuner=False, recorder_socket=socket_path)
    assert "recorder" not in [c["name"] for c in panel] and "listen" in [c["name"] for c in panel]
    assert "recorder" not in [c["name"] for c in plan_children(
        tmp_path, tmp_path / "run" / "duo.json", None, headless=True, tuner=False)]


def test_up_plans_a_recorder_for_a_headless_rig_of_channels_only(monkeypatch, tmp_path, runtime):
    from sushi_rig import rig

    seen = []
    monkeypatch.setattr(rig, "plan_children", lambda *a, **k: seen.append(k.get("recorder_socket")) or [])
    monkeypatch.setattr(rig, "port_is_open", lambda *a, **k: False)
    monkeypatch.setattr(rig, "prepare_rig", lambda *a, **k: {
        "config": tmp_path / "c.json", "patchbay": None, "source": "config/rigs/x.yaml", "warnings": [],
        "machine": None})
    monkeypatch.setattr(rig, "resolve_rig_yaml", lambda *a, **k: "config/rigs/x.yaml")
    rig.prepare_launch("x", headless=True, rig=True, amp=False, tuner=False)
    rig.prepare_launch("x", headless=False, rig=True, amp=False, tuner=False)
    assert seen == [runtime / "recorder.sock", None]


# --------------------------------------------------------------------------- #
# The process, for real, over its socket
# --------------------------------------------------------------------------- #

# The recorder's own `serve`, in a Python of its own, with an engine that
# pushes a block a quantum from its own thread until stopped, as the capture
# engine does from PipeWire's.
PROCESS = """
import sys, threading, time
import numpy as np
from sushi_rig.recorder_process import RecorderCore, serve

class Engine:
    def __init__(self, rate, quantum):
        self.rate, self.quantum = rate, quantum
        self.running = threading.Event()
        self.thread = None

    def start(self, stems, push):
        columns = sum(n for _, n in stems)
        block = np.tile(np.arange(1, columns + 1, dtype=np.float32) / 20, (self.quantum, 1))
        self.running.set()
        def run():
            while self.running.is_set():
                push(block)
                time.sleep(self.quantum / self.rate)
        self.thread = threading.Thread(target=run)
        self.thread.start()

    def stop(self):
        self.running.clear()
        self.thread.join()
        return None

sys.exit(serve(sys.argv[1], RecorderCore(engine=Engine)))
"""


@pytest.fixture
def socket_dir():
    # Short: a Unix socket's path is at most 107 bytes, and pytest's tmp_path
    # can be longer than that.
    with tempfile.TemporaryDirectory(prefix="rec-", dir="/tmp") as folder:
        yield Path(folder)


class Process:
    """A recorder process in a group of its own, killed by that group."""

    def __init__(self, socket_path: Path, log: Path):
        env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(SRC), str(TESTS)])}
        self.log = log
        with open(log, "wb") as sink:
            self.proc = subprocess.Popen([sys.executable, "-c", PROCESS, str(socket_path)], env=env,
                                         stdin=subprocess.DEVNULL, stdout=sink, stderr=sink, process_group=0)
        self.link = RecorderLink(socket_path)

    def ready(self, seconds: float = 15.0) -> None:
        deadline = time.monotonic() + seconds
        while True:
            try:
                self.link.ask({"op": "status"}, 2.0)
                return
            except RecorderDown:
                if self.proc.poll() is not None or time.monotonic() > deadline:
                    raise AssertionError(f"the recorder did not come up: {self.log.read_text()}")
                time.sleep(0.05)

    def kill(self) -> None:
        try:
            os.killpg(self.proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass


@pytest.fixture
def process(socket_dir, tmp_path):
    started = []

    def start(path=None):
        made = Process(path or socket_dir / "recorder.sock", tmp_path / f"recorder-{len(started)}.log")
        started.append(made)
        return made

    yield start
    for made in started:
        made.kill()


def test_a_take_through_the_process_passes_take_check(duo, process, socket_dir):
    recorder = process()
    recorder.ready()
    assert oct(os.stat(socket_dir / "recorder.sock").st_mode & 0o777) == "0o600", "the user's alone"
    started = recorder.link.ask({"op": "start", "name": "riff-idea", "run": duo, "amp": AMP}, 20.0)
    assert started["ok"], started
    time.sleep(0.4)
    status = recorder.link.ask({"op": "status", "run": duo}, 3.0)
    assert status["state"] == "recording" and status["elapsed"] >= 0.3
    stopped = recorder.link.ask({"op": "stop"}, 20.0)
    assert stopped["ok"] and stopped["problems"] == [], stopped
    assert stopped["seconds"] >= 0.3
    folder = Path(duo["takes_dir"]) / "riff-idea"
    checked = subprocess.run([sys.executable, "-m", "sushi_rig.cli", "take", "check", str(folder)],
                             env={**os.environ, "PYTHONPATH": str(SRC)}, capture_output=True, text=True)
    assert checked.returncode == 0, checked.stdout + checked.stderr
    assert "riff-idea: ok, 5 stem(s)" in checked.stdout


def test_sigint_mid_take_finishes_the_take_and_removes_the_socket(duo, process, socket_dir):
    """What `down` does to the group: SIGINT first. The take ends whole."""
    recorder = process()
    recorder.ready()
    assert recorder.link.ask({"op": "start", "name": "cut-off", "run": duo, "amp": None}, 20.0)["ok"]
    time.sleep(0.3)
    os.killpg(recorder.proc.pid, signal.SIGINT)
    assert recorder.proc.wait(timeout=20) == 0
    take = load_take(Path(duo["takes_dir"]) / "cut-off")
    assert take.complete and check_take(take) == []
    assert not (socket_dir / "recorder.sock").exists()
    assert "finished cut-off on the way out" in recorder.log.read_text()


def test_a_second_recorder_on_the_socket_is_refused_and_a_dead_ones_is_replaced(process, socket_dir):
    first = process()
    first.ready()
    second = process()
    assert second.proc.wait(timeout=20) == 1
    assert "another recorder is already listening" in second.log.read_text()
    first.kill()
    assert (socket_dir / "recorder.sock").exists(), "a SIGKILL leaves the socket behind"
    with pytest.raises(RecorderDown):
        ask(socket_dir / "recorder.sock", {"op": "status"}, 1.0)
    third = process()
    third.ready()
    assert third.link.ask({"op": "status"}, 3.0)["state"] == "idle"


def test_a_request_the_process_cannot_read_is_answered_and_it_lives_on(process, socket_dir):
    import socket as sockets

    recorder = process()
    recorder.ready()
    conn = sockets.socket(sockets.AF_UNIX, sockets.SOCK_STREAM)
    conn.settimeout(5)
    conn.connect(str(socket_dir / "recorder.sock"))
    conn.sendall(b'{"op": "rm -rf"}\n')
    answer = json.loads(conn.makefile().readline())
    conn.close()
    assert answer["code"] == "bad_request" and "a request is" in answer["reason"]
    assert recorder.link.ask({"op": "status"}, 3.0)["state"] == "idle"


def test_a_socket_path_too_long_for_unix_is_said_not_cut(tmp_path):
    long = Path("/tmp") / ("x" * 120) / "recorder.sock"
    with pytest.raises(RecorderDown, match="longer than a Unix socket's 107 bytes"):
        ask(long, {"op": "status"}, 1.0)


def test_no_recorder_is_said_in_the_sockets_own_words(socket_dir):
    with pytest.raises(RecorderDown, match="no recorder is listening on"):
        RecorderLink(socket_dir / "recorder.sock").ask({"op": "status"}, 1.0)


def test_the_planned_recorder_crashing_is_a_crash_in_health_and_is_not_restarted(tmp_path, runtime):
    """The supervisor holds the recorder like any child: its exit while the
    rig is up is in `crashed`, with its name, and nothing starts it again."""
    import asyncio

    from sushi_rig import rig
    from sushi_rig.rig import Launch, plan_children
    from sushi_rig.supervisor import Supervisor, World, _spawn

    root = tmp_path / "checkout"
    (root / "config").mkdir(parents=True)
    config = root / "config" / "t.json"
    config.write_text("{}")
    plan = plan_children(root, config, "config/rigs/t.yaml", headless=True, tuner=False,
                         recorder_socket=runtime / "recorder.sock")
    started = []

    def spawn(command, *, cwd, env, log, process_group):
        if command[0] == sys.executable:
            real = command  # the group holder
        elif "recorder" in command:
            real = [sys.executable, "-c", "import sys, time; time.sleep(0.3); sys.exit(3)"]
        else:
            real = [sys.executable, "-c", "import time; time.sleep(60)"]
        proc = _spawn(real, cwd=cwd, env=env, log=log, process_group=process_group)
        started.append(proc)
        return proc

    launch = Launch(root=root, config_name="t", config=config, children=plan, headless=True, amp_model=None,
                    amp_values={}, tuner_requested=False, amp_requested=False, amp_model_requested=None,
                    rig_source="config/rigs/t.yaml", machine_file=None)
    world = World(prepare=lambda *a, **k: launch, spawn=spawn,
                  start_qpwgraph=lambda child, root, env, logs, process_group=None: spawn(
                      child["command"], cwd=root, env=env, log=logs / "qpwgraph.log", process_group=process_group),
                  sushi_ready=lambda alive: True, generate_panel=lambda *a: (0, ""),
                  link_problems=lambda *a: [], replay_amp=lambda values: None,
                  shutdown_steps=((signal.SIGINT, 1.0), (signal.SIGKILL, 1.0)), watch_interval=0.05)
    said = []
    supervisor = Supervisor(world, say=said.append)

    async def go():
        assert (await supervisor.up("t", headless=True, rig=True)).ok
        deadline = asyncio.get_running_loop().time() + 5
        while not supervisor.crashed:
            assert asyncio.get_running_loop().time() < deadline, "no crash seen"
            await asyncio.sleep(0.05)
        await asyncio.sleep(0.3)
        described = supervisor.describe(rig.describe_rig())
        await supervisor.down()
        return described

    try:
        described = asyncio.run(go())
    finally:
        for proc in started:
            try:
                os.killpg(proc.pid, signal.SIGKILL) if os.getpgid(proc.pid) == proc.pid else proc.kill()
            except (ProcessLookupError, PermissionError):
                pass
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
    [crash] = described["crashed"]
    assert (crash["name"], crash["returncode"]) == ("recorder", 3)
    assert described["holds"]["children"]["recorder"]["running"] is False
    assert any("recorder" in message and "Not restarted" in message for message in said)
    spawned_recorders = [p for p in started if p.args[-1].startswith("import sys, time; time.sleep(0.3)")]
    assert len(spawned_recorders) == 1, "started once, never again"


def test_a_take_that_cannot_be_read_back_still_says_it_stopped(duo, monkeypatch):
    """The take is over whatever reading it back does: the answer names it
    and says it stopped, with why it could not be checked."""
    from sushi_rig import take as take_module

    recorder = core()
    asked(recorder, {"op": "start", "name": "unread", "run": duo, "amp": None})

    def broken(folder):
        raise OSError("disk went away (test)")

    monkeypatch.setattr(take_module, "take_summary", broken)
    answer = asked(recorder, {"op": "stop"})
    assert (answer["ok"], answer["stopped"], answer["name"], answer["code"]) == (False, True, "unread", "failed")
    assert answer["summary_error"] == "OSError: disk went away (test)"
    assert answer["reason"] == "unread stopped, but could not be read back: OSError: disk went away (test)"
    assert "could not be read back to check it" in answer["problems"][0]
    assert asked(recorder, {"op": "status"})["state"] == "idle"
    monkeypatch.undo()
    assert check_take(load_take(Path(duo["takes_dir"]) / "unread")) == [], "and it was finished"


def test_a_recorder_whose_queue_is_full_is_busy_not_gone_and_its_socket_is_kept(socket_dir):
    """A socket that is listened on but not taking connections: its queue
    fills, and a connect then waits, or is refused with EAGAIN. Either is a
    recorder busy, not a crash's leftover, so `bind` must not take it over."""
    import socket as sockets

    path = socket_dir / "recorder.sock"
    listener = sockets.socket(sockets.AF_UNIX, sockets.SOCK_STREAM)
    listener.bind(str(path))
    listener.listen(0)
    queued = []
    try:
        for _ in range(8):  # fill the queue; never accepted
            conn = sockets.socket(sockets.AF_UNIX, sockets.SOCK_STREAM)
            conn.setblocking(False)
            try:
                conn.connect(str(path))
            except (BlockingIOError, InterruptedError):
                pass
            queued.append(conn)
        with pytest.raises(recorder_process.RecorderTimeout, match="it is busy"):
            ask(path, {"op": "status"}, 0.3)
        with pytest.raises(OSError, match="does not answer: not replacing it"):
            recorder_process.bind(path, core())
        assert path.exists(), "a busy recorder's socket is not unlinked"
    finally:
        for conn in queued:
            conn.close()
        listener.close()


def test_a_connect_refused_with_eagain_is_busy(monkeypatch, socket_dir):
    import socket as sockets

    def full(self, address):
        raise BlockingIOError(11, "Resource temporarily unavailable")

    monkeypatch.setattr(sockets.socket, "connect", full)
    with pytest.raises(recorder_process.RecorderTimeout):
        ask(socket_dir / "recorder.sock", {"op": "status"}, 1.0)


def test_the_server_keeps_no_record_of_requests_served(duo, socket_dir):
    """A recorder polled every second for hours: each request's thread is a
    daemon one the server does not keep (Python 3.10 kept every one when
    joined on close)."""
    import threading

    server = recorder_process.bind(socket_dir / "recorder.sock", core())
    server.timeout = 0.05
    stop = threading.Event()

    def loop():
        while not stop.is_set():
            server.handle_request()

    serving = threading.Thread(target=loop)
    serving.start()
    try:
        link = RecorderLink(socket_dir / "recorder.sock")
        for _ in range(30):
            assert link.ask({"op": "status", "run": duo}, 3.0)["state"] == "idle"
        assert server.daemon_threads is True and server.block_on_close is False
        kept = vars(server).get("_threads")
        assert not kept or len(list(kept)) == 0, "the server kept its request threads"
    finally:
        stop.set()
        serving.join(5)
        server.server_close()
