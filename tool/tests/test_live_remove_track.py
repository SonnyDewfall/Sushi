"""A channel taken out of a running Sushi (#190): `live.remove_live_track`,
the call a `Live.remove_track` would make for the agent's `remove_track`
switch step, against a real Sushi 1.3.0 while it plays.

The agent's own Sushi link does not make it (`Live.at` gives no
`remove_track`), so a channel's removal is a restart, asked first. Run by
hand hundreds of times (2026-11-24, the release AppImage, the rig of this
test), the call did what this test asks nearly always, but not every time:
about one command in twenty was applied and never confirmed (elkpy's
notice of an async command, nearly always the first on a fresh connection,
which is why this waits a second after connecting), and a few times in
some six hundred runs Sushi stopped applying graph changes (the deletion
neither applied nor confirmed) and then ignored SIGTERM. A failure here is
one of those, and they are why the agent does not do this live.

As test_send_return.py's running test (#223): Sushi's dummy frontend, which
processes on its own thread faster than real time, with gRPC; two channels,
each playing a sine of its own (Sushi's wav_streamer, looping) into its
send, and the master's return recorded by a wav_writer. The last channel is
removed while it plays. What the master heard is read by its spectrum,
block by block: the channel that stays is 440 Hz, the one that goes 660 Hz.
Not by comparing two recordings frame by frame: two wav_writers drift apart
on the dummy frontend (seen here: 11168 frames in one run), which would
fail the comparison with nothing wrong in the sound.

What it cannot show: the JACK frontend the rig runs, whose engine inputs
and outputs a track is connected to (the dummy frontend has none to let go
of; `AudioEngine::delete_track` removes a track's connections first).
"""

from __future__ import annotations

import glob
import json

import pytest

np = pytest.importorskip("numpy")

from test_send_return import BLOCK, SR, SUSHI, ROOT, needs_sushi  # noqa: E402

STAYS_HZ, GOES_HZ = 440, 660  # whole cycles in a 100 ms block: each its own FFT bin
HEARD, GONE = 0.1, 1e-4  # amplitudes: a sine at 0.3 or 0.4 is heard; float noise is gone


def two_channels(tmp_path, sines) -> dict:
    """Channels `stays` and `goes`, in that order, each a streamer into its
    send, and the master's return recorded."""
    def channel(name):
        return {"name": name, "channels": 2, "inputs": [], "outputs": [], "plugins": [
            {"uid": "sushi.testing.wav_streamer", "name": f"{name}_src", "type": "internal"},
            {"uid": "sushi.testing.send", "name": f"{name}_send", "type": "internal"}]}

    master = {"name": "master", "channels": 2, "inputs": [], "outputs": [], "plugins": [
        {"uid": "sushi.testing.return", "name": "master_return", "type": "internal"},
        {"uid": "sushi.testing.wav_writer", "name": "master_rec", "type": "internal"}]}
    state = [{"processor": "master_rec", "properties": {"destination_file": str(tmp_path / "master_rec")},
              "parameters": {"recording": 1.0}}]
    for name in ("stays", "goes"):
        state += [{"processor": f"{name}_send", "properties": {"destination_name": "master_return"}},
                  {"processor": f"{name}_src", "properties": {"file": str(sines[name])},
                   "parameters": {"loop": 1.0, "playing": 1.0}}]
    return {"host_config": {"samplerate": SR}, "initial_state": state,
            "tracks": [channel("stays"), channel("goes"), master]}


