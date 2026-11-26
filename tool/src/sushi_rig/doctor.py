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
import math
import re
import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, NamedTuple

from . import patchbay
from .machine import MachineError, MachineSpec, machine_path
from .patchbay import PatchbayError
from .procs import threads_of  # noqa: F401 -- read by the checks below, and doctor.threads_of to callers

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

    def as_dict(self) -> dict:
        """The same four fields, for the agent's JSON (#29)."""
        return asdict(self)


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


def child_of_node(node: str) -> str | None:
    """Which of the rig's processes (the state file's child name) a saved
    patchbay node names, or None for a device. Through `patchbay`'s own
    matcher and `top.CHILD_NODES`, so the three cannot drift."""
    from .top import CHILD_NODES

    found = patchbay._rig_child(node, patchbay.rig_node_names())
    return next((child for child, names in CHILD_NODES.items() if found in names), None)


def _why_absent(child: str, children: dict, crashed: list[dict],
                state: dict | None) -> tuple[str, bool]:
    """Why the rig's process `child` has no node while the rig is up, and
    whether restarting the rig is the remedy. The agent's crash record is the
    best witness (it read the exit status), but only one of the running rig
    (`supervisor.same_run`, the state file's group and start): an earlier
    held rig's crash stays in the agent's record. The state file's pid,
    probed, is the next witness; a child the state file does not list was not
    started. The words are the supervisor's (`child_label`, `how_it_ended`)."""
    from .supervisor import child_label, how_it_ended, same_run

    label = child_label(child)
    record = next((r for r in reversed(crashed)
                   if isinstance(r, dict) and r.get("name") == child and same_run(r, state)), None)
    if record is not None:
        return f"{label} {how_it_ended(record)} at {record.get('at')} (the agent saw it)", True
    entry = children.get(child)
    if entry is None:
        return f"the rig was started without {label}", False
    if entry.get("alive") is False:
        return f"{label} (pid {entry.get('pid')}) is no longer running: it has exited since the rig started", True
    return f"{label} (pid {entry.get('pid')}) is running but has no node in the graph", False


def patchbay_checks(missing: dict, not_running: dict, *, file_name: str,
                    links: int, rig: dict | None = None,
                    crashed: list[dict] | None = None, state: dict | None = None) -> list[Check]:
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

    `rig` is `rig.describe_rig`'s answer, `crashed` the agent's crash
    records (`agent.supervisor.crashed`) when an agent was asked, and `state`
    the state file, which says which run a crash record must belong to. With the rig
    up, a node of its own that is absent is not "the rig is down": on the rig
    on 2026-11-02 that sentence, and its `sushi-rig up`, were given for a tuner
    that had been killed. So the check says which process it is and why it is
    absent, from the agent's record if there is one and the state file's pids
    if not.
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
    if not_running and not (rig or {}).get("running"):
        checks.append(Check(
            "patchbay-unchecked", UNCHECKED,
            f"{len(not_running)} node(s) {file_name} names are started by the rig "
            f"itself, so their absence says nothing while the rig is down",
            "run `sushi-rig up` and check again to cover those links",
        ))
    elif not_running:
        children = rig.get("children") if isinstance(rig.get("children"), dict) else {}
        whose: dict[str, list[str]] = {}
        for node in sorted(not_running):
            whose.setdefault(child_of_node(node) or node, []).append(node)
        reasons, restart = [], False
        for child, nodes in whose.items():
            why, dead = _why_absent(child, children, list(crashed or []), state)
            reasons.append(f"{why}, so {', '.join(repr(n) for n in nodes)} is not in the graph")
            restart = restart or dead
        checks.append(Check(
            "patchbay-unchecked", UNCHECKED,
            f"{len(not_running)} node(s) {file_name} names are the rig's own, and the rig "
            f"is up: {'; '.join(reasons)}. Their links cannot be checked",
            "restart the rig (`sushi-rig restart`) to bring it back, then check again" if restart
            else "nothing to fix for the sound: those links are checked when the process is there",
        ))
    if not checks:
        checks.append(Check(
            "patchbay-nodes", OK,
            f"all {links} link(s) in {file_name} name a node that exists",
        ))
    return checks


