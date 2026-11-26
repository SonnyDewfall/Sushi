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
from rig_fixtures import FROZEN_MACHINE, frozen

from sushi_rig import doctor
from sushi_rig.doctor import FAULT, OK, UNCHECKED, Check, Environment
from sushi_rig.machine import MachineSpec
from sushi_rig.patchbay import nodes_by_id
# The real reader of the live graph, bound before conftest stands it in for
# every test, for the one test of what doctor says when it cannot read.
from sushi_rig.transport import _live_jack_names as read_live_jack_names

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
    spec = MachineSpec.load(FROZEN_MACHINE)  # frozen beside the capture it is read against (rig_fixtures.py)
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
        "takes_free_bytes": 10 ** 12, "takes_dir": "/home/me/sushi-rig-takes",
        "take_stems": 4, "take_rig": "guitar_vocal",
        "models": (doctor.ModelInfo("Neve 1.nam", 48000),),
        # As /proc showed PipeWire 1.0.5's pipewire-pulse on 2026-11-18, in a container, as root
        # (`ps -L`: pw-data-loop FF 83). Not read on the rig.
        "pulse_threads": {4242: (("pipewire-pulse", 0, 0), ("pw-data-loop", 1, 83))},
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
        pw_jack=None, carla=None, jackd_pids=(1,), fuse2=False, takes_free_bytes=10,
        models=(doctor.ModelInfo("old.nam", 44100), doctor.ModelInfo("broken.nam", None, "not valid JSON")),
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


@pytest.mark.committed_rigs  # doctor reads every rig of config/rigs/ (#218)
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


@pytest.mark.committed_rigs  # doctor reads every rig of config/rigs/ (#218)
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
    for name in ("machine-file", "pw-jack", "jackd", "carla", "fuse2", "disk", "amp-models"):
        assert name in printed.out, f"{name} was not reported at all"
    # Either the graph was read and the four questions answered, or it was not
    # and that was said. Both are honest; silence would not be.
    assert "pipewire-graph" in printed.out or "clock-driver" in printed.out
    # The patchbay check is three-way, and which arm fires depends on the
    # machine: `patchbay-nodes` (OK) where every device it names is present,
    # `patchbay-stale` (FAULT) where it names one that is absent, and
    # `patchbay-unchecked` where the graph could not be read. All three are an
    # answer; exactly one must be given, and never none.
    verdicts = [
        name for name in ("patchbay-nodes", "patchbay-stale", "patchbay-unchecked")
        if name in printed.out
    ]
    assert len(verdicts) == 1, f"expected one patchbay verdict, got {verdicts}"


@pytest.mark.committed_rigs  # doctor reads every rig of config/rigs/ (#218)
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



# --------------------------------------------------------------------------- #
# Room to record, and the amp models' rates
# --------------------------------------------------------------------------- #

# Four stems of 48 kHz float32 for an hour: the threshold, by its own arithmetic.
ONE_HOUR = 4 * doctor.EXPECTED_RATE * doctor.TAKE_BYTES_PER_SAMPLE * 3600


def test_an_hour_of_recording_is_enough_and_a_byte_less_is_not():
    """#53 puts this here so it is found before playing, not mid-take."""
    assert ONE_HOUR == 2_764_800_000
    assert doctor.check_disk(environment(takes_free_bytes=ONE_HOUR)).state == OK
    short = doctor.check_disk(environment(takes_free_bytes=ONE_HOUR - 1))
    assert short.state == FAULT
    assert "performance lost" in short.remedy


def test_a_fault_never_rounds_itself_up_past_the_threshold():
    """It used to say "about 1.0 hour(s)" and "under the 1 hour" in one breath."""
    short = doctor.check_disk(environment(takes_free_bytes=ONE_HOUR - 10_000_000))
    assert "about 59 minutes" in short.message
    assert "1.0" not in short.message


def test_the_stem_count_comes_from_the_largest_rig_not_a_guess(frozen_root):
    """Rigs will have one, two or more channels. guitar_vocal has two, each
    recorded dry and processed. Read off the rigs of channels, the frozen
    ones here (rig_fixtures.py), since the v1 configs were retired (#218)."""
    assert doctor._largest_rig(frozen_root) == (4, "guitar_vocal")
    eight = doctor.check_disk(environment(take_stems=8, takes_free_bytes=ONE_HOUR))
    assert eight.state == FAULT, "twice the stems, half the time"
    assert "8-stem take of guitar_vocal" in eight.message


def test_free_space_that_cannot_be_read_is_unchecked():
    assert doctor.check_disk(environment(takes_free_bytes=None)).state == UNCHECKED


def test_free_space_is_read_where_the_takes_will_go_before_the_folder_exists(tmp_path):
    """The folder is made by the first take, so until then its parent says."""
    free, problem = doctor.free_bytes(tmp_path / "not" / "made" / "yet")
    assert isinstance(free, int) and problem == ""


