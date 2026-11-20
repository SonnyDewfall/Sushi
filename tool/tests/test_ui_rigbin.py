"""Deleting a rig from the rack's picker, and Undo (#129).

Two layers, as test_ui.py has them: rig.js's decisions imported into a bare
page, then the rack driven in Chromium against a real agent over the fake of
`ui_fake.py` with a checkout in a temporary folder (`Served(save=True)`):
the agent's real POST /rigs/{name}/delete and /restore move real files there.
The rigs beside the fake's own are `ui_fake.RIG_FILE` under other names, three
committed and one not; a delete only moves a file, so what is in them is
nothing to these tests.

What stands in for what. "A reload" is Playwright's, in the same browser
context, so the page's storage is kept as a browser keeps it. "A file made
since" and "a bin file gone" are made by the test on disk, which is all an
agent can see of either. The 15 s a notice that worked stays (#189) is passed
on Playwright's fake clock, installed before the page loads and paused once
it has (`pause_clock`): installed alone, the fake clock still moves with real
time, so a slow step between a notice and the test moving the clock would
eat into the 15 s. Paused, the page's timers, `Date.now` and
`performance.now` move only as a test moves them (`run_for`); the network
does not wait for it, so what follows is waited for by condition. Nothing here is the
rig machine, a phone or a pedal: the Program Change numbers are shown as the
agent counts them, and no Program Change is sent.
"""

from __future__ import annotations

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
    only by `p.clock.run_for`, however long a step of the test takes."""
    p.clock.pause_at(p.evaluate("() => Date.now()") / 1000 + 1)  # Playwright's Python clock takes seconds


def rack(browser, served, width=1280, clock=False):
    if clock:
        page = Page.__new__(Page)
        page.errors = []
        page.page = browser.new_page(viewport={"width": width, "height": 900})
        page.page.on("pageerror", lambda error: page.errors.append(str(error)))
        page.page.clock.install()
        page.page.goto(served.url + "/#rack")
    else:
        page = Page(browser, served.url + "/#rack", width=width)
    page.page.wait_for_selector(".rig-row")
    if clock:
        pause_clock(page.page)
    return page


NOTICE_S = 15


def row(p, name):
    return p.locator(f'.rig-row[data-rig="{name}"]')


def undo(p, rig):
    return p.locator(".bin-offers li", has_text=f"Deleted {rig}").locator(".undo-delete")


def ask_delete(p, name):
    row(p, name).get_by_role("button", name=f"Delete {name}…").click()
    confirm = p.get_by_role("group", name=f"Confirm deleting {name}")
    expect(confirm.locator(".delete-go")).to_be_enabled()
    return confirm


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
      };""")
    running, other = out["rows"]
    assert running["disabled"] is True and "electric_board is the running rig" in running["why"]
    assert "switch to another rig, or stop it, first" in running["why"]
    assert other == {"name": "clean", "disabled": False, "label": "Delete", "aria": "Delete clean…", "why": None}
    assert out["none"] == [None, None, None], "no row is off unless the agent says it is the running rig's"
    assert out["busy"] is True, "one delete at a time"
    assert len(set(out["keys"])) == 3, "another rig file, or no answer, is another run: /rigs is read again"


