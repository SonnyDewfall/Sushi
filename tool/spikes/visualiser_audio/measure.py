"""CPU of process trees over a window, by /proc, and pw-top's quantum and ERR."""
import json, os, subprocess, sys, time
from playwright.sync_api import sync_playwright
HZ = os.sysconf("SC_CLK_TCK")
def ticks(pid):
    try:
        f = open(f"/proc/{pid}/stat").read().rsplit(")", 1)[1].split()
        return int(f[11]) + int(f[12])
    except OSError:
        return 0
def tree(root):
    out, todo = [], [root]
    kids = {}
    for p in os.listdir("/proc"):
        if p.isdigit():
            try:
                ppid = int(open(f"/proc/{p}/stat").read().rsplit(")", 1)[1].split()[1]); kids.setdefault(ppid, []).append(int(p))
            except OSError:
                pass
    while todo:
        p = todo.pop(); out.append(p); todo += kids.get(p, [])
    return out
def cpu(groups, seconds):
    before = {n: sum(ticks(p) for p in ps) for n, ps in groups.items()}
    time.sleep(seconds)
    return {n: round(100 * (sum(ticks(p) for p in ps) - before[n]) / HZ / seconds, 1) for n, ps in groups.items()}
def pwtop():
    out = subprocess.run(["pw-top", "-b", "-n", "2"], capture_output=True, text=True, timeout=10).stdout.splitlines()
    half = out[len(out)//2:]
    return [l for l in half if l.startswith("R")]
def errs():
    return {l.split()[-1]: int(l.split()[8]) for l in pwtop() if l.split()[-1] in ("sushi", "fake_id14_in", "fake_id14_out")}
def window(groups, seconds, label):
    e0 = errs(); c = cpu(groups, seconds); e1 = errs()
    print(label, c, "new ERR:", {k: e1[k] - e0.get(k, 0) for k in e1}, flush=True)
pids = {n: int(open(f"{sys.argv[1]}/{n}.pid").read()) for n in ("pipewire", "pulse", "sushi", "wp")}
mode, url = sys.argv[2], sys.argv[3]
w, h = int(sys.argv[4]), int(sys.argv[5])
groups = {n: [p] for n, p in pids.items()}
window(groups, 30, "no browser (30 s):")
with sync_playwright() as pw:
    b = pw.chromium.launch(args=["--use-fake-ui-for-media-stream", "--autoplay-policy=no-user-gesture-required"] + sys.argv[6:])
    page = b.new_page(viewport={"width": w, "height": h})
    page.goto(url)
    browser_pid = [p for p in tree(os.getpid()) if "chrom" in open(f"/proc/{p}/cmdline").read()][0]
    groups["chromium"] = tree(browser_pid)
    print("webgl2:", page.evaluate("!!document.createElement('canvas').getContext('webgl2')"), page.evaluate("(() => { const g = document.createElement('canvas').getContext('webgl2'); if (!g) return null; const e = g.getExtension('WEBGL_debug_renderer_info'); return e ? g.getParameter(e.UNMASKED_RENDERER_WEBGL) : g.getParameter(g.RENDERER); })()"))
    if mode != "none":
        print(page.evaluate("([l, r, w, h]) => start(l, r, w, h)", ["Fake iD14 Multichannel", mode == "render", w, h]))
        time.sleep(2)
        groups["chromium"] = tree(browser_pid)
        f0 = page.evaluate("state.frames"); t0 = time.time()
        window(groups, 30, mode + " (30 s):")
        print("fps", round((page.evaluate("state.frames") - f0) / (time.time() - t0), 1))
        print("\n".join(pwtop()))
    b.close()
