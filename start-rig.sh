#!/usr/bin/env bash
cd "$HOME/Sushi" || exit 1

# 1. Point LV2 path to your local portable plugins directory
export LV2_PATH="$HOME/Sushi/plugins:${LV2_PATH:-/usr/lib/lv2:/usr/local/lib/lv2}"

# 2. Kill any existing instances
killall -q fmit sushi

# 3. Launch Visual Tuner in the background
fmit &

# 4. Launch qpwgraph minimized with saved auto-connections
qpwgraph -a "$HOME/Sushi/Patchbay/rig.qpwgraph" -m &

# 5. Launch SUSHI via PipeWire-JACK
pw-jack ./sushi -j -c config/acoustic_chorus.json
