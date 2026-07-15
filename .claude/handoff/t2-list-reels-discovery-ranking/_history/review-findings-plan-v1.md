---
artifact_type: handoff
artifact_version: 2
producer_role: review
consumer_role: planner
plan_type: findings
review_type: plan
review_round: 1
slug: t2-list-reels-discovery-ranking
scope_hint: t2-list-reels-discovery-ranking (T2 plan, round 1)
canonical_name: review-findings-plan
review_status: REQUEST_CHANGES
owner: vd
overlays: []
status: draft
version: 1
created: 2026-07-15T14:30:05Z
updated: 2026-07-15T14:30:05Z
prior_versions: []
---

# Review findings: T2 — list_reels: anonymous discovery + ranking (call-driven fill)

Plan review, iteration 1. No diff — the artifact under review is the T2 task-breakdown plan. Reviewers assess approach correctness, completeness, risk coverage, and adherence to the project's hard invariants.

## Triage Decision

Scope: large (multi-phase orchestration plan, cross-cutting on the T1 foundation)
Partition: backend (Python fetch/store/ranking; no FE/infra signals)
Memory overrides: none (no `.claude/skill-memory/multi-perspective-review/` present)

Selected Reviewers:
- Domain Logic Reviewer (backend) — the top-check/deepen/coverage state machine is domain logic against IG feed ordering
- Ripple Effect Analyst (common) — plan rides frozen T1 contracts; must confirm no assumption depends on T1 behavior that T1 doesn't guarantee
- Error Handling & Resilience Inspector (common) — stop_signal partial path, cooldown/never-sleep discipline
- API & Contract Reviewer (backend) — the `list_reels` signature + return envelope is a published MCP surface
- Test Coverage Auditor (common) — acceptance criteria completeness, esp. the pinned-reel case
- Backward Compatibility Reviewer (common) — additive-only state, T1 contract non-modification
- Tech Debt Sentinel (baseline) — deferred/unclosed items (gaps handed to batch runner)
- Naming & Clarity Guardian (baseline) — "complete-to-scan_depth" vs contiguity ambiguity

Skipped: Security & Trust (anonymous-only already invariant-locked, no new request path), Performance/Scalability (metered budget is the governor, covered), Concurrency (single-dev, T1 atomic write), Observability, Data Integrity/Migration (additive YAML field only), Documentation (plan-level), Dependency & Coupling.

---

## Domain Logic Reviewer — REQUEST CHANGES (Confidence: HIGH)

**BLOCKING — B1: Pinned-reel feed ordering defeats the top-check short-circuit; the plan does not acknowledge it.**
T2.4 stops top_scan on "seen-set membership / high_water_media_id watermark." T1's build already surfaced that the IG feed is **not strictly newest-first** for accounts that pin reels: a pinned, already-seen reel can occupy position 0. With a first-seen or `media_id <= high_water_media_id` short-circuit, the scan terminates **at the pin** and never reaches genuinely-newer un-seen reels sitting below it — they are silently dropped, and since the watermark then sits at/above them, later calls never rediscover them either. This is a correctness hole in the tool's primary job (surface new reels), and the plan is silent on it end to end. (Corroborated by the T1 build handoff, which explicitly flagged pinned-reel non-newest-first ordering as an accepted-debt followup — T2 must carry it forward, not drop it.)
Recommended fix: Make the top-check *skip-not-stop* on seen items across at least the full first page (IG pins up to ~3): collect every un-seen clip on the page, treat pins as skips, and use the watermark only to bound *paging* (stop paging when a page yields no new items), not to hard-stop mid-page at the first seen item. Correctness must rest on T1's per-shortcode dedupe, not on positional ordering. Add an explicit plan step/AC for it.

**BLOCKING — B2: B1 collides with the "must NOT modify T1's frozen fetch loop / stop conditions" constraint — unresolved contradiction.**
If T1's `fetch_window(mode=top_scan)` already short-circuits on the first seen item internally, then B1 cannot be fixed in T2 without touching T1's frozen stop condition — which the plan lists as out of scope. The plan cannot simultaneously honor "don't skip newer reels behind a pin" and "don't modify T1's top_scan stop condition" until it's known which behavior T1 actually shipped. This is an unacknowledged dependency that can hard-block the build.
Recommended fix: Add a micro-gate (extend T2.0, or a new T2.0b verify-by-pilot against a *known pinned account*) that observes T1 top_scan's real stop behavior in the presence of a top pin. Branch the plan: (a) if T1 already scans the full page and only skips seen items → T2 just relies on it and adds the AC; (b) if T1 hard-stops at first seen → the plan must explicitly declare that a scoped T1 change is required (promoting it out of "out of scope") or document the T2-side workaround, rather than leaving the contradiction latent.

