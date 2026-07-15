"""T3 download_reel — cached-hit no-network gate, TTL-margin freshness,
identity-anchored (never-positional) re-resolve, redirect-following binary CDN
download + ftyp-verify, atomic manifest update, and the typed-envelope contract.
Offline only (FakeTransport) — no live IG. See probe/probe_download.py for the
opt-in live pilot."""

from __future__ import annotations

import csv

import pytest

from ig_media_kit import IG_APP_ID
from ig_media_kit.config import (
    Config, FetchSettings, OutputSettings, TopReelsFilter,
)
from ig_media_kit.download import (
    URL_REFRESH_MARGIN_SECONDS, _looks_like_mp4, run_download_reel,
)
from ig_media_kit.fetch import FetchMode, normalize_item
from ig_media_kit.http_client import AnonymousClient
from ig_media_kit.store import Store
from tests.conftest import FakeResponse, FakeTransport

USER_ID = "787132"
NOW = 1_720_600_000
# A minimal ISO-BMFF/mp4 opener: 4-byte box size, then the 'ftyp' box TYPE at
# offset 4 (the signature is at [4:8], NOT [0:4]).
MP4_BYTES = b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isomiso2"


def _config(tmp_path, *, channels=None, max_pages=4):
    return Config(
        channels=channels or [],
        top_reels=TopReelsFilter(count=10, sort_by="play_count"),
        fetch=FetchSettings(scan_depth=90, max_pages_per_call=max_pages,
                            page_pace_seconds=1.5),
        output=OutputSettings(store_dir=str(tmp_path / "store"),
                              media_dir=str(tmp_path / "media")),
        raw={},
    )


def _clip(pk: int, code: str, url: str, plays: int = 1000) -> dict:
    return {
        "pk": str(pk), "id": f"{pk}_{USER_ID}", "code": code,
        "product_type": "clips", "media_type": 2, "play_count": plays,
        "ig_play_count": plays, "like_count": 10, "comment_count": 1,
        "taken_at": NOW - 100, "video_duration": 30.0,
        "caption": {"text": code},
        "video_versions": [{"url": url}],
    }


def _page(items, *, more=True, next_id="cur"):
    return {"num_results": len(items), "more_available": more,
            "next_max_id": next_id, "items": items}


def _client(responses):
    wrapped = [r if isinstance(r, FakeResponse) else FakeResponse(200, r)
               for r in responses]
    t = FakeTransport(wrapped)
    return AnonymousClient(t), t


class _NoNet:
    def __call__(self, *a, **k):
        raise AssertionError("network hit on a no-network path")


def _seed_row(store, handle, *, code, pk, url, fetched_at, plays=1000):
    """Persist one manifest row through the real store write path."""
    reel = normalize_item(_clip(pk, code, url, plays), fetched_at)
    store.write_window(handle, [reel], user_id=USER_ID, next_cursor=None,
                       stop_reason="end_of_feed", mode=FetchMode.TOP_SCAN)


def _assert_anonymous_calls(transport):
    for call in transport.calls:
        assert "authorization" not in {k.lower() for k in call["headers"]}
        assert "sessionid" not in {k.lower() for k in call["cookies"]}
        assert "ds_user_id" not in {k.lower() for k in call["cookies"]}


# --- ftyp signature (the plan-review fix: offset 4, not 0) -------------------

def test_ftyp_verify_offset_4_not_0():
    assert _looks_like_mp4(MP4_BYTES) is True
    # 'ftyp' placed at offset 0 (the size field) must NOT be accepted.
    assert _looks_like_mp4(b"ftyp" + b"\x00" * 16) is False
    assert _looks_like_mp4(b"") is False
    assert _looks_like_mp4(b"\x00\x00\x00\x18moovxxxx") is False


# --- T3.1 store resolver ----------------------------------------------------

def test_find_reel_unions_config_and_disk(tmp_path):
    store = Store(tmp_path / "store")
    _seed_row(store, "alpha", code="AAA", pk=10, url="u1", fetched_at=NOW)
    _seed_row(store, "beta", code="BBB", pk=20, url="u2", fetched_at=NOW)
    # config lists only alpha; beta is discovered from its on-disk CSV.
    hit = store.find_reel("BBB", handles=["alpha"])
    assert hit is not None
    handle, row = hit
    assert handle == "beta"
    assert row["media_id"] == "20"
    assert store.find_reel("MISSING", handles=["alpha"]) is None


# --- T3.6 atomic manifest update --------------------------------------------

