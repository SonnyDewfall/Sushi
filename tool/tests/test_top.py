"""Reading `pw-top` for the rig.

Runs against captured output rather than a live PipeWire, so the parsing and the
thresholds are pinned without needing audio hardware or a rig.
"""

from __future__ import annotations

import pytest

from sushi_rig.top import (
    BUSY_WARN,
    CHILD_NODES,
    LoadError,
    bar,
    format_sample,
    load_report,
    parse_duration,
    parse_pw_top,
    read_pw_top,
    rig_nodes,
)

# Real output, two samples, trimmed. The second block is the one that counts.
PW_TOP = """S   ID  QUANT   RATE    WAIT    BUSY   W/Q   B/Q  ERR FORMAT           NAME
R   45      0      0   0.0us   0.0us  0.00  0.00    0                  Dummy-Driver
R  130      0      0   0.0us   0.0us  0.00  0.00    0                   + sushi
S   ID  QUANT   RATE    WAIT    BUSY   W/Q   B/Q  ERR FORMAT           NAME
R   45   1024  48000   5.8ms   3.0us  0.27  0.00    1   S32LE 10 48000 alsa_input.usb-Audient
R  127      0      0  40.6us   6.2us  0.00  0.00    0                   + fmit
R  130      0      0  34.3us 674.6us  0.00  0.03    0                   + sushi
R  159      0      0  51.3us   3.0us  0.00  0.00    0                   + Carla
R  143      0      0  51.3us   4.9ms  0.00  0.23    2                   + NAM
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


def test_a_follower_reports_no_quantum_of_its_own():
    """Real `pw-top` states the quantum on the driver row only; every follower
    reads 0. The fixture used to carry it on every row, which is why nothing
    caught `top` printing "quantum 0 @ 0 Hz" on a real rig for as long as it
    did."""
    nodes = {n["name"]: n for n in parse_pw_top(PW_TOP)}
    assert nodes["sushi"]["quantum"] == "0"
    assert nodes["sushi"]["is_driver"] is False
    assert nodes["alsa_input.usb-Audient"]["is_driver"] is True


def test_a_node_carries_the_driver_it_runs_under():
    """Sushi is always a follower, so the quantum that governs it has to be
    read off whatever is driving the graph."""
    nodes = {n["name"]: n for n in parse_pw_top(PW_TOP)}
    assert nodes["sushi"]["driver"] == "alsa_input.usb-Audient"
    assert nodes["sushi"]["driver_quantum"] == "1024"
    assert nodes["sushi"]["driver_rate"] == "48000"


def test_the_block_budget_is_read_from_the_driver_not_from_sushi():
    """The regression this replaces: reading Sushi's own columns gave
    "quantum 0 @ 0 Hz" and no block time, on the one display built to explain
    latency."""
    out = format_sample(parse_pw_top(PW_TOP), {}, "rig")
    assert "quantum 1024 @ 48000 Hz" in out
    assert "21.3 ms per block" in out
    assert "quantum 0" not in out


def test_the_driving_device_is_named():
    """A webcam that had joined the graph through the tuner was clocking this
    rig at 2048, and no part of the tool said so. The wrong driver has no
    symptom other than latency."""
    out = format_sample(parse_pw_top(PW_TOP), {}, "rig")
    assert "clocked by alsa_input.usb-Audient" in out


# --- which node's driver describes the rig -----------------------------------


# The amp in a different driver group from Sushi: Carla parked on a
# Dummy-Driver carrying no quantum, while Sushi follows the interface at 1024.
PW_TOP_SPLIT = """S   ID  QUANT   RATE    WAIT    BUSY   W/Q   B/Q  ERR FORMAT           NAME
R   45   1024  48000   5.8ms   3.0us  0.27  0.00    1   S32LE 10 48000 alsa_input.usb-Audient
R  130      0      0  34.3us 674.6us  0.00  0.03    0                   + sushi
R   46      0      0   0.0us   0.0us  0.00  0.00    0                  Dummy-Driver
R  143      0      0  51.3us   4.9ms  0.00  0.23    2                   + NAM
"""


def test_the_budget_comes_from_sushis_driver_not_from_the_first_row():
    """`rig_nodes` returns signal order, so the first row is the amp whenever
    one runs. Reading the budget off it printed "quantum 0 @ 0 Hz" again —
    the same symptom as before, arrived at from the other end."""
    nodes = rig_nodes(parse_pw_top(PW_TOP_SPLIT), {"amp": 1, "sushi": 2})
    assert nodes[0]["name"] == "NAM", "the amp should still be shown first"
    out = format_sample(nodes, {}, "rig")
    assert "quantum 1024 @ 48000 Hz" in out
    assert "21.3 ms per block" in out
    assert "quantum 0" not in out


def test_nodes_in_separate_driver_groups_are_reported():
    """Two driver groups means the amp and Sushi are not scheduled in one graph
    cycle, so putting the amp in front stops being free and the chain stops
    being sample-aligned. It has no other symptom."""
    nodes = rig_nodes(parse_pw_top(PW_TOP_SPLIT), {"amp": 1, "sushi": 2})
    out = format_sample(nodes, {}, "rig")
    assert "split across 2 driver groups" in out
    assert "Dummy-Driver" in out


def test_one_driver_group_says_nothing_about_a_split():
    assert "split across" not in format_sample(parse_pw_top(PW_TOP), {}, "rig")


def test_a_node_with_no_driver_is_not_named_as_its_own():
    """"[clocked by sushi]" is never true of anything: Sushi is always a
    follower. A missing driver row has to read as unknown, not as self."""
    only_followers = """S   ID  QUANT   RATE    WAIT    BUSY   W/Q   B/Q  ERR FORMAT           NAME
