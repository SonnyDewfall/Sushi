"""The recorder the agent owns (S7): recording from the web UI.

A take records through a JACK client (`capture.CaptureEngine`), which has to
run under `pw-jack` with the rig's `PIPEWIRE_QUANTUM`: the system libjack
finds no server on a PipeWire-only machine (capture.py). In panel mode the
listener is that process, which is why `up` starts it as `pw-jack ... sushi-rig
listen`. Headless, which is how the web UI runs the rig, there is no listener,
and the agent is not under `pw-jack` and must not be: it serves HTTP, holds
the rig's group, and outlives the rig. So recording is a process of its own,
`sushi-rig recorder`, and the agent speaks to it.

## A child of the rig, started with it

The recorder is planned by `rig.plan_children` for a headless run of a rig of
channels (`up --rig --headless`, which is what the page starts), and started
into the rig's process group with the rest, in the listener's place. Chosen
over starting it at the first RECORD and keeping it, because:

- **It is supervised for nothing.** A held child that exits is a crash in
  /health (`agent.supervisor.crashed`) and on stderr, and is not restarted:
  principle 9, the same as Sushi or the amp, with no second path for one
  process started outside a lifecycle step. Started at the first RECORD, it
  would need its own start into the held group mid-run (as only the amp has,
  for a reason the recorder lacks) and its own story for a rig the agent does
  not hold.
- **It stops with the rig.** `down` signals the group, SIGINT first, and the
  recorder finishes a take on SIGINT (`serve`), so a rig stopped from the CLI,
  or by an agent killed mid-take, still leaves a finished take, as the
  listener's `Transport.close` does in panel mode.
- **It costs nothing until a take.** It opens no JACK client until RECORD
  (`CaptureEngine.start`); between takes it is a Python waiting on a socket.

`up --direct` plans it too, since `plan_children` is the one planner, so a
headless rig of channels started from the CLI records from the page as well.

## A Unix socket in the runtime dir

`paths.recorder_socket()`, beside the state file, mode 0600, one JSON line in
and one out per connection (`ask`, `handle`). Not the child's stdin and
stdout: the rig outlives the agent, and a pipe would tie the recorder to the
agent that started it. A socket at a known path is found by the next agent,
and by one that never held the rig. Whoever can connect can start a take
that writes where the request says (`run.takes_dir`): the socket is the
user's own, in the user's runtime directory, as the state file is, so that is
nothing the user could not do anyway.

## What a request carries, and what is read when

The recorder holds no idea of which rig is running: a live switch (#93) and
a save-as (S4) change that while it runs. Each request names the run's files
(`Run`: the rig file, the compiled config Sushi loaded, the machine file,
the takes folder, the checkout) from the state file as the agent reads it,
and a take is planned at its RECORD by `transport.record_session`, exactly as
the listener plans one: the same ports (`transport.rig_stems`), the same
refusals, the same `TakeSession`. So a take carries the config as it is live
at RECORD, with the amp as the agent last sent it (`amp`, written by
`amp.write_amp` as SAVE writes it, #117) and the rig file as it is at RECORD
(#110). A status plans from the same files with `transport.plan_for_run` to
say whether a take could be made now, once per change of them (`_available`),
and builds nothing: no session and no engine, which only a start makes. That
JACK-Client loads is checked once, when the process starts (`main`,
`engine_problem`), and a status says so if it does not.

One take at a time; a start and a stop are serialised (`_lock`), and a status
never waits for them: it answers what is being done (`doing`). Each request
is served on a thread of its own, a daemon one that nothing keeps once it
ends (`_Server`), so a status is not held behind a stop draining the writer
and a recorder polled every second for hours keeps no record of each poll.

**NOT VERIFIED: no take has been captured through this path.** The process,
its socket and its sessions are tested with a stand-in engine; the engine
itself has never run against a live PipeWire (capture.py, transport.py).
"""

from __future__ import annotations

import json
import os
import signal
import socket
import socketserver
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

# One request is a line of JSON well under this; anything longer is refused
# rather than read without end.
REQUEST_LIMIT = 64 * 1024

# How long the agent waits for each answer. A status reads three small files.
# A start opens a JACK client, waits `capture.SETTLE_S` for the quantum on the
# first take, makes and reads back every link (`LINK_RETRY_S`); a stop drains
# the writer to disk. Guesses with room in them: nothing here has run against
# a live PipeWire, and how long a stop takes on the rig is for it to measure
# (transport.Transport.close says the same of the listener's).
STATUS_TIMEOUT = 3.0
START_TIMEOUT = 20.0
STOP_TIMEOUT = 20.0

