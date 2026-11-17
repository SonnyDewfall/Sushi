"""Channel presets (#127), the file: `config/channels/<name>.yaml`, its
loader and validator (presets.py), and what a load does to a channel's chain
and to a rig's MIDI map, before any agent is involved.

Against real rig channels: every channel of every frozen rig
(`tests/fixtures/rigs/`, rig_fixtures.py) is made a preset, written, read
back and checked by the real compiler (`rigfile.compile_rig`, with the
committed `plugin-ports.txt`). Nothing here reads config/rigs/ or
config/channels/ of the checkout.
"""

from __future__ import annotations

import copy

import pytest
import yaml
from rig_fixtures import frozen, frozen_names

from sushi_rig import presets
from sushi_rig.rigfile import RigFileError, compile_rig, parse_rig
from sushi_rig.save import SaveError

GXTS9 = "http://guitarix.sourceforge.net/plugins/gxts9#ts9sim"
COMP = "http://guitarix.sourceforge.net/plugins/gx_compressor#_compressor"
NAM = "http://github.com/mikeoliphant/neural-amp-modeler-lv2"


def frozen_channels():
    out = []
    for rig in frozen_names():
        raw = yaml.safe_load(frozen(rig).read_text())
        for channel in raw["channels"]:
            out.append((rig, channel["name"], raw, channel))
    return out


CHANNELS = frozen_channels()
IDS = [f"{rig}-{channel}" for rig, channel, _, _ in CHANNELS]


def preset_doc(**over):
    doc = {"kind": "channel", "schema_version": 1, "name": "crunch", "width": "mono",
           "chain": [{"name": "drive", "uri": GXTS9, "bypassed": True, "parameters": {"Drive": 0.4}}]}
    doc.update(over)
    return doc


# --------------------------------------------------------------------------- #
# Made from a real channel, written, read back
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(("rig", "channel", "raw", "entry"), CHANNELS, ids=IDS)
def test_every_frozen_channel_makes_a_preset_that_reads_back_and_compiles(tmp_path, rig, channel, raw, entry):
    doc = presets.from_channel(entry, "sound", saved_from={"rig": rig, "channel": channel})
    path = tmp_path / "config" / "channels" / "sound.yaml"
    presets.write_preset(path, doc, header_text=presets.header("sound", "2026-11-17", f"{rig}'s {channel}"))
    text = path.read_text()
    assert "input:" not in text, "a preset names no input"
    assert "_to_stereo" not in text
    assert text.startswith("# Channel preset sound: saved from ")
    preset = presets.load_preset(path)
    assert presets.check_preset(preset) == []
    assert preset.width == entry["width"]
    assert preset.level == entry.get("level", 0.0)
    assert preset.saved_from == {"rig": rig, "channel": channel}
    # Settings and bypass are the channel's, entry for entry, under base names.
    assert len(preset.chain) == len(entry["chain"])
    for stored, original in zip(preset.chain, entry["chain"]):
        assert {k: v for k, v in stored.items() if k != "name"} == {k: v for k, v in original.items() if k != "name"}


@pytest.mark.parametrize(("rig", "channel", "raw", "entry"), CHANNELS, ids=IDS)
def test_a_preset_loaded_back_onto_its_own_channel_compiles_in_its_rig(rig, channel, raw, entry):
    """The channel with the preset loaded is a valid rig, by the compiler:
    names on the channel are its prefix and the base names."""
    preset = presets.parse_preset(presets.from_channel(entry, "sound"))
    edited = copy.deepcopy(raw)
    edited.pop("midi", None)
    for i, c in enumerate(edited["channels"]):
        if c["name"] == channel:
            edited["channels"][i] = presets.loaded_channel(c, preset)
            names = [p["name"] for p in edited["channels"][i]["chain"]]
            assert all(n.startswith(f"{channel}_") for n in names)
    compile_rig(parse_rig(edited))


