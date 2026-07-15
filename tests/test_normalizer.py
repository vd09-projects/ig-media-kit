"""Normalizer: clips filter, play_count, video_url, fetched_at, pk-vs-shortcode."""

from __future__ import annotations

from ig_media_kit.fetch import normalize_item
from tests.conftest import load_feed


def test_clips_filter_drops_carousel():
    feed = load_feed()
    normed = [normalize_item(it, fetched_at=1720999999) for it in feed["items"]]
    kept = [r for r in normed if r is not None]
    # Fixture has 3 clips + 1 carousel_container -> carousel dropped.
    assert len(kept) == 3
    assert all(r.product_type == "clips" for r in kept)
    assert "DZcarousel3" not in {r.shortcode for r in kept}


def test_play_count_and_video_url_extracted():
    feed = load_feed()
    reel = normalize_item(feed["items"][0], fetched_at=1720999999)
    assert reel is not None
    assert reel.play_count == 4531103
    assert reel.ig_play_count == 4600000
    assert reel.video_url == "https://instagram.fxyz1-1.fbcdn.net/o1/v/reel04.mp4"
    assert reel.fetched_at == 1720999999


def test_shortcode_and_media_id_are_distinct_fields():
    feed = load_feed()
    reel = normalize_item(feed["items"][0], fetched_at=1)
    assert reel is not None
    # shortcode is the opaque code; media_id is the numeric pk.
    assert reel.shortcode == "DZclip04"
    assert reel.media_id == 3300000000000000004
    assert isinstance(reel.media_id, int)


def test_caption_with_comma_preserved():
    feed = load_feed()
    reel = normalize_item(feed["items"][0], fetched_at=1)
    assert "," in reel.caption  # exercised by the CSV quoting test too


def test_carousel_returns_none():
    feed = load_feed()
    carousel = feed["items"][1]
    assert carousel["product_type"] == "carousel_container"
    assert normalize_item(carousel, fetched_at=1) is None


def test_malformed_item_dropped():
    assert normalize_item({"product_type": "clips"}, fetched_at=1) is None  # no code/pk
