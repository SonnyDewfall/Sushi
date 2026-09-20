"""Start and stop the whole rig: `sushi-rig up`, `down`, `status`.

This replaces three shell scripts (`start-rig.sh`, `start-rig-and-panel.sh`,
`stop-rig.sh`). Every lifecycle failure this project has hit came from using
bash as a process supervisor, and they are all the same bug wearing different
hats — shutdown that identifies processes by *pattern* rather than by *identity*:

- `pkill -f "sushi-rig listen"` matches the command line of the shell that runs
  it, so a shutdown could kill its own caller and silently do nothing else.
- `killall sushi` never matched the running process at all. Sushi is an AppImage
  that re-execs `sushi.bin` from a randomly-named mount, so `killall` only ever
  saw the wrapper.
- `stop-rig.sh` had no pattern for open-stage-control, so it was simply left
  running — along with its Electron helpers — after a "clean" shutdown.

The fix is structural rather than a better set of patterns: every child is put
in one process group, and shutdown signals that group by the id recorded when it
was created. A process is then reachable regardless of what it renames itself
to, what it re-execs into, or whether anyone remembered to add a pattern for it.

The supervisor is deliberately unambitious. It starts children, owns the group,
and waits. It does not restart them, interpret a child dying, or try to keep the
rig healthy — a half-clever supervisor is harder to reason about than none, and
this one exists to make shutdown reliable, not to make the rig self-healing.
"""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

DEFAULT_CONFIG_NAME = "electric_board"
FALLBACK_RIG_YAML = "config/src/electric_board.yaml"
PANEL_FILE = "/tmp/sushi-rig-panel.json"
GRPC_HOST = "127.0.0.1"
GRPC_PORT = 51051
OSC_SEND_PORT = 24024

# Shutdown escalation. SIGINT first because that is what lets Sushi unhook its
# JACK ports cleanly rather than leaving them held; the later signals exist for
# anything that ignores the polite one.
SHUTDOWN_STEPS: tuple[tuple[signal.Signals, float], ...] = (
    (signal.SIGINT, 3.0),
    (signal.SIGTERM, 3.0),
    (signal.SIGKILL, 2.0),
)

# How long to wait for a killed qpwgraph to actually exit before starting ours.
QPWGRAPH_EXIT_TIMEOUT = 5.0
# How many times to try starting qpwgraph, and how long to watch each attempt
# before believing it. See `start_qpwgraph`.
QPWGRAPH_ATTEMPTS = 3
QPWGRAPH_SETTLE = 1.5
# How long to wait for Sushi's gRPC to start accepting connections.
GRPC_TIMEOUT = 15.0


class RigError(Exception):
    """Something the user needs to fix, reported without a traceback."""


# --- paths ------------------------------------------------------------------


def checkout_root() -> Path:
    """The rig checkout this package was installed from.

    Resolved from the package's own location, never from `$HOME`. The old
    `start-rig.sh` did `cd "$HOME/Sushi"`, which meant running it from a
    worktree silently drove the *main* checkout's config and tool — and the same
    assumption, baked into `LV2_PATH`, is the logged defect where a rig started
    outside the script finds no plugins at all.

    src/sushi_rig/rig.py -> src/sushi_rig -> src -> tool -> <root>
    """
    return Path(__file__).resolve().parents[3]


def runtime_dir() -> Path:
    """Where the state file and logs live.

    `XDG_RUNTIME_DIR` is tmpfs and is cleared when the user logs out, so a
    machine that crashed with a rig up cannot come back claiming one is still
    running. The `~/.cache` fallback is for systems without it, and is the one
    case where a stale file can outlive a reboot — which the liveness check
    below handles anyway.
    """
    base = os.environ.get("XDG_RUNTIME_DIR")
    root = Path(base) if base else Path.home() / ".cache"
    return root / "sushi-rig"


def state_path() -> Path:
    return runtime_dir() / "rig.json"


