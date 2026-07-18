---
artifact_type: handoff
artifact_version: 2
producer_role: review
consumer_role: implementation
plan_type: findings
slug: t17-list-reels-readonly
scope_hint: "Pivot list_reels to READ-ONLY over the store (CQRS hard split) — Code review (iteration 1)"
canonical_name: review-findings
overlays: public-api-change
status: draft
version: 2
created: 2026-07-18T10:45:53Z
updated: 2026-07-18T12:48:21Z
prior_versions:
  - _history/review-findings-v1.md
---

# Review findings: Pivot list_reels to READ-ONLY over the store (CQRS hard split) — Code review (iteration 1)

**Owner:** vd
**Overlays:** public-api-change

Code review of the implemented T17 CQRS hard split (`implementation-build.md` v1) against the merge base `main`. The panel read `git diff main` (committed + uncommitted) plus the two untracked NEW files (`src/ig_media_kit/fill.py`, `tests/test_fill.py`). The reviewer independently re-ran the full suite (**177 passed**, 1.32s) and the affected subset (84 passed), and verified every load-bearing claim by direct source inspection + grep, not by trusting the build note.

## Triage Decision

Scope: **large** (list_reels ~410 lines rewritten; new `fill.py` ~340 lines; new `test_fill.py`; 16 files; a cross-cutting error/response-contract change).
Partition: **backend** (Python).
Memory overrides: `always_include` Reliability / Rate-Limit Reviewer (IG-hitting path relocated); `always_exclude` Accessibility (no UI). Custom rule fired: change touches an IG-hitting path → Reliability reviewer active, scope forced ≥ medium.

Selected reviewers:
- Reliability / Rate-Limit Reviewer (backend) — the metered fetch path was physically relocated; verify pacing/cap/partial/no-poll/header preserved.
- API & Contract Reviewer (backend) — response-schema evolution (`staleness`), uniform `error_kind`/`retryable`, `fresh_fetch` removal.
- Backward Compatibility Reviewer (common) — removed public param, new `State` field, frozen-snapshot updates.
- Domain Logic Reviewer (backend) — the three-state readiness gate keyed on coverage evidence.
- Data Integrity & Migration Reviewer (backend) — `State.last_analyzed_at` YAML round-trip + legacy-None default.
- Test Coverage Auditor (common) — the zero-IG poison tests are the acceptance gate.
- Ripple Effect Analyst (common) — batch re-point + download backfill + probe re-point.
- Error Handling & Resilience Inspector (common) — never-raise envelope, typed errors.
- Security & Trust Reviewer (common) — anonymous-only invariant preserved.
- Tech Debt Sentinel (baseline) — DUP param helpers, dead `window.py`.
- Naming & Clarity Guardian (baseline).
- Documentation Reviewer (common) — README + CLAUDE.md.

Skipped: Performance & Scalability Critic (no new hot loop; read path is one CSV scan, unchanged), Concurrency & State Safety (gate/threading untouched by this diff), Observability, Dependency & Coupling (no new deps), Infra, all Frontend reviewers.

---

## Reliability / Rate-Limit Reviewer — APPROVE (HIGH)

**Voice:** the shared IP is the asset; every metered byte is borrowed. Verdict: the split *strengthens* politeness rather than weakening it.

