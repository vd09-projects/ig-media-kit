---
artifact_type: handoff
artifact_version: 2
producer_role: review
consumer_role: implementation
plan_type: findings
slug: t4-async-batch-runner
scope_hint: T4 async batch runner — round-2 build (F1/F2 iteration verification)
canonical_name: review-findings
overlays: [concurrency]
status: draft
version: 4
created: 2026-07-16T21:56:14Z
updated: 2026-07-16T23:55:00Z
prior_versions: [review-findings-v1, review-findings-v2, review-findings-v3]
---

# Review findings: T4 async batch runner — round-2 build (F1/F2 iteration verification)

## Triage Decision
Scope: medium (iteration diff — F1 gate-atomicity fix + F2 liveness reorder in `batch.py`, an F5 config docstring, and 2 new regression tests over an unchanged ~2.7k-line subsystem). Round-1 already reviewed the full greenfield subsystem at large scope; this round targets the two fixes and re-scans the invariant surface for regressions.
Partition: backend (Python; threads, `threading.Condition` gate, sockets, flat-file store).
Review type: BUILD round 2 over the working-tree diff (new untracked `batch.py`/`fetch_gate.py`/`test_batch.py`/`test_fetch_gate.py` + modified `config.py`/`mcp_server.py`/`store.py`) against round-1 findings (review-findings v3) and CLAUDE.md invariants.
Memory overrides: `always_include` Reliability / Rate-Limit Reviewer; `always_exclude` Accessibility; hard-stop on any auth/cookie/account path; any IG-hitting path change ⇒ scope ≥ medium + Reliability mandatory.

Selected Reviewers:
- Reliability / Rate-Limit Reviewer (backend, always_include) — owns F1; the metered-stop/gate atomicity is the whole politeness surface.
- Concurrency & State Safety Reviewer (backend) — corroborated F1 in round 1; owns F2 liveness ordering; re-checks gate mutual exclusion, daemon-thread/lock nesting, resume idempotency.
- Test Coverage Auditor (common) — the two new regression tests are the load-bearing proof the fixes hold.
- Security & Trust Reviewer (common) — confirm SSRF/anonymity surface untouched by the fix.

Skipped: Accessibility (always_exclude — no UI); Error Handling & Resilience, Tech Debt Sentinel, Naming & Clarity — APPROVE in round 1 with no code churn on their surface this round (F5 is a pure docstring; F7 debt marker retained unchanged). Their round-1 verdicts stand.

Anonymity hard-stop check: PASS. The fix touches only gate-cooldown ordering and a status classifier — no auth/cookie/session/account code introduced. Every IG hit still routes through `AnonymousClient`; the callback remains a bare non-IG `curl_cffi.requests.post` (`_default_poster`) with no `x-ig-app-id`, no cookies, no credentials. Hard-stop does NOT fire.

---

## Reliability / Rate-Limit Reviewer — APPROVE (confidence: HIGH)

**F1 is RESOLVED.** `src/ig_media_kit/batch.py` `_fill_handle`, lines 274–308: the outcome classification (`err`/`complete`/`stop_reason`/`metered`) and the shared-state mutation `gate.note_metered_stop(stop_reason)` (line 304) — and the symmetric `gate.note_success()` (line 308) — now execute **inside** the `with gate.acquire():` block, before the context manager releases at line 309. The escalated `cooldown_until` is therefore set while the worker still holds the sole-holder gate. The next acquirer (a resumed non-terminal job on its own thread, or an overlapping `start_batch_fetch`) runs `_sleep_out_cooldown()` at the top of its `acquire()` and observes the fresh `cooldown_until`, so it sleeps the back-off out instead of firing a window on the just-401'd IP. The round-1 window — release-then-mutate, where a second worker read a stale `cooldown_until == 0` and slept nothing — is closed. This restores the load-bearing "stop/back-off on the first 401, never poll during cooldown" invariant under the multi-worker scenario the subsystem explicitly supports.

