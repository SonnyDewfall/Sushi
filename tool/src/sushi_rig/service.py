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


# What `--host` may be in the unit: an address or a plain name, which systemd
# reads as one word with nothing to expand.
HOST_PATTERN = re.compile(r"^[A-Za-z0-9.:-]+$")


def listen_args(*, phone: bool = False, host: str | None = None, port: int | None = None,
                allow_hosts: list[str] | tuple[str, ...] = ()) -> list[str]:
    """What `sushi-rig agent` is given in ExecStart for `install-service
    --phone`, `--host`, `--port` and `--allow-host` (#181, #161): nothing
    for the defaults, so a plain install writes the committed unit as it is.
    Pure."""
    from .agent_client import DEFAULT_HOST, DEFAULT_PORT

    args: list[str] = []
    host = host or DEFAULT_HOST
    if phone and host not in (DEFAULT_HOST, "0.0.0.0"):
        raise ServiceError(f"--phone listens on every address (0.0.0.0), and --host {host} says one; "
                           "give one or the other")
    if phone or host == "0.0.0.0":
        args.append("--phone")
    elif host != DEFAULT_HOST:
        if not HOST_PATTERN.match(host):
            raise ServiceError(f"--host {host!r} is not an address or a plain name")
        args += ["--host", host]
    if port is not None and port != DEFAULT_PORT:
        if not 0 < port < 65536:
            raise ServiceError(f"--port {port} is not a port")
        args += ["--port", str(port)]
    seen: list[str] = []
    for name in allow_hosts:
        # As `sushi-rig agent --allow-host` takes it (the CLI has checked it
        # already); refused here too, so a caller of this cannot write a
        # word systemd would read as more than one.
        if not HOST_PATTERN.match(name) or ":" in name:
            raise ServiceError(f"--allow-host {name!r} is not a host name")
        if name not in seen:
            seen.append(name)
            args += ["--allow-host", name]
    return args


def where(args: list[str]) -> tuple[str, int]:
    """The host and port an agent started with `args` (`listen_args`)
    listens on. Pure."""
    from .agent_client import DEFAULT_HOST, DEFAULT_PORT

    port = int(args[args.index("--port") + 1]) if "--port" in args else DEFAULT_PORT
    if "--phone" in args:
        return "0.0.0.0", port
    return (args[args.index("--host") + 1] if "--host" in args else DEFAULT_HOST), port


def listening_unit(unit: str, args: list[str]) -> str:
    """The unit with `args` after `sushi-rig agent` in ExecStart, and its
    Description saying where it listens. The unit as it is for no args.
    Pure."""
    if not args:
        return unit
    lines, done = [], False
    for line in unit.splitlines(keepends=True):
        if line.startswith("ExecStart=") and line.rstrip("\n").endswith(" agent"):
            line = line.rstrip("\n") + " " + " ".join(args) + "\n"
            done = True
        elif line.startswith("Description=") and "127.0.0.1:8029" in line:
            host, port = where(args)
            line = line.replace("127.0.0.1:8029", f"{host}:{port}")
        lines.append(line)
    if not done:
        raise ServiceError("the unit template has no `ExecStart=… sushi-rig agent` line to add "
                           f"{' '.join(args)} to")
    return "".join(lines)


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


def install(root: Path, *, env: dict[str, str] | None = None, home: Path | None = None,
            listen: list[str] | None = None, found: list | None = None) -> list[str]:
    """Write and enable the unit; the lines to print, ending with the
    systemctl commands for the player to run. `listen` is `listen_args`:
    what the service's agent is started with beyond `agent`. `found` is the
    interfaces the phone's URLs are printed for (`reach.interfaces` when
    None), only looked for when the service listens on the network."""
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
            "as docs/reference/environment.md says, then run this again"
        )
    listen = list(listen or [])
    unit = listening_unit(unit_text(template, root), listen)
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
    ]
    if before is not None and before != unit:
        lines += [
            "If it is already running, restart it instead, so it takes the new unit (the unit",
            "is written to leave the rig playing across a restart):",
            "",
            f"  systemctl --user restart {UNIT_NAME}",
            "",
        ]
    lines += [
        "It then starts with each graphical login. To see that it is up:",
        "",
        f"  systemctl --user status {UNIT_NAME}",
        f"  journalctl --user -u {UNIT_NAME} -f",
        f"  curl localhost:{where(listen)[1]}/doctor     # the machine as the service sees it",
        "",
    ]
    return lines + phone_lines(listen, found)


def phone_lines(listen: list[str], found: list | None = None) -> list[str]:
    """What `install-service` says of where the service listens (#181):
    the phone's URLs and the no-password warning for `--phone`, the one
    address for `--host`, and how to open it to the phone otherwise."""
    from . import reach

    host, port = where(listen)
    names = [listen[i + 1] for i, word in enumerate(listen) if word == "--allow-host"]
    if not reach.is_network(host):
        # The same install with --phone: its port and allowed names kept
        # (review of PR #185), only the localhost --host dropped.
        keep = [f"--port {port}"] if "--port" in listen else []
        keep += [f"--allow-host {name}" for name in names]
        return [
            "It listens on this machine only. For a phone as well, install it with:",
            "",
            " ".join(["  sushi-rig agent install-service --phone", *keep]),
        ]
    if found is None:
        found = reach.interfaces()[0]
    urls = reach.phone_urls(host, port, found)
    lines = ["It listens on the network. Once it is up, open on the phone, on the same network:", ""]
    lines += [f"  {u['url']}   {reach.describe(u)}".rstrip() for u in urls] or \
        ["  (no network address yet: join Wi-Fi or the phone's hotspot; the rack page shows it then)"]
    if names:
        lines += ["", f"A browser may reach it by {', '.join(names)} as well (--allow-host), "
                  "beside any of these addresses."]
    return lines + ["", reach.NO_PASSWORD]
