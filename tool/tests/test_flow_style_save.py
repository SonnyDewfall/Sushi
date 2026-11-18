"""Saving a rig whose chain entries are written in flow style, twice (#163).

guitar_vocal's chains are `- {name: ..., uri: "..."}` with no parameters.
A save writes the capture's parameters into those entries; the second save
must be able to update what the first wrote. It could not: the first save
built the new `parameters` inside the flow entries, ruamel.yaml folded them
across lines at the original file's width (its widest line, 109 columns),
and the folded file's widest line (121) is not that width, so the second
save could not find a layout that gave the file back and refused it ("layout
cannot be reproduced").

Against the frozen guitar_vocal in a temporary checkout
(rig_fixtures.frozen_checkout, never config/rigs/), through the agent's
routes as test_agent_presets.py drives them: the real compile, the real
capture of `test_agent.RigLive`, `save.save_rig` and `rigfile.write_rig`.
NOT shown: any of it against a live Sushi.
"""

from __future__ import annotations

import pytest
import yaml

pytest.importorskip("aiohttp")

from sushi_rig import rigfile, yamlmerge  # noqa: E402

from rig_fixtures import FROZEN, frozen  # noqa: E402
from test_agent_presets import call, make_agent, rig_file, root  # noqa: E402,F401


def comments(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip().startswith("#")]


def reproducible(text: str) -> bool:
    """Whether the next update can be made to this text: an update that
    changes nothing gives it back byte for byte."""
    return yamlmerge.merged(text, yaml.safe_load(text)) == text


# The vocal chain's plugins as the rig has them: each with parameters, so a
# save gives every vocal entry some, as it gives the guitar's. `test_agent`'s
# fake knows none of these plugins' (no frozen rig sets them). MVerb's and
# 3BandEQ's names are their Turtle's (tests/fixtures/lv2/); the mda ones'
# are not in any fixture and are written as the plugins name them, unchecked
# here. Spaces and a slash in a name are the point: a fold can land inside one.
VOCAL_PARAMETERS = {
    "vocal_dynamics": {"Thresh": 0.6, "Ratio": 0.4, "Output": 0.5, "Attack": 0.18, "Release": 0.55,
                       "Limiter": 1.0, "Gate Thr": 0.123456, "Gate Att": 0.1, "Gate Rel": 0.5, "Mix": 1.0},
    "vocal_deess": {"Thresh": 0.15, "Freq": 0.6, "HF Drive": 0.5},
    "vocal_eq": {"Low": 0.0, "Mid": -3.5, "High": 2.0, "Master": 0.0, "Low-Mid Freq": 440.0,
                 "Mid-High Freq": 2000.0},
    "vocal_reverb": {"Damping": 0.5, "Density": 0.5, "Bandwidth": 0.5, "Decay": 0.5, "Predelay": 0.0001,
                     "Size": 0.75, "Gain": 1.0, "Mix": 0.15, "Early/Late Mix": 0.5},
    "vocal_limiter": {"Thresh": 0.6, "Output": 0.7, "Attack": 0.15, "Release": 0.5, "Knee": 0.0},
}


def knowing_the_vocal(fake):
    for name, values in VOCAL_PARAMETERS.items():
        fake.values[name] = dict(values)
    return fake


def set_value(agent, fake, processor, value):
    parameter = sorted(fake.values[processor])[0]
    status, body = call(agent, "POST", "/live/parameter",
                        json={"processor": processor, "parameter": parameter, "value": value})
    assert status == 200, body
    return parameter


