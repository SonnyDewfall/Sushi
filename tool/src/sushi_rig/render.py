"""Render a config through Sushi offline, so its sound can be fingerprinted.

The effect half of #40. `nulltest.py` holds the decisions — what to measure and
what counts as a change — and this gets the audio for it to measure.

Sushi has an offline frontend: `-o -i in.wav -O out.wav -c config.json` loads the
config, pushes a file through the graph and exits. No audio device, no JACK, no
PipeWire. So a null test runs anywhere the rig's plugins and Sushi exist, which
means the CI container, which means a change in the rig's sound can fail a build.

## The stimulus is described, not stored

Nothing here commits a WAV. `signals.log_sweep` is deterministic, so a reference
records *what was fed in* as a short string and the renderer regenerates it
identically. That keeps the repository free of binaries, and it keeps the
comparison honest: `nulltest.compare` refuses two fingerprints whose stimulus
strings differ, so changing the stimulus invalidates every reference loudly
instead of silently comparing a sweep against a sine.

The string is for identity, never for parsing. `Stimulus` holds the parameters
structurally and derives the string from them; nothing reads it back.

## Why a sweep

It covers the band in one pass, and it fails *diagnostically*: a reference that
no longer matches tells you which band moved, because a sweep put energy in all
of them. A sine would answer "different" for one frequency and nothing about the
rest.

A sine is available all the same, because the one question a sweep cannot answer
is whether a component is adding harmonics: distortion products have to be read
somewhere the stimulus itself put no energy, and a sweep puts energy everywhere.
That is what `selftest` asks of an amp (#49).
"""

from __future__ import annotations

import copy
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import signals
from .save import NAME_PATTERN
from .verify import sushi_env

# Long enough for the lowest band to have cycles to measure, short enough that
# seven configs are a few seconds of CI rather than a minute. Not tuned against
# anything -- if a band turns out to be unreliable at this length, lengthening
# it is the fix, and it invalidates existing references on purpose.
DEFAULT_DURATION_S = 5.0

# Well below clipping, so a chain with gain in it still has somewhere to go.
# A render that clips measures the clipping, not the chain.
DEFAULT_LEVEL_DBFS = -20.0


class RenderError(Exception):
    """Something about the render the user needs to fix."""


@dataclass(frozen=True)
class Stimulus:
    """What gets pushed through the graph.

    `channels` is two because that is all Sushi's offline frontend has
    (`OFFLINE_CHANNELS`). Wider is refused by `render_offline`, because Sushi
    does not refuse it: it overflows a stack buffer instead.
    """

    kind: str = "log_sweep"
    duration_s: float = DEFAULT_DURATION_S
    level_dbfs: float = DEFAULT_LEVEL_DBFS
    f_start_hz: float = 20.0
    f_end_hz: float = 20000.0
    channels: int = 2
    # Only a `sine` uses this, and a sine uses neither of the two above. Each
    # kind therefore describes itself with its own fields and no others, so a
    # field that cannot change the samples cannot change the identity either —
    # and the identity of an existing `log_sweep` reference is untouched by this
    # one being added.
    frequency_hz: float | None = None

    def describe(self) -> str:
        """The identity a reference records. Compared, never parsed."""
        if self.kind == "sine":
            if self.frequency_hz is None:
                # `render()` refuses this too. Both have to: a reference's
                # identity is sometimes asked for without rendering anything,
                # and formatting None with :g raises a TypeError that says
                # nothing about stimuli.
                raise RenderError("a sine stimulus needs frequency_hz")
            return (
                f"sine {self.frequency_hz:g}Hz {self.duration_s:g}s "
                f"{self.level_dbfs:g}dBFS {self.channels}ch"
            )
        return (
            f"{self.kind} {self.f_start_hz:g}-{self.f_end_hz:g}Hz "
            f"{self.duration_s:g}s {self.level_dbfs:g}dBFS {self.channels}ch"
        )

    def render(self, sample_rate: int):
        """The samples themselves, regenerated from the parameters."""
        if self.kind == "sine":
            if self.frequency_hz is None:
                raise RenderError("a sine stimulus needs frequency_hz")
            return signals.sine(
                sample_rate,
                self.duration_s,
                self.frequency_hz,
                level_dbfs=self.level_dbfs,
                channels=self.channels,
            )
        if self.kind != "log_sweep":
            raise RenderError(
                f"unknown stimulus kind {self.kind!r}; this tool generates "
                f"log_sweep and sine"
            )
        return signals.log_sweep(
            sample_rate,
            self.duration_s,
            f_start_hz=self.f_start_hz,
            f_end_hz=self.f_end_hz,
            level_dbfs=self.level_dbfs,
            channels=self.channels,
        )


