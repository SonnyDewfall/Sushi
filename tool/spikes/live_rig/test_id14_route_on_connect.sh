#!/usr/bin/env bash
# Tests id14-route-on-connect.sh against a fake sysfs, /proc/asound and
# /dev/snd, with a stand-in idctl that drops and rebuilds the card's PCMs the
# way the real claim and teardown are expected to. This checks the script's own
# logic (the settle wait and its timeout, the loop guard against real replugs,
# the card-return check against stale state, failing once); it says nothing
# about the real driver's timing, which only the rig can show. Run by
# `make -f idctl.mk check`.
set -u
here=$(cd "$(dirname "$0")" && pwd) || exit 1
script=$here/id14-route-on-connect.sh
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
fail=0

# A fresh fake world: one iD14 at 3-1.2.1, bus 3 device 5, card 1.
setup() {
    rm -rf "$work/sys" "$work/asound" "$work/devsnd" "$work/run" "$work/calls"
    mkdir -p "$work/sys/3-1.2.1" "$work/asound/card1" "$work/devsnd" "$work/run" "$work/grave"
    printf '2708\n' >"$work/sys/3-1.2.1/idVendor"
    printf '0002\n' >"$work/sys/3-1.2.1/idProduct"
    printf '3\n' >"$work/sys/3-1.2.1/busnum"
    printf '5\n' >"$work/sys/3-1.2.1/devnum"
    printf '003/005\n' >"$work/asound/card1/usbbus"
    : >"$work/calls"
}
pcms_on() {
    mkdir -p "$work/asound/card1/pcm0p" "$work/asound/card1/pcm0c"
    touch "$work/devsnd/pcmC1D0p" "$work/devsnd/pcmC1D0c"
}

# The stand-in idctl. FAKE says what the "device" does:
#   ok        PCMs go during the claim and come back with new nodes 1 s after exit
#   fast      ... and are already back, with new nodes, when idctl exits
#   stale     nothing happens to the PCMs, exit 0
#   prefail   nothing happens to the PCMs, exit 1 (the claim failed)
#   fail      PCMs rebuilt, exit 1 (a write failed)
#   lose      PCMs go and never come back
#   renumber  the card comes back as card 2
# Replaced nodes are moved to a grave rather than deleted, so their inode
# numbers stay taken and a new node cannot reuse one, as on devtmpfs.
cat >"$work/idctl" <<'EOF'
#!/usr/bin/env bash
echo "$*" >>"$WORK/calls"
echo "fake idctl $*: $FAKE"
c=$WORK/asound/card1
gone() {
    g=$(mktemp -d "$WORK/grave/XXXX")
    mv "$WORK"/devsnd/pcmC1D* "$g"/ 2>/dev/null
    rm -rf "$c/pcm0p" "$c/pcm0c"
}
back() {  # card number
    mkdir -p "$WORK/asound/card$1/pcm0p" "$WORK/asound/card$1/pcm0c"
    touch "$WORK/devsnd/pcmC$1D0p" "$WORK/devsnd/pcmC$1D0c"
}
case $FAKE in
    ok)       gone; (sleep 1; back 1) & exit 0 ;;
    fast)     gone; back 1; exit 0 ;;
    stale)    exit 0 ;;
    prefail)  echo "claim failed: LIBUSB_ERROR_BUSY"; exit 1 ;;
    fail)     gone; back 1; exit 1 ;;
    lose)     gone; exit 0 ;;
    renumber) gone; mv "$c" "$WORK/asound/card2"; back 2; exit 0 ;;
esac
EOF
chmod +x "$work/idctl"

run() {
    WORK=$work IDCTL=$work/idctl ID14_SYSFS=$work/sys ID14_ASOUND=$work/asound \
        ID14_DEVSND=$work/devsnd ID14_STATE_DIR=$work/run ID14_SETTLE_TIMEOUT=${SETTLE:-3} \
        ID14_SETTLE_EXTRA=0 ID14_RETURN_TIMEOUT=3 ID14_GUARD=15 ID14_PROFILE=${PROFILE:-daw} \
        "$script" "${DEV:-3-1.2.1}" >"$work/out" 2>&1
}

expect() {  # name, want-rc, got-rc, want-calls, grep pattern
    local calls
    calls=$(wc -l <"$work/calls")
    if [ "$3" -eq "$2" ] && [ "$calls" -eq "$4" ] && grep -q -- "$5" "$work/out"; then
        echo "ok   $1 (exit $3, idctl run $calls time(s))"
    else
        echo "FAIL $1: exit $3 want $2, idctl run $calls want $4, pattern '$5'"
        sed 's/^/     /' "$work/out"
        fail=1
    fi
}

