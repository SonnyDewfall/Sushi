"""A channel from another rig on the page (#208): a strip's Load… panel,
which offers every channel of every rig of config/rigs as `rig / channel`
and loads the one chosen onto the strip's channel; and the same channels as
"Start from" on Add channel.

Two layers, as test_ui.py has them: rig.js's decisions imported into a bare
page, then the rack driven in Chromium against a real agent over the fake of
`ui_fake.py` with a checkout in a temporary folder (`Served(save=True)`),
into which each test writes a second rig, `spare`, to load from: the agent's
real route writes the real rig file, compiled by the real compiler before
anything is written.

What stands in for what. The running rig is `ui_fake.UiRig`; a load's live
switch is `UiLifecycle.switch_held`, which says "live" and then has the fake
take the rig file's chains (`UiSave.switched`), so the page drawing the
loaded plugins is a reading of the fake, not of a live switch's steps (those
are test_agent_channel_load.py's, against the real switch). Nothing here is
the rig machine, a phone or a pedal.
"""

from __future__ import annotations

import pytest
import yaml

pytest.importorskip("aiohttp")

from test_ui import Watched, blank, browser, expect, rigjs, served  # noqa: E402,F401
from ui_fake import GXTS9, MDA_DELAY, Served  # noqa: E402

SPARE = f"""\
kind: rig
schema_version: 1
name: spare
channels:
- name: lead
  input: guitar-in
  width: mono
  level: -3.0
  chain:
  - name: drive
    uri: {GXTS9}
  - name: echo
    uri: {MDA_DELAY}
    bypassed: true
master:
  output: monitor-out
  chain: []
"""


@pytest.fixture
def files_served():
    with Served(save=True) as served:
        (served.save.root / "config" / "rigs" / "spare.yaml").write_text(SPARE)
        yield served


def rack(browser, served):
    page = Watched(browser, served, "/#rack")
    page.page.wait_for_selector(".unit")
    return page


def unlock(p):
    lock = p.locator("button.lock")
    lock.click()
    lock.click()
    expect(lock).to_have_text("Lock off · editing")


def rig(served):
    return yaml.safe_load((served.save.root / "config" / "rigs" / "electric_board.yaml").read_text())


def panel(p, channel):
    p.get_by_role("button", name=f"Load a channel onto {channel}").click()
    group = p.get_by_role("group", name=f"Load onto {channel}")
    expect(group).to_be_visible()
    return group


# --------------------------------------------------------------------------- #
# The decisions, pure (rig.js)
# --------------------------------------------------------------------------- #

def test_every_rigs_channels_are_offered_as_rig_slash_channel_but_the_one_loaded_onto(blank):
    out = rigjs(blank, """
      const rigs = {ok: true, body: {rigs: ['electric_board', 'spare', 'gone']}};
      const byRig = {electric_board: {ok: true, body: {channels: [{name: 'board', plugins: ['a', 'b']},
                                                                  {name: 'vocal', plugins: ['c']}]}},
                     spare: {ok: true, body: {channels: [{name: 'lead', plugins: ['d', 'e']}]}},
                     gone: {ok: false, reason: '404 no such rig'}};
      return {
        all: R.sourceChoices(rigs, byRig),
        onto: R.sourceChoices(rigs, byRig, {rig: 'electric_board', channel: 'board'}),
        values: [R.sourceValue({rig: 'r', channel: 'c'}), R.sourceValue(null)],
        back: [R.sourceFrom('r\\u0000c'), R.sourceFrom('')],
        calls: [R.channelLoadCall('my board', {rig: 'spare', channel: 'lead', x: 1}, {discard: true, dropMidi: true, label: 'x'}),
                R.channelLoadCall('board', {rig: 'spare', channel: 'lead'})],
        add: R.addChannelCall('electric_board', {name: 'keys', input: 'keys-in', width: 'mono', from: {rig: 'spare', channel: 'lead', y: 2}}),
      };""")
    labels = [[o["label"] for o in g["options"]] for g in out["all"]]
    assert labels == [["electric_board / board", "electric_board / vocal"], ["spare / lead"], []]
    assert [g["label"] for g in out["all"]] == ["electric_board", "spare", "gone (could not be read)"]
    assert out["all"][0]["options"][0]["title"] == "2 plugins" and out["all"][0]["options"][1]["title"] == "1 plugin"
    assert [[o["label"] for o in g["options"]] for g in out["onto"]][:2] == [["electric_board / vocal"], ["spare / lead"]], \
        "a channel is not offered to load onto itself"
    assert out["values"] == ["r\u0000c", ""]
    assert out["back"] == [{"rig": "r", "channel": "c"}, None]
    assert out["calls"][0] == {"path": "/rig/channels/my%20board/load",
                               "json": {"from": {"rig": "spare", "channel": "lead"}, "discard": True, "drop_midi": True}}
    assert out["calls"][1] == {"path": "/rig/channels/board/load", "json": {"from": {"rig": "spare", "channel": "lead"}}}
    assert out["add"]["json"]["from"] == {"rig": "spare", "channel": "lead"}


