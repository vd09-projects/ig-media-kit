---
artifact_type: handoff
artifact_version: 2
producer_role: review
consumer_role: none
plan_type: findings
slug: t4-async-batch-runner
scope_hint: T4 async batch runner — start_batch_fetch + get_batch_status + callback
canonical_name: review-findings
overlays: [concurrency]
status: draft
version: 1
created: 2026-07-16T21:56:14Z
updated: 2026-07-16T21:56:14Z
prior_versions: []
---

# Review findings: T4 async batch runner — start_batch_fetch + get_batch_status + callback

## Triage Decision
Scope: large (greenfield subsystem, concurrency overlay, cross-cutting)
Partition: backend
Review type: PLAN (round 1) — no diff; findings target design gaps and unresolved decisions
Memory overrides: always_include Reliability / Rate-Limit Reviewer; hard-stop rule on any auth/cookie/account path

Selected Reviewers:
- Reliability / Rate-Limit Reviewer (backend) — politeness invariant is load-bearing; the runner is the only sleeper
- Concurrency & State Safety Reviewer (backend) — single-IP serialization, detached worker lifecycle, checkpoint races
- Security & Trust Reviewer (common) — anonymity on the one non-IG path; caller-supplied callback URL (SSRF)
- Error Handling & Resilience Inspector (common) — crash/resume recovery, callback retry semantics
- Data Integrity & Migration Reviewer (backend) — checkpoint atomicity, store dedupe/watermark under resume
- API & Contract Reviewer (backend) — entrypoint surface, result/callback payload schema
- Test Coverage Auditor (common) — fault-injection + anonymity + serialization coverage
- Tech Debt Sentinel (common, baseline) — unresolved open questions blocking foundational steps
- Naming & Clarity Guardian (common, baseline)

Skipped: Frontend/CSS/A11y/State-Management (no UI); Infrastructure (no manifests); Domain Logic (reused unchanged from T2/T3); Observability (light — noted as suggestion).

---

## Reliability / Rate-Limit Reviewer — REQUEST CHANGES (Confidence: HIGH)

**BLOCKING — Cross-job fetch serialization is left as an Open Question, but parallel IG fetch is a counter-metric violation.** Step 8 launches a detached worker per `start_batch_fetch` and there is no process-wide fetch lock in the *design* — only in Open Question 2. If a second `start_batch_fetch` arrives while one job runs (or the same call is retried), two daemon threads hit the single IP concurrently → cooldown escalation (6.6→13 min, budget 48→36→12), which is exactly the "no politeness regression" counter-metric. This cannot ship as an open question; the serialization mechanism (a global fetch mutex or a single job-runner queue) must be part of the foundational design (Steps 1/4/8), and Step 8 must define what the second caller gets back (queued-behind vs rejected).

**Suggestion — cooldown duration source is underspecified.** The plan says "sleep the observed window + margin" and "treat a repeat immediate 401 as escalation → longer sleep," but `run_window` is synchronous/no-sleep and it is not stated that `WindowOutcome.stop_reason` carries an observed cooldown / `Retry-After` or that the escalation counter is persisted in `HandleProgress`. Specify where the escalating-backoff state lives and what the runner reads to size the sleep, otherwise the "only sleeper" guesses blindly and may under-wait (which re-triggers 401 and extends the cooldown).

**FYI — re-fetch of the stale window spends metered budget.** Checkpoint lags the store write by one window (see Data Integrity), so resume re-fetches the in-flight page. Dedupe makes it *correct*, but it still costs one metered IG call against a tight budget. Acceptable; just name it.

## Concurrency & State Safety Reviewer — REQUEST CHANGES (Confidence: HIGH)

**BLOCKING — kill/restart resume is the PRIMARY success metric, but there is no entrypoint to resume an existing `job_id`.** `start_batch_fetch` "creates + checkpoints a *new* queued job." After a full process restart (the daemon thread dies with the server — acknowledged in Risks, and "cross-restart auto-resume daemon" is explicitly Out of Scope), nothing re-attaches to the on-disk checkpoint. So the durable checkpoints exist but are unreachable: `get_batch_status` will report a stale `fetching` phase for a job whose worker is dead, with no API to continue it. This is a direct contradiction between the success metric ("resumes from last checkpoint and completes") and the API surface. Resolve by either (a) adding a `resume`/`run_start_batch_fetch(job_id=...)` path that re-attaches a checkpoint, or (b) an at-start sweep of `store/_batch/` that relaunches non-terminal jobs — and narrow the success metric to what the chosen model actually delivers.

