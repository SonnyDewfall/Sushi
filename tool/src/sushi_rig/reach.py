"""How a phone reaches the agent (#181): the addresses to open, and who holds
the port when the agent cannot listen.

What it took to get a phone connected the first time, and what each part here
answers:

- `sushi-rig agent --host 0.0.0.0` stopped with a bare `OSError: [Errno 98]`
  traceback, because an agent bound to 127.0.0.1 alone already held 8029.
  `bind_problem` tries the address before the agent starts anything, and
  `port_in_use_message` says who holds it -- the `sushi-rig-agent` user
  service or which pid -- with the command that stops it, and, when what
  holds it listens on localhost alone, that this is why the phone could not
  reach it.
- The laptop had one address on ethernet and another on Wi-Fi, and nothing
  said which to open. `interfaces` lists each, labelled; `phone_urls` turns
  them into the URLs to type.

Standard library only, and nothing that needs root. The interfaces are
`socket.if_nameindex` and two `ioctl`s (SIOCGIFFLAGS, SIOCGIFADDR) for each,
with `/sys/class/net` saying which is Wi-Fi and which is virtual; no `ip`,
which a minimal install may not have, and no subprocess on a route the page
polls. The port's holder is `/proc/net/tcp` and `tcp6` for the listening
socket, the `/proc/<pid>/fd` links for its process (only this user's
processes can be seen, which the agent's always are), and `/proc/<pid>/cgroup`
for whether systemd started it as the user service.

IPv4 only in what is listed: a phone's browser is typed an IPv4 address, and
a LAN's IPv6 addresses are long, change, and are often not routed between
Wi-Fi clients at all.

Nothing private is printed or logged: the addresses, the interfaces' names,
a pid and a program's name (`comm`), never a command line or an environment.

Pure decisions over facts the effect functions gather, so the decisions are
tested over facts written out, and the effects against this machine's own.
"""

from __future__ import annotations

import contextlib
import errno
import ipaddress
import os
import socket
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

UNIT_NAME = "sushi-rig-agent.service"
DEFAULT_PORT = 8029  # agent_client.DEFAULT_PORT, not imported so this stays a leaf module

# Names of interfaces that are not a network a phone is on: containers and
# VMs' bridges, VPN tunnels, traffic shapers. Checked as well as /sys's
# `devices/virtual`, which catches the rest and is what decides when /sys
# can be read. A tunnel can be reachable (Tailscale from a phone on it), but
# it is not the network the issue is about; the guide's VPN section is.
VIRTUAL_PREFIXES = (
    "docker", "br-", "virbr", "veth", "tun", "tap", "wg", "tailscale", "zt",
    "vmnet", "vboxnet", "lxc", "lxd", "podman", "cni", "flannel", "ifb", "dummy",
)

# linux/sockios.h; the interface's flags and its (first) IPv4 address. Neither
# needs any privilege.
SIOCGIFFLAGS = 0x8913
SIOCGIFADDR = 0x8915
IFF_UP = 0x1
IFF_LOOPBACK = 0x8
ARPHRD_ETHER = 1

# The order the URLs are listed in: the phone is on Wi-Fi, so that first.
KIND_ORDER = {"Wi-Fi": 0, "ethernet": 1, "network": 2}


@dataclass(frozen=True)
class Link:
    """One interface as found: what `classify` decides over."""

    name: str
    address: str | None  # its IPv4 address, None when it has none
    up: bool
    loopback: bool
    virtual: bool | None  # under /sys/devices/virtual; None when /sys said nothing
    wireless: bool  # /sys/class/net/<name>/wireless, or DEVTYPE=wlan
    arp_type: int | None  # /sys/class/net/<name>/type


@dataclass(frozen=True)
class Interface:
    """An address a phone may reach this machine at."""

    name: str
    address: str
    kind: str  # "Wi-Fi", "ethernet" or "network"

    def as_dict(self) -> dict:
        return {"interface": self.name, "address": self.address, "kind": self.kind}


def classify(link: Link) -> tuple[str | None, str]:
    """`(kind, why)`: the label of an interface a phone may be on, or None
    with why it is left out ("" for one with no address, or down, which is
    not worth saying). Pure."""
    if link.address is None or not link.up:
        return None, ""
    if link.loopback or link.name == "lo" or link.address.startswith("127."):
        return None, "loopback"
    if link.virtual or link.name.startswith(VIRTUAL_PREFIXES):
        return None, "virtual"
    if link.wireless:
        return "Wi-Fi", ""
    if link.arp_type == ARPHRD_ETHER:
        return "ethernet", ""
    return "network", ""


