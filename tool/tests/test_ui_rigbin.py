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
agent can see of either. Nothing here is the rig machine, a phone or a pedal:
the Program Change numbers are shown as the agent counts them, and no
Program Change is sent.
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


def rack(browser, served, width=1280):
    page = Page(browser, served.url + "/#rack", width=width)
    page.page.wait_for_selector(".rig-row")
    return page


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


def test_the_undos_kept_are_a_few_per_agent_and_only_well_formed_ones(blank):
    out = rigjs(blank, """
      const o = (rig, s) => ({rig, entry: `${rig}-20261116T1015${s}Z.yaml`, file: `config/rigs/.deleted/${rig}-20261116T1015${s}Z.yaml`});
      let offers = [];
      for (const [rig, s] of [['a', '01'], ['b', '02'], ['c', '03'], ['d', '04'], ['e', '05'], ['f', '06']]) offers = R.addOffer(offers, o(rig, s));
      let text = R.offersToRemember(null, 'agent-one', offers);
      text = R.offersToRemember(text, 'agent-two', [o('z', '09')]);
      const bad = JSON.stringify({'agent-one': [{rig: '../x', entry: '../x-20261116T101501Z.yaml', file: 'x'},
        {rig: 'a', entry: 'b-20261116T101501Z.yaml', file: 'x'}, {rig: 'a', entry: 'a.yaml', file: 'x'}, o('ok', '07')]});
      return {
        one: R.rememberedOffers(text, 'agent-one').map((x) => x.rig),
        two: R.rememberedOffers(text, 'agent-two').map((x) => x.rig),
        other: R.rememberedOffers(text, 'agent-three'), noAgent: R.rememberedOffers(text, null),
        junk: [R.rememberedOffers('not json', 'agent-one'), R.rememberedOffers('[1]', 'agent-one')],
        bad: R.rememberedOffers(bad, 'agent-one').map((x) => x.rig),
        dropped: R.rememberedOffers(R.offersToRemember(text, 'agent-two', []), 'agent-two'),
        kept: R.rememberedOffers(R.offersToRemember(text, 'agent-two', []), 'agent-one').length,
      };""")
    assert out["one"] == ["f", "e", "d", "c", "b"], "newest first, five at most: deleting B does not strand A"
    assert out["two"] == ["z"] and out["other"] == [] and out["noAgent"] == [], "another rig's agent sees none"
    assert out["junk"] == [[], []]
    assert out["bad"] == ["ok"]
    assert out["dropped"] == [] and out["kept"] == 5


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


def test_undo_is_still_offered_after_a_reload_while_its_file_is_in_the_bin(browser, bin_served):
    page = rack(browser, bin_served)
    p = page.page
    ask_delete(p, "clean").locator(".delete-go").click()
    ask_delete(p, LOOSE).locator(".delete-go").click()
    expect(p.locator(".bin-offers li")).to_have_count(2)
    # Meanwhile, by hand on the rig: one bin file removed.
    [gone] = [e for e in binned(bin_served) if e.startswith(LOOSE)]
    (rigs_dir(bin_served) / ".deleted" / gone).unlink()
    p.reload()
    p.wait_for_selector(".rig-row")
    expect(p.locator(".bin-offers li")).to_have_count(1)
    expect(undo(p, "clean")).to_be_visible()
    stored = p.evaluate("() => JSON.parse(localStorage.getItem('sushi-rig.binned'))")
    assert [[o["rig"] for o in kept] for kept in stored.values()] == [["clean"]], "the gone one let go in storage too"
    undo(p, "clean").click()
    expect(row(p, "clean")).to_have_count(1)
    assert (rigs_dir(bin_served) / "clean.yaml").is_file()
    p.reload()
    p.wait_for_selector(".rig-row")
    expect(p.locator(".bin-offers")).to_have_count(0), "a done Undo is not offered again"
    page.close()


def test_an_undo_kept_by_another_agent_at_this_address_is_not_offered(browser, bin_served):
    page = rack(browser, bin_served)
    p = page.page
    p.evaluate("""() => localStorage.setItem('sushi-rig.binned', JSON.stringify({'another-rig-machine': [
      {rig: 'clean', entry: 'clean-20261116T101501Z.yaml', file: 'config/rigs/.deleted/clean-20261116T101501Z.yaml'}]}))""")
    p.reload()
    p.wait_for_selector(".rig-row")
    p.wait_for_function("() => performance.getEntriesByType('resource').some((r) => r.name.endsWith('/rigs/bin'))")
    expect(p.locator(".bin-offers")).to_have_count(0)
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
    expect(p.locator(".stage-rigs select")).to_be_enabled()
    expect(p.locator(".stage-rigs .rig-del")).to_have_count(0)
    page.close()
