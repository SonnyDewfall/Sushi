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
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .paths import checkout_root, log_dir, runtime_dir, state_path
from .procs import (
    SHUTDOWN_STEPS, pgid_alive, pid_alive, port_is_open, stop_group, wait_for_grpc,
)
from .qpwgraph import QPWGRAPH_ATTEMPTS, start_qpwgraph, unconnected_inputs
from .state import clear_state, is_real_id, liveness, read_state, write_state

DEFAULT_CONFIG_NAME = "electric_board"
PANEL_FILE = "/tmp/sushi-rig-panel.json"
GRPC_HOST = "127.0.0.1"
GRPC_PORT = 51051
OSC_SEND_PORT = 24024

# Shutdown escalation. SIGINT first because that is what lets Sushi unhook its
# JACK ports cleanly rather than leaving them held; the later signals exist for
# anything that ignores the polite one.
# How long to wait for Sushi's gRPC to start accepting connections.
GRPC_TIMEOUT = 15.0

# The buffer size the rig asks PipeWire for, in samples at the graph rate.
#
# This used to be left to whatever the desktop happened to be doing, and on a
# fresh machine that turned out to be 2048 — 42.7 ms per block, measured as
# 42.69 ms of delay through Sushi and audible as the "high latency" this was
# first reported as. Worse, it was *sticky*: a JACK client sets
# `node.lock-quantum`, so Sushi pins whatever the graph was at when it started
# and holds it there for its whole life, however the graph later changes.
#
# So the rig states it rather than inheriting it, for the same reason it states
# `LV2_PATH`: a number this audible should come from the repository and not
# from the machine. 256 measures at 5.35 ms through Sushi with the chain at 2%
# of its budget and no xruns, which leaves a wide margin for the amp. Lower is
# available — 128 and 64 both run — but below about 256 the interface's own
# 512-sample headroom dominates, so the gain gets small and the risk does not.
QUANTUM = 256

# The PipeWire node `fmit` registers as, via the ALSA compatibility layer.
TUNER_NODE = "alsa_capture.fmit"



def child_environment(
    base: dict[str, str], child: dict[str, Any]
) -> dict[str, str]:
    """The environment one child is started with.

    Scoped per child rather than set on the supervisor, because the tuner and
    the panel have no business being dragged to the audio path's buffer size.

    `env_set` is **assigned, not defaulted**: stating these values in the
    repository is the entire point, so an inherited one must not win. While
    this merged with `setdefault`, a `PIPEWIRE_QUANTUM` already in the
    environment put the rig back on the desktop's buffer size and a
    `PIPEWIRE_PROPS` replaced the tuner's autoconnect setting outright — each
    quietly undoing the fix it sits beside, which is why the precedence is now
    pinned by a test instead of trusted.
    """
    env = dict(base)
    for key in child.get("env_unset", ()):
        env.pop(key, None)
    for key, value in child.get("env_set", {}).items():
        env[key] = value
    return env


class RigError(Exception):
    """Something the user needs to fix, reported without a traceback."""


# --- paths ------------------------------------------------------------------


def resolve_rig_yaml(config_path: Path, root: Path | None = None) -> str | None:
    """The rig source SAVE can save this config from, or None with a reason in
    `rig_source_problem`.

    A source is a candidate only if it describes the running config: every
    track and plugin the config names is in the source's spec. The candidates,
    in order: the config's `_meta.source` (written by `emit` and `save`), then
    the file of the config's own name under `config/src/`, then under
    `config/rigs/`. A candidate that does not fit is passed over, so a fact is
    checked rather than a path guessed at. None means SAVE is refused with a
    message.

    It used to return `_meta.source` or fall back to electric_board's yaml,
    unchecked. For any other config that meant SAVE captured the running rig,
    `emit` dropped every processor the capture named that electric_board's spec
    did not, and the file written under the player's name was electric_board's
    chain with no settings, while the panel said "saved". Found by the audit,
    on guitar_vocal. The check against the config is what stops the same thing
    happening through a `_meta.source` that is merely wrong — the converted v2
    rigs carry their v1 variant's provenance, for one.
    """
    source, _ = _rig_source(config_path, root)
    return source


def rig_source_problem(config_path: Path, root: Path | None = None) -> str | None:
    """Why SAVE is off for this config, in a sentence, or None if it is on."""
    _, problem = _rig_source(config_path, root)
    return problem


