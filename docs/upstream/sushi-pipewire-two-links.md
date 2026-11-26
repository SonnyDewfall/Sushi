# Draft: report to Elk on Sushi under PipeWire, an input fed by two links

**Status: draft, not posted.** Written 2026-11-24 for #143, from the rig's
measurement of 31 Oct 2026 (#81, #106), and checked against Sushi's source on
2026-11-26. For
[elk-audio/sushi's issues](https://github.com/elk-audio/sushi/issues). When it
is posted, put its link in MANIFEST's entry for this draft and in #143.

What was checked before posting, by the session that closed #143 out, on
2026-11-26:

- **The source line.** Read from shallow clones of the `1.3.0` tag (commit
  `08f24e9cd47ce2f95c827db19350593b023cebe9`, the commit the release
  AppImage's `--version` prints) and the `1.4.0` tag (commit
  `c6ddde6b6b6c06229118432c8c7f7a7c61a61408`, dated 2026-10-12). The call
  quoted by the first draft, `jack_port_get_buffer(port, AUDIO_CHUNK_SIZE)`,
  was not the line: line 345 of `src/audio_frontends/jack_frontend.cpp` is
  `jack_port_get_buffer(_input_ports[i], framecount)` plus `start_frame`,
  inside `process_audio(start_frame, framecount, ...)`, which
  `internal_process_callback` calls once per 64-frame chunk with
  `AUDIO_CHUNK_SIZE` as its `framecount` (lines 295 to 300). The effect is
  what the draft said, 64 frames asked for once per chunk; the report now
  quotes the real line and its caller.
- **A newer Sushi.** 1.4.0 is the newest tag. Its `jack_frontend.cpp` differs
  from 1.3.0's in one line, 273, where the chunk-size check reads
  `AUDIO_CHUNK_SIZE` instead of the literal 64 (its HISTORY.md: "Allow
  buffersizes < 64 in JackFrontend"). Lines 295 to 300 and 345 are the same,
  so the fault is still there in 1.4.0 as read. 1.4.0 has not been run.
- **An existing report.** None. elk-audio/sushi's issues (seven in all, one
  open) return nothing for "jack_port_get_buffer", "pipewire", "pw-jack",
  "jack input zeros" or "chunk jack".
- **Not checked:** `pipewire-jack`'s source, for how it mixes two
  connections (the report says so), and JACK2 (likewise).

Everything below the line is the report.

---

## JACK frontend: an input port fed by two connections carries only the first 64 frames of each period (PipeWire; 1.3.0 and 1.4.0)

### Versions

| | |
|---|---|
| Sushi | 1.3.0, the release AppImage (`sushi-linux-x86_64_1_3_0.zip`), extracted. `--version`: `Build options enabled: vst3, lv2, jack, rpc control, ableton link`, `Audio buffer size in frames: 64`, `Git commit: 08f24e9cd47ce2f95c827db19350593b023cebe9` |
| Sushi 1.4.0 | Not run. Read at the `1.4.0` tag (commit `c6ddde6b6b6c06229118432c8c7f7a7c61a61408`, 2026-10-12): the lines below are unchanged, so we expect the same. The one change to `jack_frontend.cpp` since 1.3.0 is line 273's chunk-size check (`AUDIO_CHUNK_SIZE` for the literal 64, "Allow buffersizes < 64 in JackFrontend") |
| Frontend | JACK (`sushi -j -c config.json`), run under `pw-jack` |
| PipeWire | 1.6.2 (compiled and linked against 1.6.2), `pipewire-jack 1.6.2-1ubuntu1.2` |
| OS | Ubuntu 26.04.1 LTS, kernel 7.0.0-42-generic, x86_64 (Intel i7-1260P) |
| Rate, period | 48000 Hz; quantum 256 for the main measurement, also 128 and 64. `pw-dump` showed Sushi's node with `node.force-quantum = 256`, `node.lock-quantum = True` |

### What happens

When one of Sushi's JACK input ports has **two** connections into it, Sushi's
processing receives the correct sum of the two for the first 64 frames of each
period, and exact zeros for the rest of the period. A second input fed by
**one** connection, in the same run, arrives whole. At a period of 64 the
fault is gone.

Nothing in Sushi's log says so at its default level (we did not record xruns
in these runs), and the signal that does arrive is correctly aligned, so at a
period of 256 it reads as the input at a quarter of its level.

### How to reproduce

1. A Sushi config with a mono track with an empty chain, whose input is
   engine channel 0 and whose output is engine channel 0. (We ran it with two
   such tracks, each with an empty chain and a send to a third, master,
   track, using Sushi's internal send and return; we expect one track alone
   to show it too, but have not run that.)
2. Under PipeWire with `pipewire-jack`, force the period:
   `pw-metadata -n settings 0 clock.force-quantum 256` (or start the client
   with `PIPEWIRE_QUANTUM=256/48000`).
3. Start Sushi on the JACK frontend: `pw-jack ./sushi -j -c config.json`.
4. From a second JACK client, play two independent broadband signals (noise
   A and noise B) on two of its output ports, and connect **both** to
   `sushi:audio_input_0`. Connect `sushi:audio_output_0` back to an input of
   that client and record it.
5. Look at the recording frame by frame, aligned to the period: within every
   period, frames 0 to 63 hold A + B, and frames 64 to 255 are exactly 0.0.
6. With one connection into the input, the whole period should arrive: our
   control, in the same runs, was a second Sushi input fed by A alone (our
   `audio_input_0`; the doubly fed one was `audio_input_1`), which arrived
   whole at every period. We did not disconnect B from the doubly fed input
   to see it recover. At a period of 64, with both connections, the whole
   period arrived (measured, below).

These steps are the shape of what we ran, not a script we ran as written. We
measured it with a test client written for our rig (Python, `JACK-Client`
0.5.7), which fed noise A alone to `sushi:audio_input_0` and A and B together
to `sushi:audio_input_1`, each input on a track of its own; it can be shared
if useful.

### What we measured

Three runs at a period of 256, identical to four decimal places. Gains are
read off the impulse response of each recorded output against each source
(1.0 is the source at its own level):

| Sushi input | Connections into it | Gain of A | Gain of B | Expected |
|---|---|---|---|---|
| `audio_input_0` | A | 1.0000 | 0.0029 | A 1.0, B 0.0 |
| `audio_input_1` | A and B | **0.2529** | **0.2521** | A 1.0, B 1.0 |

The 0.25 is not an attenuation: the second input's output has a crest factor
far too high for a quarter of the level (RMS 0.0706 against 0.1412 for A + B,
peak 0.6127 against 0.6264), and frame by frame within each period:

| Frames of the period | Exact zeros | Gain against A + B |
|---|---|---|
| 0 to 63 | 0.0 % | 1.0000 |
| 64 to 127 | 100.0 % | 0 |
| 128 to 191 | 100.0 % | 0 |
| 192 to 255 | 100.0 % | 0 |

The run that survives is always 64 frames, whatever the period:

| Period | One connection | Two connections |
|---|---|---|
| 256 | whole period, gain 1.0000 | first 64 frames, 75.1 % zeros, gain 0.248 |
| 128 | whole period, gain 1.0000 | first 64 frames, 50.2 % zeros, gain 0.499 |
| 64 | whole period, gain 1.0000 | whole period, 0.3 % zeros, gain 0.9998 |

Two controls put it in Sushi rather than in PipeWire:

- Without Sushi, between two plain JACK clients, one, two and three
  connections from constant 0.25 sources into one input port give exactly
  0.25, 0.5 and 0.75, at a period of 256, with no frame missing. Within one
  client, one to four connections into one port sum at unity across the
  whole period too.
- With one connection, Sushi's own input is complete at every period tried.

### Where it seems to come from

`src/audio_frontends/jack_frontend.cpp`, at 1.3.0 and unchanged at 1.4.0.
The process callback cuts the period into 64-frame chunks and calls
`process_audio` once per chunk, passing the chunk's size as its frame count
(`internal_process_callback`, lines 295 to 300):

```cpp
    /* Process in chunks of AUDIO_CHUNK_SIZE */
    for (jack_nframes_t frame = 0; frame < framecount; frame += AUDIO_CHUNK_SIZE)
    {
        Time delta_time = std::chrono::microseconds((frame * 1'000'000) / _int_sample_rate);
        process_audio(frame, AUDIO_CHUNK_SIZE, start_time + delta_time, current_frames + frame - _start_frame);
    }
```

and `process_audio(jack_nframes_t start_frame, jack_nframes_t framecount, ...)`
gets each input port's buffer with that frame count and reads the chunk at
`start_frame` into it (line 345):

```cpp
        float* in_data = static_cast<float*>(jack_port_get_buffer(_input_ports[i], framecount)) + start_frame;
```

So at a period of 256 each input's buffer is asked for four times, each time
for 64 frames, and read at offsets 0, 64, 128 and 192. The JACK API asks for
the callback's frame count: `jack_port_get_buffer`'s `nframes` is the number
of frames passed to the process callback. Our reading, which we have not
checked in `pipewire-jack`'s source: with one connection, `pipewire-jack` can
hand back the connected port's own buffer, and the size asked for does not
matter; with two, it mixes the connections into a buffer of the frames asked
for, 64, and the rest of the period stays zero, which is what the offsets 64
to 255 read. JACK2 may behave differently, which would explain why this has
not been seen there. We have not tried JACK2.

The CV inputs (line 350) and the outputs (line 367) get their buffers the same
way. We saw nothing wrong on an output: the singly fed input came back whole
through one at every period.

A fix would be to get each port's buffer once per callback, at the top of
`internal_process_callback`, with the callback's `framecount`, and step
through it chunk by chunk (passing the pointers, or the offset into them, to
`process_audio`).

### What we do meanwhile

Our rig never connects two sources to one Sushi input. A self-test that used to
inject its test signal beside the instrument, into the instrument's own Sushi
input, now plays it through a track of its own on an input nothing else feeds.
Two other ways round it work too: summing the sources upstream into a single
connection, or running at a period of 64.
