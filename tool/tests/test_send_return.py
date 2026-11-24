"""What Sushi's send and return add to the path you hear, measured.

The monitor mix is made in Sushi (decided 2026-10-31): each channel keeps its
own output, so stems exist, and sends a copy to a master track that feeds the
monitors. That puts `sushi.testing.send` and `sushi.testing.return` in the
monitor path, so what they add has to be known before anything is built on them.

Sushi 1.3.0's source says what to expect (return_plugin.cpp, `send_audio`): a
send that runs on the return's thread *before* the return has processed this
block writes straight into the block the return is about to play — no delay. A
send that runs after it writes into the next block — one internal chunk late,
`AUDIO_CHUNK_SIZE`, 64 samples in the stock build. Not a PipeWire quantum: Sushi
processes each 256-sample quantum as four 64-sample chunks.

So the order of tracks in the config should be the whole story. These tests
render through a real Sushi and compare whole stereo renders, sample for sample,
against a plain passthrough. Each channel of the stimulus is a different signal,
so a send that dropped, swapped or inverted a channel would not match.

What this cannot say: whether the live JACK frontend runs tracks on more than
one thread. A send on another thread is always a chunk late. `up` runs Sushi
without `--multicore-processing`, so it should be one thread there too; that is
for the rig machine to confirm.
"""

from __future__ import annotations

import json

import pytest

np = pytest.importorskip("numpy")

from sushi_rig.paths import checkout_root  # noqa: E402
from sushi_rig.render import Stimulus, render_offline  # noqa: E402

ROOT = checkout_root()
SUSHI = ROOT / "sushi"
SR = 48000
# A shift of a PipeWire block or more would be a different mechanism from the
# one the source describes, and would be audible as one.
QUANTUM = 256
# Passthrough is a copy, so a matching render matches to float rounding.
EXACT = 1e-6

needs_sushi = pytest.mark.skipif(not SUSHI.exists(), reason="needs the Sushi binary")


class Distinct(Stimulus):
    """The default sweep on the left, and on the right the same sweep inverted
    and 6 dB down: a dropped, swapped or inverted channel cannot match."""

    def render(self, sample_rate: int):
        fed = super().render(sample_rate).copy()
        fed[:, 1] *= -0.5
        return fed


def passthrough_track(name: str, inputs, outputs) -> dict:
    return {
        "name": name,
        "channels": 2,
        "inputs": inputs,
        "outputs": outputs,
        "plugins": [{"uid": "sushi.testing.passthrough", "name": f"{name}_pass", "type": "internal"}],
    }


BUS0 = [{"engine_bus": 0, "track_bus": 0}]

DIRECT = {"host_config": {"samplerate": SR}, "tracks": [passthrough_track("channel", BUS0, BUS0)]}


def through_master(*, master_first: bool, channel_output: bool) -> dict:
    """A channel sending to a master track's return.

    `channel_output` is the decided design: the channel keeps its own output as
    well as sending. With only one stereo bus offline, both land on bus 0, so the
    render is the channel twice — which is what shows the send copies the signal
    without taking anything from the channel's own output.
    """
    channel = passthrough_track("channel", BUS0, BUS0 if channel_output else [])
    channel["plugins"].append({"uid": "sushi.testing.send", "name": "channel_send", "type": "internal"})
    master = {
        "name": "master",
        "channels": 2,
        "inputs": [],
        "outputs": BUS0,
        "plugins": [{"uid": "sushi.testing.return", "name": "master_return", "type": "internal"}],
    }
    return {
        "host_config": {"samplerate": SR},
        "tracks": [master, channel] if master_first else [channel, master],
        "initial_state": [
            {"processor": "channel_send", "properties": {"destination_name": "master_return"}},
        ],
    }


def render(tmp_path, name: str, config: dict) -> "np.ndarray":
    path = tmp_path / f"{name}.json"
    path.write_text(json.dumps(config))
    out = render_offline(path, ROOT, tmp_path / name, stimulus=Distinct(duration_s=1.0),
                         sushi_bin=str(SUSHI))
    return out["rendered"]


