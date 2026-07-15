"""T2 orchestration — list_reels: serve-from-store gate, budget governor,
cold-start fill, coverage wiring, partial-on-stop_signal, never-sleep, and
header provenance. Offline only (FakeTransport)."""

from __future__ import annotations

import pytest

from ig_media_kit import IG_APP_ID, coverage
from ig_media_kit.config import (
    Config, FetchSettings, OutputSettings, TopReelsFilter,
)
from ig_media_kit.fetch import FetchMode, normalize_item
from ig_media_kit.http_client import AnonymousClient
from ig_media_kit.list_reels import run_list_reels
from ig_media_kit.store import Store
from tests.conftest import FakeResponse, FakeTransport

USER_ID = "787132"


def _config(store_dir, *, scan_depth=90, max_pages=4, count=10):
    return Config(
        channels=[],
        top_reels=TopReelsFilter(count=count, sort_by="play_count",
                                 min_play_count=0, min_duration=None,
                                 max_age_days=None),
        fetch=FetchSettings(scan_depth=scan_depth, max_pages_per_call=max_pages,
                            page_pace_seconds=1.5),
        output=OutputSettings(store_dir=str(store_dir),
                              media_dir=str(store_dir / "media")),
        raw={},
    )


def _clip(pk: int, code: str, plays: int = 1000) -> dict:
    return {
        "pk": str(pk), "id": f"{pk}_{USER_ID}", "code": code,
        "product_type": "clips", "media_type": 2, "play_count": plays,
        "ig_play_count": plays, "like_count": 10, "comment_count": 1,
        "taken_at": 1720600000, "video_duration": 30.0,
        "caption": {"text": code},
        "video_versions": [{"url": f"https://x.fbcdn.net/{code}.mp4"}],
    }


def _page(items, *, more=True, next_id="cur"):
    return {"num_results": len(items), "more_available": more,
            "next_max_id": next_id, "items": items}


def _profile():
    return FakeResponse(200, {"data": {"user": {"id": USER_ID}}})


def _client(responses):
    # Wrap bare page dicts as 200 responses; pass FakeResponse instances through.
    wrapped = [r if isinstance(r, FakeResponse) else FakeResponse(200, r)
               for r in responses]
    t = FakeTransport(wrapped)
    return AnonymousClient(t), t


class _NoNet:
    def __call__(self, *a, **k):
        raise AssertionError("network hit on a serve-from-store path")


# --- T2.2 serve-from-store (gate on CONTIGUITY) -----------------------------

def _seed_contiguous_store(store, handle, *, terminal=True):
    from tests.conftest import load_feed
    reels = [r for r in (normalize_item(i, 1) for i in load_feed()["items"]) if r]
    store.write_window(handle, reels, user_id=USER_ID, next_cursor=None,
                       stop_reason="end_of_feed", mode=FetchMode.TOP_SCAN)
    pks = [r.media_id for r in reels]
    store.save_coverage_segments(
        handle, [coverage._segment(max(pks), min(pks), None, terminal)])
    return len(reels)


def test_serve_from_store_zero_network_when_contiguous(tmp_path):
    config = _config(tmp_path)
    store = Store(tmp_path)
    n = _seed_contiguous_store(store, "natgeo")

    env = run_list_reels("natgeo", config=config, client=AnonymousClient(_NoNet()),
                         fresh_fetch=False)
    assert env["pages_fetched"] == 0          # ZERO network
    assert env["partial"] is False
    assert env["coverage"]["complete"] is True
    assert env["pool_depth"] == n
    assert "served from store" in env["note"]
    assert env["count_returned"] == n


def test_fresh_fetch_true_bypasses_serve_from_store(tmp_path):
    config = _config(tmp_path)
    store = Store(tmp_path)
    _seed_contiguous_store(store, "natgeo")
    # fresh_fetch bypasses the gate -> it WILL top-check (network). Give it a
    # caught-up page so it stops cheaply.
    client, t = _client([_page([_clip(9, "seenX")], more=False, next_id=None)])
    env = run_list_reels("natgeo", config=config, client=client, fresh_fetch=True)
    assert len(t.calls) >= 1                    # network WAS taken