def test_a_takes_folder_linked_to_an_unplugged_drive_is_a_fault(tmp_path):
    """It used to walk past the dead link to the home disk, report plenty of
    room, and let the first take fail as it started."""
    takes = tmp_path / "sushi-rig-takes"
    takes.symlink_to(tmp_path / "media" / "external")
    free, problem = doctor.free_bytes(takes / "riff")
    assert free is None and "plugged in" in problem
    check = doctor.check_disk(environment(takes_free_bytes=None, takes_problem=problem))
    assert check.state == FAULT and "connect the drive" in check.remedy


@pytest.mark.committed_rigs
def test_the_committed_amp_models_are_trained_at_the_rigs_rate():
    """Real files: both Neve models declare 48 kHz. A superset, so committing a
    third good model does not break the test of the property it shares."""
    models = doctor._models(ROOT)
    assert {"Neve 1.nam", "Neve 2.nam"} <= {m.name for m in models}
    assert doctor.check_models(environment(models=models)).state == OK


def test_a_model_a_config_plays_is_checked_wherever_it_lives(tmp_path):
    """A rig's amp model can point outside amp/models. Checking the library
    alone passed a config whose model was never looked at. A rig of channels,
    since the v1 configs, where this was first found, were retired (#218)."""
    import json

    (tmp_path / "config" / "rigs").mkdir(parents=True)
    elsewhere = tmp_path / "elsewhere" / "vintage.nam"
    elsewhere.parent.mkdir()
    elsewhere.write_text(json.dumps({"architecture": "WaveNet", "sample_rate": 44100}))
    (tmp_path / "config" / "rigs" / "tone.yaml").write_text(
        "kind: rig\nschema_version: 1\nname: tone\n"
        f"amp: {{plugin: nam, model: {elsewhere}}}\n"
        "channels:\n- {name: guitar, input: guitar-in, width: mono, chain: []}\n"
        "master: {output: monitor-out, chain: []}\n"
    )
    models = doctor._models(tmp_path)
    assert models == (doctor.ModelInfo("vintage.nam", 44100, "", ("tone",)),)
    check = doctor.check_models(environment(models=models))
    assert check.state == FAULT and "played by tone" in check.message


def test_a_malformed_model_costs_one_line_not_the_run(tmp_path):
    """describe_model can raise more than AmpError on a malformed file; any of
    it used to escape and abort doctor with every other result lost."""
    import json

    bad = tmp_path / "odd.nam"
    bad.write_text(json.dumps({"architecture": "WaveNet", "config": []}))
    info = doctor._describe(bad, ())
    assert info.rate is None and info.error.startswith("AttributeError")
    assert doctor.check_models(environment(models=(info,))).state == FAULT


def test_every_kind_of_model_problem_is_reported_in_one_run():
    """A fault that named only the first kind left the others for the next run."""
    check = doctor.check_models(environment(models=(
        doctor.ModelInfo("old.nam", 44100), doctor.ModelInfo("plain.nam", None),
    )))
    assert check.state == FAULT
    assert "old.nam was trained at 44100 Hz" in check.message
    assert "plain.nam declares no sample rate" in check.message


def test_an_unreadable_model_is_not_reported_as_a_rate_problem():
    check = doctor.check_models(environment(models=(
        doctor.ModelInfo("broken.nam", None, "not valid JSON"),
    )))
    assert check.state == FAULT and "broken.nam cannot be read" in check.message
    assert "48000" not in check.message + check.remedy


def test_a_model_declaring_no_rate_is_unchecked_rather_than_assumed():
    check = doctor.check_models(environment(models=(doctor.ModelInfo("plain.nam", None),)))
    assert check.state == UNCHECKED and "plain.nam" in check.message


def test_no_models_at_all_cannot_mismatch():
    assert doctor.check_models(environment(models=())).state == OK


@pytest.mark.parametrize("body", [
    "kind: rig\nchannels: null\n", "kind: rig\nchannels: 5\n", "kind: rig\nchannels: {a: 1}\n",
    "[channels]\n", "", "{not yaml\n",
])
def test_one_odd_config_does_not_take_the_doctor_down(tmp_path, body):
    """`"tracks": null` parsed, and raised TypeError out of the comprehension
    with nothing above `gather_environment` to catch it. The rigs of channels
    now (the v1 configs were retired, #218): an odd rig file is passed over."""
    (tmp_path / "config" / "rigs").mkdir(parents=True)
    (tmp_path / "config" / "rigs" / "odd.yaml").write_text(body)
    channel = "- {{name: {}, input: guitar-in, width: mono, chain: []}}\n"
    (tmp_path / "config" / "rigs" / "tone.yaml").write_text(
        "kind: rig\nschema_version: 1\nname: tone\nchannels:\n"
        + "".join(channel.format(n) for n in ("a", "b", "c"))
        + "master: {output: monitor-out, chain: []}\n")
    assert doctor._largest_rig(tmp_path) == (3 * doctor.STEMS_PER_CHANNEL, "tone")
    assert doctor._models(tmp_path) == ()


# --------------------------------------------------------------------------- #
# The recorder's ports (2026-11-02: 11 checks passed while no take could start)
# --------------------------------------------------------------------------- #

