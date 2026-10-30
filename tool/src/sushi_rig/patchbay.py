"""The patchbay as something derived, and a check that the saved one is still true.

`Patchbay/rig.qpwgraph` is hand-wired in qpwgraph's GUI and names device
profiles ("Audient iD14 Multichannel"), so changing interface or even ALSA
profile silently orphans it: qpwgraph maintains its patch and dead links just sit
there. Two halves of the answer live here (issue #54):

- `generate_patchbay` writes the file from roles and routes instead of by hand.
- `stale_nodes` reports saved links whose node no longer exists, which is the
  cheap half and needs no machine file at all.

Everything is pure except `read_pw_dump`. A role resolving to a live port is
likewise a decision (`resolve_nodes`) over data that function fetched, so it can
be tested without PipeWire (quality principle 1).
"""

from __future__ import annotations

import json
import subprocess
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any, Collection, Mapping
from xml.sax.saxutils import quoteattr

# What qpwgraph wrote in the committed file. Copied, not understood: whether
# qpwgraph checks it on load is not established.
PATCHBAY_VERSION = "0.9.9"

SUSHI_NODE = "sushi"

# pw-dump is a one-shot snapshot of the whole graph; a hung PipeWire is the only
# way it takes long, and a check that blocks forever is worse than one that fails.
PW_DUMP_TIMEOUT = 5.0


class PatchbayError(Exception):
    """Carries every problem found, not the first — see `RigSpec.validate`."""

    def __init__(self, problems: list[str]):
        self.problems = problems
        super().__init__("\n".join(problems))


# --- where a role goes -------------------------------------------------------


@dataclass(frozen=True)
class Dest:
    """A place on Sushi: a stereo bus pair, or a single channel.

    Sushi's own rule, which this mirrors rather than relaxes: a routing entry is
    one or the other, never both. `engine_bus: N` is channels 2N and 2N+1.
    """

    kind: str  # "bus" | "channel"
    index: int

    def channels(self) -> list[int]:
        return [2 * self.index, 2 * self.index + 1] if self.kind == "bus" else [self.index]


def bus(n: int) -> Dest:
    return Dest("bus", n)


def channel(n: int) -> Dest:
    return Dest("channel", n)


# One track's engine-side entries for a role. Paired together because a stereo
# role on channel-form routing is *two* entries (`engine_channel: 0` and `: 1`),
# and judging each alone refuses it against every one of them.
Route = tuple[Dest, ...]


def _index_problem(where: str, key: str, value: Any) -> str | None:
    # `type(...) is int`: a quoted "0" would otherwise die later in arithmetic
    # with a TypeError, and a negative index would generate
    # `sushi:audio_input_-2` without a word. bool is an int, so True is refused.
    if type(value) is int and value >= 0:
        return None
    return f"{where}: {key} must be a non-negative integer (got {value!r})"


def routes_from_rig(
    rig: Any,
) -> tuple[dict[str, list[Route]], dict[str, list[Route]]]:
    """role -> the routes it feeds on Sushi, and role -> the ones it takes, from a `RigSpec`.

    One `Route` per track (deduplicated), holding that track's entries for the
    role.

    Raises `PatchbayError` for any entry it cannot place, rather than skipping
    it. `RigSpec.validate()` accepts an entry carrying only `track_bus`, and
    dropping it here left a track named in `source:` with nothing wired while
    the generation went ahead for everyone else.
    """
    feeds: dict[str, list[Route]] = {}
    sinks: dict[str, list[Route]] = {}
    problems: list[str] = []
    for track in rig.tracks:
        for role, label, entries, into in [
            (track.source, "inputs", track.inputs, feeds),
            (track.sink, "outputs", track.outputs, sinks),
        ]:
            if not role:
                continue
            route: list[Dest] = []
            for entry in entries:
                where = f"track {track.name!r}: {label} entry {entry}"
                has_bus, has_channel = "engine_bus" in entry, "engine_channel" in entry
                if has_bus and has_channel:
                    problems.append(f"{where} mixes bus and channel forms")
                elif has_bus or has_channel:
                    key = "engine_bus" if has_bus else "engine_channel"
                    problem = _index_problem(where, key, entry[key])
                    if problem:
                        problems.append(problem)
                    else:
                        route.append((bus if has_bus else channel)(entry[key]))
                else:
                    problems.append(
                        f"{where} names no engine_bus or engine_channel, so which "
                        f"Sushi {label[:-1]} the role {role!r} takes is unknown"
                    )
            if route and tuple(route) not in into.setdefault(role, []):
                into[role].append(tuple(route))
    if problems:
        raise PatchbayError(problems)
    return feeds, sinks