**BLOCKING — B3: `high_water_media_id` bump must be numeric-max, not positional-top — pins make "top item" the wrong anchor.**
T2.4 says "bumping high_water_media_id" without specifying from what. A pinned older reel at position 0 has a *lower* numeric media_id than the newer reels below it. Bumping to the top item's id would move the watermark backward (or leave newer items un-anchored). T1's handoff explicitly requires the numeric `high_water_media_id` (never compare shortcodes).
Recommended fix: Specify that top-check advances `high_water_media_id` to the **max numeric media_id among newly-merged items**, never to the positionally-top item. Add this to the T2.4 acceptance.

**SUGGESTION:** T2.5's gap-detection predicate ("batch oldest still newer than prior segment newest") also assumes monotonic ordering — re-state it in terms of numeric media_id comparisons and confirm a top pin (older id at top) cannot spuriously open a phantom segment.

## Ripple Effect Analyst — REQUEST CHANGES (Confidence: HIGH)

**BLOCKING — corroborates B2:** The plan asserts reuse of `fetch_window` "unchanged" for top_scan while simultaneously depending on it to surface newer-than-pin reels. Every downstream step (T2.5 coverage bump, T2.6 deepen cursor, T2.2 serve gate) inherits whatever ordering assumption T2.4 makes. If B1/B2 resolve toward a T1 behavior gap, the ripple reaches coverage segments and the watermark. Pin down T1's actual top_scan semantics *before* T2.3+ are wired, exactly as the step-ordering note demands for T2.3.

**SUGGESTION:** Confirm the `x-ig-app-id` header is owned entirely by T1's `AnonymousClient` and that no T2 step constructs a request outside it. The plan's "adds no new request path that bypasses AnonymousClient" covers this — keep it as an explicit AC in T2.9 so the header invariant can't regress via a stray helper.

## Naming & Clarity Guardian — REQUEST CHANGES (Confidence: HIGH)

**BLOCKING — B4: "complete-to-scan_depth" conflates raw depth with coverage contiguity.**
T2.2 defines `pool_depth = count of stored clips` and gates serve-from-store on `pool_depth >= scan_depth`. But T2.5 allows an unconverged gap (a burst opens a 2nd segment). A pool can reach count >= 90 while a hole remains in the middle. Then `fresh_fetch=false` skips **all** network forever and the envelope reports "complete," yet coverage is non-contiguous — reels in the gap are permanently invisible to `list_reels` (only the out-of-scope batch runner would close it). The envelope's `complete` flag and the serve gate are both ambiguous about which notion of "complete" they mean.
Recommended fix: Separate the two concepts explicitly: `pool_depth` (effort/count) vs `coverage_contiguous` (single joined segment spanning to scan_depth). Decide and document whether the serve-from-store gate keys on count alone (accepting frozen gaps, explicitly noted) or on contiguity; surface `complete` in the envelope as contiguity, not count. Resolve the related open question in the same stroke.

## API & Contract Reviewer — APPROVE with nits (Confidence: MED)

The envelope (ranked records, `partial`, `note`, `pool_depth`, `pages_fetched`, coverage summary) is well-scoped and validation (unknown `sort_by` rejected, non-negative args, `count > pool` returns pool) is correct. Nits, non-blocking:
- Resolve the open questions that shape the *published* contract before T2.1 freezes it: `count`/`scan_depth` config-defaulting and whether coverage detail is exposed. These are contract-visible and shouldn't be discovered mid-build.
- Decide the `fresh_fetch=true` semantics precisely: does it bypass only the serve gate (still <=4-page budget) or also force a deepen? State it in the envelope contract.

## Error Handling & Resilience Inspector — APPROVE (Confidence: HIGH)

The never-sleep / stop-on-first-stop_signal / return-partial discipline (T2.3, T2.7) is faithful to the load-bearing politeness invariant: `sleep=None`, phase-2 not started after a phase-1 stop, no poll during cooldown, no exception to the client, cursor not advanced past unpersisted rows (T1 durable-first). Test asserts sleep is never invoked. This is the strongest part of the plan. One FYI: ensure the budget governor's "reserve >=1 page for deepen" interacts cleanly with a stop_signal during top-check — a stop must abort the whole call, not "fall through" to spend the reserved deepen page. State it in T2.3's AC.

## Test Coverage Auditor — REQUEST CHANGES (Confidence: HIGH)

