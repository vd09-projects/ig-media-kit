"""Async batch runner — the ONLY background-job path in the IG Media Kit (T4).

``run_start_batch_fetch`` hands back a ``job_id`` instantly and continues detached
on a daemon thread: it fills each configured (or requested) handle toward
``scan_depth`` across escalating IG rate-limit cooldowns (the runner is the
system's only sleeper), checkpoints durably after every window so a kill/restart
resumes rather than restarts, aggregates a top-N over the full stored pool
(cross-channel ``global`` or ``per_channel``), optionally downloads the top-N
mp4s via the T3 downloader, and POSTs the aggregated result to a callback URL
with a bounded, SSRF-guarded retry. ``run_get_batch_status`` is a pure read.
``resume_pending_jobs`` re-adopts orphaned jobs from checkpoint after a restart.

Built on reuse of the shipped T2/T3 fetch + store code:
  * ``fill.run_fill`` — one paced, never-sleeping page-budget unit of call-driven
    fill (top-check + deepen, capped at ``max_pages_per_call``, ranked partial on
    stop_signal). This is the command-side fetch primitive extracted from the old
    ``run_list_reels`` when ``list_reels`` became a read-only query (T17 CQRS
    split); the batch runner is the only writer that advances coverage, so it owns
    the metered primitive. The batch LOOPS it under the ``FetchGate``, sleeping
    between units on a metered stop. Completion is the envelope's
    ``coverage.complete`` (contiguity — a single segment reaching scan_depth OR
    the account's real end), NEVER a raw pool count.
  * ``ranking`` — ``load_pool`` / ``filter_pool`` / ``rank`` / ``select_top`` over
    the full deduped manifest (aggregation is order-safe by construction; the
    store already holds the per-shortcode dedupe + monotonic media_id watermark).
  * ``download.run_download_reel`` — never-raises typed envelope, cached-hit
    no-network gate, 24 h TTL re-resolve (an internal re-resolve is IG-metered,
    so the call is wrapped in the gate).

ANONYMITY (CLAUDE.md): every IG hit goes through ``AnonymousClient`` (sole owner
of ``x-ig-app-id``). The callback POST is the one outbound non-IG request — it
uses a SEPARATE bare transport with NO ``x-ig-app-id``, no cookies, no
credentials, and redirect-follow disabled.
"""

from __future__ import annotations

import ipaddress
import socket
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Callable
from urllib.parse import urlparse

from . import ranking
from .config import Config
from .fetch_gate import FetchGate, get_gate
from .fill import run_fill
from .http_client import STOP_SIGNAL_REASONS, AnonymousClient
from .download import run_download_reel
from .ranking import InvalidSortKey
from .store import Store


# --- state model (Step 1) ---------------------------------------------------


class JobPhase(str, Enum):
    QUEUED = "queued"
    FETCHING = "fetching"
    AGGREGATING = "aggregating"
    DOWNLOADING = "downloading"
    CALLING_BACK = "calling_back"
    DONE = "done"
    FAILED = "failed"


_TERMINAL_PHASES = (JobPhase.DONE, JobPhase.FAILED)

# Per-handle fetch outcome flags (keyed on coverage contiguity, never raw count).
OUTCOME_PENDING = "pending"
OUTCOME_COVERED = "covered"    # coverage contiguous (scan_depth or terminal)
OUTCOME_PARTIAL = "partial"    # budget/stop exhausted before contiguity
OUTCOME_ERROR = "error"        # e.g. handle resolve not-found (partial=False)


@dataclass
class HandleProgress:
    """Per-handle progress snapshot in the checkpoint. The real resume cursor +
    coverage segments live in the handle's ``<handle>.state.yaml`` (owned by the
    store); this carries only the coarse outcome flag + effort accounting so a
    restart knows which handle to resume and which are already covered."""

    handle: str
    outcome: str = OUTCOME_PENDING
    stop_reason: str | None = None
    pages_spent: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "HandleProgress":
        return cls(
            handle=d["handle"],
            outcome=d.get("outcome", OUTCOME_PENDING),
            stop_reason=d.get("stop_reason"),
            pages_spent=int(d.get("pages_spent", 0)),
        )


