"""T17 — fill.run_fill: the command-side call-driven fetch primitive.

This is the metered top-check + deepen unit extracted from the old run_list_reels
when list_reels became read-only (CQRS split). It is the batch runner's per-unit
fetch primitive. These tests migrate the network-path coverage that used to live
in test_list_reels: serve-from-store short-circuit, budget governor, cold-start
fill + high_water, partial-on-stop_signal, never-sleep, and header provenance.
Offline only (FakeTransport)."""

from __future__ import annotations

from ig_media_kit import IG_APP_ID, coverage
from ig_media_kit.config import (
    Config, FetchSettings, OutputSettings, TopReelsFilter,
)
from ig_media_kit.fetch import FetchMode, normalize_item
from ig_media_kit.fill import run_fill
from ig_media_kit.http_client import AnonymousClient
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
    wrapped = [r if isinstance(r, FakeResponse) else FakeResponse(200, r)
               for r in responses]
    t = FakeTransport(wrapped)
    return AnonymousClient(t), t


class _NoNet:
    def __call__(self, *a, **k):
        raise AssertionError("network hit on a serve-from-store path")


def _seed_contiguous_store(store, handle, *, terminal=True):
    from tests.conftest import load_feed
    reels = [r for r in (normalize_item(i, 1) for i in load_feed()["items"]) if r]
    store.write_window(handle, reels, user_id=USER_ID, next_cursor=None,
                       stop_reason="end_of_feed", mode=FetchMode.TOP_SCAN)
    pks = [r.media_id for r in reels]
    store.save_coverage_segments(
        handle, [coverage._segment(max(pks), min(pks), None, terminal)])
    return len(reels)


# --- serve-from-store short-circuit (gate on CONTIGUITY) --------------------

def test_serve_from_store_zero_network_when_contiguous(tmp_path):
    config = _config(tmp_path)
    store = Store(tmp_path)
    n = _seed_contiguous_store(store, "natgeo")

    env = run_fill("natgeo", config=config, client=AnonymousClient(_NoNet()),
                   store=store)
    assert env["pages_fetched"] == 0          # ZERO network
    assert env["partial"] is False
    assert env["coverage"]["complete"] is True
    assert env["pool_depth"] == n
    assert env["count_returned"] == n


def test_two_segment_pool_takes_network(tmp_path):
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
    client, t = _client([_page([], more=False, next_id=None)])
    env = run_fill("h", config=config, client=client, store=store)
    assert len(t.calls) >= 1                    # gate did NOT fire; network taken
    assert env["coverage"]["segments"] >= 1


# --- cold-start fill + high_water via store ---------------------------------

def test_cold_start_fill_persists_and_ranks(tmp_path):
    config = _config(tmp_path, scan_depth=6)
    client, t = _client([
        _profile(),
        _page([_clip(1006, "a", 500), _clip(1005, "b", 900)], next_id="1005"),
        _page([_clip(1004, "c", 100), _clip(1003, "d", 700)], next_id="1003"),
        _page([_clip(1002, "e", 300), _clip(1001, "f", 800)], next_id="1001"),
    ])
    env = run_fill("natgeo", config=config, client=client)
    assert env["pool_depth"] == 6
    assert env["pages_fetched"] == 3            # topcheck_cap = max_pages - 1
    store = Store(tmp_path)
    assert store.load_state("natgeo").high_water_media_id == 1006
    assert env["coverage"]["segments"] == 1
    assert [r["shortcode"] for r in env["reels"]][:2] == ["b", "f"]


def test_high_water_monotonic_with_low_pk_pin(tmp_path):
    config = _config(tmp_path, scan_depth=3, max_pages=1)
    client, t = _client([
        _profile(),
        _page([_clip(50, "pin"), _clip(9001, "x"), _clip(9002, "y")],
              more=False, next_id=None),
    ])
    run_fill("natgeo", config=config, client=client)
    assert Store(tmp_path).load_state("natgeo").high_water_media_id == 9002


