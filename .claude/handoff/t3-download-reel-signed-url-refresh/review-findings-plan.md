---
artifact_type: handoff
artifact_version: 2
producer_role: review
consumer_role: planner
plan_type: findings
review_type: plan
review_round: 1
slug: t3-download-reel-signed-url-refresh
scope_hint: t3-download-reel-signed-url-refresh (T3 plan, round 1)
canonical_name: review-findings-plan
review_status: APPROVE
owner: vd
overlays: []
status: draft
version: 1
created: 2026-07-15T19:32:20Z
updated: 2026-07-15T19:32:20Z
prior_versions: []
---

# Review findings: T3 download_reel — mp4 download + signed-URL refresh (plan review)

**Review type:** plan · iteration 1
**Scope under review:** `planner-task.md` v1 (T3.1–T3.8)

## Triage Decision

Scope: **medium** (new feature, multi-file, touches an IG-hitting metadata path → at-least-medium per project custom rule)
Partition: **backend** (Python MCP tooling, flat-file store, anonymous fetch)
Memory overrides: `always_include` Reliability/Rate-Limit Reviewer; `always_exclude` Accessibility Reviewer (applied)

Selected Reviewers:
- **Reliability / Rate-Limit Reviewer** (backend) — the re-resolve is a metered instagram.com path; politeness is load-bearing
- **Domain Logic Reviewer** (backend) — reel identity (shortcode/media_id vs positional), TTL-freshness semantics
- **Data Integrity & Migration Reviewer** (backend) — atomic CSV rewrite, column preservation, `fetched_at` semantic drift
- **Security & Trust Reviewer** (common) — anonymity invariant on both the CDN GET and the re-resolve
- **Error Handling & Resilience Inspector** (common) — typed envelopes, no-exception-to-MCP, ftyp/0-byte rejection
- **Test Coverage Auditor** (common) — adequacy of the T3.8 suite + pilot
- **Naming & Clarity Guardian** (common, baseline) — envelope field clarity, `fetched_at` overloading

Skipped:
- Accessibility Reviewer — excluded by memory (no UI)
- Concurrency & State Safety — the single-process assumption is explicitly stated and the CSV race is already noted-and-deferred; nothing new to add
- API & Contract Reviewer — MCP tool signature is unchanged (stub→real, same args)

---

## Reliability / Rate-Limit Reviewer

**Verdict: APPROVE** · Confidence: HIGH

The metered/unmetered split is correct and precisely drawn: `fbcdn.net` download is unmetered (follow redirects, never sleep), only the owner-feed re-resolve on `instagram.com` is metered. T3.4 reuses the exact politeness contract the codebase already enforces in `fetch_window` — `classify_response` on every page, stop-and-return on the first `stop_signal`, cap at `max_pages_per_call`, `sleep=None`. Good.

- **[Suggestion]** The plan reuses `_get_feed_page` but stands up a *distinct* traversal (correctly — `fetch_window(TOP_SCAN)` would treat the already-seen target as the caught-up boundary and collect nothing). Ensure the new find-by-identity loop still increments a `pages_fetched` counter and honors the same `DEFAULT_MAX_PAGES=4` cap; the "walk until identity match OR budget exhausted" phrasing must not become an uncapped walk. The plan says "cap at `max_pages_per_call`" — hold that line in code.
- **[FYI]** There is **no cross-call cooldown suppression**. If a prior `list_reels` hit a 401, `State.last_stop_reason` is persisted but without a timestamp, so `download_reel` cannot know it is inside a cooldown and its re-resolve will issue one probing metadata hit. This is **not a regression** — it is exactly the pattern `list_reels` already uses (one call, stop-on-first-signal, no poll loop), so it complies with "stop + return partial on first 401." The invariant's "never poll during a cooldown" targets *loops*, which this is not. Worth a one-line note in the plan that this is a conscious equivalence, not an oversight.

## Domain Logic Reviewer

**Verdict: APPROVE** · Confidence: HIGH

The load-bearing correctness anchor — re-resolve by `code == shortcode` (primary) / `pk == media_id` (numeric backstop), **never** `items[0]` — is exactly right and matches the codebase's existing discipline (`_item_shortcode`/`_item_media_id` are already the identity primitives; shortcodes are never ordered, media_ids are). The standing-order guard test is correctly called mandatory, not optional.

- **[Suggestion]** The freshness gate (T3.3) re-resolves when `video_url` is blank OR age ≥ margin. Confirm the age computation is robust to a **missing/empty `fetched_at`** (older T1 rows, or a row whose `fetched_at` failed to coerce). `ranking._to_int` returns `None` for blank; `now - None` would throw. Treat absent `fetched_at` as "infinitely stale → re-resolve," never as an exception. Add this to the freshness-decision description.

## Data Integrity & Migration Reviewer

**Verdict: APPROVE** · Confidence: HIGH

