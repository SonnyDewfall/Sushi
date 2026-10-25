"""Component profiles: how one plugin is presented, with nothing invented.

A profile describes one plugin, pinned by URI and version, and how a panel
should present it. Its author is, eventually, an AI working from metadata, docs
and screenshots — so the format exists less to say what a profile contains than
to make it *impossible to smuggle in a fabrication*. The split that does the
work:

- **Facts** come from `probe` (lilv reading the plugin's own TTL) and never from
  anyone's say-so: a parameter's port symbol, the name Sushi uses for it, its
  range, default, units, enumeration values, whether it is settable, and whether
  the plugin can be driven from a config at all. A profile *may* restate facts
  under `facts:` so a human reading the file need not run probe, but
  `validate()` checks every restated fact against probe's and rejects any it
  cannot verify. A restated fact that probe does not report is an error, not a
  pass: an unverifiable fact is exactly how an invention gets in.
- **Judgements** are the presentation choices — visibility, group, order,
  label, description, display scale, control type, phone priority. They cannot
  be checked against the plugin, only against its facts, so each carries
  `provenance`: `draft` (proposed, unreviewed), `reviewed` (a human approved it)
  or `fact` (derived mechanically). `fact` is only accepted where the value
  really is derivable, and `validate()` re-derives it to check — otherwise it
  would be a free pass for an AI to stamp anything "fact".

## Why parameters are identified by symbol AND name

lilv gives the port *symbol* (`fslider2_`); Sushi addresses a parameter by the
`lv2:name` (`Drive`) with spaces turned into underscores in OSC paths — see
`examples/dump.example.json`. The panel needs the second, the plugin's TTL
gives the first, and a profile that carried only one would leave the mapping
between them to be guessed. Both are required, and both must name the *same*
port.

## What the validator guards against, and what it cannot

It rejects: parameters probe does not know, restated facts that disagree,
controls the facts do not allow (a toggle on a port that is not toggled, a
selector with nothing to select, a log scale across zero), undefined groups,
zones off their screenshot, duplicates, unknown schema versions, and controls
on a plugin that cannot be driven. It also rejects unknown keys everywhere: a
typo like `visbility` would otherwise be silently ignored, and silence is the
failure mode this whole format is built against.

It cannot tell whether a label is *good* or a zone is *right*. That is what
`reviewed` and zone `sweep` confirmation are for, and the validator only warns
about the unreviewed remainder.

## Gaps in probe this module tolerates but does not paper over

`probe.describe()` currently reports neither the plugin version, units, nor
enumeration scale points. `validate()` therefore:
- warns that the pinned version is unverified when probe gives none (and errors
  on a mismatch when it does, under `facts["version"]`);
- errors on a restated `unit` or `scale_points` when probe gives none, rather
  than trusting it. Probe should grow these keys (`version`, per-parameter
  `unit` and `scale_points`) — this module already compares against them when
  present. `settable` needs no probe field: probe lists only *input control
  ports*, and every one of those is settable; meters and other output ports are
  not listed at all, so naming one in a profile is reported as invented.

## Display scales

- `linear`, `log`: the drag-to-value mapping. `log` needs a strictly positive
  minimum — the logarithm of a range containing zero is undefined, and a panel
  asked to draw it would silently clamp.
- `db`: the parameter holds a *linear amplitude gain* and is shown and dragged
  in decibels (the wide-domain gain multipliers `panel.py` has to cope with).
  It is rejected for a port whose unit is already `dB`, where the value is
  decibels and `linear` is the honest scale.
- `enum`, `toggle`: only for ports whose facts say enumeration/toggled.

## The file format

YAML, machine-written but meant to be read and diffed by a person. `dump_profile`
writes one canonical form: fixed key order, a blank line between parameters,
each judgement and each zone bbox on a single line. Round-tripping
dump -> load -> dump is byte-identical, so a diff of a regenerated profile shows
only real changes.

What is **not** preserved: comments. PyYAML discards them on load, so a
hand-added comment is lost the next time a tool rewrites the file. The header
comment is regenerated, not preserved. Put anything worth keeping in a
`description` or a `provenance`, not a comment. Unknown keys *are* preserved by
`dump_profile` (appended after the known ones, sorted) so that rewriting never
loses data — though `validate()` rejects them, so they never survive review.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypedDict

import yaml

SCHEMA_VERSION = 1
SUPPORTED_SCHEMA_VERSIONS = frozenset({SCHEMA_VERSION})

KINDS = ("effect", "instrument", "midi-effect", "audio-to-midi", "amp", "utility")
# Hosts a profile has been written against. An unfamiliar one is a warning, not
# an error: a new host is legitimate, a typo is not, and only a person can tell.
KNOWN_HOSTS = ("sushi-lv2", "carla-lv2")
VISIBILITIES = ("primary", "advanced", "hidden")
SCALES = ("linear", "log", "db", "enum", "toggle")
CONTROLS = ("knob", "fader", "toggle", "selector")
PROVENANCES = ("draft", "reviewed", "fact")
ZONE_CONFIRMATIONS = ("sweep", "draft")

#: Longest description that reads as "short" on a phone.
MAX_DESCRIPTION = 100

# --- shape of the document ---------------------------------------------------
# TypedDicts document the shape; validation works on plain dicts because the
# input is untrusted (an AI's output may have the wrong type anywhere, and a
# constructor that raises on the first bad field would report one problem
# where the validator should report them all).


class Judgement(TypedDict):
    value: Any
    provenance: str


class Zone(TypedDict):
    parameter: str
    bbox: list[int]
    confirmed: str


class Screenshot(TypedDict):
    file: str
    width: int
    height: int


class Face(TypedDict, total=False):
    screenshot: Screenshot
    zones: list[Zone]


class Parameter(TypedDict, total=False):
    name: str
    symbol: str
    facts: dict[str, Any]
    visibility: Judgement
    group: Judgement
    order: Judgement
    label: Judgement
    description: Judgement
    scale: Judgement
    control: Judgement
    phone: Judgement


class Profile(TypedDict, total=False):
    schema_version: int
    plugin: dict[str, Any]
    kind: str
    host: str
    ports: dict[str, Any]
    instrument: dict[str, Any]
    groups: list[dict[str, Any]]
    parameters: list[Parameter]
    face: Face


# Canonical key order for every mapping in the format. Anything not listed is
# appended after, sorted, so rewriting a file never drops data.
_TOP_KEYS = (
    "schema_version", "plugin", "kind", "host", "ports", "instrument",
    "groups", "parameters", "face",
)
_PLUGIN_KEYS = ("uri", "version", "name")
_PORTS_KEYS = ("audio_in", "audio_out", "config_drivable")
_INSTRUMENT_KEYS = ("midi_in", "polyphony", "patch_banks")
_GROUP_KEYS = ("id", "label", "provenance")
_FACT_KEYS = (
    "min", "max", "default", "unit", "toggled", "integer", "enumeration",
    "logarithmic", "scale_points", "settable",
)
_JUDGEMENT_KEYS = (
    "visibility", "group", "order", "label", "description", "scale", "control",
    "phone",
)
_PARAMETER_KEYS = ("name", "symbol", "facts") + _JUDGEMENT_KEYS
_FACE_KEYS = ("screenshot", "zones")
_SCREENSHOT_KEYS = ("file", "width", "height")
_ZONE_KEYS = ("parameter", "bbox", "confirmed")
_SCALE_POINT_KEYS = ("value", "label")

_NUMERIC_FACTS = ("min", "max", "default")
_BOOL_FACTS = ("toggled", "integer", "enumeration", "logarithmic", "settable")

# --- findings ---------------------------------------------------------------


@dataclass(frozen=True)
class Finding:
    """One thing the validator has to say. `code` is stable and machine-checkable
    (tests and tooling match on it); `message` is for the person."""

    severity: str  # "error" | "warning"
    code: str
    path: str
    message: str

    def __str__(self) -> str:
        where = f" {self.path}:" if self.path else ""
        return f"{self.severity}[{self.code}]{where} {self.message}"


def errors(findings: list[Finding]) -> list[Finding]:
    return [f for f in findings if f.severity == "error"]


def warnings(findings: list[Finding]) -> list[Finding]:
    return [f for f in findings if f.severity == "warning"]


class _Collector:
    def __init__(self) -> None:
        self.findings: list[Finding] = []

    def error(self, code: str, path: str, message: str) -> None:
        self.findings.append(Finding("error", code, path, message))

    def warn(self, code: str, path: str, message: str) -> None:
        self.findings.append(Finding("warning", code, path, message))


# --- small pure helpers ------------------------------------------------------


def _is_int(value: Any) -> bool:
    # bool is an int subclass; `order: true` must not pass as the integer 1.
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _same_number(a: Any, b: Any) -> bool:
    # YAML writes floats via repr, so a round-trip is exact, but a hand-typed
    # 0.1 against a float32-derived probe value is not worth failing on at the
    # 1e-12 level.
    return _is_number(a) and _is_number(b) and math.isclose(
        a, b, rel_tol=1e-9, abs_tol=1e-12
    )


def derive_scale(fact: dict[str, Any]) -> str:
    """The display scale the facts alone imply. This is what makes
    `provenance: fact` checkable for `scale`: it is only honest if it equals
    this."""
    if fact.get("toggled"):
        return "toggle"
    if fact.get("enumeration"):
        return "enum"
    if fact.get("logarithmic"):
        return "log"
    return "linear"


def _has_choices(fact: dict[str, Any]) -> bool:
    return bool(fact.get("enumeration") or fact.get("scale_points"))


# --- loading and dumping -----------------------------------------------------


class ProfileError(Exception):
    """A profile file that cannot be read as a profile at all."""


class _StrictLoader(yaml.SafeLoader):
    """SafeLoader that refuses a repeated key.

    PyYAML silently keeps the last of two identical keys, so a profile with two
    `visibility:` lines would load as if the first never existed — the reviewer
    sees one value, the program uses another.
    """

    def construct_mapping(self, node, deep=False):  # type: ignore[override]
        seen = set()
        for key_node, _ in node.value:
            key = self.construct_object(key_node, deep=True)
            if key in seen:
                raise ProfileError(
                    f"duplicate key {key!r} at line {key_node.start_mark.line + 1}"
                )
            seen.add(key)
        return super().construct_mapping(node, deep)


def load_profile_text(text: str) -> dict[str, Any]:
    try:
        data = yaml.load(text, Loader=_StrictLoader)  # noqa: S506 - SafeLoader subclass
    except yaml.YAMLError as exc:
        raise ProfileError(f"not valid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise ProfileError("a profile must be a YAML mapping at the top level")
    return data


def load_profile(path: str | Path) -> dict[str, Any]:
    path = Path(path)
    try:
        text = path.read_text()
    except OSError as exc:
        raise ProfileError(f"cannot read {path}: {exc}") from exc
    try:
        return load_profile_text(text)
    except ProfileError as exc:
        raise ProfileError(f"{path}: {exc}") from exc


class _Flow(dict):
    """Marks a mapping to be written on one line: `{value: knob, provenance: draft}`."""


class _FlowList(list):
    """Marks a sequence to be written on one line: `[10, 20, 30, 40]`."""


class _CanonicalDumper(yaml.SafeDumper):
    # No anchors/aliases: the same dict appearing twice would otherwise be
    # written as `&id001`/`*id001`, which a person cannot read at a glance.
    def ignore_aliases(self, data):  # type: ignore[override]
        return True

    # PyYAML indents a block mapping's lists flush with the key by default
    # (`groups:\n- id: x`), which is legal and hard to scan. Indenting them is
    # the difference between a list that reads as belonging to its key and one
    # that does not.
    def increase_indent(self, flow=False, indentless=False):  # type: ignore[override]
        return super().increase_indent(flow, False)


def _represent_float(dumper: yaml.Dumper, value: float):
    text = repr(value)
    # PyYAML's float resolver (YAML 1.1) wants a '.' in the mantissa, so
    # repr(1e-05) = "1e-05" would be written bare and read back as a *string*.
    if "e" in text and "." not in text and "inf" not in text and "nan" not in text:
        text = text.replace("e", ".0e")
    if value != value:
        text = ".nan"
    elif value in (math.inf, -math.inf):
        text = ".inf" if value > 0 else "-.inf"
    return dumper.represent_scalar("tag:yaml.org,2002:float", text)


_CanonicalDumper.add_representer(float, _represent_float)
_CanonicalDumper.add_representer(
    _Flow,
    lambda d, data: d.represent_mapping("tag:yaml.org,2002:map", data, flow_style=True),
)
_CanonicalDumper.add_representer(
    _FlowList,
    lambda d, data: d.represent_sequence("tag:yaml.org,2002:seq", data, flow_style=True),
)
# dict is written in insertion order (see _ordered) rather than PyYAML's
# default sort, which would put `symbol` before `name` and `max` before `min`.
_CanonicalDumper.add_representer(
    dict,
    lambda d, data: d.represent_mapping("tag:yaml.org,2002:map", data.items()),
)


def _ordered(mapping: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    known = {k: mapping[k] for k in keys if k in mapping}
    extra = {k: mapping[k] for k in sorted(mapping, key=str) if k not in keys}
    return {**known, **extra}


def canonicalise(profile: dict[str, Any]) -> dict[str, Any]:
    """The profile with canonical key order and one-line markers applied.

    Tolerates a malformed profile (wrong types are passed through untouched) so
    that a file `validate()` rejects can still be rewritten and inspected.
    """
    top = _ordered(profile, _TOP_KEYS)

    def section(key: str, order: tuple[str, ...]) -> None:
        if isinstance(top.get(key), dict):
            top[key] = _ordered(top[key], order)

    section("plugin", _PLUGIN_KEYS)
    section("ports", _PORTS_KEYS)
    section("instrument", _INSTRUMENT_KEYS)

    if isinstance(top.get("groups"), list):
        top["groups"] = [
            _ordered(g, _GROUP_KEYS) if isinstance(g, dict) else g
            for g in top["groups"]
        ]

    if isinstance(top.get("parameters"), list):
        top["parameters"] = [_canonical_parameter(p) for p in top["parameters"]]

    face = top.get("face")
    if isinstance(face, dict):
        face = _ordered(face, _FACE_KEYS)
        if isinstance(face.get("screenshot"), dict):
            face["screenshot"] = _ordered(face["screenshot"], _SCREENSHOT_KEYS)
        if isinstance(face.get("zones"), list):
            face["zones"] = [_canonical_zone(z) for z in face["zones"]]
        top["face"] = face
    return top


def _canonical_parameter(entry: Any) -> Any:
    if not isinstance(entry, dict):
        return entry
    out = _ordered(entry, _PARAMETER_KEYS)
    if isinstance(out.get("facts"), dict):
        facts = _ordered(out["facts"], _FACT_KEYS)
        if isinstance(facts.get("scale_points"), list):
            facts["scale_points"] = [
                _Flow(_ordered(sp, _SCALE_POINT_KEYS)) if isinstance(sp, dict) else sp
                for sp in facts["scale_points"]
            ]
        out["facts"] = facts
    for key in _JUDGEMENT_KEYS:
        if isinstance(out.get(key), dict):
            ordered = _ordered(out[key], ("value", "provenance"))
            # Free text stays in block style: inside a one-line `{...}` a
            # description's own commas and colons force quoting and bury the
            # provenance at the end of a very long line.
            out[key] = ordered if key == "description" else _Flow(ordered)
    return out


def _canonical_zone(zone: Any) -> Any:
    if not isinstance(zone, dict):
        return zone
    out = _ordered(zone, _ZONE_KEYS)
    if isinstance(out.get("bbox"), list):
        out["bbox"] = _FlowList(out["bbox"])
    return out


_HEADER = (
    "# sushi-rig component profile (schema {version}).\n"
    "# Machine-written and canonical: edit by hand if you like, but comments\n"
    "# do not survive the next rewrite. Facts are checked against `sushi-rig\n"
    "# probe`; judgements carry provenance (draft / reviewed / fact).\n"
    "# Check with:  sushi-rig profile validate <this file>\n"
)


def dump_profile(profile: dict[str, Any]) -> str:
    """Canonical YAML text for a profile. Same input, same bytes, always."""
    body = yaml.dump(
        canonicalise(profile),
        Dumper=_CanonicalDumper,
        default_flow_style=False,
        sort_keys=False,
        allow_unicode=True,
        # Effectively no wrapping. A folded plain scalar is deterministic but
        # reads as if the description had a line break in it.
        width=1_000_000,
    )
    return _HEADER.format(version=profile.get("schema_version", "?")) + _spaced(body)


def _spaced(body: str) -> str:
    """Blank lines before each top-level block and each parameter.

    A profile of twenty parameters is a wall of text otherwise. Blank lines are
    ignored by the loader, so this cannot change what the file means, and being
    derived purely from structure it keeps the output canonical.
    """
    out: list[str] = []
    section = ""
    for line in body.splitlines():
        if line and not line[0].isspace() and not line.startswith("-"):
            section = line.split(":", 1)[0]
            # Only sections that open a block; the scalars at the top
            # (schema_version, kind, host) read better kept together.
            if out and line.endswith(":"):
                out.append("")
        elif section == "parameters" and line.startswith("  - "):
            if not out[-1].startswith("parameters:"):
                out.append("")
        out.append(line)
    return "\n".join(out) + "\n"


def save_profile(profile: dict[str, Any], path: str | Path) -> None:
    Path(path).write_text(dump_profile(profile))


# --- validation --------------------------------------------------------------


def validate(profile: Any, facts: dict[str, Any]) -> list[Finding]:
    """Check a profile against probe's facts for the same plugin.

    Pure: no files, no lilv. `profile` is the loaded document (a dict, however
    malformed); `facts` is one plugin's `probe.describe()` output, optionally
    carrying `version` and, per parameter, `unit` and `scale_points`.

    Never raises on bad input — every problem it can find is returned, so a
    reviewer fixes them in one pass.
    """
    c = _Collector()
    if not isinstance(profile, dict):
        c.error("bad-type", "", "a profile must be a mapping")
        return c.findings

    _unknown_keys(c, "", profile, _TOP_KEYS)
    _check_schema_version(c, profile)
    _check_plugin(c, profile.get("plugin"), facts)
    _check_kind_and_host(c, profile)
    _check_ports(c, profile.get("ports"), facts)
    _check_instrument(c, profile)
    group_ids = _check_groups(c, profile.get("groups"))
    declared = _check_parameters(c, profile.get("parameters"), facts, group_ids)
    _check_face(c, profile.get("face"), facts, declared)

    drivable = bool(facts.get("config_drivable"))
    if not drivable and declared:
        c.error(
            "not-drivable",
            "parameters",
            f"{facts.get('uri', 'this plugin')} cannot be driven from a config "
            "(it keeps state outside its control ports), so declaring controls "
            "for it would present knobs that do nothing",
        )
    if drivable:
        for prm in facts.get("parameters", []):
            if prm["symbol"] not in declared:
                c.warn(
                    "unmentioned-parameter",
                    "parameters",
                    f"settable parameter {prm['name']!r} ({prm['symbol']}) is not "
                    "mentioned; declare it, even as hidden, so omission is a decision",
                )
    return c.findings


def _unknown_keys(c: _Collector, path: str, mapping: dict, allowed: tuple[str, ...]) -> None:
    for key in mapping:
        if key not in allowed:
            c.error(
                "unknown-key", f"{path}.{key}".lstrip("."),
                f"unknown key {key!r}; allowed here: {', '.join(allowed)}",
            )


def _check_schema_version(c: _Collector, profile: dict) -> None:
    if "schema_version" not in profile:
        c.error(
            "missing-schema-version", "schema_version",
            f"schema_version is required (current: {SCHEMA_VERSION})",
        )
        return
    version = profile["schema_version"]
    if not _is_int(version) or version not in SUPPORTED_SCHEMA_VERSIONS:
        c.error(
            "unknown-schema-version", "schema_version",
            f"schema_version {version!r} is not supported "
            f"(supported: {sorted(SUPPORTED_SCHEMA_VERSIONS)})",
        )


def _check_plugin(c: _Collector, plugin: Any, facts: dict[str, Any]) -> None:
    if not isinstance(plugin, dict):
        c.error("bad-type", "plugin", "plugin must be a mapping with uri and version")
        return
    _unknown_keys(c, "plugin", plugin, _PLUGIN_KEYS)
    uri = plugin.get("uri")
    if not isinstance(uri, str) or not uri:
        c.error("bad-type", "plugin.uri", "plugin.uri is required")
    elif uri != facts.get("uri"):
        c.error(
            "plugin-mismatch", "plugin.uri",
            f"profile is for {uri!r} but probe facts are for {facts.get('uri')!r}",
        )

    version = plugin.get("version")
    if not isinstance(version, str) or not version:
        # A YAML float (`version: 43.0`) lands here: version strings like
        # "1.10" and "1.1" are different and must survive as text.
        c.error("bad-type", "plugin.version",
                "plugin.version is required, as a quoted string (e.g. \"43.0\")")
    elif "version" not in facts:
        c.warn(
            "unverified-version", "plugin.version",
            f"probe did not report a version, so the pin {version!r} is unchecked",
        )
    elif version != facts["version"]:
        c.error(
            "fact-mismatch", "plugin.version",
            f"pinned to {version!r} but the installed plugin is {facts['version']!r}",
        )

    if "name" in plugin and plugin["name"] != facts.get("name"):
        c.error(
            "fact-mismatch", "plugin.name",
            f"restated name {plugin['name']!r} but probe says {facts.get('name')!r}",
        )


def _check_kind_and_host(c: _Collector, profile: dict) -> None:
    kind = profile.get("kind")
    if kind not in KINDS:
        c.error("bad-value", "kind", f"kind {kind!r} must be one of {', '.join(KINDS)}")
    host = profile.get("host")
    if not isinstance(host, str) or not host:
        c.error("bad-type", "host", "host is required, e.g. sushi-lv2")
    elif host not in KNOWN_HOSTS:
        c.warn("unknown-host", "host",
               f"host {host!r} is not one of {', '.join(KNOWN_HOSTS)}; typo?")


def _check_ports(c: _Collector, ports: Any, facts: dict[str, Any]) -> None:
    if ports is None:
        return
    if not isinstance(ports, dict):
        c.error("bad-type", "ports", "ports must be a mapping")
        return
    _unknown_keys(c, "ports", ports, _PORTS_KEYS)
    for key in _PORTS_KEYS:
        if key in ports and ports[key] != facts.get(key):
            c.error(
                "fact-mismatch", f"ports.{key}",
                f"restated {ports[key]!r} but probe says {facts.get(key)!r}",
            )


def _check_instrument(c: _Collector, profile: dict) -> None:
    extras = profile.get("instrument")
    if extras is None:
        return
    if profile.get("kind") != "instrument":
        c.error("bad-value", "instrument",
                "instrument extras belong only on a plugin of kind 'instrument'")
    if not isinstance(extras, dict):
        c.error("bad-type", "instrument", "instrument must be a mapping")
        return
    _unknown_keys(c, "instrument", extras, _INSTRUMENT_KEYS)
    if "midi_in" in extras and not isinstance(extras["midi_in"], bool):
        c.error("bad-type", "instrument.midi_in", "midi_in must be true or false")
    poly = extras.get("polyphony")
    if poly is not None and (not _is_int(poly) or poly < 1):
        c.error("bad-type", "instrument.polyphony",
                "polyphony must be a positive integer, or null when unknown")
    banks = extras.get("patch_banks")
    if banks is not None and not (
        isinstance(banks, list) and all(isinstance(b, str) for b in banks)
    ):
        c.error("bad-type", "instrument.patch_banks", "patch_banks must be a list of names")


def _check_groups(c: _Collector, groups: Any) -> set[str]:
    ids: set[str] = set()
    if groups is None:
        return ids
    if not isinstance(groups, list):
        c.error("bad-type", "groups", "groups must be an ordered list")
        return ids
    for i, group in enumerate(groups):
        path = f"groups[{i}]"
        if not isinstance(group, dict):
            c.error("bad-type", path, "a group must be a mapping")
            continue
        _unknown_keys(c, path, group, _GROUP_KEYS)
        gid = group.get("id")
        if not isinstance(gid, str) or not gid:
            c.error("bad-type", f"{path}.id", "a group needs an id")
        elif gid in ids:
            c.error("duplicate-group", f"{path}.id", f"group {gid!r} is defined twice")
        else:
            ids.add(gid)
        if not isinstance(group.get("label"), str) or not group.get("label"):
            c.error("bad-type", f"{path}.label", "a group needs a label")
        # A group is a judgement but cannot be mechanically derived.
        if group.get("provenance") not in ("draft", "reviewed"):
            c.error("bad-value", f"{path}.provenance",
                    "group provenance must be draft or reviewed")
    return ids


def _check_parameters(
    c: _Collector, params: Any, facts: dict[str, Any], group_ids: set[str]
) -> set[str]:
    """Returns the symbols of the parameters the profile validly declares."""
    declared: set[str] = set()
    if params is None:
        c.error("bad-type", "parameters", "parameters is required (may be an empty list)")
        return declared
    if not isinstance(params, list):
        c.error("bad-type", "parameters", "parameters must be a list")
        return declared

    by_symbol = {p["symbol"]: p for p in facts.get("parameters", [])}
    by_name = {p["name"]: p for p in facts.get("parameters", [])}
    seen_symbols: set[str] = set()
    seen_names: set[str] = set()
    orders: dict[str, dict[int, str]] = {}

    for i, entry in enumerate(params):
        path = f"parameters[{i}]"
        if not isinstance(entry, dict):
            c.error("bad-type", path, "a parameter must be a mapping")
            continue
        _unknown_keys(c, path, entry, _PARAMETER_KEYS)
        name, symbol = entry.get("name"), entry.get("symbol")
        if not isinstance(name, str) or not isinstance(symbol, str):
            c.error("bad-type", path,
                    "a parameter needs both `name` (what Sushi calls it) and `symbol` (the port symbol)")
            continue
        path = f"parameters[{i}] ({name})"

        if symbol in seen_symbols or name in seen_names:
            c.error("duplicate-parameter", path,
                    f"{name!r} / {symbol!r} appears more than once")
            continue
        seen_symbols.add(symbol)
        seen_names.add(name)

        fact = by_symbol.get(symbol)
        if fact is None or fact["name"] != name:
            if symbol not in by_symbol:
                c.error("invented-parameter", path,
                        f"probe knows no input control port with symbol {symbol!r}")
            elif name not in by_name:
                c.error("invented-parameter", path,
                        f"probe knows no parameter named {name!r} "
                        f"(symbol {symbol!r} is called {by_symbol[symbol]['name']!r} in Sushi)")
            else:
                c.error("identity-mismatch", path,
                        f"symbol {symbol!r} and name {name!r} belong to different ports")
            continue
        declared.add(symbol)

        if "facts" in entry:
            _check_facts(c, f"{path}.facts", entry["facts"], fact)
        _check_judgements(c, path, entry, fact, group_ids, orders)
    return declared


def _check_facts(c: _Collector, path: str, restated: Any, fact: dict[str, Any]) -> None:
    if not isinstance(restated, dict):
        c.error("bad-type", path, "facts must be a mapping")
        return
    _unknown_keys(c, path, restated, _FACT_KEYS)
    for key in _FACT_KEYS:
        if key not in restated:
            continue
        value = restated[key]
        where = f"{path}.{key}"
        if key == "settable":
            # Every port probe lists is an input control port, hence settable.
            truth: Any = True
        elif key in fact:
            truth = fact[key]
        else:
            c.error(
                "unverifiable-fact", where,
                f"{key} is restated but probe did not report it, so it cannot be "
                "checked; remove it (or teach probe to report it)",
            )
            continue
        if key in _NUMERIC_FACTS:
            same = _same_number(value, truth) or (value is None and truth is None)
        elif key in _BOOL_FACTS:
            same = isinstance(value, bool) and value == truth
        else:  # unit, scale_points: compared as-is
            same = value == truth
        if not same:
            c.error("fact-mismatch", where, f"restated {value!r} but probe says {truth!r}")


def _judgement(c: _Collector, path: str, entry: dict, key: str) -> Judgement | None:
    """The `{value, provenance}` pair for one judgement, or None if absent/bad."""
    if key not in entry:
        return None
    raw = entry[key]
    where = f"{path}.{key}"
    if not isinstance(raw, dict):
        c.error("bad-type", where,
                "a judgement is a mapping of `value` and `provenance`")
        return None
    _unknown_keys(c, where, raw, ("value", "provenance"))
    if "value" not in raw:
        c.error("bad-type", where, "judgement has no `value`")
        return None
    if raw.get("provenance") not in PROVENANCES:
        c.error("bad-value", f"{where}.provenance",
                f"provenance {raw.get('provenance')!r} must be one of {', '.join(PROVENANCES)}")
        return None
    return {"value": raw["value"], "provenance": raw["provenance"]}


def _check_judgements(
    c: _Collector,
    path: str,
    entry: dict,
    fact: dict[str, Any],
    group_ids: set[str],
    orders: dict[str, dict[int, str]],
) -> None:
    j = {k: _judgement(c, path, entry, k) for k in _JUDGEMENT_KEYS}

    if "visibility" not in entry:
        c.error("missing-judgement", f"{path}.visibility",
                "every declared parameter needs a visibility (hidden is a valid answer)")

    def value(key: str) -> Any:
        return j[key]["value"] if j[key] else None

    enums = {"visibility": VISIBILITIES, "scale": SCALES, "control": CONTROLS}
    for key, allowed in enums.items():
        if j[key] and value(key) not in allowed:
            c.error("bad-value", f"{path}.{key}.value",
                    f"{value(key)!r} must be one of {', '.join(allowed)}")

    if j["group"]:
        g = value("group")
        if not isinstance(g, str) or g not in group_ids:
            c.error("undefined-group", f"{path}.group.value",
                    f"group {g!r} is not defined under `groups`")
    if j["order"]:
        o = value("order")
        if not _is_int(o) or o < 0:
            c.error("bad-value", f"{path}.order.value", "order must be an integer >= 0")
        elif j["group"] and isinstance(value("group"), str):
            taken = orders.setdefault(value("group"), {})
            if o in taken:
                c.warn("duplicate-order", f"{path}.order.value",
                       f"order {o} in group {value('group')!r} is also used by "
                       f"{taken[o]!r}; their relative position is ambiguous")
            taken.setdefault(o, entry["name"])
    if j["label"] and (not isinstance(value("label"), str) or not value("label").strip()):
        c.error("bad-value", f"{path}.label.value", "label must be non-empty text")
    if j["description"]:
        d = value("description")
        if not isinstance(d, str):
            c.error("bad-value", f"{path}.description.value", "description must be text")
        elif len(d) > MAX_DESCRIPTION:
            c.warn("long-description", f"{path}.description.value",
                   f"{len(d)} characters; a phone shows about {MAX_DESCRIPTION}")
    if j["phone"] and (not _is_int(value("phone")) or value("phone") < 1):
        c.error("bad-value", f"{path}.phone.value",
                "phone priority must be an integer >= 1 (1 = most important)")

    # `fact` provenance is a claim of mechanical derivation, so re-derive it.
    derivable = {"scale": derive_scale(fact), "label": fact["name"]}
    for key in _JUDGEMENT_KEYS:
        if j[key] and j[key]["provenance"] == "fact":
            if key not in derivable:
                c.error("fact-not-derivable", f"{path}.{key}.provenance",
                        f"{key} cannot be derived mechanically, so it cannot be 'fact'")
            elif value(key) != derivable[key]:
                c.error("fact-not-derivable", f"{path}.{key}.provenance",
                        f"marked fact but the facts derive {derivable[key]!r}, not {value(key)!r}")

    _check_control_against_facts(c, path, fact, value("scale"), value("control"))


def _check_control_against_facts(
    c: _Collector, path: str, fact: dict[str, Any], scale: Any, control: Any
) -> None:
    if scale == "toggle" or control == "toggle":
        if not fact.get("toggled"):
            c.error("control-not-allowed", path,
                    f"{fact['name']!r} is not a toggled port; a toggle would present "
                    "an arbitrary float as on/off")
    if scale == "enum" or control == "selector":
        if not _has_choices(fact):
            c.error("control-not-allowed", path,
                    f"{fact['name']!r} declares no enumeration or scale points; "
                    "a selector would have nothing to select")
    if scale == "log" and not (fact.get("min") is not None and fact["min"] > 0):
        c.error("control-not-allowed", path,
                f"log scale needs a strictly positive minimum; {fact['name']!r} "
                f"starts at {fact.get('min')!r}")
    if scale == "db":
        if fact.get("unit") == "dB":
            c.error("control-not-allowed", path,
                    f"{fact['name']!r} is already in dB; its value is decibels, not a "
                    "linear gain to convert (use linear)")
        if fact.get("min") is None or fact["min"] < 0:
            c.error("control-not-allowed", path,
                    f"db scale shows a linear gain in decibels, which needs a minimum "
                    f"of 0 or more; {fact['name']!r} starts at {fact.get('min')!r}")

    expected = {
        "toggle": ("toggle",), "selector": ("enum",),
        "knob": ("linear", "log", "db"), "fader": ("linear", "log", "db"),
    }
    if isinstance(control, str) and control in expected and scale in SCALES and scale not in expected[control]:
        c.error("control-scale-mismatch", path,
                f"a {control} cannot present a {scale!r} scale "
                f"(expected {' or '.join(expected[control])})")

    if scale == "linear" and fact.get("logarithmic"):
        c.warn("scale-contradicts-hint", path,
               f"{fact['name']!r} declares itself logarithmic but the profile "
               "says linear; fine if deliberate")


def _check_face(
    c: _Collector, face: Any, facts: dict[str, Any], declared: set[str]
) -> None:
    if face is None:
        return
    if not isinstance(face, dict):
        c.error("bad-type", "face", "face must be a mapping")
        return
    _unknown_keys(c, "face", face, _FACE_KEYS)

    width = height = None
    shot = face.get("screenshot")
    if not isinstance(shot, dict):
        c.error("bad-type", "face.screenshot",
                "face needs a screenshot: file, width and height in pixels")
    else:
        _unknown_keys(c, "face.screenshot", shot, _SCREENSHOT_KEYS)
        if not isinstance(shot.get("file"), str) or not shot.get("file"):
            c.error("bad-type", "face.screenshot.file", "screenshot needs a file name")
        for key in ("width", "height"):
            if not _is_int(shot.get(key)) or shot[key] <= 0:
                c.error("bad-value", f"face.screenshot.{key}",
                        f"{key} must be a positive integer number of pixels")
        if _is_int(shot.get("width")) and shot["width"] > 0:
            width = shot["width"]
        if _is_int(shot.get("height")) and shot["height"] > 0:
            height = shot["height"]

    zones = face.get("zones", [])
    if not isinstance(zones, list):
        c.error("bad-type", "face.zones", "zones must be a list")
        return

    by_name = {p["name"]: p for p in facts.get("parameters", [])}
    declared_names = {
        p["name"] for p in facts.get("parameters", []) if p["symbol"] in declared
    }
    for i, zone in enumerate(zones):
        path = f"face.zones[{i}]"
        if not isinstance(zone, dict):
            c.error("bad-type", path, "a zone must be a mapping")
            continue
        _unknown_keys(c, path, zone, _ZONE_KEYS)

        name = zone.get("parameter")
        if not isinstance(name, str) or name not in by_name:
            c.error("zone-unknown-parameter", f"{path}.parameter",
                    f"zone points at {name!r}, which probe does not know "
                    "(use the name Sushi uses)")
        elif name not in declared_names:
            c.error("zone-undeclared-parameter", f"{path}.parameter",
                    f"zone points at {name!r}, which this profile does not declare")

        _check_bbox(c, f"{path}.bbox", zone.get("bbox"), width, height)

        confirmed = zone.get("confirmed")
        if confirmed not in ZONE_CONFIRMATIONS:
            c.error("bad-value", f"{path}.confirmed",
                    f"confirmed must be one of {', '.join(ZONE_CONFIRMATIONS)}")
        elif confirmed == "draft":
            c.warn("draft-zone", path,
                   f"zone for {name!r} is a draft; it has not been confirmed by "
                   "sweeping the parameter and seeing these pixels change")


def _check_bbox(c: _Collector, path: str, bbox: Any, width: int | None, height: int | None) -> None:
    if not (isinstance(bbox, list) and len(bbox) == 4 and all(_is_int(v) for v in bbox)):
        c.error("bad-value", path, "bbox must be four integers [x, y, w, h]")
        return
    x, y, w, h = bbox
    if x < 0 or y < 0 or w <= 0 or h <= 0:
        c.error("zone-out-of-bounds", path,
                f"bbox {bbox} must have x, y >= 0 and w, h > 0")
        return
    if width is None or height is None:
        return  # the screenshot is already reported as bad
    if x + w > width or y + h > height:
        c.error("zone-out-of-bounds", path,
                f"bbox {bbox} extends outside the {width}x{height} screenshot")
