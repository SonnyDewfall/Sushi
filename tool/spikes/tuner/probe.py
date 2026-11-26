"""A tuner inside Sushi: can its reading be read, how fast, and what it costs (#58).

Starts a JACK server on its dummy driver (a real-time clock, no hardware),
Sushi on JACK with one channel laid out as the rig lays out a mono guitar
channel (a 2-channel track, both halves from engine channel 0), the tuner
first in its chain and the rig's compressor after it, for scale. This
process is a JACK client of its own: it plays a sine into Sushi's input,
phase-continuous across steps of pitch, and listens to Sushi's output. Over
elkpy it reads the tuner's output control ports, which Sushi registers as
parameters (`LV2_Wrapper::_register_parameters`, Direction::OUTPUT).

    python3 tool/spikes/tuner/probe.py --sushi ./sushi --plugin gxtuner --json gxtuner.json

Phases, each a key of the printed JSON:

- `ports`: the parameters Sushi lists for the tuner, output ones too.
- `reading`: steady sines (110 Hz, +5 and -20 cents, the low E, E4) and
  silence, each read back; cents are worked out here from the frequency
  the plugin reports, against A440.
- `latency`: `--steps` steps between 110 Hz and 110 Hz x 2^(5/1200), read
  at once by a poll on one held connection (`--latency-poll-hz`) and by
  Sushi's parameter notifications; for each, the time from the period that took the
  step to the first reading within `--tolerance` cents of the new pitch
  that stays within it.
- `rates`: the poll at set rates, on one held connection and on a fresh
  connection a read (the agent's `Live` connects per request): the rate
  reached, a read's time, this process's and Sushi's CPU, and how steady
  the cents are.
- `dsp`: Sushi's own timings (`--timing-statistics`) of the tuner, the
  compressor, the track and the engine, the tuner running and then
  bypassed; Sushi's CPU each way; what a bypassed tuner reads; how long an
  un-bypassed one takes to read right; and the level in and out.

`--plugin tuna` (x42's tuner) is there to show what happens to it: Sushi
aborts as soon as it hears a note (README.md). The report then says so.
`--plugin none` leaves the tuner out and reports Sushi's timings and CPU
with the compressor alone, three times: the baseline for the cost.

Needs jackd (jackd2), elkpy, JACK-Client, numpy, and the plugins on
`--lv2-path`. CI's rig image has all but jackd; where there is no
/dev/snd/seq, Sushi on JACK needs `--preload alsa_seq_standin.so`. README.md
says how it was run. Not a test: pytest never collects it (testpaths is
tests/).
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import shutil
import socket
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import numpy as np

SR = 48000
CHUNK = 64  # Sushi's AUDIO_CHUNK_SIZE in the release build (`./sushi --version`)
NOTES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
A2 = 110.0
A2_UP5 = A2 * 2 ** (5 / 1200)
E2 = 440.0 * 2 ** (-29 / 12)  # 82.407 Hz, the low E string
COMPRESSOR = "http://guitarix.sourceforge.net/plugins/gx_compressor#_compressor"
# Each candidate: its URI and the name Sushi gives (the port's lv2:name) to
# the output that carries the detected frequency in Hz.
PLUGINS = {
    "none": {"uri": None, "freq": None},  # the compressor alone: a baseline for the cost
    "gxtuner": {"uri": "http://guitarix.sourceforge.net/plugins/gxtuner#tuner", "freq": "FREQ"},
    "tuna": {"uri": "http://gareus.org/oss/lv2/tuna#one", "freq": "Detected Frequency"},
}


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def proc_cpu(pid: int) -> float:
    """utime + stime of a process and its threads, in seconds."""
    fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
    return (int(fields[11]) + int(fields[12])) / os.sysconf("SC_CLK_TCK")


def thread_cpu(pid: int) -> dict[str, float]:
    """utime + stime of each of a process's threads, by "name/tid", in seconds."""
    out = {}
    for task in Path(f"/proc/{pid}/task").iterdir():
        try:
            stat = (task / "stat").read_text()
        except OSError:
            continue
        name = stat[stat.index("(") + 1:stat.rindex(")")]
        fields = stat.rsplit(")", 1)[1].split()
        out[f"{name}/{task.name}"] = (int(fields[11]) + int(fields[12])) / os.sysconf("SC_CLK_TCK")
    return out


