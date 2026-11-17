"""Render a chain through the real Sushi 1.3.0, offline (#166).

The #153 bench host (`../plugin_research/host.c`) loads one plugin the way
Sushi does. A chain that passes MIDI from one plugin to the next, uses Sushi's
own internal plugins, or sends and returns inside a track cannot be checked
there, so this runs the Sushi AppImage itself: `sushi -o -i in.wav -O out.wav
-c config.json --timing-statistics`, which loads the config, pushes the file
through the graph in 64-frame chunks and writes `timings.txt`, Sushi's own
per-processor CPU figures (average, minimum and maximum, as a share of one
chunk's time).

The track is built as the rig's compiler builds a channel (tool/src/sushi_rig/
rigfile.py and stereo.py): a stereo track with the guitar on both halves, and
`sushi.testing.mono_summing` after every LV2 plugin with one audio output.

    SUSHI_APPIMAGE   the AppRun of the unpacked AppImage (`sushi.AppImage
                     --appimage-extract` makes squashfs-root/, the default)

Settings are given in each parameter's own units and normalised here, as the
rig's files hold them: LV2 linearly over the port's range (lilv), internal
plugins by the preprocessor each registers in Sushi's source (linear, dB, or
the cubic warp).

Run with python3.12 (Ubuntu's python3-lilv).
"""

from __future__ import annotations

import json
import os
import shutil
import struct
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

SR = 48000
SUSHI = Path(os.environ.get("SUSHI_APPIMAGE", "squashfs-root/AppRun"))
COPY_UID = "sushi.testing.mono_summing"

# Internal plugins' parameters used here: (min, max, warp), from Sushi 1.3.0's
# src/plugins/**/*_plugin.cpp register_*_parameter calls. "cubic" is
# CubicWarpPreProcessor; dB parameters are linear in dB, like any other.
INTERNAL = {
    "sushi.brickworks.chorus": {"rate": (0.01, 2.0, "cubic"), "amount": (0.0, 1.0, "lin")},
    "sushi.brickworks.vibrato": {"rate": (2.0, 10.0, "cubic"), "amount": (0.0, 1.0, "lin")},
    "sushi.brickworks.phaser": {"rate": (0.5, 5.0, "cubic"), "center": (100.0, 10000.0, "cubic"),
                                "amount": (0.0, 4.0, "lin")},
    "sushi.brickworks.ring_mod": {"frequency": (20.0, 20000.0, "cubic"), "amount": (-1.0, 1.0, "lin")},
    "sushi.brickworks.wah": {"wah": (0.0, 1.0, "lin")},
    "sushi.brickworks.multi_filter": {"frequency": (20.0, 20000.0, "cubic"), "Q": (0.5, 10.0, "lin"),
                                      "input_coeff": (-1.0, 1.0, "lin"), "lowpass_coeff": (-1.0, 1.0, "lin"),
                                      "bandpass_coeff": (-1.0, 1.0, "lin"), "highpass_coeff": (-1.0, 1.0, "lin")},
    "sushi.brickworks.simple_synth": {"volume": (-60.0, 12.0, "lin"), "portamento": (0.0, 1.0, "lin"),
                                      "pulse_width": (0.0, 1.0, "lin"), "filter_cutoff": (20.0, 20000.0, "cubic"),
                                      "filter_Q": (0.5, 10.0, "lin"), "attack": (0.002, 1.0, "lin"),
                                      "decay": (0.002, 1.0, "lin"), "sustain": (0.0, 1.0, "lin"),
                                      "release": (0.002, 1.0, "lin")},
    "sushi.brickworks.noise_gate": {"threshold": (-60.0, 0.0, "lin"), "ratio": (0.0, 1.0, "lin"),
                                    "attack": (0.0, 1.0, "lin"), "release": (0.0, 1.0, "lin")},
    "sushi.brickworks.fuzz": {"fuzz": (0.0, 1.0, "lin"), "gain": (0.0, 1.0, "lin")},
    "sushi.brickworks.tremolo": {"rate": (1.0, 20.0, "cubic"), "amount": (0.0, 1.0, "lin")},
    "sushi.testing.freeverb": {"freeze": (0.0, 1.0, "lin"), "dry": (0.0, 1.0, "lin"), "wet": (0.0, 1.0, "lin"),
                               "room_size": (0.0, 1.0, "lin"), "width": (0.0, 1.0, "lin"),
                               "damp": (0.0, 1.0, "lin")},
    "sushi.testing.send": {"gain": (-120.0, 24.0, "lin")},
    "sushi.testing.gain": {"gain": (-120.0, 24.0, "lin")},
}

_lilv_world = None
_lilv_path = None


