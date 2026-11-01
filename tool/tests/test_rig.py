"""The rig supervisor's decisions, pinned without starting anything.

Everything here runs with no audio hardware, no Sushi and no processes of the
rig's own. The parts that genuinely need a running rig are verified by hand
against real hardware, and are NOT covered here -- faking them would prove only
that the fakes were called:

- that a killed process group really does take `sushi.bin` and
  open-stage-control's Electron helpers with it;
- that SIGINT is what lets Sushi release its JACK ports cleanly;
- that `up` leaves nothing holding the caller's stdout (the `| grep` hang);
- the real children themselves: Sushi's gRPC coming up, Carla's OSC replay,
  fmit's wiring and the tuner's PipeWire node.

What is testable offline is the logic that decides *what* to start, *whether*
to start it, *how far* to escalate when stopping and *what to say* -- plus one
test that forks a real supervisor with its children stubbed out, because the
`WARN`/`READY`/`FAIL` pipe protocol and the supervisor's `try` block are
exactly the kind of thing a mock would agree with whatever it was told.

Processes and signals are stood in for at the edges (`pid_alive`, `pgid_alive`,
`os.killpg`, `time`, `_fork_supervisor`); the state file is real, in a
temporary `XDG_RUNTIME_DIR`.
"""

from __future__ import annotations

import json
import os
import signal
from types import SimpleNamespace

import pytest

from sushi_rig.paths import checkout_root
from sushi_rig.procs import SHUTDOWN_STEPS
from sushi_rig.rig import (
    DEFAULT_CONFIG_NAME,
    QUANTUM,
    child_environment,
    format_uptime,
    plan_children,
    resolve_rig_yaml,
    rig_source_problem,
)
from sushi_rig import procs, rig, state as state_file
from sushi_rig.state import liveness
from pathlib import Path


# --- which rig.yaml a config came from --------------------------------------


V1_SOURCE = """\
meta: {name: t, version: "1"}
host: {samplerate: 48000}
tracks:
  - name: board
    channels: 2
    inputs: [{engine_bus: 0, track_bus: 0}]
    outputs: [{engine_bus: 0, track_bus: 0}]
    plugins:
      - {name: comp, type: lv2, uri: "urn:comp"}
"""

V2_SOURCE = """\
kind: rig
schema_version: 1
name: newer
channels:
  - {name: board, input: guitar-in, width: mono, chain: [{name: comp, uri: "urn:comp"}]}
master: {output: monitor-out}
"""

BOARD_CONFIG = {"tracks": [{"name": "board", "plugins": [{"name": "comp"}]}]}


def _root(tmp_path):
    (tmp_path / "config" / "src").mkdir(parents=True)
    (tmp_path / "config" / "rigs").mkdir()
    return tmp_path


def test_a_saved_variant_is_attributed_to_the_source_it_names(tmp_path):
    """A variant records the yaml it was built from, because variants share a
    hand-authored source rather than getting one of their own."""
    root = _root(tmp_path)
    (root / "config" / "src" / "acoustic.yaml").write_text(V1_SOURCE)
    config = root / "acoustic_chorus.json"
    config.write_text(json.dumps({**BOARD_CONFIG, "_meta": {"source": "config/src/acoustic.yaml"}}))
    assert resolve_rig_yaml(config, root) == "config/src/acoustic.yaml"


def test_a_hand_authored_config_is_sourced_from_the_file_of_its_own_name(tmp_path):
    """The canonical configs carry no _meta.source; their source is the yaml of
    the same name under config/src/, or config/rigs/ once they are v2 rigs."""
    root = _root(tmp_path)
    (root / "config" / "src" / "electric_board.yaml").write_text(V1_SOURCE)
    (root / "config" / "rigs" / "newer.yaml").write_text(V2_SOURCE)
    for name in ("electric_board", "newer"):
        (root / f"{name}.json").write_text(json.dumps(BOARD_CONFIG))
    assert resolve_rig_yaml(root / "electric_board.json", root) == "config/src/electric_board.yaml"
    assert resolve_rig_yaml(root / "newer.json", root) == "config/rigs/newer.yaml"


def test_a_source_that_does_not_describe_the_config_is_passed_over(tmp_path):
    """The converted v2 rigs carry their v1 variant's provenance, so a config
    emitted from one names electric_board's yaml as its source. A source is
    taken only if every track and plugin the config names is in it; otherwise
    SAVE would be refused on every press, or worse."""
    root = _root(tmp_path)
    (root / "config" / "src" / "electric_board.yaml").write_text(V1_SOURCE)
    (root / "config" / "rigs" / "accustomed.yaml").write_text(V2_SOURCE.replace("newer", "accustomed"))
    config = root / "accustomed.json"
    config.write_text(json.dumps({
        "_meta": {"source": "config/src/electric_board.yaml"},
        "tracks": [{"name": "board", "plugins": [{"name": "comp"}, {"name": "board_send"}]},
                   {"name": "master", "plugins": [{"name": "master_return"}]}],
    }))
    assert resolve_rig_yaml(config, root) == "config/rigs/accustomed.yaml"
    assert rig_source_problem(config, root) is None


def test_a_config_with_no_source_anywhere_gets_none_and_a_reason(tmp_path):
    """It used to fall back to electric_board's yaml, so SAVE on guitar_vocal
    wrote electric_board's chain with no settings and said saved."""
    root = _root(tmp_path)
    (root / "config" / "src" / "orphan.yaml").write_text(V1_SOURCE)
    config = root / "orphan.json"
    config.write_text(json.dumps({"tracks": [{"name": "vocal", "plugins": [{"name": "vocal_eq"}]}]}))
    assert resolve_rig_yaml(config, root) is None
    problem = rig_source_problem(config, root)
    assert problem.startswith("SAVE is off for orphan")
    assert "config/src/orphan.yaml has no vocal, vocal_eq" in problem
    assert "config/rigs/orphan.yaml does not exist" in problem
    assert resolve_rig_yaml(root / "missing.json", root) is None
    broken = root / "broken.json"
    broken.write_text("{not json")
    assert resolve_rig_yaml(broken, root) is None


def test_every_committed_config_has_a_source_to_save_from():
    """guitar_vocal was the one that fell through to electric_board."""
    from sushi_rig.paths import checkout_root

    root = checkout_root()
    for config in sorted((root / "config").glob("*.json")):
        source = resolve_rig_yaml(config, root)
        assert source is not None, (config.name, rig_source_problem(config, root))
        assert (root / source).is_file()


def test_the_listener_is_started_without_a_rig_when_there_is_none(plan_args):
    root, config, _ = plan_args
    listen = next(c for c in plan_children(root, config, None, headless=False, tuner=True)
                  if c["name"] == "listen")
    assert "--rig" not in listen["command"]
    listen = next(c for c in plan_children(root, config, "config/src/x.yaml", headless=False, tuner=True)
                  if c["name"] == "listen")
    assert listen["command"][listen["command"].index("--rig") + 1] == "config/src/x.yaml"


# --- v2 rigs: up --rig (#30's last slice) ------------------------------------


ROOT = checkout_root()
CAPTURED_DUMP = (Path(__file__).parent / "fixtures" / "pw-dump-rig.json").read_text()
MACHINE = ROOT / "machines" / "sonny-dewfall-XPS-9320.yaml"


