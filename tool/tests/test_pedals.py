"""The four pedals of #165: GxTremolo, GxFlanger, Gxshimmizita (a shimmer
reverb) and GxEchoCat (a tape echo), all from guitarix-lv2.

The fast half needs nothing installed: each is in the inventory
(`plugin-manifest.txt`, `plugin-ports.txt`) with the ports lilv reads, each
profile is clean against its plugin's probe, frozen in
`fixtures/probe_gx*.json` (`sushi-rig probe <uri> --json` over Ubuntu 24.04's
guitarix-lv2 43.0, 2026-11-17), and the controls that keep the echo and the
shimmer safe end where the guide says they do.

The slow half loads each in a real Sushi 1.3.0, alone on a mono channel as the
compiler builds one, through the master, and holds the levels the guide
promises: the echo at the loudest its page allows, its repeats dying away,
its Level's two ends, and the shimmer at its loudest corner and at the
plugin's own settings, which a library add gives it. Settings are worked out from the profiles' own curves, so a curve
widened fails here. A level is the render's against the same channel with the
pedal bypassed, on a -20 dBFS sweep: RMS, and peak against peak.

The rigs in config/rigs/ are the owner's (#133's rule): the tests that read
them are marked `committed_rigs` and ask only what holds of any rig's channel
that has one of the four, whatever there is (the pedals rig, today).
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from sushi_rig import plugins, stereo
from sushi_rig.paths import checkout_root
from sushi_rig.profile import curve_at, driven_value, dump_profile, load_profile, validate

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

# What the guide promises (docs/guide/effects.md), over the guitar bypassed
# on the sweep, and what is asserted. Measured here: the echo at the loudest
# its page allows, +3.1 dB RMS and +10.9 dB peak (2026-11-17), asserted a
# little inside its promise, so a curve widened fails before the promise
# does; the shimmer at its loudest corner (Mix 100 %, Dry/Wet 0.15, both
# decays 8, Pitch mix 0), +5.88 and +16.24 (2026-11-18), asserted at its
# promise itself, the coordinator's +6 dB RMS window: Mix at 0.2 gave +7.7.
# 0.12 dB is margin enough because these renders are the same bits in CI as
# here: the same Sushi 1.3.0 and Ubuntu 24.04's packages, and every sound
# reference, frozen ones calibrated in CI among them, matches across the
# two to the 0.0001 dB they are written at.
ECHO_PROMISE = {"rms": 4.0, "peak": 12.0}
ECHO_HELD = {"rms": 3.5, "peak": 11.0}
SHIMMER_PROMISE = {"rms": 6.0, "peak": 17.0}
SHIMMER_LOUDEST = {"T60M": 8.0, "T60DS": 8.0, "PSDRYWET": 0.0}
# The one setting the page cannot reach but a library add can: the plugin's
# own Dry/Wet, 0.5, past Mix's top (an add cannot set starting values).
SHIMMER_OWN = {"DRYWET": 0.5}


def facts(uri: str) -> dict:
    return json.loads((HERE / "fixtures" / PEDALS[uri][1]).read_text())


def profile(uri: str) -> dict:
    return load_profile(PROFILES / PEDALS[uri][0])


def by_name(prof: dict) -> dict:
    return {p["name"]: p for p in prof["parameters"]}


def controls(prof: dict) -> dict:
    return {c["id"]: c for c in prof.get("controls", [])}


def drives(prof: dict, cid: str) -> list[dict]:
    return controls(prof)[cid]["drives"]["value"]


def shown(prof: dict, visibility: str) -> list[str]:
    return [p["label"]["value"] for p in prof["parameters"] if p["visibility"]["value"] == visibility] + \
        [c["label"]["value"] for c in prof.get("controls", []) if c["visibility"]["value"] == visibility]


def normalised(prof: dict, name: str, real: float) -> float:
    """Sushi's 0 to 1 for a real value, by the range the profile restates."""
    f = by_name(prof)[name]["facts"]
    return (real - f["min"]) / (f["max"] - f["min"])


def real_of(prof: dict, name: str, value: float) -> float:
    f = by_name(prof)[name]["facts"]
    return f["min"] + value * (f["max"] - f["min"])


