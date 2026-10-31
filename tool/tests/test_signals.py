"""Test signals and WAV I/O.

Everything is checked against ground truth that does not come from the code
under test: the frequency a sweep should be passing through at a given instant,
the bytes a WAV header must contain, what a float round-trip must preserve.
Skipped entirely when the optional `audio` extra (numpy) is not installed.
"""

from __future__ import annotations

import struct
import subprocess
import sys
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")

import sushi_rig  # noqa: E402
from sushi_rig import signals  # noqa: E402

SR = 48000


def _zero_crossing_frequency(x, centre: int, span: int) -> float:
    """Frequency near sample `centre`, from the spacing of upward zero crossings."""
    seg = x[centre - span:centre + span]
    ups = np.nonzero((seg[:-1] < 0) & (seg[1:] >= 0))[0]
    return SR / np.mean(np.diff(ups))


# --------------------------------------------------------------------------- #
# Generators
# --------------------------------------------------------------------------- #

def test_everything_returned_is_two_dimensional_even_for_mono():
    for x in (signals.sine(SR, 0.1, 440), signals.log_sweep(SR, 0.1), signals.impulse(SR, 0.1),
              signals.silence(SR, 0.1), signals.pink_noise(SR, 0.1)):
        assert x.shape == (4800, 1)
        assert x.dtype == np.float64


def test_sine_has_the_requested_peak_level_frequency_and_channels():
    x = signals.sine(SR, 1.0, 1000, level_dbfs=-6.0, channels=2)
    assert x.shape == (SR, 2)
    assert np.max(np.abs(x)) == pytest.approx(10 ** (-6 / 20), rel=1e-6)
    np.testing.assert_array_equal(x[:, 0], x[:, 1])
    # 1 s of 1 kHz is exactly 1000 cycles: the FFT peak is bin 1000.
    assert np.argmax(np.abs(np.fft.rfft(x[:, 0]))) == 1000


def test_sine_above_nyquist_is_refused_rather_than_aliased():
    with pytest.raises(ValueError, match="Nyquist"):
        signals.sine(SR, 0.1, 30000)