No deadlock introduced by mutating inside the gate: `acquire()` yields **outside** `self._cond` (the condition lock is held only in `_take_ticket`/`_await_turn`/`_release`), and `note_metered_stop`/`note_success` take `self._cond` freshly — no nested-lock ordering, no re-entrancy on a held lock.

**F5 addressed** (`config.py:64–69`): `BatchSettings.retries` docstring now states the fill-loop stall guard is `retries + 2`, so `retries` also bounds total hard-block wait, not just callback attempts. Accurate against `_fill_handle` (`max_stalls = config.batch.retries + 2`, line 269).

FYI (non-blocking, carried from round 1): the escalating cooldown still means a permanently-blocked handle can sleep out several escalated cooldowns before the stall guard trips — intended politeness, now documented.

---

## Concurrency & State Safety Reviewer — APPROVE (confidence: HIGH)

**F1 corroborated RESOLVED** — the mutation now lives in the same mutual-exclusion region as the window it describes; the gate's "at most one metadata window in flight process-wide" invariant and the cooldown write are now atomic with respect to the next acquirer. The FIFO ticket queue (`_await_turn`: `while holder is not None or waiters[0] != ticket`) is unchanged and still correct.

**F2 is RESOLVED.** `_classify_liveness` (`batch.py:787–811`) now evaluates `_worker_alive(job.job_id)` (line 801) **before** consulting the cooldown clock. A live daemon sleeping out a cooldown → `cooldown-sleeping`; a live worker mid-window → `fetching`. A worker that crashed mid-cooldown — thread gone from `_THREADS`, checkpoint still carrying a future `sleep_until` — no longer masks as `cooldown-sleeping` for up to `cooldown_cap_s`; with no live thread it falls through to the staleness check (line 807) and surfaces as `dead-worker`. The status field now distinguishes a healthy sleeper from a crash, which is its whole purpose.

Re-scan of the surrounding concurrency surface (unchanged, still correct): daemon-thread registry + `_launch` alive-guard make resume idempotent; the `lambda jid=job_id:` default-arg binding in `resume_pending_jobs` (line 838) avoids late-binding capture; `_maybe_resume_once` + `_RESUME_LOCK` fire resume exactly once; the worker pops itself from `_THREADS` in a `finally`. No new shared-state mutation outside the gate. `cooldown_until` is still read lock-free in `_sleep_out_cooldown` (plain float read, atomic in CPython; combined with the in-gate mutation this is a non-issue — carried F3 FYI).

---

## Test Coverage Auditor — APPROVE (confidence: HIGH)

Both new tests genuinely assert the fixed behavior (not tautologies), suite is **145 passed** offline (fake clock, injected transports, zero real sleeps/network) — verified by running `.venv/bin/pytest -q`.

