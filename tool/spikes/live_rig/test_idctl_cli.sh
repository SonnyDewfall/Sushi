#!/usr/bin/env bash
# The parts of idctl that run without an iD14: the dry-run listings, compared
# with the committed ones, the signal path, which must stop the writes and
# still reach the teardown, and the argument checks. Run by
# `make -f idctl.mk check`.
set -u
cd "$(dirname "$0")" || exit 1
fail=0
work=$(mktemp -d)
pid=
# shellcheck disable=SC2317  # called by the trap
cleanup() { [ -n "$pid" ] && kill "$pid" 2>/dev/null; rm -rf "$work"; }
trap cleanup EXIT

for profile in daw mainmix; do
    if out=$(./idctl --dry-run apply "$profile" 2>/dev/null) \
        && diff -u "idctl-apply-$profile.expected" - <<<"$out"; then
        echo "ok   dry-run apply $profile matches idctl-apply-$profile.expected"
    else
        echo "FAIL dry-run apply $profile differs from idctl-apply-$profile.expected"
        fail=1
    fi
done

# The default profile is daw.
if [ "$(./idctl --dry-run apply 2>/dev/null)" = "$(./idctl --dry-run apply daw 2>/dev/null)" ]; then
    echo "ok   apply with no profile is daw"
else
    echo "FAIL apply with no profile is not daw"; fail=1
fi

# Wait up to $2 seconds for file $1 to contain a line matching $3.
wait_for() {
    local i
    for ((i = 0; i < $2 * 20; i++)); do
        grep -q -- "$3" "$1" 2>/dev/null && return 0
        sleep 0.05
    done
    return 1
}

# SIGTERM part-way through: the remaining writes are skipped, the exit status
# is 128 + 15. No fixed sleep: the signal goes only once idctl has said its
# handlers are in place and has listed its third write, and the 300 ms gap
# leaves about 18 s of writes still to go, so a slow machine cannot finish
# the list first. A dry run never opens the device, so this exercises the
# handler and the loop, not libusb.
./idctl --dry-run --gap-ms 300 apply mainmix >"$work/out" 2>"$work/err" &
pid=$!
if wait_for "$work/err" 20 '^idctl: signal handlers installed$' && wait_for "$work/out" 20 '^ 3/64 '; then
    kill -TERM "$pid"
fi
wait "$pid"
rc=$?
pid=
sent=$(grep -c '^ *[0-9]*/64 ' "$work/out")
if [ "$rc" -eq 143 ] && grep -q '^skipped the remaining .* signal$' "$work/out" \
    && grep -q '^interrupted by signal 15' "$work/out" && [ "$sent" -ge 3 ] && [ "$sent" -lt 64 ]; then
    echo "ok   SIGTERM after $sent of 64 writes: rest skipped, exit $rc"
else
    echo "FAIL SIGTERM: exit $rc, $sent writes listed"; sed 's/^/     /' "$work/out" "$work/err"; fail=1
fi

# Refused, exit 2, before any device is opened.
refuse() {
    ./idctl "$@" >"$work/out" 2>&1
    local rc=$?
    if [ "$rc" -eq 2 ] && ! grep -q '^opened\|^dry run' "$work/out"; then
        echo "ok   'idctl $*' refused, exit 2: $(head -1 "$work/out")"
    else
        echo "FAIL 'idctl $*' exit $rc, want 2"; sed 's/^/     /' "$work/out"; fail=1
    fi
}
refuse apply bogus
refuse --dry-run apply --gap-ms mainmix        # --gap-ms must not eat the profile
refuse --dry-run apply mainmix --gap-ms
refuse --dry-run apply --gap-ms 5x mainmix
refuse --dry-run apply --gap-ms -1
refuse --dry-run --gap-ms 50 route 0 2          # only apply uses a gap
refuse --dry-run route HP_L 2
refuse --dry-run route 0 2x
refuse --dry-run route 4 0
refuse --dry-run route 0 3
refuse --dry-run set 0x33 6 0 9 0
refuse --dry-run set 0x33 6 0 1 0x100            # does not fit in 1 byte
refuse --dry-run set 0x33 6 0 2 12abc
refuse --dry-run set 0x33 0x1g 0 1 0
refuse get 0x36 0x12 1x 2                        # the channel, once atoi'd
refuse get 0x36 0x12 0 0
refuse --dry-run get 0x36 0x12 0 2

# Accepted, with the bytes as given.
accept() {  # pattern, args...
    local want=$1 rc
    shift
    ./idctl "$@" >"$work/out" 2>/dev/null
    rc=$?
    if [ "$rc" -eq 0 ] && grep -q -- "$want" "$work/out"; then
        echo "ok   'idctl $*': $(grep -- "$want" "$work/out" | sed 's/^ *//')"
    else
        echo "FAIL 'idctl $*' exit $rc, want 0 and '$want'"; sed 's/^/     /' "$work/out"; fail=1
    fi
}
accept 'channel 0x02  bytes 00     route HP L <- DAW 1$' --dry-run route 0x2 2
accept 'entity 0x3c selector 0x01 channel 0x28  bytes 00 80  set$' --dry-run set 0x3c 1 0x28 2 0x8000
accept 'entity 0x29 selector 0x01 channel 0x00  bytes 44 ac 00 00  set$' --dry-run set 0x29 0x01 0 4 0xac44
accept '^64/64 ' --dry-run --gap-ms 0 apply mainmix

exit $fail
