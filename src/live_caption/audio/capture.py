"""Capture raw PCM from a single PipeWire stream via `pw-record`.

Audio arrives already as 16 kHz mono — PipeWire resamples/downmixes for
us — which is exactly what Whisper expects, so later phases never touch
sample-rate conversion.
"""

from __future__ import annotations

import subprocess
from collections.abc import Iterator

import numpy as np

SAMPLE_RATE = 16_000
BYTES_PER_SAMPLE = 2  # s16le

# Without these, WirePlumber "helpfully" re-routes an orphaned capture
# stream to the default source (your microphone) when the target app's
# stream disappears — e.g. when the video is paused or the tab closes.
_STREAM_PROPS = "{ node.dont-reconnect = true node.dont-fallback = true node.name = live-caption-capture }"


class StreamGone(RuntimeError):
    """The target app stream vanished (paused video, closed tab, app quit)."""


class StreamCapture:
    """Context manager yielding float32 chunks in [-1, 1] from one stream."""

    def __init__(self, target_serial: int, chunk_ms: int = 100):
        self.target_serial = target_serial
        self.chunk_samples = SAMPLE_RATE * chunk_ms // 1000
        self._proc: subprocess.Popen | None = None

    def __enter__(self) -> "StreamCapture":
        self._proc = subprocess.Popen(
            [
                "pw-record",
                "--target", str(self.target_serial),
                "--rate", str(SAMPLE_RATE),
                "--channels", "1",
                "--format", "s16",
                "-P", _STREAM_PROPS,
                "--raw", "-",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        return self

    def __exit__(self, *exc) -> None:
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._proc.kill()

    def chunks(self) -> Iterator[np.ndarray]:
        assert self._proc and self._proc.stdout, "use inside a `with` block"
        nbytes = self.chunk_samples * BYTES_PER_SAMPLE
        while True:
            buf = self._proc.stdout.read(nbytes)
            if not buf:
                err = self._proc.stderr.read().decode(errors="replace").strip() if self._proc.stderr else ""
                if "target not found" in err:
                    raise StreamGone(f"stream {self.target_serial} is gone")
                if err:
                    raise RuntimeError(f"pw-record exited: {err}")
                return
            # A short final read may leave an odd byte; drop it.
            buf = buf[: len(buf) - len(buf) % BYTES_PER_SAMPLE]
            yield np.frombuffer(buf, dtype="<i2").astype(np.float32) / 32768.0


def rms_dbfs(chunk: np.ndarray) -> float:
    rms = float(np.sqrt(np.mean(chunk * chunk))) if chunk.size else 0.0
    return 20 * np.log10(max(rms, 1e-10))
