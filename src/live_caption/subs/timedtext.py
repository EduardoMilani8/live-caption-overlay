"""Parse the subtitle files players download: TTML (Netflix), WebVTT, and
YouTube's json3. Everything becomes a flat list of timed cues.

Only the timing features these services actually use are supported: no
nested time containers, no styling. Times are seconds of video time.
"""

from __future__ import annotations

import html
import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass

TTP_NS = "{http://www.w3.org/ns/ttml#parameter}"


@dataclass(frozen=True)
class Cue:
    start: float
    end: float
    text: str  # lines separated by "\n"


def parse(kind: str, body: str) -> list[Cue]:
    parsers = {"ttml": parse_ttml, "vtt": parse_vtt, "json3": parse_json3}
    if kind not in parsers:
        raise ValueError(f"unknown subtitle format: {kind}")
    return parsers[kind](body)


# --- TTML -------------------------------------------------------------------

_OFFSET_TIME = re.compile(r"^(\d+(?:\.\d+)?)(h|m|s|ms|f|t)$")
_CLOCK_TIME = re.compile(r"^(\d+):(\d{2}):(\d{2})(?:\.(\d+)|:(\d+(?:\.\d+)?))?$")


def _ttml_time(value: str, tick_rate: float, frame_rate: float) -> float:
    value = value.strip()
    if m := _OFFSET_TIME.match(value):
        n, unit = float(m[1]), m[2]
        scale = {"h": 3600, "m": 60, "s": 1, "ms": 1e-3, "f": 1 / frame_rate, "t": 1 / tick_rate}
        return n * scale[unit]
    if m := _CLOCK_TIME.match(value):
        seconds = int(m[1]) * 3600 + int(m[2]) * 60 + int(m[3])
        if m[4]:
            seconds += float(f"0.{m[4]}")
        elif m[5]:
            seconds += float(m[5]) / frame_rate
        return seconds
    raise ValueError(f"unsupported TTML time: {value!r}")


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _ttml_text(el: ET.Element) -> str:
    parts = [el.text or ""]
    for child in el:
        parts.append("\n" if _local(child.tag) == "br" else _ttml_text(child))
        parts.append(child.tail or "")
    return "".join(parts)


def _clean_lines(text: str) -> str:
    lines = (" ".join(line.split()) for line in text.split("\n"))
    return "\n".join(line for line in lines if line)


def parse_ttml(body: str) -> list[Cue]:
    root = ET.fromstring(body)
    tick_rate = float(root.get(f"{TTP_NS}tickRate") or 1)
    frame_rate = float(root.get(f"{TTP_NS}frameRate") or 30)
    cues = []
    for p in root.iter():
        if _local(p.tag) != "p" or p.get("begin") is None:
            continue
        start = _ttml_time(p.get("begin"), tick_rate, frame_rate)
        if p.get("end") is not None:
            end = _ttml_time(p.get("end"), tick_rate, frame_rate)
        elif p.get("dur") is not None:
            end = start + _ttml_time(p.get("dur"), tick_rate, frame_rate)
        else:
            continue
        if text := _clean_lines(_ttml_text(p)):
            cues.append(Cue(start, end, text))
    return sorted(cues, key=lambda c: c.start)


# --- WebVTT -----------------------------------------------------------------

_VTT_TIME = re.compile(r"(?:(\d+):)?(\d{2}):(\d{2})\.(\d{3})")
_TAG = re.compile(r"<[^>]+>")


def _vtt_time(value: str) -> float:
    m = _VTT_TIME.fullmatch(value.strip())
    if not m:
        raise ValueError(f"unsupported WebVTT time: {value!r}")
    return int(m[1] or 0) * 3600 + int(m[2]) * 60 + int(m[3]) + int(m[4]) / 1000


def parse_vtt(body: str) -> list[Cue]:
    cues = []
    for block in re.split(r"\n\s*\n", body.replace("\r\n", "\n")):
        lines = block.strip().split("\n")
        timing = next((i for i, line in enumerate(lines) if "-->" in line), None)
        if timing is None:
            continue  # header, NOTE, STYLE
        start, _, rest = lines[timing].partition("-->")
        end = rest.split()[0]  # cue settings may follow
        text = html.unescape(_TAG.sub("", "\n".join(lines[timing + 1 :])))
        if text := _clean_lines(text):
            cues.append(Cue(_vtt_time(start), _vtt_time(end), text))
    return sorted(cues, key=lambda c: c.start)


# --- YouTube json3 ----------------------------------------------------------


def parse_json3(body: str) -> list[Cue]:
    cues = []
    for event in json.loads(body).get("events", []):
        segs = event.get("segs")
        if not segs or "tStartMs" not in event:
            continue
        text = _clean_lines("".join(s.get("utf8", "") for s in segs))
        if text:
            start = event["tStartMs"] / 1000
            cues.append(Cue(start, start + event.get("dDurationMs", 0) / 1000, text))
    return cues


# --- matching ---------------------------------------------------------------

_NON_WORD = re.compile(r"[^\w]+")


def normalize(text: str) -> str:
    """Comparison form for matching on-screen text against a track: players
    restyle, re-wrap and sometimes re-punctuate what the file says."""
    return _NON_WORD.sub(" ", _TAG.sub("", text).casefold()).strip()
