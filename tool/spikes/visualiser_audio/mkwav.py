"""A 10-channel test file: AUX0 a plucked low E repeating (the guitar),
AUX1 a quiet 1 kHz sine (the vocal mic), the rest silent."""
import math, struct, sys, wave
rate, seconds, out = 48000, int(sys.argv[2]), sys.argv[1]
w = wave.open(out, "wb"); w.setnchannels(10); w.setsampwidth(2); w.setframerate(rate)
frames = bytearray()
for n in range(rate * seconds):
    t = (n % rate) / rate
    g = 0.6 * math.exp(-4 * t) * sum(math.sin(2 * math.pi * 82.4 * k * (n / rate)) / k for k in (1, 2, 3))
    v = 0.1 * math.sin(2 * math.pi * 1000 * n / rate)
    frames += struct.pack("<10h", int(g * 32767 / 1.9), int(v * 32767), *([0] * 8))
w.writeframes(bytes(frames)); w.close()
