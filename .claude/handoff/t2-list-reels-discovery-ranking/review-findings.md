---
artifact_type: handoff
artifact_version: 2
producer_role: review
consumer_role: implementation
plan_type: findings
slug: t2-list-reels-discovery-ranking
scope_hint: T2 — list_reels discovery + ranking
canonical_name: review-findings
overlays: []
status: draft
version: 1
created: 2026-07-15T17:21:29Z
updated: 2026-07-15T17:21:29Z
prior_versions: []
---

# Review findings: T2 — list_reels discovery + ranking

**Change under review:** T2 `list_reels` — anonymous discovery + ranking with call-driven fill. Uncommitted working tree on `main`. Modified: `fetch.py`, `store.py`, `config.py`, `mcp_server.py`, `window.py`, `tests/test_cursor.py`. New: `coverage.py`, `list_reels.py`, `ranking.py`, `tests/test_coverage.py`, `tests/test_list_reels.py`, `tests/test_pinned_prefix.py`, `tests/test_ranking.py`.

**Spec source:** approved `planner-task.md` (12 steps T2.0–T2.10). No `implementation-build.md` was persisted for this scope — reviewed code against plan + working-tree diff.

**Test status:** `90 passed in 0.14s` (offline).

## Triage Decision
Scope: **large** (3 new modules + bounded engine change, ~290 net lines, cross-cutting)
Partition: **backend** (Python)
Memory overrides: `always_include` Reliability / Rate-Limit Reviewer; `always_exclude` Accessibility; IG-hitting path ⇒ treat as ≥medium (applied — escalated to large)

Selected Reviewers:
- **Reliability / Rate-Limit Reviewer** (project-mandated) — politeness is load-bearing
- **Security & Trust Reviewer** (common) — anonymous-only invariant, credential surface
- **Domain Logic Reviewer** (backend) — pinned-prefix semantics, coverage segments, caught-up boundary
- **Test Coverage Auditor** (common) — the modified T1 test + invariant coverage
- **Ripple Effect Analyst** (common) — T1 carve-out fencing, `run_window` now orphaned
- **API & Contract Reviewer** (backend) — `list_reels` signature + envelope shape change
- **Error Handling & Resilience Inspector** (common) — no-exception-to-client claim
- **Tech Debt Sentinel** (baseline) — orphaned `run_window`, `PINNED_PREFIX_BOUND` assumption
- **Naming & Clarity Guardian** (baseline)

Skipped: Accessibility (excluded — no UI); Concurrency (single-process sync path); Data Integrity/Migration (additive YAML field, back-compatible); Performance Critic (folded into Reliability, pool sizes ~90).

---

## Reliability / Rate-Limit Reviewer — **APPROVE** (Confidence: HIGH)
The politeness invariant holds in code and is test-locked:
- **Never sleeps:** `sleep=None` passed to `fetch_window` in both phases (`list_reels.py:175,254`); no `time.sleep` anywhere on the path. `test_governor_never_sleeps` monkeypatches `time.sleep` to throw and passes.
- **Combined ≤ budget:** single `PageBudget(max_pages_per_call)` shared across phases; top-check capped at `max(1, total-1)` with ≥1 page reserved for deepen (`list_reels.py:165-169`); deepen gets only `budget.remaining`. `test_budget_cap_across_both_phases` proves combined `pages_fetched == 4` with `max_pages=4`.
- **Stop + partial, no retry/poll:** `partial` is `True iff stopped on a stop_signal` (`fetch.py:118`), set only in the abnormal-stop branches — never for `page_cap`/`caught_up`/`end_of_feed`. `if not top.partial:` (`list_reels.py:193`) skips deepen entirely on a top-check stop_signal, leaving the reserved page unspent. `test_stop_signal_in_topcheck_aborts_whole_call` confirms exactly 2 HTTP calls (profile + 1 feed) and `pages_fetched == 1`.

## Security & Trust Reviewer — **APPROVE** (Confidence: HIGH)
Anonymous-only invariant intact on every new code path:
- `x-ig-app-id` exists **only** in `http_client.DEFAULT_HEADERS` via `IG_APP_ID` and is applied solely by `AnonymousClient.get`. T2 code (`list_reels.py`) constructs no headers — every IG hit routes through `resolve_user_id`/`fetch_window`, both taking the `AnonymousClient`.
- `assert_anonymous` guards every send, redirect-follow, cookie update, and client construction (`http_client.py:298,307,324,351`). Auth cookies (`sessionid`/`ds_user_id`/`ds_user`) and auth params rejected; an IG-issued auth cookie is dropped, not stored.
- No `login`/`session`/`Authorization`/token/burner on any T2 path. `test_x_ig_app_id_on_every_api_call` asserts the header on each call. **No violation.**

