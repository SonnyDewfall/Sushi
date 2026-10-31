"""`write_rig`: a rig file rewritten by the tool keeps what a person wrote in it.

The reason it exists: `save` will rewrite a rig file's settings after a capture,
and the files are meant to be read and annotated by a person (the comments in
config/rigs/guitar_vocal.yaml are a third of it). PyYAML would drop every one.
So each test here reads the text back, not just the parsed values: a comment is
not in the parsed values, and "the values are right" cannot see it go.

The rig text below is hand-written. What is observed (ruamel.yaml 0.19, checked
while writing yamlmerge.py) and what is a choice:

- observed: ruamel keeps the comment lines between two entries on the entry
  before them, so a naive delete drops the *next* entry's comments;
- observed: ruamel loses a comment line directly above a flow-style `- {...}`
  entry that has its own trailing comment, even with no change at all;
- chosen: the comment above a list's first entry stays when that entry goes.
"""

from __future__ import annotations

import copy
import re

import pytest
import yaml

from sushi_rig.paths import checkout_root
from sushi_rig.rigfile import RigFileError, load_rig, write_rig

EXAMPLE = checkout_root() / "config" / "rigs" / "guitar_vocal.yaml"

# Comments above a channel, inside a chain (above a plugin, beside a value, above
# a parameter), after a plugin, at the end; a blank line between the channels.
TEXT = """\
# A rig for the write tests. Not a real tone.

kind: rig
schema_version: 1
name: demo
description: A demo   # trailing, on the description
channels:
  # The guitar, played from the front input.
  - name: guitar
    input: guitar-in
    width: mono
    chain:
      # Keep the squash light.
      - name: comp
        uri: http://example.org/comp
        parameters:
          threshold: -20.0   # dB; do not go lower
          # The ratio is deliberately gentle.
          ratio: 2.0
      # Dirt, off for the clean tone.
      - name: drive
        uri: http://example.org/drive
        bypassed: true
      # Flat, and meant to stay flat.
      - {name: eq, uri: http://example.org/eq}
      # Last in the chain.
      - {name: verb, uri: http://example.org/verb}

  # The vocal, on its own input.
  - name: vocal
    input: vocal-in
    width: mono
    chain: []
master:
  output: monitor-out
  chain: []
# The very end.
"""

COMMENTS = [line.strip() for line in TEXT.splitlines() if line.strip().startswith("#")] \
    + ["# trailing, on the description", "# dB; do not go lower"]


def comments(text: str) -> list[str]:
    """Each comment in a text, standalone or trailing, in order."""
    found = []
    for line in text.splitlines():
        if line.strip().startswith("#"):
            found.append(line.strip())
        elif "   # " in line:
            found.append(line[line.index("# "):].strip())
    return found


@pytest.fixture
def rig_path(tmp_path):
    path = tmp_path / "demo.yaml"
    path.write_text(TEXT)
    return path


def edited(text: str, change) -> dict:
    rig = yaml.safe_load(text)
    change(rig)
    return rig


def test_a_changed_value_and_a_flipped_flag_keep_every_comment_and_blank_line(rig_path):
    def change(rig):
        chain = rig["channels"][0]["chain"]
        chain[0]["parameters"]["threshold"] = -24.5
        chain[1]["bypassed"] = False

    write_rig(rig_path, edited(TEXT, change))
    after = rig_path.read_text()

    # The values changed ...
    got = yaml.safe_load(after)["channels"][0]["chain"]
    assert got[0]["parameters"] == {"threshold": -24.5, "ratio": 2.0}
    assert got[1]["bypassed"] is False
    # ... and nothing else did: the text differs from before on exactly those two lines.
    changed = [(a, b) for a, b in zip(TEXT.splitlines(), after.splitlines()) if a != b]
    assert changed == [
        ("          threshold: -20.0   # dB; do not go lower",
         "          threshold: -24.5   # dB; do not go lower"),
        ("        bypassed: true", "        bypassed: false"),
    ]
    assert len(after.splitlines()) == len(TEXT.splitlines())
    # Said again in terms of what the test is for: every comment, in order, and the blank line.
    assert comments(after) == comments(TEXT)
    assert "\n\n  # The vocal, on its own input.\n" in after
    assert after.startswith("# A rig for the write tests. Not a real tone.\n\nkind: rig\n")
    assert after.endswith("# The very end.\n")


