"""``download_reel`` orchestration — Task T3.

Downloads a single reel's mp4 on demand, transparently re-resolving the fbcdn
signed URL when the stored one has aged past its TTL margin, and serving an
already-downloaded file with ZERO network. Composes the T3 subtasks:

  T3.1  store.find_reel        — shortcode -> owner handle + manifest row.
  T3.2  cached-hit gate        — provable local artifact -> return, no network.
  T3.3  freshness decision     — stored URL age vs the TTL margin.
  T3.4  fetch.resolve_reel_url — identity-anchored owner-feed re-resolve (metered).
  T3.5  _download_to           — binary redirect-follow CDN GET + ftyp-verify +
                                 temp-write + atomic os.replace.
  T3.6  store.update_local_mp4 — atomic manifest local_mp4 (+ refreshed URL) write.

Every failure branch returns a typed error/partial ENVELOPE — an exception never
reaches the MCP client. Anonymity + politeness invariants hold: metadata (the
re-resolve) is metered and stops on the first stop_signal without polling; the
CDN download is unmetered and never sleeps.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any, Callable

from .config import Config
from .fetch import ResolveOutcome, resolve_reel_url, resolve_user_id
from .http_client import STOP_SIGNAL_REASONS, AnonymousClient
from .store import Store

# Re-resolve when the stored URL is older than this margin. Deliberately BELOW
# the measured ~36 h fbcdn signed-URL TTL (the ``oe=`` param) so a URL is never
# used within an hour or two of expiry. A config knob is out of scope (T3); this
# is a module constant. (sindri/patterns.md accepts a fixed ~24 h margin.)
URL_REFRESH_MARGIN_SECONDS = 24 * 3600

# An mp4/ISO-BMFF file opens with a box: a 4-byte big-endian size, THEN the
# 4-byte box TYPE. For the first box that type is ``ftyp``. So the signature
# lives at bytes [4:8], NOT [0:4] (offset 0 is the size field, not the tag).
_FTYP_OFFSET = 4
_FTYP_TAG = b"ftyp"
_MIN_MP4_BYTES = _FTYP_OFFSET + len(_FTYP_TAG)  # need at least the size + tag


def _looks_like_mp4(data: bytes) -> bool:
    """True iff ``data`` opens with an ISO-BMFF ``ftyp`` box (tag at offset 4).

    Rejects a 0-byte body, a 302/HTML error body, and any non-mp4 payload — none
    of those carry ``ftyp`` at offset 4."""
    return len(data) >= _MIN_MP4_BYTES and data[_FTYP_OFFSET:_FTYP_OFFSET + 4] == _FTYP_TAG


def run_download_reel(
    shortcode: str,
    *,
    config: Config,
    client: AnonymousClient | None = None,
    store: Store | None = None,
    now: Callable[[], int] = lambda: int(time.time()),
) -> dict[str, Any]:
    """Download one reel's mp4 by ``shortcode`` and return a result envelope.

    Flow: resolve row (T3.1) -> cached-hit gate (T3.2) -> freshness (T3.3) ->
    reuse-or-re-resolve URL (T3.4) -> binary download + ftyp-verify (T3.5) ->
    atomic manifest update (T3.6). Synchronous; the metered re-resolve stops on
    the first stop_signal (returns a partial); the CDN download never sleeps.
    NEVER raises to the caller — every failure is a typed envelope."""
    store = store or Store(config.output.store_dir)

    # --- T3.1 resolve the owning handle + row (no network) ---
    found = store.find_reel(shortcode, handles=config.channels)
    if found is None:
        return _error(
            shortcode, None,
            "shortcode not in store; run list_reels for its owner first",
        )
    handle, row = found

    # --- T3.2 idempotent cached-hit gate: provable local artifact -> no network ---
    local_mp4 = (row.get("local_mp4") or "").strip()
    if local_mp4:
        p = Path(local_mp4)
        if p.exists() and p.stat().st_size > 0:
            return _ok(
                shortcode, handle, local_mp4, cached=True, refreshed=False,
                note="served from disk (cached); no network",
            )
        # A stale local_mp4 pointing at a deleted/empty file falls THROUGH to a
        # re-download — the gate fires only on a provable artifact.

    # --- T3.3 freshness decision (TTL margin) ---
    stored_url = (row.get("video_url") or "").strip()
    fetched_at = _as_int(row.get("fetched_at"))
    fresh_enough = (
        bool(stored_url)
        and fetched_at is not None
        and (now() - fetched_at) < URL_REFRESH_MARGIN_SECONDS
    )

    client = client or AnonymousClient()
    refreshed = False
    fresh_url: str | None = None

    if fresh_enough:
        download_url = stored_url
    else:
        # --- T3.4 targeted, identity-anchored owner-feed re-resolve (metered) ---
        media_id = _as_int(row.get("media_id"))
        state = store.load_state(handle)
        user_id = state.user_id
        if not user_id:
            resolved = resolve_user_id(client, handle)
            if not resolved.ok:
                reason = resolved.stop_reason.value if resolved.stop_reason else "unknown"
                return _partial(shortcode, handle, reason,
                                _cooling_note(reason, "resolving user_id"))
            user_id = resolved.user_id

        rr = resolve_reel_url(
            client, user_id, shortcode=shortcode, media_id=media_id,
            max_pages=config.fetch.max_pages_per_call,
            sleep=None,  # SYNC PATH: never sleeps.
        )
        if rr.outcome is ResolveOutcome.STOP_SIGNAL:
            reason = rr.stop_reason or "unknown"
            return _partial(shortcode, handle, reason,
                            _cooling_note(reason, "re-resolving signed URL"))
        if rr.outcome is ResolveOutcome.NOT_FOUND or not rr.video_url:
            # NOT a partial: distinct from the stop_signal cooldown case above.
            # The walk completed within budget and the reel was simply not in
            # reach (aged out of the reachable pages). A consumer branching on
            # retryability treats this as terminal-for-now (nothing to retry
            # sooner — it will not reappear), whereas the stop_signal `_partial`
            # above IS retryable (metered cooldown, retry after a few minutes).
            # So this is a clean typed error WITHOUT partial=True.
            return _error(
                shortcode, handle,
                "could not re-resolve within page budget — the reel may have "
                "aged out of reach; retry later",
            )
        fresh_url = rr.video_url
        download_url = fresh_url
        refreshed = True

    # --- T3.5 binary download + ftyp-verify + atomic move ---
    dest = Path(config.output.media_dir) / handle / f"{shortcode}.mp4"
    ok, detail = _download_to(client, download_url, dest)
    if not ok:
        # TODO: fall back to ONE metered re-resolve when a REUSED (in-margin)
        # stored URL fails to download, in run_download_reel; fbcdn can rotate a
        # signed URL before the 24 h margin (a 403/302 on a "fresh enough" URL),
        # and today that hard-errors instead of recovering. Guard it so it never
        # re-resolves after an already-refreshed URL fails (no metered retry loop).
        return _error(shortcode, handle, f"download failed: {detail}")

    # --- T3.6 atomic manifest update (local_mp4 + optionally refreshed URL) ---
    store.update_local_mp4(
        handle, shortcode, local_mp4=str(dest),
        video_url=fresh_url if refreshed else None,
        fetched_at=now() if refreshed else None,
    )

    note = (
        "downloaded (URL re-resolved)" if refreshed
        else "downloaded (stored URL still fresh)"
    )
    return _ok(shortcode, handle, str(dest), cached=False, refreshed=refreshed, note=note)


def _download_to(client: AnonymousClient, url: str, dest: Path) -> tuple[bool, str]:
    """Fetch ``url`` from the CDN (redirect-following), verify it is an mp4, and
    atomically move it into ``dest``. Returns ``(ok, detail)``.

    Writes to a sibling temp path and ``os.replace``-s only AFTER the ftyp-verify
    passes — a 0-byte / 302-shaped / non-mp4 body fails the check and does NOT
    clobber any prior file at ``dest``. Unmetered: no sleep, no rate-limit
    handling."""
    resp = client.download_cdn(url)
    if resp.status_code != 200:
        return False, f"CDN returned status {resp.status_code}"
    if not _looks_like_mp4(resp.content):
        return False, (
            f"payload is not an mp4 (no 'ftyp' box at offset {_FTYP_OFFSET}; "
            f"{len(resp.content)} bytes) — likely a 302/empty body"
        )
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    with tmp.open("wb") as fh:
        fh.write(resp.content)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, dest)
    return True, "ok"


# --- envelope builders ------------------------------------------------------


def _base(shortcode: str, handle: str | None) -> dict[str, Any]:
    return {
        "shortcode": shortcode,
        "handle": handle,
        "local_mp4": None,
        "cached": False,
        "refreshed": False,
        "partial": False,
        "stop_reason": None,
        "note": "",
    }


def _ok(
    shortcode: str, handle: str, local_mp4: str, *,
    cached: bool, refreshed: bool, note: str,
) -> dict[str, Any]:
    env = _base(shortcode, handle)
    env.update(local_mp4=local_mp4, cached=cached, refreshed=refreshed, note=note)
    return env


def _error(
    shortcode: str, handle: str | None, note: str, *, partial: bool = False,
) -> dict[str, Any]:
    env = _base(shortcode, handle)
    env.update(note=note, error=note, partial=partial)
    return env


def _partial(shortcode: str, handle: str, stop_reason: str, note: str) -> dict[str, Any]:
    env = _base(shortcode, handle)
    env.update(partial=True, stop_reason=stop_reason, note=note)
    return env


def _cooling_note(stop_reason: str, where: str) -> str:
    if stop_reason in STOP_SIGNAL_REASONS:
        return (f"budget cooling — IG {stop_reason} while {where}; "
                f"retry after a few minutes")
    return f"stopped ({stop_reason}) while {where}"


def _as_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
