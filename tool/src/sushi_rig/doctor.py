"""Will this machine run the rig properly, and what to do if it will not.

Issue #48. Setting the rig up on a second machine cost a multi-hour session, and
every fault was the same kind of thing: an undeclared dependency on *the
machine* rather than something the rig stated for itself. Not one of them failed
loudly. The amp was dead for the whole session and the rig played dry; a webcam
was clocking the graph at a 2048 quantum, giving 42.69 ms; the tuner had dragged
that webcam into the rig's clock domain.

`doctor` is the counterpart to `selftest`, and the split is worth stating because
it decides where each check belongs: **doctor asks whether the environment
*can* work, selftest proves the rig *does*.** So the quantum appears in both and
means something different in each. Here it is the value Sushi *asked the graph
for*, read off `node.force-quantum`; there it is the delay actually measured
through the graph. A pw-dump cannot give the second — the running quantum lives
in PipeWire's settings metadata, not on the node — and the issue's wording ("the
quantum the rig will actually get") asks for something only a measurement
answers. Reporting a request as though it were a result is the kind of
near-enough that this project keeps paying for, so each says which it is.

## Three states, not two

Every check is `ok`, `fault` or `unchecked`, and the third is load-bearing: most
of these questions cannot be answered at all with the rig down or PipeWire
absent, and answering "ok" there would be the loudest lie the command could
tell. `patchbay.NodeCheck` learned this the expensive way and its docstring is
worth reading — a bucket that neither passes nor fails is only honest while the
thing really is unknown. Only a `fault` fails the command.

## Every fault carries a remedy

Principle 7 applied to setup: the rig is played by a musician, not operated by
an engineer. A check that reports `node.force-quantum` is 2048 and stops there
has told the wrong person a true thing. `remedy` is a field rather than prose
inside `message` so that it cannot quietly go missing.

## Decisions here, effects in `gather_environment`

The checks are pure functions over facts someone else collected, so each is
pinned by a test — and the graph half is pinned against a *real* capture,
`tests/fixtures/pw-dump-rig.json`, taken with the rig running and healthy.
Where a test needs the unhealthy case it derives it from that capture by
changing one field, and says which field.
"""

from __future__ import annotations

import ctypes
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

# The graph rate everything here assumes. Sushi's configs declare 48000, the
# `.nam` models are trained at it, and a mismatch degrades tone without saying
# anything -- which is why it is checked rather than trusted.
EXPECTED_RATE = 48000

# `node.name` of the nodes the rig puts in the graph. Sushi is the one that must
# be there; the other two are the amp, which is optional.
SUSHI_NODE = "sushi"
AMP_NODES = ("Carla", "NAM")

# Bytes 8..10 of a type-2 AppImage, which is how `./sushi` ships. Checked by
# magic rather than by filename: the rig's `./sushi` is a symlink, and a
# directory extracted with `--appimage-extract` is a perfectly good `./sushi`
# that needs no FUSE at all.
APPIMAGE_MAGIC = b"AI\x02"

# What the AppImage's own loader asks for. FUSE 3 does not satisfy it, so this
# is checked by soname and not by asking whether "fuse" is installed -- a
# FUSE-3-only machine has fuse installed and still cannot run the AppImage.
FUSE2_SONAME = "libfuse.so.2"

OK = "ok"
FAULT = "fault"
UNCHECKED = "unchecked"


@dataclass(frozen=True)
class Check:
    """One thing doctor looked at.

    `name` is stable and machine-checkable, the way `profile.Finding.code` is:
    tests match on it, so rewording a message cannot silently change what a
    test asserts. `message` says what was measured. `remedy` says what to do,
    and is empty only when there is nothing to do.
    """

    name: str
    state: str
    message: str
    remedy: str = ""

    def __str__(self) -> str:
        label = {OK: "ok", FAULT: "FAULT", UNCHECKED: "unchecked"}[self.state]
        line = f"{label:>9}  {self.name}: {self.message}"
        return f"{line}\n           -> {self.remedy}" if self.remedy else line


