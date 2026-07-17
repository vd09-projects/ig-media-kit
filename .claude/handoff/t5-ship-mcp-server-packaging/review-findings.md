---
artifact_type: handoff
artifact_version: 2
producer_role: review
consumer_role: implementation
plan_type: findings
slug: t5-ship-mcp-server-packaging
scope_hint: "T5 — Ship: MCP server wiring, packaging, docs, extensibility, smoke"
canonical_name: review-findings
overlays: public-api-change
status: draft
version: 3
created: 2026-07-16T08:04:20Z
updated: 2026-07-16T11:27:05Z
prior_versions: [review-findings-v1.md, review-findings-v2.md]
---

# Review findings: T5 — Ship: MCP server wiring, packaging, docs, extensibility, smoke

**Owner:** vd

**BUILD / code review — iteration 1.** This is a fresh review of the *implemented* T5 build
(`implementation-build.md` v1) against the working tree on `feat/t5-ship-mcp-server` — not a
re-review of the plan. (Prior versions v1/v2 of this canonical were *plan* reviews.) Material:
`implementation-build.md` v1 + the working-tree diff of `src/ig_media_kit/mcp_server.py`,
`src/ig_media_kit/fetch.py`, `config.yaml`, `README.md`, plus new files
`probe/probe_smoke.py`, `tests/test_mcp_server.py`, `tests/test_product_type_dispatch.py`,
`tests/test_smoke.py`. Every load-bearing signature and import the wiring depends on was
verified against live source; all three changed modules byte-compile clean. `pytest` and the
`mcp` package are not installed in the review sandbox, so the build's "162 passed" claim was
**not** independently re-run — findings rest on source analysis, not a re-executed suite.

## Triage Decision
Scope: **large** (capstone; public tool surface + concurrency/gate wiring + packaging + docs + smoke)
Partition: **backend** (`.py`, `.yaml`; MCP server + fetch engine)
Memory overrides: none (no MPR skill-memory dir present)

Selected Reviewers:
- API & Contract (backend) — the four-tool public surface is frozen/renamed; envelopes are the contract
- Concurrency & State Safety (backend) — one process-wide gate, divergent-store rejection, startup resume ordering
- Error Handling & Resilience (common) — never-raise envelopes on all four tools; partial-vs-typed-error
- Security & Trust (common) — anonymous-only, SSRF/callback guard, smoke test seam
- Test Coverage Auditor (common) — 17 new tests across surface/gate/product_type/smoke
- Backward Compatibility (common) — `top_reels` removed, `batch_fetch` → `start_batch_fetch`
- Developer Experience (common) — `--config` flag, config precedence, packaging/entry point
- Documentation (common) — README rewrite (4 tools, ~48/window, ~36 h TTL, burner-rejected)
- Tech Debt Sentinel (baseline) — disabled stub markers, deferred gate/cooldown question
- Naming & Clarity Guardian (baseline)

Skipped: Data Integrity/Migration (no schema), Performance Critic (no hot-path change), Observability
(print-line only), Domain Logic (no ranking change), Ripple Effect (changes are localized + covered),
Dependency & Coupling (no new deps — pyproject pins pre-existing).

---

## API & Contract Reviewer — "Your consumers can't read your mind." — HIGH

**Verdict:** LGTM

The frozen four-tool surface is exactly as specified and enforced by a behavioral test, not just
a name snapshot:

- `list_reels` is now wrapped in the same typed never-raise envelope as the other three
  (`mcp_server.py` `list_reels` try/except → typed dict with `handle/reels/partial/coverage/
  pages_fetched/stop_reason/note/error`). The round-1 plan gap (list_reels bare `return`) is
  closed in code. `test_all_four_tools_never_raise_when_run_throws` stubs each `run_*` to raise
  and asserts a `dict` return carrying the failure detail — including `list_reels`.
