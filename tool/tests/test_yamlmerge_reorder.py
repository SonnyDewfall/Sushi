"""A name-matched list that is reordered moves each entry with its comments.

yamlmerge matches a chain's entries by `name`. When only some were added or
removed that was always right; when the order changed it used to fall back to
matching by position, so a comment stayed at its line and ended up beside a
different plugin (reviewer, 2026-11-01: `comp  # squash first` / `drive  # the
dirt pedal`, drive moved first, came out `drive  # squash first`). These tests
pin what a move carries: the comment lines above an entry, the blank lines
that were between entries stay where the gaps are, and what follows the last
entry stays last.

They drive `yamlmerge.merged` with small texts rather than a whole rig, so each
case is one shape and the expected text is the whole of it.
"""

from __future__ import annotations

import pytest
import yaml

from sushi_rig import yamlmerge
from sushi_rig.yamlmerge import MergeError, merged


def reorder(text: str, names: list[str], **changes) -> str:
    """`text` with its `chain` list put in the order of `names`, taking each entry from the file as
    it is (so only its identity moves), and `changes` {name: {key: value}} applied on top."""
    data = yaml.safe_load(text)
    by_name = {e["name"]: e for e in data["chain"]}
    data["chain"] = [{**by_name[n], **changes.get(n, {})} if n in by_name else {"name": n}
                     for n in names]
    out = merged(text, data)
    assert yaml.safe_load(out) == data
    # Writing the same data again is a no-op: what was written is what a later write finds.
    assert merged(out, data) == out
    return out


def test_the_example_from_review_each_trailing_comment_goes_with_its_plugin():
    text = "chain:\n  - name: comp  # squash first\n  - name: drive  # the dirt pedal\n"
    assert reorder(text, ["drive", "comp"]) == \
        "chain:\n  - name: drive  # the dirt pedal\n  - name: comp  # squash first\n"


def test_three_entries_rotated_keep_each_lead_comment_and_trailing_comment():
    text = """\
chain:
  # comp lead
  - name: comp
    uri: u1  # u1 note
  # drive lead
  - name: drive
    uri: u2
  # eq lead
  - name: eq  # eq eol
    uri: u3
master: 1
"""
    assert reorder(text, ["drive", "eq", "comp"]) == """\
chain:
  # drive lead
  - name: drive
    uri: u2
  # eq lead
  - name: eq  # eq eol
    uri: u3
  # comp lead
  - name: comp
    uri: u1  # u1 note
master: 1
"""
    # And the other way round.
    assert reorder(text, ["eq", "comp", "drive"]) == """\
chain:
  # eq lead
  - name: eq  # eq eol
    uri: u3
  # comp lead
  - name: comp
    uri: u1  # u1 note
  # drive lead
  - name: drive
    uri: u2
master: 1
"""


def test_the_last_entry_moved_first_goes_under_a_heading_a_blank_line_sets_apart():
    text = """\
chain:
  # The guitar chain.

  # about a
  - name: a
    x: 1

  # about b
  - name: b  # b eol
    x: 2

  - name: c  # c eol
    x: 3
  # after the chain

next: 1
"""
    # The heading stays at the top; a's own comment goes down with a. The blank lines are
    # between entries, not part of any one, so they stay where the gaps are; what follows the
    # chain stays last, now after b.
    assert reorder(text, ["c", "a", "b"]) == """\
chain:
  # The guitar chain.

  - name: c  # c eol
    x: 3

  # about a
  - name: a
    x: 1

  # about b
  - name: b  # b eol
    x: 2
  # after the chain

next: 1
"""


def test_an_entry_with_a_multi_line_lead_comment_takes_every_line_of_it():
    text = """\
chain:
  - name: a
  # why b is here,
  # and the second line of why,
  # and a third
  - name: b
    x: 1
"""
    assert reorder(text, ["b", "a"]) == """\
chain:
  # why b is here,
  # and the second line of why,
  # and a third
  - name: b
    x: 1
  - name: a
"""


