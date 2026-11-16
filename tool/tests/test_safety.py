"""Safety (#137): the master's dip around a live structural edit, and the CPU
check before a live add.

The dip's decisions are `ramp.py`'s and the budget's `top.add_budget`; both
are pinned here first. Then the agent, against the fake Sushis the edit and
switch tests already use (`test_agent_edits.EditingLive`, `test_agent.RigLive`):
the order of the calls (gain down, edit, confirm, gain back), an edit that
fails part-way, one that raises, the level failing to come back, and a master
volume set while the dip is under way, from the page and from beside the
agent. No Sushi: what Sushi's smoother does with the two sets is read from its
source (`ramp.py`) and was not heard on the rig.
"""

from __future__ import annotations

import asyncio
import copy
import math
from pathlib import Path

import pytest

pytest.importorskip("aiohttp")
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from sushi_rig import ramp, top  # noqa: E402
from sushi_rig.rig import QUANTUM, RATE  # noqa: E402

from test_agent_edits import (  # noqa: E402,F401  (the fixture releases each agent's pool)
    DELAY, DIP_BACK, DIP_DOWN, MASTER_LEVEL, EditingLive, _agents_release_their_pools, call, edit_agent,
    log, undo,
)

FIXTURES = Path(__file__).parent / "fixtures"
PW_TOP = (FIXTURES / "pw-top-b-n2.txt").read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# The dip's decisions
# --------------------------------------------------------------------------- #

def test_the_level_goes_back_unless_someone_else_set_it_during_the_dip():
    assert ramp.restore_to(0.79, ramp.FLOOR) == 0.79, "read back as the dip left it"
    assert ramp.restore_to(0.79, 0.79) == 0.79, "a read that still sees the level from before the set"
    assert ramp.restore_to(0.79, None) == 0.79, "not read: put back, never left silent"
    assert ramp.restore_to(0.79, float("nan")) == 0.79
    assert ramp.restore_to(0.79, 0.5) is None, "set by someone else meanwhile: theirs is newer, left"
    assert ramp.restore_to(0.79, 0.79 + 1e-7) == 0.79, "a float32's worth from the level is the level"


def test_a_master_at_its_floor_is_not_dipped():
    assert not ramp.worth_dipping(0.0) and ramp.worth_dipping(1e-3) and ramp.worth_dipping(MASTER_LEVEL)


def test_the_wait_is_the_depth_on_sushis_smoother_plus_one_block():
    """Sushi 1.3.0's track gain smoother (track.cpp, value_smoother.h): one
    pole, coefficient exp(-2.19 / (20 ms * chunks a second)) per 64-sample
    chunk. Worked through at 48 kHz chunk by chunk, it is DEPTH_DB down after
    SETTLE less one block, and the wait adds the block."""
    chunks_a_second = RATE / ramp.CHUNK
    coefficient = math.exp(-ramp.RISE_TIME_CONSTANTS / (ramp.SMOOTHING_S * chunks_a_second))
    down = ramp.SETTLE - QUANTUM / RATE
    level_db = 20 * math.log10(coefficient ** (down * chunks_a_second))
    assert level_db == pytest.approx(-ramp.DEPTH_DB, abs=1e-6)
    assert ramp.falling_db_per_second() == pytest.approx(-951.1, abs=0.1)
    rise_to_1db = math.log(1 - 10 ** (-1 / 20)) / math.log(coefficient) / chunks_a_second
    assert rise_to_1db == pytest.approx(0.0203, abs=5e-4), "back within 1 dB about 20 ms after the set"
    assert 0.030 < ramp.SETTLE < 0.045, "with the rise, a gap of about 57 ms plus the edit"


def test_a_switch_dips_from_its_first_structural_step_to_the_bypass_after_its_last_add():
    assert ramp.STRUCTURAL == {"add", "remove", "move"}
    ops = ["bypass", "remove", "move", "add", "bypass", "add", "bypass", "parameter", "amp", "bypass"]
    assert ramp.switch_span(ops) == (1, 6), "the last add's bypass keeps it unheard at its defaults"
    assert ramp.switch_span(["remove", "move", "parameter"]) == (0, 1)
    assert ramp.switch_span(["bypass", "parameter", "amp"]) is None
    assert ramp.switch_span(["add"]) == (0, 0)


