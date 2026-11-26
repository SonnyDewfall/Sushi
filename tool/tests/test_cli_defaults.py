"""Where commands look for things when told nothing.

`verify` and `nulltest` already defaulted to the checkout's `./sushi`; `emit`
(and `panel` and `listen`, since retired) used a bare `sushi` from PATH, and
`save` (and `listen`) wrote to `../config` relative to wherever they were run.
Sushi is resolved from `paths.checkout_root()` now, so a command does the same
thing from any directory; `save` writes only the rig file it is given, since
the v1 configs it once wrote into `config/` were retired (#218).
"""

import argparse

from sushi_rig import cli, save
from sushi_rig.paths import checkout_root


def test_sushi_defaults_to_the_checkouts_own_binary_not_path():
    args = argparse.Namespace(sushi=None)
    assert cli._sushi_bin(args) == str(checkout_root() / "sushi")


def test_an_explicit_sushi_still_wins():
    assert cli._sushi_bin(argparse.Namespace(sushi="/opt/sushi")) == "/opt/sushi"


def test_save_hands_save_any_the_rig_it_was_given(tmp_path, monkeypatch, capsys):
    """`sushi-rig save` writes the rig file it is given, from any directory:
    no config directory is resolved any more, since it writes no v1 config
    (#218)."""
    seen = {}

    def fake_save(name, rig, address):
        seen.update(name=name, rig=rig)
        return rig

    monkeypatch.setattr(save, "save_any", fake_save)
    monkeypatch.chdir(tmp_path)
    assert cli.main(["save", "bright", "--rig", "config/rigs/bright.yaml"]) == 0
    assert seen == {"name": "bright", "rig": __import__("pathlib").Path("config/rigs/bright.yaml")}
    assert "saved config/rigs/bright.yaml" in capsys.readouterr().out


def test_a_missing_elkpy_ends_the_cli_with_its_message_as_it_always_did(monkeypatch, tmp_path):
    """`live.py` raises it so the agent can outlive it; the
    command line is where it becomes an exit."""
    import pytest

    from sushi_rig.live import ElkpyMissing

    def missing(address):
        raise ElkpyMissing("elkpy not found. Install it into the tool's venv")

    monkeypatch.setattr("sushi_rig.live.capture", missing)
    with pytest.raises(SystemExit) as caught:
        cli.main(["capture", "-o", str(tmp_path / "state.json")])
    assert caught.value.code == "elkpy not found. Install it into the tool's venv"