## Domain Logic Reviewer — **APPROVE** (Confidence: HIGH)
- **T2.4a pin-skip is correct and bounded.** `_consume_page` skips a leading prefix of ≤`PINNED_PREFIX_BOUND`(=3) known items *only while nothing new has been collected*, then treats the next known item as the real caught-up boundary (`fetch.py:358-372`). A page that skips known items but collects nothing new signals `CAUGHT_UP` (`fetch.py:388-392`) — so the genuinely-caught-up scan still short-circuits on page 1 and returns `caught_up`, **not** `page_cap`. Traced mid-page cases (`[pin,new,seen]`, `[new,seen]`, `>3 pins`) — all resolve correctly; over-`bound` pins under-collect by documented design (IG pin cap ≈3).
- **deep_resume unchanged:** the pin logic is fenced behind `if mode is FetchMode.TOP_SCAN`; `test_deep_resume_ignores_seen_and_watermark` confirms deep_resume keeps skip-seen dedupe but applies no watermark stop.
- **Coverage contiguity (B4 fix):** `is_contiguous` requires exactly one segment AND (terminal OR `pool_depth >= scan_depth`) (`coverage.py:171-184`); serve-from-store gates on this, never raw count. A pin cannot open a phantom segment — the gap predicate `batch_oldest > prior_newest` is numeric and a pin's low pk lowers `batch_oldest` (`coverage.py:95-98`, `test_pin_cannot_open_phantom_segment_low_pk`).
- **high_water monotonic (B3 fix):** `write_window` sets `max(state.high_water_media_id or 0, max(persisted_media_ids))` and pins are deduped out before this — never bumped backward. `test_high_water_monotonic_with_low_pk_pin` confirms.

## Test Coverage Auditor — **APPROVE** (Confidence: HIGH)
**Verdict on the modified `test_caught_up_short_circuits_on_membership_page1`: LEGITIMATE — not masking a regression.**
The original test asserted "newest clip seen ⇒ hard-stop on first item," which is *precisely the behavior T2.4a intentionally removes* (a lone leading seen item is now treated as a possible pin and skipped). That old assertion could not survive the sanctioned change. It was correctly repurposed to guard the load-bearing anti-regression — *genuinely* caught up (all three fixture clips in `seen`) still yields `pages_fetched==1`, `reels==[]`, `stop_reason=="caught_up"`. Verified against the fixture (3 clips: DZclip04/02/01; no clip03): all three skipped as pins, page ends with zero new ⇒ `CAUGHT_UP`. The behavior the old test *used* to cover (skip pins, collect new below) is now covered by a **dedicated co-located file** `test_pinned_prefix.py` with 6 cases including bounded-tolerance and deep_resume-unchanged proofs. Coverage is complete: every hard invariant has a named test (serve-from-store contiguity, budget cap, never-sleeps, stop-in-topcheck-aborts, stop-in-deepen-partial, high_water-monotonic, x-ig-app-id, phantom-segment guard).

## Ripple Effect Analyst — **APPROVE** (Confidence: HIGH)
The T1 carve-out is fenced: the only `fetch.py` engine change is the TOP_SCAN pin-skip; `deep_resume`, the page cap, the stop classifier, and durable-first ordering are untouched. `store.write_window` is unchanged; the new `save_coverage_segments` reloads state first so an immediately-preceding `write_window`'s anchor/cursor is not clobbered (durable-first preserved — CSV fsync, then two independent atomic state writes; a crash between them loses only recomputable segments). One ripple: `window.run_window` is now **orphaned** (`mcp_server` switched to `run_list_reels`); flagged by its own TODO as retained for the future batch runner. See Tech Debt.

## API & Contract Reviewer — **APPROVE** (Confidence: HIGH)
`list_reels` MCP signature expands to the full T2.1 contract (`count`/`sort_by`/`min_views`/`min_duration`/`max_age_days`/`scan_depth`/`fresh_fetch`), all optional with config `top_reels` fallback. Envelope is well-formed and self-documenting (`complete` + `complete_means` doc, `segments`, `pool_depth`, `pages_fetched`, `stop_reason`). Invalid `sort_by` and negative bounds return a clean typed error envelope, not a traceback (`_validate`, tests present). `config.py` adds `min_duration` additively. No back-compat break (first real `list_reels` contract).

## Error Handling & Resilience Inspector — **APPROVE w/ suggestion** (Confidence: MED)
Expected failure modes handled cleanly without raising: stop_signals return partials, `resolve_user_id` failure writes an empty window and returns a partial. **However**, the docstring claim "never lets an exception reach the MCP client" (`list_reels.py:22`) is not backed by a top-level `try/except` — an *unexpected* exception (malformed feed JSON, disk error) would propagate to FastMCP, which converts it to an MCP error response rather than a typed envelope. The load-bearing case (rate-limits) is covered; the claim slightly overstates. Non-blocking suggestion below.

