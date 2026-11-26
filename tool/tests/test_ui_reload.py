"""The running rig's file reloaded, and a channel moved, on the page (#142):
"File changed" in the status zone when the file differs from what plays,
Reload in the activity zone (from it, and from a rack edit's answer) saying
"Live" or "Restart?" first, and a strip's move up and down.

Two layers, as test_ui_channel_remove.py has them: rig.js's decisions
imported into a bare page, then the rack driven in Chromium against a real
agent over the fake of `ui_fake.py`, with a checkout in a temporary folder
whose rig file a test edits by hand, or the agent's real routes write.

What stands in for what. Whether the file differs from what plays, and
whether its reload is live or a restart, is the agent's real `rigfile.diff`
of the fake's running config (the real compile of the file as it was) and
the file now. The switch the reload makes is `UiLifecycle.switch_held`,
which says "live" unless a test says restart, and then the fake takes the
file's chains and compile (`UiSave.switched`). Nothing here is Sushi, the
rig machine or a phone.
"""

from __future__ import annotations

import re

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


def path_of(served):
    return served.save.root / "config" / "rigs" / "electric_board.yaml"


def channels(served):
    return [c["name"] for c in yaml.safe_load(path_of(served).read_text())["channels"]]


def bar(p):
    return p.locator(".activity")


def reloads(served):
    return [c for c in served.lifecycle.calls if c[:1] == ["switch"]]


def hand_edit_delay(served, mix):
    """The board's delay given a parameter by hand, as a person would."""
    text = path_of(served).read_text()
    marker = "  - name: delay\n"
    at = text.index(marker) + len(marker)
    line_end = text.index("\n", at) + 1  # its uri line
    path_of(served).write_text(text[:line_end] + f"    parameters: {{mix: {mix}}}\n" + text[line_end:])


# --------------------------------------------------------------------------- #
# The decisions, pure (rig.js)
# --------------------------------------------------------------------------- #

def test_the_calls_the_words_and_the_lock(blank):
    out = rigjs(blank, """
      const filed = [{name: 'board'}, {name: 'vocal'}, {name: 'keys'}];
      return {
        calls: [R.reloadCall({}, 'electric_board'), R.reloadCall({discard: true, restart: true, label: 'x'})],
        move: R.moveChannelCall('my rig', 'my vocal', 'up'),
        can: [R.canMoveChannel(filed, 'board', 'up'), R.canMoveChannel(filed, 'board', 'down'),
              R.canMoveChannel(filed, 'keys', 'down'), R.canMoveChannel(filed, 'vocal', 'up'),
              R.canMoveChannel(filed, 'drums', 'up'), R.canMoveChannel(null, 'board', 'down')],
        words: [R.reloadWord('live'), R.reloadWord('restart'), R.reloadWord(null)],
        locked: [R.lockRefusal(true, 'reload'), R.lockRefusal(true, 'move_channel'), R.lockRefusal(false, 'reload')],
        undo: ['reload', 'move_channel'].map((act) => R.undoButton({ok: true, body: {next: {seq: 3, kind: 'file', act,
          label: `${act} x`, can: true}}}, {locked: true})),
        busy: R.activityBusy({reloading: true}),
      };""")
    assert out["calls"][0] == {"path": "/rig/reload", "json": {"restart": False, "rig": "electric_board"}}
    assert out["calls"][1] == {"path": "/rig/reload", "json": {"restart": True, "discard": True}}
    assert out["move"] == {"path": "/rigs/my%20rig/channels/my%20vocal/move", "json": {"direction": "up"}}
    assert out["can"] == [False, True, False, True, False, False]
    assert out["words"] == ["Live", "Restart?", "Restart?"]
    assert out["locked"][0].startswith("Locked: a reload plays the rig's file as it is now")
    assert out["locked"][1].startswith("Locked: moving a channel changes the rig's structure")
    assert out["locked"][2] is None
    assert [u["disabled"] for u in out["undo"]] == [True, True], "the lock holds a reload's and a move's Undo"
    assert out["busy"] == "Reloading…"