setup; pcms_on; FAKE=ok run; rc=$?
expect "card ready, apply ok, card rebuilt" 0 $rc 1 '^<6>3-1.2.1: routing applied: daw; card .*/card1 back with PCMs \[pcm0c pcm0p\]'
grep -q '^apply daw$' "$work/calls" || { echo "FAIL idctl not called as 'apply daw'"; fail=1; }

# The loop guard, which must refuse a retrigger but never a replug.
: >"$work/calls"; FAKE=ok run; rc=$?
expect "same connection again at once: refused as a retrigger" 1 $rc 0 '^<3>3-1.2.1: refusing: already ran .* (003/005, guard 15s)'

: >"$work/calls"; printf '6\n' >"$work/sys/3-1.2.1/devnum"; printf '003/006\n' >"$work/asound/card1/usbbus"
FAKE=ok run; rc=$?
expect "a replug at once (new devnum): runs" 0 $rc 1 '^<6>3-1.2.1: routing applied: daw'

: >"$work/calls"; touch -d '-20 seconds' "$work/run/3-1.2.1"; FAKE=ok run; rc=$?
expect "same connection after the guard (a manual start): runs" 0 $rc 1 '^<6>3-1.2.1: routing applied: daw'

# The card-return check, which must not pass on stale state.
setup; pcms_on; FAKE=fast run; rc=$?
expect "PCMs already back when idctl exits, with new nodes: ok" 0 $rc 1 '^<6>3-1.2.1: card rebuilt: PCMs \[pcm0c pcm0p\]'

setup; pcms_on; FAKE=stale run; rc=$?
expect "PCMs never went away: reported as such, not success" 4 $rc 1 '^<3>3-1.2.1: the PCMs never went away'

setup; pcms_on; FAKE=lose run; rc=$?
expect "card does not come back: says how to rebind" 3 $rc 1 "echo -n '3-1.2.1:1.0' | sudo tee /sys/bus/usb/drivers/snd-usb-audio/bind"

setup; pcms_on; FAKE=renumber run; rc=$?
expect "card renumbered after teardown: still found" 0 $rc 1 'card .*/card2 back with PCMs'

setup; pcms_on; FAKE=fail run; rc=$?
expect "a write fails: reported, not retried" 1 $rc 1 '^<3>3-1.2.1: idctl apply daw failed (exit 1): routing NOT set'

setup; pcms_on; FAKE=prefail run; rc=$?
expect "the claim fails, card untouched: idctl's failure, not 'never went away'" 1 $rc 1 \
    '^<3>3-1.2.1: idctl apply daw failed (exit 1) and the card was never detached'

# Settling.
setup; (sleep 1.5; pcms_on) & FAKE=ok run; rc=$?; wait
expect "PCMs appear late, the wait covers it" 0 $rc 1 'has PCMs \[pcm0c pcm0p\] after [12]s'

setup; SETTLE=2 FAKE=ok run; rc=$?
expect "PCMs never appear: no claim, fails once" 1 $rc 0 '^<3>3-1.2.1: no PCM devices for this iD14 after 2s'

setup; mkdir -p "$work/asound/card1/pcm0p" "$work/asound/card1/pcm0c"; SETTLE=2 FAKE=ok run; rc=$?
expect "PCMs listed but no /dev/snd nodes: no claim" 1 $rc 0 '^<3>3-1.2.1: no PCM devices'

# Refusals.
setup; pcms_on; PROFILE=mainmix FAKE=ok run; rc=$?
expect "mainmix on connect: refused" 2 $rc 0 '^<3>3-1.2.1: ID14_PROFILE=mainmix refused'

setup; pcms_on; cp -r "$work/sys/3-1.2.1" "$work/sys/3-2"; FAKE=ok run; rc=$?
expect "two iD14s: refuses" 1 $rc 0 '^<3>3-1.2.1: 2 iD14s connected'

setup; pcms_on; printf '0003\n' >"$work/sys/3-1.2.1/idProduct"; FAKE=ok run; rc=$?
expect "not an iD14 MKI: refuses" 1 $rc 0 '^<3>3-1.2.1: not an iD14 MKI'

setup; pcms_on; DEV=../etc FAKE=ok run; rc=$?
expect "a path for a device name: refuses" 2 $rc 0 '^<3>usage'

exit $fail