# --------------------------------------------------------------------------- #
# The budget's decision
# --------------------------------------------------------------------------- #

def report(load, block_ms=5.333):
    return {"total_load": load, "budget": {"block_ms": block_ms}}


def test_an_add_over_the_budget_is_refused_with_the_measured_load():
    load, refusal = top.add_budget(report(0.86))
    assert load == {"measured": 0.86, "budget": top.ADD_BUDGET}
    assert refusal.startswith("the rig is using 86% of its 5.333 ms block, over the 80% a live add may start from")
    assert "bypass or remove a plugin" in refusal


def test_an_add_under_or_at_the_budget_goes_ahead():
    assert top.add_budget(report(0.26)) == ({"measured": 0.26, "budget": 0.8}, None)
    assert top.add_budget(report(0.80))[1] is None, "over the budget, not at it"


def test_a_load_just_over_the_budget_says_a_figure_that_is_over_it():
    assert "using 80.4%" in top.add_budget(report(0.804))[1]
    assert "of its block" in top.add_budget(report(0.9, block_ms=None))[1]


def test_a_load_that_cannot_be_read_is_said_and_refuses_nothing():
    load, refusal = top.add_budget(None, "pw-top is not installed")
    assert refusal is None and load["measured"] is None
    assert load["why"] == ("the rig's load could not be read (pw-top is not installed), "
                           "so it was not checked before this add")
    load, refusal = top.add_budget(report(None))
    assert refusal is None and "measured none of the rig's nodes" in load["why"]


# --------------------------------------------------------------------------- #
# The dip, through the routes, against the fake Sushi
# --------------------------------------------------------------------------- #

def dip_agent(fake=None, **kwargs):
    """An edit agent whose wait is recorded among Sushi's calls, so the order
    shows where the wait falls."""
    agent, made = edit_agent(**kwargs)
    fake = fake or made
    agent.live = fake

    async def sleep(seconds):
        fake.calls.append(("sleep", seconds))
        await asyncio.sleep(0)

    agent.sleep = sleep
    return agent, fake


def master_gain(fake):
    return fake.values_of["master"]["parameters"]["gain"]


SLEEP = ("sleep", ramp.SETTLE)


def test_an_add_goes_in_inside_the_dip_and_is_confirmed_before_the_level_comes_back():
    agent, fake = dip_agent()
    status, body = call(agent, "POST", "/live/add", json={"track": "guitar", "uri": DELAY, "name": "ts"})
    assert status == 200, body
    assert fake.calls == [
        ("walk", True),
        *DIP_DOWN, SLEEP,
        ("add", "ts", DELAY, 1, 4, False),
        ("walk", False),
        *DIP_BACK,
        ("values", "ts"),
    ], "read, gain down, wait, add, read back to confirm, gain back; then the defaults"
    assert master_gain(fake) == MASTER_LEVEL, "exactly the level it was"
    assert body["dip"]["made"] is True and body["dip"]["ms"] >= 0
    assert [e["op"] for e in log(agent)] == ["add"], "the dip is not logged"


def test_a_remove_goes_inside_the_dip_with_its_values_read_before_it():
    agent, fake = dip_agent()
    status, body = call(agent, "POST", "/live/remove", json={"processor": "drive"})
    assert status == 200, body
    assert fake.calls == [
        ("removal", "drive"), ("values", "drive"),
        *DIP_DOWN, SLEEP,
        ("remove", "drive", 3, 1),
        ("walk", False),
        *DIP_BACK,
    ]
    assert master_gain(fake) == MASTER_LEVEL and body["dip"]["made"] is True
    assert [e["op"] for e in log(agent)] == ["remove"]


def test_a_move_is_sent_inside_the_dip():
    agent, fake = dip_agent()
    status, body = call(agent, "POST", "/live/move", json={"processor": "drive", "direction": "up"})
    assert status == 200 and body["changed"] is True, body
    assert fake.calls == [("plan_move", "drive", -1), *DIP_DOWN, SLEEP, ("move", "drive", -1), *DIP_BACK]
    assert fake.chains["guitar"][:2] == ["drive", "comp"] and master_gain(fake) == MASTER_LEVEL


