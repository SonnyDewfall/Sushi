"""Render test signals through mda/Limiter with lilv's `lv2apply` (#137).

    python3 tool/spikes/master_limiter/render.py      # needs numpy, lv2apply, mda-lv2

Writes float32 WAVs to a temporary folder, runs each through the plugin at a
ceiling of -1 dBFS (Thresh 0.975: the hard knee's threshold is
10^(2*Thresh - 2)) with Output at unity (0.5: 10^(2*Output - 1)), and prints
what came out. lv2apply runs the plugin in blocks on one instance per file,
as Sushi would at 64 samples; the algorithm is per sample with its gain kept
between blocks, so the block size does not change the output.
"""

from __future__ import annotations

import struct
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

URI = "http://drobilla.net/plugins/mda/Limiter"
SR = 48000
CEILING = {"thresh": 0.975, "output": 0.5, "knee": 0.0, "release": 0.5}


def write(path: Path, x: np.ndarray) -> None:
    data = np.asarray(x, dtype=np.float32).tobytes()
    channels = x.shape[1]
    header = (b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVEfmt "
              + struct.pack("<IHHIIHH", 16, 3, channels, SR, SR * channels * 4, channels * 4, 32)
              + b"data" + struct.pack("<I", len(data)))
    path.write_bytes(header + data)


def read(path: Path) -> np.ndarray:
    raw = path.read_bytes()
    at, fmt = 12, None
    while at < len(raw):
        chunk, size = raw[at:at + 4], struct.unpack("<I", raw[at + 4:at + 8])[0]
        if chunk == b"fmt ":
            fmt = struct.unpack("<HHIIHH", raw[at + 8:at + 24])
        if chunk == b"data":
            return np.frombuffer(raw[at + 8:at + 8 + size], dtype=np.float32).reshape(-1, fmt[1]).astype(np.float64)
        at += 8 + size + (size & 1)
    raise ValueError(f"no data in {path}")


def run(folder: Path, x: np.ndarray, **controls: float) -> np.ndarray:
    write(folder / "in.wav", x)
    args = ["lv2apply", "-i", str(folder / "in.wav"), "-o", str(folder / "out.wav")]
    for name, value in {**CEILING, **controls}.items():
        args += ["-c", name, str(value)]
    subprocess.run(args + [URI], check=True, capture_output=True)
    return read(folder / "out.wav")


def db(v: float) -> float:
    return 20 * np.log10(max(v, 1e-30))


def main() -> int:
    t = np.arange(SR) / SR
    sweep = np.sin(2 * np.pi * 20 / np.log(1000) * (np.exp(t * np.log(1000)) - 1))  # 20 Hz to 20 kHz in 1 s
    noise = np.random.default_rng(1).standard_normal(SR)
    noise /= np.max(np.abs(noise))
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        print("Below the ceiling: is the output the input, bit for bit?")
        for name, s in (("sweep at -20 dBFS (nulltest's level)", 0.1 * sweep),
                        ("sine at -1.5 dBFS", 10 ** (-1.5 / 20) * np.sin(2 * np.pi * 440 * t))):
            x = np.stack([s, s], 1).astype(np.float32)
            for attack in (0.0, 0.15, 0.5, 1.0):
                y = run(folder, x, attack=attack)
                print(f"  {name}, attack {attack}: identical={np.array_equal(y.astype(np.float32), x)}")
        print("\nAbove it (both channels the same): peak / peak after the first 50 ms, dBFS;"
              " '!' where the output is louder than the input")
        for attack in (0.0, 0.15, 0.3, 0.5, 1.0):
            row = []
            for gain_db in (3, 6, 12, 20, 26, 34, 40):
                a = 10 ** (gain_db / 20)
                peak = late = -999.0
                louder = False
                for s in (a * np.sin(2 * np.pi * 440 * t), a * sweep, a * noise):
                    y = run(folder, np.stack([s, s], 1), attack=attack)
                    peak, late = max(peak, db(np.max(np.abs(y)))), max(late, db(np.max(np.abs(y[2400:]))))
                    louder |= bool(np.max(np.abs(y)) > a)
                row.append(f"+{gain_db}: {peak:5.1f}/{late:5.1f}{'!' if louder else ' '}")
            print(f"  attack {attack:4}: " + "  ".join(row))
        s = 2.0 * np.sin(2 * np.pi * 440 * t)
        y = run(folder, np.stack([s, -s], 1), attack=0.15)
        print(f"\nOpposite channels (L = -R) at +6 dBFS: out at {db(np.max(np.abs(y))):.2f} dBFS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
