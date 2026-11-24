"""A channel of one rig loaded onto a channel of another (#208), pure
(channelcopy.py): what is copied of the source, the names it takes, what a
load does to the target's chain and MIDI map, the amp, and the provenance
line it leaves in the rig file (rigfile.with_provenance).

Against every channel of every frozen rig (`tests/fixtures/rigs/`,
rig_fixtures.py), checked by the real compiler (`rigfile.compile_rig`, with
the committed `plugin-ports.txt`). Nothing here reads config/rigs/ of the
checkout.
"""

from __future__ import annotations

import copy

import pytest
import yaml
from rig_fixtures import frozen, frozen_names

from sushi_rig import channelcopy
from sushi_rig.rigfile import compile_rig, parse_rig

GXTS9 = "http://guitarix.sourceforge.net/plugins/gxts9#ts9sim"
COMP = "http://guitarix.sourceforge.net/plugins/gx_compressor#_compressor"


def frozen_channels():
    out = []
    for rig in frozen_names():
        raw = yaml.safe_load(frozen(rig).read_text())
        for channel in raw["channels"]:
            out.append((rig, channel["name"], raw, channel))
    return out


CHANNELS = frozen_channels()
IDS = [f"{rig}-{channel}" for rig, channel, _, _ in CHANNELS]


def source(chain, rig="r", channel="lead", width="mono", input_role="guitar-in", level=0.0, amp=None):
    return channelcopy.Source(rig, channel, width, input_role, tuple(copy.deepcopy(chain)), level, amp)


# --------------------------------------------------------------------------- #
# The source, from a real channel
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(("rig", "channel", "raw", "entry"), CHANNELS, ids=IDS)
def test_every_frozen_channel_is_copied_with_its_settings_and_none_of_its_rigs_map(rig, channel, raw, entry):
    got = channelcopy.source_of(raw, rig, channel)
    assert got.name == f"{rig} / {channel}"
    assert (got.width, got.input) == (entry["width"], entry["input"])
    assert got.level == entry.get("level", 0.0)
    assert len(got.chain) == len(entry["chain"])
    for copied, original in zip(got.chain, entry["chain"]):
        assert {k: v for k, v in copied.items() if k != "name"} == {k: v for k, v in original.items() if k != "name"}
    model = (raw.get("amp") or {}).get("model")
    assert got.heard_through_amp == (model if entry["input"] == "guitar-in" else None)


@pytest.mark.parametrize(("rig", "channel", "raw", "entry"), CHANNELS, ids=IDS)
def test_every_frozen_channel_loaded_onto_its_own_channel_compiles_in_its_rig(rig, channel, raw, entry):
    """The channel with the copy loaded is a valid rig, by the compiler:
    names on the channel are its prefix and the base names."""
    got = channelcopy.source_of(raw, rig, channel)
    edited = copy.deepcopy(raw)
    edited.pop("midi", None)
    for i, c in enumerate(edited["channels"]):
        if c["name"] == channel:
            edited["channels"][i] = channelcopy.loaded_channel(c, got)
            assert all(p["name"].startswith(f"{channel}_") for p in edited["channels"][i]["chain"])
    compile_rig(parse_rig(edited))


def test_a_prefixed_chain_comes_back_with_the_names_it_had():
    """guitar_vocal names its plugins after their channel: loaded back onto
    that channel, the chain is the one it was, name for name."""
    raw = yaml.safe_load(frozen("guitar_vocal").read_text())
    got = channelcopy.source_of(raw, "guitar_vocal", "guitar")
    assert got.plugins == ["compressor", "overdrive", "eq", "chorus", "delay", "room"]
    assert channelcopy.loaded_chain(got, "guitar") == raw["channels"][0]["chain"]
    assert [p["name"] for p in channelcopy.loaded_chain(got, "lead")] == [
        "lead_compressor", "lead_overdrive", "lead_eq", "lead_chorus", "lead_delay", "lead_room"]


def test_a_rig_without_the_channel_is_said_with_what_it_has():
    raw = yaml.safe_load(frozen("guitar_vocal").read_text())
    with pytest.raises(LookupError, match=r"guitar_vocal has no channel called bass \(it has: guitar, vocal\)"):
        channelcopy.source_of(raw, "guitar_vocal", "bass")
    with pytest.raises(LookupError):
        channelcopy.source_of(None, "x", "y")


