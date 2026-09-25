"""Sliding-window streaming on top of any whole-buffer transcriber."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Protocol

import numpy as np

from ..audio.capture import SAMPLE_RATE
from .agreement import LocalAgreement, Word

SENTENCE_END = (".", "?", "!", "。", "？", "！")


class Transcriber(Protocol):
    def transcribe(self, audio: np.ndarray, prompt: str) -> list[Word]:
        """Words with timestamps relative to the start of `audio`."""
        ...


@dataclass
class Update:
    committed: list[Word]  # newly committed this pass
    tentative: list[Word]  # current unstable tail (replaces the previous one)
    compute_s: float = 0.0
    buffer_s: float = 0.0


class StreamingTranscriber:
    """Owns the audio buffer: grows it, re-transcribes it, trims it.

    `max_buffer_s` bounds the cost of each pass. When exceeded, the buffer is
    cut at the end of the last committed sentence (or, failing that, the last
    committed word) so Whisper never restarts mid-word.
    """

    def __init__(self, engine: Transcriber, max_buffer_s: float = 15.0, prompt_chars: int = 200):
        self.engine = engine
        self.max_buffer_s = max_buffer_s
        self.prompt_chars = prompt_chars
        self.agreement = LocalAgreement()
        self._buffer = np.zeros(0, dtype=np.float32)
        self._offset = 0.0  # stream time of _buffer[0]

    @property
    def buffer_s(self) -> float:
        return self._buffer.size / SAMPLE_RATE

    @property
    def audio_end(self) -> float:
        """Stream time of the newest sample received."""
        return self._offset + self.buffer_s

    def add_audio(self, chunk: np.ndarray) -> None:
        self._buffer = np.concatenate([self._buffer, chunk])

    def process(self) -> Update:
        started = time.perf_counter()
        words = self.engine.transcribe(self._buffer, self._prompt())
        compute_s = time.perf_counter() - started

        absolute = [Word(w.start + self._offset, w.end + self._offset, w.text) for w in words]
        committed = self.agreement.update(absolute)
        buffer_s = self.buffer_s
        if not absolute:
            self._trim_silence()
        elif self.buffer_s > self.max_buffer_s:
            self._trim_at_commit()
        return Update(committed, self.agreement.tentative, compute_s, buffer_s)

    def finish(self) -> Update:
        return Update(self.agreement.flush(), [])

    def _prompt(self) -> str:
        # Context = committed text whose audio is no longer in the buffer;
        # words still in the buffer will be transcribed again anyway.
        before = [w.text for w in self.agreement.committed if w.end <= self._offset]
        return "".join(before)[-self.prompt_chars :].strip()

    def _trim_silence(self) -> None:
        # No speech in the whole buffer: keep only the last second, in case
        # a word is just starting.
        self._cut_to(max(self._offset, self.audio_end - 1.0))

    def _trim_at_commit(self) -> None:
        in_buffer = [w for w in self.agreement.committed if w.end > self._offset]
        if not in_buffer:
            # Nothing ever agreed on (music, noise, crosstalk): drop the older half.
            self._cut_to(self.audio_end - self.max_buffer_s / 2)
            return
        sentence_ends = [w for w in in_buffer if w.text.rstrip().endswith(SENTENCE_END)]
        self._cut_to((sentence_ends or in_buffer)[-1].end)

    def _cut_to(self, t: float) -> None:
        drop = int(round((t - self._offset) * SAMPLE_RATE))
        if drop <= 0:
            return
        self._buffer = self._buffer[drop:]
        self._offset += drop / SAMPLE_RATE