def _rig_source(config_path: Path, root: Path | None) -> tuple[str | None, str | None]:
    from .rigfile import spec_of

    root = Path(root) if root is not None else checkout_root()
    try:
        config = json.loads(Path(config_path).read_text())
    except (OSError, ValueError):
        config = {}
    if not isinstance(config, dict):
        config = {}
    named = {t.get("name") for t in (config.get("tracks") or []) if isinstance(t, dict)} | {
        p.get("name") for t in (config.get("tracks") or []) if isinstance(t, dict)
        for p in (t.get("plugins") or []) if isinstance(p, dict)
    }
    stem = Path(config_path).stem
    candidates = []
    source = (config.get("_meta") or {}).get("source")
    if isinstance(source, str) and source:
        candidates.append(source)
    candidates += [f"config/src/{stem}.yaml", f"config/rigs/{stem}.yaml"]
    tried = []
    for candidate in dict.fromkeys(candidates):
        path = root / candidate
        if not path.is_file():
            tried.append(f"{candidate} does not exist")
            continue
        try:
            spec = spec_of(path)
        except Exception as exc:  # noqa: BLE001 - a bad candidate is a reason, not a crash
            tried.append(f"{candidate} cannot be read as a rig ({exc})")
            continue
        known = set(spec.plugin_names()) | {t.name for t in spec.tracks}
        missing = sorted(named - known)
        if missing:
            tried.append(f"{candidate} has no {', '.join(missing)}")
            continue
        return candidate, None
    return None, (
        f"SAVE is off for {stem}: no rig source describes it ("
        + "; ".join(tried) + "). Reorder and the amp controls still work"
    )


def prepare_rig(
    name: str,
    root: Path,
    runtime: Path,
    *,
    tuner: bool = True,
    machine_file: Path | None = None,
    dump: str | None = None,
) -> dict[str, Any]:
    """Everything a run of a v2 rig needs, written out: the compiled config and
    the patchbay generated for it (#30's last slice).

    A rig of channels is compiled by `emit_rig` to the config Sushi loads,
    under the runtime directory, never into `config/`: the rig file is the
    source and the compiled form is a run's artefact, like the amp's Carla
    project. The patchbay comes from the rig and the machine file, through the
    same `patchbay_for` as `patchbay generate`, because the committed
    `rig.qpwgraph` describes the v1 layout: on guitar_vocal it would feed the
    guitar into input 1, the vocal channel under v2, and wire the microphone
    to inputs the rig does not read. Read off the files on 2026-11-01, not
    heard, and exactly the kind of healthy-looking silence this project has
    paid for before.

    `dump` is the live graph's pw-dump text; None reads it. `machine_file`
    defaults to this machine's. Raises `RigError` with the reason, since a rig
    whose channels cannot be wired must not come up half-wired.
    """
    from .machine import MachineError, MachineSpec, machine_path
    from .patchbay import PatchbayError, patchbay_for, read_pw_dump
    from .rigfile import RigFileError, emit_rig, load_rig, spec_of

    path = root / "config" / "rigs" / f"{name}.yaml"
    if not path.is_file():
        raise RigError(f"no such rig: {path}")
    try:
        compiled = emit_rig(load_rig(path))
        spec = spec_of(path)
    except RigFileError as exc:
        raise RigError(f"{path.name}:\n  " + "\n  ".join(exc.problems)) from exc
    problems = spec.validate()
    if problems:
        raise RigError(f"{path.name}: " + "; ".join(problems))

    machine_file = machine_file or machine_path(root)
    try:
        machine = MachineSpec.load(machine_file)
    except MachineError as exc:
        raise RigError(
            f"a rig of channels needs the machine file to wire its inputs, and "
            f"{machine_file.name} could not be read: {exc}"
        ) from exc
    problems = machine.validate()
    if problems:
        raise RigError(f"{machine_file.name}: " + "; ".join(problems))

    if dump is None:
        try:
            dump = read_pw_dump()
        except PatchbayError as exc:
            raise RigError("cannot read the PipeWire graph to wire the rig: " + "; ".join(exc.problems)) from exc
    try:
        xml, warnings = patchbay_for(
            spec, machine, dump, tuner=tuner, rig_name=path.name,
            machine_name=machine_file.name,
        )
    except PatchbayError as exc:
        raise RigError(f"{path.name} cannot be wired on this machine:\n  " + "\n  ".join(exc.problems)) from exc

    # The provenance `emit` writes, so `resolve_rig_yaml` knows which file this
    # run came from and `save` (when it can write a v2 rig) saves there.
    source = f"config/rigs/{name}.yaml"
    meta = compiled.setdefault("_meta", {})
    if isinstance(meta, dict):
        meta["source"] = source
    runtime.mkdir(parents=True, exist_ok=True)
    config = runtime / f"{name}.json"
    config.write_text(json.dumps(compiled, indent=2) + "\n")
    patchbay = runtime / f"{name}.qpwgraph"
    patchbay.write_text(xml)
    return {
        "config": config,
        "patchbay": patchbay,
        "source": source,
        "warnings": warnings,
    }


