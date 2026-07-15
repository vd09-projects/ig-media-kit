"""Skip-seen dedupe (zero dupes on overlap) + durable-first partial persistence."""

from __future__ import annotations

import csv

import pytest

from ig_media_kit.fetch import FetchMode, normalize_item
from ig_media_kit.store import Store
from tests.conftest import load_feed


def _reels():
    feed = load_feed()
    return [r for r in (normalize_item(i, 1) for i in feed["items"]) if r]


def _read_shortcodes(store: Store, handle: str) -> list[str]:
    with store.csv_path(handle).open("r", encoding="utf-8", newline="") as fh:
        return [row["shortcode"] for row in csv.DictReader(fh)]


def test_overlapping_write_adds_zero_duplicates(tmp_path):
    store = Store(tmp_path)
    reels = _reels()
    first = store.write_window("h", reels, user_id="1", mode=FetchMode.TOP_SCAN)
    assert first.persisted == 3
    # Re-write the SAME reels -> all dropped as seen, zero duplicates on disk.
    second = store.write_window("h", reels, user_id="1", mode=FetchMode.TOP_SCAN)
    assert second.persisted == 0
    assert second.skipped_seen == 3
    codes = _read_shortcodes(store, "h")
    assert sorted(codes) == sorted(set(codes))  # no duplicate rows
    assert len(codes) == 3


def test_caption_with_comma_survives_csv_round_trip(tmp_path):
    store = Store(tmp_path)
    store.write_window("h", _reels(), user_id="1", mode=FetchMode.TOP_SCAN)
    with store.csv_path("h").open("r", encoding="utf-8", newline="") as fh:
        rows = {row["shortcode"]: row for row in csv.DictReader(fh)}
    assert rows["DZclip04"]["caption"] == "Newest reel, with a comma, and more"


def test_durable_first_failure_leaves_anchor_unadvanced(tmp_path):
    store = Store(tmp_path)
    reels = _reels()

    class Boom(RuntimeError):
        pass

    # Inject a crash AFTER the CSV fsync but BEFORE the state write.
    with pytest.raises(Boom):
        store.write_window("h", reels, user_id="1", mode=FetchMode.TOP_SCAN,
                           _after_csv_hook=lambda: (_ for _ in ()).throw(Boom()))

    # CSV rows ARE durable (they were flushed first)...
    assert len(_read_shortcodes(store, "h")) == 3
    # ...but the state anchor/cursor never advanced (state write never ran).
    state = store.load_state("h")
    assert state.high_water_media_id is None
    assert state.deep_cursor is None

    # On retry, the persisted rows dedupe (no dup), and NOW the anchor advances.
    retry = store.write_window("h", reels, user_id="1", mode=FetchMode.TOP_SCAN,
                               next_cursor="3300000000000000001_1", stop_reason="end_of_feed")
    assert retry.persisted == 0          # all absorbed by dedupe — none lost, none duped
    assert retry.skipped_seen == 3
    assert store.load_state("h").high_water_media_id is None  # no NEW rows -> no advance
    assert len(_read_shortcodes(store, "h")) == 3


def test_state_yaml_is_atomic_full_write(tmp_path):
    # After a successful write, no leftover .tmp file and state parses cleanly.
    store = Store(tmp_path)
    store.write_window("h", _reels(), user_id="1", next_cursor="c_1",
                       stop_reason="caught_up", mode=FetchMode.TOP_SCAN)
    tmp = store.state_path("h").with_suffix(".yaml.tmp")
    assert not tmp.exists()
    state = store.load_state("h")
    assert state.last_stop_reason == "caught_up"
