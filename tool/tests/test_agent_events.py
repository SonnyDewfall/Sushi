"""The pushed changes (#139): `GET /events`, with no browser.

What a stream says and when (`events.py`), what publishes to it (a write
answered, the operation log, `busy`, a crash the supervisor saw, a thread
that is not the loop's), and the three constraints: the cross-site guard is
in front of it, every stream ends when the agent stops so nothing waits for
it, and a client that does not read holds up nothing but its own stream.
The two pages in Chromium are test_ui_push.py's.

The agent is real and served on 127.0.0.1 by aiohttp's own runner, as
`serve` runs it; the rig is a `describe` and a runner standing in, as in
test_agent.py. Nothing here is Sushi, a phone or a pedal.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time

import pytest

aiohttp = pytest.importorskip("aiohttp")
from aiohttp import web  # noqa: E402

from sushi_rig import agent as agent_module  # noqa: E402
from sushi_rig import events as events_module  # noqa: E402
from sushi_rig.agent import Agent  # noqa: E402
from sushi_rig.supervisor import Outcome  # noqa: E402

DOWN = {"verdict": "absent", "running": False, "message": "No rig is running."}


class Runner:
    def __init__(self):
        self.calls = []

    async def __call__(self, args):
        self.calls.append(list(args))
        return Outcome(True, 0, "down\n", "")


@pytest.fixture(autouse=True)
def _agents_release_their_pools(monkeypatch):
    """As test_agent.py's: each Agent's pool of Sushi-call threads is shut."""
    made = []
    original = Agent.__init__

    def recording(self, *args, **kwargs):
        original(self, *args, **kwargs)
        made.append(self)

    monkeypatch.setattr(Agent, "__init__", recording)
    yield
    for agent in made:
        agent._executor.shutdown(wait=True, cancel_futures=True)


class Served:
    """An agent on a real port, run by `web.AppRunner` as `serve` runs it
    (its shutdown waits up to 60 s for a handler), inside one test's loop."""

    def __init__(self, agent):
        self.agent = agent

    async def __aenter__(self):
        self.runner = web.AppRunner(self.agent.app())
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        self.port = site._server.sockets[0].getsockname()[1]
        self.url = f"http://127.0.0.1:{self.port}"
        self.session = aiohttp.ClientSession()
        return self

    async def stop(self):
        await self.runner.cleanup()

    async def __aexit__(self, *exc):
        await self.session.close()
        await self.stop()


def served(**kwargs):
    return Served(Agent(describe=lambda: dict(DOWN), run=Runner(), **kwargs))


async def next_event(stream, timeout=2.0):
    """The next event's fields ({"event", "id", "data", "retry"}), comments
    and the blank line skipped; None at the end of the stream."""
    fields = {}
    while True:
        line = await asyncio.wait_for(stream.content.readline(), timeout)
        if not line:
            return None
        text = line.decode("utf-8").rstrip("\n")
        if text == "":
            if fields:
                if "data" in fields:
                    fields["data"] = json.loads(fields["data"])
                return fields
            continue
        if text.startswith(":"):
            continue
        name, _, value = text.partition(": ")
        fields[name] = value


async def opened(server, headers=None):
    stream = await server.session.get(server.url + "/events", headers=headers or {})
    assert stream.status == 200, await stream.text()
    assert stream.headers["Content-Type"].startswith("text/event-stream")
    assert stream.headers["Cache-Control"] == "no-store"
    assert await next_event(stream) == {"retry": str(events_module.RETRY_MS)}
    hello = await next_event(stream)
    assert hello["event"] == "hello" and hello["data"] == {"seq": server.agent.events.seq}
    return stream


def test_a_write_is_pushed_as_it_is_answered_naming_what_changed():
    async def go():
        async with served() as server:
            stream = await opened(server)
            began = time.monotonic()
            answer = await server.session.post(server.url + "/rig/down")
            assert answer.status == 200
            event = await next_event(stream)
            took = time.monotonic() - began
            assert event["event"] == "changed"
            assert event["data"]["seq"] == int(event["id"])
            # `busy` went to "down" and back (pushed as it ended), and the
            # route was answered: what came within the gap after the first
            # event goes in the next.
            said = set(event["data"]["what"])
            assert said <= {"busy", "/rig/down"} and "busy" in said, said
            assert took < 0.5, took
            while "/rig/down" not in said:
                event = await next_event(stream, timeout=events_module.GAP + 0.5)
                said |= set(event["data"]["what"])
            assert said == {"busy", "/rig/down"}
            assert event["data"]["seq"] == server.agent.events.seq, "the last event counts every change"
            stream.close()

    asyncio.run(go())