def test_a_plugin_added_and_another_removed_leave_the_others_comments_alone(rig_path):
    def change(rig):
        chain = rig["channels"][0]["chain"]
        del chain[1]                                                   # drive, with its comment
        chain.insert(2, {"name": "delay", "uri": "http://example.org/delay"})  # between eq and verb

    write_rig(rig_path, edited(TEXT, change))
    after = rig_path.read_text()

    names = [p["name"] for p in yaml.safe_load(after)["channels"][0]["chain"]]
    assert names == ["comp", "eq", "delay", "verb"]
    # The comments of the plugins that stayed are still above them ...
    lines = after.splitlines()
    for comment, plugin in (("# Keep the squash light.", "- name: comp"),
                            ("# Flat, and meant to stay flat.", "- {name: eq"),
                            ("# Last in the chain.", "- {name: verb")):
        i = lines.index(next(l for l in lines if l.strip() == comment))
        assert lines[i + 1].strip().startswith(plugin), (comment, lines[i + 1])
    # ... the parameter comments in comp are untouched ...
    assert "# dB; do not go lower" in after and "# The ratio is deliberately gentle." in after
    # ... the removed plugin's own comment went with it, and no other did.
    assert "# Dirt, off for the clean tone." not in after
    assert [c for c in comments(TEXT) if c != "# Dirt, off for the clean tone."] == comments(after)
    # The new entry is written like its neighbours.
    assert "      - {name: delay, uri: http://example.org/delay}\n" in after
    # The blank line between the channels and the closing comment are where they were.
    assert "\n\n  # The vocal, on its own input.\n" in after
    assert after.endswith("# The very end.\n")


def test_the_first_plugin_can_go_and_a_new_one_can_come_last(rig_path):
    def change(rig):
        chain = rig["channels"][0]["chain"]
        del chain[0]
        chain.append({"name": "limiter", "uri": "http://example.org/limiter"})

    write_rig(rig_path, edited(TEXT, change))
    after = rig_path.read_text()
    assert [p["name"] for p in yaml.safe_load(after)["channels"][0]["chain"]] == \
        ["drive", "eq", "verb", "limiter"]
    # comp's comments on its parameters went with comp; everyone else's stayed. So did
    # the one above the chain's first entry: it is as likely to be about the chain.
    gone = {"# dB; do not go lower", "# The ratio is deliberately gentle."}
    assert [c for c in comments(TEXT) if c not in gone] == comments(after)


def test_a_key_added_to_a_plugin_and_one_removed_keep_the_comments(rig_path):
    def change(rig):
        comp = rig["channels"][0]["chain"][0]
        comp["bypassed"] = True
        del comp["parameters"]["ratio"]
        comp["parameters"]["makeup"] = 3.0

    write_rig(rig_path, edited(TEXT, change))
    after = rig_path.read_text()
    assert yaml.safe_load(after)["channels"][0]["chain"][0] == {
        "name": "comp", "uri": "http://example.org/comp",
        "parameters": {"threshold": -20.0, "makeup": 3.0}, "bypassed": True}
    assert "# dB; do not go lower" in after
    assert "# The ratio is deliberately gentle." not in after  # it was written above ratio
    assert [c for c in comments(TEXT) if c != "# The ratio is deliberately gentle."] == comments(after)


def test_an_unchanged_rig_leaves_the_file_unchanged(rig_path):
    write_rig(rig_path, yaml.safe_load(TEXT))
    assert rig_path.read_text() == TEXT


