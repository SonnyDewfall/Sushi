#!/usr/bin/env python3
"""Is it stereo? One repeatable measurement per layer, through the loopback cables.

The loop this assumes (Sonny, 2026-11-12): two mono cables on the iD14's back
panel, Output 1 (Main L) -> Input 1 and Output 2 (Main R) -> Input 2, 48V off.

Every playback channel gets its own pitch, all at once, so what comes back on
an input names the output that fed it. Nothing depends on timing between the
player and the recorder.

    stereo_check.py iface   # layer 1: the iD14 alone, ALSA direct, no PipeWire
    stereo_check.py pw      # layer 2: the same tones through PipeWire's sink
    stereo_check.py links   # layer 3: what Sushi's outputs are linked to
    stereo_check.py tones   # layer 3: a tone per rig input, left running
    stereo_check.py rig --inputs guitar,vocal
                            # layer 3: links, then the running rig through the loop
    stereo_check.py log     # every run so far, one line each

Give a run a `--label` saying what state the rig was in ("fresh replug",
"after idctl apply daw"). Each run is appended to stereo-check-log.jsonl beside
this file, so states can be compared afterwards. Exit status is 0 only for
PASS.

`rig` needs the rig started with machines/loopback-test.yaml, whose inputs are
the two `tones` streams (eight pitches each, none shared) and not the interface's
inputs. A mono source through a stereo rig should come back on both inputs at
the same level. `--inputs` says which inputs the rig has, and `rig` passes only
when every one of them is heard on both sides equally; an input it was not told
of is reported and never counted.

`rig` reads the patchbay the rig's own qpwgraph loaded and makes only the links
in it that start at a `tones` stream, which qpwgraph cannot make itself (it does
not match a stream node by the name the patchbay gives it). It makes no link to
or from the amp that the patchbay does not ask for: an amp that is running and
not wired by the rig is a FAIL that names what is missing. Every link it made is
removed again when it ends, however it ends. It refuses to run if any of the
interface's ports is upstream of a Sushi input, directly or through another
node such as the amp: with the loop cabled that is a feedback path.

`iface` and `pw` refuse to run while Sushi is up: with the outputs cabled into
the inputs, the rig's own guitar input is the loop's return, and a tone would
go round it.

Standard library only; run with `python3 -I`.
"""

import argparse
import array
import datetime
import html
import json
import math
import os
import pathlib
import re
import signal
import subprocess
import sys
import tempfile
import time
import wave

HERE = pathlib.Path(__file__).resolve().parent
LOG = HERE / "stereo-check-log.jsonl"

RATE = 48000
# One pitch per playback channel. Multiples of 10 Hz, so each sits exactly on a
# bin of a 100 ms block, and none is a low harmonic of another: distortion of
# one output cannot be read as another output.
FREQS = [310.0, 430.0, 530.0, 710.0]
OUT_NAMES = ["out 1 (Main L)", "out 2 (Main R)", "out 3", "out 4"]
LEAD, TONE, TAIL = 0.5, 3.0, 0.5
BLOCK = 0.1
PRESENT_BELOW_LOUDEST_DB = 30.0  # a tone this far under the loudest is "not there"
FLOOR_DBFS = -105.0  # and nothing under this is a tone at all
ID14 = "iD14"


class Stopped(BaseException):
    """SIGTERM or SIGHUP arrived. Raised where the signal landed, so every
    `finally` and `with` on the way out runs as it does for Ctrl-C."""

    def __init__(self, signum):
        super().__init__(signum)
        self.signum = signum


def _stopped(signum, _frame):
    # Once only: a second signal must not cut the cleanup the first one began.
    for sig in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, signal.SIG_IGN)
    raise Stopped(signum)


def stop_on_signals():
    """A plain `kill`, or the terminal closing, ends every subcommand through
    its normal cleanup: children stopped by pid, temporary directories removed,
    links taken down. The default action would skip all of it."""
    for sig in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, _stopped)


def stop(proc):
    """Stop a child by its own pid and wait for it."""
    if proc.poll() is None:
        proc.terminate()  # arecord and pw-record both finish the WAV header on SIGTERM
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


# ---- signal ---------------------------------------------------------------

def write_tones(path, level_db, channels):
    """All `channels` at once, each its own pitch, at `level_db` dBFS peak."""
    amp = 10 ** (level_db / 20.0) * 32767.0
    total = int((LEAD + TONE + TAIL) * RATE)
    start, stop, fade = int(LEAD * RATE), int((LEAD + TONE) * RATE), int(0.02 * RATE)
    data = array.array("h", bytes(2 * total * channels))
    for c in range(channels):
        w = 2 * math.pi * FREQS[c] / RATE
        for n in range(start, stop):
            env = min(1.0, (n - start) / fade, (stop - n) / fade)
            data[n * channels + c] = int(round(amp * env * math.sin(w * (n - start))))
    with wave.open(str(path), "wb") as f:
        f.setnchannels(channels)
        f.setsampwidth(2)
        f.setframerate(RATE)
        f.writeframes(data.tobytes())