def note_of(hz: float, a4: float = 440.0) -> tuple[str, int, float]:
    """Nearest equal-tempered note, its octave, and the cents off it."""
    semis = 12 * math.log2(hz / a4) + 57  # semitones above C0
    nearest = round(semis)
    return NOTES[nearest % 12], nearest // 12, 100 * (semis - nearest)


def cents_of(hz: float) -> float:
    return note_of(hz)[2] if hz > 0 else float("nan")


class Sine:
    """A JACK client: a sine into Sushi's input, Sushi's output read back."""

    def __init__(self):
        import jack

        self.client = jack.Client("tuner-probe", no_start_server=True)
        self.out = self.client.outports.register("sine")
        self.back = self.client.inports.register("back")
        self.hz, self.level, self.phase = A2, 0.25, 0.0  # 0.25: -12 dBFS peak
        self.switched: list[tuple[float, float]] = []  # (monotonic, hz) when a period took a step
        self._want: float | None = None
        self._acc = [0.0, 0.0, 0]
        self.xruns = 0
        self.client.set_process_callback(self._process)
        self.client.set_xrun_callback(self._xrun)

    def _xrun(self, delay: float) -> None:
        self.xruns += 1

    def _process(self, frames: int) -> None:
        if self._want is not None:
            self.hz, self._want = self._want, None
            self.switched.append((time.monotonic(), self.hz))
        step = 2 * math.pi * self.hz / SR
        buf = self.level * np.sin(self.phase + step * np.arange(frames, dtype=np.float64))
        self.phase = (self.phase + step * frames) % (2 * math.pi)
        self.out.get_array()[:] = buf
        back = self.back.get_array()
        self._acc[0] += float(np.dot(buf, buf))
        self._acc[1] += float(np.dot(back, back))
        self._acc[2] += frames

    def set(self, hz: float) -> float:
        """Ask for a new pitch; the next period takes it. Returns when asked."""
        t = time.monotonic()
        self._want = hz
        return t

    def levels_dbfs(self) -> list[float]:
        """RMS in and out since the last call, in dBFS."""
        s_in, s_out, n = self._acc
        self._acc = [0.0, 0.0, 0]
        return [round(10 * math.log10(max(s / max(n, 1), 1e-24)), 2) for s in (s_in, s_out)]


