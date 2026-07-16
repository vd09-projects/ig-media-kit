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
version: 2
created: 2026-07-16T08:04:20Z
updated: 2026-07-16T08:16:00Z
prior_versions: [review-findings-v1.md]
---

# Review findings: T5 — Ship: MCP server wiring, packaging, docs, extensibility, smoke

**Owner:** vd

Plan review **(iteration 2)** of `planner-task.md` **v2**. This is a targeted re-review: the two round-1 BLOCKING reviewers (API & Contract; Concurrency & State Safety) re-run against the revision with their prior findings in hand to verify resolution, plus a full invariant-compliance pass. Load-bearing plan claims re-verified against live source (`mcp_server.py`, `fetch_gate.py`, `config.py`, `fetch.py`).

## Triage Decision
Scope: large (capstone; unchanged from v1)
Partition: backend
Memory overrides: `always_include` Reliability / Rate-Limit; `always_exclude` Accessibility
Mode: **re-review** — targeted verification of resolved blockers, not a fresh full panel.

Targeted Reviewers (prior_round_findings attached):
- API & Contract Reviewer — verify blocking #1 (list_reels never-raise) resolved
- Concurrency & State Safety Reviewer — verify blocking #2 (config_path gate split) resolved

Corroborating passes (unchanged domains spot-checked, not re-litigated): Reliability/Rate-Limit, Test Coverage, Tech Debt, Developer Experience, Documentation, Security & Trust, Backward Compatibility, Naming.

---
## API & Contract Reviewer — "Your consumers can't read your mind." — RE-REVIEW

**Prior round (v1):** BLOCKING — step 2 asserted the never-raise envelope was "already present" on all four tools; `list_reels` (`mcp_server.py:24–57`) verifiably had none (bare `return run_list_reels(...)`), so an implementer would ship the frozen surface with the never-raise AC unmet.

**Verdict:** RESOLVED — LGTM

Re-verified the source: `mcp_server.py:51–56` still ends in a bare `config = load_config(config_path); return run_list_reels(...)` with **no** try/except, while `batch_fetch` (:93–103), `get_batch_status` (:115–120), and `download_reel` (:139–148) each carry an `except Exception` typed backstop. So the gap the round-1 finding named is real and still present in the code — and plan v2 now addresses it head-on:

- The Review Response (line 26) explicitly retracts the false "already present" claim and re-scopes Step 2 to **wrap `list_reels` in the same typed never-raise envelope** as explicit work.
- Step 2 body (line 93) and the Existing Code Shape note (line 65) both now call out `:24–57` as un-wrapped and mandate the wrap.
- The contract test (lines 95, 127, 159) is upgraded from names+params to **behavioral**: each of the four tools must return a `dict` (not raise) when its `run_*` is stubbed to throw — explicitly including the newly-wrapped `list_reels`.
- Added as an explicit Constraint ("Never-raise surface", line 47) and a Risk with mitigation (line 182).

This is a complete resolution: the false assumption is corrected, the work is explicit, and the guarantee is now enforced by a behavioral contract test rather than a name snapshot. No residual.

**Issues Found:** none.

---
## Concurrency & State Safety Reviewer — "Assume concurrent access until proven otherwise." — RE-REVIEW

**Prior round (v1):** BLOCKING — step 1 kept a per-call `config_path` that (via `get_gate(config)` "keyed on `store_dir/_batch/_gate.json`") could resolve a *different* `FetchGate` for a divergent `store_dir`, silently splitting the one-process-wide gate and permitting two concurrent IG windows.

**Verdict:** RESOLVED (concern closed) — with one accuracy correction the implementer must absorb (non-blocking)

The plan's prescribed fix is sound and, if anything, over-delivers. Step 1 (lines 86–89) now: (a) resolves the `FetchGate` **once** from the server context and forbids tools re-keying `get_gate` off a per-call `config_path`; (b) constrains the surviving `config_path` arg to a **store-compatible / test-only** override — a tool handed a `config_path` whose `store_dir` differs from the server context is **rejected** via the never-raise envelope, not silently gate-split; (c) adds a startup-order assertion that `resume_pending_jobs` completes before `mcp.run()` and that the daemon adoption path shares the **same gate instance** (identity, not equality). New Constraint (line 45), new Risk (line 180), and two new tests (lines 126, 129) cover the split explicitly. That fully answers the invariant concern.

