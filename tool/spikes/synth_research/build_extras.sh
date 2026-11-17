#!/usr/bin/env bash
# Build what no Ubuntu 24.04 package provides for the synth research (#166),
# into DIR:
#
#   tool/spikes/synth_research/build_extras.sh DIR
#
#   DIR/lv2/string-machine-chorus.lv2   jpcima's Solina ensemble chorus, 1 in / 2 out
#   DIR/lv2/string-machine.lv2          jpcima's string machine synth (MIDI in, 2 out)
#   DIR/lv2/gx_slowgear.lv2            brummer10's GxSlowGear, a volume swell
#   DIR/lv2/guitarmidi.lv2              GuitarMidi-LV2 v3.2, the author's own
#                                       Ubuntu 24.04 package, unpacked, not installed
#   DIR/lv2/spike-pitch2midi.lv2        a small monophonic pitch-to-MIDI tracker
#                                       written for this spike (pitch2midi.c), to
#                                       test Sushi's MIDI routing with a known tracker
#   DIR/lv2/spike-midiprobe.lv2         a MIDI-to-audio probe (midiprobe.c): sits
#                                       where a synth would and plays what MIDI
#                                       reaches it as a readable piano roll
#   DIR/lv2/spike-pacer.lv2             a pass-through that makes Sushi's offline
#                                       frontend run at real time (pacer.c)
#
# Rubber Band 4.0 comes from ../plugin_research/build_extras.sh (DIR/rb4/lv2).
#
# string-machine is built with g++ straight from its sources and DPF (both at
# the commits below), DSP only: its own Makefile also builds an OpenGL UI the
# rig cannot show. Needs network, git, g++, lv2-dev, dpkg-deb.
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
out=$(realpath -m "${1:?usage: build_extras.sh DIR}")
mkdir -p "$out/src" "$out/lv2" "$out/obj"

# --- string-machine (BSL-1.0), at 7050cb1 (2022-02-19), DPF at its submodule commit
sm="$out/src/string-machine"
if [ ! -d "$sm" ]; then
    git clone -q --recursive --shallow-submodules https://github.com/jpcima/string-machine "$sm"
fi
git -C "$sm" checkout -q 7050cb1
dpf="$sm/dpf"
CXXFLAGS="-O2 -fPIC -std=gnu++17 -DNDEBUG -DDISTRHO_PLUGIN_TARGET_LV2 -fvisibility=hidden -ffast-math"

build_dpf() {   # name plugin-dir uri-bundle files...
    local name=$1 dir=$2; shift 2
    local meta="$out/obj/$name-meta"
    mkdir -p "$meta"
    # the plugin's own info header, with its UI switched off
    { echo "#include \"$dir/meta/DistrhoPluginInfo.h\""
      echo "#undef DISTRHO_PLUGIN_HAS_UI"
      echo "#define DISTRHO_PLUGIN_HAS_UI 0"; } > "$meta/DistrhoPluginInfo.h"
    local srcs=("$dpf/distrho/DistrhoPluginMain.cpp")
    for f in "$@"; do srcs+=("$dir/$f"); done
    local bundle="$out/lv2/$name.lv2"
    mkdir -p "$bundle"
    g++ $CXXFLAGS -I"$meta" -I"$dpf/distrho" -I"$dir" -I"$dir/sources" -I"$dir/gen" \
        -I"$dir/thirdparty/blink" -I"$dir/thirdparty/vco" -I"$dir/thirdparty/pl_list" "${srcs[@]}" -shared -o "$bundle/$name.so" -lpthread
    ( cd "$bundle" && "$out/obj/lv2_ttl_generator" "./$name.so" >/dev/null )
}

gcc -O2 -o "$out/obj/lv2_ttl_generator" "$dpf/utils/lv2-ttl-generator/lv2_ttl_generator.c" -ldl
build_dpf string-machine-chorus "$sm/plugins/string-machine-chorus" \
    ChorusPlugin.cpp ChorusShared.cpp sources/SolinaChorus.cpp sources/bbd/bbd_line.cpp \
    sources/bbd/bbd_filter.cpp sources/dsp/Delay3Phase.cpp gen/dsp/Delay3PhaseDigital.cpp \
    gen/dsp/LFO3PhaseDual.cpp