# A Unix socket's path is at most this many bytes on Linux (sun_path, 108,
# less its terminating NUL); a longer one is refused with that said, not cut.
SOCKET_PATH_MAX = 107

OPS = ("status", "start", "stop")


class RecorderDown(Exception):
    """No recorder answers on the socket: none is running, or it is not
    listening. The message says which, in the socket's own words."""


class RecorderTimeout(Exception):
    """The recorder took longer than it was given to answer."""


class RequestError(ValueError):
    """A request the recorder will not act on, with why."""


# --------------------------------------------------------------------------- #
# The request, pure
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class Run:
    """The files of the run a request is about: what `record_session` plans
    from. Every one an absolute path, or None where the run has none (a run
    of a v1 config has no rig file, and `record_session` says so)."""

    root: str | None
    rig: str | None
    config: str | None
    machine: str | None
    takes_dir: str | None

    def key(self) -> tuple:
        """These paths and what is at them now: a plan made from one key is
        good until a file changes or another run is named."""
        stamps = []
        for path in (self.rig, self.config, self.machine):
            try:
                stat = os.stat(path) if path else None
                stamps.append((stat.st_mtime_ns, stat.st_size) if stat else None)
            except OSError:
                stamps.append("missing")
        return (self.root, self.rig, self.config, self.machine, self.takes_dir, tuple(stamps))


def parse_run(raw: Any) -> Run:
    fields = ("root", "rig", "config", "machine", "takes_dir")
    if not isinstance(raw, dict) or not set(raw) <= set(fields):
        raise RequestError(f"run must be a mapping of {', '.join(fields)}, not {raw!r}")
    for name in fields:
        value = raw.get(name)
        if value is not None and not (isinstance(value, str) and value and os.path.isabs(value)):
            raise RequestError(f"run.{name} must be an absolute path or null, not {value!r}")
    return Run(**{name: raw.get(name) for name in fields})


def parse_amp(raw: Any) -> dict[str, Any] | None:
    """The amp a take's config carries: `{"model", "parameters"}`, `model`
    None for a run with no amp (the take carries none), or None for an amp
    whose model is not known (the config keeps the `_amp` it was compiled
    with, rather than a guess, and the agent's answer says so)."""
    if raw is None:
        return None
    if not isinstance(raw, dict) or not set(raw) <= {"model", "parameters"}:
        raise RequestError(f'amp must be null or {{"model", "parameters"}}, not {raw!r}')
    model, parameters = raw.get("model"), raw.get("parameters") or {}
    if model is not None and not (isinstance(model, str) and model):
        raise RequestError(f"amp.model must be a name or null, not {model!r}")
    if not isinstance(parameters, dict) or not all(
            isinstance(k, str) and isinstance(v, (int, float)) and not isinstance(v, bool)
            for k, v in parameters.items()):
        raise RequestError(f"amp.parameters must map names to numbers, not {parameters!r}")
    return {"model": model, "parameters": {k: float(v) for k, v in parameters.items()}}


def parse_request(line: bytes) -> dict[str, Any]:
    """One request line, checked: `op` and what that op takes, nothing else."""
    try:
        request = json.loads(line)
    except ValueError as exc:
        raise RequestError(f"the request is not JSON: {exc}") from exc
    if not isinstance(request, dict) or request.get("op") not in OPS:
        raise RequestError(f"a request is {{\"op\": one of {', '.join(OPS)}, ...}}, not {request!r}")
    allowed = {"status": {"op", "run"}, "start": {"op", "name", "run", "amp"}, "stop": {"op", "by"}}[request["op"]]
    extra = set(request) - allowed
    if extra:
        raise RequestError(f"{request['op']} does not take {', '.join(sorted(extra))}")
    parsed: dict[str, Any] = {"op": request["op"]}
    if "run" in request:
        parsed["run"] = parse_run(request["run"])
    if request["op"] == "start":
        if "run" not in parsed:
            raise RequestError("start needs the run's files (run)")
        if not isinstance(request.get("name"), str):
            raise RequestError(f"start needs a take name, not {request.get('name')!r}")
        parsed["name"] = request["name"]
        parsed["amp"] = parse_amp(request.get("amp"))
    if request["op"] == "stop":
        by = request.get("by", "request")
        if not isinstance(by, str) or not by:
            raise RequestError(f"by is what stopped the take, a word, not {by!r}")
        parsed["by"] = by
    return parsed


