"""The loopback measurement tool's own logic (tool/spikes/live_rig/stereo_check.py).

The tool is standard library only and is run on the laptop against a real
interface; nothing here has PipeWire, Sushi or a sound card. What is tested is
what can be without them: the walk back through the graph that the feedback
guard rests on, what `rig` decides to link, the verdict, the pitch sets, and
the cleanup, the last two against generated signals and stand-ins for
`pw-link` and `pw-record`. Whether any of it holds on the interface is for a
run there to say.

Loaded by path: it is a script beside its results, not part of the package.
"""

from __future__ import annotations

import array
import importlib.util
import itertools
import json
import math
import os
import random
import signal
import subprocess
import sys
import time
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "spikes" / "live_rig" / "stereo_check.py"
LOGGED = SCRIPT.with_name("stereo-check-log.jsonl")


def load():
    spec = importlib.util.spec_from_file_location("stereo_check", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


sc = load()

GUITAR, VOCAL = sc.TONE_PORTS
CAPTURE = "alsa_input.usb-Audient_Audient_iD14-00.multichannel-input:capture_AUX0"
PLAY_L = "alsa_output.usb-Audient_Audient_iD14-00.analog-surround-40:playback_FL"
PLAY_R = "alsa_output.usb-Audient_Audient_iD14-00.analog-surround-40:playback_FR"
OUTPUTS = [("sushi:audio_output_0", PLAY_L), ("sushi:audio_output_1", PLAY_R)]


# ---- the feedback guard: the walk back from Sushi's inputs --------------------


def test_the_interface_straight_into_sushi_is_a_feedback_path():
    assert sc.feedback([(CAPTURE, "sushi:audio_input_0")]) == [f"sushi:audio_input_0 <- {CAPTURE}"]


def test_the_interface_into_sushi_through_the_amp_is_a_feedback_path():
    """The finding. With the amp on the real machine file the interface feeds
    NAM and NAM feeds Sushi, so Sushi's own source is `NAM:Output`."""
    links = [(CAPTURE, "NAM:Input"), ("NAM:Output", "sushi:audio_input_0"), *OUTPUTS]
    assert sc.feedback(links) == [f"sushi:audio_input_0 <- NAM:Output <- NAM:Input <- {CAPTURE}"]

    # What the guard used to ask, and why it passed: the direct sources only.
    direct = [src for out, inp in links if sc.SUSHI_INPUT.match(inp) for src in [out] if sc.ID14 in src]
    assert direct == []


def test_the_walk_goes_through_as_many_nodes_as_there_are():
    links = [(CAPTURE, "eq:in_1"), ("eq:out_1", "NAM:Input"), ("NAM:Output", "sushi:audio_input_1")]
    assert sc.feedback(links) == [
        f"sushi:audio_input_1 <- NAM:Output <- NAM:Input <- eq:out_1 <- eq:in_1 <- {CAPTURE}"]


def test_every_input_of_a_node_between_is_followed():
    """What goes into a node is taken to come out of all its outputs: the
    interface on a mixer's second input reaches Sushi through its only output."""
    links = [(GUITAR, "mix:in_1"), (CAPTURE, "mix:in_2"), ("mix:out", "sushi:audio_input_0")]
    assert sc.feedback(links) == [f"sushi:audio_input_0 <- mix:out <- mix:in_2 <- {CAPTURE}"]


def test_the_loopback_rig_with_its_amp_is_not_a_feedback_path():
    """The test's own wiring: tones in, the interface only after Sushi and in
    front of the recorder."""
    links = [(GUITAR, "NAM:Input"), ("NAM:Output", "sushi:audio_input_0"),
             (VOCAL, "sushi:audio_input_1"), *OUTPUTS, (CAPTURE, "pw-record:input_FL")]
    assert sc.feedback(links) == []


def test_a_loop_in_the_graph_ends_the_walk():
    links = [("a:out", "b:in"), ("b:out", "a:in"), ("a:out", "sushi:audio_input_0")]
    assert sc.feedback(links) == []
    assert sc.feedback(links + [(CAPTURE, "b:in")]) == [
        f"sushi:audio_input_0 <- a:out <- a:in <- b:out <- b:in <- {CAPTURE}"]


def test_a_monitor_of_the_interfaces_own_output_counts_too():
    """Sushi's output read back off the sink is the loop without the cable."""
    monitor = PLAY_L.replace("playback_FL", "monitor_FL")
    assert sc.feedback([*OUTPUTS, (monitor, "sushi:audio_input_0")]) != []


def test_the_guard_reads_what_pw_link_prints():
    listing = (
        f"{CAPTURE}\n  |-> NAM:Input\n"
        "NAM:Input\n  |<- " + CAPTURE + "\n"
        "NAM:Output\n  |-> sushi:audio_input_0\n"
        "sushi:audio_input_0\n  |<- NAM:Output\n"
        f"sushi:audio_output_0\n  |-> {PLAY_L}\n"
        f"{PLAY_L}\n  |<- sushi:audio_output_0\n"
    )
    links = sc.links_of(sc.parse_links(listing))
    assert links == [(CAPTURE, "NAM:Input"), ("NAM:Output", "sushi:audio_input_0"),
                     ("sushi:audio_output_0", PLAY_L)], "each link once, though listed from both ends"
    assert len(sc.feedback(links)) == 1


def test_a_node_name_with_a_colon_is_cut_short_which_only_refuses_more():
    assert sc.node_of("NAM:Input") == "NAM"
    assert sc.node_of("Midi-Bridge:Midi Through:(capture_0) Port-0") == "Midi-Bridge"


# ---- the rig's patchbay, and what `rig` may link ----------------------------------


def item(out, inp):
    return ('  <item node-type="pipewire" port-type="pipewire-audio">\n'
            f'   <output node="{out.split(":")[0]}" port="{out}"/>\n'
            f'   <input node="{inp.split(":")[0]}" port="{inp}"/>\n  </item>\n')


def patchbay(*links):
    return ('<!DOCTYPE patchbay>\n<patchbay name="rig" version="0.9.9">\n <items>\n'
            + "".join(item(a, b) for a, b in links) + " </items>\n</patchbay>\n")


TWO_CHANNELS = [(GUITAR, "sushi:audio_input_0"), (VOCAL, "sushi:audio_input_1"),
                ("sushi:audio_output_0", "Audient iD14:playback_FL"),
                ("sushi:audio_output_1", "Audient iD14:playback_FR")]
ONE_CHANNEL = [TWO_CHANNELS[0], *TWO_CHANNELS[2:]]
AMPED = [("NAM:Output", "sushi:audio_input_0"), *ONE_CHANNEL[1:], (GUITAR, "NAM:Input")]


def test_a_patchbay_is_read_as_the_shipped_code_writes_it():
    """The amp's patchbay as `sushi_rig.amp.amp_patchbay` makes it from one the
    rig generated: the two files have to agree on what a link looks like."""
    from sushi_rig.amp import amp_patchbay

    assert sc.patchbay_links(patchbay(*ONE_CHANNEL)) == ONE_CHANNEL
    assert sc.patchbay_links(amp_patchbay(patchbay(*ONE_CHANNEL), GUITAR)) == AMPED
    assert sc.patchbay_links('<output node="a" port="a &amp; b:out"/> <input node="c" port="c:in"/>') == [
        ("a & b:out", "c:in")]


def test_the_patchbay_is_the_one_on_qpwgraphs_command_line():
    """`qpwgraph -a FILE -m`, as rig.plan_children starts it and the log of
    2026-11-12 shows it; a second one opened from the desktop has none."""
    assert sc.patchbay_of(["qpwgraph", "-a", "/run/user/1000/sushi-rig/rig-amp.qpwgraph", "-m"]) == (
        "/run/user/1000/sushi-rig/rig-amp.qpwgraph")
    assert sc.patchbay_of(["qpwgraph", "-m", ""]) is None


def test_only_the_tones_links_are_made_and_only_the_ones_the_patchbay_asks_for():
    live = [*OUTPUTS]
    wanted, make, problems = sc.plan_rig(TWO_CHANNELS, live, False, [0, 1])
    assert problems == []
    assert make == [(GUITAR, "sushi:audio_input_0"), (VOCAL, "sushi:audio_input_1")]
    assert wanted == TWO_CHANNELS


def test_a_one_channel_rig_gets_no_second_tone():
    """It used to be tone n into input n, whatever the rig: the vocal's tone
    went into a one-channel rig's second input."""
    _, make, problems = sc.plan_rig(ONE_CHANNEL, [*OUTPUTS], False, [0])
    assert (make, problems) == ([(GUITAR, "sushi:audio_input_0")], [])


def test_with_the_amp_wired_by_the_rig_only_the_tones_own_link_is_made():
    """qpwgraph made NAM's link into Sushi itself. The tone into NAM is the one
    it cannot make; the amp's own link is never made here."""
    live = [("NAM:Output", "sushi:audio_input_0"), *OUTPUTS]
    _, make, problems = sc.plan_rig(AMPED, live, True, [0])
    assert (make, problems) == ([(GUITAR, "NAM:Input")], [])

    # And if qpwgraph did not make it, it is not made for it: it is found missing.
    wanted, make, problems = sc.plan_rig(AMPED, [*OUTPUTS], True, [0])
    assert (make, problems) == ([(GUITAR, "NAM:Input")], [])
    after = [*OUTPUTS, *make]
    assert [link for link in wanted if sc.ours(link) and link not in after] == [
        ("NAM:Output", "sushi:audio_input_0")]


def test_an_amp_the_rigs_patchbay_does_not_wire_is_a_problem_not_something_to_link():
    """The bug the tool could not see (the log, 2026-11-12T18:18:32, a PASS):
    Carla up, `rig-amp.qpwgraph` with no NAM in it, the guitar going in dry."""
    _, make, problems = sc.plan_rig(ONE_CHANNEL, [*OUTPUTS], True, [0])
    assert len(problems) == 1
    assert "the rig did not wire it" in problems[0]
    assert "no link into NAM:Input and none out of NAM:Output" in problems[0]
    assert all("NAM" not in a + b for a, b in make)


def test_wire_amp_makes_the_amps_links_as_amp_patchbay_would():
    wanted, make, problems = sc.plan_rig(ONE_CHANNEL, [*OUTPUTS], True, [0], wire_amp=True)
    assert problems == []
    assert make == [("NAM:Output", "sushi:audio_input_0"), (GUITAR, "NAM:Input")]
    assert (GUITAR, "sushi:audio_input_0") not in wanted, "no dry path beside the amp"

    _, _, problems = sc.plan_rig(ONE_CHANNEL, [*OUTPUTS], False, [0], wire_amp=True)
    assert problems == ["--wire-amp, but the graph has no NAM:Input: the amp is not running"]


def test_the_caller_and_the_patchbay_must_agree_on_the_rigs_inputs():
    """`--expect 1`, the default, passed guitar_vocal with one channel not
    heard. The rig's patchbay says which inputs it has; so must the caller."""
    _, _, problems = sc.plan_rig(TWO_CHANNELS, [*OUTPUTS], False, [0])
    assert problems == [f"the rig's patchbay wires vocal ({VOCAL}), which --inputs does not name"]
    _, _, problems = sc.plan_rig(ONE_CHANNEL, [*OUTPUTS], False, [0, 1])
    assert problems == [f"vocal is expected, but the rig's patchbay has no link from {VOCAL} into Sushi or the amp"]


def test_the_tuners_tap_off_the_guitar_is_the_rigs_to_make_or_not():
    """The tuner hears the guitar's port too. That link is neither made here
    nor missed: it is not on the way to the interface's outputs."""
    tap = (GUITAR, "ALSA Capture [fmit]:input_MONO")
    wanted, make, problems = sc.plan_rig([*ONE_CHANNEL, tap], [*OUTPUTS], False, [0])
    assert (make, problems) == ([(GUITAR, "sushi:audio_input_0")], [])
    assert not sc.ours(tap)
    _, _, problems = sc.plan_rig([tap, *ONE_CHANNEL[1:]], [*OUTPUTS, tap], False, [0])
    assert problems == [f"guitar is expected, but the rig's patchbay has no link from {GUITAR} into Sushi or the amp"]


def test_a_tone_link_left_by_an_earlier_run_is_said_not_measured_through():
    live = [*OUTPUTS, (GUITAR, "sushi:audio_input_0"), (VOCAL, "sushi:audio_input_1")]
    _, make, problems = sc.plan_rig(ONE_CHANNEL, live, False, [0])
    assert make == [], "what is there already is not made, so it is not removed after"
    assert len(problems) == 1 and f"{VOCAL} -> sushi:audio_input_1" in problems[0]


# ---- the verdict ---------------------------------------------------------------

QUIET = [[-118.0, -118.0], [-118.0, -118.0]]


def logged(line):
    return json.loads(LOGGED.read_text().splitlines()[line - 1])


def test_the_amps_products_are_not_a_second_input():
    """The log's line 71, "PASS: 2 rig input(s)": one-channel electric-clean
    with the amp, the vocal's old pitches at -90 dBFS."""
    entry = logged(71)
    assert entry["verdict"].startswith("PASS: 2 rig input(s)")
    matrix = entry["matrix_dbfs"]
    assert matrix == [[-61.6, -90.5], [-61.9, -90.4]]

    verdict, lines = sc.judge_rig(matrix, QUIET, [0], 2.5)
    assert verdict == "PASS: guitar, on both sides equally"
    assert lines[1] == "vocal: not expected, not counted (left -90.5 dBFS, right -90.4 dBFS)"


def test_a_dead_vocal_fails_though_the_amps_products_read_on_its_pitches():
    """`--expect 2` on an amped guitar_vocal passed with the vocal dead."""
    verdict, lines = sc.judge_rig([[-61.6, -90.5], [-61.9, -90.4]], QUIET, [0, 1], 2.5)
    assert verdict == "FAIL: vocal was not heard"
    assert "28.8 dB under the strongest expected input, 20 allowed" in lines[1]


def test_both_inputs_of_guitar_vocal_are_heard_fourteen_decibels_apart():
    """The log's line 49. The margin has to let a real rig's quieter channel
    through: 20 dB sits between this 14 and the products' 29."""
    matrix = logged(49)["matrix_dbfs"]
    assert matrix == [[-73.3, -59.3], [-72.8, -58.4]]
    verdict, _ = sc.judge_rig(matrix, QUIET, [0, 1], 2.5)
    assert verdict == "PASS: guitar and vocal, each on both sides equally"
    assert 14.0 < sc.HEARD_WITHIN_DB < 28.8


def test_noise_alone_is_not_heard_however_loud():
    """Idle, the pitches read -114 dBFS (the log's line 41). Ten decibels more
    input gain and that clears the -105 floor on both sides, equally."""
    assert logged(41)["matrix_dbfs"][1][1] == -114.2
    noise = [[-97.0, -96.5], [-97.2, -96.8]]
    assert all(v > sc.FLOOR_DBFS for row in noise for v in row)
    verdict, lines = sc.judge_rig(noise, noise, [0, 1], 2.5)
    assert verdict == "FAIL: guitar was not heard; vocal was not heard"
    assert "0.0 dB above the level beside its pitches, 10 needed" in lines[0]


@pytest.mark.parametrize("matrix, beside, expected, said", [
    ([[-61.6, -90.5], [-61.9, -90.4]], QUIET, [], "FAIL: no rig input was expected, so nothing was tested"),
    ([], [], [0], "FAIL: nothing was measured"),
    ([[-200.0, -200.0], [-200.0, -200.0]], [[-200.0] * 2] * 2, [0], "FAIL: guitar was not heard"),
    ([[-61.6, -200.0], [-110.0, -200.0]], QUIET, [0], "FAIL: guitar is on the left only"),
    ([[-95.0, -200.0], [-61.9, -200.0]], QUIET, [0], "FAIL: guitar is on the right only"),
    ([[-61.6, -200.0], [-65.0, -200.0]], QUIET, [0], "FAIL: guitar is unbalanced by +3.4 dB"),
])
def test_what_is_never_a_pass(matrix, beside, expected, said):
    assert sc.judge_rig(matrix, beside, expected, 2.5)[0] == said


def test_inputs_are_named_or_numbered_and_nothing_else():
    assert sc.parse_inputs("guitar") == [0]
    assert sc.parse_inputs("vocal, Guitar") == [0, 1]
    assert sc.parse_inputs("1,0,1") == [0, 1]
    for bad in ("", "2", "bass", "guitar,"):
        with pytest.raises(sc.argparse.ArgumentTypeError, match="not a rig input"):
            sc.parse_inputs(bad)


# ---- the pitches ----------------------------------------------------------------


def products(freqs, order):
    """Every sum and difference of up to `order` of these pitches: {Hz: the lowest order that gives it}."""
    found = {}
    for n in range(1, order + 1):
        for chosen in itertools.combinations_with_replacement([f for g in freqs for f in (g, -g)], n):
            hz = abs(sum(chosen))
            if hz:
                found.setdefault(hz, n)
    return found


def test_the_first_pitch_sets_put_the_guitars_products_on_the_vocals_pitches():
    """What was measured as a second input, counted out: third-order products
    of the eight guitar pitches fall on six of the vocal's eight."""
    guitar = [110, 190, 310, 530, 890, 1490, 2510, 4210]
    vocal = [130, 230, 430, 710, 1130, 1870, 3070, 5090]
    assert 2 * 310 - 190 == 430
    assert sum(1 for f in vocal if products(guitar, 3).get(f) == 3) == 6


def test_neither_sets_low_order_products_land_on_the_other_or_on_the_quiet_frequencies():
    guitar, vocal = ([int(f) for f in freqs] for freqs in sc.TONE_SETS)
    quiet = {int(f) for freqs in sc.TONE_SETS for f in sc.quiet_beside(freqs)}
    from_guitar, from_vocal = products(guitar, 5), products(vocal, 5)
    assert not set(vocal) & set(from_guitar), "the guitar's products, to the 5th order, miss the vocal's pitches"
    assert not set(guitar) & set(from_vocal)
    assert not quiet & set(from_guitar)
    assert not quiet & set(from_vocal)
    # And by the rule the choice rests on, at every order: 130 x s is 70 above
    # a multiple of 140 only for s seven from a multiple of 14.
    assert {f % 140 for f in guitar} == {130} and {f % 140 for f in vocal} == {70}
    assert [s for s in range(-13, 14) if (130 * s) % 140 == 70] == [-7, 7]
    assert {(70 * s) % 140 for s in range(-13, 14)} == {0, 70}
    assert min(abs(s) for s in range(-13, 14) if (130 * s) % 140 in (60, 80)) == 6


def test_the_pitches_are_what_the_measurement_needs_of_them():
    for freqs in sc.TONE_SETS:
        assert len(freqs) == 8 and freqs == sorted(freqs)
        assert all(f % 10 == 0 for f in freqs), "on a bin of a 100 ms block"
        assert not any(f % 50 == 0 or f % 60 == 0 for f in freqs), "not where mains hum is"
        assert freqs[-1] / freqs[0] > 2 ** 4.5, "spread over about five octaves"
        assert not any(b % a == 0 for a, b in itertools.combinations(freqs, 2)), "none a harmonic of another"
    assert not set(sc.TONE_SETS[0]) & set(sc.TONE_SETS[1])
    for freqs in sc.TONE_SETS:
        quiet = sc.quiet_beside(freqs)
        assert all(50 <= f - q <= 130 and q % 10 == 0 for f, q in zip(freqs, quiet))
        assert not set(quiet) & set(sc.TONE_SETS[0] + sc.TONE_SETS[1])


# ---- measured: generated signals through the tool's own analysis ---------------

RATE = 16000  # the analysis takes the rate from the file; a third of the work at this one


def signal_of(levels, noise_db, seconds=1.0, seed=1):
    """Two sides of `seconds`: each rig input's eight pitches at a set level
    (dBFS, its pitches together; None for absent) over white noise."""
    rng = random.Random(seed)
    n = int(seconds * RATE)
    sides = []
    for side in (0, 1):
        tones = [(10 ** (level / 20.0) / math.sqrt(len(freqs)), 2 * math.pi * f / RATE)
                 for level, freqs in zip(levels[side], sc.TONE_SETS) if level is not None for f in freqs]
        sigma = 10 ** (noise_db / 20.0)
        sides.append([sum(a * math.sin(w * i) for a, w in tones) + rng.gauss(0.0, sigma) for i in range(n)])
    return sides


def test_tones_over_noise_are_heard_and_read_at_their_level():
    chans = signal_of([[-60.0, -62.0], [-61.0, -62.5]], noise_db=-80.0)
    matrix, beside = sc.measure_rig(chans, RATE)
    assert matrix == [[pytest.approx(-60.0, abs=0.5), pytest.approx(-62.0, abs=0.5)],
                      [pytest.approx(-61.0, abs=0.5), pytest.approx(-62.5, abs=0.5)]]
    assert all(v < -85.0 for row in beside for v in row), "beside the pitches is the noise, not the tones"
    assert sc.judge_rig(matrix, beside, [0, 1], 2.5)[0] == "PASS: guitar and vocal, each on both sides equally"


def test_loud_noise_with_no_tone_in_it_is_measured_as_not_heard():
    """Broadband noise reading about -85 dBFS at the pitches, 20 dB over the
    fixed floor, the same on both sides: the old test's "heard and balanced"."""
    chans = signal_of([[None, None], [None, None]], noise_db=-60.0)
    matrix, beside = sc.measure_rig(chans, RATE)
    assert all(v > sc.FLOOR_DBFS + 10 for row in matrix for v in row)
    assert abs(matrix[0][0] - matrix[1][0]) < 2.5 and abs(matrix[0][1] - matrix[1][1]) < 2.5
    verdict, _ = sc.judge_rig(matrix, beside, [0, 1], 2.5)
    assert verdict == "FAIL: guitar was not heard; vocal was not heard"


# ---- `rig` against stand-ins for pw-link, pw-record and the rest ----------------------


class World:
    """A graph `pw-link` reads and changes, and a recorder that writes a file."""

    def __init__(self, sc, monkeypatch, tmp_path, live, wanted, recording, amp=False, refuse=()):
        self.links, self.calls, self.refuse, self.recorders = list(live), [], set(refuse), []
        self.amp = amp
        self.file = tmp_path / "rig.qpwgraph"
        self.file.write_text(patchbay(*wanted))
        self.log = tmp_path / "log.jsonl"
        world = self

        class Recorder:
            def __init__(self, command, **_):
                self.command, self.running, self.pid = command, True, 4242
                world.recorders.append(self)
                if recording is not None:
                    with wave.open(command[-1], "wb") as f:
                        f.setnchannels(2), f.setsampwidth(4), f.setframerate(RATE)
                        f.writeframes(recording)

            def poll(self):
                return None if self.running else 0

            def terminate(self):
                self.running = False

            def wait(self, timeout=None):
                return 0

        monkeypatch.setattr(sc, "LOG", self.log)
        monkeypatch.setattr(sc, "run", self.run)
        monkeypatch.setattr(sc, "sushi_pids", lambda: [100])
        monkeypatch.setattr(sc, "pw_nodes", lambda: {"Audio/Source": {"name": "iD14-in", "position": ["AUX0", "AUX1"]}})
        monkeypatch.setattr(sc, "time", SimpleNamespace(sleep=self.sleep))
        monkeypatch.setattr(sc, "subprocess", SimpleNamespace(
            Popen=Recorder, DEVNULL=None, TimeoutExpired=subprocess.TimeoutExpired))
        self.sleeps = 0
        self.on_sleep = None

    def sleep(self, seconds):
        self.sleeps += 1
        if self.on_sleep:
            self.on_sleep(self.sleeps)

    def run(self, cmd, **_):
        done = lambda code=0, out="", err="": SimpleNamespace(returncode=code, stdout=out, stderr=err)  # noqa: E731
        if cmd[0] == "pgrep":
            return done(out=f"77 qpwgraph -a {self.file} -m\n")
        assert cmd[0] == "pw-link", cmd
        if cmd[1] == "-l":
            ports = {}
            for out, inp in self.links:
                ports.setdefault(out, []).append(f"  |-> {inp}")
                ports.setdefault(inp, []).append(f"  |<- {out}")
            return done(out="".join(f"{port}\n" + "".join(f"{line}\n" for line in lines)
                                    for port, lines in ports.items()))
        if cmd[1] == "-i":
            return done(out="sushi:audio_input_0\nsushi:audio_input_1\n" + ("NAM:Input\n" if self.amp else ""))
        self.calls.append(tuple(cmd[1:]))
        if cmd[1] == "-d":
            self.links.remove((cmd[2], cmd[3]))
            return done()
        if (cmd[1], cmd[2]) in self.refuse:
            return done(1, err="failed to link ports: No such file or directory")
        self.links.append((cmd[1], cmd[2]))
        return done()

    def args(self, inputs, **more):
        return SimpleNamespace(inputs=inputs, expect=None, patchbay=str(self.file), wire_amp=False,
                               label="test", file=None, seconds=3.0, tolerance=2.5, **more)

    def entries(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()]


def frames(levels, noise_db=-90.0):
    left, right = signal_of(levels, noise_db)
    data = array.array("i", (int(v * 2147483647) for pair in zip(left, right) for v in pair))
    return data.tobytes()


@pytest.fixture(scope="module")
def balanced_guitar():
    return frames([[-60.0, None], [-60.5, None]])


@pytest.fixture
def fresh(monkeypatch):
    """The tool loaded again, so what a test stands in for is gone with the test."""
    return load()


def test_rig_links_the_tone_measures_and_takes_its_link_down_again(fresh, monkeypatch, tmp_path, balanced_guitar, capsys):
    """The amp wired by the rig: the tone into NAM is made here, NAM's link
    into Sushi was the rig's and is left alone, and afterwards the graph is as
    it was found."""
    before = [("NAM:Output", "sushi:audio_input_0"), *OUTPUTS]
    world = World(fresh, monkeypatch, tmp_path, before, AMPED, balanced_guitar, amp=True)
    assert fresh.cmd_rig(world.args([0])) == 0
    assert world.calls == [(GUITAR, "NAM:Input"), ("-d", GUITAR, "NAM:Input")]
    assert world.links == before
    assert not world.recorders[0].running, "the recorder was stopped"
    assert not Path(world.recorders[0].command[-1]).parent.exists(), "and its directory removed"
    entry = world.entries()[-1]
    assert entry["verdict"] == "PASS: guitar, on both sides equally"
    assert entry["expected"] == ["guitar"] and entry["made"] == [f"{GUITAR} -> NAM:Input"]
    assert entry["pitches_hz"] == fresh.TONE_SETS and entry["patchbay"] == str(world.file)
    assert "vocal: not expected, not counted" in capsys.readouterr().out


def test_rig_fails_an_amp_the_rig_left_unwired_and_links_nothing(fresh, monkeypatch, tmp_path, balanced_guitar):
    world = World(fresh, monkeypatch, tmp_path, [*OUTPUTS], ONE_CHANNEL, balanced_guitar, amp=True)
    assert fresh.cmd_rig(world.args([0])) == 1
    assert world.calls == [] and world.recorders == []
    assert "the amp is running and the rig did not wire it" in world.entries()[-1]["verdict"]


def test_rig_names_the_amps_link_the_rig_asked_for_and_did_not_get(fresh, monkeypatch, tmp_path, balanced_guitar):
    world = World(fresh, monkeypatch, tmp_path, [*OUTPUTS], AMPED, balanced_guitar, amp=True)
    assert fresh.cmd_rig(world.args([0])) == 1
    assert world.entries()[-1]["verdict"] == (
        "FAIL: the rig's patchbay asks for links that are not there: NAM:Output -> sushi:audio_input_0")
    assert world.links == [*OUTPUTS], "the tone's link was made, and removed"


def test_wire_amp_says_so_in_the_verdict_and_removes_both_links(fresh, monkeypatch, tmp_path, balanced_guitar):
    world = World(fresh, monkeypatch, tmp_path, [*OUTPUTS], ONE_CHANNEL, balanced_guitar, amp=True)
    args = world.args([0])
    args.wire_amp = True
    assert fresh.cmd_rig(args) == 0
    assert world.calls == [("NAM:Output", "sushi:audio_input_0"), (GUITAR, "NAM:Input"),
                           ("-d", GUITAR, "NAM:Input"), ("-d", "NAM:Output", "sushi:audio_input_0")]
    assert world.links == [*OUTPUTS]
    assert world.entries()[-1]["verdict"] == (
        "PASS: guitar, on both sides equally (the amp was wired by --wire-amp: "
        "this says nothing about the rig's own amp wiring)")


def test_a_link_pw_link_refuses_is_a_fail_and_what_was_made_before_it_is_removed(
        fresh, monkeypatch, tmp_path, balanced_guitar):
    """Its exit status used to be ignored."""
    world = World(fresh, monkeypatch, tmp_path, [*OUTPUTS], TWO_CHANNELS, balanced_guitar,
                  refuse={(VOCAL, "sushi:audio_input_1")})
    assert fresh.cmd_rig(world.args([0, 1])) == 1
    assert world.entries()[-1]["verdict"] == (
        f"FAIL: could not link {VOCAL} -> sushi:audio_input_1: failed to link ports: No such file or directory")
    assert world.calls[-1] == ("-d", GUITAR, "sushi:audio_input_0")
    assert world.links == [*OUTPUTS] and world.recorders == []


def test_rig_refuses_a_loop_closed_through_the_amp_and_links_nothing(fresh, monkeypatch, tmp_path, balanced_guitar):
    """The real machine file with the amp, the loop cabled: refused, not warned of."""
    real = [(CAPTURE, "NAM:Input"), ("NAM:Output", "sushi:audio_input_0"), *OUTPUTS]
    world = World(fresh, monkeypatch, tmp_path, real, AMPED, balanced_guitar, amp=True)
    with pytest.raises(SystemExit) as stopped:
        fresh.cmd_rig(world.args([0]))
    assert str(stopped.value).startswith("STOP: ") and CAPTURE in str(stopped.value)
    assert world.calls == [] and world.recorders == []
    assert world.entries()[-1]["verdict"].startswith("FAIL: refused, the interface feeds Sushi")


def test_rig_refuses_when_its_own_links_would_close_the_loop(fresh, monkeypatch, tmp_path, balanced_guitar):
    """The interface already on the amp's input, the amp not yet into Sushi:
    no loop until --wire-amp makes NAM's link. Looked for before it is made."""
    world = World(fresh, monkeypatch, tmp_path, [(CAPTURE, "NAM:Input"), *OUTPUTS], ONE_CHANNEL,
                  balanced_guitar, amp=True)
    args = world.args([0])
    args.wire_amp = True
    with pytest.raises(SystemExit, match="STOP"):
        fresh.cmd_rig(args)
    assert world.calls == []


def test_a_kill_while_recording_stops_the_recorder_and_removes_the_links(fresh, monkeypatch, tmp_path, balanced_guitar):
    world = World(fresh, monkeypatch, tmp_path, [*OUTPUTS], ONE_CHANNEL, balanced_guitar)

    def killed(count):
        if count == 2:  # the second sleep is the listening
            raise fresh.Stopped(signal.SIGTERM)

    world.on_sleep = killed
    with pytest.raises(fresh.Stopped):
        fresh.cmd_rig(world.args([0]))
    assert world.links == [*OUTPUTS]
    assert not world.recorders[0].running
    assert not Path(world.recorders[0].command[-1]).parent.exists()


def test_an_empty_recording_is_a_fail(fresh, monkeypatch, tmp_path):
    world = World(fresh, monkeypatch, tmp_path, [*OUTPUTS], ONE_CHANNEL, b"")
    assert fresh.cmd_rig(world.args([0])) == 1
    assert world.entries()[-1]["verdict"] == "FAIL: nothing was measured, the recording is empty"
    world = World(fresh, monkeypatch, tmp_path, [*OUTPUTS], ONE_CHANNEL, None)
    assert fresh.cmd_rig(world.args([0])) == 1
    assert "nothing was measured, the recording cannot be read" in world.entries()[-1]["verdict"]


# ---- the command line, and a real kill --------------------------------------------


def cli(*args):
    return subprocess.run([sys.executable, "-I", str(SCRIPT), *args], capture_output=True, text=True, timeout=30)


@pytest.mark.parametrize("command", [[], ["iface"], ["pw"], ["links"], ["tones"], ["rig"], ["log"]])
def test_every_help_still_works(command):
    done = cli(*command, "--help")
    assert done.returncode == 0 and done.stdout.startswith("usage: stereo_check.py")


@pytest.mark.parametrize("args, said", [
    (["--expect", "0"], "--expect counted the inputs heard without saying which"),
    (["--expect", "2"], "--expect counted the inputs heard without saying which"),
    ([], "say which inputs the rig has"),
    (["--inputs", "bass"], "'bass' is not a rig input"),
    (["--inputs", ""], "'' is not a rig input"),
])
def test_rig_is_refused_until_it_is_told_which_inputs(args, said):
    before = LOGGED.read_bytes()
    done = cli("rig", *args)
    assert done.returncode != 0 and said in done.stderr
    assert LOGGED.read_bytes() == before, "a refusal is not a run"


def children_of(pid):
    out = subprocess.run(["pgrep", "-P", str(pid)], capture_output=True, text=True).stdout
    return [int(p) for p in out.split()]


def gone(pid):
    try:
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0] == "Z"
    except OSError:
        return True