def test_a_flow_style_rig_saves_again_and_again_and_each_save_can_be_updated_by_the_next(root):
    before = rig_file(root, "guitar_vocal").read_text()
    assert before == frozen("guitar_vocal").read_text()
    assert "      - {name: guitar_delay, uri:" in before, "the case: flow-style entries with no parameters"
    agent, world, fake, runner = make_agent(root)
    knowing_the_vocal(fake)

    # As it plays: every captured parameter is written into entries that had none,
    # all eleven of them (review of PR #169: one entry moved while another was
    # still folded inside a key was blamed on the move, and the save refused).
    status, body = call(agent, "POST", "/rig/save", json={})
    assert status == 200, body
    once = rig_file(root, "guitar_vocal").read_text()
    assert reproducible(once), "the first save wrote a layout the next cannot reproduce"
    assert all(not line.endswith(" ") for line in once.splitlines()), "a line folded mid-entry"
    assert comments(once) == comments(before)

    # The issue's case: the second save was refused, "layout cannot be reproduced".
    delay = set_value(agent, fake, "guitar_delay", 0.75)
    status, body = call(agent, "POST", "/rig/save", json={})
    assert status == 200, body
    twice = rig_file(root, "guitar_vocal").read_text()
    assert reproducible(twice)
    assert comments(twice) == comments(before)
    changed = [(a, b) for a, b in zip(once.splitlines(), twice.splitlines()) if a != b]
    assert len(once.splitlines()) == len(twice.splitlines()) and len(changed) == 1, changed
    assert changed[0][1].strip() == f"{delay}: 0.75", "a save that changed one value changes one line"

    # One character shorter moved the fold into a key, and a save of that
    # was refused as text that would not read back. Folded no more.
    set_value(agent, fake, "guitar_delay", 0.25)
    status, body = call(agent, "POST", "/rig/save", json={})
    assert status == 200, body
    thrice = rig_file(root, "guitar_vocal").read_text()
    assert reproducible(thrice)
    assert comments(thrice) == comments(before)
    rigfile.load_rig(rig_file(root, "guitar_vocal"))  # a rig file still
    saved = {p["name"]: p for p in yaml.safe_load(thrice)["channels"][0]["chain"]}
    assert saved["guitar_delay"]["parameters"][delay] == 0.25

    # An entry given parameters is written in block style, one setting a line,
    # as every other rig file writes one, its quotes as the person wrote them.
    assert ('      - name: guitar_delay\n        uri: "http://plugin.org.uk/swh-plugins/lcrDelay"\n'
            "        parameters:\n") in thrice
    assert "{name: " not in thrice
    assert ('      - name: vocal_reverb\n        uri: "http://distrho.sf.net/plugins/MVerb"\n'
            "        parameters:\n") in thrice and "          Early/Late Mix: 0.5\n" in thrice
    # Everything outside the chains is as it was.
    head, tail = before.index("channels:"), before.index("master:")
    assert thrice[:head] == before[:head] and thrice.endswith(before[tail:])


# --------------------------------------------------------------------------- #
# The other writers that share the path (#162's presets, revert, new and copy)
# --------------------------------------------------------------------------- #

def save_twice(agent, fake, root, rig="guitar_vocal", processor="guitar_delay"):
    """Two saves of the running rig, a value moved before each: both written,
    each leaving a file the next can update, the second changing one line."""
    texts = []
    for value in (0.75, 0.25):
        set_value(agent, fake, processor, value)
        status, body = call(agent, "POST", "/rig/save", json={})
        assert status == 200, body
        texts.append(rig_file(root, rig).read_text())
        assert reproducible(texts[-1])
    changed = [(a, b) for a, b in zip(*(t.splitlines() for t in texts)) if a != b]
    assert len(changed) == 1, changed
    return texts[-1]


def test_a_channel_saved_as_a_preset_and_loaded_back_saves_twice(root):
    before = rig_file(root, "guitar_vocal").read_text()
    agent, world, fake, runner = make_agent(root)
    status, body = call(agent, "POST", "/rig/channels/guitar/save-as", json={"name": "gv-guitar"})
    assert status == 200, body
    preset = (root / "config" / "channels" / "gv-guitar.yaml").read_text()
    assert reproducible(preset)
    # Saved again over itself, from a rig whose chain is flow style.
    set_value(agent, fake, "guitar_delay", 0.75)
    status, body = call(agent, "POST", "/rig/channels/guitar/save-as", json={"name": "gv-guitar", "replace": True})
    assert status == 200, body
    assert reproducible((root / "config" / "channels" / "gv-guitar.yaml").read_text())
    status, body = call(agent, "POST", "/rig/save", json={})
    assert status == 200, body
    saved = rig_file(root, "guitar_vocal").read_text()
    # Loaded onto the vocal (`replace_chains`), whose entries are still flow style.
    assert '      - {name: vocal_dynamics, uri: "http://drobilla.net/plugins/mda/Dynamics"}\n' in saved
    status, body = call(agent, "POST", "/rig/channels/vocal/load", json={"preset": "gv-guitar"})
    assert status == 200, body
    loaded = rig_file(root, "guitar_vocal").read_text()
    assert reproducible(loaded)

    def guitar(text):
        return text[text.index("  - name: guitar\n"):text.index("  - name: vocal\n")]

    assert guitar(loaded) == guitar(saved), "the guitar's channel untouched by the load"
    after = save_twice(agent, fake, root)
    # The vocal's chain went whole, with the comments inside it (#162); every other stays.
    vocal = [c for c in comments(before) if before.index(c) > before.index("  - name: vocal\n")
             and before.index(c) < before.index("master:")]
    assert len(vocal) == 3 and [c for c in comments(after) if c in comments(before)] == \
        [c for c in comments(before) if c not in vocal]