class Rig:
    """jackd (dummy driver), Sushi on it and this process's JACK client, each
    started here and stopped by `close`, by its own handle."""

    def __init__(self, sushi: str, plugin: str, lv2_path: str, period: int, tmp: Path,
                 preload: str | None):
        self.sushi_path, self.plugin, self.lv2_path = sushi, plugin, lv2_path
        self.period, self.tmp, self.preload, self.procs = period, tmp, preload, []
        self.sushi = self.sine = None

    def start(self) -> None:
        sushi, plugin, lv2_path, period, tmp, preload = (
            self.sushi_path, self.plugin, self.lv2_path, self.period, self.tmp, self.preload)
        self.jack_log = (tmp / "jackd.log").open("wb")
        self.jackd = subprocess.Popen(
            ["jackd", "--no-realtime", "-d", "dummy", "-r", str(SR), "-p", str(period)],
            stdout=self.jack_log, stderr=subprocess.STDOUT)
        self.procs.append(self.jackd)
        self._wait_for_jack()
        config = {
            "host_config": {"samplerate": SR},
            "tracks": [{
                "name": "guitar", "channels": 2,
                "inputs": [{"engine_channel": 0, "track_channel": 0},
                           {"engine_channel": 0, "track_channel": 1}],
                "outputs": [{"engine_bus": 0, "track_bus": 0}],
                "plugins": ([{"uri": PLUGINS[plugin]["uri"], "name": "tuner", "type": "lv2"}]
                            if PLUGINS[plugin]["uri"] else [])
                + [{"uri": COMPRESSOR, "name": "compressor", "type": "lv2"}],
            }],
        }
        (tmp / "c.json").write_text(json.dumps(config))
        self.port = free_port()
        self.address = f"localhost:{self.port}"
        env = dict(os.environ, LV2_PATH=lv2_path)
        if preload:
            env["LD_PRELOAD"] = preload
        self.sushi_log = (tmp / "sushi.log").open("wb")
        self.sushi = subprocess.Popen(
            [sushi, "-j", "--timing-statistics", "-c", str(tmp / "c.json"),
             f"--grpc-address={self.address}", "-L", str(tmp / "sushi-elk.log")],
            stdout=self.sushi_log, stderr=subprocess.STDOUT, env=env)
        self.procs.append(self.sushi)
        deadline = time.monotonic() + 20
        while True:
            try:
                socket.create_connection(("127.0.0.1", self.port), timeout=0.5).close()
                break
            except OSError:
                if self.sushi.poll() is not None or time.monotonic() > deadline:
                    raise RuntimeError(f"Sushi did not start ({self.sushi.poll()})")
                time.sleep(0.1)
        self.sine = Sine()
        self.sine.client.activate()
        self.sine.client.connect(self.sine.out, "sushi:audio_input_0")
        self.sine.client.connect("sushi:audio_output_0", self.sine.back)

    def _wait_for_jack(self) -> None:
        import jack

        deadline = time.monotonic() + 10
        while True:
            try:
                jack.Client("tuner-probe-wait", no_start_server=True).close()
                return
            except jack.JackOpenError:
                if self.jackd.poll() is not None or time.monotonic() > deadline:
                    raise RuntimeError("jackd did not start")
                time.sleep(0.1)

    def sushi_said(self) -> list[str]:
        if not (self.tmp / "sushi.log").exists():
            return []
        self.sushi_log.flush()
        lines = (self.tmp / "sushi.log").read_text(errors="replace").splitlines()
        return [line for line in lines if "lock down" not in line][-5:]

    def close(self) -> None:
        sine = self.sine
        if sine is not None:
            try:
                sine.client.deactivate()
                sine.client.close()
            except Exception:
                pass
        for proc in reversed(self.procs):
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(10)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(10)
        for log in ("jack_log", "sushi_log"):
            if hasattr(self, log):
                getattr(self, log).close()


def connect(address: str):
    from sushi_rig.agent import connect_quietly

    return connect_quietly(address)


def disconnect(c) -> None:
    from sushi_rig import live

    live.close_controller(c)


class Reader:
    """The tuner's parameters on one held connection, by name."""

    def __init__(self, c, freq_name: str):
        self.c = c
        self.proc = c.audio_graph.get_processor_id("tuner")
        self.params = {p.name: p for p in c.parameters.get_processor_parameters(self.proc)}
        self.freq = self.params[freq_name].id
        self.outputs = [p.name for p in self.params.values() if not p.automatable]

    def hz(self) -> float:
        return self.c.parameters.get_parameter_value_in_domain(self.proc, self.freq)

    def snapshot(self) -> dict[str, float]:
        return {name: round(self.c.parameters.get_parameter_value_in_domain(self.proc, self.params[name].id), 3)
                for name in self.outputs}


def phase_ports(reader: Reader) -> list[dict]:
    return [{"id": p.id, "name": p.name, "automatable": p.automatable,
             "min": p.min_domain_value, "max": p.max_domain_value} for p in reader.params.values()]