- `download_reel`'s envelope is preserved end-to-end through the wrapper:
  `test_download_tool_preserves_partial_vs_typed_error` confirms a metered stop stays
  `partial=True, stop_reason=rate_limited` and an aged-out reel stays `partial=False, error=...`
  (the outer backstop does not collapse the typed distinction). Matches the invariant.
- The surface snapshot (`test_four_tool_surface_snapshot`) pins exact tool names + param sets and
  asserts `top_reels`/`batch_fetch` are gone.
- Signatures verified against source: `run_list_reels(..., store=, client=, now=)`,
  `run_download_reel(..., store=)`, `run_start_batch_fetch(..., deps=)`,
  `run_get_batch_status(..., deps=)`, `resume_pending_jobs(config, deps=)` all accept the kwargs
  the new wiring passes. No signature drift.

**Issues Found:** none.

---

## Concurrency & State Safety Reviewer — "Assume concurrent access until proven otherwise." — HIGH

**Verdict:** LGTM on the T5 scope — with one disclosed, out-of-scope tension carried forward (non-blocking).

The one-gate invariant is honored the way the plan-review round-2 prescribed, and the guard is
built for the *right* reason (not the vacuous one):

- `get_gate` is confirmed against `fetch_gate.py` to be an **argument-ignoring process singleton**
  (`_SINGLETON`, docstring "same instance regardless of args"). `mcp_server`'s own comments and
  `ContextMismatch` docstring now state this correctly — the guard is framed as
  **config/store divergence sharing the one startup gate**, not a "second gate" split.
- `_resolve_context` **rejects** a `config_path` whose resolved `store_dir` differs from the
  installed server context (`ContextMismatch` → caught → typed envelope). This is the load-bearing,
  non-vacuous assertion, and `test_divergent_store_dir_config_path_is_rejected` checks the
  envelope shape (`"store_dir" in error`) AND that `current_context()` is untouched.
  `test_same_store_dir_config_path_reuses_server_context` confirms a store-compatible override
  reuses the context and serves from store with zero pages.
- Startup ordering is correct and tested: `main()` → `startup()` installs the context and runs
  `resume_pending_jobs` **before** `mcp.run()`; `test_resume_runs_before_mcp_run` asserts the
  order spy is `["resume", "run"]`. The first `get_gate` call happens inside `startup`'s
  `build_context`, so the singleton's persisted `store_dir/_batch/_gate.json` path is pinned from
  the server config — the persisted cooldown tracks the same store the tools use.

**Carried-forward tension (disclosed follow-up #2, NOT introduced by T5):** the synchronous tools
(`list_reels` / `download_reel`) share the gate *object* (for cooldown state + the divergent-store
guard) but do **not** call `gate.acquire()`. Consequence: a user-driven `list_reels(fresh_fetch=True)`
or a `download_reel` re-resolve can fire an IG metadata window *during an active batch cooldown*,
which the "never poll during a cooldown (it extends it)" invariant warns against. This is real, but:
(a) it is **not** a T5 regression — the sync path never acquired the gate (T2 behavior); (b) T5's
approved plan explicitly scoped "gate/cooldown logic" **Out of Scope**; (c) `list_reels` still
correctly **never sleeps** and self-limits (stops+partials on its own first 401). The honest fix is a
*non-sleeping* cooldown **check** that short-circuits the sync path to a partial — a design question,
not a typo. Because it is out-of-scope-by-plan and honestly disclosed, it does **not block** T5
acceptance; see Tech Debt for the tracking recommendation.

**Issues Found:**
- [SUGGESTION] Reconcile sync-path-fetch-during-cooldown with a non-sleeping gate *check* (short-circuit
  to a partial) in a follow-up; today the disclosure lives only as build note #2 with no code marker.

---

## Error Handling & Resilience Inspector — HIGH

**Verdict:** LGTM

- All four tools terminate in `except Exception ... return {typed dict}` — no path propagates to the
  MCP client. `list_reels`' envelope mirrors its success shape (empty `reels`, `partial=False`,
  zeroed coverage) so a consumer parses failure the same way it parses success.