def test_a_rig_written_to_a_path_with_nothing_there_reads_back_equal(tmp_path):
    rig = yaml.safe_load(TEXT)
    rig["description"] = "Quote: 'this', and a colon: here"
    rig["channels"][0]["chain"][0]["parameters"]["On"] = 1.0         # `On` is a bool to PyYAML
    rig["channels"][0]["chain"][0]["parameters"]["tiny"] = 1e-05     # `1e-05` is a str to PyYAML
    rig["meta"] = {"version": "1.0", "status": "yes"}                # both strings
    path = tmp_path / "sub" / "fresh.yaml"
    write_rig(path, rig, source="config/demo.json")
    assert yaml.safe_load(path.read_text()) == rig
    assert load_rig(path).name == "demo"
    text = path.read_text()
    assert text.startswith("# Converted from config/demo.json by `sushi-rig convert`")
    # Keys in the dict's order, block style, lists at the key's own indent.
    assert text.index("\nkind: rig\n") < text.index("\nname: demo\n") < text.index("\nchannels:\n")
    assert "\nchannels:\n- name: guitar\n" in text
    assert not list(path.parent.glob("*.tmp"))


def test_without_a_source_a_fresh_file_has_no_header(tmp_path):
    write_rig(tmp_path / "a.yaml", yaml.safe_load(TEXT))
    assert (tmp_path / "a.yaml").read_text().startswith("kind: rig\n")


def test_the_committed_example_written_back_over_a_copy_is_byte_identical(tmp_path):
    copy_ = tmp_path / "guitar_vocal.yaml"
    copy_.write_text(EXAMPLE.read_text())
    write_rig(copy_, yaml.safe_load(EXAMPLE.read_text()))
    assert copy_.read_bytes() == EXAMPLE.read_bytes()


def test_the_committed_example_keeps_its_comments_when_a_chain_changes(tmp_path):
    path = tmp_path / "guitar_vocal.yaml"
    path.write_text(EXAMPLE.read_text())
    rig = yaml.safe_load(EXAMPLE.read_text())
    del rig["channels"][1]["chain"][2]                                  # vocal_eq
    rig["channels"][0]["chain"][1]["bypassed"] = True                   # guitar_overdrive
    write_rig(path, rig)
    after = path.read_text()
    assert load_rig(path).channels[1].chain[2].name == "vocal_reverb"
    # The comments above both chains stay with the chains, even though one lost a plugin ...
    assert "# The electric_board chain, shortened" in after
    assert "# Gate, compressor, sibilance, tone, space, ceiling" in after
    # ... and so do all of the header's.
    assert comments(after) == comments(EXAMPLE.read_text())
    # The new flag sits in the flow-style entry it belongs to, quotes and all.
    assert ('      - {name: guitar_overdrive, uri: "http://guitarix.sourceforge.net/plugins/gxts9#ts9sim", '
            'bypassed: true}\n') in after
    assert "vocal_eq" not in after


def test_a_reordered_chain_is_matched_by_position_and_loses_no_comment(rig_path):
    def change(rig):
        chain = rig["channels"][0]["chain"]
        chain[0], chain[3] = chain[3], chain[0]

    write_rig(rig_path, edited(TEXT, change))
    after = rig_path.read_text()
    assert [p["name"] for p in yaml.safe_load(after)["channels"][0]["chain"]] == \
        ["verb", "drive", "eq", "comp"]
    assert comments(after)[:3] == comments(TEXT)[:3]


def test_the_layout_a_file_was_written_in_is_the_layout_it_keeps(tmp_path):
    # Dashes under their key (what `convert` writes) rather than inside it.
    rig = yaml.safe_load(TEXT)
    path = tmp_path / "flush.yaml"
    write_rig(path, rig)
    assert "\nchannels:\n- name: guitar\n" in path.read_text()
    path.write_text(path.read_text().replace("kind: rig\n", "# first\nkind: rig\n"))
    rig["channels"][0]["chain"][0]["parameters"]["ratio"] = 3.0
    write_rig(path, rig)
    after = path.read_text()
    assert after.startswith("# first\nkind: rig\n")
    assert "\nchannels:\n- name: guitar\n" in after
    assert "ratio: 3.0" in after


def test_a_new_string_that_pyyaml_would_misread_is_quoted(rig_path):
    rig = yaml.safe_load(TEXT)
    rig["description"] = "no"
    rig["meta"] = {"version": "1.0"}
    write_rig(rig_path, rig)
    assert yaml.safe_load(rig_path.read_text()) == rig
    assert comments(rig_path.read_text())[0] == "# A rig for the write tests. Not a real tone."


