"""T2.5 — coverage-segment tracking: numeric gap predicate, phantom-segment
guard, deepen bridge/merge, contiguity, and state.yaml round-trip."""

from __future__ import annotations

from ig_media_kit import coverage
from ig_media_kit.fetch import FetchResult, StopKind
from ig_media_kit.store import Store


def _result(stop_reason: str, next_cursor: str | None = "cur") -> FetchResult:
    r = FetchResult()
    r.stop_reason = stop_reason
    r.next_cursor = next_cursor
    return r


# --- seed + steady state ----------------------------------------------------

def test_cold_seed_creates_single_segment():
    segs = coverage.seed_or_extend_top(
        [], _result(StopKind.PAGE_CAP.value, "c1"),
        persisted_media_ids=[500, 510, 520], prior_high_water=None,
    )
    assert len(segs) == 1
    assert segs[0]["newest_media_id"] == 520
    assert segs[0]["oldest_media_id"] == 500
    assert segs[0]["resume_cursor"] == "c1"
    assert segs[0]["terminal"] is False


def test_end_of_feed_seed_is_terminal():
    segs = coverage.seed_or_extend_top(
        [], _result(StopKind.END_OF_FEED.value, None),
        persisted_media_ids=[10, 20], prior_high_water=None,
    )
    assert segs[0]["terminal"] is True


def test_caught_up_topcheck_no_new_leaves_segments_unchanged():
    before = [coverage._segment(520, 500, "c1", False)]
    after = coverage.seed_or_extend_top(
        before, _result(StopKind.CAUGHT_UP.value), persisted_media_ids=[],
        prior_high_water=520,
    )
    assert after == before


def test_small_new_batch_extends_top_segment_no_gap():
    before = [coverage._segment(520, 500, "c1", False)]
    # New reels 521,522 are contiguous above the top; caught_up (not page_cap).
    after = coverage.seed_or_extend_top(
        before, _result(StopKind.CAUGHT_UP.value),
        persisted_media_ids=[521, 522], prior_high_water=520,
    )
    assert len(after) == 1
    assert after[0]["newest_media_id"] == 522


# --- gap predicate (numeric) ------------------------------------------------

def test_burst_over_one_window_opens_second_segment():
    before = [coverage._segment(520, 500, "c1", False)]
    # page_cap (walked the whole budget) AND every new pk > prior newest (520)
    # -> a whole window of newer posts with a provable numeric gap.
    after = coverage.seed_or_extend_top(
        before, _result(StopKind.PAGE_CAP.value, "c2"),
        persisted_media_ids=[900, 905, 910], prior_high_water=520,
    )
    assert len(after) == 2
    assert after[0]["newest_media_id"] == 910
    assert after[0]["oldest_media_id"] == 900
    assert after[0]["resume_cursor"] == "c2"     # valid resume cursor recorded
    assert after[1]["newest_media_id"] == 520     # prior segment preserved


def test_pin_cannot_open_phantom_segment_low_pk():
    # A page led by an UN-seen pin (low pk) among newer items: batch MIN is the
    # pin's low pk, so batch_min > prior_newest is FALSE -> no new segment.
    before = [coverage._segment(520, 500, "c1", False)]
    after = coverage.seed_or_extend_top(
        before, _result(StopKind.PAGE_CAP.value, "c2"),
        persisted_media_ids=[105, 900, 905], prior_high_water=520,
    )
    assert len(after) == 1   # phantom guard held


def test_no_gap_when_not_page_cap():
    # Even if all new pks > prior newest, without page_cap there is no gap (we
    # caught up within budget) -> extend, don't open.
    before = [coverage._segment(520, 500, "c1", False)]
    after = coverage.seed_or_extend_top(
        before, _result(StopKind.CAUGHT_UP.value),
        persisted_media_ids=[900, 905], prior_high_water=520,
    )
    assert len(after) == 1
    assert after[0]["newest_media_id"] == 905


# --- deepen bridge / merge --------------------------------------------------

def test_deepen_extends_single_segment():
    segs = [coverage._segment(520, 500, "c1", False)]
    after = coverage.apply_deepen(
        segs, 0, _result(StopKind.PAGE_CAP.value, "c1b"),
        persisted_media_ids=[480, 490],
    )
    assert len(after) == 1
    assert after[0]["oldest_media_id"] == 480
    assert after[0]["resume_cursor"] == "c1b"


def test_deepen_bridges_and_merges_two_segments():
    # front [910..900], back [520..500]. Deepen the front down to pk 515, which
    # crosses <= back.newest (520) -> the two merge into one.
    segs = [coverage._segment(910, 900, "cf", False),
            coverage._segment(520, 500, "cb", False)]
    after = coverage.apply_deepen(
        segs, 0, _result(StopKind.PAGE_CAP.value, "cf2"),
        persisted_media_ids=[600, 515],
    )
    assert len(after) == 1
    assert after[0]["newest_media_id"] == 910
    assert after[0]["oldest_media_id"] == 500
    assert after[0]["resume_cursor"] == "cb"     # inherits the back segment's cursor


def test_deepen_end_of_feed_marks_terminal():
    segs = [coverage._segment(520, 500, "c1", False)]
    after = coverage.apply_deepen(
        segs, 0, _result(StopKind.END_OF_FEED.value, None),
        persisted_media_ids=[480],
    )
    assert after[0]["terminal"] is True
    assert after[0]["resume_cursor"] is None


# --- contiguity predicate ---------------------------------------------------

def test_contiguous_single_segment_reaching_depth():
    segs = [coverage._segment(520, 500, "c1", False)]
    assert coverage.is_contiguous(segs, pool_depth=90, scan_depth=90) is True
    assert coverage.is_contiguous(segs, pool_depth=40, scan_depth=90) is False


def test_contiguous_terminal_single_segment_short_account():
    segs = [coverage._segment(520, 500, None, True)]
    assert coverage.is_contiguous(segs, pool_depth=12, scan_depth=90) is True


def test_two_segments_never_contiguous_even_if_deep():
    segs = [coverage._segment(910, 900, "cf", False),
            coverage._segment(520, 500, "cb", False)]
    assert coverage.is_contiguous(segs, pool_depth=200, scan_depth=90) is False


# --- state.yaml round-trip (additive; T1 fields untouched) ------------------

def test_coverage_segments_round_trip(tmp_path):
    store = Store(tmp_path)
    segs = [coverage._segment(910, 900, "cf", False),
            coverage._segment(520, 500, "cb", True)]
    # Seed some T1 state first, then attach coverage segments.
    from ig_media_kit.fetch import FetchMode, normalize_item
    from tests.conftest import load_feed
    reels = [r for r in (normalize_item(i, 1) for i in load_feed()["items"]) if r]
    store.write_window("h", reels, user_id="787132", next_cursor="deepc",
                       stop_reason="page_cap", mode=FetchMode.TOP_SCAN)
    store.save_coverage_segments("h", segs)

    reloaded = store.load_state("h")
    assert reloaded.coverage_segments == segs
    # T1 fields survived the additive coverage write.
    assert reloaded.user_id == "787132"
    assert reloaded.deep_cursor == "deepc"
    assert reloaded.high_water_media_id == 3300000000000000004
