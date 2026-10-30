"""Patchbay generation, and the check that a saved patchbay is still true.

Everything here is a decision, so nothing needs PipeWire or an audio interface
(quality principle 1). What is NOT verified by this file is the live half:
`read_pw_dump` against a real PipeWire, and qpwgraph loading a generated file.
Those are effects, checked by running them on the rig machine.

Two different fixtures, and the difference matters (#62).

`fixtures/pw-dump-rig.json` IS a capture: a real `pw-dump` from the rig machine
with the rig running, cut down to `id`, `type` and `info.props` with every prop
value left exactly as PipeWire wrote it. A fixture is a captured artefact, not a
tidied one (the lesson of #51), so the facts this check depends on are asserted
against it rather than against a fixture written to agree with them.

`LIVE` below is still constructed, and stays that way on purpose: it is how the
edge cases are reached (an empty graph, a device description containing a slash,
a node that is simply gone). It is evidence for nothing about the rig, so no test
using it asserts a fact about PipeWire.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from sushi_rig import cli, patchbay
from sushi_rig.amp import amp_patchbay
from sushi_rig.patchbay import (
    Link,
    PatchbayError,
    bus,
    channel,
    generate_patchbay,
    live_node_names,
    live_port_nodes,
    parse_patchbay,
    render_patchbay,
    resolve_nodes,
    routes_from_rig,
    check_nodes,
)
from sushi_rig.paths import checkout_root
from sushi_rig.spec import RigSpec, TrackSpec

COMMITTED = checkout_root() / "Patchbay" / "rig.qpwgraph"
# The patchbay as it was hand-wired in qpwgraph's GUI, kept now that the
# committed one is generated (#18). It is what the check was written against,
# and the stale links in it are real ones rather than invented: a dead `Pro`
# ALSA profile, and a bare `fmit` from before the tuner reached PipeWire
# through the ALSA layer.
HANDMADE = (Path(__file__).parent / "fixtures" / "patchbay-handmade.qpwgraph").read_text()
CAPTURED = (Path(__file__).parent / "fixtures" / "pw-dump-rig.json").read_text()

ROLES = {
    "guitar-in": ["Audient iD14:capture_AUX0"],
    "vocal-in": ["Audient iD14:capture_AUX1"],
    "stereo-in": ["Audient iD14:capture_AUX2", "Audient iD14:capture_AUX3"],
    "monitor-out": ["Audient iD14:playback_AUX0", "Audient iD14:playback_AUX1"],
}


# Roles that say they mean to feed both halves of a bus. Duplication is declared,
# never inferred from a role having one port.
MONO = {"guitar-in", "vocal-in"}


def _gen(roles, feeds, sinks=None, **kw):
    kw.setdefault("mono_to_stereo", MONO)
    return generate_patchbay(roles, feeds, sinks, **kw)


def _pairs(xml: str) -> list[tuple[str, str]]:
    """(source port, destination port) for each link, in file order."""
    return [(l.out_port, l.in_port) for l in parse_patchbay(xml)]


# --- mono into stereo ---------------------------------------------------------


def test_a_mono_role_feeding_a_stereo_bus_is_duplicated_across_both_halves():
    """The established pattern: capture_AUX0 feeds both audio_input_0 and
    audio_input_1, which is what lets a stereo chain follow a mono guitar."""
    xml = _gen(ROLES, {"guitar-in": [bus(0)]})
    assert _pairs(xml) == [
        ("Audient iD14:capture_AUX0", "sushi:audio_input_0"),
        ("Audient iD14:capture_AUX0", "sushi:audio_input_1"),
    ]


def test_a_second_mono_role_on_the_next_bus_uses_that_buss_channels():
    """Bus N is channels 2N and 2N+1 — the committed patchbay has capture_AUX1
    on audio_input_2 and audio_input_3."""
    xml = _gen(ROLES, {"guitar-in": [bus(0)], "vocal-in": [bus(1)]})
    assert _pairs(xml)[2:] == [
        ("Audient iD14:capture_AUX1", "sushi:audio_input_2"),
        ("Audient iD14:capture_AUX1", "sushi:audio_input_3"),
    ]


def test_a_two_port_role_goes_left_then_right_without_duplication():
    xml = _gen(ROLES, {"stereo-in": [bus(1)]})
    assert _pairs(xml) == [
        ("Audient iD14:capture_AUX2", "sushi:audio_input_2"),
        ("Audient iD14:capture_AUX3", "sushi:audio_input_3"),
    ]


def test_a_one_port_role_feeding_a_single_channel_feeds_only_that_channel():
    """Sushi routes by pair or by channel, never both in one entry."""
    xml = _gen(ROLES, {"guitar-in": [channel(3)]})
    assert _pairs(xml) == [("Audient iD14:capture_AUX0", "sushi:audio_input_3")]


def test_one_role_may_feed_several_buses():
    xml = _gen(ROLES, {"guitar-in": [bus(0), bus(1)]})
    assert [dst for _, dst in _pairs(xml)] == [f"sushi:audio_input_{i}" for i in range(4)]


# --- what cannot be resolved is an error, not a guess --------------------------


def test_three_ports_into_a_stereo_bus_is_an_error():
    roles = {"wide": ["A:1", "A:2", "A:3"]}
    with pytest.raises(PatchbayError, match="3 ports but feeds bus 0"):
        _gen(roles, {"wide": [bus(0)]})


def test_two_ports_into_one_channel_is_an_error_rather_than_a_pick():
    """Dropping one, or summing both, are both guesses about the user's intent."""
    with pytest.raises(PatchbayError, match="2 port.* routes it to 1 engine channel"):
        _gen(ROLES, {"stereo-in": [channel(0)]})


def test_an_unknown_role_names_the_role_and_what_the_machine_defines():
    with pytest.raises(PatchbayError) as caught:
        _gen(ROLES, {"guitar_in": [bus(0)]})
    message = str(caught.value)
    assert "'guitar_in'" in message
    assert "'guitar-in'" in message, "the near-miss spelling must be offered"


def test_two_roles_on_one_bus_are_refused_rather_than_summed():
    """The near-miss `amp_patchbay` records: redirecting everything that feeds
    Sushi summed two physical inputs into one mono amp. With a microphone on the
    second input that stops being hypothetical."""
    with pytest.raises(PatchbayError) as caught:
        _gen(ROLES, {"guitar-in": [bus(0)], "vocal-in": [bus(0)]})
    assert "sushi:audio_input_0 would be fed by more than one port" in str(caught.value)


def test_a_bus_and_a_channel_overlapping_are_caught_too():
    with pytest.raises(PatchbayError, match="audio_input_1"):
        _gen(ROLES, {"guitar-in": [bus(0)], "vocal-in": [channel(1)]})