- **VERIFIED — list_reels can no longer emit a cooldown at all.** `list_reels.py` imports neither `.fetch` nor `.http_client` (grep-clean at the module level, confirmed). It constructs no `AnonymousClient`, calls no `fetch_window`/`resolve_user_id`. The "never sleeps" invariant is now *fully* true — the last interactive metered path is gone. This is exactly the reliability win the governing decision promised.
- **VERIFIED — the relocated primitive is byte-identical on the fetch axis.** `fill.py::run_fill` is a verbatim lift of the old `run_list_reels` network path: same `PageBudget` (cap `max_pages_per_call`), same `reserve_deepen` (≥1 page held for deepen), same `sleep=None` on both `fetch_window` calls (SYNC PATH: never sleeps), same first-`stop_signal`-aborts-the-whole-unit control flow (deepen page left unspent on a top-check stop). `test_fill.py` migrates the full politeness battery and all pass: `test_stop_signal_in_topcheck_aborts_whole_call` (partial, `pages_fetched==1`, `len(calls)==2` = profile + ONE feed page, `stop_reason=="rate_limited"`), `test_budget_cap_across_both_phases` (≤4 pages), `test_never_sleeps` (monkeypatches `time.sleep` to throw), `test_x_ig_app_id_on_every_api_call` (asserts `x-ig-app-id` on EVERY call AND no `authorization` header).
- **VERIFIED — only two paths hit IG now**, both still gated/paced: the async batch runner (`fill.run_fill` under the `FetchGate`, the only sleeper) and `download_reel`'s >24 h re-resolve. Documented accurately in CLAUDE.md and README.
- **FYI (non-blocking):** the sole non-verbatim change in the relocation is that `run_fill` threads `now=now` into `store.write_window` (three call sites) where the old `run_list_reels` did not. This does NOT alter any fetch semantic — it only feeds the new additive `last_analyzed_at` stamp (D1). The build note frames this as fixing a latent wall-clock bug; that is accurate and an improvement, not a regression.

## API & Contract Reviewer — APPROVE (HIGH)

- **VERIFIED — uniform, machine-branchable error contract.** `list_reels` errors carry `error` + `error_kind` (`not_analyzed` | `invalid_params`) + `retryable=False`; the mcp_server wrapper fallback adds `error_kind="internal_error"` + `retryable=False`. `download._error` was backfilled with `error_kind` (`not_in_store` | `aged_out` | `download_failed`) + `retryable=False`, and `_partial` gained the retryable sibling `error_kind="rate_limited"` + `retryable=True`. A consumer can now branch on `retryable` uniformly across both tools instead of inferring it from `partial`. This resolves the exact contract asymmetry the *plan review* (v1) flagged as its top suggestion — good closure.
- **VERIFIED — `staleness` determinism.** Present on EVERY served envelope (`_served_envelope` always includes the key), absent on EVERY error envelope (`_error_envelope` deliberately omits it — both not-analyzed and invalid-params). Asserted from three angles: `test_not_analyzed_returns_typed_error` (`"staleness" not in env`), `test_invalid_sort_by_returns_clean_typed_error` (same), and `test_list_reels_response_contract_changed_deliberately` at the MCP boundary.
- **VERIFIED — `signed_url_maybe_expired` scope.** Computed over the SERVED top-N rows only (`min(fetched_at)` of served reels vs the 36 h TTL), `None` when nothing served or no served row carries `fetched_at`. Correct: that is precisely the set the consumer would download. Tests cover fresh (False), aged (True), and unknown/legacy (None).
- **FYI:** `partial`/`pages_fetched`/`stop_reason` are now constant (`False`/`0`/`None`) on the read-only served envelope, retained only to keep the served/error/fallback shapes a stable superset. This is documented in the `_served_envelope` docstring. Defensible for shape stability, but a downstream reader may find a `pages_fetched` on a read-only tool confusing — worth one line in release notes.

## Backward Compatibility Reviewer — APPROVE (HIGH)