def read_wav(path):
    """-> (channels as lists of floats in -1..1, rate). 16 or 32 bit PCM."""
    with wave.open(str(path), "rb") as f:
        nch, rate, width = f.getnchannels(), f.getframerate(), f.getsampwidth()
        raw = f.readframes(f.getnframes())
    code, full = {2: ("h", 32768.0), 4: ("i", 2147483648.0)}[width]
    xs = array.array(code)
    xs.frombytes(raw[: len(raw) - len(raw) % (width * nch)])
    return [[v / full for v in xs[c::nch]] for c in range(nch)], rate


def goertzel(xs, freq, rate):
    """Peak amplitude of `freq` in xs (1.0 = full scale sine)."""
    w = 2 * math.cos(2 * math.pi * freq / rate)
    s1 = s2 = 0.0
    for x in xs:
        s1, s2 = x + w * s1 - s2, s1
    return 2 * math.sqrt(max(s1 * s1 + s2 * s2 - w * s1 * s2, 0.0)) / max(len(xs), 1)


def db(v):
    return 20 * math.log10(v) if v > 1e-10 else -200.0


def tone_level(xs, freq, rate):
    """dBFS of `freq` while it was sounding: the loudest two seconds of blocks."""
    n = int(rate * BLOCK)
    blocks = sorted(goertzel(xs[i:i + n], freq, rate) for i in range(0, len(xs) - n + 1, n))
    top = blocks[-int(2.0 / BLOCK):]
    return db(sum(top) / len(top)) if top else -200.0


def measure(path, inputs=(0, 1)):
    chans, rate = read_wav(path)
    if len(chans) <= max(inputs):
        sys.exit(f"{path}: {len(chans)} channels recorded, need {max(inputs) + 1}")
    return [[round(tone_level(chans[i], f, rate), 1) for f in FREQS] for i in inputs]


def judge(matrix, tolerance_db):
    """matrix[input][tone] in dBFS -> (verdict, one line per input, balance dB)."""
    loudest = max(max(row) for row in matrix)
    limit = max(loudest - PRESENT_BELOW_LOUDEST_DB, FLOOR_DBFS)
    heard = [[t for t, v in enumerate(row) if v >= limit] for row in matrix]
    lines = []
    for i, tones in enumerate(heard):
        what = ", ".join(f"{OUT_NAMES[t]} at {matrix[i][t]:.1f} dBFS" for t in tones)
        if not what:
            what = (f"nothing (its own output's pitch is at {matrix[i][i]:.1f} dBFS, "
                    f"{loudest - matrix[i][i]:.1f} dB under the loudest)")
        lines.append(f"input {i + 1} hears: {what}")
    balance = None
    if heard == [[0], [1]]:
        balance = round(matrix[0][0] - matrix[1][1], 1)
        if abs(balance) <= tolerance_db:
            verdict = "PASS: stereo, each output on its own input"
        else:
            verdict = f"FAIL: stereo but unbalanced, left minus right {balance:+.1f} dB"
    elif heard == [[], []]:
        verdict = "FAIL: silent, nothing came back on either input"
    elif heard == [[0], []]:
        verdict = "FAIL: left only, output 2 is silent"
    elif heard == [[], [1]]:
        verdict = "FAIL: right only, output 1 is silent"
    elif heard == [[1], [0]]:
        verdict = "FAIL: left and right are swapped"
    elif heard[0] == heard[1] and heard[0]:
        verdict = "FAIL: both outputs carry the same signal (mono, not stereo)"
    else:
        verdict = "FAIL: unexpected routing, see what each input hears"
    return verdict, lines, balance


def print_matrix(matrix, level_db):
    print(f"\n  sent at {level_db:.0f} dBFS; received, dBFS at each output's pitch:")
    print("            " + "".join(f"{n.split(' (')[0]:>9}" for n in OUT_NAMES))
    for i, row in enumerate(matrix):
        print(f"  input {i + 1}   " + "".join(f"{v:9.1f}" if v > -150 else "        -" for v in row))
    print()


# ---- the machine ------------------------------------------------------------

def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def sushi_pids():
    """Sushi's pids. It runs as `sushi.bin` from the AppImage, or as `sushi`."""
    out = run(["pgrep", "-x", "sushi|sushi\\.bin"]).stdout.split()
    return [int(p) for p in out]


def refuse_if_rig_up(force):
    pids = sushi_pids()
    if pids and not force:
        sys.exit(f"Sushi is running (pid {pids}). With the loop cabled, its input is the\n"
                 "loop's return: stop the rig first (`sushi-rig down`), or pass --force.")


def id14_card():
    for line in run(["aplay", "-l"]).stdout.splitlines():
        m = re.match(r"card (\d+): .*" + ID14, line)
        if m:
            return int(m.group(1))
    sys.exit("no iD14 in `aplay -l`: is it plugged in?")


def pw_nodes():
    """-> {media.class: (node.name, channels, positions, volume info)} for the iD14."""
    found = {}
    for o in json.loads(run(["pw-dump"]).stdout or "[]"):
        p = o.get("info", {}).get("props", {})
        if ID14 in str(p.get("node.name", "")) and p.get("media.class") in ("Audio/Sink", "Audio/Source"):
            vol = None
            for prm in o.get("info", {}).get("params", {}).get("Props", []):
                if "channelVolumes" in prm:
                    vol = {"mute": prm.get("mute"), "volume": prm.get("volume"),
                           "channelVolumes": prm.get("channelVolumes")}
            found[p["media.class"]] = {"name": p["node.name"], "id": o["id"],
                                       "channels": p.get("audio.channels"),
                                       "position": re.findall(r"\w+", str(p.get("audio.position"))),
                                       "volume": vol}
    return found