def log_dir() -> Path:
    return runtime_dir() / "logs"


# --- pure logic -------------------------------------------------------------


def resolve_rig_yaml(config_path: Path, root: Path | None = None) -> str:
    """The rig.yaml a config was built from, for the save listener.

    A saved variant records its originating yaml in `_meta.source`, because
    variants share the hand-authored source rather than getting one of their own.
    Reading it back means a save made *from* a variant is attributed correctly
    instead of always to electric_board.

    Canonical hand-authored configs carry no `_meta.source`, hence the fallback.

    Previously this was inline Python inside a bash heredoc, interpolating a
    shell variable straight into a string literal.
    """
    try:
        config = json.loads(Path(config_path).read_text())
    except (OSError, ValueError):
        return FALLBACK_RIG_YAML
    source = config.get("_meta", {}).get("source")
    return source if isinstance(source, str) and source else FALLBACK_RIG_YAML


def is_stale(state: dict[str, Any] | None, pid_alive: Callable[[int], bool]) -> bool:
    """Whether a recorded rig is gone and its state file can be ignored.

    No state is not stale state — there is simply nothing there. A state file
    naming a supervisor that no longer exists is stale, and `up` clears it and
    carries on rather than refusing forever after a crash.
    """
    if not state:
        return False
    pid = state.get("supervisor_pid")
    if not isinstance(pid, int):
        return True
    return not pid_alive(pid)


def plan_children(
    root: Path,
    config: Path,
    rig_yaml: str,
    *,
    headless: bool,
    tuner: bool,
    amp_project: Path | None = None,
    amp_model: str | None = None,
    amp_values: dict[str, float] | None = None,
    patchbay: Path | None = None,
) -> list[dict[str, Any]]:
    """The children to start, in order, as data.

    Pure so the two personas' differences can be asserted without launching
    anything. Each entry is `{name, command, optional}` — `optional` marks a
    child whose failure should be reported but not abort startup, which is the
    tuner and the patchbay: neither is in the audio path.
    """
    children: list[dict[str, Any]] = []

    if tuner:
        children.append({"name": "fmit", "command": ["fmit"], "optional": True})

    children.append({
        "name": "qpwgraph",
        "command": ["qpwgraph", "-a", str(patchbay or root / "Patchbay" / "rig.qpwgraph"), "-m"],
        "optional": True,
        # qpwgraph is a Qt app that registers with the desktop session manager
        # over ICE. The supervisor runs in its own session (see `_supervise`),
        # and from there that registration makes Qt quit immediately and
        # silently — no error, no output, an empty log, exactly like the stale
        # socket does. Isolated by A/B: identical command and environment, the
        # only difference being a new session, dies; with SESSION_MANAGER
        # removed from that same new session, it lives.
        #
        # Nothing here wants to be session-managed anyway. These are background
        # services, not desktop applications that should be asked to save state
        # and quit at logout.
        "env_unset": ["SESSION_MANAGER"],
    })

    if not headless:
        # Serves the panel's save button and its reorder buttons. Headless has
        # no panel, so nothing would ever talk to it.
        children.append({
            "name": "listen",
            "command": [
                str(root / "tool" / ".venv" / "bin" / "sushi-rig"), "listen",
                "--rig", rig_yaml,
                "--out-dir", "config",
                "--archive-dir", "config/archive",
                "--panel-config", str(config),
                "--panel-out", PANEL_FILE,
                "--sushi", "./sushi",
            ],
            "optional": False,
        })
        if amp_model:
            # So the save button can write the amp into the config it saves.
            children[-1]["command"] += ["--amp-model", amp_model]
            for name, value in (amp_values or {}).items():
                children[-1]["command"] += ["--amp-parameter", f"{name}={value}"]

    children.append({
        "name": "sushi",
        "command": ["pw-jack", "./sushi", "-j", "-c", str(config)],
        "optional": False,
    })

    if amp_project is not None:
        # The neural amp, in Carla, because Sushi cannot set its model — see
        # amp.py. Optional in the same sense the tuner is: the rig is perfectly
        # playable without it, and a missing Carla should say so rather than
        # stop the rig coming up.
        children.append({
            "name": "amp",
            "command": ["carla", "-n", str(amp_project)],
            "optional": True,
        })

    return children


