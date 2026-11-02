"""A mono plugin on a stereo channel, and the compiler's copy to stereo after it.

Sonny on the rig, 2026-11-02: "I only get the left channel out." Every channel
is a stereo track and a guitar reaches both of its halves, but Sushi 1.3.0
gives a plugin on a track min(its own audio ports, the track's channels) of
each (src/engine/audio_engine.cpp `add_plugin_to_track`) and then clears every
channel the plugin did not write, "to avoid passing garbage to the next
processor" (src/engine/track.cpp `_process_plugins`). So a plugin with one
output -- the Guitarix compressor that starts every guitar chain, the Tube
Screamer, the octavers -- leaves the right channel silent from there on, and
bypassing it changes nothing, since a bypassed processor keeps its channel
count (processor.cpp `bypass_process`). Measured in CI's container
(tests/test_stereo.py, 2026-11-02): right at about -279 dBFS for seven of the
eleven rigs, and only a later reverb's or delay's crossfeed in the other four.

A DAW puts a mono plugin's output in the middle of a stereo track. The
compiler does that here: after every plugin whose LV2 description has one
audio output it inserts `sushi.testing.mono_summing`, Sushi's own internal
plugin, which writes the sum of its input channels to each output. Directly
after a one-output plugin the right channel has just been cleared, so the sum
is the left channel exactly, on both: measured, left and right within 0.3 dB
on every rig with it in place (CI, ba0f918). It has no
parameters to set, and is never after a stereo plugin, where it would fold
the stereo image down and add 6 dB.

## Known by uid, named for a person reading the chain

The copy is `<plugin>_to_stereo` -- what Sushi's processor list and `/live`
show, so `compressor` followed by `compressor_to_stereo` reads as what it is
-- with `COPY_UID` as its uid. Like the sends and the return it is the
compiler's: never in a rig file, never saved, not shown by the web UI, never
moved, removed or bypassed on its own. It moves and is removed with its
plugin, as one unit (`units`), and a live add of a one-output plugin adds it
in the same operation. Names ending in `_to_stereo` are refused for anything
else (`reserved_problem`), so the name alone is unambiguous where no uid was
read. Sushi 1.3.0 keeps processor names in a map from std::string with no
length limit (processor_container.cpp), so the suffix cannot push a name past
one; it only has to be unique, which the reserved suffix makes it.

## Where the counts come from

Sushi reports no channel counts: `--dump-plugins` gives each processor's name,
label, ids and parameters (parameter_dump.cpp) and gRPC's `ProcessorInfo` its
id, label, name and parameter and program counts (elkpy 1.3.0's
sushi_rpc.proto). lilv reads them from the plugin's own Turtle, but lilv is a
system package the compiler cannot count on (CI's bare runner, the agent on
a machine without it). So they are committed, in `plugin-ports.txt` beside the
manifest, and CI checks every line against lilv over `plugins/`. A plugin the
table does not list is refused by name rather than guessed at: guessing stereo
is the silent right channel again, and guessing mono folds a stereo plugin.

## What this does not do

On a channel whose input is stereo, a one-output plugin hears the left input
alone, and its copy puts that on both sides: the right input is lost from
there, as Sushi has always done it, now centred. No committed rig has a stereo
channel. A plugin with no audio output at all silences everything after it on
the track, and is refused.

NOT HEARD ON THE RIG: everything above about the sound is from offline renders
in CI's container; the guitar through the iD14's two monitors is the test.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

COPY_UID = "sushi.testing.mono_summing"
COPY_SUFFIX = "_to_stereo"
PORTS_FILE = "plugin-ports.txt"


class PortsError(ValueError):
    """`plugin-ports.txt` cannot be read: every line that is wrong."""

    def __init__(self, problems: list[str]):
        self.problems = problems
        super().__init__("\n".join(problems))


@dataclass(frozen=True)
class Ports:
    inputs: int
    outputs: int


def read_ports(text: str) -> dict[str, Ports]:
    """`{uri: Ports}` from the table's text: `inputs outputs uri` per line,
    blanks and `#` comments ignored. Refuses a malformed line, or a URI
    listed twice, with its line number."""
    found: dict[str, Ports] = {}
    problems = []
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) != 3 or not parts[0].isdigit() or not parts[1].isdigit():
            problems.append(f"{PORTS_FILE} line {number}: expected 'inputs outputs uri', got {raw!r}")
            continue
        uri = parts[2]
        if uri in found:
            problems.append(f"{PORTS_FILE} line {number}: {uri} is listed twice")
            continue
        found[uri] = Ports(int(parts[0]), int(parts[1]))
    if problems:
        raise PortsError(problems)
    return found


_CACHE: dict[Path, tuple[int, dict[str, Ports]]] = {}


def load_ports(root: Path) -> dict[str, Ports]:
    """The committed table at `root`, read once per change to the file."""
    path = Path(root) / PORTS_FILE
    try:
        stamp = path.stat().st_mtime_ns
    except FileNotFoundError:
        raise PortsError([f"no {PORTS_FILE} at {root}: the compiler needs each plugin's audio ports"])
    cached = _CACHE.get(path)
    if cached is None or cached[0] != stamp:
        cached = (stamp, read_ports(path.read_text()))
        _CACHE[path] = cached
    return cached[1]


def copy_name(processor: str) -> str:
    return processor + COPY_SUFFIX


def is_copy(processor: str, uid: str | None = None) -> bool:
    """Whether `processor` is a compiler's copy to stereo: by uid where one was
    read, and by the reserved suffix, which nothing else may carry."""
    return uid == COPY_UID or processor.endswith(COPY_SUFFIX)


def source_of(copy: str) -> str:
    return copy[: -len(COPY_SUFFIX)] if copy.endswith(COPY_SUFFIX) else copy


def reserved_problem(what: str, name: str) -> str | None:
    """Why `name` cannot be a plugin's or a channel's, or None."""
    if isinstance(name, str) and name.endswith(COPY_SUFFIX):
        return (f"{what} {name!r}: names ending in {COPY_SUFFIX!r} are the compiler's, for the copy "
                "to stereo it puts after a mono plugin; rename it")
    return None


def plugin_problem(name: str, uri: str | None, ports: Mapping[str, Ports],
                   amp_uris: frozenset[str] = frozenset()) -> str | None:
    """Why an LV2 plugin cannot be compiled onto a stereo track, or None."""
    if uri in amp_uris:
        return (f"plugin {name!r} is the amp ({uri}), which runs in Carla, not on a Sushi chain: "
                "give it to the rig's `amp:` instead")
    if uri not in ports:
        return (f"plugin {name!r} ({uri}) is not in {PORTS_FILE}, so the compiler cannot tell whether "
                f"it is mono: `sushi-rig probe {uri}` prints its audio ports; add a line "
                f"'<in> <out> {uri}' to {PORTS_FILE}")
    if ports[uri].outputs == 0:
        return (f"plugin {name!r} ({uri}) has no audio output, so on a track it would silence "
                "everything after it")
    return None


def needs_copy(uri: str | None, ports: Mapping[str, Ports]) -> bool:
    """Whether an LV2 plugin needs its copy to stereo after it: one output."""
    return uri in ports and ports[uri].outputs == 1


def copy_entry(processor: str) -> dict[str, Any]:
    """The copy as a compiled config's plugin entry."""
    return {"name": copy_name(processor), "type": "internal", "uid": COPY_UID}