def choose(links: Iterable[Link]) -> tuple[list[Interface], list[tuple[str, str]]]:
    """The interfaces to list, Wi-Fi first, and the ones left out with why.
    Pure."""
    listed, left_out = [], []
    for link in links:
        kind, why = classify(link)
        if kind is not None:
            listed.append(Interface(link.name, link.address or "", kind))
        elif why:
            left_out.append((link.name, why))
    listed.sort(key=lambda i: (KIND_ORDER.get(i.kind, 9), i.name, i.address))
    return listed, left_out


def is_network(host: str) -> bool:
    """Whether an agent bound to `host` can be reached from another machine:
    anything but a loopback address or `localhost`. Pure."""
    name = (host or "").strip().strip("[]").lower()
    if name in ("localhost", "localhost.localdomain"):
        return False
    try:
        return not ipaddress.ip_address(name).is_loopback
    except ValueError:
        return True  # a name: what it resolves to is the operator's


def is_wildcard(host: str) -> bool:
    return (host or "").strip().strip("[]") in ("", "0.0.0.0", "::")


def url(address: str, port: int) -> str:
    return f"http://{address}:{port}/"


def phone_urls(host: str, port: int, found: list[Interface]) -> list[dict]:
    """What a phone opens, one entry for each address, labelled: every listed
    interface for a wildcard bind, the one address for a bind to one, none
    for loopback. Pure."""
    if not is_network(host):
        return []
    if is_wildcard(host):
        return [{"url": url(i.address, port), **i.as_dict()} for i in found]
    bare = host.strip().strip("[]")
    match = next((i for i in found if i.address == bare), None)
    if match is not None:
        return [{"url": url(match.address, port), **match.as_dict()}]
    shown = f"[{bare}]" if ":" in bare else bare
    return [{"url": f"http://{shown}:{port}/", "interface": None, "address": bare, "kind": None}]


def describe(entry: dict) -> str:
    """`Wi-Fi, wlp0s20f3` for an entry of `phone_urls`. Pure."""
    parts = [p for p in (entry.get("kind"), entry.get("interface")) if p]
    return ", ".join(parts)


NO_PASSWORD = "No password: anyone on these networks can open the page and change the rig."


def listening_lines(host: str, port: int, found: list[Interface]) -> list[str]:
    """What `sushi-rig agent` prints under its first line: the phone's URLs,
    or that only this machine can open it. Pure."""
    if not is_network(host):
        return ["Only this machine can open it. For a phone as well: sushi-rig agent --phone"]
    urls = phone_urls(host, port, found)
    if not urls:
        return [
            "Listening on the network, but this machine has no network address yet:",
            "join a Wi-Fi network or the phone's hotspot; the rack page on this machine",
            "then shows the address to open on the phone.",
            NO_PASSWORD,
        ]
    width = max(len(u["url"]) for u in urls)
    lines = ["On the phone, on the same network, open:"]
    for u in urls:
        label = describe(u)
        lines.append(f"  {u['url']:<{width}}   {label}".rstrip())
    if len(urls) > 1:
        lines.append("Try them in this order; if none opens, see \"Playing out\" in the guide.")
    lines.append(NO_PASSWORD)
    return lines


# --------------------------------------------------------------------------- #
# The port: can it be had, and if not, who has it
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class Holder:
    """A socket listening on the port, and what is known of its process."""

    address: str  # the address it listens on: 127.0.0.1, 0.0.0.0, ::
    pid: int | None = None  # None when its process cannot be seen (another user's)
    service: bool = False  # in the sushi-rig-agent user service's cgroup
    agent: bool = False  # a sushi-rig agent, by its command line
    program: str | None = None  # /proc/<pid>/comm


def holders_from(tcp_tables: Iterable[tuple[str, str]], port: int) -> list[tuple[str, int]]:
    """`(address, inode)` of each socket listening on `port`, from the text of
    `/proc/net/tcp` and `tcp6` as `(family, text)` pairs. Pure."""
    found = []
    for family, text in tcp_tables:
        for line in text.splitlines()[1:]:
            fields = line.split()
            if len(fields) < 10 or fields[3] != "0A":  # 0A: TCP_LISTEN
                continue
            local, _, hexport = fields[1].partition(":")
            try:
                if int(hexport, 16) != port:
                    continue
                inode = int(fields[9])
                address = _proc_address(local, family)
            except ValueError:
                continue
            found.append((address, inode))
    return found


