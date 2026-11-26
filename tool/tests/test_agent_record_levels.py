"""The record bar's meters, free space and a take's files, from the agent
(#139): the recorder's `Meter`, measured in its writer thread only while
asked; `GET /record/levels`; GET /record's `space`; and `GET
/takes/{take}/{file}`, read-only and from the takes folder only.

The recorder is real (`recorder.Recorder`, `RecorderCore` through
`ui_fake.CoreLink`, planning from real files as test_agent_record.py's
do), and so are the takes it writes. The engine is stood in: `Held`
below pushes only the blocks a test gives it, so what the meter saw is
known to the sample. Nothing here is PipeWire or JACK.
"""

from __future__ import annotations

import asyncio
import math
import time

import pytest

pytest.importorskip("aiohttp")
np = pytest.importorskip("numpy")
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from sushi_rig import doctor  # noqa: E402
from sushi_rig.agent import take_file  # noqa: E402
from sushi_rig.recorder import Meter, Recorder, column_peaks, peak_dbfs  # noqa: E402
from sushi_rig.recorder_process import RecorderCore, RequestError, bytes_per_second, parse_request  # noqa: E402
from sushi_rig.signals import wav_info  # noqa: E402

from test_agent_record import (  # noqa: E402,F401
    START, STATUS, STOP, World, _agents_release_their_pools, call, calls, record_agent, root,
)
from ui_fake import CoreLink, FakeClock  # noqa: E402


class Held:
    """A capture engine that pushes nothing by itself: `play(levels)` pushes
    one block whose columns are at those linear levels, as the callback
    would. The last one made is `Held.last`."""

    last: "Held | None" = None

    def __init__(self, rate, quantum):
        self.rate, self.quantum, self.push, self.width = rate, quantum, None, 0
        Held.last = self

    def start(self, stems, push):
        self.push, self.width = push, sum(n for _, n in stems)

    def play(self, *levels, frames=None):
        block = np.zeros((frames or self.quantum, self.width), dtype=np.float32)
        for column, level in enumerate(levels):
            block[:, column] = level
        self.push(block)

    def stop(self):
        self.push = None
        return None


def held_agent(root):
    clock = FakeClock()
    link = CoreLink(RecorderCore(engine=Held, clock=clock, say=lambda message: None))
    return record_agent(root, link=link, clock=clock)


def drained(core, timeout=5.0):
    """Wait until the running take's writer has written all that was pushed."""
    recorder = core._take.session._recorder
    end = time.monotonic() + timeout
    while recorder.ring._read < recorder.ring._written:
        assert time.monotonic() < end, "the writer did not drain"
        time.sleep(0.005)
    time.sleep(0.02)  # past the write and the meter's look at it


# --------------------------------------------------------------------------- #
# The meter, and what a level is
# --------------------------------------------------------------------------- #

def test_a_peak_is_said_in_dbfs_and_silence_and_a_fault_are_not_numbers_a_meter_hides():
    assert peak_dbfs(1.0) == 0.0
    assert peak_dbfs(0.5) == pytest.approx(-6.02, abs=0.01)
    assert peak_dbfs(2.0) == pytest.approx(6.02, abs=0.01), "over full scale is kept: the stems are float"
    assert peak_dbfs(0.0) is None, "exact silence has no level in dB"
    assert peak_dbfs(math.inf) == 99.0, "a sample that is not finite is as loud as can be said"