def record_ports_check(columns: list, ports: list) -> Check:
    """Can the recorder find every port it plans to link?

    Pure. `columns` are `capture.plan_columns` of the running rig's plan and
    `ports` what a JACK client under `pw-jack` lists (`capture.jack_ports`):
    the same plan, the same listing and the same match (`capture.match_sources`)
    the capture engine makes at RECORD, so this fails exactly when a take would
    be refused at that step. Added after the rig, on 2026-11-02, passed 11
    checks while every RECORD was refused: the machine file names the guitar
    by its PipeWire alias, and the recorder looked among JACK's names alone.
    The plan now names each port as JACK does (`transport.jack_port`, off
    the live graph), and this checks that JACK lists each one by that name.
    """
    from .capture import match_sources

    matched, problems = match_sources(columns, ports)
    if problems:
        return Check(
            "record-ports", FAULT,
            f"a take of the running rig would be refused: {'; '.join(problems)}",
            "the machine file names each port by its PipeWire alias, as qpwgraph shows it, and "
            "the plan names it as JACK does from the live graph (pw-dump); compare them with "
            "`pw-jack jack_lsp -A`, which lists every JACK port with its aliases",
        )
    return Check("record-ports", OK, f"the recorder's {len(matched)} column(s) are all JACK ports")


def record_check(root: Path, rig: dict, state: dict | None, *,
                 list_ports: Any = None) -> Check:
    """`record_ports_check` for the running rig, planned as the recorder plans
    it (`transport.plan_for_run`, from the files the state file names), or
    why it could not be asked.

    A plan the recorder would refuse is a fault here too, in its words, and
    so is an error the recorder would meet planning it: the recorder catches
    anything there (`transport.record_session`), and a bug in a plan must
    not take the rest of doctor down. A rig that is down or a v1 run is
    unchecked. So is a machine where no JACK client can be opened
    (`list_ports`, `capture.list_jack_ports` by default, which runs one under
    `pw-jack`): "needs JACK", never green."""
    from .capture import CaptureError, JackUnreachable, list_jack_ports, plan_columns
    from .transport import GraphUnreadable, PlanError, plan_for_run

    if not rig.get("running") or not state:
        return Check("record-ports", UNCHECKED,
                     "no rig is running, so there is no take to plan and no port to look for",
                     "run `sushi-rig up <name>` and check again")
    if not (isinstance(state.get("rig"), str) and state["rig"]):
        return Check("record-ports", UNCHECKED,
                     f"{state.get('config_name') or 'the running config'} was started from a v1 config by an "
                     "older sushi-rig, and takes are planned only for a rig of channels, so recording is off "
                     "for this run",
                     "restart it (`sushi-rig restart`), which runs the rig of channels of its name, to record")

    def absolute(value: Any) -> Path | None:
        if not isinstance(value, str) or not value:
            return None
        return Path(value) if Path(value).is_absolute() else Path(root) / value

    try:
        plan = plan_for_run(absolute(state["rig"]), absolute(state.get("config")),
                            machine_file=absolute(state.get("machine")), root=Path(root))
        columns = plan_columns(plan.stems)
    except GraphUnreadable as exc:
        # Cannot look, as for JACK below: the ports are named off the live
        # graph, and a graph that will not read says nothing of them.
        return Check("record-ports", UNCHECKED,
                     "needs PipeWire: the recorder's ports are named off the live graph, and it could not "
                     f"be read here: {'; '.join(exc.problems)}",
                     "run doctor on the rig machine with the rig up; `pw-dump` must run there")
    except (PlanError, CaptureError) as exc:
        return Check("record-ports", FAULT,
                     f"no take can be planned for the running rig: {'; '.join(exc.problems)}",
                     "the record bar says the same; fix what it names and restart the rig")
    except Exception as exc:  # noqa: BLE001 -- the recorder's own rule, and doctor goes on
        return Check("record-ports", FAULT,
                     f"planning a take of the running rig failed unexpectedly: {type(exc).__name__}: {exc}",
                     "a take would be refused the same way; this is a bug in sushi-rig, worth reporting")
    try:
        ports = (list_ports or list_jack_ports)()
    except JackUnreachable as exc:
        return Check("record-ports", UNCHECKED,
                     "needs JACK: the recorder's ports are found among a JACK client's, and none "
                     f"could be opened here: {'; '.join(exc.problems)}",
                     "run doctor on the rig machine with the rig up; `pw-jack jack_lsp -A` lists "
                     "the ports by hand")
    return record_ports_check(columns, ports)


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
    # pipewire-pulse's threads, by pid: each `(name, policy, rt_priority)` as
    # /proc/<pid>/task/*/stat gives them. `{}` when none is running, None when
    # that could not be looked for.
    pulse_threads: dict[int, tuple[tuple[str, int, int], ...]] | None = None
    # What closing the lid does, by logind property (HandleLidSwitch,
    # HandleLidSwitchExternalPower); None when logind could not be asked.
    # The defaults are a laptop set up for playing with the lid closed.
    lid_actions: tuple[tuple[str, str], ...] | None = (
        ("HandleLidSwitch", "ignore"), ("HandleLidSwitchExternalPower", "ignore"))
    # Who holds a `handle-lid-switch` block inhibitor (KDE's PowerDevil,
    # GNOME's gsd-power under some setups): while one is held, logind's
    # HandleLidSwitch is not what happens, the desktop's own setting is.
    # `()` for none; None when logind could not be asked.
    lid_inhibitors: tuple[str, ...] | None = ()
    # Each Wi-Fi interface and whether power saving is on (None: `iw` could
    # not say); None for the whole when `iw` is not there to ask.
    wifi_power_save: tuple[tuple[str, bool | None], ...] | None = ()


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
            "see docs/reference/environment.md for where Sushi comes from",
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