def test_a_v2_rig_is_compiled_and_wired_from_its_own_file(tmp_path):
    """The committed patchbay describes the v1 layout: on guitar_vocal it would
    feed the guitar into input 1, the vocal channel under v2, and wire the
    microphone to inputs 2/3, which v2 does not read. Read off the files on
    2026-11-01. So a v2 run wires each channel from the rig and the machine
    file, through the same `patchbay_for` as `patchbay generate`, against a
    real pw-dump capture."""
    from sushi_rig.patchbay import parse_patchbay

    out = rig.prepare_rig("guitar_vocal", ROOT, tmp_path / "run",
                          machine_file=MACHINE, dump=CAPTURED_DUMP)
    assert out["source"] == "config/rigs/guitar_vocal.yaml"
    config = json.loads(out["config"].read_text())
    assert [t["name"] for t in config["tracks"]] == ["guitar", "vocal", "master"]
    assert config["_meta"]["source"] == "config/rigs/guitar_vocal.yaml"
    links = {(l.out_port, l.in_port) for l in parse_patchbay(out["patchbay"].read_text())}
    sushi_in = {o: i for o, i in links if i.startswith("sushi:audio_input")}
    assert sushi_in == {
        "Audient iD14:capture_AUX0": "sushi:audio_input_0",
        "Audient iD14:capture_AUX1": "sushi:audio_input_1",
    }, "one link per mono source: the widening happens inside Sushi, not in the patchbay"
    assert ("sushi:audio_output_0", "Audient iD14:playback_FL") in links
    assert out["warnings"] == []


def test_a_rig_that_does_not_exist_is_refused_by_name(tmp_path):
    with pytest.raises(rig.RigError, match="no such rig"):
        rig.prepare_rig("nope", ROOT, tmp_path, machine_file=MACHINE, dump=CAPTURED_DUMP)


def test_a_rig_without_a_machine_file_is_refused_not_half_wired(tmp_path):
    with pytest.raises(rig.RigError, match="machine file"):
        rig.prepare_rig("guitar_vocal", ROOT, tmp_path,
                        machine_file=tmp_path / "missing.yaml", dump=CAPTURED_DUMP)


def test_up_on_a_v2_rig_uses_its_compiled_config_and_patchbay(up_world, monkeypatch, tmp_path):
    """`up --rig` plans the children on the compiled config and the generated
    patchbay, and tells the supervisor which rig file it came from."""
    compiled = tmp_path / "x.json"
    compiled.write_text(json.dumps({"tracks": [], "_meta": {"source": "config/rigs/x.yaml"}}))
    wired = tmp_path / "x.qpwgraph"
    wired.write_text("<patchbay/>")
    monkeypatch.setattr(rig, "prepare_rig", lambda name, root, runtime, **kw: {
        "config": compiled, "patchbay": wired, "source": "config/rigs/x.yaml", "warnings": ["w"],
    })
    seen = {}

    def plan(root, config, rig_yaml, **kw):
        seen.update(config=config, patchbay=kw.get("patchbay"))
        return []

    monkeypatch.setattr(rig, "plan_children", plan)
    up_world.say("READY", then_write=running_state(config_name="x", rig="config/rigs/x.yaml"))
    assert rig.up("x", headless=True, amp=False, rig=True) == 0
    assert seen == {"config": compiled, "patchbay": wired}


def test_up_on_a_v2_rig_with_an_amp_wires_the_amp_into_the_generated_patchbay(up_world, monkeypatch, tmp_path):
    """The hazard this slice closes: the amp's patchbay used to be rewired from
    the committed file, which would feed guitar_vocal's guitar into the vocal
    channel. For a v2 run it is rewired from the generated one, and that
    rewired file is what qpwgraph loads."""
    compiled = tmp_path / "x.json"
    compiled.write_text(json.dumps({"tracks": [], "_meta": {"source": "config/rigs/x.yaml"}}))
    wired = tmp_path / "x.qpwgraph"
    wired.write_text("<patchbay/>")
    rewired = tmp_path / "rig-amp.qpwgraph"
    monkeypatch.setattr(rig, "prepare_rig", lambda name, root, runtime, **kw: {
        "config": compiled, "patchbay": wired, "source": "config/rigs/x.yaml", "warnings": [],
    })
    seen = {}

    def fake_prepare_amp(config, root, runtime, override, base=None):
        seen["base"] = base
        return {"model": "amp/models/M.nam", "values": {}, "project": tmp_path / "amp.carxp", "patchbay": rewired}

    monkeypatch.setattr("sushi_rig.amp.prepare", fake_prepare_amp)

    def plan(root, config, rig_yaml, **kw):
        seen["patchbay"] = kw.get("patchbay")
        return []

    monkeypatch.setattr(rig, "plan_children", plan)
    up_world.say("READY", then_write=running_state(config_name="x", rig="config/rigs/x.yaml"))
    assert rig.up("x", headless=True, amp=True, rig=True) == 0
    assert seen == {"base": wired, "patchbay": rewired}


def test_a_v2_runs_artefacts_cannot_collide_with_the_state_file(tmp_path):
    """The state file is runtime/rig.json, so a rig called `rig` compiled into
    the runtime root would overwrite it, or be overwritten by it."""
    from sushi_rig.paths import state_path

    out = rig.prepare_rig("guitar_vocal", ROOT, tmp_path / "run",
                          machine_file=MACHINE, dump=CAPTURED_DUMP)
    assert out["config"].parent == tmp_path / "run" / "rigs"
    assert out["config"].name != state_path().name or out["config"].parent != tmp_path / "run"


def test_the_patchbay_the_supervisor_checks_is_the_one_qpwgraph_loaded(tmp_path):
    plan = [{"name": "qpwgraph", "command": ["qpwgraph", "-a", str(tmp_path / "x.qpwgraph"), "-m"]}]
    assert rig._patchbay_in(plan, ROOT) == tmp_path / "x.qpwgraph"
    assert rig._patchbay_in([{"name": "qpwgraph", "command": ["true"]}], ROOT) == ROOT / "Patchbay" / "rig.qpwgraph"
    assert rig._patchbay_in([], ROOT) == ROOT / "Patchbay" / "rig.qpwgraph"


def test_unmade_links_are_one_loud_problem_and_an_unaskable_graph_is_said(tmp_path, monkeypatch):
    patchbay = tmp_path / "p.qpwgraph"
    patchbay.write_text((ROOT / "Patchbay" / "rig.qpwgraph").read_text())
    monkeypatch.setattr("sushi_rig.patchbay.read_pw_dump", lambda *a, **k: "[]")
    monkeypatch.setattr("sushi_rig.qpwgraph.wait_for_links",
                        lambda wanted, read_names, settle=0: [("Audient iD14:capture_AUX1", "sushi:audio_input_1")])
    running = {"sushi", "fmit", "qpwgraph"}
    (problem,) = rig._unmade_link_problems(patchbay, running)
    assert "1 link(s) in p.qpwgraph were not made: Audient iD14:capture_AUX1 -> sushi:audio_input_1" in problem
    assert "silent while everything looks healthy" in problem
    assert f"save {patchbay}" in problem, "a v1 run is told to fix the file qpwgraph loaded"
    (problem,) = rig._unmade_link_problems(patchbay, running, rig_run=True)
    assert "machine file" in problem, "a rig run's ports come from the machine file"

    def cannot(*a, **k):
        raise OSError("no pw-link")

    monkeypatch.setattr("sushi_rig.qpwgraph.wait_for_links", cannot)
    (problem,) = rig._unmade_link_problems(patchbay, running)
    assert "could not check" in problem and "no pw-link" in problem
    assert rig._unmade_link_problems(tmp_path / "missing.qpwgraph", running)[0].startswith("could not check")


def test_a_link_to_a_child_that_is_not_running_is_not_waited_for(tmp_path, monkeypatch):
    """The committed patchbay carries the tuner's tap. `up --no-tuner` must
    not wait the settle and then warn about a link that was never going to be
    made; a tuner that failed to start is already reported as that."""
    patchbay = tmp_path / "p.qpwgraph"
    patchbay.write_text((ROOT / "Patchbay" / "rig.qpwgraph").read_text())
    monkeypatch.setattr("sushi_rig.patchbay.read_pw_dump", lambda *a, **k: "[]")
    asked = {}

    def record(wanted, read_names, settle=0):
        asked["sinks"] = {link.in_port for link in wanted}
        return []

    monkeypatch.setattr("sushi_rig.qpwgraph.wait_for_links", record)
    assert rig._unmade_link_problems(patchbay, {"sushi", "qpwgraph"}) == []
    assert "ALSA Capture [fmit]:input_MONO" not in asked["sinks"]
    assert "sushi:audio_input_0" in asked["sinks"]
    rig._unmade_link_problems(patchbay, {"sushi", "qpwgraph", "fmit"})
    assert "ALSA Capture [fmit]:input_MONO" in asked["sinks"], "wanted once the tuner runs"