Atomic CSV rewrite (T3.6) correctly mirrors the store's existing `_write_state_atomic` discipline: full write to a temp path in the same dir + `os.replace`, `csv.DictWriter` with `QUOTE_MINIMAL` and `CSV_COLUMNS` order, preserving every other row/column. This is the right pattern and `os.replace` is atomic within the one `store_dir` filesystem.

- **Open question #2 is RESOLVED by the codebase — persisting the refreshed `video_url` + `fetched_at` is not just safe but *correct*.** Verified: **nothing reads `fetched_at`** today — `ranking.filter_pool`'s `max_age_days` uses `taken_at`, and no `sort_by` key uses `fetched_at`. So refreshing it cannot perturb ranking or age filtering. It becomes the URL-freshness clock, which is precisely T3's new and only consumer. The plan should record this evidence and close OQ#2 rather than carry it as a live question.
- **[Suggestion — semantic drift, Naming overlap]** After this change `fetched_at` means "when `video_url` was last refreshed," not "when the reel was first ingested." Since nothing else reads it, this is acceptable, but note it in a code comment so a future reader (or a future `max_age`-on-`fetched_at` filter) is not surprised.
- **[FYI]** The temp filename must be unique per rewrite (mirror `path.with_suffix(suffix + ".tmp")`). Two concurrent `download_reel` calls on reels in the *same* handle CSV would collide on one temp path — but the single-process assumption is stated and the concurrent-append race is already noted-and-deferred. No action now.

## Security & Trust Reviewer

**Verdict: APPROVE** · Confidence: HIGH

Anonymity holds on both new network paths. The re-resolve goes through `AnonymousClient.get_api` (sole owner of `x-ig-app-id`, `assert_anonymous` on every send, no redirect-follow so a login/challenge 302 reaches the classifier). The binary CDN GET goes through the `get_cdn` sibling — no `x-ig-app-id` (correct, it is a CDN not the API), no cookies, `assert_anonymous(headers=...)` with an empty header set, `allow_redirects=True`. No auth path is introduced anywhere. The per-media `/api/v1/media/{id}/info/` endpoint (dead anonymously) is correctly excluded — re-resolve is owner-feed only.

- No blocking or suggestion items. The "anonymity assertion holds on every issued request" test in T3.8 is the right guard to keep green.

## Error Handling & Resilience Inspector

**Verdict: REQUEST CHANGES (1 should-fix, non-blocking to design)** · Confidence: HIGH

The typed-envelope discipline is strong: unknown shortcode, re-resolve `stop_signal`, not-found-in-budget, and bad download all surface as typed envelope notes, never an exception to the MCP client — matching `run_list_reels`. The temp-write → verify → `os.replace` ordering correctly guarantees a failed/0-byte/302 body never clobbers a prior good file.

- **[Should-fix — ftyp offset]** Both the success metric ("first bytes are a valid MP4 `ftyp` box") and T3.5 ("begins with a valid MP4 `ftyp` box") are **imprecise and will mislead the implementer**. An MP4 does **not** begin with `ftyp` — bytes 0–3 are the box *size* (big-endian uint32), and the ASCII `ftyp` sits at **offset 4–8**. The verifier must assert `data[4:8] == b"ftyp"`, not `data.startswith(b"ftyp")`. Fix the wording in the plan and make the T3.8 reject-test include a payload whose first 4 bytes look like a plausible size but offset 4 is *not* `ftyp`, plus a valid payload with `ftyp` at offset 4. This is the single most likely place the implementation goes subtly wrong.
- **[Suggestion]** "streaming where the transport allows" overstates the current seam. The injected `Transport` Protocol has a fixed signature with no `stream=True` and `_to_view` buffers the whole body; the binary path will read `.content` (full buffer) too. For reels (single-digit to low-tens of MB) a full buffer is fine — but drop the "streaming" language or explicitly add a stream seam, so the implementer doesn't chase a capability the Transport doesn't expose.

## Test Coverage Auditor

**Verdict: APPROVE (with additions)** · Confidence: HIGH

The T3.8 matrix is genuinely thorough — cached-hit zero-network (transport-never-called assertion), in-margin reuse (no metadata, one CDN GET), expired→one re-resolve+download, the standing-order guard (target not first), `stop_signal`→clean partial, unknown-shortcode→typed error, redirect-follow proven, ftyp-reject, manifest-rewrite isolation + quoting, anonymity on every request, plus a live pilot. This covers the load-bearing behaviors.

Add these to close the remaining gaps:
- **[Suggestion] media_id numeric-backstop test.** The plan tests shortcode match "when NOT first" but not the `pk == media_id` backstop. Add a case where the target item's `code` is absent/different but `pk` matches, proving the numeric backstop selects it (and still non-positionally).
- **[Suggestion] not-found-in-budget test.** T3.4 returns a *distinct* outcome for "target aged out of the reachable feed" vs `stop_signal`. Only the `stop_signal` partial is in the test list; add a test that exhausts the page budget without a match and asserts the typed "could not re-resolve within page budget" partial (not an exception, distinct note).
- **[Suggestion] close-the-loop persistence test.** Assert that after an expired-URL re-resolve, the *next* `download_reel` for the same shortcode sees an in-margin URL and issues **zero** metadata calls — this is what makes OQ#2's persistence worth doing.
- **[Suggestion] missing-`fetched_at` freshness test.** A row with blank `fetched_at` must route to re-resolve, not throw (see Domain Logic note).