def faults(checks: list[Check]) -> list[Check]:
    return [c for c in checks if c.state == FAULT]


def unchecked(checks: list[Check]) -> list[Check]:
    return [c for c in checks if c.state == UNCHECKED]


# --------------------------------------------------------------------------- #
# The live graph
# --------------------------------------------------------------------------- #

def _named(nodes: dict[int, dict], node_name: str) -> dict | None:
    return next(
        (props for props in nodes.values() if props.get("node.name") == node_name), None
    )


def _as_int(value) -> int | None:
    """PipeWire writes these as JSON numbers, but a node property is whatever
    was put there. `True` is an int in Python and must not read as 1."""
    return value if type(value) is int else None


def interface_clients(role_ports: dict[str, list[str]]) -> set[str]:
    """The device names the machine file's roles mention.

    Taken from the ports rather than from the file's `interface:` key, which
    documents itself as informational — making it load-bearing here would change
    what it means. The ports are the authoritative half: they are what the
    patchbay actually wires.
    """
    return {
        port.split(":", 1)[0]
        for ports in role_ports.values()
        for port in ports
        if isinstance(port, str) and ":" in port
    }


def driver_names(props: dict) -> set[str]:
    """Every name a node might be recognised by, for matching against the
    machine file. Generous on purpose, the same way `live_node_names` is: the
    Audient answers to `node.nick` and `api.alsa.card.name` as
    "Audient iD14" and to `node.description` as "Audient iD14 Multichannel",
    and the machine file names the first form."""
    return {str(props[key]) for key in ("node.nick", "api.alsa.card.name",
                                        "node.description") if props.get(key)}


def describe_node(props: dict) -> str:
    for key in ("node.description", "node.nick", "node.name"):
        if props.get(key):
            return str(props[key])
    return "an unnamed node"


def check_clock_driver(nodes: dict[int, dict], clients: set[str]) -> Check:
    """Is the rig clocked by its own interface, or by something else entirely?

    The failure: `fmit` reaches PipeWire through the ALSA compatibility layer,
    which auto-connects to the *default source* — on that machine a Logitech
    BRIO webcam. The patchbay also wired fmit to the Audient, so the tuner
    bridged the two devices into one driver group and every rig node, Sushi
    included, ended up carrying the webcam's `node.driver-id`. A guitar rig
    clocked by a webcam, resampled to its clock and inheriting its xruns.

    Followed through `node.driver-id` from Sushi, and **not** by looking for a
    node with `node.driver = True`. In the healthy capture the webcam carries
    that flag too — it drives its own group — so a check that went looking for
    the driver would have found two and had nothing to choose between them.
    """
    if not clients:
        # Reported as unchecked rather than as a fault naming no devices: the
        # machine file has its own check, and one unreadable file should produce
        # one finding, not a cascade of them.
        return Check(
            "clock-driver", UNCHECKED,
            "the machine file names no devices, so there is nothing to compare "
            "the rig's clock against",
            "fix the machine file first — `sushi-rig machine check`",
        )
    sushi = _named(nodes, SUSHI_NODE)
    if sushi is None:
        return Check(
            "clock-driver", UNCHECKED,
            "Sushi is not in the graph, so nothing is clocking it yet",
            "run `sushi-rig up` and check again",
        )
    driver_id = _as_int(sushi.get("node.driver-id"))
    if driver_id is None:
        return Check(
            "clock-driver", FAULT,
            "Sushi is running but is in no driver group, so it is not being "
            "scheduled with anything",
            "bring the rig down and up again; if it persists, check that the "
            "interface is present with `sushi-rig patchbay check`",
        )
    driver = nodes.get(driver_id)
    if driver is None:
        return Check(
            "clock-driver", FAULT,
            f"Sushi names driver node {driver_id}, which is not in the graph",
            "bring the rig down and up again — the device it was clocked by has gone",
        )
    names = driver_names(driver)
    if names & clients or any(
        name.startswith(f"{client} ") for name in names for client in clients
    ):
        return Check(
            "clock-driver", OK,
            f"the rig is clocked by {describe_node(driver)}, which the machine "
            f"file names",
        )
    return Check(
        "clock-driver", FAULT,
        f"the rig is clocked by {describe_node(driver)}, which is not a device "
        f"the machine file names ({', '.join(sorted(clients)) or 'it names none'}) "
        f"— the audio path is being resampled to that device's clock and inherits "
        f"its xruns",
        "something has bridged another device into the rig's driver group. The "
        "tuner is the usual culprit: it must carry "
        "PIPEWIRE_PROPS='{ node.autoconnect = false }' so its routing comes from "
        "the patchbay. Bring the rig down and up again after fixing it — a driver "
        "group is settled at startup",
    )


