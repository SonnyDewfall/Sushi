"""A channel removed from the running rig on the page (#190): a strip's
Remove, which asks "Remove?" naming what goes, then removes the channel, its
chain and its send, the MIDI controls that name it once dropped, and asks
"Restart?" before a removal that restarts Sushi.

Two layers, as test_ui_channel_load.py has them: rig.js's decisions imported
into a bare page, then the rack driven in Chromium against a real agent
over the fake of `ui_fake.py`, with a checkout in a temporary folder whose
rig file the agent's real route writes, compiled by the real compiler first.

What stands in for what. The running rig is `ui_fake.UiRig`; the switch
into the edited file is `UiLifecycle.switch_held`, which says "live" (or a
restart, when a test says so), and the fake then takes the file's tracks
(`UiSave.switched`), so the strip going is a reading of the fake. Whether
the removal is live or a restart is decided by the agent's real
`rigfile.diff`, over `UiRig.remove_track`. Nothing here is the rig machine,
a phone or a pedal.
"""

from __future__ import annotations

import re

import pytest
import yaml

pytest.importorskip("aiohttp")

from test_ui import Watched, blank, browser, expect, rigjs, served  # noqa: E402,F401
from ui_fake import RIG_FILE, Served  # noqa: E402

MAPPED = RIG_FILE + "midi:\n  controller: fbv\n  controls:\n  - {control: d, mute: vocal}\n  - {control: c, bypass: board/delay}\n"


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


def path_of(served):
    return served.save.root / "config" / "rigs" / "electric_board.yaml"


def channels(served):
    return [c["name"] for c in yaml.safe_load(path_of(served).read_text())["channels"]]


def ask(p, channel):
    p.get_by_role("button", name=f"Remove {channel}", exact=True).click()
    group = p.get_by_role("group", name=f"Remove {channel}?")
    expect(group).to_be_visible()
    return group


# --------------------------------------------------------------------------- #
# The decisions, pure (rig.js)
# --------------------------------------------------------------------------- #

def test_the_call_what_goes_and_what_names_the_channel(blank):
    out = rigjs(blank, """
      const controls = [{control: 'd', mute: 'vocal'}, {control: 'c', bypass: 'board/delay'},
                        {control: 'k', parameter: 'vocal/vocal_delay/Mix'}, {control: 'p', blend: ['board', 'vocal']},
                        {control: 'v', volume: 'vocalist'}, null, {control: 'x'}];
      return {
        calls: [R.channelRemoveCall('my vocal', {}, 'electric_board'),
                R.channelRemoveCall('vocal', {discard: true, dropMidi: true, restart: true, label: 'x'})],
        named: R.midiNaming(controls, 'vocal').map((m) => m.control),
        none: R.midiNaming(null, 'vocal'),
        goes: R.channelGoes({name: 'vocal', plugins: ['vocal_delay']}, controls),
        bare: R.channelGoes({name: 'board', plugins: ['a', 'b']}, null),
        locked: R.lockRefusal(true, 'remove_channel'),
        unlocked: R.lockRefusal(false, 'remove_channel'),
        undoLocked: R.undoButton({ok: true, body: {next: {seq: 3, kind: 'file', act: 'remove_channel',
          label: 'remove channel vocal', can: true}}}, {locked: true}),
      };""")
    assert out["calls"][0] == {"path": "/rig/channels/my%20vocal/remove",
                               "json": {"restart": False, "rig": "electric_board"}}
    assert out["calls"][1] == {"path": "/rig/channels/vocal/remove",
                               "json": {"restart": True, "discard": True, "drop_midi": True}}
    assert out["named"] == ["d", "k", "p"] and out["none"] == []
    assert out["goes"]["words"] == "vocal · 1 plugin · 3 MIDI"
    assert out["goes"]["title"] == ("vocal, its chain (vocal_delay) and its send (vocal_send), and MIDI d (mute vocal), "
                                    "k (parameter vocal/vocal_delay/Mix), p (blend board, vocal) go.")
    assert out["bare"]["words"] == "board · 2 plugins"
    assert out["locked"].startswith("Locked: removing a channel changes the rig's structure")
    assert out["unlocked"] is None
    assert out["undoLocked"] == {"disabled": True, "title": "Locked: remove channel vocal", "cant": False}


