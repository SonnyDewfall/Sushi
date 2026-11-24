"""Pushed changes (#139): `GET /events`, a server-sent event stream that
tells each open page when the rig or the agent changed, so a knob turned on
one device moves on another without waiting for a poll.

## What is pushed, and why not the state itself

An event names what changed (`{"seq", "what": ["/live/parameter", ...]}`)
and the page answers it by polling at once (`app.js`'s `kick`). It does not
carry the new state, because the page's poll is the one place its answers
are put together: `/health` before `/live`, the sequence number a change in
flight is settled against, a rig switch told from a rig of the same name,
the plugins read again. A second way in would need all of that again. And
the state is costly to make: `/live` is a full read of Sushi (hundreds of
calls), which a push would make on every change whether a page was open or
not; a page's re-fetch costs what one of its polls costs, sooner.

The agent publishes (`Hub.publish`) when:

- a POST, PUT, PATCH or DELETE has been answered (`Agent._push_changes`, a
  middleware, so every route that changes something is covered, one added
  later too); the visualiser's tap lease is left out, since it renews every
  few seconds and changes nothing a page shows;
- an entry goes into the operation log (`Agent._record`): a pedal's change
  is in it, and sends no request;
- `busy` ends (a lifecycle command, a switch, a save done, from a page or
  a pedal); not as it starts, which `Agent.busy` says why;
- the MIDI listener hears a message of a control the machine file names
  (the pedals' lights, `/health`'s counts): the page reads `/health` alone
  for an event that names nothing else;
- the supervisor sees a child of the rig exit that no `down` asked for.

What it cannot see is not pushed: a change made to Sushi from outside the
agent (the Open Stage Control panel talks to Sushi itself), a state file
written by a CLI run with no agent. The poll stays, at its second, for
those and for a stream that has dropped.

## Slow clients, a flood of changes, and shutdown

Publishing never waits: it marks each stream and wakes it. Each stream
writes in its own handler, so a client that reads slowly holds up only its
own stream, and one whose write does not finish within `WRITE_TIMEOUT` is
closed (the page's EventSource opens another, or its poll carries on).
Writes are small, so a peer that has gone without closing (a phone off the
Wi-Fi) fails no write: the stream's socket is given TCP_USER_TIMEOUT and
keepalive (`hold_to_a_deadline`), so the kernel gives it up within about 30
s, and one address may hold `MAX_PER_PEER` streams. Changes that come while
a stream is writing, or within `GAP` of its last event, are sent together as
one event: a pedal swept through a hundred
values is a few events a second, not a hundred, and each page polls at
most that often. A stream sends a comment every `HEARTBEAT` seconds, which
keeps a proxy from timing it out and finds a client that has gone. At most
`MAX_STREAMS` are open at once; past it, 503. On shutdown (`close`) every
stream is woken and ends, so aiohttp's wait for its handlers does not wait
for them.
"""

from __future__ import annotations

import asyncio
import json
import threading
from typing import Any

# Changes that come within this long of a stream's last event wait and go
# with the next: the most events a second a stream sends is 1 / GAP. A
# guess, not measured on the rig: short against "well under a second", long
# against a /live read on the rig (hundreds of Sushi calls).
GAP = 0.25
# A comment line this often keeps a quiet stream open through a proxy and
# finds a client that has gone without closing.
HEARTBEAT = 15.0
# How often a quiet stream looks whether its client has gone, so a closed
# tab's handler ends within this, not at the next heartbeat.
LOOK = 1.0
# A write that has not finished in this long is a client not reading: its
# stream is closed rather than kept waiting.
WRITE_TIMEOUT = 5.0
# Streams open at once, past which a new one is refused (503): a page has
# one, so this is far more than a rig's pages.
MAX_STREAMS = 32
# What a page's EventSource waits before connecting again, in ms: sent as
# the stream's `retry`.
RETRY_MS = 1000
# The most distinct paths one event names; past it, "many".
MOST_NAMED = 16
# Streams open at once from one address, past which a new one from it is
# refused (503): a phone has a page or two, so a device that opens more is
# one whose connections are not closing. Loopback is exempt (`Hub.refusal`).
MAX_PER_PEER = 4
# A stream's socket gives up on a peer that has stopped acknowledging what
# is sent (a phone that left the Wi-Fi: half-open, so nothing fails a
# write) after this long, where the platform has TCP_USER_TIMEOUT (Linux),
# with keepalive probes from KEEPALIVE_IDLE seconds of quiet. Writes are a
# few hundred bytes, far under what makes aiohttp wait on a drain, so
# `WRITE_TIMEOUT` alone would never fire for one: without these a gone
# phone held its stream for the kernel's own retransmission limit, about 15
# minutes.
USER_TIMEOUT_MS = 30_000
KEEPALIVE_IDLE = 10
KEEPALIVE_INTERVAL = 5
KEEPALIVE_COUNT = 3


def is_loopback(peer: str) -> bool:
    return peer == "::1" or peer.startswith("127.") or peer.startswith("::ffff:127.")


