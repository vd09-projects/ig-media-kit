"""T2.4a — pinned-prefix hardening of fetch._consume_page (co-located with the
caught-up==1-page anti-regression so a future refactor cannot silently regress
the pin-skip).

The owner feed is NOT strictly pk-descending: a bounded prefix of pinned reels
(older, smaller pk, already-seen) floats ABOVE genuinely-newer un-seen reels.
top_scan must SKIP the pins and COLLECT the newer reels below them — without
weakening the page-1 caught-up short-circuit, and returning caught_up (never
page_cap) when it is genuinely caught up.
"""

from __future__ import annotations

from ig_media_kit.fetch import PINNED_PREFIX_BOUND, FetchMode, fetch_window
from ig_media_kit.http_client import AnonymousClient
from tests.conftest import FakeResponse, FakeTransport

USER_ID = "787132"


def _clip(pk: int, code: str) -> dict:
    return {
        "pk": str(pk), "id": f"{pk}_{USER_ID}", "code": code,
        "product_type": "clips", "media_type": 2, "play_count": 1000 + pk % 1000,
        "ig_play_count": 1000, "like_count": 10, "comment_count": 1,
        "taken_at": 1720600000, "video_duration": 30.0,
        "caption": {"text": code},
        "video_versions": [{"url": f"https://x.fbcdn.net/{code}.mp4"}],
    }


def _page(items: list[dict], *, more: bool = False, next_id: str | None = None) -> dict:
    return {"num_results": len(items), "more_available": more,
            "next_max_id": next_id, "items": items}


def _client(pages: list[dict]) -> AnonymousClient:
    return AnonymousClient(FakeTransport([FakeResponse(200, p) for p in pages]))


# --- (a) new un-seen reels BELOW a pinned prefix ARE collected --------------

def test_unseen_reels_below_pinned_prefix_are_collected():
    # 3 pinned (already-seen, low pk) on top, then two genuinely-newer un-seen
    # reels (high pk) below. The pins must be skipped, the new reels collected.
    pins = [_clip(100, "pinA"), _clip(101, "pinB"), _clip(102, "pinC")]
    newer = [_clip(9001, "newX"), _clip(9002, "newY")]
    page = _page(pins + newer, more=False, next_id=None)
    client = _client([page])

    res = fetch_window(client, USER_ID, mode=FetchMode.TOP_SCAN,
                       seen={"pinA", "pinB", "pinC"},
                       high_water_media_id=None, max_pages=4)

    assert [r.shortcode for r in res.reels] == ["newX", "newY"]  # no silent drop
    assert res.newest_media_id == 9002                            # max numeric pk
    assert res.pages_fetched == 1


def test_new_reel_then_seen_boundary_stops_caught_up():
    # After the pin block + one NEW reel, the next already-seen reel is the REAL
    # caught-up boundary -> stop, one page.
    pins = [_clip(100, "pinA"), _clip(101, "pinB")]
    page = _page(pins + [_clip(9001, "newX"), _clip(50, "oldSeen")],
                 more=True, next_id="50_" + USER_ID)
    client = _client([page])  # only ONE page; a 2nd fetch would raise

    res = fetch_window(client, USER_ID, mode=FetchMode.TOP_SCAN,
                       seen={"pinA", "pinB", "oldSeen"},
                       high_water_media_id=None, max_pages=4)

    assert [r.shortcode for r in res.reels] == ["newX"]
    assert res.pages_fetched == 1
    assert res.stop_reason == "caught_up"


# --- (b) anti-regression: genuinely caught-up handle stays 1 page, 0 rows ----

def test_all_seen_page_short_circuits_one_page_caught_up():
    # Every clip already seen (pins included) -> caught_up on page 1, zero rows.
    items = [_clip(100, "pinA"), _clip(101, "pinB"), _clip(102, "pinC"),
             _clip(103, "realD"), _clip(104, "realE")]
    page = _page(items, more=True, next_id="104_" + USER_ID)
    client = _client([page])  # a 2nd page would raise -> proves 1-page stop

    res = fetch_window(client, USER_ID, mode=FetchMode.TOP_SCAN,
                       seen={"pinA", "pinB", "pinC", "realD", "realE"},
                       high_water_media_id=None, max_pages=4)

    assert res.pages_fetched == 1
    assert res.reels == []
    assert res.stop_reason == "caught_up"   # NOT page_cap (segment predicate relies on this)


def test_watermark_all_known_short_circuits_caught_up():
    # No seen set, but every pk is at/below the numeric watermark -> caught_up p1.
    items = [_clip(200, "a"), _clip(199, "b"), _clip(198, "c"), _clip(197, "d")]
    page = _page(items, more=True, next_id="197_" + USER_ID)
    client = _client([page])

    res = fetch_window(client, USER_ID, mode=FetchMode.TOP_SCAN,
                       seen=set(), high_water_media_id=200, max_pages=4)

    assert res.pages_fetched == 1
    assert res.reels == []
    assert res.stop_reason == "caught_up"


# --- bounded: MORE pins than the bound stop at the tolerance (documented) ----

def test_pin_tolerance_is_bounded():
    # bound+1 leading seen items -> the (bound+1)th trips the caught-up boundary.
    assert PINNED_PREFIX_BOUND == 3
    seen_pins = [_clip(100 + i, f"pin{i}") for i in range(PINNED_PREFIX_BOUND + 1)]
    page = _page(seen_pins + [_clip(9001, "newX")], more=True,
                 next_id="9001_" + USER_ID)
    client = _client([page])

    res = fetch_window(client, USER_ID, mode=FetchMode.TOP_SCAN,
                       seen={f"pin{i}" for i in range(PINNED_PREFIX_BOUND + 1)},
                       high_water_media_id=None, max_pages=4)
    # Bounded tolerance: it stops at the (bound+1)th known item (caught_up),
    # so a new reel below MORE than `bound` pins is not reached. IG's pin cap is
    # ~3, so this is safe in practice.
    assert res.reels == []
    assert res.stop_reason == "caught_up"
    assert res.pages_fetched == 1


# --- (c) deep_resume is byte-for-byte unchanged -----------------------------

def test_deep_resume_ignores_seen_and_watermark():
    # deep_resume must NOT apply the pin/watermark logic — it collects every clip
    # regardless of seen/high_water (the T2.4a change is TOP_SCAN-only).
    items = [_clip(100, "pinA"), _clip(9001, "newX"), _clip(50, "oldSeen")]
    page = _page(items, more=False, next_id=None)
    client = _client([page])

    res = fetch_window(client, USER_ID, mode=FetchMode.DEEP_RESUME,
                       start_cursor="c_" + USER_ID,
                       seen={"pinA", "oldSeen"}, high_water_media_id=9999,
                       depth_target=10, max_pages=4)

    # seen reels ARE dropped by the shared dedupe, but the watermark does NOT
    # stop the walk (that is top_scan-only): every UN-seen clip is collected.
    assert [r.shortcode for r in res.reels] == ["newX"]
    assert res.stop_reason == "end_of_feed"