def phase_reading(rig: Rig, reader: Reader) -> list[dict]:
    cases = [("A2", A2), ("A2 +5c", A2_UP5), ("A2 -20c", A2 * 2 ** (-20 / 1200)),
             ("E2", E2), ("E2 +12c", E2 * 2 ** (12 / 1200)), ("E4", E2 * 4)]
    rows = []
    for label, hz in cases:
        rig.sine.set(hz)
        time.sleep(2.0)
        reads = []
        for _ in range(40):  # 2 s at 20 Hz
            reads.append(reader.hz())
            time.sleep(0.05)
        note, octave, want = note_of(hz)
        cents = [cents_of(f) for f in reads if f > 0]
        rows.append({
            "case": label, "hz": round(hz, 3), "want": f"{note}{octave} {want:+.2f}c",
            "read_hz_last": round(reads[-1], 3),
            "read_note": "{}{}".format(*note_of(reads[-1])[:2]) if reads[-1] > 0 else None,
            "cents_mean": round(statistics.fmean(cents), 2) if cents else None,
            "cents_min": round(min(cents), 2) if cents else None,
            "cents_max": round(max(cents), 2) if cents else None,
            "zero_reads": sum(1 for f in reads if f <= 0),
            "outputs": reader.snapshot(),
        })
    level = rig.sine.level
    rig.sine.level = 0.0
    time.sleep(2.0)
    rows.append({"case": "silence", "outputs": reader.snapshot()})
    rig.sine.level = level
    rig.sine.set(A2)
    time.sleep(2.0)
    return rows


def first_settled(samples: list[tuple[float, float]], t0: float, until: float,
                  want: float, tolerance: float) -> float | None:
    """Seconds from t0 to the first sample after which every sample up to
    `until` reads within `tolerance` cents of `want`."""
    window = [(t, v) for t, v in samples if t0 <= t < until]
    for k, (t, _) in enumerate(window):
        if all(abs(v - want) <= tolerance for _, v in window[k:]):
            return t - t0
    return None


def summary(values: list[float | None]) -> dict:
    got = sorted(v for v in values if v is not None)
    out: dict = {"settled": len(got), "of": len(values),
                 "over_300ms": sum(1 for v in got if v > 0.3),
                 "each_ms": [None if v is None else round(1000 * v) for v in values]}
    if got:
        out.update({"median_ms": round(1000 * statistics.median(got), 1),
                    "p90_ms": round(1000 * got[int(0.9 * (len(got) - 1))], 1),
                    "min_ms": round(1000 * got[0], 1), "max_ms": round(1000 * got[-1], 1)})
    return out


def phase_latency(rig: Rig, reader: Reader, freq_name: str, steps: int, tolerance: float,
                  hold: float, poll_hz: float) -> dict:
    """Steps between A2 and A2+5c. A poller at `poll_hz` (0: as fast as it
    goes) on a held connection of its own and Sushi's notifications on
    another, both timestamped on arrival."""
    poll_c = connect(rig.address)
    poll_reader = Reader(poll_c, freq_name)
    polled: list[tuple[float, float]] = []
    raw: list[float] = []
    notified: list[tuple[float, float]] = []
    stop = threading.Event()

    def poller() -> None:
        next_at = time.monotonic()
        while not stop.is_set():
            hz = poll_reader.hz()
            polled.append((time.monotonic(), cents_of(hz)))
            raw.append(hz)
            if poll_hz:
                next_at += 1 / poll_hz
                delay = next_at - time.monotonic()
                if delay > 0:
                    time.sleep(delay)

    def on_notice(n) -> None:
        if n.parameter.processor_id == reader.proc and n.parameter.parameter_id == reader.freq:
            notified.append((time.monotonic(), cents_of(n.domain_value)))

    note_c = connect(rig.address)
    note_c.notifications.subscribe_to_parameter_updates(on_notice)
    time.sleep(1.0)
    thread = threading.Thread(target=poller, daemon=True)
    thread.start()
    rig.sine.set(A2)
    time.sleep(2.0)
    marks = []
    xruns0 = rig.sine.xruns
    for i in range(steps):
        target = A2_UP5 if i % 2 == 0 else A2
        marks.append((rig.sine.set(target), target))
        time.sleep(hold)
    stop.set()
    thread.join()
    disconnect(poll_c)
    disconnect(note_c)

    def latencies(samples):
        out = []
        for t_asked, target in marks:
            t0 = next((t for t, hz in rig.sine.switched if t >= t_asked and hz == target), t_asked)
            out.append(first_settled(samples, t0, t_asked + hold, cents_of(target), tolerance))
        return out

    def rate(samples):
        span = samples[-1][0] - samples[0][0] if len(samples) > 1 else 0
        return round(len(samples) / span, 1) if span else 0

    steady = [v for t, v in polled if any(t_asked + hold - 0.3 <= t < t_asked + hold for t_asked, _ in marks)]
    return {
        "steps": steps, "hold_s": hold, "tolerance_cents": tolerance, "xruns": rig.sine.xruns - xruns0,
        "poll": {**summary(latencies(polled)), "reads_per_s": rate(polled),
                 # how often the value read changes: the plugin's own update rate, at most
                 "changes_per_s": round(sum(1 for a, b in zip(raw, raw[1:]) if a != b)
                                        / (polled[-1][0] - polled[0][0]), 1) if len(polled) > 1 else 0},
        "notifications": {**summary(latencies(notified)), "per_s": rate(notified),
                          "count": len(notified)},
        "poll_cents_in_last_300ms_of_each_step": {
            "n": len(steady), "nan": sum(1 for v in steady if v != v)},
    }