def test_what_file_changed_says(blank):
    out = rigjs(blank, """
      const look = (body) => ({ok: true, status: 200, body: {ok: true, rig: 'r', file: 'config/rigs/r.yaml', ...body}});
      return {
        same: R.fileChanged(look({changed: false})),
        unknown: R.fileChanged(look({changed: null})),
        down: R.fileChanged({ok: false, status: 0, body: null}),
        live: R.fileChanged(look({changed: true, how: 'live', why: [], unsaved: 0})),
        restart: R.fileChanged(look({changed: true, how: 'restart', why: [{code: 'track_added', detail: "track 'keys'"}],
          unsaved: 2})),
        broken: R.fileChanged(look({changed: true, how: null, problem: 'not reloaded: no role x-in', code: 'rig_problem'})),
      };""")
    assert out["same"] is None and out["unknown"] is None and out["down"] is None
    assert (out["live"]["title"], out["live"]["word"], out["live"]["how"]) == ("File changed", "Live", "live")
    assert out["live"]["detail"] == "config/rigs/r.yaml differs from what r plays: Reload plays it live."
    assert out["restart"]["word"] == "Restart?"
    assert out["restart"]["detail"] == ("config/rigs/r.yaml differs from what r plays: Reload restarts it "
                                        "(track 'keys'). 2 unsaved changes: a reload drops them.")
    assert out["broken"]["word"] == "Won't load" and out["broken"]["problem"]


def test_what_a_reload_says_and_the_second_tap_each_refusal_offers(blank):
    out = rigjs(blank, """
      const ok = (body) => ({ok: true, status: 200, body: {ok: true, file: 'config/rigs/r.yaml', ...body}});
      const no = (code, reason, body = {}) => ({ok: false, status: 409, code, reason, body: {ok: false, code, reason, ...body}});
      return {
        live: R.reloadOutcome(ok({how: 'live', why: []})),
        restart: R.reloadOutcome(ok({how: 'restart', why: [{code: 'track_added', detail: "track 'keys'"}],
          discard: {note: '1 unsaved change dropped: the rig restarted from its file, which drops them'}})),
        same: R.reloadOutcome(ok({how: 'unchanged', note: 'config/rigs/r.yaml is what r plays: nothing to reload'})),
        unsaved: R.reloadOutcome(no('unsaved', 'r is running with 1 change made through the agent and not saved')),
        restarts: R.reloadOutcome(no('restarts', 'nothing was changed: reloading r restarts Sushi: track_added',
          {why: [{code: 'track_added', detail: "track 'keys'"}]}), {discard: true}),
        broken: R.reloadOutcome(no('rig_problem', 'not reloaded: r.yaml cannot be wired')),
        unswitched: R.reloadOutcome(no('not_switched', "r's file was read again, but the rig was not switched to it",
          {written: true, midi_note: 'the pedals follow the rig as it runs'})),
      };""")
    assert (out["live"]["title"], out["live"]["said"]) == ("Reloaded", "Live")
    assert out["live"]["text"] == "Played config/rigs/r.yaml live, with no restart."
    assert (out["restart"]["said"], out["restart"]["text"]) == ("Restarted", "Restarted to play config/rigs/r.yaml (track 'keys').")
    assert out["restart"]["notes"] == ["1 unsaved change dropped: the rig restarted from its file, which drops them."]
    assert out["same"]["title"] == "Unchanged" and out["same"]["offer"] is None
    assert out["unsaved"]["title"] == "Not reloaded"
    assert out["unsaved"]["offer"] == {"label": "Reload anyway", "discard": True}
    assert out["restarts"]["ask"] == "restart"
    assert out["restarts"]["offer"] == {"discard": True, "restart": True}, "what was said yes to is kept"
    assert out["restarts"]["why"] == [{"code": "track_added", "detail": "track 'keys'"}]
    assert out["broken"]["title"] == "Not reloaded" and out["broken"]["offer"] is None
    assert out["broken"]["said"] == "r.yaml cannot be wired"
    assert out["unswitched"]["title"] == "Not switched"
    assert out["unswitched"]["notes"] == ["the pedals follow the rig as it runs."]


