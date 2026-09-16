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

# 4. Launch qpwgraph minimized with saved auto-connections.
#
# qpwgraph is SINGLE-INSTANCE. Launching a second one while another is
# already running makes the *new* one exit immediately and silently — no
# error, no crash, nothing in the journal. Without it the patchbay never
# auto-connects, so Sushi runs with no audio in or out while looking
# completely healthy.
#
# This was issue #14, and it looked for a long time like a random ~50% race
# against fmit or PipeWire. It was neither: the rate was simply how often a
# qpwgraph happened to already be running — a leftover from a previous run
# that had not finished exiting, or one opened by hand. That is also why
# retrying never helped (the existing instance was still there) and why
# dropping fmit changed nothing.
#
# So: stop any existing instance and *wait for it to actually be gone*
# before starting ours. The waiting is the part that matters — a killed
# qpwgraph takes a moment to exit, and starting into that window loses the
# new instance to the same silent exit.
pkill -x qpwgraph 2>/dev/null
for _ in $(seq 1 20); do
    pgrep -x qpwgraph >/dev/null 2>&1 || break
    sleep 0.25
done

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