# Sushi 1.3.0's offline frontend has exactly two audio channels, fixed at
# compile time: `set_audio_channels(OFFLINE_FRONTEND_CHANNELS, ...)` with
# `OFFLINE_FRONTEND_CHANNELS = 2` (src/audio_frontends/offline_frontend.h). The
# dummy frontend `verify` uses has ten. Read from the source rather than
# inferred, after CI showed the two disagreeing about the same config.
OFFLINE_CHANNELS = 2


def offline_problem(config: dict[str, Any]) -> str | None:
    """Why Sushi's offline frontend cannot host `config` at all, or None.

    Two channels is one stereo engine bus, bus 0. A track on any other bus names
    channels the offline engine does not have, and Sushi refuses the whole
    config with "Failed to load tracks from the Json config file" — the same
    message an unloadable plugin gives, which is why this is decided from the
    config rather than read off the error.

    `guitar_vocal` is the case that found it: its vocal track listens on bus 1,
    so no stimulus of any width can render it offline as a whole. `channels_of`
    renders it one track at a time instead, and this is what is left over: a
    single track that needs more than two channels by itself.
    """
    # Both routing forms the rig accepts (spec.py, patchbay.routes_from_rig):
    # `engine_bus: n` is channels 2n and 2n+1, `engine_channel: n` is channel n.
    # Checking only the bus form let a track on `engine_channel: 2` pass as
    # renderable, and Sushi then refused it with the plugin-shaped error.
    for track in config.get("tracks", []):
        if not isinstance(track, dict):
            continue
        for side in ("inputs", "outputs"):
            entries = track.get(side)
            if not isinstance(entries, list):
                continue
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                for key, width in (("engine_bus", 2), ("engine_channel", 1)):
                    value = entry.get(key)
                    # A malformed index is verify's to refuse — it runs first in
                    # CI — and the rule for one is patchbay._index_problem's:
                    # a non-negative int, with bool excluded.
                    if type(value) is not int or value < 0:
                        continue
                    if value * width + width > OFFLINE_CHANNELS:
                        return (
                            f"track {track.get('name')!r} uses "
                            f"{key.replace('_', ' ')} {value} for its "
                            f"{side}, and Sushi's offline frontend has "
                            f"{OFFLINE_CHANNELS} channels — bus 0 only — so it "
                            f"cannot load this config"
                        )
    return None


@dataclass(frozen=True)
class Channel:
    """One thing the null test renders and keeps a reference for.

    `name` is the reference's file stem: the config's own stem for a
    single-track config, so every reference written before channels existed
    still applies, and `<config>.<track>` for each track of a multi-track one.
    `whole` is true when `config` is the config file itself, unchanged, which
    is then rendered from its own path rather than from a copy.
    """

    name: str
    label: str
    config: dict
    whole: bool


def channels_of(config: dict[str, Any], path: Path) -> list[Channel]:
    """What to render for `config`: itself if it has one track, each track alone
    if it has several.

    Sushi's offline frontend has one stereo bus, so a rig of several channels
    cannot be rendered as a whole — `guitar_vocal`'s vocal track is on bus 1.
    Rendered one channel at a time, each on bus 0, any number of channels can
    be, and a failure then says *which* chain's sound moved.

    That is faithful for these configs because the only thing their tracks do
    to each other is sum at the output bus, and a sum of unchanged channels is
    unchanged. What it does not cover is a change in the summing itself — a
    track sent to a different output — which `verify` checks as routing, and
    any future cross-track processing such as sends, which would need the mix
    rendered as a whole.

    Raises ValueError for a track whose name cannot be a file name, since the
    name becomes its reference's.
    """
    tracks = [t for t in config.get("tracks", []) if isinstance(t, dict)]
    if len(tracks) <= 1:
        if not tracks:
            return [Channel(path.stem, path.name, config, True)]
        moved = _on_bus_zero(tracks[0])
        if moved == tracks[0]:
            return [Channel(path.stem, path.name, config, True)]
        alone = copy.deepcopy(config)
        alone["tracks"] = [moved]
        return [Channel(path.stem, path.name, alone, False)]

    channels = []
    for track in tracks:
        name = track.get("name")
        if not isinstance(name, str) or not NAME_PATTERN.fullmatch(name):
            raise ValueError(
                f"track name {name!r} in {path.name} cannot name a reference file: "
                f"it must match {NAME_PATTERN.pattern}"
            )
        channels.append(Channel(
            f"{path.stem}.{name}", f"{path.name} [{name}]", _alone(config, track), False
        ))
    return channels


