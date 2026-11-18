"""How a phone reaches the agent (#181), the decisions: which interfaces are
listed and how, the URLs and lines printed, and what is said when the port is
taken. Pure over facts written out, then each effect once against this
machine's own interfaces, sockets and /proc.

What stands in for what: the laptop of the issue (ethernet 192.168.1.147 on
`enx…`, Wi-Fi 192.168.1.150 on `wlp0s20f3`, Docker beside them) is `Link`s
written out; a phone's hotspot is its address. No real phone, LAN, router or
systemd is met here: the service's cgroup and `systemctl show` are their text.
"""

from __future__ import annotations

import errno
import os
import socket
import subprocess

import pytest

from sushi_rig import reach
from sushi_rig.reach import Holder, Interface, Link


def link(name, address, *, wireless=False, virtual=False, arp=1, up=True, loopback=False):
    return Link(name=name, address=address, up=up, loopback=loopback, virtual=virtual,
                wireless=wireless, arp_type=arp)


# The laptop of the issue, and what else a Linux laptop tends to have.
LAPTOP = [
    link("lo", "127.0.0.1", loopback=True, arp=772, virtual=True),
    link("enx00e04c680001", "192.168.1.147"),
    link("wlp0s20f3", "192.168.1.150", wireless=True),
    link("docker0", "172.17.0.1", virtual=True),
    link("virbr0", "192.168.122.1", virtual=True),
    link("veth1a2b3c", None, virtual=True),
    link("tun0", "10.8.0.2", virtual=True, arp=65534),
    link("enp2s0", None),  # a port with no cable in it
]


# --------------------------------------------------------------------------- #
# Which interfaces, labelled how
# --------------------------------------------------------------------------- #

def test_the_issues_laptop_lists_wifi_then_ethernet_and_leaves_out_the_rest():
    listed, left_out = reach.choose(LAPTOP)
    assert listed == [Interface("wlp0s20f3", "192.168.1.150", "Wi-Fi"),
                      Interface("enx00e04c680001", "192.168.1.147", "ethernet")]
    assert left_out == [("lo", "loopback"), ("docker0", "virtual"), ("virbr0", "virtual"), ("tun0", "virtual")]


@pytest.mark.parametrize("name", ["docker0", "br-0123abcd", "virbr0", "veth9", "tun0", "tap0", "wg0",
                                  "tailscale0", "vboxnet0", "lxcbr0"])
def test_a_virtual_interface_is_left_out_by_its_name_when_sys_cannot_say(name):
    found = Link(name=name, address="10.1.2.3", up=True, loopback=False, virtual=None, wireless=False, arp_type=None)
    assert reach.classify(found) == (None, "virtual")


def test_an_interface_down_or_with_no_address_is_left_out_without_a_word():
    assert reach.classify(link("wlan0", "192.168.1.9", wireless=True, up=False)) == (None, "")
    assert reach.classify(link("eth0", None)) == (None, "")


def test_a_hotspots_address_is_wifi_whoever_makes_it():
    # The phone's hotspot as the issue's laptop had it, an iPhone's, and the
    # hotspot the laptop makes itself with nmcli.
    for address in ("10.40.226.17", "172.20.10.2", "10.42.0.1"):
        assert reach.classify(link("wlp0s20f3", address, wireless=True)) == ("Wi-Fi", "")


def test_usb_tethering_and_an_odd_link_are_still_listed():
    assert reach.classify(link("usb0", "192.168.42.10")) == ("ethernet", "")
    assert reach.classify(link("ib0", "10.0.0.5", arp=32)) == ("network", "")


