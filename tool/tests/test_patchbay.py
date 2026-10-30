"""Patchbay generation, and the check that a saved patchbay is still true.

Everything here is a decision, so nothing needs PipeWire or an audio interface
(quality principle 1). What is NOT verified by this file is the live half:
`read_pw_dump` against a real PipeWire, and qpwgraph loading a generated file.
Those are effects, checked by running them on the rig machine.

THE pw-dump FIXTURE IS NOT A CAPTURE. It was written in an environment with no
PipeWire. What it relies on is pw-dump's documented object shape (a JSON list of
objects with `id`, `type` and `info.props`); the node descriptions are taken from
the node names in the committed `Patchbay/rig.qpwgraph`, which is evidence for
what qpwgraph labels a node, not for what pw-dump says. Replace it with a real
`pw-dump` from the rig machine when one is to hand.
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
    stale_nodes,
)
from sushi_rig.paths import checkout_root
from sushi_rig.spec import RigSpec, TrackSpec

COMMITTED = checkout_root() / "Patchbay" / "rig.qpwgraph"

ROLES = {
    "guitar-in": ["Audient iD14:capture_AUX0"],
    "vocal-in": ["Audient iD14:capture_AUX1"],
    "stereo-in": ["Audient iD14:capture_AUX2", "Audient iD14:capture_AUX3"],
    "monitor-out": ["Audient iD14:playback_AUX0", "Audient iD14:playback_AUX1"],
}


def _pairs(xml: str) -> list[tuple[str, str]]:
    """(source port, destination port) for each link, in file order."""
    return [(l.out_port, l.in_port) for l in parse_patchbay(xml)]


# --- mono into stereo ---------------------------------------------------------


def test_a_mono_role_feeding_a_stereo_bus_is_duplicated_across_both_halves():
    """The established pattern: capture_AUX0 feeds both audio_input_0 and
    audio_input_1, which is what lets a stereo chain follow a mono guitar."""
    xml = generate_patchbay(ROLES, {"guitar-in": [bus(0)]})
    assert _pairs(xml) == [
        ("Audient iD14:capture_AUX0", "sushi:audio_input_0"),
        ("Audient iD14:capture_AUX0", "sushi:audio_input_1"),
    ]


def test_a_second_mono_role_on_the_next_bus_uses_that_buss_channels():
    """Bus N is channels 2N and 2N+1 — the committed patchbay has capture_AUX1
    on audio_input_2 and audio_input_3."""
    xml = generate_patchbay(ROLES, {"guitar-in": [bus(0)], "vocal-in": [bus(1)]})
    assert _pairs(xml)[2:] == [
        ("Audient iD14:capture_AUX1", "sushi:audio_input_2"),
        ("Audient iD14:capture_AUX1", "sushi:audio_input_3"),
    ]


def test_a_two_port_role_goes_left_then_right_without_duplication():
    xml = generate_patchbay(ROLES, {"stereo-in": [bus(1)]})
    assert _pairs(xml) == [
        ("Audient iD14:capture_AUX2", "sushi:audio_input_2"),
        ("Audient iD14:capture_AUX3", "sushi:audio_input_3"),
    ]


def test_a_one_port_role_feeding_a_single_channel_feeds_only_that_channel():
    """Sushi routes by pair or by channel, never both in one entry."""
    xml = generate_patchbay(ROLES, {"guitar-in": [channel(3)]})
    assert _pairs(xml) == [("Audient iD14:capture_AUX0", "sushi:audio_input_3")]


def test_one_role_may_feed_several_buses():
    xml = generate_patchbay(ROLES, {"guitar-in": [bus(0), bus(1)]})
    assert [dst for _, dst in _pairs(xml)] == [f"sushi:audio_input_{i}" for i in range(4)]


# --- what cannot be resolved is an error, not a guess --------------------------


def test_three_ports_into_a_stereo_bus_is_an_error():
    roles = {"wide": ["A:1", "A:2", "A:3"]}
    with pytest.raises(PatchbayError, match="3 ports but feeds bus 0"):
        generate_patchbay(roles, {"wide": [bus(0)]})


def test_two_ports_into_one_channel_is_an_error_rather_than_a_pick():
    """Dropping one, or summing both, are both guesses about the user's intent."""
    with pytest.raises(PatchbayError, match="single channel 0"):
        generate_patchbay(ROLES, {"stereo-in": [channel(0)]})