def test_a_prefixed_chain_comes_back_with_the_names_it_had():
    """guitar_vocal names its plugins after their channel: saved and loaded
    back onto that channel, the chain is the one it was, name for name."""
    raw = yaml.safe_load(frozen("guitar_vocal").read_text())
    guitar = raw["channels"][0]
    preset = presets.parse_preset(presets.from_channel(guitar, "gv-guitar"))
    assert preset.plugins == ["compressor", "overdrive", "eq", "chorus", "delay", "room"]
    assert presets.loaded_chain(preset, "guitar") == guitar["chain"]
    assert [p["name"] for p in presets.loaded_chain(preset, "lead")] == [
        "lead_compressor", "lead_overdrive", "lead_eq", "lead_chorus", "lead_delay", "lead_room"]


def test_two_channels_from_one_preset_compile_with_distinct_names():
    """#127's "done": the same sound on two inputs. #125's copy of a rig's
    channel keeps its names, so the compiler refuses the second; a preset's
    are made per channel, so it compiles, and every processor is distinct."""
    raw = yaml.safe_load(frozen("electric_board").read_text())
    board = raw["channels"][0]
    preset = presets.parse_preset(presets.from_channel(board, "board"))
    rig = {"kind": "rig", "schema_version": 1, "name": "two-guitars", "master": {"output": "monitor-out", "chain": []},
           "channels": [presets.channel_entry("left", "guitar-in", "mono", preset),
                        presets.channel_entry("right", "vocal-in", "mono", preset)]}
    spec, _ = compile_rig(parse_rig(rig))
    names = [p.name for t in spec.tracks for p in t.plugins]
    assert len(names) == len(set(names))
    assert "left_compressor" in names and "right_compressor" in names
    copied = copy.deepcopy(rig)
    copied["channels"][1]["chain"] = copy.deepcopy(board["chain"])
    copied["channels"][0]["chain"] = copy.deepcopy(board["chain"])
    with pytest.raises(RigFileError):
        compile_rig(parse_rig(copied))


def test_a_stereo_copy_is_never_stored():
    entry = {"name": "board", "input": "guitar-in", "width": "mono",
             "chain": [{"name": "board_drive", "uri": GXTS9}, {"name": "board_drive_to_stereo", "uri": None}]}
    doc = presets.from_channel(entry, "p")
    assert [e["name"] for e in doc["chain"]] == ["drive"]
    with pytest.raises(presets.PresetError, match="_to_stereo"):
        presets.parse_preset(preset_doc(chain=[{"name": "drive_to_stereo", "uri": GXTS9}]))


def test_level_and_width_and_the_amp_are_kept_and_the_input_and_strip_are_not():
    entry = {"name": "board", "input": "guitar-in", "width": "stereo", "level": -4.5,
             "strip": {"gain": 0.833333, "pan": 0.5, "mute": 0.0}, "chain": [{"name": "drive", "uri": GXTS9}]}
    doc = presets.from_channel(entry, "p", heard_through_amp="amp/models/Some.nam")
    assert doc["width"] == "stereo" and doc["level"] == -4.5
    assert doc["heard_through_amp"] == "amp/models/Some.nam"
    assert "input" not in doc and "strip" not in doc
    unity = presets.from_channel({**entry, "level": 0.0}, "p")
    assert "level" not in unity


# --------------------------------------------------------------------------- #
# Names
# --------------------------------------------------------------------------- #

def test_names_are_per_channel():
    assert presets.processor_name("lead", "drive") == "lead_drive"
    assert presets.processor_name("lead", "lead_drive") == "lead_lead_drive", "always prefixed: one-to-one"
    bases = ["eq", "lead_eq", "lead_lead_eq", "x", "lead_x"]
    assert len({presets.processor_name("lead", b) for b in bases}) == len(bases)
    assert presets.base_names("guitar", ["guitar_eq", "room", "guitar_"]) == {
        "guitar_eq": "eq", "room": "room", "guitar_": "guitar_"}
    # Taking the prefix off would make two names one: none is taken off.
    assert presets.base_names("guitar", ["guitar_eq", "eq"]) == {"guitar_eq": "guitar_eq", "eq": "eq"}


