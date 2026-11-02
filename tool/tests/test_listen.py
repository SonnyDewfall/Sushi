"""Tests for the OSC-trigger outcome logic — not the socket/dispatcher
wiring in `serve()`, which needs a real port and is exercised by the
end-to-end verification instead (see the plan for issue #10)."""

import textwrap
from pathlib import Path

import pytest

from sushi_rig.amp import AmpError
from sushi_rig.listen import AMP_ADDRESS_PREFIX, amp_command, handle_move, handle_save, move_direction

RIG_YAML = textwrap.dedent(
    """\
    meta:
      name: test_rig
      version: "1.0"

    host:
      samplerate: 48000

    tracks:
      - name: fx_track
        channels: 2
        inputs:
          - engine_bus: 0
            track_bus: 0
        outputs:
          - engine_bus: 0
            track_bus: 0
        plugins:
          - name: comp
            type: lv2
            uri: http://example.org/comp
    """
)


@pytest.fixture
def rig_path(tmp_path):
    path = tmp_path / "rig.yaml"
    path.write_text(RIG_YAML)
    return path


@pytest.fixture(autouse=True)
def fake_capture(monkeypatch):
    monkeypatch.setattr(
        "sushi_rig.save.capture",
        lambda address: {"processors": {"comp": {"parameters": {"X": 0.5}}}},
    )


def test_handle_save_reports_success(tmp_path, rig_path):
    message = handle_save("bright", rig_path, tmp_path / "config", tmp_path / "archive")
    assert message.startswith("saved bright.json")


def test_a_save_that_cannot_be_checked_says_so(tmp_path, rig_path):
    """The save is reported as done — it is — but an unchecked config must not
    pass for a checked one. There is no Sushi binary in a test environment, so
    this is also the path every unit test takes."""
    message = handle_save(
        "bright", rig_path, tmp_path / "config", tmp_path / "archive",
        sushi_bin="definitely-not-a-real-binary",
    )
    assert message.startswith("saved bright.json")
    assert "could not check it" in message


def test_a_save_is_never_lost_to_a_failing_check(tmp_path, rig_path):
    """The file is written before anything is checked. Losing a dialled-in tone
    because the checker fell over would be far worse than a config with a
    problem the player can see."""
    handle_save(
        "bright", rig_path, tmp_path / "config", tmp_path / "archive",
        sushi_bin="definitely-not-a-real-binary",
    )
    assert (tmp_path / "config" / "bright.json").is_file()
    assert (tmp_path / "config" / "bright.json").exists()


def test_handle_save_never_raises_on_a_hostile_name(tmp_path, rig_path):
    """This is the actual attack surface: a name arriving over
    unauthenticated UDP. The listener must report the failure and keep
    running, not crash the process handling every other request."""
    message = handle_save("../../etc/passwd", rig_path, tmp_path / "config", tmp_path / "archive")
    assert "save failed" in message
    assert not (tmp_path / "config").exists()


def test_handle_save_never_raises_on_missing_rig_source(tmp_path):
    message = handle_save(
        "bright", tmp_path / "missing.yaml", tmp_path / "config", tmp_path / "archive"
    )
    assert "save failed" in message


def test_handle_save_reports_unexpected_errors_without_raising(tmp_path, rig_path, monkeypatch):
    """Anything unexpected (Sushi unreachable, a gRPC error) must also be
    absorbed and reported — not just the validated SaveError cases."""

    def _boom(address):
        raise RuntimeError("gRPC channel unavailable")

    monkeypatch.setattr("sushi_rig.save.capture", _boom)
    message = handle_save("bright", rig_path, tmp_path / "config", tmp_path / "archive")
    assert "save failed" in message
    assert "gRPC channel unavailable" in message


# --- the decisions inside the OSC handlers ----------------------------------
#
# The handlers are closures in `serve()`, which needs a real port; what they
# decide is pulled out so it can be tested, and `serve` only reports it.

