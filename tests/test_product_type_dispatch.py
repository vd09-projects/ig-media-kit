"""T5 product_type dispatch — the extensibility SWITCH is OBSERVABLE: a non-clip
routes to the disabled stub handler with a TYPED skip-reason (distinguishable from a
clips-path drop), clips still normalize unchanged, and what reaches the store stays
clips-only. Watermark/dedupe stay non-positional. Offline."""

from __future__ import annotations

import random

from ig_media_kit.fetch import (
    CLIP_PRODUCT_TYPE, STUB_PRODUCT_TYPE, FetchMode, NormalizeResult, ReelRecord,
    SkipReason, fetch_window, normalize_item, normalize_item_routed,
)
from ig_media_kit.http_client import AnonymousClient
from tests.conftest import FakeResponse, FakeTransport

USER_ID = "787132"


def _item(pk: int, code: str, product_type: str, *, plays=1000, with_code=True) -> dict:
    it = {
        "pk": str(pk), "id": f"{pk}_{USER_ID}", "product_type": product_type,
        "play_count": plays, "ig_play_count": plays, "like_count": 5,
        "comment_count": 1, "taken_at": 1_700_000_000, "video_duration": 20.0,
        "caption": {"text": code}, "video_versions": [{"url": f"u/{code}"}],
    }
    if with_code:
        it["code"] = code
    return it


# --- the switch routes observably -------------------------------------------

def test_clip_routes_to_a_reel_no_skip():
    res = normalize_item_routed(_item(101, "DZa", CLIP_PRODUCT_TYPE), 1)
    assert isinstance(res, NormalizeResult)
    assert isinstance(res.reel, ReelRecord) and res.reel.shortcode == "DZa"
    assert res.skip_reason is None


def test_stub_type_routes_to_typed_skip_reason_not_bare_none():
    # The registered demonstrator stub type ("image") routes to the stub handler.
    res = normalize_item_routed(_item(102, "DZimg", STUB_PRODUCT_TYPE), 1)
    assert res.reel is None
    assert res.skip_reason is SkipReason.UNSUPPORTED_PRODUCT_TYPE


def test_unregistered_type_falls_through_to_stub():
    res = normalize_item_routed(_item(103, "DZcar", "carousel_container"), 1)
    assert res.reel is None
    assert res.skip_reason is SkipReason.UNSUPPORTED_PRODUCT_TYPE


def test_malformed_clip_is_distinguishable_from_unsupported():
    res = normalize_item_routed(_item(104, "DZbad", CLIP_PRODUCT_TYPE, with_code=False), 1)
    assert res.reel is None
    assert res.skip_reason is SkipReason.MALFORMED  # NOT UNSUPPORTED — observably different


def test_normalize_item_backwards_compatible_none_contract():
    # The thin wrapper's None-vs-ReelRecord contract is unchanged for existing callers.
    assert isinstance(normalize_item(_item(105, "DZc", CLIP_PRODUCT_TYPE), 1), ReelRecord)
    assert normalize_item(_item(106, "DZi", STUB_PRODUCT_TYPE), 1) is None
    assert normalize_item(_item(107, "DZx", "carousel_container"), 1) is None


# --- the store stays clips-only; watermark is non-positional ----------------

def test_fetch_window_excludes_stub_and_watermarks_by_max_media_id_non_positional():
    # A mixed page in SHUFFLED order — the newest clip is NOT first, proving the
    # watermark rests on numeric media_id, never on feed position.
    clip_low = _item(200, "DZlow", CLIP_PRODUCT_TYPE, plays=10)
    clip_high = _item(400, "DZhigh", CLIP_PRODUCT_TYPE, plays=99)
    stub_img = _item(300, "DZimg", STUB_PRODUCT_TYPE)
    carousel = _item(350, "DZcar", "carousel_container")
    items = [clip_low, stub_img, clip_high, carousel]
    random.Random(7).shuffle(items)

    page = {"num_results": len(items), "more_available": False,
            "next_max_id": None, "items": items}
    # fetch_window pages the feed directly (user_id already known) — one page.
    client = AnonymousClient(FakeTransport([FakeResponse(200, page)]))
    result = fetch_window(client, USER_ID, mode=FetchMode.TOP_SCAN, seen=None,
                          high_water_media_id=None, sleep=None)

    codes = sorted(r.shortcode for r in result.reels)
    assert codes == ["DZhigh", "DZlow"], f"non-clips leaked into the pool: {codes}"
    assert result.newest_media_id == 400, "watermark must be the max clip media_id"
    assert result.newest_shortcode == "DZhigh"
