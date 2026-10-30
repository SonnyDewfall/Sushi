"""Rendering a config offline, for the null tests.

The command line, the stimulus and the failure handling are decisions, so they
are pinned here. Actually starting Sushi is the effect and is verified by running
it in the CI container, which has a real Sushi and the rig's plugins.

`subprocess.run` is monkeypatched rather than mocked wholesale, so what is being
checked is the argv and environment this module really builds.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

np = pytest.importorskip("numpy")

from sushi_rig import render, signals  # noqa: E402
from sushi_rig.render import RenderError, Stimulus, sushi_argv  # noqa: E402

SR = 48000


# --- the command line -------------------------------------------------------


def test_the_offline_frontend_is_selected():
    """`-o` is what makes this possible without audio hardware. Without it Sushi
    waits for a JACK server that CI does not have."""
    argv = sushi_argv(Path("config/x.json"), Path("/t/in.wav"), Path("/t/out.wav"))
    assert "-o" in argv
    assert argv[0] == "./sushi", "the rig keeps its own Sushi, not one on PATH"


def test_the_input_and_output_flags_differ_in_case():
    """Sushi's own asymmetry: lowercase -i in, uppercase -O out. Easy to 'tidy'
    into matching and then wonder why nothing is written."""
    argv = sushi_argv(Path("c.json"), Path("in.wav"), Path("out.wav"))
    assert argv[argv.index("-i") + 1] == "in.wav"
    assert argv[argv.index("-O") + 1] == "out.wav"


# --- the stimulus -----------------------------------------------------------


def test_the_stimulus_describes_itself_for_the_reference():
    """The description is a reference's identity. `nulltest.compare` refuses two
    fingerprints whose descriptions differ, so it has to carry everything that
    changes the audio."""
    described = Stimulus().describe()
    for part in ("log_sweep", "20-20000Hz", "5s", "-20dBFS", "2ch"):
        assert part in described, f"{part!r} missing from {described!r}"


def test_changing_any_parameter_changes_the_description():
    """Otherwise a reference could be compared against a different stimulus and
    pass — the failure the description exists to prevent."""
    base = Stimulus()
    for changed in (
        Stimulus(duration_s=10.0),
        Stimulus(level_dbfs=-6.0),
        Stimulus(f_start_hz=40.0),
        Stimulus(f_end_hz=16000.0),
        Stimulus(channels=1),
    ):
        assert changed.describe() != base.describe()


def test_the_stimulus_regenerates_identically():
    """Why no WAV is committed: the signal is deterministic, so a reference
    records the description and the renderer rebuilds the audio."""
    first = Stimulus().render(SR)
    assert np.array_equal(first, Stimulus().render(SR))
    assert first.shape == (SR * 5, 2)


def test_an_unknown_stimulus_kind_is_refused_rather_than_guessed():
    with pytest.raises(RenderError, match="log_sweep"):
        Stimulus(kind="white_noise").render(SR)


def test_a_sine_stimulus_describes_itself_by_its_own_frequency():
    """A sine uses neither sweep endpoint, so neither appears in its identity —
    two sines that sound the same must not compare as different."""
    described = Stimulus(kind="sine", frequency_hz=220.0, duration_s=1.0).describe()
    for part in ("sine", "220Hz", "1s", "-20dBFS", "2ch"):
        assert part in described, f"{part!r} missing from {described!r}"
    assert "20-20000Hz" not in described
    assert Stimulus(kind="sine", frequency_hz=440.0).describe() != described


def test_a_sine_renders_at_the_frequency_it_names():
    rendered = Stimulus(kind="sine", frequency_hz=220.0, duration_s=1.0).render(SR)
    assert rendered.shape == (SR, 2)
    from sushi_rig import analysis

    assert analysis.harmonic_distortion(
        rendered[:, 0], SR, 220.0
    ).fundamental_dbfs == pytest.approx(-23.01, abs=0.01), "peak -20 dBFS is -23.01 RMS"


def test_a_sine_without_a_frequency_is_refused_rather_than_defaulted():
    """Defaulting it would put a tone nobody chose into a reference's identity."""
    with pytest.raises(RenderError, match="needs frequency_hz"):
        Stimulus(kind="sine").render(SR)


