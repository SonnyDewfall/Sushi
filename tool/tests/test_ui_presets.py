"""Channel presets on the page (#127): a strip's Preset… panel (Save channel
as…, and Load onto it), and a preset as "Start from" on Add channel and the
New rig screen.

Two layers, as test_ui.py has them: rig.js's decisions imported into a bare
page, then the rack driven in Chromium against a real agent over the fake of
`ui_fake.py` with a checkout in a temporary folder (`Served(save=True)`):
the agent's real routes write real presets to config/channels/ there and the
real rig file, compiled by the real compiler before anything is written.

What stands in for what. The running rig is `ui_fake.UiRig`; a load's live
switch is `UiLifecycle.switch_held`, which says "live" and then has the fake
take the rig file's chains (`UiSave.switched`), so the page drawing the
loaded plugins is a reading of the fake, not of a live switch's steps (those
are test_agent_presets.py's, against the real switch). Nothing here is the
rig machine, a phone or a pedal.
"""

from __future__ import annotations

import pytest
import yaml

pytest.importorskip("aiohttp")

from test_ui import Watched, blank, browser, expect, rigjs, served  # noqa: E402,F401
from ui_fake import Served  # noqa: E402


@pytest.fixture
def files_served():
    with Served(save=True) as served:
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


def preset(served, name):
    path = served.save.root / "config" / "channels" / f"{name}.yaml"
    return yaml.safe_load(path.read_text()) if path.exists() else None


def rig(served):
    return yaml.safe_load((served.save.root / "config" / "rigs" / "electric_board.yaml").read_text())


def panel(p, channel):
    p.get_by_role("button", name=f"Presets for {channel}").click()
    group = p.get_by_role("group", name=f"Presets for {channel}")
    expect(group).to_be_visible()
    return group


def save_as(p, channel, name):
    group = panel(p, channel)
    group.get_by_label(f"Save {channel} as").fill(name)
    group.get_by_role("button", name=f"Save {channel} as {name}").click()
    return group


# --------------------------------------------------------------------------- #
# The decisions, pure (rig.js)
# --------------------------------------------------------------------------- #

PRESETS = {"ok": True, "status": 200, "body": {"ok": True, "presets": [
    {"name": "crunch", "ok": True, "width": "mono", "plugins": ["drive", "room"], "heard_through_amp": "amp/models/N.nam"},
    {"name": "wide", "ok": True, "width": "stereo", "plugins": ["chorus"]},
    {"name": "broken", "ok": False, "problem": "width must be one of mono, stereo"},
]}}


def test_presets_are_offered_first_as_a_start_and_one_that_cannot_load_is_off(blank):
    out = rigjs(blank, """
      const rigs = {ok: true, body: {rigs: ['electric_board']}};
      const byRig = {electric_board: {ok: true, body: {channels: [{name: 'board', plugins: ['a', 'b']}]}}};
      const groups = R.sourceChoices(rigs, byRig, arg);
      return {
        groups, without: R.sourceChoices(rigs, byRig, null).length,
        values: [R.sourceValue({preset: 'crunch'}), R.sourceValue({rig: 'r', channel: 'c'}), R.sourceValue(null)],
        back: [R.sourceFrom(R.sourceValue({preset: 'crunch'})), R.sourceFrom('r\\u0000c'), R.sourceFrom('')],
        widths: [R.presetWidth(arg, {preset: 'wide'}, 'mono'), R.presetWidth(arg, {rig: 'r', channel: 'c'}, 'mono'),
                 R.presetWidth(arg, {preset: 'gone'}, 'mono')],
        calls: [R.newRigCall('two', [{name: 'a', input: 'guitar-in', width: 'mono', from: {preset: 'crunch', x: 1}}]).json,
                R.presetSaveCall('board', 'crunch'), R.presetSaveCall('my board', 'crunch', true),
                R.presetLoadCall('board', 'crunch', {discard: true, dropMidi: true, label: 'x'}),
                R.presetLoadCall('board', 'crunch')],
        names: [R.checkPresetName('', arg).problem !== null, R.checkPresetName('Crunch', arg).problem !== null,
                R.checkPresetName('crunch', arg), R.checkPresetName('new-one', arg)],
      };""", PRESETS)
    first, second = out["groups"]
    assert first["label"] == "Channel presets" and second["label"] == "electric_board"
    assert [o["label"] for o in first["options"]] == [
        "preset crunch (2 plugins, mono)", "preset wide (1 plugin, stereo)", "preset broken (cannot be loaded)"]
    assert [o["disabled"] for o in first["options"]] == [False, False, True]
    assert first["options"][2]["title"] == "width must be one of mono, stereo"
    assert out["without"] == 1, "no presets, no group"
    assert out["values"] == ["\u0001crunch", "r\u0000c", ""]
    assert out["back"] == [{"preset": "crunch"}, {"rig": "r", "channel": "c"}, None]
    assert out["widths"] == ["stereo", "mono", "mono"], "a preset's preferred width; anything else is left"
    assert out["calls"][0]["channels"][0]["from"] == {"preset": "crunch"}
    assert out["calls"][1] == {"path": "/rig/channels/board/save-as", "json": {"name": "crunch"}}
    assert out["calls"][2] == {"path": "/rig/channels/my%20board/save-as", "json": {"name": "crunch", "replace": True}}
    assert out["calls"][3] == {"path": "/rig/channels/board/load",
                               "json": {"preset": "crunch", "discard": True, "drop_midi": True}}
    assert out["calls"][4] == {"path": "/rig/channels/board/load", "json": {"preset": "crunch"}}
    assert out["names"][:2] == [True, True]
    assert out["names"][2] == {"problem": None, "taken": True} and out["names"][3] == {"problem": None, "taken": False}


