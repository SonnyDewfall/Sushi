# Dependencies

Everything from outside this repository that the rig runs on, or that its
tests and CI are built with: which version the rig uses, where that is pinned
or written down, where to look upstream for a newer one, and the known
upstream faults the rig works around. The owner's rule (#143): check for
updates on a regular basis. This page is that check's list.

Two machines use these, and they differ. **The rig machine**
(`sonny-dewfall-XPS-9320`) runs Ubuntu 26.04.1 with PipeWire 1.6.2, as
recorded on 2026-10-31 in
[`tool/spikes/live_rig/RESULTS-sonny-dewfall-XPS-9320-2026-10-31.md`](../../tool/spikes/live_rig/RESULTS-sonny-dewfall-XPS-9320-2026-10-31.md)
(the "rig record" below). **CI's container** ([`ci/Dockerfile`](../../ci/Dockerfile))
is built on every run from a floating `ubuntu:24.04` and unpinned packages,
so it gets whatever Ubuntu 24.04 has that day; `rig-container.yml` prints the
plugin packages' versions in each run's log, and nothing keeps them.
"Not recorded" means the repository does not say; nothing here is guessed.

**There is no lockfile.** `tool/` has only `pyproject.toml`, whose ranges pip
resolves afresh on every install, in CI on every run.

## How to check

Most of the one-liners use this function, pasted into the shell first:

```bash
tags() { git ls-remote --tags --refs "$1" | sed 's#.*refs/tags/##' | sort -V | tail -${2:-3}; }
```

