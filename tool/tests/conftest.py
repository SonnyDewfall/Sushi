import json
from pathlib import Path

import pytest

EXAMPLES = Path(__file__).parent.parent / "examples"


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


def pytest_terminal_summary(terminalreporter):
    """Print the stereo renders' table (test_stereo.py) and the repeated
    renders' (test_render_spread.py), when any ran: the CI container's log is
    the only place those numbers can be read. Then every render the null tests
    retried after a NaN, with a count, on every run that had one."""
    import spread_table
    import stereo_table

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
        terminalreporter.section(spread_table.RETRIES_TITLE)
        for note in retried:
            terminalreporter.write_line(note)
        terminalreporter.write_line(f"{len(retried)} render(s) retried after a NaN, each once")
