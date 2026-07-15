---
artifact_type: handoff
artifact_version: 2
producer_role: review
consumer_role: planner
plan_type: findings
review_type: plan
review_round: 3
slug: fetch-engine-store-foundation
scope_hint: fetch-engine-store-foundation (T1 plan, round 3)
canonical_name: review-findings-plan
review_status: APPROVE
owner: vd
overlays: []
status: draft
version: 3
created: 2026-07-14T17:54:00Z
updated: 2026-07-15T07:35:00Z
prior_versions:
  - review-findings-plan-v1.md
  - review-findings-plan-v2.md
---

# Review findings: fetch-engine-store-foundation (T1 plan, round 3)

Round-3 (final) multi-perspective panel review of the **REVISED PLAN** (planner-task v3) for T1 — Fetch engine + store foundation. Re-review, review-type=plan, round 3. Mandate: verify the single round-2 blocker (the `top_scan` stop condition specified as an ordered `<= high_water_id` comparison on non-orderable Instagram shortcodes) is *genuinely* resolved rather than name-checked, confirm no new blocker was introduced by the fix, and decide whether the plan is now sound enough to build. Standing rule for this round: only raise a blocking finding for a real must-fix-before-code defect — do not invent new scope. Panel weighted toward the two reviewers who raised/corroborated the round-2 blocker (Domain Logic, Reliability / Rate-Limit), plus the round-1 blocker owners for a fast continued-resolution confirmation and the contract/test reviewers who own the folded-in secondaries.

## Triage Decision
Scope: large (foundational; unchanged across rounds)
Partition: backend (Python service; flat-file store; metered network I/O)
Memory overrides: `always_include: Reliability / Rate-Limit Reviewer` (config.md) — applied; `always_exclude: Accessibility` — applied. Custom rule "any IG-hitting path → include Reliability, scope ≥ medium" — applied.

Selected Reviewers (weighted):
- Domain Logic Reviewer (backend) — raised the round-2 blocker; verify the stop-condition redefinition is correct and complete
- Reliability / Rate-Limit Reviewer (project custom, always-include) — corroborated the round-2 blocker on the budget axis; verify the short-circuit actually caps metered spend
- Error Handling & Resilience Inspector (common) — round-1 B1 owner; confirm the stop_signal contract survived the revision intact
- Data Integrity & Migration Reviewer (backend) — round-1 B2 owner; confirm durable-first ordering survived the anchor rename
- Security & Trust Reviewer (common) — anonymity invariant is load-bearing; confirm the auth-cookie-keyed guard is unchanged
- API & Contract Reviewer (backend) — owns the primitive's return shape; confirm the terminal-reason vocabulary suggestion landed
- Test Coverage Auditor (common) — confirm the `pages_fetched == 1` anti-regression check is a real, assertable acceptance gate

Skipped: Performance/Scalability, Tech Debt Sentinel, Naming, Dependency & Coupling, all FE/infra reviewers (no new surface; round-2 suggestions in those areas unchanged in force and non-blocking).

---

## Domain Logic Reviewer
*"Does the code do what the business actually needs?"*

**Verdict:** LGTM (prior blocker genuinely resolved)

**Round-2 blocker — GENUINELY RESOLVED, not name-checked.** The revision does the real semantic work I asked for and does it in the right place. The `<=`-on-shortcode formulation is gone entirely. The stop condition is now a two-part contract with the correct primary/secondary split (T1.5):
- **PRIMARY = seen-set membership.** Walking newest-first, stop the moment a fetched shortcode is already in the persisted `seen` set. This is order-tolerant and does not depend on any comparison operator over opaque tokens — which is exactly the property the old spec lacked. It is also robust to the deleted-anchor case I flagged as an FYI last round (if the post that set the watermark is deleted, other seen shortcodes still halt the walk).
- **SECONDARY = numeric `high_water_media_id` watermark.** `media_id <= high_water_media_id` on the monotonic `pk`, explicitly typed numeric and explicitly NOT the shortcode. This is a legitimate ordered comparison because `pk` is a monotonic integer (the left half of `next_max_id`), and it correctly backstops a fresh/empty/rebuilt `seen` set.

