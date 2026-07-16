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
version: 2
created: 2026-07-16T21:56:14Z
updated: 2026-07-16T22:40:00Z
prior_versions: [review-findings-v1]
---

# Review findings: T4 async batch runner — start_batch_fetch + get_batch_status + callback

## Triage Decision
Scope: large (greenfield subsystem, concurrency overlay, cross-cutting) — ROUND 2 re-review of a revised plan
Partition: backend
Review type: PLAN (round 2) — no diff; verify round-1 blockers are resolved in the revised design body + hunt new blockers
Memory overrides: always_include Reliability / Rate-Limit Reviewer; hard-stop rule on any auth/cookie/account path

Selected Reviewers (round-1 blockers re-run + corroborators):
- Reliability / Rate-Limit Reviewer (backend) — verify F1 (cross-job serialization) + politeness invariant
- Concurrency & State Safety Reviewer (backend) — verify F2 (restart-resume) + gate/thread lifecycle
- Security & Trust Reviewer (common) — verify F3 (callback SSRF/redirect)
- Tech Debt Sentinel (common, baseline) — verify Open Questions reduced to non-blocking
- Error Handling & Resilience Inspector (common) — recovery-table completeness after revision
- Data Integrity & Migration Reviewer (backend) — checkpoint atomicity + resume dedupe
- API & Contract Reviewer (backend) — pinned param contract + stable envelope
- Test Coverage Auditor (common) — new tests for the three fixes
- Naming & Clarity Guardian (common, baseline)

Skipped: Frontend/CSS/A11y/State-Management (no UI); Infrastructure (no manifests); Domain Logic (reused unchanged from T2/T3).

---

## Verification of Round-1 Blocking Findings

**F1 — Cross-job fetch serialization → RESOLVED.** The revised body promotes it from Open Question to a hard invariant with its own foundational step. Step 3 builds `fetch_gate.FetchGate`, a module-level singleton whose `acquire()` context manager enforces mutual exclusion (at most one IG window in flight process-wide), sleeps before yielding if `now < cooldown_until`, and carries `note_metered_stop`/`note_success`. Step 4 wraps every `run_window` in `acquire()`; Step 9 states a second `start_batch_fetch` gets its own `job_id` and its worker queues behind the gate rather than rejecting or racing. The Constraints, Invariants-Under-Concurrency, and Shared-State-Inventory sections all now name the gate as the single-IP enforcement point, and a serialization test is added. Genuinely designed in, not deferred.

**F2 — Restart-resume entrypoint → RESOLVED.** Step 9 adds `resume_pending_jobs(config)` as a first-class callable: runs `Store.sweep_batch_tmp`, sweeps `store/_batch/` for non-terminal jobs, relaunches each from its checkpoint on a fresh daemon thread, guarded by a per-`job_id` in-process launch guard for idempotency. The primary success metric is rewritten around a full-process-restart simulation, and a matching integration test (discard threads + in-memory state → `resume_pending_jobs` → completes from checkpoint, no duplicate shortcodes, no concurrent fetch) is specified. The API-surface/metric contradiction from round 1 is closed.

**F3 — Callback SSRF/redirect → RESOLVED.** Step 8 requires `https` scheme; resolves the hostname and rejects private (RFC1918), link-local (169.254.0.0/16 incl. 169.254.169.254), loopback (127/8, ::1), unique-local (fc00::/7), and non-global addresses; disables redirect-follow; re-checks immediately before each POST (DNS-rebinding mitigation); uses a bare non-IG transport with zero IG headers. Validation runs both at `start_batch_fetch` time and pre-POST. A dedicated SSRF/redirect-rejection test is added. Well-specified.

All three round-1 blockers are genuinely resolved in the design body. No blocker regressed; Open Questions are down to two non-blocking, probe-tunable constants.

---

## Reliability / Rate-Limit Reviewer — APPROVE with suggestions (Confidence: HIGH)

F1 is resolved (see above). The gate is the correct primitive and the "runner is the only sleeper" + "no poll during cooldown" invariants are now enforced at a single chokepoint that also covers the future T5 sync-tool wiring. The escalation-sourced sleep (`cooldown_base_s * factor**escalation_count`, decay on success) replaces the round-1 blind-guess concern.