def test_an_unknown_role_names_the_role_and_what_the_machine_defines():
    with pytest.raises(PatchbayError) as caught:
        generate_patchbay(ROLES, {"guitar_in": [bus(0)]})
    message = str(caught.value)
    assert "'guitar_in'" in message
    assert "'guitar-in'" in message, "the near-miss spelling must be offered"


def test_two_roles_on_one_bus_are_refused_rather_than_summed():
    """The near-miss `amp_patchbay` records: redirecting everything that feeds
    Sushi summed two physical inputs into one mono amp. With a microphone on the
    second input that stops being hypothetical."""
    with pytest.raises(PatchbayError) as caught:
        generate_patchbay(ROLES, {"guitar-in": [bus(0)], "vocal-in": [bus(0)]})
    assert "sushi:audio_input_0 would be fed by more than one port" in str(caught.value)


def test_a_bus_and_a_channel_overlapping_are_caught_too():
    with pytest.raises(PatchbayError, match="audio_input_1"):
        generate_patchbay(ROLES, {"guitar-in": [bus(0)], "vocal-in": [channel(1)]})


def test_two_roles_sharing_one_physical_port_is_fine():
    """The tuner and Sushi both listening to the guitar is the committed setup."""
    roles = {"guitar-in": ["A:x"], "tuner-in": ["A:x"]}
    xml = generate_patchbay(roles, {"guitar-in": [channel(0)], "tuner-in": [channel(0)]})
    assert _pairs(xml) == [("A:x", "sushi:audio_input_0")]


def test_every_problem_is_reported_together():
    with pytest.raises(PatchbayError) as caught:
        generate_patchbay(ROLES, {"nope": [bus(0)], "stereo-in": [channel(0)]})
    assert len(caught.value.problems) == 2


# --- outputs ------------------------------------------------------------------


def test_a_two_port_sink_takes_a_bus_left_then_right():
    xml = generate_patchbay(ROLES, {}, {"monitor-out": [bus(0)]})
    assert _pairs(xml) == [
        ("sushi:audio_output_0", "Audient iD14:playback_AUX0"),
        ("sushi:audio_output_1", "Audient iD14:playback_AUX1"),
    ]


def test_a_one_port_sink_for_a_stereo_bus_is_an_error_not_a_mixdown():
    roles = {"mono-out": ["A:p"]}
    with pytest.raises(PatchbayError, match="needs 2"):
        generate_patchbay(roles, {}, {"mono-out": [bus(0)]})


def test_a_sink_for_a_channel_needs_exactly_one_port():
    with pytest.raises(PatchbayError, match="needs 1"):
        generate_patchbay(ROLES, {}, {"monitor-out": [channel(0)]})


def test_one_output_may_go_to_two_places():
    """Monitors and headphones at once is ordinary; only *inputs* must not sum."""
    roles = {"mon": ["A:l", "A:r"], "phones": ["B:l", "B:r"]}
    xml = generate_patchbay(roles, {}, {"mon": [bus(0)], "phones": [bus(0)]})
    assert len(parse_patchbay(xml)) == 4


def test_two_outputs_into_one_playback_port_are_refused():
    roles = {"mon": ["A:l", "A:r"], "other": ["A:r", "A:x"]}
    with pytest.raises(PatchbayError, match="playback port 'A:r'"):
        generate_patchbay(roles, {}, {"mon": [bus(0)], "other": [bus(1)]})


# --- node names ---------------------------------------------------------------


def test_the_node_falls_back_to_the_ports_client_and_sushi_is_always_sushi():
    link = parse_patchbay(generate_patchbay(ROLES, {"guitar-in": [bus(0)]}))[0]
    assert (link.out_node, link.in_node) == ("Audient iD14", "sushi")


def test_a_supplied_node_replaces_the_fallback():
    """The port alias is the same under every ALSA profile; the node is what
    differs, so it has to come from somewhere other than the machine file."""
    nodes = {"Audient iD14:capture_AUX0": "Audient iD14 Multichannel"}
    link = parse_patchbay(generate_patchbay(ROLES, {"guitar-in": [bus(0)]}, nodes=nodes))[0]
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
    a = generate_patchbay(ROLES, {"guitar-in": [bus(0)], "vocal-in": [bus(1)]})
    b = generate_patchbay(dict(reversed(ROLES.items())), {"vocal-in": [bus(1)], "guitar-in": [bus(0)]})
    assert a == b


