#!/bin/sh
# A private PipeWire 1.0.5 session standing in for the rig's audio, for the
# visualiser's audio spike (README.md). `session.sh start DIR` starts it in
# DIR (pids in DIR/*.pid), `session.sh stop DIR` stops what it started, by pid.
# Needs: apt install pipewire pipewire-pulse wireplumber pipewire-bin
# pipewire-jack pulseaudio-utils dbus; python3-numpy and JACK-Client.
set -e
cmd=$1; dir=$(realpath "$2"); here=$(dirname "$(realpath "$0")")
export XDG_RUNTIME_DIR=$dir/run DBUS_SESSION_BUS_ADDRESS=unix:path=$dir/run/bus
case $cmd in
start)
  mkdir -p "$dir/run" "$dir/site"; chmod 700 "$dir/run"
  dbus-daemon --session --address="$DBUS_SESSION_BUS_ADDRESS" --nofork --nopidfile >"$dir/dbus.log" 2>&1 & echo $! >"$dir/dbus.pid"
  sleep 0.5
  pipewire >"$dir/pipewire.log" 2>&1 & echo $! >"$dir/pipewire.pid"; sleep 1
  wireplumber >"$dir/wp.log" 2>&1 & echo $! >"$dir/wp.pid"
  pipewire-pulse >"$dir/pulse.log" 2>&1 & echo $! >"$dir/pulse.pid"; sleep 2
  # The interface, stood in: its 4-channel output and 10-channel input, named as on the rig but "Fake".
  pactl load-module module-null-sink sink_name=fake_id14_out channels=4 channel_map=front-left,front-right,rear-left,rear-right sink_properties=device.description=Fake-iD14-Analog-Surround-4.0 >/dev/null
  pw-cli create-node adapter '{ factory.name=support.null-audio-sink node.name=fake_id14_in node.description="Fake iD14 Multichannel" media.class=Audio/Source/Virtual audio.position=[AUX0 AUX1 AUX2 AUX3 AUX4 AUX5 AUX6 AUX7 AUX8 AUX9] object.linger=true }' >/dev/null
  sleep 1
  python3 -I "$here/mkwav.py" "$dir/test10.wav" 600
  pw-play -P '{ node.autoconnect = false node.name = test_signal }' --channel-map AUX0,AUX1,AUX2,AUX3,AUX4,AUX5,AUX6,AUX7,AUX8,AUX9 "$dir/test10.wav" >"$dir/play.log" 2>&1 & echo $! >"$dir/play.pid"
  sleep 1
  for i in 0 1; do pw-link test_signal:output_AUX$i fake_id14_in:input_AUX$i; done
  # Sushi, stood in: a JACK client at the rig's quantum, guitar to both outputs.
  PIPEWIRE_QUANTUM=256/48000 pw-jack python3 -I "$here/standin_sushi.py" >"$dir/sushi.log" 2>&1 & echo $! >"$dir/sushi.pid"
  cp "$here/site/"*.html "$dir/site/"
  cp "$here/../../src/sushi_rig/ui/static/vendor/butterchurn-2.6.7/butterchurn.min.js" "$dir/site/"
  (cd "$dir/site" && exec python3 -m http.server 8765 --bind 127.0.0.1) >"$dir/http.log" 2>&1 & echo $! >"$dir/http.pid"
  sleep 2; echo "up: XDG_RUNTIME_DIR=$XDG_RUNTIME_DIR; the pages are at http://localhost:8765/"
  ;;
stop)
  for n in http sushi play pulse wp pipewire dbus; do [ -f "$dir/$n.pid" ] && kill "$(cat "$dir/$n.pid")" 2>/dev/null || true; done
  ;;
esac
