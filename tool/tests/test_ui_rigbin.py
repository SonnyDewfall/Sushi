"""Deleting a rig from the rack's picker (#129), and the one Undo that puts it
back (#209).

Two layers, as test_ui.py has them: rig.js's decisions imported into a bare
page, then the rack driven in Chromium against a real agent over the fake of
`ui_fake.py` with a checkout in a temporary folder (`Served(save=True)`):
the agent's real POST /rigs/{name}/delete and POST /undo move real files there.
The rigs beside the fake's own are `ui_fake.RIG_FILE` under other names, three
committed and one not; a delete only moves a file, so what is in them is
nothing to these tests.

Since #209 a delete asks nothing and the picker offers no Undo of its own:
the header's one Undo puts the rig back (test_ui_undo.py has the rest of it).
What stands in for what. "A reload" is Playwright's, in the same browser
context, so the page's storage is kept as a browser keeps it. "A file made
since" and "a bin file gone" are made by the test on disk, which is all an
agent can see of either. Nothing here is the rig machine, a phone or a pedal:
no Program Change is sent.
"""

from __future__ import annotations

import re
import subprocess

import pytest

pytest.importorskip("aiohttp")

from test_ui import Page, blank, browser, expect, rigjs, served  # noqa: E402,F401
from ui_fake import RIG_FILE, Served  # noqa: E402

COMMITTED = ("accustomed", "clean", "electric-delay")
LOOSE = "zz-last"
# Sorted as a Program Change counts them: accustomed 0, clean 1,
# electric-delay 2, electric_board 3 (the running rig), zz-last 4.


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), "-c", "user.name=ui fake", "-c", "user.email=ui@fake.invalid",
                           "-c", "commit.gpgsign=false", *args], check=True, capture_output=True, text=True).stdout


@pytest.fixture
def bin_served():
    with Served(save=True) as served:
        rigs = served.save.root / "config" / "rigs"
        for name in COMMITTED:
            (rigs / f"{name}.yaml").write_text(RIG_FILE)
        git(served.save.root, "add", "config")
        git(served.save.root, "commit", "-q", "-m", "three more rigs")
        (rigs / f"{LOOSE}.yaml").write_text(RIG_FILE)
        yield served


def rigs_dir(served):
    return served.save.root / "config" / "rigs"


def binned(served):
    folder = rigs_dir(served) / ".deleted"
    return sorted(p.name for p in folder.iterdir() if p.name != ".gitignore") if folder.is_dir() else []


def pause_clock(p):
    """Hold the page's fake clock (installed before it loaded) where it is,
    give or take the second it is moved on to pause: from now on it moves
    only by `p.clock.run_for`, however long a step of the test takes.
    (test_ui_takes.py's notices are timed on it.)"""
    p.clock.pause_at(p.evaluate("() => Date.now()") / 1000 + 1)  # Playwright's Python clock takes seconds


def rack(browser, served, width=1280):
    page = Page(browser, served.url + "/#rack", width=width)
    page.page.wait_for_selector(".rig-row")
    return page


def row(p, name):
    return p.locator(f'.rig-row[data-rig="{name}"]')


def delete(p, name):
    row(p, name).get_by_role("button", name=f"Delete {name}", exact=True).click()


def undo_button(p):
    return p.locator("button.undo")


# --------------------------------------------------------------------------- #
# The decisions, pure (rig.js)
# --------------------------------------------------------------------------- #

