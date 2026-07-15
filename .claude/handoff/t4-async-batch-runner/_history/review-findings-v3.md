---
artifact_type: handoff
artifact_version: 2
producer_role: review
consumer_role: implementation
plan_type: findings
slug: t4-async-batch-runner
scope_hint: T4 async batch runner — start_batch_fetch + get_batch_status + callback
canonical_name: review-findings
overlays: [concurrency]
status: draft
version: 3
created: 2026-07-16T21:56:14Z
updated: 2026-07-16T23:20:00Z
prior_versions: [review-findings-v1, review-findings-v2]
---

# Review findings: T4 async batch runner — start_batch_fetch + get_batch_status + callback

## Triage Decision
Scope: large (greenfield subsystem — new `batch.py` ~818 lines + `fetch_gate.py` ~223 lines, 37 new tests, concurrency overlay, cross-cutting store/config/mcp_server edits) — BUILD review (round 1) over the working-tree diff.
Partition: backend (Python; threads, sockets, flat-file store, MCP tool surface).
Review type: BUILD (round 1) — the actual diff on `feat/t4-async-batch-runner` against approved `planner-task.md` v2 and CLAUDE.md invariants. This supersedes the PLAN review (v1/v2, now archived to `_history/`).
Memory overrides: `always_include` Reliability / Rate-Limit Reviewer; `always_exclude` Accessibility; hard-stop rule on any auth/cookie/account path; any IG-hitting path change ⇒ scope at-least-medium + Reliability reviewer mandatory.

Selected Reviewers:
- Reliability / Rate-Limit Reviewer (backend, always_include) — the FetchGate + `_fill_handle` metered-stop path is the whole politeness surface.
- Concurrency & State Safety Reviewer (backend) — threads, `threading.Condition` ticket gate, daemon workers, shared cooldown, resume/launch guards.
- Security & Trust Reviewer (common) — callback SSRF guard, DNS-rebind pinning, anonymity of the callback transport.
- Test Coverage Auditor (common) — 37 new tests, injected transports + fake clock claim.
- Error Handling & Resilience Inspector (backend) — never-raise envelopes, checkpoint/resume durability.
- Tech Debt Sentinel (common, baseline) — the self-noted `run_window` divergence + TODO marker.
- Naming & Clarity Guardian (common, baseline) — brief.

Skipped: Accessibility (always_exclude — no UI); API & Contract, Data Integrity/Migration (additive-only store methods, no schema break); Performance Critic (no hot-path DB/loop concern beyond what Reliability covers).

Anonymity hard-stop check: PASS. Every IG hit is routed through `AnonymousClient` (`deps.client_factory`, default `AnonymousClient`); `x-ig-app-id` stays owned by `http_client`. The callback uses a separate bare `curl_cffi.requests.post` with no `x-ig-app-id`, no cookies, no credentials (`_default_poster`, asserted by `test_default_poster_is_anonymous_no_redirect_and_pinned`). No login/cookie/session/account code path anywhere in the diff. The hard-stop rule does NOT fire.

---

## Reliability / Rate-Limit Reviewer — REQUEST CHANGES (confidence: HIGH)

The gate design is right in the common case, and the four required refinements (persisted cooldown, explicit resume, FIFO fairness, DNS-rebind re-pin) are all present and tested. One load-bearing gap under the multi-worker scenario the subsystem explicitly supports.