# The scheduling policies /proc reports that are realtime: SCHED_FIFO, SCHED_RR.
REALTIME_POLICIES = {1: "SCHED_FIFO", 2: "SCHED_RR"}


def check_pulse_realtime(environment: Environment) -> Check:
    """Whether PipeWire's PulseAudio server runs a thread at realtime priority.

    For the web page's visualiser (#176), and nothing else in the rig: the
    browser hears the guitar through pipewire-pulse, whose capture stream is
    a node in the graph Sushi's driver runs, doing a small copy each cycle in
    pipewire-pulse's data thread. Without realtime priority that thread can be
    late, and so can the cycle. Never a fault, since the rig does not need it:
    `ok` either way, with what to do when it is not realtime."""
    threads = environment.pulse_threads
    if threads is None:
        return Check("pulse-realtime", UNCHECKED,
                     "could not look for pipewire-pulse (pgrep or /proc could not be read)",
                     "check by hand: ps -L -o tid,cls,rtprio,comm -p \"$(pgrep -x pipewire-pulse)\"")
    if not threads:
        return Check("pulse-realtime", UNCHECKED,
                     "pipewire-pulse is not running, so no browser can hear an input: the visualiser cannot "
                     "start (nothing else in the rig uses it)")
    realtime = [(pid, name, REALTIME_POLICIES[policy], prio)
                for pid, found in sorted(threads.items()) for name, policy, prio in found if policy in REALTIME_POLICIES]
    if realtime:
        shown = ", ".join(f"{name} ({policy} {prio})" for _, name, policy, prio in realtime)
        return Check("pulse-realtime", OK, f"pipewire-pulse runs realtime: {shown}")
    return Check(
        "pulse-realtime", OK,
        "pipewire-pulse runs no thread at realtime priority. Nothing in the rig needs it, but the web "
        "page's visualiser hears the guitar through it, inside Sushi's cycle, where a late thread can "
        "make the cycle late",
        "turn the visualiser off on the page while playing, or give PipeWire realtime priority (on "
        "Ubuntu it gets it through rtkit: sudo apt install rtkit), then, with the rig down, "
        "systemctl --user restart pipewire pipewire-pulse",
    )


