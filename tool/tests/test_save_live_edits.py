"""A save after plugins were added and removed live (#32): `rigfile.fold_state`
with the agent's account of the edits (`agent.live_edits`), through
`save.save_rig` and `write_rig`, into copies of committed rig files.

What is real: the rig files (config/rigs/, copied into a temporary folder,
and in two tests given comments the committed files do not have, said where),
the fold, the write and the comment rule. What is invented: the captures. Each
is the file's own values read back as float32 (`_capture_of`), with the
copies to stereo after the mono plugins and the send and return the compiler
adds, as `live.capture` would list them, then the edits applied by hand; and
the defaults of the added plugin, which on the rig are what the agent reads
from Sushi straight after the add. NOT RUN against a live Sushi.
"""

from __future__ import annotations

import shutil
import struct

import pytest
import yaml

from sushi_rig.paths import checkout_root
from sushi_rig.rigfile import RigFileError, fold_state, level_to_gain
from sushi_rig.save import SaveError, save_rig

RIGS = checkout_root() / "config" / "rigs"
DELAY = "http://drobilla.net/plugins/mda/Delay"
LIMITER = "http://drobilla.net/plugins/mda/Limiter"
# The mda Delay's defaults, normalised: from its Turtle (tests/fixtures/lv2/mda.lv2),
# as Sushi would report them for a processor just added (an assumption, rigfile.py).
DELAY_DEFAULTS = {"L Delay": 0.5, "R Delay": 0.27, "Feedback": 0.7, "Fb Tone": 0.5, "FX Mix": 0.33, "Output": 0.5}
# Which of accustomed's plugins are mono (plugin-ports.txt), so the capture has their copies.
MONO = {"compressor", "overdrive", "octave_down", "octave_up"}


def _float32(value):
    return struct.unpack("f", struct.pack("f", value))[0] if isinstance(value, float) else value


def _capture_of(raw):
    """`raw` running unchanged, as `live.capture` would read it."""
    processors, tracks = {}, {}
    for channel in raw["channels"]:
        names = []
        for plugin in channel.get("chain") or []:
            names.append(plugin["name"])
            processors[plugin["name"]] = {
                "bypassed": bool(plugin.get("bypassed", False)),
                "parameters": {k: _float32(v) for k, v in (plugin.get("parameters") or {}).items()},
            }
            if plugin["name"] in MONO:
                names.append(plugin["name"] + "_to_stereo")
                processors[plugin["name"] + "_to_stereo"] = {"bypassed": False, "parameters": {}}
        send = channel["name"] + "_send"
        tracks[channel["name"]] = names + [send]
        processors[send] = {"bypassed": False, "parameters": {"gain": _float32(level_to_gain(channel.get("level", 0.0)))}}
        processors[channel["name"]] = {"parameters": {"gain": 0.833333, "pan": 0.5, "mute": 0.0}}
    tracks["master"] = ["master_return"] + [p["name"] for p in raw["master"].get("chain") or []]
    processors["master_return"] = {"bypassed": False, "parameters": {}}
    return {"processors": processors, "tracks": tracks}


def copied(tmp_path, name):
    path = tmp_path / "config" / "rigs" / f"{name}.yaml"
    path.parent.mkdir(parents=True)
    shutil.copy(RIGS / f"{name}.yaml", path)
    return path


def entry_blocks(text):
    """The board chain's entries of a block-style rig file, each with the
    comment lines directly above it: `(head, [(name, lines)], tail)`."""
    lines = text.splitlines(keepends=True)
    start = lines.index("  chain:\n") + 1
    end = next(i for i in range(start, len(lines)) if not lines[i].startswith(("  ", "#")) or lines[i].startswith("master"))
    blocks, pending = [], []
    for line in lines[start:end]:
        if line.lstrip().startswith("#"):
            pending.append(line)
        elif line.startswith("  - name: "):
            blocks.append([line.split(": ", 1)[1].strip(), pending + [line]])
            pending = []
        else:
            blocks[-1][1].append(line)
    assert not pending
    return "".join(lines[:start]), [(n, "".join(b)) for n, b in blocks], "".join(lines[end:])


