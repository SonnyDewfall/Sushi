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
@pytest.mark.slow
@pytest.mark.parametrize("path", valid_committed(), ids=lambda p: p.stem)
def test_every_parameter_a_committed_rig_names_is_one_its_plugin_has(path, tmp_path):
    """`verify`'s names check, against Sushi's own list of each plugin's
    parameters: the half of validity no file here can answer. A rig is
    compiled as a run loads it and given to `verify` as the config it is,
    since `verify --all` reads config/*.json and a rig file given to it is
    compiled for this host's machine file, which the container has none of.

    Slow, as `test_stereo.test_the_port_table_is_what_lilv_reads` is: it
    loads every committed rig into this machine's Sushi from this machine's
    plugins/, so it asks what plugins/ holds as much as what the rig names.
    In the fast suite it went red on a checkout that had pulled a rig needing
    a plugin not yet built there (#164's octave, before
    scripts/build-rubberband4.sh). The container job runs it with the rest;
    a plugin plugins/ lacks fails it naming the plugin."""
    from sushi_rig.verify import verify

    config = tmp_path / f"{path.stem}.json"
    compiled_rig = compiled(path)
    config.write_text(json.dumps(compiled_rig, indent=2))
    try:
        problems = verify(config, str(SUSHI), quick=True)
    except SystemExit as exc:
        pytest.fail(f"{path.name}: {exc}{_absent_from_plugins(compiled_rig)}")
    assert not problems, f"{path.name}:\n" + "\n".join(f"  {p}" for p in problems)


def _absent_from_plugins(config: dict) -> str:
    """Which of a compiled rig's plugins plugins/ does not provide, said
    beside Sushi's "Failed to load tracks", which names none."""
    import importlib.util

    if importlib.util.find_spec("lilv") is None:
        return ""
    from sushi_rig.plugins import plugins_on

    # The tracks' plugins alone: the amp runs in Carla, not in this Sushi.
    uris = {p["uri"] for t in config.get("tracks", []) for p in t.get("plugins", []) if p.get("uri")}
    absent = sorted(uris - plugins_on(str(ROOT / "plugins")).keys())
    if not absent:
        return ""
    return ("\nplugins/ provides none of: " + ", ".join(absent)
            + "\n(`sushi-rig plugins check` says the same; Rubber Band 4.0's Live shifter is built by "
            "scripts/build-rubberband4.sh, docs/reference/plugins.md)")


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


# sha256 of every file of the frozen set, as each was at c494625 (the sound
# references: below, where each came from). A frozen
# file is changed on purpose or not at all: with the code that reads it, in
# the same commit, and with its line here. The eleven rigs/ files differ from
# c494625 by one line each, `  version: '1.0'` (`"1.0"` in guitar_vocal's)
# under `meta:`, taken out when the format dropped the field (#143,
# 2026-11-26, rigfile.RETIRED_META); every other byte as frozen.
CHECKSUMS = {
    "machines/sonny-dewfall-XPS-9320.yaml": "f90c8f537dae347caf32ff648d2b3ac1f72a7d3fd4d15f1113a1bc431631f82a",
    "rigs/accustomed.yaml": "5a861c692c874d0cda2a7dbd770577303c54bf9b2d900235a8ae2de300fb3d13",
    "rigs/electric-chorus-2.yaml": "b717955b7c456769a64c9e9f28dc620f280fac0dea0bbfe34484326026d359f5",
    "rigs/electric-chorus-hall.yaml": "a70ae1ed34edc1fd444ce3f16375ead31b2d84847cd5c6808fa9c49d6a52bfdd",
    "rigs/electric-chorus.yaml": "1a737348f268e7ba6de96e06c40ee740ce0bee08d5d58bccd37d1113419a4dec",
    "rigs/electric-clean.yaml": "e1e45505dd9fc5ef29af7c54d40c0a52bad349070c6033f6452767754f5cdafa",
    "rigs/electric-delay.yaml": "a4faa9fa7cf519e9c5f24805b95e9d5c59c4585778f6a57aa637da24ffe21eba",
    "rigs/electric-octave-up.yaml": "d635d353c57b4dfcc57ebee1a4e17aeb0c6b03eb759aadd2185badbcd9f20359",
    "rigs/electric-spacey-delay.yaml": "cdc0404ed60ab44646dc3fbff7d2b81b7ec0f23d2956047461725306f13b7145",
    "rigs/electric_board.yaml": "610b98be202576e5a7c028d7cf86fa38c77b1b95d263a887bcf9560489b88649",
    "rigs/guitar_vocal.yaml": "0d8db23decbfe8fd8f095e8922f98417dad7497c045dacd6eb2f695e4dcde9c9",
    "rigs/hard-feelings.yaml": "957ee2044549ed9a4dd04d740f0d662eedb665100293113529f35205e2e9a5df",
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
    # The frozen rigs' sound references (#144), not of c494625. Each is
    # config/sound/'s file of the same name at 155e6c8, where CI run
    # 43891871551 found every one of them "sounds the same" and each
    # committed rig compiled to the very channel its frozen copy does; and
    # electric-chorus-2's is config/sound/electric-chorus-2.yaml as it was
    # before 8c9bb76 renamed it to the two-channel rig's `.board`, which
    # differs from it in its `config:` line alone. Each sha256 here is that
    # git blob's, so `git show <commit>:<path> | sha256sum` says the same;
    # except electric-delay's and electric_board's, whose `delay_samples` (and
    # electric_board's note on it) were rewritten when the delay became the
    # earliest path rather than the strongest (#143), 19440 to 0 and 4163 to
    # 1, every other byte as frozen.
    "sound/accustomed.yaml": "dea3c2aefb4ccc16026abc247561249b4eb37ee1b47f3f13aab8f2d7014be0dc",
    "sound/electric-chorus-2.yaml": "7d6469d1840ee606d7976f00247d3880c80212d494403000466f6281525bbc3b",
    "sound/electric-chorus-hall.yaml": "008d130a8209e96fb8130af04eaf6c31e60eb6cddbbeef773618a70b1c34a117",
    "sound/electric-chorus.yaml": "d2a184db1b92133090bfc4b65e23fa7290696f93ff538f714ae7cbbd33f7c440",
    "sound/electric-clean.yaml": "ed29336ee65a8934a76a9a76edc39a56a91387ba7fa2bcb67c74c76aa5152f9e",
    "sound/electric-delay.yaml": "b3e8cd27b40eeff888249de660b9fc637031d1c45d3c4c0fda931409ce10174d",
    "sound/electric-octave-up.yaml": "c5346663244708688cf79c760ff0640b63c02ccc1088e3d14cd4a28deeb48853",
    "sound/electric-spacey-delay.yaml": "456b672f1d6614bae2a5ebd6b8cd8146d38e0249c87498e1590721a07cff8692",
    "sound/electric_board.yaml": "b801c22ffeb89e33ff32fd254ad2d613a8ba6b4e80688738a2e1235c6ef27fb6",
    "sound/guitar_vocal.guitar.yaml": "722da50c8de79c51eee0853bc0063a610912fc6bba415843f7995f3f0215abfc",
    "sound/guitar_vocal.vocal.yaml": "2a0d60051d4b4ae4438d2f785224b20b963663c3d9ec05347596f77a34226f61",
    "sound/hard-feelings.yaml": "45b54cecf78e87b7a7253b8e6347fa08c3a0c1d2a08b3ba99fbee1c97ed61a30",
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