def test_two_roles_sharing_one_physical_port_is_fine():
    """The tuner and Sushi both listening to the guitar is the committed setup."""
    roles = {"guitar-in": ["A:x"], "tuner-in": ["A:x"]}
    xml = _gen(roles, {"guitar-in": [channel(0)], "tuner-in": [channel(0)]})
    assert _pairs(xml) == [("A:x", "sushi:audio_input_0")]


def test_every_problem_is_reported_together():
    with pytest.raises(PatchbayError) as caught:
        _gen(ROLES, {"nope": [bus(0)], "stereo-in": [channel(0)]})
    assert len(caught.value.problems) == 2


# --- outputs ------------------------------------------------------------------


def test_a_two_port_sink_takes_a_bus_left_then_right():
    xml = _gen(ROLES, {}, {"monitor-out": [bus(0)]})
    assert _pairs(xml) == [
        ("sushi:audio_output_0", "Audient iD14:playback_AUX0"),
        ("sushi:audio_output_1", "Audient iD14:playback_AUX1"),
    ]


def test_a_one_port_sink_for_a_stereo_bus_is_an_error_not_a_mixdown():
    roles = {"mono-out": ["A:p"]}
    with pytest.raises(PatchbayError, match="needs 2"):
        _gen(roles, {}, {"mono-out": [bus(0)]})


def test_a_sink_for_a_channel_needs_exactly_one_port():
    with pytest.raises(PatchbayError, match="needs 1"):
        _gen(ROLES, {}, {"monitor-out": [channel(0)]})


def test_one_output_may_go_to_two_places():
    """Monitors and headphones at once is ordinary; only *inputs* must not sum."""
    roles = {"mon": ["A:l", "A:r"], "phones": ["B:l", "B:r"]}
    xml = _gen(roles, {}, {"mon": [bus(0)], "phones": [bus(0)]})
    assert len(parse_patchbay(xml)) == 4


def test_two_outputs_into_one_playback_port_are_refused():
    roles = {"mon": ["A:l", "A:r"], "other": ["A:r", "A:x"]}
    with pytest.raises(PatchbayError, match="playback port 'A:r'"):
        _gen(roles, {}, {"mon": [bus(0)], "other": [bus(1)]})


# --- node names ---------------------------------------------------------------


def test_the_node_falls_back_to_the_ports_client_and_sushi_is_always_sushi():
    link = parse_patchbay(_gen(ROLES, {"guitar-in": [bus(0)]}))[0]
    assert (link.out_node, link.in_node) == ("Audient iD14", "sushi")


def test_a_supplied_node_replaces_the_fallback():
    """The port alias is the same under every ALSA profile; the node is what
    differs, so it has to come from somewhere other than the machine file."""
    nodes = {"Audient iD14:capture_AUX0": "Audient iD14 Multichannel"}
    link = parse_patchbay(_gen(ROLES, {"guitar-in": [bus(0)]}, nodes=nodes))[0]
    assert link.out_node == "Audient iD14 Multichannel"
    assert link.out_port == "Audient iD14:capture_AUX0"


# --- the file shape -----------------------------------------------------------


def test_rendering_the_committed_patchbay_reproduces_it_byte_for_byte():
    """qpwgraph reads this file, so the shape is matched exactly — DOCTYPE,
    indentation, attribute order, and the blank line after the closing tag."""
    text = COMMITTED.read_text()
    assert render_patchbay(parse_patchbay(text)) == text


def test_a_name_with_xml_metacharacters_survives():
    """A device description may contain an ampersand or a quote."""
    links = [Link('Mic & "Pre"', "Mic:out", "sushi", "sushi:audio_input_0")]
    assert parse_patchbay(render_patchbay(links)) == links


def test_generation_is_deterministic_whatever_order_the_roles_arrive_in():
    a = _gen(ROLES, {"guitar-in": [bus(0)], "vocal-in": [bus(1)]})
    b = _gen(dict(reversed(ROLES.items())), {"vocal-in": [bus(1)], "guitar-in": [bus(0)]})
    assert a == b


def test_the_amp_rewrite_understands_a_generated_patchbay():
    """The two halves have to fit: if generation produced something
    `amp_patchbay` could not read, switching to generated patchbays would
    silently drop the amp. Both key off the same role now, so this reads the
    port out of the machine file's roles rather than off an input index."""
    xml = _gen(
        ROLES, {"guitar-in": [bus(0)]}, {"monitor-out": [bus(0)]},
        tuner_role="guitar-in",
    )
    (guitar,) = ROLES["guitar-in"]
    pairs = _pairs(amp_patchbay(xml, guitar))
    assert (guitar, "NAM:Input") in pairs
    assert ("NAM:Output", "sushi:audio_input_0") in pairs
    assert ("NAM:Output", "sushi:audio_input_1") in pairs
    assert not any(src == guitar and dst.startswith("sushi:") for src, dst in pairs)
    assert (guitar, patchbay.TUNER_PORT) in pairs, "the tuner keeps the dry string"


# --- reading routes off a rig -------------------------------------------------


def test_routes_come_from_each_tracks_inputs_and_outputs():
    rig = RigSpec(tracks=[
        TrackSpec(
            name="board", source="guitar-in", sink="monitor-out",
            inputs=[{"engine_bus": 0, "track_bus": 0}],
            outputs=[{"engine_bus": 0, "track_bus": 0}],
        ),
        TrackSpec(name="mic", source="vocal-in", inputs=[{"engine_channel": 3, "track_channel": 0}]),
    ])
    feeds, sinks = routes_from_rig(rig)
    assert feeds == {"guitar-in": [(bus(0),)], "vocal-in": [(channel(3),)]}
    assert sinks == {"monitor-out": [(bus(0),)]}


def test_a_track_without_a_source_contributes_nothing():
    """Every existing config is like this, and must generate nothing rather than
    something."""
    rig = RigSpec(tracks=[TrackSpec(name="t", inputs=[{"engine_bus": 0, "track_bus": 0}])])
    assert routes_from_rig(rig) == ({}, {})


def test_an_entry_mixing_bus_and_channel_forms_is_refused_not_skipped():
    rig = RigSpec(tracks=[TrackSpec(
        name="t", source="r", inputs=[{"engine_bus": 0, "engine_channel": 1}],
    )])
    with pytest.raises(PatchbayError, match="mixes bus and channel forms"):
        routes_from_rig(rig)


