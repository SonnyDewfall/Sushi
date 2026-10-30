# syntax=docker/dockerfile:1
#
# A "dummy rig": everything the slow suite and `sushi-rig verify --all` need,
# with no audio hardware, no JACK and no GUI -- so CI can run them without the
# rig machine. Build from the repository root:
#
#   docker build -f ci/Dockerfile -t sushi-rig-ci .
#
# What this reproduces from the README, and how:
#   plugins/   assembled from distro packages by `sushi-rig plugins assemble`,
#              then proven self-contained by `sushi-rig plugins check` (which
#              runs with LV2_PATH set to plugins/ alone, as the rig does)
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
# real Sushi 1.3.0, then runs the slow suite and `sushi-rig verify --all` in
# it. It also builds locally with a stand-in in place of the Sushi AppImage.

FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive

# --- system packages --------------------------------------------------------
#
# The first group is the plugin set: together these packages provide all 54
# URIs in plugin-manifest.txt. swh-lv2 and ardour-lv2-plugins are there for
# `lcrDelay` and `urn:ardour:a-reverb`, which the configs load. Checked with
# `sushi-rig plugins check` against a real Ubuntu 24.04 LV2 tree, not assumed.
#
# The second group is what the tool needs. `liblilv-dev` and `lv2-dev` are both
# required: python3-lilv's ctypes binding looks for the unversioned
# liblilv-0.so (README: Environment).
#
RUN apt-get update && apt-get install -y --no-install-recommends \
        guitarix-lv2 mda-lv2 dpf-plugins-lv2 x42-plugins zynaddsubfx-lv2 \
        rubberband-lv2 swh-lv2 ardour-lv2-plugins \
        # Sushi is built with JACK and ALSA support and links those libraries
        # dynamically, and AppImages usually leave them out. With them, the
        # real Sushi starts here (CI); without them it would likely not.
        libjack-jackd2-0 libasound2t64 \
        python3 python3-venv python3-pip python3-lilv \
        lilv-utils liblilv-dev lv2-dev \
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
# image; the README reports 3.14 too). `audio` brings numpy, without which the
# signal and analysis tests skip -- and a skip fails the slow job.
COPY . /rig
WORKDIR /rig
RUN python3 -m venv --system-site-packages /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir -e "/rig/tool[dev,live,audio]"
ENV PATH="/opt/venv/bin:${PATH}"

# `./sushi` at the repo root is where tests/test_configs.py and the README look.
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
ARG NAM_URI=http://github.com/mikeoliphant/neural-amp-modeler-lv2
RUN sushi-rig plugins assemble --from /usr/lib/lv2 --to /rig/plugins --waive "$NAM_URI" \
    && sushi-rig plugins check /rig/plugins --waive "$NAM_URI"

# --- preflight --------------------------------------------------------------
#
# README: "./sushi --version must list lv2". `test -x` follows the symlink, so a
# dangling link fails the build here instead of letting tests/test_configs.py
# skip itself silently (its `needs_sushi` marker skips when ./sushi is absent).
RUN test -x /rig/sushi \
    && /rig/sushi --version | tee /dev/stderr | grep -qi lv2

WORKDIR /rig/tool
CMD ["pytest", "-m", "slow", "-rs"]
