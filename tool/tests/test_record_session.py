"""The record bar turned on (#57): which ports a take records, and the session
the listener runs.

No JACK and no PipeWire anywhere: the port plan is pure, the engine is stood
in, and each fallback is a sentence checked here. Whether those ports carry
signal on the rig is the rig machine's question (transport.py, NOT VERIFIED).
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

from sushi_rig import capture, cli, transport
from sushi_rig.capture import CaptureUnavailable, plan_columns
from sushi_rig.machine import MachineSpec
from sushi_rig.paths import checkout_root
from sushi_rig.rigfile import compile_rig, emit_compiled, load_rig
from sushi_rig.take import DRY, PROCESSED, Stem
from sushi_rig.transport import (
    NoEngine,
    PlanError,
    TakeSession,
    Transport,
    record_session,
    rig_stems,
)

ROOT = checkout_root()
GUITAR_VOCAL = ROOT / "config" / "rigs" / "guitar_vocal.yaml"
THIS_MACHINE = ROOT / "machines" / "sonny-dewfall-XPS-9320.yaml"

TWO_CHANNELS = textwrap.dedent("""\
    kind: rig
    schema_version: 1
    name: duo
    channels:
      - name: guitar
        input: guitar-in
        width: mono
      - name: keys
        input: keys-in
        width: stereo
    master:
      output: monitor-out
    """)

MACHINE = textwrap.dedent("""\
    schema_version: 1
    roles:
      guitar-in:
        ports: ["iface:capture_AUX0"]
        mono_to_stereo: true
      keys-in: ["iface:capture_AUX2", "iface:capture_AUX3"]
      monitor-out: ["iface:playback_FL", "iface:playback_FR"]
    """)


def config_of(rig) -> dict:
    """The config `up` compiles for `rig`: what Sushi loads, and where the
    plan reads each track's output from."""
    return emit_compiled(rig, *compile_rig(rig))


def stems_of(rig_path, machine) -> tuple:
    rig = load_rig(rig_path)
    machine = machine if isinstance(machine, MachineSpec) else MachineSpec.load(machine)
    return rig_stems(rig, machine, config_of(rig))


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


@pytest.fixture
def duo(tmp_path):
    """A two-channel rig, its machine file, and the config `up` would compile."""
    rig_path = write(tmp_path / "config" / "rigs" / "duo.yaml", TWO_CHANNELS)
    machine_path = write(tmp_path / "machines" / "box.yaml", MACHINE)
    rig = load_rig(rig_path)
    config = config_of(rig)
    config.setdefault("_meta", {})["source"] = "config/rigs/duo.yaml"
    config_path = write(tmp_path / "run" / "duo.json", json.dumps(config))
    return rig_path, config_path, machine_path


class StandInEngine:
    def __init__(self, sample_rate, quantum):
        self.made_with = (sample_rate, quantum)


def session_for(paths, tmp_path, **kw):
    rig_path, config_path, machine_path = paths
    kw.setdefault("engine", StandInEngine)
    return record_session(rig_path, config_path, machine_file=machine_path,
                          takes_dir=tmp_path / "takes", root=tmp_path, **kw)


# --------------------------------------------------------------------------- #
# The port plan
# --------------------------------------------------------------------------- #

def test_a_two_channel_rig_records_each_stem_its_dry_tap_and_the_master(duo):
    rig_path, _, machine_path = duo
    stems = stems_of(rig_path, machine_path)
    assert stems == (
        (Stem("guitar", PROCESSED, "sushi:audio_output_2"), 2),
        (Stem("guitar", DRY, "iface:capture_AUX0"), 1),
        (Stem("keys", PROCESSED, "sushi:audio_output_4"), 2),
        (Stem("keys", DRY, "iface:capture_AUX2"), 2),
        (Stem("master", PROCESSED, "sushi:audio_output_0"), 2),
    )
    # And the engine links every column from its own port.
    assert [c.source for c in plan_columns(stems)] == [
        "sushi:audio_output_2", "sushi:audio_output_3", "iface:capture_AUX0",
        "sushi:audio_output_4", "sushi:audio_output_5", "iface:capture_AUX2",
        "iface:capture_AUX3", "sushi:audio_output_0", "sushi:audio_output_1",
    ]


