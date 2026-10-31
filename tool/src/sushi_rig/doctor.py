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
import json
import math
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

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

# What a take costs on disk, for the free-space check. Each channel records two
# stems, what you heard and its dry tap; the channel count comes from the
# largest rig in config/, because rigs will have one, two or more channels and a
# fixed guess would be wrong for every one that is not the guess. float32 is the
# larger of the two formats #53 chooses between, so the estimate is cautious.
STEMS_PER_CHANNEL = 2
TAKE_BYTES_PER_SAMPLE = 4
# Below this much recording time the disk is a fault, not a footnote: a take
# that runs out of space is a performance lost, and #53 assigns the check here
# so it is found before playing rather than mid-take.
MIN_RECORDING_HOURS = 1.0

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
    if _as_int(sushi.get("node.driver-id")) is None:
        # `clock-driver` reports this one. Saying it twice, and saying it here as
        # "all in driver group None", is how the cascade this module avoids
        # elsewhere would creep back in.
        return Check(
            "one-clock", UNCHECKED,
            "Sushi is in no driver group, so there is no group for the amp to "
            "share — clock-driver reports that",
            "bring the rig down and up again",
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
    is `selftest`'s to measure (#49, a module today and not yet a command).
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

    The two can both be true at once, so they are two checks with **two
    different names**. They shared one at first, which breaks the promise that
    a name is a stable key — anything building a dict by name silently kept
    whichever came last, and the first thing to do so was this module's own
    tests.
    """
    checks = []
    if missing:
        checks.append(Check(
            "patchbay-stale", FAULT,
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
            "patchbay-unchecked", UNCHECKED,
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
    # None when pgrep could not be run at all, which is not the same as finding
    # nothing. Collapsing the two gave a machine with no procps a confident
    # "no jackd is running" — the module's own three-state rule, broken by a
    # field that could not express the third state.
    jackd_pids: tuple[int, ...] | None
    fuse2: bool
    # None when there is no ./sushi to look at.
    sushi_is_appimage: bool | None
    # Free bytes where takes are written; None when that could not be read, and
    # `takes_problem` says why when the reason is itself a fault.
    takes_free_bytes: int | None = None
    takes_dir: str = ""
    takes_problem: str = ""
    # Stems a take of the largest rig in config/ records, and which rig that is.
    take_stems: int = 2
    take_rig: str = ""
    models: tuple["ModelInfo", ...] = ()


class ModelInfo(NamedTuple):
    """One amp model doctor looked at: what it declares, or why it could not
    be read. `used_by` names the configs that play it, empty for a model that
    is only in the library."""

    name: str
    rate: int | None
    error: str = ""
    used_by: tuple[str, ...] = ()


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
    if environment.jackd_pids is None:
        return Check(
            "jackd", UNCHECKED,
            "whether a jackd is running could not be determined — pgrep could not "
            "be run",
            "install procps, or check by hand with `ps` that no jackd is running "
            "beside PipeWire",
        )
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


def _duration(hours: float) -> str:
    """Rounded down, never up: a check that fails at 0.997 hours must not say
    "about 1.0 hour" in the same sentence as "under the 1 hour it asks for"."""
    if hours < 2:
        return f"about {math.floor(hours * 60)} minutes"
    return f"about {math.floor(hours * 10) / 10:g} hours"


def check_disk(environment: Environment, *, hours: float = MIN_RECORDING_HOURS) -> Check:
    """Is there room to record before playing, rather than discovering it mid-take."""
    where = environment.takes_dir or "the takes folder"
    if environment.takes_problem:
        return Check(
            "disk", FAULT, environment.takes_problem,
            "connect the drive, or point the link at a folder that exists — "
            "otherwise the first take fails as it starts",
        )
    free = environment.takes_free_bytes
    if free is None:
        return Check(
            "disk", UNCHECKED, f"how much space is free at {where} could not be read",
            "check it by hand with `df -h`",
        )
    stems = max(1, environment.take_stems)
    per_hour = stems * EXPECTED_RATE * TAKE_BYTES_PER_SAMPLE * 3600
    fits = free / per_hour
    rig = f" of {environment.take_rig}" if environment.take_rig else ""
    summary = (
        f"{free / 1e9:.1f} GB free at {where}: {_duration(fits)} of a {stems}-stem "
        f"take{rig} at {EXPECTED_RATE} Hz in float32"
    )
    if fits >= hours:
        return Check("disk", OK, summary)
    return Check(
        "disk", FAULT, summary + f", under the {hours:g} hour this check asks for",
        "free some space, or record somewhere with more of it — a take that runs out "
        "of disk is a performance lost",
    )


def check_models(environment: Environment, expected: int = EXPECTED_RATE) -> Check:
    """Was every amp model the rig can play trained at the rate it runs at?

    A model played at a rate it was not trained at degrades the tone and says
    nothing, which is why #48 asks for it. The rate is what the `.nam` file
    declares about itself (`amp.describe_model`). "Every model the rig can
    play" is the library *and* every model a config's `_amp` points at, which
    can be anywhere — checking the library alone would pass a config whose
    model was never looked at.

    Every problem in one run: a fault that named only the first kind left the
    others to be found on the next.
    """
    models = environment.models
    if not models:
        return Check("amp-models", OK, "no .nam models in the library or the configs, so none can mismatch")

    def who(model: ModelInfo) -> str:
        return f"{model.name} (played by {', '.join(model.used_by)})" if model.used_by else model.name

    unreadable = [m for m in models if m.error]
    wrong = [m for m in models if not m.error and m.rate not in (None, expected)]
    undeclared = [m for m in models if not m.error and m.rate is None]
    parts = [f"{who(m)} cannot be read: {m.error}" for m in unreadable]
    parts += [f"{who(m)} was trained at {m.rate} Hz, the rig runs at {expected} Hz" for m in wrong]
    parts += [f"{who(m)} declares no sample rate, so a mismatch cannot be ruled out" for m in undeclared]

    if unreadable or wrong:
        remedies = []
        if wrong:
            remedies.append(
                f"use a model trained at {expected} Hz — one at another rate changes the "
                "tone without saying so"
            )
        if unreadable:
            remedies.append("replace or remove the file that cannot be read, or point the config at one that can")
        return Check("amp-models", FAULT, "; ".join(parts), "; and ".join(remedies))
    if undeclared:
        return Check(
            "amp-models", UNCHECKED, "; ".join(parts),
            "check where the model came from, or retrain it with its rate recorded",
        )
    return Check("amp-models", OK, f"{len(models)} model(s), all trained at {expected} Hz")


def environment_checks(environment: Environment) -> list[Check]:
    return [
        check_pw_jack(environment),
        check_no_jackd(environment),
        check_carla(environment),
        check_fuse(environment),
        check_disk(environment),
        check_models(environment),
    ]


# --------------------------------------------------------------------------- #
# Effects
# --------------------------------------------------------------------------- #

def _jackd_pids() -> tuple[int, ...] | None:
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
        # No pgrep, or it could not be run. `None`, never `()`: this check does
        # not get to say "nothing is running" on the strength of not having
        # looked.
        return None
    return tuple(
        int(line) for line in result.stdout.split() if line.strip().isdigit()
    )


def _is_appimage(path: Path) -> bool | None:
    """Whether `path` is a type-2 AppImage, by magic.

    Three answers, because the two kinds of "cannot read it" mean opposite
    things. `None` is "there is nothing there", including a broken symlink.
    `False` covers a path that exists but is not a readable file — and a
    directory left by `--appimage-extract` is exactly that, a perfectly good
    `./sushi` that needs no FUSE. Mapping both to `None` reported "there is no
    ./sushi to run" for a working extracted one.
    """
    if not path.exists():
        return None
    try:
        with path.open("rb") as handle:
            head = handle.read(11)
    except OSError:
        return False
    return head[8:11] == APPIMAGE_MAGIC


def _fuse2_loads() -> bool:
    try:
        ctypes.CDLL(FUSE2_SONAME)
    except OSError:
        return False
    return True


def _free_bytes(path: Path) -> tuple[int | None, str]:
    """Free space where `path` is, or would be, and a fault when that is a lie.

    The takes folder is made by the first take, so before then it is the
    nearest folder that exists that tells how much room there is. Except when
    the path, or a folder above it, is a link to something missing — an
    external drive not plugged in. Walking past that measured the home disk
    instead, reported plenty of room, and the first take then failed as it
    started. A mount point with nothing mounted cannot be told from an empty
    folder, so it is still measured as whatever it sits on.
    """
    for candidate in (path, *path.parents):
        if candidate.is_symlink() and not candidate.exists():
            try:
                target = candidate.readlink()
            except OSError:
                target = "something that is not there"
            return None, (
                f"{candidate} is a link to {target}, which does not exist — is the "
                "drive takes are recorded to plugged in?"
            )
        if candidate.exists():
            try:
                return shutil.disk_usage(candidate).free, ""
            except OSError:
                return None, ""
    return None, ""


def _largest_rig(root: Path) -> tuple[int, str]:
    """The stems a take of the largest rig in config/ records, and its name."""
    from .verify import configs_to_check

    stems, name = STEMS_PER_CHANNEL, ""
    for config in configs_to_check(root):
        try:
            tracks = json.loads(config.read_text()).get("tracks", [])
        except (OSError, ValueError, AttributeError):
            continue
        count = STEMS_PER_CHANNEL * len([t for t in tracks if isinstance(t, dict)])
        if count > stems:
            stems, name = count, config.stem
    return stems, name


def _describe(path: Path, used_by: tuple[str, ...]) -> ModelInfo:
    from .amp import describe_model

    try:
        return ModelInfo(path.name, describe_model(path)["sample_rate"], "", used_by)
    except Exception as exc:  # noqa: BLE001 — see below
        # Broad on purpose. `describe_model` raises AmpError for what it
        # expects, but a malformed file can reach AttributeError or
        # OverflowError inside it, and one bad model must cost one line of
        # the report, never the whole run.
        return ModelInfo(path.name, None, f"{type(exc).__name__}: {exc}", used_by)


def _models(root: Path) -> tuple[ModelInfo, ...]:
    """Every model the rig can play: the library, and what each config points at."""
    from .amp import CONFIG_KEY, AmpError, model_library, resolve_model
    from .verify import configs_to_check

    users: dict[Path, list[str]] = {}
    missing: list[ModelInfo] = []
    for config in configs_to_check(root):
        try:
            amp = json.loads(config.read_text()).get(CONFIG_KEY)
        except (OSError, ValueError, AttributeError):
            continue
        model = amp.get("model") if isinstance(amp, dict) else None
        if not isinstance(model, str):
            continue
        try:
            users.setdefault(resolve_model(model, root).resolve(), []).append(config.stem)
        except AmpError:
            missing.append(ModelInfo(Path(model).name, None, "the file is not there", (config.stem,)))

    library = model_library(root)
    for path in sorted(library.glob("*.nam")) if library.is_dir() else []:
        users.setdefault(path.resolve(), [])
    return tuple(_describe(path, tuple(sorted(by))) for path, by in sorted(users.items())) + tuple(missing)


def gather_environment(root: Path) -> Environment:
    """Look at the machine. The one impure function here."""
    from .take import default_takes_dir

    takes = default_takes_dir()
    free, problem = _free_bytes(takes)
    stems, rig = _largest_rig(root)
    return Environment(
        pw_jack=shutil.which("pw-jack"),
        carla=shutil.which("carla"),
        jackd_pids=_jackd_pids(),
        fuse2=_fuse2_loads(),
        sushi_is_appimage=_is_appimage(root / "sushi"),
        takes_free_bytes=free,
        takes_dir=str(takes),
        takes_problem=problem,
        take_stems=stems,
        take_rig=rig,
        models=_models(root),
    )
