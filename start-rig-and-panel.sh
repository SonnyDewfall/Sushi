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
#   config-name defaults to "electric_board" and names config/<name>.json —
#   e.g. `./start-rig-and-panel.sh empty` loads config/empty.json instead
#   (the passthrough config, useful for checking the audio path alone).

# This script lives inside a git worktree/checkout of the rig, and must run
# from its OWN directory (not $HOME/Sushi) — otherwise "tool/.venv/bin/
# sushi-rig" below resolves to whichever tool package happens to be
# installed at $HOME/Sushi instead of the one that ships with this checkout,
# silently running old code with no error (confirmed the hard way: this
# produced a panel with no save bar and no listener, from the main
# checkout's pre-issue-10 sushi-rig, while this script quietly kept running).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR" || exit 1

CONFIG_NAME="${1:-electric_board}"
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
# electric_board. Falls back to electric_board.yaml for the canonical,
# hand-authored configs, which don't carry a _meta.source themselves.
RIG_YAML="$(tool/.venv/bin/python -c "
import json
with open('$CONFIG') as f:
    print(json.load(f).get('_meta', {}).get('source', 'config/src/electric_board.yaml'))
")"

# 1. Point LV2 path to your local portable plugins directory
# Deliberately the ONLY entry: no /usr/lib/lv2 fallback. plugins/ now holds
# exactly the bundles this rig uses (everything else is in plugins/archive/,
# off the path), so a missing or renamed plugin fails loudly here instead of
# silently resolving against the system copy and hiding a portability break.
export LV2_PATH="$HOME/Sushi/plugins"

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

# 4. Launch qpwgraph minimized with saved auto-connections.
# qpwgraph is SINGLE-INSTANCE. Launching a second one while another is
# already running makes the *new* one exit immediately and silently — no
# error, no crash, nothing in the journal. Without it the patchbay never
# auto-connects, so Sushi runs with no audio in or out while looking
# completely healthy.
#
# This was issue #14, and it looked for a long time like a random ~50% race
# against fmit or PipeWire. It was neither: the rate was simply how often a
# qpwgraph happened to already be running — a leftover from a previous run
# that had not finished exiting, or one the user had opened by hand. That is
# also why retrying never helped (the existing instance was still there) and
# why dropping fmit changed nothing.
#
# So: stop any existing instance and *wait for it to actually be gone*
# before starting ours. Waiting is the part that matters — a killed
# qpwgraph takes a moment to exit, and starting into that window loses the
# new instance to the same silent exit.
pkill -x qpwgraph 2>/dev/null
for _ in $(seq 1 20); do
    pgrep -x qpwgraph >/dev/null 2>&1 || break
    sleep 0.25
done
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