- The `download_reel` backstop is deliberately a *last resort*: the typed partial-vs-terminal
  distinction is produced inside `run_download_reel` and passes through untouched; the outer
  `except` only catches truly unexpected throws (verified by the preserve test).
- `ContextMismatch` is raised then caught by the same envelope — a divergent store is a *typed
  refusal to serve*, not a crash and not a silent wrong-store write. Correct failure mode.

**Issues Found:** none.

---

## Security & Trust Reviewer — HIGH

**Verdict:** LGTM

- **Anonymous-only honored.** No login/cookie/session/account path anywhere in the diff. The smoke
  harness installs a process-global guard (`http_client._default_transport = _forbid_real_transport`)
  that turns any attempt to build a *real* transport into a hard `AssertionError`, and asserts
  `zero_ig_network` at the end. Zero-IG is enforced, not merely intended.
- **SSRF/callback guard NOT loosened.** The localhost callback receiver is reached through an
  **injected `poster` deps seam**; production `_default_poster` (curl_cffi, https, redirect-off) is
  untouched. `validate_callback_url` still runs unchanged — the test injects a `resolver` that
  returns a public IP so the guard *passes* for the benign `https://smoke.example` URL, then delivery
  is redirected to `127.0.0.1` by the seam. This is a legitimate test seam, not a production guard
  relaxation. Confirmed against `probe_smoke.py` (`_CallbackSink.poster`, `resolver` injection).
- `x-ig-app-id` header delegated to `http_client` (unchanged); no bypass introduced.

**Issues Found:** none.

---

## Test Coverage Auditor — HIGH

**Verdict:** LGTM

17 new tests land on every T5 acceptance criterion:
- Surface: exact name+param snapshot + explicit "top_reels/batch_fetch gone".
- Never-raise: all four tools return a dict carrying the failure detail when `run_*` throws.
- Download envelope: partial-vs-typed-error non-collapse through the wrapper.
- One gate: divergent-store rejection (context untouched) + same-store reuse (zero pages).
- Config precedence: explicit `--config` > `$IG_MK_CONFIG`, via `load_config` (not a parallel resolver).
- Startup: resume runs, 0 adopted on clean store, and **before** `mcp.run()` (order spy); `--help` exits 0.
- product_type: clip→reel; stub type + unregistered type → `UNSUPPORTED_PRODUCT_TYPE`; malformed clip
  → `MALFORMED` (distinguishable); backward-compat None contract; **shuffled** mixed page proves the
  watermark rests on numeric `media_id` (non-positional) and non-clips never leak into the pool.
- Smoke: the fixture harness green end-to-end with the mid-fetch-401 politeness counter-metric
  (`ig_calls == 2` = profile + one page then stop) and a zero-IG assertion.

**Issues Found:**
- [FYI] `test_four_tool_surface_snapshot` reaches into FastMCP internals
  (`mcp._tool_manager.list_tools()`, `t.parameters`). Correct today; note it as a brittleness point if
  the `mcp[cli]` SDK is bumped. Not blocking.
- [FYI] The live smoke pass is legitimately **deferred** to pilots #10/#14 — `probe_smoke.main()` under
  `IG_MK_SMOKE_LIVE=1` prints the procedure and returns 0 **without** running or faking a green live
  result. Verified; this is the expected deferral, not a gap.

---

## Backward Compatibility Reviewer — HIGH

**Verdict:** LGTM (intended breaking change, pre-1.0)

`top_reels` (stub) removed and `batch_fetch` renamed to `start_batch_fetch` are deliberate surface
freezes, documented in the README's tool table and asserted absent by test. No external/published
consumers exist (pre-1.0; the tools were only merged internally). The rename is total (no alias left
dangling). Acceptable.

**Issues Found:** none.

---

## Developer Experience Reviewer — HIGH

**Verdict:** LGTM

