"""faster-whisper wrapper implementing the streaming `Transcriber` protocol."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from .agreement import Word
from .gpu import cuda_available

# Models fetched manually (see README) live here; anything else is passed to
# faster-whisper, which downloads it from the Hugging Face Hub.
LOCAL_MODELS = Path.home() / ".cache" / "live-caption" / "models"

# Language detection must be this confident before we lock it in.
LANGUAGE_LOCK_PROB = 0.8
VAD_MIN_SILENCE_MS = 500


class WhisperEngine:
    def __init__(
        self,
        model: str = "small",
        device: str = "auto",
        compute_type: str | None = None,
        language: str | None = None,
        beam_size: int = 1,
    ):
        from faster_whisper import WhisperModel
        from faster_whisper.vad import VadOptions, get_speech_timestamps

        self._vad_options = VadOptions(min_silence_duration_ms=VAD_MIN_SILENCE_MS)
        self._speech_timestamps = get_speech_timestamps

        if device == "auto":
            device = "cuda" if cuda_available() else "cpu"
        elif device == "cuda" and not cuda_available():
            raise RuntimeError("CUDA requested but no usable GPU/cuBLAS/cuDNN was found")
        self.device = device
        self.compute_type = compute_type or ("int8_float16" if device == "cuda" else "int8")

        local = LOCAL_MODELS / model
        self.model_name = model
        self._model = WhisperModel(str(local) if local.is_dir() else model, device=device, compute_type=self.compute_type)
        # Auto-detection on short windows flips between passes (e.g. pt/es),
        # which breaks agreement; once confident we stop re-detecting.
        self.language = language
        self.beam_size = beam_size

    def transcribe(self, audio: np.ndarray, prompt: str) -> list[Word]:
        # Gate on our own VAD pass: given silence, Whisper doesn't return
        # nothing, it recites the initial_prompt (all words stamped at the
        # same instant), and faster-whisper's vad_filter still runs it.
        if audio.size == 0 or not self._speech_timestamps(audio, self._vad_options):
            return []
        segments, info = self._model.transcribe(
            audio,
            language=self.language,
            initial_prompt=prompt or None,
            beam_size=self.beam_size,
            word_timestamps=True,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": VAD_MIN_SILENCE_MS},
            # No temperature fallback: retries cost up to 6 decodes (5-10 s
            # passes), and sampled output breaks agreement between passes.
            temperature=0.0,
            # We supply context ourselves via the prompt; letting Whisper chain
            # its own output across 30 s windows invites repetition loops.
            condition_on_previous_text=False,
        )
        words = [
            Word(w.start, w.end, w.word)
            for seg in segments
            if not _likely_hallucination(seg)
            for w in (seg.words or [])
        ]
        if self.language is None and words and info.language_probability >= LANGUAGE_LOCK_PROB:
            self.language = info.language
        return words


def _likely_hallucination(seg) -> bool:
    # Whisper invents text ("Thanks for watching!") over music/noise; those
    # segments pair a high no-speech probability with low token confidence.
    return seg.no_speech_prob > 0.6 and seg.avg_logprob < -1.0
