"""FastMCP server skeleton — Task T1.8.

Boots a FastMCP instance so ``python -m ig_media_kit.mcp_server`` runs. Registers
a thin ``list_reels`` tool wired to the synchronous window path (T1.7) to prove
end-to-end wiring; the other three tools (batch runner, download, top-N) are
registered as stubs pointing at later tickets.
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from .config import load_config
from .download import run_download_reel
from .list_reels import run_list_reels
from .store import Store

mcp = FastMCP("ig-media-kit")


@mcp.tool()
def list_reels(
    handle: str,
    count: int | None = None,
    sort_by: str | None = None,
    min_views: int | None = None,
    min_duration: float | None = None,
    max_age_days: int | None = None,
    scan_depth: int | None = None,
    fresh_fetch: bool = False,
    config_path: str | None = None,
) -> dict[str, Any]:
    """Return a public IG handle's top reels, ranked, filling the store as it goes.

    Discovers reels anonymously (no login), accumulating the pool call-by-call
    toward ``scan_depth`` and returning the top ``count`` ranked by ``sort_by``
    (``play_count`` | ``like_count`` | ``comment_count`` | ``taken_at``, desc).
    Filters: ``min_views`` (play_count), ``min_duration`` (seconds),
    ``max_age_days``. Unset args fall back to the config ``top_reels`` defaults.

    Fast + NEVER-BLOCKING: synchronous, never sleeps, spends at most
    ``max_pages_per_call`` feed pages across its top-check + deepen phases, and
    returns a ranked PARTIAL with a "budget cooling" note on the first IG
    rate-limit rather than blocking. ``fresh_fetch=true`` forces a top-check even
    when coverage is already complete; ``fresh_fetch=false`` (default) serves
    straight from the store with ZERO network once coverage is contiguous+deep.
    """
    config = load_config(config_path)
    return run_list_reels(
        handle, config=config, count=count, sort_by=sort_by,
        min_views=min_views, min_duration=min_duration,
        max_age_days=max_age_days, scan_depth=scan_depth, fresh_fetch=fresh_fetch,
    )


@mcp.tool()
def top_reels(handle: str, config_path: str | None = None) -> dict[str, Any]:
    """STUB (later ticket): top-N ranking/filtering over the accumulated pool.

    Returns the current stored reel count; ranking is not yet implemented."""
    config = load_config(config_path)
    store = Store(config.output.store_dir)
    return {"handle": handle, "total_reels": store.count_reels(handle), "stub": True}


@mcp.tool()
def batch_fetch(handles: list[str] | None = None, config_path: str | None = None) -> dict[str, Any]:
    """STUB (later ticket): async batch runner across channels (the only path
    permitted to sleep/pace between pages). Not yet implemented."""
    return {"stub": True, "handles": handles or [], "note": "batch runner is a later ticket"}


@mcp.tool()
def download_reel(shortcode: str, config_path: str | None = None) -> dict[str, Any]:
    """Download one reel's mp4 by shortcode, refreshing the signed URL if stale.

    Anonymous + on-demand: locates the shortcode's owner in the store, serves an
    already-downloaded file with ZERO network, otherwise fetches the mp4 from
    fbcdn (redirect-following, unmetered). If the stored signed URL has aged past
    its TTL margin (~24 h under the ~36 h fbcdn TTL) it re-resolves a fresh URL
    from the owner feed — a metered call that stops on the first IG rate-limit
    and returns a typed partial rather than polling. The downloaded bytes are
    ftyp-verified before replacing any prior file, and the manifest's
    ``local_mp4`` (plus the refreshed URL) is rewritten atomically.

    NEVER raises to the client: every failure — unknown shortcode, re-resolve
    rate-limit, reel aged out of reach, or a bad/empty CDN body — comes back as a
    typed envelope with a ``note`` (and an ``error``/``partial`` marker)."""
    try:
        config = load_config(config_path)
        return run_download_reel(shortcode, config=config)
    except Exception as exc:  # noqa: BLE001 — final backstop: the tool must never throw.
        return {
            "shortcode": shortcode, "handle": None, "local_mp4": None,
            "cached": False, "refreshed": False, "partial": False,
            "stop_reason": None,
            "note": f"download_reel failed: {exc}", "error": str(exc),
        }


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
