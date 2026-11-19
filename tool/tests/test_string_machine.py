"""The string machine (#177): string-machine chorus, built from a pinned source
(`scripts/build-string-machine-chorus.sh`), abGate for swells and GxZita_rev1
for the hall, each with a profile.

The fast half needs nothing installed: the build pins both its sources; each
plugin is in the inventory with the ports lilv reads; each profile is clean
against its plugin's probe, frozen in `fixtures/probe_*.json` (`sushi-rig
probe <uri> --json`, 2026-11-18: the chorus from the pinned build, the rest
over Ubuntu 24.04's packages); and the controls the guide names do what it
says of them.

The slow half needs the rig's plugins/ (and Sushi): plugins/ holds the pinned
build, and each plugin, compiled as a mono channel through the master, does
what the rig counts on. The chorus is stereo, adds about 4 ms and treats a
chord as the sum of its notes; abGate switched on swells a note out of
silence and switched off passes it untouched; the hall at Mix 0 % is the
guitar. The rig itself is not read here (the #133 rule): every chain is
written out below.
"""

from __future__ import annotations

import re

import pytest

from sushi_rig import plugins, stereo
from sushi_rig.plugins import read_manifest
from sushi_rig.profile import driven_value, dump_profile, load_profile, validate

from synth_renders import (E_MAJOR, HERE, PLUGINS, ROOT, SR, SUSHI, compiled, normalised, onset, padded, probe,
                           render, rms_db, string)

PROFILES = HERE.parent / "examples" / "profiles"
BUILD_SCRIPT = ROOT / "scripts" / "build-string-machine-chorus.sh"

CHORUS = "http://jpcima.sdf1.org/lv2/string-machine-chorus"
GATE = "http://hippie.lt/lv2/gate"
HALL = "http://guitarix.sourceforge.net/plugins/gx_zita_rev1_stereo#_zita_rev1_stereo"

# uri: (profile, frozen probe, audio in and out as lilv reads them)
PLUGINS_OF = {
    CHORUS: ("string-machine-chorus.yaml", "string-machine-chorus", (1, 2)),
    GATE: ("abgate.yaml", "abgate", (1, 1)),
    HALL: ("gxzita-rev1.yaml", "gxzita-rev1", (2, 2)),
}
IDS = {uri: probe_name for uri, (_, probe_name, _) in PLUGINS_OF.items()}

BUILD_IT = (f"run scripts/build-string-machine-chorus.sh {PLUGINS} "
            "(docs/reference/plugins.md, string-machine chorus, built for the string machine)")

needs_sushi = pytest.mark.skipif(not SUSHI.exists() or not PLUGINS.is_dir(),
                                 reason="needs the Sushi binary and the rig's plugins")


def profile(uri: str) -> dict:
    return load_profile(PROFILES / PLUGINS_OF[uri][0])


def facts(uri: str) -> dict:
    return probe(PLUGINS_OF[uri][1])


def controls(prof: dict) -> dict:
    return {c["id"]: c for c in prof.get("controls", [])}


def drives(prof: dict, cid: str) -> list[tuple]:
    return [(d["parameter"], d["curve"], d["at_0"], d["at_1"]) for d in controls(prof)[cid]["drives"]["value"]]


def shown(prof: dict, visibility: str) -> list[str]:
    return [p["label"]["value"] for p in prof["parameters"] if p["visibility"]["value"] == visibility] + \
        [c["label"]["value"] for c in prof.get("controls", []) if c["visibility"]["value"] == visibility]


def default(prof: dict, name: str) -> float:
    return next(p["facts"]["default"] for p in prof["parameters"] if p["name"] == name)


# --- the build ----------------------------------------------------------------

def pinned() -> dict[str, str]:
    text = BUILD_SCRIPT.read_text()
    found = dict(re.findall(r"^(SM_COMMIT|DPF_COMMIT)=([0-9a-f]{40})\b", text, re.M))
    assert set(found) == {"SM_COMMIT", "DPF_COMMIT"}, f"{BUILD_SCRIPT} pins {found}"
    return found


def test_the_build_pins_string_machine_and_the_dpf_it_records():
    """Both sources by full commit, and the script checks DPF's against what
    string-machine records for its submodule, so neither can move."""
    assert pinned() == {"SM_COMMIT": "7050cb1516fbaf5aaaa2c6cc9999fe5ac100d746",
                        "DPF_COMMIT": "f24e5317cd7b57c8ab36b0987ec29a5579cd5dd2"}
    text = BUILD_SCRIPT.read_text()
    assert 'ls-tree HEAD dpf' in text and 'refusing to build' in text
    assert "BUILT-FROM" in text