## Tech Debt Sentinel — **APPROVE w/ notes** (Confidence: HIGH)
Two tracked, honestly-documented items (neither blocking):
1. `window.run_window` orphaned — TODO already flags "consolidate onto this OR retire once the batch-runner ticket lands." Two compose paths risk drift; acceptable as a named follow-up.
2. `PINNED_PREFIX_BOUND = 3` encodes an observed IG pin cap; documented as an anonymous-feed implementation detail, not user config, and its over-bound under-collection is explicitly tested (`test_pin_tolerance_is_bounded`). Reasonable, but revisit if IG's pin cap ever exceeds 3.

## Naming & Clarity Guardian — **APPROVE** (Confidence: HIGH)
Names are precise and self-explaining (`coverage_contiguous`/`pool_depth` split, `seed_or_extend_top`, `segment_to_deepen`, `_COMPLETE_DOC`). Docstrings carry the "why" (pin float, watermark-as-backstop, numeric gap predicate). No misleading identifiers.

---

## Review Summary

| Reviewer | Verdict | Blocking | Suggestions | FYI | Confidence |
|---|---|---|---|---|---|
| Reliability / Rate-Limit | APPROVE | 0 | 0 | 0 | HIGH |
| Security & Trust | APPROVE | 0 | 0 | 0 | HIGH |
| Domain Logic | APPROVE | 0 | 0 | 1 | HIGH |
| Test Coverage Auditor | APPROVE | 0 | 0 | 0 | HIGH |
| Ripple Effect Analyst | APPROVE | 0 | 0 | 1 | HIGH |
| API & Contract | APPROVE | 0 | 0 | 0 | HIGH |
| Error Handling | APPROVE w/ suggestion | 0 | 1 | 0 | MED |
| Tech Debt Sentinel | APPROVE w/ notes | 0 | 1 | 1 | HIGH |
| Naming & Clarity | APPROVE | 0 | 0 | 0 | HIGH |

**Overall Recommendation: APPROVE** (0 blocking findings)

**Rationale:** All five hard invariants are verified present in the actual code and each is locked by a dedicated test: anonymous-only (`x-ig-app-id` sole-sourced via `AnonymousClient`, `assert_anonymous` on every send), never-sleeps + ≤4-page combined budget + partial-on-first-stop-signal, the bounded TOP_SCAN-only T2.4a carve-out with `deep_resume` untouched and `caught_up`≠`page_cap` preserved, non-destructive store with contiguity-gated serve and monotonic high_water, and durable-first write ordering. The implementation faithfully realizes the approved plan. 90 offline tests pass.

**Blocking Items:** None.

**Judgment on the modified T1 test:** Legitimate part of the sanctioned T2.4a change, **not** a masked regression — the old assertion encoded the exact hard-stop the change intentionally removes, the caught-up short-circuit it now guards is the genuine anti-regression, and the removed behavior is re-covered by the dedicated `test_pinned_prefix.py`.

**Judgment on the unobserved live T2.10 happy-path:** **Acceptable ship condition, with a follow-up gate.** No invariant is violated and nothing in the code is unverified logic: the pin-float premise it depends on was live-proven (T1.2 natgeo probe), the stop-signal path was live-proven this round, and the fetch primitive itself was piloted in T1. Everything T2 adds on top (two-phase compose, coverage segments, ranking) is deterministic pure logic exhaustively unit-tested (90 offline). Residual risk is low but nonzero. Recommend shipping and tracking a single live happy-path smoke run once the IP cooldown clears as a non-blocking verification follow-up — not a code blocker (there is no code change it would prompt).

**Top Suggestions (non-blocking):**
1. **Error Handling** — either wrap `run_list_reels` body in a top-level guard that converts unexpected exceptions into an `error` envelope (matching the docstring), or soften the "never lets an exception reach the MCP client" claim to scope it to rate-limit/stop-signals. (`list_reels.py:22`)
2. **Tech Debt** — resolve the orphaned `window.run_window` when the batch-runner ticket lands (retire or consolidate) to avoid two drifting compose paths. (`window.py`)

**FYI:**
- **Stale signed-URL on serve-from-store** — a "complete"-coverage handle serves stored `video_url`s with zero network indefinitely; per the ~36 h TTL invariant these can age out. Discovery/ranking (T2) doesn't download, so non-blocking here, but the downloader ticket (T3) must re-resolve `video_url` older than ~24 h. (Domain Logic)
- **CSV re-read amplification** — `count_reels`/`select_top` read the full CSV ~4–5×/call; negligible at ~90 rows, worth noting if pools grow. (Ripple Effect)

**Corroborated Findings:** None flagged by 2+ reviewers as problems — the corroboration this round is *positive*: Domain Logic + Test Coverage independently confirm the modified test is legitimate; Reliability + Security independently confirm the invariants hold.

**Accepted Debt:** Orphaned `run_window` (follow-up: batch-runner ticket); `PINNED_PREFIX_BOUND=3` assumption (revisit if IG pin cap changes).