def format_uptime(seconds: float) -> str:
    seconds = int(max(0, seconds))
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m {seconds % 60}s"
    return f"{seconds // 3600}h {(seconds % 3600) // 60}m"


# --- process helpers --------------------------------------------------------


def pid_alive(pid: int) -> bool:
    """Whether a pid exists, without caring whether we may signal it."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Exists, owned by someone else. Still alive for our purposes.
        return True
    return True


def pgid_alive(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def read_state() -> dict[str, Any] | None:
    try:
        return json.loads(state_path().read_text())
    except (OSError, ValueError):
        return None


def write_state(state: dict[str, Any]) -> None:
    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2) + "\n")


def clear_state() -> None:
    try:
        state_path().unlink()
    except FileNotFoundError:
        pass


def port_is_open(host: str, port: int, timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def wait_for_grpc(
    host: str,
    port: int,
    timeout: float,
    still_starting: Callable[[], bool] | None = None,
) -> bool:
    """Block until Sushi's gRPC accepts a connection, or give up.

    The panel is generated from the *live* rig, so asking for one before Sushi
    is listening fails in a way that looks like a tool bug rather than a timing
    problem.

    `still_starting` reports whether *our* Sushi is alive. Without it this waits
    for a port rather than for a process, and a port is not proof of anything:
    caught in testing, where a Sushi left over from an earlier start already
    held 51051, so the check passed instantly and the rig was declared up while
    its own Sushi had already exited. It also turns "the config is broken" from
    a full timeout into an immediate, accurate failure.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if still_starting is not None and not still_starting():
            return False
        if port_is_open(host, port):
            return True
        time.sleep(0.25)
    return False


def stale_qpwgraph_sockets(paths: list[Path], any_running: bool) -> list[Path]:
    """Which qpwgraph single-instance sockets are safe to remove.

    None of them while a qpwgraph is running — that socket is how the live
    instance is reachable, and deleting it would break a patchbay the user may
    be watching. Once nothing is running, any socket left behind is by
    definition stale.

    Pure, so the "never touch a live instance's socket" rule is pinned by a test
    rather than by care.
    """
    return [] if any_running else list(paths)


def qpwgraph_running() -> bool:
    return subprocess.run(
        ["pgrep", "-x", "qpwgraph"], check=False,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    ).returncode == 0


