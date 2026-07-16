---
artifact_type: handoff
artifact_version: 2
producer_role: review
consumer_role: planner
plan_type: findings
slug: t5-ship-mcp-server-packaging
scope_hint: T5 — Ship: MCP server wiring, packaging, docs, extensibility, smoke
canonical_name: review-findings
overlays: public-api-change
status: draft
version: 1
created: 2026-07-16T08:04:20Z
updated: 2026-07-16T08:04:20Z
prior_versions: []
---

# Review findings: T5 — Ship: MCP server wiring, packaging, docs, extensibility, smoke

**Owner:** vd

Plan review (iteration 1) of `planner-task.md` v1. Judged against the 5 acceptance criteria, correctness of the 6-step breakdown, the project invariants, and the soundness of deferring the live IG smoke to pilots #10/#14. Load-bearing plan claims were verified against the actual source (`mcp_server.py`, `config.py`, `fetch.py`, `batch.py`).

## Triage Decision
Scope: large (capstone; cross-cutting ship — server wiring, public API freeze, packaging, docs, concurrency startup, smoke)
Partition: backend
Memory overrides: `always_include` Reliability / Rate-Limit Reviewer; `always_exclude` Accessibility

Selected Reviewers:
- Reliability / Rate-Limit Reviewer (custom) — politeness invariant + live-smoke deferral soundness
- Backward Compatibility Reviewer (common) — freezing four-tool surface, `batch_fetch`→`start_batch_fetch` rename
- API & Contract Reviewer (backend) — never-raise tool envelope contract, schema freeze
- Concurrency & State Safety Reviewer (backend) — `resume_pending_jobs` at startup, process-wide `FetchGate` singleton, shared context
- Test Coverage Auditor (common) — fixture/dry-run harness, contract snapshot test
- Documentation Reviewer (common) — README rewrite AC
- Developer Experience Reviewer (common) — fresh-clone path, `--config`/`$IG_MK_CONFIG`
- Tech Debt Sentinel (common, baseline) — `product_type` stub seam
- Naming & Clarity Guardian (common, baseline) — rename correctness
- Security & Trust Reviewer (common) — anonymous-only invariant, callback sink

Skipped: Accessibility (no UI, excluded by memory); FE/CSS/State reviewers (no frontend); Data Integrity/Migration (no schema change); Performance Critic (no hot-path change — wiring only).

---
## Reliability / Rate-Limit Reviewer — "The shared IP is a commons; don't burn it."

**Verdict:** Suggestions

Deferring the live IG smoke to pilots #10/#14 and shipping a fixture/dry-run harness instead is **the correct call and I endorse it**. A live fetch in CI or default smoke would spend the ~48-item/~6.6-min budget and, worse, could trip the escalating cooldown (6.6→13 min) on the maintainer's IP for no verification value the unit suite doesn't already give. The plan honors "never poll during a cooldown" by refusing to run live by default, gates live behind an explicit opt-in, and — importantly — forbids faking a green live result (step 6, Out of Scope). That is exactly right.

One real gap: the fixture harness injects a sample feed, so it exercises **wiring** but bypasses the entire politeness path — `FetchGate` acquisition, the 401→`partial` stop, cooldown persistence to `store/_batch/_gate.json`, and the ~4-pages/call cap. The counter-metric claims "politeness unchanged," but the *delivered* harness never observes it; only the pre-existing `test_fetch_gate`/`test_stop_signal` units do, and those don't run through the new shared-context server path.

**Issues Found:**
- [SUGGESTION] The smoke harness (or a companion wiring test) should drive one simulated mid-fetch 401 through the *wired* server surface and assert `list_reels`/batch return a partial with the cooling note AND that the gate cooldown was written — so the shared-context refactor is proven not to have severed the politeness path, not just the happy path. Rationale: AC's "politeness unchanged" counter-metric is otherwise asserted nowhere in this deliverable.
- [FYI] Consider having the README's deferred-live section restate the ~48/6.6-min budget as the *reason* live is opt-in, so a pilot operator understands why before running #10/#14.

---
## API & Contract Reviewer — "Your consumers can't read your mind."