def test_an_incomplete_sine_is_refused_by_its_description_too():
    """A reference's identity is sometimes asked for without rendering anything,
    and `:g` on None raises a TypeError that says nothing about stimuli."""
    with pytest.raises(RenderError, match="needs frequency_hz"):
        Stimulus(kind="sine").describe()


# --- rendering --------------------------------------------------------------


def _fake_sushi(tmp_path, *, returncode=0, write_output=True, out_rate=SR, frames=240000):
    """Stand in for Sushi: write a plausible output file, or fail like it would."""
    def run(argv, **kwargs):
        if write_output:
            out = Path(argv[argv.index("-O") + 1])
            signals.write_wav(out, np.zeros((frames, 2)), out_rate, subtype="float32")
        return SimpleNamespace(returncode=returncode, stdout="", stderr="")
    return run


def test_a_render_comes_back_with_everything_the_fingerprint_needs(tmp_path, monkeypatch):
    monkeypatch.setattr(render.subprocess, "run", _fake_sushi(tmp_path))
    out = render.render_offline(
        Path("config/x.json"), tmp_path, tmp_path / "work",
        stimulus=Stimulus(duration_s=1.0), sample_rate=SR,
    )
    assert out["rendered"].shape[0] == 240000
    assert out["sample_rate"] == SR
    assert out["channels"] == 2
    assert "log_sweep" in out["stimulus"]
    assert out["stimulus_samples"].shape == (SR, 2), "the input, for measuring delay"


def test_the_render_runs_with_the_rigs_lv2_path(tmp_path, monkeypatch):
    """The #47 bug, in a new place: a render that inherits the caller's LV2_PATH
    passes on a machine whose shell exports it and fails with "Failed to load
    tracks" on a clean one."""
    monkeypatch.delenv("LV2_PATH", raising=False)
    seen = {}

    def run(argv, **kwargs):
        seen.update(kwargs)
        out = Path(argv[argv.index("-O") + 1])
        signals.write_wav(out, np.zeros((100, 2)), SR, subtype="float32")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(render.subprocess, "run", run)
    render.render_offline(
        Path("config/x.json"), tmp_path, tmp_path / "work",
        stimulus=Stimulus(duration_s=0.1),
    )
    assert seen["env"]["LV2_PATH"] == str(tmp_path / "plugins")
    assert seen["cwd"] == tmp_path, "paths in a config are relative to the rig root"


def test_a_nonzero_exit_reports_both_streams(tmp_path, monkeypatch):
    """The LV2_PATH failure prints on stdout, and reporting only stderr is what
    turned it into a blank error message once before."""
    def run(argv, **kwargs):
        return SimpleNamespace(
            returncode=4, stdout="Failed to load tracks from the Json config file",
            stderr="",
        )

    monkeypatch.setattr(render.subprocess, "run", run)
    with pytest.raises(RenderError, match="Failed to load tracks"):
        render.render_offline(
            Path("config/x.json"), tmp_path, tmp_path / "work",
            stimulus=Stimulus(duration_s=0.1),
        )


def test_exiting_zero_without_writing_anything_is_an_error(tmp_path, monkeypatch):
    """A silent success is the worst outcome here: no output file would be
    fingerprinted as a change in the rig's sound."""
    monkeypatch.setattr(
        render.subprocess, "run", _fake_sushi(tmp_path, write_output=False)
    )
    with pytest.raises(RenderError, match="wrote no"):
        render.render_offline(
            Path("config/x.json"), tmp_path, tmp_path / "work",
            stimulus=Stimulus(duration_s=0.1),
        )


def test_a_rate_change_is_refused_rather_than_fingerprinted(tmp_path, monkeypatch):
    """Every band moves if the rate does, so this would read as the whole tone
    having changed rather than as a rate problem."""
    monkeypatch.setattr(
        render.subprocess, "run", _fake_sushi(tmp_path, out_rate=44100)
    )
    with pytest.raises(RenderError, match="44100"):
        render.render_offline(
            Path("config/x.json"), tmp_path, tmp_path / "work",
            stimulus=Stimulus(duration_s=0.1),
        )


