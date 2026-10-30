"""What a take is on disk, and the decisions about its shape.

Everything here runs without audio hardware. The decisions — names, layout,
which plans are refused, how the offset is stated — are pure and tested
directly. The writes are tested by running them against a temporary directory,
which is cheap enough that there is no reason to mock them.

The stems are synthetic. A take's contents are not this module's concern —
whether a take actually holds sound is `selftest`'s question (#49) — so a sine
written at the right rate and depth is all a stem needs to be here.

The ports are a mixture, and which is which matters. The **dry** ports are real:
`Audient iD14:capture_AUX0` and `AUX1` are the guitar and vocal inputs in the
rig's own machine file. The **processed** ones are invented. Today's
`guitar_vocal.json` sums both tracks onto engine bus 0, so there is no port
anywhere that carries the processed vocal on its own; `sushi:audio_output_2`
below is where it would be if the vocal track had its own output bus. The test
that refuses the real routing uses the real port.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")

from sushi_rig import signals, take  # noqa: E402
from sushi_rig.paths import checkout_root  # noqa: E402
from sushi_rig.take import DRY, PROCESSED, Stem, TakeError  # noqa: E402

SR = 48000
ROOT = checkout_root()
SUSHI = ROOT / "sushi"
RECORDED = datetime(2026, 10, 30, 21, 40, tzinfo=timezone.utc)

GUITAR_IN = "Audient iD14:capture_AUX0"
VOCAL_IN = "Audient iD14:capture_AUX1"
# Where the processed signals would be with one engine output bus per track.
GUITAR_OUT = "sushi:audio_output_0"
VOCAL_OUT = "sushi:audio_output_2"


def four_stems() -> list[Stem]:
    return [
        Stem("guitar", PROCESSED, GUITAR_OUT),
        Stem("guitar", DRY, GUITAR_IN),
        Stem("vocal", PROCESSED, VOCAL_OUT),
        Stem("vocal", DRY, VOCAL_IN),
    ]


def config() -> dict:
    return json.loads((ROOT / "config" / "guitar_vocal.json").read_text())


def start(tmp_path, name="riff-idea", stems=None, cfg=None, **overrides) -> dict[str, Path]:
    options = dict(
        sample_rate=SR, subtype="pcm24", quantum=256,
        config_source="config/guitar_vocal.json", now=RECORDED,
    )
    options.update(overrides)
    return take.start_take(
        tmp_path, name, cfg if cfg is not None else config(),
        stems or four_stems(), **options,
    )


def record(paths: dict[str, Path], seconds: float = 0.25, subtype="pcm24") -> None:
    """What the recorder will do: fill every stem, in lockstep."""
    for path in paths.values():
        signals.write_wav(path, signals.sine(SR, seconds, 220.0), SR, subtype=subtype)


# --------------------------------------------------------------------------- #
# Names
# --------------------------------------------------------------------------- #

def test_what_you_heard_gets_the_plain_name_and_the_dry_tap_the_suffix():
    assert take.stem_filename("guitar", PROCESSED) == "guitar.wav"
    assert take.stem_filename("guitar", DRY) == "guitar-dry.wav"


@pytest.mark.parametrize("name", ["riff-idea", "take_2", "a"])
def test_a_take_is_named_by_the_same_rule_as_a_config(name):
    assert take.check_name(name) == []


@pytest.mark.parametrize("name", ["../escape", "/abs", ".hidden", "Riff", "_archive", ""])
def test_a_name_that_could_escape_or_collide_is_refused(name):
    """Names will arrive from the record button over unauthenticated OSC, so
    this is a security boundary, not tidiness — `save.NAME_PATTERN`'s."""
    assert take.check_name(name)


def test_the_archive_cannot_collide_with_a_take_of_the_same_name():
    """A take called "archive" is legal. The archive is `_archive`, which the
    name rule cannot produce."""
    assert take.check_name("archive") == []
    assert take.check_name(take.ARCHIVE_DIR)


def test_takes_live_outside_the_checkout_where_git_clean_cannot_reach_them():
    """Inside it they would have to be gitignored, and `git clean -x` deletes
    ignored files — every performance, gone in a routine cleanup."""
    with pytest.raises(ValueError):
        take.default_takes_dir().resolve().relative_to(ROOT.resolve())


