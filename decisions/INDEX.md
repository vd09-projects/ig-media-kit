# Decision Index

<!-- 
  This file is maintained by the decision-journal skill.
  Entries are in YAML format for machine-friendly querying.
  Newest entries go at the top. Do not manually reorder.
-->

```yaml
decisions:
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
