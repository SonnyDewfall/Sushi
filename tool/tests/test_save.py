import json
import textwrap

import pytest

from sushi_rig.save import SaveError, save_config, validate_name

RIG_YAML = textwrap.dedent(
    """\
    meta:
      name: test_rig
      version: "1.0"
      description: A test rig
      status: wip

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

FAKE_STATE = {
    "processors": {
        "fx_track": {"parameters": {"gain": 0.8}},
        "comp": {"parameters": {"Ratio": 0.27}},
    }
}


@pytest.fixture
def rig_path(tmp_path):
    path = tmp_path / "rig.yaml"
    path.write_text(RIG_YAML)
    return path


@pytest.fixture(autouse=True)
def fake_capture(monkeypatch):
    """save_config must never need a live Sushi to be tested — this is the
    boundary the offline modules are supposed to stay inside (brief §3.1)."""
    calls = []

    def _fake(address):
        calls.append(address)
        return FAKE_STATE

    monkeypatch.setattr("sushi_rig.save.capture", _fake)
    return calls


# --- name validation ---------------------------------------------------


@pytest.mark.parametrize(
    "name",
    ["a", "acoustic_chorus", "bright-lead", "v2", "a" * 64],
)
def test_validate_name_accepts_valid_names(name):
    assert validate_name(name) == name


@pytest.mark.parametrize(
    "name",
    [
        "",
        "../../etc/passwd",
        "/etc/passwd",
        "..",
        ".hidden",
        "Acoustic_Chorus",  # uppercase
        "a b",  # space
        "a" * 65,  # over length
        "-leading-dash",
        "name.json",  # dot
    ],
)
def test_validate_name_rejects_hostile_or_malformed_names(name):
    with pytest.raises(SaveError):
        validate_name(name)


def test_save_config_rejects_invalid_name_before_touching_capture_or_disk(
    tmp_path, rig_path, fake_capture
):
    """The name is validated before any live call or filesystem write — a
    hostile name (this is the actual attack surface, since it arrives over
    unauthenticated UDP via the OSC listener) must fail fast, not partway
    through."""
    out_dir = tmp_path / "config"
    with pytest.raises(SaveError):
        save_config("../escape", rig_path, out_dir, tmp_path / "archive")
    assert fake_capture == []  # capture() never called
    assert not out_dir.exists()


def test_save_config_raises_when_rig_source_missing(tmp_path):
    with pytest.raises(SaveError):
        save_config("newname", tmp_path / "missing.yaml", tmp_path / "config", tmp_path / "archive")


# --- new config: version, meta, structure -------------------------------


def test_save_config_writes_a_loadable_looking_config(tmp_path, rig_path):
    out_dir = tmp_path / "config"
    out_path = save_config("bright", rig_path, out_dir, tmp_path / "archive")

    assert out_path == out_dir / "bright.json"
    config = json.loads(out_path.read_text())
    assert config["host_config"] == {"samplerate": 48000}
    assert config["tracks"][0]["plugins"][0]["name"] == "comp"
    assert config["initial_state"] == [
        {"processor": "fx_track", "parameters": {"gain": 0.8}},
        {"processor": "comp", "parameters": {"Ratio": 0.27}},
    ]


def test_save_config_new_name_gets_version_1_0(tmp_path, rig_path):
    out_path = save_config("bright", rig_path, tmp_path / "config", tmp_path / "archive")
    config = json.loads(out_path.read_text())
    assert config["_meta"]["version"] == "1.0"


def test_save_config_sets_name_and_source_in_meta(tmp_path, rig_path):
    out_path = save_config("bright", rig_path, tmp_path / "config", tmp_path / "archive")
    config = json.loads(out_path.read_text())
    assert config["_meta"]["name"] == "bright"
    assert config["_meta"]["source"] == rig_path.as_posix()


def test_save_config_carries_through_description_and_status_from_source(tmp_path, rig_path):
    """Variants share the originating yaml rather than each getting their
    own source file — description/status ride along from it, only name,
    version and source are overridden."""
    out_path = save_config("bright", rig_path, tmp_path / "config", tmp_path / "archive")
    config = json.loads(out_path.read_text())
    assert config["_meta"]["description"] == "A test rig"
    assert config["_meta"]["status"] == "wip"


# --- re-saving over an existing name: version and archiving -------------


def test_save_config_existing_name_leaves_version_untouched(tmp_path, rig_path):
    """Version bumps are a deliberate act (the project's versioning
    convention) — a save button firing automatically must not invent one."""
    out_dir = tmp_path / "config"
    first = save_config("bright", rig_path, out_dir, tmp_path / "archive")
    first_config = json.loads(first.read_text())
    assert first_config["_meta"]["version"] == "1.0"

    second = save_config("bright", rig_path, out_dir, tmp_path / "archive")
    second_config = json.loads(second.read_text())
    assert second_config["_meta"]["version"] == "1.0"  # untouched, not bumped


def test_save_config_archives_the_previous_file_before_overwriting(tmp_path, rig_path):
    out_dir = tmp_path / "config"
    archive_dir = tmp_path / "archive"

    save_config("bright", rig_path, out_dir, archive_dir)
    archived_before = list((archive_dir / "bright").glob("*.json")) if (
        archive_dir / "bright"
    ).exists() else []
    assert archived_before == []  # nothing to archive on the first save

    save_config("bright", rig_path, out_dir, archive_dir)
    archived_after = list((archive_dir / "bright").glob("*.json"))
    assert len(archived_after) == 1
    assert archived_after[0].name == "v1.0.json"


def test_save_config_archived_copy_carries_archived_at(tmp_path, rig_path):
    out_dir = tmp_path / "config"
    archive_dir = tmp_path / "archive"
    save_config("bright", rig_path, out_dir, archive_dir)
    save_config("bright", rig_path, out_dir, archive_dir)

    archived = json.loads((archive_dir / "bright" / "v1.0.json").read_text())
    assert "archived_at" in archived["_meta"]


def test_save_config_nothing_is_lost_when_re_saving_repeatedly(tmp_path, rig_path):
    """The version never bumps automatically (see the test above), so a
    third save would try to archive to the same v1.0.json path a second
    time — that must not silently clobber the first archive."""
    out_dir = tmp_path / "config"
    archive_dir = tmp_path / "archive"

    save_config("bright", rig_path, out_dir, archive_dir)  # nothing archived yet
    save_config("bright", rig_path, out_dir, archive_dir)  # archives save #1 as v1.0.json
    save_config("bright", rig_path, out_dir, archive_dir)  # must not overwrite that archive

    archived = list((archive_dir / "bright").glob("*.json"))
    assert len(archived) == 2  # both saves' prior states preserved
    names = {p.name for p in archived}
    assert "v1.0.json" in names
    assert any(n != "v1.0.json" for n in names)  # the collision got a distinct name


def test_save_config_different_names_do_not_interact(tmp_path, rig_path):
    out_dir = tmp_path / "config"
    archive_dir = tmp_path / "archive"
    save_config("bright", rig_path, out_dir, archive_dir)
    save_config("dark", rig_path, out_dir, archive_dir)

    assert (out_dir / "bright.json").exists()
    assert (out_dir / "dark.json").exists()
    assert not (archive_dir / "bright").exists()
    assert not (archive_dir / "dark").exists()
