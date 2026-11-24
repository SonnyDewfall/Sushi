"""The four pedals of #165 as the rack draws them: each profile, served by
the agent, laid out by rig.js (`unitPanel`) over the parameter info Sushi
would give the plugin (its ports from `fixtures/probe_gx*.json`). The values
are worked out from each profile's own controls (`test_pedals.at`), or are
the plugin's own defaults, which is what a pedal added from the library has;
the committed rigs are read only by the test marked `committed_rigs`.
Nothing here is a real Sushi; that the plugins load and sound is
test_pedals.py's.
"""

from __future__ import annotations

import pytest

pytest.importorskip("aiohttp")

from sushi_rig.profile import driven_value, load_profile  # noqa: E402

from test_pedals import ECHO, FLANGER, SHIMMER, TREMOLO, at, by_name, real_of  # noqa: E402
from test_ui import blank, browser, rigjs, served  # noqa: E402,F401
from ui_fake import FIXTURES, info_of, probe_ports  # noqa: E402

PROFILES = FIXTURES.parent.parent / "examples" / "profiles"
PROBES = {TREMOLO: "probe_gxtremolo.json", FLANGER: "probe_gxflanger.json",
          SHIMMER: "probe_gxshimmizita.json", ECHO: "probe_gxechocat.json"}
FILES = {TREMOLO: "gxtremolo.yaml", FLANGER: "gxflanger.yaml", SHIMMER: "gxshimmizita.yaml", ECHO: "gxechocat.yaml"}


def real_values(uri, normalised: dict[str, float]) -> dict[str, float]:
    prof = load_profile(PROFILES / FILES[uri])
    return {name: real_of(prof, name, value) for name, value in normalised.items()}


# Real values for each card: set to do what it is for.
VALUES = {
    TREMOLO: lambda: {"Dry/Wet": 100.0, "Mode": 1.0, "Depth": 0.6, "Freq": 5.0},
    FLANGER: lambda: {},
    SHIMMER: lambda: real_values(SHIMMER, at(SHIMMER, {"mix": 0.5}, {"SHIFT": 2.0, "PSDRYWET": 0.7, "T60M": 4.0})),
    ECHO: lambda: real_values(ECHO, at(ECHO, {"mix": 0.5, "repeats": 1.0, "level": 1.0},
                                       {"Head 1": 1.0, "BPM": 120.0})),
}


def info(uri, values=None):
    return info_of(probe_ports(FIXTURES / PROBES[uri]), VALUES[uri]() if values is None else values)


def panel(page, uri, values=None):
    return rigjs(page, """
      const profile = (await (await fetch('/profiles')).json()).profiles[arg.uri];
      const p = R.unitPanel(arg.info, profile);
      const names = (cs) => cs.map((c) => c.label);
      return {groups: p.groups.map((g) => [g.label, names(g.controls)]), advanced: names(p.advanced),
              missing: names(p.missing), kinds: Object.fromEntries(p.groups.flatMap((g) => g.controls)
                .map((c) => [c.label, c.control])),
              summary: R.summaryOf({name: 'pedal', panel: p}, {parameter_info: {pedal: arg.info}})};""",
                 {"uri": uri, "info": info(uri, values)})


def positions(page, uri, info_):
    """rig.js's place for each of the profile's controls (`mixPosition`), by id."""
    return rigjs(page, """
      const profile = (await (await fetch('/profiles')).json()).profiles[arg.uri];
      return Object.fromEntries(profile.parameters.filter((p) => p.drives).map((entry) => {
        const mix = R.makeMix(entry, arg.info);
        const values = Object.fromEntries(mix.drives.map((d) => [d.parameter, arg.info[d.parameter].value]));
        return [entry.name, R.mixPosition(mix, values)];
      }));""", {"uri": uri, "info": info_})


def test_the_tremolo_shows_speed_and_depth_then_its_dry_wet(blank):
    out = panel(blank, TREMOLO)
    assert out["groups"] == [["Tremolo", ["Speed", "Depth"]], ["Mix", ["Dry/Wet"]]]
    assert out["advanced"] == ["Wave"] and out["missing"] == []
    assert out["summary"].startswith("Speed 5"), out["summary"]


def test_the_flanger_shows_its_sweep_then_its_dry_wet(blank):
    out = panel(blank, FLANGER)
    assert out["groups"] == [["Sweep", ["Speed", "Width", "Feedback"]], ["Mix", ["Dry/Wet"]]]
    assert out["advanced"] == ["Delay", "Mix"] and out["missing"] == []


def test_the_shimmer_shows_mix_first_and_hides_its_own_dry_wet(blank):
    out = panel(blank, SHIMMER)
    assert out["groups"] == [["Mix", ["Mix"]], ["Shimmer", ["Shift", "Pitch mix"]], ["Space", ["Decay"]]]
    assert out["advanced"] == ["Low decay", "Crossover", "Damping", "Mod depth", "Mod speed", "Mode",
                               "Control", "Envelope"]
    assert out["missing"] == []
    assert out["summary"].startswith("Mix 50 %"), out["summary"]


