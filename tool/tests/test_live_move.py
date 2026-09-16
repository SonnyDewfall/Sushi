"""Position maths for reordering the plugin chain.

Pure, so it runs without a Sushi instance. Worth pinning precisely: getting it
wrong silently rearranges someone's signal chain, and the "move later past the
last element" case has to become add_to_back rather than "before" anything,
because that is how Sushi's API expresses position.
"""

from sushi_rig.live import plan_move

CHAIN = ["compressor", "overdrive", "octave", "eq", "chorus", "delay", "reverb"]


def test_moving_earlier_lands_before_the_previous_processor():
    assert plan_move(CHAIN, "overdrive", -1) == {
        "before": "compressor",
        "add_to_back": False,
    }


def test_moving_later_lands_before_the_one_after_next():
    """Moving past exactly one neighbour means inserting before whatever
    follows that neighbour."""
    assert plan_move(CHAIN, "overdrive", 1) == {"before": "eq", "add_to_back": False}


def test_moving_the_second_to_last_later_goes_to_the_back():
    """There is nothing to be "before" past the end, so Sushi needs
    add_to_back instead."""
    assert plan_move(CHAIN, "delay", 1) == {"before": None, "add_to_back": True}


def test_first_cannot_move_earlier():
    assert plan_move(CHAIN, "compressor", -1) is None


def test_last_cannot_move_later():
    assert plan_move(CHAIN, "reverb", 1) is None


def test_unknown_processor_is_a_no_op():
    assert plan_move(CHAIN, "not-on-this-track", 1) is None


def test_single_plugin_chain_cannot_move_either_way():
    assert plan_move(["only"], "only", -1) is None
    assert plan_move(["only"], "only", 1) is None
