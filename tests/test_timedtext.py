import json

import pytest

from live_caption.subs.timedtext import Cue, normalize, parse, parse_json3, parse_ttml, parse_vtt

# Shape of a Netflix "dfxp-ls-sdh" track: tick-based times, <br/> between lines,
# styling spans, cues not necessarily in order.
NETFLIX_TTML = """<?xml version="1.0" encoding="utf-8"?>
<tt xmlns="http://www.w3.org/ns/ttml" xmlns:ttp="http://www.w3.org/ns/ttml#parameter"
    xmlns:tts="http://www.w3.org/ns/ttml#styling" ttp:tickRate="10000000" xml:lang="pt-BR">
  <body><div>
    <p begin="52000000t" end="75000000t" region="region_00">
      <span style="style_0">Onde você</span><br/><span style="style_0">estava?</span>
    </p>
    <p begin="10000000t" end="30000000t">-Oi.<br/>-Olá &amp; tchau.</p>
    <p begin="80000000t" end="90000000t">   </p>
  </div></body>
</tt>"""


def test_ttml_ticks_and_line_breaks():
    assert parse_ttml(NETFLIX_TTML) == [
        Cue(1.0, 3.0, "-Oi.\n-Olá & tchau."),
        Cue(5.2, 7.5, "Onde você\nestava?"),
    ]


def test_ttml_clock_times_and_dur():
    body = """<tt xmlns="http://www.w3.org/ns/ttml"><body><div>
      <p begin="00:01:02.500" dur="1.5s">a</p>
      <p begin="00:00:01:15" end="2000ms">b</p>
    </div></body></tt>"""
    assert parse_ttml(body) == [Cue(1.5, 2.0, "b"), Cue(62.5, 64.0, "a")]


def test_vtt():
    body = """WEBVTT

NOTE produced by a player

1
00:00:01.000 --> 00:00:02.500 line:80%
<c.yellow>Hello</c>
there &amp; back

01:02.000 --> 01:03.000
Short time"""
    assert parse_vtt(body) == [
        Cue(1.0, 2.5, "Hello\nthere & back"),
        Cue(62.0, 63.0, "Short time"),
    ]


def test_json3_skips_empty_events():
    body = json.dumps({"events": [
        {"tStartMs": 0, "dDurationMs": 5000},
        {"tStartMs": 1200, "dDurationMs": 800, "segs": [{"utf8": "hi "}, {"utf8": "there"}]},
        {"tStartMs": 2000, "segs": [{"utf8": "\n"}]},
    ]})
    assert parse_json3(body) == [Cue(1.2, 2.0, "hi there")]


def test_unknown_kind():
    with pytest.raises(ValueError):
        parse("srt", "")


def test_normalize_ignores_layout_and_punctuation():
    assert normalize("-Oi.\n-Olá  <i>tchau</i>!") == normalize("- oi - olá tchau")