@dataclass
class BatchJob:
    job_id: str
    phase: JobPhase
    params: dict[str, Any]
    per_handle: list[HandleProgress]
    sleep_until: float = 0.0
    escalation_count: int = 0
    heartbeat_at: float = 0.0
    result_ref: str | None = None
    callback: dict[str, Any] = field(
        default_factory=lambda: {"attempts": 0, "next_retry_at": None,
                                 "last_status": None, "delivered": False})
    downloads: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    created: float = 0.0
    updated: float = 0.0

    def handles(self) -> list[str]:
        return [hp.handle for hp in self.per_handle]

    def progress(self, handle: str) -> HandleProgress:
        for hp in self.per_handle:
            if hp.handle == handle:
                return hp
        hp = HandleProgress(handle)
        self.per_handle.append(hp)
        return hp

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "phase": self.phase.value,
            "params": self.params,
            "per_handle": [hp.to_dict() for hp in self.per_handle],
            "sleep_until": self.sleep_until,
            "escalation_count": self.escalation_count,
            "heartbeat_at": self.heartbeat_at,
            "result_ref": self.result_ref,
            "callback": self.callback,
            "downloads": self.downloads,
            "error": self.error,
            "created": self.created,
            "updated": self.updated,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "BatchJob":
        return cls(
            job_id=d["job_id"],
            phase=JobPhase(d["phase"]),
            params=d.get("params") or {},
            per_handle=[HandleProgress.from_dict(x) for x in d.get("per_handle", [])],
            sleep_until=float(d.get("sleep_until", 0.0)),
            escalation_count=int(d.get("escalation_count", 0)),
            heartbeat_at=float(d.get("heartbeat_at", 0.0)),
            result_ref=d.get("result_ref"),
            callback=d.get("callback") or {"attempts": 0, "next_retry_at": None,
                                           "last_status": None, "delivered": False},
            downloads=d.get("downloads") or {},
            error=d.get("error"),
            created=float(d.get("created", 0.0)),
            updated=float(d.get("updated", 0.0)),
        )


# --- dependency injection (all injectables flow through one struct) ----------


@dataclass
class BatchDeps:
    """Everything the runner touches the outside world through — injected whole in
    tests (fake clock, fake transport-backed client, fake resolver/poster) so CI
    does zero real sleeps and zero real IG/callback network."""

    config: Config
    store: Store
    gate: FetchGate
    clock: Callable[[], float] = time.time
    sleep: Callable[[float], None] = time.sleep
    client_factory: Callable[[], AnonymousClient] = AnonymousClient
    resolver: Callable[[str], list[str]] = None  # set in __post_init__
    poster: Callable[..., int] = None            # set in __post_init__
    jitter: Callable[[float], float] = lambda _base: 0.0

    def __post_init__(self) -> None:
        if self.resolver is None:
            self.resolver = _default_resolver
        if self.poster is None:
            self.poster = _default_poster


def _default_deps(config: Config) -> BatchDeps:
    return BatchDeps(
        config=config,
        store=Store(config.output.store_dir),
        gate=get_gate(config),
    )


# --- thread registry + launch guard (idempotent resume) ---------------------

_THREADS: dict[str, threading.Thread] = {}
_THREADS_LOCK = threading.Lock()
_RESUMED_ONCE = False
_RESUME_LOCK = threading.Lock()


def _launch(job_id: str, target: Callable[[], None]) -> bool:
    """Start a daemon worker for ``job_id`` unless one is already alive. Returns
    True iff a NEW thread was started (the idempotency guard against the two entry
    points — ``start_batch_fetch`` and ``resume_pending_jobs`` — double-adopting
    the same job)."""
    with _THREADS_LOCK:
        existing = _THREADS.get(job_id)
        if existing is not None and existing.is_alive():
            return False
        t = threading.Thread(target=target, name=f"batch-{job_id}", daemon=True)
        _THREADS[job_id] = t
    t.start()
    return True


def _worker_alive(job_id: str) -> bool:
    with _THREADS_LOCK:
        t = _THREADS.get(job_id)
        return t is not None and t.is_alive()


