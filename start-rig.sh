#!/usr/bin/env bash
cd "$HOME/Sushi" || exit 1

# 1. Kill any existing instances
killall -q fmit sushi

# 2. Launch Visual Tuner in the background
fmit &

# 3. Launch qpwgraph minimized with saved auto-connections
qpwgraph -a "$HOME/Sushi/config/rig.qpwgraph" -m &

# 4. Launch SUSHI via PipeWire-JACK
pw-jack ./sushi -j -c config/fx.json