def test_an_entry_with_only_track_side_routing_is_refused_not_dropped():
    """`RigSpec.validate()` accepts `{track_bus: 0}`, so it reaches here. Dropping
    it left the track in `source:` unwired while generation went ahead for every
    other track."""
    rig = RigSpec(tracks=[
        TrackSpec(name="a", source="guitar-in", inputs=[{"track_bus": 0}]),
        TrackSpec(name="b", source="vocal-in", inputs=[{"engine_bus": 1, "track_bus": 0}]),
    ])
    assert rig.validate() == []
    with pytest.raises(PatchbayError) as caught:
        routes_from_rig(rig)
    assert "track 'a'" in str(caught.value) and "names no engine_bus" in str(caught.value)


@pytest.mark.parametrize("bad", ['"0"', -1, 1.0, True, None])
def test_an_engine_index_that_is_not_a_non_negative_integer_is_refused(bad):
    """A quoted "0" used to die in arithmetic with a TypeError, and -1 generated
    `sushi:audio_input_-2` without a word."""
    rig = RigSpec(tracks=[TrackSpec(name="t", source="r", inputs=[{"engine_bus": bad}])])
    with pytest.raises(PatchbayError, match="engine_bus must be a non-negative integer"):
        routes_from_rig(rig)


def test_a_bad_engine_channel_is_refused_on_outputs_too():
    rig = RigSpec(tracks=[TrackSpec(name="t", sink="r", outputs=[{"engine_channel": -2}])])
    with pytest.raises(PatchbayError, match="engine_channel must be a non-negative integer"):
        routes_from_rig(rig)


def test_every_unplaceable_entry_is_reported_together():
    rig = RigSpec(tracks=[
        TrackSpec(name="a", source="r", inputs=[{"track_bus": 0}]),
        TrackSpec(name="b", source="r", inputs=[{"engine_bus": -1}]),
    ])
    with pytest.raises(PatchbayError) as caught:
        routes_from_rig(rig)
    assert len(caught.value.problems) == 2


def test_two_tracks_on_one_role_and_bus_do_not_duplicate_the_route():
    rig = RigSpec(tracks=[
        TrackSpec(name=n, source="guitar-in", inputs=[{"engine_bus": 0, "track_bus": 0}])
        for n in ("a", "b")
    ])
    assert routes_from_rig(rig)[0] == {"guitar-in": [(bus(0),)]}


def test_a_tracks_channel_entries_stay_together_as_one_route():
    rig = RigSpec(tracks=[TrackSpec(
        name="t", source="stereo-in",
        inputs=[{"engine_channel": 2, "track_channel": 0}, {"engine_channel": 3, "track_channel": 1}],
    )])
    assert routes_from_rig(rig)[0] == {"stereo-in": [(channel(2), channel(3))]}


# --- a stereo source on channel-form routing -----------------------------------


def test_a_two_port_role_pairs_across_a_tracks_two_channel_entries():
    """Channel form is the spelling `examples/rig.example.yaml` documents, and
    judging each entry alone refused a stereo source against every one."""
    rig = RigSpec(tracks=[TrackSpec(
        name="t", source="stereo-in",
        inputs=[{"engine_channel": 2, "track_channel": 0}, {"engine_channel": 3, "track_channel": 1}],
    )])
    feeds, _ = routes_from_rig(rig)
    assert _pairs(_gen(ROLES, feeds)) == [
        ("Audient iD14:capture_AUX2", "sushi:audio_input_2"),
        ("Audient iD14:capture_AUX3", "sushi:audio_input_3"),
    ]


def test_a_one_port_role_on_two_channel_entries_needs_the_declaration():
    feeds = {"guitar-in": [(channel(0), channel(1))]}
    assert len(_pairs(_gen(ROLES, feeds))) == 2
    with pytest.raises(PatchbayError, match="counts must match"):
        _gen(ROLES, feeds, mono_to_stereo=())


def test_the_same_role_on_two_tracks_is_fan_out_not_a_stereo_pair():
    """Pairing is per track. Two tracks each taking one channel from a one-port
    role is one port going to two places, not a pair to be split."""
    feeds = {"guitar-in": [(channel(0),), (channel(4),)]}
    assert [d for _, d in _pairs(_gen(ROLES, feeds, mono_to_stereo=()))] == [
        "sushi:audio_input_0", "sushi:audio_input_4",
    ]


def test_a_sink_pairs_across_a_tracks_channel_entries_too():
    xml = _gen(ROLES, {}, {"monitor-out": [(channel(0), channel(1))]})
    assert _pairs(xml) == [
        ("sushi:audio_output_0", "Audient iD14:playback_AUX0"),
        ("sushi:audio_output_1", "Audient iD14:playback_AUX1"),
    ]


# --- mono_to_stereo is declared, never inferred ----------------------------------


def test_a_one_port_role_into_a_bus_is_an_error_unless_declared():
    """The design assumed duplication from the port count. The live profile
    wires one input per channel, so a silent duplication was wrong for it."""
    with pytest.raises(PatchbayError) as caught:
        _gen(ROLES, {"guitar-in": [bus(0)]}, mono_to_stereo=())
    message = str(caught.value)
    assert "'guitar-in'" in message and "mono_to_stereo: true" in message


def test_declaring_mono_to_stereo_duplicates_across_both_halves():
    xml = _gen(ROLES, {"guitar-in": [bus(0)]}, mono_to_stereo={"guitar-in"})
    assert [d for _, d in _pairs(xml)] == ["sushi:audio_input_0", "sushi:audio_input_1"]


def test_declaring_mono_to_stereo_on_a_two_port_role_is_an_error():
    with pytest.raises(PatchbayError, match="declares mono_to_stereo but has 2 ports"):
        _gen(ROLES, {"stereo-in": [bus(1)]}, mono_to_stereo={"stereo-in"})


def test_the_declaration_only_applies_to_the_role_that_made_it():
    with pytest.raises(PatchbayError, match="'vocal-in'"):
        _gen(ROLES, {"guitar-in": [bus(0)], "vocal-in": [bus(1)]}, mono_to_stereo={"guitar-in"})


def test_fan_out_outputs_are_sorted_so_track_order_cannot_change_the_bytes():
    roles = {"mon": ["A:l", "A:r"], "phones": ["B:l", "B:r"]}
    one = _gen(roles, {}, {"mon": [bus(0)], "phones": [bus(0)]})
    two = _gen(roles, {}, {"phones": [bus(0)], "mon": [bus(0)]})
    assert one == two


# --- the live graph -------------------------------------------------------------


def _node(id_, **props):
    return {"id": id_, "type": "PipeWire:Interface:Node", "info": {"props": props}}


