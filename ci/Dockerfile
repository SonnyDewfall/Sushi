# syntax=docker/dockerfile:1
#
# A "dummy rig": everything the full test suite and `sushi-rig verify --all` need,
# with no audio hardware, no JACK and no GUI -- so CI can run them without the
# rig machine. Build from the repository root:
#
#   docker build -f ci/Dockerfile -t sushi-rig-ci .
#
# What this reproduces from docs/reference/ (environment.md, plugins.md), and how:
#   plugins/   assembled from distro packages and Rubber Band 4.0.0 (built by
#              scripts/build-rubberband4.sh, as on the rig) by `sushi-rig
#              plugins assemble`, then proven self-contained by `sushi-rig
#              plugins check` (which runs with LV2_PATH set to plugins/ alone,
#              as the rig does)
#   ./sushi    the Sushi AppImage, extracted
#
# What it deliberately does NOT contain, and what that costs:
#   - The neural amp (neural-amp-modeler-lv2) and Carla. The distro does not
#     package NAM, and nothing here needs it: the amp runs in Carla *outside*
#     Sushi (see sushi_rig/amp.py), configs carry only an inert `_amp` section,
#     and the amp's tests (tests/test_amp.py) are pure. Only `sushi-rig up`
#     starts it. So this image proves the Sushi half of the rig and says
#     nothing about the amp half. If that ever needs covering, NAM has to be
#     built from source (mikeoliphant/neural-amp-modeler-lv2) -- not done.
#
# VERIFIED: CI (.github/workflows/rig-container.yml) builds this image with the
# real Sushi 1.3.0, then runs the full suite and `sushi-rig verify --all` in
# it. It also builds locally with a stand-in in place of the Sushi AppImage.