# --------------------------------------------------------------------------- #
# What it refuses
# --------------------------------------------------------------------------- #

def test_a_rig_load_rig_would_refuse_is_not_written(tmp_path):
    rig = yaml.safe_load(TEXT)
    rig["channels"][0]["width"] = "quadraphonic"
    path = tmp_path / "bad.yaml"
    with pytest.raises(RigFileError, match="width"):
        write_rig(path, rig)
    assert not path.exists()


def test_a_file_that_is_not_yaml_is_refused_and_left_alone(tmp_path):
    path = tmp_path / "broken.yaml"
    path.write_text("kind: [unclosed\n")
    with pytest.raises(RigFileError, match="not YAML"):
        write_rig(path, yaml.safe_load(TEXT))
    assert path.read_text() == "kind: [unclosed\n"


def test_a_file_that_is_not_a_mapping_is_refused_and_left_alone(tmp_path):
    path = tmp_path / "list.yaml"
    path.write_text("- a\n- b\n")
    with pytest.raises(RigFileError, match="not a mapping"):
        write_rig(path, yaml.safe_load(TEXT))
    assert path.read_text() == "- a\n- b\n"


def test_a_comment_ruamel_would_drop_is_refused_not_dropped(tmp_path):
    # Observed: after a flow-style entry, a comment line above the next flow-style entry is
    # lost if that one has a trailing comment of its own, with no change made at all.
    text = TEXT.replace("      # Last in the chain.\n      - {name: verb, uri: http://example.org/verb}\n",
                        "      # Last in the chain.\n      - {name: verb, uri: http://example.org/verb}   # wet\n")
    path = tmp_path / "lossy.yaml"
    path.write_text(text)
    with pytest.raises(RigFileError, match="would drop 1 comment"):
        write_rig(path, yaml.safe_load(text))
    assert path.read_text() == text


def test_the_first_key_of_a_mapping_cannot_go_while_comments_sit_below_it(tmp_path):
    text = TEXT.replace("          threshold: -20.0   # dB; do not go lower\n",
                        "          threshold: -20.0\n          # a note on the ratio\n")
    path = tmp_path / "first.yaml"
    path.write_text(text)
    rig = yaml.safe_load(text)
    del rig["channels"][0]["chain"][0]["parameters"]["threshold"]
    with pytest.raises(RigFileError, match="first key"):
        write_rig(path, rig)
    assert path.read_text() == text


def test_the_dict_given_is_not_changed(rig_path):
    rig = yaml.safe_load(TEXT)
    rig["channels"][0]["chain"][0]["parameters"]["threshold"] = -30.0
    before = copy.deepcopy(rig)
    write_rig(rig_path, rig)
    assert rig == before


# --------------------------------------------------------------------------- #
# The shapes `save` will produce most: a chain's last plugin removed, a channel
# appended, an entry whose last key is an empty list. Each was wrong once.
# --------------------------------------------------------------------------- #

def example_text() -> str:
    return EXAMPLE.read_text()


def written(tmp_path, text: str, change) -> str:
    """`text` through write_rig with `change` applied to its parsed rig; the text out."""
    path = tmp_path / "rig.yaml"
    path.write_text(text)
    write_rig(path, edited(text, change))
    return path.read_text()


def test_removing_a_chains_last_plugin_does_not_duplicate_what_follows_it(tmp_path):
    after = written(tmp_path, TEXT, lambda rig: rig["channels"][0]["chain"].pop())
    assert [p["name"] for p in yaml.safe_load(after)["channels"][0]["chain"]] == ["comp", "drive", "eq"]
    # Everything after the chain is where it was, once: the blank line, the comment, the rest.
    assert after == TEXT.replace(
        "      # Last in the chain.\n      - {name: verb, uri: http://example.org/verb}\n", "")
    assert comments(after).count("# The vocal, on its own input.") == 1


def test_removing_the_last_plugin_of_the_example_leaves_the_blank_line_before_master_single(tmp_path):
    after = written(tmp_path, example_text(), lambda rig: rig["channels"][1]["chain"].pop())
    assert after == example_text().replace(
        '      - {name: vocal_limiter, uri: "http://drobilla.net/plugins/mda/Limiter"}\n', "")
    # And a second rewrite of the result changes nothing: it does not compound.
    path = tmp_path / "rig.yaml"
    write_rig(path, yaml.safe_load(after))
    assert path.read_text() == after