def test_what_a_save_and_a_load_say_and_the_second_tap_each_refusal_offers(blank):
    out = rigjs(blank, """
      const ok = (body) => ({ok: true, status: 200, body: {ok: true, ...body}});
      const no = (code, reason, body = {}) => ({ok: false, status: 409, code, reason, body: {ok: false, code, reason, ...body}});
      const mappings = [{control: 'pad1', binding: 'bypass', target: 'board/overdrive', why: "the preset's chain has no overdrive"}];
      return {
        saved: R.presetOutcome('save', 'board', 'crunch', ok({file: 'config/channels/crunch.yaml', how: 'new',
          preset: {plugins: ['a', 'b'], heard_through_amp: 'amp/models/N.nam'}})),
        taken: R.presetOutcome('save', 'board', 'crunch', no('taken', 'a channel preset called crunch already exists')),
        loaded: R.presetOutcome('load', 'vocal', 'crunch', ok({file: 'config/rigs/r.yaml', how: 'live', why: [],
          amp: {note: 'vocal has no amp: crunch was saved with the amp (N) in front of it'}, width: null,
          followed: [{control: 'pad2', from: 'board/drive', to: 'board/board_drive'}], dropped: [],
          kept_before: {id: 'x', how: 'before'}})),
        restarted: R.presetOutcome('load', 'vocal', 'crunch', ok({file: 'f', how: 'restart',
          why: [{code: 'midi', detail: 'the cc_mappings differ'}], followed: [], dropped: [], amp: {note: null}})).text,
        unsaved: R.presetOutcome('load', 'vocal', 'crunch', no('unsaved', 'r is running with 1 change')),
        midi: R.presetOutcome('load', 'vocal', 'crunch', no('midi', 'would orphan', {mappings}), {discard: true, label: 'old'}),
        locked: R.lockRefusal(true, 'load_preset'),
        unlocked: R.lockRefusal(false, 'load_preset'),
      };""")
    assert out["saved"]["title"] == "Saved board as crunch"
    assert out["saved"]["text"] == ("Wrote config/channels/crunch.yaml: board as it plays now, 2 plugins. The rig is "
                                    "unchanged, and nothing is committed.")
    assert "does not carry the amp" in out["saved"]["notes"][0]
    assert out["taken"]["offer"] == {"label": "Replace crunch", "replace": True}
    assert out["loaded"]["text"].startswith("Played live, without a restart. Wrote config/rigs/r.yaml in place")
    assert out["loaded"]["notes"] == [
        "vocal has no amp: crunch was saved with the amp (N) in front of it.",
        "MIDI pad2 follows its plugin: board/drive is now board/board_drive.",
        "The rig's file as it was before is kept: Revert… goes back to it."]
    assert out["restarted"].startswith("Played by a restart (the cc_mappings differ).")
    assert out["unsaved"]["offer"] == {"label": "Load anyway, dropping the unsaved changes", "discard": True}
    assert out["midi"]["notes"] == ["pad1: bypass board/overdrive. the preset's chain has no overdrive."]
    assert out["midi"]["offer"] == {"label": "Load, dropping 1 MIDI mapping", "discard": True, "dropMidi": True}, \
        "the first yes (discard) is kept, and the label is this refusal's"
    assert out["locked"].startswith("Locked: loading a channel preset changes the rig's structure")
    assert out["unlocked"] is None