def test_a_refused_write_and_a_read_push_nothing_and_the_tap_lease_is_not_pushed():
    async def go():
        async with served() as server:
            stream = await opened(server)
            refused = await server.session.post(server.url + "/rig/down",
                                                headers={"Sec-Fetch-Site": "cross-site"})
            assert refused.status == 403
            assert (await server.session.get(server.url + "/health")).status == 200
            tap = await server.session.post(server.url + "/visualiser/tap", json={"page": "p"})
            assert tap.status in (403, 409)  # not local, or refused: either way not pushed
            with pytest.raises(asyncio.TimeoutError):
                await next_event(stream, timeout=0.6)
            assert server.agent.events.seq == 0
            stream.close()

    asyncio.run(go())


def test_a_log_entry_busy_a_crash_and_a_thread_are_each_pushed():
    async def go():
        async with served() as server:
            stream = await opened(server)
            agent = server.agent
            for publish, said in [
                (lambda: agent._record("bypass", {"processor": "drive", "bypassed": True}, False), "log"),
                # A command starting is not pushed (`Agent.busy` says why); ending is,
                # a pedal's switch too; nothing running ending again is no change.
                (lambda: setattr(agent, "busy", "switch"), None),
                (lambda: setattr(agent, "busy", None), "busy"),
                (lambda: setattr(agent, "busy", None), None),
                (lambda: agent.supervisor.changed(), "supervisor"),
                (lambda: threading.Thread(target=agent.events.publish, args=("from a worker",)).start(),
                 "from a worker"),
            ]:
                publish()
                if said is None:
                    with pytest.raises(asyncio.TimeoutError):
                        await next_event(stream, timeout=events_module.GAP + 0.3)
                    continue
                event = await next_event(stream)
                assert event["data"]["what"] == [said], (said, event)
            stream.close()

    asyncio.run(go())


def test_changes_close_together_go_as_one_event_and_a_flood_is_named_many():
    async def go():
        async with served() as server:
            stream = await opened(server)
            for n in range(100):  # a pedal swept: a hundred values at once
                server.agent.events.publish(f"/live/parameter {n}")
            first = await next_event(stream)
            assert len(first["data"]["what"]) == events_module.MOST_NAMED + 1
            assert "many" in first["data"]["what"]
            # Within the gap after an event, what comes waits and goes together.
            began = time.monotonic()
            server.agent.events.publish("a")
            server.agent.events.publish("b")
            second = await next_event(stream)
            assert second["data"]["what"] == ["a", "b"]
            with pytest.raises(asyncio.TimeoutError):
                await next_event(stream, timeout=0.5)
            assert time.monotonic() - began >= 0.5
            stream.close()

    asyncio.run(go())


def test_every_stream_ends_when_the_agent_stops_and_the_stop_does_not_wait_for_them():
    """aiohttp's runner waits up to 60 s for a handler to finish on a stop:
    without the streams being ended, this stop would take that long."""
    async def go():
        server = served()
        await server.__aenter__()
        streams = [await opened(server) for _ in range(3)]
        assert len(server.agent.events.streams) == 3
        began = time.monotonic()
        await server.stop()
        took = time.monotonic() - began
        for stream in streams:
            assert await next_event(stream) is None, "each stream ends"
        await server.session.close()
        assert took < 2.0, took
        assert server.agent.events.streams == set()

    asyncio.run(go())


def test_a_stream_whose_page_has_gone_ends_within_a_second():
    async def go():
        async with served() as server:
            stream = await opened(server)
            assert len(server.agent.events.streams) == 1
            stream.close()
            deadline = time.monotonic() + events_module.LOOK + 1.0
            while server.agent.events.streams and time.monotonic() < deadline:
                await asyncio.sleep(0.05)
            assert server.agent.events.streams == set()

    asyncio.run(go())


