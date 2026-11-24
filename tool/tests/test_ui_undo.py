"""The one Undo (#209), in Chromium against the real agent over the fake.

`Served(save=True, pedals=True)`: `UiRig` for Sushi, `UiSave`'s checkout for
the rig files, the bin and the saves, its machine file with the FBV. Each
test makes a change through the page and takes it back with the one Undo
button, then reads what was put back where it lives (Sushi's value, the
rig file's bytes, config/rigs/), not the page's words alone.

Also the confirms (#209): no confirm stands over what Undo takes back
(Delete, Remove, a revert, a pedal's assignment), and those still asked
(Stop, a restart, the amp's model) are one or two words with Yes and No.

NOT shown: any of it against a live Sushi.
"""

from __future__ import annotations

import re
import time

import pytest

pytest.importorskip("aiohttp")

from test_ui import Page, blank, browser, expect, rigjs  # noqa: E402,F401
from test_ui_tabs import unit  # noqa: E402
from ui_fake import RIG_FILE, Served  # noqa: E402


@pytest.fixture
def served():
    with Served(save=True, pedals=True) as s:
        (s.save.root / "config" / "rigs" / "clean.yaml").write_text(RIG_FILE)
        yield s


def until(check, timeout=8.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if check():
            return
        time.sleep(0.05)
    assert check()


def open_page(browser, served, screen="#rack", locked=False, width=1280, height=900):
    page = Page(browser, served.url + "/" + screen, width=width, height=height)
    p = page.page
    p.evaluate(f"localStorage.setItem('sushi-rig.locked', '{'on' if locked else 'off'}')")
    p.reload()
    p.wait_for_selector(".card" if screen == "#stage" else ".unit")
    return page


def undo_button(p):
    return p.locator("button.undo")


def press_undo(p, label):
    button = undo_button(p)
    expect(button).to_have_attribute("title", f"Undo {label}")
    button.click()
    expect(p.locator(".undo-said")).to_have_text(f"Undone: {label}")


def rig_file(served, name="electric_board"):
    return served.save.root / "config" / "rigs" / f"{name}.yaml"


def test_the_button_undoes_a_knob(browser, served):
    page = open_page(browser, served)
    p = page.page
    expect(undo_button(p)).to_be_disabled()
    expect(undo_button(p)).to_have_attribute("title", "Nothing to undo")
    before = served.rig.info["delay"]["Feedback"]["value"]
    field = p.get_by_role("textbox", name="delay Feedback value", exact=True)
    field.fill("0.9")
    p.keyboard.press("Enter")
    until(lambda: abs(served.rig.info["delay"]["Feedback"]["value"] - 0.9) < 1e-6)
    press_undo(p, "delay Feedback")
    until(lambda: abs(served.rig.info["delay"]["Feedback"]["value"] - before) < 1e-6)
    expect(undo_button(p)).to_be_disabled()
    page.close()


def test_the_button_undoes_a_plugin_removal_and_remove_takes_no_confirm(browser, served):
    page = open_page(browser, served)
    p = page.page
    unit(p, "delay").get_by_role("button", name="delay", exact=True).click()
    unit(p, "delay").get_by_role("button", name="Remove delay", exact=True).click()
    until(lambda: "delay" not in served.rig.chains["board"])
    assert p.locator(".confirm").count() == 0
    press_undo(p, "remove delay")
    until(lambda: "delay" in served.rig.chains["board"])
    page.close()


def test_the_button_undoes_a_rig_delete_and_delete_takes_no_confirm(browser, served):
    page = open_page(browser, served)
    p = page.page
    clean = rig_file(served, "clean")
    p.locator('.rig-row[data-rig="clean"] .rig-del').click()
    until(lambda: not clean.exists())
    assert p.locator(".confirm").count() == 0, "a delete Undo takes back asks nothing"
    expect(p.locator('.rig-row[data-rig="clean"]')).to_have_count(0)
    press_undo(p, "delete clean")
    until(clean.exists)
    assert clean.read_text() == RIG_FILE
    expect(p.locator('.rig-row[data-rig="clean"]')).to_have_count(1)
    page.close()


def test_the_button_undoes_a_save_over_the_file(browser, served):
    page = open_page(browser, served)
    p = page.page
    path = rig_file(served)
    original = path.read_bytes()
    field = p.get_by_role("textbox", name="delay Feedback value", exact=True)
    field.fill("0.9")
    p.keyboard.press("Enter")
    until(lambda: abs(served.rig.info["delay"]["Feedback"]["value"] - 0.9) < 1e-6)
    p.locator("button.save-main").click()
    until(lambda: path.read_bytes() != original)
    press_undo(p, "save electric_board")
    until(lambda: path.read_bytes() == original)
    # The knob is next: the save is taken back, the knob is not.
    expect(undo_button(p)).to_have_attribute("title", "Undo delay Feedback")
    page.close()


def test_the_history_survives_a_reload(browser, served):
    page = open_page(browser, served)
    p = page.page
    p.locator('.rig-row[data-rig="clean"] .rig-del').click()
    until(lambda: not rig_file(served, "clean").exists())
    p.reload()
    p.wait_for_selector(".unit")
    press_undo(p, "delete clean")
    until(rig_file(served, "clean").exists)
    page.close()


def test_locked_the_button_holds_what_the_lock_holds_and_lets_the_rest_through(browser, served):
    """The lock holds a pedal's assignment, and lets a knob and a delete
    (which is not behind the lock on the rack) be undone, as they were done."""
    page = open_page(browser, served, locked=True)
    p = page.page
    # A delete made elsewhere (another page, curl): not behind the lock, so neither is its undo.
    assert p.evaluate("async () => (await fetch('/rigs/clean/delete', {method: 'POST'})).status") == 200
    expect(undo_button(p)).to_have_attribute("title", "Undo delete clean")
    # A pedal assigned elsewhere: locked, it is not undone here.
    assert p.evaluate("""async () => (await fetch('/rig/midi/assign', {method: 'POST',
        headers: {'content-type': 'application/json'},
        body: JSON.stringify({control: 'c', bypass: 'board/delay'})})).status""") == 200
    expect(undo_button(p)).to_be_disabled()
    expect(undo_button(p)).to_have_attribute("title", "Locked: pedal c")
    page.close()
    # The same page unlocked undoes it, then the delete.
    page = open_page(browser, served, locked=False)
    p = page.page
    press_undo(p, "pedal c")
    press_undo(p, "delete clean")
    until(rig_file(served, "clean").exists)
    page.close()


def test_locked_a_knob_is_undone(browser, served):
    page = open_page(browser, served, locked=True)
    p = page.page
    field = p.get_by_role("textbox", name="delay Feedback value", exact=True)
    field.fill("0.9")
    p.keyboard.press("Enter")
    until(lambda: abs(served.rig.info["delay"]["Feedback"]["value"] - 0.9) < 1e-6)
    press_undo(p, "delay Feedback")
    page.close()


def test_locked_a_structural_live_edit_is_held_too(browser, served):
    page = open_page(browser, served, locked=True)
    p = page.page
    assert p.evaluate("""async () => (await fetch('/live/remove', {method: 'POST',
        headers: {'content-type': 'application/json'}, body: JSON.stringify({processor: 'delay'})})).status""") == 200
    expect(undo_button(p)).to_be_disabled()
    expect(undo_button(p)).to_have_attribute("title", "Locked: remove delay")
    page.close()


def test_on_the_stage_the_button_is_there_and_for_live_edits(browser, served):
    page = open_page(browser, served, screen="#stage", width=390, height=844)
    p = page.page
    expect(undo_button(p)).to_be_visible()
    assert p.evaluate("async () => (await fetch('/rigs/clean/delete', {method: 'POST'})).status") == 200
    expect(undo_button(p)).to_be_disabled()
    expect(undo_button(p)).to_have_attribute("title", "Undo delete clean on the rack")
    p.locator('.card[data-unit="delay"] button.bypass').click()
    until(lambda: served.rig.bypassed["delay"] is True)
    press_undo(p, "bypass delay")
    until(lambda: served.rig.bypassed["delay"] is False)
    page.close()


def test_what_cannot_be_undone_says_so_in_the_title_and_the_next_press_goes_on(browser, served):
    page = open_page(browser, served)
    p = page.page
    p.locator('.rig-row[data-rig="clean"] .rig-del').click()
    until(lambda: not rig_file(served, "clean").exists())
    rig_file(served, "clean").write_text("# made since\n")  # a file of that name is there again
    button = undo_button(p)
    expect(button).to_have_attribute("title", "Can't undo: a rig called clean is there again")
    button.click()
    # Which step, on the page: a phone shows no title (#235's review).
    expect(p.locator(".undo-said")).to_have_text("Can't undo: delete clean")
    expect(p.locator(".undo-said")).to_have_attribute("title", re.compile("a rig called clean is there again"))
    expect(button).to_have_attribute("title", "Nothing to undo")
    assert rig_file(served, "clean").read_text() == "# made since\n"
    page.close()


def test_stop_asks_one_word_with_yes_and_no(browser, served):
    page = open_page(browser, served)
    p = page.page
    p.get_by_role("button", name="Stop electric_board").click()
    confirm = p.get_by_role("group", name="Stop?")
    expect(confirm).to_contain_text("Stop?")
    expect(confirm.get_by_role("button")).to_have_text(["Yes", "No"])
    confirm.get_by_role("button", name="No").click()
    expect(p.get_by_role("group", name="Stop?")).to_have_count(0)
    assert all(c[0] != "down" for c in served.lifecycle.calls)
    page.close()


def test_the_old_undo_controls_are_gone(browser, served):
    page = open_page(browser, served)
    p = page.page
    p.locator('.rig-row[data-rig="clean"] .rig-del').click()
    until(lambda: not rig_file(served, "clean").exists())
    expect(p.locator("button.undo")).to_have_count(1)
    assert p.locator(".bin-offers, .undo-delete").count() == 0
    assert p.locator(".changes-list button").count() == 0, "the save column has no Undo of its own"
    page.close()


def test_a_pedal_moved_off_what_it_did_asks_nothing_and_undo_puts_it_back(browser, served):
    import yaml

    from test_ui_midi_assign import open_panel, summary

    def midi():
        return yaml.safe_load(rig_file(served).read_text()).get("midi")

    page = open_page(browser, served)
    p = page.page
    p.wait_for_selector(".pedal-badge")
    open_panel(p, "overdrive").get_by_label("overdrive on/off").select_option("c")
    expect(summary(p, "c")).to_have_text("board/overdrive on/off")
    open_panel(p, "vocal").get_by_label("vocal on/off").select_option("c")
    # Moved at once: no "Move C" or "Keep it" to answer.
    expect(summary(p, "c")).to_have_text("vocal on/off")
    assert p.locator(".pedal-panel .confirm").count() == 0
    assert midi()["controls"] == [{"control": "c", "mute": "vocal"}]
    press_undo(p, "pedal c")
    until(lambda: midi()["controls"] == [{"control": "c", "bypass": "board/overdrive"}])
    expect(summary(p, "c")).to_have_text("board/overdrive on/off")
    page.close()


def test_a_revert_is_one_tap_and_undo_puts_the_file_back(browser, served):
    page = open_page(browser, served)
    p = page.page
    path = rig_file(served)
    field = p.get_by_role("textbox", name="delay Feedback value", exact=True)
    field.fill("0.9")
    p.keyboard.press("Enter")
    until(lambda: abs(served.rig.info["delay"]["Feedback"]["value"] - 0.9) < 1e-6)
    p.locator("button.save-main").click()
    expect(undo_button(p)).to_have_attribute("title", "Undo save electric_board")
    saved = path.read_bytes()
    p.locator("button.revert-open").click()
    p.locator("button.revert-point", has_text="Commit").first.click()
    until(lambda: path.read_bytes() != saved)
    assert p.locator(".confirm").count() == 0, "a revert Undo takes back asks nothing"
    press_undo(p, "revert electric_board")
    until(lambda: path.read_bytes() == saved)
    page.close()


def test_a_restart_asks_one_word_with_yes_and_no(browser, served):
    page = open_page(browser, served)
    p = page.page
    p.get_by_role("button", name="Change vocal's input").click()
    p.get_by_label("vocal input").select_option("keys-in")
    p.get_by_role("button", name="Apply").click()
    p.locator(".rigfile-result").get_by_role("button", name="Restart").click()
    confirm = p.get_by_role("group", name="Restart?")
    expect(confirm.get_by_role("button")).to_have_text(["Yes", "No"])
    calls = len(served.lifecycle.calls)
    confirm.get_by_role("button", name="Yes").click()
    until(lambda: any(c[0] == "restart" for c in served.lifecycle.calls[calls:]))
    page.close()


def test_a_refusal_is_cut_to_what_is_wrong_in_a_few_words(blank):
    cut = rigjs(blank, """
      return [
        R.shortSaid("a rig called hall already exists (config/rigs/hall.yaml)"),
        R.shortSaid("nothing was written: duplicate name 'overdrive' in track guitar; rename one"),
        R.shortSaid("Sushi would not set Feedback (test)"),
        R.shortSaid("the rig is using 86% of its 21.333 ms block, over the 80% a live add may start from"),
        R.shortSaid("nothing was written"),
        R.shortSaid(""),
        R.shortSaid(null),
        // The heading it is shown under, when the words begin with it, is passed over.
        R.shortSaid("Not switched to x: 1 plugin it loads is missing. Nothing was changed", 40, "Not switched to x"),
        R.shortSaid("error: x was not started: 1 plugin missing"),
        R.shortSaid("Not switched to x", 40, "Not switched to x"),
      ];""")
    assert cut == ["a rig called hall already exists", "duplicate name 'overdrive' in track…",
                   "Sushi would not set Feedback", "the rig is using 86% of its 21.333 ms…",
                   "nothing was written", "", "", "1 plugin it loads is missing", "x was not started",
                   "Not switched to x"]


# ------------------------------------------------------------- PR #235's review

def test_the_button_holds_a_switch_back_locked_and_says_which_step_and_why(blank):
    out = rigjs(blank, """
      const next = (extra) => ({ok: true, body: {next: {seq: 7, kind: 'file', label: 'x', can: true, ...extra}}});
      return {
        load: R.undoButton(next({act: 'load', label: 'load crunch'}), {locked: true}),
        revert: R.undoButton(next({act: 'revert', label: 'revert board'}), {locked: true}),
        save: R.undoButton(next({act: 'save', label: 'save board'}), {locked: true}),
        call: R.undoCall({seq: 7}), again: R.undoCall({seq: 7}, {discard: true, restart: true}), none: R.undoCall(null),
        cant: R.undoOutcome({ok: false, status: 409, code: 'cant_undo', reason: "can't undo bypass delay: moved",
                             body: {skipped: {label: 'bypass delay'}}}),
        sushi: R.undoOutcome({ok: false, status: 502, reason: 'Sushi did not answer in 2 s'}),
        late: R.undoOutcome({ok: false, status: 504, reason: 'timed out'}),
        gone: R.undoOutcome({ok: false, status: 0, reason: 'the agent did not answer (x)'}),
        held: R.undoOutcome({ok: false, status: 409, code: 'unsaved', reason: 'board is running with 1 change'}),
        restarts: R.undoOutcome({ok: false, status: 409, code: 'restarts', reason: 'switching board back restarts'}),
        changed: R.undoOutcome({ok: false, status: 409, code: 'changed', reason: 'the step shown is no longer'}),
        other: R.undoOutcome({ok: false, status: 400, reason: 'the body of an undo is {}'}),
      };""")
    assert out["load"] == {"disabled": True, "title": "Locked: load crunch", "cant": False}
    assert out["revert"] == {"disabled": True, "title": "Locked: revert board", "cant": False}
    assert out["save"]["disabled"] is False, "a save is not behind the lock, nor its Undo"
    assert out["call"] == {"path": "/undo", "json": {"restart": False, "seq": 7}}
    assert out["again"] == {"path": "/undo", "json": {"restart": True, "seq": 7, "discard": True}}
    assert out["none"] == {"path": "/undo", "json": {"restart": False}}
    assert out["cant"]["text"] == "Can't undo: bypass delay" and "moved" in out["cant"]["title"]
    assert out["sushi"]["text"] == "Sushi not answering" and out["sushi"]["title"] == "Sushi did not answer in 2 s"
    assert out["late"]["text"] == "Timed out" and out["gone"]["text"] == "Agent not answering"
    assert (out["held"]["ask"], out["held"]["text"]) == ("drop", "Drop changes?")
    assert (out["restarts"]["ask"], out["restarts"]["text"]) == ("restart", "Restart?")
    assert out["changed"]["text"] == "Changed: press again" and out["changed"]["ask"] is None
    assert out["other"]["text"] == "Not undone: the body of an undo is {}"


def test_an_undo_that_would_drop_unsaved_work_asks_drop_changes_first(browser, served):
    """A revert, then a bypass that cannot be undone (moved elsewhere) and
    still plays: the revert's Undo would drop it, so the page asks, and only
    Yes puts the file back."""
    path = rig_file(served)
    page = open_page(browser, served)
    p = page.page
    field = p.get_by_role("textbox", name="delay Feedback value", exact=True)
    field.fill("0.9")
    p.keyboard.press("Enter")
    until(lambda: abs(served.rig.info["delay"]["Feedback"]["value"] - 0.9) < 1e-6)
    p.locator("button.save-main").click()
    expect(undo_button(p)).to_have_attribute("title", "Undo save electric_board")
    saved = path.read_bytes()
    p.locator("button.revert-open").click()
    p.locator("button.revert-point", has_text="Commit").first.click()
    until(lambda: path.read_bytes() != saved)
    reverted = path.read_bytes()
    # The revert's answer is in, so the agent is no longer busy with it.
    expect(undo_button(p)).to_have_attribute("title", "Undo revert electric_board")
    assert p.evaluate("""async () => (await fetch('/live/bypass', {method: 'POST',
        headers: {'content-type': 'application/json'},
        body: JSON.stringify({processor: 'delay', bypassed: true})})).status""") == 200
    served.rig.bypassed["delay"] = False  # moved since from elsewhere: not undoable
    expect(undo_button(p)).to_have_attribute("title", "Undo bypass delay")
    undo_button(p).click()
    expect(p.locator(".undo-said")).to_have_text("Can't undo: bypass delay")
    served.rig.bypassed["delay"] = True  # and it plays, unsaved
    expect(undo_button(p)).to_have_attribute("title", "Undo revert electric_board")
    undo_button(p).click()
    ask = p.get_by_role("group", name="Drop changes?")
    expect(ask.get_by_role("button")).to_have_text(["Yes", "No"])
    assert path.read_bytes() == reverted, "nothing put back while it is asked"
    ask.get_by_role("button", name="Yes").click()
    expect(p.locator(".undo-said")).to_have_text("Undone: revert electric_board")
    until(lambda: path.read_bytes() == saved)
    page.close()


def test_a_revert_point_that_would_restart_sushi_asks_restart_first(browser, served):
    """A revert is one tap, unless it would restart Sushi, which cuts the
    sound: then "Restart?", and only Yes writes the file."""
    path = rig_file(served)
    page = open_page(browser, served)
    p = page.page
    assert p.evaluate("""async () => (await fetch('/rigs/electric_board/channels/add', {method: 'POST',
        headers: {'content-type': 'application/json'},
        body: JSON.stringify({name: 'keys', input: 'keys-in', width: 'mono'})})).status""") == 200
    assert p.evaluate("async () => (await fetch('/rig/restart', {method: 'POST'})).status") == 200
    with_keys = path.read_bytes()
    p.locator("button.revert-open").click()
    p.locator("button.revert-point", has_text="Commit").first.click()
    ask = p.get_by_role("group", name="Restart?")
    expect(ask.get_by_role("button")).to_have_text(["Yes", "No"])
    assert path.read_bytes() == with_keys, "nothing written while it is asked"
    ask.get_by_role("button", name="Yes").click()
    until(lambda: path.read_bytes() != with_keys)
    expect(p.get_by_role("group", name="Restart?")).to_have_count(0)
    page.close()
