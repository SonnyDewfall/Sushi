"""Run and parse `sushi --dump-plugins`.

This is the authoritative source for parameter names and OSC addresses (brief
§2.7): resolve everything from here rather than assuming Sushi uses lv2:name or
the port symbol, or that OSC paths follow a fixed convention.

On Sushi 1.3.0 the JSON payload is followed by a trailing line on stdout,
`Parameter dump completed - exiting.`, so a plain `json.loads` on the full
output raises `json.JSONDecodeError: Extra data`. Parse with `raw_decode`
instead, which stops at the end of the JSON value and ignores what follows.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any


def dump_plugins(config_path: Path, sushi_bin: str = "sushi") -> Any:
    """Run Sushi's own introspection and parse the JSON payload it prints."""
    result = subprocess.run(
        [sushi_bin, "--dump-plugins", "-c", str(config_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        sys.exit(
            f"{sushi_bin} --dump-plugins failed (exit {result.returncode}):\n"
            f"{result.stderr.strip()}"
        )

    stdout = result.stdout
    start = stdout.find("{")
    if start == -1:
        sys.exit("could not find JSON in --dump-plugins output")
    try:
        payload, _ = json.JSONDecoder().raw_decode(stdout, start)
    except json.JSONDecodeError as exc:
        sys.exit(f"could not parse --dump-plugins JSON: {exc}")
    return payload


def collect_parameter_names(dump: Any) -> dict[str, set[str]]:
    """Walk the dump generically and index parameter names by processor name.

    Written defensively rather than against a fixed schema, because the shape
    of the dump has moved between Sushi versions (brief §2.7).
    """
    found: dict[str, set[str]] = {}

    def visit(node: Any) -> None:
        if isinstance(node, dict):
            name = node.get("name")
            params = node.get("parameters")
            if isinstance(params, list) and isinstance(name, str):
                bucket = found.setdefault(name, set())
                for param in params:
                    if isinstance(param, dict) and isinstance(param.get("name"), str):
                        bucket.add(param["name"])
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for item in node:
                visit(item)

    visit(dump)
    return found


def collect_parameter_info(dump: Any) -> dict[str, dict[str, dict[str, Any]]]:
    """Like `collect_parameter_names`, but keeps each parameter's full record
    (id, label, osc_path) rather than just its name.

    `panel.py` needs `osc_path` verbatim — Sushi replaces spaces with
    underscores in it, so an address built from the parameter name directly
    would not match.
    """
    found: dict[str, dict[str, dict[str, Any]]] = {}

    def visit(node: Any) -> None:
        if isinstance(node, dict):
            name = node.get("name")
            params = node.get("parameters")
            if isinstance(params, list) and isinstance(name, str):
                bucket = found.setdefault(name, {})
                for param in params:
                    if isinstance(param, dict) and isinstance(param.get("name"), str):
                        bucket[param["name"]] = param
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for item in node:
                visit(item)

    visit(dump)
    return found
