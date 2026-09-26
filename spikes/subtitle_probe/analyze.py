"""Summarize a probe log per scenario (the markers typed into server.py).

For every subtitle seen on screen we know the video time at which it
appeared; the intercepted subtitle file says when it *should* appear. The
difference is the on-screen lag, and cues the file has but the screen never
showed are missed. Both are reported per scenario, which answers the spike's
question: do subtitles keep flowing on time when the tab isn't visible?

    .venv/bin/python spikes/subtitle_probe/analyze.py probe.jsonl
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from live_caption.subs.timedtext import Cue, normalize, parse

SETTLE_MS = 2000  # after play/seek the first cue appears mid-way; don't count its lag
RUN_BREAK_S = 2.0  # video clock vs wall clock mismatch that means a seek/stall


@dataclass
class Segment:
    label: str
    t0: int
    t1: int = 0
    states: Counter = field(default_factory=Counter)
    shown: int = 0
    unmatched: list[str] = field(default_factory=list)
    lags_ms: list[float] = field(default_factory=list)
    timer_gaps_ms: list[int] = field(default_factory=list)
    delivery_ms: list[int] = field(default_factory=list)
    runs: list[list[float]] = field(default_factory=list)  # [vt_start, vt_end]


class TrackIndex:
    def __init__(self, tracks: list[list[Cue]]) -> None:
        self.cues: list[tuple[int, Cue]] = []
        self.by_text: dict[str, list[int]] = defaultdict(list)
        for track_no, cues in enumerate(tracks):
            for cue in cues:
                idx = len(self.cues)
                self.cues.append((track_no, cue))
                self.by_text[normalize(cue.text)].append(idx)
                for line in cue.text.split("\n"):
                    self.by_text[normalize(line)].append(idx)

    def match(self, text: str, vt: float) -> int | None:
        """The newest track cue that the on-screen text belongs to. Two cues
        can be on screen at once, so lines are matched individually."""
        keys = [normalize(text)] + [normalize(line) for line in text.split("\n")]
        candidates = {i for k in keys for i in self.by_text.get(k, ())}
        near = [i for i in candidates if self.cues[i][1].start - 1.0 <= vt <= self.cues[i][1].end + 5.0]
        return max(near, key=lambda i: self.cues[i][1].start, default=None)


def pct(values: list[float], q: float) -> float:
    s = sorted(values)
    return s[min(len(s) - 1, round(q * (len(s) - 1)))]


def load(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        events = [json.loads(line) for line in f if line.strip()]
    return sorted(events, key=lambda e: (e["t"], e.get("seq", 0)))


def analyze(events: list[dict]) -> tuple[list[Segment], TrackIndex, set[int], int]:
    tracks = []
    for ev in events:
        if ev["type"] == "track":
            try:
                tracks.append(parse(ev["kind"], ev["body"]))
            except Exception as e:  # a parse failure is itself a spike finding
                print(f"could not parse {ev['kind']} track: {e}", file=sys.stderr)
    index = TrackIndex(tracks)

    t_first = events[0]["t"] if events else 0
    segments = [Segment("(before first marker)", t_first, t_first)]
    matched: set[int] = set()
    settle_until = 0
    last_sample_t: int | None = None
    last_clock: tuple[int, float] | None = None  # (t, vt) of last playing event

    for ev in events:
        seg = segments[-1]
        seg.t1 = ev["t"]
        kind = ev["type"]
        if kind == "mark":
            segments.append(Segment(ev["note"], ev["t"], ev["t"]))
            last_sample_t = last_clock = None
            continue
        if "vis" in ev:
            seg.states[(ev["vis"], ev["focus"])] += 1
        seg.delivery_ms.append(ev["rt"] - ev["t"])

        if kind == "media":
            if ev["what"] in ("play", "seeked"):
                settle_until = ev["t"] + SETTLE_MS
            last_clock = None  # any play/pause/seek starts a new playback run
            continue
        if kind == "sample":
            if last_sample_t is not None:
                seg.timer_gaps_ms.append(ev["t"] - last_sample_t)
            last_sample_t = ev["t"]

        vt = ev.get("vt")
        if vt is None or ev.get("paused"):
            continue
        if last_clock and abs((vt - last_clock[1]) - (ev["t"] - last_clock[0]) / 1000) < RUN_BREAK_S:
            seg.runs[-1][1] = vt
        else:
            seg.runs.append([vt, vt])
        last_clock = (ev["t"], vt)

        if kind == "cue" and ev["text"]:
            seg.shown += 1
            i = index.match(ev["text"], vt)
            if i is None:
                seg.unmatched.append(ev["text"])
            else:
                matched.add(i)
                if ev["t"] >= settle_until:
                    seg.lags_ms.append((vt - index.cues[i][1].start) * 1000)

    active = Counter(index.cues[i][0] for i in matched).most_common(1)
    active_track = active[0][0] if active else -1
    return [s for s in segments if s.states or s.label != segments[0].label], index, matched, active_track


def report(segments: list[Segment], index: TrackIndex, matched: set[int], active_track: int) -> None:
    n_tracks = len({t for t, _ in index.cues})
    print(f"subtitle tracks captured: {n_tracks}" + ("" if n_tracks else
          "  -> lag/missed can't be measured; only on-screen flow is reported"))
    for seg in segments:
        dur = (seg.t1 - seg.t0) / 1000
        total = sum(seg.states.values()) or 1
        states = ", ".join(f"{vis}/{'focused' if focus else 'unfocused'} {n * 100 // total}%"
                           for (vis, focus), n in seg.states.most_common())
        print(f"\n== {seg.label}  ({dur / 60:.1f} min; {states or 'no events'})")
        print(f"   subtitles on screen: {seg.shown}   matched to track: {seg.shown - len(seg.unmatched)}")
        if seg.lags_ms:
            print(f"   lag vs track (ms): p50 {pct(seg.lags_ms, .5):.0f}  p95 {pct(seg.lags_ms, .95):.0f}"
                  f"  max {max(seg.lags_ms):.0f}  (n={len(seg.lags_ms)})")
        if active_track >= 0:
            expected = [i for i, (track, cue) in enumerate(index.cues) if track == active_track
                        and any(a + 1 <= cue.start and cue.end <= b for a, b in seg.runs)]
            missed = [i for i in expected if i not in matched]
            print(f"   missed subtitles: {len(missed)} / {len(expected)}")
            for i in missed[:5]:
                cue = index.cues[i][1]
                print(f"      {cue.start:8.2f}  {cue.text.replace(chr(10), ' / ')}")
        if seg.timer_gaps_ms:
            print(f"   timer heartbeat: p95 gap {pct(seg.timer_gaps_ms, .95) / 1000:.1f} s,"
                  f" max {max(seg.timer_gaps_ms) / 1000:.1f} s")
        if seg.delivery_ms:
            print(f"   delivery to receiver: p95 {pct(seg.delivery_ms, .95):.0f} ms")
        for text in seg.unmatched[:3]:
            print(f"   unmatched: {text.replace(chr(10), ' / ')}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("log")
    args = parser.parse_args()
    events = load(args.log)
    if not events:
        print("empty log")
        return 1
    report(*analyze(events))
    return 0


if __name__ == "__main__":
    sys.exit(main())