def refusal(code: str, reason: str) -> dict[str, Any]:
    return {"ok": False, "code": code, "reason": reason}


def stems_shown(stems) -> list[dict[str, Any]]:
    """A plan's stems as an answer lists them: file, port, channel count."""
    return [{"file": stem.filename, "channel": stem.channel, "tap": stem.tap, "port": stem.port,
             "channels": channels} for stem, channels in stems]


# --------------------------------------------------------------------------- #
# The recorder: one take at a time
# --------------------------------------------------------------------------- #

@dataclass
class _Take:
    name: str
    session: Any
    takes_dir: Path
    started_at: str
    started: float
    stems: list[dict[str, Any]]
    said: str


class RecorderCore:
    """What the process does with a request (`handle`), apart from the socket,
    so the agent's tests can run it in their own process and the process's
    tests can stand its engine in.

    `engine(sample_rate, quantum)` makes the capture engine, as
    `transport.record_session` takes it: `capture.CaptureEngine` by default.
    One engine is kept per rate and quantum, so the graph's quantum is read on
    the first take only (`CaptureEngine`'s docstring), as the listener's one
    engine does. `session_for` is `record_session`; `clock` times a take
    (monotonic) and `now` says when it began."""

    def __init__(
        self, *, engine: Callable[[int, int], Any] | None = None,
        session_for: Callable[..., Any] | None = None,
        clock: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        say: Callable[[str], None] | None = None,
        engine_problem: str | None = None,
    ) -> None:
        from .transport import _capture_engine, record_session

        self._make_engine = engine or _capture_engine
        self._session_for = session_for or record_session
        self.clock = clock
        self.now = now
        self.say = say or (lambda message: print(f"sushi-rig recorder: {message}", file=sys.stderr, flush=True))
        self._engines: dict[tuple[int, int], Any] = {}
        self._engines_lock = threading.Lock()  # one engine per key, however asked
        # Why no capture engine can be made here (JACK-Client missing), found
        # once at the process's start: what a status says without making one.
        self.engine_problem = engine_problem
        self._lock = threading.Lock()  # one start or stop at a time
        self._take: _Take | None = None
        self.doing: str | None = None  # "starting" or "stopping", read by a status
        self._plan: tuple[tuple, dict[str, Any]] | None = None  # (Run.key, what a status says of it)

    # ------------------------------------------------------------- requests

    def handle(self, request: dict[str, Any]) -> dict[str, Any]:
        """One parsed request's answer. Never raises: the process must
        survive whatever a take does, and say it."""
        try:
            if request["op"] == "status":
                return self.status(request.get("run"))
            if request["op"] == "start":
                return self.start(request["name"], request["run"], request.get("amp"))
            return self.stop(request.get("by", "request"))
        except Exception as exc:  # noqa: BLE001 -- said, the process lives on
            reason = " ".join(f"{type(exc).__name__}: {exc}".split())
            self.say(f"{request.get('op')} failed unexpectedly: {reason}")
            return refusal("failed", f"the recorder failed unexpectedly: {reason}")

    def status(self, run: Run | None = None) -> dict[str, Any]:
        """Recording (`name`, `started_at`, `elapsed` in seconds, `stems`), or
        idle with whether a take of `run` could be made now: `idle` with the
        stems it would record, or `unavailable` with why. `doing` while a
        start or stop is under way. Never waits for one."""
        take = self._take
        if take is not None:
            return {"ok": True, "state": "recording", "name": take.name, "started_at": take.started_at,
                    "elapsed": round(self.clock() - take.started, 3), "stems": take.stems,
                    "takes_dir": str(take.takes_dir), "said": take.said, "doing": self.doing}
        answer: dict[str, Any] = {"ok": True, "state": "idle", "doing": self.doing}
        if run is not None:
            answer.update(self._available(run))
        return answer

    def _available(self, run: Run) -> dict[str, Any]:
        """What a status says of `run`: the plan `record_session` would make,
        or its refusal in the same words, from `plan_for_run` alone. Reads
        the run's files and the live graph, once per change of the files;
        makes no session and no engine, so a status costs a stat of three
        files and changes nothing.

        The ports it shows are the ones that read of the graph resolved
        (`transport.jack_port`), so a device that comes back under another
        profile is named as it was until the files change. A take is not: its
        own plan reads the graph again at RECORD."""
        from .transport import NoEngine, PlanError, plan_for_run

        if self.engine_problem:
            return {"state": "unavailable", "reason": NoEngine(self.engine_problem).message}
        key = run.key()
        planned = self._plan
        if planned is None or planned[0] != key:
            try:
                plan = plan_for_run(
                    Path(run.rig) if run.rig else None, Path(run.config) if run.config else None,
                    machine_file=Path(run.machine) if run.machine else None,
                    takes_dir=Path(run.takes_dir) if run.takes_dir else None,
                    root=Path(run.root) if run.root else None)
                said = {"state": "idle", "stems": stems_shown(plan.stems), "takes_dir": str(plan.takes_dir)}
            except PlanError as exc:
                said = {"state": "unavailable",
                        "reason": NoEngine(f"no take can be planned for this run: {exc}").message}
            except Exception as exc:  # noqa: BLE001 -- as record_session says it
                said = {"state": "unavailable", "reason": NoEngine(
                    f"no take can be planned for this run: unexpected error: {exc}").message}
            planned = (key, said)
            self._plan = planned  # one assignment: a status racing it sees one plan or the other
        return dict(planned[1])

    def _session(self, run: Run, amp: dict[str, Any] | None):
        """`record_session` for `run`, with the amp `amp` says (see
        `parse_amp`): planned now, from the files as they are now."""
        session = self._session_for(
            Path(run.rig) if run.rig else None, Path(run.config) if run.config else None,
            machine_file=Path(run.machine) if run.machine else None,
            takes_dir=Path(run.takes_dir) if run.takes_dir else None,
            root=Path(run.root) if run.root else None,
            amp_model=(amp or {}).get("model"), amp_parameters=dict((amp or {}).get("parameters") or {}),
            engine=self._engine,
        )
        if amp is None and hasattr(session, "amend_config"):
            # The amp's model is not known: the config keeps the `_amp` it was
            # compiled with rather than losing it to `write_amp(None)`, which
            # would say no amp ran.
            session.amend_config = None
        return session

    def _engine(self, sample_rate: int, quantum: int):
        """The engine for this rate and quantum, made once. Only a start asks
        (under `_lock`), but the dict has its own lock so that stays true of
        whatever asks next."""
        key = (sample_rate, quantum)
        with self._engines_lock:
            if key not in self._engines:
                self._engines[key] = self._make_engine(sample_rate, quantum)
            return self._engines[key]

    def start(self, name: str, run: Run, amp: dict[str, Any] | None) -> dict[str, Any]:
        """Begin take `name` of `run`: planned now (`_session`), then
        `TakeSession.start`. Refused: `recording` (one is running), `name`
        (the name rule), `unavailable` (no take can be planned, with
        `NoEngine`'s reason); `failed` when it was tried and did not start,
        with `TakeSession`'s sentence, the take's own folder saying so."""
        from .take import check_name
        from .transport import NoEngine, TransportError

        with self._lock:
            if self._take is not None:
                return refusal("recording", f"already recording {self._take.name}: stop it first")
            problems = check_name(name)
            if problems:
                return refusal("name", problems[0])
            session = self._session(run, amp)
            if isinstance(session, NoEngine):
                return refusal("unavailable", session.message)
            takes_dir = Path(session.plan.takes_dir)
            replacing = (takes_dir / name).exists()
            self.doing = "starting"
            try:
                said = session.start(name)
            except TransportError as exc:
                self.say(f"record failed: {exc}")
                return refusal("failed", f"record failed: {exc}")
            finally:
                self.doing = None
            self._take = _Take(name, session, takes_dir, self.now().isoformat(timespec="seconds"),
                               self.clock(), stems_shown(session.plan.stems), said)
            self.say(said)
            return {**self.status(), "said": said, "replaced": replacing}

    def stop(self, by: str = "request") -> dict[str, Any]:
        """Finish the running take, and say how it came out: `seconds`, and
        `take check`'s `problems` (empty when whole), read back from the
        folder. Idle afterwards whatever happened, as `Transport.on_stop` is:
        a session that raised on stop has stopped as far as it can. Refused
        (`not_recording`) when no take runs."""
        from .take import take_summary
        from .transport import TransportError

        with self._lock:
            take = self._take
            if take is None:
                return refusal("not_recording", "not recording")
            self._take = None
            self.doing = "stopping"
            error = None
            try:
                said = take.session.stop()
            except TransportError as exc:
                said, error = f"stop failed for {take.name}: {exc}", str(exc)
            except Exception as exc:  # noqa: BLE001 -- the take is over either way
                said = error = f"stop failed for {take.name}: unexpected error: {type(exc).__name__}: {exc}"
            finally:
                self.doing = None
            self.say(f"{said} (stopped by {by})")
            folder = take.takes_dir / take.name
            # The take has stopped whatever reading it back does: a summary
            # that fails is said beside the stop, never in place of it, so the
            # agent logs the stop and does not report a take still running.
            try:
                summary = take_summary(folder)
            except Exception as exc:  # noqa: BLE001 -- said in the answer
                reason = " ".join(f"{type(exc).__name__}: {exc}".split())
                self.say(f"{take.name} stopped, but could not be read back: {reason}")
                summary = {"folder": str(folder), "seconds": None, "complete": None,
                           "problems": [f"the take stopped, but could not be read back to check it: {reason}; "
                                        f"`sushi-rig take check {folder}` says more"],
                           "error": reason}
            answer = {
                "ok": error is None and "error" not in summary, "stopped": True, "state": "idle",
                "name": take.name, "folder": summary["folder"],
                "started_at": take.started_at, "seconds": summary["seconds"],
                "complete": summary["complete"], "problems": summary["problems"],
                "stems": take.stems, "said": said, "by": by,
            }
            if error is not None:
                answer.update(code="failed", reason=said)
            elif "error" in summary:
                answer.update(code="failed", summary_error=summary["error"],
                              reason=f"{take.name} stopped, but could not be read back: {summary['error']}")
            return answer

    def close(self) -> dict[str, Any] | None:
        """At the process's end: finish a running take, so a `down` (SIGINT)
        ends a take rather than abandoning it. None when none was running."""
        return self.stop("the recorder stopping") if self._take is not None else None