`pip index versions <pkg>` gives a PyPI package's newest first. `apt-cache
policy <pkg>` gives the candidate for the machine's own release (on the rig,
26.04's; in an Ubuntu 24.04 shell, CI's), after `sudo apt update`.

The last column is what was found on **2026-11-26**, from a 24.04 container
with GitHub, PyPI, npm, Docker Hub and Ubuntu's archive reachable and
gitlab.freedesktop.org, Launchpad's API, x42-plugins.com and GitHub's release
pages not. "Same" means nothing newer upstream than what the rig uses.

## Sushi and its control

| Dependency | Version used, and where it is pinned or recorded | Upstream | Check | Last checked 2026-11-26 | Known upstream faults we work around |
|---|---|---|---|---|---|
| **Sushi** (Elk) | **1.3.0**, commit `08f24e9`. Installed by hand on the rig: the release zip `sushi-linux-x86_64_1_3_0.zip` from GitHub, its AppImage extracted (`--appimage-extract`, no FUSE), `./sushi` at the checkout's root a symlink to `squashfs-root/AppRun` (untracked; [environment](environment.md), rig record). CI: `SUSHI_TAG: "1.3.0"` and `SUSHI_ASSET` in `.github/workflows/rig-container.yml`, fetched with `gh release download`, extracted in `ci/Dockerfile` | [elk-audio/sushi tags](https://github.com/elk-audio/sushi/tags), [releases](https://github.com/elk-audio/sushi/releases) | `tags https://github.com/elk-audio/sushi` | **1.4.0** (commit `c6ddde6`, 2026-10-12). Its HISTORY.md: a ZMQ IPC API, a reactive frontend, "Allow buffersizes < 64 in JackFrontend", a fix "when disconnecting all audio connections to a track". The release page could not be read from here, and no asset named as 1.3.0's is (`sushi-linux-x86_64_1_4_0.zip`: 404). Not tried with the rig | **Two links into one input** carry only the first 64 frames of a period under PipeWire (#81, #106): a probe never shares an input (`selftest_probe`). Read again on 2026-11-26: still in 1.4.0's source. Report drafted, not posted: [`docs/upstream/sushi-pipewire-two-links.md`](../upstream/sushi-pipewire-two-links.md). **Offline frontend**: two channels only, and a wider file overflows its stack; `render_offline` refuses it and splits a rig per channel (MANIFEST 2026-10-30, not reported). **`delete_track`** sometimes stalls with its processors taken off first (#190; MANIFEST 2026-11-24). Eight JACK channels each way (`rigfile.MAX_FRONTEND_CHANNELS`), and a mono plugin's channel 1 cleared after it (the compiler's copies to stereo, `stereo.py`) |
| **elkpy** (Sushi's gRPC client) | Rig: **1.3.0** (rig record). CI's rig container: **1.4.0**. Pinned: no, `elkpy` unversioned in the `live` extra, `tool/pyproject.toml`, so which one pip installs depends on the Python: 1.4.0 declares `Requires-Python <3.13`, so Python 3.12 (the rig container, Ubuntu 24.04) gets 1.4.0, and 3.13 and 3.14 get 1.3.0 | [PyPI](https://pypi.org/project/elkpy/), [elk-audio/elkpy tags](https://github.com/elk-audio/elkpy/tags) | `curl -s https://pypi.org/pypi/elkpy/json` (`releases`, and each one's `requires_python`): `pip index versions elkpy` lists only what the Python running it can install, so on 3.13 it says 1.3.0 is the newest | PyPI: **1.4.0** for Python 3.10 to 3.12 (tag `ea5827e`), 1.3.0 newest for 3.13 and later; 2.0.0rc5 a pre-release. 1.4.0 ships Sushi's gRPC modules generated (`elkpy.rpc`) and needs no grpcio-tools; it adds a ZMQ transport, and `SushiController`'s `auto_update_async_command_responses` now defaults to **False**: no subscription to Sushi's notices of async commands, so no command is confirmed by its notice unless asked. `live._controller` asks by name (#241) | **#241**: a command's notice is lost when it comes before its response is registered (kept now, `live.Confirmations`) or before the notice stream is up (the change is read back instead, `live._wait`'s `applied`); on 1.4.0 it never came at all until `live._controller` asked for the subscription. 1.3.0 prints to stdout from its own modules, and reports a version mismatch as "Could not find a running Sushi": `live.ELKPY_CHATTER`, `ELKPY_ONCE`. Its API reports 1.2.0 against Sushi 1.3.0 (rig record) |

## The amp

| Dependency | Version used, and where it is pinned or recorded | Upstream | Check | Last checked 2026-11-26 | Known upstream faults we work around |
|---|---|---|---|---|---|
| **Neural Amp Modeler LV2** | Not recorded. Built or installed by hand on the rig; no distribution packages it, and CI's image has none (waived by URI in `ci/Dockerfile`, `plugin-manifest.txt`) | [mikeoliphant/neural-amp-modeler-lv2 tags](https://github.com/mikeoliphant/neural-amp-modeler-lv2/tags) | `tags https://github.com/mikeoliphant/neural-amp-modeler-lv2` | Newest tag v0.2.3. Whether that is newer than the rig's is not known | Not a fault of its own: `plugins assemble` cannot reproduce it, so `--waive` names it on every run. Its models must be at 48 kHz (`doctor`) |
| **Carla** (hosts the amp) | Not recorded. On the rig, `/usr/bin/carla` (rig record). Not in CI | [falkTX/Carla tags](https://github.com/falkTX/Carla/tags) | `tags https://github.com/falkTX/Carla`; `apt-cache policy carla` | Newest tag v2.5.10; Ubuntu 24.04 has 2.5.8 | Must be started under `pw-jack` (MANIFEST 2026-10-26). Cannot be asked what it holds, so the amp's settings are what the rig last sent (`amp.py`, `amp_live.py`). Not run against a real Carla: the agent's live amp control (MANIFEST) |

## Audio graph

| Dependency | Version used, and where it is pinned or recorded | Upstream | Check | Last checked 2026-11-26 | Known upstream faults we work around |
|---|---|---|---|---|---|
| **PipeWire**, **pipewire-jack** | Rig: **1.6.2**, `pipewire-jack 1.6.2-1ubuntu1.2` (rig record; the Elk draft's Versions). CI has no PipeWire; the container measurements in `tool/spikes/visualiser_audio/` used Ubuntu 24.04's **1.0.5**, which that README calls "the rig's version", against the rig record | [pipewire on GitLab](https://gitlab.freedesktop.org/pipewire/pipewire/-/tags) (its [GitHub mirror](https://github.com/PipeWire/pipewire)) | `tags https://gitlab.freedesktop.org/pipewire/pipewire.git`; `apt-cache policy pipewire pipewire-jack` | GitLab could not be reached from here. The GitHub mirror's newest tag is **1.6.11**; Ubuntu 24.04 has 1.0.5-1ubuntu3.3 | With two links into one port it mixes only the frames asked for, which is half of Sushi's fault above. 1.0.5's `pw-loopback` refuses `--channel-map` though its help lists it (`vistap` uses the short options), and has no `node.async` |
| **qpwgraph** (the patchbay) | Not recorded as run. The committed patchbays say `version="0.9.9"`, as qpwgraph wrote them (`Patchbay/*.qpwgraph`, copied as `patchbay.PATCHBAY_VERSION`) | [rncbc/qpwgraph tags](https://github.com/rncbc/qpwgraph/tags) | `tags https://github.com/rncbc/qpwgraph`; `apt-cache policy qpwgraph` | Newest tag **v1.0.5**; Ubuntu 24.04 has 0.6.1 | Exits silently at start (#14, open): its lock socket survives SIGKILL and it quits under a detached session manager. `rig.start_qpwgraph` clears the socket, drops `SESSION_MANAGER`, and retries three times. |
| **JACK** library (`libjack-jackd2-0`) | CI: Ubuntu 24.04's, unpinned (`ci/Dockerfile`, `ci.yml`), 1.9.21 today. On the rig the JACK clients run under `pw-jack` | Ubuntu | `apt-cache policy libjack-jackd2-0` | 1.9.21~dfsg-3ubuntu3 | Sushi's AppImage links it dynamically and leaves it out, so CI installs it |
| **fmit** (the tuner) | Not recorded. Not in CI | Ubuntu | `apt-cache policy fmit` | Ubuntu 24.04 has 1.2.14 | Through ALSA's compatibility layer it auto-connects to the default source, which once put the rig on a webcam's clock: started with `node.autoconnect = false` in `PIPEWIRE_PROPS` (MANIFEST 2026-10-26) |

## LV2 plugins

Each plugin's LV2 version (its `lv2:minorVersion.microVersion`) is pinned in
its profile under `tool/examples/profiles/` where it has one, and checked
against the installed plugin. The package versions below are Ubuntu 24.04's,
which is what CI installs (`ci/Dockerfile`, unpinned); the rig machine's are
not recorded. `lilv` and `lv2-dev` are with them, since the tool reads every
plugin through them.

| Group | Profiles (LV2 version pinned) | Package, version used, and where recorded | Upstream | Check | Last checked 2026-11-26 | Known upstream faults we work around |
|---|---|---|---|---|---|---|
| **Guitarix** (`guitarix-lv2`) | gxbigmuffpi, gxechocat, gxflanger, gxshimmizita, gxtremolo, gxts9, gxzita-rev1 (43.0) | 0.46.0+dfsg-1 (`tool/tests/test_library.py`, `tool/spikes/gx_detune_nan/README.md`) | [brummer10/guitarix tags](https://github.com/brummer10/guitarix/tags) | `tags https://github.com/brummer10/guitarix`; `apt-cache policy guitarix-lv2` | Upstream **V0.47.0**; Ubuntu 24.04 still 0.46.0 | `gx_detune_` gives NaN about once in a hundred loads (MANIFEST 2026-11-03): `render.detune_nan` retries a render once; on the rig, `sushi-rig restart`. Fix drafted, not filed (the spike's README) |
| **Rubber Band** (built) | rubberband-live (6.1) | **4.0.0**, tag `v4.0.0`, commit `1d95888`, in `scripts/build-rubberband4.sh` (`TAG`, `COMMIT`); checked in CI's image by `BUILT-FROM`. The package `rubberband-lv2` 3.3.0+dfsg-2build1 is installed beside it and not used | [breakfastquay/rubberband tags](https://github.com/breakfastquay/rubberband/tags) | `tags https://github.com/breakfastquay/rubberband` | v4.0.0, same | Ubuntu's 3.3.0 has no Live shifter, and declares the same URIs under the same bundle name; lilv takes the higher `lv2:minorVersion`, and `plugins check` fails a `plugins/` without 4.0.0's ([plugins](plugins.md)) |
| **string-machine chorus** (built) | string-machine-chorus (0.0) | string-machine commit `7050cb1`, DPF commit `f24e531`, in `scripts/build-string-machine-chorus.sh` (`SM_COMMIT`, `DPF_COMMIT`); checked by `BUILT-FROM` | [jpcima/string-machine](https://github.com/jpcima/string-machine) | `git ls-remote https://github.com/jpcima/string-machine HEAD` | HEAD is `7050cb1`, same | No distribution packages it |
| **DPF-Plugins** (`dpf-plugins-lv2`) | magigaverb (1.0) | 1.7+ds-2build3 (`test_library.py`) | [DISTRHO/DPF-Plugins tags](https://github.com/DISTRHO/DPF-Plugins/tags) | `tags https://github.com/DISTRHO/DPF-Plugins` | v1.7, same | |
| **Ardour's a-plugins** (`ardour-lv2-plugins`) | a-reverb (2.4) | Not recorded; Ubuntu 24.04 has 1:8.4.0+ds1-2ubuntu8 | [Ardour/ardour tags](https://github.com/Ardour/ardour/tags) | `tags https://github.com/Ardour/ardour`; `apt-cache policy ardour-lv2-plugins` | Upstream Ardour **9.10** | |
| **Calf** (`calf-plugins`) | calf-envelope-filter (0.90.3) | Not recorded; Ubuntu 24.04 has 0.90.3-4build2 | [calf-studio-gear/calf tags](https://github.com/calf-studio-gear/calf/tags) | `tags https://github.com/calf-studio-gear/calf` | Upstream **0.90.9** | Ubuntu's `calf.lv2` binary is a relative link out of its bundle: `plugins assemble` points it at the file (`plugins.copy_bundles`) |
| **abGate** (`abgate`) | abgate (1.2.0, the package's: the plugin declares none) | 1.2.0-2build3 (`test_string_machine.py`) | [antanasbruzas/abGate tags](https://github.com/antanasbruzas/abGate/tags) | `tags https://github.com/antanasbruzas/abGate` | v1.2.0, same | Declares no version, so its profile's pin is the package's and is the one profile lilv cannot check (`test_string_machine.py`) |
| **mda** (`mda-lv2`): the master's limiter | none | 1.2.10-2build1 (`test_library.py`, `tool/spikes/master_limiter/README.md`) | [drobilla/mda-lv2 on GitLab](https://gitlab.com/drobilla/mda-lv2/-/tags) | `tags https://gitlab.com/drobilla/mda-lv2.git` | Upstream **v1.2.12** | Overshoots at very high input levels; known and accepted (`rigfile.LIMITER`, #137) |
| **x42** (`x42-plugins`): the darc compressor | none | 20230915+ds-1build2 (`test_library.py`) | [x42-plugins.com](https://x42-plugins.com/x42/) | `apt-cache policy x42-plugins` | Could not check from here (x42-plugins.com unreachable, and its GitHub repository has no tags) | |
| **SWH** (`swh-lv2`): lcrDelay | none | Not recorded; Ubuntu 24.04 has 1.0.16+git20160519 | [swh/lv2 tags](https://github.com/swh/lv2/tags) | `tags https://github.com/swh/lv2` | v1.0.16, same | |
| **ZynAddSubFX** (`zynaddsubfx-lv2`): its chorus | none | Not recorded; Ubuntu 24.04 has 3.0.6-6build3 | [zynaddsubfx tags](https://github.com/zynaddsubfx/zynaddsubfx/tags) | `tags https://github.com/zynaddsubfx/zynaddsubfx` | 3.0.6, same | |
| **lilv**, **lv2** (`python3-lilv`, `liblilv-dev`, `lilv-utils`, `lv2-dev`) | — | Rig: lilv 0.26.2-1, lv2-dev 1.18.10-3 (rig record). CI: Ubuntu 24.04's, lilv 0.24.22 (`test_library.py`) | [lv2/lilv tags](https://github.com/lv2/lilv/tags) | `tags https://github.com/lv2/lilv` | Upstream **v0.28.0**; Ubuntu 24.04 still 0.24.22 | `python3-lilv` has no PyPI wheel and needs the unversioned `liblilv-0.so` from `liblilv-dev`; the venv needs `--system-site-packages` ([environment](environment.md)). With serd 0.32, an empty `manifest.ttl` logs an error, so `plugins/archive/manifest.ttl` holds a comment |

## Python

| Dependency | Version used, and where it is pinned or recorded | Upstream | Check | Last checked 2026-11-26 | Known upstream faults we work around |
|---|---|---|---|---|---|
| **Python** | `requires-python = ">=3.11"` (`tool/pyproject.toml`). Rig: 3.14.4 (rig record). CI: 3.11 and 3.13 (`ci.yml`'s matrix), and Ubuntu 24.04's 3.12 in the container | [python/cpython tags](https://github.com/python/cpython/tags) | `tags https://github.com/python/cpython` | Newest 3.15.2 | |
| **PyYAML** | `pyyaml>=6.0` (`tool/pyproject.toml`); the rig's not recorded | [PyPI](https://pypi.org/project/PyYAML/) | `pip index versions pyyaml` | 6.0.4 | |
| **ruamel.yaml** | `ruamel.yaml>=0.19,<0.20` (`tool/pyproject.toml`); the rig's not recorded | [PyPI](https://pypi.org/project/ruamel.yaml/) | `pip index versions ruamel.yaml` | 0.19.1, inside the range | Pinned below 0.20 because `yamlmerge.py` relies on 0.19's comment layout |
| **python-osc** | Rig: 1.10.2 (rig record). Unversioned in the `live` and `agent` extras | [PyPI](https://pypi.org/project/python-osc/) | `pip index versions python-osc` | **1.10.3** | |
| **numpy** | Rig: 2.5.4 (rig record). `numpy>=1.24` (`audio`, `capture`) | [PyPI](https://pypi.org/project/numpy/) | `pip index versions numpy` | **2.5.5** | |
| **aiohttp** | `aiohttp>=3.9` (`agent`); the rig's not recorded | [PyPI](https://pypi.org/project/aiohttp/) | `pip index versions aiohttp` | 3.14.5 | |
| **alsa-midi** | `alsa-midi>=1.0.4,<2` (`midi`); the rig's not recorded | [PyPI](https://pypi.org/project/alsa-midi/) | `pip index versions alsa-midi` | 1.0.4 | Chosen over mido and python-rtmidi, which had no wheel past CPython 3.12 (`midi_listener.py`) |
| **JACK-Client** | Rig: **0.5.7** (rig record). `JACK-Client>=0.5.7,<0.6` (`capture`): only 0.5.7 has been checked, and `capture.py`'s names are tested against it | [PyPI](https://pypi.org/project/JACK-Client/), [spatialaudio/jackclient-python tags](https://github.com/spatialaudio/jackclient-python/tags) | `pip index versions JACK-Client` | 0.5.7, same | Opens libjack on import, so CI installs `libjack-jackd2-0` for the tests |
| **pytest** | Rig: 9.1.1 (rig record). `pytest>=7.0` (`dev`) | [PyPI](https://pypi.org/project/pytest/) | `pip index versions pytest` | **9.2.0** | |
| **setuptools** (build) | `setuptools>=61.0` (`[build-system]`) | [PyPI](https://pypi.org/project/setuptools/) | `pip index versions setuptools` | 84.2.1 | |

## The page and its tests

| Dependency | Version used, and where it is pinned or recorded | Upstream | Check | Last checked 2026-11-26 | Known upstream faults we work around |
|---|---|---|---|---|---|
| **Playwright**, and its **Chromium** (tests only) | `playwright>=1.49,<2` (`ui-test`). CI resolved 1.65.1 on 2026-11-12 (MANIFEST). Chromium is the build that Playwright version drives, fetched by `playwright install --with-deps chromium` (`ci.yml`, `ci/Dockerfile`); Chromium 153 was recorded on 2026-11-17 (`docs/reference/agent.md`) | [PyPI](https://pypi.org/project/playwright/), [microsoft/playwright-python tags](https://github.com/microsoft/playwright-python/tags) | `pip index versions playwright`; `python -m playwright --version` | **1.65.2**, which CI takes on its next run | Playwright 1.65's node links `libatomic.so.1`, absent from a bare ubuntu:24.04: `ci/Dockerfile` installs `libatomic1` |
| **Fonts**: Newsreader, Instrument Sans, Geist Mono | Newsreader 1.003, commit `1ece6a8`; Instrument Sans 1.000, commit `7fa2230`; Geist Mono 1.700, tag `v1.9.2`. All in [`ui/static/fonts/README.md`](../../tool/src/sushi_rig/ui/static/fonts/README.md), held by checksum in `tests/test_ui_install.py` | [productiontype/Newsreader](https://github.com/productiontype/Newsreader), [Instrument/instrument-sans](https://github.com/Instrument/instrument-sans), [vercel/geist-font tags](https://github.com/vercel/geist-font/tags) | `git ls-remote https://github.com/productiontype/Newsreader HEAD`; the same for instrument-sans; `tags https://github.com/vercel/geist-font` | Same: Newsreader's master is still `cfcb4f7` (the README says it has the same file), instrument-sans's `7fa2230`, Geist's newest tag v1.9.2 | |
| **butterchurn** (the visualiser) | 2.6.7, the npm package byte for byte ([`vendor/butterchurn-2.6.7/SOURCE.txt`](../../tool/src/sushi_rig/ui/static/vendor/butterchurn-2.6.7/SOURCE.txt), checksums in `tests/test_ui_visualiser.py`) | [npm](https://www.npmjs.com/package/butterchurn) | `npm view butterchurn dist-tags` | 2.6.7 is still `latest`; 3.0.0-beta.5 is `beta` | |

## CI

| Dependency | Version used, and where it is pinned or recorded | Upstream | Check | Last checked 2026-11-26 | Known upstream faults we work around |
|---|---|---|---|---|---|
| **Base image** `ubuntu:24.04` | Floating tag, in all three stages of `ci/Dockerfile`; no digest pinned, so each build takes the tag's newest | [Docker Hub, ubuntu](https://hub.docker.com/_/ubuntu/tags) | `curl -s https://hub.docker.com/v2/repositories/library/ubuntu/tags/24.04 \| python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["last_updated"], d["digest"])'` | Last updated 2026-11-23 (`sha256:562111b8…`). `26.04`, the rig's release, is on Docker Hub too | |
| **GitHub Actions** | `actions/checkout@v4`, `actions/setup-python@v5` (`ci.yml`, `rig-container.yml`), `actions/upload-artifact@v4` (`rig-container.yml`) | [checkout](https://github.com/actions/checkout/tags), [setup-python](https://github.com/actions/setup-python/tags), [upload-artifact](https://github.com/actions/upload-artifact/tags) | `tags https://github.com/actions/checkout` (and the others) | Newer majors: checkout **v7**, setup-python **v7**, upload-artifact **v7** | |

Not listed: Open Stage Control, whose panel and the v1 path are being
retired, and the system libraries the plugins link (`libfftw3-double3`,
`libsamplerate0`, `libasound2t64`), which come with Ubuntu's release.
