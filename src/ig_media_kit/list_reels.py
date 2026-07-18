"""``list_reels`` orchestration — a pure READ-ONLY query over the local store (T17).

CQRS hard split (decision:
``2026-07-16-list-reels-is-read-only-over-the-store-cqrs-split``): ``list_reels``
is the QUERY side and **NEVER hits Instagram on any code path**. It never sleeps,
never authenticates, never even *attempts* IG. Analysis (the metered fetch that
advances a handle's coverage toward ``scan_depth``) is the COMMAND side and lives
entirely in ``start_batch_fetch`` / the async runner (which calls ``fill.run_fill``)
and ``download_reel``'s >24h re-resolve.

Three readiness states, keyed on coverage EVIDENCE (never a raw store-count vs 90):

  (a) NOT ANALYZED   — no coverage evidence at all (empty pool AND no coverage
                       segments AND no high_water_media_id) -> a typed
                       ``error_kind="not_analyzed"`` envelope steering the caller
                       to run ``start_batch_fetch`` first. No reels, no staleness.
  (b) ANALYZED, SHALLOW/STALE — has coverage evidence but not contiguous ->
                       serve the ranked top-N from the store with ``coverage
                       .complete=False`` + a ``staleness`` block.
  (c) ANALYZED & CONTIGUOUS  — a single segment reaching scan_depth or terminal ->
                       serve with ``coverage.complete=True`` + a ``staleness`` block.

Envelope determinism (frozen-surface contract): ``staleness`` is ALWAYS present on
the served (analyzed) envelope and ALWAYS ABSENT on an error envelope
(not-analyzed OR invalid-params). Error envelopes always carry ``error`` +
``error_kind`` + ``retryable`` (a uniform, machine-branchable error contract shared
with ``download_reel``).

Ranking is unchanged: ``ranking.select_top`` over the FULL deduped pool (the store
is never destructively capped), top-N by ``sort_by`` (default ``play_count`` desc),
with per-shortcode dedupe + numeric ``media_id`` ordering — never positional feed
order.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable

from . import coverage, ranking
from .config import Config
from .store import State, Store

# Envelope semantics — ``complete`` == coverage_contiguous: a single contiguous
# segment reaching scan_depth OR the account's real end (terminal). It is NEVER
# "pool_depth >= scan_depth" when a gap (>1 segment) is present.
_COMPLETE_DOC = "single contiguous segment reaching scan_depth OR the account's real end"

# The stored fbcdn ``video_url`` carries an ``oe=`` expiry ≈ 36 h out. The served
# staleness hint warns when a served row's URL-resolve time (``fetched_at``) is
# older than this TTL, so a consumer knows a download_reel re-resolve may be
# needed. (download_reel itself re-resolves under a ~24 h margin; this is a hint,
# not a gate.)
SIGNED_URL_TTL_SECONDS = 36 * 3600

# Machine-branchable discriminators for the (never-raised) error envelopes.
ERROR_KIND_NOT_ANALYZED = "not_analyzed"
ERROR_KIND_INVALID_PARAMS = "invalid_params"


@dataclass
class _Params:
    count: int
    sort_by: str
    min_views: int | None
    min_duration: float | None
    max_age_days: int | None
    scan_depth: int


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


def _has_been_analyzed(state: State, pool_depth: int) -> bool:
    """Has this handle EVER been analyzed (fetched) at all?

    Keyed on coverage EVIDENCE, explicitly NOT on the store-count-vs-scan_depth
    figure: a handle is "analyzed" if the store holds any reel, OR any coverage
    segment was recorded, OR a high-water media_id was set (a window persisted).
    A handle with 1 shallow reel is analyzed (serve it, state b); only a truly
    untouched handle (empty pool AND no segments AND no high_water) is
    not-analyzed (state a). ``last_analyzed_at`` is intentionally NOT consulted
    here — it is staleness metadata, and a legacy pre-T17 store can be analyzed
    yet carry ``None`` for it."""
    return (
        pool_depth > 0
        or bool(state.coverage_segments)
        or state.high_water_media_id is not None
    )


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
    store: Store | None = None,
    now: Callable[[], int] = lambda: int(time.time()),
) -> dict[str, Any]:
    """Run one ``list_reels`` call and return the result envelope (READ-ONLY).

    Never sleeps, never issues an IG request on ANY path, never raises. Resolves
    params -> validates -> loads state -> branches on readiness into
    {not-analyzed error | served ranked top-N + staleness}."""
    store = store or Store(config.output.store_dir)
    p = _resolve_params(config, {
        "count": count, "sort_by": sort_by, "min_views": min_views,
        "min_duration": min_duration, "max_age_days": max_age_days,
        "scan_depth": scan_depth,
    })

    err = _validate(p)
    if err is not None:
        return _error_envelope(
            handle, p, error=err, error_kind=ERROR_KIND_INVALID_PARAMS,
            note=f"error: {err}",
        )

    state = store.load_state(handle)
    segments = state.coverage_segments
    pool_depth = store.count_reels(handle)

    # --- state (a): NOT ANALYZED -> typed error steering to start_batch_fetch ---
    if not _has_been_analyzed(state, pool_depth):
        return _error_envelope(
            handle, p,
            error="handle not analyzed yet; run start_batch_fetch first",
            error_kind=ERROR_KIND_NOT_ANALYZED,
            note=("no reels stored for this handle yet — run start_batch_fetch to "
                  "fetch it, then list_reels serves the ranked top-N from the store"),
        )

    # --- states (b)/(c): ANALYZED -> serve ranked top-N + staleness metadata ---
    reels = ranking.select_top(
        store.csv_path(handle), count=p.count, sort_by=p.sort_by,
        min_views=p.min_views, min_duration=p.min_duration,
        max_age_days=p.max_age_days, now=now,
    )
    complete = coverage.is_contiguous(
        segments, pool_depth=pool_depth, scan_depth=p.scan_depth,
    )
    return _served_envelope(
        handle, state, p, reels=reels, pool_depth=pool_depth,
        segments=segments, complete=complete, now=now,
    )


# --- envelopes --------------------------------------------------------------


def _served_envelope(
    handle: str,
    state: State,
    p: _Params,
    *,
    reels: list[dict[str, Any]],
    pool_depth: int,
    segments: list[coverage.Segment],
    complete: bool,
    now: Callable[[], int],
) -> dict[str, Any]:
    """The analyzed/served envelope. ``partial``/``pages_fetched``/``stop_reason``
    are constant on this read-only path (no fetch ever happens) but retained so the
    served, error, and wrapper-fallback shapes stay a stable superset. ``staleness``
    is ALWAYS present here and ALWAYS absent on the error envelopes."""
    return {
        "handle": handle,
        "user_id": state.user_id,
        "reels": reels,
        "count_returned": len(reels),
        "partial": False,
        "note": _serve_note(pool_depth, len(segments), complete),
        "pool_depth": pool_depth,
        "coverage": {  # complete == coverage_contiguous (see _COMPLETE_DOC)
            "complete": complete,
            "complete_means": _COMPLETE_DOC,
            "segments": len(segments),
            "pool_depth": pool_depth,
        },
        "pages_fetched": 0,
        "stop_reason": None,
        "sort_by": p.sort_by,
        "scan_depth": p.scan_depth,
        "staleness": _staleness(state, p, reels, pool_depth, now),
    }


def _error_envelope(
    handle: str,
    p: _Params,
    *,
    error: str,
    error_kind: str,
    note: str,
) -> dict[str, Any]:
    """A never-raised, non-retryable typed error envelope mirroring the served
    shape (empty ``reels``, zeroed coverage) plus the uniform ``error`` /
    ``error_kind`` / ``retryable`` discriminators. Deliberately carries NO
    ``staleness`` block (staleness is a served-only, deterministic key)."""
    return {
        "handle": handle,
        "user_id": None,
        "reels": [],
        "count_returned": 0,
        "partial": False,
        "note": note,
        "pool_depth": 0,
        "coverage": {
            "complete": False,
            "complete_means": _COMPLETE_DOC,
            "segments": 0,
            "pool_depth": 0,
        },
        "pages_fetched": 0,
        "stop_reason": None,
        "sort_by": p.sort_by,
        "scan_depth": p.scan_depth,
        "error": error,
        "error_kind": error_kind,
        "retryable": False,
    }


def _staleness(
    state: State,
    p: _Params,
    reels: list[dict[str, Any]],
    pool_depth: int,
    now: Callable[[], int],
) -> dict[str, Any]:
    """Staleness metadata for the served top-N (T17).

      * ``last_analyzed_at`` — epoch of the last window that persisted (from
        State; ``None`` for a legacy pre-T17 store).
      * ``store_count`` / ``scan_depth_target`` — an INFORMATIONAL depth hint
        (how full the pool is vs the effort target); does NOT gate readiness or
        flip ``coverage.complete``.
      * ``signed_url_maybe_expired`` — computed over the SERVED top-N rows only
        (that is what the consumer downloads): True if the oldest served row's
        ``fetched_at`` is older than the ~36 h signed-URL TTL; ``None`` (unknown)
        if nothing was served or no served row carries a ``fetched_at``.
    """
    served_fetched = [
        r.get("fetched_at") for r in reels if r.get("fetched_at") is not None
    ]
    if not served_fetched:
        signed_url_maybe_expired: bool | None = None
    else:
        signed_url_maybe_expired = (now() - min(served_fetched)) >= SIGNED_URL_TTL_SECONDS
    return {
        "last_analyzed_at": state.last_analyzed_at,
        "store_count": pool_depth,
        "scan_depth_target": p.scan_depth,
        "signed_url_maybe_expired": signed_url_maybe_expired,
    }


def _serve_note(pool_depth: int, segments: int, complete: bool) -> str:
    """Human-readable served note. Only the branches reachable on the read-only
    served path remain (the old partial/cooling branches moved to fill.py with the
    network path)."""
    if complete:
        return f"served from store ({pool_depth} reels, coverage complete)"
    if segments > 1:
        return (f"served from store ({pool_depth} reels); incomplete coverage: "
                f"{segments} segments — run start_batch_fetch to converge")
    return (f"served from store ({pool_depth} reels); coverage still shallow — "
            f"run start_batch_fetch to deepen toward scan_depth")