def test_log_sweep_follows_the_exponential_frequency_law():
    f1, f2, seconds = 100.0, 10000.0, 4.0
    x = signals.log_sweep(SR, seconds, f1, f2, level_dbfs=0.0)[:, 0]
    # Ground truth: f(t) = f1 * (f2/f1)^(t/T), so each octave takes equal time.
    # Spot-check it at three instants, away from the very first/last cycles.
    for centre in (3000, len(x) // 2, len(x) - 600):
        expected = f1 * (f2 / f1) ** (centre / len(x))
        span = 300 if expected > 1000 else 1500
        assert _zero_crossing_frequency(x, centre, span) == pytest.approx(expected, rel=0.03)
    assert np.max(np.abs(x)) == pytest.approx(1.0, abs=1e-3)


def test_log_sweep_default_end_stays_below_nyquist_at_low_sample_rates():
    x = signals.log_sweep(16000, 0.5)  # default would be 20 kHz > Nyquist if not clamped
    assert np.all(np.isfinite(x))


def test_log_sweep_rejects_nonsense_ranges():
    with pytest.raises(ValueError):
        signals.log_sweep(SR, 1.0, f_start_hz=1000, f_end_hz=500)
    with pytest.raises(ValueError):
        signals.log_sweep(SR, 1.0, f_end_hz=SR / 2)


def test_impulse_is_one_sample_at_the_requested_position_and_level():
    x = signals.impulse(SR, 0.1, channels=2, at_sample=10)
    assert x[10].tolist() == [1.0, 1.0]
    assert np.count_nonzero(x) == 2
    assert signals.impulse(SR, 0.1, level_dbfs=-20.0)[0, 0] == pytest.approx(0.1)
    with pytest.raises(ValueError):
        signals.impulse(SR, 0.1, at_sample=10 ** 6)


def test_silence_is_exactly_zero():
    assert not signals.silence(SR, 0.1, channels=2).any()


def test_pink_noise_is_deterministic_for_a_seed_and_differs_between_seeds():
    a = signals.pink_noise(SR, 0.5, seed=7)
    np.testing.assert_array_equal(a, signals.pink_noise(SR, 0.5, seed=7))
    assert not np.array_equal(a, signals.pink_noise(SR, 0.5, seed=8))


def test_pink_noise_has_the_requested_peak_and_no_dc():
    x = signals.pink_noise(SR, 2.0, level_dbfs=-12.0, channels=2)
    assert np.max(np.abs(x), axis=0) == pytest.approx([10 ** (-12 / 20)] * 2, rel=1e-9)
    assert np.all(np.abs(x.mean(axis=0)) < 1e-9)


def test_pink_noise_channels_are_independent():
    x = signals.pink_noise(SR, 2.0, channels=2)
    assert abs(np.corrcoef(x[:, 0], x[:, 1])[0, 1]) < 0.1


def test_pink_noise_falls_three_db_per_octave_in_power_density():
    # Pink = equal energy per octave, which is -3 dB/octave per unit bandwidth.
    # Measured directly from the FFT so this does not lean on analysis.py.
    x = signals.pink_noise(SR, 8.0)[:, 0]
    power = np.abs(np.fft.rfft(x)) ** 2
    freqs = np.fft.rfftfreq(len(x), 1 / SR)
    octaves = [(100, 200), (200, 400), (400, 800), (800, 1600), (1600, 3200), (3200, 6400)]
    energy = [power[(freqs >= lo) & (freqs < hi)].sum() for lo, hi in octaves]
    db = 10 * np.log10(energy)
    assert np.ptp(db) < 0.3


@pytest.mark.parametrize("bad", [
    lambda: signals.sine(0, 1, 100),
    lambda: signals.sine(SR, 0, 100),
    lambda: signals.sine(SR, 1, 100, channels=0),
    lambda: signals.sine(SR, 1, 100, level_dbfs=float("nan")),
])
def test_generators_reject_invalid_arguments(bad):
    with pytest.raises(ValueError):
        bad()


# --------------------------------------------------------------------------- #
# WAV
# --------------------------------------------------------------------------- #

def _stereo_signal():
    return np.stack([signals.sine(SR, 0.2, 440, -3.0)[:, 0],
                     signals.pink_noise(SR, 0.2, -9.0)[:, 0]], axis=1)


@pytest.mark.parametrize("subtype,tolerance", [
    ("float32", 1e-7),                  # float32 holds ~24 bits of mantissa
    ("pcm24", 1 / 2 ** 23),             # within one LSB
    ("pcm16", 1 / 2 ** 15),
])
def test_wav_round_trips_each_format(tmp_path, subtype, tolerance):
    original = _stereo_signal()
    path = tmp_path / "t.wav"
    signals.write_wav(path, original, SR, subtype)
    back = signals.read_wav(path)
    assert back.subtype == subtype
    assert back.sample_rate == SR
    assert back.samples.shape == original.shape
    assert np.max(np.abs(back.samples - original)) <= tolerance


@pytest.mark.parametrize("subtype", signals.SUBTYPES)
def test_wav_round_trips_mono_and_odd_byte_counts(tmp_path, subtype):
    # Mono 24-bit with an odd frame count has an odd data size, which RIFF pads.
    original = signals.sine(SR, 0.01, 440)[:-1]  # 479 frames
    path = tmp_path / "m.wav"
    signals.write_wav(path, original[:, 0], SR, subtype)  # 1-D accepted as mono
    back = signals.read_wav(path)
    assert back.samples.shape == (479, 1)
    assert np.max(np.abs(back.samples - original)) < 1e-4


def test_pcm_extremes_survive_and_negative_24_bit_is_sign_extended(tmp_path):
    x = np.array([[0.0], [1.0], [-1.0], [-1 / 2 ** 23], [0.5]])
    for subtype in ("pcm16", "pcm24"):
        signals.write_wav(tmp_path / "e.wav", x, SR, subtype)
        back = signals.read_wav(tmp_path / "e.wav").samples
        # +1.0 has no code in two's complement and clips to one LSB below.
        assert back[1, 0] == pytest.approx(1.0, abs=1e-4)
        assert back[2, 0] == -1.0
        assert back[0, 0] == 0.0


def test_float_wav_keeps_nan_inf_and_overs_so_the_checks_can_see_them(tmp_path):
    x = np.array([[0.0], [np.nan], [np.inf], [-np.inf], [2.5]])
    signals.write_wav(tmp_path / "f.wav", x, SR, "float32")
    back = signals.read_wav(tmp_path / "f.wav").samples
    assert np.isnan(back[1, 0]) and back[2, 0] == np.inf and back[3, 0] == -np.inf
    assert back[4, 0] == 2.5


def test_integer_formats_refuse_what_they_would_have_to_clip_or_cannot_hold(tmp_path):
    path = tmp_path / "x.wav"
    with pytest.raises(ValueError, match="clip"):
        signals.write_wav(path, np.array([[1.5]]), SR, "pcm16")
    with pytest.raises(ValueError, match="NaN"):
        signals.write_wav(path, np.array([[np.nan]]), SR, "pcm24")
    assert not path.exists()  # refused before anything was written


def test_float_wav_header_is_what_other_readers_expect(tmp_path):
    # Format tag 3, an 18-byte fmt chunk, and a fact chunk: the layout the spec
    # requires for non-PCM and that other readers insist on.
    path = tmp_path / "h.wav"
    signals.write_wav(path, signals.silence(SR, 0.01, channels=2), SR, "float32")
    blob = path.read_bytes()
    assert blob[:4] == b"RIFF" and blob[8:12] == b"WAVE"
    assert struct.unpack_from("<I", blob, 4)[0] == len(blob) - 8
    assert blob[12:16] == b"fmt " and struct.unpack_from("<I", blob, 16)[0] == 18
    tag, channels, rate, byte_rate, align, bits = struct.unpack_from("<HHIIHH", blob, 20)
    assert (tag, channels, rate, byte_rate, align, bits) == (3, 2, SR, SR * 8, 8, 32)
    assert blob[38:42] == b"fact"


def test_pcm_header_is_stdlib_wave_compatible(tmp_path):
    # An independent reader for the formats it does support.
    import wave
    for subtype, width in (("pcm16", 2), ("pcm24", 3)):
        path = tmp_path / f"{subtype}.wav"
        signals.write_wav(path, _stereo_signal(), SR, subtype)
        with wave.open(str(path)) as w:
            assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (2, width, SR)
            assert w.getnframes() == int(0.2 * SR)


def test_reader_accepts_wave_format_extensible(tmp_path):
    # libsndfile writes this for >2 channels; the real tag is in the GUID.
    data = np.array([[0.25, -0.25, 0.5]], dtype="<f4")
    guid = struct.pack("<H", 3) + bytes.fromhex("000000001000800000aa00389b71")
    fmt = struct.pack("<HHIIHH", 0xFFFE, 3, SR, SR * 12, 12, 32) + struct.pack("<HHI", 22, 32, 7) + guid
    body = b"WAVE" + b"fmt " + struct.pack("<I", len(fmt)) + fmt + b"data" + struct.pack("<I", 12) + data.tobytes()
    path = tmp_path / "ext.wav"
    path.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)
    back = signals.read_wav(path)
    assert back.subtype == "float32"
    np.testing.assert_array_equal(back.samples, data.astype(np.float64))


