"""Checking that a config does what it says.

The decisions are pure — a config dict against a captured-state dict — so all of
this runs with no Sushi, no plugins and no audio.

Two of these tests are regressions for bugs that actually shipped. Both times a
config loaded cleanly, named only things that existed, and quietly did something
else; both times it looked like a save bug and was a load bug. They are the
reason this module exists, so they are written as the scenarios they were rather
than as minimal cases.
"""

from __future__ import annotations

from sushi_rig.verify import TOLERANCE, check_names, diff_state


def _config(**overrides):
    """A small rig: one track, two plugins, state for both."""
    config = {
        "tracks": [{
            "name": "board",
            "plugins": [{"name": "eq", "uri": "urn:eq"},
                        {"name": "verb", "uri": "urn:verb"}],
        }],
        "initial_state": [
            {"processor": "board", "parameters": {"gain": 0.8}},
            {"processor": "eq", "bypassed": False, "parameters": {"Low": 0.41, "Mid": 0.56}},
            {"processor": "verb", "bypassed": True, "parameters": {"Mix": 0.3}},
        ],
    }
    config.update(overrides)
    return config


def _live(**overrides):
    """What `capture()` would return for a rig that matches `_config()`."""
    live = {
        "processors": {
            "board": {"parameters": {"gain": 0.8}},
            "eq": {"bypassed": False, "parameters": {"Low": 0.41, "Mid": 0.56}},
            "verb": {"bypassed": True, "parameters": {"Mix": 0.3}},
        },
        "tracks": {"board": ["eq", "verb"]},
    }
    live.update(overrides)
    return live


# --- the two bugs that prompted this ----------------------------------------


def test_a_preset_index_overwriting_saved_parameters_is_caught():
    """The bug: Sushi applies a `program` *over* the `parameters` in the same
    initial_state entry, so a saved EQ came back as the plugin's factory preset
    every time. Reported as "the EQ params are not getting saved" — they were
    saved perfectly and overwritten at load.

    Nothing in the file is invalid, so only comparing against the running rig
    finds it."""
    live = _live()
    live["processors"]["eq"]["parameters"] = {"Low": 0.5, "Mid": 0.5}  # the preset

    problems = {str(p) for p in diff_state(_config(), live)}
    assert any("Low did not apply" in p for p in problems)
    assert any("Mid did not apply" in p for p in problems)


def test_a_track_bypass_cascading_over_every_plugin_is_caught():
    """The bug: `bypassed` on a *track* cascades to every processor on it, so a
    single `"bypassed": false` silently undid every per-plugin flag in the same
    file. A config with four bypassed pedals came up with all four active."""
    live = _live()
    live["processors"]["verb"]["bypassed"] = False  # cascaded on

    problems = [str(p) for p in diff_state(_config(), live)]
    assert problems == ["verb: bypass did not apply — config True, rig False"]


# --- what it must not report ------------------------------------------------


def test_a_config_that_applied_correctly_reports_nothing():
    assert diff_state(_config(), _live()) == []


def test_a_tracks_bypass_is_never_compared():
    """Tracks report no bypass state, so comparing one invents a difference.
    Found by running this against a real rig: a track entry saying `false` came
    back as `None`, which read as a failure and was not one."""
    config = _config()
    config["initial_state"][0]["bypassed"] = False
    assert diff_state(config, _live()) == []


def test_values_within_tolerance_are_the_same_value():
    """Values are rounded to 6dp on write and Sushi quantises, so an exact
    comparison would report differences that are not differences."""
    live = _live()
    live["processors"]["eq"]["parameters"]["Low"] = 0.41 + TOLERANCE / 2
    assert diff_state(_config(), live) == []


def test_a_difference_just_outside_tolerance_is_reported():
    live = _live()
    live["processors"]["eq"]["parameters"]["Low"] = 0.41 + TOLERANCE * 2
    assert len(diff_state(_config(), live)) == 1


# --- structure --------------------------------------------------------------


def test_a_reordered_chain_is_caught():
    """Chain order is part of the sound — the panel can reorder it live, and a
    save is supposed to keep it."""
    live = _live(tracks={"board": ["verb", "eq"]})
    assert any("chain order differs" in str(p) for p in diff_state(_config(), live))


def test_a_processor_missing_from_the_rig_is_caught():
    live = _live()
    del live["processors"]["verb"]
    problems = [str(p) for p in diff_state(_config(), live)]
    assert any("not in the running rig" in p for p in problems)


def test_a_missing_track_is_caught():
    assert any(
        "track is missing" in str(p)
        for p in diff_state(_config(), _live(tracks={}))
    )


def test_every_difference_is_reported_not_just_the_first():
    """At rig scale you want the whole list, not one fix cycle per problem —
    following RigSpec.validate, which decided this already."""
    live = _live()
    live["processors"]["eq"]["parameters"] = {"Low": 0.9, "Mid": 0.9}
    live["processors"]["verb"]["bypassed"] = False
    assert len(diff_state(_config(), live)) == 3