def phase_rates(rig: Rig, reader: Reader, freq_name: str, rates: list[float], seconds: float) -> list[dict]:
    rig.sine.set(A2_UP5)
    time.sleep(2.0)
    rows = []
    for mode in ("held", "per-read"):
        for rate in rates:
            if mode == "per-read" and (rate == 0 or rate > 50):
                continue
            held = Reader(connect(rig.address), freq_name) if mode == "held" else None
            times, cents = [], []
            cpu_me, cpu_sushi, xruns0 = time.process_time(), proc_cpu(rig.sushi.pid), rig.sine.xruns
            t_start = next_at = time.monotonic()
            while time.monotonic() - t_start < seconds:
                if rate == 0:
                    time.sleep(0.05)
                    continue
                t0 = time.monotonic()
                if held is not None:
                    hz = held.hz()
                else:
                    c = connect(rig.address)
                    hz = c.parameters.get_parameter_value_in_domain(reader.proc, reader.freq)
                    disconnect(c)
                times.append(time.monotonic() - t0)
                cents.append(cents_of(hz))
                if rate != float("inf"):
                    next_at += 1 / rate
                    delay = next_at - time.monotonic()
                    if delay > 0:
                        time.sleep(delay)
            wall = time.monotonic() - t_start
            row = {"mode": mode, "asked_hz": "max" if rate == float("inf") else rate,
                   "reads_per_s": round(len(times) / wall, 1),
                   "probe_cpu_pct": round(100 * (time.process_time() - cpu_me) / wall, 1),
                   "sushi_cpu_pct": round(100 * (proc_cpu(rig.sushi.pid) - cpu_sushi) / wall, 1),
                   "xruns": rig.sine.xruns - xruns0}
            if times:
                st = sorted(times)
                good = [v for v in cents if v == v]
                row.update({"read_ms_median": round(1000 * statistics.median(st), 3),
                            "read_ms_p99": round(1000 * st[int(0.99 * (len(st) - 1))], 3)})
                if good:
                    row.update({"cents_mean": round(statistics.fmean(good), 3),
                                "cents_sd": round(statistics.pstdev(good), 3),
                                "cents_min": round(min(good), 3), "cents_max": round(max(good), 3),
                                "no_reading": len(cents) - len(good),
                                "off_by_over_1c": sum(1 for v in good if abs(v - cents_of(A2_UP5)) > 1)})
            if held is not None:
                disconnect(held.c)
            rows.append(row)
    return rows


