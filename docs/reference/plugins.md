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
    --from /usr/lib/lv2 --to plugins \
    --waive http://github.com/mikeoliphant/neural-amp-modeler-lv2
sushi-rig plugins check --waive http://github.com/mikeoliphant/neural-amp-modeler-lv2
                                   # every other URI resolves with LV2_PATH = plugins/ alone
```

The waiver is the right answer, not a workaround. The neural amp's URI is
required, and no distro packages it, so `/usr/lib/lv2` has no provider and a bare
`assemble` refuses — correctly. NAM is the one component you must carry by hand
(build it from `mikeoliphant/neural-amp-modeler-lv2`). Waiving it by name makes
`assemble` and `check` report it on every run, so that fact is something the tool
says out loud rather than something you have to remember.

`assemble` copies exactly the bundles the rig needs and nothing else, and fails
without copying if any required URI has no provider, listing all of them. "Needed"
is the manifest **plus every plugin the configs in `config/` use**, because the
manifest can fall behind them; both commands say which URIs it is missing. A URI the
packages cannot provide can be accepted by name with `--waive URI`, and is
reported on every run. `ci/Dockerfile` does this inside a container with a real
Sushi, so the whole test suite can run without the rig machine.

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
(`guitarix-lv2`, already installed). The synth-lead rig uses the first and the
third, and GxEchoCat (#165).

Measured under Sushi 1.3.0 here (2026-11-18, offline, in a container, not on
the rig): the chorus adds about 4 ms (169 to 222 samples), treats a chord
exactly as the sum of its notes, and costs about 1.4 % of a core per channel.

**On the rig machine**, once, and again whenever `plugins/` is rebuilt:

```bash
sudo apt install git gcc g++ calf-plugins abgate guitarix-lv2
cd ~/Sushi
sushi-rig down
scripts/build-string-machine-chorus.sh ~/Sushi/plugins
for b in calf abGate gx_bmp gx_zita_rev1 gxechocat; do
    rm -rf "plugins/$b.lv2" && cp -rL "/usr/lib/lv2/$b.lv2" plugins/
done
sushi-rig plugins check
(cd tool && .venv/bin/python -m pytest tests/test_string_machine.py tests/test_synth_lead.py -rs)
sushi-rig up --rig string-machine
```

In order:

- `down`, for the reason it comes first for Rubber Band (above).
- The script builds in a temporary folder (seconds, with network to
  github.com) and replaces `plugins/string-machine-chorus.lv2`. Nothing else
  in `plugins/` is touched.
- The loop copies the packaged bundles into `plugins/`, replacing any copy
  already there; `gxechocat` is the pedals' (#165), and is copied again
  harmlessly if it is there. `-L` matters: Ubuntu's `calf.lv2/calf.so` is a
  relative link out of the bundle (`../../x86_64-linux-gnu/calf/calf.so`),
  which a plain `cp -r` copies as a link to nothing, and `plugins check`
  would still pass, since it reads only the Turtle. `sushi-rig plugins
  assemble` makes such a link absolute instead (#177).
- `plugins check` fails if any URI the manifest lists does not resolve from
  `plugins/`.
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
`plugin-manifest.txt` for the v1 config below and the frozen rigs. Only the v1 config `electric_board.json` (and its
source, `config/src/electric_board.yaml`) still runs it, at no shift.

What was found, for whoever puts it back: an octave up (gx_detune_ at +12)
has a plugin bug: on one or two loads in a hundred it starts putting out NaN
some seconds in, and keeps doing so until it is loaded again. What you hear
is the rig going silent, or a burst of noise, with nothing reported as wrong.
A fresh load (`sushi-rig restart`) clears it. Bypassing it is not expected to
help, since the NaN is held in the plugin's state and in any reverb after it;
that, like the bug on the rig at all, was never tried, only measured offline
(MANIFEST, 2026-11-03; `tool/spikes/gx_detune_nan/`). At no shift the bug
cannot reach the output.
