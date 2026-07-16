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
version: 1
created: 2026-07-16T07:57:44Z
updated: 2026-07-16T07:57:44Z
prior_versions: []
---

# Task plan: T5 — Ship: MCP server wiring, packaging, docs, extensibility, smoke

**Overlays:** public-api-change

## Problem

The four tools (`list_reels`, `download_reel`, `start_batch_fetch`, `get_batch_status`) are already built, tested, and merged as separate modules, but nothing yet assembles them into one runnable, packaged, documented FastMCP server a fresh cloner can start. A skeleton `mcp_server.py` exists but ships a leftover `top_reels` stub, mis-names the batch tool (`batch_fetch`), loads config per-call instead of sharing one context, and never calls `resume_pending_jobs` at startup (the accepted daemon-thread-plus-explicit-resume decision). This ticket is the capstone: freeze the MCP tool surface as the public contract, package + document the fresh-clone path, convert the existing `product_type` filter into a demonstrated extension switch, and deliver a smoke harness that exercises the wiring without hitting IG live (the live run is deferred to pilot tickets #10/#14 because this IP is subject to IG's escalating cooldown).

## Constraints

- **ANONYMOUS ONLY** — no login/cookies/session/account on any code path; the smoke harness and any example must not authenticate.
- **Politeness is load-bearing** — pace ~1–2s/page, cap ~4 pages/call, stop+return partial on first 401, never poll during cooldown. `list_reels` never sleeps; only the async batch runner may.
- Required header `x-ig-app-id: 936619743392459` on every API call (already enforced in `http_client`; T5 must not bypass it).
- Server startup MUST call `resume_pending_jobs(config)` **explicitly** — no import side-effect, no lazy first-call adoption as the ship path, no orphan watcher.
- All IG-hitting work serializes through the one process-wide `FetchGate` singleton (`get_gate(config)`); CDN downloads stay ungated; persisted cooldown lives in `store/_batch/_gate.json`.
- `download_reel` envelope: metered 401 → `partial=True` + `stop_reason` (retryable); aged-out-of-budget → typed error `partial=False` (not retryable). T5 wiring must not flatten this distinction.
- `product_type` dispatch stays a switch, not a rewrite: clips today; image/carousel/story are stubs.
- Standing order: discovery correctness rests on per-shortcode dedupe + numeric `media_id` watermark, never positional feed order — the scaffold and smoke fixtures must not reintroduce positional assumptions.
- **No live IG hit in this build.** AC#4 ships as a runnable harness + fixture/dry-run mode + documented live procedure; a live pass is explicitly deferred and must not be faked green.

## Success Metric

- **Primary metric:** On a fresh clone, a reader following README top-to-bottom reaches a running server via `python -m ig_media_kit.mcp_server --config config.yaml` (and the `ig-media-kit` entry point) with all four tools listed over MCP and correct input schemas, and the fixture-mode smoke harness exits 0 exercising list → download → batch+callback — all in one sitting, zero source edits, zero IG network calls.
- **Counter-metric (must not regress):** No new authenticated/cookie path; existing test suite (`tests/`, incl. `test_anonymity`, `test_fetch_gate`, `test_batch`, `test_stop_signal`) stays green; the download partial-vs-typed-error envelope and per-shortcode/media_id watermark behavior are unchanged; no live IG call introduced anywhere in CI or smoke default.
- **Evaluation window:** One reviewer does the cold-clone walkthrough in a clean venv at PR review; smoke harness green in CI on fixtures.
- **Evaluator:** PR reviewer (multi-perspective-review) + the maintainer doing the cold-clone pass.

## Mode

Modification (assembling and hardening existing merged modules; two net-new artifacts: smoke harness, expanded README).

## Existing Code Shape (modification only)

- `src/ig_media_kit/mcp_server.py` — FastMCP skeleton. Registers `list_reels` (real), `top_reels` (stale stub — remove), `batch_fetch` (real logic, **wrong name** — must be `start_batch_fetch`), `get_batch_status` (real), `download_reel` (real). `main()` is bare `mcp.run()`. **Gaps:** no `--config` CLI parse; `load_config(config_path)` called per-tool-invocation instead of one shared context; no `resume_pending_jobs` at startup.
- `src/ig_media_kit/config.py` — `load_config(path)`. Verify `$IG_MK_CONFIG` override precedence (default path → `$IG_MK_CONFIG` → explicit `--config`) exists; if not, add.
- `src/ig_media_kit/fetch.py:175–198` — `product_type` dispatch point. `CLIP_PRODUCT_TYPE = "clips"`; the normalizer currently **hard-drops** any `item["product_type"] != "clips"` (returns None). This is the switch to formalize.
- `src/ig_media_kit/batch.py` — `resume_pending_jobs(config, deps=...)` at :814; `run_start_batch_fetch`, `run_get_batch_status`, `_run_job`, `_post_callback`, daemon adoption at :663 (`_ensure_resume_once`). The explicit-resume entry point already exists; T5 calls it from server startup.
- `src/ig_media_kit/fetch_gate.py` — `FetchGate`, `get_gate(config)` module singleton keyed on `store_dir/_batch/_gate.json`, `reset_gate()` for tests.
- `pyproject.toml` — already pins `curl_cffi>=0.7`, `mcp[cli]>=1.2`, `PyYAML>=6.0`; `[project.scripts] ig-media-kit = "ig_media_kit.mcp_server:main"`; src layout. Mostly done — verify only.
- `config.yaml` — example already mirrors yt-media-kit (`channels`, `top_reels`, `fetch`, `output`). Verify completeness against tool args.
- `README.md` — stub ("implementation pending"), points at research. Needs full rewrite.
- `tests/fixtures/feed_sample.json` — existing feed fixture reusable by the smoke harness.

## Integration Points

- **MCP client (Claude Desktop / `mcp` CLI)** — consumes the four tool schemas; this is the public surface being frozen (see overlay sections).
- **`research/no-login-reel-fetch/report.md`** — README links its Burner section for the rejected-approach rationale; keep the anchor stable.
- **`store/` and `media/` dirs** — created/owned at runtime; `.gitignore` already excludes; server must not assume they pre-exist.
- **`store/_batch/_gate.json` + `store/_batch/<job_id>.*`** — `resume_pending_jobs` reads these at startup; harness must point at a temp store so a real run's checkpoints aren't adopted.
- **`decisions/architecture/2026-07-16-daemon-thread-batch-runner-with-explicit-resume.md`** — the binding decision for step 1's startup call; do not contradict.

## Steps

1. **Shared server context + CLI + explicit resume** — `mcp_server.py`, `main()`.
   - Add a `main()` that parses `--config` (argparse), resolves the config path with precedence `--config` > `$IG_MK_CONFIG` > default `config.yaml`, calls `load_config(...)` **once**, builds a shared context (config + `Store(config.output.store_dir)` + `get_gate(config)`), calls `resume_pending_jobs(config)` explicitly before `mcp.run()`, and makes that shared config reachable by every tool (module-level context set in `main`, tools read it; `config_path` tool arg stays as an optional override for direct/test invocation, defaulting to the server context).
   - Ensure `python -m ig_media_kit.mcp_server --config config.yaml` and the `ig-media-kit` entry point both reach the same `main()`.
   - Acceptance: server boots; startup log shows the resume call ran (count of re-adopted jobs, 0 on a clean store); all tools observe the same loaded config without re-reading disk per call.
   - Parallel-safe with: none (foundation).

2. **Freeze the four-tool surface** — `mcp_server.py`.
   - Delete the `top_reels` stub tool. Rename `batch_fetch` → `start_batch_fetch` (matching AC + docstring). Confirm exactly four registered tools: `list_reels`, `download_reel`, `start_batch_fetch`, `get_batch_status`, each with explicit typed input schemas and the never-raise envelope already present.
   - Verify `download_reel` return path preserves `partial` / `stop_reason` vs typed-error `partial=False` distinction end-to-end (no wiring flattening).
   - Acceptance: `mcp` client lists four tools; a schema snapshot test asserts names + params; `download_reel` metered-stop vs aged-out envelopes distinguishable in a wiring test.
   - Parallel-safe with: none (depends on 1).

3. **Product-type dispatch scaffold + stub type** — `fetch.py:175–198` (+ small new registry section).
   - Convert the hard `product_type != "clips"` drop into an explicit switch/registry keyed by `product_type`: a `clips` handler = today's normalizer (unchanged behavior), plus a registered **stub** handler for one non-clip type (e.g. `image`/`carousel`/`story`) that is a clearly-marked no-op/skip returning `None` with a "not yet supported" marker — demonstrating that adding a type is a localized registry entry, not a rewrite. Keep `CLIP_PRODUCT_TYPE` as the only enabled type.
   - Do NOT change what reaches the store (clips-only today); the stub proves the seam, ships disabled.
   - Acceptance: a test drives a fixture item of the stubbed type through `normalize` and asserts it routes to the stub handler (not the clips path) and is excluded from stored reels; reviewers can point to a single localized diff as "how you'd add a type."
   - Parallel-safe with: 4, 5 (independent of server wiring).

4. **Packaging + config verification** — `pyproject.toml`, `config.py`, `config.yaml`.
   - Verify deps pinned (done), entry point resolves to the new `main`, src-layout install works. Confirm `$IG_MK_CONFIG` override precedence in `load_config` (add if missing) and that the shipped `config.yaml` example covers every tool default (channels, `top_reels`, `fetch` politeness knobs, `output` dirs).
   - Do a clean-venv `pip install .` (or `uv`) dry pass to confirm fresh-clone install → import → `ig-media-kit --help` works.
   - Acceptance: clean-venv install exposes the console script; `IG_MK_CONFIG=/path python -m ig_media_kit.mcp_server` picks up the env path when `--config` omitted; example config validates through `load_config` with no missing keys.
   - Parallel-safe with: 3, 5.

5. **README rewrite** — `README.md`.
   - Document install → configure → run (clone, install, edit `config.yaml`, `$IG_MK_CONFIG`, start server via module + entry point). List the four tools with a one-line "when to use each" (list_reels = fast synchronous top-N per handle, never sleeps; download_reel = on-demand mp4 by shortcode; start_batch_fetch = async multi-handle fill + optional download + callback; get_batch_status = poll, IG-free). Include the **~48-items/~6.6-min escalating politeness** note, the **~36 h signed-URL TTL** (re-resolve after ~24 h), and a **Burner: out of scope / rejected** section linking `research/no-login-reel-fetch/report.md` Burner section. State AC#4's live smoke is deferred to pilot #10/#14 and how to run the fixture-mode harness.
   - Acceptance: a reader with no prior context reaches a running server and a green fixture smoke from the README alone; all five documented items present; Burner link resolves to the report section.
   - Parallel-safe with: 3, 4.

6. **Smoke harness (fixture/dry-run) + documented live procedure** — new `probe/` or `tests/` harness (align with existing `probe/probe_*.py` convention), reusing `tests/fixtures/feed_sample.json`.
   - Build a runnable harness that exercises the full wiring in a **fixture/dry-run mode with zero IG network**: inject the sample feed so `list_reels` returns ranked reels from a temp store → `download_reel` resolves against a stubbed CDN body (ftyp-valid bytes) → `start_batch_fetch` runs the daemon path against the fixture and POSTs to a **local callback receiver** (localhost HTTP sink), then `get_batch_status` reports terminal. Point store at a temp dir so real checkpoints/gate aren't touched.
   - Ship a documented **live procedure** (real public handle, real CDN) marked DEFERRED to #10/#14, gated behind an explicit opt-in flag/env so it never runs by default or in CI. Do not fabricate a passing live result.
   - Acceptance: `python -m <harness>` (fixture mode) exits 0 offline and prints the list→download→batch→callback trace; grep confirms no `instagram.com` request in fixture mode; live mode is opt-in-only and clearly labeled deferred.
   - Parallel-safe with: none (depends on 1, 2 for the wired surface).

## Test Strategy

- **Server boots + explicit resume runs** — integration: start server in-process with a temp store, assert `resume_pending_jobs` was invoked exactly once at startup (spy/deps injection like `test_batch.py`) and returns 0 adopted on a clean store.
- **Four-tool surface frozen** — schema/golden: assert the registered tool set is exactly the four names with expected param signatures; snapshot guards accidental additions/renames (public-API contract test).
- **download_reel envelope preserved through wiring** — unit: metered-401 path yields `partial=True`+`stop_reason`; aged-out path yields typed error `partial=False`; assert the server tool wrapper doesn't collapse them.
- **product_type switch routes + excludes stub** — unit: fixture items of `clips` and the stubbed type through `normalize`; clips stored, stub routed to its handler and excluded; watermark/dedupe unaffected (feed order shuffled in fixture to prove non-positional).
- **$IG_MK_CONFIG precedence** — unit: `--config` beats `$IG_MK_CONFIG` beats default; missing file errors cleanly.
- **Fresh-clone install** — smoke: clean venv `pip install .`, import package, `ig-media-kit --help` exits 0.
- **End-to-end fixture smoke** — smoke: harness runs list→download→batch→callback offline, exits 0, callback sink receives the aggregated payload; assert zero `instagram.com` calls.

### Consumer Inventory

| Consumer | Surface used | Breaking? | Migration action | Owner |
|---|---|---|---|---|
| MCP client (Claude Desktop / `mcp` CLI) | 4 tool schemas | N/A — initial ship | none (no prior public release) | this project |
| `batch_fetch` early callers (internal only, pre-merge) | old tool name | yes (rename → `start_batch_fetch`) | update tool name; no external users exist yet | this project |
| README/pilot tickets #10/#14 | smoke harness live mode | no | run opt-in live procedure when de-risked | this project |

### Versioning Policy

- **Current policy:** semver, pre-1.0 (`0.1.0` in pyproject); the MCP tool set is the public contract.
- **This change classification:** minor (initial public surface finalization) — the `batch_fetch`→`start_batch_fetch` rename and `top_reels` removal are pre-release surface cleanup, acceptable at 0.x with no external consumers.
- **Justification:** no tagged release or external consumer of the old names exists; freezing the four-tool set now establishes the baseline contract subsequent changes are measured against.

### Deprecation Timeline

- **Announcement date:** at this PR (README documents the four-tool surface as canonical).
- **Dual-support window:** none — `batch_fetch`/`top_reels` were never released; removed outright.
- **Removal date:** this PR.
- **Sunset signal:** N/A (no external callers); note in PR description that names changed from the skeleton.

### Contract Tests

- **Existing coverage:** per-tool behavior tests exist (`test_list_reels`, `test_download`, `test_batch`); no test asserting the *registered MCP surface* as a whole.
- **Coverage gap closed by this work:** add a tool-surface snapshot/schema test (step 2) asserting exactly four tools with expected names + params — the contract guard.
- **Test type:** schema/golden snapshot of the registered tool set + param signatures.

### Communication Plan

- **Pre-release:** PR description enumerates the frozen four-tool surface and the skeleton→ship renames.
- **At release:** README "Tools" section is the canonical reference; CHANGELOG/commit (conventional-commits) records the surface finalization.
- **Post-release:** pilot tickets #10/#14 validate the surface against live IG; any schema change after this ships is treated as a real semver event.

## Out of Scope

- Any live IG fetch in this build (CI or default smoke) — deferred to pilot #10/#14; harness stays fixture/dry-run by default.
- Enabling image/carousel/story extraction — only the *dispatch seam* + a disabled stub handler ship; no real non-clip normalization.
- Changing fetch/pacing/gate/cooldown logic, the download envelope semantics, or the dedupe/watermark algorithm — T5 wires and documents existing behavior, it does not re-tune it.
- Login/cookie/burner support of any kind — permanently rejected.
- Publishing to PyPI or building wheels for distribution — fresh-clone `pip install .` is the target, not a package index release.
- New MCP tools or new tool parameters beyond the frozen four.

## Risks

- **Shared context vs per-call config drift** — moving from per-call `load_config` to one shared context could change which config a tool sees (esp. tests passing `config_path`). Likelihood med, impact med. Mitigation: keep the `config_path` tool arg as an explicit override that falls back to the server context; cover precedence with a unit test.
- **Startup resume adopting a real in-flight job during dev/smoke** — pointing at the default store could re-adopt live `store/_batch` jobs. Likelihood med, impact med. Mitigation: harness + tests always use a temp store; document that the server adopts pending jobs at boot.
- **Product-type switch accidentally widening what reaches the store** — a registry refactor could let non-clips through and pollute the pool with `play_count == null` items. Likelihood low, impact high. Mitigation: stub handler returns None/skip; test asserts stub type excluded from stored reels; clips path byte-for-byte behavior unchanged.
- **Frozen schema drift from `mcp[cli]` version** — FastMCP schema generation could differ across `mcp` versions, breaking the snapshot test. Likelihood low, impact low. Mitigation: dep already pinned `>=1.2`; snapshot asserts names/params, not full serialized JSON.
- **README claims outrunning reality** — documenting a flow the wiring doesn't quite deliver. Likelihood med, impact med. Mitigation: README's run + smoke steps are exactly what the fixture harness executes; reviewer does the cold-clone pass as the Success Metric gate.

## Open Questions

- Which non-clip type to ship as the demonstrator stub — `image`, `carousel`, or `story`? (Blocks step 3's exact registry entry; default to `image` as the simplest, lowest-ambiguity stub unless told otherwise.)
- Harness location + invocation convention — under `probe/` (matches `probe_spike.py`, `probe_download.py`, `probe_batch.py`) vs a `tests/`-integrated smoke? (Blocks step 6 file placement; default `probe/probe_smoke.py` with a thin pytest wrapper for CI.)
- Does `load_config` already implement `$IG_MK_CONFIG` precedence, or is it net-new in step 4? (Blocks step 4 sizing; confirm by reading `config.py` at build start.)

## Handoff Notes

This artifact targets `consumer_role: implementation`. The consumer should:
- Honor the step order — step 1 (shared context + explicit resume) is the foundation everything else assumes; steps 3/4/5 are parallel-safe once 1–2 land.
- Treat "Out of Scope" as hard — no live IG calls, no real non-clip extraction, no re-tuning of fetch/gate/download semantics while-you're-here.
- Treat the test strategy as the minimum: the tool-surface snapshot test and the offline-assertion in the smoke harness (zero `instagram.com`) are required, not optional.
- Honor the overlay sections — the four-tool surface is a frozen public contract; the schema snapshot test and the `batch_fetch`→`start_batch_fetch` rename record are acceptance criteria, not advice.
- Resolve the three Open Questions at build start (two have safe defaults; the `$IG_MK_CONFIG` one is a 2-minute read of `config.py`).
- Re-plan if discovery shows `resume_pending_jobs` cannot be called cleanly at startup without import side-effects, or if the `product_type` normalizer can't be made a switch without touching the store contract — either invalidates ≥2 steps.
