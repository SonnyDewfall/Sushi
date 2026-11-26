"""sushi-rig — author an LV2 rig live in Sushi, then capture it back as a config file.

The commands are described in docs/reference/commands.md, and the workflow they
came from in docs/reference/v1-workflow.md. docs/design/IMPLEMENTATION_BRIEF.md
is the original brief, kept as history; where it and the code disagree, the
code is right.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .emit import emit
from .live import DEFAULT_GRPC_ADDRESS
from .rig import DEFAULT_CONFIG_NAME
from .spec import RigSpec


def write_json(path: Path, payload) -> None:
    path.write_text(json.dumps(payload, indent=2) + "\n")
    print(f"wrote {path}")


_SUSHI_HELP = "Sushi binary [default: ./sushi in the rig checkout, the one `up` runs]"


def _sushi_bin(args: argparse.Namespace) -> str:
    """The Sushi to run: `--sushi`, else the rig's own `./sushi`.

    Not whatever `sushi` is on PATH: a command should use the Sushi the rig
    actually plays through, and with a PATH default it failed with "No such
    file" on any machine where Sushi is not also installed system-wide. The
    default is resolved from the checkout, not the working directory, so it
    does the same thing from anywhere.
    """
    from .paths import checkout_root

    return args.sushi or str(checkout_root() / "sushi")


_FROM_HELP = (
    "LV2 directory to take bundles from. Repeatable; searched in order, as an LV2_PATH is: "
    "of two bundles declaring one URI, lilv takes the higher version, and the earlier "
    "directory between equal ones. Replaces the defaults, so name every directory to search "
    "[default: /usr/lib/lv2, then the multiarch /usr/lib/<triplet>/lv2 (Debian, Ubuntu), "
    "each where it exists]"
)


def _plugins(args: argparse.Namespace) -> int:
    """`plugins assemble`, `add` and `check`, which share how they find what the
    rig requires and differ only in what they do with it."""
    from .paths import checkout_root
    from .plugins import (
        PluginsError, add, assemble, check, config_uris, default_sources, read_manifest,
    )
    from .rigfile import RigFileError
    from .verify import config_to_check, configs_to_check

    root = checkout_root()
    manifest_path = args.manifest or root / "plugin-manifest.txt"
    try:
        manifest = read_manifest(manifest_path.read_text())
    except OSError as exc:
        sys.exit(f"cannot read the plugin manifest: {exc}")

    used: set[str] = set()
    if args.config_dir:
        for config in sorted(args.config_dir.glob("*.json")):
            used |= config_uris(json.loads(config.read_text()), str(config))
    else:
        # Every rig of channels, compiled as Sushi loads it (the v1 configs,
        # config/*.json, until they were retired, #218).
        for path in configs_to_check(root):
            try:
                used |= config_uris(config_to_check(path), str(path))
            except RigFileError as exc:
                sys.exit(f"{path.name} does not compile, so what it needs cannot be said:\n  "
                         + "\n  ".join(exc.problems))

    waived = set(args.waive)
    try:
        if args.plugins_command == "assemble":
            to = args.to or root / "plugins"
            plan = assemble(
                manifest, used, args.from_dirs or default_sources(), to, waived,
                args.replace,
            )
            print(f"copied {len(plan.bundles)} bundle(s) into {to}")
        elif args.plugins_command == "add":
            plugins_dir = args.plugins_dir or root / "plugins"
            plan = add(manifest, used, args.from_dirs or default_sources(), plugins_dir, waived)
            names = ", ".join(b.name for b in plan.bundles) or "nothing: it had everything"
            print(f"added to {plugins_dir}: {names}")
        else:
            plugins_dir = args.plugins_dir or root / "plugins"
            plan = check(manifest, used, plugins_dir, waived)
            print(
                f"{plugins_dir}: {plan.resolved} required plugin URI(s) all resolve "
                "with LV2_PATH set to it alone"
            )
    except PluginsError as exc:
        sys.exit(str(exc))

    # Neither is a failure of this command, but both mean the next person to
    # trust the output is trusting something untrue, so they are not buried.
    for uri in plan.waived:
        print(f"waived, not provided: {uri}", file=sys.stderr)
    if plan.undeclared:
        print(
            f"{manifest_path.name} is out of date: {len(plan.undeclared)} URI(s) are used "
            "by configs but not listed:",
            file=sys.stderr,
        )
        for uri in plan.undeclared:
            print(f"  {uri}", file=sys.stderr)
    return 0


def _machine(args: argparse.Namespace) -> int:
    """`machine check`: validate a machine file on its own.

    Separate from `patchbay generate` because that needs a rig whose tracks name
    roles, and a new machine's file should be checkable before there is anything
    to generate. (`config/src/guitar_vocal.yaml` does name roles, and
    `Patchbay/rig.qpwgraph` is generated from it.)
    """
    from .machine import MachineError, MachineSpec, machine_path
    from .paths import checkout_root

    path = args.file or machine_path(checkout_root())
    try:
        machine = MachineSpec.load(path)
    except MachineError as exc:
        sys.exit(str(exc))
    problems = machine.validate()
    for problem in problems:
        print(f"{path.name}: {problem}")
    if problems:
        print(f"\n{len(problems)} problem(s)", file=sys.stderr)
        return 1
    ports = machine.role_ports()
    print(f"{path.name}: ok, {len(ports)} role(s): {', '.join(sorted(ports))}")
    return 0


def _as_typed(target: Path, root: Path) -> str:
    """A config path as the user would type it from the rig root.

    `configs_to_check` returns absolute paths, and printing `target.name` in a
    "run this command" hint produced a command that cannot find its config.
    """
    try:
        return str(target.relative_to(root))
    except ValueError:
        return str(target)


def _patchbay(args: argparse.Namespace) -> int:
    """`patchbay check` and `patchbay generate`."""
    from .machine import MachineError, MachineSpec, machine_path
    from .patchbay import (
        PatchbayError, check_nodes, live_node_names, parse_patchbay, patchbay_for,
        read_pw_dump,
    )
    from .paths import checkout_root

    root = checkout_root()

    if args.patchbay_command == "check":
        path = args.file or root / "Patchbay" / "rig.qpwgraph"
        # The read and the live query are separate steps so that a failure in
        # one is not reported as the other: a pw-dump that cannot be executed
        # raises OSError too, and "cannot read rig.qpwgraph" would send the
        # reader to the wrong file.
        try:
            text = path.read_text()
        except OSError as exc:
            sys.exit(f"cannot read {path}: {exc}")
        try:
            links = parse_patchbay(text)
            result = check_nodes(links, live_node_names(read_pw_dump()))
        except PatchbayError as exc:
            sys.exit(f"{path.name}: cannot check\n" + "\n".join(f"  {p}" for p in exc.problems))

        for node, node_links in result.missing.items():
            print(f"{path.name}: MISSING no live node {node!r} ({len(node_links)} link(s))")
            for link in node_links:
                print(f"   {link.out_port} -> {link.in_port}")
        for node, node_links in result.not_running.items():
            print(
                f"{path.name}: rig down: node {node!r} ({len(node_links)} link(s)) is "
                "started by the rig, so its absence says nothing about the patchbay"
            )
        if result.not_running:
            # Printed whenever anything went unchecked, not only when nothing
            # else was found. In the run that prompted all this there *was* a
            # real finding alongside, and an elif here meant the reader was
            # never told that three nodes had been skipped.
            print(
                f"\n{len(result.not_running)} node(s) could not be checked because "
                "the rig is not running. Run `sushi-rig up` and check again to "
                "cover those links.",
                file=sys.stderr,
            )
        if result.missing:
            print(
                f"\n{len(result.missing)} node(s) the patchbay names are definitely "
                "not in the graph. qpwgraph keeps their links and never connects them.",
                file=sys.stderr,
            )
        elif not result.not_running:
            print(f"{path.name}: all {len(links)} link(s) name a node that exists")
        # Exit 1 means a node is definitely gone, and nothing less. A rig that is
        # merely down exits 0: the check was run at the wrong moment, which is
        # not a fault in the patchbay and must not fail a script.
        return 1 if result.missing else 0

    # generate
    import yaml

    from .rigfile import RigFileError, spec_of

    try:
        # Routing only: the patchbay wires ports and never loads the chains.
        rig = spec_of(args.rig, routing_only=True)
        machine_file = args.machine or machine_path(root)
        machine = MachineSpec.load(machine_file)
    except RigFileError as exc:
        sys.exit(f"{args.rig}:\n  " + "\n  ".join(exc.problems))
    except (MachineError, OSError, ValueError, yaml.YAMLError) as exc:
        sys.exit(f"{args.rig}: {exc}" if isinstance(exc, yaml.YAMLError) else str(exc))
    problems = [f"{args.rig.name}: {p}" for p in rig.validate()]
    problems += [f"{machine_file.name}: {p}" for p in machine.validate()]
    if problems:
        sys.exit("\n".join(problems))

    try:
        dump = None if args.offline else read_pw_dump()
        xml, warnings = patchbay_for(
            rig, machine, dump, tuner=not args.no_tuner,
            rig_name=args.rig.name, machine_name=machine_file.name,
        )
    except PatchbayError as exc:
        sys.exit("\n".join(exc.problems))
    for warning in warnings:
        # Silence is the one thing a missing tap must not be: nothing else in
        # either file mentions fmit, so it would look like a broken tuner.
        print(f"warning: {warning}. Pass --no-tuner to stop saying so", file=sys.stderr)

    if args.out:
        args.out.write_text(xml)
        print(f"wrote {args.out}", file=sys.stderr)
    else:
        sys.stdout.write(xml)
    return 0


def _controller_for(rig, machine_file: Path | None) -> dict | None:
    """The machine's description of the controller a rig's MIDI map names, or
    None when the rig has no map. A map whose controller this machine does
    not describe is an exit with the reason, never a config without it."""
    from .machine import MachineError, rig_controller

    try:
        return rig_controller(rig, machine_file)
    except MachineError as exc:
        sys.exit(str(exc))


def _emit_any(path: Path, state: dict | None, machine_file: Path | None = None) -> dict:
    """A config from either kind of rig file: channels and a master (#30), or
    tracks spelt out by hand. The file says which, by its `kind`."""
    from .emit import EmitError
    from .rigfile import Rig, RigFileError, emit_rig, load_any

    from .paths import checkout_root

    try:
        rig = load_any(path)
        controller = _controller_for(rig, machine_file) if isinstance(rig, Rig) else None
        config = emit_rig(rig, state, controller=controller) if isinstance(rig, Rig) else emit(rig, state)
    except (RigFileError, EmitError) as exc:
        sys.exit(f"{path}:\n  " + "\n  ".join(exc.problems))
    # Where this config came from, so `up` can find the rig SAVE saves from
    # without guessing. Only within the checkout: a path outside it is not one
    # another machine can follow.
    try:
        source = Path(path).resolve().relative_to(checkout_root())
    except ValueError:
        return config
    meta = dict(config.get("_meta") or {})
    meta["source"] = source.as_posix()
    config["_meta"] = meta
    return config


def _agent_host(phone: bool, host: str) -> str:
    """The address `sushi-rig agent` binds: `--phone` is `--host 0.0.0.0`
    (#181), and with another `--host` beside it the two disagree, which is
    refused rather than one of them quietly ignored."""
    from .agent_client import DEFAULT_HOST

    if not phone:
        return host
    if host not in (DEFAULT_HOST, "0.0.0.0"):
        sys.exit(f"sushi-rig: --phone listens on every address (0.0.0.0), and --host {host} says one; "
                 "give one or the other")
    return "0.0.0.0"


def _convert(args: argparse.Namespace) -> int:
    from .convert import ConvertError, convert, parse_inputs
    from .paths import checkout_root
    from .rigfile import write_rig

    try:
        config = json.loads(args.config.read_text())
        meta = config.get("_meta") if isinstance(config, dict) else None
        name = args.name or (meta or {}).get("name") or args.config.stem
        out = args.out or checkout_root() / "config" / "rigs" / f"{name}.yaml"
        if out.exists() and not args.force:
            # A rig file may carry comments and edits its config never had.
            print(f"FAIL {out} exists; --force updates it, keeping its comments")
            return 1
        rig = convert(config, name, parse_inputs(args.input))
    except ConvertError as exc:
        for problem in exc.problems:
            print(f"FAIL {args.config}: {problem}")
        return 1
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        print(f"FAIL {args.config}: {type(exc).__name__}: {exc}")
        return 1
    try:
        source = args.config.resolve().relative_to(checkout_root())
    except ValueError:
        source = args.config
    try:
        write_rig(out, rig, source=str(source))
    except (OSError, ValueError) as exc:
        print(f"FAIL {out}: {exc}")
        return 1
    print(f"wrote {out}")
    return 0


def _probes(args: argparse.Namespace) -> int:
    from .probes import ProbeError, plan_probes

    import yaml

    from .rigfile import RigFileError, spec_of

    try:
        rig = spec_of(args.rig)
    except RigFileError as exc:
        for problem in exc.problems:
            print(f"FAIL {args.rig}: {problem}")
        return 1
    except (OSError, ValueError, yaml.YAMLError) as exc:
        print(f"FAIL {args.rig}: {exc}")
        return 1
    problems = [f"{args.rig.name}: {p}" for p in rig.validate()]
    if not problems:
        try:
            plan = plan_probes(rig, amp=args.amp)
        except ProbeError as exc:
            problems = exc.problems
    if problems:
        for problem in problems:
            print(f"FAIL {problem}")
        return 1
    for point in plan.points:
        expect = "  must add harmonics" if point.nonlinear else ""
        print(f"{point.name}\n  inject  {', '.join(point.inject)}")
        for source, sink in point.links:
            print(f"  link    {source} -> {sink}")
        print(f"  listen  {point.capture}{expect}")
        if point.track is not None:
            # The track is what makes the injection safe to run with the
            # instrument plugged in, so the listing says what it carries.
            chains = " then ".join(f"{name}'s chain" for name in point.track.copy_of)
            print(f"  track   {point.track.name}: a copy of {chains} "
                  f"({len(point.track.plugins)} plugins)")
        if point.beside:
            print(f"  beside  {point.beside}: the instrument feeds this too, so it is in "
                  "the measurement and the player hears it; keep it quiet")
    for note in plan.not_checked:
        print(f"NOT CHECKED {note}")
    return 0


def _take(args: argparse.Namespace) -> int:
    """`take check`: is this folder a whole, honest take, and how do its stems line up."""
    from .take import TakeError, check_take, describe_alignment, load_take, repair_take

    if args.take_command == "repair":
        try:
            repaired, failed = repair_take(args.folder)
        except TakeError as exc:
            sys.exit("\n".join(exc.problems))
        for name, frames in repaired.items():
            print(f"{name}: {frames} frames")
        for name, why in failed.items():
            print(f"{name}: NOT REPAIRED — {why}", file=sys.stderr)
        print(f"{args.folder.name}: {len(repaired)} stem(s) repaired; `sushi-rig take check` next")
        return 1 if failed else 0

    try:
        take = load_take(args.folder)
    except TakeError as exc:
        sys.exit("\n".join(exc.problems))
    problems = check_take(take)
    for problem in problems:
        print(f"  {problem}")
    # Recomputed from the offsets, not read back from the file: a number
    # corrected by hand must not be undercut by the sentence that was there.
    alignment = describe_alignment(take)
    if alignment:
        print(f"{args.folder.name}: {alignment}")
    print(
        f"{args.folder.name}: {len(problems)} problem(s)" if problems
        else f"{args.folder.name}: ok, {len(take.stems)} stem(s)"
    )
    print(
        f"the rig that made it: `sushi-rig verify {args.folder / 'config.json'}`",
        file=sys.stderr,
    )
    return 1 if problems else 0


def _references_of(ref_stem: str, stem: str, is_rig: bool) -> bool:
    """Whether a reference named `ref_stem` is one the target `stem` would
    produce: a rig's `<stem>` or `<stem>.<channel>`, a v1 config's the same
    with `.v1-mono` after it. A rig that cannot compile must not exempt its v1
    config's references from the orphan check, nor the reverse (review)."""
    from .render import V1_REFERENCE_SUFFIX

    if ref_stem.endswith(V1_REFERENCE_SUFFIX):
        if is_rig:
            return False
        ref_stem = ref_stem[: -len(V1_REFERENCE_SUFFIX)]
    elif not is_rig:
        return False
    return ref_stem == stem or ref_stem.startswith(f"{stem}.")


def _nulltest(args: argparse.Namespace) -> int:
    """`nulltest`: has any config, or any channel of one, changed its sound.

    Three kinds of target, each with its own references (`--all` takes all
    three):

    - a v1 config, `config/*.json` (none since they were retired to
      `config/retired/v1/`, #218), rendered whole when Sushi's offline
      frontend can host it and one channel at a time when it cannot
      (`render.channels_of`), under `config/sound/<name>.v1-mono.yaml`;
    - a frozen rig, `tool/tests/fixtures/rigs/*.yaml`, through its master one
      channel at a time (`render.rig_channels`), under
      `tool/tests/fixtures/sound/<name>.yaml`;
    - a committed rig, `config/rigs/*.yaml`, the same way, under
      `config/sound/<name>.yaml`.

    The first two are held exactly: a reference missing, a sound changed or an
    orphaned reference fails. A committed rig is the owner's to change, so its
    reference is compared exactly only while it was made from the rig as it is
    (`nulltest.staleness`, #144); one that was not, or no reference at all, is
    STALE, a warning, once the render is finite, two-sided and balanced
    (`nulltest.heard_findings`), which every rig render is held to whatever
    its reference says; a v1 config's render, to its left side not being
    silent (`nulltest.v1_findings`). No reference is written of a render
    that fails either. An orphaned reference of a committed rig is a warning.
    """
    import tempfile

    import yaml

    from .nulltest import (
        ORPHAN_PREFIX, STALE_PREFIX, MadeFrom, compare, delay_margin_warnings, delay_margins, from_dict,
        keep_wider, made_from_of, played_findings, rig_findings, staleness, to_dict,
    )
    from .paths import checkout_root
    from .render import (
        FROZEN_SOUND, RETRIED, RETRIED_PREFIX, SOUND_DIR, V1_REFERENCE_SUFFIX, RenderError, Unplayable,
        calibrate,
        channels_of, frozen_reference_path, frozen_rigs_to_check, is_frozen, measure, offline_problem,
        reference_path, rig_channels, rig_file_hash, rigs_to_check, v1_reference_path,
    )
    from .rigfile import RigFileError

    root = checkout_root()
    if args.check_all:
        # A v1 config left in config/ is still held to its reference; none is
        # since they were retired to config/retired/v1/ (#218).
        targets = ([(path, "v1") for path in sorted((root / "config").glob("*.json"))]
                   + [(path, "rig") for path in rigs_to_check(root)]
                   + [(path, "frozen") for path in frozen_rigs_to_check(root)])
    elif args.config:
        kind = ("frozen" if is_frozen(root, args.config)
                else "rig" if args.config.suffix in (".yaml", ".yml") else "v1")
        targets = [(args.config, kind)]
    else:
        sys.exit("give a config to null-test, or --all")
    if args.calibrate and args.candidates is not None:
        sys.exit("--candidates writes what --calibrate would, for the stale rigs alone: give one or the other")

    sushi = _sushi_bin(args)
    failed = 0
    checked = 0
    unrenderable: list[str] = []
    # Every render retried after gx_detune_'s known NaN (`render.measure`),
    # printed beside its channel and again at the end, every time.
    retried: list[str] = []
    # Each committed rig's channel with no reference made from the rig as it
    # is, and each orphaned reference of one: warnings, said at the end too.
    stale: list[str] = []
    orphans: list[str] = []
    # The references each folder's targets expect, for its orphan check.
    expected: dict[str, set[str]] = {"sound": set(), "frozen": set()}
    # (stem, kind) of each target that could not be expanded into channels:
    # its own references are kept out of the orphan check, and only its own.
    unexpanded: list[tuple[str, str]] = []

    def cannot_check(label: str, exc: Exception) -> None:
        nonlocal failed
        # The type as well as the message: a KeyError's message is the bare key,
        # which on its own reads like nothing at all.
        print(f"{label}: CANNOT CHECK — {type(exc).__name__}: {exc}", file=sys.stderr)
        failed += 1

    def write_reference(path: Path, fp, tolerance, made) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.dump(to_dict(fp, tolerance, made), default_flow_style=False, sort_keys=False))

    def say_delay_margins(label: str, fp) -> None:
        # How near the delay written came to another answer (#143): every
        # reference made says it, and one within a margin of the floor warns,
        # since its delay could move between renders with nothing changed.
        margins = delay_margins(fp)
        if margins is not None:
            print(f"   {margins}")
        for warning in delay_margin_warnings(fp):
            print(f"{label}: WARNING, {warning}", file=sys.stderr)

    def committed_tolerance(ref_file: Path):
        if not ref_file.exists():
            return None
        try:
            return from_dict(yaml.safe_load(ref_file.read_text()))[1]
        except (ValueError, KeyError, TypeError, AttributeError, yaml.YAMLError):
            # An unreadable reference has no tolerance worth keeping; this
            # calibration replaces it whole.
            return None

    with tempfile.TemporaryDirectory(prefix="sushi-rig-nulltest-") as tmp:
        for target, kind in targets:
            is_rig = kind != "v1"
            current = None
            try:
                if is_rig:
                    channels = rig_channels(target)
                    if kind == "rig":
                        current = rig_file_hash(target)
                else:
                    channels = channels_of(json.loads(target.read_text()), target)
            except (RigFileError, ValueError, OSError, TypeError, AttributeError, yaml.YAMLError) as exc:
                # TypeError and AttributeError too: a config that is a list, or
                # whose tracks are null, must cost one line, not the whole run
                # and the orphan check after it.
                unexpanded.append((target.stem, kind))
                checked += 1
                cannot_check(f"frozen {target.name}" if kind == "frozen" else target.name, exc)
                continue

            for channel in channels:
                checked += 1
                label = f"frozen {channel.label}" if kind == "frozen" else channel.label
                if kind == "v1":
                    ref_file = v1_reference_path(root, Path(f"{channel.name}.json"))
                elif kind == "rig":
                    ref_file = reference_path(root, Path(f"{channel.name}.json"))
                else:
                    ref_file = frozen_reference_path(root, channel.name)
                ref_name = ref_file.stem
                expected["frozen" if kind == "frozen" else "sound"].add(ref_name)
                work = Path(tmp) / kind / ref_name
                # Where this channel's retries start, so they are printed beside
                # it whether it passes, differs or cannot be checked.
                already = len(retried)
                if args.calibrate and args.missing and ref_file.exists():
                    # --missing calibrates only what has no reference yet, so
                    # adding a channel does not quietly re-baseline the others,
                    # whose sound may have changed by accident.
                    print(f"{label}: has a reference, left as it is")
                    continue
                try:
                    problem = offline_problem(channel.config)
                    if problem is not None:
                        if ref_file.exists():
                            # It was protected and silently stopped being. A
                            # derived exemption must not be a way to lose one —
                            # nor `--calibrate`, which once reported it as
                            # unrenderable, exited 0 and left the stale
                            # reference to fail the next run.
                            print(
                                f"{label}: NO LONGER RENDERABLE — it has a "
                                f"reference, so it used to be checked, but {problem}. "
                                f"Fix the routing, or if the move was meant, delete "
                                f"{ref_file.relative_to(root)}: its sound can no "
                                f"longer be checked here",
                                file=sys.stderr,
                            )
                            failed += 1
                        else:
                            print(
                                f"{label}: NOT RENDERABLE OFFLINE — {problem}. "
                                "A change in its sound would not fail this check",
                                file=sys.stderr,
                            )
                            unrenderable.append(label)
                        continue

                    if channel.whole:
                        source = target
                    else:
                        # A copy cut down to this channel, named after it so
                        # Sushi's own messages and the fingerprint say which.
                        work.mkdir(parents=True, exist_ok=True)
                        source = work / f"{channel.name}.json"
                        source.write_text(json.dumps(channel.config, indent=2) + "\n")

                    # What every render is held to, whatever its reference
                    # says, and what no reference is made of: two sides,
                    # neither silent, balanced, for a rig; the left side not
                    # silent for a v1 config, which plays that side alone.
                    # A stereo reverb's 4 dB is its own: the channel is also
                    # rendered with its stereo reverbs bypassed, once, and that
                    # render held to 1 dB (re-review of PR #173).
                    rendered_without: list = []  # once per channel, however often asked

                    def render_without(without, work=work, rendered_without=rendered_without):
                        if not rendered_without:
                            work.mkdir(parents=True, exist_ok=True)
                            bypassed = work / "reverbs-bypassed.json"
                            bypassed.write_text(json.dumps(without, indent=2) + "\n")
                            rendered_without.append(measure(bypassed, root, work / "reverbs-bypassed",
                                                            sushi_bin=sushi, retried=retried))
                        return rendered_without[0]

                    def played(fp, is_rig=is_rig, config=channel.config, render_without=render_without):
                        if not is_rig:
                            return played_findings(fp, is_rig)
                        return rig_findings(fp, config, render_without)

                    if args.calibrate:
                        try:
                            fp, measured = calibrate(source, root, work, sushi_bin=sushi,
                                                     retried=retried, refuse=played)
                        except Unplayable as exc:
                            # Through --calibrate, --missing and the job's
                            # failure path alike: a reference made of such a
                            # render would pass every such render after it.
                            failed += 1
                            print(f"{label}: NOT CALIBRATED — {'; '.join(str(f) for f in exc.findings)}",
                                  file=sys.stderr)
                            for note in retried[already:]:
                                print(f"   {note}", file=sys.stderr)
                            continue
                        tolerance = keep_wider(measured, committed_tolerance(ref_file))
                        if tolerance != measured:
                            print(
                                f"{label}: kept the committed tolerance, wider "
                                f"than this calibration's floor — delete the reference "
                                f"first to tighten it",
                                file=sys.stderr,
                            )
                        write_reference(ref_file, fp, tolerance,
                                        MadeFrom(target.name, current) if kind == "rig" else None)
                        print(
                            f"{label}: calibrated — band {tolerance.band_db:.4f} dB, "
                            f"level {tolerance.level_db:.4f} dB, "
                            f"delay {tolerance.delay_samples} samples "
                            f"-> {ref_file.relative_to(root)}"
                        )
                        say_delay_margins(label, fp)
                        for note in retried[already:]:
                            print(f"   {note}")
                        continue

                    reference = tolerance = raw = None
                    if ref_file.exists():
                        # Read whole before anything is decided on it: a
                        # reference that cannot be read fails, for a committed
                        # rig as for any other, and is not taken for none.
                        raw = yaml.safe_load(ref_file.read_text())
                        reference, tolerance = from_dict(raw)
                    why = None
                    if kind == "rig":
                        why = ("it has no reference yet" if reference is None
                               else staleness(made_from_of(raw), current))

                    if why is not None:
                        # #144: a committed rig the owner changed, or added, is
                        # not a failure of the sound. It is rendered all the
                        # same, and held to what any rig must sound like.
                        try:
                            if args.candidates is not None:
                                fp, measured = calibrate(source, root, work, sushi_bin=sushi, retried=retried,
                                                         refuse=played)
                            else:
                                fp = measure(source, root, work, sushi_bin=sushi, retried=retried)
                            problems = played(fp)
                        except Unplayable as exc:
                            problems = exc.findings
                        if problems:
                            failed += 1
                            print(f"{label}: {len(problems)} problem(s), with no reference made from this "
                                  f"rig to compare against ({why})")
                            for problem in problems:
                                print(f"   {problem}")
                        else:
                            where = None
                            if args.candidates is not None:
                                where = args.candidates / ref_file.name
                                write_reference(where, fp, keep_wider(measured, tolerance),
                                                MadeFrom(target.name, current))
                                say_delay_margins(label, fp)
                            stale.append(f"{ref_name}: {why}"
                                         + (f"; candidate {where}" if where is not None else ""))
                            print(
                                f"{label}: STALE — {why}, so it was held only to being finite, "
                                f"two-sided and balanced, which it is. "
                                + (f"Candidate reference: {where}" if where is not None else
                                   f"Its reference: `sushi-rig nulltest {_as_typed(target, root)} "
                                   "--calibrate` on a machine that can render, or the container job's "
                                   "candidate"),
                                file=sys.stderr,
                            )
                        for note in retried[already:]:
                            print(f"   {note}")
                        continue

                    if reference is None:
                        # Not a failure of the sound: there is nothing to compare
                        # against yet. Saying "ok" here would be the loudest
                        # possible lie this command could tell. The path as the
                        # user must type it: the render runs from the rig root.
                        # --missing, so following the hint calibrates this one
                        # and leaves every channel that already has a reference
                        # alone: recalibrating the whole config would accept
                        # any accidental change in the others as the new sound.
                        print(
                            f"{label}: NO REFERENCE — run "
                            f"`sushi-rig nulltest {_as_typed(target, root)} --calibrate --missing`"
                            " on a machine that can render",
                            file=sys.stderr,
                        )
                        failed += 1
                        continue

                    fp = measure(source, root, work, sushi_bin=sushi, retried=retried)
                    differences = compare(reference, fp, tolerance) + played(fp)
                except (RenderError, ValueError, OSError, KeyError, TypeError,
                        AttributeError, yaml.YAMLError) as exc:
                    # Everything a hand-edited reference can do, caught per
                    # channel so one bad file does not abandon the rest.
                    cannot_check(label, exc)
                    for note in retried[already:]:
                        print(f"   {note}", file=sys.stderr)
                    continue

                if differences:
                    failed += 1
                    print(f"{label}: {len(differences)} difference(s)")
                    for difference in differences:
                        print(f"   {difference}")
                else:
                    print(f"{label}: sounds the same")
                for note in retried[already:]:
                    print(f"   {note}")

    if args.check_all:
        # A reference nothing produces any more — a config renamed, a track
        # renamed or removed — checks nothing and fails nothing, which reads as
        # coverage that is not there. Only with --all, the one time every
        # expected name is known; a config that could not be expanded keeps
        # its own references out of this. A committed rig's is a warning
        # (#144): renaming a rig or a channel is the owner's to do. A v1
        # config's, or a frozen rig's, fails.
        for folder, which in ((root / "config" / SOUND_DIR, "sound"), (root / FROZEN_SOUND, "frozen")):
            for ref in sorted(folder.glob("*.yaml")) if folder.is_dir() else []:
                if ref.stem in expected[which] or any(
                    _references_of(ref.stem, stem, kind != "v1") for stem, kind in unexpanded
                    if (kind == "frozen") == (which == "frozen")
                ):
                    continue
                # Counted as a check too, or the summary reads "3 of 1".
                checked += 1
                if which == "sound" and not ref.stem.endswith(V1_REFERENCE_SUFFIX):
                    orphans.append(str(ref.relative_to(root)))
                    print(
                        f"{ref.relative_to(root)}: ORPHANED REFERENCE (a warning) — no rig in "
                        f"config/rigs/ produces it any more, so it checks nothing: a rig or a "
                        f"channel renamed or removed. Delete it, or restore what it described",
                        file=sys.stderr,
                    )
                    continue
                print(
                    f"{ref.relative_to(root)}: ORPHANED REFERENCE — no config or channel "
                    f"produces it any more, so it checks nothing. Delete it, or "
                    f"restore what it described",
                    file=sys.stderr,
                )
                failed += 1

    if unrenderable:
        # Every run, not only the first: a gap in coverage that is reported once
        # and then goes quiet is a gap nobody remembers.
        print(
            f"\n{len(unrenderable)} channel(s) cannot be rendered offline, so their "
            f"sound is not protected: {', '.join(unrenderable)}",
            file=sys.stderr,
        )
    if retried:
        # Every run, like the unrenderable list: a retry that passes is still a
        # NaN the rig's plugin produced, and reading as a clean pass would hide
        # how often that happens.
        # One prefixed line per retry, here only: what the CI job counts.
        print(f"\n{len(retried)} render(s) {RETRIED}, each once:", file=sys.stderr)
        for note in retried:
            print(f"{RETRIED_PREFIX} {note}", file=sys.stderr)
    if stale:
        # The warnings, one prefixed line each, here only: what the CI job
        # turns into its annotation. Not failures, and never quiet.
        print(f"\n{len(stale)} committed rig channel(s) STALE, held to the generic checks alone:",
              file=sys.stderr)
        for line in stale:
            print(f"{STALE_PREFIX} {line}", file=sys.stderr)
    if orphans:
        print(f"\n{len(orphans)} orphaned reference(s) of committed rigs:", file=sys.stderr)
        for line in orphans:
            print(f"{ORPHAN_PREFIX} {line}", file=sys.stderr)
    if failed:
        print(f"\n{failed} of {checked} check(s) could not be confirmed", file=sys.stderr)
    return 1 if failed else 0


def _agent_tap() -> dict | None:
    """A running agent's report of the visualiser's tap
    (`agent.supervisor.visualiser_tap`), or None when no agent answers."""
    from .agent_client import Absent, Unusable, find_agent, probe

    try:
        health = probe(find_agent()[0])
    except (Absent, Unusable):
        return None
    tap = ((health.get("agent") or {}).get("supervisor") or {}).get("visualiser_tap")
    return tap if isinstance(tap, dict) else None


def _agent_crashes() -> list[dict] | None:
    """The rig's children a running agent saw exit (`agent.supervisor.crashed`),
    or None when no agent answers. Only the agent that started a rig can read
    its children's exits; doctor uses them to say that an absent node of the
    rig's is a crash rather than a rig that is down. Asked briefly, and never
    a reason for doctor to fail: without it, doctor reads the state file."""
    from .agent_client import Absent, Unusable, find_agent, probe

    try:
        health = probe(find_agent()[0])
    except (Absent, Unusable):
        return None
    supervisor = (health.get("agent") or {}).get("supervisor")
    crashed = supervisor.get("crashed") if isinstance(supervisor, dict) else None
    return crashed if isinstance(crashed, list) else None


def _doctor(args: argparse.Namespace) -> int:
    """`doctor`: everything about this machine that has to be true before you play."""
    from .doctor import faults, run, unchecked
    from .paths import checkout_root

    checks = run(checkout_root(), machine_file=args.machine, patchbay_file=args.patchbay,
                 crashed=_agent_crashes(), tap=_agent_tap())
    for check in checks:
        print(check)
    broken, skipped = faults(checks), unchecked(checks)
    if skipped:
        print(
            f"\n{len(skipped)} check(s) could not be run. That is not a pass — "
            "most of these need PipeWire and a running rig.",
            file=sys.stderr,
        )
    if broken:
        print(
            f"\n{len(broken)} of {len(checks)} check(s) failed. Each line above "
            "says what to do.",
            file=sys.stderr,
        )
        return 1
    print(f"\n{len(checks) - len(skipped)} check(s) passed")
    return 0


_DIRECT_HELP = (
    "act on the rig directly, without asking the agent even if one is running "
    "(#29); what the command did before there was an agent"
)


def main(argv: list[str] | None = None) -> int:
    """Run one command. A missing elkpy ends the process with its message, as it
    always has here; `live.py` raises it instead, so that the agent, which must
    outlive it, can report it and carry on.
    """
    from .live import ElkpyMissing

    try:
        return _main(argv)
    except ElkpyMissing as exc:
        sys.exit(str(exc))


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sushi-rig", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("emit", help="rig.yaml (+ state) -> Sushi config")
    p.add_argument("rig", type=Path)
    p.add_argument("--state", type=Path, help="captured state file to fold in")
    p.add_argument("-o", "--out", type=Path, default=Path("rig.json"))
    p.add_argument("--sushi", help=_SUSHI_HELP)
    p.add_argument(
        "--machine", type=Path,
        help="machine file whose controllers a rig's MIDI map names [default: machines/<this hostname>.yaml]",
    )
    p.add_argument(
        "--no-verify", dest="verify", action="store_false",
        help="skip the load and parameter-name checks run after writing",
    )

    p = sub.add_parser(
        "convert",
        help="write a v1 config as a v2 rig file of channels, checked to compile back "
        "to every plugin and setting it held",
    )
    p.add_argument("config", type=Path, help="config/<name>.json")
    p.add_argument("-o", "--out", type=Path,
                   help="where to write it [default: config/rigs/<name>.yaml]")
    p.add_argument(
        "--input", action="append", default=[], metavar="TRACK=ROLE:WIDTH",
        help="which input a track plays from, e.g. vocal=vocal-in:mono. A single-track "
        "config defaults to guitar-in:mono; any other must be told",
    )
    p.add_argument("--force", action="store_true", help="update a rig file already there to this config, keeping its comments")
    p.add_argument("--name", help="the rig's name [default: the config's _meta name, or its file name]")

    p = sub.add_parser("push", help="build the rig in a running Sushi")
    p.add_argument("rig", type=Path)
    p.add_argument("--address", default=DEFAULT_GRPC_ADDRESS)

    p = sub.add_parser("capture", help="read parameter state from a running Sushi")
    p.add_argument("--address", default=DEFAULT_GRPC_ADDRESS)
    p.add_argument("-o", "--out", type=Path, default=Path("state.json"))

    p = sub.add_parser(
        "probe",
        help="what an LV2 plugin declares about itself — parameters, ranges, "
        "and whether it can be driven from a config at all",
    )
    p.add_argument(
        "uri",
        nargs="?",
        help="plugin URI, or the path to a .nam amp model. Omit to list every "
        "plugin visible on LV2_PATH, which is how you find candidates in the "
        "first place",
    )
    p.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="full detail as JSON, including every parameter's range and flags",
    )

    p = sub.add_parser(
        "profile",
        help="component profiles: how one plugin is presented, checked against probe",
    )
    profile_sub = p.add_subparsers(dest="profile_command", required=True)
    p = profile_sub.add_parser(
        "validate",
        help="check a profile's facts against the plugin, and its judgements "
        "against those facts; exit 1 on any error",
    )
    p.add_argument("file", type=Path)
    p.add_argument(
        "--facts",
        type=Path,
        help="probe output saved with 'probe URI --json', to check against "
        "instead of probing — for machines without lilv, and for fixtures",
    )

    p = sub.add_parser(
        "save",
        help="capture the running Sushi into a rig file of channels, in place with its comments "
        "(the web UI's Save, without the agent's log)",
    )
    p.add_argument("name", help="the rig's name: its own to save in place, another for a new rig file beside it")
    p.add_argument("--rig", type=Path, required=True, help="the rig file it runs from, config/rigs/<name>.yaml")
    p.add_argument("--address", default=DEFAULT_GRPC_ADDRESS)

    p = sub.add_parser(
        "up",
        help="start the whole rig — patchbay, tuner, Sushi, the amp and the recorder "
        "— then return to the prompt; the web page (sushi-rig agent) is its control surface",
    )
    p.add_argument(
        "config_name",
        nargs="?",
        default=DEFAULT_CONFIG_NAME,
        help="names a rig of channels, config/rigs/<name>.yaml, compiled at start with a "
        f"patchbay generated from it and the machine file [default: {DEFAULT_CONFIG_NAME}]",
    )
    # Every start is a rig of channels since the v1 configs were retired
    # (#218): `--rig` is taken, so a command written before still runs, and
    # changes nothing.
    p.add_argument("--rig", action="store_true", help=argparse.SUPPRESS)
    # Every run is headless since the Open Stage Control panel was retired
    # (#139): the flag is taken, so a command written before still runs, and
    # changes nothing.
    p.add_argument("--headless", action="store_true", help=argparse.SUPPRESS)
    p.add_argument(
        "--no-tuner", dest="tuner", action="store_false", help="skip fmit"
    )
    p.add_argument(
        "--no-amp",
        dest="amp",
        action="store_false",
        help="skip the neural amp even if the config describes one",
    )
    p.add_argument(
        "--no-visualiser-tap", dest="visualiser_tap", action="store_false",
        help="never offer the rig's output (the master, after its limiter) to the web page's "
        "visualiser as the input 'Sushi rig output': the agent otherwise starts that tap, a "
        "pw-loopback in Sushi's cycle, while the visualiser listens. up itself never starts one. "
        "restart and a switch keep this choice",
    )
    p.add_argument(
        "--amp-model",
        metavar="NAME",
        help="use this model instead of the one in the config — a filename in "
        "amp/models/ or a path. For auditioning without editing the config; "
        "a save then records whichever was actually used",
    )
    p.add_argument(
        "--machine", type=Path,
        help="the machine file to compile and wire against [default: "
        "machines/<this hostname>.yaml]; with --direct only, since the agent takes no path from "
        "a request (its restart and switch carry the one the run started with)",
    )
    p.add_argument("--direct", action="store_true", help=_DIRECT_HELP)

    p = sub.add_parser("down", help="stop a running rig and verify it stopped")
    p.add_argument(
        "--force",
        action="store_true",
        help="skip straight to SIGKILL, giving Sushi no chance to release its "
        "JACK ports cleanly — for a rig that is already wedged",
    )
    p.add_argument("--direct", action="store_true", help=_DIRECT_HELP)

    p = sub.add_parser(
        "verify",
        help="check a config loads, names things that exist, and actually "
        "applies what it says",
    )
    p.add_argument("config", type=Path, nargs="?", help="config to check")
    p.add_argument(
        "--all", action="store_true", dest="check_all",
        help="every config in config/, skipping retired ones",
    )
    p.add_argument(
        "--quick", action="store_true",
        help="skip the check that starts Sushi — loads and names only",
    )
    p.add_argument("--sushi", help=_SUSHI_HELP)

    p = sub.add_parser(
        "nulltest",
        help="render a config offline and check its sound against an approved "
        "reference",
    )
    p.add_argument("config", type=Path, nargs="?", help="config to check")
    p.add_argument(
        "--all", action="store_true", dest="check_all",
        help="every rig in config/rigs/, every frozen rig in tool/tests/fixtures/rigs/, and any "
        "v1 config left in config/ (none since #218)",
    )
    p.add_argument(
        "--calibrate", action="store_true",
        help="render twice, measure how far two identical runs differ, and write "
        "that as the reference's tolerance. Needed once per config, and again "
        "whenever the config changes on purpose",
    )
    p.add_argument(
        "--missing", action="store_true",
        help="with --calibrate, only what has no reference yet — so adding a "
        "channel does not re-baseline the ones already checked",
    )
    p.add_argument(
        "--candidates", type=Path, metavar="DIR",
        help="for each committed rig's channel that is STALE (no reference made from "
        "the rig as it is), calibrate it and write the candidate reference to DIR. "
        "What the container job attaches to its warning",
    )
    p.add_argument("--sushi", help=_SUSHI_HELP)

    p = sub.add_parser(
        "plugins",
        help="build the rig's plugins/ folder from installed packages, and "
        "check it is self-contained",
    )
    plugins_sub = p.add_subparsers(dest="plugins_command", required=True)

    def add_requirement_args(q):
        q.add_argument(
            "--manifest", type=Path,
            help="LV2 URIs the rig can load [default: plugin-manifest.txt at the repo root]",
        )
        q.add_argument(
            "--config-dir", type=Path,
            help="also require every plugin these configs use, because the manifest "
            "does not list them all [default: config/ at the repo root]",
        )
        q.add_argument(
            "--waive", action="append", default=[], metavar="URI",
            help="accept that this required URI is missing, by name. Repeatable. "
            "An error if the rig does not require it, so stale waivers cannot linger",
        )

    q = plugins_sub.add_parser(
        "assemble",
        help="copy exactly the bundles the rig needs from a system LV2 tree into plugins/",
    )
    q.add_argument(
        "--from", dest="from_dirs", type=Path, action="append", metavar="DIR",
        help=_FROM_HELP,
    )
    q.add_argument(
        "--to", type=Path,
        help="folder to build [default: plugins/ at the repo root, where LV2_PATH points]",
    )
    q.add_argument(
        "--replace", action="store_true",
        help="delete --to first if it is not empty",
    )
    add_requirement_args(q)

    q = plugins_sub.add_parser(
        "add",
        help="copy into plugins/, as it is, only the bundles the rig needs and it lacks "
        "(the amp and built bundles there are left alone); all or nothing",
    )
    q.add_argument(
        "--from", dest="from_dirs", type=Path, action="append", metavar="DIR",
        help=_FROM_HELP,
    )
    q.add_argument(
        "plugins_dir", type=Path, nargs="?",
        help="folder to add to [default: plugins/ at the repo root]",
    )
    add_requirement_args(q)

    q = plugins_sub.add_parser(
        "check",
        help="every required plugin URI resolves with LV2_PATH set to plugins/ alone, "
        "every link in it leads to a file, and every required plugin's binary is there",
    )
    q.add_argument(
        "plugins_dir", type=Path, nargs="?",
        help="folder to check [default: plugins/ at the repo root]",
    )
    add_requirement_args(q)

    p = sub.add_parser(
        "patchbay",
        help="check the saved patchbay against the live graph, or generate one "
        "from the machine file's roles",
    )
    patchbay_sub = p.add_subparsers(dest="patchbay_command", required=True)
    q = patchbay_sub.add_parser(
        "check",
        help="saved links whose node is not in the live PipeWire graph. Exit 1 "
        "means a node is definitely gone; nodes the rig itself starts are "
        "reported as unchecked when it is down, and do not fail",
    )
    q.add_argument(
        "file", type=Path, nargs="?",
        help="patchbay to check [default: Patchbay/rig.qpwgraph at the repo root]",
    )
    q = patchbay_sub.add_parser(
        "generate",
        help="write a patchbay from machines/<hostname>.yaml and a rig.yaml; "
        "prints it unless -o is given; -o Patchbay/rig.qpwgraph replaces the "
        "committed one, which is how it is maintained",
    )
    q.add_argument("rig", type=Path, help="a rig file of channels (config/rigs/), or a rig.yaml whose tracks name source/sink roles")
    q.add_argument(
        "--machine", type=Path,
        help="machine file [default: machines/<this hostname>.yaml at the repo root]",
    )
    q.add_argument("-o", "--out", type=Path, help="write here instead of stdout")
    q.add_argument(
        "--offline", action="store_true",
        help="do not read the live graph: name each device node after its port's "
        "client instead. Approximate for an ALSA device, whose node is its "
        "profile, so qpwgraph will not match it — for diffing, not for use",
    )
    q.add_argument(
        "--no-tuner", action="store_true",
        help="leave out the tuner tap, which otherwise follows the guitar role",
    )

    p = sub.add_parser("machine", help="the machine file: which physical ports play which role")
    machine_sub = p.add_subparsers(dest="machine_command", required=True)
    q = machine_sub.add_parser(
        "check",
        help="validate a machine file on its own, reporting every problem; exit 1 if any",
    )
    q.add_argument(
        "file", type=Path, nargs="?",
        help="machine file [default: machines/<this hostname>.yaml at the repo root]",
    )

    p = sub.add_parser(
        "take", help="takes: folders holding stems and the config that made them"
    )
    take_sub = p.add_subparsers(dest="take_command", required=True)
    q = take_sub.add_parser(
        "check",
        help="check a take folder is whole and self-describing, and say how its "
        "stems line up; exit 1 on any problem",
    )
    q.add_argument("folder", type=Path, help="the take's folder")
    q = take_sub.add_parser(
        "repair",
        help="make the stems of a take cut off mid-write readable again, by setting "
        "each header from its file's own length",
    )
    q.add_argument("folder", type=Path, help="the take's folder")

    p = sub.add_parser(
        "probes",
        help="where selftest will inject and listen for a rig: the ports each "
        "probe links, on a probe track of its own beside the instrument's. Reads "
        "nothing live; exit 1 if the rig cannot be probed",
    )
    p.add_argument("rig", type=Path, help="a rig file (config/rigs/*.yaml) or a rig.yaml (config/src/*.yaml)")
    p.add_argument(
        "--amp", action="store_true",
        help="plan for the rig with the amp in it, as `up` runs it for a config "
        "with an _amp model or with --amp-model",
    )

    p = sub.add_parser(
        "doctor",
        help="whether this machine will run the rig properly, and what to do "
        "about it if not; exit 1 on any failure",
    )
    p.add_argument(
        "--machine", type=Path,
        help="machine file [default: machines/<this hostname>.yaml at the repo root]",
    )
    p.add_argument(
        "--patchbay", type=Path,
        help="patchbay to check [default: Patchbay/rig.qpwgraph at the repo root]",
    )

    p = sub.add_parser("status", help="what is running, if anything")
    p.add_argument("--direct", action="store_true", help=_DIRECT_HELP)
    p = sub.add_parser(
        "agent",
        help="serve the rig over HTTP on localhost: health, status, doctor, up/down/restart (#29)",
    )
    # Localhost first (#29): opening it to the LAN is the operator's call.
    # 8029 is the issue's number, and clear of 8080, which Open Stage Control
    # served the retired panel on. The defaults live in agent_client.py rather
    # than agent.py so that `sushi-rig status`, which looks for the agent
    # there, does not import asyncio for two numbers.
    from .agent_client import DEFAULT_HOST, DEFAULT_PORT

    # #181: what `--host 0.0.0.0` is for, said as what it is for, and the
    # URLs to open printed once it listens. Opt-in: the default stays local.
    p.add_argument(
        "--phone", action="store_true",
        help="listen on the network (--host 0.0.0.0) so a phone can open the page, and print "
        "the address to open on it for each Wi-Fi or ethernet interface. No password: use it "
        "on a network you trust",
    )
    p.add_argument("--host", default=DEFAULT_HOST,
                   help=f"bind address [default: {DEFAULT_HOST}; 0.0.0.0 opens it to the LAN]")
    p.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"[default: {DEFAULT_PORT}]")
    def allow_host(value: str) -> str:
        # Refused here, not reduced to its name (review of PR #174): a port
        # given would read as if only that port were allowed, and none is
        # checked. agent.py's own check, imported only when the flag is given.
        from .agent import allowed_host_name

        try:
            return allowed_host_name(value)
        except ValueError as exc:
            raise argparse.ArgumentTypeError(str(exc)) from exc

    p.add_argument(
        "--allow-host", action="append", default=[], metavar="NAME", dest="allow_hosts",
        type=allow_host,
        help="a host name a browser may reach the agent by, beside any IP, localhost, this "
        "machine's hostname and its .local name: a tunnel's, a VPN's or a reverse proxy's name "
        "for it, with no scheme or port (it is allowed on every one); repeatable. A request by "
        "any other name is refused, a read too, which is what stops DNS rebinding (#161)",
    )
    agent_sub = p.add_subparsers(dest="agent_command")
    service_p = agent_sub.add_parser(
        "install-service",
        help="write the agent's systemd user unit to ~/.config/systemd/user/ and enable it; "
        "prints the systemctl commands to start it rather than running them",
    )
    # Their own dests: a subcommand's defaults would otherwise overwrite what
    # `sushi-rig agent --phone install-service` gave the agent's own.
    service_p.add_argument(
        "--phone", action="store_true", dest="service_phone",
        help="the service listens on the network, as `sushi-rig agent --phone` (#181)",
    )
    service_p.add_argument("--host", dest="service_host", metavar="HOST",
                           help=f"the address the service listens on [default: {DEFAULT_HOST}]")
    service_p.add_argument("--port", type=int, dest="service_port", metavar="PORT",
                           help=f"the port the service listens on [default: {DEFAULT_PORT}]")
    service_p.add_argument(
        "--allow-host", action="append", default=[], metavar="NAME", dest="service_allow_hosts",
        type=allow_host, help="a host name the service's agent answers a browser to, as "
        "`sushi-rig agent --allow-host`; repeatable",
    )
    p = sub.add_parser(
        "restart",
        help="stop the running rig and bring the same one back — same config, same mode, same amp, tuner and visualiser tap choices",
    )
    p.add_argument(
        "--discard", action="store_true",
        help="restart even though the agent saw changes since the last save, dropping them "
        "(without it, the agent refuses a restart that would lose them)",
    )
    p.add_argument(
        "--amp", action="store_true", dest="amp_on",
        help="bring the amp back on a rig started with --no-amp, which a restart otherwise keeps off",
    )
    p.add_argument("--direct", action="store_true", help=_DIRECT_HELP)

    p = sub.add_parser(
        "top",
        help="live CPU load per rig component, and whether xruns are climbing",
    )
    p.add_argument(
        "--interval", type=float, default=1.0, help="seconds between samples"
    )
    p.add_argument(
        "--once", action="store_true", help="print one reading and exit"
    )

    p = sub.add_parser(
        "recorder",
        help="record takes for the agent on request: what `up --rig` starts, under "
        "pw-jack, so the web UI can record (recorder_process.py)",
    )
    p.add_argument(
        "--socket", type=Path,
        help="the Unix socket to answer on [default: recorder.sock beside the state file]",
    )

    args = parser.parse_args(argv)

    if args.command == "emit":
        state = json.loads(args.state.read_text()) if args.state else None
        write_json(args.out, _emit_any(args.rig, state, args.machine))
        if args.verify:
            # The cheap checks only: they cost milliseconds and catch a bad
            # config at the moment it is created. The check that starts Sushi
            # stays an explicit `sushi-rig verify`.
            from .verify import VerifyError, verify

            try:
                problems = verify(args.out, _sushi_bin(args), quick=True)
            except (VerifyError, SystemExit) as exc:
                sys.exit(f"{args.out} was written, but does not load: {exc}")
            if problems:
                for problem in problems:
                    print(f"  {problem}", file=sys.stderr)
                sys.exit(f"{args.out} was written, but {len(problems)} problem(s) above")
    elif args.command == "push":
        from .live import push

        push(RigSpec.load(args.rig), args.address)
    elif args.command == "capture":
        from .live import capture

        write_json(args.out, capture(args.address))
    elif args.command == "probe":
        from .probe import probe, summarise

        # A .nam amp model is probed too. It answers the same question a plugin
        # URI does — what is this, and can the rig actually use it — and the
        # answer is equally undiscoverable without opening the file.
        if args.uri and (args.uri.endswith(".nam") or Path(args.uri).is_file()):
            from .amp import AmpError, describe_model, summarise_model

            try:
                described = describe_model(args.uri)
            except AmpError as exc:
                sys.exit(str(exc))
            print(json.dumps(described, indent=2) if args.as_json
                  else summarise_model(described))
            return 0

        described = probe(args.uri)
        if args.as_json:
            print(json.dumps(described if args.uri is None else described[0], indent=2))
        elif args.uri is None:
            print(summarise(described))
            drivable = sum(1 for d in described if d["config_drivable"])
            print(
                f"\n{len(described)} plugins, {drivable} config-drivable. "
                "STATE marks the rest: they keep state outside their control "
                "ports (a file, a model, a sample), which Sushi cannot set "
                "from a config.",
                file=sys.stderr,
            )
        else:
            d = described[0]
            print(f"{d['name']}  ({d['class'] or 'no class declared'})")
            print(f"  uri     {d['uri']}")
            print(f"  bundle  {d['bundle']}")
            print(f"  audio   {d['audio_in']} in / {d['audio_out']} out")
            if d["config_drivable"]:
                print("  config  drivable — all state lives in control ports")
            else:
                reasons = []
                required = d["atom_ports"] - d.get("optional_atom_ports", 0)
                if required:
                    reasons.append(f"{required} atom port(s) it needs connected")
                if d["patch_writables"]:
                    reasons.append(f"{len(d['patch_writables'])} patch:writable")
                if d["state_interface"]:
                    reasons.append("state:interface")
                print(
                    "  config  NOT fully drivable — " + ", ".join(reasons) + "."
                    " Sushi cannot set state held outside control ports."
                )
            print(f"  {len(d['parameters'])} parameter(s):")
            for prm in d["parameters"]:
                flags = " ".join(
                    f for f in ("toggled", "integer", "enumeration", "logarithmic")
                    if prm[f]
                )
                rng = f"[{prm['min']}, {prm['max']}]"
                print(
                    f"    {prm['name'][:28]:30s} {rng:24s} "
                    f"default={prm['default']}{'  ' + flags if flags else ''}"
                )

    elif args.command == "profile":
        from .profile import ProfileError, errors, load_profile, validate

        try:
            profile = load_profile(args.file)
            if args.facts:
                facts = json.loads(args.facts.read_text())
            else:
                from .probe import probe

                uri = (profile.get("plugin") or {}).get("uri")
                if not isinstance(uri, str):
                    sys.exit(f"{args.file}: plugin.uri is missing, so there is nothing to probe")
                facts = probe(uri)[0]
        except (ProfileError, OSError, ValueError) as exc:
            sys.exit(str(exc))

        findings = validate(profile, facts)
        for finding in findings:
            print(f"  {finding}", file=sys.stderr if finding.severity == "warning" else sys.stdout)
        failed = errors(findings)
        print(
            f"{args.file.name}: {len(failed)} error(s), "
            f"{len(findings) - len(failed)} warning(s)"
        )
        return 1 if failed else 0
    elif args.command == "save":
        from .save import SaveError, save_any

        try:
            out_path = save_any(args.name, args.rig, args.address)
        except SaveError as exc:
            sys.exit(str(exc))
        print(f"saved {out_path}")
    elif args.command == "verify":
        import tempfile

        from .paths import checkout_root
        from .rigfile import RigFileError
        from .verify import VerifyError, config_to_check, configs_to_check, verify

        if args.check_all:
            targets = configs_to_check(checkout_root())
        elif args.config:
            targets = [args.config]
        else:
            sys.exit("give a config to verify, or --all")

        # See _sushi_bin for why this is the rig's ./sushi and not PATH's.
        sushi = _sushi_bin(args)

        failed = 0
        scratch = tempfile.TemporaryDirectory(prefix="sushi-rig-verify-")
        for target in targets:
            try:
                if args.check_all:
                    # Each rig of channels compiled as nulltest renders it
                    # (`config_to_check`): with no machine file to hand, as
                    # in the CI container, a rig's MIDI map cannot be.
                    try:
                        compiled = config_to_check(target)
                    except RigFileError as exc:
                        raise VerifyError("it does not compile: " + "; ".join(exc.problems)) from exc
                    checked = Path(scratch.name) / f"{target.stem}.json"
                    checked.write_text(json.dumps(compiled, indent=2) + "\n")
                    problems = verify(checked, sushi, args.quick)
                else:
                    problems = verify(target, sushi, args.quick)
            except VerifyError as exc:
                print(f"{target.name}: CANNOT CHECK — {exc}", file=sys.stderr)
                failed += 1
                continue
            if problems:
                failed += 1
                print(f"{target.name}: {len(problems)} problem(s)")
                for problem in problems:
                    print(f"   {problem}")
            else:
                print(f"{target.name}: ok")
        scratch.cleanup()
        if failed:
            print(f"\n{failed} of {len(targets)} config(s) have problems", file=sys.stderr)
        return 1 if failed else 0
    elif args.command == "nulltest":
        return _nulltest(args)
    elif args.command == "plugins":
        return _plugins(args)
    elif args.command == "patchbay":
        return _patchbay(args)
    elif args.command == "machine":
        return _machine(args)
    elif args.command == "doctor":
        return _doctor(args)
    elif args.command == "take":
        return _take(args)
    elif args.command == "probes":
        return _probes(args)
    elif args.command == "convert":
        return _convert(args)
    elif args.command == "top":
        from .top import top

        return top(args.interval, args.once)
    elif args.command == "agent":
        args.host = _agent_host(args.phone, args.host)
        if args.agent_command == "install-service":
            from .paths import checkout_root
            from .service import ServiceError, install, listen_args

            try:
                lines = install(checkout_root(), listen=listen_args(
                    phone=args.service_phone or args.phone,
                    host=args.service_host or args.host, port=args.service_port or args.port,
                    allow_hosts=[*args.allow_hosts, *args.service_allow_hosts],
                ))
            except ServiceError as exc:
                sys.exit(f"the service was not installed: {exc}")
            print("\n".join(lines))
            return 0
        from .agent import AgentError, serve

        try:
            return serve(args.host, args.port, allow_hosts=args.allow_hosts)
        except AgentError as exc:
            sys.exit(str(exc))
    elif args.command in ("up", "down", "status", "restart"):
        from .amp import AmpError
        from .rig import RigError, down, restart, status, up

        # The agent holds the rig when it is running (#29), so ask it first;
        # with nothing listening, act as the CLI always has. Either way one
        # line on stderr says which.
        if args.command == "up" and args.machine is not None and not args.direct:
            sys.exit("sushi-rig: --machine is taken with --direct only: the agent takes no path from a "
                     "request. Nothing was done.")
        if args.direct:
            print("sushi-rig: --direct, so acting without asking the agent", file=sys.stderr)
        else:
            from .agent_client import Unusable, route

            try:
                routed = route(args.command, args)
            except Unusable as exc:
                sys.exit(
                    f"sushi-rig: {exc}. Nothing was done. If the agent is stuck, stop it "
                    f"(systemctl --user stop sushi-rig-agent.service, or Ctrl+C where it runs); "
                    f"'sushi-rig {args.command} --direct' acts without it."
                )
            if routed is not None:
                return routed

        # AmpError is caught alongside RigError rather than subclassing it:
        # amp.py is imported *by* rig.py, so inheriting the other way round
        # would be circular. Both mean "something the user needs to fix", and
        # both should read as a message rather than a traceback.
        try:
            if args.command == "up":
                return up(
                    args.config_name,
                    tuner=args.tuner, amp=args.amp,
                    amp_model=args.amp_model, visualiser_tap=args.visualiser_tap,
                    machine=str(args.machine.resolve()) if args.machine is not None else None,
                )
            if args.command == "down":
                return down(args.force)
            if args.command == "restart":
                return restart(amp=True) if args.amp_on else restart()
            return status()
        except (RigError, AmpError) as exc:
            sys.exit(str(exc))
    elif args.command == "recorder":
        from .recorder_process import main as recorder_main

        return recorder_main(args.socket)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
