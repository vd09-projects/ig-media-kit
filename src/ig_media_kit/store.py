"""Store layer — CSV manifest + YAML state, skip-seen dedupe, durable-first
partial persistence.

Task T1.6. The store is the source of truth and is NEVER destructively capped.

Design decisions (stated per T1.6):
  * ``seen`` is DERIVED from the CSV's ``shortcode`` column on load — NOT
    duplicated in YAML — so the CSV stays the single source of truth. Membership
    is O(1) against an in-memory set.
  * CSV uses the ``csv`` module with proper (minimal) quoting; a caption
    containing commas or newlines is quoted correctly, so no TSV fallback is
    needed. (Decision: proper quoting over TSV — one format, robust to commas.)
  * DURABLE-FIRST write order on every window (including a partial stop):
    (a) append + fsync the CSV rows for items actually normalized;
    (b) ONLY THEN advance high_water_media_id / deep_cursor for PERSISTED items;
    (c) write state.yaml atomically via temp-file + os.replace.
    A crash between (a) and (c) re-fetches already-persisted rows (dedupe
    absorbs them) — never a skipped-forever reel.
"""

from __future__ import annotations

import csv
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Sequence

import yaml

from .fetch import FetchMode, ReelRecord

# CSV column order — token-lean, mirroring yt-media-kit, carrying BOTH the
# shortcode (identity/dedupe key) AND media_id (numeric ordered anchor).
CSV_COLUMNS: tuple[str, ...] = (
    "shortcode",
    "media_id",
    "play_count",
    "ig_play_count",
    "like_count",
    "comment_count",
    "caption",
    "taken_at",
    "duration",
    "product_type",
    "video_url",
    "local_mp4",
    "fetched_at",
)


@dataclass
class State:
    """Per-handle YAML state. Field roles are disambiguated:
    ``high_water_media_id`` + the derived ``seen`` set govern "caught up to new
    posts?" (top_scan); ``deep_cursor`` governs "how far back have we backfilled?"
    (deep_resume). They advance independently."""

    user_id: str | None = None
    high_water_media_id: int | None = None   # numeric pk of newest reel ingested
    deep_cursor: str | None = None           # next_max_id toward scan_depth
    last_stop_reason: str | None = None


@dataclass
class WriteResult:
    persisted: int            # rows actually appended (post-dedupe)
    skipped_seen: int         # rows dropped as already-seen
    high_water_media_id: int | None
    deep_cursor: str | None