def test_the_confirm_says_the_numbers_that_move_and_only_when_one_does(blank):
    out = rigjs(blank, """
      const ok = (body) => ({ok: true, status: 200, body});
      const moved = {moved: [{rig: 'electric-delay', from: 6, to: 5}, {rig: 'zz', from: 7, to: 6}], note: 'x'};
      const git = {committed: true, same: true, restore: 'git checkout HEAD -- config/rigs/clean.yaml'};
      return {
        moves: R.deleteConfirm('clean', ok({file: 'config/rigs/clean.yaml', git, programs: moved})),
        last: R.deleteConfirm('zz', ok({file: 'config/rigs/zz.yaml', git: {committed: false, same: null, restore: null}})),
        changed: R.deleteConfirm('clean', ok({file: 'config/rigs/clean.yaml', git: {...git, same: false}})).lines[1],
        unknown: R.deleteConfirm('clean', ok({file: 'config/rigs/clean.yaml', git: null, git_note: 'git could not be read here'})).lines[1],
        asking: R.deleteConfirm('clean', null),
        refused: R.deleteConfirm('electric_board', {ok: false, status: 409, reason: 'electric_board is the running rig', code: 'running'}),
        busy: R.deleteConfirm('clean', {ok: false, status: 409, reason: "busy: 'up' is still running", code: 'busy'}).retry,
        silent: R.deleteConfirm('clean', {ok: false, status: 0, reason: 'the agent did not answer'}).retry,
        busyDelete: R.deletedOutcome('clean', {ok: false, status: 409, reason: 'busy', code: 'busy'}).retry,
      };""")
    assert out["moves"]["title"] == "Delete config/rigs/clean.yaml?"
    assert out["moves"]["moves"] == ["electric-delay moves from program 6 to 5", "zz moves from program 7 to 6"]
    assert "pedal preset" in out["moves"]["programsNote"]
    assert out["moves"]["lines"][1] == "It is committed, so `git checkout HEAD -- config/rigs/clean.yaml` restores it too."
    assert out["last"]["moves"] == [] and out["last"]["programsNote"] is None
    assert out["last"]["lines"][1] == "It is not committed, so the bin holds the only copy."
    assert "as it was last committed: the changes since are only in the bin" in out["changed"]
    assert out["unknown"] == "git could not be read here."
    assert out["asking"]["loading"] is True
    assert out["refused"]["refused"] == "electric_board is the running rig" and out["refused"]["retry"] is False
    assert out["busy"] is True and out["silent"] is True and out["busyDelete"] is True, "a passing refusal can be tried again"


def test_undo_stays_offered_only_after_a_refusal_that_may_pass(blank):
    out = rigjs(blank, """
      const no = (code) => ({ok: false, status: 409, reason: 'r', code});
      return ['taken', 'gone', 'entry', 'name', 'link', 'busy'].map((c) => R.restoreOutcome('clean', no(c)).keep)
        .concat([R.restoreOutcome('clean', {ok: false, status: 0, reason: 'the agent did not answer'}).keep]);""")
    assert out == [False, False, False, False, False, True, True], "a clash is said once, not kept"


def test_a_notice_that_worked_goes_after_its_time_and_a_refusal_never(blank):
    out = rigjs(blank, """
      const o = (rig, at) => ({rig, entry: `${rig}-20261116T101501Z.yaml`, file: 'f', at});
      const said = {ok: true, title: 'Deleted a', at: 1000};
      const refused = {ok: false, title: 'Not put back: a', at: 1000};
      const left = (bin, now) => R.noticesLeft(bin, now);
      let offers = [];
      for (const rig of ['a', 'b', 'c', 'd', 'e', 'f']) offers = R.addOffer(offers, o(rig, 1000));
      return {
        ms: R.NOTICE_MS,
        before: left({said, offers: [o('a', 1000)]}, 1000 + R.NOTICE_MS - 1),
        after: left({said, offers: [o('a', 1000), o('b', 9000)]}, 1000 + R.NOTICE_MS),
        refused: left({said: refused}, 1e12).said,
        restoring: left({offers: [o('a', 1000)], restoring: 'a-20261116T101501Z.yaml'}, 1e12).offers.map((x) => x.rig),
        unstamped: R.noticeDone({ok: true}, 1e12),
        next: [R.nextNoticeIn([said, null, refused, {ok: true, at: 4000}], 2000),
               R.nextNoticeIn([said], 1000 + R.NOTICE_MS + 50), R.nextNoticeIn([refused, null], 0)],
        five: offers.map((x) => x.rig),
        retired: R.RETIRED_KEYS,
      };""")
    assert out["ms"] == 15000, "about 15 s"
    assert out["before"]["said"]["title"] == "Deleted a" and len(out["before"]["offers"]) == 1, "not before"
    assert out["after"]["said"] is None and [x["rig"] for x in out["after"]["offers"]] == ["b"], "each by its own time"
    assert out["refused"]["title"] == "Not put back: a", "a refusal stays until it is dismissed"
    assert out["restoring"] == ["a"], "the Undo in flight stays until its answer"
    assert out["unstamped"] is False
    assert out["next"] == [14000, 0, None], "the soonest due, never below 0; none for a refusal"
    assert out["five"] == ["f", "e", "d", "c", "b"], "newest first, five at most"
    assert out["retired"] == ["sushi-rig.binned"]