def test_appending_a_channel_with_an_empty_chain_is_valid_yaml_and_keeps_the_comment(tmp_path):
    bass = {"name": "bass", "input": "bass-in", "width": "mono", "chain": []}
    after = written(tmp_path, example_text(), lambda rig: rig["channels"].append(bass))
    assert [c["name"] for c in yaml.safe_load(after)["channels"]] == ["guitar", "vocal", "bass"]
    assert "  - name: bass\n    input: bass-in\n    width: mono\n    chain: []\n" in after
    assert comments(after) == comments(example_text())
    assert "\n\nmaster:\n" in after


def test_a_plugin_added_to_an_empty_chain_and_an_empty_one_added_to_a_plugin(tmp_path):
    def change(rig):
        rig["channels"][1]["chain"].append({"name": "gate", "uri": "http://example.org/gate"})
        rig["channels"][0]["chain"].insert(
            1, {"name": "bare", "uri": "http://example.org/bare", "parameters": {}})

    after = written(tmp_path, TEXT, change)
    got = yaml.safe_load(after)["channels"]
    assert [p["name"] for p in got[0]["chain"]] == ["comp", "bare", "drive", "eq", "verb"]
    assert got[0]["chain"][1]["parameters"] == {}
    assert [p["name"] for p in got[1]["chain"]] == ["gate"]
    assert comments(after) == comments(TEXT)


def test_a_write_that_produces_text_that_is_not_yaml_is_a_rig_file_error(tmp_path, monkeypatch):
    from sushi_rig import yamlmerge

    monkeypatch.setattr(yamlmerge, "fresh", lambda data, header="": "kind: [unclosed\n")
    path = tmp_path / "out.yaml"
    with pytest.raises(RigFileError, match="read back"):
        write_rig(path, yaml.safe_load(TEXT))
    assert not path.exists()


def test_convert_force_says_fail_instead_of_a_traceback_when_the_write_cannot_be_read_back(
        tmp_path, monkeypatch, capsys):
    from sushi_rig import yamlmerge
    from sushi_rig.cli import main

    monkeypatch.setattr(yamlmerge, "fresh", lambda data, header="": "kind: [unclosed\n")
    out = tmp_path / "clean.yaml"
    assert main(["convert", str(checkout_root() / "config" / "electric-clean.json"),
                 "-o", str(out), "--force"]) == 1
    assert "FAIL" in capsys.readouterr().out


def test_a_long_comment_elsewhere_does_not_reflow_an_unchanged_scalar(tmp_path):
    # A converted rig, whose description is folded at 100 columns, plus one long comment line.
    path = tmp_path / "accustomed.yaml"
    text = (checkout_root() / "config" / "rigs" / "accustomed.yaml").read_text()
    text = text.replace("kind: rig\n", "# " + "long " * 26 + "\nkind: rig\n", 1)
    assert max(len(l) for l in text.splitlines()) > 125
    path.write_text(text)
    write_rig(path, yaml.safe_load(text))
    assert path.read_text() == text


def test_a_long_flow_line_and_a_folded_description_together_stay_as_they_were(tmp_path):
    # guitar_vocal.yaml has flow lines of 109 columns; give it a description folded at 100,
    # as `convert` writes them. No single "longest line" width keeps both.
    folded = re.search(r"^description: .*\n  .*\n", (checkout_root() / "config" / "rigs" / "accustomed.yaml").read_text(),
                       re.M).group(0)
    text = example_text().replace(
        "description: Guitar and vocals — two channels, two chains, one monitor mix\n", folded)
    assert text != example_text()
    path = tmp_path / "a.yaml"
    path.write_text(text)
    write_rig(path, yaml.safe_load(text))
    assert path.read_text() == text


