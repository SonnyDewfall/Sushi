"""The machine file: the one hand-authored place that names physical ports.

`machines/<hostname>.yaml` maps role names ("guitar-in") to the PipeWire ports
that play that role on this machine. Everything else — which track a role feeds,
the patchbay itself — is derived from it, so changing interface means editing
this file and nothing else (issue #54).

Like `RigSpec`, `validate()` returns every problem rather than raising on the
first. The file is edited by hand on a machine that may be mid-setup, and one fix
cycle per typo is a poor way to find five of them.

What is deliberately *not* checked here: whether a port exists. That needs a live
PipeWire graph and belongs with the effects (patchbay.py); this module is pure.
"""

from __future__ import annotations

import socket
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Bumped when the shape of the file changes incompatibly. A reader that meets a
# version it does not know must not guess at the rest of the file: a v2 `roles`
# could mean something different, and a wrong guess here produces a patchbay
# that is silently wired to the wrong place.
SCHEMA_VERSION = 1

_KNOWN_KEYS = ("schema_version", "interface", "roles")
_ROLE_KEYS = ("ports", "mono_to_stereo")


class MachineError(Exception):
    """The file could not be read as a machine file at all.

    Anything a person can fix by editing it comes back from `validate()` instead.
    This is only for a file with no structure to validate.
    """


def machine_path(root: Path, hostname: str | None = None) -> Path:
    """Where this machine's file lives. The hostname is what keeps two machines'
    files from clashing when both are committed to one repository."""
    return root / "machines" / f"{hostname or socket.gethostname()}.yaml"


@dataclass
class MachineSpec:
    # Everything below is held as read, not coerced. A hand-written file will
    # hold the wrong type sooner or later (`guitar-in: "Audient..."` instead of
    # a list), and coercing it would hide that: iterating a string yields
    # characters, each of which would become a "port".
    schema_version: Any = None
    interface: Any = None
    roles: Any = field(default_factory=dict)
    # Keys the schema does not define. Kept so validate() can name them — a
    # misspelt `role:` would otherwise load as a machine with no roles.
    unknown_keys: list[str] = field(default_factory=list)
    # PyYAML keeps the last of two equal keys and says nothing, so a role written
    # twice would silently lose the first. Found from the parse tree instead.
    duplicate_keys: list[tuple[str, Any, int]] = field(default_factory=list)

    @classmethod
    def load(cls, path: Path) -> "MachineSpec":
        import yaml

        try:
            text = Path(path).read_text()
        except OSError as exc:
            raise MachineError(f"cannot read machine file {path}: {exc}") from exc
        try:
            tree = yaml.compose(text)
            raw = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            raise MachineError(f"{path} is not valid YAML: {exc}") from exc
        if not isinstance(raw, dict):
            raise MachineError(
                f"{path}: expected a mapping with schema_version, interface and "
                f"roles, got {type(raw).__name__}"
            )

        return cls(
            schema_version=raw.get("schema_version"),
            interface=raw.get("interface"),
            roles=raw.get("roles", {}),
            unknown_keys=[str(k) for k in raw if k not in _KNOWN_KEYS],
            duplicate_keys=_duplicate_keys(tree),
        )

    def role_ports(self) -> dict[str, list[str]]:
        """role -> its ports, whichever form each was written in.

        For a file that has passed `validate()`; on one that has not, the result
        is whatever could be read.
        """
        found: dict[str, list[str]] = {}
        for role, value in (self.roles if isinstance(self.roles, dict) else {}).items():
            ports = value.get("ports") if isinstance(value, dict) else value
            found[role] = list(ports) if isinstance(ports, list) else []
        return found

    def mono_roles(self) -> set[str]:
        """Roles that declared `mono_to_stereo`.

        Declared and never inferred from a role having one port: the committed
        patchbay wires one-to-one on the live profile and duplicated on the dead
        one, so the port count says nothing about which was meant.
        """
        if not isinstance(self.roles, dict):
            return set()
        return {
            role for role, value in self.roles.items()
            if isinstance(value, dict) and value.get("mono_to_stereo") is True
        }

    def validate(self) -> list[str]:
        """Return every problem found, rather than raising on the first."""
        # Version first, and alone. If we do not recognise the version we do not
        # know what the rest of the file means, so reporting problems against it
        # would be noise at best and misleading at worst.
        if self.schema_version is None:
            return [
                "schema_version is missing — this file's shape can change, so it "
                f"must say which version it is (expected {SCHEMA_VERSION})"
            ]
        # `type(...) is int`, because YAML's `true` is a bool and bool is an int:
        # `schema_version: true` would otherwise read as version 1.
        if type(self.schema_version) is not int or self.schema_version != SCHEMA_VERSION:
            return [
                f"unknown schema_version {self.schema_version!r}: this tool reads "
                f"version {SCHEMA_VERSION}. Not guessing at the rest of the file"
            ]

        problems: list[str] = []

        for key in self.unknown_keys:
            problems.append(
                f"unknown key {key!r} (expected {', '.join(_KNOWN_KEYS)}) — "
                "a misspelt key is ignored by YAML and would leave the setting unset"
            )

        for where, key, line in self.duplicate_keys:
            what = f"role {key!r}" if where == "roles" else f"key {key!r}"
            problems.append(
                f"duplicate {what} (line {line}) — YAML keeps only the last, "
                "so the earlier definition would be silently dropped"
            )

        if self.interface is not None and (
            not isinstance(self.interface, str) or not self.interface.strip()
        ):
            problems.append(f"interface must be a non-empty string (got {self.interface!r})")

        if not isinstance(self.roles, dict):
            problems.append(
                f"roles must be a mapping of role name to port list (got "
                f"{type(self.roles).__name__})"
            )
            return problems
        if not self.roles:
            # Not an error in YAML terms, but a machine with no roles can never
            # feed anything, and a generated patchbay from it would be empty.
            problems.append("roles is empty — nothing here could feed or take anything")

        for role, value in self.roles.items():
            if not isinstance(role, str) or not role.strip():
                problems.append(f"role name {role!r} must be a non-empty string")
                continue
            ports, mono = value, False
            if isinstance(value, dict):
                # The long form, for a role that needs to say more than which
                # ports it has. Today that is one thing: that a single port is
                # meant to feed both halves of a stereo bus.
                for key in value:
                    if key not in _ROLE_KEYS:
                        problems.append(
                            f"role {role!r}: unknown key {key!r} "
                            f"(expected {', '.join(_ROLE_KEYS)})"
                        )
                ports = value.get("ports")
                mono = value.get("mono_to_stereo", False)
                if not isinstance(mono, bool):
                    problems.append(
                        f"role {role!r}: mono_to_stereo must be true or false "
                        f"(got {mono!r})"
                    )
                    mono = False
                if ports is None:
                    problems.append(f"role {role!r}: has no 'ports' key")
                    continue
            if not isinstance(ports, list):
                problems.append(
                    f"role {role!r}: must be a list of ports, or a mapping with "
                    f"'ports' (got {type(ports).__name__}) — write "
                    "[\"Device:port\"] even for one port"
                )
                continue
            if not ports:
                # Refused rather than treated as "this machine has no such
                # input". An empty role generates no links, which is silence with
                # nothing reporting it (quality principle 8).
                problems.append(f"role {role!r}: has no ports")
                continue
            if mono and len(ports) != 1:
                # It duplicates one port across a pair; with two there is nothing
                # to duplicate and nothing to say which one is meant.
                problems.append(
                    f"role {role!r}: mono_to_stereo needs exactly one port "
                    f"(got {len(ports)})"
                )
            seen: set[str] = set()
            for port in ports:
                problem = _port_problem(port)
                if problem:
                    problems.append(f"role {role!r}: {problem}")
                    continue
                if port in seen:
                    problems.append(f"role {role!r}: port {port!r} is listed twice")
                seen.add(port)
            # The same port in two *different* roles is allowed, and not an
            # oversight: the saved patchbay feeds one capture port to both Sushi
            # and the tuner.

        return problems