def test_an_undo_is_offered_only_while_the_agent_still_has_it_in_its_bin(blank):
    out = rigjs(blank, """
      const o = (rig) => ({rig, entry: `${rig}-20261116T101501Z.yaml`, file: 'f'});
      const offers = [o('a'), o('b'), o('c'), o('d')];
      const bin = {ok: true, body: {entries: [
        {rig: 'a', entry: 'a-20261116T101501Z.yaml', back: false},
        {rig: 'b', entry: 'b-20261116T101501Z.yaml', back: true},
        {rig: 'x', entry: 'd-20261116T101501Z.yaml', back: false}]}};
      return [R.checkedOffers(offers, bin).map((x) => x.rig), R.checkedOffers(offers, {ok: false, reason: 'x'}),
              R.checkedOffers(offers, null)];""")
    assert out == [["a"], [], []], "gone (c), back in the picker (b), not that rig's (d), or not asked yet"


# --------------------------------------------------------------------------- #
# The rack, in the browser
# --------------------------------------------------------------------------- #

def test_the_confirm_shows_which_program_numbers_move(browser, bin_served):
    page = rack(browser, bin_served)
    p = page.page
    confirm = ask_delete(p, "clean")
    expect(confirm).to_contain_text("Delete config/rigs/clean.yaml?")
    moves = confirm.locator(".moves li")
    expect(moves).to_have_text(["electric-delay moves from program 2 to 1", "electric_board moves from program 3 to 2",
                                "zz-last moves from program 4 to 3"])
    expect(confirm).to_contain_text("`git checkout HEAD -- config/rigs/clean.yaml` restores it too")
    expect(p.locator(".delete-cancel")).to_be_focused(), "a second Enter does not delete"
    assert sorted(p.name for p in rigs_dir(bin_served).glob("*.yaml"))[1] == "clean.yaml", "asking moves nothing"
    confirm.get_by_role("button", name="Cancel").click()
    expect(p.locator(".delete-confirm")).to_have_count(0)
    expect(row(p, "clean").locator(".rig-del")).to_be_focused()
    page.close()


def test_the_last_rig_moves_no_number_so_the_confirm_says_none(browser, bin_served):
    page = rack(browser, bin_served)
    p = page.page
    confirm = ask_delete(p, LOOSE)
    expect(confirm).to_contain_text(f"Delete config/rigs/{LOOSE}.yaml?")
    expect(confirm).to_contain_text("It is not committed, so the bin holds the only copy.")
    expect(confirm.locator(".programs-moved")).to_have_count(0)
    expect(confirm).not_to_contain_text("moves from program")
    page.close()


def test_the_running_rigs_delete_is_off_with_the_reason_shown(browser, bin_served):
    page = rack(browser, bin_served)
    p = page.page
    button = row(p, "electric_board").locator(".rig-del")
    expect(button).to_be_disabled()
    reason = p.locator("#rig-del-why")
    expect(reason).to_have_text("electric_board is the running rig, so it cannot be deleted: switch to another rig, "
                                "or stop it, first.")
    expect(button).to_have_attribute("aria-describedby", "rig-del-why")
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
    expect(p.locator("#rig-del-why")).to_contain_text("clean is the running rig")
    page.close()


def test_delete_then_undo_moves_the_file_out_and_back(browser, bin_served):
    page = rack(browser, bin_served)
    p = page.page
    before = (rigs_dir(bin_served) / "clean.yaml").read_bytes()
    ask_delete(p, "clean").locator(".delete-go").click()
    said = p.locator(".rig-deleted")
    expect(said).to_contain_text("Deleted clean")
    expect(said).to_contain_text("electric-delay moves from program 2 to 1")
    expect(row(p, "clean")).to_have_count(0)
    assert not (rigs_dir(bin_served) / "clean.yaml").exists()
    [entry] = binned(bin_served)
    assert entry.startswith("clean-")
    expect(undo(p, "clean")).to_be_focused()
    undo(p, "clean").click()
    expect(said).to_contain_text("Put back clean")
    expect(said).to_contain_text("electric-delay moves from program 1 to 2")
    expect(row(p, "clean")).to_have_count(1)
    assert (rigs_dir(bin_served) / "clean.yaml").read_bytes() == before
    assert binned(bin_served) == []
    expect(p.locator(".undo-delete")).to_have_count(0)
    page.close()


