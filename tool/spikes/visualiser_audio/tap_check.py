"""The visualiser tap (#195) against session.sh's PipeWire, with the tool's own
code: start it as the rig does, link it, read what PipeWire and Chromium make
of it, time it with pw-top, and kill -9 it. Run with XDG_RUNTIME_DIR set to
the session's; argv[1] is the session directory (README.md, "The rig's output")."""
import json, os, re, subprocess, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from sushi_rig import rig, vistap
from sushi_rig.doctor import threads_of

d = Path(sys.argv[1])
def sh(*a):
    return subprocess.run(list(a), capture_output=True, text=True, timeout=60).stdout
def top(n):
    out = sh("pw-top", "-b", "-n", str(n))
    blocks = out.split("S   ID  QUANT")[2:]  # drop the zero first sample
    rows = {}
    for b in blocks:
        for line in b.splitlines()[1:]:
            f = line.split()
            if len(f) >= 10 and f[0] == "R":
                rows.setdefault(f[-1], []).append((f[5], int(f[8]), f[4]))
    return rows
def errs(rows):
    return {k: v[-1][1] for k, v in rows.items()}

print("== baseline 30 s")
b0 = errs(top(2)); time.sleep(30); b1 = errs(top(2))
print("new ERR:", {k: b1[k] - b0.get(k, 0) for k in b1})

config = d / "rig.json"
config.write_text(json.dumps({"tracks": [{"name": "master", "outputs": [{"engine_bus": 0, "track_bus": 0}]}]}))
child = vistap.child()
proc = rig.spawn_child(child["command"], cwd=d, env=rig.child_environment(dict(os.environ), child), log=d / "vistap.log")
print("tap pid", proc.pid)
t = time.monotonic(); problems = vistap.link(config); print("link:", problems, f"{time.monotonic() - t:.2f}s")
print("threads", threads_of(proc.pid))
print("sources:\n" + sh("pactl", "list", "short", "sources"))
print("default source:", sh("pactl", "get-default-source").strip())
desc = [l.strip() for l in sh("pactl", "list", "sources").splitlines() if l.strip().startswith(("Name:", "Description:"))]
print(desc)
links = sh("pw-link", "-l")
print("links touching the tap:\n" + "\n".join(l for l in links.splitlines() if "rig_output" in l or l.startswith("sushi")))
dump = json.loads(sh("pw-dump"))
nodes = {o["id"]: o["info"]["props"] for o in dump if o.get("type") == "PipeWire:Interface:Node"}
names = {i: p.get("node.name") for i, p in nodes.items()}
for o in dump:
    if o.get("type") == "PipeWire:Interface:Link":
        i = o["info"]
        a, b = names.get(i["output-node-id"]), names.get(i["input-node-id"])
        if "sushi_rig_output" in (a or "") + (b or ""):
            print("link node", a, "->", b)
print("== with the tap, 15 s")
r0 = errs(top(2)); rows = top(15); r1 = errs(top(2))
print("new ERR:", {k: r1[k] - r0.get(k, 0) for k in r1})
for name in ("sushi", *vistap.NODES):
    busy = [x[0] for x in rows.get(name, [])]
    print(name, "BUSY", busy[:30])
    print(name, "WAIT", [x[2] for x in rows.get(name, [])][:30])
print("== Chromium")
print(sh(sys.executable, str(Path(__file__).with_name("chromium_devices.py")),
         "http://localhost:8765/devices.html")[-3000:])
print("== kill -9 the tap by pid")
proc.kill(); proc.wait()
time.sleep(0.5)
print("nodes left:", [n for n in sh("pw-cli", "ls", "Node").splitlines() if "rig_output" in n])
print("sources:\n" + sh("pactl", "list", "short", "sources"))