def at(uri: str, travel: dict[str, float], real: dict[str, float] | None = None) -> dict[str, float]:
    """The rig file's normalised parameters for `travel`, each of the
    profile's controls at a point of its own (0 to 1), and `real` values for
    any other parameter: what the page sends for those knob positions."""
    prof = profile(uri)
    out = {name: normalised(prof, name, value) for name, value in (real or {}).items()}
    for cid, m in travel.items():
        for d in drives(prof, cid):
            out[d["parameter"]] = normalised(prof, d["parameter"], driven_value(d, m))
    return out


def nearest(prof: dict, cid: str, values: dict[str, float]) -> tuple[float, float]:
    """Where a control is for its parameters' normalised `values`, and how far
    the furthest is from the curve there (as a fraction of its range): rig.js
    `mixPosition`'s least squares, on a fine grid."""
    ds = drives(prof, cid)

    def miss(m):
        return [normalised(prof, d["parameter"], driven_value(d, m)) - values[d["parameter"]] for d in ds]

    m = min((i / 4000 for i in range(4001)), key=lambda x: sum(e * e for e in miss(x)))
    return m, max(abs(e) for e in miss(m))


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
    assert shown(profile(ECHO), "primary") == ["Tempo", "Head 1", "Head 2", "Head 3", "Mix", "Repeats"]
    assert shown(profile(ECHO), "advanced") == ["Level"]
    for uri in (TREMOLO, FLANGER):
        assert "controls" not in profile(uri), "Mix is the reverb's and the echo's"


@pytest.mark.parametrize("uri", (SHIMMER, ECHO), ids=IDS.get)
def test_what_a_control_drives_is_never_shown_as_itself(uri):
    prof = profile(uri)
    params = by_name(prof)
    assert controls(prof)["mix"]["visibility"]["value"] == "primary"
    for control in prof["controls"]:
        for drive in control["drives"]["value"]:
            assert params[drive["parameter"]]["visibility"]["value"] == "hidden", drive["parameter"]


def test_the_echos_controls_end_where_its_levels_were_measured():
    """Each of the echo's controls drives one parameter over part of its
    range, to the ends the slow tests below hold to the guide's window.
    Input is not shown at all: it raises the dry and the echoes together,
    +11.6 dB at 1 (review of PR #173), past anything Level could take back.
    Output, about 27 dB a unit and +78 at its top, is reached only as Level."""
    ends = {cid: [(d["parameter"], d["curve"], d["at_0"], d["at_1"]) for d in drives(profile(ECHO), cid)]
            for cid in ("mix", "repeats", "level")}
    assert ends == {"mix": [("Swell", "linear", 0.0, 0.7)], "repeats": [("Sustain", "linear", 0.0, 0.3)],
                    "level": [("Output", "linear", 0.725, 1.17)]}
    params = by_name(profile(ECHO))
    assert params["Input"]["visibility"]["value"] == "hidden"
    assert params["Output"]["visibility"]["value"] == "hidden"


def test_the_echo_at_the_plugins_own_settings_is_on_its_controls_travel():
    """An echo added from the library has the plugin's defaults: each lies on
    its control's travel, so the page shows it there and a turn starts from
    it (Swell 0 and Sustain 0 at 0 %, Output 1.0 at 62 %)."""
    prof = profile(ECHO)
    for cid in ("mix", "repeats", "level"):
        ((d),) = drives(prof, cid)
        default = by_name(prof)[d["parameter"]]["facts"]["default"]
        assert min(d["at_0"], d["at_1"]) <= default <= max(d["at_0"], d["at_1"]), (cid, default)


def test_the_shimmers_mix_stops_at_0_15_and_its_own_setting_is_the_one_exception():
    """Dry/Wet is a straight crossfade to a wet about 17 dB over the guitar
    (measured), so Mix stops at 0.15, where the loudest the page allows is
    +6 dB RMS (the coordinator's window, PR #173). The documented exception:
    the plugin's own Dry/Wet is 0.5, past Mix's top, and a shimmer added from
    the library has it, since an add cannot set starting values. The page
    shows it at 100 %, off the curve, and turning Mix down brings it back."""
    ((drive),) = drives(profile(SHIMMER), "mix")
    assert (drive["parameter"], drive["curve"], drive["at_0"], drive["at_1"]) == ("DRYWET", "linear", 0.0, 0.15)
    default = by_name(profile(SHIMMER))["DRYWET"]["facts"]["default"]
    assert default == SHIMMER_OWN["DRYWET"] == 0.5 and default > drive["at_1"]