def test_deleting_another_rig_keeps_the_first_ones_undo(browser, bin_served):
    page = rack(browser, bin_served)
    p = page.page
    ask_delete(p, "clean").locator(".delete-go").click()
    expect(undo(p, "clean")).to_be_visible()
    ask_delete(p, "accustomed").locator(".delete-go").click()
    expect(p.locator(".bin-offers li")).to_have_count(2)
    expect(p.locator(".bin-offers li").first).to_contain_text("Deleted accustomed")
    undo(p, "clean").click()
    expect(row(p, "clean")).to_have_count(1)
    expect(p.locator(".bin-offers li")).to_have_count(1)
    expect(undo(p, "accustomed")).to_be_visible()
    page.close()


def test_the_x_beside_an_undo_forgets_it(browser, bin_served):
    """#189: the × on an Undo changed a copy of the picker's state made for
    its key (`binKey`), so nothing changed and nothing was redrawn."""
    page = rack(browser, bin_served)
    p = page.page
    ask_delete(p, "clean").locator(".delete-go").click()
    ask_delete(p, "accustomed").locator(".delete-go").click()
    expect(p.locator(".bin-offers li")).to_have_count(2)
    p.get_by_role("button", name="Forget the Undo for clean").click()
    expect(p.locator(".bin-offers li")).to_have_count(1)
    expect(undo(p, "accustomed")).to_be_visible()
    p.get_by_role("button", name="Forget the Undo for accustomed").click()
    expect(p.locator(".bin-offers")).to_have_count(0)
    # Polls and a bin listing later, it stays forgotten.
    polls = p.evaluate("() => performance.getEntriesByType('resource').filter((r) => r.name.endsWith('/health')).length")
    p.wait_for_function(f"() => performance.getEntriesByType('resource').filter((r) => r.name.endsWith('/health')).length > {polls + 2}")
    expect(p.locator(".bin-offers")).to_have_count(0)
    assert len(binned(bin_served)) == 2, "forgetting an Undo moves nothing: the files stay in the bin"
    page.close()


def test_the_outcomes_x_and_the_confirms_cancel_close_them(browser, bin_served):
    page = rack(browser, bin_served)
    p = page.page
    ask_delete(p, "clean").locator(".delete-go").click()
    said = p.locator(".rig-deleted")
    expect(said).to_contain_text("Deleted clean")
    said.get_by_role("button", name="Dismiss").click()
    expect(said).to_have_count(0)
    expect(undo(p, "clean")).to_be_visible()  # the Undo is its own, with its own ×
    ask_delete(p, "accustomed").get_by_role("button", name="Cancel").click()
    expect(p.locator(".delete-confirm")).to_have_count(0)
    page.close()


def test_a_delete_that_worked_and_its_undo_go_after_about_15_s(browser, bin_served):
    page = rack(browser, bin_served, clock=True)
    p = page.page
    ask_delete(p, "clean").locator(".delete-go").click()
    said = p.locator(".rig-deleted")
    expect(said).to_contain_text("Deleted clean")
    expect(undo(p, "clean")).to_be_visible()
    p.clock.run_for((NOTICE_S - 1) * 1000)
    expect(said).to_contain_text("Deleted clean")
    expect(undo(p, "clean")).to_be_visible()
    p.clock.run_for(1500)
    expect(said).to_have_count(0)
    expect(p.locator(".bin-offers")).to_have_count(0)
    [entry] = binned(bin_served)
    assert entry.startswith("clean-"), "the file stays in config/rigs/.deleted/, to move back by hand"
    page.close()


