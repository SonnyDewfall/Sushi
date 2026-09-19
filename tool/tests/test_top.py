"""Reading `pw-top` for the rig.

Runs against captured output rather than a live PipeWire, so the parsing and the
thresholds are pinned without needing audio hardware or a rig.
"""

from __future__ import annotations

import pytest

from sushi_rig.top import (
    BUSY_WARN,
    CHILD_NODES,
    bar,
    format_sample,
    parse_duration,
    parse_pw_top,
    rig_nodes,
)

# Real output, two samples, trimmed. The second block is the one that counts.
PW_TOP = """S   ID  QUANT   RATE    WAIT    BUSY   W/Q   B/Q  ERR FORMAT           NAME
R   45      0      0   0.0us   0.0us  0.00  0.00    0                  Dummy-Driver
R  130      0      0   0.0us   0.0us  0.00  0.00    0                   + sushi
S   ID  QUANT   RATE    WAIT    BUSY   W/Q   B/Q  ERR FORMAT           NAME
R   45   1024  48000   5.8ms   3.0us  0.27  0.00    1   S32LE 10 48000 alsa_input.usb-Audient
R  127   1024  48000  40.6us   6.2us  0.00  0.00    0                   + fmit
R  130   1024  48000  34.3us 674.6us  0.00  0.03    0                   + sushi
R  159   1024  48000  51.3us   3.0us  0.00  0.00    0                   + Carla
R  143   1024  48000  51.3us   4.9ms  0.00  0.23    2                   + NAM
"""


# --- parsing ----------------------------------------------------------------


@pytest.mark.parametrize(
    "text,seconds",
    [("674.6us", 0.0006746), ("4.9ms", 0.0049), ("3.0us", 3e-6), ("1.5s", 1.5)],
)
def test_durations_are_read_in_their_own_units(text, seconds):
    assert parse_duration(text) == pytest.approx(seconds)


def test_a_duration_that_is_not_one_is_not_guessed_at():
    assert parse_duration("--") is None
    assert parse_duration("") is None


def test_only_the_last_sample_is_used():
    """`pw-top`'s first sample has nothing to compare against and reports zeros
    throughout — which is why `-n 1` looks like it works and quietly says the
    rig is idle. The freshest block is the one that counts."""
    nodes = {n["name"]: n for n in parse_pw_top(PW_TOP)}
    assert nodes["sushi"]["load"] == 0.03, "the first block's zeros must not win"
    assert nodes["NAM"]["busy"] == pytest.approx(0.0049)


def test_the_name_survives_a_variable_width_format_column():
    """Device rows carry a FORMAT the plugin rows do not, so counting columns
    from the left finds the wrong field."""
    nodes = {n["name"]: n for n in parse_pw_top(PW_TOP)}
    assert "alsa_input.usb-Audient" in nodes
    assert nodes["alsa_input.usb-Audient"]["xruns"] == 1


def test_output_with_no_sample_block_is_not_an_error():
    assert parse_pw_top("") == []
    assert parse_pw_top("some unrelated text") == []


# --- picking the rig's nodes ------------------------------------------------


def test_only_the_rigs_own_nodes_are_shown():
    """Everything PipeWire knows about is noise here — the point is the rig."""
    names = [n["name"] for n in rig_nodes(parse_pw_top(PW_TOP), {"sushi": 1, "amp": 2})]
    assert "alsa_input.usb-Audient" not in names
    assert "sushi" in names and "NAM" in names


def test_nodes_are_listed_in_signal_order():
    """The amp is in front of Sushi and the tuner only taps the input, so
    reading top to bottom should follow the guitar rather than the order the
    supervisor happened to start things in."""
    children = {"fmit": 1, "qpwgraph": 2, "listen": 3, "sushi": 4, "amp": 5}
    names = [n["name"] for n in rig_nodes(parse_pw_top(PW_TOP), children)]
    assert names.index("NAM") < names.index("sushi") < names.index("fmit")


def test_children_that_do_no_dsp_contribute_nothing():
    """The listener, the panel and the patchbay process no audio."""
    for child in ("listen", "qpwgraph", "open-stage-control"):
        assert child not in CHILD_NODES


def test_a_rig_with_no_amp_shows_no_amp():
    names = [n["name"] for n in rig_nodes(parse_pw_top(PW_TOP), {"sushi": 1, "fmit": 2})]
    assert "NAM" not in names and "Carla" not in names
    assert names == ["sushi", "fmit"]


def test_without_a_state_file_everything_known_is_shown():
    """Still useful against a rig someone started by hand."""
    names = [n["name"] for n in rig_nodes(parse_pw_top(PW_TOP), None)]
    assert {"sushi", "NAM", "fmit"} <= set(names)


# --- the display ------------------------------------------------------------


def test_the_bar_is_proportional_and_bounded():
    assert bar(0.0).count("█") == 0
    assert bar(1.0).count("░") == 0
    assert bar(0.5).count("█") == pytest.approx(len(bar(0.5)) / 2, abs=1)
    # A node over budget must not draw past the end of its own bar.
    assert len(bar(3.0)) == len(bar(0.5))


def test_being_over_budget_is_said_in_words_not_just_drawn():
    """This is the failure that presents as stutter, and it was invisible in
    every other measure — CPU percentage looked fine throughout."""
    nodes = parse_pw_top(PW_TOP)
    heavy = [dict(n, load=0.6) for n in nodes if n["name"] in ("sushi", "NAM")]
    out = format_sample(heavy, {}, "rig")
    assert "OVER BUDGET" in out


def test_approaching_budget_is_flagged_before_it_breaks():
    nodes = [dict(n, load=BUSY_WARN + 0.01) for n in parse_pw_top(PW_TOP)[:1]]
    assert "Close to budget" in format_sample(nodes, {}, "rig")


def test_climbing_xruns_are_called_out_and_the_total_is_not():
    """The cumulative count never resets, so a number that sits still is old
    history. A change since the last sample is the thing worth seeing."""
    nodes = [n for n in parse_pw_top(PW_TOP) if n["name"] == "NAM"]
    assert "Xruns are climbing" not in format_sample(nodes, {"NAM": 0}, "rig")
    out = format_sample(nodes, {"NAM": 3}, "rig")
    assert "Xruns are climbing" in out
    assert "(+3)" in out


def test_the_block_budget_is_spelled_out():
    """1024 at 48 kHz means nothing until it is 21.3 ms, which is what the load
    percentage is a fraction of."""
    assert "21.3 ms per block" in format_sample(parse_pw_top(PW_TOP), {}, "rig")


def test_nothing_running_says_so_rather_than_printing_an_empty_table():
    assert "is the rig running?" in format_sample([], {}, "rig")