- `main(argv)` adds `--config` and routes it through `load_config`'s
  `explicit > $IG_MK_CONFIG > ./config.yaml` precedence — no reimplemented resolver
  (confirmed by `test_startup_reuses_load_config_precedence`). `--help` exits 0.
- Packaging: `pyproject` pins (`curl_cffi`/`mcp[cli]`/`PyYAML`) + `ig-media-kit` entry point → `main`
  pre-existed; `config.yaml` is completed with the `batch:` politeness block and a documented
  `min_duration` filter so the shipped config covers every tool default.
- The boot print line reports jobs re-adopted + tmp swept, which is the right operator signal.

**Issues Found:**
- [FYI] `config.yaml` documents `min_duration` as a commented example only; the tool accepts it
  (surface test lists it). Fine — just confirm the commented default matches intended semantics.

---

## Documentation Reviewer — HIGH

**Verdict:** LGTM

README rewrite covers install → configure → run (module + entry point), startup job adoption (with the
throwaway-store caveat), the four tools with when-to-use each, the ~48-items/~6.6-min *escalating*
politeness note, the ~36 h signed-URL TTL with the ~24 h re-resolve margin, a "Burner: out of scope /
rejected" section linking the research report, and the deferred-live/fixture-mode smoke instructions
(explicitly "not faked green"). All AC doc points present.

**Issues Found:** none.

---

## Tech Debt Sentinel — HIGH

**Verdict:** LGTM (debt disclosed, not worsened)

- The disabled `image` demonstrator stub carries a self-describing `# TODO:` marker in
  `_handle_unsupported` (real normalization + a non-clip store contract before enabling any non-clip
  type). Intended per plan; the switch stores clips-only today.
- Build follow-up #2 (sync-path fetch during cooldown) is honestly disclosed in the build summary.

