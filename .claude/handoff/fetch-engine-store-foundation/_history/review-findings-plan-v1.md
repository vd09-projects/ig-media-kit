---
artifact_type: handoff
artifact_version: 2
producer_role: review
consumer_role: planner
plan_type: findings
review_type: plan
review_round: 1
slug: fetch-engine-store-foundation
scope_hint: fetch-engine-store-foundation (T1 plan, round 1)
canonical_name: review-findings-plan
review_status: REQUEST_CHANGES
owner: vd
overlays: []
status: draft
version: 1
created: 2026-07-14T17:54:00Z
updated: 2026-07-14T17:54:00Z
prior_versions: []
---

# Review findings: fetch-engine-store-foundation (T1 plan, round 1)

Multi-perspective panel review of the **PLAN** for T1 — Fetch engine + store foundation. This is a pre-build plan review (review-type=plan): the question is whether the plan is sound, complete, correctly sequenced, and whether it honors the project's load-bearing invariants before any code is written. No code exists yet; findings target design gaps, risky assumptions, sequencing hazards, and missing acceptance coverage.

## Triage Decision
Scope: large (foundational; touches HTTP client, fetch primitive, store, config, MCP boot)
Partition: backend (Python service; flat-file store; network I/O)
Memory overrides: none (no multi-perspective-review skill-memory present)

Selected Reviewers:
- Error Handling & Resilience Inspector (backend) — the whole primitive hinges on 401/partial/cooldown behavior
- Security & Trust Reviewer (common) — ANONYMOUS-ONLY is the top load-bearing invariant
- Data Integrity & Migration Reviewer (backend) — CSV+YAML store, dedupe, cursor, atomicity, "never destructively capped"
- Domain Logic Reviewer (backend) — feed traversal semantics (new vs deep), scan_depth accumulation
- API & Contract Reviewer (backend) — the shared fetch primitive is the contract three later tools consume
- Test Coverage Auditor (common) — acceptance coverage, AC enumeration, reproducible tests
- Performance & Scalability Critic (backend) — rate-limit budget during dev iteration
- Tech Debt Sentinel (common, baseline) — TSV fork, speculative state, deferred concerns
- Naming & Clarity Guardian (common, baseline) — undefined ACs, ambiguous "resolvable"

Skipped:
- Concurrency & State Safety Reviewer — escalated inline (batch runner out of T1 scope, but single-writer assumption flagged as FYI)
- Accessibility / CSS / State Management / FE reviewers — no frontend surface
- Infrastructure & Deployment Reviewer — no CI/Docker/k8s in this plan
- Documentation Reviewer — folded into DX/Naming findings

---

## Error Handling & Resilience Inspector
*"The happy path is a rumor; show me what happens when it breaks."*

**BLOCKING — B1: The stop-signal is modeled as "first 401", but IG's real throttle/block family is wider.** The plan repeatedly anchors politeness to "stop + partial on the FIRST 401." CLAUDE.md itself notes the per-media endpoint returns **302 → login**, and rate-limited public endpoints commonly return **429** or a **200 carrying a challenge/error JSON**, not always a clean 401. If the loop only recognizes literal HTTP 401 as "stop," a 429/302/challenge slips the guard and the loop keeps paging — a direct violation of the load-bearing "never poll during cooldown" invariant, and it will *escalate* the cooldown (48→36→12). Fix before build: (a) T1.2 probe must **enumerate the actual status codes/bodies IG returns under throttle and under block**, not assume 401; (b) T1.3/T1.5 must treat the whole throttled/blocked family (401, 429, 403, 302-to-login, challenge JSON) as stop-and-return-partial. Confidence: HIGH.

**BLOCKING — B2 (shared with Data Integrity): Partial persistence has no atomicity or cursor-advance ordering defined.** T1.7 promises "a 401 mid-window still persists a valid partial + cursor," but writing CSV rows and updating `state.yaml` is a two-file, non-atomic operation. Two failure orderings exist and the plan picks neither: if the cursor/high-water advances *before* the rows are durably written and the process dies, those reels are skipped **forever** (cursor moved past them, skip-seen never re-encounters them) — that silently violates "store is never destructively capped." Fix: specify (1) write/flush CSV rows first, (2) advance cursor/high_water **only for items actually persisted**, (3) atomic write of `state.yaml` via temp-file + `os.replace`. Confidence: HIGH.

