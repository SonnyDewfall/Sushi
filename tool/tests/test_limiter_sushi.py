"""The master's limiter in a real Sushi (#137, option 2): that every rig plays
through it, that it changes nothing below its threshold, and that it catches
a hot input. Slow: each render starts Sushi's offline frontend, so these run
in CI's container (rig-container.yml), which has Sushi 1.3.0 and the plugins.

How a hot input reaches the master: a bare channel, no plugins, its send at
+24 dB (the top of a send's range, `rigfile.LEVEL_RANGE`), fed the null
tests' sweep at -12 dBFS, so the master's chain sees +12 dBFS. Through the
send rather than a stimulus file over full scale, so that nothing outside
Sushi has to carry a sample above 1.0.

What these hold, and why those numbers (measured here first, 2026-11-17, in
Sushi 1.3.0 over Ubuntu 24.04's mda-lv2 1.2.10):

- Below the threshold the limiter is not there at all: a sweep into the
  master at -6 dBFS comes out identical, sample for sample, with it and
  without it. That is why no sound reference changed when it went in.
- At +12 dBFS in, a sweep and a sine come out under full scale with it, at
  about -0.9 dBFS, and at +12 dBFS without it: a hot input caught. The
  limiter's threshold is -1 dBFS, and its own Turtle says it is not a
  brickwall limiter, so "under the ceiling" is held as under full scale
  after its attack (the first 50 ms), and within half a dB of it before.
- Every committed rig's master, as compiled (the player's master chain, then
  the limiter), holds the same: whatever config/rigs/ holds, assumed nothing
  of beyond that it is a valid rig (#133).
- Any render that is not finite, or is silent or one-sided, fails, as the
  null tests' do.

What these do not hold, because it is not so (`tool/spikes/master_limiter/`,
and measured in Sushi here): very loud input gets through. +20 dBFS of noise
came out at up to +4.7 dBFS in Sushi (lv2apply had +2.6), +26 dBFS at about
+12, and +40 dBFS can come out louder than it went in. A signal in opposite
phase on the two sides passes untouched. An infinity reaching it leaves the
master NaN until the rig restarts; a NaN passes, and it recovers.
"""

from __future__ import annotations

import copy
import json

import pytest

np = pytest.importorskip("numpy")

from rig_fixtures import compiled_to_render, valid_committed  # noqa: E402
from sushi_rig import rigfile, signals  # noqa: E402
from sushi_rig.analysis import channel_balance, check_finite  # noqa: E402
from sushi_rig.nulltest import balance_db_of, reverbs_bypassed  # noqa: E402
from sushi_rig.paths import checkout_root  # noqa: E402
from sushi_rig.render import PreparedStimulus, Stimulus, render_offline  # noqa: E402
from sushi_rig.rigfile import LIMITER, LIMITER_SETTINGS  # noqa: E402

ROOT = checkout_root()
SUSHI = ROOT / "sushi"
needs_sushi = pytest.mark.skipif(not SUSHI.exists(), reason="needs the Sushi binary")
pytestmark = [pytest.mark.slow, needs_sushi]

RIGS = valid_committed()
# +24 dB on the send, the sweep at -12 dBFS: +12 dBFS into the master.
SEND_DB = 24.0
HOT_DBFS = 12.0
# After the limiter's attack, and in it (see the module docstring).
SETTLED_S = 0.050
FULL_SCALE_DBFS = 0.0
ATTACK_MARGIN_DB = 0.5


def db(x) -> float:
    return float(20 * np.log10(max(float(np.max(np.abs(x))), 1e-30)))


