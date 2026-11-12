import json
from pathlib import Path

import pytest
import rig_fixtures

EXAMPLES = Path(__file__).parent.parent / "examples"

# The guard on config/rigs/ (rig_fixtures.py): on before any test module is
# imported, so that a rig read as one is imported is seen too.
rig_fixtures.watch()


def pytest_collection_finish(session):
    """What read config/rigs/ while the test modules were imported, set aside
    for test_rig_fixtures.py to fail on: no test was running to blame."""
    rig_fixtures.AT_COLLECTION[:] = rig_fixtures.STRAY
    del rig_fixtures.STRAY[:]


def pytest_sessionfinish(session, exitstatus):
    """A read of config/rigs/ that no test was failed for fails the run: one
    made in a module-, class- or session-scoped fixture is set up before the
    fixture below looks and torn down after it, and passed unseen, its
    entries left behind (review of 0ae6593). Said in the summary, below."""
    if rig_fixtures.left_unreported() and session.exitstatus == pytest.ExitCode.OK:
        session.exitstatus = pytest.ExitCode.TESTS_FAILED


@pytest.fixture(autouse=True)
def _config_rigs_is_read_only_on_purpose(request):
    """Fails a test that read the checkout's config/rigs/ without being
    marked `committed_rigs` (rig_fixtures.py, the guard): from its fixtures'
    set-up to its last line. First of the autouse fixtures, so it is around
    the others."""
    if request.node.get_closest_marker(rig_fixtures.MARKER):
        with rig_fixtures.reading_committed():
            yield
        return
    before = len(rig_fixtures.STRAY)
    yield
    stray = rig_fixtures.STRAY[before:]
    del rig_fixtures.STRAY[before:]
    if stray:
        pytest.fail(rig_fixtures.said(stray), pytrace=False)


@pytest.fixture
def real_dump() -> dict:
    """The real `--dump-plugins` output captured from Sushi 1.3.0.

    Loaded with raw_decode because the file — like Sushi's actual stdout —
    carries a trailing "Parameter dump completed - exiting." line after the
    JSON value.
    """
    raw = (EXAMPLES / "dump.example.json").read_text()
    payload, _ = json.JSONDecoder().raw_decode(raw, raw.index("{"))
    return payload


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    """A private runtime dir, so the real state file is neither read nor hurt.

    `XDG_RUNTIME_DIR` is pointed at a temporary directory; the fixture's value
    is the rig's own directory inside it, where the state file and logs go.
    """
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "run"))
    return tmp_path / "run" / "sushi-rig"


@pytest.fixture
def frozen_root(tmp_path):
    """A checkout whose config/rigs/ is the frozen rigs (rig_fixtures.py): the
    root to give code that looks a rig up by name, in a test that knows what
    the rig holds. The committed config/rigs/ is the owner's and changes."""
    return rig_fixtures.frozen_checkout(tmp_path / "frozen-checkout")


# The marker of an investigation kept to be run again, not on every push
# (test_render_spread.py). Deselected, not skipped: the container job fails on
# any skip, and an opt-in measurement is not a missing dependency.
SPREAD_ENV = "SUSHI_RIG_SPREAD"


def pytest_collection_modifyitems(config, items):
    """Leave the `spread` tests out unless `SUSHI_RIG_SPREAD=1`."""
    import os

    if os.environ.get(SPREAD_ENV) == "1":
        return
    left_out = [item for item in items if item.get_closest_marker("spread")]
    if left_out:
        config.hook.pytest_deselected(items=left_out)
        items[:] = [item for item in items if not item.get_closest_marker("spread")]


def pytest_terminal_summary(terminalreporter):
    """Print the stereo renders' table (test_stereo.py) and the repeated
    renders' (test_render_spread.py), when any ran: the CI container's log is
    the only place those numbers can be read. Then every render the null tests
    retried after a NaN, with a count, on every run that had one."""
    import spread_table
    import stereo_table

    unreported = rig_fixtures.left_unreported()
    if unreported:
        terminalreporter.section("config/rigs/ read outside any test: THE RUN FAILS", sep="!", red=True, bold=True)
        terminalreporter.write_line(unreported)
    stash = terminalreporter.config.stash
    found = stash.get(stereo_table.ROWS, [])
    if found:
        terminalreporter.section(stereo_table.TITLE)
        for line in stereo_table.lines(found):
            terminalreporter.write_line(line)
    spread = stash.get(spread_table.ROWS, [])
    if spread:
        terminalreporter.section(spread_table.TITLE)
        for line in spread_table.lines(spread):
            terminalreporter.write_line(line)
    retried = stash.get(spread_table.RETRIES, [])
    if retried:
        from sushi_rig.render import RETRIED_PREFIX

        # One prefixed line per retry, which the CI job counts (`^RETRIED-NAN:`).
        terminalreporter.section(spread_table.RETRIES_TITLE)
        for note in retried:
            terminalreporter.write_line(f"{RETRIED_PREFIX} {note}")
        terminalreporter.write_line(f"{len(retried)} render(s) retried after a NaN, each once")