**Suggestion — the gate's cooldown state is in-memory and does not survive the restart it is designed to tolerate.** The Shared-State Inventory lists `FetchGate` (with `cooldown_until` + `escalation_count`) as an *in-memory singleton*, while Step 3 describes the escalation counter as "persisted/shared." These conflict. On a full-process restart, `resume_pending_jobs` relaunches jobs with a fresh gate whose `cooldown_until = 0` and `escalation_count = 0`, so the first post-restart window can hit IG while the *external* per-IP cooldown is still active — a (small, self-correcting) politeness regression exactly in the restart path the plan optimizes for. It self-heals (the window 401s → `note_metered_stop` re-escalates), costing one metered call, which is consistent with the already-accepted "resume re-fetches one metered page" debt. Recommend either persisting `cooldown_until`/`escalation_count` to a small gate-state file read on init, or explicitly naming this as accepted debt in the Risks table so it isn't discovered in the pilot. Non-blocking.

**FYI — reconcile the Step 3 "persisted/shared" wording with the in-memory inventory** so the implementer knows which is authoritative.

## Concurrency & State Safety Reviewer — APPROVE with suggestions (Confidence: HIGH)

F2 is resolved (see above); the launch-guard idempotency, single-writer-per-checkpoint claim (now genuinely true because the gate serializes fetching), and the full-restart recovery row are all sound. FIFO fairness + per-window (not per-job) gate release correctly prevent a long job from starving a short one.