def timings(rig: Rig, c, seconds: float, names=("tuner", "compressor")) -> dict:
    """Sushi's own timings over `seconds`, its CPU and its busiest threads'.
    elkpy's getters return (average, max, min), whatever their docstrings say
    (timingcontroller.py); each a fraction of one chunk's time."""
    budget_us = 1e6 * CHUNK / SR
    c.timings.reset_all_timings()
    cpu0, threads0, xruns0, t0 = proc_cpu(rig.sushi.pid), thread_cpu(rig.sushi.pid), rig.sine.xruns, time.monotonic()
    time.sleep(seconds)
    wall = time.monotonic() - t0
    threads1 = thread_cpu(rig.sushi.pid)
    out: dict = {}
    for name in names:
        avg, high, low = c.timings.get_processor_timings(c.audio_graph.get_processor_id(name))
        out[name] = {"avg_us": round(avg * budget_us, 2), "max_us": round(high * budget_us, 2),
                     "avg_of_chunk_pct": round(100 * avg, 3)}
    avg, high, low = c.timings.get_track_timings(c.audio_graph.get_track_id("guitar"))
    out["track"] = {"avg_us": round(avg * budget_us, 2), "max_us": round(high * budget_us, 2)}
    busy = sorted(((threads1[k] - threads0.get(k, 0.0), k) for k in threads1), reverse=True)
    out["sushi_threads_cpu_pct"] = {k: round(100 * d / wall, 2) for d, k in busy[:6] if d > 0}
    out["xruns"] = rig.sine.xruns - xruns0
    engine = c.timings.get_engine_timings()
    main = engine[0] if isinstance(engine, tuple) else engine
    out["engine"] = {"avg_of_chunk_pct": round(100 * main.average, 3), "max_of_chunk_pct": round(100 * main.max, 3)}
    out["sushi_cpu_pct"] = round(100 * (proc_cpu(rig.sushi.pid) - cpu0) / wall, 1)
    return out


def pass_through(rig: Rig, c, proc: int) -> dict:
    """Level in and out with the compressor bypassed: the tuner running, then
    bypassed, 2 s each."""
    comp = c.audio_graph.get_processor_id("compressor")
    c.audio_graph.set_processor_bypass_state(comp, True)
    out = {}
    for label, bypassed in (("tuner_running", False), ("tuner_bypassed", True)):
        c.audio_graph.set_processor_bypass_state(proc, bypassed)
        time.sleep(0.5)
        rig.sine.levels_dbfs()
        time.sleep(2.0)
        out[label] = rig.sine.levels_dbfs()
    c.audio_graph.set_processor_bypass_state(proc, False)
    c.audio_graph.set_processor_bypass_state(comp, False)
    return out


