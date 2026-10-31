"""A take is a folder: its stems, the config that made them, and how they line up.

Issue #55. The premise of this project is that a tone is a readable, versioned
artefact rather than state trapped in a session file, and a recording made by
that tone should not throw the connection away. So a take is not loose WAVs. It
carries the config that was live, which makes it a take you can re-amp through
the exact rig that made it, months later, because the rig is in the folder.

## Layout

    <takes>/<name>/
        take.yaml         what this is, and how its stems line up
        config.json       the config that was live, amp included
        guitar.wav        what you heard
        guitar-dry.wav    what went in
        vocal.wav
        vocal-dry.wav
    <takes>/_archive/<name>/<recorded at>/    an earlier take of that name

Naming and archiving follow `save`, as the issue asks, rather than inventing a
second convention: a take's name passes `save.NAME_PATTERN` — the security
boundary for names arriving over unauthenticated OSC, which is exactly how the
record button will send them — and recording over a name archives the old take
instead of replacing it. Nothing recorded is ever lost; that matters more here
than for configs, because a config can be re-captured and a performance cannot.

The archive is `_archive`, not `archive`. `NAME_PATTERN` allows a take called
"archive", which would have been the archive directory; it cannot start with an
underscore, so `_archive` can never collide with a take.

A stem is named after its channel, which is the track's name in the config, and
its tap: the processed stem gets the plain name because it is what you heard,
and the dry tap carries the suffix.

## The offset is carried, not compensated

The dry tap leads the processed stem by the rig's buffering — 257 samples, 5.35
ms, at a quantum of 256. The issue leaves open whether to compensate on write or
carry the number, and says both are defensible. Carried, for three reasons:

1. **Compensating needs the number before the first sample is written.** The
   shift is applied by writing silence ahead of a stem, and a WAV cannot have
   audio inserted at its start afterwards without rewriting the whole file.
   Before a take the only number available is an assumption (one quantum) or
   an earlier measurement — and this project does not write in numbers it has
   not measured. Carrying lets the offset be measured from this take, after it,
   and recorded beside audio nothing has touched.
2. **A stem is a capture.** The README's fixture corollary was paid for once
   already: a capture tidied into something neater encodes a guess as a fact.
   If an offset is ever wrong, a carried one is corrected by editing one line;
   a compensated one is baked into every stem.
3. **Re-amping wants the dry signal exactly as it arrived**, and re-amping is
   the reason a dry tap exists at all.

The cost is principle 7's: the stems do not line up by themselves in a DAW. So
`take.yaml` states the fix as a sentence in milliseconds near its top, making it
a slide rather than a calculation. If the friction turns out to matter in use,
compensation can be added at finish time as an option; the number it needs is
already in the file.

## No two stems from one port

Refused before the take starts, not reported after it. Two stems from one port
are one signal under two names, and the routing that ships today produces
exactly that: `guitar_vocal.json` sends both tracks to engine bus 0, where Sushi
sums them into the monitor mix, so a "processed guitar" tap there carries the
vocal too. #57 requires that neither stem contain the other. A stem whose file
name says "guitar" while it holds the vocal is a lie this module will not write.

## Two phases

`start_take` makes the folder, copies the config, and writes `take.yaml` marked
`complete: false` before a sample is recorded; `finish_take` records the
offsets and marks it complete. A rig killed mid-take therefore leaves a folder
that still describes itself, says plainly that it was cut off, and points at
what was captured — principle 9 applied to a recording. Metadata is replaced
atomically, so a kill *during* that write cannot leave a half-written file.

What is not here: capturing audio (#53, the recorder, streams into the paths
`start_take` returns), measuring the offset (the caller passes it in, with what
measured it), and the record button (#57).
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from .save import NAME_PATTERN
from .signals import SUBTYPES, repair_wav, wav_info

# Bumped when the shape of take.yaml changes incompatibly. A reader that meets a
# version it does not know refuses rather than guessing, for machine.py's
# reason: a guess about what a field means here is a take that lines up wrong.
SCHEMA_VERSION = 1

TAKE_FILE = "take.yaml"
CONFIG_FILE = "config.json"
ARCHIVE_DIR = "_archive"

DRY = "dry"
PROCESSED = "processed"
TAPS = (PROCESSED, DRY)

# Regenerated on every write: take.yaml is read back and rewritten by
# `finish_take`, which would drop any comment a human had added.
_HEADER = (
    "# A take recorded by sushi-rig. The stems are exactly as captured; see\n"
    "# `alignment` for how they line up. config.json is the rig that made them.\n"
)


class TakeError(ValueError):
    """A take that must not be started, or a folder that is not a take.

    Carries every problem rather than the first, the way `RigSpec.validate`
    and `MachineSpec.validate` do: a take set up wrong should be fixed in one
    pass, not one error per attempt, with a performance waiting on it.
    """

    def __init__(self, problems: list[str]):
        self.problems = problems
        super().__init__("; ".join(problems))


def default_takes_dir() -> Path:
    """Where takes go unless told otherwise: `~/sushi-rig-takes`.

    Outside the checkout on purpose. Inside it, a takes directory has to be
    gitignored — `*.wav` already is — and `git clean -x` deletes ignored files,
    so a routine cleanup would wipe every performance recorded. A half-ignored
    layout is no better: take.yaml and config.json would be committable while
    the stems beside them were not.

    `paths.py` resolves the *checkout* from the package's own location and never
    from `$HOME`, and that rule is right for code. Takes are the player's, not a
    checkout's, so two worktrees writing to one place is the intent here.
    """
    return Path.home() / "sushi-rig-takes"


# --------------------------------------------------------------------------- #
# Decisions
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class Stem:
    """One file in a take.

    `channel` is the track it belongs to, by the name the config gives it.
    `tap` is `processed` (what you heard) or `dry` (what went in). `port` is the
    PipeWire port it was captured from, recorded because it is the one thing
    that says what is actually in the file — two stems meant to be different
    and captured from one port are visibly the same signal here.

    `latency_samples` is how much later this stem's audio is than the dry
    taps', which are the moment the sound entered the rig. Dry stems are 0;
    `None` means not measured.
    """

    channel: str
    tap: str
    port: str
    latency_samples: int | None = None

    @property
    def filename(self) -> str:
        return stem_filename(self.channel, self.tap)


def _with_reference(stems: Sequence[Stem]) -> list[Stem]:
    """Dry stems at latency 0, which is what being the reference means.

    Left as `None` they were written as `latency_samples: null`, which reads as
    "not measured" — wrong, and exactly the kind of near-enough a reader then
    has to second-guess.
    """
    return [
        Stem(s.channel, s.tap, s.port, 0) if s.tap == DRY and s.latency_samples is None else s
        for s in stems
    ]


def stem_filename(channel: str, tap: str) -> str:
    """`guitar.wav` for what you heard, `guitar-dry.wav` for what went in."""
    return f"{channel}.wav" if tap == PROCESSED else f"{channel}-{tap}.wav"


def check_name(name: Any) -> list[str]:
    if not isinstance(name, str) or not NAME_PATTERN.fullmatch(name):
        return [
            f"invalid take name {name!r}: must match {NAME_PATTERN.pattern} "
            "(lowercase letters, digits, '-', '_'; starts with a letter or digit; "
            "max 64 characters) — the same rule as a config's"
        ]
    return []


def check_stems(stems: Sequence[Stem]) -> list[str]:
    """Every reason these stems cannot make an honest take. Empty means fine.

    Pure, and run before anything is written: a take set up wrong is refused
    while there is still no performance to lose.
    """
    if not stems:
        return ["a take needs at least one stem"]
    problems: list[str] = []
    for stem in stems:
        if not isinstance(stem.channel, str) or not NAME_PATTERN.fullmatch(stem.channel):
            # It becomes a file name, and it comes from a config's track name.
            problems.append(
                f"channel {stem.channel!r} cannot name a file: it must match "
                f"{NAME_PATTERN.pattern}"
            )
        if stem.tap not in TAPS:
            problems.append(
                f"stem {stem.channel!r}: tap must be one of {', '.join(TAPS)}, "
                f"got {stem.tap!r}"
            )
        if not isinstance(stem.port, str) or ":" not in stem.port:
            problems.append(
                f"stem {stem.channel!r} ({stem.tap}): port {stem.port!r} is not in "
                "<client>:<port> form"
            )
        latency = stem.latency_samples
        if latency is not None and (type(latency) is not int or latency < 0):
            problems.append(
                f"stem {stem.channel!r} ({stem.tap}): latency_samples must be a "
                f"whole number of samples, 0 or more, or unset (got {latency!r})"
            )
        elif stem.tap == DRY and latency not in (None, 0):
            problems.append(
                f"stem {stem.channel!r}: a dry tap is the reference the others are "
                f"measured from, so its latency is 0, not {latency}"
            )

    seen_files: dict[str, Stem] = {}
    for stem in stems:
        if stem.filename in seen_files:
            problems.append(f"two stems would both be written to {stem.filename}")
        seen_files[stem.filename] = stem

    by_port: dict[str, list[Stem]] = {}
    for stem in stems:
        by_port.setdefault(stem.port, []).append(stem)
    for port, sharing in by_port.items():
        if len(sharing) > 1:
            names = " and ".join(s.filename for s in sharing)
            problems.append(
                f"{names} would both be captured from {port}, so they are one signal "
                f"under two names. If these are two channels' processed outputs, "
                f"they are being summed before they reach that port — give each "
                f"track its own engine output bus so each can be tapped on its own"
            )
    return problems


def alignment_note(stems: Sequence[Stem], sample_rate: int) -> str:
    """How the stems line up, as a sentence a musician can act on.

    Derived from the stems' `latency_samples` every time, never stored as the
    truth: `take.yaml` carries this sentence for a person reading the file, but
    the number is what is authoritative, and `check_take` reports the sentence
    as stale if someone corrects the number by hand.
    """
    late: dict[int, list[str]] = {}
    unmeasured = []
    for stem in stems:
        if stem.latency_samples is None:
            if stem.tap != DRY:
                unmeasured.append(stem.filename)
        elif stem.latency_samples > 0:
            late.setdefault(stem.latency_samples, []).append(stem.filename)

    has_dry = any(stem.tap == DRY for stem in stems)
    sentences = []
    for samples, files in sorted(late.items()):
        ms = 1000.0 * samples / sample_rate
        verb = "is" if len(files) == 1 else "are"
        if has_dry:
            sentences.append(
                f"{_listed(files)} {verb} {samples} samples ({ms:.2f} ms) later than "
                f"the dry stems. To line them up in a DAW, move "
                f"{'it' if len(files) == 1 else 'them'} {ms:.2f} ms earlier."
            )
        else:
            # Nothing to slide against. The old wording told people to line a
            # stem up with dry stems the take did not have.
            sentences.append(
                f"{_listed(files)} {verb} {samples} samples ({ms:.2f} ms) behind "
                f"what was played."
            )
    measured = {s.latency_samples for s in stems if s.latency_samples is not None}
    if not has_dry and len(measured) == 1 and not unmeasured and late:
        sentences.append("There is no dry tap, and the stems line up with each other.")
    if unmeasured:
        sentences.append(
            f"The offset of {_listed(unmeasured)} was not measured, so "
            f"{'it is' if len(unmeasured) == 1 else 'they are'} exactly as captured "
            f"and may need lining up by ear."
        )
    return " ".join(sentences) or "All the stems line up as they are."


def _recorded_at(value: Any) -> datetime | None:
    """`recorded_at` as written, or as YAML reads it back after a hand edit.

    An unquoted timestamp in YAML loads as a `datetime`, not a string, and a
    naive one at that; this file is always written in UTC, so that is what a
    naive one means.
    """
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return None


def check_format(sample_rate: Any, subtype: Any, quantum: Any) -> list[str]:
    """The numbers every later step divides by or writes into a header.

    Checked when a take starts, because the first thing that would otherwise
    notice a sample rate of 0 is `finish_take` dividing by it — after the
    performance, leaving a take that can never be marked complete.
    """
    problems = []
    if type(sample_rate) is not int or sample_rate <= 0:
        problems.append(f"sample_rate must be a positive whole number of Hz, got {sample_rate!r}")
    if subtype not in SUBTYPES:
        problems.append(f"subtype {subtype!r} is not one of {', '.join(SUBTYPES)}")
    if quantum is not None and (type(quantum) is not int or quantum <= 0):
        problems.append(f"quantum must be a positive block size or unset, got {quantum!r}")
    return problems


def check_channels(stems: Sequence[Stem], config: Any) -> list[str]:
    """Every stem's channel is a track the config actually has.

    A stem is named after its track, and that name is how a take is re-amped:
    `guitr.wav` beside a config with no `guitr` track cannot be put back through
    anything.
    """
    tracks = config.get("tracks") if isinstance(config, dict) else None
    if not isinstance(tracks, list):
        return []
    names = {t.get("name") for t in tracks if isinstance(t, dict)}
    return [
        f"stem {stem.filename} names channel {stem.channel!r}, but the config has no "
        f"such track (it has {', '.join(sorted(map(str, names))) or 'none'})"
        for stem in stems if stem.channel not in names
    ]


def describe_alignment(take: "Take") -> str | None:
    """The sentence to show a person: recomputed for a finished take, so a
    corrected number is never undercut by a stale sentence; the stored one for a
    take that was cut off; nothing when the take cannot say."""
    if not take.complete:
        alignment = take.metadata.get("alignment")
        note = alignment.get("note") if isinstance(alignment, dict) else None
        return note if isinstance(note, str) else None
    rate = take.metadata.get("sample_rate")
    if type(rate) is not int or rate <= 0 or check_stems(take.stems):
        return None
    return alignment_note(take.stems, rate)


def _listed(names: Sequence[str]) -> str:
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def take_metadata(
    name: str,
    stems: Sequence[Stem],
    *,
    sample_rate: int,
    subtype: str,
    quantum: int | None,
    recorded_at: datetime,
    config_source: str,
    complete: bool,
    measured_by: str = "",
    capture: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """take.yaml as written: what a person needs first, first.

    `capture` is what the recorder says it captured and lost
    (`recorder.Recording.summary`). Near the top, because a take with a gap in
    it, or one whose writer died, is one a person needs to know about before
    they use it.
    """
    return {
        "schema_version": SCHEMA_VERSION,
        "name": name,
        "complete": complete,
        "recorded_at": recorded_at.astimezone(timezone.utc).isoformat(timespec="seconds"),
        **({"capture": dict(capture)} if capture is not None else {}),
        "alignment": {
            "note": (
                alignment_note(stems, sample_rate) if complete else
                "This take was cut off before it finished, so no offsets were "
                "measured. The stems hold everything captured up to then."
            ),
            "measured_by": measured_by,
        },
        "sample_rate": sample_rate,
        "subtype": subtype,
        "quantum": quantum,
        "config": CONFIG_FILE,
        "config_source": config_source,
        "stems": [
            {
                "file": stem.filename,
                "channel": stem.channel,
                "tap": stem.tap,
                "port": stem.port,
                "latency_samples": stem.latency_samples,
            }
            for stem in stems
        ],
    }


# --------------------------------------------------------------------------- #
# Reading a take back
# --------------------------------------------------------------------------- #

@dataclass
class Take:
    """A take folder, as read back from disk."""

    folder: Path
    metadata: dict[str, Any]
    stems: list[Stem] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        return self.metadata.get("complete") is True


def load_take(folder: Path) -> Take:
    """Read take.yaml. Raises `TakeError` only for a folder that is not a take.

    Anything that is wrong *with* a take comes from `check_take` instead, so a
    damaged take can still be opened and described.
    """
    import yaml

    folder = Path(folder)
    path = folder / TAKE_FILE
    try:
        raw = yaml.safe_load(path.read_text())
    except OSError as exc:
        raise TakeError([f"{folder} is not a take: cannot read {TAKE_FILE}: {exc}"]) from exc
    except yaml.YAMLError as exc:
        raise TakeError([f"{path} is not valid YAML: {exc}"]) from exc
    if not isinstance(raw, dict):
        raise TakeError([f"{path}: expected a mapping, got {type(raw).__name__}"])
    if raw.get("schema_version") != SCHEMA_VERSION or type(raw.get("schema_version")) is not int:
        raise TakeError([
            f"{path}: schema_version {raw.get('schema_version')!r} is not one this tool "
            f"reads ({SCHEMA_VERSION}). Not guessing at the rest of the file"
        ])
    # Entries that are not mappings are skipped here and *reported* by
    # `check_take`. Skipping them silently made a four-stem take with one
    # damaged entry check out as "ok, 3 stem(s)".
    stems = []
    entries = raw.get("stems")
    for entry in entries if isinstance(entries, list) else []:
        if isinstance(entry, dict):
            stems.append(Stem(
                channel=entry.get("channel"),
                tap=entry.get("tap"),
                port=entry.get("port"),
                latency_samples=entry.get("latency_samples"),
            ))
    return Take(folder, raw, stems)


def check_take(take: Take) -> list[str]:
    """Every problem with a take, including that it was never finished.

    An unfinished take is reported, loudly, but still opened and described:
    whatever it captured is still a performance.
    """
    problems = []
    meta = take.metadata
    if not take.complete:
        problems.append(
            "this take was never finished — the rig stopped mid-take, so its stems "
            "hold whatever was captured up to then and no offsets were measured. "
            "If a stem cannot be read, `sushi-rig take repair` fixes its header"
        )
    capture = meta.get("capture")
    if isinstance(capture, dict):
        if capture.get("error"):
            problems.append(
                f"the recorder stopped early: {capture['error']} — the stems hold "
                "what was written before it"
            )
        lost = capture.get("lost_frames")
        if type(lost) is int and lost > 0:
            dropouts = capture.get("dropouts") or []
            episodes = (len(dropouts) if isinstance(dropouts, list) else 0) + (
                capture.get("unrecorded_episodes") or 0
            )
            rate = meta.get("sample_rate") if type(meta.get("sample_rate")) is int else 0
            problems.append(
                f"the recorder lost audio {episodes} time(s)"
                + (f", {1000.0 * lost / rate:.1f} ms in all" if rate else "")
                + " — the stems are still aligned with each other, but each gap is "
                "a skip in the performance"
            )
    problems += check_name(meta.get("name"))
    entries = meta.get("stems")
    if not isinstance(entries, list):
        problems.append(f"stems is not a list, so the take does not say what it holds (got {entries!r})")
        entries = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            problems.append(
                f"stem entry {index + 1} is {entry!r}, not a description of a stem, "
                f"so whatever file it was is no longer part of the take"
            )
    stem_problems = check_stems(take.stems)
    problems += stem_problems

    files = [entry.get("file") for entry in entries if isinstance(entry, dict)]
    for stem, written in zip(take.stems, files):
        if written != stem.filename:
            problems.append(
                f"stem {stem.channel!r} ({stem.tap}) is listed as {written!r}, but "
                f"this tool names it {stem.filename!r}"
            )

    config = take.folder / CONFIG_FILE
    try:
        parsed = json.loads(config.read_text())
    except OSError as exc:
        problems.append(f"{CONFIG_FILE} is missing, so the take no longer says what rig made it: {exc}")
    except ValueError as exc:
        problems.append(f"{CONFIG_FILE} is not valid JSON: {exc}")
    else:
        if not isinstance(parsed, dict) or not isinstance(parsed.get("tracks"), list):
            problems.append(f"{CONFIG_FILE} has no tracks, so it is not a Sushi config")
        else:
            problems += check_channels(take.stems, parsed)

    rate, subtype = meta.get("sample_rate"), meta.get("subtype")
    format_problems = check_format(rate, subtype, meta.get("quantum"))
    problems += format_problems

    alignment = meta.get("alignment")
    if not isinstance(alignment, dict):
        problems.append(
            f"alignment is {alignment!r}, not a mapping, so the take does not say how "
            f"its stems line up"
        )
    elif take.complete and not stem_problems and not format_problems:
        # The number is the truth and the sentence is for reading. Correcting a
        # `latency_samples` by hand — the fix the carried-offset design relies
        # on — must not leave the instruction above it telling someone to slide
        # a stem by the old amount.
        expected = alignment_note(take.stems, rate)
        if alignment.get("note") != expected:
            problems.append(
                f"the alignment note is out of date with the stems' offsets: it says "
                f"{alignment.get('note')!r}, and the offsets say {expected!r}"
            )
    lengths: dict[str, int] = {}
    for stem in take.stems:
        path = take.folder / stem.filename
        if not path.exists():
            problems.append(f"{stem.filename} is listed but not in the folder")
            continue
        try:
            info = wav_info(path)
        except (ValueError, OSError) as exc:
            # OSError too: a directory where a stem should be, or a file this
            # user cannot read, must be one line in the list, not the end of it.
            problems.append(f"{stem.filename} cannot be read: {exc}")
            continue
        if info.sample_rate != rate:
            problems.append(
                f"{stem.filename} is at {info.sample_rate} Hz, but the take says {rate} Hz"
            )
        if info.subtype != subtype:
            problems.append(
                f"{stem.filename} is {info.subtype}, but the take says {subtype}"
            )
        lengths[stem.filename] = info.frames
    if len(set(lengths.values())) > 1:
        # One capture stream writes every tap in lockstep, so unequal lengths
        # mean one stem lost audio the others kept.
        problems.append(
            "the stems are different lengths ("
            + ", ".join(f"{f} {n} frames" for f, n in lengths.items())
            + "), so at least one is missing audio the others have"
        )
    return problems


# --------------------------------------------------------------------------- #
# Effects
# --------------------------------------------------------------------------- #

def _write_atomic(path: Path, text: str) -> None:
    """Replace `path` in one step, so a kill or a power cut mid-write leaves the
    old file or the new one and never half of either.

    The rename alone survives a killed process, because the data is already in
    the page cache. It does not survive losing power: ext4 and xfs can commit the
    rename before the data, leaving a zero-byte file, so the data is synced
    before the rename and the directory after it. A rig losing power on stage is
    not a hypothetical worth skipping two syscalls for.
    """
    tmp = path.with_name(f".{path.name}.tmp")
    with tmp.open("w") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def _write_metadata(folder: Path, metadata: dict[str, Any]) -> None:
    import yaml

    _write_atomic(
        folder / TAKE_FILE,
        _HEADER + yaml.dump(metadata, default_flow_style=False, sort_keys=False,
                            allow_unicode=True),
    )


def _archive_existing(takes_dir: Path, name: str, folder: Path, now: datetime) -> Path:
    """Move an earlier take of this name aside, keyed by when it was recorded.

    Keyed by its own `recorded_at` where that can be read, because "the take
    from Tuesday evening" is how a person looks for it; by now otherwise. A
    collision gets a suffix rather than replacing anything.
    """
    stamp = now
    try:
        stamp = _recorded_at(load_take(folder).metadata.get("recorded_at")) or now
    except TakeError:
        pass
    base = takes_dir / ARCHIVE_DIR / name
    base.mkdir(parents=True, exist_ok=True)
    label = stamp.astimezone(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")
    target, n = base / label, 2
    while target.exists():
        target, n = base / f"{label}-{n}", n + 1
    folder.rename(target)
    print(f"archived the earlier take {name!r} -> {target}", file=sys.stderr)
    return target


def start_take(
    takes_dir: Path,
    name: str,
    config: dict[str, Any],
    stems: Sequence[Stem],
    *,
    sample_rate: int,
    subtype: str,
    quantum: int | None,
    config_source: str,
    now: datetime | None = None,
) -> dict[str, Path]:
    """Make the take's folder before a sample is recorded. Returns stem -> path.

    `config` is the config **as it was live**, amp included. The caller builds
    it, because only the caller knows: `up --amp-model` runs an amp the file on
    disk does not name, and `--no-amp` runs none where the file names one. See
    `save.save_config` for how the amp is written into a config.

    Refuses, writing nothing, if the plan cannot make an honest take.
    """
    stems = _with_reference(stems)
    problems = check_name(name) + check_stems(stems)
    problems += check_format(sample_rate, subtype, quantum)
    if not isinstance(config, dict) or not isinstance(config.get("tracks"), list):
        problems.append("the config has no tracks, so it is not a Sushi config")
    else:
        problems += check_channels(stems, config)
    # Serialised here, before anything is moved or made. Writing it after the
    # earlier take had been archived and the new folder created meant a config
    # holding something JSON cannot encode left an empty folder behind, which
    # is exactly what "writes nothing" promised not to.
    config_text = ""
    try:
        config_text = json.dumps(config, indent=2) + "\n"
    except (TypeError, ValueError) as exc:
        problems.append(f"the config cannot be written as JSON: {exc}")
    if problems:
        raise TakeError(problems)

    now = now or datetime.now(timezone.utc)
    takes_dir = Path(takes_dir)
    folder = takes_dir / name
    if folder.exists():
        _archive_existing(takes_dir, name, folder, now)
    folder.mkdir(parents=True)

    # A copy, never a reference: a take that points at config/electric-clean.json
    # says nothing once that file has moved on, which is the failure the
    # project's versioning exists to prevent.
    _write_atomic(folder / CONFIG_FILE, config_text)
    _write_metadata(folder, take_metadata(
        name, stems, sample_rate=sample_rate, subtype=subtype, quantum=quantum,
        recorded_at=now, config_source=config_source, complete=False,
    ))
    return {stem.filename: folder / stem.filename for stem in stems}


def repair_take(folder: Path) -> tuple[dict[str, int], dict[str, str]]:
    """Make every stem of a take cut off mid-write readable.

    Returns frames per repaired stem, and why each of the others could not be.
    Every stem is tried: stopping at the first that failed left the rest with
    their stale headers, each of which could have been fixed.

    The recorder rewrites each stem's header after every block, so only a kill
    in the instant between a block and its header leaves one stale; this sets
    it from the file's own length.
    """
    take = load_take(folder)
    repaired: dict[str, int] = {}
    failed: dict[str, str] = {}
    for stem in take.stems:
        path = Path(folder) / stem.filename
        if not path.exists():
            failed[stem.filename] = "not in the folder"
            continue
        try:
            repaired[stem.filename] = repair_wav(path)
        except (ValueError, OSError) as exc:
            failed[stem.filename] = str(exc)
    return repaired, failed


def finish_take(
    folder: Path,
    latencies: dict[str, int | None],
    *,
    measured_by: str,
    capture: dict[str, Any] | None = None,
) -> Take:
    """Record each stem's measured offset and mark the take complete.

    `latencies` maps a stem's file name to its offset; a stem missing from it
    keeps what it had. Dry stems are the reference and need no entry.
    `measured_by` says how the numbers were arrived at, because an offset whose
    provenance is lost is indistinguishable from one somebody guessed.
    """
    take = load_take(folder)
    meta = take.metadata
    recorded_at = _recorded_at(meta.get("recorded_at"))
    problems = check_name(meta.get("name")) + check_format(
        meta.get("sample_rate"), meta.get("subtype"), meta.get("quantum")
    )
    if recorded_at is None:
        problems.append(f"recorded_at {meta.get('recorded_at')!r} is not a time")
    if problems:
        # As a TakeError, which the callers catch. A KeyError here would be a
        # traceback at the end of a take, the worst moment to have one.
        raise TakeError(problems)
    known = {stem.filename for stem in take.stems}
    unknown = sorted(set(latencies) - known)
    if unknown:
        raise TakeError([f"no stem called {name} in this take" for name in unknown])
    stems = _with_reference([
        Stem(s.channel, s.tap, s.port, latencies.get(s.filename, s.latency_samples))
        for s in take.stems
    ])
    problems = check_stems(stems)
    if problems:
        raise TakeError(problems)
    _write_metadata(Path(folder), take_metadata(
        meta["name"], stems,
        sample_rate=meta["sample_rate"], subtype=meta["subtype"],
        quantum=meta.get("quantum"), recorded_at=recorded_at,
        config_source=str(meta.get("config_source", "")), complete=True,
        measured_by=measured_by,
        # Kept when not given: finishing a take again to correct an offset
        # used to rewrite its record of what was lost as nothing at all.
        capture=capture if capture is not None else meta.get("capture"),
    ))
    return load_take(folder)
