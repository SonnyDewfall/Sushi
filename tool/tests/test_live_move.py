"""Position maths for reordering the plugin chain.

Pure, so it runs without a Sushi instance. Worth pinning precisely: getting it
wrong silently rearranges someone's signal chain, and the "move later past the
last element" case has to become add_to_back rather than "before" anything,
because that is how Sushi's API expresses position.
"""

import sys
import types

import pytest

from sushi_rig import live
from sushi_rig.live import TAB_RESELECT_DELAY, plan_move, refresh_panel

CHAIN = ["compressor", "overdrive", "octave", "eq", "chorus", "delay", "reverb"]


def test_moving_earlier_lands_before_the_previous_processor():
    assert plan_move(CHAIN, "overdrive", -1) == {
        "before": "compressor",
        "add_to_back": False,
    }


def test_moving_later_lands_before_the_one_after_next():
    """Moving past exactly one neighbour means inserting before whatever
    follows that neighbour."""
    assert plan_move(CHAIN, "overdrive", 1) == {"before": "eq", "add_to_back": False}


def test_moving_the_second_to_last_later_goes_to_the_back():
    """There is nothing to be "before" past the end, so Sushi needs
    add_to_back instead."""
    assert plan_move(CHAIN, "delay", 1) == {"before": None, "add_to_back": True}


def test_first_cannot_move_earlier():
    assert plan_move(CHAIN, "compressor", -1) is None


def test_last_cannot_move_later():
    assert plan_move(CHAIN, "reverb", 1) is None


def test_unknown_processor_is_a_no_op():
    assert plan_move(CHAIN, "not-on-this-track", 1) is None


def test_single_plugin_chain_cannot_move_either_way():
    assert plan_move(["only"], "only", -1) is None
    assert plan_move(["only"], "only", 1) is None


class _FakeClient:
    """Records what would have gone out on the wire."""

    sent: list[tuple[str, object]] = []

    def __init__(self, host, port):
        self.host, self.port = host, port

    def send_message(self, address, value):
        _FakeClient.sent.append((address, value))


@pytest.fixture
def fake_osc(monkeypatch):
    _FakeClient.sent = []
    module = types.ModuleType("pythonosc.udp_client")
    module.SimpleUDPClient = _FakeClient
    monkeypatch.setitem(sys.modules, "pythonosc.udp_client", module)
    return _FakeClient


def test_refresh_opens_the_session_then_reselects_the_tab(fake_osc):
    """Both halves matter: the reload puts the tabs in the new order, the
    re-select keeps you on the plugin you just moved."""
    refresh_panel("/tmp/panel.json", "reverb", tab_delay=0)
    assert fake_osc.sent == [
        ("/SESSION/OPEN", "/tmp/panel.json"),
        ("/TABS", "reverb"),
    ]


def test_tab_reselect_is_deferred_rather_than_sent_immediately(fake_osc):
    """The regression this guards: sent back to back, the /TABS lands while
    open-stage-control is still rebuilding its widget tree and is silently
    dropped, so every move bounced you to the first tab. Verified against the
    running rig — the identical message works once the rebuild has settled.

    The deferral is injected, so this asks only what refresh_panel did with it:
    it handed over the delay and an action, sent nothing more itself, and the
    action, when fired, sends the /TABS."""
    scheduled = []
    refresh_panel(
        "/tmp/panel.json", "reverb",
        schedule=lambda delay, action: scheduled.append((delay, action)),
    )
    assert fake_osc.sent == [("/SESSION/OPEN", "/tmp/panel.json")], (
        "/TABS must not go out in the same breath as /SESSION/OPEN"
    )
    assert len(scheduled) == 1
    delay, action = scheduled[0]
    assert delay == TAB_RESELECT_DELAY

    action()
    assert fake_osc.sent[1] == ("/TABS", "reverb"), "but it must still arrive"


def test_by_default_the_deferral_goes_through_the_timer_helper(fake_osc, monkeypatch):
    """Without an injected schedule the real path is `live._start_timer`, handed
    the default delay. It is replaced here, so no wall clock is spent and no
    stdlib module is patched."""
    started = []
    monkeypatch.setattr(live, "_start_timer", lambda delay, action: started.append((delay, action)))
    refresh_panel("/tmp/panel.json", "reverb")
    assert [d for d, _ in started] == [TAB_RESELECT_DELAY]
    started[0][1]()
    assert fake_osc.sent[-1] == ("/TABS", "reverb")


def test_refresh_without_a_tab_only_reloads(fake_osc):
    refresh_panel("/tmp/panel.json", tab_delay=0)
    assert fake_osc.sent == [("/SESSION/OPEN", "/tmp/panel.json")]
