"""sushi-rig — author an LV2 rig live in Sushi, then capture it back as a config file.

The workflow this supports is in README.md. IMPLEMENTATION_BRIEF.md is the
original brief, kept as history; where it and the code disagree, the code is right.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .dump import dump_plugins
from .emit import emit
from .live import DEFAULT_GRPC_ADDRESS
from .listen import DEFAULT_LISTEN_PORT, DEFAULT_STATUS_HOST, DEFAULT_STATUS_PORT
from .panel import build_osc_panel
from .rig import DEFAULT_CONFIG_NAME
from .spec import RigSpec


def write_json(path: Path, payload) -> None:
    path.write_text(json.dumps(payload, indent=2) + "\n")
    print(f"wrote {path}")


def _amp_parameters(pairs: list[str]) -> dict[str, float]:
    """Parse repeated `--amp-parameter NAME=VALUE` into a dict.

    Names carry spaces ("Input Lvl"), so splitting on the first `=` only.
    """
    values: dict[str, float] = {}
    for pair in pairs:
        name, _, raw = pair.partition("=")
        try:
            values[name] = float(raw)
        except ValueError:
            sys.exit(f"bad --amp-parameter {pair!r}: expected NAME=VALUE")
    return values


def _plugins(args: argparse.Namespace) -> int:
    """`plugins assemble` and `plugins check`, which share how they find what the
    rig requires and differ only in what they do with it."""
    from .paths import checkout_root
    from .plugins import (
        SYSTEM_LV2_DIR, PluginsError, assemble, check, config_uris, read_manifest,
    )
    from .verify import configs_to_check

    root = checkout_root()
    manifest_path = args.manifest or root / "plugin-manifest.txt"
    try:
        manifest = read_manifest(manifest_path.read_text())
    except OSError as exc:
        sys.exit(f"cannot read the plugin manifest: {exc}")

    used: set[str] = set()
    configs = (
        sorted(args.config_dir.glob("*.json")) if args.config_dir
        else configs_to_check(root)
    )
    for config in configs:
        used |= config_uris(json.loads(config.read_text()), str(config))

    waived = set(args.waive)
    try:
        if args.plugins_command == "assemble":
            to = args.to or root / "plugins"
            plan = assemble(
                manifest, used, args.from_dirs or [SYSTEM_LV2_DIR], to, waived,
                args.replace,
            )
            print(f"copied {len(plan.bundles)} bundle(s) into {to}")
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
        GUITAR_ROLE, PatchbayError, check_nodes, generate_patchbay,
        live_node_names, live_port_nodes, parse_patchbay, read_pw_dump,
        resolve_nodes, routes_from_rig, tuner_problem,
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
        rig = spec_of(args.rig)
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
        feeds, sinks = routes_from_rig(rig)
        if not feeds and not sinks:
            # An empty patchbay written without comment is the silent failure
            # this exists to end: nothing wired, and nothing says why.
            sys.exit(
                f"{args.rig.name}: no track names a source or sink role, so there "
                "is nothing to generate"
            )
        roles = machine.role_ports()
        # The tuner follows the guitar role rather than a port, so it moves with
        # the channel. Decided before the live graph is read, because the tuner's
        # port has to be resolved alongside the tracks' -- left out of
        # `resolve_nodes`, the tap was written with the port's client name
        # ("Audient iD14") instead of the live ALSA profile, which qpwgraph never
        # matches: a deaf tuner in a file that generated cleanly.
        tuner_role = None if args.no_tuner else GUITAR_ROLE
        refusal = tuner_problem(roles, tuner_role) if tuner_role else None
        if refusal:
            # A warning rather than a failure: a guitar role the tuner cannot tap
            # is a reason to have no tuner tap, not a reason to have no patchbay.
            # Silence is the one thing it must not be -- nothing else in either
            # file mentions fmit, so a missing tap looks like a broken tuner.
            print(
                f"warning: {machine_file.name}: {refusal}. The generated patchbay "
                "has no tuner tap, so fmit will hear nothing. Pass --no-tuner to "
                "stop saying so",
                file=sys.stderr,
            )
            tuner_role = None
        nodes = None
        if not args.offline:
            sources = set(feeds) | ({tuner_role} if tuner_role else set())
            nodes, problems = resolve_nodes(
                roles, sources, set(sinks), live_port_nodes(read_pw_dump())
            )
            if problems:
                raise PatchbayError(problems)
        xml = generate_patchbay(
            roles, feeds, sinks, mono_to_stereo=machine.mono_roles(), nodes=nodes,
            tuner_role=tuner_role,
        )
    except PatchbayError as exc:
        sys.exit("\n".join(exc.problems))

    if args.out:
        args.out.write_text(xml)
        print(f"wrote {args.out}", file=sys.stderr)
    else:
        sys.stdout.write(xml)
    return 0


def _emit_any(path: Path, state: dict | None) -> dict:
    """A config from either kind of rig file: channels and a master (#30), or
    tracks spelt out by hand. The file says which, by its `kind`."""
    from .rigfile import Rig, RigFileError, emit_rig, load_any

    try:
        rig = load_any(path)
        if not isinstance(rig, Rig):
            return emit(rig, state)
        return emit_rig(rig, state)
    except RigFileError as exc:
        sys.exit(f"{path}:\n  " + "\n  ".join(exc.problems))


def _convert(args: argparse.Namespace) -> int:
    from .convert import ConvertError, convert, parse_inputs, render_rig_file
    from .paths import checkout_root

    try:
        config = json.loads(args.config.read_text())
        meta = config.get("_meta") if isinstance(config, dict) else None
        name = args.name or (meta or {}).get("name") or args.config.stem
        out = args.out or checkout_root() / "config" / "rigs" / f"{name}.yaml"
        if out.exists() and not args.force:
            # A rig file may carry comments and edits its config never had.
            print(f"FAIL {out} exists; --force replaces it")
            return 1
        rig = convert(config, name, parse_inputs(args.input))
    except ConvertError as exc:
        for problem in exc.problems:
            print(f"FAIL {args.config}: {problem}")
        return 1
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        print(f"FAIL {args.config}: {type(exc).__name__}: {exc}")
        return 1
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        source = args.config.resolve().relative_to(checkout_root())
    except ValueError:
        source = args.config
    out.write_text(render_rig_file(rig, str(source)))
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
        print(f"{point.name}\n  inject  {', '.join(point.inject)}\n  listen  {point.capture}{expect}")
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


def _nulltest(args: argparse.Namespace) -> int:
    """`nulltest`: has any config, or any channel of one, changed its sound.

    A config is rendered whole when Sushi's offline frontend can host it, and
    one channel at a time when it cannot (`render.channels_of`).
    """
    import tempfile

    import yaml

    from .nulltest import compare, from_dict, keep_wider, to_dict
    from .paths import checkout_root
    from .render import (
        SOUND_DIR, RenderError, calibrate, channels_of, measure, offline_problem,
        reference_path,
    )
    from .verify import configs_to_check

    root = checkout_root()
    if args.check_all:
        targets = configs_to_check(root)
    elif args.config:
        targets = [args.config]
    else:
        sys.exit("give a config to null-test, or --all")

    sushi = args.sushi or str(root / "sushi")
    failed = 0
    checked = 0
    unrenderable: list[str] = []
    expected: set[str] = set()
    unexpanded: list[str] = []

    def cannot_check(label: str, exc: Exception) -> None:
        nonlocal failed
        # The type as well as the message: a KeyError's message is the bare key,
        # which on its own reads like nothing at all.
        print(f"{label}: CANNOT CHECK — {type(exc).__name__}: {exc}", file=sys.stderr)
        failed += 1

    with tempfile.TemporaryDirectory(prefix="sushi-rig-nulltest-") as tmp:
        for target in targets:
            try:
                channels = channels_of(json.loads(target.read_text()), target)
            except (ValueError, OSError, TypeError, AttributeError) as exc:
                # TypeError and AttributeError too: a config that is a list, or
                # whose tracks are null, must cost one line, not the whole run
                # and the orphan check after it.
                unexpanded.append(target.stem)
                checked += 1
                cannot_check(target.name, exc)
                continue

            for channel in channels:
                checked += 1
                expected.add(channel.name)
                work = Path(tmp) / channel.name
                ref_file = reference_path(root, Path(f"{channel.name}.json"))
                if args.calibrate and args.missing and ref_file.exists():
                    # --missing calibrates only what has no reference yet, so
                    # adding a channel does not quietly re-baseline the others,
                    # whose sound may have changed by accident.
                    print(f"{channel.label}: has a reference, left as it is")
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
                                f"{channel.label}: NO LONGER RENDERABLE — it has a "
                                f"reference, so it used to be checked, but {problem}. "
                                f"Fix the routing, or if the move was meant, delete "
                                f"{ref_file.relative_to(root)}: its sound can no "
                                f"longer be checked here",
                                file=sys.stderr,
                            )
                            failed += 1
                        else:
                            print(
                                f"{channel.label}: NOT RENDERABLE OFFLINE — {problem}. "
                                "A change in its sound would not fail this check",
                                file=sys.stderr,
                            )
                            unrenderable.append(channel.label)
                        continue

                    if channel.whole:
                        source = target
                    else:
                        # A copy cut down to this channel, named after it so
                        # Sushi's own messages and the fingerprint say which.
                        work.mkdir(parents=True, exist_ok=True)
                        source = work / f"{channel.name}.json"
                        source.write_text(json.dumps(channel.config, indent=2) + "\n")

                    if args.calibrate:
                        fp, measured = calibrate(source, root, work, sushi_bin=sushi)
                        committed = None
                        if ref_file.exists():
                            try:
                                committed = from_dict(yaml.safe_load(ref_file.read_text()))[1]
                            except (ValueError, KeyError, TypeError, AttributeError,
                                    yaml.YAMLError):
                                # An unreadable reference has no tolerance worth
                                # keeping; this calibration replaces it whole.
                                committed = None
                        tolerance = keep_wider(measured, committed)
                        if tolerance != measured:
                            print(
                                f"{channel.label}: kept the committed tolerance, wider "
                                f"than this calibration's floor — delete the reference "
                                f"first to tighten it",
                                file=sys.stderr,
                            )
                        ref_file.parent.mkdir(parents=True, exist_ok=True)
                        ref_file.write_text(yaml.dump(
                            to_dict(fp, tolerance), default_flow_style=False, sort_keys=False,
                        ))
                        print(
                            f"{channel.label}: calibrated — band {tolerance.band_db:.4f} dB, "
                            f"level {tolerance.level_db:.4f} dB, "
                            f"delay {tolerance.delay_samples} samples "
                            f"-> {ref_file.relative_to(root)}"
                        )
                        continue

                    if not ref_file.exists():
                        # Not a failure of the sound: there is nothing to compare
                        # against yet. Saying "ok" here would be the loudest
                        # possible lie this command could tell. The path as the
                        # user must type it: the render runs from the rig root.
                        # --missing, so following the hint calibrates this one
                        # and leaves every channel that already has a reference
                        # alone: recalibrating the whole config would accept
                        # any accidental change in the others as the new sound.
                        print(
                            f"{channel.label}: NO REFERENCE — run "
                            f"`sushi-rig nulltest {_as_typed(target, root)} --calibrate --missing`"
                            " on a machine that can render",
                            file=sys.stderr,
                        )
                        failed += 1
                        continue

                    reference, tolerance = from_dict(yaml.safe_load(ref_file.read_text()))
                    differences = compare(
                        reference, measure(source, root, work, sushi_bin=sushi), tolerance
                    )
                except (RenderError, ValueError, OSError, KeyError, TypeError,
                        AttributeError, yaml.YAMLError) as exc:
                    # Everything a hand-edited reference can do, caught per
                    # channel so one bad file does not abandon the rest.
                    cannot_check(channel.label, exc)
                    continue

                if differences:
                    failed += 1
                    print(f"{channel.label}: {len(differences)} difference(s)")
                    for difference in differences:
                        print(f"   {difference}")
                else:
                    print(f"{channel.label}: sounds the same")

    if args.check_all:
        # A reference nothing produces any more — a config renamed, a track
        # renamed or removed — checks nothing and fails nothing, which reads as
        # coverage that is not there. Only with --all, the one time every
        # expected name is known; a config that could not be expanded keeps
        # its own references out of this.
        sound = root / "config" / SOUND_DIR
        for ref in sorted(sound.glob("*.yaml")) if sound.is_dir() else []:
            if ref.stem in expected or any(
                ref.stem == stem or ref.stem.startswith(f"{stem}.") for stem in unexpanded
            ):
                continue
            print(
                f"{ref.relative_to(root)}: ORPHANED REFERENCE — no config or channel in "
                f"config/ produces it any more, so it checks nothing. Delete it, or "
                f"restore what it described",
                file=sys.stderr,
            )
            # Counted as a check too, or the summary reads "3 of 1".
            checked += 1
            failed += 1

    if unrenderable:
        # Every run, not only the first: a gap in coverage that is reported once
        # and then goes quiet is a gap nobody remembers.
        print(
            f"\n{len(unrenderable)} channel(s) cannot be rendered offline, so their "
            f"sound is not protected: {', '.join(unrenderable)}",
            file=sys.stderr,
        )
    if failed:
        print(f"\n{failed} of {checked} check(s) could not be confirmed", file=sys.stderr)
    return 1 if failed else 0


def _doctor(args: argparse.Namespace) -> int:
    """`doctor`: everything about this machine that has to be true before you play.

    One pw-dump, read once and used by both the graph checks and the patchbay
    check, so the two cannot disagree about a graph that changed between them.
    """
    from .doctor import (
        EXPECTED_RATE, UNCHECKED, Check, environment_checks, faults,
        gather_environment, graph_checks, interface_clients, machine_check,
        patchbay_checks, unchecked,
    )
    from .machine import MachineError, MachineSpec, machine_path
    from .patchbay import (
        PatchbayError, check_nodes, live_node_names, nodes_by_id, parse_patchbay,
        read_pw_dump,
    )
    from .paths import checkout_root
    from .rig import QUANTUM

    root = checkout_root()
    checks = []

    # The machine file first: every device name the checks below compare
    # against comes from it, so an unreadable one is reported once here rather
    # than as a fault in each of them.
    machine_file = args.machine or machine_path(root)
    clients: set[str] = set()
    try:
        spec = MachineSpec.load(machine_file)
    except MachineError as exc:
        checks.append(machine_check([], file_name=machine_file.name, readable=False,
                                    why=str(exc)))
    else:
        problems = spec.validate()
        checks.append(machine_check(problems, file_name=machine_file.name))
        if not problems:
            clients = interface_clients(spec.role_ports())

    # Read and parsed inside one try. `pw-dump` exiting 0 with output that is
    # not JSON raises from the parse, not the read, and that used to escape as a
    # traceback — while the identical exception from the same helper was caught
    # for the patchbay check a few lines below. One of two readers of the same
    # bytes being guarded is the kind of asymmetry that only shows on the day it
    # happens.
    dump, nodes, why = None, None, ""
    try:
        dump = read_pw_dump()
        nodes = nodes_by_id(dump)
    except PatchbayError as exc:
        dump, why = None, "; ".join(exc.problems)

    checks += graph_checks(
        nodes,
        clients,
        expected_quantum=QUANTUM,
        expected_rate=EXPECTED_RATE,
        reason=why,
    )

    patchbay_file = args.patchbay or root / "Patchbay" / "rig.qpwgraph"

    def not_compared(reason: str) -> Check:
        """One `unchecked` for a patchbay that could not be compared at all."""
        return Check(
            "patchbay-nodes", UNCHECKED,
            f"{patchbay_file.name} could not be checked against the graph: "
            f"{reason or 'no reason given'}",
            "`sushi-rig patchbay check` reports on it on its own",
        )

    if dump is None:
        checks.append(not_compared(why))
    else:
        try:
            links = parse_patchbay(patchbay_file.read_text())
            result = check_nodes(links, live_node_names(dump))
        except OSError as exc:
            checks.append(not_compared(str(exc)))
        except PatchbayError as exc:
            checks.append(not_compared("; ".join(exc.problems)))
        else:
            checks += patchbay_checks(
                result.missing, result.not_running,
                file_name=patchbay_file.name, links=len(links),
            )

    checks += environment_checks(gather_environment(root))

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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sushi-rig", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("emit", help="rig.yaml (+ state) -> Sushi config")
    p.add_argument("rig", type=Path)
    p.add_argument("--state", type=Path, help="captured state file to fold in")
    p.add_argument("-o", "--out", type=Path, default=Path("rig.json"))
    p.add_argument("--sushi", default="sushi")
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
    p.add_argument("--force", action="store_true", help="replace a rig file already there")
    p.add_argument("--name", help="the rig's name [default: the config's _meta name, or its file name]")

    p = sub.add_parser("push", help="build the rig in a running Sushi")
    p.add_argument("rig", type=Path)
    p.add_argument("--address", default=DEFAULT_GRPC_ADDRESS)

    p = sub.add_parser("capture", help="read parameter state from a running Sushi")
    p.add_argument("--address", default=DEFAULT_GRPC_ADDRESS)
    p.add_argument("-o", "--out", type=Path, default=Path("state.json"))

    p = sub.add_parser(
        "panel",
        help="Open Stage Control panel from a config, using live values from a running Sushi",
    )
    p.add_argument("config", type=Path)
    p.add_argument("--sushi", default="sushi")
    p.add_argument(
        "--address",
        default=DEFAULT_GRPC_ADDRESS,
        help="the running Sushi to read current parameter values from — "
        "must already be running this same config",
    )
    p.add_argument(
        "--osc-port",
        type=int,
        default=24024,
        help="Sushi's OSC receive port (--osc-rcv-port), just to print the matching "
        "open-stage-control --send value — not written into the panel file",
    )
    p.add_argument(
        "--listener-port",
        type=int,
        default=DEFAULT_LISTEN_PORT,
        help="port the save button targets — must match 'sushi-rig listen --port'",
    )
    p.add_argument(
        "--record-bar",
        action="store_true",
        help="add RECORD and STOP under the save bar. Until there is a capture "
        "engine they only say so; for checking the layout on the rig",
    )
    p.add_argument("-o", "--out", type=Path, default=Path("panel.json"))

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

    p = sub.add_parser("save", help="capture live state and write it as a named config")
    p.add_argument("name", help="config name — becomes config/<name>.json")
    p.add_argument("--rig", type=Path, required=True, help="rig.yaml this config is built from")
    p.add_argument("--out-dir", type=Path, default=Path("../config"))
    p.add_argument("--archive-dir", type=Path, default=Path("../config/archive"))
    p.add_argument("--address", default=DEFAULT_GRPC_ADDRESS)

    p = sub.add_parser(
        "up",
        help="start the whole rig — patchbay, tuner, Sushi, the amp, and (by "
        "default) the control panel — then return to the prompt",
    )
    p.add_argument(
        "config_name",
        nargs="?",
        default=DEFAULT_CONFIG_NAME,
        help=f"names config/<name>.json [default: {DEFAULT_CONFIG_NAME}]",
    )
    p.add_argument(
        "--headless",
        action="store_true",
        help="no panel and no save listener — for playing through the rig "
        "rather than authoring a tone",
    )
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
        "--amp-model",
        metavar="NAME",
        help="use this model instead of the one in the config — a filename in "
        "amp/models/ or a path. For auditioning without editing the config; "
        "a save then records whichever was actually used",
    )

    p = sub.add_parser("down", help="stop a running rig and verify it stopped")
    p.add_argument(
        "--force",
        action="store_true",
        help="skip straight to SIGKILL, giving Sushi no chance to release its "
        "JACK ports cleanly — for a rig that is already wedged",
    )

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
    p.add_argument(
        "--sushi",
        help="Sushi binary [default: ./sushi in the rig checkout, the one `up` runs]",
    )

    p = sub.add_parser(
        "nulltest",
        help="render a config offline and check its sound against an approved "
        "reference",
    )
    p.add_argument("config", type=Path, nargs="?", help="config to check")
    p.add_argument(
        "--all", action="store_true", dest="check_all",
        help="every config in config/, skipping retired ones",
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
        "--sushi",
        help="Sushi binary [default: ./sushi in the rig checkout, the one `up` runs]",
    )

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
        help="LV2 directory to take bundles from. Repeatable, earlier wins "
        "[default: /usr/lib/lv2]",
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
        "check",
        help="every required plugin URI resolves with LV2_PATH set to plugins/ alone",
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
        "prints it unless -o is given, and never replaces the committed one",
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
        "probe links. Reads nothing live; exit 1 if the rig cannot be probed",
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

    sub.add_parser("status", help="what is running, if anything")
    sub.add_parser(
        "restart",
        help="stop the running rig and bring the same one back — same config, same mode, same amp and tuner choices",
    )

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
        "listen",
        help="run an OSC listener that saves a config on request (for the panel's save button)",
    )
    p.add_argument("--rig", type=Path, required=True, help="rig.yaml every save is built from")
    p.add_argument("--out-dir", type=Path, default=Path("../config"))
    p.add_argument("--archive-dir", type=Path, default=Path("../config/archive"))
    p.add_argument("--address", default=DEFAULT_GRPC_ADDRESS, help="Sushi's gRPC address")
    p.add_argument(
        "--host",
        default="127.0.0.1",
        help="address to listen on — defaults to loopback-only; widen deliberately",
    )
    p.add_argument("--port", type=int, default=DEFAULT_LISTEN_PORT)
    p.add_argument(
        "--status-host",
        default=DEFAULT_STATUS_HOST,
        help="where to send save-outcome messages — open-stage-control's OSC input",
    )
    p.add_argument("--status-port", type=int, default=DEFAULT_STATUS_PORT)

    # Supplying these turns on panel auto-refresh: after a move, the panel is
    # regenerated and open-stage-control is told to reload it, so the tabs end
    # up in the rig's new order. Optional — without them a move still works, it
    # just leaves the tabs showing the old order.
    p.add_argument(
        "--panel-config",
        type=Path,
        help="config to regenerate the panel from after a chain reorder",
    )
    p.add_argument(
        "--panel-out",
        type=Path,
        help="panel file to rewrite and reload (the one open-stage-control loaded)",
    )
    p.add_argument(
        "--sushi",
        default="sushi",
        help="sushi binary, used to re-dump plugin parameters when regenerating",
    )

    # The amp runs outside Sushi and cannot be captured from it, so the save
    # button can only record what it was told. See sushi_rig/amp.py.
    p.add_argument(
        "--amp-model",
        help="model path to record in a saved config's _amp section",
    )
    p.add_argument(
        "--amp-parameter",
        action="append",
        default=[],
        metavar="NAME=VALUE",
        help="starting value for an amp control, repeatable",
    )

    args = parser.parse_args(argv)

    if args.command == "emit":
        state = json.loads(args.state.read_text()) if args.state else None
        write_json(args.out, _emit_any(args.rig, state))
        if args.verify:
            # The cheap checks only: they cost milliseconds and catch a bad
            # config at the moment it is created. The check that starts Sushi
            # stays an explicit `sushi-rig verify`.
            from .verify import VerifyError, verify

            try:
                problems = verify(args.out, args.sushi, quick=True)
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
    elif args.command == "panel":
        from .live import get_live_bypass_state, get_live_parameter_info
        from .amp import panel_amp
        from .probe import units_for_config
        from .paths import checkout_root

        dump = dump_plugins(args.config, args.sushi)
        live_info = get_live_parameter_info(args.address)
        bypass_info = get_live_bypass_state(args.address)
        write_json(
            args.out,
            build_osc_panel(
                dump,
                live_info,
                args.listener_port,
                bypass_info,
                units_for_config(args.config),
                panel_amp(args.config, checkout_root()),
                record_bar=args.record_bar,
            ),
        )
        print(
            f"open with: open-stage-control --load {args.out} "
            f"--send 127.0.0.1:{args.osc_port}"
        )
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
                if d["atom_ports"]:
                    reasons.append(f"{d['atom_ports']} atom port(s)")
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
        from .save import SaveError, save_config

        try:
            out_path = save_config(
                args.name, args.rig, args.out_dir, args.archive_dir, args.address
            )
        except SaveError as exc:
            sys.exit(str(exc))
        print(f"saved {out_path}")
    elif args.command == "verify":
        from .paths import checkout_root
        from .verify import VerifyError, configs_to_check, verify

        if args.check_all:
            targets = configs_to_check(checkout_root())
        elif args.config:
            targets = [args.config]
        else:
            sys.exit("give a config to verify, or --all")

        # The rig's own ./sushi, not whatever `sushi` is on PATH: verify should
        # check the Sushi the rig actually plays through, and with a PATH
        # default it failed with "No such file" on any machine where Sushi is
        # not also installed system-wide.
        sushi = args.sushi or str(checkout_root() / "sushi")

        failed = 0
        for target in targets:
            try:
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
    elif args.command in ("up", "down", "status", "restart"):
        from .amp import AmpError
        from .rig import RigError, down, restart, status, up

        # AmpError is caught alongside RigError rather than subclassing it:
        # amp.py is imported *by* rig.py, so inheriting the other way round
        # would be circular. Both mean "something the user needs to fix", and
        # both should read as a message rather than a traceback.
        try:
            if args.command == "up":
                return up(
                    args.config_name, headless=args.headless,
                    tuner=args.tuner, amp=args.amp,
                    amp_model=args.amp_model,
                )
            if args.command == "down":
                return down(args.force)
            if args.command == "restart":
                return restart()
            return status()
        except (RigError, AmpError) as exc:
            sys.exit(str(exc))
    elif args.command == "listen":
        from .listen import serve

        serve(
            args.rig,
            args.out_dir,
            args.archive_dir,
            address=args.address,
            host=args.host,
            port=args.port,
            status_host=args.status_host,
            status_port=args.status_port,
            panel_config=args.panel_config,
            panel_out=args.panel_out,
            sushi_bin=args.sushi,
            amp_model=args.amp_model,
            amp_parameters=_amp_parameters(args.amp_parameter),
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