def tap_check(rig: dict, state: dict | None, tap: dict | None, threads: tuple | None,
              grant: tuple[bool | None, str] = (None, "not asked")) -> Check:
    """The visualiser's tap (#195, `vistap`): the agent's, started while the
    page's visualiser listens and stopped when it lets go, and refused
    unless realtime, since in Sushi's cycle a late one is a cycle the graph
    did not finish. A fault when it runs without a realtime thread, when the
    agent refused one for want of it, or when nothing on this machine would
    give it one; never for a rig started with --no-visualiser-tap.

    `rig` is `describe_rig`'s answer, `state` the state file, `tap` the
    agent's report (`Supervisor.tap_report`; None: no agent was asked),
    `threads` the running tap's (`threads_of`; None: none to look at), and
    `grant` whether realtime can be had (`_realtime_grant`: True, False or
    None, and why). Pure."""
    from .supervisor import child_label
    from .vistap import CHILD, REALTIME_FIX, realtime

    name = "visualiser-tap"
    label = child_label(CHILD)
    if not rig.get("running") or not state:
        return Check(name, UNCHECKED, "no rig is running, so there is no output to tap",
                     "run the rig through the agent and check again")
    left = rig.get("visualiser_tap") if isinstance(rig.get("visualiser_tap"), dict) else None
    if (left and left.get("alive") and not left.get("owner_alive")
            and not (tap and tap.get("running") and tap.get("pid") == left.get("pid"))):
        # Its agent died without stopping it (SIGKILL, the OOM killer), and
        # no agent holds a lease on it: in Sushi's cycle, unseen by the page.
        return Check(name, FAULT, f"{label} (pid {left.get('pid')}) is running with no agent holding it, in "
                     "Sushi's cycle, where each time it is late is a cycle the graph did not finish",
                     f"start the agent (it stops a tap it finds left behind), or kill {left.get('pid')}")
    if state.get("visualiser_tap_requested") is False:
        return Check(name, OK, "the rig was started with --no-visualiser-tap, so its output is never offered: "
                     "the visualiser listens to the guitar's input")
    can, why = grant
    if tap and tap.get("running") and isinstance(tap.get("pid"), int):
        pid = tap["pid"]
        if threads is None:
            return Check(name, UNCHECKED, f"could not read the threads of {label} (pid {pid})",
                         f"check by hand: ps -L -o tid,cls,rtprio,comm -p {pid}")
        found = realtime(threads)
        if found:
            shown = ", ".join(f"{n} ({policy} {prio})" for n, policy, prio in found)
            return Check(name, OK, f"{label} (pid {pid}) is running for {tap.get('pages', 0)} page(s), realtime: "
                         f"{shown}")
        return Check(name, FAULT, f"{label} (pid {pid}) runs no thread at realtime priority, in Sushi's cycle, "
                     "where each time it is late is a cycle the graph did not finish", REALTIME_FIX)
    refused = (tap or {}).get("refused") or {}
    if refused.get("code") == "not_realtime":
        return Check(name, FAULT, f"the agent refused a tap at {refused.get('at')} for want of a realtime thread, "
                     "so the visualiser listens to the guitar's input", REALTIME_FIX)
    if can is False:
        return Check(name, FAULT, f"a tap would be refused: nothing here would give it a realtime thread ({why}), "
                     "so the visualiser listens to the guitar's input", REALTIME_FIX)
    if tap is None:
        return Check(name, UNCHECKED, "the tap is the agent's, started while the page's visualiser listens, and "
                     f"no agent holding this rig was asked{'' if can is None else f'; realtime can be had ({why})'}",
                     "run doctor with the agent running the rig")
    said = f"; realtime can be had ({why})" if can else f"; whether realtime can be had is not known ({why})"
    if refused:
        return Check(name, OK, f"no tap is running; the last was refused ({refused.get('reason')})" + said)
    return Check(name, OK, "no tap is running: the agent starts one while the page's visualiser listens and "
                 "stops it when it lets go" + said)