def check_one_clock(nodes: dict[int, dict]) -> Check:
    """Sushi and the amp have to be in the same driver group.

    They are separate JACK clients and each locks a quantum, so two groups is a
    fight nobody wins. It is also what makes the amp free: PipeWire schedules
    both inside one graph cycle, so NAM in front of Sushi measured as adding no
    latency at all. Different groups would end that silently.

    This check is only about *agreement*. It says nothing about whether an amp
    ought to be running, because a pw-dump does not carry the config — a dead
    amp is `selftest`'s to catch, by measuring that nothing is being modelled.
    """
    sushi = _named(nodes, SUSHI_NODE)
    if sushi is None:
        return Check(
            "one-clock", UNCHECKED, "Sushi is not in the graph",
            "run `sushi-rig up` and check again",
        )
    groups: dict[str, int | None] = {SUSHI_NODE: _as_int(sushi.get("node.driver-id"))}
    for name in AMP_NODES:
        props = _named(nodes, name)
        if props is not None:
            groups[name] = _as_int(props.get("node.driver-id"))
    if len(groups) == 1:
        return Check(
            "one-clock", OK,
            f"only Sushi is in the graph ({', '.join(AMP_NODES)} are absent), so "
            f"there is nothing to be out of step with",
        )
    distinct = set(groups.values())
    if len(distinct) == 1:
        return Check(
            "one-clock", OK,
            f"{', '.join(groups)} are all in driver group {distinct.pop()}",
        )
    return Check(
        "one-clock", FAULT,
        "the rig's nodes are in different driver groups ("
        + ", ".join(f"{name} in {group}" for name, group in groups.items())
        + "), so they are not scheduled in the same graph cycle and each is "
        "locking its own quantum",
        "bring the rig down and up again so they start together; a driver group "
        "is settled at startup and cannot be changed underneath a running client",
    )


