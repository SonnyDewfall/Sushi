import textwrap
from pathlib import Path

from sushi_rig.spec import PluginSpec, RigSpec, TrackSpec


def _write(tmp_path: Path, yaml_text: str) -> Path:
    p = tmp_path / "rig.yaml"
    p.write_text(textwrap.dedent(yaml_text))
    return p


def test_load_minimal_rig(tmp_path):
    path = _write(
        tmp_path,
        """
        host:
          samplerate: 48000
        tracks:
          - name: gtr_in
            channels: 2
            inputs:
              - engine_bus: 0
                track_bus: 0
            outputs:
              - engine_bus: 0
                track_bus: 0
            plugins:
              - name: gtr_comp
                type: lv2
                uri: http://calf.sourceforge.net/plugins/Compressor
        """,
    )
    rig = RigSpec.load(path)
    assert rig.host == {"samplerate": 48000}
    assert len(rig.tracks) == 1
    assert rig.tracks[0].plugins[0].uri == "http://calf.sourceforge.net/plugins/Compressor"
    assert rig.validate() == []


def test_validate_reports_all_duplicate_names_at_once():
    """At rig scale, the user wants every collision reported in one pass, not
    one fix cycle per problem — validate() returns a list, never raises."""
    rig = RigSpec(
        tracks=[
            TrackSpec(name="dup", plugins=[PluginSpec(name="dup", uri="u")]),
            TrackSpec(name="track2", plugins=[PluginSpec(name="track2", uri="u")]),
        ]
    )
    problems = rig.validate()
    assert len(problems) == 2
    assert any("dup" in p for p in problems)
    assert any("track2" in p for p in problems)


def test_validate_missing_uri_on_lv2_plugin():
    rig = RigSpec(tracks=[TrackSpec(name="t", plugins=[PluginSpec(name="p", type="lv2")])])
    problems = rig.validate()
    assert len(problems) == 1
    assert "p" in problems[0] and "uri" in problems[0]


def test_validate_vst_requires_path_or_uid():
    rig = RigSpec(tracks=[TrackSpec(name="t", plugins=[PluginSpec(name="p", type="vst2x")])])
    problems = rig.validate()
    assert len(problems) == 1
    assert "path" in problems[0] or "uid" in problems[0]


def test_validate_vst_with_path_is_fine():
    rig = RigSpec(
        tracks=[TrackSpec(name="t", plugins=[PluginSpec(name="p", type="vst2x", path="/x.so")])]
    )
    assert rig.validate() == []


def test_validate_multibus_without_buses():
    rig = RigSpec(tracks=[TrackSpec(name="t", channels=2, multibus=True, buses=None)])
    problems = rig.validate()
    assert any("buses" in p for p in problems)


def test_validate_multibus_requires_even_channels():
    rig = RigSpec(tracks=[TrackSpec(name="t", channels=3, multibus=True, buses=1)])
    problems = rig.validate()
    assert any("even" in p for p in problems)


def test_validate_mixed_bus_and_channel_routing_forms():
    rig = RigSpec(
        tracks=[
            TrackSpec(
                name="t",
                inputs=[{"engine_bus": 0, "track_channel": 1}],
            )
        ]
    )
    problems = rig.validate()
    assert any("mixes bus and channel" in p for p in problems)


def test_validate_routing_entry_with_neither_form():
    rig = RigSpec(tracks=[TrackSpec(name="t", inputs=[{}])])
    problems = rig.validate()
    assert any("neither" in p for p in problems)


def test_validate_clean_routing_forms_pass():
    rig = RigSpec(
        tracks=[
            TrackSpec(
                name="t",
                inputs=[{"engine_bus": 0, "track_bus": 0}],
                outputs=[{"engine_channel": 0, "track_channel": 0}],
            )
        ]
    )
    assert rig.validate() == []


def test_validate_unknown_plugin_type():
    rig = RigSpec(tracks=[TrackSpec(name="t", plugins=[PluginSpec(name="p", type="bogus")])])
    problems = rig.validate()
    assert any("bogus" in p for p in problems)


def test_plugin_to_sushi_lv2():
    p = PluginSpec(name="comp", type="lv2", uri="http://example.org/comp")
    assert p.to_sushi() == {"name": "comp", "type": "lv2", "uri": "http://example.org/comp"}


def test_track_to_sushi_multibus():
    t = TrackSpec(name="t", channels=4, multibus=True, buses=2)
    sushi = t.to_sushi()
    assert sushi["multibus"] is True
    assert sushi["buses"] == 2
    assert sushi["channels"] == 4