build_dpf string-machine "$sm/plugins/string-machine" \
    StringMachinePlugin.cpp StringMachineShared.cpp StringMachinePresets.cpp sources/StringOsc.cpp \
    sources/StringSynth.cpp sources/StringFilters.cpp sources/SolinaChorus.cpp \
    sources/SolinaChorusStereo.cpp sources/MidiDefs.cpp sources/bbd/bbd_line.cpp \
    sources/bbd/bbd_filter.cpp sources/dsp/AHDSREnvelope.cpp sources/dsp/Delay3Phase.cpp \
    sources/dsp/Delay3PhaseStereo.cpp sources/dsp/OnePoleFilter.cpp gen/dsp/Delay3PhaseDigital.cpp \
    gen/dsp/Delay3PhaseDigitalStereo.cpp gen/dsp/LFO3PhaseDual.cpp gen/dsp/StringFiltersHighshelf.cpp \
    gen/dsp/NoiseLFO.cpp gen/dsp/PwmOscillator.cpp gen/dsp/AsymWaveshaper.cpp \
    thirdparty/vco/OscillatorBlepRect.cpp

# --- GxSlowGear (GPL-2.0+), brummer10's Slow Gear, at 2086656 (2024-10-01): a
# volume swell that re-opens on each pick. DSP only, with g++ (its Makefile
# also builds an X11 UI).
gx="$out/src/GxSlowGear.lv2"
if [ ! -d "$gx" ]; then
    git clone -q https://github.com/brummer10/GxSlowGear.lv2 "$gx"
fi
git -C "$gx" checkout -q 2086656
bundle="$out/lv2/gx_slowgear.lv2"
mkdir -p "$bundle"
g++ -std=gnu++11 -O2 -fPIC -shared -I"$gx" -I"$gx/dsp" -I"$gx/plugin" "$gx/plugin/gx_slowgear.cpp" \
    -o "$bundle/gx_slowgear.so" -lm
cp "$gx/plugin/manifest.ttl" "$gx/plugin/gx_slowgear.ttl" "$bundle/"

# --- GuitarMidi-LV2 v3.2 (LGPL-2.1+): the author's own package for Ubuntu 24.04.
# Building it means building TensorFlow Lite from its submodule; the package
# carries the same plugin and its model.
gm="$out/src/guitarmidi_3.2_ubuntu2404_amd64.deb"
if [ ! -f "$gm" ]; then
    curl -sSL -o "$gm" https://github.com/geraldmwangi/GuitarMidi-LV2/releases/download/v3.2/guitarmidi-lv2_3.2_ubuntu2404_amd64.deb
fi
rm -rf "$out/obj/gm" && mkdir -p "$out/obj/gm"
dpkg-deb -x "$gm" "$out/obj/gm"
rm -rf "$out/lv2/guitarmidi.lv2"
cp -r "$out/obj/gm/usr/lib/lv2/guitarmidi.lv2" "$out/lv2/"

# --- the spike's own two plugins: a mono tracker and a MIDI-to-audio probe
for name in pitch2midi midiprobe pacer; do
    bundle="$out/lv2/spike-$name.lv2"
    mkdir -p "$bundle"
    gcc -O2 -Wall -fPIC -shared -o "$bundle/$name.so" "$here/$name.c" -lm
    cp "$here/$name.ttl" "$bundle/$name.ttl"
    cat > "$bundle/manifest.ttl" <<TTL
@prefix lv2: <http://lv2plug.in/ns/lv2core#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
<urn:sushi-rig:spike:$name> a lv2:Plugin ; lv2:binary <$name.so> ; rdfs:seeAlso <$name.ttl> .
TTL
done
echo "built into $out/lv2: $(ls "$out/lv2" | tr '\n' ' ')"
