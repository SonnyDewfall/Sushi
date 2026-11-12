"""Find the loopback round trip by correlating the capture against the chirp.

`rtlat.c` emits a chirp at a known absolute output frame and records the input.
Because playback and capture are snd_pcm_link'd, output frame N and input frame
N are simultaneous, so the correlation peak minus emit_at is the round trip --
*provided* rtlat reported trigger_skew_us of zero. A non-zero skew is a
constant offset and has to be subtracted; pass it with --skew-frames.

Reported with the peak-to-background ratio, because a weak peak means the
number should not be trusted. An earlier threshold-based detector returned a
negative latency, which is what prompted doing this properly.

    python3 -I rtlat_analyse.py <prefix> <emit_at> <rate> [channels] [format]
                                [--skew-frames N]
"""
import sys
import numpy as np

DTYPES = {"S16_LE": "<i2", "S32_LE": "<i4"}


def round_trip(prefix, channels, dtype):
    cap = np.frombuffer(open(f"{prefix}.cap", "rb").read(), dtype)
    cap = cap[: len(cap) // channels * channels].reshape(-1, channels)
    cap = cap.astype(np.float64)
    ref = np.frombuffer(open(f"{prefix}.ref", "rb").read(), "<i2").astype(np.float64)

    out = []
    for ch in range(min(channels, 2)):
        sig = cap[:, ch]
        n = 1 << int(np.ceil(np.log2(len(sig) + len(ref))))
        corr = np.fft.irfft(np.fft.rfft(sig, n) * np.conj(np.fft.rfft(ref, n)), n)
        mag = np.abs(corr[: len(sig)])
        peak = int(np.argmax(mag))
        # background: everything more than one chirp away from the peak
        mask = np.ones(len(mag), bool)
        mask[max(0, peak - len(ref)):peak + len(ref)] = False
        ratio = mag[peak] / max(np.median(mag[mask]), 1e-9)
        out.append((peak, ratio))
    return out


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    skew = 0.0
    for a in sys.argv[1:]:
        if a.startswith("--skew-frames="):
            skew = float(a.split("=", 1)[1])

    prefix, emit_at, rate = args[0], int(args[1]), int(args[2])
    channels = int(args[3]) if len(args) > 3 else 2
    fmt = args[4] if len(args) > 4 else "S16_LE"
    if fmt not in DTYPES:
        sys.exit(f"format must be one of {', '.join(DTYPES)}, got {fmt}")

    for ch, (peak, ratio) in zip("LR", round_trip(prefix, channels, DTYPES[fmt])):
        shift = peak - emit_at - skew
        ms = 1000.0 * shift / rate
        verdict = "ok" if ratio > 20 else "WEAK -- do not trust"
        note = "" if not skew else f" (skew {skew:+.1f} frames removed)"
        print(f"  {ch}: round trip {shift:9.1f} frames = {ms:7.2f} ms   "
              f"peak/background {ratio:8.1f}  {verdict}{note}")


if __name__ == "__main__":
    main()
