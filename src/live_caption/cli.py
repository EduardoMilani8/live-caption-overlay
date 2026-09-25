"""Command-line entry points, one subcommand per pipeline stage.

    python -m live_caption list
    python -m live_caption record firefox --seconds 10 --out /tmp/test.wav
    python -m live_caption transcribe            # pick a tab, live captions
    python -m live_caption transcribe --file talk.wav
    python -m live_caption bench speech.wav
"""

from __future__ import annotations

import argparse
import shutil
import sys
import time
import wave

import numpy as np

from .audio.capture import SAMPLE_RATE, StreamCapture, StreamGone, rms_dbfs
from .audio.streams import AudioStream, list_output_streams, pick_stream

GRAY, RESET, CLEAR_LINE = "\033[90m", "\033[0m", "\r\033[K"


def cmd_list(_args) -> int:
    streams = list_output_streams()
    if not streams:
        print("No application is playing audio right now.")
        return 1
    print(f"{'SERIAL':>7}  {'STATE':<9} {'APP':<18} MEDIA")
    for s in streams:
        state = "playing" if s.is_playing else ("paused" if s.corked else s.state)
        print(f"{s.serial:>7}  {state:<9} {s.app_name[:18]:<18} {s.media_name}")
    return 0


def choose_stream_interactively() -> AudioStream | None:
    """Ask which tab/app to caption. Only sources producing sound are listed,
    so the user may need to press play first — Enter refreshes the list."""
    while True:
        streams = list_output_streams()
        if not streams:
            print("Nothing is playing. Start the video, then press Enter (q to quit).")
        else:
            print("\nWhich tab/app should be captioned?")
            for i, s in enumerate(streams, 1):
                status = "" if s.is_playing else "  (paused)"
                title = s.media_name or "(no title)"
                print(f"  [{i}] {title}  — {s.app_name}{status}")
            print("Number to choose, Enter to refresh, q to quit.")
        answer = input("> ").strip().lower()
        if answer == "q":
            return None
        if answer.isdigit() and 1 <= int(answer) <= len(streams):
            return streams[int(answer) - 1]


def _meter(db: float, width: int = 40) -> str:
    filled = int(np.clip((db + 60) / 60, 0, 1) * width)  # -60 dBFS .. 0 dBFS
    return "█" * filled + "·" * (width - filled)


def _resolve_stream(query: str | None) -> AudioStream | None:
    if not query:
        return choose_stream_interactively()
    stream = pick_stream(list_output_streams(), query)
    if stream is None:
        print(f"No stream matches {query!r}. Try `list`.", file=sys.stderr)
    return stream