def test_a_move_that_sends_nothing_does_not_dip():
    agent, fake = dip_agent()
    status, body = call(agent, "POST", "/live/move", json={"processor": "comp", "direction": "up"})
    assert status == 200 and body["changed"] is False
    assert fake.calls == [("plan_move", "comp", -1)] and "dip" not in body


def test_a_parameter_change_does_not_dip():
    """Nor a bypass, which has no structural step either (`_apply_bypass` is
    not dipped); the master's own volume is a parameter like any other."""
    agent, fake = dip_agent()
    status, _ = call(agent, "POST", "/live/parameter", json={"processor": "drive", "parameter": "Drive", "value": 0.3})
    assert status == 200
    assert not any(c[1:2] == ("master",) for c in fake.calls)
    status, _ = call(agent, "POST", "/live/parameter", json={"processor": "master", "parameter": "gain", "value": 0.7})
    assert status == 200 and master_gain(fake) == 0.7
    assert fake.writes() == [("set_parameter", "drive", "Drive", 0.3), ("set_parameter", "master", "gain", 0.7)]


def test_an_add_sushi_refuses_part_way_still_brings_the_level_back():
    from sushi_rig.live import LiveError

    agent, fake = dip_agent()
    fake.fail, fake.fail_on = LiveError("Sushi would not add it"), "add"
    status, body = call(agent, "POST", "/live/add", json={"track": "guitar", "uri": DELAY, "name": "ts"})
    assert status == 502 and "Sushi would not add it" in body["reason"]
    assert fake.calls[-2:] == DIP_BACK, "the level put back after the failure"
    assert master_gain(fake) == MASTER_LEVEL and log(agent) == []


def test_a_remove_whose_read_back_is_wrong_still_brings_the_level_back():
    agent, fake = dip_agent()
    fake.keep = True  # Sushi confirms the remove and the processor stays
    status, body = call(agent, "POST", "/live/remove", json={"processor": "drive"})
    assert status == 502 and "but 'guitar' read back" in body["reason"]
    assert fake.calls[-2:] == DIP_BACK and master_gain(fake) == MASTER_LEVEL
    assert log(agent) == []


def test_a_move_sushi_refuses_still_brings_the_level_back():
    from sushi_rig.live import LiveError

    agent, fake = dip_agent()
    fake.fail, fake.fail_on = LiveError("no"), "move"
    status, _ = call(agent, "POST", "/live/move", json={"processor": "drive", "direction": "up"})
    assert status == 502
    assert fake.calls[-2:] == DIP_BACK and master_gain(fake) == MASTER_LEVEL


class _Origin:
    """What `Agent._dip` reads off a request: a method and a path."""
    method, path = "TEST", "/dip"


def test_an_edit_that_raises_inside_the_dip_still_brings_the_level_back():
    """Not an error Sushi answered with (those are HTTP errors by now): the
    body of the dip raising anything at all."""
    agent, fake = dip_agent()

    async def go():
        controller = fake.connect()
        with pytest.raises(ValueError, match="mid-edit"):
            async with agent._dip(_Origin(), controller, fake.chains):
                assert master_gain(fake) == 0.0, "down while the edit runs"
                raise ValueError("mid-edit")
        fake.disconnect(controller)

    asyncio.run(go())
    assert fake.calls[-2:] == DIP_BACK and master_gain(fake) == MASTER_LEVEL
    assert agent._dipping is None


def test_a_gain_that_cannot_be_turned_down_stops_the_edit_and_is_put_back():
    from sushi_rig.live import LiveError

    agent, fake = dip_agent()
    fake.fail, fake.fail_on = LiveError("Sushi would not set it"), "set_parameter"
    fake.only = lambda name, args: args[:1] == ("master",) and args[-1] == 0.0
    status, body = call(agent, "POST", "/live/add", json={"track": "guitar", "uri": DELAY, "name": "ts"})
    assert status == 502 and "Sushi would not set it" in body["reason"]
    assert not any(c[0] == "add" for c in fake.calls), "no edit with the master still up"
    assert fake.calls[-2:] == DIP_BACK, "put back, since a set that failed may have been applied"
    assert log(agent) == []


