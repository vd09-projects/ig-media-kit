"""FastMCP server — the four-tool public surface over the shared fetch engine (T5).

Assembles the four merged tools — ``list_reels``, ``download_reel``,
``start_batch_fetch``, ``get_batch_status`` — into one runnable, packaged FastMCP
server a fresh cloner can start with ``python -m ig_media_kit.mcp_server --config
config.yaml`` (or the ``ig-media-kit`` console script).

Load-bearing wiring (CLAUDE.md + the T5 plan):
  * **One shared context.** ``main()`` loads config ONCE (reusing ``load_config``'s
    ``explicit > $IG_MK_CONFIG > ./config.yaml`` precedence — never reimplemented
    here), builds one :class:`ServerContext` (config + Store + the single
    process-wide FetchGate), and every tool reads it — no per-call ``load_config``,
    no per-call gate resolution.
  * **One FetchGate per process.** All IG-hitting work serializes through the
    single ``get_gate`` singleton; CDN downloads stay ungated. ``get_gate`` is
    argument-ignoring, so it can NOT be split — but a tool handed a ``config_path``
    pointing at a DIVERGENT ``store_dir`` would read/write a different store than
    the one whose gate cooldown is persisted. That is rejected (a typed envelope),
    not silently allowed. ``config_path`` survives only as a store-compatible /
    test-only override.
  * **Explicit restart-resume BEFORE serving.** ``main()`` calls
    ``resume_pending_jobs`` explicitly (not on import, not lazily) so a restart
    re-adopts pending ``store/_batch`` jobs, and it completes before ``mcp.run()``.
  * **Never-raise surface.** All four tools return a typed dict envelope and never
    propagate an exception to the MCP client — including ``list_reels``.

ANONYMOUS ONLY: no login, no cookies, no session, no account — on any path.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from typing import Any

from mcp.server.fastmcp import FastMCP

from .batch import (
    BatchDeps, resume_pending_jobs, run_get_batch_status, run_start_batch_fetch,
)
from .config import Config, load_config
from .download import run_download_reel
from .fetch_gate import FetchGate, get_gate
from .list_reels import run_list_reels
from .store import Store

mcp = FastMCP("ig-media-kit")


# --- shared server context (one config + one store + one gate) ---------------


@dataclass(frozen=True)
class ServerContext:
    """The single context every tool reads: the loaded config, the Store over its
    ``store_dir``, and the one process-wide FetchGate. Built once in ``main()``
    (or lazily on a standalone/test call) and reused — tools never re-read config
    from disk or re-resolve a gate per call."""

    config: Config
    store: Store
    gate: FetchGate


class ContextMismatch(RuntimeError):
    """A tool was called with a ``config_path`` whose ``store_dir`` diverges from
    the server context's ``store_dir``. Rejected rather than served: the one
    process-wide gate is argument-ignoring (it would NOT split), so honouring the
    divergent path would fetch/write a different ``store_dir`` than the gate's
    persisted cooldown tracks — breaking the one-gate politeness invariant."""


_CONTEXT: ServerContext | None = None


def build_context(config: Config) -> ServerContext:
    """Build a :class:`ServerContext` — the Store over ``config``'s ``store_dir``
    plus the process-wide gate singleton (``get_gate`` ignores args after the
    first call, so this never mints a second gate)."""
    return ServerContext(
        config=config,
        store=Store(config.output.store_dir),
        gate=get_gate(config),
    )


def install_context(config: Config) -> ServerContext:
    """Build and install the shared server context (called by ``startup``)."""
    global _CONTEXT
    _CONTEXT = build_context(config)
    return _CONTEXT


def reset_context() -> None:
    """Drop the installed server context (test-only seam)."""
    global _CONTEXT
    _CONTEXT = None


def current_context() -> ServerContext | None:
    """The installed server context, or ``None`` before ``startup``/``install_context``.
    A read accessor for the smoke harness + tests (never mutate the returned object)."""
    return _CONTEXT


def _resolve_context(config_path: str | None) -> ServerContext:
    """Return the shared context for a tool call.

    ``config_path`` is a store-compatible / TEST-ONLY override, not a routing knob:
      * ``None`` → the installed server context (or a lazily-built one when no
        server has been started, e.g. a direct/test call).
      * given, with a server context installed → REUSE the server context iff the
        override's ``store_dir`` matches; a DIVERGENT ``store_dir`` is rejected
        (:class:`ContextMismatch`) so the one process-wide gate is never bypassed.
      * given, with no server context → build an ephemeral context from it
        (standalone/test path; the gate is still the process singleton).
    """
    global _CONTEXT
    if config_path is None:
        if _CONTEXT is None:
            _CONTEXT = build_context(load_config(None))
        return _CONTEXT
    override = load_config(config_path)
    if _CONTEXT is not None:
        if override.output.store_dir != _CONTEXT.config.output.store_dir:
            raise ContextMismatch(
                f"config_path store_dir {override.output.store_dir!r} differs from "
                f"the server store_dir {_CONTEXT.config.output.store_dir!r}; refusing "
                f"to split the process-wide fetch gate — pass a store-compatible "
                f"config, or omit config_path"
            )
        return _CONTEXT
    return build_context(override)


def _batch_deps(ctx: ServerContext) -> BatchDeps:
    """Batch deps bound to the SHARED store + gate (not a per-call re-resolution)."""
    return BatchDeps(config=ctx.config, store=ctx.store, gate=ctx.gate)


# --- the four tools (frozen public surface — each never raises) --------------


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

    NEVER raises to the client — any failure comes back as a typed envelope with a
    ``note`` and an ``error`` marker."""
    try:
        ctx = _resolve_context(config_path)
        return run_list_reels(
            handle, config=ctx.config, store=ctx.store,
            count=count, sort_by=sort_by, min_views=min_views,
            min_duration=min_duration, max_age_days=max_age_days,
            scan_depth=scan_depth, fresh_fetch=fresh_fetch,
        )
    except Exception as exc:  # noqa: BLE001 — the tool must never throw.
        return {
            "handle": handle, "user_id": None, "reels": [], "count_returned": 0,
            "partial": False, "pool_depth": 0,
            "coverage": {"complete": False, "segments": 0, "pool_depth": 0},
            "pages_fetched": 0, "stop_reason": None,
            "note": f"list_reels failed: {exc}", "error": str(exc),
        }


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
    rate-limit (``partial=True``, retryable), reel aged out of reach
    (``partial=False``, terminal), or a bad/empty CDN body — comes back as a
    typed envelope with a ``note`` (and an ``error``/``partial`` marker)."""
    try:
        ctx = _resolve_context(config_path)
        return run_download_reel(shortcode, config=ctx.config, store=ctx.store)
    except Exception as exc:  # noqa: BLE001 — final backstop: the tool must never throw.
        return {
            "shortcode": shortcode, "handle": None, "local_mp4": None,
            "cached": False, "refreshed": False, "partial": False,
            "stop_reason": None,
            "note": f"download_reel failed: {exc}", "error": str(exc),
        }


@mcp.tool()
def start_batch_fetch(
    handles: list[str] | None = None,
    scope: str = "global",
    count: int | None = None,
    sort_by: str | None = None,
    min_views: int | None = None,
    min_duration: float | None = None,
    max_age_days: int | None = None,
    download_top: bool = False,
    callback_url: str | None = None,
    config_path: str | None = None,
) -> dict[str, Any]:
    """Start an async batch fill + top-N aggregation across channels; returns a
    ``job_id`` immediately (the ONLY path permitted to sleep/pace between pages).

    Fills each handle (or ``handles`` if given, else config ``channels``) toward
    ``scan_depth`` across escalating IG rate-limit cooldowns — serialized through
    the process-wide fetch gate so only one IG window is ever in flight — then
    ranks a top-``count`` by ``sort_by`` either cross-channel (``scope="global"``)
    or per handle (``scope="per_channel"``). Optionally downloads the top-N mp4s
    (``download_top``) and POSTs the result to ``callback_url`` (https-only,
    SSRF-guarded, best-effort). Poll ``get_batch_status`` for progress + result.
    Never blocks: returns ``{job_id, phase: queued}`` while the job runs detached."""
    try:
        ctx = _resolve_context(config_path)
        return run_start_batch_fetch(
            config=ctx.config, handles=handles, scope=scope, count=count,
            sort_by=sort_by, download_top=download_top, callback_url=callback_url,
            filters={"min_views": min_views, "min_duration": min_duration,
                     "max_age_days": max_age_days},
            deps=_batch_deps(ctx),
        )
    except Exception as exc:  # noqa: BLE001 — the tool must never throw.
        return {"ok": False, "job_id": None, "phase": None,
                "error": str(exc), "note": f"start_batch_fetch failed: {exc}"}


@mcp.tool()
def get_batch_status(job_id: str, config_path: str | None = None) -> dict[str, Any]:
    """Read an async batch job's phase, per-handle progress, and final result.

    Pure read — no IG network, never triggers a fetch, safe to call during a
    cooldown. Reports liveness (``fetching`` / ``cooldown-sleeping`` /
    ``dead-worker``) so a sleeping job is distinguishable from a crashed one, and
    returns the aggregated result once ready (even if the callback never landed).
    An unknown ``job_id`` comes back as a typed not-found envelope, never raises."""
    try:
        ctx = _resolve_context(config_path)
        return run_get_batch_status(job_id, config=ctx.config, deps=_batch_deps(ctx))
    except Exception as exc:  # noqa: BLE001 — the tool must never throw.
        return {"found": False, "job_id": job_id, "phase": None,
                "error": str(exc), "note": f"get_batch_status failed: {exc}"}


# --- startup + entrypoint ----------------------------------------------------


def startup(config_path: str | None = None, *, deps: BatchDeps | None = None) -> dict[str, Any]:
    """Load config, install the shared context, and EXPLICITLY re-adopt pending
    batch jobs — the boot wiring ``main()`` runs BEFORE ``mcp.run()``.

    Separated from ``main()`` so the "resume ran before serving" ordering + the
    context install are testable without blocking on ``mcp.run()``. Returns the
    ``resume_pending_jobs`` summary (``count`` re-adopted, ``tmp_swept``)."""
    config = load_config(config_path)          # explicit > $IG_MK_CONFIG > ./config.yaml
    ctx = install_context(config)
    deps = deps or _batch_deps(ctx)
    return resume_pending_jobs(config, deps=deps)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="ig-media-kit",
        description="Anonymous Instagram reel-fetch MCP server "
                    "(list_reels, download_reel, start_batch_fetch, get_batch_status "
                    "over one shared fetch engine).",
    )
    parser.add_argument(
        "--config", default=None,
        help="path to config.yaml (default: $IG_MK_CONFIG, else ./config.yaml)",
    )
    args = parser.parse_args(argv)

    resumed = startup(args.config)
    print(
        f"[ig-media-kit] re-adopted {resumed['count']} pending batch job(s) "
        f"(swept {resumed['tmp_swept']} tmp checkpoint(s)); serving 4 tools over MCP",
        flush=True,
    )
    mcp.run()


if __name__ == "__main__":
    main()
