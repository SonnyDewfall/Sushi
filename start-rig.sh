#!/usr/bin/env bash
cd "$HOME/Sushi" || exit 1

# 1. Point LV2 path to your local portable plugins directory
export LV2_PATH="$HOME/Sushi/plugins:${LV2_PATH:-/usr/lib/lv2:/usr/local/lib/lv2}"

# 2. Kill any existing instances
killall -q sushi

# 3. Launch qpwgraph minimized with saved auto-connections. It can exit
# silently within ~1s of starting — no error printed anywhere — if it races
# another app for the PipeWire session at the same moment; without it
# nothing gets auto-connected, and Sushi runs with no audio in or out while
# looking otherwise fine. fmit (the tuner) was dropped from this script
# entirely on the theory that it's the other side of that race — tune
# manually instead for now. Still checks and warns rather than fail
# silently, in case the race has another cause.
qpwgraph -a "$HOME/Sushi/Patchbay/rig.qpwgraph" -m &
QPWGRAPH_PID=$!
sleep 1.5
if ! kill -0 "$QPWGRAPH_PID" 2>/dev/null; then
    echo "WARNING: qpwgraph exited immediately — the patchbay is not connected," >&2
    echo "so you likely won't hear anything even though Sushi is running." >&2
    echo "Run manually: qpwgraph -a $HOME/Sushi/Patchbay/rig.qpwgraph" >&2
fi

# 4. Launch the OSC listener so the panel's save button can write a config
# without a terminal in the loop (issue #10). --rig must match whichever
# config is loaded below — config/src/acoustic_chorus.yaml describes
# config/acoustic_chorus.json's structure.
"$HOME/Sushi/tool/.venv/bin/sushi-rig" listen \
  --rig "$HOME/Sushi/config/src/acoustic_chorus.yaml" \
  --out-dir "$HOME/Sushi/config" \
  --archive-dir "$HOME/Sushi/config/archive" &

# 5. Launch SUSHI via PipeWire-JACK
pw-jack ./sushi -j -c config/acoustic_chorus.json
