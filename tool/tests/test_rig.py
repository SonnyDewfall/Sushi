"""The rig supervisor's decisions, pinned without starting anything.

Everything here runs with no audio hardware, no Sushi and no processes. The
parts that genuinely need a running rig — that a killed process group really
does take `sushi.bin` and open-stage-control's Electron helpers with it — are
verified by hand against real hardware; what is testable offline is the logic
that decides *what* to start and *how far* to escalate when stopping.
"""

from __future__ import annotations

import json
import os
import signal
from types import SimpleNamespace

import pytest

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
from sushi_rig import rig, state as state_file
from sushi_rig.state import is_stale
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


# --- second-instance guard --------------------------------------------------


def test_a_running_supervisor_is_not_stale():
    """The guard that was missing: starting a second rig on top of a running one
    is what produced the two-wrapper collision that took a live session down."""
    assert not is_stale({"supervisor_pid": 42}, lambda pid: True)


def test_a_dead_supervisor_is_stale():
    """A crash must not leave `up` refusing forever."""
    assert is_stale({"supervisor_pid": 42}, lambda pid: False)


def test_no_state_is_not_stale_state():
    """Nothing recorded means nothing to clean up, not a stale file."""
    assert not is_stale(None, lambda pid: pytest.fail("should not be consulted"))
    assert not is_stale({}, lambda pid: pytest.fail("should not be consulted"))


def test_state_without_a_pid_is_stale():
    """Unusable is as good as gone — better than trusting a malformed file."""
    assert is_stale({"mode": "panel"}, lambda pid: pytest.fail("no pid to check"))


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
        "headless": False, "tuner": True, "amp": True, "amp_model": None,
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
        "headless": True, "tuner": False, "amp": False, "amp_model": None,
    }


def test_restart_carries_the_amp_model_override():
    """`--amp-model` is the whole reason for naming a model on the command line.
    Dropped on restart, the config's own model came back with nothing said --
    an amp that sounds plausible and is the wrong one."""
    from sushi_rig.rig import restart_arguments

    _, options = restart_arguments({
        "config_name": "x", "mode": "panel", "amp_requested": True,
        "amp_model": "amp/models/Other.nam", "children": {"sushi": 1, "amp": 2},
    })
    assert options["amp_model"] == "amp/models/Other.nam"


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
        "amp_model": "amp/models/M.nam",
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


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    """A private runtime dir, so the real state file is neither read nor hurt."""
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "run"))
    return tmp_path / "run"


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


# restart


def test_restart_with_nothing_running_refuses_and_says_how_to_start(runtime):
    with pytest.raises(rig.RigError, match="nothing to restart"):
        rig.restart()


def test_restart_hands_up_what_the_state_says_was_asked_for(runtime, monkeypatch):
    """The whole chain, end to end with `down` and `up` stubbed: an amp that
    failed to start and a model named on the command line both survive."""
    state_file.write_state(running_state(
        mode="headless", amp_requested=True, amp_model="amp/models/Other.nam",
        children={"sushi": 4002},  # the amp is not here: it failed to start
    ))
    calls = []
    monkeypatch.setattr(rig, "down", lambda force=False: 0)
    monkeypatch.setattr(rig, "up", lambda name, **kw: calls.append((name, kw)) or 0)
    assert rig.restart() == 0
    assert calls == [("electric_board", {
        "headless": True, "tuner": True, "amp": True,
        "amp_model": "amp/models/Other.nam",
    })]


def test_restart_does_not_start_again_if_the_rig_would_not_stop(runtime, monkeypatch):
    state_file.write_state(running_state())
    monkeypatch.setattr(rig, "down", lambda force=False: 1)
    monkeypatch.setattr(rig, "up", lambda *a, **k: pytest.fail("started over a live rig"))
    with pytest.raises(rig.RigError, match="could not stop"):
        rig.restart()


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
    assert rig.up(headless=True, tuner=False, amp=False) == 0
    assert got["tuner_requested"] is False
    assert got["amp_requested"] is False
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
