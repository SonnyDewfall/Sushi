"""Deterministic test signals, and WAV files to carry them.

What this enables: render audio offline through the rig, then check it. The
checks that need this library are null tests (does v2 of a chain still null
against v1?), approved reference renders (does today's render still match the
one a human signed off?), and behaviour and safety checks (does the delay really
sit at 375 ms; does anything produce NaN or clip?). This module is the input
side — stimuli and file I/O. `analysis` is the measuring side.

What is NOT here: the renderer. Nothing in this module starts Sushi or knows how
to drive it, and Sushi's offline-mode command-line flags have not been verified
yet, so none are guessed at. Everything below is pure, runs with no audio
hardware, and is tested against synthetic ground truth.

Conventions, chosen once so the two modules agree:

- Audio is a float64 array shaped `(frames, channels)`, always 2-D, even for
  mono. A mono/stereo ambiguity in array shape is the sort of thing that makes
  an offline test pass for the wrong reason. (`write_wav` is lenient and takes a
  1-D array as mono; everything this module *returns* is 2-D.)
- 0 dBFS means amplitude 1.0. `level_dbfs` on every generator is the **peak**
  level, not RMS: one definition for all of them, and a generator can never be
  asked to clip by accident. (A full-scale sine is therefore -3.01 dBFS RMS.)

Why a hand-written WAV reader/writer rather than a library:

- The stdlib `wave` module cannot read IEEE-float WAVs (format tag 3) and the
  renderer will most likely write float, because float is the only format that
  can carry a NaN or an over-0 dBFS peak to the checks intact.
- `soundfile`/libsndfile would handle it, but it is a native dependency for what
  is a ~100-line, fully round-trip-testable format; numpy alone is enough.

The one place this is not a plain reimplementation of a spec: the reader refuses
a file whose chunk claims more bytes than the file holds, instead of quietly
reading what is there. A render that was cut short is exactly what an offline
test exists to catch.

Pink noise uses its own deterministic construction rather than numpy's random
distributions. numpy documents that the output of `Generator.standard_normal`
and friends may change between versions, and an approved-reference workflow
regenerates its stimulus on every run — a silent change in the stimulus would
look like a regression in the rig. The raw PCG64 bit stream *is* stable, so
noise is built from that with plain arithmetic.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from sushi_rig._numpy import require_numpy

if TYPE_CHECKING:
    import numpy as np

SUBTYPES = ("pcm16", "pcm24", "float32")

# WAVE_FORMAT_* tags (RFC 2361). EXTENSIBLE hides the real tag in a sub-format
# GUID; libsndfile and others write it for >2 channels or >16 bit.
_TAG_PCM = 1
_TAG_FLOAT = 3
_TAG_EXTENSIBLE = 0xFFFE

# (format tag, bits per sample) for each supported subtype.
_FORMATS = {"pcm16": (_TAG_PCM, 16), "pcm24": (_TAG_PCM, 24), "float32": (_TAG_FLOAT, 32)}


# --------------------------------------------------------------------------- #
# Generators
# --------------------------------------------------------------------------- #

def _frame_count(sample_rate: int, duration_s: float) -> int:
    if sample_rate <= 0:
        raise ValueError(f"sample_rate must be positive, got {sample_rate}")
    if not duration_s > 0:
        raise ValueError(f"duration_s must be positive, got {duration_s}")
    return max(1, round(sample_rate * duration_s))


def _amplitude(level_dbfs: float) -> float:
    if level_dbfs != level_dbfs or level_dbfs in (float("inf"), float("-inf")):
        raise ValueError(f"level_dbfs must be a finite number, got {level_dbfs}")
    return 10.0 ** (level_dbfs / 20.0)


def _check_channels(channels: int) -> None:
    if channels < 1:
        raise ValueError(f"channels must be at least 1, got {channels}")


def sine(sample_rate: int, duration_s: float, frequency_hz: float,
         level_dbfs: float = -6.0, channels: int = 1,
         phase_rad: float = 0.0) -> "np.ndarray":
    """A sine wave, the same on every channel."""
    np = require_numpy()
    n = _frame_count(sample_rate, duration_s)
    _check_channels(channels)
    if not 0 < frequency_hz < sample_rate / 2:
        # Above Nyquist the sine would silently alias to a different frequency.
        raise ValueError(
            f"frequency_hz must be between 0 and Nyquist ({sample_rate / 2} Hz), "
            f"got {frequency_hz}"
        )
    t = np.arange(n) / sample_rate
    wave = _amplitude(level_dbfs) * np.sin(2 * np.pi * frequency_hz * t + phase_rad)
    return np.repeat(wave[:, None], channels, axis=1)


def log_sweep(sample_rate: int, duration_s: float, f_start_hz: float = 20.0,
              f_end_hz: float | None = None, level_dbfs: float = -6.0,
              channels: int = 1) -> "np.ndarray":
    """An exponential (logarithmic) sine sweep, f_start to f_end over the duration.

    Exponential rather than linear because it spends equal time per octave,
    which is how both ears and guitar tones are distributed, and because it is
    the stimulus a sweep deconvolution needs later to separate harmonic
    distortion from the linear response.

    `f_end_hz` defaults to 20 kHz, or 0.45 * sample_rate if that is lower; a
    sweep that ends at Nyquist would alias on its last samples.
    """
    np = require_numpy()
    n = _frame_count(sample_rate, duration_s)
    _check_channels(channels)
    if f_end_hz is None:
        f_end_hz = min(20000.0, 0.45 * sample_rate)
    if not 0 < f_start_hz < f_end_hz < sample_rate / 2:
        raise ValueError(
            f"need 0 < f_start_hz < f_end_hz < Nyquist ({sample_rate / 2} Hz), "
            f"got {f_start_hz} and {f_end_hz}"
        )
    t = np.arange(n) / sample_rate
    # Farina's formulation: phase(t) = 2*pi*f1*T/ln(f2/f1) * (exp(t/T*ln(f2/f1)) - 1).
    # Use the actual length n/sample_rate as T so the sweep ends exactly on f_end.
    total = n / sample_rate
    rate = np.log(f_end_hz / f_start_hz)
    phase = 2 * np.pi * f_start_hz * total / rate * (np.exp(t / total * rate) - 1.0)
    wave = _amplitude(level_dbfs) * np.sin(phase)
    return np.repeat(wave[:, None], channels, axis=1)


def impulse(sample_rate: int, duration_s: float, level_dbfs: float = 0.0,
            channels: int = 1, at_sample: int = 0) -> "np.ndarray":
    """A single-sample impulse. Defaults to 0 dBFS, i.e. the unit impulse."""
    np = require_numpy()
    n = _frame_count(sample_rate, duration_s)
    _check_channels(channels)
    if not 0 <= at_sample < n:
        raise ValueError(f"at_sample must be within the signal (0..{n - 1}), got {at_sample}")
    out = np.zeros((n, channels))
    out[at_sample, :] = _amplitude(level_dbfs)
    return out


def silence(sample_rate: int, duration_s: float, channels: int = 1) -> "np.ndarray":
    """All zeros."""
    np = require_numpy()
    n = _frame_count(sample_rate, duration_s)
    _check_channels(channels)
    return np.zeros((n, channels))


def pink_noise(sample_rate: int, duration_s: float, level_dbfs: float = -6.0,
               channels: int = 1, seed: int = 0) -> "np.ndarray":
    """Pink (1/f power) noise, identical for a given seed on every machine.

    Built in the frequency domain: magnitude proportional to 1/sqrt(f), phase
    uniformly random from the PCG64 bit stream. That gives an octave-band
    spectrum that is flat to within the FFT's resolution — a better measuring
    stimulus than filtered white noise, whose spectrum wobbles by chance — and
    it is the construction that does not depend on any numpy distribution
    (see the module docstring). Time-domain statistics are close to Gaussian,
    which the CLT gives for free with this many bins.

    Channels are independent noise (successive draws from the same stream), so
    a stereo render is not accidentally mono-compatible. `level_dbfs` is the
    peak, per channel.
    """
    np = require_numpy()
    n = _frame_count(sample_rate, duration_s)
    _check_channels(channels)
    if n < 4:
        raise ValueError("pink noise needs at least 4 samples")
    bins = n // 2 + 1
    freqs = np.arange(bins) / n  # cycles per sample; only the ratio matters
    magnitude = np.zeros(bins)
    magnitude[1:] = 1.0 / np.sqrt(freqs[1:])  # DC stays 0: noise has no offset

    raw = np.random.PCG64(seed).random_raw(channels * bins).reshape(channels, bins)
    # Top 53 bits -> uniform [0, 1) by exact arithmetic, then to a phase.
    phase = 2 * np.pi * (raw >> np.uint64(11)).astype(np.float64) / float(1 << 53)
    spectrum = magnitude * np.exp(1j * phase)
    spectrum[:, 0] = 0.0
    if n % 2 == 0:
        # irfft ignores the imaginary part of the Nyquist bin; make it real so
        # the energy we asked for is the energy we get.
        spectrum[:, -1] = np.abs(spectrum[:, -1])
    noise = np.fft.irfft(spectrum, n=n, axis=1)
    peak = np.max(np.abs(noise), axis=1, keepdims=True)
    return np.ascontiguousarray((noise / peak * _amplitude(level_dbfs)).T)


# --------------------------------------------------------------------------- #
# WAV
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class WavData:
    """What a WAV file held. `samples` is float64, shape (frames, channels)."""
    samples: "np.ndarray"
    sample_rate: int
    subtype: str


def _as_frames_channels(samples) -> "np.ndarray":
    np = require_numpy()
    arr = np.asarray(samples, dtype=np.float64)
    if arr.ndim == 1:
        arr = arr[:, None]
    if arr.ndim != 2 or arr.shape[0] == 0 or arr.shape[1] == 0:
        raise ValueError(f"samples must be non-empty (frames,) or (frames, channels); got shape {arr.shape}")
    return arr


def _encode(arr: "np.ndarray", subtype: str) -> bytes:
    np = require_numpy()
    if subtype == "float32":
        # Written as-is, NaN and over-0 dBFS values included: carrying those to
        # the checks is the reason to use float at all.
        return arr.astype("<f4").tobytes()

    if not np.all(np.isfinite(arr)):
        raise ValueError(f"cannot write NaN/Inf as {subtype}; use float32 to keep them")
    if np.max(np.abs(arr)) > 1.0:
        # Integer formats would clip. Silent clipping would hide the very
        # overshoot that a safety check should report, so refuse instead.
        raise ValueError(
            f"samples exceed full scale and {subtype} would clip them; use float32"
        )
    bits = _FORMATS[subtype][1]
    full = float(1 << (bits - 1))
    quantised = np.clip(np.rint(arr * full), -full, full - 1).astype("<i4")
    if bits == 16:
        return quantised.astype("<i2").tobytes()
    # 24-bit: keep the three low-order bytes of each little-endian int32.
    return quantised.view(np.uint8).reshape(-1, 4)[:, :3].tobytes()


def write_wav(path: str | Path, samples, sample_rate: int, subtype: str = "float32") -> None:
    """Write a WAV file. `samples` is (frames, channels), or 1-D for mono.

    Float files get the 18-byte `fmt ` chunk and a `fact` chunk that the spec
    requires for non-PCM formats; some readers reject float WAVs without them.
    24-bit PCM is written with the plain PCM tag rather than WAVE_FORMAT_EXTENSIBLE,
    which every common reader accepts for up to two channels.
    """
    if subtype not in _FORMATS:
        raise ValueError(f"unsupported subtype {subtype!r}; choose from {SUBTYPES}")
    arr = _as_frames_channels(samples)
    frames, channels = arr.shape
    if sample_rate <= 0:
        raise ValueError(f"sample_rate must be positive, got {sample_rate}")
    tag, bits = _FORMATS[subtype]
    block_align = channels * bits // 8

    fmt = struct.pack("<HHIIHH", tag, channels, sample_rate, sample_rate * block_align, block_align, bits)
    chunks = b""
    if tag == _TAG_FLOAT:
        chunks += b"fmt " + struct.pack("<I", 18) + fmt + struct.pack("<H", 0)
        chunks += b"fact" + struct.pack("<II", 4, frames)
    else:
        chunks += b"fmt " + struct.pack("<I", 16) + fmt
    data = _encode(arr, subtype)
    chunks += b"data" + struct.pack("<I", len(data)) + data
    if len(data) % 2:
        chunks += b"\x00"  # RIFF chunks are word-aligned; the pad is not counted in the size
    if len(chunks) + 4 >= 1 << 32:
        raise ValueError("audio too long for a plain WAV (4 GiB limit; RF64 is not supported)")
    Path(path).write_bytes(b"RIFF" + struct.pack("<I", len(chunks) + 4) + b"WAVE" + chunks)


def read_wav(path: str | Path) -> WavData:
    """Read a PCM16, PCM24 or IEEE-float32 WAV into float64 (frames, channels).

    PCM is scaled so that full scale is +/-1.0 (divide by 2^(bits-1)), the exact
    inverse of what `write_wav` does. Anything else — 8-bit, 32-bit integer,
    float64, compressed, RF64, truncated — raises rather than guessing.
    """
    np = require_numpy()
    blob = Path(path).read_bytes()
    if len(blob) < 12 or blob[:4] != b"RIFF" or blob[8:12] != b"WAVE":
        what = blob[:4].decode("latin-1") if len(blob) >= 4 else "<empty>"
        raise ValueError(f"{path}: not a RIFF/WAVE file (starts with {what!r}; RF64 is not supported)")

    fmt = None
    data = None
    pos = 12
    while pos + 8 <= len(blob):
        chunk_id = blob[pos:pos + 4]
        (size,) = struct.unpack_from("<I", blob, pos + 4)
        body = pos + 8
        if body + size > len(blob):
            raise ValueError(
                f"{path}: chunk {chunk_id.decode('latin-1')!r} claims {size} bytes "
                f"but only {len(blob) - body} remain — the file is truncated"
            )
        if chunk_id == b"fmt ":
            fmt = blob[body:body + size]
        elif chunk_id == b"data":
            data = blob[body:body + size]
        pos = body + size + (size % 2)  # skip the word-alignment pad
    if fmt is None or data is None:
        raise ValueError(f"{path}: missing {'fmt' if fmt is None else 'data'} chunk")
    if len(fmt) < 16:
        raise ValueError(f"{path}: fmt chunk is {len(fmt)} bytes, need at least 16")

    tag, channels, sample_rate, _, block_align, bits = struct.unpack_from("<HHIIHH", fmt)
    if tag == _TAG_EXTENSIBLE:
        if len(fmt) < 40:
            raise ValueError(f"{path}: WAVE_FORMAT_EXTENSIBLE fmt chunk is too short")
        (tag,) = struct.unpack_from("<H", fmt, 24)  # first 2 bytes of the sub-format GUID
    subtype = next((name for name, f in _FORMATS.items() if f == (tag, bits)), None)
    if subtype is None:
        raise ValueError(
            f"{path}: unsupported WAV format (tag {tag}, {bits} bits); "
            f"supported: {', '.join(SUBTYPES)}"
        )
    if channels < 1 or block_align != channels * bits // 8:
        raise ValueError(f"{path}: inconsistent fmt chunk (channels={channels}, block_align={block_align})")
    if len(data) % block_align:
        raise ValueError(f"{path}: data is {len(data)} bytes, not a whole number of {block_align}-byte frames")

    if subtype == "float32":
        flat = np.frombuffer(data, dtype="<f4").astype(np.float64)
    elif subtype == "pcm16":
        flat = np.frombuffer(data, dtype="<i2").astype(np.float64) / 32768.0
    else:
        b = np.frombuffer(data, dtype=np.uint8).reshape(-1, 3).astype(np.int32)
        value = b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16)
        flat = ((value ^ 0x800000) - 0x800000).astype(np.float64) / 8388608.0  # sign-extend
    return WavData(flat.reshape(-1, channels), sample_rate, subtype)