def reset_batch_state() -> None:
    """Drop the thread registry + one-shot resume guard (test-only seam)."""
    global _RESUMED_ONCE
    with _THREADS_LOCK:
        _THREADS.clear()
    with _RESUME_LOCK:
        _RESUMED_ONCE = False


# --- per-handle fill loop (Step 4) ------------------------------------------


def _fill_handle(job: BatchJob, handle: str, *, deps: BatchDeps,
                 client: AnonymousClient, checkpoint: Callable[[], None]) -> None:
    """Fill one handle toward contiguity, one gate-gated page-budget unit at a
    time, sleeping out cooldowns between units. Reuses ``fill.run_fill`` (which
    never sleeps and is capped at ``max_pages_per_call``) as the per-unit
    primitive; the gate + this loop own the serialization and the sleeping.

    ``run_fill`` is the command-side fetch unit extracted from the old
    ``run_list_reels`` when ``list_reels`` became read-only (T17 CQRS split): the
    batch runner is the only writer that advances coverage toward scan_depth, so
    it owns the metered primitive. The envelope contract read below
    (``error`` / ``coverage.complete`` / ``stop_reason`` / ``partial`` /
    ``pages_fetched``) is unchanged from the pre-split compose.

    # TODO: reconcile the batch per-unit primitive with window.run_window; the T4
    # plan named run_window as the per-window call, but run_window is TOP_SCAN-only
    # (no deepen, no cursor resume toward scan_depth), so this loops run_fill
    # (top-check + deepen + cursor resume) instead. run_window is now unreferenced
    # — either retire it or route the batch through it once it can deepen, to avoid
    # two divergent compose paths."""
    store = deps.store
    gate = deps.gate
    config = deps.config
    filters = job.params.get("filters") or {}
    budget = config.batch.per_job_page_budget
    hp = job.progress(handle)

    # A stall = a completed unit that neither grew the pool nor reached
    # contiguity (e.g. a 0-page resolve-stop, or a deepen that returned nothing).
    # Bounded so a hard IG block can never spin the loop forever advancing only
    # (virtual) cooldown time. Reset on real progress.
    max_stalls = config.batch.retries + 2
    stalls = 0

    while hp.pages_spent < budget:
        before = store.count_reels(handle)
        with gate.acquire():
            env = run_fill(
                handle,
                config=config,
                client=client,
                store=store,
                min_views=filters.get("min_views"),
                min_duration=filters.get("min_duration"),
                max_age_days=filters.get("max_age_days"),
                now=lambda: int(deps.clock()),
            )
            # Classify the outcome and apply the cooldown mutation WHILE STILL
            # HOLDING THE GATE (F1). If the escalated back-off were registered
            # after the `with` block released, a second worker — resume relaunches
            # every non-terminal job on its own thread, and a new start_batch_fetch
            # can overlap — could acquire the gate and hit the just-401'd IP before
            # `cooldown_until` was set, its `_sleep_out_cooldown` reading the stale
            # value and sleeping nothing. That extra window on an already
            # rate-limited IP is exactly the abuse that ESCALATES the cooldown,
            # breaching the "stop/back-off on first 401" politeness invariant.
            # Escalating here means the next holder's acquire() observes the fresh
            # cooldown and sleeps it out. NEVER poll IG during the cooldown.
            err = bool(env.get("error"))
            complete = bool(env.get("coverage", {}).get("complete"))
            stop_reason = env.get("stop_reason")
            metered = (not err and not complete
                       and bool(env.get("partial"))
                       and stop_reason in STOP_SIGNAL_REASONS)
            if metered:
                gate.note_metered_stop(stop_reason)
            elif not err and not complete:
                # A clean (non-metered) unit — decay the escalation. Also mutated
                # inside the gate so success/metered ordering stays consistent.
                gate.note_success()
        after = store.count_reels(handle)

        hp.pages_spent += int(env.get("pages_fetched", 0) or 0)
        hp.stop_reason = stop_reason
        job.heartbeat_at = deps.clock()

        if err:
            hp.outcome = OUTCOME_ERROR
            checkpoint()
            return
        if complete:
            hp.outcome = OUTCOME_COVERED
            checkpoint()
            return

        if metered:
            job.sleep_until = gate.cooldown_until
            job.escalation_count = gate.escalation_count
            hp.outcome = OUTCOME_PARTIAL
            checkpoint()
            # A metered stop that fetched nothing is a stall; one that persisted
            # rows made progress.
            stalls = 0 if after > before else stalls + 1
            if stalls > max_stalls:
                return
            continue

        # A clean (non-metered) unit — cooldown escalation already decayed above.
        job.sleep_until = 0.0
        job.escalation_count = gate.escalation_count
        hp.outcome = OUTCOME_PARTIAL
        checkpoint()

        stalls = 0 if after > before else stalls + 1
        if stalls > max_stalls:
            # Made no headway and not a rate-limit — give up rather than spin.
            return

    # Page budget exhausted before contiguity.
    hp.outcome = OUTCOME_PARTIAL
    checkpoint()


