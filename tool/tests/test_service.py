"""`sushi-rig agent install-service` (#29): the unit is written and enabled
under the user's systemd directory, and systemctl is never run.

All of this is files: what systemd then does with them -- whether the agent
starts with the graphical session, whether KillMode=process leaves the rig
playing across a `systemctl --user restart` -- has NOT been seen on a real
machine. MANIFEST.md says what to check.
"""

from __future__ import annotations

import subprocess

import pytest

from sushi_rig import cli, paths, service
from sushi_rig.paths import checkout_root
from sushi_rig.service import ServiceError, install, unit_dir, unit_text, wanted_by

TEMPLATE = (checkout_root() / "systemd" / service.UNIT_NAME).read_text()


@pytest.fixture
def checkout(tmp_path):
    root = tmp_path / "Sushi"
    (root / "systemd").mkdir(parents=True)
    (root / "systemd" / service.UNIT_NAME).write_text(TEMPLATE)
    program = root / "tool" / ".venv" / "bin" / "sushi-rig"
    program.parent.mkdir(parents=True)
    program.write_text("#!/bin/sh\n")
    return root


@pytest.fixture
def no_systemctl(monkeypatch):
    def refuse(*args, **kwargs):
        pytest.fail(f"ran a process: {args}")

    monkeypatch.setattr(subprocess, "run", refuse)
    monkeypatch.setattr(subprocess, "Popen", refuse)


def test_the_committed_unit_runs_the_agent_from_the_checkout_and_leaves_the_rig_up():
    unit = unit_text(TEMPLATE, checkout_root())
    assert service.PLACEHOLDER not in unit
    assert f"ExecStart={checkout_root()}/tool/.venv/bin/sushi-rig agent\n" in unit
    assert f"WorkingDirectory={checkout_root()}\n" in unit
    assert "\nKillMode=process\n" in unit, "a service restart must not cut the sound"
    assert wanted_by(unit) == ["graphical-session.target"]


@pytest.mark.parametrize("root", [
    "/home/a b/Sushi", "/home/a/100%", "/home/$USER/Sushi", '/home/"q"/Sushi', "/x\\y", "relative/Sushi",
])
def test_a_checkout_systemd_would_read_as_more_than_a_path_is_refused(root):
    from pathlib import Path

    with pytest.raises(ServiceError):
        unit_text(TEMPLATE, Path(root))


def test_the_user_unit_directory_is_where_systemd_looks(tmp_path):
    assert unit_dir({}, tmp_path) == tmp_path / ".config" / "systemd" / "user"
    assert unit_dir({"XDG_CONFIG_HOME": ""}, tmp_path) == tmp_path / ".config" / "systemd" / "user"
    assert unit_dir({"XDG_CONFIG_HOME": "/cfg"}, tmp_path).as_posix() == "/cfg/systemd/user"


def test_install_writes_and_enables_the_unit_and_prints_the_systemctl_commands(checkout, tmp_path, no_systemctl):
    home = tmp_path / "home"
    lines = install(checkout, env={}, home=home)
    directory = home / ".config" / "systemd" / "user"
    unit = directory / service.UNIT_NAME
    assert unit.read_text() == unit_text(TEMPLATE, checkout)
    link = directory / "graphical-session.target.wants" / service.UNIT_NAME
    assert link.is_symlink() and link.resolve() == unit.resolve()
    assert "  systemctl --user daemon-reload" in lines
    assert f"  systemctl --user start {service.UNIT_NAME}" in lines
    assert any("systemctl was not run" in line for line in lines)


def test_install_twice_changes_nothing_the_second_time(checkout, tmp_path, no_systemctl):
    home = tmp_path / "home"
    install(checkout, env={}, home=home)
    again = install(checkout, env={}, home=home)
    assert any("already this unit" in line for line in again)
    assert any("already enables it" in line for line in again)


def test_install_refuses_a_checkout_with_no_venv_to_start(checkout, tmp_path, no_systemctl):
    (checkout / "tool" / ".venv" / "bin" / "sushi-rig").unlink()
    with pytest.raises(ServiceError, match="does not exist"):
        install(checkout, env={}, home=tmp_path / "home")
    assert not (tmp_path / "home" / ".config").exists(), "nothing written for a refusal"


def test_install_does_not_replace_a_file_where_its_link_would_go(checkout, tmp_path, no_systemctl):
    wants = tmp_path / "home" / ".config" / "systemd" / "user" / "graphical-session.target.wants"
    wants.mkdir(parents=True)
    (wants / service.UNIT_NAME).write_text("someone's own")
    with pytest.raises(ServiceError, match="is not a link"):
        install(checkout, env={}, home=tmp_path / "home")
    assert (wants / service.UNIT_NAME).read_text() == "someone's own"


def test_the_cli_command_installs_for_the_checkout_it_runs_from(checkout, tmp_path, monkeypatch, capsys, no_systemctl):
    monkeypatch.setattr(paths, "checkout_root", lambda: checkout)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    assert cli._main(["agent", "install-service"]) == 0
    out = capsys.readouterr().out
    assert "systemctl --user daemon-reload" in out
    assert (tmp_path / "home" / ".config" / "systemd" / "user" / service.UNIT_NAME).is_file()


def test_the_cli_says_why_the_service_was_not_installed(checkout, tmp_path, monkeypatch, no_systemctl):
    (checkout / "tool" / ".venv" / "bin" / "sushi-rig").unlink()
    monkeypatch.setattr(paths, "checkout_root", lambda: checkout)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    with pytest.raises(SystemExit, match="the service was not installed: .* does not exist"):
        cli._main(["agent", "install-service"])
