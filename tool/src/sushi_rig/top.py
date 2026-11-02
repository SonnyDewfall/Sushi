"""`sushi-rig top` — what the rig is costing, live.

`pw-top` already has this information, but it reports on every node PipeWire
knows about, names them by JACK client rather than by their role in the rig, and
leaves you to work out which of nine columns matter. This shows the rig's own
nodes, labelled the way `sushi-rig status` labels them, with the two numbers
that actually predict trouble.

Those two are:

`B/Q` — busy over quantum, the fraction of its time budget a node used. This is
the one to watch. A reverb that pushed the total over 1.0 is what "stutter"
turned out to be, and it was invisible in every other measure: CPU percentage
looked fine, and the plugin's three innocuous knobs gave no hint it cost twenty
times everything else combined.

`ERR` — xruns, cumulative. The absolute number is nearly meaningless, since it
counts everything since the device appeared and never resets. What matters is
whether it is *climbing*, so this reports the change between samples rather than
the total.

The agent serves the same reading as data at GET /rig/load (`load_report`,
`read_pw_top`, #111), for the web UI's LOAD and XRUNS tiles.
"""

from __future__ import annotations

import re
import subprocess
import sys
import time
from typing import Any

# How much of its budget a node can use before it is worth pointing out. Well
# below 1.0: a node that only occasionally overruns still drops blocks, and the
# average hides it.
BUSY_WARN = 0.70

BAR_WIDTH = 24

# What starts every sample block `pw-top -b` prints: the column header.
PW_TOP_HEADER = "S   ID  QUANT"

_UNITS = {"ns": 1e-9, "us": 1e-6, "µs": 1e-6, "ms": 1e-3, "s": 1.0}


def parse_duration(text: str) -> float | None:
    """`pw-top`'s human-readable durations ("674.6us", "4.9ms") as seconds."""
    match = re.fullmatch(r"([\d.]+)(ns|us|µs|ms|s)", text.strip())
    if not match:
        return None
    return float(match.group(1)) * _UNITS[match.group(2)]


def parse_pw_top(output: str) -> list[dict[str, Any]]:
    """The last full sample from `pw-top -b` output.

    `-b` prints one block per sample, so the last block is the freshest. Two
    samples are always requested because the first has nothing to compare
    against and reports zeros throughout — a real trap, since `-n 1` looks like
    it works and quietly tells you the rig is idle.

    The NAME column is taken as the final field. PipeWire node names in this rig
    never contain spaces, and the FORMAT column before it is variable-width,
    which makes counting from the left unreliable.

    Each row also carries the *driver* it runs under. `pw-top` prints a driver
    and then its followers, marking a follower with a `+` before the name, and
    only the driver row states the graph's quantum and rate — a follower's
    columns read 0. Sushi is always a follower, so reading the quantum off the
    rig's own node reported "quantum 0 @ 0 Hz" and no block time at all: the
    one display built to explain latency could never show the number that
    causes it. Keeping the association also makes *which device is driving*
    visible, which is worth seeing on its own — a webcam that had joined the
    graph through the tuner was clocking this rig at 2048 and nothing said so.
    """
    blocks = output.split(PW_TOP_HEADER)
    if len(blocks) < 2:
        return []

    nodes: list[dict[str, Any]] = []
    driver: dict[str, Any] | None = None
    for line in blocks[-1].splitlines()[1:]:
        fields = line.split()
        if len(fields) < 10 or fields[0] not in ("R", "S", "I", "C"):
            continue
        busy = parse_duration(fields[5])
        try:
            # "---" is what pw-top prints for a node it has no timing for: a
            # row in state C, observed in tool/spikes/live_rig/RESULTS-...
            # (2026-10-31). Kept, with no load, rather than dropped: a rig node
            # PipeWire lists but does not schedule is a different fault from
            # one it does not list, and the load route says which.
            load = None if fields[7] == "---" else float(fields[7])
            xruns = int(fields[8])
        except ValueError:
            continue
        node = {
            "name": fields[-1],
            "running": fields[0] == "R",
            "quantum": fields[2],
            "rate": fields[3],
            "busy": busy,
            "load": load,
            "xruns": xruns,
            # An unmeasured row is not scheduled, so it drives nothing, and
            # must not become the driver of the rows printed after it.
            "is_driver": "+" not in fields and load is not None,
        }
        if node["is_driver"]:
            driver = node
        # None, not the node itself, when no driver row has been seen: naming a
        # follower as its own driver prints "[clocked by sushi]", which is
        # never true of anything.
        # Nor is an unscheduled row (no "+", no timing) under the driver
        # printed before it: it follows nothing.
        under = driver if node["is_driver"] or "+" in fields else None
        node["driver"] = under["name"] if under else None
        node["driver_quantum"] = under["quantum"] if under else node["quantum"]
        node["driver_rate"] = under["rate"] if under else node["rate"]
        nodes.append(node)
    return nodes