def test_a_base_name_that_becomes_the_compilers_send_is_said():
    preset = presets.parse_preset(preset_doc(chain=[{"name": "send", "uri": GXTS9}]))
    problems = presets.check_preset(preset)
    assert problems and "send" in problems[0]


# --------------------------------------------------------------------------- #
# The validator
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(("change", "said"), [
    ({"kind": "rig"}, "kind must be 'channel'"),
    ({"schema_version": 2}, "schema_version 2"),
    ({"name": "Bad Name"}, "name 'Bad Name'"),
    ({"width": "wide"}, "width must be one of"),
    ({"level": 40}, "level 40 dB is outside"),
    ({"input": "guitar-in"}, "unknown key 'input'"),
    ({"chain": "x"}, "chain must be a list"),
    ({"chain": [{"name": "a", "uri": GXTS9}, {"name": "a", "uri": GXTS9}]}, "appears twice"),
    ({"chain": [{"name": "a"}]}, "says no uri"),
    ({"chain": [{"name": "a", "uri": GXTS9, "parameters": {"Drive": "high"}}]}, "is not a mapping of parameter"),
    ({"saved_from": "electric_board"}, "saved_from must be"),
    ({"heard_through_amp": 3}, "heard_through_amp must name"),
])
def test_a_preset_of_the_wrong_shape_is_refused_by_name(change, said):
    with pytest.raises(presets.PresetError) as caught:
        presets.parse_preset(preset_doc(**change))
    assert any(said in p for p in caught.value.problems), caught.value.problems


def test_the_compiler_decides_what_the_plugins_may_be():
    unknown = presets.parse_preset(preset_doc(chain=[{"name": "x", "uri": "http://example.invalid/none"}]))
    assert any("plugin-ports.txt" in p for p in presets.check_preset(unknown))
    amp = presets.parse_preset(preset_doc(chain=[{"name": "amp", "uri": NAM}]))
    assert any("Carla" in p for p in presets.check_preset(amp))


def test_a_file_that_is_not_yaml_is_a_preset_error(tmp_path):
    path = tmp_path / "bad.yaml"
    path.write_text("kind: [\n")
    with pytest.raises(presets.PresetError, match="not YAML"):
        presets.load_preset(path)


def test_a_preset_name_is_a_name_before_it_is_a_path(tmp_path):
    assert presets.preset_path(tmp_path, "crunch") == tmp_path / "config" / "channels" / "crunch.yaml"
    for bad in ("../rigs/x", "Crunch", "", "a/b"):
        with pytest.raises(SaveError):
            presets.preset_path(tmp_path, bad)


# --------------------------------------------------------------------------- #
# Writing: write_rig's rules
# --------------------------------------------------------------------------- #

def test_an_update_keeps_the_files_comments(tmp_path):
    path = tmp_path / "crunch.yaml"
    path.write_text(
        "# My crunch.\n\n"
        "kind: channel\nschema_version: 1\nname: crunch\nwidth: mono   # the guitar\n"
        "chain:\n"
        "# The drive, low.\n"
        "- name: drive\n  uri: " + GXTS9 + "\n  bypassed: true\n  parameters:\n    Drive: 0.4\n")
    doc = preset_doc()
    doc["chain"][0]["parameters"]["Drive"] = 0.55
    presets.write_preset(path, doc)
    text = path.read_text()
    assert "# My crunch." in text and "# the guitar" in text and "# The drive, low." in text
    assert "Drive: 0.55" in text
    assert presets.load_preset(path).chain[0]["parameters"] == {"Drive": 0.55}


def test_nothing_is_written_for_a_preset_that_does_not_parse(tmp_path):
    path = tmp_path / "crunch.yaml"
    with pytest.raises(presets.PresetError):
        presets.write_preset(path, preset_doc(width="wide"))
    assert not path.exists()


