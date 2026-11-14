# Recording

The rig can record what you play, straight from the page. A **take** is one
recording. It is saved as a folder holding a sound file for each channel, the
mix you heard, and a copy of the rig's files.

The page calls a take's sound files **stems**.

Recording is on the **stage** screen, and works while the page is **Locked**.

<img src="img/recording.png" alt="The stage screen while recording: the Record bar turned red, reading Recording with the time 0:47, the take name riff-idea, the five stems it is writing, and the Stop button" width="420">

## Record a take

1. Start a rig from the page, if one is not playing
   ([Getting started](getting-started.md)).
2. Open the stage screen: on the laptop, click **Stage** at the top of the
   rack.
3. Optionally type a name under **Take name**, such as `riff-idea`. Names are
   lowercase letters, digits, `-` and `_`. With no name, the take is named from
   the date and time it starts, like `take-20261106-203817`.
4. Look at the line under the name box. It lists the stems this take will
   write, for example
   `5 stems: board.wav, board-dry.wav, vocal.wav, vocal-dry.wav, master.wav`.
5. Tap **Record**. The bar turns red and reads **Recording**, with the take's
   name and a running clock.
6. Play.
7. Tap **Stop**. There is no second question, because stopping loses nothing.

A line headed **Recorded** and the take's name gives the verdict. If all is
well it reads like `0:47 · Whole · 5 stems · nothing lost`: the length, that
the take is whole, how many stems, and that no sound was dropped. If not, it
gives the number of problems and the first of them.

## Find your takes

Takes go in a folder called `sushi-rig-takes` in your home folder on the rig's
laptop, one folder for each take. The rack lists them on the right, under
**TAKES**, newest first, each with its length and its verdict.

The page cannot play a take or open its folder. Use a file manager, or your
recording software.

## What is in a take's folder

For a rig with a channel called `guitar` and one called `vocal`, each on its
own input:

| File | What it is |
|---|---|
| `guitar.wav`, `vocal.wav` | Each channel by itself, after its effects, **before its volume fader** |
| `guitar-dry.wav`, `vocal-dry.wav` | Each input exactly as it came into the interface: no amp, no effects |
| `master.wav` | The mix as it went to the monitors, with the channel volumes, the master's effects and the master volume |
| `take.yaml` | Notes about the take: what was recorded and from where, whether it is complete, anything that was lost, and what is known about how the stems line up |
| `config.json`, `rig.yaml` | The rig's files as they stood when you pressed Record |

The stems are named after your channels, so a rig whose one channel is called
`board` gives `board.wav`, `board-dry.wav` and `master.wav`.

**Two channels on the same input share one dry file**, named after the input
instead of a channel. A rig with channels `clean` and `dirty` both listening to
`guitar-in` gives `clean.wav`, `dirty.wav`, one `guitar-in-dry.wav`, and
`master.wav`. It is one guitar, so it is recorded dry once.

Things worth knowing about these files:

- **The channel stems are deliberately without the channel's volume.** Whatever
  the **VOLUME** faders were at, each channel is recorded at the level its
  effects left it, so you can rebalance later. If you want the balance you
  heard, that is `master.wav`.
- **The dry stems are for re-amping**: playing the bare guitar back through a
  different sound later.
- **The stems do not line up exactly, and a take from the page does not yet
  measure by how much.** The dry stems run slightly ahead of the others, by
  about five thousandths of a second. Every stem is written exactly as it was
  captured, and `take.yaml` says in words that the offset was not measured and
  the stems may need lining up by ear.
- **The take's copy of the rig does not include knobs you have turned and not
  saved.** `rig.yaml` is the rig's file as it stood when you pressed Record, and
  `config.json` is what the rig was loaded from when it was started or last
  switched, with the amp as the page last set it. If you want a take to carry the exact sound you played, save before
  you record ([Saving and going back](saving.md)).

## Names

- Recording under a name you have already used does not overwrite the earlier
  take. The earlier one is moved into a folder called `_archive` inside the
  takes folder, and the bar warns you before you start:
  **Will replace the earlier …, which moves to _archive/…**.
- A name the rules do not allow is refused before the take starts, with the
  rule shown under the box.

## Things that may surprise you

- **Stopping the rig, switching rigs or changing the amp's model while
  recording finishes the take first** (not yet tried on the rig). The take is
  kept, up to that moment.
- **Recording needs a rig started the way the page starts one.** A rig started
  from a terminal in an older way cannot be recorded here. If the bar reads
  **Recording is off**, the line under it says why.
- There are no level meters, and the page does not show how much disk space is
  left.
- Takes have been recorded from the page on the rig with a one-channel rig and
  with a two-channel one, guitar and vocal, whose `master.wav` was listened
  back.

## If something goes wrong

- **The bar reads "Recording is off".** Read the reason under it. Most often no
  rig is running.
- **A line headed "Not recording".** The take did not start; the reason
  follows.
- **The verdict names a problem**, or a take in the **TAKES** list has a
  warning. For the full story, run this in a terminal, with the take's name:

  ```bash
  sushi-rig take check ~/sushi-rig-takes/riff-idea
  ```

- **The rig crashed or was killed mid-take.** The take is cut off but not
  lost. This makes its stems readable:

  ```bash
  sushi-rig take repair ~/sushi-rig-takes/riff-idea
  ```

The detail of how takes are stored and checked is in
[Takes](../reference/takes.md).