def test_fill_stamps_last_analyzed_at(tmp_path):
    # The batch runner is the only writer that advances coverage; run_fill routes
    # through write_window, so a fill stamps last_analyzed_at for list_reels to read.
    config = _config(tmp_path, scan_depth=3, max_pages=1)
    client, t = _client([
        _profile(),
        _page([_clip(9001, "x")], more=False, next_id=None),
    ])
    run_fill("natgeo", config=config, client=client, now=lambda: 555_000)
    assert Store(tmp_path).load_state("natgeo").last_analyzed_at == 555_000


# --- budget governor --------------------------------------------------------

def test_budget_cap_across_both_phases(tmp_path):
    config = _config(tmp_path, scan_depth=90, max_pages=4)
    store = Store(tmp_path)
    seeded = [normalize_item(_clip(1006, "a"), 1), normalize_item(_clip(1005, "b"), 1)]
    store.write_window("h", seeded, user_id=USER_ID, next_cursor="1005",
                       stop_reason="caught_up", mode=FetchMode.TOP_SCAN)
    store.save_coverage_segments("h", [coverage._segment(1006, 1005, "1005", False)])

    client, t = _client([
        _page([_clip(1006, "a"), _clip(1005, "b")], more=True, next_id="1005"),
        _page([_clip(1004, "c"), _clip(1003, "d")], more=True, next_id="1003"),
        _page([_clip(1002, "e"), _clip(1001, "f")], more=True, next_id="1001"),
        _page([_clip(1000, "g"), _clip(999, "h")], more=True, next_id="999"),
    ])
    env = run_fill("h", config=config, client=client, store=store)
    assert env["pages_fetched"] <= 4
    assert env["pages_fetched"] == 4
    assert len(t.calls) == 4


def test_never_sleeps(tmp_path, monkeypatch):
    import time as _time
    monkeypatch.setattr(_time, "sleep",
                        lambda *_a, **_k: (_ for _ in ()).throw(
                            AssertionError("the fill primitive must never sleep")))
    config = _config(tmp_path, scan_depth=4)
    client, t = _client([
        _profile(),
        _page([_clip(1006, "a"), _clip(1005, "b")], more=True, next_id="1005"),
        _page([_clip(1004, "c"), _clip(1003, "d")], more=False, next_id=None),
    ])
    env = run_fill("natgeo", config=config, client=client)
    assert env["partial"] is False


# --- partial on stop_signal -------------------------------------------------

def test_stop_signal_in_topcheck_aborts_whole_call(tmp_path):
    config = _config(tmp_path, scan_depth=90)
    client, t = _client([
        _profile(),
        FakeResponse(401, {"message": "login_required"}),
    ])
    env = run_fill("natgeo", config=config, client=client)
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
        _page([_clip(1006, "a"), _clip(1005, "b")], more=True, next_id="1005"),
        FakeResponse(429, {"message": "too many"}),
    ])
    env = run_fill("h", config=config, client=client, store=store)
    assert env["partial"] is True
    assert "budget cooling" in env["note"]
    assert env["pool_depth"] == 2                     # persisted pool intact
    assert env["stop_reason"] == "rate_limited"


# --- validation + header provenance -----------------------------------------

def test_invalid_sort_by_returns_clean_error_no_network(tmp_path):
    config = _config(tmp_path)
    env = run_fill("natgeo", config=config, sort_by="view_count",
                   client=AnonymousClient(_NoNet()))
    assert env["reels"] == []
    assert "invalid sort_by" in env["error"]
    assert env["pages_fetched"] == 0


def test_x_ig_app_id_on_every_api_call(tmp_path):
    config = _config(tmp_path, scan_depth=4)
    client, t = _client([
        _profile(),
        _page([_clip(1006, "a"), _clip(1005, "b")], more=False, next_id=None),
    ])
    run_fill("natgeo", config=config, client=client)
    assert t.calls
    for call in t.calls:
        assert call["headers"].get("x-ig-app-id") == IG_APP_ID
        assert "authorization" not in {k.lower() for k in call["headers"]}
