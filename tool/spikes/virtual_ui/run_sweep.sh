#!/bin/bash
# Headless sweep of one LV2 plugin's own GUI: Xvfb + JACK(dummy) + Carla host
# + the sweep, all torn down on exit. Starts only what it starts, kills only that.
#
# Usage: run_sweep.sh PLUGIN_URI OUT_DIR [extra sweep.py args, e.g. --steps 25]
#
# Needs: Xvfb, jackd, lv2info, the system python3.12 with Carla's bindings
# (/usr/share/carla), and in whatever `python3` is on PATH: numpy, Pillow,
# scipy, python-osc, python-xlib.
set -u
URI=${1:?plugin URI}
OUT=${2:?output dir}
shift 2
HERE=$(cd "$(dirname "$0")" && pwd)
N=$((90 + RANDOM % 9))              # display number, and a private JACK server name
export DISPLAY=:$N
export JACK_DEFAULT_SERVER=sweep$N
export JACK_NO_AUDIO_RESERVATION=1  # do not fight a desktop for the audio device
TMP=$(mktemp -d)
PIDS=()
cleanup() { for p in "${PIDS[@]}"; do kill "$p" 2>/dev/null; done; wait 2>/dev/null; rm -rf "$TMP"; }
trap cleanup EXIT

# -noreset: atoms must outlive the last client. See prime_x.py.
Xvfb "$DISPLAY" -screen 0 1280x800x24 -noreset >"$TMP/xvfb.log" 2>&1 & PIDS+=($!)
jackd -n "$JACK_DEFAULT_SERVER" -d dummy -r 48000 -p 256 >"$TMP/jackd.log" 2>&1 & PIDS+=($!)

for _ in $(seq 50); do xdpyinfo >/dev/null 2>&1 && break; sleep 0.1; done
python3 "$HERE/prime_x.py" >/dev/null || { echo "prime_x failed"; exit 1; }
sleep 1  # let jackd come up; the host fails loudly if it did not

# stdin is /dev/null: the host copes with EOF on its optional stdin control.
# (An earlier version held stdin open with `sleep 3600` and leaked the sleep.)
/usr/bin/python3.12 "$HERE/carla_ui_host.py" "$URI" >"$TMP/host.log" 2>&1 </dev/null & PIDS+=($!)
for _ in $(seq 100); do grep -q -E '^(READY|FAIL)' "$TMP/host.log" && break; sleep 0.1; done
if ! grep -q '^READY' "$TMP/host.log"; then echo "host did not start:"; cat "$TMP/host.log" "$TMP/jackd.log"; exit 1; fi
sleep 1  # first paint

python3 "$HERE/sweep.py" "$URI" --out "$OUT" "$@"