def test_two_segment_pool_does_not_serve_from_store(tmp_path):
    # pool_depth large but 2 segments (a gap) -> NOT contiguous -> network path.
    config = _config(tmp_path, scan_depth=2)
    store = Store(tmp_path)
    from tests.conftest import load_feed
    reels = [r for r in (normalize_item(i, 1) for i in load_feed()["items"]) if r]
    store.write_window("h", reels, user_id=USER_ID, next_cursor="cb",
                       stop_reason="page_cap", mode=FetchMode.TOP_SCAN)
    store.save_coverage_segments("h", [
        coverage._segment(9999, 9990, "cf", False),
        coverage._segment(5000, 4990, "cb", False),
    ])
    # Give a caught-up top page so the network call is cheap.
    client, t = _client([_page([], more=False, next_id=None)])
    env = run_list_reels("h", config=config, client=client, fresh_fetch=False)
    assert len(t.calls) >= 1                    # gate did NOT fire; network taken
    assert env["coverage"]["segments"] >= 1


# --- T2.4 cold-start fill + high_water via store ----------------------------

def test_cold_start_fill_persists_and_ranks(tmp_path):
    config = _config(tmp_path, scan_depth=6)
    client, t = _client([
        _profile(),
        _page([_clip(1006, "a", 500), _clip(1005, "b", 900)], next_id="1005"),
        _page([_clip(1004, "c", 100), _clip(1003, "d", 700)], next_id="1003"),
        _page([_clip(1002, "e", 300), _clip(1001, "f", 800)], next_id="1001"),
    ])
    env = run_list_reels("natgeo", config=config, client=client)
    assert env["pool_depth"] == 6
    assert env["pages_fetched"] == 3            # topcheck_cap = max_pages - 1
    store = Store(tmp_path)
    assert store.load_state("natgeo").high_water_media_id == 1006
    assert env["coverage"]["segments"] == 1
    # ranked default play_count desc
    assert [r["shortcode"] for r in env["reels"]][:2] == ["b", "f"]


def test_high_water_monotonic_with_low_pk_pin(tmp_path):
    # A low-pk pin among high-pk new reels: high_water must be the MAX numeric
    # media_id, never the positionally-first (low-pk) item.
    config = _config(tmp_path, scan_depth=3, max_pages=1)
    client, t = _client([
        _profile(),
        _page([_clip(50, "pin"), _clip(9001, "x"), _clip(9002, "y")],
              more=False, next_id=None),
    ])
    run_list_reels("natgeo", config=config, client=client)
    assert Store(tmp_path).load_state("natgeo").high_water_media_id == 9002


# --- T2.3 budget governor ---------------------------------------------------

def test_budget_cap_across_both_phases(tmp_path):
    # Pre-seed 2 reels so top-check catches up in 1 page, then deepen gets the
    # remaining budget -> combined pages_fetched <= max_pages_per_call.
    config = _config(tmp_path, scan_depth=90, max_pages=4)
    store = Store(tmp_path)
    seeded = [normalize_item(_clip(1006, "a"), 1), normalize_item(_clip(1005, "b"), 1)]
    store.write_window("h", seeded, user_id=USER_ID, next_cursor="1005",
                       stop_reason="caught_up", mode=FetchMode.TOP_SCAN)
    store.save_coverage_segments("h", [coverage._segment(1006, 1005, "1005", False)])

    client, t = _client([
        _page([_clip(1006, "a"), _clip(1005, "b")], more=True, next_id="1005"),  # top: caught_up
        _page([_clip(1004, "c"), _clip(1003, "d")], more=True, next_id="1003"),  # deepen p1
        _page([_clip(1002, "e"), _clip(1001, "f")], more=True, next_id="1001"),  # deepen p2
        _page([_clip(1000, "g"), _clip(999, "h")], more=True, next_id="999"),    # deepen p3
    ])
    env = run_list_reels("h", config=config, client=client, fresh_fetch=True)
    assert env["pages_fetched"] <= 4
    # top-check spent 1 page (caught up), deepen got the remaining 3.
    assert env["pages_fetched"] == 4
    assert len(t.calls) == 4