def test_an_amp_message_becomes_the_carla_message_and_the_value_to_remember():
    path, payload, name, remembered = amp_command(AMP_ADDRESS_PREFIX + "Input Lvl", (50.0,))
    assert (name, remembered) == ("Input Lvl", 20.0), "what is remembered is what took effect"
    assert path.endswith("/set_parameter_value") and payload[1] == 20.0


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf"), "nan", 10 ** 400])
def test_an_amp_value_that_is_no_number_is_reported_not_sent_as_the_top_of_the_range(value):
    with pytest.raises(AmpError):
        amp_command(AMP_ADDRESS_PREFIX + "Input Lvl", (value,))


def test_an_amp_message_with_no_value_or_a_word_is_reported():
    with pytest.raises(AmpError, match="no value"):
        amp_command(AMP_ADDRESS_PREFIX + "Input Lvl", ())
    with pytest.raises(AmpError, match="non-numeric"):
        amp_command(AMP_ADDRESS_PREFIX + "Input Lvl", ("loud",))


@pytest.mark.parametrize("args, direction", [((), 0), ((1,), 1), ((-1.0,), -1), (("2",), 2), ((0.9,), 0)])
def test_a_move_direction_is_read_as_a_whole_number(args, direction):
    assert move_direction(args) == direction


@pytest.mark.parametrize("bad", [float("inf"), float("-inf"), float("nan"), "up", None])
def test_a_move_direction_that_is_no_number_is_refused_infinity_included(bad):
    """`int(float(inf))` is an OverflowError, which was not caught: the panel
    heard nothing and a traceback went to the log."""
    with pytest.raises(ValueError, match="non-numeric direction"):
        move_direction((bad,))


def test_a_listener_with_no_rig_source_refuses_to_save(tmp_path):
    """`up` starts the listener without --rig for a config whose source it
    cannot find. SAVE then says so instead of writing a different rig."""
    from sushi_rig.listen import NO_SOURCE

    assert handle_save("riff", None, tmp_path, tmp_path / "archive") == NO_SOURCE
    assert not list(tmp_path.glob("*.json"))


def test_a_capture_of_a_different_rig_is_refused_not_written(tmp_path, rig_path, monkeypatch):
    """The silent wrong save the audit found: the capture named processors
    the spec did not have, emit skipped them, and the spec's chain was
    written under the typed name with no settings."""
    monkeypatch.setattr(
        "sushi_rig.save.capture",
        lambda address: {"processors": {"vocal_eq": {"parameters": {"Low": 0.5}}}, "tracks": {}},
    )
    said = handle_save("riff", rig_path, tmp_path, tmp_path / "archive")
    assert said.startswith("save failed:") and "'vocal_eq'" in said and "different rig" in said
    assert not list(tmp_path.glob("*.json"))


def test_a_system_exit_from_library_code_is_a_message_not_the_end(tmp_path, rig_path, monkeypatch):
    def exits(address):
        raise SystemExit("elkpy not found")

    monkeypatch.setattr("sushi_rig.save.capture", exits)
    assert handle_save("riff", rig_path, tmp_path, tmp_path / "archive") == "save failed: elkpy not found"


def test_a_missing_elkpy_is_the_same_message_it_was_when_it_exited(tmp_path, rig_path, monkeypatch):
    """live.py raises `ElkpyMissing` now instead of calling sys.exit; the
    player is told the same thing, and the listener is still not ended."""
    from sushi_rig.live import ElkpyMissing

    def missing(address):
        raise ElkpyMissing("elkpy not found. Install it into the tool's venv")

    monkeypatch.setattr("sushi_rig.save.capture", missing)
    assert handle_save("riff", rig_path, tmp_path, tmp_path / "archive") == \
        "save failed: elkpy not found. Install it into the tool's venv"


def test_a_capture_of_a_different_rig_is_refused_not_written_into_a_rig_of_channels(tmp_path, fake_capture):
    """The autouse capture is of the v1 test rig (`comp` on `fx_track`); folded
    into guitar_vocal it would be a different rig under that name."""
    from sushi_rig.paths import checkout_root

    said = handle_save("riff", checkout_root() / "config" / "rigs" / "guitar_vocal.yaml",
                       tmp_path, tmp_path / "archive")
    assert said.startswith("save failed: the capture names comp")
    assert not (tmp_path / "rigs" / "riff.yaml").exists()


