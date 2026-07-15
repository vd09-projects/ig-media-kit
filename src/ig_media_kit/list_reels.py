"""``list_reels`` orchestration — Flow A (Task T2).

The interactive, fast, NEVER-BLOCKING surface. On a call it:

  T2.2  serve-from-store fast path — if ``fresh_fetch`` is off AND coverage is
        CONTIGUOUS (single segment reaching scan_depth or terminal), skip ALL
        network and rank the stored pool.
  T2.3  a single-call page-budget governor (<= max_pages_per_call combined,
        sourced from the SAME config knob as the fetch primitive, sleep=None) so
        top-check + deepen together never exceed the metered budget and a busy
        handle cannot starve backfill (>=1 page reserved for deepen).
  T2.4  top-check phase (T1 ``top_scan``) — surfaces genuinely-new reels,
        including reels below a bounded pinned prefix (fetch T2.4a), and advances
        ``high_water_media_id`` to the MAX numeric media_id via the store.
  T2.5  coverage-segment tracking — records a numeric gap if a whole window of
        newer posts appeared (page_cap + provable gap); a pin cannot open one.
  T2.6  deepen phase (T1 ``deep_resume``) — pages OLDER toward scan_depth /
        end_of_feed on the remaining budget; bridges + merges segments.
  T2.7  partial-on-stop_signal — the FIRST stop_signal in EITHER phase ends the
        call with a ranked partial + a typed "budget cooling" note; a stop in
        top-check leaves the reserved deepen page UNSPENT. Never sleeps, never
        polls, never lets an exception reach the MCP client.
  T2.8  filter + rank over the FULL pool (default ``play_count`` desc), top-N.

This path NEVER sleeps and NEVER authenticates — every IG call goes through the
T1 ``AnonymousClient`` (the sole owner of the ``x-ig-app-id`` header).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable

from . import coverage, ranking
from .config import Config
from .fetch import FetchMode, FetchResult, ReelRecord, StopKind, fetch_window, resolve_user_id
from .http_client import STOP_SIGNAL_REASONS, AnonymousClient
from .store import Store

# Envelope semantics — ``complete`` == coverage_contiguous: a single contiguous
# segment reaching scan_depth OR the account's real end (terminal). It is NEVER
# "pool_depth >= scan_depth" when a gap (>1 segment) is present.
_COMPLETE_DOC = "single contiguous segment reaching scan_depth OR the account's real end"


@dataclass
class _Params:
    count: int
    sort_by: str
    min_views: int | None
    min_duration: float | None
    max_age_days: int | None
    scan_depth: int
    fresh_fetch: bool


class PageBudget:
    """Shared per-call feed-page budget across BOTH phases (T2.3)."""

    def __init__(self, total: int) -> None:
        self.total = max(0, total)
        self.used = 0

    @property
    def remaining(self) -> int:
        return max(0, self.total - self.used)

    def spend(self, pages: int) -> None:
        self.used += max(0, pages)


def _resolve_params(config: Config, overrides: dict[str, Any]) -> _Params:
    top = config.top_reels
    def pick(key: str, default: Any) -> Any:
        val = overrides.get(key)
        return default if val is None else val
    return _Params(
        count=pick("count", top.count),
        sort_by=pick("sort_by", top.sort_by),
        min_views=pick("min_views", top.min_play_count),
        min_duration=pick("min_duration", top.min_duration),
        max_age_days=pick("max_age_days", top.max_age_days),
        scan_depth=pick("scan_depth", config.fetch.scan_depth),
        fresh_fetch=bool(overrides.get("fresh_fetch", False)),
    )


def _validate(p: _Params) -> str | None:
    """Return an error string if args are invalid, else None. Rejects an unknown
    sort_by and negative numeric bounds with a clear message (no traceback)."""
    if p.sort_by not in ranking.SORT_WHITELIST:
        return (f"invalid sort_by {p.sort_by!r}; valid: "
                f"{sorted(ranking.SORT_WHITELIST)}")
    for name, val in (("count", p.count), ("scan_depth", p.scan_depth),
                      ("min_views", p.min_views), ("min_duration", p.min_duration),
                      ("max_age_days", p.max_age_days)):
        if val is not None and val < 0:
            return f"invalid {name}={val!r}: must be non-negative"
    return None


def run_list_reels(
    handle: str,
    *,
    config: Config,
    count: int | None = None,
    sort_by: str | None = None,
    min_views: int | None = None,
    min_duration: float | None = None,
    max_age_days: int | None = None,
    scan_depth: int | None = None,
    fresh_fetch: bool = False,
    client: AnonymousClient | None = None,
    store: Store | None = None,
    now: Callable[[], int] = lambda: int(time.time()),
) -> dict[str, Any]:
    """Run one ``list_reels`` call and return the result envelope (T2.1).

    Synchronous, never sleeps, <= ``config.fetch.max_pages_per_call`` feed pages
    across both phases, returns a ranked partial on the first stop_signal."""
    store = store or Store(config.output.store_dir)
    p = _resolve_params(config, {
        "count": count, "sort_by": sort_by, "min_views": min_views,
        "min_duration": min_duration, "max_age_days": max_age_days,
        "scan_depth": scan_depth, "fresh_fetch": fresh_fetch,
    })

    err = _validate(p)
    if err is not None:
        return _envelope(handle, None, p, store, segments=[],
                         pages_fetched=0, partial=False, note=f"error: {err}",
                         now=now, error=err)

    state = store.load_state(handle)
    segments = state.coverage_segments
    pool_depth = store.count_reels(handle)
    contiguous = coverage.is_contiguous(
        segments, pool_depth=pool_depth, scan_depth=p.scan_depth,
    )

    # --- T2.2 serve-from-store fast path (gate on CONTIGUITY, not raw count) ---
    if not p.fresh_fetch and contiguous:
        note = f"served from store ({pool_depth} reels, coverage complete)"
        return _envelope(handle, state.user_id, p, store, segments=segments,
                         pages_fetched=0, partial=False, note=note, now=now)

    # --- network path: top-check (+ deepen) under the shared budget governor ---
    client = client or AnonymousClient()
    seen = store.load_seen(handle)

    user_id = state.user_id
    if not user_id:
        resolved = resolve_user_id(client, handle)
        if not resolved.ok:
            reason = resolved.stop_reason.value if resolved.stop_reason else "unknown"
            store.write_window(handle, [], user_id=None, stop_reason=reason,
                               mode=FetchMode.TOP_SCAN)
            note = _cooling_note(reason, pool_depth)
            return _envelope(handle, None, p, store, segments=segments,
                             pages_fetched=0, partial=True, note=note,
                             stop_reason=reason, now=now)
        user_id = resolved.user_id

    budget = PageBudget(config.fetch.max_pages_per_call)
    # Reserve >=1 page for deepen while the pool is not yet contiguous-to-depth,
    # so a busy handle's top-check cannot starve backfill forever.
    reserve_deepen = not contiguous
    topcheck_cap = max(1, budget.total - 1) if reserve_deepen else budget.total

    # --- T2.4 top-check phase ---
    top = fetch_window(
        client, user_id, mode=FetchMode.TOP_SCAN, seen=seen,
        high_water_media_id=state.high_water_media_id, max_pages=topcheck_cap,
        sleep=None,  # SYNC PATH: never sleeps.
    )
    budget.spend(top.pages_fetched)
    prior_high_water = state.high_water_media_id
    top_write = store.write_window(
        handle, top.reels, user_id=user_id, next_cursor=top.next_cursor,
        stop_reason=top.stop_reason, mode=FetchMode.TOP_SCAN,
    )
    segments = coverage.seed_or_extend_top(
        segments, top, persisted_media_ids=[r.media_id for r in top.reels],
        prior_high_water=prior_high_water,
    )

    pages_fetched = top.pages_fetched
    partial = top.partial
    stop_reason = top.stop_reason

    # --- T2.7 stop_signal in top-check aborts the WHOLE call (deepen unspent) ---
    if not top.partial:
        pool_depth = store.count_reels(handle)
        contiguous = coverage.is_contiguous(
            segments, pool_depth=pool_depth, scan_depth=p.scan_depth,
        )
        deepen_needed = (
            not contiguous
            and coverage.has_more_to_fetch(segments)
            and budget.remaining > 0
        )
        if deepen_needed:
            deep = _run_deepen(client, user_id, segments, store, handle,
                               pool_depth=pool_depth, scan_depth=p.scan_depth,
                               max_pages=budget.remaining)
            if deep is not None:
                deep_result, segments = deep
                budget.spend(deep_result.pages_fetched)
                pages_fetched += deep_result.pages_fetched
                if deep_result.partial:
                    partial = True
                    stop_reason = deep_result.stop_reason

    # Persist the (additive) coverage segments; T1 fields already written above.
    store.save_coverage_segments(handle, segments)

    pool_depth = store.count_reels(handle)
    contiguous = coverage.is_contiguous(
        segments, pool_depth=pool_depth, scan_depth=p.scan_depth,
    )
    note = _compose_note(partial, stop_reason, pool_depth, len(segments), contiguous)
    return _envelope(handle, user_id, p, store, segments=segments,
                     pages_fetched=pages_fetched, partial=partial, note=note,
                     stop_reason=stop_reason, now=now)


def _run_deepen(
    client: AnonymousClient,
    user_id: str,
    segments: list[coverage.Segment],
    store: Store,
    handle: str,
    *,
    pool_depth: int,
    scan_depth: int,
    max_pages: int,
) -> tuple[FetchResult, list[coverage.Segment]] | None:
    """Run one deepen pass on the front-most workable segment. Returns the fetch
    result + updated segments, or None if there is nothing to deepen."""
    target_idx = coverage.segment_to_deepen(segments)
    if target_idx is None:
        return None
    resume_cursor = segments[target_idx].get("resume_cursor")
    if not resume_cursor:
        return None
    depth_target = max(0, scan_depth - pool_depth)
    if depth_target == 0:
        return None

    deep = fetch_window(
        client, user_id, mode=FetchMode.DEEP_RESUME, start_cursor=resume_cursor,
        depth_target=depth_target, max_pages=max_pages,
        sleep=None,  # SYNC PATH: never sleeps.
    )
    store.write_window(
        handle, deep.reels, user_id=user_id, next_cursor=deep.next_cursor,
        stop_reason=deep.stop_reason, mode=FetchMode.DEEP_RESUME,
    )
    segments = coverage.apply_deepen(
        segments, target_idx, deep,
        persisted_media_ids=[r.media_id for r in deep.reels],
    )
    return deep, segments


# --- envelope + notes -------------------------------------------------------


def _envelope(
    handle: str,
    user_id: str | None,
    p: _Params,
    store: Store,
    *,
    segments: list[coverage.Segment],
    pages_fetched: int,
    partial: bool,
    note: str,
    now: Callable[[], int],
    stop_reason: str | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    pool_depth = store.count_reels(handle)
    reels: list[dict[str, Any]] = []
    if error is None:
        reels = ranking.select_top(
            store.csv_path(handle), count=p.count, sort_by=p.sort_by,
            min_views=p.min_views, min_duration=p.min_duration,
            max_age_days=p.max_age_days, now=now,
        )
    complete = coverage.is_contiguous(
        segments, pool_depth=pool_depth, scan_depth=p.scan_depth,
    )
    env: dict[str, Any] = {
        "handle": handle,
        "user_id": user_id,
        "reels": reels,
        "count_returned": len(reels),
        "partial": partial,
        "note": note,
        "pool_depth": pool_depth,
        "coverage": {  # complete == coverage_contiguous (see _COMPLETE_DOC)
            "complete": complete,
            "complete_means": _COMPLETE_DOC,
            "segments": len(segments),
            "pool_depth": pool_depth,
        },
        "pages_fetched": pages_fetched,
        "stop_reason": stop_reason,
        "sort_by": p.sort_by,
        "scan_depth": p.scan_depth,
    }
    if error is not None:
        env["error"] = error
    return env


def _compose_note(
    partial: bool, stop_reason: str, pool_depth: int, segments: int, contiguous: bool
) -> str:
    if partial:
        return _cooling_note(stop_reason, pool_depth)
    if contiguous:
        return f"coverage complete ({pool_depth} reels)"
    if segments > 1:
        return (f"incomplete coverage: {segments} segments — converging "
                f"({pool_depth} reels)")
    return f"converging: {pool_depth} reels, deepening toward scan_depth"


def _cooling_note(stop_reason: str, pool_depth: int) -> str:
    if stop_reason in STOP_SIGNAL_REASONS:
        return (f"budget cooling — IG {stop_reason}; returned from the "
                f"{pool_depth} stored reels; retry after a few minutes")
    return f"stopped ({stop_reason}); returned from the {pool_depth} stored reels"
