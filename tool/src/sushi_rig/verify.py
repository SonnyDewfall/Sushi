"""Does this config actually do what it says?

Three questions, cheapest first:

1. **Does it load?** `sushi --dump-plugins` over it. Catches a missing plugin, a
   bad URI, a malformed file.
2. **Do the names exist?** Every parameter in `initial_state` is one its
   processor actually has.
3. **Does it apply?** Start Sushi, read back what the rig is doing, and compare
   it to what the file claims.

The third is the one that earns this module. Twice a config has loaded
perfectly, named only things that exist, and quietly not done what it said — a
track's `bypassed` cascading over every plugin on it, and a `program` index
applied over the `parameters` written beside it. Both presented as "saving is
broken" and were load-time bugs. Both were invisible because everything *else*
in the file applied correctly.

Checks 1 and 2 would have caught neither. Comparing the running rig against the
file catches both, and named the exact parameters in seconds.

None of this needs audio hardware. Sushi's `--dummy` frontend runs with no
audio device at all, still serves gRPC, and still applies `initial_state` —
which is what makes this runnable over every config in the repo, on any machine.
"""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from difflib import get_close_matches
from pathlib import Path
from typing import Any

from .dump import collect_parameter_names, dump_plugins
from .paths import checkout_root
from .procs import pgid_alive, port_is_open

# Values are rounded to 6dp on write and Sushi quantises, so an exact comparison
# would report differences that are not differences.
TOLERANCE = 0.005

# How long to wait for the headless instance to answer, and to die afterwards.
START_TIMEOUT = 15.0
STOP_TIMEOUT = 5.0


@dataclass(frozen=True)
class Problem:
    """One difference between a config and reality."""

    processor: str
    detail: str

    def __str__(self) -> str:
        return f"{self.processor}: {self.detail}"


# --- the pure part ----------------------------------------------------------


def diff_state(config: dict[str, Any], live: dict[str, Any]) -> list[Problem]:
    """Every way the running rig differs from the config that describes it.

    `live` is exactly what `live.capture()` returns, so this is pure dictionary
    comparison — which is the point. The decision is testable without Sushi,
    and both bugs that prompted this module are unit tests.

    Reports every difference rather than the first, following `RigSpec.validate`:
    at rig scale you want the whole list, not one fix cycle per problem.
    """
    problems: list[Problem] = []
    tracks = {t.get("name") for t in config.get("tracks", [])}
    processors = live.get("processors") or {}

    for entry in config.get("initial_state", []):
        name = entry.get("processor")
        actual = processors.get(name)
        if actual is None:
            problems.append(Problem(name, "in the config, but not in the running rig"))
            continue

        for param, expected in (entry.get("parameters") or {}).items():
            got = (actual.get("parameters") or {}).get(param)
            if got is None:
                problems.append(Problem(name, f"{param} is not a parameter of this processor"))
            elif abs(got - expected) > TOLERANCE:
                problems.append(Problem(
                    name, f"{param} did not apply — config {expected:.4f}, rig {got:.4f}"
                ))

        # Tracks report no bypass state at all, so comparing one produces a
        # difference that is not a difference. Found by running this: a track
        # entry saying `false` came back as `None`.
        if name not in tracks and entry.get("bypassed") is not None:
            if actual.get("bypassed") != entry["bypassed"]:
                problems.append(Problem(
                    name,
                    f"bypass did not apply — config {entry['bypassed']}, "
                    f"rig {actual.get('bypassed')}",
                ))

    for track in config.get("tracks", []):
        expected_chain = [p.get("name") for p in track.get("plugins", [])]
        actual_chain = (live.get("tracks") or {}).get(track.get("name"))
        if actual_chain is None:
            problems.append(Problem(track.get("name"), "track is missing from the running rig"))
        elif actual_chain != expected_chain:
            problems.append(Problem(
                track.get("name"),
                f"chain order differs — config {expected_chain}, rig {actual_chain}",
            ))

    return problems


def check_names(config: dict[str, Any], dump: Any) -> list[Problem]:
    """Every parameter named in `initial_state` exists on its processor.

    Reuses `collect_parameter_names`, which already indexes exactly this from
    Sushi's own introspection — the authoritative answer, since Sushi assigns
    parameter names from TTL metadata rather than using the port symbol.

    Suggests a near miss on failure, because the usual mistake is a case
    difference or a symbol-versus-name mix-up rather than an invented name.
    """
    problems: list[Problem] = []
    known = collect_parameter_names(dump)
    tracks = {t.get("name") for t in config.get("tracks", [])}

    for entry in config.get("initial_state", []):
        name = entry.get("processor")
        # Tracks carry parameters (gain, pan, mute) but are not in the plugin
        # dump, so there is nothing to check them against.
        if name in tracks:
            continue
        available = known.get(name)
        if available is None:
            problems.append(Problem(name, "no such processor in this config"))
            continue
        for param in (entry.get("parameters") or {}):
            if param not in available:
                near = get_close_matches(param, sorted(available), n=1, cutoff=0.6)
                hint = f" — did you mean {near[0]!r}?" if near else \
                       f" — it has: {', '.join(sorted(available))}"
                problems.append(Problem(name, f"no parameter named {param!r}{hint}"))
    return problems