def test_the_amp_rewrite_understands_a_generated_patchbay():
    """`amp_patchbay` finds the guitar as whatever feeds sushi:audio_input_0 and
    is out of scope to change. If generation produced something it could not read,
    switching to generated patchbays would silently drop the amp."""
    xml = generate_patchbay(ROLES, {"guitar-in": [bus(0)]}, {"monitor-out": [bus(0)]})
    amped = amp_patchbay(xml)
    pairs = _pairs(amped)
    assert ("Audient iD14:capture_AUX0", "NAM:Input") in pairs
    assert ("NAM:Output", "sushi:audio_input_0") in pairs
    assert ("NAM:Output", "sushi:audio_input_1") in pairs
    assert not any(src == "Audient iD14:capture_AUX0" and dst.startswith("sushi:") for src, dst in pairs)


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
    assert feeds == {"guitar-in": [bus(0)], "vocal-in": [channel(3)]}
    assert sinks == {"monitor-out": [bus(0)]}


def test_a_track_without_a_source_contributes_nothing():
    """Every existing config is like this, and must generate nothing rather than
    something."""
    rig = RigSpec(tracks=[TrackSpec(name="t", inputs=[{"engine_bus": 0, "track_bus": 0}])])
    assert routes_from_rig(rig) == ({}, {})


def test_an_entry_mixing_bus_and_channel_forms_is_skipped_not_guessed():
    rig = RigSpec(tracks=[TrackSpec(
        name="t", source="r", inputs=[{"engine_bus": 0, "engine_channel": 1}],
    )])
    assert routes_from_rig(rig) == ({}, {})


def test_two_tracks_on_one_role_and_bus_do_not_duplicate_the_route():
    rig = RigSpec(tracks=[
        TrackSpec(name=n, source="guitar-in", inputs=[{"engine_bus": 0, "track_bus": 0}])
        for n in ("a", "b")
    ])
    assert routes_from_rig(rig)[0] == {"guitar-in": [bus(0)]}


# --- the live graph -------------------------------------------------------------


def _node(id_, **props):
    return {"id": id_, "type": "PipeWire:Interface:Node", "info": {"props": props}}


def _port(id_, node_id, alias):
    return {
        "id": id_, "type": "PipeWire:Interface:Port",
        "info": {"props": {"node.id": node_id, "port.alias": alias}},
    }


