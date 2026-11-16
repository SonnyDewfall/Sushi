#!/usr/bin/env bash
# Build the octave candidates no Ubuntu 24.04 package provides, into DIR:
#
#   tool/spikes/plugin_research/build_extras.sh DIR
#
#   DIR/lv2       MOD's Drop, SuperCapo, SuperWhammy and Capo (moddevices/
#                 mod-pitchshifter at 3140d9a), and a bundle naming Ubuntu's own
#                 Rubber Band 3.3.0 R3 shifters by the URIs its library exports
#   DIR/rb4/lv2   Rubber Band 4.0.0's rubberband.lv2 alone (its URIs collide
#                 with Ubuntu's 3.3.0, so it is kept apart)
#
# then: python3 tool/spikes/plugin_research/octave.py \
#           --extra-lv2 DIR/lv2 --rubberband4 DIR/rb4/lv2
#
# Needs network, git, g++, make, meson, ninja, pkg-config, and
#   apt-get install libarmadillo-dev libfftw3-dev python3-mpmath \
#       libsamplerate0-dev lv2-dev rubberband-lv2
# Nothing is installed system-wide; everything lands in DIR.
set -euo pipefail
out=$(realpath -m "${1:?usage: build_extras.sh DIR}")
mkdir -p "$out/src" "$out/lv2" "$out/rb4"

# MOD's pitch shifters. Their Makefile also copies a harmonizer.wisdom file the
# repository does not carry (a dangling link); without it the plugins plan
# their FFTs with FFTW_ESTIMATE, and say so on stdout. The copy is done here.
if [ ! -d "$out/src/mod-pitchshifter" ]; then
    git clone -q https://github.com/moddevices/mod-pitchshifter "$out/src/mod-pitchshifter"
fi
git -C "$out/src/mod-pitchshifter" checkout -q 3140d9a3c29a44f9c79813b34de1e44404a39b98
for p in Drop SuperCapo SuperWhammy Capo; do
    make -s -C "$out/src/mod-pitchshifter/$p" >/dev/null 2>"$out/build-$p.log"
    name=mod-$(echo "$p" | tr A-Z a-z)
    mkdir -p "$out/lv2/$name.lv2"
    cp "$out/src/mod-pitchshifter/$p/$name.so" "$out/src/mod-pitchshifter/$p"/ttl/*.ttl "$out/lv2/$name.lv2/"
done

# Ubuntu 24.04's rubberband-lv2 3.3.0 declares <...lv2-rubberband#r3mono> and
# <#r3stereo> in its Turtle, but its library exports them as
# <...lv2-rubberband-r3#mono> and <#stereo>, so lilv finds the first and cannot
# instantiate it. This bundle names the library's own URIs, to hear R3 at all.
r3="$out/lv2/rubberband-r3-fixed.lv2"
mkdir -p "$r3"
cat > "$r3/manifest.ttl" <<'EOF'
@prefix lv2: <http://lv2plug.in/ns/lv2core#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .

<http://breakfastquay.com/rdf/lv2-rubberband-r3#mono>
        a lv2:Plugin ;
        lv2:binary <file:///usr/lib/lv2/rubberband.lv2/lv2-rubberband.so> ;
        rdfs:seeAlso <r3.ttl> .

<http://breakfastquay.com/rdf/lv2-rubberband-r3#stereo>
        a lv2:Plugin ;
        lv2:binary <file:///usr/lib/lv2/rubberband.lv2/lv2-rubberband.so> ;
        rdfs:seeAlso <r3.ttl> .
EOF
sed -e 's#^rubberband:r3mono#<http://breakfastquay.com/rdf/lv2-rubberband-r3\#mono>#' \
    -e 's#^rubberband:r3stereo#<http://breakfastquay.com/rdf/lv2-rubberband-r3\#stereo>#' \
    /usr/lib/lv2/rubberband.lv2/lv2-rubberband.ttl > "$r3/r3.ttl"

# Rubber Band 4.0.0, which adds the Live shifter and fixes the R3 URIs.
if [ ! -d "$out/src/rubberband" ]; then
    git clone -q --depth 1 -b v4.0.0 https://github.com/breakfastquay/rubberband "$out/src/rubberband"
fi
meson setup --wipe "$out/rb-build" "$out/src/rubberband" --prefix="$out/rb-install" \
    -Dfft=fftw -Dresampler=libsamplerate -Dlv2=enabled -Dladspa=disabled -Dvamp=disabled \
    -Djni=disabled -Dcmdline=disabled -Dtests=disabled >/dev/null
ninja -C "$out/rb-build" >/dev/null
ninja -C "$out/rb-build" install >/dev/null
mkdir -p "$out/rb4/lv2"
cp -r "$(find "$out/rb-install" -type d -name rubberband.lv2 | head -1)" "$out/rb4/lv2/"
echo "built into $out: lv2/ ($(ls "$out/lv2" | tr '\n' ' ')) and rb4/lv2/rubberband.lv2"