def test_guitar_vocal_on_this_machine_is_eight_columns():
    stems = stems_of(GUITAR_VOCAL, THIS_MACHINE)
    assert [(s.filename, s.port, n) for s, n in stems] == [
        ("guitar.wav", "sushi:audio_output_2", 2),
        ("guitar-dry.wav", "Audient iD14:capture_AUX0", 1),
        ("vocal.wav", "sushi:audio_output_4", 2),
        ("vocal-dry.wav", "Audient iD14:capture_AUX1", 1),
        ("master.wav", "sushi:audio_output_0", 2),
    ]
    assert len(plan_columns(stems)) == 8


def test_no_stem_is_tapped_from_a_port_the_patchbay_links_to_the_monitors():
    """A channel's stem is linked to nothing (rigfile.py); only the master is
    heard. A stem on a monitor port would hold the mix under a channel's name."""
    from sushi_rig.patchbay import parse_patchbay, patchbay_for

    rig = load_rig(GUITAR_VOCAL)
    machine = MachineSpec.load(THIS_MACHINE)
    xml, _ = patchbay_for(compile_rig(rig)[0], machine, None)
    linked_out = {link.out_port for link in parse_patchbay(xml)}
    columns = plan_columns(rig_stems(rig, machine, config_of(rig)))
    heard = {c.source for c in columns if c.source in linked_out and c.source.startswith("sushi:")}
    assert heard == {"sushi:audio_output_0", "sushi:audio_output_1"}
    assert {c.stem for c in columns if c.source in heard} == {"master.wav"}


def test_a_channel_whose_input_role_is_not_in_the_machine_file_is_refused(duo, tmp_path):
    rig_path, _, _ = duo
    machine = write(tmp_path / "less.yaml", MACHINE.replace("keys-in", "piano-in"))
    with pytest.raises(PlanError) as caught:
        stems_of(rig_path, machine)
    assert caught.value.problems == [
        "channel 'keys' plays from 'keys-in', which the machine file gives no ports, "
        "so it has no dry tap"
    ]


def test_a_stereo_input_on_ports_that_are_not_consecutive_is_refused(duo, tmp_path):
    rig_path, _, _ = duo
    machine = write(tmp_path / "odd.yaml", MACHINE.replace(
        '["iface:capture_AUX2", "iface:capture_AUX3"]', '["iface:capture_AUX3", "iface:capture_AUX2"]'))
    with pytest.raises(PlanError) as caught:
        stems_of(rig_path, machine)
    (problem,) = caught.value.problems
    assert problem.startswith("keys's dry tap cannot be recorded: its ports iface:capture_AUX3, "
                              "iface:capture_AUX2 are not consecutively numbered")


BLEND = TWO_CHANNELS.replace("name: keys\n    input: keys-in\n    width: stereo",
                             "name: clean\n    input: guitar-in\n    width: mono")


def test_two_channels_on_one_input_share_one_dry_tap_named_for_the_input(tmp_path):
    """A blend of two chains on one guitar: one physical input, so one dry
    stem, named for the role, not two stems of one signal."""
    rig_path = write(tmp_path / "blend.yaml", BLEND)
    stems = stems_of(rig_path, write(tmp_path / "m.yaml", MACHINE))
    assert [(s.filename, s.port, n) for s, n in stems] == [
        ("guitar.wav", "sushi:audio_output_2", 2),
        ("guitar-in-dry.wav", "iface:capture_AUX0", 1),
        ("clean.wav", "sushi:audio_output_4", 2),
        ("master.wav", "sushi:audio_output_0", 2),
    ]


def test_the_outputs_are_the_ones_in_the_config_sushi_loaded(duo):
    """Not a fresh compile of the rig file, which may have changed since `up`."""
    rig_path, config_path, machine_path = duo
    rig = load_rig(rig_path)
    config = json.loads(config_path.read_text())
    guitar = next(t for t in config["tracks"] if t["name"] == "guitar")
    guitar["outputs"] = [{"engine_bus": 3, "track_bus": 0}]
    stems = rig_stems(rig, MachineSpec.load(machine_path), config)
    assert stems[0] == (Stem("guitar", PROCESSED, "sushi:audio_output_6"), 2)


