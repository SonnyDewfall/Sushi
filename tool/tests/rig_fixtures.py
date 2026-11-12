"""Which rig files a test reads: the committed ones, or the frozen copies.

`config/rigs/` is the owner's: rigs are saved there from the web UI and
committed, whenever a sound changes. On 2026-11-12 one such commit (a new rig,
and a second channel on electric-chorus-2) failed eight tests that were right
about the code and had only pinned what two rig files held. So, the rule:

- A test may fail because the code is wrong, or because a committed rig is
  not a valid rig. It reads the committed rigs through this module, takes
  every one, and assumes nothing about what any of them holds: not how many
  there are, not their names, channels, plugins or values.
- A test that needs a rig it knows (one channel called board, an amp at
  -0.4 dB, two rigs that differ only in the amp's levels) reads a frozen one.
  The frozen set is test data. A frozen file changes only with the code that
  reads it (a field the format drops, say), in the commit that changes that
  code, and test_rig_fixtures.py pins each by checksum so that it does not
  change any other way.

**The frozen set**, all under fixtures/, each file byte for byte what master
held at c494625 (2026-11-07):

- `rigs/*.yaml`: the eleven rigs of config/rigs/ (`FROZEN`, `frozen(name)`).
- `v1/*.json`: the eleven v1 configs of config/ those rigs were converted
  from (`FROZEN_V1`, `frozen_v1(name)`), and `v1/src/*.yaml`, config/src/'s
  three sources (`FROZEN_SRC`).
- `machines/sonny-dewfall-XPS-9320.yaml`: the machine file (`FROZEN_MACHINE`)
  the frozen rigs are wired against, with the pw-dump capture it describes
  (fixtures/pw-dump-rig.json, there before this).

`frozen_checkout` lays them out as a checkout. Two things a frozen rig still
takes from the real one, and why: `plugin-ports.txt`, which is the compiler's
table and changes with the compiler; and nothing of amp/models/, where a
frozen checkout holds an empty stand-in under each name its rigs give, since
no test reads a model's contents.

**What a committed rig is held to** (`valid`, and test_rig_fixtures.py): it
parses; it compiles, its MIDI map for a controller a machine file describes;
its amp's model is a file, found as `up` finds it; its amp's parameters are
the amp's, numbers, and inside the amp's range; and it wires on the committed
machine file. In the container, where there is a Sushi, every parameter it
names is one its plugin has (`verify`'s names check). No file in the checkout
lists a plugin's parameters, so that last one cannot be asked without Sushi.
A rig `valid` refuses fails one test, by name, and is left out of every
other test over the committed rigs (`valid_committed`, `committed_that`).

**The guard** (`watch`, conftest.py). Every `open`, `os.scandir` and
`os.listdir` in the test process is audited (`sys.addaudithook`), and one that
lands in the checkout's config/rigs/ while nothing here is reading it, and in
a test not marked `committed_rigs`, fails that test. So a test cannot read a
committed rig by any spelling of its path, nor through code that is handed
the checkout's root (`prepare_rig("guitar_vocal", ROOT, ...)`,
`rigs_to_check(ROOT)`), without saying that it means to. A read in a
fixture wider than one test (module, class, session) is nobody's to fail, so
whatever is left unreported when the run ends fails the run, with its lines
(`left_unreported`, conftest's `pytest_sessionfinish`).

What it costs: every audited call in the process runs `_audit`. Timed
here on 2026-11-12, by calling it: about 0.1 microseconds for an event that
is not a file's or a folder's, 0.3 for an open of an absolute path with no
"rigs" in it, 0.7 for a relative one (one `getcwd`, to see whether the
working directory is the folder), and 14 for a path that does say "rigs",
which is the only kind resolved: a frozen rig, a temporary checkout's.

What it does not see: a rig read by a child process; a path only tested for
existence (`is_file`, which opens nothing); a symbolic link into the folder
whose own path has no "rigs" in it, since only a path that says "rigs" is
resolved; a file opened relative to a directory descriptor
(`os.open(name, dir_fd=...)`), whose path the event does not carry; a test
that is not run where the suite is (the slow ones here: the container runs
them, and the guard with them); and a marked test that goes on to name one
rig, which is left to whoever reads the marker.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import sys
from pathlib import Path

from sushi_rig.paths import checkout_root
from sushi_rig.stereo import PORTS_FILE

FIXTURES = Path(__file__).parent / "fixtures"
FROZEN = FIXTURES / "rigs"
FROZEN_V1 = FIXTURES / "v1"
FROZEN_SRC = FROZEN_V1 / "src"
FROZEN_MACHINE = FIXTURES / "machines" / "sonny-dewfall-XPS-9320.yaml"
COMMITTED = checkout_root() / "config" / "rigs"
# The machine file the committed rigs are wired against: the rig machine's.
COMMITTED_MACHINE = checkout_root() / "machines" / "sonny-dewfall-XPS-9320.yaml"

# The marker of a test that reads the committed rigs on purpose (pyproject.toml).
MARKER = "committed_rigs"


# --------------------------------------------------------------------------- #
# The frozen set
# --------------------------------------------------------------------------- #

def frozen(name: str) -> Path:
    """The frozen copy of the rig called `name`."""
    path = FROZEN / f"{name}.yaml"
    assert path.is_file(), f"no frozen rig called {name} (tests/fixtures/rigs/ holds: {frozen_names()})"
    return path


def frozen_names() -> list[str]:
    return sorted(path.stem for path in FROZEN.glob("*.yaml"))


def frozen_v1(name: str) -> Path:
    """The frozen copy of the v1 config called `name` (config/<name>.json)."""
    path = FROZEN_V1 / f"{name}.json"
    assert path.is_file(), f"no frozen v1 config called {name}"
    return path


def frozen_files() -> list[Path]:
    """Every file of the frozen set, for the checksums."""
    return sorted([*FROZEN.glob("*.yaml"), *FROZEN_V1.glob("*.json"), *FROZEN_SRC.glob("*.yaml"), FROZEN_MACHINE])


def frozen_checkout(folder: Path) -> Path:
    """The frozen set laid out as a checkout at `folder`, for code that is
    given a root and a name (`prepare_rig`, `compile_named_rig`,
    `resolve_rig_yaml`, the agent): config/rigs/, config/*.json, config/src/
    and machines/. With them the real port table, and an empty stand-in for
    each amp model a frozen rig names, so that a model is found as `up` finds
    one without this depending on what amp/models/ holds."""
    import yaml

    rigs = folder / "config" / "rigs"
    src = folder / "config" / "src"
    machines = folder / "machines"
    for made in (rigs, src, machines):
        made.mkdir(parents=True)
    for path in sorted(FROZEN.glob("*.yaml")):
        shutil.copy(path, rigs / path.name)
        model = (yaml.safe_load(path.read_text()).get("amp") or {}).get("model")
        if model:
            stand_in = folder / model
            stand_in.parent.mkdir(parents=True, exist_ok=True)
            stand_in.write_text("")
    for path in sorted(FROZEN_V1.glob("*.json")):
        shutil.copy(path, folder / "config" / path.name)
    for path in sorted(FROZEN_SRC.glob("*.yaml")):
        shutil.copy(path, src / path.name)
    shutil.copy(FROZEN_MACHINE, machines / FROZEN_MACHINE.name)
    shutil.copy(checkout_root() / PORTS_FILE, folder / PORTS_FILE)
    return folder


# --------------------------------------------------------------------------- #
# The committed rigs, and the only ways to them
# --------------------------------------------------------------------------- #

_reading = 0  # how many readers of this module are at work; the guard lets those through


@contextlib.contextmanager
def reading_committed():
    """While this is open, reading config/rigs/ is meant. This module's own
    readers use it; a test that reads the committed rigs itself is marked
    `committed_rigs` instead, and conftest opens this around it."""
    global _reading
    _reading += 1
    try:
        yield
    finally:
        _reading -= 1


def committed() -> list[Path]:
    """Every rig file in config/rigs/, whatever is there today."""
    with reading_committed():
        return sorted(COMMITTED.glob("*.yaml"))


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

    with reading_committed():
        rig = rigfile.load_rig(path)
        return rigfile.emit_rig(rig, controller=controller_of(rig))


def compiled_to_render(path: Path) -> dict:
    """The config `path` compiles to with no MIDI map, which no offline
    render can play: what `render.rig_channels` renders for the null tests,
    and for a rig with no map exactly `emit_rig(load_rig(path))`."""
    from sushi_rig import rigfile

    with reading_committed():
        rig = rigfile.load_rig(path)
        return rigfile.emit_compiled(rig, *rigfile.compile_rig(rig))


def amp_problems(config: dict, root: Path) -> list[str]:
    """What `up` would trip on, or quietly change, in a compiled rig's amp:
    a model that is not a file (`amp.prepare` resolves it, and refuses), a
    parameter the amp does not have, a value that is not a number, and one
    outside the amp's range, which Carla would be sent clamped, so that the
    rig would not play what its file says."""
    from sushi_rig.amp import PARAMETER_INDEX, PARAMETER_RANGE, AmpError, clamp, read_amp, resolve_model

    amp = read_amp(config)
    if not amp:
        return []
    found = []
    if not amp.get("model"):
        found.append("the amp names no model, so `up` starts no amp for it")
    else:
        try:
            resolve_model(amp["model"], root)
        except AmpError as exc:
            found.append(str(exc).split("\n", 1)[0])
    parameters = amp.get("parameters") or {}
    if not isinstance(parameters, dict):
        return found + ["the amp's parameters are not a mapping"]
    for name, value in parameters.items():
        if name not in PARAMETER_INDEX:
            found.append(f"amp parameter {name!r} is not one the amp has ({', '.join(PARAMETER_INDEX)})")
        elif isinstance(value, bool) or not isinstance(value, (int, float)):
            found.append(f"amp parameter {name} is {value!r}, not a number")
        else:
            try:
                sent = clamp(name, value)
            except AmpError as exc:
                found.append(str(exc))
                continue
            if sent != float(value):
                low, high = PARAMETER_RANGE[name]
                found.append(f"amp parameter {name} is {value}, outside {low} to {high}: the amp would be sent {sent}")
    return found


def valid(path: Path, root: Path | None = None) -> dict:
    """The compiled config of the rig at `path`, if it is a rig `up --rig`
    would bring up as its file says, as far as that can be told with no Sushi
    and no graph: it parses, compiles, and its amp is one that starts and is
    set to what is written (`amp_problems`). `root` is the checkout its model
    is looked for in. Raises, saying why, for one that is not; wiring is
    test_rig_fixtures.py's to add, which has a machine file."""
    config = compiled(path)
    problems = amp_problems(config, root if root is not None else checkout_root())
    assert not problems, f"{Path(path).name}: " + "; ".join(problems)
    return config


def valid_committed() -> list[Path]:
    """Every committed rig that is `valid`: what a test over all of them
    takes. One that is not fails test_rig_fixtures.py, by name, and nothing
    else: a test here that took it too would fail on a rig that is already
    said to be broken, and a loop would stop there with the rigs after it
    unlooked at."""
    return [path for path, _ in committed_that(lambda path: path)]


def committed_that(read) -> list[tuple[Path, object]]:
    """`(path, read(path))` for every committed rig that is `valid` and that
    `read` accepts, for a module that reads them all as it is imported (to
    parametrize over them). A rig refused by either is left out: raised
    there, it would stop the whole run at collection, every test unrun,
    behind a traceback from whichever module was imported first, and left in
    it would fail a dozen tests about other things. It is not passed over:
    test_rig_fixtures.py fails on it, once and by name."""
    found = []
    with reading_committed():
        for path in committed():
            try:
                valid(path)
                found.append((path, read(path)))
            except Exception:  # noqa: BLE001 -- any refusal; said by test_rig_fixtures.py
                continue
    return found


# --------------------------------------------------------------------------- #
# The guard
# --------------------------------------------------------------------------- #

# Each read of config/rigs/ that nothing here meant: (event, path, where from).
STRAY: list[tuple[str, str, str]] = []
# Those of them made while the test modules were imported (conftest moves them here).
AT_COLLECTION: list[tuple[str, str, str]] = []
_WATCHED = ("open", "os.scandir", "os.listdir")
_LIVE = os.path.realpath(COMMITTED)
_TESTS = str(Path(__file__).parent)
_watching = False


def _where() -> str:
    """The line of a test file the read came from, for the failure to name."""
    frame = sys._getframe(2)
    while frame is not None:
        name = frame.f_code.co_filename
        if name.startswith(_TESTS) and name != __file__:
            return f"{os.path.basename(name)}:{frame.f_lineno}"
        frame = frame.f_back
    return "not from a test file's own line"


def _audit(event: str, args: tuple) -> None:
    if event not in _WATCHED or _reading:
        return
    try:
        target = args[0] if args else None
        if not isinstance(target, (str, bytes, os.PathLike)):
            return  # a file descriptor, or the current directory
        text = os.fsdecode(target)
        if "rigs" not in text and (text.startswith(os.sep) or "rigs" not in os.getcwd()):
            # The cheap half, where nearly every open in a run ends: a path
            # with no "rigs" in it, nor in the working directory it is
            # relative to (a relative path costs one getcwd to find out).
            return
        resolved = os.path.realpath(text)
        if resolved == _LIVE or resolved.startswith(_LIVE + os.sep):
            STRAY.append((event, resolved, _where()))
    except Exception:  # noqa: BLE001 -- an audit hook that raises breaks the call it watches
        return


def watch() -> None:
    """Start the guard, once for the process (a hook cannot be taken off)."""
    global _watching
    if not _watching:
        sys.addaudithook(_audit)
        _watching = True


def left_unreported() -> str | None:
    """What to say, when the run ends, of reads no test was failed for: ones
    made in a fixture wider than a test, set up before the test's own guard
    looked and torn down after. None when there are none."""
    if not STRAY:
        return None
    return ("Not reported by any test (read in a module-, class- or session-scoped fixture, or between "
            "tests):\n" + said(STRAY))


def said(stray: list[tuple[str, str, str]]) -> str:
    lines = sorted({f"  {where}: {event} {os.path.relpath(path, checkout_root())}" for event, path, where in stray})
    return (
        "config/rigs/ was read by a test that does not say it reads the committed rigs. They are the "
        "owner's and change with every save: read a frozen rig (rig_fixtures.frozen, the frozen_root "
        f"fixture), or take every committed rig through rig_fixtures and mark the test `{MARKER}`.\n"
        + "\n".join(lines)
    )
