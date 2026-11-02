"""A stand-in for JACK-Client, and the graph it sits in, for the capture engine's tests.

Read this for what it is. `FakeJack` is a few dozen lines of Python written from
JACK-Client's documented shape, not a capture of the real thing, so it proves
the engine's own logic (what it calls, how many times, what it does when a read
comes back empty) and nothing about JACK or PipeWire.

What is observed, and what is not:

- `RIG_DUMP` is a capture: `fixtures/pw-dump-rig.json`, a real `pw-dump` from
  the rig with the rig up. In it the iD14's guitar input has the alias
  `Audient iD14:capture_AUX0` (what the machine file names) on the node whose
  description is `Audient iD14 Multichannel`.
- `JACK_PORTS` is what a JACK client lists. The first eight are copied from the
  rig's own refusal on 2026-11-02, the one that found the bug: `output ports in
  the graph include: Audient iD14 Analog Surround 4.0:monitor_FL, ...,
  Audient iD14 Multichannel:capture_AUX3 …`. Sushi's `sushi:audio_output_N`
  are observed too, in the sense that the same refusal did not name them while
  the take's processed stems tapped two of them. `capture_AUX4` to `_AUX9` are
  invented, by the pattern of the observed four, so a ten-input device has ten
  inputs here.

The point of the pair is that **they do not agree**: the alias the machine file
uses is in the dump and not in JACK's list, and the JACK name is in JACK's list
and is not an alias. The fakes this replaced listed the alias as a JACK name,
which satisfied both spellings at once and is why no test caught a recorder
that could not find the guitar.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

RIG_DUMP = (Path(__file__).parent / "fixtures" / "pw-dump-rig.json").read_text()

# The machine file's spelling (machines/sonny-dewfall-XPS-9320.yaml) and JACK's.
ALIAS = "Audient iD14:capture_AUX0"
JACK_NAME = "Audient iD14 Multichannel:capture_AUX0"

JACK_PORTS = (
    [f"Audient iD14 Analog Surround 4.0:monitor_{c}" for c in ("FL", "FR", "RL", "RR")]
    + [f"Audient iD14 Multichannel:capture_AUX{n}" for n in range(10)]
    + [f"sushi:audio_output_{n}" for n in range(8)]
)


class FakeJack:
    """Just enough of `jack` for `CaptureEngine`. `visible_after` is how many
    reads of a port's connections come back empty before the link shows, which
    is what a registry that updates asynchronously would do. `ports` is what
    `get_ports` lists; `connect` refuses a port not in it, as JACK does. Each
    input port reads a level of its own source, `levels[source]`, so a block
    pushed shows which port fed which column."""

    class JackError(Exception):
        pass

    def __init__(self, blocksize=256, visible_after=0, ports=JACK_PORTS, levels=None):
        import numpy as np

        self.blocksize, self.visible_after = blocksize, visible_after
        self.ports = list(ports)
        self.levels = dict(levels or {})
        self.activations = 0
        self.clients = []
        self.connected = []  # (source, our port) for every connect, in order
        self.on_activate = []  # called with the client, to play things that happen at activation
        self.on_connect = []   # called after each connect, to play a cycle between links
        outer = self

        class Port:
            def __init__(self, client, name):
                self.client, self.name = client, name
                self.reads = 0

            def get_array(self):
                source = self.client.links.get(self.name)
                return np.full(outer.blocksize, outer.levels.get(source, 0.0), dtype=np.float32)

        class Ports(list):
            def __init__(self, client):
                super().__init__()
                self.client = client

            def register(self, short):
                port = Port(self.client, f"sushi-rig-capture:{short}")
                self.append(port)
                return port

        class Client:
            def __init__(self, name, no_start_server=False):
                self.name, self.links = name, {}
                self.inports = Ports(self)
                self.samplerate = 48000
                self.closed = False
                outer.clients.append(self)

            @property
            def blocksize(self):
                return outer.blocksize

            def get_ports(self, **kwargs):
                return [types.SimpleNamespace(name=name) for name in outer.ports]

            def activate(self):
                outer.activations += 1
                for hook in outer.on_activate:
                    hook(self)

            def deactivate(self, ignore_errors=True):
                pass

            def close(self, ignore_errors=True):
                self.closed = True

            def connect(self, source, port):
                if source not in outer.ports:
                    raise outer.JackError(f"no port {source!r}")
                self.links[port.name] = source
                outer.connected.append((source, port.name))
                for hook in outer.on_connect:
                    hook(self)

            def get_all_connections(self, port):
                port.reads += 1
                if port.reads <= outer.visible_after or port.name not in self.links:
                    return []
                return [types.SimpleNamespace(name=self.links[port.name])]

            def set_process_callback(self, fn): self.process = fn
            def set_xrun_callback(self, fn): pass
            def set_blocksize_callback(self, fn): self.blocksize_cb = fn
            def set_shutdown_callback(self, fn): pass

        self.Client = Client

    def install(self, monkeypatch):
        module = types.ModuleType("jack")
        module.Client, module.JackError = self.Client, self.JackError
        monkeypatch.setitem(sys.modules, "jack", module)
