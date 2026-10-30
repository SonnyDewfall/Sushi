"""What doctor makes of a machine, against a real capture of a healthy one.

The graph checks are pinned against `fixtures/pw-dump-rig.json` — a real
`pw-dump` from the rig machine with the rig up and working. That capture is the
*healthy* case, so every unhealthy case here is derived from it by changing one
named field and nothing else. Which field is changed is stated in each test,
because a fixture edited until it proves a point is the mistake this project
already made with `pw-top`: the capture is the observation, the edit is the
hypothesis, and the two must not be confusable.

The environment checks take a `doctor.Environment` built by hand. That is not a
capture of anything, and does not pretend to be: it is four facts about a
machine, and the questions worth asking are what each combination of them
means.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from sushi_rig import doctor
from sushi_rig.doctor import FAULT, OK, UNCHECKED, Check, Environment
from sushi_rig.machine import MachineSpec
from sushi_rig.patchbay import nodes_by_id

ROOT = Path(__file__).parent.parent.parent
CAPTURED = (Path(__file__).parent / "fixtures" / "pw-dump-rig.json").read_text()

# Read off the capture, not chosen: the Audient's capture node is the driver
# every rig node points at, and the webcam is the device that once displaced it.
AUDIENT_ID = 71
WEBCAM_ID = 72
RIG_QUANTUM = 256

# What the machine file for that capture names. Hard-coded rather than loaded so
# a later edit to the rig's own machine file cannot quietly change what these
# tests assert.
CLIENTS = {"Audient iD14"}


def captured_nodes() -> dict[int, dict]:
    return nodes_by_id(CAPTURED)


def nodes_with(changes: dict) -> dict[int, dict]:
    """The captured graph with these properties changed on Sushi's node.

    Keys are the PipeWire property names verbatim, so each test states exactly
    which field it moved away from the capture. `None` removes the property,
    which is what "nothing is forcing the quantum" really looks like — the key
    is absent, not set to null.
    """
    nodes = copy.deepcopy(captured_nodes())
    sushi = next(p for p in nodes.values() if p.get("node.name") == "sushi")
    for key, value in changes.items():
        assert key.startswith("node."), f"{key!r} is not a node property"
        if value is None:
            sushi.pop(key, None)
        else:
            sushi[key] = value
    return nodes


def states(checks: list[Check]) -> dict[str, str]:
    """Name -> state, refusing to collapse two checks that share a name.

    This helper is why a real defect hid: `patchbay_checks` returned a FAULT and
    an UNCHECKED both called `patchbay-nodes`, and building a dict from them
    silently dropped the FAULT. The assert is here so the next duplicate fails
    loudly in the helper rather than passing quietly in a test.
    """
    assert len({c.name for c in checks}) == len(checks), (
        "two checks share a name, so a name is not a stable key: "
        + ", ".join(c.name for c in checks)
    )
    return {c.name: c.state for c in checks}


def one(checks: list[Check], name: str) -> Check:
    return next(c for c in checks if c.name == name)


# --------------------------------------------------------------------------- #
# The healthy capture
# --------------------------------------------------------------------------- #

def test_the_real_capture_of_a_working_rig_passes_every_graph_check():
    """The one case that is an observation rather than a construction. If this
    fails, either the checks are wrong or the rig was never healthy."""
    checks = doctor.graph_checks(
        captured_nodes(), CLIENTS, expected_quantum=RIG_QUANTUM
    )
    assert states(checks) == {
        "clock-driver": OK, "one-clock": OK, "quantum": OK, "rate": OK
    }, "\n" + "\n".join(str(c) for c in checks)


def test_the_capture_names_the_interface_the_machine_file_names():
    check = doctor.check_clock_driver(captured_nodes(), CLIENTS)
    assert check.state == OK
    assert "Audient iD14" in check.message


def test_the_captured_quantum_is_the_one_the_rig_states_and_is_locked():
    check = doctor.check_quantum(captured_nodes(), RIG_QUANTUM)
    assert check.state == OK
    assert "256 samples" in check.message
    assert "5.33 ms" in check.message
    assert "locked it" in check.message, "which is why a restart is the fix"


def test_the_rig_and_the_amp_share_one_driver_group_in_the_capture():
    check = doctor.check_one_clock(captured_nodes())
    assert check.state == OK
    for name in ("sushi", "Carla", "NAM"):
        assert name in check.message


def test_the_webcam_is_also_a_driver_in_the_healthy_capture():
    """Why the check follows `node.driver-id` from Sushi rather than looking for
    a node with `node.driver = True`. Both the Audient and the webcam carry that
    flag here — each drives its own group — so a check that went looking for
    "the driver" would find two and have nothing to choose between them."""
    nodes = captured_nodes()
    drivers = {nid for nid, props in nodes.items() if props.get("node.driver") is True}
    assert {AUDIENT_ID, WEBCAM_ID} <= drivers
    assert doctor.check_clock_driver(nodes, CLIENTS).state == OK


# --------------------------------------------------------------------------- #
# One field changed: the faults the session actually hit
# --------------------------------------------------------------------------- #

def test_a_webcam_clocking_the_rig_is_found_and_named():
    """Sushi's `node.driver-id` moved from the Audient to the webcam, which is
    what the tuner's autoconnect did on 2026-10-26. Nothing else changed."""
    check = doctor.check_clock_driver(nodes_with({"node.driver-id": WEBCAM_ID}), CLIENTS)
    assert check.state == FAULT
    assert "BRIO" in check.message, "name the thing that is clocking it"
    assert "Audient iD14" in check.message, "and what the machine file expected"
    assert "autoconnect" in check.remedy