# --------------------------------------------------------------------------- #
# Which plans are refused
# --------------------------------------------------------------------------- #

def test_four_stems_from_four_ports_make_an_honest_take():
    assert take.check_stems(four_stems()) == []


def test_two_channels_summed_before_their_tap_are_refused():
    """The routing that ships today. `guitar_vocal.json` sends both tracks to
    engine bus 0, so the only processed port carries guitar and vocal together,
    and a stem called guitar.wav would hold the vocal too. #57 requires neither
    stem contain the other."""
    stems = [
        Stem("guitar", PROCESSED, GUITAR_OUT),
        Stem("guitar", DRY, GUITAR_IN),
        Stem("vocal", PROCESSED, GUITAR_OUT),
        Stem("vocal", DRY, VOCAL_IN),
    ]
    problems = take.check_stems(stems)
    assert len(problems) == 1
    assert "guitar.wav and vocal.wav" in problems[0]
    assert "one signal under two names" in problems[0]
    assert "engine output bus" in problems[0], "say how to fix it"


def test_two_stems_that_would_overwrite_each_other_are_refused():
    stems = [Stem("guitar", PROCESSED, GUITAR_OUT), Stem("guitar", PROCESSED, VOCAL_OUT)]
    assert any("both be written to guitar.wav" in p for p in take.check_stems(stems))


@pytest.mark.parametrize("stem, expected", [
    (Stem("guitar", "wet", GUITAR_OUT), "tap must be one of"),
    (Stem("Guitar", PROCESSED, GUITAR_OUT), "cannot name a file"),
    (Stem("../g", PROCESSED, GUITAR_OUT), "cannot name a file"),
    (Stem("guitar", PROCESSED, "no-colon"), "<client>:<port>"),
    (Stem("guitar", PROCESSED, GUITAR_OUT, -1), "0 or more"),
    (Stem("guitar", PROCESSED, GUITAR_OUT, True), "whole number"),
    (Stem("guitar", DRY, GUITAR_IN, 12), "the reference"),
])
def test_a_stem_that_cannot_be_written_honestly_is_refused(stem, expected):
    """`True` is an int in Python, so a latency of True would otherwise be one
    sample."""
    problems = take.check_stems([stem])
    assert any(expected in p for p in problems), problems


def test_an_empty_take_is_refused():
    assert take.check_stems([]) == ["a take needs at least one stem"]


# --------------------------------------------------------------------------- #
# How the offset is stated
# --------------------------------------------------------------------------- #

def test_the_offset_is_a_sentence_a_musician_can_act_on():
    stems = [Stem("guitar", PROCESSED, GUITAR_OUT, 257), Stem("guitar", DRY, GUITAR_IN, 0)]
    note = take.alignment_note(stems, SR)
    assert note == (
        "guitar.wav is 257 samples (5.35 ms) later than the dry stems. "
        "To line them up in a DAW, move it 5.35 ms earlier."
    )


def test_stems_with_the_same_offset_are_named_together():
    stems = [Stem("guitar", PROCESSED, GUITAR_OUT, 257), Stem("vocal", PROCESSED, VOCAL_OUT, 257)]
    assert take.alignment_note(stems, SR).startswith("guitar.wav and vocal.wav are 257")


def test_an_unmeasured_offset_is_said_rather_than_assumed():
    note = take.alignment_note([Stem("guitar", PROCESSED, GUITAR_OUT)], SR)
    assert "was not measured" in note and "exactly as captured" in note


def test_stems_with_no_offset_say_so():
    stems = [Stem("guitar", DRY, GUITAR_IN, 0), Stem("vocal", DRY, VOCAL_IN, 0)]
    assert take.alignment_note(stems, SR) == "All the stems line up as they are."


# --------------------------------------------------------------------------- #
# Starting a take
# --------------------------------------------------------------------------- #

def test_starting_a_take_lays_out_the_folder_before_a_sample_is_recorded(tmp_path):
    paths = start(tmp_path)
    folder = tmp_path / "riff-idea"
    assert sorted(paths) == ["guitar-dry.wav", "guitar.wav", "vocal-dry.wav", "vocal.wav"]
    assert all(p.parent == folder for p in paths.values())
    assert (folder / take.CONFIG_FILE).is_file()
    started = take.load_take(folder)
    assert started.complete is False, "it is not finished until it is finished"
    assert started.metadata["recorded_at"] == "2026-10-30T21:40:00+00:00"