GUITAR_VOCAL = frozen("guitar_vocal")  # the rig as these tests know it (rig_fixtures.py)
THIS_MACHINE = FROZEN_MACHINE


# The rig's own `pw-dump`, which the plan names each port off as JACK does
# (`transport.jack_port`, the one resolver, #217).
CAPTURED_GRAPH = (Path(__file__).parent / "fixtures" / "pw-dump-rig.json").read_text()


def captured_names():
    from sushi_rig.patchbay import jack_port_names

    return jack_port_names(CAPTURED_GRAPH, "out")


def planned_stems():
    """guitar_vocal's take on this machine, planned as the recorder plans it,
    over the rig's captured graph."""
    from sushi_rig.machine import MachineSpec
    from sushi_rig.rigfile import compile_rig, emit_compiled, load_rig
    from sushi_rig.transport import rig_stems

    rig = load_rig(GUITAR_VOCAL)
    return rig_stems(rig, MachineSpec.load(THIS_MACHINE), emit_compiled(rig, *compile_rig(rig)), captured_names())


def planned_columns():
    from sushi_rig.capture import plan_columns

    return plan_columns(planned_stems())


def jack_listing(drop=(), extra=None):
    """What a JACK client lists on the rig (`jack_fake.JACK_PORTS`: the names
    observed, the aliases a guess), as `capture.jack_ports` reads it, with
    some ports dropped or added: the one thing each test changes."""
    from sushi_rig.capture import JackPort
    from jack_fake import JACK_PORTS

    ports = {name: aliases for name, aliases in JACK_PORTS.items() if name not in drop}
    ports.update(extra or {})
    return [JackPort(name, tuple(aliases)) for name, aliases in ports.items()]


def test_the_recorders_ports_are_found_among_jacks_by_the_names_the_plan_gives():
    """The plan names each port as JACK does (#217), so every one is found by
    its name: no alias is looked up."""
    assert "Audient iD14 Multichannel:capture_AUX0" in {c.source for c in planned_columns()}
    check = doctor.record_ports_check(planned_columns(), jack_listing())
    assert check.state == OK, check.message
    assert "8 column(s)" in check.message


def test_a_dry_tap_jack_has_no_port_for_is_a_fault_naming_it_and_every_port():
    check = doctor.record_ports_check(planned_columns(), jack_listing(drop={"Audient iD14 Multichannel:capture_AUX0"}))
    assert check.state == FAULT
    assert "no JACK port is called 'Audient iD14 Multichannel:capture_AUX0' (for guitar-dry.wav)" in check.message
    assert "Audient iD14 Multichannel:capture_AUX1 (aliases:" in check.message
    assert "jack_lsp -A" in check.remedy


@pytest.mark.parametrize("graph", ["the rig's", "guitar gone", "sushi gone", "two profiles"])
def test_doctor_fails_exactly_when_the_engine_would_refuse_the_take(graph, monkeypatch):
    """One plan through the capture engine (JACK stood in by `jack_fake`) and
    through doctor, on one JACK listing, read the same way (`jack_ports`)."""
    pytest.importorskip("numpy")
    from sushi_rig import capture
    from jack_fake import JACK_PORTS, FakeJack

    ports = dict(JACK_PORTS)
    if graph == "guitar gone":
        del ports["Audient iD14 Multichannel:capture_AUX0"]
    elif graph == "sushi gone":
        del ports["sushi:audio_output_0"]
    elif graph == "two profiles":
        ports["Audient iD14 Pro:capture_AUX1"] = ("alsa:acp:iD14:1:capture:capture_1", "Audient iD14:capture_AUX1")
    jack = FakeJack(ports=ports)
    jack.install(monkeypatch)
    engine = capture.CaptureEngine(48000, 256, settle_s=0.0, link_retry_s=0.0)
    try:
        engine.start(planned_stems(), lambda block: len(block))
        refused = False
        engine.stop()
    except capture.CaptureError:
        refused = True
    assert (doctor.record_ports_check(planned_columns(), jack.listed()).state == FAULT) is refused
    # A second profile carrying the alias is no refusal at this step any more:
    # the plan named the port as JACK does, and that port is there. Two live
    # nodes answering to one alias are the plan's to refuse, off the graph
    # (`transport.jack_port`, #217; test_record_session.py).
    assert refused is (graph in ("guitar gone", "sushi gone"))


def running_rig(tmp_path, **state):
    """A state file of guitar_vocal up on this machine, and `describe_rig`'s
    answer for it, without a rig: the files the recorder plans from are real."""
    from sushi_rig.rigfile import compile_rig, emit_compiled, load_rig

    rig = load_rig(GUITAR_VOCAL)
    config = tmp_path / "guitar_vocal.json"
    config.write_text(json.dumps(emit_compiled(rig, *compile_rig(rig))))
    recorded = {"config_name": "guitar_vocal", "config": str(config), "rig": str(GUITAR_VOCAL),
                "machine": str(THIS_MACHINE), "pgid": 8301, "started_at": "2026-11-02T20:30:00+00:00", **state}
    return recorded, {"running": True, "children": {}}