# --- whole-job driver (Step 5) ----------------------------------------------


def _run_job(job_id: str, deps: BatchDeps) -> None:
    """Drive one job through its phases, resuming from whatever phase the loaded
    checkpoint is in. Strictly sequential handles; checkpoint between each so a
    restart resumes at the right handle + cursor. Never raises out of the thread
    — an unexpected fault lands the job in ``failed`` with the error recorded."""
    store = deps.store
    data = store.load_batch_job(job_id)
    if data is None:
        return
    job = BatchJob.from_dict(data)

    def checkpoint() -> None:
        job.updated = deps.clock()
        store.save_batch_job(job_id, job.to_dict())

    try:
        client = deps.client_factory()

        if job.phase in (JobPhase.QUEUED, JobPhase.FETCHING):
            job.phase = JobPhase.FETCHING
            job.heartbeat_at = deps.clock()
            checkpoint()
            for handle in job.handles():
                if job.progress(handle).outcome == OUTCOME_COVERED:
                    continue  # resume: already-covered handles are untouched
                _fill_handle(job, handle, deps=deps, client=client,
                             checkpoint=checkpoint)
            job.phase = JobPhase.AGGREGATING
            checkpoint()

        if job.phase == JobPhase.AGGREGATING:
            envelope = _aggregate(job, deps=deps)
            # Persist the result FIRST — before any download/callback — so it is
            # durable independent of delivery (result durability ⟂ callback).
            store.save_batch_result(job_id, envelope)
            job.result_ref = f"{job_id}.result.json"
            job.phase = (JobPhase.DOWNLOADING
                         if job.params.get("download_top") else JobPhase.CALLING_BACK)
            checkpoint()

        if job.phase == JobPhase.DOWNLOADING:
            _download_top(job, deps=deps, client=client, checkpoint=checkpoint)
            job.phase = JobPhase.CALLING_BACK
            checkpoint()

        if job.phase == JobPhase.CALLING_BACK:
            if job.params.get("callback_url"):
                _post_callback(job, deps=deps, checkpoint=checkpoint)
            job.phase = JobPhase.DONE
            checkpoint()

        if job.phase not in _TERMINAL_PHASES:
            job.phase = JobPhase.DONE
            checkpoint()
    except Exception as exc:  # noqa: BLE001 — the worker must never crash silently.
        job.phase = JobPhase.FAILED
        job.error = f"{type(exc).__name__}: {exc}"
        try:
            checkpoint()
        except Exception:  # noqa: BLE001
            pass
    finally:
        with _THREADS_LOCK:
            _THREADS.pop(job_id, None)


# --- aggregation (Step 6) ---------------------------------------------------


