---
artifact_type: handoff
artifact_version: 2
producer_role: review
consumer_role: planner
plan_type: findings
review_type: plan
review_round: 2
slug: t2-list-reels-discovery-ranking
scope_hint: t2-list-reels-discovery-ranking (T2 plan, round 2)
canonical_name: review-findings-plan
review_status: APPROVE
owner: vd
overlays: []
status: draft
version: 2
created: 2026-07-15T14:30:05Z
updated: 2026-07-15T20:15:00Z
prior_versions:
  - _history/review-findings-plan-v1.md
---

# Review findings: T2 — list_reels: anonymous discovery + ranking (call-driven fill)

Plan review, iteration 2 (round 2). No diff — the artifact under review is the REVISED T2 task-breakdown plan (version 2). This round is scoped to the three round-1 blocking reviewers verifying that findings B1–B4 are genuinely resolved by the revision, and that the revision introduced no new blocking problem (with special scrutiny on the T2.4a budget-burn regression risk).

## Triage Decision

Scope: large (multi-phase orchestration plan; carries a scoped T1 carve-out)
Partition: backend (Python fetch/store/ranking)
Memory overrides: none (no `.claude/skill-memory/multi-perspective-review/` present)

Targeted re-review — only the three round-1 blocking reviewers run this iteration:
- Domain Logic Reviewer (backend) — owned B1, B2, B3; the discovery state machine is domain logic
- Naming & Clarity Guardian (baseline) — owned B4; the count-vs-contiguity conflation
- Ripple Effect Analyst (common) — corroborated B2; owns the "does the T1 carve-out ripple" question