def speaker_volume(card):
    """The iD14's own output volume as ALSA shows it, which PipeWire's sink volume drives."""
    out = run(["amixer", "-c", str(card), "cget", "name=Speaker Playback Volume"]).stdout
    m = re.search(r": values=([\d,]+)", out)
    if not m:
        return None
    steps = [int(v) for v in m.group(1).split(",")]
    return {"steps": steps, "db": [v - 127 for v in steps]}  # 0..127 over -127..0 dB


def record_play(rec_cmd, play_cmd, seconds):
    """Start the recorder, play, stop the recorder by its own pid."""
    rec = subprocess.Popen(rec_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    try:
        time.sleep(1.0)
        if rec.poll() is not None:
            sys.exit(f"recorder failed: {' '.join(rec_cmd)}\n{rec.stderr.read()}")
        played = run(play_cmd)
        if played.returncode:
            sys.exit(f"player failed: {' '.join(play_cmd)}\n{played.stderr}")
        time.sleep(0.5)
    finally:
        stop(rec)
    return seconds


def save(entry):
    entry = {"at": datetime.datetime.now().isoformat(timespec="seconds"), **entry}
    with LOG.open("a") as f:
        f.write(json.dumps(entry) + "\n")


def finish(layer, args, matrix, extra=None):
    verdict, lines, balance = judge(matrix, args.tolerance)
    print_matrix(matrix, args.level)
    for line in lines:
        print("  " + line)
    print(f"\n  {layer}: {verdict}\n")
    save({"layer": layer, "label": args.label, "level_dbfs": args.level, "verdict": verdict,
          "balance_db": balance, "matrix_dbfs": matrix, **(extra or {})})
    return 0 if verdict.startswith("PASS") else 1


# ---- layers -----------------------------------------------------------------

def cmd_iface(args):
    """The interface alone: aplay and arecord on the ALSA device, PipeWire not in the path."""
    refuse_if_rig_up(args.force)
    card = args.card if args.card is not None else id14_card()
    dev = f"plughw:{card},0"
    with tempfile.TemporaryDirectory() as d:
        tones, back = os.path.join(d, "tones.wav"), os.path.join(d, "back.wav")
        write_tones(tones, args.level, 4)
        record_play(
            ["arecord", "-q", "-D", dev, "-c", "10", "-f", "S32_LE", "-r", str(RATE), back],
            ["aplay", "-q", "-D", dev, tones], LEAD + TONE + TAIL)
        matrix = measure(back)
    vol = speaker_volume(card)
    print(f"layer 1, interface alone ({dev}), label: {args.label or '-'}")
    if vol:
        print(f"  the iD14's 'Speaker Playback Volume' is at {vol['db'][:2]} dB (left, right)")
    return finish("iface", args, matrix, {"device": dev, "speaker_volume": vol})


def cmd_pw(args):
    """The same tones into PipeWire's iD14 sink, recorded from PipeWire's iD14 source."""
    refuse_if_rig_up(args.force)
    nodes = pw_nodes()
    sink, source = nodes.get("Audio/Sink"), nodes.get("Audio/Source")
    if not sink or not source:
        sys.exit(f"PipeWire has no iD14 sink and source: found {nodes}")
    nch = int(sink["channels"] or 2)
    positions = sink["position"] or ["FL", "FR"]
    print(f"layer 2, through PipeWire, label: {args.label or '-'}")
    print(f"  sink   {sink['name']}  {positions}  volume {sink['volume']}")
    print(f"  source {source['name']}  {source['channels']} channels")
    with tempfile.TemporaryDirectory() as d:
        tones, back = os.path.join(d, "tones.wav"), os.path.join(d, "back.wav")
        write_tones(tones, args.level, min(nch, 4))
        record_play(
            ["pw-record", "--target", source["name"], "--rate", str(RATE), "--format", "s32",
             # The first two inputs by name. All ten will not record: libsndfile
             # refuses a ten-channel WAV here ("Format not recognised").
             "--channels", "2", "--channel-map", ",".join(source["position"][:2]), back],
            ["pw-play", "--target", sink["name"], "--volume", "1.0",
             "--channel-map", ",".join(positions[:4]), tones], LEAD + TONE + TAIL)
        matrix = measure(back)
    return finish("pw", args, matrix, {"sink": sink, "source": source})


def parse_links(text):
    """`pw-link -l` -> {port: {"to": [...], "from": [...]}}."""
    ports, cur = {}, None
    for line in text.splitlines():
        if not line.strip():
            continue
        if not line[0].isspace():
            cur = ports.setdefault(line.strip(), {"to": [], "from": []})
        elif cur is not None:
            m = re.match(r"\s+\|(->|<-)\s+(.*)", line)
            if m:
                cur["to" if m.group(1) == "->" else "from"].append(m.group(2).strip())
    return ports


def cmd_links(args):
    """What Sushi's outputs are linked to, and who else might be linking them."""
    text = open(args.file).read() if args.file else run(["pw-link", "-l"]).stdout
    ports = parse_links(text)
    outs = {}
    for port, links in ports.items():
        m = re.match(r"(?i)sushi[^:]*:audio_output_(\d+)$", port)
        if m:
            outs[int(m.group(1))] = links["to"]
    ins = {p: l["from"] for p, l in ports.items() if re.match(r"(?i)sushi[^:]*:audio_input_\d+$", p)}
    qpw = run(["pgrep", "-a", "qpwgraph"]).stdout.strip().splitlines()

    print(f"layer 3, links, label: {args.label or '-'}")
    problems = []
    if not outs and not ins:
        verdict = "FAIL: no Sushi ports in the graph, the rig is not running"
    else:
        for n in sorted(outs):
            print(f"  sushi output {n} -> {', '.join(outs[n]) or '(nothing)'}")
        for p in sorted(ins):
            print(f"  {p} <- {', '.join(ins[p]) or '(nothing)'}")
        for n, side in ((0, "playback_FL"), (1, "playback_FR")):
            want = [t for t in outs.get(n, []) if ID14 in t and t.endswith(side)]
            if not want:
                problems.append(f"master output {n} does not reach {side}")
            wrong = [t for t in outs.get(n, []) if ID14 in t and not t.endswith(side)]
            if wrong:
                problems.append(f"master output {n} also reaches {', '.join(wrong)}")
        for n in sorted(outs):
            stray = [t for t in outs[n] if n >= 2 and ID14 in t]
            if stray:
                problems.append(f"stem output {n} reaches the interface: {', '.join(stray)}")
        verdict = "FAIL: " + "; ".join(problems) if problems else \
            "PASS: master left to FL and right to FR, no stem on the interface"
    print("  qpwgraph processes:" + ("" if qpw else " none"))
    for line in qpw:
        print("    " + line)
    if len(qpw) > 1:
        print("  NOTE: more than one qpwgraph is running")
    print(f"\n  links: {verdict}\n")
    save({"layer": "links", "label": args.label, "verdict": verdict,
          "sushi_outputs": outs, "sushi_inputs": ins, "qpwgraph": qpw})
    return 0 if verdict.startswith("PASS") else 1


TONE_NODES = ["stereo-check-guitar", "stereo-check-vocal"]
INPUT_NAMES = [node.split("-")[-1] for node in TONE_NODES]  # guitar, vocal
TONE_PORTS = [f"{node}:output_MONO" for node in TONE_NODES]
# Eight pitches per rig input, spread over five octaves, no two shared, all
# multiples of 10 Hz. One steady pitch is not a fair test of a stereo reverb:
# its two sides differ at any single frequency by design (measured 2026-11-12,
# MVerb alone: 17 dB apart at 310 Hz). Summed over a spread they should agree,
# as the offline sweep in tests/test_stereo.py finds they do.
#
# Which pitches (changed 2026-11-12, after the review of the first sets). An
# amp or a drive on one input makes products of its pitches, sums and
# differences of them, and with the first sets those of the guitar landed on
# six of the vocal's eight (2 x 310 - 190 = 430) and read as a vocal 29 dB
# under the guitar. Now every guitar pitch is 130 Hz above a multiple of 140
# and every vocal pitch 70 Hz above one. A product of the guitar's pitches
# with coefficients summing to s is then 130 x s above a multiple of 140, which
# is 70 only when s is 7 more or less than a multiple of 14: nothing of the
# guitar's below the 7th order lands on a vocal pitch. A product of the vocal's
# is a multiple of 140 or 70 above one, never 130: nothing of the vocal's
# lands on a guitar pitch at any order. None is a multiple of 50 or 60 Hz, so
# mains hum is not read as a tone. tests/test_stereo_check.py counts the
# products out rather than trusting this paragraph.
TONE_STEP = 140.0
TONE_SETS = [
    [130.0, 270.0, 410.0, 690.0, 1110.0, 1810.0, 2930.0, 4610.0],
    [210.0, 490.0, 770.0, 1190.0, 1610.0, 2590.0, 3710.0, 5110.0],
]
# Where neither input should put anything: 60 and 80 Hz above a multiple of
# 140. The guitar's products reach these from the 6th order, the vocal's
# never. What reads there is the noise the tones stand in. (A stage both
# inputs pass through, the master's chain, can put a product of the two
# there. That only raises what a tone has to stand above.)
QUIET_ABOVE_STEP = (60.0, 80.0)
# "Heard" is three tests, all needed, on each side.
# The level beside an input's pitches is what noise alone reads at them too:
# idle, the pitches read -114 dBFS (the log, 2026-11-12T18:10:26), 9 dB under
# FLOOR_DBFS, and with the input gains up 10 dB noise would clear that floor on
# both sides, equally. Noise reads the same at a pitch and beside it; the
# quietest input in that day's log read -73 dBFS, 40 dB over the idle reading.
STANDS_ABOVE_DB = 10.0
# And an expected input this far under the strongest expected one is not that
# input. From the log: guitar_vocal's guitar reads 14 dB under its vocal, and
# the amp's products on the vocal's old pitches read 29 dB under the guitar.
HEARD_WITHIN_DB = 20.0

AMP_IN, AMP_OUT = "NAM:Input", "NAM:Output"
SUSHI_INPUT = re.compile(r"(?i)sushi[^:]*:audio_input_\d+$")


def quiet_beside(freqs):
    """For each pitch, the nearest quiet frequency 50 Hz or more below it.

    Not the next bin: a chorus or a detune moves a pitch off its bin, and what
    it spills falls away with distance. Multiples of 10 Hz, as the pitches
    are, so a steady pitch puts nothing at all here."""
    quiet = []
    for f in freqs:
        g = f - 50.0
        while g % TONE_STEP not in QUIET_ABOVE_STEP:
            g -= 10.0
        quiet.append(g)
    return quiet


def set_level(xs, freqs, rate):
    """dBFS of a set of pitches together: their powers summed."""
    return round(db(math.sqrt(sum((10 ** (tone_level(xs, f, rate) / 20.0)) ** 2 for f in freqs))), 1)


def measure_rig(chans, rate):
    """-> (matrix, beside), each [side][rig input] in dBFS: every input's
    pitches together, and the quiet frequencies beside them together."""
    matrix = [[set_level(chans[i], freqs, rate) for freqs in TONE_SETS] for i in (0, 1)]
    beside = [[set_level(chans[i], quiet_beside(freqs), rate) for freqs in TONE_SETS] for i in (0, 1)]
    return matrix, beside


def cmd_tones(args):
    """One endless tone per rig input, linked to nothing until `rig` links it."""
    amp = 10 ** (args.level / 20.0) * 32767.0
    chunk = RATE // 10  # 100 ms holds a whole number of cycles of every pitch
    players = []
    try:
        for node, freqs in zip(TONE_NODES, TONE_SETS):
            each = amp / len(freqs)  # so the sum can never exceed the stated level
            block = array.array("h", (int(round(sum(each * math.sin(2 * math.pi * f * n / RATE + i)
                                                    for i, f in enumerate(freqs))))
                                      for n in range(chunk))).tobytes()
            props = f"{{ node.name={node} node.description={node} node.autoconnect=false }}"
            proc = subprocess.Popen(
                ["pw-cat", "-p", "--raw", "--target", "0", "--format", "s16", "--rate", str(RATE),
                 "--channels", "1", "-P", props, "-"], stdin=subprocess.PIPE)
            players.append((proc, block))
            print(f"{node}: {len(freqs)} pitches, {freqs[0]:.0f} to {freqs[-1]:.0f} Hz, "
                  f"peak under {args.level:.0f} dBFS, pid {proc.pid}", flush=True)
        while all(p.poll() is None for p, _ in players):
            for proc, block in players:
                proc.stdin.write(block)  # blocks when the pipe is full: pw-cat sets the pace
                proc.stdin.flush()
    except (KeyboardInterrupt, Stopped, BrokenPipeError):
        pass  # Ctrl-C or a plain kill is how this ends
    finally:
        for proc, _ in players:
            stop(proc)
    return 0


# ---- the rig's graph, as lists of (output port, input port) ---------------------

def links_of(ports):
    """`parse_links`' answer as links, each once, in the order listed."""
    found = []
    for port, linked in ports.items():
        for link in [(port, dst) for dst in linked["to"]] + [(src, port) for src in linked["from"]]:
            if link not in found:
                found.append(link)
    return found


def node_of(port):
    """The node a port belongs to: the text before the first colon. A node
    whose own name holds a colon is cut short by this, which can only take two
    nodes for one. The feedback guard then refuses too often, never too
    seldom."""
    return port.split(":", 1)[0]


def feedback(links):
    """Every way one of the interface's ports reaches a Sushi input, followed
    back from each Sushi input through any node between: `[]` when there is
    none. What goes into a node is taken to come out of every output it has,
    so `iD14 -> NAM:Input` and `NAM:Output -> sushi` is a path."""
    feeders, inputs_of = {}, {}
    for out, inp in links:
        feeders.setdefault(inp, []).append(out)
        known = inputs_of.setdefault(node_of(inp), [])
        if inp not in known:
            known.append(inp)
    found = []

    def walk(port, path, seen):
        for src in feeders.get(port, []):
            if ID14 in src:
                found.append(" <- ".join(path + [src]))
                continue
            for inp in inputs_of.get(node_of(src), []):
                if inp not in seen:
                    seen.add(inp)
                    walk(inp, path + [src, inp], seen)

    for port in sorted(feeders):
        if SUSHI_INPUT.match(port):
            walk(port, [port], {port})
    return found


def patchbay_links(xml):
    """The links a saved qpwgraph patchbay asks for, by port, in file order."""
    return [(html.unescape(out), html.unescape(inp)) for out, inp in re.findall(
        r'<output\b[^>]*\bport="([^"]*)"[^>]*/>\s*<input\b[^>]*\bport="([^"]*)"[^>]*/>', xml)]


def patchbay_of(argv):
    """The patchbay file in a qpwgraph command line (`qpwgraph -a FILE -m`), or None."""
    files = [a for a in argv[1:] if a and not a.startswith("-")]
    return files[0] if files else None


def running_patchbays():
    """The patchbay each running qpwgraph was started with, from its own command line."""
    found = []
    for pid in run(["pgrep", "-x", "qpwgraph"]).stdout.split():
        try:
            argv = pathlib.Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
        except OSError:
            continue
        file = patchbay_of([a.decode(errors="replace") for a in argv])
        if file and file not in found:
            found.append(file)
    return found


def hand_wired(wanted, guitar):
    """A patchbay's links with the amp put in front of the guitar by hand, as
    amp.amp_patchbay does it: the guitar into NAM, and NAM into every Sushi
    input the guitar fed."""
    fed = [inp for out, inp in wanted if out == guitar and SUSHI_INPUT.match(inp)]
    links = [(AMP_OUT, inp) if out == guitar and inp in fed else (out, inp) for out, inp in wanted]
    if fed:
        links.append((guitar, AMP_IN))
    return [link for n, link in enumerate(links) if link not in links[:n]]


def feeds_rig(link):
    """Whether a link is a tone's into Sushi or into the amp: the only kind
    `rig` makes. A tone's link anywhere else in the patchbay (the tuner's tap
    leaves the guitar's port too) is the rig's business and is left to it."""
    return link[0] in TONE_PORTS and (link[1] == AMP_IN or bool(SUSHI_INPUT.match(link[1])))


def ours(link):
    """Whether a link is one this test needs there: a tone's into the rig, or the amp's."""
    return feeds_rig(link) or link[0] == AMP_OUT or link[1] == AMP_IN


def plan_rig(wanted, live, amp_running, expected, wire_amp=False):
    """What `rig` may link, and why it must not start: `(wanted, to make, problems)`.

    `wanted` is the rig's own patchbay, `live` the graph, `expected` the
    indices of the inputs the caller says the rig has. Only links the patchbay
    asks for are ever made, and of those only a tone's into Sushi or the amp:
    qpwgraph makes the rest or they are missing, which is the finding. With
    `wire_amp`, and a patchbay that does not wire the amp, the amp's links are
    made here too, and the verdict says so. Pure."""
    problems = []
    for t, port in enumerate(TONE_PORTS):
        listed = any(link[0] == port and feeds_rig(link) for link in wanted)
        if t in expected and not listed:
            problems.append(f"{INPUT_NAMES[t]} is expected, but the rig's patchbay has no link from {port} "
                            "into Sushi or the amp")
        if listed and t not in expected:
            problems.append(f"the rig's patchbay wires {INPUT_NAMES[t]} ({port}), which --inputs does not name")
    missing = [f"{what} {port}" for what, port, there in (
        ("into", AMP_IN, any(inp == AMP_IN for _, inp in wanted)),
        ("out of", AMP_OUT, any(out == AMP_OUT for out, _ in wanted))) if not there]
    if wire_amp:
        if not amp_running:
            problems.append(f"--wire-amp, but the graph has no {AMP_IN}: the amp is not running")
        elif 0 not in expected:
            problems.append("--wire-amp puts the amp in front of the guitar, which --inputs does not name")
        elif missing:
            wanted = hand_wired(wanted, TONE_PORTS[0])
    elif amp_running and missing:
        problems.append(
            "the amp is running and the rig did not wire it: its patchbay has no link "
            + " and none ".join(missing) + ", so the guitar does not go through the amp")
    stray = [link for link in live if feeds_rig(link) and link not in wanted]
    if stray:
        problems.append("links from the tones that the rig's patchbay does not ask for (remove with "
                        "`pw-link -d`): " + ", ".join(f"{a} -> {b}" for a, b in stray))
    make = [link for link in wanted
            if link not in live and (feeds_rig(link) or (wire_amp and link[0] == AMP_OUT))]
    return wanted, make, problems


def pw_link(out, inp, remove=False):
    """Make or remove one link: None, or why `pw-link` could not."""
    done = run(["pw-link"] + (["-d"] if remove else []) + [out, inp])
    if done.returncode == 0:
        return None
    return (done.stderr or done.stdout).strip() or f"pw-link exit status {done.returncode}"


def judge_rig(matrix, beside, expected, tolerance_db):
    """Every expected rig input on both interface inputs, equally: `(verdict, lines)`.

    `matrix[side][input]` and `beside[side][input]` are `measure_rig`'s,
    `expected` the indices of the inputs the rig has. An input is heard on a
    side when it reads above FLOOR_DBFS, and STANDS_ABOVE_DB above the level
    beside its pitches, and within HEARD_WITHIN_DB of the strongest expected
    input. One that is not expected is reported and never counted. Never PASS
    with nothing expected or nothing measured."""
    expected = sorted(set(expected))
    if not expected:
        return "FAIL: no rig input was expected, so nothing was tested", []
    if not matrix or not beside or len(matrix) < 2 or len(beside) < 2:
        return "FAIL: nothing was measured", []
    strongest = max(matrix[side][t] for t in expected for side in (0, 1))

    def short(side, t):
        """Why input t is not heard on a side: nothing, when it is."""
        level, quiet = matrix[side][t], beside[side][t]
        why = []
        if level < FLOOR_DBFS:
            why.append(f"under the {FLOOR_DBFS:.0f} dBFS floor")
        if level - quiet < STANDS_ABOVE_DB:
            why.append(f"{level - quiet:.1f} dB above the level beside its pitches, "
                       f"{STANDS_ABOVE_DB:.0f} needed")
        if level < strongest - HEARD_WITHIN_DB:
            why.append(f"{strongest - level:.1f} dB under the strongest expected input, "
                       f"{HEARD_WITHIN_DB:.0f} allowed")
        return why

    lines, bad = [], []
    for t, name in enumerate(INPUT_NAMES):
        left, right = matrix[0][t], matrix[1][t]
        reads = f"left {left:.1f} dBFS, right {right:.1f} dBFS"
        if t not in expected:
            lines.append(f"{name}: not expected, not counted ({reads})")
            continue
        missing = [short(0, t), short(1, t)]
        if missing[0] and missing[1]:
            louder = 0 if left >= right else 1
            lines.append(f"{name}: expected, NOT heard ({reads}; {'; '.join(missing[louder])})")
            bad.append(f"{name} was not heard")
            continue
        lines.append(f"{name}: {reads}, left minus right {left - right:+.1f} dB")
        if missing[0] or missing[1]:
            side = 1 if missing[1] else 0
            lines.append(f"{name}: the {('left', 'right')[side]} is {'; '.join(missing[side])}")
            bad.append(f"{name} is on the {('right', 'left')[side]} only")
        elif abs(left - right) > tolerance_db:
            bad.append(f"{name} is unbalanced by {left - right:+.1f} dB")
    if bad:
        return "FAIL: " + "; ".join(bad), lines
    names = " and ".join(INPUT_NAMES[t] for t in expected)
    return f"PASS: {names}, {'each ' if len(expected) > 1 else ''}on both sides equally", lines


def parse_inputs(text):
    """`guitar,vocal` or `0,1` -> the indices of those rig inputs."""
    chosen = []
    for word in text.split(","):
        word = word.strip().lower()
        if word in INPUT_NAMES:
            index = INPUT_NAMES.index(word)
        elif word.isdigit() and int(word) < len(INPUT_NAMES):
            index = int(word)
        else:
            known = ", ".join(f"{name} (or {n})" for n, name in enumerate(INPUT_NAMES))
            raise argparse.ArgumentTypeError(f"{word!r} is not a rig input: {known}")
        if index not in chosen:
            chosen.append(index)
    return sorted(chosen)


def cmd_rig(args):
    """The running rig through the loop: its links, then what comes back on each input."""
    if args.expect is not None:
        sys.exit("--expect counted the inputs heard without saying which. Say which inputs the\n"
                 "rig has: --inputs guitar, or --inputs guitar,vocal.")
    if not args.inputs:
        sys.exit("say which inputs the rig has: --inputs guitar, or --inputs guitar,vocal")
    expected = args.inputs
    entry = {"layer": "rig", "label": args.label, "expected": [INPUT_NAMES[t] for t in expected],
             "wire_amp": bool(args.wire_amp)}
    by_hand = (" (the amp was wired by --wire-amp: this says nothing about the rig's own amp wiring)"
               if args.wire_amp else "")

    def ended(verdict, **more):
        verdict += by_hand
        print(f"\n  rig: {verdict}\n")
        save({**entry, "verdict": verdict, **more})
        return 0 if verdict.startswith("PASS") else 1

    if not sushi_pids():
        return ended("FAIL: Sushi is not running")

    def refuse_feedback(links, linked="Nothing was linked here."):
        loop = feedback(links)
        if loop:
            save({**entry, "verdict": "FAIL: refused, the interface feeds Sushi: " + "; ".join(loop)})
            sys.exit("STOP: the interface's ports feed Sushi, which with the loop cabled is a\n"
                     f"feedback path. Stop the rig. {linked}\n  " + "\n  ".join(loop))

    # First, before the patchbay is even looked for: this is the safety check.
    live = links_of(parse_links(run(["pw-link", "-l"]).stdout))
    refuse_feedback(live)
    files = [args.patchbay] if args.patchbay else running_patchbays()
    if len(files) != 1:
        return ended("FAIL: cannot tell which patchbay the rig's qpwgraph loaded (found "
                     f"{', '.join(files) or 'none'}): pass --patchbay FILE")
    entry["patchbay"] = files[0]
    try:
        wanted = patchbay_links(pathlib.Path(files[0]).read_text())
    except OSError as exc:
        return ended(f"FAIL: cannot read the rig's patchbay: {exc}")
    # -l lists only ports that are linked, so the amp's presence is read from -i.
    amp_running = AMP_IN in [line.strip() for line in run(["pw-link", "-i"]).stdout.splitlines()]
    wanted, make, problems = plan_rig(wanted, live, amp_running, expected, args.wire_amp)
    refuse_feedback(live + make)  # again, with what would be linked here counted in
    if problems:
        return ended("FAIL: " + "; ".join(problems))

    print(f"rig: patchbay {files[0]}")
    made = []
    try:
        for out, inp in make:
            error = pw_link(out, inp)
            if error:
                return ended(f"FAIL: could not link {out} -> {inp}: {error}")
            made.append((out, inp))
            print(f"  linked {out} -> {inp}")
        entry["made"] = [f"{a} -> {b}" for a, b in made]
        time.sleep(1.0)
        live = links_of(parse_links(run(["pw-link", "-l"]).stdout))
        refuse_feedback(live, "The links made here are being removed.")
        unmade = [f"{a} -> {b}" for a, b in wanted if ours((a, b)) and (a, b) not in live]
        if unmade:
            return ended("FAIL: the rig's patchbay asks for links that are not there: " + ", ".join(unmade))
        links_status = cmd_links(args)
        source = pw_nodes().get("Audio/Source")
        if not source:
            return ended("FAIL: PipeWire has no iD14 source")
        with tempfile.TemporaryDirectory() as d:
            back = os.path.join(d, "back.wav")
            rec = subprocess.Popen(
                ["pw-record", "--target", source["name"], "--rate", str(RATE), "--format", "s32",
                 "--channels", "2", "--channel-map", ",".join(source["position"][:2]), back],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                time.sleep(args.seconds + 0.5)
            finally:
                stop(rec)
            try:
                chans, rate = read_wav(back)
            except (OSError, EOFError, wave.Error, KeyError) as exc:
                return ended(f"FAIL: nothing was measured, the recording cannot be read ({exc})")
        if len(chans) < 2 or len(chans[0]) < int(rate * BLOCK):
            return ended("FAIL: nothing was measured, the recording is empty")
        matrix, beside = measure_rig(chans, rate)
        verdict, lines = judge_rig(matrix, beside, expected, args.tolerance)
        for line in lines:
            print("  " + line)
        if links_status and verdict.startswith("PASS"):
            verdict = "FAIL: the sound is balanced but the links are not as expected (see links)"
        return ended(verdict, matrix_dbfs=matrix, beside_dbfs=beside, pitches_hz=TONE_SETS)
    finally:
        # Every removal before any is reported: a print to a terminal that has
        # gone (SIGHUP) must not leave the rest of the links in the rig.
        removed = [(out, inp, pw_link(out, inp, remove=True)) for out, inp in reversed(made)]
        for out, inp, error in removed:
            print(f"  removed {out} -> {inp}" if not error else
                  f"  WARNING: {out} -> {inp} was made here and could not be removed: {error}")


def cmd_log(args):
    if not LOG.exists():
        print("no runs yet")
        return 0
    for line in LOG.read_text().splitlines():
        e = json.loads(line)
        print(f"{e['at']}  {e['layer']:<6} {(e.get('label') or '-'):<28} {e['verdict']}")
    return 0


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    for name, fn in (("iface", cmd_iface), ("pw", cmd_pw)):
        s = sub.add_parser(name, help=fn.__doc__)
        s.add_argument("--level", type=float, default=-40.0, help="tone level, dBFS [-40]")
        s.add_argument("--tolerance", type=float, default=1.5,
                       help="left/right difference that still passes, dB [1.5; the input gains are analogue knobs]")
        s.add_argument("--label", default="", help="what state this run measures")
        s.add_argument("--force", action="store_true", help="run even though Sushi is up")
        s.set_defaults(fn=fn)
    sub.choices["iface"].add_argument("--card", type=int, help="ALSA card number [the iD14's]")
    s = sub.add_parser("links", help=cmd_links.__doc__)
    s.add_argument("--label", default="")
    s.add_argument("--file", help="read a saved `pw-link -l` instead of the live graph")
    s.set_defaults(fn=cmd_links)
    s = sub.add_parser("tones", help=cmd_tones.__doc__)
    s.add_argument("--level", type=float, default=-20.0, help="tone level, dBFS [-20]")
    s.set_defaults(fn=cmd_tones)
    s = sub.add_parser("rig", help=cmd_rig.__doc__)
    s.add_argument("--inputs", type=parse_inputs, metavar="NAMES",
                   help="required: the inputs this rig has, by name or index, comma separated: "
                        "guitar (0), vocal (1). Each must be heard on both sides equally; "
                        "one not named is reported and never counted")
    s.add_argument("--expect", help=argparse.SUPPRESS)  # refused: it counted inputs without naming them
    s.add_argument("--patchbay", metavar="FILE",
                   help="the patchbay the rig's qpwgraph loaded [read from the running qpwgraph's command line]")
    s.add_argument("--wire-amp", action="store_true",
                   help="if the rig's patchbay does not wire the amp, link it in front of the guitar "
                        "by hand. The verdict then says nothing about the rig's own amp wiring")
    s.add_argument("--label", default="")
    s.add_argument("--file", help=argparse.SUPPRESS)
    s.add_argument("--seconds", type=float, default=3.0, help="how long to listen [3]")
    s.add_argument("--tolerance", type=float, default=2.5,
                   help="left/right difference that still passes, dB [2.5; the bare loop is 1.3]")
    s.set_defaults(fn=cmd_rig)
    sub.add_parser("log", help="every run so far").set_defaults(fn=cmd_log)
    args = p.parse_args()
    if getattr(args, "level", -40) > -12:
        sys.exit("refusing a level above -12 dBFS")
    stop_on_signals()
    try:
        status = args.fn(args)
    except Stopped as stopped:
        status = 128 + stopped.signum  # after every finally on the way out has run
    sys.exit(status)


if __name__ == "__main__":
    main()