def _aggregate(job: BatchJob, *, deps: BatchDeps) -> dict[str, Any]:
    """Build the stable result envelope over the full stored pool. ``global``
    merges every handle's manifest then ranks once (``results["*"]``);
    ``per_channel`` ranks each handle independently (``results[handle]``). Both
    fill the SAME top-level envelope shape. Reads only persisted manifests, so it
    is order-safe by construction (dedupe + watermark already hold there)."""
    store = deps.store
    params = job.params
    sort_by = ranking.validate_sort_by(params.get("sort_by"))
    count = int(params.get("count"))
    filters = params.get("filters") or {}
    scope = params.get("scope")
    handles = job.handles()
    now = lambda: int(deps.clock())  # noqa: E731

    results: dict[str, list[dict[str, Any]]] = {}
    if scope == "global":
        merged: list[dict[str, Any]] = []
        for handle in handles:
            merged.extend(ranking.load_pool(store.csv_path(handle)))
        filtered = ranking.filter_pool(
            merged, min_views=filters.get("min_views"),
            min_duration=filters.get("min_duration"),
            max_age_days=filters.get("max_age_days"), now=now,
        )
        ranked = ranking.rank(filtered, sort_by)
        results["*"] = ranked[:count]
    else:  # per_channel
        for handle in handles:
            results[handle] = ranking.select_top(
                store.csv_path(handle), count=count, sort_by=sort_by,
                min_views=filters.get("min_views"),
                min_duration=filters.get("min_duration"),
                max_age_days=filters.get("max_age_days"), now=now,
            )

    return _build_envelope(job, deps, results, sort_by=sort_by, count=count,
                           filters=filters)


def _build_envelope(job: BatchJob, deps: BatchDeps,
                    results: dict[str, list[dict[str, Any]]], *,
                    sort_by: str, count: int, filters: dict[str, Any]) -> dict[str, Any]:
    per_handle_fetch = {hp.handle: hp.outcome for hp in job.per_handle}
    errors = [f"{hp.handle}: {hp.stop_reason or hp.outcome}"
              for hp in job.per_handle if hp.outcome == OUTCOME_ERROR]
    return {
        "job_id": job.job_id,
        "scope": job.params.get("scope"),
        "sort_by": sort_by,
        "count": count,
        "filters": filters,
        "status": job.phase.value,
        "generated_at": int(deps.clock()),
        "results": results,             # ALWAYS a map: real handles / "*" for global
        "per_handle_fetch": per_handle_fetch,
        "downloads": dict(job.downloads),
        "errors": errors,
    }


# --- optional top-N download (Step 7) ---------------------------------------


def _download_top(job: BatchJob, *, deps: BatchDeps, client: AnonymousClient,
                  checkpoint: Callable[[], None]) -> None:
    """Download the aggregated top-N mp4s. The CDN bytes are unmetered, but a
    re-resolve inside ``run_download_reel`` is IG-metered — so each call is
    wrapped in the gate. A failed download is a typed note, never a job failure."""
    store = deps.store
    config = deps.config
    gate = deps.gate
    envelope = store.load_batch_result(job.job_id) or {}
    shortcodes: list[str] = []
    for lst in (envelope.get("results") or {}).values():
        for reel in lst:
            sc = reel.get("shortcode")
            if sc:
                shortcodes.append(sc)

    for sc in dict.fromkeys(shortcodes):  # dedupe, preserve order
        if sc in job.downloads:
            continue  # resume: already handled this shortcode
        with gate.acquire():
            res = run_download_reel(
                sc, config=config, client=client, store=store,
                now=lambda: int(deps.clock()),
            )
        job.downloads[sc] = {
            "local_mp4": res.get("local_mp4"),
            "partial": bool(res.get("partial")),
            "error": res.get("error"),
            "note": res.get("note", ""),
        }
        job.heartbeat_at = deps.clock()
        checkpoint()

    # Fold the downloads map into the durable result envelope too.
    envelope["downloads"] = dict(job.downloads)
    store.save_batch_result(job.job_id, envelope)


# --- callback poster + SSRF guard (Step 8) ----------------------------------


def _is_public_ip(ip: str) -> bool:
    """True iff ``ip`` is a globally-routable unicast address. Rejects private
    (RFC1918 / fc00::/7), loopback, link-local (incl. 169.254.169.254 cloud
    metadata), multicast, reserved, and unspecified addresses."""
    try:
        obj = ipaddress.ip_address(ip)
    except ValueError:
        return False
    if (obj.is_private or obj.is_loopback or obj.is_link_local
            or obj.is_multicast or obj.is_reserved or obj.is_unspecified):
        return False
    return obj.is_global


