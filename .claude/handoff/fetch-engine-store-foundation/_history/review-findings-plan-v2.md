---
artifact_type: handoff
artifact_version: 2
producer_role: review
consumer_role: planner
plan_type: findings
review_type: plan
review_round: 2
slug: fetch-engine-store-foundation
scope_hint: fetch-engine-store-foundation (T1 plan, round 2)
canonical_name: review-findings-plan
review_status: REQUEST_CHANGES
owner: vd
overlays: []
status: draft
version: 2
created: 2026-07-14T17:54:00Z
updated: 2026-07-14T18:12:00Z
prior_versions:
  - review-findings-plan-v1.md
---

# Review findings: fetch-engine-store-foundation (T1 plan, round 2)

Round-2 multi-perspective panel review of the **REVISED PLAN** (planner-task v2) for T1 — Fetch engine + store foundation. This is a re-review (review-type=plan, round 2). The mandate: verify each of the four round-1 blocking findings is *genuinely* resolved rather than name-checked, assess the one scoping decision the revision made against finding 4, and surface any NEW blocking issue the revision introduced. Panel weighted toward the four reviewers who blocked last round, plus the config-mandated Reliability / Rate-Limit reviewer.

## Triage Decision
Scope: large (foundational; unchanged from round 1)
Partition: backend (Python service; flat-file store; network I/O)
Memory overrides: `always_include: Reliability / Rate-Limit Reviewer` (config.md) — applied; `always_exclude: Accessibility` — applied.

Selected Reviewers (weighted):
- Error Handling & Resilience Inspector (common) — blocked B1 last round; verify stop_signal classifier
- Data Integrity & Migration Reviewer (backend) — corroborated B2; verify durable-first + atomic state
- Security & Trust Reviewer (common) — blocked B3; verify auth-cookie-keyed anonymity
- Domain Logic Reviewer (backend) — blocked B4; verify traversal split + assess scoping
- Reliability / Rate-Limit Reviewer (project custom) — politeness is load-bearing; the new stop-condition issue is a rate-budget hazard
- API & Contract Reviewer (backend, light) — new-issue sweep on the primitive's return shape
- Test Coverage Auditor (common, light) — confirm the four fixes carry acceptance checks

Skipped: Performance/Scalability, Tech Debt Sentinel, Naming (round-1 suggestions unchanged in force; no new surface), all FE/infra reviewers (no such surface).

---

## Error Handling & Resilience Inspector
*"Happy path is easy. I review the other 47 paths."*

**Verdict:** Suggestions (prior blocker resolved)

