#!/usr/bin/env bash
# The parts of idctl that run without an iD14: the dry-run listings, compared
# with the committed ones, and the signal path, which must stop the writes and
# still reach the teardown. Run by `make -f idctl.mk check`.
set -u
cd "$(dirname "$0")" || exit 1
fail=0

for profile in daw mainmix; do
    if out=$(./idctl --dry-run apply "$profile") \
        && diff -u "idctl-apply-$profile.expected" - <<<"$out"; then
        echo "ok   dry-run apply $profile matches idctl-apply-$profile.expected"
    else
        echo "FAIL dry-run apply $profile differs from idctl-apply-$profile.expected"
        fail=1
    fi
done

# The default profile is daw.
if [ "$(./idctl --dry-run apply)" = "$(./idctl --dry-run apply daw)" ]; then
    echo "ok   apply with no profile is daw"
else
    echo "FAIL apply with no profile is not daw"; fail=1
fi

# SIGTERM part-way through: the remaining writes are skipped, the exit status
# is 128 + 15, and nothing after the signal is sent. A dry run never opens the
# device, so this exercises the handler and the loop, not libusb.
log=$(mktemp)
./idctl --dry-run --gap-ms 100 apply mainmix >"$log" 2>&1 &
pid=$!
sleep 0.55
kill -TERM "$pid"
wait "$pid"
rc=$?
sent=$(grep -c '^ *[0-9]*/64 ' "$log")
if [ "$rc" -eq 143 ] && grep -q '^skipped the remaining .* signal$' "$log" \
    && grep -q '^interrupted by signal 15' "$log" && [ "$sent" -gt 0 ] && [ "$sent" -lt 64 ]; then
    echo "ok   SIGTERM after $sent of 64 writes: rest skipped, exit $rc"
else
    echo "FAIL SIGTERM: exit $rc, $sent writes listed"; sed 's/^/     /' "$log"; fail=1
fi
rm -f "$log"

# Usage errors exit 2 and never touch a device.
for args in "apply bogus" "route 4 0" "route 0 3" "set 0x33 6 0 9 0" "--dry-run get 0x36 0x12 0 2"; do
    # shellcheck disable=SC2086
    ./idctl $args >/dev/null 2>&1
    rc=$?
    if [ "$rc" -eq 2 ]; then echo "ok   'idctl $args' refused, exit 2"
    else echo "FAIL 'idctl $args' exit $rc, want 2"; fail=1; fi
done

exit $fail
