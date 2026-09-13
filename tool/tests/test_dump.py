import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from sushi_rig.dump import collect_parameter_info, collect_parameter_names, dump_plugins


def _fake_run(stdout: str, returncode: int = 0, stderr: str = ""):
    def run(*args, **kwargs):
        return subprocess.CompletedProcess(args, returncode, stdout=stdout, stderr=stderr)

    return run


def test_dump_plugins_parses_trailing_text_after_json():
    """Sushi 1.3.0 prints 'Parameter dump completed - exiting.' after the JSON
    on stdout — a plain json.loads raises Extra data on this. This is the
    exact break found in the original prototype."""
    stdout = '{"plugins": [{"name": "x", "parameters": []}]}Parameter dump completed - exiting.\n'
    with patch("subprocess.run", _fake_run(stdout)):
        result = dump_plugins(Path("irrelevant.json"))
    assert result == {"plugins": [{"name": "x", "parameters": []}]}


def test_dump_plugins_handles_leading_log_lines():
    stdout = 'some log line\nanother log line\n{"plugins": []}\n'
    with patch("subprocess.run", _fake_run(stdout)):
        result = dump_plugins(Path("irrelevant.json"))
    assert result == {"plugins": []}


def test_dump_plugins_exits_on_nonzero_returncode():
    with patch("subprocess.run", _fake_run("", returncode=4, stderr="boom")):
        with pytest.raises(SystemExit):
            dump_plugins(Path("irrelevant.json"))


def test_dump_plugins_exits_on_unparseable_output():
    with patch("subprocess.run", _fake_run("no json here at all")):
        with pytest.raises(SystemExit):
            dump_plugins(Path("irrelevant.json"))


def test_collect_parameter_names_generic_walk_on_real_dump(real_dump):
    found = collect_parameter_names(real_dump)
    assert set(found) == {"compressor_mono", "graph_equalizer_x16_stereo", "internal_reverb"}
    assert "Ratio" in found["compressor_mono"]
    assert "Attack threshold" in found["compressor_mono"]
    assert "room_size" in found["internal_reverb"]


def test_collect_parameter_names_holds_up_against_differently_nested_shape():
    """The dump's exact structure has shifted between Sushi versions (brief
    §2.7) — the walk should not depend on plugins living under a fixed key."""
    synthetic = {
        "tracks": [
            {
                "name": "some_track",
                "processors": [
                    {"name": "proc_a", "parameters": [{"name": "Gain"}, {"name": "Mix"}]}
                ],
            }
        ]
    }
    found = collect_parameter_names(synthetic)
    assert found == {"proc_a": {"Gain", "Mix"}}


def test_collect_parameter_info_keeps_osc_path(real_dump):
    info = collect_parameter_info(real_dump)
    param = info["compressor_mono"]["Show pre-mix overlay"]
    assert param["osc_path"] == "/parameter/compressor_mono/Show_pre-mix_overlay"


def test_collect_parameter_info_every_param_has_osc_path(real_dump):
    info = collect_parameter_info(real_dump)
    for processor, params in info.items():
        for name, record in params.items():
            assert "osc_path" in record, f"{processor}.{name} missing osc_path"