def test_a_channel_the_loaded_config_lacks_is_refused(duo):
    rig_path, config_path, machine_path = duo
    config = json.loads(config_path.read_text())
    config["tracks"] = [t for t in config["tracks"] if t["name"] != "keys"]
    with pytest.raises(PlanError) as caught:
        rig_stems(load_rig(rig_path), MachineSpec.load(machine_path), config)
    assert caught.value.problems == [
        "the config Sushi loaded has no track 'keys', which the rig file has: the "
        "rig file has changed since `up`, so restart the rig"
    ]


# --------------------------------------------------------------------------- #
# The session the listener runs
# --------------------------------------------------------------------------- #

def test_the_pieces_in_place_make_a_take_session(duo, tmp_path):
    session = session_for(duo, tmp_path)
    assert isinstance(session, TakeSession)
    plan = session.plan
    assert [s.filename for s, _ in plan.stems] == [
        "guitar.wav", "guitar-dry.wav", "keys.wav", "keys-dry.wav", "master.wav"]
    assert (plan.sample_rate, plan.quantum) == (48000, 256)
    assert session.engine.made_with == (48000, 256)
    assert plan.config_source == "config/rigs/duo.yaml"
    assert plan.rig_path == duo[0] and plan.library_root == tmp_path
    assert plan.takes_dir == tmp_path / "takes"
    assert [t["name"] for t in plan.config["tracks"]] == ["guitar", "keys", "master"]


def test_the_quantum_is_the_one_up_gives_sushi(duo, tmp_path):
    from sushi_rig.rig import QUANTUM

    assert session_for(duo, tmp_path).plan.quantum == QUANTUM


def no_engine(session) -> str:
    assert isinstance(session, NoEngine), session
    return session.message


def test_without_jack_client_record_says_how_to_get_it(duo, tmp_path, monkeypatch):
    def missing():
        raise CaptureUnavailable(["JACK-Client is not installed (No module named 'jack'): "
                                  'pip install -e "tool[capture]"'])

    monkeypatch.setattr(capture, "load_jack", missing)
    said = no_engine(session_for(duo, tmp_path, engine=None))
    assert said == ('recording is not available: JACK-Client is not installed (No module '
                    'named \'jack\'): pip install -e "tool[capture]"')


def test_with_jack_client_the_default_engine_is_a_capture_engine(duo, tmp_path, monkeypatch):
    monkeypatch.setattr(capture, "load_jack", lambda: object())
    session = session_for(duo, tmp_path, engine=None)
    assert isinstance(session.engine, capture.CaptureEngine)
    assert (session.engine.sample_rate, session.engine.quantum) == (48000, 256)


def test_a_v1_rig_of_tracks_has_no_stems_to_record(tmp_path):
    v1 = ROOT / "config" / "src" / "electric_board.yaml"
    said = no_engine(record_session(v1, ROOT / "config" / "electric_board.json",
                                    engine=StandInEngine))
    assert said == (
        f"recording is not available: no take can be planned for this run: {v1} is a "
        "rig.yaml of tracks, and takes are planned only for a rig of channels, where "
        "each channel has an output of its own to record; run it with "
        "`sushi-rig up --rig <name>`"
    )


def test_no_rig_file_at_all(tmp_path):
    said = no_engine(record_session(None, None, engine=StandInEngine))
    assert said == (
        "recording is not available: no take can be planned for this run: this run has "
        "no rig file (the listener was given no --rig), so there is nothing to say which "
        "ports a take records"
    )


def test_no_config_to_carry(duo, tmp_path):
    rig_path, _, machine_path = duo
    said = no_engine(record_session(rig_path, None, machine_file=machine_path,
                                    engine=StandInEngine))
    assert said.endswith(
        "the listener was given no --panel-config, so it does not know the config "
        "Sushi loaded, which every take carries")