def test_the_listing_says_why_a_preset_cannot_be_loaded(tmp_path):
    folder = tmp_path / "config" / "channels"
    presets.write_preset(folder / "good.yaml", preset_doc(name="good"))
    (folder / "broken.yaml").write_text("kind: channel\n")
    presets.write_preset(folder / "unknown.yaml",
                         preset_doc(name="unknown", chain=[{"name": "x", "uri": "http://example.invalid/none"}]))
    (folder / "Not A Name.yaml").write_text("")
    (folder / "deeper").mkdir()
    (folder / "deeper" / "hidden.yaml").write_text("")
    listed = {p["name"]: p for p in presets.listing(tmp_path)}
    assert sorted(listed) == ["broken", "good", "unknown"]
    assert listed["good"]["ok"] and listed["good"]["plugins"] == ["drive"]
    assert not listed["broken"]["ok"] and "width must be one of" in listed["broken"]["problem"]
    assert not listed["unknown"]["ok"] and "plugin-ports.txt" in listed["unknown"]["problem"]


# --------------------------------------------------------------------------- #
# The MIDI map, and the amp
# --------------------------------------------------------------------------- #

def midi(*controls):
    return {"controller": "pedal", "controls": [dict(c) for c in controls]}


def test_a_mapping_follows_the_same_plugin_under_its_new_name():
    old = [{"name": "drive", "uri": GXTS9}, {"name": "comp", "uri": COMP}]
    new = [{"name": "lead_drive", "uri": GXTS9}, {"name": "lead_comp", "uri": COMP}]
    section = midi({"control": "exp", "parameter": "lead/drive/Drive"}, {"control": "fs1", "bypass": "lead/comp"},
                   {"control": "fs2", "mute": "lead"}, {"control": "exp2", "blend": ["lead", "vocal"]},
                   {"control": "fs3", "bypass": "vocal/eq"})
    out, followed, orphaned = presets.midi_after_load(section, "lead", old, new)
    assert orphaned == []
    assert [c.get("parameter") or c.get("bypass") or c.get("mute") or c.get("blend") for c in out["controls"]] == [
        "lead/lead_drive/Drive", "lead/lead_comp", "lead", ["lead", "vocal"], "vocal/eq"]
    assert followed == [{"control": "exp", "from": "lead/drive/Drive", "to": "lead/lead_drive/Drive"},
                        {"control": "fs1", "from": "lead/comp", "to": "lead/lead_comp"}]
    assert section["controls"][0]["parameter"] == "lead/drive/Drive", "the section given is left as it is"


def test_a_mapping_onto_a_plugin_the_preset_lacks_or_replaces_is_orphaned():
    old = [{"name": "lead_drive", "uri": GXTS9}, {"name": "lead_comp", "uri": COMP}]
    new = [{"name": "lead_drive", "uri": COMP}, {"name": "lead_eq", "uri": "http://distrho.sf.net/plugins/3BandEQ"}]
    section = midi({"control": "exp", "parameter": "lead/lead_drive/Drive"}, {"control": "fs1", "bypass": "lead/lead_comp"})
    out, followed, orphaned = presets.midi_after_load(section, "lead", old, new)
    # The compressor is still there, under the drive's old name: it follows.
    assert followed == [{"control": "fs1", "from": "lead/lead_comp", "to": "lead/lead_drive"}]
    assert [o["control"] for o in orphaned] == ["exp"]
    assert orphaned[0]["why"] == (f"the preset's chain has lead_drive in its place, but it is another plugin ({COMP}, "
                                  f"not {GXTS9}), whose parameters are not known to match")
    assert "exp (parameter lead/lead_drive/Drive)" in presets.mapping_said(orphaned[0])
    dropped = presets.drop_orphaned(out, orphaned)
    assert dropped == {"controller": "pedal", "controls": [{"control": "fs1", "bypass": "lead/lead_drive"}]}