def stop_existing_qpwgraph(timeout: float = QPWGRAPH_EXIT_TIMEOUT) -> None:
    """Clear the way for our own qpwgraph: stop any instance AND remove the
    lock it leaves behind.

    This is issue #14, and the second half is the part that was missing.

    qpwgraph is single-instance via a Unix socket at
    `/tmp/qpwgraph:<user>@<host>`. That socket **survives SIGKILL** — the
    process dies, the socket file stays, and the next instance exits
    immediately and silently. No error, no crash, nothing in the journal, and an
    empty log file. The patchbay then never auto-connects, so Sushi runs with no
    audio in or out while looking completely healthy.

    Confirmed directly: SIGKILL a running qpwgraph, relaunch, and it dies. Delete
    the socket first and the identical command survives.

    The previous explanation — that the ~50% failure rate was just how often a
    qpwgraph happened to already be running — was incomplete, and its fix
    (kill, then wait for the process to disappear) could not work on its own:
    waiting for the *process* to go does nothing about the *socket* it left.
    It also explains what that theory could not: why retrying never helped, and
    why the failure rate tracked how the previous instance had exited — a clean
    quit removes the socket, a kill does not.
    """
    subprocess.run(["pkill", "-x", "qpwgraph"], check=False,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and qpwgraph_running():
        time.sleep(0.25)

    for socket_path in stale_qpwgraph_sockets(
        sorted(Path("/tmp").glob("qpwgraph:*")), qpwgraph_running()
    ):
        try:
            if socket_path.owner() == os.environ.get("USER", socket_path.owner()):
                socket_path.unlink()
        except (OSError, KeyError):
            # Someone else's socket, or already gone. Not ours to worry about;
            # a qpwgraph that then fails is reported by the caller anyway.
            pass


def start_qpwgraph(
    child: dict[str, Any],
    root: Path,
    env: dict[str, str],
    logs: Path,
    attempts: int = QPWGRAPH_ATTEMPTS,
    settle: float = QPWGRAPH_SETTLE,
) -> subprocess.Popen | None:
    """Start qpwgraph, and keep trying until it actually stays up.

    qpwgraph fails to start far more often than anything else here, and always
    the same way: it exits within a second or so with status 2, writing nothing
    at all — no error, no crash, an empty log. The patchbay then never
    auto-connects, so Sushi runs with no audio in or out while looking perfectly
    healthy. That is issue #14.

    Two causes are known and fixed at source: a lock socket left behind by an
    unclean exit (`stop_existing_qpwgraph`) and the desktop session manager
    (`env_unset` on the child). Neither explains all of it — with both fixed and
    no socket present, it still fails sometimes, and whether it does tracks
    nothing this code controls. It looks like a race inside qpwgraph or
    PipeWire, and it is not worth reverse-engineering someone else's GUI app to
    chase the rest.

    So: verify rather than assume, and retry rather than warn. Every attempt
    clears the socket first, because a failed attempt can leave one of its own.
    An honest bounded retry turns a silently broken patchbay into either a
    working one or a loud message, which is the outcome that actually matters.
    """
    child_env = dict(env)
    for key in child.get("env_unset", ()):
        child_env.pop(key, None)

    for attempt in range(attempts):
        stop_existing_qpwgraph()
        with open(logs / "qpwgraph.log", "wb") as sink:
            try:
                proc = subprocess.Popen(
                    child["command"], cwd=root, env=child_env,
                    stdin=subprocess.DEVNULL, stdout=sink, stderr=sink,
                )
            except OSError:
                return None
        time.sleep(settle)
        if proc.poll() is None:
            return proc
    return None


# --- up ---------------------------------------------------------------------


def _supervise(
    root: Path,
    config: Path,
    children_plan: list[dict[str, Any]],
    *,
    headless: bool,
    config_name: str,
    ready_w: int,
    amp_model: str | None = None,
    amp_values: dict[str, float] | None = None,
) -> None:
    """The supervisor process: own the group, start the children, then wait.

    Runs only in the forked child. `os.setsid()` makes it session and process
    group leader, and every child spawned from here inherits that group — so
    `down` can reach the whole tree with one `killpg`, including Sushi's
    re-exec'd `sushi.bin` and open-stage-control's Electron helpers.

    It stays alive purely to *own the group id*. If it exited and left the
    children orphaned, the recorded pgid would be a dead pid that Linux is free
    to reuse, and a later `down` could signal an unrelated process group.

    `ready_w` is a pipe back to the parent: the parent blocks on it so `up` only
    prints its summary once the rig is actually up, and reports a startup
    failure as a failure rather than returning a cheerful prompt.
    """
    os.setsid()
    logs = log_dir()
    logs.mkdir(parents=True, exist_ok=True)

    # Let go of the parent's stdin, stdout and stderr. A fork inherits them, so
    # without this the supervisor holds the write end of `up`'s stdout for as
    # long as the rig runs — and anything reading that output waits forever for
    # an EOF that never comes. Caught by piping `sushi-rig up` into grep, which
    # hung despite `up` itself having finished; it also means a terminal can be
    # closed without the supervisor still holding its tty.
    #
    # The ready pipe is deliberately untouched: it is how the parent learns the
    # rig is up, and it is closed explicitly once that has been said.
    devnull = os.open(os.devnull, os.O_RDWR)
    for stream in (0, 1, 2):
        os.dup2(devnull, stream)
    if devnull > 2:
        os.close(devnull)

    env = dict(os.environ)
    # The sole entry, deliberately: no /usr/lib/lv2 fallback. plugins/ holds
    # exactly the bundles this rig uses, so a missing or renamed plugin fails
    # loudly instead of silently resolving against a system copy and hiding a
    # portability break.
    env["LV2_PATH"] = str(root / "plugins")

    started: dict[str, int] = {}
    procs: dict[str, subprocess.Popen] = {}
    problems: list[str] = []

    def report(message: str) -> None:
        os.write(ready_w, (message + "\n").encode())

    try:
        for child in children_plan:
            name = child["name"]
            if name == "qpwgraph":
                proc = start_qpwgraph(child, root, env, logs)
                if proc is None:
                    problems.append(
                        f"qpwgraph would not stay running after "
                        f"{QPWGRAPH_ATTEMPTS} attempts — the patchbay is not "
                        "connected, so you likely won't hear anything even "
                        "though Sushi is running. Start it by hand: "
                        f"qpwgraph -a {root / 'Patchbay' / 'rig.qpwgraph'}"
                    )
                    continue
                started[name] = proc.pid
                procs[name] = proc
                continue
            handle = logs / f"{name}.log"
            child_env = dict(env)
            for key in child.get("env_unset", ()):
                child_env.pop(key, None)
            with open(handle, "wb") as sink:
                try:
                    proc = subprocess.Popen(
                        child["command"], cwd=root, env=child_env,
                        stdin=subprocess.DEVNULL, stdout=sink, stderr=sink,
                    )
                except OSError as exc:
                    if child["optional"]:
                        problems.append(f"{name} did not start ({exc})")
                        continue
                    report(f"FAIL {name} did not start: {exc}")
                    return
            started[name] = proc.pid
            procs[name] = proc

        sushi_proc = procs.get("sushi")
        alive = (lambda: sushi_proc.poll() is None) if sushi_proc else None
        if not wait_for_grpc(GRPC_HOST, GRPC_PORT, GRPC_TIMEOUT, alive):
            why = (
                "Sushi exited during startup"
                if sushi_proc and sushi_proc.poll() is not None
                else f"Sushi's gRPC never came up on {GRPC_HOST}:{GRPC_PORT}"
            )
            report(f"FAIL {why} — see {logs / 'sushi.log'}")
            return

        if not headless:
            panel = subprocess.run(
                [str(root / "tool" / ".venv" / "bin" / "sushi-rig"), "panel",
                 str(config), "--sushi", "./sushi", "-o", PANEL_FILE],
                cwd=root, env=env, capture_output=True, text=True, check=False,
            )
            if panel.returncode != 0:
                report(f"FAIL could not generate the panel:\n{panel.stderr.strip()}")
                return
            with open(logs / "panel.log", "wb") as sink:
                osc = subprocess.Popen(
                    ["open-stage-control", "--load", PANEL_FILE,
                     "--send", f"{GRPC_HOST}:{OSC_SEND_PORT}"],
                    cwd=root, env=env,
                    stdin=subprocess.DEVNULL, stdout=sink, stderr=sink,
                )
            started["open-stage-control"] = osc.pid

        if amp_model and "amp" in procs:
            # Carla starts with the model but with the plugin's own default
            # knob positions; the saved values have to be sent once it is
            # listening. Never fatal — a rig with an amp at its defaults is
            # much better than no rig.
            try:
                _replay_amp(amp_values or {})
            except Exception as exc:  # noqa: BLE001 - see above
                problems.append(f"amp parameters not restored: {exc}")

        write_state({
            "supervisor_pid": os.getpid(),
            "pgid": os.getpgrp(),
            "mode": "headless" if headless else "panel",
            "config_name": config_name,
            "config": str(config),
            "started_at": datetime.now(timezone.utc).isoformat(),
            "children": started,
            "log_dir": str(logs),
            "amp_model": amp_model,
        })

        for problem in problems:
            report(f"WARN {problem}")
        report("READY")
    except Exception as exc:  # noqa: BLE001 - the parent must hear about it
        report(f"FAIL {exc}")
        return

    os.close(ready_w)
    # Nothing to do but exist. The group id is the product.
    while True:
        try:
            os.wait()
        except ChildProcessError:
            signal.pause()
        except InterruptedError:
            continue


def _replay_amp(values: dict[str, float], timeout: float = 5.0) -> None:
    """Send the saved knob positions to a freshly started Carla.

    Carla starts with the model loaded but with the plugin's own default knob
    positions, so the saved values have to be sent once it is listening.

    The wait is a TCP connect. Carla serves OSC over *both* TCP and UDP on the
    same port, and our sends are UDP — where a send to a closed port succeeds
    silently and tells you nothing. The TCP side gives a readiness signal the
    UDP side cannot.
    """
    from pythonosc.udp_client import SimpleUDPClient

    from .amp import CARLA_OSC_PORT, parameter_message

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if port_is_open("127.0.0.1", CARLA_OSC_PORT):
            break
        time.sleep(0.2)
    else:
        raise RuntimeError("Carla's OSC server never came up")

    client = SimpleUDPClient("127.0.0.1", CARLA_OSC_PORT)
    for name, value in values.items():
        try:
            address, payload = parameter_message(name, value)
        except Exception:  # noqa: BLE001 - an unknown name is not fatal
            continue
        client.send_message(address, payload)


def up(
    config_name: str = DEFAULT_CONFIG_NAME,
    *,
    headless: bool = False,
    tuner: bool = True,
    amp: bool = True,
    amp_model: str | None = None,
) -> int:
    """Start the rig and return to the prompt.

    Forks a supervisor (see `_supervise`) and blocks on a pipe until it says the
    rig is actually up, so a failed start is reported as a failure rather than a
    cheerful prompt over a dead rig.
    """
    root = checkout_root()
    config = root / "config" / f"{config_name}.json"
    if not config.is_file():
        raise RigError(f"no such config: {config}")

    existing = read_state()
    if existing and not is_stale(existing, pid_alive):
        started = existing.get("started_at", "")
        try:
            age = format_uptime(
                (datetime.now(timezone.utc) - datetime.fromisoformat(started)).total_seconds()
            )
        except (TypeError, ValueError):
            age = "unknown"
        raise RigError(
            f"a rig is already running: {existing.get('config_name')} "
            f"({existing.get('mode')} mode), up {age}.\n"
            "Run 'sushi-rig down' first — starting a second rig on top of a "
            "running one is what takes the live session down."
        )
    if existing:
        # Named a supervisor that no longer exists: a crash, or a reboot on a
        # system without XDG_RUNTIME_DIR. Not a reason to refuse forever.
        clear_state()

    # The state file only knows about rigs this supervisor started. A Sushi
    # left over from a crashed supervisor — or one started by hand — is
    # invisible to it, and starting on top of that is exactly the collision the
    # guard above exists to prevent. The port is the evidence that survives
    # whoever started it.
    #
    # Caught by this happening during testing: a leftover Sushi already held
    # 51051, the new rig's own Sushi exited on the clash, and the readiness
    # check passed against the *other* instance.
    if port_is_open(GRPC_HOST, GRPC_PORT):
        raise RigError(
            f"something is already listening on {GRPC_HOST}:{GRPC_PORT} — "
            "a Sushi is running that this supervisor did not start.\n"
            "Find it with:  ps -eo pid,pgid,args | grep sushi.bin\n"
            "and stop its process group, or run 'sushi-rig down' if a state "
            "file exists."
        )

    rig_yaml = resolve_rig_yaml(config)

    # The amp is described in the config's `_amp` section, which Sushi ignores.
    # The Carla project is generated from it rather than kept as a file, because
    # the model changes with the config — that is the whole point of carrying it
    # in there. A config with no `_amp`, or `--no-amp`, simply starts no amp.
    amp_project = None
    amp_patch = None
    amp_values: dict[str, float] = {}
    amp_model_stored = None
    if amp:
        from .amp import amp_from_config_file, carla_project, resolve_model

        described = amp_from_config_file(config)
        if amp_model and not described:
            # --amp-model on a config with no amp section still gets an amp:
            # auditioning a model against a rig that has never had one is a
            # perfectly reasonable thing to want.
            described = {"model": amp_model, "parameters": {}}
        if described:
            amp_model_stored = described.get("model")
            if amp_model:
                # A bare filename means amp/models/, which is where they live —
                # typing the directory every time would be tedious.
                amp_model_stored = (
                    amp_model if "/" in amp_model else f"amp/models/{amp_model}"
                )
            amp_values = dict(described.get("parameters") or {})
            model_path = resolve_model(amp_model_stored, root)
            amp_project = runtime_dir() / "amp.carxp"
            amp_project.parent.mkdir(parents=True, exist_ok=True)
            amp_project.write_text(carla_project(model_path))

            # qpwgraph maintains whatever patch it loaded, so the amp needs its
            # own file rather than extra connections layered on the saved one —
            # otherwise the direct guitar-to-Sushi route stays live too and the
            # dry signal sits under the amped one. Generated from the saved
            # patchbay so the two cannot drift apart.
            from .amp import amp_patchbay

            base = root / "Patchbay" / "rig.qpwgraph"
            if base.is_file():
                amp_patch = runtime_dir() / "rig-amp.qpwgraph"
                amp_patch.write_text(amp_patchbay(base.read_text()))

    children_plan = plan_children(
        root, config, rig_yaml, headless=headless, tuner=tuner,
        amp_project=amp_project, amp_model=amp_model_stored,
        amp_values=amp_values, patchbay=amp_patch,
    )

    ready_r, ready_w = os.pipe()
    pid = os.fork()
    if pid == 0:
        os.close(ready_r)
        try:
            _supervise(
                root, config, children_plan,
                headless=headless, config_name=config_name, ready_w=ready_w,
                amp_model=amp_model_stored, amp_values=amp_values,
            )
        finally:
            os._exit(0)

    os.close(ready_w)
    outcome = ""
    with os.fdopen(ready_r) as pipe:
        for line in pipe:
            line = line.strip()
            if line.startswith("WARN "):
                print(f"warning: {line[5:]}", file=sys.stderr)
            elif line.startswith("FAIL "):
                outcome = line[5:]
                break
            elif line == "READY":
                outcome = "READY"
                break

    if outcome != "READY":
        # The supervisor gives up on its own; make sure nothing it did manage to
        # start is left behind.
        _kill_group_of(pid)
        clear_state()
        raise RigError(outcome or "the rig failed to start")

    state = read_state() or {}
    mode = "headless" if headless else "panel"
    print(f"Rig up: {config_name} ({mode} mode)")
    for name, child_pid in (state.get("children") or {}).items():
        print(f"  {name:10} pid {child_pid}")
    if not headless:
        print("\nPanel: http://127.0.0.1:8080")
    print(f"\nLogs: {state.get('log_dir')}")
    print("Stop with: sushi-rig down")
    return 0


def _kill_group_of(pid: int) -> None:
    """Best-effort teardown of a supervisor's group, used on a failed start."""
    try:
        pgid = os.getpgid(pid)
    except ProcessLookupError:
        return
    for sig, wait in SHUTDOWN_STEPS:
        try:
            os.killpg(pgid, sig)
        except ProcessLookupError:
            return
        deadline = time.monotonic() + wait
        while time.monotonic() < deadline:
            if not pgid_alive(pgid):
                return
            time.sleep(0.1)


def down(force: bool = False) -> int:
    """Stop the rig, then verify it is actually stopped.

    Signals the recorded process group, never a command-line pattern. That is
    the whole point: `sushi.bin`, open-stage-control's Electron helpers and the
    listener are all reachable as group members regardless of what they are
    called, which is precisely what the old `pkill`/`killall` shutdown missed.
    """
    state = read_state()
    if not state:
        print("No rig is running.")
        return 0

    pgid = state.get("pgid")
    if not isinstance(pgid, int):
        print("State file has no process group — clearing it.", file=sys.stderr)
        clear_state()
        return 1

    if not pgid_alive(pgid):
        print("No rig is running (clearing a stale state file).")
        clear_state()
        return 0

    steps = ((signal.SIGKILL, 2.0),) if force else SHUTDOWN_STEPS
    print(f"Stopping {state.get('config_name')} ({state.get('mode')} mode)...")
    for sig, wait in steps:
        try:
            os.killpg(pgid, sig)
        except ProcessLookupError:
            break
        deadline = time.monotonic() + wait
        while time.monotonic() < deadline:
            if not pgid_alive(pgid):
                break
            time.sleep(0.1)
        if not pgid_alive(pgid):
            break

    if pgid_alive(pgid):
        print(
            f"Process group {pgid} survived SIGKILL. Something is stuck in an "
            "uninterruptible state — check with: ps -eo pid,pgid,args | "
            f"awk '$2 == {pgid}'",
            file=sys.stderr,
        )
        return 1

    clear_state()
    print("Rig offline.")
    return 0


def restart_arguments(state: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """What `up` should be called with to bring back the rig `state` describes.

    The record of what to restore is what was actually *running*: a rig started
    with --no-amp or --no-tuner has no such child, and should come back the same
    way. Pure, so "restart brings back the same rig" is a test rather than a
    claim.
    """
    children = state.get("children") or {}
    return state.get("config_name", DEFAULT_CONFIG_NAME), {
        "headless": state.get("mode") == "headless",
        "tuner": "fmit" in children,
        "amp": "amp" in children,
    }


def restart() -> int:
    """Stop the rig and bring the same one straight back.

    Recovery is the whole answer to a failure here — the rig is allowed to die
    as long as it returns with what it had — so it should be one command, not
    two plus remembering which config was running. Measured at roughly five
    seconds end to end.

    Takes its arguments from the state file rather than from the caller, which
    is the point: after a crash you may well not remember the mode, the config
    or whether the amp was running.
    """
    state = read_state()
    if not state:
        raise RigError(
            "no rig is running, so there is nothing to restart.\n"
            "Start one with: sushi-rig up <config>"
        )

    config_name, options = restart_arguments(state)

    print(f"Restarting {config_name}...")
    outcome = down()
    if outcome != 0:
        raise RigError("could not stop the running rig, so it was not restarted")
    return up(config_name, **options)


def status() -> int:
    """What is running, if anything."""
    state = read_state()
    if not state:
        print("No rig is running.")
        return 0

    pgid = state.get("pgid")
    if not isinstance(pgid, int) or not pgid_alive(pgid):
        print("No rig is running (the state file is stale; 'sushi-rig down' clears it).")
        return 0

    try:
        age = format_uptime(
            (datetime.now(timezone.utc)
             - datetime.fromisoformat(state["started_at"])).total_seconds()
        )
    except (KeyError, TypeError, ValueError):
        age = "unknown"

    print(f"{state.get('config_name')} ({state.get('mode')} mode), up {age}")
    print(f"  config     {state.get('config')}")
    print(f"  group      {pgid}")
    for name, pid in (state.get("children") or {}).items():
        print(f"  {name:10} pid {pid} {'' if pid_alive(pid) else '(DEAD)'}")
    if state.get("amp_model"):
        print(f"  amp model  {state['amp_model']}")
    print(f"  logs       {state.get('log_dir')}")
    return 0