@pytest.mark.parametrize("frames", [1, 2, 3, 7, 255, 256, 257, 4095, 4096, 4097])
def test_a_columns_peak_is_its_largest_magnitude_however_many_frames(frames):
    block = np.random.default_rng(frames).standard_normal((frames, 8)).astype(np.float32)
    assert np.array_equal(column_peaks(block), np.abs(block).max(axis=0))
    block[frames // 2, 3] = np.nan
    assert np.isnan(column_peaks(block)[3]), "a NaN is not lost in the folding"


def test_the_meter_measures_only_while_read_and_holds_every_frame_between_reads():
    now = [100.0]
    meter = Meter(2, lease_s=2.0, clock=lambda: now[0])
    meter.observe(np.full((256, 2), 0.9, dtype=np.float32))
    assert meter.read() == (None, 0), "never asked: nothing was measured"
    meter.observe(np.array([[0.1, -0.25]] * 4 + [[-0.5, 0.0]], dtype=np.float32))
    meter.observe(np.array([[0.2, 0.125]], dtype=np.float32))
    peaks, frames = meter.read()
    assert frames == 6 and peaks == pytest.approx([0.5, 0.25]), "the peak of every frame since the last read"
    assert meter.read() == (None, 0), "and a read starts afresh"
    now[0] += 2.5  # the lease ran out: no page is asking
    assert not meter.armed()
    meter.observe(np.full((256, 2), 1.0, dtype=np.float32))
    assert meter.read() == (None, 0), "disarmed, the writer measures nothing"


def test_a_sample_that_is_not_a_number_reads_as_over_not_as_silence():
    meter = Meter(1, clock=lambda: 0.0)
    meter.read()
    meter.observe(np.array([[np.nan], [0.1]], dtype=np.float32))
    peaks, _ = meter.read()
    assert peaks == [math.inf] and peak_dbfs(peaks[0]) == 99.0


def test_the_audio_callbacks_push_does_not_touch_the_meter(tmp_path, monkeypatch):
    """The meter is the writer thread's: `push`, which the capture engine
    calls from the audio callback, is the ring and the silence watch as it
    was, and the meter sees a block only once it is drained to disk."""
    recorder = Recorder([(tmp_path / "a.wav", 1)], 48000, 256)
    seen = []
    monkeypatch.setattr(recorder.meter, "observe", lambda block: seen.append(len(block)))
    recorder.push(np.full((256, 1), 0.5, dtype=np.float32))
    assert seen == [], "nothing measured in push"
    recorder.start()
    recorder.stop()
    assert seen == [256], "measured as the writer wrote it"


def test_a_recorders_levels_are_per_stem_per_channel_in_the_order_of_its_columns(tmp_path):
    recorder = Recorder([(tmp_path / "board.wav", 2), (tmp_path / "board-dry.wav", 1)], 48000, 256)
    recorder.start()
    try:
        assert recorder.levels() == {"frames": 0, "seconds": 0.0, "stems": [
            {"file": "board.wav", "channels": 2, "peaks_dbfs": None},
            {"file": "board-dry.wav", "channels": 1, "peaks_dbfs": None}]}, "the first read arms it"
        recorder.push(np.tile(np.array([0.5, 0.25, 0.0], dtype=np.float32), (480, 1)))
        end = time.monotonic() + 5
        while recorder.ring._read < recorder.ring._written and time.monotonic() < end:
            time.sleep(0.005)
        time.sleep(0.02)
        levels = recorder.levels()
    finally:
        recorder.stop()
    assert levels["frames"] == 480 and levels["seconds"] == 0.01
    assert levels["stems"][0]["peaks_dbfs"] == pytest.approx([-6.02, -12.04], abs=0.01)
    assert levels["stems"][1]["peaks_dbfs"] == [None], "a silent channel"


# --------------------------------------------------------------------------- #
# The recorder's answer
# --------------------------------------------------------------------------- #

def test_levels_is_a_request_the_recorder_takes_and_takes_nothing_with_it():
    assert parse_request(b'{"op": "levels"}') == {"op": "levels"}
    with pytest.raises(RequestError, match="levels does not take run"):
        parse_request(b'{"op": "levels", "run": {}}')


def test_what_a_take_writes_a_second_is_its_channels_at_its_rate_in_float32():
    stems = [(object(), 2), (object(), 1), (object(), 2)]
    assert bytes_per_second(stems, 48000) == 5 * 48000 * 4


def test_the_recorder_says_its_levels_while_recording_and_idle_otherwise(root):
    agent, world, link, clock = held_agent(root)
    core = link.core
    assert core.handle({"op": "levels"}) == {"ok": True, "state": "idle"}
    assert calls(agent, START)[0][0] == 200
    first = core.handle({"op": "levels"})
    assert first["state"] == "recording" and first["name"] == "riff-idea"
    assert all(stem["peaks_dbfs"] is None for stem in first["stems"]), "not measuring until asked"
    Held.last.play(1.0, 0.5, 0.25, 0.125)
    drained(core)
    second = core.handle({"op": "levels"})
    peaks = [p for stem in second["stems"] for p in stem["peaks_dbfs"]]
    assert peaks[:4] == pytest.approx([0.0, -6.02, -12.04, -18.06], abs=0.01)
    assert all(p is None for p in peaks[4:]), "the columns given no level are silent"
    assert [s["file"] for s in second["stems"]] == [s["file"] for s in core.status()["stems"]]


# --------------------------------------------------------------------------- #
# GET /record/levels
# --------------------------------------------------------------------------- #

def test_get_record_levels_is_the_recorders_answer_while_a_take_runs(root):
    agent, world, link, clock = held_agent(root)
    status, idle = call(agent, "GET", "/record/levels")
    assert (status, idle) == (200, {"ok": True, "state": "idle"})
    calls(agent, START)[0]
    call(agent, "GET", "/record/levels")  # arms the meter
    Held.last.play(0.5, 0.5, 0.5, 1.0)
    drained(link.core)
    status, body = call(agent, "GET", "/record/levels")
    assert status == 200 and body["state"] == "recording" and body["frames"] == 256
    assert body["stems"][0]["peaks_dbfs"][0] == pytest.approx(-6.02, abs=0.01)
    assert link.asked[-1] == {"op": "levels"}, "no run, no files: the levels are the take's alone"


def test_reading_levels_remembers_nothing_and_pushes_nothing(root):
    agent, world, link, clock = held_agent(root)
    link.down = "no such file"
    status, body = call(agent, "GET", "/record/levels")
    assert status == 409 and body["code"] == "no_recorder" and "no such file" in body["reason"]
    _, health = call(agent, "GET", "/health")
    assert health["agent"]["last_error"] is None, "a read a page makes ten times a second is not an error to keep"


def test_a_page_that_stops_asking_stops_the_measuring(root):
    agent, world, link, clock = held_agent(root)
    calls(agent, START)[0]
    call(agent, "GET", "/record/levels")
    meter = link.core._take.session._recorder.meter
    assert meter.armed()
    meter._until = time.monotonic() - 0.001  # the lease run out, as METER_LEASE_S after the last read
    Held.last.play(1.0, 1.0)
    drained(link.core)
    assert meter._frames == 0, "with no page asking, the writer measured nothing"


# --------------------------------------------------------------------------- #
# GET /record's free space and hours left
# --------------------------------------------------------------------------- #

def test_record_says_the_free_space_and_how_long_a_take_of_these_stems_fits(root, monkeypatch):
    agent, world, link, clock = held_agent(root)
    monkeypatch.setattr(doctor, "free_bytes", lambda path: (41 * 3600 * 1_920_000 + 5, ""))
    status, idle = calls(agent, STATUS)[0]
    channels = sum(stem["channels"] for stem in idle["stems"])
    rate = channels * 48000 * 4
    assert status == 200 and idle["bytes_per_second"] == rate and idle["rate"] == 48000
    space = idle["space"]
    assert space["free_bytes"] == 41 * 3600 * 1_920_000 + 5 and space["bytes_per_second"] == rate
    assert space["seconds_left"] == (41 * 3600 * 1_920_000 + 5) // rate
    assert space["dir"] == str((root / "takes").resolve()) and space["problem"] is None
    calls(agent, START)[0]
    _, recording = calls(agent, STATUS)[0]
    assert recording["state"] == "recording" and recording["space"]["bytes_per_second"] == rate, \
        "while recording, the take's own rate"


def test_free_space_is_measured_where_takes_go_even_before_the_folder_exists(root):
    agent, world, link, clock = held_agent(root)
    assert not (root / "takes").exists()
    _, idle = calls(agent, STATUS)[0]
    # Measured on the nearest folder that exists: the checkout, here. Free
    # space moves as anything on this disk writes, so within 100 MB.
    assert abs(idle["space"]["free_bytes"] - doctor.free_bytes(root / "takes")[0]) < 100_000_000
    assert idle["space"]["problem"] is None and idle["space"]["seconds_left"] > 0


def test_a_takes_folder_on_a_drive_not_plugged_in_has_no_space_and_says_why(root, tmp_path_factory):
    elsewhere = tmp_path_factory.mktemp("away")
    link_path = elsewhere / "takes"
    link_path.symlink_to(elsewhere / "drive-not-there")
    agent, world, link, clock = held_agent(root)
    agent.takes_dir = link_path
    _, idle = calls(agent, STATUS)[0]
    assert idle["space"]["free_bytes"] is None and idle["space"]["seconds_left"] is None
    assert "is the drive takes are recorded to plugged in" in idle["space"]["problem"]


def test_no_space_is_said_where_recording_is_off(root):
    agent, world, link, clock = held_agent(root)
    link.down = "gone"
    _, off = calls(agent, STATUS)[0]
    assert off["state"] == "unavailable" and "space" not in off


# --------------------------------------------------------------------------- #
# GET /takes/{take}/{file}
# --------------------------------------------------------------------------- #

def recorded(root):
    """`(agent, link)` with one finished take, riff-idea, of a known block."""
    agent, world, link, clock = held_agent(root)
    calls(agent, START)[0]
    Held.last.play(0.5, 0.25, frames=4800)
    drained(link.core)
    assert calls(agent, STOP)[0][0] == 200
    return agent, link


def fetch(agent, *paths, headers=None):
    async def go():
        out = []
        async with TestClient(TestServer(agent.app())) as client:
            for path in paths:
                response = await client.get(path, headers=headers or {})
                out.append((response.status, dict(response.headers), await response.read()))
        return out

    return asyncio.run(go())


def test_a_takes_stem_is_served_as_the_file_it_is_and_can_be_saved(root):
    agent, link = recorded(root)
    folder = root / "takes" / "riff-idea"
    stem = sorted(p.name for p in folder.glob("*.wav"))[0]
    (play, played, body), (save, saved, _) = fetch(agent, f"/takes/riff-idea/{stem}",
                                                   f"/takes/riff-idea/{stem}?download=1")
    assert play == 200 and body == (folder / stem).read_bytes()
    assert played["Content-Type"] == "audio/wav" and "Content-Disposition" not in played
    assert played["X-Content-Type-Options"] == "nosniff" and played["Content-Security-Policy"].startswith("sandbox")
    assert save == 200 and saved["Content-Disposition"] == f'attachment; filename="{stem}"'
    assert wav_info(folder / stem).frames == 4800


def test_a_stem_is_served_in_ranges_as_an_audio_element_asks(root):
    agent, link = recorded(root)
    stem = sorted(p.name for p in (root / "takes" / "riff-idea").glob("*.wav"))[0]
    [(status, headers, body)] = fetch(agent, f"/takes/riff-idea/{stem}", headers={"Range": "bytes=0-11"})
    assert status == 206 and body[:4] == b"RIFF" and len(body) == 12 and headers["Content-Range"].startswith("bytes 0-11/")


def test_a_takes_own_files_are_text_and_anything_else_is_only_a_download(root):
    agent, link = recorded(root)
    folder = root / "takes" / "riff-idea"
    (folder / "page.html").write_text("<script>fetch('/rig/down', {method: 'POST'})</script>")
    (yaml_status, yaml_headers, _), (html_status, html_headers, _) = fetch(
        agent, "/takes/riff-idea/take.yaml", "/takes/riff-idea/page.html")
    assert yaml_status == 200 and yaml_headers["Content-Type"].startswith("text/plain")
    assert html_status == 200 and html_headers["Content-Type"] == "application/octet-stream"
    assert html_headers["Content-Disposition"].startswith("attachment"), "never rendered as the agent's page"


@pytest.mark.parametrize("path", [
    "/takes/riff-idea/..%2F..%2Frun%2Frigs%2Fduo.json",
    "/takes/..%2Frun/rigs",
    "/takes/../run/rigs/duo.json",
    "/takes/riff-idea/%2E%2E",
    "/takes/%2E%2E/riff-idea",
    "/takes/riff-idea/.hidden",
    "/takes/_archive/old.wav",
    "/takes/riff-idea/no-such.wav",
    "/takes/no-such-take/take.yaml",
    "/takes/riff-idea/inner",
    "/takes/riff-idea/inner%2Fdeep.wav",
    "/takes/riff-idea/out.wav",
    "/takes/outside/take.yaml",
    "/takes/riff-idea/%00.wav",
])
def test_nothing_outside_a_takes_folder_is_served(root, path):
    agent, link = recorded(root)
    takes = root / "takes"
    (takes / "riff-idea" / ".hidden").write_text("secret")
    (takes / "_archive").mkdir(exist_ok=True)
    (takes / "_archive" / "old.wav").write_text("archived")
    (takes / "riff-idea" / "inner").mkdir()
    (takes / "riff-idea" / "inner" / "deep.wav").write_text("deeper")
    (root / "secret.wav").write_text("outside")
    (takes / "riff-idea" / "out.wav").symlink_to(root / "secret.wav")
    (takes / "outside").symlink_to(root / "run", target_is_directory=True)
    (root / "run" / "take.yaml").write_text("not a take")
    [(status, headers, body)] = fetch(agent, path)
    assert status in (403, 404) and b"outside" not in body and b"secret" not in body and b"archived" not in body \
        and b"deeper" not in body and b"not a take" not in body and b'"tracks"' not in body, (status, body[:200])


def test_take_file_refuses_every_name_but_a_plain_one_in_a_take_in_the_folder(tmp_path):
    takes = tmp_path / "takes"
    (takes / "a").mkdir(parents=True)
    (takes / "a" / "x.wav").write_bytes(b"RIFF")
    resolved = takes.resolve()
    assert take_file(resolved, "a", "x.wav") == (resolved / "a" / "x.wav", "audio/wav")
    for take, name in [("..", "x.wav"), ("a", ".."), ("a/..", "x.wav"), ("a", "../a/x.wav"), ("", "x.wav"),
                       ("a", ""), ("_archive", "x.wav"), (".a", "x.wav"), ("a", ".x.wav"), ("a", "x..wav"),
                       ("a", "x.wav\x00"), (None, "x.wav")]:
        assert take_file(resolved, take, name) is None, (take, name)


def test_another_sites_page_cannot_play_or_fetch_a_take(root):
    agent, link = recorded(root)
    [(status, _, body)] = fetch(agent, "/takes/riff-idea/take.yaml", headers={"Sec-Fetch-Site": "cross-site"})
    assert status == 403 and b"cross_site" in body
    [(own, _, _)] = fetch(agent, "/takes/riff-idea/take.yaml", headers={"Sec-Fetch-Site": "same-origin"})
    assert own == 200


def test_a_take_is_served_with_no_rig_up(root):
    agent, link = recorded(root)
    agent.describe = lambda: {"verdict": "absent", "running": False}
    [(status, _, body)] = fetch(agent, "/takes/riff-idea/take.yaml")
    assert status == 200 and body == (root / "takes" / "riff-idea" / "take.yaml").read_bytes()


def test_reading_a_take_never_needs_the_recorder(root):
    agent, link = recorded(root)
    link.down = "the recorder is gone"
    asked = len(link.asked)
    [(status, _, _)] = fetch(agent, "/takes/riff-idea/take.yaml")
    assert status == 200 and len(link.asked) == asked