def test_a_mapping_follows_its_plugin_when_two_names_swap():
    """The two plugins trade names: each pedal follows the plugin it worked,
    by its URI, not the name it had (review of #162)."""
    old = [{"name": "lead_a", "uri": GXTS9}, {"name": "lead_b", "uri": COMP}]
    new = [{"name": "lead_a", "uri": COMP}, {"name": "lead_b", "uri": GXTS9}]
    section = midi({"control": "fs1", "bypass": "lead/lead_a"}, {"control": "exp", "parameter": "lead/lead_b/RATIO"})
    out, followed, orphaned = presets.midi_after_load(section, "lead", old, new)
    assert orphaned == []
    assert [c.get("bypass") or c.get("parameter") for c in out["controls"]] == ["lead/lead_b", "lead/lead_a/RATIO"]


def test_two_of_one_plugin_follow_by_name_then_by_place():
    delay = "http://drobilla.net/plugins/mda/Delay"
    old = [{"name": "slap", "uri": delay}, {"name": "echo", "uri": delay}]
    new = [{"name": "board_slap", "uri": delay}, {"name": "board_echo", "uri": delay}]
    out, followed, _ = presets.midi_after_load(midi({"control": "fs", "bypass": "board/echo"}), "board", old, new)
    assert out["controls"][0]["bypass"] == "board/board_echo", "by the same base name"
    renamed = [{"name": "board_one", "uri": delay}, {"name": "board_two", "uri": delay}]
    out, _, _ = presets.midi_after_load(midi({"control": "fs", "bypass": "board/echo"}), "board", old, renamed)
    assert out["controls"][0]["bypass"] == "board/board_two", "the second of them to the second"


def test_an_unprefixed_rig_is_told_what_is_really_there():
    """electric_board names its plugins without the channel: the reason
    compares like with like (review of #162: it compared `overdrive` with
    `board_overdrive` and said there was none)."""
    old = [{"name": "overdrive", "uri": GXTS9}]
    there = [{"name": "board_overdrive", "uri": COMP}]
    _, _, orphaned = presets.midi_after_load(midi({"control": "fs", "bypass": "board/overdrive"}), "board", old, there)
    assert orphaned[0]["why"].startswith("the preset's chain has board_overdrive in its place, but it is another plugin")
    gone = [{"name": "board_eq", "uri": "http://distrho.sf.net/plugins/3BandEQ"}]
    _, _, orphaned = presets.midi_after_load(midi({"control": "fs", "bypass": "board/overdrive"}), "board", old, gone)
    assert orphaned[0]["why"] == f"the preset's chain has no plugin like overdrive ({GXTS9}): it holds board_eq"


def test_a_mapping_kept_where_the_same_plugin_keeps_its_name():
    chain = [{"name": "lead_drive", "uri": GXTS9}]
    out, followed, orphaned = presets.midi_after_load(midi({"control": "fs", "bypass": "lead/lead_drive"}), "lead",
                                                      chain, chain)
    assert (followed, orphaned) == ([], [])
    assert out["controls"][0]["bypass"] == "lead/lead_drive"
    assert presets.midi_after_load(None, "lead", chain, chain) == (None, [], [])


def test_the_amp_is_said_where_a_guitar_preset_lands_without_it():
    guitar = presets.parse_preset(preset_doc(heard_through_amp="amp/models/Neve 2.nam"))
    vocal = presets.parse_preset(preset_doc())
    second = presets.amp_note(guitar, "lead2", "vocal-in", "amp/models/Neve 2.nam")
    assert second["in_front"] is False and "lead2 has no amp" in second["note"]
    assert "patched in front of guitar-in only" in second["note"]
    none = presets.amp_note(guitar, "lead", "guitar-in", None)
    assert none["in_front"] is False and "has no amp" in none["note"]
    first = presets.amp_note(guitar, "lead", "guitar-in", "amp/models/Neve 2.nam")
    assert first == {"in_front": True, "saved_with": "amp/models/Neve 2.nam", "note": None}
    assert "saved without one" in presets.amp_note(vocal, "lead", "guitar-in", "amp/models/Neve 2.nam")["note"]
    assert presets.amp_note(vocal, "voice", "vocal-in", None)["note"] is None