def _as_routes(value: Any) -> list[Route]:
    """Accept a bare `Dest` for a one-entry route, which is what most callers mean."""
    return [(r,) if isinstance(r, Dest) else tuple(r) for r in value]


# --- generation --------------------------------------------------------------


@dataclass(frozen=True)
class Link:
    out_node: str
    out_port: str
    in_node: str
    in_port: str


def generate_patchbay(
    roles: Mapping[str, list[str]],
    feeds: Mapping[str, Any],
    sinks: Mapping[str, Any] | None = None,
    *,
    mono_to_stereo: Collection[str] = (),
    nodes: Mapping[str, str] | None = None,
    sushi_node: str = SUSHI_NODE,
    name: str = "rig",
) -> str:
    """qpwgraph patchbay XML from role -> ports and role -> where on Sushi.

    Raises `PatchbayError` listing every problem; never returns a patchbay it is
    unsure of, because an unwired track looks exactly like a working one.

    `feeds` and `sinks` map a role to its routes (see `routes_from_rig`); a bare
    list of `Dest` is accepted as one-entry routes.

    Pairing, which is the part worth being exact about:

    - A two-port role into a bus is left to right, in the order listed.
    - A one-port role into a bus is an error *unless the role is in
      `mono_to_stereo`*, in which case the port feeds both halves. Duplication
      is declared, never inferred from the port count: the committed patchbay
      has it only on the dead `Pro` profile, and the live `Multichannel`
      profile wires one input per channel. A rule inferred from one of them was
      wrong for the other, and silently.
    - A channel-form route pairs ports with its entries in order, so a stereo
      role works with `engine_channel: 0` and `engine_channel: 1`.
    - Anything else (three ports into a bus, a count that does not match the
      entries) is an error rather than a pick: choosing which port to drop, or
      summing two physical inputs into one, is the near-miss recorded in
      `amp_patchbay`.
    - Two different ports reaching the same Sushi input is an error for the same
      reason. Fanning one output to several places is not: Sushi outputs may go
      to monitors and headphones at once.

    `nodes` maps a port to the node name qpwgraph shows for it. The machine file
    holds only ports (`Audient iD14:capture_AUX0`), but the saved file keys links
    on node *and* port, and for an ALSA device the node is the profile
    ("Audient iD14 Multichannel") — which the port name does not contain and
    which is precisely what changes between profiles. Without it the node falls
    back to the port's own client name, which is right for Sushi and not for a
    device.
    """
    nodes = nodes or {}
    sinks = sinks or {}
    mono = set(mono_to_stereo)
    problems: list[str] = []

    def node_of(port: str) -> str:
        return nodes.get(port) or port.partition(":")[0]

    # Sushi input index -> physical ports feeding it, with the role that asked.
    into: dict[int, dict[str, str]] = {}
    # Sushi output index -> physical ports it feeds.
    out_of: dict[int, dict[str, str]] = {}
    # Physical playback port -> the Sushi outputs that feed it.
    playback: dict[str, set[int]] = {}

    for role, value in feeds.items():
        ports = roles.get(role)
        if ports is None:
            problems.append(_unknown_role(role, roles, "feeds a track"))
            continue
        for route in _as_routes(value):
            pairs = _pair_source(role, ports, route, role in mono)
            if isinstance(pairs, str):
                problems.append(pairs)
                continue
            for index, port in pairs:
                into.setdefault(index, {}).setdefault(port, role)

    for role, value in sinks.items():
        ports = roles.get(role)
        if ports is None:
            problems.append(_unknown_role(role, roles, "takes a track's output"))
            continue
        for route in _as_routes(value):
            pairs = _pair_sink(role, ports, route)
            if isinstance(pairs, str):
                problems.append(pairs)
                continue
            for index, port in pairs:
                out_of.setdefault(index, {}).setdefault(port, role)
                playback.setdefault(port, set()).add(index)

    for index, ports in sorted(into.items()):
        if len(ports) > 1:
            who = ", ".join(f"{p!r} (role {r!r})" for p, r in ports.items())
            problems.append(
                f"{sushi_node}:audio_input_{index} would be fed by more than one "
                f"port: {who} — they would be summed into one input"
            )
    for port, indexes in sorted(playback.items()):
        if len(indexes) > 1:
            outs = ", ".join(f"{sushi_node}:audio_output_{i}" for i in sorted(indexes))
            problems.append(f"playback port {port!r} would be fed by {outs} — summed")

    if problems:
        raise PatchbayError(problems)

    # Sorted at both levels so the bytes depend on the rig, not on which track
    # happens to come first in rig.yaml. Only an output can fan out to several
    # ports, so that is where dict order would otherwise leak.
    links: list[Link] = []
    for index, ports in sorted(into.items()):
        for port in sorted(ports):
            links.append(
                Link(node_of(port), port, sushi_node, f"{sushi_node}:audio_input_{index}")
            )
    for index, ports in sorted(out_of.items()):
        for port in sorted(ports):
            links.append(
                Link(sushi_node, f"{sushi_node}:audio_output_{index}", node_of(port), port)
            )
    return render_patchbay(links, name)


