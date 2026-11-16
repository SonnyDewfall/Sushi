"""#144: saving or adding a rig does not turn the sound check red.

A committed rig's reference records the rig it was made from (`rig_hash`
of the parsed file). `nulltest` compares exactly while that is still the
rig; a rig that changed, or has none, is STALE, a warning, once its render
is finite, two-sided and balanced (`heard_findings`), which every rig
render is held to whatever its reference says. The frozen rigs' references
under tests/fixtures/sound/ are held exactly, and fail.

Everything here runs with a passthrough standing in for Sushi, through
`cli.main`, on a temporary checkout: no committed rig is read. The real
renders are the container job's (rig-container.yml) and the slow tests'.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

np = pytest.importorskip("numpy")

from sushi_rig import nulltest, render, signals  # noqa: E402
from sushi_rig.nulltest import (  # noqa: E402
    BALANCE_DB, RIG_HASH_RULE, MadeFrom, from_dict, heard_findings, made_from_of, rig_hash, staleness, to_dict,
)

PORTS = Path(__file__).resolve().parents[2] / "plugin-ports.txt"
# The real one: the stand-in for Sushi is put in its place (`_sushi`).
RUN = subprocess.run

RIG = """\
kind: rig
schema_version: 1
name: {name}
channels:
- name: board
  input: guitar-in
  width: mono
  level: 0.0
  chain: []