- `test_two_workers_no_window_opens_after_401_until_cooldown_slept` (`tests/test_batch.py:225`) — **genuinely guards F1.** Two real threads share ONE process `FetchGate` + one `FakeClock`. Worker A serves `resolve → 401 → resume-page`; worker B (gated on `a_hit_401.wait()`, so it only begins after A's 401) serves `resolve → page`. A `RecordingTransport` timestamps every IG call on the virtual clock. The load-bearing assertion `min(b_tx.times) >= float(NOW) + base` (line 305) is exactly the F1 guard: under the pre-fix release-then-mutate ordering, B would acquire the just-released gate while `cooldown_until` was still 0 and hit IG at `NOW` — failing this assertion. `assert base in clock.sleeps` (line 307) confirms the cooldown was actually slept, and both handles still complete (lines 309–310). This test fails against the old code and passes against the fix.
- `test_liveness_crash_mid_cooldown_is_not_masked_as_sleeping` (`tests/test_batch.py:586`) — **genuinely guards F2.** A checkpoint with a future `sleep_until` but no live thread and a stale heartbeat asserts `liveness == "dead-worker"` (line 598) — the exact crash-masking case round 1 flagged as untested.
- `test_liveness_cooldown_vs_dead_worker` (line 558, updated) — now registers a live blocker thread (`_launch("s", …)`) for the sleeping case so the assertion reflects the new worker-first ordering; correctly tears the thread down in `finally`.

Minor (non-blocking): the two-worker test uses real threads with bounded `join(timeout=5.0)` — appropriate here because thread interleaving IS the behavior under test; kept deterministic by the fake clock and the `a_hit_401` handshake.

---

## Security & Trust Reviewer — APPROVE (confidence: HIGH)

The fix does not touch the callback/SSRF surface. Re-confirmed intact: `validate_callback_url` still requires `https`, resolves the host, rejects if ANY resolved address fails `_is_public_ip` (private/loopback/link-local incl. `169.254.169.254`/multicast/reserved/unspecified, requires `is_global`), and is re-invoked immediately before EACH POST (`_post_callback:587`). `_default_poster` still pins the connection to the validated IP (`resolve=[f"{host}:{port}:{pinned_ip}"]`), disables redirects (`allow_redirects=False`), and carries no `x-ig-app-id`/cookies/credentials. Result durability ⟂ delivery is preserved (envelope saved before any callback). No credential or personal-identity linkage anywhere in the diff. F4 (IPv6 pin bracketing) remains a documented, out-of-scope FYI with an added code comment.

---

## Review Summary

| Reviewer | Verdict | Blocking | Suggestions | FYI | Confidence |
|---|---|---|---|---|---|
| Reliability / Rate-Limit | APPROVE | 0 | 0 | 1 | HIGH |
| Concurrency & State Safety | APPROVE | 0 | 0 | 1 | HIGH |
| Test Coverage Auditor | APPROVE | 0 | 1 | 0 | HIGH |
| Security & Trust | APPROVE | 0 | 0 | 1 | HIGH |

**Overall Recommendation:** APPROVE

**Rationale:** The single round-1 blocking finding (F1 — escalated cooldown registered after the gate released, opening a window on a just-401'd IP for a second worker) is genuinely resolved: `gate.note_metered_stop()` and the outcome classification now execute inside the `with gate.acquire()` critical section (`batch.py:274–308`), so the next acquirer observes the fresh `cooldown_until` and sleeps it out — closing the single-IP-under-abuse race without introducing any lock-nesting or deadlock. The new two-worker fake-clock regression test asserts exactly this (B opens no IG window before `NOW + base`) and would fail against the pre-fix ordering. The F2 liveness suggestion is also implemented and tested (worker-alive checked before the cooldown branch, so a crash mid-cooldown reads `dead-worker`), and the F5 doc note is accurate. No new blocking issues surfaced across the invariant surface: anonymity holds (IG via `AnonymousClient`, callback bare non-IG), politeness is now atomic, dedupe+watermark and the SSRF guard are untouched, and concurrency (FIFO gate, idempotent resume, daemon lifecycle) remains correct. Suite is 145 passed, fully offline.

**Round-1 findings status:**
- **F1 (BLOCKING) — RESOLVED.** `batch.py:274–308` — metered-stop mutation moved inside the gate; regression test `test_two_workers_no_window_opens_after_401_until_cooldown_slept` proves it.
- **F2 (SUGGESTION) — RESOLVED.** `batch.py:787–811` — `_worker_alive` checked before the cooldown-sleeping branch; `test_liveness_crash_mid_cooldown_is_not_masked_as_sleeping` covers the crash case.

**Blocking Items:** none.

**Top Suggestions:**
1. F7 (accepted debt, carried) — resolve the `run_window` vs `run_list_reels` divergence (retire or extend `run_window` to deepen) so batch + T5 share one compose path; tracked to a T5 decision, TODO marker retained in `_fill_handle`.

**Accepted Debt:** F7 (`run_window` unreferenced) — author-flagged, deferred by direction; decide retire-vs-extend at T5.

**Memory update suggestions (not written without confirmation):**
- patterns.md: "mutating shared rate-limit/cooldown state after releasing the serialization gate opens a concurrent-window race — apply the back-off inside the critical section; guard it with a two-worker fake-clock test asserting the second worker opens no window before NOW + cooldown_base." (F1, now fixed + regression-guarded)
