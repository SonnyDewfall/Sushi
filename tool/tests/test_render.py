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
