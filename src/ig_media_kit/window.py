"""Synchronous window call — wire fetch (top_scan) -> normalize -> store.

Task T1.7. One synchronous "fetch a window for handle H" entry point that this
path NEVER sleeps and never issues more than ``max_pages`` feed pages.

# TODO: run_window is currently unreferenced — T2 list_reels inlines its own
# two-phase (top-check + deepen) compose rather than reusing this single-window
# helper. It is kept as the reusable sync primitive for the async batch runner
# (a later ticket, the only path permitted to sleep/pace). Consider consolidating
# the batch runner onto this OR retiring it once that ticket lands, to avoid two
# compose paths drifting. (tracked: #8)

Flow: load state (user_id, high_water_media_id, deep_cursor) + derived seen ->
top_scan short-circuiting on seen-membership / media_id watermark -> paced fetch
(no sleep here) -> normalize -> dedupe -> CSV rows durable FIRST -> advance
anchors for persisted items only -> atomic state write -> record last_stop_reason.
"""

from __future__ import annotations

from dataclasses import dataclass

from .config import Config
from .fetch import FetchMode, FetchResult, fetch_window, resolve_user_id
from .http_client import AnonymousClient
from .store import Store


@dataclass
class WindowOutcome:
    handle: str
    user_id: str | None
    persisted: int
    skipped_seen: int
    pages_fetched: int
    stop_reason: str
    partial: bool
    high_water_media_id: int | None
    total_reels: int


def run_window(
    handle: str,
    *,
    config: Config,
    client: AnonymousClient | None = None,
    store: Store | None = None,
) -> WindowOutcome:
    """Fetch and persist one top_scan window for ``handle``. Synchronous, no sleep.

    Returns a WindowOutcome even on a stop_signal (a valid partial is persisted
    with rows-before-cursor ordering intact and a typed ``stop_reason``).
    """
    client = client or AnonymousClient()
    store = store or Store(config.output.store_dir)

    state = store.load_state(handle)
    seen = store.load_seen(handle)

    # Resolve user_id once and cache it (avoid re-resolving on resume).
    user_id = state.user_id
    if not user_id:
        resolved = resolve_user_id(client, handle)
        if not resolved.ok:
            # A stop_signal on resolution returns a clean partial, no rows.
            store.write_window(
                handle, [], user_id=None,
                stop_reason=(resolved.stop_reason.value if resolved.stop_reason else "unknown"),
                mode=FetchMode.TOP_SCAN,
            )
            return WindowOutcome(
                handle=handle, user_id=None, persisted=0, skipped_seen=0,
                pages_fetched=0,
                stop_reason=(resolved.stop_reason.value if resolved.stop_reason else "unknown"),
                partial=True, high_water_media_id=state.high_water_media_id,
                total_reels=store.count_reels(handle),
            )
        user_id = resolved.user_id

    result: FetchResult = fetch_window(
        client,
        user_id,
        mode=FetchMode.TOP_SCAN,
        seen=seen,
        high_water_media_id=state.high_water_media_id,
        max_pages=config.fetch.max_pages_per_call,
        # SYNC PATH: no sleep callable => fetch never sleeps (invariant).
        sleep=None,
    )

    write = store.write_window(
        handle,
        result.reels,
        user_id=user_id,
        next_cursor=result.next_cursor,
        stop_reason=result.stop_reason,
        mode=FetchMode.TOP_SCAN,
    )

    return WindowOutcome(
        handle=handle,
        user_id=user_id,
        persisted=write.persisted,
        skipped_seen=write.skipped_seen,
        pages_fetched=result.pages_fetched,
        stop_reason=result.stop_reason,
        partial=result.partial,
        high_water_media_id=write.high_water_media_id,
        total_reels=store.count_reels(handle),
    )