def phase_dsp(rig: Rig, reader: Reader, seconds: float, tolerance: float) -> dict:
    c = reader.c
    rig.sine.set(A2)
    time.sleep(2.0)
    out: dict = {"chunk_budget_us": round(1e6 * CHUNK / SR, 1),
                 "level_in_out_dbfs_compressor_bypassed": pass_through(rig, c, reader.proc)}
    time.sleep(1.0)
    rig.sine.levels_dbfs()
    out["running"] = timings(rig, c, seconds)
    out["running"]["reads_hz"] = round(reader.hz(), 3)
    c.audio_graph.set_processor_bypass_state(reader.proc, True)
    time.sleep(0.5)
    rig.sine.set(A2_UP5)  # the pitch moves while nobody is tuning
    rig.sine.levels_dbfs()
    out["bypassed"] = timings(rig, c, seconds)
    out["bypassed"]["reads_hz_after_a_move_to_+5c"] = round(reader.hz(), 3)
    samples: list[tuple[float, float]] = []
    t0 = time.monotonic()
    c.audio_graph.set_processor_bypass_state(reader.proc, False)
    while time.monotonic() - t0 < 3.0:
        samples.append((time.monotonic(), cents_of(reader.hz())))
    settled = first_settled(samples, t0, t0 + 3.0, cents_of(A2_UP5), tolerance)
    out["unbypass_to_right_ms"] = round(1000 * settled, 1) if settled is not None else None
    out["unbypass_cents_every_100ms"] = [
        round(next(v for t, v in samples if t >= t0 + k / 10), 2) for k in range(0, 30)]
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--sushi", default="./sushi", help="the Sushi binary (the release AppImage's AppRun)")
    parser.add_argument("--plugin", choices=sorted(PLUGINS), default="gxtuner")
    parser.add_argument("--lv2-path", default="/usr/lib/lv2", help="LV2_PATH for Sushi; both plugins must be on it")
    parser.add_argument("--preload", help="a library preloaded into Sushi alone: alsa_seq_standin.so where there is no /dev/snd/seq")
    parser.add_argument("--period", type=int, default=256, help="JACK period in frames (the rig's quantum)")
    parser.add_argument("--steps", type=int, default=40, help="pitch steps in the latency phase")
    parser.add_argument("--hold", type=float, default=1.5, help="seconds each step is held")
    parser.add_argument("--latency-poll-hz", type=float, default=100.0,
                        help="the latency phase's poll rate; 0 polls as fast as one connection goes")
    parser.add_argument("--tolerance", type=float, default=1.0, help="cents: a reading this close is right")
    parser.add_argument("--seconds", type=float, default=10.0, help="length of each rate and timing measurement")
    parser.add_argument("--rates", default="0,10,20,30,100,max", help="poll rates for the rates phase, Hz")
    parser.add_argument("--phases", default="ports,reading,latency,rates,dsp")
    parser.add_argument("--json", type=Path, help="write the report here as JSON too")
    parser.add_argument("--keep", action="store_true", help="keep the folder with Sushi's and jackd's logs")
    args = parser.parse_args()
    # elkpy's notification loops log grpc's BlockingIOError on every closed
    # connection (asyncio's default handler); it is noise here.
    logging.getLogger("asyncio").setLevel(logging.CRITICAL)
    if shutil.which("jackd") is None:
        print("jackd is not on PATH: install jackd2 (CI's rig image has only libjack)", file=sys.stderr)
        return 2
    rates = [float("inf") if r == "max" else float(r) for r in args.rates.split(",")]
    phases = args.phases.split(",")
    freq_name = PLUGINS[args.plugin]["freq"]
    tmp = Path(tempfile.mkdtemp(prefix="tuner-probe-"))
    report: dict = {"plugin": PLUGINS[args.plugin]["uri"], "period": args.period}
    rig = None
    status = 0
    try:
        rig = Rig(args.sushi, args.plugin, args.lv2_path, args.period, tmp, args.preload)
        rig.start()
        c = connect(rig.address)
        try:
            if freq_name is None:
                time.sleep(2.0)
                report["baseline"] = [timings(rig, c, args.seconds, names=("compressor",)) for _ in range(3)]
                phases = []
            reader = Reader(c, freq_name) if phases else None
            if "ports" in phases:
                report["ports"] = phase_ports(reader)
            if "reading" in phases:
                report["reading"] = phase_reading(rig, reader)
            if "latency" in phases:
                report["latency"] = phase_latency(rig, reader, freq_name, args.steps, args.tolerance, args.hold,
                                                  args.latency_poll_hz)
            if "rates" in phases:
                report["rates"] = phase_rates(rig, reader, freq_name, rates, args.seconds)
            if "dsp" in phases:
                report["dsp"] = phase_dsp(rig, reader, args.seconds, args.tolerance)
        finally:
            disconnect(c)
    except Exception as error:  # Sushi gone mid-run is a finding, not a crash of the probe
        report["error"] = f"{type(error).__name__}: {str(error).splitlines()[0][:200]}"
        status = 1
    finally:
        if rig is not None:
            report["sushi_exit"] = rig.sushi.poll() if rig.sushi else "not started"  # None: running at the end
            report["sushi_said"] = rig.sushi_said()
            rig.close()
        if args.keep:
            report["kept"] = str(tmp)
        else:
            shutil.rmtree(tmp, ignore_errors=True)
    print(json.dumps(report, indent=1))
    if args.json:
        args.json.write_text(json.dumps(report, indent=1))
    return status


if __name__ == "__main__":
    sys.exit(main())