def cmd_record(args) -> int:
    stream = _resolve_stream(args.query)
    if stream is None:
        return 1
    print(f"Capturing serial {stream.serial}: {stream.label()}  (Ctrl+C to stop)")

    frames: list[np.ndarray] = []
    started = time.monotonic()
    try:
        with StreamCapture(stream.serial) as cap:
            for chunk in cap.chunks():
                frames.append(chunk)
                elapsed = time.monotonic() - started
                db = rms_dbfs(chunk)
                print(f"\r{elapsed:5.1f}s {db:6.1f} dBFS {_meter(db)}", end="", flush=True)
                if args.seconds and elapsed >= args.seconds:
                    break
    except KeyboardInterrupt:
        pass
    except StreamGone:
        print("\nStream ended (app paused/closed).", end="")
    print()

    audio = np.concatenate(frames) if frames else np.zeros(0, np.float32)
    captured = audio.size / SAMPLE_RATE
    print(f"Captured {captured:.2f}s of audio, overall {rms_dbfs(audio):.1f} dBFS")
    if args.out:
        with wave.open(args.out, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(SAMPLE_RATE)
            w.writeframes((np.clip(audio, -1, 1) * 32767).astype("<i2").tobytes())
        print(f"Wrote {args.out}")
    return 0


class CaptionPrinter:
    """Committed text in white, tentative tail in gray, one line per sentence."""

    def __init__(self) -> None:
        self.line = ""

    def show(self, committed, tentative) -> None:
        from .asr.streaming import SENTENCE_END

        for w in committed:
            self.line += w.text
            if w.text.rstrip().endswith(SENTENCE_END):
                print(CLEAR_LINE + self.line.strip(), flush=True)
                self.line = ""
        pending = "".join(w.text for w in tentative)
        width = shutil.get_terminal_size().columns - 1
        visible = (self.line + pending)[-width:]
        split = max(0, len(visible) - len(pending))
        print(CLEAR_LINE + visible[:split] + GRAY + visible[split:] + RESET, end="", flush=True)

    def close(self) -> None:
        if self.line.strip():
            print(CLEAR_LINE + self.line.strip())
        else:
            print(CLEAR_LINE, end="")


def _pct(values: list[float], q: float) -> float:
    return float(np.percentile(values, q)) if values else float("nan")


def _make_engine(args):
    from .asr.whisper_engine import WhisperEngine

    print(f"Loading Whisper {args.model!r}...", file=sys.stderr)
    engine = WhisperEngine(args.model, device=args.device, language=args.lang, beam_size=args.beam)
    print(f"Model on {engine.device} ({engine.compute_type})", file=sys.stderr)
    return engine


def cmd_transcribe(args) -> int:
    from .asr.streaming import StreamingTranscriber
    from .audio.sources import file_chunks
    from .pipeline import transcribe_stream

    stream = None
    if not args.file:
        stream = _resolve_stream(args.query)
        if stream is None:
            return 1
    # Load the model before capturing: otherwise audio piles up in the pipe
    # during loading, the captions start with a stale burst, and the latency
    # clock (anchored at the first chunk) runs late.
    transcriber = StreamingTranscriber(_make_engine(args), max_buffer_s=args.max_buffer)
    if stream is None:
        source_label, chunks = args.file, file_chunks(args.file)
    else:
        capture = StreamCapture(stream.serial).__enter__()
        source_label, chunks = stream.label(), capture.chunks()
    print(f"Transcribing {source_label}  (Ctrl+C to stop)\n", file=sys.stderr)
    printer = CaptionPrinter()
    compute, committed_lag, display_lag = [], [], []
    try:
        for timed in transcribe_stream(chunks, transcriber, step_s=args.step):
            u = timed.update
            printer.show(u.committed, u.tentative)
            if u.compute_s:
                compute.append(u.compute_s)
            committed_lag += timed.committed_lag_s
            if timed.display_lag_s is not None:
                display_lag.append(timed.display_lag_s)
    except KeyboardInterrupt:
        pass
    except StreamGone:
        print("\nStream ended (app paused/closed).", file=sys.stderr)
    finally:
        printer.close()
        if stream is not None:
            capture.__exit__(None, None, None)

    print(
        f"\n{len(compute)} passes | compute p50 {_pct(compute, 50):.2f}s p95 {_pct(compute, 95):.2f}s"
        f" | commit lag p50 {_pct(committed_lag, 50):.2f}s p95 {_pct(committed_lag, 95):.2f}s"
        f" | display lag p50 {_pct(display_lag, 50):.2f}s",
        file=sys.stderr,
    )
    return 0


def cmd_bench(args) -> int:
    """Time single passes over the first N seconds of a file, per model."""
    from faster_whisper import decode_audio

    audio = decode_audio(args.file, sampling_rate=SAMPLE_RATE)[: int(args.seconds * SAMPLE_RATE)]
    for model in args.models.split(","):
        args.model = model
        engine = _make_engine(args)
        engine.transcribe(audio, "")  # warm-up: CUDA kernels, allocator
        times = []
        for _ in range(args.passes):
            t = time.perf_counter()
            text = "".join(w.text for w in engine.transcribe(audio, ""))
            times.append(time.perf_counter() - t)
        print(f"{model:<16} p50 {_pct(times, 50):.2f}s  max {max(times):.2f}s  | {text.strip()[:70]}")
        del engine
    return 0


def _add_model_args(p) -> None:
    p.add_argument("--device", default="auto", choices=["auto", "cuda", "cpu"])
    p.add_argument("--lang", help="source language code (e.g. en); default: detect then lock")
    p.add_argument("--beam", type=int, default=1, help="beam size (1 = greedy, fastest)")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="live-caption")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="list apps currently playing audio").set_defaults(func=cmd_list)
    r = sub.add_parser("record", help="capture one app's audio")
    r.add_argument("query", nargs="?", help="serial or tab/app name substring; omit to choose from a list")
    r.add_argument("--seconds", type=float, default=0, help="stop after N seconds (0 = until Ctrl+C)")
    r.add_argument("--out", help="write captured audio to this .wav file")
    r.set_defaults(func=cmd_record)

    t = sub.add_parser("transcribe", help="live transcription of one app (or a file)")
    t.add_argument("query", nargs="?", help="serial or tab/app name substring; omit to choose from a list")
    t.add_argument("--file", help="replay this audio file in real time instead of capturing")
    t.add_argument("--model", default="small")
    t.add_argument("--step", type=float, default=1.0, help="seconds of new audio per pass")
    t.add_argument("--max-buffer", type=float, default=15.0, help="max seconds re-transcribed per pass")
    _add_model_args(t)
    t.set_defaults(func=cmd_transcribe)

    b = sub.add_parser("bench", help="time Whisper passes on this machine")
    b.add_argument("file", help="speech audio file")
    b.add_argument("--models", default="small,large-v3-turbo")
    b.add_argument("--seconds", type=float, default=10.0)
    b.add_argument("--passes", type=int, default=5)
    _add_model_args(b)
    b.set_defaults(func=cmd_bench)
    args = p.parse_args(argv)
    return args.func(args)