**However — a code-vs-plan accuracy gap the implementer needs, verified this round:** the plan (lines 45, 69) and CLAUDE.md still describe `get_gate` as "keyed on `store_dir/_batch/_gate.json`" such that "a `config` with a different `store_dir` yields a *different* singleton." The actual `fetch_gate.py:187–215` is **not** keyed at all — it is a plain module singleton (`_SINGLETON`) whose docstring states "Subsequent calls return the same instance **regardless of args**." The FIRST `get_gate` call pins `state_path` from its config; every later call ignores its `config` arg and returns that one instance. Consequences for the implementer:

- The gate **cannot** actually be split by a divergent `config_path` in today's code — a second call just returns the same singleton. So the round-1 mechanism ("resolves a different FetchGate") was over-stated against this implementation.
- The **real** latent bug the fix still correctly closes is subtler: a tool doing `load_config(divergent_path)` shares the startup gate (good) but would run its fetch/**store** writes against a *different* `store_dir` — a config/store divergence, not a politeness split. The plan's "reject divergent-`store_dir` `config_path`" guard closes exactly this, so the prescribed work is right and worth keeping.
- One acceptance/test assertion is **vacuously true** as written: "assert the gate the tools use is the *same object* (identity) as the daemon adoption path's gate" (lines 88, 126) holds trivially today because `get_gate` is a lone singleton — it would pass even without the fix. The load-bearing, non-vacuous assertion is the sibling one ("a divergent-`store_dir` `config_path` is **rejected**"); keep that as the real guard.

None of this blocks: the plan mandates the correct behavior and the correct primary test. It should just correct the "keyed on store_dir / different singleton" wording in the plan and CLAUDE.md to "arg-ignoring process singleton," so the implementer builds the guard for the right reason and doesn't lean on the vacuous identity assertion.

**Issues Found:**
- [SUGGESTION] Correct the `get_gate` characterization in the plan (lines 45, 69) and CLAUDE.md: it is an argument-ignoring module singleton, not a `store_dir`-keyed map — the divergent-`config_path` hazard is config/store divergence sharing the startup gate, not a second gate. Lean the gate test on the **rejection** assertion; treat the identity assertion as a weak secondary (vacuous under the current singleton).
- [FYI] Startup-order + shared-gate identity assertions (line 88) are the right additions; just note the identity check is a regression-guard for a *future* refactor that might make `get_gate` config-keyed, not a check that catches a bug today.

---
## Corroborating spot-checks (unchanged since v1, re-confirmed)

- **Reliability / Rate-Limit** — v1 SUGGESTION (drive a simulated mid-fetch 401 through the *wired* surface) is now folded in: Step 6 (line 118) and Test Strategy (line 132) drive one simulated mid-fetch 401 through the shared-context server surface, asserting `partial=True` + `stop_reason` + the politeness counter-metric. Live-smoke deferral to pilots #10/#14 unchanged and still correctly gated (no live IG by default). Resolved.
- **Test Coverage / Tech Debt** — v1 SUGGESTION (product_type routing observable) folded in: Step 3 (lines 99–102) converts the `fetch.py:182` `return None` into a named registry + typed `SkipReason.UNSUPPORTED_PRODUCT_TYPE`, so the "routed to stub vs clips-path drop" assertion is now writeable. Stub carries a self-describing follow-up marker (marker text only, no minted issue #). Resolved.
- **Developer Experience** — v1 SUGGESTION (reuse `load_config` precedence) folded in: Open Question #3 removed; Step 4 is verify-only; Step 1 requires `main()` to route `--config` through `load_config` as the "explicit arg." Re-verified `config.py:104–114` (`resolve_config_path`) implements `explicit > $IG_MK_CONFIG > ./config.yaml` exactly as the plan states. Resolved. (Minor: plan cites "config.py:107" — that's the docstring line; the logic is 104–114. Harmless.)
- **Documentation** — v1 SUGGESTION (document startup job adoption for operators) folded into Step 5 (line 112). Resolved.
- **Security & Trust** — v1 FYI (inject localhost callback sink as a test seam, don't loosen the https/SSRF guard) folded into Step 6 + Out of Scope (lines 50, 117). Production guard explicitly unchanged. Resolved.
- **Backward Compatibility / Naming** — LGTM in v1, unchanged: `batch_fetch`→`start_batch_fetch` rename + `top_reels` removal handled via Consumer Inventory / Versioning / Deprecation sections; source still shows the stale `top_reels` stub (:59–66) and `batch_fetch` name (:69) awaiting the Step 2 rename, consistent with the plan.

---
## Invariant compliance check (explicit, full pass)

| Invariant | Status in plan v2 |
|---|---|
| ANONYMOUS ONLY | Honored — no auth/cookie path; harness forbidden from authenticating; fixture asserts zero `instagram.com` calls (Constraints line 41, 50; Test line 132). |
| Politeness load-bearing | Honored — now **verified through the wired surface** via the simulated-401 test (line 118, 132), closing the v1 verification gap. |
| `x-ig-app-id` header | Honored — delegated to `http_client`; plan forbids bypass (line 43). |
| `resume_pending_jobs` explicit at startup, before `mcp.run()` | Honored — Step 1 + startup-order assertion (lines 86–89, 125). |
| One FetchGate per process | Honored — resolved once from server context; divergent-`store_dir` `config_path` rejected (lines 45, 87). (Mechanism wording inaccurate — see Concurrency suggestion — but the enforced behavior is correct.) |
| download_reel partial vs typed-error envelope | Honored — preserved end-to-end; wiring test asserts non-collapse (lines 46, 94, 128). |
| Never-raise surface (all four tools) | Honored — the v1 gap (list_reels) is now explicit work + behavioral contract test (lines 47, 93, 127). |
| product_type = switch, observable | Honored — named registry + typed skip-reason; store contract untouched (lines 48, 99). |
| Dedupe + numeric media_id watermark, non-positional | Honored — shuffled-feed fixture guards non-positional (lines 49, 129). |
| Signed-URL TTL re-resolve | Honored — documented in README (line 112); download re-resolve behavior unchanged (Out of Scope line 172). |
| Store never destructively capped | Not touched — T5 wires/documents, does not re-tune (Out of Scope line 172). |

All invariants honored. The one residual is a **plan/doc accuracy** item (get_gate keying description), not an invariant breach.

---
## Review Summary

| Reviewer | Verdict | Blocking | Suggestions | FYI | Confidence |
|---|---|---|---|---|---|
| API & Contract (re-review) | RESOLVED / LGTM | 0 | 0 | 0 | HIGH |
| Concurrency & State Safety (re-review) | RESOLVED (w/ accuracy note) | 0 | 1 | 1 | HIGH |
| Reliability / Rate-Limit (confirm) | Resolved | 0 | 0 | 0 | HIGH |
| Test Coverage (confirm) | Resolved | 0 | 0 | 0 | HIGH |
| Tech Debt (confirm) | Resolved | 0 | 0 | 0 | HIGH |
| Developer Experience (confirm) | Resolved | 0 | 0 | 1 | HIGH |
| Documentation (confirm) | Resolved | 0 | 0 | 0 | HIGH |
| Security & Trust (confirm) | Resolved | 0 | 0 | 0 | HIGH |

**Overall Recommendation:** APPROVE

**Rationale:** Both round-1 blocking findings are resolved against verified source. Blocking #1 (never-raise on `list_reels`) — the false "already present" claim is retracted, the wrap is now explicit Step 2 work, and the contract test is upgraded to a behavioral dict-return-on-throw assertion across all four tools; the un-wrapped `:51–56` path confirms the gap was real and the fix targets it precisely. Blocking #2 (config_path gate split) — the plan's "resolve the gate once from server context + reject divergent-`store_dir` `config_path`" fix closes the concern (and the real underlying config/store-divergence hazard), with startup-order and shared-gate assertions added. All six round-1 non-blocking suggestions were also folded in, and every project invariant is honored — politeness now verified through the wired surface rather than only pre-existing units. Zero blocking issues remain, so this approves to build. One non-blocking accuracy carry-forward: the plan and CLAUDE.md still describe `get_gate` as `store_dir`-keyed when it is actually an argument-ignoring process singleton — the prescribed guard is correct regardless, but the wording should be fixed and the gate test should lean on the rejection assertion (the identity assertion is vacuous today).

**Blocking Items:** none.

**Top Suggestions:**
1. Correct the `get_gate` characterization (plan lines 45/69 + CLAUDE.md): it returns the same singleton regardless of args, not a `store_dir`-keyed instance. Frame the divergent-`config_path` guard around config/store divergence sharing the startup gate; make the gate test's load-bearing assertion the **rejection** of a divergent-`store_dir` override, not the (vacuous) same-object identity check. (Concurrency)

**Corroborated Findings:** none new — both prior corroborated items (never-raise completeness; product_type observability) are resolved this round.

**Accepted Debt:** none introduced. product_type stub now carries the tracked follow-up marker required in v1.
