# Taking a channel out of a running Sushi (#190)

Why removing a playing channel is a restart, not a live step, and what it
would take to make it live. Run 2026-11-24 against Sushi 1.3.0's release
AppImage with its dummy frontend and gRPC, on this container, not the rig.

The agent can plan a track's removal as one live step (`rigfile.diff`'s
`remove_tracks`, the switch's `remove_track` step), and
`live.remove_live_track` makes it: each processor of the track from the
back, then elkpy's `delete_track`. **The agent does not use it**: `Live.at`
gives no `remove_track` (PR #238's review), so the removal restarts, asked
first ("Restart?"). The live path is tested against stand-ins only
(`tests/test_agent_channel_remove.py`). This probe is what the decision
rests on. It used to be a slow test; it is a probe because a stuck Sushi
ignores SIGTERM and would hold up CI.

## What it measures

Each run starts Sushi with two channels, `stays` (a 440 Hz sine) and `goes`
(660 Hz), each through its send to the master, whose return a wav_writer
records. On a fresh elkpy connection it takes `goes` out while it plays and
says, for each command, whether Sushi confirmed it (within 6 s) and whether
it was applied; then whether Sushi is still up, and, from the master's
spectrum in 100 ms blocks, that the master went from both sines to the
440 Hz alone (`both>stays`), with nothing of the 660 Hz after.

## What it found

| Order | Wait after connecting | Runs | Clean | What went wrong |
|---|---|---|---|---|
| send first, streamer, `delete_track` (what `remove_live_track` does) | 0.5 s | 300 | 297 | 3 stuck at `delete_track` on the emptied track |
| streamer first, send, `delete_track` | 0.5 s | 132 | 130 | 1 stuck at the send's deletion; 1 send deletion applied and never confirmed |
| streamer first, send, `delete_track` | none | 150 | 140 | 10 first commands applied and never confirmed |
| `delete_track` alone, the track still holding its processors | 0.5 s | 300 | 300 | none |

- **Stuck** means Sushi neither applied the command nor confirmed it, went
  on answering reads and playing, applied no graph change after (another
  deletion sent later was not applied either), and then ignored SIGTERM until
  it was killed.
- **Never confirmed** means applied, but elkpy never saw Sushi's notice.
  elkpy subscribes to those notices on a thread of its own when a
  connection is made, and a command sent before that subscription is up is
  never confirmed. It is nearly always the first command on a connection.
  The agent would take it as a failed step and restart the rig without
  asking.
- **Where it worked**, the track, its processors and its send were gone from
  Sushi's lists, and the master carried the other channel alone with nothing
  of the removed one in it.

**The lead:** Sushi's own `delete_track` takes a track's processors off itself
(`AudioGraphController::delete_track`, read in Sushi 1.3.0's source), all in
one control event. Sent alone, it was clean 300 times in 300. That is not yet
enough to turn the live removal on: a 1% stuck rate would pass 300 runs about
one time in twenty, and none of this is the JACK frontend the rig runs, whose
engine connections the source says `AudioEngine::delete_track` removes first.

Two wav_writers drift apart on the dummy frontend (11168 frames in one run),
so the master is read by its spectrum, not against a second recording.

## Running it

```bash
# from the checkout, with ./sushi the AppImage's AppRun (as CI's image has it) and elkpy installed
PYTHONPATH=tool/src python3 tool/spikes/live_remove_track/probe.py --runs 300 --order send-first --pause 0.5
PYTHONPATH=tool/src python3 tool/spikes/live_remove_track/probe.py --runs 300 --order track-only
PYTHONPATH=tool/src python3 tool/spikes/live_remove_track/probe.py --runs 150 --order src-first --pause 0
```

`--sushi` names another binary, and `--keep` keeps the folder (Sushi's log,
the recording) of a run that went wrong. A run takes about 1.5 s. It prints
one line per run and a tally at the end, and exits 1 if any run was not
clean. A stuck Sushi is killed by the probe itself. pytest never collects
this file (`testpaths = ["tests"]`).
