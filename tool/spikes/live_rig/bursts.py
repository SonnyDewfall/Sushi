"""Which playback channel reached an input, by the pitch that arrived.

quiet_sweep.py gives each output its own frequency, so a burst's pitch names the
channel that produced it -- no reliance on timing between the player and recorder.
"""
import sys, wave, struct, math

FREQS = {220.0: 'out 1', 330.0: 'out 2', 440.0: 'out 3', 550.0: 'out 4'}
BLOCK = 0.05


def goertzel(xs, freq, rate):
    w = 2 * math.cos(2 * math.pi * freq / rate)
    s1 = s2 = 0.0
    for x in xs:
        s0 = x + w * s1 - s2
        s2, s1 = s1, s0
    return math.sqrt(max(s1 * s1 + s2 * s2 - w * s1 * s2, 0.0)) / max(len(xs), 1)


path, chan = sys.argv[1], int(sys.argv[2]) - 1
w = wave.open(path, 'rb')
nch, rate, width = w.getnchannels(), w.getframerate(), w.getsampwidth()
raw = w.readframes(w.getnframes())
code, full = ('<%dh' % (len(raw)//2), 32768.0) if width == 2 else ('<%di' % (len(raw)//4), 2147483648.0)
xs = struct.unpack(code, raw)[chan::nch]

db = lambda v: 20 * math.log10(v / full) if v > 0 else -999.0
bs = int(rate * BLOCK)
blocks = [math.sqrt(sum(float(v)*v for v in xs[i:i+bs]) / bs) for i in range(0, len(xs) - bs, bs)]
if not blocks:
    sys.exit("recording too short")
floor = sorted(blocks)[len(blocks)//4] * 4 + full * 1e-6

print(f"input {chan+1}: {len(xs)/rate:.1f}s, block floor {db(floor):.1f} dBFS")
runs, cur = [], None
for i, b in enumerate(blocks):
    if b > floor and cur is None:
        cur = i
    elif b <= floor and cur is not None:
        runs.append((cur, i)); cur = None
if cur is not None:
    runs.append((cur, len(blocks)))

if not runs:
    print("  no bursts above the floor -- nothing came through the loop")
for a, b in runs:
    if (b - a) * BLOCK < 0.15:
        continue
    seg = xs[a*bs:b*bs]
    rms = math.sqrt(sum(float(v)*v for v in seg) / len(seg))
    best = max(FREQS, key=lambda f: goertzel(seg, f, rate))
    print(f"  {a*BLOCK:5.2f}-{b*BLOCK:5.2f}s  rms {db(rms):7.1f} dBFS  "
          f"pitch {best:.0f}Hz -> {FREQS[best]}")
