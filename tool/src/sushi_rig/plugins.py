"""Build the rig's `plugins/` folder from installed packages, and prove it is whole.

`plugins/` is untracked (README: Plugins and portability), so on a fresh machine
it has to be made. Doing that by hand is how the old defect arose: a flat copy
of the whole system tree that only worked because `/usr/lib/lv2` was *also* on
the path. This module makes it reproducibly and then checks the one property
that matters — that every URI the rig can load resolves with `plugins/` alone
on `LV2_PATH`.

Two halves, kept apart (quality principle 1):

- `decide` is pure. Given the URIs the rig needs and a `{uri: bundle}` mapping,
  it says which bundles to copy and which URIs nothing provides.
- Discovery, copying and checking are the effects, and they are verified by
  running them against a real LV2 tree, not by mocking lilv.

## What counts as "needed"

The manifest is not enough on its own, in principle. `plugin-manifest.txt` says
it lists every URI the rig can load, and it once fell behind the configs in
`config/` (a reverb, a delay, a detuner were used but not listed). Today every
URI the committed configs use is in it. The rule stays because it is what keeps
that true: assembling from the manifest alone would produce a `plugins/` that
passes its own check and then fails the first time Sushi loads a real config —
the silent kind of failure principle 8 exists to prevent. So the requirement is
the manifest *plus* whatever the configs use, and `decide` reports the
difference (`undeclared`) so the manifest can be corrected.

## Waivers

A URI the packages cannot provide is a hard failure by default, listing every
such URI at once. When it is genuinely acceptable — the distro ships an older
plugin than the manifest was written against, and nothing the rig runs uses the
missing one — the caller must say so by name with `--waive URI`. A waiver for a
URI the rig does not require is an error, so a stale waiver cannot linger after
the reason for it has gone.

## `archive/`

The README's layout has an `archive/` beside the bundles, holding everything
off the path, with a `manifest.ttl` that declares nothing (a single comment, see
`ARCHIVE_MANIFEST`) so lilv's probe of it stays quiet. An assembled tree has nothing to archive, but gets the same shape:
anything parked there later behaves exactly as it does on the rig machine.

`lilv` is a system package and is imported lazily by `probe._world`, so this
module — and its pure half — imports without it.
"""

from __future__ import annotations

import os
import shutil
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

from .amp import CONFIG_KEY as AMP_KEY, PLUGIN_URIS as AMP_PLUGIN_URIS

# Where distro packages put LV2 bundles. The default source for `assemble`.
SYSTEM_LV2_DIR = Path("/usr/lib/lv2")

# The one file lilv needs to find in `archive/` to stop logging about it.
#
# A comment, not a zero-byte file. The manifest "declares nothing", but
# with serd 0.32 (Ubuntu 24.04) a literally empty file makes lilv log "Error
# loading file" at every startup — the very noise it exists to prevent. A
# comment-only file parses cleanly there, and is valid turtle everywhere.
ARCHIVE_DIR = "archive"
ARCHIVE_MANIFEST = "# Intentionally declares nothing: see sushi_rig/plugins.py.\n"


class PluginsError(Exception):
    """Something about `plugins/` the user needs to fix."""


# --- the pure part ----------------------------------------------------------


def read_manifest(text: str) -> list[str]:
    """URIs from `plugin-manifest.txt`: one per line, blanks and `#` comments ignored.

    The file today has neither comments nor blanks. Tolerating them costs one
    line and means adding a note to the manifest cannot turn into a plugin
    named "# rubberband is old".
    """
    uris = []
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            uris.append(line)
    return uris