def test_sys_says_which_is_wifi_and_which_is_virtual(tmp_path):
    devices = tmp_path / "devices"
    for path, name in [("pci0000:00/0000:00:14.3/net", "wlp0s20f3"), ("virtual/net", "docker0"),
                       ("pci0000:00/0000:00:1f.6/net", "enp0s31f6")]:
        (devices / path / name).mkdir(parents=True)
        (devices / path / name / "type").write_text("1\n")
    (devices / "pci0000:00/0000:00:14.3/net/wlp0s20f3" / "wireless").mkdir()
    net = tmp_path / "class" / "net"
    net.mkdir(parents=True)
    (net / "wlp0s20f3").symlink_to(devices / "pci0000:00/0000:00:14.3/net/wlp0s20f3")
    (net / "docker0").symlink_to(devices / "virtual/net/docker0")
    (net / "enp0s31f6").symlink_to(devices / "pci0000:00/0000:00:1f.6/net/enp0s31f6")

    wifi = reach.link_from_sys("wlp0s20f3", "192.168.1.150", reach.IFF_UP, net)
    docker = reach.link_from_sys("docker0", "172.17.0.1", reach.IFF_UP, net)
    wired = reach.link_from_sys("enp0s31f6", "192.168.1.147", reach.IFF_UP, net)
    assert (wifi.wireless, wifi.virtual, wifi.arp_type) == (True, False, 1)
    assert docker.virtual is True
    assert (wired.wireless, wired.virtual) == (False, False)
    assert [i.kind for i in reach.choose([wifi, docker, wired])[0]] == ["Wi-Fi", "ethernet"]


# --------------------------------------------------------------------------- #
# The URLs, and what `sushi-rig agent` prints
# --------------------------------------------------------------------------- #

FOUND = reach.choose(LAPTOP)[0]


def test_a_wildcard_bind_gives_a_url_for_each_interface_and_loopback_none():
    urls = reach.phone_urls("0.0.0.0", 8029, FOUND)
    assert [u["url"] for u in urls] == ["http://192.168.1.150:8029/", "http://192.168.1.147:8029/"]
    assert [reach.describe(u) for u in urls] == ["Wi-Fi, wlp0s20f3", "ethernet, enx00e04c680001"]
    assert reach.phone_urls("::", 8029, FOUND) == urls
    assert reach.phone_urls("127.0.0.1", 8029, FOUND) == []
    assert reach.phone_urls("localhost", 8029, FOUND) == []


def test_a_bind_to_one_address_gives_that_one_labelled_when_it_is_known():
    assert reach.phone_urls("192.168.1.147", 8030, FOUND) == [
        {"url": "http://192.168.1.147:8030/", "interface": "enx00e04c680001", "address": "192.168.1.147",
         "kind": "ethernet"}]
    assert reach.phone_urls("10.9.9.9", 8029, FOUND)[0]["url"] == "http://10.9.9.9:8029/"


@pytest.mark.parametrize("host, network", [
    ("127.0.0.1", False), ("127.0.1.1", False), ("::1", False), ("localhost", False),
    ("0.0.0.0", True), ("::", True), ("192.168.1.150", True), ("rig.local", True),
])
def test_what_is_on_the_network(host, network):
    assert reach.is_network(host) is network


def test_the_phone_lines_name_each_url_and_keep_the_no_password_warning():
    lines = reach.listening_lines("0.0.0.0", 8029, FOUND)
    assert lines == [
        "On the phone, on the same network, open:",
        "  http://192.168.1.150:8029/   Wi-Fi, wlp0s20f3",
        "  http://192.168.1.147:8029/   ethernet, enx00e04c680001",
        "Try them in this order; if none opens, see \"Playing out\" in the guide.",
        reach.NO_PASSWORD,
    ]
    assert "No password" in reach.NO_PASSWORD


def test_localhost_says_how_to_open_it_to_a_phone_and_no_address_says_what_to_join():
    assert reach.listening_lines("127.0.0.1", 8029, FOUND) == [
        "Only this machine can open it. For a phone as well: sushi-rig agent --phone"]
    alone = reach.listening_lines("0.0.0.0", 8029, [])
    assert "no network address" in alone[0] and "hotspot" in alone[1]
    assert alone[-1] == reach.NO_PASSWORD


# --------------------------------------------------------------------------- #
# The port, taken
# --------------------------------------------------------------------------- #

# /proc/net/tcp as Linux writes it: the issue's case, an agent on
# 127.0.0.1:8029 (8029 is 0x1F5D), and a connection to it, not listening.
TCP = """\
  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode
   0: 0100007F:1F5D 00000000:0000 0A 00000000:00000000 00:00000000 00000000  1000        0 424242 1 0000000000000000 100 0 0 10 0
   1: 0100007F:B3C2 0100007F:1F5D 01 00000000:00000000 00:00000000 00000000  1000        0 424243 1 0000000000000000 20 4 30 10 -1
   2: 00000000:0016 00000000:0000 0A 00000000:00000000 00:00000000 00000000     0        0 1111 1 0000000000000000 100 0 0 10 0
"""
TCP6 = """\
  sl  local_address                         remote_address                        st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode
   0: 00000000000000000000000000000000:1F5D 00000000000000000000000000000000:0000 0A 00000000:00000000 00:00000000 00000000  1000        0 515151 1 0000000000000000 100 0 0 10 0
"""


