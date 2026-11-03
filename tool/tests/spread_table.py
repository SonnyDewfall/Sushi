"""How far a config differs from itself, render to render: the table the
repeated renders print at the end of a run, and the renders retried after a NaN.

Shared by `test_render_spread.py`, which adds a row per target, the null-test
renders that may retry (`test_convert.py`), which add a line per retry, and
`conftest.py`, whose `pytest_terminal_summary` prints both. The same mechanism
as `stereo_table.py`, for the same reason: the real Sushi and the plugins exist
only in CI's container, and pytest shows a passing test's output nowhere.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest


@dataclass(frozen=True)
class Row:
    section: str
    what: str
    renders: int
    # (first frame, its channel, how many samples) for each render that was not
    # finite, in the order they were rendered.
    non_finite: tuple[tuple[int, int, int], ...]
    # Bit for bit: how many different renders there were among the finite ones.
    distinct: int
    # max - min over the finite renders, dB; None when fewer than one was finite.
    rms: float | None
    peak: float | None
    channel_rms: tuple[float, ...] | None
    bands: tuple[float, ...] | None
    band_names: tuple[str, ...]
    delays: tuple[int, ...]
    seconds_per_render: float
    note: str = ""


ROWS = pytest.StashKey[list]()
RETRIES = pytest.StashKey[list]()

TITLE = "render against render: the same config rendered again and again"
RETRIES_TITLE = "renders retried after a NaN"


def rows(config) -> list[Row]:
    return config.stash.setdefault(ROWS, [])


def retries(config) -> list[str]:
    return config.stash.setdefault(RETRIES, [])


def _db(value: float | None) -> str:
    return "      -" if value is None else f"{value:7.5f}"


def lines(found: list[Row]) -> list[str]:
    width = max([len(r.what) for r in found] + [20])
    names = next((r.band_names for r in found if r.band_names), ())
    out = [
        "",
        "Spreads are max - min over the finite renders, in dB; `distinct` counts",
        "renders that differ bit for bit; NaN counts renders that were not finite.",
    ]
    for section in dict.fromkeys(r.section for r in found):
        out.append("")
        out.append(section)
        if any(r.renders for r in found if r.section == section):
            out.append(
                f"  {'what':{width}s}  {'n':>3s}  {'NaN':>3s}  {'dist':>4s}  {'rms':>7s}  {'peak':>7s}"
                f"  {'L':>7s}  {'R':>7s}  " + "  ".join(f"{n:>7s}" for n in names)
                + f"  {'delay':>9s}  {'s/r':>5s}"
            )
        for r in (r for r in found if r.section == section):
            if not r.renders:
                # A row that measured something other than renders: its note.
                out.append(f"  {r.what}")
                out.append(f"  {r.note}")
                continue
            left, right = (r.channel_rms + (None, None))[:2] if r.channel_rms else (None, None)
            bands = r.bands or (None,) * len(names)
            delays = (
                "-" if not r.delays else str(r.delays[0]) if len(set(r.delays)) == 1
                else f"{min(r.delays)}..{max(r.delays)}"
            )
            out.append(
                f"  {r.what:{width}s}  {r.renders:3d}  {len(r.non_finite):3d}  {r.distinct:4d}"
                f"  {_db(r.rms)}  {_db(r.peak)}  {_db(left)}  {_db(right)}  "
                + "  ".join(_db(b) for b in bands)
                + f"  {delays:>9s}  {r.seconds_per_render:5.2f}"
            )
            for frame, channel, count in r.non_finite:
                out.append(
                    f"  {'':{width}s}  NaN from frame {frame}, channel {channel}: {count} samples; "
                    f"frame mod 256 = {frame % 256}"
                )
            if r.note:
                out.append(f"  {'':{width}s}  {r.note}")
    return out
