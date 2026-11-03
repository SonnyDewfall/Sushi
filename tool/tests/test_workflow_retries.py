"""The CI job's count of renders retried after a NaN, and the opt-in of the
spread table: what `.github/workflows/rig-container.yml` greps for, against
what the reporting code prints, and what conftest leaves out by default.

Review of 997dd73: the job grepped the whole pytest log for "retried after a
NaN", which tracebacks and test source also contain, and counted nulltest's
lines beside each channel as well as its summary, so each retry twice.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import spread_table
from sushi_rig.paths import checkout_root
from sushi_rig.render import RETRIED, RETRIED_PREFIX

WORKFLOW = checkout_root() / ".github" / "workflows" / "rig-container.yml"
TESTS = Path(__file__).parent


def _patterns() -> list[str]:
    """Every pattern the workflow greps a log with for retries."""
    return re.findall(r"grep -c '([^']*)' (?:suite|nulltest)\.log", WORKFLOW.read_text())


def test_the_workflow_counts_only_the_prefixed_lines():
    patterns = _patterns()
    assert len(patterns) == 2, "one count for the suite's log, one for nulltest's"
    assert set(patterns) == {f"^{RETRIED_PREFIX} "}
    assert "grep -q 'retried after a NaN'" not in WORKFLOW.read_text()


class _Reporter:
    def __init__(self, config):
        self.config = config
        self.lines: list[str] = []

    def section(self, title):
        self.lines.append(f"===== {title} =====")

    def write_line(self, line):
        self.lines.append(line)


def test_a_pytest_log_with_retries_in_tracebacks_counts_only_the_summary():
    """Two real retries in the summary; the same words in a traceback, in an
    assertion and in captured test source are not counted."""
    import conftest

    config = SimpleNamespace(stash={spread_table.RETRIES: ["a.json: one", "b.json: two"]})
    reporter = _Reporter(config)
    conftest.pytest_terminal_summary(reporter)
    log = "\n".join([
        f">       assert \"{RETRIED_PREFIX} electric-octave-up.json: {RETRIED}\" in err",
        f"E       AssertionError: {RETRIED}",
        f"    note = f\"{{config.name}}: {{RETRIED}} ({{what}})\"",
        *reporter.lines,
    ])
    (pattern,) = set(_patterns())
    assert len(re.findall(pattern, log, flags=re.MULTILINE)) == 2


def test_the_spread_table_is_left_out_unless_asked_for():
    """Deselected rather than skipped: the container job fails on any skip."""
    pytest.importorskip("numpy")  # test_render_spread.py needs it to be collected at all

    def collected(env):
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider",
             str(TESTS / "test_render_spread.py")],
            capture_output=True, text=True, cwd=TESTS.parent, env=env,
        )
        return result.stdout

    env = {k: v for k, v in os.environ.items() if k != "SUSHI_RIG_SPREAD"}
    default = collected(env)
    assert "test_the_octave_up_renders_only_its_known_nan" in default
    assert "test_rendered_again_and_again" not in default
    assert "14 deselected" in default
    asked = collected({**env, "SUSHI_RIG_SPREAD": "1"})
    assert "test_rendered_again_and_again[electric-octave-up (rig)]" in asked
    assert "deselected" not in asked


@pytest.mark.parametrize("name", ["test_rendered_again_and_again", "test_the_threads_of_an_offline_render"])
def test_the_investigation_carries_the_spread_marker(name):
    pytest.importorskip("numpy")
    import test_render_spread

    marks = {m.name for m in getattr(test_render_spread, name).pytestmark}
    assert {"spread", "slow"} <= marks