The anchor was renamed `high_water_id → high_water_media_id` throughout (Success Metric, T1.5, T1.6, T1.7, Handoff Notes), which removes the ambiguity that let the bug in. The identity-vs-ordering split is stated crisply and repeatedly: dedupe/identity keys off `shortcode`, ordering keys off `media_id`. T1.2 was correctly extended to *confirm the two are distinct fields and that the feed is newest-first*, with a documented degradation path (membership remains authoritative; the ordered check becomes a per-page min-scan) if ordering turns out not to be strictly newest-first. That fallback is the mark of a genuine fix rather than a patch — the plan no longer depends on an unverified ordering assumption for correctness, only for an optimization. The dependency graph was updated so T1.2 now gates T1.5/T1.6 on these facts. This is a complete, invariant-grade resolution.

**Issues Found:**
- [FYI] A new non-clip post (carousel/image/story) landing at the top of the feed since the last run will, on an otherwise-caught-up `top_scan`, cost one or two extra pages: its shortcode is not in `seen` (the store holds clips only) and its `media_id` is `> high_water_media_id`, so neither stop fires on it — the walk pages down to the first known reel. This is correct, bounded (still `≤ 4` pages, still stops at the first known reel), and *proportional to genuinely-new content*, so it is not a regression of the load-bearing property. Worth a one-line note near the T1.5 acceptance check that "no new posts" in the `pages_fetched == 1` assertion means no new posts of *any* product_type, not merely no new reels — so the test fixture is set up correctly. Confidence: HIGH.

## Reliability / Rate-Limit Reviewer
*"Every needless metered call is a self-inflicted cooldown."*

**Verdict:** LGTM (prior corroboration resolved)

**The budget hazard I corroborated last round is closed.** The failure mode was: a stop condition that can never fire ⇒ every `top_scan` pages to the ~4-page cap ⇒ ~48 metered items burned per steady-state call ⇒ cooldown escalation, all invisible because dedupe keeps the rows correct. The revision kills it at the root: the primary stop is membership (fires on the *first* already-seen shortcode, which on a caught-up handle is the very first item of page 1), so a steady-state `top_scan` costs exactly one page. Crucially this is now *enforced by test, not just prose* — the `pages_fetched == 1` acceptance check is written into T1.5, T1.7, AND T1.9, and `pages_fetched` was added to the primitive's emitted output specifically so a reviewer can assert it. That converts my round-2 concern from "trust the spec" to "the regression trips a red test." The Risks section names "shortcode-ordering re-introduction" as a first-class risk with the naming + typing + test mitigations, which is the right way to keep a future edit from silently re-opening it. The rest of the politeness contract (first-stop-on-any-stop_signal, `≤ 4` pages/call, never sleep in sync, never poll during cooldown, fail-closed classifier) is unchanged and intact.