def _proc_address(hexaddr: str, family: str) -> str:
    """An address as /proc/net/tcp writes it (each 32-bit word in host byte
    order) as text."""
    raw = bytes.fromhex(hexaddr)
    words = b"".join(struct.pack("=I", struct.unpack(">I", raw[i:i + 4])[0]) for i in range(0, len(raw), 4))
    if family == "tcp6":
        return str(ipaddress.IPv6Address(words))
    return str(ipaddress.IPv4Address(words))


def bind_problem(host: str, port: int) -> OSError | None:
    """The error listening on `host:port` would give, or None: a socket
    bound and closed again, with SO_REUSEADDR as aiohttp's own has it, so a
    port held only by a closing connection is not taken for one in use."""
    try:
        infos = socket.getaddrinfo(host or None, port, type=socket.SOCK_STREAM, flags=socket.AI_PASSIVE)
    except socket.gaierror as exc:
        return OSError(errno.EADDRNOTAVAIL, f"{host!r} is not an address of this machine ({exc})")
    for family, kind, proto, _, address in infos:
        sock = socket.socket(family, kind, proto)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            if family == socket.AF_INET6:
                # As asyncio's create_server has it, so `::` and `0.0.0.0`
                # are asked about separately, as aiohttp would bind them.
                with contextlib.suppress(OSError):
                    sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
            sock.bind(address)
        except OSError as exc:
            return exc
        finally:
            sock.close()
    return None


def port_in_use_message(host: str, port: int, holders: list[Holder], *,
                        service_pid: int | None = None, found: list[Interface] | None = None) -> str:
    """What `sushi-rig agent` says, instead of a traceback, when `port` is
    taken: who has it, the command that stops it, and, when what has it
    listens on localhost alone, that this is why the phone cannot reach it.
    `service_pid` is the user service's MainPID when it is running. Pure."""
    found = found or []
    seen = [h for h in holders if h.pid is not None]
    holder = seen[0] if seen else (holders[0] if holders else None)
    where = sorted({h.address for h in holders})
    listens = " and ".join(where)
    local_only = bool(where) and not any(is_network(a) for a in where)
    is_service = holder is not None and (holder.service or (service_pid is not None and holder.pid == service_pid))
    # No process seen to say otherwise, and the service is running: most
    # likely it, and said as most likely.
    likely_service = not is_service and not seen and service_pid is not None

    lines = [f"sushi-rig: cannot listen on {host}:{port}: the port is already taken."]
    if is_service:
        lines.append(f"The {UNIT_NAME} user service has it (pid {holder.pid}), listening on {listens}.")
    elif likely_service:
        lines.append(f"The {UNIT_NAME} user service is running (pid {service_pid}), and most likely has it"
                     + (f", listening on {listens}." if listens else "."))
        is_service = True
    elif holder is not None and holder.pid is not None:
        who = "another sushi-rig agent" if holder.agent else f"another program, {holder.program or 'unnamed'},"
        lines.append(f"{who[0].upper()}{who[1:]} has it (pid {holder.pid}), listening on {listens}.")
    elif holder is not None:
        lines.append(f"A process this user cannot see (another user's, or the system's) has it, "
                     f"listening on {listens}.")
    else:
        lines.append("What has it could not be found.")

    an_agent = is_service or (holder is not None and holder.agent)
    if an_agent and local_only and is_network(host):
        lines.append("It listens on this machine only (localhost), which is why a phone cannot reach it.")
    elif an_agent and where and not local_only and is_network(host):
        lines.append("It already listens on the network, so a phone can use it as it is:")
        urls = phone_urls("0.0.0.0", port, found)
        lines += [f"  {u['url']}   {describe(u)}".rstrip() for u in urls] or \
            ["  (this machine has no network address yet: join Wi-Fi or the phone's hotspot)"]
    elif an_agent and not is_network(host):
        lines.append(f"It answers on this machine as well: open http://localhost:{port}/")

    lines.append("")
    if is_service:
        if is_network(host) and local_only:
            lines += [
                "To have the service listen on the network instead (the unit is written to",
                "leave the rig playing across the restart):",
                "",
                "  sushi-rig agent install-service --phone"
                + ("" if port == DEFAULT_PORT else f" --port {port}"),
                "  systemctl --user daemon-reload",
                f"  systemctl --user restart {UNIT_NAME}",
                "",
                "Or stop the service and run this one in the terminal:",
                "",
            ]
        else:
            lines += ["To stop it (the unit is written to leave the rig playing):", ""]
        lines.append(f"  systemctl --user stop {UNIT_NAME}")
    elif holder is not None and holder.pid is not None and holder.agent:
        lines += [
            "Stop it (the rig keeps playing): Ctrl+C where it runs, or",
            "",
            f"  kill {holder.pid}",
        ]
        if is_network(host) and local_only:
            again = "" if port == DEFAULT_PORT else f" --port {port}"
            lines += ["", "and then start it again for the phone:", "", f"  sushi-rig agent --phone{again}"]
    elif holder is not None and holder.pid is not None:
        lines += [
            "If it is yours to stop:",
            "",
            f"  kill {holder.pid}",
            "",
            "Or give the agent another port:",
            "",
            f"  sushi-rig agent {'--phone ' if is_network(host) else ''}--port {port + 1}",
        ]
    else:
        lines += [
            "To see what it is:",
            "",
            f"  sudo ss -ltnp 'sport = :{port}'",
            "",
            "Or give the agent another port:",
            "",
            f"  sushi-rig agent {'--phone ' if is_network(host) else ''}--port {port + 1}",
        ]
    return "\n".join(lines)