def _lv2_ports(uri: str, lv2_path: str) -> dict:
    """{symbol: (min, max, name)} for a plugin's control inputs, and its audio counts."""
    global _lilv_world, _lilv_path
    import lilv
    if _lilv_world is None or _lilv_path != lv2_path:
        os.environ["LV2_PATH"] = lv2_path
        _lilv_world = lilv.World()
        _lilv_world.load_all()
        _lilv_path = lv2_path
    w = _lilv_world
    plugin = w.get_all_plugins().get_by_uri(w.new_uri(uri))
    if plugin is None:
        raise KeyError(f"{uri} is not on {lv2_path}")
    lv2 = "http://lv2plug.in/ns/lv2core#"
    audio, control, inp = w.new_uri(lv2 + "AudioPort"), w.new_uri(lv2 + "ControlPort"), w.new_uri(lv2 + "InputPort")
    out = {"controls": {}, "audio_in": 0, "audio_out": 0}
    for i in range(plugin.get_num_ports()):
        port = plugin.get_port_by_index(i)
        if port.is_a(audio):
            out["audio_in" if port.is_a(inp) else "audio_out"] += 1
        elif port.is_a(control) and port.is_a(inp):
            _, lo, hi = port.get_range()
            # Sushi names an LV2 parameter by the port's lv2:name, not its symbol
            out["controls"][str(port.get_symbol())] = (float(str(lo)), float(str(hi)), str(port.get_name()))
    return out


@dataclass
class Plug:
    name: str
    uri: str                  # an LV2 URI, or an internal uid ("sushi.…")
    params: dict = field(default_factory=dict)       # by port symbol (LV2) or name, in its own units
    properties: dict = field(default_factory=dict)

    @property
    def internal(self) -> bool:
        return self.uri.startswith("sushi.")


def _norm(value: float, lo: float, hi: float, warp: str = "lin") -> float:
    n = (value - lo) / (hi - lo) if hi != lo else 0.0
    n = min(1.0, max(0.0, n))
    return n ** (1 / 3) if warp == "cubic" else n


def build(chain: list[Plug], lv2_path: str, events: list[dict] | None = None,
          extra_tracks: list[dict] | None = None) -> dict:
    plugins, state = [], [{"processor": "guitar", "parameters": {"gain": _norm(0.0, -120, 24), "pan": 0.5}}]
    for p in chain:
        if p.internal:
            plugins.append({"name": p.name, "type": "internal", "uid": p.uri})
            ranges = INTERNAL.get(p.uri, {})
            values = {}
            for k, v in p.params.items():
                lo, hi, warp = ranges[k]
                values[k] = round(_norm(v, lo, hi, warp), 6)
            entry = {"processor": p.name}
            if values:
                entry["parameters"] = values
            if p.properties:
                entry["properties"] = p.properties
            if len(entry) > 1:
                state.append(entry)
            continue
        info = _lv2_ports(p.uri, lv2_path)
        plugins.append({"name": p.name, "type": "lv2", "uri": p.uri})
        values = {}
        for k, v in p.params.items():
            lo, hi, label = info["controls"][k]
            values[label] = round(_norm(v, lo, hi), 6)
        if values:
            state.append({"processor": p.name, "parameters": values})
        if info["audio_out"] == 1:
            plugins.append({"name": p.name + "_to_stereo", "type": "internal", "uid": COPY_UID})
    track = {"name": "guitar", "channels": 2, "inputs": [{"engine_bus": 0, "track_bus": 0}],
             "outputs": [{"engine_bus": 0, "track_bus": 0}], "plugins": plugins}
    cfg = {"host_config": {"samplerate": SR}, "tracks": [track] + (extra_tracks or []),
           "initial_state": state}
    if events:
        cfg["events"] = events
    return cfg


def write_wav(path: Path, x: np.ndarray) -> None:
    x = np.asarray(x, dtype=np.float32)
    if x.ndim == 1:
        x = np.stack([x, x], 1)
    data = x.tobytes()
    ch = x.shape[1]
    header = (b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVEfmt "
              + struct.pack("<IHHIIHH", 16, 3, ch, SR, SR * ch * 4, ch * 4, 32)
              + b"data" + struct.pack("<I", len(data)))
    path.write_bytes(header + data)