**Issues Found:**
- [SUGGESTION] Both deferred items currently live as prose/inline markers with **no filed ticket**
  (marker text only, no minted issue #). Recommend filing two follow-ups — (1) enable non-clip
  product types + non-clip store contract; (2) non-sleeping cooldown check on the sync path — so the
  debt is tracked rather than resident only in a build note and a code comment.

---

## Naming & Clarity Guardian — HIGH

**Verdict:** LGTM

`ServerContext`, `ContextMismatch`, `install_context`/`current_context`/`reset_context`,
`normalize_item_routed` / `NormalizeResult` / `SkipReason` (`UNSUPPORTED_PRODUCT_TYPE` vs `MALFORMED`),
`STUB_PRODUCT_TYPE` all read clearly and the docstrings state the load-bearing *why* (arg-ignoring gate,
divergent-store rejection, observable skip). `start_batch_fetch` reads better than `batch_fetch` for an
async launcher.

**Issues Found:** none.

---

## Invariant compliance check (build, full pass)

| Invariant | Status in build |
|---|---|
| ANONYMOUS ONLY | Honored — no auth path in diff; smoke enforces zero-IG via `_forbid_real_transport` + final assertion. |
| Politeness — pace / cap ~4 pages / stop+partial on first 401 | Honored — mid-fetch-401 test stops on first 401 (`ig_calls==2`), `pages_fetched <= max_pages_per_call`. |
| Politeness — never poll during cooldown | Honored for the batch runner; **sync-path gap disclosed** (follow-up #2, out-of-scope-by-plan, not a T5 regression). |
| `list_reels` never sleeps | Honored — synchronous, gate not acquired on the sync path (by design). |
| `x-ig-app-id` header | Honored — delegated to `http_client`, unchanged. |
| One process-wide FetchGate, arg-ignoring singleton | Honored — `get_gate` singleton; divergent-`store_dir` `config_path` **rejected** (not object-identity theater). |
| Persisted cooldown at `store/_batch/_gate.json` | Honored — pinned from server config at first `get_gate` in `startup`. |
| download_reel typed-error envelope (metered→partial+stop; aged→typed error partial=False) | Honored — preserved through the wrapper; test confirms non-collapse. |
| `resume_pending_jobs` explicit at startup, before `mcp.run()` | Honored — order-spy test. |
| product_type = observable switch, not rewrite | Honored — registry + typed `SkipReason`; store stays clips-only; shuffled-feed watermark test. |
| Dedupe + numeric media_id watermark, non-positional | Honored — shuffled mixed-page test asserts `newest_media_id==400` regardless of feed order. |
| Signed-URL TTL re-resolve | Honored — documented; download behavior unchanged (not re-tuned in T5). |
| Store never destructively capped | Not touched — T5 wires/documents only. |

All invariants honored. The single residual (sync-path cooldown check) is a plan-scoped, disclosed
follow-up, not an invariant breach introduced by this build.

---

## Review Summary

| Reviewer | Verdict | Blocking | Suggestions | FYI | Confidence |
|---|---|---|---|---|---|
| API & Contract | LGTM | 0 | 0 | 0 | HIGH |
| Concurrency & State Safety | LGTM (w/ disclosed tension) | 0 | 1 | 0 | HIGH |
| Error Handling & Resilience | LGTM | 0 | 0 | 0 | HIGH |
| Security & Trust | LGTM | 0 | 0 | 0 | HIGH |
| Test Coverage | LGTM | 0 | 0 | 2 | HIGH |
| Backward Compatibility | LGTM | 0 | 0 | 0 | HIGH |
| Developer Experience | LGTM | 0 | 0 | 1 | HIGH |
| Documentation | LGTM | 0 | 0 | 0 | HIGH |
| Tech Debt Sentinel | LGTM | 0 | 1 | 0 | HIGH |
| Naming & Clarity | LGTM | 0 | 0 | 0 | HIGH |

**Overall Recommendation:** APPROVE

**Rationale:** The build lands all five T5 slices and every acceptance criterion, and honors every
project invariant against verified source. The two round-1 plan blockers are closed *in code*:
`list_reels` now carries the typed never-raise envelope (behavioral test across all four tools), and
the one-gate guard is the load-bearing **divergent-`store_dir` rejection** — correctly framed around
config/store divergence sharing the arg-ignoring singleton, not a vacuous object-identity check.
Startup runs `resume_pending_jobs` before `mcp.run()` (order-spy verified); the product_type switch is
observable (typed `SkipReason`, shuffled-feed non-positional watermark) and ships clips-only; the smoke
harness enforces zero IG network, keeps the SSRF/callback guard intact (localhost delivery via an
injected `poster` seam), and legitimately defers the live pass to #10/#14 without faking a green
result. No blocking issues survive. The one real design tension — synchronous tools can hit IG during
an active batch cooldown because the sync path shares the gate object without acquiring it — is
honestly disclosed, is **not** introduced by T5, and was explicitly scoped out of this ticket; it is
carried as a tracked follow-up rather than a blocker. `pytest`/`mcp` were unavailable in the review
sandbox, so the "162 passed" claim was not independently re-executed; source analysis and a clean
byte-compile back the findings.

**Blocking Items:** none.

**Top Suggestions:**
1. File the sync-path cooldown reconciliation as a real follow-up ticket (a *non-sleeping* gate check
   that short-circuits `list_reels`/`download_reel` to a partial during an active cooldown), and drop a
   code marker at the `list_reels`/`fetch_gate` seam — today it lives only in build note #2. (Concurrency / Tech Debt)
2. File the non-clip enablement follow-up (real image/carousel/story normalization + a non-clip store
   contract) so the disabled-stub debt is tracked beyond the inline `# TODO`. (Tech Debt)

**Corroborated Findings:** the sync-path-cooldown tension is raised by both Concurrency and Tech Debt —
highest-signal non-blocking item; act on it via a filed ticket.

**Accepted Debt:** (1) sync tools do not gate-check cooldowns (plan-scoped out; disclosed);
(2) `image` product_type stub ships disabled behind a `# TODO` marker (intended per plan). Neither is
worsened by this build.
