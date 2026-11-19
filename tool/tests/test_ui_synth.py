"""The synth lead's filter and the string machine's ensemble as the rack draws
them (#177): each profile, served by the agent, laid out by rig.js
(`unitPanel`) over the parameter info Sushi would give the plugin (its ports
from `fixtures/probe_*.json`), at the settings the rigs give them, written
out here (the #133 rule). Nothing here is a real Sushi; that Sushi sets the
filter's controls is test_synth_lead.py's.
"""

from __future__ import annotations

import pytest

pytest.importorskip("aiohttp")

from test_ui import blank, browser, rigjs, served  # noqa: E402,F401
from ui_fake import FIXTURES, info_of, probe_ports  # noqa: E402

FILTER = "http://calf.sourceforge.net/plugins/EnvelopeFilter"
CHORUS = "http://jpcima.sdf1.org/lv2/string-machine-chorus"
PROBES = {FILTER: "probe_calf-envelope-filter.json", CHORUS: "probe_string-machine-chorus.json"}

# The synth lead's filter, in the plugin's own units.
LEAD = {"Activation": 8.0, "Resonance": 4.0, "Mode": 1.0, "Upper": 2500.0, "Lower": 300.0, "Mix": 1.0,
        "Output Gain": 0.4}
# The string machine's ensemble.
ENSEMBLE = {"Wet gain": -3.0, "Dry gain": -60.0}


def info(uri, values):
    return info_of(probe_ports(FIXTURES / PROBES[uri]), values)


def panel(page, uri, values):
    return rigjs(page, """
      const profile = (await (await fetch('/profiles')).json()).profiles[arg.uri];
      const p = R.unitPanel(arg.info, profile);
      const names = (cs) => cs.map((c) => c.label);
      const all = p.groups.flatMap((g) => g.controls).concat(p.advanced);
      return {groups: p.groups.map((g) => [g.label, names(g.controls)]), advanced: names(p.advanced),
              missing: names(p.missing), notes: all.flatMap((c) => c.notes || []),
              kinds: Object.fromEntries(all.map((c) => [c.label, c.control])),
              shown: Object.fromEntries(all.filter((c) => !c.drives).map((c) => [c.label, R.formatValue(c, arg.info[c.name].value)])),
              summary: R.summaryOf({name: 'unit', panel: p}, {parameter_info: {unit: arg.info}})};""",
                 {"uri": uri, "info": info(uri, values)})


def positions(page, uri, values):
    """rig.js's place for each of the profile's controls (`mixPosition`), by id."""
    info_ = info(uri, values)
    return rigjs(page, """
      const profile = (await (await fetch('/profiles')).json()).profiles[arg.uri];
      return Object.fromEntries(profile.parameters.filter((p) => p.drives).map((entry) => {
        const mix = R.makeMix(entry, arg.info);
        const values = Object.fromEntries(mix.drives.map((d) => [d.parameter, arg.info[d.parameter].value]));
        return [entry.name, R.mixPosition(mix, values)];
      }));""", {"uri": uri, "info": info_})


def test_the_filter_card_shows_its_sweep_its_shape_and_a_safe_level(blank):
    """Drawn without a note or a missing control (and the page's errors fail
    the fixture), its two gains, linear to x64, nowhere on it: Level stands
    in for the output, and the input is left at 1."""
    out = panel(blank, FILTER, LEAD)
    assert out["groups"] == [["Sweep", ["Sensitivity", "Lower", "Upper"]], ["Filter", ["Resonance", "Mode"]],
                             ["Mix", ["Level"]]]
    assert out["advanced"] == ["Attack", "Release", "Response", "Mix"]
    assert out["missing"] == [] and out["notes"] == []
    assert out["kinds"]["Mode"] == "selector"
    assert out["shown"]["Mode"] == "24dB/oct Lowpass"
    assert out["summary"].startswith("Sensitivity 45 %"), out["summary"]
    for gain in ("Input Gain", "Output Gain", "Activation"):
        assert gain not in out["kinds"], gain


def test_the_filter_at_the_synth_leads_settings_sits_on_its_controls(blank):
    out = positions(blank, FILTER, LEAD)
    assert out["sensitivity"]["v"] == pytest.approx(0.449, abs=0.002)
    assert out["resonance"]["v"] == pytest.approx(0.499, abs=0.002)
    assert out["level"]["v"] == pytest.approx(0.387, abs=0.002)
    assert all(at["off"] < 1e-6 for at in out.values()), out


def test_the_ensemble_card_shows_depth_and_mix_and_a_level_that_stops_at_0_db(blank):
    out = panel(blank, CHORUS, ENSEMBLE)
    assert out["groups"] == [["Ensemble", ["Depth"]], ["Mix", ["Mix"]]]
    assert out["advanced"] == ["Model", "Level", "Fast rate", "Fast depth", "Slow rate", "Slow depth"]
    assert out["missing"] == [] and out["notes"] == []
    assert "Wet gain" not in out["kinds"], "+20 dB is a knob away"
    at = positions(blank, CHORUS, ENSEMBLE)
    assert at["mix"]["v"] == pytest.approx(1, abs=1e-6) and at["level"]["v"] == pytest.approx(0.875, abs=1e-6)
