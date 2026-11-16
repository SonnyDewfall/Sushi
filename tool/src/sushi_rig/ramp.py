"""A short dip of the master's volume around a live structural edit (#137).

Adding, removing or moving a plugin while the rig plays changes Sushi's graph
between one block and the next, and whatever the chain was holding (a delay's
tail, a reverb's, a compressor's gain) jumps: a pop. So the agent turns the
master down first, makes the edit, confirms it from a walk of the chains, and
turns the master back to where it was. A failed edit, or one that raises,
still brings the level back. Parameter and bypass changes do not dip.

## One set each way, because Sushi smooths a track's gain itself

Sushi 1.3.0 never applies a track's gain as a step. Its gain parameter runs
through a one-pole smoother (`src/engine/track.cpp`: `ValueSmootherFilter`,
`set_lag_time(GAIN_SMOOTHING_TIME, sample_rate / AUDIO_CHUNK_SIZE)`), updated
once per 64-sample chunk and ramped linearly across each chunk
(`ChunkSampleBuffer::ramp`), with a 20 ms lag time
(`include/sushi/constants.h`: `GAIN_SMOOTHING_TIME`) that its filter treats
as a rise time of 2.19 time constants (`dsp_library/value_smoother.h`). Read
from Sushi's 1.3.0 tag, 2026-11-16; not measured. So the agent sets the gain
once to go down and once to come back, and Sushi makes the curve; steps sent
from here over gRPC would arrive with the network's timing, not the audio's,
and each would restart the filter.

What that filter does, worked out from those constants (at any rate: the lag
is set in seconds):

- going down, the level falls about 0.95 dB per millisecond: -19 dB after
  20 ms, -30 dB after about 32 ms, -48 dB after 50 ms;
- coming back, it is within 6 dB in about 6 ms, within 1 dB in about 20 ms
  and within 0.1 dB in about 41 ms.

## The gap

The agent waits (`settle`) after the gain goes down before it sends the edit:
long enough for the level to be `DEPTH_DB` down (`FALL`, about 32 ms), plus
one block of the rig, since Sushi takes an event at the start of the block
after it arrives, and the gain's set and the edit could otherwise land one
block closer together in audio than they were sent. The block is the one
pw-top last measured for the rig (the budget `GET /rig/load` reads, which the
agent keeps from every sample it reads), and where none has been measured,
`MARGIN_BLOCK`: 1024 samples at 48 kHz, 21.3 ms. The rig asks for 256
(`rig.QUANTUM`), but its own captured pw-top sample (`tests/fixtures/
pw-top-b-n2.txt`) ran at 1024, and a JACK client keeps the quantum the graph
had when it started (`rig.py`), so 256 is not assumed. The wait is then about
37 ms at 256 and about 53 ms unmeasured or at 1024 (`SETTLE`, the unmeasured
wait). Then the edit and its read-back, then the gain is set back and returns
with no wait: the level is within 1 dB about 20 ms later. So the master is more
than 1 dB down for the wait + 20 ms, about 57 ms at 256 and 73 ms at 1024,
plus the edit's own commands and read-back, which have not been timed on the
rig (a whole live switch took 0.4 to 1.2 s there on 2026-11-02, every step of
it). A live switch dips once around all of its structural steps, not once per
step (`switch_span`).

## Where it goes back to, and what is never touched

The master's gain is the player's master volume (#131): the dip must put back
exactly what was there and be no change of the rig's at all. It is never
logged, never offered by Undo, never seen by a save (a save captures under
the live lock, which the dip holds), and `GET /live`, which does not wait for
the lock, shows the level the dip will put back rather than the dip.

A master volume set while the dip is under way is not lost:

- from the page, or anything else through the agent, it waits for the live
  lock the edit holds, and lands after the level is back, as the newest;
- from anywhere that reaches Sushi directly (a MIDI mapping Sushi serves
  itself, the v1 panel's OSC), the agent reads the gain again when the edit
  is done, and a value that is neither the floor it set nor the level it
  read is someone else's, newer than the dip: it is left as it is
  (`restore_to`). What it cannot see is a change in the instant between that
  read and its own set, or a change to exactly the floor (the fader's -inf)
  or exactly the level it started at, which reads as its own.

A master already at the floor is not dipped: there is nothing to hear.
"""

from __future__ import annotations