def test_one_moved_one_changed_and_one_removed_each_keep_their_own_comments():
    text = """\
chain:
  # a lead
  - name: a
    x: 1  # a note
  # b lead
  - name: b  # b eol
    x: 2
  # c lead
  - name: c
    x: 3
  # d lead
  - {name: d, x: 4}  # d eol
"""
    # c to the front, a's value changed, b gone with its lead and trailing comment.
    assert reorder(text, ["c", "a", "d"], a={"x": 9}) == """\
chain:
  # c lead
  - name: c
    x: 3
  # a lead
  - name: a
    x: 9  # a note
  # d lead
  - {name: d, x: 4}  # d eol
"""


def test_a_flow_entry_moves_with_its_trailing_comment_where_it_was_written():
    text = "chain:\n  - {name: a, x: 1}  # a eol\n  # b lead\n  - {name: b, x: 2}  # b eol\ntail: 1\n"
    assert reorder(text, ["b", "a"]) == \
        "chain:\n  # b lead\n  - {name: b, x: 2}  # b eol\n  - {name: a, x: 1}  # a eol\ntail: 1\n"


def test_a_comment_inside_an_entry_travels_with_it():
    text = """\
chain:
  - name: a
    parameters:
      # about t
      t: 1  # t eol
      u: 2
  - name: b
"""
    assert reorder(text, ["b", "a"]) == """\
chain:
  - name: b
  - name: a
    parameters:
      # about t
      t: 1  # t eol
      u: 2
"""


def test_a_plugin_renamed_while_the_chain_is_reordered_keeps_its_comments():
    text = "chain:\n  - name: a  # a eol\n    uri: ua\n  - name: b  # b eol\n    uri: ub\n"
    data = {"chain": [{"name": "b", "uri": "ub"}, {"name": "a2", "uri": "ua"}]}
    assert merged(text, data) == \
        "chain:\n  - name: b  # b eol\n    uri: ub\n  - name: a2 # a eol\n    uri: ua\n"


def test_a_plugin_added_while_the_chain_is_reordered_gets_no_ones_comments():
    text = "chain:\n  # a lead\n  - name: a  # a eol\n  # b lead\n  - name: b  # b eol\n"
    assert reorder(text, ["b", "n", "a"]) == \
        "chain:\n  # b lead\n  - name: b  # b eol\n  - name: n\n  # a lead\n  - name: a  # a eol\n"


def test_a_removed_first_entry_leaves_its_lead_at_the_top_as_it_does_without_a_reorder():
    text = "chain:\n  # first lead\n  - name: a  # a eol\n  # b lead\n  - name: b\n  - name: c\n"
    # a goes (its trailing comment with it; its lead stays, as when a is removed alone), b and c swap.
    assert reorder(text, ["c", "b"]) == "chain:\n  # first lead\n  - name: c\n  # b lead\n  - name: b\n"


def test_comments_after_the_chain_stay_after_it_whatever_ends_up_last():
    text = "chain:\n  - name: a\n  - name: b  # b eol\n  # about master\n\nmaster: 1\n"
    assert reorder(text, ["b", "a"]) == "chain:\n  - name: b  # b eol\n  - name: a\n  # about master\n\nmaster: 1\n"


def test_a_reorder_inside_a_larger_file_leaves_every_other_line_alone():
    text = """\
# header

kind: rig
channels:
  # the guitar
  - name: guitar
    chain:
      # one
      - name: a  # a eol
      # two
      - name: b  # b eol

  # the vocal
  - name: vocal
    chain:
      # three
      - name: c  # c eol
      # four
      - name: d  # d eol
"""
    data = yaml.safe_load(text)
    data["channels"][1]["chain"].reverse()
    out = merged(text, data)
    assert out == text.replace("""\
      # three
      - name: c  # c eol
      # four
      - name: d  # d eol
""", """\
      # four
      - name: d  # d eol
      # three
      - name: c  # c eol
""")


# --- Pure: who is who ------------------------------------------------------ #