def test_a_running_tuner_the_patchbay_never_taps_is_said(tmp_path, monkeypatch):
    """A patchbay with no tuner link leaves fmit running and deaf, which looks
    exactly like a tuner that cannot hear the guitar."""
    patchbay = tmp_path / "p.qpwgraph"
    text = (ROOT / "Patchbay" / "rig.qpwgraph").read_text()
    from sushi_rig.patchbay import TUNER_PORT, parse_patchbay, render_patchbay
    links = [l for l in parse_patchbay(text) if l.in_port != TUNER_PORT]
    patchbay.write_text(render_patchbay(links))
    monkeypatch.setattr("sushi_rig.patchbay.read_pw_dump", lambda *a, **k: "[]")
    monkeypatch.setattr("sushi_rig.qpwgraph.wait_for_links", lambda wanted, read_names, settle=0: [])
    (problem,) = rig._unmade_link_problems(patchbay, {"sushi", "qpwgraph", "fmit"})
    assert "the tuner is running but p.qpwgraph has no link into" in problem
    assert rig._unmade_link_problems(patchbay, {"sushi", "qpwgraph"}) == []


def test_a_rig_run_with_a_panel_hands_its_file_to_the_listener(up_world, monkeypatch, tmp_path, capsys):
    """SAVE is on for a rig run: the listener gets the rig file, and no
    'SAVE is off' warning is printed."""
    compiled = tmp_path / "x.json"
    compiled.write_text(json.dumps({"tracks": [], "_meta": {"source": "config/rigs/x.yaml"}}))
    wired = tmp_path / "x.qpwgraph"
    wired.write_text("<patchbay/>")
    monkeypatch.setattr(rig, "prepare_rig", lambda name, root, runtime, **kw: {
        "config": compiled, "patchbay": wired, "source": "config/rigs/x.yaml", "warnings": [],
    })
    monkeypatch.setattr(rig, "resolve_rig_yaml", lambda config, root: "config/rigs/x.yaml")
    seen = {}

    def plan(root, config, rig_yaml, **kw):
        seen["rig_yaml"] = rig_yaml
        return []

    monkeypatch.setattr(rig, "plan_children", plan)
    up_world.say("READY", then_write=running_state(config_name="x", rig="config/rigs/x.yaml"))
    assert rig.up("x", headless=False, amp=False, rig=True) == 0
    assert seen["rig_yaml"] == "config/rigs/x.yaml"
    assert "SAVE is off" not in capsys.readouterr().err


def test_restart_brings_back_a_v2_rig_as_a_v2_rig():
    from sushi_rig.rig import restart_arguments

    _, options = restart_arguments({"config_name": "x", "mode": "headless", "rig": "config/rigs/x.yaml"})
    assert options["rig"] is True
    _, options = restart_arguments({"config_name": "x", "mode": "headless", "rig": None})
    assert options["rig"] is False


def test_status_names_the_rig_file_a_v2_run_came_from(world, capsys):
    world()
    state_file.write_state(running_state(rig="config/rigs/guitar_vocal.yaml"))
    assert rig.describe_rig()["rig"] == "config/rigs/guitar_vocal.yaml"
    rig.status()
    assert "rig        config/rigs/guitar_vocal.yaml" in capsys.readouterr().out


# --- second-instance guard --------------------------------------------------


def test_a_running_supervisor_is_running():
    """The guard that was missing: starting a second rig on top of a running one
    is what produced the two-wrapper collision that took a live session down."""
    yes = lambda n: True  # noqa: E731
    assert liveness({"supervisor_pid": 42, "pgid": 42}, yes, yes) == "running"


def test_a_rig_whose_group_is_gone_is_gone():
    """A crash must not leave `up` refusing forever."""
    no = lambda n: False  # noqa: E731
    assert liveness({"supervisor_pid": 42, "pgid": 42}, no, no) == "gone"


def test_no_state_is_not_stale_state():
    """Nothing recorded means nothing to clean up, not a stale file."""
    def never(n):
        pytest.fail("should not be consulted")

    assert liveness(None, never, never) == "absent"
    assert liveness({}, never, never) == "absent"


def test_ids_that_cannot_name_a_rig_are_invalid_without_asking_anyone():
    """The state file is trusted as identity, and these parse but are not one:
    0 is "my own group" to `killpg`, negatives are another call, 1 is init, and
    `true` is an `int` in Python. None may reach a liveness probe, or read as a
    running rig."""
    def never(n):
        pytest.fail("a nonsense id must not be probed")

    for pid, pgid in [(0, 42), (1, 42), (-5, 42), (42, 0), (42, 1), (42, -5),
                      (True, 42), (42, True), ("42", 42), (42, None),
                      (0, 0), (1, 1), (-5, -5)]:
        assert liveness({"supervisor_pid": pid, "pgid": pgid}, never, never) == "invalid", (pid, pgid)


def test_state_without_a_group_or_a_pid_is_invalid():
    def never(n):
        pytest.fail("not asked")

    assert liveness({"supervisor_pid": 42}, never, never) == "invalid"
    assert liveness({"mode": "panel"}, never, never) == "invalid"


def test_a_group_that_is_not_the_supervisors_own_is_invalid():
    """`_supervise` calls setsid, so it always leads a group of its own number.
    A file where the two differ was edited or damaged, and naming some other
    group is exactly how a stale or hostile file would get it signalled."""
    yes = lambda n: True  # noqa: E731
    assert liveness({"supervisor_pid": 10, "pgid": 11}, yes, yes) == "invalid"


def test_a_dead_supervisor_with_a_live_group_is_the_rigs_own_leftovers():
    """A group number cannot be handed to anything else while any member lives,
    and the group is the supervisor's own, so this is what a crash leaves
    behind -- not a stranger. `down` and `restart` exist to recover from it."""
    state = {"supervisor_pid": 10, "pgid": 10}
    yes, no = (lambda n: True), (lambda n: False)
    assert liveness(state, no, yes) == "orphaned"
    assert liveness(state, yes, yes) == "running"
    assert liveness(state, no, no) == "gone"
    assert liveness(state, yes, no) == "gone"


# --- what each persona starts -----------------------------------------------


def _names(children):
    return [c["name"] for c in children]


@pytest.fixture
def plan_args(tmp_path):
    return tmp_path, tmp_path / "config" / "electric_board.json", "config/src/x.yaml"


def test_panel_mode_starts_the_listener_and_sushi(plan_args):
    root, config, rig_yaml = plan_args
    names = _names(plan_children(root, config, rig_yaml, headless=False, tuner=True))
    assert names == ["fmit", "qpwgraph", "listen", "sushi"]


def test_headless_mode_omits_the_save_listener(plan_args):
    """The listener exists to serve the panel's save and reorder buttons. With
    no panel, nothing would ever talk to it."""
    root, config, rig_yaml = plan_args
    names = _names(plan_children(root, config, rig_yaml, headless=True, tuner=True))
    assert "listen" not in names
    assert names == ["fmit", "qpwgraph", "sushi"]


def test_the_tuner_can_be_skipped_in_either_mode(plan_args):
    """A live player wants a tuner, which is why it is not tied to the mode."""
    root, config, rig_yaml = plan_args
    for headless in (True, False):
        names = _names(
            plan_children(root, config, rig_yaml, headless=headless, tuner=False)
        )
        assert "fmit" not in names


def test_sushi_starts_last(plan_args):
    """The patchbay has to be up before Sushi appears, or its ports arrive with
    nothing ready to connect them."""
    root, config, rig_yaml = plan_args
    for headless in (True, False):
        names = _names(
            plan_children(root, config, rig_yaml, headless=headless, tuner=True)
        )
        assert names[-1] == "sushi"
        assert names.index("qpwgraph") < names.index("sushi")