def test_a_missing_sushi_says_where_the_rig_keeps_its_own(tmp_path, monkeypatch):
    def run(argv, **kwargs):
        raise FileNotFoundError(argv[0])

    monkeypatch.setattr(render.subprocess, "run", run)
    with pytest.raises(RenderError, match="./sushi"):
        render.render_offline(
            Path("config/x.json"), tmp_path, tmp_path / "work",
            stimulus=Stimulus(duration_s=0.1),
        )


def test_a_hung_render_times_out_rather_than_blocking_ci(tmp_path, monkeypatch):
    import subprocess as sp

    def run(argv, **kwargs):
        raise sp.TimeoutExpired(argv, kwargs.get("timeout", 1))

    monkeypatch.setattr(render.subprocess, "run", run)
    with pytest.raises(RenderError, match="did not finish"):
        render.render_offline(
            Path("config/x.json"), tmp_path, tmp_path / "work",
            stimulus=Stimulus(duration_s=0.1), timeout=1.0,
        )


# --- the two halves fit together --------------------------------------------


def test_a_render_fingerprints_and_compares_against_itself(tmp_path, monkeypatch):
    """The end-to-end shape, with Sushi standing in: render, fingerprint, compare.
    Proves the keys this module returns are the ones nulltest expects, which a
    test of either half alone would not."""
    from sushi_rig.nulltest import compare, fingerprint

    def run(argv, **kwargs):
        # A "chain" that passes audio through unchanged.
        fed = signals.read_wav(Path(argv[argv.index("-i") + 1]))
        signals.write_wav(
            Path(argv[argv.index("-O") + 1]), fed.samples, fed.sample_rate,
            subtype="float32",
        )
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(render.subprocess, "run", run)
    out = render.render_offline(
        Path("config/x.json"), tmp_path, tmp_path / "work",
        stimulus=Stimulus(duration_s=1.0),
    )

    fp = fingerprint(
        out["rendered"], out["sample_rate"],
        config="x", stimulus=out["stimulus"],
        stimulus_samples=out["stimulus_samples"],
    )
    assert compare(fp, fp) == []
    assert fp.delay_samples == 0, "a passthrough should measure no delay"


# --- the workflow -----------------------------------------------------------


def test_a_reference_lives_beside_the_config_it_describes(tmp_path):
    """Under config/, not a directory of its own: a reference describes one tone
    and should travel with it, including to the library repo when configs move."""
    from sushi_rig.render import reference_path

    path = reference_path(tmp_path, Path("config/electric-clean.json"))
    assert path == tmp_path / "config" / "sound" / "electric-clean.yaml"


def test_calibrate_renders_twice_and_measures_the_gap(tmp_path, monkeypatch):
    """A config that differs from itself must produce a non-zero tolerance, or
    every later run reports its own non-determinism as a change in sound."""
    from sushi_rig.render import calibrate

    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        fed = signals.read_wav(Path(argv[argv.index("-i") + 1]))
        # Second render 0.2 dB louder: a chorus differing from itself.
        gain = 1.0 if len(calls) == 1 else 10 ** (0.2 / 20)
        signals.write_wav(
            Path(argv[argv.index("-O") + 1]), fed.samples * gain,
            fed.sample_rate, subtype="float32",
        )
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(render.subprocess, "run", run)
    fp, tolerance = calibrate(
        Path("config/x.json"), tmp_path, tmp_path / "work",
        stimulus=Stimulus(duration_s=1.0),
    )

    assert len(calls) == 2, "the floor is the gap between two runs"
    assert tolerance.level_db == pytest.approx(0.2, abs=0.01)
    assert "measured" in tolerance.note
    assert fp.config == "x"


