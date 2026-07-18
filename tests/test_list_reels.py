"""T17 — list_reels is a pure READ-ONLY query over the store.

Covers: the zero-IG-request invariant on BOTH the not-analyzed and analyzed paths
(the non-negotiable acceptance gate), the typed not-analyzed error, analyzed
instant serve + ranking over the deduped pool, the staleness metadata block, the
three-state readiness boundary, and validation. Offline only — no network client
is ever constructed (asserted). The metered fetch path moved to fill.run_fill (see
test_fill.py)."""

from __future__ import annotations

import yaml

from ig_media_kit import coverage
from ig_media_kit.config import (
    Config, FetchSettings, OutputSettings, TopReelsFilter,
)
from ig_media_kit.fetch import FetchMode, normalize_item
from ig_media_kit.list_reels import SIGNED_URL_TTL_SECONDS, run_list_reels
from ig_media_kit.store import Store

USER_ID = "787132"
NOW = 1_720_600_000


def _config(store_dir, *, scan_depth=90, count=10):
    return Config(
        channels=[],
        top_reels=TopReelsFilter(count=count, sort_by="play_count",
                                 min_play_count=0, min_duration=None,
                                 max_age_days=None),
        fetch=FetchSettings(scan_depth=scan_depth, max_pages_per_call=4,
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


def _seed(store, handle, clips, *, terminal, fetched_at=NOW, now=NOW):
    """Persist clips + a single coverage segment through the real store path.
    ``terminal`` True -> contiguous (complete); a non-terminal single segment at
    shallow depth is analyzed-but-not-contiguous (state b)."""
    reels = [normalize_item(c, fetched_at) for c in clips]
    store.write_window(handle, reels, user_id=USER_ID, next_cursor=None,
                       stop_reason="end_of_feed", mode=FetchMode.TOP_SCAN,
                       now=lambda: now)
    pks = [r.media_id for r in reels]
    store.save_coverage_segments(
        handle, [coverage._segment(max(pks), min(pks),
                                   None if terminal else "curX", terminal)])
    return reels


def _poison_network(monkeypatch):
    """Make building a REAL IG transport a hard failure, so ANY attempt by
    list_reels to construct a network client blows up. Returns a hit counter — 0
    after a read-only call proves zero IG requests were even attempted."""
    import ig_media_kit.http_client as http_client
    hits = {"n": 0}

    def _boom(*_a, **_k):
        hits["n"] += 1
        raise AssertionError("list_reels attempted to build a real IG transport — "
                             "it must be zero-network on every path")

    monkeypatch.setattr(http_client, "_default_transport", _boom)
    return hits


# --- zero-IG-request invariant (the headline acceptance gate) ---------------

def test_zero_ig_on_analyzed_serve_path(tmp_path, monkeypatch):
    hits = _poison_network(monkeypatch)
    store = Store(tmp_path)
    _seed(store, "natgeo", [_clip(9, "a"), _clip(8, "b")], terminal=True)

    env = run_list_reels("natgeo", config=_config(tmp_path), store=store,
                         now=lambda: NOW)
    assert env["reels"], "analyzed handle should serve reels"
    assert env["pages_fetched"] == 0
    assert hits["n"] == 0, "served path attempted network"


def test_zero_ig_on_not_analyzed_error_path(tmp_path, monkeypatch):
    # Refinement #5: the NOT-ANALYZED path specifically records ZERO network — so
    # "not analyzed" can never be satisfied by a fetch attempt that failed.
    hits = _poison_network(monkeypatch)
    env = run_list_reels("neverseen", config=_config(tmp_path),
                         store=Store(tmp_path), now=lambda: NOW)
    assert env["error_kind"] == "not_analyzed"
    assert hits["n"] == 0, "not-analyzed path attempted network"


# --- state (a): not-analyzed typed error ------------------------------------

def test_not_analyzed_returns_typed_error(tmp_path):
    env = run_list_reels("cold", config=_config(tmp_path), store=Store(tmp_path),
                         now=lambda: NOW)
    assert env["partial"] is False
    assert env["retryable"] is False
    assert env["error_kind"] == "not_analyzed"
    assert env["reels"] == []
    assert env["count_returned"] == 0
    assert "start_batch_fetch" in env["note"]
    assert "staleness" not in env, "error envelope must not carry staleness"
    # Mirrors the served shape (superset-compatible) — no exception raised.
    for key in ("handle", "user_id", "coverage", "pool_depth", "pages_fetched"):
        assert key in env


# --- states (b)/(c): analyzed instant serve + ranking over the pool ----------

def test_analyzed_serve_ranks_over_deduped_pool_not_positional(tmp_path):
    store = Store(tmp_path)
    # Rows written OUT of play_count order; a duplicate shortcode is written twice
    # (the 2nd write is skip-seen deduped) — the served order must be play_count
    # desc, per-shortcode-deduped, NEVER CSV/feed row order.
    _seed(store, "h", [_clip(5, "low", 100), _clip(9, "hi", 900),
                       _clip(7, "mid", 500)], terminal=True)
    # Second window re-offers "hi" (dupe) + a new "top" — dedupe drops the dupe.
    store.write_window("h", [normalize_item(_clip(9, "hi", 900), NOW),
                             normalize_item(_clip(11, "top", 990), NOW)],
                       user_id=USER_ID, next_cursor=None,
                       stop_reason="end_of_feed", mode=FetchMode.TOP_SCAN,
                       now=lambda: NOW)

    env = run_list_reels("h", config=_config(tmp_path), store=store, now=lambda: NOW)
    codes = [r["shortcode"] for r in env["reels"]]
    assert codes == ["top", "hi", "mid", "low"], f"not ranked desc / deduped: {codes}"
    assert codes.count("hi") == 1, "duplicate shortcode was not deduped"
    assert env["pool_depth"] == 4
    assert env["coverage"]["complete"] is True


def test_analyzed_serve_respects_count(tmp_path):
    store = Store(tmp_path)
    _seed(store, "h", [_clip(9, "a", 900), _clip(8, "b", 800), _clip(7, "c", 700)],
          terminal=True)
    env = run_list_reels("h", config=_config(tmp_path), count=2, store=store,
                         now=lambda: NOW)
    assert [r["shortcode"] for r in env["reels"]] == ["a", "b"]
    assert env["count_returned"] == 2


# --- staleness metadata block -----------------------------------------------

def test_staleness_present_and_fields(tmp_path):
    store = Store(tmp_path)
    _seed(store, "h", [_clip(9, "a"), _clip(8, "b")], terminal=True,
          fetched_at=NOW, now=NOW)
    env = run_list_reels("h", config=_config(tmp_path, scan_depth=90), store=store,
                         now=lambda: NOW + 3600)
    st = env["staleness"]
    assert set(st) == {"last_analyzed_at", "store_count", "scan_depth_target",
                       "signed_url_maybe_expired"}
    assert st["last_analyzed_at"] == NOW          # stamped by write_window
    assert st["store_count"] == 2
    assert st["scan_depth_target"] == 90
    assert st["signed_url_maybe_expired"] is False  # 1h old << 36h TTL


def test_staleness_flags_expired_signed_url(tmp_path):
    store = Store(tmp_path)
    old = NOW - (SIGNED_URL_TTL_SECONDS + 3600)     # older than the 36h TTL
    _seed(store, "h", [_clip(9, "a"), _clip(8, "b")], terminal=True,
          fetched_at=old, now=old)
    env = run_list_reels("h", config=_config(tmp_path), store=store, now=lambda: NOW)
    assert env["staleness"]["signed_url_maybe_expired"] is True
    assert env["staleness"]["last_analyzed_at"] == old


def test_staleness_informational_hint_does_not_flip_complete(tmp_path):
    # store_count << scan_depth_target, but a terminal single segment is contiguous
    # -> complete stays True: the depth hint is informational, not a readiness gate.
    store = Store(tmp_path)
    _seed(store, "h", [_clip(9, "a")], terminal=True)
    env = run_list_reels("h", config=_config(tmp_path, scan_depth=90), store=store,
                         now=lambda: NOW)
    assert env["staleness"]["store_count"] == 1
    assert env["staleness"]["scan_depth_target"] == 90
    assert env["coverage"]["complete"] is True


def test_staleness_last_analyzed_at_none_on_legacy_state(tmp_path):
    # A pre-T17 state YAML has no last_analyzed_at -> loads as None and surfaces as
    # None in staleness (backward compatibility).
    store = Store(tmp_path)
    reels = [normalize_item(_clip(9, "a"), NOW)]
    store.write_window("h", reels, user_id=USER_ID, next_cursor=None,
                       stop_reason="end_of_feed", mode=FetchMode.TOP_SCAN)
    # Rewrite state.yaml WITHOUT the last_analyzed_at key (legacy shape).
    path = store.state_path("h")
    data = yaml.safe_load(path.read_text())
    data.pop("last_analyzed_at", None)
    data["coverage_segments"] = [dict(coverage._segment(9, 9, None, True))]
    path.write_text(yaml.safe_dump(data))
    assert store.load_state("h").last_analyzed_at is None

    env = run_list_reels("h", config=_config(tmp_path), store=store, now=lambda: NOW)
    assert env["staleness"]["last_analyzed_at"] is None
    assert env["reels"], "legacy-state handle still serves"


# --- three-state readiness boundary -----------------------------------------

def test_boundary_a_empty_store_errors(tmp_path):
    env = run_list_reels("h", config=_config(tmp_path), store=Store(tmp_path),
                         now=lambda: NOW)
    assert env["error_kind"] == "not_analyzed"


def test_boundary_b_one_shallow_reel_serves_not_errors(tmp_path):
    # 1 reel, single NON-terminal shallow segment -> analyzed (serve), complete=False.
    store = Store(tmp_path)
    _seed(store, "h", [_clip(9, "a")], terminal=False)
    env = run_list_reels("h", config=_config(tmp_path, scan_depth=90), store=store,
                         now=lambda: NOW)
    assert "error" not in env, "a shallow-but-analyzed handle must serve, not error"
    assert env["reels"]
    assert env["coverage"]["complete"] is False
    assert "staleness" in env


def test_boundary_b_segments_present_empty_pool_serves_empty(tmp_path):
    # Edge (Domain reviewer): coverage evidence exists (high_water/segments) but the
    # pool is empty (a window persisted 0 rows). That is ANALYZED -> serve an empty
    # ranked list, NOT the not-analyzed error.
    store = Store(tmp_path)
    store.write_window("h", [], user_id=USER_ID, next_cursor="curX",
                       stop_reason="page_cap", mode=FetchMode.TOP_SCAN,
                       now=lambda: NOW)
    store.save_coverage_segments("h", [coverage._segment(9, 8, "curX", False)])
    assert store.load_state("h").coverage_segments  # evidence present
    env = run_list_reels("h", config=_config(tmp_path), store=store, now=lambda: NOW)
    assert "error" not in env, "segments-present/empty-pool is analyzed, not an error"
    assert env["reels"] == []
    assert "staleness" in env


def test_boundary_c_contiguous_serves_complete(tmp_path):
    store = Store(tmp_path)
    _seed(store, "h", [_clip(9, "a"), _clip(8, "b")], terminal=True)
    env = run_list_reels("h", config=_config(tmp_path), store=store, now=lambda: NOW)
    assert "error" not in env
    assert env["coverage"]["complete"] is True


# --- validation (uniform error contract) ------------------------------------

def test_invalid_sort_by_returns_clean_typed_error(tmp_path):
    env = run_list_reels("h", config=_config(tmp_path), sort_by="view_count",
                         store=Store(tmp_path), now=lambda: NOW)
    assert env["reels"] == []
    assert "invalid sort_by" in env["error"]
    assert env["error_kind"] == "invalid_params"
    assert env["retryable"] is False
    assert env["pages_fetched"] == 0
    assert "staleness" not in env


def test_negative_count_returns_clean_typed_error(tmp_path):
    env = run_list_reels("h", config=_config(tmp_path), count=-1,
                         store=Store(tmp_path), now=lambda: NOW)
    assert "must be non-negative" in env["error"]
    assert env["error_kind"] == "invalid_params"