def test_governor_never_sleeps(tmp_path, monkeypatch):
    import time as _time
    monkeypatch.setattr(_time, "sleep",
                        lambda *_a, **_k: (_ for _ in ()).throw(
                            AssertionError("list_reels must never sleep")))
    config = _config(tmp_path, scan_depth=4)
    client, t = _client([
        _profile(),
        _page([_clip(1006, "a"), _clip(1005, "b")], more=True, next_id="1005"),
        _page([_clip(1004, "c"), _clip(1003, "d")], more=False, next_id=None),
    ])
    env = run_list_reels("natgeo", config=config, client=client)
    assert env["partial"] is False


# --- T2.7 partial on stop_signal --------------------------------------------

def test_stop_signal_in_topcheck_aborts_whole_call(tmp_path):
    config = _config(tmp_path, scan_depth=90)
    client, t = _client([
        _profile(),
        FakeResponse(401, {"message": "login_required"}),  # first feed page 401
    ])
    env = run_list_reels("natgeo", config=config, client=client)
    assert env["partial"] is True
    assert env["pages_fetched"] == 1                 # deepen page NOT spent
    assert "budget cooling" in env["note"]
    assert env["stop_reason"] == "rate_limited"
    assert len(t.calls) == 2                          # profile + ONE feed page only


def test_stop_signal_in_deepen_returns_partial(tmp_path):
    config = _config(tmp_path, scan_depth=90)
    store = Store(tmp_path)
    seeded = [normalize_item(_clip(1006, "a"), 1), normalize_item(_clip(1005, "b"), 1)]
    store.write_window("h", seeded, user_id=USER_ID, next_cursor="1005",
                       stop_reason="caught_up", mode=FetchMode.TOP_SCAN)
    store.save_coverage_segments("h", [coverage._segment(1006, 1005, "1005", False)])
    client, t = _client([
        _page([_clip(1006, "a"), _clip(1005, "b")], more=True, next_id="1005"),  # top caught_up
        FakeResponse(429, {"message": "too many"}),                              # deepen 429
    ])
    env = run_list_reels("h", config=config, client=client, fresh_fetch=True)
    assert env["partial"] is True
    assert "budget cooling" in env["note"]
    assert env["pool_depth"] == 2                     # persisted pool intact
    assert env["stop_reason"] == "rate_limited"


# --- T2.1 validation --------------------------------------------------------

def test_invalid_sort_by_returns_clean_error(tmp_path):
    config = _config(tmp_path)
    env = run_list_reels("natgeo", config=config, sort_by="view_count",
                         client=AnonymousClient(_NoNet()))
    assert env["reels"] == []
    assert "invalid sort_by" in env["error"]
    assert env["pages_fetched"] == 0


def test_negative_count_returns_clean_error(tmp_path):
    config = _config(tmp_path)
    env = run_list_reels("natgeo", config=config, count=-1,
                         client=AnonymousClient(_NoNet()))
    assert "must be non-negative" in env["error"]


# --- T2.9 header provenance -------------------------------------------------

def test_x_ig_app_id_on_every_api_call(tmp_path):
    config = _config(tmp_path, scan_depth=4)
    client, t = _client([
        _profile(),
        _page([_clip(1006, "a"), _clip(1005, "b")], more=False, next_id=None),
    ])
    run_list_reels("natgeo", config=config, client=client)
    assert t.calls                                     # calls were made
    for call in t.calls:
        assert call["headers"].get("x-ig-app-id") == IG_APP_ID
        # orchestrator sets NO auth headers itself
        assert "authorization" not in {k.lower() for k in call["headers"]}