class Store:
    """Flat-file store rooted at ``store_dir``."""

    def __init__(self, store_dir: str | os.PathLike[str] = "./store") -> None:
        self.store_dir = Path(store_dir)

    # --- paths ---
    def csv_path(self, handle: str) -> Path:
        return self.store_dir / f"{handle}.csv"

    def state_path(self, handle: str) -> Path:
        return self.store_dir / f"{handle}.state.yaml"

    # --- reads ---
    def load_state(self, handle: str) -> State:
        path = self.state_path(handle)
        if not path.exists():
            return State()
        with path.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        return State(
            user_id=data.get("user_id"),
            high_water_media_id=_as_int(data.get("high_water_media_id")),
            deep_cursor=data.get("deep_cursor"),
            last_stop_reason=data.get("last_stop_reason"),
        )

    def load_seen(self, handle: str) -> set[str]:
        """Derive the seen-shortcode set from the CSV (the source of truth)."""
        path = self.csv_path(handle)
        if not path.exists():
            return set()
        seen: set[str] = set()
        with path.open("r", encoding="utf-8", newline="") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                sc = row.get("shortcode")
                if sc:
                    seen.add(sc)
        return seen

    def count_reels(self, handle: str) -> int:
        return len(self.load_seen(handle))

    # --- durable-first write (T1.6) ---
    def write_window(
        self,
        handle: str,
        reels: Sequence[ReelRecord],
        *,
        user_id: str | None = None,
        next_cursor: str | None = None,
        stop_reason: str | None = None,
        mode: FetchMode = FetchMode.TOP_SCAN,
        _after_csv_hook: Callable[[], None] | None = None,
    ) -> WriteResult:
        """Persist a window durable-first. Returns what was actually persisted.

        ``_after_csv_hook`` is a test-only seam fired AFTER the CSV fsync and
        BEFORE the state write, to prove that an interruption there leaves the
        anchor/cursor un-advanced (the rows re-appear and dedupe on retry).
        """
        self.store_dir.mkdir(parents=True, exist_ok=True)

        # Dedupe against what is already persisted (skip-seen).
        existing_seen = self.load_seen(handle)
        new_reels = [r for r in reels if r.shortcode not in existing_seen]
        skipped = len(reels) - len(new_reels)

        # (a) append CSV rows and fsync — DURABLE FIRST.
        if new_reels:
            self._append_csv(handle, new_reels)

        # Test-only fault injection point: between durable CSV and state write.
        if _after_csv_hook is not None:
            _after_csv_hook()

        # (b) advance anchors for PERSISTED items only.
        state = self.load_state(handle)
        if user_id:
            state.user_id = user_id
        persisted_media_ids = [r.media_id for r in new_reels]
        if persisted_media_ids:
            candidate = max(persisted_media_ids)
            state.high_water_media_id = max(state.high_water_media_id or 0, candidate)
        # deep_cursor policy: deep_resume always advances deeper; top_scan seeds
        # it once (only when absent) so a later deep-backfill caller can start.
        if next_cursor:
            if mode is FetchMode.DEEP_RESUME or state.deep_cursor is None:
                state.deep_cursor = next_cursor
        if stop_reason is not None:
            state.last_stop_reason = stop_reason

        # (c) atomic state write.
        self._write_state_atomic(handle, state)

        return WriteResult(
            persisted=len(new_reels),
            skipped_seen=skipped,
            high_water_media_id=state.high_water_media_id,
            deep_cursor=state.deep_cursor,
        )

    # --- internals ---
    def _append_csv(self, handle: str, reels: Iterable[ReelRecord]) -> None:
        path = self.csv_path(handle)
        write_header = not path.exists()
        with path.open("a", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=CSV_COLUMNS, quoting=csv.QUOTE_MINIMAL)
            if write_header:
                writer.writeheader()
            for reel in reels:
                writer.writerow(_reel_to_row(reel))
            fh.flush()
            os.fsync(fh.fileno())

    def _write_state_atomic(self, handle: str, state: State) -> None:
        """Write state.yaml via temp-file + os.replace (atomic rename). The
        canonical file is only ever observed fully-written, never half-written."""
        path = self.state_path(handle)
        payload = {
            "user_id": state.user_id,
            "high_water_media_id": state.high_water_media_id,
            "deep_cursor": state.deep_cursor,
            "last_stop_reason": state.last_stop_reason,
        }
        tmp = path.with_suffix(path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            yaml.safe_dump(payload, fh, default_flow_style=False, sort_keys=True)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)


def _reel_to_row(reel: ReelRecord) -> dict[str, object]:
    return {
        "shortcode": reel.shortcode,
        "media_id": reel.media_id,
        "play_count": _blank_if_none(reel.play_count),
        "ig_play_count": _blank_if_none(reel.ig_play_count),
        "like_count": _blank_if_none(reel.like_count),
        "comment_count": _blank_if_none(reel.comment_count),
        "caption": reel.caption,
        "taken_at": _blank_if_none(reel.taken_at),
        "duration": _blank_if_none(reel.duration),
        "product_type": reel.product_type,
        "video_url": reel.video_url or "",
        "local_mp4": "",  # filled by the downloader tool (later ticket)
        "fetched_at": reel.fetched_at,
    }


def _blank_if_none(value: object) -> object:
    return "" if value is None else value


def _as_int(value: object) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