def _realtime_grant(pid: int | None) -> tuple[bool | None, str]:
    """Whether a child of process `pid` (the agent) could have a realtime
    thread: its `Max realtime priority` limit above 0, or rtkit running to
    grant one. `(True | False | None, why)`, None when neither could be
    read. A reading, not a promise: rtkit grants up to its own ceiling."""
    limit = None
    try:
        for line in Path(f"/proc/{pid if pid else 'self'}/limits").read_text().splitlines():
            if line.startswith("Max realtime priority"):
                soft = line.split()[3]
                limit = 0 if soft == "0" else (int(soft) if soft.isdigit() else 99)
    except OSError:
        pass
    try:
        rtkit = subprocess.run(["pgrep", "-x", "rtkit-daemon"], capture_output=True, text=True,
                               check=False, timeout=5.0).returncode == 0
    except (OSError, subprocess.SubprocessError):
        rtkit = None
    who = f"pid {pid}" if pid else "this process"
    if limit:
        return True, f"{who}'s realtime limit is {limit}"
    if rtkit:
        return True, "rtkit-daemon is running"
    if limit == 0 and rtkit is False:
        return False, f"{who}'s realtime limit is 0 and rtkit-daemon is not running"
    return None, "the realtime limit or rtkit could not be read"


# What logind may do on the lid closing that stops the rig (logind.conf(5)'s
# HandleLidSwitch= values); `ignore`, `lock` and `factory-reset` leave it
# playing.
LID_STOPS = frozenset({"suspend", "hibernate", "hybrid-sleep", "suspend-then-hibernate", "sleep",
                       "poweroff", "reboot", "halt", "kexec"})
LID_GUIDE = "docs/guide/playing-out.md, \"With the lid closed\""


def check_lid(environment: Environment) -> Check:
    """Whether closing the lid stops the rig (#181). Never a fault: a lid that
    suspends is a laptop's default and fine for playing with it open; the
    check says so, and where to read how to play with it closed. NOT YET
    SEEN on a real laptop: what logind answers is from its documentation."""
    actions = environment.lid_actions
    if actions is None:
        return Check(
            "lid", UNCHECKED, "logind could not be asked what closing the lid does",
            "ask it by hand: busctl get-property org.freedesktop.login1 /org/freedesktop/login1 "
            "org.freedesktop.login1.Manager HandleLidSwitch",
        )
    # An unset HandleLidSwitchExternalPower is said by some logind releases
    # as "": it then does what HandleLidSwitch does.
    first = actions[0][1] if actions else ""
    actions = tuple((name, value or first) for name, value in actions)
    said = ", ".join(f"{name}={value}" for name, value in actions)
    holders = environment.lid_inhibitors
    if holders:
        # A desktop program has taken the lid from logind (review of PR
        # #185): what HandleLidSwitch says does not happen; its setting does.
        return Check(
            "lid", OK,
            f"{', '.join(holders)} holds the lid switch (a handle-lid-switch inhibitor), so the desktop's "
            f"own power settings decide what closing the lid does, not logind's ({said})",
            "set it in the desktop's power settings (KDE: System Settings, Power Management, "
            f"\"When laptop lid closed\"; GNOME: Settings, Power); see {LID_GUIDE}",
        )
    if any(value in LID_STOPS for _, value in actions):
        return Check(
            "lid", OK, f"closing the lid suspends this machine, which stops the rig ({said}): "
            "on a laptop, keep it open while playing",
            f"to play with the lid closed, see {LID_GUIDE}",
        )
    return Check("lid", OK, f"closing the lid leaves the rig playing ({said})")


