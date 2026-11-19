#!/usr/bin/env bash
# Build string-machine chorus's LV2 bundle (string-machine-chorus.lv2), the
# Solina-style string-ensemble chorus of the string-machine rig (#177):
# http://jpcima.sdf1.org/lv2/string-machine-chorus, 1 audio in, 2 out.
#
#   scripts/build-string-machine-chorus.sh DIR
#
# leaves DIR/string-machine-chorus.lv2, so DIR is an LV2 directory: `sushi-rig
# plugins assemble --from DIR --from /usr/lib/lv2` takes the bundle from it, or
# DIR is plugins/ itself (docs/reference/plugins.md says which, on the rig and
# in CI's image). Nothing is installed anywhere else.
#
# Why it is built: no distribution packages it. It is the chorus out of
# jpcima's string-machine synth, a DPF plugin. Its own Makefile also builds an
# OpenGL UI the rig cannot show, and needs DPF's whole submodule tree for it,
# so the DSP alone is compiled here with g++, the way #166's research built
# the bundle it measured (tool/spikes/synth_research/build_extras.sh), and DPF's
# own generator writes the Turtle from the built binary.
#
# Licences: the string-machine repository is BSL-1.0; the chorus plugin
# declares GPL-2.0-or-later (its meta/DistrhoPluginInfo.h). DPF is ISC.
#
# Pinned: string-machine at the commit below, and DPF at the commit that
# string-machine commit records for its submodule. Both are fetched by commit,
# not by branch or tag, and DPF's pin is checked against what string-machine
# records, so neither can move under this script.
#
# Needs network (github.com), and:
#   sudo apt install git gcc g++
# The bundle links only the C++ runtime (libstdc++, libm, libgcc_s, libc).
set -euo pipefail

SM_REPO=https://github.com/jpcima/string-machine
SM_COMMIT=7050cb1516fbaf5aaaa2c6cc9999fe5ac100d746   # 2022-02-19, "Add build based on cmake"
DPF_REPO=https://github.com/DISTRHO/DPF
DPF_COMMIT=f24e5317cd7b57c8ab36b0987ec29a5579cd5dd2  # what SM_COMMIT records for dpf/
URI=http://jpcima.sdf1.org/lv2/string-machine-chorus
NAME=string-machine-chorus
# The flags #166 measured the plugin with (build_extras.sh), so its figures hold.
CXXFLAGS=(-O2 -fPIC -std=gnu++17 -DNDEBUG -DDISTRHO_PLUGIN_TARGET_LV2 -fvisibility=hidden -ffast-math)

out=$(realpath -m "${1:?usage: build-string-machine-chorus.sh DIR (DIR/$NAME.lv2 is made)}")
mkdir -p "$out"
# Staged inside DIR, one level down in a folder whose manifest declares
# nothing, so the swap below is two renames on one filesystem and a folder
# left by a run that died is never loaded as a second bundle: lilv reads
# <entry>/manifest.ttl for each entry at DIR's top level and does not recurse
# (as build-rubberband4.sh does). Cleaning up, at the start for a leftover and
# on any exit of this run, never loses the bundle DIR had: if the old one was
# moved aside and the new one is not in its place, it is moved back first,
# and if that fails the staging folder is kept, with it inside.
stage="$out/.$NAME-staging"
work=""
restore_and_clean() {
    [ -n "$work" ] && rm -rf "$work"
    if [ -d "$stage/old.lv2" ] && [ ! -e "$out/$NAME.lv2" ]; then
        if ! mv "$stage/old.lv2" "$out/$NAME.lv2"; then
            echo "build-string-machine-chorus.sh: could not put the previous bundle back; it is" \
                 "$stage/old.lv2: move it to $out/$NAME.lv2 by hand" >&2
            return
        fi
        echo "build-string-machine-chorus.sh: put the previous $out/$NAME.lv2 back" >&2
    fi
    rm -rf "$stage"
}
restore_and_clean
work=$(mktemp -d)
trap restore_and_clean EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

