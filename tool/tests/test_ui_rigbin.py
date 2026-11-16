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


def ask_delete(p, name):
    row(p, name).get_by_role("button", name=f"Delete {name}…").click()
    confirm = p.get_by_role("group", name=f"Confirm deleting {name}")
    expect(confirm.locator(".delete-go")).to_be_enabled()
    return confirm


# --------------------------------------------------------------------------- #
# The decisions, pure (rig.js)
# --------------------------------------------------------------------------- #

def test_the_running_rigs_row_is_the_one_whose_delete_is_off_and_says_why(blank):
    out = rigjs(blank, """
      const health = (rig) => ({ok: true, body: {rig, agent: {}}});
      const up = health({running: true, config_name: 'electric_board', rig: 'config/rigs/electric_board.yaml'});
      const v1 = health({running: true, config_name: 'electric_board', rig: null});
      const down = health({running: false});
      return {
        up: ['electric_board', 'clean'].map((n) => R.deleteAction(n, up)),
        v1: R.deleteAction('electric_board', v1).disabled,
        down: R.deleteAction('electric_board', down).disabled,
        busy: R.deleteAction('clean', up, true).disabled,
        noAgent: R.deleteAction('clean', {ok: false}).disabled,
        file: [R.runningRigFile(up), R.runningRigFile(v1), R.runningRigFile(down)],
      };""")
    running, other = out["up"]
    assert running["disabled"] is True and "electric_board is the running rig" in running["why"]
    assert "switch to another rig, or stop it, first" in running["why"]
    assert other == {"name": "clean", "disabled": False, "label": "Delete", "aria": "Delete clean…", "why": None}
    assert out["v1"] is False, "a v1 run holds no rig file, so none is the running rig's"
    assert out["down"] is False and out["noAgent"] is False
    assert out["busy"] is True, "one delete at a time"
    assert out["file"] == ["electric_board", None, None]


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
    assert out["refused"]["refused"] == "electric_board is the running rig" and out["refused"]["code"] == "running"


def test_undo_is_offered_again_only_when_trying_again_could_work(blank):
    out = rigjs(blank, """
      const no = (code) => ({ok: false, status: 409, reason: 'r', code});
      return ['taken', 'gone', 'entry', 'name', 'busy'].map((c) => R.restoreOutcome('clean', no(c)).undo)
        .concat([R.restoreOutcome('clean', {ok: false, status: 0, reason: 'the agent did not answer'}).undo]);""")
    assert out == [True, False, False, False, True, True]


def test_only_a_rig_and_one_of_its_own_bin_entries_comes_back_from_storage(blank):
    out = rigjs(blank, """
      const kept = R.binnedToRemember({rig: 'clean', entry: 'clean-20261116T101502Z.yaml', file: 'config/rigs/.deleted/clean-20261116T101502Z.yaml'});
      const of = (o) => R.rememberedBinned(JSON.stringify(o));
      return [R.rememberedBinned(kept), R.rememberedBinned(null), R.rememberedBinned('not json'),
              of({rig: 'clean', entry: 'other-20261116T101502Z.yaml', file: 'x'}),
              of({rig: '../x', entry: '../x-20261116T101502Z.yaml', file: 'x'}),
              of({rig: 'clean', entry: 'clean.yaml', file: 'x'}),
              of({rig: 'clean', entry: 'clean-20261116T101502Z-2.yaml', file: 'x'}) !== null];""")
    assert out[0] == {"rig": "clean", "entry": "clean-20261116T101502Z.yaml",
                      "file": "config/rigs/.deleted/clean-20261116T101502Z.yaml"}
    assert out[1:6] == [None] * 5
    assert out[6] is True


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
    expect(p.locator(".undo-delete")).to_be_focused()
    p.locator(".undo-delete").click()
    expect(said).to_contain_text("Put back clean")
    expect(said).to_contain_text("electric-delay moves from program 1 to 2")
    expect(row(p, "clean")).to_have_count(1)
    assert (rigs_dir(bin_served) / "clean.yaml").read_bytes() == before
    assert binned(bin_served) == []
    expect(p.locator(".undo-delete")).to_have_count(0)
    page.close()


def test_undo_is_still_offered_after_a_reload(browser, bin_served):
    page = rack(browser, bin_served)
    p = page.page
    ask_delete(p, "clean").locator(".delete-go").click()
    expect(p.locator(".undo-delete")).to_be_visible()
    p.reload()
    p.wait_for_selector(".rig-row")
    expect(p.locator(".rig-deleted")).to_contain_text("Deleted clean")
    expect(row(p, "clean")).to_have_count(0)
    p.locator(".undo-delete").click()
    expect(row(p, "clean")).to_have_count(1)
    assert (rigs_dir(bin_served) / "clean.yaml").is_file()
    p.reload()
    p.wait_for_selector(".rig-row")
    expect(p.locator(".rig-deleted")).to_have_count(0), "a done Undo is not offered again"
    page.close()


def test_undo_refused_on_a_clash_says_so_and_stays_offered(browser, bin_served):
    page = rack(browser, bin_served)
    p = page.page
    ask_delete(p, "clean").locator(".delete-go").click()
    expect(p.locator(".undo-delete")).to_be_visible()
    (rigs_dir(bin_served) / "clean.yaml").write_text("made since\n")
    p.locator(".undo-delete").click()
    said = p.locator(".rig-deleted")
    expect(said).to_contain_text("Not put back: clean")
    expect(said).to_contain_text("is there again")
    expect(p.locator(".undo-delete")).to_be_visible()
    assert (rigs_dir(bin_served) / "clean.yaml").read_text() == "made since\n"
    assert len(binned(bin_served)) == 1
    (rigs_dir(bin_served) / "clean.yaml").unlink()
    p.locator(".undo-delete").click()
    expect(said).to_contain_text("Put back clean")
    assert (rigs_dir(bin_served) / "clean.yaml").read_text() == RIG_FILE
    page.close()


def test_undo_refused_when_the_bin_file_is_gone_says_so_and_offers_it_no_more(browser, bin_served):
    page = rack(browser, bin_served)
    p = page.page
    ask_delete(p, "clean").locator(".delete-go").click()
    expect(p.locator(".undo-delete")).to_be_visible()
    [entry] = binned(bin_served)
    (rigs_dir(bin_served) / ".deleted" / entry).unlink()
    p.locator(".undo-delete").click()
    said = p.locator(".rig-deleted")
    expect(said).to_contain_text("Not put back: clean")
    expect(said).to_contain_text("is not there any more")
    expect(p.locator(".undo-delete")).to_have_count(0)
    said.get_by_role("button", name="Dismiss").click()
    expect(said).to_have_count(0)
    page.close()


def test_the_stage_picker_has_no_delete(browser, bin_served):
    page = Page(browser, bin_served.url + "/#stage")
    p = page.page
    p.locator("button.lock").click()
    p.locator("button.lock").click()
    p.wait_for_selector(".stage-rigs .rig")
    expect(p.locator(".stage-rigs .rig-del")).to_have_count(0)
    page.close()
