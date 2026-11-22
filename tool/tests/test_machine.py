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


# --- the long role form: declaring mono_to_stereo --------------------------------

LONG_FORM = """
schema_version: 1
roles:
  guitar-in:
    ports: ["A:x"]
    mono_to_stereo: true
  stereo-in:
    ports: ["A:l", "A:r"]
  monitor-out: ["A:pl", "A:pr"]
"""


def test_the_long_and_plain_forms_both_load(tmp_path):
    machine = _load(tmp_path, LONG_FORM)
    assert machine.validate() == []
    assert machine.role_ports() == {
        "guitar-in": ["A:x"], "stereo-in": ["A:l", "A:r"], "monitor-out": ["A:pl", "A:pr"],
    }


def test_only_a_role_that_says_so_is_mono(tmp_path):
    """Never inferred from the port count: `stereo-in` and a plain one-port role
    are not mono because of how many ports they have."""
    machine = _load(tmp_path, LONG_FORM + "  one-port: ['A:z']\n")
    assert machine.mono_roles() == {"guitar-in"}


def test_mono_to_stereo_on_a_role_with_two_ports_is_a_problem(tmp_path):
    machine = _load(
        tmp_path,
        "schema_version: 1\nroles:\n  r:\n    ports: ['A:l', 'A:r']\n    mono_to_stereo: true\n",
    )
    assert machine.validate() == ["role 'r': mono_to_stereo needs exactly one port (got 2)"]


def test_mono_to_stereo_must_be_a_boolean(tmp_path):
    """`"yes"` is truthy and would be taken as a declaration."""
    machine = _load(
        tmp_path, "schema_version: 1\nroles:\n  r:\n    ports: ['A:x']\n    mono_to_stereo: 'yes'\n"
    )
    problems = machine.validate()
    assert len(problems) == 1 and "must be true or false" in problems[0]
    assert machine.mono_roles() == set()


def test_a_long_form_role_without_ports_is_a_problem(tmp_path):
    machine = _load(tmp_path, "schema_version: 1\nroles:\n  r:\n    mono_to_stereo: true\n")
    assert machine.validate() == ["role 'r': has no 'ports' key"]


def test_a_misspelt_key_in_a_long_form_role_is_named(tmp_path):
    """`mono_to_stero: true` would otherwise be ignored, and the role would then
    fail later as an undeclared one-port role with no hint of the cause."""
    machine = _load(
        tmp_path, "schema_version: 1\nroles:\n  r:\n    ports: ['A:x']\n    mono_to_stero: true\n"
    )
    problems = machine.validate()
    assert len(problems) == 1 and "unknown key 'mono_to_stero'" in problems[0]


def test_long_form_ports_are_validated_like_plain_ones(tmp_path):
    machine = _load(tmp_path, "schema_version: 1\nroles:\n  r:\n    ports: []\n")
    assert machine.validate() == ["role 'r': has no ports"]
    machine = _load(tmp_path, "schema_version: 1\nroles:\n  r:\n    ports: ['bad']\n")
    assert "<client>:<port> form" in machine.validate()[0]


# --- controllers (#34) ------------------------------------------------------


def _with_controllers(controllers):
    return MachineSpec(schema_version=1, interface="x",
                       roles={"guitar-in": {"ports": ["A:in"], "mono_to_stereo": True}, "monitor-out": ["A:l", "A:r"]},
                       controllers=controllers)


def test_a_controller_describes_what_each_control_sends():
    machine = _with_controllers({"mpd218": {"port": "MPD218:MPD218 Port A", "channel": 0,
                                            "controls": {"knob1": {"cc": 3}, "pad1": {"note": 36}}}})
    assert machine.validate() == []
    assert machine.controller("mpd218") == {
        "port": "MPD218:MPD218 Port A", "channel": 0,
        "controls": {"knob1": ("cc", 3), "pad1": ("note", 36)},
    }
    assert _with_controllers({"x": {"controls": {"k": {"cc": 1}}}}).controller("x")["channel"] == "all"


