"""Glue: audio chunks -> streaming transcription updates, with latency."""

from __future__ import annotations

import time
from collections.abc import Iterator
from dataclasses import dataclass

import numpy as np

from .asr.streaming import StreamingTranscriber, Update
from .audio.sources import ChunkPump


@dataclass
class TimedUpdate:
    update: Update
    # Wall-clock delay between a word being spoken and it being committed.
    committed_lag_s: list[float]
    # Delay between the newest word on screen (tentative or not) and now:
    # what the viewer actually perceives.
    display_lag_s: float | None


def transcribe_stream(
    chunks: Iterator[np.ndarray], transcriber: StreamingTranscriber, step_s: float = 1.0
) -> Iterator[TimedUpdate]:
    pump = ChunkPump(chunks)
    while True:
        audio, finished = pump.take(step_s)
        if audio.size:
            transcriber.add_audio(audio)
        update = transcriber.finish() if finished else transcriber.process()
        yield _timed(update, pump.first_chunk_at)
        if finished:
            if pump.error is not None:
                raise pump.error
            return


def _timed(update: Update, stream_started_at: float | None) -> TimedUpdate:
    if stream_started_at is None:
        return TimedUpdate(update, [], None)
    now = time.monotonic()
    # Sources deliver in real time, so stream time t was "live" at start + t.
    lags = [now - (stream_started_at + w.end) for w in update.committed]
    newest = (update.tentative or update.committed or [None])[-1]
    display = now - (stream_started_at + newest.end) if newest else None
    return TimedUpdate(update, lags, display)