**Issues Found:**
- [FYI] The round-1/round-2 dev-budget note (probe once in T1.2, cache the JSON as fixtures, drive T1.4–T1.9 iteration off fixtures so repeated dev runs don't trip the developer-IP cooldown) is still not written into the plan text. It remains a non-blocking process suggestion — the short-circuit now keeps *steady-state* calls to ~1 page, but the build/iteration loop itself is where a developer is most likely to self-inflict a cooldown. Confidence: MED. Not a build blocker.

## Error Handling & Resilience Inspector
*"Happy path is easy. I review the other 47 paths."*

**Verdict:** LGTM (prior blocker still resolved)

The round-1 `stop_signal` resolution survived the round-2 revision untouched and, if anything, is now cleaner: the plan explicitly separates a **normal** `caught_up` end-of-walk from an **abnormal** `stop_signal`, in T1.3, T1.5, and T1.6, so the new membership/watermark stop is never miscategorised as a throttle (which would have been an easy way to reintroduce confusion). The fail-closed `stop(unknown)` default and the "metadata calls must not blindly follow a 302" note are intact. The emitted terminal reason is now a defined set — `caught_up` | stop_signal reason | `page_cap` | `end_of_feed` — which is exactly the disambiguation I wanted.

**Issues Found:**
- [FYI] Open Question #5 (can a single 200 carry *both* a partial page of real items *and* a challenge/stop?) is still correctly parked as an open question with the right answer pre-committed ("persist the real items before stopping — durable-first still applies"). T1.2 is tasked to observe whether this case is real. Leaving it as a probe-gated open question is the correct call for a plan — it does not block the build because durable-first already covers the behavior if it occurs. Confidence: MED.

## Data Integrity & Migration Reviewer
*"Data outlives code. Treat it accordingly."*

**Verdict:** LGTM (prior blocker still resolved)

The durable-first ordering (round-1 B2) is fully intact through the rename: T1.6/T1.7 still mandate (a) CSV rows fsync'd durable FIRST, (b) advance `high_water_media_id` / update `seen` / advance `deep_cursor` only over persisted items, (c) state.yaml via temp-file + `os.replace`, with the injected-failure acceptance check in both T1.6 and T1.9. The anchor rename to a numeric `high_water_media_id` did not disturb any of this. The plan also correctly ties the round-trip to the new anti-regression check — T1.6's done-clause now requires that "the anchor round-trips so a subsequent top_scan short-circuits (feeding T1.5's `pages_fetched == 1` check)," which links the persistence guarantee to the budget guarantee. Good.

**Issues Found:**
- [FYI] My round-2 CSV-append-atomicity suggestion (single buffered newline-terminated write + fsync; reader tolerates/discards one torn trailing line) is only partially reflected — T1.6 mandates fsync/close but does not yet spell out reader-side tolerance of a torn trailing line. This remains a MED-confidence SUGGESTION, not a blocker: the worst case stays "re-fetch + dedupe," and it can be pinned during T1.6 implementation. Confidence: MED. Not a build blocker.

## Security & Trust Reviewer
*"I assume every input is hostile until proven otherwise."*

**Verdict:** LGTM (prior blocker still resolved)

The auth-cookie-keyed ANONYMOUS definition (round-1 B3) is unchanged and correct: no `sessionid`/`ds_user_id`/auth params; benign anonymous cookies (`mid`/`csrftoken`/`ig_did`) permitted and possibly required; guard asserts absence of *auth* cookies/params, not absence of a cookie jar; the mandatory `x-ig-app-id: 936619743392459` presence folded into the same positive outgoing-request test (T1.9). The round-2 revision touched only the stop condition and introduced no auth, cookie, login, or account surface — the anonymous-only invariant is not implicated by this change. No credentials, tokens, or identity linkage anywhere.

**Issues Found:**
- [FYI] My round-2 defense-in-depth suggestion (make the cookie guard fail-closed via an allowlist of known benign cookies rather than a denylist of the two known auth names) is not adopted; the guard remains a denylist. Still a SUGGESTION, not a hole — there is no login code path anywhere for an unknown auth-bearing cookie to originate from. Confidence: MED. Not a build blocker.

## API & Contract Reviewer
*"Three tools build on this primitive; its shape is a contract."*

**Verdict:** LGTM

My round-2 top suggestion landed. The primitive's emitted terminal-reason vocabulary is now pinned (T1.5): the per-call output enumerates the normalized reels, the newest-seen `media_id` (candidate `high_water_media_id`) plus its shortcode, `deep_cursor`, `pages_fetched`, a partial flag, and a typed stop reason that is one of `caught_up` (normal), a stop_signal reason (abnormal), `page_cap`, or `end_of_feed`. That is precisely the `ok`-family vs stop_signal split the future batch runner needs to decide done-vs-resume-window-vs-resume-deeper, and the addition of `pages_fetched` to the contract makes the short-circuit externally observable. The return shape is now a stable contract for the three downstream tools.

**Issues Found:**
- None blocking. The contract is build-ready.

## Test Coverage Auditor
*"An untested change is an unverified assumption."*

**Verdict:** LGTM

The fix carries a real, assertable acceptance gate rather than a prose promise — this is the single most important thing for a bug that dedupe would otherwise hide. `pages_fetched == 1` for a caught-up `top_scan` is written into three places (T1.5 done-clause, T1.7 done-clause, T1.9 live-acceptance), and the primitive emits `pages_fetched` so the assertion has something to read. The complementary cases are also specified: N new posts ⇒ exactly those returned, stop at first known shortcode; injected stop_signal ⇒ clean partial with cursor/newest-id intact. Combined with the retained round-1 checks (injected-failure durable-first in T1.6/T1.9, classifier-mapping and anonymity-guard checks in T1.3), the four-plus resolutions each carry an observable check.

**Issues Found:**
- [SUGGESTION] Two HIGH-confidence round-2 test suggestions remain open and are worth doing during the build, though neither blocks starting it: (1) AC1–AC6 are still referenced by tag and by T1.9 ("confirm AC1–AC6") without an enumerated acceptance-criteria list — the individual per-task "Done when" clauses effectively define them, so a tester is not actually blocked, but a short explicit AC1–AC6 list would remove the last ambiguity; (2) drive the stop_signal and injected-failure paths as **mock-injected unit tests against captured probe fixtures**, not only live-run observation, so the invariants are verified even when IG does not happen to throttle/crash during the live pilot. Confidence: HIGH. Neither is a must-fix-before-code defect. Confidence: HIGH.

---

## Review Summary

| Reviewer | Verdict | Blocking | Suggestions | FYI | Confidence |
|---|---|---|---|---|---|
| Domain Logic | LGTM | 0 | 0 | 1 | HIGH |
| Reliability / Rate-Limit | LGTM | 0 | 0 | 1 | HIGH |
| Error Handling & Resilience | LGTM | 0 | 0 | 1 | MED |
| Data Integrity & Migration | LGTM | 0 | 0 | 1 | MED |
| Security & Trust | LGTM | 0 | 0 | 1 | MED |
| API & Contract | LGTM | 0 | 0 | 0 | HIGH |
| Test Coverage Auditor | LGTM | 0 | 1 | 0 | HIGH |

**Overall Recommendation:** APPROVE

**Rationale:** The single round-2 blocker is genuinely resolved, not name-checked. The `<=`-on-shortcode formulation is gone; the `top_scan` stop condition is now a correct primary/secondary contract — order-tolerant seen-set membership as the authoritative stop, backstopped by a monotonic numeric `high_water_media_id` watermark — with the anchor renamed and typed numeric throughout, the identity-vs-ordering roles cleanly separated, and T1.2 extended to verify the pk-vs-shortcode distinction and newest-first ordering (with a documented fallback that keeps membership authoritative if ordering is not strictly newest-first). Decisively, the fix is enforced by test: a `pages_fetched == 1` anti-regression check is wired into T1.5, T1.7, and T1.9, and `pages_fetched` is now part of the primitive's emitted contract, so the exact failure that dedupe would have hidden now trips a red test. Both reviewers who raised the blocker last round (Domain Logic on correctness, Reliability on budget) independently confirm resolution. All four round-1 blockers remain resolved and were not disturbed by the revision. No new blocking defect was introduced by the fix. The remaining items are non-blocking suggestions carried forward from round 2 (enumerate AC1–AC6 explicitly; add mock-injected fixture tests; CSV-append torn-line tolerance; allowlist cookie guard; probe-fixture dev-budget discipline) — all safe to address during the build. The plan is sound enough to build.

**Blocking Items:** None.

**Top Suggestions (non-blocking; fold in during build):**
1. Enumerate AC1–AC6 as an explicit list (the per-task "Done when" clauses already define them; this only removes residual ambiguity for the tester).
2. Add mock-injected unit tests against captured probe fixtures for the stop_signal and injected-failure paths, so those invariants are verified independent of whether IG throttles during the live pilot.
3. Pin CSV-append durability: single buffered newline-terminated write + fsync, reader tolerates/discards one torn trailing line.
4. Make the cookie guard fail-closed (allowlist benign anonymous cookies) rather than denylist the two known auth names — defense-in-depth.
5. Adopt the probe-fixture dev-budget discipline (probe once in T1.2, cache JSON, iterate off fixtures) to avoid self-inflicted developer-IP cooldowns during T1.4–T1.9.
6. Add a one-line note that the `pages_fetched == 1` "no new posts" fixture must have no new posts of *any* product_type (a new non-clip at feed-top legitimately costs an extra page).

**Corroborated Findings:** None outstanding. The round-2 corroborated blocker (Domain Logic + Reliability) is confirmed resolved by both originating reviewers.

**Prior-round disposition:** Round-1 blockers B1–B4 — all remain GENUINELY resolved and were not regressed by the round-2 revision. Round-2 blocker (top_scan stop condition on non-orderable shortcodes) — GENUINELY resolved: seen-set membership primary stop + numeric `high_water_media_id` watermark secondary + a `pages_fetched == 1` acceptance check across T1.5/T1.7/T1.9, with T1.2 extended to confirm the underlying pk/ordering facts. This is the terminal review round for the plan: no blocking findings remain, prior findings are resolved, and the plan is approved to proceed to build.