# --------------------------------------------------------------------------- #
# The process: the socket, and the signals that end it
# --------------------------------------------------------------------------- #

def _check_path(path: Path) -> None:
    if len(os.fsencode(str(path))) > SOCKET_PATH_MAX:
        raise OSError(f"the socket path {path} is longer than a Unix socket's {SOCKET_PATH_MAX} bytes")


def ask(path: Path, request: dict[str, Any], timeout: float) -> dict[str, Any]:
    """Send one request to the recorder at `path` and return its answer.
    `RecorderDown` when nothing answers there, `RecorderTimeout` past
    `timeout` (the request may still be acted on: a start that took longer
    than this may yet be recording, which the next status says)."""
    path = Path(path)
    try:
        _check_path(path)
    except OSError as exc:
        raise RecorderDown(str(exc)) from exc
    conn = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    conn.settimeout(timeout)
    try:
        try:
            conn.connect(str(path))
        except (FileNotFoundError, ConnectionRefusedError) as exc:
            # The only two that mean nobody is there: no file, or a file no
            # process listens on (what a recorder killed outright leaves).
            raise RecorderDown(f"no recorder is listening on {path} ({exc.strerror or exc})") from exc
        except (socket.timeout, BlockingIOError) as exc:
            # A recorder whose queue of connections is full (EAGAIN), or that
            # did not take this one in time: there, and busy, not gone.
            raise RecorderTimeout(
                f"the recorder on {path} did not take the connection within {timeout:g}s: it is busy") from exc
        except OSError as exc:
            raise RecorderDown(f"the recorder's socket {path} could not be reached: {exc}") from exc
        try:
            conn.sendall(json.dumps(request).encode() + b"\n")
            reply = b""
            while not reply.endswith(b"\n"):
                chunk = conn.recv(65536)
                if not chunk:
                    break
                reply += chunk
                if len(reply) > 16 * REQUEST_LIMIT:
                    raise RecorderDown("the recorder's answer was too long to be one")
        except socket.timeout as exc:
            raise RecorderTimeout(
                f"the recorder did not answer {request.get('op')} within {timeout:g}s") from exc
        except OSError as exc:
            raise RecorderDown(f"the recorder went away mid-request: {exc}") from exc
    finally:
        conn.close()
    try:
        answer = json.loads(reply)
    except ValueError as exc:
        raise RecorderDown(f"the recorder's answer is not JSON ({exc}): {reply[:200]!r}") from exc
    if not isinstance(answer, dict):
        raise RecorderDown(f"the recorder's answer is not a mapping: {answer!r}")
    return answer