def three_edits(raw):
    """Add `slap` (an mda Delay, one value off its default) after `eq`,
    remove `octave_up`, move `room` up past `delay`: the capture after them,
    and what the agent's log says of them."""
    state = _capture_of(raw)
    board = state["tracks"]["board"]
    board.remove("octave_up")
    board.remove("octave_up_to_stereo")
    del state["processors"]["octave_up"], state["processors"]["octave_up_to_stereo"]
    board.insert(board.index("eq") + 1, "slap")
    state["processors"]["slap"] = {"bypassed": False,
                                   "parameters": {**{k: _float32(v) for k, v in DELAY_DEFAULTS.items()},
                                                  "FX Mix": _float32(0.62)}}
    i = board.index("room")
    board[i - 1], board[i] = board[i], board[i - 1]
    edits = {"added": {"slap": {"track": "board", "uri": DELAY, "defaults": dict(DELAY_DEFAULTS)}},
             "removed": {"octave_up": "board"}}
    return state, edits


SLAP = f"""\
  - name: slap
    uri: {DELAY}
    parameters:
      FX Mix: 0.62
"""


def expected_after_three_edits(text):
    head, blocks, tail = entry_blocks(text)
    by_name = dict(blocks)
    order = [n for n, _ in blocks if n != "octave_up"]
    order.insert(order.index("eq") + 1, "slap")
    i = order.index("room")
    order[i - 1], order[i] = order[i], order[i - 1]
    by_name["slap"] = SLAP
    return head + "".join(by_name[n] for n in order) + tail


def test_an_add_a_remove_and_a_move_saved_into_a_committed_rig_are_exactly_those_three_edits(tmp_path):
    path = copied(tmp_path, "accustomed")
    before = path.read_text()
    state, edits = three_edits(yaml.safe_load(before))

    save_rig("accustomed", path, state=state, edits=edits)
    after = path.read_text()
    assert after == expected_after_three_edits(before)
    assert "_to_stereo" not in after and "_send" not in after and "master_return" not in after, \
        "the compiler's own processors never appear in the file"
    save_rig("accustomed", path, state=state, edits=edits)
    assert path.read_text() == after, "a second save of the same rig changes nothing"


def test_their_comments_go_and_move_with_them_and_no_other_does(tmp_path):
    """The same three edits into accustomed with comments written in (the
    committed file has none in its chain): the removed plugin's comment
    goes with it, the moved one's moves with it, the others stay put."""
    path = copied(tmp_path, "accustomed")
    text = path.read_text()
    for name, comment in (("octave_up", "the octave up, rarely on"), ("eq", "the tone: leave the mids"),
                          ("delay", "the slapback"), ("room", "the room: always on")):
        text = text.replace(f"  - name: {name}\n", f"  # {comment}\n  - name: {name}\n", 1)
    text = text.replace("      Room Size: 0.41\n", "      Room Size: 0.41   # the small room\n", 1)
    path.write_text(text)
    state, edits = three_edits(yaml.safe_load(text))

    save_rig("accustomed", path, state=state, edits=edits)
    after = path.read_text()
    assert after == expected_after_three_edits(text)
    assert "# the octave up, rarely on" not in after
    assert "  # the room: always on\n  - name: room\n" in after and "Room Size: 0.41   # the small room" in after
    assert after.index("# the room: always on") < after.index("# the slapback"), "the room moved, comment and all"
    assert "  # the tone: leave the mids\n  - name: eq\n" in after
    assert after.index("  - name: eq\n") < after.index("  - name: slap\n") < after.index("# the room")


