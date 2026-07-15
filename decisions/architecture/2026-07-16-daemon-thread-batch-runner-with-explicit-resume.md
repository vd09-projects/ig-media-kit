# Daemon-thread batch runner with explicit resume

| Field    | Value            |
|----------|------------------|
| Date     | 2026-07-16       |
| Status   | accepted         |
| Category | architecture     |
| Tags     | batch, daemon-thread, checkpoint, resume, no-broker, flat-file, job, t4 |

## Context

T4's `start_batch_fetch` must return a job handle immediately and run a
possibly-hours-long fetch (many handles, paced windows, escalating cooldowns) in the
background, and it must survive a full process restart without losing or corrupting
in-flight work. The stack is deliberately flat-file / no-DB, so heavyweight options
(a task broker, a message queue, a persistent worker daemon) are out of character
for the project.

## Options considered

### Option A: External broker / queue (Celery, RQ, etc.)
- **Pros**: mature retry/resume semantics.
- **Cons**: a whole new dependency and runtime (broker + worker process) against a
  no-DB, flat-file project. Massive over-fit.

### Option B: Continuously-running auto-watcher daemon that resumes orphans
- **Pros**: automatic recovery with no caller involvement.
- **Cons**: an always-on orphan process to supervise, and resume-on-import
  side-effects make the module unsafe to import for tests/tools.

### Option C: Daemon thread + durable per-window checkpoint + explicit resume sweep
- **Pros**: `start_batch_fetch` returns a `job_id` instantly; work runs on a daemon
  thread; each completed window is checkpointed to disk so a restart resumes from the
  last durable point; recovery is an explicit, caller-invoked sweep — no orphan
  process, no import side-effect. Fits the flat-file model.
- **Cons**: recovery only happens when something calls the resume sweep — it is not
  automatic on crash.

## Decision

`start_batch_fetch` returns a `job_id` instantly and runs `_run_job` on a daemon
thread. Progress is made durable by a per-window checkpoint, so a partial run is
never lost. A full process restart is recovered by an **explicit**
`resume_pending_jobs(config)` sweep — called by T5 startup and by the first
`start_batch_fetch` — **not** by a module-import side-effect and **not** by a
continuously-running orphan watcher. This is the simplest resume-safe model
consistent with the flat-file, no-DB stack: no broker, no always-on daemon, and no
surprising import-time behavior.

## Consequences

- Recovery is deterministic and caller-controlled: nothing resumes until
  `resume_pending_jobs` runs. If neither T5 startup nor a new `start_batch_fetch`
  ever runs, a crashed job stays pending on disk (acceptable — it is durable and
  will resume on next entry).
- Daemon threads die with the process, which is why the per-window checkpoint (not
  in-memory progress) is the source of truth for resume.
- Importing the batch module has no side effects, so it stays safe to import from
  tests and other tools.

## Related decisions

- [Process-wide FetchGate single-IP serialization](concurrency/2026-07-16-process-wide-fetchgate-single-ip-serialization.md) — the daemon thread's fetch windows go through the shared gate.
- [SSRF-guarded anonymous callback transport](security/2026-07-16-ssrf-guarded-anonymous-callback-transport.md) — how a finished background job notifies its caller.

## Revisit trigger

If jobs ever need to survive across machines, or multiple runner processes must
coordinate on the same job set, the single-process daemon-thread + local-file
checkpoint model needs to be revisited.
