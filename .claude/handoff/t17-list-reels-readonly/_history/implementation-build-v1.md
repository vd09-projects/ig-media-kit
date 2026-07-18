---
artifact_type: handoff
artifact_version: 2
producer_role: implementation
consumer_role: review
plan_type: build
slug: t17-list-reels-readonly
scope_hint: Pivot list_reels to READ-ONLY over the store (CQRS hard split)
canonical_name: implementation-build
overlays: public-api-change
status: draft
version: 1
created: 2026-07-18T12:39:19Z
updated: 2026-07-18T12:39:19Z
prior_versions: []
---

# Build summary: Pivot list_reels to READ-ONLY over the store (CQRS hard split)

**Overlays:** public-api-change

`list_reels` (`run_list_reels`) is now a pure READ-ONLY query over the local store: it resolves params → validates → loads state → branches on a three-state readiness gate into {typed `not_analyzed` error | served ranked top-N + `staleness` block}, and issues ZERO IG requests on every path. The IG-fetch path that used to live in `run_list_reels` was **extracted intact into a new `fill.py::run_fill`** — a forced, discovered deviation (see below) — because the async batch runner reused `run_list_reels` as its only fetch engine; `batch._fill_handle` was re-pointed to `run_fill` with byte-identical fetch behavior. All 177 tests pass (12 new/rewritten across `test_list_reels.py`, new `test_fill.py`, `test_mcp_server.py`, `test_download.py`); the offline smoke harness exits 0 with the zero-IG guard intact.

## Forced deviation from the plan (discovered coupling — needs review ratification)