Not re-run this round (round-1 APPROVE, revision does not disturb their domains): API & Contract, Error Handling & Resilience, Test Coverage Auditor, Backward Compatibility, Tech Debt Sentinel. The stop_signal/never-sleep discipline (Error Handling's HIGH-confidence APPROVE) is unchanged; the additive-state shape (Backward Compat) is unchanged.

---

## Domain Logic Reviewer — APPROVE (Confidence: HIGH)

I owned three of the four round-1 blockers. All three are genuinely resolved, not papered over.

**B1 — RESOLVED.** The v1 hole was "first-seen == caught up" short-circuiting the scan at a pin. T2.4a replaces the stop predicate outright: skip-not-stop across a bounded leading prefix of already-seen/pinned items, collect every un-seen clip on the page, and re-key the caught-up signal on **"the page yielded zero new un-seen clips"** rather than "the first seen item was encountered." That is the correct predicate — it rests discovery correctness on per-shortcode dedupe (AC-d), not on positional feed order, which is exactly what the natgeo `pks_descending == false` probe demanded. AC-a (page with ~3 leading pins + newer un-seen clips below → un-seen clips collected, no silent drop) directly exercises the former data-loss path. The plan no longer contradicts the observed feed behavior.

**B2 — RESOLVED.** This was the sharp one: B1 could not be honored without touching the "frozen top_scan." The revision does the right thing in the right order — it (1) verified against merged source that `_consume_page` HARD-STOPS mid-page in TOP_SCAN (branch b, not a), (2) ran the lever analysis proving B1 is unfixable purely T2-side (a pin's low `media_id` trips the SECONDARY numeric watermark even with its shortcode withheld from `seen`; the only T2-side suppression — `high_water=None` + `seen=∅` — destroys the caught-up short-circuit and burns the full budget), and (3) made the honest call: promote a scoped, bounded `_consume_page` change out of Out-of-Scope rather than leave the contradiction latent. The constraint text is explicitly amended, the carve-out is fenced (TOP_SCAN pinned-prefix only; `deep_resume` untouched; page cap / stop classifier / durable-first ordering all still frozen), and it lands as the exact fix the T1 in-code TODO reserved. This is a legitimate resolution of a real contradiction, not a dodge.

**B3 — RESOLVED.** T2.4's AC now states `high_water_media_id` advances to `max(prior, max(persisted media_ids))` and never decreases even with a low-pk pin on the page, and it correctly delegates that to T1's `store.write_window` (which already computes the numeric max) rather than computing a positional bump T2-side. The monotonic-non-decreasing property is asserted. Correct.

No new domain-logic blocker. One thing I checked specifically: the T2.5 phantom-segment argument now holds under the T2.4a change. A pin is already-seen → skipped by dedupe → never enters the newly-collected set → cannot raise `batch_min`; and even a cold-fetch un-seen pin has a LOW `media_id` that *lowers* `batch_min`, making `batch_min > prior_newest` FALSE. So a pin can neither open a phantom segment nor (via B3) push the watermark backward. The two pin-hazards are closed consistently across T2.4/T2.4a/T2.5. Good.

**SUGGESTION (non-blocking):** T2.5 opens a new segment on `stop_reason == page_cap AND batch_min > prior_newest`. Confirm during build that after T2.4a a genuinely-caught-up page returns `stop_reason == caught_up` (not `page_cap`) so the segment predicate can't misfire on the pin-skip path — the two stop reasons must stay distinct. This is an AC to assert, not a design gap.

## Naming & Clarity Guardian — APPROVE (Confidence: HIGH)

**B4 — RESOLVED.** This was my blocker: one word, "complete," silently meaning two different things (count reached vs. coverage contiguous), letting a `count>=90`-with-gap pool be served as done. The revision splits the concept cleanly into two named fields with distinct roles:
- `pool_depth` — raw stored-clip count, explicitly labeled an *effort metric*.
- `coverage_contiguous` — `len(coverage_segments)==1 AND that segment reaches scan_depth OR is terminal at end_of_feed`.

The serve-from-store gate (T2.2) now keys on `coverage_contiguous`, and the envelope's `complete` is defined as `== coverage_contiguous`, "NOT raw count." Crucially, T2.2 spells out the previously-dangerous case in words: a `pool_depth >= scan_depth` pool with a gap does NOT serve-from-store — it proceeds to deepen, with the note flagging "incomplete coverage: N segments — converging." That is the ambiguity closed at both the gate and the surface, and the incomplete-coverage state is now *visible* rather than hidden behind a true `complete`. This is exactly the separation I asked for.

Naming quality across the revision is good: `coverage_contiguous`, `newest_media_id`/`oldest_media_id`/`resume_cursor`, `batch_min` read as what they mean. No boolean-ambiguity traps left in the coverage surface.

**SUGGESTION (non-blocking):** The envelope field is named `complete` but is defined as contiguity. Since a short account that hit `end_of_feed` before `scan_depth` is also "complete: true" (terminal), a consumer might read `complete` as "reached scan_depth." Consider a one-line doc on the envelope field — "complete = coverage is a single contiguous segment reaching scan_depth OR the account's real end" — so the terminal-vs-deep distinction isn't re-litigated by a caller. Naming is fine; only the doc-string is worth a sentence.

## Ripple Effect Analyst — APPROVE (Confidence: HIGH)

I corroborated B2 in round 1 — my concern was that "reuse T1 unchanged" collided with the discovery requirement, and that whatever resolved it would ripple through coverage/deepen/watermark. The revision resolves the collision by explicitly *widening* the blast radius by exactly one bounded edit and then fencing everything around it. I read the code T2 doesn't change to check the fence holds:

- **`deep_resume` — not touched.** T2.4a AC-c requires `deep_resume` byte-for-byte unchanged, and the change is gated to TOP_SCAN's stop branch. Deepen (T2.6) draws on the same `fetch_window` but a different mode, so the carve-out does not ripple into it. Good — this was the primary ripple risk of promoting a `_consume_page` change.
- **Page cap / stop_signal classifier / durable-first write ordering — still frozen.** Constraints and Out-of-Scope both restate this after the amendment. The carve-out is scoped to the caught-up stop predicate, not the budget or the classifier.
- **`store.write_window` — relied on, not modified.** B3's numeric-max bump is delegated to the existing T1 behavior; T2 adds no positional bump path. No ripple.
- **`coverage_segments` — additive.** Existing state fields untouched (round-1 Backward Compat point still holds).

**On the specific new-risk question — does T2.4a reintroduce the budget-burn / caught-up==1-page regression?** This is the one place I pushed hardest, and the revision defends it adequately but the defense is *load-bearing on the implementation*, not fully settled by the plan:
- The skip is bounded to IG's observed pin cap (~3, a named constant), and caught-up is re-keyed to "page yielded zero new un-seen clips." A fully caught-up page (pins + all-seen) still yields zero new → stops after 1 page → `pages_fetched == 1`, 0 rows (AC-b). So *by construction* the bound prevents the "skip forever → 4-page walk" degeneration.
- The residual risk is a too-loose or off-by-one skip bound turning some caught-up calls into multi-page walks. The plan handles this the right way procedurally: T2.4a is "reviewed on its own for budget-burn regression," the `pages_fetched == 1` anti-regression test is mandatory and must stay green through T2.4a, and the Handoff Notes commit to re-planning (fall back to batch-only deferral) if the invariant can't be preserved. That is the correct safety net for a plan-stage artifact — the guard is named, tested, and has an escape hatch.

I'm satisfied this is not a new blocker: the regression is explicitly the thing T2.4a's own AC and dedicated review guard against, and the bound is finite. It stays a tracked risk, not an open hole.

**On the T2.3 governor / stop-abort interaction (raised for scrutiny):** sound. The reserved deepen page is a *floor on allocation*, not a commitment to spend — T2.7 states a stop_signal in top-check aborts the whole call and the reserved deepen page is NOT spent, and T2.3's AC mirrors it ("stop_signal in phase 1 means phase 2 not started"). There's no path where the reserve forces a metered request after a stop. No ripple, no contradiction.

**SUGGESTION (non-blocking):** Since T2.4a "may be split to a standalone T1.x pre-req ticket," make sure whichever packaging is chosen keeps the caught-up anti-regression test in the SAME suite that guards T1's existing top_scan — if the test moves with the ticket, a future T1 refactor could regress the pin-skip without tripping T2's suite. Keep the guard co-located with `_consume_page`.

---

## Review Summary

| Reviewer | Verdict | Blocking | Suggestions | FYI | Confidence |
|---|---|---|---|---|---|
| Domain Logic | APPROVE | 0 | 1 | 0 | HIGH |
| Naming & Clarity | APPROVE | 0 | 1 | 0 | HIGH |
| Ripple Effect | APPROVE | 0 | 2 | 0 | HIGH |

**Overall Recommendation: APPROVE**

**Rationale:** All four round-1 blockers are genuinely resolved, and each by the correct mechanism rather than by softening the finding. B1 is fixed at the predicate level (skip-not-stop, caught-up re-keyed to "zero new un-seen clips," correctness resting on per-shortcode dedupe). B2 — the hard contradiction — is resolved honestly: the plan verified T1's real hard-stop behavior against merged source, proved via lever analysis that a pure-T2 fix is impossible without destroying the caught-up short-circuit, and made the disciplined call to promote one bounded, fenced `_consume_page` change out of scope while re-freezing every other T1 contract. B3 delegates the numeric-max watermark bump to T1's existing `store.write_window` and asserts monotonicity. B4 splits `pool_depth` (count/effort) from `coverage_contiguous` (single segment to scan_depth or terminal), gates serve-from-store on contiguity, and surfaces `complete` as contiguity with the incomplete-coverage state made visible. The revision's one deliberate blast-radius increase (the T1 carve-out) is fenced against ripple into `deep_resume`, the page cap, the stop classifier, and durable-first ordering — checked each. The chief new-risk candidate — that the pinned-prefix skip reintroduces the budget-burn / caught-up==1-page regression — is bounded by construction (finite ~3 skip, caught-up keyed on zero-new), guarded by a mandatory anti-regression test, given its own dedicated review, and backed by a re-plan escape hatch. That is an adequately-contained risk for a plan artifact, not an open blocker. No new blocking problems introduced.

**Resolved prior findings:** B1 (Domain Logic), B2 (Domain Logic + Ripple Effect), B3 (Domain Logic), B4 (Naming & Clarity) — all four RESOLVED.

**Blocking Items:** none.

**Top Suggestions (all non-blocking, build-time ACs):**
1. (Domain Logic) Assert that a caught-up page returns `stop_reason == caught_up`, not `page_cap`, so T2.5's segment-open predicate can't misfire on the T2.4a pin-skip path — keep the two stop reasons distinct.
2. (Ripple Effect) Whatever T2.4a packaging is chosen (in-T2 vs standalone T1.x), keep the caught-up `pages_fetched == 1` anti-regression test co-located with `_consume_page`, so a future T1 refactor can't silently regress the pin-skip outside T2's suite.
3. (Naming & Clarity) Add a one-line envelope doc for `complete` clarifying it means "single contiguous segment reaching scan_depth OR the account's real end," so the terminal-vs-deep case isn't re-litigated by a caller.

**Corroborated Findings:** B2's resolution is jointly confirmed by Domain Logic (the contradiction is honestly resolved) and Ripple Effect (the carve-out is fenced against ripple into deep_resume and the other frozen T1 contracts) — highest-signal confirmation this round.

**Accepted Debt:** The pinned-prefix budget-burn regression remains a tracked risk (not debt) — bounded, tested, dedicated-review-guarded, with a named re-plan fallback. No unaddressed debt introduced by the revision.