def test_the_record_check_plans_the_running_rig_from_its_state_file(tmp_path, monkeypatch):
    from sushi_rig import transport

    monkeypatch.setattr(transport, "_live_jack_names", captured_names)
    state, described = running_rig(tmp_path)
    check = doctor.record_check(ROOT, described, state, list_ports=jack_listing)
    assert check.state == OK and "8 column(s)" in check.message, check.message


@pytest.mark.parametrize("case, said", [
    ("down", "no rig is running"),
    ("v1", "was started from a v1 config"),
])
def test_the_record_check_is_unchecked_where_there_is_nothing_to_ask(tmp_path, case, said):
    state, described = running_rig(tmp_path)
    if case == "down":
        described = {"running": False}
    else:
        state["rig"] = None

    def never():
        raise AssertionError("JACK is not asked when there is no take to plan")

    check = doctor.record_check(ROOT, described, state, list_ports=never)
    assert check.state == UNCHECKED and said in check.message


def test_where_no_jack_client_can_be_opened_the_record_check_needs_jack_not_green(tmp_path):
    from sushi_rig.capture import JackUnreachable

    def unreachable():
        raise JackUnreachable(["pw-jack is not installed, so no JACK client can be opened here"])

    state, described = running_rig(tmp_path)
    check = doctor.record_check(ROOT, described, state, list_ports=unreachable)
    assert check.state == UNCHECKED
    assert check.message.startswith("needs JACK:") and "pw-jack is not installed" in check.message


def test_where_the_live_graph_cannot_be_read_the_record_check_needs_pipewire_not_a_fault(tmp_path, monkeypatch):
    """The take's ports are named off `pw-dump` (#217), which raises when it
    cannot run. That is a check doctor could not make here, as for JACK
    above, not a fault of the rig's: UNCHECKED, saying why, and JACK is not
    asked, since there is no plan to look for."""
    from sushi_rig import patchbay, transport

    def no_dump(*a, **k):
        raise patchbay.PatchbayError(["pw-dump is not installed"])

    def never():
        raise AssertionError("JACK is not asked when no take could be planned")

    monkeypatch.setattr(transport, "_live_jack_names", read_live_jack_names)
    monkeypatch.setattr(patchbay, "read_pw_dump", no_dump)
    state, described = running_rig(tmp_path)
    check = doctor.record_check(ROOT, described, state, list_ports=never)
    assert check.state == UNCHECKED, check
    assert check.message.startswith("needs PipeWire:") and "pw-dump is not installed" in check.message


def test_the_jack_listing_runs_under_pw_jack_and_says_why_when_it_cannot(monkeypatch):
    """The listing runs in a child under `pw-jack` (doctor is not under it),
    and every way that fails is `JackUnreachable`, not a traceback."""
    import shutil
    import subprocess
    from types import SimpleNamespace

    from sushi_rig import capture

    monkeypatch.setattr(shutil, "which", lambda name: None)
    with pytest.raises(capture.JackUnreachable, match="pw-jack is not installed"):
        capture.list_jack_ports()
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/pw-jack")
    ran = []
    answers = iter([
        SimpleNamespace(returncode=1, stdout="", stderr="no JACK or PipeWire server to connect to"),
        SimpleNamespace(returncode=0, stdout='[{"name": "a:b", "aliases": ["c:d"]}]', stderr=""),
    ])
    monkeypatch.setattr(subprocess, "run", lambda command, **k: ran.append(command) or next(answers))
    with pytest.raises(capture.JackUnreachable, match="no JACK or PipeWire server"):
        capture.list_jack_ports()
    assert capture.list_jack_ports() == [capture.JackPort("a:b", ("c:d",))]
    assert ran[0][0] == "/usr/bin/pw-jack" and ran[0][-2:] == ["-m", "sushi_rig.capture"]


def test_a_run_the_recorder_cannot_plan_is_a_fault_in_its_words(tmp_path):
    state, described = running_rig(tmp_path, config=str(tmp_path / "gone.json"))
    check = doctor.record_check(ROOT, described, state, list_ports=jack_listing)
    assert check.state == FAULT and "gone.json, could not be read" in check.message


def test_an_unexpected_error_planning_the_take_is_that_checks_fault_not_doctors_end(tmp_path, monkeypatch):
    """The recorder catches anything planning a take (`record_session`); doctor
    stopped at it with a traceback, every check after it lost."""
    from sushi_rig import transport

    def broken(*a, **k):
        raise KeyError("tracks")

    monkeypatch.setattr(transport, "plan_for_run", broken)
    state, described = running_rig(tmp_path)
    check = doctor.record_check(ROOT, described, state, list_ports=jack_listing)
    assert check.state == FAULT and "failed unexpectedly: KeyError: 'tracks'" in check.message


