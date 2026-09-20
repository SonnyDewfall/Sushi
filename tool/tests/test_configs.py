"""Every config in the repository, checked against a real Sushi.

The project's first functional tests. Everything else in this suite tests
decisions in isolation; these load an actual config into an actual Sushi and
assert on what the rig actually did.

They need the Sushi binary and the rig's plugins, so they skip cleanly where
those are absent — the other tests deliberately need nothing installed, and that
should stay true.

Each config costs about six seconds, so the whole set is around a minute. That is
why only the quick checks are wired into `emit` and the panel's SAVE.
"""

from __future__ import annotations

import pytest

from sushi_rig.paths import checkout_root
from sushi_rig.verify import VerifyError, configs_to_check, verify

ROOT = checkout_root()
SUSHI = ROOT / "sushi"
CONFIGS = configs_to_check(ROOT)

needs_sushi = pytest.mark.skipif(
    not SUSHI.exists() or not (ROOT / "plugins").is_dir(),
    reason="needs the Sushi binary and the rig's plugins",
)


@needs_sushi
def test_there_are_configs_to_check():
    """A suite that silently checks nothing would pass forever."""
    assert CONFIGS, f"no configs found in {ROOT / 'config'}"


@needs_sushi
@pytest.mark.parametrize("config", CONFIGS, ids=lambda p: p.name)
def test_config_loads_and_names_things_that_exist(config):
    """The cheap half: it loads, and every parameter it sets is one the
    processor actually has.

    Run separately from the deep check so a name problem is reported as a name
    problem rather than buried in a list of values that did not apply.
    """
    problems = verify(config, str(SUSHI), quick=True)
    assert not problems, "\n" + "\n".join(f"  {p}" for p in problems)


@needs_sushi
@pytest.mark.slow
@pytest.mark.parametrize("config", CONFIGS, ids=lambda p: p.name)
def test_config_actually_applies_what_it_says(config):
    """The half that earns its keep: load it and compare the running rig against
    the file.

    Twice a config has passed every cheaper check and still not done what it
    said — a track's bypass cascading over every plugin, and a preset index
    applied over the parameters beside it.
    """
    try:
        problems = verify(config, str(SUSHI))
    except VerifyError as exc:
        pytest.fail(f"could not check {config.name}: {exc}")
    assert not problems, "\n" + "\n".join(f"  {p}" for p in problems)