def test_the_pairing_matches_by_name_and_pairs_a_rename_only_when_it_is_the_only_candidate():
    pairing = yamlmerge._pairing
    old = [{"name": "a", "uri": "ua"}, {"name": "b", "uri": "ub"}, {"name": "c", "uri": "uc"}]
    assert pairing(old, [{"name": "c"}, {"name": "a"}, {"name": "b"}]) == [2, 0, 1]
    assert pairing(old, [{"name": "c"}, {"name": "x"}]) == [2, None]
    # `a` renamed to `a2`, same uri: the same plugin.
    assert pairing(old, [{"name": "c", "uri": "uc"}, {"name": "a2", "uri": "ua"}]) == [2, 0]
    # Two plugins on one uri, both renamed: which is which cannot be told, so neither is paired.
    twins = [{"name": "p", "uri": "u"}, {"name": "q", "uri": "u"}, {"name": "z", "uri": "z"}]
    assert pairing(twins, [{"name": "z", "uri": "z"}, {"name": "r", "uri": "u"},
                           {"name": "s", "uri": "u"}]) == [2, None, None]


def test_the_blank_lines_a_gap_opens_with_are_told_from_the_comment_below_them():
    split = yamlmerge._split_gap
    assert split("\n  # c\n") == ("\n", "  # c\n")
    assert split("  # c\n") == ("", "  # c\n")
    assert split("\n\n") == ("\n\n", "")
    assert split("") == ("", "")
    assert split("  # c\n\n  # d\n") == ("", "  # c\n\n  # d\n")


# --- Shapes ruamel.yaml stores its own way --------------------------------- #

def test_flow_entries_without_a_trailing_comment_move_with_their_lead_under_a_heading():
    # Found by the first draft: the line between two flow entries is the *next* entry's
    # "before" comment in ruamel.yaml, and written as the previous one's trailing comment it
    # came out as `- name: d uri: u`, which read back as something else.
    text = """\
chain:
  # heading

  # v lead
  - {name: v}
  # d lead
  - name: d
    uri: u
    b: true
  # a lead
  - {name: a}
"""
    assert reorder(text, ["a", "d", "v"]) == """\
chain:
  # heading

  # a lead
  - {name: a}
  # d lead
  - name: d
    uri: u
    b: true
  # v lead
  - {name: v}
"""


def test_a_lead_after_an_entry_that_ends_in_an_empty_mapping_or_list_moves_too():
    # ruamel.yaml keeps the line after `p: {}` on the list, not on the entry; `_get_after` does not
    # see it there.
    text = "chain:\n  - name: a\n    p: {}\n  # b lead\n  - name: b\n    p: []  # b p eol\n  # tail\nz: 1\n"
    assert reorder(text, ["b", "a"]) == \
        "chain:\n  # b lead\n  - name: b\n    p: []  # b p eol\n  - name: a\n    p: {}\n  # tail\nz: 1\n"


def test_an_entry_ending_in_a_nested_list_or_mapping_moves_with_what_is_inside_it():
    text = """\
chain:
  - name: a
    uris:
      - x  # x eol
      - y
  # b lead
  - name: b  # b eol
    p:
      k: 1
      # about m
      m: 2  # m eol

  # c lead
  - name: c
  # tail

z: 1
"""
    # The blank line was between b and c, the second gap, and the second gap keeps it.
    assert reorder(text, ["c", "b", "a"]) == """\
chain:
  # c lead
  - name: c
  # b lead
  - name: b  # b eol
    p:
      k: 1
      # about m
      m: 2  # m eol

  - name: a
    uris:
      - x  # x eol
      - y
  # tail

z: 1
"""


def test_a_block_scalar_with_nothing_after_it_does_not_stop_a_reorder():
    text = "chain:\n  - name: a\n    note: |\n      hello\n      # text, not a comment\n  - name: b  # b eol\n"
    assert reorder(text, ["b", "a"]) == \
        "chain:\n  - name: b  # b eol\n  - name: a\n    note: |\n      hello\n      # text, not a comment\n"


