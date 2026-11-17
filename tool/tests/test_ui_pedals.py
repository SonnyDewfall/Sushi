"""The four pedals of #165 as the rack draws them: each profile, served by
the agent, laid out by rig.js (`unitPanel`) over the parameter info Sushi
would give the plugin (its ports from `fixtures/probe_gx*.json`, at the
settings the presets in config/channels/ were made with). The echo's and the
shimmer's Mix are rig.js's `makeMix`. Nothing here is a real Sushi; that the
plugins load and sound is test_pedals.py's.
"""

from __future__ import annotations

import pytest

pytest.importorskip("aiohttp")

from sushi_rig.profile import driven_value, load_profile  # noqa: E402

from test_ui import blank, browser, rigjs, served  # noqa: E402,F401
from ui_fake import FIXTURES, info_of, probe_ports  # noqa: E402

G = "http://guitarix.sourceforge.net/plugins/"
PROFILES = FIXTURES.parent.parent / "examples" / "profiles"

# uri: (frozen probe, real values: a pedal set to do what it is for)
PEDALS = {
    "tremolo": (G + "gx_tremolo#_tremolo", "probe_gxtremolo.json",
                {"Dry/Wet": 100.0, "Mode": 1.0, "Depth": 0.6, "Freq": 5.0}),
    "flanger": (G + "gx_flanger#_flanger", "probe_gxflanger.json", {}),
    "shimmer": (G + "gx_shimmizita_#_shimmizita_", "probe_gxshimmizita.json",
                {"DRYWET": 0.15, "SHIFT": 2.0, "PSDRYWET": 0.7, "T60M": 4.0}),
    "echo": (G + "gxechocat#echocat", "probe_gxechocat.json",
             {"Head 1": 1.0, "Swell": 0.4, "Sustain": 0.35, "BPM": 120.0, "Output": 1.17}),
}


def panel(page, which):
    uri, probe, values = PEDALS[which]
    return rigjs(page, """
      const profile = (await (await fetch('/profiles')).json()).profiles[arg.uri];
      const p = R.unitPanel(arg.info, profile);
      const names = (cs) => cs.map((c) => c.label);
      return {groups: p.groups.map((g) => [g.label, names(g.controls)]), advanced: names(p.advanced),
              missing: names(p.missing), kinds: Object.fromEntries(p.groups.flatMap((g) => g.controls)
                .map((c) => [c.label, c.control])),
              summary: R.summaryOf({name: 'pedal', panel: p}, {parameter_info: {pedal: arg.info}})};""",
                 {"uri": uri, "info": info_of(probe_ports(FIXTURES / probe), values)})


def test_the_tremolo_shows_speed_and_depth_then_its_dry_wet(blank):
    out = panel(blank, "tremolo")
    assert out["groups"] == [["Tremolo", ["Speed", "Depth"]], ["Mix", ["Dry/Wet"]]]
    assert out["advanced"] == ["Wave"] and out["missing"] == []
    assert out["summary"].startswith("Speed 5"), out["summary"]


def test_the_flanger_shows_its_sweep_then_its_dry_wet(blank):
    out = panel(blank, "flanger")
    assert out["groups"] == [["Sweep", ["Speed", "Width", "Feedback"]], ["Mix", ["Dry/Wet"]]]
    assert out["advanced"] == ["Delay", "Mix"] and out["missing"] == []


def test_the_shimmer_shows_mix_first_and_hides_its_own_dry_wet(blank):
    out = panel(blank, "shimmer")
    assert out["groups"] == [["Mix", ["Mix"]], ["Shimmer", ["Shift", "Pitch mix"]], ["Space", ["Decay"]]]
    assert out["advanced"] == ["Low decay", "Crossover", "Damping", "Mod depth", "Mod speed", "Mode",
                               "Control", "Envelope"]
    assert out["missing"] == []
    assert out["summary"].startswith("Mix 50 %"), out["summary"]


def test_the_echo_shows_mix_its_repeats_and_tempo_and_its_three_heads(blank):
    out = panel(blank, "echo")
    assert out["groups"] == [["Mix", ["Mix"]], ["Echo", ["Repeats", "Tempo"]],
                             ["Heads", ["Head 1", "Head 2", "Head 3"]]]
    assert out["advanced"] == ["Input", "Level"] and out["missing"] == []
    assert out["summary"].startswith("Mix 40 %"), out["summary"]
    # The heads declare an enumeration with no labels, so there is nothing
    # for a selector to list: rig.js draws a whole-number knob, 0 or 1.
    assert {out["kinds"][h] for h in ("Head 1", "Head 2", "Head 3")} == {"knob"}


@pytest.mark.parametrize("which, cid", [("shimmer", "mix"), ("echo", "mix"), ("echo", "level")])
def test_mix_on_the_page_sets_what_the_profile_says(blank, which, cid):
    uri, probe, values = PEDALS[which]
    controls = load_profile(PROFILES / ("gxshimmizita.yaml" if which == "shimmer" else "gxechocat.yaml"))["controls"]
    (control,) = [c for c in controls if c["id"] == cid]
    out = rigjs(blank, """
      const profile = (await (await fetch('/profiles')).json()).profiles[arg.uri];
      const mix = R.makeMix(profile.parameters.find((p) => p.name === arg.cid), arg.info);
      return [0, 0.4, 0.5, 1].map((m) => Object.fromEntries(R.mixTargets(mix, m).map((t) => [t.parameter, t.value])));""",
                {"uri": uri, "cid": cid, "info": info_of(probe_ports(FIXTURES / probe), values)})
    ranges = {p["name"]: (p["min"], p["max"]) for p in probe_ports(FIXTURES / probe)}

    def normalised(name, real):  # what Sushi is sent
        low, high = ranges[name]
        return (real - low) / (high - low)

    for step, m in zip(out, (0, 0.4, 0.5, 1)):
        assert step == pytest.approx({d["parameter"]: normalised(d["parameter"], driven_value(d, m))
                                      for d in control["drives"]["value"]}, abs=1e-9)


def test_the_echo_at_the_plugins_own_settings_is_on_its_mix_and_level(blank):
    """Added from the library, the echo has Swell 0 and Output 1.0: Mix shows
    0 % and Level 41 %, both on their curves, so a turn of either starts from
    the curve and not from somewhere off it."""
    uri, probe, _ = PEDALS["echo"]
    out = rigjs(blank, """
      const profile = (await (await fetch('/profiles')).json()).profiles[arg.uri];
      return ['mix', 'level'].map((cid) => {
        const mix = R.makeMix(profile.parameters.find((p) => p.name === cid), arg.info);
        const values = Object.fromEntries(mix.drives.map((d) => [d.parameter, arg.info[d.parameter].value]));
        return R.mixPosition(mix, values);
      });""", {"uri": uri, "info": info_of(probe_ports(FIXTURES / probe), {})})
    (mix, level) = out
    assert mix["v"] == pytest.approx(0, abs=1e-6) and mix["off"] < 1e-6
    assert level["v"] == pytest.approx((1.0 - 0.725) / (1.39 - 0.725), abs=1e-4) and level["off"] < 1e-6
