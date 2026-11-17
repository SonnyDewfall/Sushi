"""The four pedals of #165: GxTremolo, GxFlanger, Gxshimmizita (a shimmer
reverb) and GxEchoCat (a tape echo), all from guitarix-lv2.

The fast half needs nothing installed: each is in the inventory
(`plugin-manifest.txt`, `plugin-ports.txt`) with the ports lilv reads, and
each profile is clean against its plugin's probe, frozen in
`fixtures/probe_gx*.json` (`sushi-rig probe <uri> --json` over Ubuntu 24.04's
guitarix-lv2 43.0, 2026-11-17). The slow half loads each in a real Sushi
1.3.0, alone on a mono channel as the compiler builds one, through the master:
it comes back finite, on both sides, and different from the same channel with
the pedal bypassed. Nothing here reads a rig or a preset: the settings are
the test's own (the same as the presets in config/channels/ were made with,
but the owner may change those).
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from sushi_rig import plugins, stereo
from sushi_rig.paths import checkout_root
from sushi_rig.profile import driven_value, dump_profile, load_profile, validate

HERE = Path(__file__).parent
ROOT = checkout_root()
PROFILES = HERE.parent / "examples" / "profiles"
SUSHI = ROOT / "sushi"

G = "http://guitarix.sourceforge.net/plugins/"
TREMOLO = G + "gx_tremolo#_tremolo"
FLANGER = G + "gx_flanger#_flanger"
SHIMMER = G + "gx_shimmizita_#_shimmizita_"
ECHO = G + "gxechocat#echocat"

# uri: (profile, frozen probe, audio in and out as lilv reads them)
PEDALS = {
    TREMOLO: ("gxtremolo.yaml", "probe_gxtremolo.json", (1, 1)),
    FLANGER: ("gxflanger.yaml", "probe_gxflanger.json", (1, 1)),
    SHIMMER: ("gxshimmizita.yaml", "probe_gxshimmizita.json", (2, 2)),
    ECHO: ("gxechocat.yaml", "probe_gxechocat.json", (1, 1)),
}
IDS = {uri: name.split(".")[0] for uri, (name, _, _) in PEDALS.items()}


def facts(uri: str) -> dict:
    return json.loads((HERE / "fixtures" / PEDALS[uri][1]).read_text())


def profile(uri: str) -> dict:
    return load_profile(PROFILES / PEDALS[uri][0])


def by_name(prof: dict) -> dict:
    return {p["name"]: p for p in prof["parameters"]}


def shown(prof: dict, visibility: str) -> list[str]:
    return [p["label"]["value"] for p in prof["parameters"] if p["visibility"]["value"] == visibility] + \
        [c["label"]["value"] for c in prof.get("controls", []) if c["visibility"]["value"] == visibility]


# --- the inventory ------------------------------------------------------------

@pytest.mark.parametrize("uri", PEDALS, ids=IDS.get)
def test_each_pedal_is_in_the_manifest_and_the_port_table(uri):
    """What the library drawer lists, `plugins assemble` copies and the
    compiler's copies to stereo are decided by."""
    assert uri in plugins.read_manifest((ROOT / "plugin-manifest.txt").read_text())
    ports = stereo.load_ports(ROOT)[uri]
    assert (ports.inputs, ports.outputs) == PEDALS[uri][2]
    assert (facts(uri)["audio_in"], facts(uri)["audio_out"]) == PEDALS[uri][2], "as probe read them"


# --- the profiles -------------------------------------------------------------

@pytest.mark.parametrize("uri", PEDALS, ids=IDS.get)
def test_each_profile_is_clean_against_its_plugin_and_canonical(uri):
    assert profile(uri)["plugin"]["uri"] == uri
    assert validate(profile(uri), facts(uri)) == []
    path = PROFILES / PEDALS[uri][0]
    assert dump_profile(load_profile(path)) == path.read_text()


def test_the_panels_show_what_a_player_reaches_for():
    assert shown(profile(TREMOLO), "primary") == ["Speed", "Depth", "Dry/Wet"]
    assert shown(profile(FLANGER), "primary") == ["Speed", "Width", "Feedback", "Dry/Wet"]
    assert shown(profile(SHIMMER), "primary") == ["Shift", "Pitch mix", "Decay", "Mix"]
    assert shown(profile(ECHO), "primary") == ["Repeats", "Tempo", "Head 1", "Head 2", "Head 3", "Mix"]
    assert shown(profile(ECHO), "advanced") == ["Input", "Level"]
    for uri in (TREMOLO, FLANGER):
        assert "controls" not in profile(uri), "Mix is the reverb's and the echo's"


@pytest.mark.parametrize("uri", (SHIMMER, ECHO), ids=IDS.get)
def test_the_reverb_and_the_echo_have_one_mix_and_hide_what_it_drives(uri):
    prof = profile(uri)
    (mix,) = [c for c in prof["controls"] if c["label"]["value"] == "Mix"]
    assert mix["visibility"]["value"] == "primary"
    params = by_name(prof)
    for control in prof["controls"]:
        for drive in control["drives"]["value"]:
            assert params[drive["parameter"]]["visibility"]["value"] == "hidden", drive["parameter"]