def test_a_rig_of_channels_is_saved_as_one_with_its_comments(tmp_path, monkeypatch):
    """SAVE on a v2 run (#30's last slice): the rig file is updated in place
    through write_rig, so the player's comments survive, and the compiled
    config is never written into config/."""
    import textwrap

    rigs = tmp_path / "config" / "rigs"
    rigs.mkdir(parents=True)
    source = rigs / "riff.yaml"
    source.write_text(textwrap.dedent("""\
        # Hand-written, with a comment worth keeping.
        kind: rig
        schema_version: 1
        name: riff
        channels:
        - name: board
          input: guitar-in
          width: mono
          chain:
          - name: eq          # the one knob that matters
            uri: http://distrho.sf.net/plugins/3BandEQ
            parameters: {Low: 0.4}
        master:
          output: monitor-out
          chain: []
        """))
    monkeypatch.setattr("sushi_rig.save.capture", lambda address: {
        "processors": {"eq": {"bypassed": True, "parameters": {"Low": 0.6}}},
        "tracks": {"board": ["eq", "board_send"], "master": ["master_return"]},
    })
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "run"))
    said = handle_save("riff", source, tmp_path / "config", tmp_path / "archive",
                       sushi_bin="definitely-not-a-real-binary")
    assert said.startswith("saved riff.yaml"), said
    text = source.read_text()
    assert "# Hand-written, with a comment worth keeping." in text
    assert "# the one knob that matters" in text
    assert "Low: 0.6" in text and "bypassed: true" in text
    assert not (tmp_path / "config" / "riff.json").exists(), "the compiled config stays out of config/"
    assert (tmp_path / "run" / "sushi-rig" / "checks" / "riff.json").exists(), "but was compiled for the check"
    assert not (tmp_path / "run" / "sushi-rig" / "rigs").exists(), "and not where the running rig's compiled config lives"




# --------------------------------------------------------------------------- #
# A move from the panel: when the panel is rebuilt
# --------------------------------------------------------------------------- #

def _moved(outcome):
    """A stand-in for `live.move_live_processor` that answers with `outcome`."""
    from sushi_rig.live import Move

    def move(processor, direction, address):
        return Move(outcome, processor, direction, ["a", processor], {})
    return move


def _raising(exc):
    def move(processor, direction, address):
        raise exc
    return move


def _handle(move):
    said, rebuilt = [], []
    handle_move("drive", -1, "somewhere:1", said.append, rebuilt.append, move=move)
    return said, rebuilt


def test_a_confirmed_move_is_said_and_the_panel_rebuilt_on_that_tab():
    said, rebuilt = _handle(_moved("moved"))
    assert said == ["moved drive earlier"]
    assert rebuilt == ["drive"]


@pytest.mark.parametrize("outcome", ["unchanged", "absent"])
def test_a_move_that_sent_nothing_does_not_rebuild_the_panel(outcome):
    said, rebuilt = _handle(_moved(outcome))
    assert len(said) == 1 and rebuilt == []


def test_a_move_sushi_did_not_confirm_may_have_happened_so_the_panel_is_rebuilt_and_says_so():
    """Strictness made a move that Sushi applied but confirmed late raise
    `LiveError`; the listener used to report "move failed" and skip the
    rebuild, leaving the panel's tab order stale with no hint."""
    from sushi_rig.live import LiveError

    said, rebuilt = _handle(_raising(LiveError("Sushi did not confirm the command within 5s")))
    assert rebuilt == ["drive"]
    assert len(said) == 1
    assert "did not confirm" in said[0] and "may have happened" in said[0]
    assert "move failed" not in said[0]


def test_a_move_that_failed_any_other_way_is_a_failure_and_the_panel_is_left_alone():
    from sushi_rig.live import ElkpyMissing

    for exc in (ElkpyMissing("elkpy not found"), ConnectionError("no route")):
        said, rebuilt = _handle(_raising(exc))
        assert said == [f"move failed: {exc}"]
        assert rebuilt == []

