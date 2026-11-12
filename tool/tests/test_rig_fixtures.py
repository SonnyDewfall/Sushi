"""The rule of rig_fixtures.py, held (2026-11-12).

Two things a test may ask of a rig file, and where it asks them:

- Of every rig in config/rigs/, that it is a rig: it parses, compiles and
  wires, as `up --rig` would have it. That is all. Those files are the
  owner's, saved from the web UI and committed, and a test that pinned what
  one held (its channels, its amp's levels, how many there are) failed on
  the day one was saved, with nothing wrong: eight did, on 2c36874.
- Of the frozen rigs under fixtures/rigs/, anything: they are test data, and
  the tests that name a rig mean one of these.

So this file checks the committed rigs for validity and nothing else, checks
the frozen rigs are still valid rigs to the code as it is now (a fixture that
stopped compiling would fail forty tests elsewhere with forty reasons), and
reads the tests' own source for a path into config/rigs/, which is how the
next test to pin a rig's contents would begin.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from rig_fixtures import COMMITTED, FROZEN, committed, controller_of, frozen_names

from sushi_rig import rig, rigfile
from sushi_rig.patchbay import parse_patchbay
from sushi_rig.paths import checkout_root

ROOT = checkout_root()
TESTS = Path(__file__).parent
# A real pw-dump from the rig machine, and the machine file that describes it:
# what test_rig.py wires guitar_vocal against.
CAPTURED_DUMP = (TESTS / "fixtures" / "pw-dump-rig.json").read_text()
MACHINE = ROOT / "machines" / "sonny-dewfall-XPS-9320.yaml"


def _is_a_rig(name: str, root: Path, runtime: Path) -> None:
    """Rig `name` of `root`/config/rigs parses, compiles and wires: the three
    things `up --rig` does to it before anything is started."""
    path = root / "config" / "rigs" / f"{name}.yaml"
    loaded = rigfile.load_rig(path)  # parses, or RigFileError with every problem
    assert loaded.name and loaded.channels, f"{path.name}: a rig has a name and a channel"
    compiled = rigfile.emit_rig(loaded, controller=controller_of(loaded))  # compiles, alone
    channels = [c.name for c in loaded.channels]
    assert [t["name"] for t in compiled["tracks"]] == channels + [rigfile.MASTER]
    # And as `up --rig` compiles and wires it, against the machine file and a
    # captured graph: RigError says why not.
    out = rig.prepare_rig(name, root, runtime, machine_file=MACHINE, dump=CAPTURED_DUMP)
    config = json.loads(out["config"].read_text())
    assert [t["name"] for t in config["tracks"]] == channels + [rigfile.MASTER]
    links = parse_patchbay(out["patchbay"].read_text())
    into_sushi = {link.in_port for link in links if link.in_port.startswith("sushi:audio_input")}
    out_of_sushi = {link.out_port for link in links if link.out_port.startswith("sushi:audio_output")}
    assert len(into_sushi) >= len(channels), f"{path.name}: a channel has no source wired to it: {sorted(into_sushi)}"
    assert out_of_sushi, f"{path.name}: nothing is wired out of Sushi"


@pytest.mark.parametrize("path", committed(), ids=lambda p: p.stem)
def test_every_committed_rig_parses_compiles_and_wires(path, tmp_path):
    """The whole of what the suite asks of a rig in config/rigs/ by itself.
    A failure here is a rig that would not come up, and is the rig's to fix,
    or the compiler's; it is never that the rig changed."""
    _is_a_rig(path.stem, ROOT, tmp_path / "run")


@pytest.mark.parametrize("name", frozen_names())
def test_every_frozen_rig_is_still_a_rig(name, frozen_root, tmp_path):
    _is_a_rig(name, frozen_root, tmp_path / "run")


def test_the_frozen_rigs_are_the_ones_the_tests_name():
    """The eleven of c494625. One added is fine, and is added here; one gone
    or renamed fails here, once, and not as a missing file in whichever test
    asked for it first."""
    assert frozen_names() == [
        "accustomed", "electric-chorus", "electric-chorus-2", "electric-chorus-hall", "electric-clean",
        "electric-delay", "electric-octave-up", "electric-spacey-delay", "electric_board", "guitar_vocal",
        "hard-feelings",
    ]


def test_the_frozen_rigs_are_copies_and_not_the_committed_files():
    """Not links into config/rigs/, which would follow every save."""
    assert FROZEN.resolve() != COMMITTED.resolve()
    for path in sorted(FROZEN.glob("*.yaml")):
        assert path.is_file() and not path.is_symlink(), path.name
        assert COMMITTED.resolve() not in path.resolve().parents, path.name


# A path into the checkout's config/rigs/, as the tests wrote them: the root
# by any of its names here, then the two folders. A temporary checkout's
# (`tmp_path / "config" / "rigs"`, `root / ...`) is not one.
_INTO_CONFIG_RIGS = re.compile(r"""(?:\b_?ROOT\b|checkout_root\(\))\s*/\s*["']config["']\s*/\s*["']rigs["']""")
# rig_fixtures.py is where the committed folder is named, once; this file
# writes such paths out to try the search on them.
_MAY_NAME_IT = {"rig_fixtures.py", Path(__file__).name}


def test_no_test_reads_config_rigs_but_through_rig_fixtures():
    """Every test that reads the committed rigs takes all of them, from
    `rig_fixtures.committed()`, and a test that wants one it knows takes a
    frozen one. A path into config/rigs/ written in a test is how eight tests
    came to fail on a save (2c36874): the file it named had changed."""
    found = []
    for path in sorted(TESTS.glob("*.py")):
        if path.name in _MAY_NAME_IT:
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if _INTO_CONFIG_RIGS.search(line):
                found.append(f"{path.name}:{number}: {line.strip()}")
    assert found == [], "read config/rigs/ through rig_fixtures (committed(), or frozen(name)):\n" + "\n".join(found)


def test_the_search_for_such_a_path_finds_the_ways_one_was_written():
    for line in ('RIGS = checkout_root() / "config" / "rigs"',
                 'rig = ROOT / "config" / "rigs" / f"{config.stem}.yaml"',
                 'for path in sorted((_ROOT / "config" / "rigs").glob("*.yaml"))'):
        assert _INTO_CONFIG_RIGS.search(line), line
    for line in ('rigs = tmp_path / "config" / "rigs"', 'path = root / "config" / "rigs" / f"{name}.yaml"',
                 'CONFIGS = sorted((ROOT / "config").glob("*.json"))'):
        assert not _INTO_CONFIG_RIGS.search(line), line