def check_wifi_power_save(environment: Environment) -> Check:
    """Whether Wi-Fi power saving is on (#181): it can hold a phone's changes
    back by a beat or drop the page's connection while it dozes. Never a
    fault, for the reason `check_lid` gives. NOT YET SEEN on a real laptop."""
    found = environment.wifi_power_save
    if found is None:
        return Check("wifi-power-save", UNCHECKED, "`iw` is not installed, so Wi-Fi power saving could not be read",
                     "sudo apt install iw, then run doctor again")
    if not found:
        return Check("wifi-power-save", OK, "no Wi-Fi interface")
    on = [name for name, value in found if value]
    unknown = [name for name, value in found if value is None]
    if on:
        return Check(
            "wifi-power-save", OK,
            f"Wi-Fi power saving is on for {', '.join(on)}: the phone's changes can arrive late",
            f"for this session: sudo iw dev {on[0]} set power_save off; for good, see {LID_GUIDE}",
        )
    if unknown:
        return Check("wifi-power-save", UNCHECKED, f"`iw` did not say whether power saving is on for {', '.join(unknown)}",
                     f"iw dev {unknown[0]} get power_save")
    return Check("wifi-power-save", OK, f"Wi-Fi power saving is off ({', '.join(n for n, _ in found)})")


def environment_checks(environment: Environment) -> list[Check]:
    return [
        check_pw_jack(environment),
        check_no_jackd(environment),
        check_carla(environment),
        check_fuse(environment),
        check_disk(environment),
        check_models(environment),
        check_pulse_realtime(environment),
        check_lid(environment),
        check_wifi_power_save(environment),
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


def _pulse_threads() -> dict[int, tuple[tuple[str, int, int], ...]] | None:
    """pipewire-pulse's threads, by pid, as `(name, policy, rt_priority)`
    (`threads_of`). `pgrep -x`, for the reason `_jackd_pids` gives. None when
    pgrep could not be run."""
    try:
        result = subprocess.run(["pgrep", "-x", "pipewire-pulse"], capture_output=True, text=True,
                                check=False, timeout=5.0)
    except (OSError, subprocess.SubprocessError):
        return None
    return {pid: threads_of(pid) for pid in (int(line) for line in result.stdout.split() if line.strip().isdigit())}


def _lid_actions() -> tuple[tuple[str, str], ...] | None:
    """What logind does on the lid closing, by `busctl get-property` (which
    prints `s "suspend"`), or None when it cannot be asked."""
    found = []
    for name in ("HandleLidSwitch", "HandleLidSwitchExternalPower"):
        try:
            result = subprocess.run(
                ["busctl", "get-property", "org.freedesktop.login1", "/org/freedesktop/login1",
                 "org.freedesktop.login1.Manager", name],
                capture_output=True, text=True, check=False, timeout=5.0,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        value = busctl_string(result.stdout) if result.returncode == 0 else None
        if value is None:
            return None
        found.append((name, value))
    return tuple(found)


def _lid_inhibitors() -> tuple[str, ...] | None:
    """Who holds a `handle-lid-switch` block inhibitor: logind's
    `BlockInhibited` says whether one is held, `ListInhibitors` who. Only
    block mode matters: logind takes delay inhibitors for sleep and
    shutdown alone, so `DelayInhibited` never names the lid. None when
    logind cannot be asked."""
    base = ["busctl", "org.freedesktop.login1", "/org/freedesktop/login1", "org.freedesktop.login1.Manager"]
    try:
        blocked = subprocess.run([base[0], "get-property", *base[1:], "BlockInhibited"],
                                 capture_output=True, text=True, check=False, timeout=5.0)
        if blocked.returncode != 0 or busctl_string(blocked.stdout) is None:
            return None
        if "handle-lid-switch" not in busctl_string(blocked.stdout).split(":"):
            return ()
        listed = subprocess.run([base[0], "call", *base[1:], "ListInhibitors"],
                                capture_output=True, text=True, check=False, timeout=5.0)
    except (OSError, subprocess.SubprocessError):
        return None
    return lid_inhibitors_from(listed.stdout if listed.returncode == 0 else "") or ("a desktop program",)


_INHIBITOR = re.compile(r'"((?:[^"\\]|\\.)*)" "((?:[^"\\]|\\.)*)" "(?:[^"\\]|\\.)*" "((?:[^"\\]|\\.)*)" \d+ \d+')


def lid_inhibitors_from(text: str) -> tuple[str, ...]:
    """Who holds a block `handle-lid-switch` inhibitor, from `busctl call
    ... ListInhibitors`, which prints `a(ssssuu) N "what" "who" "why"
    "mode" uid pid ...`. Pure."""
    who = []
    for what, name, mode in _INHIBITOR.findall(text):
        if mode == "block" and "handle-lid-switch" in what.split(":") and name not in who:
            who.append(name)
    return tuple(who)


def busctl_string(text: str) -> str | None:
    """The string of `busctl get-property`'s `s "suspend"`. Pure."""
    kind, _, value = text.strip().partition(" ")
    if kind != "s" or len(value) < 2 or not (value[0] == value[-1] == '"'):
        return None
    return value[1:-1]


def _wifi_power_save(sys_net: Path = Path("/sys/class/net")) -> tuple[tuple[str, bool | None], ...] | None:
    """Each Wi-Fi interface (by /sys/class/net/<if>/wireless) and whether
    `iw dev <if> get power_save` says power saving is on. `()` with no Wi-Fi;
    None when there is Wi-Fi and no `iw` to ask."""
    try:
        names = sorted(p.name for p in sys_net.iterdir() if (p / "wireless").exists() or (p / "phy80211").exists())
    except OSError:
        return ()
    if not names:
        return ()
    if shutil.which("iw") is None:
        return None
    found = []
    for name in names:
        try:
            result = subprocess.run(["iw", "dev", name, "get", "power_save"],
                                    capture_output=True, text=True, check=False, timeout=5.0)
        except (OSError, subprocess.SubprocessError):
            found.append((name, None))
            continue
        found.append((name, power_save_from(result.stdout)))
    return tuple(found)


def power_save_from(text: str) -> bool | None:
    """`Power save: on` from `iw dev <if> get power_save`. Pure."""
    for line in text.splitlines():
        key, _, value = line.partition(":")
        if key.strip().lower() == "power save":
            return {"on": True, "off": False}.get(value.strip().lower())
    return None


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


def free_bytes(path: Path) -> tuple[int | None, str]:
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
    """The stems a take of the largest rig in config/rigs/ records, and its name."""
    from .rigfile import load_rig
    from .verify import configs_to_check

    stems, name = STEMS_PER_CHANNEL, ""
    for config in configs_to_check(root):
        try:
            # Its channels, as the file has them: each is recorded dry and
            # processed. A file that is not a rig is skipped:
            # test_rig_fixtures.py says which.
            count = STEMS_PER_CHANNEL * len(load_rig(config).channels)
        except Exception:  # noqa: BLE001 -- one bad rig must not cost doctor its answer
            continue
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
    """Every model the rig can play: the library, and what each rig points at."""
    from .amp import CONFIG_KEY, AmpError, model_library, resolve_model
    from .verify import config_to_check, configs_to_check

    users: dict[Path, list[str]] = {}
    missing: list[ModelInfo] = []
    for config in configs_to_check(root):
        try:
            amp = config_to_check(config).get(CONFIG_KEY)
        except Exception:  # noqa: BLE001 -- a rig that does not compile is test_rig_fixtures.py's to name
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
    free, problem = free_bytes(takes)
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
        pulse_threads=_pulse_threads(),
        lid_actions=_lid_actions(),
        lid_inhibitors=_lid_inhibitors(),
        wifi_power_save=_wifi_power_save(),
    )


# --------------------------------------------------------------------------- #
# The whole examination
# --------------------------------------------------------------------------- #

def run(root: Path, machine_file: Path | None = None,
        patchbay_file: Path | None = None, crashed: list[dict] | None = None,
        list_ports: Any = None, tap: dict | None = None) -> list[Check]:
    """Every check, in the order the command prints them.

    This was the body of the CLI command until the agent (#29) needed the same
    answer as data; the command now prints what this returns. One pw-dump, read
    once and used by the graph checks, the patchbay check and the recorder's
    ports, so they cannot disagree about a graph that changed between them.

    `crashed` is the agent's record of the rig's children that exited
    (`agent.supervisor.crashed`): the agent passes its own, the command asks a
    running agent for it, and None is "no agent was asked". The state file,
    read once here, says whether a rig is up and which children it started.
    `list_ports` lists JACK's ports for the record check
    (`capture.list_jack_ports` by default). `tap` is the agent's report of
    the visualiser's tap (`Supervisor.tap_report`), None when no agent was
    asked.
    """
    from .rig import QUANTUM, describe_rig
    from .state import read_state

    checks: list[Check] = []

    # The machine file first: every device name the checks below compare
    # against comes from it, so an unreadable one is reported once here rather
    # than as a fault in each of them.
    machine_file = machine_file or machine_path(root)
    clients: set[str] = set()
    try:
        spec = MachineSpec.load(machine_file)
    except MachineError as exc:
        checks.append(machine_check([], file_name=machine_file.name, readable=False,
                                    why=str(exc)))
    else:
        problems = spec.validate()
        checks.append(machine_check(problems, file_name=machine_file.name))
        if not problems:
            clients = interface_clients(spec.role_ports())

    # Read and parsed inside one try. `pw-dump` exiting 0 with output that is
    # not JSON raises from the parse, not the read, and that used to escape as a
    # traceback — while the identical exception from the same helper was caught
    # for the patchbay check a few lines below. One of two readers of the same
    # bytes being guarded is the kind of asymmetry that only shows on the day it
    # happens.
    dump, nodes, why = None, None, ""
    try:
        dump = patchbay.read_pw_dump()
        nodes = patchbay.nodes_by_id(dump)
    except PatchbayError as exc:
        dump, nodes, why = None, None, "; ".join(exc.problems)
    state = read_state()
    rig = describe_rig(state)

    checks += graph_checks(
        nodes,
        clients,
        expected_quantum=QUANTUM,
        expected_rate=EXPECTED_RATE,
        reason=why,
    )

    patchbay_file = patchbay_file or root / "Patchbay" / "rig.qpwgraph"

    def not_compared(reason: str) -> Check:
        """One `unchecked` for a patchbay that could not be compared at all."""
        return Check(
            "patchbay-nodes", UNCHECKED,
            f"{patchbay_file.name} could not be checked against the graph: "
            f"{reason or 'no reason given'}",
            "`sushi-rig patchbay check` reports on it on its own",
        )

    if dump is None:
        checks.append(not_compared(why))
    else:
        try:
            links = patchbay.parse_patchbay(patchbay_file.read_text())
            result = patchbay.check_nodes(links, patchbay.live_node_names(dump))
        except OSError as exc:
            checks.append(not_compared(str(exc)))
        except PatchbayError as exc:
            checks.append(not_compared("; ".join(exc.problems)))
        else:
            checks += patchbay_checks(
                result.missing, result.not_running,
                file_name=patchbay_file.name, links=len(links), rig=rig, crashed=crashed,
                state=state,
            )

    checks.append(record_check(root, rig, state, list_ports=list_ports))
    pid = (tap or {}).get("pid") if (tap or {}).get("running") else None
    checks.append(tap_check(rig, state, tap, (threads_of(pid) or None) if isinstance(pid, int) else None,
                            _realtime_grant((tap or {}).get("agent_pid"))))
    checks += environment_checks(gather_environment(root))
    return checks