def test_reader_skips_unknown_chunks(tmp_path):
    path = tmp_path / "l.wav"
    signals.write_wav(path, signals.sine(SR, 0.01, 440), SR, "pcm16")
    blob = path.read_bytes()
    junk = b"LIST" + struct.pack("<I", 3) + b"abc" + b"\x00"  # odd size + pad byte
    patched = blob[:12] + junk + blob[12:]
    patched = patched[:4] + struct.pack("<I", len(patched) - 8) + patched[8:]
    path.write_bytes(patched)
    assert signals.read_wav(path).samples.shape == (480, 1)


def test_reader_refuses_truncated_files_loudly(tmp_path):
    path = tmp_path / "t.wav"
    signals.write_wav(path, signals.sine(SR, 0.1, 440), SR, "float32")
    path.write_bytes(path.read_bytes()[:-100])
    with pytest.raises(ValueError, match="truncated"):
        signals.read_wav(path)


def test_reader_refuses_formats_it_does_not_support(tmp_path):
    path = tmp_path / "u.wav"
    fmt = struct.pack("<HHIIHH", 1, 1, SR, SR * 4, 4, 32)  # 32-bit integer PCM
    body = b"WAVE" + b"fmt " + struct.pack("<I", 16) + fmt + b"data" + struct.pack("<I", 4) + b"\0\0\0\0"
    path.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)
    with pytest.raises(ValueError, match="unsupported"):
        signals.read_wav(path)
    path.write_bytes(b"not a wav file at all")
    with pytest.raises(ValueError, match="RIFF"):
        signals.read_wav(path)


