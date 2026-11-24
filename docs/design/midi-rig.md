# A MIDI rig: the guitar as a MIDI instrument

This page covers a rig in which the guitar plays synths through a MIDI pickup,
beside or instead of its own sound: what is known, what is not, and the order
to find out. Written 2026-11-23. Nothing here is built yet.

Terms used here:
- A **synth channel** is a rig channel that plays a synth.
- **MIDI channels** are MIDI's own 1 to 16, counted 0 to 15 in the machine
  file and by `aseqdump`.

## In short

- **The pickup:** a Fishman TriplePlay Express (USB-C). It should appear on
  Linux as a plain USB MIDI device, as the FBV did, but nobody says so in
  print, so check it the day it arrives.
- **Synth channels:** a synth channel's first sound-making plugin is a synth,
  played by the pickup's notes. Only MIDI effects can sit before it. After the
  synth it is an ordinary channel: effects from the library, a volume, a send
  into the master and its limiter, loading onto other rigs, save, the pedal.
- **Mixing:** a rig can hold several synth channels and several guitar
  channels side by side, as it holds channels now.
- **First sounds:** pads, and a plucked double bass.
- **Playing:** chords are needed; per-string bends would be nice.
- **First step:** a short spike in Sushi on the laptop, played from a USB
  keyboard or the MPD218's pads, with the FBV on a second port for the pedal.
  It doesn't need the pickup, so the rig side is proven before the pickup
  arrives.
