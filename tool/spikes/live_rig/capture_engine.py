"""Can a Python JACK callback feed the recorder at a 256 quantum? (#53)

The biggest open question in Phase 2. The recorder's core (recorder.py) is
built and tested against a synthetic source; what nobody knows is whether the
engine in front of it — a Python process callback copying PipeWire's buffers
into the ring — keeps up in real time. If it cannot, the engine becomes a small
native helper and nothing else changes.

This registers one output port that plays a sample counter, links it into six
input ports (the column count of a guitar_vocal take: two stereo stems and two
mono dry taps), and pushes every block into a real `Recorder` writing real WAVs.
Afterwards it reads the WAVs back and checks the counter is continuous, so a
lost block is found from the audio itself, not only from the recorder's own
accounting.

    PIPEWIRE_QUANTUM=256/48000 pw-jack python capture_engine.py --seconds 600
    PIPEWIRE_QUANTUM=256/48000 pw-jack python capture_engine.py --seconds 300 --load 4

Needs: PipeWire with pipewire-jack, `pip install JACK-Client numpy`, and the
sushi_rig package importable (run from tool/ with the venv active, or
PYTHONPATH=src). Writes a JSON report to --out and prints it.

The callback allocates small Python objects on every block (ints, views, a
0-d array for the counter add). If it misses deadlines, that is the first
thing to name in the report; the recorder's own push is the second.

Written without a machine to run it on (2026-10-31): expect to fix small
things, and say what was fixed in the report.
"""

from __future__ import annotations

import argparse
import json
import multiprocessing
import os
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

try:
    import jack
except (ImportError, OSError) as exc:  # OSError: libjack missing
    sys.exit(f"JACK-Client not usable ({exc}). pip install JACK-Client, and run under pw-jack")

from sushi_rig.recorder import Recorder
from sushi_rig.signals import read_wav

# Column layout of a guitar_vocal take: (file, channels).
STEMS = [("guitar.wav", 2), ("guitar-dry.wav", 1), ("vocal.wav", 2), ("vocal-dry.wav", 1)]
WIDTH = sum(c for _, c in STEMS)
# The counter wraps here; k / 2**20 is exact in float32.
WRAP = 1 << 20


def _burn(stop) -> None:
    while not stop.is_set():
        sum(i * i for i in range(10_000))


def continuity(column: np.ndarray) -> dict:
    """Jumps in a recorded counter: each is audio lost (or repeated)."""
    nonzero = np.flatnonzero(column)
    if not len(nonzero):
        return {"samples": int(len(column)), "jumps": None, "note": "silent: never linked?"}
    first = int(nonzero[0])
    x = np.rint(column[first:].astype(np.float64) * WRAP).astype(np.int64)
    step = np.diff(x) % WRAP
    bad = np.flatnonzero(step != 1)
    # Losses and repeats counted apart, so they cannot cancel. A loss of a
    # whole multiple of WRAP (21.8 s at 48 kHz) is invisible to this; the
    # recorder's own count would show it.
    lost = int(np.sum(step[bad][step[bad] > 1] - 1)) if len(bad) else 0
    repeated = int(np.sum(step[bad] == 0)) if len(bad) else 0
    return {
        "samples": int(len(x)),
        "leading_silence": first,
        "jumps": int(len(bad)),
        "first_jumps_at_file_index": [int(i) + first for i in bad[:10]],
        "lost_by_jumps": lost,
        "repeated_samples": repeated,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--seconds", type=float, default=600.0)
    parser.add_argument("--load", type=int, default=0, help="busy processes to run alongside")
    parser.add_argument("--out", type=Path, default=Path("capture_engine.json"))
    args = parser.parse_args()

    client = jack.Client("sushi-rig-capture-spike", no_start_server=True)
    sr, q = client.samplerate, client.blocksize
    out = client.outports.register("counter")
    ins = [client.inports.register(f"in_{i}") for i in range(WIDTH)]

    work = Path(tempfile.mkdtemp(prefix="capture-spike-"))
    recorder = Recorder([(work / name, channels) for name, channels in STEMS], sr, q)

    # Everything the callback touches is allocated here.
    block = np.zeros((q, WIDTH), dtype=np.float32)
    index = np.arange(q, dtype=np.int64)
    scratch = np.zeros(q, dtype=np.int64)
    durations = np.zeros(int(args.seconds * sr / q) + 4096, dtype=np.int64)
    state = {"n": 0, "position": 0, "xruns": 0, "blocksize_changes": 0, "short_blocks": 0}

    @client.set_process_callback
    def process(frames):
        t0 = time.perf_counter_ns()
        if frames != q:
            # Not the block size this ring was built for: say so, keep the
            # counter stream continuous by advancing it, and push nothing.
            # Pushed, a short block would be the ring's fault; skipped
            # silently, the stale output buffer would read as a jump.
            state["short_blocks"] += 1
            out.get_array()[:] = 0.0
            state["position"] += frames
            return
        np.add(index, state["position"], out=scratch)
        np.remainder(scratch, WRAP, out=scratch)
        np.multiply(scratch, 1.0 / WRAP, out=out.get_array(), casting="unsafe")
        state["position"] += frames
        for column, port in enumerate(ins):
            block[:, column] = port.get_array()
        recorder.push(block)
        n = state["n"]
        if n < len(durations):
            durations[n] = time.perf_counter_ns() - t0
        state["n"] = n + 1

    @client.set_xrun_callback
    def xrun(delay):
        state["xruns"] += 1

    @client.set_blocksize_callback
    def blocksize(size):
        if size != q:
            state["blocksize_changes"] += 1

    stop = multiprocessing.Event()
    burners = [multiprocessing.Process(target=_burn, args=(stop,), daemon=True) for _ in range(args.load)]
    report: dict = {"sample_rate": sr, "quantum": q, "budget_us": round(1e6 * q / sr, 1),
                    "seconds_asked": args.seconds, "load_processes": args.load,
                    "work_dir": str(work), "pid": os.getpid()}
    try:
        for burner in burners:
            burner.start()
        recorder.start()
        with client:
            for port in ins:
                client.connect(out, port)
            started = time.monotonic()
            while time.monotonic() - started < args.seconds:
                time.sleep(10)
                print(f"{time.monotonic() - started:6.0f} s  blocks {state['n']}  "
                      f"xruns {state['xruns']}  offered {recorder.ring.offered}", flush=True)
        recording = recorder.stop()
    finally:
        stop.set()
        for burner in burners:
            burner.join(timeout=5)
            if burner.is_alive():
                burner.kill()

    taken = durations[:min(state["n"], len(durations))] / 1000.0
    report.update({
        "blocks": state["n"],
        "xruns": state["xruns"],
        "short_blocks": state["short_blocks"],
        "blocksize_changes": state["blocksize_changes"],
        "callback_us": {
            "p50": round(float(np.percentile(taken, 50)), 1),
            "p99": round(float(np.percentile(taken, 99)), 1),
            "p99.9": round(float(np.percentile(taken, 99.9)), 1),
            "max": round(float(taken.max()), 1),
        } if len(taken) else None,
        "recorder": recording.summary(),
        "continuity": {},
    })
    for name, _ in STEMS:
        data = read_wav(work / name).samples
        report["continuity"][name] = [continuity(data[:, c]) for c in range(data.shape[1])]
    text = json.dumps(report, indent=2)
    args.out.write_text(text + "\n")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