def test_a_deterministic_config_calibrates_to_the_tightest_tolerance_the_file_holds(
    tmp_path, monkeypatch
):
    """Most of the rig is deterministic, and its tolerance should say so rather
    than carry slack that would hide a real change. Not *zero*, though: a
    reference writes its levels to four decimals, so a tolerance below that
    resolution fails the render it was calibrated from."""
    from sushi_rig.render import calibrate

    def run(argv, **kwargs):
        fed = signals.read_wav(Path(argv[argv.index("-i") + 1]))
        signals.write_wav(
            Path(argv[argv.index("-O") + 1]), fed.samples, fed.sample_rate,
            subtype="float32",
        )
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(render.subprocess, "run", run)
    _, tolerance = calibrate(
        Path("config/x.json"), tmp_path, tmp_path / "work",
        stimulus=Stimulus(duration_s=1.0),
    )
    from sushi_rig.nulltest import WRITTEN_RESOLUTION_DB

    assert tolerance.band_db == WRITTEN_RESOLUTION_DB
    assert tolerance.level_db == WRITTEN_RESOLUTION_DB
    assert tolerance.delay_samples == 0
    assert "identical to the 0.0001 dB this file is written at" in tolerance.note


# --- the command, end to end ------------------------------------------------
#
# What the first round of tests got wrong: `calibrate` was exercised in memory
# only, so nothing round-tripped a reference through YAML and the CLI -- and a
# calibrated reference failed its own check on the very next run. These go
# through `cli.main` with a passthrough standing in for Sushi.


def _rig(tmp_path, monkeypatch, *, chain=None):
    """A rig root with one config, and a Sushi that applies `chain` to the audio."""
    from sushi_rig import cli, paths

    monkeypatch.setattr(paths, "checkout_root", lambda: tmp_path)
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    config = tmp_path / "config" / "electric-clean.json"
    config.write_text("{}")

    def run(argv, **kwargs):
        fed = signals.read_wav(Path(argv[argv.index("-i") + 1]))
        samples = chain(fed.samples) if chain else fed.samples
        signals.write_wav(
            Path(argv[argv.index("-O") + 1]), samples, fed.sample_rate,
            subtype="float32",
        )
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(render.subprocess, "run", run)
    return cli, config


def test_a_calibrated_reference_passes_its_own_check(tmp_path, monkeypatch, capsys):
    """The bug this closes: `--calibrate` wrote a tolerance of 0.0 for a
    deterministic config, and the next run compared the reference's 4-decimal
    levels against an unrounded measurement and reported eight differences of
    +0.000 dB. A reference that fails the render it was written from makes the
    whole check worthless, and every in-memory test of `calibrate` passed."""
    cli, config = _rig(tmp_path, monkeypatch)

    assert cli.main(["nulltest", str(config), "--calibrate"]) == 0
    assert "calibrated" in capsys.readouterr().out

    assert cli.main(["nulltest", str(config)]) == 0
    assert "sounds the same" in capsys.readouterr().out


def test_a_calibrated_reference_still_catches_a_real_change(tmp_path, monkeypatch, capsys):
    """The other half: the floor must not have been widened into blindness. A
    6 dB drop is the cheapest real regression there is."""
    cli, config = _rig(tmp_path, monkeypatch)
    assert cli.main(["nulltest", str(config), "--calibrate"]) == 0
    capsys.readouterr()

    _rig(tmp_path, monkeypatch, chain=lambda samples: samples * 0.5)
    assert cli.main(["nulltest", str(config)]) == 1
    assert "difference(s)" in capsys.readouterr().out


def test_the_no_reference_hint_names_a_path_the_command_can_find(tmp_path, monkeypatch, capsys):
    """`sushi-rig nulltest electric-clean.json` finds nothing: the render runs
    from the rig root, so the hint has to say `config/electric-clean.json`."""
    cli, config = _rig(tmp_path, monkeypatch)
    assert cli.main(["nulltest", str(config)]) == 1
    assert "nulltest config/electric-clean.json --calibrate" in capsys.readouterr().err


