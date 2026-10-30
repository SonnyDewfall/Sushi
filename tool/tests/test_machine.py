"""The machine file: what it accepts, and every way it can be wrong.

`validate()` returns every problem at once, so most of these assert on the whole
list — a test that only checked "something was reported" would pass for the
wrong reason.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from sushi_rig.machine import MachineError, MachineSpec, machine_path
from sushi_rig.paths import checkout_root

GOOD = """
schema_version: 1
interface: Audient iD14
roles:
  guitar-in:   ["Audient iD14:capture_AUX0"]
  vocal-in:    ["Audient iD14:capture_AUX1"]
  monitor-out: ["Audient iD14:playback_AUX0", "Audient iD14:playback_AUX1"]
"""


def _load(tmp_path: Path, text: str) -> MachineSpec:
    path = tmp_path / "m.yaml"
    path.write_text(textwrap.dedent(text))
    return MachineSpec.load(path)


def test_a_good_file_loads_and_validates(tmp_path):
    machine = _load(tmp_path, GOOD)
    assert machine.validate() == []
    assert machine.roles["monitor-out"] == [
        "Audient iD14:playback_AUX0", "Audient iD14:playback_AUX1",
    ]
    assert machine.interface == "Audient iD14"


def test_the_committed_example_is_valid():
    """The worked example is what the next machine starts from. One that does not
    validate would teach the wrong thing."""
    example = checkout_root() / "machines" / "example.yaml"
    assert MachineSpec.load(example).validate() == []


def test_interface_is_optional(tmp_path):
    """It is informational, so a file without it is not wrong."""
    machine = _load(tmp_path, "schema_version: 1\nroles:\n  a: ['x:y']\n")
    assert machine.validate() == []


# --- schema_version -----------------------------------------------------------


def test_a_missing_schema_version_is_the_only_problem_reported(tmp_path):
    machine = _load(tmp_path, "roles:\n  a: []\n")
    problems = machine.validate()
    assert len(problems) == 1 and "schema_version is missing" in problems[0]


def test_an_unknown_schema_version_is_refused_without_judging_the_rest(tmp_path):
    """A v2 file's `roles` may mean something else. Reporting its roles as
    malformed would be noise, and accepting them would be a guess about where
    audio goes."""
    machine = _load(tmp_path, "schema_version: 2\nroles: {a: []}\nsurprise: 1\n")
    problems = machine.validate()
    assert len(problems) == 1
    assert "unknown schema_version 2" in problems[0]


@pytest.mark.parametrize("value", ["true", '"1"', "1.0", "[1]"])
def test_a_schema_version_that_is_not_the_integer_one_is_refused(tmp_path, value):
    """`true` is an int in Python, so `== 1` alone would accept it."""
    problems = _load(tmp_path, f"schema_version: {value}\nroles: {{a: ['x:y']}}\n").validate()
    assert len(problems) == 1 and "unknown schema_version" in problems[0]


# --- roles --------------------------------------------------------------------


def test_a_role_with_no_ports_is_a_problem(tmp_path):
    """An empty role generates no links: silence, with nothing saying why."""
    machine = _load(tmp_path, "schema_version: 1\nroles:\n  guitar-in: []\n")
    assert machine.validate() == ["role 'guitar-in': has no ports"]


def test_a_duplicate_role_is_reported_not_silently_last_wins(tmp_path):
    """PyYAML keeps the later of two equal keys and says nothing, so the first
    definition would vanish."""
    machine = _load(
        tmp_path,
        """
        schema_version: 1
        roles:
          guitar-in: ["A:x"]
          vocal-in:  ["A:y"]
          guitar-in: ["B:x"]
        """,
    )
    problems = machine.validate()
    assert len(problems) == 1
    assert "duplicate role 'guitar-in'" in problems[0]
    assert "line 6" in problems[0]


def test_a_duplicate_top_level_key_is_reported(tmp_path):
    machine = _load(
        tmp_path,
        "schema_version: 1\nroles: {a: ['x:y']}\nroles: {b: ['x:z']}\n",
    )
    problems = machine.validate()
    assert any("duplicate key 'roles'" in p for p in problems)


def test_a_role_written_as_a_bare_string_is_refused_not_split_into_characters(tmp_path):
    machine = _load(tmp_path, 'schema_version: 1\nroles:\n  guitar-in: "A:x"\n')
    problems = machine.validate()
    assert len(problems) == 1 and "must be a list" in problems[0]


def test_an_empty_roles_mapping_is_a_problem(tmp_path):
    problems = _load(tmp_path, "schema_version: 1\nroles: {}\n").validate()
    assert problems == ["roles is empty — nothing here could feed or take anything"]


def test_missing_roles_is_the_same_problem(tmp_path):
    problems = _load(tmp_path, "schema_version: 1\n").validate()
    assert len(problems) == 1 and "roles is empty" in problems[0]


def test_roles_that_is_not_a_mapping_is_a_problem(tmp_path):
    problems = _load(tmp_path, "schema_version: 1\nroles: [a, b]\n").validate()
    assert len(problems) == 1 and "roles must be a mapping" in problems[0]


def test_a_misspelt_key_is_named(tmp_path):
    """YAML ignores it, so `role:` would load as a machine with no roles at all."""
    problems = _load(
        tmp_path, "schema_version: 1\nrole: {a: ['x:y']}\nroles: {a: ['x:y']}\n"
    ).validate()
    assert len(problems) == 1 and "unknown key 'role'" in problems[0]


@pytest.mark.parametrize(
    "port, fragment",
    [
        ('""', "non-empty string"),
        ("5", "non-empty string"),
        ('"no-colon"', "<client>:<port> form"),
        ('":port"', "<client>:<port> form"),
        ('"client:"', "<client>:<port> form"),
        ('" A:x"', "whitespace"),
    ],
)
def test_a_malformed_port_is_named(tmp_path, port, fragment):
    problems = _load(tmp_path, f"schema_version: 1\nroles:\n  r: [{port}]\n").validate()
    assert len(problems) == 1
    assert "role 'r'" in problems[0] and fragment in problems[0]


def test_the_same_port_twice_in_one_role_is_a_problem(tmp_path):
    problems = _load(tmp_path, "schema_version: 1\nroles:\n  r: ['A:x', 'A:x']\n").validate()
    assert problems == ["role 'r': port 'A:x' is listed twice"]


def test_one_port_in_two_roles_is_fine(tmp_path):
    """The saved patchbay feeds one capture port to both Sushi and the tuner."""
    machine = _load(
        tmp_path,
        "schema_version: 1\nroles:\n  guitar-in: ['A:x']\n  tuner-in: ['A:x']\n",
    )
    assert machine.validate() == []


def test_a_non_string_role_name_is_a_problem(tmp_path):
    """`yes:` is a boolean to YAML."""
    problems = _load(tmp_path, "schema_version: 1\nroles:\n  yes: ['A:x']\n").validate()
    assert len(problems) == 1 and "True" in problems[0]


def test_every_problem_is_reported_in_one_pass(tmp_path):
    machine = _load(
        tmp_path,
        """
        schema_version: 1
        interface: 3
        roles:
          a: []
          b: "A:x"
          c: ["bad"]
        """,
    )
    assert len(machine.validate()) == 4


# --- the file itself ----------------------------------------------------------


def test_a_file_that_is_not_a_mapping_cannot_be_validated(tmp_path):
    with pytest.raises(MachineError, match="expected a mapping"):
        _load(tmp_path, "- a\n- b\n")


def test_invalid_yaml_is_a_machine_error(tmp_path):
    with pytest.raises(MachineError, match="not valid YAML"):
        _load(tmp_path, "roles: [unclosed\n")


def test_a_missing_file_is_a_machine_error(tmp_path):
    with pytest.raises(MachineError, match="cannot read"):
        MachineSpec.load(tmp_path / "absent.yaml")


def test_the_machine_file_is_named_by_hostname():
    assert machine_path(Path("/r"), "studio") == Path("/r/machines/studio.yaml")
