#!/usr/bin/env bash

echo "Initiating rig shutdown..."

# killall matches a process's short comm name, but Sushi runs as an AppImage:
# the process actually doing the work is "sushi.bin", re-exec'd from a
# randomly-named mount (/tmp/.mount_sushiXXXXXX/usr/bin/sushi.bin) — not
# "sushi", which is just the wrapper. killall sushi never touched the real
# process, leaving it running and holding the JACK ports open. pkill -f
# matches the full command line instead, catching it regardless of the
# mount path.

# 1. Send a polite interrupt signal to cleanly unhook JACK ports
pkill -INT -f sushi.bin
pkill -INT -f "sushi-rig listen"
killall -INT fmit qpwgraph pw-jack 2>/dev/null

# 2. Give the audio backend a second to release the ports
sleep 1

# 3. Force kill any processes that hung or refused to close
pkill -9 -f sushi.bin
pkill -9 -f "sushi-rig listen"
killall -9 fmit qpwgraph pw-jack 2>/dev/null

echo "Rig offline."
