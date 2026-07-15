"""Fetch primitive — handle -> user_id, then paced feed pagination + normalization.

Tasks T1.4 + T1.5. This is the one shared engine ``list_reels`` and the batch
runner both call. It is anonymous, polite (short-circuiting page-walk, first
stop_signal stops, <=4 pages/call, no sleep on the sync path), and it normalizes
mixed feed media to clip reel records.

Load-bearing (see plan + CLAUDE.md):
  * The top_scan stop condition is (PRIMARY) seen-set MEMBERSHIP on the opaque
    shortcode, backstopped by (SECONDARY) a numeric ``high_water_media_id``
    watermark. Shortcodes are NEVER compared with ``<=`` — they are not
    orderable. media_ids are.
  * ANY stop_signal (not just 401) stops the walk and returns a partial with the
    cursor/newest-id intact, a typed reason, and NO sleep, NO poll.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Iterable, Mapping

from .http_client import (
    AnonymousClient,
    Classification,
    Outcome,
    ResponseView,
    StopReason,
    classify_response,
)

# --- Endpoints (v1, the stable anonymous path) ------------------------------

WEB_PROFILE_INFO_URL = "https://i.instagram.com/api/v1/users/web_profile_info/"
FEED_USER_URL = "https://i.instagram.com/api/v1/feed/user/{user_id}/"

# Feed pages are hard-capped at 12 items regardless of the requested count.
FEED_PAGE_COUNT = 12
# Only clips (reels) carry a real play_count; images/carousels have play_count == null.
CLIP_PRODUCT_TYPE = "clips"
DEFAULT_MAX_PAGES = 4


class FetchMode(str, Enum):
    """Two disambiguated traversal modes over the same pagination code.

    ``top_scan`` surfaces posts newer than the anchors (the mode T1 wires into
    the sync window). ``deep_resume`` pages older/deeper from a saved cursor
    toward scan_depth (backfill; caller is a follow-up ticket)."""

    TOP_SCAN = "top_scan"
    DEEP_RESUME = "deep_resume"


class StopKind(str, Enum):
    """Why the page-walk ended. NORMAL kinds vs a stop_signal reason."""

    CAUGHT_UP = "caught_up"        # top_scan re-hit known content — normal success
    END_OF_FEED = "end_of_feed"    # no more_available — normal
    PAGE_CAP = "page_cap"          # hit max_pages_per_call — normal
    DEPTH_REACHED = "depth_reached"  # deep_resume hit its depth target — normal
    # Abnormal stops carry a StopReason value instead (see FetchResult.stop_reason).


@dataclass(frozen=True)
class ReelRecord:
    """One normalized clip. ``shortcode`` is the dedupe/identity key; ``media_id``
    is the numeric ordered anchor. They are DISTINCT fields — never conflated."""

    shortcode: str
    media_id: int
    play_count: int | None
    ig_play_count: int | None
    like_count: int | None
    comment_count: int | None
    caption: str
    taken_at: int | None
    duration: float | None
    product_type: str
    video_url: str | None
    fetched_at: int


@dataclass(frozen=True)
class UserIdResult:
    """Result of resolving a handle. On a stop_signal, ``user_id`` is None and
    ``stop_reason`` carries the typed reason — it does NOT raise."""

    user_id: str | None
    stop_reason: StopReason | None = None
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.user_id is not None


@dataclass
class FetchResult:
    """Output of one window call. ``pages_fetched`` is emitted so a reviewer can
    assert the short-circuit (a caught-up top_scan must report ``pages_fetched == 1``)."""

    reels: list[ReelRecord] = field(default_factory=list)
    newest_media_id: int | None = None      # candidate high_water_media_id (max pk collected)
    newest_shortcode: str | None = None
    next_cursor: str | None = None          # next_max_id from the last page (deep_cursor candidate)
    pages_fetched: int = 0
    partial: bool = False                   # True iff stopped on a stop_signal
    stop_reason: str = StopKind.END_OF_FEED.value  # StopKind value OR a StopReason value


# --- user_id resolution (T1.4) ----------------------------------------------


def resolve_user_id(client: AnonymousClient, handle: str) -> UserIdResult:
    """Resolve a public handle to its numeric user_id via web_profile_info.

    A stop_signal returns cleanly (no user_id, typed reason) — never raises."""
    resp = client.get_api(WEB_PROFILE_INFO_URL, params={"username": handle})
    cls = classify_response(resp.status_code, headers=resp.headers, body=resp.json() or resp.text,
                            location=resp.location)
    if cls.is_stop:
        return UserIdResult(None, cls.reason, cls.detail)
    if cls.outcome is Outcome.ERROR:
        return UserIdResult(None, StopReason.UNKNOWN, cls.detail)
    body = resp.json() or {}
    user = (((body.get("data") or {}).get("user")) or {})
    user_id = user.get("id")
    if not user_id:
        return UserIdResult(None, StopReason.UNKNOWN, "web_profile_info missing data.user.id")
    return UserIdResult(str(user_id))


# --- normalization (T1.5) ---------------------------------------------------


def _as_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _caption_text(item: Mapping[str, Any]) -> str:
    cap = item.get("caption")
    if isinstance(cap, Mapping):
        return cap.get("text") or ""
    if isinstance(cap, str):
        return cap
    return ""


def _video_url(item: Mapping[str, Any]) -> str | None:
    versions = item.get("video_versions")
    if isinstance(versions, list) and versions:
        first = versions[0]
        if isinstance(first, Mapping):
            return first.get("url")
    return None


# product_type dispatch — a SWITCH, not a rewrite. Clips today; image/carousel/
# story slot in here later without restructuring the fetch loop.
def normalize_item(item: Mapping[str, Any], fetched_at: int) -> ReelRecord | None:
    """Normalize one feed item to a ReelRecord, or None if it is not a clip.

    Filters on ``product_type == "clips"`` (the extensibility dispatch point).
    Requires a shortcode (``code``) and a numeric ``pk``; drops malformed items."""
    if item.get("product_type") != CLIP_PRODUCT_TYPE:
        return None
    shortcode = item.get("code")
    media_id = _as_int(item.get("pk") or item.get("id"))
    if not shortcode or media_id is None:
        return None
    return ReelRecord(
        shortcode=str(shortcode),
        media_id=media_id,
        play_count=_as_int(item.get("play_count")),
        ig_play_count=_as_int(item.get("ig_play_count")),
        like_count=_as_int(item.get("like_count")),
        comment_count=_as_int(item.get("comment_count")),
        caption=_caption_text(item),
        taken_at=_as_int(item.get("taken_at")),
        duration=_coerce_float(item.get("video_duration")),
        product_type=str(item.get("product_type")),
        video_url=_video_url(item),
        fetched_at=fetched_at,
    )


def _coerce_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _item_media_id(item: Mapping[str, Any]) -> int | None:
    return _as_int(item.get("pk") or item.get("id"))


def _item_shortcode(item: Mapping[str, Any]) -> str | None:
    code = item.get("code")
    return str(code) if code else None


# --- paged window fetch (T1.5) ----------------------------------------------


def fetch_window(
    client: AnonymousClient,
    user_id: str,
    *,
    mode: FetchMode = FetchMode.TOP_SCAN,
    seen: Iterable[str] | None = None,
    high_water_media_id: int | None = None,
    start_cursor: str | None = None,
    max_pages: int = DEFAULT_MAX_PAGES,
    depth_target: int | None = None,
    pace_seconds: float = 0.0,
    sleep: Callable[[float], None] | None = None,
    now: Callable[[], int] = lambda: int(time.time()),
) -> FetchResult:
    """Fetch one paced window of the owner feed and normalize its clips.

    ``mode``:
      * ``top_scan`` — page from the newest item; STOP the walk on the first
        already-seen shortcode (PRIMARY) or first ``media_id <= high_water_media_id``
        (SECONDARY). Surfaces posts since the last run.
      * ``deep_resume`` — page from ``start_cursor`` (a saved next_max_id) toward
        ``depth_target``; stops on depth/end-of-feed/page-cap, NOT the watermark.

    Politeness: ``max_pages`` caps pages/call; ANY stop_signal stops and returns a
    partial with cursor + newest-id intact; sleeping happens ONLY if a ``sleep``
    callable is supplied (the sync path passes none — it NEVER sleeps).
    """
    seen_set = set(seen or ())
    result = FetchResult()
    fetched_at = now()
    cursor = start_cursor
    collected_media_ids: list[int] = []

    for page_index in range(max_pages):
        # Pace pages ONLY off the sync path (sleep must be explicitly supplied).
        if page_index > 0 and sleep is not None and pace_seconds > 0:
            sleep(pace_seconds)

        resp = _get_feed_page(client, user_id, cursor)
        cls = classify_response(
            resp.status_code, headers=resp.headers,
            body=resp.json() or resp.text, location=resp.location,
        )
        result.pages_fetched += 1

        if cls.is_stop:
            # Abnormal stop — return the partial accumulated so far, cursor intact.
            result.partial = True
            result.stop_reason = (cls.reason or StopReason.UNKNOWN).value
            return result
        if cls.outcome is Outcome.ERROR:
            result.partial = True
            result.stop_reason = StopReason.UNKNOWN.value
            return result

        body = resp.json() or {}
        items = body.get("items") or []
        next_max_id = body.get("next_max_id")
        more_available = bool(body.get("more_available"))

        caught_up = _consume_page(
            items, mode, seen_set, high_water_media_id, fetched_at,
            result, collected_media_ids, depth_target,
        )

        # Advance the deep cursor to the last page we successfully read.
        if next_max_id:
            result.next_cursor = str(next_max_id)

        if caught_up is not None:
            result.stop_reason = caught_up.value
            break
        if depth_target is not None and len(result.reels) >= depth_target:
            result.stop_reason = StopKind.DEPTH_REACHED.value
            break
        if not more_available or not next_max_id:
            result.stop_reason = StopKind.END_OF_FEED.value
            break
        cursor = str(next_max_id)
    else:
        # Loop exhausted the page budget without a natural stop.
        result.stop_reason = StopKind.PAGE_CAP.value

    if collected_media_ids:
        result.newest_media_id = max(collected_media_ids)
        # Newest shortcode = the shortcode of the max-pk (newest) collected reel.
        for reel in result.reels:
            if reel.media_id == result.newest_media_id:
                result.newest_shortcode = reel.shortcode
                break
    return result


def _consume_page(
    items: list[Mapping[str, Any]],
    mode: FetchMode,
    seen_set: set[str],
    high_water_media_id: int | None,
    fetched_at: int,
    result: FetchResult,
    collected_media_ids: list[int],
    depth_target: int | None,
) -> StopKind | None:
    """Walk one page newest-first, collecting clips and applying the top_scan
    stop condition. Returns a StopKind if the walk should end, else None.

    The stop check runs over EVERY item (so we stop at the first known clip even
    if non-clip items precede it); only clips are collected.

    # TODO: harden top_scan against PINNED reels in fetch._consume_page — the
    # T1.2 live probe (natgeo, 2026-07-15) showed the owner feed is NOT strictly
    # pk-descending: pinned reels (older, smaller pk) appear ABOVE newer ones
    # (pks_descending == false). That weakens the plan's "first-known == caught
    # up" premise: for an account that pins reels, a subsequent top_scan can hit
    # a pinned (already-seen) reel first and STOP above genuinely newer reels,
    # under-collecting them. The caught-up short-circuit (pages_fetched == 1) is
    # unaffected and correct; only the "new posts below a pinned block" case
    # under-collects. Fix (own ticket, needs its own review because it must not
    # reintroduce the budget-burn regression the caught-up==1-page invariant
    # guards): skip a bounded prefix of known/pinned items before applying the
    # stop, or resolve the pinned-shortcode set and exclude it from the stop
    # check. Do NOT weaken the short-circuit to do it. See discovered_followups.
    """
    for item in items:
        if mode is FetchMode.TOP_SCAN:
            shortcode = _item_shortcode(item)
            media_id = _item_media_id(item)
            # PRIMARY: seen-set membership (order-tolerant; the authoritative stop).
            if shortcode is not None and shortcode in seen_set:
                return StopKind.CAUGHT_UP
            # SECONDARY: numeric media_id watermark (belt-and-suspenders).
            # NOTE (T1.2 probe): feed is not strictly pk-descending under pinning,
            # so this watermark is a monotonic BACKSTOP only — membership above is
            # authoritative. A per-page min-scan (plan's documented fallback) is
            # folded into the pinned-reel followup above, not done here.
            if (
                high_water_media_id is not None
                and media_id is not None
                and media_id <= high_water_media_id
            ):
                return StopKind.CAUGHT_UP

        reel = normalize_item(item, fetched_at)
        if reel is None:
            continue
        # In-window dedupe (a page could, in theory, repeat) + skip already-seen.
        if reel.shortcode in seen_set:
            continue
        seen_set.add(reel.shortcode)
        result.reels.append(reel)
        collected_media_ids.append(reel.media_id)
        if depth_target is not None and len(result.reels) >= depth_target:
            return StopKind.DEPTH_REACHED
    return None


def _get_feed_page(client: AnonymousClient, user_id: str, cursor: str | None) -> ResponseView:
    params: dict[str, Any] = {"count": FEED_PAGE_COUNT}
    if cursor:
        params["max_id"] = cursor
    url = FEED_USER_URL.format(user_id=user_id)
    return client.get_api(url, params=params)
