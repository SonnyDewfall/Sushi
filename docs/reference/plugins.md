# Plugins and portability

`plugins/` is **not tracked** — it holds third-party GPL binaries that a package
manager reproduces in seconds, and tracking it would add hundreds of MB for no
benefit.

It is structured deliberately:

```
plugins/
├── <name>.lv2        one bundle per plugin family the manifest and the configs
│                     need (`plugins assemble` copies them), at the top level
│                     where LV2_PATH looks
└── archive/          everything else — off the path, ignored by lilv
```

**`LV2_PATH` is set to `plugins/` and nothing else** — no `/usr/lib/lv2`
fallback. That is the whole point: a missing or renamed plugin now fails loudly
at startup instead of silently resolving against the system copy and hiding the
fact that the rig is not actually self-contained.

**`sushi-rig up` checks first** (#188). Sushi given a chain it cannot build
quits with only `Failed to load tracks from the Json config file`, naming no
plugin. So before it starts anything, `up` reads which plugins the rig loads
and whether a bundle in `plugins/` declares each, and whether the binary each
names (its `lv2:binary`) is a file: a bundle copied with `cp -r`, its binary a
link to nothing, is a refusal too. It reads the bundles' `manifest.ttl`,
without lilv, in a fraction of a second. What that leaves unseen is asked of
lilv as well, where lilv can be loaded, so a manifest the reader cannot parse
does not stop a rig Sushi would load; where lilv cannot be loaded and a bundle
could not be read, what is unseen may be in it, so `up` warns of it and
starts. If anything is missing or broken it starts nothing, names every one,
says where each is instead (installed in `/usr/lib/lv2` or the multiarch
folder, in `~/.lv2`, built by a script here, or nowhere) or what is wrong
with its bundle, gives the commands that fix it in the order to run them, and
exits 1:

```
synth-lead was not started: 1 plugin(s) it loads are not in /home/you/Sushi/plugins, the only folder Sushi loads plugins from (LV2_PATH), so Sushi would quit at startup saying only "Failed to load tracks from the Json config file":
  http://calf.sourceforge.net/plugins/EnvelopeFilter: installed in /usr/lib/x86_64-linux-gnu/lv2, which `sushi-rig plugins add` copies from
Nothing was started. To fix it, run this, then start the rig again:
  sushi-rig plugins add
```

The commands are the ones that work: a broken bundle is removed first
(`rm -r`), since `plugins add` leaves a bundle that is there alone; a
plugin built here is built next, since `add` copies nothing while anything
it requires is missing. And `add` requires every plugin the manifest and
the configs name, not only this rig's, so the refusal dry-runs it: what it
would still miss and this rig does not load (the neural amp, carried by
hand; a plugin built for another rig) is waived by name in the command, with
a line saying why.

The agent says the same, for a start from the web UI, and refuses before
touching the running rig for a rig switch (the picker's **Switch to**, the
stage's dropdown, a Program Change), a revert or a channel's load from another rig
(`409`, code `plugins_missing`; a revert or a load writes nothing), and a
restart (it stops nothing, the running rig plays on). The amp is the
exception: Carla loads it, not Sushi, so a missing amp is a warning (the rig
plays dry, as when Carla exits), asked for exactly when an amp will start
(`amp.prepare`'s rule: an `_amp` with a model, or `--amp-model` with none),
and never a refusal. `plugins check` is still the whole check: every URI
the manifest lists, and every link in `plugins/`.

`plugin-manifest.txt` means **every LV2 URI this rig can load**, one a line
(`grep -c . plugin-manifest.txt` counts them), and it must include every
plugin a config in `config/` uses; `sushi-rig plugins check` fails if one is
missing. Since Sushi addresses
plugins by URI and never by path, that manifest plus those bundles is the whole
dependency set.

`plugins/archive/manifest.ttl` is an LV2 manifest that declares nothing: a
single comment line. `archive/` sits inside a directory on `LV2_PATH`, and lilv
probes every subdirectory there for one; without it, every startup logs a
spurious "failed to open .../archive/manifest.ttl". A zero-byte file used to do
the job, but with serd 0.32 (Ubuntu 24.04) lilv logs "Error loading file" for
it on every startup, while a comment-only file parses cleanly and stays quiet —
which keeps a real plugin-loading error visible instead of buried in known
noise.

To rebuild `plugins/` from distro packages and the two plugins built here
(Rubber Band 4.0, [below](#rubber-band-40-built-for-the-octave), and
string-machine chorus, [below](#string-machine-chorus-built-for-the-string-machine)),
and to prove it is self-contained:

```bash
scripts/build-rubberband4.sh ~/rubberband-4.0.0/lv2
scripts/build-string-machine-chorus.sh ~/string-machine-chorus/lv2
sushi-rig plugins assemble --from ~/rubberband-4.0.0/lv2 --from ~/string-machine-chorus/lv2 \
    --from /usr/lib/lv2 --from /usr/lib/x86_64-linux-gnu/lv2 --to plugins \
    --waive http://github.com/mikeoliphant/neural-amp-modeler-lv2
sushi-rig plugins check --waive http://github.com/mikeoliphant/neural-amp-modeler-lv2
                                   # every other URI resolves with LV2_PATH = plugins/ alone
```

`--from` replaces the default folders, so the system's are named too: drop
`--from /usr/lib/x86_64-linux-gnu/lv2` where that folder does not exist
(`assemble` refuses a `--from` that is not there), as in CI's image.

The waiver is the right answer, not a workaround. The neural amp's URI is
required, and no distro packages it, so `/usr/lib/lv2` has no provider and a bare
`assemble` refuses — correctly. NAM is the one component you must carry by hand
(build it from `mikeoliphant/neural-amp-modeler-lv2`). Waiving it by name makes
`assemble` and `check` report it on every run, so that fact is something the tool
says out loud rather than something you have to remember.

**Where bundles are taken from.** Without `--from`, `assemble` and `add` search
`/usr/lib/lv2` and then the multiarch folder `/usr/lib/<triplet>/lv2`
(`/usr/lib/x86_64-linux-gnu/lv2` on a 64-bit PC), each where it exists. Debian
and Ubuntu install some LV2 packages into the second: on the owner's laptop
(Ubuntu, 2026-11-19) `calf-plugins` put `calf.lv2` there, and `add`, which
then searched `/usr/lib/lv2` alone, found no Calf, copied nothing, and
synth-lead failed inside Sushi (#188). The triplet is the one Python was built
for (`sysconfig`'s `MULTIARCH`); without one, the `/usr/lib/*-linux-gnu/lv2`
that exist are searched, this machine's architecture's only when it has one.
The folders are searched as an `LV2_PATH` is: for a URI two bundles declare,
lilv takes the higher `lv2:minorVersion`, and between equal versions the
earlier folder, so the same bundle in both is copied once, from
`/usr/lib/lv2`. Two bundles of one name that each provide something the rig
needs (an old copy left beside a new one) are refused, naming both, since
`plugins/` is flat. `--from` replaces the defaults: name every folder to
search. When a required URI is missing, the refusal says where it is instead:
in another standard LV2 folder (`~/.lv2`, `/usr/local/lib/lv2`, the multiarch
folder when `--from` left it out), with the `--from`s that would take it from
there; built by a script here, with the script; or in no folder at all, to
install its package.

`assemble` copies exactly the bundles the rig needs and nothing else, and fails
without copying if any required URI has no provider, listing all of them. "Needed"
is the manifest **plus every plugin the configs in `config/` use**, because the
manifest can fall behind them; both commands say which URIs it is missing. A URI the
packages cannot provide can be accepted by name with `--waive URI`, and is
reported on every run. `ci/Dockerfile` does this inside a container with a real
Sushi, so the whole test suite can run without the rig machine.

`assemble` builds the new folder beside `plugins/` and renames it into place
only once every bundle is copied and every link in it leads to a file, so a
copy that fails leaves `plugins/` as it was, `--replace` or not. The swap is
two renames (the old folder aside to `.plugins.previous`, the new one in);
should a run die between them, the next puts the old folder back before
anything else, and never deletes it while `plugins/` is missing. A link is
pointed where it led from the package: into the copy of whichever bundle it
led into, if that bundle is copied too, and at the file by its absolute path
otherwise. One that leads nowhere, or round in a loop, is refused, naming the
bundle and the link. `plugins add` does the same for the bundles a
`plugins/` lacks, leaving everything already in it alone (#177). `check`
also fails a `plugins/` with a link that leads nowhere (`archive/` aside) or
a required plugin whose `lv2:binary` is not a file: lilv reads only the
Turtle, so neither shows otherwise.

## Rubber Band 4.0, built for the octave

The octave pedal (`config/rigs/electric-octave.yaml`, #164) is Rubber Band
4.0's Live shifter, `http://breakfastquay.com/rdf/lv2-rubberband#livemono`.
Ubuntu 24.04 packages Rubber Band 3.3.0 (`rubberband-lv2`), which has no Live
shifter, so 4.0.0 is built from source by `scripts/build-rubberband4.sh`, from
the tag `v4.0.0` of breakfastquay/rubberband, which must be the commit
`1d95888bec3ae0a17c0c4af791810d5a63f6bc35` or the script refuses to build it.
The same script builds it in CI's image (`ci/Dockerfile`).

Measured under Sushi 1.3.0 with the built bundle (`tool/tests/test_octave.py`,
2026-11-17, in a container, not on the rig): an octave up and down within
0.01 cent on notes from E2 to A4, finite throughout; the octave comes out
3,202 samples (66.7 ms) after the note. Sushi reads a plugin's latency port
and does not compensate for it. **Its own Wet-Dry Mix is not a blend**: the
plugin delays its dry signal to line up with the shifted one, so at Mix 1,
the dry alone, the guitar comes out 2,144 samples (44.7 ms) late. That is why
`electric-octave` puts the octave on a second channel from the same input
and blends with the channel's level ([The rig file](rig-file.md)), keeping
the board on time and the octave behind it, as electric-octave-up's
gx_detune_ was (about 65 ms).

**On the rig machine**, once, and again whenever `plugins/` is rebuilt:

```bash
sudo apt install git meson ninja-build g++ pkg-config libfftw3-dev libsamplerate0-dev lv2-dev
cd ~/Sushi
sushi-rig down
scripts/build-rubberband4.sh ~/Sushi/plugins
sushi-rig plugins check
(cd tool && .venv/bin/python -m pytest tests/test_octave.py -rs)
sushi-rig up --rig electric-octave
```

In order:

- `down` stops a running rig, so no Sushi has the old bundle loaded and the
  check two steps on can start Sushis of its own: each render starts one, on
  the gRPC port a running rig's Sushi holds, and one of the two aborts. With
  no rig running it says so and does nothing.
- The script builds in a temporary folder (under a minute, with network to
  github.com) and replaces `plugins/rubberband.lv2` with 4.0.0's. Everything
  else in `plugins/`, the amp included, is left alone.
- `plugins check` fails if the Live shifter does not resolve from `plugins/`.
- The tests render the octave under this machine's Sushi and print, at the
  end, each note's error in cents and the octave's lag. They need numpy
  (the `audio` extra, [Environment](environment.md)). Until the build has
  run, the first of them fails saying to run it; the fast suite
  (`-m "not slow"`) does not include them, so pulling this before building
  leaves it green.
- `up --rig electric-octave` starts the octave's rig. Starting it from the
  web UI's rig list does the same; `sushi-rig restart` would only bring back
  what was running before `down`.

**The URI clash.** Ubuntu's 3.3.0 bundle and 4.0.0's are both called
`rubberband.lv2` and both declare `#mono`, `#stereo`, `#r3mono` and
`#r3stereo` (4.0.0 adds `#livemono` and `#livestereo`). Exactly one is what
Sushi sees: `plugins/` is flat, so it holds one `rubberband.lv2`, and Sushi
reads `plugins/` alone. That one must be 4.0.0's: `plugins check` fails
without the Live shifter, which the manifest lists, and
`tool/tests/test_octave.py` checks every Rubber Band URI resolves to the
pinned build (the `BUILT-FROM` file the script leaves in the bundle). The
system's own `/usr/lib/lv2` keeps 3.3.0 if it is installed, which is what
`lv2ls` and lilv's default path see; the script installs nothing outside the
folder it is given. When `assemble` reads both trees, lilv takes the higher
`lv2:minorVersion` for each URI (4.0.0 declares 6, 3.3.0 declares 4) and
ignores the older bundle whichever comes first on the path (checked with lilv
0.24.22, 2026-11-17); a split between the two would be refused as two
bundles of one name.

What the build links at run time is `libfftw3.so.3` and `libsamplerate.so.0`
(`libfftw3-double3` and `libsamplerate0`, which the `-dev` packages bring);
Rubber Band itself is in the bundle, statically. A side effect: 4.0.0 fixes
3.3.0's R3 URIs ("Fix incorrect URIs in LV2 plugins"), so the manifest's
`#r3mono` and `#r3stereo`, which 3.3.0 lists and cannot instantiate
(`tool/spikes/plugin_research/README.md`), now load: `#r3mono` rendered
under Sushi 1.3.0 from the built bundle, 2026-11-17.

## string-machine chorus, built for the string machine

The string-machine rig (`config/rigs/string-machine.yaml`, #177) runs
string-machine chorus, `http://jpcima.sdf1.org/lv2/string-machine-chorus`, on
each of its channels: the Solina-style ensemble out of jpcima's string-machine
synth, one input and two outputs (`plugin-ports.txt` says `1 2`). No
distribution packages it, so `scripts/build-string-machine-chorus.sh` builds
it: string-machine at commit `7050cb1516fbaf5aaaa2c6cc9999fe5ac100d746` and
DPF at `f24e5317cd7b57c8ab36b0987ec29a5579cd5dd2`, the commit that
string-machine commit records for it, both fetched by commit and checked. Only
the plugin's DSP is compiled, with g++ and the flags #166 measured it with
(`tool/spikes/synth_research/build_extras.sh`); its own Makefile would also
build an OpenGL interface the rig cannot show. The bundle links nothing but
the C++ runtime, and gets a `BUILT-FROM` file as Rubber Band's does. The same
script builds it in CI's image. Licences: the repository is BSL-1.0, the
chorus plugin GPL-2.0-or-later, DPF ISC.

The rig needs four packaged plugins that `plugins/` did not hold before:
Calf's Envelope Filter (`calf-plugins`, whose `calf.lv2` bundle holds all of
Calf), abGate (`abgate`), and GxBigMuffPi and GxZita_rev1-Stereo
(`guitarix-lv2`, already installed). The synth-lead rig
(`config/rigs/synth-lead.yaml`) uses the first and the third, and GxEchoCat
(#165).

Measured under Sushi 1.3.0 here (2026-11-18, offline, in a container, not on
the rig): the chorus treats a chord exactly as the sum of its strings (a
residual under -100 dB, `tests/test_string_machine.py`) and costs 1.4 to
2.3 % of a core per channel; the string machine's `strings` channel answers
a 500 Hz burst 169 samples (3.5 ms) late, its `octave` 3,376 (70 ms).
Calf's Envelope Filter has two atom ports, for MIDI and Calf's own
messages, both `lv2:connectionOptional`; `probe` no longer counts an optional
one against a plugin (`probe.is_config_drivable`, review of PR #186), and
Sushi sets every one of its controls (`tests/test_synth_lead.py`). Its
profile hides its Input and Output Gain, linear from 1/64 to 64, behind a
bounded Level.

**On the rig machine**, once, and again whenever `plugins/` is rebuilt. Each
line runs only if the one before it worked (`&&`), so the first that fails
stops the rest, with its own message as the last thing on the screen:

```bash
sudo apt install git gcc g++ calf-plugins abgate guitarix-lv2 &&
cd ~/Sushi &&
sushi-rig down &&
scripts/build-string-machine-chorus.sh ~/Sushi/plugins &&
sushi-rig plugins add &&
sushi-rig plugins check &&
(cd tool && .venv/bin/python -m pytest tests/test_string_machine.py tests/test_synth_lead.py -rs) &&
sushi-rig up --rig string-machine
```

In order:

- `down`, for the reason it comes first for Rubber Band (above).
- The script builds in a temporary folder (seconds, with network to
  github.com) and replaces `plugins/string-machine-chorus.lv2`, putting the
  old one back if it fails. Nothing else in `plugins/` is touched.
- `plugins add` copies from `/usr/lib/lv2` and the multiarch folder
  `/usr/lib/x86_64-linux-gnu/lv2` (where Ubuntu may put `calf.lv2`, as on
  the owner's laptop, #188) only the bundles the manifest
  needs and `plugins/` lacks (here Calf's, abGate's, GxBigMuffPi's and
  GxZita_rev1's, and GxEchoCat's if the pedals' steps have not put it there);
  a bundle already in `plugins/`, the amp and the built ones among them, is
  never touched. It is all or nothing: every bundle is copied into a staging
  folder inside `plugins/` that lilv cannot see, its links checked, and only
  then are they renamed into place. It refuses, copying nothing, if a bundle
  it would add has the name of one already there that provides something
  else, or if a link in one leads nowhere. Links are what make the copy
  matter: Ubuntu 24.04's `calf.lv2/calf.so` is a relative link out of its bundle
  (`../../x86_64-linux-gnu/calf/calf.so`), which a plain `cp -r` copies as a
  link to nothing; `add` and `assemble` point it at the file (#177). When
  a plugin the rig needs is in none of the folders `add` searched, it says
  where that plugin is instead; `up` says the same before it starts a rig
  that needs one `plugins/` lacks.
- `plugins check` fails if any URI the manifest lists does not resolve from
  `plugins/`, if any link in `plugins/` (but `archive/`) leads nowhere, or if
  a required plugin's binary is not a file, which is what a `cp -r` of Calf
  would leave.
- The tests check `plugins/` holds the pinned build and render each new
  plugin under this machine's Sushi; they need numpy, as the octave's do.
  The fast suite does not include them.
- `up --rig string-machine` starts it; `--rig synth-lead` starts the other.

## Choosing a plugin

Before a plugin goes into a rig, `probe` answers what you'd otherwise only
find out by trying it:

```bash
sushi-rig probe                 # everything on LV2_PATH, one line each
sushi-rig probe <uri>           # full detail for one plugin
sushi-rig probe <uri> --json    # same, machine-readable
sushi-rig probe amp/models/X.nam  # a NAM amp model: architecture, quality tiers,
                                  # and whether its sample rate matches the rig
```

It reads the plugin's own TTL through lilv, so every value is the plugin
author's declaration rather than our inference:

```
GxTubeScreamer  (Distortion Plugin)
  uri     http://guitarix.sourceforge.net/plugins/gxts9#ts9sim
  audio   1 in / 1 out
  config  drivable — all state lives in control ports
  4 parameter(s):
    Level                          [-20.0, 4.0]             default=-16.0
    Tone                           [100.0, 1000.0]          default=400.0
    Drive                          [0.0, 1.0]               default=0.5
    BYPASS                         [0.0, 1.0]               default=1.0  toggled
```

Two things there are invisible everywhere downstream. **Real-world ranges** —
Tone is 100–1000 Hz, which in a Sushi config appears only as a normalised
`0.0–1.0`. And **`config drivable`**, which is the question that actually
decides whether a plugin can be used at all.

A plugin is drivable only if all of its state lives in control ports. If it
keeps state elsewhere — a file, a model, an impulse response — it reaches it
through the LV2 `patch:`/`state:` extensions, which **Sushi does not support**.
Such a plugin loads without complaint and then silently ignores the part you
cared about. `probe` checks three signals for this (atom ports,
`patch:writable`, `state:interface`), because any one alone is insufficient:
`zeroconvolv` declares no atom ports and no `patch:writable` yet is entirely
driven by an IR file, and only `state:interface` reveals it.

Note that `probe` reads and reports — it doesn't write a catalogue or decide
which parameters are worth showing. Those are judgment calls, and keeping them
out of generated data is what makes the generated half safe to regenerate.

## `LV2_PATH`

**`LV2_PATH` must be set, or Sushi loads no LV2 plugins at all.** It exits 4
with `Failed to load tracks from the Json config file` — an error that never
mentions `LV2_PATH`. `lv2ls` is not a proxy for this: it finds plugins using
lilv's own defaults while Sushi finds none. `sushi-rig` sets it for everything
it starts; running `./sushi -c <config>` by hand hits this every time:

```bash
export LV2_PATH="$HOME/Sushi/plugins"
```

That single path is deliberate and complete, as above. Do not add
`/usr/lib/lv2`: it would let a config load against system plugins that
`plugins/` doesn't carry, which is exactly the silent portability break the
layout exists to prevent.

## Known issue: gx_detune_ can go silent an octave up

No rig runs it any more. The octave pair (gx_detune_, as `octave_down` and
`octave_up`) was taken out of every rig in `config/rigs/` on 2026-11-16 (#152),
and electric-octave-up, which existed for its octave up, was retired to
`config/retired/`. Its replacement is `electric-octave`, on Rubber Band 4.0
([above](#rubber-band-40-built-for-the-octave)). gx_detune_ stays in
`plugin-manifest.txt` for the frozen rigs, and for the v1 config `electric_board.json` (and its source),
retired to `config/retired/v1/` with the other v1 configs (#218), which runs it at no shift.

What was found, for whoever puts it back: an octave up (gx_detune_ at +12)
has a plugin bug: on one or two loads in a hundred it starts putting out NaN
some seconds in, and keeps doing so until it is loaded again. What you hear
is the rig going silent, or a burst of noise, with nothing reported as wrong.
A fresh load (`sushi-rig restart`) clears it. Bypassing it is not expected to
help, since the NaN is held in the plugin's state and in any reverb after it;
that, like the bug on the rig at all, was never tried, only measured offline
(MANIFEST, 2026-11-03; `tool/spikes/gx_detune_nan/`). At no shift the bug
cannot reach the output.
