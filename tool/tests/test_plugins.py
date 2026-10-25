"""Assembling `plugins/`: the decisions are pure and pinned here; the lilv and
filesystem parts run against small synthetic LV2 bundles written to a temp dir.

The synthetic bundles are real as far as lilv is concerned — a `manifest.ttl`
declaring a `lv2:Plugin` — so discovery and the self-containment check are
exercised through the actual library, not a mock of it. They have no binary,
which lilv does not need in order to list a plugin.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from sushi_rig.plugins import (
    ARCHIVE_MANIFEST,
    Plan,
    PluginsError,
    assemble,
    check,
    config_uris,
    copy_bundles,
    decide,
    describe_failure,
    discover,
    read_manifest,
)

needs_lilv = pytest.mark.skipif(
    importlib.util.find_spec("lilv") is None,
    reason="lilv is a system package (python3-lilv)",
)

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
    startup — found by running it; the README says empty is fine."""
    assert ARCHIVE_MANIFEST.strip().startswith("#")


def test_copy_keeps_symlinks_as_links(tmp_path):
    src = tmp_path / "src" / "a.lv2"
    src.mkdir(parents=True)
    (src / "real.so").write_text("lib")
    (src / "link.so").symlink_to("real.so")
    copy_bundles([src], tmp_path / "plugins")
    assert (tmp_path / "plugins" / "a.lv2" / "link.so").is_symlink()


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
    (bundle / f"{name}.ttl").write_text("")
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
