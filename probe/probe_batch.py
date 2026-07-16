"""Manual-only live pilot for the T4 async batch runner — NOT run in CI.

Verify-by-pilot (CLAUDE.md): any claim about IG behaviour is confirmed by a real
probe. This exercises one real batch over >= 3 configured handles against live
Instagram — anonymously, through the same paced/serialized path CI exercises with
fakes — and prints the terminal state + top-N so the escalation-curve and
heartbeat-stale constants in the ``batch:`` config block can be tuned.

Run it by hand (it sleeps out real IG cooldowns and is the ONLY sleeper):

    IG_MK_CONFIG=./config.yaml python -m probe.probe_batch handleA handleB handleC

It will:
  * start a real background batch (``scope=global``, top 5 by play_count),
  * poll ``get_batch_status`` every few seconds WITHOUT hitting IG (status is a
    pure checkpoint read — safe during a cooldown),
  * print phase / liveness transitions until the job reaches ``done``.

Nothing here is imported by the package or the test suite. The callback is left
unset by default (best-effort delivery is exercised separately); pass --callback
to point at an https collector you control.

MANUAL ONLY — never wire this into CI (it makes live, rate-limited IG calls).
"""

from __future__ import annotations

import argparse
import time

from ig_media_kit.batch import (
    resume_pending_jobs, run_get_batch_status, run_start_batch_fetch,
)
from ig_media_kit.config import load_config


def main() -> None:
    ap = argparse.ArgumentParser(description="Live pilot for the T4 batch runner.")
    ap.add_argument("handles", nargs="*", help="handles to batch (else config channels[])")
    ap.add_argument("--scope", default="global", choices=["global", "per_channel"])
    ap.add_argument("--count", type=int, default=5)
    ap.add_argument("--sort-by", default="play_count")
    ap.add_argument("--download-top", action="store_true")
    ap.add_argument("--callback", default=None, help="https callback URL (SSRF-guarded)")
    ap.add_argument("--config", default=None)
    ap.add_argument("--poll-seconds", type=float, default=5.0)
    args = ap.parse_args()

    config = load_config(args.config)

    # Re-adopt any orphaned jobs from a previous run first (explicit, not on import).
    resume_pending_jobs(config)

    started = run_start_batch_fetch(
        config=config,
        handles=args.handles or None,
        scope=args.scope,
        count=args.count,
        sort_by=args.sort_by,
        download_top=args.download_top,
        callback_url=args.callback,
    )
    if not started.get("ok"):
        print(f"start rejected: {started.get('note')}")
        return
    job_id = started["job_id"]
    print(f"started job {job_id} ({args.scope}) — polling every {args.poll_seconds}s")

    last = None
    while True:
        status = run_get_batch_status(job_id, config=config)
        line = f"phase={status['phase']} liveness={status.get('liveness')}"
        if status.get("sleep_until"):
            line += f" sleep_until={status['sleep_until']}"
        if line != last:
            print(f"  {line}  per_handle={status.get('per_handle')}")
            last = line
        if status["phase"] in ("done", "failed"):
            break
        time.sleep(args.poll_seconds)

    result = run_get_batch_status(job_id, config=config).get("result") or {}
    print(f"\nterminal phase: {status['phase']}")
    print(f"per_handle_fetch: {result.get('per_handle_fetch')}")
    for key, reels in (result.get("results") or {}).items():
        print(f"\ntop-{args.count} [{key}]:")
        for r in reels:
            print(f"  {r.get('shortcode'):<16} play_count={r.get('play_count')}")


if __name__ == "__main__":
    main()