def test_the_running_rigs_row_is_the_one_the_agent_names_in_rigs(blank):
    out = rigjs(blank, """
      const rigs = (running) => ({ok: true, body: {rigs: ['clean', 'electric_board'], running}});
      const running = R.runningListed(rigs('electric_board'));
      return {
        rows: ['electric_board', 'clean'].map((n) => R.deleteAction(n, running)),
        none: [R.runningListed(rigs(null)), R.runningListed({ok: false, reason: 'x'}), R.runningListed(null)],
        busy: R.deleteAction('clean', null, true).disabled,
        keys: [R.runKeyOf({ok: true, body: {rig: {running: true, rig: 'config/rigs/a.yaml', config_name: 'a', started_at: 't'}}}),
               R.runKeyOf({ok: true, body: {rig: {running: true, rig: 'config/rigs/b.yaml', config_name: 'a', started_at: 't'}}}),
               R.runKeyOf({ok: false})],
        busyDelete: R.deletedOutcome('clean', {ok: false, status: 409, reason: 'busy', code: 'busy'}).retry,
        silent: R.deletedOutcome('clean', {ok: false, status: 0, reason: 'the agent did not answer'}).retry,
        running: R.deletedOutcome('clean', {ok: false, status: 409, reason: 'r', code: 'running'}).retry,
      };""")
    running, other = out["rows"]
    assert running["disabled"] is True and "electric_board is the running rig" in running["why"]
    assert "switch to another rig, or stop it, first" in running["why"]
    # #209: Delete acts at once, so its name no longer ends in "…" (it opened a confirm).
    assert other == {"name": "clean", "disabled": False, "label": "Delete", "aria": "Delete clean", "why": None}
    assert out["none"] == [None, None, None], "no row is off unless the agent says it is the running rig's"
    assert out["busy"] is True, "one delete at a time"
    assert len(set(out["keys"])) == 3, "another rig file, or no answer, is another run: /rigs is read again"
    assert out["busyDelete"] is True and out["silent"] is True and out["running"] is False


def test_a_notice_that_worked_goes_after_its_time_and_a_refusal_never(blank):
    out = rigjs(blank, """
      const said = {ok: true, title: 'Recorded a', at: 1000};
      const refused = {ok: false, title: 'Not stopped', at: 1000};
      return {
        ms: R.NOTICE_MS,
        before: R.noticeDone(said, 1000 + R.NOTICE_MS - 1),
        after: R.noticeDone(said, 1000 + R.NOTICE_MS),
        refused: R.noticeDone(refused, 1e12),
        unstamped: R.noticeDone({ok: true}, 1e12),
        next: [R.nextNoticeIn([said, null, refused, {ok: true, at: 4000}], 2000),
               R.nextNoticeIn([said], 1000 + R.NOTICE_MS + 50), R.nextNoticeIn([refused, null], 0)],
        retired: R.RETIRED_KEYS,
      };""")
    assert out["ms"] == 15000, "about 15 s"
    assert out["before"] is False and out["after"] is True
    assert out["refused"] is False, "a refusal stays until it is dismissed"
    assert out["unstamped"] is False
    assert out["next"] == [14000, 0, None], "the soonest due, never below 0; none for a refusal"
    assert out["retired"] == ["sushi-rig.binned"]


# --------------------------------------------------------------------------- #
# The rack, in the browser
# --------------------------------------------------------------------------- #

def test_the_running_rigs_delete_is_off_with_the_reason_on_hover(browser, bin_served):
    page = rack(browser, bin_served)
    p = page.page
    button = row(p, "electric_board").locator(".rig-del")
    expect(button).to_be_disabled()
    # #209: the reason is the button's title, not a paragraph under the list.
    expect(button).to_have_attribute("title", "electric_board is the running rig, so it cannot be deleted: "
                                     "switch to another rig, or stop it, first.")
    expect(p.locator("#rig-del-why")).to_have_count(0)
    expect(row(p, "clean").locator(".rig-del")).to_be_enabled()
    page.close()