**Verdict:** Blocking Issues

The whole point of step 2 is to freeze the four-tool surface as the public never-raise contract. But the plan's step 2 states each tool ships "with … the never-raise envelope already present." **That is factually false for `list_reels`.** In `mcp_server.py:24-57`, `list_reels` ends with a bare `return run_list_reels(...)` — no `try/except`, unlike `batch_fetch`, `get_batch_status`, and `download_reel`, which all wrap in `except Exception` backstops. An implementer following the plan literally will "confirm the envelope is present," see three of four, and ship `list_reels` able to raise an uncaught exception to the MCP client — leaving the never-raise surface AC unmet at the moment it's declared frozen.

**Issues Found:**
- [BLOCKING] Step 2 must add "wrap `list_reels` in the same never-raise envelope" as explicit work, not assume it exists. Verified gap at `src/ig_media_kit/mcp_server.py:24-57` (no try/except) vs the other three tools. Fix: fold a typed-envelope backstop into `list_reels` and assert never-raise for all four in the contract test.
- [SUGGESTION] The contract/snapshot test (step 2) should assert not just names+params but that each of the four returns a dict (never raises) when its `run_*` throws — the never-raise contract is behavioral, and a name-only snapshot won't catch a regression that removes an envelope.

---
## Concurrency & State Safety Reviewer — "Assume concurrent access until proven otherwise."

**Verdict:** Blocking Issues

Step 1 keeps the per-tool `config_path` override, which today calls `load_config(config_path)` and (inside the `run_*` paths) `get_gate(config)`. `get_gate` is a module singleton **keyed on `store_dir/_batch/_gate.json`**. If a tool is invoked with a `config_path` resolving to a *different* `store_dir` than the server context, it resolves a *different* `FetchGate` — silently splitting the "one process-wide gate" invariant and allowing two concurrent IG windows against the shared IP. The plan's Risks note "config drift" generically but never names this gate-keying split, and step 1's "config_path override falls back to server context" mitigation only covers which config is read, not which gate is keyed.

**Issues Found:**
- [BLOCKING] Step 1 must specify that the `FetchGate` is resolved **once** from the server context and that no tool re-keys `get_gate` off a per-call `config_path` (or restrict the `config_path` override to a store-compatible config / test-only). Otherwise the process-wide-gate invariant is defeatable through a normal tool call. Ref: `fetch_gate.py get_gate(config)` keyed on store_dir; `mcp_server.py` tools call `load_config(config_path)` per-invocation.
- [SUGGESTION] Step 1 acceptance should pin the startup ordering: `resume_pending_jobs(config)` (which spawns the daemon runner per the 2026-07-16 decision) completes/returns its adopted-count *before* `mcp.run()`, and the daemon shares the same gate instance — add a startup-order assertion to the boot test so a future refactor can't interleave them.

---
## Backward Compatibility Reviewer — "Existing consumers didn't get the PR notification."

**Verdict:** LGTM

The rename `batch_fetch`→`start_batch_fetch` and removal of `top_reels` are handled properly: Consumer Inventory names the only affected caller (internal, pre-merge), the Versioning Policy classifies it as pre-1.0 surface cleanup with no external consumers, and the Deprecation Timeline correctly records "never released → removed outright, no dual-support window." This is the right amount of ceremony for a 0.x initial-surface freeze — no flag day, no migration shim needed.

**Issues Found:**
- [FYI] The frozen-surface snapshot test is the durable guard here; once merged, any later rename becomes a real semver event as the plan states. Good.

---
## Test Coverage Auditor — "An untested change is an unverified assumption."

**Verdict:** Suggestions

Test strategy is strong on breadth (boot+resume spy, four-tool snapshot, download envelope preserved, product_type routing with shuffled feed to prove non-positional, `$IG_MK_CONFIG` precedence, clean-venv install, end-to-end offline smoke). Two observability gaps make specific asserts unwriteable as described.