# --- Refusals ---------------------------------------------------------------- #

def test_a_comment_after_a_block_scalar_is_refused_not_carried_to_the_wrong_plugin():
    # ruamel.yaml keeps the line under a `|` scalar as that scalar's own trailing comment, so the
    # lead of the next plugin cannot be told from a comment about this one. Without the refusal
    # `# b lead` stayed under `a` (the comment count was still right).
    text = "chain:\n  - name: a\n    note: |\n      hello\n  # b lead\n  - name: b\n    note: x\n"
    data = {"chain": [{"name": "b", "note": "x"}, {"name": "a", "note": "hello\n"}]}
    with pytest.raises(MergeError, match="block scalar"):
        merged(text, data)
    # The same list, not reordered, is not affected.
    assert merged(text, yaml.safe_load(text)) == text


def test_a_slot_the_list_keeps_a_comment_in_that_is_not_read_is_refused():
    # No file is known that puts a comment in the list's own third or fourth slot for an entry, so
    # this plants one: a reorder that cannot see where a comment is must not move around it.
    from ruamel.yaml import YAML
    from ruamel.yaml.error import CommentMark
    from ruamel.yaml.tokens import CommentToken

    doc = YAML().load("chain:\n  - name: a\n  - name: b\n")
    doc["chain"].ca.items[1] = [None, None, CommentToken("# unseen\n", CommentMark(2)), None]
    with pytest.raises(MergeError, match="entry 2 has comments in a place"):
        yamlmerge._Update().reorder(doc["chain"], [{"name": "b"}, {"name": "a"}], (doc, "chain"))


def test_a_hand_wrapped_flow_list_with_comments_is_still_refused_before_any_reorder():
    text = "chain: [\n  {name: a},  # a eol\n  # b lead\n  {name: b}  # b eol\n]\nz: 1\n"
    with pytest.raises(MergeError, match="drop"):
        merged(text, {"chain": [{"name": "b"}, {"name": "a"}], "z": 1})


def test_a_list_that_is_reordered_without_comments_is_just_reordered():
    assert merged("chain:\n  - name: a\n  - name: b\n", {"chain": [{"name": "b"}, {"name": "a"}]}) == \
        "chain:\n  - name: b\n  - name: a\n"


# --- The committed rigs ------------------------------------------------------ #

def _above_and_beside(text: str) -> dict[str, list[tuple[tuple[str, ...], str]]]:
    """Each named list entry: the comment lines directly above its line, and the comment on it."""
    import re

    lines = text.splitlines()
    found: dict[str, list] = {}
    for i, line in enumerate(lines):
        m = re.match(r"\s*- \{?name: ([\w.-]+)", line)
        if not m:
            continue
        above, k = [], i - 1
        while k >= 0 and lines[k].strip().startswith("#"):
            above.insert(0, lines[k].strip())
            k -= 1
        found.setdefault(m.group(1), []).append((tuple(above), line[line.index("#"):] if "#" in line else ""))
    return {name: sorted(entries) for name, entries in found.items()}


@pytest.mark.parametrize("how", ["reversed", "rotated"])
def test_every_committed_rig_with_its_chains_reordered_keeps_each_plugins_comments(how):
    from sushi_rig.paths import checkout_root

    rigs = sorted((checkout_root() / "config" / "rigs").glob("*.yaml"))
    assert rigs
    moved = 0
    for path in rigs:
        text = path.read_text()
        data = yaml.safe_load(text)
        chains = [c["chain"] for c in data.get("channels", [])]
        for chain in chains:
            chain[:] = chain[::-1] if how == "reversed" else chain[1:] + chain[:1]
            moved += len(chain) > 1
        out = merged(text, data)
        assert yaml.safe_load(out) == data, path.name
        assert _above_and_beside(out) == _above_and_beside(text), path.name
        assert merged(out, data) == out, path.name
    assert moved, "no committed rig has a chain of two or more plugins, so this proved nothing"
