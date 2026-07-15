"""Filter + rank over the FULL accumulated reel pool — Task T2.8 (+ T2.0 gate).

Reads the whole stored CSV pool for a handle, applies the optional filters, sorts
by a validated ``sort_by`` whitelist, and returns the top-``count``. This is a
pure store-read + in-memory rank: it performs NO network and NO downloads (it
never touches ``video_url`` beyond passing it through in the result record).

T2.0 field micro-gate (verify-by-pilot) — the exact normalized fields each
filter / sort key reads, confirmed present + typed by the T1.2 live natgeo probe
and captured in ``fetch.normalize_item``:

  * ``min_views``     -> ReelRecord.play_count   (int; may be None/0 — treated 0)
  * ``min_duration``  -> ReelRecord.duration     (float seconds, from
                                                  feed ``video_duration``)
  * ``max_age_days``  -> ReelRecord.taken_at      (int epoch seconds)
  * sort ``play_count``    -> play_count   (desc)
  * sort ``like_count``    -> like_count   (desc)
  * sort ``comment_count`` -> comment_count(desc)
  * sort ``taken_at``      -> taken_at     (desc == most-recent first / recency)

No filter or sort is built on an assumed field — each maps to a column the store
already persists (see ``store.CSV_COLUMNS``).
"""

from __future__ import annotations

import csv
import time
from pathlib import Path
from typing import Any, Callable

# sort_by vocabulary — mirrors yt-media-kit's field set. Every key sorts
# DESCENDING (top of the list = highest count / most recent). The default is
# ``play_count`` (the real "plays" metric that comes from the feed endpoint).
SORT_WHITELIST: frozenset[str] = frozenset(
    {"play_count", "like_count", "comment_count", "taken_at"}
)
DEFAULT_SORT = "play_count"

# Numeric columns coerced on load; everything else stays a string.
_INT_FIELDS = ("media_id", "play_count", "ig_play_count", "like_count",
               "comment_count", "taken_at", "fetched_at")
_FLOAT_FIELDS = ("duration",)


class InvalidSortKey(ValueError):
    """Raised when ``sort_by`` is not in :data:`SORT_WHITELIST`."""


def validate_sort_by(sort_by: str | None) -> str:
    """Return a valid sort key or raise :class:`InvalidSortKey`.

    ``None`` resolves to the default (``play_count``)."""
    if sort_by is None:
        return DEFAULT_SORT
    if sort_by not in SORT_WHITELIST:
        raise InvalidSortKey(
            f"unknown sort_by {sort_by!r}; valid: {sorted(SORT_WHITELIST)}"
        )
    return sort_by


def load_pool(csv_path: str | Path) -> list[dict[str, Any]]:
    """Load the FULL stored pool from a handle's CSV, coercing numeric columns.

    Returns [] if the CSV does not exist yet (cold handle). Never networks."""
    path = Path(csv_path)
    if not path.exists():
        return []
    pool: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            pool.append(_coerce_row(row))
    return pool


def _coerce_row(row: dict[str, str]) -> dict[str, Any]:
    out: dict[str, Any] = dict(row)
    for key in _INT_FIELDS:
        out[key] = _to_int(row.get(key))
    for key in _FLOAT_FIELDS:
        out[key] = _to_float(row.get(key))
    return out


def filter_pool(
    pool: list[dict[str, Any]],
    *,
    min_views: int | None = None,
    min_duration: float | None = None,
    max_age_days: int | None = None,
    now: Callable[[], int] = lambda: int(time.time()),
) -> list[dict[str, Any]]:
    """Apply the optional filters. An unset (``None``) filter is a no-op.

    ``min_views`` -> play_count >=; ``min_duration`` -> duration secs >=;
    ``max_age_days`` -> (now - taken_at) <= days. A reel missing the field a
    filter reads is EXCLUDED by that filter (its value cannot satisfy the bound)."""
    out = pool
    if min_views is not None:
        out = [r for r in out if (r.get("play_count") or 0) >= min_views]
    if min_duration is not None:
        out = [r for r in out if (r.get("duration") is not None
                                  and r["duration"] >= min_duration)]
    if max_age_days is not None:
        cutoff = now() - max_age_days * 86400
        out = [r for r in out if (r.get("taken_at") is not None
                                  and r["taken_at"] >= cutoff)]
    return out


def rank(pool: list[dict[str, Any]], sort_by: str) -> list[dict[str, Any]]:
    """Sort a pool DESCENDING by ``sort_by`` (validated). Missing values sort
    as 0 (bottom). Stable within ties (shortcode as a deterministic tiebreak)."""
    key = validate_sort_by(sort_by)
    return sorted(
        pool,
        key=lambda r: ((r.get(key) or 0), r.get("shortcode") or ""),
        reverse=True,
    )


def select_top(
    csv_path: str | Path,
    *,
    count: int,
    sort_by: str | None = None,
    min_views: int | None = None,
    min_duration: float | None = None,
    max_age_days: int | None = None,
    now: Callable[[], int] = lambda: int(time.time()),
) -> list[dict[str, Any]]:
    """Full pipeline: load the pool -> filter -> rank -> take top ``count``.

    ``count`` larger than the filtered pool returns the whole filtered pool.
    Filters that exclude everything return ``[]`` (the caller notes it — not an
    error). Never networks, never downloads."""
    sort_by = validate_sort_by(sort_by)
    pool = load_pool(csv_path)
    filtered = filter_pool(
        pool, min_views=min_views, min_duration=min_duration,
        max_age_days=max_age_days, now=now,
    )
    ranked = rank(filtered, sort_by)
    if count is None or count < 0:
        return ranked
    return ranked[:count]


def _to_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _to_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