@pytest.mark.committed_rigs  # doctor reads every rig of config/rigs/ (#218)
def test_run_reports_the_recorders_ports(tmp_path, monkeypatch):
    """The whole examination, with the graph, the rig and JACK stood in: the
    check is there, between the patchbay and the machine around it."""
    from sushi_rig import patchbay, rig as rig_module, state as state_module

    state, described = running_rig(tmp_path)
    monkeypatch.setattr(patchbay, "read_pw_dump", lambda *a, **k: CAPTURED)
    monkeypatch.setattr(state_module, "read_state", lambda: state)
    monkeypatch.setattr(rig_module, "describe_rig", lambda recorded=None: described)
    checks = doctor.run(ROOT, machine_file=THIS_MACHINE,
                        list_ports=lambda: jack_listing(drop={"Audient iD14 Multichannel:capture_AUX0"}))
    record = next(c for c in checks if c.name == "record-ports")
    assert record.state == FAULT and "guitar-dry.wav" in record.message
    assert [c.name for c in checks].index("record-ports") > [c.name for c in checks].index("machine-file")


# --------------------------------------------------------------------------- #
# A node of the rig's own, absent while the rig is up (2026-11-02)
# --------------------------------------------------------------------------- #

FMIT_LINKS = {"PipeWire ALSA [fmit]/ALSA Capture [fmit]": ["a"]}
# The state file of the run that is up: which run a crash record must name.
RUN = {"pgid": 8301, "started_at": "2026-11-02T20:30:00+00:00"}


def fmit_crash(**run):
    """The supervisor's record of the tuner killed on the rig, of the run `run`
    (the running one by default)."""
    from sushi_rig.supervisor import exit_record

    return exit_record("fmit", 8302, -15, at="2026-11-02T21:08:22+00:00", phase="running",
                       config_name="electric-chorus-2", log=None, **{**RUN, **run})


def test_an_absent_tuner_with_the_rig_down_is_the_rig_being_down():
    checks = doctor.patchbay_checks({}, FMIT_LINKS, file_name="rig.qpwgraph", links=20,
                                    rig={"running": False})
    assert "while the rig is down" in checks[0].message and "sushi-rig up" in checks[0].remedy


def test_a_tuner_the_agent_saw_killed_is_said_as_a_crash_not_a_rig_that_is_down():
    """What doctor said on the rig after `kill` of fmit: "while the rig is down
    -> run `sushi-rig up`", of a rig that was up."""
    checks = doctor.patchbay_checks({}, FMIT_LINKS, file_name="rig.qpwgraph", links=20,
                                    rig={"running": True, "children": {"fmit": {"pid": 8302, "alive": False}}},
                                    crashed=[fmit_crash()], state=RUN)
    check = checks[0]
    assert check.state == UNCHECKED
    assert "the rig is up" in check.message and "rig is down" not in check.message
    assert "the tuner (fmit) was killed by SIGTERM at 2026-11-02T21:08:22+00:00" in check.message
    assert "sushi-rig restart" in check.remedy and "sushi-rig up" not in check.remedy


def test_without_an_agent_a_dead_child_is_read_from_the_state_files_pid():
    checks = doctor.patchbay_checks({}, FMIT_LINKS, file_name="rig.qpwgraph", links=20,
                                    rig={"running": True, "children": {"fmit": {"pid": 8302, "alive": False}}})
    assert "the tuner (fmit) (pid 8302) is no longer running" in checks[0].message
    assert "sushi-rig restart" in checks[0].remedy


def test_a_rig_started_without_the_tuner_is_said_so_with_nothing_to_fix():
    checks = doctor.patchbay_checks({}, FMIT_LINKS, file_name="rig.qpwgraph", links=20,
                                    rig={"running": True, "children": {"sushi": {"pid": 9, "alive": True}}})
    assert "the rig was started without the tuner (fmit)" in checks[0].message
    assert "restart" not in checks[0].remedy


def test_a_crash_of_an_earlier_run_is_not_told_as_the_running_rigs():
    """The agent keeps crash records after their rig is gone. A tuner that died
    in an earlier run said nothing about this one, whose tuner is alive here
    by its pid, and must not be blamed on it."""
    earlier = fmit_crash(pgid=7001, started_at="2026-11-02T19:00:00+00:00")
    checks = doctor.patchbay_checks({}, FMIT_LINKS, file_name="rig.qpwgraph", links=20,
                                    rig={"running": True, "children": {"fmit": {"pid": 9302, "alive": True}}},
                                    crashed=[earlier], state=RUN)
    assert "the agent saw it" not in checks[0].message
    assert "the tuner (fmit) (pid 9302) is running but has no node in the graph" in checks[0].message
    same_group_new_start = fmit_crash(started_at="2026-11-02T22:00:00+00:00")
    assert "the agent saw it" not in doctor.patchbay_checks(
        {}, FMIT_LINKS, file_name="p", links=1, rig={"running": True, "children": {}},
        crashed=[same_group_new_start], state=RUN)[0].message


def test_doctor_says_a_child_in_the_supervisors_own_words():
    """One copy of what a child is and how it ended: `supervisor.CHILD_ROLES`
    and `how_it_ended`, which the crash line and the web UI use too."""
    from sushi_rig.supervisor import child_label, crash_message, how_it_ended

    record = fmit_crash()
    message = doctor.patchbay_checks({}, FMIT_LINKS, file_name="p", links=1,
                                     rig={"running": True, "children": {}}, crashed=[record], state=RUN)[0].message
    assert f"{child_label('fmit')} {how_it_ended(record)}" in message
    assert how_it_ended(record) in crash_message(record)
    assert record["label"] == child_label("fmit") and record["means"] == "sound is unaffected; there is no tuner"


