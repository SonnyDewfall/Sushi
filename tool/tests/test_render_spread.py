"""electric-octave-up against itself, rendered again and again, and the pieces
of its chain alone, to find what makes it differ from itself and, now and
then, render NaN.

The flake (CI, 2 Nov, three times, each passing when re-run unchanged):
`electric-octave-up.json rendered unusable audio: non_finite: ... the first is
at frame N, channel 0`, with N 15296, 57536 and 202688. electric-octave-up and
electric_board are the two configs that differ from themselves between
renders (MANIFEST, 30 Oct), and the only two that run gx_detune_ unbypassed.

What is rendered, `RENDERS` times each, with the null tests' own stimulus:

- electric-octave-up as `nulltest --all` renders it: the rig through its
  master (`render.rig_channels`) and the v1 config whole;
- electric_board's rig, for comparison: it runs gx_detune_ twice, at the
  plugin's default of no shift;
- each plugin of electric-octave-up's chain alone, with the rig's settings,
  compiled as the compiler compiles a one-plugin mono channel, and not
  bypassed, since a bypassed plugin alone would measure only Sushi's bypass;
- electric-octave-up with octave_up's DETUNE a hair under an octave, the one
  setting that keeps the bug below from reaching the output, to price that fix
  against the reference.

What a bench host that runs gx_detune_ alone found first (2026-11-03,
tool/spikes/gx_detune_nan/): its pitch shifter reads one float past the end
of two arrays in every hop, and that float is what the heap left there; when
it is NaN, Inf or large, the output is NaN from that hop on, starting on a
frame 192 past a multiple of 256 (`render.DETUNE_HOP_PHASE`). It only reaches
the output when the shift writes the Nyquist bin, as an octave up does. And
the plugin plans its FFTs with FFTW_MEASURE, which picks an algorithm by
timing it, so two processes round differently. This is what tests that in
Sushi, in CI.

Each target's row goes into a table that `conftest.pytest_terminal_summary`
prints at the end of the run (`spread_table.py`): the CI container's log is
the only place it can be read.

What is asserted is only what the measurement rests on: every render
completes, and any render that is not finite is gx_detune_'s known NaN
(`render.detune_nan`). A NaN anywhere else, or of any other shape, fails.

**Opt-in.** The full table is 520 renders, about 70 s of CI, and it measured
what it was written to (MANIFEST, 2026-11-03), so it is marked `spread` and
deselected unless `SUSHI_RIG_SPREAD=1` (conftest): not skipped, which the
container job would fail on. To print the table again, in the image:

    docker run --rm -e SUSHI_RIG_SPREAD=1 sushi-rig-ci pytest -rs -m spread

What runs by default is the guard, `test_the_octave_up_renders_only_its_known_nan`:
gx_detune_ an octave up alone, `GUARD_RENDERS` times, with the same assertion.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import subprocess
import time

import pytest
import yaml

np = pytest.importorskip("numpy")

import spread_table  # noqa: E402
from rig_fixtures import frozen  # noqa: E402
from sushi_rig import rigfile  # noqa: E402
from sushi_rig.analysis import first_non_finite  # noqa: E402
from sushi_rig.nulltest import compare, fingerprint, from_dict  # noqa: E402
from sushi_rig.paths import checkout_root  # noqa: E402
from sushi_rig.render import (  # noqa: E402
    Stimulus, channels_of, detune_nan, prepare_stimulus, render_offline, rig_channels,
    sushi_argv,
)
from sushi_rig.verify import sushi_env  # noqa: E402

ROOT = checkout_root()
SUSHI = ROOT / "sushi"
# The frozen copies (rig_fixtures.py), which are these two rigs as they were
# when this was measured: the targets below are built from electric-octave-up's
# chain as this file is imported, so on the committed file a save that renamed
# a plugin would stop the whole suite at collection. The references they are
# priced against are still config/sound/'s, calibrated to the committed rigs:
# if those two rigs have changed since, rerun with that in mind.
OCTAVE_UP = frozen("electric-octave-up")
BOARD = frozen("electric_board")

# Forty of each: thirteen targets at about a quarter of a second a render, and
# a fingerprint for each distinct one, is two to four minutes. A NaN in one
# render in a few hundred, which is what CI's history suggests, may well not
# show in forty; what forty does show is which pieces differ from themselves.
RENDERS = 40

# The null tests' own sweep: what `nulltest` and the references use.
STIMULUS = Stimulus()

needs_sushi = pytest.mark.skipif(not SUSHI.exists(), reason="needs the Sushi binary")


def _raw(path) -> dict:
    return yaml.safe_load(path.read_text())


def _compiled(raw: dict) -> tuple[str, dict]:
    """A rig as the null tests render it: compiled as `up --rig` loads it, the
    master alone (`render.rig_channels`), for a rig given as a dict."""
    rig = rigfile.parse_rig(raw)
    spec, fixed = rigfile.compile_rig(rig)
    ((name, view),) = rigfile.heard(rigfile.emit_compiled(rig, spec, fixed))
    return name, view


def _as_nulltest(path) -> tuple[str, dict]:
    (channel,) = rig_channels(path)
    return channel.name, channel.config


def _v1(stem: str) -> tuple[str, dict]:
    path = ROOT / "config" / f"{stem}.json"
    (channel,) = channels_of(json.loads(path.read_text()), path)
    return channel.name, channel.config


def _alone(entry: dict) -> tuple[str, dict]:
    """One plugin of the chain, with its settings, unbypassed, on a mono
    channel as the compiler builds one (its copy to stereo after it when it is
    mono), through the master."""
    entry = {k: v for k, v in entry.items() if k != "bypassed"}
    return _compiled({
        "kind": "rig", "schema_version": 1, "name": f"alone-{entry['name']}",
        "channels": [{"name": "board", "input": "guitar-in", "width": "mono", "chain": [entry]}],
        "master": {"output": "monitor-out", "chain": []},
    })


# Normalised: DETUNE runs -12 to 12 semitones, so 0.99995 is 11.9988, a tenth
# of a cent under the octave. Any shift under 2 keeps the Nyquist bin unwritten
# (`index = k * shift` stops at 255), and any at or over 2 writes it.
JUST_UNDER_AN_OCTAVE = 0.99995


def _just_under() -> tuple[str, dict]:
    raw = copy.deepcopy(_raw(OCTAVE_UP))
    (entry,) = [p for p in raw["channels"][0]["chain"] if p["name"] == "octave_up"]
    entry["parameters"]["DETUNE"] = JUST_UNDER_AN_OCTAVE
    return _compiled(raw)


SECTION_CONFIGS = "the configs, as the null tests render them"
SECTION_ALONE = "each plugin of electric-octave-up's chain alone, its settings, not bypassed"
SECTION_CANDIDATE = "a candidate setting, priced against the reference"


def _targets() -> list[tuple[str, str, object, str | None]]:
    """(section, what, a builder of (name, config), the reference to compare against)."""
    found = [
        (SECTION_CONFIGS, "electric-octave-up (rig)", lambda: _as_nulltest(OCTAVE_UP),
         "electric-octave-up.yaml"),
        (SECTION_CONFIGS, "electric-octave-up.json (v1)", lambda: _v1("electric-octave-up"),
         "electric-octave-up.v1-mono.yaml"),
        (SECTION_CONFIGS, "electric_board (rig)", lambda: _as_nulltest(BOARD), "electric_board.yaml"),
    ]
    for entry in _raw(OCTAVE_UP)["channels"][0]["chain"]:
        plugin = entry["uri"].rsplit("/", 1)[-1].split("#")[0].rsplit(":", 1)[-1]
        label = f"{plugin} [{entry['name']}]"
        found.append((SECTION_ALONE, label, (lambda e=entry: _alone(e)), None))
    found.append((SECTION_CANDIDATE, f"electric-octave-up, octave_up DETUNE {JUST_UNDER_AN_OCTAVE}",
                  _just_under, "electric-octave-up.yaml"))
    return found


TARGETS = _targets()


def _spread(values) -> float | None:
    return None if not values else float(max(values) - min(values))


def _measure(section, what, build, reference, renders, tmp_path, request):
    """Render one target `renders` times; add its row to the table; fail on
    any NaN that is not gx_detune_'s known one."""
    name, config = build()
    path = tmp_path / f"{name}.json"
    path.write_text(json.dumps(config, indent=2))
    # Written once: every render of a target is fed the same file.
    prepared = prepare_stimulus(tmp_path / "stimulus", STIMULUS)

    non_finite, unexplained, prints = [], [], []
    # Fingerprinted once per distinct render: a render identical bit for bit
    # measures the same, and most of these targets render identically.
    by_digest: dict = {}
    started = time.monotonic()
    for _ in range(renders):
        out = render_offline(path, ROOT, tmp_path / "work", sushi_bin=str(SUSHI), prepared=prepared)
        rendered = out["rendered"]
        bad = ~np.isfinite(rendered)  # once per render; everything below reads it
        first = first_non_finite(bad)
        if first is not None:
            frame, channel = first
            non_finite.append((frame, channel, int(bad.sum())))
            if detune_nan(config, rendered, bad) is None:
                unexplained.append(f"frame {frame}, channel {channel}")
            continue
        digest = hashlib.sha1(np.ascontiguousarray(rendered).tobytes()).hexdigest()
        if digest not in by_digest:
            by_digest[digest] = fingerprint(rendered, out["sample_rate"], config=name,
                                            stimulus=out["stimulus"],
                                            stimulus_samples=out["stimulus_samples"])
        prints.append(by_digest[digest])
    seconds = (time.monotonic() - started) / renders

    note = ""
    if reference and prints:
        expected, tolerance = from_dict(yaml.safe_load((ROOT / "config" / "sound" / reference).read_text()))
        against = [compare(expected, fp, tolerance) for fp in prints]
        outside = sum(1 for found in against if found)
        worst = max(against, key=len)
        note = (f"against {reference}: {outside} of {len(prints)} finite renders outside its tolerance"
                + (f"; the worst: {'; '.join(str(f) for f in worst[:3])}"
                   + (f"; and {len(worst) - 3} more" if len(worst) > 3 else "") if worst else ""))

    bands = None
    if prints:
        bands = tuple(_spread([fp.bands[i].level_dbfs for fp in prints]) for i in range(len(prints[0].bands)))
    spread_table.rows(request.config).append(spread_table.Row(
        section=section, what=what, renders=renders, non_finite=tuple(non_finite),
        distinct=len(by_digest),
        rms=_spread([fp.rms_dbfs for fp in prints]), peak=_spread([fp.peak_dbfs for fp in prints]),
        channel_rms=(tuple(_spread([fp.channel_rms_dbfs[c] for fp in prints])
                           for c in range(len(prints[0].channel_rms_dbfs))) if prints else None),
        bands=bands,
        band_names=tuple(f"{b.low_hz / 1000:g}k" if b.low_hz >= 1000 else f"{b.low_hz:g}"
                         for b in (prints[0].bands if prints else ())),
        delays=tuple(sorted({fp.delay_samples for fp in prints if fp.delay_samples is not None})),
        seconds_per_render=seconds, note=note,
    ))
    assert unexplained == [], (
        f"{what}: {len(unexplained)} NaN render(s) that are not gx_detune_'s known one "
        f"(render.detune_nan): {', '.join(unexplained)}"
    )