def test_a_config_with_no_sample_rate(duo, tmp_path):
    rig_path, config_path, machine_path = duo
    config = json.loads(config_path.read_text())
    del config["host_config"]["samplerate"]
    config_path.write_text(json.dumps(config))
    said = no_engine(session_for(duo, tmp_path))
    assert said.endswith(f"{config_path} states no sample rate (host_config.samplerate is None)")


def test_a_machine_file_that_is_not_there(duo, tmp_path):
    rig_path, config_path, _ = duo
    said = no_engine(session_for((rig_path, config_path, tmp_path / "gone.yaml"), tmp_path))
    assert said.startswith("recording is not available: no take can be planned for this "
                           "run: the machine file names the dry taps, and cannot read "
                           "machine file")


def test_a_plan_problem_is_named(duo, tmp_path):
    rig_path, config_path, _ = duo
    odd = write(tmp_path / "odd.yaml", MACHINE.replace("keys-in", "piano-in"))
    said = no_engine(session_for((rig_path, config_path, odd), tmp_path))
    assert said == (
        "recording is not available: no take can be planned for this run: channel "
        "'keys' plays from 'keys-in', which the machine file gives no ports, so it has "
        "no dry tap"
    )


def test_an_engine_that_cannot_be_made_is_named(duo, tmp_path):
    def broken(rate, quantum):
        raise RuntimeError("no libjack today")

    said = no_engine(session_for(duo, tmp_path, engine=broken))
    assert said == "recording is not available: the capture engine could not be made: no libjack today"


def test_nothing_unexpected_stops_the_listener_starting(duo, tmp_path, monkeypatch):
    def explode(*a, **k):
        raise KeyError("surprise")

    monkeypatch.setattr(transport, "rig_stems", explode)
    said = no_engine(session_for(duo, tmp_path))
    assert said == ("recording is not available: no take can be planned for this run: "
                    "unexpected error: 'surprise'")


def test_record_on_a_refused_run_shows_the_reason_on_the_panel():
    said = Transport(NoEngine("no take can be planned for this run: because")).on_record()
    assert said == "record failed: recording is not available: no take can be planned for this run: because"
    assert NoEngine().message == NoEngine.MESSAGE


# --------------------------------------------------------------------------- #
# The listener is given it
# --------------------------------------------------------------------------- #

def run_listen(monkeypatch, argv):
    seen = {}
    monkeypatch.setattr("sushi_rig.listen.serve", lambda *a, **kw: seen.update(kw))
    assert cli.main(["listen", *argv]) == 0
    return seen["session"]


def test_the_listener_gets_a_take_session_when_the_pieces_are_there(duo, tmp_path, monkeypatch, capsys):
    rig_path, config_path, machine_path = duo
    monkeypatch.setattr(transport, "_capture_engine", StandInEngine)
    session = run_listen(monkeypatch, [
        "--rig", str(rig_path), "--panel-config", str(config_path),
        "--machine", str(machine_path), "--takes-dir", str(tmp_path / "takes"),
    ])
    assert isinstance(session, TakeSession)
    assert session.plan.takes_dir == tmp_path / "takes"
    assert "RECORD records guitar.wav from sushi:audio_output_2" in capsys.readouterr().err


def test_the_listener_says_at_start_why_record_is_off(monkeypatch, capsys):
    session = run_listen(monkeypatch, [])
    assert isinstance(session, NoEngine)
    assert f"RECORD is off — {session.message}" in capsys.readouterr().err