- **`fresh_fetch` param removed from the `list_reels` MCP tool** — a genuine breaking change to the frozen public surface. It is deliberate, governed by the accepted decision, and correct (a "force fresh top-check" flag is meaningless once the tool never fetches). The frozen snapshot `EXPECTED_SURFACE` in `test_mcp_server.py` was updated *with an explicit comment* stating the snapshot must FAIL if `fresh_fetch` ever reappears — this is the right way to make a deliberate break auditable. The `public-api-change` overlay is active. Callers still passing `fresh_fetch` will get a FastMCP unknown-param error; call this out in release notes (the README already documents the new behavior).
- **`State.last_analyzed_at` added** — additive, keyword-defaulted `None`, loaded via `_as_int(data.get("last_analyzed_at"))` so pre-T17 YAMLs deserialize cleanly. `test_staleness_last_analyzed_at_none_on_legacy_state` writes a legacy YAML with the key removed and proves `load_state(...).last_analyzed_at is None` and the handle still serves. Backward-compatible. ✓
- **`download._error` signature** — `error_kind` is now a keyword-only arg with NO default. I grep-confirmed exactly 3 call sites, all in `download.py`, all updated; `_error`/`_partial` are module-private, so no external caller can be broken. A missed caller would have been a `TypeError` at runtime; there are none.

## Domain Logic Reviewer — APPROVE (HIGH)

- **VERIFIED — readiness keys on coverage EVIDENCE, not the count-vs-90 figure.** `_has_been_analyzed(state, pool_depth)` returns `pool_depth > 0 OR bool(state.coverage_segments) OR state.high_water_media_id is not None`. `scan_depth` never enters this gate. `complete` is a *separate* computation (`coverage.is_contiguous`). `store_count` vs `scan_depth_target` lives only in the informational `staleness` block. `test_staleness_informational_hint_does_not_flip_complete` nails this: `store_count==1`, `scan_depth_target==90`, yet `coverage.complete is True` because the single segment is terminal.
- **VERIFIED — all three states + the tricky edge.** (a) empty/untouched → `not_analyzed` error; (b) one shallow non-terminal reel → serve, `complete=False`; (c) contiguous → serve, `complete=True`. Plus `test_boundary_b_segments_present_empty_pool_serves_empty`: coverage evidence present but pool empty (a window persisted 0 rows) is correctly treated as ANALYZED → serves an empty ranked list, NOT the not-analyzed error. This is the subtle case that a naive `pool_depth>0` gate would get wrong; the OR-of-evidence gate gets it right.
- **FYI (no change needed):** a cold-start `resolve_user_id` 401 in `run_fill` persists an empty window (stamping `last_analyzed_at`) but writes no segments/high_water, so `_has_been_analyzed` still returns False and `list_reels` still says not-analyzed. That is the correct steer (nothing was actually fetched); and because the error envelope carries no `staleness`, the stamped-but-empty `last_analyzed_at` is never surfaced inconsistently. Clean.

## Data Integrity & Migration Reviewer — APPROVE (HIGH)

