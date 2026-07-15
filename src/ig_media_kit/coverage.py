"""Coverage-segment tracking — Task T2.5.

A handle's pool is described by an ordered (newest -> oldest) list of contiguous
segments. Each segment is a span the store has ACTUALLY covered:

    {newest_media_id, oldest_media_id, resume_cursor, terminal}

  * ``newest_media_id`` / ``oldest_media_id`` — numeric pk bounds of the span.
  * ``resume_cursor`` — the ``next_max_id`` to page OLDER from (None if the
    segment is terminal / has nothing more below it).
  * ``terminal`` — True once paging this segment hit ``end_of_feed`` (the
    account's real bottom).

Normally there is exactly ONE segment (top of feed -> deep cursor). A NEW segment
is opened ONLY when a top-check walks the whole page budget WITHOUT catching up
(``stop_reason == page_cap``) AND the newly-collected items are provably a whole
window of genuinely-NEWER posts sitting above a numeric gap. Deepen bridges +
merges segments so they converge back to one.

Coverage is CONTIGUOUS (the serve-from-store gate + the envelope ``complete``
flag) when there is a single segment that either reaches ``scan_depth`` or is
terminal at the account's real end.

All predicates are in NUMERIC media_id terms. A pinned reel (older, smaller pk)
can never open a phantom segment: pins are already-seen so they are skipped by
dedupe and never enter the newly-collected set; and even on a cold first fetch an
un-seen pin's LOW media_id lowers the batch MIN, making the
``batch_min > prior_newest`` gap predicate FALSE.
"""

from __future__ import annotations

from typing import Any

from .fetch import FetchResult, StopKind

Segment = dict[str, Any]


def _segment(
    newest: int, oldest: int, resume_cursor: str | None, terminal: bool
) -> Segment:
    return {
        "newest_media_id": int(newest),
        "oldest_media_id": int(oldest),
        "resume_cursor": resume_cursor,
        "terminal": bool(terminal),
    }


def is_terminal(stop_reason: str) -> bool:
    return stop_reason == StopKind.END_OF_FEED.value


def seed_or_extend_top(
    segments: list[Segment],
    result: FetchResult,
    *,
    persisted_media_ids: list[int],
    prior_high_water: int | None,
) -> list[Segment]:
    """Fold a TOP-CHECK result into the coverage segments.

    Cases:
      * No segments yet (cold) AND something persisted -> seed segment 0.
      * A prior top segment exists and the fetch stopped ``caught_up`` /
        ``end_of_feed`` / ``page_cap`` with the new items merging INTO the top
        span (no numeric gap) -> extend segment 0's newest bound.
      * ``page_cap`` with a provable numeric gap above the prior top
        (``min(new) > prior segment newest``) -> OPEN a new front segment.
      * Nothing persisted -> segments unchanged.
    """
    segs = [dict(s) for s in segments]
    if not persisted_media_ids:
        return segs

    batch_newest = max(persisted_media_ids)
    batch_oldest = min(persisted_media_ids)

    if not segs:
        # Cold seed. terminal only if the walk actually reached end_of_feed.
        return [
            _segment(
                batch_newest, batch_oldest, result.next_cursor,
                terminal=is_terminal(result.stop_reason),
            )
        ]

    top = segs[0]
    prior_newest = top["newest_media_id"]

    # GAP predicate (numeric): a whole window of genuinely-newer posts appeared
    # AND the fetch walked the full budget without catching up. A pin cannot
    # satisfy batch_oldest > prior_newest (its low pk lowers batch_oldest).
    opened_gap = (
        result.stop_reason == StopKind.PAGE_CAP.value
        and batch_oldest > prior_newest
    )
    if opened_gap:
        new_front = _segment(
            batch_newest, batch_oldest, result.next_cursor, terminal=False
        )
        return [new_front, *segs]

    # No gap: the new items are contiguous with the existing top span. Advance
    # the top segment's newest bound (never backward).
    top["newest_media_id"] = max(prior_newest, batch_newest)
    if batch_oldest < top["oldest_media_id"]:
        top["oldest_media_id"] = batch_oldest
    segs[0] = top
    return segs


def segment_to_deepen(segments: list[Segment]) -> int | None:
    """Index of the segment deepen should extend, or None if none is workable.

    Works the FRONT-MOST non-terminal segment that still has a resume cursor:
      * single segment -> extend it toward scan_depth / end_of_feed;
      * multi-segment (a gap) -> extend the front segment DOWN to bridge the
        next-older segment, after which the two merge.
    """
    for idx, seg in enumerate(segments):
        if not seg.get("terminal") and seg.get("resume_cursor"):
            return idx
    return None


def apply_deepen(
    segments: list[Segment],
    target_idx: int,
    result: FetchResult,
    *,
    persisted_media_ids: list[int],
) -> list[Segment]:
    """Fold a DEEPEN result into ``segments[target_idx]``: lower its oldest bound,
    advance/clear its resume cursor, mark terminal on end_of_feed, then merge
    with the next-older segment if the worked span now reaches into it."""
    segs = [dict(s) for s in segments]
    if not (0 <= target_idx < len(segs)):
        return segs
    seg = segs[target_idx]

    if persisted_media_ids:
        batch_oldest = min(persisted_media_ids)
        if batch_oldest < seg["oldest_media_id"]:
            seg["oldest_media_id"] = batch_oldest

    terminal = is_terminal(result.stop_reason)
    seg["terminal"] = terminal
    # A terminal segment has nothing below it; otherwise carry the new cursor.
    seg["resume_cursor"] = None if terminal else (result.next_cursor
                                                  or seg.get("resume_cursor"))
    segs[target_idx] = seg

    # Bridge/merge: if the worked segment's oldest now crosses at/below the
    # next-older segment's newest, the gap is closed — merge them into one.
    nxt = target_idx + 1
    if nxt < len(segs):
        lower = segs[nxt]
        if seg["oldest_media_id"] <= lower["newest_media_id"]:
            merged = _segment(
                newest=max(seg["newest_media_id"], lower["newest_media_id"]),
                oldest=min(seg["oldest_media_id"], lower["oldest_media_id"]),
                resume_cursor=lower.get("resume_cursor"),
                terminal=bool(lower.get("terminal")),
            )
            segs = [*segs[:target_idx], merged, *segs[nxt + 1:]]
    return segs


def is_contiguous(
    segments: list[Segment], *, pool_depth: int, scan_depth: int
) -> bool:
    """Coverage is contiguous when there is exactly ONE segment that either is
    terminal (the account's real end) or spans at least ``scan_depth`` reels.

    With a single segment the pool IS that segment, so ``pool_depth`` measures
    its depth. NEVER defined as raw count with a gap present (>1 segment)."""
    if len(segments) != 1:
        return False
    seg = segments[0]
    if seg.get("terminal"):
        return True
    return pool_depth >= scan_depth


def has_more_to_fetch(segments: list[Segment]) -> bool:
    """True if any segment still has an open resume cursor (deepen has work)."""
    return segment_to_deepen(segments) is not None
