# Ship AC#4 as an offline fixture smoke harness; defer the live IG pass to pilots #10/#14

| Field    | Value            |
|----------|------------------|
| Date     | 2026-07-16       |
| Status   | accepted         |
| Category | architecture/testing |
| Tags     | smoke-harness, fixture, dry-run, zero-ig, deferred-live, escalating-cooldown, test-seam, t5 |

## Context

T5's success metric includes an end-to-end smoke that exercises the wiring (`list_reels` → `download_reel` → `start_batch_fetch` + callback → `get_batch_status`). But the build IP is subject to IG's *escalating* cooldown (measured 6.6→13 min; budget 48→36→12 under abuse), and politeness is a load-bearing invariant. Running a live IG smoke in CI or by default would burn the shared metadata budget on every run and risk escalating the cooldown for real fetches. A green smoke must not depend on — or fake — a live IG hit.

## Options considered

### Option A: Fixture/dry-run smoke by default, zero IG network; live procedure documented + opt-in, deferred to pilots #10/#14
- **Pros**: Exercises the whole wired surface offline (injected sample feed, stubbed ftyp CDN body, real localhost callback sink via an injected `poster` seam); a process-global guard makes constructing a *real* transport a hard failure, so zero-IG is enforced not merely intended; CI stays green and budget-free; live validation happens once, deliberately, when de-risked.
- **Cons**: The live path is genuinely unverified at ship time — the surface's real-IG behavior rests on the deferred pilots, not this PR.

### Option B: Live smoke in CI / default
- **Pros**: Real end-to-end proof.
- **Cons**: Burns the ~48-item metered budget every run; risks escalating the cooldown against real fetches; flaky (IG-dependent); violates "never poll during cooldown"; can't run in a clean CI sandbox.

### Option C: Loosen the SSRF/https callback guard to accept `http://localhost` for the harness
- **Pros**: Simpler local callback delivery.
- **Cons**: Relaxes a production security guard for test convenience — rejected; the localhost sink is injected as a `poster` test seam instead, leaving `validate_callback_url` and the production transport unchanged.

## Decision

Ship AC#4 as a runnable fixture/dry-run smoke harness (`probe/probe_smoke.py` + `tests/test_smoke.py`) that drives the full wiring with **zero IG network**, enforced by a guard that turns any real-transport construction into a hard `AssertionError` plus a final `zero_ig_network` assertion. A simulated mid-fetch 401 is driven through the shared server context to prove `partial=True` + `stop_reason=rate_limited` + stop-on-first-401 through the wired path (not just the pre-existing unit). The localhost callback receiver is reached via an injected `poster` seam — the production `https`/SSRF guard is *not* loosened. The live procedure (real handle, real CDN) is documented and gated behind explicit opt-in (`IG_MK_SMOKE_LIVE=1`), never runs by default or in CI, and is explicitly deferred to pilot tickets #10/#14 — no passing live result is fabricated. This upholds the "verify by pilot" convention: the live claim is confirmed by a real probe, just later and deliberately.

## Consequences

- CI proves the *wiring* (list→download→batch→callback + politeness stop) offline, deterministically, at zero budget cost.
- The real-IG behavior of the shipped surface is an open, tracked deferral until #10/#14 run the opt-in live pass — a known gap, not a hidden one.
- Future smoke additions inherit the injected-seam pattern (no guard relaxation for test convenience).

## Related decisions

- [SSRF-guarded anonymous callback transport](../security/2026-07-16-ssrf-guarded-anonymous-callback-transport.md) — the production guard the harness reaches around via a test seam, not by relaxing it.
- [Daemon-thread batch runner with explicit resume](../2026-07-16-daemon-thread-batch-runner-with-explicit-resume.md) — the batch path the harness drives against a temp store.

## Revisit trigger

When pilots #10/#14 run the live pass — fold any divergence between fixture behavior and real IG behavior back into the harness/fixtures.