## Naming & Clarity Guardian

**Verdict: APPROVE** · Confidence: MED

Envelope shape `{shortcode, handle, local_mp4, cached, refreshed, partial, stop_reason, note}` mirrors `list_reels` ergonomics — good. `URL_REFRESH_MARGIN_SECONDS` as a named module constant tied by comment to the measured ~36 h TTL is the right call.

- **[Suggestion]** `refreshed` (bool) and `cached` (bool) can co-occur confusingly — a cached hit is neither refreshed nor a re-resolve. Document the four reachable states in the envelope (cached / reused-in-margin / refreshed-via-re-resolve / partial) so the MCP consumer can interpret them unambiguously.
- **[Suggestion]** Consider `first_fetched_at` vs a separate `url_refreshed_at` if you ever want to preserve original ingest time — but given nothing reads `fetched_at`, overloading it is acceptable now (flagged by Data Integrity).

---

## Review Summary

| Reviewer | Verdict | Blocking | Suggestions | FYI | Confidence |
|---|---|---|---|---|---|
| Reliability / Rate-Limit | APPROVE | 0 | 1 | 1 | HIGH |
| Domain Logic | APPROVE | 0 | 1 | 0 | HIGH |
| Data Integrity & Migration | APPROVE | 0 | 1 | 1 | HIGH |
| Security & Trust | APPROVE | 0 | 0 | 0 | HIGH |
| Error Handling & Resilience | REQUEST CHANGES | 0 | 1 | 0 | HIGH |
| Test Coverage Auditor | APPROVE | 0 | 4 | 0 | HIGH |
| Naming & Clarity Guardian | APPROVE | 0 | 2 | 0 | MED |

**Overall Recommendation: APPROVE** (zero blocking issues; one should-fix wording correction + test additions to fold in before/during build)

**Rationale:** The plan is well-grounded in the existing T1/T2 codebase and honors every CLAUDE.md invariant. The two load-bearing correctness anchors — (a) re-resolve by shortcode/media_id never positional, (b) a provably network-free cached path — are correct and testable with the injected-transport seam the repo already uses. The metered/unmetered split is precise, anonymity holds on both new network paths, and the atomic CSV rewrite faithfully mirrors the store's existing discipline. No design-level blocker exists. The single should-fix is a wording/precision bug in the ftyp check (the `ftyp` box lives at **offset 4**, not byte 0) that would otherwise seed a subtle implementation defect; it is a clarification, not a redesign. The three open questions do **not** block:

- **OQ#1 (TTL margin 24h):** a safe-default value choice, decidable in-plan. 24 h under a ~36 h TTL is comfortable; keep it as the module constant, promote to config later if needed.
- **OQ#2 (persist refreshed `video_url` + `fetched_at`):** **resolved by codebase evidence** — nothing reads `fetched_at` today (ranking age uses `taken_at`), so persisting the refresh is safe *and* correct. Close it.
- **OQ#3 (unknown-shortcode fallback):** **forced by the invariants** — there is no anonymous way to resolve a bare shortcode to its owner (the per-media info endpoint is dead). The typed-error-no-IG-search choice is the only correct one. Close it.

**Blocking Items:** none.

**Top Suggestions (fold into the build):**
1. **Fix the ftyp check to assert `data[4:8] == b"ftyp"`** (offset 4, not byte 0) in both the success metric and T3.5, and mirror it in the reject test. *(should-fix)*
2. Treat missing/blank `fetched_at` as infinitely-stale → re-resolve (never `now - None`).
3. Add tests for: the `pk` numeric backstop, the not-found-in-budget outcome, close-the-loop persistence (next call = zero metadata), and blank-`fetched_at` freshness routing.
4. Drop the "streaming where the transport allows" language (the injected Transport buffers `.content`; fine for reel-sized files) or add a real stream seam.
5. Record OQ#2 and OQ#3 as decided (with the evidence above) rather than open.

**Corroborated Findings (2+ reviewers — highest signal):**
- `fetched_at` semantic overloading — flagged by Data Integrity and Naming. Safe now (no reader), comment it.

**Accepted Debt:** the concurrent CSV-rewrite-vs-append race under the single-process assumption — already noted-and-deferred in the plan's Risks; no locking now. No new debt introduced.

**Memory update suggestion (needs user confirmation, not written):** consider adding to `patterns.md` a note that "IG mp4 ftyp verification checks bytes 4–8, not 0–4" — a repo-specific gotcha likely to recur in the batch-download ticket.
