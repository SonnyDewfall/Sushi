"""The state file and where it lives.

Real files in a temporary directory; nothing here is mocked except the
environment. What the file *means* (stale, orphaned, running) is pinned in
`test_rig.py` beside the supervisor that acts on it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from sushi_rig import paths, state


def test_read_state_of_a_missing_file_is_none(runtime):
    assert state.read_state() is None


def test_read_state_of_a_corrupt_file_is_none_not_an_exception(runtime):
    """A half-written file after a crash must read as "nothing recorded", so
    `up` can start again; it is `down`'s job to be loud about a rig that exists."""
    runtime.mkdir(parents=True)
    (runtime / "rig.json").write_text("{not json")
    assert state.read_state() is None


@pytest.mark.parametrize("body", ["[]", '"text"', "42", "null"])
def test_read_state_of_json_that_is_not_an_object_is_none(runtime, body):
    """Every caller indexes into the result, so a list would blow up inside
    `down` instead of reading as nothing recorded."""
    runtime.mkdir(parents=True)
    (runtime / "rig.json").write_text(body)
    assert state.read_state() is None


def test_write_then_read_round_trips_and_creates_the_directory(runtime):
    assert not runtime.exists()
    wanted = {"supervisor_pid": 7, "pgid": 7, "children": {"sushi": 8}, "amp_model": None}
    state.write_state(wanted)
    assert (runtime / "rig.json").is_file()
    assert state.read_state() == wanted


def test_write_replaces_rather_than_merges(runtime):
    state.write_state({"a": 1, "b": 2})
    state.write_state({"a": 3})
    assert state.read_state() == {"a": 3}


def test_clear_state_removes_the_file_and_tolerates_there_being_none(runtime):
    state.write_state({"a": 1})
    state.clear_state()
    assert state.read_state() is None
    state.clear_state()  # already gone: not an error


def test_runtime_dir_uses_xdg_runtime_dir_when_set(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    assert paths.runtime_dir() == tmp_path / "sushi-rig"
    assert paths.state_path() == tmp_path / "sushi-rig" / "rig.json"
    assert paths.log_dir() == tmp_path / "sushi-rig" / "logs"


@pytest.mark.parametrize("value", [None, ""])
def test_runtime_dir_falls_back_to_the_cache_when_xdg_is_unset_or_empty(tmp_path, monkeypatch, value):
    """An empty `XDG_RUNTIME_DIR` is as good as none: `Path("")` would be the
    current directory, and the state file would land wherever `up` was run."""
    if value is None:
        monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    else:
        monkeypatch.setenv("XDG_RUNTIME_DIR", value)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert paths.runtime_dir() == tmp_path / ".cache" / "sushi-rig"


def test_checkout_root_is_where_this_package_was_installed_from_not_home(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    root = paths.checkout_root()
    assert root != tmp_path
    assert (root / "tool" / "src" / "sushi_rig" / "paths.py").is_file()