def test_dry_stems_are_written_at_zero_not_as_unmeasured(tmp_path):
    """Left as None they were written `null`, which reads as "not measured" —
    and a dry tap is the reference, so its offset is known by definition."""
    start(tmp_path)
    written = take.load_take(tmp_path / "riff-idea").metadata["stems"]
    assert {s["file"]: s["latency_samples"] for s in written if s["tap"] == DRY} == {
        "guitar-dry.wav": 0, "vocal-dry.wav": 0
    }


def test_a_refused_plan_writes_nothing_at_all(tmp_path):
    """Refused while there is still no performance to lose, and leaving no
    half-made folder behind to be mistaken for a take."""
    with pytest.raises(TakeError) as refused:
        start(tmp_path, name="Bad Name", stems=[Stem("guitar", "wet", "x")])
    assert len(refused.value.problems) >= 3, "every problem, not the first"
    assert list(tmp_path.iterdir()) == []


def test_the_config_is_a_copy_not_a_reference(tmp_path):
    """A take pointing at config/ says nothing once that file has moved on."""
    live = config()
    start(tmp_path, cfg=live)
    live["tracks"].clear()
    copied = json.loads((tmp_path / "riff-idea" / take.CONFIG_FILE).read_text())
    assert copied["tracks"], "later edits to the source do not reach the take"
    assert copied == config()


def test_the_config_as_it_was_live_is_what_is_kept(tmp_path):
    """The caller passes the live config because only it knows: `up
    --amp-model` runs an amp the file does not name."""
    live = config()
    live["_amp"] = {"plugin": "nam", "model": "amp/models/audition.nam", "parameters": {}}
    start(tmp_path, cfg=live)
    copied = json.loads((tmp_path / "riff-idea" / take.CONFIG_FILE).read_text())
    assert copied["_amp"]["model"] == "amp/models/audition.nam"


def test_recording_over_a_name_archives_the_earlier_take(tmp_path):
    record(start(tmp_path))
    take.finish_take(tmp_path / "riff-idea", {}, measured_by="")
    record(start(tmp_path, now=datetime(2026, 10, 31, 9, 0, tzinfo=timezone.utc)))

    archived = tmp_path / take.ARCHIVE_DIR / "riff-idea" / "2026-10-30T21-40-00"
    assert (archived / "guitar.wav").is_file(), "keyed by when it was recorded"
    assert take.load_take(archived).metadata["recorded_at"] == "2026-10-30T21:40:00+00:00"
    assert take.load_take(tmp_path / "riff-idea").metadata["recorded_at"].startswith("2026-10-31")


def test_an_archive_collision_gets_a_suffix_rather_than_replacing_anything(tmp_path):
    for _ in range(3):
        record(start(tmp_path))
    archive = tmp_path / take.ARCHIVE_DIR / "riff-idea"
    assert sorted(p.name for p in archive.iterdir()) == [
        "2026-10-30T21-40-00", "2026-10-30T21-40-00-2"
    ]


def test_a_take_called_archive_coexists_with_the_archive(tmp_path):
    record(start(tmp_path, name="archive"))
    record(start(tmp_path, name="archive"))
    assert (tmp_path / "archive" / take.TAKE_FILE).is_file()
    assert (tmp_path / take.ARCHIVE_DIR / "archive").is_dir()


# --------------------------------------------------------------------------- #
# Finishing, and a take that never was
# --------------------------------------------------------------------------- #

def test_finishing_records_the_offsets_and_how_they_were_measured(tmp_path):
    record(start(tmp_path))
    finished = take.finish_take(
        tmp_path / "riff-idea", {"guitar.wav": 257, "vocal.wav": 257},
        measured_by="estimate_delay, each stem against its dry tap",
    )
    assert finished.complete
    assert {s.filename: s.latency_samples for s in finished.stems} == {
        "guitar.wav": 257, "guitar-dry.wav": 0, "vocal.wav": 257, "vocal-dry.wav": 0,
    }
    alignment = finished.metadata["alignment"]
    assert "5.35 ms earlier" in alignment["note"]
    assert alignment["measured_by"] == "estimate_delay, each stem against its dry tap"
    assert take.check_take(finished) == []