**[BLOCKING] F1 — the escalated cooldown is registered AFTER the gate is released, opening a window where a second live job hits the just-rate-limited IP before the back-off lands.**
`src/ig_media_kit/batch.py`, `_fill_handle`, ~line 274–306. The metered window runs inside `with gate.acquire(): env = run_list_reels(...)`; the block then **exits/releases the gate** (line ~285), and only afterward — outside the critical section — does the code call `gate.note_metered_stop(stop_reason)` (line ~306) to set `cooldown_until`. Multiple batch workers are a supported, reachable state: `resume_pending_jobs` relaunches EVERY non-terminal job on its own daemon thread (line ~807), and `run_start_batch_fetch` can start a new job while others run. Sequence: worker A gets a 401, releases the gate; waiting worker B is notified, becomes holder, runs `_sleep_out_cooldown` which reads the still-stale `cooldown_until` (A has not set it yet) → no sleep → B immediately hits the same IP; only then does A call `note_metered_stop`. The gate's "at most one window in flight" invariant is NOT violated, but "stop hitting IG on the first 401 / back off, because the cooldown escalates under abuse" is — the extra window on an already-limited IP is exactly the abuse that extends the cooldown. Fix: apply the metered stop while still holding the gate — call `gate.note_metered_stop(...)` before the `with gate.acquire()` block exits (e.g. detect `metered` inside the block and set the cooldown there), so the next holder observes the fresh `cooldown_until` and sleeps it out. Single-job runs are unaffected, which is why the green suite doesn't catch it.

FYI (non-blocking): `note_success` (clean-window decay) has the same out-of-gate placement but is benign — decaying the counter a beat late only makes the system MORE conservative, never less.

---

## Concurrency & State Safety Reviewer — REQUEST CHANGES (confidence: HIGH)

The `FetchGate` ticket queue is correct: `_take_ticket`/`_await_turn` under one `threading.Condition`, `while holder is not None or waiters[0] != ticket: wait()`, head popped only by its own owner — so `waiters[0]` is never indexed empty and two acquirers' windows strictly interleave (`test_two_acquirers_never_overlap_and_are_fifo_fair` proves `max_in_flight == 1` + FIFO). Cooldown sleep happens while holding the gate but outside `_cond`, so other callers can still enqueue. Daemon-thread registry + `_launch` default-arg closure binding (`lambda jid=job_id:`) avoid the classic late-binding loop bug. `_maybe_resume_once` + `_RESUME_LOCK` + the per-job `_launch` guard make resume idempotent (`test_resume_pending_jobs_readopts_from_checkpoint`, `test_launch_guard_is_idempotent`).

- Corroborates **F1** — the metered-stop mutation lives outside the gate's mutual-exclusion region; the fix belongs in the same critical section as the window it describes.

**[SUGGESTION] F2 — `_classify_liveness` can mask a crashed worker as `cooldown-sleeping` for up to `cooldown_cap_s` (30 min default).**
`src/ig_media_kit/batch.py`, `_classify_liveness`, ~line 769–780. The `if job.sleep_until and now < job.sleep_until: return "cooldown-sleeping"` branch is checked BEFORE `_worker_alive`. A worker that crashes mid-cooldown (thread gone, but the last checkpoint has `sleep_until` in the future) reports `cooldown-sleeping`, indistinguishable from a healthy sleeping job, until `sleep_until` elapses. Not a correctness bug for recovery (`resume_pending_jobs` relaunches on phase, not liveness, and the `_launch` guard sees the dead thread), but the status field exists precisely to tell a sleeping job from a crashed one, and here it doesn't. Consider `_worker_alive(job.job_id)` as a precondition of the cooldown branch, or fold liveness = alive-and-sleeping vs dead-and-was-sleeping.

**[FYI] F3 — `cooldown_until` is read without the `_cond` lock in `_sleep_out_cooldown`.** A plain float read in CPython is effectively atomic; combined with F1's fix (mutation inside the gate) this is a non-issue. No action beyond F1.

---

## Security & Trust Reviewer — APPROVE (confidence: HIGH)

The SSRF guard is thorough and the anonymity of the callback transport is verified.