**Issues Found:**
- [SUGGESTION] Step 3 acceptance asserts a stub-type item "routes to the stub handler (not the clips path)." Both the clips-reject and the stub-skip currently return `None` (see `fetch.py:182` `return None`), so the test cannot distinguish them unless the dispatch is made observable — e.g., handlers return a typed skip-reason or route through a named registry the test can inspect. The plan should require the seam to expose *which* handler fired, or the routing assertion is untestable.
- [SUGGESTION] Add the never-raise behavioral assertion (from API reviewer) to the four-tool contract test — currently the strategy snapshots names/params only.

---
## Tech Debt Sentinel — "Every shortcut is a loan; I read the terms."

**Verdict:** Suggestions

The `product_type` switch-not-rewrite is well-scoped: convert the hard `!= "clips"` drop into a registry, ship one disabled stub, keep `CLIP_PRODUCT_TYPE` the only enabled type, don't change what reaches the store. This repays existing implicit debt (the magic `return None` at `fetch.py:182`) rather than adding to it. Good.

**Issues Found:**
- [SUGGESTION] Open Question #1 (which stub type) defaults to `image` — fine, but the stub must carry a tracked marker ("not yet supported," pointing at a follow-up ticket) rather than a bare no-op, or it becomes an untracked "temporary" that outlives us. Tie it to a real backlog id.
- [FYI] The plan already declares the store contract untouched and tests it — that's the correct guardrail against the "switch widens what reaches the store" risk it names.

---
## Developer Experience Reviewer — "Your teammates are users too."

**Verdict:** Suggestions

Fresh-clone path is the headline metric and is well-specified (module + entry point, one shared context, README top-to-bottom). One correctness note on sizing.

**Issues Found:**
- [SUGGESTION] Open Question #3 is already answered: `config.py:107` implements `Priority: explicit arg > $IG_MK_CONFIG > ./config.yaml` (`CONFIG_PATH_ENV = "IG_MK_CONFIG"`, line 19). So step 4 is verify-only, and step 1's `main()` must **reuse** `load_config`'s existing resolution for `--config`/env/default rather than reimplement precedence — two implementations will drift. Fold `--config` in as the "explicit arg" that `load_config` already honors.
- [FYI] Keeping `config_path` as a per-tool override for direct/test invocation is a reasonable ergonomic — just constrain it per the Concurrency reviewer's gate finding.

---
## Documentation Reviewer — "The next dev has only the docs and the code."

**Verdict:** Suggestions

README AC (install→configure→run, four tools with when-to-use, ~48/6.6-min politeness, ~36h TTL, Burner-rejected link) is complete and the acceptance ("reader with no context reaches a running server + green fixture smoke") is the right gate.

**Issues Found:**
- [SUGGESTION] The README run section should document a real surprise: the server **adopts pending batch jobs at startup** via `resume_pending_jobs`. A fresh operator whose prior run left `store/_batch/*` checkpoints will see jobs resume on boot. The plan flags this only for dev/smoke (Risks) — it belongs in the operator-facing README too.

---
## Security & Trust Reviewer — "Every input is hostile until proven otherwise."

**Verdict:** LGTM

No auth/cookie/login/account path is introduced anywhere; the harness and examples are explicitly forbidden from authenticating; fixture mode asserts zero `instagram.com` calls. The anonymous-only invariant is honored. `x-ig-app-id` enforcement is delegated to the existing `http_client` and the plan forbids bypassing it.

**Issues Found:**
- [FYI] The smoke uses a localhost HTTP callback sink, but `start_batch_fetch`'s real callback path is https-only + SSRF-guarded. Ensure the harness **injects** the local sink (test seam) rather than loosening the production https/SSRF guard to accommodate `http://localhost` — the guard must ship unchanged.

---
## Naming & Clarity Guardian — "Understand it in 30 seconds or on-call can't at 2am."

**Verdict:** LGTM

`batch_fetch`→`start_batch_fetch` matches the domain verb-object form and the underlying `run_start_batch_fetch`; dropping the misleading `top_reels` stub (whose behavior was never top-N) removes a lie. Names align with the frozen surface.

---
## Review Summary