def shift_matching(rendered, direct) -> int | None:
    """The delay, in samples, at which `rendered` is `direct` exactly, if any."""
    for shift in range(QUANTUM):
        later = rendered[shift:]
        if np.max(np.abs(later - direct[:len(later)])) < EXACT:
            return shift
    return None


@pytest.fixture(scope="module")
def direct(tmp_path_factory):
    rendered = render(tmp_path_factory.mktemp("direct"), "direct", DIRECT)
    # Something must come out, and on both sides, or every comparison below
    # is between silences.
    assert np.max(np.abs(rendered[:, 0])) > 0.1 and np.max(np.abs(rendered[:, 1])) > 0.05
    return rendered


@needs_sushi
@pytest.mark.slow
def test_a_master_after_the_channel_adds_nothing(tmp_path, direct):
    rendered = render(tmp_path, "after", through_master(master_first=False, channel_output=False))
    assert shift_matching(rendered, direct) == 0


@needs_sushi
@pytest.mark.slow
def test_a_master_before_the_channel_is_late_by_less_than_a_block(tmp_path, direct):
    """One internal chunk, by the source: 64 samples in the stock build, which
    is what 1.3.0 measured here. Asserted as a shift under one PipeWire block
    rather than as 64, so a build with another chunk size still reads true."""
    rendered = render(tmp_path, "before", through_master(master_first=True, channel_output=False))
    shift = shift_matching(rendered, direct)
    print(f"master before the channel: {shift} samples late")
    assert shift is not None and 0 < shift < QUANTUM, shift


@needs_sushi
@pytest.mark.slow
def test_a_channel_that_sends_keeps_its_own_output(tmp_path, direct):
    """The decided design: the channel's own output and the master's copy, both
    on bus 0 here, sum to the channel exactly twice over."""
    rendered = render(tmp_path, "both", through_master(master_first=False, channel_output=True))
    assert np.max(np.abs(rendered - 2 * direct)) < EXACT


# --------------------------------------------------------------------------- #
# A channel's mute (#210): which of Sushi's switches reaches the monitors
# --------------------------------------------------------------------------- #

# Sushi 1.3.0 starts every processor running and ramps a saved bypass in over
# 7 chunks, 448 frames (`BypassManager`, processor.h; MANIFEST, the octave
# pair's re-recording): what comes before that is a fade, not the state.
SETTLED = 1024


def with_state(config: dict, *entries: dict) -> dict:
    config = json.loads(json.dumps(config))
    config["initial_state"] = config["initial_state"] + list(entries)
    return config


@needs_sushi
@pytest.mark.slow
def test_a_tracks_mute_does_not_reach_the_master(tmp_path, direct):
    """The bug #210 found on the laptop: the track's `mute`, which a
    footswitch used to toggle, acts after the track's plugins, so after its
    send, and the master's copy is the channel as if nothing were muted."""
    config = with_state(through_master(master_first=False, channel_output=False),
                        {"processor": "channel", "parameters": {"mute": 1.0}})
    rendered = render(tmp_path, "track-muted", config)
    assert np.max(np.abs(rendered[SETTLED:] - direct[SETTLED:])) < EXACT


@needs_sushi
@pytest.mark.slow
def test_offline_a_send_not_bypassed_reaches_the_master(tmp_path, direct):
    """The control for the bypass tests: the same send, with its bypass
    stated false in `initial_state` as a bypassed one's is stated true,
    reaches the master as it is, so a silence below means the bypass and
    not a send that never connected."""
    config = with_state(through_master(master_first=False, channel_output=False),
                        {"processor": "channel_send", "bypassed": False})
    rendered = render(tmp_path, "send-not-bypassed", config)
    assert np.max(np.abs(rendered[SETTLED:] - direct[SETTLED:])) < EXACT