def _port(id_, node_id, alias, direction=None):
    # `direction` was added when generation began checking it. As with the rest
    # of this fixture, "in"/"out" is pw-dump's documented `port.direction`, not
    # something captured here.
    props = {"node.id": node_id, "port.alias": alias}
    if direction:
        props["port.direction"] = direction
    return {"id": id_, "type": "PipeWire:Interface:Port", "info": {"props": props}}


# A constructed graph, for the edge cases the capture does not contain. The
# shapes are pw-dump's documented ones; nothing here is evidence about the rig,
# which is what `CAPTURED` is for.
LIVE = json.dumps([
    {"id": 0, "type": "PipeWire:Interface:Core", "info": {"props": {}}},
    _node(40, **{"node.name": "alsa_input.usb-Audient_Audient_iD14-00.multichannel-input",
                 "node.description": "Audient iD14 Multichannel", "node.nick": "Audient iD14"}),
    _node(41, **{"node.name": "alsa_output.usb-Audient_Audient_iD14-00.analog-surround-40",
                 "node.description": "Audient iD14 Analog Surround 4.0"}),
    _node(42, **{"node.name": "sushi"}),
    _node(43, **{"node.name": "alsa_capture.fmit"}),
    _port(50, 40, "Audient iD14:capture_AUX0", "out"),
    _port(51, 41, "Audient iD14:playback_FL", "in"),
    _port(53, 41, "Audient iD14:playback_FR", "in"),
    _port(52, 42, "sushi:audio_input_0", "in"),
    {"id": 60, "type": "PipeWire:Interface:Link", "info": {"props": {}}},
])


def test_live_node_names_include_description_nick_and_name():
    names = live_node_names(LIVE)
    assert {"Audient iD14 Multichannel", "Audient iD14", "sushi", "alsa_capture.fmit"} <= names


def test_non_node_objects_are_not_nodes():
    """A port alias or a link is not a node, so a saved link naming one by
    mistake must still be reported."""
    assert "Audient iD14:capture_AUX0" not in live_node_names(LIVE)


def test_an_empty_graph_cannot_be_checked_rather_than_everything_being_stale():
    with pytest.raises(PatchbayError, match="no nodes"):
        live_node_names("[]")


def test_pw_dump_that_is_not_json_is_an_error():
    with pytest.raises(PatchbayError, match="did not produce JSON"):
        live_node_names("not json")


def test_pw_dump_that_is_not_a_list_is_an_error():
    with pytest.raises(PatchbayError, match="not a list"):
        live_node_names("{}")


def _with_the_rig_stopped(dump: str) -> str:
    """The capture minus the nodes the rig starts, which is what `patchbay check`
    sees when it is run before `sushi-rig up` — the mistake that exposed the bug
    these three buckets exist for."""
    objects = json.loads(dump)
    rig = ("sushi", "fmit", "Carla", "NAM")
    return json.dumps([
        obj for obj in objects
        if not (
            obj.get("type") == "PipeWire:Interface:Node"
            and any(t in json.dumps((obj.get("info") or {}).get("props") or {}) for t in rig)
        )
    ])


def test_the_name_the_patchbay_saves_is_node_description():
    """Established by the capture, not inferred. The committed patchbay says
    `node="Audient iD14 Multichannel"`, and that string is the interface's
    `node.description` — which is why `_display_name` prefers it."""
    assert {"Audient iD14 Multichannel", "Audient iD14 Analog Surround 4.0"} <= live_node_names(CAPTURED)


def test_the_port_alias_prefix_is_shared_across_profiles_and_so_cannot_name_a_node():
    """`node.nick` is `Audient iD14` for both the input and the output profile.
    A check that matched on it would call the dead `Pro` profile live."""
    nicks = {
        (obj["info"]["props"].get("node.nick"))
        for obj in json.loads(CAPTURED)
        if obj.get("type") == "PipeWire:Interface:Node"
        and "Audient" in str(obj["info"]["props"].get("node.description"))
    }
    assert nicks == {"Audient iD14"}


def test_the_slashed_form_is_application_name_joined_to_node_description():
    """The form that used to be unmatchable. The capture shows the tuner carries
    `application.name = 'PipeWire ALSA [fmit]'` and
    `node.description = 'ALSA Capture [fmit]'`, which join to exactly the name
    the committed patchbay saved — so it is composed, not guessed at."""
    assert "PipeWire ALSA [fmit]/ALSA Capture [fmit]" in live_node_names(CAPTURED)


def test_checking_the_handmade_patchbay_against_the_running_rig():
    """With the rig up, two names in the hand-wired patchbay are genuinely dead:
    the `Pro` ALSA profile, and the bare `fmit` — because under PipeWire's ALSA
    layer the tuner is `ALSA Capture [fmit]` and never `fmit`. Both were real,
    and generating the file is what fixed them."""
    result = check_nodes(parse_patchbay(HANDMADE), live_node_names(CAPTURED))
    assert set(result.missing) == {"Audient iD14 Pro", "fmit"}
    assert result.not_running == {}
    # Five links leave the dead Pro profile and four enter it.
    assert len(result.missing["Audient iD14 Pro"]) == 9


def test_the_committed_patchbay_is_clean_against_the_capture():
    """The point of generating it. Every node it names was in the graph on the
    machine the capture came from, so nothing in it is a dead link."""
    result = check_nodes(parse_patchbay(COMMITTED.read_text()), live_node_names(CAPTURED))
    assert result.missing == {} and result.not_running == {}


def test_the_same_check_with_the_rig_down_does_not_report_the_rigs_own_nodes_as_gone():
    """The bug. Run before `sushi-rig up`, the check said three nodes were
    "definitely not in the graph" when one was, which buries the only finding
    that mattered."""
    result = check_nodes(
        parse_patchbay(HANDMADE),
        live_node_names(_with_the_rig_stopped(CAPTURED)),
    )
    assert set(result.missing) == {"Audient iD14 Pro"}, "the finding is still the finding"
    assert set(result.not_running) == {
        "sushi", "fmit", "PipeWire ALSA [fmit]/ALSA Capture [fmit]"
    }


def test_a_rig_name_is_only_excused_while_that_process_has_no_live_node():
    """Otherwise every stale name for a rig process would be excused for ever,
    and `fmit` above would never be reported. The tuner running under a name the
    patchbay does not know is a dead link, not a stopped process."""
    link = Link("fmit", "fmit:in", "sushi", "sushi:audio_output_0")
    running = check_nodes([link], {"sushi", "ALSA Capture [fmit]"})
    assert set(running.missing) == {"fmit"} and running.not_running == {}

    stopped = check_nodes([link], {"Audient iD14 Multichannel"})
    assert set(stopped.not_running) == {"fmit", "sushi"} and stopped.missing == {}


