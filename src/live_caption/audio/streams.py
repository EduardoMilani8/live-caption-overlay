"""Discover application audio streams currently known to PipeWire.

We shell out to `pw-dump` (ships with PipeWire) instead of binding to
libpipewire: the JSON it prints is a stable, documented snapshot of the
graph, and it keeps the project free of native build dependencies.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass

OUTPUT_STREAM_CLASS = "Stream/Output/Audio"


@dataclass(frozen=True)
class AudioStream:
    node_id: int
    serial: int  # preferred capture target: never reused, unlike node_id
    app_name: str
    binary: str
    media_name: str
    pid: int | None
    state: str  # "running", "idle", "suspended", ...
    corked: bool  # paused by the app (pulse clients only)

    @property
    def is_playing(self) -> bool:
        return self.state == "running" and not self.corked

    def label(self) -> str:
        return f"{self.app_name} — {self.media_name}" if self.media_name else self.app_name


def parse_pw_dump(dump: list[dict]) -> list[AudioStream]:
    """Extract app output streams (things *playing* audio) from pw-dump JSON."""
    streams = []
    for obj in dump:
        info = obj.get("info") or {}
        props = info.get("props") or {}
        if props.get("media.class") != OUTPUT_STREAM_CLASS:
            continue
        pid = props.get("application.process.id")
        streams.append(
            AudioStream(
                node_id=obj["id"],
                serial=int(props.get("object.serial", obj["id"])),
                app_name=props.get("application.name") or props.get("node.name", "?"),
                binary=props.get("application.process.binary", ""),
                media_name=props.get("media.name", ""),
                pid=int(pid) if pid is not None else None,
                state=info.get("state", "unknown"),
                corked=bool(props.get("pulse.corked", False)),
            )
        )
    return streams


def list_output_streams() -> list[AudioStream]:
    out = subprocess.run(["pw-dump"], check=True, capture_output=True, text=True).stdout
    return parse_pw_dump(json.loads(out))


def pick_stream(streams: list[AudioStream], query: str) -> AudioStream | None:
    """Choose which stream to capture given a user query.

    `query` may be a serial number ("204") or free text ("firefox", "netflix").
    Returns None when nothing reasonable matches.

    Ranking: a tab-title match beats an app-name match (it's more specific),
    and a playing stream beats a paused one of the same rank.
    """
    query = query.strip()
    if query.isdigit():
        return next((s for s in streams if s.serial == int(query)), None)

    needle = query.casefold()

    def rank(s: AudioStream) -> tuple[int, int] | None:
        if needle in s.media_name.casefold():
            match = 0
        elif needle in s.app_name.casefold() or needle in s.binary.casefold():
            match = 1
        else:
            return None
        return (match, 0 if s.is_playing else 1)

    ranked = [(r, s) for s in streams if (r := rank(s)) is not None]
    # min() keeps the first of equal ranks, i.e. pw-dump order.
    return min(ranked, key=lambda rs: rs[0])[1] if ranked else None