def hot_master(config: dict) -> dict:
    """`config`'s master, as compiled, fed by one bare channel at +24 dB: the
    channel's own plugins gone, so that what reaches the master is known."""
    ((_, view),) = rigfile.heard(rigfile.emit_rig(rigfile.parse_rig({
        "kind": "rig", "schema_version": 1, "name": "hot",
        "channels": [{"name": "feed", "input": "guitar-in", "width": "mono", "chain": [], "level": SEND_DB}],
        "master": {"output": "monitor-out", "chain": []},
    })))
    master = copy.deepcopy(next(t for t in config["tracks"] if t["name"] == "master"))
    view["tracks"] = [t for t in view["tracks"] if t["name"] != "master"] + [master]
    own = {p["name"] for p in master["plugins"]} | {"master"}
    view["initial_state"] = (
        [e for e in view["initial_state"] if e["processor"] not in own]
        + [e for e in config.get("initial_state", []) if e["processor"] in own])
    return view


def without_limiter(view: dict) -> dict:
    out = copy.deepcopy(view)
    master = next(t for t in out["tracks"] if t["name"] == "master")
    master["plugins"] = [p for p in master["plugins"] if p["name"] != LIMITER]
    out["initial_state"] = [e for e in out["initial_state"] if e["processor"] != LIMITER]
    return out


def render(tmp_path, name, view, stimulus):
    """Rendered, finite, two-sided and balanced as nulltest holds a rig: a
    stereo reverb playing gets its 4 dB, and the same view with its stereo
    reverbs bypassed is rendered too and held to 1 dB (PR #173)."""
    path = tmp_path / f"{name}.json"
    path.write_text(json.dumps(view, indent=2))
    rendered = render_offline(path, ROOT, tmp_path / name, stimulus=stimulus, sushi_bin=str(SUSHI))["rendered"]
    assert check_finite(rendered) is None, f"{name}: the render is not finite"
    balance = channel_balance(rendered)
    assert balance.left_dbfs > -120 and balance.right_dbfs > -120, f"{name}: a side is silent ({balance})"
    limit = balance_db_of(view)
    assert abs(balance.left_dbfs - balance.right_dbfs) <= limit, f"{name}: one-sided ({balance})"
    without = reverbs_bypassed(view)
    if without is not None:
        render(tmp_path, f"{name}-reverbs-bypassed", without, stimulus)
    return rendered


def sweep(level_dbfs):
    return Stimulus(duration_s=1.0, level_dbfs=level_dbfs)


def sine(level_dbfs):
    return Stimulus(kind="sine", duration_s=1.0, level_dbfs=level_dbfs, frequency_hz=440.0)


def assert_caught(name, rendered, sample_rate=48000):
    settled = rendered[int(SETTLED_S * sample_rate):]
    assert db(settled) < FULL_SCALE_DBFS, f"{name}: {db(settled):.2f} dBFS after the attack, not under full scale"
    assert db(rendered) <= FULL_SCALE_DBFS + ATTACK_MARGIN_DB, f"{name}: {db(rendered):.2f} dBFS in the attack"


EMPTY = rigfile.emit_rig(rigfile.parse_rig({
    "kind": "rig", "schema_version": 1, "name": "empty",
    "channels": [{"name": "board", "input": "guitar-in", "width": "mono", "chain": []}],
    "master": {"output": "monitor-out", "chain": []},
}))


def test_below_its_threshold_the_limiter_changes_nothing_sample_for_sample(tmp_path):
    view = hot_master(EMPTY)
    stimulus = sweep(-6.0 - SEND_DB)
    with_it = render(tmp_path, "with", view, stimulus)
    without = render(tmp_path, "without", without_limiter(view), stimulus)
    assert db(with_it) == pytest.approx(-6.0, abs=0.01)
    assert np.array_equal(with_it, without)


@pytest.mark.parametrize("stimulus", [sweep(HOT_DBFS - SEND_DB), sine(HOT_DBFS - SEND_DB)], ids=["sweep", "sine"])
def test_a_hot_input_is_caught_with_it_and_not_without_it(tmp_path, stimulus):
    view = hot_master(EMPTY)
    assert_caught("with the limiter", render(tmp_path, "with", view, stimulus))
    without = render(tmp_path, "without", without_limiter(view), stimulus)
    assert db(without) == pytest.approx(HOT_DBFS, abs=0.05), "the input was hot, and only the limiter caught it"