def test_the_desktops_quantum_is_found_with_both_figures():
    """`node.force-quantum` set to 2048, the value a fresh machine inherited."""
    check = doctor.check_quantum(nodes_with({"node.force-quantum": 2048}), RIG_QUANTUM)
    assert check.state == FAULT
    assert "2048" in check.message and "256" in check.message
    assert "42.67 ms" in check.message, "the cost, not just the number"
    assert "PIPEWIRE_QUANTUM" in check.remedy


def test_nothing_forcing_a_quantum_at_all_is_a_fault_not_a_default():
    """The original failure: nothing in the rig set it, so the graph's own
    value applied and no one was told."""
    check = doctor.check_quantum(nodes_with({"node.force-quantum": None}), RIG_QUANTUM)
    assert check.state == FAULT
    assert "2048" in check.message and "42.69 ms" in check.message
    assert "sushi-rig up" in check.remedy


def test_a_quantum_that_is_not_a_block_size_is_reported_rather_than_coerced():
    """`True` is an `int` in Python, so a sloppy read of this property would
    report a quantum of 1 and call it a fault about the wrong thing."""
    check = doctor.check_quantum(nodes_with({"node.force-quantum": True}), RIG_QUANTUM)
    assert check.state == FAULT
    assert "not a block size" in check.message


def test_the_amp_in_a_different_driver_group_is_found():
    """NAM's `node.driver-id` moved off the Audient. Two JACK clients in
    different groups are not scheduled in one cycle, and each locks its own
    quantum."""
    nodes = copy.deepcopy(captured_nodes())
    nam = next(p for p in nodes.values() if p.get("node.name") == "NAM")
    nam["node.driver-id"] = WEBCAM_ID
    check = doctor.check_one_clock(nodes)
    assert check.state == FAULT
    assert "NAM" in check.message and "sushi" in check.message


def test_no_rig_node_in_any_driver_group_is_not_one_shared_group():
    """`node.driver-id` removed from Sushi. Every node then reports `None`, and
    reading that as agreement printed "all in driver group None" — an ok for a
    rig that is not being scheduled at all. clock-driver is what reports it, so
    this goes unchecked rather than saying it twice."""
    check = doctor.check_one_clock(nodes_with({"node.driver-id": None}))
    assert check.state == UNCHECKED
    assert "clock-driver reports that" in check.message


def test_a_wrong_sample_rate_is_found():
    check = doctor.check_rate(nodes_with({"node.force-rate": 44100}))
    assert check.state == FAULT
    assert "44100" in check.message and "48000" in check.message


def test_no_forced_rate_is_a_fault_because_a_mismatch_would_be_silent():
    check = doctor.check_rate(nodes_with({"node.force-rate": None}))
    assert check.state == FAULT
    assert "silent" in check.message


