"""The rig.yaml model.

`RigSpec.load` reads the only hand-authored file in the workflow. `host`,
`midi`, `osc` and `cv_control` pass through to the emitted config unchanged —
only `tracks` is transformed — so the tool never has to model Sushi's entire
schema, and new Sushi config features work without a tool change (brief §5.1).

`validate()` returns a list of problems rather than raising on the first one:
at rig scale you want every naming collision reported at once, not one fix
cycle per problem.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_VALID_TYPES = {"lv2", "vst2x", "vst3x", "internal"}


@dataclass
class PluginSpec:
    name: str
    uri: str | None = None  # LV2
    path: str | None = None  # VST2 / VST3
    uid: str | None = None  # internal / VST3 sub-plugin id
    type: str = "lv2"

    def to_sushi(self) -> dict[str, Any]:
        entry: dict[str, Any] = {"name": self.name, "type": self.type}
        if self.type == "lv2":
            entry["uri"] = self.uri
        else:
            if self.path:
                entry["path"] = self.path
            if self.uid:
                entry["uid"] = self.uid
        return entry


@dataclass
class TrackSpec:
    name: str
    channels: int = 2
    multibus: bool = False
    buses: int | None = None
    thread: int | None = None
    inputs: list[dict[str, int]] = field(default_factory=list)
    outputs: list[dict[str, int]] = field(default_factory=list)
    plugins: list[PluginSpec] = field(default_factory=list)

    def to_sushi(self) -> dict[str, Any]:
        entry: dict[str, Any] = {"name": self.name}
        if self.multibus:
            entry["multibus"] = True
            entry["buses"] = self.buses if self.buses is not None else 1
            entry["channels"] = self.channels
        else:
            entry["channels"] = self.channels
        if self.thread is not None:
            entry["thread"] = self.thread
        entry["inputs"] = self.inputs
        entry["outputs"] = self.outputs
        entry["plugins"] = [p.to_sushi() for p in self.plugins]
        return entry


@dataclass
class RigSpec:
    host: dict[str, Any] = field(default_factory=dict)
    tracks: list[TrackSpec] = field(default_factory=list)
    midi: dict[str, Any] = field(default_factory=dict)
    osc: dict[str, Any] = field(default_factory=dict)
    cv_control: dict[str, Any] = field(default_factory=dict)
    meta: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> "RigSpec":
        import yaml

        raw = yaml.safe_load(Path(path).read_text()) or {}
        tracks = []
        for t in raw.get("tracks", []):
            t = dict(t)
            plugins = [PluginSpec(**p) for p in t.pop("plugins", [])]
            tracks.append(TrackSpec(plugins=plugins, **t))
        return cls(
            host=raw.get("host", {}),
            tracks=tracks,
            midi=raw.get("midi", {}),
            osc=raw.get("osc", {}),
            cv_control=raw.get("cv_control", {}),
            meta=raw.get("meta", {}),
        )

    def plugin_names(self) -> list[str]:
        return [p.name for t in self.tracks for p in t.plugins]

    def validate(self) -> list[str]:
        """Return every problem found, rather than raising on the first."""
        problems: list[str] = []
        seen: dict[str, str] = {}

        for track in self.tracks:
            if track.name in seen:
                problems.append(
                    f"duplicate name {track.name!r} "
                    f"(already used as a {seen[track.name]})"
                )
            seen[track.name] = "track"

            if track.multibus:
                if track.buses is None:
                    problems.append(f"track {track.name!r}: multibus requires 'buses'")
                if track.channels % 2 != 0:
                    problems.append(
                        f"track {track.name!r}: multibus requires an even 'channels' "
                        f"(got {track.channels})"
                    )

            for entry, direction in [(i, "inputs") for i in track.inputs] + [
                (o, "outputs") for o in track.outputs
            ]:
                has_bus = "engine_bus" in entry or "track_bus" in entry
                has_channel = "engine_channel" in entry or "track_channel" in entry
                if has_bus and has_channel:
                    problems.append(
                        f"track {track.name!r}: {direction} entry {entry} mixes bus and "
                        "channel routing forms — use one or the other"
                    )
                elif not has_bus and not has_channel:
                    problems.append(
                        f"track {track.name!r}: {direction} entry {entry} has neither a "
                        "bus nor a channel form"
                    )

            for plugin in track.plugins:
                if plugin.name in seen:
                    problems.append(
                        f"duplicate name {plugin.name!r} "
                        f"(already used as a {seen[plugin.name]})"
                    )
                seen[plugin.name] = "plugin"

                if plugin.type not in _VALID_TYPES:
                    problems.append(
                        f"plugin {plugin.name!r}: unknown type {plugin.type!r} "
                        f"(expected one of {sorted(_VALID_TYPES)})"
                    )
                elif plugin.type == "lv2" and not plugin.uri:
                    problems.append(f"plugin {plugin.name!r}: lv2 type requires 'uri'")
                elif plugin.type in ("vst2x", "vst3x") and not (plugin.path or plugin.uid):
                    problems.append(
                        f"plugin {plugin.name!r}: {plugin.type} type requires 'path' or 'uid'"
                    )

        return problems
