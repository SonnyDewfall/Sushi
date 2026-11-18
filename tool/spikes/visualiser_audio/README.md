# How the visualiser hears the guitar (#176), 2026-11-18

The question: the sound is in Sushi, the visualiser in the browser. Which way
in adds nothing to Sushi's path and cannot cause an xrun?

- **(a)** the browser captures an input itself (`getUserMedia`), through
  PipeWire's PulseAudio server;
- **(b)** the agent taps the audio (here `parec`, also a PulseAudio client)
  and streams it to the page.

**Chosen: (a), the guitar's own input on the interface.** Both go through the
same server, which copies each block into a buffer of its own in the
PipeWire graph and hands it to the client from there, so a client that falls
behind loses its own samples and cannot hold up the graph. (b) costs about
the same and adds an audio stream through the agent's one event loop, which
serves every control on the page, and code to write; it would also work for
a phone, which the visualiser does not serve. A JACK client or `pw-record`
tap was not tried: either is a node of the graph whose own process callback
runs each cycle, so a slow one is late in Sushi's cycle (PipeWire 1.0.5 has
no `node.async`), which is the risk the question rules out.

## Where it was run

This container, not the rig: 4 cores of a Xeon at 2.1 GHz, no GPU, Ubuntu
24.04's PipeWire 1.0.5 (the rig's version) in a private session
(`session.sh`), Chromium 153 (Playwright's build, headless). The interface
is stood in by a 4-channel null sink and a 10-channel virtual source named
as on the rig but "Fake"; Sushi by `standin_sushi.py`, a JACK client under
`pw-jack` at the rig's 256/48000 taking AUX0 to both outputs; the guitar by a
plucked low E on AUX0 and a quiet 1 kHz on AUX1 (`mkwav.py`). No real-time
scheduling, so the stand-in has a baseline of xruns of its own (0 to 2 in
30 s), and the counts below are against that. Firefox could not be
installed (its download host is blocked here).

## Measured

What Chromium lists (`chromium_devices.py`, `site/devices.html`): `default`
and **Fake iD14 Multichannel**, and **not** the sink's monitor: Chromium
leaves PulseAudio monitor sources out of its inputs, so it cannot capture
what the rig plays without a PipeWire change. Firefox's audio backend
(cubeb's PulseAudio code, read, not run) lists every source, monitors
included.

What a capture of the 10-channel input gives: 2 channels at 48 kHz, peaks
0.454 on the left and 0.100 on the right, that is AUX0 (the guitar) on the
left and AUX1 on the right. The stream PipeWire made: `s16le 2ch 48000Hz`,
`node.latency = 1024/48000`. The graph stayed at a quantum of 256.

CPU over 30 s (`measure.py`, % of one core):

| | PipeWire | its PulseAudio server | Sushi stand-in | the client | new xruns, stand-in |
|---|---|---|---|---|---|
| nothing | 1.2 | 0.0 | 1.9 | — | 0 to 2 |
| (a) Chromium capturing, 60 analyser reads a second | 1.5 | 1.6 | 1.9 | Chromium 5.2 | 2 |
| (b) `parec` and a reader making butterchurn's bytes 60 times a second | 1.3 | 1.0 | — | parec 0.3, reader 1.1 | — |
| (a) and butterchurn at 1280 by 720, SwiftShader | 1.5 | 1.4 | 1.9 | Chromium 173 (10 fps) | 2 |
| (a) and butterchurn at 640 by 360, SwiftShader | 1.5 | 1.4 | 1.8 | Chromium 157 (30 fps) | 11 |

(b)'s row has no network and no page, so its real cost is more. A software
renderer is ruinous: the page refuses one.

The page as built, driven against the fake agent with this session's
PipeWire: it picked "iface Multichannel" (a source named after the fake
machine file's `iface`), left channel, by the machine file's `guitar-in`;
`pw-top` showed a `Chromium input` node and **no Chromium output node** (the
context's `sinkId: {type: "none"}`); after a pointer move the capture was let
go and the context suspended.

## Not measured

The rig: its GPU (Iris Xe), its browser, its PipeWire graph with the real
Sushi, the iD14 and the patchbay; the permission prompt (answered by a
Chromium switch here); Firefox at all.

## Run it again

```bash
tool/spikes/visualiser_audio/session.sh start /tmp/vis      # needs the packages it names
export XDG_RUNTIME_DIR=/tmp/vis/run
python3 tool/spikes/visualiser_audio/chromium_devices.py http://localhost:8765/devices.html
python3 tool/spikes/visualiser_audio/measure.py /tmp/vis capture http://localhost:8765/viz.html 1280 720
python3 tool/spikes/visualiser_audio/measure.py /tmp/vis render  http://localhost:8765/viz.html 640 360 --enable-unsafe-swiftshader
tool/spikes/visualiser_audio/session.sh stop /tmp/vis
```

(b) by hand: `parec -d fake_id14_in --channels=1 --channel-map=aux0
--format=s16le --rate=48000 --latency-msec=20 --raw | python3
tap_consumer.py`, and the processes' CPU from `/proc`.
