# Running the tests, and CI

```bash
cd tool && python -m pytest -m "not slow" -rs   # fast suite; lilv/Sushi-guarded tests skip without them
cd tool && python -m pytest -m slow         # starts a real Sushi, seconds per test
cd tool && PYTHONPATH=src python -m pytest -rs   # everything, on a machine with all of it
```

With the package not installed, put `PYTHONPATH=src` in front of any of them, as
the last line does.

The web UI's browser tests (`tests/test_ui*.py`) need Playwright and a Chromium for
it; without them those tests skip, and the rest of each file still runs:

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
it cannot see). The sound check makes the same split (#144): the frozen rigs'
references, under `tests/fixtures/sound/`, are what pin the rig's sound and
fail; a committed rig's reference is compared exactly only while it was made
from the rig as it is, and a rig changed or added is a warning, not a failure.
See `nulltest --all` under [CI](#ci).

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
  `SKIPPED`. That is what makes the first workflow's skips safe. Its
  plugins include Rubber Band 4.0, built by `scripts/build-rubberband4.sh`,
  and `tests/test_octave.py` checks there that `plugins/` holds that build and
  that the octave it plays under Sushi is in tune and finite; its numbers
  (cents per note, the lag) are printed at the end of the run. They include
  string-machine chorus too, built by `scripts/build-string-machine-chorus.sh`,
  and `tests/test_string_machine.py` checks the build and what the string
  machine's plugins do under Sushi.
- The same job runs `nulltest --all`, so **a change in the code or a plugin
  that alters the sound fails the build**. References are calibrated in that
  container, for every v1 config (`config/sound/<name>.v1-mono.yaml`), every
  frozen rig (`tool/tests/fixtures/sound/<name>.yaml`) and every rig of
  channels in `config/rigs/` (`config/sound/<name>.yaml`), a rig rendered
  through the master one channel at a time. A reference records each
  channel's level as well as the whole render's, so a channel lost is a
  finding of its own; a silent right channel used to read as only 3 dB down.
  The check runs even when the suite failed. What fails and what warns
  (#144):

  | | v1 config, frozen rig | committed rig (`config/rigs/`) |
  |---|---|---|
  | reference matches | passes | passes, if it was made from the rig as it is |
  | sound differs from the reference | **fails** | **fails**, if the reference was made from the rig as it is: the code changed the sound |
  | rig changed since its reference, or no reference | **fails** (no reference) | **STALE**, a warning, with a candidate reference attached |
  | orphaned reference | **fails** | a warning |
  | render NaN, silent, or one side only (or the sides more than 1 dB apart) | **fails**, a frozen rig on any of them, a v1 config if its left side, the one it plays, is silent | **fails**, whatever its reference says |

  No reference is ever written of such a render, by `--calibrate`,
  `--calibrate --missing`, `--candidates` or the job's failure path: of
  either of a calibration's two renders, since a silent reference would pass
  every silent render after it.

  A committed rig's reference records the rig it was made from under
  `made_from`: the file's name and a sha256 (`nulltest.rig_hash`, rule 2) of
  the part of the file **as parsed** that the render is made from, its
  mappings in key order and every whole number written as an integer. That
  part is the schema version, `host`, and the channels and the master less
  their machine roles (`nulltest.RENDERED`): not the amp, which runs in
  Carla outside any render, nor the MIDI map, the description, `meta`, the
  rig's name, a channel's `input` or the master's `output`, none of which
  reaches what Sushi renders (shown on every frozen rig in
  `tests/test_sound_refs.py`, which also fails on a key added to the rig
  format until it is placed). So comments, layout, key order, `1.0` against
  `1` and an amp nudged from the page do not make a reference stale; any
  rendered value that changes does; and a compiler change does not make
  every reference stale at once: it is compared against all of them. A STALE channel is rendered, twice, and held to being finite,
  two-sided and balanced alone; the job then puts a warning on the check run
  naming each channel, with its candidate in the warning, printed in the log,
  and as the `stale-sound-references` artifact (`nulltest --all --candidates
  DIR`). Committing a candidate as `config/sound/<name>.yaml` is what makes
  that rig compared exactly again; until then a change to its sound by the
  code is not caught for that rig, only by the frozen ones. The frozen
  references are byte for byte config/sound/'s of the day each committed rig
  compiled to the frozen one's channels, and are pinned by sha256 with the
  frozen rigs (`tests/test_rig_fixtures.py`). When the check fails, the job
  calibrates every config afresh and attaches the result
  (`sound-reference-candidates`, with the frozen rigs' under `frozen/`, and
  printed in the log). Commit those only if the change was meant; if it was
  not, they are the evidence and the fix is in the config. A frozen rig's new
  reference goes in with its new sha256, in the same commit. A config is rendered whole when Sushi's
  offline frontend can host it, which also checks how its channels mix. One it
  cannot — the offline frontend has one stereo bus, and `guitar_vocal`'s vocal
  is on the second — is checked one channel at a time, each with its own
  reference (`config/sound/<config>.<channel>.v1-mono.yaml`). To add a reference
  without re-baselining the rest, `nulltest <config> --calibrate --missing`.
- **The master's limiter (#137)** is the compiler's, never written in a rig
  file, so `rig_hash` does not see it and no reference went STALE when it
  went in: every reference, frozen, committed and v1, is still compared
  exactly against renders that now run through it, and all of them still
  match (`nulltest --all`, 2026-11-17, Sushi 1.3.0 here), which is the
  evidence that it is transparent below its −1 dBFS threshold. A hash that
  left it out on purpose was not needed: it was never in what the hash reads.
  Had it been written into the rig files, every reference would have gone
  stale and the code's sound been unchecked until each was remade. The
  limiter's own checks are `tests/test_limiter_sushi.py` (slow, in the
  container): sample for sample the same below the threshold, a +12 dBFS
  sweep and sine caught under full scale with it and not without, a NaN let
  through and recovered from, and each committed rig's master, as compiled,
  catching a hot input; every render there must be finite, two-sided and
  balanced. Its fast half, and the agent's refusals, are `tests/test_limiter.py`
  and `tests/test_ui_limiter.py`.
- **One config does not render the same twice**: the v1 config
  `electric_board.json`, the only one that still runs gx_detune_ unbypassed
  (no rig in `config/rigs/` has it since #152; electric-octave-up, which had
  the other, is retired). The plugin plans its FFTs by timing them
  (`FFTW_MEASURE`), so each Sushi rounds a little differently; inside that
  chain that is at most a few thousandths of a dB, and its reference
  (`electric_board.v1-mono.yaml`) carries a hand-set 0.01 dB tolerance for it.
  Every other plugin of electric-octave-up's chain, rendered alone, came out
  bit for bit the same (CI, 2026-11-03, 40 renders each:
  `tests/test_render_spread.py`, which renders the frozen copy of that rig and
  whose table prints at the end of the run). An octave up, as
  electric-octave-up's was, also reads past the end of
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