@pytest.mark.parametrize("name", ["NAMM Keys", "NAM Keys", "fmit-old", "my sushi"])
def test_a_rig_node_name_does_not_claim_a_device_whose_name_merely_contains_it(name):
    """`NAM`, `fmit` and `sushi` are rig nodes. An earlier version matched on any
    non-alphanumeric boundary, which excused every one of these dead names as
    "the rig is not running"."""
    link = Link(name, "k:o", "sushi", "sushi:audio_input_0")
    assert set(check_nodes([link], {"sushi"}).missing) == {name}


@pytest.mark.parametrize("live_name", ["sushi monitor", "sushi-old", "notsushi"])
def test_an_incidental_mention_in_a_live_name_does_not_declare_a_child_running(live_name):
    """The other half of the same mistake, and worse: a loopback called
    `sushi monitor` in the graph marked Sushi up, so the saved `sushi` links
    were reported MISSING and the check exited 1 on a rig that was merely down."""
    link = Link("sushi", "sushi:audio_output_0", "X", "x:i")
    result = check_nodes([link], {live_name, "X"})
    assert set(result.not_running) == {"sushi"}, "the rig is down, whatever that node is"
    assert result.missing == {}


@pytest.mark.parametrize(
    "live_name", ["fmit", "ALSA Capture [fmit]", "alsa_capture.fmit"]
)
def test_the_three_forms_a_rig_child_really_answers_to(live_name):
    """Every one read off the committed patchbay or the capture: the name, the
    name bracketed by PipeWire's ALSA layer, and the name as a dotted suffix.
    Any of them in the graph means the tuner is up, which makes the *other*
    saved name for it stale rather than excused."""
    link = Link("PipeWire ALSA [fmit]/ALSA Capture [fmit]", "f:in", "X", "x:i")
    result = check_nodes([link], {live_name, "X"})
    assert set(result.missing) == {"PipeWire ALSA [fmit]/ALSA Capture [fmit]"}
    assert result.not_running == {}


def test_a_slashed_name_whose_client_has_gone_is_a_finding():
    """There used to be a third bucket for the `<client>/<node>` form, holding
    names this could not match. It excused exactly this link -- a client that is
    genuinely gone -- reporting "cannot tell" and exiting 0, which is the failure
    the check exists to catch. `live_node_names` composes the form now, so a
    slashed name is judged like any other."""
    link = Link("sushi", "sushi:audio_output_0", "Chromium/Playback", "x:y")
    assert set(check_nodes([link], {"sushi"}).missing) == {"Chromium/Playback"}


def test_a_slashed_name_that_is_live_is_simply_present():
    """A device description may contain a slash. If it matches, it is just fine."""
    dump = json.dumps([_node(1, **{"node.description": "Mic/Line In"})])
    link = Link("Mic/Line In", "Mic:out", "Mic/Line In", "Mic:in")
    result = check_nodes([link], live_node_names(dump))
    assert result.missing == {} and result.not_running == {}


def test_a_client_with_no_description_still_gets_its_slashed_name_composed():
    """The capture's own `speech-dispatcher-dummy` carries an `application.name`
    and no `node.description`. Composing the slashed form from the description
    alone left that node's saved name unmatchable for ever -- which, with the
    bucket for unmatchable names gone, now means permanently reported missing."""
    dump = json.dumps([_node(1, **{
        "node.name": "speech-dispatcher-dummy",
        "application.name": "speech-dispatcher-dummy",
    })])
    names = live_node_names(dump)
    assert "speech-dispatcher-dummy/speech-dispatcher-dummy" in names


def test_a_fully_live_patchbay_has_nothing_in_either_bucket():
    links = [Link("Audient iD14 Multichannel", "Audient iD14:capture_AUX0", "sushi", "sushi:audio_input_0")]
    result = check_nodes(links, live_node_names(LIVE))
    assert result.missing == {} and result.not_running == {}


def test_a_link_with_both_ends_missing_is_listed_under_each():
    link = Link("gone-a", "a:o", "gone-b", "b:i")
    result = check_nodes([link], live_node_names(LIVE))
    assert result.missing == {"gone-a": [link], "gone-b": [link]}


def test_a_link_is_not_listed_twice_under_one_node():
    link = Link("gone", "g:o", "gone", "g:i")
    assert check_nodes([link], set()).missing == {"gone": [link]}


def test_a_patchbay_that_is_not_xml_is_an_error():
    with pytest.raises(PatchbayError, match="not a readable patchbay"):
        parse_patchbay("<patchbay><items>")


# --- role -> live port ----------------------------------------------------------


def test_a_role_resolves_to_the_node_that_owns_its_port():
    nodes, problems = resolve_nodes(ROLES, {"guitar-in"}, set(), live_port_nodes(LIVE))
    assert problems == []
    assert nodes == {"Audient iD14:capture_AUX0": "Audient iD14 Multichannel"}


def test_a_role_with_no_live_port_names_the_role_and_what_it_looked_for():
    """The failure this issue exists for: a renamed interface used to give
    silence and dead entries, and now gives this."""
    _, problems = resolve_nodes(ROLES, {"vocal-in"}, set(), live_port_nodes(LIVE))
    assert len(problems) == 1
    assert "'vocal-in'" in problems[0] and "Audient iD14:capture_AUX1" in problems[0]


def test_roles_no_track_uses_are_not_looked_up():
    """Unplugged hardware is a problem only if something needs it."""
    _, problems = resolve_nodes(ROLES, {"guitar-in"}, set(), live_port_nodes(LIVE))
    assert problems == []


def test_a_port_alias_on_two_nodes_is_ambiguous_not_picked():
    """`Audient iD14:capture_AUX0` is the same under the Pro and Multichannel
    profiles, so both being live would make the node a coin toss."""
    dump = json.dumps([
        _node(1, **{"node.description": "Audient iD14 Pro"}),
        _node(2, **{"node.description": "Audient iD14 Multichannel"}),
        _port(10, 1, "Audient iD14:capture_AUX0", "out"),
        _port(11, 2, "Audient iD14:capture_AUX0", "out"),
    ])
    _, problems = resolve_nodes(ROLES, {"guitar-in"}, set(), live_port_nodes(dump))
    assert len(problems) == 1 and "more than one node" in problems[0]


def test_a_playback_port_in_a_source_role_is_refused_naming_the_direction():
    """Resolved cleanly before, and generation then asked for a link PipeWire can
    never make: `sushi:audio_output_N -> ...` into a capture port, or the
    reverse."""
    roles = {"guitar-in": ["Audient iD14:playback_FL"]}
    _, problems = resolve_nodes(roles, {"guitar-in"}, set(), live_port_nodes(LIVE))
    assert len(problems) == 1
    assert "'guitar-in'" in problems[0] and "playback_FL" in problems[0]
    assert "direction 'out'" in problems[0] and "'in'" in problems[0]