def config_uris(config: dict[str, Any], source: str | None = None) -> set[str]:
    """Every LV2 plugin URI a config needs on `LV2_PATH`.

    Not the same as "every plugin Sushi loads", and the difference was a real
    hole. The amp is an LV2 plugin hosted by Carla rather than by Sushi, so it
    lives in the top-level `_amp` section instead of under `tracks`, and that
    section names it `"plugin": "nam"` with no `uri` key at all. Walking
    `tracks` therefore missed it twice over, and the manifest did not list it
    either — so the one plugin no distro packages, and the one most likely to
    be absent on a fresh machine, was the one plugin `check` did not check. It
    would report every URI resolving while `plugins/` held no amp.

    The amp belongs here because the supervisor hands Carla the same
    `LV2_PATH` as everything else: if NAM is not in `plugins/`, the rig has no
    amp, which is exactly the property this function exists to protect.
    """
    uris = {
        plugin["uri"]
        for track in config.get("tracks", [])
        for plugin in track.get("plugins", [])
        if plugin.get("uri")
    }

    amp = config.get(AMP_KEY)
    if isinstance(amp, dict):
        # Named, because `check --all` walks every config and an error that
        # says only "None" leaves you grepping for which file it meant.
        known = ", ".join(sorted(AMP_PLUGIN_URIS)) or "none"
        where = f"{source}: " if source else ""
        name = amp.get("plugin")
        if name is None:
            raise PluginsError(
                f'{where}its "{AMP_KEY}" section names no plugin, so the amp\'s '
                f'URI cannot be required. Add "plugin": "nam".'
            )
        if name not in AMP_PLUGIN_URIS:
            # Loud, because the alternative is quietly not requiring an amp
            # the config says it wants — the precise failure this function was
            # just widened to prevent.
            raise PluginsError(
                f"{where}names an amp plugin this tool has no URI for: "
                f"{name!r}. Known: {known}."
            )
        uris.add(AMP_PLUGIN_URIS[name])

    return uris


@dataclass(frozen=True)
class Plan:
    """What `decide` concluded. All lists are sorted, so output is stable."""

    # Bundle directories to copy, each once, however many URIs it provides.
    bundles: list[Path] = field(default_factory=list)
    # How many required URIs a bundle does provide.
    resolved: int = 0
    # Required URIs nothing provides and nobody waived — the failure list.
    missing: list[str] = field(default_factory=list)
    # Required URIs nothing provides but that were waived by name.
    waived: list[str] = field(default_factory=list)
    # Used by a config but absent from the manifest: the manifest is stale.
    undeclared: list[str] = field(default_factory=list)
    # Different bundles that would land at the same name in `plugins/`.
    collisions: dict[str, list[Path]] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.missing and not self.collisions


def decide(
    manifest: list[str],
    used: set[str],
    providers: dict[str, Path],
    waived: set[str] | frozenset[str] = frozenset(),
) -> Plan:
    """Which bundles to copy, and which required URIs have no provider.

    `providers` maps plugin URI -> the bundle directory that declares it. The
    requirement is `manifest | used`; see the module docstring for why.

    Reports every problem rather than the first, following `RigSpec.validate`:
    when a package set is wrong, you want the whole list, not one fix cycle per
    URI.
    """
    required = set(manifest) | set(used)

    stray = sorted(set(waived) - required)
    if stray:
        raise PluginsError(
            "waived, but not required by the manifest or any config — remove the "
            "waiver:\n" + "\n".join(f"  {u}" for u in stray)
        )

    absent = required - providers.keys()
    needed = {providers[u] for u in required if u in providers}

    # `plugins/` is flat, so two bundles sharing a directory name (possible when
    # assembling from more than one source dir) cannot both be copied. Copying
    # one over the other would silently pick a winner.
    by_name: dict[str, list[Path]] = {}
    for bundle in sorted(needed):
        by_name.setdefault(bundle.name, []).append(bundle)

    return Plan(
        bundles=sorted(needed),
        resolved=len(required) - len(absent),
        missing=sorted(absent - set(waived)),
        waived=sorted(absent & set(waived)),
        undeclared=sorted(set(used) - set(manifest)),
        collisions={n: paths for n, paths in by_name.items() if len(paths) > 1},
    )


def describe_failure(plan: Plan, where: str) -> str:
    """The message for a plan that is not ok — every problem, not the first."""
    lines = []
    if plan.missing:
        lines.append(
            f"{len(plan.missing)} required plugin URI(s) have no provider in {where}:"
        )
        lines += [f"  {uri}" for uri in plan.missing]
    for name, paths in plan.collisions.items():
        lines.append(f"{name} is provided by more than one bundle:")
        lines += [f"  {p}" for p in paths]
    return "\n".join(lines)