- `last_analyzed_at` is stamped by `write_window` on EVERY persist including a zero-row window (records *analysis* time, distinct from a CSV row's per-row `fetched_at`), and round-trips through `_write_state_atomic` (added to the serialized dict). Atomic tmp-then-rename write is unchanged. The one-writer / read-only-reader split (only `write_window` writes it; `list_reels` only reads) is clean and documented. No migration script needed — absence loads as `None`.

## Test Coverage Auditor — APPROVE (HIGH), with the top suggestion

- **VERIFIED — the zero-IG acceptance gate is real on BOTH paths.** `_poison_network` monkeypatches `http_client._default_transport` to a `_boom` that increments a hit counter and raises. `AnonymousClient.__init__` does `self._transport = transport or _default_transport()`, so any no-transport client construction inside list_reels would fire `_boom`. `test_zero_ig_on_analyzed_serve_path` and `test_zero_ig_on_not_analyzed_error_path` both assert `hits["n"] == 0` — the not-analyzed error path is covered *specifically* (refinement #5), so "not analyzed" can never be silently satisfied by a failed fetch attempt. The migration of the network-path tests to `test_fill.py` is faithful and complete.
- **SUGGESTION (top, non-blocking) — machine-enforce the import boundary in CI.** The zero-IG invariant currently rests on (1) two runtime poison tests that each exercise ONE branch, and (2) a manual build-time grep. The grep is not in CI. A future refactor that imports `fetch_window` into `list_reels` and calls it on some *third*, untested branch would slip past both poison tests. Add a tiny static test asserting `list_reels` imports neither `ig_media_kit.fetch` nor `ig_media_kit.http_client` (AST parse of the module source, or assert the modules are absent from its import graph). That converts the grep-acceptance into a permanent regression tripwire — the strongest guarantee for the invariant that defines this whole change.

## Ripple Effect Analyst — APPROVE (HIGH)

- Blast radius traced and each edge verified: `batch._fill_handle` re-pointed `run_list_reels`→`run_fill` (import + call site; dropped the now-gone `fresh_fetch=False` arg) — `test_batch.py` green unchanged; the envelope keys the runner reads (`error`/`coverage.complete`/`stop_reason`/`partial`/`pages_fetched`) are preserved identically. `probe_smoke.py` + `probe_download.py` re-pointed to `run_fill`. `mcp_server` fallback updated. Only functional `run_list_reels` caller remaining is `mcp_server.py:175` (no removed params). No orphaned call site.

## Error Handling & Resilience Inspector — APPROVE (HIGH)

- `run_list_reels` never raises: validation and not-analyzed both return typed envelopes; the mcp_server `except Exception` fallback now also carries `error_kind`/`retryable`. `test_all_four_tools_never_raise_when_run_throws` still guards the boundary. The error taxonomy is coherent (retryable only for the rate-limited cooldown; every terminal/unknown condition is `retryable=False`).

## Security & Trust Reviewer — APPROVE (HIGH)

- Anonymous-only invariant intact: no login/cookie/session/account/token introduced or referenced anywhere in the diff. The `x-ig-app-id` header remains solely owned by `AnonymousClient`, and `test_x_ig_app_id_on_every_api_call` additionally asserts no `authorization` header is ever set. Removing the interactive fetch path strictly reduces the attack/rate-limit surface. No credentials or personal-identity linkage.

## Tech Debt Sentinel — APPROVE (HIGH), two tracked debts

- **DUP (tracked, follow-up #1):** `_Params` / `_resolve_params` / `_validate` are now duplicated verbatim between `list_reels.py` (query) and `fill.py` (command), flagged with a `# DUP:` marker pointing at a future `ig_media_kit/params.py`. Real drift hazard: a param/validation change applied to one module but not the other would let the query and the batch fill disagree on what a valid request is. Risk is currently low (sort validation is shared via `ranking.SORT_WHITELIST`; only the plumbing is copied), but the follow-up should land before the next param change. Acceptable as filed debt.
- **Dead code (tracked, follow-up #2):** `window.py::run_window` is now fully unreferenced (the batch loops `run_fill`). The batch TODO was updated to name `run_fill`. Retire `window.py` or route the batch through it — do not let two divergent compose paths linger.

## Naming & Clarity Guardian — APPROVE (HIGH)

- `run_fill` / `_has_been_analyzed` / `_served_envelope` / `_error_envelope` / `SIGNED_URL_TTL_SECONDS` / `ERROR_KIND_*` all read true to intent. Module + function docstrings are unusually good — the CQRS command/query framing is stated at the top of both `list_reels.py` and `fill.py`, and the "keyed on evidence not the count" rationale is inline where it matters.

## Documentation Reviewer — APPROVE (MED)

- README tool table + CLAUDE.md Architecture/Invariants both rewritten accurately: `list_reels` READ-ONLY, the two-metered-paths list, the typed `not_analyzed` steer, the `staleness` block, and the download `error_kind`/`retryable` pair. The decision doc is present in the working tree.
- **Nit (non-blocking):** `src/ig_media_kit/fetch_gate.py:7` still reads "Every batch ``run_list_reels`` / ..." in its module docstring — the batch no longer routes `run_list_reels` through the gate, it routes `run_fill`. Stale comment only; update opportunistically with follow-up #1/#2.

---

## Review Summary

| Reviewer | Verdict | Blocking | Suggestions | FYI | Confidence |
|---|---|---|---|---|---|
| Reliability / Rate-Limit | APPROVE | 0 | 0 | 1 | HIGH |
| API & Contract | APPROVE | 0 | 0 | 1 | HIGH |
| Backward Compatibility | APPROVE | 0 | 1 | 0 | HIGH |
| Domain Logic | APPROVE | 0 | 0 | 1 | HIGH |
| Data Integrity & Migration | APPROVE | 0 | 0 | 0 | HIGH |
| Test Coverage Auditor | APPROVE | 0 | 1 | 0 | HIGH |
| Ripple Effect Analyst | APPROVE | 0 | 0 | 0 | HIGH |
| Error Handling & Resilience | APPROVE | 0 | 0 | 0 | HIGH |
| Security & Trust | APPROVE | 0 | 0 | 0 | HIGH |
| Tech Debt Sentinel | APPROVE | 0 | 2 | 0 | HIGH |
| Naming & Clarity | APPROVE | 0 | 0 | 0 | HIGH |
| Documentation | APPROVE | 0 | 1 | 0 | MED |

**Overall Recommendation: APPROVE** — 0 blocking findings. The forced deviation (extracting the fetch path into `fill.py::run_fill` and re-pointing `batch._fill_handle`) is explicitly **RATIFIED**: it is behavior-preserving on the fetch axis (verbatim relocation; the only delta is the additive `now`-threading for `last_analyzed_at`), it is the literal realization of the governing CQRS decision (command = fetch/`fill`, query = serve/`list_reels`), and it satisfies both hard invariants (list_reels zero-IG AND the batch runner remains the only coverage-advancing writer). All 177 tests pass on an independent run.

**Rationale:** Every one of the seven scrutiny axes (a–g) is verified by direct source inspection + grep + a fresh test run, not by trusting the build note. Zero-IG holds on both the served and not-analyzed paths with the not-analyzed path asserted specifically; the readiness gate keys on coverage evidence and is cleanly separated from the informational depth hint; the error/response contracts are uniform and their frozen snapshots were updated deliberately with auditable comments; ranking is unchanged (desc over the full deduped pool, non-positional); `staleness` is deterministic and correctly scoped; the new `State` field is backward-compatible; and the anonymous-only invariant is not merely preserved but structurally reinforced. The remaining items are debt and hardening, none gating.

**Blocking Items:** none.

**Top Suggestions:**
1. (Test Coverage) Machine-enforce the zero-IG import boundary: add a static/AST test asserting `list_reels` imports neither `ig_media_kit.fetch` nor `ig_media_kit.http_client`, so a future fetch-import regression is caught in CI rather than only by the build-time grep (the two runtime poison tests each exercise a single branch).
2. (Tech Debt #1) Extract the duplicated `_Params`/`_resolve_params`/`_validate` into `ig_media_kit/params.py` before the next param change, to prevent query/command drift.
3. (Tech Debt #2) Retire or re-route `window.py::run_window` (now fully unreferenced).
4. (Docs) Fix the stale `fetch_gate.py:7` docstring ("batch run_list_reels" → run_fill); note the `fresh_fetch` removal + the vestigial `pages_fetched`/`partial`/`stop_reason` on the read-only envelope in release notes.

**Corroborated Findings (2+ reviewers, highest signal):** the `fill.py` extraction being behavior-preserving-and-ratifiable was independently reached by Reliability, Ripple Effect, and Domain Logic; the deliberate-snapshot discipline (frozen surface + response contract) was praised by both API & Contract and Backward Compatibility.

**Accepted Debt:** follow-ups #1 (DUP → params.py) and #2 (dead `window.py`) are already filed by the build; the panel endorses both and adds the CI import-boundary test as a new hardening item. No timeline gate — none blocks merge.