@needs_sushi
@pytest.mark.slow
@pytest.mark.spread
@pytest.mark.parametrize("section, what, build, reference", TARGETS, ids=[t[1] for t in TARGETS])
def test_rendered_again_and_again(section, what, build, reference, tmp_path, request):
    _measure(section, what, build, reference, RENDERS, tmp_path, request)


# The default run's guard: a handful of renders, about a second, of the one
# piece that renders NaN, asserting any NaN is the known one.
GUARD_RENDERS = 5


@needs_sushi
@pytest.mark.slow
def test_the_octave_up_renders_only_its_known_nan(tmp_path, request):
    (target,) = [t for t in TARGETS if t[1] == "gx_detune_ [octave_up]"]
    _, what, build, reference = target
    _measure("the guard, in every run: gx_detune_ an octave up alone", what, build, reference,
             GUARD_RENDERS, tmp_path, request)


def _tree(pid: int) -> list[int]:
    """`pid` and its descendants, from /proc."""
    found, todo = [], [pid]
    while todo:
        p = todo.pop()
        found.append(p)
        try:
            for task in os.listdir(f"/proc/{p}/task"):
                with open(f"/proc/{p}/task/{task}/children") as f:
                    todo.extend(int(c) for c in f.read().split())
        except OSError:
            pass
    return found


