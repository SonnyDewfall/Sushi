"""The listener's ALSA half, against the installed alsa-midi and libasound.

What this proves, and what it does not. There is no ALSA sequencer in CI
(no /dev/snd/seq), so nothing here opens a client, lists a port or hears a
pedal; whether `AlsaPort` finds and reads the MPD218 is unverified until the
rig session. What it does prove:

- every alsa-midi name `AlsaPort` calls exists in the installed version, so a
  rename surfaces here rather than on stage;
- the bytes libasound's own event decoder gives for each event the MPD218
  was seen to send (with running status off, as alsa-midi sets it) are what
  `decode` reads: the same note, channel and value, and aftertouch dropped.
  That is libasound itself, not a hand-written byte string.

Its own module because it needs the `midi` extra, and `importorskip` in the
middle of test_midi_listener.py would hide the tests that need nothing. CI
installs it (ci.yml, ci/Dockerfile), so there it runs rather than skips.
"""

from __future__ import annotations

import pytest

# Not importorskip: alsa-midi's compiled module raises ImportError when
# libasound.so.2 is missing, but an OSError is possible from cffi as well, and
# either would otherwise be a collection error that fails the whole run.
try:
    import alsa_midi
except (ImportError, OSError) as exc:
    pytest.skip(f"needs the midi extra and libasound ({exc})", allow_module_level=True)

from sushi_rig import midi_listener  # noqa: E402
from sushi_rig.midi_listener import Message, decode  # noqa: E402


@pytest.mark.parametrize("name", midi_listener.ALSA_NAMES)
def test_the_names_the_port_calls_exist(name):
    owner, _, attribute = name.partition(".")
    assert hasattr(alsa_midi, owner), owner
    if attribute:
        assert hasattr(getattr(alsa_midi, owner), attribute), name


def alsa_bytes(event) -> bytes:
    """What `SequencerClient.event_input(prefer_bytes=True)` hands the port
    for `event`: libasound's `snd_midi_event_decode`, with running status off
    as alsa-midi's own parser has it. Reaches into alsa-midi's cffi binding
    (`_to_alsa`, `alsa`, `ffi`) because there is no sequencer here to send
    the event through."""
    ffi, alsa = alsa_midi.ffi, alsa_midi.alsa
    holder = ffi.new("snd_midi_event_t **")
    assert alsa.snd_midi_event_new(1024, holder) == 0
    parser = ffi.gc(holder[0], alsa.snd_midi_event_free)
    alsa.snd_midi_event_no_status(parser, 1)
    raw = ffi.new("snd_seq_event_t *")
    event._to_alsa(raw)
    out = ffi.new("char[]", 12)
    count = alsa.snd_midi_event_decode(parser, out, 12, raw)
    assert count > 0
    return bytes(ffi.buffer(out, count))


@pytest.mark.parametrize("event, expected", [
    (alsa_midi.NoteOnEvent(36, 0, 100), Message("note_on", 0, 36, 100)),
    (alsa_midi.NoteOnEvent(36, 0, 0), Message("note_off", 0, 36, 0)),
    (alsa_midi.NoteOffEvent(41, 0, 0), Message("note_off", 0, 41, 0)),
    (alsa_midi.ControlChangeEvent(0, 3, 64), Message("cc", 0, 3, 64)),
    (alsa_midi.ControlChangeEvent(9, 80, 0), Message("cc", 9, 80, 0)),
    (alsa_midi.ProgramChangeEvent(0, 2), Message("program", 0, 2, 0)),
    (alsa_midi.ChannelPressureEvent(0, 50), None),
    (alsa_midi.KeyPressureEvent(36, 0, 50), None),
])
def test_what_libasound_decodes_is_what_decode_reads(event, expected):
    assert decode(alsa_bytes(event)) == expected
