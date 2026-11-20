# How the visualiser hears the guitar (#176), 2026-11-18

The question: the sound is in Sushi, the visualiser in the browser. Which way
in adds nothing to Sushi's path and least risks an xrun?

- **(a)** the browser captures an input itself (`getUserMedia`), through
  PipeWire's PulseAudio server;
- **(b)** the agent taps the audio (here `parec`, also a PulseAudio client)
  and streams it to the page.

**Chosen: (a), the guitar's own input on the interface.** Neither adds
anything to the signal's path or its delay. Both go through the same server,
pipewire-pulse, which keeps a buffer of its own for each client and hands the
client its samples from there, so a client that stalls only loses its own
samples. **What that does not keep apart** (corrected in the review of PR
#183; this README first said the server "cannot hold up the graph"):
pipewire-pulse's capture stream is itself a node in the graph Sushi's driver
runs, a follower of the interface, doing a small copy every cycle in
pipewire-pulse's data thread (`pw-top` shows it: BUSY about 17 to 22 µs). If
that thread is late, because it has no realtime priority or is preempted, the
cycle is late and Sushi can xrun. So the question's "cannot cause an xrun" is
not met by any capture of the live graph, this one included; it is made
small, and watched (the page stops the visualiser when the rig's xruns rise
while it listens, and `sushi-rig doctor` says whether pipewire-pulse runs
realtime). (b) costs about the same and adds an audio stream through the
agent's one event loop, which serves every control on the page, and code to
write; it would also work for a phone, which the visualiser does not serve.
A JACK client or `pw-record` tap was not tried. Either would be a node of the
graph in the same way, its own process doing the per-cycle work (and
`pw-record` writing a file from its process callback), so it would be no
safer, and with `pw-record` a blocked write is a late node (PipeWire 1.0.5
has no `node.async`).

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

## The rig's output (#195), 2026-11-20

The user asked for the rig's processed output, not the guitar's input. Since
Chromium lists no monitor sources, the tap is a real `Audio/Source`: a
`pw-loopback` (`tool/src/sushi_rig/vistap.py`), started with the rig in this
first run and on demand since (below). Run with
the tool's own code against this session (`session.sh`; the stand-in Sushi
drives AUX0 through `tanh` to both outputs): `vistap.child()` started as the
rig starts a child, `vistap.link()` with a compiled-shaped config whose master
is on engine bus 0. Same container, with another job's test suite running on
the same four cores. What was seen:

- **The first run found a defect**: PipeWire 1.0.5's `pw-loopback` lists
  `-m, --channel-map` in its help but refuses `--channel-map` and exits, so
  no node appeared and `link` gave up after its 5 s and said so. The short
  options work; the code uses them, and a test pins them.