# Which PipeWire nodes belong to each thing the supervisor started. Only the
# children that actually process audio appear: the listener, the panel and the
# patchbay do no DSP and would be noise here.
#
# The amp is two nodes — Carla's own client and the plugin's, which Carla names
# after the project's plugin entry. The plugin node is where the work happens;
# Carla's is near zero but shown so a dead amp is visible rather than absent.
CHILD_NODES: dict[str, tuple[str, ...]] = {
    "sushi": ("sushi",),
    "amp": ("NAM", "Carla"),
    "fmit": ("fmit",),
}


# Signal order, not the order the supervisor happens to start things in. The
# amp is in front of Sushi, and the tuner only taps the input — reading the
# display top to bottom should follow the guitar.
DISPLAY_ORDER = ("amp", "sushi", "fmit")


def rig_nodes(
    nodes: list[dict[str, Any]], children: dict[str, Any] | None
) -> list[dict[str, Any]]:
    """The rig's own audio nodes, in signal order.

    Falls back to every known node name when there is no state file, so this
    still says something useful about a rig someone started by hand.
    """
    running = set(children or CHILD_NODES)
    wanted: list[str] = []
    for child in DISPLAY_ORDER:
        if child in running:
            wanted.extend(CHILD_NODES.get(child, ()))
    if not wanted:
        wanted = [n for child in DISPLAY_ORDER for n in CHILD_NODES.get(child, ())]

    by_name = {node["name"]: node for node in nodes}
    return [by_name[name] for name in wanted if name in by_name]


# Which node's driver describes the rig. `rig_nodes` returns signal order, so
# the first row is the amp whenever one is running — and Carla can sit in a
# different driver group from Sushi entirely, on a Dummy-Driver carrying no
# quantum, which printed "quantum 0 @ 0 Hz" again from the other end. Sushi is
# the spine: it hosts the config, and it is what `verify` and `capture` talk to.
BUDGET_NODE = "sushi"