def check_quantum(nodes: dict[int, dict], expected: int) -> Check:
    """Did Sushi ask the graph for the buffer size the rig states?

    Nothing in the rig used to set this, so on a fresh machine it took whatever
    the desktop was at — `clock.max-quantum`, 2048, measured as 42.69 ms through
    Sushi and reported by nothing. `up` now states it per child, and this is the
    check that it took: a `PIPEWIRE_QUANTUM` already in the environment used to
    win and put the rig straight back on the desktop's buffer size.

    What this reads is the *request*. The quantum the graph actually runs at is
    in PipeWire's settings metadata, not on the node, and the delay it produces
    is `sushi-rig selftest`'s to measure.
    """
    sushi = _named(nodes, SUSHI_NODE)
    if sushi is None:
        return Check(
            "quantum", UNCHECKED, "Sushi is not in the graph",
            "run `sushi-rig up` and check again",
        )
    asked = _as_int(sushi.get("node.force-quantum"))
    locked = sushi.get("node.lock-quantum") is True
    if sushi.get("node.force-quantum") is not None and asked is None:
        return Check(
            "quantum", FAULT,
            f"Sushi's node.force-quantum is {sushi['node.force-quantum']!r}, which "
            f"is not a block size",
            "this is PipeWire reporting something unexpected; capture a `pw-dump` "
            "before changing anything",
        )
    if asked is None:
        return Check(
            "quantum", FAULT,
            "Sushi is not forcing a quantum, so the graph's own value applies — on "
            "a fresh machine that was 2048, which measured 42.69 ms through Sushi "
            "and was audible as latency",
            f"start the rig with `sushi-rig up`, which sets "
            f"PIPEWIRE_QUANTUM={expected}/{EXPECTED_RATE} on Sushi and the amp. If "
            f"you started Sushi by hand, set it yourself",
        )
    if asked != expected:
        return Check(
            "quantum", FAULT,
            f"Sushi asked for a quantum of {asked}, not the {expected} the rig "
            f"states ({1000.0 * asked / EXPECTED_RATE:.2f} ms a block against "
            f"{1000.0 * expected / EXPECTED_RATE:.2f})",
            "something in the environment overrode PIPEWIRE_QUANTUM — it is "
            "assigned per child by `up`, so an inherited value should not win. "
            "Check for it in your shell profile, then bring the rig down and up",
        )
    return Check(
        "quantum", OK,
        f"Sushi asked for {asked} samples "
        f"({1000.0 * asked / EXPECTED_RATE:.2f} ms a block)"
        + (" and locked it, so a restart is the only way to change it" if locked
           else " but did not lock it"),
    )


def check_rate(nodes: dict[int, dict], expected: int = EXPECTED_RATE) -> Check:
    """Did Sushi ask for the sample rate everything else assumes?

    A rate mismatch between the graph, the configs and the `.nam` models
    degrades tone and says nothing, which is the whole reason it is here.
    """
    sushi = _named(nodes, SUSHI_NODE)
    if sushi is None:
        return Check(
            "rate", UNCHECKED, "Sushi is not in the graph",
            "run `sushi-rig up` and check again",
        )
    asked = _as_int(sushi.get("node.force-rate"))
    if asked is None:
        return Check(
            "rate", FAULT,
            "Sushi is not forcing a sample rate, so the graph's own applies and a "
            "mismatch with the configs would be silent",
            f"start the rig with `sushi-rig up`, which asks for {expected} Hz",
        )
    if asked != expected:
        return Check(
            "rate", FAULT,
            f"Sushi asked for {asked} Hz, not the {expected} Hz the configs and "
            f"the amp models are built for",
            f"the configs declare {expected}; if this machine genuinely runs at "
            f"{asked}, that is a decision to make deliberately and record, not to "
            f"discover by ear",
        )
    return Check("rate", OK, f"Sushi asked for {asked} Hz")


def graph_checks(
    nodes: dict[int, dict] | None,
    clients: set[str],
    *,
    expected_quantum: int,
    expected_rate: int = EXPECTED_RATE,
    reason: str = "",
) -> list[Check]:
    """Everything the live graph can answer.

    `nodes=None` means the graph could not be read at all, and comes back as one
    `unchecked` naming the four questions rather than four identical lines — the
    reader needs to know what went unanswered, not to read the same sentence
    four times.
    """
    if nodes is None:
        return [Check(
            "pipewire-graph", UNCHECKED,
            f"the live graph could not be read, so the clock's driver, the rig's "
            f"driver group, the quantum and the rate all went unchecked"
            + (f": {reason}" if reason else ""),
            "these need PipeWire and a running rig; on a machine with neither "
            "there is nothing here to check",
        )]
    return [
        check_clock_driver(nodes, clients),
        check_one_clock(nodes),
        check_quantum(nodes, expected_quantum),
        check_rate(nodes, expected_rate),
    ]


