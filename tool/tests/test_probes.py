"""Which ports each selftest probe links (#49 part 2's decisions).

Against the committed rigs, so a routing change that moves a probe shows here,
and against small rigs written to put one rule under strain each.
"""

from __future__ import annotations

import textwrap

import pytest

from sushi_rig.paths import checkout_root
from sushi_rig.probes import ProbeError, ProbePoint, plan_probes
from sushi_rig.spec import RigSpec

SOURCES = checkout_root() / "config" / "src"


def rig(tmp_path, tracks: str) -> RigSpec:
    path = tmp_path / "rig.yaml"
    path.write_text(
        'meta:\n  name: t\n  version: "1"\nhost:\n  samplerate: 48000\ntracks:\n'
        + textwrap.indent(textwrap.dedent(tracks), "  ")
    )
    return RigSpec.load(path)


def by_name(plan) -> dict[str, ProbePoint]:
    return {point.name: point for point in plan.points}


def test_guitar_vocal_as_committed(tmp_path):
    plan = plan_probes(RigSpec.load(SOURCES / "guitar_vocal.yaml"), amp=True)
    points = by_name(plan)
    assert list(points) == [
        "guitar through sushi (sushi:audio_output_0)",
        "guitar through sushi (sushi:audio_output_1)",
        "vocal through sushi (sushi:audio_output_0)",
        "vocal through sushi (sushi:audio_output_1)",
        "amp",
        "guitar via amp (sushi:audio_output_0)",
        "guitar via amp (sushi:audio_output_1)",
    ]
    # The vocal goes in on bus 1 and the guitar on bus 0.
    assert points["vocal through sushi (sushi:audio_output_0)"].inject == (
        "sushi:audio_input_2", "sushi:audio_input_3",
    )
    assert points["amp"] == ProbePoint("amp", ("NAM:Input",), "NAM:Output", nonlinear=True)
    # Today both tracks sum onto bus 0, which the plan says rather than hides.
    assert plan.not_checked == (
        "separation of guitar and vocal: both come out of sushi:audio_output_0, "
        "sushi:audio_output_1, so whether either leaks into the other cannot be measured",
    )


def test_every_half_of_a_stereo_output_is_its_own_probe(tmp_path):
    """The guitar once came out of the left speaker only; a probe reading one
    side, or the two summed, would have passed it."""
    plan = plan_probes(RigSpec.load(SOURCES / "electric_board.yaml"), amp=False)
    assert [p.capture for p in plan.points] == ["sushi:audio_output_0", "sushi:audio_output_1"]


def test_without_the_amp_nothing_is_asserted_nonlinear():
    plan = plan_probes(RigSpec.load(SOURCES / "guitar_vocal.yaml"), amp=False)
    assert plan.points and all(p.nonlinear is None for p in plan.points)
    assert not any("NAM" in port for p in plan.points for port in p.inject)


def test_the_amp_follows_the_guitar_role_not_the_first_track(tmp_path):
    spec = rig(tmp_path, """\
        - name: vocal
          channels: 2
          source: vocal-in
          inputs: [{engine_bus: 0, track_bus: 0}]
          outputs: [{engine_bus: 0, track_bus: 0}]
        - name: guitar
          channels: 2
          source: guitar-in
          inputs: [{engine_bus: 1, track_bus: 0}]
          outputs: [{engine_bus: 1, track_bus: 0}]
        """)
    via = [p for p in plan_probes(spec, amp=True).points if "via amp" in p.name]
    assert [p.capture for p in via] == ["sushi:audio_output_2", "sushi:audio_output_3"]
    assert all(p.name.startswith("guitar via amp") for p in via)


def test_channel_form_routing_injects_and_reads_the_channels_named(tmp_path):
    spec = rig(tmp_path, """\
        - name: mono
          channels: 1
          inputs: [{engine_channel: 4, track_channel: 0}]
          outputs: [{engine_channel: 5, track_channel: 0}]
        """)
    (point,) = plan_probes(spec, amp=False).points
    assert point.inject == ("sushi:audio_input_4",)
    assert point.capture == "sushi:audio_output_5"


def test_tracks_with_outputs_of_their_own_can_be_separated_but_are_not_yet(tmp_path):
    spec = rig(tmp_path, """\
        - name: a
          channels: 2
          inputs: [{engine_bus: 0, track_bus: 0}]
          outputs: [{engine_bus: 0, track_bus: 0}]
        - name: b
          channels: 2
          inputs: [{engine_bus: 1, track_bus: 0}]
          outputs: [{engine_bus: 1, track_bus: 0}]
        """)
    assert plan_probes(spec, amp=False).not_checked == (
        "separation of a, b: each has an output of its own, so whether one leaks into "
        "another can be measured, but no probe for it exists yet",
    )