**Suggestion:** Define behavior for the *first-page* 401 (before any item is fetched). "Partial" with zero rows is valid, but the acceptance test should assert it produces no traceback and leaves prior state untouched (not a zeroed cursor). Confidence: HIGH.

## Security & Trust Reviewer
*"Anonymous-only is not a vibe; it's an assertion that must be enforced and tested."*

**BLOCKING — B3: "No cookie jar" conflates 'no authenticated session' with 'no cookies at all', and the invariant is under-specified.** The invariant is ANONYMOUS (no login/session/sessionid/ds_user_id). But `curl_cffi` with `impersonate="chrome"` and a Session normally *accepts* the anonymous cookies IG sets (`mid`, `csrftoken`, `ig_did`) — these are not identity, and stripping them entirely can make requests look *more* bot-like and increase blocking. The plan's T1.3 phrasing "no cookie jar" risks either (a) breaking/degrading the fetch, or (b) being interpreted so loosely it lets an auth cookie through later. Resolve before coding T1.3: define anonymous precisely as **"no `sessionid`/`ds_user_id`/authenticated cookies and no login/auth params; benign anonymous cookies IG issues are permitted"**, and have T1.2 observe whether anonymous cookies are required for the feed endpoint to respond. Confidence: MED (depends on live probe).

**Suggestion:** The counter-metric "zero authenticated requests" needs a *positive test*, not "by construction." Add an acceptance assertion that inspects the outgoing request and confirms no `Cookie: sessionid=...`, no `Authorization`, no auth query params. Lock the invariant with a test, so no later tool can regress it silently. Confidence: HIGH.

**FYI:** `x-ig-app-id: 936619743392459` must be asserted present on *every* metadata request in the same test — it's a required header, and a missing-header regression would look like a random block.

## Domain Logic Reviewer
*"What does the feature actually mean when the data moves?"*

**BLOCKING — B4: New-posts vs deep-paging traversal is conflated; "a second call adds only new rows" is undefined.** The feed returns newest-first; `next_max_id` pages *older/deeper*. There are two distinct traversal intents and the plan collapses them: (1) **catch new posts** since last run (page from the top until you hit `high_water_id`), vs (2) **go deeper** toward `scan_depth=90` (resume from saved `next_max_id`). T1.5/T1.6 say "resume from saved next_max_id, don't re-page" — that only ever goes deeper and will **never surface reels posted since the last run**. Yet the success metric's "second call adds only new rows" most naturally reads as *newer* posts. Which is it? This is the core correctness question of the whole primitive and must be answered before T1.5 is built. Recommend: define the two modes explicitly, decide which T1 implements (likely: top-scan to high_water on every call PLUS optional deep-resume toward scan_depth), and make `high_water_id` vs `deep_cursor` roles unambiguous in state. Confidence: HIGH.

**Suggestion:** `high_water_id` is stored but nothing in T1 is shown to *consume* it (skip-seen is per-shortcode). Either wire it to the top-scan stop condition (per B4) or mark it explicitly as forward-looking state — otherwise it's speculative. Confidence: MED.

## Data Integrity & Migration Reviewer
*"The store is the source of truth; treat every write like it might be the last."*

Corroborates **B2** (atomicity/ordering) — strongest-signal finding, act first.

**Suggestion — TSV-fork is a data-dependent schema.** "CSV, or TSV if captions carry commas" means a consumer opening `store/<handle>.csv` cannot know its delimiter, and a handle could flip formats when a comma-bearing caption first appears. Python's `csv` module already quotes commas/newlines/quotes correctly. Recommend: **always proper-CSV with quoting, drop the TSV fork** (or commit to TSV always) — a conditional, per-file format is a footgun for every downstream reader. Confidence: HIGH.

**Suggestion:** Specify dedupe key durability — shortcode is the dedupe key; confirm via probe (T1.2) that shortcode/`code` is always present on clips items and never reused. Confidence: MED.

