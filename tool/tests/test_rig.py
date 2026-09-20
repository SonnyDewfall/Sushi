"""The rig supervisor's decisions, pinned without starting anything.

Everything here runs with no audio hardware, no Sushi and no processes. The
parts that genuinely need a running rig — that a killed process group really
does take `sushi.bin` and open-stage-control's Electron helpers with it — are
verified by hand against real hardware; what is testable offline is the logic
that decides *what* to start and *how far* to escalate when stopping.
"""

from __future__ import annotations

import json
import signal

import pytest

from sushi_rig.procs import SHUTDOWN_STEPS
from sushi_rig.rig import (
    DEFAULT_CONFIG_NAME,
    FALLBACK_RIG_YAML,
    format_uptime,
    plan_children,
    resolve_rig_yaml,
)
from sushi_rig.state import is_stale
from pathlib import Path


# --- which rig.yaml a config came from --------------------------------------


def test_saved_variant_is_attributed_to_its_own_source(tmp_path):
    """A variant records the yaml it was built from, because variants share a
    hand-authored source rather than getting one of their own. Without reading
    it back, every save made from a variant would be attributed to
    electric_board."""
    config = tmp_path / "acoustic_chorus.json"
    config.write_text(json.dumps({"_meta": {"source": "config/src/acoustic.yaml"}}))
    assert resolve_rig_yaml(config) == "config/src/acoustic.yaml"


def test_hand_authored_config_falls_back(tmp_path):
    """The canonical configs carry no _meta.source."""
    config = tmp_path / "electric_board.json"
    config.write_text(json.dumps({"tracks": []}))
    assert resolve_rig_yaml(config) == FALLBACK_RIG_YAML


def test_unreadable_config_falls_back_rather_than_raising(tmp_path):
    """Resolving the source is a nicety; a broken config should fail later, on
    the thing that actually needs it, with a better message than this could
    give."""
    assert resolve_rig_yaml(tmp_path / "missing.json") == FALLBACK_RIG_YAML
    broken = tmp_path / "broken.json"
    broken.write_text("{not json")
    assert resolve_rig_yaml(broken) == FALLBACK_RIG_YAML


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
    assert amp["command"][:2] == ["carla", "-n"]


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
        "children": {"fmit": 1, "sushi": 2, "amp": 3, "open-stage-control": 4},
    })
    assert name == "electric-chorus-2"
    assert options == {"headless": False, "tuner": True, "amp": True}


def test_restart_preserves_the_choices_the_rig_was_started_with():
    """A rig started --headless --no-amp --no-tuner must not quietly gain a
    panel, an amp and a tuner on the way back."""
    from sushi_rig.rig import restart_arguments

    _, options = restart_arguments({
        "config_name": "x", "mode": "headless", "children": {"sushi": 1},
    })
    assert options == {"headless": True, "tuner": False, "amp": False}


def test_restart_falls_back_to_the_default_config_rather_than_failing():
    """A state file missing its config name is damaged, not a reason to refuse
    to bring the rig back."""
    from sushi_rig.rig import DEFAULT_CONFIG_NAME, restart_arguments

    assert restart_arguments({"children": {}})[0] == DEFAULT_CONFIG_NAME