def test_the_listening_socket_on_the_port_is_found_in_proc_net_tcp():
    assert reach.holders_from([("tcp", TCP)], 8029) == [("127.0.0.1", 424242)]
    assert reach.holders_from([("tcp", TCP), ("tcp6", TCP6)], 8029) == [("127.0.0.1", 424242), ("::", 515151)]
    assert reach.holders_from([("tcp", TCP)], 22) == [("0.0.0.0", 1111)]
    assert reach.holders_from([("tcp", TCP)], 8080) == []


SERVICE_CGROUP = "0::/user.slice/user-1000.slice/user@1000.service/app.slice/sushi-rig-agent.service\n"
TERMINAL_CGROUP = "0::/user.slice/user-1000.slice/user@1000.service/app.slice/app-gnome-org.gnome.Terminal-4242.scope\n"


def test_the_service_is_known_by_its_cgroup_and_an_agent_by_its_command_line():
    assert reach.is_service_cgroup(SERVICE_CGROUP)
    assert not reach.is_service_cgroup(TERMINAL_CGROUP)
    assert reach.is_agent_argv([b"/usr/bin/python3", b"/home/me/Sushi/tool/.venv/bin/sushi-rig", b"agent", b""])
    assert reach.is_agent_argv([b"/home/me/Sushi/tool/.venv/bin/sushi-rig", b"agent", b"--phone"])
    assert reach.is_agent_argv([b"python3", b"-m", b"sushi_rig", b"agent"])
    assert not reach.is_agent_argv([b"sushi-rig", b"listen", b"--port", b"8029"])
    assert not reach.is_agent_argv([b"python3", b"-m", b"http.server", b"8029"])


def test_the_services_main_pid_is_read_only_while_it_is_active():
    assert reach.main_pid_from("MainPID=4242\nActiveState=active\n") == 4242
    assert reach.main_pid_from("ActiveState=inactive\nMainPID=0\n") is None
    assert reach.main_pid_from("") is None

    def ran(argv, **kwargs):
        assert argv[:3] == ["systemctl", "--user", "show"] and argv[-1] == reach.UNIT_NAME
        return subprocess.CompletedProcess(argv, 0, "MainPID=77\nActiveState=active\n", "")

    def missing(argv, **kwargs):
        raise FileNotFoundError("systemctl")

    assert reach.service_main_pid(ran) == 77
    assert reach.service_main_pid(missing) is None


def test_the_issues_case_says_the_localhost_agent_holds_it_and_why_the_phone_cannot_reach_it():
    message = reach.port_in_use_message("0.0.0.0", 8029, [Holder("127.0.0.1", 4242, agent=True, program="sushi-rig")])
    assert "Traceback" not in message and "Errno" not in message
    assert "Another sushi-rig agent has it (pid 4242), listening on 127.0.0.1." in message
    assert "which is why a phone cannot reach it" in message
    assert "\n  kill 4242\n" in message
    assert message.endswith("  sushi-rig agent --phone")


def test_the_service_holding_it_is_named_with_the_systemctl_that_stops_it_or_opens_it():
    held = [Holder("127.0.0.1", 900, service=True, agent=True, program="sushi-rig")]
    message = reach.port_in_use_message("0.0.0.0", 8029, held)
    assert f"The {reach.UNIT_NAME} user service has it (pid 900), listening on 127.0.0.1." in message
    assert "which is why a phone cannot reach it" in message
    for command in ("sushi-rig agent install-service --phone", "systemctl --user daemon-reload",
                    f"systemctl --user restart {reach.UNIT_NAME}", f"systemctl --user stop {reach.UNIT_NAME}"):
        assert f"\n  {command}" in message, command
    assert "kill" not in message

    # Known by systemctl's MainPID when its cgroup could not be read.
    by_pid = reach.port_in_use_message("0.0.0.0", 8029, [Holder("127.0.0.1", 900, agent=True)], service_pid=900)
    assert f"The {reach.UNIT_NAME} user service has it (pid 900)" in by_pid