| Reviewer | Verdict | Blocking | Suggestions | FYI | Confidence |
|---|---|---|---|---|---|
| Reliability / Rate-Limit | Suggestions | 0 | 1 | 1 | HIGH |
| API & Contract | Blocking | 1 | 1 | 0 | HIGH |
| Concurrency & State Safety | Blocking | 1 | 1 | 0 | HIGH |
| Backward Compatibility | LGTM | 0 | 0 | 1 | HIGH |
| Test Coverage Auditor | Suggestions | 0 | 2 | 0 | HIGH |
| Tech Debt Sentinel | Suggestions | 0 | 1 | 1 | HIGH |
| Developer Experience | Suggestions | 0 | 1 | 1 | HIGH |
| Documentation | Suggestions | 0 | 1 | 0 | HIGH |
| Security & Trust | LGTM | 0 | 0 | 1 | HIGH |
| Naming & Clarity | LGTM | 0 | 0 | 0 | HIGH |

**Overall Recommendation:** REQUEST CHANGES

**Rationale:** This is a strong, complete plan — all five acceptance criteria are covered (AC1 server wiring/run = steps 1+2+4; AC2 frozen four-tool surface = step 2; AC3 docs = step 5; AC4 smoke = step 6; AC5 extensibility = step 3), the invariants are explicitly enumerated as constraints, and the decision to defer the live IG smoke to pilots #10/#14 while shipping a fixture/dry-run harness is correct and well-justified against the escalating-cooldown invariant. Two plan-level defects block, both cheap to fold in: (1) step 2 asserts a never-raise envelope is "already present" on all four tools, but `list_reels` verifiably has none — an implementer following the plan ships the frozen surface with an unmet never-raise AC; (2) step 1 leaves a per-call `config_path` path that can re-key the `FetchGate` off a different `store_dir`, defeating the process-wide-gate invariant the ticket is meant to lock down. Neither requires redesign — they're refinements to steps 1 and 2. Fix those two, make the product_type routing observable so its test is writeable, and this is ready to build.

**Blocking Items:**
1. Step 2 — add "wrap `list_reels` in the never-raise envelope" as explicit work; the plan's "already present" claim is false at `mcp_server.py:24-57`. (API & Contract)
2. Step 1 — pin that `FetchGate` is resolved once from server context and no tool re-keys `get_gate` off a per-call `config_path`, or the one-process-wide-gate invariant is defeatable via a normal tool call. (Concurrency & State Safety)

**Top Suggestions:**
1. Make `product_type` dispatch observable (typed skip-reason / named registry) so step 3's "routed to stub not clips path" assertion is actually writeable. (Test Coverage, Tech Debt)
2. Fold one simulated mid-fetch 401 through the wired server surface in the smoke/wiring test so the "politeness unchanged" counter-metric is verified through the new shared context, not only pre-existing units. (Reliability, Test Coverage)
3. Reuse `load_config`'s existing `$IG_MK_CONFIG` precedence (config.py:107) in `main()` instead of reimplementing it; downgrade step 4 to verify-only. (DX)
4. Document server-adopts-pending-jobs-at-startup in the README, not just the Risks section. (Documentation)

**Corroborated Findings (highest signal):**
- Never-raise contract completeness flagged by both API & Contract (blocking) and Test Coverage (snapshot should assert behavior, not just names).
- `product_type` routing observability flagged by both Test Coverage and Tech Debt Sentinel.

**Accepted Debt:** None introduced. The `product_type` stub is acceptable debt only if it carries a tracked follow-up ticket marker (Tech Debt Sentinel suggestion 1).

**Invariant compliance check (explicit):** ANONYMOUS ONLY — honored (no auth path; fixture asserts zero IG calls). Politeness/rate-limit — honored in design; verification gap noted (suggestion 2). `x-ig-app-id` — delegated to `http_client`, not bypassed. `resume_pending_jobs` at startup — present in step 1; ordering assertion recommended. Process-wide `FetchGate` — **at risk** via per-call config_path (blocking 2). download_reel typed-error envelope — preserved and tested (step 2). product_type switch-not-rewrite — honored (step 3). Per-shortcode dedupe + media_id watermark — honored; shuffled-feed test guards non-positional.
