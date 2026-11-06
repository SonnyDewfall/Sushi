"""The CLI's way to the agent (#29): `up`, `down`, `restart` and `status` ask
the agent when one is running, and act directly when none is.

Why ask at all: the agent holds the rig it starts and watches its children, so
a crash shows in `/health`. A rig the CLI forked for itself while an agent ran
would be one the agent can stop, through the state file, but not watch. So
with an agent there the CLI is its client; with nothing listening the CLI does
what it always did. Either way it says which, in one line on stderr, so a
player can tell without guessing and stdout stays the command's own output.

Refuse rather than guess: something on the agent's port that does not answer
as the agent does -- an agent stuck mid-request, another program -- is neither
"running" nor "not running", so the CLI stops and says what it found, with
`--direct` as the way to act without it.

Standard library only, so `sushi-rig status` does not need the agent extra.
"""

from __future__ import annotations

import json
import socket
import sys
import urllib.error
import urllib.request
from typing import Any, Callable, TextIO

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8029

# How long the CLI waits to hear whether an agent is there. A local agent
# answers /health in milliseconds; this is for one that accepted the
# connection and is stuck, which is refused rather than waited on.
PROBE_TIMEOUT = 2.0

# How long a lifecycle request may take. `up` is bounded step by step (Sushi's
# gRPC 15 s, qpwgraph's three attempts, the patchbay check's 10 s, the panel's
# 60 s, the amp's 5 s) and `down` by its escalation; this is well past their
# sum, so it fires only on an agent that has stopped answering.
REQUEST_TIMEOUT = 300.0


class Absent(Exception):
    """Nothing is listening where the agent would be: act directly."""


class Unusable(Exception):
    """Something is there and is not answering as the agent does: refuse."""


def agent_url(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> str:
    """The address a client on this machine reaches an agent bound to `host`
    at: a wildcard bind is reached on loopback, an IPv6 address in brackets.
    Pure."""
    if host in ("", "0.0.0.0"):
        host = "127.0.0.1"
    elif host == "::":
        host = "::1"
    if ":" in host:
        host = f"[{host}]"
    return f"http://{host}:{port}"


def address_from(text: str, alive: Callable[[int], bool]) -> tuple[str | None, str]:
    """What an agent file says: `(url, why)`, the url None when the file
    cannot be trusted, with why. Pure over `alive`, the pid probe."""
    try:
        recorded = json.loads(text)
    except ValueError:
        return None, "is not JSON"
    if not isinstance(recorded, dict):
        return None, "is not an object"
    url, pid = recorded.get("url"), recorded.get("pid")
    if not isinstance(url, str) or not url.startswith("http://"):
        return None, "names no http:// address"
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 1:
        return None, "names no agent pid"
    if not alive(pid):
        return None, f"names agent pid {pid}, which is gone"
    return url, f"written by agent pid {pid}"


def find_agent() -> tuple[str, str]:
    """Where to look for the agent, and how that was decided, for the line
    the CLI prints: the address a running agent wrote (`paths.agent_path`),
    or the default when there is none or it is stale."""
    from .paths import agent_path
    from .procs import pid_alive

    path = agent_path()
    default = agent_url()
    try:
        text = path.read_text()
    except FileNotFoundError:
        return default, f"the default; no agent has written {path}"
    except OSError as exc:
        return default, f"the default; {path} could not be read: {exc}"
    url, why = address_from(text, pid_alive)
    if url is None:
        return default, f"the default; {path} {why}"
    return url, f"from {path}, {why}"


def write_address(url: str, pid: int) -> None:
    """Said by the agent at its start: where it listens."""
    from .paths import agent_path

    path = agent_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"url": url, "pid": pid}) + "\n")


def remove_address(pid: int) -> None:
    """Unsaid by the agent at its end, if the file is still its own: a second
    agent that failed to bind must not erase the first one's."""
    from .paths import agent_path

    path = agent_path()
    try:
        recorded = json.loads(path.read_text())
    except (OSError, ValueError):
        return
    if isinstance(recorded, dict) and recorded.get("pid") == pid:
        path.unlink(missing_ok=True)


def _open(request: urllib.request.Request, timeout: float):
    # Never through a proxy: an HTTP_PROXY in the environment would otherwise
    # carry a request for localhost off the machine, or to nothing.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    return opener.open(request, timeout=timeout)


def ask(base: str, method: str, path: str, body: Any = None,
        timeout: float = REQUEST_TIMEOUT) -> tuple[int, Any]:
    """One request to the agent, as (status, JSON). An error status is an
    answer, not an exception: the agent says why in the body. `Absent` when
    nothing is listening; `Unusable` for no answer, or one that is not JSON."""
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(
        base + path, data=data, method=method,
        headers={"Content-Type": "application/json"} if data is not None else {},
    )
    try:
        with _open(request, timeout) as response:
            status, raw = response.status, response.read()
    except urllib.error.HTTPError as exc:
        status, raw = exc.code, exc.read()
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, ConnectionRefusedError):
            raise Absent(f"nothing is listening at {base}") from exc
        if isinstance(exc.reason, (TimeoutError, socket.timeout)):
            raise Unusable(f"{base} accepted the connection but did not answer within {timeout:g}s") from exc
        raise Unusable(f"{base} could not be asked: {exc.reason}") from exc
    except ConnectionRefusedError as exc:
        raise Absent(f"nothing is listening at {base}") from exc
    except (TimeoutError, socket.timeout) as exc:
        raise Unusable(f"{base} accepted the connection but did not answer within {timeout:g}s") from exc
    except OSError as exc:  # reset mid-answer, and the like
        raise Unusable(f"{base} could not be asked: {exc}") from exc
    try:
        return status, json.loads(raw.decode())
    except (UnicodeDecodeError, ValueError) as exc:
        raise Unusable(f"{base} answered {status}, but not with JSON, so it is not the agent") from exc


