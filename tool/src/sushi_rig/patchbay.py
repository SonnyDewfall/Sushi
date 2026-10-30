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
from typing import Any, Mapping
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


def routes_from_rig(rig: Any) -> tuple[dict[str, list[Dest]], dict[str, list[Dest]]]:
    """role -> where it feeds Sushi, and role -> what feeds it, from a `RigSpec`.

    Only entries the spec itself accepts are read: one that mixes bus and channel
    forms is `RigSpec.validate()`'s to report, and guessing which half was meant
    here would route audio on a guess.
    """
    feeds: dict[str, list[Dest]] = {}
    sinks: dict[str, list[Dest]] = {}
    for track in rig.tracks:
        for role, entries, into in [
            (track.source, track.inputs, feeds),
            (track.sink, track.outputs, sinks),
        ]:
            if not role:
                continue
            for entry in entries:
                if "engine_bus" in entry and "engine_channel" not in entry:
                    dest = bus(entry["engine_bus"])
                elif "engine_channel" in entry and "engine_bus" not in entry:
                    dest = channel(entry["engine_channel"])
                else:
                    continue
                if dest not in into.setdefault(role, []):
                    into[role].append(dest)
    return feeds, sinks


# --- generation --------------------------------------------------------------


@dataclass(frozen=True)
class Link:
    out_node: str
    out_port: str
    in_node: str
    in_port: str