def test_a_track_with_no_outputs_is_said_to_be_unprobed(tmp_path):
    spec = rig(tmp_path, """\
        - name: send_only
          channels: 2
          inputs: [{engine_bus: 0, track_bus: 0}]
          outputs: []
        """)
    plan = plan_probes(spec, amp=False)
    assert plan.points == ()
    assert plan.not_checked == ("send_only: no outputs on Sushi, so nothing to probe",)


def test_an_amp_in_front_of_nothing_is_refused(tmp_path):
    spec = rig(tmp_path, """\
        - name: vocal
          channels: 2
          source: vocal-in
          inputs: [{engine_bus: 0, track_bus: 0}]
          outputs: [{engine_bus: 0, track_bus: 0}]
        """)
    with pytest.raises(ProbeError, match="no track takes 'guitar-in'"):
        plan_probes(spec, amp=True)


def test_two_tracks_claiming_the_guitar_are_refused(tmp_path):
    spec = rig(tmp_path, """\
        - name: a
          channels: 2
          source: guitar-in
          inputs: [{engine_bus: 0, track_bus: 0}]
          outputs: [{engine_bus: 0, track_bus: 0}]
        - name: b
          channels: 2
          source: guitar-in
          inputs: [{engine_bus: 1, track_bus: 0}]
          outputs: [{engine_bus: 1, track_bus: 0}]
        """)
    with pytest.raises(ProbeError, match="tracks 'a', 'b' all take 'guitar-in'"):
        plan_probes(spec, amp=True)
    # Without the amp, two guitars are nobody's problem.
    assert len(plan_probes(spec, amp=False).points) == 4


def test_an_amp_in_front_of_a_guitar_with_no_outputs_says_so(tmp_path):
    spec = rig(tmp_path, """\
        - name: g
          channels: 2
          source: guitar-in
          inputs: [{engine_bus: 0, track_bus: 0}]
          outputs: []
        """)
    with pytest.raises(ProbeError) as caught:
        plan_probes(spec, amp=True)
    assert caught.value.problems == [
        "the amp is in front of 'g', which has no outputs on Sushi, so what the "
        "amp does can never be heard"
    ]


def test_a_broken_guitar_track_is_reported_as_broken_not_as_missing(tmp_path):
    spec = rig(tmp_path, """\
        - name: g
          channels: 2
          source: guitar-in
          inputs: [{track_bus: 0}]
          outputs: [{engine_bus: 0, track_bus: 0}]
        """)
    with pytest.raises(ProbeError) as caught:
        plan_probes(spec, amp=True)
    (problem,) = caught.value.problems
    assert "names no engine_bus or engine_channel" in problem


def test_tracks_sharing_inputs_and_outputs_cannot_be_told_apart(tmp_path):
    spec = rig(tmp_path, """\
        - name: a
          channels: 2
          inputs: [{engine_bus: 0, track_bus: 0}]
          outputs: [{engine_bus: 0, track_bus: 0}]
        - name: b
          channels: 2
          inputs: [{engine_bus: 0, track_bus: 0}]
          outputs: [{engine_bus: 0, track_bus: 0}]
        """)
    (note,) = plan_probes(spec, amp=False).not_checked
    assert note.startswith("a and b: both read sushi:audio_input_0, sushi:audio_input_1")
    assert "a silent chain on one is heard through the other" in note


def test_every_routing_problem_is_reported_at_once(tmp_path):
    spec = rig(tmp_path, """\
        - name: a
          channels: 2
          inputs: [{track_bus: 0}]
          outputs: [{engine_bus: 0, track_bus: 0}]
        - name: b
          channels: 2
          inputs: [{engine_bus: 1, track_bus: 0}]
          outputs: [{track_bus: 0}]
        """)
    with pytest.raises(ProbeError) as caught:
        plan_probes(spec, amp=False)
    assert len(caught.value.problems) == 2
    assert "track 'a'" in caught.value.problems[0] and "track 'b'" in caught.value.problems[1]
    # A track with no role is "the track", never a placeholder role.
    assert "which Sushi input the track takes is unknown" in caught.value.problems[0]


@pytest.mark.parametrize("source", sorted(SOURCES.glob("*.yaml")), ids=lambda p: p.name)
def test_every_committed_rig_has_a_plan(source):
    """With the amp and without: none of the committed sources names an amp,
    but each can be run with one, by `up --amp-model`."""
    assert any(p.nonlinear for p in plan_probes(RigSpec.load(source), amp=True).points)
    assert plan_probes(RigSpec.load(source), amp=False).points