# --------------------------------------------------------------------------- #
# pipewire-pulse's priority, for the visualiser (#176)
# --------------------------------------------------------------------------- #

def test_a_realtime_pipewire_pulse_is_said_with_its_thread():
    check = doctor.check_pulse_realtime(environment())
    assert (check.state, check.remedy) == (OK, "")
    assert "pw-data-loop (SCHED_FIFO 83)" in check.message


def test_a_pipewire_pulse_without_realtime_is_said_with_what_to_do_and_never_a_fault():
    check = doctor.check_pulse_realtime(environment(pulse_threads={7: (("pipewire-pulse", 0, 0), ("pw-data-loop", 0, 0))}))
    assert check.state == OK, "the rig itself does not need it"
    assert "visualiser" in check.message and "rtkit" in check.remedy and "restart" in check.remedy


@pytest.mark.parametrize("threads, said", [(None, "could not look"), ({}, "not running")])
def test_a_pipewire_pulse_not_seen_is_unchecked(threads, said):
    check = doctor.check_pulse_realtime(environment(pulse_threads=threads))
    assert check.state == UNCHECKED and said in check.message


def test_pipewire_pulses_threads_are_read_from_proc(monkeypatch, tmp_path):
    """The parse, against stat lines shaped as the kernel writes them (a name
    with a space and parentheses in it, as a thread's can have)."""
    import subprocess as sp

    rest = " ".join(["S"] + ["0"] * 36 + ["20", "1"] + ["0"] * 10)  # fields 3 to 39, then rt_priority (40), policy (41)
    task = tmp_path / "proc" / "99" / "task"
    for tid, name in ((99, "pipewire-pulse"), (100, "data (loop) 0")):
        (task / str(tid)).mkdir(parents=True)
        (task / str(tid) / "stat").write_text(f"{tid} ({name}) {rest}\n")
    real = Path
    monkeypatch.setattr("sushi_rig.procs.Path", lambda p: real(str(p).replace("/proc", str(tmp_path / "proc"), 1)))
    monkeypatch.setattr(doctor.subprocess, "run", lambda *a, **k: sp.CompletedProcess(a, 0, "99\n", ""))
    assert doctor._pulse_threads() == {99: (("pipewire-pulse", 1, 20), ("data (loop) 0", 1, 20))}


# --------------------------------------------------------------------------- #
# Playing with the lid closed (#181): what the lid does, Wi-Fi power saving.
# Never a fault; what logind and iw answer is written out from their
# documentation, not captured on a real laptop.
# --------------------------------------------------------------------------- #

def test_a_lid_that_suspends_is_said_with_where_to_read_how_to_keep_playing_never_a_fault():
    check = doctor.check_lid(environment(lid_actions=(("HandleLidSwitch", "suspend"),
                                                      ("HandleLidSwitchExternalPower", "suspend"))))
    assert check.state == OK
    assert "closing the lid suspends this machine, which stops the rig" in check.message
    assert "HandleLidSwitch=suspend" in check.message
    assert "playing-out.md" in check.remedy and "With the lid closed" in check.remedy


def test_a_lid_set_to_ignore_leaves_the_rig_playing_and_an_unset_external_power_follows_the_lid():
    check = doctor.check_lid(environment(lid_actions=(("HandleLidSwitch", "ignore"),
                                                      ("HandleLidSwitchExternalPower", "ignore"))))
    assert (check.state, check.remedy) == (OK, "")
    assert "leaves the rig playing" in check.message
    # On mains power only: still stops it when the laptop is plugged in.
    plugged = doctor.check_lid(environment(lid_actions=(("HandleLidSwitch", "ignore"),
                                                        ("HandleLidSwitchExternalPower", "suspend"))))
    assert "stops the rig" in plugged.message
    unset = doctor.check_lid(environment(lid_actions=(("HandleLidSwitch", "suspend"),
                                                      ("HandleLidSwitchExternalPower", ""))))
    assert "HandleLidSwitchExternalPower=suspend" in unset.message


def test_a_logind_that_cannot_be_asked_is_unchecked_with_the_command_to_ask_it():
    check = doctor.check_lid(environment(lid_actions=None))
    assert check.state == UNCHECKED
    assert "busctl get-property org.freedesktop.login1" in check.remedy


def test_busctl_and_iw_are_read_as_they_print():
    assert doctor.busctl_string('s "suspend"\n') == "suspend"
    assert doctor.busctl_string('s ""') == ""
    assert doctor.busctl_string("b true") is None
    assert doctor.power_save_from("Power save: on\n") is True
    assert doctor.power_save_from("Power save: off\n") is False
    assert doctor.power_save_from("command failed: No such device (-19)\n") is None