def plan_children(
    root: Path,
    config: Path,
    rig_yaml: str | None,
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
        children.append({
            "name": "fmit",
            "command": ["fmit"],
            "optional": True,
            # `fmit` reaches PipeWire through the ALSA compatibility layer,
            # which auto-connects a capture stream to whatever the *default
            # source* happens to be. On this machine that was a webcam, and
            # because the patchbay also wires `fmit` to the interface, the
            # tuner bridged the two devices into one driver group: `pw-dump`
            # showed the interface and Sushi both carrying the webcam's
            # `node.driver-id`. A guitar rig clocked by a webcam, resampling
            # the audio path to it and inheriting its xruns.
            #
            # Turning autoconnect off puts the tuner's routing where every
            # other connection in this rig already lives — the patchbay. It
            # also makes `fmit` negotiate the single `input_MONO` port it
            # actually wants, rather than the stereo pair the default source
            # imposed on it.
            #
            # The cost is that an unwired tuner is silent rather than wrong,
            # so `_supervise` checks for exactly that and says so.
            "env_set": {"PIPEWIRE_PROPS": "{ node.autoconnect = false }"},
        })

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
                # Without a source the listener still serves the reorder and
                # amp buttons; SAVE answers with why it cannot.
                *(["--rig", rig_yaml] if rig_yaml else []),
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
        "env_set": {"PIPEWIRE_QUANTUM": f"{QUANTUM}/48000"},
    })

    if amp_project is not None:
        # The neural amp, in Carla, because Sushi cannot set its model — see
        # amp.py. Optional in the same sense the tuner is: the rig is perfectly
        # playable without it, and a missing Carla should say so rather than
        # stop the rig coming up.
        children.append({
            "name": "amp",
            # `pw-jack`, for the same reason Sushi has it. Carla links against
            # the system libjack, which on a PipeWire-only machine finds no
            # server and exits during startup — "jack server is not running or
            # cannot be started", in the amp log, nowhere else. PipeWire's own
            # libjack lives in a directory the loader does not search, so only
            # the shim puts it in front. It worked before this was noticed
            # because the previous machine had a jackd to fall back on, which
            # made the rig quietly non-portable in exactly the way `LV2_PATH`
            # is kept narrow to prevent.
            "command": ["pw-jack", "carla", "-n", str(amp_project)],
            "optional": True,
            # The same quantum as Sushi. Two JACK clients each locking a
            # different one is a fight nobody wins.
            "env_set": {"PIPEWIRE_QUANTUM": f"{QUANTUM}/48000"},
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
    tuner_requested: bool = True,
    amp_requested: bool = True,
    amp_model_requested: str | None = None,
    rig_source: str | None = None,
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
            child_env = child_environment(env, child)
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
            # In `procs` too, so the liveness check below covers the panel.
            # It was the one child the check missed, because it is started
            # outside the loop that fills `procs`.
            procs["open-stage-control"] = osc

        # A child that starts and *then* exits was, until this check, silent.
        # Popen succeeding only means the binary existed; Carla dying a second
        # later for want of a JACK server left `up` printing READY and a rig
        # with no amp in it, which is precisely the "processing silence while
        # looking perfectly healthy" failure principle 8 exists to forbid. The
        # amp's own symptom was worse than absence: the only warning came from
        # the parameter replay timing out, which reads as "your knobs are at
        # their defaults" rather than "you have no amp".
        for name, proc in procs.items():
            if proc.poll() is None:
                continue
            problems.append(
                f"{name} exited during startup (status {proc.returncode}) — "
                f"see {logs / f'{name}.log'}"
            )
            if name == "amp":
                problems[-1] += (
                    "; the rig is playable but there is NO AMP in the signal "
                    "path, so the guitar is running dry"
                )

        # The tuner no longer auto-connects (see `plan_children`), so the
        # patchbay is the only thing that wires it. A patchbay that does not
        # name its ports leaves the tuner running and deaf, which looks exactly
        # like a tuner that cannot hear the guitar — so it is said out loud
        # rather than left to be discovered mid-session (principle 8).
        if "fmit" in procs and procs["fmit"].poll() is None:
            # Guarded, unlike its neighbours' `poll()` calls, because this one
            # shells out. Every check here is deliberately non-fatal -- it
            # appends to `problems` and the rig still comes up -- but an
            # exception escaping into the enclosing `except Exception` reports
            # FAIL, and the parent then tears down a rig that was playing
            # perfectly well. A missing `pw-link` must not cost the rig.
            try:
                unwired = unconnected_inputs(TUNER_NODE)
            except (OSError, subprocess.TimeoutExpired) as exc:
                problems.append(
                    f"could not check whether the tuner is wired ({exc}) — if it "
                    "shows nothing, wire its input in qpwgraph"
                )
            else:
                if unwired is None:
                    # Distinct from "nothing unwired": PipeWire listed no such
                    # node, so the question went unanswered rather than
                    # answered well. Saying "fine" here would be the same
                    # mistake as the fixture that gave every row a quantum.
                    problems.append(
                        f"the tuner is running but PipeWire lists no {TUNER_NODE} "
                        "ports, so its wiring could not be checked — the node may "
                        "have been renamed"
                    )
                elif unwired:
                    problems.append(
                        "the tuner is running but nothing is connected to "
                        + ", ".join(unwired)
                        + f" — wire the guitar's input to it in qpwgraph and save "
                        f"{root / 'Patchbay' / 'rig.qpwgraph'}, or the tuner will "
                        "show nothing"
                    )

        if amp_model and "amp" in procs and procs["amp"].poll() is None:
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
            # What was asked for, which `children` cannot say once an optional
            # child has failed to start. `restart` reads these.
            "tuner_requested": tuner_requested,
            "amp_requested": amp_requested,
            # The override as the caller gave it, None when there was none. Not
            # `amp_model`, which is what *ran* and so includes the config's own
            # choice: restoring that would pin today's model for good, and a
            # later edit to the config would be ignored by every restart.
            "amp_model_requested": amp_model_requested,
            # The rig file a v2 run was compiled from, None for a v1 config.
            # `restart` brings back the same kind; `status` says which.
            "rig": rig_source,
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
    refused = []
    for name, value in values.items():
        try:
            address, payload = parameter_message(name, value)
        except Exception:  # noqa: BLE001 - one bad value must not stop the rest
            refused.append(name)
            continue
        client.send_message(address, payload)
    if refused:
        # After the valid ones are sent, so the amp is as close to its saved
        # sound as it can be; and loud, so a NaN in a config is not a knob
        # quietly left at Carla's default.
        raise RuntimeError("not sent, as not valid: " + ", ".join(refused))


def _fork_supervisor(supervise: Callable[[int], None]) -> tuple[int, int]:
    """Fork the supervisor; return its pid and the read end of the ready pipe.

    Kept apart from `up` so that the fork and the pipe are the one place a test
    has to stand in for, and everything `up` *decides* -- refuse, clear, report
    -- can be pinned without forking anything.

    The child runs `supervise(write_end)` and then leaves with `os._exit`,
    never by returning: returning would unwind into the caller's stack and run
    the parent's cleanup and `atexit` handlers a second time, in a process that
    is meant to be a detached daemon.
    """
    ready_r, ready_w = os.pipe()
    pid = os.fork()
    if pid == 0:
        os.close(ready_r)
        try:
            supervise(ready_w)
        finally:
            os._exit(0)
    os.close(ready_w)
    return pid, ready_r


def up(
    config_name: str = DEFAULT_CONFIG_NAME,
    *,
    headless: bool = False,
    tuner: bool = True,
    amp: bool = True,
    amp_model: str | None = None,
    rig: bool = False,
) -> int:
    """Start the rig and return to the prompt.

    Forks a supervisor (see `_supervise`) and blocks on a pipe until it says the
    rig is actually up, so a failed start is reported as a failure rather than a
    cheerful prompt over a dead rig.

    `rig` means `config_name` names a rig of channels, `config/rigs/<name>.yaml`,
    compiled at start with its own patchbay (`prepare_rig`). Opt-in rather than
    the default while a v1 config of the same name exists: the monitor path was
    heard to be the same on 2026-11-01, but a two-channel rig has not been, and
    what `up` starts by default changes only once it has.
    """
    root = checkout_root()
    rig_source: str | None = None
    rig_patchbay: Path | None = None
    if rig:
        pass  # compiled below, after the running-rig guard, so a refusal costs nothing
    config = root / "config" / f"{config_name}.json"
    if not rig and not config.is_file():
        raise RigError(f"no such config: {config}")

    existing = read_state()
    verdict = liveness(existing, pid_alive, pgid_alive)
    if verdict == "running":
        raise RigError(
            f"a rig is already running: {describe_rig(existing)['message']}.\n"
            "Run 'sushi-rig down' first — starting a second rig on top of a "
            "running one is what takes the live session down."
        )
    if verdict == "orphaned":
        raise RigError(
            f"a previous rig's supervisor ({existing.get('supervisor_pid')}) is "
            f"gone but its process group still has members: what a crash left "
            "behind. Starting over would put a second rig on top of them.\n"
            "Run 'sushi-rig down' to stop them, then start again."
        )
    if verdict == "invalid":
        raise RigError(
            f"{state_path()} does not describe a rig this tool started (its "
            "ids are missing, nonsensical, or the group is not the "
            "supervisor's own), so it will not be trusted either way.\n"
            "Inspect it; if it is garbage, delete it, or run "
            "'sushi-rig down --force' if its pgid really is a rig's."
        )
    if verdict == "gone":
        # Named a supervisor and group that no longer exist: a crash, or a
        # reboot on a system without XDG_RUNTIME_DIR. Not a reason to refuse
        # forever.
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

    if rig:
        prepared_rig = prepare_rig(config_name, root, runtime_dir(), tuner=tuner)
        config = prepared_rig["config"]
        rig_patchbay = prepared_rig["patchbay"]
        rig_source = prepared_rig["source"]
        for warning in prepared_rig["warnings"]:
            print(f"warning: {warning}", file=sys.stderr)

    rig_yaml = resolve_rig_yaml(config, root)
    if not headless:
        problem = rig_source_problem(config, root) if rig_yaml is None else None
        if rig_yaml is not None and rig_yaml.startswith("config/rigs/"):
            # A v2 rig file describes the config, and save cannot write one
            # yet: #30's last slice. Said at start, not at the first press.
            problem = (
                f"SAVE is off for {config_name}: it was built from {rig_yaml}, a rig of "
                "channels, which save cannot write yet. Reorder and the amp controls "
                "still work"
            )
            rig_yaml = None
        if problem:
            print(f"warning: {problem}", file=sys.stderr)

    # The amp is described in the config's `_amp` section, which Sushi ignores.
    # The Carla project is generated from it rather than kept as a file, because
    # the model changes with the config — that is the whole point of carrying it
    # in there. A config with no `_amp`, or `--no-amp`, simply starts no amp.
    prepared = {}
    if amp:
        from .amp import prepare as prepare_amp

        prepared = prepare_amp(config, root, runtime_dir(), amp_model, base=rig_patchbay)
    amp_project = prepared.get("project")
    amp_patch = prepared.get("patchbay") or rig_patchbay
    amp_values = prepared.get("values", {})
    amp_model_stored = prepared.get("model")

    children_plan = plan_children(
        root, config, rig_yaml, headless=headless, tuner=tuner,
        amp_project=amp_project, amp_model=amp_model_stored,
        amp_values=amp_values, patchbay=amp_patch,
    )

    pid, ready_r = _fork_supervisor(
        lambda ready_w: _supervise(
            root, config, children_plan,
            headless=headless, config_name=config_name, ready_w=ready_w,
            amp_model=amp_model_stored, amp_values=amp_values,
            tuner_requested=tuner, amp_requested=amp,
            amp_model_requested=amp_model, rig_source=rig_source,
        )
    )
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
    """Best-effort teardown of a supervisor's group, used on a failed start.

    `pid` is our own child, and is reaped as the group is polled. A supervisor
    that has already given up (it reports FAIL and exits) is a zombie until
    someone waits for it, and a zombie still counts as a member of its group, so
    without that the escalation ran its whole eight seconds against a process
    that was already dead -- the error message arrived that much late.
    """
    def reap() -> None:
        try:
            os.waitpid(pid, os.WNOHANG)
        except ChildProcessError:
            pass

    try:
        pgid = os.getpgid(pid)
    except ProcessLookupError:
        return
    if pgid == os.getpgrp():
        # The supervisor died before its `setsid`, so it is still in *our*
        # group, and signalling that would kill the command reporting the
        # failure. Nothing was started yet if it never got that far.
        return
    try:
        stop_group(pgid, poll=reap)
    except PermissionError:
        # Already a failure path; there is nothing better to say than the error
        # that brought us here.
        return


def down(force: bool = False) -> int:
    """Stop the rig, then verify it is actually stopped.

    Signals the recorded process group, never a command-line pattern. That is
    the whole point: `sushi.bin`, open-stage-control's Electron helpers and the
    listener are all reachable as group members regardless of what they are
    called, which is precisely what the old `pkill`/`killall` shutdown missed.

    The state file is trusted as identity, so it is checked before anything is
    signalled (see `liveness`). `force` skips the polite signals, and on a file
    that fails that check it is also the user's say-so to signal the recorded
    group anyway.
    """
    state = read_state()
    verdict = liveness(state, pid_alive, pgid_alive)
    if verdict == "absent":
        print("No rig is running.")
        return 0
    if verdict == "gone":
        print("No rig is running (clearing a stale state file).")
        clear_state()
        return 0

    pgid = state.get("pgid")
    if verdict == "invalid":
        reason = (
            f"{state_path()} does not describe a rig this tool started "
            f"(supervisor_pid {state.get('supervisor_pid')!r}, pgid {pgid!r})"
        )
        if not force:
            print(
                f"{reason}, so nothing was signalled and the file is kept. "
                "If it is garbage, delete it; if its pgid really is a rig's, "
                "'sushi-rig down --force' will signal that group on your say-so.",
                file=sys.stderr,
            )
            return 1
        if not is_real_id(pgid) or pgid == os.getpgrp():
            print(
                f"{reason}, and its pgid cannot be signalled even with --force "
                "(it is not a real group id, or it is this command's own). "
                "Delete the file.",
                file=sys.stderr,
            )
            return 1
        print(f"{reason}; signalling group {pgid} anyway because --force was given.",
              file=sys.stderr)
    elif verdict == "orphaned":
        print(
            f"Supervisor {state.get('supervisor_pid')} is gone but its process "
            f"group {pgid} still has members: this rig's leftovers after a "
            "crash. Stopping them."
        )

    steps = ((signal.SIGKILL, 2.0),) if (force or verdict == "invalid") else SHUTDOWN_STEPS
    print(f"Stopping {state.get('config_name')} ({state.get('mode')} mode)...")
    try:
        gone = stop_group(pgid, steps)
    except PermissionError:
        # Alive, but not ours to signal: a state file from another user. A
        # message, not a traceback, and the file is kept as evidence.
        print(
            f"Not permitted to signal process group {pgid}, so it was left "
            "alone. It is not owned by this user; the state file is likely "
            "stale or belongs to someone else's rig.",
            file=sys.stderr,
        )
        return 1
    if not gone:
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

    The record of what to restore is what was *asked for*, not what survived.
    The tuner and the amp are optional, so one that failed to start is absent
    from `children` -- and reading the children would make that failure silently
    permanent: the first restart would drop the amp for good, with nothing said,
    the exact "looks healthy, has no amp" state principle 8 forbids. So
    `_supervise` records the request, and a rig started with --no-amp still
    comes back without one because it never asked for one.

    The amp's model is restored from `amp_model_requested`: the `--amp-model`
    the caller gave, or None. Not from `amp_model`, which is what ran and so
    includes the config's own choice -- restoring that would pin today's model
    for good and make every restart ignore a later edit to the config. A state
    file from before it was recorded restores no override.

    A state file written before the request was recorded falls back to reading
    the children, which is the best that file can say. Pure, so "restart brings
    back the same rig" is a test rather than a claim.
    """
    children = state.get("children") or {}
    tuner = state.get("tuner_requested")
    amp = state.get("amp_requested")
    return state.get("config_name", DEFAULT_CONFIG_NAME), {
        "headless": state.get("mode") == "headless",
        "tuner": tuner if isinstance(tuner, bool) else "fmit" in children,
        "amp": amp if isinstance(amp, bool) else "amp" in children,
        "amp_model": state.get("amp_model_requested") or None,
        "rig": isinstance(state.get("rig"), str) and bool(state.get("rig")),
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


def describe_rig(
    state: dict[str, Any] | None = None,
    alive: Callable[[int], bool] | None = None,
    group_alive: Callable[[int], bool] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """What is running, if anything, as data: the one answer `status` prints
    and the agent serves (#29).

    `verdict` is `liveness`'s word (absent, gone, invalid, orphaned, running)
    and `message` says it the way `status` always has. The rest is filled only
    for a running rig. A child's `alive` is None when the recorded pid is not
    one, which is reported rather than raised because this is what you read
    when things are odd. Pure over its arguments so the shape is a test, not a
    claim; `status` and the agent both call it with the real state and probes.
    """
    # The module's own probes, looked up now rather than bound at import, so
    # standing them in for a test works the same way it does for `status`.
    alive = alive or pid_alive
    group_alive = group_alive or pgid_alive
    if state is None:
        state = read_state()
    verdict = liveness(state, alive, group_alive)
    described: dict[str, Any] = {"verdict": verdict, "running": verdict == "running"}
    if verdict == "absent":
        described["message"] = "No rig is running."
        return described
    if verdict == "gone":
        described["message"] = (
            "No rig is running (the state file is stale; 'sushi-rig down' clears it)."
        )
        return described
    if verdict == "invalid":
        described["message"] = (
            f"{state_path()} does not describe a rig this tool started, so "
            "its contents are not reported. 'sushi-rig down' says more."
        )
        return described
    if verdict == "orphaned":
        described["message"] = (
            f"The supervisor ({state.get('supervisor_pid')}) is gone but its "
            f"process group {state.get('pgid')} still has members: what a "
            "crash left behind. 'sushi-rig down' stops them."
        )
        return described

    try:
        started = datetime.fromisoformat(state["started_at"])
        uptime = ((now or datetime.now(timezone.utc)) - started).total_seconds()
    except (KeyError, TypeError, ValueError):
        uptime = None
    children = state.get("children")
    described.update({
        "config_name": state.get("config_name"),
        "mode": state.get("mode"),
        "config": state.get("config"),
        "pgid": state["pgid"],
        "started_at": state.get("started_at"),
        "uptime_seconds": uptime,
        "children": {
            str(name): {
                "pid": pid,
                "alive": alive(pid) if is_real_id(pid) else None,
            }
            for name, pid in (children.items() if isinstance(children, dict) else ())
        },
        "amp_model": state.get("amp_model") or None,
        "rig": state.get("rig") if isinstance(state.get("rig"), str) else None,
        "log_dir": state.get("log_dir"),
    })
    age = format_uptime(uptime) if uptime is not None else "unknown"
    described["message"] = f"{described['config_name']} ({described['mode']} mode), up {age}"
    return described


def status() -> int:
    """What is running, if anything."""
    described = describe_rig()
    print(described["message"])
    if not described["running"]:
        return 0
    print(f"  config     {described['config']}")
    if described["rig"]:
        print(f"  rig        {described['rig']}")
    print(f"  group      {described['pgid']}")
    for name, child in described["children"].items():
        if child["alive"] is None:
            # Read from a file, so not trusted to be a pid at all; said, not
            # raised, because `status` is what you run when things are odd.
            print(f"  {name:10} pid {child['pid']!r} (INVALID)")
            continue
        print(f"  {name:10} pid {child['pid']} {'' if child['alive'] else '(DEAD)'}")
    if described["amp_model"]:
        print(f"  amp model  {described['amp_model']}")
    print(f"  logs       {described['log_dir']}")
    return 0