def test_a_new_channel_from_a_preset_says_it_has_no_amp(blank):
    said = rigjs(blank, """
      const ok = (body) => ({ok: true, status: 200, body: {ok: true, library: null, ...body}});
      const amp = {in_front: false, note: 'second has no amp: guitar was saved with the amp (N) in front of it'};
      return [
        R.rigFileOutcome('new', 'two', ok({rig: 'two', file: 'config/rigs/two.yaml', offer: 'start',
          channels: [{name: 'a', from: {preset: 'guitar', amp}}, {name: 'b', from: {rig: 'x', channel: 'board'}}]})).text,
        R.rigFileOutcome('add', 'second to r', ok({rig: 'r', file: 'config/rigs/r.yaml', running: false,
          change: {from: {preset: 'guitar', amp}}})).text,
      ];""")
    assert said[0] == ("Wrote config/rigs/two.yaml, a new rig with 2 channels. Chains copied: a from preset guitar, "
                       "b from x · board. second has no amp: guitar was saved with the amp (N) in front of it.")
    assert said[1].startswith("Wrote config/rigs/r.yaml in place, keeping its comments. Its chain and level are "
                              "channel preset guitar's. second has no amp")


# --------------------------------------------------------------------------- #
# The rack, against the agent
# --------------------------------------------------------------------------- #

def test_a_channel_is_saved_as_a_preset_from_its_strip_and_a_second_save_offers_to_replace(browser, files_served):
    page = rack(browser, files_served)
    p = page.page
    group = save_as(p, "board", "crunch")
    result = group.locator(".preset-result")
    expect(result).to_contain_text("Saved board as crunch")
    expect(result).to_contain_text("Wrote config/channels/crunch.yaml: board as it plays now, 3 plugins.")
    doc = preset(files_served, "crunch")
    assert [e["name"] for e in doc["chain"]] == ["overdrive", "octave", "delay"]
    assert doc["saved_from"] == {"rig": "electric_board", "channel": "board"} and "input" not in doc
    # Saving under that name again: refused, and offered as a replace.
    group.get_by_label("Save board as").fill("crunch")
    expect(group.locator('[data-check="preset-name-board"]')).to_have_text(
        "crunch is a preset already: Save offers to replace it.")
    group.get_by_role("button", name="Save board as crunch").click()
    expect(result).to_contain_text("Not saved: board as crunch")
    group.get_by_role("button", name="Replace crunch").click()
    expect(result).to_contain_text("Saved board as crunch")
    expect(result).to_contain_text("in place, keeping its comments")
    # The load picker lists it now.
    expect(group.get_by_label("Preset to load onto board").locator("option")).to_have_text(
        ["Choose a preset…", "crunch (3 plugins, mono)"])
    page.close()


def test_a_preset_is_loaded_onto_another_channel_and_the_page_shows_its_plugins(browser, files_served):
    page = rack(browser, files_served)
    p = page.page
    save_as(p, "board", "crunch")
    expect(p.locator(".preset-result")).to_contain_text("Saved board as crunch")
    p.get_by_role("button", name="Close").click()
    group = panel(p, "vocal")
    group.get_by_label("Preset to load onto vocal").select_option("crunch")
    load = group.get_by_role("button", name="Load crunch onto vocal")
    expect(load).to_be_disabled()  # the page starts locked: a load changes the rig's structure
    expect(group).to_contain_text("Locked: loading a channel preset changes the rig's structure")
    unlock(p)
    expect(load).to_be_enabled()
    load.click()
    result = group.locator(".preset-result")
    expect(result).to_contain_text("Loaded crunch onto vocal")
    expect(result).to_contain_text("Played live, without a restart.")
    chain = [e["name"] for e in rig(files_served)["channels"][1]["chain"]]
    assert chain == ["vocal_overdrive", "vocal_octave", "vocal_delay"]
    text = (files_served.save.root / "config" / "rigs" / "electric_board.yaml").read_text()
    assert "# From channel preset crunch (config/channels/crunch.yaml)" in text, "the provenance line"
    assert "# The fake rig of ui_fake.UiRig, as a rig file." in text, "the file's own comments stay"
    p.get_by_role("tab", name="vocal").click()
    expect(p.locator(".unit[data-unit='vocal_overdrive']")).to_be_visible()
    expect(p.locator(".unit[data-unit='vocal_octave_to_stereo']")).to_have_count(0)
    assert files_served.rig.chains["board"][:3] == ["overdrive", "octave", "delay"], "the board is as it was"
    page.close()