@pytest.mark.parametrize(
    "written, expected",
    [
        ("", "AttributeError"),       # yaml.safe_load returns None
        ("schema_version: 1\n", "KeyError"),
        ("[not, a, mapping]", "AttributeError"),
        ("schema_version: 1\nmeasured: {", "ParserError"),  # a yaml.YAMLError
    ],
)
def test_a_reference_that_cannot_be_read_is_reported_not_raised(
    tmp_path, monkeypatch, capsys, written, expected
):
    """These files are committed and hand-edited, so a half-written one is a
    matter of time. A traceback would also abandon the remaining configs under
    `--all`, turning one bad file into no check at all."""
    cli, config = _rig(tmp_path, monkeypatch)
    reference = render.reference_path(tmp_path, config)
    reference.parent.mkdir(parents=True, exist_ok=True)
    reference.write_text(written)

    assert cli.main(["nulltest", str(config)]) == 1
    assert f"CANNOT CHECK — {expected}" in capsys.readouterr().err


def test_a_render_that_is_not_finite_is_refused_before_it_is_fingerprinted(
    tmp_path, monkeypatch
):
    """`render_findings` was written for this step and had no caller outside its
    own tests. Without it a NaN render died inside `estimate_delay` with "no
    correlation between the signals", which describes the measurement and not
    the chain — and `--calibrate` would have written it into a reference."""
    def all_nan(samples):
        return samples * np.nan

    _rig(tmp_path, monkeypatch, chain=all_nan)
    with pytest.raises(RenderError, match="unusable audio"):
        render.measure(
            Path("config/x.json"), tmp_path, tmp_path / "work",
            stimulus=Stimulus(duration_s=0.2),
        )


# --- what Sushi's offline frontend can host --------------------------------
#
# Settled from Sushi 1.3.0's source (src/audio_frontends/offline_frontend.*)
# after CI rendered two configs at two channel counts: the offline engine has
# exactly two channels, and a file wider than that overflows a stack buffer.

ROOT = Path(__file__).parent.parent.parent
BUS_ONE = {"tracks": [{"name": "vocal", "inputs": [{"engine_bus": 1, "track_bus": 0}],
                       "outputs": [{"engine_bus": 0, "track_bus": 0}]}]}


def test_guitar_vocal_cannot_be_rendered_offline_and_says_why():
    """Its vocal track listens on engine bus 1, and the offline engine is bus 0
    alone. CI saw "Failed to load tracks" at two channels and at four."""
    import json

    problem = render.offline_problem(json.loads((ROOT / "config" / "guitar_vocal.json").read_text()))
    assert problem and "'vocal'" in problem and "engine bus 1" in problem


def test_guitar_vocal_is_the_only_config_in_the_repository_that_cannot_be():
    """Pinned as a set, not by one example each way: a regression that wrongly
    flagged electric-delay would otherwise pass, and quietly drop its
    protection."""
    import json

    unrenderable = {
        c.name for c in (ROOT / "config").glob("*.json")
        if render.offline_problem(json.loads(c.read_text())) is not None
    }
    assert unrenderable == {"guitar_vocal.json"}


@pytest.mark.parametrize("config, renderable", [
    ({"tracks": []}, True),
    ({"tracks": [{"inputs": [{"engine_bus": 0}], "outputs": [{"engine_bus": 0}]}]}, True),
    ({"tracks": [{"outputs": [{"engine_bus": 1}]}]}, False),
    ({"tracks": [{"inputs": [{"engine_channel": 0}, {"engine_channel": 1}]}]}, True),
    ({"tracks": [{"inputs": [{"engine_channel": 2}]}]}, False),
    ({"tracks": [{"outputs": [{"engine_channel": 3}]}]}, False),
    ({"tracks": [{"inputs": [{"engine_bus": True}]}]}, True),
    ({"tracks": ["not a track", {"inputs": "nor these"}]}, True),
])
def test_offline_problem_looks_at_both_routing_forms_on_both_sides(config, renderable):
    """The rig routes by `engine_bus` or by `engine_channel` (spec.py,
    patchbay.routes_from_rig), and only the first was checked: a track on
    channel 2 passed as renderable. `True` is an int in Python and must not
    read as bus 1; malformed indices are left for verify, which runs first."""
    assert (render.offline_problem(config) is None) is renderable


