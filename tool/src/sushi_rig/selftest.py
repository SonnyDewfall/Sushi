"""Does the rig make sound, or has it only started?

Issue #49. Every check this project had before now was a check that things
*started*: pids alive, a port open, a config that loads, gRPC answering. The
neural amp passed all of them for a whole session while processing nothing —
Carla had found no JACK server and exited at startup, and the rig reported
READY and played dry.

That failure turned out to be measurable, which is what this module rests on.
Measured on 2026-10-26, injecting a 220 Hz sine either side of the amp:

| | RMS | THD (h2–h5) |
|---|---|---|
| NAM in | −23.01 dBFS | 0.00 % |
| NAM out | −17.82 dBFS | 4.06 % |

A NAM with no model loaded passes audio and adds no harmonics. So "is the amp
modelling anything" is an assertion rather than a judgement, and so is "is
anything coming out of Sushi at all".

## What is here, and what is not

These are the decisions: given what a probe point captured, what does it mean.
Pure, and tested against signals whose answer is known by construction, plus one
functional test that renders a real passthrough through a real Sushi and asserts
it reads as linear — the same shape as the amp failure.

The effect — injecting into the live PipeWire graph and capturing back out of it
— is deliberately *not* here. It can only be verified on the rig machine, and
this project has shipped enough "I have not run this" to know how that ends.

## The order the checks run in

`level_findings` first, always. If the test's own injection never arrived, or
nothing came back at all, every other number is about nothing: a delay measured
on silence, a THD measured on a signal that is not there. Measure the rest only
once it comes back empty.

## Two channels of one stream, never two recordings

A `Probe` holds what went in and what came back as two channels captured
*together*. The delay between two separate recordings is dominated by when each
happened to start; measured differentially that offset cancels. The same session
also found that `pw-record --target <id>` silently ignores its target and falls
back to the default source, which produced a convincing false positive from a
webcam hearing the monitors — so the capture half, when it lands, links ports by
hand with `pw-link` and verifies with `pw-link -l`. Both in MANIFEST.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .analysis import (
    DEFAULT_HARMONICS,
    Finding,
    Harmonics,
    find_delays,
    harmonic_distortion,
    peak_dbfs,
    rms_dbfs,
)

# The sine the amp measurement above used. Low enough that harmonics 2–5 stay
# well inside the band, high enough to resolve in a capture of about a second,
# and an open A on a guitar.
DEFAULT_FUNDAMENTAL_HZ = 220.0

# Below this, a component that should be adding harmonics is not adding any.
# NAM with a model measured 4.06 % and the sine going into it measured 0.00 %,
# so 1 % sits a quarter of the way to the one working figure observed and far
# above what a clean sine measures. Not tuned finer than that on purpose: a
# threshold fitted to one amp's one model would be a guess wearing a number.
MIN_NONLINEAR_THD_PERCENT = 1.0

# Above this, `thd_percent` is a large number about a signal that is no longer
# the sine that was injected — see `analysis.Harmonics.noise_percent`. A missing
# fundamental puts this in the hundreds, which is what it exists to catch.
#
# **This is the one number here with nothing measured behind it.** It is loose
# so that a real amp — whose own 6th and higher harmonics land in this figure —
# cannot trip it, and it wants checking against the rig the first time the live
# half runs.
MAX_NOISE_PERCENT = 100.0

# How far past the quantum a port-to-port delay may read before the graph is
# not running at the buffer size the rig asked for. See `delay_findings`.
MAX_DELAY_BLOCKS = 2.0

# The injected signal's own peak must clear this, or the test is reporting on
# its own plumbing rather than on the rig. Peak and not RMS: an impulse's RMS
# depends on how much silence surrounds it, its peak does not.
INJECTED_FLOOR_DBFS = -60.0

# How much quieter than the injection the response may be and still count as
# "something came out". Wide, because a chain is allowed to have gain in it;
# what this catches is a component passing nothing.
MIN_GAIN_DB = -60.0


@dataclass(frozen=True)
class Probe:
    """What one probe point captured: the signal going in, and what came back.

    Both mono, both from one capture of two channels (see the module docstring),
    at one sample rate. `name` is what a finding calls the component — "sushi",
    "amp" — and is the only thing in here a human reads.
    """

    name: str
    injected: Any
    returned: Any
    sample_rate: int


# --------------------------------------------------------------------------- #
# Levels: did anything come out
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class Levels:
    """What arrived and what came back, in dBFS."""

    name: str
    injected_peak_dbfs: float
    injected_rms_dbfs: float
    returned_peak_dbfs: float
    returned_rms_dbfs: float

    @property
    def gain_db(self) -> float:
        """Peak in to peak out. `-inf` when nothing came back."""
        return self.returned_peak_dbfs - self.injected_peak_dbfs

    def __str__(self) -> str:
        return (
            f"{self.name}: in {self.injected_peak_dbfs:.1f} dBFS peak / "
            f"{self.injected_rms_dbfs:.1f} RMS, out {self.returned_peak_dbfs:.1f} / "
            f"{self.returned_rms_dbfs:.1f}, gain {self.gain_db:+.1f} dB"
        )


def measure_levels(probe: Probe) -> Levels:
    """Peak and RMS either side of a component. Works on any stimulus."""
    return Levels(
        name=probe.name,
        injected_peak_dbfs=peak_dbfs(probe.injected),
        injected_rms_dbfs=rms_dbfs(probe.injected),
        returned_peak_dbfs=peak_dbfs(probe.returned),
        returned_rms_dbfs=rms_dbfs(probe.returned),
    )


def level_findings(
    levels: Levels,
    *,
    injected_floor_dbfs: float = INJECTED_FLOOR_DBFS,
    min_gain_db: float = MIN_GAIN_DB,
) -> list[Finding]:
    """Whether this probe measured the rig at all, and whether the rig answered.

    Returns at most one finding, not a list of every way it went wrong: if the
    injection never arrived then "nothing came back" is a consequence of it and
    not a second fact, and reporting both would bury which one to fix.
    """
    if levels.injected_peak_dbfs <= injected_floor_dbfs:
        return [Finding(
            "nothing-injected",
            f"the test's own signal never reached {levels.name}: "
            f"{levels.injected_peak_dbfs:.1f} dBFS peak, at or below the "
            f"{injected_floor_dbfs:.1f} dBFS floor. This says nothing about the rig — "
            f"check that the injection is linked to {levels.name}",
        )]
    if levels.gain_db <= min_gain_db:
        return [Finding(
            "silent",
            f"{levels.name} is passing nothing: {levels.returned_peak_dbfs:.1f} dBFS "
            f"peak out against {levels.injected_peak_dbfs:.1f} in, "
            f"{levels.gain_db:+.1f} dB, at or below the {min_gain_db:.1f} dB floor",
        )]
    return []


# --------------------------------------------------------------------------- #
# Delay: how late, and is it one block
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class Delay:
    """How much later the signal came back. `samples` is None when it did not."""

    name: str
    sample_rate: int
    samples: int | None

    @property
    def ms(self) -> float | None:
        if self.samples is None:
            return None
        return 1000.0 * self.samples / self.sample_rate

    def __str__(self) -> str:
        if self.samples is None:
            return f"{self.name}: delay not measurable — nothing correlated"
        return f"{self.name}: delay {self.samples} samples ({self.ms:.2f} ms)"


def measure_delay(probe: Probe) -> Delay:
    """How late the response is, from the strongest alignment between the two.

    Needs a **broadband** stimulus — an impulse, noise or a sweep. A sine would
    produce a confident and meaningless answer, because every cycle of it aligns
    with every other; `analysis.find_delays` says so and this cannot check it.
    `samples is None` means nothing correlated at all, which is what silence
    looks like.
    """
    peaks = find_delays(probe.injected, probe.returned, max_peaks=1)
    return Delay(
        name=probe.name,
        sample_rate=probe.sample_rate,
        samples=peaks[0].lag_samples if peaks else None,
    )


def delay_findings(
    delay: Delay,
    *,
    expected_quantum: int | None = None,
    max_blocks: float = MAX_DELAY_BLOCKS,
) -> list[Finding]:
    """Whether a delay was measured, and whether it is the buffer size asked for.

    `up` states `PIPEWIRE_QUANTUM` on Sushi and the amp (`rig.QUANTUM`) because
    the value is sticky: a JACK client sets `node.lock-quantum`, so Sushi pins
    whatever the graph was at the moment it started and holds it for its whole
    life. On a fresh machine that was 2048 — 42.69 ms measured through Sushi,
    audible as the "high latency" it was first reported as, and announced by
    nothing at all.

    Port to port through Sushi is one quantum, plus the one sample the
    correlation resolves to: 257 samples at a quantum of 256, 2049 at 2048. So
    the test is whether the delay is still within `max_blocks` of the quantum
    that was asked for. Loose on purpose — those two figures are the only two
    ever observed here, and a tight tolerance would be a guess dressed as a
    measurement.

    `expected_quantum=None` skips that check, for a path with no quantum to
    compare against: an offline render has an internal block size of its own and
    is not running in a PipeWire graph at all.
    """
    if delay.samples is None:
        return [Finding(
            "no-correlation",
            f"nothing in {delay.name}'s response correlates with what went in, so "
            f"there is no delay to report — a broadband stimulus is needed, and a "
            f"silent response gives this too",
        )]
    if expected_quantum is None:
        return []
    if expected_quantum < 1:
        raise ValueError(f"expected_quantum must be a positive block size, got {expected_quantum}")
    blocks = delay.samples / expected_quantum
    if blocks <= max_blocks:
        return []
    return [Finding(
        "quantum-not-as-asked",
        f"{delay.name} is {delay.samples} samples late ({delay.ms:.2f} ms), "
        f"{blocks:.1f} blocks of the {expected_quantum} the rig asked for — the graph "
        f"is running at some other buffer size. A JACK client pins the quantum it "
        f"started at, so this is fixed by restarting the rig, not by changing the "
        f"graph underneath it",
    )]


# --------------------------------------------------------------------------- #
# Nonlinearity: is the amp modelling anything
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class Nonlinearity:
    """The harmonics either side of a component that is supposed to add them."""

    name: str
    injected: Harmonics
    returned: Harmonics

    def __str__(self) -> str:
        return (
            f"{self.name}: THD {self.returned.thd_percent:.2f} % out against "
            f"{self.injected.thd_percent:.2f} % in, at "
            f"{self.returned.fundamental_hz:g} Hz"
        )


def measure_nonlinearity(
    probe: Probe,
    *,
    fundamental_hz: float = DEFAULT_FUNDAMENTAL_HZ,
    harmonics: int = DEFAULT_HARMONICS,
) -> Nonlinearity:
    """Harmonic distortion either side of a component, from a sine probe.

    Needs a steady sine at `fundamental_hz` as the stimulus. Raises what
    `analysis.harmonic_distortion` raises, including on a silent capture — run
    `level_findings` first, which is what says "silent" in words a player can
    act on.
    """
    return Nonlinearity(
        name=probe.name,
        injected=harmonic_distortion(
            probe.injected, probe.sample_rate, fundamental_hz, harmonics=harmonics
        ),
        returned=harmonic_distortion(
            probe.returned, probe.sample_rate, fundamental_hz, harmonics=harmonics
        ),
    )


def nonlinearity_findings(
    nonlinearity: Nonlinearity,
    *,
    min_thd_percent: float = MIN_NONLINEAR_THD_PERCENT,
    max_noise_percent: float = MAX_NOISE_PERCENT,
) -> list[Finding]:
    """Whether the component added harmonics that were not going into it.

    Two things have to hold before the comparison means anything, and each is
    its own finding because each has a different fix:

    - the response still has to *be* the sine that went in, or its THD is a
      large number about something else;
    - the sine arriving has to be clean, or distortion cannot be attributed to
      this component rather than to whatever is upstream of it.
    """
    if nonlinearity.returned.noise_percent > max_noise_percent:
        return [Finding(
            "not-a-sine",
            f"what came back from {nonlinearity.name} is not the "
            f"{nonlinearity.returned.fundamental_hz:g} Hz sine that went in — "
            f"{nonlinearity.returned.noise_percent:.0f} % of the fundamental sits "
            f"outside it and its harmonics, above the {max_noise_percent:.0f} % limit. "
            f"Its {nonlinearity.returned.thd_percent:.2f} % THD is not a distortion "
            f"figure; check the probe is measuring the component it names",
        )]
    if nonlinearity.injected.thd_percent > min_thd_percent:
        return [Finding(
            "stimulus-distorted",
            f"the sine arriving at {nonlinearity.name} already carries "
            f"{nonlinearity.injected.thd_percent:.2f} % THD, over the "
            f"{min_thd_percent:.2f} % the comparison treats as clean — distortion at "
            f"the output cannot be attributed to {nonlinearity.name}",
        )]
    if nonlinearity.returned.thd_percent < min_thd_percent:
        return [Finding(
            "linear",
            f"{nonlinearity.name} is passing audio but adding no harmonics: THD "
            f"{nonlinearity.returned.thd_percent:.2f} %, under the "
            f"{min_thd_percent:.2f} % that counts as modelling anything. A neural amp "
            f"with no model loaded measures about 0 % and looks perfectly healthy "
            f"otherwise, which is the failure this check exists for",
        )]
    return []