def test_what_a_load_says_and_the_second_tap_each_refusal_offers(blank):
    out = rigjs(blank, """
      const ok = (body) => ({ok: true, status: 200, body: {ok: true, ...body}});
      const no = (code, reason, body = {}) => ({ok: false, status: 409, code, reason, body: {ok: false, code, reason, ...body}});
      const from = {rig: 'spare', channel: 'lead'};
      const mappings = [{control: 'pad1', binding: 'bypass', target: 'board/overdrive', why: "the loaded chain has no overdrive"}];
      return {
        loaded: R.loadOutcome('vocal', from, ok({file: 'config/rigs/r.yaml', how: 'live', why: [],
          amp: {note: 'vocal has no amp: spare / lead plays through one (N), and this rig has no amp'}, width: null,
          followed: [{control: 'pad2', from: 'board/drive', to: 'board/board_drive'}], dropped: [],
          kept_before: {id: 'x', how: 'before'}})),
        restarted: R.loadOutcome('vocal', from, ok({file: 'f', how: 'restart',
          why: [{code: 'midi', detail: 'the cc_mappings differ'}], followed: [], dropped: [], amp: {note: null}})).text,
        unsaved: R.loadOutcome('vocal', from, no('unsaved', 'r is running with 1 change')),
        dropped: R.loadOutcome('vocal', from, ok({file: 'f', how: 'live', why: [], followed: [], dropped: [],
          discard: {note: '2 unsaved changes dropped: the live switch put back each value they set'}})).notes,
        unswitched: R.loadOutcome('vocal', from, no('not_switched', 'the file was written',
          {midi_note: 'the pedals follow the rig as it runs'})).notes,
        midi: R.loadOutcome('vocal', from, no('midi', 'would orphan', {mappings}), {discard: true, label: 'old'}),
        locked: R.lockRefusal(true, 'load_channel'),
        unlocked: R.lockRefusal(false, 'load_channel'),
        made: R.rigFileOutcome('new', 'two', {ok: true, status: 200, body: {ok: true, library: null, rig: 'two',
          file: 'config/rigs/two.yaml', offer: 'start', channels: [{name: 'a', from: {rig: 'spare', channel: 'lead'}}]}}).text,
      };""")
    assert out["loaded"]["title"] == "Loaded spare / lead onto vocal"
    assert out["loaded"]["text"] == "Played live. Wrote config/rigs/r.yaml; not committed. Revert… goes back."
    assert out["loaded"]["notes"] == [
        "vocal has no amp: spare / lead plays through one (N), and this rig has no amp.",
        "MIDI pad2 follows its plugin: board/drive is now board/board_drive."]
    assert out["restarted"] == "Played by a restart (the cc_mappings differ). Wrote f; not committed."
    assert out["unsaved"]["title"] == "Not loaded: spare / lead onto vocal"
    assert out["unsaved"]["offer"] == {"label": "Load anyway, dropping the unsaved changes", "discard": True}
    assert out["dropped"] == ["2 unsaved changes dropped: the live switch put back each value they set."]
    assert out["unswitched"] == ["the pedals follow the rig as it runs."]
    assert out["midi"]["notes"] == ["pad1: bypass board/overdrive. the loaded chain has no overdrive."]
    assert out["midi"]["offer"] == {"label": "Load, dropping 1 MIDI mapping", "discard": True, "dropMidi": True}, \
        "the first yes (discard) is kept, and the label is this refusal's"
    assert out["locked"].startswith("Locked: loading a channel changes the rig's structure")
    assert out["unlocked"] is None
    assert out["made"] == "Wrote config/rigs/two.yaml, a new rig with 1 channel. Chains copied: a from spare / lead."


