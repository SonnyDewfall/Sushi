#!/usr/bin/env bash
# id14-route-on-connect <usb device, e.g. 3-1.2.1>
#
# Run by id14-route@<device>.service when 85-id14-route.rules sees an Audient
# iD14 MKI arrive. Waits for snd-usb-audio to finish with the card, then runs
# `idctl apply daw` once, then checks the card's PCM devices came back.
# Installed as /usr/local/libexec/id14-route-on-connect. Everything it says goes
# to the journal, each line prefixed with the device; failures at error
# priority. It never retries: one failure, logged, and it stops.
#
# Settling: the claim detaches snd-usb-audio from interface 0, so claiming
# while the driver is still probing races its setup. So this waits until the
# card that belongs to this device (matched by bus/devnum in
# /proc/asound/card*/usbbus) lists its PCM devices, then a further few
# seconds, so that WirePlumber has opened and probed it too. That makes the
# claim happen in the one situation that has been seen to work: MixiD's
# Connect, against a card PipeWire already had open. Nothing here has run on
# the rig yet.
set -u

k=${1:-}
IDCTL=${IDCTL:-/usr/local/bin/idctl}
PROFILE=${ID14_PROFILE:-daw}
SETTLE_TIMEOUT=${ID14_SETTLE_TIMEOUT:-30}   # s to wait for the PCMs to appear
SETTLE_EXTRA=${ID14_SETTLE_EXTRA:-3}        # s more, for WirePlumber's probe
RETURN_TIMEOUT=${ID14_RETURN_TIMEOUT:-15}   # s to wait for them after the teardown
GUARD=${ID14_GUARD:-15}                     # s after a run in which another is refused
STATE=${ID14_STATE_DIR:-/run/id14-route}
SYS=${ID14_SYSFS:-/sys/bus/usb/devices}
ASOUND=${ID14_ASOUND:-/proc/asound}

# <6> and <3> are journald's syslog priority prefixes: info and err.
log() { echo "<6>$k: $*"; }
err() { echo "<3>$k: $*"; }

case $k in
    ''|*/*|.*) echo "<3>usage: $0 <usb device, e.g. 3-1.2.1>"; exit 2 ;;
esac
dev=$SYS/$k

is_id14() {
    [ "$(cat "$1/idVendor" 2>/dev/null)" = 2708 ] && [ "$(cat "$1/idProduct" 2>/dev/null)" = 0002 ]
}

# The /proc/asound/cardN that belongs to this device. Looked up afresh each
# time: the teardown can change the card number.
card_dir() {
    local bus num want f
    bus=$(cat "$dev/busnum" 2>/dev/null) && num=$(cat "$dev/devnum" 2>/dev/null) || return 1
    printf -v want '%03d/%03d' "$((10#$bus))" "$((10#$num))"
    for f in "$ASOUND"/card*/usbbus; do
        if [ -r "$f" ] && [ "$(cat "$f")" = "$want" ]; then
            echo "${f%/usbbus}"
            return 0
        fi
    done
    return 1
}

# The card's PCM entries, e.g. "pcm0c pcm0p", or nothing.
pcms() {
    local c p out=()
    c=$(card_dir) || return 0
    for p in "$c"/pcm*[pc]; do
        [ -d "$p" ] && out+=("${p##*/}")
    done
    echo "${out[*]}"
}

# Wait up to $1 seconds for pcms() to satisfy test $2 ("any", or an exact list).
wait_pcms() {
    local limit=$1 want=$2 start now got
    start=$(date +%s)
    while :; do
        got=$(pcms)
        if { [ "$want" = any ] && [ -n "$got" ]; } || { [ "$want" != any ] && [ "$got" = "$want" ]; }; then
            echo "$got"
            return 0
        fi
        now=$(date +%s)
        [ $((now - start)) -ge "$limit" ] && { echo "$got"; return 1; }
        sleep 0.5
    done
}

if ! is_id14 "$dev"; then
    err "not an iD14 MKI (2708:0002) at $dev; nothing done"
    exit 1
fi

# idctl opens the first 2708:0002 it finds, which need not be this one.
n=0
for d in "$SYS"/*; do
    is_id14 "$d" && n=$((n + 1))
done
if [ "$n" -ne 1 ]; then
    err "$n iD14s connected; idctl cannot choose between them, so nothing done"
    exit 1
fi

# The loop guard. idctl's teardown resets the device. That is not expected to
# re-add it, but if it ever does, the rule would start this again, and again.
stamp=$STATE/$k
if [ -e "$stamp" ]; then
    age=$(( $(date +%s) - $(stat -c %Y "$stamp") ))
    if [ "$age" -lt "$GUARD" ]; then
        err "refusing: the last run for this device ended ${age}s ago (guard ${GUARD}s)." \
            "If this repeats on its own, the apply is retriggering itself. Run '$IDCTL apply $PROFILE' by hand."
        exit 1
    fi
fi
mkdir -p "$STATE"

log "iD14 connected at $dev; waiting up to ${SETTLE_TIMEOUT}s for snd-usb-audio's PCM devices"
t0=$(date +%s)
if ! before=$(wait_pcms "$SETTLE_TIMEOUT" any); then
    err "no PCM devices for this iD14 after ${SETTLE_TIMEOUT}s (card: $(card_dir || echo none))." \
        "snd-usb-audio has not bound, or something else holds interface 0. Not claiming; routing NOT set."
    touch "$stamp"
    exit 1
fi
log "card $(card_dir) has PCMs [$before] after $(( $(date +%s) - t0 ))s; waiting ${SETTLE_EXTRA}s more for PipeWire"
sleep "$SETTLE_EXTRA"

log "running: $IDCTL apply $PROFILE"
"$IDCTL" apply "$PROFILE"
rc=$?
touch "$stamp"

if ! after=$(wait_pcms "$RETURN_TIMEOUT" "$before"); then
    err "the card did not come back within ${RETURN_TIMEOUT}s: PCMs were [$before], now [$after]." \
        "Recover with: echo -n '$k:1.0' | sudo tee /sys/bus/usb/drivers/snd-usb-audio/bind  (or replug)"
    exit 3
fi
if [ "$rc" -ne 0 ]; then
    err "idctl apply $PROFILE failed (exit $rc): routing NOT set, see idctl's lines above." \
        "Not retrying. The card is back with PCMs [$after]."
    exit 1
fi
log "routing applied: $PROFILE; card $(card_dir) back with PCMs [$after]"
