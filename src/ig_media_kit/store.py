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
    (deep_resume). They advance independently.

    ``coverage_segments`` (T2.5, additive — T1 fields untouched) records the
    contiguous [newest_media_id, oldest_media_id, resume_cursor, terminal] spans
    the store has actually covered. Normally ONE segment (top -> deep_cursor); a
    burst of >1 window of genuinely-newer posts can open a 2nd, which deepen
    later bridges + merges. It is a list of plain dicts so it round-trips through
    YAML without custom tags."""

    user_id: str | None = None
    high_water_media_id: int | None = None   # numeric pk of newest reel ingested
    deep_cursor: str | None = None           # next_max_id toward scan_depth
    last_stop_reason: str | None = None
    coverage_segments: list[dict] = field(default_factory=list)


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
        segments = data.get("coverage_segments") or []
        return State(
            user_id=data.get("user_id"),
            high_water_media_id=_as_int(data.get("high_water_media_id")),
            deep_cursor=data.get("deep_cursor"),
            last_stop_reason=data.get("last_stop_reason"),
            coverage_segments=[dict(s) for s in segments if isinstance(s, dict)],
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

    # --- shortcode resolution (T3.1) ---
    def handles_on_disk(self) -> list[str]:
        """Every handle that already has a manifest CSV in ``store_dir``.

        The ``<handle>.state.yaml`` sidecars never match ``*.csv`` so they are
        naturally excluded. Sorted for a deterministic scan order."""
        if not self.store_dir.exists():
            return []
        return sorted(p.stem for p in self.store_dir.glob("*.csv"))

    def find_reel(
        self, shortcode: str, *, handles: Iterable[str] = ()
    ) -> tuple[str, dict[str, str]] | None:
        """Locate the owning handle + manifest row for a bare ``shortcode``.

        Candidate handles = the passed ``handles`` (config channels) UNIONED with
        every handle that has a CSV on disk (a reel may sit in the store from a
        prior ``list_reels`` even after its channel was dropped from config),
        de-duplicated with config order first. A reel has exactly one owner, so
        the FIRST row whose ``shortcode`` column matches wins. Returns
        ``(handle, row_dict)`` or ``None``. NO network — a pure CSV read."""
        candidates = list(dict.fromkeys([*handles, *self.handles_on_disk()]))
        for handle in candidates:
            row = self._find_row(handle, shortcode)
            if row is not None:
                return handle, row
        return None

    def _find_row(self, handle: str, shortcode: str) -> dict[str, str] | None:
        path = self.csv_path(handle)
        if not path.exists():
            return None
        with path.open("r", encoding="utf-8", newline="") as fh:
            for row in csv.DictReader(fh):
                if row.get("shortcode") == shortcode:
                    return dict(row)
        return None

    # --- atomic manifest local_mp4 / freshness update (T3.6) ---
    def update_local_mp4(
        self,
        handle: str,
        shortcode: str,
        *,
        local_mp4: str,
        video_url: str | None = None,
        fetched_at: int | None = None,
    ) -> bool:
        """Set ``local_mp4`` (and optionally refresh ``video_url`` + ``fetched_at``)
        for one ``(handle, shortcode)`` row, rewriting the CSV ATOMICALLY.

        The whole manifest is written to a temp file then ``os.replace``-d in —
        the same discipline as ``_write_state_atomic`` — so the CSV is never
        observed half-written. Header, column order (``CSV_COLUMNS``), and every
        OTHER row/column are preserved verbatim (``csv.DictWriter`` with
        ``QUOTE_MINIMAL``, exactly as ``_append_csv`` — caption-comma quoting is
        preserved). When T3.4 produced a fresh signed URL, ``video_url`` +
        ``fetched_at`` are refreshed IN THE SAME rewrite so the next call sees an
        in-margin URL and skips a needless re-resolve. Returns True iff the row
        was found and updated; False if the shortcode is not in this handle's CSV
        (no file is written in that case)."""
        path = self.csv_path(handle)
        if not path.exists():
            return False
        with path.open("r", encoding="utf-8", newline="") as fh:
            rows = list(csv.DictReader(fh))

        found = False
        for row in rows:
            if row.get("shortcode") == shortcode:
                row["local_mp4"] = local_mp4
                if video_url is not None:
                    row["video_url"] = video_url
                if fetched_at is not None:
                    row["fetched_at"] = str(fetched_at)
                found = True
                break
        if not found:
            return False

        tmp = path.with_suffix(path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=CSV_COLUMNS, quoting=csv.QUOTE_MINIMAL)
            writer.writeheader()
            for row in rows:
                # Restrict to known columns so an unexpected extra key can't wedge
                # DictWriter; every CSV_COLUMNS key is present in a store-written row.
                writer.writerow({col: row.get(col, "") for col in CSV_COLUMNS})
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
        return True

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

    def save_coverage_segments(self, handle: str, segments: Sequence[dict]) -> None:
        """Persist ``coverage_segments`` (T2.5) atomically, preserving every T1
        state field. Loads the current state first so the anchor/cursor written
        by an immediately-preceding ``write_window`` are not clobbered."""
        self.store_dir.mkdir(parents=True, exist_ok=True)
        state = self.load_state(handle)
        state.coverage_segments = [dict(s) for s in segments]
        self._write_state_atomic(handle, state)

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
            "coverage_segments": [dict(s) for s in state.coverage_segments],
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