def bind_message(host: str, port: int, error: OSError, **facts) -> str:
    """The message for any error listening gives: the port's holder for
    EADDRINUSE, a plain reason otherwise. `facts` are `holders`,
    `service_pid` and `found`, which are only looked for when needed."""
    if error.errno == errno.EADDRINUSE:
        holders = facts.get("holders")
        if holders is None:
            holders = find_holders(port)
        service_pid = facts["service_pid"] if "service_pid" in facts else service_main_pid()
        found = facts["found"] if "found" in facts else interfaces()[0]
        return port_in_use_message(host, port, holders, service_pid=service_pid, found=found)
    if error.errno == errno.EADDRNOTAVAIL:
        return (f"sushi-rig: cannot listen on {host}:{port}: no interface of this machine has "
                f"that address. For a phone, use --phone, which listens on every address.")
    if error.errno == errno.EACCES:
        return (f"sushi-rig: cannot listen on {host}:{port}: a port under 1024 needs root. "
                f"Use the default, 8029, or another above 1024.")
    return f"sushi-rig: cannot listen on {host}:{port}: {error.strerror or error}"


# --------------------------------------------------------------------------- #
# Effects
# --------------------------------------------------------------------------- #

def _ioctl(sock: socket.socket, request: int, name: str) -> bytes | None:
    import fcntl

    try:
        return fcntl.ioctl(sock.fileno(), request, struct.pack("256s", name.encode()[:15]))
    except OSError:
        return None


def read_links(sys_net: Path = Path("/sys/class/net")) -> list[Link]:
    """Every interface on this machine, as found. No privilege needed."""
    links = []
    try:
        names = [name for _, name in socket.if_nameindex()]
    except OSError:
        return []
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        for name in names:
            raw_flags = _ioctl(sock, SIOCGIFFLAGS, name)
            flags = struct.unpack("H", raw_flags[16:18])[0] if raw_flags else 0
            raw_addr = _ioctl(sock, SIOCGIFADDR, name)
            address = socket.inet_ntoa(raw_addr[20:24]) if raw_addr else None
            links.append(link_from_sys(name, address, flags, sys_net))
    return links


def link_from_sys(name: str, address: str | None, flags: int, sys_net: Path) -> Link:
    """A `Link`, its kind read from /sys/class/net/<name>."""
    base = sys_net / name
    try:
        virtual: bool | None = "/devices/virtual/" in os.path.realpath(base) if base.exists() else None
    except OSError:
        virtual = None
    try:
        uevent = (base / "uevent").read_text()
    except OSError:
        uevent = ""
    try:
        arp_type: int | None = int((base / "type").read_text().strip())
    except (OSError, ValueError):
        arp_type = None
    wireless = (base / "wireless").exists() or (base / "phy80211").exists() or "DEVTYPE=wlan" in uevent
    return Link(name=name, address=address, up=bool(flags & IFF_UP), loopback=bool(flags & IFF_LOOPBACK),
                virtual=virtual, wireless=wireless, arp_type=arp_type)


def interfaces() -> tuple[list[Interface], list[tuple[str, str]]]:
    """This machine's interfaces a phone may be on, and those left out."""
    try:
        return choose(read_links())
    except OSError:
        return [], []