def _on_bus_zero(track: dict[str, Any]) -> dict[str, Any]:
    """`track` with its engine indices moved down to start at 0, order kept.

    Inputs and outputs separately, and each routing form separately: bus 1
    becomes bus 0, channels 2 and 3 become 0 and 1. A track that needs more
    than the offline frontend's two channels still needs them afterwards, and
    `offline_problem` says so.
    """
    moved = copy.deepcopy(track)
    for side in ("inputs", "outputs"):
        entries = moved.get(side)
        if not isinstance(entries, list):
            continue
        for key in ("engine_bus", "engine_channel"):
            used = sorted({
                e[key] for e in entries
                if isinstance(e, dict) and type(e.get(key)) is int and e[key] >= 0
            })
            for entry in entries:
                if isinstance(entry, dict) and entry.get(key) in used and type(entry.get(key)) is int:
                    entry[key] = used.index(entry[key])
    return moved


def _alone(config: dict[str, Any], track: dict[str, Any]) -> dict[str, Any]:
    """A copy of `config` holding `track` alone, on bus 0.

    Everything keyed by processor name is cut down to this track's own: Sushi
    refuses a config whose `initial_state` or MIDI mappings name a processor
    that is not there.
    """
    own = {track.get("name")} | {
        p.get("name") for p in track.get("plugins", []) if isinstance(p, dict)
    }
    alone = copy.deepcopy(config)
    alone["tracks"] = [_on_bus_zero(track)]
    if isinstance(config.get("initial_state"), list):
        alone["initial_state"] = [
            e for e in alone["initial_state"]
            if not isinstance(e, dict) or e.get("processor") in own
        ]
    if isinstance(config.get("midi"), dict):
        alone["midi"] = {
            key: [e for e in value if not _names_another(e, own)]
            if isinstance(value, list) else value
            for key, value in alone["midi"].items()
        }
    return alone


def _names_another(entry: Any, own: set) -> bool:
    """Whether a MIDI entry points at a processor this channel does not have."""
    if not isinstance(entry, dict):
        return False
    return any(
        isinstance(entry.get(key), str) and entry[key] not in own
        for key in ("processor", "track", "plugin", "plugin_name")
    )


def sushi_argv(
    config: Path, stimulus_wav: Path, out_wav: Path, sushi_bin: str = "./sushi"
) -> list[str]:
    """The offline-render command line.

    Pure, so the flags are pinned by a test rather than discovered when a render
    silently produces nothing. `-o` selects the offline frontend; `-i` and `-O`
    are its input and output files. Note the asymmetry in case: that is Sushi's,
    not a typo here.
    """
    return [
        sushi_bin,
        "-o",
        "-i", str(stimulus_wav),
        "-O", str(out_wav),
        "-c", str(config),
    ]