class RecorderLink:
    """The agent's end of the channel: `ask` at a fixed socket. A test
    stands in an object with the same `ask(request, timeout)`."""

    def __init__(self, path: Path | None = None) -> None:
        from .paths import recorder_socket

        self.path = Path(path) if path is not None else recorder_socket()

    def ask(self, request: dict[str, Any], timeout: float) -> dict[str, Any]:
        return ask(self.path, request, timeout)

    def __repr__(self) -> str:
        return f"RecorderLink({str(self.path)!r})"


def _handler(core: RecorderCore):
    class Handler(socketserver.StreamRequestHandler):
        def handle(self) -> None:
            self.connection.settimeout(5.0)  # a client that connects and says nothing is let go
            try:
                line = self.rfile.readline(REQUEST_LIMIT + 1)
            except OSError:
                return
            if len(line) > REQUEST_LIMIT:
                answer = refusal("bad_request", f"a request is at most {REQUEST_LIMIT} bytes")
            else:
                try:
                    answer = core.handle(parse_request(line))
                except RequestError as exc:
                    answer = refusal("bad_request", str(exc))
            try:
                self.wfile.write(json.dumps(answer).encode() + b"\n")
            except OSError:
                pass  # the asker went; the take, if any, goes on
    return Handler


class _Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    # A status must not wait behind a stop draining the writer, so each
    # connection gets a thread; the core serialises start and stop itself.
    # Daemon threads, not joined on close: with `block_on_close` the server
    # keeps every request's thread in a list it never trims (Python 3.10, the
    # floor), and a recorder polled every second would grow without bound. A
    # stop in flight when the process ends is still finished, since
    # `RecorderCore.close` waits for the core's lock, which that stop holds.
    daemon_threads = True
    block_on_close = False