def test_a_save_reverted_and_saved_again_saves_twice(root):
    """Back to the committed file (flow style, `commit:`), and back to what
    a save wrote (its snapshot): each written byte for byte, and saved twice."""
    before = rig_file(root, "guitar_vocal").read_bytes()
    agent, world, fake, runner = make_agent(root)
    set_value(agent, fake, "guitar_delay", 0.5)
    status, body = call(agent, "POST", "/rig/save", json={})
    assert status == 200, body
    saved = rig_file(root, "guitar_vocal").read_bytes()
    status, listed = call(agent, "GET", "/rigs/guitar_vocal/saves")
    assert status == 200, listed
    points = {p["kind"]: p["id"] for p in listed["points"]}
    for point, expected in ((points["commit"], before), (points["save"], saved)):
        status, body = call(agent, "POST", "/rigs/guitar_vocal/revert", json={"save": point})
        assert status == 200, body
        assert rig_file(root, "guitar_vocal").read_bytes() == expected, "a revert is the file as it was"
        save_twice(agent, fake, root)


def test_a_new_rig_and_a_channel_copied_from_a_flow_style_rig_are_block_style_and_write_twice(root):
    """A guard, not a regression test: rig new and a copied channel write
    block style throughout, so they were never folded (this passes on
    f6303f5). It keeps them so."""
    agent, world, fake, runner = make_agent(root)
    status, body = call(agent, "POST", "/rigs/new", json={"name": "gv-copy", "channels": [
        {"name": "lead", "input": "guitar-in", "width": "mono", "from": {"rig": "guitar_vocal", "channel": "guitar"}}]})
    assert status == 200, body
    path = rig_file(root, "gv-copy")
    assert reproducible(path.read_text())
    status, body = call(agent, "POST", "/rigs/gv-copy/channels/add", json={
        "name": "voice", "input": "vocal-in", "width": "mono", "from": {"rig": "guitar_vocal", "channel": "vocal"}})
    assert status == 200, body
    assert reproducible(path.read_text())
    # A save's write into it, twice (the rig is not running, so `write_rig` as SAVE calls it).
    for value in (0.75, 0.25):
        raw = yaml.safe_load(path.read_text())
        for channel in raw["channels"]:
            for entry in channel["chain"]:
                entry["parameters"] = {**entry.get("parameters", {}), "Level": value}
        rigfile.write_rig(path, raw)
        assert reproducible(path.read_text())
    rigfile.load_rig(path)


# --------------------------------------------------------------------------- #
# The rule, in yamlmerge (`_outgrown_to_block`), on hand-written text
# --------------------------------------------------------------------------- #

FLOW = """\
chain:
  # heading
  - {name: a, uri: "x#y"}   # about a
  # above b
  - {name: b, uri: q, parameters: {X: 1}}
  # above c
  - {name: c, uri: z}

# after the chain
tail: 1
"""


def updated(text, change):
    data = yaml.safe_load(text)
    change(data)
    out = yamlmerge.merged(text, data)
    assert reproducible(out) and yaml.safe_load(out) == data
    return out


def test_an_entry_that_gains_parameters_is_a_block_with_its_comments_where_they_were():
    out = updated(FLOW, lambda d: d["chain"][0].update(parameters={"P": 1, "Q": 2}))
    assert out == FLOW.replace('  - {name: a, uri: "x#y"}   # about a\n',
                               '  - name: a   # about a\n    uri: "x#y"\n    parameters:\n      P: 1\n      Q: 2\n')
    # The last entry, with the blank line and the comment after the list.
    out = updated(FLOW, lambda d: d["chain"][2].update(parameters={"P": 1}))
    assert out == FLOW.replace("  - {name: c, uri: z}\n", "  - name: c\n    uri: z\n    parameters:\n      P: 1\n")