def test_a_chain_written_in_flow_style_gets_a_flow_entry_and_keeps_its_comments(tmp_path):
    """guitar_vocal as committed: flow entries and the comments above each
    chain. The capture has no parameters for its plugins (invented: the file
    states none, and folding a real capture would write every one of them,
    which is a save's ordinary work and not what this shows)."""
    path = copied(tmp_path, "guitar_vocal")
    before = path.read_text()
    raw = yaml.safe_load(before)
    state = {"processors": {}, "tracks": {}}
    for channel in raw["channels"]:
        names = [p["name"] for p in channel["chain"]]
        state["tracks"][channel["name"]] = names + [channel["name"] + "_send"]
        state["processors"].update({n: {"bypassed": False, "parameters": {}} for n in names})
    state["tracks"]["master"] = ["master_return"]
    vocal = state["tracks"]["vocal"]
    vocal.remove("vocal_eq")
    vocal.insert(vocal.index("vocal_limiter"), "vocal_echo")
    state["processors"]["vocal_echo"] = {"bypassed": True, "parameters": dict(DELAY_DEFAULTS)}
    guitar = state["tracks"]["guitar"]
    guitar[0], guitar[1] = guitar[1], guitar[0]  # the overdrive before the compressor
    edits = {"added": {"vocal_echo": {"track": "vocal", "uri": DELAY, "defaults": dict(DELAY_DEFAULTS)}},
             "removed": {"vocal_eq": "vocal"}}

    save_rig("guitar_vocal", path, state=state, edits=edits)
    after = path.read_text()
    lines = before.splitlines(keepends=True)
    compressor = next(i for i, line in enumerate(lines) if "name: guitar_compressor" in line)
    lines[compressor], lines[compressor + 1] = lines[compressor + 1], lines[compressor]
    # The comment above the compressor was directly above the first entry: it is
    # the compressor's by the rule a reorder moves comments by, so it goes with
    # it to second place (a heading a blank line set apart would have stayed).
    heading = lines[compressor - 2:compressor]
    del lines[compressor - 2:compressor]
    lines[compressor - 1:compressor - 1] = heading
    expected = "".join(lines).replace(
        '      - {name: vocal_eq, uri: "http://distrho.sf.net/plugins/3BandEQ"}\n', "").replace(
        '      - {name: vocal_limiter,',
        f'      - {{name: vocal_echo, uri: {DELAY}, bypassed: true}}\n      - {{name: vocal_limiter,')
    assert after == expected


def test_a_plugin_added_to_an_empty_master_chain_is_written_there(tmp_path):
    path = copied(tmp_path, "electric-clean")
    raw = yaml.safe_load(path.read_text())
    state = _capture_of(raw)
    state["tracks"]["master"].append("ceiling")
    state["processors"]["ceiling"] = {"bypassed": False, "parameters": {"Thresh": _float32(0.8), "Output": 0.5}}
    save_rig("electric-clean", path, state=state,
             edits={"added": {"ceiling": {"track": "master", "uri": LIMITER, "defaults": {"Thresh": 0.6, "Output": 0.5}}},
                    "removed": {}})
    assert yaml.safe_load(path.read_text())["master"]["chain"] == [
        {"name": "ceiling", "uri": LIMITER, "parameters": {"Thresh": 0.8}}]


def test_with_no_defaults_known_every_value_is_written():
    from sushi_rig.rigfile import added_entry

    entry = added_entry("slap", DELAY, {"bypassed": False, "parameters": {"FX Mix": _float32(0.62), "Output": 0.5}}, None)
    assert entry == {"name": "slap", "uri": DELAY, "parameters": {"FX Mix": 0.62, "Output": 0.5}}
    assert added_entry("slap", DELAY, {"bypassed": False, "parameters": {"Output": 0.5}}, {"Output": 0.5}) == \
        {"name": "slap", "uri": DELAY}, "a plugin at its defaults is its name and URI, as the rig files write one"