import math

# The master track and its own gain: what the page's master volume sets
# (`POST /live/parameter`, #131), named as `rigfile.MASTER` names the track.
MASTER = "master"
GAIN = "gain"

# Normalised, as the agent reads and sets parameters: Sushi's track gain
# runs from -120 to +24 dB (track.cpp `_common_init`), so 0 is -120 dB, its
# floor, the nearest it has to silence.
FLOOR = 0.0

# Sushi 1.3.0's gain smoother, as the module docstring reads it.
SMOOTHING_S = 0.020        # GAIN_SMOOTHING_TIME
RISE_TIME_CONSTANTS = 2.19  # value_smoother.h TIMECONSTANTS_RISE_TIME
CHUNK = 64                  # AUDIO_CHUNK_SIZE


def falling_db_per_second() -> float:
    """How fast the smoothed gain falls toward the floor, in dB a second (a
    negative number). The filter's coefficient per chunk is
    exp(-2.19 / (lag * chunks a second)), and the chunks a second cancel
    out, so the slope does not depend on the rate."""
    return 20.0 * math.log10(math.e) * -RISE_TIME_CONSTANTS / SMOOTHING_S


# How far down the master is when the edit is sent: a pop 30 dB down is
# under almost anything the rig is playing.
DEPTH_DB = 30.0

# How long the smoothed gain takes to fall DEPTH_DB (see "The gap").
FALL = DEPTH_DB / -falling_db_per_second()

# The block assumed where pw-top has measured none: 1024 at 48 kHz, the
# quantum the rig's own captured sample shows, four times what it asks for.
MARGIN_BLOCK = 1024 / 48000


def settle(block_s: float | None = None) -> float:
    """The wait between the gain going down and the edit going in, in
    seconds: `FALL` plus one block, `block_s` where one was measured (a
    positive, finite number of seconds), else `MARGIN_BLOCK`. Pure."""
    if (isinstance(block_s, bool) or not isinstance(block_s, (int, float))
            or not math.isfinite(block_s) or block_s <= 0):
        block_s = MARGIN_BLOCK
    return FALL + block_s


# The wait with no block measured.
SETTLE = settle(None)

# The steps of a live switch (`rigfile.SwitchStep.op`) that change the graph.
# A plan puts them together, after the bypasses that go on and before the
# parameters (`rigfile.diff`).
STRUCTURAL = frozenset({"remove", "move", "add"})


def switch_span(ops: list[str]) -> tuple[int, int] | None:
    """`(first, last)`: the steps of a switch's plan, by index, that one dip
    goes around, or None when none is structural. Pure.

    From the first structural step to the last, and the bypass that follows
    the last add with it: a plugin is added un-bypassed and bypassed by the
    very next step (`rigfile.diff`), so that bypass belongs to the add, and
    with the level back before it the plugin would be heard at its defaults
    for a step. Each add before the last has its bypass inside the span.
    """
    marked = [i for i, op in enumerate(ops) if op in STRUCTURAL]
    if not marked:
        return None
    last = marked[-1]
    if ops[last] == "add" and last + 1 < len(ops) and ops[last + 1] == "bypass":
        last += 1
    return marked[0], last

# How close two normalised values must be to count as the same: Sushi hands
# back a float32's worth of the value it was given.
SAME = 1e-6


def same(a: float, b: float) -> bool:
    return abs(a - b) <= SAME


def worth_dipping(level: float) -> bool:
    """Whether a master at `level` (normalised) is dipped: not when it is
    already at the floor."""
    return not same(level, FLOOR)


def restore_to(before: float, now: float | None) -> float | None:
    """What to set the master's gain to once the edit is done, or None to
    leave it: `before` is the level read before the dip, `now` the one read
    after the edit, None where that read failed. Pure.

    - Read back as the floor the dip set, or as `before` (Sushi applies a set
      at the start of a block, so a read can still see the value from before
      it): put `before` back.
    - Not read: put `before` back. Leaving a master silent is the one outcome
      that must not happen; overwriting a change made in the last 60 ms is
      the lesser fault.
    - Anything else: someone set it during the dip, and theirs is newer than
      the dip's. Leave it.
    """
    if now is None or not isinstance(now, (int, float)) or not math.isfinite(now):
        return before
    if same(now, FLOOR) or same(now, before):
        return before
    return None