# A `git` that answers as the script's three sources would, without the
# network: `rev-parse HEAD` in string-machine's checkout or DPF's (its `dpf/`)
# gives STUB_SM_HEAD or STUB_DPF_HEAD, and `ls-tree HEAD dpf` the commit
# string-machine records for DPF, STUB_DPF_RECORDED. Everything else (init,
# fetch, checkout) succeeds and does nothing.
GIT_STUB = """#!/usr/bin/env bash
dir=""
args=()
while [ $# -gt 0 ]; do
    case "$1" in
        -C) dir="$2"; shift 2 ;;
        -c) shift 2 ;;
        *) args+=("$1"); shift ;;
    esac
done
case "${args[0]}" in
    init) mkdir -p "${args[-1]}" ;;
    rev-parse) case "$dir" in */dpf) echo "$STUB_DPF_HEAD" ;; *) echo "$STUB_SM_HEAD" ;; esac ;;
    ls-tree) echo "160000 commit $STUB_DPF_RECORDED	dpf" ;;
esac
exit 0
"""


def _run_build(tmp_path, sm_head: str, dpf_recorded: str, dpf_head: str):
    import os
    import subprocess

    stub = tmp_path / "bin"
    stub.mkdir()
    (stub / "git").write_text(GIT_STUB)
    (stub / "git").chmod(0o755)
    env = {**os.environ, "PATH": f"{stub}{os.pathsep}{os.environ['PATH']}", "STUB_SM_HEAD": sm_head,
           "STUB_DPF_RECORDED": dpf_recorded, "STUB_DPF_HEAD": dpf_head}
    out = tmp_path / "lv2"
    out.mkdir()
    (out / "string-machine-chorus.lv2").mkdir()
    (out / "string-machine-chorus.lv2" / "manifest.ttl").write_text("# the bundle there before\n")
    done = subprocess.run(["bash", str(BUILD_SCRIPT), str(out)], env=env, capture_output=True, text=True,
                          timeout=60)
    return done, out


OTHER = "0123456789abcdef0123456789abcdef01234567"


@pytest.mark.parametrize("which", ["string-machine", "dpf-recorded", "dpf-fetched"])
def test_the_build_refuses_a_source_that_is_not_its_pin_and_keeps_the_bundle_it_had(tmp_path, which):
    """The script's own checks, run: a string-machine that is not the pinned
    commit, one that records another DPF, and a DPF fetched as something
    else each stop it, non-zero and saying which, before anything is
    compiled, and the bundle already in DIR is left as it was."""
    pins = pinned()
    sm, dpf = pins["SM_COMMIT"], pins["DPF_COMMIT"]
    done, out = _run_build(tmp_path, sm_head=OTHER if which == "string-machine" else sm,
                           dpf_recorded=OTHER if which == "dpf-recorded" else dpf,
                           dpf_head=OTHER if which == "dpf-fetched" else dpf)
    assert done.returncode != 0, done.stdout + done.stderr
    assert OTHER in done.stderr, done.stderr
    assert ("refusing to build" in done.stderr) if which == "dpf-recorded" else ("not the pinned" in done.stderr)
    assert sorted(p.name for p in out.iterdir()) == ["string-machine-chorus.lv2"], "staging left behind"
    assert (out / "string-machine-chorus.lv2" / "manifest.ttl").read_text() == "# the bundle there before\n"


# --- the inventory ------------------------------------------------------------

@pytest.mark.parametrize("uri", PLUGINS_OF, ids=IDS.get)
def test_each_plugin_is_in_the_manifest_and_the_port_table(uri):
    assert uri in read_manifest((ROOT / "plugin-manifest.txt").read_text())
    ports = stereo.load_ports(ROOT)[uri]
    assert (ports.inputs, ports.outputs) == PLUGINS_OF[uri][2]
    assert (facts(uri)["audio_in"], facts(uri)["audio_out"]) == PLUGINS_OF[uri][2], "as probe read them"


# --- the profiles -------------------------------------------------------------

@pytest.mark.parametrize("uri", PLUGINS_OF, ids=IDS.get)
def test_each_profile_is_clean_against_its_plugin_and_canonical(uri):
    """abGate declares no version, so its pin (Ubuntu's abgate 1.2.0) is the
    one warning, and unverifiable: lilv reads none."""
    prof = profile(uri)
    assert prof["plugin"]["uri"] == uri
    findings = validate(prof, facts(uri))
    assert [f.code for f in findings] == (["unverified-version"] if uri == GATE else [])
    path = PROFILES / PLUGINS_OF[uri][0]
    assert dump_profile(load_profile(path)) == path.read_text()