**BLOCKING — corroborates B1:** There is no test for the pinned-reel case anywhere in the Test Strategy or T2.10. Given T1 explicitly surfaced this behavior, its absence is a coverage gap on the tool's core function.
Recommended fix: Add a fixture-based test — feed with a pinned (older, already-seen) reel at position 0 plus >=1 newer un-seen clip below it → top-check must merge the newer clip(s) and must **not** short-circuit at the pin — and an accompanying assertion that this still costs `pages_fetched == 1` on an otherwise caught-up handle (so the pin-fix doesn't defeat the caught-up anti-regression). Also add a coverage test that a count->=-scan_depth pool with a recorded gap reports non-contiguous coverage (ties to B4).

## Backward Compatibility Reviewer — APPROVE (Confidence: MED)

Additive-only `coverage_segments` in state.yaml with T1 fields untouched is the right shape; durable-first ordering and the numeric watermark are respected in intent. Caveat rides on B2: "reuse T1 unchanged" is only truly compatible if T1's top_scan already tolerates pins — otherwise the plan will be pressured into a T1 change it currently forbids. Gate on the T2.0b probe.

## Tech Debt Sentinel — APPROVE with note (Confidence: HIGH)

Deferring gap-convergence to the batch runner is a legitimate, explicitly-scoped debt (not a silent one) — acceptable. One note: if B4 resolves toward "serve-from-store freezes internal gaps," that debt becomes user-visible (a permanently incomplete pool served as complete). Record it in the coverage summary/note so it's observable, not hidden. (Note also: T1 already logged pinned-reel under-collection as accepted-debt "safe as shipped" — B1 is the point at which that debt must be paid, since T2 is the discovery surface that depends on it.)

---

## Review Summary

| Reviewer | Verdict | Blocking | Suggestions | Confidence |
|---|---|---|---|---|
| Domain Logic | REQUEST CHANGES | 3 (B1,B2,B3) | 1 | HIGH |
| Ripple Effect | REQUEST CHANGES | 1 (corrob. B2) | 1 | HIGH |
| Naming & Clarity | REQUEST CHANGES | 1 (B4) | 0 | HIGH |
| API & Contract | APPROVE (nits) | 0 | 2 | MED |
| Error Handling | APPROVE | 0 | 1 | HIGH |
| Test Coverage | REQUEST CHANGES | 1 (corrob. B1) | 0 | HIGH |
| Backward Compat | APPROVE | 0 | 1 | MED |
| Tech Debt | APPROVE (note) | 0 | 1 | HIGH |

**Overall Recommendation: REQUEST CHANGES**

**Rationale:** The plan is strong on the load-bearing politeness/never-block discipline (T2.3/T2.7 are faithful to every rate-limit invariant) and on additive, T1-respecting store changes. But its top-check phase (T2.4) is built on a strictly-newest-first ordering assumption that T1's own build already disproved for pinned accounts, and the plan never acknowledges the hazard. This is a real correctness hole in the tool's primary job, and it directly collides with the "do not modify T1's frozen stop conditions" constraint — a contradiction that must be resolved with a verify-by-pilot probe before T2.3+ are wired, not discovered mid-build. A secondary ambiguity (count-based `pool_depth` vs coverage contiguity) lets a gapped pool be served as "complete." None of these are fatal to the design; they are additive plan changes plus one micro-gate. Address B1–B4 and the plan is APPROVE-ready.

**Blocking Items:**
1. **B1** (Domain Logic; corrob. Test Coverage) — Pinned already-seen reel at feed top short-circuits top-check above genuinely-newer reels; plan silent. Fix: skip-not-stop on seen items across the full first page; watermark bounds paging only; correctness rests on per-shortcode dedupe. Add step + AC + pinned-account test.
2. **B2** (Domain Logic; corrob. Ripple) — B1 collides with "don't modify T1 top_scan." Fix: add a T2.0b verify-by-pilot against a known pinned account to observe T1's real stop behavior, then branch the plan (rely-on-T1 vs scoped-T1-change vs T2-workaround); resolve before T2.3.
3. **B3** (Domain Logic) — `high_water_media_id` must bump to numeric-max of merged items, never the positionally-top (possibly pinned/older) item. Add to T2.4 AC.
4. **B4** (Naming & Clarity) — Separate `pool_depth` (count) from `coverage_contiguous`; make the serve-from-store gate and the envelope `complete` flag explicit about which they mean, so a gapped >=90 pool isn't served as complete.

**Top Suggestions:**
1. Keep the `x-ig-app-id` header invariant as an explicit T2.9 AC (no request path outside `AnonymousClient`).
2. Freeze contract-visible open questions (count/scan_depth defaulting, fresh_fetch semantics, coverage exposure) before T2.1 publishes the envelope.
3. State in T2.3 that a stop_signal during top-check aborts the whole call — the reserved deepen page is not spent afterward.
4. Re-state T2.5's gap predicate in numeric-media_id terms and confirm a top pin cannot open a phantom segment.

**Corroborated Findings (highest signal):** B1 (Domain Logic + Test Coverage), B2 (Domain Logic + Ripple Effect) — the pinned-reel ordering hazard and its collision with the T1-freeze constraint are the act-first items.

**Accepted Debt:** Gap-convergence deferred to the batch runner is acceptable, scoped debt — but if B4 resolves toward frozen gaps, surface it in the coverage note so it is observable, not hidden. T1's pre-existing pinned-reel accepted-debt is now due via B1.