def _unknown_role(role: str, roles: Mapping[str, list[str]], doing: str) -> str:
    known = ", ".join(repr(r) for r in sorted(roles)) or "none"
    return f"role {role!r} {doing} but the machine file does not define it (it defines: {known})"


def _pair_source(
    role: str, ports: list[str], route: Route, mono: bool
) -> list[tuple[int, str]] | str:
    """(Sushi input index, physical port) pairs, or why the role cannot feed here."""
    if mono and len(ports) != 1:
        return (
            f"role {role!r} declares mono_to_stereo but has {len(ports)} ports; "
            "it duplicates one port, so it needs exactly one"
        )
    pairs: list[tuple[int, str]] = []
    for dest in (d for d in route if d.kind == "bus"):
        chans = dest.channels()
        if len(ports) == 1 and mono:
            pairs += [(c, ports[0]) for c in chans]
        elif len(ports) == 1:
            return (
                f"role {role!r} has 1 port but feeds bus {dest.index}, a stereo "
                "pair. If one mono source should drive both halves, declare it: "
                f"{role}: {{ports: [{ports[0]!r}], mono_to_stereo: true}}. "
                "Otherwise list two ports"
            )
        elif len(ports) == 2:
            pairs += list(zip(chans, ports))
        else:
            return (
                f"role {role!r} has {len(ports)} ports but feeds bus {dest.index}, "
                "a stereo pair; give it two (left, right) or one with mono_to_stereo"
            )
    channels = [d for d in route if d.kind == "channel"]
    if channels:
        if len(ports) == len(channels):
            pairs += [(d.index, p) for d, p in zip(channels, ports)]
        elif len(ports) == 1 and mono:
            pairs += [(d.index, ports[0]) for d in channels]
        else:
            return (
                f"role {role!r} has {len(ports)} port(s) but this track routes it "
                f"to {len(channels)} engine channel(s) "
                f"({', '.join(str(d.index) for d in channels)}); each channel takes "
                "one port, so the counts must match"
            )
    return pairs


def _pair_sink(role: str, ports: list[str], route: Route) -> list[tuple[int, str]] | str:
    """(Sushi output index, physical port) pairs, or why the role cannot take it."""
    pairs: list[tuple[int, str]] = []
    for dest in (d for d in route if d.kind == "bus"):
        if len(ports) != 2:
            # No mono-into-stereo rule on the way out. Folding two channels into
            # one port is a mix-down, and which mix-down is a decision nobody has
            # made.
            return (
                f"role {role!r} has {len(ports)} port(s) but takes bus "
                f"{dest.index}, a stereo pair, which needs 2"
            )
        pairs += list(zip(dest.channels(), ports))
    channels = [d for d in route if d.kind == "channel"]
    if channels:
        if len(ports) != len(channels):
            return (
                f"role {role!r} has {len(ports)} port(s) but this track takes "
                f"{len(channels)} engine channel(s) "
                f"({', '.join(str(d.index) for d in channels)}), which needs "
                f"{len(channels)}"
            )
        pairs += [(d.index, p) for d, p in zip(channels, ports)]
    return pairs


# The shape qpwgraph itself writes, including the blank line after the closing
# tag and the attribute order. qpwgraph reads this file, so it is matched rather
# than improved.
_HEAD = '<!DOCTYPE patchbay>\n<patchbay name={name} version={version}>\n <items>\n'
_ITEM = (
    '  <item node-type="pipewire" port-type="pipewire-audio">\n'
    '   <output node={out_node} port={out_port}/>\n'
    '   <input node={in_node} port={in_port}/>\n'
    '  </item>\n'
)
_TAIL = ' </items>\n</patchbay>\n\n'