def test_a_saved_rig_with_a_midi_map_is_checked_with_its_controller(tmp_path, monkeypatch):
    """SAVE on a mapped rig compiles it for the check through the machine
    file's controller, the same way `prepare_rig` does; without that the
    check would refuse a rig that runs."""
    import json
    import textwrap

    from sushi_rig.listen import _checkable

    root = tmp_path / "checkout"
    (root / "config" / "rigs").mkdir(parents=True)
    (root / "machines").mkdir()
    (root / "machines" / "box.yaml").write_text(textwrap.dedent("""\
        schema_version: 1
        interface: Audient iD14
        roles:
          guitar-in: {ports: ["Audient iD14:capture_AUX0"], mono_to_stereo: true}
          monitor-out: ["Audient iD14:playback_FL", "Audient iD14:playback_FR"]
        controllers:
          mpd218:
            port: "MPD218:MPD218 Port A"
            channel: all
            controls:
              knob1: {cc: 3}
        """))
    source = root / "config" / "rigs" / "blend.yaml"
    source.write_text(textwrap.dedent("""\
        kind: rig
        schema_version: 1
        name: blend
        channels:
        - {name: clean, input: guitar-in, width: mono, chain: []}
        - {name: lead, input: guitar-in, width: mono, chain: [], level: -3}
        master: {output: monitor-out, chain: []}
        midi:
          controller: mpd218
          controls:
          - {control: knob1, blend: [clean, lead]}
        """))
    # A checkout has the compiler's table of plugin ports (stereo.py).
    (root / "plugin-ports.txt").write_text((Path(__file__).resolve().parents[2] / "plugin-ports.txt").read_text())
    monkeypatch.setattr("sushi_rig.paths.checkout_root", lambda: root)
    monkeypatch.setattr("sushi_rig.machine.machine_path", lambda r: root / "machines" / "box.yaml")
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "run"))
    compiled = _checkable(source)
    mappings = json.loads(compiled.read_text())["midi"]["cc_mappings"]
    assert [m["plugin_name"] for m in mappings] == ["clean_send", "lead_send"]


def test_the_save_check_compiles_against_the_machine_file_the_running_rig_used(tmp_path, monkeypatch):
    """`up` records the machine file it compiled against; the listener's check
    reads it rather than assuming this host's default, which may describe a
    different controller (or none). Without a record, the default."""
    import json
    import textwrap

    from sushi_rig.listen import _checkable
    from sushi_rig.state import write_state

    def machine(name, cc):
        path = tmp_path / name
        path.write_text(textwrap.dedent(f"""\
            schema_version: 1
            interface: Audient iD14
            roles:
              guitar-in: {{ports: ["Audient iD14:capture_AUX0"], mono_to_stereo: true}}
              monitor-out: ["Audient iD14:playback_FL", "Audient iD14:playback_FR"]
            controllers:
              mpd218:
                controls:
                  knob1: {{cc: {cc}}}
            """))
        return path

    default, running = machine("default.yaml", 3), machine("running.yaml", 9)
    source = tmp_path / "blend.yaml"
    source.write_text(textwrap.dedent("""\
        kind: rig
        schema_version: 1
        name: blend
        channels:
        - {name: clean, input: guitar-in, width: mono, chain: []}
        - {name: lead, input: guitar-in, width: mono, chain: [], level: -3}
        master: {output: monitor-out, chain: []}
        midi:
          controller: mpd218
          controls:
          - {control: knob1, blend: [clean, lead]}
        """))
    # A checkout has the compiler's table of plugin ports (stereo.py).
    (tmp_path / "plugin-ports.txt").write_text((Path(__file__).resolve().parents[2] / "plugin-ports.txt").read_text())
    monkeypatch.setattr("sushi_rig.paths.checkout_root", lambda: tmp_path)
    monkeypatch.setattr("sushi_rig.machine.machine_path", lambda r: default)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "run"))

    def numbers():
        mappings = json.loads(_checkable(source).read_text())["midi"]["cc_mappings"]
        return {m["cc_number"] for m in mappings}

    assert numbers() == {3}, "no state file: the default"
    write_state({"rig": "config/rigs/blend.yaml", "machine": None})
    assert numbers() == {3}, "a state that records no machine: the default"
    write_state({"rig": "config/rigs/blend.yaml", "machine": str(running)})
    assert numbers() == {9}, "the running rig's own machine file"
