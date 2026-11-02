"""The capture engine's decisions: what to link, and what to refuse.

No numpy and no JACK are needed here, on purpose: this module has no
`importorskip`, so none of these can be hidden by a missing optional package.
The block bookkeeping (needs numpy) is in test_capture_pump.py, and the check
that the names the engine calls exist in the installed JACK-Client is in
test_capture_jack.py.

What none of these can say is anything about a live PipeWire. The port names
below are the ones the repository already uses for the rig (`machines/`, the
patchbay, the take tests); whether PipeWire gives the same names to a JACK
client is for the rig machine.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from sushi_rig import capture
from sushi_rig.capture import CaptureError, CaptureUnavailable, Column
from sushi_rig.take import DRY, PROCESSED, Stem

# A guitar_vocal take as the transport plans it: the processed stems are
# stereo, the dry taps mono. The dry ports are the real iD14 inputs in the
# machine file; the processed ones are where they would be with an engine
# output bus per track.
GUITAR = Stem("guitar", PROCESSED, "sushi:audio_output_0")
GUITAR_DRY = Stem("guitar", DRY, "Audient iD14:capture_AUX0")
VOCAL = Stem("vocal", PROCESSED, "sushi:audio_output_2")
VOCAL_DRY = Stem("vocal", DRY, "Audient iD14:capture_AUX1")
PLAN = ((GUITAR, 2), (GUITAR_DRY, 1), (VOCAL, 2), (VOCAL_DRY, 1))


# --------------------------------------------------------------------------- #
# The link plan
# --------------------------------------------------------------------------- #

def test_a_stereo_stem_is_the_consecutive_ports_from_the_one_named():
    assert capture.numbered_ports("sushi:audio_output_0", 2) == [
        "sushi:audio_output_0", "sushi:audio_output_1"]
    assert capture.numbered_ports("sushi:audio_output_8", 3) == [
        f"sushi:audio_output_{n}" for n in (8, 9, 10)], "carrying into a second digit"


def test_digits_keep_their_width():
    assert capture.numbered_ports("dev:in_08", 3) == ["dev:in_08", "dev:in_09", "dev:in_10"]
    assert capture.numbered_ports("dev:in_007", 2) == ["dev:in_007", "dev:in_008"]


def test_a_mono_stem_is_its_port_whatever_the_port_is_called():
    """Nothing to count from, so nothing to guess."""
    assert capture.numbered_ports("Audient iD14:capture_AUX0", 1) == ["Audient iD14:capture_AUX0"]
    assert capture.numbered_ports("mic:left", 1) == ["mic:left"]


def test_a_stereo_stem_whose_port_has_no_number_is_refused_not_guessed():
    reason = capture.numbered_ports("mic:left", 2)
    assert isinstance(reason, str) and "'mic:left' ends in no number" in reason


def test_a_stem_of_no_channels_captures_nothing():
    assert "captures nothing" in capture.numbered_ports("sushi:audio_output_0", 0)


def test_the_columns_follow_the_stems_in_order_with_their_ports():
    columns = capture.plan_columns(PLAN)
    assert [(c.index, c.stem, c.channel, c.source) for c in columns] == [
        (0, "guitar.wav", 0, "sushi:audio_output_0"),
        (1, "guitar.wav", 1, "sushi:audio_output_1"),
        (2, "guitar-dry.wav", 0, "Audient iD14:capture_AUX0"),
        (3, "vocal.wav", 0, "sushi:audio_output_2"),
        (4, "vocal.wav", 1, "sushi:audio_output_3"),
        (5, "vocal-dry.wav", 0, "Audient iD14:capture_AUX1"),
    ]
    assert [c.port for c in columns] == [f"in_{i}" for i in range(6)]


def test_two_columns_from_one_port_are_refused_even_across_stems():
    """The take module refuses two stems naming one port; a stereo stem's second
    channel landing on another stem's port is the same fault, and only visible
    once the channels are expanded."""
    clash = ((GUITAR, 2), (Stem("vocal", PROCESSED, "sushi:audio_output_1"), 1))
    with pytest.raises(CaptureError, match=r"guitar.wav\[1\] and vocal.wav\[0\] .*audio_output_1.*one signal"):
        capture.plan_columns(clash)


def test_every_problem_is_reported_at_once():
    plan = ((Stem("a", PROCESSED, "x:left"), 2), (Stem("b", PROCESSED, "y:right"), 2))
    with pytest.raises(CaptureError) as caught:
        capture.plan_columns(plan)
    assert len(caught.value.problems) == 2


def test_an_empty_plan_is_refused():
    with pytest.raises(CaptureError, match="at least one column"):
        capture.plan_columns(())


# --------------------------------------------------------------------------- #
# What the graph must hold, before and after linking
# --------------------------------------------------------------------------- #

COLUMNS = capture.plan_columns(PLAN)
EVERY_SOURCE = [c.source for c in COLUMNS]


def test_a_graph_with_every_source_has_nothing_missing():
    assert capture.missing_sources(COLUMNS, EVERY_SOURCE + ["other:port"]) == []


def test_a_missing_source_is_named_with_the_ports_that_do_exist():
    problems = capture.missing_sources(COLUMNS, [s for s in EVERY_SOURCE if "AUX0" not in s])
    assert "no port called 'Audient iD14:capture_AUX0' (for guitar-dry.wav[0])" in problems[0]
    assert "output ports in the graph include:" in problems[-1]
    assert "capture_AUX1" in problems[-1]


def test_an_empty_graph_says_so_rather_than_listing_nothing():
    problems = capture.missing_sources(COLUMNS, [])
    assert len(problems) == len(COLUMNS) + 1
    assert "no output ports in the graph at all" in problems[-1]


def test_links_that_took_are_clean():
    assert capture.check_links(COLUMNS, [[c.source] for c in COLUMNS]) == []


def test_a_link_that_did_not_take_is_named():
    actual = [[c.source] for c in COLUMNS]
    actual[2] = []
    (problem,) = capture.check_links(COLUMNS, actual)
    assert "sushi-rig-capture:in_2 should be fed by Audient iD14:capture_AUX0 alone" in problem
    assert "(for guitar-dry.wav[0])" in problem and "fed by nothing" in problem


def test_an_extra_link_is_refused_because_it_would_be_summed_in():
    actual = [[c.source] for c in COLUMNS]
    actual[0] = [COLUMNS[0].source, "system:capture_1"]
    (problem,) = capture.check_links(COLUMNS, actual)
    assert "fed by sushi:audio_output_0, system:capture_1" in problem


def test_a_port_read_back_short_is_a_problem_not_a_pass():
    problems = capture.check_links(COLUMNS, [[c.source] for c in COLUMNS][:-1])
    assert problems == ["6 columns but 5 ports read back"]


def test_the_graph_must_run_at_the_rate_and_quantum_the_take_was_planned_for():
    assert capture.check_granted(48000, 256, 48000, 256) == []
    both = capture.check_granted(48000, 256, 44100, 1024)
    assert "runs at 44100 Hz, but the take was planned for 48000 Hz" in both[0]
    assert "granted a quantum of 1024, but the take was planned for 256" in both[1]


# --------------------------------------------------------------------------- #
# JACK is optional, and its absence is a sentence
# --------------------------------------------------------------------------- #

def test_importing_the_module_does_not_import_jack():
    """The reason JACK is loaded late: a machine without libjack must still be
    able to import capture and run everything above."""
    # A fresh interpreter in which importing jack is an error, so any import
    # of it at module level would fail here.
    out = subprocess.run(
        [sys.executable, "-c",
         "import sys; sys.modules['jack'] = None; import sushi_rig.capture; print('ok')"],
        capture_output=True, text=True, env={"PYTHONPATH": str(Path(capture.__file__).parents[1])},
    )
    assert out.stdout.strip() == "ok", out.stderr


def test_a_missing_package_names_the_extra(monkeypatch):
    monkeypatch.setitem(sys.modules, "jack", None)  # makes `import jack` raise ImportError
    with pytest.raises(CaptureUnavailable, match=r"JACK-Client is not installed .*tool\[capture\]"):
        capture.load_jack()


def test_a_missing_libjack_names_the_library_and_pw_jack(monkeypatch):
    """JACK-Client raises OSError on import when libjack is not found."""
    import builtins

    real = builtins.__import__

    def refuse(name, *args, **kwargs):
        if name == "jack":
            raise OSError("JACK library not found")
        return real(name, *args, **kwargs)

    monkeypatch.delitem(sys.modules, "jack", raising=False)
    monkeypatch.setattr(builtins, "__import__", refuse)
    with pytest.raises(CaptureUnavailable, match=r"libjack.*libjack-jackd2-0.*pw-jack"):
        capture.load_jack()


def test_an_engine_without_jack_fails_start_with_the_sentence_not_a_traceback(monkeypatch):
    monkeypatch.setitem(sys.modules, "jack", None)
    engine = capture.CaptureEngine(48000, 256)
    with pytest.raises(CaptureUnavailable):
        engine.start(PLAN, lambda block: 0)
    assert engine.stop() == {}, "nothing was started, so stop has nothing to say and does not raise"


def test_a_plan_that_cannot_be_linked_is_refused_before_jack_is_touched(monkeypatch):
    monkeypatch.setitem(sys.modules, "jack", None)
    clash = ((GUITAR, 2), (Stem("vocal", PROCESSED, "sushi:audio_output_1"), 1))
    with pytest.raises(CaptureError) as caught:
        capture.CaptureEngine(48000, 256).start(clash, lambda block: 0)
    assert not isinstance(caught.value, CaptureUnavailable), "the plan is the news, not JACK"


def test_an_engine_cannot_be_built_for_a_nonsense_format():
    with pytest.raises(ValueError):
        capture.CaptureEngine(0, 256)
    with pytest.raises(ValueError):
        capture.CaptureEngine(48000, 0)


def test_a_column_knows_its_own_port_name():
    assert Column(3, "vocal.wav", 0, "x:y").port == "in_3"