def test_only_the_audio_path_is_mandatory(plan_args):
    """fmit and qpwgraph failing should be reported, not abort the start —
    neither is in the audio path. Sushi and the listener are."""
    root, config, rig_yaml = plan_args
    children = plan_children(root, config, rig_yaml, headless=False, tuner=True)
    optional = {c["name"] for c in children if c["optional"]}
    assert optional == {"fmit", "qpwgraph"}


def test_the_listener_is_pointed_at_the_config_being_run(plan_args):
    """Panel auto-refresh after a reorder regenerates from this config; pointing
    it at the wrong one would rebuild the panel from a rig that isn't running."""
    root, config, rig_yaml = plan_args
    listener = next(
        c for c in plan_children(root, config, rig_yaml, headless=False, tuner=True)
        if c["name"] == "listen"
    )
    command = listener["command"]
    assert command[command.index("--panel-config") + 1] == str(config)
    assert command[command.index("--rig") + 1] == rig_yaml


def test_commands_carry_no_shell_metacharacters(plan_args):
    """Every command is a list passed straight to exec, never a shell string —
    so a config or path containing a space or a quote cannot turn into
    something else on the way."""
    root, config, rig_yaml = plan_args
    for child in plan_children(root, config, rig_yaml, headless=False, tuner=True):
        assert isinstance(child["command"], list)
        assert all(isinstance(part, str) for part in child["command"])


# --- shutdown policy --------------------------------------------------------


def test_shutdown_starts_politely_and_ends_decisively():
    """SIGINT first because that is what lets Sushi unhook its JACK ports
    cleanly; SIGKILL last so nothing can simply decline to stop."""
    signals = [sig for sig, _ in SHUTDOWN_STEPS]
    assert signals[0] == signal.SIGINT
    assert signals[-1] == signal.SIGKILL
    assert signal.SIGTERM in signals


def test_every_shutdown_step_waits_before_escalating():
    """Escalating instantly would defeat the point of asking politely first."""
    assert all(wait > 0 for _, wait in SHUTDOWN_STEPS)


def test_shutdown_is_bounded():
    """A stop that can hang forever is no better than the old one."""
    assert sum(wait for _, wait in SHUTDOWN_STEPS) <= 10


# --- reporting --------------------------------------------------------------


@pytest.mark.parametrize(
    "seconds,expected",
    [(0, "0s"), (45, "45s"), (60, "1m 0s"), (125, "2m 5s"), (3600, "1h 0m"),
     (7830, "2h 10m"), (-5, "0s")],
)
def test_uptime_reads_naturally(seconds, expected):
    assert format_uptime(seconds) == expected


def test_default_config_matches_what_the_old_script_launched():
    """Deleting the scripts must not quietly change which rig comes up."""
    assert DEFAULT_CONFIG_NAME == "electric_board"


def test_the_amp_is_planned_only_when_a_project_exists(plan_args):
    """A config with no `_amp` section describes a rig with no amp, and should
    start one no more than --no-amp does."""
    root, config, rig_yaml = plan_args
    without = _names(plan_children(root, config, rig_yaml, headless=False, tuner=True))
    assert "amp" not in without

    with_amp = _names(plan_children(
        root, config, rig_yaml, headless=False, tuner=True,
        amp_project=root / "amp.carxp",
    ))
    assert "amp" in with_amp


def test_a_missing_carla_does_not_stop_the_rig(plan_args):
    """Optional in the same sense the tuner is: the rig is perfectly playable
    without an amp, so Carla failing should be reported, not fatal."""
    root, config, rig_yaml = plan_args
    amp = next(
        c for c in plan_children(
            root, config, rig_yaml, headless=False, tuner=True,
            amp_project=root / "amp.carxp",
        ) if c["name"] == "amp"
    )
    assert amp["optional"] is True
    assert amp["command"][:3] == ["pw-jack", "carla", "-n"]


def test_the_amp_runs_under_pw_jack(plan_args):
    """Carla links against the system libjack, which finds no server on a
    PipeWire-only machine and exits during startup. The previous rig machine
    had a jackd to fall back on, so this was invisible until a new one didn't
    — the same class of hidden portability break `LV2_PATH` is kept narrow to
    catch."""
    root, config, rig_yaml = plan_args
    amp = next(
        c for c in plan_children(
            root, config, rig_yaml, headless=False, tuner=True,
            amp_project=root / "amp.carxp",
        ) if c["name"] == "amp"
    )
    assert amp["command"][0] == "pw-jack"


def test_the_dsp_children_ask_for_the_rigs_own_quantum(plan_args):
    """Left to the machine this was 2048 — 42.7 ms per block, which is what
    "high latency" turned out to mean. A JACK client locks whatever the graph
    was at when it started, so the rig has to say the number rather than
    inherit it."""
    root, config, rig_yaml = plan_args
    children = {
        c["name"]: c for c in plan_children(
            root, config, rig_yaml, headless=False, tuner=True,
            amp_project=root / "amp.carxp",
        )
    }
    expected = f"{QUANTUM}/48000"
    assert children["sushi"]["env_set"]["PIPEWIRE_QUANTUM"] == expected
    # The same one: two JACK clients locking different quanta is a fight
    # nobody wins.
    assert children["amp"]["env_set"]["PIPEWIRE_QUANTUM"] == expected


def test_the_tuner_is_not_dragged_to_the_audio_quantum(plan_args):
    """It taps the input and does no DSP; it has no business being pinned to
    the audio path's buffer size. It does carry other environment of its own
    (see the autoconnect test below), so this is about the quantum alone."""
    root, config, rig_yaml = plan_args
    fmit = next(
        c for c in plan_children(
            root, config, rig_yaml, headless=False, tuner=True,
        ) if c["name"] == "fmit"
    )
    assert "PIPEWIRE_QUANTUM" not in fmit.get("env_set", {})


def test_the_tuner_does_not_auto_connect(plan_args):
    """`fmit` reaches PipeWire through the ALSA compatibility layer, which
    auto-connects to the default source — a webcam on this machine. Since the
    patchbay also wires it to the interface, the tuner bridged the two into one
    driver group and the whole rig ended up clocked by the webcam. Its routing
    belongs in the patchbay, where every other connection in this rig lives."""
    root, config, rig_yaml = plan_args
    fmit = next(
        c for c in plan_children(
            root, config, rig_yaml, headless=False, tuner=True,
        ) if c["name"] == "fmit"
    )
    assert fmit["env_set"]["PIPEWIRE_PROPS"] == "{ node.autoconnect = false }"


def test_the_amp_starts_after_sushi(plan_args):
    """Nothing depends on the ordering for correctness, but Sushi is the part
    worth getting up first if anything is slow."""
    root, config, rig_yaml = plan_args
    names = _names(plan_children(
        root, config, rig_yaml, headless=False, tuner=True,
        amp_project=root / "amp.carxp",
    ))
    assert names.index("sushi") < names.index("amp")


def test_the_listener_is_told_the_amp_so_saves_can_record_it(plan_args):
    """The save button writes the amp into the config it saves, and the only
    record of the amp's values is what the listener was told — Carla cannot be
    asked."""
    root, config, rig_yaml = plan_args
    listener = next(
        c for c in plan_children(
            root, config, rig_yaml, headless=False, tuner=True,
            amp_project=root / "amp.carxp", amp_model="amp/models/M.nam",
            amp_values={"Input Lvl": 3.0},
        ) if c["name"] == "listen"
    )
    command = listener["command"]
    assert command[command.index("--amp-model") + 1] == "amp/models/M.nam"
    assert "Input Lvl=3.0" in command


def test_headless_has_no_listener_to_tell(plan_args):
    """Headless runs the amp but no panel and no listener, so there is nothing
    to record values for — and nothing that would change them."""
    root, config, rig_yaml = plan_args
    names = _names(plan_children(
        root, config, rig_yaml, headless=True, tuner=True,
        amp_project=root / "amp.carxp", amp_model="amp/models/M.nam",
    ))
    assert "listen" not in names
    assert "amp" in names


# --- restart (principle 9: fail loudly, recover instantly) -------------------