def bind(path: Path, core: RecorderCore) -> _Server:
    """The server for `core` on `path`, made 0600. A socket already there that answers
    is another recorder, which is refused rather than taken over; one that
    does not is a crash's leftover, and is replaced."""
    path = Path(path)
    _check_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() or path.is_symlink():
        try:
            ask(path, {"op": "status"}, 1.0)
        except RecorderDown:
            path.unlink()
        except RecorderTimeout:
            raise OSError(f"something holds {path} and does not answer: not replacing it")
        else:
            raise OSError(f"another recorder is already listening on {path}")
    old = os.umask(0o077)  # the socket is never reachable by another user, even for an instant
    try:
        server = _Server(str(path), _handler(core))
    finally:
        os.umask(old)
    os.chmod(path, 0o600)
    return server


def serve(path: Path, core: RecorderCore, *, poll: float = 0.2) -> int:
    """Answer requests on `path` until SIGINT or SIGTERM, then finish a running
    take, remove the socket and return 0.

    The signal handlers only set a flag, and the loop looks at it between
    requests (`poll`), so a signal never lands in the middle of a start or a
    stop: `down` sends SIGINT and, three seconds later, SIGTERM
    (`procs.SHUTDOWN_STEPS`), and a take finishing then is not interrupted by
    the second signal, though one still finishing at SIGKILL is cut off and
    left saying it is unfinished (`take repair`, then `take check`)."""
    stopping = threading.Event()

    def stop(signum, frame) -> None:
        stopping.set()

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    server = bind(path, core)
    server.timeout = poll
    mine = os.stat(path).st_ino
    core.say(f"listening on {path}")
    try:
        while not stopping.is_set():
            server.handle_request()
    finally:
        server.server_close()  # no new requests; one in flight runs on
        finished = core.close()  # waits for a start or stop in flight, then ends a take
        if finished is not None:
            core.say(f"finished {finished['name']} on the way out: {finished['said']}")
        try:
            if os.stat(path).st_ino == mine:
                os.unlink(path)
        except OSError:
            pass
        core.say("stopped")
    return 0


def main(path: Path | None = None) -> int:
    """`sushi-rig recorder`: the process `up` starts for a headless rig of
    channels, under `pw-jack`. Says at its start whether JACK-Client loads,
    so a machine without it is found in recorder.log before anyone presses
    RECORD (and GET /record says it too)."""
    from .capture import CaptureError, load_jack
    from .paths import recorder_socket

    path = Path(path) if path is not None else recorder_socket()
    core = RecorderCore()
    try:
        load_jack()
    except CaptureError as exc:
        core.engine_problem = str(exc)
        core.say(f"RECORD is off: {exc}")
    try:
        return serve(path, core)
    except OSError as exc:
        core.say(f"cannot listen: {exc}")
        return 1
