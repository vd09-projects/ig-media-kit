---
artifact_type: handoff
artifact_version: 2
producer_role: implementation
consumer_role: review
plan_type: build
slug: t5-ship-mcp-server-packaging
scope_hint: "T5 — Ship: MCP server wiring, packaging, docs, extensibility, smoke"
canonical_name: implementation-build
overlays: public-api-change
status: draft
version: 1
created: 2026-07-16T11:21:20Z
updated: 2026-07-16T11:21:20Z
prior_versions: []
---

# Build summary: T5 — Ship: MCP server wiring, packaging, docs, extensibility, smoke

The four already-merged tools are now assembled into one runnable, packaged, documented FastMCP server. All five slices landed. `python -m ig_media_kit.mcp_server --config config.yaml` boots, prints an explicit resume line, and serves the frozen four-tool surface; a fresh-clone `pip install .` exposes `ig-media-kit --help`; the product_type filter is now an observable switch demonstrated by a disabled stub type; and an offline fixture smoke exercises the whole wiring with zero IG network (live run deferred to pilots #10/#14, not faked). Full suite green at 162 passed (145 baseline + 17 new), deterministic across 3 runs.

## What changed, by slice

**Slice 1 — server wiring (`src/ig_media_kit/mcp_server.py`, full rewrite).** Introduced a frozen `ServerContext` (config + `Store` + the one process-wide `FetchGate`) built once and read by every tool. Removed the stale `top_reels` stub tool; renamed `batch_fetch` → `start_batch_fetch`. Wrapped `list_reels` in the same typed never-raise envelope the other three already had. Added `main(argv)` parsing `--config` and **reusing** `load_config`'s precedence (`explicit > $IG_MK_CONFIG > ./config.yaml`) — no reimplementation. Split out `startup()` which installs the context and calls `resume_pending_jobs` **explicitly before** `mcp.run()`. One-gate enforcement via `_resolve_context`: a `config_path` whose resolved `store_dir` diverges from the server context is **rejected** (typed envelope), same-`store_dir` reuses the server context. Guard asserts **divergent-store rejection**, not object identity — because `get_gate` is an argument-ignoring singleton, identity is vacuous.

**Slice 2 — packaging (`config.yaml`).** `pyproject.toml` already pinned `curl_cffi`/`mcp[cli]`/`PyYAML` + the `ig-media-kit` entry point → `main` (verified). Completed the shipped config with the `batch:` politeness block (cooldown knobs) and a documented `min_duration` filter so it covers every tool default. Verified a clean-venv `pip install .` exposes the console script and imports the package with all four tools.

**Slice 3 — observable product_type dispatch (`src/ig_media_kit/fetch.py`).** Converted the hard `product_type != "clips"` drop into a named `_PRODUCT_HANDLERS` registry + typed `SkipReason` (`UNSUPPORTED_PRODUCT_TYPE` / `MALFORMED`) returned in a `NormalizeResult`. `normalize_item_routed` makes routing observable; `normalize_item` stays a byte-for-byte-compatible thin wrapper (unchanged None-vs-ReelRecord contract). Registered a disabled `image` demonstrator stub with a self-describing follow-up marker (no minted issue number). What reaches the store is still clips-only.

**Slice 4 — README (`README.md`, full rewrite).** install → configure → run (module + entry point), startup job adoption, the four tools with when-to-use each, the ~48-items/~6.6-min escalating politeness note, the ~36 h signed-URL TTL (~24 h re-resolve margin), a Burner: out-of-scope/rejected section linking the report's Burner heading, and the deferred-live/fixture-mode smoke instructions.

**Slice 5 — smoke harness (`probe/probe_smoke.py` + `tests/test_smoke.py`).** Fixture/dry-run mode drives `list_reels` (serve-from-store) → `download_reel` (stubbed ftyp CDN body) → `start_batch_fetch` (daemon fill of a cold handle via injected IG transport, POSTing to a **real localhost callback sink injected as a `poster` test seam** — the production https/SSRF guard runs unchanged) → `get_batch_status` (terminal), plus a simulated mid-fetch 401 through the shared context asserting `partial=True` + `stop_reason=rate_limited` + stop-on-first-401. The whole run executes under a guard that makes constructing a **real** transport a hard failure — zero IG network is enforced. The live procedure is documented and opt-in (`IG_MK_SMOKE_LIVE=1`), never run by default; no passing live result is fabricated.

## Files modified
- `src/ig_media_kit/mcp_server.py` — shared context, four-tool surface, never-raise on all four, `--config`/resume `main()`, one-gate guard.
- `src/ig_media_kit/fetch.py` — observable product_type switch (registry + `SkipReason` + `NormalizeResult`), disabled `image` stub + follow-up marker.
- `config.yaml` — added `batch:` block + documented `min_duration`.
- `README.md` — full rewrite.

## Files added
- `probe/probe_smoke.py` — offline fixture smoke harness + documented deferred-live procedure.
- `tests/test_mcp_server.py`, `tests/test_product_type_dispatch.py`, `tests/test_smoke.py`.

## Tests written (17 new)
- **Four-tool surface**: exact-name+param snapshot; assertion that `top_reels`/`batch_fetch` are gone.
- **Never-raise**: all four tools return a dict when their `run_*` throws (incl. the newly-wrapped `list_reels`); download wrapper preserves `partial` vs typed-error.
- **One gate**: divergent-`store_dir` `config_path` rejected as a typed envelope (context untouched); same-`store_dir` override reuses the server context (serve-from-store, zero pages).
- **Config precedence**: explicit `--config` beats `$IG_MK_CONFIG` beats default via `load_config`; `startup` uses it, not a parallel resolver.
- **Startup**: resume runs, returns 0 on a clean store, and completes **before** `mcp.run()` (order spy); `--help` exits 0.
- **product_type**: clip→reel; stub type→`UNSUPPORTED_PRODUCT_TYPE`; unregistered→stub; malformed clip→`MALFORMED` (distinguishable); backward-compat None contract; shuffled mixed page collects only clips with a numeric-max watermark (non-positional).
- **Smoke**: the fixture harness green end-to-end with zero-IG proof.

## Quality gate
**PASS.** `pytest` 162 passed (145 baseline unchanged + 17 new), deterministic across 3 consecutive runs (~1.2 s). Fresh clean-venv `pip install .` → `ig-media-kit --help` exit 0, four tools registered. `python -m ig_media_kit.mcp_server --config config.yaml` boots with the resume line before serving. Byte-compile clean. No lint/typecheck tooling is configured in this repo (no ruff/mypy/flake8), so the gate is the suite + install + boot + smoke, all green.

## Discovered follow-ups (reported, not filed)
1. **product_type stub seam** — the disabled `image` handler carries a self-describing marker in `fetch.py` (`_handle_unsupported`); enabling any non-clip type needs real normalization + a non-clip store contract. Intended per plan.
2. **Sync tools don't `acquire()` the gate** — `list_reels`/`download_reel` share the gate *object* (for cooldown state + the divergent-store guard) but do not call `gate.acquire()`, because acquiring would sleep out cooldowns and `list_reels` must never sleep. Consequence: a sync tool can still fire an IG window during an active batch cooldown. Reconciling this (e.g. a non-sleeping cooldown *check* that short-circuits the sync path to a partial, honoring "never poll during a cooldown" without sleeping) is a real design question the T5 scope explicitly deferred (Out of Scope: gate/cooldown logic). No single code site to mark — cross-cuts `list_reels.py` + `fetch_gate.py`.

**Ready for review — recommend multi-perspective-review.** (Medium/large change; touches the public tool surface, concurrency/gate wiring, and packaging.)
