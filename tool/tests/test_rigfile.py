"""A rig as channels and a master (#30), compiled to what Sushi runs.

Most of this is the compiler's arithmetic, checked by construction. The end is
a load through a real Sushi: that a compiled rig loads with its sends pointed
where the compiler pointed them. The render that shows a mono input really does
fill both halves of its channel needs numpy, so it is in test_rigfile_live.py.
"""

from __future__ import annotations

import json
import pytest
import yaml

from sushi_rig import rigfile
from sushi_rig.paths import checkout_root
from sushi_rig.rigfile import RigFileError, compile_rig, emit_rig, parse_rig

ROOT = checkout_root()
EXAMPLE = ROOT / "config" / "rigs" / "guitar_vocal.yaml"


def rig_yaml(channels: list[dict], master: dict | None = None, **top) -> dict:
    raw = {"kind": "rig", "schema_version": 1, "name": "test", "channels": channels,
           "master": master or {"output": "monitor-out"}}
    raw.update(top)
    return raw


def channel(name: str, width: str = "mono", input: str | None = None, chain=None) -> list[dict]:
    entry = {"name": name, "input": input or name + "-in", "width": width}
    if chain is not None:
        entry["chain"] = chain
    return [entry]


def problems(raw) -> list[str]:
    with pytest.raises(RigFileError) as caught:
        parse_rig(raw)
    return caught.value.problems


def tracks(raw) -> dict[str, dict]:
    return {t["name"]: t for t in emit_rig(parse_rig(raw))["tracks"]}


# --------------------------------------------------------------------------- #
# Allocation
# --------------------------------------------------------------------------- #

def test_the_example_compiles_to_two_stems_and_a_master():
    config = emit_rig(rigfile.load_rig(EXAMPLE))
    assert [t["name"] for t in config["tracks"]] == ["guitar", "vocal", "master"]
    by_name = {t["name"]: t for t in config["tracks"]}
    assert by_name["guitar"]["outputs"] == [{"engine_bus": 1, "track_bus": 0}]
    assert by_name["vocal"]["outputs"] == [{"engine_bus": 2, "track_bus": 0}]
    assert by_name["master"]["outputs"] == [{"engine_bus": 0, "track_bus": 0}]
    assert by_name["master"]["inputs"] == []


def test_a_mono_input_uses_one_engine_channel_for_both_halves():
    t = tracks(rig_yaml(channel("guitar") + channel("vocal")))
    assert t["guitar"]["inputs"] == [
        {"engine_channel": 0, "track_channel": 0}, {"engine_channel": 0, "track_channel": 1},
    ]
    assert t["vocal"]["inputs"] == [
        {"engine_channel": 1, "track_channel": 0}, {"engine_channel": 1, "track_channel": 1},
    ]


def test_a_stereo_input_takes_the_next_two_engine_channels():
    t = tracks(rig_yaml(channel("guitar") + channel("keys", "stereo") + channel("vocal")))
    assert t["keys"]["inputs"] == [
        {"engine_channel": 1, "track_channel": 0}, {"engine_channel": 2, "track_channel": 1},
    ]
    assert t["vocal"]["inputs"][0]["engine_channel"] == 3


def test_every_channel_ends_in_a_send_to_the_master_and_the_master_is_last():
    config = emit_rig(parse_rig(rig_yaml(channel("guitar") + channel("vocal"))))
    assert config["tracks"][-1]["name"] == "master"
    for track in config["tracks"][:-1]:
        last = track["plugins"][-1]
        assert last == {"name": f"{track['name']}_send", "type": "internal", "uid": rigfile.SEND_UID}
    assert config["tracks"][-1]["plugins"][0]["uid"] == rigfile.RETURN_UID
    assert config["initial_state"] == [
        {"processor": "guitar_send", "properties": {"destination_name": "master_return"}},
        {"processor": "vocal_send", "properties": {"destination_name": "master_return"}},
    ]


def test_the_master_chain_follows_the_return():
    raw = rig_yaml(channel("guitar"), {"output": "monitor-out", "chain": [{"name": "ceiling", "uri": "urn:x"}]})
    assert [p["name"] for p in tracks(raw)["master"]["plugins"]] == ["master_return", "ceiling"]


def test_roles_are_carried_for_the_patchbay():
    spec, _ = compile_rig(parse_rig(rig_yaml(channel("guitar", input="guitar-in"))))
    by_name = {t.name: t for t in spec.tracks}
    assert by_name["guitar"].source == "guitar-in" and by_name["guitar"].sink is None
    assert by_name["master"].sink == "monitor-out" and by_name["master"].source is None


