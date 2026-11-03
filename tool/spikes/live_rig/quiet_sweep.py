"""One tone per physical output, at a level you choose, written as a WAV.

`speaker-test` plays at close to full scale. With a monitor knob set for a broken,
inaudible output, that is painfully loud the moment the output starts working -- which
is exactly what happened on 2026-11-03. This asks for the level in dBFS instead, so a
test can be quiet on purpose.

Each output gets its own pitch, rising, so the channels can be told apart by ear rather
than only by their order:

    python3 quiet_sweep.py sweep.wav            # -40 dBFS, barely audible
    python3 quiet_sweep.py sweep.wav -25        # 15 dB louder
    aplay -D hw:1,0 sweep.wav                   # straight to the device, no PipeWire

Play it to `hw:<card>,0` to take PipeWire out of the path entirely: that is what proved
the iD14's silent outputs were the device's own doing and not the audio server's. See
RESULTS-id14-mixer-2026-11-03.md.
"""

import math
import struct
import sys
import wave

RATE = 48000
CHANNELS = 4
TONE_SECONDS = 1.0
GAP_SECONDS = 0.5
FADE_SECONDS = 0.02
FREQS = [220.0, 330.0, 440.0, 550.0]  # one per output, rising


def sweep(dbfs):
    """Interleaved frames: each channel's tone in turn, silence elsewhere."""
    amplitude = int(32767 * (10 ** (dbfs / 20.0)))
    tone_frames = int(RATE * TONE_SECONDS)
    fade_frames = RATE * FADE_SECONDS
    frames = bytearray()

    for channel in range(CHANNELS):
        for n in range(tone_frames):
            # raised edges, so nothing clicks into the monitors
            envelope = min(1.0, n / fade_frames, (tone_frames - n) / fade_frames)
            value = int(amplitude * envelope
                        * math.sin(2 * math.pi * FREQS[channel] * n / RATE))
            for c in range(CHANNELS):
                frames += struct.pack('<h', value if c == channel else 0)
        frames += bytes(int(RATE * GAP_SECONDS) * CHANNELS * 2)

    return amplitude, bytes(frames)


def main():
    if len(sys.argv) < 2:
        sys.exit(f"usage: {sys.argv[0]} <out.wav> [dbfs, default -40]")
    path = sys.argv[1]
    dbfs = float(sys.argv[2]) if len(sys.argv) > 2 else -40.0
    if dbfs > 0:
        sys.exit(f"dbfs must be at or below 0, got {dbfs}")

    amplitude, frames = sweep(dbfs)
    with wave.open(path, 'wb') as w:
        w.setnchannels(CHANNELS)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(frames)

    pitches = ", ".join(f"out {i + 1} {f:.0f}Hz" for i, f in enumerate(FREQS))
    print(f"{path}: {dbfs} dBFS (amplitude {amplitude}/32767), "
          f"{TONE_SECONDS}s per channel, {GAP_SECONDS}s gaps")
    print(f"order: {pitches}")


if __name__ == '__main__':
    main()