def test_update_local_mp4_changes_only_target_row_and_preserves_quoting(tmp_path):
    store = Store(tmp_path / "store")
    # Two rows, one with a comma in the caption (exercises QUOTE_MINIMAL).
    r1 = normalize_item(
        {**_clip(10, "AAA", "http://a"), "caption": {"text": "hello, world"}}, NOW)
    r2 = normalize_item(_clip(20, "BBB", "http://b"), NOW)
    store.write_window("h", [r1, r2], user_id=USER_ID, next_cursor=None,
                       stop_reason="end_of_feed", mode=FetchMode.TOP_SCAN)

    updated = store.update_local_mp4("h", "AAA", local_mp4="/media/h/AAA.mp4",
                                     video_url="http://a-FRESH", fetched_at=NOW + 999)
    assert updated is True

    rows = {r["shortcode"]: r for r in _read_csv(store.csv_path("h"))}
    assert rows["AAA"]["local_mp4"] == "/media/h/AAA.mp4"
    assert rows["AAA"]["video_url"] == "http://a-FRESH"
    assert rows["AAA"]["fetched_at"] == str(NOW + 999)
    assert rows["AAA"]["caption"] == "hello, world"          # quoting round-trips
    # The OTHER row is untouched.
    assert rows["BBB"]["local_mp4"] == ""
    assert rows["BBB"]["video_url"] == "http://b"
    assert rows["BBB"]["fetched_at"] == str(NOW)
    # A missing shortcode is a no-op returning False.
    assert store.update_local_mp4("h", "ZZZ", local_mp4="x") is False


