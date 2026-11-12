"""The rule of rig_fixtures.py, held (2026-11-12).

Two things a test may ask of a rig file, and where it asks them:

- Of every rig in config/rigs/, that it is a rig `up --rig` would bring up
  as its file says. That is all. Those files are the owner's, saved from the
  web UI and committed, and a test that pinned what one held (its channels,
  its amp's levels, how many there are) failed on the day one was saved, with
  nothing wrong: eight did, on 2c36874.
- Of the frozen set under fixtures/, anything: it is test data, and the tests
  that name a rig mean one of these.

So this file: checks each committed rig for validity, and the committed
machine file; checks the frozen rigs are still valid rigs to the code as it
is now (a fixture that stopped compiling would fail forty tests elsewhere
with forty reasons); pins every frozen file by checksum; shows that an
invalid rig is refused, on copies made invalid three ways; and holds the
guard on config/rigs/ to working.

**What is not checked without a Sushi**: that a parameter a rig names is one
its plugin has. Nothing in the checkout lists a plugin's parameters
(plugin-ports.txt holds audio ports only), so that is `verify`'s names check
against Sushi's own dump, which runs in the container and skips on a bare
runner (`needs_sushi`, as test_configs.py does for the v1 configs;
`verify --all` covers config/*.json alone). The v1 comparison in
test_convert used to catch a misspelt parameter in a converted rig by
accident; it is of the frozen conversions now.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path

import pytest
import rig_fixtures
import yaml
from rig_fixtures import (
    COMMITTED, COMMITTED_MACHINE, FIXTURES, FROZEN, FROZEN_MACHINE, STRAY, committed, committed_that, compiled,
    frozen, frozen_files, frozen_names, valid, valid_committed,
)

from sushi_rig import rig, rigfile
from sushi_rig.machine import MachineSpec
from sushi_rig.patchbay import parse_patchbay, patchbay_for
from sushi_rig.paths import checkout_root

ROOT = checkout_root()
TESTS = Path(__file__).parent
SUSHI = ROOT / "sushi"
# A real pw-dump from the rig machine: the graph the frozen machine file describes.
CAPTURED_DUMP = (FIXTURES / "pw-dump-rig.json").read_text()

needs_sushi = pytest.mark.skipif(
    not SUSHI.exists() or not (ROOT / "plugins").is_dir(),
    reason="needs the Sushi binary and the rig's plugins",
)


def _wired(xml: str, channels: list[str], name: str) -> None:
    links = parse_patchbay(xml)
    into_sushi = {link.in_port for link in links if link.in_port.startswith("sushi:audio_input")}
    out_of_sushi = {link.out_port for link in links if link.out_port.startswith("sushi:audio_output")}
    assert len(into_sushi) >= len(channels), f"{name}: a channel has no source wired to it: {sorted(into_sushi)}"
    assert out_of_sushi, f"{name}: nothing is wired out of Sushi"


# --------------------------------------------------------------------------- #
# The committed rigs: valid, and nothing else asked
# --------------------------------------------------------------------------- #

def test_there_are_committed_rigs_and_the_frozen_ones_are_the_eleven():
    """Not vacuous. With config/rigs/ emptied, every test over the committed
    rigs has no cases and the suite passed, 444 tests lighter and none the
    wiser (review, 2026-11-12): so an empty folder fails here. And the
    frozen rigs are the eleven of c494625 by name: one gone or renamed fails
    here, once, and not as a missing file in whichever test asked first."""
    assert committed(), "config/rigs/ holds no rig, so nothing was checked of the committed rigs"
    assert frozen_names() == [
        "accustomed", "electric-chorus", "electric-chorus-2", "electric-chorus-hall", "electric-clean",
        "electric-delay", "electric-octave-up", "electric-spacey-delay", "electric_board", "guitar_vocal",
        "hard-feelings",
    ]


@pytest.mark.committed_rigs
@pytest.mark.parametrize("path", committed(), ids=lambda p: p.stem)
def test_every_committed_rig_parses_compiles_and_wires(path):
    """The whole of what the fast suite asks of a rig in config/rigs/ by
    itself: it parses and compiles, its amp's model is a file and its amp's
    parameters ones the amp takes, in range (`rig_fixtures.valid`); and, as
    `up --rig` compiles it for the committed machine file, every channel has
    a source and the master a sink. A failure here is a rig that would not
    come up as written, and is the rig's to fix, or the compiler's; it is
    never that the rig changed.

    Wired with no graph (`patchbay_for`'s offline form): the one capture
    there is (fixtures/pw-dump-rig.json) is of the interface the frozen
    machine file describes, and the committed one may come to describe
    another."""
    config = valid(path)
    loaded = rigfile.load_rig(path)
    channels = [c.name for c in loaded.channels]
    assert loaded.name and channels, f"{path.name}: a rig has a name and a channel"
    assert [t["name"] for t in config["tracks"]] == channels + [rigfile.MASTER]
    # RigError says why not: a controller the machine file lacks, a role, a plugin.
    as_up = rig.compile_named_rig(path.stem, ROOT, COMMITTED_MACHINE)
    assert [t["name"] for t in as_up["tracks"]] == channels + [rigfile.MASTER]
    xml, _ = patchbay_for(rigfile.spec_of(path, routing_only=True), MachineSpec.load(COMMITTED_MACHINE), None,
                          rig_name=path.name, machine_name=COMMITTED_MACHINE.name)
    _wired(xml, channels, path.name)


@needs_sushi
@pytest.mark.parametrize("path", valid_committed(), ids=lambda p: p.stem)
def test_every_parameter_a_committed_rig_names_is_one_its_plugin_has(path, tmp_path):
    """`verify`'s names check, against Sushi's own list of each plugin's
    parameters: the half of validity no file here can answer. A rig is
    compiled as a run loads it and given to `verify` as the config it is,
    since `verify --all` reads config/*.json and a rig file given to it is
    compiled for this host's machine file, which the container has none of."""
    from sushi_rig.verify import verify

    config = tmp_path / f"{path.stem}.json"
    config.write_text(json.dumps(compiled(path), indent=2))
    problems = verify(config, str(SUSHI), quick=True)
    assert not problems, f"{path.name}:\n" + "\n".join(f"  {p}" for p in problems)


def test_the_committed_machine_file_is_a_machine_file():
    """The committed rigs are wired against it, so it is held to what they
    are: valid, and nothing pinned of what it holds. (The tests that know a
    port's name read the frozen copy.)"""
    machine = MachineSpec.load(COMMITTED_MACHINE)
    assert machine.validate() == []
    assert machine.role_ports(), "no role names a port"


# --------------------------------------------------------------------------- #
# A rig that is not one is refused: three, made from a frozen rig
# --------------------------------------------------------------------------- #

def _changed(frozen_root: Path, name: str, change) -> Path:
    """Rig `name` of a frozen checkout, with `change` made to its document."""
    path = frozen_root / "config" / "rigs" / f"{name}.yaml"
    raw = yaml.safe_load(path.read_text())
    change(raw)
    path.write_text(yaml.safe_dump(raw, sort_keys=False))
    return path


def test_a_rig_whose_amp_model_is_not_there_is_not_valid(frozen_root):
    """`up` resolves the model after it has prepared the rig (`amp.prepare`),
    and refuses: Carla started on a missing model looks healthy and plays
    silence. Unchanged, the same rig is valid."""
    assert valid(frozen_root / "config" / "rigs" / "electric-clean.yaml", frozen_root)
    path = _changed(frozen_root, "electric-clean", lambda raw: raw["amp"].update(model="amp/models/Not There.nam"))
    with pytest.raises(AssertionError, match=r"electric-clean.yaml: amp model not found: .*Not There\.nam"):
        valid(path, frozen_root)


@pytest.mark.parametrize("parameters, said", [
    ({"Output Lvl": 99.0}, r"amp parameter Output Lvl is 99.0, outside -20.0 to 20.0: the amp would be sent 20.0"),
    ({"Quality": -1}, r"amp parameter Quality is -1, outside 0.0 to 1.0"),
    ({"Gain": 1.0}, r"amp parameter 'Gain' is not one the amp has"),
    ({"Input Lvl": "loud"}, r"amp parameter Input Lvl is 'loud', not a number"),
    ({"Input Lvl": float("nan")}, r"Input Lvl got nan"),
])
def test_a_rig_whose_amp_is_set_to_what_the_amp_cannot_be_is_not_valid(frozen_root, parameters, said):
    """A level past the amp's range is sent clamped, so the rig would not
    play what its file says. It used to pass here and fail seven tests of
    pairs instead, none of them about this rig (review, 2026-11-12)."""
    path = _changed(frozen_root, "electric-clean", lambda raw: raw["amp"]["parameters"].update(parameters))
    with pytest.raises(AssertionError, match="electric-clean.yaml: " + said):
        valid(path, frozen_root)


def test_a_rig_that_is_not_valid_is_left_out_of_the_tests_over_them_all(frozen_root, monkeypatch):
    """`committed_that` is how a module takes every committed rig as it is
    imported: one that is not valid fails its own test above, by name, and
    is not handed to the tests of pairs, probes and the rest."""
    rigs = frozen_root / "config" / "rigs"
    _changed(frozen_root, "electric-clean", lambda raw: raw["amp"]["parameters"].update({"Output Lvl": 99.0}))
    (rigs / "broken.yaml").write_text("kind: [rig\n")
    monkeypatch.setattr(rig_fixtures, "COMMITTED", rigs)
    monkeypatch.setattr(rig_fixtures, "checkout_root", lambda: frozen_root)
    names = sorted(path.stem for path, _ in committed_that(compiled))
    assert names == [n for n in frozen_names() if n != "electric-clean"]


@needs_sushi
def test_a_rig_naming_a_parameter_its_plugin_lacks_is_caught_by_the_names_check(frozen_root, tmp_path):
    """The trial that passed the whole fast suite (review, 2026-11-12):
    electric-clean with the compressor's RATIO spelt as something it has
    not. Sushi's own list is what knows, so this runs where Sushi does."""
    from sushi_rig.verify import verify

    def misspelt(raw):
        (compressor,) = [p for p in raw["channels"][0]["chain"] if p["name"] == "compressor"]
        compressor["parameters"]["RATIOO_NOT_A_PARAMETER"] = compressor["parameters"].pop("RATIO")

    path = _changed(frozen_root, "electric-clean", misspelt)
    config = tmp_path / "electric-clean.json"
    config.write_text(json.dumps(compiled(path), indent=2))
    problems = [str(p) for p in verify(config, str(SUSHI), quick=True)]
    assert len(problems) == 1 and "compressor" in problems[0] and "RATIOO_NOT_A_PARAMETER" in problems[0]


# --------------------------------------------------------------------------- #
# The frozen set: still rigs, and still what was frozen
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("name", frozen_names())
def test_every_frozen_rig_is_still_a_rig(name, frozen_root, tmp_path):
    """As the committed rigs are held, and then all the way `up --rig` takes
    one before anything starts (`prepare_rig`): compiled for the frozen
    machine file and wired against the graph captured from that machine."""
    path = frozen_root / "config" / "rigs" / f"{name}.yaml"
    config = valid(path, frozen_root)
    channels = [c.name for c in rigfile.load_rig(path).channels]
    assert [t["name"] for t in config["tracks"]] == channels + [rigfile.MASTER]
    out = rig.prepare_rig(name, frozen_root, tmp_path / "run",
                          machine_file=frozen_root / "machines" / FROZEN_MACHINE.name, dump=CAPTURED_DUMP)
    assert [t["name"] for t in json.loads(out["config"].read_text())["tracks"]] == channels + [rigfile.MASTER]
    assert out["warnings"] == []
    _wired(out["patchbay"].read_text(), channels, path.name)


# sha256 of every file of the frozen set, as each was at c494625. A frozen
# file is changed on purpose or not at all: with the code that reads it, in
# the same commit, and with its line here.
CHECKSUMS = {
    "machines/sonny-dewfall-XPS-9320.yaml": "f90c8f537dae347caf32ff648d2b3ac1f72a7d3fd4d15f1113a1bc431631f82a",
    "rigs/accustomed.yaml": "f7bedabee55a8775483d97b690e0007b69c5efdd19f7c15f3244663088e1632b",
    "rigs/electric-chorus-2.yaml": "663ffb98cdbe1359985b9450e5e811218cd8674066d693454ce14dc957e08f46",
    "rigs/electric-chorus-hall.yaml": "835c903ecbf7c22b0fbec7a47c47d3a3b312da398ea4bc5b68ed165cd037609a",
    "rigs/electric-chorus.yaml": "8f85343d9fa44de945f69a3e9e660b3b2b156798084cb6eed059995ba2611d6f",
    "rigs/electric-clean.yaml": "278c70c0b9681fee5b23b1980a7ab93df48b013aec942bd87d1792e5af75f990",
    "rigs/electric-delay.yaml": "f7585ad8496a36e235b759aa7aca28a838ef890f7a679ec83a0fe9051bbfacdd",
    "rigs/electric-octave-up.yaml": "00d6d49fa31cc9da6303a0d1c7b61c05760b47f6d3cdc50d515438ac6ea6e99c",
    "rigs/electric-spacey-delay.yaml": "7f166b6b2d6ee8b5aa4905cb64e6ffa7d20427eb550372618eaef6d44d0269a0",
    "rigs/electric_board.yaml": "aabdec2cf0c0a355e15807767aa8f620be5290ea2249b9387f51d1abaed2e934",
    "rigs/guitar_vocal.yaml": "547be75454da3f20320b069cfa7253541b6d813cda9a0b4604d86f407fdd9add",
    "rigs/hard-feelings.yaml": "3164fc95cebc3694b93d5232d57dd0fa31cf8acde0f3e80e6a9d42dbf89443dc",
    "v1/accustomed.json": "cd6c2d93ff324ad95bc9933165ea7ce46ec9c2bce39507e10c30ab9e271b2a21",
    "v1/electric-chorus-2.json": "274f10cf0067c4457b06f7f2b7dc458e2d5868c400bc80e6ceb9d9a757670ac6",
    "v1/electric-chorus-hall.json": "d159fa3e4b1e751c8ce9e6ec7705f81c09bcc4d9b26dbe3365388e1e4ddcf354",
    "v1/electric-chorus.json": "ae00ee2e75bcc6f526cbc14c06695ba5e4ba7fd62acd84f29d5c0142241e62ed",
    "v1/electric-clean.json": "d6f1e618265db6fe21a70f71c4b688179b22de98df9ffe6639252e59c2681d8b",
    "v1/electric-delay.json": "2e1d0549e938104cf4df590fbc34dad70315f2419e2ae684cfa21b595162f243",
    "v1/electric-octave-up.json": "1312871ebb3d1d996502660a40325a7742ce64953d984cfacf708028696ffa35",
    "v1/electric-spacey-delay.json": "67665301651de6efab85082e9ea19ac9fe41445a2501acfcd8d4c4052634afb4",
    "v1/electric_board.json": "6c17f4e62d367da3c7ec988238f0417c0c80c46ed5cfd4a8ab8c72eea37786f2",
    "v1/guitar_vocal.json": "673a83bfee2dd5ff08969a06f8b608053b711d6d62d2dda241327bf8dfbb1010",
    "v1/hard-feelings.json": "255f73f7357e479aa350eccced111f4fb325391d4f8acbefb7bc4b3ed4500d55",
    "v1/src/electric_board.yaml": "8447844a055ca5043beac7d4cb00a49fce5ad0d6c12331c2b468033fdf7457c3",
    "v1/src/electric_stereo.yaml": "9741960288a45bbd51cebadd705ee697562d52dc25b92a0d8ba6ac5eb7af35e7",
    "v1/src/guitar_vocal.yaml": "113f13cde244aaaaf53ba8f063279a13467b31d36a04e0764a7ae1ffad9526b6",
}


@pytest.mark.parametrize("name", sorted(CHECKSUMS))
def test_a_frozen_file_is_byte_for_byte_what_was_frozen(name):
    """An edit to one, meant for config/ or made by a tool that rewrites
    YAML, fails here under the file's own name, and not as whichever of the
    tests that read it noticed first."""
    path = FIXTURES / name
    assert path.is_file(), f"fixtures/{name} is gone"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == CHECKSUMS[name], (
        f"fixtures/{name} is not the file that was frozen. If the change is meant (the code that "
        "reads it changed), put its new sha256 in CHECKSUMS in the same commit; if not, restore it"
    )


def test_every_frozen_file_has_a_checksum_and_nothing_else_does():
    assert sorted(path.relative_to(FIXTURES).as_posix() for path in frozen_files()) == sorted(CHECKSUMS)


def test_the_frozen_rigs_are_copies_and_not_the_committed_files():
    """Not links into config/rigs/, which would follow every save."""
    assert FROZEN.resolve() != COMMITTED.resolve()
    for path in frozen_files():
        assert path.is_file() and not path.is_symlink(), path.name
        assert ROOT.resolve() / "config" not in path.resolve().parents, path.name
        assert ROOT.resolve() / "machines" not in path.resolve().parents, path.name


# --------------------------------------------------------------------------- #
# The guard on config/rigs/ (rig_fixtures.py, conftest.py)
# --------------------------------------------------------------------------- #

def _strays_made(read) -> list[tuple[str, str, str]]:
    """What the guard recorded while `read` ran, taken back off its list so
    that this test is not failed for the reads it made to try it. A read
    that finds no file is a read all the same: the guard hears of an open
    before it is tried."""
    before = len(STRAY)
    try:
        read()
    except (OSError, rig.RigError, rigfile.RigFileError):
        pass
    made = STRAY[before:]
    del STRAY[before:]
    return made


def _a_committed_name() -> str:
    """A rig file's name in config/rigs/: one that is there, or, on the day
    the folder is empty (which is another test's to fail on), one that is not."""
    return (committed() or [COMMITTED / "not-there.yaml"])[0].name


def test_no_test_module_reads_config_rigs_as_it_is_imported_but_through_rig_fixtures():
    """A module that reads the committed rigs to parametrize over them does
    it through `rig_fixtures.committed_that`, `compiled` and the like, which
    take every rig and leave out one that is not valid. One that opens a rig
    file itself at import is how a save that renamed a plugin would stop the
    whole run at collection."""
    assert rig_fixtures.AT_COLLECTION == [], rig_fixtures.said(rig_fixtures.AT_COLLECTION)


def test_the_guard_sees_config_rigs_read_by_any_spelling_and_through_code_given_the_root():
    """The ways the first guard, a search of the tests' source for a path,
    did not see (review, 2026-11-12), each tried: every one opens or lists
    the folder, whatever the line that asked was spelt like."""
    from sushi_rig.render import rigs_to_check

    name = _a_committed_name()
    listed = _strays_made(lambda: sorted((ROOT / "config/rigs").glob("*.yaml")))
    assert listed and {(event in ("os.scandir", "os.listdir"), path) for event, path, _ in listed} == {
        (True, os.path.realpath(COMMITTED))}
    assert listed[0][2].startswith("test_rig_fixtures.py:"), "the failure names the test's own line"
    ways = {
        "COMMITTED / name": lambda: (COMMITTED / name).read_text(),
        "root = checkout_root(), then root / ...": lambda: (lambda root: (root / "config" / "rigs" / name).read_bytes())(ROOT),
        "joinpath": lambda: ROOT.joinpath("config", "rigs", name).read_text(),
        "a string": lambda: open(os.path.join(str(ROOT), "config", "rigs", name)).close(),
        "from this file's own place": lambda: (Path(__file__).resolve().parents[2] / "config" / "rigs" / name).read_text(),
        "load_rig": lambda: rigfile.load_rig(COMMITTED / name),
        "rigs_to_check(ROOT)": lambda: rigs_to_check(ROOT),
    }
    if committed():  # it asks whether the file is there before it opens it
        ways["compile_named_rig(name, ROOT)"] = lambda: rig.compile_named_rig(Path(name).stem, ROOT, COMMITTED_MACHINE)
    for way, read in ways.items():
        made = _strays_made(read)
        assert made and all(path.startswith(os.path.realpath(COMMITTED)) for _, path, _ in made), way


def test_the_guard_sees_a_relative_path_read_from_inside_the_folder(monkeypatch):
    """A path with no "rigs" in it, relative to a working directory that is
    config/rigs/: passed unseen while only the path as given was looked at
    (review of 0ae6593)."""
    monkeypatch.chdir(COMMITTED)
    made = _strays_made(lambda: open(_a_committed_name()).close())
    assert [event for event, _, _ in made] == ["open"]
    assert made[0][1] == os.path.join(os.path.realpath(COMMITTED), _a_committed_name())
    monkeypatch.chdir(TESTS)
    assert _strays_made(lambda: open("conftest.py").close()) == [], "and a relative path anywhere else is let be"


def test_a_read_no_test_was_failed_for_fails_the_run_when_it_ends():
    """A module-, class- or session-scoped fixture is set up before a test's
    own guard looks and torn down after it, so what it reads of config/rigs/
    is left on the list with no test to fail (review of 0ae6593: a module
    fixture read every rig, and the run passed). conftest's
    `pytest_sessionfinish` fails the run for whatever is left, and the
    summary says where each came from."""
    import conftest

    class Session:
        exitstatus = pytest.ExitCode.OK

    assert rig_fixtures.left_unreported() is None, "nothing is left over as this test starts"
    session = Session()
    conftest.pytest_sessionfinish(session, session.exitstatus)
    assert session.exitstatus == pytest.ExitCode.OK
    before = len(STRAY)
    (COMMITTED / _a_committed_name()).exists() and (COMMITTED / _a_committed_name()).read_text()
    sorted(COMMITTED.glob("*.yaml"))
    try:
        assert len(STRAY) > before
        message = rig_fixtures.left_unreported()
        assert message.startswith("Not reported by any test") and "test_rig_fixtures.py:" in message
        assert "os.scandir config/rigs" in message or "os.listdir config/rigs" in message
        conftest.pytest_sessionfinish(session, session.exitstatus)
        assert session.exitstatus == pytest.ExitCode.TESTS_FAILED
        failed = Session()
        failed.exitstatus = pytest.ExitCode.INTERRUPTED
        conftest.pytest_sessionfinish(failed, failed.exitstatus)
        assert failed.exitstatus == pytest.ExitCode.INTERRUPTED, "a run that failed some other way keeps its own status"
    finally:
        del STRAY[before:]  # this test's own, made to try it


def test_the_guard_leaves_the_frozen_rigs_a_copy_of_them_and_the_readers_alone(frozen_root, tmp_path):
    def reads():
        frozen("guitar_vocal").read_text()
        sorted((frozen_root / "config" / "rigs").glob("*.yaml"))
        rigfile.load_rig(frozen_root / "config" / "rigs" / "guitar_vocal.yaml")
        shutil.copy(frozen("accustomed"), tmp_path / "accustomed.yaml")
        (ROOT / "config").is_dir() and sorted((ROOT / "config").glob("*.json"))  # config/, not config/rigs/
        for path, _ in committed_that(compiled):  # the readers: every rig, on purpose
            compiled(path)

    assert _strays_made(reads) == []


@pytest.mark.committed_rigs
def test_a_test_marked_as_reading_the_committed_rigs_may():
    before = len(STRAY)
    for path in committed():
        assert path.read_text()
    assert STRAY[before:] == []


def test_a_stray_read_is_said_with_the_line_that_made_it_and_what_to_do_instead():
    """What conftest's fixture says of a stray read: where, and what to do."""
    made = _strays_made(lambda: (COMMITTED / _a_committed_name()).read_text())
    message = rig_fixtures.said(made)
    assert "test_rig_fixtures.py:" in message and "config/rigs/" in message
    assert "rig_fixtures.frozen" in message and "committed_rigs" in message