def render_patchbay(links: list[Link], name: str = "rig") -> str:
    # quoteattr, not an f-string: a device description with an ampersand or a
    # quote in it is a valid node name and must not corrupt the file.
    out = [_HEAD.format(name=quoteattr(name), version=quoteattr(PATCHBAY_VERSION))]
    for link in links:
        out.append(
            _ITEM.format(
                out_node=quoteattr(link.out_node), out_port=quoteattr(link.out_port),
                in_node=quoteattr(link.in_node), in_port=quoteattr(link.in_port),
            )
        )
    out.append(_TAIL)
    return "".join(out)


def parse_patchbay(xml: str) -> list[Link]:
    """The links in a saved patchbay, in file order.

    A real XML parse, unlike `amp_patchbay`'s regex: a check that misreads the
    file reports the wrong nodes as missing, and the cost of that is the user
    deleting links that were fine.
    """
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        raise PatchbayError([f"not a readable patchbay: {exc}"]) from exc
    links = []
    for item in root.iter("item"):
        out, inp = item.find("output"), item.find("input")
        if out is None or inp is None:
            raise PatchbayError(["an <item> is missing its <output> or <input>"])
        links.append(
            Link(
                out.get("node", ""), out.get("port", ""),
                inp.get("node", ""), inp.get("port", ""),
            )
        )
    return links


# --- what is actually there --------------------------------------------------

_NODE = "PipeWire:Interface:Node"
_PORT = "PipeWire:Interface:Port"


def _props(obj: dict) -> dict:
    return (obj.get("info") or {}).get("props") or {}


def _objects(dump: str) -> list[dict]:
    try:
        parsed = json.loads(dump)
    except ValueError as exc:
        raise PatchbayError([f"pw-dump did not produce JSON: {exc}"]) from exc
    if not isinstance(parsed, list):
        raise PatchbayError(["pw-dump output is not a list of objects"])
    return [o for o in parsed if isinstance(o, dict)]


def _display_name(props: dict) -> str | None:
    # description, then nick, then name: what qpwgraph's node labels look like
    # for the one device whose labels we have ("Audient iD14 Multichannel" reads
    # as a description). The order is inferred from that, not read from
    # qpwgraph's source.
    for key in ("node.description", "node.nick", "node.name"):
        if props.get(key):
            return props[key]
    return None


def live_node_names(dump: str) -> set[str]:
    """Every name a live node answers to: description, nick and node.name.

    Deliberately generous. qpwgraph's label for a node is derived from these, but
    exactly how is not settled here, so a saved name is accepted if it matches
    any of them. That errs towards missing a stale link rather than inventing
    one, which suits a check people will stop reading if it cries wolf.

    Raises if the graph holds no nodes at all. PipeWire answering with nothing
    is a check that could not look, and reporting every link stale on that would
    read as a finding when it is not.
    """
    names: set[str] = set()
    found_node = False
    for obj in _objects(dump):
        if obj.get("type") != _NODE:
            continue
        found_node = True
        props = _props(obj)
        names.update(props[k] for k in ("node.description", "node.nick", "node.name") if props.get(k))
    if not found_node:
        raise PatchbayError(["the PipeWire graph holds no nodes — cannot tell what is missing"])
    return names


@dataclass(frozen=True)
class LivePort:
    node: str
    # PipeWire's `port.direction`, "in" or "out", from the node's side: a
    # capture port is an "out" (the node produces audio), a playback port an "in".
    direction: str | None


def live_port_nodes(dump: str) -> dict[str, list[LivePort]]:
    """Port alias -> the ports that carry it, with their node and direction.

    A list because nothing makes an alias unique: `Audient iD14:capture_AUX0` is
    the same under every ALSA profile of the device, so two profiles live at once
    would both answer to it.
    """
    objects = _objects(dump)
    node_name = {
        o.get("id"): _display_name(_props(o)) for o in objects if o.get("type") == _NODE
    }
    found: dict[str, list[LivePort]] = {}
    for obj in objects:
        if obj.get("type") != _PORT:
            continue
        props = _props(obj)
        alias, owner = props.get("port.alias"), node_name.get(props.get("node.id"))
        if alias and owner:
            port = LivePort(owner, props.get("port.direction"))
            if port not in found.setdefault(alias, []):
                found[alias].append(port)
    return found