def test_what_a_removal_says_and_the_second_tap_each_refusal_offers(blank):
    out = rigjs(blank, """
      const ok = (body) => ({ok: true, status: 200, body: {ok: true, ...body}});
      const no = (code, reason, body = {}) => ({ok: false, status: 409, code, reason, body: {ok: false, code, reason, ...body}});
      const mappings = [{control: 'd', binding: 'mute', target: 'vocal', why: "it is the channel's mute"},
                        {control: 'p', binding: 'blend', target: ['board', 'vocal'], why: 'a blend needs two channels'}];
      return {
        live: R.removeOutcome('vocal', ok({file: 'config/rigs/r.yaml', how: 'live', why: [], dropped: [mappings[0]],
          kept_before: {id: 'x'}})),
        restarted: R.removeOutcome('board', ok({file: 'f', how: 'restart',
          why: [{code: 'track', detail: "track 'vocal': inputs differ"}], dropped: []})).text,
        unsaved: R.removeOutcome('vocal', no('unsaved', 'r is running with 1 change')),
        midi: R.removeOutcome('vocal', no('midi', 'names vocal', {mappings}), {discard: true}),
        restarts: R.removeOutcome('vocal', no('restarts', 'nothing was written: removing vocal restarts Sushi: x',
          {why: []}), {dropMidi: true}),
        last: R.removeOutcome('vocal', no('last_channel', "vocal is r's only channel")),
      };""")
    assert out["live"]["title"] == "Removed vocal"
    assert out["live"]["text"] == "Live. Wrote config/rigs/r.yaml; not committed. Revert… goes back."
    assert out["live"]["notes"] == ["Dropped MIDI d (mute vocal)."]
    assert out["restarted"] == "Restarted (track 'vocal': inputs differ). Wrote f; not committed."
    assert out["unsaved"]["title"] == "Not removed"
    assert out["unsaved"]["offer"] == {"label": "Remove anyway", "discard": True}
    assert out["midi"]["offer"] == {"label": "Drop 2 and remove", "discard": True, "dropMidi": True}
    assert out["midi"]["midi"] == "d, p", "the controls are named on the page"
    assert out["midi"]["notes"] == ["d: mute vocal. it is the channel's mute.", "p: blend board, vocal. a blend needs two channels."]
    assert out["restarts"]["ask"] == "restart"
    assert out["restarts"]["offer"] == {"dropMidi": True, "restart": True}, "what was said yes to is kept"
    assert out["last"]["offer"] is None and out["last"]["ask"] is None


# --------------------------------------------------------------------------- #
# The rack, against the agent
# --------------------------------------------------------------------------- #

def test_a_channel_is_removed_after_remove_and_its_strip_goes(browser, files_served):
    page = rack(browser, files_served)
    p = page.page
    sent = []
    p.route("**/rig/channels/*/remove", lambda route: sent.append(route.request.post_data_json) or route.continue_())
    group = ask(p, "vocal")
    expect(group.locator(".confirm .q")).to_have_text("Remove?")
    expect(group.locator(".remove-goes")).to_have_text("vocal · 1 plugin")
    expect(group.locator(".remove-goes")).to_have_attribute(
        "title", "vocal, its chain (vocal_delay) and its send (vocal_send) go.")
    yes = group.get_by_role("button", name="Yes")
    expect(yes).to_be_disabled()  # the page starts locked: a removal changes the rig's structure
    expect(group.locator(".lock-says")).to_have_text("Locked")
    expect(group.locator(".lock-says")).to_have_attribute("title", re.compile("^Locked: removing a channel"))
    unlock(p)
    expect(yes).to_be_enabled()
    yes.click()
    done = p.locator(".remove-done")
    expect(done).to_contain_text("Removed vocal")
    expect(done).to_have_attribute("title", re.compile("^Live\\. Wrote config/rigs/electric_board\\.yaml"))
    assert sent == [{"restart": False, "rig": "electric_board"}]
    assert channels(files_served) == ["board"]
    text = path_of(files_served).read_text()
    assert "# The fake rig of ui_fake.UiRig, as a rig file." in text and "# the drive: a comment the save keeps" in text
    assert "vocal" not in files_served.rig.chains, "the fake rig plays it"
    expect(p.locator(".strip[data-channel='vocal']")).to_have_count(0)
    expect(p.locator(".strip[data-channel='board']")).to_have_count(1)
    assert page.errors == []
    page.close()


def test_no_lets_the_channel_be(browser, files_served):
    page = rack(browser, files_served)
    p = page.page
    unlock(p)
    group = ask(p, "vocal")
    group.get_by_role("button", name="No").click()
    expect(p.locator(".remove-panel")).to_have_count(0)
    assert channels(files_served) == ["board", "vocal"]
    page.close()


