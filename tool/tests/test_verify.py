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