def generate_patchbay(
    roles: Mapping[str, list[str]],
    feeds: Mapping[str, list[Dest]],
    sinks: Mapping[str, list[Dest]] | None = None,
    *,
    nodes: Mapping[str, str] | None = None,
    sushi_node: str = SUSHI_NODE,
    name: str = "rig",
) -> str:
    """qpwgraph patchbay XML from role -> ports and role -> where on Sushi.

    Raises `PatchbayError` listing every problem; never returns a patchbay it is
    unsure of, because an unwired track looks exactly like a working one.

    Pairing, which is the part worth being exact about:

    - A one-port role into a stereo bus feeds *both* halves. That is the guitar,
      and it is what lets a stereo chain follow a mono source.
    - A two-port role into a bus is left to right, in the order listed.
    - Anything else (three ports into a bus, two into a single channel) is an
      error rather than a pick: choosing which port to drop, or summing two
      physical inputs into one, is the near-miss recorded in `amp_patchbay`.
    - Two roles reaching the same Sushi input is an error for the same reason.
      Fanning one output to several places is not: Sushi outputs may go to
      monitors and headphones at once.

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
    problems: list[str] = []

    def node_of(port: str) -> str:
        return nodes.get(port) or port.partition(":")[0]

    # Sushi input index -> physical ports feeding it, with the role that asked.
    into: dict[int, dict[str, str]] = {}
    # Sushi output index -> physical ports it feeds.
    out_of: dict[int, dict[str, str]] = {}
    # Physical playback port -> the Sushi outputs that feed it.
    playback: dict[str, set[int]] = {}

    for role, dests in feeds.items():
        ports = roles.get(role)
        if ports is None:
            problems.append(_unknown_role(role, roles, "feeds a track"))
            continue
        for dest in dests:
            pairs = _pair_source(role, ports, dest)
            if isinstance(pairs, str):
                problems.append(pairs)
                continue
            for index, port in pairs:
                into.setdefault(index, {}).setdefault(port, role)

    for role, dests in sinks.items():
        ports = roles.get(role)
        if ports is None:
            problems.append(_unknown_role(role, roles, "takes a track's output"))
            continue
        for dest in dests:
            pairs = _pair_sink(role, ports, dest)
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

    links: list[Link] = []
    for index, ports in sorted(into.items()):
        for port in ports:
            links.append(
                Link(node_of(port), port, sushi_node, f"{sushi_node}:audio_input_{index}")
            )
    for index, ports in sorted(out_of.items()):
        for port in ports:
            links.append(
                Link(sushi_node, f"{sushi_node}:audio_output_{index}", node_of(port), port)
            )
    return render_patchbay(links, name)


def _unknown_role(role: str, roles: Mapping[str, list[str]], doing: str) -> str:
    known = ", ".join(repr(r) for r in sorted(roles)) or "none"
    return f"role {role!r} {doing} but the machine file does not define it (it defines: {known})"


def _pair_source(role: str, ports: list[str], dest: Dest) -> list[tuple[int, str]] | str:
    """(Sushi input index, physical port) pairs, or why the role cannot feed here."""
    chans = dest.channels()
    if dest.kind == "channel":
        if len(ports) != 1:
            return (
                f"role {role!r} has {len(ports)} ports but feeds the single channel "
                f"{dest.index}; a channel takes exactly one"
            )
        return [(chans[0], ports[0])]
    if len(ports) == 1:
        return [(c, ports[0]) for c in chans]
    if len(ports) == 2:
        return list(zip(chans, ports))
    return (
        f"role {role!r} has {len(ports)} ports but feeds bus {dest.index}, "
        "a stereo pair; give it one (mono, fed to both sides) or two"
    )


def _pair_sink(role: str, ports: list[str], dest: Dest) -> list[tuple[int, str]] | str:
    """(Sushi output index, physical port) pairs, or why the role cannot take it."""
    chans = dest.channels()
    if len(ports) != len(chans):
        # No mono-into-stereo rule on the way out. Folding two channels into one
        # port is a mix-down, and which mix-down is a decision nobody has made.
        what = f"bus {dest.index}, a stereo pair" if dest.kind == "bus" else f"channel {dest.index}"
        return (
            f"role {role!r} has {len(ports)} port(s) but takes {what}, which needs "
            f"{len(chans)}"
        )
    return list(zip(chans, ports))


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

    The one shape it does not accept is qpwgraph's "<client>/<node>" form
    (`PipeWire ALSA [fmit]/ALSA Capture [fmit]` in the committed file): how that
    is composed is unverified, so such a node reports as missing. Wrong in the
    loud direction, which for this check is the right one.

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


def live_port_nodes(dump: str) -> dict[str, list[str]]:
    """Port alias -> the node names that own a port with it.

    A list because nothing makes an alias unique: `Audient iD14:capture_AUX0` is
    the same under every ALSA profile of the device, so two profiles live at once
    would both answer to it.
    """
    objects = _objects(dump)
    node_name = {
        o.get("id"): _display_name(_props(o)) for o in objects if o.get("type") == _NODE
    }
    found: dict[str, list[str]] = {}
    for obj in objects:
        if obj.get("type") != _PORT:
            continue
        props = _props(obj)
        alias, owner = props.get("port.alias"), node_name.get(props.get("node.id"))
        if alias and owner and owner not in found.setdefault(alias, []):
            found[alias].append(owner)
    return found


def resolve_nodes(
    roles: Mapping[str, list[str]],
    used: set[str],
    port_nodes: Mapping[str, list[str]],
) -> tuple[dict[str, str], list[str]]:
    """port -> node name for every port of the roles in use, plus what failed.

    Only roles in `used`: a machine file may describe hardware that is unplugged
    today, and that is only a problem if a track needs it.

    This is the issue's "a role that resolves to no live port fails loudly,
    naming the role and what it looked for".
    """
    resolved: dict[str, str] = {}
    problems: list[str] = []
    for role in sorted(used & set(roles)):
        for port in roles[role]:
            owners = port_nodes.get(port, [])
            if not owners:
                problems.append(
                    f"role {role!r}: no live port with alias {port!r} — is the "
                    "interface connected, and is this its current name?"
                )
            elif len(owners) > 1:
                problems.append(
                    f"role {role!r}: port {port!r} exists on more than one node "
                    f"({', '.join(map(repr, owners))}); cannot tell which one the "
                    "patchbay should name"
                )
            else:
                resolved[port] = owners[0]
    return resolved, problems


def stale_nodes(links: list[Link], live: set[str]) -> dict[str, list[Link]]:
    """Saved links whose node is not live, grouped by the missing node.

    A link with both ends missing appears under both. Keyed by node because that
    is what the user acts on: a missing node is one profile or one app, however
    many links it carries.
    """
    stale: dict[str, list[Link]] = {}
    for link in links:
        for node in (link.out_node, link.in_node):
            if node not in live:
                bucket = stale.setdefault(node, [])
                if link not in bucket:
                    bucket.append(link)
    return stale


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
    except subprocess.TimeoutExpired as exc:
        raise PatchbayError([f"pw-dump did not answer within {timeout:g}s"]) from exc
    if result.returncode != 0:
        raise PatchbayError(
            [f"pw-dump failed (exit {result.returncode}): {result.stderr.strip() or 'no message'}"]
        )
    return result.stdout