# --------------------------------------------------------------------------- #
# The ceiling
# --------------------------------------------------------------------------- #

def test_three_channels_fit_and_a_fourth_is_refused_with_the_arithmetic():
    three = channel("a") + channel("b") + channel("c")
    assert len(emit_rig(parse_rig(rig_yaml(three)))["tracks"]) == 4
    (problem,) = problems(rig_yaml(three + channel("d")))
    assert problem == (
        "4 channels need 10 outputs — 2 for the master and 2 for each channel's stem — "
        "and Sushi has 8, so a rig has at most 3 channels"
    )


def test_inputs_are_counted_by_width():
    """Three stereo channels fit the outputs but need six inputs, which fits;
    the arithmetic is shown when it does not."""
    rig = rigfile.Rig("t", tuple(
        rigfile.Channel(n, "x", "stereo") for n in "abcde"
    ), rigfile.Master("out"))
    found = rigfile.check_rig(rig)
    assert "the channels need 10 inputs (a 2 + b 2 + c 2 + d 2 + e 2) and Sushi has 8" in found


# --------------------------------------------------------------------------- #
# Refusals
# --------------------------------------------------------------------------- #

def test_a_misspelt_key_is_refused_not_ignored():
    raw = rig_yaml([{"name": "keys", "input": "keys-in", "widht": "stereo"}])
    found = problems(raw)
    assert any("unknown key 'widht'" in p for p in found)
    assert any("width must be one of mono, stereo" in p for p in found)


def test_a_version_this_tool_does_not_read_is_refused():
    found = problems(rig_yaml(channel("guitar"), schema_version=2))
    assert found == ["schema_version 2 is not one this tool reads (it reads 1)"]


def test_names_the_compiler_owns_are_refused_by_name():
    found = problems(rig_yaml(
        channel("master") + channel("guitar", chain=[{"name": "guitar_send", "uri": "urn:x"}])
    ))
    assert any(p.startswith("channel 'master': a name the compiler gives") for p in found)
    assert any("plugin 'guitar_send' has a name the compiler gives" in p for p in found)


@pytest.mark.parametrize("name", ["master_return", "vocal_send"])
def test_a_channel_named_like_a_send_or_the_return_is_refused(name):
    found = problems(rig_yaml(channel("vocal") + channel(name)))
    assert any(p.startswith(f"channel {name!r}: a name the compiler gives") for p in found)


def test_a_hand_built_rig_with_a_bad_width_is_a_problem_not_a_crash():
    rig = rigfile.Rig("t", (rigfile.Channel("a", "in", "Mono"),), rigfile.Master("out"))
    with pytest.raises(RigFileError, match="width must be one of mono, stereo"):
        compile_rig(rig)


def test_a_name_that_cannot_be_a_stem_is_refused():
    found = problems(rig_yaml(channel("Lead Guitar")))
    assert any("channel 'Lead Guitar': a name must match" in p for p in found)


def test_every_problem_is_reported_at_once():
    raw = {"kind": "rig", "schema_version": 1, "name": "t",
           "channels": [{"name": "a", "width": "wide"}, "b"], "extra": 1}
    found = problems(raw)
    assert len(found) == 5, found


def test_a_v1_rig_is_not_mistaken_for_one():
    v1 = yaml.safe_load((ROOT / "config" / "src" / "guitar_vocal.yaml").read_text())
    assert not rigfile.is_rig_file(v1)
    assert rigfile.is_rig_file(yaml.safe_load(EXAMPLE.read_text()))


# --------------------------------------------------------------------------- #
# Captured state
# --------------------------------------------------------------------------- #

def test_a_capture_keeps_its_values_but_not_a_moved_send_or_return():
    """A player who moves a pedal on the panel and saves keeps the move; the
    send and return stay where the monitor path needs them."""
    captured = {
        "processors": {
            "guitar_send": {"parameters": {"gain": 0.4}, "properties": {"destination_name": "nowhere"}},
            "guitar_delay": {"parameters": {"Delay": 0.2}},
        },
        "tracks": {
            "guitar": ["guitar_send", "guitar_delay", "guitar_room"],
            "master": ["ceiling", "master_return"],
        },
    }
    raw = rig_yaml(
        channel("guitar", chain=[{"name": "guitar_room", "uri": "urn:r"}, {"name": "guitar_delay", "uri": "urn:d"}]),
        {"output": "monitor-out", "chain": [{"name": "ceiling", "uri": "urn:x"}]},
    )
    config = emit_rig(parse_rig(raw), captured)
    by_name = {t["name"]: t for t in config["tracks"]}
    assert [p["name"] for p in by_name["guitar"]["plugins"]] == ["guitar_delay", "guitar_room", "guitar_send"]
    assert [p["name"] for p in by_name["master"]["plugins"]] == ["master_return", "ceiling"]
    state = {e["processor"]: e for e in config["initial_state"]}
    assert state["guitar_send"]["properties"] == {"destination_name": "master_return"}
    assert state["guitar_send"]["parameters"] == {"gain": 0.4}
    assert state["guitar_delay"]["parameters"] == {"Delay": 0.2}