def test_a_flow_entry_that_still_fits_keeps_its_style():
    out = updated(FLOW, lambda d: d["chain"][1]["parameters"].update(X=2))
    assert out == FLOW.replace("{X: 1}", "{X: 2}")
    out = updated(FLOW, lambda d: d["chain"][2].update(bypassed=True))
    assert out == FLOW.replace("{name: c, uri: z}", "{name: c, uri: z, bypassed: true}")


def test_a_flow_entry_a_scalar_pushes_past_the_width_is_a_block_rather_than_folded():
    uri = "http://guitarix.sourceforge.net/plugins/gx_chorus_stereo#_chorus_stereo_x"
    text = FLOW.replace("uri: z}", f'uri: "{uri}"}}')
    assert max(len(line) for line in text.splitlines()) <= yamlmerge.FRESH_WIDTH
    out = updated(text, lambda d: d["chain"][2].update(bypassed=True))
    assert out == text.replace(f'  - {{name: c, uri: "{uri}"}}\n',
                               f'  - name: c\n    uri: "{uri}"\n    bypassed: true\n')


def test_an_entry_ruamel_would_write_wrongly_as_a_block_is_left_in_flow_style():
    # ruamel.yaml 0.19 runs a block entry's keys together after a flow entry
    # with its own trailing comment, in a list with other comments: a new
    # entry there keeps the flow style its neighbours have, as before #163.
    out = updated(FLOW, lambda d: d["chain"].insert(1, {"name": "n", "uri": "u", "parameters": {"P": 1}}))
    assert "  - {name: a, uri: \"x#y\"}   # about a\n  - {name: n, uri: u, parameters: {P: 1}}\n" in out
    # Without the trailing comment before it, it is a block like any entry with settings.
    out = updated(FLOW.replace("   # about a", ""),
                  lambda d: d["chain"].insert(1, {"name": "n", "uri": "u", "parameters": {"P": 1}}))
    assert '  - {name: a, uri: "x#y"}\n  - name: n\n    uri: u\n    parameters:\n      P: 1\n  # above b\n' in out


def test_a_flow_entry_nobody_changed_is_left_however_it_is_written():
    text = FLOW.replace("{name: b, uri: q, parameters: {X: 1}}", "{name: b, uri: q, parameters: {X: 1, Y: 2, Z: 3}}")
    out = updated(text, lambda d: d.update(tail=2))
    assert out == text.replace("tail: 1", "tail: 2")


def test_inline_parameters_that_outgrow_the_line_are_a_block_not_folded_inside_a_key():
    # Folded, `C delay (ms)` and its neighbours were split across lines, and
    # PyYAML could not read the text back at all.
    text = 'chain:\n  - {name: a, uri: "http://plugin.org.uk/swh-plugins/lcrDelay", parameters: {C delay (ms): 0.5}}\n'
    out = updated(text, lambda d: d["chain"][0]["parameters"].update({f"L delay {i} (ms)": 0.25 for i in range(6)}))
    assert out.startswith('chain:\n  - name: a\n    uri: "http://plugin.org.uk/swh-plugins/lcrDelay"\n'
                          "    parameters:\n      C delay (ms): 0.5\n      L delay 0 (ms): 0.25\n")


def test_every_entry_of_guitar_vocal_gaining_parameters_is_written_at_once():
    # The reviewer's probe, as a test: values of every length, so folds land
    # everywhere, inside keys too, on a first save of the whole rig. 3 of 60
    # were refused before (review of PR #169).
    import random

    known = {}
    for path in sorted(FROZEN.glob("*.yaml")):
        for channel in yaml.safe_load(path.read_text()).get("channels") or []:
            for entry in channel.get("chain") or []:
                if entry.get("parameters"):
                    known.setdefault(entry["uri"], list(entry["parameters"]))
    text = frozen("guitar_vocal").read_text()
    names = {e["name"]: list(VOCAL_PARAMETERS[e["name"]]) for e in yaml.safe_load(text)["channels"][1]["chain"]}
    lengths = [0.5, 0.25, -12.0, 0.123456, 1, 0, 440.0, -3.5, 0.0001, 20000.0, True]
    chosen = random.Random(169)
    for _ in range(40):
        def change(d):
            for entry in d["channels"][0]["chain"]:
                entry["parameters"] = {k: chosen.choice(lengths) for k in known[entry["uri"]]}
            for entry in d["channels"][1]["chain"]:
                entry["parameters"] = {k: chosen.choice(lengths) for k in names[entry["name"]]}

        out = updated(text, change)
        assert "{name: " not in out and comments(out) == comments(text)