# --- effects: lilv and the filesystem ---------------------------------------


@contextmanager
def _lv2_path(value: str) -> Iterator[None]:
    """`LV2_PATH` set to exactly `value` while a lilv world is built.

    lilv reads the variable once, when the world loads, and when it is set uses
    *only* it. That is the same mechanism Sushi relies on, so using it here
    checks what Sushi will see rather than a lilv-specific approximation.
    """
    previous = os.environ.get("LV2_PATH")
    os.environ["LV2_PATH"] = value
    try:
        yield
    finally:
        if previous is None:
            del os.environ["LV2_PATH"]
        else:
            os.environ["LV2_PATH"] = previous


def plugins_on(lv2_path: str) -> dict[str, Path]:
    """Every plugin URI visible with `LV2_PATH=lv2_path`, mapped to its bundle."""
    # Private to probe, but it is the one place that turns a missing python3-lilv
    # into an actionable message, and duplicating that would let them drift.
    from .probe import _world

    with _lv2_path(lv2_path):
        world = _world()
    found: dict[str, Path] = {}
    for plugin in world.get_all_plugins():
        bundle = str(plugin.get_bundle_uri()).replace("file://", "").rstrip("/")
        found[str(plugin.get_uri())] = Path(bundle)
    return found


def discover(from_dirs: list[Path]) -> dict[str, Path]:
    """`{uri: bundle}` for everything installed under `from_dirs`.

    Earlier directories win, as on an `LV2_PATH`.
    """
    for directory in from_dirs:
        if not directory.is_dir():
            raise PluginsError(f"no such LV2 directory: {directory}")
    return plugins_on(os.pathsep.join(str(d) for d in from_dirs))


def copy_bundles(bundles: list[Path], to: Path) -> None:
    """Copy `bundles` into `to` and lay out `archive/`.

    Symlinks are kept as links: some bundles are made of them, and following
    them would copy a shared library once per bundle that points at it.
    """
    to.mkdir(parents=True, exist_ok=True)
    for bundle in bundles:
        shutil.copytree(bundle, to / bundle.name, symlinks=True)
    archive = to / ARCHIVE_DIR
    archive.mkdir(exist_ok=True)
    (archive / "manifest.ttl").write_text(ARCHIVE_MANIFEST)


def assemble(
    manifest: list[str],
    used: set[str],
    from_dirs: list[Path],
    to: Path,
    waived: set[str] | frozenset[str] = frozenset(),
    replace: bool = False,
) -> Plan:
    """Build `to` from the bundles under `from_dirs` that the rig needs.

    Decides first and copies nothing if the plan is not ok: a half-built
    `plugins/` that looks plausible is worse than none.

    Refuses to write into a non-empty `to` unless `replace`, because the target
    is a folder someone may have put things in by hand, and deleting it on a
    mistyped path is not a recoverable mistake.
    """
    plan = decide(manifest, used, discover(from_dirs), waived)
    if not plan.ok:
        raise PluginsError(describe_failure(plan, ", ".join(map(str, from_dirs))))

    if to.exists() and any(to.iterdir()):
        if not replace:
            raise PluginsError(
                f"{to} already exists and is not empty. Pass --replace to delete "
                "it and rebuild, or choose another --to."
            )
        shutil.rmtree(to)
    copy_bundles(plan.bundles, to)
    return plan


def check(
    manifest: list[str],
    used: set[str],
    plugins_dir: Path,
    waived: set[str] | frozenset[str] = frozenset(),
) -> Plan:
    """Every required URI resolves with `LV2_PATH` set to `plugins_dir` alone.

    The point of the whole arrangement: nothing falls back to a system copy. A
    `plugins/` that is missing a bundle fails here, naming every URI it lacks,
    rather than at the first config that happens to need one.
    """
    if not plugins_dir.is_dir():
        raise PluginsError(
            f"{plugins_dir} does not exist. Build it with `sushi-rig plugins assemble`."
        )
    plan = decide(manifest, used, plugins_on(str(plugins_dir)), waived)
    if not plan.ok:
        raise PluginsError(describe_failure(plan, f"{plugins_dir} (LV2_PATH set to it alone)"))
    return plan