# --- parameter names --------------------------------------------------------


DUMP = {"plugins": [
    {"name": "eq", "parameters": [{"name": "Low"}, {"name": "Mid"}, {"name": "High"}]},
    {"name": "verb", "parameters": [{"name": "Mix"}]},
]}


def test_a_name_that_exists_passes():
    assert check_names(_config(), DUMP) == []


def test_a_case_difference_is_named_with_a_suggestion():
    """The usual mistake is case or a symbol-versus-name mix-up, not an invented
    name — and Sushi's own refusal says only "Failed to load the initial
    processor states", naming neither the processor nor the parameter."""
    config = _config()
    config["initial_state"][1]["parameters"] = {"low": 0.41}
    problems = [str(p) for p in check_names(config, DUMP)]
    assert problems == ["eq: no parameter named 'low' — did you mean 'Low'?"]


def test_an_invented_name_lists_what_is_available():
    config = _config()
    config["initial_state"][1]["parameters"] = {"Treble": 0.5}
    (problem,) = check_names(config, DUMP)
    assert "it has: High, Low, Mid" in str(problem)


def test_a_tracks_parameters_are_not_checked_against_the_plugin_dump():
    """Tracks carry gain, pan and mute but are not plugins, so there is nothing
    in the dump to check them against."""
    config = _config()
    config["initial_state"][0]["parameters"] = {"gain": 0.8, "pan": 0.5}
    assert check_names(config, DUMP) == []


def _rig_with_sends():
    """What the compiler writes for one channel and a master: the send on the
    channel names the master's return, and the dump lists neither plugin's
    properties, because `--dump-plugins` never does."""
    return {
        "tracks": [
            {"name": "board", "plugins": [
                {"name": "eq", "uri": "urn:eq"},
                {"name": "board_send", "uid": "sushi.testing.send", "type": "internal"},
            ]},
            {"name": "master", "plugins": [
                {"name": "master_return", "uid": "sushi.testing.return", "type": "internal"},
            ]},
        ],
        "initial_state": [
            {"processor": "eq", "parameters": {"Low": 0.41}},
            {"processor": "board_send", "properties": {"destination_name": "master_return"}},
        ],
    }


SENDS_DUMP = {"plugins": [
    {"name": "eq", "parameters": [{"name": "Low"}, {"name": "Mid"}, {"name": "High"}]},
    {"name": "board_send", "parameters": [{"name": "gain"}, {"name": "channel_count"}]},
    {"name": "master_return", "parameters": []},
]}


def test_a_sends_destination_is_a_property_it_has():
    assert check_names(_rig_with_sends(), SENDS_DUMP) == []


def test_a_mistyped_destination_name_is_caught_before_sushi_exits_7():
    """Sushi refuses a config whose initial_state names a property the plugin
    lacks with "Failed to load the initial processor states" and exit 7,
    naming nothing. For the internal plugins the compiler writes, the
    properties are known in advance, so --quick can say which."""
    config = _rig_with_sends()
    config["initial_state"][1]["properties"] = {"destination": "master_return"}
    problems = [str(p) for p in check_names(config, SENDS_DUMP)]
    assert problems == ["board_send: no property named 'destination' — did you mean 'destination_name'?"]


def test_a_property_on_a_return_is_caught_because_a_return_has_none():
    config = _rig_with_sends()
    config["initial_state"].append({"processor": "master_return", "properties": {"source_name": "x"}})
    problems = [str(p) for p in check_names(config, SENDS_DUMP)]
    assert problems == ["master_return: no property named 'source_name' — it has none"]


def test_an_lv2_plugins_properties_are_left_to_the_running_rig():
    """The dump lists no properties for any plugin, so a name here can be
    neither confirmed nor refuted offline; `diff_state` checks it live rather
    than this check guessing."""
    config = _rig_with_sends()
    config["initial_state"][0]["properties"] = {"anything": "goes"}
    assert check_names(config, SENDS_DUMP) == []


# --- the environment Sushi runs in -----------------------------------------


def test_sushi_env_points_lv2_path_at_plugins_alone(tmp_path):
    from sushi_rig.verify import sushi_env

    env = sushi_env(tmp_path, base={"LV2_PATH": "/usr/lib/lv2", "HOME": "/home/x"})
    assert env["LV2_PATH"] == str(tmp_path / "plugins")
    assert env["HOME"] == "/home/x"