master: {{output: monitor-out, chain: []}}
"""


# --------------------------------------------------------------------------- #
# The hash
# --------------------------------------------------------------------------- #

def _hash(text: str) -> str:
    return rig_hash(yaml.safe_load(text))


def test_the_hash_does_not_see_how_a_number_is_written():
    """`1.0` and `1`, `0.50` and `.5`, `-0.0` and `0`: what a save may
    rewrite without changing anything (2c36874 did, fifteen lines)."""
    assert _hash("a: 1.0\nb: 0.50\nc: -0.0\nd: 1.000e+2\n") == _hash("a: 1\nb: .5\nc: 0\nd: 100\n")


def test_the_hash_does_not_see_key_order_comments_quoting_or_layout():
    one = "kind: rig\nname: x  # the rig\nmaster: {output: monitor-out, chain: []}\n"
    two = "# saved from the page\nmaster:\n  chain: []\n  output: 'monitor-out'\nname: \"x\"\nkind: rig\n"
    assert _hash(one) == _hash(two)


@pytest.mark.parametrize("changed", [
    "a: 1.01\nb: [x, y]\nc: true\n",   # a value
    "a: 1\nb: [y, x]\nc: true\n",      # a chain's order
    "a: 1\nb: [x, y]\nc: 1\n",         # a bool is not the number 1
    "a: 1\nb: [x, y]\nc: 'true'\n",    # nor a string
    "a: 1\nb: [x, y]\nc: true\nd: 0\n",  # a key added
    "a: 1\nb: [x, y]\n",               # a key taken away
])
def test_the_hash_does_see_any_change_of_value(changed):
    assert _hash(changed) != _hash("a: 1\nb: [x, y]\nc: true\n")


def test_the_hash_is_a_sha256_and_names_its_rule():
    """Hex, 64 characters; and the rule's number is hashed with the rig, so a
    change of rule makes every reference stale on purpose, and says so."""
    digest = _hash("a: 1\n")
    assert len(digest) == 64 and int(digest, 16) >= 0
    assert digest != nulltest.hashlib.sha256(b'{"a":1}').hexdigest()


def test_a_reference_round_trips_the_rig_it_was_made_from():
    fp = _a_fingerprint()
    made = MadeFrom("clean.yaml", "ab" * 32)
    written = yaml.safe_load(yaml.dump(to_dict(fp, nulltest.PROVISIONAL, made), sort_keys=False))
    assert written["made_from"] == {"rig": "clean.yaml", "hash": "ab" * 32, "hash_rule": RIG_HASH_RULE}
    assert made_from_of(written) == made
    assert from_dict(written)[0] == from_dict(to_dict(fp, nulltest.PROVISIONAL))[0], "the fingerprint reads as it did"
    assert "made_from" not in to_dict(fp, nulltest.PROVISIONAL), "a v1 config's or a frozen rig's has none"
    assert made_from_of(to_dict(fp, nulltest.PROVISIONAL)) is None


@pytest.mark.parametrize("made", ["abc", {"rig": "x.yaml"}, {"rig": "x.yaml", "hash": 7, "hash_rule": 1},
                                  {"rig": "x.yaml", "hash": "ab", "hash_rule": "1"}])
def test_a_made_from_that_is_not_one_is_refused(made):
    with pytest.raises(ValueError, match="made_from"):
        made_from_of({"made_from": made})


def test_staleness():
    current = "cd" * 32
    assert staleness(MadeFrom("x.yaml", current), current) is None
    assert "changed since" in staleness(MadeFrom("x.yaml", "ab" * 32), current)
    assert "does not say which version" in staleness(None, current)
    assert f"rule {RIG_HASH_RULE + 1}" in staleness(MadeFrom("x.yaml", current, RIG_HASH_RULE + 1), current)


# --------------------------------------------------------------------------- #
# What any rig render must be
# --------------------------------------------------------------------------- #

def _a_fingerprint(left=1.0, right=1.0):
    sweep = signals.log_sweep(48000, 0.5, level_dbfs=-20.0, channels=2) * np.array([left, right])
    return nulltest.fingerprint(sweep, 48000, config="x", stimulus="s")


def test_a_balanced_render_passes_the_generic_checks():
    assert heard_findings(_a_fingerprint()) == []
    assert heard_findings(_a_fingerprint(1.0, 10 ** (-0.9 / 20))) == [], "0.9 dB apart"


@pytest.mark.parametrize("left, right, check", [
    (0.0, 0.0, "silent"),
    (1.0, 0.0, "one-sided"),
    (0.0, 1.0, "one-sided"),
    (1.0, 10 ** (-(BALANCE_DB + 0.1) / 20), "unbalanced"),
])
def test_silence_one_side_and_imbalance_are_each_found(left, right, check):
    (found,) = heard_findings(_a_fingerprint(left, right))
    assert found.check == check


# --------------------------------------------------------------------------- #
# The command, on a temporary checkout
# --------------------------------------------------------------------------- #

def _checkout(tmp_path, monkeypatch, *, chain=None):
    """A checkout with one committed rig and a Sushi that applies `chain` to
    the audio (a passthrough without one). Returns (main, rig path)."""
    from sushi_rig import cli, paths

    monkeypatch.setattr(paths, "checkout_root", lambda: tmp_path)
    (tmp_path / "plugin-ports.txt").write_text(PORTS.read_text())
    rigs = tmp_path / "config" / "rigs"
    rigs.mkdir(parents=True, exist_ok=True)
    rig = rigs / "clean.yaml"
    if not rig.exists():
        rig.write_text(RIG.format(name="clean"))
    _sushi(monkeypatch, chain)
    return (lambda *argv: cli.main(["nulltest", *argv])), rig


def _sushi(monkeypatch, chain=None):
    def run(argv, **kwargs):
        fed = signals.read_wav(Path(argv[argv.index("-i") + 1]))
        samples = chain(fed.samples) if chain else fed.samples
        signals.write_wav(Path(argv[argv.index("-O") + 1]), samples, fed.sample_rate, subtype="float32")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(render.subprocess, "run", run)


def _left_only(samples):
    return samples * np.array([1.0, 0.0])


def _reference(tmp_path, name="clean"):
    return tmp_path / "config" / "sound" / f"{name}.yaml"


def _prefixed(text, prefix):
    return [line for line in text.splitlines() if line.startswith(prefix + " ")]


def test_a_committed_rig_unchanged_since_its_reference_is_compared_exactly(tmp_path, monkeypatch, capsys):
    """The case that must stay as it was: a rig whose file has not changed
    since its reference was made. Calibrating records the rig; the check
    compares; a render that changed (the code did, not the rig) fails."""
    nulltest_, rig = _checkout(tmp_path, monkeypatch)
    assert nulltest_("--all", "--calibrate") == 0
    made = made_from_of(yaml.safe_load(_reference(tmp_path).read_text()))
    assert made == MadeFrom("clean.yaml", render.rig_file_hash(rig))
    capsys.readouterr()

    assert nulltest_("--all") == 0
    assert "clean.yaml: sounds the same" in capsys.readouterr().out

    _sushi(monkeypatch, lambda samples: samples * 0.5)
    assert nulltest_("--all") == 1, "a change of sound with the rig as it was is the code's, and fails"
    out, err = capsys.readouterr()
    assert "clean.yaml: " in out and "difference(s)" in out and "STALE" not in err


def test_a_rig_rewritten_with_its_numbers_written_differently_is_still_compared_exactly(
        tmp_path, monkeypatch, capsys):
    nulltest_, rig = _checkout(tmp_path, monkeypatch)
    assert nulltest_("--all", "--calibrate") == 0
    rig.write_text("# saved again\n" + rig.read_text().replace("level: 0.0", "level: -0"))
    capsys.readouterr()
    _sushi(monkeypatch, lambda samples: samples * 0.5)
    assert nulltest_("--all") == 1, "still the same rig, so still compared"
    assert "difference(s)" in capsys.readouterr().out


def test_a_changed_rig_is_stale_a_warning_and_gets_a_candidate(tmp_path, monkeypatch, capsys):
    """What #144 is for: a setting changed and pushed. The rig sounds
    different, as it should; the job stays green, names the rig, and hands
    over the reference to commit, made from the rig as it now is."""
    nulltest_, rig = _checkout(tmp_path, monkeypatch)
    assert nulltest_("--all", "--calibrate") == 0
    committed = _reference(tmp_path).read_text()
    rig.write_text(rig.read_text().replace("level: 0.0", "level: -6.0"))
    _sushi(monkeypatch, lambda samples: samples * 0.5)
    capsys.readouterr()

    candidates = tmp_path / "candidates"
    assert nulltest_("--all", "--candidates", str(candidates)) == 0
    out, err = capsys.readouterr()
    assert "clean.yaml: STALE — the rig has changed since its reference was made" in err
    (line,) = _prefixed(err, nulltest.STALE_PREFIX)
    assert line.startswith("STALE-REFERENCE: clean: the rig has changed") and "candidate" in line
    assert "could not be confirmed" not in err
    assert _reference(tmp_path).read_text() == committed, "the committed reference is left alone"

    candidate = yaml.safe_load((candidates / "clean.yaml").read_text())
    assert made_from_of(candidate) == MadeFrom("clean.yaml", render.rig_file_hash(rig))
    (candidates / "clean.yaml").replace(_reference(tmp_path))
    assert nulltest_("--all") == 0, "committed, the candidate is the rig's reference"
    assert "clean.yaml: sounds the same" in capsys.readouterr().out


def test_a_stale_rig_without_candidates_says_how_to_get_its_reference(tmp_path, monkeypatch, capsys):
    nulltest_, rig = _checkout(tmp_path, monkeypatch)
    assert nulltest_("--all", "--calibrate") == 0
    rig.write_text(rig.read_text().replace("level: 0.0", "level: -0.1"))
    capsys.readouterr()
    assert nulltest_("--all") == 0
    err = capsys.readouterr().err
    assert "`sushi-rig nulltest config/rigs/clean.yaml --calibrate`" in err
    assert len(_prefixed(err, nulltest.STALE_PREFIX)) == 1


def test_a_new_rig_with_no_reference_is_a_warning_with_a_candidate(tmp_path, monkeypatch, capsys):
    nulltest_, rig = _checkout(tmp_path, monkeypatch)
    candidates = tmp_path / "candidates"
    assert nulltest_("--all", "--candidates", str(candidates)) == 0
    err = capsys.readouterr().err
    assert "clean.yaml: STALE — it has no reference yet" in err
    assert [p.name for p in candidates.iterdir()] == ["clean.yaml"]
    assert not _reference(tmp_path).exists()


def test_a_reference_from_before_the_rig_was_recorded_is_stale(tmp_path, monkeypatch, capsys):
    """One with no `made_from`: nothing says it is of this rig."""
    nulltest_, _ = _checkout(tmp_path, monkeypatch)
    assert nulltest_("--all", "--calibrate") == 0
    raw = yaml.safe_load(_reference(tmp_path).read_text())
    del raw["made_from"]
    _reference(tmp_path).write_text(yaml.dump(raw, sort_keys=False))
    capsys.readouterr()
    assert nulltest_("--all") == 0
    assert "does not say which version of the rig it was made from" in capsys.readouterr().err


def test_a_reference_whose_made_from_is_broken_fails(tmp_path, monkeypatch, capsys):
    nulltest_, _ = _checkout(tmp_path, monkeypatch)
    assert nulltest_("--all", "--calibrate") == 0
    _reference(tmp_path).write_text(_reference(tmp_path).read_text().replace("hash_rule: 1", "hash_rule: one"))
    capsys.readouterr()
    assert nulltest_("--all") == 1
    assert "clean.yaml: CANNOT CHECK — ValueError: made_from" in capsys.readouterr().err


@pytest.mark.parametrize("calibrated", [True, False], ids=["with its reference", "with none"])
@pytest.mark.parametrize("chain, said", [
    (_left_only, "one-sided: the right side is silent"),
    (lambda samples: samples * np.array([0.0, 1.0]), "one-sided: the left side is silent"),
    (lambda samples: samples * 0.0, "silent: both sides are silent"),
    (lambda samples: samples * np.array([1.0, 0.5]), "unbalanced"),
], ids=["left only", "right only", "silence", "imbalance"])
def test_a_rig_that_renders_silence_or_one_side_fails_whatever_its_reference_says(
        tmp_path, monkeypatch, capsys, calibrated, chain, said):
    nulltest_, rig = _checkout(tmp_path, monkeypatch)
    if calibrated:
        assert nulltest_("--all", "--calibrate") == 0
        rig.write_text(rig.read_text().replace("level: 0.0", "level: -1.0"))  # stale, so only the generic checks
    _sushi(monkeypatch, chain)
    capsys.readouterr()
    assert nulltest_("--all", "--candidates", str(tmp_path / "candidates")) == 1
    out, err = capsys.readouterr()
    assert said in out and "STALE" not in err
    assert not (tmp_path / "candidates").exists() or not any((tmp_path / "candidates").iterdir()), \
        "no candidate for a render no rig should make"


def test_one_side_fails_a_rig_compared_exactly_too(tmp_path, monkeypatch, capsys):
    """A reference made of a one-sided render (as a v1 config's is, on
    purpose) would pass the exact comparison of the same render: the generic
    checks are added to it, so it fails all the same."""
    nulltest_, _ = _checkout(tmp_path, monkeypatch, chain=_left_only)
    with monkeypatch.context() as allowed:
        allowed.setattr(nulltest, "heard_findings", lambda fp: [])  # a reference --calibrate would refuse
        assert nulltest_("--all", "--calibrate") == 0
    capsys.readouterr()
    assert nulltest_("--all") == 1
    out = capsys.readouterr().out
    assert "clean.yaml: 1 difference(s)\n   one-sided: the right side is silent" in out


def test_a_render_that_is_not_finite_fails_a_stale_rig(tmp_path, monkeypatch, capsys):
    def nan(samples):
        samples = samples.copy()
        samples[1000:, :] = np.nan
        return samples

    nulltest_, _ = _checkout(tmp_path, monkeypatch, chain=nan)
    assert nulltest_("--all") == 1
    err = capsys.readouterr().err
    assert "clean.yaml: CANNOT CHECK — RenderError: clean.json rendered unusable audio" in err
    assert "STALE" not in err


def test_calibrating_refuses_a_rig_that_renders_one_side(tmp_path, monkeypatch, capsys):
    nulltest_, _ = _checkout(tmp_path, monkeypatch, chain=_left_only)
    assert nulltest_("--all", "--calibrate") == 1
    assert "clean.yaml: NOT CALIBRATED — one-sided" in capsys.readouterr().err
    assert not _reference(tmp_path).exists()


def test_a_committed_rigs_orphan_is_a_warning(tmp_path, monkeypatch, capsys):
    """A rig or a channel renamed from the page leaves its old reference
    behind: said, every run, and not a failure."""
    nulltest_, _ = _checkout(tmp_path, monkeypatch)
    assert nulltest_("--all", "--calibrate") == 0
    (tmp_path / "config" / "sound" / "renamed-away.board.yaml").write_text(_reference(tmp_path).read_text())
    capsys.readouterr()
    assert nulltest_("--all") == 0
    err = capsys.readouterr().err
    assert "config/sound/renamed-away.board.yaml: ORPHANED REFERENCE (a warning)" in err
    assert _prefixed(err, nulltest.ORPHAN_PREFIX) == ["ORPHANED-REFERENCE: config/sound/renamed-away.board.yaml"]


def test_candidates_and_calibrate_are_one_or_the_other(tmp_path, monkeypatch):
    nulltest_, _ = _checkout(tmp_path, monkeypatch)
    with pytest.raises(SystemExit, match="one or the other"):
        nulltest_("--all", "--calibrate", "--candidates", str(tmp_path / "c"))


# --------------------------------------------------------------------------- #
# The frozen rigs: what pins the sound
# --------------------------------------------------------------------------- #

def _frozen(tmp_path, name="electric-clean"):
    folder = tmp_path / render.FROZEN_RIGS
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{name}.yaml"
    path.write_text(RIG.format(name=name))
    return path


def test_a_frozen_rig_is_checked_against_its_reference_beside_it(tmp_path, monkeypatch, capsys):
    nulltest_, _ = _checkout(tmp_path, monkeypatch)
    _frozen(tmp_path)
    assert nulltest_("--all", "--calibrate") == 0
    ref = tmp_path / render.FROZEN_SOUND / "electric-clean.yaml"
    assert ref.is_file() and made_from_of(yaml.safe_load(ref.read_text())) is None, \
        "a frozen rig is pinned by its checksum, not by a record in its reference"
    capsys.readouterr()
    assert nulltest_("--all") == 0
    assert "frozen electric-clean.yaml: sounds the same" in capsys.readouterr().out


def test_a_change_of_sound_in_a_frozen_rig_fails_by_name(tmp_path, monkeypatch, capsys):
    """A code change or plugin update that alters a frozen rig's sound, while
    the committed one, changed by its owner the same day, only warns."""
    nulltest_, rig = _checkout(tmp_path, monkeypatch)
    _frozen(tmp_path)
    assert nulltest_("--all", "--calibrate") == 0
    rig.write_text(rig.read_text().replace("level: 0.0", "level: -6.0"))
    _sushi(monkeypatch, lambda samples: samples * 0.5)
    capsys.readouterr()
    assert nulltest_("--all") == 1
    out, err = capsys.readouterr()
    assert "frozen electric-clean.yaml: " in out and "difference(s)" in out
    assert "clean.yaml: STALE" in err


def test_a_frozen_rig_with_no_reference_fails(tmp_path, monkeypatch, capsys):
    nulltest_, _ = _checkout(tmp_path, monkeypatch)
    _frozen(tmp_path)
    assert nulltest_("--all") == 1
    err = capsys.readouterr().err
    assert "frozen electric-clean.yaml: NO REFERENCE" in err
    assert "STALE-REFERENCE: clean: it has no reference yet" in err, "the committed one only warns"


def test_a_frozen_reference_nothing_produces_fails(tmp_path, monkeypatch, capsys):
    nulltest_, _ = _checkout(tmp_path, monkeypatch)
    _frozen(tmp_path)
    assert nulltest_("--all", "--calibrate") == 0
    sound = tmp_path / render.FROZEN_SOUND
    (sound / "gone.yaml").write_text((sound / "electric-clean.yaml").read_text())
    capsys.readouterr()
    assert nulltest_("--all") == 1
    err = capsys.readouterr().err
    assert "tool/tests/fixtures/sound/gone.yaml: ORPHANED REFERENCE —" in err
    assert "ORPHANED-REFERENCE:" not in err


def test_a_frozen_rig_given_by_name_is_held_as_frozen(tmp_path, monkeypatch, capsys):
    nulltest_, _ = _checkout(tmp_path, monkeypatch)
    frozen = _frozen(tmp_path)
    assert nulltest_(str(frozen)) == 1
    assert "frozen electric-clean.yaml: NO REFERENCE" in capsys.readouterr().err


def test_the_frozen_folders_are_where_the_tests_keep_them():
    """render.FROZEN_RIGS is rig_fixtures.FROZEN, from the checkout's root."""
    from rig_fixtures import FIXTURES, FROZEN

    from sushi_rig.paths import checkout_root

    assert checkout_root() / render.FROZEN_RIGS == FROZEN
    assert checkout_root() / render.FROZEN_SOUND == FIXTURES / "sound"