def test_a_put_back_that_worked_goes_too_and_a_refusal_stays(browser, bin_served):
    page = rack(browser, bin_served, clock=True)
    p = page.page
    ask_delete(p, "clean").locator(".delete-go").click()
    ask_delete(p, "accustomed").locator(".delete-go").click()
    undo(p, "clean").click()
    said = p.locator(".rig-deleted")
    expect(said).to_contain_text("Put back clean")
    expect(row(p, "clean")).to_have_count(1)
    p.clock.run_for((NOTICE_S + 1) * 1000)
    expect(said).to_have_count(0)
    expect(p.locator(".bin-offers")).to_have_count(0)
    # A refusal: a delete, a file of that name made since by hand, and its Undo.
    ask_delete(p, LOOSE).locator(".delete-go").click()
    expect(undo(p, LOOSE)).to_be_visible()
    (rigs_dir(bin_served) / f"{LOOSE}.yaml").write_text("made since\n")
    undo(p, LOOSE).click()
    expect(said).to_contain_text(f"Not put back: {LOOSE}")
    p.clock.run_for(5 * NOTICE_S * 1000)
    expect(said).to_contain_text(f"Not put back: {LOOSE}"), "an error is not gone unread"
    said.get_by_role("button", name="Dismiss").click()
    expect(said).to_have_count(0)
    page.close()


def test_an_undo_is_not_kept_across_a_reload_and_the_old_record_is_let_go(browser, bin_served):
    """Until #189 the Undos were kept in this browser (`sushi-rig.binned`).
    Now a reload offers none, and an old record left in storage is removed
    as the page loads and never offered, even of a file still in the bin."""
    page = rack(browser, bin_served)
    p = page.page
    ask_delete(p, "clean").locator(".delete-go").click()
    expect(undo(p, "clean")).to_be_visible()
    [entry] = binned(bin_served)
    agent = p.evaluate("() => fetch('/rigs/bin').then((r) => r.json()).then((b) => b.agent)")
    assert p.evaluate("() => localStorage.getItem('sushi-rig.binned')") is None, "nothing is written"
    old = {agent: [{"rig": "clean", "entry": entry, "file": f"config/rigs/.deleted/{entry}"}]}
    p.evaluate("(old) => localStorage.setItem('sushi-rig.binned', JSON.stringify(old))", old)
    p.reload()
    p.wait_for_selector(".rig-row")
    p.wait_for_function("() => performance.getEntriesByType('resource').some((r) => r.name.endsWith('/rigs/bin'))")
    expect(p.locator(".bin-offers")).to_have_count(0)
    expect(p.locator(".rig-deleted")).to_have_count(0)
    assert p.evaluate("() => localStorage.getItem('sushi-rig.binned')") is None, "the old record is removed"
    assert binned(bin_served) == [entry]
    page.close()


def test_undo_refused_on_a_clash_is_said_once_and_not_offered_again(browser, bin_served):
    page = rack(browser, bin_served)
    p = page.page
    ask_delete(p, "clean").locator(".delete-go").click()
    expect(undo(p, "clean")).to_be_visible()
    (rigs_dir(bin_served) / "clean.yaml").write_text("made since\n")
    undo(p, "clean").click()
    said = p.locator(".rig-deleted")
    expect(said).to_contain_text("Not put back: clean")
    expect(said).to_contain_text("is there again")
    expect(p.locator(".bin-offers")).to_have_count(0)
    assert (rigs_dir(bin_served) / "clean.yaml").read_text() == "made since\n"
    assert len(binned(bin_served)) == 1, "the binned one stays in config/rigs/.deleted/"
    p.reload()
    p.wait_for_selector(".rig-row")
    p.wait_for_function("() => performance.getEntriesByType('resource').some((r) => r.name.endsWith('/rigs/bin'))")
    expect(p.locator(".rig-deleted")).to_have_count(0)
    expect(p.locator(".bin-offers")).to_have_count(0)
    page.close()


def test_undo_refused_when_the_bin_file_is_gone_says_so_and_offers_it_no_more(browser, bin_served):
    page = rack(browser, bin_served)
    p = page.page
    ask_delete(p, "clean").locator(".delete-go").click()
    expect(undo(p, "clean")).to_be_visible()
    [entry] = binned(bin_served)
    (rigs_dir(bin_served) / ".deleted" / entry).unlink()
    undo(p, "clean").click()
    said = p.locator(".rig-deleted")
    expect(said).to_contain_text("Not put back: clean")
    expect(said).to_contain_text("is not there any more")
    expect(p.locator(".undo-delete")).to_have_count(0)
    said.get_by_role("button", name="Dismiss").click()
    expect(said).to_have_count(0)
    page.close()