@pytest.mark.parametrize("signum", [signal.SIGTERM, signal.SIGHUP])
def test_a_plain_kill_runs_the_cleanup(tmp_path, signum):
    """`iface` and `pw` under a kill: the recorder and the player are stopped
    by pid and the temporary directory goes, where the default action would
    have left `arecord` holding the card. The recorder and the player here are
    two `sleep`s; the function and the handler are the tool's own."""
    script = tmp_path / "run.py"
    script.write_text(
        "import importlib.util, sys, tempfile\n"
        f"spec = importlib.util.spec_from_file_location('stereo_check', {str(SCRIPT)!r})\n"
        "m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)\n"
        "m.stop_on_signals()\n"
        "try:\n"
        "    with tempfile.TemporaryDirectory() as d:\n"
        "        print(d, flush=True)\n"
        "        m.record_play(['sleep', '60'], ['sleep', '61'], 0)\n"
        "except m.Stopped as stopped:\n"
        "    sys.exit(128 + stopped.signum)\n"
    )
    proc = subprocess.Popen([sys.executable, "-I", str(script)], stdout=subprocess.PIPE, text=True)
    children = []
    try:
        directory = Path(proc.stdout.readline().strip())
        deadline = time.monotonic() + 10
        while len(children) < 2 and time.monotonic() < deadline:  # the player starts a second after the recorder
            time.sleep(0.1)
            children = children_of(proc.pid)
        assert len(children) == 2 and directory.is_dir()
        os.kill(proc.pid, signum)
        assert proc.wait(timeout=10) == 128 + signum
        assert not directory.exists()
        assert all(gone(pid) for pid in children)
    finally:
        for pid in [proc.pid, *children]:
            if not gone(pid):
                os.kill(pid, signal.SIGKILL)
        proc.wait()
