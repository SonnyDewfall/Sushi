"""Tests for the OSC-trigger outcome logic — not the socket/dispatcher
wiring in `serve()`, which needs a real port and is exercised by the
end-to-end verification instead (see the plan for issue #10)."""

import textwrap

import pytest

from sushi_rig.amp import AmpError
from sushi_rig.listen import AMP_ADDRESS_PREFIX, amp_command, handle_save, move_direction

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
            uri: urn:eq
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
    assert (tmp_path / "run" / "sushi-rig" / "rigs" / "riff.json").exists(), "but was compiled for the check"