def test_a_capture_port_in_a_sink_role_is_refused_naming_the_direction():
    roles = {"monitor-out": ["Audient iD14:capture_AUX0"]}
    _, problems = resolve_nodes(roles, set(), {"monitor-out"}, live_port_nodes(LIVE))
    assert len(problems) == 1
    assert "'monitor-out'" in problems[0] and "capture_AUX0" in problems[0]
    assert "direction 'in'" in problems[0]


def test_a_sink_role_with_playback_ports_resolves():
    roles = {"monitor-out": ["Audient iD14:playback_FL", "Audient iD14:playback_FR"]}
    nodes, problems = resolve_nodes(roles, set(), {"monitor-out"}, live_port_nodes(LIVE))
    assert problems == []
    assert set(nodes.values()) == {"Audient iD14 Analog Surround 4.0"}


def test_a_port_whose_direction_pw_dump_does_not_state_is_refused():
    """Not assumed to be right: a check that cannot tell must say so."""
    dump = json.dumps([_node(1, **{"node.description": "X"}), _port(2, 1, "X:in")])
    _, problems = resolve_nodes({"r": ["X:in"]}, {"r"}, set(), live_port_nodes(dump))
    assert len(problems) == 1 and "None" in problems[0]


def test_a_role_used_as_both_source_and_sink_is_a_problem():
    _, problems = resolve_nodes(ROLES, {"guitar-in"}, {"guitar-in"}, live_port_nodes(LIVE))
    assert any("both a source and a sink" in p for p in problems)


# --- effects, with the process faked --------------------------------------------


def test_a_missing_pw_dump_is_an_error_not_an_empty_graph(monkeypatch):
    def boom(*a, **k):
        raise FileNotFoundError("pw-dump")

    monkeypatch.setattr(patchbay.subprocess, "run", boom)
    with pytest.raises(PatchbayError, match="not installed"):
        patchbay.read_pw_dump()


def test_a_hung_pw_dump_is_an_error(monkeypatch):
    def hang(*a, **k):
        raise subprocess.TimeoutExpired("pw-dump", 1)

    monkeypatch.setattr(patchbay.subprocess, "run", hang)
    with pytest.raises(PatchbayError, match="did not answer"):
        patchbay.read_pw_dump()


def test_a_failing_pw_dump_reports_its_message(monkeypatch):
    monkeypatch.setattr(
        patchbay.subprocess, "run",
        lambda *a, **k: SimpleNamespace(returncode=1, stdout="", stderr="connection refused"),
    )
    with pytest.raises(PatchbayError, match="connection refused"):
        patchbay.read_pw_dump()


# --- the commands -----------------------------------------------------------------

RIG = """
tracks:
  - name: board
    source: guitar-in
    sink: monitor-out
    channels: 2
    inputs: [{engine_bus: 0, track_bus: 0}]
    outputs: [{engine_bus: 0, track_bus: 0}]
"""
MACHINE = """
schema_version: 1
roles:
  guitar-in:
    ports: ["Audient iD14:capture_AUX0"]
    mono_to_stereo: true
  monitor-out: ["Audient iD14:playback_AUX0", "Audient iD14:playback_AUX1"]
"""


@pytest.fixture
def files(tmp_path):
    (tmp_path / "rig.yaml").write_text(RIG)
    (tmp_path / "m.yaml").write_text(MACHINE)
    return tmp_path


def test_generate_prints_to_stdout_by_default(files, capsys):
    code = cli.main(["patchbay", "generate", str(files / "rig.yaml"),
                     "--machine", str(files / "m.yaml"), "--offline"])
    assert code == 0
    out = capsys.readouterr().out
    assert out.startswith("<!DOCTYPE patchbay>\n")
    assert len(parse_patchbay(out)) == 5, "two in, two out, and the tuner tap"


def test_generate_writes_only_where_told(files, capsys):
    target = files / "out.qpwgraph"
    cli.main(["patchbay", "generate", str(files / "rig.yaml"),
              "--machine", str(files / "m.yaml"), "--offline", "-o", str(target)])
    assert len(parse_patchbay(target.read_text())) == 5
    assert "<" not in capsys.readouterr().out


def test_generate_names_device_nodes_from_the_live_graph(files, monkeypatch, capsys):
    """Offline, the device node would be "Audient iD14" and qpwgraph would never
    match it. Live, it is the profile that is actually running."""
    (files / "m.yaml").write_text(
        "schema_version: 1\nroles:\n  guitar-in:\n    ports: ['Audient iD14:capture_AUX0']\n"
        "    mono_to_stereo: true\n"
        "  monitor-out: ['Audient iD14:playback_FL', 'Audient iD14:playback_FR']\n"
    )
    monkeypatch.setattr(patchbay, "read_pw_dump", lambda *a, **k: LIVE)
    assert cli.main(["patchbay", "generate", str(files / "rig.yaml"),
                     "--machine", str(files / "m.yaml")]) == 0
    links = parse_patchbay(capsys.readouterr().out)
    assert {l.out_node for l in links if l.in_node == "sushi"} == {"Audient iD14 Multichannel"}
    assert {l.in_node for l in links if l.out_node == "sushi"} == {"Audient iD14 Analog Surround 4.0"}


def test_generate_fails_loudly_when_a_role_has_no_live_port(files, monkeypatch):
    """This machine's playback_AUX0/1 are not in the live graph, which must be an
    error naming them, not a patchbay that quietly points at nothing."""
    monkeypatch.setattr(patchbay, "read_pw_dump", lambda *a, **k: LIVE)
    with pytest.raises(SystemExit) as caught:
        cli.main(["patchbay", "generate", str(files / "rig.yaml"),
                  "--machine", str(files / "m.yaml")])
    assert "'monitor-out'" in str(caught.value) and "playback_AUX0" in str(caught.value)


def test_generate_refuses_a_rig_where_no_track_names_a_role(files):
    (files / "rig.yaml").write_text(RIG.replace("source: guitar-in", "").replace("sink: monitor-out", ""))
    with pytest.raises(SystemExit, match="nothing to generate"):
        cli.main(["patchbay", "generate", str(files / "rig.yaml"),
                  "--machine", str(files / "m.yaml"), "--offline"])


