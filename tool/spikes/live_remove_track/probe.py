"""A channel taken out of a running Sushi, many times (#190, PR #238).

Each run starts Sushi (dummy frontend, gRPC) with two channels, each a
looping sine (wav_streamer: `stays` at 440 Hz, `goes` at 660 Hz) into its
send, and the master's return recorded by a wav_writer; then, on a fresh
elkpy connection, takes `goes` out while it plays, one command at a time,
and says for each whether Sushi confirmed it, how long that took, and
whether it was applied. Then it checks Sushi's lists, Sushi still running,
and, by the master's spectrum, that the master went from both sines to the
440 Hz alone, with nothing of the 660 Hz after.

    PYTHONPATH=tool/src python3 tool/spikes/live_remove_track/probe.py \\
        --sushi ./sushi --runs 300 --order send-first --pause 0.5

`--order`: `send-first` (what `live.remove_live_track` does: each processor
from the back, then `delete_track`), `src-first` (the processors front to
back, then `delete_track`), or `track-only` (`delete_track` alone, which
takes the processors off inside Sushi). `--pause`: seconds between
connecting and the first command (elkpy's notice subscription, see the
README). A Sushi that ignores SIGTERM after a run is killed and said
(`STUCK`). One line a run, and a tally at the end.

Not a test: pytest never collects it (testpaths is tests/). See README.md.
"""

from __future__ import annotations

import argparse
import glob
import json
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

from sushi_rig import live, signals
from sushi_rig.agent import connect_quietly

SR = 48000
BLOCK = SR // 10  # 100 ms: 44 cycles of 440 Hz and 66 of 660 Hz, each its own FFT bin
STAYS_HZ, GOES_HZ = 440, 660
HEARD, GONE = 0.1, 1e-4  # amplitudes: a sine at 0.3 or 0.4 is heard; float noise is gone


def config(tmp: Path) -> dict:
    seconds = np.arange(SR) / SR
    tracks, state = [], [{"processor": "master_rec", "properties": {"destination_file": str(tmp / "master_rec")},
                          "parameters": {"recording": 1.0}}]
    for name, hz, level in (("stays", STAYS_HZ, 0.4), ("goes", GOES_HZ, 0.3)):
        wav = tmp / f"{name}.wav"
        signals.write_wav(wav, np.stack([level * np.sin(2 * np.pi * hz * seconds)] * 2, axis=1), SR)
        tracks.append({"name": name, "channels": 2, "inputs": [], "outputs": [], "plugins": [
            {"uid": "sushi.testing.wav_streamer", "name": f"{name}_src", "type": "internal"},
            {"uid": "sushi.testing.send", "name": f"{name}_send", "type": "internal"}]})
        state += [{"processor": f"{name}_send", "properties": {"destination_name": "master_return"}},
                  {"processor": f"{name}_src", "properties": {"file": str(wav)},
                   "parameters": {"loop": 1.0, "playing": 1.0}}]
    tracks.append({"name": "master", "channels": 2, "inputs": [], "outputs": [], "plugins": [
        {"uid": "sushi.testing.return", "name": "master_return", "type": "internal"},
        {"uid": "sushi.testing.wav_writer", "name": "master_rec", "type": "internal"}]})
    return {"host_config": {"samplerate": SR}, "tracks": tracks, "initial_state": state}