- **One known blocker:** today's plugin probe marks every synth not drivable
  from the page (see [below](#synths-in-sushi-what-is-already-known)), so
  programming a synth on the page needs a change to the probe first.

## The pickup: Fishman TriplePlay Express

**What Fishman's documents and retailers say:**

- **Fitting and power:** a hexaphonic pickup that mounts beside the guitar's
  own, with no drilling. It is powered and read over one USB-C cable, and the
  guitar's own pickups and jack keep working.
- **Default mode:** by default it "will appear as a MIDI device transmitting
  all strings on MIDI Channel 1" (channel 0 in the machine file and to
  `aseqdump`). That is **Poly mode**, with a bend range of ±2 and "Trigger"
  bend mode.
- **Mono mode:** in Mono mode it sends each string on its own MIDI channel,
  six in all. Per-string bends need this, so a bend on one string doesn't bend
  the others.
- **Presets:** the Express has presets that pair these settings, for example
  001 Poly ±2 Trigger, 002 Poly ±12 Auto, 004 Mono ±12 Auto. Fishman suggests
  ±12 on both the pickup and the synth where the synth allows it.
- **Setup apps:** Fishman's utility and host apps are for Windows, macOS and
  iOS. The laptop's Windows boot can run them, as it ran FBV Control.

**What is not known:**

- **Linux class compliance.** The evidence is good but indirect:
  - Fishman's support page says the pickups work with "a computer, MIDI
    interface, or keyboard" over USB.
  - A retailer listing says "any computer … that accepts USB MIDI".
  - One Linux user, in a retailer's Q&A, says it works with any DAW that
    takes MIDI, but that its utility may not.
  - The older TriplePlay's USB receiver was reported as class compliant on
    Linux in 2018.
- **Its port names on Linux.**
- **Where settings are kept.** Whether settings made in Fishman's app are
  stored in the pickup, as FBV Control's were in the board, or only in the
  app.
- **What it sends.** What a bend and a release send in each mode, and whether
  it sends anything besides notes and pitch bend, such as aftertouch or CCs.

**Check it the day it arrives,** as the FBV was checked:

1. Run `lsusb` and `aseqdump -l`. It should appear as a client with at least
   one port; note the names.
2. In the default Poly mode, run `aseqdump -p <its port>`. Play each open
   string, a chord, a bend and a muted string. Note the channel, the notes,
   the velocities and what a bend sends.
3. Switch to Mono mode in Fishman's app (for example, preset 004), then
   unplug and replug. Did the mode survive? Repeat step 2: each string should
   now be on its own MIDI channel.
4. Measure pluck-to-note latency on each string, especially the low E.

**Buy it somewhere with a return policy,** in case step 1 fails.

### If it needs setting up from Linux: our own control app

Fishman's setup app runs on Windows, macOS and iOS only. Whether we would ever
need our own depends on whether the settings are kept in the pickup, and on
how the app talks to it. In order:

1. **Do the settings survive an unplug?** Set Mono mode (or a preset) once on
   Windows, then unplug, replug on Linux and check with `aseqdump`. If they
   survive, as FBV Control's did in the FBV, an app is only needed to change
   settings from the rig mid-set, which may never matter.
2. **Does Fishman's app run under Wine?** If it talks plain MIDI, Wine may
   pass that through, and the official app may just work on the laptop. This
   is a guess until tried.
3. **How does it talk?** `lsusb -v` shows the device's interfaces. The guesses
   below are from how such devices usually work, before anyone has seen this
   one's `lsusb -v`:

   | What `lsusb -v` shows | How it would be done | Effort |
   |---|---|---|
   | Only a MIDI interface, so the app uses System Exclusive messages over USB MIDI | Decode the messages, then send them from Linux | Very doable; perhaps a day or two to decode and a day to build |
   | A separate vendor-specific interface | Wireshark captures and `pyusb` | Doable, but slower and riskier |
   | Encrypted or signed messages | None | Not practical: keep setting it up on Windows |
4. **Capture it, listen-only.** On Windows, record what the app sends while
   changing one setting at a time: Mono to Poly, the bend range, one string's
   sensitivity. Use a MIDI monitor, or USBPcap with Wireshark. Work out which
   bytes mean what, and look for a checksum.
5. **Only then send.** Replay a known message from Linux with `amidi -S` and
   check the change in Fishman's app on Windows. Then wrap the settings in a
   small command (for example `sushi-rig pickup mode mono`) and a few controls
   on the page.

**Limits:**

- **Never the firmware.** Updates stay with Fishman's own tools.
- **Nothing is sent to the pickup** until steps 1 to 4 say what it means. As
  with the FBV, everything starts listen-only.
- **Fishman's licence.** In the UK and EU, working out a protocol so your own
  software can work with a device you own is generally allowed. That isn't
  legal advice, and the app's licence is worth a glance first.

## Synths in Sushi: what is already known

The synth research (#166, PR #175) measured these in Sushi 1.3.0; the details
are in [its README](../../tool/spikes/synth_research/README.md). Its synth
runs were offline, in a container, on a synthetic guitar. A note reached each
synth from Sushi's config or from a tracker plugin on the same track. **Sushi's
MIDI input was never used, and nothing was heard.**

- **Synths that played** in that sense:

  | Synth | Plays (offline) |
  |---|---|
  | mda JX10 (poly, in the plugin manifest) | yes |
  | mda DX10 (FM, in the plugin manifest) | yes |
  | jpcima's string-machine synth | yes |
  | Calf Monosynth | yes |
  | Sushi's own brickworks simple_synth | yes |
  | DISTRHO Nekobi | no, silent (not chased) |

  The rig has only the string-machine's **chorus**, built by
  `scripts/build-string-machine-chorus.sh` for the string-machine rig. The
  synth itself is not in the manifest and not built; it would need a build
  and its manifest and ports lines.
- **CPU:** string-machine was the heaviest, up to about 16 % of a core holding
  chords, or 20 % with GuitarMidi-LV2 in front of it. That was offline in the
  spike's container, not on the rig.
- **The probe marks every synth not drivable.** `probe.is_config_drivable`
  fails a plugin for any atom port it needs connected, and a synth's MIDI input
  is one. The probe, run on the synths installed in the dev container, says
  mda JX10 and DX10 are not drivable for that reason. Calf Monosynth also fails
  on `state:interface`. `profile.py` refuses profile controls on a plugin that
  isn't drivable, so **no synth can have a profile today**. The rule needs an
  exception for an input that only carries MIDI before any synth can be
  programmed on the page. A synth that also implements `state:interface` or
  `patch:writable` would still fail.
- **MIDI does not cross tracks inside Sushi.** Each synth channel must get its
  notes from Sushi's MIDI input, through the config, not from another track.
- **A plugin's MIDI output reaches only the next plugin on the same track.**
  An LV2 plugin that isn't a MIDI effect passes nothing on, so a MIDI effect
  must sit directly in front of the synth. Sushi's internal plugins pass
  keyboard events on (`InternalPlugin::process_event`; measured through
  `mono_summing` and brickworks chorus). Its transposer and arpeggiator were
  not tested.
- **Timing within a 64-frame chunk is lost** for MIDI a plugin sends: at most
  1.3 ms. MIDI arriving from Sushi's input takes another path, which was not
  measured.
- **A synth has no audio input,** so a synth channel has no guitar sound in
  it. Dry guitar is a separate channel, which is how it's wanted anyway.

## The rig: what would be built

The architecture extends the rig's existing parts:

1. **The machine file** describes the pickup as a MIDI device with a new type,
   `instrument`, beside the FBV's `pedal`. It records the device's port and its
   mode: Poly, or Mono with a MIDI channel per string.
2. **The rig file:**
   - A channel can have `input: midi` and name its device.
   - Its chain must start with a synth, with only MIDI effects before it. The
     rig refuses a chain that doesn't.
   - A channel can also name which MIDI channels it hears. That allows a
     **string split** in Mono mode: the low two strings play a bass channel,
     the top four a pad.
3. **The compiler:**
   - Gives Sushi one MIDI input port per device: the FBV on one, the pickup on
     another.
   - Connects the pickup's port, filtered by MIDI channel where asked, to each
     synth channel's track.
   - Keeps the pedal's mappings on the pedal's own port, so the pickup can
     never trigger them by accident (it may send CCs too).
4. **The patchbay** links the pickup to its Sushi input, as it is meant to
   link the pedal. That link is unverified for the pedal too: see
   [MIDI, Not yet verified](../reference/midi.md#not-yet-verified-on-the-rig).
5. **The probe** gets an exception for a synth's MIDI input (above), so a
   synth can be drivable.
6. **The page** shows a synth channel like any other: the synth's card first,
   then its effects. The synth's card needs a **profile**, because a synth has
   hundreds of controls and a profile picks the ones worth a knob. That is how
   the synth gets **programmed**, on the page. Its settings save with the rig,
   and can be loaded onto another rig's channel, like any other plugin's.
7. **The pedal works as the device model allows**
   ([MIDI, the device model](../reference/midi.md#the-device-model-191)):
   - C, D and the toe can switch a synth channel, or one of its effects, on
     and off.
   - The pedal and the wah can take its volume, or a synth control: a pad's
     filter on the wah, say.
   - An optional extra: pass the pedal through to the synth, so the synth's
     own modulation (mod wheel, expression) responds.
8. **Rig switching** with A and B should work across synth rigs and guitar
   rigs. Switching itself is not yet heard from a real pedal.

## Sounds to start with

None of these has been heard on the rig.

**Pads:**

- **The string-machine synth.** It played in #166's spike, but needs building
  and adding to the manifest.
- **mda JX10.** Light, and already in the manifest.
- **Surge XT.** The strongest free candidate: very good pads, and MPE and
  per-string bends according to its documentation. Check that it plays in
  Sushi, how many of its controls the probe can drive once the MIDI-input
  exception exists, and whether it implements `state:interface`, which a
  JUCE-built LV2 synth may.

**Plucked double bass:**

- **sfizz**, an LV2 SFZ sample player, with an upright-bass SFZ library. It
  would be the most realistic, because it is a real recorded bass, but no
  library has been chosen or heard. Caution: sfizz loads its `.sfz` file
  through `patch:`, which Sushi can't set from a config. That's to confirm
  with `sushi-rig probe`, but if so it may load and play nothing useful. The
  probe's docstring describes exactly that trap.
- **Surge XT's string oscillator** (Karplus-Strong plucked string). Synthetic,
  but fully programmable, if Surge is drivable.
- **mda DX10, or another FM synth,** for an FM bass with a different
  character.
- A bass layer usually wants the notes an octave down. Sushi's transposer in
  front of the synth (untested) or the synth's own octave setting would do
  that.

**Getting them:** the mda synths are already handled. According to their
download pages (not checked), Surge XT and sfizz publish Linux builds with LV2.
Whether Ubuntu's own packages carry the LV2 versions is also to check.

## Chords and bends

| Mode | Chords | Bends |
|---|---|---|
| Poly (the default) | play | bend every note together: fine for pads, odd for chords |
| Mono, per string | play | per string |

- **A synth must take notes on several MIDI channels for Mono mode,** and
  treat each channel's bend on its own. That is MPE, or "multi-channel".
  - Surge XT does, according to its documentation. MPE through Sushi, with
    all MIDI channels into one track, is untested.
  - mda JX10 and string-machine are to check; many synths listen on one MIDI
    channel only.
- **Bend range:** ±12 on both ends where the synth allows it, ±2 where it
  doesn't.
- **Recommendation:** start in Poly mode for pads and bass, and treat
  per-string bends as a later step, with Surge XT.

## Latency

**From #166's spike** (offline, synthetic guitar, pitch trackers on the
guitar's audio, not a hex pickup), pluck to synth note was:
- about 50–60 ms with GuitarMidi-LV2;
- 157 ms on the low E;
- 31–43 ms with the spike's own monophonic test tracker, which is not a
  candidate.

**A hex pickup should be faster,** but nothing here has measured one:
- **The pickup itself:** the figure often quoted for good hex systems is
  10–20 ms on the low E, and less on the high strings. That's an estimate, not
  a measurement. Premier Guitar puts the TriplePlay level with Roland's
  current systems, or slightly faster.
- **The rig's own buffer** (about 5 ms) and **the synth's attack** add to
  that.
- **Layering under dry guitar** should hide much of it, as the dry channel
  does for the octave. That's expected, not yet heard.

## The plan

1. **Spike: synth channels in Sushi** (no pickup needed). Play a USB keyboard
   or the MPD218's pads into one MIDI input port, with the FBV on a second
   port. Check:
   - an LV2 synth on a track fed from a MIDI input port;
   - two synth tracks layered from one port;
   - a split, with two tracks each hearing different MIDI channels;
   - Sushi's transposer in front of a bass synth;
   - the FBV on the second port driving a synth control and a synth channel's
     volume, with its CCs reaching no synth;
   - whether Surge XT and sfizz load and play, and what the probe says of
     them;
   - which name PipeWire gives Sushi's MIDI inputs, and whether Sushi counts
     MIDI channels from 0.

   Measure CPU and the latency from keyboard to sound.
2. **The pickup's day-one check,** above. If its settings are needed from
   Linux, add the control-app steps after it.
3. **Build:**
   - the probe's exception for a synth's MIDI input;
   - the `instrument` device;
   - `input: midi` channels with MIDI-channel filters;
   - the compile;
   - the patchbay;
   - the page's synth channel;
   - profiles for the first synths;
   - a first synth rig: a pad and a plucked bass, with a dry guitar channel.
4. **Later:** per-string bends with an MPE synth, more sounds, MIDI effects.

## Open questions

- **Which synth?** Which one, if any, gives good pads and a convincing plucked
  bass and is fully drivable from the page once the probe takes a MIDI input?
  This decides how much programming on the page is possible.
- **Polyphony limits.** Does a synth need its own polyphony limit, so CPU stays
  bounded on big chords?
- **A missing pickup.** What should a synth rig do when its pickup isn't
  plugged in? A pedal the running rig needs shows its port as `waiting` and the
  agent's MIDI as `failed`, so the rig shows a fault but plays on. The pickup
  would most likely do the same.

## Sources

- [Fishman: TriplePlay Express Quick Start Guide](https://fishman.com/wp-content/uploads/2025/01/TriplePlay-Express-Quick-Start-Guide.pdf)
- [Fishman: TriplePlay support](https://fishman.com/tripleplay-support/)
- [Fishman: TriplePlay User Guide](https://files.kraftmusic.com/media/ownersmanual/Fishman_TriplePlay_User_Guide.pdf)
- [Sweetwater: TriplePlay Express reviews and Q&A](https://www.sweetwater.com/store/detail/TriplePlayExp--fishman-tripleplay-express-usb-c-midi-guitar-pickup/reviews)
- [Rekkerd: Fishman TriplePlay Express](https://rekkerd.org/?p=272201)
- [arch-proaudio list: MIDI guitar (2018)](https://lists.archlinux.org/pipermail/arch-proaudio/2018-April/000126.html)
- [Blokas community: TriplePlay Connect over USB](https://community.blokas.io/t/fishman-tripleplay-connect-midi-usb/3459)
- [Premier Guitar: Decoding modern MIDI guitar](https://www.premierguitar.com/gear/decoding-modern-midi-guitar)