def test_restart_brings_back_the_same_rig():
    """Recovery is the whole answer to a failure here, so it has to return what
    was running — not a default."""
    from sushi_rig.rig import restart_arguments

    name, options = restart_arguments({
        "config_name": "electric-chorus-2", "mode": "panel",
        "tuner_requested": True, "amp_requested": True,
        "children": {"fmit": 1, "sushi": 2, "amp": 3, "open-stage-control": 4},
    })
    assert name == "electric-chorus-2"
    assert options == {
        "headless": False, "tuner": True, "amp": True, "amp_model": None, "rig": False,
    }


def test_restart_preserves_the_choices_the_rig_was_started_with():
    """A rig started --headless --no-amp --no-tuner must not quietly gain a
    panel, an amp and a tuner on the way back."""
    from sushi_rig.rig import restart_arguments

    _, options = restart_arguments({
        "config_name": "x", "mode": "headless",
        "tuner_requested": False, "amp_requested": False,
        "children": {"sushi": 1},
    })
    assert options == {
        "headless": True, "tuner": False, "amp": False, "amp_model": None, "rig": False,
    }


def test_restart_carries_the_amp_model_override():
    """`--amp-model` is the whole reason for naming a model on the command line.
    Dropped on restart, the config's own model came back with nothing said --
    an amp that sounds plausible and is the wrong one."""
    from sushi_rig.rig import restart_arguments

    _, options = restart_arguments({
        "config_name": "x", "mode": "panel", "amp_requested": True,
        "amp_model": "amp/models/Other.nam",
        "amp_model_requested": "amp/models/Other.nam",
        "children": {"sushi": 1, "amp": 2},
    })
    assert options["amp_model"] == "amp/models/Other.nam"


def test_restart_does_not_pin_the_configs_own_model_as_an_override():
    """`amp_model` is what ran, which is the config's own choice unless one was
    named. Restoring that as an override would freeze today's model: edit the
    config afterwards and every restart would still bring back the old one."""
    from sushi_rig.rig import restart_arguments

    _, options = restart_arguments({
        "config_name": "x", "mode": "panel", "amp_requested": True,
        "amp_model": "amp/models/FromConfig.nam", "amp_model_requested": None,
        "children": {"sushi": 1, "amp": 2},
    })
    assert options["amp_model"] is None


def test_an_amp_that_was_asked_for_but_failed_comes_back_asked_for():
    """The amp is optional, so a Carla that died at startup is absent from the
    children. Deciding from the children made that permanent: every later
    restart had no amp, silently, which is the looks-healthy-but-dry failure
    this rig exists to avoid. The request is what is restored, so the next
    restart tries again and the failure is reported again."""
    from sushi_rig.rig import restart_arguments

    _, options = restart_arguments({
        "config_name": "x", "mode": "panel",
        "tuner_requested": True, "amp_requested": True,
        "amp_model": "amp/models/M.nam", "amp_model_requested": "amp/models/M.nam",
        "children": {"sushi": 1, "listen": 3},  # no fmit, no amp: both failed
    })
    assert options["amp"] is True
    assert options["tuner"] is True
    assert options["amp_model"] == "amp/models/M.nam"


def test_a_state_file_from_before_requests_were_recorded_falls_back_to_children():
    """Written by an older `up`, it cannot say what was asked for; the children
    are the best evidence it has, and refusing to restart would be worse."""
    from sushi_rig.rig import restart_arguments

    _, options = restart_arguments({
        "config_name": "x", "mode": "panel", "children": {"sushi": 1, "amp": 2},
    })
    assert options["amp"] is True
    assert options["tuner"] is False


def test_restart_falls_back_to_the_default_config_rather_than_failing():
    """A state file missing its config name is damaged, not a reason to refuse
    to bring the rig back."""
    from sushi_rig.rig import DEFAULT_CONFIG_NAME, restart_arguments

    assert restart_arguments({"children": {}})[0] == DEFAULT_CONFIG_NAME


# --- what environment a child actually gets ---------------------------------


def test_a_stated_value_beats_an_inherited_one():
    """The regression this closes: merging with `setdefault` meant a
    `PIPEWIRE_QUANTUM` already in the environment won, putting the rig back on
    the desktop's 2048-sample buffer with nothing said — undoing the fix it sat
    beside. Stating the number in the repository is the whole point."""
    env = child_environment(
        {"PIPEWIRE_QUANTUM": "2048/48000", "HOME": "/home/x"},
        {"env_set": {"PIPEWIRE_QUANTUM": "256/48000"}},
    )
    assert env["PIPEWIRE_QUANTUM"] == "256/48000"
    assert env["HOME"] == "/home/x"


def test_an_inherited_pipewire_props_cannot_replace_the_tuners():
    """Same bug, worse consequence: a `PIPEWIRE_PROPS` in the environment
    replaced `node.autoconnect = false` outright, so the tuner went back to
    auto-connecting and the webcam went back to clocking the rig."""
    env = child_environment(
        {"PIPEWIRE_PROPS": "{ node.latency = 2048/48000 }"},
        {"env_set": {"PIPEWIRE_PROPS": "{ node.autoconnect = false }"}},
    )
    assert env["PIPEWIRE_PROPS"] == "{ node.autoconnect = false }"


def test_env_unset_still_removes_what_a_child_must_not_see():
    env = child_environment({"LV2_PATH": "/usr/lib/lv2"}, {"env_unset": ["LV2_PATH"]})
    assert "LV2_PATH" not in env


# --- the supervisor's effects, with the world stood in for -------------------
#
# Fakes for what a process table and a clock would say, and a real state file in
# a temporary directory. Nothing is started and nothing is signalled.


def running_state(**over):
    state = {
        "supervisor_pid": 4001, "pgid": 4001, "mode": "panel",
        "config_name": "electric_board", "config": "/x/electric_board.json",
        "started_at": "2026-01-01T00:00:00+00:00",
        "children": {"sushi": 4002, "fmit": 4003}, "log_dir": "/x/logs",
        "amp_model": None, "tuner_requested": True, "amp_requested": False,
    }
    state.update(over)
    return state


class Clock:
    """Time that only moves when something sleeps, so a three-second grace
    period costs nothing and can still be asserted on."""

    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


@pytest.fixture
def clock(monkeypatch):
    clock = Clock()
    # Replaces the module's name, not `time.sleep` itself, so nothing else in
    # the process sees a clock that stands still. The escalation loop lives in
    # procs.stop_group, which is where the clock is read.
    monkeypatch.setattr(procs, "time", SimpleNamespace(
        monotonic=clock.monotonic, sleep=clock.sleep,
    ))
    return clock


class Group:
    """A process group that dies some time after receiving one of `dies_on`."""

    def __init__(self, clock, dies_on=(), delay=0.0, vanishes=False):
        self.clock = clock
        self.dies_on = set(dies_on)
        self.delay = delay
        self.vanishes = vanishes
        self.sent: list[tuple[signal.Signals, float]] = []
        self.dead_at: float | None = None

    def killpg(self, pgid, sig):
        if self.vanishes:
            self.dead_at = self.clock.now
            raise ProcessLookupError
        self.sent.append((sig, self.clock.now))
        if sig in self.dies_on and self.dead_at is None:
            self.dead_at = self.clock.now + self.delay

    def alive(self, pgid):
        return self.dead_at is None or self.clock.now < self.dead_at

    @property
    def signals(self):
        return [sig for sig, _ in self.sent]


@pytest.fixture
def world(runtime, clock, monkeypatch):
    """A live supervisor (pid 4001) owning a group the test can program."""

    def make(*, dies_on=(), delay=0.0, vanishes=False, supervisor_alive=True):
        group = Group(clock, dies_on, delay, vanishes)
        monkeypatch.setattr(rig, "pid_alive", lambda pid: supervisor_alive)
        monkeypatch.setattr(rig, "pgid_alive", group.alive)
        monkeypatch.setattr(procs, "pgid_alive", group.alive)
        monkeypatch.setattr(os, "killpg", group.killpg)
        state_file.write_state(running_state())
        return group

    return make


# down