fetch() {   # repo commit dir: exactly that commit, nothing else
    git init -q "$3"
    git -C "$3" fetch -q --depth 1 "$1" "$2"
    git -C "$3" -c advice.detachedHead=false checkout -q FETCH_HEAD
    local got
    got=$(git -C "$3" rev-parse HEAD)
    if [ "$got" != "$2" ]; then
        echo "build-string-machine-chorus.sh: fetched $got from $1, not the pinned $2" >&2
        exit 1
    fi
}

sm="$work/string-machine"
fetch "$SM_REPO" "$SM_COMMIT" "$sm"
recorded=$(git -C "$sm" ls-tree HEAD dpf | awk '{print $3}')
if [ "$recorded" != "$DPF_COMMIT" ]; then
    echo "build-string-machine-chorus.sh: string-machine $SM_COMMIT records DPF $recorded," \
         "not the pinned $DPF_COMMIT; refusing to build" >&2
    exit 1
fi
rmdir "$sm/dpf" 2>/dev/null || true
fetch "$DPF_REPO" "$DPF_COMMIT" "$sm/dpf"

dir="$sm/plugins/$NAME"
dpf="$sm/dpf"
# The plugin's own info header, with its UI switched off whatever it says.
meta="$work/meta"
mkdir -p "$meta"
{ echo "#include \"$dir/meta/DistrhoPluginInfo.h\""
  echo "#undef DISTRHO_PLUGIN_HAS_UI"
  echo "#define DISTRHO_PLUGIN_HAS_UI 0"; } > "$meta/DistrhoPluginInfo.h"

bundle="$work/$NAME.lv2"
mkdir -p "$bundle"
g++ "${CXXFLAGS[@]}" -I"$meta" -I"$dpf/distrho" -I"$dir" -I"$dir/sources" -I"$dir/gen" \
    -I"$dir/thirdparty/blink" \
    "$dpf/distrho/DistrhoPluginMain.cpp" \
    "$dir/ChorusPlugin.cpp" "$dir/ChorusShared.cpp" "$dir/sources/SolinaChorus.cpp" \
    "$dir/sources/bbd/bbd_line.cpp" "$dir/sources/bbd/bbd_filter.cpp" \
    "$dir/sources/dsp/Delay3Phase.cpp" "$dir/gen/dsp/Delay3PhaseDigital.cpp" \
    "$dir/gen/dsp/LFO3PhaseDual.cpp" \
    -shared -o "$bundle/$NAME.so" -lpthread
gcc -O2 -o "$work/lv2_ttl_generator" "$dpf/utils/lv2-ttl-generator/lv2_ttl_generator.c" -ldl
( cd "$bundle" && "$work/lv2_ttl_generator" "./$NAME.so" >/dev/null )

grep -q "<$URI>" "$bundle/manifest.ttl" || {
    echo "build-string-machine-chorus.sh: the built bundle does not declare $URI" >&2; exit 1; }
# What it was built from, beside the Turtle; lilv reads only what manifest.ttl
# names, so this is for a person, and for tests/test_string_machine.py.
printf 'string-machine chorus, string-machine commit %s, from %s\nDPF commit %s, from %s\ng++ %s\n' \
    "$SM_COMMIT" "$SM_REPO" "$DPF_COMMIT" "$DPF_REPO" "${CXXFLAGS[*]}" > "$bundle/BUILT-FROM"

# Replace, not merge: the old bundle is renamed into the staging folder, then
# the new one into place; DIR is without the bundle only between two renames,
# and a run stopped there puts the old one back (restore_and_clean).
mkdir "$stage"
echo "# string-machine chorus staging: declares nothing (scripts/build-string-machine-chorus.sh)." \
    > "$stage/manifest.ttl"
cp -r "$bundle" "$stage/$NAME.lv2"
if [ -e "$out/$NAME.lv2" ]; then
    mv "$out/$NAME.lv2" "$stage/old.lv2"
fi
mv "$stage/$NAME.lv2" "$out/$NAME.lv2"
echo "built $out/$NAME.lv2 (string-machine $SM_COMMIT, DPF $DPF_COMMIT)"
