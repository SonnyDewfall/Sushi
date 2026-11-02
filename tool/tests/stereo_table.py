"""The left-against-right table the stereo renders print at the end of a run.

Shared by `test_stereo.py`, which adds a row per render, and `conftest.py`,
whose `pytest_terminal_summary` prints them. The renders are measurements as
much as checks: a CI log is the only place this table can be read from, since
the real Sushi and the plugins exist only in CI's container, and pytest shows
a passing test's output nowhere.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest


@dataclass(frozen=True)
class Row:
    section: str
    what: str
    ports: str  # lilv's audio in/out for a plugin, "" for a rig
    left_dbfs: float
    right_dbfs: float
    correlation: float | None


ROWS = pytest.StashKey[list]()

TITLE = "left against right: a mono stimulus fed equally to both channels"


def rows(config) -> list[Row]:
    return config.stash.setdefault(ROWS, [])


def _db(value: float) -> str:
    return "silent" if value == float("-inf") else f"{value:7.2f}"


def lines(found: list[Row]) -> list[str]:
    width = max([len(r.what) for r in found] + [20])
    out = []
    for section in dict.fromkeys(r.section for r in found):
        out.append("")
        out.append(f"{section}")
        out.append(f"  {'plugin or rig':{width}s}  {'lilv':5s}  {'L dBFS':>7s}  {'R dBFS':>7s}  {'corr':>6s}")
        for r in (r for r in found if r.section == section):
            corr = "  n/a" if r.correlation is None else f"{r.correlation:6.3f}"
            out.append(f"  {r.what:{width}s}  {r.ports:5s}  {_db(r.left_dbfs):>7s}  {_db(r.right_dbfs):>7s}  {corr:>6s}")
    return out