R  130      0      0  34.3us 674.6us  0.00  0.03    0                   + sushi
"""
    nodes = parse_pw_top(only_followers)
    assert nodes[0]["driver"] is None
    assert "clocked by" not in format_sample(nodes, {}, "rig")


# --- a row pw-top has no timing for (#111) -----------------------------------

# Observed: the one line quoted in tool/spikes/live_rig/RESULTS-sonny-dewfall-
# XPS-9320-2026-10-31.md (a follower in state C, every timing "---"). Invented:
# its name is the rig's Sushi here, and the rows around it are PW_TOP's.
UNTIMED = """S   ID  QUANT   RATE    WAIT    BUSY   W/Q   B/Q  ERR FORMAT           NAME
R   45   1024  48000   5.8ms   3.0us  0.27  0.00    1   S32LE 10 48000 alsa_input.usb-Audient
C  130      0      0    ---     ---   ---   ---     0         sushi
R  143      0      0  51.3us   4.9ms  0.00  0.23    2                   + NAM
"""


def test_a_row_with_no_timing_is_kept_unmeasured_and_drives_nothing():
    """Dropped, it read as Sushi missing from the graph, a different fault from
    Sushi listed and not scheduled. Taken as a driver (it has no "+"), it
    would have clocked the rows after it."""
    nodes = {n["name"]: n for n in parse_pw_top(UNTIMED)}
    assert nodes["sushi"]["load"] is None and nodes["sushi"]["busy"] is None
    assert nodes["sushi"]["xruns"] == 0 and nodes["sushi"]["is_driver"] is False
    assert nodes["NAM"]["driver"] == "alsa_input.usb-Audient"
    out = format_sample(rig_nodes(list(nodes.values()), {"sushi": 1, "amp": 2}), {}, "rig")
    assert "sushi" in out and "  —%" in out


# --- the agent's GET /rig/load (#111) ------------------------------------------


def test_the_load_report_is_the_rigs_nodes_their_budget_and_what_is_missing():
    report = load_report(parse_pw_top(PW_TOP), {"sushi": 1, "amp": 2, "listen": 3})
    assert [(n["name"], n["child"], n["load"], n["xruns"]) for n in report["nodes"]] == [
        ("NAM", "amp", 0.23, 2), ("Carla", "amp", 0.0, 0), ("sushi", "sushi", 0.03, 0)]
    assert report["nodes"][2]["busy_ms"] == pytest.approx(0.675, abs=1e-3)
    # From Sushi's driver row, never its own (a follower's reads 0).
    assert report["budget"] == {"quantum": 1024, "rate": 48000, "block_ms": 21.333,
                                "driver": "alsa_input.usb-Audient"}
    assert report["total_load"] == pytest.approx(0.26)
    assert report["missing"] == [] and report["drivers"] == ["alsa_input.usb-Audient"]
    assert report["warn_at"] == BUSY_WARN


def test_a_running_child_pw_top_does_not_list_is_named():
    without_sushi = "\n".join(line for line in PW_TOP.splitlines() if not line.endswith("+ sushi"))
    report = load_report(parse_pw_top(without_sushi), {"sushi": 1, "fmit": 2})
    assert report["missing"] == ["sushi"]
    assert [n["name"] for n in report["nodes"]] == ["fmit"]


def test_with_no_children_known_nothing_is_called_missing():
    report = load_report(parse_pw_top(PW_TOP), {})
    assert report["missing"] == []
    assert {n["name"] for n in report["nodes"]} == {"NAM", "Carla", "sushi", "fmit"}


def test_nothing_measured_is_no_total_rather_than_zero():
    report = load_report(parse_pw_top(UNTIMED), {"sushi": 1})
    assert report["total_load"] is None
    assert report["nodes"][0]["load"] is None and report["budget"]["block_ms"] is None


@pytest.fixture
def pw_top_on_path(tmp_path, monkeypatch):
    """Put a stand-in `pw-top` (a shell script, `body`) first on PATH. With
    `exec`, so a timeout's kill reaches the process that holds the pipes."""
    def install(body):
        script = tmp_path / "pw-top"
        script.write_text("#!/bin/sh\n" + body + "\n")
        script.chmod(0o755)
        monkeypatch.setenv("PATH", f"{tmp_path}:/usr/bin:/bin")
    return install


