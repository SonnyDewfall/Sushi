"""A v2 rig in a live Sushi: its stems, its master and its sends, measured.

Everything here was measured offline in CI, where Sushi runs one thread and
one stereo bus. This checks the same things in the live JACK frontend under
PipeWire, which is what `up` runs, using a v2 rig with empty chains (only
Sushi's internal sends and returns, so no LV2 plugins are needed):

1. **The rig loads** with the compiler's layout: inputs 0 and 1, the master on
   outputs 0/1, the guitar's stem on 2/3, the vocal's on 4/5.
2. **The send costs nothing live.** The master is exactly as late as the stem.
   Offline it was 0 samples; live it depends on Sushi keeping every track on
   one thread (no --multicore-processing), which is the hypothesis.
3. **Port to port is one quantum.** Measured against this client's own
   output fed back into its own input, a reference path that itself crosses
   a PipeWire link; October's 257 was measured against pw-record's capture.
   The two reference paths differ, so the numbers may differ by one.
4. **Channels are separate.** Noise in the guitar's input does not appear in
   the vocal's stem.
5. **PipeWire sums two links into one input** (probes.py's first hypothesis):
   two noise sources linked into the vocal's input both appear in its stem.

Two independent noise sources, A and B. A goes to Sushi input 0 (guitar) and
input 1 (vocal); B goes to input 1 too. Expected, as gains read off the
impulse response at one lag:

    guitar stem   A 1.0  B 0.0
    vocal stem    A 1.0  B 1.0        (5: summed)
    master        A 2.0  B 1.0        (both channels, through their sends)

    SUSHI=../../../sushi PIPEWIRE_QUANTUM=256/48000 pw-jack python live_paths.py
    ... --hold     # keep Sushi up afterwards, for the panel check, until Enter

Starts Sushi itself and stops it by pid, always. Needs: PipeWire with
pipewire-jack, `pip install JACK-Client numpy`, the Sushi 1.3.0 AppImage, and
sushi_rig importable. Writes a JSON report to --out and prints it.

Written without a machine to run it on (2026-10-31): expect to fix small
things, and say what was fixed in the report.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

try:
    import jack
except (ImportError, OSError) as exc:
    sys.exit(f"JACK-Client not usable ({exc}). pip install JACK-Client, and run under pw-jack")

from sushi_rig.analysis import find_delays
from sushi_rig.rigfile import emit_rig, parse_rig

sys.path.insert(0, str(Path(__file__).resolve().parent))
from jackq import granted_quantum  # noqa: E402

RIG = {
    "kind": "rig", "schema_version": 1, "name": "live_paths",
    "channels": [
        {"name": "guitar", "input": "guitar-in", "width": "mono", "chain": []},
        {"name": "vocal", "input": "vocal-in", "width": "mono", "chain": []},
    ],
    "master": {"output": "monitor-out", "chain": []},
}

CAPTURES = {
    "master_l": "sushi:audio_output_0", "master_r": "sushi:audio_output_1",
    "guitar_l": "sushi:audio_output_2", "guitar_r": "sushi:audio_output_3",
    "vocal_l": "sushi:audio_output_4", "vocal_r": "sushi:audio_output_5",
}
EXPECTED = {
    "guitar_l": {"A": 1.0, "B": 0.0}, "guitar_r": {"A": 1.0, "B": 0.0},
    "vocal_l": {"A": 1.0, "B": 1.0}, "vocal_r": {"A": 1.0, "B": 1.0},
    "master_l": {"A": 2.0, "B": 1.0}, "master_r": {"A": 2.0, "B": 1.0},
}
GAIN_TOLERANCE = 0.01


def wait_for_ports(client, proc, pattern: str, count: int, timeout: float = 15.0) -> list[str]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"Sushi exited with status {proc.returncode} before its ports appeared")
        names = [p.name for p in client.get_ports(pattern, is_audio=True)]
        if len(names) >= count:
            return names
        time.sleep(0.2)
    raise RuntimeError(f"Sushi's ports never appeared (wanted {count} matching {pattern!r})")


def gain_at(reference, test, lag: int) -> float:
    """The impulse-response gain of `reference` in `test` at exactly `lag`."""
    ref = reference.astype(np.float64)
    seg = test[lag:lag + len(ref)].astype(np.float64)
    ref = ref[:len(seg)]
    return float(np.dot(seg, ref) / np.dot(ref, ref))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--seconds", type=float, default=6.0)
    parser.add_argument("--hold", action="store_true", help="keep Sushi running until Enter")
    parser.add_argument("--out", type=Path, default=Path("live_paths.json"))
    args = parser.parse_args()

    sushi = os.environ.get("SUSHI", "sushi")
    work = Path(tempfile.mkdtemp(prefix="live-paths-"))
    config = work / "live_paths.json"
    config.write_text(json.dumps(emit_rig(parse_rig(RIG)), indent=2))

    print(f"config: {config}", flush=True)
    log = open(work / "sushi.log", "wb")
    proc = subprocess.Popen([sushi, "-j", "-c", str(config)], stdout=log, stderr=subprocess.STDOUT)
    report: dict = {"sushi_pid": proc.pid, "config": str(config), "log": str(work / "sushi.log")}
    try:
        client = jack.Client("sushi-rig-live-spike", no_start_server=True)
        granted = granted_quantum(client)
        sr, q = granted["sample_rate"], granted["quantum"]
        report.update(granted)
        sushi_in = wait_for_ports(client, proc, "sushi:audio_input_", 2)
        sushi_out = wait_for_ports(client, proc, "sushi:audio_output_", 6)
        report["sushi_ports"] = {"inputs": len(sushi_in), "outputs": len(sushi_out)}

        frames = int(args.seconds * sr)
        rng = np.random.default_rng(31)
        noise = {k: (0.1 * rng.standard_normal(frames)).astype(np.float32) for k in "AB"}
        captured = {k: np.zeros(frames, dtype=np.float32) for k in list(CAPTURES) + ["inject_A", "inject_B"]}
        outs = {k: client.outports.register(f"probe_{k}") for k in "AB"}
        ins = {k: client.inports.register(k) for k in captured}
        pos = {"at": 0}

        @client.set_process_callback
        def process(n):
            at = pos["at"]
            take = max(0, min(n, frames - at))
            for k, port in outs.items():
                buf = port.get_array()
                buf[:] = 0
                buf[:take] = noise[k][at:at + take]
            for k, port in ins.items():
                captured[k][at:at + take] = port.get_array()[:take]
            pos["at"] = at + take

        with client:
            # Inputs to Sushi: A into guitar and vocal, B into vocal too.
            client.connect(outs["A"], "sushi:audio_input_0")
            client.connect(outs["A"], "sushi:audio_input_1")
            client.connect(outs["B"], "sushi:audio_input_1")
            # What went in, captured beside what came out, in one client.
            client.connect(outs["A"], ins["inject_A"])
            client.connect(outs["B"], ins["inject_B"])
            for k, port in CAPTURES.items():
                client.connect(port, ins[k])
            report["links"] = subprocess.run(["pw-link", "-l"], capture_output=True, text=True).stdout
            while pos["at"] < frames:
                time.sleep(0.1)

        skip = sr // 2  # settle
        results = {}
        for k in CAPTURES:
            signal = captured[k]
            peaks = find_delays(captured["inject_A"][skip:skip + sr], signal[skip:], max_peaks=1)
            lag = peaks[0].lag_samples if peaks else None
            row = {"lag_samples": lag}
            if lag is not None:
                # Over the whole settled capture, not one second of it: the
                # cross-term of two independent noises projects to about
                # 1/sqrt(N), 0.0046 at one second and 0.002 at five, against a
                # tolerance of 0.01.
                for source in "AB":
                    row[source] = round(gain_at(captured[f"inject_{source}"][skip:], signal[skip:], lag), 4)
                row["as_expected"] = all(abs(row[s] - EXPECTED[k][s]) <= GAIN_TOLERANCE for s in "AB")
            results[k] = row
        report["paths"] = results
        lags = {k: r["lag_samples"] for k, r in results.items()}
        found = all(v is not None for v in lags.values())
        report["checks"] = {
            # Three Nones would compare equal: a silent Sushi must not pass.
            "send_costs_nothing": found and lags["master_l"] == lags["guitar_l"] == lags["vocal_l"],
            "port_to_port_samples": lags["guitar_l"],
            "separate_channels": results["guitar_l"].get("B") is not None and abs(results["guitar_l"]["B"]) <= GAIN_TOLERANCE,
            "pipewire_sums_two_links": results["vocal_l"].get("as_expected", False),
            "all_gains_as_expected": all(r.get("as_expected", False) for r in results.values()),
        }
        if args.hold:
            # EOF (stdin closed, or driven non-interactively) ends the hold
            # the same way Enter does; a traceback here lost the whole report.
            try:
                input("Sushi is up with the live_paths rig. Press Enter to stop it. ")
            except EOFError:
                pass
    except Exception as exc:  # noqa: BLE001 — the report says what went wrong
        report["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        log.close()
        report["sushi_exit"] = proc.returncode
        # Written whatever happened, so a Sushi that never started still
        # leaves a report naming its exit status and log.
        text = json.dumps(report, indent=2)
        args.out.write_text(text + "\n")
        print(text)
    checks = report.get("checks") or {}
    return 0 if checks and all(v for k, v in checks.items() if k != "port_to_port_samples") else 1


if __name__ == "__main__":
    raise SystemExit(main())