def machine_check(problems: list[str], *, file_name: str, readable: bool = True,
                  why: str = "") -> Check:
    """The machine file, which is where every other device name comes from.

    A fault here is reported once and the checks that depend on it go
    `unchecked`, rather than each inventing its own version of the same problem.
    """
    if not readable:
        return Check(
            "machine-file", FAULT, f"{file_name} could not be read: {why}",
            "copy machines/example.yaml to machines/<hostname>.yaml and edit the "
            "ports — `hostname` prints the name this machine needs",
        )
    if problems:
        return Check(
            "machine-file", FAULT,
            f"{file_name} has {len(problems)} problem(s): " + "; ".join(problems),
            "`sushi-rig machine check` reports them on their own",
        )
    return Check("machine-file", OK, f"{file_name} is valid")


def patchbay_checks(missing: dict, not_running: dict, *, file_name: str,
                    links: int) -> list[Check]:
    """What the saved patchbay's node names did against the live graph.

    Takes the two dictionaries rather than `patchbay.NodeCheck` itself, so this
    module depends on nothing of that one's shape. The three states are its
    three states, and for its reasons: a node the *rig* starts is absent
    whenever the rig is down, which says nothing about the patchbay, while a
    node that is simply gone keeps its links for ever and is never connected.
    """
    checks = []
    if missing:
        checks.append(Check(
            "patchbay-nodes", FAULT,
            f"{file_name} names {len(missing)} node(s) that are not in the graph ("
            + ", ".join(
                f"{node!r}, {len(node_links)} link(s)"
                for node, node_links in sorted(missing.items())
            )
            + ") — qpwgraph keeps those links and never connects them",
            "regenerate it with `sushi-rig patchbay generate`, which writes the "
            "node names from the live graph rather than from whatever the device "
            "was called last time",
        ))
    if not_running:
        checks.append(Check(
            "patchbay-nodes", UNCHECKED,
            f"{len(not_running)} node(s) {file_name} names are started by the rig "
            f"itself, so their absence says nothing while the rig is down",
            "run `sushi-rig up` and check again to cover those links",
        ))
    if not checks:
        checks.append(Check(
            "patchbay-nodes", OK,
            f"all {links} link(s) in {file_name} name a node that exists",
        ))
    return checks


# --------------------------------------------------------------------------- #
# The machine around it
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class Environment:
    """What was found on this machine, gathered once so the checks stay pure."""

    pw_jack: str | None
    carla: str | None
    jackd_pids: tuple[int, ...]
    fuse2: bool
    # None when there is no ./sushi to look at.
    sushi_is_appimage: bool | None


def check_pw_jack(environment: Environment) -> Check:
    """`pw-jack` has to be there, because both JACK clients are started under it.

    Carla was launched as plain `carla` for a whole session. It links against
    the system libjack, which on a PipeWire-only machine finds no server and
    exits during startup — "jack server is not running or cannot be started",
    in the amp log and nowhere else. The rig reported READY and played dry.
    """
    if environment.pw_jack:
        return Check("pw-jack", OK, f"present at {environment.pw_jack}")
    return Check(
        "pw-jack", FAULT,
        "pw-jack is not on PATH. Sushi and the amp are both started under it, and "
        "without it Carla finds no JACK server and exits during startup — which "
        "leaves the rig reporting READY and playing dry",
        "install PipeWire's JACK compatibility package (pipewire-jack on Debian "
        "and Ubuntu)",
    )


def check_no_jackd(environment: Environment) -> Check:
    """Which JACK is in play — the invisible difference between two machines.

    A real jackd alongside PipeWire's JACK layer means `pw-jack` is putting a
    shim in front of a server that is also running, and which one a client
    reaches stops being predictable.
    """
    if not environment.jackd_pids:
        return Check("jackd", OK, "no jackd is running, so PipeWire's JACK layer is "
                                  "the only one in play")
    pids = ", ".join(str(pid) for pid in environment.jackd_pids)
    return Check(
        "jackd", FAULT,
        f"a real jackd is running (pid {pids}) alongside PipeWire's JACK layer, so "
        f"which server a client reaches is not predictable",
        "stop jackd and let PipeWire serve JACK, or decide deliberately that this "
        "machine runs jackd and record that — the rig assumes PipeWire",
    )


