"""What option (b) would make the agent do: read the tap's PCM and turn it
into butterchurn's time-domain bytes 60 times a second (no network)."""
import sys, numpy as np
block = 800 * 2  # 1/60 s of 48 kHz s16 mono
n = 0
while True:
    data = sys.stdin.buffer.read(block)
    if not data:
        break
    x = np.frombuffer(data, dtype="<i2").astype(np.float32) / 32768
    frame = np.clip(128 + 127 * np.resize(x, 1024), 0, 255).astype(np.uint8).tobytes()
    spec = np.abs(np.fft.rfft(np.resize(x, 1024)))
    n += 1
