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
    """
    blocks = output.split("S   ID  QUANT")
    if len(blocks) < 2:
        return []

    nodes: list[dict[str, Any]] = []
    for line in blocks[-1].splitlines()[1:]:
        fields = line.split()
        if len(fields) < 10 or fields[0] not in ("R", "S", "I", "C"):
            continue
        busy = parse_duration(fields[5])
        try:
            load = float(fields[7])
            xruns = int(fields[8])
        except ValueError:
            continue
        nodes.append({
            "name": fields[-1],
            "running": fields[0] == "R",
            "quantum": fields[2],
            "rate": fields[3],
            "busy": busy,
            "load": load,
            "xruns": xruns,
        })
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

    quantum, rate = nodes[0]["quantum"], nodes[0]["rate"]
    try:
        block_ms = 1000 * int(quantum) / int(rate)
        budget = f"quantum {quantum} @ {rate} Hz — {block_ms:.1f} ms per block"
    except (TypeError, ValueError, ZeroDivisionError):
        budget = f"quantum {quantum} @ {rate} Hz"

    lines = [title, budget, ""]
    lines.append(f"  {'node':<10} {'busy':>9}  {'load':<{BAR_WIDTH}}  {'':>5}  xruns")

    total_busy = 0.0
    total_load = 0.0
    for node in nodes:
        busy = node["busy"] or 0.0
        total_busy += busy
        total_load += node["load"]
        delta = deltas.get(node["name"], 0)
        # The cumulative count is nearly meaningless — it never resets. A change
        # since the last sample is the thing worth seeing.
        xruns = f"{node['xruns']}" + (f"  (+{delta})" if delta else "")
        mark = " !" if node["load"] >= BUSY_WARN else "  "
        dead = "" if node["running"] else "  (not running)"
        lines.append(
            f"  {node['name']:<10} {_ms(busy):>9}  {bar(node['load'])} "
            f"{node['load'] * 100:4.0f}%{mark} {xruns}{dead}"
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
