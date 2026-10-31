"""Record and stop (#57): the decisions on their own, against a fake session.

Pure, so it runs everywhere. A whole take — a real folder, recorder and stems,
with only the engine fake — needs numpy and is in test_transport_take.py.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from sushi_rig import take
from sushi_rig.transport import (
    NoEngine,
    Transport,
    TransportError,
    auto_name,
)

NOW = datetime(2026, 10, 31, 14, 25, 1)


class FakeSession:
    """Records what the transport asked of it, and does what it is told."""

    def __init__(self, start_error=None, stop_error=None):
        self.calls = []
        self.start_error = start_error
        self.stop_error = stop_error

    def start(self, name):
        self.calls.append(("start", name))
        if self.start_error:
            raise self.start_error
        return f"recording {name}"

    def stop(self):
        self.calls.append(("stop",))
        if self.stop_error:
            raise self.stop_error
        return "recorded"


# --------------------------------------------------------------------------- #
# Decisions
# --------------------------------------------------------------------------- #

def test_a_take_recorded_without_a_name_is_named_by_when_it_was_recorded():
    assert auto_name(NOW) == "take-20261031-142501"
    assert not take.check_name(auto_name(NOW))


def test_record_uses_the_last_name_received():
    session = FakeSession()
    transport = Transport(session, clock=lambda: NOW)
    transport.on_name("  riff-idea ")
    assert transport.on_record() == "recording riff-idea"
    assert session.calls == [("start", "riff-idea")]
    assert transport.recording == "riff-idea"


def test_record_with_no_name_still_records():
    session = FakeSession()
    assert Transport(session, clock=lambda: NOW).on_record() == "recording take-20261031-142501"


def test_a_name_that_could_escape_the_takes_folder_never_reaches_the_session():
    session = FakeSession()
    transport = Transport(session)
    transport.on_name("../../etc")
    said = transport.on_record()
    assert said.startswith("record refused: invalid take name")
    assert session.calls == []
    assert transport.recording is None


def test_record_while_recording_is_refused_not_restarted():
    session = FakeSession()
    transport = Transport(session)
    transport.on_name("riff-idea")
    transport.on_record()
    transport.on_name("other")
    assert transport.on_record() == "already recording riff-idea — press STOP first"
    assert session.calls == [("start", "riff-idea")]


def test_stop_while_stopped_says_so():
    session = FakeSession()
    assert Transport(session).on_stop() == "not recording"
    assert session.calls == []


def test_stop_ends_the_take_and_record_works_again():
    session = FakeSession()
    transport = Transport(session)
    transport.on_name("riff-idea")
    transport.on_record()
    assert transport.on_stop() == "recorded"
    assert transport.recording is None
    assert transport.on_record() == "recording riff-idea"


def test_a_take_that_would_not_start_leaves_the_transport_stopped():
    transport = Transport(FakeSession(start_error=TransportError("no ports")))
    assert transport.on_record() == "record failed: no ports"
    assert transport.recording is None
    assert transport.on_stop() == "not recording"


@pytest.mark.parametrize("error", [TransportError("disk gone"), RuntimeError("disk gone")])
def test_a_stop_that_fails_still_stops(error):
    """Otherwise a failed stop leaves the player unable to record again."""
    transport = Transport(FakeSession(stop_error=error))
    transport.on_name("riff-idea")
    transport.on_record()
    said = transport.on_stop()
    assert said.startswith("stop failed for riff-idea:") and "disk gone" in said
    assert transport.recording is None


def test_an_unexpected_error_never_reaches_the_listener():
    transport = Transport(FakeSession(start_error=RuntimeError("boom")))
    assert transport.on_record() == "record failed: unexpected error: boom"


def test_without_an_engine_record_says_why_it_cannot():
    transport = Transport()
    said = transport.on_record()
    assert said == f"record failed: {NoEngine.MESSAGE}"
    assert "no capture engine" in said
    assert transport.recording is None


def test_a_reloaded_panel_forgets_the_name_it_no_longer_shows():
    """The reload empties the name field; RECORD on an empty field must not
    record, and archive, under a name typed before it."""
    session = FakeSession()
    transport = Transport(session, clock=lambda: NOW)
    transport.on_name("riff-idea")
    assert transport.on_panel_reloaded() is None
    assert transport.on_record() == "recording take-20261031-142501"


def test_a_reloaded_panel_is_told_a_take_is_still_running():
    transport = Transport(FakeSession())
    transport.on_name("riff-idea")
    transport.on_record()
    assert transport.on_panel_reloaded() == "recording riff-idea"
    assert transport.recording == "riff-idea"


def test_closing_the_listener_finishes_a_running_take():
    session = FakeSession()
    transport = Transport(session)
    assert transport.close() is None
    transport.on_record()
    assert transport.close() == "recorded"
    assert session.calls[-1] == ("stop",)