def test_down_with_nothing_recorded_says_so_and_signals_nothing(runtime, monkeypatch, capsys):
    """Exit status 0, deliberately: stopping a rig that is not running has
    achieved what was asked, and a script that always runs `down` first should
    not have to tell the two apart. (`restart` has its own, non-zero refusal.)"""
    monkeypatch.setattr(os, "killpg", lambda *a: pytest.fail("signalled nothing's group"))
    assert rig.down() == 0
    assert "No rig is running." in capsys.readouterr().out


def test_down_stops_at_the_first_signal_after_which_the_group_is_gone(world, capsys):
    """SIGINT is the polite one, and it is what lets Sushi release its JACK
    ports. Escalating anyway would turn every clean stop into a kill."""
    group = world(dies_on={signal.SIGINT}, delay=1.5)
    assert rig.down() == 0
    assert group.signals == [signal.SIGINT]
    assert state_file.read_state() is None, "a stopped rig leaves no state"
    assert "Rig offline." in capsys.readouterr().out


def test_down_gives_each_signal_its_whole_grace_period_before_the_next(world):
    group = world(dies_on={signal.SIGTERM})
    assert rig.down() == 0
    assert group.signals == [signal.SIGINT, signal.SIGTERM]
    (_, int_at), (_, term_at) = group.sent
    assert term_at - int_at >= dict(SHUTDOWN_STEPS)[signal.SIGINT], (
        "SIGTERM came before SIGINT had its chance"
    )


def test_down_escalates_through_every_step_and_then_reports_a_stuck_group(world, capsys):
    group = world()  # nothing kills it
    assert rig.down() == 1
    assert group.signals == [sig for sig, _ in SHUTDOWN_STEPS]
    assert "survived SIGKILL" in capsys.readouterr().err
    assert state_file.read_state() is not None, "the evidence is kept"


def test_down_force_goes_straight_to_sigkill(world):
    group = world(dies_on={signal.SIGKILL})
    assert rig.down(force=True) == 0
    assert group.signals == [signal.SIGKILL]


def test_down_treats_a_group_that_vanishes_mid_stop_as_stopped(world):
    group = world(vanishes=True)
    assert rig.down() == 0
    assert state_file.read_state() is None


def test_down_clears_a_stale_file_without_signalling(world, monkeypatch, capsys):
    group = world()
    monkeypatch.setattr(rig, "pid_alive", lambda pid: False)
    monkeypatch.setattr(rig, "pgid_alive", lambda pgid: False)
    assert rig.down() == 0
    assert group.sent == []
    assert state_file.read_state() is None
    assert "stale" in capsys.readouterr().out


@pytest.mark.parametrize("pgid", [0, 1, -5, None, "7"])
def test_down_never_signals_a_group_id_that_cannot_be_a_rigs(world, pgid, capsys):
    """`killpg(0, ...)` is the caller's own group: it is the shell running
    `down`. The id comes from a file anyone could have edited, so it is checked
    before it is used, not after it has done damage -- and `--force` is no
    licence to signal something that is not a group at all."""
    group = world()
    state_file.write_state(running_state(pgid=pgid))
    assert rig.down() == 1
    assert rig.down(force=True) == 1
    assert group.sent == []
    assert state_file.read_state() is not None, "the evidence is kept"
    assert "cannot be signalled even with --force" in capsys.readouterr().err


def test_down_refuses_a_group_that_is_not_the_supervisors_own(world, capsys):
    """Refused, said so, file kept: nothing here says the group is the rig's."""
    group = world()
    state_file.write_state(running_state(pgid=4999))
    assert rig.down() == 1
    assert group.sent == []
    assert state_file.read_state() is not None
    err = capsys.readouterr().err
    assert "nothing was signalled" in err and "--force" in err


def test_down_force_signals_an_inconsistent_file_on_the_users_say_so(world, capsys):
    group = world(dies_on={signal.SIGKILL})
    state_file.write_state(running_state(pgid=4999))
    assert rig.down(force=True) == 0
    assert group.signals == [signal.SIGKILL], "no polite signals for a file we do not trust"
    assert "because --force was given" in capsys.readouterr().err
    assert state_file.read_state() is None


def test_down_never_forces_its_own_group(world, monkeypatch, capsys):
    group = world()
    monkeypatch.setattr(os, "getpgrp", lambda: 4999)
    state_file.write_state(running_state(pgid=4999))
    assert rig.down(force=True) == 1
    assert group.sent == []


def test_down_stops_the_leftovers_of_a_crashed_supervisor(world, capsys):
    """The post-crash path `restart` exists for: the supervisor is dead, its
    own group is not, and nothing else can be using that number. Refusing here
    would leave the rig's children running with no way to stop them but a
    hand-written kill."""
    group = world(dies_on={signal.SIGINT}, supervisor_alive=False)
    assert rig.down() == 0
    assert group.signals == [signal.SIGINT]
    assert state_file.read_state() is None
    assert "leftovers" in capsys.readouterr().out


def test_down_reports_a_group_it_may_not_signal_as_a_message(world, monkeypatch, capsys):
    """Someone else's group: `killpg` raises PermissionError. That is a sentence
    for the person at the keyboard, not a traceback."""
    world()

    def refuse(pgid, sig):
        raise PermissionError(1, "Operation not permitted")

    monkeypatch.setattr(os, "killpg", refuse)
    assert rig.down() == 1
    assert "Not permitted to signal process group 4001" in capsys.readouterr().err
    assert state_file.read_state() is not None


# _kill_group_of


@pytest.fixture
def kill_world(clock, monkeypatch):
    def make(group, *, pgid=4001, own_pgid=100):
        monkeypatch.setattr(os, "getpgid", lambda pid: pgid)
        monkeypatch.setattr(os, "getpgrp", lambda: own_pgid)
        monkeypatch.setattr(os, "waitpid", lambda pid, flags: (0, 0))
        monkeypatch.setattr(os, "killpg", group.killpg)
        monkeypatch.setattr(procs, "pgid_alive", group.alive)
        return group

    return make


def test_kill_group_of_stops_once_the_group_is_gone(clock, kill_world):
    group = kill_world(Group(clock, dies_on={signal.SIGINT}, delay=0.5))
    rig._kill_group_of(4001)
    assert group.signals == [signal.SIGINT]


def test_kill_group_of_escalates_when_the_group_holds_on(clock, kill_world):
    group = kill_world(Group(clock, dies_on={signal.SIGKILL}))
    rig._kill_group_of(4001)
    assert group.signals == [sig for sig, _ in SHUTDOWN_STEPS]


def test_kill_group_of_does_nothing_for_a_supervisor_that_is_already_gone(monkeypatch):
    def gone(pid):
        raise ProcessLookupError

    monkeypatch.setattr(os, "getpgid", gone)
    monkeypatch.setattr(os, "killpg", lambda *a: pytest.fail("nothing to signal"))
    rig._kill_group_of(4001)


def test_kill_group_of_never_signals_the_callers_own_group(clock, kill_world):
    """A supervisor that died before its `setsid` is still in the parent's
    group, and signalling that group kills the command reporting the failure."""
    group = kill_world(Group(clock), pgid=100, own_pgid=100)
    rig._kill_group_of(4001)
    assert group.sent == []


def test_kill_group_of_gives_up_quietly_on_a_group_it_may_not_signal(clock, kill_world, monkeypatch):
    kill_world(Group(clock))

    def refuse(pgid, sig):
        raise PermissionError

    monkeypatch.setattr(os, "killpg", refuse)
    rig._kill_group_of(4001)  # must not raise: this is already a failure path


# status


def test_status_marks_a_dead_child(world, monkeypatch, capsys):
    world()
    monkeypatch.setattr(rig, "pid_alive", lambda pid: pid != 4003)
    assert rig.status() == 0
    out = capsys.readouterr().out
    assert "electric_board (panel mode)" in out
    sushi = next(l for l in out.splitlines() if "sushi" in l)
    fmit = next(l for l in out.splitlines() if "fmit" in l)
    assert "(DEAD)" not in sushi
    assert "(DEAD)" in fmit