def _threads(pids: list[int]) -> dict[tuple[int, int], tuple[str, int]]:
    """{(pid, tid): (name, user + system clock ticks)} for every thread of `pids`."""
    seen = {}
    for pid in pids:
        try:
            tasks = os.listdir(f"/proc/{pid}/task")
        except OSError:
            continue
        for tid in tasks:
            try:
                with open(f"/proc/{pid}/task/{tid}/stat") as f:
                    stat = f.read()
            except OSError:
                continue
            name = stat[stat.index("(") + 1:stat.rindex(")")]
            fields = stat[stat.rindex(")") + 2:].split()
            seen[(pid, int(tid))] = (name, int(fields[11]) + int(fields[12]))
    return seen


@needs_sushi
@pytest.mark.slow
@pytest.mark.spread
def test_the_threads_of_an_offline_render(tmp_path, request):
    """Whether Sushi's offline frontend renders on more than one thread: its
    source says the file is processed on the calling thread, chunk by chunk
    (`OfflineFrontend::_run_blocking`), before the event dispatcher, which runs
    LV2 workers, is even started (`ConcreteSushi::start`). Measured by reading
    /proc while electric-octave-up renders: every thread seen, and the CPU
    each had used at the last look before Sushi exited."""
    name, config = _as_nulltest(OCTAVE_UP)
    path = tmp_path / f"{name}.json"
    path.write_text(json.dumps(config, indent=2))
    stimulus_wav, out_wav = prepare_stimulus(tmp_path, STIMULUS).path, tmp_path / "rendered.wav"
    process = subprocess.Popen(sushi_argv(path, stimulus_wav, out_wav, str(SUSHI)), cwd=ROOT,
                               env=sushi_env(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    last: dict = {}
    most = 0
    while process.poll() is None:
        now = _threads(_tree(process.pid))
        if now:
            last.update(now)
            most = max(most, len(now))
        time.sleep(0.002)
    assert process.returncode == 0
    busy = sorted(last.items(), key=lambda kv: -kv[1][1])
    ticks = os.sysconf("SC_CLK_TCK")
    summary = ", ".join(f"{n} {t / ticks:.2f}s" for (_, _), (n, t) in busy[:6])
    spread_table.rows(request.config).append(spread_table.Row(
        section="threads of one offline render of electric-octave-up (/proc)",
        what=f"{len(last)} threads seen, at most {most} at once", renders=0, non_finite=(),
        distinct=0, rms=None, peak=None, channel_rms=None, bands=None, band_names=(), delays=(),
        seconds_per_render=0.0, note=f"CPU by thread, busiest first: {summary}",
    ))
