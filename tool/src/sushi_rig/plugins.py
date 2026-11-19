"""Build the rig's `plugins/` folder from installed packages, and prove it is whole.

`plugins/` is untracked (docs/reference/plugins.md), so on a fresh machine
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

The layout in docs/reference/plugins.md has an `archive/` beside the bundles, holding everything
off the path, with a `manifest.ttl` that declares nothing (a single comment, see
`ARCHIVE_MANIFEST`) so lilv's probe of it stays quiet. An assembled tree has nothing to archive, but gets the same shape:
anything parked there later behaves exactly as it does on the rig machine.

`lilv` is a system package and is imported lazily by `probe._world`, so this
module — and its pure half — imports without it.
"""

from __future__ import annotations

import dataclasses
import os
import shutil
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

from .amp import CONFIG_KEY as AMP_KEY, PLUGIN_URIS as AMP_PLUGIN_URIS

# Where distro packages put LV2 bundles: `/usr/lib/lv2`, and on Debian and
# Ubuntu also the multiarch folder `/usr/lib/<triplet>/lv2`, where Ubuntu's
# calf-plugins puts calf.lv2 on the owner's laptop (2026-11-19). Both are
# the default sources of `assemble` and `add` (`default_sources`). Module
# names, so a test stands a folder of its own in for `/usr/lib`.
USR_LIB = Path("/usr/lib")
USR_LOCAL_LIB = Path("/usr/local/lib")
SYSTEM_LV2_DIR = USR_LIB / "lv2"

# Plugins this repository builds because no distribution packages them (or
# packages a version without them): the script that builds each, given the
# folder to leave its bundle in (docs/reference/plugins.md).
BUILT = {
    "http://jpcima.sdf1.org/lv2/string-machine-chorus": "scripts/build-string-machine-chorus.sh",
    "http://breakfastquay.com/rdf/lv2-rubberband#livemono": "scripts/build-rubberband4.sh",
    "http://breakfastquay.com/rdf/lv2-rubberband#livestereo": "scripts/build-rubberband4.sh",
}

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


def describe_failure(
    plan: Plan, where: str, notes: dict[str, str] | None = None, then: list[str] | None = None,
) -> str:
    """The message for a plan that is not ok — every problem, not the first.

    `notes` says, beside a missing URI, where it is instead or how it is
    made (`advise`); `then` follows the list, the commands that would fix it."""
    notes = notes or {}
    lines = []
    if plan.missing:
        lines.append(
            f"{len(plan.missing)} required plugin URI(s) have no provider in {where}:"
        )
        lines += [f"  {uri}" + (f": {notes[uri]}" if uri in notes else "") for uri in plan.missing]
        lines += then or []
    for name, paths in plan.collisions.items():
        lines.append(f"{name} is provided by more than one bundle:")
        lines += [f"  {p}" for p in paths]
    if plan.collisions:
        # Two bundles of one name, each providing something the rig needs:
        # two installs of one package, an old copy left beside a new one.
        # lilv takes the higher version of a URI both declare, and the
        # earlier folder only between equal ones, so the order of --from
        # cannot always settle it; leaving the stale folder out does.
        lines.append(
            "plugins/ is flat, so only one bundle of a name can be copied. Pass --from with only "
            "the folders to take bundles from, leaving out the one whose copy is not wanted, or "
            "remove the stale copy."
        )
    return "\n".join(lines)


# --- where bundles are looked for -------------------------------------------


def _multiarch() -> str | None:
    """This system's multiarch triplet (`x86_64-linux-gnu`), as the Python
    running this was built for it; None where it was built without one."""
    import sysconfig

    return sysconfig.get_config_var("MULTIARCH") or None


def multiarch_lv2_dirs() -> list[Path]:
    """`/usr/lib/<triplet>/lv2` for this system's triplet, where it exists.

    The triplet comes from Python (`_multiarch`). A Python built without one
    (some built from source) falls back to the `/usr/lib/*-linux-gnu/lv2`
    that exist, in name order, and of those only this machine's
    architecture's when any is: an i386 folder beside an x86_64 one holds
    binaries Sushi cannot load."""
    import platform

    triplet = _multiarch()
    if triplet:
        candidates = [USR_LIB / triplet / "lv2"]
    else:
        candidates = sorted(USR_LIB.glob("*-linux-gnu/lv2"))
        own = [c for c in candidates if c.parent.name.startswith(f"{platform.machine()}-")]
        candidates = own or candidates
    return [c for c in candidates if c.is_dir()]