- `validate_callback_url` requires `https`, resolves the host, and rejects if ANY resolved address fails `_is_public_ip` — which rejects private (RFC1918 / fc00::/7), loopback, link-local (incl. `169.254.169.254` cloud metadata), multicast, reserved, unspecified, and requires `is_global`. Parametrized coverage in `test_callback_ssrf_guard` hits http, metadata, private, loopback, link-local.
- DNS-rebind TOCTOU closed: the URL is re-validated immediately before EACH POST (`_post_callback` loop) and `_default_poster` pins the connection to the validated IP via curl's `resolve=[host:port:ip]` map, so a rebind between validate and connect can't swap in an internal IP. `allow_redirects=False` means a 3xx can't bounce the POST to an internal host or to instagram.com. `test_default_poster_is_anonymous_no_redirect_and_pinned` asserts no `x-ig-app-id`, no `authorization`, no cookies, redirects off, pin present.
- Result durability ⟂ delivery: the envelope is persisted before any callback attempt; a rejected/exhausted callback still reaches `done` (`test_callback_permanent_fail_still_reaches_done`).

**[FYI] F4 — IPv6 pin formatting + resolver ordering.** `_default_resolver` returns addresses from a `set` (non-deterministic order) and pins `ips[0]`; harmless because every returned address must already pass `_is_public_ip`, so any pick is public. If an IPv6 AAAA is ever pinned, confirm curl's `resolve` map accepts the bare v6 literal in `host:port:addr` form (some curl builds want brackets). Low priority — add a v6 case to the guard test if/when a v6 callback is in scope.

---

## Error Handling & Resilience Inspector — APPROVE (confidence: HIGH)

Never-raise discipline holds end to end: both MCP tools wrap in `try/except → typed envelope`; `run_start_batch_fetch`/`run_get_batch_status` return typed `ok:False`/`found:False`; `_run_job` catches everything into `phase=failed` + recorded error and still pops the thread registry in `finally`. Atomic checkpoints reuse the store's temp+fsync+`os.replace` discipline (`_write_json_atomic`), and `save_batch_result` lands before download/callback so the result is durable independent of delivery. Phase state machine is resume-safe (re-entrant `if job.phase == ...` ladder; already-`covered` handles skipped on resume). `sweep_batch_tmp` GCs torn temps. Corrupt gate-state is non-fatal (`test_corrupt_gate_state_is_non_fatal`).

**[SUGGESTION] F5 — hard-block give-up cost.** `_fill_handle` bounds spinning via `stalls > max_stalls` (`retries+2`), but each stall still triggers a real escalating cooldown; with defaults (`retries=5`, cap 1800s) a permanently-blocked handle can sleep out ~7 escalated cooldowns (~tens of minutes to hours) before giving up. Intended politeness, but worth a one-line note in `BatchSettings` docs so an operator tuning `retries` knows it also caps total block-wait, not just callback attempts.

---

## Test Coverage Auditor — APPROVE (confidence: HIGH)

37 new tests; injected-transport + fake-clock claim verified — `FakeClock.sleep` records + advances virtual time (zero wall sleep), `FakeTransport`/`FakeResponse` back the client, resolver/poster injected. Real threads are used only where behavior IS the thing under test (gate interleaving, launch guard) with tiny bounded waits. Strong cases: no-poll-during-cooldown (`test_fill_handle_cooldown_then_resume_no_poll_during_sleep` asserts exactly `[cooldown_base_s]` sleeps + 3 transport calls), persisted cooldown across restart, stall-guard bound, both aggregation scopes + identical envelope keys, retry `500,500,200` with growing backoff, resume dedupe (no duplicate shortcodes), typed not-found, liveness cooldown-vs-dead.