@pytest.mark.parametrize("controllers, said", [
    ("mpd218", "controllers must be a mapping"),
    ({"x": "cc 3"}, "must be a mapping with controls"),
    ({"x": {"controls": {}}}, "controls must be a mapping"),
    ({"x": {"controls": {"k": {"cc": 3, "note": 1}}}}, "exactly one of cc"),
    ({"x": {"controls": {"k": {"pc": 3}}}}, "exactly one of cc"),
    ({"x": {"controls": {"k": {"cc": 128}}}}, "cc must be 0 to 127"),
    ({"x": {"channel": 16, "controls": {"k": {"cc": 3}}}}, "channel must be 0 to 15"),
    ({"x": {"ports": "y", "controls": {"k": {"cc": 3}}}}, "unknown key 'ports'"),
    ({"x": {"port": 3, "controls": {"k": {"cc": 3}}}}, "port must be the MIDI port's name"),
])
def test_a_controller_that_is_not_one_is_named(controllers, said):
    problems = _with_controllers(controllers).validate()
    assert any(said in p for p in problems), problems


def test_two_controls_that_send_the_same_number_are_refused_naming_both():
    """A rig binding both would put two mappings on one pedal, each firing on
    the same message, with nothing to say so."""
    machine = _with_controllers({"x": {"controls": {"knob1": {"cc": 3}, "pedal": {"cc": 3}}}})
    problems = machine.validate()
    assert len(problems) == 1, problems
    assert "controller 'x'" in problems[0]
    assert "'knob1'" in problems[0] and "'pedal'" in problems[0] and "cc 3" in problems[0]
    machine = _with_controllers({"x": {"controls": {"a": {"note": 36}, "b": {"note": 36}}}})
    assert "note 36" in machine.validate()[0]


def test_a_cc_and_a_note_of_one_number_are_different_messages_and_allowed():
    machine = _with_controllers({"x": {"controls": {"knob": {"cc": 36}, "pad": {"note": 36}}}})
    assert machine.validate() == []
    # The same number on two controllers is two pedals, not a clash.
    machine = _with_controllers({"x": {"controls": {"k": {"cc": 3}}}, "y": {"controls": {"k": {"cc": 3}}}})
    assert machine.validate() == []


def test_a_loaded_machine_answers_for_its_controller_and_refuses_one_it_lacks():
    machine = _with_controllers({"x": {"controls": {"k": {"cc": 3}}}})
    assert machine.controller_for("x")["controls"] == {"k": ("cc", 3)}
    with pytest.raises(MachineError, match="controller 'y', which the machine file does not describe; its controllers: x"):
        machine.controller_for("y")


def test_a_loaded_machine_remembers_where_it_was_read_from_and_names_it(tmp_path):
    path = tmp_path / "box.yaml"
    path.write_text("schema_version: 1\ninterface: x\nroles:\n  r: {ports: ['A:in']}\n"
                    "controllers:\n  x:\n    controls:\n      k: {cc: 3}\n")
    machine = MachineSpec.load(path)
    assert machine.source == path
    with pytest.raises(MachineError, match="which box.yaml does not describe"):
        machine.controller_for("y")


def test_rig_controller_is_one_helper_for_every_caller(tmp_path):
    from types import SimpleNamespace

    from sushi_rig.machine import rig_controller

    machine_file = tmp_path / "box.yaml"
    machine_file.write_text(
        "schema_version: 1\ninterface: x\nroles:\n  r: {ports: ['A:in']}\n"
        "controllers:\n  x:\n    controls:\n      k: {cc: 3}\n"
    )
    mapped = SimpleNamespace(midi=SimpleNamespace(controller="x"))
    assert rig_controller(SimpleNamespace(midi=None), machine_file) is None
    assert rig_controller(mapped, machine_file)["controls"] == {"k": ("cc", 3)}
    # An already-loaded machine is used as it is, whatever the file does next.
    loaded = MachineSpec.load(machine_file)
    machine_file.unlink()
    assert rig_controller(mapped, loaded)["controls"] == {"k": ("cc", 3)}
    with pytest.raises(MachineError, match="could not be read"):
        rig_controller(mapped, machine_file)