def resolve_nodes(
    roles: Mapping[str, list[str]],
    sources: Collection[str],
    sinks: Collection[str],
    port_nodes: Mapping[str, list[LivePort]],
) -> tuple[dict[str, str], list[str]]:
    """port -> node name for every port of the roles in use, plus what failed.

    Only roles in `sources` or `sinks`: a machine file may describe hardware
    that is unplugged today, and that is only a problem if a track needs it.

    A source role's ports must be PipeWire outputs (capture) and a sink role's
    inputs (playback). Without that, a playback port named in a source role
    resolves cleanly and the patchbay asks for a link PipeWire can never make,
    which looks exactly like a working file.

    This is the issue's "a role that resolves to no live port fails loudly,
    naming the role and what it looked for".
    """
    resolved: dict[str, str] = {}
    problems: list[str] = []
    wanted = {r: "out" for r in sources}
    for role in sinks:
        if role in wanted:
            problems.append(f"role {role!r} is used as both a source and a sink")
        wanted[role] = "in"
    for role in sorted(set(wanted) & set(roles)):
        expected = wanted[role]
        use = "source (capture)" if expected == "out" else "sink (playback)"
        for port in roles[role]:
            found = port_nodes.get(port, [])
            if not found:
                problems.append(
                    f"role {role!r}: no live port with alias {port!r} — is the "
                    "interface connected, and is this its current name?"
                )
                continue
            right = [p for p in found if p.direction == expected]
            if not right:
                seen = ", ".join(sorted({repr(p.direction) for p in found}))
                problems.append(
                    f"role {role!r}: port {port!r} is used as a {use} and so must "
                    f"have direction {expected!r}, but the live graph says {seen}"
                )
            elif len(right) > 1:
                owners = ", ".join(repr(p.node) for p in right)
                problems.append(
                    f"role {role!r}: port {port!r} exists on more than one node "
                    f"({owners}); cannot tell which one the patchbay should name"
                )
            else:
                resolved[port] = right[0].node
    return resolved, problems


@dataclass(frozen=True)
class NodeCheck:
    """What the live graph says about each node a saved patchbay names.

    Three states, not two. "Present" is a name that matches a live node.
    "Missing" is a name in a form we know how to match and that matched nothing.
    "Unevaluated" is a form we do not know how to match, so the honest answer is
    neither: reporting it missing makes the check fail on a healthy rig, and a
    check that fails on its first run gets ignored.
    """

    missing: dict[str, list[Link]]
    unevaluated: dict[str, list[Link]]


def _can_match(node: str) -> bool:
    # qpwgraph shows some clients as "<client>/<node>" (`PipeWire ALSA
    # [fmit]/ALSA Capture [fmit]` in the committed file). How that string is
    # composed from PipeWire's properties is not established, so a name of that
    # shape cannot be judged either way. A device description that really does
    # contain a slash is still matched first, below, by being live.
    return "/" not in node


def check_nodes(links: list[Link], live: set[str]) -> NodeCheck:
    """Classify each node the links name against the live node names.

    A link with both ends in a bad state appears under each. Keyed by node
    because that is what the user acts on: a missing node is one profile or one
    app, however many links it carries.
    """
    missing: dict[str, list[Link]] = {}
    unevaluated: dict[str, list[Link]] = {}
    for link in links:
        for node in (link.out_node, link.in_node):
            if node in live:
                continue
            bucket = (missing if _can_match(node) else unevaluated).setdefault(node, [])
            if link not in bucket:
                bucket.append(link)
    return NodeCheck(missing, unevaluated)


# --- effects ------------------------------------------------------------------


def read_pw_dump(timeout: float = PW_DUMP_TIMEOUT) -> str:
    """The live graph as `pw-dump` prints it.

    Not `pw-link -l`, which `qpwgraph.node_ports` parses for the tuner: that
    names nodes by `node.name`, while the saved patchbay names them by the label
    qpwgraph shows ("Audient iD14 Multichannel"). Those are different strings
    for an ALSA device, so a `pw-link` listing could not tell a stale profile
    from a live one — which is the whole question here.
    """
    try:
        result = subprocess.run(
            ["pw-dump"], capture_output=True, text=True, check=False, timeout=timeout
        )
    except FileNotFoundError as exc:
        raise PatchbayError(["pw-dump is not installed, so the live graph cannot be read"]) from exc
    except OSError as exc:
        # Not executable, say. Converted here so no caller mistakes it for a
        # failure to read the file it was asked about.
        raise PatchbayError([f"pw-dump could not be run: {exc}"]) from exc
    except subprocess.TimeoutExpired as exc:
        raise PatchbayError([f"pw-dump did not answer within {timeout:g}s"]) from exc
    if result.returncode != 0:
        raise PatchbayError(
            [f"pw-dump failed (exit {result.returncode}): {result.stderr.strip() or 'no message'}"]
        )
    return result.stdout