@needs_sushi
@pytest.mark.slow
def test_offline_a_bypassed_send_still_sends_so_a_render_cannot_show_a_mute(tmp_path, direct):
    """What CI found on 8a798de (#210's review): rendered offline, a send
    bypassed in `initial_state` reaches the master at full level, for as
    long as the render runs (10 s, checked by hand). Not the send ignoring
    its bypass: Sushi 1.3.0's `SendPlugin::set_bypassed` posts a
    `SetProcessorBypassEvent` to the event dispatcher and only applies it
    when that arrives on the audio thread (send_plugin.cpp, `process_event`),
    and the offline frontend's blocking loop never delivers it (it plays
    only the config's own `events`, offline_frontend.cpp). An LV2 plugin
    sets its bypass directly, which is why a bypassed plugin renders as one.
    On a running Sushi the event does arrive, and the send goes silent:
    the running test below. Held here so that a Sushi whose offline
    renders do apply it says so, and the two tests can become one."""
    config = with_state(through_master(master_first=False, channel_output=False),
                        {"processor": "channel_send", "bypassed": True})
    rendered = render(tmp_path, "send-bypassed-offline", config)
    assert np.max(np.abs(rendered[SETTLED:] - direct[SETTLED:])) < EXACT, (
        "a send bypassed offline no longer sends: an offline render can now show a mute")


# --------------------------------------------------------------------------- #
# The mute on a running Sushi: the send's bypass set over gRPC while it plays
# --------------------------------------------------------------------------- #

# A block of the recording, for saying what the master did in it: 100 ms.
BLOCK = SR // 10
# How long each phase of the running test lasts, in wall-clock seconds. The
# dummy frontend runs faster than real time (about 17 times, here), so each is
# many seconds of audio; the un-muted one is the longest because it is the one
# a short recording cut off (#243).
PLAYING, MUTED, UNMUTED = 0.5, 0.5, 1.5
# The send's channel parameters are integers from 0 to 16 (MAX_TRACK_CHANNELS,
# send_plugin.cpp) and the config sets them normalised: 1 is 1/16.
ONE_CHANNEL = 1 / 16


# The channel the running test's sine plays on: each side checks its own
# channel of the send, the tap carrying the stem to the other.
LEFT, RIGHT = 0, 1


def running_config(tmp_path, sine, side: int = LEFT) -> dict:
    """A channel playing `sine` (Sushi's own wav_streamer, looping) into its
    send, as the rig makes it, then a second send, the tap, carrying the
    channel's `side` after the first send (its stem) to the master's other
    side; the master's return, then a wav_writer recording what the master
    hears.

    One recording, so the stem and the master are on one timeline. With the
    stem on a wav_writer of its own (as this was until #243), the two
    recordings drift apart: in 402 runs here, 42 ended with the stem and the
    master out of line, and in 6 of them that came early enough that the
    master's last phase never compared as a copy (out by 272 to 576 frames,
    as near as the sine's 1200-frame repeat lets it be said). Why, read in
    Sushi 1.3.0's source and not isolated: the dummy frontend runs as fast
    as it can, and a wav_writer pushes each chunk into a ring buffer that a
    worker empties, losing a chunk that does not fit, so each writer loses
    its own. `sine` plays on `side` only: the master's `side` is then the
    send's copy alone (the tap does not reach it), and the other side the
    stem alone (the send adds zeros there). One side per run, so the running
    test is run once for each, and each checks its own channel of the
    send."""
    other = 1 - side
    channel = {"name": "channel", "channels": 2, "inputs": [], "outputs": [], "plugins": [
        {"uid": "sushi.testing.wav_streamer", "name": "channel_src", "type": "internal"},
        {"uid": "sushi.testing.send", "name": "channel_send", "type": "internal"},
        {"uid": "sushi.testing.send", "name": "channel_tap", "type": "internal"}]}
    master = {"name": "master", "channels": 2, "inputs": [], "outputs": [], "plugins": [
        {"uid": "sushi.testing.return", "name": "master_return", "type": "internal"},
        {"uid": "sushi.testing.wav_writer", "name": "master_rec", "type": "internal"}]}
    return {"host_config": {"samplerate": SR}, "tracks": [channel, master], "initial_state": [
        {"processor": "channel_send", "properties": {"destination_name": "master_return"}},
        {"processor": "channel_tap", "properties": {"destination_name": "master_return"},
         "parameters": {"start_channel": side * ONE_CHANNEL, "channel_count": ONE_CHANNEL,
                        "dest_channel": other * ONE_CHANNEL}},
        {"processor": "channel_src", "properties": {"file": str(sine)}, "parameters": {"loop": 1.0, "playing": 1.0}},
        {"processor": "master_rec", "properties": {"destination_file": str(tmp_path / "master_rec")},
         "parameters": {"recording": 1.0}}]}