def test_a_level_that_cannot_be_put_back_is_a_warning_and_the_add_still_stands():
    from sushi_rig.live import LiveError

    agent, fake = dip_agent()
    fake.fail, fake.fail_on = LiveError("Sushi would not set it"), "set_parameter"
    fake.only = lambda name, args: args[:1] == ("master",) and args[-1] != 0.0
    status, body = call(agent, "POST", "/live/add", json={"track": "guitar", "uri": DELAY, "name": "ts"})
    assert status == 200, "the add is on the rig and confirmed"
    assert [e["op"] for e in log(agent)] == ["add"], "so it is logged: undo and a save know it"
    assert body["dip"]["restored"] is False
    assert "could not be put back" in body["warning"] and "set the master volume again" in body["warning"]
    error = call(agent, "GET", "/health")[1]["agent"]["last_error"]
    assert "could not be put back" in error["message"] + " ".join(error.get("also", []))


def test_an_edit_and_a_level_that_both_fail_are_said_in_one_answer():
    from sushi_rig.live import LiveError

    agent, fake = dip_agent()
    fake.fail, fake.fail_on = LiveError("Sushi said no"), {"add", "set_parameter"}
    fake.only = lambda name, args: name == "add" or (args[:1] == ("master",) and args[-1] != 0.0)
    status, body = call(agent, "POST", "/live/add", json={"track": "guitar", "uri": DELAY, "name": "ts"})
    assert status == 502
    assert body["reason"].startswith("Sushi said no; and the master volume, turned down for the edit, "
                                     "could not be put back")


class _Snapshots(EditingLive):
    """EditingLive with GET /live's read."""

    def snapshot(self, connection):
        self._enter("snapshot", connection)
        return {"processors": copy.deepcopy(self.values_of), "tracks": copy.deepcopy(self.chains),
                "parameter_info": {}}


def test_a_master_volume_set_beside_the_agent_during_the_dip_is_kept():
    """A MIDI mapping Sushi serves itself, or the v1 panel's OSC: it reaches
    Sushi without the agent, mid-dip. Read again after the edit, it is
    neither the floor nor the level from before, so it is left."""

    class Moved(EditingLive):
        def add(self, connection, addition):
            super().add(connection, addition)
            self.values_of["master"]["parameters"]["gain"] = 0.5  # the pedal, mid-dip

    agent, fake = dip_agent(Moved())
    status, body = call(agent, "POST", "/live/add", json={"track": "guitar", "uri": DELAY, "name": "ts"})
    assert status == 200, body
    assert master_gain(fake) == 0.5, "the newest level, not the one from before the dip"
    assert fake.calls[-2:] == [("parameter", "master", "gain"), ("values", "ts")], "read, and not set"
    assert body["dip"]["kept"] == 0.5


def test_a_master_volume_set_from_the_page_during_the_dip_lands_after_it():
    """The page's master fader is POST /live/parameter, which waits for the
    live lock the edit holds: it is applied once the level is back, so it is
    the newest, and its `previous` is the level the dip put back."""
    agent, fake = dip_agent(_Snapshots())
    fake.hang_on = "add"

    async def go():
        async with TestClient(TestServer(agent.app())) as client:
            add = asyncio.ensure_future(client.post(
                "/live/add", json={"track": "guitar", "uri": DELAY, "name": "ts"}))
            while not fake.hung.is_set():
                await asyncio.sleep(0.01)
            assert master_gain(fake) == 0.0, "mid-dip"
            seen = await (await client.get("/live")).json()
            fader = asyncio.ensure_future(client.post(
                "/live/parameter", json={"processor": "master", "parameter": "gain", "value": 0.5}))
            await asyncio.sleep(0.2)
            assert not fader.done(), "waiting for the edit"
            fake.release.set()
            added, moved = await add, await fader
            return added.status, moved.status, await moved.json(), seen

    added, moved, body, seen = asyncio.run(go())
    assert (added, moved) == (200, 200)
    assert master_gain(fake) == 0.5, "the fader's value, set after the dip put the level back"
    assert body["previous"] == MASTER_LEVEL, "what it replaced is the player's level, never the dip"
    assert [e["op"] for e in log(agent)] == ["add", "parameter"]
    master_at = [i for i, c in enumerate(fake.calls) if c == ("set_parameter", "master", "gain", 0.5)]
    back_at = fake.calls.index(DIP_BACK[1])
    assert master_at and master_at[0] > back_at
    assert seen["processors"]["master"]["parameters"]["gain"] == MASTER_LEVEL, \
        "GET /live mid-dip shows the level the dip puts back, not the dip"


