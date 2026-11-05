#!/usr/bin/env bash
# id14-route-on-connect <usb device, e.g. 3-1.2.1>
#
# Run by id14-route@<device>.service when 85-id14-route.rules sees an Audient
# iD14 MKI arrive. Waits for snd-usb-audio to finish with the card, then runs
# `idctl apply daw` once, then checks the card's PCM devices were rebuilt.
# Installed as /usr/local/libexec/id14-route-on-connect. Everything it says goes
# to the journal, each line prefixed with the device; failures at error
# priority. It never retries: one failure, logged, and it stops.
#
# Settling: the claim detaches snd-usb-audio from interface 0, so claiming
# while the driver is still probing races its setup. So this waits until the
# card that belongs to this device (matched by bus/devnum in
# /proc/asound/card*/usbbus) lists its PCM devices and their /dev/snd nodes
# exist, then a further few seconds, so that WirePlumber has opened and probed
# it too. That makes the claim happen in the one situation that has been seen
# to work: MixiD's Connect, against a card PipeWire already had open.
#
# The card-return check does not trust a listing: the PCMs from before the
# claim look exactly like rebuilt ones. It records the inode of each
# /dev/snd/pcmC<N>D<d><p|c> node before the claim. A rebuilt PCM gets a new
# node, and devtmpfs never hands a new node its predecessor's inode number, so
# success needs the same PCMs with all-new inodes. The same inodes afterwards
# mean the PCMs never went away, and that is reported as a failure of its own.
# Nothing here has run on the rig yet.
set -u

k=${1:-}
IDCTL=${IDCTL:-/usr/local/bin/idctl}
PROFILE=${ID14_PROFILE:-daw}
SETTLE_TIMEOUT=${ID14_SETTLE_TIMEOUT:-30}   # s to wait for the PCMs to appear
SETTLE_EXTRA=${ID14_SETTLE_EXTRA:-3}        # s more, for WirePlumber's probe
RETURN_TIMEOUT=${ID14_RETURN_TIMEOUT:-15}   # s to wait for them after the teardown
GUARD=${ID14_GUARD:-15}                     # s in which the same enumeration is not run twice
STATE=${ID14_STATE_DIR:-/run/id14-route}
SYS=${ID14_SYSFS:-/sys/bus/usb/devices}
ASOUND=${ID14_ASOUND:-/proc/asound}
DEVSND=${ID14_DEVSND:-/dev/snd}

# <6> and <3> are journald's syslog priority prefixes: info and err.
log() { echo "<6>$k: $*"; }
err() { echo "<3>$k: $*"; }

case $k in
    ''|*/*|.*) echo "<3>usage: $0 <usb device, e.g. 3-1.2.1>"; exit 2 ;;
esac
dev=$SYS/$k

# Only daw runs on connect. mainmix is untested on the device and is for
# trying by hand with the monitors down; the unit's timeout is also sized for
# daw's 4 writes, not mainmix's 64.
if [ "$PROFILE" != daw ]; then
    err "ID14_PROFILE=$PROFILE refused: only daw is applied on connect. Run 'idctl apply $PROFILE' by hand."
    exit 2
fi

is_id14() {
    [ "$(cat "$1/idVendor" 2>/dev/null)" = 2708 ] && [ "$(cat "$1/idProduct" 2>/dev/null)" = 0002 ]
}

# This enumeration of the device, e.g. 003/005. The kernel gives a device a
# new devnum every time it enumerates, so a replug changes it; a retrigger of
# the same arrival does not.
enumeration() {
    local bus num
    bus=$(cat "$dev/busnum" 2>/dev/null) && num=$(cat "$dev/devnum" 2>/dev/null) || return 1
    printf '%03d/%03d' "$((10#$bus))" "$((10#$num))"
}

# The /proc/asound/cardN that belongs to this device. Looked up afresh each
# time: the teardown can change the card number.
card_dir() {
    local want f
    want=$(enumeration) || return 1
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

# The card's PCM device nodes with their inodes, e.g.
# "pcmC1D0c:812 pcmC1D0p:813", or nothing.
pcm_ids() {
    local c n f out=()
    c=$(card_dir) || return 0
    n=${c##*/card}
    for f in "$DEVSND"/pcmC"$n"D*[pc]; do
        [ -e "$f" ] && out+=("${f##*/}:$(stat -c %i "$f")")
    done
    echo "${out[*]}"
}