# --- running a config to see what it does -----------------------------------


def free_port() -> int:
    """A port nothing is using, asked of the OS rather than guessed.

    Never the rig's own defaults. A verify run must not collide with a rig
    someone is playing through — during development a hard-coded port meant a
    test silently queried the *running* rig instead of its own instance, and
    reported on the wrong thing entirely.
    """
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def capture_headless(config: Path, sushi_bin: str, root: Path) -> dict[str, Any]:
    """Load `config` in a throwaway Sushi and read back what it is doing.

    `--dummy` is what makes this possible anywhere: no audio device, no JACK, no
    PipeWire, and `initial_state` still applied.

    The instance gets its own process group and is killed in a `finally`. A
    verify that leaves a Sushi behind holds a port and could poison a later
    `sushi-rig up`.

    A `finally` does not run on SIGKILL, so that was tested rather than assumed:
    killing a `verify --all` at three different points always left the child
    dead within a few seconds. The likely reason is this function holding
    Sushi's stdout — when the parent dies the read end closes and Sushi's next
    write fails — but that is an observation about Sushi's behaviour rather than
    a guarantee this code makes.
    """
    from .live import capture

    grpc_port = free_port()
    env = dict(os.environ)
    # plugins/ alone, as the rig runs. A wider path would hide exactly the
    # portability break the rig cares about.
    env["LV2_PATH"] = str(root / "plugins")

    process = subprocess.Popen(
        [sushi_bin, "-d", "-c", str(config),
         f"--grpc-address=127.0.0.1:{grpc_port}",
         f"--osc-rcv-port={free_port()}"],
        cwd=root, env=env, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, start_new_session=True,
    )
    try:
        deadline = time.monotonic() + START_TIMEOUT
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise VerifyError(
                    f"Sushi exited before it could be read:\n{process.stdout.read().strip()}"
                )
            if port_is_open("127.0.0.1", grpc_port):
                break
            time.sleep(0.2)
        else:
            raise VerifyError(f"Sushi never answered on port {grpc_port}")

        return capture(f"127.0.0.1:{grpc_port}")
    finally:
        _stop(process)


def _stop(process: subprocess.Popen) -> None:
    try:
        pgid = os.getpgid(process.pid)
    except ProcessLookupError:
        return
    for sig in (signal.SIGINT, signal.SIGKILL):
        try:
            os.killpg(pgid, sig)
        except ProcessLookupError:
            return
        deadline = time.monotonic() + STOP_TIMEOUT / 2
        while time.monotonic() < deadline:
            if not pgid_alive(pgid):
                return
            time.sleep(0.05)


class VerifyError(Exception):
    """Verification could not be carried out — distinct from it failing."""


# --- the checks, run together ------------------------------------------------


def _without_state(config: Path) -> Path:
    """The config with `initial_state` removed, written somewhere temporary.

    See `verify` for why. The file is left in place — it lives in the system
    temporary directory and is a few kilobytes.
    """
    import tempfile

    parsed = json.loads(Path(config).read_text())
    parsed.pop("initial_state", None)
    handle = tempfile.NamedTemporaryFile(
        "w", suffix=".json", prefix=f"verify-{Path(config).stem}-", delete=False
    )
    with handle:
        json.dump(parsed, handle)
    return Path(handle.name)


def verify(config: Path, sushi_bin: str = "sushi", quick: bool = False) -> list[Problem]:
    """Run the checks over one config and return everything wrong with it."""
    root = checkout_root()
    config = Path(config)
    if not config.is_file():
        raise VerifyError(f"no such config: {config}")

    parsed = json.loads(config.read_text())

    # The plugin dump is taken from the config with `initial_state` removed.
    #
    # Sushi validates parameter names as it loads them and refuses the whole
    # config if one is wrong — exit 7, "Failed to load the initial processor
    # states", with no indication of which parameter or which processor. So
    # dumping the config as written tells us only that *something* is wrong,
    # and that is precisely the failure this check exists to explain.
    #
    # Stripped of its state the config always loads, the dump lists every
    # parameter each processor really has, and the offending name can be named
    # along with a near miss.
    dump = dump_plugins(_without_state(config), sushi_bin)

    problems = check_names(parsed, dump)
    if quick:
        return problems

    return problems + diff_state(parsed, capture_headless(config, sushi_bin, root))


def configs_to_check(root: Path) -> list[Path]:
    """Every config worth verifying: `config/*.json`, and nothing retired.

    `config/retired/` is kept for recovery only and deliberately references
    plugins the rig no longer has, so checking it would report failures that are
    the whole point of retiring it.
    """
    return sorted((root / "config").glob("*.json"))
