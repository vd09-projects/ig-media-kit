"""T2.8 — filter + rank over the FULL pool: golden filter/sort checks, top-N,
unknown sort_by rejection, and the no-download invariant."""

from __future__ import annotations

import csv

import pytest

from ig_media_kit import ranking
from ig_media_kit.store import CSV_COLUMNS

NOW = 1_721_000_000  # fixed "now" for age filtering


def _write_pool(path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=CSV_COLUMNS, quoting=csv.QUOTE_MINIMAL)
        w.writeheader()
        for r in rows:
            full = {c: "" for c in CSV_COLUMNS}
            full.update(r)
            w.writerow(full)


def _row(code, pk, plays, likes, comments, taken_at, duration):
    return {"shortcode": code, "media_id": pk, "play_count": plays,
            "like_count": likes, "comment_count": comments, "taken_at": taken_at,
            "duration": duration, "product_type": "clips",
            "video_url": f"https://x.fbcdn.net/{code}.mp4", "fetched_at": 1}


@pytest.fixture
def pool_csv(tmp_path):
    path = tmp_path / "natgeo.csv"
    _write_pool(path, [
        # code   pk    plays   likes  comm   taken_at         dur
        _row("A", 104, 500,    50,    5,     NOW - 10 * 86400, 60.0),
        _row("B", 103, 9000,   10,    99,    NOW - 40 * 86400, 15.0),
        _row("C", 102, 100,    900,   1,     NOW - 2 * 86400,  120.0),
        _row("D", 101, 3000,   300,   30,    NOW - 400 * 86400, 8.0),
    ])
    return path


# --- sort goldens (all DESC) ------------------------------------------------

def test_default_sort_is_play_count_desc(pool_csv):
    got = ranking.select_top(pool_csv, count=10, sort_by=None, now=lambda: NOW)
    assert [r["shortcode"] for r in got] == ["B", "D", "A", "C"]


def test_sort_like_count_desc(pool_csv):
    got = ranking.select_top(pool_csv, count=10, sort_by="like_count", now=lambda: NOW)
    assert [r["shortcode"] for r in got] == ["C", "D", "A", "B"]


def test_sort_comment_count_desc(pool_csv):
    got = ranking.select_top(pool_csv, count=10, sort_by="comment_count", now=lambda: NOW)
    assert [r["shortcode"] for r in got] == ["B", "D", "A", "C"]


def test_sort_taken_at_desc_is_recency(pool_csv):
    got = ranking.select_top(pool_csv, count=10, sort_by="taken_at", now=lambda: NOW)
    assert [r["shortcode"] for r in got] == ["C", "A", "B", "D"]


# --- filter goldens (each in isolation) -------------------------------------

def test_min_views_filter(pool_csv):
    got = ranking.select_top(pool_csv, count=10, min_views=1000, now=lambda: NOW)
    assert {r["shortcode"] for r in got} == {"B", "D"}


def test_min_duration_filter(pool_csv):
    got = ranking.select_top(pool_csv, count=10, min_duration=60.0, now=lambda: NOW)
    assert {r["shortcode"] for r in got} == {"A", "C"}


def test_max_age_days_filter(pool_csv):
    got = ranking.select_top(pool_csv, count=10, max_age_days=30, now=lambda: NOW)
    # only A (10d) and C (2d) are within 30 days; B(40d) and D(400d) excluded.
    assert {r["shortcode"] for r in got} == {"A", "C"}


def test_unset_filters_are_noops(pool_csv):
    got = ranking.select_top(pool_csv, count=10, now=lambda: NOW)
    assert len(got) == 4


def test_filters_excluding_all_return_empty(pool_csv):
    got = ranking.select_top(pool_csv, count=10, min_views=10_000_000, now=lambda: NOW)
    assert got == []


# --- top-N over the pool ----------------------------------------------------

def test_top_n_over_pool(pool_csv):
    got = ranking.select_top(pool_csv, count=2, sort_by="play_count", now=lambda: NOW)
    assert [r["shortcode"] for r in got] == ["B", "D"]


def test_count_larger_than_pool_returns_whole_pool(pool_csv):
    got = ranking.select_top(pool_csv, count=999, now=lambda: NOW)
    assert len(got) == 4


def test_empty_pool_returns_empty(tmp_path):
    assert ranking.select_top(tmp_path / "missing.csv", count=5) == []


# --- sort_by validation -----------------------------------------------------

def test_unknown_sort_by_raises():
    with pytest.raises(ranking.InvalidSortKey):
        ranking.validate_sort_by("view_count")


def test_none_sort_by_defaults_to_play_count():
    assert ranking.validate_sort_by(None) == "play_count"


# --- no-download invariant --------------------------------------------------

def test_ranking_never_touches_network_or_downloads(pool_csv, monkeypatch):
    # ranking must be a pure store read; it must never import/instantiate a
    # client or fetch a video_url. Guard: poison the transport factory.
    import ig_media_kit.http_client as hc

    def _boom():
        raise AssertionError("ranking must not create a network client")

    monkeypatch.setattr(hc, "_default_transport", _boom)
    got = ranking.select_top(pool_csv, count=10, now=lambda: NOW)
    assert len(got) == 4  # produced a ranking with zero network
