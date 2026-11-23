# A MIDI rig: the guitar as a MIDI instrument

What a rig would look like in which the guitar plays synths through a MIDI
pickup, beside or instead of its own sound: what is known, what is not, and
the order to find out. Written 2026-11-23. Nothing here is built yet.

## In short

- **The pickup:** a Fishman TriplePlay Express (USB-C). It should appear on
  Linux as a plain USB MIDI device, as the FBV did, but nobody has said so in
  print: check it on the day it arrives.
- **The rig:** a **MIDI channel** is a channel whose first plugin is a synth,
  played by the pickup's notes. After the synth it is an ordinary channel:
  effects from the library, a volume, a send into the master and its limiter,
  presets, save, the pedal. A rig can hold several MIDI channels and several
  guitar channels side by side, as it holds channels now.
- **First sounds:** pads, and a plucked double bass.
- **Polyphony and bends:** chords are needed; per-string bends would be nice.
- **First step:** a short spike in Sushi on the laptop, played from the FBV or
  any USB keyboard. It doesn't need the pickup, so the rig side is proven
  before the pickup arrives.

## The pickup: Fishman TriplePlay Express

**What is known** (from Fishman's documents and retailers):

- **Power and connection:** a hexaphonic pickup that mounts beside the
  guitar's own, with no drilling. It is powered and read over one USB-C cable.
  The guitar's own pickups and jack keep working.
- **Default MIDI mode:** by default it "will appear as a MIDI device
  transmitting all strings on MIDI Channel 1": **Poly mode**, bend range ±2,
  "Trigger" bend mode.
- **Per-string mode:** in **Mono mode** it sends each string on its own
  channel (six channels). Per-string bends need this, so a bend on one string
  doesn't bend the others.
- **Presets:** the Express has presets pairing these settings, for example
  001 Poly ±2 Trigger, 002 Poly ±12 Auto, 004 Mono ±12 Auto. Fishman suggests
  ±12 on both the pickup and the synth where the synth allows it.
- **Setup apps:** Fishman's utility and host apps are for Windows, macOS and
  iOS. The laptop's Windows boot can run them, as it did FBV Control.

**What is not known:**

- **Linux class compliance.** Fishman's support page says the pickups work
  with "a computer, MIDI interface, or keyboard" over USB, and a retailer
  listing says "any computer … that accepts USB MIDI". One Linux user in a
  retailer's Q&A says it works with any DAW that takes MIDI, but that its
  utility may not. The older TriplePlay's USB receiver was reported as class
  compliant on Linux in 2018. That is good indirect evidence, not a statement.
- **Its port names on Linux.**
- **Where settings live.** Whether settings made in Fishman's app are stored
  in the pickup, as FBV Control's were in the board, or only in the app.
- **What a bend and a release send in each mode,** and whether it sends
  anything other than notes and pitch bend (aftertouch, CCs).

**Check it the day it arrives,** as the FBV was checked:

1. Run `lsusb` and `aseqdump -l`. It should appear as a client with at least
   one port; note the names.
2. In the default Poly mode, run `aseqdump -p <its port>`. Play each open
   string, a chord, a bend, and a muted string. Note the channel, the notes,
   the velocities and what a bend sends.
3. Switch to Mono mode (for example preset 004) in Fishman's app, then unplug
   and plug back in. Does the mode survive? Repeat step 2: each string should
   now be on its own channel.
4. Measure pluck-to-note latency on each string, low E especially.

**Buy it somewhere with a return policy,** in case step 1 fails.

## Synths in Sushi: what is already known

Measured in Sushi 1.3.0 for the synth research (#166, PR #175,
`tool/spikes/synth_research/README.md`):

- **Sushi plays LV2 synths from MIDI:**

  | Synth | Result |
  |---|---|
  | mda JX10 (poly, already in the plugin manifest) | plays |
  | mda DX10 (FM) | plays |
  | string-machine (built for the string-machine rig) | plays |
  | Calf Monosynth | plays |
  | Sushi's own brickworks simple_synth | plays |
  | DISTRHO Nekobi | silent; not investigated |

  - **Cost:** string-machine is the heaviest, about 20 % of a core holding
    chords.
  - **Calf Monosynth** implements `state:interface`, so `sushi-rig probe`
    counts it as not drivable from the page.
- **MIDI does not cross tracks inside Sushi.** Each MIDI channel must get its
  notes from Sushi's MIDI input, configured, not from another track.
- **A plugin's MIDI output reaches only the next plugin on the same track.**
  An LV2 plugin that isn't a MIDI effect passes nothing on. So any MIDI effect
  (transposer, arpeggiator, chord) must sit directly in front of the synth.
  Sushi's own transposer and arpeggiator pass MIDI on.
- **Timing within a 64-frame chunk is lost,** at most 1.3 ms.
- **A synth has no audio input.** A MIDI channel has no guitar sound in it.
  Dry guitar is a separate channel, which is how you want it anyway.

## The rig: what would be built

The architecture extends the rig's existing parts:

1. **The machine file** describes the pickup as a MIDI device with a new type,
   `instrument`, beside the FBV's `pedal`. It records the device's port and
   its mode (poly, or mono with each string on its own channel).
2. **The rig file:**
   - A channel can have `input: midi` and name its device. Its chain must
     start with an instrument, and the rig refuses one that doesn't.
   - It can also name which MIDI channels it hears. That makes a **string
     split** possible in Mono mode: the low two strings play a bass channel
     and the top four a pad.
3. **The compiler:**
   - Gives Sushi one MIDI input port per device: the FBV on one, the pickup
     on another.
   - Connects the pickup's port, filtered by channel where asked, to each
     MIDI channel's track.
   - Keeps the pedal's mappings on the pedal's own port, so the pickup can
     never trigger them by accident (it may send CCs too).
4. **The patchbay** links the pickup to its Sushi input, as it links the
   pedal.
5. **The page** shows a MIDI channel like any other: the synth's card first,
   then its effects. The synth's card needs a **profile**: a synth has
   hundreds of controls, and a profile picks the ones worth a knob. That is
   how the synth gets **programmed**, on the page, and the settings save with
   the rig and as channel presets like any other plugin's.
6. **The pedal** works unchanged:
   - C, D, the pedal, the wah and the toe can be assigned to a MIDI channel's
     volume, its effects' on/off, or the synth's own controls (a pad's filter
     on the wah, say).
   - An optional extra: pass the pedal through to the synth, so its own
     modulation (mod wheel, expression) responds.
7. **Rig switching** with A and B works across MIDI and guitar rigs.

## Sounds to start with

**Pads:**

- **string-machine** is already in the plugin manifest, measured, and built
  for the string-machine rig.
- **mda JX10** is light and already in the manifest.
- **Surge XT** is the strongest free candidate, with very good pads, MPE and
  per-string bends.
  - Check it plays in Sushi.
  - Check how many of its controls the probe can drive. A JUCE-built LV2
    synth may implement `state:interface`, which the rig treats as not
    drivable from the page.

**Plucked double bass:**

- **sfizz**, an LV2 SFZ sample player, with a free upright-bass SFZ library.
  It is the most realistic, because it is a real recorded bass. Its
  "programming" is choosing samples and envelopes, and the library decides
  how it sounds.
- **Surge XT's string oscillator** (Karplus-Strong plucked string) is
  synthetic but fully programmable.
- **mda DX10 or an FM synth** gives an FM bass, a different character.
- A bass layer usually wants the notes an octave down. Sushi's transposer in
  front of the synth does that, or the synth's own octave setting.

**Install routes:**

- string-machine and mda are already handled.
- Surge XT and sfizz publish Linux builds with LV2. Whether Ubuntu's own
  packages carry the LV2 versions is to check.

## Chords and bends

| Mode | Chords | Bends |
|---|---|---|
| Poly (default) | play | bend every note together. Fine for pads, odd for chords |
| Mono, per string | play | per string |

- **The synth must take notes on several channels for Mono mode** and treat
  each channel's bend on its own. That is MPE, or "multi-channel".
  - **Surge XT:** yes, per its documentation.
  - **mda JX10 and string-machine:** to check. Many synths listen on one
    channel only.
- **Bend range:** set it to ±12 on both ends where the synth allows it, ±2
  where it doesn't.
- **Recommendation:** start in Poly mode for pads and bass, and treat
  per-string bends as a later step, with Surge XT.

## Latency

**From the spike (#166):** pluck to synth note was about 31–60 ms, using
pitch trackers running on the guitar's audio.

**A hex pickup should be much faster:**

- **Expected:** 10–20 ms on the low E for good hex systems, and less on the
  high strings. Premier Guitar puts the TriplePlay level with Roland's
  current systems, or slightly faster.
- **Added by the rig:** its own buffer (about 5 ms) and the synth's attack.
- **Masking:** layering the synth under dry guitar hides most of it, as
  with the octave.

This is unmeasured on this rig until the pickup arrives.

## The plan

1. **Spike: MIDI channels in Sushi** (no pickup needed). Use the FBV, or a
   USB keyboard, as the MIDI source:
   - an LV2 synth on a track fed from a MIDI input port;
   - two synth tracks layered from one port;
   - a channel split (two tracks, each hearing different MIDI channels);
   - the transposer in front of a bass synth;
   - the FBV on a second MIDI port driving a synth control and a MIDI
     channel's volume, its CCs reaching no synth;
   - Surge XT and sfizz: do they load and play, and what can the probe
     drive?

   Measure CPU and keyboard-to-sound latency.
2. **The pickup's day-one check,** above.
3. **Build:**
   - the `instrument` device;
   - `input: midi` channels with channel filters;
   - the compile;
   - the patchbay;
   - the page's MIDI channel;
   - profiles for the first synths;
   - a first MIDI rig: a pad plus a plucked bass, with a dry guitar channel.
4. **Later:** per-string bends with an MPE synth, more sounds, MIDI effects.

## Open questions

- Which synth, if any, gives good pads and a convincing plucked bass and is
  fully drivable from the page? This decides how much "programming on the
  page" is possible.
- Does a synth need its own polyphony limit, so CPU stays bounded on big
  chords? string-machine reached 20 % of a core.
- How should a MIDI rig behave when its pickup isn't plugged in? Presumably
  like a pedal the rig needs: "waiting", not a crash.

## Sources

- [Fishman: TriplePlay Express Quick Start Guide](https://fishman.com/wp-content/uploads/2025/01/TriplePlay-Express-Quick-Start-Guide.pdf)
- [Fishman: TriplePlay support](https://fishman.com/tripleplay-support/)
- [Fishman: TriplePlay User Guide](https://files.kraftmusic.com/media/ownersmanual/Fishman_TriplePlay_User_Guide.pdf)
- [Sweetwater: TriplePlay Express reviews and Q&A](https://www.sweetwater.com/store/detail/TriplePlayExp--fishman-tripleplay-express-usb-c-midi-guitar-pickup/reviews?stars=2)
- [Rekkerd: Fishman TriplePlay Express](https://rekkerd.org/?p=272201)
- [arch-proaudio list: MIDI guitar (2018)](https://lists.archlinux.org/pipermail/arch-proaudio/2018-April/000126.html)
- [Blokas community: TriplePlay Connect over USB](https://community.blokas.io/t/fishman-tripleplay-connect-midi-usb/3459)
- [Premier Guitar: Decoding modern MIDI guitar](https://www.premierguitar.com/gear/decoding-modern-midi-guitar)
- [Jam Origin: MIDI Guitar downloads (no Linux)](https://docs-jam-live.pages.dev/downloads)
