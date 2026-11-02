"""Where commands look for things when told nothing.

`verify` and `nulltest` already defaulted to the checkout's `./sushi`; `emit`,
`panel` and `listen` used a bare `sushi` from PATH, and `save` and `listen`
wrote to `../config` relative to wherever they were run. Both are resolved from
`paths.checkout_root()` now, so a command does the same thing from any directory.
"""

import argparse

from sushi_rig import cli, listen, save
from sushi_rig.paths import checkout_root


def test_sushi_defaults_to_the_checkouts_own_binary_not_path():
    args = argparse.Namespace(sushi=None)
    assert cli._sushi_bin(args) == str(checkout_root() / "sushi")


def test_an_explicit_sushi_still_wins():
    assert cli._sushi_bin(argparse.Namespace(sushi="/opt/sushi")) == "/opt/sushi"


def test_config_dirs_default_to_the_checkouts_config():
    out_dir, archive_dir = cli._config_dirs(
        argparse.Namespace(out_dir=None, archive_dir=None)
    )
    assert out_dir == checkout_root() / "config"
    assert archive_dir == checkout_root() / "config" / "archive"


def test_the_archive_follows_an_explicit_out_dir(tmp_path):
    out_dir, archive_dir = cli._config_dirs(
        argparse.Namespace(out_dir=tmp_path, archive_dir=None)
    )
    assert (out_dir, archive_dir) == (tmp_path, tmp_path / "archive")


def test_explicit_dirs_are_kept_as_given(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    assert cli._config_dirs(argparse.Namespace(out_dir=a, archive_dir=b)) == (a, b)


def test_save_resolves_its_directories_from_the_checkout_not_the_cwd(
    tmp_path, monkeypatch, capsys
):
    seen = {}

    def fake_save(name, rig, out_dir, archive_dir, address):
        seen.update(out_dir=out_dir, archive_dir=archive_dir)
        return out_dir / f"{name}.json"

    monkeypatch.setattr(save, "save_any", fake_save)
    monkeypatch.chdir(tmp_path)
    assert cli.main(["save", "bright", "--rig", "rig.yaml"]) == 0
    assert seen["out_dir"] == checkout_root() / "config"
    assert seen["archive_dir"] == checkout_root() / "config" / "archive"


def test_listen_resolves_its_directories_and_sushi_from_the_checkout(
    tmp_path, monkeypatch
):
    seen = {}

    def fake_serve(rig, out_dir, archive_dir, **kwargs):
        seen.update(out_dir=out_dir, archive_dir=archive_dir, **kwargs)

    monkeypatch.setattr(listen, "serve", fake_serve)
    monkeypatch.chdir(tmp_path)
    assert cli.main(["listen", "--rig", "rig.yaml"]) == 0
    assert seen["out_dir"] == checkout_root() / "config"
    assert seen["archive_dir"] == checkout_root() / "config" / "archive"
    assert seen["sushi_bin"] == str(checkout_root() / "sushi")


def test_up_passes_listen_explicit_values_so_the_new_defaults_do_not_touch_it(tmp_path):
    """`rig.plan_children` runs `listen` from the checkout root with these, so
    changing the defaults changes nothing for `sushi-rig up`."""
    from sushi_rig.rig import plan_children

    children = plan_children(
        tmp_path, tmp_path / "config" / "x.json", "config/src/x.yaml",
        headless=False, tuner=False,
    )
    command = next(c for c in children if c["name"] == "listen")["command"]
    assert command[command.index("--out-dir") + 1] == "config"
    assert command[command.index("--archive-dir") + 1] == "config/archive"
    assert command[command.index("--sushi") + 1] == "./sushi"


def test_a_missing_elkpy_ends_the_cli_with_its_message_as_it_always_did(monkeypatch, tmp_path):
    """`live.py` raises it so the listener and the agent can outlive it; the
    command line is where it becomes an exit."""
    import pytest

    from sushi_rig.live import ElkpyMissing

    def missing(address):
        raise ElkpyMissing("elkpy not found. Install it into the tool's venv")

    monkeypatch.setattr("sushi_rig.live.capture", missing)
    with pytest.raises(SystemExit) as caught:
        cli.main(["capture", "-o", str(tmp_path / "state.json")])
    assert caught.value.code == "elkpy not found. Install it into the tool's venv"