def _distinct(folders: list[Path]) -> list[Path]:
    """`folders` in order, each once, by where it really is: a multiarch
    folder that is a link to `/usr/lib/lv2` is the same folder."""
    seen: set[Path] = set()
    kept = []
    for folder in folders:
        key = folder.resolve()
        if key not in seen:
            seen.add(key)
            kept.append(folder)
    return kept


def default_sources() -> list[Path]:
    """Where `assemble` and `add` take bundles from when no `--from` is
    given, in this order: `/usr/lib/lv2`, then the multiarch folder
    (`multiarch_lv2_dirs`), each only where it exists. `--from` replaces
    them all.

    The order is that of an `LV2_PATH`, which is how they are searched
    (`discover`): for a URI both declare, lilv takes the higher
    `lv2:minorVersion`/`lv2:microVersion`, and between equal versions the
    earlier folder, `/usr/lib/lv2`. The same bundle in both is therefore
    copied once, from `/usr/lib/lv2`; two bundles of one name that each
    provide something the rig needs are refused, naming both (`decide`).

    With neither folder there, `/usr/lib/lv2` alone, for `discover` to say
    it does not exist."""
    found = [d for d in _distinct([USR_LIB / "lv2", *multiarch_lv2_dirs()]) if d.is_dir()]
    return found or [USR_LIB / "lv2"]


def standard_lv2_dirs() -> list[Path]:
    """The folders an LV2 plugin is installed in on Linux, where they exist:
    lilv's own default path (`~/.lv2`, `/usr/local/lib/lv2`, `/usr/lib/lv2`)
    and the multiarch folder. Where a URI the rig lacks is looked for, to
    say where it is (`advise`); never a source unless named with `--from`."""
    folders = [Path.home() / ".lv2", USR_LOCAL_LIB / "lv2", USR_LIB / "lv2", *multiarch_lv2_dirs()]
    return [d for d in _distinct(folders) if d.is_dir()]


def locate(uris: list[str] | set[str], folders: list[Path]) -> dict[str, Path]:
    """`{uri: folder}` for each of `uris` a bundle in one of `folders`
    declares, the first folder's where several do. Read from the bundles'
    manifests, without lilv (`library.declared_plugins`)."""
    from .library import declared_plugins

    where: dict[str, Path] = {}
    wanted = set(uris)
    for folder in folders:
        if not wanted - where.keys():
            break
        for uri in declared_plugins(folder).keys() & wanted:
            where.setdefault(uri, folder)
    return where


def _from_flags(folders: list[Path]) -> str:
    return " ".join(f"--from {folder}" for folder in folders)


def advise(missing: list[str], searched: list[Path], build_into: str) -> tuple[dict[str, str], list[str]]:
    """For URIs the rig needs and `searched` did not provide: what to say
    beside each (`{uri: note}`), and the lines after the list that say what
    would fix them. A URI in another standard LV2 folder (`~/.lv2`, the
    multiarch folder when `--from` left it out) names the folder and the
    `--from`s that would take it from there; one this repository builds
    names its script, run with `build_into`; the amp says it is carried by
    hand; anything else is not installed."""
    searched_at = {folder.resolve() for folder in searched}
    others = [d for d in standard_lv2_dirs() if d.resolve() not in searched_at]
    elsewhere = locate(missing, others)
    notes: dict[str, str] = {}
    for uri in missing:
        if uri in elsewhere:
            notes[uri] = f"installed in {elsewhere[uri]}, which was not searched"
        elif uri in BUILT:
            notes[uri] = f"built here, not packaged: {BUILT[uri]} {build_into} builds it"
        elif uri in AMP_PLUGIN_URIS.values():
            notes[uri] = ("the neural amp, which no distribution packages: build it from "
                          "mikeoliphant/neural-amp-modeler-lv2 and put its bundle in plugins/ by hand, "
                          "or --waive it (docs/reference/plugins.md)")
        else:
            notes[uri] = "in no standard LV2 folder: install the package that provides it"
    then = []
    if any(uri not in elsewhere and uri not in BUILT and uri not in AMP_PLUGIN_URIS.values()
           for uri in missing):
        looked = _distinct([*searched, *others])
        then.append(f"Looked in {', '.join(map(str, looked))}.")
    extra = [d for d in others if d in set(elsewhere.values())]
    if extra:
        then.append(
            f"To take {'it' if len(elsewhere) == 1 else 'them'} from {', '.join(map(str, extra))} as well, "
            f"pass {_from_flags([*searched, *extra])} (every folder: --from replaces the defaults)."
        )
    return notes, then


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
    return {uri: bundle for uri, (bundle, _) in _plugins_and_binaries(lv2_path).items()}