def test_a_service_already_on_the_network_gives_the_phones_urls_as_it_is():
    held = [Holder("0.0.0.0", 900, service=True, agent=True)]
    message = reach.port_in_use_message("0.0.0.0", 8029, held, found=FOUND)
    assert "It already listens on the network, so a phone can use it as it is:" in message
    assert "  http://192.168.1.150:8029/   Wi-Fi, wlp0s20f3" in message
    assert "install-service" not in message
    assert f"\n  systemctl --user stop {reach.UNIT_NAME}" in message


def test_asking_for_localhost_where_an_agent_listens_says_it_answers_here_already():
    message = reach.port_in_use_message("127.0.0.1", 8029, [Holder("0.0.0.0", 4242, agent=True)])
    assert "It answers on this machine as well: open http://localhost:8029/" in message
    assert "\n  kill 4242" in message and "--phone" not in message


def test_another_program_is_named_by_pid_with_another_port_offered():
    message = reach.port_in_use_message("0.0.0.0", 8029, [Holder("0.0.0.0", 31337, program="python3")])
    assert "Another program, python3, has it (pid 31337), listening on 0.0.0.0." in message
    assert "\n  kill 31337" in message
    assert "\n  sushi-rig agent --phone --port 8030" in message
    assert "phone cannot reach it" not in message


def test_a_process_this_user_cannot_see_is_said_so_with_how_to_find_it():
    message = reach.port_in_use_message("0.0.0.0", 8029, [Holder("0.0.0.0")])
    assert "A process this user cannot see" in message
    assert "sudo ss -ltnp 'sport = :8029'" in message
    likely = reach.port_in_use_message("0.0.0.0", 8029, [Holder("127.0.0.1")], service_pid=900)
    assert f"The {reach.UNIT_NAME} user service is running (pid 900), and most likely has it" in likely
    assert f"\n  systemctl --user stop {reach.UNIT_NAME}" in likely
    nothing = reach.port_in_use_message("0.0.0.0", 8029, [])
    assert "What has it could not be found." in nothing


def test_other_bind_errors_are_said_plainly():
    assert "no interface of this machine has that address" in reach.bind_message(
        "10.9.9.9", 8029, OSError(errno.EADDRNOTAVAIL, "x"))
    assert "needs root" in reach.bind_message("0.0.0.0", 80, OSError(errno.EACCES, "x"))


# --------------------------------------------------------------------------- #
# The effects, against this machine
# --------------------------------------------------------------------------- #

def test_this_machines_interfaces_never_list_loopback():
    listed, left_out = reach.interfaces()
    assert all(isinstance(i, Interface) and i.kind in reach.KIND_ORDER for i in listed)
    assert not any(i.address.startswith("127.") or i.name == "lo" for i in listed)
    names = {name for _, name in socket.if_nameindex()}
    assert {i.name for i in listed} <= names
    assert ("lo", "loopback") in left_out


def test_a_port_held_on_localhost_alone_refuses_the_wildcard_and_names_this_process():
    """The issue's traceback, met for real: a socket listening on 127.0.0.1
    and a bind to 0.0.0.0 on the same port, both with SO_REUSEADDR."""
    with socket.socket() as held:
        held.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        held.bind(("127.0.0.1", 0))
        held.listen()
        port = held.getsockname()[1]
        problem = reach.bind_problem("0.0.0.0", port)
        assert problem is not None and problem.errno == errno.EADDRINUSE
        holders = reach.find_holders(port, agent_pid=0)
        assert [(h.address, h.pid) for h in holders] == [("127.0.0.1", os.getpid())]
        assert holders[0].program and not holders[0].service
        # Told it is the agent by the pid an agent wrote, as `serve` would be.
        assert reach.find_holders(port, agent_pid=os.getpid())[0].agent is True
        message = reach.bind_message("0.0.0.0", port, problem, service_pid=None, found=[])
        assert f"(pid {os.getpid()})" in message and f"kill {os.getpid()}" in message
    assert reach.bind_problem("0.0.0.0", port) is None, "free again once it is closed"
