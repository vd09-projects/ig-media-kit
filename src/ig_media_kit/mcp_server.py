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
from .store import Store
from .window import run_window

mcp = FastMCP("ig-media-kit")


@mcp.tool()
def list_reels(handle: str, config_path: str | None = None) -> dict[str, Any]:
    """Fetch one anonymous top_scan window of reels for a public IG handle and
    persist them to the flat-file store. Returns a summary of the window.

    Synchronous, never sleeps, <=4 feed pages, stops on the first stop_signal.
    """
    config = load_config(config_path)
    outcome = run_window(handle, config=config)
    return {
        "handle": outcome.handle,
        "user_id": outcome.user_id,
        "persisted": outcome.persisted,
        "skipped_seen": outcome.skipped_seen,
        "pages_fetched": outcome.pages_fetched,
        "stop_reason": outcome.stop_reason,
        "partial": outcome.partial,
        "high_water_media_id": outcome.high_water_media_id,
        "total_reels": outcome.total_reels,
    }


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
    """STUB (later ticket): download an mp4 from fbcdn by shortcode (redirect-
    follow lives in http_client.get_cdn). Not yet implemented."""
    return {"stub": True, "shortcode": shortcode, "note": "downloader is a later ticket"}


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
