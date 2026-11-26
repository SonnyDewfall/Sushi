"""Every rig in the repository, checked against a real Sushi.

The project's first functional tests. Everything else in this suite tests
decisions in isolation; these load an actual config into an actual Sushi and
assert on what the rig actually did.

What is loaded is each rig of channels of config/rigs/, compiled as `verify
--all` compiles it (`verify.config_to_check`: as Sushi loads it, with no MIDI
map). Until the v1 configs were retired (#218) it was config/*.json.

They need the Sushi binary and the rig's plugins, so they skip cleanly where
those are absent — the other tests deliberately need nothing installed, and that
should stay true.

Each config costs about six seconds, so the whole set is around a minute. That is
why only the quick checks are wired into `emit`.
"""

from __future__ import annotations

import json

import pytest
from rig_fixtures import valid_committed

from sushi_rig.paths import checkout_root
from sushi_rig.verify import VerifyError, config_to_check, verify

ROOT = checkout_root()
SUSHI = ROOT / "sushi"
RIGS = valid_committed()

needs_sushi = pytest.mark.skipif(
    not SUSHI.exists() or not (ROOT / "plugins").is_dir(),
    reason="needs the Sushi binary and the rig's plugins",
)
pytestmark = pytest.mark.committed_rigs


def compiled(rig, tmp_path):
    """The rig's config as `verify --all` checks it, written where `verify`
    can read it."""
    path = tmp_path / f"{rig.stem}.json"
    path.write_text(json.dumps(config_to_check(rig), indent=2) + "\n")
    return path


@needs_sushi
def test_there_are_configs_to_check():
    """A suite that silently checks nothing would pass forever."""
    assert RIGS, f"no rigs found in {ROOT / 'config' / 'rigs'}"


@needs_sushi
@pytest.mark.parametrize("rig", RIGS, ids=lambda p: p.name)
def test_config_loads_and_names_things_that_exist(rig, tmp_path):
    """The cheap half: it loads, and every parameter it sets is one the
    processor actually has.

    Run separately from the deep check so a name problem is reported as a name
    problem rather than buried in a list of values that did not apply.
    """
    problems = verify(compiled(rig, tmp_path), str(SUSHI), quick=True)
    assert not problems, "\n" + "\n".join(f"  {p}" for p in problems)


@needs_sushi
@pytest.mark.slow
@pytest.mark.parametrize("rig", RIGS, ids=lambda p: p.name)
def test_config_actually_applies_what_it_says(rig, tmp_path):
    """The half that earns its keep: load it and compare the running rig against
    the file.

    Twice a config has passed every cheaper check and still not done what it
    said — a track's bypass cascading over every plugin, and a preset index
    applied over the parameters beside it.
    """
    try:
        problems = verify(compiled(rig, tmp_path), str(SUSHI))
    except VerifyError as exc:
        pytest.fail(f"could not check {rig.name}: {exc}")
    assert not problems, "\n" + "\n".join(f"  {p}" for p in problems)