def validate_callback_url(url: str, *, resolver: Callable[[str], list[str]]):
    """SSRF guard. Returns ``(ok, ip, port, host, error)``.

    Requires ``https``; resolves the hostname and rejects if ANY resolved address
    is non-public; pins to the first validated address so a later re-resolve
    (DNS rebinding) cannot swap in an internal IP between validation and POST.
    Re-invoked immediately before EACH POST attempt, not only at submit time."""
    try:
        parsed = urlparse(url)
    except (ValueError, TypeError):
        return (False, None, None, None, "unparseable url")
    if parsed.scheme != "https":
        return (False, None, None, None, "callback scheme must be https")
    host = parsed.hostname
    if not host:
        return (False, None, None, None, "callback url has no host")
    port = parsed.port or 443
    try:
        ips = resolver(host)
    except Exception as exc:  # noqa: BLE001 — a DNS failure is a rejection, not a crash.
        return (False, None, None, host, f"dns resolution failed: {exc}")
    if not ips:
        return (False, None, None, host, "callback host resolved to no addresses")
    for ip in ips:
        if not _is_public_ip(ip):
            return (False, None, None, host, f"non-public callback address: {ip}")
    return (True, ips[0], port, host, "")


def _post_callback(job: BatchJob, *, deps: BatchDeps,
                   checkpoint: Callable[[], None]) -> None:
    """POST the durable result envelope to ``callback_url`` with bounded
    retry+backoff. At-least-once, best-effort — the result is already persisted,
    so callback failure never blocks ``done``. Re-validates + re-pins the target
    immediately before each attempt (DNS-rebind guard); redirect-follow off."""
    url = job.params.get("callback_url")
    envelope = deps.store.load_batch_result(job.job_id) or {}
    cb = job.callback
    retries = int(deps.config.batch.retries)
    base = float(deps.config.batch.backoff_base_s)
    cap = float(deps.config.batch.backoff_cap_s)

    start_attempt = int(cb.get("attempts", 0))
    for attempt in range(start_attempt, retries):
        ok, ip, port, host, err = validate_callback_url(url, resolver=deps.resolver)
        if not ok:
            cb["attempts"] = attempt + 1
            cb["last_status"] = f"rejected: {err}"
            cb["delivered"] = False
            checkpoint()
            return  # SSRF rejection — no POST reaches the target.

        status: int | None
        try:
            status = deps.poster(url, envelope, pinned_ip=ip, port=port, host=host)
        except Exception as exc:  # noqa: BLE001 — a transport error is a failed attempt.
            status = None
            cb["last_error"] = f"{type(exc).__name__}: {exc}"

        cb["attempts"] = attempt + 1
        cb["last_status"] = status
        job.heartbeat_at = deps.clock()

        if status is not None and 200 <= status < 300:
            cb["delivered"] = True
            checkpoint()
            return

        # Failed attempt — back off before the next one (if any remain).
        if attempt + 1 < retries:
            delay = min(cap, base * (2 ** attempt)) + deps.jitter(base)
            cb["next_retry_at"] = deps.clock() + delay
            checkpoint()
            deps.sleep(delay)
        else:
            checkpoint()


def _default_resolver(host: str) -> list[str]:
    """Resolve ``host`` to its A/AAAA addresses (deduped)."""
    infos = socket.getaddrinfo(host, None)
    return list({info[4][0] for info in infos})


def _default_poster(url: str, envelope: dict[str, Any], *,
                    pinned_ip: str, port: int, host: str) -> int:
    """POST ``envelope`` as JSON to ``url`` via a BARE non-IG transport.

    Carries NO ``x-ig-app-id``, no cookies, no credentials (anonymity: the
    callback is not an IG call and must never look like one). Redirect-follow is
    DISABLED so a 3xx cannot bounce the POST to an internal host or to
    instagram.com. The connection is PINNED to ``pinned_ip`` via curl's resolve
    map so the address validated by the SSRF guard is exactly the one dialled —
    closing the DNS-rebind TOCTOU (a hostname resolving safe at validation then
    to a metadata IP at connect time)."""
    import json as _json

    from curl_cffi import requests as cffi_requests  # local import by design

    # NOTE (F4): the resolve-map entry is ``host:port:ip``. This is exercised
    # only with IPv4 literals today (callbacks pin the first validated address,
    # and no AAAA is pinned in practice). If an IPv6 literal is ever pinned here,
    # some curl builds require it bracketed (``host:port:[addr]``) in the resolve
    # map — add a v6 case to the SSRF guard test and bracket the literal then.
    body = _json.dumps(envelope).encode("utf-8")
    resp = cffi_requests.post(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        allow_redirects=False,          # a 3xx must NOT be followed (SSRF).
        resolve=[f"{host}:{port}:{pinned_ip}"],  # pin to the validated address.
        timeout=30,
    )
    return int(resp.status_code)


