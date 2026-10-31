"""Where selftest injects and listens: the ports each probe links.

Issue #49, part 2's decisions. `selftest.py` says what a capture means; this
says what to capture. Given the rig and whether the amp is in it, it lists every
probe point as the ports a test signal is linked into and the one port it is
read back from — nothing here touches PipeWire. The effect that links them, plays
the stimulus and records both channels together needs the rig machine.

## Every half of every output

A probe reads one output port. A track feeding a stereo bus gets one probe per
half, so a chain that comes out of one side only — a mono plugin on a stereo
track, an output mapped to the wrong channel — fails on the side that is
silent. A probe that summed the two or picked one would pass it.

What this does not catch is the guitar's own port feeding only one of Sushi's
inputs, the left-speaker-only failure this rig once had: the probe drives both
inputs itself. That is the physical side of the routing, `mono_to_stereo` in
the machine file and `patchbay check` against the live graph.

## Injected where the track's own input lands

The stimulus goes into every Sushi input the track's input route names, so a
mono guitar declared `mono_to_stereo` is probed through both halves exactly as
it is played. It is injected into Sushi's ports, not the interface's: nothing
can be played *into* a capture port, and the physical side of the routing is
`patchbay check`'s to verify.

Every input of a track is driven at once, so a multibus track whose bus 1 input
never reaches its bus 1 output still passes on signal mixed in from bus 0. One
stimulus per input bus would see it; no rig here has a multibus track yet.

## The amp, twice

With the amp in the rig, two probes cover it:

- **amp** — `NAM:Input` to `NAM:Output`, asserted nonlinear. This is the
  failure that started #49: an amp with no model passes audio and adds nothing.
- **<track> via amp** — `NAM:Input` to the guitar track's outputs, asserted
  nonlinear too. It catches the amp working and its output never reaching
  Sushi, which is the wiring `amp.amp_patchbay` writes and nothing else checks.

A probe straight into Sushi asserts nothing about harmonics either way: a chain
may hold a distortion pedal, and the probe is about whether sound comes through.

## Through the master

A rig compiled from channels and a master (rigfile.py) is heard through a
master track that has no inputs: it is fed by each channel's send. So each
channel is probed twice, through its own output (its stem) and through the
master's (what the monitors hear), and with the amp on, from the amp to the
master as well. The send is followed by its plugin, not by its destination:
a compiled rig has exactly one return, and with any other number the path to
the monitors is listed as not probed rather than guessed.

## Separation is not probed yet

Whether one channel leaks into another needs the two to come out of different
ports, and in today's guitar_vocal they share bus 0. The plan says so for every
pair that shares an output; probing separation waits for per-channel outputs.
Two tracks sharing their inputs *and* outputs are worse: a probe of either
drives both, so a silent chain on one is heard through the other, and the plan
says that too.

## Links are added, never taken away

A probe links its stimulus alongside whatever already feeds an input; it does
not unlink the guitar or the amp first. PipeWire sums an input's links, so a
quiet instrument adds a little noise, which the level and delay checks tolerate.
Unlinking would mean restoring afterwards, and a selftest killed between the two
leaves a rig with no guitar in it — worse than any measurement is worth.

## Not yet seen on the rig

- That PipeWire sums a probe's link into an input that already has one, rather
  than refusing or replacing it.
- That qpwgraph leaves a probe's links alone. If its patchbay is in exclusive
  mode it removes links not in the saved file, which would cut a probe off
  mid-capture.
- That `NAM:Input` and `NAM:Output` are the names Carla gives the ports live.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .amp import AMP_INPUT, AMP_OUTPUT
from .patchbay import GUITAR_ROLE, SUSHI_NODE, track_route
from .rigfile import MASTER, RETURN, RETURN_UID, SEND_SUFFIX, SEND_UID


@dataclass(frozen=True)
class ProbePoint:
    """One probe: link a stimulus into `inject`, read back from `capture`.

    `nonlinear` is True where the path must add harmonics (the amp), and None
    where it is not asserted either way. `name` is what a finding calls it.
    """

    name: str
    inject: tuple[str, ...]
    capture: str
    nonlinear: bool | None = None


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
    problems: list[str] = []
    # (name, its own outputs or None if broken, whether it sends to the master)
    guitar: list[tuple[str, tuple[str, ...] | None, bool]] = []
    routed: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {}
    master = _master(rig, sushi_node)
    senders: list[tuple[str, tuple[str, ...]]] = []
    for track in rig.tracks:
        into, found = track_route(track, "inputs", track.source)
        out, more = track_route(track, "outputs", track.sink)
        inject = _ports(sushi_node, "input", into)
        captures = _ports(sushi_node, "output", out)
        is_master = master is not None and track.name == master[0]
        sends = master is not None and _sends_to_master(track)
        if track.source == GUITAR_ROLE:
            # Recorded before anything can skip the track, so an amp in front
            # of a broken guitar track is reported as that, not as no guitar.
            guitar.append((track.name, None if found or more else captures, sends))
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
            senders.append((track.name, inject))
        if not inject or not captures:
            # A master is fed by sends rather than inputs, and a channel with no
            # output of its own is heard through the master: neither is unprobed.
            if not ((is_master and not inject) or (sends and inject and not captures)):
                missing = "inputs" if not inject else "outputs"
                not_checked.append(f"{track.name}: no {missing} on Sushi, so nothing to probe")
            continue
        routed[track.name] = (inject, captures)
        for port in captures:
            points.append(ProbePoint(f"{track.name} through {sushi_node} ({port})", inject, port))

    if master is not None and senders:
        name, outputs = master
        if not outputs:
            not_checked.append(
                f"{', '.join(s for s, _ in senders)}: sent to the {name}, which has no "
                "outputs on Sushi, so nothing reaches the monitors to probe"
            )
        for sender, inject in senders:
            points += [
                ProbePoint(f"{sender} through the {name} ({port})", inject, port)
                for port in outputs
            ]

    if amp:
        if not guitar:
            problems.append(
                f"the amp is on but no track takes {GUITAR_ROLE!r}, so it is in front "
                "of nothing; the amp follows that role"
            )
        elif len(guitar) > 1:
            problems.append(
                f"tracks {', '.join(repr(name) for name, _, _ in guitar)} all take "
                f"{GUITAR_ROLE!r}, so which one the amp sits in front of is not decided"
            )
        else:
            (name, captures, sends), = guitar
            heard = list(captures or ())
            monitors = list(master[1]) if sends and master is not None else []
            if captures is not None and not heard and not monitors:
                problems.append(
                    f"the amp is in front of {name!r}, which has no outputs on Sushi "
                    "and no master to send to, so what the amp does can never be heard"
                )
            elif captures is not None:
                points.append(ProbePoint("amp", (AMP_INPUT,), AMP_OUTPUT, nonlinear=True))
                points += [
                    ProbePoint(f"{name} via amp ({port})", (AMP_INPUT,), port, nonlinear=True)
                    for port in heard
                ]
                # What the monitors hear, which is the point of the amp.
                points += [
                    ProbePoint(f"{name} via amp and the {master[0]} ({port})",
                               (AMP_INPUT,), port, nonlinear=True)
                    for port in monitors
                ]
    if problems:
        raise ProbeError(problems)
    not_checked += _shared(routed)
    return ProbePlan(tuple(points), tuple(not_checked))


def _master(rig: Any, sushi_node: str) -> tuple[str, tuple[str, ...]] | None:
    """The compiled master and its outputs, found by the compiler's own names.

    By name and not by any return: a hand-written rig can return a reverb aux
    while its mix leaves another track, and following every send there would
    probe the monitor path through the reverb and pass.
    """
    for track in rig.tracks:
        if track.name == MASTER and any(
            p.name == RETURN and getattr(p, "uid", None) == RETURN_UID for p in track.plugins
        ):
            out, _ = track_route(track, "outputs")
            return track.name, _ports(sushi_node, "output", out)
    return None


def _sends_to_master(track: Any) -> bool:
    return any(
        p.name == track.name + SEND_SUFFIX and getattr(p, "uid", None) == SEND_UID
        for p in track.plugins
    )


def _carries(track: Any, uid: str) -> bool:
    return any(getattr(p, "uid", None) == uid for p in track.plugins)


def _shared(routed: dict[str, tuple[tuple[str, ...], tuple[str, ...]]]) -> list[str]:
    """Pairs of tracks a probe cannot tell apart, and why.

    Channel separation — a stimulus in one track and nothing from the other —
    can only be measured where the two have outputs of their own. Where they
    share one, today's guitar_vocal included, the probe would hear the
    stimulus through the track it was injected into and call it a leak. Where
    they share inputs as well, a probe of either drives both.
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
            inputs = [port for port in routed[a][0] if port in routed[b][0]]
            if inputs:
                said.append(
                    f"{a} and {b}: both read {', '.join(inputs)} and come out of "
                    f"{', '.join(outputs)}, so a probe of either drives both, and a "
                    "silent chain on one is heard through the other"
                )
            else:
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
