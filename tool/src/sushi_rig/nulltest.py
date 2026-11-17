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

import hashlib
import json
import math
from dataclasses import dataclass
from typing import Any, Sequence

from ._numpy import require_numpy
from .analysis import (
    DEFAULT_BAND_EDGES_HZ,
    channel_rms_dbfs,
    BandEnergy,
    Finding,
    band_differences_db,
    band_energies,
    check_finite,
    estimate_delay,
    peak_dbfs,
    rms_dbfs,
)

# 2 added each channel's RMS (`channel_rms_dbfs`), 2026-11-02. A schema-1
# reference is still read, without them: its channels are not compared.
SCHEMA_VERSION = 2

# How many decimals a reference keeps. Four is a readability choice (see
# `to_dict`), but it is also a measurement limit: a level written at 4 dp is
# only known to within half of 1e-4 dB, so a tolerance tighter than that asks
# the file for precision it does not hold. `floor_between` will not go below it.
WRITTEN_DECIMALS = 4
WRITTEN_RESOLUTION_DB = 10.0 ** -WRITTEN_DECIMALS


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
    # Each channel's RMS, left first. The whole-render `rms_dbfs` is every
    # channel together, so a render with its right channel silent was only
    # 3 dB down, and that is how a rig playing out of the left monitor alone
    # passed this check for its whole life (2026-11-02). None for a schema-1
    # reference, which did not record them.
    channel_rms_dbfs: tuple[float, ...] | None = None


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
    left = arr[:, 0] if arr.ndim > 1 else arr
    # A silent left side has no delay to measure, and `estimate_delay` would
    # raise "no correlation" over it: a message about the measurement, where
    # the finding is the silence, which `heard_findings` names (#144).
    if stimulus_samples is not None and _audible(float(rms_dbfs(left))):
        # Both reduced to their first channel, because `estimate_delay` refuses
        # multichannel input rather than silently reducing it -- and every real
        # render is at least stereo, so without this the delay could never be
        # measured at all. Found by rendering through a passthrough and
        # fingerprinting the result, which neither half's own tests would have
        # caught: each was correct alone.
        #
        # Channel 0 rather than a mixdown, deliberately. A mixdown of two
        # channels that are out of phase partially cancels, and a cancelled
        # reference makes the correlation peak meaningless -- which would read
        # as a latency change rather than as a measurement that could not be
        # made. This comment used to add that both halves of a centred channel
        # are identical on this rig anyway. They were not: every rig starting
        # with a mono plugin had a silent right channel (2026-11-02, stereo.py),
        # and nothing here could see it. The delay is still channel 0's; each
        # channel's level is now its own (`channel_rms_dbfs`), so a channel
        # lost or changed is a finding of its own.
        delay = int(
            estimate_delay(
                np.asarray(stimulus_samples)[:, 0] if np.asarray(stimulus_samples).ndim > 1
                else stimulus_samples,
                arr[:, 0] if arr.ndim > 1 else arr,
            ).lag_samples
        )

    return Fingerprint(
        config=config,
        stimulus=stimulus,
        sample_rate=sample_rate,
        frames=int(arr.shape[0]),
        peak_dbfs=float(peak_dbfs(arr)),
        rms_dbfs=float(rms_dbfs(arr)),
        bands=tuple(band_energies(arr, sample_rate, edges_hz)),
        delay_samples=delay,
        channel_rms_dbfs=tuple(channel_rms_dbfs(arr)),
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

    if reference.channel_rms_dbfs is not None and test.channel_rms_dbfs is not None:
        if len(reference.channel_rms_dbfs) != len(test.channel_rms_dbfs):
            findings.append(Finding(
                "channels-differ",
                f"reference has {len(reference.channel_rms_dbfs)} channels, this run "
                f"{len(test.channel_rms_dbfs)}",
            ))
        else:
            for index, (ref_v, test_v) in enumerate(zip(reference.channel_rms_dbfs, test.channel_rms_dbfs)):
                which = CHANNEL_NAMES.get(index, f"channel {index}")
                if not _audible(ref_v) and not _audible(test_v):
                    continue  # silent in both (`SILENCE_FLOOR_DBFS`), -inf included
                if not _audible(ref_v) or not _audible(test_v):
                    findings.append(Finding(
                        "channel-changed", f"the {which} channel is silent in one render and not the other",
                    ))
                elif abs(test_v - ref_v) > tolerance.level_db:
                    findings.append(Finding(
                        "channel-changed",
                        f"the {which} channel's rms moved {test_v - ref_v:+.3f} dB (was {ref_v:.2f}, "
                        f"now {test_v:.2f}; tolerance {tolerance.level_db} dB)",
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


CHANNEL_NAMES = {0: "left", 1: "right"}

# A channel this quiet is silent, for comparing: the v1 configs' right channel
# measures about -279 dBFS (CI, 2026-11-02), float residue rather than sound,
# and a residue that far down can move by decibels on a change too small to
# matter anywhere else, which a reference written to 0.0001 dB would report.
# Far below anything audible or any plugin's own noise, far above that residue.
SILENCE_FLOOR_DBFS = -200.0


def _audible(level: float) -> bool:
    return level > SILENCE_FLOOR_DBFS


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

    The returned numbers are the observed differences with no margin added:
    deciding how much headroom to allow on top is a judgement for whoever
    commits the reference, and it should be a visible one rather than something
    this function quietly folded in.

    The one floor applied is the file's own resolution. A deterministic config
    differs from itself by exactly nothing, which would be recorded as
    `band_db: 0.0` -- and then every later check would fail, because the
    reference stores levels rounded to `WRITTEN_DECIMALS` and compares them
    against a fresh measurement that is not rounded. Those differences are the
    file format, not the rig. Found by round-tripping a calibration through YAML,
    which none of the in-memory tests did.
    """
    bands = [abs(d) for d in band_differences_db(a.bands, b.bands) if d not in (
        float("inf"), float("-inf")
    )]
    delay = 0
    if a.delay_samples is not None and b.delay_samples is not None:
        delay = abs(b.delay_samples - a.delay_samples)
    observed_band = max(bands) if bands else 0.0
    observed_level = max(abs(b.peak_dbfs - a.peak_dbfs), abs(b.rms_dbfs - a.rms_dbfs))
    if a.channel_rms_dbfs is not None and b.channel_rms_dbfs is not None:
        observed_level = max([observed_level] + [
            abs(y - x) for x, y in zip(a.channel_rms_dbfs, b.channel_rms_dbfs)
            if _audible(x) and _audible(y)
        ])
    note = "measured: the largest difference between two renders of this config"
    if max(observed_band, observed_level) < WRITTEN_RESOLUTION_DB:
        note = (
            "measured: two renders of this config were identical to the "
            f"{WRITTEN_RESOLUTION_DB:g} dB this file is written at"
        )
    return Tolerance(
        band_db=max(observed_band, WRITTEN_RESOLUTION_DB),
        level_db=max(observed_level, WRITTEN_RESOLUTION_DB),
        delay_samples=delay,
        note=note,
    )


def keep_wider(measured: Tolerance, committed: Tolerance | None) -> Tolerance:
    """The tolerance a recalibration writes: never tighter than the committed one.

    A calibration's floor is one pair of renders. For a config that is not
    deterministic, that understates its spread — `electric-octave-up` measured
    steady three times and then moved its peak by five times the tolerance
    those three implied — so its committed tolerance is set wider by hand.
    Recalibrating after a deliberate change in tone used to overwrite that with
    the new floor, and the config went back to failing at random. Per field,
    the wider of the two wins; the committed note is kept whenever any field of
    it is, because that is the judgement still in force. Tightening one is a
    deliberate act: delete the reference and calibrate afresh.
    """
    if committed is None:
        return measured
    widest = Tolerance(
        band_db=max(measured.band_db, committed.band_db),
        level_db=max(measured.level_db, committed.level_db),
        delay_samples=max(measured.delay_samples, committed.delay_samples),
        note=measured.note,
    )
    kept = (
        widest.band_db > measured.band_db
        or widest.level_db > measured.level_db
        or widest.delay_samples > measured.delay_samples
    )
    return Tolerance(widest.band_db, widest.level_db, widest.delay_samples,
                     committed.note if kept else measured.note)


def to_dict(fp: Fingerprint, tolerance: Tolerance, made: "MadeFrom | None" = None) -> dict[str, Any]:
    """The reference as it is written to disk: readable, diffable, ordered.

    `made` is the committed rig the reference was rendered from (`MadeFrom`),
    written last, so a reference without one reads as it always has."""
    return {
        "schema_version": SCHEMA_VERSION,
        "config": fp.config,
        "stimulus": fp.stimulus,
        "sample_rate": fp.sample_rate,
        "frames": fp.frames,
        "measured": {
            "peak_dbfs": round(fp.peak_dbfs, WRITTEN_DECIMALS),
            "rms_dbfs": round(fp.rms_dbfs, WRITTEN_DECIMALS),
            **({"channel_rms_dbfs": [round(v, WRITTEN_DECIMALS) for v in fp.channel_rms_dbfs]}
               if fp.channel_rms_dbfs is not None else {}),
            "delay_samples": fp.delay_samples,
            "bands": [
                {
                    "low_hz": b.low_hz,
                    "high_hz": b.high_hz,
                    "level_dbfs": round(b.level_dbfs, WRITTEN_DECIMALS),
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
        **({"made_from": {"rig": made.rig, "hash": made.hash, "hash_rule": made.rule}}
           if made is not None else {}),
    }


def from_dict(raw: dict[str, Any]) -> tuple[Fingerprint, Tolerance]:
    """Read a reference back. Refuses a schema it does not know.

    An unknown version is refused rather than best-guessed, because the thing
    this module protects is the claim "the sound has not changed", and a
    half-understood reference cannot support it.
    """
    version = raw.get("schema_version")
    if version not in (1, SCHEMA_VERSION):
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
        channel_rms_dbfs=(tuple(float(v) for v in m["channel_rms_dbfs"])
                          if version >= 2 and m.get("channel_rms_dbfs") is not None else None),
    )
    t = raw["tolerance"]
    return fp, Tolerance(
        band_db=float(t["band_db"]),
        level_db=float(t["level_db"]),
        delay_samples=int(t["delay_samples"]),
        note=str(t.get("note", "")),
    )


# --- which rig a reference was made from (#144) ------------------------------
#
# A rig in config/rigs/ is the owner's: saved from the web UI and pushed. Its
# reference pins its sound, and used to fail CI the moment a setting changed,
# because the rig no longer sounded like the reference of the rig it had been.
# So a committed rig's reference records the rig it was rendered from, and the
# null test compares exactly only while the rig is still that rig. A rig that
# has changed, or has no reference yet, is held to `heard_findings` alone and
# reported as stale, with a candidate reference for it.
#
# The record is a hash of the rig file as parsed, not of the config it compiles
# to: a change to the compiler must still be compared against every reference,
# not make every one look stale at once. It is of the part of the file the
# rendered graph is made from (`RENDERED`), so that a change the render cannot
# hear (the amp's levels saved from the page, the MIDI map, the description)
# does not make a reference stale and open a window in which a change of sound
# by the code would pass as the owner's. And the numbers in it are normalised,
# so that a save writing `1` where the file said `1.0` is not a change (the
# web UI's Save did exactly that once, 2c36874; the compiled config is the same
# either way, since `emit` writes every parameter as a float).

# Bumped when what `rig_hash` reads changes: every reference made under another
# rule is then stale, which is said, and is what a change of rule must mean.
# 1 hashed the whole file; 2 the part of it the render is made from (review of
# PR #158).
RIG_HASH_RULE = 2

# What of a rig file `render.rig_channels` renders, read off the compiler
# (`rigfile.compile_rig`, `emit_compiled`, `heard`) rather than assumed, by key
# of the file, the channel and the master. Every key the format has is in one
# of the two (test_sound_refs.py fails on a key added to `rigfile` until it is
# placed), and changing any key outside RENDERED leaves the rendered configs,
# Sushi's part of them, as they were (tried on every frozen rig, the same test).
#
# - `schema_version` says what the rest means. `host` is the config's
#   `host_config`. `channels` and `master` are the tracks, their chains, levels
#   and strips, in order: a channel's place decides its engine channels.
# - Not rendered: `kind` (always "rig"); `name`, which names the references and
#   the config's `_meta`, and no sound (a renamed rig has references of other
#   names); `description` and `meta` (`_meta`); `amp` (`_amp`: the amp runs in
#   Carla, outside Sushi and outside any render); `midi`, which a render
#   compiles without (no controller); a channel's `input` and the master's
#   `output`, which are machine roles, written to `_wiring` for the patchbay
#   and never to Sushi. Sushi ignores an underscored key.
RENDERED = {
    "rig": ("schema_version", "host", "channels", "master"),
    "channel": ("name", "width", "strip", "chain", "level"),
    "master": ("chain", "level"),
}
NOT_RENDERED = {
    "rig": ("kind", "name", "description", "meta", "amp", "midi"),
    "channel": ("input",),
    "master": ("output",),
}

# What starts each line of `nulltest`'s summary that reports a warning, and
# only those lines: the container job counts them and makes its annotation of
# them (.github/workflows/rig-container.yml), as it does `render.RETRIED_PREFIX`.
STALE_PREFIX = "STALE-REFERENCE:"
ORPHAN_PREFIX = "ORPHANED-REFERENCE:"


@dataclass(frozen=True)
class MadeFrom:
    """The committed rig a reference was rendered from: the file's name, and
    `rig_hash` of it under `rule`."""

    rig: str
    hash: str
    rule: int = RIG_HASH_RULE


def _canonical(value: Any) -> Any:
    """`value` with every number that is a whole number written as an int, a
    mapping's keys as strings, and nothing else changed. A bool stays a bool
    (it is an int to Python, and `true` is not `1` in a rig). A float that is
    not whole stays as it is, which json writes as its shortest round-trip
    form: `0.50` and `.5` parse to the same float, so they hash the same."""
    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if math.isfinite(value) and value == math.floor(value):
            return int(value)  # 1.0 is 1, -0.0 is 0
        return value
    if isinstance(value, dict):
        return {str(key): _canonical(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_canonical(item) for item in value]
    # Anything else YAML can produce (a date, say) is no part of a rig's
    # format, but hashing it by its text keeps it a change when it changes.
    return f"{type(value).__name__}:{value}"


def _kept(entry: Any, keys: tuple[str, ...]) -> Any:
    """Of a mapping, the `keys` it has; anything else as it is, so a file
    the compiler would refuse still hashes, and differently."""
    if not isinstance(entry, dict):
        return entry
    return {key: entry[key] for key in keys if key in entry}


def rendered_part(raw: Any) -> Any:
    """The part of a parsed rig file the render is made from (`RENDERED`)."""
    if not isinstance(raw, dict):
        return raw
    part = _kept(raw, RENDERED["rig"])
    if isinstance(part.get("channels"), list):
        part["channels"] = [_kept(channel, RENDERED["channel"]) for channel in part["channels"]]
    if "master" in part:
        part["master"] = _kept(part["master"], RENDERED["master"])
    return part


def rig_hash(raw: Any) -> str:
    """sha256, in hex, of the part of a rig file the render is made from
    (`rendered_part`), as parsed (`yaml.safe_load`, as `rigfile.load_rig`
    reads it): its mappings in key order, every whole number as an int,
    compact json, UTF-8. So key order, comments, quoting, layout, `1.0`
    against `1`, and any key outside `RENDERED` do not change it; any value,
    key or list order inside it does. The rule's number is part of what is
    hashed, so two rules never share a hash."""
    text = json.dumps(
        {"rig_hash_rule": RIG_HASH_RULE, "rendered": _canonical(rendered_part(raw))},
        sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=True,
    )
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def made_from_of(raw: Any) -> MadeFrom | None:
    """The `made_from` of a reference as read from its file, or None when it
    has none (a v1 config's, a frozen rig's, or one made before #144). A
    `made_from` that is not one is a ValueError: a hand-edited reference must
    not pass as made from any rig at all, or from the one it names."""
    if not isinstance(raw, dict) or raw.get("made_from") is None:
        return None
    made = raw["made_from"]
    if not isinstance(made, dict) or not isinstance(made.get("hash"), str) \
            or not isinstance(made.get("rig"), str) or type(made.get("hash_rule")) is not int:
        raise ValueError(f"made_from is {made!r}: expected rig, hash and hash_rule")
    return MadeFrom(made["rig"], made["hash"], made["hash_rule"])


def staleness(made: MadeFrom | None, current: str) -> str | None:
    """Why a committed rig's reference no longer says how the rig sounds, or
    None when it was made from the rig as it is (`current`, its `rig_hash`).
    A reference with no record of its rig is stale: nothing says it is of
    this one."""
    if made is None:
        return "its reference does not say which version of the rig it was made from"
    if made.rule != RIG_HASH_RULE:
        return (f"its reference was made under rig hash rule {made.rule}, and this tool's is "
                f"{RIG_HASH_RULE}")
    if made.hash != current:
        return "the rig has changed since its reference was made"
    return None


# How far apart a rig's two sides may be for the null test's stimulus, which
# feeds both sides the same sweep. A centred chain is exactly balanced; a
# stereo reverb or chorus is not, by a little: every rig reference committed on
# 2026-11-16 has its sides within 0.11 dB, and test_stereo.py has held every
# committed rig to this since 2026-11-02. A silent side fails by any margin.
BALANCE_DB = 1.0


def heard_findings(fp: Fingerprint) -> list[Finding]:
    """What a rig must sound like with no reference to compare against: two
    sides, neither silent (`SILENCE_FLOOR_DBFS`), within `BALANCE_DB` of
    each other. That the render is finite is `render.measure`'s, which
    refuses one that is not before it is fingerprinted.

    For the rigs of channels only. A v1 config plays its left side alone,
    on purpose (`render.V1_REFERENCE_SUFFIX`), and has no such rule."""
    levels = fp.channel_rms_dbfs
    if levels is None or len(levels) != 2:
        return [Finding("not-two-sides", f"the render has {len(levels) if levels else 'no'} channel level(s), "
                                         "where a rig's master has a left and a right")]
    left, right = levels
    if not _audible(left) and not _audible(right):
        return [Finding("silent", f"both sides are silent (L {left:.1f}, R {right:.1f} dBFS)")]
    for which, level in (("left", left), ("right", right)):
        if not _audible(level):
            return [Finding("one-sided", f"the {which} side is silent (L {left:.1f}, R {right:.1f} dBFS)")]
    if abs(left - right) > BALANCE_DB:
        return [Finding("unbalanced", f"L {left:.2f} and R {right:.2f} dBFS, more than {BALANCE_DB} dB apart")]
    return []


def v1_findings(fp: Fingerprint) -> list[Finding]:
    """What a v1 config's render must be, whatever its reference says: its
    left side, the one it plays (`render.V1_REFERENCE_SUFFIX`), not silent.
    Its right side may be: every v1 config whose chain starts with a mono
    plugin leaves it so. Without this a silent render calibrated into a
    reference compared equal to every silent render after it (review of PR
    #158), since `compare` passes a side silent in both."""
    levels = fp.channel_rms_dbfs
    if not levels:
        return [Finding("not-measured", "the render has no channel levels to check")]
    if not _audible(levels[0]):
        return [Finding("silent", f"the left side, the one a v1 config plays, is silent ({levels[0]:.1f} dBFS)")]
    return []


def played_findings(fp: Fingerprint, is_rig: bool) -> list[Finding]:
    """`heard_findings` for a rig of channels, `v1_findings` for a v1 config."""
    return heard_findings(fp) if is_rig else v1_findings(fp)
