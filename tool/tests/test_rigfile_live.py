"""The rig compiler against a real Sushi, where the answer is audio (#30).

Its own module because it needs numpy, and `importorskip` mid-module skips
everything in the file: the compiler's pure tests live in test_rigfile.py and
run on a machine with no numpy.
"""

from __future__ import annotations

import json

import pytest

from sushi_rig import rigfile
from sushi_rig.paths import checkout_root

np = pytest.importorskip("numpy")

ROOT = checkout_root()
SUSHI = ROOT / "sushi"
needs_sushi = pytest.mark.skipif(not SUSHI.exists(), reason="needs the Sushi binary")


def _render(tmp_path, name, inputs):
    from sushi_rig.render import Stimulus, render_offline

    class Distinct(Stimulus):
        def render(self, sample_rate):
            fed = super().render(sample_rate).copy()
            fed[:, 1] *= -0.5
            return fed

    config = {"host_config": {"samplerate": 48000}, "tracks": [{
        "name": "t", "channels": 2, "inputs": inputs,
        "outputs": [{"engine_bus": 0, "track_bus": 0}],
        "plugins": [{"uid": "sushi.testing.passthrough", "name": "p", "type": "internal"}],
    }]}
    path = tmp_path / f"{name}.json"
    path.write_text(json.dumps(config))
    return render_offline(path, ROOT, tmp_path / name, stimulus=Distinct(duration_s=0.5),
                          sushi_bin=str(SUSHI))["rendered"]


@needs_sushi
@pytest.mark.slow
def test_one_engine_channel_fills_both_halves_of_a_track(tmp_path):
    """The compiler's mono allocation, which nothing had tried: engine channel
    0 routed to both of a track's channels comes out as input 0 on both sides,
    sample for sample — what the patchbay's duplication gives today."""
    direct = _render(tmp_path, "direct", [{"engine_bus": 0, "track_bus": 0}])
    mono = _render(tmp_path, "mono", rigfile._inputs(0, "mono"))
    assert np.max(np.abs(mono[:, 0] - direct[:, 0])) < 1e-6
    assert np.max(np.abs(mono[:, 1] - direct[:, 0])) < 1e-6
