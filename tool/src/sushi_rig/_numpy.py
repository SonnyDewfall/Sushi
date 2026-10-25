"""The one place that imports numpy.

numpy is an optional extra (`pip install -e ".[audio]"`), needed only for the
offline-audio modules (`signals`, `analysis`). Importing it lazily, with one
clear message, keeps the rest of the package — which supervises processes and
edits YAML and has no use for it — importable on a machine without it. Same
shape as how `live.py` treats elkpy.

This is its own module so that the two audio modules share one message rather
than each growing a copy that drifts.
"""

from __future__ import annotations

import sys


def require_numpy():
    """Return the numpy module, or exit with an instruction to install it."""
    try:
        import numpy
    except ImportError:
        sys.exit(
            "numpy not found. It is only needed for offline audio analysis: "
            'pip install -e ".[audio]" (or pip install numpy)'
        )
    return numpy