def test_generate_reports_machine_and_rig_problems_together(files):
    (files / "m.yaml").write_text("schema_version: 1\nroles:\n  guitar-in: []\n")
    (files / "rig.yaml").write_text(RIG.replace("inputs: [{engine_bus: 0, track_bus: 0}]", "inputs: []"))
    with pytest.raises(SystemExit) as caught:
        cli.main(["patchbay", "generate", str(files / "rig.yaml"),
                  "--machine", str(files / "m.yaml"), "--offline"])
    message = str(caught.value)
    assert "has no ports" in message and "has no inputs" in message


def test_generate_refuses_a_one_port_role_on_a_bus_without_the_declaration(files, capsys):
    (files / "m.yaml").write_text(MACHINE.replace("    mono_to_stereo: true\n", ""))
    with pytest.raises(SystemExit) as caught:
        cli.main(["patchbay", "generate", str(files / "rig.yaml"),
                  "--machine", str(files / "m.yaml"), "--offline"])
    assert "mono_to_stereo: true" in str(caught.value)


def test_generate_refuses_a_playback_port_used_as_a_source(files, monkeypatch):
    (files / "m.yaml").write_text(
        MACHINE.replace("capture_AUX0", "playback_FL")
        .replace("playback_AUX0", "playback_FL").replace("playback_AUX1", "playback_FR")
    )
    monkeypatch.setattr(patchbay, "read_pw_dump", lambda *a, **k: LIVE)
    with pytest.raises(SystemExit) as caught:
        cli.main(["patchbay", "generate", str(files / "rig.yaml"),
                  "--machine", str(files / "m.yaml")])
    assert "'guitar-in'" in str(caught.value) and "direction 'out'" in str(caught.value)


def test_generate_refuses_a_track_side_only_entry_rather_than_dropping_it(files):
    (files / "rig.yaml").write_text(RIG.replace(
        "inputs: [{engine_bus: 0, track_bus: 0}]", "inputs: [{track_bus: 0}]"))
    with pytest.raises(SystemExit, match="names no engine_bus"):
        cli.main(["patchbay", "generate", str(files / "rig.yaml"),
                  "--machine", str(files / "m.yaml"), "--offline"])


def test_machine_check_validates_without_a_rig(tmp_path, capsys):
    """None of the committed rigs names a role, so `generate` has nothing to work
    on; a machine file must still be checkable."""
    path = tmp_path / "m.yaml"
    path.write_text(MACHINE)
    assert cli.main(["machine", "check", str(path)]) == 0
    assert "ok, 2 role(s)" in capsys.readouterr().out


def test_machine_check_reports_every_problem_and_exits_nonzero(tmp_path, capsys):
    path = tmp_path / "m.yaml"
    path.write_text("schema_version: 1\nroles:\n  a: []\n  b: 'x'\n")
    assert cli.main(["machine", "check", str(path)]) == 1
    out = capsys.readouterr().out
    assert "role 'a': has no ports" in out and "role 'b'" in out


def test_the_committed_example_passes_machine_check(capsys):
    assert cli.main(["machine", "check", str(checkout_root() / "machines" / "example.yaml")]) == 0


def test_check_lists_missing_nodes_and_exits_nonzero(tmp_path, monkeypatch, capsys):
    stale = tmp_path / "handmade.qpwgraph"
    stale.write_text(HANDMADE)
    monkeypatch.setattr(patchbay, "read_pw_dump", lambda *a, **k: LIVE)
    assert cli.main(["patchbay", "check", str(stale)]) == 1
    out = capsys.readouterr().out
    assert "no live node 'Audient iD14 Pro'" in out
    assert "Audient iD14:capture_AUX0 -> sushi:audio_input_0" in out


def test_check_fails_on_a_link_to_a_client_that_has_gone(tmp_path, monkeypatch, capsys):
    """This used to exit 0 saying "not evaluated", because the `<client>/<node>`
    form had a bucket of its own that neither passed nor failed. The form is
    matched now, so a dead client is a finding like any other."""
    path = tmp_path / "p.qpwgraph"
    path.write_text(render_patchbay([
        Link("sushi", "sushi:audio_output_0", "Chromium/Playback", "x:y"),
    ]))
    monkeypatch.setattr(patchbay, "read_pw_dump", lambda *a, **k: LIVE)
    assert cli.main(["patchbay", "check", str(path)]) == 1
    assert "MISSING no live node 'Chromium/Playback'" in capsys.readouterr().out


def test_check_run_before_the_rig_is_up_says_so_and_does_not_fail(
    tmp_path, monkeypatch, capsys
):
    """The run that exposed this: `patchbay check` with the rig down reported
    `sushi` and the tuner as "definitely not in the graph" alongside one real
    finding, and exited 1. The real finding still shows; the rig's own nodes are
    reported as unchecked, and a check run at the wrong moment does not fail."""
    stale = tmp_path / "handmade.qpwgraph"
    stale.write_text(HANDMADE)
    monkeypatch.setattr(
        patchbay, "read_pw_dump", lambda *a, **k: _with_the_rig_stopped(CAPTURED)
    )
    assert cli.main(["patchbay", "check", str(stale)]) == 1, "the dead profile still fails"
    captured = capsys.readouterr()
    assert "MISSING no live node 'Audient iD14 Pro'" in captured.out
    assert "rig down: node 'sushi'" in captured.out
    assert "MISSING no live node 'sushi'" not in captured.out
    assert "1 node(s) the patchbay names are definitely not in the graph" in captured.err
    # The skipped nodes are reported alongside the finding, not instead of it.
    # An `elif` here meant that in this very scenario -- a real finding and three
    # unchecked nodes -- the reader was never told anything had been skipped.
    assert "3 node(s) could not be checked because the rig is not running" in captured.err


def test_only_the_rig_being_down_is_neither_a_pass_nor_a_failure(tmp_path, monkeypatch, capsys):
    """Nothing here checked the links that matter most, so claiming they all
    name a live node would be false — and failing would punish the user for
    running the check a minute early."""
    path = tmp_path / "p.qpwgraph"
    path.write_text(render_patchbay([
        Link("Audient iD14 Multichannel", "Audient iD14:capture_AUX0", "sushi", "sushi:audio_input_0"),
    ]))
    monkeypatch.setattr(
        patchbay, "read_pw_dump", lambda *a, **k: _with_the_rig_stopped(CAPTURED)
    )
    assert cli.main(["patchbay", "check", str(path)]) == 0
    captured = capsys.readouterr()
    assert "all 1 link(s)" not in captured.out
    assert "sushi-rig up" in captured.err, "tell them how to cover those links"


