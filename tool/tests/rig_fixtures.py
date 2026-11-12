"""Which rig files a test reads: the committed ones, or the frozen copies.

`config/rigs/` is the owner's: rigs are saved there from the web UI and
committed, whenever a sound changes. On 2026-11-12 one such commit (a new rig,
and a second channel on electric-chorus-2) failed eight tests that were right
about the code and had only pinned what two rig files held. So, the rule:

- A test may fail because the code is wrong, or because a committed rig is
  not a valid rig (it does not parse, compile or wire). It reads `COMMITTED`,
  takes every file there, and assumes nothing about what any of them holds:
  not how many there are, not their names, channels, plugins or values.
- A test that needs a rig it knows (one channel called board, an amp at
  -0.4 dB, two rigs that differ only in the amp's levels) reads `FROZEN`:
  `fixtures/rigs/`, the eleven rigs as they were on master at c494625
  (2026-11-07), copied byte for byte and never edited to follow config/rigs/.
  They are test data. A frozen rig changes only with the code that reads it
  (a field the format drops, say), in the commit that changes that code.

test_rig_fixtures.py holds the frozen copies to being valid rigs, and holds
the tests to this rule: none names a file under config/rigs/.

What the frozen rigs still read from the checkout: `plugin-ports.txt` (which
plugins are mono) and, by path only, the amp models they name. Nothing reads
a model's contents, and no test here needs the file to exist.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from sushi_rig.paths import checkout_root
from sushi_rig.stereo import PORTS_FILE

FROZEN = Path(__file__).parent / "fixtures" / "rigs"
COMMITTED = checkout_root() / "config" / "rigs"


def frozen(name: str) -> Path:
    """The frozen copy of the rig called `name`."""
    path = FROZEN / f"{name}.yaml"
    assert path.is_file(), f"no frozen rig called {name} (tests/fixtures/rigs/ holds: {frozen_names()})"
    return path


def frozen_names() -> list[str]:
    return sorted(path.stem for path in FROZEN.glob("*.yaml"))


def frozen_checkout(folder: Path) -> Path:
    """A checkout at `folder` whose config/rigs/ is the frozen rigs, for code
    that is given a root and a rig's name (`prepare_rig`, `compile_named_rig`,
    the agent). It holds the rigs and the port table, which is all a compile
    reads from its root; a test that needs more beside them adds it."""
    rigs = folder / "config" / "rigs"
    rigs.mkdir(parents=True)
    for path in sorted(FROZEN.glob("*.yaml")):
        shutil.copy(path, rigs / path.name)
    shutil.copy(checkout_root() / PORTS_FILE, folder / PORTS_FILE)
    return folder


def committed() -> list[Path]:
    """Every rig file in config/rigs/, whatever is there today."""
    return sorted(COMMITTED.glob("*.yaml"))


def committed_that(read) -> list[tuple[Path, object]]:
    """`(path, read(path))` for every committed rig `read` accepts, for a
    module that reads them all as it is imported (to parametrize over them).
    A rig `read` refuses is left out: raised there, it would stop the whole
    run at collection, every test unrun, behind a traceback from whichever
    module was imported first. It is not passed over: it is a rig that does
    not parse or compile, and test_rig_fixtures.py fails on it by name."""
    found = []
    for path in committed():
        try:
            found.append((path, read(path)))
        except Exception:  # noqa: BLE001 -- any refusal; said by test_rig_fixtures.py
            continue
    return found


def controller_of(rig):
    """The controller a rig's MIDI map names, from a committed machine file
    that describes it; None for a rig with no map. A rig may have one (none
    did on 2026-11-12), and `emit_rig` refuses a map with no controller, so a
    test that compiles a rig it does not know asks here first."""
    if rig.midi is None:
        return None
    from sushi_rig.machine import MachineSpec

    for machine in sorted((checkout_root() / "machines").glob("*.yaml")):
        spec = MachineSpec.load(machine)
        if rig.midi.controller in (spec.controllers or {}):
            return spec.controller(rig.midi.controller)
    raise AssertionError(f"no committed machine file describes {rig.midi.controller}")


def compiled(path: Path) -> dict:
    """The config `path` compiles to, as a run would load it: its MIDI map,
    if it has one, compiled for the controller a machine file describes."""
    from sushi_rig import rigfile

    rig = rigfile.load_rig(path)
    return rigfile.emit_rig(rig, controller=controller_of(rig))


def compiled_to_render(path: Path) -> dict:
    """The config `path` compiles to with no MIDI map, which no offline
    render can play: what `render.rig_channels` renders for the null tests,
    and for a rig with no map exactly `emit_rig(load_rig(path))`."""
    from sushi_rig import rigfile

    rig = rigfile.load_rig(path)
    return rigfile.emit_compiled(rig, *rigfile.compile_rig(rig))