def test_a_flow_list_that_outgrows_its_line_is_folded_as_before_when_that_can_be_reproduced():
    # `_to_block` moves mappings only; a flow list it cannot move is written
    # folded, as before #163, when the next update can make that file
    # (review of PR #169: it was refused).
    text = "chain:\n  - name: a\n    ports: [1, 2]\n"
    out = updated(text, lambda d: d["chain"][0]["ports"].extend(range(3, 40)))
    assert out.startswith("chain:\n  - name: a\n    ports: [1, 2, 3,") and len(out.splitlines()) > 3
    # Inside a flow entry it goes with the entry, into block style.
    text = "chain:\n  - {name: a, uri: u, ports: [1, 2]}\n"
    out = updated(text, lambda d: d["chain"][0]["ports"].extend(range(3, 40)))
    assert out.startswith("chain:\n  - name: a\n    uri: u\n    ports:\n    - 1\n") or \
        out.startswith("chain:\n  - name: a\n    uri: u\n    ports:\n      - 1\n"), out


def test_a_flow_mapping_under_a_key_with_a_trailing_comment_is_folded_as_before():
    # A mapping under a key has no line of its own for its trailing comment,
    # so it is not moved; folded, the file still reproduces, so it is written.
    text = "kind: rig\nmeta: {version: '1.0', status: wip}  # meta note\nx: 1\n"
    out = updated(text, lambda d: d["meta"].update(status="w" * 100))
    assert out.startswith("kind: rig\nmeta: {version: '1.0', status:") and "# meta note" in out


def test_an_entry_after_a_trailing_commented_flow_entry_is_written_folded_when_that_reproduces():
    # Where block style comes out wrongly (after `a`'s trailing comment, in a
    # list with other comments), `b` stays in flow style, folded as before
    # #163, and is written when that file reproduces (review of PR #169).
    # Where the fold falls inside a key (`Knob 3`) the text does not read
    # back, and the save is refused, as it was before, now saying why.
    text = (
        "# head\nchannels:\n  - name: guitar\n    chain:\n      # above a\n"
        '      - {name: a, uri: "http://x.org/p#frag"}   # about a\n'
        '      # above b\n      - {name: b, uri: "urn:b"}\n      - {name: c, uri: "urn:c"}  # about c (last)\n'
        "      # end of chain list\n    # end of channel\n  # end of channels\n\n# end of document\n")
    out = updated(text, lambda d: d["channels"][0]["chain"][1].update(parameters={f"Knob{i}": 0.5 for i in range(6)}))
    assert '      # above b\n      - {name: b, uri: "urn:b", parameters: {Knob0: 0.5,' in out
    assert len(out.splitlines()) == len(text.splitlines()) + 1, "folded once"
    data = yaml.safe_load(text)
    data["channels"][0]["chain"][1]["parameters"] = {f"Knob {i}": 0.5 for i in range(6)}
    with pytest.raises(yamlmerge.MergeError, match="left in flow style.*entry 'b'.*trailing comment"):
        yamlmerge.merged(text, data)


def test_an_entry_put_back_in_flow_style_leaves_the_last_entrys_end_comments_where_it_moved_them():
    # Re-review of PR #169: `b` cannot be a block after `a`'s trailing comment
    # and goes back to flow style; `c`, the last, moved and took the comments
    # after the list with it. `b`'s undo wrote those back to the list's end
    # as well, so they came out twice and the save was refused.
    text = (
        "channels:\n  - name: guitar\n    chain:\n      # above a\n"
        '      - {name: a, uri: "http://x.org/p#frag"}   # about a\n'
        '      # above b\n      - {name: b, uri: "urn:b"}\n      - {name: c, uri: "urn:c"}\n'
        "      # end of chain list\n    # end of channel\nx: 1\n")
    out = updated(text, lambda d: [d["channels"][0]["chain"][i].update(parameters={"g": 1}) for i in (1, 2)])
    assert out.count("# end of chain list") == 1 and comments(out) == comments(text)
    assert '      - {name: b, uri: "urn:b", parameters: {g: 1}}\n' in out
    assert '      - name: c\n        uri: "urn:c"\n        parameters:\n          g: 1\n      # end of chain list\n' in out
