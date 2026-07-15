# Process-wide FetchGate single-IP serialization

| Field    | Value            |
|----------|------------------|
| Date     | 2026-07-16       |
| Status   | accepted         |
| Category | architecture/concurrency |
| Tags     | fetchgate, concurrency, serialization, single-ip, rate-limit, batch, t4 |

## Context

T4 introduces the async batch runner, which fans out fetch work across multiple
handles and can run concurrently with the synchronous `list_reels` / `download_reel`
tools. All of these paths hit Instagram from a single host IP. The metadata API
(`web_profile_info` → `/api/v1/feed/user/{id}/`) is IP-rate-limited to roughly 48
items per ~6.6-minute window, and the cooldown **escalates under abuse** (measured
6.6→13 min; budget 48→36→12). Parallel fetching on one IP therefore buys no
throughput — the shared budget is the ceiling regardless of worker count — while
actively raising the risk of tripping and extending the escalating cooldown.

## Options considered

### Option A: Per-worker / per-handle pacing, no global coordination
- **Pros**: simplest to write; each worker self-paces.
- **Cons**: multiple workers can open overlapping IG windows on the same IP,
  blowing the shared per-IP budget and triggering escalation. Pacing is a
  per-worker property but the rate limit is a per-IP property — the two don't line up.

### Option B: Process-wide FetchGate singleton, at most one IG window in flight
- **Pros**: matches the enforcement boundary (per-IP) to the coordination boundary
  (per-process, one IP); FIFO-fair so no worker starves; a single place to attach
  cooldown/back-off state.
- **Cons**: no fetch parallelism — but on a single IP there is no parallelism to
  win anyway.

## Decision

All IG-hitting work in the process is serialized through one module-level
`FetchGate` singleton: at most one IG window is in flight at any time, admission is
FIFO-fair. The batch windows go through it now; the synchronous `list_reels` and
`download_reel` tools are wrapped through the same gate once T5 lands. Because the
rate limit is enforced per-IP and the process holds exactly one IP, serializing all
fetches through a single gate costs nothing in throughput and removes the only way
concurrent callers could collide on the shared budget and trigger escalation.

## Consequences

- Fetch work is effectively single-lane process-wide; CDN downloads (`fbcdn.net`,
  unmetered) are deliberately NOT gated and remain free to parallelize.
- The gate becomes the natural home for cooldown persistence and metered-stop
  back-off (see the paired reliability decision) — one chokepoint to make the
  politeness invariant atomic.
- T5 must route the sync tools through the same singleton or the single-lane
  guarantee is only partial.

## Related decisions

- [Persisted cooldown; in-gate metered-stop](reliability/2026-07-16-persisted-cooldown-in-gate-metered-stop.md) — the gate is where cooldown state and metered-stop back-off are made atomic and durable.

## Revisit trigger

If the fetch architecture ever moves off a single egress IP (e.g. a proxy pool or
multiple source IPs), the "one window process-wide" premise no longer holds and the
gate should become per-IP rather than per-process.