**Suggestion — `resume_pending_jobs` running "at `batch` module import/init" is a side-effect-on-import hazard.** Spawning daemon threads that read `store/_batch/` and hit the network-adjacent fetch path merely because something imported `batch` will surprise tests and any tool that imports the module, and can race the explicit T5 startup call (the launch guard makes it *safe*, but not *predictable*). Recommend making resume an explicit call (T5 startup + `start_batch_fetch`'s first-run adopt) rather than bare import-time execution; keep import pure. Non-blocking but worth pinning before build.

**Suggestion — "FIFO waiters" is not free with a bare `threading.Lock`** (Python lock acquisition is not guaranteed fair). The implementer should use a `Condition`/ticket queue to deliver the FIFO guarantee the AC asserts. Call it out in Step 3 so it isn't built on an unfair primitive.

## Security & Trust Reviewer — APPROVE with suggestions (Confidence: HIGH)

F3 is resolved (see above); https-only + range-blocking + redirect-disable + pre-POST re-resolve + bare no-credential transport is the right shape, and callback anonymity is asserted by test.

**Suggestion — residual DNS-rebinding TOCTOU.** Re-resolving immediately before the POST narrows but does not close the gap: if the hostname is handed to the HTTP client, the client re-resolves at connect time, so a rebind between the validation resolve and the connect resolve can still land on an internal IP. The robust closure is to connect to the *validated IP* directly (pin the resolved address, set the `Host` header) rather than re-passing the hostname. Given single-dev/small-scale and a semi-trusted caller this is acceptable as documented residual risk, but recommend either IP-pinning or explicitly logging it as accepted debt. Non-blocking.

**Suggestion (carried from round 1, still open) — document the result payload's exposure surface.** The callback envelope may carry signed CDN `video_url`s (≈36 h TTL). Confirm no manifest columns beyond the intended top-N fields ride along and note that signed URLs are time-boxed and cross the trust boundary. Non-blocking.

## Tech Debt Sentinel — APPROVE (Confidence: HIGH)

The round-1 NEEDS-DISCUSSION driver is gone: the two load-bearing Open Questions (background-execution model, cross-job serialization) are now *resolved design decisions*, and only two genuinely-tunable constants remain (cooldown curve, `heartbeat_stale_s`) — both explicitly conservative-default + probe-tuned and non-blocking to the build. No new debt introduced beyond the two documented residuals above (in-memory gate cooldown across restart; DNS-rebind TOCTOU), which are appropriately small. The "T4 ships the gate, T5 wires the sync tools" split is clean and honestly scoped.

## Error Handling & Resilience Inspector — APPROVE (Confidence: HIGH)

The failure-mode table now includes the full-process-restart row, the SSRF-refused row, and the dead-worker row; result-durability-before-callback ordering is preserved; unknown-`job_id` returns a typed not-found. Orphan `*.tmp` sweep promoted from "optional" to a defined step (Step 2 + invoked in Step 9). No gaps.

## Data Integrity & Migration Reviewer — APPROVE (Confidence: HIGH)

Atomic temp+fsync+`os.replace` reuse for the checkpoint + result files; single-writer now genuinely guaranteed by the gate; aggregation reads only atomically-written manifests (old-or-new full file, never torn); dedupe + monotonic `media_id` watermark survive resume. No destructive cap. Clean.

## API & Contract Reviewer — APPROVE (Confidence: HIGH)

Round-1 suggestions folded in: the `start_batch_fetch` param contract is pinned in Step 1, and the single stable result/callback envelope (`results` always a handle-keyed map; `global` uses the reserved `"*"` key) gives the receiver one shape across scopes. `get_batch_status` liveness classification (fetching / cooldown-sleeping / dead-worker) + typed not-found round out the surface.

## Test Coverage Auditor — APPROVE (Confidence: HIGH)

All four round-1 gaps now have named tests: cross-job serialization (two acquirers never overlap, second start queues), full-process-restart resume (+ double-call idempotency), callback SSRF/redirect rejection, and unknown-`job_id`. Fake-clock discipline (zero real sleeps/IG hits in CI) is preserved; fault injection at every checkpoint boundary. One small add worth considering: an explicit assertion that after restart the *gate* starts cold and the first window's behavior is the intended one (ties to the Reliability suggestion) — optional.

## Naming & Clarity Guardian — APPROVE (Confidence: HIGH)

`FetchGate`, `resume_pending_jobs`, `_fill_handle`/`_run_job`/`_aggregate`/`_download_top`/`_post_callback`, `sleep_until` (explicitly "not a poll"), and the covered/partial/error classification all read cleanly and self-document. No concerns.

---

## Review Summary

| Reviewer | Verdict | Blocking | Suggestions | FYI | Confidence |
|---|---|---|---|---|---|
| Reliability / Rate-Limit | APPROVE | 0 | 1 | 1 | HIGH |
| Concurrency & State Safety | APPROVE | 0 | 2 | 0 | HIGH |
| Security & Trust | APPROVE | 0 | 2 | 0 | HIGH |
| Tech Debt Sentinel | APPROVE | 0 | 0 | 0 | HIGH |
| Error Handling & Resilience | APPROVE | 0 | 0 | 0 | HIGH |
| Data Integrity & Migration | APPROVE | 0 | 0 | 0 | HIGH |
| API & Contract | APPROVE | 0 | 0 | 0 | HIGH |
| Test Coverage Auditor | APPROVE | 0 | 1 | 0 | HIGH |
| Naming & Clarity | APPROVE | 0 | 0 | 0 | HIGH |

**Overall Recommendation: APPROVE**

**Rationale:** All three round-1 blocking findings are genuinely resolved in the design body, not deferred: cross-job serialization is now a foundational `FetchGate` step with a hard-invariant framing and a serialization test; restart-resume is a first-class idempotent `resume_pending_jobs` entrypoint with the primary success metric and integration test rewritten around a real full-process restart; and callback SSRF is hardened to https-only + IP-range blocking + redirect-disable + pre-POST re-resolve on a bare no-credential transport with a rejection test. The two remaining Open Questions are non-load-bearing tunable constants (cooldown curve, heartbeat-stale threshold) explicitly shipped with conservative defaults and probe-tuned. The plan is now build-ready. The surviving items are all non-blocking hardening notes.

**Blocking Items:** None.

**Top Suggestions (non-blocking, fold into build):**
1. **[Reliability]** Persist (or explicitly accept-as-debt) the gate's `cooldown_until`/`escalation_count` across restart — a cold gate after `resume_pending_jobs` can spend one metered call re-learning an active IP cooldown; also reconcile Step 3 "persisted/shared" wording with the in-memory Shared-State Inventory entry.
2. **[Concurrency]** Make `resume_pending_jobs` an explicit call (T5 startup + first `start_batch_fetch`) rather than bare module-import execution, to avoid side-effect-on-import surprises in tests/tools.
3. **[Concurrency]** Implement gate FIFO fairness with a `Condition`/ticket queue, not a bare unfair `threading.Lock`, so the interleaving AC holds.
4. **[Security]** Close (via IP-pinning to the validated address) or explicitly accept-as-debt the residual DNS-rebinding TOCTOU between the pre-POST resolve and the client's connect-time resolve; document the signed-CDN-URL exposure in the callback payload.

**Corroborated Findings (2+ reviewers):** The in-memory-gate-across-restart note is raised by Reliability and touched by Concurrency/Tech Debt as a shared residual — highest-signal of the non-blocking items, worth a one-line Risks entry.

**Accepted Debt:** Two probe-tunable constants (cooldown curve, `heartbeat_stale_s`) and, if not fixed, the two small residuals above (cold-gate-post-restart metered call; DNS-rebind TOCTOU) — all appropriate to carry with documentation.

— Review complete.