# True if no node in $2 has the same name and inode as one in $1.
all_new() {
    local id
    for id in $1; do
        case " $2 " in *" $id "*) return 1 ;; esac
    done
    return 0
}

now() { date +%s; }

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
# Keyed on the enumeration, so a real replug (a new devnum) always runs, however
# soon; only the same enumeration within GUARD seconds of the last run is
# refused. A loop that re-enumerated on every reset would get past this, and is
# what the unit's StartLimitBurst is for.
this=$(enumeration) || { err "cannot read busnum/devnum under $dev"; exit 1; }
stamp=$STATE/$k
if [ -e "$stamp" ] && [ "$(cat "$stamp")" = "$this" ]; then
    age=$(( $(now) - $(stat -c %Y "$stamp") ))
    if [ "$age" -lt "$GUARD" ]; then
        err "refusing: already ran ${age}s ago for this same connection ($this, guard ${GUARD}s)." \
            "If this repeats on its own, the apply is retriggering itself. Run '$IDCTL apply $PROFILE' by hand."
        exit 1
    fi
fi
mkdir -p "$STATE"
mark() { echo "$this" >"$stamp"; }

log "iD14 connected at $dev ($this); waiting up to ${SETTLE_TIMEOUT}s for snd-usb-audio's PCM devices"
t0=$(now)
while :; do
    before=$(pcms)
    before_ids=$(pcm_ids)
    [ -n "$before" ] && [ -n "$before_ids" ] && break
    if [ $(( $(now) - t0 )) -ge "$SETTLE_TIMEOUT" ]; then
        err "no PCM devices for this iD14 after ${SETTLE_TIMEOUT}s (card: $(card_dir || echo none);" \
            "PCMs [$before]; nodes [$before_ids]). snd-usb-audio has not bound, or something else" \
            "holds interface 0. Not claiming; routing NOT set."
        mark
        exit 1
    fi
    sleep 0.5
done
log "card $(card_dir) has PCMs [$before] after $(( $(now) - t0 ))s; waiting ${SETTLE_EXTRA}s more for PipeWire"
sleep "$SETTLE_EXTRA"
before=$(pcms)
before_ids=$(pcm_ids)
log "before the claim: nodes [$before_ids]"

log "running: $IDCTL apply $PROFILE"
"$IDCTL" apply "$PROFILE"
rc=$?
mark

# Wait for the same PCMs to be listed again, every one with a new node.
t0=$(now)
while :; do
    after=$(pcms)
    after_ids=$(pcm_ids)
    if [ "$after" = "$before" ] && [ -n "$after_ids" ] && all_new "$before_ids" "$after_ids"; then
        break
    fi
    if [ $(( $(now) - t0 )) -ge "$RETURN_TIMEOUT" ]; then
        if [ "$after_ids" = "$before_ids" ] && [ "$rc" -ne 0 ]; then
            err "idctl apply $PROFILE failed (exit $rc) and the card was never detached (the same" \
                "nodes [$after_ids]): routing NOT set, see idctl's lines above. Not retrying."
            exit 1
        fi
        if [ "$after_ids" = "$before_ids" ]; then
            err "the PCMs never went away: the same nodes [$after_ids] before and after the claim." \
                "Either the claim did not detach snd-usb-audio, or this check is wrong; either way the" \
                "teardown's rebuild was not seen. idctl exit $rc."
            exit 4
        fi
        err "the card did not come back within ${RETURN_TIMEOUT}s: PCMs were [$before], now [$after];" \
            "nodes were [$before_ids], now [$after_ids]." \
            "Recover with: echo -n '$k:1.0' | sudo tee /sys/bus/usb/drivers/snd-usb-audio/bind  (or replug)"
        exit 3
    fi
    sleep 0.25
done
log "card rebuilt: PCMs [$after], new nodes [$after_ids]"

if [ "$rc" -ne 0 ]; then
    err "idctl apply $PROFILE failed (exit $rc): routing NOT set, see idctl's lines above. Not retrying."
    exit 1
fi
log "routing applied: $PROFILE; card $(card_dir) back with PCMs [$after]"