def test_wifi_power_saving_on_is_said_with_the_command_that_turns_it_off():
    check = doctor.check_wifi_power_save(environment(wifi_power_save=(("wlp0s20f3", True),)))
    assert check.state == OK and "on for wlp0s20f3" in check.message
    assert "sudo iw dev wlp0s20f3 set power_save off" in check.remedy
    off = doctor.check_wifi_power_save(environment(wifi_power_save=(("wlp0s20f3", False),)))
    assert (off.state, off.remedy) == (OK, "") and "off" in off.message
    assert doctor.check_wifi_power_save(environment(wifi_power_save=())).message == "no Wi-Fi interface"
    assert doctor.check_wifi_power_save(environment(wifi_power_save=None)).state == UNCHECKED
    assert doctor.check_wifi_power_save(environment(wifi_power_save=(("wlan0", None),))).state == UNCHECKED


def test_wifi_is_found_by_sys_and_no_iw_is_not_said_as_power_saving_off(tmp_path, monkeypatch):
    (tmp_path / "eth0").mkdir()
    assert doctor._wifi_power_save(tmp_path) == ()
    (tmp_path / "wlp0s20f3" / "wireless").mkdir(parents=True)
    monkeypatch.setattr(doctor.shutil, "which", lambda name: None)
    assert doctor._wifi_power_save(tmp_path) is None


def test_the_lid_and_wifi_checks_are_run_with_the_rest():
    names = [c.name for c in doctor.environment_checks(environment())]
    assert names[-2:] == ["lid", "wifi-power-save"]


def test_a_desktop_holding_the_lid_switch_is_said_to_decide_instead_of_logind():
    """Review of PR #185: KDE's PowerDevil (and GNOME's gsd-power under some
    setups) takes a `handle-lid-switch` block inhibitor and applies its own
    setting, so logind's HandleLidSwitch is not what happens."""
    check = doctor.check_lid(environment(lid_actions=(("HandleLidSwitch", "ignore"),
                                                      ("HandleLidSwitchExternalPower", "ignore")),
                                         lid_inhibitors=("PowerDevil",)))
    assert check.state == OK
    assert check.message.startswith("PowerDevil holds the lid switch (a handle-lid-switch inhibitor), so the "
                                    "desktop's own power settings decide")
    assert "leaves the rig playing" not in check.message
    assert "Power Management" in check.remedy and "GNOME" in check.remedy and "playing-out.md" in check.remedy


def test_who_holds_the_lid_is_read_from_list_inhibitors_block_mode_only():
    # As `busctl call ... ListInhibitors` prints it: written from busctl's
    # documented format, not captured on a laptop.
    listed = ('a(ssssuu) 3 "shutdown:sleep" "NetworkManager" "NetworkManager needs to turn off networks" '
              '"delay" 0 812 "handle-power-key:handle-suspend-key:handle-hibernate-key:handle-lid-switch" '
              '"PowerDevil" "KDE handles power events" "block" 1000 1650 "handle-lid-switch" "Odd" "x" "delay" 0 1')
    assert doctor.lid_inhibitors_from(listed) == ("PowerDevil",)
    assert doctor.lid_inhibitors_from("a(ssssuu) 0") == ()


# --------------------------------------------------------------------------- #
# The visualiser's tap (#195): the agent's, on demand, and realtime or
# nothing; a fault when it would not be realtime.
# --------------------------------------------------------------------------- #

TAP_RIG = {"running": True, "children": {"sushi": {"pid": 10, "alive": True}}}
TAP_STATE = {"visualiser_tap_requested": True}


def tap_report(**over):
    report = {"offered": True, "running": False, "pid": None, "pages": 0, "stops_in": None,
              "refused": None, "stopped": None, "agent_pid": 99}
    report.update(over)
    return report


def test_a_running_tap_with_a_realtime_thread_is_ok_and_one_without_is_a_fault():
    running = tap_report(running=True, pid=11, pages=1)
    ok = doctor.tap_check(TAP_RIG, TAP_STATE, running, (("pw-loopback", 0, 0), ("pw-data-loop", 1, 83)))
    assert (ok.name, ok.state) == ("visualiser-tap", OK)
    assert "pid 11" in ok.message and "pw-data-loop (SCHED_FIFO 83)" in ok.message and "1 page(s)" in ok.message
    bad = doctor.tap_check(TAP_RIG, TAP_STATE, running, (("pw-loopback", 0, 0),))
    assert bad.state == FAULT and "a cycle the graph did not finish" in bad.message and "rtkit" in bad.remedy


def test_a_tap_refused_for_want_of_realtime_or_one_that_would_be_is_a_fault_with_the_fix():
    refused = tap_report(refused={"code": "not_realtime", "reason": "...", "at": "t"})
    check = doctor.tap_check(TAP_RIG, TAP_STATE, refused, None, (True, "x"))
    assert check.state == FAULT and "for want of a realtime thread" in check.message
    assert "rtkit" in check.remedy and "limit" in check.remedy
    would = doctor.tap_check(TAP_RIG, TAP_STATE, tap_report(), None,
                             (False, "pid 99's realtime limit is 0 and rtkit-daemon is not running"))
    assert would.state == FAULT and "a tap would be refused" in would.message and "rtkit" in would.remedy


