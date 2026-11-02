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
from sushi_rig.live import TAB_RESELECT_DELAY, decide_move, refresh_panel

CHAIN = ["compressor", "overdrive", "octave", "eq", "chorus", "delay", "reverb"]


def command(chain, processor, direction):
    """Where `decide_move` sends `processor`, as (before, add_to_back) by name,
    or None when nothing would be sent."""
    ids = {"t": 100, **{name: i + 1 for i, name in enumerate(chain)}}
    move = decide_move({"t": chain}, ids, processor, direction)
    if move.outcome != "planned":
        return None
    by_id = {i: name for name, i in ids.items()}
    _, _, before, back = move.command
    return {"before": None if back else by_id[before], "add_to_back": back}


def test_moving_earlier_lands_before_the_previous_processor():
    assert command(CHAIN, "overdrive", -1) == {"before": "compressor", "add_to_back": False}


def test_moving_later_lands_before_the_one_after_next():
    """Moving past exactly one neighbour means inserting before whatever
    follows that neighbour."""
    assert command(CHAIN, "overdrive", 1) == {"before": "eq", "add_to_back": False}


def test_moving_the_second_to_last_later_goes_to_the_back():
    """There is nothing to be "before" past the end, so Sushi needs
    add_to_back instead."""
    assert command(CHAIN, "delay", 1) == {"before": None, "add_to_back": True}


def test_first_cannot_move_earlier():
    assert command(CHAIN, "compressor", -1) is None


def test_last_cannot_move_later():
    assert command(CHAIN, "reverb", 1) is None


def test_unknown_processor_is_a_no_op():
    assert command(CHAIN, "not-on-this-track", 1) is None


def test_single_plugin_chain_cannot_move_either_way():
    assert command(["only"], "only", -1) is None
    assert command(["only"], "only", 1) is None


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


# --------------------------------------------------------------------------- #
# The decision a move request is made on, pure
# --------------------------------------------------------------------------- #

CHAINS = {"main": ["comp", "drive", "reverb"], "aux": ["delay"]}
IDS = {"main": 1, "comp": 2, "drive": 3, "reverb": 4, "aux": 9, "delay": 7}


@pytest.mark.parametrize("processor, direction, outcome, command", [
    ("drive", -1, "planned", (3, 1, 2, False)),   # before comp
    ("comp", 1, "planned", (2, 1, 4, False)),     # before reverb: one step later
    ("drive", 1, "planned", (3, 1, 0, True)),     # past the last: the back of the chain
    ("delay", 1, "unchanged", None),
    ("comp", -1, "unchanged", None),
    ("reverb", 1, "unchanged", None),
    ("main", -1, "absent", None),                 # a track is not on a chain
    ("nope", 1, "absent", None),
])
def test_a_move_is_decided_once_from_what_the_walk_found(processor, direction, outcome, command):
    move = live.decide_move(CHAINS, IDS, processor, direction)
    assert (move.outcome, move.command) == (outcome, command)
    assert move.chains == CHAINS
    assert move.chain == (None if outcome == "absent" else CHAINS["aux" if processor == "delay" else "main"])
