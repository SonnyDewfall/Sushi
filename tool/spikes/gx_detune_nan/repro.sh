#!/bin/bash
# gx_detune_ (guitarix-lv2 0.46.0) alone, an octave up, rendered again and
# again: how many renders are NaN, and how many differ from each other, as it
# is and with each of the two causes taken away: the reads one past the end of
# `fpb` and `expect`, and FFTW_MEASURE (see the comment above DETUNE_URI in
# tool/src/sushi_rig/render.py, and MANIFEST.md, 2026-11-03).
#
#   ./repro.sh [RENDERS]          default 100; needs gcc, liblilv-dev, lv2-dev,
#                                 python3 with numpy, and guitarix-lv2
#
# Everything is built and written in a temporary directory, removed at the end.
set -euo pipefail
renders="${1:-100}"
here="$(cd "$(dirname "$0")" && pwd)"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

gcc -O1 -o "$work/host" "$here/host.c" $(pkg-config --cflags --libs lilv-0)
gcc -O1 -shared -fPIC -o "$work/pad.so" "$here/pad_fpb_expect.c"
gcc -O1 -shared -fPIC -o "$work/estimate.so" "$here/fftw_estimate.c" -ldl

# A 5 s logarithmic sweep, 20 Hz to 20 kHz, at -20 dBFS, 48 kHz, mono float32.
python3 - "$work/sweep.f32" <<'PY'
import sys
import numpy as np
rate, seconds, f0, f1 = 48000, 5.0, 20.0, 20000.0
t = np.arange(int(rate * seconds)) / rate
k = seconds / np.log(f1 / f0)
sweep = 10 ** (-20 / 20) * np.sin(2 * np.pi * f0 * k * (np.exp(t / k) - 1))
sweep.astype(np.float32).tofile(sys.argv[1])
PY

uri='http://guitarix.sourceforge.net/plugins/gx_detune_#_detune_'
# DETUNE +12 semitones, the rest as the rig sets them (in the ports' units).
ports=(DETUNE=12 OCTAVE=0 COMPENSATE=0 LATENCY=0 WET=44 DRY=73
       LOW=0.86 MIDDLELOW=0.26 MIDDLETREBLE=0.2 TREBLE=0.88 BYPASS=1)

run() {  # [renders=N] [GX_PAST_END=x] run NAME PRELOAD
    mkdir -p "$work/$1"
    for i in $(seq 1 "$renders"); do
        LD_PRELOAD="$2" "$work/host" "$uri" "$work/sweep.f32" "$work/$1/$i.f32" 1 "${ports[@]}" \
            >/dev/null 2>&1
    done
    python3 - "$1" "$work/$1" <<'PY'
import glob, hashlib, sys
import numpy as np
outputs = [np.fromfile(f, dtype=np.float32) for f in glob.glob(sys.argv[2] + "/*.f32")]
nan = sorted(int(np.argmax(~np.isfinite(o))) for o in outputs if not np.isfinite(o).all())
distinct = len({hashlib.sha1(o.tobytes()).hexdigest() for o in outputs if np.isfinite(o).all()})
print(f"{sys.argv[1]:32s} {len(outputs):4d} renders  {len(nan):3d} NaN  {distinct:4d} distinct  "
      f"NaN from frames {nan} (mod 256: {sorted({f % 256 for f in nan})})")
PY
}

run "as it is" ""
run "fpb, expect padded with 0" "$work/pad.so"
run "FFTW_ESTIMATE" "$work/estimate.so"
run "padded with 0, FFTW_ESTIMATE" "$work/pad.so:$work/estimate.so"
# What lies past the end is what the output gets: NaN there is NaN from the
# first hop that writes the Nyquist bin, and a large finite value is NaN once
# the bin's running phase overflows float, later the smaller it is (1e35
# does not, within the five seconds).
renders=3 GX_PAST_END=nan run "padded with NaN" "$work/pad.so"
renders=3 GX_PAST_END=1e37 run "padded with 1e37" "$work/pad.so"
renders=3 GX_PAST_END=1e36 run "padded with 1e36" "$work/pad.so"