def test_a_stimulus_wider_than_the_frontend_is_refused_before_sushi_sees_it(
    tmp_path, monkeypatch
):
    """Sushi does not refuse it: it reads the file into a two-channel stack
    buffer and checks only for mono, so CI saw "*** stack smashing detected ***"
    from a four-channel stimulus on a config that uses bus 0 alone."""
    def run(*args, **kwargs):
        raise AssertionError("Sushi must not be started with this stimulus")

    monkeypatch.setattr(render.subprocess, "run", run)
    with pytest.raises(RenderError, match="overflow"):
        render.render_offline(
            Path("config/x.json"), tmp_path, tmp_path / "work",
            stimulus=Stimulus(channels=4, duration_s=0.1),
        )


def test_an_unrenderable_config_is_reported_every_run_without_failing_it(
    tmp_path, monkeypatch, capsys
):
    """Loud, and not a failure: there is nothing to fix in the config, and a
    check that is red for ever is a check people stop reading."""
    import json

    cli, config = _rig(tmp_path, monkeypatch)
    config.write_text(json.dumps(BUS_ONE))
    assert cli.main(["nulltest", str(config)]) == 0
    err = capsys.readouterr().err
    assert "NOT RENDERABLE OFFLINE" in err and "engine bus 1" in err
    assert "sound is not protected: electric-clean.json" in err


def test_calibrating_an_unrenderable_config_writes_no_reference(tmp_path, monkeypatch, capsys):
    import json

    cli, config = _rig(tmp_path, monkeypatch)
    config.write_text(json.dumps(BUS_ONE))
    assert cli.main(["nulltest", str(config), "--calibrate"]) == 0
    assert not render.reference_path(tmp_path, config).exists()
    assert "NOT RENDERABLE OFFLINE" in capsys.readouterr().err


def test_a_config_that_had_a_reference_and_became_unrenderable_fails(
    tmp_path, monkeypatch, capsys
):
    """The hole a derived exemption opens, closed: moving a track to bus 1 by
    mistake must not quietly turn a protected config into an unprotected one."""
    import json

    cli, config = _rig(tmp_path, monkeypatch)
    assert cli.main(["nulltest", str(config), "--calibrate"]) == 0
    config.write_text(json.dumps(BUS_ONE))
    assert cli.main(["nulltest", str(config)]) == 1
    err = capsys.readouterr().err
    assert "NO LONGER RENDERABLE" in err
    assert "delete config/sound/electric-clean.yaml" in err, "say how to resolve it"


def test_recalibrating_a_config_that_became_unrenderable_fails_rather_than_lying(
    tmp_path, monkeypatch, capsys
):
    """It used to print NOT RENDERABLE, exit 0, and leave the stale reference
    for the next run to fail on — so accepting the change looked accepted."""
    import json

    cli, config = _rig(tmp_path, monkeypatch)
    assert cli.main(["nulltest", str(config), "--calibrate"]) == 0
    config.write_text(json.dumps(BUS_ONE))
    assert cli.main(["nulltest", str(config), "--calibrate"]) == 1
    assert "NO LONGER RENDERABLE" in capsys.readouterr().err


def test_a_recalibration_keeps_a_tolerance_that_was_set_wider_by_hand(
    tmp_path, monkeypatch, capsys
):
    """What committing CI's candidates used to do to the two non-deterministic
    configs: put their hand-widened 0.01 dB back to one calibration's floor,
    and with it the failures at random that the widening was for."""
    import yaml
    from sushi_rig.nulltest import Tolerance, from_dict, to_dict

    cli, config = _rig(tmp_path, monkeypatch)
    assert cli.main(["nulltest", str(config), "--calibrate"]) == 0
    ref = render.reference_path(tmp_path, config)
    fp, _ = from_dict(yaml.safe_load(ref.read_text()))
    ref.write_text(yaml.dump(to_dict(fp, Tolerance(0.01, 0.01, 0, "set by hand")),
                             sort_keys=False))

    assert cli.main(["nulltest", str(config), "--calibrate"]) == 0
    _, kept = from_dict(yaml.safe_load(ref.read_text()))
    assert (kept.band_db, kept.level_db, kept.note) == (0.01, 0.01, "set by hand")
    assert "kept the committed tolerance" in capsys.readouterr().err
