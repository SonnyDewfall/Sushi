"""Which ports each selftest probe links (#49 part 2's decisions).

Against the committed rigs, so a routing change that moves a probe shows here,
and against small rigs written to put one rule under strain each. Since #81 a
probe through Sushi is played through a probe track on inputs and outputs the
rig leaves free, so most of what is pinned here is which free ports those are.
"""

from __future__ import annotations

import textwrap

import pytest

from sushi_rig.paths import checkout_root
from sushi_rig.probes import (
    PROBE_TRACK,
    ProbeError,
    ProbePoint,
    ProbeTrack,
    crowded_ports,
    plan_probes,
    probe_links,
)
from sushi_rig.spec import RigSpec

SOURCES = checkout_root() / "config" / "src"
RUNNING = "the running tracks: each probe through Sushi plays a copy of the chain"


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
    # The rig reads buses 0 and 1 and writes bus 0, so the probe track takes
    # input bus 2 and output bus 1: ports nothing in the rig touches.
    assert list(points) == [
        "guitar through sushi (sushi:audio_output_2)",
        "guitar through sushi (sushi:audio_output_3)",
        "vocal through sushi (sushi:audio_output_2)",
        "vocal through sushi (sushi:audio_output_3)",
        "amp",
        "guitar via amp (sushi:audio_output_2)",
        "guitar via amp (sushi:audio_output_3)",
    ]
    assert points["vocal through sushi (sushi:audio_output_2)"].inject == (
        "sushi:audio_input_4", "sushi:audio_input_5",
    )
    assert points["vocal through sushi (sushi:audio_output_2)"].track.copy_of == ("vocal",)
    # The amp cannot be copied, so it is probed where the guitar plays into it,
    # and says so.
    assert points["amp"] == ProbePoint(
        "amp", ("NAM:Input",), "NAM:Output", nonlinear=True, beside="guitar-in",
    )
    assert points["guitar via amp (sushi:audio_output_2)"].links == (
        ("NAM:Output", "sushi:audio_input_4"), ("NAM:Output", "sushi:audio_input_5"),
    )
    # Today both tracks sum onto bus 0, which the plan says rather than hides.
    assert plan.not_checked[0].startswith(RUNNING)
    assert plan.not_checked[1:] == (
        "separation of guitar and vocal: both come out of sushi:audio_output_0, "
        "sushi:audio_output_1, so whether either leaks into the other cannot be measured",
    )


def test_every_half_of_a_stereo_output_is_its_own_probe(tmp_path):
    """The guitar once came out of the left speaker only; a probe reading one
    side, or the two summed, would have passed it."""
    plan = plan_probes(RigSpec.load(SOURCES / "electric_board.yaml"), amp=False)
    assert [p.capture for p in plan.points] == ["sushi:audio_output_2", "sushi:audio_output_3"]


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
    # Buses 0 and 1 are the rig's, so the probe track is on bus 2 each way.
    assert [p.capture for p in via] == ["sushi:audio_output_4", "sushi:audio_output_5"]
    assert all(p.name.startswith("guitar via amp") for p in via)
    assert all(p.track.copy_of == ("guitar",) for p in via)