def test_a_stale_rig_and_the_warning_lines_name_each_channel(tmp_path, monkeypatch, capsys):
    """A rig of two channels changed: one warning line per channel, so the
    annotation says which references to commit."""
    nulltest_, rig = _checkout(tmp_path, monkeypatch)
    rig.write_text(RIG.format(name="clean").replace(
        "master:", "- {name: vocal, input: vocal-in, width: mono, chain: []}\nmaster:"))
    candidates = tmp_path / "candidates"
    assert nulltest_("--all", "--candidates", str(candidates)) == 0
    err = capsys.readouterr().err
    assert [line.split(":")[1].strip() for line in _prefixed(err, nulltest.STALE_PREFIX)] == [
        "clean.board", "clean.vocal"]
    assert sorted(p.name for p in candidates.iterdir()) == ["clean.board.yaml", "clean.vocal.yaml"]
    assert json.loads(json.dumps(yaml.safe_load((candidates / "clean.vocal.yaml").read_text())))["made_from"][
        "rig"] == "clean.yaml"


def test_every_frozen_rigs_channel_has_a_reference_and_nothing_else_is_there():
    """The frozen references are the frozen rigs' channels, one each, every
    one a reference this tool reads, and none recording a rig it was made
    from: a frozen rig is pinned by its checksum (test_rig_fixtures.py)."""
    from rig_fixtures import FROZEN_SOUND, compiled_to_render, frozen, frozen_names

    from sushi_rig import rigfile

    channels = sorted(name for rig in frozen_names() for name, _ in rigfile.heard(compiled_to_render(frozen(rig))))
    assert channels == sorted(path.stem for path in FROZEN_SOUND.glob("*.yaml"))
    for name in channels:
        raw = yaml.safe_load((FROZEN_SOUND / f"{name}.yaml").read_text())
        fingerprint, _ = from_dict(raw)
        assert fingerprint.config == name and made_from_of(raw) is None
        assert heard_findings(fingerprint) == [], f"{name}: a frozen reference any rig would fail"