**B1 — GENUINELY RESOLVED.** The revision does the real work, not a rename. T1.2 is now charged to *enumerate the concrete throttle/block/challenge responses live* (status codes, redirect targets, 200-challenge JSON shapes) and that enumeration explicitly gates T1.3. T1.3 introduces a `stop_signal` classifier returning `ok | stop(reason) | error` over the whole family, with typed reasons (`rate_limited`, `login_redirect`, `challenge`, `forbidden`). T1.5 stops on any stop_signal, not literal 401. The decisive addition is the **fail-closed default** (Risks: classifier defaults to `stop(unknown)` for any non-`ok` it can't positively classify as feed data) — that is what makes it resilient to an unseen future throttle shape, and it directly answers the "loop keeps paging and escalates cooldown" hazard I raised. The "metadata calls must NOT blindly follow a 302" note closes the redirect-follow loophole. This is a correct, invariant-grade resolution.

**Issues Found:**
- [SUGGESTION] The classifier's return type is a mutually-exclusive trichotomy (`ok | stop | error`), but Open Question #4 correctly asks whether a single 200 can be *both* a partial page of real feed items *and* a challenge/stop. If yes, `ok XOR stop` can't represent it, and the plan's own answer ("persist the real items before stopping") requires the classifier to surface items AND a stop reason simultaneously. Pin the contract now: the classify result for a mixed response should carry `(items, stop_reason)` so durable-first can persist then stop. Cheap to specify in the plan; expensive to bolt onto the enum after T1.3 is built. Confidence: HIGH.
- [FYI] First-page stop (zero items fetched) — round-1 suggestion — is now implicitly covered by "partial with the cursor/newest-id intact," but T1.9 should still assert the zero-row partial leaves prior state *untouched* (not a zeroed cursor).

## Data Integrity & Migration Reviewer
*"Data outlives code. Treat it accordingly."*

**Verdict:** Suggestions (prior blocker resolved)

**B2 — GENUINELY RESOLVED.** The three-step ordering is now explicit and, crucially, testable: (a) flush/fsync CSV rows durable FIRST, (b) advance high_water_id/deep_cursor *only over persisted items*, (c) state.yaml via temp-file + `os.replace`. The injected-failure acceptance check appears in both T1.6 and T1.9 ("failure after CSV flush but before state write leaves the cursor NOT advanced… rows re-appear and dedupe on retry"). That is exactly the "never skip a reel forever" guarantee I asked for, wired to an observable test. The two state fields are role-disambiguated (high_water_id = caught-up-to-new; deep_cursor = how-far-back). Fully addressed.

**Issues Found:**
- [SUGGESTION] Durable-first protects against *cursor-skip*, but the CSV *append itself* is not atomic. If the process dies mid-append, the manifest can carry a torn trailing line. State.yaml gets temp+rename; the CSV does not. Specify: write each window's rows as one buffered, newline-terminated `write()` then fsync, and have the reader tolerate/discard a single malformed trailing line on load. Worst case stays "re-fetch + dedupe," but without this the torn row can break CSV parsing on the next call. Confidence: MED.
- [SUGGESTION] The skip-seen dedupe source is unspecified — is the seen-set read from the CSV on each call, or held in state? It matters for the torn-append case above and for cold-start after a crash. Pin it (recommend: derive seen-set from the CSV, the source of truth). Confidence: MED.
- [FYI] Round-1's "drop the conditional TSV fork" suggestion is still only partially taken (T1.6 keeps "TSV fallback if captions carry commas / proper quoting"). Proper CSV quoting alone handles commas; the conditional per-file delimiter remains a downstream footgun. Non-blocking, but the plan could commit to always-quoted-CSV and delete the fork.

## Security & Trust Reviewer
*"I assume every input is hostile until proven otherwise."*

**Verdict:** Suggestions (prior blocker resolved)

**B3 — GENUINELY RESOLVED.** The revision corrects the conceptual error cleanly: ANONYMOUS is redefined as *no `sessionid`/`ds_user_id`/auth cookies and no login/auth params*, with benign anonymous cookies (`mid`/`csrftoken`/`ig_did`) explicitly permitted and possibly required. The guard now asserts *absence of auth cookies/params*, not absence of a cookie jar, and T1.2 is tasked to settle empirically whether anonymous cookies are needed before T1.3 codes the policy. The positive test (T1.9: inspect outgoing request, assert no auth cookie/param, assert `x-ig-app-id` present) locks the invariant against silent regression. This is the right shape.

**Issues Found:**
- [SUGGESTION] The guard is framed as a **denylist** ("assert absence of sessionid/ds_user_id/auth cookies"). For consistency with the fail-closed philosophy the plan adopted for the stop_signal classifier, make the cookie guard fail-closed too: **allowlist** the known benign cookies (`mid`/`csrftoken`/`ig_did` + whatever T1.2 observes) and reject anything else, rather than denylisting the two known auth names. A denylist silently permits an unknown future auth-bearing cookie. Since there is no login code path anywhere, this is defense-in-depth, not a live hole — hence suggestion, not blocking. Confidence: MED.
- [FYI] Good that `x-ig-app-id: 936619743392459` presence is now folded into the same positive test — a missing-header regression would masquerade as a random block.

## Domain Logic Reviewer
*"Does the code do what the business actually needs?"*

**Verdict:** Blocking Issues (prior blocker resolved; NEW blocker introduced)

**B4 — core conflation RESOLVED; scoping decision SOUND.** The two traversal intents are now explicit modes on the primitive (`top_scan` vs `deep_resume`) with disambiguated state anchors (high_water_id vs deep_cursor advancing independently). On the scope disagreement the planner flagged: **I concur the scoping is sound.** The Success Metric's "second call adds only newer rows" is precisely `top_scan`; shipping only the top_scan *caller* while building the primitive to *support* both modes is a clean seam — the deferred deep-backfill caller reuses the identical pagination with a different start cursor and stop condition. Deferring it keeps T1 at "foundation" and avoids widening the metered-endpoint cooldown surface for a path the metric never exercises. The primitive-supports-both / one-caller-ships split is the right call, not scope-dodging.

**Issues Found:**
- [BLOCKING] **NEW — the concrete `top_scan` stop condition is specified as an ordered comparison on shortcodes, which is ill-defined.** T1.5 says top_scan stops "as soon as an already-seen shortcode (`<= high_water_id`) is reached," and T1.6 stores high_water_id as "newest shortcode." Instagram shortcodes are base64 media-id encodings that are **not lexically orderable** — `<=` on a shortcode string is meaningless. Only the numeric `media_id` (and the `next_max_id` cursor built from it) is monotonic. If an implementer takes "`<= high_water_id`" literally, top_scan never recognizes "caught up," pages to the ~4-page cap on *every* call, and burns the metered ~48-item/6.6-min budget each run — dedupe hides the waste while it silently invites cooldown escalation (a direct hit to the load-bearing politeness invariant). Fix in the plan before build: define the top_scan stop condition as **membership in the seen-set** (stop when a fetched shortcode is already persisted) OR as an ordered comparison on the **numeric media_id / next_max_id**, and store the anchor accordingly (e.g. `high_water_media_id`), not as an ordered "`<=`" on shortcode. This is a one-paragraph spec fix, but it pins the exact semantics of the only mode T1 ships. Confidence: HIGH.
- [FYI] Deleted-anchor robustness: if the post that set high_water_id is later deleted by the creator, a membership-based stop still works (other seen shortcodes halt it); an anchor-only stop would page to the cap. Another reason to prefer membership as the primary stop and treat the anchor as an optimization. Confidence: MED.

## Reliability / Rate-Limit Reviewer
*"Every needless metered call is a self-inflicted cooldown."*

**Verdict:** Suggestions (with one corroboration)

The politeness contract is materially stronger than round 1: first-stop-on-any-signal (not just 401), `<=4` pages/call, never sleep in sync, never poll during cooldown, fail-closed classifier. The Handoff Notes correctly elevate these to invariants a reviewer treats any violation of as a blocker. Good.

**Issues Found:**
- [BLOCKING — corroborates Domain Logic] I independently reach the same conclusion as the Domain reviewer's new blocker, from the rate-budget angle: a top_scan stop condition that can't fire (because "`<=`" on shortcodes never matches) means every top_scan call pages to the cap regardless of whether anything is new. That is the single most expensive politeness regression possible in this design and it is invisible to the Success Metric (dedupe absorbs the rows). Must pin the stop condition before build. Confidence: HIGH.
- [FYI] Dev-budget note from round 1 (probe once, cache JSON as fixtures, run only the single live pilot + resume) is not yet reflected in the plan text. Non-blocking, but adopting the probe-fixture strategy would keep T1.4–T1.9 iteration from repeatedly tripping the developer-IP cooldown.

## API & Contract Reviewer
*"Three tools build on this primitive; its shape is a contract."*

**Verdict:** Suggestions

The return contract is now largely nailed (round-1 suggestion taken): T1.5 emits normalized reels + newest-seen id + deep_cursor + partial flag + typed stop reason. That is what the later batch runner needs to decide sleep-vs-stop.

**Issues Found:**
- [SUGGESTION] `stop_reason` currently enumerates the *throttle* family (rate_limited/login_redirect/challenge/forbidden) but the batch runner also needs to distinguish **non-error terminal states**: `end_of_feed` (no more items) vs `page_cap_hit` (stopped at 4 pages, more may exist) vs `caught_up` (top_scan hit the seen-set). These are `ok`-family stops, not stop_signal stops, and they drive completely different batch behavior (done vs resume-next-window vs resume-deeper). Pin this terminal-reason vocabulary in T1.5's contract now. Confidence: HIGH.

## Test Coverage Auditor
*"If it isn't asserted, it isn't done."*

**Verdict:** Suggestions

Each of the four resolutions now carries an observable acceptance check (T1.9 enumerates all four round-1 checks; T1.6 has the injected-failure check; T1.3 has the classifier-mapping and anonymity-guard checks). Strong.

**Issues Found:**
- [SUGGESTION] Round-1's "AC1–AC6 are referenced but never enumerated" is still open — T1.9 says "confirm AC1–AC6" and tasks are tagged (AC1…AC6) but no acceptance-criteria list appears in v2. A tester still cannot verify "all ACs" against an undefined set. Enumerate them before build. Confidence: HIGH.
- [SUGGESTION] Make the stop_signal and injected-failure paths **mock-injected unit tests against captured probe fixtures**, not only live-run observations — otherwise the invariant is unverified whenever IG happens not to throttle/crash during the pilot. Confidence: HIGH.

---

## Review Summary

| Reviewer | Verdict | Blocking | Suggestions | FYI | Confidence |
|---|---|---|---|---|---|
| Error Handling & Resilience | Suggestions | 0 | 1 | 1 | HIGH |
| Data Integrity & Migration | Suggestions | 0 | 2 | 1 | MED |
| Security & Trust | Suggestions | 0 | 1 | 1 | MED |
| Domain Logic | Blocking | 1 | 0 | 1 | HIGH |
| Reliability / Rate-Limit | Suggestions | 1 (corrob.) | 0 | 1 | HIGH |
| API & Contract | Suggestions | 0 | 1 | 0 | HIGH |
| Test Coverage Auditor | Suggestions | 0 | 2 | 0 | HIGH |

**Overall Recommendation:** REQUEST CHANGES

**Rationale:** The revision genuinely resolves all four round-1 blockers — not cosmetically. B1 gains a real stop_signal classifier with a fail-closed `stop(unknown)` default and a probe that enumerates the live throttle family; B2 gains explicit durable-first ordering (CSV fsync before cursor advance, atomic state via temp+os.replace) with an injected-failure acceptance test; B3 correctly redefines ANONYMOUS as auth-cookie-keyed with a positive outgoing-request test; B4's mode conflation is resolved with disambiguated state, and the planner's decision to ship only the `top_scan` caller while building both modes into the primitive is sound and well-argued. However, the revision introduced **one new blocking issue**: the concrete `top_scan` stop condition is written as an ordered comparison (`<= high_water_id`) on Instagram *shortcodes*, which are not lexically orderable — taken literally it never fires, causing every top_scan call to page to the cap and silently burn the metered rate budget (a load-bearing-politeness regression invisible to the Success Metric because dedupe hides it). Two reviewers reach this independently. It is a one-paragraph spec fix but must be pinned before build, since it defines the semantics of the only mode T1 ships.

**Blocking Items:**
1. **(Domain Logic / Reliability, corroborated)** T1.5/T1.6: redefine the `top_scan` stop condition as **membership in the persisted seen-set** (or an ordered comparison on the numeric `media_id`/`next_max_id`), NOT `<=` on a shortcode string; store the anchor as a media_id, not a "newest shortcode." Without this the stop can never fire and every call pages to the cap, escalating cooldown.

**Top Suggestions:**
1. Pin the classifier result for a mixed 200 (real items + challenge) as `(items, stop_reason)` so durable-first can persist-then-stop — the current `ok XOR stop` trichotomy can't represent Open Question #4's case.
2. Extend the T1.5 terminal vocabulary with the `ok`-family stops the batch runner needs: `end_of_feed` vs `page_cap_hit` vs `caught_up`, distinct from the throttle stop_reasons.
3. Specify CSV-append durability (single newline-terminated buffered write + fsync; reader tolerates one torn trailing line) and pin the skip-seen source (derive from the CSV).
4. Make the cookie guard fail-closed: allowlist benign anonymous cookies rather than denylist the two known auth names.
5. Enumerate AC1–AC6 explicitly, and add mock-injected fixture tests for the stop_signal and injected-failure paths.

**Corroborated Findings:** The `top_scan` stop-condition blocker is raised independently by Domain Logic (correctness) and Reliability / Rate-Limit (budget) — highest signal, act first.

**Prior-round disposition:** All four round-1 blockers (B1–B4) verified genuinely resolved. The scoping decision the revision made on B4 (implement top_scan end-to-end, support-only deep_resume, defer the deep-backfill caller) is assessed SOUND. The single REQUEST CHANGES is driven entirely by the newly-introduced stop-condition precision defect, not by any unresolved prior finding.