# --------------------------------------------------------------------------- #
# Units: a plugin and its copy, which move and go together
# --------------------------------------------------------------------------- #

def units(chain: list[str], uids: Mapping[str, str | None] | None = None) -> list[list[str]]:
    """`chain` as units: each processor alone, or followed by its copy. A copy
    is a unit with its plugin only when it directly follows it; one found
    anywhere else is a unit by itself, so it is still never mistaken for the
    plugin's and can be refused by name."""
    uids = uids or {}
    out: list[list[str]] = []
    for name in chain:
        if (out and is_copy(name, uids.get(name)) and len(out[-1]) == 1
                and copy_name(out[-1][0]) == name):
            out[-1].append(name)
        else:
            out.append([name])
    return out


def step_units(chain: list[str], processor: str, direction: int,
               uids: Mapping[str, str | None] | None = None) -> list[str] | None:
    """`chain` after `processor`'s unit swaps with its neighbour unit one step
    earlier (direction < 0) or later, or None if `processor` is not the head
    of a unit on it or is already at that end. Pure: what a move does, and
    what undo checks a move left."""
    grouped = units(chain, uids)
    index = next((i for i, unit in enumerate(grouped) if unit[0] == processor), None)
    if index is None:
        return None
    other = index + (-1 if direction < 0 else 1)
    if not 0 <= other < len(grouped):
        return None
    grouped[index], grouped[other] = grouped[other], grouped[index]
    return [name for unit in grouped for name in unit]


def strip_copies(plugins: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """A compiled chain without the compiler's copies."""
    return [p for p in plugins if not is_copy(str(p.get("name", "")), p.get("uid"))]


def pin_copies(plugins: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Each copy directly after its plugin, everything else in its order: a
    capture's order or `emit`'s appending can put a copy anywhere, and a copy
    anywhere else is either a silent right channel or a stereo plugin folded."""
    copies = {p["name"]: p for p in plugins if is_copy(str(p.get("name", "")), p.get("uid"))}
    names = set(copies)
    out = []
    for plugin in plugins:
        if plugin["name"] in names:
            continue
        out.append(plugin)
        mine = copies.pop(copy_name(plugin["name"]), None)
        if mine is not None:
            out.append(mine)
    # A copy whose plugin is not on this track is the compiler's own mistake;
    # kept, at the end, so Sushi still loads what the compiler meant and the
    # stereo renders fail loudly rather than this dropping it quietly.
    return out + list(copies.values())