def master_blocks(master, side: int = LEFT) -> list[str]:
    """Each 100 ms block of the master's recording, on its own timeline: the
    send's copy on `side`, the stem on the other. "copy" (the send's side is
    the stem exactly), "muted" (it has nothing of it), "between" (a bypass
    ramping), or "quiet" where the stem itself is silent, which says
    nothing: the streamer leaves a gap after its first loop, and may fall
    behind a frontend running faster than real time, which is the
    streamer's and not the mute's."""
    kinds = []
    for start in range(0, len(master) - BLOCK + 1, BLOCK):
        sent, stem = master[start:start + BLOCK, side], master[start:start + BLOCK, 1 - side]
        kinds.append("quiet" if np.max(np.abs(stem)) < 0.25 else
                     "copy" if np.max(np.abs(sent - stem)) < EXACT else
                     "muted" if np.max(np.abs(sent)) < EXACT else "between")
    return kinds


def what_the_master_did(kinds: list[str]) -> list[str]:
    """`master_blocks` with runs of one kind said once and the "quiet"s and
    "between"s left out."""
    said: list[str] = []
    for kind in kinds:
        if kind in ("copy", "muted") and (not said or said[-1] != kind):
            said.append(kind)
    return said


def runs(kinds: list[str]) -> str:
    """The blocks as runs, for a failure to say how long each phase was."""
    out: list[list] = []
    for kind in kinds:
        if out and out[-1][0] == kind:
            out[-1][1] += 1
        else:
            out.append([kind, 1])
    return ", ".join(f"{kind} {n}" for kind, n in out)


@pytest.mark.parametrize("side", [LEFT, RIGHT], ids=["left", "right"])
def test_the_master_is_read_on_its_own_timeline(side):
    """The classifier, on a made-up recording: the stem's gap and a ramp
    say nothing, and a phase is a copy only where the send's side is the
    stem's. Read from the wrong side, the same recording is no copy."""
    pair = (lambda sent, stem: np.stack([sent, stem] if side == LEFT else [stem, sent], axis=1))
    n = BLOCK
    tone = 0.5 * np.sin(2 * np.pi * 440 * np.arange(10 * n) / SR)
    stem = tone.copy()
    stem[n:2 * n] = 0.0                      # the streamer's gap
    sent = stem.copy()
    sent[4 * n:7 * n] = 0.0                  # the mute
    sent[7 * n:8 * n] *= np.linspace(0, 1, n)  # its ramp back
    kinds = master_blocks(pair(sent, stem), side)
    assert kinds == ["copy", "quiet", "copy", "copy", "muted", "muted", "muted", "between", "copy", "copy"]
    assert what_the_master_did(kinds) == ["copy", "muted", "copy"]
    assert runs(kinds).startswith("copy 1, quiet 1, copy 2, muted 3")
    # Read with the sides swapped, the stem is taken for the send: the
    # mute's blocks are a stem that is silent, so the mute is not seen.
    assert "muted" not in master_blocks(pair(sent, stem), 1 - side)
    # A send that is the stem a little late is not a copy.
    late = np.concatenate([np.zeros(7), stem[:-7]])
    assert "copy" not in master_blocks(pair(late, stem), side)


