"""Phase 1 CLI: list app audio streams and record one of them.

    python -m live_caption list
    python -m live_caption record firefox --seconds 10 --out /tmp/test.wav
"""

from __future__ import annotations

import argparse
import sys
import time
import wave

import numpy as np

from .audio.capture import SAMPLE_RATE, StreamCapture, StreamGone, rms_dbfs
from .audio.streams import AudioStream, list_output_streams, pick_stream


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


def cmd_record(args) -> int:
    if args.query:
        stream = pick_stream(list_output_streams(), args.query)
        if stream is None:
            print(f"No stream matches {args.query!r}. Try `list`.", file=sys.stderr)
            return 1
    else:
        stream = choose_stream_interactively()
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


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="live-caption")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="list apps currently playing audio").set_defaults(func=cmd_list)
    r = sub.add_parser("record", help="capture one app's audio")
    r.add_argument("query", nargs="?", help="serial or tab/app name substring; omit to choose from a list")
    r.add_argument("--seconds", type=float, default=0, help="stop after N seconds (0 = until Ctrl+C)")
    r.add_argument("--out", help="write captured audio to this .wav file")
    r.set_defaults(func=cmd_record)
    args = p.parse_args(argv)
    return args.func(args)