def _read_csv(path):
    with open(path, "r", encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


# --- T3.2 cached-hit gate: provably network-free ----------------------------

def test_cached_hit_returns_without_network(tmp_path):
    config = _config(tmp_path)
    store = Store(config.output.store_dir)
    media = tmp_path / "media" / "h"
    media.mkdir(parents=True)
    mp4 = media / "AAA.mp4"
    mp4.write_bytes(MP4_BYTES)
    _seed_row(store, "h", code="AAA", pk=10, url="http://a", fetched_at=NOW)
    store.update_local_mp4("h", "AAA", local_mp4=str(mp4))

    # _NoNet transport: any network touch raises. Proves the gate is network-free.
    env = run_download_reel("AAA", config=config,
                            client=AnonymousClient(_NoNet()), store=store,
                            now=lambda: NOW)
    assert env["cached"] is True
    assert env["local_mp4"] == str(mp4)
    assert env["partial"] is False
    assert "error" not in env


def test_stale_local_mp4_pointing_at_deleted_file_falls_through(tmp_path):
    config = _config(tmp_path)
    store = Store(config.output.store_dir)
    _seed_row(store, "h", code="AAA", pk=10, url="http://a", fetched_at=NOW)
    store.update_local_mp4("h", "AAA", local_mp4=str(tmp_path / "gone.mp4"))
    # Fresh URL in margin -> one CDN GET, no metadata call.
    client, t = _client([FakeResponse(200, content=MP4_BYTES)])
    env = run_download_reel("AAA", config=config, client=client, store=store,
                            now=lambda: NOW)
    assert env["cached"] is False
    assert env["local_mp4"].endswith("AAA.mp4")
    assert len(t.calls) == 1                       # re-downloaded, did not serve stale


# --- T3.3 freshness: in-margin URL reuse ------------------------------------

def test_in_margin_url_reuse_no_metadata_one_cdn_get(tmp_path):
    config = _config(tmp_path)
    store = Store(config.output.store_dir)
    _seed_row(store, "h", code="AAA", pk=10, url="http://cdn/AAA.mp4", fetched_at=NOW)
    client, t = _client([FakeResponse(200, content=MP4_BYTES)])
    env = run_download_reel("AAA", config=config, client=client, store=store,
                            now=lambda: NOW + 3600)          # 1h old, within margin
    assert env["refreshed"] is False
    assert env["cached"] is False
    assert len(t.calls) == 1                                 # exactly one CDN GET
    cdn = t.calls[0]
    assert cdn["url"] == "http://cdn/AAA.mp4"                 # reused stored URL
    assert cdn["allow_redirects"] is True                    # redirect-follow proven
    _assert_anonymous_calls(t)


# --- T3.4 expired URL -> one re-resolve + download --------------------------

def test_expired_url_triggers_reresolve_then_download(tmp_path):
    config = _config(tmp_path)
    store = Store(config.output.store_dir)
    old = NOW - 2 * URL_REFRESH_MARGIN_SECONDS
    _seed_row(store, "h", code="AAA", pk=10, url="http://stale/AAA.mp4", fetched_at=old)
    client, t = _client([
        _page([_clip(10, "AAA", "http://cdn/AAA-FRESH.mp4")], more=False, next_id=None),
        FakeResponse(200, content=MP4_BYTES),
    ])
    env = run_download_reel("AAA", config=config, client=client, store=store,
                            now=lambda: NOW)
    assert env["refreshed"] is True
    assert env["partial"] is False
    # one metadata (feed) call + one CDN GET
    assert len(t.calls) == 2
    meta, cdn = t.calls
    assert meta["allow_redirects"] is False                  # metadata: no follow
    assert meta["headers"].get("x-ig-app-id") == IG_APP_ID   # required header
    assert cdn["url"] == "http://cdn/AAA-FRESH.mp4"           # fresh URL used
    assert cdn["allow_redirects"] is True
    # manifest persisted the fresh URL + fetched_at so the next call is in-margin.
    row = {r["shortcode"]: r for r in _read_csv(store.csv_path("h"))}["AAA"]
    assert row["video_url"] == "http://cdn/AAA-FRESH.mp4"
    assert row["fetched_at"] == str(NOW)
    assert row["local_mp4"].endswith("AAA.mp4")
    _assert_anonymous_calls(t)


# --- T3.4 STANDING ORDER: re-resolve picks target when NOT first in feed -----

def test_reresolve_matches_identity_not_positional(tmp_path):
    config = _config(tmp_path)
    store = Store(config.output.store_dir)
    old = NOW - 2 * URL_REFRESH_MARGIN_SECONDS
    # target TAR has a LOW pk (a pinned/older reel) — a positional items[0] pick
    # would wrongly grab the newest (NEW1) reel at the top of the feed.
    _seed_row(store, "h", code="TAR", pk=100, url="http://stale/TAR.mp4", fetched_at=old)
    feed = _page([
        _clip(9002, "NEW1", "http://cdn/NEW1.mp4"),   # newest, position 0 (trap)
        _clip(9001, "NEW2", "http://cdn/NEW2.mp4"),
        _clip(100, "TAR", "http://cdn/TAR-FRESH.mp4"),  # the real target, position 2
    ], more=False, next_id=None)
    client, t = _client([feed, FakeResponse(200, content=MP4_BYTES)])
    env = run_download_reel("TAR", config=config, client=client, store=store,
                            now=lambda: NOW)
    assert env["refreshed"] is True
    cdn = t.calls[-1]
    assert cdn["url"] == "http://cdn/TAR-FRESH.mp4"    # identity match, NOT items[0]
    assert cdn["url"] != "http://cdn/NEW1.mp4"


def test_reresolve_by_numeric_media_id_backstop(tmp_path):
    # The seeded shortcode ("OLDCODE") does NOT appear in the feed — the item
    # that carries the target's numeric media_id (pk=555) has a DIFFERENT code
    # ("RENAMED"). So a shortcode match is impossible; only the numeric
    # media_id backstop (pk == media_id) can select it. This proves the
    # non-positional, identity-by-media_id behavior (not code, not items[0]).
    config = _config(tmp_path)
    store = Store(config.output.store_dir)
    old = NOW - 2 * URL_REFRESH_MARGIN_SECONDS
    _seed_row(store, "h", code="OLDCODE", pk=555, url="http://stale.mp4", fetched_at=old)
    feed = _page([
        _clip(9002, "NEW1", "http://cdn/NEW1.mp4"),      # position 0 (positional trap)
        _clip(555, "RENAMED", "http://cdn/BYID.mp4"),    # DIFFERENT code, SAME pk=555
    ], more=False, next_id=None)
    # No feed item shares the seeded shortcode — only pk=555 links them.
    assert all(it["code"] != "OLDCODE" for it in feed["items"])
    client, t = _client([feed, FakeResponse(200, content=MP4_BYTES)])
    env = run_download_reel("OLDCODE", config=config, client=client, store=store,
                            now=lambda: NOW)
    assert env["refreshed"] is True
    # Selected by numeric media_id backstop, NOT by shortcode and NOT positionally.
    assert t.calls[-1]["url"] == "http://cdn/BYID.mp4"
    assert t.calls[-1]["url"] != "http://cdn/NEW1.mp4"


# --- T3.4 stop_signal -> clean typed partial (no exception) ------------------

def test_reresolve_stop_signal_returns_partial(tmp_path):
    config = _config(tmp_path)
    store = Store(config.output.store_dir)
    old = NOW - 2 * URL_REFRESH_MARGIN_SECONDS
    _seed_row(store, "h", code="AAA", pk=10, url="http://stale.mp4", fetched_at=old)
    client, t = _client([FakeResponse(401, {"message": "login_required"})])
    env = run_download_reel("AAA", config=config, client=client, store=store,
                            now=lambda: NOW)
    assert env["partial"] is True
    assert env["stop_reason"] == "rate_limited"
    assert "budget cooling" in env["note"]
    assert env["local_mp4"] is None
    assert len(t.calls) == 1                          # one feed page, then STOP


def test_reresolve_not_found_in_budget_returns_typed_error(tmp_path):
    config = _config(tmp_path, max_pages=1)
    store = Store(config.output.store_dir)
    old = NOW - 2 * URL_REFRESH_MARGIN_SECONDS
    _seed_row(store, "h", code="AAA", pk=10, url="http://stale.mp4", fetched_at=old)
    # feed page WITHOUT the target, and more_available stops the walk.
    client, t = _client([_page([_clip(999, "OTHER", "http://o.mp4")],
                               more=False, next_id=None)])
    env = run_download_reel("AAA", config=config, client=client, store=store,
                            now=lambda: NOW)
    # Aged-out / not-found-in-budget is a CLEAN typed error, NOT a partial:
    # distinct from the metered-cooldown stop_signal case (which IS partial +
    # retryable). partial must be False so a retryability consumer can tell them
    # apart.
    assert env["partial"] is False
    assert "could not re-resolve" in env["error"]
    assert env["local_mp4"] is None


# --- T3.1 unknown shortcode -> typed error ----------------------------------

def test_unknown_shortcode_typed_error_no_network(tmp_path):
    config = _config(tmp_path)
    store = Store(config.output.store_dir)
    env = run_download_reel("NOPE", config=config,
                            client=AnonymousClient(_NoNet()), store=store,
                            now=lambda: NOW)
    assert env["handle"] is None
    assert "not in store" in env["error"]
    assert env["local_mp4"] is None


# --- T3.5 ftyp-verify rejects 0-byte / 302-shaped bodies --------------------

def test_download_rejects_empty_body(tmp_path):
    config = _config(tmp_path)
    store = Store(config.output.store_dir)
    _seed_row(store, "h", code="AAA", pk=10, url="http://cdn/AAA.mp4", fetched_at=NOW)
    client, t = _client([FakeResponse(200, content=b"")])   # 0 bytes
    env = run_download_reel("AAA", config=config, client=client, store=store,
                            now=lambda: NOW)
    assert "download failed" in env["error"]
    assert env["local_mp4"] is None
    # no file was written
    assert not (tmp_path / "media" / "h" / "AAA.mp4").exists()


def test_download_rejects_non_mp4_body(tmp_path):
    config = _config(tmp_path)
    store = Store(config.output.store_dir)
    _seed_row(store, "h", code="AAA", pk=10, url="http://cdn/AAA.mp4", fetched_at=NOW)
    client, t = _client([FakeResponse(200, content=b"<html>302 Found</html>")])
    env = run_download_reel("AAA", config=config, client=client, store=store,
                            now=lambda: NOW)
    assert "download failed" in env["error"]


def test_download_rejects_non_200(tmp_path):
    config = _config(tmp_path)
    store = Store(config.output.store_dir)
    _seed_row(store, "h", code="AAA", pk=10, url="http://cdn/AAA.mp4", fetched_at=NOW)
    client, t = _client([FakeResponse(302, content=b"", headers={"Location": "/x"})])
    env = run_download_reel("AAA", config=config, client=client, store=store,
                            now=lambda: NOW)
    assert "download failed" in env["error"]


# --- happy-path file is actually written with a valid ftyp ------------------

def test_successful_download_writes_valid_mp4(tmp_path):
    config = _config(tmp_path)
    store = Store(config.output.store_dir)
    _seed_row(store, "h", code="AAA", pk=10, url="http://cdn/AAA.mp4", fetched_at=NOW)
    client, t = _client([FakeResponse(200, content=MP4_BYTES)])
    env = run_download_reel("AAA", config=config, client=client, store=store,
                            now=lambda: NOW)
    path = tmp_path / "media" / "h" / "AAA.mp4"
    assert path.exists()
    data = path.read_bytes()
    assert data[4:8] == b"ftyp"                       # valid mp4 signature on disk
    assert env["local_mp4"] == str(path)
    # no leftover temp file
    assert not path.with_suffix(".mp4.tmp").exists()
