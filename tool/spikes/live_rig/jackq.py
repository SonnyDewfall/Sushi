"""The quantum PipeWire granted this client, read the only way it can be.

`client.blocksize` is the server's default until the client is active;
PIPEWIRE_QUANTUM reaches a node only once it starts. Read before activation it
said 1024 for a run at 256, and the first capture run recorded 20 seconds of
silence with every counter at zero. So: activate with no process callback,
let the blocksize settle (observed 256 -> 1024 -> 256), read it, deactivate.
"""

from __future__ import annotations

import os
import time

SETTLE_S = 0.5


def granted_quantum(client) -> dict:
    """Activate, settle, read, deactivate. Returns what the report carries."""
    asked = os.environ.get("PIPEWIRE_QUANTUM", "")
    before = client.blocksize
    client.activate()
    time.sleep(SETTLE_S)
    sr, q = client.samplerate, client.blocksize
    client.deactivate()
    return {"sample_rate": sr, "quantum": q, "quantum_asked": asked,
            "quantum_before_activate": before}
