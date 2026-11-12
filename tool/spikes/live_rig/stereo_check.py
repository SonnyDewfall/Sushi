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
    stereo_check.py rig     # layer 3: links, then the running rig through the loop
    stereo_check.py log     # every run so far, one line each

Give a run a `--label` saying what state the rig was in ("fresh replug",
"after idctl apply daw"). Each run is appended to stereo-check-log.jsonl beside
this file, so states can be compared afterwards. Exit status is 0 only for
PASS.

`rig` needs the rig started with machines/loopback-test.yaml, whose inputs are
the two `tones` streams (eight pitches each, none shared) and not the interface's
inputs. A mono source through a stereo rig should come back on both inputs at
the same level; `rig` passes when every pitch it hears does.

`iface` and `pw` refuse to run while Sushi is up: with the outputs cabled into
the inputs, the rig's own guitar input is the loop's return, and a tone would
go round it.

Standard library only; run with `python3 -I`.
"""

import argparse
import array
import datetime
import json
import math
import os
import pathlib
import re
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
        if rec.poll() is None:
            rec.terminate()  # arecord and pw-record both finish the WAV header on SIGTERM
            try:
                rec.wait(timeout=5)
            except subprocess.TimeoutExpired:
                rec.kill()
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
# Eight pitches per rig input, spread over five octaves, no two shared, all
# multiples of 10 Hz. One steady pitch is not a fair test of a stereo reverb:
# its two sides differ at any single frequency by design (measured 2026-11-12,
# MVerb alone: 17 dB apart at 310 Hz). Summed over a spread they should agree,
# as the offline sweep in tests/test_stereo.py finds they do.
TONE_SETS = [
    [110.0, 190.0, 310.0, 530.0, 890.0, 1490.0, 2510.0, 4210.0],
    [130.0, 230.0, 430.0, 710.0, 1130.0, 1870.0, 3070.0, 5090.0],
]


def set_level(xs, freqs, rate):
    """dBFS of a set of pitches together: their powers summed."""
    return round(db(math.sqrt(sum((10 ** (tone_level(xs, f, rate) / 20.0)) ** 2 for f in freqs))), 1)


def cmd_tones(args):
    """One endless tone per rig input, linked to nothing until the rig's patchbay links it."""
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
    except (KeyboardInterrupt, BrokenPipeError):
        pass
    finally:
        for proc, _ in players:
            if proc.poll() is None:
                proc.terminate()
    return 0


def judge_rig(matrix, expect, tolerance_db):
    """Each rig input's pitch should be on both interface inputs, equally."""
    loudest = max(max(row) for row in matrix)
    limit = max(loudest - PRESENT_BELOW_LOUDEST_DB, FLOOR_DBFS)
    lines, bad, heard = [], [], 0
    for t, node in enumerate(TONE_NODES):
        left, right = matrix[0][t], matrix[1][t]
        name = node.split("-")[-1]
        if left < limit and right < limit:
            lines.append(f"{name}: not heard on either side")
            continue
        heard += 1
        lines.append(f"{name}: left {left:.1f} dBFS, right {right:.1f} dBFS, left minus right {left - right:+.1f} dB")
        if left < limit or right < limit:
            bad.append(f"{name} is on the {'left' if right < limit else 'right'} only")
        elif abs(left - right) > tolerance_db:
            bad.append(f"{name} is unbalanced by {left - right:+.1f} dB")
    if heard < expect:
        bad.append(f"heard {heard} of the {expect} rig inputs expected")
    verdict = "FAIL: " + "; ".join(bad) if bad else f"PASS: {heard} rig input(s), each on both sides equally"
    return verdict, lines


def cmd_rig(args):
    """The running rig through the loop: its links, then what comes back on each input."""
    if not sushi_pids():
        print("rig: FAIL: Sushi is not running")
        save({"layer": "rig", "label": args.label, "verdict": "FAIL: Sushi is not running"})
        return 1
    ports = parse_links(run(["pw-link", "-l"]).stdout)
    feedback = [f"{p} <- {src}" for p, l in ports.items() if re.match(r"(?i)sushi[^:]*:audio_input_", p)
                for src in l["from"] if ID14 in src]
    if feedback:
        sys.exit("STOP: the interface's inputs feed Sushi, which with the loop cabled is a\n"
                 "feedback path. Stop the rig. " + "; ".join(feedback))
    # qpwgraph does not make the tones' links (it warns so at `up`: it does not
    # match a stream node by the name the patchbay gives it), so they are made
    # here, each tone to the engine input its channel reads. Sushi's outputs,
    # which are what is under test, are left exactly as the rig wired them.
    inputs = run(["pw-link", "-i"]).stdout.split()  # -l lists only ports that are linked
    for n, node in enumerate(TONE_NODES):
        if f"sushi:audio_input_{n}" in inputs:
            run(["pw-link", f"{node}:output_MONO", f"sushi:audio_input_{n}"])
    time.sleep(1.0)
    links_status = cmd_links(args)
    source = pw_nodes().get("Audio/Source")
    if not source:
        sys.exit("PipeWire has no iD14 source")
    with tempfile.TemporaryDirectory() as d:
        back = os.path.join(d, "back.wav")
        rec = subprocess.Popen(
            ["pw-record", "--target", source["name"], "--rate", str(RATE), "--format", "s32",
             "--channels", "2", "--channel-map", ",".join(source["position"][:2]), back],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(args.seconds + 0.5)
        rec.terminate()
        rec.wait(timeout=5)
        chans, rate = read_wav(back)
        matrix = [[set_level(chans[i], freqs, rate) for freqs in TONE_SETS] for i in (0, 1)]
    verdict, lines = judge_rig(matrix, args.expect, args.tolerance)
    for line in lines:
        print("  " + line)
    if links_status and verdict.startswith("PASS"):
        verdict = "FAIL: the sound is balanced but the links are not as expected (see links)"
    print(f"\n  rig: {verdict}\n")
    save({"layer": "rig", "label": args.label, "verdict": verdict, "matrix_dbfs": matrix})
    return 0 if verdict.startswith("PASS") else 1


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
    s.add_argument("--label", default="")
    s.add_argument("--file", help=argparse.SUPPRESS)
    s.add_argument("--expect", type=int, default=1, help="how many rig inputs should be heard [1]")
    s.add_argument("--seconds", type=float, default=3.0, help="how long to listen [3]")
    s.add_argument("--tolerance", type=float, default=2.5,
                   help="left/right difference that still passes, dB [2.5; the bare loop is 1.3]")
    s.set_defaults(fn=cmd_rig)
    sub.add_parser("log", help="every run so far").set_defaults(fn=cmd_log)
    args = p.parse_args()
    if getattr(args, "level", -40) > -12:
        sys.exit("refusing a level above -12 dBFS")
    sys.exit(args.fn(args))


if __name__ == "__main__":
    main()