def test_the_echo_shows_mix_repeats_and_tempo_and_its_three_heads_and_hides_input(blank):
    out = panel(blank, ECHO)
    assert out["groups"] == [["Mix", ["Mix"]], ["Echo", ["Repeats", "Tempo"]],
                             ["Heads", ["Head 1", "Head 2", "Head 3"]]]
    assert out["advanced"] == ["Level"] and out["missing"] == []
    assert out["summary"].startswith("Mix 50 %"), out["summary"]
    # The heads declare an enumeration with no labels, so there is nothing
    # for a selector to list: rig.js draws a whole-number knob, 0 or 1.
    assert {out["kinds"][h] for h in ("Head 1", "Head 2", "Head 3")} == {"knob"}
    assert "Input" not in str(out), "Input raises the dry and the echoes past Level's reach"


@pytest.mark.parametrize("uri, cid", [(SHIMMER, "mix"), (ECHO, "mix"), (ECHO, "repeats"), (ECHO, "level")])
def test_a_control_on_the_page_sets_what_the_profile_says(blank, uri, cid):
    prof = load_profile(PROFILES / FILES[uri])
    (control,) = [c for c in prof["controls"] if c["id"] == cid]
    out = rigjs(blank, """
      const profile = (await (await fetch('/profiles')).json()).profiles[arg.uri];
      const mix = R.makeMix(profile.parameters.find((p) => p.name === arg.cid), arg.info);
      return [0, 0.4, 0.5, 1].map((m) => Object.fromEntries(R.mixTargets(mix, m).map((t) => [t.parameter, t.value])));""",
                {"uri": uri, "cid": cid, "info": info(uri)})
    facts = {name: p["facts"] for name, p in by_name(prof).items()}

    def normalised(name, real):  # what Sushi is sent
        return (real - facts[name]["min"]) / (facts[name]["max"] - facts[name]["min"])

    for step, m in zip(out, (0, 0.4, 0.5, 1)):
        assert step == pytest.approx({d["parameter"]: normalised(d["parameter"], driven_value(d, m))
                                      for d in control["drives"]["value"]}, abs=1e-9)


def test_the_echo_at_the_plugins_own_settings_is_on_its_mix_and_level(blank):
    """Added from the library, the echo has Swell 0, Sustain 0 and Output 1.0:
    Mix and Repeats show 0 % and Level 62 %, each on its curve, so a turn of
    any of them starts from the curve and not from somewhere off it."""
    out = positions(blank, ECHO, info(ECHO, {}))
    assert out["mix"]["v"] == pytest.approx(0, abs=1e-6) and out["mix"]["off"] < 1e-6
    assert out["repeats"]["v"] == pytest.approx(0, abs=1e-6) and out["repeats"]["off"] < 1e-6
    assert out["level"]["v"] == pytest.approx((1.0 - 0.725) / (1.17 - 0.725), abs=1e-3) and out["level"]["off"] < 1e-6


def test_the_shimmer_at_the_plugins_own_settings_is_past_its_mix_and_turns_down_from_there(blank):
    """The documented exception (PR #173): added from the library, the
    shimmer has Dry/Wet 0.5, past Mix's top of 0.15, since an add cannot set
    starting values. The page shows it at 100 %, off the curve and saying so,
    and a turn down takes Dry/Wet from where it is to 0, never up."""
    out = rigjs(blank, """
      const profile = (await (await fetch('/profiles')).json()).profiles[arg.uri];
      const mix = R.makeMix(profile.parameters.find((p) => p.name === 'mix'), arg.info);
      const values = {DRYWET: arg.info.DRYWET.value};
      const anchor = R.mixAnchor(mix, values);
      return {at: R.mixPosition(mix, values), limit: R.MIX_OFF_CURVE,
              down: [0.75, 0.5, 0].map((m) => R.mixPath(mix, anchor, m)[0].value)};""",
                {"uri": SHIMMER, "info": info(SHIMMER, {})})
    assert out["at"]["v"] == pytest.approx(1, abs=1e-6) and out["at"]["off"] > out["limit"]
    assert 0.5 > out["down"][0] > out["down"][1] > out["down"][2] == pytest.approx(0, abs=1e-9)


@pytest.mark.committed_rigs
def test_every_committed_channel_with_these_pedals_is_shown_on_its_controls_curves(blank):
    """Each committed rig's channel with the echo or the shimmer in it
    (whatever there is): the page shows each of its controls on its curve,
    inside its travel, with no "off the curve" note (`MIX_OFF_CURVE`)."""
    from test_pedals import committed_channels

    limit = rigjs(blank, "return R.MIX_OFF_CURVE;")
    for name, chain in committed_channels():
        for entry in chain:
            if entry.get("uri") not in (SHIMMER, ECHO):
                continue
            real = real_values(entry["uri"], entry.get("parameters") or {})
            for cid, at_ in positions(blank, entry["uri"], info(entry["uri"], real)).items():
                assert at_ is not None and at_["off"] < limit, (name, cid, at_)