# --- entrypoints (Step 9) ---------------------------------------------------


def _maybe_resume_once(config: Config, deps: BatchDeps) -> None:
    """Run ``resume_pending_jobs`` exactly once per process on the first
    ``start_batch_fetch`` — an EXPLICIT re-adoption of orphans left by a prior
    crash, NOT a module-import side-effect."""
    global _RESUMED_ONCE
    with _RESUME_LOCK:
        if _RESUMED_ONCE:
            return
        _RESUMED_ONCE = True
    resume_pending_jobs(config, deps=deps)


def run_start_batch_fetch(
    *,
    config: Config,
    handles: list[str] | None = None,
    scope: str = "global",
    count: int | None = None,
    sort_by: str | None = None,
    filters: dict[str, Any] | None = None,
    download_top: bool = False,
    callback_url: str | None = None,
    deps: BatchDeps | None = None,
    background: bool = True,
) -> dict[str, Any]:
    """Validate the pinned param contract, create + checkpoint a ``queued`` job,
    launch it detached, and return ``{job_id, phase}`` immediately. Never raises —
    a validation failure returns a typed envelope with ``ok: False``."""
    deps = deps or _default_deps(config)
    store = deps.store

    # Explicitly re-adopt any orphaned jobs once, before starting a new one.
    _maybe_resume_once(config, deps)

    scope = scope or "global"
    if scope not in ("global", "per_channel"):
        return _rejected(f"invalid scope {scope!r}; use 'global' or 'per_channel'")
    try:
        sort_by = ranking.validate_sort_by(sort_by)
    except InvalidSortKey as exc:
        return _rejected(str(exc))

    count = config.top_reels.count if count is None else int(count)
    if count <= 0:
        return _rejected(f"count must be > 0, got {count}")

    resolved_handles = list(config.channels) if handles is None else list(handles)
    if not resolved_handles:
        return _rejected("no handles: pass handles= or configure channels[]")
    if handles is not None:
        allowed = set(config.channels) | set(store.handles_on_disk())
        unknown = [h for h in resolved_handles if h not in allowed]
        if unknown:
            return _rejected(
                f"unknown handles {unknown}; not in config.channels or on disk")

    if callback_url is not None:
        ok, _ip, _port, _host, err = validate_callback_url(
            callback_url, resolver=deps.resolver)
        if not ok:
            return _rejected(f"callback_url rejected: {err}")

    filters = dict(filters or {})
    job_id = uuid.uuid4().hex
    now = deps.clock()
    job = BatchJob(
        job_id=job_id,
        phase=JobPhase.QUEUED,
        params={
            "handles": resolved_handles,
            "scope": scope,
            "count": count,
            "sort_by": sort_by,
            "filters": filters,
            "download_top": bool(download_top),
            "callback_url": callback_url,
        },
        per_handle=[HandleProgress(h) for h in resolved_handles],
        heartbeat_at=now,
        created=now,
        updated=now,
    )
    store.save_batch_job(job_id, job.to_dict())

    if background:
        _launch(job_id, lambda: _run_job(job_id, deps))
        phase = JobPhase.QUEUED.value
    else:
        _run_job(job_id, deps)
        reloaded = store.load_batch_job(job_id)
        phase = reloaded["phase"] if reloaded else JobPhase.FAILED.value

    return {"ok": True, "job_id": job_id, "phase": phase,
            "note": f"batch job {job_id} started ({scope}, {len(resolved_handles)} handles)"}