def test_the_rig_machines_file_describes_the_mpd218():
    from rig_fixtures import FROZEN_MACHINE  # the file as it was when the MPD218 was measured

    machine = MachineSpec.load(FROZEN_MACHINE)
    assert machine.validate() == []
    assert machine.controller("mpd218")["controls"]["knob1"] == ("cc", 3)


# --- the device model (#191) ------------------------------------------------

def _fbv(**controls):
    base = {
        "a": {"cc": 20, "kind": "switch", "behaviour": "momentary", "job": "previous-rig"},
        "b": {"cc": 21, "kind": "switch", "behaviour": "momentary", "job": "next-rig"},
        "c": {"cc": 22, "kind": "switch", "behaviour": "latching"},
        "pedal": {"cc": 7, "kind": "expression", "range": [3, 125]},
    }
    base.update(controls)
    return {"fbv": {"type": "pedal", "port": "FBV:FBV MIDI 1", "channel": 0,
                    "controls": {k: v for k, v in base.items() if v is not None}}}


def test_a_controller_with_a_type_describes_each_controls_kind_behaviour_range_and_job():
    machine = _with_controllers(_fbv())
    assert machine.validate() == []
    described = machine.controller("fbv")
    assert described["type"] == "pedal"
    assert described["controls"]["pedal"] == ("cc", 7), "what it sends, as for any controller"
    assert described["model"] == {
        "a": {"kind": "switch", "behaviour": "momentary", "range": None, "job": "previous-rig"},
        "b": {"kind": "switch", "behaviour": "momentary", "range": None, "job": "next-rig"},
        "c": {"kind": "switch", "behaviour": "latching", "range": None, "job": None},
        "pedal": {"kind": "expression", "behaviour": None, "range": [3, 125], "job": None},
    }
    assert _with_controllers(_fbv(pedal={"cc": 7, "kind": "expression"})).controller("fbv")["model"]["pedal"][
        "range"] == [0, 127], "a pedal's travel is the full 0 to 127 unless said"


def test_the_controllers_that_step_rigs_and_every_device_for_the_page():
    machine = _with_controllers({**_fbv(), "mpd218": {"controls": {"knob1": {"cc": 3}}}})
    assert list(machine.steppers()) == ["fbv"]
    devices = {d["name"]: d for d in machine.devices()}
    assert devices["mpd218"] == {"name": "mpd218", "type": None, "port": None, "channel": "all", "controls": [
        {"name": "knob1", "sends": {"cc": 3}, "kind": None, "behaviour": None, "range": None, "job": None}]}
    assert devices["fbv"]["controls"][0] == {"name": "a", "sends": {"cc": 20}, "kind": "switch",
                                             "behaviour": "momentary", "range": None, "job": "previous-rig"}


@pytest.mark.parametrize("controllers, said", [
    ({"x": {"type": "keyboard", "controls": {"k": {"cc": 1, "kind": "switch", "behaviour": "momentary"}}}},
     "type must be one of pedal"),
    (_fbv(c={"cc": 22}), "kind must be switch or expression"),
    (_fbv(c={"cc": 22, "kind": "knob"}), "kind must be switch or expression"),
    (_fbv(c={"cc": 22, "kind": "switch"}), "behaviour must be momentary, single, latching"),
    (_fbv(c={"cc": 22, "kind": "switch", "behaviour": "sticky"}), "behaviour must be momentary, single, latching"),
    (_fbv(c={"note": 40, "kind": "switch", "behaviour": "latching"}), "a note is a press"),
    (_fbv(c={"cc": 22, "kind": "switch", "behaviour": "momentary", "range": [0, 127]}), "a switch has none"),
    (_fbv(c={"cc": 22, "kind": "switch", "behaviour": "momentary", "job": "tuner"}), "job must be previous-rig or next-rig"),
    (_fbv(c={"cc": 22, "kind": "switch", "behaviour": "momentary", "job": "next-rig"}), "are both next-rig"),
    (_fbv(pedal={"note": 7, "kind": "expression"}), "sends a cc, not a note"),
    (_fbv(pedal={"cc": 7, "kind": "expression", "behaviour": "momentary"}), "an expression pedal has none"),
    (_fbv(pedal={"cc": 7, "kind": "expression", "range": [100, 20]}), "heel below toe"),
    (_fbv(pedal={"cc": 7, "kind": "expression", "range": [0, 128]}), "range must be [heel, toe]"),
    (_fbv(pedal={"cc": 7, "kind": "expression", "colour": "red"}), "unknown key 'colour'"),
    ({"x": {"controls": {"k": {"cc": 3, "kind": "switch"}}}}, "give 'x' one (type: pedal)"),
    ({"x": {"type": "pedal", "controls": {"a": {"cc": 1, "kind": "switch", "behaviour": "momentary",
                                                "job": "next-rig"}}}}, "needs the port"),
])
def test_a_device_model_that_is_not_one_is_named(controllers, said):
    problems = _with_controllers(controllers).validate()
    assert any(said in p for p in problems), problems