def what_the_master_had(master) -> list[tuple[str | None, int]]:
    """Each 100 ms block of the master's left channel, by the amplitude of
    each channel's sine in it: "both", "stays" (the 440 Hz alone, nothing of
    the 660 Hz), or None (anything else: a streamer's gap, a block a change
    fell in), as `(kind, block index)`."""
    out = []
    bins = {hz: hz * BLOCK // SR for hz in (STAYS_HZ, GOES_HZ)}
    for index, start in enumerate(range(0, len(master) - BLOCK + 1, BLOCK)):
        spectrum = np.abs(np.fft.rfft(master[start:start + BLOCK, 0])) * 2 / BLOCK
        stays, goes = spectrum[bins[STAYS_HZ]], spectrum[bins[GOES_HZ]]
        kind = None
        if stays > HEARD:
            kind = "both" if goes > HEARD else "stays" if goes < GONE else None
        out.append((kind, index))
    return out


@needs_sushi
@pytest.mark.slow
def test_on_a_running_sushi_the_last_channel_is_removed_and_the_other_plays_on(tmp_path):
    import socket
    import subprocess
    import time

    pytest.importorskip("elkpy")
    from sushi_rig import live, signals
    from sushi_rig.agent import Live

    seconds = np.arange(SR) / SR
    sines = {}
    for name, hz, level in (("stays", STAYS_HZ, 0.4), ("goes", GOES_HZ, 0.3)):
        sines[name] = tmp_path / f"{name}.wav"
        signals.write_wav(sines[name], np.stack([level * np.sin(2 * np.pi * hz * seconds)] * 2, axis=1), SR)
    config = tmp_path / "two.json"
    config.write_text(json.dumps(two_channels(tmp_path, sines)))
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    address = f"localhost:{port}"
    log = tmp_path / "sushi.log"
    with log.open("wb") as out:
        proc = subprocess.Popen([str(SUSHI), "-d", "-c", str(config), f"--grpc-address={address}"],
                                cwd=ROOT, stdout=out, stderr=subprocess.STDOUT)
    try:
        deadline = time.monotonic() + 20
        while True:
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                    break
            except OSError:
                assert proc.poll() is None, f"Sushi exited {proc.returncode} before its gRPC answered"
                assert time.monotonic() < deadline, "Sushi's gRPC never answered"
                time.sleep(0.1)
        time.sleep(0.5)  # both channels playing: the control
        before = live.read_live_chains(address)
        assert list(before["chains"]) == ["stays", "goes", "master"], before
        assert before["chains"]["goes"] == ["goes_src", "goes_send"]
        # On a connection of its own, as the agent's switch would make it.
        # The second's wait: elkpy subscribes to Sushi's command notices on
        # a thread of its own, and a command sent before that is up is
        # applied and never confirmed (measured: 10 first commands in 150
        # unconfirmed without a wait, none of 150 with half a second).
        sushi = Live.at(address)
        controller = sushi.connect()
        time.sleep(1.0)
        try:
            live.remove_live_track("goes", address, controller)
        finally:
            sushi.disconnect(controller)
        assert proc.poll() is None, f"Sushi exited {proc.returncode} on the track's removal"
        after = live.read_live_chains(address)
        assert list(after["chains"]) == ["stays", "master"], f"the track is still listed: {after['chains']}"
        assert not {"goes", "goes_src", "goes_send"} & set(after["ids"]), f"left behind: {after['ids']}"
        assert after["chains"]["stays"] == before["chains"]["stays"], "the other channel is as it was"
        with pytest.raises(live.LiveError, match="no track 'goes'"):
            live.remove_live_track("goes", address)
        time.sleep(0.5)  # the one channel left
        live.set_live_parameter("master_rec", "recording", 0.0, address)  # still answering
        time.sleep(0.3)
        assert proc.poll() is None, f"Sushi exited {proc.returncode} after the removal"
    finally:
        proc.terminate()
        try:
            proc.wait(10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(5)
    said = log.read_text(errors="replace")
    (master_file,) = glob.glob(str(tmp_path / "master_rec*.wav"))
    master = signals.read_wav(master_file).samples
    assert len(master) > 4 * SR, f"too little was recorded to say anything ({len(master)} frames)"
    had = what_the_master_had(master)
    kinds = [kind for kind, _ in had if kind]
    runs = [kind for i, kind in enumerate(kinds) if i == 0 or kinds[i - 1] != kind]
    assert runs == ["both", "stays"], f"the master did not go from both channels to the one left: {runs}\n{said[-2000:]}"
    # Nothing of the removed channel reaches the master any more: from the
    # first block of the channel left alone to the end, no block has the
    # removed channel's sine in it (a gap of the streamer may have neither).
    first = next(index for kind, index in had if kind == "stays")
    late = [index for kind, index in had[first:] if kind == "both"]
    assert late == [], f"the removed channel reached the master again, in blocks {late[:10]}"
    alone = sum(1 for kind, _ in had[first:] if kind == "stays")
    assert alone >= 5, f"only {alone} blocks of the channel left reaching the master alone"
