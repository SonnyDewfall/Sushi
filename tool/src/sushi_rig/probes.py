"""Where selftest injects and listens: the ports each probe links.

Issue #49, part 2's decisions. `selftest.py` says what a capture means; this
says what to capture. Given the rig and whether the amp is in it, it lists every
probe point as the ports a test signal is linked into, the one port it is read
back from, and the track it is played through — nothing here touches PipeWire
or Sushi. The effect that builds that track, links it, plays the stimulus and
records both channels together needs the rig machine.

## On a track of its own, not beside the instrument

Decided 2026-11-02 (#81). A probe through Sushi drives a **probe track**,
`selftest_probe`: the track under test, with its engine inputs and outputs moved
to ones no track in the rig uses, carrying a copy of its chain. The stimulus is
the only thing linked into those inputs and nothing is linked to those outputs,
so the instrument stays out of the measurement, the player hears nothing, and
the guitar keeps playing through its own track untouched.

This replaced injecting into the track's own inputs, beside the instrument,
which failed twice over:

- **Sushi reads only 64 frames of a doubly-linked input.** Measured on the rig
  machine on 31 Oct 2026 (`tool/spikes/live_rig/live_paths.py`, its
  `live-paths-*.json` and the results file beside them): PipeWire does sum two
  links into one input, at unity — but Sushi 1.3.0's JACK frontend asks for
  `AUDIO_CHUNK_SIZE` (64) frames of each input per chunk, and PipeWire mixes
  only the frames asked for (the cause as read from `jack_frontend.cpp` and
  recorded in #81), so a Sushi input with two links carries the first
  64 frames of every quantum and zeros for the rest. At the rig's 256 the
  stimulus read back at a quarter of its level (0.2529 for 1.0), correctly
  aligned, with nothing to say so — and the guitar the player hears, sharing
  the input, would have been chopped the same way for the probe's duration. The
  spike's own `pipewire_sums_two_links` check reads `false` for this reason; the
  sum is PipeWire's and correct, the truncation is Sushi's.
- **The instrument is in the measurement, and the player hears the probe.**
  Even summed correctly, a guitar plugged in adds its noise to every figure and
  hears a test tone through the monitors, so the selftest could only be run
  with the instrument unplugged.

**Unlinking the instrument for the probe, and relinking after, was the
alternative, and is rejected.** It would measure the running tracks themselves,
which this does not (below). But:

- qpwgraph *maintains* its saved patch and reconnects a link that goes missing
  — `amp.amp_patchbay` records it undoing a by-hand rewiring, repeatedly. An
  unlinked guitar would come back by itself mid-probe, putting two links on
  the input again: the truncation and the guitar back in the measurement, with
  nothing reporting either. Holding the unlink means stopping qpwgraph or
  swapping its file for the probe, a second thing to put back.
- A selftest killed between unlink and relink leaves the guitar silent, or
  leaves qpwgraph to decide. Recording the unlink so a later run can restore it
  is a journal and a recovery path, state changed at runtime in the one place
  principle 9 says state must not live.
- Working as meant, it still takes the instrument away for the test and plays
  the stimulus to the monitors.

Summing upstream — the stimulus mixed into the guitar in a node of our own,
one link into Sushi — has the same unlink at its heart, or else puts a mixing
node permanently in the path the player hears, for the test's sake.

A probe track needs nothing restored. Killed mid-probe, it leaves a track on
inputs nothing feeds and outputs nothing hears; the next selftest is to
remove it by name first (selftest.py lists what the effect owes), and the next
`up` starts Sushi without it. Until then `live.capture` leaves it out and says
so, so a SAVE made meanwhile is the rig's and is not refused as a capture of
another; the name is kept from every rig (`spec.PROBE_TRACK`) for that reason.

"Nothing feeds" and "nothing hears" are true of the rig's own links, and are
checked live rather than assumed: `crowded_ports` reads `pw-link -l` and names
anything else on either end, an input linked twice or an output linked to the
monitors or a recorder by a stale patchbay or a killed run.

**What the copy costs.** The probe measures a twin, built in the same Sushi at
the same quantum with the running chain's settings — so a plugin that passes
nothing, a NaN, a delay of the wrong number of blocks all show on it. What it
cannot see is a fault only the running instance has (a plugin blown up mid-set),
the running tracks' own output ports (`patchbay check` and the compiled
routing's tests cover those), and the channel's send and return (`verify`
reads each send's `destination_name` back, #88). Every plan with a probe
through Sushi says so in `not_checked`, so it reads as a limit and not a pass.

Internal sends and returns are left off the copy. A copied send would carry the
probe into the master the player hears, which is the thing this exists to
avoid; a copied return, under its new name, is fed by nothing.

## Every half of every output

A probe reads one output port. A track feeding a stereo bus gets a probe track
feeding a stereo bus, and one probe per half, so a chain that comes out of one
side only — a mono plugin on a stereo track, an output mapped to the wrong
channel — fails on the side that is silent. A probe that summed the two or
picked one would pass it. The probe track keeps the track's own shape: its
inputs and outputs are the track's entries with only the engine numbers moved,
so a mono channel's one input feeding both halves is probed as one input
feeding both halves, exactly as it is played.

What this does not catch is the guitar's own port feeding only one of Sushi's
inputs, the left-speaker-only failure this rig once had. That is the physical
side of the routing, `mono_to_stereo` in the machine file and `patchbay check`
against the live graph.

Every input of a probe track is driven at once, so a multibus track whose bus 1
input never reaches its bus 1 output still passes on signal mixed in from bus 0.
One stimulus per input bus would see it; no rig here has a multibus track yet.

The engine numbers moved to are the lowest Sushi has free (8 each way, as
`rigfile.MAX_FRONTEND_CHANNELS` says). A compiled rig of three channels fills
all eight outputs, and its chains are then listed as not probed with the
arithmetic, not probed beside the instrument as a fallback: a fallback here is
the 64-frame measurement above.

## The amp, twice, and beside the guitar

With the amp in the rig, two probes cover it:

- **amp** — `NAM:Input` to `NAM:Output`, asserted nonlinear. This is the
  failure that started #49: an amp with no model passes audio and adds nothing.
- **<track> via amp** — `NAM:Input`, through the amp, into the guitar track's
  probe track, asserted nonlinear too. `NAM:Output` is linked into the probe
  track's inputs for the probe (`ProbePoint.links`), beside the links
  `amp.amp_patchbay` gives it to the guitar's own: one source fanning out, one
  link into each Sushi input. Seen working on the rig: a link of exactly this
  shape, `NAM:Output -> sushi:audio_input_7`, was made with the rig up and was
  still there 15 seconds later.

The amp is not copied. It runs in Carla, not Sushi, and a second Carla would
pass while the running one is dead — which is #49's own failure. So both amp
probes inject into the amp the guitar is linked into, and each says so
(`ProbePoint.beside`): the instrument is in those measurements and the player
hears them through the guitar's own track, so they are run with the guitar
quiet. A guitar played over it shows as `not-a-sine` (selftest.py) rather than
as a pass.

A probe straight into Sushi asserts nothing about harmonics either way: a chain
may hold a distortion pedal, and the probe is about whether sound comes through.

## Through the master

A rig compiled from channels and a master (rigfile.py) is heard through a
master track that has no inputs: it is fed by each channel's send. The probe
for that path is a probe track carrying the channel's chain and then the
master's, out of outputs moved from the master's own — the send and return
between them are the part a copy cannot hold (above). With the master's chain
empty, as in every committed rig, that track would be the channel's own probe
again, so it is listed as not probed rather than measured twice — and with the
amp on, the amp to the monitors is listed by name too; a channel with
no output of its own is probed this way all the same, since it is its only
probe. The master is found by the compiler's names, not by any return: a
compiled rig has exactly one, and with any other number the path to the
monitors is listed as not probed rather than guessed.

## Separation is not probed yet

Whether one channel leaks into another needs the two to come out of different
ports, and in config/src's guitar_vocal they share bus 0. The plan says so for
every pair of running tracks that shares an output. Two tracks that share
their inputs as well are no longer a probe's problem: each is probed on its own
probe track, so a silent chain on one cannot hide behind the other.

## Seen on the rig, 31 Oct 2026

- That `NAM:Input` and `NAM:Output` are the names Carla gives the ports live.
- That qpwgraph is not in exclusive mode and leaves a link not in its file
  alone (the `NAM:Output -> sushi:audio_input_7` link above).
- That PipeWire sums two links into one input at unity — and that Sushi then
  reads 64 frames of each quantum, which is why this module now never puts two
  links on a Sushi input, and why `crowded_ports` exists.

## Not yet seen on the rig

- That Sushi 1.3.0 creates a track, its routing and its processors over gRPC
  while running, and deletes them again, without a glitch on the other tracks.
- That a chain rebuilt with its source's parameters, properties, program and
  bypass sounds as its source does; LV2 state a plugin keeps outside its
  parameters would not be copied.
- That Carla reads the whole quantum of `NAM:Input` with two links into it, as
  plain JACK clients do, rather than as Sushi does.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from .amp import AMP_INPUT, AMP_OUTPUT
from .patchbay import GUITAR_ROLE, SUSHI_NODE, Dest, bus, channel, track_entries, track_route
from .qpwgraph import listed_links
from .rigfile import (
    MASTER,
    MAX_FRONTEND_CHANNELS,
    RETURN,
    RETURN_UID,
    SEND_SUFFIX,
    SEND_UID,
)
from .spec import PROBE_TRACK, PluginSpec, TrackSpec, probe_track_problem

# What a copied processor's name gains; the probe track's own name is
# spec.PROBE_TRACK, kept from every rig there. Sushi names every track and
# processor in one namespace, so a copy cannot share its source's name; a rig
# already holding either name is refused, not renamed around, since a probe
# that deleted `<name>_probe` afterwards would delete the player's.
PROBE_SUFFIX = "_probe"

# Moved onto a copy, these would carry audio between tracks by name — see the
# module docstring.
_NOT_COPIED = frozenset({SEND_UID, RETURN_UID})


@dataclass(frozen=True)
class CopiedPlugin:
    """One processor on a probe track: `plugin` is created under its own name,
    and its settings are read off the running processor `source` — the chain as
    it is playing, not as the file says, which `verify` compares."""

    source: str
    plugin: PluginSpec


@dataclass(frozen=True)
class ProbeTrack:
    """The track a probe is played through: see the module docstring.

    `inputs` and `outputs` are Sushi routing entries, held as sorted pairs so
    the plan stays immutable; `to_spec` gives them back as Sushi writes them.
    `copy_of` is whose chains it carries, in order. `input_ports` and
    `output_ports` are those entries as Sushi's ports, worked out once when the
    track is planned.
    """

    name: str
    copy_of: tuple[str, ...]
    channels: int
    inputs: tuple[tuple[tuple[str, int], ...], ...]
    outputs: tuple[tuple[tuple[str, int], ...], ...]
    plugins: tuple[CopiedPlugin, ...] = ()
    multibus: bool = False
    buses: int | None = None
    input_ports: tuple[str, ...] = ()
    output_ports: tuple[str, ...] = ()

    def to_spec(self) -> TrackSpec:
        """The probe track as a `TrackSpec`, which is what the effect creates."""
        return TrackSpec(
            name=self.name,
            channels=self.channels,
            multibus=self.multibus,
            buses=self.buses,
            inputs=[dict(entry) for entry in self.inputs],
            outputs=[dict(entry) for entry in self.outputs],
            plugins=[copied.plugin for copied in self.plugins],
        )


@dataclass(frozen=True)
class ProbePoint:
    """One probe: link a stimulus into `inject`, read back from `capture`.

    `nonlinear` is True where the path must add harmonics (the amp), and None
    where it is not asserted either way. `name` is what a finding calls it.
    `track` is the probe track to build first, None for the amp on its own.
    `links` are the other links the probe makes for its duration, as (source,
    sink). `beside` is the role whose instrument also feeds `inject`: set only
    for the amp, which cannot be copied, and said so the player knows to be quiet.
    """

    name: str
    inject: tuple[str, ...]
    capture: str
    nonlinear: bool | None = None
    track: ProbeTrack | None = None
    links: tuple[tuple[str, str], ...] = ()
    beside: str | None = None


class ProbeError(Exception):
    """A rig whose probe points cannot be worked out. Carries every problem."""

    def __init__(self, problems: list[str]):
        self.problems = problems
        super().__init__("; ".join(problems))


@dataclass(frozen=True)
class ProbePlan:
    points: tuple[ProbePoint, ...]
    # What the probes cannot check, and why: said, not skipped silently, since
    # a track the selftest never touched must not read as one that passed.
    not_checked: tuple[str, ...] = ()


def _ports(sushi_node: str, direction: str, route) -> tuple[str, ...]:
    channels = [c for dest in route for c in dest.channels()]
    return tuple(f"{sushi_node}:audio_{direction}_{c}" for c in dict.fromkeys(channels))


def plan_probes(rig: Any, *, amp: bool, sushi_node: str = SUSHI_NODE) -> ProbePlan:
    """Every probe point in `rig`, or `ProbeError` naming everything wrong.

    `amp` is whether the amp runs for this rig: a config with an `_amp` model,
    or `up --amp-model`, and not `--no-amp`. With it, the track the guitar role
    feeds is the one the amp sits in front of — the role, not input 0, for the
    reason `amp.amp_patchbay` gives.
    """
    points: list[ProbePoint] = []
    not_checked: list[str] = []
    problems: list[str] = _name_problems(rig)
    # (track, its own outputs or None if broken, whether it sends to the master)
    guitar: list[tuple[Any, tuple[str, ...] | None, bool]] = []
    routed: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {}
    master = _master(rig)
    senders: list[tuple[Any, bool]] = []
    # The guitar's probe tracks, kept for the amp: (its own, through the master).
    twins: dict[str, list[ProbeTrack | None]] = {}
    # Why a track has no probe track, for the amp's note, per path: its own
    # (no room, or nothing to probe) and through the master (no room). The amp
    # note gives the cause of the path that was tried last, since a channel
    # with no stem is tried through the master and fails there for room.
    own_why: dict[str, str] = {}
    through_why: dict[str, str] = {}
    used = _used(rig)
    # The master's outputs, read once: every path to the monitors ends there.
    master_ports: tuple[str, ...] = ()
    master_broken: list[str] = []
    if master is not None:
        master_out, master_broken = track_route(master, "outputs")
        master_ports = _ports(sushi_node, "output", master_out)
    for track in rig.tracks:
        into, found = track_route(track, "inputs", track.source)
        out, more = track_route(track, "outputs", track.sink)
        inject = _ports(sushi_node, "input", into)
        captures = _ports(sushi_node, "output", out)
        is_master = master is not None and track is master
        sends = master is not None and _sends_to_master(track)
        if track.source == GUITAR_ROLE:
            # Recorded before anything can skip the track, so an amp in front
            # of a broken guitar track is reported as that, not as no guitar.
            guitar.append((track, None if found or more else captures, sends))
        if found or more:
            problems += found + more
            continue
        if _carries(track, SEND_UID) and not sends:
            not_checked.append(
                f"{track.name}: sends somewhere this plan cannot follow — where a send "
                "goes is in its state, and only a compiled rig's sends are known to "
                "feed its master — so what it adds to the monitors is not probed"
            )
        if sends and inject:
            senders.append((track, bool(captures)))
        if not inject or not captures:
            # A master is fed by sends rather than inputs, and a channel with no
            # output of its own is heard through the master: neither is unprobed.
            missing = "inputs" if not inject else "outputs"
            if not ((is_master and not inject) or (sends and inject and not captures)):
                not_checked.append(f"{track.name}: no {missing} on Sushi, so nothing to probe")
            own_why[track.name] = f"{track.name} has no {missing} on Sushi"
            continue
        routed[track.name] = (inject, captures)
        twin, why = _probe_track(track, track, (track,), used, sushi_node)
        twins.setdefault(track.name, [None, None])[0] = twin
        if twin is None:
            not_checked.append(f"{track.name}: {why}, so its chain is not probed")
            own_why[track.name] = why
            continue
        points += _through(twin, f"{track.name} through {sushi_node}", sushi_node)

    # A master whose outputs cannot be read is already in `problems`.
    if master is not None and senders and not master_broken:
        if not master_ports:
            not_checked.append(
                f"{', '.join(s.name for s, _ in senders)}: sent to the {master.name}, "
                "which has no outputs on Sushi, so nothing reaches the monitors to probe"
            )
        else:
            again = [s.name for s, own in senders if own and not _copied(master)]
            if again:
                not_checked.append(
                    f"{', '.join(again)} through the {master.name}: the {master.name}'s chain is empty, "
                    "so a probe of that path would measure the channel's chain again; "
                    "the send and return between them are not probed (`verify` reads "
                    "each send's destination back)"
                )
                amped = [t.name for t, _, _ in guitar] if amp and len(guitar) == 1 else []
                if amped and amped[0] in again and twins.get(amped[0], [None])[0] is not None:
                    # Named on its own: the amp heard in the monitors is what the
                    # amp is for, and a reader looking for it must find it said.
                    not_checked.append(
                        f"{amped[0]} via amp and the {master.name}: the {master.name}'s "
                        f"chain is empty, so the amp to the monitors is \"{amped[0]} via "
                        "amp\" again and is probed as that; the send and return into "
                        f"the {master.name} are not probed"
                    )
            for sender, own in senders:
                if sender.name in again:
                    continue
                twin, why = _probe_track(sender, master, (sender, master), used, sushi_node)
                twins.setdefault(sender.name, [None, None])[1] = twin
                if twin is None:
                    not_checked.append(
                        f"{sender.name} through the {master.name}: {why}, so it is not probed"
                    )
                    through_why[sender.name] = why
                    continue
                points += _through(twin, f"{sender.name} through the {master.name}", sushi_node)

    if amp:
        if not guitar:
            problems.append(
                f"the amp is on but no track takes {GUITAR_ROLE!r}, so it is in front "
                "of nothing; the amp follows that role"
            )
        elif len(guitar) > 1:
            problems.append(
                f"tracks {', '.join(repr(t.name) for t, _, _ in guitar)} all take "
                f"{GUITAR_ROLE!r}, so which one the amp sits in front of is not decided"
            )
        else:
            (track, captures, sends), = guitar
            monitors = sends and master is not None and bool(master_ports)
            if captures is not None and not captures and not monitors:
                problems.append(
                    f"the amp is in front of {track.name!r}, which has no outputs on Sushi "
                    "and no master to send to, so what the amp does can never be heard"
                )
            elif captures is not None:
                points.append(ProbePoint(
                    "amp", (AMP_INPUT,), AMP_OUTPUT, nonlinear=True, beside=GUITAR_ROLE,
                ))
                own, through = twins.get(track.name, [None, None])
                if own is None and through is None:
                    cause = (through_why.get(track.name) or own_why.get(track.name)
                             or f"{track.name} has no probe track")
                    not_checked.append(
                        f"{track.name} via amp: {cause}, so whether the amp's output "
                        "reaches Sushi is not probed"
                    )
                for twin, name in [(own, f"{track.name} via amp"),
                                   (through, f"{track.name} via amp and the {MASTER}")]:
                    if twin is None:
                        continue
                    # The amp's output into the probe track, as the patchbay
                    # links it into the guitar's own: what the monitors hear
                    # of the amp is the point of the second of these.
                    links = tuple((AMP_OUTPUT, port) for port in twin.input_ports)
                    points += [
                        replace(point, inject=(AMP_INPUT,), links=links, nonlinear=True,
                                beside=GUITAR_ROLE)
                        for point in _through(twin, name, sushi_node)
                    ]
    if problems:
        raise ProbeError(problems)
    if any(point.track is not None for point in points):
        not_checked.append(
            f"the running tracks: each probe through Sushi plays a copy of the chain "
            f"on {PROBE_TRACK}, so the instrument is out of it and the player hears "
            "nothing, but a fault only a running instance has (a plugin blown up "
            "mid-set) and the tracks' own outputs are not probed"
        )
    not_checked += _shared(routed)
    return ProbePlan(tuple(points), tuple(not_checked))


def probe_links(point: ProbePoint, stimulus: str) -> tuple[tuple[str, str], ...]:
    """Every link `point` makes, as (source, sink), for `stimulus`'s port.

    What the effect links for the probe's duration and removes after, and
    nothing else: a probe never unlinks what the rig linked.
    """
    return tuple((stimulus, port) for port in point.inject) + point.links


def crowded_ports(
    point: ProbePoint, listing: str, stimulus: str, sushi_node: str = SUSHI_NODE
) -> list[str]:
    """Each Sushi port of `point` linked to something it should not be, from `pw-link -l`.

    Run on the live graph once the probe's links are made and before anything
    is played, and a reason to refuse the probe if not empty. Both ends of the
    probe track are checked, because each end carries one half of the promise:

    - **its inputs** must be fed by the probe alone. A second link on a Sushi
      input is the 64-frame truncation the module docstring describes, and
      whatever the other link carries is in the measurement. The probe's own
      sources are exactly `probe_links`'s: another port of the same client is
      a second link like any other, which the spike's own listing shows.
    - **its outputs** — both halves, whichever one this point listens to — must
      reach nothing but the selftest. Linked anywhere else, to the monitors or a
      recorder, the player or a take hears the probe. The selftest captures in
      the client that plays the stimulus (two channels of one stream,
      selftest.py), so a sink on that client is ours.

    Nothing the rig links touches a probe track's ports, so a stale patchbay, a
    leftover link from a killed run, or a probe track sitting where a channel
    was added since is what this catches. `NAM:Input` and `NAM:Output` are not
    Sushi's and are not checked: the guitar on the amp is declared, in `beside`.
    """
    ours = {source for source, _ in probe_links(point, stimulus)}
    client = stimulus.rsplit(":", 1)[0]
    sinks = [sink for _, sink in probe_links(point, stimulus)
             if sink.startswith(f"{sushi_node}:audio_input_")]
    outputs = point.track.output_ports if point.track else ()
    links = listed_links(listing)
    found = []
    for sink in dict.fromkeys(sinks):
        others = sorted(source for source, into in links if into == sink and source not in ours)
        if others:
            found.append(
                f"{point.name}: {sink} is fed by {', '.join(others)} as well as the probe. "
                "Sushi reads only the first 64 frames of each quantum of an input with "
                "two links, and the other source would be in the measurement; unlink it "
                "or run `sushi-rig patchbay check`"
            )
    for port in outputs:
        others = sorted(into for source, into in links
                        if source == port and into.rsplit(":", 1)[0] != client)
        if others:
            found.append(
                f"{point.name}: {port} is linked to {', '.join(others)} as well as the "
                "selftest, so the player or a recorder would hear the probe; unlink it "
                "or run `sushi-rig patchbay check`"
            )
    return found


def _through(twin: ProbeTrack, name: str, sushi_node: str) -> list[ProbePoint]:
    return [ProbePoint(f"{name} ({port})", twin.input_ports, port, track=twin)
            for port in twin.output_ports]


def _used(rig: Any) -> dict[str, set[int]]:
    """Every engine channel some track of the rig names, each way.

    The patchbay links physical ports only into the routes tracks name, and the
    amp only into the guitar's, so a channel outside these is one the machine
    file routes nothing to. Unreadable entries are left to the caller, which
    reports them.
    """
    used: dict[str, set[int]] = {"inputs": set(), "outputs": set()}
    for track in rig.tracks:
        for label in used:
            route, _ = track_route(track, label)
            used[label].update(c for dest in route for c in dest.channels())
    return used


def _free(dest: Dest, taken: set[int]) -> Dest | None:
    """The lowest free place of `dest`'s kind: a bus is an aligned pair."""
    if dest.kind == "bus":
        for n in range(MAX_FRONTEND_CHANNELS // 2):
            if not {2 * n, 2 * n + 1} & taken:
                return bus(n)
        return None
    for c in range(MAX_FRONTEND_CHANNELS):
        if c not in taken:
            return channel(c)
    return None


def _moved(track: Any, label: str, used: set[int]) -> tuple[tuple, tuple[Dest, ...]] | str:
    """`track`'s `label` entries with each engine number moved to a free one,
    and the places they now name.

    The same place named twice — a mono channel's one input into both halves —
    moves to the same new place, so the shape is kept. Returns why not, as
    text, when Sushi has no room.
    """
    pairs, _ = track_entries(track, label)
    taken = set(used)
    moves: dict[Dest, Dest] = {}
    for dest in dict.fromkeys(dest for _, dest in pairs):
        new = _free(dest, taken)
        if new is None:
            return (
                f"no Sushi {label[:-1]} {'pair ' if dest.kind == 'bus' else ''}is free "
                f"for {PROBE_TRACK} — the rig uses {len(used)} of Sushi's "
                f"{MAX_FRONTEND_CHANNELS} {label}"
            )
        moves[dest] = new
        taken.update(new.channels())
    entries = tuple(
        tuple(sorted({**entry, dest.key: moves[dest].index}.items())) for entry, dest in pairs
    )
    return entries, tuple(moves.values())


def _probe_track(
    shape_in: Any, shape_out: Any, chains: tuple[Any, ...], used: dict[str, set[int]],
    sushi_node: str,
) -> tuple[ProbeTrack | None, str]:
    """A probe track with `shape_in`'s inputs, `shape_out`'s outputs, both
    moved to free places, carrying the chains of `chains` in order; or None
    and why there is no room for one."""
    inputs = _moved(shape_in, "inputs", used["inputs"])
    outputs = _moved(shape_out, "outputs", used["outputs"])
    for why in (inputs, outputs):
        if isinstance(why, str):
            return None, why
    plugins = tuple(
        CopiedPlugin(source=p.name, plugin=replace(p, name=p.name + PROBE_SUFFIX))
        for track in chains for p in _copyable(track)
    )
    return ProbeTrack(
        name=PROBE_TRACK,
        copy_of=tuple(track.name for track in chains),
        channels=shape_in.channels,
        inputs=inputs[0],
        outputs=outputs[0],
        plugins=plugins,
        input_ports=_ports(sushi_node, "input", inputs[1]),
        output_ports=_ports(sushi_node, "output", outputs[1]),
        multibus=getattr(shape_in, "multibus", False),
        buses=getattr(shape_in, "buses", None),
    ), ""


def _name_problems(rig: Any) -> list[str]:
    """Names the probe track would take that the rig already has.

    Only names a copy could really be given: a send or return is never copied,
    so `<send>_probe` can never clash. The track's own name is the spec's check
    (`spec.probe_track_problem`), repeated here in its wording because a plan
    can be asked of a spec nothing has validated.
    """
    names = {track.name for track in rig.tracks} | {
        p.name for track in rig.tracks for p in track.plugins
    }
    wanted = {p.name + PROBE_SUFFIX for track in rig.tracks for p in _copyable(track)}
    problems = [probe_track_problem(t.name) for t in rig.tracks if probe_track_problem(t.name)]
    return problems + [
        f"{name!r} is in the rig, and is a name selftest gives a processor copied "
        "onto its probe track; rename it, since a probe that removed its own "
        "afterwards would remove the rig's"
        for name in sorted(names & wanted)
    ]


def _master(rig: Any) -> Any | None:
    """The compiled master, found by the compiler's own names.

    By name and not by any return: a hand-written rig can return a reverb aux
    while its mix leaves another track, and following every send there would
    probe the monitor path through the reverb and pass.
    """
    for track in rig.tracks:
        if track.name == MASTER and any(
            p.name == RETURN and getattr(p, "uid", None) == RETURN_UID for p in track.plugins
        ):
            return track
    return None


def _copyable(track: Any) -> list[Any]:
    """The plugins of `track` a probe track copies: all but sends and returns."""
    return [p for p in track.plugins if getattr(p, "uid", None) not in _NOT_COPIED]


def _copied(track: Any) -> bool:
    """Whether a copy of `track`'s chain would carry anything."""
    return bool(_copyable(track))


def _sends_to_master(track: Any) -> bool:
    return any(
        p.name == track.name + SEND_SUFFIX and getattr(p, "uid", None) == SEND_UID
        for p in track.plugins
    )


def _carries(track: Any, uid: str) -> bool:
    return any(getattr(p, "uid", None) == uid for p in track.plugins)


def _shared(routed: dict[str, tuple[tuple[str, ...], tuple[str, ...]]]) -> list[str]:
    """Pairs of running tracks whose separation cannot be measured, and why.

    Channel separation — a stimulus in one track and nothing from the other —
    can only be measured where the two have outputs of their own. Where they
    share one, config/src's guitar_vocal included, a probe would hear the
    stimulus through the track it was injected into and call it a leak. Shared
    inputs no longer matter here: each track is probed on its own probe track.
    """
    names = list(routed)
    said = []
    apart = []
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            outputs = [port for port in routed[a][1] if port in routed[b][1]]
            if not outputs:
                apart.append(f"{a}/{b}")
                continue
            said.append(
                f"separation of {a} and {b}: both come out of {', '.join(outputs)}, "
                "so whether either leaks into the other cannot be measured"
            )
    if apart:
        said.append(
            f"separation of {', '.join(apart)}: outputs of their own, so whether one "
            "leaks into the other can be measured, but no probe for it exists yet"
        )
    return said