**FYI:** Consider a schema/version marker column or a header contract in the CSV so a later column addition (mirroring yt-media-kit) doesn't silently misalign old rows.

## API & Contract Reviewer
*"Three tools will build on this primitive; its shape is a contract, not an implementation detail."*

**Suggestion:** T1.7's synchronous window entry point is the contract `list_reels` and the batch runner both consume, yet its **return shape is under-specified**: it should explicitly return `(reels, cursor, partial_flag, stop_reason)` so the later batch runner can decide whether to sleep (on throttle) vs stop (on end-of-feed). Nailing `stop_reason` now (end-of-feed vs throttled vs page-cap-hit) prevents the batch runner from re-deriving it later and mis-handling cooldown. Confidence: HIGH.

**Suggestion:** `video_versions[0].url` assumes index 0 is present and is the desired rendition. Add to T1.2 probe checklist: confirm `video_versions` ordering/quality and presence on clips. Confidence: MED.

## Test Coverage Auditor
*"If it isn't asserted, it isn't done."*

**Suggestion — AC1..AC6 are referenced but never enumerated.** Tasks are tagged (AC1, AC2 … AC6) and T1.9 says "confirm AC1-AC6," but the plan contains no acceptance-criteria list. A tester cannot verify "all ACs" against an undefined set. Add the enumerated AC list before build; each AC should map to an observable check. Confidence: HIGH.

**Suggestion — build reproducible tests from probe fixtures.** Capture T1.2's raw JSON responses to disk as fixtures. Then normalization (T1.5), dedupe (T1.6), and cursor parsing can be **unit-tested deterministically against saved JSON** without live IG hits. Without this, every dev iteration burns rate-limit budget and tests are non-reproducible. This also de-risks B1/B4 by letting you assert parsing against a real captured throttle/error body. Confidence: HIGH.

**Suggestion:** The 401/throttle-partial path is described as "inject a 401 mid-run." Make that an actual injected-mock test (not only the live run), so the invariant is covered even when IG happens not to throttle during the pilot. Confidence: HIGH.

## Performance & Scalability Critic
*"The bottleneck is the metered endpoint; every needless call is a self-inflicted cooldown."*

**Suggestion:** Dev iteration against a live handle (T1.4–T1.9) plus the probe (T1.2) all draw from the same ~48-item/~6.6-min budget on the developer's IP. Without the fixture strategy (above), building + testing T1.5–T1.7 will repeatedly trip cooldown and *slow the whole build*. Add an explicit dev-budget note: probe once, cache JSON, run at most the single live pilot + single resume for acceptance. Confidence: MED.

**FYI:** CDN (`fbcdn.net`) is unmetered — if "resolvable video_url" verification needs a network touch, a HEAD to the CDN is free; do not verify resolvability against the metered `instagram.com` host.

## Tech Debt Sentinel
*"Deferred is fine; undefined-and-deferred is debt."*

**FYI:** Single-writer assumption on `store/<handle>.*` is implicit. T1.7 says "list_reels and the batch runner both call this." Batch is out of scope, but record now that the store assumes **one writer per handle at a time** (a per-handle lock/advisory) so the later batch runner doesn't corrupt CSV/state on concurrent calls. Cheap to note now, expensive to retrofit. Confidence: MED.

**FYI:** "resolvable video_url" in the success metric is undefined — presence-of-URL vs actually-fetchable. Ties to Performance FYI; pin the definition so acceptance isn't subjective.

## Naming & Clarity Guardian
*"Ambiguity in the plan becomes bugs in the code."*

**Suggestion:** `store/<handle>.csv` with possible TSV content is an extension/content mismatch (see Data Integrity). Keep extension truthful to format.

**Suggestion:** Clarify `high_water_id` vs `deep cursor` (next_max_id) naming in state so the two traversal roles (B4) are self-documenting — e.g., `newest_seen_id` vs `deep_max_id`.

---

## Review Summary