def binaries_on(lv2_path: str) -> dict[str, Path | None]:
    """Every plugin URI visible with `LV2_PATH=lv2_path`, mapped to the file
    its `lv2:binary` names (None where it names none)."""
    return {uri: binary for uri, (_, binary) in _plugins_and_binaries(lv2_path).items()}


def _file_path(uri: str) -> Path:
    from urllib.parse import unquote, urlparse

    return Path(unquote(urlparse(uri).path))


def _plugins_and_binaries(lv2_path: str) -> dict[str, tuple[Path, Path | None]]:
    # Private to probe, but it is the one place that turns a missing python3-lilv
    # into an actionable message, and duplicating that would let them drift.
    from .probe import _each, _world

    with _lv2_path(lv2_path):
        world = _world()
    found: dict[str, tuple[Path, Path | None]] = {}
    for plugin in _each(world.get_all_plugins()):
        bundle = str(plugin.get_bundle_uri()).replace("file://", "").rstrip("/")
        library = plugin.get_library_uri()
        binary = _file_path(str(library)) if library is not None and str(library) else None
        found[str(plugin.get_uri())] = (Path(bundle), binary)
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
    them would copy a shared library once per bundle that points at it. Each
    is then pointed where it led from the source (`_relinked`): into the
    copy, when it led into one of the bundles copied; at its target by its
    absolute path, when it led anywhere else by a relative path. Ubuntu's
    calf-plugins ships `calf.lv2/calf.so` as `../../x86_64-linux-gnu/calf/calf.so`;
    copied as it was, every Calf plugin's binary was missing from plugins/
    (#177). A link that leads nowhere, or round in a loop, is refused, naming
    the bundle and the link, since Sushi could not load what it stands for.
    """
    to.mkdir(parents=True, exist_ok=True)
    roots = {bundle.resolve(): to / bundle.name for bundle in bundles}
    for bundle in bundles:
        shutil.copytree(bundle, to / bundle.name, symlinks=True)
        _relinked(bundle, to / bundle.name, roots)
    _check_copies([to / bundle.name for bundle in bundles])
    archive = to / ARCHIVE_DIR
    archive.mkdir(exist_ok=True)
    (archive / "manifest.ttl").write_text(ARCHIVE_MANIFEST)


def _links(folder: Path) -> list[Path]:
    """Every symlink under `folder`, files and folders alike, none followed."""
    found = []
    for here, dirs, files in os.walk(folder, followlinks=False):
        for name in dirs + files:
            path = Path(here) / name
            if path.is_symlink():
                found.append(path)
    return sorted(found)


def _target(link: Path, where: str) -> Path:
    """Where `link` leads, resolved; `PluginsError` if it leads nowhere or loops.

    `Path.resolve` raises RuntimeError on a loop on Python 3.12 and OSError on
    3.13; a missing target is FileNotFoundError with `strict`."""
    try:
        return link.resolve(strict=True)
    except (RuntimeError, OSError) as exc:
        reason = "goes round in a loop" if isinstance(exc, RuntimeError) or "loop" in str(exc).lower() \
            else "leads to nothing"
        raise PluginsError(f"{where}: the link {link.name} ({link} -> {os.readlink(link)}) {reason}") from exc


def _relinked(source: Path, copy: Path, roots: dict[Path, Path]) -> None:
    """Each symlink in `copy`, a copy of bundle `source`, pointed where it led
    from `source`: relative, into the copy of whichever copied bundle it led
    into (`roots`, each source bundle's resolved path to its copy), or by its
    absolute path to anything else. `PluginsError`, naming the bundle and the
    link, for one that led nowhere or loops."""
    for link in _links(copy):
        original = source / link.relative_to(copy)
        target = _target(original, f"bundle {source}")
        inside = next(((root, dest) for root, dest in roots.items()
                       if target == root or root in target.parents), None)
        if inside is not None:
            root, dest = inside
            wanted = os.path.relpath(dest / target.relative_to(root), link.parent)
        elif os.path.isabs(os.readlink(original)):
            continue
        else:
            wanted = str(target)
        if os.readlink(link) != wanted:
            link.unlink()
            link.symlink_to(wanted)


def _check_copies(copies: list[Path]) -> None:
    """Every link in `copies` leads to something, once all of them are in
    place (a link may lead into a sibling copied after it)."""
    for copy in copies:
        for link in _links(copy):
            _target(link, f"the copy {copy}")


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
    `plugins/` that looks plausible is worse than none. The copy is built
    beside `to`, in a folder of its own, and only renamed into place once it
    is whole, so a copy that fails (a link that leads nowhere, a full disk)
    leaves `to` as it was, `--replace` or not. The swap is two renames, the
    old `to` aside to `.<name>.previous`, then the new one into place; a
    failure between them puts the old one back, and a run that died there
    (power, kill -9) leaves it to the next run, which puts it back before
    anything else and then goes on as for any `to` that is there.

    Refuses to write into a non-empty `to` unless `replace`, because the target
    is a folder someone may have put things in by hand, and deleting it on a
    mistyped path is not a recoverable mistake.
    """
    plan = decide(manifest, used, discover(from_dirs), waived)
    if not plan.ok:
        notes, then = advise(plan.missing, from_dirs, "DIR (then --from DIR)")
        raise PluginsError(describe_failure(plan, ", ".join(map(str, from_dirs)), notes, then))

    staging = to.parent / f".{to.name}.assembling"
    previous = to.parent / f".{to.name}.previous"
    restored = ""
    if previous.exists() and not to.exists():
        # A run stopped between its two renames (power, kill -9): `previous`
        # is the plugins/ it was replacing, and the only copy of it. Put it
        # back before anything else, never delete it (review of PR #186).
        previous.rename(to)
        restored = (f" (it was {previous}, left by a run that stopped while swapping folders, "
                    "and has been put back)")
    full = to.exists() and any(to.iterdir())
    if full and not replace:
        raise PluginsError(
            f"{to} already exists and is not empty{restored}. Pass --replace to delete "
            "it and rebuild, or choose another --to."
        )
    if staging.exists():
        shutil.rmtree(staging)  # a copy that never finished: never anyone's only copy
    if previous.exists():
        # `to` exists, so this is what a finished swap had left to delete.
        shutil.rmtree(previous)
    try:
        copy_bundles(plan.bundles, staging)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    if to.exists():
        to.rename(previous)
    try:
        staging.rename(to)
    except BaseException:
        if previous.exists() and not to.exists():
            previous.rename(to)
        shutil.rmtree(staging, ignore_errors=True)
        raise
    shutil.rmtree(previous, ignore_errors=True)
    return plan


def add(
    manifest: list[str],
    used: set[str],
    from_dirs: list[Path],
    plugins_dir: Path,
    waived: set[str] | frozenset[str] = frozenset(),
) -> Plan:
    """Copy into `plugins_dir`, as it is, the bundles from `from_dirs` that
    provide what the rig needs and `plugins_dir` does not: what a rig machine
    with a hand-carried amp and built bundles in its `plugins/` runs when
    the manifest grows. Nothing already there is touched.

    Refused, copying nothing, when a required URI resolves from neither
    (unless waived), or when a bundle to add has the name of one already in
    `plugins_dir`: that one provides something else, and replacing it is
    `assemble --replace`'s decision, not this one's. Each bundle is copied
    into a staging folder inside `plugins_dir` whose manifest declares
    nothing, as the build scripts stage theirs, and all are renamed into
    place only once every copy is whole."""
    if not plugins_dir.is_dir():
        raise PluginsError(
            f"{plugins_dir} does not exist. Build it with `sushi-rig plugins assemble`."
        )
    present = plugins_on(str(plugins_dir))
    found = discover(from_dirs)
    providers = {**found, **present}
    plan = decide(manifest, used, providers, waived)
    if not plan.ok:
        notes, then = advise(plan.missing, from_dirs, str(plugins_dir))
        raise PluginsError(describe_failure(plan, f"{plugins_dir} and {', '.join(map(str, from_dirs))}",
                                            notes, then))
    # By where `decide` took each from, not by resolving its path: a bundle in
    # plugins/ may be a link to one elsewhere (the amp, say), and resolved it
    # would look like one to add, and clash with itself (review of PR #186).
    already = set(present.values())
    new = [b for b in plan.bundles if b not in already]
    taken = [b for b in new if (plugins_dir / b.name).exists()]
    if taken:
        raise PluginsError(
            "these bundles provide what the rig needs, but plugins/ already holds a bundle of the same "
            "name that does not provide it; nothing was copied:\n"
            + "\n".join(f"  {b} (beside {plugins_dir / b.name})" for b in taken)
        )
    staging = plugins_dir / ".adding"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir()
    (staging / "manifest.ttl").write_text(ARCHIVE_MANIFEST)
    try:
        # Siblings in the staging folder, as they will be in plugins_dir, so a
        # link from one new bundle into another means the same in both places.
        roots = {b.resolve(): staging / b.name for b in new}
        for bundle in new:
            shutil.copytree(bundle, staging / bundle.name, symlinks=True)
            _relinked(bundle, staging / bundle.name, roots)
        _check_copies([staging / bundle.name for bundle in new])
        for bundle in new:
            (staging / bundle.name).rename(plugins_dir / bundle.name)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return dataclasses.replace(plan, bundles=sorted(new))


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
    found = _plugins_and_binaries(str(plugins_dir))
    plan = decide(manifest, used, {uri: bundle for uri, (bundle, _) in found.items()}, waived)
    if not plan.ok:
        raise PluginsError(describe_failure(plan, f"{plugins_dir} (LV2_PATH set to it alone)"))
    problems = broken_files(plugins_dir, {uri: found[uri][1] for uri in set(manifest) | set(used) if uri in found})
    if problems:
        raise PluginsError(f"{plugins_dir}: every URI resolves, but Sushi could not load all of them:\n"
                           + "\n".join(f"  {p}" for p in problems))
    return plan


def broken_files(plugins_dir: Path, binaries: dict[str, Path | None]) -> list[str]:
    """What the Turtle alone does not show (#177): each link under
    `plugins_dir` that leads nowhere or loops, `archive/` aside, since it is
    off the path; and each of `binaries` (a required URI's `lv2:binary`)
    that names no file. Ubuntu's calf.lv2 copied with a plain `cp -r` passes
    lilv and fails both."""
    problems = []
    for link in _links(plugins_dir):
        if ARCHIVE_DIR in link.relative_to(plugins_dir).parts[:1]:
            continue
        try:
            _target(link, f"{link.parent}")
        except PluginsError as exc:
            problems.append(str(exc))
    for uri, binary in sorted(binaries.items()):
        if binary is None:
            problems.append(f"{uri} names no lv2:binary")
        elif not binary.is_file():
            problems.append(f"{uri}: its binary {binary} is not a file")
    return problems


# --- before a start -----------------------------------------------------------


def preflight(config: dict[str, Any], plugins_dir: Path, amp: bool = True) -> tuple[list[str], list[str]]:
    """What a start of `config` would miss in `plugins_dir`, the only folder
    on the rig's `LV2_PATH`: `(the URIs Sushi would load that no bundle there
    declares, the amp's URI if it is missing and the amp will start)`.

    Sushi given a chain it cannot build quits with "Failed to load tracks
    from the Json config file" and nothing else, naming no plugin (the
    synth-lead start on the owner's laptop, 2026-11-19); asked first, the
    rig can name every one and say how to get it. The amp is kept apart
    because Carla, not Sushi, loads it: without it the rig plays, dry, as it
    does when Carla exits (`rig.startup_exit_problem`).

    Read from the bundles' manifests, without lilv (`library.declared_plugins`),
    so it costs a fraction of a second and runs where lilv is not
    installed. What that finds missing is asked of lilv where lilv is
    installed, and only what lilv cannot find either is missing: a manifest
    the reader cannot parse must not stop a rig Sushi would load. It checks
    that each URI is declared, not that its binary loads: that is `check`'s."""
    import importlib.util

    from .library import declared_plugins

    chain = config_uris({key: value for key, value in config.items() if key != AMP_KEY})
    amp_uris = config_uris(config) - chain if amp else set()
    absent = (chain | amp_uris) - declared_plugins(plugins_dir).keys()
    if absent and plugins_dir.is_dir() and importlib.util.find_spec("lilv") is not None:
        absent -= plugins_on(str(plugins_dir)).keys()
    return sorted(chain & absent), sorted(amp_uris & absent)


def preflight_refusal(
    name: str, missing: list[str], plugins_dir: Path, root: Path, *, switch_from: str | None = None,
) -> str:
    """Why rig `name` is not started, for `missing` (`preflight`'s first
    list, not empty): each URI with where it is or how it is made, then the
    commands that put them all in `plugins_dir`, in the order to run them.
    The build scripts come before `plugins add`, which refuses, copying
    nothing, while anything the rig needs is still missing. `switch_from`,
    the rig playing, words it as a switch to `name` that was not made."""
    sources = default_sources()
    packaged = locate(missing, sources)
    notes, _ = advise([uri for uri in missing if uri not in packaged], sources, str(plugins_dir))
    heading = f"Not switched to {name}" if switch_from is not None else f"{name} was not started"
    nothing = (f"Nothing was changed: {switch_from} plays on." if switch_from is not None
               else "Nothing was started.")
    again = "switch to it again" if switch_from is not None else "start the rig again"
    lines = [
        f"{heading}: {len(missing)} plugin(s) it loads are not in {plugins_dir}, "
        "the only folder Sushi loads plugins from (LV2_PATH), so Sushi would quit at startup "
        "saying only \"Failed to load tracks from the Json config file\":",
    ]
    for uri in missing:
        if uri in packaged:
            lines.append(f"  {uri}: installed in {packaged[uri]}, which `sushi-rig plugins add` copies from")
        else:
            lines.append(f"  {uri}: {notes[uri]}")
    if not plugins_dir.is_dir():
        lines.append(f"{plugins_dir} does not exist. Build it as docs/reference/plugins.md says "
                     f"(`sushi-rig plugins assemble`, with the plugins built here), then {again}.")
        return "\n".join(lines)
    searched_at = {folder.resolve() for folder in sources}
    others = [d for d in standard_lv2_dirs() if d.resolve() not in searched_at]
    elsewhere = locate([uri for uri in missing if uri not in packaged], others)
    fix = []
    for script in dict.fromkeys(BUILT[uri] for uri in missing if uri in BUILT and uri not in packaged
                                and uri not in elsewhere):
        fix.append(f"{root / script} {plugins_dir}")
    uninstalled = any(uri not in packaged and uri not in elsewhere and uri not in BUILT
                      and uri not in AMP_PLUGIN_URIS.values() for uri in missing)
    extra = [d for d in others if d in set(elsewhere.values())]
    if extra:
        fix.append(f"sushi-rig plugins add {_from_flags([*sources, *extra])}")
    else:
        fix.append("sushi-rig plugins add")
    lines.append(
        f"{nothing} To fix it, "
        + ("install the package that provides each one in no standard LV2 folder, then " if uninstalled else "")
        + ("run these, in order" if len(fix) > 1 else "run this") + f", then {again}:"
    )
    lines += [f"  {command}" for command in fix]
    return "\n".join(lines)


def preflight_amp_warning(missing: list[str], plugins_dir: Path) -> str:
    """The warning for the amp's URI missing from `plugins_dir` (`preflight`'s
    second list, not empty). A warning, not a refusal: a rig with no amp
    still plays, which `up` has always preferred to no rig."""
    return (
        f"the amp's plugin ({', '.join(missing)}) is not in {plugins_dir}, so Carla cannot load it "
        "and the rig will have NO AMP: the guitar runs dry. No distribution packages it: it is built "
        "from mikeoliphant/neural-amp-modeler-lv2 and put in plugins/ by hand "
        "(docs/reference/plugins.md). --no-amp starts the rig without one"
    )