# --------------------------------------------------------------------------- #
# Unchecked is not a pass
# --------------------------------------------------------------------------- #

def test_a_rig_that_is_down_leaves_every_graph_check_unchecked():
    """Reporting ok here would be the loudest lie the command could tell."""
    nodes = {
        nid: props for nid, props in captured_nodes().items()
        if props.get("node.name") != "sushi"
    }
    checks = doctor.graph_checks(nodes, CLIENTS, expected_quantum=RIG_QUANTUM)
    assert set(states(checks).values()) == {UNCHECKED}
    assert all("sushi-rig up" in c.remedy for c in checks)


def test_no_graph_at_all_is_one_finding_naming_what_went_unanswered():
    """Four identical lines would say the same thing four times; the reader
    needs to know which questions went unanswered, once."""
    checks = doctor.graph_checks(
        None, CLIENTS, expected_quantum=RIG_QUANTUM, reason="pw-dump is not installed"
    )
    assert states(checks) == {"pipewire-graph": UNCHECKED}
    for question in ("driver", "quantum", "rate"):
        assert question in checks[0].message
    assert "pw-dump is not installed" in checks[0].message


def test_an_unreadable_machine_file_does_not_cascade_into_the_clock_check():
    """One broken file should produce one finding, not a second one inventing
    its own version of the same problem."""
    check = doctor.check_clock_driver(captured_nodes(), set())
    assert check.state == UNCHECKED
    assert "machine check" in check.remedy


# --------------------------------------------------------------------------- #
# The machine file and the patchbay
# --------------------------------------------------------------------------- #

def test_the_rigs_own_machine_file_is_valid_and_names_its_interface():
    """Both halves at once: the file the rig actually ships passes, and what
    `interface_clients` reads off it is what the captured driver answers to."""
    spec = MachineSpec.load(ROOT / "machines" / "sonny-dewfall-XPS-9320.yaml")
    assert spec.validate() == []
    assert doctor.interface_clients(spec.role_ports()) == CLIENTS


def test_interface_clients_takes_the_ports_and_ignores_what_is_not_one():
    assert doctor.interface_clients({
        "guitar-in": ["Audient iD14:capture_AUX0"],
        "monitor-out": ["Audient iD14:playback_FL", "Other Box:playback_FR"],
        "broken": ["no-colon-here", 7],
    }) == {"Audient iD14", "Other Box"}


@pytest.mark.parametrize("problems, readable, state", [
    ([], True, OK),
    (["role 'guitar-in': has no ports"], True, FAULT),
    ([], False, FAULT),
])
def test_the_machine_file_check_reports_each_way_it_can_go(problems, readable, state):
    check = doctor.machine_check(
        problems, file_name="host.yaml", readable=readable, why="no such file"
    )
    assert check.state == state
    assert check.remedy if state == FAULT else True


def test_a_stale_patchbay_node_is_a_fault_and_says_how_to_fix_it():
    checks = doctor.patchbay_checks(
        {"Audient iD14 Pro": ["a", "b"]}, {}, file_name="rig.qpwgraph", links=20
    )
    assert states(checks) == {"patchbay-stale": FAULT}
    assert "Audient iD14 Pro" in checks[0].message and "2 link(s)" in checks[0].message
    assert "patchbay generate" in checks[0].remedy


def test_a_node_the_rig_starts_is_unchecked_rather_than_stale():
    checks = doctor.patchbay_checks(
        {}, {"sushi": ["a"]}, file_name="rig.qpwgraph", links=20
    )
    assert states(checks) == {"patchbay-unchecked": UNCHECKED}


def test_both_a_stale_node_and_an_unchecked_one_are_reported():
    """The `elif` that hid this is a logged defect: there was a real finding
    alongside three skipped nodes, and the reader was never told."""
    checks = doctor.patchbay_checks(
        {"Gone": ["a"]}, {"sushi": ["b"]}, file_name="rig.qpwgraph", links=20
    )
    # Two names, not one: `states` refuses to build a dict from duplicates, and
    # that refusal is the whole reason this reads as two checks now.
    assert states(checks) == {"patchbay-stale": FAULT, "patchbay-unchecked": UNCHECKED}


