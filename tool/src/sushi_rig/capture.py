"""The capture engine: PipeWire's ports into the recorder, block by block.

Issues #53 and #81. `recorder.py` is the half that was built first and tested
without a soundcard: a ring, a writer thread, the stems. This is the half that
needs one. It registers a JACK client (PipeWire serves JACK clients through
`pw-jack`), gives it one input port per column of a take, links each to the port
the take names, copies every block into `Recorder.push`, and stops cleanly.

## Is a Python callback the real engine? On the evidence, yes

The question that decided whether this module is the engine or a stopgap before
a native helper was whether a Python process callback keeps up. The spike
(`tool/spikes/live_rig/capture_engine.py`) answered it on the rig machine
(i7-1260P, PipeWire 1.6.2, Firefox and a desktop app open) on 31 Oct 2026, with
a counter played into the same six columns a guitar-and-vocal take has, and the
recorder writing real files. Three runs, in `capture-*.json` beside the spike:

    run                 quantum  budget    callback p50 / p99 / p99.9 / max      xruns  lost
    256, idle, 600 s    256      5333 us   271 / 607 / 862 / 1344 us            0      0
    256, 16 busy procs  256      5333 us   124 / 212 / 293 / 851 us             0      0
    128, idle, 300 s    128      2667 us   304 / 709 / 889 / 1205 us            0      0

The p99.9 is 16%, 5% and 33% of the budget, and the callback was faster under
full load than idle, not slower. So the callback is Python, and the callback
does little: copy six columns, one `push`, one silence check. A first load run
once showed a single 5774 us block, over a 5333 us budget, with no xrun; it did
not recur and its report was not kept. That is the reason this module counts
xruns and reports its slowest callback, instead of trusting the three runs.

The 256 idle run also did not come out clean, and what it found is why the
silence check exists. Its read-back counted 257 "jumps" on three of six
columns: all consecutive, from sample 9965823 (207.6 s in), each of the three
holding exactly 256 samples of zero with the counter continuing correctly on
both sides. The other three columns, written from the same pushed block, were
perfect. Nothing was late and nothing was dropped: three of six input ports had
delivered one block of zeros. xruns, lost frames and errors were all 0. It
happened in one of two otherwise identical 600 s runs. Not explained. The
hypothesis (not confirmed) is a client entering or leaving the graph.

## The silent-block check

That is a take with a 5 ms hole and every counter green, which the recorder's
accounting cannot see because by its own lights nothing went wrong. So the
recorder now watches each column (`recorder.SilenceWatch`): a block of exact
zeros on a column that carried signal in the block before is counted, per
column, and `take.yaml` names the columns. A dropped link looks exactly like
this. The engine adds what only it can see: xruns, a block size that changed
mid-take, blocks it could not copy.

## What has and has not been run

Run on the rig machine: the spike, above, whose callback this one is derived
from. Run here, in the test suite: the plan from a take's columns, the
bookkeeping (`BlockPump`, driven by stand-in ports carrying a counter), the
silence check, the checks on what JACK reports, and that the names this module
calls exist in the installed JACK-Client.

Not run by anyone: **this module against a live PipeWire.** There was no
PipeWire, JACK or soundcard where it was written. `CaptureEngine`'s calls into
JACK, the order of activate and connect, the exact port names PipeWire gives
the Audient iD14 and Sushi through `pw-jack`, and the claim below that a stereo
stem's ports are consecutive, are all unverified. The first run on the rig is
the test, and its result goes in the MANIFEST either way.

Three things to know before that run. The process has to be started under
`pw-jack`, or JACK-Client finds a libjack that talks to no server. Nothing in
`listen` constructs a `CaptureEngine` yet: that needs the port names a take
records, which follow from the machine file and the rig (see `transport.py`).
And the JACK dependency is the `[capture]` extra; importing this module never
imports it.

## JACK is imported late on purpose

`import jack` opens libjack at import time and raises `OSError` if there is
none. A machine without it, which is every CI runner, would then be unable to
import this module and test the plan, the bookkeeping or the checks, which need
no JACK at all. So `load_jack` is the only place that imports it, it is called
from `CaptureEngine.start`, and its failure is an error naming the extra and
`pw-jack` rather than a traceback.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Sequence

from ._numpy import require_numpy
from .take import Stem

# The name the client has in the graph, and so in qpwgraph and pw-top.
CLIENT_NAME = "sushi-rig-capture"

# How long to wait after the first activation for the quantum to settle. The
# spike saw it go 256 -> 1024 -> 256 when the client started; half a second was
# enough on the rig, and is chosen, not derived.
SETTLE_S = 0.5

# After connecting, how often and how long to wait for the graph to show the
# links before refusing the capture. Under pipewire-jack the client's view of
# the registry may lag a connect(); whether it does is unverified (nobody has
# run this against one), so the read-back retries rather than assuming it is
# synchronous. Half a second in all, paid only when a link is not yet visible.
# Chosen, not measured.
LINK_ATTEMPTS = 10
LINK_RETRY_S = 0.05

# The most frames one process callback may deliver. The block buffer is
# allocated to this once, so no callback allocates. Chosen, not measured: far
# above any quantum the rig has run (128 to 1024 in the spike's reports) at a
# few hundred kilobytes for six columns. A block of more than this is not
# copied, and is counted and reported, never dropped without saying so.
MAX_BLOCK = 8192

# Everything this module calls on JACK-Client, so a test can check each name
# exists in the installed version. That proves the names, not the behaviour;
# behaviour needs a server.
JACK_CLIENT_ATTRIBUTES = (
    "inports", "get_ports", "get_all_connections", "connect", "activate",
    "deactivate", "close", "blocksize", "samplerate", "name",
    "set_process_callback", "set_xrun_callback", "set_blocksize_callback",
    "set_shutdown_callback",
)
JACK_MODULE_NAMES = ("Client", "JackError", "OwnPort")
JACK_OWN_PORT_ATTRIBUTES = ("name", "get_array")


class CaptureError(RuntimeError):
    """A capture that must not start, or could not. Carries every problem
    found, for the reason `take.TakeError` does: fixed in one pass, not one
    attempt per problem with a performance waiting."""

    def __init__(self, problems: Sequence[str]):
        self.problems = list(problems)
        super().__init__("; ".join(self.problems))


class CaptureUnavailable(CaptureError):
    """JACK-Client, or the libjack it needs, is not on this machine."""


# --------------------------------------------------------------------------- #
# Decisions
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class Column:
    """One column of a pushed block: which stem it belongs to, which channel of
    it, and the port it is linked from."""

    index: int
    stem: str  # the stem's file name
    channel: int  # within the stem, from 0
    source: str  # JACK port name, `<client>:<port>`

    @property
    def port(self) -> str:
        """The name of this engine's own input port for the column. Fixed by
        the column, so a port is the same one in every take."""
        return f"in_{self.index}"


_TRAILING_NUMBER = re.compile(r"^(.*?)(\d+)$")


def numbered_ports(port: str, width: int) -> list[str] | str:
    """The ports a stem of `width` channels is captured from, or why not.

    A take names one port per stem, and a stereo stem has two. The rule is that
    its channels are the consecutive numbered ports starting at the one named:
    `sushi:audio_output_0` is `_0` and `_1`. That is how Sushi numbers a stereo
    track's outputs (`patchbay.py` links them that way) and how a multichannel
    interface numbers its `capture_AUXn`. It is a convention, not something
    JACK promises, so a client that numbers otherwise would be linked wrongly
    and silently; the one case that can be caught is a port with no number to
    count from, which is refused here rather than guessed at. Digits keep their
    width (`_01` is followed by `_02`).

    Returns a string, the reason, when it cannot say, so the caller can gather
    every reason; a list otherwise.
    """
    if width < 1:
        return f"a stem of {width} channels captures nothing"
    if width == 1:
        return [port]
    match = _TRAILING_NUMBER.match(port)
    if match is None:
        return (
            f"{port!r} ends in no number, so the other {width - 1} channel(s) of "
            f"a {width}-channel stem cannot be told from it; name a port that "
            f"ends in one, like audio_output_0"
        )
    prefix, digits = match.groups()
    start = int(digits)
    return [f"{prefix}{start + k:0{len(digits)}d}" for k in range(width)]


def plan_columns(stems: Sequence[tuple[Stem, int]]) -> list[Column]:
    """The columns of a take's blocks, in order, with the port each is linked from.

    `stems` is `TakePlan.stems`: each stem with its channel count, in the order
    the recorder lays them out. Refuses, with every reason, a plan that would
    link two columns from one port: the take module refuses two stems from one
    port, but a stereo stem's second channel can land on another stem's port,
    and two columns from one port are one signal under two names.
    """
    problems: list[str] = []
    columns: list[Column] = []
    for stem, channels in stems:
        ports = numbered_ports(stem.port, channels)
        if isinstance(ports, str):
            problems.append(f"stem {stem.filename}: {ports}")
            continue
        for channel, source in enumerate(ports):
            columns.append(Column(len(columns), stem.filename, channel, source))
    if not columns and not problems:
        problems.append("a capture needs at least one column")
    seen: dict[str, Column] = {}
    for column in columns:
        earlier = seen.setdefault(column.source, column)
        if earlier is not column:
            problems.append(
                f"{earlier.stem}[{earlier.channel}] and {column.stem}[{column.channel}] "
                f"would both be captured from {column.source}, so they are one signal "
                f"under two names"
            )
    if problems:
        raise CaptureError(problems)
    return columns


def missing_sources(columns: Sequence[Column], available: Iterable[str]) -> list[str]:
    """Every source port that is not in the graph, named, with a few that are.

    Run before anything is linked, so a mistyped port or an unplugged interface
    is one sentence naming it, not a half-linked client. The ports that do
    exist are listed because the name PipeWire gives a device through JACK is
    not the one its own tools print, and the fix is to copy the right one.
    """
    present = set(available)
    absent = [c for c in columns if c.source not in present]
    if not absent:
        return []
    shown = sorted(present)[:8]
    hint = (
        f"output ports in the graph include: {', '.join(shown)}"
        f"{' …' if len(present) > len(shown) else ''}"
        if present else "there are no output ports in the graph at all"
    )
    return [
        f"no port called {c.source!r} (for {c.stem}[{c.channel}]) is in the graph" for c in absent
    ] + [hint]


def check_links(columns: Sequence[Column], actual: Sequence[Sequence[str]],
                client: str = CLIENT_NAME) -> list[str]:
    """After linking, is each input port fed by its source and nothing else?

    `actual[i]` is what column `i`'s port is connected to, read back from the
    graph. A link that did not take is a column of silence; an extra link into
    the port (a session manager's auto-connect) is summed into it, and the
    column holds a mix and says it is one signal. Both are refused.
    """
    problems = []
    for column, linked in zip(columns, actual):
        linked = list(linked)
        if linked != [column.source]:
            got = ", ".join(linked) if linked else "nothing"
            problems.append(
                f"{client}:{column.port} should be fed by {column.source} alone "
                f"(for {column.stem}[{column.channel}]), but is fed by {got}"
            )
    if len(actual) != len(columns):
        problems.append(f"{len(columns)} columns but {len(actual)} ports read back")
    return problems


def settle_links(read: Callable[[], Sequence[Sequence[str]]], columns: Sequence[Column], *,
                 client: str = CLIENT_NAME, attempts: int = LINK_ATTEMPTS,
                 wait_s: float = LINK_RETRY_S,
                 sleep: Callable[[float], None] = time.sleep) -> list[str]:
    """`check_links` over a few reads, returning the last attempt's problems.

    A link made a moment ago may not be visible to this client yet, and
    refusing a good start for that would be the engine's fault. A link that is
    wrong stays wrong across every attempt, so the retry delays only the
    refusal, by at most `attempts * wait_s`. Nothing is waited when the first
    read is clean.
    """
    problems: list[str] = []
    for attempt in range(max(1, attempts)):
        problems = check_links(columns, read(), client)
        if not problems:
            return []
        if attempt + 1 < attempts:
            sleep(wait_s)
    return problems


def check_granted(sample_rate: int, quantum: int, granted_rate: int, granted_quantum: int) -> list[str]:
    """Is the graph running at the rate and block size the take was set up for?

    The recorder's ring is sized for the quantum, and `take.yaml` states the
    rate and quantum the plan said. If PipeWire grants something else, the
    take would be wrong about itself from its first line. The engine cannot
    change the plan once the take exists, so it refuses; the caller builds
    the plan for the quantum PipeWire is set to. PipeWire applies
    `PIPEWIRE_QUANTUM` to a node only when it starts, which is why the granted
    value is read after activation (`CaptureEngine._read_granted`).
    """
    problems = []
    if granted_rate != sample_rate:
        problems.append(f"the graph runs at {granted_rate} Hz, but the take was planned for {sample_rate} Hz")
    if granted_quantum != quantum:
        problems.append(
            f"the graph granted a quantum of {granted_quantum}, but the take was planned "
            f"for {quantum} (PIPEWIRE_QUANTUM reaches a client only once it starts, so "
            f"set it in the environment of this process)"
        )
    return problems


# --------------------------------------------------------------------------- #
# The audio thread's bookkeeping
# --------------------------------------------------------------------------- #

class BlockPump:
    """What happens each cycle, apart from JACK: the process callback's body.

    `ports` are anything with a `get_array()` returning that cycle's samples,
    which is what a JACK input port is; the test drives it with stand-ins that
    carry a counter, the spike's counter source moved out of product code.

    It exists as its own class so that the bookkeeping can be pinned by tests
    that never meet a soundcard, and so the callback JACK sees is one bound
    method. It follows the recorder's rule: allocate everything up front. The
    block buffer is `MAX_BLOCK` frames; a callback pushes a view of its first
    `frames` rows, so a block size that changes mid-take is still recorded,
    where the spike skipped it and left the take short with nothing saying so.

    Every cycle is accounted for. A block that cannot be copied is counted in
    frames, a push that raises is counted apart (see `__call__`), and an exception in the callback is held, not raised: JACK removes
    a client whose callback fails, which would silently end the capture, so the
    first one is kept for the report and the cycles after it are counted as
    skipped.
    """

    def __init__(self, ports: Sequence[Any], push: Callable[[Any], int], quantum: int,
                 *, max_frames: int = MAX_BLOCK, clock: Callable[[], int] = time.perf_counter_ns):
        np = require_numpy()
        if not ports:
            raise ValueError("a pump needs at least one port")
        if quantum < 1 or max_frames < quantum:
            raise ValueError(f"quantum {quantum} must be between 1 and max_frames {max_frames}")
        self._ports = tuple(ports)
        self._push = push
        self._clock = clock
        self._block = np.zeros((max_frames, len(self._ports)), dtype=np.float32)
        self.quantum = quantum
        self.blocks = 0
        self.off_quantum_blocks = 0  # copied, but not `quantum` frames long
        self.skipped_blocks = 0
        self.skipped_frames = 0
        self.push_errors = 0  # `push` raised: the ring may or may not hold the block
        self.xruns = 0
        self.quantum_changes = 0
        self.slowest_ns = 0
        self.error = ""

    def __call__(self, frames: int) -> None:
        """The process callback: copy this cycle's ports into one block and push it."""
        started = self._clock()
        copied = False
        try:
            if frames < 1 or frames > len(self._block):
                self.skipped_blocks += 1
                self.skipped_frames += max(frames, 0)
                return
            if frames != self.quantum:
                self.off_quantum_blocks += 1
            block = self._block
            for column, port in enumerate(self._ports):
                block[:frames, column] = port.get_array()
            copied = True
            self._push(block[:frames])
            self.blocks += 1
        except Exception as exc:  # noqa: BLE001 — held and reported, see the class docstring
            self.error = self.error or f"{type(exc).__name__}: {exc}"
            if copied:
                # `Recorder.push` copies into the ring and then runs the silence
                # watch, so a raise from it can come after the ring kept the
                # block. Claiming the frames as skipped would tell the take they
                # are missing from stems that hold them, and the recorder's
                # count would disagree. They are not claimed; the error is.
                self.push_errors += 1
            else:
                self.skipped_blocks += 1
                self.skipped_frames += max(frames, 0)
        finally:
            elapsed = self._clock() - started
            if elapsed > self.slowest_ns:
                self.slowest_ns = elapsed

    def xrun(self, delay: float = 0.0) -> None:
        """JACK's xrun callback: a deadline was missed, so audio may be missing
        that nothing else counts."""
        self.xruns += 1

    def blocksize(self, size: int) -> None:
        """JACK's block size callback. A change from the quantum the take was
        planned for means `take.yaml`'s quantum is no longer what it ran at."""
        if size != self.quantum:
            self.quantum_changes += 1

    def account(self, sample_rate: int) -> dict[str, Any]:
        """What the engine saw, as `take.yaml` carries it under `capture.engine`.
        Always the same keys, so a take with no problems can be told from one
        with no engine."""
        return {
            "quantum": self.quantum,
            "blocks": self.blocks,
            "xruns": self.xruns,
            "quantum_changes": self.quantum_changes,
            "off_quantum_blocks": self.off_quantum_blocks,
            "skipped_blocks": self.skipped_blocks,
            "skipped_frames": self.skipped_frames,
            "push_errors": self.push_errors,
            "slowest_callback_us": round(self.slowest_ns / 1000.0, 1),
            "budget_us": round(1e6 * self.quantum / sample_rate, 1),
            "error": self.error,
        }


# --------------------------------------------------------------------------- #
# Effects
# --------------------------------------------------------------------------- #

def load_jack():
    """Import JACK-Client, or raise `CaptureUnavailable` saying how to get it.

    The only import of `jack` in the package; see the module docstring for why
    it is late. `OSError` is what a missing libjack raises, `ImportError` a
    missing package.
    """
    try:
        import jack
    except ImportError as exc:
        raise CaptureUnavailable([
            f"JACK-Client is not installed ({exc}): pip install -e \"tool[capture]\""
        ]) from exc
    except OSError as exc:
        raise CaptureUnavailable([
            f"JACK-Client could not find libjack ({exc}): install libjack-jackd2-0 "
            f"(or pipewire-jack), and run this under pw-jack"
        ]) from exc
    return jack


class CaptureEngine:
    """Reads a take's ports from the JACK graph and feeds the recorder.

    Implements `transport.Engine`. Built with the rate and quantum the take was
    planned for; `start` refuses if the graph is not running at them. One
    capture at a time; `stop` may be called twice, or after a failed `start`.

    The graph's rate and quantum are read once per engine, not once per take
    (`prepare`): reading them costs an activation and a wait, on the thread
    that called `start`, which in the listener is the one OSC thread. A graph
    that changes quantum later is not noticed up front; it shows in the take's
    account as `quantum_changes` and `off_quantum_blocks` instead.

    `stop` returns the engine's account (`BlockPump.account`) for the take to
    keep. It deactivates the client, which guarantees no further callback, and
    only then reads the counters, so they are final.

    **Not run against a live PipeWire.** See the module docstring.
    """

    def __init__(self, sample_rate: int, quantum: int, *, client_name: str = CLIENT_NAME,
                 settle_s: float = SETTLE_S, link_retry_s: float = LINK_RETRY_S):
        if sample_rate <= 0 or quantum <= 0:
            raise ValueError(f"sample_rate and quantum must be positive, got {sample_rate} and {quantum}")
        self.sample_rate = sample_rate
        self.quantum = quantum
        self.client_name = client_name
        self.settle_s = settle_s
        self.link_retry_s = link_retry_s
        self._granted: tuple[int, int] | None = None
        self._client = None
        self._pump: BlockPump | None = None
        self._shutdown = ""
        self._account: dict[str, Any] = {}

    def prepare(self) -> None:
        """Read the graph's rate and quantum now, so the first `start` does not.

        Call it where a pause is harmless, at the listener's start, not inside
        RECORD. `start` calls it itself if nobody has, so forgetting is a
        delay and not a fault. Uses a throwaway client, closed before returning.
        """
        jack = load_jack()
        client = self._open(jack)
        try:
            self._granted = self._read_granted(client)
        finally:
            try:
                client.close()
            except Exception:  # noqa: BLE001 — the answer is already in hand
                pass

    def start(self, stems: Sequence[tuple[Stem, int]], push: Callable[[Any], int]) -> None:
        """Register, check, activate, link, read the links back. Any failure
        tears the client down and raises `CaptureError` with every problem."""
        if self._client is not None:
            raise CaptureError(["already capturing"])
        # First, before anything can fail: a start refused at the plan or at
        # JACK must not leave the last take's account for `stop` to return as
        # this one's.
        self._shutdown = ""
        self._account = {}
        columns = plan_columns(stems)
        jack = load_jack()
        if self._granted is None:
            self.prepare()
        rate, quantum = self._granted
        problems = check_granted(self.sample_rate, self.quantum, rate, quantum)
        if problems:
            # Read again next time: the answer may have been wrong for a reason
            # that has since been fixed (the environment, another client).
            self._granted = None
        client = self._open(jack)
        self._client = client
        try:
            ports = [client.inports.register(column.port) for column in columns]
            available = [p.name for p in client.get_ports(is_output=True, is_audio=True)]
            problems += missing_sources(columns, available)
            if problems:
                raise CaptureError(problems)
            pump = BlockPump(ports, push, quantum)
            self._pump = pump
            client.set_process_callback(pump)
            client.set_xrun_callback(pump.xrun)
            client.set_blocksize_callback(pump.blocksize)
            client.set_shutdown_callback(self._on_shutdown)
            client.activate()
            for column, port in zip(columns, ports):
                client.connect(column.source, port)
            problems = settle_links(
                lambda: [[p.name for p in client.get_all_connections(port)] for port in ports],
                columns, client=client.name, wait_s=self.link_retry_s,
            )
            if problems:
                raise CaptureError(problems)
        except BaseException:
            self._teardown()
            raise

    def stop(self) -> dict[str, Any]:
        """Stop calling `push`, and return the account of what was seen.

        Safe to call twice: the second returns the same account. Raises
        nothing for a server that has already gone; that is in the account.
        """
        self._teardown()
        return dict(self._account)

    def _open(self, jack):
        """A client, or `CaptureError` saying there is no server to join."""
        try:
            return jack.Client(self.client_name, no_start_server=True)
        except jack.JackError as exc:
            raise CaptureError([
                f"no JACK or PipeWire server to connect to ({exc}); run under pw-jack"
            ]) from exc

    def _read_granted(self, client) -> tuple[int, int]:
        """The rate and block size the graph runs at, read the only way they
        can be: activate with no process callback, let the block size settle,
        read, deactivate. `client.blocksize` before activation is the server's
        default (1024 on the rig), and PIPEWIRE_QUANTUM reaches a node only once
        it starts. Read early, the spike sized its buffers for 1024 on a run at
        256, skipped every real block, and recorded 20 s of silence with every
        counter at zero. The wait is the settling: the block size was seen to go
        256, 1024, 256 as the client started, and there is no event to wait on
        before a process callback exists."""
        client.activate()
        try:
            time.sleep(self.settle_s)
            return client.samplerate, client.blocksize
        finally:
            client.deactivate()

    def _on_shutdown(self, status, reason) -> None:
        # JACK calls this from its own thread when the server goes away.
        self._shutdown = f"the JACK/PipeWire server shut the client down: {reason}"

    def _teardown(self) -> None:
        client, pump = self._client, self._pump
        self._client = self._pump = None
        if client is not None:
            for close in (client.deactivate, client.close):
                try:
                    close()
                except Exception:  # noqa: BLE001 — a dead server must not stop the account
                    pass
        if pump is not None:
            self._account = pump.account(self.sample_rate)
            if self._shutdown and not self._account["error"]:
                self._account["error"] = self._shutdown