def test_the_shimmers_mix_is_dry_at_0_and_stops_at_0_3():
    """Its own Dry/Wet is a straight crossfade to a wet about 17 dB over the
    guitar (measured, MANIFEST), so Mix stops at 0.3: past that it is only
    louder."""
    ((drive),) = profile(SHIMMER)["controls"][0]["drives"]["value"]
    assert drive["parameter"] == "DRYWET"
    assert (driven_value(drive, 0), driven_value(drive, 1)) == (0.0, 0.3)


def test_the_echos_mix_is_its_echo_level_and_its_output_is_kept_to_a_safe_range():
    """Swell is the echoes' level and leaves the dry where it is, so Mix is
    Swell, 0 to 1. Output is never shown as itself: it is about 27 dB a unit,
    +78 at its top (measured, MANIFEST). Level drives it from 0.725 to 1.39:
    rendered through Sushi on a synthetic guitar, -12.3 to +5.9 dB of the
    guitar bypassed, level with it at 1.17. The plugin's own default, 1.0,
    is on Level's travel, so an echo added from the library is not off it."""
    controls = {c["id"]: c for c in profile(ECHO)["controls"]}
    ((swell),) = controls["mix"]["drives"]["value"]
    assert swell["parameter"] == "Swell" and (driven_value(swell, 0), driven_value(swell, 1)) == (0.0, 1.0)
    ((output),) = controls["level"]["drives"]["value"]
    assert output["parameter"] == "Output" and (output["at_0"], output["at_1"]) == (0.725, 1.39)
    assert output["at_0"] < 1.0 < 1.17 < output["at_1"] <= 1.4
    assert controls["level"]["visibility"]["value"] == "advanced"
    assert by_name(profile(ECHO))["Output"]["visibility"]["value"] == "hidden"


# --- in Sushi -----------------------------------------------------------------

# Each pedal set to do what it is for, in Sushi's normalised 0 to 1 (the
# rig file's units). The echo and the shimmer at their Mix's 40 and 50 %,
# the echo's Level at 67 % (Output 1.17).
SETTINGS = {
    TREMOLO: {"Dry/Wet": 1.0, "Mode": 1.0, "Depth": 0.6, "Freq": 0.098196},
    FLANGER: {},  # its own defaults: the deepest sweep of the mono flangers (#155)
    SHIMMER: {"DRYWET": 0.15, "SHIFT": 0.666667, "PSDRYWET": 0.7, "T60M": 0.428571},
    ECHO: {"Head 1": 1.0, "Swell": 0.4, "Sustain": 0.35, "Output": 0.2925, "BPM": 0.285714},
}

needs_sushi = pytest.mark.skipif(not SUSHI.exists(), reason="needs the Sushi binary")


def _render(tmp_path: Path, uri: str, bypassed: bool):
    from sushi_rig import rigfile
    from sushi_rig.render import Stimulus, render_offline

    raw = {
        "kind": "rig", "schema_version": 1, "name": "pedal",
        "channels": [{"name": "board", "input": "guitar-in", "width": "mono",
                      "chain": [{"name": "pedal", "uri": uri, "bypassed": bypassed,
                                 "parameters": SETTINGS[uri]}]}],
        "master": {"output": "monitor-out", "chain": []},
    }
    ((_, view),) = rigfile.heard(rigfile.emit_rig(rigfile.parse_rig(raw)))
    name = "bypassed" if bypassed else "on"
    path = tmp_path / f"{name}.json"
    path.write_text(json.dumps(view, indent=2))
    return render_offline(path, ROOT, tmp_path / name, stimulus=Stimulus(duration_s=2.0),
                          sushi_bin=str(SUSHI))["rendered"]


@needs_sushi
@pytest.mark.slow
@pytest.mark.parametrize("uri", PEDALS, ids=IDS.get)
def test_each_pedal_loads_in_sushi_and_makes_a_sound_of_its_own(uri, tmp_path):
    np = pytest.importorskip("numpy")
    from sushi_rig.analysis import channel_balance, check_finite
    from sushi_rig.nulltest import BALANCE_DB, SILENCE_FLOOR_DBFS

    on = _render(tmp_path, uri, bypassed=False)
    dry = _render(tmp_path, uri, bypassed=True)
    assert on.shape == dry.shape and on.shape[0] > 0 and on.shape[1] == 2
    assert check_finite(on) is None, check_finite(on)
    balance = channel_balance(on)
    assert balance.left_dbfs > SILENCE_FLOOR_DBFS and balance.right_dbfs > SILENCE_FLOOR_DBFS, balance
    assert balance.left_dbfs > -60 and balance.right_dbfs > -60, f"a -20 dBFS sweep came back at {balance}"
    if PEDALS[uri][2][1] == 1:
        # One output, copied to both sides by the compiler.
        assert abs(balance.left_dbfs - balance.right_dbfs) <= BALANCE_DB, balance
    changed = float(np.sqrt(np.mean((on - dry) ** 2)) / np.sqrt(np.mean(dry ** 2)))
    assert 20 * math.log10(changed) > -20, f"on differs from bypassed by only {20 * math.log10(changed):.1f} dB"
