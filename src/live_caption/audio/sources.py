"""Audio sources and a background pump that decouples capture from ASR."""

from __future__ import annotations

import queue
import threading
import time
from collections.abc import Iterator

import numpy as np

from .capture import SAMPLE_RATE


def file_chunks(path: str, chunk_ms: int = 100, realtime: bool = True) -> Iterator[np.ndarray]:
    """Replay an audio file as if it were a live stream (repeatable tests)."""
    from faster_whisper import decode_audio  # any format -> 16 kHz mono float32

    audio = decode_audio(path, sampling_rate=SAMPLE_RATE)
    step = SAMPLE_RATE * chunk_ms // 1000
    started = time.monotonic()
    for i in range(0, audio.size, step):
        if realtime:
            # Schedule against the start time so sleep jitter doesn't accumulate.
            delay = started + i / SAMPLE_RATE - time.monotonic()
            if delay > 0:
                time.sleep(delay)
        yield audio[i : i + step]


class ChunkPump:
    """Drains a chunk iterator on a background thread.

    Transcription blocks for ~1 s per pass; if nobody reads pw-record's pipe
    meanwhile, the 64 KiB pipe buffer (~2 s of audio) fills and capture
    stalls. The pump keeps reading and hands over everything accumulated.
    """

    _DONE = object()

    def __init__(self, chunks: Iterator[np.ndarray]):
        self._queue: queue.Queue = queue.Queue()
        self.error: BaseException | None = None
        self.first_chunk_at: float | None = None  # monotonic time, for latency
        self._thread = threading.Thread(target=self._run, args=(chunks,), daemon=True)
        self._thread.start()

    def _run(self, chunks: Iterator[np.ndarray]) -> None:
        try:
            for chunk in chunks:
                if self.first_chunk_at is None:
                    self.first_chunk_at = time.monotonic() - chunk.size / SAMPLE_RATE
                self._queue.put(chunk)
        except BaseException as e:  # surfaced to the consumer in take()
            self.error = e
        finally:
            self._queue.put(self._DONE)

    def take(self, min_s: float) -> tuple[np.ndarray, bool]:
        """Block until at least `min_s` of audio (or the end) is available.

        Returns (audio, finished). If the consumer fell behind, returns all
        the backlog at once, so slow passes naturally process bigger steps.
        """
        parts, total, finished = [], 0, False
        need = int(min_s * SAMPLE_RATE)
        while total < need or not self._queue.empty():
            item = self._queue.get()
            if item is self._DONE:
                finished = True
                break
            parts.append(item)
            total += item.size
        audio = np.concatenate(parts) if parts else np.zeros(0, np.float32)
        return audio, finished
