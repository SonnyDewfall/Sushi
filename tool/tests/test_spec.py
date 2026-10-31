import pytest
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


# --- source / sink: the roles that sit below a track (issue #54) ---------------


def test_source_and_sink_are_optional_so_existing_rigs_load_unchanged(tmp_path):
    rig = RigSpec.load(_write(tmp_path, """
        tracks:
          - name: t
            inputs: [{engine_bus: 0, track_bus: 0}]
            outputs: [{engine_bus: 0, track_bus: 0}]
    """))
    assert rig.tracks[0].source is None and rig.tracks[0].sink is None
    assert rig.validate() == []


def test_every_committed_rig_source_still_loads_and_validates():
    """There are live configs built from these; none may need touching to take
    the new fields."""
    from sushi_rig.paths import checkout_root

    sources = sorted((checkout_root() / "config" / "src").glob("*.yaml"))
    assert sources, "found nothing to check"
    for path in sources:
        assert RigSpec.load(path).validate() == [], path.name


def test_source_and_sink_load_from_yaml(tmp_path):
    rig = RigSpec.load(_write(tmp_path, """
        tracks:
          - name: t
            source: guitar-in
            sink: monitor-out
            inputs: [{engine_bus: 0, track_bus: 0}]
            outputs: [{engine_bus: 0, track_bus: 0}]
    """))
    assert (rig.tracks[0].source, rig.tracks[0].sink) == ("guitar-in", "monitor-out")
    assert rig.validate() == []


def test_roles_never_reach_the_sushi_config():
    """Sushi would not know what to do with them, and they only mean something to
    the patchbay generator."""
    entry = TrackSpec(
        name="t", source="guitar-in", sink="monitor-out",
        inputs=[{"engine_bus": 0, "track_bus": 0}],
    ).to_sushi()
    assert "source" not in entry and "sink" not in entry


def test_a_source_on_a_track_with_no_inputs_is_a_problem():
    problems = RigSpec(tracks=[TrackSpec(name="t", source="guitar-in")]).validate()
    assert len(problems) == 1
    assert "source 'guitar-in'" in problems[0] and "no inputs" in problems[0]


def test_a_sink_on_a_track_with_no_outputs_is_a_problem():
    problems = RigSpec(tracks=[TrackSpec(name="t", sink="monitor-out")]).validate()
    assert len(problems) == 1 and "no outputs" in problems[0]


@pytest.mark.parametrize("bad", ["", "  ", 3, ["guitar-in"]])
def test_a_source_that_is_not_a_role_name_is_a_problem(bad):
    track = TrackSpec(name="t", source=bad, inputs=[{"engine_bus": 0, "track_bus": 0}])
    problems = RigSpec(tracks=[track]).validate()
    assert len(problems) == 1 and "must be a role name" in problems[0]


def test_the_example_spec_is_valid_and_uses_only_plugins_in_the_manifest():
    """The example's header says its plugins are real and in the manifest; this
    is what keeps that true (it once used Calf plugins the rig never had)."""
    from sushi_rig.paths import checkout_root

    root = checkout_root()
    rig = RigSpec.load(root / "tool" / "examples" / "rig.example.yaml")
    assert rig.validate() == []
    manifest = set((root / "plugin-manifest.txt").read_text().split())
    uris = {p.uri for t in rig.tracks for p in t.plugins if p.type == "lv2"}
    assert uris, "no lv2 plugins would make this vacuous"
    assert uris <= manifest, sorted(uris - manifest)
