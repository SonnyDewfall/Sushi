"""Stand-ins for Carla's OSC, for the agent's amp (amp_live.py).

`FakeCarla` is `amp_live.Carla` in memory: whether it answers, and every
message it was sent. `OscCarla` is a real one on loopback: a UDP socket that
decodes the OSC messages it receives with python-osc, and a TCP listener on
the same port, which is what the agent's "is Carla answering" asks
(`procs.port_is_open`). It runs `Carla.at(port=...)`, the agent's own sender,
end to end.

What is observed and what is invented (docs/design/principles.md, principle 1). Observed, and
written down in rig.py and amp.py: Carla serves OSC on 22752 over both UDP
and TCP, addresses a parameter as `/Carla/<plugin>/set_parameter_value
<index> <value>`, numbers plugins from 0, and says nothing back to a UDP
send. Invented: everything else about Carla, which these do not model. A
real Carla applying the value, or doing anything at all with a TCP
connection that is opened and closed at once, is not shown here.
"""

from __future__ import annotations

import socket
import threading

from sushi_rig.amp import PARAMETER_INDEX
from sushi_rig.amp_live import Carla

BY_INDEX = {index: name for name, index in PARAMETER_INDEX.items()}


class FakeCarla:
    """`answering` says whether the OSC port takes a connection; `sent` is
    every (address, payload) sent, `fail` an exception to raise from a send."""

    def __init__(self, answering: bool = True):
        self.answering = answering
        self.sent: list = []
        self.asked = 0
        self.forgotten = 0
        self.fail: BaseException | None = None

    def carla(self) -> Carla:
        def answering():
            self.asked += 1
            return self.answering

        def send(address, payload):
            if self.fail is not None:
                raise self.fail
            self.sent.append((address, list(payload)))

        def forget():
            self.forgotten += 1

        return Carla(answering=answering, send=send, forget=forget)

    def values(self) -> dict:
        """The last value sent for each parameter, by name."""
        out = {}
        for address, (index, value) in self.sent:
            assert address == "/Carla/0/set_parameter_value", address
            out[BY_INDEX[index]] = value
        return out


class OscCarla:
    """A UDP and TCP listener on one loopback port, recording the OSC
    messages that reach it over UDP. Use as a context manager; `close`
    stops both."""

    def __init__(self):
        from pythonosc.osc_message import OscMessage

        self._decode = OscMessage
        # The UDP port is the system's choice and the TCP one must be the same
        # number, which another socket may hold (seen once in a full run: an
        # EADDRINUSE here): another pair is tried rather than failing.
        for _ in range(20):
            self.udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.udp.bind(("127.0.0.1", 0))
            self.port = self.udp.getsockname()[1]
            self.tcp = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.tcp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                self.tcp.bind(("127.0.0.1", self.port))
                break
            except OSError:
                self.tcp.close()
                self.udp.close()
        else:
            raise OSError("no loopback port was free for both UDP and TCP in 20 tries")
        self.tcp.listen(16)
        self.received: list = []
        self.connections = 0
        self.connected = threading.Event()
        self.got = threading.Event()
        self._stop = threading.Event()
        self._stop_tcp = threading.Event()
        self.udp.settimeout(0.1)
        self.tcp.settimeout(0.1)
        self._threads = [threading.Thread(target=self._read, daemon=True),
                         threading.Thread(target=self._accept, daemon=True)]
        for thread in self._threads:
            thread.start()

    def _read(self):
        while not self._stop.is_set():
            try:
                data, _ = self.udp.recvfrom(65536)
            except (socket.timeout, OSError):
                continue
            message = self._decode(data)
            self.received.append((message.address, list(message.params)))
            self.got.set()

    def _accept(self):
        while not (self._stop.is_set() or self._stop_tcp.is_set()):
            try:
                connection, _ = self.tcp.accept()
            except (socket.timeout, OSError):
                continue
            self.connections += 1
            self.connected.set()
            connection.close()

    def stop_answering(self):
        """Close the TCP side, as a Carla that has gone would be: once the
        thread accepting on it has let go, since a socket closed under a
        blocked accept() stays open until the accept() returns."""
        self._stop_tcp.set()
        self._threads[1].join(2)
        self.tcp.close()

    def close(self):
        self._stop.set()
        for thread in self._threads:
            thread.join(2)
        for sock in (self.udp, self.tcp):
            try:
                sock.close()
            except OSError:
                pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