def test_each_take_carries_the_amp_as_the_listener_last_sent_it(duo, tmp_path, monkeypatch):
    """The session is built with the amp hook over the listener's own dict,
    and `serve` updates that dict, not a copy, as a fader moves."""
    pytest.importorskip("pythonosc")
    import pythonosc.dispatcher
    import pythonosc.osc_server

    from sushi_rig.listen import AMP_ADDRESS_PREFIX, serve

    handlers = {}

    class Dispatcher:
        def map(self, address, handler):
            handlers[address] = handler

    class Server:
        def __init__(self, *a, **k):
            pass

        def serve_forever(self):
            # A fader move arrives, then the listener is stopped.
            handlers[AMP_ADDRESS_PREFIX + "*"](AMP_ADDRESS_PREFIX + "Input Lvl", 0.75)
            raise KeyboardInterrupt

        def server_close(self):
            pass

    monkeypatch.setattr(pythonosc.dispatcher, "Dispatcher", Dispatcher)
    monkeypatch.setattr(pythonosc.osc_server, "BlockingOSCUDPServer", Server)
    amp = {"Input Lvl": 0.25}
    session = session_for(duo, tmp_path, amp_model="amp/x.nam", amp_parameters=amp)
    assert session.amend_config({})["_amp"]["parameters"]["Input Lvl"] == 0.25
    serve(None, tmp_path, tmp_path, amp_model="amp/x.nam", amp_parameters=amp, session=session)
    config = session.amend_config(json.loads(json.dumps(session.plan.config)))
    assert config["_amp"]["model"] == "amp/x.nam"
    assert config["_amp"]["parameters"]["Input Lvl"] == 0.75


def test_a_run_without_an_amp_records_none(duo, tmp_path):
    session = session_for(duo, tmp_path)
    assert "_amp" not in session.amend_config({"_amp": {"model": "stale.nam"}})


def test_a_config_that_cannot_be_made_is_the_takes_own_refusal(duo, tmp_path):
    session = session_for(duo, tmp_path)

    def broken(config):
        raise KeyError("Input Lvl")

    session.amend_config = broken
    said = Transport(session).on_record()
    assert said.startswith("record failed: cannot record take-")
    assert said.endswith("the config it would carry could not be made (KeyError: 'Input Lvl')")
    assert not (tmp_path / "takes").exists()


def test_a_config_at_another_rate_is_refused_when_planned(duo, tmp_path):
    from sushi_rig.rig import RATE

    rig_path, config_path, machine_path = duo
    config = json.loads(config_path.read_text())
    config["host_config"]["samplerate"] = 44100
    config_path.write_text(json.dumps(config))
    said = no_engine(session_for(duo, tmp_path))
    assert said.endswith(
        f"{config_path} runs at 44100 Hz, but the rig runs PipeWire at {RATE} Hz, so a "
        "take could not be captured at the rate it would declare")


def test_up_starts_the_listener_under_pw_jack_with_the_machine_and_quantum(tmp_path):
    from sushi_rig.rig import QUANTUM, RATE, plan_children

    machine = tmp_path / "machines" / "box.yaml"
    listen = next(c for c in plan_children(
        tmp_path, tmp_path / "run" / "duo.json", "config/rigs/duo.yaml",
        headless=False, tuner=False, machine_file=machine,
    ) if c["name"] == "listen")
    command = listen["command"]
    assert command[0] == "pw-jack" and command[2] == "listen"
    assert command[command.index("--machine") + 1] == str(machine.resolve())
    assert listen["env_set"] == {"PIPEWIRE_QUANTUM": f"{QUANTUM}/{RATE}"}
    sushi = next(c for c in plan_children(
        tmp_path, tmp_path / "run" / "duo.json", None, headless=False, tuner=False,
    ) if c["name"] == "sushi")
    assert sushi["env_set"] == listen["env_set"]


# --------------------------------------------------------------------------- #
# A whole take, through the session the listener would run
# --------------------------------------------------------------------------- #

class Pushing:
    """Pushes forty blocks whose columns are the plan's, a different level on
    each, as the capture engine would from PipeWire's ports."""

    def __init__(self, rate, quantum):
        self.quantum = quantum

    def start(self, stems, push):
        np = pytest.importorskip("numpy")
        columns = sum(n for _, n in stems)
        block = np.tile(np.arange(1, columns + 1, dtype=np.float32) / 20, (self.quantum, 1))
        for _ in range(40):
            push(block)

    def stop(self):
        return None


def record(session, name):
    assert session.start(name) == f"recording {name}"
    said = session.stop()
    assert said.startswith(f"recorded {name}"), said