def test_a_rack_edit_offers_a_reload_saying_live_or_restart(blank):
    out = rigjs(blank, """
      const ok = (body) => ({ok: true, status: 200, body: {ok: true, rig: 'r', file: 'config/rigs/r.yaml', running: true,
        offer: 'reload', ...body}});
      return {
        live: R.rigFileOutcome('input', "vocal's input", ok({how_reload: 'live', why: []})),
        restart: R.rigFileOutcome('move', 'vocal up', ok({how_reload: 'restart',
          why: [{code: 'track_order', detail: "['board', 'vocal'] becomes ['vocal', 'board']"}]})),
        other: R.rigFileOutcome('move', 'vocal up', ok({running: false, offer: null})),
        refused: R.rigFileOutcome('move', 'board up', {ok: false, status: 409, code: 'edge', reason: 'nothing was written: board is already r\\'s first channel', body: {}}),
      };""")
    assert out["live"]["offer"] == {"op": "reload", "name": "r", "label": "Reload", "how": "live", "why": []}
    assert out["live"]["text"] == ("Wrote config/rigs/r.yaml in place, keeping its comments. r keeps playing as it was "
                                   "until it is reloaded: Reload plays it live.")
    assert out["restart"]["title"] == "Moved vocal up" and out["restart"]["offer"]["how"] == "restart"
    assert out["restart"]["text"].endswith("Reload restarts it (['board', 'vocal'] becomes ['vocal', 'board']).")
    assert out["other"]["offer"] is None and "next started or switched to" in out["other"]["text"]
    assert out["refused"]["title"] == "Not moved: board up"


# --------------------------------------------------------------------------- #
# The rack, against the agent
# --------------------------------------------------------------------------- #

def test_a_hand_edit_is_file_changed_and_reload_plays_it_live(browser, files_served):
    page = rack(browser, files_served)
    p = page.page
    expect(p.locator(".zone-status .problem.file")).to_have_count(0)
    hand_edit_delay(files_served, 0.3)
    changed = p.locator(".zone-status .problem.file")
    expect(changed).to_have_text("File changed")
    expect(changed).to_have_attribute("title", re.compile(
        "^config/rigs/electric_board.yaml differs from what electric_board plays: Reload plays it live\\.$"))
    item = bar(p).locator(".file-changed-act")
    expect(item.locator("strong")).to_have_text("File changed")
    expect(item.locator(".said")).to_have_text("Live")
    unlock(p)
    sent = []
    p.route("**/rig/reload", lambda route: (sent.append(route.request.post_data_json)
                                            if route.request.method == "POST" else None) or route.continue_())
    item.get_by_role("button", name="Reload").click()
    done = bar(p).locator(".reload-result")
    expect(done.locator("strong")).to_have_text("Reloaded")
    expect(done.locator(".said")).to_have_text("Live")
    expect(done).to_have_attribute("title", "Played config/rigs/electric_board.yaml live, with no restart.")
    assert sent == [{"restart": False, "rig": "electric_board"}], "asked to say first if it would restart"
    assert reloads(files_served) == [["switch", "electric_board"]]
    expect(p.locator(".zone-status .problem.file")).to_have_count(0)
    expect(bar(p).locator(".file-changed-act")).to_have_count(0)
    assert "mix: 0.3" in path_of(files_served).read_text(), "nothing written"
    assert page.errors == []
    page.close()


def test_file_changed_that_restarts_asks_first_and_no_leaves_it(browser, files_served):
    files_served.lifecycle.how["electric_board"] = ("restart", [{"code": "track_added", "detail": "track 'keys'"}])
    page = rack(browser, files_served)
    p = page.page
    unlock(p)
    text = path_of(files_served).read_text()
    path_of(files_served).write_text(text.replace("master:\n", "- name: keys\n  input: keys-in\n  width: mono\n  chain: []\nmaster:\n"))
    item = bar(p).locator(".file-changed-act")
    expect(item.locator(".said")).to_have_text("Restart?")
    expect(p.locator(".zone-status .problem.file")).to_have_attribute("title", re.compile("Reload restarts it \\(track 'keys'\\)"))
    item.get_by_role("button", name="Reload").click()
    ask = bar(p).locator(".reload-ask")
    expect(ask.locator(".q")).to_have_text("Restart?")
    expect(ask.locator(".q")).to_have_attribute("title", re.compile("it restarts \\(track 'keys'\\)"))
    expect(bar(p).locator(".file-changed-act")).to_have_count(0), "the question is the one place for it"
    assert reloads(files_served) == [], "nothing before the yes"
    bar(p).get_by_role("button", name="No", exact=True).click()
    expect(bar(p).locator(".reload-ask")).to_have_count(0)
    expect(bar(p).locator(".file-changed-act")).to_be_visible()
    assert reloads(files_served) == []
    bar(p).locator(".file-changed-act").get_by_role("button", name="Reload").click()
    bar(p).get_by_role("button", name="Yes").click()
    expect(bar(p).locator(".reload-result .said")).to_have_text("Restarted")
    expect(p.get_by_role("tab")).to_have_text(["board", "vocal", "keys", "Master"])
    expect(p.locator(".zone-status .problem.file")).to_have_count(0)
    assert page.errors == []
    page.close()