def test_check_exits_zero_when_everything_exists(tmp_path, monkeypatch, capsys):
    path = tmp_path / "p.qpwgraph"
    path.write_text(render_patchbay([Link("sushi", "sushi:audio_output_0", "alsa_capture.fmit", "x:y")]))
    monkeypatch.setattr(patchbay, "read_pw_dump", lambda *a, **k: LIVE)
    assert cli.main(["patchbay", "check", str(path)]) == 0
    assert "all 1 link(s)" in capsys.readouterr().out


def test_a_pw_dump_that_cannot_run_is_not_reported_as_an_unreadable_patchbay(monkeypatch):
    """A non-executable pw-dump raises OSError, which `check` used to report as
    "cannot read .../rig.qpwgraph" and send the reader to the wrong file."""
    def not_executable(*a, **k):
        raise PermissionError(13, "Permission denied", "pw-dump")

    monkeypatch.setattr(patchbay.subprocess, "run", not_executable)
    with pytest.raises(SystemExit) as caught:
        cli.main(["patchbay", "check", str(COMMITTED)])
    message = str(caught.value)
    assert "pw-dump could not be run" in message
    assert "rig.qpwgraph: cannot check" in message and "cannot read" not in message


def test_an_unreadable_patchbay_file_names_that_file(tmp_path):
    with pytest.raises(SystemExit, match="cannot read .*absent.qpwgraph"):
        cli.main(["patchbay", "check", str(tmp_path / "absent.qpwgraph")])


def test_check_that_cannot_ask_says_so_rather_than_passing(monkeypatch):
    def unavailable(*a, **k):
        raise PatchbayError(["pw-dump is not installed, so the live graph cannot be read"])

    monkeypatch.setattr(patchbay, "read_pw_dump", unavailable)
    with pytest.raises(SystemExit, match="cannot check"):
        cli.main(["patchbay", "check", str(COMMITTED)])


# --- the tuner follows the guitar (#18) ---------------------------------------


def test_the_tuner_taps_the_role_rather_than_a_port():
    """What makes a generated patchbay complete. Without the tap, replacing the
    hand-wired file with a generated one would leave fmit hearing nothing — a
    quiet regression, since nothing else in either file mentions the tuner."""
    xml = _gen(ROLES, {"guitar-in": [bus(0)]}, tuner_role="guitar-in")
    assert (
        "Audient iD14:capture_AUX0",
        patchbay.TUNER_PORT,
    ) in _pairs(xml), _pairs(xml)


def test_the_tuner_taps_the_dry_input_not_sushis_output():
    """A tuner wants the string, not the chain: reading a pitch through a reverb
    and a delay is reading the tail as well as the note."""
    links = parse_patchbay(
        _gen(ROLES, {"guitar-in": [bus(0)]}, {"monitor-out": [bus(0)]},
             tuner_role="guitar-in")
    )
    (tap,) = [l for l in links if l.in_port == patchbay.TUNER_PORT]
    assert tap.out_port == "Audient iD14:capture_AUX0"


def test_the_tuner_has_one_input_so_a_two_port_role_is_refused():
    """Which half of a stereo pair to tune is a judgement, and this module
    refuses those rather than picking — the same rule as `_pair_source`."""
    with pytest.raises(PatchbayError, match="one input"):
        _gen(ROLES, {"stereo-in": [bus(0)]}, tuner_role="stereo-in")


def test_a_tuner_role_the_machine_file_does_not_define_is_refused():
    with pytest.raises(PatchbayError, match="feeds the tuner"):
        _gen(ROLES, {"guitar-in": [bus(0)]}, tuner_role="nonexistent")


def test_no_tuner_role_means_no_tap_at_all():
    xml = _gen(ROLES, {"guitar-in": [bus(0)]})
    assert patchbay.TUNER_PORT not in xml


def test_generate_warns_rather_than_silently_omitting_the_tuner(files, capsys):
    """A machine file with no guitar role gets a patchbay with no tap. That is
    the right file and the wrong silence: a deaf tuner looks like a broken one."""
    (files / "m.yaml").write_text(MACHINE.replace("guitar-in", "lead-in"))
    (files / "rig.yaml").write_text(RIG.replace("guitar-in", "lead-in"))
    assert cli.main(["patchbay", "generate", str(files / "rig.yaml"),
                     "--machine", str(files / "m.yaml"), "--offline"]) == 0
    captured = capsys.readouterr()
    assert patchbay.TUNER_PORT not in captured.out
    assert "no 'guitar-in' role" in captured.err and "fmit" in captured.err


def test_no_tuner_leaves_the_tap_out_on_request(files, capsys):
    cli.main(["patchbay", "generate", str(files / "rig.yaml"),
              "--machine", str(files / "m.yaml"), "--offline", "--no-tuner"])
    assert patchbay.TUNER_PORT not in capsys.readouterr().out


# --- the committed patchbay is generated, not hand-wired ----------------------


def test_the_committed_patchbay_is_what_generate_produces(monkeypatch, capsys):
    """The claim this whole exercise rests on, checked rather than asserted: the
    committed file is byte-for-byte what the command produces from the committed
    rig, the committed machine file and the captured graph. If someone edits it
    in qpwgraph's GUI, this fails and says to regenerate it instead."""
    root = checkout_root()
    monkeypatch.setattr(patchbay, "read_pw_dump", lambda *a, **k: CAPTURED)
    assert cli.main([
        "patchbay", "generate", str(root / "config" / "src" / "guitar_vocal.yaml"),
        "--machine", str(root / "machines" / "sonny-dewfall-XPS-9320.yaml"),
    ]) == 0
    assert capsys.readouterr().out == COMMITTED.read_text()


def test_the_committed_patchbay_centres_the_guitar():
    """The symptom that started this: the guitar came out of the left speaker
    only, because one capture port fed Sushi's input 0 and nothing fed input 1."""
    into = {
        l.in_port: l.out_port for l in parse_patchbay(COMMITTED.read_text())
        if l.in_node == "sushi"
    }
    assert into["sushi:audio_input_0"] == into["sushi:audio_input_1"]


def test_the_committed_patchbay_keeps_the_two_channels_apart():
    """#18's measurable claim. The vocal microphone must not reach the guitar's
    inputs, and the guitar must not reach the vocal track's."""
    into = {
        l.in_port: l.out_port for l in parse_patchbay(COMMITTED.read_text())
        if l.in_node == "sushi"
    }
    guitar = {into["sushi:audio_input_0"], into["sushi:audio_input_1"]}
    vocal = {into["sushi:audio_input_2"], into["sushi:audio_input_3"]}
    assert len(guitar) == 1 and len(vocal) == 1
    assert guitar.isdisjoint(vocal), f"guitar {guitar} and vocal {vocal} share a port"