def test_no_tap_running_is_ok_when_realtime_can_be_had_and_said_either_way():
    idle = doctor.tap_check(TAP_RIG, TAP_STATE, tap_report(), None, (True, "rtkit-daemon is running"))
    assert idle.state == OK and "starts one while the page's visualiser listens" in idle.message
    assert "rtkit-daemon is running" in idle.message
    unknown = doctor.tap_check(TAP_RIG, TAP_STATE, tap_report(), None, (None, "could not be read"))
    assert unknown.state == OK and "not known" in unknown.message
    no_agent = doctor.tap_check(TAP_RIG, TAP_STATE, None, None, (True, "x"))
    assert no_agent.state == UNCHECKED and "no agent" in no_agent.message
    linked = doctor.tap_check(TAP_RIG, TAP_STATE, tap_report(refused={"code": "not_linked", "reason": "r"}), None,
                              (True, "x"))
    assert linked.state == OK and "the last was refused (r)" in linked.message


def test_a_rig_that_never_offers_its_output_or_none_running_is_not_a_fault():
    off = doctor.tap_check(TAP_RIG, {"visualiser_tap_requested": False}, tap_report(offered=False), None,
                           (False, "x"))
    assert off.state == OK and "--no-visualiser-tap" in off.message
    down = doctor.tap_check({"running": False}, None, None, None)
    assert down.state == UNCHECKED and "no rig is running" in down.message
    unread = doctor.tap_check(TAP_RIG, TAP_STATE, tap_report(running=True, pid=11), None)
    assert unread.state == UNCHECKED and "ps -L" in unread.remedy


def test_whether_realtime_can_be_had_is_read_from_the_agents_limit_and_rtkit(monkeypatch, tmp_path):
    import subprocess as sp

    limits = tmp_path / "limits"
    real = Path
    monkeypatch.setattr(doctor, "Path", lambda p: limits if str(p).endswith("/limits") else real(p))
    rtkit = {"code": 1}
    monkeypatch.setattr(doctor.subprocess, "run", lambda *a, **k: sp.CompletedProcess(a, rtkit["code"], "", ""))
    limits.write_text("Limit                     Soft Limit           Hard Limit           Units\n"
                      "Max realtime priority     95                   95\n")
    assert doctor._realtime_grant(99) == (True, "pid 99's realtime limit is 95")
    limits.write_text("Max realtime priority     0                    0\n")
    assert doctor._realtime_grant(99)[0] is False
    rtkit["code"] = 0
    assert doctor._realtime_grant(99) == (True, "rtkit-daemon is running")


@pytest.mark.committed_rigs  # doctor reads every rig of config/rigs/ (#218)
def test_the_examination_reads_the_agents_tap_and_its_threads_by_pid(tmp_path, monkeypatch):
    from sushi_rig import patchbay, rig as rig_module, state as state_module

    state = {"supervisor_pid": 5, "pgid": 5, "children": {}, "visualiser_tap_requested": True}
    monkeypatch.setattr(patchbay, "read_pw_dump", lambda *a, **k: CAPTURED)
    monkeypatch.setattr(state_module, "read_state", lambda: state)
    monkeypatch.setattr(rig_module, "describe_rig", lambda recorded=None: TAP_RIG)
    asked = []
    monkeypatch.setattr(doctor, "threads_of", lambda pid: asked.append(pid) or (("pw-data-loop", 1, 83),))
    monkeypatch.setattr(doctor, "record_check", lambda *a, **k: Check("record-ports", UNCHECKED, "stood in"))
    checks = doctor.run(ROOT, machine_file=THIS_MACHINE, tap=tap_report(running=True, pid=11, pages=2))
    tap = next(c for c in checks if c.name == "visualiser-tap")
    assert asked == [11] and tap.state == OK and "2 page(s)" in tap.message


def test_a_tap_left_running_with_no_agent_holding_it_is_a_fault():
    """Its agent died uncleanly: in Sushi's cycle with no lease and no watch."""
    rig = {**TAP_RIG, "visualiser_tap": {"pid": 12, "alive": True}}
    left = doctor.tap_check(rig, TAP_STATE, None, None)
    assert left.state == FAULT and "pid 12" in left.message and "no agent holding it" in left.message
    assert "kill 12" in left.remedy
    held = doctor.tap_check(rig, TAP_STATE, tap_report(running=True, pid=12, pages=1), (("pw-data-loop", 1, 83),))
    assert held.state == OK, "the agent's own, running: no orphan"
    dead = doctor.tap_check({**TAP_RIG, "visualiser_tap": {"pid": 12, "alive": False}}, TAP_STATE, None, None,
                            (True, "x"))
    assert dead.state != FAULT
    owned = doctor.tap_check({**TAP_RIG, "visualiser_tap": {"pid": 12, "alive": True, "owner_alive": True}},
                             TAP_STATE, None, None, (True, "x"))
    assert owned.state != FAULT, "another agent's, which still runs: not left behind"
