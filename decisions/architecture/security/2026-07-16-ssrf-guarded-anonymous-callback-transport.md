# SSRF-guarded anonymous callback transport

| Field    | Value            |
|----------|------------------|
| Date     | 2026-07-16       |
| Status   | accepted         |
| Category | architecture/security |
| Tags     | callback, ssrf, anonymous, transport, result-durability, dns-rebind, egress, t4 |

## Context

T4's batch runner can notify a caller-supplied URL when a job finishes. This is the
only outbound egress in the system that is NOT an IG/CDN request, and a
caller-supplied URL is an SSRF vector. Two invariants had to be protected: (1) the
project's anonymous-only rule — the callback must carry none of the IG identity
surface — and (2) job results must not be lost if the callback fails.

## Options considered

### Option A: Reuse the IG transport, fire callback and treat delivery as "done"
- **Pros**: one HTTP client; less code.
- **Cons**: the IG transport carries `x-ig-app-id` (and could carry cookies) —
  leaking identity surface to an arbitrary caller URL, violating anonymous-only. And
  if "done" depends on callback success, a failed/unreachable callback loses the
  result.

### Option B: Separate hardened transport; persist result before any callback
- **Pros**: no IG headers on the egress; SSRF-guarded; result is durable
  independent of delivery.
- **Cons**: a second HTTP path to maintain.

## Decision

The callback POST uses a **separate, non-IG transport**: no `x-ig-app-id`, no
cookies — bare and anonymous. It is SSRF-guarded: it requires `https`, blocks
private / link-local / loopback / cloud-metadata IP ranges, **pins the connection to
the validated IP** to defeat DNS-rebind TOCTOU, and disables redirect-following (so
a redirect can't bounce the request to a blocked target). Separately, the aggregated
job result is persisted to `result.json` **before any callback attempt**, so a job
being "done" never depends on callback success. Delivery is at-least-once,
best-effort; `get_batch_status` is the durable fallback for a caller that never
received (or missed) its callback.

## Consequences

- The callback is best-effort by design — callers must not assume exactly-once
  delivery and should be able to reconcile via `get_batch_status`.
- Result durability and notification are fully decoupled: a callback outage never
  costs data.
- The SSRF guard rejects some legitimate-looking targets (anything on a private
  range, plain http, redirecting endpoints) — accepted as the safe default for a
  caller-supplied URL.

## Related decisions

- [Daemon-thread batch runner with explicit resume](../2026-07-16-daemon-thread-batch-runner-with-explicit-resume.md) — the background job whose completion this callback signals.

## Revisit trigger

If callbacks ever need to target internal services (a legitimate private-range
destination) or delivery must become guaranteed, the block-private-ranges default
and the best-effort delivery model both need to be revisited.