# --- Rubber Band 4.0.0, built (#164) -----------------------------------------
#
# The octave pedal is Rubber Band 4.0's Live shifter
# (http://breakfastquay.com/rdf/lv2-rubberband#livemono), and Ubuntu 24.04
# packages only 3.3.0, which has none. It is built here by the same script, at
# the same pinned tag and commit, that builds it on the rig machine
# (scripts/build-rubberband4.sh; docs/reference/plugins.md), in a stage of its
# own so the compiler stays out of the image. Its bundle goes into plugins/
# below, through `plugins assemble`; the system's /usr/lib/lv2 keeps Ubuntu's
# 3.3.0 bundle, which lilv's default path and the tests that probe installed
# plugins read, and which plugins/ never holds.
FROM ubuntu:24.04 AS rubberband
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates git meson ninja-build g++ pkg-config \
        libfftw3-dev libsamplerate0-dev lv2-dev \
    && rm -rf /var/lib/apt/lists/*
COPY scripts/build-rubberband4.sh /build/build-rubberband4.sh
RUN /build/build-rubberband4.sh /opt/rubberband-4.0.0/lv2

FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive

# --- system packages --------------------------------------------------------
#
# The first group is the plugin set: together these packages provide every URI
# in plugin-manifest.txt except the neural amp's, which is waived below,
# and Rubber Band 4.0's Live shifter, which is built above. libfftw3-double3
# and libsamplerate0 are what that build links at run time. swh-lv2 and ardour-lv2-plugins are there for
# `lcrDelay` and `urn:ardour:a-reverb`, which the configs load. Checked with
# `sushi-rig plugins check` against a real Ubuntu 24.04 LV2 tree, not assumed.
#
# The second group is what the tool needs. `liblilv-dev` and `lv2-dev` are both
# required: python3-lilv's ctypes binding looks for the unversioned
# liblilv-0.so (docs/reference/environment.md).
#
RUN apt-get update && apt-get install -y --no-install-recommends \
        guitarix-lv2 mda-lv2 dpf-plugins-lv2 x42-plugins zynaddsubfx-lv2 \
        rubberband-lv2 swh-lv2 ardour-lv2-plugins \
        libfftw3-double3 libsamplerate0 \
        # Sushi is built with JACK and ALSA support and links those libraries
        # dynamically, and AppImages usually leave them out. With them, the
        # real Sushi starts here (CI); without them it would likely not.
        libjack-jackd2-0 libasound2t64 \
        python3 python3-venv python3-pip python3-lilv \
        lilv-utils liblilv-dev lv2-dev \
        # git: a save reports the rig file's own git state (save.file_git_state)
        # and its tests build a real repository; the rig is always a checkout.
        git \
        # libatomic1: playwright's driver is a node binary carried in the wheel,
        # and playwright 1.65.1's (node 26) links libatomic.so.1, which a bare
        # ubuntu:24.04 does not have. `playwright install --with-deps` cannot
        # bring it: it is that node that runs the install, and it died loading
        # (2026-11-12, the first build to resolve 1.65.1; 1.64.1's node 24,
        # on the build before, does not link it). ci.yml's runner image has
        # the library, so the same playwright installed there without a word.
        libatomic1 \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# --- Sushi ------------------------------------------------------------------
#
# The AppImage arrives through a named build context, `sushi`, holding one file,
# Sushi.AppImage. It is fetched BEFORE the build rather than in here, for two
# reasons:
#
#   - A failed fetch has to say why. Inside `docker build` a failing RUN reaches
#     CI as "exit code 1" and nothing else; on the runner, `gh release download`
#     can put the release's real asset list into a CI annotation.
#   - The image then needs no network credentials or build secrets.
#
# The workflow does the fetch (.github/workflows/rig-container.yml). Locally:
#   gh release download 1.3.0 --repo elk-audio/sushi \
#       --pattern 'sushi-linux-x86_64_1_3_0.zip' --dir ci/dl
#   unzip ci/dl/sushi-linux-x86_64_1_3_0.zip -d ci/dl/unzipped
#   mv "$(find ci/dl/unzipped -name '*.AppImage')" ci/dl/Sushi.AppImage
#   docker build -f ci/Dockerfile --build-context sushi=ci/dl -t sushi-rig-ci .
#
# Containers have no FUSE, so the AppImage cannot mount itself. Extracting it
# unpacks to ./squashfs-root, whose AppRun is the real entry point.
WORKDIR /opt/sushi
COPY --from=sushi --chmod=755 Sushi.AppImage /opt/sushi/Sushi.AppImage
RUN ./Sushi.AppImage --appimage-extract >/dev/null \
    && rm Sushi.AppImage \
    && test -x squashfs-root/AppRun

# --- the tool ---------------------------------------------------------------
#
# The repo lives at /rig. Editable install is required, not a convenience:
# `checkout_root()` finds the rig (config/, plugins/, ./sushi) relative to the
# package's own file, so a copy installed into site-packages would look for
# them in the wrong place.
#
# --system-site-packages is how the venv sees apt's python3-lilv, which has no
# PyPI wheel. `elkpy` installs cleanly on Ubuntu's Python 3.12 (checked in this
# image; docs/reference/environment.md reports 3.14 too). `audio` brings numpy, without which the
# signal and analysis tests skip -- and a skip fails the job. `agent` brings
# aiohttp, for the same reason, and `capture` brings JACK-Client, which needs the
# libjack the apt line above installs: without both the capture tests skip.
# `midi` brings alsa-midi, which links the libasound2t64 installed above; without
# it test_midi_alsa.py skips. `ui-test` brings playwright, for the web UI's
# browser tests (#31), which skip without it.
COPY . /rig
WORKDIR /rig
RUN python3 -m venv --system-site-packages /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir -e "/rig/tool[dev,live,audio,agent,capture,midi,ui-test]"
ENV PATH="/opt/venv/bin:${PATH}"

# --- Chromium, for the web UI's browser tests --------------------------------
#
# The playwright package carries no browser. `playwright install` downloads the
# Chromium build matching the installed playwright from Playwright's own CDN
# (network at build time, as pip and apt already need), and --with-deps runs
# apt-get for the system libraries it links (this image is root, so no sudo).
# Fixed path, so the browser does not depend on whose HOME the tests run under.
# Without this the browser tests would fail to launch Chromium -- a failure,
# not a skip, by design (tests/test_ui.py). NOT VERIFIED: this step was written
# where Docker could not be run; the first CI build of it is its test.
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright
RUN playwright install --with-deps chromium \
    && rm -rf /var/lib/apt/lists/*

# `./sushi` at the repo root is where tests/test_configs.py and docs/reference/ look.
RUN ln -s /opt/sushi/squashfs-root/AppRun /rig/sushi

# --- plugins/ ---------------------------------------------------------------
#
# Every URI the rig needs comes from the packages above *except the neural amp*,
# which no distro packages and which therefore has to be waived by name.
#
# The waiver is the point, not a workaround. NAM is the one component
# `plugins assemble` cannot reproduce, so it is the one most likely to be
# missing on a fresh machine — and until the URI was added to
# `plugin-manifest.txt`, `check` did not know to look for it and would report
# every URI resolving while `plugins/` held no amp at all. Naming it here makes
# "NAM is the thing you carry by hand" something the build says out loud on
# every run, which is the same bargain `--waive` was built for.
#
# The consequence for this image: it has no amp, so the container cannot run
# the rig's amped path. That was already true; it is now stated.
#
# Rubber Band's directory comes first, and both trees hold a rubberband.lv2
# declaring the same URIs. lilv takes the higher lv2:minorVersion for each URI
# (4.0.0's 6 against 3.3.0's 4) and ignores the older bundle, so every Rubber
# Band URI resolves to 4.0.0's; were it ever split between the two, `assemble`
# would refuse the two bundles of one name, and `check` fails a plugins/ whose
# Rubber Band has no Live shifter. tests/test_octave.py checks plugins/ holds
# the pinned build and renders it under Sushi.
ARG NAM_URI=http://github.com/mikeoliphant/neural-amp-modeler-lv2
COPY --from=rubberband /opt/rubberband-4.0.0/lv2 /opt/rubberband-4.0.0/lv2
RUN sushi-rig plugins assemble --from /opt/rubberband-4.0.0/lv2 --from /usr/lib/lv2 \
        --to /rig/plugins --waive "$NAM_URI" \
    && sushi-rig plugins check /rig/plugins --waive "$NAM_URI" \
    && grep -q 'Rubber Band v4.0.0' /rig/plugins/rubberband.lv2/BUILT-FROM \
    && ! ldd /rig/plugins/rubberband.lv2/lv2-rubberband.so | grep 'not found'

# --- the rig machine's layout, for tests/test_probe.py ----------------------
#
# Those tests look for the rig where the rig machine keeps it, not relative to
# the checkout: plugins at ~/Sushi/plugins (machine-wide and untracked, so that
# a git worktree can share it) and the CLI at tool/.venv/bin/sushi-rig. Without
# these two links the five tests marked needs_rig_plugins skip with "folder not
# present" -- found by running the whole suite here, where it had never run --
# and a skip fails the job. Linking is cheaper than teaching the tests about
# the container, and keeps them asserting against the layout the rig really has.
RUN ln -s /rig /root/Sushi \
    && ln -s /opt/venv /rig/tool/.venv

# --- preflight --------------------------------------------------------------
#
# docs/reference/environment.md: Sushi is built with lv2, so `./sushi --version`
# must list it. `test -x` follows the symlink, so a
# dangling link fails the build here instead of letting tests/test_configs.py
# skip itself silently (its `needs_sushi` marker skips when ./sushi is absent).
RUN test -x /rig/sushi \
    && /rig/sushi --version | tee /dev/stderr | grep -qi lv2

WORKDIR /rig/tool
CMD ["pytest", "-rs"]