def test_pw_top_is_run_for_two_samples_and_its_output_returned(pw_top_on_path, tmp_path):
    (tmp_path / "sample.txt").write_text(PW_TOP)
    pw_top_on_path(f'echo "$@" > {tmp_path}/args; cat {tmp_path}/sample.txt')
    assert read_pw_top(5) == PW_TOP
    assert (tmp_path / "args").read_text().split() == ["-b", "-n", "2"]


def test_a_pw_top_that_is_not_installed_is_said_so(monkeypatch, tmp_path):
    monkeypatch.setenv("PATH", str(tmp_path))
    with pytest.raises(LoadError) as caught:
        read_pw_top(5)
    assert caught.value.code == "no_pw_top" and "pw-top is not installed" in str(caught.value)


def test_a_pw_top_that_fails_is_said_with_what_it_said(pw_top_on_path):
    pw_top_on_path('echo "failed to connect to PipeWire" >&2; exit 1')
    with pytest.raises(LoadError) as caught:
        read_pw_top(5)
    assert caught.value.code == "failed"
    assert str(caught.value) == "pw-top failed (exit 1): failed to connect to PipeWire"


def test_a_pw_top_that_prints_no_sample_is_not_read_as_no_rig(pw_top_on_path):
    pw_top_on_path('echo "usage: pw-top [options]"')
    with pytest.raises(LoadError) as caught:
        read_pw_top(5)
    assert caught.value.code == "no_sample" and "usage: pw-top" in str(caught.value)


def test_a_pw_top_that_overruns_is_stopped_at_the_deadline(pw_top_on_path):
    import time

    pw_top_on_path("exec sleep 30")
    started = time.monotonic()
    with pytest.raises(LoadError) as caught:
        read_pw_top(0.5)
    assert caught.value.code == "timeout" and "within 0.5s" in str(caught.value)
    assert time.monotonic() - started < 5, "killed at the deadline, not waited for"
