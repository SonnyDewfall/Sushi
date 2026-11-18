"""Shared by the tests of #177's rigs (test_string_machine.py, test_synth_lead.py):
a chain compiled as the rig's compiler compiles a mono channel, through the
master, and rendered by the real Sushi 1.3.0 offline; a plucked note and a
chord to feed it; and settings in each plugin's own units, normalised as a
rig file holds them, by the range in the plugin's frozen probe
(`fixtures/probe_*.json`).

No config/rigs/ or config/channels/ file is read (the #133 rule): every chain
here is written out by the test that renders it.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from sushi_rig.paths import checkout_root

HERE = Path(__file__).parent
ROOT = checkout_root()
SUSHI = ROOT / "sushi"
PLUGINS = ROOT / "plugins"
SR = 48000


def probe(name: str) -> dict:
    """A plugin's frozen probe, `fixtures/probe_<name>.json`."""
    return json.loads((HERE / "fixtures" / f"probe_{name}.json").read_text())


def normalised(facts: dict, real: dict[str, float]) -> dict[str, float]:
    """`real`, by each parameter's `lv2:name` in its own units, as Sushi's 0 to
    1: linear over the port's range, which is how Sushi normalises an LV2
    control and how a rig file holds it."""
    ranges = {p["name"]: (p["min"], p["max"]) for p in facts["parameters"]}
    out = {}
    for name, value in real.items():
        lo, hi = ranges[name]
        assert lo <= value <= hi, f"{name} {value} is outside {lo} to {hi}"
        out[name] = round((value - lo) / (hi - lo), 6)
    return out


def compiled(chain: list[dict]) -> dict:
    """One mono channel of `chain` (rig-file chain entries) and the master, as
    `rigfile.heard` gives it for rendering: the compiler's copies to stereo,
    its send and return, and the master's limiter included."""
    from sushi_rig import rigfile

    raw = {"kind": "rig", "schema_version": 1, "name": "synth",
           "channels": [{"name": "board", "input": "guitar-in", "width": "mono", "chain": chain}],
           "master": {"output": "monitor-out", "chain": []}}
    ((_, view),) = rigfile.heard(rigfile.emit_rig(rigfile.parse_rig(raw)))
    return view


def render(np, tmp_path: Path, name: str, config: dict, mono):
    """`mono` on both halves of the input, through `config`, by Sushi's offline
    frontend with plugins/ alone on LV2_PATH; the stereo output, checked
    finite."""
    from sushi_rig import signals
    from sushi_rig.analysis import check_finite
    from sushi_rig.render import sushi_argv
    from sushi_rig.verify import sushi_env

    work = tmp_path / name
    work.mkdir()
    (work / "config.json").write_text(json.dumps(config))
    signals.write_wav(work / "in.wav", np.stack([mono, mono], axis=1), SR, subtype="float32")
    argv = sushi_argv(work / "config.json", work / "in.wav", work / "out.wav", str(SUSHI))
    if "--no-grpc" not in argv:
        argv.append("--no-grpc")  # another Sushi may hold gRPC's port; a render needs none
    result = subprocess.run(argv, cwd=ROOT, env=sushi_env(ROOT), capture_output=True, text=True,
                            errors="replace", timeout=300)
    assert result.returncode == 0 and (work / "out.wav").is_file(), (result.stdout + result.stderr).strip()[-2000:]
    out = signals.read_wav(work / "out.wav").samples
    assert check_finite(out) is None, f"{name}: {check_finite(out)}"
    return out


# --- what is played ---------------------------------------------------------

# A plucked string: five harmonics at 1/h, each dying away faster than the one
# below it, as a string's do. Loud enough to work a compressor and a gate.
def string(np, f0: float, seconds: float = 2.0, level: float = 0.15):
    t = np.arange(int(seconds * SR)) / SR
    return sum((level / h) * np.sin(2 * np.pi * f0 * h * t) * np.exp(-t * (1.5 + 0.6 * h)) for h in range(1, 6))


# An open E major chord's six strings.
E_MAJOR = (82.41, 123.47, 164.81, 207.65, 246.94, 329.63)


def padded(np, x, before: float = 0.5, after: float = 1.5):
    """`x` with silence before and after, so an onset and a tail can be read."""
    return np.concatenate([np.zeros(int(before * SR)), x, np.zeros(int(after * SR))])


def rms_db(np, x) -> float:
    return float(20 * np.log10(np.sqrt(np.mean(np.asarray(x) ** 2)) + 1e-15))


def onset(np, signal, start: int, below_peak_db: float = -20.0) -> int:
    """Samples from `start` until the signal first reaches `below_peak_db` of
    its own peak (#153's onset)."""
    env = np.abs(signal)
    return int(np.argmax(env > env.max() * 10 ** (below_peak_db / 20))) - start
