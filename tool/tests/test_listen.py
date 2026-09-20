"""Tests for the OSC-trigger outcome logic — not the socket/dispatcher
wiring in `serve()`, which needs a real port and is exercised by the
end-to-end verification instead (see the plan for issue #10)."""

import textwrap

import pytest

from sushi_rig.listen import handle_save

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