def test_an_offset_for_a_stem_the_take_does_not_have_is_refused(tmp_path):
    record(start(tmp_path))
    with pytest.raises(TakeError, match="no stem called bass.wav"):
        take.finish_take(tmp_path / "riff-idea", {"bass.wav": 1}, measured_by="")


def test_a_take_cut_off_mid_recording_still_describes_itself(tmp_path):
    """The rig killed after `start_take` and part way through the stems.
    Principle 9: it must come back with the state it had, and here the state is
    a performance."""
    record(start(tmp_path), seconds=0.1)
    cut_off = take.load_take(tmp_path / "riff-idea")
    problems = take.check_take(cut_off)
    assert problems == [
        "this take was never finished — the rig stopped mid-take, so its stems "
        "hold whatever was captured up to then and no offsets were measured"
    ]
    assert "cut off before it finished" in cut_off.metadata["alignment"]["note"]


def test_metadata_is_replaced_in_one_step(tmp_path):
    """No temporary file is left beside it, so a reader never sees half of one."""
    record(start(tmp_path))
    take.finish_take(tmp_path / "riff-idea", {}, measured_by="")
    assert sorted(p.name for p in (tmp_path / "riff-idea").iterdir() if p.name.startswith(".")) == []


# --------------------------------------------------------------------------- #
# Checking a take
# --------------------------------------------------------------------------- #

def finished_take(tmp_path, **record_kwargs) -> Path:
    paths = start(tmp_path)
    record(paths, **record_kwargs)
    take.finish_take(tmp_path / "riff-idea", {"guitar.wav": 257, "vocal.wav": 257},
                     measured_by="test")
    return tmp_path / "riff-idea"


def test_a_missing_stem_is_found(tmp_path):
    folder = finished_take(tmp_path)
    (folder / "vocal-dry.wav").unlink()
    assert any("vocal-dry.wav is listed but not in the folder" in p
               for p in take.check_take(take.load_take(folder)))


def test_a_stem_shorter_than_the_others_is_found(tmp_path):
    """One capture stream writes every tap in lockstep, so a short one lost
    audio the others kept."""
    folder = finished_take(tmp_path)
    signals.write_wav(folder / "vocal.wav", signals.sine(SR, 0.1, 220.0), SR, subtype="pcm24")
    assert any("different lengths" in p for p in take.check_take(take.load_take(folder)))


def test_a_stem_at_the_wrong_rate_or_depth_is_found(tmp_path):
    folder = finished_take(tmp_path)
    signals.write_wav(folder / "guitar.wav", signals.sine(44100, 0.25, 220.0), 44100,
                      subtype="pcm24")
    signals.write_wav(folder / "vocal.wav", signals.sine(SR, 0.25, 220.0), SR,
                      subtype="float32")
    problems = take.check_take(take.load_take(folder))
    assert any("guitar.wav is at 44100 Hz" in p for p in problems)
    assert any("vocal.wav is float32" in p for p in problems)


def test_a_truncated_stem_is_reported_rather_than_raised(tmp_path):
    """What a recording cut off mid-write looks like: a data chunk claiming
    more than the file holds."""
    folder = finished_take(tmp_path)
    stem = folder / "guitar.wav"
    stem.write_bytes(stem.read_bytes()[:-1000])
    problems = take.check_take(take.load_take(folder))
    assert any("guitar.wav cannot be read" in p and "truncated" in p for p in problems)


def test_a_take_without_its_config_no_longer_says_what_made_it(tmp_path):
    folder = finished_take(tmp_path)
    (folder / take.CONFIG_FILE).unlink()
    assert any("no longer says what rig made it" in p
               for p in take.check_take(take.load_take(folder)))


def test_a_folder_that_is_not_a_take_is_refused_by_name(tmp_path):
    with pytest.raises(TakeError, match="is not a take"):
        take.load_take(tmp_path)


