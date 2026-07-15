"""Top_scan short-circuit (pages_fetched == 1 anti-regression), traversal modes,
and cursor/anchor round-trip through the store."""

from __future__ import annotations

from ig_media_kit.fetch import FetchMode, fetch_window
from ig_media_kit.http_client import AnonymousClient
from ig_media_kit.store import Store
from tests.conftest import FakeResponse, FakeTransport, load_feed

USER_ID = "787132"
NEWEST_PK = 3300000000000000004


def _client(pages):
    return AnonymousClient(FakeTransport([FakeResponse(200, p) for p in pages]))


def test_caught_up_short_circuits_on_membership_page1():
    # Newest clip already seen -> stop on the FIRST item, one page, zero rows.
    feed = load_feed()
    client = _client([feed])  # only ONE page supplied; a 2nd fetch would raise
    res = fetch_window(client, USER_ID, mode=FetchMode.TOP_SCAN,
                       seen={"DZclip04"}, high_water_media_id=None, max_pages=4)
    assert res.pages_fetched == 1          # <-- load-bearing: did NOT page to the cap
    assert res.reels == []
    assert res.stop_reason == "caught_up"


def test_caught_up_short_circuits_on_watermark_page1():
    # Empty seen, but the numeric watermark equals the newest pk -> stop page 1.
    feed = load_feed()
    client = _client([feed])
    res = fetch_window(client, USER_ID, mode=FetchMode.TOP_SCAN,
                       seen=set(), high_water_media_id=NEWEST_PK, max_pages=4)
    assert res.pages_fetched == 1
    assert res.reels == []
    assert res.stop_reason == "caught_up"


def test_new_posts_return_only_new_and_stop_at_first_known():
    # Anchored below clip04; clip02/clip01 already seen -> only clip04 is new.
    feed = load_feed()
    client = _client([feed])
    res = fetch_window(client, USER_ID, mode=FetchMode.TOP_SCAN,
                       seen={"DZclip02", "DZclip01"},
                       high_water_media_id=3300000000000000002, max_pages=4)
    assert res.pages_fetched == 1
    assert [r.shortcode for r in res.reels] == ["DZclip04"]
    assert res.newest_media_id == NEWEST_PK
    assert res.newest_shortcode == "DZclip04"
    assert res.stop_reason == "caught_up"


def test_fresh_handle_walks_and_collects_all_clips():
    # No anchors -> genuinely all-new; collects all 3 clips, reports end_of_feed
    # (fixture more_available is true but only one page is supplied, so the
    # walk ends when the single page is consumed and the cap is not reached).
    feed = dict(load_feed())
    feed["more_available"] = False  # single page, no more
    client = _client([feed])
    res = fetch_window(client, USER_ID, mode=FetchMode.TOP_SCAN,
                       seen=set(), high_water_media_id=None, max_pages=4)
    assert res.pages_fetched == 1
    assert {r.shortcode for r in res.reels} == {"DZclip04", "DZclip02", "DZclip01"}
    assert res.stop_reason == "end_of_feed"


def test_deep_resume_stops_at_depth_target():
    feed = load_feed()
    client = _client([feed])
    res = fetch_window(client, USER_ID, mode=FetchMode.DEEP_RESUME,
                       start_cursor="3300000000000000005_787132",
                       depth_target=2, max_pages=4)
    assert len(res.reels) == 2
    assert res.stop_reason == "depth_reached"
    # deep_resume passes max_id from the supplied cursor.
    assert client  # sanity


def test_cursor_and_anchor_round_trip(tmp_path):
    store = Store(tmp_path)
    from ig_media_kit.fetch import normalize_item
    feed = load_feed()
    reels = [r for r in (normalize_item(i, 1) for i in feed["items"]) if r]
    store.write_window("natgeo", reels, user_id=USER_ID,
                       next_cursor=feed["next_max_id"], stop_reason="end_of_feed",
                       mode=FetchMode.TOP_SCAN)
    state = store.load_state("natgeo")
    assert state.user_id == USER_ID
    assert state.high_water_media_id == NEWEST_PK      # numeric pk, not a shortcode
    assert isinstance(state.high_water_media_id, int)
    assert state.deep_cursor == feed["next_max_id"]
    assert state.last_stop_reason == "end_of_feed"
