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
#   - The rubberband "live" plugins. See the --waive lines below.
#
# NOT VERIFIED: this file has never been built. The environment it was written
# in has no Docker daemon and blocks GitHub release downloads. The plugin
# half (apt package names, `plugins assemble`/`check`) was run for real against
# an Ubuntu 24.04 LV2 tree; the Sushi fetch and everything after it was not.

FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive

# --- system packages --------------------------------------------------------
#
# The first group is the plugin set. guitarix/mda/dpf/x42/zynaddsubfx/rubberband
# provide 50 of the 52 URIs in plugin-manifest.txt. swh-lv2 and
# ardour-lv2-plugins are NOT in the manifest but are needed all the same: the
# configs in config/ load `lcrDelay` and `urn:ardour:a-reverb`. Checked by
# unpacking both .debs and listing them with lilv, not assumed.
#
# The second group is what the tool needs. `liblilv-dev` and `lv2-dev` are both
# required: python3-lilv's ctypes binding looks for the unversioned
# liblilv-0.so (README: Environment).
#
# curl + jq fetch Sushi below; ca-certificates is what lets curl trust GitHub.
RUN apt-get update && apt-get install -y --no-install-recommends \
        guitarix-lv2 mda-lv2 dpf-plugins-lv2 x42-plugins zynaddsubfx-lv2 \
        rubberband-lv2 swh-lv2 ardour-lv2-plugins \
        # Sushi is built with JACK and ALSA support and links those libraries
        # dynamically. AppImages usually leave them out, so the binary would not
        # start without them even with --dummy. (Unverified: no build has run yet.)
        libjack-jackd2-0 libasound2t64 \
        python3 python3-venv python3-pip python3-lilv \
        lilv-utils liblilv-dev lv2-dev \
        ca-certificates curl jq \
    && rm -rf /var/lib/apt/lists/*

# --- Sushi ------------------------------------------------------------------
#
# UNVERIFIED. GitHub release downloads are blocked where this was written, so
# none of the three values below, nor the lookup itself, has ever run. The
# README says only that Sushi 1.3.0 is used and that the file is
# `Sushi-x86_64.AppImage`. It does not say where that file is published or
# under what release tag. The repo below is the upstream project's; whether it
# attaches an AppImage to its releases is exactly what is unknown, and the file
# may be a build of the rig owner's own.
#
# So rather than guess a download URL, the build asks the GitHub API for the
# release by tag and picks the asset by name, and FAILS if either is absent.
# If this breaks, fix the ARGs (or point them at wherever the AppImage is
# really hosted). Do not paper over it.
ARG SUSHI_REPO=elk-audio/sushi
# Release tag, as GitHub names it. UNVERIFIED: could equally be "v1.3.0".
ARG SUSHI_TAG=1.3.0
# Regex (jq `test`) the release asset's name must match. UNVERIFIED.
ARG SUSHI_ASSET_REGEX=^Sushi-x86_64\.AppImage$

# The API is rate-limited for anonymous callers and CI runners share IPs, so a
# token is optional: pass `--secret id=gh_token,env=GITHUB_TOKEN`. It is a build
# secret, so it never lands in a layer.
RUN --mount=type=secret,id=gh_token,required=false <<'EOF'
set -eu

token=""
if [ -s /run/secrets/gh_token ]; then token="$(cat /run/secrets/gh_token)"; fi

# curl, with the token attached when there is one.
gh_curl() {
    if [ -n "$token" ]; then
        curl -fsSL -H "Authorization: Bearer $token" "$@"
    else
        curl -fsSL "$@"
    fi
}

release_url="https://api.github.com/repos/${SUSHI_REPO}/releases/tags/${SUSHI_TAG}"
gh_curl -H "Accept: application/vnd.github+json" "$release_url" -o /tmp/release.json \
    || { echo "ERROR: could not fetch release ${SUSHI_TAG} of ${SUSHI_REPO} (${release_url}). Check SUSHI_REPO and SUSHI_TAG." >&2; exit 1; }

# -e makes jq exit non-zero when nothing matches (first of [] is null).
asset_url="$(jq -er --arg re "$SUSHI_ASSET_REGEX" \
    '[.assets[] | select(.name | test($re))] | first | .url' /tmp/release.json)" \
    || { echo "ERROR: release ${SUSHI_TAG} has no asset matching ${SUSHI_ASSET_REGEX}. Its assets: $(jq -r '[.assets[].name] | join(", ")' /tmp/release.json)" >&2; exit 1; }

mkdir -p /opt/sushi
gh_curl -H "Accept: application/octet-stream" "$asset_url" -o /opt/sushi/Sushi.AppImage
chmod +x /opt/sushi/Sushi.AppImage
rm /tmp/release.json
EOF

# Containers have no FUSE, so the AppImage cannot mount itself. Extracting it
# unpacks to ./squashfs-root, whose AppRun is the real entry point.
WORKDIR /opt/sushi
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
# PyPI wheel. Whether `elkpy` installs cleanly on Ubuntu's Python 3.12 is
# UNVERIFIED -- the README reports it working on 3.14 only.
COPY . /rig
WORKDIR /rig
RUN python3 -m venv --system-site-packages /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir -e "/rig/tool[dev,live]"
ENV PATH="/opt/venv/bin:${PATH}"

# `./sushi` at the repo root is where tests/test_configs.py and the README look.
RUN ln -s /opt/sushi/squashfs-root/AppRun /rig/sushi

# --- plugins/ ---------------------------------------------------------------
#
# The two rubberband "live" plugins are waived by name. The distro's rubberband
# (3.3.0) predates them, and the only config that loads one is the retired
# config/retired/electric_stereo.json, which `verify --all` skips.
#
# A waiver is not silent: assemble and check print every waived URI on every
# run, and a config that did use one would fail `verify --all`, because Sushi
# cannot load a plugin that is not there. Revisit these lines if the manifest
# or a live config starts depending on them.
RUN sushi-rig plugins assemble --from /usr/lib/lv2 --to /rig/plugins \
        --waive 'http://breakfastquay.com/rdf/lv2-rubberband#livemono' \
        --waive 'http://breakfastquay.com/rdf/lv2-rubberband#livestereo' \
    && sushi-rig plugins check /rig/plugins \
        --waive 'http://breakfastquay.com/rdf/lv2-rubberband#livemono' \
        --waive 'http://breakfastquay.com/rdf/lv2-rubberband#livestereo'

# --- preflight --------------------------------------------------------------
#
# README: "./sushi --version must list lv2". `test -x` follows the symlink, so a
# dangling link fails the build here instead of letting tests/test_configs.py
# skip itself silently (its `needs_sushi` marker skips when ./sushi is absent).
RUN test -x /rig/sushi \
    && /rig/sushi --version | tee /dev/stderr | grep -qi lv2

WORKDIR /rig/tool
CMD ["pytest", "-m", "slow", "-rs"]