# --- the committed rigs' channels ----------------------------------------------

def committed_channels():
    """`(rig / channel, chain)` for each channel of each valid committed rig
    (rig_fixtures.valid_committed). Read inside the tests that are marked,
    never at import."""
    import yaml
    from rig_fixtures import valid_committed

    out = []
    for path in valid_committed():
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        for channel in raw.get("channels") or []:
            out.append((f"{path.stem} / {channel['name']}", channel.get("chain") or []))
    return out


# What the channel presets of #165 and #166 held (#208 removed them; their
# channels are in the pedals, string-machine and synth-lead rigs): the four
# pedals, and the string machine's and the synth lead's plugins. The check
# below was theirs, and stays over these plugins wherever a rig has them.
PRESET_PLUGINS = set(PEDALS) | {
    "http://hippie.lt/lv2/gate",
    "http://jpcima.sdf1.org/lv2/string-machine-chorus",
    "http://guitarix.sourceforge.net/plugins/gx_zita_rev1_stereo#_zita_rev1_stereo",
    "http://breakfastquay.com/rdf/lv2-rubberband#livemono",
    "http://guitarix.sourceforge.net/plugins/gx_bmp_#_bmp_",
    "http://calf.sourceforge.net/plugins/EnvelopeFilter",
}


def _profiled_entries():
    """(channel, plugin, profile) for each plugin of `PRESET_PLUGINS` on a
    committed rig's channel, with its profile from tool/examples/profiles/."""
    from sushi_rig.library import load_profiles

    profiles, problems = load_profiles(PROFILES)
    assert problems == []
    return [(name, entry, profiles[entry["uri"]]) for name, chain in committed_channels()
            for entry in chain if entry.get("uri") in PRESET_PLUGINS and entry.get("uri") in profiles]


@pytest.mark.committed_rigs
def test_every_committed_channel_sits_on_its_profiles_controls():
    """Whatever rigs there are (the pedals rig, the owner's): a plugin whose
    profile has controls is saved where each of them is on its curve, inside
    its travel, as the page would have put it. One off its
    curve would be shown at the nearest point, and one past a control's end
    is past what that control was bounded to (the echo's Output, its Sustain,
    the shimmer's Dry/Wet)."""
    for name, entry, prof in _profiled_entries():
        values = entry.get("parameters") or {}
        for cid, control in controls(prof).items():
            ds = control["drives"]["value"]
            held = {d["parameter"]: values[d["parameter"]] for d in ds if d["parameter"] in values}
            if not held:
                continue  # the plugin's defaults, which test_the_*_own_setting* places
            assert set(held) == {d["parameter"] for d in ds}, f"{name}: {cid} saved in part: {held}"
            for d in ds:
                real = real_of(prof, d["parameter"], held[d["parameter"]])
                low, high = sorted((d["at_0"], d["at_1"]))
                assert low - 1e-6 <= real <= high + 1e-6, f"{name}: {d['parameter']} {real} is past {cid}'s travel"
            m, off = nearest(prof, cid, held)
            assert off < 0.005, f"{name}: {cid} is off its curve by {off:.4f} (nearest {m:.3f})"


# --- in Sushi -----------------------------------------------------------------

needs_sushi = pytest.mark.skipif(not SUSHI.exists(), reason="needs the Sushi binary")