# --------------------------------------------------------------------------- #
# Telling "added live" from "a different rig"
# --------------------------------------------------------------------------- #

def test_without_the_log_a_plugin_added_or_removed_live_is_refused_as_today(tmp_path):
    """The CLI's `sushi-rig save` and the panel's SAVE have no log."""
    path = copied(tmp_path, "accustomed")
    before = path.read_text()
    state, _ = three_edits(yaml.safe_load(before))
    with pytest.raises(SaveError, match="names slap, which accustomed does not have: that is a capture of a "
                                        "different rig, or a plugin added live, which only the agent's save"):
        save_rig("accustomed", path, state=state)
    del state["processors"]["slap"]
    state["tracks"]["board"].remove("slap")
    with pytest.raises(SaveError, match=r"lacks octave_up \(on board\), which accustomed has, and this save "
                                        "has no log of what was removed live"):
        save_rig("accustomed", path, state=state)
    assert path.read_text() == before


@pytest.mark.parametrize("edit, said", [
    # A name the log never added: another rig's processor.
    (lambda state, edits: (state["tracks"]["board"].insert(2, "phaser"),
                           state["processors"].__setitem__("phaser", {"parameters": {}})),
     "names phaser, which accustomed does not have: that is a capture of a different rig: the agent's log "
     "does not say it was added live"),
    # Added, by the log, to another track than the capture has it on.
    (lambda state, edits: edits["added"]["slap"].__setitem__("track", "vocal"),
     "names slap, which accustomed does not have: that is a capture of a different rig"),
    # Lacking a plugin the log does not say was removed.
    (lambda state, edits: (state["tracks"]["board"].remove("hall"),),
     r"lacks hall \(on board\), which accustomed has, and the agent's log does not say it was removed"),
])
def test_what_the_log_does_not_explain_is_still_refused_and_nothing_is_written(tmp_path, edit, said):
    path = copied(tmp_path, "accustomed")
    before = path.read_text()
    state, edits = three_edits(yaml.safe_load(before))
    edit(state, edits)
    with pytest.raises(SaveError, match=said):
        save_rig("accustomed", path, state=state, edits=edits)
    assert path.read_text() == before


def test_the_log_explains_a_capture_and_never_changes_one():
    """A plugin the log says was added but the capture does not hold is not
    written, and one it says was removed but the capture holds is kept."""
    raw = yaml.safe_load((RIGS / "accustomed.yaml").read_text())
    state = _capture_of(raw)
    folded = fold_state(raw, state, {"added": {"slap": {"track": "board", "uri": DELAY, "defaults": {}}},
                                     "removed": {"hall": "board"}})
    assert [p["name"] for p in folded["channels"][0]["chain"]] == [p["name"] for p in raw["channels"][0]["chain"]]


def test_a_remove_undone_is_the_files_own_entry_again_and_a_replacement_is_a_new_one(tmp_path):
    path = copied(tmp_path, "accustomed")
    before = path.read_text()
    raw = yaml.safe_load(before)
    # Removed, then put back by an undo: same URI, so the file's entry, comments and all.
    undone = {"added": {"eq": {"track": "board", "uri": "http://distrho.sf.net/plugins/3BandEQ", "defaults": None}},
              "removed": {"eq": "board"}}
    save_rig("accustomed", path, state=_capture_of(raw), edits=undone)
    assert path.read_text() == before
    # Removed, and another plugin added under its name: a new entry in its place.
    state = _capture_of(raw)
    state["processors"]["eq"] = {"bypassed": False, "parameters": {"Output": 0.5}}
    replaced = {"added": {"eq": {"track": "board", "uri": LIMITER, "defaults": {"Output": 0.5}}},
                "removed": {"eq": "board"}}
    save_rig("accustomed", path, state=state, edits=replaced)
    chain = yaml.safe_load(path.read_text())["channels"][0]["chain"]
    assert chain[4] == {"name": "eq", "uri": LIMITER}
