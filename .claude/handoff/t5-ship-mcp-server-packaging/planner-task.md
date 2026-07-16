---
artifact_type: handoff
artifact_version: 2
producer_role: planner
consumer_role: implementation
plan_type: task
slug: t5-ship-mcp-server-packaging
scope_hint: T5 — Ship: MCP server wiring, packaging, docs, extensibility, smoke
canonical_name: planner-task
overlays: public-api-change
status: draft
version: 2
created: 2026-07-16T07:57:44Z
updated: 2026-07-16T08:09:29Z
prior_versions: [planner-task-v1.md]
---

# Task plan: T5 — Ship: MCP server wiring, packaging, docs, extensibility, smoke

**Overlays:** public-api-change

## Review Response (round 1)

Each round-1 item is acknowledged below with where it lands in this revision. Two blocking items, six non-blocking. No item is rejected outright; two are refined rather than taken verbatim (noted).

- **[API & Contract] list_reels missing never-raise envelope** — ACCEPTED. Step 2 previously asserted the envelope was "already present" on all four tools; that was wrong — `list_reels` (`mcp_server.py:24–57`) returns `run_list_reels(...)` directly with no try/except, unlike `batch_fetch`/`get_batch_status`/`download_reel`. Step 2 now has explicit work: wrap `list_reels` in the same typed never-raise envelope. The contract test now asserts each of the four tools returns a `dict` (never raises) when its `run_*` throws — not just names/params.
- **[Concurrency] per-call `config_path` splits the FetchGate singleton** — ACCEPTED, with a scoping refinement. `get_gate(config)` is keyed on `store_dir/_batch/_gate.json`, so a tool invoked with a `config_path` pointing at a different `store_dir` resolves a *different* `FetchGate` — silently splitting the one-process-wide gate and permitting two concurrent IG windows against the shared IP. Step 1 now: (a) resolves the `FetchGate` **once** from the server context and forbids any tool from re-keying `get_gate` off a per-call `config_path`; (b) restricts the surviving `config_path` arg to a **store-compatible / test-only** override (a tool given a `config_path` whose `store_dir` differs from the server context's is rejected, not silently gate-split); (c) adds a startup-order assertion that `resume_pending_jobs` completes before `mcp.run()` and that the daemon adoption path shares the *same* gate instance. New Risk + new Test cover the split explicitly. (Refinement: rather than delete `config_path` entirely, it is constrained to a store-compatible/test seam so the existing per-tool tests keep working — the invariant is "one gate per process," enforced by same-`store_dir` validation, not "no override arg.")
- **[non-blocking] product_type dispatch not observable** — ACCEPTED. Both the clips and non-clip paths return `None` at `fetch.py:182`, so a test can't distinguish "routed to stub" from "dropped by clips path." Step 3 now makes dispatch observable via a named registry + a typed skip-reason (e.g. `SkipReason.UNSUPPORTED_PRODUCT_TYPE`) so the routing test is writeable.
- **[non-blocking] simulated mid-fetch 401 through the wired surface** — ACCEPTED. Step 6 / Test Strategy now drives one simulated mid-fetch 401 through the wired server surface (shared context), asserting `partial=True` + `stop_reason` and the politeness counter-metric, rather than relying only on the pre-existing unit.
- **[non-blocking] Open Question #3 already answered** — ACCEPTED. `config.py:107` already implements `explicit arg > $IG_MK_CONFIG > ./config.yaml`. Open Question #3 is removed. Step 4 is now verify-only, and Step 1 explicitly requires `main()` to **reuse** `load_config`'s resolution for `--config` — not reimplement precedence.
- **[non-blocking] document resume_pending_jobs for operators** — ACCEPTED. Step 5 (README) now documents startup job adoption ("the server adopts pending `store/_batch` jobs at boot") in the operator-facing run section, not only in Risks.
- **[non-blocking] tie product_type stub to a tracked follow-up marker** — ACCEPTED. Step 3 adds a self-describing follow-up marker at the stub seam (marker text only, NO minted issue number) so the disabled seam is tracked, not untracked temporary code.
- **[non-blocking] inject smoke callback sink as a test seam, don't loosen the SSRF guard** — ACCEPTED. Step 6 injects the localhost callback sink as a test seam (dependency injection of the callback target/transport) rather than relaxing the production `https`/SSRF callback guard to accept `http://localhost`. The production guard is unchanged.

## Problem

The four tools (`list_reels`, `download_reel`, `start_batch_fetch`, `get_batch_status`) are already built, tested, and merged as separate modules, but nothing yet assembles them into one runnable, packaged, documented FastMCP server a fresh cloner can start. A skeleton `mcp_server.py` exists but ships a leftover `top_reels` stub, mis-names the batch tool (`batch_fetch`), loads config per-call instead of sharing one context, leaves `list_reels` able to raise to the MCP client, and never calls `resume_pending_jobs` at startup (the accepted daemon-thread-plus-explicit-resume decision). This ticket is the capstone: freeze the MCP tool surface as the public contract, package + document the fresh-clone path, convert the existing `product_type` filter into a demonstrated *and observable* extension switch, and deliver a smoke harness that exercises the wiring without hitting IG live (the live run is deferred to pilot tickets #10/#14 because this IP is subject to IG's escalating cooldown).

## Constraints

- **ANONYMOUS ONLY** — no login/cookies/session/account on any code path; the smoke harness and any example must not authenticate.
- **Politeness is load-bearing** — pace ~1–2s/page, cap ~4 pages/call, stop+return partial on first 401, never poll during cooldown. `list_reels` never sleeps; only the async batch runner may.
- Required header `x-ig-app-id: 936619743392459` on every API call (already enforced in `http_client`; T5 must not bypass it).
- Server startup MUST call `resume_pending_jobs(config)` **explicitly** — no import side-effect, no lazy first-call adoption as the ship path, no orphan watcher — and it MUST complete before `mcp.run()`.
- **One FetchGate per process.** All IG-hitting work serializes through the single process-wide `FetchGate` (`get_gate(config)`, keyed on `store_dir/_batch/_gate.json`); CDN downloads stay ungated; persisted cooldown lives in `store/_batch/_gate.json`. No code path may resolve a second gate by passing a `config` with a different `store_dir`.
- `download_reel` envelope: metered 401 → `partial=True` + `stop_reason` (retryable); aged-out-of-budget → typed error `partial=False` (not retryable). T5 wiring must not flatten this distinction.
- **Never-raise surface.** Every one of the four MCP tools returns a typed dict envelope and never propagates an exception to the MCP client, even when its `run_*` implementation throws.
- `product_type` dispatch stays a switch, not a rewrite: clips today; image/carousel/story are stubs — and the dispatch outcome must be **observable** (named handler + typed skip-reason), not an indistinguishable `None`.
- Standing order: discovery correctness rests on per-shortcode dedupe + numeric `media_id` watermark, never positional feed order — the scaffold and smoke fixtures must not reintroduce positional assumptions.
- **No live IG hit in this build.** AC#4 ships as a runnable harness + fixture/dry-run mode + documented live procedure; a live pass is explicitly deferred and must not be faked green. The production callback SSRF/`https` guard is not loosened for the harness — the localhost sink is injected as a test seam.

## Success Metric

- **Primary metric:** On a fresh clone, a reader following README top-to-bottom reaches a running server via `python -m ig_media_kit.mcp_server --config config.yaml` (and the `ig-media-kit` entry point) with all four tools listed over MCP and correct input schemas, and the fixture-mode smoke harness exits 0 exercising list → download → batch+callback — all in one sitting, zero source edits, zero IG network calls.
- **Counter-metric (must not regress):** No new authenticated/cookie path; existing test suite (`tests/`, incl. `test_anonymity`, `test_fetch_gate`, `test_batch`, `test_stop_signal`) stays green; the download partial-vs-typed-error envelope, the never-raise tool surface, the single-process FetchGate invariant, and the per-shortcode/media_id watermark behavior are unchanged/enforced; no live IG call introduced anywhere in CI or smoke default.
- **Evaluation window:** One reviewer does the cold-clone walkthrough in a clean venv at PR review; smoke harness green in CI on fixtures.
- **Evaluator:** PR reviewer (multi-perspective-review) + the maintainer doing the cold-clone pass.

## Mode

Modification (assembling and hardening existing merged modules; two net-new artifacts: smoke harness, expanded README).

## Existing Code Shape (modification only)

- `src/ig_media_kit/mcp_server.py` — FastMCP skeleton. Registers `list_reels` (real, **but returns `run_list_reels(...)` directly at :24–57 with no never-raise envelope** — must be wrapped), `top_reels` (stale stub — remove), `batch_fetch` (real logic, **wrong name** — must be `start_batch_fetch`), `get_batch_status` (real), `download_reel` (real). The latter three already carry the typed never-raise envelope; `list_reels` does not. `main()` is bare `mcp.run()`. **Gaps:** no `--config` CLI parse; `load_config(config_path)` called per-tool-invocation instead of one shared context; no `resume_pending_jobs` at startup; FetchGate resolved per-call (can split under a divergent `config_path`).
- `src/ig_media_kit/config.py` — `load_config(path)`. **Confirmed:** `config.py:107` already implements precedence `explicit arg > $IG_MK_CONFIG > ./config.yaml`. T5 reuses this; does not reimplement.
- `src/ig_media_kit/fetch.py:175–198` — `product_type` dispatch point. `CLIP_PRODUCT_TYPE = "clips"`; the normalizer currently **hard-drops** any `item["product_type"] != "clips"` (returns `None` at :182, indistinguishable from a clips-path drop). This is the switch to formalize *and make observable*.
- `src/ig_media_kit/batch.py` — `resume_pending_jobs(config, deps=...)` at :814; `run_start_batch_fetch`, `run_get_batch_status`, `_run_job`, `_post_callback`, daemon adoption at :663 (`_ensure_resume_once`). The explicit-resume entry point already exists; T5 calls it from server startup and asserts it shares the server's gate instance.
- `src/ig_media_kit/fetch_gate.py` — `FetchGate`, `get_gate(config)` module singleton keyed on `store_dir/_batch/_gate.json`, `reset_gate()` for tests. **Keying caveat:** a `config` with a different `store_dir` yields a different singleton — the split T5 must prevent.
- `pyproject.toml` — already pins `curl_cffi>=0.7`, `mcp[cli]>=1.2`, `PyYAML>=6.0`; `[project.scripts] ig-media-kit = "ig_media_kit.mcp_server:main"`; src layout. Mostly done — verify only.
- `config.yaml` — example already mirrors yt-media-kit (`channels`, `top_reels`, `fetch`, `output`). Verify completeness against tool args.
- `README.md` — stub ("implementation pending"), points at research. Needs full rewrite.
- `tests/fixtures/feed_sample.json` — existing feed fixture reusable by the smoke harness.

## Integration Points

- **MCP client (Claude Desktop / `mcp` CLI)** — consumes the four tool schemas; this is the public surface being frozen (see overlay sections).
- **`research/no-login-reel-fetch/report.md`** — README links its Burner section for the rejected-approach rationale; keep the anchor stable.
- **`store/` and `media/` dirs** — created/owned at runtime; `.gitignore` already excludes; server must not assume they pre-exist.
- **`store/_batch/_gate.json` + `store/_batch/<job_id>.*`** — `resume_pending_jobs` reads these at startup; harness must point at a temp store so a real run's checkpoints aren't adopted, and so its gate is a temp-store gate (not the default-store singleton).
- **`decisions/architecture/2026-07-16-daemon-thread-batch-runner-with-explicit-resume.md`** — the binding decision for step 1's startup call; do not contradict.

## Steps

1. **Shared server context (one config + one gate) + CLI + explicit resume** — `mcp_server.py`, `main()`.
   - Add a `main()` that parses `--config` (argparse) and **reuses `load_config`'s existing precedence** (`explicit arg > $IG_MK_CONFIG > ./config.yaml`, `config.py:107`) — it must NOT reimplement precedence. Call `load_config(...)` **once**, build a shared context (config + `Store(config.output.store_dir)` + a single `FetchGate` resolved once via `get_gate(config)`), call `resume_pending_jobs(config)` explicitly **before** `mcp.run()`, and make that shared config + gate reachable by every tool (module-level context set in `main`; tools read it).
   - **One-gate enforcement:** no tool re-keys `get_gate` off a per-call `config_path`. The `config_path` tool arg survives only as a **store-compatible / test-only** override: if a tool is called with a `config_path` whose resolved `store_dir` differs from the server context's `store_dir`, reject it (typed error in the never-raise envelope) rather than silently resolving a second gate. Same-`store_dir` overrides (the test path) reuse the server's gate instance.
   - **Startup-order assertion:** `resume_pending_jobs` completes before `mcp.run()`, and the daemon adoption path (`batch.py:663`) shares the *same* gate instance as the tools (assert identity, not equality).
   - Acceptance: server boots; startup log shows the resume call ran (count of re-adopted jobs, 0 on a clean store); all tools observe the same loaded config and the same gate object without re-reading disk or re-resolving a gate per call; a divergent-`store_dir` `config_path` is rejected, not gate-split.
   - Parallel-safe with: none (foundation).

2. **Freeze the four-tool surface + never-raise on all four** — `mcp_server.py`.
   - **Wrap `list_reels` (`:24–57`) in the same typed never-raise envelope** the other three tools already use — this is explicit work, not a verification. Delete the `top_reels` stub tool. Rename `batch_fetch` → `start_batch_fetch` (matching AC + docstring). Confirm exactly four registered tools: `list_reels`, `download_reel`, `start_batch_fetch`, `get_batch_status`, each with explicit typed input schemas.
   - Verify `download_reel` return path preserves `partial` / `stop_reason` vs typed-error `partial=False` distinction end-to-end (no wiring flattening).
   - Acceptance: `mcp` client lists four tools; the contract test asserts names + params **and** that each tool returns a `dict` (never raises) when its `run_*` throws; `download_reel` metered-stop vs aged-out envelopes distinguishable in a wiring test.
   - Parallel-safe with: none (depends on 1).

3. **Observable product-type dispatch + tracked stub type** — `fetch.py:175–198` (+ small new registry section).
   - Convert the hard `product_type != "clips"` drop into an explicit **named registry** keyed by `product_type`: a `clips` handler = today's normalizer (unchanged behavior), plus a registered **stub** handler for one non-clip type (default `image`) that is a clearly-marked no-op/skip. Make the outcome **observable**: the dispatch returns/records a typed skip-reason (e.g. `SkipReason.UNSUPPORTED_PRODUCT_TYPE`) so a test can distinguish "routed to the stub handler" from "dropped by the clips path" — today both are an indistinguishable `None` at `:182`.
   - Attach a **self-describing follow-up marker** at the stub seam (marker text only — NO minted issue number) so the disabled seam is tracked, not untracked temporary code. Keep `CLIP_PRODUCT_TYPE` as the only enabled type.
   - Do NOT change what reaches the store (clips-only today); the stub proves the seam, ships disabled.
   - Acceptance: a test drives a fixture item of the stubbed type through `normalize`, asserts it routes to the stub handler (via the typed skip-reason, not a bare `None`) and is excluded from stored reels; reviewers can point to a single localized diff as "how you'd add a type," plus the tracked follow-up marker.
   - Parallel-safe with: 4, 5 (independent of server wiring).

4. **Packaging + config verification** — `pyproject.toml`, `config.py`, `config.yaml`.
   - Verify deps pinned (done), entry point resolves to the new `main`, src-layout install works. **`$IG_MK_CONFIG` precedence is already implemented (`config.py:107`) — this step is verify-only**, confirming the shipped `config.yaml` example covers every tool default (channels, `top_reels`, `fetch` politeness knobs, `output` dirs) and that `main()` (step 1) routes `--config` through `load_config` rather than a parallel resolver.
   - Do a clean-venv `pip install .` (or `uv`) dry pass to confirm fresh-clone install → import → `ig-media-kit --help` works.
   - Acceptance: clean-venv install exposes the console script; `IG_MK_CONFIG=/path python -m ig_media_kit.mcp_server` picks up the env path when `--config` omitted (via `load_config`, not a reimplementation); example config validates through `load_config` with no missing keys.
   - Parallel-safe with: 3, 5.

5. **README rewrite** — `README.md`.
   - Document install → configure → run (clone, install, edit `config.yaml`, `$IG_MK_CONFIG`, start server via module + entry point). **Document startup job adoption in the operator-facing run section:** the server calls `resume_pending_jobs` at boot and adopts any pending `store/_batch` jobs — so operators know a restart resumes in-flight batches (and why a dev run should point at a temp store). List the four tools with a one-line "when to use each" (list_reels = fast synchronous top-N per handle, never sleeps; download_reel = on-demand mp4 by shortcode; start_batch_fetch = async multi-handle fill + optional download + callback; get_batch_status = poll, IG-free). Include the **~48-items/~6.6-min escalating politeness** note, the **~36 h signed-URL TTL** (re-resolve after ~24 h), and a **Burner: out of scope / rejected** section linking `research/no-login-reel-fetch/report.md` Burner section. State AC#4's live smoke is deferred to pilot #10/#14 and how to run the fixture-mode harness.
   - Acceptance: a reader with no prior context reaches a running server and a green fixture smoke from the README alone; all documented items present (including startup job adoption); Burner link resolves to the report section.
   - Parallel-safe with: 3, 4.

6. **Smoke harness (fixture/dry-run, injected sink) + documented live procedure** — new `probe/` or `tests/` harness (align with existing `probe/probe_*.py` convention), reusing `tests/fixtures/feed_sample.json`.
   - Build a runnable harness that exercises the full wiring in a **fixture/dry-run mode with zero IG network**: inject the sample feed so `list_reels` returns ranked reels from a temp store → `download_reel` resolves against a stubbed CDN body (ftyp-valid bytes) → `start_batch_fetch` runs the daemon path against the fixture and POSTs to a **local callback receiver injected as a test seam** (localhost HTTP sink supplied via dependency injection of the callback target/transport — the production `https`/SSRF guard is NOT loosened to accept `http://localhost`), then `get_batch_status` reports terminal. Point store at a temp dir so real checkpoints/gate aren't touched (and so the harness gate is the temp-store gate).
   - **Drive one simulated mid-fetch 401 through the wired server surface** (shared context), asserting the fetch stops and returns `partial=True` + `stop_reason` and that the politeness counter-metric holds — verifying politeness through the wired context, not only the pre-existing unit.
   - Ship a documented **live procedure** (real public handle, real CDN) marked DEFERRED to #10/#14, gated behind an explicit opt-in flag/env so it never runs by default or in CI. Do not fabricate a passing live result.
   - Acceptance: `python -m <harness>` (fixture mode) exits 0 offline and prints the list→download→batch→callback trace; the simulated-401 path yields a partial with `stop_reason`; grep confirms no `instagram.com` request in fixture mode; live mode is opt-in-only and clearly labeled deferred.
   - Parallel-safe with: none (depends on 1, 2 for the wired surface).

## Test Strategy

- **Server boots + explicit resume runs before serving** — integration: start server in-process with a temp store, assert `resume_pending_jobs` was invoked exactly once **before** `mcp.run()` (spy/deps injection like `test_batch.py`) and returns 0 adopted on a clean store.
- **One FetchGate per process (no config_path split)** — unit/integration: assert the gate the tools use is the *same object* (identity) as the daemon adoption path's gate; assert a tool called with a `config_path` whose `store_dir` differs from the server context is rejected (typed error, never-raise) rather than resolving a second `get_gate` singleton; a same-`store_dir` override reuses the server gate.
- **Four-tool surface frozen + never-raise** — schema/golden + behavior: assert the registered tool set is exactly the four names with expected param signatures (snapshot guards accidental additions/renames); AND assert each of the four tools returns a `dict` (does not raise) when its `run_*` is stubbed to throw — including `list_reels`, the newly wrapped one.
- **download_reel envelope preserved through wiring** — unit: metered-401 path yields `partial=True`+`stop_reason`; aged-out path yields typed error `partial=False`; assert the server tool wrapper doesn't collapse them.
- **product_type switch routes observably + excludes stub** — unit: fixture items of `clips` and the stubbed type through `normalize`; clips stored, stub routed to its handler and marked with the typed skip-reason (distinguishable from a clips-path `None`) and excluded from stored reels; watermark/dedupe unaffected (feed order shuffled in fixture to prove non-positional).
- **$IG_MK_CONFIG precedence (verify existing)** — unit: `--config` beats `$IG_MK_CONFIG` beats default via `load_config` (`config.py:107`); missing file errors cleanly; `main()` uses `load_config`, not a parallel resolver.
- **Fresh-clone install** — smoke: clean venv `pip install .`, import package, `ig-media-kit --help` exits 0.
- **End-to-end fixture smoke + mid-fetch 401** — smoke: harness runs list→download→batch→callback offline, exits 0, injected callback sink receives the aggregated payload; a simulated mid-fetch 401 through the wired surface yields a partial with `stop_reason`; assert zero `instagram.com` calls.

### Consumer Inventory

| Consumer | Surface used | Breaking? | Migration action | Owner |
|---|---|---|---|---|
| MCP client (Claude Desktop / `mcp` CLI) | 4 tool schemas | N/A — initial ship | none (no prior public release) | this project |
| `batch_fetch` early callers (internal only, pre-merge) | old tool name | yes (rename → `start_batch_fetch`) | update tool name; no external users exist yet | this project |
| Direct/test callers passing `config_path` | per-tool `config_path` override | yes (now constrained to same-`store_dir`) | pass a store-compatible config, or use the test seam; divergent-store overrides now rejected | this project |
| README/pilot tickets #10/#14 | smoke harness live mode | no | run opt-in live procedure when de-risked | this project |

### Versioning Policy

- **Current policy:** semver, pre-1.0 (`0.1.0` in pyproject); the MCP tool set is the public contract.
- **This change classification:** minor (initial public surface finalization) — the `batch_fetch`→`start_batch_fetch` rename, `top_reels` removal, the never-raise wrap of `list_reels`, and the `config_path` store-compat constraint are pre-release surface cleanup, acceptable at 0.x with no external consumers.
- **Justification:** no tagged release or external consumer of the old names/behavior exists; freezing the four-tool set (all never-raise) now establishes the baseline contract subsequent changes are measured against.

### Deprecation Timeline

- **Announcement date:** at this PR (README documents the four-tool surface as canonical).
- **Dual-support window:** none — `batch_fetch`/`top_reels` were never released; removed outright. The `config_path` constraint has no external callers to phase.
- **Removal date:** this PR.
- **Sunset signal:** N/A (no external callers); note in PR description that names changed from the skeleton and that `config_path` is now store-compatible/test-only.

### Contract Tests

- **Existing coverage:** per-tool behavior tests exist (`test_list_reels`, `test_download`, `test_batch`); no test asserting the *registered MCP surface* as a whole, nor the never-raise guarantee across all four.
- **Coverage gap closed by this work:** add a tool-surface snapshot/schema test (step 2) asserting exactly four tools with expected names + params, PLUS a never-raise assertion per tool (each returns a dict when its `run_*` throws) — the contract guard. Add the one-gate identity/split test (step 1).
- **Test type:** schema/golden snapshot of the registered tool set + param signatures + per-tool never-raise behavior test + gate-identity test.

### Communication Plan

- **Pre-release:** PR description enumerates the frozen four-tool surface, the never-raise guarantee, the skeleton→ship renames, and the one-gate/`config_path` constraint.
- **At release:** README "Tools" section is the canonical reference; CHANGELOG/commit (conventional-commits) records the surface finalization.
- **Post-release:** pilot tickets #10/#14 validate the surface against live IG; any schema change after this ships is treated as a real semver event.

## Out of Scope

- Any live IG fetch in this build (CI or default smoke) — deferred to pilot #10/#14; harness stays fixture/dry-run by default.
- Enabling image/carousel/story extraction — only the *dispatch seam* + a disabled, tracked stub handler ship; no real non-clip normalization.
- Changing fetch/pacing/gate/cooldown logic, the download envelope semantics, the callback SSRF/`https` guard, or the dedupe/watermark algorithm — T5 wires and documents existing behavior, it does not re-tune it. (The `config_path`→same-`store_dir` constraint is a wiring guard on gate resolution, not a change to gate/cooldown logic.)
- Login/cookie/burner support of any kind — permanently rejected.
- Publishing to PyPI or building wheels for distribution — fresh-clone `pip install .` is the target, not a package index release.
- New MCP tools or new tool parameters beyond the frozen four.

## Risks

- **Shared context vs per-call config drift** — moving from per-call `load_config` to one shared context could change which config a tool sees (esp. tests passing `config_path`). Likelihood med, impact med. Mitigation: `config_path` survives as a store-compatible/test-only override that falls back to the server context; precedence covered by a unit test that reuses `load_config`.
- **FetchGate split via divergent `config_path`** — a tool called with a `config_path` pointing at a different `store_dir` would resolve a second `get_gate` singleton, splitting the one-process-wide gate and permitting two concurrent IG windows against the shared IP. Likelihood med (pre-fix), impact high (politeness invariant broken → escalated cooldown). Mitigation: resolve the gate once from server context; reject divergent-`store_dir` overrides; test asserts gate identity across tools + daemon and rejection of a divergent override.
- **Startup resume adopting a real in-flight job during dev/smoke** — pointing at the default store could re-adopt live `store/_batch` jobs. Likelihood med, impact med. Mitigation: harness + tests always use a temp store; README documents (operator-facing) that the server adopts pending jobs at boot.
- **`list_reels` leaking an exception to the MCP client** — the un-wrapped `:24–57` path could raise past the client, breaking the never-raise AC. Likelihood med (pre-fix), impact med. Mitigation: wrap in the shared typed envelope; contract test asserts a dict-return-on-throw for all four tools.
- **Product-type switch accidentally widening what reaches the store** — a registry refactor could let non-clips through and pollute the pool with `play_count == null` items. Likelihood low, impact high. Mitigation: stub handler returns a typed skip / excludes; test asserts stub type excluded from stored reels; clips path byte-for-byte behavior unchanged.
- **Frozen schema drift from `mcp[cli]` version** — FastMCP schema generation could differ across `mcp` versions, breaking the snapshot test. Likelihood low, impact low. Mitigation: dep already pinned `>=1.2`; snapshot asserts names/params, not full serialized JSON.
- **README claims outrunning reality** — documenting a flow the wiring doesn't quite deliver. Likelihood med, impact med. Mitigation: README's run + smoke steps are exactly what the fixture harness executes; reviewer does the cold-clone pass as the Success Metric gate.

## Open Questions

- Which non-clip type to ship as the demonstrator stub — `image`, `carousel`, or `story`? (Blocks step 3's exact registry entry; default to `image` as the simplest, lowest-ambiguity stub unless told otherwise.)
- Harness location + invocation convention — under `probe/` (matches `probe_spike.py`, `probe_download.py`, `probe_batch.py`) vs a `tests/`-integrated smoke? (Blocks step 6 file placement; default `probe/probe_smoke.py` with a thin pytest wrapper for CI.)
- ~~Does `load_config` already implement `$IG_MK_CONFIG` precedence?~~ **RESOLVED** — yes, `config.py:107` implements `explicit arg > $IG_MK_CONFIG > ./config.yaml`. Step 4 is verify-only; step 1 reuses it.

## Handoff Notes

This artifact targets `consumer_role: implementation`. The consumer should:
- Honor the step order — step 1 (shared context + one gate + explicit resume) is the foundation everything else assumes; steps 3/4/5 are parallel-safe once 1–2 land.
- Treat "Out of Scope" as hard — no live IG calls, no real non-clip extraction, no loosening the callback SSRF guard, no re-tuning of fetch/gate/download semantics while-you're-here.
- Treat the test strategy as the minimum: the tool-surface snapshot test, the per-tool never-raise assertion, the one-gate identity/split test, the observable product-type routing test, and the offline-assertion in the smoke harness (zero `instagram.com`) are required, not optional.
- Honor the overlay sections — the four-tool surface is a frozen public contract; the schema snapshot test, the never-raise guarantee, and the `batch_fetch`→`start_batch_fetch` rename record are acceptance criteria, not advice.
- Resolve the two remaining Open Questions at build start (both have safe defaults; the `$IG_MK_CONFIG` question is resolved).
- Re-plan if discovery shows `resume_pending_jobs` cannot be called cleanly at startup without import side-effects, if the gate cannot be resolved once and shared without a larger refactor, or if the `product_type` normalizer can't be made an observable switch without touching the store contract — any of these invalidates ≥2 steps.