def test_the_settings_are_what_sushi_runs(tmp_path):
    """Sushi names an LV2 plugin's parameters by `lv2:name`: had the names
    been wrong, Sushi would not load the config (verify.py), and had it run
    at the plugin's own defaults (a -16 dBFS threshold and +2 dB of output
    trim), -6 dBFS would not come out at -6 dBFS. Held directly too."""
    view = hot_master(EMPTY)
    assert next(e for e in view["initial_state"] if e["processor"] == LIMITER) == {
        "processor": LIMITER, **LIMITER_SETTINGS}
    rendered = render(tmp_path, "level", view, sweep(-18.0 - SEND_DB))
    assert db(rendered) == pytest.approx(-18.0, abs=0.01), "at its defaults the output trim would add 2 dB"


def test_a_nan_passes_and_the_master_recovers(tmp_path):
    """A NaN from a plugin upstream (gx_detune_ has made them) is let through
    for as long as it lasts and no longer: the limiter's gain is not
    poisoned by it. (An infinity is: see the module docstring.)"""
    t = np.arange(48000) / 48000
    s = 10 ** ((-20.0 - SEND_DB) / 20) * np.sin(2 * np.pi * 440 * t)
    s[1000:1010] = np.nan
    x = np.stack([s, s], 1)
    wav = tmp_path / "nan.wav"
    signals.write_wav(wav, x, 48000, subtype="float32")
    path = tmp_path / "nan.json"
    path.write_text(json.dumps(hot_master(EMPTY)))
    rendered = render_offline(path, ROOT, tmp_path / "out", prepared=PreparedStimulus(wav, x, Stimulus(), 48000),
                              sushi_bin=str(SUSHI))["rendered"]
    bad = ~np.isfinite(rendered).all(axis=1)
    assert bad.any() and not bad[1100:].any(), "the NaN goes through, and only while it lasts"
    assert db(rendered[2000:]) == pytest.approx(-20.0, abs=0.01)


@pytest.mark.committed_rigs
@pytest.mark.parametrize("rig", RIGS, ids=lambda p: p.stem)
def test_each_committed_rigs_master_plays_through_the_limiter_and_catches_a_hot_input(rig, tmp_path):
    config = compiled_to_render(rig)
    master = next(t for t in config["tracks"] if t["name"] == "master")
    assert master["plugins"][-1]["name"] == LIMITER, f"{rig.name}: the master does not end with the limiter"
    for stimulus in (sweep(HOT_DBFS - SEND_DB), sine(HOT_DBFS - SEND_DB)):
        assert_caught(f"{rig.stem} [{stimulus.kind}]",
                      render(tmp_path, f"{rig.stem}-{stimulus.kind}", hot_master(config), stimulus))


def test_a_panned_master_would_lift_a_side_past_the_limiter(tmp_path):
    """Why the master's pan is held at the centre (PR #172's review): Sushi
    pans the master with its gain, after its plugins, and its pan law lifts
    the side panned to. The agent refuses such a pan; this renders it to show
    what it would do."""
    heard = {}
    for pan in (0.5, 0.0):
        view = hot_master(EMPTY)
        view["initial_state"] = [e for e in view["initial_state"] if e["processor"] != "master"] + [
            {"processor": "master", "parameters": {"pan": pan}}]
        path = tmp_path / f"pan{pan}.json"
        path.write_text(json.dumps(view))
        out = render_offline(path, ROOT, tmp_path / f"out{pan}", stimulus=sine(HOT_DBFS - SEND_DB),
                             sushi_bin=str(SUSHI))["rendered"]
        heard[pan] = db(out[int(SETTLED_S * 48000):, 0])
    assert heard[0.5] < FULL_SCALE_DBFS
    assert heard[0.0] > FULL_SCALE_DBFS and heard[0.0] - heard[0.5] > 2.5, heard
