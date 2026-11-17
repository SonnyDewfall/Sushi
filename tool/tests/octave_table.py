"""What the octave's renders measured (test_octave.py), printed at the end of
a run by `conftest.pytest_terminal_summary`: the CI container's log is the
only place they can be read, as with `stereo_table`."""

from __future__ import annotations

import pytest

ROWS = pytest.StashKey[list]()

TITLE = "the octave (Rubber Band 4.0 Live shifter) under Sushi"


def rows(config) -> list[tuple[str, str]]:
    return config.stash.setdefault(ROWS, [])


def lines(found: list[tuple[str, str]]) -> list[str]:
    width = max(len(what) for what, _ in found)
    return [f"  {what:{width}s}  {value}" for what, value in found]