def test_a_reload_with_unsaved_work_offers_to_drop_it(browser, files_served):
    page = rack(browser, files_served)
    p = page.page
    unlock(p)
    hand_edit_delay(files_served, 0.3)
    status = p.evaluate("""async () => (await fetch('/live/parameter', {method: 'POST', headers: {'content-type':
      'application/json'}, body: JSON.stringify({processor: 'board_send', parameter: 'gain', value: 0.7})})).status""")
    assert status == 200
    expect(p.locator(".zone-status .problem.file")).to_have_attribute("title", re.compile("1 unsaved change: a reload drops them"))
    bar(p).locator(".file-changed-act").get_by_role("button", name="Reload").click()
    result = bar(p).locator(".reload-result")
    expect(result.locator("strong")).to_have_text("Not reloaded")
    expect(result.locator(".said")).to_contain_text("electric_board is running with 1 change")
    assert reloads(files_served) == []
    result.get_by_role("button", name="Reload anyway").click()
    expect(bar(p).locator(".reload-result strong")).to_have_text("Reloaded")
    assert reloads(files_served) == [["switch", "electric_board"]]
    page.close()


def test_a_channel_moves_up_from_its_strip_and_the_reload_says_restart(browser, files_served):
    """Behind the lock; the file's order, comments kept; the strips stay in
    the order that plays until the reload, which restarts (a channel's place
    is its track's in Sushi) and is asked first."""
    files_served.lifecycle.how["electric_board"] = ("restart", [{"code": "track_order", "detail": "order"}])
    page = rack(browser, files_served)
    p = page.page
    up = p.get_by_role("button", name="Move vocal up")
    expect(p.get_by_role("button", name="Move board up")).to_be_disabled()
    expect(p.get_by_role("button", name="Move vocal down")).to_be_disabled()
    up.click()
    refused = bar(p).locator(".act-item.refused")
    expect(refused.locator("strong")).to_have_text("Not moved: vocal")
    expect(refused).to_have_attribute("title", re.compile("^Locked: moving a channel"))
    assert channels(files_served) == ["board", "vocal"]
    unlock(p)
    expect(bar(p).locator(".act-item.refused:not(.past)")).to_have_count(0), "the lock's refusal goes with the lock"
    up.click()
    result = bar(p).locator(".wiring-result")
    expect(result.locator("strong")).to_have_text("Moved vocal up")
    expect(result.locator(".said")).to_have_text("Restart?")
    expect(result).to_have_attribute("title", re.compile("Reload restarts it \\(.*becomes"))
    assert channels(files_served) == ["vocal", "board"]
    assert "# the drive: a comment the save keeps" in path_of(files_served).read_text()
    expect(p.locator(".strip .strip-name")).to_have_text(["board", "vocal", "master"]), "still playing as it was"
    expect(p.get_by_role("button", name="Move vocal up")).to_be_disabled(), "first in the file now"
    expect(p.locator(".zone-status .problem.file")).to_have_text("File changed")
    expect(bar(p).locator(".file-changed-act")).to_have_count(0), "the edit's answer offers the reload"
    result.get_by_role("button", name="Reload").click()
    expect(bar(p).locator(".reload-ask .q")).to_have_text("Restart?")
    bar(p).get_by_role("button", name="Yes").click()
    expect(bar(p).locator(".reload-result .said")).to_have_text("Restarted")
    assert reloads(files_served) == [["switch", "electric_board"]]
    button = p.locator("button.undo")
    expect(button).to_have_attribute("title", "Undo reload electric_board")
    assert page.errors == []
    page.close()