def test_channel_form_routing_injects_and_reads_the_channels_named(tmp_path):
    spec = rig(tmp_path, """\
        - name: mono
          channels: 1
          inputs: [{engine_channel: 4, track_channel: 0}]
          outputs: [{engine_channel: 5, track_channel: 0}]
        """)
    (point,) = plan_probes(spec, amp=False).points
    # Moved to the lowest free channel each way, and kept in channel form.
    assert point.inject == ("sushi:audio_input_0",)
    assert point.capture == "sushi:audio_output_0"
    assert point.track.to_spec().inputs == [{"engine_channel": 0, "track_channel": 0}]


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
    assert plan_probes(spec, amp=False).not_checked[1:] == (
        "separation of a/b: outputs of their own, so whether one leaks into the "
        "other can be measured, but no probe for it exists yet",
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
        "the amp is in front of 'g', which has no outputs on Sushi and no master "
        "to send to, so what the amp does can never be heard"
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


def test_tracks_sharing_inputs_and_outputs_are_each_probed_on_their_own(tmp_path):
    """Probed beside each other, a silent chain on one was heard through the
    other. On a probe track each is played alone."""
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
    plan = plan_probes(spec, amp=False)
    assert {p.track.copy_of for p in plan.points} == {("a",), ("b",)}
    assert all(p.inject == ("sushi:audio_input_2", "sushi:audio_input_3") for p in plan.points)
    running, note = plan.not_checked
    assert running.startswith(RUNNING)
    assert note.startswith("separation of a and b: both come out of sushi:audio_output_0")


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
    assert "  beside  guitar-in: the instrument feeds this too" in out
    assert "  link    NAM:Output -> sushi:audio_input_4\n" in out
    assert f"  track   {PROBE_TRACK}: a copy of guitar's chain (6 plugins)\n" in out


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

    plan = plan_probes(spec_of(RIGS / "guitar_vocal.yaml"), amp=False)
    points = by_name(plan)
    # A mono channel is one engine input, injected once — on input 2, the
    # first the guitar (0) and the vocal (1) leave free, and out of bus 3,
    # after the master's 0 and the stems' 1 and 2.
    assert points["guitar through sushi (sushi:audio_output_6)"].inject == ("sushi:audio_input_2",)
    assert points["vocal through sushi (sushi:audio_output_7)"].inject == ("sushi:audio_input_2",)
    # The master's chain is empty, so its path would be the stem's again.
    assert not any("through the master" in name for name in points)
    assert any(n.startswith("guitar, vocal through the master: the master's chain is empty")
               for n in plan.not_checked)
    # The master track has no inputs of its own and is not called unprobed.
    assert not any(note.startswith("master:") for note in plan.not_checked)


def test_with_the_amp_a_v2_rig_is_probed_from_the_amp_to_the_monitors():
    from sushi_rig.rigfile import spec_of

    points = by_name(plan_probes(spec_of(RIGS / "guitar_vocal.yaml"), amp=True))
    via = points["guitar via amp (sushi:audio_output_6)"]
    assert via.inject == ("NAM:Input",) and via.nonlinear is True
    assert via.links == (("NAM:Output", "sushi:audio_input_2"),)
    assert via.beside == "guitar-in"


def test_a_send_with_no_return_to_follow_is_said_not_probed(tmp_path):
    spec = rig(tmp_path, """\
        - name: a
          channels: 2
          inputs: [{engine_bus: 0, track_bus: 0}]
          outputs: [{engine_bus: 0, track_bus: 0}]
          plugins: [{name: a_send, type: internal, uid: sushi.testing.send}]
        """)
    (note,) = [n for n in plan_probes(spec, amp=False).not_checked if "sends" in n]
    assert note.startswith("a: sends somewhere this plan cannot follow")


MASTER_TRACK = """\
        - name: master
          channels: 2
          inputs: []
          outputs: {outputs}
          plugins: [{{name: master_return, type: internal, uid: sushi.testing.return}}]
        """


def test_a_channel_heard_only_through_the_master_is_probed_and_amped(tmp_path):
    spec = rig(tmp_path, textwrap.dedent("""\
        - name: g
          channels: 2
          source: guitar-in
          inputs: [{engine_channel: 0, track_channel: 0}, {engine_channel: 0, track_channel: 1}]
          outputs: []
          plugins: [{name: g_send, type: internal, uid: sushi.testing.send}]
        """) + textwrap.dedent(MASTER_TRACK.format(outputs="[{engine_bus: 0, track_bus: 0}]")))
    plan = plan_probes(spec, amp=True)
    names = [p.name for p in plan.points]
    # Its only probe, so made even with the master's chain empty: the
    # channel's inputs and the master's outputs, both moved to free places.
    assert "g through the master (sushi:audio_output_2)" in names
    assert "g via amp and the master (sushi:audio_output_3)" in names
    (running,) = plan.not_checked
    assert running.startswith(RUNNING)


def test_a_master_with_no_outputs_is_said_to_reach_nothing(tmp_path):
    spec = rig(tmp_path, textwrap.dedent("""\
        - name: a
          channels: 2
          inputs: [{engine_bus: 0, track_bus: 0}]
          outputs: [{engine_bus: 1, track_bus: 0}]
          plugins: [{name: a_send, type: internal, uid: sushi.testing.send}]
        """) + textwrap.dedent(MASTER_TRACK.format(outputs="[]")))
    notes = plan_probes(spec, amp=False).not_checked
    assert any(n.startswith("a: sent to the master, which has no outputs") for n in notes)


def test_a_return_that_is_not_the_compiled_master_is_not_followed(tmp_path):
    """A reverb aux's return is not the monitor path, and probing through it
    would pass on the wrong path."""
    spec = rig(tmp_path, """\
        - name: a
          channels: 2
          inputs: [{engine_bus: 0, track_bus: 0}]
          outputs: [{engine_bus: 0, track_bus: 0}]
          plugins: [{name: to_verb, type: internal, uid: sushi.testing.send}]
        - name: verb
          channels: 2
          source: guitar-in
          inputs: [{engine_bus: 1, track_bus: 0}]
          outputs: [{engine_bus: 1, track_bus: 0}]
          plugins: [{name: verb_return, type: internal, uid: sushi.testing.return}]
        """)
    plan = plan_probes(spec, amp=True)
    assert not any("through the" in p.name for p in plan.points)
    # A return track with inputs of its own is still probed, and still the guitar.
    assert "verb through sushi (sushi:audio_output_4)" in [p.name for p in plan.points]
    assert any(p.name.startswith("verb via amp") for p in plan.points)


def test_separable_pairs_are_named_even_when_another_pair_shares(tmp_path):
    spec = rig(tmp_path, """\
        - name: v
          channels: 2
          inputs: [{engine_bus: 0, track_bus: 0}]
          outputs: [{engine_bus: 1, track_bus: 0}]
        - name: w
          channels: 2
          inputs: [{engine_bus: 1, track_bus: 0}]
          outputs: [{engine_bus: 1, track_bus: 0}]
        - name: x
          channels: 2
          inputs: [{engine_bus: 2, track_bus: 0}]
          outputs: [{engine_bus: 3, track_bus: 0}]
        """)
    notes = plan_probes(spec, amp=False).not_checked
    assert any(n.startswith("separation of v and w: both come out of") for n in notes)
    assert any(n.startswith("separation of v/x, w/x: outputs of their own") for n in notes)


def test_every_problem_in_a_v2_rig_is_its_own_fail_line(tmp_path, capsys):
    from sushi_rig.cli import main
    import yaml

    path = tmp_path / "bad.yaml"
    path.write_text(yaml.safe_dump({
        "kind": "rig", "schema_version": 1, "name": "bad", "master": {"output": "out"},
        "channels": [{"name": "a", "input": "a", "width": "mon"}, {"name": "b", "width": "mono"}],
    }))
    assert main(["probes", str(path)]) == 1
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 2 and all(line.startswith(f"FAIL {path}: ") for line in lines)


# --------------------------------------------------------------------------- #
# The probe track (#81): beside the instrument's, never on it
# --------------------------------------------------------------------------- #

def listing(rig_name: str, amp: bool) -> list[tuple]:
    from sushi_rig.rigfile import spec_of

    plan = plan_probes(spec_of(RIGS / f"{rig_name}.yaml"), amp=amp)
    return [(p.name, p.inject, p.links, p.capture) for p in plan.points]


GUITAR_VOCAL = [
    ("guitar through sushi (sushi:audio_output_6)", ("sushi:audio_input_2",), (), "sushi:audio_output_6"),
    ("guitar through sushi (sushi:audio_output_7)", ("sushi:audio_input_2",), (), "sushi:audio_output_7"),
    ("vocal through sushi (sushi:audio_output_6)", ("sushi:audio_input_2",), (), "sushi:audio_output_6"),
    ("vocal through sushi (sushi:audio_output_7)", ("sushi:audio_input_2",), (), "sushi:audio_output_7"),
]
ELECTRIC_BOARD = [
    ("board through sushi (sushi:audio_output_4)", ("sushi:audio_input_1",), (), "sushi:audio_output_4"),
    ("board through sushi (sushi:audio_output_5)", ("sushi:audio_input_1",), (), "sushi:audio_output_5"),
]


def amp_points(track: str, into: str, outputs: tuple[str, str]) -> list[tuple]:
    return [("amp", ("NAM:Input",), (), "NAM:Output")] + [
        (f"{track} via amp ({port})", ("NAM:Input",), (("NAM:Output", into),), port)
        for port in outputs
    ]


def test_guitar_vocal_injects_only_where_nothing_else_plays():
    """The guitar is on input 0 and the vocal on 1; the master is outputs
    0/1 and the stems 2-5. The probe goes into 2 and comes out of 6/7."""
    assert listing("guitar_vocal", amp=False) == GUITAR_VOCAL
    assert listing("guitar_vocal", amp=True) == GUITAR_VOCAL + amp_points(
        "guitar", "sushi:audio_input_2", ("sushi:audio_output_6", "sushi:audio_output_7"))


def test_electric_board_injects_only_where_nothing_else_plays():
    """One channel: input 0, the master on 0/1, the stem on 2/3."""
    assert listing("electric_board", amp=False) == ELECTRIC_BOARD
    assert listing("electric_board", amp=True) == ELECTRIC_BOARD + amp_points(
        "board", "sushi:audio_input_1", ("sushi:audio_output_4", "sushi:audio_output_5"))


def committed_rigs():
    from sushi_rig.rigfile import spec_of

    return [(path.name, RigSpec.load(path)) for path in sorted(SOURCES.glob("*.yaml"))] + [
        (f"rigs/{path.name}", spec_of(path)) for path in sorted(RIGS.glob("*.yaml"))
    ]


@pytest.mark.parametrize("name,spec", committed_rigs(), ids=lambda v: v if isinstance(v, str) else "")
def test_no_probe_shares_a_sushi_port_with_the_rig(name, spec):
    """The whole of #81's finding: a Sushi input with two links carries 64
    frames of each quantum, and whatever else feeds it is measured too. So no
    probe of any committed rig may link into an input a track reads, or
    listen on an output a track writes."""
    from sushi_rig.patchbay import track_route

    used = {label: {c for t in spec.tracks for d in track_route(t, label)[0] for c in d.channels()}
            for label in ("inputs", "outputs")}
    plan = plan_probes(spec, amp=True)
    sushi = [p for p in plan.points if p.track is not None]
    assert sushi, f"{name}: nothing is probed through Sushi"
    for point in sushi:
        sinks = [sink for _, sink in probe_links(point, "stimulus:out")]
        for port in sinks + [point.capture]:
            if port.startswith("sushi:"):
                direction = "inputs" if "_input_" in port else "outputs"
                assert int(port.rsplit("_", 1)[1]) not in used[direction], (name, point.name, port)
    # And the amp, which cannot be copied, is the one place that says it is shared.
    assert {p.name for p in plan.points if p.beside} == {
        p.name for p in plan.points if "amp" in p.name
    }


@pytest.mark.parametrize("name,spec", committed_rigs(), ids=lambda v: v if isinstance(v, str) else "")
def test_the_probe_track_is_a_track_the_rig_could_hold(name, spec):
    """Beside the rig's own tracks it must still validate: its name and its
    copies' names are new, and it routes as a track does."""
    for point in plan_probes(spec, amp=True).points:
        if point.track is not None:
            beside = RigSpec(host=spec.host, tracks=list(spec.tracks) + [point.track.to_spec()])
            # Its name is the one thing a rig may not use, which is the point.
            assert beside.validate() == [
                f"track {PROBE_TRACK!r}: a name selftest gives its probe track, which "
                "a capture leaves out; rename it"
            ], (name, point.name)


def test_the_copy_is_the_chain_without_its_send():
    """A copied send would carry the probe into the master the player hears."""
    from sushi_rig.rigfile import spec_of

    spec = spec_of(RIGS / "guitar_vocal.yaml")
    point = by_name(plan_probes(spec, amp=False))["guitar through sushi (sushi:audio_output_6)"]
    guitar = next(t for t in spec.tracks if t.name == "guitar")
    assert [c.source for c in point.track.plugins] == [
        p.name for p in guitar.plugins if p.name != "guitar_send"
    ]
    assert [c.plugin.name for c in point.track.plugins] == [
        f"{p.name}_probe" for p in guitar.plugins if p.name != "guitar_send"
    ]
    assert point.track.plugins[0].plugin.uri == guitar.plugins[0].uri
    # The mono channel's one input into both halves, kept as that shape.
    assert point.track.to_spec().inputs == [
        {"engine_channel": 2, "track_channel": 0}, {"engine_channel": 2, "track_channel": 1},
    ]
    assert point.track.to_spec().outputs == [{"engine_bus": 3, "track_bus": 0}]


def v2(tmp_path, channels: list[dict], master_chain: list | None = None):
    import yaml

    from sushi_rig.rigfile import spec_of

    path = tmp_path / "v2.yaml"
    path.write_text(yaml.safe_dump({
        "kind": "rig", "schema_version": 1, "name": "t",
        "channels": channels,
        "master": {"output": "monitor-out", "chain": master_chain or []},
    }))
    return spec_of(path)


EQ = {"name": "eq", "uri": "http://distrho.sf.net/plugins/3BandEQ"}


def test_a_master_with_a_chain_is_probed_on_a_copy_of_both(tmp_path):
    spec = v2(tmp_path, [{"name": "guitar", "input": "guitar-in", "width": "mono", "chain": []}],
              master_chain=[{"name": "limiter", "uri": "http://drobilla.net/plugins/mda/Limiter"}])
    points = by_name(plan_probes(spec, amp=True))
    through = points["guitar through the master (sushi:audio_output_4)"]
    assert through.track.copy_of == ("guitar", "master")
    # The return is left off with the send: on a copy nothing feeds it.
    assert [c.source for c in through.track.plugins] == ["limiter"]
    assert through.inject == ("sushi:audio_input_1",)
    via = points["guitar via amp and the master (sushi:audio_output_5)"]
    assert via.links == (("NAM:Output", "sushi:audio_input_1"),) and via.nonlinear


def test_a_stereo_channel_keeps_two_inputs_on_its_probe_track(tmp_path):
    spec = v2(tmp_path, [
        {"name": "keys", "input": "keys-in", "width": "stereo", "chain": [EQ]},
    ])
    (left, right) = plan_probes(spec, amp=False).points
    # Keys read 0 and 1, so the probe track reads 2 and 3, still two halves.
    assert left.inject == right.inject == ("sushi:audio_input_2", "sushi:audio_input_3")


def test_three_channels_leave_no_room_and_say_so_rather_than_probe_beside(tmp_path):
    """The master and three stems fill Sushi's eight outputs. Falling back to
    the old injection would be the 64-frame measurement, so the plan says the
    chains are not probed, and probes the amp on its own."""
    spec = v2(tmp_path, [
        {"name": n, "input": f"{n}-in", "width": "mono", "chain": [{**EQ, "name": f"{n}_eq"}]}
        for n in ("guitar", "vocal", "bass")
    ])
    plan = plan_probes(spec, amp=True)
    assert [p.name for p in plan.points] == ["amp"]
    assert sum(n.endswith("no Sushi output pair is free for selftest_probe — the rig uses 8 "
                          "of Sushi's 8 outputs, so its chain is not probed")
               for n in plan.not_checked) == 3
    assert any(n.startswith("guitar via amp: no Sushi output pair is free") for n in plan.not_checked)
    assert not any(n.startswith(RUNNING) for n in plan.not_checked)


def test_a_rig_already_using_a_probe_name_is_refused(tmp_path):
    """Renaming around it would mean a probe removing what it made could
    remove the player's processor instead."""
    spec = rig(tmp_path, """\
        - name: a
          channels: 2
          inputs: [{engine_bus: 0, track_bus: 0}]
          outputs: [{engine_bus: 0, track_bus: 0}]
          plugins:
            - {name: eq, uri: "http://distrho.sf.net/plugins/3BandEQ"}
            - {name: eq_probe, uri: "http://distrho.sf.net/plugins/3BandEQ"}
        - name: selftest_probe
          channels: 2
          inputs: [{engine_bus: 1, track_bus: 0}]
          outputs: [{engine_bus: 1, track_bus: 0}]
        """)
    with pytest.raises(ProbeError) as caught:
        plan_probes(spec, amp=False)
    assert [p.split(" is in the rig")[0] for p in caught.value.problems] == [
        "'eq_probe'", "'selftest_probe'",
    ]


# The `pw-link -l` listing live_paths.py captured on the rig machine on
# 31 Oct 2026, with Sushi up: input 0 fed by probe_A alone, input 1 by probe_A
# and probe_B — the doubly-linked input that read back at a quarter.
LIVE_LISTING = __import__("json").loads(
    (checkout_root() / "tool" / "spikes" / "live_rig" / "live-paths-1.json").read_text()
)["links"]
SPIKE = "sushi-rig-live-spike"


def test_a_second_link_on_a_probed_input_is_named_from_a_real_listing():
    crowded = ProbePoint("vocal", ("sushi:audio_input_1",), "sushi:audio_output_4")
    (problem,) = crowded_ports(crowded, LIVE_LISTING, f"{SPIKE}:probe_A")
    assert problem.startswith(
        f"vocal: sushi:audio_input_1 is fed by {SPIKE}:probe_B as well as the probe")
    assert "first 64 frames" in problem
    alone = ProbePoint("guitar", ("sushi:audio_input_0",), "sushi:audio_output_2")
    assert crowded_ports(alone, LIVE_LISTING, f"{SPIKE}:probe_A") == []


def test_the_probes_own_links_and_the_amp_input_are_not_crowding():
    """The amp's link into the probe track is the probe's; the guitar into
    NAM:Input is declared in `beside`, and is not a Sushi input."""
    point = ProbePoint(
        "g via amp", ("NAM:Input",), "sushi:audio_output_6",
        links=(("NAM:Output", "sushi:audio_input_2"),), beside="guitar-in",
    )
    listing = (
        "stim:out\n  |-> NAM:Input\n"
        "Audient iD14:capture_AUX0\n  |-> NAM:Input\n"
        "NAM:Output\n  |-> sushi:audio_input_0\n  |-> sushi:audio_input_2\n"
    )
    assert probe_links(point, "stim:out") == (
        ("stim:out", "NAM:Input"), ("NAM:Output", "sushi:audio_input_2"),
    )
    assert crowded_ports(point, listing, "stim:out") == []
    stale = listing + "Audient iD14:capture_AUX1\n  |-> sushi:audio_input_2\n"
    (problem,) = crowded_ports(point, stale, "stim:out")
    assert "fed by Audient iD14:capture_AUX1 as well as the probe" in problem


def _track_on_bus(n: int) -> ProbeTrack:
    return ProbeTrack(
        name=PROBE_TRACK, copy_of=("guitar",), channels=2,
        inputs=((("engine_channel", 2), ("track_channel", 0)),),
        outputs=((("engine_bus", n), ("track_bus", 0)),),
    )


def test_a_probe_tracks_output_linked_anywhere_else_is_named_from_a_real_listing():
    """The probe is inaudible only while nothing but the selftest listens to
    its outputs. In the spike's listing outputs 0-5 go to the spike's own
    client: the selftest's ports when the spike is the selftest, someone
    else's — a monitor, a recorder — when it is not."""
    point = ProbePoint("g (sushi:audio_output_0)", ("sushi:audio_input_2",),
                       "sushi:audio_output_0", track=_track_on_bus(0))
    assert crowded_ports(point, LIVE_LISTING, f"{SPIKE}:probe_A") == []
    problems = crowded_ports(point, LIVE_LISTING, "selftest:stimulus")
    assert [p.split(" is linked")[0] for p in problems] == [
        "g (sushi:audio_output_0): sushi:audio_output_0",
        "g (sushi:audio_output_0): sushi:audio_output_1",
    ]
    assert f"to {SPIKE}:master_l as well as the selftest" in problems[0]
    assert "the player or a recorder would hear the probe" in problems[0]


def test_both_halves_of_the_probe_track_are_checked_whichever_is_listened_to():
    point = ProbePoint("g (sushi:audio_output_6)", ("sushi:audio_input_2",),
                       "sushi:audio_output_6", track=_track_on_bus(3))
    listing = (
        "sushi:audio_output_6\n  |-> selftest:returned\n"
        "sushi:audio_output_7\n  |-> Audient iD14:playback_FR\n"
    )
    (problem,) = crowded_ports(point, listing, "selftest:stimulus")
    assert problem.startswith("g (sushi:audio_output_6): sushi:audio_output_7 is linked to "
                              "Audient iD14:playback_FR")


def test_the_amp_to_the_monitors_is_said_not_probed_when_it_would_repeat():
    """With the master's chain empty, 'via amp and the master' would be 'via
    amp' again. It is not measured twice, and the plan says so by name."""
    from sushi_rig.rigfile import spec_of

    plan = plan_probes(spec_of(RIGS / "guitar_vocal.yaml"), amp=True)
    (note,) = [n for n in plan.not_checked if n.startswith("guitar via amp and the master")]
    assert "the master's chain is empty" in note and "guitar via amp" in note
    assert not any(n.startswith("guitar via amp and the master")
                   for n in plan_probes(spec_of(RIGS / "guitar_vocal.yaml"), amp=False).not_checked)


def test_a_guitar_with_no_inputs_is_not_blamed_on_a_lack_of_room(tmp_path):
    spec = rig(tmp_path, """\
        - name: g
          channels: 2
          inputs: []
          outputs: [{engine_bus: 0, track_bus: 0}]
        """)
    spec.tracks[0].source = "guitar-in"  # what validate refuses, to reach the plan
    notes = plan_probes(spec, amp=True).not_checked
    (note,) = [n for n in notes if n.startswith("g via amp")]
    assert "g has no inputs on Sushi" in note
    assert "free" not in note


def test_a_guitar_with_no_room_says_what_is_full(tmp_path):
    spec = v2(tmp_path, [
        {"name": n, "input": "guitar-in" if n == "guitar" else f"{n}-in", "width": "mono",
         "chain": [{**EQ, "name": f"{n}_eq"}]}
        for n in ("guitar", "vocal", "bass")
    ])
    notes = plan_probes(spec, amp=True).not_checked
    (note,) = [n for n in notes if n.startswith("guitar via amp")]
    assert note.startswith("guitar via amp: no Sushi output pair is free for selftest_probe")


def test_the_probe_tracks_name_is_kept_from_every_rig(tmp_path):
    """A capture leaves out a track by this name, so a rig must never have
    one: its state would be dropped from every SAVE without a word."""
    from sushi_rig.rigfile import RigFileError

    with pytest.raises(RigFileError, match="'selftest_probe': a name selftest gives"):
        v2(tmp_path, [{"name": PROBE_TRACK, "input": "guitar-in", "width": "mono", "chain": []}])
    spec = rig(tmp_path, """\
        - name: selftest_probe
          channels: 2
          inputs: [{engine_bus: 0, track_bus: 0}]
          outputs: [{engine_bus: 0, track_bus: 0}]
        """)
    assert any("'selftest_probe': a name selftest gives" in p for p in spec.validate())