def heard(master: np.ndarray) -> str:
    """The master block by block, as runs: "both", then "stays" is a removal
    heard to work; "both" alone, or "both" after "stays", is not."""
    bins = {hz: hz * BLOCK // SR for hz in (STAYS_HZ, GOES_HZ)}
    runs: list[str] = []
    for start in range(0, len(master) - BLOCK + 1, BLOCK):
        spectrum = np.abs(np.fft.rfft(master[start:start + BLOCK, 0])) * 2 / BLOCK
        stays, goes = spectrum[bins[STAYS_HZ]], spectrum[bins[GOES_HZ]]
        kind = None if stays < HEARD else "both" if goes > HEARD else "stays" if goes < GONE else None
        if kind and (not runs or runs[-1] != kind):
            runs.append(kind)
    return ">".join(runs) or "nothing"


def command(c, response) -> str:
    t0 = time.monotonic()
    try:
        live._wait(response, timeout=6, strict=True)
        return f"ok {time.monotonic() - t0:.2f}s"
    except live.LiveError:
        return f"UNCONFIRMED {time.monotonic() - t0:.2f}s"


def run(sushi: str, order: str, pause: float, keep: bool) -> tuple[str, bool]:
    tmp = Path(tempfile.mkdtemp(prefix="remove-track-"))
    (tmp / "c.json").write_text(json.dumps(config(tmp)))
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    address = f"localhost:{port}"
    with (tmp / "sushi.log").open("wb") as log:
        proc = subprocess.Popen([sushi, "-d", "-c", str(tmp / "c.json"), f"--grpc-address={address}"],
                                stdout=log, stderr=subprocess.STDOUT)
    said, bad = [], False
    try:
        deadline = time.monotonic() + 20
        while True:
            try:
                socket.create_connection(("127.0.0.1", port), timeout=0.5).close()
                break
            except OSError:
                if proc.poll() is not None or time.monotonic() > deadline:
                    return f"Sushi did not start ({proc.poll()})", True
                time.sleep(0.1)
        time.sleep(0.5)  # both playing
        c = connect_quietly(address)
        try:
            time.sleep(pause)
            chains, ids = live._walk_chains(c)
            names = [] if order == "track-only" else (
                list(reversed(chains["goes"])) if order == "send-first" else list(chains["goes"]))
            for name in names:
                done = command(c, c.audio_graph.delete_processor_from_track(ids[name], ids["goes"]))
                applied = name not in live._walk_chains(c)[0].get("goes", [])
                said.append(f"{name}:{done}:{'gone' if applied else 'THERE'}")
                bad |= not done.startswith("ok") or not applied
            done = command(c, c.audio_graph.delete_track(ids["goes"]))
            after = live._walk_chains(c)
            applied = "goes" not in after[0] and not {"goes_src", "goes_send"} & set(after[1])
            said.append(f"track:{done}:{'gone' if applied else 'THERE'}")
            bad |= not done.startswith("ok") or not applied
        finally:
            live.close_controller(c)
        time.sleep(0.5)  # the one left
        alive = proc.poll() is None
        said.append("alive" if alive else f"EXITED {proc.returncode}")
        bad |= not alive
    finally:
        proc.terminate()
        try:
            proc.wait(10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(10)
            said.append("STUCK: SIGTERM ignored, killed")
            bad = True
    recorded = glob.glob(str(tmp / "master_rec*.wav"))
    if recorded:
        sound = heard(signals.read_wav(recorded[0]).samples)
        said.append(f"master {sound}")
        bad |= sound != "both>stays"
    if bad and keep:
        said.append(f"kept {tmp}")
    else:
        shutil.rmtree(tmp, ignore_errors=True)
    return " ".join(said), bad


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--sushi", default="./sushi", help="the Sushi binary (the release AppImage's AppRun)")
    parser.add_argument("--runs", type=int, default=20)
    parser.add_argument("--order", choices=("send-first", "src-first", "track-only"), default="send-first")
    parser.add_argument("--pause", type=float, default=0.5, help="seconds from connecting to the first command")
    parser.add_argument("--keep", action="store_true", help="keep the folder (log, recording) of a run that went wrong")
    args = parser.parse_args()
    failed = 0
    for index in range(args.runs):
        said, bad = run(args.sushi, args.order, args.pause, args.keep)
        failed += bad
        print(index, said, flush=True)
    print(f"{args.order}, pause {args.pause:g}s: {args.runs - failed} of {args.runs} clean", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
