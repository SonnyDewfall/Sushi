"""Has the rig's sound changed?

The question #40 exists to answer, and the safety net the v2 refactor needs:
after moving the data model, the agent and the emit path, does every config
still produce the sound it produced before? Nothing in the project answers that
today, and `verify` cannot — it proves a config *loaded* as written, not that it
*sounds* as it did.

Sushi renders offline (`./sushi -o -i in.wav -O out.wav -c config.json`), so
this needs no audio hardware and runs in the CI container.

## A reference is a fingerprint, not a WAV

The obvious design is to keep an approved render per config and diff the audio.
This does not, for two reasons.

A five-second stereo sweep at float32 is about 1.9 MB, so the current seven
configs are 13 MB that grows with every tone saved -- in a repository that
deliberately purged 141 MB of binaries from its own history, and whose
`.gitignore` already excludes `*.wav`. And a binary diff answers "something
changed" without ever saying what, which is the least useful form of a failure.

So a reference is a small readable file: level, per-band energy, and the delay
through the chain. It diffs in git, a human can read it, and when it fails it
says *which band* moved and by how much. Coarse on purpose, like the bands it
rests on: the question is whether the tone is still the same shape.

## Tolerance is measured per config, never assumed

Two renders of the same config are not guaranteed identical. An LFO with a
random start phase, a plugin carrying internal state, denormal handling -- any
of these makes a chorus differ from itself while a clean EQ does not. A single
global tolerance would therefore either cry wolf on the chorus or go blind on
the EQ.

So the tolerance lives in the reference file, and `--calibrate` puts it there by
rendering the same config twice and measuring how far apart the two runs are.
That number is the floor: a tolerance below it reports differences that are not
differences. Guessing it instead would be the same mistake as a test fixture
tidier than reality -- a number that looks like a measurement and is not.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from ._numpy import require_numpy
from .analysis import (
    DEFAULT_BAND_EDGES_HZ,
    BandEnergy,
    Finding,
    band_differences_db,
    band_energies,
    check_finite,
    estimate_delay,
    peak_dbfs,
    rms_dbfs,
)

SCHEMA_VERSION = 1


@dataclass(frozen=True)
class Tolerance:
    """How far a render may drift before it counts as a change.

    `note` carries how the numbers were arrived at, because a tolerance whose
    provenance is lost is indistinguishable from one somebody invented.
    """

    band_db: float
    level_db: float
    delay_samples: int
    note: str = ""


# Deliberately tight, and deliberately not presented as measured: these are a
# starting point for `--calibrate` to replace with the floor it observes. If a
# reference still carries these, nobody has calibrated it yet.
PROVISIONAL = Tolerance(
    band_db=0.1,
    level_db=0.05,
    delay_samples=0,
    note="provisional, not measured -- run with --calibrate",
)


@dataclass(frozen=True)
class Fingerprint:
    """What a rendered config sounded like, in the few numbers worth comparing."""

    config: str
    stimulus: str
    sample_rate: int
    frames: int
    peak_dbfs: float
    rms_dbfs: float
    bands: tuple[BandEnergy, ...]
    delay_samples: int | None


def fingerprint(
    rendered,
    sample_rate: int,
    *,
    config: str,
    stimulus: str,
    stimulus_samples=None,
    edges_hz: Sequence[float] = DEFAULT_BAND_EDGES_HZ,
) -> Fingerprint:
    """Measure a render.

    `stimulus_samples`, when given, is the signal that was fed in, and the delay
    through the chain is measured against it. Latency is worth fingerprinting
    separately from tone: a chain that gained 20 ms still nulls perfectly once
    aligned, so a comparison that only looked at spectrum would miss it.
    """
    np = require_numpy()
    arr = np.asarray(rendered)

    delay = None
    if stimulus_samples is not None:
        delay = int(estimate_delay(stimulus_samples, arr).lag_samples)

    return Fingerprint(
        config=config,
        stimulus=stimulus,
        sample_rate=sample_rate,
        frames=int(arr.shape[0]),
        peak_dbfs=float(peak_dbfs(arr)),
        rms_dbfs=float(rms_dbfs(arr)),
        bands=tuple(band_energies(arr, sample_rate, edges_hz)),
        delay_samples=delay,
    )


def _comparable(reference: Fingerprint, test: Fingerprint) -> list[Finding]:
    """Reasons these two cannot meaningfully be compared at all.

    Reported rather than raised, and reported *first*: a mismatch here means the
    numbers below are about different things, and a tone difference computed
    across two different stimuli is noise dressed as a finding.
    """
    problems = []
    if reference.stimulus != test.stimulus:
        problems.append(Finding(
            "stimulus-differs",
            f"reference was rendered with {reference.stimulus!r}, "
            f"this run with {test.stimulus!r} — nothing below is comparable",
        ))
    if reference.sample_rate != test.sample_rate:
        problems.append(Finding(
            "rate-differs",
            f"reference at {reference.sample_rate} Hz, this run at "
            f"{test.sample_rate} Hz",
        ))
    if reference.frames != test.frames:
        problems.append(Finding(
            "length-differs",
            f"reference is {reference.frames} frames, this run "
            f"{test.frames} — a render missing its tail would otherwise pass",
        ))
    return problems


def compare(
    reference: Fingerprint, test: Fingerprint, tolerance: Tolerance = PROVISIONAL
) -> list[Finding]:
    """Every way `test` differs from `reference` by more than `tolerance`.

    Returns all of them rather than the first: at rig scale you want the whole
    list, the same reason `RigSpec.validate` and `verify` return lists.
    """
    blocking = _comparable(reference, test)
    if blocking:
        return blocking

    findings = list(blocking)

    for name, ref_v, test_v, limit in (
        ("peak", reference.peak_dbfs, test.peak_dbfs, tolerance.level_db),
        ("rms", reference.rms_dbfs, test.rms_dbfs, tolerance.level_db),
    ):
        delta = test_v - ref_v
        if abs(delta) > limit:
            findings.append(Finding(
                f"{name}-changed",
                f"{name} moved {delta:+.3f} dB (was {ref_v:.2f}, now {test_v:.2f}; "
                f"tolerance {limit} dB)",
            ))

    # Raises when the band layouts differ, which is a programming error here
    # rather than a render difference -- both sides are computed by this module
    # with the same edges.
    for band, delta in zip(reference.bands, band_differences_db(reference.bands, test.bands)):
        if abs(delta) > tolerance.band_db:
            # An infinite delta is a band silent on exactly one side. Worth its
            # own wording, because "+inf dB" reads like a bug in the tool.
            how = (
                "is silent in one render and not the other"
                if delta in (float("inf"), float("-inf"))
                else f"moved {delta:+.3f} dB (tolerance {tolerance.band_db} dB)"
            )
            findings.append(Finding(
                "band-changed",
                f"{band.low_hz:.0f}–{band.high_hz:.0f} Hz {how}",
            ))

    if reference.delay_samples is not None and test.delay_samples is not None:
        drift = test.delay_samples - reference.delay_samples
        if abs(drift) > tolerance.delay_samples:
            findings.append(Finding(
                "delay-changed",
                f"the chain's delay moved {drift:+d} samples "
                f"(was {reference.delay_samples}, now {test.delay_samples})",
            ))

    return findings


def render_findings(rendered, sample_rate: int) -> list[Finding]:
    """Whether the render is usable at all, before asking whether it matches.

    A render full of NaN compares equal to nothing and would otherwise surface
    as six confusing band differences rather than the one real problem.
    """
    bad = check_finite(rendered)
    return [bad] if bad else []


def floor_between(a: Fingerprint, b: Fingerprint) -> Tolerance:
    """The tolerance two renders of the same config demand of each other.

    This is what `--calibrate` records: the largest difference observed between
    two runs that *should* be identical. Anything tighter reports the rig's own
    non-determinism as a change in its sound.

    The returned numbers are the observed differences, unrounded and with no
    margin added. Deciding how much headroom to allow on top is a judgement for
    whoever commits the reference, and it should be a visible one rather than
    something this function quietly folded in.
    """
    bands = [abs(d) for d in band_differences_db(a.bands, b.bands) if d not in (
        float("inf"), float("-inf")
    )]
    delay = 0
    if a.delay_samples is not None and b.delay_samples is not None:
        delay = abs(b.delay_samples - a.delay_samples)
    return Tolerance(
        band_db=max(bands) if bands else 0.0,
        level_db=max(abs(b.peak_dbfs - a.peak_dbfs), abs(b.rms_dbfs - a.rms_dbfs)),
        delay_samples=delay,
        note="measured: the largest difference between two renders of this config",
    )


def to_dict(fp: Fingerprint, tolerance: Tolerance) -> dict[str, Any]:
    """The reference as it is written to disk: readable, diffable, ordered."""
    return {
        "schema_version": SCHEMA_VERSION,
        "config": fp.config,
        "stimulus": fp.stimulus,
        "sample_rate": fp.sample_rate,
        "frames": fp.frames,
        "measured": {
            "peak_dbfs": round(fp.peak_dbfs, 4),
            "rms_dbfs": round(fp.rms_dbfs, 4),
            "delay_samples": fp.delay_samples,
            "bands": [
                {
                    "low_hz": b.low_hz,
                    "high_hz": b.high_hz,
                    "level_dbfs": round(b.level_dbfs, 4),
                }
                for b in fp.bands
            ],
        },
        "tolerance": {
            "band_db": tolerance.band_db,
            "level_db": tolerance.level_db,
            "delay_samples": tolerance.delay_samples,
            "note": tolerance.note,
        },
    }


def from_dict(raw: dict[str, Any]) -> tuple[Fingerprint, Tolerance]:
    """Read a reference back. Refuses a schema it does not know.

    An unknown version is refused rather than best-guessed, because the thing
    this module protects is the claim "the sound has not changed", and a
    half-understood reference cannot support it.
    """
    version = raw.get("schema_version")
    if version not in (SCHEMA_VERSION,):
        raise ValueError(
            f"reference schema_version {version!r} is not supported "
            f"(this tool writes {SCHEMA_VERSION})"
        )
    m = raw["measured"]
    fp = Fingerprint(
        config=raw["config"],
        stimulus=raw["stimulus"],
        sample_rate=int(raw["sample_rate"]),
        frames=int(raw["frames"]),
        peak_dbfs=float(m["peak_dbfs"]),
        rms_dbfs=float(m["rms_dbfs"]),
        bands=tuple(
            BandEnergy(float(b["low_hz"]), float(b["high_hz"]), float(b["level_dbfs"]))
            for b in m["bands"]
        ),
        delay_samples=None if m.get("delay_samples") is None else int(m["delay_samples"]),
    )
    t = raw["tolerance"]
    return fp, Tolerance(
        band_db=float(t["band_db"]),
        level_db=float(t["level_db"]),
        delay_samples=int(t["delay_samples"]),
        note=str(t.get("note", "")),
    )
