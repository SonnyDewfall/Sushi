#!/usr/bin/env bash
# Tests id14-route-on-connect.sh against a fake sysfs and /proc/asound, with a
# stand-in idctl that drops and restores the card's PCMs the way the real
# claim and teardown are expected to. This checks the script's own logic (the
# settle wait, its timeout, the loop guard, the card-return check, failing
# once); it says nothing about the real driver's timing, which only the rig
# can show. Run by `make -f idctl.mk check`.
set -u
here=$(cd "$(dirname "$0")" && pwd) || exit 1
script=$here/id14-route-on-connect.sh
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
fail=0

# A fresh fake world: one iD14 at 3-1.2.1, bus 3 device 5, and card 1.
setup() {
    rm -rf "$work/sys" "$work/asound" "$work/run" "$work/calls"
    mkdir -p "$work/sys/3-1.2.1" "$work/asound/card1" "$work/run"
    printf '2708\n' >"$work/sys/3-1.2.1/idVendor"
    printf '0002\n' >"$work/sys/3-1.2.1/idProduct"
    printf '3\n' >"$work/sys/3-1.2.1/busnum"
    printf '5\n' >"$work/sys/3-1.2.1/devnum"
    printf '003/005\n' >"$work/asound/card1/usbbus"
    : >"$work/calls"
}
pcms_on()  { mkdir -p "$work/asound/card1/pcm0p" "$work/asound/card1/pcm0c"; }

# The stand-in idctl. FAKE=ok|fail|lose.
cat >"$work/idctl" <<'EOF'
#!/usr/bin/env bash
echo "$*" >>"$WORK/calls"
rm -rf "$WORK/asound/card1/pcm0p" "$WORK/asound/card1/pcm0c"   # the claim
echo "fake idctl $*: ${FAKE}"
sleep 0.3
case $FAKE in
    ok)   (sleep 1; mkdir -p "$WORK/asound/card1/pcm0p" "$WORK/asound/card1/pcm0c") & exit 0 ;;
    fail) mkdir -p "$WORK/asound/card1/pcm0p" "$WORK/asound/card1/pcm0c"; exit 1 ;;
    lose) exit 0 ;;
esac
EOF
chmod +x "$work/idctl"

run() {
    WORK=$work IDCTL=$work/idctl ID14_SYSFS=$work/sys ID14_ASOUND=$work/asound \
        ID14_STATE_DIR=$work/run ID14_SETTLE_TIMEOUT=${SETTLE:-3} ID14_SETTLE_EXTRA=0 \
        ID14_RETURN_TIMEOUT=3 ID14_GUARD=15 "$script" "${DEV:-3-1.2.1}" >"$work/out" 2>&1
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
expect "card ready, apply ok, card returns" 0 $rc 1 '^<6>3-1.2.1: routing applied: daw; card .*/card1 back with PCMs \[pcm0c pcm0p\]'
grep -q '^apply daw$' "$work/calls" || { echo "FAIL idctl not called as 'apply daw'"; fail=1; }

# Run again at once: the guard refuses, idctl is not called a second time.
: >"$work/calls"; FAKE=ok run; rc=$?
expect "second run within the guard is refused" 1 $rc 0 '^<3>3-1.2.1: refusing: the last run'

setup; (sleep 1.5; pcms_on) & FAKE=ok run; rc=$?; wait
expect "PCMs appear late, the wait covers it" 0 $rc 1 'has PCMs \[pcm0c pcm0p\] after [12]s'

setup; SETTLE=2 FAKE=ok run; rc=$?
expect "PCMs never appear: no claim, fails once" 1 $rc 0 '^<3>3-1.2.1: no PCM devices for this iD14 after 2s'

setup; pcms_on; FAKE=fail run; rc=$?
expect "idctl fails: reported, not retried" 1 $rc 1 '^<3>3-1.2.1: idctl apply daw failed (exit 1)'

setup; pcms_on; FAKE=lose run; rc=$?
expect "card does not come back: says how to rebind" 3 $rc 1 "echo -n '3-1.2.1:1.0' | sudo tee /sys/bus/usb/drivers/snd-usb-audio/bind"

setup; pcms_on; cp -r "$work/sys/3-1.2.1" "$work/sys/3-2"; FAKE=ok run; rc=$?
expect "two iD14s: refuses" 1 $rc 0 '^<3>3-1.2.1: 2 iD14s connected'

setup; pcms_on; printf '0003\n' >"$work/sys/3-1.2.1/idProduct"; FAKE=ok run; rc=$?
expect "not an iD14 MKI: refuses" 1 $rc 0 '^<3>3-1.2.1: not an iD14 MKI'

setup; pcms_on; DEV=../etc FAKE=ok run; rc=$?
expect "a path for a device name: refuses" 2 $rc 0 '^<3>usage'

# Card number changes across the teardown: still found, by bus/devnum.
setup; pcms_on
cat >"$work/idctl" <<'EOF'
#!/usr/bin/env bash
echo "$*" >>"$WORK/calls"
mv "$WORK/asound/card1" "$WORK/asound/card2"
EOF
FAKE=ok run; rc=$?
expect "card renumbered after teardown: still found" 0 $rc 1 'card .*/card2 back with PCMs'

exit $fail