def budget_source(nodes: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The node to read the block budget from."""
    for node in nodes:
        if node["name"] == BUDGET_NODE:
            return node
    # No Sushi on display (--no-amp aside, that means someone is running the
    # tuner alone): any node that knows its driver beats one that does not.
    for node in nodes:
        if node.get("driver"):
            return node
    return nodes[0] if nodes else None


def driver_groups(nodes: list[dict[str, Any]]) -> list[str]:
    """Every distinct driver the displayed nodes run under, in display order.

    More than one is a real fault with no other symptom: nodes in separate
    driver groups are not scheduled in the same graph cycle, so the amp in
    front of Sushi stops being free and the chain stops being sample-aligned.
    """
    seen: list[str] = []
    for node in nodes:
        driver = node.get("driver")
        if driver and driver not in seen:
            seen.append(driver)
    return seen


# --------------------------------------------------------------------------- #
# The agent's GET /rig/load: the same reading, as data (the web UI's LOAD and
# XRUNS tiles)
# --------------------------------------------------------------------------- #

class LoadError(Exception):
    """pw-top could not be read: `code` says how (`no_pw_top`, `timeout`,
    `failed`, `no_sample`), the message says it to a person."""

    def __init__(self, message: str, code: str):
        super().__init__(message)
        self.code = code


def _number(text: Any) -> int | None:
    return int(text) if isinstance(text, str) and text.isdigit() else None


def load_report(nodes: list[dict[str, Any]], children: dict[str, Any] | None) -> dict[str, Any]:
    """What `format_sample` shows, as data. Pure.

    `nodes` is `parse_pw_top`'s, `children` the state file's (`describe_rig`'s
    `children`). Each rig node with the child it belongs to, its load (B/Q,
    the fraction of the block it used; None where pw-top printed "---"), its
    busy time and its cumulative xruns; the budget from Sushi's driver, never
    a follower's row (see `parse_pw_top`); every driver the nodes run under,
    since more than one is a fault with no other symptom; and `missing`, the
    nodes the running children should have that pw-top does not list (only
    when the children are known: with none, every known name is looked for,
    as `rig_nodes` does, and an absent amp would be no news).

    `total_load` sums the measured loads, as `format_sample`'s total does: the
    amp and Sushi run one after the other in the same cycle. None when no node
    was measured, so nothing reads as 0 % that was not measured. `warn_at` is
    `BUSY_WARN`, so the page does not keep a second threshold.
    """
    shown = rig_nodes(nodes, children)
    owner = {name: child for child, names in CHILD_NODES.items() for name in names}
    source = budget_source(shown)
    quantum = rate = block_ms = driver = None
    if source is not None:
        quantum = _number(source.get("driver_quantum") or source.get("quantum"))
        rate = _number(source.get("driver_rate") or source.get("rate"))
        driver = source.get("driver")
        if quantum and rate:
            block_ms = round(1000 * quantum / rate, 3)
    expected = [name for child in DISPLAY_ORDER if child in (children or {})
                for name in CHILD_NODES.get(child, ())]
    names = {node["name"] for node in shown}
    measured = [node["load"] for node in shown if node["load"] is not None]
    return {
        "nodes": [{
            "name": node["name"],
            "child": owner.get(node["name"]),
            "running": node["running"],
            "load": node["load"],
            "busy_ms": None if node["busy"] is None else round(node["busy"] * 1000, 3),
            "xruns": node["xruns"],
            "driver": node.get("driver"),
        } for node in shown],
        "missing": [name for name in expected if name not in names],
        "budget": {"quantum": quantum, "rate": rate, "block_ms": block_ms, "driver": driver},
        "drivers": driver_groups(shown),
        "total_load": round(sum(measured), 4) if measured else None,
        "warn_at": BUSY_WARN,
    }


def read_pw_top(timeout: float) -> str:
    """One `pw-top -b -n 2` (two samples: see `parse_pw_top`), its output, or
    `LoadError` saying why not. Blocking, so the agent runs it off its loop;
    `timeout` is a deadline, past which the child is killed (`subprocess.run`
    does that) rather than left running beside the agent.

    `sample()` above, for `sushi-rig top`, shows an empty frame when pw-top
    fails; a figure on the stage screen must not quietly become nothing, so
    every way this fails is said."""
    try:
        result = subprocess.run(
            ["pw-top", "-b", "-n", "2"], capture_output=True, text=True, check=False, timeout=timeout)
    except FileNotFoundError:
        raise LoadError(
            "pw-top is not installed, so load and xruns cannot be read "
            "(it comes with PipeWire's tools: the pipewire-bin package)", "no_pw_top") from None
    except subprocess.TimeoutExpired:
        raise LoadError(f"pw-top did not finish within {timeout:g}s and was stopped", "timeout") from None
    if result.returncode != 0:
        said = " ".join((result.stderr or result.stdout or "").split())[-300:] or "nothing"
        raise LoadError(f"pw-top failed (exit {result.returncode}): {said}", "failed")
    samples = result.stdout.count(PW_TOP_HEADER)
    if samples == 0:
        # Not a pw-top this parser knows, or a PipeWire with no graph: the
        # header is what `parse_pw_top` splits on, so without it every rig
        # node would read as missing, which is a different claim.
        said = " ".join(result.stdout.split())[:200] or "nothing"
        raise LoadError(f"pw-top printed no sample this tool can read: {said}", "no_sample")
    if samples < 2:
        # The first block is all zeros (see `parse_pw_top`), so read alone it
        # is 0 % load and no xruns, measured: the quiet lie this is to avoid
        # (found by review).
        raise LoadError(
            "pw-top printed one sample, not the two asked for, and the first is all zeros: "
            "nothing was measured", "no_sample")
    return result.stdout


def bar(load: float, width: int = BAR_WIDTH) -> str:
    filled = max(0, min(width, round(load * width)))
    return "█" * filled + "░" * (width - filled)


def format_sample(
    nodes: list[dict[str, Any]],
    deltas: dict[str, int],
    title: str,
) -> str:
    """One frame of the display."""
    if not nodes:
        return f"{title}\n\n  no rig audio nodes found — is the rig running?"

    # From the driver, not from Sushi: see `parse_pw_top`. And from Sushi's
    # driver specifically, not the first row's: see `budget_source`.
    source = budget_source(nodes) or nodes[0]
    quantum = source.get("driver_quantum") or source["quantum"]
    rate = source.get("driver_rate") or source["rate"]
    driver = source.get("driver")
    try:
        block_ms = 1000 * int(quantum) / int(rate)
        budget = f"quantum {quantum} @ {rate} Hz — {block_ms:.1f} ms per block"
    except (TypeError, ValueError, ZeroDivisionError):
        budget = f"quantum {quantum} @ {rate} Hz"
    if driver:
        # Named because the wrong driver is a real fault with no other symptom
        # than latency, and it is invisible everywhere else in this tool.
        budget += f"  [clocked by {driver}]"
    groups = driver_groups(nodes)
    if len(groups) > 1:
        budget += (
            "\n  ** split across " + str(len(groups)) + " driver groups ("
            + ", ".join(groups) + ") — these nodes are not scheduled in one "
            "graph cycle, so the chain is not sample-aligned **"
        )

    lines = [title, budget, ""]
    lines.append(f"  {'node':<10} {'busy':>9}  {'load':<{BAR_WIDTH}}  {'':>5}  xruns")

    total_busy = 0.0
    total_load = 0.0
    for node in nodes:
        busy = node["busy"] or 0.0
        total_busy += busy
        load = node["load"]
        total_load += load or 0.0
        delta = deltas.get(node["name"], 0)
        # The cumulative count is nearly meaningless — it never resets. A change
        # since the last sample is the thing worth seeing.
        xruns = f"{node['xruns']}" + (f"  (+{delta})" if delta else "")
        mark = " !" if load is not None and load >= BUSY_WARN else "  "
        dead = "" if node["running"] else "  (not running)"
        shown = "   —" if load is None else f"{load * 100:4.0f}"
        lines.append(
            f"  {node['name']:<10} {_ms(busy):>9}  {bar(load or 0.0)} "
            f"{shown}%{mark} {xruns}{dead}"
        )

    lines.append(f"  {'-' * (BAR_WIDTH + 34)}")
    lines.append(
        f"  {'total':<10} {_ms(total_busy):>9}  {bar(total_load)} "
        f"{total_load * 100:4.0f}%{' !' if total_load >= BUSY_WARN else '  '}"
    )

    if total_load >= 1.0:
        lines += ["", "  OVER BUDGET — the rig cannot keep up and is dropping blocks."]
    elif total_load >= BUSY_WARN:
        lines += ["", "  Close to budget. Expect xruns if anything else is added."]
    if any(deltas.values()):
        lines += ["", "  Xruns are climbing right now."]
    return "\n".join(lines)


def _ms(seconds: float) -> str:
    if seconds >= 1e-3:
        return f"{seconds * 1e3:.1f}ms"
    return f"{seconds * 1e6:.0f}us"


def sample() -> list[dict[str, Any]]:
    """One reading from pw-top. Two samples requested — see `parse_pw_top`."""
    result = subprocess.run(
        ["pw-top", "-b", "-n", "2"], capture_output=True, text=True, check=False
    )
    return parse_pw_top(result.stdout)


def top(interval: float = 1.0, once: bool = False) -> int:
    """Show the rig's load, refreshing until interrupted."""
    from .state import read_state

    previous: dict[str, int] = {}
    try:
        while True:
            state = read_state()
            title = (
                f"{state.get('config_name')} ({state.get('mode')} mode)"
                if state else "no rig recorded — showing whatever is running"
            )
            nodes = rig_nodes(sample(), (state or {}).get("children"))
            deltas = {
                node["name"]: max(0, node["xruns"] - previous.get(node["name"], node["xruns"]))
                for node in nodes
            }
            previous = {node["name"]: node["xruns"] for node in nodes}

            frame = format_sample(nodes, deltas, title)
            if once:
                print(frame)
                return 0
            # Home and clear-to-end rather than a full clear: wiping the screen
            # every second makes the numbers flicker and unreadable.
            sys.stdout.write("\033[H\033[J" + frame + "\n\n  Ctrl+C to stop\n")
            sys.stdout.flush()
            time.sleep(interval)
    except KeyboardInterrupt:
        print()
        return 0
