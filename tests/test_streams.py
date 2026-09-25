import json
from pathlib import Path

import pytest

from live_caption.audio.streams import parse_pw_dump, pick_stream

FIXTURE = Path(__file__).parent / "fixtures" / "pw_dump_sample.json"


@pytest.fixture
def streams():
    return parse_pw_dump(json.loads(FIXTURE.read_text()))


def test_only_output_streams_are_listed(streams):
    # sinks, input streams (like our own pw-record) and clients are ignored
    assert [s.serial for s in streams] == [204, 310, 315]


def test_fields_are_parsed(streams):
    ff = streams[0]
    assert (ff.node_id, ff.app_name, ff.binary, ff.pid) == (70, "Firefox", "firefox", 7111)
    assert ff.media_name == "Netflix - Dark S01E01"
    assert ff.is_playing


def test_corked_stream_is_not_playing(streams):
    assert not streams[2].is_playing


def test_pick_by_serial(streams):
    assert pick_stream(streams, "310").app_name == "Spotify"


def test_pick_by_unique_app_name_is_case_insensitive(streams):
    assert pick_stream(streams, "spotify").serial == 310


def test_pick_by_media_title(streams):
    assert pick_stream(streams, "netflix").serial == 204


def test_pick_returns_none_when_nothing_matches(streams):
    assert pick_stream(streams, "vlc") is None


def test_pick_prefers_playing_stream_of_same_app(streams):
    # Netflix (playing) must win over the paused YouTube tab regardless of order
    assert pick_stream(list(reversed(streams)), "firefox").serial == 204


def test_pick_title_match_beats_app_match(streams):
    # "fire" hits the Firefox app name, but a tab titled "Fire..." is more specific
    from dataclasses import replace
    tab = replace(streams[1], serial=999, app_name="Brave", binary="brave", media_name="Fireworks live")
    assert pick_stream(streams + [tab], "fire").serial == 999


def test_pick_paused_stream_when_it_is_the_only_match(streams):
    assert pick_stream(streams, "lofi").serial == 315