def render_offline(
    config: Path,
    root: Path,
    work: Path,
    *,
    stimulus: Stimulus | None = None,
    sushi_bin: str = "./sushi",
    sample_rate: int = 48000,
    timeout: float = 120.0,
) -> dict[str, Any]:
    """Push `stimulus` through `config` and read back what came out.

    Returns the rendered samples, the stimulus that produced them and its
    description, which is everything `nulltest.fingerprint` needs.

    `LV2_PATH` comes from `verify.sushi_env`, for the reason recorded there: a
    render that inherited the caller's path would pass on a machine whose shell
    exports it and fail with "Failed to load tracks" on a clean one, which is the
    bug that cost a CI cycle in #47.
    """
    stimulus = stimulus or Stimulus()
    if stimulus.channels > OFFLINE_CHANNELS:
        # Not a limitation worth working around: Sushi reads AUDIO_CHUNK_SIZE
        # frames of the file into `float file_buffer[OFFLINE_FRONTEND_CHANNELS *
        # AUDIO_CHUNK_SIZE]` on the stack and checks only for mono, so a wider
        # file overflows it. CI saw "*** stack smashing detected ***" from a
        # four-channel stimulus on a config that uses bus 0 alone.
        raise RenderError(
            f"a {stimulus.channels}-channel stimulus would overflow Sushi's offline "
            f"frontend, which reads every file into a {OFFLINE_CHANNELS}-channel "
            f"buffer; use {OFFLINE_CHANNELS} channels or fewer"
        )
    work.mkdir(parents=True, exist_ok=True)
    in_wav = work / "stimulus.wav"
    out_wav = work / "rendered.wav"

    fed = stimulus.render(sample_rate)
    # float32: the stimulus is below full scale, but a float file cannot clip on
    # write and this is not where a level problem should first appear.
    signals.write_wav(in_wav, fed, sample_rate, subtype="float32")

    try:
        result = subprocess.run(
            sushi_argv(config, in_wav, out_wav, sushi_bin),
            cwd=root,
            env=sushi_env(root),
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise RenderError(
            f"cannot run {sushi_bin!r} from {root}: {exc}. The rig keeps its own "
            "Sushi at ./sushi"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise RenderError(
            f"Sushi did not finish rendering {config.name} within {timeout}s. The "
            "offline frontend should exit on its own once the input is consumed"
        ) from exc

    if result.returncode != 0:
        # Both streams, because the LV2_PATH failure prints on stdout and
        # dump.py reporting only stderr turned it into a blank error once.
        raise RenderError(
            f"Sushi exited {result.returncode} rendering {config.name}:\n"
            f"{(result.stdout + result.stderr).strip()}"
        )

    if not out_wav.exists():
        raise RenderError(
            f"Sushi exited 0 but wrote no {out_wav.name} for {config.name}. "
            "A silent success here would be fingerprinted as a change in sound"
        )

    came_back = signals.read_wav(out_wav)
    if came_back.sample_rate != sample_rate:
        raise RenderError(
            f"rendered {config.name} at {came_back.sample_rate} Hz, expected "
            f"{sample_rate} Hz — a rate change moves every band"
        )

    return {
        "rendered": came_back.samples,
        "sample_rate": came_back.sample_rate,
        "channels": int(came_back.samples.shape[1]),
        "stimulus": stimulus.describe(),
        "stimulus_samples": fed,
    }


# --- the workflow -----------------------------------------------------------
#
# Where a config's sound reference lives. Beside the configs rather than in a
# directory of its own, because a reference describes one tone and should travel
# with it -- including to the library repository when the configs move (#27).
SOUND_DIR = "sound"


def reference_path(root: Path, config: Path) -> Path:
    """The reference for `config`, whether it exists yet or not."""
    return root / "config" / SOUND_DIR / f"{config.stem}.yaml"


def measure(
    config: Path,
    root: Path,
    work: Path,
    *,
    stimulus: Stimulus | None = None,
    sushi_bin: str = "./sushi",
) -> Any:
    """Render `config` and fingerprint the result.

    A render that is not finite is refused here rather than fingerprinted.
    `nulltest.render_findings` exists for exactly this step, and without the
    call a NaN render died inside `estimate_delay` with "no correlation between
    the signals" -- a message about the measurement, naming nothing about the
    chain that produced it. `--calibrate` went the same way, so an unusable
    render could have been written into a reference.
    """
    from .nulltest import fingerprint, render_findings

    out = render_offline(
        config, root, work, stimulus=stimulus, sushi_bin=sushi_bin
    )
    broken = render_findings(out["rendered"], out["sample_rate"])
    if broken:
        raise RenderError(
            f"{config.name} rendered unusable audio: "
            + "; ".join(str(finding) for finding in broken)
        )
    return fingerprint(
        out["rendered"],
        out["sample_rate"],
        config=config.stem,
        stimulus=out["stimulus"],
        stimulus_samples=out["stimulus_samples"],
    )


def calibrate(
    config: Path,
    root: Path,
    work: Path,
    *,
    stimulus: Stimulus | None = None,
    sushi_bin: str = "./sushi",
) -> tuple[Any, Any]:
    """Render `config` twice and measure how far apart two identical runs are.

    The second fingerprint is the one kept, arbitrarily -- if the two differ by
    more than the floor they establish, there is no "right" one of the pair, and
    that is precisely what the recorded tolerance exists to admit.

    Two runs, not more. A third would tighten the estimate a little and treble
    the CI time, and the number this produces is a floor rather than a
    distribution: what matters is that it is not zero when the config is not
    deterministic.
    """
    from .nulltest import floor_between

    first = measure(config, root, work / "run-1", stimulus=stimulus, sushi_bin=sushi_bin)
    second = measure(config, root, work / "run-2", stimulus=stimulus, sushi_bin=sushi_bin)
    return second, floor_between(first, second)