| Reviewer | Verdict | Blocking | Suggestions | FYI | Confidence |
|---|---|---|---|---|---|
| Error Handling & Resilience | REQUEST CHANGES | 2 | 1 | 0 | HIGH |
| Security & Trust | REQUEST CHANGES | 1 | 1 | 1 | MED |
| Domain Logic | REQUEST CHANGES | 1 | 1 | 0 | HIGH |
| Data Integrity & Migration | REQUEST CHANGES | 0 (corrob. B2) | 2 | 1 | HIGH |
| API & Contract | APPROVE w/ suggestions | 0 | 2 | 0 | HIGH |
| Test Coverage Auditor | REQUEST CHANGES | 0 | 3 | 0 | HIGH |
| Performance & Scalability | APPROVE w/ suggestions | 0 | 1 | 1 | MED |
| Tech Debt Sentinel | APPROVE w/ suggestions | 0 | 0 | 2 | MED |
| Naming & Clarity | APPROVE w/ suggestions | 0 | 2 | 0 | HIGH |

**Overall Recommendation:** REQUEST CHANGES

**Rationale:** This is a strong, well-sequenced plan — the probe-first gate (T1.2), the explicit politeness invariants, the anonymous-only code-level guard, and the never-destructive store posture all show the load-bearing constraints were taken seriously. It is close to build-ready. But four design questions must be resolved *before* code, because each one, if guessed wrong, silently breaks a load-bearing invariant and would be expensive to retrofit into the foundational primitive: (B1) throttle detection is scoped to literal 401 when IG's real block family is wider — the biggest risk to "never poll during cooldown"; (B2) partial-write atomicity/cursor-ordering is unspecified and can permanently skip reels, violating "never destructively capped"; (B3) "no cookie jar" over-broadly interpreted could break the fetch or under-define the anonymous invariant; (B4) the new-posts-vs-deep-paging traversal is conflated, leaving the core "second call adds only new rows" behavior ambiguous. Resolve these four (mostly by tightening T1.2's probe checklist and pinning the state/traversal + write-ordering semantics), enumerate AC1–AC6, and adopt the probe-fixture testing strategy, and the plan is ready to build.

**Blocking Items:**
1. **B1 (Error Handling/Security):** Model the stop-signal as the full throttle/block family (401/429/403/302-to-login/challenge JSON), not just literal 401. T1.2 must enumerate real codes; T1.3/T1.5 must stop-and-partial on all of them.
2. **B2 (Data Integrity/Resilience):** Define partial-write atomicity and cursor-advance ordering — persist rows first, advance cursor/high-water only for persisted items, atomic temp-file+replace for state.yaml.
3. **B3 (Security):** Precisely define "anonymous" (no sessionid/ds_user_id/auth params; benign anonymous cookies permitted) and have T1.2 confirm whether anonymous cookies are needed — do not code "no cookie jar" as-written.
4. **B4 (Domain Logic):** Disambiguate new-posts (top-scan to high_water) vs deep-paging (resume next_max_id toward scan_depth); define which T1 implements and pin the meaning of "second call adds only new rows."

**Top Suggestions:**
1. Enumerate AC1–AC6 explicitly and map each to an observable check (T1.9 depends on it).
2. Capture probe JSON as fixtures; unit-test normalization/dedupe/cursor deterministically and mock-inject the throttle path (don't rely on live throttling).
3. Drop the conditional TSV fork — always proper-CSV with quoting (or always TSV); a per-file data-dependent delimiter is a downstream footgun.
4. Specify T1.7's return contract: `(reels, cursor, partial_flag, stop_reason)` with stop_reason ∈ {end-of-feed, throttled, page-cap} so the later batch runner sleeps vs stops correctly.
5. Add `video_versions` ordering/presence and shortcode presence/uniqueness to the T1.2 probe checklist.

**Corroborated Findings:** B2 (partial-write atomicity/ordering) flagged by both Error Handling and Data Integrity — highest signal, act first.

**Accepted Debt:** Single-writer-per-handle store assumption (Tech Debt Sentinel FYI) — acceptable to defer since the batch runner is out of T1 scope, but record it as a documented assumption now; follow-up when the batch runner ticket lands.