def test_the_panels_show_what_the_guide_says_to_try():
    assert shown(profile(CHORUS), "primary") == ["Depth", "Mix"]
    assert shown(profile(HALL), "primary") == ["Mix", "Size"]
    assert shown(profile(GATE), "primary") == ["Swell", "Threshold"]
    assert "Gate on" in shown(profile(GATE), "advanced"), "abGate's own switch can be found"


@pytest.mark.parametrize("uri", (CHORUS, HALL), ids=IDS.get)
def test_what_a_control_drives_is_never_shown_as_itself(uri):
    prof = profile(uri)
    vis = {p["name"]: p["visibility"]["value"] for p in prof["parameters"]}
    for control in prof["controls"]:
        for drive in control["drives"]["value"]:
            assert vis[drive["parameter"]] == "hidden", drive["parameter"]


def test_the_ensembles_mix_brings_the_plain_guitar_in_under_it():
    """Mix moves only the dry: 100 % is the plugin's own -60 dB (none), as a
    chorus added from the library starts; 0 % is -3 dB, as loud as the
    ensemble in the rig. Cosine, so the dry falls quickly from 0 %: half
    way it is about -20 dB."""
    prof = profile(CHORUS)
    assert drives(prof, "mix") == [("Dry gain", "cosine", -3.0, -60.0)]
    (drive,) = controls(prof)["mix"]["drives"]["value"]
    assert default(prof, "Dry gain") == -60.0
    assert driven_value(drive, 1.0) == pytest.approx(-60.0)
    assert driven_value(drive, 0.5) == pytest.approx(-19.7, abs=0.1)


def test_the_ensembles_level_stops_at_0_db():
    """Wet gain runs to +20 dB (review of PR #186): hidden, and set by Level,
    -24 to 0 dB, the rig's -3 at 88 % and the plugin's own -6 at 75 %."""
    prof = profile(CHORUS)
    assert drives(prof, "level") == [("Wet gain", "linear", -24.0, 0.0)]
    assert controls(prof)["level"]["visibility"]["value"] == "advanced"
    assert next(p for p in prof["parameters"] if p["name"] == "Wet gain")["visibility"]["value"] == "hidden"


def test_the_halls_size_moves_both_decays_and_its_mix_runs_dry_to_wet():
    prof = profile(HALL)
    assert drives(prof, "size") == [("MID_RT60", "linear", 1.0, 8.0), ("LOW_RT60", "linear", 1.0, 8.0)]
    assert drives(prof, "mix") == [("DRY_WET_MIX", "linear", -1.0, 1.0)]
    (mix,) = controls(prof)["mix"]["drives"]["value"]
    assert driven_value(mix, 0.5) == default(prof, "DRY_WET_MIX") == 0.0, "the plugin's own setting is 50 %"


def test_abgates_swell_is_its_attack_and_its_own_switch_is_not_hidden():
    """Its Switch is off by default, and off it passes everything (measured,
    below): hidden, an abGate added from the library would be a gate that
    can never close. So it is shown, under More, and says so."""
    params = {p["name"]: p for p in profile(GATE)["parameters"]}
    assert params["Attack"]["label"]["value"] == "Swell" and params["Attack"]["phone"]["value"] == 1
    assert params["Switch"]["visibility"]["value"] == "advanced"
    assert default(profile(GATE), "Switch") == 0.0
    assert "passes everything" in params["Switch"]["description"]["value"]


# --- plugins/ -----------------------------------------------------------------

@pytest.mark.slow
def test_plugins_holds_the_pinned_chorus():
    """Slow, as test_octave's check of Rubber Band is: it asks what this
    machine's plugins/ holds. It skips only where there is no plugins/."""
    lilv = pytest.importorskip("lilv")  # noqa: F841
    if not PLUGINS.is_dir():
        pytest.skip("the rig's plugins/ folder is not present (it is untracked)")
    found = plugins.plugins_on(str(PLUGINS))
    assert found.get(CHORUS) == PLUGINS / "string-machine-chorus.lv2", f"{PLUGINS} has no {CHORUS}; {BUILD_IT}"
    stamp = PLUGINS / "string-machine-chorus.lv2" / "BUILT-FROM"
    built = stamp.read_text() if stamp.is_file() else ""
    assert f"string-machine commit {pinned()['SM_COMMIT']}" in built, f"not the pinned build; {BUILD_IT}"
    assert f"DPF commit {pinned()['DPF_COMMIT']}" in built, f"not the pinned build; {BUILD_IT}"


# --- under Sushi --------------------------------------------------------------

def _chorus(**real) -> list[dict]:
    values = normalised(facts(CHORUS), {"Wet gain": -3.0, "Dry gain": -60.0, **real})
    return [{"name": "ensemble", "uri": CHORUS, "parameters": values}]