def test_the_target_keeps_its_input_width_and_strip_and_takes_the_level():
    got = source([{"name": "drive", "uri": GXTS9}], width="stereo", input_role="keys-in", level=-4.0)
    target = {"name": "vocal", "input": "vocal-in", "width": "mono", "strip": {"gain": 0.8}, "level": -6.0,
              "chain": [{"name": "vocal_eq", "uri": COMP}]}
    out = channelcopy.loaded_channel(target, got)
    assert out == {"name": "vocal", "input": "vocal-in", "width": "mono", "strip": {"gain": 0.8}, "level": -4.0,
                   "chain": [{"name": "vocal_drive", "uri": GXTS9}]}
    assert target["chain"] == [{"name": "vocal_eq", "uri": COMP}], "the entry given is left as it is"
    assert "level" not in channelcopy.loaded_channel(target, source([])), "unity is written as none"


# --------------------------------------------------------------------------- #
# Names
# --------------------------------------------------------------------------- #

def test_names_are_per_channel():
    assert channelcopy.processor_name("lead", "drive") == "lead_drive"
    assert channelcopy.processor_name("lead", "lead_drive") == "lead_lead_drive", "always prefixed: one-to-one"
    bases = ["eq", "lead_eq", "lead_lead_eq", "x", "lead_x"]
    assert len({channelcopy.processor_name("lead", b) for b in bases}) == len(bases)
    assert channelcopy.base_names("guitar", ["guitar_eq", "room", "guitar_"]) == {
        "guitar_eq": "eq", "room": "room", "guitar_": "guitar_"}
    # Taking the prefix off would make two names one: none is taken off.
    assert channelcopy.base_names("guitar", ["guitar_eq", "eq"]) == {"guitar_eq": "guitar_eq", "eq": "eq"}


def test_a_name_too_long_for_a_processor_is_said():
    got = source([{"name": "a" * 60, "uri": GXTS9}])
    assert "would be the name of" in channelcopy.name_problems("lead", got)[0]
    assert channelcopy.name_problems("lead", source([{"name": "drive", "uri": GXTS9}])) == []


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
    out, followed, orphaned = channelcopy.midi_after_load(section, "lead", old, new)
    assert orphaned == []
    assert [c.get("parameter") or c.get("bypass") or c.get("mute") or c.get("blend") for c in out["controls"]] == [
        "lead/lead_drive/Drive", "lead/lead_comp", "lead", ["lead", "vocal"], "vocal/eq"]
    assert followed == [{"control": "exp", "from": "lead/drive/Drive", "to": "lead/lead_drive/Drive"},
                        {"control": "fs1", "from": "lead/comp", "to": "lead/lead_comp"}]
    assert section["controls"][0]["parameter"] == "lead/drive/Drive", "the section given is left as it is"


def test_a_parameter_whose_name_has_a_slash_follows_with_its_name_whole():
    """A parameter target is split at its first two slashes (#210's review):
    SWH lcrDelay's "Dry/Wet level" follows its plugin as one name."""
    delay = "http://plugin.org.uk/swh-plugins/lcrDelay"
    old = [{"name": "delay", "uri": delay}]
    new = [{"name": "lead_delay", "uri": delay}]
    section = midi({"control": "exp", "parameter": "lead/delay/Dry/Wet level"})
    out, followed, orphaned = channelcopy.midi_after_load(section, "lead", old, new)
    assert orphaned == []
    assert out["controls"][0]["parameter"] == "lead/lead_delay/Dry/Wet level"
    assert followed == [{"control": "exp", "from": "lead/delay/Dry/Wet level", "to": "lead/lead_delay/Dry/Wet level"}]
    assert parse_rig({"kind": "rig", "schema_version": 1, "name": "r",
                      "channels": [{"name": "lead", "input": "guitar-in", "width": "mono", "chain": new}],
                      "master": {"output": "monitor-out", "chain": []},
                      "midi": out}).midi is not None, "the followed target is one a rig file takes"