def test_get_live_reads_the_master_as_it_is_when_no_dip_is_under_way():
    agent, fake = dip_agent(_Snapshots())
    fake.values_of["master"]["parameters"]["gain"] = 0.25
    status, body = call(agent, "GET", "/live")
    assert status == 200 and body["processors"]["master"]["parameters"]["gain"] == 0.25


def test_no_dip_on_a_rig_with_no_master_or_one_already_silent():
    agent, fake = dip_agent()
    del fake.chains["master"]
    status, body = call(agent, "POST", "/live/add", json={"track": "guitar", "uri": DELAY, "name": "ts"})
    assert status == 200 and body["dip"] == {"made": False, "why": "the rig has no master track"}
    assert not any(c[1:2] == ("master",) for c in fake.calls)

    agent, fake = dip_agent()
    fake.values_of["master"]["parameters"]["gain"] = 0.0
    status, body = call(agent, "POST", "/live/add", json={"track": "guitar", "uri": DELAY, "name": "ts"})
    assert status == 200 and body["dip"]["why"] == "the master is already at its floor"
    assert [c for c in fake.calls if c[1:2] == ("master",)] == [DIP_DOWN[0]], "read, and nothing set"


def test_the_dip_is_never_offered_by_undo_and_an_undo_of_an_edit_dips_too():
    agent, fake = dip_agent()
    call(agent, "POST", "/live/add", json={"track": "guitar", "uri": DELAY, "name": "ts"})
    fake.calls.clear()
    status, body = undo(agent)
    assert status == 200 and body["undone"]["op"] == "add", "the add, not a master volume"
    assert DIP_DOWN[1] in fake.calls and fake.calls[-2:] == DIP_BACK
    assert body["dip"]["made"] is True
    assert [e["op"] for e in log(agent)] == ["add", "undo"]
    assert undo(agent)[1]["code"] == "nothing_to_undo", "nothing of the dip's is left to undo"
    assert master_gain(fake) == MASTER_LEVEL


# --------------------------------------------------------------------------- #
# The dip around a live switch's structural steps
# --------------------------------------------------------------------------- #

def test_a_live_switch_dips_once_around_its_structural_steps():
    """electric-chorus to electric-delay (frozen rigs): removes, moves and
    adds, then the parameters. One dip from before the first structural step
    to after the last, and the parameter steps after the level is back."""
    from test_agent import switch_agent

    agent, fake, world, runner = switch_agent("electric-chorus")
    level = fake.values["master"]["gain"]
    status, body = call(agent, "POST", "/rig/switch", json={"config_name": "electric-delay"})
    assert status == 200 and body["how"] == "live", body
    calls = fake.calls
    down = [i for i, c in enumerate(calls) if c == ("set_parameter", "master", "gain", 0.0)]
    back = [i for i, c in enumerate(calls) if c == ("set_parameter", "master", "gain", level)]
    structural = [i for i, c in enumerate(calls) if c[0] in ("remove", "move", "add")]
    assert len(down) == 1 and len(back) == 1, "one dip, not one a step"
    assert down[0] < structural[0] and structural[-1] < back[0]
    later = [c for c in calls[back[0] + 1:] if c[0] == "set_parameter"]
    assert later, "the parameter steps come after the level is back"
    assert fake.values["master"]["gain"] == level and body["dip"]["made"] is True
    assert runner.calls == []