def _port_problem(port: Any) -> str | None:
    """Why this is not a port name, or None.

    A port is written `<client>:<port>`, the form qpwgraph saves in its `port`
    attribute (e.g. `Audient iD14:capture_AUX0`). Only the shape is checked;
    whether it exists is a question for the live graph.
    """
    if not isinstance(port, str) or not port:
        return f"port {port!r} must be a non-empty string"
    if port != port.strip():
        # Would never match anything, and the cause is invisible in the file.
        return f"port {port!r} has leading or trailing whitespace"
    node, sep, name = port.partition(":")
    if not sep or not node or not name:
        return f"port {port!r} is not in <client>:<port> form"
    return None


def _duplicate_keys(tree: Any) -> list[tuple[str, Any, int]]:
    """(where, key, line) for every repeated key at the top level or in `roles`.

    Read off the composed tree because `safe_load` has already discarded the
    evidence by the time it returns a dict. Only these two mappings, as the only
    two where a repeat means anything.
    """
    import yaml

    def repeats(mapping: Any, where: str) -> list[tuple[str, Any, int]]:
        found = []
        seen: set[Any] = set()
        for key_node, _value in mapping.value:
            if not isinstance(key_node, yaml.ScalarNode):
                continue
            if key_node.value in seen:
                found.append((where, key_node.value, key_node.start_mark.line + 1))
            seen.add(key_node.value)
        return found

    if not isinstance(tree, yaml.MappingNode):
        return []
    found = repeats(tree, "top")
    for key_node, value_node in tree.value:
        if (
            isinstance(key_node, yaml.ScalarNode)
            and key_node.value == "roles"
            and isinstance(value_node, yaml.MappingNode)
        ):
            found += repeats(value_node, "roles")
    return found