def test_a_mapping_onto_a_plugin_the_copy_lacks_or_replaces_is_orphaned():
    old = [{"name": "lead_drive", "uri": GXTS9}, {"name": "lead_comp", "uri": COMP}]
    new = [{"name": "lead_drive", "uri": COMP}, {"name": "lead_eq", "uri": "http://distrho.sf.net/plugins/3BandEQ"}]
    section = midi({"control": "exp", "parameter": "lead/lead_drive/Drive"}, {"control": "fs1", "bypass": "lead/lead_comp"})
    out, followed, orphaned = channelcopy.midi_after_load(section, "lead", old, new)
    # The compressor is still there, under the drive's old name: it follows.
    assert followed == [{"control": "fs1", "from": "lead/lead_comp", "to": "lead/lead_drive"}]
    assert [o["control"] for o in orphaned] == ["exp"]
    assert orphaned[0]["why"] == (f"the loaded chain has lead_drive in its place, but it is another plugin ({COMP}, "
                                  f"not {GXTS9}), whose parameters are not known to match")
    assert "exp (parameter lead/lead_drive/Drive)" in channelcopy.mapping_said(orphaned[0])
    dropped = channelcopy.drop_orphaned(out, orphaned)
    assert dropped == {"controller": "pedal", "controls": [{"control": "fs1", "bypass": "lead/lead_drive"}]}


def test_a_mapping_follows_its_plugin_when_two_names_swap():
    """The two plugins trade names: each pedal follows the plugin it worked,
    by its URI, not the name it had (review of #162)."""
    old = [{"name": "lead_a", "uri": GXTS9}, {"name": "lead_b", "uri": COMP}]
    new = [{"name": "lead_a", "uri": COMP}, {"name": "lead_b", "uri": GXTS9}]
    section = midi({"control": "fs1", "bypass": "lead/lead_a"}, {"control": "exp", "parameter": "lead/lead_b/RATIO"})
    out, followed, orphaned = channelcopy.midi_after_load(section, "lead", old, new)
    assert orphaned == []
    assert [c.get("bypass") or c.get("parameter") for c in out["controls"]] == ["lead/lead_b", "lead/lead_a/RATIO"]


def test_two_of_one_plugin_follow_by_name_then_by_place():
    delay = "http://drobilla.net/plugins/mda/Delay"
    old = [{"name": "slap", "uri": delay}, {"name": "echo", "uri": delay}]
    new = [{"name": "board_slap", "uri": delay}, {"name": "board_echo", "uri": delay}]
    out, followed, _ = channelcopy.midi_after_load(midi({"control": "fs", "bypass": "board/echo"}), "board", old, new)
    assert out["controls"][0]["bypass"] == "board/board_echo", "by the same base name"
    renamed = [{"name": "board_one", "uri": delay}, {"name": "board_two", "uri": delay}]
    out, _, _ = channelcopy.midi_after_load(midi({"control": "fs", "bypass": "board/echo"}), "board", old, renamed)
    assert out["controls"][0]["bypass"] == "board/board_two", "the second of them to the second"


def test_an_unprefixed_rig_is_told_what_is_really_there():
    """electric_board names its plugins without the channel: the reason
    compares like with like (review of #162)."""
    old = [{"name": "overdrive", "uri": GXTS9}]
    there = [{"name": "board_overdrive", "uri": COMP}]
    _, _, orphaned = channelcopy.midi_after_load(midi({"control": "fs", "bypass": "board/overdrive"}), "board", old, there)
    assert orphaned[0]["why"].startswith("the loaded chain has board_overdrive in its place, but it is another plugin")
    gone = [{"name": "board_eq", "uri": "http://distrho.sf.net/plugins/3BandEQ"}]
    _, _, orphaned = channelcopy.midi_after_load(midi({"control": "fs", "bypass": "board/overdrive"}), "board", old, gone)
    assert orphaned[0]["why"] == f"the loaded chain has no plugin like overdrive ({GXTS9}): it holds board_eq"


def test_a_mapping_kept_where_the_same_plugin_keeps_its_name():
    chain = [{"name": "lead_drive", "uri": GXTS9}]
    out, followed, orphaned = channelcopy.midi_after_load(midi({"control": "fs", "bypass": "lead/lead_drive"}), "lead",
                                                          chain, chain)
    assert (followed, orphaned) == ([], [])
    assert out["controls"][0]["bypass"] == "lead/lead_drive"
    assert channelcopy.midi_after_load(None, "lead", chain, chain) == (None, [], [])