- `link` made `sushi:audio_output_0 -> sushi_rig_output_tap:input_FL` and
  `audio_output_1 -> input_FR` in 0.29 s. Those were the only links of either
  node (`pw-dump`'s links): nothing to the interface's sink, and the source
  linked only to a capture while one was open.
- `pactl list sources`: `sushi_rig_output`, "Description: Sushi rig output".
  Chromium (`chromium_devices.py`) listed **Sushi rig output** beside
  "Fake iD14 Multichannel"; captured, peaks 0.876 on both channels (the
  stand-in's output) against 0.45 and 0.10 on the interface's input.
- Its threads: `pw-loopback` (normal) and `pw-data-loop` SCHED_FIFO 83, with
  `PIPEWIRE_CONFIG_NAME=client-rt.conf`. Without it (by hand, 2026-11-19),
  the data thread was at normal priority and the tap's ERR rose about once a
  second (0.3 to 1 a second, with and without a capture); with it, once in
  20 s in that run. `client.conf`, what `pw-loopback` reads otherwise, loads
  no `module-rt`. **What ERR on the tap means** (review of PR #200): it is
  charged to the node that was late, and the driver waits for its followers,
  so each is a cycle the graph did not finish in time (PipeWire logs `client
  too slow!` for the node in `check_states`, beside `graph not finished`),
  not a lateness private to the tap. The driver here is a null sink's timer,
  so its own count says nothing about that.
- `pw-top`, 14 samples a second apart, both nodes followers of the driver
  (`+`), quantum 256 at 48 kHz (a 5333 µs cycle): BUSY 5.1 to 13.0 µs (mostly
  6 to 8) for `sushi_rig_output_tap`, 1.7 to 2.8 µs for `sushi_rig_output`;
  WAIT, the wake-up latency, 32 to 123 µs for the capture side. This costs
  the cycle for as long as the tap is linked, whether or not a browser
  listens; a capture adds pipewire-pulse's stream as before.
- **The default source.** With no other source of priority, WirePlumber
  0.4.17 made the tap the default source, even at `priority.session = 0`; a
  source at 1600 took it back at once. On the rig every ALSA source has
  more (`tool/tests/fixtures/pw-dump-rig.json`: the iD14's input 2100, the
  others 1680 to 2109), so it should never be the default there. Not seen on
  the rig.
- `kill -9` of the tap's pid: both nodes and the source gone at once.

Not seen: any of it on the rig (the iD14 as driver, the real Sushi, rtkit
rather than root granting realtime), or in Firefox.

### On demand (review of PR #200), 2026-11-20

The tap is now the agent's, started when the page's visualiser is about to
listen and stopped when it lets go (`tool/src/sushi_rig/vistap.py`). Run
through the real `Supervisor` (`ondemand_check.py`) against this session,
started with `PIPEWIRE_DEBUG=pw.node:4` so that each `client too slow!` is in
`pipewire.log`: a held rig whose `sushi` is a sleeper (the session's stand-in
Sushi has the ports), the lease shortened to 4 s and the linger to 5 s.

- `Supervisor.tap` answered in 0.32 s: started in the rig's group, linked
  (its only links from `sushi:audio_output_0/1`), `pw-data-loop` SCHED_FIFO
  83, "Sushi rig output" listed by `pactl`.
- 15 s with the rig up and no tap: `client too slow` for `sushi` 2,
  `test_signal` 1, `fake_id14_in` 1. The next 15 s with the tap linked and
  no capture: `sushi_rig_output_tap` 5 (its ERR rose by 5), `sushi` 3,
  `test_signal` 11. Realtime, in a container whose four cores another job's
  test suite was using: five unfinished cycles in 15 s, where the first run
  had one in 20 s. BUSY as before: 4.9 to 13.4 µs and 2.3 to 3.8 µs.
- No renewal: the lease ran out and, 5 s later, the watch stopped the tap by
  its pid, 7.0 s after the last renewal, "no page held its lease for 5s";
  no crash recorded, the pid gone, no node left.
- Asked for again, then released with `now`: stopped at once ("its page's
  xrun safeguard tripped"), no node left. `down` then stopped the rig.
- Not seen: the capture in this run (`parec` killed before it flushed
  anything; the first run's Chromium capture stands), the page itself
  against this agent, and any of it on the rig.

## Run it again

```bash
tool/spikes/visualiser_audio/session.sh start /tmp/vis      # needs the packages it names
export XDG_RUNTIME_DIR=/tmp/vis/run
python3 tool/spikes/visualiser_audio/chromium_devices.py http://localhost:8765/devices.html
python3 tool/spikes/visualiser_audio/measure.py /tmp/vis capture http://localhost:8765/viz.html 1280 720
python3 tool/spikes/visualiser_audio/measure.py /tmp/vis render  http://localhost:8765/viz.html 640 360 --enable-unsafe-swiftshader
python3 tool/spikes/visualiser_audio/tap_check.py /tmp/vis      # the rig's output (#195)
python3 tool/spikes/visualiser_audio/ondemand_check.py /tmp/vis # ... on demand, through the Supervisor
tool/spikes/visualiser_audio/session.sh stop /tmp/vis
```

(b) by hand: `parec -d fake_id14_in --channels=1 --channel-map=aux0
--format=s16le --rate=48000 --latency-msec=20 --raw | python3
tap_consumer.py`, and the processes' CPU from `/proc`.