def read_wav(path: Path) -> np.ndarray:
    raw = path.read_bytes()
    pos, fmt, data = 12, None, None
    while pos + 8 <= len(raw):
        cid, size = raw[pos:pos + 4], struct.unpack("<I", raw[pos + 4:pos + 8])[0]
        body = raw[pos + 8:pos + 8 + size]
        if cid == b"fmt ":
            fmt = struct.unpack("<HHIIHH", body[:16])
        elif cid == b"data":
            data = body
        pos += 8 + size + (size & 1)
    tag, ch, _, _, _, bits = fmt
    if bits == 32 and tag in (3, 0xFFFE):
        y = np.frombuffer(data, dtype="<f4")
    elif bits == 16:
        y = np.frombuffer(data, dtype="<i2") / 32768.0
    elif bits == 24:
        b = np.frombuffer(data, dtype=np.uint8).reshape(-1, 3)
        y = ((b[:, 0].astype(np.int32) | (b[:, 1].astype(np.int32) << 8) | (b[:, 2].astype(np.int32) << 16)) << 8 >> 8) / 8388608.0
    else:
        y = np.frombuffer(data, dtype="<i4") / 2147483648.0
    return y.reshape(-1, ch).astype(np.float64)


@dataclass
class Result:
    out: np.ndarray | None
    timings: dict            # processor -> (average %, minimum %, maximum %) of a 64-frame chunk
    log: str
    config: dict
    wall: float = 0.0

    @property
    def ok(self) -> bool:
        return self.out is not None


RETRIES: list[str] = []   # every render that had to be run again, and why


def run(cfg: dict, x: np.ndarray, lv2_path: str, keep: Path | None = None, timeout: float = 600) -> Result:
    """Render x (mono: sent to both halves) through cfg in Sushi offline; a
    render that fails is run once more, and the first failure kept in RETRIES."""
    r = _run(cfg, x, lv2_path, keep, timeout)
    if not r.ok:
        names = ", ".join(p["name"] for t in cfg["tracks"] for p in t["plugins"])
        RETRIES.append(f"{names}: " + (r.log.strip().splitlines()[-1] if r.log.strip() else "no log"))
        r = _run(cfg, x, lv2_path, keep, timeout)
    return r


def _run(cfg: dict, x: np.ndarray, lv2_path: str, keep: Path | None, timeout: float) -> Result:
    import time
    work = Path(tempfile.mkdtemp(prefix="sushi-synth-"))
    try:
        write_wav(work / "in.wav", x)
        (work / "config.json").write_text(json.dumps(cfg, indent=1))
        env = {**os.environ, "LV2_PATH": lv2_path}
        t0 = time.monotonic()
        done = subprocess.run([str(SUSHI), "-o", "-i", "in.wav", "-O", "out.wav", "-c", "config.json",
                               "--timing-statistics", "-L", str(work / "sushi.log"), "-l", "info"],
                              cwd=work, env=env, capture_output=True, text=True, errors="replace", timeout=timeout)
        wall = time.monotonic() - t0
        log = done.stdout + done.stderr
        if (work / "sushi.log").exists():
            log += (work / "sushi.log").read_text(errors="replace")
        timings = {}
        if (work / "timings.txt").exists():
            for line in (work / "timings.txt").read_text().splitlines():
                parts = line.split()
                if len(parts) >= 4:
                    try:
                        nums = [float(v) for v in parts[-3:]]
                    except ValueError:
                        continue
                    timings[" ".join(parts[:-3])] = tuple(nums)
        out = read_wav(work / "out.wav") if (work / "out.wav").exists() and done.returncode == 0 else None
        if out is not None and len(out) < len(x):
            log += f"\nshort output: {len(out)} of {len(x)} frames, exit {done.returncode}"
            out = None
        if done.returncode != 0:
            log += f"\nexit {done.returncode}"
        if keep is not None:
            keep.mkdir(parents=True, exist_ok=True)
            for f in ("config.json", "timings.txt", "sushi.log"):
                if (work / f).exists():
                    shutil.copy(work / f, keep / f)
        return Result(out, timings, log, cfg, wall)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def lv2_farm(dest: Path, system: Path = Path("/usr/lib/lv2"), extra: list[Path] = (),
             replace: dict[str, Path] | None = None) -> str:
    """One folder of links: every bundle in `system`, with those named in
    `replace` swapped for another build (Rubber Band 4.0's rubberband.lv2 for
    Ubuntu's 3.3.0, as #164 would do on the rig), plus every bundle in each
    `extra` folder. Returned as an LV2_PATH."""
    replace = replace or {}
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    for bundle in sorted(system.iterdir()):
        if bundle.name not in replace:
            (dest / bundle.name).symlink_to(bundle)
    for name, path in replace.items():
        (dest / name).symlink_to(path)
    for folder in extra:
        for bundle in sorted(Path(folder).iterdir()):
            if bundle.suffix == ".lv2" and not (dest / bundle.name).exists():
                (dest / bundle.name).symlink_to(bundle)
    return str(dest)