def test_a_take_of_the_planned_stems_checks_clean(tmp_path, monkeypatch):
    """Real folder, real recorder, real stems, and the real capture engine,
    over guitar_vocal on this machine's file, a real pw-dump of the rig, and
    the ports JACK listed on the rig: only JACK itself stands in
    (`jack_fake.py`). The machine file's `Audient iD14:capture_AUX0` is not a
    JACK name, `Audient iD14 Multichannel:capture_AUX0` is, and the take must
    link the second for the first. The stand-in this replaced pushed blocks
    without asking which ports, and so passed while the rig could not record
    at all (2026-11-02)."""
    np = pytest.importorskip("numpy")
    import yaml

    from sushi_rig.take import check_take, load_take, wav_info
    from jack_fake import ALIAS, JACK_NAME, JACK_PORTS, RIG_DUMP, FakeJack

    assert ALIAS not in JACK_PORTS, "the two spellings differ, as they did on the rig"
    levels = {port: (i + 1) / 40 for i, port in enumerate(JACK_PORTS)}
    jack = FakeJack(levels=levels)
    jack.install(monkeypatch)
    config = config_of(load_rig(GUITAR_VOCAL))
    config_path = write(tmp_path / "run" / "guitar_vocal.json", json.dumps(config))
    session = record_session(
        GUITAR_VOCAL, config_path, machine_file=THIS_MACHINE, takes_dir=tmp_path / "takes", root=tmp_path,
        engine=lambda rate, quantum: capture.CaptureEngine(
            rate, quantum, settle_s=0.0, link_retry_s=0.0, read_dump=lambda: RIG_DUMP))
    assert session.start("first-idea") == "recording first-idea"
    for _ in range(40):
        jack.clients[-1].process(256)
    said = session.stop()
    assert said.startswith("recorded first-idea"), said
    folder = tmp_path / "takes" / "first-idea"
    take = load_take(folder)
    assert check_take(take) == [] and take.complete
    meta = yaml.safe_load((folder / "take.yaml").read_text())
    assert {s["file"]: s["port"] for s in meta["stems"]}["guitar-dry.wav"] == ALIAS, "named as the machine file names it"
    assert meta["capture"]["engine"]["sources"]["guitar-dry.wav"] == JACK_NAME, "linked as JACK names it"
    from sushi_rig.signals import read_wav

    dry = read_wav(folder / "guitar-dry.wav").samples
    assert np.allclose(dry, levels[JACK_NAME]), "the dry stem holds the guitar's port, not silence"
    assert wav_info(folder / "master.wav").frames == 40 * 256


def test_a_shared_dry_tap_makes_a_take_that_checks_clean(tmp_path):
    """`take check` accepts a dry stem named for one of its rig's inputs."""
    pytest.importorskip("numpy")
    from sushi_rig.take import check_take, load_take

    rig_path = write(tmp_path / "config" / "rigs" / "blend.yaml", BLEND)
    config_path = write(tmp_path / "run" / "blend.json", json.dumps(config_of(load_rig(rig_path))))
    paths = (rig_path, config_path, write(tmp_path / "machines" / "box.yaml", MACHINE))
    record(session_for(paths, tmp_path, engine=Pushing), "blend-idea")
    folder = tmp_path / "takes" / "blend-idea"
    assert (folder / "guitar-in-dry.wav").is_file()
    assert check_take(load_take(folder)) == []


def test_the_rig_file_and_library_are_read_at_each_record(duo, tmp_path, monkeypatch):
    """A SAVE rewrites the rig file and a commit moves the library while the
    listener runs; each take says where they were when it was made."""
    pytest.importorskip("numpy")
    commits = iter(["aaa", "bbb"])
    monkeypatch.setattr(transport, "library_state",
                        lambda root: {"commit": next(commits), "dirty": False})
    session = session_for(duo, tmp_path, engine=Pushing)
    record(session, "before")
    saved = TWO_CHANNELS.replace("name: duo", "name: duo\ndescription: saved since")
    duo[0].write_text(saved)
    record(session, "after")
    takes = tmp_path / "takes"
    assert (takes / "before" / "rig.yaml").read_text() == TWO_CHANNELS
    assert (takes / "after" / "rig.yaml").read_text() == saved
    import yaml

    assert yaml.safe_load((takes / "before" / "take.yaml").read_text())["library"]["commit"] == "aaa"
    assert yaml.safe_load((takes / "after" / "take.yaml").read_text())["library"]["commit"] == "bbb"