@needs_sushi
@pytest.mark.slow
@pytest.mark.parametrize("side", [LEFT, RIGHT], ids=["left", "right"])
def test_on_a_running_sushi_a_bypassed_send_sends_nothing_and_the_stem_plays_on(tmp_path, side):
    """The mute the agent plays (#210), as the agent plays it: Sushi 1.3.0
    running (its dummy frontend, which processes on its own thread, faster
    than real time), the send's bypass set and cleared over gRPC by
    `live.set_live_bypass`, the call the agent's mute makes, while a sine
    plays, and read back by `live.read_live_bypass` after each change. The
    master is the stem exactly until the bypass, nothing at all while it
    holds, and the stem exactly again once it is cleared; the stem plays
    throughout. Seen with the release AppImage by hand first (2026-11-23):
    23 s of audio silent on the master, then back.

    #243: this failed once in CI with the master's last phase missing. Run
    402 times here as it stood, Sushi took every bypass and un-bypass (read
    back at once and 0.2 s later, and the master's own recording playing
    again after the mute in all 402); the 6 failures were the stem's and the
    master's recordings drifting apart, which `running_config` now avoids.
    Run once with the sine on each side, so each of the send's two channels
    is heard muted and coming back."""
    import glob
    import socket
    import subprocess
    import time

    pytest.importorskip("elkpy")
    from sushi_rig import live, signals

    seconds = np.arange(SR) / SR
    sine = tmp_path / "sine.wav"
    tone = 0.5 * np.sin(2 * np.pi * 440 * seconds)
    silent = np.zeros_like(tone)
    signals.write_wav(sine, np.stack([tone, silent] if side == LEFT else [silent, tone], axis=1), SR)
    config = tmp_path / "running.json"
    config.write_text(json.dumps(running_config(tmp_path, sine, side)))
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    address = f"localhost:{port}"
    proc = subprocess.Popen([str(SUSHI), "-d", "-c", str(config), f"--grpc-address={address}"],
                            cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
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
        time.sleep(PLAYING)  # playing, not muted: the control
        assert live.read_live_bypass("channel_send", address)["bypassed"] is False
        live.set_live_bypass("channel_send", True, address)
        assert live.read_live_bypass("channel_send", address)["bypassed"] is True, "the bypass did not take"
        time.sleep(MUTED)
        live.set_live_bypass("channel_send", False, address)
        assert live.read_live_bypass("channel_send", address)["bypassed"] is False, "the un-bypass did not take"
        time.sleep(UNMUTED)
        assert live.read_live_bypass("channel_send", address)["bypassed"] is False, "the un-bypass did not hold"
        live.set_live_parameter("master_rec", "recording", 0.0, address)
        time.sleep(0.3)
    finally:
        proc.terminate()
        try:
            proc.wait(10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(5)
    (master_file,) = glob.glob(str(tmp_path / "master_rec*.wav"))
    master = signals.read_wav(master_file).samples
    assert len(master) > 4 * SR, f"too little was recorded to say anything ({len(master)} frames)"
    kinds = master_blocks(master, side)
    said = what_the_master_did(kinds)
    phases = f"{said}, in 100 ms blocks of the master: {runs(kinds)}"
    assert said[:1] == ["copy"], f"the send did not reach the master before the mute: {phases}"
    assert "muted" in said, f"the master heard a bypassed send: {phases}"
    assert said == ["copy", "muted", "copy"], f"the send did not come back once un-bypassed: {phases}"
    # A "muted" block is one where the stem plays and the master has none of
    # it: the stem going on through the mute is in the count.
    muted = kinds.count("muted")
    assert muted >= 5, f"only {muted} blocks of the stem playing with the master silent: {phases}"