def test_a_load_with_unsaved_work_offers_to_drop_it(browser, files_served):
    page = rack(browser, files_served)
    p = page.page
    unlock(p)
    save_as(p, "vocal", "voice")
    expect(p.locator(".preset-result")).to_contain_text("Saved vocal as voice")
    status = p.evaluate("""async () => (await fetch('/live/parameter', {method: 'POST', headers: {'content-type':
      'application/json'}, body: JSON.stringify({processor: 'vocal_send', parameter: 'gain', value: 0.7})})).status""")
    assert status == 200
    group = p.get_by_role("group", name="Presets for vocal")
    group.get_by_label("Preset to load onto vocal").select_option("voice")
    group.get_by_role("button", name="Load voice onto vocal").click()
    result = group.locator(".preset-result")
    expect(result).to_contain_text("Not loaded: voice onto vocal")
    expect(result).to_contain_text("not saved")
    group.get_by_role("button", name="Load anyway, dropping the unsaved changes").click()
    expect(result).to_contain_text("Loaded voice onto vocal")
    page.close()


def test_a_channel_is_added_from_a_preset_on_a_chosen_input(browser, files_served):
    page = rack(browser, files_served)
    p = page.page
    save_as(p, "board", "crunch")
    expect(p.locator(".preset-result")).to_contain_text("Saved board as crunch")
    p.get_by_role("button", name="Close").click()
    p.locator(".strips-foot").get_by_role("button", name="Add channel").click()
    p.get_by_label("New channel name").fill("keys")
    p.get_by_label("New channel input").select_option("keys-in")
    p.get_by_label("New channel starts from").select_option(label="preset crunch (3 plugins, mono)")
    expect(p.get_by_label("New channel width")).to_have_value("mono")
    p.get_by_role("button", name="Add keys").click()
    result = p.locator(".rigfile-result.ok")
    expect(result).to_contain_text("Its chain and level are channel preset crunch's.")
    keys = rig(files_served)["channels"][2]
    assert keys["name"] == "keys" and keys["input"] == "keys-in"
    assert [e["name"] for e in keys["chain"]] == ["keys_overdrive", "keys_octave", "keys_delay"]
    page.close()


def test_a_new_rig_is_made_of_two_channels_from_one_preset(browser, files_served):
    page = rack(browser, files_served)
    p = page.page
    save_as(p, "board", "crunch")
    expect(p.locator(".preset-result")).to_contain_text("Saved board as crunch")
    p.get_by_role("link", name="New rig…").click()
    expect(p.locator("h1.rig-name")).to_have_text("New rig")
    p.get_by_label("Rig name").fill("two-guitars")
    p.get_by_label("Channel 1 starts from").select_option(label="preset crunch (3 plugins, mono)")
    p.get_by_role("button", name="Add channel").click()
    p.get_by_label("Channel 2 input").select_option("vocal-in")
    p.get_by_label("Channel 2 starts from").select_option(label="preset crunch (3 plugins, mono)")
    p.get_by_role("button", name="Create two-guitars").click()
    result = p.locator(".rigfile-result.ok")
    expect(result).to_contain_text("Chains copied: guitar from preset crunch, vocal from preset crunch.")
    made = yaml.safe_load((files_served.save.root / "config" / "rigs" / "two-guitars.yaml").read_text())
    assert [[e["name"] for e in c["chain"]] for c in made["channels"]] == [
        ["guitar_overdrive", "guitar_octave", "guitar_delay"], ["vocal_overdrive", "vocal_octave", "vocal_delay"]]
    page.close()
