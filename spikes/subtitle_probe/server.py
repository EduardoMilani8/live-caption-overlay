"""Receiver for the Live Caption Probe extension.

Appends every event to a JSONL log and prints a readable live view. Lines
typed into this terminal are logged as markers, so a test run can be split
into scenarios ("visible", "behind vscode", "minimized", ...) afterwards:

    .venv/bin/python spikes/subtitle_probe/server.py --out probe.jsonl
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

GRAY, YELLOW, GREEN, RESET = "\033[90m", "\033[33m", "\033[32m", "\033[0m"
TIMER_GAP_WARN_MS = 2500  # heartbeat is 1 s
DELIVERY_WARN_MS = 500


class Log:
    def __init__(self, path: str) -> None:
        self._file = open(path, "a", encoding="utf-8")
        self._lock = threading.Lock()
        self._last_sample: dict[object, int] = {}
        self._state: dict[object, tuple] = {}
        self._clock: dict[object, tuple] = {}

    def write(self, event: dict) -> None:
        with self._lock:
            self._file.write(json.dumps(event, ensure_ascii=False) + "\n")
            self._file.flush()
            self._show(event)

    def _show(self, ev: dict) -> None:
        clock = time.strftime("%H:%M:%S", time.localtime(ev["t"] / 1000))
        kind = ev.get("type")
        if kind == "mark":
            print(f"{GREEN}{clock} ==== {ev['note']} ===={RESET}")
            return

        tab = ev.get("tab")
        state = (ev.get("vis"), ev.get("focus"))
        if "vis" in ev and self._state.get(tab) != state:
            self._state[tab] = state
            print(f"{YELLOW}{clock} tab {tab}: visibility={state[0]} focus={state[1]}{RESET}")

        delivery = ev["rt"] - ev["t"]
        if delivery > DELIVERY_WARN_MS:
            print(f"{YELLOW}{clock} slow delivery: {delivery} ms{RESET}")

        vt = f"{ev['vt']:8.2f}" if ev.get("vt") is not None else "     n/a"
        if kind == "cue":
            if ev["text"]:
                print(f"{clock} vt={vt}  {ev['text'].replace(chr(10), ' / ')}")
        elif kind == "sample":
            prev = self._last_sample.get(tab)
            self._last_sample[tab] = ev["t"]
            if prev is not None and ev["t"] - prev > TIMER_GAP_WARN_MS:
                print(f"{YELLOW}{clock} timer gap {(ev['t'] - prev) / 1000:.1f} s{RESET}")
            if not ev.get("capEl") and not ev.get("paused"):
                print(f"{GRAY}{clock} no subtitle element on page (subtitles off?){RESET}")
        elif kind == "track":
            about = f" lang={ev['lang']} movie={ev['movieId']}" if "lang" in ev else ""
            print(f"{GREEN}{clock} captured {ev['kind']} track, {ev['size']} chars{about}{RESET}")
        elif kind == "anchor":  # from the app's extension (extension/ at the repo root)
            state = (ev.get("ad"), ev.get("clock"), ev.get("movieId"))
            if ev["reason"] != "timeupdate" or self._clock.get(tab) != state:
                self._clock[tab] = state
                flags = " ".join(f for f, on in (("PAUSED", ev["paused"]), ("AD", ev["ad"])) if on)
                print(f"{GRAY}{clock} anchor {ev['reason']:<14} vt={vt} rate={ev['rate']} "
                      f"clock={ev['clock']} movie={ev['movieId']} {flags}{RESET}")
        elif kind == "media":
            if ev["what"] in ("play", "pause", "seeked", "ratechange"):
                print(f"{GRAY}{clock} vt={vt}  [{ev['what']}]{RESET}")
            else:
                print(f"{GRAY}{clock} video #{ev.get('idx')} {ev['what']} "
                      f"t={ev.get('evt')} dur={ev.get('dur')}{RESET}")
        elif kind == "uia":
            for name, text in ev["added"].items():
                print(f"{GRAY}{clock} ui + {name}{'  ' + repr(text) if text else ''}{RESET}")
            for name in ev["removed"]:
                print(f"{GRAY}{clock} ui - {name}{RESET}")
        elif kind == "napi" and "methods" in ev:
            print(f"{GREEN}{clock} player API: {len(ev['methods'])} methods, "
                  f"{len(ev['players'][0]) - 1} time/ad getters{RESET}")
        elif kind == "hello":
            print(f"{GRAY}{clock} probe loaded in tab {tab} ({ev['site']}{ev['path']}){RESET}")


def make_handler(log: Log):
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            origin = self.headers.get("Origin", "")
            # Only the extension may post; any web page could otherwise reach localhost.
            if self.path != "/event" or (origin and not origin.startswith("moz-extension://")):
                self.send_error(403)
                return
            length = int(self.headers.get("Content-Length", 0))
            event = json.loads(self.rfile.read(length))
            event["rt"] = int(time.time() * 1000)
            log.write(event)
            self.send_response(204)
            self.end_headers()

        def log_message(self, *args) -> None:  # silence per-request access log
            pass

    return Handler


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--out", default="probe.jsonl")
    args = parser.parse_args()

    log = Log(args.out)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(log))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f"Listening on 127.0.0.1:{args.port}, logging to {args.out}")
    print("Type a note + Enter to mark a scenario change; Ctrl+C to stop.\n")
    try:
        for line in sys.stdin:
            if note := line.strip():
                now = int(time.time() * 1000)
                log.write({"type": "mark", "note": note, "t": now, "rt": now})
    except KeyboardInterrupt:
        pass
    server.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