# Each pedal set to do what it is for, in the rig file's 0 to 1, from the
# profile's own controls where it has them: Mix and Repeats at the travel
# given, Level at 100 % (level with the guitar).
DOING_IT = {
    TREMOLO: lambda: {"Dry/Wet": 1.0, "Mode": 1.0, "Depth": 0.6, "Freq": (5.0 - 0.1) / 49.9},
    FLANGER: lambda: {},  # its own defaults: the deepest sweep of the mono flangers (#155)
    SHIMMER: lambda: at(SHIMMER, {"mix": 0.5}, {"SHIFT": 2.0, "PSDRYWET": 0.7, "T60M": 4.0}),
    ECHO: lambda: at(ECHO, {"mix": 0.5, "repeats": 1.0, "level": 1.0}, {"Head 1": 1.0, "BPM": 120.0}),
}
# The echo as loud as its page can make it: every head, Mix, Repeats and
# Level at 100 %, at the tempo that measured loudest and one slow.
ECHO_LOUDEST = {"mix": 1.0, "repeats": 1.0, "level": 1.0}
ALL_HEADS = {"Head 1": 1.0, "Head 2": 1.0, "Head 3": 1.0}


def _chain(uri: str, parameters: dict, bypassed: bool = False) -> dict:
    return {"kind": "rig", "schema_version": 1, "name": "pedal",
            "channels": [{"name": "board", "input": "guitar-in", "width": "mono",
                          "chain": [{"name": "pedal", "uri": uri, "bypassed": bypassed,
                                     "parameters": parameters}]}],
            "master": {"output": "monitor-out", "chain": []}}


def _render(tmp_path: Path, raw: dict, name: str, *, seconds: float = 3.0, prepared=None):
    from sushi_rig import rigfile
    from sushi_rig.render import Stimulus, render_offline

    ((_, view),) = rigfile.heard(rigfile.emit_rig(rigfile.parse_rig(raw)))
    path = tmp_path / f"{name}.json"
    path.write_text(json.dumps(view, indent=2))
    return render_offline(path, ROOT, tmp_path / name, stimulus=Stimulus(duration_s=seconds),
                          prepared=prepared, sushi_bin=str(SUSHI))["rendered"]


def _db(x) -> float:
    np = pytest.importorskip("numpy")
    return 20 * math.log10(float(np.sqrt(np.mean(np.asarray(x) ** 2))) + 1e-30)


def _peak(x) -> float:
    np = pytest.importorskip("numpy")
    return 20 * math.log10(float(np.max(np.abs(x))) + 1e-30)


def _over_bypassed(tmp_path: Path, uri: str, parameters: dict, name: str) -> dict:
    """The render's RMS and peak over the same channel's with the pedal
    bypassed, in dB."""
    from sushi_rig.analysis import check_finite

    on = _render(tmp_path, _chain(uri, parameters), name)
    dry = _render(tmp_path, _chain(uri, parameters, bypassed=True), name + "-bypassed")
    assert on.shape == dry.shape and on.shape[0] > 0
    assert check_finite(on) is None, check_finite(on)
    return {"rms": _db(on) - _db(dry), "peak": _peak(on) - _peak(dry)}


@needs_sushi
@pytest.mark.slow
@pytest.mark.parametrize("uri", PEDALS, ids=IDS.get)
def test_each_pedal_loads_in_sushi_and_makes_a_sound_of_its_own(uri, tmp_path):
    np = pytest.importorskip("numpy")
    from sushi_rig.analysis import channel_balance, check_finite
    from sushi_rig.nulltest import BALANCE_DB, SILENCE_FLOOR_DBFS

    on = _render(tmp_path, _chain(uri, DOING_IT[uri]()), "on", seconds=2.0)
    dry = _render(tmp_path, _chain(uri, DOING_IT[uri](), bypassed=True), "bypassed", seconds=2.0)
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


@needs_sushi
@pytest.mark.slow
@pytest.mark.parametrize("bpm", [360.0, 124.8], ids=["fastest", "slower"])
def test_the_echo_at_the_loudest_its_page_allows_stays_inside_the_guides_window(bpm, tmp_path):
    level = _over_bypassed(tmp_path, ECHO, at(ECHO, ECHO_LOUDEST, {**ALL_HEADS, "BPM": bpm}), "loudest")
    assert level["rms"] <= ECHO_HELD["rms"] < ECHO_PROMISE["rms"], level
    assert level["peak"] <= ECHO_HELD["peak"] < ECHO_PROMISE["peak"], level