# --------------------------------------------------------------------------- #
# The rack, against the agent
# --------------------------------------------------------------------------- #

def test_the_picker_lists_every_rigs_channels_as_rig_slash_channel(browser, files_served):
    page = rack(browser, files_served)
    p = page.page
    group = panel(p, "board")
    picker = group.get_by_label("Channel to load onto board")
    expect(picker.locator("option")).to_have_text(["Choose rig / channel…", "electric_board / vocal", "spare / lead"])
    expect(picker.locator("optgroup")).to_have_count(2)
    assert picker.locator("optgroup").evaluate_all("(gs) => gs.map((g) => g.label)") == ["electric_board", "spare"]
    expect(group.get_by_role("button", name="Load", exact=True)).to_be_disabled()
    assert page.errors == []
    page.close()


def test_a_channel_of_another_rig_is_loaded_and_the_page_shows_its_plugins(browser, files_served):
    page = rack(browser, files_served)
    p = page.page
    group = panel(p, "vocal")
    group.get_by_label("Channel to load onto vocal").select_option(label="spare / lead")
    go = group.get_by_role("button", name="Load", exact=True)
    expect(go).to_be_disabled()  # the page starts locked: a load changes the rig's structure
    expect(group).to_contain_text("Locked: loading a channel changes the rig's structure")
    unlock(p)
    expect(go).to_be_enabled()
    expect(group).to_contain_text("Replaces vocal's chain and level.")
    go.click()
    result = group.locator(".load-result")
    expect(result).to_contain_text("Loaded spare / lead onto vocal")
    expect(result).to_contain_text("Played live.")
    vocal = rig(files_served)["channels"][1]
    assert [e["name"] for e in vocal["chain"]] == ["vocal_drive", "vocal_echo"]
    assert vocal["chain"][1]["bypassed"] is True and vocal["level"] == -3.0
    assert (vocal["input"], vocal["width"]) == ("vocal-in", "mono")
    text = (files_served.save.root / "config" / "rigs" / "electric_board.yaml").read_text()
    assert "# Chain copied from spare / lead (config/rigs/spare.yaml)" in text, "the provenance line"
    assert "# The fake rig of ui_fake.UiRig, as a rig file." in text, "the file's own comments stay"
    p.get_by_role("tab", name="vocal").click()
    expect(p.locator(".unit[data-unit='vocal_drive']")).to_be_visible()
    assert files_served.rig.chains["board"][:3] == ["overdrive", "octave", "delay"], "the board is as it was"
    assert page.errors == []
    page.close()


def test_a_load_with_unsaved_work_offers_to_drop_it(browser, files_served):
    page = rack(browser, files_served)
    p = page.page
    unlock(p)
    status = p.evaluate("""async () => (await fetch('/live/parameter', {method: 'POST', headers: {'content-type':
      'application/json'}, body: JSON.stringify({processor: 'vocal_send', parameter: 'gain', value: 0.7})})).status""")
    assert status == 200
    group = panel(p, "vocal")
    group.get_by_label("Channel to load onto vocal").select_option(label="spare / lead")
    group.get_by_role("button", name="Load", exact=True).click()
    result = group.locator(".load-result")
    expect(result).to_contain_text("Not loaded: spare / lead onto vocal")
    expect(result).to_contain_text("not saved")
    group.get_by_role("button", name="Load anyway, dropping the unsaved changes").click()
    expect(result).to_contain_text("Loaded spare / lead onto vocal")
    page.close()


def test_a_channel_is_added_from_another_rigs_channel(browser, files_served):
    page = rack(browser, files_served)
    p = page.page
    p.locator(".strips-foot").get_by_role("button", name="Add channel").click()
    p.get_by_label("New channel name").fill("keys")
    p.get_by_label("New channel input").select_option("keys-in")
    p.get_by_label("New channel starts from").select_option(label="spare / lead")
    p.get_by_role("button", name="Add keys").click()
    expect(p.locator(".rigfile-result.ok")).to_contain_text("Added keys to electric_board")
    keys = rig(files_served)["channels"][2]
    assert keys["name"] == "keys" and keys["input"] == "keys-in"
    assert [e["name"] for e in keys["chain"]] == ["drive", "echo"], "a start copies the chain as written (#125)"
    page.close()
