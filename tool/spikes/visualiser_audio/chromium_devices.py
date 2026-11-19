"""What Chromium lists as audio inputs on PipeWire, and what each capture
gives: its settings, its channels' peaks, and the PulseAudio stream it opened."""
import json, os, subprocess, sys, time
from playwright.sync_api import sync_playwright
url = sys.argv[1]
with sync_playwright() as p:
    b = p.chromium.launch(args=["--use-fake-ui-for-media-stream", "--autoplay-policy=no-user-gesture-required"])
    page = b.new_page()
    page.goto(url)
    d = page.evaluate("listDevices()")
    print(json.dumps(d, indent=1))
    for dev in d["devices"]:
        if dev["id"] in ("default",):
            continue
        r = page.evaluate("([id]) => levels(id)", [dev["id"]])
        so = subprocess.run(["pactl", "list", "source-outputs"], capture_output=True, text=True).stdout
        keep = [l.strip() for l in so.splitlines() if any(k in l for k in ("Sample Specification", "Channel Map", "Source:", "latency", "node.latency", "Buffer"))]
        print(dev["label"], json.dumps(r), keep)
        page.evaluate("stopAll()")
        time.sleep(0.3)
    b.close()
