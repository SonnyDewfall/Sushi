import json
from pathlib import Path

import pytest

EXAMPLES = Path(__file__).parent.parent / "examples"


@pytest.fixture
def real_dump() -> dict:
    """The real `--dump-plugins` output captured from Sushi 1.3.0.

    Loaded with raw_decode because the file — like Sushi's actual stdout —
    carries a trailing "Parameter dump completed - exiting." line after the
    JSON value.
    """
    raw = (EXAMPLES / "dump.example.json").read_text()
    payload, _ = json.JSONDecoder().raw_decode(raw, raw.index("{"))
    return payload


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    """A private runtime dir, so the real state file is neither read nor hurt.

    `XDG_RUNTIME_DIR` is pointed at a temporary directory; the fixture's value
    is the rig's own directory inside it, where the state file and logs go.
    """
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "run"))
    return tmp_path / "run" / "sushi-rig"
