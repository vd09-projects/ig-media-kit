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
version: 2
created: 2026-07-18T12:39:19Z
updated: 2026-07-18T12:56:44Z
prior_versions:
  - _history/implementation-build-v1.md
---

# Build summary: T17 iterate — machine-enforce zero-IG import boundary + fix stale fetch_gate docstring

**Overlays:** public-api-change

ITERATE over the APPROVED T17 build (review v2, 0 blocking). Absorbed exactly the two concrete, low-risk hardening suggestions from the review panel — the Test Coverage Auditor's top suggestion and the Documentation Reviewer's nit — and nothing else. **No production behavior changed:** one new test file + one docstring-word fix. The two deferred suggestions (`params.py` extraction, `window.py` retire) were explicitly left as separate follow-up tickets, untouched.

## Findings addressed

1. **(Test Coverage Auditor — top suggestion) Machine-enforce the zero-IG import boundary in CI.** Added `tests/test_import_boundary.py`: a purely STATIC, AST-based guard. It resolves `list_reels`' source via `importlib.util.find_spec` (which imports only the parent package, never executing `list_reels` itself), `ast.parse`s it, and walks the ENTIRE module (top-level *and* any nested/lazy imports inside functions) asserting it imports neither `ig_media_kit.fetch` nor `ig_media_kit.http_client`, and no `AnonymousClient` / `fetch_window` / `resolve_user_id` symbol. This converts the old build-time grep acceptance into a permanent CI regression tripwire, closing the gap the auditor named: a future refactor calling `fetch_window` on a *third*, untested branch would now fail here rather than slip past the two single-branch runtime poison tests.

   The test carries a built-in **positive control**: before the negative assertion it runs the same detector against `fill.py` (the known violator) and asserts it DOES find both forbidden modules + `AnonymousClient`. This proves the detector actually fires — a broken/trivially-passing detector can't give false assurance. Kept inside the one test function so the suite grows by exactly one test (177 → 178), per the iteration's expected count.

2. **(Documentation Reviewer — nit) Stale `fetch_gate.py` docstring.** `src/ig_media_kit/fetch_gate.py:7` read "Every batch ``run_list_reels`` / re-resolve call is wrapped in ``acquire()``". After the T17 extraction the batch runner routes `fill.run_fill` through the gate, not `run_list_reels`. Corrected `run_list_reels` → `run_fill`. Docstring-only; zero behavior change.

## Files modified

- `tests/test_import_boundary.py` — **NEW**: static AST import-boundary guard for the zero-IG invariant (1 test, self-validating via a `fill.py` positive control).
- `src/ig_media_kit/fetch_gate.py` — module docstring: `run_list_reels` → `run_fill` (1 word; the batch path name after the T17 CQRS extraction).

## Explicitly NOT done (deferred follow-ups, out of scope)

- Follow-up #1 — extract duplicated `_Params`/`_resolve_params`/`_validate` into `ig_media_kit/params.py`. Separate ticket.
- Follow-up #2 — retire/re-route the now-unreferenced `window.py::run_window`. Separate ticket.

## Quality gate

- `pytest`: **178 passed** (was 177; +1 the new import-boundary test) in 1.50s.
- New test in isolation: 1 passed.
- Byte-compile (`compileall src tests probe`): exit 0 — the project's lint surrogate (no ruff/mypy in env).
- Offline smoke harness (`python -m probe.probe_smoke`): exit 0, `zero_ig_network True` — the zero-IG guard still holds end-to-end.
- Invariant reinforced: the zero-IG boundary for `list_reels` is now enforced statically at import time, not only by two single-branch runtime poison tests + a non-CI grep.

## Terminal state

Ready for review. Low-risk hardening iteration (one new static test + one docstring word); no production behavior change, no new blocking surface.
