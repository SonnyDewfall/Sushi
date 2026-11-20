"""The on-demand visualiser tap (#195, review of PR #200) against session.sh's
PipeWire, through the real Supervisor: a held rig (its 'sushi' a sleeper; the
session's stand-in Sushi has the ports), a tap asked for, linked and checked
for realtime, its lease run out, stopped by pid; again, released at once.
argv[1]: the session dir, with XDG_RUNTIME_DIR set to its run/."""
import array, asyncio, json, os, subprocess, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from sushi_rig import rig, supervisor as S, vistap
from sushi_rig.procs import threads_of

d = Path(sys.argv[1])
pwlog = d / "pipewire.log"


def sh(*a, timeout=60):
    return subprocess.run(list(a), capture_output=True, text=True, timeout=timeout).stdout


def too_slow():
    import re, collections
    found = collections.Counter()
    for line in pwlog.read_text(errors="replace").splitlines():
        m = re.search(r"\((\S+)-\d+\) client too slow", line)
        if m:
            found[m.group(1)] += 1
    return found


def top(n):
    out = sh("pw-top", "-b", "-n", str(n))
    rows = {}
    for b in out.split("S   ID  QUANT")[2:]:
        for line in b.splitlines()[1:]:
            f = line.split()
            if len(f) >= 10 and f[0] == "R":
                rows.setdefault(f[-1], []).append((f[5], int(f[8])))
    return rows


def window(label, seconds):
    s0, e0 = too_slow(), {k: v[-1][1] for k, v in top(2).items()}
    rows = top(seconds)
    s1, e1 = too_slow(), {k: v[-1][1] for k, v in top(2).items()}
    slow = {k: s1[k] - s0.get(k, 0) for k in s1 if s1[k] - s0.get(k, 0)}
    print(f"{label}: 'client too slow' by node {slow}; new ERR {({k: e1[k] - e0.get(k, 0) for k in e1})}", flush=True)
    for name in vistap.NODES:
        if name in rows:
            print(f"  {name} BUSY {[b for b, _ in rows[name]]}", flush=True)


config = d / "rig.json"
config.write_text(json.dumps({"tracks": [{"name": "master", "outputs": [{"engine_bus": 0, "track_bus": 0}]}]}))
launch = rig.Launch(
    root=d, config_name="live", config=config,
    children=[{"name": "sushi", "command": [sys.executable, "-c", "import time; time.sleep(900)"], "optional": False}],
    headless=True, amp_model=None, amp_values={}, tuner_requested=False, amp_requested=False,
    amp_model_requested=None, rig_source=None, machine_file=None, visualiser_tap_requested=True)
world = S.World(prepare=lambda *a, **k: launch, sushi_ready=lambda alive: True,
                generate_panel=lambda *a: (0, ""), link_problems=lambda *a: [], replay_amp=lambda v: None,
                log_dir=lambda: d / "logs", tap_lease=4.0, tap_linger=5.0)


async def main():
    sup = S.Supervisor(world)
    up = await sup.up("live", headless=True)
    print("up:", up.ok, up.stderr.strip(), flush=True)
    window("rig up, no tap, 15 s", 15)
    t = time.monotonic()
    answer = await sup.tap("page1")
    print(f"tap: {answer} in {time.monotonic() - t:.2f}s", flush=True)
    rep = sup.tap_report()
    print("report:", rep, flush=True)
    print("threads:", threads_of(rep["pid"]), flush=True)
    print("sources:", sh("pactl", "list", "short", "sources").strip().replace("\n", " | "), flush=True)
    print("links:", [l for l in sh("pw-link", "-l").splitlines() if "rig_output" in l], flush=True)
    window("tap linked, no capture, 15 s", 15)
    for i in range(3):  # renewing, as a page does, while a capture listens
        await sup.tap("page1")
        await asyncio.sleep(0.1)
    rec = subprocess.Popen(["parec", "-d", "sushi_rig_output", "--format=float32le", "--channels=2", "--rate=48000"],
                           stdout=subprocess.PIPE)
    await asyncio.sleep(2)
    rec.terminate()
    data, _ = rec.communicate()
    a = array.array("f")
    a.frombytes(data[: len(data) // 8 * 8])
    print(f"captured {len(a)} samples, peak {max(map(abs, a)) if a else None}", flush=True)
    pid = rep["pid"]
    print("no renewal now: lease 4 s, linger 5 s", flush=True)
    t = time.monotonic()
    while sup.tap_report()["stopped"] is None and time.monotonic() - t < 20:
        await asyncio.sleep(0.1)
    print(f"stopped after {time.monotonic() - t:.1f}s: {sup.tap_report()['stopped']}; crashed {sup.crashed}", flush=True)
    print("pid gone:", not Path(f"/proc/{pid}").exists() or open(f"/proc/{pid}/stat").read().split()[2] == "Z", flush=True)
    await asyncio.sleep(0.5)
    print("nodes left:", [l for l in sh("pw-cli", "ls", "Node").splitlines() if "rig_output" in l], flush=True)
    again = await sup.tap("page2")
    pid2 = sup.tap_report()["pid"]
    released = await sup.tap_release("page2", at_once=True)
    print("again:", again.ok, "pid", pid2, "released at once:", released, sup.tap_report()["stopped"], flush=True)
    await asyncio.sleep(0.5)
    print("nodes left:", [l for l in sh("pw-cli", "ls", "Node").splitlines() if "rig_output" in l], flush=True)
    down = await sup.down()
    print("down:", down.ok, down.stdout.strip().replace("\n", " "), flush=True)


asyncio.run(main())
