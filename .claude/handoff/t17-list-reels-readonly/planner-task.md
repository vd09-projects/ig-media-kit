---
artifact_type: handoff
artifact_version: 2
producer_role: planner
consumer_role: implementation
plan_type: task
slug: t17-list-reels-readonly
scope_hint: Pivot list_reels to READ-ONLY over the store (CQRS hard split)
canonical_name: planner-task
overlays: public-api-change
status: consumed
version: 1
created: 2026-07-18T10:38:24Z
updated: 2026-07-18T12:39:19Z
prior_versions: []
---

# Task plan: Pivot list_reels to READ-ONLY over the store (CQRS hard split)

**Overlays:** public-api-change

## Problem

`list_reels` currently couples a read (rank + serve from the store) with a write (a metered IG fetch that deepens the pool toward `scan_depth=90`). That coupling lets an interactive serve call surface cross-channel rate-limit / 401 cooldown errors that originate in the metered path, and leaves the "`list_reels` never sleeps" invariant only half-true (it never sleeps but can still *attempt* IG and fail on a cooldown). The accepted decision (`decisions/architecture/api-contract/2026-07-16-list-reels-is-read-only-over-the-store-cqrs-split.md`) mandates a hard CQRS split: `list_reels` becomes a pure READ-ONLY query over the local store and NEVER hits Instagram on any code path. Analysis moves entirely to the explicit command tools (`start_batch_fetch`; `download_reel`'s >24h re-resolve). Doing it now closes the last interactive metered path and makes the never-sleeps invariant fully true.

## Constraints

- **Blast radius:** one tool's behavior + its response schema; three IG-hitting call sites removed from the query path. `download_reel` and `start_batch_fetch` must remain untouched functionally.
- **Frozen four-tool surface:** tool NAME and count must not change; param-schema and response-schema changes are deliberate, tested contract changes to the frozen snapshot.
- **Never-raise typed envelope:** every outcome (including not-analyzed) is a typed dict envelope; no exception may reach the MCP client.
- **Anonymous-only:** trivially preserved — the removal deletes the only remaining interactive IG path; no auth ever.
- **Store never destructively capped:** top-N still computed over the full deduped pool.
- **Ordering:** ranking/dedupe/coverage key on per-shortcode dedupe + monotonic numeric `media_id`, never positional/newest-first feed order.
- **Solo developer, flat-file store, no external consumers** beyond the MCP client and the frozen snapshot test.

## Success Metric

- **Primary metric:** After the change, `list_reels` issues **zero** IG HTTP requests across BOTH the not-analyzed and analyzed cases (asserted by a test that fails if any `AnonymousClient`/network call fires), while an analyzed handle still returns correctly-ranked top-N from the store and a not-analyzed handle returns the typed "run start_batch_fetch first" error — 100% of these paths green in CI.
- **Counter-metric (must not regress):** ranking correctness over the pool (same top-N ordering as today for an analyzed handle), and `download_reel` / `start_batch_fetch` behavior + their tests remain unchanged; the never-raise property holds (no tool raises).
- **Evaluation window:** the PR's CI run plus one manual smoke against the offline fixture store (analyzed + not-analyzed handle) before merge.
- **Evaluator:** the implementer at PR review, backed by the new zero-IG-request test and the updated frozen-snapshot test.

## Mode

Modification.

## Existing Code Shape (modification only)

- **`src/ig_media_kit/list_reels.py::run_list_reels`** — today: (1) resolves params incl. `fresh_fetch`; (2) validates; (3) loads state + computes `contiguous = coverage.is_contiguous(segments, pool_depth, scan_depth)`; (4) **serve-from-store fast path** when `not fresh_fetch and contiguous`; (5) otherwise the **NETWORK path** — `AnonymousClient`, `resolve_user_id`, `PageBudget`, top-check via `fetch_window(FetchMode.TOP_SCAN)`, `_run_deepen` via `fetch_window(FetchMode.DEEP_RESUME)`, coverage seed/extend/apply, partial-on-stop_signal; (6) `_envelope(...)` which computes `pool_depth`, ranks via `ranking.select_top(csv_path, ...)`, and reports `coverage.complete`. **What stays:** param resolve/validate (minus `fresh_fetch`), state load, `coverage.is_contiguous`, `_envelope`'s ranking core + `pool_depth` + coverage block. **What is deleted:** the entire network path (~lines 148-225), `_run_deepen`, `PageBudget`, `_cooling_note`/stop_signal handling, and imports of `AnonymousClient`, `resolve_user_id`, `fetch_window`, `FetchMode`, `FetchResult`, `StopKind`, `STOP_SIGNAL_REASONS`.
- **`src/ig_media_kit/mcp_server.py::list_reels` (144-188)** — tool wrapper with try/except typed-envelope fallback; declares `fresh_fetch: bool = False` param; `run_list_reels` is called with `config`+`store` but NOT the gate (the gate lives on `ServerContext` via `get_gate` and is consumed inside `fetch_window`). The docstring describes call-driven fill / metered budget / "budget cooling" — all now false.
- **`src/ig_media_kit/store.py::State`** — fields: `user_id`, `high_water_media_id`, `deep_cursor`, `last_stop_reason`, `coverage_segments`. **No `last_analyzed_at`.** CSV rows carry per-row `fetched_at` (int epoch) and `video_url`. `save_state`/`load_state` round-trip the YAML dict (see `_state_dict` around lines 390-400).
- **`src/ig_media_kit/coverage.py::is_contiguous`** — the readiness predicate: exactly ONE segment that is terminal OR spans ≥ `scan_depth`. Also `segment_to_deepen` / `has_more_to_fetch` used only by the now-removed deepen path.
- **`src/ig_media_kit/download.py::_error(..., partial=False)`** — the typed-error precedent: terminal, non-retryable, `error`/`note` markers, `partial=False`. Model the not-analyzed error on this exact shape.
- **Frozen snapshot test** — a test asserts the four-tool MCP surface (names + param schemas). It will need a deliberate update for the `fresh_fetch` param change.

## Integration Points

- **`start_batch_fetch` / async runner (`batch.py`)** — the async runner is now the *only* writer that advances coverage toward `scan_depth`. If `last_analyzed_at` is sourced from a new state field (Decision D1 below), the runner (and/or `store.write_window`) must stamp it. This is the one cross-module touch point.
- **`download_reel` (`download.py`)** — unchanged, but the plan reaffirms it remains the only sync metered path (#13 >24h re-resolve). Its staleness semantics (`video_url` ~36h TTL, `fetched_at`) are the source for `list_reels`' signed-URL-maybe-expired hint.
- **`FetchGate` (`fetch_gate.py`, `ServerContext`)** — `list_reels` stops consuming it. The gate stays on `ServerContext` for the batch + download paths; only the `list_reels` code path is unwired.
- **Frozen-surface snapshot test** — the deliberate param/response schema change must be reflected there, not worked around.

## Steps

1. **Excise the IG-fetch path from `run_list_reels`** — reduce `run_list_reels` to: resolve params → validate → load state → compute readiness → branch into {not-analyzed typed error | serve + staleness}. Delete the network block (top-check, `_run_deepen`, `PageBudget`, `resolve_user_id`, `fetch_window`, stop_signal/partial/cooling-note handling) and their now-dead imports (`AnonymousClient`, `resolve_user_id`, `fetch_window`, `FetchMode`, `FetchResult`, `StopKind`, `STOP_SIGNAL_REASONS`). Remove the `client` parameter from `run_list_reels`. Files: `list_reels.py`.
   - Acceptance: `run_list_reels` has no import of and no reference to any IG client / fetch symbol; `grep` for `AnonymousClient|fetch_window|resolve_user_id|PageBudget|_run_deepen` in `list_reels.py` returns nothing. Module still imports cleanly.
   - Parallel-safe with: none (foundational — all later steps build on the reduced function).

2. **Define the three-state readiness branch (the query core)** — implement the explicit tri-state using the SAME `coverage.is_contiguous` machinery, but distinguish *readiness to serve at all* from *contiguity*: **(a) NOT ANALYZED** = no coverage evidence whatsoever (empty pool AND no `coverage_segments` AND no `high_water_media_id` / never fetched) → return the typed not-analyzed error (Step 3); **(b) ANALYZED-BUT-SHALLOW/STALE** = has coverage evidence but `is_contiguous` is False (still converging, has a gap, or shallow) OR timestamps look stale → serve ranked top-N + staleness meta (Step 4) with `coverage.complete=False`; **(c) ANALYZED & CONTIGUOUS** = `is_contiguous` True → serve ranked top-N + staleness meta with `coverage.complete=True`. Encode the "analyzed at all?" gate as a small helper (e.g. `_has_been_analyzed(state, pool_depth)`), explicitly NOT the `scan_depth` count. Files: `list_reels.py` (optionally a one-line predicate in `coverage.py` if cleaner, but do not overload `is_contiguous`).
   - Acceptance: a table-mapping exists in code/comments for the three states; the store-count-vs-90 value is computed for the staleness block but is NEVER consulted by the readiness gate. A handle with 1 stored reel but no contiguity serves (state b), NOT errors; a handle with an empty store + no segments errors (state a).
   - Parallel-safe with: none (depends on Step 1; Steps 3 and 4 depend on it).

3. **Add the typed not-analyzed error envelope** — model on `download.py::_error(..., partial=False)`: a dict mirroring the success envelope shape (`handle`, `user_id=None`, `reels=[]`, `count_returned=0`, `pool_depth=0`, `coverage`, `pages_fetched=0`) plus `partial=False`, an `error` marker, a `retryable=False` flag (fetching is a separate explicit command, not a transient cooldown), and a `note` that literally instructs "run `start_batch_fetch` first". Keep a stable machine-branchable discriminator (e.g. `error` string prefix or an `error_kind: "not_analyzed"` field) so an LLM consumer can distinguish it from the download aged-out error. Files: `list_reels.py`.
   - Acceptance: calling `run_list_reels` on an unanalyzed handle returns a dict with `partial=False`, `retryable=False`, empty `reels`, and a `note`/`error` naming `start_batch_fetch`; no exception is raised; shape keys are a superset-compatible mirror of the success envelope.
   - Parallel-safe with: Step 4 (both consume Step 2's branch; can be built together).

4. **Add the staleness metadata block to the served envelope** — extend `_envelope` (served/analyzed path only) with a `staleness` sub-dict: `last_analyzed_at` (see Decision D1), `store_count` vs `scan_depth` target (informational depth hint — reuse `pool_depth` and `p.scan_depth`, explicitly labeled informational), and a `signed_url_maybe_expired` hint computed from the reels' `fetched_at` vs a ~36h TTL (True if the freshest/served rows' `fetched_at` is older than the TTL margin, or unknown if absent). Ranking stays `ranking.select_top` over the full pool with per-shortcode dedupe + numeric `media_id` ordering — unchanged. Files: `list_reels.py` (+ read helpers from `store.py` for `fetched_at`).
   - Acceptance: an analyzed handle's envelope contains `staleness.last_analyzed_at`, `staleness.store_count`, `staleness.scan_depth_target`, and `staleness.signed_url_maybe_expired`; the depth hint is documented as informational and does not alter `coverage.complete`.
   - Parallel-safe with: Step 3.

5. **Resolve `last_analyzed_at` sourcing (Decision D1) and wire the writer** — decide and implement per D1 below. **Recommendation:** add a `last_analyzed_at: int | None` field to `State`, stamped by `store.write_window` (so both the batch runner and any writer set it) at each successful window persist; `list_reels` reads it read-only. Fallback if avoiding a schema bump: derive `last_analyzed_at = max(fetched_at)` across CSV rows. Prefer the explicit state field — it records *analysis* time (last fetch attempt that persisted), which is semantically distinct from per-row `fetched_at` (URL resolve time) and survives an empty-but-attempted window. Files: `store.py` (State + write_window + `_state_dict`), possibly `batch.py` (no change if it flows through `write_window`).
   - Acceptance: after a batch window persists, `load_state(handle).last_analyzed_at` is set; `list_reels` reflects it; old state YAMLs without the field load with `None` (backward-compatible default).
   - Parallel-safe with: Step 4 can stub the field read while this lands, but must integrate before tests in Step 8.

6. **Unwire `list_reels` from the tool wrapper, FetchGate, and `fresh_fetch`** — in `mcp_server.py::list_reels`: drop the `fresh_fetch` parameter; stop passing it; ensure the call does NOT touch `ctx.gate`/`get_gate` (confirm `list_reels` never referenced the gate directly and the removed `fetch_window` path was the only consumer). Rewrite the docstring to describe read-only analyze-then-serve semantics (typed not-analyzed error, staleness meta, "never a metered path"). Keep the outer try/except typed-envelope fallback. Files: `mcp_server.py`.
   - Acceptance: `list_reels` tool signature no longer has `fresh_fetch`; docstring makes no claim of fetching/filling/cooling; `ServerContext.gate` is untouched by the `list_reels` path; other three tools unchanged.
   - Parallel-safe with: none (depends on Steps 1-4 defining the new `run_list_reels` signature).

7. **Record the deliberate contract change against the frozen snapshot** — update the four-tool frozen-surface snapshot/schema test to reflect: `list_reels` loses `fresh_fetch`; response gains the `staleness` block and the not-analyzed `error`/`retryable` fields. Add an inline note (test comment or the decision's consequences) that this is an intentional evolution of the frozen surface tied to the CQRS decision, not drift. Files: the snapshot test module (e.g. `tests/test_mcp_server*.py` / snapshot fixture). See Consumer Inventory / Versioning below.
   - Acceptance: the snapshot test passes against the new surface and would FAIL if `fresh_fetch` reappeared or the `staleness` block regressed; the diff is a single intentional update with a justifying comment.
   - Parallel-safe with: Step 8 (tests) — same test tier.

8. **Tests: zero-IG-request assertion, not-analyzed, analyzed serve+ranking, staleness fields** — add/adjust tests: (i) **zero-IG guard** — inject a spy/no-network client (or monkeypatch `AnonymousClient` / the HTTP layer to raise on any call) and assert `list_reels` makes ZERO IG requests in BOTH not-analyzed and analyzed cases; (ii) **not-analyzed typed error** — empty/unanalyzed handle returns `partial=False`, `retryable=False`, `error_kind`/note naming `start_batch_fetch`, no raise; (iii) **analyzed instant serve + ranking** — seeded store returns correct top-N by `play_count` desc over the deduped pool with numeric `media_id` ordering (not positional); (iv) **staleness fields** — assert `last_analyzed_at`, `store_count`/`scan_depth_target`, `signed_url_maybe_expired` present and correct for fresh vs aged `fetched_at`. Delete/repurpose now-obsolete network-path tests for `list_reels` (top-check/deepen/partial-cooling) — move any still-relevant coverage assertions to the batch runner tests if they validate shared machinery. Files: `tests/test_list_reels*.py` (+ fixtures).
   - Acceptance: all four behaviors covered and green; the zero-IG test fails if a fetch is reintroduced; no `list_reels` test exercises a network path; `download_reel`/`batch` tests remain green and unchanged.
   - Parallel-safe with: Step 7.

9. **Docs: README + CLAUDE.md analyze-then-serve semantics** — update README's `list_reels` section to: read-only over the store, must `start_batch_fetch` first (typed not-analyzed error), staleness meta block (`last_analyzed_at`, store-count-vs-90 informational hint, signed-URL-maybe-expired), and that `list_reels` is **no longer a metered path**. Update CLAUDE.md: adjust the Architecture line ("`list_reels` calls it synchronously" → `list_reels` is read-only over the store; only `start_batch_fetch` and `download_reel`'s re-resolve are metered), and the Politeness invariant's mention of `list_reels`. Files: `README.md`, `CLAUDE.md`.
   - Acceptance: neither doc describes `list_reels` as fetching/filling/metered; both describe the typed not-analyzed error + staleness meta; the metered-paths list names only `start_batch_fetch` and `download_reel` re-resolve.
   - Parallel-safe with: Steps 7, 8.

## Test Strategy

- **Zero IG requests (the headline invariant)** — unit with a network-poisoned client (monkeypatch the HTTP layer to raise on any request). Assert `run_list_reels` completes for BOTH an unanalyzed and an analyzed seeded handle with zero requests recorded. This is the acceptance-criteria #4 gate.
- **Not-analyzed typed error** — unit. Empty store + no state → returns dict with `partial=False`, `retryable=False`, a stable `error_kind`/`error` discriminator, `note` naming `start_batch_fetch`; asserts NO exception (never-raise).
- **Analyzed instant serve + ranking over the pool** — unit with a seeded CSV containing duplicate shortcodes and out-of-order rows. Asserts top-N is deduped per-shortcode and ordered by `sort_by` (default `play_count` desc), with tie/order behavior anchored on numeric `media_id`, NOT CSV row order.
- **Staleness metadata** — table-driven unit over fresh vs aged `fetched_at` rows and present/absent `last_analyzed_at`: asserts each `staleness.*` field's value, and that `store_count`/`scan_depth_target` are reported but do NOT flip `coverage.complete`.
- **Three-state boundary** — unit: (a) no coverage → error; (b) 1 reel, non-contiguous → serves with `complete=False`; (c) contiguous-to-depth → serves with `complete=True`. Guards the readiness-vs-informational separation.
- **Frozen surface snapshot** — schema/golden test updated deliberately; asserts `fresh_fetch` gone and `staleness` block present; fails on regression in either direction.
- **Regression guard** — run existing `download_reel` + `batch` suites unchanged to confirm no collateral change to the remaining metered paths or the shared `coverage`/`store` machinery.

### Consumer Inventory

| Consumer | Surface used | Breaking? | Migration action | Owner |
|---|---|---|---|---|
| MCP client (LLM agent) | `list_reels` tool params + response | Yes | Stop passing `fresh_fetch`; handle the typed not-analyzed error by calling `start_batch_fetch` first; optionally read the new `staleness` block | first-party (this repo) |
| Frozen-surface snapshot test | `list_reels` param + response schema | Yes (intentional) | Update snapshot to the new surface with a justifying comment | first-party (this repo) |
| `download_reel` / `start_batch_fetch` | shared `store` / `coverage` machinery | No | none — behavior unchanged | first-party (this repo) |

### Versioning Policy

- **Current policy:** none formal — the "frozen four-tool MCP surface" decision is the de-facto contract, enforced by a snapshot test.
- **This change classification:** breaking (a param is removed; the response schema gains a required-shaped `staleness` block and a new error variant).
- **Justification:** removing the `fresh_fetch` parameter and changing `list_reels` from fetch-capable to read-only alters both the request and response contract; per the overlay's discipline, a removed parameter and changed behavior are breaking even for an internal consumer. This is sanctioned by the accepted CQRS decision, which explicitly calls it a contract change.

### Deprecation Timeline

- **Announcement date:** at merge — the governing decision doc + the ticket already record intent; no external partners to pre-notify.
- **Dual-support window:** none — hard split, no escape hatch. `fresh_fetch` is removed outright rather than deprecated-then-removed (a single-consumer, single-repo surface with the semantics explicitly rejected by the decision; keeping a dead flag would invite the exact coupling being removed).
- **Removal date:** same as merge (immediate).
- **Sunset signal:** the typed not-analyzed error's `note`/`error_kind` is the runtime signal that steers a consumer to `start_batch_fetch`; the frozen-snapshot test is the compile-time signal.

### Contract Tests

- **Existing coverage:** the four-tool frozen-surface snapshot test (names + param schemas); per-tool behavior tests.
- **Coverage gap closed by this work:** the zero-IG-request assertion for `list_reels`, the not-analyzed error contract, and the `staleness` block schema — all new.
- **Test type:** schema/golden snapshot for the surface; behavioral unit tests for the envelope variants.

### Communication Plan

- **Pre-release:** governing decision doc (already accepted) + the dedicated ticket.
- **At release:** README + CLAUDE.md updates (Step 9) serve as the changelog/migration guide; commit via the `conventional-commits` skill referencing the decision.
- **Post-release:** none automated (solo project) — the snapshot test is the guard that the old shape does not silently return.

## Out of Scope

- **Any change to `start_batch_fetch` / the async runner's fetch logic** — it remains the analysis command; only a possible `last_analyzed_at` stamp via `write_window` (Step 5) touches its neighborhood, and only if D1 picks the state-field option.
- **`download_reel`'s >24h re-resolve (#13)** — unchanged; remains the only sync metered path.
- **A new "serve, fetching if unknown" convenience tool** — explicitly deferred by the decision's revisit trigger; NOT built here.
- **Changing `scan_depth` semantics, ranking algorithm, or the coverage-segment model** — reused as-is; no algorithmic change.
- **Removing `coverage.segment_to_deepen` / `has_more_to_fetch` from `coverage.py`** — leave them (still used by the batch/deepen path); only `list_reels`' calls into them go away.
- **Introducing a config knob to re-enable fetching on `list_reels`** — forbidden by the hard-split decision (no escape hatch).

## Risks

- **Hidden gate/network coupling remains after excision** — likelihood low, impact high (breaks the headline invariant). Mitigation: the zero-IG-request test (Step 8) is the definitive guard; also `grep` the module for fetch/gate symbols (Step 1 acceptance).
- **`last_analyzed_at` semantics ambiguity (analysis-time vs URL-resolve-time)** — likelihood medium, impact medium (misleading staleness hint). Mitigation: Decision D1 + a docstring defining the field as "last window that persisted analysis"; test both fresh and aged cases.
- **Mis-gating state (a) vs (b): treating a shallow-but-analyzed handle as not-analyzed (or vice-versa)** — likelihood medium, impact high (spurious errors or serving an unfetched handle empty). Mitigation: explicit three-state boundary test (Step 8) and the `_has_been_analyzed` helper keyed on coverage evidence, not `scan_depth` count.
- **Frozen-snapshot test drift vs intentional change confusion** — likelihood medium, impact low. Mitigation: single intentional snapshot update with a justifying comment tied to the decision (Step 7).
- **Backward-compat of old state YAML files** — likelihood low, impact medium (load error on pre-existing stores). Mitigation: `last_analyzed_at` defaults to `None` on load; test loading a field-less state.
- **Deleting network-path tests loses coverage of shared coverage machinery** — likelihood low, impact medium. Mitigation: migrate any still-load-bearing coverage assertions to the batch-runner suite rather than dropping them (Step 8).

## Open Questions

- **Decision D1 — `last_analyzed_at` sourcing (blocks Step 5, and Step 4's field value):** (A) add `last_analyzed_at` to `State`, stamped in `store.write_window`; vs (B) derive `max(fetched_at)` across CSV rows at read time. **Recommendation: A** — it records genuine analysis time (survives an empty-but-attempted window, distinct from per-row URL-resolve time) at the cost of a small, backward-compatible schema bump. Choose before Step 5; Steps 1-4 do not block on it.
- **Not-analyzed discriminator shape:** a dedicated `error_kind: "not_analyzed"` field vs an `error`-string prefix convention. Recommendation: add `error_kind` for machine-branchability while keeping `error`/`note` human-readable — confirm it does not collide with the download tool's error contract. Blocks the exact envelope keys in Step 3.
- **`signed_url_maybe_expired` scope:** computed over the served top-N rows only, or over the whole pool? Recommendation: over the served rows (that is what the consumer will download). Confirm during Step 4.

## Handoff Notes

This artifact targets `consumer_role: implementation`. The consumer should:
- Honor the step order — Step 1 (excision) and Step 2 (three-state branch) are foundational; Steps 3-4 build the envelopes; Step 5 resolves D1; Steps 6-9 unwire, snapshot, test, and document.
- Resolve **Decision D1** (and the two lesser open questions) before/at Step 5 — do not leave `last_analyzed_at` sourcing implicit.
- Treat "Out of Scope" as hard — especially: no new convenience fetch tool, no escape-hatch config, no `download_reel`/runner behavior changes.
- Treat the test strategy as the minimum — the zero-IG-request assertion is the non-negotiable acceptance gate (criteria #4).
- Honor the `public-api-change` overlay sections — the Consumer Inventory, breaking classification, and the deliberate frozen-snapshot update are required acceptance criteria, not advisory. The removed `fresh_fetch` param must be a real removal, not a silently-ignored no-op.
- Re-plan if discovery shows the FetchGate or a shared helper is entangled with `list_reels` in a way that makes excision touch ≥ 2 additional modules.
