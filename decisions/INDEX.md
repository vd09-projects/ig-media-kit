# Decision Index

<!-- 
  This file is maintained by the decision-journal skill.
  Entries are in YAML format for machine-friendly querying.
  Newest entries go at the top. Do not manually reorder.
-->

```yaml
decisions:
  - id: 2026-07-16-process-wide-fetchgate-single-ip-serialization
    title: "FetchGate: one process-wide singleton serializes all IG-hitting work (single-IP, FIFO-fair)"
    date: 2026-07-16
    status: accepted
    category: architecture/concurrency
    tags: [fetchgate, concurrency, serialization, single-ip, rate-limit, batch, t4]
    path: architecture/concurrency/2026-07-16-process-wide-fetchgate-single-ip-serialization.md
    summary: "All IG fetch work in the process (batch now; sync list_reels/download once T5 wraps them) is serialized through one module-level FetchGate singleton — at most one IG window in flight, FIFO-fair — because the rate limit is per-IP and the process holds one IP, so parallel fetching buys nothing and risks escalation; CDN downloads stay ungated."

  - id: 2026-07-16-persisted-cooldown-in-gate-metered-stop
    title: "Escalating cooldown is persisted, and note_metered_stop is applied inside the gate critical section"
    date: 2026-07-16
    status: accepted
    category: architecture/reliability
    tags: [fetchgate, cooldown, escalation, 401, back-off, persistence, restart-safety, t4]
    path: architecture/reliability/2026-07-16-persisted-cooldown-in-gate-metered-stop.md
    summary: "The gate persists cooldown_until + escalation_count to store/_batch/_gate.json so a restart mid-cooldown sleeps out the remainder instead of re-hitting IG, and registers the metered-stop back-off while still holding the gate so no second worker can open a window on a just-401'd IP — making the stop/back-off/escalate/never-poll politeness invariant atomic and restart-durable."

  - id: 2026-07-16-daemon-thread-batch-runner-with-explicit-resume
    title: "Batch execution = daemon thread + durable per-window checkpoint + explicit resume_pending_jobs (no broker, no auto-watcher)"
    date: 2026-07-16
    status: accepted
    category: architecture
    tags: [batch, daemon-thread, checkpoint, resume, no-broker, flat-file, job, t4]
    path: architecture/2026-07-16-daemon-thread-batch-runner-with-explicit-resume.md
    summary: "start_batch_fetch returns a job_id instantly and runs _run_job on a daemon thread with per-window checkpointing; a full process restart is recovered by an explicit resume_pending_jobs(config) sweep (called by T5 startup and the first start_batch_fetch), not a module-import side-effect and not a continuously-running orphan watcher — the simplest resume-safe model for the flat-file, no-DB stack."

  - id: 2026-07-16-ssrf-guarded-anonymous-callback-transport
    title: "Callback transport is bare/anonymous and SSRF-guarded; result durability is decoupled from callback delivery"
    date: 2026-07-16
    status: accepted
    category: architecture/security
    tags: [callback, ssrf, anonymous, transport, result-durability, dns-rebind, egress, t4]
    path: architecture/security/2026-07-16-ssrf-guarded-anonymous-callback-transport.md
    summary: "The completion callback POST uses a separate non-IG transport (no x-ig-app-id, no cookies), requires https, blocks private/link-local/loopback/metadata IPs, pins the connection to the validated IP (DNS-rebind TOCTOU), and disables redirect-follow; the aggregated result is persisted to result.json BEFORE any callback attempt so 'done' never depends on delivery (at-least-once best-effort; get_batch_status is the durable fallback)."

  - id: 2026-07-16-aged-out-typed-error-vs-stop-signal-partial
    title: "Aged-out / not-found-in-budget re-resolve returns a typed error with partial=False, disambiguated from stop_signal partial"
    date: 2026-07-16
    status: accepted
    category: architecture/api-contract
    tags: [download_reel, error-envelope, partial, retryability, mcp-contract, cooldown, t3]
    path: architecture/api-contract/2026-07-16-aged-out-typed-error-vs-stop-signal-partial.md
    summary: "download_reel disambiguates two 'no fresh URL' outcomes: metered 401 cooldown returns partial=True + stop_reason (retry in minutes), while a reel aged out of the polite page budget returns a typed error partial=False (retrying now won't help), so an MCP consumer can branch on retryability."

  - id: 2026-07-16-targeted-re-resolve-find-by-identity-traversal
    title: "Targeted owner-feed re-resolve is a distinct find-by-identity traversal, not a reuse of fetch_window(TOP_SCAN)"
    date: 2026-07-16
    status: accepted
    category: architecture
    tags: [download_reel, re-resolve, fetch-engine, top_scan, identity-match, media_id, t3]
    path: architecture/2026-07-16-targeted-re-resolve-find-by-identity-traversal.md
    summary: "Re-resolve walks owner-feed pages matching by shortcode/numeric media_id (never positional order) until identity match or the polite page budget is exhausted; TOP_SCAN can't be reused because it treats the already-seen target as the caught-up boundary and collects nothing."

  - id: 2026-07-16-unknown-shortcode-typed-error-no-search
    title: "Unknown shortcode returns a typed error envelope — no IG-wide search fallback"
    date: 2026-07-16
    status: accepted
    category: architecture
    tags: [download_reel, shortcode, error-envelope, anonymous, store-as-index, t3]
    path: architecture/2026-07-16-unknown-shortcode-typed-error-no-search.md
    summary: "A shortcode not found in any store CSV returns a typed error, not an IG-wide search; the per-media anonymous endpoint /api/v1/media/{id}/info/ is dead (302->login) so there is no safe anonymous shortcode->owner resolution — the store is the only index."

  - id: 2026-07-16-persist-refreshed-url-to-manifest
    title: "On owner-feed re-resolve, persist the fresh video_url and fetched_at back into the manifest row"
    date: 2026-07-16
    status: accepted
    category: architecture
    tags: [download_reel, signed-url, freshness, manifest, atomic-write, store, t3]
    path: architecture/2026-07-16-persist-refreshed-url-to-manifest.md
    summary: "After a re-resolve, download_reel writes the fresh video_url + fetched_at back into the manifest row via atomic CSV rewrite (not use-and-discard); confirmed SAFE against 'never destructively cap' because ranking keys age on taken_at not fetched_at, so it is a pure freshness update that avoids repeat re-resolves."

  - id: 2026-07-16-url-refresh-24h-ttl-margin
    title: "URL-refresh reuse window = 24h named constant, a safety margin under the measured ~36h fbcdn signed-URL TTL"
    date: 2026-07-16
    status: accepted
    category: architecture
    tags: [download_reel, signed-url, ttl, freshness, fbcdn, constant, t3]
    path: architecture/2026-07-16-url-refresh-24h-ttl-margin.md
    summary: "download_reel reuses a stored video_url when fetched_at age < 24h, else re-resolves via the owner feed; 24h is a named module constant chosen as a comfortable margin under the measured ~36h fbcdn signed-URL (oe=) TTL, with a config knob deliberately deferred."

  - id: 2026-07-15-fold-the-pinned-prefix-top-scan-fix-into-t2-step
    title: "Fold the pinned-prefix top_scan fix into T2 (step T2.4a) rather than a standalone T1.x ticket"
    date: 2026-07-15
    status: accepted
    category: architecture/scope
    tags: [top_scan, pinned-reels, data-loss, scoping, T2, fetch-engine]
    path: architecture/scope/2026-07-15-fold-the-pinned-prefix-top-scan-fix-into-t2-step.md
    summary: "Fixed the pinned-prefix top_scan data-loss hole inside T2 as bounded step T2.4a (PINNED_PREFIX_BOUND=3, TOP_SCAN only) rather than splitting a separate T1.x ticket, because it is a hard dependency of T2's acceptance."

  - id: 2026-07-15-serve-from-store-no-network-gate-keys-on
    title: "Serve-from-store no-network gate keys on coverage contiguity, not raw pool count"
    date: 2026-07-15
    status: accepted
    category: architecture
    tags: [serve-from-store, coverage-contiguity, pool-depth, scan_depth, gaps, list_reels]
    path: architecture/2026-07-15-serve-from-store-no-network-gate-keys-on.md
    summary: "Gate the zero-network serve-from-store path on coverage_contiguous (one joined segment reaching scan_depth or account end), splitting it from pool_depth (raw count), so a count>=90-with-gap pool keeps deepening instead of freezing the gap forever."

  - id: 2026-07-15-discovery-correctness-rests-on-per-shortcode
    title: "Discovery correctness rests on per-shortcode dedupe + numeric media_id ordering, never positional feed order"
    date: 2026-07-15
    status: accepted
    category: architecture
    tags: [discovery, dedupe, media_id, watermark, ordering, principle, fetch-engine]
    path: architecture/2026-07-15-discovery-correctness-rests-on-per-shortcode.md
    summary: "Standing principle: since IG feeds are not strictly pk-descending under pinning, discovery relies on per-shortcode dedupe (authoritative caught-up signal) and a monotonic numeric media_id watermark/gap predicates, never positional/newest-first feed order."
```