**Suggestion — `get_batch_status` cannot distinguish "actively fetching" from "sleeping in cooldown" from "worker dead."** `phase: fetching` covers all three. Surface `sleep_until` and a worker-liveness/heartbeat (e.g., `updated` freshness) so a caller can tell a healthy cooldown from a silently-dead thread.

**FYI — single-writer claim holds only under the cross-job lock.** The "single writer to the checkpoint / same handle csv" invariant is asserted, but it is only true once Open Question 2 is resolved; two jobs touching overlapping handles would break it. Ties back to the Reliability blocker.

## Security & Trust Reviewer — REQUEST CHANGES (Confidence: HIGH)

**BLOCKING (per project hard-stop rule scope) — callback SSRF is deferred to an Open Question, but the callback URL is caller-supplied and the process holds an IG-reputation IP.** Posting to an arbitrary URL from the server is an SSRF surface (cloud metadata `169.254.169.254`, link-local, internal hosts). For an MCP tool whose caller may be an autonomous agent, this should be resolved *now*, not deferred: enforce `https`, block private/link-local/metadata ranges, and **disable redirect-follow on the callback** (a 3xx could bounce the POST to an internal host — or back to `instagram.com`, re-entangling anonymity). The anonymity design (separate bare transport, no `x-ig-app-id`/cookies) is correct and well-tested; the redirect gap is the missing piece.

**Suggestion — define what the result payload exposes.** `result.json` posted to the callback may contain signed CDN `video_url`s (36 h TTL). Confirm no manifest columns beyond the intended top-N fields ride along, and document that signed URLs are time-boxed and leave the trust boundary.

## Error Handling & Resilience Inspector — APPROVE with suggestions (Confidence: HIGH)

The failure-mode table is genuinely strong: crash-mid-window, between-handles, post-aggregate, mid-download, mid-callback each have a detection + recovery path, and result-durability-before-callback is correctly ordered. Callback at-least-once + `job_id` for receiver dedupe is the right semantics.

**Suggestion — `get_batch_status(unknown/garbage job_id)` behavior is unspecified.** Plan says both entrypoints "return typed envelopes, never raise" — add an explicit not-found envelope case and a test for it.

**Suggestion — orphan `*.tmp` sweep is called "optional."** With crashes as a first-class concern, promote the tmp sweep on start to a defined step so `_batch/` doesn't accrete torn temp files over time.

## Data Integrity & Migration Reviewer — APPROVE (Confidence: HIGH)

Atomic temp+fsync+`os.replace` reuse for the checkpoint is the correct pattern and matches `_write_state_atomic`. Resume idempotency via per-shortcode dedupe + monotonic `media_id` watermark is sound and inherits T2's discipline; aggregation reading only persisted manifests is order-safe by construction. No destructive cap — honored. No concerns; the one-window checkpoint lag is correct-but-costly (noted by Reliability).

## API & Contract Reviewer — Suggestions (Confidence: MED)

**Suggestion — the callback/result payload schema differs by `scope`** (`global` → a flat top-N list; `per_channel` → a dict keyed by handle). Define one stable envelope (e.g., `{scope, results, per_handle_status, job_id}`) so the receiver isn't branch-parsing two shapes.

**Suggestion — pin the `start_batch_fetch` param contract** (top-N `count` name, `sort_by`, `filters`, `download_top`, `callback_url`) and its validation errors in Step 8's AC; currently only `scope`/`sort_by`/URL-shape/handle-subset validation is named.

## Test Coverage Auditor — Suggestions (Confidence: HIGH)

Coverage is broad and behavior-anchored (fault injection at every checkpoint boundary, fake clock, callback-anonymity assertion, atomic-write fault test). Gaps:
- **No test for the second-`start_batch_fetch`/cross-job serialization** — because the mechanism is unresolved. Add once Open Question 2 lands; it guards the single-IP invariant directly.
- **No restart-with-dead-worker test** (relaunch process → resume existing job) — required to actually prove the primary success metric, not just an in-process reload.
- **No callback-redirect / SSRF-rejection test** — add alongside the Security fix.

## Tech Debt Sentinel — NEEDS DISCUSSION (Confidence: HIGH)

Two of the five Open Questions (background-execution model, cross-job serialization) are not cosmetic — they are load-bearing for the concurrency correctness and the politeness counter-metric, and the Handoff Notes admit "three of them block foundational steps." Deferring them into implementation risks a re-plan (the plan itself flags "re-plan if the background-execution decision invalidates checkpoint/resume"). These should be decided at plan sign-off, not discovered in Step 8. The remaining three (config block shape, checkpoint JSON/YAML, callback policy) are fine to settle early in Step 1 — though callback policy is upgraded to blocking by Security.