def test_past_the_most_streams_a_new_one_is_refused_and_the_page_polls(monkeypatch):
    monkeypatch.setattr(events_module, "MAX_STREAMS", 2)

    async def go():
        async with served() as server:
            kept = [await opened(server), await opened(server)]
            refused = await server.session.get(server.url + "/events")
            assert refused.status == 503
            body = await refused.json()
            assert body["code"] == "too_many_streams" and "2 streams" in body["reason"]
            kept[0].close()
            deadline = time.monotonic() + events_module.LOOK + 1.0
            while len(server.agent.events.streams) > 1 and time.monotonic() < deadline:
                await asyncio.sleep(0.05)
            (await opened(server)).close()  # room again
            kept[1].close()

    asyncio.run(go())


def test_a_client_that_does_not_read_holds_up_nothing_but_its_own_stream(monkeypatch):
    """A stream's first write made far larger than any socket buffer, to a
    client that never reads: the write cannot finish. Meanwhile /health and
    a write answer at once and another page's stream is told of the change;
    after `WRITE_TIMEOUT` the stuck stream is dropped."""
    monkeypatch.setattr(events_module, "WRITE_TIMEOUT", 0.8)
    big = b": " + b"x" * (32 * 1024 * 1024) + b"\n\n"

    async def go():
        async with served() as server:
            reader = await opened(server)  # the page that reads, opened while the opening is small
            monkeypatch.setattr(events_module, "opening", lambda seq: big)
            stuck_reader, stuck = await asyncio.open_connection("127.0.0.1", server.port)
            stuck.write(f"GET /events HTTP/1.1\r\nHost: 127.0.0.1:{server.port}\r\n\r\n".encode())
            await stuck.drain()
            deadline = time.monotonic() + 2.0
            while len(server.agent.events.streams) < 2 and time.monotonic() < deadline:
                await asyncio.sleep(0.02)
            assert len(server.agent.events.streams) == 2, "the stuck stream is open and writing"
            began = time.monotonic()
            assert (await server.session.get(server.url + "/health")).status == 200
            assert (await server.session.post(server.url + "/rig/down")).status == 200
            event = await next_event(reader)
            assert event["event"] == "changed"
            assert time.monotonic() - began < 0.5
            deadline = time.monotonic() + events_module.WRITE_TIMEOUT + 2.0
            while len(server.agent.events.streams) > 1 and time.monotonic() < deadline:
                await asyncio.sleep(0.05)
            assert len(server.agent.events.streams) == 1, "the stuck stream was dropped"
            stuck.close()
            reader.close()

    asyncio.run(go())


@pytest.mark.parametrize("headers", [
    {"Sec-Fetch-Site": "cross-site"},
    {"Origin": "https://example.invalid"},
    {"Origin": "http://127.0.0.1:1", "Sec-Fetch-Site": "same-site"},
    {"Origin": "null"},
], ids=["another site", "another site's Origin", "another port", "no origin"])
def test_a_stream_another_sites_page_asks_for_is_refused_before_it_opens(headers):
    async def go():
        async with served() as server:
            refused = await server.session.get(server.url + "/events", headers=headers)
            assert refused.status == 403
            assert (await refused.json())["code"] == "cross_site"
            assert server.agent.events.streams == set()

    asyncio.run(go())


def test_the_agents_own_page_and_curl_may_open_a_stream():
    async def go():
        async with served() as server:
            own = {"Origin": server.url, "Sec-Fetch-Site": "same-origin", "Sec-Fetch-Mode": "cors"}
            (await opened(server, own)).close()
            (await opened(server)).close()  # curl: no Origin, no Sec-Fetch-Site

    asyncio.run(go())


def test_the_events_route_is_the_one_read_the_cross_site_check_is_asked_of():
    assert agent_module.GUARDED_READS == frozenset({"/events"})


def test_an_event_is_one_data_line_of_json():
    assert events_module.event("changed", {"seq": 3, "what": ["/live/bypass"]}, 3) == \
        b'event: changed\nid: 3\ndata: {"seq":3,"what":["/live/bypass"]}\n\n'
    assert events_module.opening(0).startswith(b"retry: 1000\n\nevent: hello\n")