def test_a_removal_that_restarts_asks_restart_first(browser, files_served):
    """board is the first channel: without it the vocal's engine input moves,
    which only a restart does. "Restart?" is asked before anything is
    written; Yes removes it by restart."""
    files_served.lifecycle.how["electric_board"] = ("restart", [{"code": "track", "detail": "track 'vocal': inputs differ"}])
    before = path_of(files_served).read_bytes()
    page = rack(browser, files_served)
    p = page.page
    unlock(p)
    sent = []
    p.route("**/rig/channels/*/remove", lambda route: sent.append(route.request.post_data_json) or route.continue_())
    group = ask(p, "board")
    group.get_by_role("button", name="Yes").click()
    expect(group.locator(".confirm .q")).to_have_text("Restart?")
    expect(group.locator(".said")).to_have_text("removing board restarts Sushi")
    assert path_of(files_served).read_bytes() == before, "nothing written before the yes"
    group.get_by_role("button", name="Yes").click()
    expect(p.locator(".remove-done")).to_contain_text("Removed board")
    expect(p.locator(".remove-done")).to_have_attribute("title", re.compile("^Restarted \\(track 'vocal': inputs differ\\)"))
    assert sent == [{"restart": False, "rig": "electric_board"}, {"restart": True, "rig": "electric_board"}]
    assert channels(files_served) == ["vocal"]
    assert page.errors == []
    page.close()


def test_the_last_channel_is_not_offered(browser, files_served):
    one = RIG_FILE.split("- name: vocal")[0] + "master:\n  output: monitor-out\n  chain: []\n"
    path_of(files_served).write_text(one)
    page = rack(browser, files_served)
    p = page.page
    unlock(p)
    group = ask(p, "board")
    expect(group.locator(".said")).to_have_text("Last channel")
    expect(group.get_by_role("button", name="Yes")).to_have_count(0)
    page.close()


def test_a_removal_with_unsaved_work_offers_to_drop_it(browser, files_served):
    page = rack(browser, files_served)
    p = page.page
    unlock(p)
    status = p.evaluate("""async () => (await fetch('/live/parameter', {method: 'POST', headers: {'content-type':
      'application/json'}, body: JSON.stringify({processor: 'board_send', parameter: 'gain', value: 0.7})})).status""")
    assert status == 200
    group = ask(p, "vocal")
    group.get_by_role("button", name="Yes").click()
    result = group.locator(".remove-result")
    expect(result.locator("strong")).to_have_text("Not removed")
    expect(result.locator(".said")).to_contain_text("electric_board is running with 1 change")
    group.get_by_role("button", name="Remove anyway").click()
    expect(p.locator(".remove-done")).to_contain_text("Removed vocal")
    assert channels(files_served) == ["board"]
    page.close()


def test_midi_controls_on_the_channel_are_named_and_dropped_on_the_second_tap(browser):
    """The ask names the map's control on the vocal (from GET /midi); the
    agent refuses until it is dropped, the control named on the page."""
    with Served(save=True, pedals=True, rig_file=MAPPED) as served:
        page = rack(browser, served)
        p = page.page
        unlock(p)
        group = ask(p, "vocal")
        expect(group.locator(".remove-goes")).to_have_text("vocal · 1 plugin · 1 MIDI")
        expect(group.locator(".remove-goes")).to_have_attribute("title", re.compile(", and MIDI d \\(mute vocal\\) go\\.$"))
        group.get_by_role("button", name="Yes").click()
        result = group.locator(".remove-result")
        expect(result.locator(".said")).to_have_text("MIDI: d")
        expect(result).to_have_attribute("title", re.compile("d: mute vocal\\. it is the channel's mute\\."))
        assert channels(served) == ["board", "vocal"]
        group.get_by_role("button", name="Drop 1 and remove").click()
        expect(p.locator(".remove-done")).to_contain_text("Removed vocal")
        written = yaml.safe_load(path_of(served).read_text())
        assert [c["name"] for c in written["channels"]] == ["board"]
        assert written["midi"]["controls"] == [{"control": "c", "bypass": "board/delay"}]
        assert page.errors == []
        page.close()


def test_a_removal_is_taken_back_by_the_one_undo_held_while_locked(browser, files_served):
    """#209: the one Undo puts the file back as it was; the lock holds it, as
    it holds the removal. Putting a channel back is a restart, asked."""
    before = path_of(files_served).read_bytes()
    page = rack(browser, files_served)
    p = page.page
    unlock(p)
    ask(p, "vocal").get_by_role("button", name="Yes").click()
    expect(p.locator(".remove-done")).to_contain_text("Removed vocal")
    button = p.locator("button.undo")
    expect(button).to_have_attribute("title", "Undo remove channel vocal")
    p.locator("button.lock").click()  # locked again
    expect(button).to_be_disabled()
    expect(button).to_have_attribute("title", "Locked: remove channel vocal")
    unlock(p)
    button.click()
    expect(p.locator(".undo-ask .q")).to_have_text("Restart?")
    p.locator(".undo-ask").get_by_role("button", name="Yes").click()
    expect(p.locator(".undo-said")).to_have_text("Undone: remove channel vocal")
    assert path_of(files_served).read_bytes() == before
    page.close()