def test_a_take_from_a_newer_schema_is_refused_rather_than_guessed(tmp_path):
    folder = finished_take(tmp_path)
    text = (folder / take.TAKE_FILE).read_text().replace("schema_version: 1", "schema_version: 2")
    (folder / take.TAKE_FILE).write_text(text)
    with pytest.raises(TakeError, match="Not guessing"):
        take.load_take(folder)


def test_take_check_states_the_alignment_and_exits_by_the_problems(tmp_path, capsys):
    from sushi_rig import cli

    folder = finished_take(tmp_path)
    assert cli.main(["take", "check", str(folder)]) == 0
    out = capsys.readouterr().out
    assert "5.35 ms earlier" in out and "ok, 4 stem(s)" in out

    (folder / "vocal.wav").unlink()
    assert cli.main(["take", "check", str(folder)]) == 1


# --------------------------------------------------------------------------- #
# The claim that the rig is in the folder
# --------------------------------------------------------------------------- #

needs_sushi = pytest.mark.skipif(
    not SUSHI.exists() or not (ROOT / "plugins").is_dir(),
    reason="needs the Sushi binary and the rig's plugins",
)


@needs_sushi
def test_the_config_inside_a_take_loads_and_verifies_on_its_own(tmp_path):
    """#55's "done": the claim that a take reproduces the rig is tested rather
    than asserted. The copy lives outside config/, which is the point — `verify`
    resolves the rig's plugins from the checkout, not from where the config
    sits."""
    from sushi_rig.verify import verify

    folder = finished_take(tmp_path)
    problems = verify(folder / take.CONFIG_FILE, str(SUSHI), quick=True)
    assert not problems, "\n" + "\n".join(f"  {p}" for p in problems)


# --------------------------------------------------------------------------- #
# Found in review: each of these was a way to lose a take, or to mislead about one
# --------------------------------------------------------------------------- #

def test_a_config_json_cannot_encode_is_refused_before_the_earlier_take_moves(tmp_path):
    """It used to be serialised after the earlier take had been archived and the
    new folder made, so the refusal left an empty folder where a take had been."""
    record(start(tmp_path))
    take.finish_take(tmp_path / "riff-idea", {}, measured_by="")
    unencodable = config()
    unencodable["amp"] = Path("amp/models/x.nam")
    with pytest.raises(TakeError, match="cannot be written as JSON"):
        start(tmp_path, cfg=unencodable)
    assert take.load_take(tmp_path / "riff-idea").complete, "the earlier take is untouched"
    assert not (tmp_path / take.ARCHIVE_DIR).exists(), "and was never archived"


@pytest.mark.parametrize("rate", [0, -48000, None, True, 48000.0])
def test_a_sample_rate_nothing_can_divide_by_is_refused_at_the_start(tmp_path, rate):
    """The first thing to notice used to be `finish_take` dividing by it —
    after the performance, leaving a take that could never be marked complete."""
    with pytest.raises(TakeError, match="sample_rate"):
        start(tmp_path, sample_rate=rate)
    assert list(tmp_path.iterdir()) == []


def test_correcting_an_offset_by_hand_is_not_undercut_by_the_old_sentence(tmp_path, capsys):
    """The carried-offset design's whole claim is that a wrong offset is fixed
    by editing one number. `take check` used to print the stored sentence, so
    after the edit it still said 5.35 ms and the stem was slid by the old
    amount."""
    from sushi_rig import cli

    folder = finished_take(tmp_path)
    path = folder / take.TAKE_FILE
    path.write_text(path.read_text().replace("latency_samples: 257", "latency_samples: 513", 1))

    problems = take.check_take(take.load_take(folder))
    assert any("out of date with the stems' offsets" in p for p in problems)
    assert "10.69 ms" in take.describe_alignment(take.load_take(folder))

    cli.main(["take", "check", str(folder)])
    out = capsys.readouterr().out
    assert "guitar.wav is 513 samples (10.69 ms)" in out, "the number wins"


def test_a_stem_that_is_a_directory_is_reported_rather_than_crashing(tmp_path):
    folder = finished_take(tmp_path)
    (folder / "guitar.wav").unlink()
    (folder / "guitar.wav").mkdir()
    assert any("guitar.wav cannot be read" in p for p in take.check_take(take.load_take(folder)))