## Naming & Clarity Guardian — APPROVE (Confidence: HIGH)

Phase enum, `HandleProgress`, `sleep_until` (explicitly "not a poll"), covered/partial/typed-error classification are all clear and self-documenting. `_fill_handle` / `_run_job` / `_aggregate` / `_download_top` / `_post_callback` read cleanly. No concerns.

---

## Review Summary

| Reviewer | Verdict | Blocking | Suggestions | FYI | Confidence |
|---|---|---|---|---|---|
| Reliability / Rate-Limit | REQUEST CHANGES | 1 | 1 | 1 | HIGH |
| Concurrency & State Safety | REQUEST CHANGES | 1 | 1 | 1 | HIGH |
| Security & Trust | REQUEST CHANGES | 1 | 1 | 0 | HIGH |
| Error Handling & Resilience | APPROVE | 0 | 2 | 0 | HIGH |
| Data Integrity & Migration | APPROVE | 0 | 0 | 0 | HIGH |
| API & Contract | SUGGEST | 0 | 2 | 0 | MED |
| Test Coverage Auditor | SUGGEST | 0 | 3 | 0 | HIGH |
| Tech Debt Sentinel | NEEDS DISCUSSION | 0 | 1 | 0 | HIGH |
| Naming & Clarity | APPROVE | 0 | 0 | 0 | HIGH |

**Overall Recommendation: REQUEST CHANGES**

**Rationale:** The plan is thorough, invariant-aware, and unusually strong on failure-mode enumeration, checkpoint atomicity, resume idempotency, and callback anonymity/durability decoupling — those are effectively approve-grade. But three design gaps are load-bearing enough to fix before build, and two of them stem from the same root: concurrency correctness is parked in Open Questions rather than decided. (1) Cross-job fetch serialization is deferred, yet parallel IG fetch is precisely the politeness counter-metric — the design needs a process-wide fetch lock/queue, not an open question. (2) Kill/restart resume is the PRIMARY success metric, but no entrypoint re-attaches an existing `job_id` after a process restart (the daemon thread dies with the server, and auto-resume is Out of Scope) — the durable checkpoints are unreachable, so the metric is unmeetable as specified. (3) The caller-supplied callback URL is an SSRF surface with redirect-follow unaddressed; enforce https + block private/metadata ranges + disable callback redirects now. None of these are code-level nits — they are plan-level decisions the panel cannot resolve unilaterally, hence REQUEST CHANGES bordering on NEEDS DISCUSSION.

**Blocking Items:**
1. **[Reliability + Tech Debt]** Move cross-job fetch serialization from Open Question into the design — a process-wide fetch mutex/single-runner queue; define second-caller behavior in Step 8.
2. **[Concurrency]** Add a resume path for an existing `job_id` (explicit `resume`/`start(job_id=...)` or an at-start sweep that relaunches non-terminal checkpoints), or narrow the primary success metric to in-process reload; today the restart-resume metric has no API to trigger it.
3. **[Security]** Resolve callback URL safety now: require https, block private/link-local/metadata IPs, and disable redirect-follow on the callback POST.

**Top Suggestions:**
1. Specify where the observed/escalating cooldown duration comes from (`WindowOutcome.stop_reason` field + persisted escalation counter in `HandleProgress`) so the only-sleeper sizes its sleep from data, not a guess.
2. Make `get_batch_status` distinguish actively-fetching vs cooldown-sleeping (`sleep_until`) vs dead-worker (heartbeat/`updated` freshness).
3. Define one stable callback/result envelope across `global` and `per_channel` scopes.
4. Add tests for second-`start_batch_fetch` serialization, full-process-restart resume, callback-redirect/SSRF rejection, and `get_batch_status(unknown job_id)`.
5. Promote the orphan `*.tmp` sweep on start from "optional" to a defined step.

**Corroborated Findings (2+ reviewers — act first):**
- Cross-job serialization gap — Reliability + Concurrency + Tech Debt + Test Coverage.
- Resume-entrypoint / dead-worker contradiction — Concurrency + Test Coverage (+ success-metric contradiction).

**Accepted Debt:** Config block shape and checkpoint JSON/YAML are fine to settle early in Step 1 (non-blocking). Callback-signing/auth remains a legitimate deferral once SSRF hardening lands.