def run_get_batch_status(job_id: str, *, config: Config,
                         deps: BatchDeps | None = None) -> dict[str, Any]:
    """Pure read of a job's checkpoint + final result. NO IG network, never
    triggers a fetch — safe during a cooldown. Classifies liveness (fetching /
    cooldown-sleeping / dead-worker). Unknown ``job_id`` → typed not-found."""
    deps = deps or _default_deps(config)
    store = deps.store
    data = store.load_batch_job(job_id)
    if data is None:
        return {"found": False, "job_id": job_id, "phase": None,
                "note": f"unknown job_id {job_id!r}"}
    job = BatchJob.from_dict(data)
    result = store.load_batch_result(job_id) if job.result_ref else None
    return {
        "found": True,
        "job_id": job_id,
        "phase": job.phase.value,
        "liveness": _classify_liveness(job, deps),
        "per_handle": {hp.handle: hp.outcome for hp in job.per_handle},
        "sleep_until": job.sleep_until,
        "heartbeat_at": job.heartbeat_at,
        "callback": job.callback,
        "downloads": job.downloads,
        "result": result,
        "error": job.error,
        "note": job.error or f"job {job.phase.value}",
    }


def _classify_liveness(job: BatchJob, deps: BatchDeps) -> str:
    """Distinguish a live worker, a job sleeping out a cooldown, and a crashed
    (dead) worker — so a caller can tell a sleeping job from one that needs
    ``resume_pending_jobs``."""
    if job.phase in _TERMINAL_PHASES:
        return job.phase.value
    now = deps.clock()
    # Liveness FIRST (F2): a worker that sleeps out a cooldown is still an alive
    # daemon thread (blocked in _sleep_out_cooldown), so check the thread before
    # the cooldown clock. Otherwise a worker that CRASHED mid-cooldown — thread
    # gone, but the last checkpoint still carries a future sleep_until — would be
    # masked as "cooldown-sleeping" (indistinguishable from a healthy sleeper) for
    # up to cooldown_cap_s, exactly when a caller most needs to see it is dead.
    sleeping = bool(job.sleep_until) and now < job.sleep_until
    if _worker_alive(job.job_id):
        return "cooldown-sleeping" if sleeping else "fetching"
    # No live worker: a stale checkpoint with a future cooldown is a crash
    # mid-cooldown, NOT a healthy sleep — fall through to the staleness check so it
    # surfaces as dead-worker rather than a phantom sleeper.
    stale_after = float(deps.config.batch.heartbeat_stale_s)
    if not job.heartbeat_at or (now - job.heartbeat_at) > stale_after:
        return "dead-worker"
    # Fresh heartbeat, no live thread — presumably between windows (a worker just
    # popped from the registry in its finally; a resume will re-adopt it).
    return "fetching"


def resume_pending_jobs(config: Config, *,
                        deps: BatchDeps | None = None) -> dict[str, Any]:
    """The restart-resume entrypoint (T5 calls this on server startup; the first
    ``start_batch_fetch`` also calls it once). Sweeps orphaned ``*.tmp`` files,
    then relaunches every non-terminal job from its checkpoint on a fresh daemon
    thread. IDEMPOTENT: the per-job launch guard means a job already running (or
    already relaunched) is never double-launched; re-entry re-fetches from the
    persisted cursor (dedupe + watermark make re-adoption safe)."""
    deps = deps or _default_deps(config)
    store = deps.store
    swept = store.sweep_batch_tmp()

    relaunched: list[str] = []
    skipped: list[str] = []
    for job_id in store.list_batch_jobs():
        data = store.load_batch_job(job_id)
        if not data:
            continue
        try:
            job = BatchJob.from_dict(data)
        except (KeyError, ValueError):
            continue
        if job.phase in _TERMINAL_PHASES:
            continue
        if _launch(job_id, lambda jid=job_id: _run_job(jid, deps)):
            relaunched.append(job_id)
        else:
            skipped.append(job_id)  # already alive — the guard held.

    return {"relaunched": relaunched, "skipped": skipped,
            "tmp_swept": swept, "count": len(relaunched)}


def _rejected(note: str) -> dict[str, Any]:
    return {"ok": False, "job_id": None, "phase": None, "error": note, "note": note}