def test_a_plugin_added_after_a_capture_still_comes_before_the_send():
    """emit appends what a capture does not mention, which once put it after
    the send: heard in the stem, missing from the monitors."""
    raw = rig_yaml(channel("guitar", chain=[{"name": "a", "uri": "urn:a"}, {"name": "b", "uri": "urn:b"}]))
    for order in (["a", "guitar_send"], []):
        config = emit_rig(parse_rig(raw), {"tracks": {"guitar": order}})
        assert [p["name"] for p in config["tracks"][0]["plugins"]][-1] == "guitar_send", order


def test_a_state_file_with_null_properties_is_read(tmp_path):
    config = emit_rig(parse_rig(rig_yaml(channel("guitar"))),
                      {"processors": {"guitar_send": {"properties": None}}})
    (entry,) = config["initial_state"]
    assert entry["properties"] == {"destination_name": "master_return"}


@pytest.mark.parametrize("kind", ["Rig", None])
def test_a_rig_file_with_its_kind_wrong_is_refused_not_emitted_empty(tmp_path, kind):
    from sushi_rig.cli import main

    raw = rig_yaml(channel("guitar"))
    if kind is None:
        del raw["kind"]
    else:
        raw["kind"] = kind
    path = tmp_path / "rig.yaml"
    path.write_text(yaml.safe_dump(raw))
    with pytest.raises(SystemExit) as caught:
        main(["emit", str(path), "-o", str(tmp_path / "x.json"), "--no-verify"])
    assert "kind must be 'rig'" in str(caught.value.code)
    assert not (tmp_path / "x.json").exists()


def test_the_tools_that_read_tracks_refuse_a_rig_file_by_name():
    """save, push, probes and the patchbay read tracks; given a rig file they
    would have run a rig with none."""
    from sushi_rig.spec import RigSpec

    with pytest.raises(ValueError, match="channels-and-master rig file"):
        RigSpec.load(EXAMPLE)


def test_the_command_emits_either_kind(tmp_path):
    from sushi_rig.cli import main

    out = tmp_path / "gv.json"
    assert main(["emit", str(EXAMPLE), "-o", str(out), "--no-verify"]) == 0
    assert json.loads(out.read_text())["tracks"][-1]["name"] == "master"
    v1 = tmp_path / "v1.json"
    assert main(["emit", str(ROOT / "config" / "src" / "guitar_vocal.yaml"), "-o", str(v1), "--no-verify"]) == 0
    assert "master" not in [t["name"] for t in json.loads(v1.read_text())["tracks"]]


def test_the_command_refuses_a_bad_rig_with_every_problem(tmp_path):
    from sushi_rig.cli import main

    bad = tmp_path / "bad.yaml"
    bad.write_text(yaml.safe_dump(rig_yaml(channel("a") + channel("b") + channel("c") + channel("d"))))
    with pytest.raises(SystemExit) as caught:
        main(["emit", str(bad), "-o", str(tmp_path / "x.json"), "--no-verify"])
    assert "at most 3 channels" in str(caught.value.code)


# --------------------------------------------------------------------------- #
# Against a real Sushi
#
# The renders that need numpy are in test_rigfile_live.py, so a machine without
# numpy still runs everything above and this.
# --------------------------------------------------------------------------- #

SUSHI = ROOT / "sushi"
needs_sushi = pytest.mark.skipif(not SUSHI.exists(), reason="needs the Sushi binary")


@needs_sushi
@pytest.mark.slow
def test_the_example_loads_with_its_sends_pointed_at_the_master(tmp_path):
    """Loaded headless, and its state read back: `verify` diffs what Sushi holds
    against what the config asked for, including each send's destination."""
    from sushi_rig.verify import verify

    config = tmp_path / "guitar_vocal_v2.json"
    config.write_text(json.dumps(emit_rig(rigfile.load_rig(EXAMPLE)), indent=2))
    assert verify(config, str(SUSHI)) == []