@needs_sushi
@pytest.mark.slow
def test_the_ensemble_is_stereo_and_about_4_ms_late(tmp_path):
    """#166 measured it at 199 samples on a 500 Hz burst, and its two sides
    correlated at 0.27: the spread is the string machine. On a sweep, the
    signal nulltest's balance check plays, the sides are within 1 dB of
    each other. A single plucked note is not: which side is louder depends on
    where the ensemble's slow sweep is when the note starts, and was 3.4 dB
    to the right for every note tried here, since each render starts it in
    the same place."""
    np = pytest.importorskip("numpy")
    t = np.arange(SR // 2) / SR
    burst = np.zeros(2 * SR)
    burst[SR // 2:SR] = 0.3 * np.sin(2 * np.pi * 500 * t)
    y = render(np, tmp_path, "burst", compiled(_chorus()), burst)
    lag = onset(np, y.mean(axis=1), SR // 2)
    assert 0 < lag < 0.008 * SR, f"{lag} samples"
    t = np.arange(5 * SR) / SR
    sweep = 0.1 * np.sin(2 * np.pi * np.cumsum(20 * 1000 ** (t / 5)) / SR)
    y = render(np, tmp_path, "sweep", compiled(_chorus()), sweep)
    assert abs(rms_db(np, y[:, 0]) - rms_db(np, y[:, 1])) < 1.0
    y = render(np, tmp_path, "note", compiled(_chorus()), padded(np, string(np, 220.0)))
    assert np.corrcoef(y[:, 0], y[:, 1])[0, 1] < 0.6, "not spread across the two sides"


@needs_sushi
@pytest.mark.slow
def test_the_ensemble_plays_a_chord_as_the_sum_of_its_strings(tmp_path):
    """What makes it a string machine for chords: it is linear, so six strings
    together come out as the six rendered one at a time, added (#166: a
    residual of -141 dB). Rendered without the master's limiter in the way:
    the chord stays far under it."""
    np = pytest.importorskip("numpy")
    strings = [padded(np, string(np, f, level=0.05)) for f in E_MAJOR]
    config = compiled(_chorus())
    whole = render(np, tmp_path, "chord", config, sum(strings))
    parts = sum(render(np, tmp_path, f"string-{i}", config, s) for i, s in enumerate(strings))
    residual = rms_db(np, whole - parts) - rms_db(np, whole)
    assert residual < -100, f"residual {residual:.1f} dB"


def _gate(switch: float) -> list[dict]:
    values = normalised(facts(GATE), {"Switch": switch, "Threshold": -45.0, "Attack": 500.0, "Hold": 50.0,
                                      "Decay": 400.0})
    return [{"name": "swell", "uri": GATE, "parameters": values}]


@needs_sushi
@pytest.mark.slow
def test_abgate_switched_on_swells_out_of_silence_and_off_passes_the_note(tmp_path):
    """The swell the guide promises, and the reason its Switch is shown: off,
    its default, it does nothing at all."""
    np = pytest.importorskip("numpy")
    x = padded(np, string(np, 220.0))
    start, first = SR // 2, SR // 2 + SR // 10
    dry = rms_db(np, x[start:first])
    on = render(np, tmp_path, "on", compiled(_gate(1.0)), x)[:, 0]
    off = render(np, tmp_path, "off", compiled(_gate(0.0)), x)[:, 0]
    assert rms_db(np, on[start:first]) < dry - 15, "the first 100 ms are not held back"
    assert rms_db(np, on[SR:SR + SR // 2]) > rms_db(np, x[SR:SR + SR // 2]) - 3, "it never opens"
    assert np.max(np.abs(off - x)) < 1e-4, "Switch off changed the sound"


def _hall(mix: float) -> list[dict]:
    values = normalised(facts(HALL), {"MID_RT60": 5.0, "LOW_RT60": 5.0, "DRY_WET_MIX": mix})
    return [{"name": "hall", "uri": HALL, "parameters": values}]


@needs_sushi
@pytest.mark.slow
def test_the_hall_at_mix_0_is_the_guitar_and_at_100_has_no_guitar_in_it(tmp_path):
    np = pytest.importorskip("numpy")
    x = padded(np, string(np, 220.0))
    dry = render(np, tmp_path, "dry", compiled(_hall(-1.0)), x)
    assert np.max(np.abs(dry - x[:, None])) < 1e-4
    wet = render(np, tmp_path, "wet", compiled(_hall(1.0)), x)[:, 0]
    first = slice(SR // 2, SR // 2 + SR // 50)  # 20 ms: before the hall answers
    assert rms_db(np, wet[first]) < rms_db(np, x[first]) - 60