**[SUGGESTION] F6 — the F1 race is untested (and the suite's green status is why it slipped).** Add a fake-clock, two-worker test: worker A hits a 401, and assert that worker B cannot open a window (no transport call) until the cooldown has been slept out. This both drives the F1 fix and guards the single-IP-under-abuse invariant against regression. Minor gap: no test asserts the crashed-during-cooldown liveness case from F2.

---

## Tech Debt Sentinel — APPROVE (confidence: HIGH)

One self-declared, well-documented debt item; no hidden hacks, no hardcoded secrets, no stray magic numbers (all knobs live in `BatchSettings` with rationale referencing CLAUDE.md's measured rate-limit behavior).

**[SUGGESTION] F7 — `run_window` divergence (author-flagged).** `_fill_handle` loops `run_list_reels` (top-check + deepen + cursor-resume) rather than the plan's named `run_window` (TOP_SCAN-only, can't deepen), leaving `run_window` unreferenced. The TODO marker + build-summary follow-up are honest. Resolve before it rots: either retire `run_window` or extend it to deepen so batch + T5 share one compose path. Track as accepted debt with a T5 checkpoint.

## Naming & Clarity Guardian — APPROVE (confidence: MED)
Names are precise and self-documenting (`OUTCOME_COVERED/PARTIAL`, `note_metered_stop`, `_sleep_out_cooldown`, `validate_callback_url`). Docstrings carry the "why" (anonymity, durability ⟂ delivery, never-poll). No action.

---

## Review Summary

| Reviewer | Verdict | Blocking | Suggestions | FYI | Confidence |
|---|---|---|---|---|---|
| Reliability / Rate-Limit | REQUEST CHANGES | 1 | 0 | 1 | HIGH |
| Concurrency & State Safety | REQUEST CHANGES | 0 (corrob. F1) | 1 | 1 | HIGH |
| Security & Trust | APPROVE | 0 | 0 | 1 | HIGH |
| Error Handling & Resilience | APPROVE | 0 | 1 | 0 | HIGH |
| Test Coverage Auditor | APPROVE | 0 | 1 | 0 | HIGH |
| Tech Debt Sentinel | APPROVE | 0 | 1 | 0 | HIGH |
| Naming & Clarity Guardian | APPROVE | 0 | 0 | 0 | MED |

**Overall Recommendation:** REQUEST CHANGES

**Rationale:** This is a high-quality build — the anonymity hard-stop passes cleanly (IG via `AnonymousClient`, callback on a bare non-IG transport with no headers/cookies), the SSRF guard is comprehensive with DNS-rebind re-pinning and redirects disabled, the FetchGate is a correct FIFO mutual-exclusion primitive, checkpoint/resume is idempotent and durable, the store is only additively extended (never capped), and the test suite genuinely runs offline on a fake clock with zero real sleeps/network. One blocking issue: the escalated cooldown is registered AFTER the gate is released (F1), so under the multi-worker scenario the subsystem explicitly supports (resume relaunches all non-terminal jobs; start can overlap), a second job can fire one more window on an already-401'd IP before the back-off lands — a real breach of the load-bearing "back off under abuse" invariant, invisible to the current single-job tests. The fix is small and localized (move `note_metered_stop` inside the gate's critical section) and should ship with the regression test F6.

**Blocking Items:**
1. F1 — `src/ig_media_kit/batch.py` `_fill_handle` ~line 306: `note_metered_stop` runs after the `gate.acquire()` block releases; move the metered-stop cooldown mutation inside the gate critical section so a concurrent worker can't slip a window onto the rate-limited IP before the escalated cooldown is set.

**Top Suggestions:**
1. F6 — add a two-worker fake-clock test asserting no IG window opens after a 401 until the cooldown elapses (drives + guards F1).
2. F2 — order `_worker_alive` before the `cooldown-sleeping` branch in `_classify_liveness` so a crash mid-cooldown isn't masked for up to `cooldown_cap_s`.
3. F7 — resolve the `run_window` vs `run_list_reels` divergence (retire or extend) to keep one compose path.
4. F5 — document that `retries` also bounds total hard-block wait time in `BatchSettings`.

**Corroborated Findings:** F1 — flagged by Reliability (blocking) and Concurrency (corroborating); highest signal, fix first.

**Accepted Debt:** F7 (`run_window` divergence) — author-flagged with a TODO + follow-up; track to a T5 decision (retire vs extend). Suggest an entry in `accepted-debt-ledger.md`.

**Memory update suggestions (not written without confirmation):**
- patterns.md: "mutating shared rate-limit/cooldown state after releasing the serialization gate opens a concurrent-window race — apply the back-off inside the critical section." (from F1)
- accepted-debt-ledger.md: `run_window` unreferenced after batch routes through `run_list_reels`; decide retire-or-extend at T5.