def test_status_survives_a_child_pid_that_is_not_a_pid(world, capsys):
    """Child pids come straight from the file. A null was a TypeError out of
    `os.kill`, in the command people run when things are already odd."""
    world()
    state_file.write_state(running_state(children={"sushi": None, "fmit": 0, "amp": "x"}))
    assert rig.status() == 0
    out = capsys.readouterr().out
    assert out.count("(INVALID)") == 3


def test_status_survives_children_that_is_not_a_mapping(world, capsys):
    world()
    state_file.write_state(running_state(children=["sushi"]))
    assert rig.status() == 0


def test_status_says_when_the_file_describes_no_rig_of_ours(world, capsys):
    world()
    state_file.write_state(running_state(pgid=4999))
    assert rig.status() == 0
    assert "does not describe a rig" in capsys.readouterr().out


def test_status_reports_the_amp_model_when_there_is_one(world, capsys):
    world()
    state_file.write_state(running_state(amp_model="amp/models/M.nam"))
    rig.status()
    assert "amp model  amp/models/M.nam" in capsys.readouterr().out


def test_status_with_nothing_recorded_or_a_stale_file(runtime, monkeypatch, capsys):
    assert rig.status() == 0
    assert "No rig is running." in capsys.readouterr().out
    state_file.write_state(running_state())
    monkeypatch.setattr(rig, "pid_alive", lambda pid: False)
    monkeypatch.setattr(rig, "pgid_alive", lambda pgid: False)
    assert rig.status() == 0
    assert "stale" in capsys.readouterr().out


def test_status_does_not_call_an_orphaned_group_a_running_rig(world, capsys):
    world(supervisor_alive=False)
    rig.status()
    out = capsys.readouterr().out
    assert "is gone" in out and "'sushi-rig down' stops them" in out
    assert "electric_board" not in out and "pid 4002" not in out, "it is not a status of a rig"


def test_describe_rig_is_status_as_data(world, monkeypatch):
    """The agent (#29) serves this; `status` prints it. One answer, two readers."""
    from datetime import datetime, timedelta

    world()
    monkeypatch.setattr(rig, "pid_alive", lambda pid: pid != 4003)
    state = state_file.read_state()
    now = datetime.fromisoformat(state["started_at"]) + timedelta(minutes=2)
    described = rig.describe_rig(now=now)
    assert described["verdict"] == "running" and described["running"] is True
    assert described["config_name"] == "electric_board"
    assert described["mode"] == "panel"
    assert described["uptime_seconds"] == 120.0
    assert described["message"] == f"electric_board (panel mode), up {format_uptime(120)}"
    assert described["children"]["sushi"]["alive"] is True
    assert described["children"]["fmit"] == {"pid": 4003, "alive": False}
    assert described["log_dir"] == state["log_dir"]
    assert "supervisor_pid" not in described, "the group is the handle, as for down"


def test_describe_rig_marks_a_child_pid_that_is_not_a_pid_as_unknown(world):
    world()
    state_file.write_state(running_state(children={"sushi": None, "amp": "x"}))
    children = rig.describe_rig()["children"]
    assert children == {"sushi": {"pid": None, "alive": None}, "amp": {"pid": "x", "alive": None}}


@pytest.mark.parametrize("verdict", ["absent", "gone", "invalid", "orphaned"])
def test_describe_rig_says_why_there_is_nothing_to_describe(runtime, monkeypatch, verdict):
    """Each non-running verdict is its own word plus the sentence `status`
    prints, and nothing that could read as a running rig."""
    if verdict != "absent":
        state_file.write_state(running_state(pgid=4999 if verdict == "invalid" else 4001))
    monkeypatch.setattr(rig, "pid_alive", lambda pid: False)
    monkeypatch.setattr(rig, "pgid_alive", lambda pgid: verdict == "orphaned")
    described = rig.describe_rig()
    assert described["verdict"] == verdict
    assert described["running"] is False
    assert "config_name" not in described
    assert described["message"]


# restart


def test_restart_with_nothing_running_refuses_and_says_how_to_start(runtime):
    with pytest.raises(rig.RigError, match="nothing to restart"):
        rig.restart()


def test_restart_hands_up_what_the_state_says_was_asked_for(runtime, monkeypatch):
    """The whole chain, end to end with `down` and `up` stubbed: an amp that
    failed to start and a model named on the command line both survive."""
    state_file.write_state(running_state(
        mode="headless", amp_requested=True,
        amp_model="amp/models/Other.nam", amp_model_requested="amp/models/Other.nam",
        children={"sushi": 4002},  # the amp is not here: it failed to start
    ))
    calls = []
    monkeypatch.setattr(rig, "down", lambda force=False: 0)
    monkeypatch.setattr(rig, "up", lambda name, **kw: calls.append((name, kw)) or 0)
    assert rig.restart() == 0
    assert calls == [("electric_board", {
        "headless": True, "tuner": True, "amp": True,
        "amp_model": "amp/models/Other.nam", "rig": False,
    })]


def test_restart_does_not_start_again_if_the_rig_would_not_stop(runtime, monkeypatch):
    state_file.write_state(running_state())
    monkeypatch.setattr(rig, "down", lambda force=False: 1)
    monkeypatch.setattr(rig, "up", lambda *a, **k: pytest.fail("started over a live rig"))
    with pytest.raises(rig.RigError, match="could not stop"):
        rig.restart()


# up


@pytest.fixture
def up_world(runtime, monkeypatch):
    """`up` with the fork stood in for: a pipe pre-loaded with what a
    supervisor would have said, and a record of what was done about it."""
    seen = SimpleNamespace(killed=[], forked=0, supervise_kwargs=[])
    monkeypatch.setattr(rig, "pid_alive", lambda pid: False)
    monkeypatch.setattr(rig, "pgid_alive", lambda pgid: False)
    monkeypatch.setattr(rig, "port_is_open", lambda host, port, *a, **k: False)
    monkeypatch.setattr(rig, "_kill_group_of", lambda pid: seen.killed.append(pid))

    def say(*lines, then_write=None):
        def fork(supervise):
            seen.forked += 1
            ready_r, ready_w = os.pipe()
            os.write(ready_w, "".join(line + "\n" for line in lines).encode())
            os.close(ready_w)
            if then_write is not None:
                state_file.write_state(then_write)
            return 4242, ready_r

        monkeypatch.setattr(rig, "_fork_supervisor", fork)

    seen.say = say
    return seen


def test_up_refuses_when_a_live_rig_is_recorded(up_world, monkeypatch):
    state_file.write_state(running_state())
    monkeypatch.setattr(rig, "pid_alive", lambda pid: True)
    monkeypatch.setattr(rig, "pgid_alive", lambda pgid: True)
    up_world.say("READY")
    with pytest.raises(rig.RigError, match="already running"):
        rig.up(headless=True)
    assert up_world.forked == 0
    assert state_file.read_state() is not None, "a refusal must not clear a live rig's state"


def test_up_clears_a_stale_state_file_and_carries_on(up_world, capsys):
    state_file.write_state(running_state(supervisor_pid=4001))  # pid_alive: False
    up_world.say("READY", then_write=running_state(supervisor_pid=4242, pgid=4242))
    assert rig.up(headless=True, tuner=False, amp=False) == 0
    assert up_world.forked == 1
    assert state_file.read_state()["supervisor_pid"] == 4242, "the stale file was replaced"
    assert "Rig up: electric_board (headless mode)" in capsys.readouterr().out


def test_up_refuses_on_a_crashed_rigs_leftovers_and_points_at_down(up_world, monkeypatch):
    """Dead supervisor, live group: starting over would stack a second rig on
    the first one's children. Clearing the file would hide them. It refuses,
    keeps the file, and says what stops them."""
    state_file.write_state(running_state())
    monkeypatch.setattr(rig, "pgid_alive", lambda pgid: True)  # pid_alive: False
    up_world.say("READY")
    with pytest.raises(rig.RigError, match="sushi-rig down"):
        rig.up(headless=True, tuner=False, amp=False)
    assert up_world.forked == 0
    assert state_file.read_state() is not None


