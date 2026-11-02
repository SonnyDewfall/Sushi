"""`sushi-rig agent install-service` (#29): the agent as a systemd user service.

The unit is `systemd/sushi-rig-agent.service` in the checkout, with
`@CHECKOUT@` where the checkout's path goes; its comments say why each setting
is what it is. This writes it under the user's systemd directory and enables
it the way `systemctl --user enable` does, by linking it into each target its
`[Install]` section names. It does not run systemctl: starting a service that
runs the rig is the player's decision to make, and the commands to make it are
printed, exactly, instead.

Refuse rather than guess: a checkout whose path systemd would read differently
from the filesystem (a space, a `%` specifier, a `$` variable, a quote) is
refused rather than escaped, since an escaping mistake is a unit that starts
something else or nothing, silently.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

UNIT_NAME = "sushi-rig-agent.service"
PLACEHOLDER = "@CHECKOUT@"

# Characters systemd gives a meaning to in ExecStart, WorkingDirectory or
# Documentation (specifiers, variables, quoting, word splitting), or that end
# a line. A path made of anything else is written as it is.
UNSAFE = re.compile(r"[\s%$\"'\\;]")


class ServiceError(Exception):
    """The service cannot be installed as asked, with the reason."""


def unit_text(template: str, root: Path) -> str:
    """The unit for this checkout, from the committed template. Pure."""
    path = str(root)
    if not Path(path).is_absolute():
        raise ServiceError(f"the checkout must be an absolute path, not {path!r}")
    bad = sorted(set(UNSAFE.findall(path)))
    if bad:
        raise ServiceError(
            f"the checkout's path {path!r} contains {' '.join(repr(c) for c in bad)}, which "
            "systemd would read as more than a path; move the checkout to a plain path "
            "(letters, digits, '/', '.', '_', '-')"
        )
    if PLACEHOLDER not in template:
        raise ServiceError(f"the unit template has no {PLACEHOLDER} to fill in")
    return template.replace(PLACEHOLDER, path)


def wanted_by(unit: str) -> list[str]:
    """The targets the unit's `[Install]` section names in `WantedBy=`, in
    order: what enabling it links it into. Pure."""
    targets: list[str] = []
    section = None
    for line in unit.splitlines():
        text = line.strip()
        if text.startswith("[") and text.endswith("]"):
            section = text
            continue
        if section == "[Install]" and text.startswith("WantedBy="):
            targets += text[len("WantedBy="):].split()
    return targets


def unit_dir(env: dict[str, str], home: Path) -> Path:
    """Where systemd looks for a user's own units: `$XDG_CONFIG_HOME/systemd/user`,
    `~/.config/systemd/user` when that is unset or empty."""
    base = env.get("XDG_CONFIG_HOME") or str(home / ".config")
    return Path(base) / "systemd" / "user"


def install(root: Path, *, env: dict[str, str] | None = None, home: Path | None = None) -> list[str]:
    """Write and enable the unit; the lines to print, ending with the
    systemctl commands for the player to run."""
    env = dict(os.environ) if env is None else env
    home = Path.home() if home is None else home
    template_path = root / "systemd" / UNIT_NAME
    try:
        template = template_path.read_text()
    except OSError as exc:
        raise ServiceError(f"cannot read the unit template {template_path}: {exc}") from exc
    program = root / "tool" / ".venv" / "bin" / "sushi-rig"
    if not program.is_file():
        # The unit starts this, and `up` starts the listener from it too.
        raise ServiceError(
            f"{program} does not exist, and the service would start it. Create the venv "
            "as the README's Environment section says, then run this again"
        )
    unit = unit_text(template, root)
    targets = wanted_by(unit)
    if not targets:
        raise ServiceError(f"{template_path} names no WantedBy= target, so it cannot be enabled")

    directory = unit_dir(env, home)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / UNIT_NAME
    try:
        before = path.read_text()
    except FileNotFoundError:
        before = None
    lines = []
    if before == unit:
        lines.append(f"{path} is already this unit; left as it is")
    else:
        path.write_text(unit)
        lines.append(f"{'replaced' if before is not None else 'wrote'} {path}")

    for target in targets:
        wants = directory / f"{target}.wants"
        link = wants / UNIT_NAME
        if link.is_symlink():
            if Path(os.readlink(link)) == path:
                lines.append(f"{link} already enables it")
                continue
            link.unlink()  # a link of ours to an old place; systemctl replaces these too
        elif link.exists():
            raise ServiceError(f"{link} exists and is not a link; enabling would replace a file nobody said to")
        wants.mkdir(parents=True, exist_ok=True)
        link.symlink_to(path)
        lines.append(f"enabled it for {target}: {link} -> {path}")

    lines += [
        "",
        "systemctl was not run. To load it and start the agent now:",
        "",
        "  systemctl --user daemon-reload",
        f"  systemctl --user start {UNIT_NAME}",
        "",
        "It then starts with each graphical login. To see that it is up:",
        "",
        f"  systemctl --user status {UNIT_NAME}",
        f"  journalctl --user -u {UNIT_NAME} -f",
        "  curl localhost:8029/doctor     # the machine as the service sees it",
    ]
    return lines