def test_the_amp_is_said_where_a_guitar_channel_lands_without_it():
    guitar = source([], rig="string-machine", channel="strings", amp="amp/models/Neve 2.nam")
    vocal = source([], rig="guitar_vocal", channel="vocal", input_role="vocal-in")
    second = channelcopy.amp_note(guitar, "lead2", "vocal-in", "amp/models/Neve 2.nam")
    assert second["in_front"] is False
    assert second["note"] == ("lead2 has no amp: string-machine / strings plays through one (Neve 2), and lead2 "
                              "plays from vocal-in, and the amp is in front of guitar-in only")
    none = channelcopy.amp_note(guitar, "lead", "guitar-in", None)
    assert none["in_front"] is False and none["note"].endswith("and this rig has no amp")
    first = channelcopy.amp_note(guitar, "lead", "guitar-in", "amp/models/Neve 2.nam")
    assert first == {"in_front": True, "heard_with": "amp/models/Neve 2.nam", "note": None}
    assert channelcopy.amp_note(vocal, "lead", "guitar-in", "amp/models/Neve 2.nam")["note"] == (
        "the amp (Neve 2) is in front of lead; guitar_vocal / vocal has none in its rig")
    assert channelcopy.amp_note(vocal, "voice", "vocal-in", None)["note"] is None


# --------------------------------------------------------------------------- #
# The provenance line (rigfile.with_provenance)
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(("rig", "channel", "raw", "entry"), CHANNELS, ids=IDS)
def test_the_provenance_line_goes_above_the_chain_and_a_second_replaces_the_first(rig, channel, raw, entry):
    from sushi_rig import rigfile, yamlmerge

    text = frozen(rig).read_text()
    first, problems = rigfile.with_provenance(
        text, {channel: rigfile.provenance_line("pedals", "board", "config/rigs/pedals.yaml", "2026-11-23")})
    assert problems == []
    assert yaml.safe_load(first) == yaml.safe_load(text), "comments only"
    lines = first.splitlines()
    at = next(i for i, line in enumerate(lines) if line.lstrip().startswith(rigfile.PROVENANCE))
    assert "# Chain copied from pedals / board (config/rigs/pedals.yaml), 2026-11-23." in lines[at]
    after = next(line for line in lines[at + 1:] if not line.lstrip().startswith("#"))
    assert after.strip() == "chain:", "directly above the chain"
    assert all(len(line) <= 100 for line in lines[at:at + 4] if line.lstrip().startswith("#"))
    long = "a-rig-with-a-name-long-enough-to-fold-the-line-over-two-of-them-x"
    second, problems = rigfile.with_provenance(
        first, {channel: rigfile.provenance_line(long, "lead", f"config/rigs/{long}.yaml", "2026-11-24")})
    assert problems == [] and "pedals / board" not in second and second.count(rigfile.PROVENANCE) == 1
    assert yamlmerge.merged(second, yaml.safe_load(second)) == second, "the file can still be updated in place"
    assert all(len(line) <= 100 for line in second.splitlines() if line.lstrip().startswith("#"))


def test_a_channel_presets_line_from_before_is_replaced_too():
    """A rig file a preset was loaded into (#127) has its line; the next load
    replaces it rather than stacking a second."""
    from sushi_rig import rigfile

    text = frozen("electric_board").read_text()
    assert "\n  chain:\n" in text
    old = ("  # From channel preset crunch (config/channels/crunch.yaml), 2026-11-17: its chain and level were\n"
           "  #   copied, so editing the preset later changes nothing here.\n")
    before = text.replace("\n  chain:\n", "\n" + old + "  chain:\n", 1)
    out, problems = rigfile.with_provenance(
        before, {"board": rigfile.provenance_line("pedals", "board", "config/rigs/pedals.yaml", "2026-11-23")})
    assert problems == []
    assert "channel preset" not in out and "#   copied, so editing" not in out
    assert out.count(rigfile.PROVENANCE) == 1


def test_a_channel_that_cannot_carry_the_line_is_said_and_left_as_it_was():
    from sushi_rig import rigfile

    text = ("kind: rig\nschema_version: 1\nname: x\nchannels:\n"
            "- {name: a, input: guitar-in, width: mono, chain: []}\nmaster: {output: monitor-out, chain: []}\n")
    out, problems = rigfile.with_provenance(text, {"a": "Chain copied from r / c.", "nope": "Chain copied from r / c."})
    assert out == text and len(problems) == 2
