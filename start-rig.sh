#!/usr/bin/env bash
cd "$HOME/Sushi" || exit 1

# 1. Point LV2 path to your local portable plugins directory
# Deliberately the ONLY entry: no /usr/lib/lv2 fallback. plugins/ now holds
# exactly the bundles this rig uses (everything else is in plugins/archive/,
# off the path), so a missing or renamed plugin fails loudly here instead of
# silently resolving against the system copy and hiding a portability break.
export LV2_PATH="$HOME/Sushi/plugins"

# 2. Kill any existing instances
killall -q fmit sushi

# 3. Launch Visual Tuner in the background
fmit &

# 4. Launch qpwgraph minimized with saved auto-connections. It can exit
# silently within ~1s of starting — no error printed anywhere — if it races
# fmit/Sushi for the PipeWire session at the same moment; without it nothing
# gets auto-connected, and Sushi runs with no audio in or out while looking
# otherwise fine. Check and warn rather than fail silently.
qpwgraph -a "$HOME/Sushi/Patchbay/rig.qpwgraph" -m &
QPWGRAPH_PID=$!
sleep 1.5
if ! kill -0 "$QPWGRAPH_PID" 2>/dev/null; then
    echo "WARNING: qpwgraph exited immediately — the patchbay is not connected," >&2
    echo "so you likely won't hear anything even though Sushi is running." >&2
    echo "Run manually: qpwgraph -a $HOME/Sushi/Patchbay/rig.qpwgraph" >&2
fi

# 5. Launch the OSC listener so the panel's save button can write a config
# without a terminal in the loop (issue #10). --rig must match whichever
# config is loaded below — config/src/electric_board.yaml describes
# config/electric_board.json's structure.
"$HOME/Sushi/tool/.venv/bin/sushi-rig" listen \
  --rig "$HOME/Sushi/config/src/electric_board.yaml" \
  --out-dir "$HOME/Sushi/config" \
  --archive-dir "$HOME/Sushi/config/archive" &

# 6. Launch SUSHI via PipeWire-JACK
pw-jack ./sushi -j -c config/electric_board.json