def test_the_running_rigs_row_follows_a_switch_made_elsewhere(browser, bin_served):
    """A switch from a pedal or another page: the page reads /rigs again
    when /health says the run changed, and the agent says which row."""
    page = rack(browser, bin_served)
    p = page.page
    expect(row(p, "electric_board").locator(".rig-del")).to_be_disabled()
    save = bin_served.save
    save.write_state({**save.state, "config_name": "clean", "rig": "config/rigs/clean.yaml"})
    expect(row(p, "clean").locator(".rig-del")).to_be_disabled()
    expect(row(p, "electric_board").locator(".rig-del")).to_be_enabled()
    expect(row(p, "clean").locator(".rig-del")).to_have_attribute("title", re.compile("clean is the running rig"))
    page.close()


def test_delete_moves_the_file_out_at_once_and_undo_moves_it_back(browser, bin_served):
    page = rack(browser, bin_served)
    p = page.page
    before = (rigs_dir(bin_served) / "clean.yaml").read_bytes()
    delete(p, "clean")
    expect(row(p, "clean")).to_have_count(0)
    assert p.locator(".confirm").count() == 0, "no confirm: Undo takes it back"
    assert not (rigs_dir(bin_served) / "clean.yaml").exists()
    [entry] = binned(bin_served)
    assert entry.startswith("clean-")
    expect(p.locator(".bin-offers, .undo-delete, .rig-deleted")).to_have_count(0)
    expect(undo_button(p)).to_have_attribute("title", "Undo delete clean")
    undo_button(p).click()
    expect(p.locator(".undo-said")).to_have_text("Undone: delete clean")
    expect(row(p, "clean")).to_have_count(1)
    assert (rigs_dir(bin_served) / "clean.yaml").read_bytes() == before
    assert binned(bin_served) == []
    page.close()


def test_two_deletes_are_undone_newest_first(browser, bin_served):
    page = rack(browser, bin_served)
    p = page.page
    delete(p, "clean")
    expect(row(p, "clean")).to_have_count(0)
    delete(p, "accustomed")
    expect(row(p, "accustomed")).to_have_count(0)
    undo_button(p).click()
    expect(row(p, "accustomed")).to_have_count(1)
    expect(row(p, "clean")).to_have_count(0)
    undo_button(p).click()
    expect(row(p, "clean")).to_have_count(1)
    page.close()


def test_the_old_record_of_undos_is_let_go_as_the_page_loads(browser, bin_served):
    """Until #189 the Undos were kept in this browser (`sushi-rig.binned`).
    An old record left in storage is removed as the page loads and never
    offered."""
    page = rack(browser, bin_served)
    p = page.page
    p.evaluate("() => localStorage.setItem('sushi-rig.binned', JSON.stringify({x: [{rig: 'clean'}]}))")
    p.reload()
    p.wait_for_selector(".rig-row")
    assert p.evaluate("() => localStorage.getItem('sushi-rig.binned')") is None, "the old record is removed"
    expect(p.locator(".bin-offers")).to_have_count(0)
    page.close()


def test_a_refused_delete_is_said_in_a_few_words_and_stays_until_dismissed(browser, bin_served):
    page = rack(browser, bin_served)
    p = page.page
    p.route("**/rigs/clean/delete", lambda route: route.fulfill(
        status=409, content_type="application/json",
        body='{"ok": false, "status": 409, "reason": "busy: \'up\' is still running", "code": "busy"}'))
    delete(p, "clean")
    said = p.locator(".rig-deleted")
    expect(said).to_have_text(re.compile("^Not deleted: clean"))
    expect(said).to_have_attribute("title", "busy: 'up' is still running")
    assert (rigs_dir(bin_served) / "clean.yaml").exists()
    said.get_by_role("button", name="Dismiss").click()
    expect(said).to_have_count(0)
    page.close()


def test_the_stage_picker_has_no_delete(browser, bin_served):
    page = Page(browser, bin_served.url + "/#stage")
    p = page.page
    p.locator("button.lock").click()
    p.locator("button.lock").click()
    expect(p.locator(".stage-head select")).to_be_enabled()
    expect(p.locator(".stage-rigs .rig-del")).to_have_count(0)
    page.close()