def test_a_key_inserted_first_leaves_the_comment_above_the_old_first_key(tmp_path):
    text = TEXT.replace("          threshold: -20.0   # dB; do not go lower\n", "")

    def change(rig):
        params = rig["channels"][0]["chain"][0]["parameters"]
        rig["channels"][0]["chain"][0]["parameters"] = {"threshold": -20.0, **params}

    after = written(tmp_path, text, change)
    lines = after.splitlines()
    i = lines.index("          # The ratio is deliberately gentle.")
    assert lines[i - 1].strip() == "threshold: -20.0"
    assert lines[i + 1].strip() == "ratio: 2.0"


def test_a_list_item_that_opens_with_a_mapping_and_a_comment_on_a_key_keep_the_files_indent(tmp_path):
    # `- strip:` is a list item, so what is nested below it steps from "strip", not from the
    # dash; and `channels:   # ...` is still the key whose list this is.
    text = TEXT.replace("channels:\n", "channels:   # one per player\n", 1).replace(
        "  - name: guitar\n    input: guitar-in\n    width: mono\n",
        "  - strip:\n      gain: 0.833333\n      pan: 0.5\n    name: guitar\n"
        "    input: guitar-in\n    width: mono\n", 1)
    path = tmp_path / "rig.yaml"
    path.write_text(text)
    parsed = yaml.safe_load(text)
    write_rig(path, parsed)
    assert path.read_text() == text
    parsed["channels"][0]["chain"][0]["bypassed"] = True
    write_rig(path, parsed)
    # The new key follows the one before it in the rig (parameters), and the comment written
    # above the next plugin stays above that plugin.
    assert path.read_text() == text.replace(
        "          ratio: 2.0\n", "          ratio: 2.0\n        bypassed: true\n", 1)


def test_an_update_that_would_duplicate_or_drop_a_comment_is_refused(tmp_path, monkeypatch):
    from sushi_rig import yamlmerge

    real = yamlmerge._tokens
    monkeypatch.setattr(yamlmerge, "_tokens", lambda text: real(text) + real(text))
    path = tmp_path / "rig.yaml"
    path.write_text(TEXT)
    rig = yaml.safe_load(TEXT)
    rig["channels"][0]["chain"].pop()
    with pytest.raises(RigFileError, match="comment"):
        write_rig(path, rig)
    assert path.read_text() == TEXT


def test_a_renamed_plugin_keeps_its_comment_and_its_block_layout(tmp_path):
    def change(rig):
        rig["channels"][0]["chain"][1]["name"] = "fuzz"

    after = written(tmp_path, TEXT, change)
    assert after == TEXT.replace("name: drive", "name: fuzz")


def test_a_plugin_inserted_beside_block_neighbours_is_a_block_and_beside_flow_ones_a_flow(tmp_path):
    def change(rig):
        chain = rig["channels"][0]["chain"]
        chain.insert(1, {"name": "boost", "uri": "http://example.org/boost"})
        chain.insert(4, {"name": "delay", "uri": "http://example.org/delay"})

    after = written(tmp_path, TEXT, change)
    assert "      - name: boost\n        uri: http://example.org/boost\n" in after
    assert "      - {name: delay, uri: http://example.org/delay}\n" in after
    assert comments(after) == comments(TEXT)


def test_a_chain_can_be_emptied_when_nothing_follows_it_and_is_refused_when_comments_do(tmp_path):
    # Where comments follow it, ruamel.yaml would drop them after the `[]`: refused, loudly.
    path = tmp_path / "rig.yaml"
    path.write_text(TEXT)
    rig = yaml.safe_load(TEXT)
    rig["channels"][0]["chain"] = []
    with pytest.raises(RigFileError, match="only entry"):
        write_rig(path, rig)
    assert path.read_text() == TEXT

    # The vocal is the last channel; its chain is emptied of the one plugin it was given.
    text = TEXT.replace("    chain: []\nmaster:", "    chain:\n      # Gate.\n"
                        "      - {name: gate, uri: http://example.org/gate}\nmaster:", 1)
    assert text != TEXT
    after = written(tmp_path, text, lambda rig: rig["channels"][1].update(chain=[]))
    assert yaml.safe_load(after)["channels"][1]["chain"] == []
    assert "# Gate." not in after and after.endswith("master:\n  output: monitor-out\n  chain: []\n# The very end.\n")
