#!/usr/bin/env bash

echo "Initiating rig shutdown..."

# 1. Send a polite interrupt signal to cleanly unhook JACK ports
killall -INT sushi pw-jack fmit qpwgraph 2>/dev/null

# 2. Give the audio backend a second to release the ports
sleep 1

# 3. Force kill any processes that hung or refused to close
killall -9 sushi pw-jack fmit qpwgraph 2>/dev/null

echo "Rig offline."