def test_a_live_switch_that_fails_part_way_brings_the_level_back_before_it_restarts():
    from test_agent import switch_agent

    agent, fake, world, runner = switch_agent("electric-chorus")
    level = fake.values["master"]["gain"]
    fake.fail, fake.fail_on = RuntimeError("Sushi said no"), "add"
    status, body = call(agent, "POST", "/rig/switch", json={"config_name": "electric-delay"})
    assert status == 200 and body["how"] == "restart", body
    assert fake.calls[-2:] == [("parameter", "master", "gain"), ("set_parameter", "master", "gain", level)]
    assert fake.values["master"]["gain"] == level
    assert runner.calls[0] == ["down"], "restarted after the level was back"


def test_a_live_switch_with_nothing_structural_does_not_dip():
    from test_agent import switch_agent

    agent, fake, world, runner = switch_agent("electric-delay")
    status, body = call(agent, "POST", "/rig/switch", json={"config_name": "electric-octave-up"})
    assert status == 200 and body["how"] == "live", body
    assert ("set_parameter", "master", "gain", 0.0) not in fake.calls and "dip" not in body


# --------------------------------------------------------------------------- #
# The CPU check before a live add
# --------------------------------------------------------------------------- #

def loaded(output, nam_load):
    """The captured sample with NAM's B/Q set to `nam_load` (the fixture's
    row reads 0.23), so the rig's total is that plus Sushi's 0.03."""
    assert "0.23    2" in output
    return output.replace("0.23    2", f"{nam_load:.2f}    2")


def test_an_add_over_the_budget_is_refused_with_the_figure_and_sushi_untouched():
    agent, fake = dip_agent(pw_top=lambda timeout: loaded(PW_TOP, 0.83))
    status, body = call(agent, "POST", "/live/add", json={"track": "guitar", "uri": DELAY, "name": "ts"})
    assert (status, body["code"]) == (409, "over_budget")
    assert body["reason"].startswith("the rig is using 86% of its 21.333 ms block, over the 80%")
    assert body["load"] == {"measured": 0.86, "budget": 0.8}
    assert fake.calls == [] and fake.opened == [], "refused before Sushi is asked anything"
    assert log(agent) == []


def test_an_add_under_the_budget_goes_ahead_and_says_the_load():
    agent, fake = dip_agent(pw_top=lambda timeout: PW_TOP)
    status, body = call(agent, "POST", "/live/add", json={"track": "guitar", "uri": DELAY, "name": "ts"})
    assert status == 200, body
    assert body["load"] == {"measured": 0.26, "budget": 0.8}


def test_an_add_whose_load_cannot_be_read_goes_ahead_and_says_so():
    agent, fake = dip_agent()  # edit_agent's pw-top, which is not run
    status, body = call(agent, "POST", "/live/add", json={"track": "guitar", "uri": DELAY, "name": "ts"})
    assert status == 200, body
    assert body["load"]["measured"] is None
    assert "the rig's load could not be read (pw-top is not run in this test)" in body["load"]["why"]


def test_the_add_reads_the_sample_the_load_tiles_share():
    runs = []

    def pw_top(timeout):
        runs.append(timeout)
        return PW_TOP

    agent, fake = dip_agent(pw_top=pw_top)

    async def go():
        async with TestClient(TestServer(agent.app())) as client:
            assert (await client.get("/rig/load")).status == 200
            added = await client.post("/live/add", json={"track": "guitar", "uri": DELAY, "name": "ts"})
            return added.status

    assert asyncio.run(go()) == 200
    assert len(runs) == 1, "one pw-top run for the page's tile and the add"


def test_only_an_add_is_checked():
    agent, fake = dip_agent(pw_top=lambda timeout: loaded(PW_TOP, 0.95))
    assert call(agent, "POST", "/live/move", json={"processor": "drive", "direction": "up"})[0] == 200
    assert call(agent, "POST", "/live/remove", json={"processor": "drive"})[0] == 200
    assert call(agent, "POST", "/live/parameter", json={"processor": "comp", "parameter": "RATIO", "value": 0.4})[0] == 200