@needs_sushi
@pytest.mark.slow
def test_the_echos_repeats_die_away_at_the_most_its_page_allows(tmp_path):
    """Three heads feed back together: at Sustain 0.4, the slowest tempo
    rang on at -35 dBFS for twenty seconds (measured); Repeats stops at 0.3.
    A one-second sweep, then silence: twenty seconds on, the echoes are
    20 dB under where they were three seconds after it."""
    np = pytest.importorskip("numpy")
    from sushi_rig import signals
    from sushi_rig.render import PreparedStimulus, Stimulus

    sweep = signals.log_sweep(48000, 1.0, f_start_hz=20.0, f_end_hz=20000.0, level_dbfs=-20.0, channels=2)
    fed = np.concatenate([sweep, np.zeros((21 * 48000, 2))]).astype(np.float32)
    wav = tmp_path / "sweep-then-silence.wav"
    signals.write_wav(wav, fed, 48000, subtype="float32")
    prepared = PreparedStimulus(wav, fed, Stimulus(), 48000)
    out = _render(tmp_path, _chain(ECHO, at(ECHO, ECHO_LOUDEST, {**ALL_HEADS, "BPM": 24.0})), "tail",
                  prepared=prepared)
    early, late = _db(out[4 * 48000:5 * 48000]), _db(out[20 * 48000:21 * 48000])
    assert late <= early - 20, f"4-5 s {early:.1f}, 20-21 s {late:.1f} dBFS: the repeats do not die away"


@needs_sushi
@pytest.mark.slow
def test_the_echos_level_runs_from_12_db_under_the_guitar_to_level_with_it(tmp_path):
    no_echo = {"Head 1": 1.0, "BPM": 120.0}
    bottom = _over_bypassed(tmp_path, ECHO, at(ECHO, {"mix": 0, "repeats": 0, "level": 0}, no_echo), "level0")
    top = _over_bypassed(tmp_path, ECHO, at(ECHO, {"mix": 0, "repeats": 0, "level": 1}, no_echo), "level1")
    assert -13.0 <= bottom["rms"] <= -12.0, bottom
    assert abs(top["rms"]) <= 0.5, top


@needs_sushi
@pytest.mark.slow
def test_the_shimmer_at_its_own_settings_is_the_louder_exception_the_guide_names(tmp_path):
    """Added from the library: the plugin's own settings, past Mix's top.
    About 11 dB over the guitar (the guide says so); and it is louder than
    Mix at 100 % with the same other knobs, which is why Mix stops short."""
    own = _over_bypassed(tmp_path, SHIMMER, {}, "defaults")
    full = _over_bypassed(tmp_path, SHIMMER, at(SHIMMER, {"mix": 1.0}), "mix100")
    assert 10.0 <= own["rms"] <= 11.5, own
    assert full["rms"] <= own["rms"] - 5, (full, own)


@needs_sushi
@pytest.mark.slow
def test_the_shimmer_at_its_loudest_corner_stays_inside_the_guides_window(tmp_path):
    """Its decays at their longest and Pitch mix at 0: the loudest of a grid
    of its knobs (Pitch mix, Shift, Crossover, Damping, the decays), measured."""
    level = _over_bypassed(tmp_path, SHIMMER, at(SHIMMER, {"mix": 1.0}, SHIMMER_LOUDEST), "loudest")
    assert level["rms"] <= SHIMMER_PROMISE["rms"], level
    assert level["peak"] <= SHIMMER_PROMISE["peak"], level


def _stress(scale: float):
    """After #166's stress signal (`tool/spikes/synth_research/measures.py`),
    without its guitar: a full-scale sweep, full-scale noise, a DC step, a
    full-scale 30 Hz sine, and ten seconds of silence, times `scale`."""
    np = pytest.importorskip("numpy")
    from sushi_rig import signals

    sr = 48000
    rng = np.random.default_rng(3)
    sweep = signals.log_sweep(sr, 5.0, f_start_hz=20.0, f_end_hz=20000.0, level_dbfs=-1.0, channels=1)
    parts = [np.asarray(sweep).reshape(-1), rng.standard_normal(2 * sr) * 0.7,
             np.concatenate([np.full(sr, 0.5), np.zeros(sr)]),
             0.99 * np.sin(2 * np.pi * 30 * np.arange(2 * sr) / sr), np.zeros(10 * sr)]
    mono = np.clip(np.concatenate(parts), -1, 1) * scale
    return np.stack([mono, mono], axis=1).astype(np.float32)