def test_a_patchbay_with_nothing_wrong_says_how_many_links_it_checked():
    checks = doctor.patchbay_checks({}, {}, file_name="rig.qpwgraph", links=20)
    assert states(checks) == {"patchbay-nodes": OK}
    assert "20 link(s)" in checks[0].message


# --------------------------------------------------------------------------- #
# The machine around the graph
# --------------------------------------------------------------------------- #

def environment(**changes) -> Environment:
    base = {
        "pw_jack": "/usr/bin/pw-jack", "carla": "/usr/bin/carla",
        "jackd_pids": (), "fuse2": True, "sushi_is_appimage": True,
    }
    return Environment(**{**base, **changes})


def test_a_healthy_machine_passes_every_environment_check():
    assert set(states(doctor.environment_checks(environment())).values()) == {OK}


def test_a_missing_pw_jack_explains_the_session_it_cost():
    check = doctor.check_pw_jack(environment(pw_jack=None))
    assert check.state == FAULT
    assert "playing dry" in check.message
    assert "pipewire-jack" in check.remedy


def test_not_being_able_to_look_for_jackd_is_not_the_same_as_finding_none():
    """The module's own three-state rule, applied to its own gathering: a
    machine without procps used to get a confident "no jackd is running"."""
    check = doctor.check_no_jackd(environment(jackd_pids=None))
    assert check.state == UNCHECKED
    assert "could not be run" in check.message
    assert check.remedy


def test_a_real_jackd_alongside_pipewire_is_found_by_pid():
    check = doctor.check_no_jackd(environment(jackd_pids=(4242,)))
    assert check.state == FAULT
    assert "4242" in check.message


def test_a_missing_carla_says_the_amp_cannot_run_and_offers_the_honest_fallback():
    check = doctor.check_carla(environment(carla=None))
    assert check.state == FAULT
    assert "--no-amp" in check.remedy, "which says so rather than playing dry"


@pytest.mark.parametrize("is_appimage, fuse2, state, expected", [
    (True, True, OK, "loads"),
    (True, False, FAULT, "require FUSE to run"),
    (False, False, OK, "not an AppImage"),
    (None, False, UNCHECKED, "no ./sushi"),
])
def test_the_fuse_check_covers_every_combination(is_appimage, fuse2, state, expected):
    """FUSE 3 does not satisfy an AppImage, which is why this is checked by
    soname rather than by asking whether "fuse" is installed."""
    check = doctor.check_fuse(environment(sushi_is_appimage=is_appimage, fuse2=fuse2))
    assert check.state == state
    assert expected in check.message


# --------------------------------------------------------------------------- #
# Invariants and the effects
# --------------------------------------------------------------------------- #

def test_every_fault_doctor_can_report_carries_a_remedy():
    """Principle 7, enforced rather than remembered: a check that says
    `node.force-quantum` is 2048 and stops there has told the wrong person a
    true thing."""
    produced: list[Check] = []
    for nodes in (
        nodes_with({"node.driver-id": WEBCAM_ID}),
        nodes_with({"node.force-quantum": 2048}),
        nodes_with({"node.force-quantum": None}),
        nodes_with({"node.force-rate": 44100}),
        nodes_with({"node.force-rate": None}),
        nodes_with({"node.driver-id": None}),
        nodes_with({"node.driver-id": 99999}),
    ):
        produced += doctor.graph_checks(nodes, CLIENTS, expected_quantum=RIG_QUANTUM)
    produced += doctor.environment_checks(environment(
        pw_jack=None, carla=None, jackd_pids=(1,), fuse2=False
    ))
    produced += doctor.patchbay_checks({"Gone": ["a"]}, {}, file_name="p", links=1)
    produced.append(doctor.machine_check(["bad"], file_name="m.yaml"))

    assert doctor.faults(produced), "this test would pass vacuously otherwise"
    for check in doctor.faults(produced):
        assert check.remedy, f"{check.name} says what is wrong but not what to do"


def test_a_check_reads_as_its_state_its_measurement_and_its_remedy():
    printed = str(Check("quantum", FAULT, "asked for 2048", "bring the rig down"))
    assert "FAULT" in printed
    assert "quantum: asked for 2048" in printed
    assert "-> bring the rig down" in printed