@pytest.mark.parametrize("override", [
    {"supervisor_pid": 1, "pgid": 1}, {"supervisor_pid": 0, "pgid": 0},
    {"pgid": 4999},
])
def test_up_refuses_a_state_file_that_describes_no_rig_of_ours(up_world, monkeypatch, override):
    """Even when the ids it names happen to be alive. `up` uses the same verdict
    as `down` so the two cannot disagree about whether a rig exists."""
    state_file.write_state(running_state(**override))
    monkeypatch.setattr(rig, "pid_alive", lambda pid: True)
    monkeypatch.setattr(rig, "pgid_alive", lambda pgid: True)
    up_world.say("READY")
    with pytest.raises(rig.RigError, match="does not describe a rig"):
        rig.up(headless=True, tuner=False, amp=False)
    assert up_world.forked == 0
    assert state_file.read_state() is not None


def test_up_refuses_when_something_already_holds_the_grpc_port(up_world, monkeypatch):
    """A Sushi this supervisor did not start is invisible to the state file, and
    starting on top of it passed the readiness check against the other one."""
    monkeypatch.setattr(rig, "port_is_open", lambda host, port, *a, **k: port == 51051)
    up_world.say("READY")
    with pytest.raises(rig.RigError, match="127.0.0.1:51051"):
        rig.up(headless=True)
    assert up_world.forked == 0


def test_up_refuses_an_unknown_config(up_world):
    up_world.say("READY")
    with pytest.raises(rig.RigError, match="no such config"):
        rig.up("no_such_config_anywhere")
    assert up_world.forked == 0


def test_up_reports_a_fail_line_as_an_error_and_kills_the_group(up_world):
    # The supervisor got as far as recording itself before it gave up, so the
    # state file `up` must remove is the one written during the start.
    up_world.say(
        "WARN the tuner is deaf", "FAIL Sushi exited during startup — see /x/sushi.log",
        then_write=running_state(supervisor_pid=4242, pgid=4242),
    )
    with pytest.raises(rig.RigError, match="Sushi exited during startup"):
        rig.up(headless=True, tuner=False, amp=False)
    assert up_world.killed == [4242], "whatever did start must not be left running"
    assert state_file.read_state() is None, "a failed start leaves no state"


def test_up_reports_a_supervisor_that_went_silent_as_a_failure(up_world):
    """The pipe closing with neither READY nor FAIL -- the supervisor was killed
    or crashed -- is still a failed start, not a cheerful prompt."""
    up_world.say()
    with pytest.raises(rig.RigError, match="failed to start"):
        rig.up(headless=True, tuner=False, amp=False)
    assert up_world.killed == [4242]


def test_up_prints_warnings_and_returns_zero_on_ready(up_world, capsys):
    up_world.say("WARN amp exited during startup (status 1)", "WARN the tuner is deaf", "READY",
                 then_write=running_state(supervisor_pid=4242, pgid=4242))
    assert rig.up(headless=True, tuner=False, amp=False) == 0
    printed = capsys.readouterr()
    assert "warning: amp exited during startup (status 1)" in printed.err
    assert "warning: the tuner is deaf" in printed.err
    assert "Rig up:" in printed.out
    assert up_world.killed == [], "a rig that came up is not torn down"


def test_up_does_not_wait_for_lines_after_ready(up_world):
    """READY ends the wait. The supervisor keeps the pipe's write end only until
    it closes it, but `up` must not depend on that."""
    up_world.say("READY", "FAIL too late to matter")
    assert rig.up(headless=True, tuner=False, amp=False) == 0


def test_up_tells_the_supervisor_what_was_asked_for(runtime, monkeypatch):
    """What `restart` later restores is recorded from these, so they must be
    the caller's request and not a guess."""
    monkeypatch.setattr(rig, "pid_alive", lambda pid: False)
    monkeypatch.setattr(rig, "port_is_open", lambda *a, **k: False)
    got = {}

    def supervise(*args, ready_w, **kwargs):
        got.update(kwargs)
        os.write(ready_w, b"READY\n")

    def fork(run):
        ready_r, ready_w = os.pipe()
        run(ready_w)  # in-process: no fork, just the same call
        os.close(ready_w)
        return 4242, ready_r

    monkeypatch.setattr(rig, "_supervise", supervise)
    monkeypatch.setattr(rig, "_fork_supervisor", fork)
    assert rig.up(headless=True, tuner=False, amp=False, amp_model="M.nam") == 0
    assert got["tuner_requested"] is False
    assert got["amp_requested"] is False
    assert got["amp_model_requested"] == "M.nam", "the override as given, not as resolved"
    assert got["headless"] is True


# --- a real supervisor, children stubbed out ----------------------------------


@pytest.fixture
def real_supervisor(runtime, monkeypatch):
    """Let `up` really fork, with the *children* replaced.

    The pipe protocol and the supervisor's `try` block are where a mock would
    agree with whatever it was told, so this runs the real thing: a real fork,
    a real `setsid`, a real pipe, a real state file. Only what would start
    external programs is swapped, and the swaps are inherited by the child.

    The supervisor never exits on its own once the rig is up, so it is killed by
    its recorded pid afterwards -- never by pattern.
    """
    spawned: list[int] = []
    real_fork = rig._fork_supervisor

    def recording_fork(supervise):
        pid, ready_r = real_fork(supervise)
        spawned.append(pid)
        return pid, ready_r

    monkeypatch.setattr(rig, "_fork_supervisor", recording_fork)
    monkeypatch.setattr(rig, "pid_alive", lambda pid: False)
    monkeypatch.setattr(rig, "port_is_open", lambda *a, **k: False)
    monkeypatch.setattr(rig, "wait_for_grpc", lambda *a, **k: True)
    yield spawned
    for pid in spawned:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        try:
            os.waitpid(pid, 0)
        except ChildProcessError:
            pass


def test_a_patchbay_that_will_not_stay_up_is_a_warning_not_a_failed_rig(
    real_supervisor, monkeypatch, capsys
):
    """qpwgraph is optional. When all its attempts fail the supervisor words a
    warning -- and while that f-string named a constant nobody had imported, the
    NameError landed in the supervisor's blanket `except`, was reported as FAIL,
    and `up` tore down a rig whose only problem was the patchbay."""
    from sushi_rig.qpwgraph import QPWGRAPH_ATTEMPTS

    monkeypatch.setattr(rig, "plan_children", lambda *a, **k: [{
        "name": "qpwgraph", "command": ["qpwgraph"], "optional": True,
    }])
    monkeypatch.setattr(rig, "start_qpwgraph", lambda *a, **k: None)

    assert rig.up(headless=True, tuner=False, amp=False) == 0

    printed = capsys.readouterr()
    assert "warning:" in printed.err and "patchbay" in printed.err
    assert f"{QPWGRAPH_ATTEMPTS} attempts" in printed.err
    assert "Rig up:" in printed.out
    recorded = state_file.read_state()
    assert recorded is not None and recorded["supervisor_pid"] == real_supervisor[0]
    assert recorded["pgid"] == real_supervisor[0], "the supervisor leads its own group"
    assert recorded["tuner_requested"] is False and recorded["amp_requested"] is False


def test_a_child_that_cannot_start_fails_up_and_takes_the_group_down_promptly(
    real_supervisor, monkeypatch
):
    """A required child that cannot be executed is FAIL. The supervisor then
    exits as an unreaped zombie of `up`, and a zombie still counts as a group
    member -- so, until `_kill_group_of` reaped it, the error arrived after the
    whole eight-second escalation had run against a process already dead."""
    import time

    monkeypatch.setattr(rig, "plan_children", lambda *a, **k: [{
        "name": "sushi", "command": ["/nonexistent/sushi"], "optional": False,
    }])
    started = time.monotonic()
    with pytest.raises(rig.RigError, match="sushi did not start"):
        rig.up(headless=True, tuner=False, amp=False)
    assert time.monotonic() - started < 3.0
    assert state_file.read_state() is None