def test_a_busy_refusal_in_the_confirm_can_be_tried_again_in_place(browser, bin_served):
    page = rack(browser, bin_served)
    p = page.page
    calls = {"n": 0}

    def busy_once(route):
        calls["n"] += 1
        if calls["n"] == 1:
            route.fulfill(status=409, content_type="application/json",
                          body='{"ok": false, "status": 409, "reason": "busy: \'up\' is still running", "code": "busy"}')
        else:
            route.continue_()

    p.route("**/rigs/clean/delete", busy_once)
    row(p, "clean").get_by_role("button", name="Delete clean…").click()
    confirm = p.get_by_role("group", name="Confirm deleting clean")
    expect(confirm).to_contain_text("clean cannot be deleted now: busy: 'up' is still running")
    confirm.get_by_role("button", name="Try again").click()
    expect(confirm.locator(".delete-go")).to_be_enabled()
    expect(confirm).to_contain_text("Delete config/rigs/clean.yaml?")
    confirm.locator(".delete-go").click()
    expect(row(p, "clean")).to_have_count(0)
    page.close()


def test_the_stage_picker_has_no_delete(browser, bin_served):
    page = Page(browser, bin_served.url + "/#stage")
    p = page.page
    p.locator("button.lock").click()
    p.locator("button.lock").click()
    expect(p.locator(".stage-head select")).to_be_enabled()
    expect(p.locator(".stage-rigs .rig-del")).to_have_count(0)
    page.close()


def test_a_wall_clock_stepped_back_does_not_hold_a_notice(browser, bin_served):
    """#189 review: the notices are timed on the page's monotonic clock and
    looked at again until due, so a wall clock stepped back (NTP) while one
    is shown does not keep it for ever."""
    page = rack(browser, bin_served, clock=True)
    p = page.page
    ask_delete(p, "clean").locator(".delete-go").click()
    said = p.locator(".rig-deleted")
    expect(said).to_contain_text("Deleted clean")
    p.clock.set_system_time(p.evaluate("() => Date.now()") / 1000 - 10 * 60)  # seconds, as pause_clock's
    p.clock.run_for((NOTICE_S + 1) * 1000)
    expect(said).to_have_count(0)
    expect(p.locator(".bin-offers")).to_have_count(0)
    page.close()


def test_the_focus_on_an_undo_that_leaves_goes_to_the_picker(browser, bin_served):
    page = rack(browser, bin_served, clock=True)
    p = page.page
    ask_delete(p, "clean").locator(".delete-go").click()
    expect(undo(p, "clean")).to_be_focused()
    p.clock.run_for((NOTICE_S + 1) * 1000)
    expect(p.locator(".bin-offers")).to_have_count(0)
    expect(p.locator(".rig-list button.rig").first).to_be_focused()
    page.close()


def test_an_undo_kept_through_a_busy_refusal_is_offered_afresh(browser, bin_served):
    """Tried at 14 s and refused busy, the Undo stays offered for its own
    15 s from the refusal, not the 1 s left of the first."""
    page = rack(browser, bin_served, clock=True)
    p = page.page
    calls = {"n": 0}

    def busy_once(route):
        calls["n"] += 1
        if calls["n"] == 1:
            route.fulfill(status=409, content_type="application/json",
                          body='{"ok": false, "status": 409, "reason": "busy: \'up\' is still running", "code": "busy"}')
        else:
            route.continue_()

    p.route("**/rigs/clean/restore", busy_once)
    ask_delete(p, "clean").locator(".delete-go").click()
    expect(undo(p, "clean")).to_be_visible()
    p.clock.run_for((NOTICE_S - 1) * 1000)
    undo(p, "clean").click()
    expect(p.locator(".rig-deleted")).to_contain_text("Not put back: clean")
    p.clock.run_for(5000)
    expect(undo(p, "clean")).to_be_visible()  # offered afresh by the refusal
    p.clock.run_for(NOTICE_S * 1000)
    expect(p.locator(".bin-offers")).to_have_count(0)
    expect(p.locator(".rig-deleted")).to_contain_text("Not put back: clean"), "the refusal itself stays"
    page.close()
