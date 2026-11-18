#!/usr/bin/env bash
# Build Rubber Band 4.0.0's LV2 bundle (rubberband.lv2), for the octave pedal
# (#164): its Live shifter, http://breakfastquay.com/rdf/lv2-rubberband#livemono.
#
#   scripts/build-rubberband4.sh DIR
#
# leaves DIR/rubberband.lv2, so DIR is an LV2 directory: `sushi-rig plugins
# assemble --from DIR --from /usr/lib/lv2` takes the bundle from it, or the
# bundle is copied into plugins/ by hand (docs/reference/plugins.md says which,
# on the rig and in CI's image). Nothing is installed anywhere else, and DIR
# is not one of lilv's default directories unless you make it one: keep it off
# them, so that the system's lilv goes on seeing only what apt installed.
#
# Why it is built: Ubuntu 24.04 packages Rubber Band 3.3.0, which has no Live
# shifter. 3.3.0's bundle has the same name and declares the same URIs (mono,
# stereo, r3mono, r3stereo); 4.0.0 declares those and livemono and livestereo,
# at a higher lv2:minorVersion (6 against 4), so a lilv that sees both takes
# 4.0.0's for every URI and ignores 3.3.0's bundle. plugins/ holds exactly one
# rubberband.lv2, and `sushi-rig plugins check` fails if it is 3.3.0's, since
# plugin-manifest.txt lists livemono.
#
# Pinned: the tag v4.0.0 and the commit it names, checked after the clone, so a
# moved tag fails here instead of building something else.
#
# Needs network (github.com), and:
#   sudo apt install git meson ninja-build g++ pkg-config libfftw3-dev \
#       libsamplerate0-dev lv2-dev
# The bundle links libfftw3.so.3 and libsamplerate.so.0 at run time (the
# libfftw3-double3 and libsamplerate0 packages, which the -dev ones bring);
# Rubber Band itself is linked in statically.
set -euo pipefail

TAG=v4.0.0
COMMIT=1d95888bec3ae0a17c0c4af791810d5a63f6bc35
REPO=https://github.com/breakfastquay/rubberband
# The options of the build #153 measured (tool/spikes/plugin_research/): FFTW
# and libsamplerate, and nothing but the LV2 plugins.
OPTIONS=(-Dfft=fftw -Dresampler=libsamplerate -Dlv2=enabled -Dladspa=disabled
         -Dvamp=disabled -Djni=disabled -Dcmdline=disabled -Dtests=disabled)

out=$(realpath -m "${1:?usage: build-rubberband4.sh DIR (DIR/rubberband.lv2 is made)}")
mkdir -p "$out"
# The new bundle is staged inside DIR, so the swap below is two renames on one
# filesystem, but one level down: lilv reads <entry>/manifest.ttl for each
# entry at DIR's top level and does not recurse, so it reads the staging
# folder's own manifest (which declares nothing) and never the bundles inside
# it. A staging folder left by a run that died (power, kill -9) is therefore
# never loaded as a second Rubber Band beside the real one.
#
# Cleaning up, at the start for a leftover and on any exit of this run (an
# error, Ctrl-C or a kill), never loses the bundle DIR had: if the old one was
# moved aside and the new one is not in its place, the old one is moved back
# first, and if that fails the staging folder is kept, with it inside.
stage="$out/.rubberband4-staging"
work=""
restore_and_clean() {
    [ -n "$work" ] && rm -rf "$work"
    if [ -d "$stage/old.lv2" ] && [ ! -e "$out/rubberband.lv2" ]; then
        if ! mv "$stage/old.lv2" "$out/rubberband.lv2"; then
            echo "build-rubberband4.sh: could not put the previous bundle back; it is" \
                 "$stage/old.lv2: move it to $out/rubberband.lv2 by hand" >&2
            return
        fi
        echo "build-rubberband4.sh: put the previous $out/rubberband.lv2 back" >&2
    fi
    rm -rf "$stage"
}
restore_and_clean
work=$(mktemp -d)
trap restore_and_clean EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

git -c advice.detachedHead=false clone -q --depth 1 --branch "$TAG" "$REPO" "$work/src"
got=$(git -C "$work/src" rev-parse HEAD)
if [ "$got" != "$COMMIT" ]; then
    echo "build-rubberband4.sh: $REPO's $TAG is $got, not the pinned $COMMIT;" \
         "refusing to build it" >&2
    exit 1
fi

meson setup "$work/build" "$work/src" --prefix="$work/install" --libdir=lib \
    "${OPTIONS[@]}" >"$work/setup.log" || { cat "$work/setup.log" >&2; exit 1; }
ninja -C "$work/build" >"$work/build.log" || { tail -40 "$work/build.log" >&2; exit 1; }
ninja -C "$work/build" install >"$work/install.log" || { cat "$work/install.log" >&2; exit 1; }

bundle="$work/install/lib/lv2/rubberband.lv2"
grep -q 'rubberband:livemono' "$bundle/manifest.ttl" || {
    echo "build-rubberband4.sh: the built bundle declares no livemono" >&2; exit 1; }
# What it was built from, beside the Turtle; lilv reads only what manifest.ttl
# names, so this is for a person alone.
printf 'Rubber Band %s, commit %s, from %s\nmeson %s\n' \
    "$TAG" "$COMMIT" "$REPO" "${OPTIONS[*]}" > "$bundle/BUILT-FROM"

# Replace, not merge: a bundle left from another build must not survive in
# part. The old bundle is renamed into the staging folder, then the new one
# into place: DIR is without a rubberband.lv2 only between two renames, and a
# run that fails or is stopped there puts the old one back (restore_and_clean).
mkdir "$stage"
# Declares nothing, so even a leftover folder is quiet in lilv's log, as
# plugins/archive/ is (sushi_rig/plugins.py, ARCHIVE_MANIFEST).
echo '# Rubber Band staging: declares nothing (scripts/build-rubberband4.sh).' > "$stage/manifest.ttl"
cp -r "$bundle" "$stage/rubberband.lv2"
if [ -e "$out/rubberband.lv2" ]; then
    mv "$out/rubberband.lv2" "$stage/old.lv2"
fi
mv "$stage/rubberband.lv2" "$out/rubberband.lv2"
echo "built $out/rubberband.lv2 (Rubber Band $TAG, $COMMIT)"