def test_finishing_a_take_with_damaged_metadata_says_so_as_a_take_error(tmp_path):
    """A KeyError here would be a traceback at the end of a take, the worst
    moment to have one, and the CLI only catches TakeError."""
    paths = start(tmp_path)
    record(paths)
    path = tmp_path / "riff-idea" / take.TAKE_FILE
    path.write_text(path.read_text().replace("sample_rate: 48000\n", ""))
    with pytest.raises(TakeError, match="sample_rate"):
        take.finish_take(tmp_path / "riff-idea", {}, measured_by="")


def test_an_unquoted_recorded_at_from_a_hand_edit_still_finishes(tmp_path):
    """YAML reads an unquoted timestamp back as a datetime, not a string."""
    record(start(tmp_path))
    path = tmp_path / "riff-idea" / take.TAKE_FILE
    path.write_text(path.read_text().replace(
        "recorded_at: '2026-10-30T21:40:00+00:00'", "recorded_at: 2026-10-30 21:40:00"
    ))
    finished = take.finish_take(tmp_path / "riff-idea", {}, measured_by="")
    assert finished.metadata["recorded_at"] == "2026-10-30T21:40:00+00:00"


def test_an_alignment_that_is_not_a_mapping_is_reported_by_the_command_that_describes_it(
    tmp_path, capsys
):
    from sushi_rig import cli

    folder = finished_take(tmp_path)
    path = folder / take.TAKE_FILE
    text = path.read_text()
    start_at = text.index("alignment:")
    end_at = text.index("sample_rate:")
    path.write_text(text[:start_at] + "alignment: shifted by hand\n" + text[end_at:])
    assert cli.main(["take", "check", str(folder)]) == 1
    assert "not a mapping" in capsys.readouterr().out


def test_a_damaged_stem_entry_is_reported_not_silently_dropped(tmp_path, capsys):
    """It used to vanish: a four-stem take with one entry turned into a bare
    string checked out as "ok, 3 stem(s)"."""
    from sushi_rig import cli
    import yaml

    folder = finished_take(tmp_path)
    path = folder / take.TAKE_FILE
    meta = yaml.safe_load(path.read_text())
    meta["stems"][3] = "vocal-dry.wav"
    path.write_text(yaml.dump(meta, sort_keys=False))

    assert any("stem entry 4 is 'vocal-dry.wav'" in p
               for p in take.check_take(take.load_take(folder)))
    assert cli.main(["take", "check", str(folder)]) == 1
    assert "ok, 3 stem(s)" not in capsys.readouterr().out


def test_a_stem_whose_channel_is_no_track_in_the_config_is_refused(tmp_path):
    """A stem is named after its track, and that name is how it is re-amped:
    guitr.wav beside a config with no guitr track goes back through nothing."""
    stems = four_stems()
    stems[0] = Stem("guitr", PROCESSED, GUITAR_OUT)
    with pytest.raises(TakeError, match="no such track"):
        start(tmp_path, stems=stems)


def test_a_take_with_no_dry_tap_does_not_point_at_dry_stems():
    lone = take.alignment_note([Stem("guitar", PROCESSED, GUITAR_OUT, 257)], SR)
    assert "dry stems" not in lone and "move" not in lone
    assert "behind what was played" in lone

    together = take.alignment_note(
        [Stem("guitar", PROCESSED, GUITAR_OUT, 257), Stem("vocal", PROCESSED, VOCAL_OUT, 257)],
        SR,
    )
    assert "line up with each other" in together

    apart = take.alignment_note(
        [Stem("guitar", PROCESSED, GUITAR_OUT, 0), Stem("vocal", PROCESSED, VOCAL_OUT, 257)],
        SR,
    )
    assert "line up with each other" not in apart, "they do not"


def test_metadata_reaches_the_disk_before_it_replaces_the_old(tmp_path, monkeypatch):
    """The rename alone survives a killed process but not a power cut: ext4 and
    xfs can commit the rename before the data, leaving take.yaml empty. Pinned
    so the two syncs cannot be tidied away."""
    synced = []
    real_fsync = take.os.fsync

    def recording_fsync(fd):
        synced.append(fd)
        real_fsync(fd)

    monkeypatch.setattr(take.os, "fsync", recording_fsync)
    start(tmp_path)
    # config.json and take.yaml, each: the file, then its directory.
    assert len(synced) == 4