# --------------------------------------------------------------------------- #
# The optional dependency
# --------------------------------------------------------------------------- #

def test_importing_the_audio_modules_does_not_need_numpy():
    # The rest of the package must stay importable without the audio extra.
    src = str(Path(sushi_rig.__file__).parent.parent)
    code = "import sys; sys.modules['numpy'] = None; import sushi_rig.signals, sushi_rig.analysis"
    done = subprocess.run([sys.executable, "-c", code], env={"PYTHONPATH": src}, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr


def test_missing_numpy_gives_an_instruction_not_a_traceback(monkeypatch):
    monkeypatch.setitem(sys.modules, "numpy", None)
    with pytest.raises(SystemExit) as exc:
        signals.silence(SR, 0.1)
    assert "audio" in str(exc.value) and "pip install" in str(exc.value)



# --- wav_info: the header without the samples --------------------------------

@pytest.mark.parametrize("subtype", signals.SUBTYPES)
def test_wav_info_agrees_with_read_wav_on_every_subtype(tmp_path, subtype):
    """The two share the fmt parsing, so this guards the factoring as much as
    the new reader."""
    path = tmp_path / f"{subtype}.wav"
    signals.write_wav(path, signals.sine(SR, 0.3, 440.0, channels=2), SR, subtype=subtype)
    info, data = signals.wav_info(path), signals.read_wav(path)
    assert (info.sample_rate, info.subtype, info.channels, info.frames) == (
        data.sample_rate, data.subtype, data.samples.shape[1], data.samples.shape[0]
    )


def test_wav_info_refuses_a_truncated_file_as_read_wav_does(tmp_path):
    path = tmp_path / "cut.wav"
    signals.write_wav(path, signals.sine(SR, 0.3, 440.0), SR, subtype="pcm24")
    path.write_bytes(path.read_bytes()[:-500])
    for reader in (signals.wav_info, signals.read_wav):
        with pytest.raises(ValueError, match="truncated"):
            reader(path)


def test_wav_info_refuses_what_is_not_a_wav(tmp_path):
    path = tmp_path / "not.wav"
    path.write_bytes(b"OggS" + bytes(40))
    with pytest.raises(ValueError, match="not a RIFF/WAVE file"):
        signals.wav_info(path)


# --- WavWriter: a file written as it is recorded -----------------------------

def test_a_streamed_file_reads_back_exactly_and_is_valid_after_every_block(tmp_path):
    path = tmp_path / "s.wav"
    audio = signals.sine(SR, 0.5, 220.0, channels=2).astype("float32")
    with signals.WavWriter(path, 2, SR) as writer:
        for start in range(0, len(audio), 1000):
            writer.write(audio[start:start + 1000])
            assert signals.wav_info(path).frames == writer.frames, "a valid WAV at every point"
    back = signals.read_wav(path)
    assert back.subtype == "float32" and (back.samples == audio).all()


def test_a_streamed_file_keeps_what_is_over_full_scale():
    """Why float32 only: a recorder cannot stop mid-take to refuse a sample."""
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "hot.wav"
        with signals.WavWriter(path, 1, SR) as writer:
            writer.write([[1.5], [-2.0]])
        assert signals.read_wav(path).samples[:, 0].tolist() == [1.5, -2.0]


def test_a_block_of_the_wrong_width_is_refused(tmp_path):
    with signals.WavWriter(tmp_path / "s.wav", 2, SR) as writer:
        with pytest.raises(ValueError, match="takes 2 channel"):
            writer.write([[0.1, 0.2, 0.3]])


def test_a_file_that_would_pass_four_gibibytes_is_refused_not_wrapped(tmp_path):
    """A size field that rolls over makes a long take read as a few seconds."""
    with signals.WavWriter(tmp_path / "s.wav", 1, SR) as writer:
        writer.frames = (1 << 30) - 10  # as if nearly 4 GiB of mono float were in it
        with pytest.raises(ValueError, match="4 GiB"):
            writer.write([[0.0]] * 20)


def test_a_file_cut_off_between_its_data_and_its_header_is_repaired(tmp_path):
    """A process killed in that instant leaves a header claiming less than the
    file holds, and the audio past it reads as a chunk claiming a size the file
    cannot hold."""
    path = tmp_path / "s.wav"
    with signals.WavWriter(path, 2, SR) as writer:
        writer.write(signals.sine(SR, 0.1, 220.0, channels=2))
    with path.open("ab") as handle:  # a block's data, its sizes never written
        handle.write(signals.sine(SR, 0.01, 330.0, channels=2).astype("<f4").tobytes() + b"\x01")
    with pytest.raises(ValueError, match="truncated"):
        signals.wav_info(path)
    assert signals.repair_wav(path) == 4800 + 480, "every whole frame, the stray byte dropped"
    assert signals.wav_info(path).frames == 5280


def test_repair_refuses_a_file_it_did_not_write(tmp_path):
    path = tmp_path / "pcm.wav"
    signals.write_wav(path, signals.sine(SR, 0.1, 220.0), SR, subtype="pcm16")
    with pytest.raises(ValueError, match="not a file WavWriter wrote"):
        signals.repair_wav(path)



def test_a_new_stem_is_a_valid_empty_wav_before_its_first_block(tmp_path):
    """A recorder killed before its first block left zero-byte stems, which no
    repair can recover because nothing says what they were."""
    path = tmp_path / "s.wav"
    writer = signals.WavWriter(path, 2, SR)
    assert signals.wav_info(path).frames == 0
    writer.close()


def test_repair_leaves_a_finished_file_and_its_metadata_alone(tmp_path):
    """A RIFF size that matches the file means it was finished; a LIST chunk an
    editor added after its data is metadata, not audio to claim."""
    import struct

    path = tmp_path / "s.wav"
    with signals.WavWriter(path, 1, SR) as writer:
        writer.write([[0.25]] * 10)
    blob = path.read_bytes()
    info = b"LIST" + struct.pack("<I", 4) + b"INFO"
    path.write_bytes(blob[:4] + struct.pack("<I", len(blob) - 8 + len(info)) + blob[8:] + info)
    assert signals.repair_wav(path) == 10
    assert signals.wav_info(path).frames == 10


def test_a_streamed_file_can_be_cut_back_to_match_its_siblings(tmp_path):
    path = tmp_path / "s.wav"
    with signals.WavWriter(path, 1, SR) as writer:
        writer.write([[0.1]] * 100)
        writer.truncate(40)
    assert signals.wav_info(path).frames == 40
