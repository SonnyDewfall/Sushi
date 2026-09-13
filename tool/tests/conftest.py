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