def probe(base: str, timeout: float = PROBE_TIMEOUT) -> dict[str, Any]:
    """The agent's /health, or `Absent` (nothing there) or `Unusable`
    (something there that is not the agent answering)."""
    status, body = ask(base, "GET", "/health", timeout=timeout)
    if status != 200 or not isinstance(body, dict) or not isinstance(body.get("agent"), dict):
        raise Unusable(f"{base} answered /health with {status}, but not as the agent does")
    return body


def lifecycle_request(command: str, args: Any) -> tuple[str, dict[str, Any]]:
    """The agent route and body for one CLI lifecycle command. Pure: the whole
    of what the CLI's arguments become, so that `up --no-amp` through the
    agent is the `up --no-amp` the CLI would run itself."""
    if command == "up":
        body: dict[str, Any] = {
            "config_name": args.config_name, "rig": args.rig, "headless": args.headless,
            "tuner": args.tuner, "amp": args.amp,
        }
        if args.amp_model is not None:
            body["amp_model"] = model_for_agent(args.amp_model)
        return "/rig/up", body
    if command == "down":
        return "/rig/down", {"force": args.force}
    if command == "restart":
        # Refused by the agent when it saw changes since the last save, which
        # the restart would drop; `--discard` is saying to drop them.
        return "/rig/restart", {"discard": True} if getattr(args, "discard", False) else {}
    raise ValueError(f"not a lifecycle command: {command!r}")


def model_for_agent(model: str, root: Any = None) -> str:
    """`--amp-model` as the agent takes it: a bare name as it is (a file in
    amp/models), a path made absolute against the checkout root, which is
    where `amp.prepare` resolves a relative one when the CLI acts directly.
    The agent, whose working directory is not this shell's, takes a path only
    from this machine and only absolute."""
    from pathlib import Path

    from .paths import checkout_root

    if "/" not in model:
        return model
    path = Path(model)
    return str(path if path.is_absolute() else Path(root or checkout_root()) / path)


def lifecycle_result(status: int, body: Any) -> tuple[int, str, str]:
    """What the CLI prints for the agent's answer, as (exit status, stdout,
    stderr): the command's own output and warnings, as the CLI would have
    printed them itself, or the reason it failed. Pure."""
    if not isinstance(body, dict):
        return 1, "", f"the agent answered {status} with something that is not an object\n"
    output = body.get("output") if isinstance(body.get("output"), str) else ""
    if status == 200 and body.get("ok") is True:
        warnings = body.get("warnings") if isinstance(body.get("warnings"), list) else []
        return 0, output, "".join(f"warning: {w}\n" for w in warnings)
    reason = body.get("reason") or f"the agent answered {status}"
    if status == 400:
        # The agent takes a name where the CLI also takes a path (an amp
        # model), so a refusal of the request's shape is not the last word.
        reason += "\n(refused by the agent; 'sushi-rig <command> --direct' acts without it)"
    return 1, output, reason.rstrip("\n") + "\n"


def status_text(health: dict[str, Any]) -> str:
    """`status` from the agent's /health: what `status` prints from the state
    file, then each child the agent saw crash and each problem it found (a
    lost group holder, a start that left processes), which the state file
    cannot say. Pure."""
    from .rig import status_lines

    lines = status_lines(health.get("rig") or {"message": "the agent did not say", "running": False})
    supervisor = (health.get("agent") or {}).get("supervisor") or {}
    for crash in supervisor.get("crashed") or []:
        how = f"killed by {crash['signal']}" if crash.get("signal") else f"exited {crash.get('returncode')}"
        lines.append(f"  CRASHED    {crash.get('name')} (pid {crash.get('pid')}) {how} at {crash.get('at')}"
                     f"{' — see ' + crash['log'] if crash.get('log') else ''}")
    for problem in supervisor.get("problems") or []:
        lines.append(f"  PROBLEM    {problem.get('message')}")
    if supervisor.get("note"):
        lines.append(f"  note       {supervisor['note']}")
    return "\n".join(lines) + "\n"


def route(
    command: str,
    args: Any,
    *,
    base: str | None = None,
    out: TextIO | None = None,
    err: TextIO | None = None,
) -> int | None:
    """Do `command` through the agent if one is running, and return the exit
    status; or say that none is and return None, for the caller to act
    directly. `Unusable` is the caller's to refuse with. Every path says, in
    one line on stderr, which it took."""
    out = out or sys.stdout
    err = err or sys.stderr
    if base is None:
        base, source = find_agent()
    else:
        source = "as given"
    try:
        health = probe(base)
    except Absent:
        print(f"sushi-rig: no agent is running at {base} ({source}), so acting directly", file=err)
        return None
    print(f"sushi-rig: asking the agent at {base}", file=err)
    if command == "status":
        out.write(status_text(health))
        return 0
    path, body = lifecycle_request(command, args)
    try:
        status, answer = ask(base, "POST", path, body)
    except Absent as exc:
        # Gone between the look and the request. Acting directly now could
        # start a second rig beside whatever the agent had begun, so no.
        raise Unusable(
            f"the agent at {base} went away before it answered; what it did, if "
            "anything, is in 'sushi-rig status --direct'"
        ) from exc
    code, said, problems = lifecycle_result(status, answer)
    out.write(said)
    err.write(problems)
    return code