def find_holders(port: int, proc: Path = Path("/proc"), agent_pid: int | None = None) -> list[Holder]:
    """The sockets listening on `port`, each with the process that has it
    when this user can see it. `agent_pid` is the pid a running agent wrote
    beside the state file (`recorded_agent_pid` when None): that pid is an
    agent however it was started."""
    if agent_pid is None:
        agent_pid = recorded_agent_pid()
    tables = []
    for family in ("tcp", "tcp6"):
        try:
            tables.append((family, (proc / "net" / family).read_text()))
        except OSError:
            pass
    listening = holders_from(tables, port)
    if not listening:
        return []
    wanted = {f"socket:[{inode}]": address for address, inode in listening}
    owners: dict[str, int] = {}
    for entry in _pids(proc):
        try:
            fds = list((proc / str(entry) / "fd").iterdir())
        except OSError:
            continue  # another user's, or gone
        for fd in fds:
            try:
                target = os.readlink(fd)
            except OSError:
                continue
            if target in wanted and target not in owners:
                owners[target] = entry
        if len(owners) == len(wanted):
            break
    out = []
    for target, address in wanted.items():
        pid = owners.get(target)
        if pid is None:
            out.append(Holder(address))
            continue
        holder = describe_process(address, pid, proc)
        if pid == agent_pid and not holder.agent:
            holder = Holder(holder.address, holder.pid, holder.service, True, holder.program)
        out.append(holder)
    return out


def recorded_agent_pid() -> int | None:
    """The pid in the file a running agent writes where it listens
    (`agent_client.write_address`), or None."""
    import json

    from .paths import agent_path

    try:
        pid = json.loads(agent_path().read_text()).get("pid")
    except (OSError, ValueError, AttributeError):
        return None
    return pid if isinstance(pid, int) and not isinstance(pid, bool) else None


def _pids(proc: Path) -> list[int]:
    try:
        return sorted(int(p.name) for p in proc.iterdir() if p.name.isdigit())
    except OSError:
        return []


def describe_process(address: str, pid: int, proc: Path = Path("/proc")) -> Holder:
    """A `Holder` for `pid`: its cgroup, whether it is an agent, its name.
    The command line is read, never kept or printed."""
    base = proc / str(pid)
    try:
        cgroup = (base / "cgroup").read_text()
    except OSError:
        cgroup = ""
    try:
        argv = (base / "cmdline").read_bytes().split(b"\0")
    except OSError:
        argv = []
    try:
        program: str | None = (base / "comm").read_text().strip() or None
    except OSError:
        program = None
    return Holder(address=address, pid=pid, service=is_service_cgroup(cgroup), agent=is_agent_argv(argv),
                  program=program)


def is_service_cgroup(text: str) -> bool:
    """Whether a /proc/<pid>/cgroup is the user service's. Pure."""
    return any(line.rstrip().endswith(f"/{UNIT_NAME}") for line in text.splitlines())


def is_agent_argv(argv: list[bytes]) -> bool:
    """Whether a command line is `sushi-rig agent` (by the program, or by
    `-m sushi_rig`), with no subcommand but its options. Pure."""
    words = [a.decode(errors="replace") for a in argv if a]
    for i, word in enumerate(words):
        if os.path.basename(word) == "sushi-rig" or (word == "sushi_rig" and i and words[i - 1] == "-m") \
                or word.endswith("sushi_rig/cli.py"):
            rest = [w for w in words[i + 1:] if not w.startswith("-")]
            return bool(rest) and rest[0] == "agent"
    return False


def service_main_pid(run: Callable | None = None) -> int | None:
    """The user service's MainPID while it is active (`systemctl --user
    show`, which is what `is-active` reads), or None: not running, or no
    systemctl to ask."""
    import subprocess

    run = run or subprocess.run
    try:
        done = run(["systemctl", "--user", "show", "-p", "MainPID", "-p", "ActiveState", UNIT_NAME],
                   capture_output=True, text=True, timeout=3)
    except (OSError, subprocess.SubprocessError):
        return None
    return main_pid_from(done.stdout if done.returncode == 0 else "")


def main_pid_from(text: str) -> int | None:
    """MainPID from `systemctl show -p MainPID -p ActiveState`, when active. Pure."""
    values = dict(line.split("=", 1) for line in text.splitlines() if "=" in line)
    if values.get("ActiveState") not in ("active", "activating", "reloading"):
        return None
    try:
        pid = int(values.get("MainPID", "0"))
    except ValueError:
        return None
    return pid if pid > 0 else None