def check_carla(environment: Environment) -> Check:
    """Only the amp needs it, so this is a fault only in what it costs you."""
    if environment.carla:
        return Check("carla", OK, f"present at {environment.carla}")
    return Check(
        "carla", FAULT,
        "carla is not on PATH, so the neural amp cannot run — Sushi cannot set "
        "NAM's model itself, which is why the amp lives in Carla at all",
        "install carla, or run without the amp (`sushi-rig up --no-amp`), which "
        "says so rather than playing dry",
    )


def check_fuse(environment: Environment) -> Check:
    """The AppImage needs FUSE 2, and says so in a message easily discarded.

    Observed directly: `./Sushi-x86_64.AppImage --version` on a machine without
    it prints "dlopen(): error loading libfuse.so.2 / AppImages require FUSE to
    run." and exits — and a tool that captured only stderr, or only the exit
    code, would report nothing at all.
    """
    if environment.sushi_is_appimage is None:
        return Check(
            "fuse2", UNCHECKED, "there is no ./sushi to run",
            "see the README's Plugins and portability section for where Sushi "
            "comes from",
        )
    if not environment.sushi_is_appimage:
        return Check("fuse2", OK, "./sushi is not an AppImage, so FUSE is not needed")
    if environment.fuse2:
        return Check("fuse2", OK, f"{FUSE2_SONAME} loads, so the AppImage can mount itself")
    return Check(
        "fuse2", FAULT,
        f"./sushi is an AppImage and {FUSE2_SONAME} cannot be loaded, so it will "
        f'exit with "AppImages require FUSE to run". FUSE 3 does not satisfy it',
        "install FUSE 2 (libfuse2 on Debian and Ubuntu), or extract the AppImage "
        "once with `--appimage-extract` and point ./sushi at the extracted AppRun, "
        "which needs no FUSE at all",
    )


def environment_checks(environment: Environment) -> list[Check]:
    return [
        check_pw_jack(environment),
        check_no_jackd(environment),
        check_carla(environment),
        check_fuse(environment),
    ]


# --------------------------------------------------------------------------- #
# Effects
# --------------------------------------------------------------------------- #

def _jackd_pids() -> tuple[int, ...]:
    """Running jackd processes, by pid.

    `pgrep -x`, so a command merely *containing* "jackd" does not count — this
    project has killed its own shell with a loose pattern before now, and the
    same looseness here would report a fault on a machine reading this file.
    """
    try:
        result = subprocess.run(
            ["pgrep", "-x", "jackd"], capture_output=True, text=True, check=False,
            timeout=5.0,
        )
    except (OSError, subprocess.SubprocessError):
        # No pgrep, or it could not be run. Reported as "none found" rather than
        # guessed at; the check that matters most here is the one above it.
        return ()
    return tuple(
        int(line) for line in result.stdout.split() if line.strip().isdigit()
    )


def _is_appimage(path: Path) -> bool | None:
    """Whether `path` is a type-2 AppImage, by magic. None when it is not there."""
    try:
        with path.open("rb") as handle:
            head = handle.read(11)
    except OSError:
        return None
    return head[8:11] == APPIMAGE_MAGIC


def _fuse2_loads() -> bool:
    try:
        ctypes.CDLL(FUSE2_SONAME)
    except OSError:
        return False
    return True


def gather_environment(root: Path) -> Environment:
    """Look at the machine. The one impure function here."""
    return Environment(
        pw_jack=shutil.which("pw-jack"),
        carla=shutil.which("carla"),
        jackd_pids=_jackd_pids(),
        fuse2=_fuse2_loads(),
        sushi_is_appimage=_is_appimage(root / "sushi"),
    )