@pytest.mark.committed_presets
def test_every_committed_preset_is_one_that_can_be_loaded():
    """The checkout's config/channels/, whatever it holds (none, today, or the
    owner's saves): each parses and compiles, as config/rigs/'s rigs are held
    to (rig_fixtures.py's rule: nothing is assumed of what is there, not even
    that there are any). Marked, since the folder is guarded as config/rigs/
    is (review of #162)."""
    from sushi_rig.paths import checkout_root

    for listed in presets.listing(checkout_root()):
        assert listed["ok"], f"{listed['file']}: {listed['problem']}"


def test_the_guard_sees_config_channels_read_unmarked():
    import os

    import rig_fixtures

    before = len(rig_fixtures.STRAY)
    try:
        sorted(rig_fixtures.COMMITTED_PRESETS.glob("*.yaml"))
        (rig_fixtures.COMMITTED_PRESETS / "not-there.yaml").read_text()
    except OSError:
        pass
    made = rig_fixtures.STRAY[before:]
    del rig_fixtures.STRAY[before:]  # this test's own, made to try it
    assert made and all(path.startswith(os.path.realpath(rig_fixtures.COMMITTED_PRESETS)) for _, path, _ in made)
    assert "config/channels/ was read by a test that does not say" in rig_fixtures.said(made)


# --------------------------------------------------------------------------- #
# The provenance line (rigfile.with_provenance)
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(("rig", "channel", "raw", "entry"), CHANNELS, ids=IDS)
def test_the_provenance_line_goes_above_the_chain_and_a_second_replaces_the_first(rig, channel, raw, entry):
    from sushi_rig import rigfile, yamlmerge

    text = frozen(rig).read_text()
    first, problems = rigfile.with_provenance(
        text, {channel: rigfile.provenance_line("crunch", "config/channels/crunch.yaml", "2026-11-17")})
    assert problems == []
    assert yaml.safe_load(first) == yaml.safe_load(text), "comments only"
    lines = first.splitlines()
    at = next(i for i, line in enumerate(lines) if line.lstrip().startswith(rigfile.PROVENANCE))
    assert "crunch" in lines[at]
    after = next(line for line in lines[at + 1:] if not line.lstrip().startswith("#"))
    assert after.strip() == "chain:", "directly above the chain"
    assert all(len(line) <= 100 for line in lines[at:at + 4] if line.lstrip().startswith("#"))
    long = "a-preset-with-a-name-long-enough-to-fold-over-two-lines-x"
    second, problems = rigfile.with_provenance(
        first, {channel: rigfile.provenance_line(long, f"config/channels/{long}.yaml", "2026-11-18")})
    assert problems == [] and "crunch" not in second and second.count(rigfile.PROVENANCE) == 1
    assert yamlmerge.merged(second, yaml.safe_load(second)) == second, "the file can still be updated in place"
    assert all(len(line) <= 100 for line in second.splitlines() if line.lstrip().startswith("#"))


def test_a_channel_that_cannot_carry_the_line_is_said_and_left_as_it_was():
    from sushi_rig import rigfile

    text = ("kind: rig\nschema_version: 1\nname: x\nchannels:\n"
            "- {name: a, input: guitar-in, width: mono, chain: []}\nmaster: {output: monitor-out, chain: []}\n")
    out, problems = rigfile.with_provenance(text, {"a": "From channel preset p.", "nope": "From channel preset p."})
    assert out == text and len(problems) == 2


def test_what_the_listing_calls_loadable_loads_back_on_the_channel_it_came_from(tmp_path):
    """Checked on the channel it was saved from (review of #162): a base
    name that becomes the compiler's own there is said, not listed ok."""
    folder = tmp_path / "config" / "channels"
    presets.write_preset(folder / "p.yaml", preset_doc(name="p", saved_from={"rig": "r", "channel": "lead"},
                                                       chain=[{"name": "send", "uri": GXTS9}]))
    listed = presets.listing(tmp_path)[0]
    assert listed["ok"] is False and "lead_send" in listed["problem"]
