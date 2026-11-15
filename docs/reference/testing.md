# Running the tests, and CI

```bash
cd tool && python -m pytest -m "not slow" -rs   # fast suite; lilv/Sushi-guarded tests skip without them
cd tool && python -m pytest -m slow         # starts a real Sushi, seconds per test
cd tool && PYTHONPATH=src python -m pytest -rs   # everything, on a machine with all of it
```

With the package not installed, put `PYTHONPATH=src` in front of any of them, as
the last line does.

The web UI's browser tests (`tests/test_ui.py`) need Playwright and a Chromium for
it; without them those tests skip, and the rest of the file still runs:

```bash
pip install -e "tool[ui-test]" && python -m playwright install chromium
```

## The rule about `config/rigs/` and the frozen fixtures

**A test may fail because the code is wrong, or because a committed rig is
not a valid rig. It must not fail because a rig changed.** `config/rigs/` is
saved into from the web UI and committed, so a test that reads it takes every
rig there through `tests/rig_fixtures.py` and assumes nothing about what any
of them holds. What a committed rig is held to is in
`tests/test_rig_fixtures.py`, one test for each by name: it parses and
compiles, its amp's model is a file and its amp's parameters are in the amp's
range, and it wires on the committed machine file; in the container, where
there is a Sushi, every parameter it names is one its plugin has (nothing in
the checkout lists a plugin's parameters, so a bare runner cannot ask). A rig
that fails that is left out of the tests over every pair of rigs, which ask
only what holds of any two. A test that needs a rig it knows (one channel
called `board`, two rigs that differ only in the amp's levels) reads the
frozen set in `tests/fixtures/`: `rigs/`, the eleven rigs as they were at
c494625, with the v1 configs and sources they were converted from (`v1/`) and
the machine file they are wired against (`machines/`). They are test data,
pinned by checksum, and do not follow `config/` or `machines/`. The
conversions of #30 are held to their v1 configs there, and as `convert`
writes them today; the rig in `config/rigs/` is the one to edit and is not
compared with the config it came from. A guard keeps this so: every file
opened and folder listed in the test process is audited, and a test that
reads `config/rigs/` without the marker `committed_rigs` fails, however the
path was spelt and whatever code did the reading (`rig_fixtures.py` says what
it cannot see). The one check that does follow a rig's content is its sound:
see `nulltest --all` under [CI](#ci), where a rig with no reference, or one that no
longer sounds like its reference, fails the container job until a reference
is calibrated for it.

## The documentation's links

`tests/test_docs.py` is part of the fast suite: every relative link and image
in `README.md` and under `docs/` must point at a file that exists, a link to a
heading in a Markdown file must name a heading it has, and the README must stay
a front page (under 200 lines). It reads only `README.md` and `docs/`.

## CI

**Every test must run somewhere, and a skip counts as a failure.** CI is two
workflows because no single runner has everything:

- `ci.yml` runs the fast suite on a bare runner. Tests that need `python3-lilv`
  or `./sushi` skip there — expected, and not a pass.
- `rig-container.yml` builds `ci/Dockerfile` (system lilv, the distro plugins,
  a real Sushi 1.3.0) and runs the **whole** suite in it, failing the job on any
  `SKIPPED`. That is what makes the first workflow's skips safe.
- The same job runs `nulltest --all`, so **a change in a config's sound fails
  the build**. References live in `config/sound/` and are calibrated in that
  container, for every v1 config (`<name>.v1-mono.yaml`) and every rig of
  channels in `config/rigs/` (`<name>.yaml`, rendered through the master one
  channel at a time). A reference records each channel's level as well as the
  whole render's, so a channel lost is a finding of its own; a silent right
  channel used to read as only 3 dB down. The check runs even when the suite
  failed. When the check fails, the job calibrates every config afresh and
  attaches the result (`sound-reference-candidates`, and printed in the log).
  Commit those only if the change was meant; if it was not, they are the
  evidence and the fix is in the config. A config is rendered whole when Sushi's
  offline frontend can host it, which also checks how its channels mix. One it
  cannot — the offline frontend has one stereo bus, and `guitar_vocal`'s vocal
  is on the second — is checked one channel at a time, each with its own
  reference (`config/sound/<config>.<channel>.v1-mono.yaml`). To add a reference
  without re-baselining the rest, `nulltest <config> --calibrate --missing`.
- **Two configs do not render the same twice**: electric-octave-up and
  electric_board, the only two that run gx_detune_ unbypassed. The plugin plans
  its FFTs by timing them (`FFTW_MEASURE`), so each Sushi rounds a little
  differently; inside those chains that is at most a few thousandths of a dB,
  and their references carry a hand-set 0.01 dB tolerance for it. Every other
  plugin of electric-octave-up's chain, rendered alone, came out bit for bit
  the same (CI, 2026-11-03, 40 renders each: `tests/test_render_spread.py`,
  whose table prints at the end of the run). An octave up, as electric-octave-up's is, also reads past the end of
  two of the plugin's arrays, and one or two loads in a hundred render NaN
  from some point on (`tool/spikes/gx_detune_nan/`). The null tests render
  such a NaN once more, and only that NaN (a config running gx_detune_, starting on
  its hop grid): any other NaN, or the same one twice, fails. A retry is never
  quiet: `nulltest` prints "retried after a NaN" beside the channel and a
  `RETRIED-NAN:` line per retry in a summary at the end, pytest prints the same
  lines at the end of the run, and the job counts those lines and puts them on
  the check run as a warning. The 520-render table that found all this is
  opt-in (marker `spread`, deselected unless `SUSHI_RIG_SPREAD=1`); every run
  has a five-render guard of the octave up instead. To print the table again:
  `docker run --rm -e SUSHI_RIG_SPREAD=1 sushi-rig-ci pytest -rs -m spread`.

Only `-m slow` used to run in the container, so the lilv- and Sushi-guarded
tests ran in neither workflow and nothing said so. If a test genuinely cannot
run in the container (it would need the NAM amp or Carla, which the image
deliberately lacks), give it a registered marker that says what it needs and why
CI cannot provide it, and exclude that marker in the container run — a declared
exemption, not a silent skip. There are none today: `tests/test_amp.py` is pure.