def test_the_plugin_dump_runs_with_the_rigs_lv2_path(tmp_path, monkeypatch):
    """The dump used to inherit the caller's LV2_PATH while the headless rig set
    its own. On a clean machine -- the CI container, a fresh deploy -- Sushi
    then loaded no plugins and exited 4, "Failed to load tracks from the Json
    config file", for every config."""
    import json
    from sushi_rig import verify as verify_module

    monkeypatch.delenv("LV2_PATH", raising=False)
    monkeypatch.setattr(verify_module, "checkout_root", lambda: tmp_path)
    seen = {}

    def fake_dump(config_path, sushi_bin="sushi", env=None):
        seen["env"] = env
        return {"plugins": []}

    monkeypatch.setattr(verify_module, "dump_plugins", fake_dump)
    config = tmp_path / "rig.json"
    config.write_text(json.dumps({"tracks": []}))

    verify_module.verify(config, quick=True)

    assert seen["env"] is not None, "the dump inherited the caller's environment"
    assert seen["env"]["LV2_PATH"] == str(tmp_path / "plugins")


# --- which Sushi `sushi-rig verify` runs -------------------------------------


def _run_verify_cli(tmp_path, monkeypatch, *extra):
    """Run `sushi-rig verify` with verify() stubbed, returning the binary it got."""
    from sushi_rig import cli, paths
    from sushi_rig import verify as verify_module

    monkeypatch.setattr(paths, "checkout_root", lambda: tmp_path)
    seen = []
    monkeypatch.setattr(
        verify_module, "verify", lambda target, sushi, quick=False: seen.append(sushi) or []
    )
    config = tmp_path / "rig.json"
    config.write_text("{}")
    try:
        cli.main(["verify", str(config), "--quick", *extra])
    except SystemExit as exc:
        assert not exc.code, f"verify exited {exc.code}"
    return seen


def test_verify_defaults_to_the_rigs_own_sushi(tmp_path, monkeypatch):
    """It used to default to a `sushi` on PATH, which fails with "No such file"
    wherever Sushi is not also installed system-wide -- the rig keeps its own
    at ./sushi, and that is the one `sushi-rig up` runs."""
    assert _run_verify_cli(tmp_path, monkeypatch) == [str(tmp_path / "sushi")]


def test_an_explicit_sushi_still_wins(tmp_path, monkeypatch):
    assert _run_verify_cli(tmp_path, monkeypatch, "--sushi", "/opt/other/sushi") == [
        "/opt/other/sushi"
    ]


def test_stopping_a_verification_sushi_does_not_wait_out_the_grace_period():
    """`_stop` reaps the Popen it was given. Otherwise the child, once killed,
    is a zombie -- which still counts as a member of its group -- and the stop
    sits through both full grace periods (five seconds) against a dead process.
    Against a real process leading its own group, as the verification Sushi is."""
    import subprocess
    import sys
    import time

    from sushi_rig import verify

    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"], start_new_session=True,
    )
    started = time.monotonic()
    verify._stop(child)
    assert child.returncode is not None, "it was reaped, not left a zombie"
    assert time.monotonic() - started < 2.0


# --- properties: a send's destination is one -------------------------------


def _config_with_send():
    return {
        "tracks": [{"name": "guitar", "plugins": [{"name": "guitar_send"}]}],
        "initial_state": [
            {"processor": "guitar_send", "properties": {"destination_name": "master_return"}},
        ],
    }


def test_a_send_whose_destination_did_not_apply_is_caught():
    """The compiled rig's most important setting. Before this, diff_state
    compared parameters, bypass and order only, so a channel silent in the
    monitors passed as applied."""
    live = {"processors": {"guitar_send": {"parameters": {}, "properties": {"destination_name": "No destination"}}},
            "tracks": {"guitar": ["guitar_send"]}}
    (problem,) = diff_state(_config_with_send(), live)
    assert problem.processor == "guitar_send"
    assert "destination_name did not apply" in problem.detail
    assert "'master_return'" in problem.detail and "'No destination'" in problem.detail


def test_a_property_that_applied_reports_nothing():
    live = {"processors": {"guitar_send": {"parameters": {}, "properties": {"destination_name": "master_return"}}},
            "tracks": {"guitar": ["guitar_send"]}}
    assert diff_state(_config_with_send(), live) == []


def test_properties_the_rig_could_not_be_asked_about_are_unchecked_not_passed():
    """A capture from a Sushi that answered nothing about properties leaves
    the key absent; that is a problem to report, not a pass."""
    live = {"processors": {"guitar_send": {"parameters": {}}}, "tracks": {"guitar": ["guitar_send"]}}
    (problem,) = diff_state(_config_with_send(), live)
    assert "could not be read" in problem.detail and "unchecked" in problem.detail


def test_a_property_the_processor_does_not_have_is_caught():
    live = {"processors": {"guitar_send": {"parameters": {}, "properties": {}}}, "tracks": {"guitar": ["guitar_send"]}}
    (problem,) = diff_state(_config_with_send(), live)
    assert "destination_name is not a property" in problem.detail


def test_a_property_listed_but_unreadable_is_unchecked_not_absent():
    live = {"processors": {"guitar_send": {"parameters": {}, "properties": {"destination_name": None}}},
            "tracks": {"guitar": ["guitar_send"]}}
    (problem,) = diff_state(_config_with_send(), live)
    assert "could not be read" in problem.detail and "is a property" in problem.detail