def test_the_command_prints_the_plan_and_what_it_cannot_check(capsys):
    from sushi_rig.cli import main

    assert main(["probes", str(SOURCES / "guitar_vocal.yaml")]) == 0
    out = capsys.readouterr().out
    # No amp unless asked for: guitar_vocal names none, and `up` starts none.
    assert "NAM" not in out
    assert "NOT CHECKED separation of guitar and vocal" in out
    assert main(["probes", str(SOURCES / "guitar_vocal.yaml"), "--amp"]) == 0
    out = capsys.readouterr().out
    assert "amp\n  inject  NAM:Input\n  listen  NAM:Output  must add harmonics\n" in out


def test_the_command_fails_loudly_on_a_file_it_cannot_read(tmp_path, capsys):
    from sushi_rig.cli import main

    assert main(["probes", str(tmp_path / "missing.yaml")]) == 1
    assert capsys.readouterr().out.startswith("FAIL ")
    (tmp_path / "bad.yaml").write_text("tracks: [unclosed")
    assert main(["probes", str(tmp_path / "bad.yaml")]) == 1
    assert capsys.readouterr().out.startswith("FAIL ")


def test_the_command_fails_loudly_on_a_rig_it_cannot_probe(tmp_path, capsys):
    from sushi_rig.cli import main

    rig(tmp_path, """\
        - name: vocal
          channels: 2
          source: vocal-in
          inputs: [{engine_bus: 0, track_bus: 0}]
          outputs: [{engine_bus: 0, track_bus: 0}]
        """)
    assert main(["probes", str(tmp_path / "rig.yaml"), "--amp"]) == 1
    assert capsys.readouterr().out.startswith("FAIL the amp is on but no track takes")
    assert main(["probes", str(tmp_path / "rig.yaml")]) == 0


def test_the_command_refuses_a_rig_that_does_not_validate(tmp_path, capsys):
    from sushi_rig.cli import main

    rig(tmp_path, """\
        - name: a
          channels: 2
          inputs: [{engine_bus: 0, track_bus: 0}]
          outputs: [{engine_bus: 0, track_bus: 0}]
        - name: a
          channels: 2
          inputs: [{engine_bus: 1, track_bus: 0}]
          outputs: [{engine_bus: 1, track_bus: 0}]
        """)
    assert main(["probes", str(tmp_path / "rig.yaml")]) == 1
    assert capsys.readouterr().out.startswith("FAIL rig.yaml: ")


# --------------------------------------------------------------------------- #
# A v2 rig: channels and a master (#30)
# --------------------------------------------------------------------------- #

RIGS = checkout_root() / "config" / "rigs"


def test_a_v2_rig_is_probed_through_each_stem_and_through_the_master():
    from sushi_rig.rigfile import spec_of

    points = by_name(plan_probes(spec_of(RIGS / "guitar_vocal.yaml"), amp=False))
    # A mono channel is one engine input, injected once.
    assert points["guitar through sushi (sushi:audio_output_2)"].inject == ("sushi:audio_input_0",)
    assert points["vocal through sushi (sushi:audio_output_4)"].inject == ("sushi:audio_input_1",)
    # What the monitors hear: every channel, through its send, out of the master.
    assert points["guitar through the master (sushi:audio_output_0)"].inject == ("sushi:audio_input_0",)
    assert points["vocal through the master (sushi:audio_output_1)"].capture == "sushi:audio_output_1"
    # The master track has no inputs of its own and is not called unprobed.
    assert not any(note.startswith("master:") for note in plan_probes(
        spec_of(RIGS / "guitar_vocal.yaml"), amp=False).not_checked)


def test_with_the_amp_a_v2_rig_is_probed_from_the_amp_to_the_monitors():
    from sushi_rig.rigfile import spec_of

    points = by_name(plan_probes(spec_of(RIGS / "guitar_vocal.yaml"), amp=True))
    via = points["guitar via amp and the master (sushi:audio_output_0)"]
    assert via.inject == ("NAM:Input",) and via.nonlinear is True


def test_a_send_with_no_return_to_follow_is_said_not_probed(tmp_path):
    spec = rig(tmp_path, """\
        - name: a
          channels: 2
          inputs: [{engine_bus: 0, track_bus: 0}]
          outputs: [{engine_bus: 0, track_bus: 0}]
          plugins: [{name: a_send, type: internal, uid: sushi.testing.send}]
        """)
    (note,) = [n for n in plan_probes(spec, amp=False).not_checked if "send to" in n]
    assert note.startswith("a send to no return")