# --------------------------------------------------------------------------- #
# The container job's warning, run as the job runs it
# --------------------------------------------------------------------------- #

WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "rig-container.yml"


def _warning_script() -> str:
    """The part of the job's nulltest step that turns nulltest's warning
    lines into annotations: from its count of STALE lines to the check of
    nulltest's own status, as the workflow holds it."""
    step = next(s for s in yaml.safe_load(WORKFLOW.read_text())["jobs"]["slow"]["steps"]
                if s.get("id") == "nulltest")["run"]
    start = step.index('stale="$(grep -c')
    return step[start:step.index('if [ "$status" != 0 ]', start)]


def test_the_job_warns_naming_each_stale_channel_with_its_candidate_and_each_orphan(tmp_path, monkeypatch, capsys):
    """nulltest's own output, from a stale rig of two channels and an orphan,
    put through the workflow's shell: a warning that names both channels and
    carries both candidates, and one for the orphan. Not an error."""
    import shutil

    bash = shutil.which("bash")
    if bash is None:  # every runner and the container have it; said, not skipped
        raise AssertionError("bash is needed to run the workflow's own shell")
    nulltest_, rig = _checkout(tmp_path, monkeypatch)
    rig.write_text(RIG.format(name="clean").replace(
        "master:", "- {name: vocal, input: vocal-in, width: mono, chain: []}\nmaster:"))
    sound = tmp_path / "config" / "sound"
    sound.mkdir(parents=True)
    job = tmp_path / "job"
    (job / "stale-candidates").mkdir(parents=True)
    assert nulltest_("--all", "--candidates", str(job / "stale-candidates")) == 0
    (sound / "old-name.yaml").write_text((job / "stale-candidates" / "clean.board.yaml").read_text())
    capsys.readouterr()
    assert nulltest_("--all", "--candidates", str(job / "stale-candidates")) == 0
    out, err = capsys.readouterr()
    (job / "nulltest.log").write_text(out + err)

    ran = RUN([bash, "-c", "set -o pipefail\n" + _warning_script()], cwd=job,
                         capture_output=True, text=True, check=True)
    annotations = [line for line in ran.stdout.splitlines() if line.startswith("::")]
    assert len(annotations) == 2, ran.stdout
    stale, orphan = annotations
    assert stale.startswith("::warning title=2 rig channel(s) STALE, held to the generic checks only: "
                            "clean.board clean.vocal ::")
    assert "===== BEGIN config/sound/clean.vocal.yaml =====" in stale and "made_from:" in stale
    assert orphan.startswith("::warning title=1 orphaned reference(s) of committed rigs::")
    assert "config/sound/old-name.yaml" in orphan
    assert "::error" not in ran.stdout