def test_an_appimage_is_recognised_by_its_magic_not_its_name(tmp_path):
    """The rig's `./sushi` is a symlink, and a directory extracted with
    `--appimage-extract` is a perfectly good `./sushi` that needs no FUSE."""
    appimage = tmp_path / "anything"
    appimage.write_bytes(b"\x7fELF\x02\x01\x01\x00" + doctor.APPIMAGE_MAGIC + b"rest")
    assert doctor._is_appimage(appimage) is True

    plain = tmp_path / "plain"
    plain.write_bytes(b"\x7fELF\x02\x01\x01\x00\x00\x00\x00rest")
    assert doctor._is_appimage(plain) is False

    assert doctor._is_appimage(tmp_path / "absent") is None

    # A directory is what `--appimage-extract` leaves, and the comment on
    # APPIMAGE_MAGIC says that is a valid ./sushi needing no FUSE. Mapping every
    # OSError to None contradicted it and reported "there is no ./sushi to run".
    extracted = tmp_path / "squashfs-root"
    extracted.mkdir()
    assert doctor._is_appimage(extracted) is False


def test_gathering_the_environment_runs_and_answers_in_the_right_types():
    """The effect, exercised. What it finds depends on the machine, so only the
    shape is asserted — the decisions above are where the meaning is tested."""
    found = doctor.gather_environment(ROOT)
    assert isinstance(found.jackd_pids, tuple)
    assert all(isinstance(pid, int) for pid in found.jackd_pids)
    assert isinstance(found.fuse2, bool)
    assert found.sushi_is_appimage in (True, False, None)
    assert found.pw_jack is None or isinstance(found.pw_jack, str)


def test_the_captured_fixture_is_still_a_dump_of_nodes_and_ports():
    """If the capture is ever replaced, these tests' ids and assumptions go with
    it. Fail here rather than three tests later with a confusing message."""
    objects = json.loads(CAPTURED)
    assert {o["id"] for o in objects} >= {AUDIENT_ID, WEBCAM_ID}
    names = {p.get("node.name") for p in captured_nodes().values()}
    assert {"sushi", "Carla", "NAM"} <= names


def test_the_command_runs_end_to_end_and_names_every_check(capsys):
    """The anti-rot guarantee #48 asks for. What it *finds* depends on the
    machine — this container has no PipeWire, the rig machine has all of it — so
    the assertion is that the handler runs every check and says something about
    each, rather than that any particular one passes.

    Worth having because the failure this guards is not a wrong verdict but a
    handler that raises: every effect in `doctor` is exercised here, including
    the two that shell out.
    """
    from sushi_rig import cli

    exit_code = cli.main([
        "doctor",
        "--machine", str(ROOT / "machines" / "sonny-dewfall-XPS-9320.yaml"),
        "--patchbay", str(ROOT / "Patchbay" / "rig.qpwgraph"),
    ])
    printed = capsys.readouterr()
    assert exit_code in (0, 1), "doctor either passes or fails; nothing else"
    for name in ("machine-file", "pw-jack", "jackd", "carla", "fuse2"):
        assert name in printed.out, f"{name} was not reported at all"
    # Either the graph was read and the four questions answered, or it was not
    # and that was said. Both are honest; silence would not be.
    assert "pipewire-graph" in printed.out or "clock-driver" in printed.out
    assert "patchbay-nodes" in printed.out


def test_a_pw_dump_that_is_not_json_does_not_abort_the_command(monkeypatch, capsys):
    """`pw-dump` exiting 0 with output that is not JSON raises from the *parse*,
    not the read. That escaped as a traceback, while the identical exception
    from the same helper was caught for the patchbay check."""
    from sushi_rig import cli, patchbay

    monkeypatch.setattr(patchbay, "read_pw_dump", lambda *a, **k: "not json at all")
    exit_code = cli.main([
        "doctor",
        "--machine", str(ROOT / "machines" / "sonny-dewfall-XPS-9320.yaml"),
    ])
    printed = capsys.readouterr().out
    assert exit_code in (0, 1)
    assert "pipewire-graph" in printed, "the graph went unchecked, and says so"
    assert "did not produce JSON" in printed
    assert "patchbay-unchecked" in printed or "could not be checked" in printed
