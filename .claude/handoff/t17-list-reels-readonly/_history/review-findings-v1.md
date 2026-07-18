---
artifact_type: handoff
artifact_version: 2
producer_role: review
consumer_role: implementation
plan_type: findings
slug: t17-list-reels-readonly
scope_hint: "Pivot list_reels to READ-ONLY over the store (CQRS hard split) — Plan review (iteration 1)"
canonical_name: review-findings
overlays: public-api-change
status: draft
version: 1
created: 2026-07-18T10:45:53Z
updated: 2026-07-18T10:45:53Z
prior_versions: []
---

# Review findings: Pivot list_reels to READ-ONLY over the store (CQRS hard split) — Plan review (iteration 1)

**Owner:** vd

**Review type:** PLAN (not code). Target: `.claude/handoff/t17-list-reels-readonly/planner-task.md` v1.

## Triage Decision
Scope: medium-large (one tool's logic + response schema; ~80 lines excised, cross-module `last_analyzed_at` touch, tests, docs; `public-api-change` overlay).
Partition: backend (Python).
Memory overrides: `always_include` Reliability / Rate-Limit Reviewer (config.md); custom rule — IG-hitting path change → include Reliability reviewer + treat as ≥medium.

Selected reviewers:
- Reliability / Rate-Limit Reviewer (always_include) — the zero-IG invariant is the headline
- API & Contract Reviewer (backend) — param removal + response-schema growth
- Backward Compatibility Reviewer (common) — `fresh_fetch` removal, frozen snapshot
- Error Handling & Resilience Inspector (common) — never-raise typed envelope, not-analyzed error
- Domain Logic Reviewer (backend) — the three-state readiness gate
- Ripple Effect Analyst (common) — `write_window`/`State` cross-module touch
- Test Coverage Auditor (common) — zero-IG assertion + variants
- Documentation Reviewer (common) — README + CLAUDE.md
- Naming & Clarity Guardian (baseline) — `error_kind` discriminator
- Tech Debt Sentinel (baseline) — dead helpers after excision

Skipped: Accessibility (always_exclude, no UI); Concurrency/Data-Integrity/Performance (no new hot path, no migration, single-process read); Security (no auth/secrets introduced — the change deletes the last interactive network path).

---

## Reliability / Rate-Limit Reviewer — verdict: APPROVE (HIGH)
(a) Escape-hatch removal is thorough and enforceable. Step 1 deletes the network block + the `AnonymousClient`/`resolve_user_id`/`fetch_window`/`PageBudget` imports and drops the `client` param from `run_list_reels`; Step 6 removes `fresh_fetch` from the wrapper; Out-of-Scope explicitly forbids a re-enable config knob; the zero-IG test (Step 8-i) is the runtime guard. This closes the last interactive metered path and makes "never sleeps" fully true. No blocking issues.
- FYI: the zero-IG test correctly poisons the **HTTP layer** (not the now-removed `AnonymousClient` import) — the right seam, since after excision the symbol is gone from the module.

## Error Handling & Resilience Inspector — verdict: REQUEST CHANGES → downgraded to suggestion (HIGH)
(c) The not-analyzed error rides the never-raise envelope correctly (returns a dict; wrapper try/except stays). But the precedent claim needs a correction the implementer must not take literally: **`download._error` has no `retryable` field and no `error_kind` field** — it encodes retryability purely via `partial` (partial=True = retryable cooldown; partial=False+`error` = non-retryable, the aged-out case). The plan (Step 3) adds *new* `retryable=False` and `error_kind` keys that the download contract does not carry.
- Not blocking, but flag the **contract asymmetry**: an LLM consumer branching on `retryable`/`error_kind` finds them on `list_reels`' error but not on `download_reel`'s aged-out error. Recommend the implementer either (i) also add `retryable`/`error_kind` to `download._error` for a uniform error contract, or (ii) follow the precedent literally (partial-based retryability, no new field) — the plan's own Open Question #2 already flags the collision check; make that resolution a required acceptance item, not optional.

## Domain Logic Reviewer — verdict: APPROVE (HIGH)
(b) The readiness-vs-informational separation is correct and well-specified. `_has_been_analyzed(state, pool_depth)` keys on coverage evidence (empty pool AND no `coverage_segments` AND no `high_water_media_id`), explicitly NOT the store-count-vs-90 figure, which is reused only for the informational staleness block. The Step-2 acceptance ("1 stored reel non-contiguous → serves state b, NOT errors") and the three-state boundary test lock this. Verified against `coverage.is_contiguous`: the terminal branch means a small account (5 reels, terminal) correctly reports `complete=True` at pool_depth < scan_depth — which is exactly why the gate must not be raw count. Plan gets this right.
- FYI (edge, resolve in impl): a handle with `coverage_segments` present but an **empty pool** (a window persisted 0 reels) is classed "analyzed" and would serve empty `reels` rather than the not-analyzed error. Define the intended behavior for segments-present-but-pool-empty in Step 2 so it isn't decided by accident.

## API & Contract Reviewer — verdict: APPROVE with one schema-determinism ask (MED)
(d) Ranking is reused unchanged (`ranking.select_top` over the full deduped pool, per-shortcode dedupe + numeric `media_id`, never positional) — the plan explicitly forbids algorithmic change and the test (Step 8-iii) asserts dedupe + numeric ordering with out-of-order seeded rows. Good.
- Suggestion: pin **when `staleness` is present**. `_envelope` is shared by the validation-error path (line 131) and will be shared by the not-analyzed error path. State in Step 4 whether `staleness` is always-present-on-success / always-absent-on-error, so the frozen snapshot is deterministic and the consumer can rely on a stable shape rather than a conditional key.

## Backward Compatibility Reviewer — verdict: APPROVE (HIGH)
`fresh_fetch` is a real removal (not a silently-ignored no-op — Handoff Notes make this explicit), the breaking classification is honest, the Consumer Inventory + Versioning + deliberate frozen-snapshot update (Step 7) are all present and treated as acceptance criteria. Backward-compat of old state YAMLs is covered (`last_analyzed_at` defaults to `None`, with a load test). Nothing to add.

## Ripple Effect Analyst — verdict: APPROVE (MED)
The one cross-module touch (`last_analyzed_at` stamped in `store.write_window`, read-only in `list_reels`) is correctly identified as the sole seam, and routing the stamp through `write_window` means `batch.py` needs no change. Decision D1 (state field vs `max(fetched_at)` derivation) is the right fork and the recommendation (explicit field = analysis-time, distinct from per-row URL-resolve time) is sound.
- Suggestion: Step 5 says Step 4 "can stub the field read while this lands" — ensure D1 is resolved *before* Step 8 tests assert `staleness.last_analyzed_at`, else the staleness test asserts against a stub. The plan says this; keep it a hard ordering constraint.

## Test Coverage Auditor — verdict: APPROVE (HIGH)
(e) Coverage is strong: zero-IG assertion in BOTH not-analyzed and analyzed cases (the non-negotiable gate), not-analyzed typed error, analyzed serve+ranking with duplicate/out-of-order rows, staleness table-driven (fresh vs aged `fetched_at`, present/absent `last_analyzed_at`), three-state boundary, deliberate frozen snapshot, and a regression run of `download_reel`/`batch` suites. The plan also correctly says to migrate (not drop) still-load-bearing coverage assertions from deleted network-path tests to the batch suite.
- Suggestion: add an explicit assertion that the not-analyzed error is produced **without** any network poison firing (i.e., the zero-IG spy records 0 calls on the error path specifically), so a future regression can't satisfy "not analyzed" by attempting a fetch that fails.

## Documentation Reviewer — verdict: APPROVE (HIGH)
(f) Step 9 updates both README (`list_reels` read-only, must `start_batch_fetch` first, staleness meta, no longer metered) and CLAUDE.md (the Architecture "`list_reels` calls it synchronously" line AND the Politeness invariant's `list_reels` mention). Acceptance criteria are concrete. Complete.

## Naming & Clarity Guardian — verdict: APPROVE (LOW)
`error_kind: "not_analyzed"` is a clear, machine-branchable discriminator. Confirm it does not collide with any future `download_reel` discriminator (plan already flags this). Minor: the served-path note builder should read naturally now that "budget cooling" is gone.

## Tech Debt Sentinel — verdict: APPROVE with a completeness note (MED)
The plan's deletion list names `_cooling_note` and the stop_signal/partial handling, but **omits `_compose_note`**, which also goes partly dead once the network path is removed (its `partial`/cooling branches become unreachable; only the contiguous/converging branches remain useful for served notes). Call out `_compose_note` in Step 1's deletion/refactor list so it isn't left as a half-dead helper. Non-blocking.

---

## Review Summary

| Reviewer | Verdict | Blocking | Suggestions | FYI | Confidence |
|---|---|---|---|---|---|
| Reliability / Rate-Limit | APPROVE | 0 | 0 | 1 | HIGH |
| Error Handling & Resilience | APPROVE* | 0 | 1 | 0 | HIGH |
| Domain Logic | APPROVE | 0 | 0 | 1 | HIGH |
| API & Contract | APPROVE | 0 | 1 | 0 | MED |
| Backward Compatibility | APPROVE | 0 | 0 | 0 | HIGH |
| Ripple Effect Analyst | APPROVE | 0 | 1 | 0 | MED |
| Test Coverage Auditor | APPROVE | 0 | 1 | 0 | HIGH |
| Documentation | APPROVE | 0 | 0 | 0 | HIGH |
| Naming & Clarity | APPROVE | 0 | 0 | 0 | LOW |
| Tech Debt Sentinel | APPROVE | 0 | 1 | 0 | MED |

**Overall Recommendation:** APPROVE

**Rationale:** The plan is unusually complete and correctly nails all six scrutiny axes: (a) the IG-fetch excision is total with no escape hatch and is guarded by a zero-IG test plus a grep acceptance; (b) the readiness gate is coverage-contiguity via `_has_been_analyzed`, cleanly separated from the informational store-count-vs-90 staleness figure (verified against `is_contiguous`'s terminal-or-≥scan_depth semantics); (c) the not-analyzed error rides the never-raise envelope with `partial=False`; (d) ranking is reused unchanged (dedupe + numeric `media_id`, never positional); (e) test coverage includes the non-negotiable zero-IG assertion in both cases; (f) README + CLAUDE.md are updated including the Politeness invariant line. No blocking findings survived — every issue is a refinement the implementer can absorb without re-planning.

**Top Suggestions (act during implementation):**
1. **Error-contract symmetry (Error Handling):** `download._error` has no `retryable`/`error_kind` fields — it encodes retryability via `partial`. Resolve the plan's Open Question #2 as a hard acceptance item: either mirror `retryable`/`error_kind` onto the download error too, or drop the new fields and follow the partial-based precedent literally. Don't ship an asymmetric error contract.
2. **`staleness` presence determinism (API & Contract):** specify whether `staleness` is always-on-success / always-absent-on-error so the frozen snapshot and consumers see a stable shape.
3. **`_compose_note` cleanup (Tech Debt):** add it to Step 1's deletion/refactor list — it goes partly dead with the network path removed.
4. **D1 ordering (Ripple Effect):** resolve `last_analyzed_at` sourcing before Step 8 so staleness tests don't assert against a stub.
5. **Not-analyzed zero-call assertion (Test Coverage):** assert the error path records 0 network calls specifically, so "not analyzed" can never be satisfied by a failed fetch attempt.

**Corroborated Findings:** none flagged by 2+ reviewers as blocking — the plan's internal consistency held up.

**Accepted Debt:** none new.

**Blocking Items:** none.

*Error Handling verdict downgraded from REQUEST CHANGES to APPROVE-with-suggestion because the precedent divergence is a contract-consistency refinement (single internal consumer) and the plan already surfaces it as an Open Question, not an unresolved gap.