The plan's Step 1 said to **delete** `run_list_reels`' network path, and Out-of-Scope fenced `start_batch_fetch`/the async runner as untouchable. On contact with the code these are contradictory: **`batch._fill_handle` used `run_list_reels(..., fresh_fetch=False)` as its ONLY per-unit fetch primitive** (the plan's own "Existing Code Shape" missed this; `window.py::run_window` is TOP_SCAN-only and unreferenced). Deleting the path outright would have silently broken the batch runner — a P0 regression against the plan's own hard invariant that "the async runner is the only writer that advances coverage."

This is exactly the plan's Handoff-Note re-plan trigger ("excision touches ≥2 additional modules"). Resolution taken — the only one that satisfies BOTH hard invariants and is the literal realization of the governing CQRS decision ("command = analyze/fetch, query = serve"):

- The network path (top-check + deepen + `PageBudget` + `_run_deepen` + coverage compose + `_compose_note`/`_cooling_note`) was **relocated** verbatim from `list_reels.py` into a new module **`fill.py::run_fill`** — the command-side fetch primitive.
- `batch._fill_handle` was re-pointed from `run_list_reels` to `run_fill` (a two-line import + call-site change). The runner's loop / gate / cooldown / checkpoint / stall logic is **unchanged**; the envelope contract it reads (`error` / `coverage.complete` / `stop_reason` / `partial` / `pages_fetched`) is preserved identically. All existing `test_batch.py` pass unchanged.
- Review-refinement #3 (`_compose_note` "goes dead") is therefore reframed: it is NOT dead — it moved with the network path to `fill.py` and is still reachable there; `list_reels.py` got a new, simpler read-only `_serve_note` (only the contiguous/converging branches).

Net blast radius grew from "1 tool" to: `list_reels.py` (read-only) + new `fill.py` + `batch.py` (call-site re-point) + `download.py` (D2 fields) + `store.py` (D1) + `mcp_server.py` (param drop) + 2 probes + docs. **Recommend the reviewer explicitly ratify the `fill.py` extraction + batch re-point as within the CQRS decision's intent.**

## Resolved decisions honored

- **D1** — added `State.last_analyzed_at: int | None`, stamped in `store.write_window` on EVERY window persist (incl. empty windows), read-only in `list_reels`; old state YAMLs load it as `None` (test: `test_staleness_last_analyzed_at_none_on_legacy_state`). `run_fill` threads its `now` into `write_window` so the stamp respects the batch's injected clock (caught + fixed a latent wall-clock bug via `test_fill_stamps_last_analyzed_at`).
- **D2** — uniform, machine-branchable error contract: `list_reels` errors carry `error`/`error_kind`/`retryable` (`not_analyzed`, `invalid_params`; wrapper fallback `internal_error`). `download.py::_error` backfilled with `error_kind` (`not_in_store`/`aged_out`/`download_failed`) + `retryable=False`; `_partial` gains the symmetric `error_kind="rate_limited"` + `retryable=True`. Deliberate contract change asserted in `test_download.py`.
- **D3** — `signed_url_maybe_expired` computed over the SERVED top-N rows only (oldest served `fetched_at` vs a 36 h TTL; `None` when unknown).

## Review refinements absorbed

1. Error-contract symmetry — done (D2, both tools).
2. `staleness` presence is DETERMINISTIC: ALWAYS on the served envelope, ALWAYS absent on both error envelopes (not-analyzed + invalid-params). Documented in `list_reels.py` and asserted in `test_mcp_server.py::test_list_reels_response_contract_changed_deliberately`.
3. `_compose_note` handled (see deviation section — relocated, not deleted).
4. D1 landed BEFORE the staleness tests (State field implemented first).
5. Explicit assertion that the NOT-ANALYZED path records ZERO network calls specifically — `test_zero_ig_on_not_analyzed_error_path` (network-transport poison, hit count == 0).

## Files modified

- `src/ig_media_kit/list_reels.py` — rewritten read-only: three-state readiness (`_has_been_analyzed`, keyed on coverage evidence not scan_depth count), `_served_envelope` + `_staleness` + `_error_envelope` + `_serve_note`; no network imports (grep-clean).
- `src/ig_media_kit/fill.py` — **NEW**: relocated command-side fetch primitive `run_fill` + `PageBudget`/`_run_deepen`/`_envelope`/`_compose_note`/`_cooling_note`.
- `src/ig_media_kit/store.py` — `State.last_analyzed_at`; `write_window` stamps it (new `now` kwarg); load (backward-compat `None`) + atomic-write persistence.
- `src/ig_media_kit/batch.py` — re-point `_fill_handle` to `run_fill` (import + call site + docstrings). Fetch logic unchanged.
- `src/ig_media_kit/download.py` — `error_kind`/`retryable` on `_error` (3 call sites) + `_partial`.
- `src/ig_media_kit/mcp_server.py` — dropped `fresh_fetch` param from the `list_reels` tool; rewrote docstring (read-only analyze-then-serve); fallback envelope gains `error_kind`/`retryable`.
- `probe/probe_smoke.py`, `probe/probe_download.py` — re-point the metered discovery / mid-fetch-401 step to `run_fill`.
- `README.md`, `CLAUDE.md` — `list_reels` read-only + metered-paths list (only `start_batch_fetch` + `download_reel` re-resolve); staleness/typed-error semantics.
- Decision doc + `decisions/INDEX.md` — left correct in the working tree (already present; not committed, per orchestrator).

## Tests added / changed

- `tests/test_list_reels.py` — REWRITTEN: zero-IG on both served + not-analyzed paths, typed not-analyzed error, ranking over deduped pool (non-positional), staleness fields (fresh/aged/legacy-None), three-state boundary incl. segments-present/empty-pool edge, uniform validation errors.
- `tests/test_fill.py` — **NEW**: migrated network-path coverage (serve short-circuit, cold-start fill + high_water, budget cap, never-sleep, stop-signal partials in top-check + deepen, header provenance, last_analyzed_at stamp).
- `tests/test_mcp_server.py` — frozen surface drops `fresh_fetch`; new deliberate response-contract test (served staleness present / error fields present / staleness absent on error).
- `tests/test_download.py` — assert new `error_kind`/`retryable` on aged-out, cooldown-partial, unknown-shortcode.

## Quality gate

- `pytest`: **177 passed** (was 168; +12 new/rewritten, net after deletions).
- Byte-compile clean across all `src`/`tests`/`probe`; deprecation-strict run clean. No ruff/mypy configured or installed in this env (pytest is the project's only dev tool) — byte-compile used as the lint surrogate.
- Offline smoke harness (`python -m probe.probe_smoke`): exit 0, zero-IG guard held.
- Invariants verified: `list_reels.py` has ZERO `AnonymousClient|fetch_window|resolve_user_id|PageBudget|_run_deepen` references (grep acceptance); anonymous-only preserved (the last interactive IG path is gone); store never destructively capped (ranking over full deduped pool unchanged); ranking keys on per-shortcode dedupe + numeric ordering, never positional.

## Discovered follow-ups

1. **Duplicated param helpers** — `_Params` / `_resolve_params` / `_validate` are duplicated between `list_reels.py` (query) and `fill.py` (command). Marked `# DUP:` in `fill.py`; extract to a shared `ig_media_kit/params.py` so the param contract has one home.
2. **`window.py::run_window` is dead** — TOP_SCAN-only, now referenced by nothing after the batch re-point (batch loops `run_fill`). Either retire `window.py` or route the batch through it once it can deepen. Its existing `# TODO`/`(tracked: #8)` marker remains; `batch.py`'s TODO was updated to name `run_fill`.

## Terminal state

Ready for review — recommend multi-perspective-review (medium-large scope; a public-api-change contract; a forced module extraction + a touch of the fenced batch runner that needs reviewer ratification).
