"""A stand-in for JACK-Client, and the ports it lists, for the capture engine's tests.

Read this for what it is. `FakeJack` is a few dozen lines of Python written from
JACK-Client's documented shape, not a capture of the real thing, so it proves
the engine's own logic (what it calls, how many times, what it does when a read
comes back empty) and nothing about JACK or PipeWire.

What is observed, and what is not, in `JACK_PORTS`:

- **Observed: the names.** The first eight are copied from the rig's own
  refusal on 2026-11-02, the one that found the bug: `output ports in the graph
  include: Audient iD14 Analog Surround 4.0:monitor_FL, ..., Audient iD14
  Multichannel:capture_AUX3 …`. Sushi's `sushi:audio_output_N` are observed in
  the sense that the same refusal did not name them while the take's processed
  stems tapped two of them. `capture_AUX4` to `_AUX9` are invented, by the
  pattern of the observed four.
- **A guess: the aliases.** Each port's aliases are written as pipewire-jack is
  understood to give them, its `object.path` and its PipeWire `port.alias`
  (`Audient iD14:capture_AUX0`, the machine file's spelling; the paths are read
  off `fixtures/pw-dump-rig.json`). Nobody has seen `jack_lsp -A` on the rig:
  the MANIFEST's rig check asks for it, and if the alias is absent there the
  engine refuses every take and this file is wrong.

The point of the list is that **the name and the machine file's spelling
differ**: `Audient iD14:capture_AUX0` is an alias here and no port's name. The
fakes this replaced listed the alias as a JACK name, which satisfied both
spellings at once and is why no test caught a recorder that could not find the
guitar.
"""

from __future__ import annotations

import sys
import types

# The machine file's spelling (machines/sonny-dewfall-XPS-9320.yaml) and JACK's name.
ALIAS = "Audient iD14:capture_AUX0"
JACK_NAME = "Audient iD14 Multichannel:capture_AUX0"


def _rig_ports() -> dict[str, tuple[str, ...]]:
    ports: dict[str, tuple[str, ...]] = {}
    for n, c in enumerate(("FL", "FR", "RL", "RR")):
        ports[f"Audient iD14 Analog Surround 4.0:monitor_{c}"] = (
            f"alsa:acp:iD14:3:playback:monitor_{n}", f"Audient iD14:monitor_{c}")
    for n in range(10):
        ports[f"Audient iD14 Multichannel:capture_AUX{n}"] = (
            f"alsa:acp:iD14:2:capture:capture_{n}", f"Audient iD14:capture_AUX{n}")
    for n in range(8):
        ports[f"sushi:audio_output_{n}"] = (f"sushi:output_{n}", f"sushi:audio_output_{n}")
    return ports


JACK_PORTS: dict[str, tuple[str, ...]] = _rig_ports()


class FakeJack:
    """Just enough of `jack` for `CaptureEngine`. `visible_after` is how many
    reads of a port's connections come back empty before the link shows, which
    is what a registry that updates asynchronously would do. `ports` is what
    `get_ports` lists, name to aliases; `connect` refuses a source that is not
    a listed port's name, as JACK refuses one it has no port for. Each input
    port reads a level of its own source, `levels[source]`, so a block pushed
    shows which port fed which column."""

    class JackError(Exception):
        pass

    def __init__(self, blocksize=256, visible_after=0, ports=None, levels=None):
        import numpy as np

        self.blocksize, self.visible_after = blocksize, visible_after
        self.ports = dict(JACK_PORTS if ports is None else ports)
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
                return [types.SimpleNamespace(name=name, aliases=list(aliases))
                        for name, aliases in outer.ports.items()]

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

    def listed(self):
        """What `capture.jack_ports` reads from a client of this JACK."""
        from sushi_rig.capture import jack_ports

        return jack_ports(self.Client("listing"))
