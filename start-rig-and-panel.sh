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

cd "$HOME/Sushi" || exit 1

CONFIG="config/acoustic_chorus.json"
RIG_YAML="config/src/acoustic_chorus.yaml"
PANEL_FILE="/tmp/sushi-rig-panel.json"
GRPC_HOST="127.0.0.1"
GRPC_PORT="51051"
OSC_SEND_PORT="24024"

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

# 4. Launch qpwgraph minimized with saved auto-connections
qpwgraph -a "$HOME/Sushi/Patchbay/rig.qpwgraph" -m &

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
