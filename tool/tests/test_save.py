import textwrap

import pytest

from sushi_rig.save import SaveError, validate_name

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
    """A save must never need a live Sushi to be tested — this is the
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


def test_unused_path_claims_what_it_returns_and_never_replaces_anything(tmp_path):
    """Claiming is the check: `exists()` then write let two saves in the same
    second (the CLI and the listener's button) both be told a name was free."""
    from sushi_rig.save import unused_path

    (tmp_path / "v1.0-stamp.json").write_text("first")
    got = [unused_path(tmp_path / "v1.0-stamp", ".json") for _ in range(2)]
    assert [p.name for p in got] == ["v1.0-stamp-2.json", "v1.0-stamp-3.json"]
    assert all(p.exists() for p in got), "claimed on return, before any caller writes"
    assert (tmp_path / "v1.0-stamp.json").read_text() == "first"


def test_unused_path_can_claim_a_directory(tmp_path):
    from sushi_rig.save import unused_path

    (tmp_path / "2026").mkdir()
    got = unused_path(tmp_path / "2026", directory=True)
    assert got.name == "2026-2" and got.is_dir()


# --- save_rig: a rig of channels (#30's last slice) --------------------------

import textwrap as _tw  # noqa: E402

from sushi_rig.save import save_any, save_rig  # noqa: E402

RIG_OF_CHANNELS = _tw.dedent("""\
    # A hand-written rig.
    kind: rig
    schema_version: 1
    name: riff
    channels:
    - name: board
      input: guitar-in
      width: mono
      chain:
      - name: eq   # keep
        uri: urn:eq
        parameters: {Low: 0.4}
    master:
      output: monitor-out
      chain: []
    amp:
      plugin: nam
      model: amp/models/Neve 2.nam
      parameters: {Input Lvl: 0.0, Output Lvl: 6.0, Quality: 1.0}
    """)


@pytest.fixture
def rigs(tmp_path, monkeypatch):
    rigs = tmp_path / "config" / "rigs"
    rigs.mkdir(parents=True)
    (rigs / "riff.yaml").write_text(RIG_OF_CHANNELS)
    monkeypatch.setattr("sushi_rig.save.capture", lambda address: {
        "processors": {"eq": {"parameters": {"Low": 0.6000000238418579}}},
        "tracks": {"board": ["eq", "board_send"], "master": ["master_return"]},
    })
    return rigs


def test_save_rig_updates_its_own_file_in_place_with_comments_and_rounding(rigs):
    out = save_rig("riff", rigs / "riff.yaml")
    assert out == rigs / "riff.yaml"
    text = out.read_text()
    assert "# A hand-written rig." in text and "# keep" in text
    assert "Low: 0.6" in text and "0.6000000" not in text


def test_save_rig_under_a_new_name_is_a_new_file_beside_the_source(rigs):
    out = save_rig("riff-2", rigs / "riff.yaml")
    assert out == rigs / "riff-2.yaml" and out.is_file()
    assert "name: riff-2" in out.read_text()
    assert "Low: 0.4" in (rigs / "riff.yaml").read_text(), "the source is untouched"


def test_save_rig_refuses_a_name_that_is_another_rigs(rigs):
    (rigs / "hall.yaml").write_text(RIG_OF_CHANNELS.replace("name: riff", "name: hall"))
    with pytest.raises(SaveError, match="a rig called hall already exists"):
        save_rig("hall", rigs / "riff.yaml")
    assert "name: hall" in (rigs / "hall.yaml").read_text(), "and it is as it was"


def test_a_run_without_the_amp_leaves_the_files_amp_alone(rigs):
    """`--no-amp` says nothing about the amp; a config's archive could give a
    removed amp back, a rig file's cannot."""
    save_rig("riff", rigs / "riff.yaml", amp_model=None)
    assert "model: amp/models/Neve 2.nam" in (rigs / "riff.yaml").read_text()
    save_rig("riff", rigs / "riff.yaml", amp_model="amp/models/Other.nam", amp_parameters={"Output Lvl": 2.0})
    text = (rigs / "riff.yaml").read_text()
    assert "model: amp/models/Other.nam" in text and "Output Lvl: 2.0" in text


def test_save_any_saves_a_rig_of_channels_and_refuses_a_rig_yaml_of_tracks(rigs, rig_path, tmp_path,
                                                                          fake_capture):
    """`sushi-rig save`: a rig of channels is saved into its own file; a
    rig.yaml of tracks once saved a v1 config, config/<name>.json, and v1
    configs are retired (#218), so it is refused, with nothing captured or
    written."""
    assert save_any("riff", rigs / "riff.yaml") == rigs / "riff.yaml"
    before = sorted(tmp_path.rglob("*"))
    with pytest.raises(SaveError, match="a rig.yaml of tracks.* v1 configs are retired"):
        save_any("bright", rig_path)
    assert fake_capture == [] and sorted(tmp_path.rglob("*")) == before
    with pytest.raises(SaveError, match="rig source not found"):
        save_any("x", tmp_path / "nope.yaml")


def test_a_hostile_name_is_refused_before_capture_or_disk(tmp_path, fake_capture):
    """The name of a new rig file is validated before any live call or file
    write (it once arrived over unauthenticated UDP from the panel's
    listener, and is still the security boundary: NAME_PATTERN), so it fails
    fast, not partway through."""
    rigs = tmp_path / "config" / "rigs"
    rigs.mkdir(parents=True)
    (rigs / "riff.yaml").write_text(RIG_OF_CHANNELS)
    with pytest.raises(SaveError):
        save_rig("../escape", rigs / "riff.yaml")
    assert fake_capture == []  # capture() never called
    assert sorted(p.name for p in rigs.iterdir()) == ["riff.yaml"]


def test_captured_properties_are_checked_not_written(rigs, monkeypatch):
    """Which of a plugin's properties may be set at load is not known from the
    listing; a value read back as "" would be baked into the saved file."""
    monkeypatch.setattr("sushi_rig.save.capture", lambda address: {
        "processors": {"eq": {"parameters": {"Low": 0.5}, "properties": {"file": ""}}},
        "tracks": {"board": ["eq", "board_send"], "master": ["master_return"]},
    })
    text = save_rig("riff", rigs / "riff.yaml").read_text()
    assert "Low: 0.5" in text and "properties" not in text and "file:" not in text


def test_save_rig_takes_a_capture_already_made_and_does_not_capture_again(rigs, monkeypatch):
    """The agent captures on its own connection, under its live lock, and
    hands the capture in (POST /rig/save)."""
    monkeypatch.setattr("sushi_rig.save.capture", lambda address: pytest.fail("captured again"))
    given = {"processors": {"eq": {"parameters": {"Low": 0.25}, "properties": {"f": "x"}}},
             "tracks": {"board": ["eq", "board_send"], "master": ["master_return"]}}
    save_rig("riff", rigs / "riff.yaml", state=given)
    assert "Low: 0.25" in (rigs / "riff.yaml").read_text()
    assert given["processors"]["eq"]["properties"] == {"f": "x"}, "the caller's capture is not changed"


@pytest.mark.parametrize("chain, said", [
    (["board_send"], r"the running rig lacks eq \(on board\), which riff has"),
    (["eq", "comp", "board_send"], "the capture names comp, which riff does not have: .* or a plugin added live"),
])
def test_a_chain_changed_live_is_refused_rather_than_saved_wrong(rigs, monkeypatch, chain, said):
    """A save writes values, bypass and order, not which plugins a chain
    holds: a plugin removed live used to stay in the file with nothing said."""
    monkeypatch.setattr("sushi_rig.save.capture", lambda address: {
        "processors": {}, "tracks": {"board": chain, "master": ["master_return"]}})
    with pytest.raises(SaveError, match=said):
        save_rig("riff", rigs / "riff.yaml")
    assert (rigs / "riff.yaml").read_text() == RIG_OF_CHANNELS


def test_a_rig_whose_file_name_the_name_rule_refuses_saves_in_place(rigs):
    """`Guitar.Vocal.yaml` compiles; saving it in place writes the file the
    rig runs from, so its stem is not a name to check, and its own `name:`
    stays. Only a name chosen for a new file is checked."""
    odd = rigs / "Guitar.Vocal.yaml"
    odd.write_text(RIG_OF_CHANNELS)
    assert save_rig("Guitar.Vocal", odd) == odd
    text = odd.read_text()
    assert "Low: 0.6" in text and "name: riff" in text and "# keep" in text
    with pytest.raises(SaveError, match="invalid config name"):
        save_rig("Guitar.Two", odd)
    with pytest.raises(SaveError, match="invalid config name"):
        save_rig("../escape", odd)