@needs_sushi
@pytest.mark.slow
@pytest.mark.parametrize("shift", [0.0, 6.0, -6.0])
def test_the_shimmer_does_not_run_away_at_its_longest_and_most_shifted(shift, tmp_path):
    """#166's stress signal took Gxshimmizita to +20 dBFS. That is its gain,
    not feedback: the wet is 15 to 25 dB over the input, and the input there
    is full scale. Measured on that signal (2026-11-17): a tenth of the
    input gave exactly a tenth of the output, and in the silence after it
    the tail fell 48 dB in eight seconds at its longest. Held here at every
    knob that feeds the shifter back at its top (the plugin's own Dry/Wet
    0.5, both decays 8, Pitch mix 1) and Shift at 0 and both ends: the output scales with the
    input, and the tail dies away."""
    from sushi_rig import signals
    from sushi_rig.analysis import check_finite
    from sushi_rig.render import PreparedStimulus, Stimulus

    from sushi_rig.render import render_offline

    # The plugin's own Dry/Wet, the wettest a library add reaches, and not
    # the page's Mix: feedback is in the wet, which this makes loudest.
    settings = at(SHIMMER, {}, {**SHIMMER_OWN, "T60M": 8.0, "T60DS": 8.0, "PSDRYWET": 1.0, "SHIFT": shift})
    # The plugin alone on a stereo track, not a compiled channel: the master
    # limiter (#172) holds a full-scale input's +20 dBFS down, which is its
    # job and would read here as the shimmer being non-linear.
    alone = tmp_path / "alone.json"
    alone.write_text(json.dumps({
        "host_config": {"samplerate": 48000},
        "tracks": [{"name": "channel", "channels": 2,
                    "inputs": [{"engine_channel": 0, "track_channel": 0}, {"engine_channel": 1, "track_channel": 1}],
                    "outputs": [{"engine_bus": 0, "track_bus": 0}],
                    "plugins": [{"name": "shimmer", "type": "lv2", "uri": SHIMMER}]}],
        "initial_state": [{"processor": "shimmer", "parameters": settings}],
    }))
    peaks, tails = [], []
    for scale in (1.0, 0.1):
        fed = _stress(scale)
        wav = tmp_path / f"stress-{scale}.wav"
        signals.write_wav(wav, fed, 48000, subtype="float32")
        out = render_offline(alone, ROOT, tmp_path / f"stress-{scale}", sushi_bin=str(SUSHI),
                             prepared=PreparedStimulus(wav, fed, Stimulus(), 48000))["rendered"]
        assert out.shape[0] == fed.shape[0] and check_finite(out) is None
        silence = fed.shape[0] - 10 * 48000
        peaks.append(_peak(out))
        tails.append((_db(out[silence:silence + 48000]), _db(out[silence + 8 * 48000:silence + 9 * 48000])))
    assert peaks[0] - peaks[1] == pytest.approx(20.0, abs=0.5), f"not linear: peaks {peaks}"
    for first, last in tails:
        assert last <= first - 30, f"the tail went from {first:.1f} to {last:.1f} dBFS in eight seconds"


@needs_sushi
@pytest.mark.slow
@pytest.mark.committed_rigs
def test_every_committed_channel_with_these_pedals_renders(tmp_path):
    """Each committed rig's channel with one of the four in it (whatever
    there is), its chain alone on a mono channel: finite, on both sides, and
    not silent. Balance is not asked: the shimmer is a stereo reverb
    (docs/guide/effects.md)."""
    from sushi_rig.analysis import channel_balance, check_finite

    for name, chain in committed_channels():
        if not any(e.get("uri") in PEDALS for e in chain):
            continue
        raw = {"kind": "rig", "schema_version": 1, "name": "channel",
               "channels": [{"name": "board", "input": "guitar-in", "width": "mono", "chain": chain}],
               "master": {"output": "monitor-out", "chain": []}}
        out = _render(tmp_path, raw, name.replace(" / ", "-"))
        assert out.shape[0] > 0 and check_finite(out) is None, name
        balance = channel_balance(out)
        assert balance.left_dbfs > -60 and balance.right_dbfs > -60, (name, balance)