@pytest.mark.parametrize("name", ["example.yaml", "sonny-dewfall-XPS-9320.yaml"])
def test_the_committed_machine_files_describe_the_fbv_as_aseqdump_heard_it(name):
    """The FBV Express MkII as captured on the laptop on 2026-11-22, after
    FBV Control set it up (machines/fbv/sushi-rig-line-6.fbv): A to D on CC
    20 to 23, each a single message a press (CC n = 0, then CC 0 = 0 on
    release); the pedal CC 7 in Vol and CC 11 in Wah, its heel 8 to 12; the
    toe switch CC 43 toggling 127 / 0. No guess is left marked unverified."""
    path = checkout_root() / "machines" / name
    machine = MachineSpec.load(path)
    assert machine.validate() == []
    fbv = machine.controller("fbv")
    assert fbv["type"] == "pedal" and fbv["channel"] == 0
    assert fbv["port"] == "FBV Express Mk II:FBV Express Mk II MIDI 1"
    assert fbv["controls"] == {"a": ("cc", 20), "b": ("cc", 21), "c": ("cc", 22), "d": ("cc", 23),
                               "pedal": ("cc", 7), "wah": ("cc", 11), "toe": ("cc", 43)}
    assert {c: m["job"] for c, m in fbv["model"].items() if m["job"]} == {"a": "previous-rig", "b": "next-rig"}
    assert {c: (m["kind"], m["behaviour"]) for c, m in fbv["model"].items()} == {
        "a": ("switch", "single"), "b": ("switch", "single"), "c": ("switch", "single"),
        "d": ("switch", "single"), "pedal": ("expression", None), "wah": ("expression", None),
        "toe": ("switch", "latching")}
    # The heel is where the agent silences a volume: the pedal never reads
    # below 8 and rests up to 12 there, so a heel of 0 would never be reached.
    assert fbv["model"]["pedal"]["range"] == [12, 127] and fbv["model"]["wah"]["range"] == [12, 127]
    text = path.read_text()
    assert "unverified" not in text[text.index("  fbv:"):]
    assert (checkout_root() / "machines" / "fbv" / "sushi-rig-line-6.fbv").read_bytes().startswith(b"FBVP")


def test_the_rig_machines_mpd218_is_unchanged_beside_the_fbv():
    machine = MachineSpec.load(checkout_root() / "machines" / "sonny-dewfall-XPS-9320.yaml")
    assert machine.controller("mpd218") == {
        "port": "MPD218:MPD218 Port A", "channel": "all",
        "controls": {"knob1": ("cc", 3), "pad1": ("note", 36), "pad2": ("note", 38),
                     "pad3": ("note", 40), "pad4": ("note", 41)},
    }


def test_a_single_switch_is_a_switch_and_a_note_may_be_one():
    machine = _with_controllers(_fbv(c={"cc": 22, "kind": "switch", "behaviour": "single"},
                                     d={"note": 40, "kind": "switch", "behaviour": "single"}))
    assert machine.validate() == []
    assert machine.controller("fbv")["model"]["c"]["behaviour"] == "single"
