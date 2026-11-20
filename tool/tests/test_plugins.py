"""Assembling `plugins/`: the decisions are pure and pinned here; the lilv and
filesystem parts run against small synthetic LV2 bundles written to a temp dir.

The synthetic bundles are real as far as lilv is concerned — a `manifest.ttl`
declaring a `lv2:Plugin` — so discovery and the self-containment check are
exercised through the actual library, not a mock of it. They have no binary,
which lilv does not need in order to list a plugin.
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

from sushi_rig.amp import NAM_URI
from sushi_rig.plugins import (
    ARCHIVE_MANIFEST,
    Plan,
    PluginsError,
    add,
    assemble,
    check,
    config_uris,
    copy_bundles,
    decide,
    default_sources,
    describe_failure,
    discover,
    Preflight,
    preflight,
    preflight_refusal,
    read_manifest,
)

needs_lilv = pytest.mark.skipif(
    importlib.util.find_spec("lilv") is None,
    reason="lilv is a system package (python3-lilv)",
)


@pytest.fixture(autouse=True)
def lv2_system(tmp_path_factory, monkeypatch):
    """The system's LV2 folders, as this file's tests see them: empty folders
    of their own for /usr/lib, /usr/local/lib and the home folder, and the
    triplet x86_64-linux-gnu, so no test reads or depends on what this
    machine has installed. A test lays bundles out under them as it needs."""
    from sushi_rig import plugins

    # Not under `tmp_path`, which some tests list.
    system = tmp_path_factory.mktemp("lv2-system")
    for folder in ("usr/lib", "usr/local/lib", "home"):
        (system / folder).mkdir(parents=True)
    monkeypatch.setattr(plugins, "USR_LIB", system / "usr" / "lib")
    monkeypatch.setattr(plugins, "USR_LOCAL_LIB", system / "usr" / "local" / "lib")
    monkeypatch.setattr(plugins, "_multiarch", lambda: "x86_64-linux-gnu")
    monkeypatch.setenv("HOME", str(system / "home"))
    return system

SYS = Path("/usr/lib/lv2")
A, B, C = "urn:t:a", "urn:t:b", "urn:t:c"


# --- reading the inputs -----------------------------------------------------


def test_manifest_ignores_blanks_and_comments():
    assert read_manifest("urn:t:a\n\n# old\n  urn:t:b  \n") == [A, B]


def test_config_uris_collects_every_track_and_skips_uriless_plugins():
    config = {
        "tracks": [
            {"plugins": [{"uri": A}, {"name": "internal", "type": "internal"}]},
            {"plugins": [{"uri": B}, {"uri": A}]},
        ]
    }
    assert config_uris(config) == {A, B}


def test_config_uris_of_a_config_with_no_tracks_is_empty():
    assert config_uris({}) == set()


def test_config_uris_includes_the_amp_which_lives_outside_tracks():
    """The amp is an LV2 plugin Carla hosts, not Sushi, so it sits in the
    top-level `_amp` section with a short name and no `uri` key. Walking
    `tracks` missed it twice over, and the manifest did not list it either —
    so the one plugin no distro packages was the one `check` did not check,
    and a `plugins/` with no amp in it reported every URI resolving."""
    config = {
        "tracks": [{"plugins": [{"uri": A}]}],
        "_amp": {"plugin": "nam", "model": "amp/models/Neve 2.nam"},
    }
    assert config_uris(config) == {A, NAM_URI}


def test_config_uris_leaves_a_config_with_no_amp_alone():
    """`electric_board.json` carries no `_amp`, and must not be made to
    require one."""
    assert config_uris({"tracks": [{"plugins": [{"uri": A}]}]}) == {A}


def test_an_unknown_amp_plugin_is_loud_rather_than_skipped():
    """Quietly not requiring an amp the config asks for is the exact failure
    this was widened to prevent, so an unrecognised name stops the command."""
    config = {"_amp": {"plugin": "some-future-amp", "model": "x.nam"}}
    with pytest.raises(PluginsError, match="some-future-amp"):
        config_uris(config)


def test_an_amp_section_with_no_plugin_key_says_which_config():
    """`check` walks every config, so an error reading only "None" leaves you
    grepping for the file it meant. It also has to name the key that is
    missing, rather than reporting it as an unknown plugin."""
    with pytest.raises(PluginsError, match="electric-clean.json"):
        config_uris({"_amp": {"model": "x.nam"}}, "config/electric-clean.json")


def test_the_missing_key_is_reported_as_missing_not_as_unknown():
    with pytest.raises(PluginsError, match="names no plugin"):
        config_uris({"_amp": {"model": "x.nam"}})


def test_the_shipped_manifest_lists_the_amp():
    """A repo-level invariant, not a unit test: if this URI falls out of
    `plugin-manifest.txt` again, `plugins check` silently stops protecting the
    one component `plugins assemble` cannot rebuild."""
    manifest = Path(__file__).resolve().parents[2] / "plugin-manifest.txt"
    assert NAM_URI in read_manifest(manifest.read_text())


# --- the decision -----------------------------------------------------------


def test_a_bundle_providing_several_uris_is_copied_once():
    plan = decide([A, B], set(), {A: SYS / "m.lv2", B: SYS / "m.lv2"})
    assert plan.bundles == [SYS / "m.lv2"]


def test_only_bundles_that_something_requires_are_copied():
    providers = {A: SYS / "a.lv2", B: SYS / "b.lv2", C: SYS / "unused.lv2"}
    plan = decide([A, B], set(), providers)
    assert plan.bundles == [SYS / "a.lv2", SYS / "b.lv2"]


def test_every_missing_uri_is_reported_not_just_the_first():
    plan = decide([A, B, C], set(), {A: SYS / "a.lv2"})
    assert plan.missing == [B, C]
    assert not plan.ok


def test_a_plugin_used_by_a_config_is_required_even_if_the_manifest_omits_it():
    """The real manifest omits four URIs the configs load. Assembling from the
    manifest alone would pass its own check and then fail on the first config."""
    plan = decide([A], {A, B}, {A: SYS / "a.lv2"})
    assert plan.missing == [B]
    assert plan.undeclared == [B]


def test_used_and_provided_but_undeclared_is_still_copied_and_still_flagged():
    plan = decide([A], {B}, {A: SYS / "a.lv2", B: SYS / "b.lv2"})
    assert plan.ok
    assert plan.bundles == [SYS / "a.lv2", SYS / "b.lv2"]
    assert plan.undeclared == [B]


def test_a_waived_missing_uri_is_not_a_failure_but_is_reported():
    plan = decide([A, B], set(), {A: SYS / "a.lv2"}, waived={B})
    assert plan.ok
    assert plan.missing == []
    assert plan.waived == [B]


def test_a_waiver_does_not_hide_a_different_missing_uri():
    plan = decide([A, B, C], set(), {A: SYS / "a.lv2"}, waived={B})
    assert plan.missing == [C]


def test_a_waiver_for_a_uri_that_is_provided_is_harmless():
    plan = decide([A], set(), {A: SYS / "a.lv2"}, waived={A})
    assert plan.ok
    assert plan.waived == []


def test_a_waiver_for_something_the_rig_does_not_require_is_an_error():
    """So a waiver cannot outlive the reason for it, or hide a typo'd URI that
    leaves the real missing one failing for no visible reason."""
    with pytest.raises(PluginsError, match="urn:t:typo"):
        decide([A], set(), {A: SYS / "a.lv2"}, waived={"urn:t:typo"})


def test_two_different_bundles_with_one_name_collide():
    """`plugins/` is flat. Copying one over the other would silently pick a winner."""
    plan = decide(
        [A, B], set(), {A: Path("/x/p.lv2"), B: Path("/y/p.lv2")}
    )
    assert plan.collisions == {"p.lv2": [Path("/x/p.lv2"), Path("/y/p.lv2")]}
    assert not plan.ok


def test_nothing_required_is_trivially_ok():
    assert decide([], set(), {}) == Plan()


def test_failure_message_lists_every_missing_uri_and_every_collision():
    plan = decide(
        [A, B, C], set(), {A: Path("/x/p.lv2"), B: Path("/y/p.lv2")}
    )
    message = describe_failure(plan, "somewhere")
    assert "somewhere" in message
    assert C in message
    assert "/x/p.lv2" in message and "/y/p.lv2" in message


# --- copying ---------------------------------------------------------------


def test_copy_makes_flat_bundles_and_an_archive_with_a_manifest(tmp_path):
    src = tmp_path / "src" / "a.lv2"
    src.mkdir(parents=True)
    (src / "manifest.ttl").write_text("x")
    to = tmp_path / "plugins"
    copy_bundles([src], to)
    assert (to / "a.lv2" / "manifest.ttl").read_text() == "x"
    assert (to / "archive" / "manifest.ttl").read_text() == ARCHIVE_MANIFEST


def test_archive_manifest_is_not_zero_bytes():
    """A zero-byte manifest.ttl makes lilv (serd 0.32) log an error on every
    startup — found by running it; the README of the time said empty is fine
    (docs/reference/plugins.md now says why it is a comment)."""
    assert ARCHIVE_MANIFEST.strip().startswith("#")


def test_copy_keeps_symlinks_as_links(tmp_path):
    src = tmp_path / "src" / "a.lv2"
    src.mkdir(parents=True)
    (src / "real.so").write_text("lib")
    (src / "link.so").symlink_to("real.so")
    copy_bundles([src], tmp_path / "plugins")
    assert (tmp_path / "plugins" / "a.lv2" / "link.so").is_symlink()


def test_copy_points_a_relative_link_out_of_its_bundle_at_what_it_led_to(tmp_path):
    """Ubuntu's calf.lv2/calf.so is `../../x86_64-linux-gnu/calf/calf.so`;
    kept relative in plugins/, it leads nowhere (#177). A link inside the
    bundle stays relative, and so means the copy's own file."""
    lib = tmp_path / "usr" / "lib" / "x86_64-linux-gnu" / "calf"
    lib.mkdir(parents=True)
    (lib / "calf.so").write_text("the library")
    src = tmp_path / "usr" / "lib" / "lv2" / "calf.lv2"
    src.mkdir(parents=True)
    (src / "calf.so").symlink_to("../../x86_64-linux-gnu/calf/calf.so")
    (src / "real.ttl").write_text("turtle")
    (src / "inside.ttl").symlink_to("real.ttl")
    copy_bundles([src], tmp_path / "plugins")
    copy = tmp_path / "plugins" / "calf.lv2"
    assert (copy / "calf.so").read_text() == "the library"
    assert os.readlink(copy / "calf.so") == str((lib / "calf.so").resolve())
    assert os.readlink(copy / "inside.ttl") == "real.ttl"


def test_copy_keeps_a_link_into_a_sibling_bundle_it_copies_inside_the_copy(tmp_path):
    """`a.lv2/lib.so -> ../b.lv2/lib.so` with b.lv2 copied too means the copy
    of b, not the source tree's (review of PR #186)."""
    lv2 = tmp_path / "usr" / "lib" / "lv2"
    a, b = lv2 / "a.lv2", lv2 / "b.lv2"
    a.mkdir(parents=True)
    b.mkdir()
    (b / "lib.so").write_text("b's")
    (a / "lib.so").symlink_to("../b.lv2/lib.so")
    copy_bundles([a, b], tmp_path / "plugins")
    link = tmp_path / "plugins" / "a.lv2" / "lib.so"
    assert os.readlink(link) == "../b.lv2/lib.so"
    assert link.resolve() == (tmp_path / "plugins" / "b.lv2" / "lib.so").resolve()


def test_copy_points_a_link_into_a_bundle_it_does_not_copy_at_the_source(tmp_path):
    lv2 = tmp_path / "usr" / "lib" / "lv2"
    a, b = lv2 / "a.lv2", lv2 / "b.lv2"
    a.mkdir(parents=True)
    b.mkdir()
    (b / "lib.so").write_text("b's")
    (a / "lib.so").symlink_to("../b.lv2/lib.so")
    copy_bundles([a], tmp_path / "plugins")
    assert os.readlink(tmp_path / "plugins" / "a.lv2" / "lib.so") == str((b / "lib.so").resolve())


def test_copy_refuses_a_link_that_leads_nowhere_naming_the_bundle_and_the_link(tmp_path):
    a = tmp_path / "usr" / "lib" / "lv2" / "a.lv2"
    a.mkdir(parents=True)
    (a / "x.so").symlink_to("../../x86_64-linux-gnu/missing.so")
    with pytest.raises(PluginsError) as err:
        copy_bundles([a], tmp_path / "plugins")
    assert str(a) in str(err.value) and "x.so" in str(err.value) and "leads to nothing" in str(err.value)


def test_copy_refuses_a_link_loop_naming_the_bundle_and_the_link(tmp_path):
    a = tmp_path / "usr" / "lib" / "lv2" / "a.lv2"
    a.mkdir(parents=True)
    (a / "p").symlink_to("q")
    (a / "q").symlink_to("p")
    with pytest.raises(PluginsError) as err:
        copy_bundles([a], tmp_path / "plugins")
    assert str(a) in str(err.value) and "loop" in str(err.value)


def test_a_loop_is_refused_as_python_3_12_reports_it_too(tmp_path, monkeypatch):
    """3.12's `Path.resolve` raises RuntimeError on a loop (3.13 OSError);
    CI and the rig run 3.12, this suite may not."""
    a = tmp_path / "a.lv2"
    a.mkdir()
    (a / "p").symlink_to("q")

    resolve = Path.resolve

    def loops(self, strict=False):
        if strict:  # how the links are resolved; the bundles themselves are not links
            raise RuntimeError(f"Symlink loop from {self!r}")
        return resolve(self)

    monkeypatch.setattr(Path, "resolve", loops)
    with pytest.raises(PluginsError, match="loop"):
        copy_bundles([a], tmp_path / "plugins")


# --- against real lilv, on synthetic bundles -------------------------------


def _bundle(root: Path, name: str, *uris: str) -> Path:
    bundle = root / f"{name}.lv2"
    bundle.mkdir(parents=True)
    declarations = "".join(
        f"<{u}> a lv2:Plugin ; lv2:binary <{name}.so> ; rdfs:seeAlso <{name}.ttl> .\n"
        for u in uris
    )
    (bundle / "manifest.ttl").write_text(
        "@prefix lv2: <http://lv2plug.in/ns/lv2core#> .\n"
        "@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .\n" + declarations
    )
    # Not empty: `check` reads each plugin's data for its lv2:binary, and lilv
    # (serd 0.32) logs "Error loading file" for a zero-byte one.
    (bundle / f"{name}.ttl").write_text("# nothing more to say\n")
    # The binary its lv2:binary names: `check` requires the file (#177).
    (bundle / f"{name}.so").write_text("")
    return bundle


@pytest.fixture
def system(tmp_path):
    """A fake /usr/lib/lv2: two plugins in one bundle, one elsewhere, and one
    nothing requires."""
    root = tmp_path / "system"
    _bundle(root, "ab", A, B)
    _bundle(root, "c", C)
    _bundle(root, "unused", "urn:t:unused")
    return root


@needs_lilv
def test_discover_maps_each_uri_to_its_bundle(system):
    found = discover([system])
    assert found[A] == found[B] == system / "ab.lv2"
    assert found[C] == system / "c.lv2"


@needs_lilv
def test_discover_rejects_a_directory_that_does_not_exist(tmp_path):
    with pytest.raises(PluginsError, match="no such LV2 directory"):
        discover([tmp_path / "nope"])


@needs_lilv
def test_assemble_copies_exactly_the_needed_bundles_and_check_passes(system, tmp_path):
    to = tmp_path / "plugins"
    plan = assemble([A, C], set(), [system], to)
    assert plan.ok
    assert sorted(p.name for p in to.iterdir()) == ["ab.lv2", "archive", "c.lv2"]
    assert check([A, B, C], set(), to).missing == []


@needs_lilv
def test_check_sees_only_the_target_not_the_system(system, tmp_path, monkeypatch):
    """The whole point: with the system tree on the ambient LV2_PATH, a bundle
    missing from plugins/ must still be reported missing."""
    to = tmp_path / "plugins"
    assemble([A], set(), [system], to)
    monkeypatch.setenv("LV2_PATH", str(system))
    with pytest.raises(PluginsError) as err:
        check([A, C], set(), to)
    assert C in str(err.value)


@needs_lilv
def test_check_restores_the_callers_lv2_path(system, tmp_path, monkeypatch):
    to = tmp_path / "plugins"
    assemble([A], set(), [system], to)
    monkeypatch.setenv("LV2_PATH", "/somewhere/else")
    check([A], set(), to)
    import os

    assert os.environ["LV2_PATH"] == "/somewhere/else"


@needs_lilv
def test_check_lists_every_missing_uri(system, tmp_path):
    to = tmp_path / "plugins"
    assemble([A], set(), [system], to)
    with pytest.raises(PluginsError) as err:
        check([A, B, C, "urn:t:gone"], set(), to)
    # A and B share a bundle, so only C and the invented one are missing.
    assert C in str(err.value) and "urn:t:gone" in str(err.value)
    assert B not in str(err.value)


@needs_lilv
def test_check_fails_on_a_folder_that_does_not_exist(tmp_path):
    with pytest.raises(PluginsError, match="does not exist"):
        check([A], set(), tmp_path / "plugins")


@needs_lilv
def test_assemble_copies_nothing_when_a_uri_is_missing(system, tmp_path):
    to = tmp_path / "plugins"
    with pytest.raises(PluginsError, match="urn:t:gone"):
        assemble([A, "urn:t:gone"], set(), [system], to)
    assert not to.exists()


@needs_lilv
def test_assemble_refuses_a_non_empty_target_unless_told_to_replace(system, tmp_path):
    to = tmp_path / "plugins"
    to.mkdir()
    (to / "mine.txt").write_text("hand-placed")
    with pytest.raises(PluginsError, match="--replace"):
        assemble([A], set(), [system], to)
    assert (to / "mine.txt").exists()

    assemble([A], set(), [system], to, replace=True)
    assert not (to / "mine.txt").exists()
    assert (to / "ab.lv2").is_dir()


@needs_lilv
def test_assemble_leaves_plugins_as_it_was_when_a_copy_fails(system, tmp_path):
    """The copy is built beside plugins/ and renamed in only when whole, so a
    link that leads nowhere fails it with plugins/ untouched, --replace or
    not, and nothing of the attempt left (review of PR #186)."""
    to = tmp_path / "plugins"
    assemble([A, C], set(), [system], to)
    before = sorted(p.name for p in to.iterdir())
    (system / "c.lv2" / "lib.so").symlink_to("../../nowhere.so")
    with pytest.raises(PluginsError, match="leads to nothing"):
        assemble([A, C], set(), [system], to, replace=True)
    assert sorted(p.name for p in to.iterdir()) == before
    assert sorted(p.name for p in tmp_path.iterdir()) == ["plugins", "system"]


@needs_lilv
def test_a_swap_that_fails_between_its_two_renames_puts_plugins_back(system, tmp_path, monkeypatch):
    """The old plugins/ is renamed aside, then the new one into place: a
    failure of the second puts the first back (review of PR #186)."""
    to = tmp_path / "plugins"
    assemble([A], set(), [system], to)
    (to / "mine.txt").write_text("the old plugins/")
    rename = Path.rename

    def fails_into_place(self, target):
        if Path(target) == to and self.name == ".plugins.assembling":
            raise OSError("the power went")
        return rename(self, target)

    monkeypatch.setattr(Path, "rename", fails_into_place)
    with pytest.raises(OSError, match="power"):
        assemble([A, C], set(), [system], to, replace=True)
    assert (to / "mine.txt").read_text() == "the old plugins/"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["plugins", "system"]


@needs_lilv
def test_a_run_that_died_between_the_renames_gets_plugins_back_and_deletes_nothing(system, tmp_path):
    """What a kill -9 between the two renames leaves: no plugins/, the old
    one as .plugins.previous and a whole new copy as .plugins.assembling.
    The next run puts the old one back first: without --replace it then
    refuses, as for any plugins/ that is there, saying what it did; with it,
    it rebuilds (review of PR #186)."""
    to = tmp_path / "plugins"
    previous = tmp_path / ".plugins.previous"
    assemble([A], set(), [system], previous)
    (previous / "mine.txt").write_text("the only copy of the old plugins/")
    assemble([A, C], set(), [system], tmp_path / ".plugins.assembling")
    with pytest.raises(PluginsError, match="put back"):
        assemble([A, C], set(), [system], to)
    assert (to / "mine.txt").read_text() == "the only copy of the old plugins/"
    assert not previous.exists()
    assemble([A, C], set(), [system], to, replace=True)
    assert sorted(p.name for p in to.iterdir()) == ["ab.lv2", "archive", "c.lv2"]
    assert sorted(p.name for p in tmp_path.iterdir()) == ["plugins", "system"]


@needs_lilv
def test_add_leaves_a_bundle_that_is_a_link_to_elsewhere_alone(system, tmp_path):
    """plugins/nam.lv2 may be a link to the amp built in /opt: it is in
    plugins/, and add must not take it for one to add and refuse it as a
    clash with itself (review of PR #186)."""
    to = tmp_path / "plugins"
    assemble([A], set(), [system], to)
    elsewhere = tmp_path / "opt"
    _bundle(elsewhere, "nam", "urn:t:nam")
    (to / "nam.lv2").symlink_to(elsewhere / "nam.lv2")
    plan = add([A, C, "urn:t:nam"], set(), [system], to)
    assert [b.name for b in plan.bundles] == ["c.lv2"]
    assert (to / "nam.lv2").is_symlink()
    check([A, C, "urn:t:nam"], set(), to)


@needs_lilv
def test_check_fails_on_a_link_that_leads_nowhere_but_not_in_the_archive(system, tmp_path):
    to = tmp_path / "plugins"
    assemble([A], set(), [system], to)
    (to / "archive" / "parked.so").symlink_to("../../gone.so")
    check([A], set(), to)
    (to / "ab.lv2" / "extra.so").symlink_to("../../x86_64-linux-gnu/calf/calf.so")
    with pytest.raises(PluginsError) as err:
        check([A], set(), to)
    assert "extra.so" in str(err.value) and "leads to nothing" in str(err.value)


@needs_lilv
def test_check_fails_on_a_required_plugin_whose_binary_is_not_a_file(system, tmp_path):
    """What Calf's bundle copied with `cp -r` looked like: lilv finds every
    URI, and Sushi then cannot load the plugin (#177)."""
    to = tmp_path / "plugins"
    assemble([A, C], set(), [system], to)
    (to / "c.lv2" / "c.so").unlink()
    with pytest.raises(PluginsError) as err:
        check([A, C], set(), to)
    assert C in str(err.value) and "c.so" in str(err.value) and A not in str(err.value)


@needs_lilv
def test_add_copies_only_what_plugins_lacks_and_touches_nothing_else(system, tmp_path):
    to = tmp_path / "plugins"
    assemble([A], set(), [system], to)
    (to / "amp.lv2").mkdir()
    (to / "amp.lv2" / "carried-by-hand").write_text("the amp")
    plan = add([A, C], set(), [system], to)
    assert [b.name for b in plan.bundles] == ["c.lv2"]
    assert sorted(p.name for p in to.iterdir()) == ["ab.lv2", "amp.lv2", "archive", "c.lv2"]
    assert (to / "amp.lv2" / "carried-by-hand").read_text() == "the amp"
    check([A, C], set(), to)
    assert add([A, C], set(), [system], to).bundles == [], "a second run has nothing to do"


@needs_lilv
def test_add_copies_nothing_when_one_bundle_fails_or_its_name_is_taken(system, tmp_path):
    to = tmp_path / "plugins"
    assemble([A], set(), [system], to)
    other = tmp_path / "other"
    _bundle(other, "d", "urn:t:d")
    (other / "d.lv2" / "lib.so").symlink_to("../../nowhere.so")
    with pytest.raises(PluginsError, match="leads to nothing"):
        add([A, C, "urn:t:d"], set(), [system, other], to)
    assert sorted(p.name for p in to.iterdir()) == ["ab.lv2", "archive"], "c.lv2 was added alone"
    (to / "c.lv2").mkdir()
    (to / "c.lv2" / "manifest.ttl").write_text("# something else of the same name\n")
    with pytest.raises(PluginsError, match="same name"):
        add([A, C], set(), [system], to)
    assert sorted(p.name for p in (to / "c.lv2").iterdir()) == ["manifest.ttl"]


@needs_lilv
def test_the_assembled_archive_is_silent_to_lilv(system, tmp_path, capfd):
    """lilv reports an unreadable manifest on stderr and carries on, so only
    watching stderr catches it."""
    to = tmp_path / "plugins"
    assemble([A], set(), [system], to)
    capfd.readouterr()
    check([A], set(), to)
    assert "Error loading file" not in capfd.readouterr().err


# --- through the CLI -------------------------------------------------------


@needs_lilv
def test_cli_assemble_then_check_round_trips(system, tmp_path, capsys):
    import json

    from sushi_rig.cli import main

    manifest = tmp_path / "manifest.txt"
    manifest.write_text(f"{A}\n")
    configs = tmp_path / "configs"
    configs.mkdir()
    (configs / "c.json").write_text(
        json.dumps({"tracks": [{"plugins": [{"uri": C}]}]})
    )
    to = tmp_path / "plugins"
    common = ["--manifest", str(manifest), "--config-dir", str(configs)]

    assert main(["plugins", "assemble", "--from", str(system), "--to", str(to), *common]) == 0
    assert (to / "c.lv2").is_dir()  # used by a config, absent from the manifest
    assert "out of date" in capsys.readouterr().err

    assert main(["plugins", "check", str(to), *common]) == 0


@needs_lilv
def test_cli_check_exits_nonzero_naming_the_missing_uri(system, tmp_path):
    from sushi_rig.cli import main

    manifest = tmp_path / "manifest.txt"
    manifest.write_text(f"{A}\n{C}\n")
    to = tmp_path / "plugins"
    to.mkdir()
    configs = tmp_path / "none"
    configs.mkdir()
    with pytest.raises(SystemExit) as exit_info:
        main(["plugins", "check", str(to), "--manifest", str(manifest),
              "--config-dir", str(configs)])
    assert C in str(exit_info.value.code)


@needs_lilv
def test_cli_add_adds_what_is_missing_and_says_which(system, tmp_path, capsys):
    from sushi_rig.cli import main

    manifest = tmp_path / "manifest.txt"
    manifest.write_text(f"{A}\n{C}\n")
    configs = tmp_path / "none"
    configs.mkdir()
    to = tmp_path / "plugins"
    assemble([A], set(), [system], to)
    common = ["--manifest", str(manifest), "--config-dir", str(configs)]
    assert main(["plugins", "add", "--from", str(system), str(to), *common]) == 0
    assert "c.lv2" in capsys.readouterr().out
    assert main(["plugins", "check", str(to), *common]) == 0



# --- the default sources (#188) ----------------------------------------------
#
# Ubuntu's calf-plugins puts calf.lv2 in /usr/lib/x86_64-linux-gnu/lv2 (the
# owner's laptop, 2026-11-19), and `add` searched /usr/lib/lv2 alone: the rig
# then failed inside Sushi. `lv2_system` stands a folder in for /usr/lib.

CALF = "http://calf.sourceforge.net/plugins/EnvelopeFilter"
MULTIARCH = Path("x86_64-linux-gnu") / "lv2"


def test_the_default_sources_are_usr_lib_lv2_then_the_multiarch_folder(lv2_system):
    usr_lib = lv2_system / "usr" / "lib"
    assert default_sources() == [usr_lib / "lv2"], "neither there: /usr/lib/lv2, for discover to refuse"
    (usr_lib / MULTIARCH).mkdir(parents=True)
    assert default_sources() == [usr_lib / MULTIARCH], "only what exists"
    (usr_lib / "lv2").mkdir()
    assert default_sources() == [usr_lib / "lv2", usr_lib / MULTIARCH]


def test_a_multiarch_folder_that_is_a_link_to_usr_lib_lv2_is_searched_once(lv2_system):
    usr_lib = lv2_system / "usr" / "lib"
    (usr_lib / "lv2").mkdir()
    (usr_lib / "x86_64-linux-gnu").mkdir()
    (usr_lib / MULTIARCH).symlink_to(usr_lib / "lv2", target_is_directory=True)
    assert default_sources() == [usr_lib / "lv2"]


def test_without_a_triplet_from_python_the_multiarch_folder_is_globbed(lv2_system, monkeypatch):
    """A Python built without MULTIARCH: the /usr/lib/*-linux-gnu*/lv2 there
    of this machine's architecture, and none when it has none, since an i386
    folder beside it holds binaries Sushi cannot load (review of PR #198:
    every one there was taken when none matched)."""
    import platform

    from sushi_rig import plugins

    usr_lib = lv2_system / "usr" / "lib"
    monkeypatch.setattr(plugins, "_multiarch", lambda: None)
    monkeypatch.setattr(platform, "machine", lambda: "x86_64")
    (usr_lib / "i386-linux-gnu" / "lv2").mkdir(parents=True)
    assert default_sources() == [usr_lib / "lv2"], "another architecture's alone: no multiarch folder"
    (usr_lib / MULTIARCH).mkdir(parents=True)
    assert default_sources() == [usr_lib / MULTIARCH]


@pytest.mark.parametrize("machine, triplet", [
    ("x86_64", "x86_64-linux-gnu"), ("i686", "i386-linux-gnu"), ("aarch64", "aarch64-linux-gnu"),
    ("armv7l", "arm-linux-gnueabihf"),
])
def test_the_globbed_multiarch_folder_is_the_machines_own_by_its_debian_name(
        lv2_system, monkeypatch, machine, triplet):
    """`platform.machine()` is not the triplet's first word on i686 or
    armv7l, and arm's triplet ends `-gnueabihf`, which `*-linux-gnu/lv2`
    missed (review of PR #198)."""
    import platform

    from sushi_rig import plugins

    usr_lib = lv2_system / "usr" / "lib"
    monkeypatch.setattr(plugins, "_multiarch", lambda: None)
    monkeypatch.setattr(platform, "machine", lambda: machine)
    for other in ("x86_64-linux-gnu", "i386-linux-gnu", "aarch64-linux-gnu", "arm-linux-gnueabihf"):
        (usr_lib / other / "lv2").mkdir(parents=True)
    assert default_sources() == [usr_lib / triplet / "lv2"]


def _requirement(tmp_path, *uris):
    """`--manifest` and `--config-dir` arguments requiring `uris`."""
    manifest = tmp_path / "manifest.txt"
    manifest.write_text("".join(f"{u}\n" for u in uris))
    configs = tmp_path / "no-configs"
    configs.mkdir(exist_ok=True)
    return ["--manifest", str(manifest), "--config-dir", str(configs)]


@needs_lilv
def test_add_with_no_from_finds_a_bundle_only_in_the_multiarch_folder(lv2_system, tmp_path, capsys):
    """The laptop's case: abGate in /usr/lib/lv2, Calf only in the multiarch
    folder, and `sushi-rig plugins add` with no --from."""
    from sushi_rig.cli import main

    usr_lib = lv2_system / "usr" / "lib"
    _bundle(usr_lib / "lv2", "abGate", A)
    _bundle(usr_lib / MULTIARCH, "calf", CALF)
    to = tmp_path / "plugins"
    to.mkdir()
    assert main(["plugins", "add", str(to), *_requirement(tmp_path, A, CALF)]) == 0
    assert "abGate.lv2, calf.lv2" in capsys.readouterr().out
    check([A, CALF], set(), to)


@needs_lilv
def test_the_same_bundle_in_both_default_sources_is_copied_once_from_usr_lib_lv2(lv2_system, tmp_path):
    usr_lib = lv2_system / "usr" / "lib"
    _bundle(usr_lib / "lv2", "calf", CALF)
    _bundle(usr_lib / MULTIARCH, "calf", CALF)
    (usr_lib / MULTIARCH / "calf.lv2" / "from-multiarch").write_text("")
    to = tmp_path / "plugins"
    to.mkdir()
    plan = add([CALF], set(), default_sources(), to)
    assert plan.bundles == [usr_lib / "lv2" / "calf.lv2"]
    assert not (to / "calf.lv2" / "from-multiarch").exists()


@needs_lilv
def test_two_bundles_of_one_name_split_between_the_sources_are_refused_naming_both(lv2_system, tmp_path):
    """An old calf.lv2 left in /usr/lib/lv2 beside a newer one in the
    multiarch folder that declares one more plugin: the rig needs a URI from
    each, plugins/ can hold one calf.lv2, and nothing is copied."""
    usr_lib = lv2_system / "usr" / "lib"
    _bundle(usr_lib / "lv2", "calf", CALF)
    _bundle(usr_lib / MULTIARCH, "calf", CALF, B)
    to = tmp_path / "plugins"
    to.mkdir()
    with pytest.raises(PluginsError) as err:
        add([CALF, B], set(), default_sources(), to)
    said = str(err.value)
    assert str(usr_lib / "lv2" / "calf.lv2") in said and str(usr_lib / MULTIARCH / "calf.lv2") in said
    assert "--from" in said
    assert list(to.iterdir()) == []


# --- what a missing URI is said with (#188) ----------------------------------


@needs_lilv
def test_a_missing_uri_installed_in_another_lv2_folder_names_it_and_the_from_to_add(lv2_system, tmp_path):
    usr_lib = lv2_system / "usr" / "lib"
    _bundle(usr_lib / "lv2", "ab", A)
    home_lv2 = lv2_system / "home" / ".lv2"
    _bundle(home_lv2, "c", C)
    to = tmp_path / "plugins"
    to.mkdir()
    with pytest.raises(PluginsError) as err:
        add([A, C], set(), default_sources(), to)
    said = str(err.value)
    assert f"{C}: installed in {home_lv2}, which was not searched" in said
    assert f"--from {usr_lib / 'lv2'} --from {home_lv2}" in said


@needs_lilv
def test_the_multiarch_folder_left_out_by_from_is_named_for_a_uri_it_holds(lv2_system, tmp_path):
    """The workaround the laptop needed, said by the refusal itself: --from
    /usr/lib/lv2 alone, and Calf in the multiarch folder."""
    usr_lib = lv2_system / "usr" / "lib"
    _bundle(usr_lib / "lv2", "ab", A)
    _bundle(usr_lib / MULTIARCH, "calf", CALF)
    to = tmp_path / "plugins"
    with pytest.raises(PluginsError) as err:
        assemble([A, CALF], set(), [usr_lib / "lv2"], to)
    said = str(err.value)
    assert f"{CALF}: installed in {usr_lib / MULTIARCH}" in said
    assert f"--from {usr_lib / 'lv2'} --from {usr_lib / MULTIARCH}" in said


@needs_lilv
def test_a_missing_built_plugin_names_its_build_script_and_one_nowhere_says_install(lv2_system, tmp_path):
    usr_lib = lv2_system / "usr" / "lib"
    _bundle(usr_lib / "lv2", "ab", A)
    chorus = "http://jpcima.sdf1.org/lv2/string-machine-chorus"
    octave = "http://breakfastquay.com/rdf/lv2-rubberband#livemono"
    to = tmp_path / "plugins"
    to.mkdir()
    with pytest.raises(PluginsError) as err:
        add([A, chorus, octave, C], set(), default_sources(), to)
    said = str(err.value)
    assert f"{chorus}: built here, not packaged: scripts/build-string-machine-chorus.sh {to} builds it" in said
    assert f"{octave}: built here, not packaged: scripts/build-rubberband4.sh {to} builds it" in said
    assert f"{C}: in no standard LV2 folder: install the package that provides it" in said


# --- before a start (#188) ---------------------------------------------------


def _declaring(folder: Path, name: str, *uris: str) -> Path:
    """A bundle `preflight` reads: a manifest declaring `uris`. No lilv."""
    bundle = folder / f"{name}.lv2"
    bundle.mkdir(parents=True)
    (bundle / "manifest.ttl").write_text(
        "@prefix lv2: <http://lv2plug.in/ns/lv2core#> .\n"
        + "".join(f"<{u}> a lv2:Plugin .\n" for u in uris))
    return bundle


def test_preflight_names_what_the_chain_lacks_and_keeps_the_amp_apart(tmp_path):
    plugins = tmp_path / "plugins"
    _declaring(plugins, "ab", A, B)
    config = {"tracks": [{"plugins": [{"uri": A}, {"uri": C}]}],
              "_amp": {"plugin": "nam", "model": "amp/models/x.nam"}}
    assert (preflight(config, plugins).missing, preflight(config, plugins).amp) == ([C], [NAM_URI])
    off = preflight(config, plugins, amp=False)
    assert (off.missing, off.amp) == ([C], []), "no amp is started, so none is needed"
    _declaring(plugins, "c", C)
    _declaring(plugins, "nam", NAM_URI)
    assert preflight(config, plugins) == Preflight()


@pytest.mark.parametrize("section, amp_model, asked", [
    ({"plugin": "nam", "model": "amp/models/x.nam"}, None, True),
    ({"model": "amp/models/x.nam"}, None, True),  # no "plugin": amp.prepare starts NAM all the same
    ({"plugin": "nam"}, None, False),  # no model: amp.prepare starts none
    ({"parameters": {"Input Lvl": 0.0}}, "Other.nam", False),  # an _amp, so the override is not one
    (None, "Other.nam", True),  # --amp-model with no _amp gets an amp
    (None, None, False),
    ({"plugin": "some-future-amp"}, None, False),
])
def test_the_amp_is_asked_for_exactly_when_amp_prepare_starts_one(tmp_path, section, amp_model, asked):
    """`amp.prepare`'s rule (review of PR #198): an `_amp` section never
    raises here, and never refuses; it is a warning when an amp will start."""
    from sushi_rig import rig

    config = {"tracks": [], **({"_amp": section} if section is not None else {})}
    (tmp_path / "plugins").mkdir()
    pre = preflight(config, tmp_path / "plugins", amp_model=amp_model)
    assert pre.amp == ([NAM_URI] if asked else []) and not pre.missing
    refusal, warnings = rig.plugins_refusal(config, tmp_path, "x", amp=True, amp_model=amp_model)
    assert refusal is None and len(warnings) == (1 if asked else 0)


@needs_lilv
def test_preflight_asks_lilv_before_it_calls_a_plugin_missing(tmp_path, monkeypatch):
    """A manifest the Turtle reader could not read declares nothing to it;
    lilv, which Sushi loads plugins through, still finds the plugin, and a
    rig that would play is not refused for it."""
    from sushi_rig import library

    plugins = tmp_path / "plugins"
    _bundle(plugins, "ab", A)
    monkeypatch.setattr(library, "manifest_plugins", lambda folder: ({}, []))
    config = {"tracks": [{"plugins": [{"uri": A}, {"uri": B}]}]}
    assert preflight(config, plugins).missing == [B]


def test_preflight_of_a_plugins_folder_that_is_not_there_lacks_everything(tmp_path):
    config = {"tracks": [{"plugins": [{"uri": A}]}]}
    assert preflight(config, tmp_path / "plugins").missing == [A]


def test_the_refusal_says_each_uri_and_the_commands_in_the_order_to_run_them(lv2_system, tmp_path):
    """synth-lead on the laptop: Calf packaged but in the multiarch folder,
    the chorus built, abGate in ~/.lv2. The script first, since `plugins add`
    copies nothing while anything is still missing."""
    usr_lib = lv2_system / "usr" / "lib"
    _declaring(usr_lib / "lv2", "gx", A)
    _declaring(usr_lib / MULTIARCH, "calf", CALF)
    home_lv2 = lv2_system / "home" / ".lv2"
    _declaring(home_lv2, "abGate", "http://hippie.lt/lv2/gate")
    chorus = "http://jpcima.sdf1.org/lv2/string-machine-chorus"
    plugins = tmp_path / "Sushi" / "plugins"
    plugins.mkdir(parents=True)
    root = tmp_path / "Sushi"
    said = preflight_refusal("synth-lead", Preflight(missing=sorted([A, CALF, chorus, "http://hippie.lt/lv2/gate"])),
                             plugins, root)
    lines = said.splitlines()
    assert lines[0].startswith(f"synth-lead was not started: 4 plugin(s) it loads are not in {plugins}")
    assert f"  {CALF}: installed in {usr_lib / MULTIARCH}, which `sushi-rig plugins add` copies from" in lines
    assert f"  {A}: installed in {usr_lib / 'lv2'}, which `sushi-rig plugins add` copies from" in lines
    assert f"  http://hippie.lt/lv2/gate: installed in {home_lv2}, which was not searched" in lines
    assert any(line.startswith(f"  {chorus}: built here") for line in lines)
    assert lines[-3].startswith("Nothing was started. To fix it, run these, in order")
    assert lines[-2:] == [
        f"  {root / 'scripts/build-string-machine-chorus.sh'} {plugins}",
        f"  sushi-rig plugins add --from {usr_lib / 'lv2'} --from {usr_lib / MULTIARCH} --from {home_lv2}",
    ]


def test_the_refusal_for_packaged_plugins_alone_is_plugins_add(lv2_system, tmp_path):
    usr_lib = lv2_system / "usr" / "lib"
    _declaring(usr_lib / MULTIARCH, "calf", CALF)
    plugins = tmp_path / "plugins"
    plugins.mkdir()
    said = preflight_refusal("synth-lead", Preflight(missing=[CALF]), plugins, tmp_path)
    assert said.splitlines()[-2:] == [
        "Nothing was started. To fix it, run this, then start the rig again:",
        "  sushi-rig plugins add",
    ]


# --- the review of PR #198 -----------------------------------------------------


@pytest.mark.parametrize("failure", [
    "raise OSError('liblilv-0.so: cannot open shared object file: No such file or directory')",
    "raise ImportError('No module named _lilv')",
])
def test_a_lilv_that_cannot_load_is_no_lilv_and_ends_nothing(tmp_path, monkeypatch, failure):
    """python3-lilv without liblilv-dev: `find_spec` finds it and importing
    it raises OSError from its CDLL; an ImportError made `probe._world` call
    `sys.exit`, whose SystemExit passed the MIDI handler's `except
    Exception`. Either is "no lilv", and the manifests' answer stands."""
    import sys

    from sushi_rig import rig

    stand_in = tmp_path / "stand-in"
    stand_in.mkdir()
    (stand_in / "lilv.py").write_text(failure + "\n")
    monkeypatch.syspath_prepend(str(stand_in))
    monkeypatch.delitem(sys.modules, "lilv", raising=False)
    plugins = tmp_path / "plugins"
    _declaring(plugins, "ab", A)
    config = {"tracks": [{"plugins": [{"uri": A}, {"uri": B}]}]}
    assert preflight(config, plugins).missing == [B]
    refusal, _ = rig.plugins_refusal(config, tmp_path, "x", amp=False)
    assert refusal is not None and B in refusal


def test_a_lilv_world_that_exits_is_no_lilv(tmp_path, monkeypatch):
    """`probe._world` ends the process when lilv will not import; the check
    asks lilv through it, and a SystemExit must not come out of a check."""
    from sushi_rig import plugins as plugins_module

    def exits(lv2_path):
        raise SystemExit("lilv not found")

    monkeypatch.setattr(plugins_module, "_plugins_and_binaries", exits)
    plugins = tmp_path / "plugins"
    _declaring(plugins, "ab", A)
    assert preflight({"tracks": [{"plugins": [{"uri": B}]}]}, plugins).missing == [B]


@pytest.mark.parametrize("name, manifest", [
    ("base", b"@base <http://example.invalid/p/> .\n@prefix lv2: <http://lv2plug.in/ns/lv2core#> .\n"
             b"<a> a lv2:Plugin .\n"),
    ("bom", "﻿@prefix lv2: <http://lv2plug.in/ns/lv2core#> .\n<http://example.invalid/p/a> a lv2:Plugin "
            ".\n".encode("utf-8")),
    ("latin1", '@prefix lv2: <http://lv2plug.in/ns/lv2core#> .\n<http://example.invalid/p/a> a lv2:Plugin ; '
               '<urn:t:name> "caf\xe9" .\n'.encode("latin-1")),
])
def test_manifests_lilv_loads_are_read_as_lilv_reads_them(tmp_path, monkeypatch, name, manifest):
    """Three a review found the reader dropped whole while lilv 0.24.22
    loads them: an `@base` with a relative plugin IRI (urljoin dropped the
    `#` of the absolute lv2: prefix under a base of the same scheme), a
    UTF-8 byte-order mark, a Latin-1 byte in a literal. With no lilv to ask,
    each was a false refusal."""
    from sushi_rig.library import declared_plugins

    plugins = tmp_path / "plugins"
    (plugins / f"{name}.lv2").mkdir(parents=True)
    (plugins / f"{name}.lv2" / "manifest.ttl").write_bytes(manifest)
    unread: list = []
    assert declared_plugins(plugins, unread) == {"http://example.invalid/p/a": plugins / f"{name}.lv2"}
    assert unread == []
    monkeypatch.setattr("sushi_rig.plugins._lilv_plugins", lambda folder: None)
    assert preflight({"tracks": [{"plugins": [{"uri": "http://example.invalid/p/a"}]}]}, plugins) == Preflight()


def test_with_a_bundle_unread_and_no_lilv_what_is_unseen_is_warned_of_not_refused(tmp_path, monkeypatch):
    """The reader cannot see into a bundle it could not parse, so what it
    does not see may be there: without lilv to ask, that is said, and the
    rig starts."""
    from sushi_rig import rig

    plugins = tmp_path / "plugins"
    _declaring(plugins, "ab", A)
    (plugins / "odd.lv2").mkdir()
    (plugins / "odd.lv2" / "manifest.ttl").write_text("@prefix lv2: <http://lv2plug.in/ns/lv2core#> .\n<x> a {\n")
    monkeypatch.setattr("sushi_rig.plugins._lilv_plugins", lambda folder: None)
    config = {"tracks": [{"plugins": [{"uri": A}, {"uri": B}]}]}
    pre = preflight(config, plugins)
    assert (pre.missing, pre.unsure, pre.unread) == ([], [B], [plugins / "odd.lv2"])
    refusal, warnings = rig.plugins_refusal(config, tmp_path, "x", amp=False)
    assert refusal is None
    assert len(warnings) == 1 and B in warnings[0] and "odd.lv2 could not be read" in warnings[0]


def test_a_bundle_whose_binary_is_a_link_to_nothing_is_refused_naming_it(lv2_system, tmp_path):
    """Calf copied with `cp -r`: its manifest declares the URI and its
    `calf.so` leads nowhere, so Sushi quits with the bare "Failed to load
    tracks". Refused, naming the bundle; the fix removes it first, since
    `plugins add` leaves a bundle that is there alone."""
    from sushi_rig import rig

    _declaring(lv2_system / "usr" / "lib" / MULTIARCH, "calf", CALF)
    plugins = tmp_path / "plugins"
    bundle = plugins / "calf.lv2"
    bundle.mkdir(parents=True)
    (bundle / "manifest.ttl").write_text(
        f"@prefix lv2: <http://lv2plug.in/ns/lv2core#> .\n<{CALF}> a lv2:Plugin ; lv2:binary <calf.so> .\n")
    (bundle / "calf.so").symlink_to("../../x86_64-linux-gnu/calf/calf.so")
    config = {"tracks": [{"plugins": [{"uri": CALF}]}]}
    pre = preflight(config, plugins)
    assert pre.missing == [] and list(pre.broken) == [CALF]
    refusal, _ = rig.plugins_refusal(config, tmp_path, "synth-lead", amp=False)
    lines = refusal.splitlines()
    assert f"  {CALF}: in {bundle}, but its binary calf.so is a link that leads nowhere" in "\n".join(lines)
    assert lines[-2:] == [f"  rm -r {bundle}", "  sushi-rig plugins add"]
    (bundle / "calf.so").unlink()
    (bundle / "calf.so").write_text("")
    assert preflight(config, plugins) == Preflight()


def test_the_add_the_refusal_prescribes_waives_what_add_would_still_miss(lv2_system, tmp_path):
    """`plugins add` requires the whole manifest and every config, not one
    rig: on a machine whose plugins/ lacks the amp and a plugin built for
    another rig, the bare `sushi-rig plugins add` the refusal gave exits 1.
    Dry-run, the command waives each by name and says why; this rig's own
    are never waived."""
    from sushi_rig.amp import NAM_URI as NAM

    chorus = "http://jpcima.sdf1.org/lv2/string-machine-chorus"
    _declaring(lv2_system / "usr" / "lib" / MULTIARCH, "calf", CALF)
    root = tmp_path / "Sushi"
    (root / "config").mkdir(parents=True)
    (root / "plugin-manifest.txt").write_text(f"{A}\n{CALF}\n{NAM}\n{chorus}\n")
    plugins = root / "plugins"
    _declaring(plugins, "ab", A)
    said = preflight_refusal("synth-lead", Preflight(missing=[CALF]), plugins, root)
    lines = said.splitlines()
    assert lines[-3:-1] == [
        "Nothing was started. To fix it, run this, then start the rig again:",
        f"  sushi-rig plugins add --waive {NAM} --waive {chorus}",
    ]
    assert f"{chorus} (built here, not loaded by this rig: scripts/build-string-machine-chorus.sh" in lines[-1]
    assert NAM in lines[-1] and "by hand" in lines[-1]


@needs_lilv
def test_the_add_the_refusal_prescribes_works(lv2_system, tmp_path, capsys):
    """And run as given, it exits 0 and leaves the rig with what it needs.
    Run through the CLI, with lilv, as the player would."""
    import shlex

    from sushi_rig.amp import NAM_URI as NAM
    from sushi_rig.cli import main

    chorus = "http://jpcima.sdf1.org/lv2/string-machine-chorus"
    _bundle(lv2_system / "usr" / "lib" / MULTIARCH, "calf", CALF)
    root = tmp_path / "Sushi"
    (root / "config").mkdir(parents=True)
    (root / "plugin-manifest.txt").write_text(f"{A}\n{CALF}\n{NAM}\n{chorus}\n")
    plugins = root / "plugins"
    _bundle(plugins, "ab", A)
    said = preflight_refusal("synth-lead", Preflight(missing=[CALF]), plugins, root)
    command = shlex.split(said.splitlines()[-2])
    assert command[:3] == ["sushi-rig", "plugins", "add"]
    args = command[1:] + [str(plugins), "--manifest", str(root / "plugin-manifest.txt"),
                          "--config-dir", str(root / "config")]
    assert main(args) == 0
    assert preflight({"tracks": [{"plugins": [{"uri": CALF}]}]}, plugins) == Preflight()
