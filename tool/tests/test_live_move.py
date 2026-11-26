"""Position maths for reordering the plugin chain.

Pure, so it runs without a Sushi instance. Worth pinning precisely: getting it
wrong silently rearranges someone's signal chain, and the "move later past the
last element" case has to become add_to_back rather than "before" anything,
because that is how Sushi's API expresses position.
"""


import pytest

from sushi_rig import live
from sushi_rig.live import decide_move

CHAIN = ["compressor", "overdrive", "octave", "eq", "chorus", "delay", "reverb"]


def command(chain, processor, direction):
    """Where `decide_move` sends `processor`, as (before, add_to_back) by name,
    or None when nothing would be sent."""
    ids = {"t": 100, **{name: i + 1 for i, name in enumerate(chain)}}
    move = decide_move({"t": chain}, ids, processor, direction)
    if move.outcome != "planned":
        return None
    by_id = {i: name for name, i in ids.items()}
    _, _, before, back = move.command
    return {"before": None if back else by_id[before], "add_to_back": back}


def test_moving_earlier_lands_before_the_previous_processor():
    assert command(CHAIN, "overdrive", -1) == {"before": "compressor", "add_to_back": False}


def test_moving_later_lands_before_the_one_after_next():
    """Moving past exactly one neighbour means inserting before whatever
    follows that neighbour."""
    assert command(CHAIN, "overdrive", 1) == {"before": "eq", "add_to_back": False}


def test_moving_the_second_to_last_later_goes_to_the_back():
    """There is nothing to be "before" past the end, so Sushi needs
    add_to_back instead."""
    assert command(CHAIN, "delay", 1) == {"before": None, "add_to_back": True}


def test_first_cannot_move_earlier():
    assert command(CHAIN, "compressor", -1) is None


def test_last_cannot_move_later():
    assert command(CHAIN, "reverb", 1) is None


def test_unknown_processor_is_a_no_op():
    assert command(CHAIN, "not-on-this-track", 1) is None


def test_single_plugin_chain_cannot_move_either_way():
    assert command(["only"], "only", -1) is None
    assert command(["only"], "only", 1) is None


# --------------------------------------------------------------------------- #
# The decision a move request is made on, pure
# --------------------------------------------------------------------------- #

CHAINS = {"main": ["comp", "drive", "reverb"], "aux": ["delay"]}
IDS = {"main": 1, "comp": 2, "drive": 3, "reverb": 4, "aux": 9, "delay": 7}


@pytest.mark.parametrize("processor, direction, outcome, command", [
    ("drive", -1, "planned", (3, 1, 2, False)),   # before comp
    ("comp", 1, "planned", (2, 1, 4, False)),     # before reverb: one step later
    ("drive", 1, "planned", (3, 1, 0, True)),     # past the last: the back of the chain
    ("delay", 1, "unchanged", None),
    ("comp", -1, "unchanged", None),
    ("reverb", 1, "unchanged", None),
    ("main", -1, "absent", None),                 # a track is not on a chain
    ("nope", 1, "absent", None),
])
def test_a_move_is_decided_once_from_what_the_walk_found(processor, direction, outcome, command):
    move = live.decide_move(CHAINS, IDS, processor, direction)
    assert (move.outcome, move.command) == (outcome, command)
    assert move.chains == CHAINS
    assert move.chain == (None if outcome == "absent" else CHAINS["aux" if processor == "delay" else "main"])
