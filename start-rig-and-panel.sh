#!/usr/bin/env bash
# Launch the audio rig AND the Open Stage Control configuration panel
# together — for modelling a sound, not just running one. Runs the same
# startup start-rig.sh does (tuner, patchbay, the save listener, Sushi),
# then waits for Sushi's gRPC to actually come up, generates a fresh panel
# from the live rig, and opens it. Ctrl+C stops everything together.
#
# For just running the rig with no tweaking UI, use start-rig.sh instead —
# this script exists because generating and opening the panel by hand needs
# Sushi to already be live, which means juggling two terminals otherwise.
#
# Usage: ./start-rig-and-panel.sh [config-name]
#   config-name defaults to "acoustic_chorus" and names config/<name>.json —
#   e.g. `./start-rig-and-panel.sh acoustic_reverb` loads
#   config/acoustic_reverb.json instead.

# This script lives inside a git worktree/checkout of the rig, and must run
# from its OWN directory (not $HOME/Sushi) — otherwise "tool/.venv/bin/
# sushi-rig" below resolves to whichever tool package happens to be
# installed at $HOME/Sushi instead of the one that ships with this checkout,
# silently running old code with no error (confirmed the hard way: this
# produced a panel with no save bar and no listener, from the main
# checkout's pre-issue-10 sushi-rig, while this script quietly kept running).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR" || exit 1

CONFIG_NAME="${1:-acoustic_chorus}"
CONFIG="config/${CONFIG_NAME}.json"
PANEL_FILE="/tmp/sushi-rig-panel.json"
GRPC_HOST="127.0.0.1"
GRPC_PORT="51051"
OSC_SEND_PORT="24024"

if [ ! -f "$CONFIG" ]; then
    echo "No such config: $CONFIG" >&2
    exit 1
fi

# A saved variant (via `sushi-rig save`/the panel's SAVE button) records the
# rig.yaml it was built from in its own _meta.source, since variants share
# their originating yaml rather than getting one of their own (see issue
# #10's design notes) — read that back so a save made *from* this config
# is attributed to the right source instead of always assuming
# acoustic_chorus. Falls back to acoustic_chorus.yaml for the canonical,
# hand-authored configs, which don't carry a _meta.source themselves.
RIG_YAML="$(tool/.venv/bin/python -c "
import json
with open('$CONFIG') as f:
    print(json.load(f).get('_meta', {}).get('source', 'config/src/acoustic_chorus.yaml'))
")"

# 1. Point LV2 path to your local portable plugins directory
export LV2_PATH="$HOME/Sushi/plugins:${LV2_PATH:-/usr/lib/lv2:/usr/local/lib/lv2}"

cleanup() {
    echo
    echo "Shutting down..."
    pkill -f "open-stage-control --load $PANEL_FILE" 2>/dev/null
    ./stop-rig.sh
}
trap cleanup EXIT INT TERM

# 2. Kill any existing instances (mirrors start-rig.sh)
killall -q fmit sushi 2>/dev/null
pkill -f "sushi-rig listen" 2>/dev/null
pkill -f "open-stage-control --load $PANEL_FILE" 2>/dev/null

# 3. Launch Visual Tuner in the background
fmit &

# 4. Launch qpwgraph minimized with saved auto-connections. Found this
# session while chasing a "no playback" report: qpwgraph can exit silently
# within ~1s of starting — no error printed anywhere — if it races another
# app (fmit, Sushi) for the PipeWire session at the same moment. Without it,
# nothing gets auto-connected and Sushi runs with no audio in or out, which
# looks identical to everything working. A retry didn't reliably help in
# testing, so this just checks and warns loudly instead of pretending to
# have fixed it — if you see the warning, run
# `qpwgraph -a Patchbay/rig.qpwgraph` by hand in another terminal.
qpwgraph -a "$SCRIPT_DIR/Patchbay/rig.qpwgraph" -m &
QPWGRAPH_PID=$!
sleep 1.5
if ! kill -0 "$QPWGRAPH_PID" 2>/dev/null; then
    echo "WARNING: qpwgraph exited immediately — the patchbay is not connected," >&2
    echo "so you likely won't hear anything even though Sushi is running." >&2
    echo "Run manually: qpwgraph -a $SCRIPT_DIR/Patchbay/rig.qpwgraph" >&2
fi

# 5. Launch the save listener (issue #10) so the panel's save button works
tool/.venv/bin/sushi-rig listen \
  --rig "$RIG_YAML" \
  --out-dir config \
  --archive-dir config/archive &

# 6. Launch SUSHI via PipeWire-JACK, backgrounded so this script can carry
# on to generate and open the panel once it's up
pw-jack ./sushi -j -c "$CONFIG" &

# 7. Wait for Sushi's gRPC to actually accept connections before asking
# sushi-rig for a panel — it needs a live instance to read current values
# from (see `sushi-rig panel --help`)
echo "Waiting for Sushi..."
ready=false
for _ in $(seq 1 30); do
    if (exec 3<>"/dev/tcp/$GRPC_HOST/$GRPC_PORT") 2>/dev/null; then
        exec 3>&-
        ready=true
        break
    fi
    sleep 0.5
done
if [ "$ready" != true ]; then
    echo "Sushi's gRPC never came up on $GRPC_HOST:$GRPC_PORT — check the rig actually started." >&2
    exit 1
fi

# 8. Generate a panel from the live rig and open it in Open Stage Control
tool/.venv/bin/sushi-rig panel "$CONFIG" --sushi ./sushi -o "$PANEL_FILE"
open-stage-control --load "$PANEL_FILE" --send "$GRPC_HOST:$OSC_SEND_PORT" &

echo
echo "Rig and panel running. Press Ctrl+C to stop everything."
wait