# The rig machine as it is today, as best the committed patchbay shows: the
# Multichannel input and Surround 4.0 output profiles live, Sushi and the tuner
# running. Invented structure, see the module docstring.
LIVE = json.dumps([
    {"id": 0, "type": "PipeWire:Interface:Core", "info": {"props": {}}},
    _node(40, **{"node.name": "alsa_input.usb-Audient_Audient_iD14-00.multichannel-input",
                 "node.description": "Audient iD14 Multichannel", "node.nick": "Audient iD14"}),
    _node(41, **{"node.name": "alsa_output.usb-Audient_Audient_iD14-00.analog-surround-40",
                 "node.description": "Audient iD14 Analog Surround 4.0"}),
    _node(42, **{"node.name": "sushi"}),
    _node(43, **{"node.name": "alsa_capture.fmit"}),
    _port(50, 40, "Audient iD14:capture_AUX0"),
    _port(51, 41, "Audient iD14:playback_FL"),
    _port(53, 41, "Audient iD14:playback_FR"),
    _port(52, 42, "sushi:audio_input_0"),
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


def test_the_stale_check_names_each_missing_node_once_with_its_links():
    """The committed patchbay names three Audient profiles. Only the ones that
    exist are live; the rest are what this check exists to show."""
    stale = stale_nodes(parse_patchbay(COMMITTED.read_text()), live_node_names(LIVE))
    assert set(stale) == {
        "Audient iD14 Pro",
        "PipeWire ALSA [fmit]/ALSA Capture [fmit]",
        "fmit",
    }
    # Four capture links and four playback links name the dead Pro profile.
    assert len(stale["Audient iD14 Pro"]) == 9


def test_a_fully_live_patchbay_has_nothing_stale():
    links = [Link("Audient iD14 Multichannel", "Audient iD14:capture_AUX0", "sushi", "sushi:audio_input_0")]
    assert stale_nodes(links, live_node_names(LIVE)) == {}


def test_a_link_with_both_ends_missing_is_listed_under_each():
    link = Link("gone-a", "a:o", "gone-b", "b:i")
    assert stale_nodes([link], live_node_names(LIVE)) == {"gone-a": [link], "gone-b": [link]}


def test_a_link_is_not_listed_twice_under_one_node():
    link = Link("gone", "g:o", "gone", "g:i")
    assert stale_nodes([link], set()) == {"gone": [link]}


def test_a_patchbay_that_is_not_xml_is_an_error():
    with pytest.raises(PatchbayError, match="not a readable patchbay"):
        parse_patchbay("<patchbay><items>")


# --- role -> live port ----------------------------------------------------------


def test_a_role_resolves_to_the_node_that_owns_its_port():
    nodes, problems = resolve_nodes(ROLES, {"guitar-in"}, live_port_nodes(LIVE))
    assert problems == []
    assert nodes == {"Audient iD14:capture_AUX0": "Audient iD14 Multichannel"}


def test_a_role_with_no_live_port_names_the_role_and_what_it_looked_for():
    """The failure this issue exists for: a renamed interface used to give
    silence and dead entries, and now gives this."""
    _, problems = resolve_nodes(ROLES, {"vocal-in"}, live_port_nodes(LIVE))
    assert len(problems) == 1
    assert "'vocal-in'" in problems[0] and "Audient iD14:capture_AUX1" in problems[0]


def test_roles_no_track_uses_are_not_looked_up():
    """Unplugged hardware is a problem only if something needs it."""
    _, problems = resolve_nodes(ROLES, {"guitar-in"}, live_port_nodes(LIVE))
    assert problems == []


def test_a_port_alias_on_two_nodes_is_ambiguous_not_picked():
    """`Audient iD14:capture_AUX0` is the same under the Pro and Multichannel
    profiles, so both being live would make the node a coin toss."""
    dump = json.dumps([
        _node(1, **{"node.description": "Audient iD14 Pro"}),
        _node(2, **{"node.description": "Audient iD14 Multichannel"}),
        _port(10, 1, "Audient iD14:capture_AUX0"),
        _port(11, 2, "Audient iD14:capture_AUX0"),
    ])
    _, problems = resolve_nodes(ROLES, {"guitar-in"}, live_port_nodes(dump))
    assert len(problems) == 1 and "more than one node" in problems[0]


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
  guitar-in: ["Audient iD14:capture_AUX0"]
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
    assert len(parse_patchbay(out)) == 4


def test_generate_writes_only_where_told(files, capsys):
    target = files / "out.qpwgraph"
    cli.main(["patchbay", "generate", str(files / "rig.yaml"),
              "--machine", str(files / "m.yaml"), "--offline", "-o", str(target)])
    assert len(parse_patchbay(target.read_text())) == 4
    assert "<" not in capsys.readouterr().out


def test_generate_names_device_nodes_from_the_live_graph(files, monkeypatch, capsys):
    """Offline, the device node would be "Audient iD14" and qpwgraph would never
    match it. Live, it is the profile that is actually running."""
    (files / "m.yaml").write_text(
        "schema_version: 1\nroles:\n  guitar-in: ['Audient iD14:capture_AUX0']\n"
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


def test_check_lists_stale_nodes_and_exits_nonzero(monkeypatch, capsys):
    monkeypatch.setattr(patchbay, "read_pw_dump", lambda *a, **k: LIVE)
    assert cli.main(["patchbay", "check", str(COMMITTED)]) == 1
    out = capsys.readouterr().out
    assert "no live node 'Audient iD14 Pro'" in out
    assert "Audient iD14:capture_AUX0 -> sushi:audio_input_0" in out


def test_check_exits_zero_when_everything_exists(tmp_path, monkeypatch, capsys):
    path = tmp_path / "p.qpwgraph"
    path.write_text(render_patchbay([Link("sushi", "sushi:audio_output_0", "alsa_capture.fmit", "x:y")]))
    monkeypatch.setattr(patchbay, "read_pw_dump", lambda *a, **k: LIVE)
    assert cli.main(["patchbay", "check", str(path)]) == 0
    assert "all 1 link(s)" in capsys.readouterr().out


def test_check_that_cannot_ask_says_so_rather_than_passing(monkeypatch):
    def unavailable(*a, **k):
        raise PatchbayError(["pw-dump is not installed, so the live graph cannot be read"])

    monkeypatch.setattr(patchbay, "read_pw_dump", unavailable)
    with pytest.raises(SystemExit, match="cannot check"):
        cli.main(["patchbay", "check", str(COMMITTED)])