def hold_to_a_deadline(sock: Any) -> list[str]:
    """Set the stream socket's TCP_USER_TIMEOUT and keepalive (above), each
    where the platform has it; the names of those set. A socket that refuses
    one keeps the rest: the stream still works, it is only slower to give
    up on a gone peer."""
    import socket

    done = []
    options = [
        (socket.SOL_SOCKET, "SO_KEEPALIVE", 1),
        (socket.IPPROTO_TCP, "TCP_USER_TIMEOUT", USER_TIMEOUT_MS),
        (socket.IPPROTO_TCP, "TCP_KEEPIDLE", KEEPALIVE_IDLE),
        (socket.IPPROTO_TCP, "TCP_KEEPINTVL", KEEPALIVE_INTERVAL),
        (socket.IPPROTO_TCP, "TCP_KEEPCNT", KEEPALIVE_COUNT),
    ]
    for level, name, value in options:
        option = getattr(socket, name, None)
        if option is None or sock is None:
            continue
        try:
            sock.setsockopt(level, option, value)
        except OSError:
            continue
        done.append(name)
    return done


class Stream:
    """One open stream: what has changed since its last event, the event
    its handler waits on, and the address it came from (`peer`)."""

    def __init__(self, peer: str | None = None) -> None:
        self.peer = peer
        self.pending: set[str] = set()
        self.wake = asyncio.Event()
        self.closing = False

    def note(self, what: str) -> None:
        if len(self.pending) < MOST_NAMED:
            self.pending.add(what)
        else:
            self.pending.add("many")
        self.wake.set()

    def take(self) -> list[str]:
        """What changed, now sent: sorted, and forgotten here."""
        taken = sorted(self.pending)
        self.pending.clear()
        self.wake.clear()
        return taken


class Hub:
    """The open streams, and a count of the changes published (`seq`)."""

    def __init__(self) -> None:
        self.streams: set[Stream] = set()
        self.seq = 0
        self.closed = False
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: int | None = None

    def publish(self, what: str) -> None:
        """Say `what` changed to every open stream. Never waits. From the
        loop's own thread it marks the streams at once; from another thread
        (a Sushi call's worker) it is handed to the loop."""
        if self._loop is not None and threading.get_ident() != self._thread:
            try:
                self._loop.call_soon_threadsafe(self._publish, what)
            except RuntimeError:  # the loop is closed: nothing is listening
                pass
            return
        self._publish(what)

    def _publish(self, what: str) -> None:
        self.seq += 1
        for stream in self.streams:
            stream.note(what)

    def bind(self) -> None:
        """Remember the loop the agent serves on, and its thread, so a
        `publish` from another thread is handed to it, from the start (not
        only once a first stream has opened). Called on that loop, as the
        app starts; reopens a hub an earlier stop closed."""
        self._loop = asyncio.get_running_loop()
        self._thread = threading.get_ident()
        self.closed = False

    def refusal(self, peer: str | None) -> str | None:
        """Why a new stream from `peer` is not opened, or None: the hub is
        closed ("stopping"), `MAX_STREAMS` are open, or `MAX_PER_PEER` are
        open from that address ("too_many_streams"). A loopback peer is held
        to the total alone: a reverse proxy on this machine (Tailscale Serve,
        nginx) brings every phone's stream from 127.0.0.1. Pure over the
        hub's state."""
        if self.closed:
            return "stopping"
        if len(self.streams) >= MAX_STREAMS:
            return "too_many_streams"
        if peer and not is_loopback(peer) and sum(s.peer == peer for s in self.streams) >= MAX_PER_PEER:
            return "too_many_streams"
        return None

    def open(self, peer: str | None = None) -> Stream | None:
        """A new stream from `peer`, or None when `refusal` says why not.
        Called on the loop."""
        if self.refusal(peer) is not None:
            return None
        if self._loop is None:
            self.bind()
        stream = Stream(peer)
        self.streams.add(stream)
        return stream

    def drop(self, stream: Stream) -> None:
        self.streams.discard(stream)

    def close(self) -> None:
        """End every stream (the agent is stopping) and open no more."""
        self.closed = True
        for stream in self.streams:
            stream.closing = True
            stream.wake.set()


def event(name: str, data: dict[str, Any], seq: int | None = None) -> bytes:
    """One server-sent event: `event:`, `id:` and one `data:` line of
    JSON, which has no newline in it. Pure."""
    lines = [f"event: {name}"]
    if seq is not None:
        lines.append(f"id: {seq}")
    lines.append("data: " + json.dumps(data, separators=(",", ":")))
    return ("\n".join(lines) + "\n\n").encode("utf-8")


def opening(seq: int) -> bytes:
    """What a stream starts with: the reconnection delay, and a `hello`
    with the count so far. Pure."""
    return f"retry: {RETRY_MS}\n\n".encode("ascii") + event("hello", {"seq": seq}, seq)


HEARTBEAT_LINE = b": still here\n\n"
