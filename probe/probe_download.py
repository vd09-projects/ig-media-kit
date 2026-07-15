"""[LIVE PILOT — verify-by-pilot for T3 download_reel] — OPT-IN, not shipped in CI.

Confirms the load-bearing IG/fbcdn behaviours the offline unit tests can only
mock: that a real signed ``video_versions[0].url`` downloads over the CDN with
redirect-follow (a bare non-following GET returns 302/0 bytes) and lands a file
whose first box is a valid mp4 ``ftyp`` (tag at offset 4).

It drives the REAL orchestration end-to-end for ONE reel of ONE config channel:
list_reels (anonymous discovery, to populate the store if empty) -> download_reel
(cached gate / freshness / re-resolve / CDN download / atomic manifest write).

POLITENESS (CLAUDE.md — load-bearing):
  * DO NOT run this during a cooldown. The metadata API escalates its cooldown
    under abuse; polling while throttled EXTENDS it. If list_reels comes back
    with a stop_signal (partial + a "budget cooling" note), this pilot PRINTS it
    and EXITS — it does not retry, does not poll, does not sleep-and-hammer.
  * The CDN download itself is unmetered; only the metadata discovery is metered
    and is already stop_signal-guarded inside the tools.

Run:  python probe/probe_download.py [handle]     (defaults to the first config channel)

Exit 0 = a real mp4 with a valid ftyp landed on disk (or a clean, POLITE stop).
Exit 1 = a hard failure worth investigating (bad download, unexpected shape).
"""

from __future__ import annotations

import sys
from pathlib import Path

# Make the package importable when run as a bare script from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from ig_media_kit.config import load_config          # noqa: E402
from ig_media_kit.download import run_download_reel   # noqa: E402
from ig_media_kit.list_reels import run_list_reels    # noqa: E402
from ig_media_kit.store import Store                  # noqa: E402

FTYP_OFFSET = 4


def _pick_handle(config, argv: list[str]) -> str | None:
    if len(argv) > 1:
        return argv[1]
    return config.channels[0] if config.channels else None


def main() -> int:
    config = load_config()
    handle = _pick_handle(config, sys.argv)
    if not handle:
        print("ABORT: no handle given and config has no channels")
        return 1

    store = Store(config.output.store_dir)

    # 1) Ensure the store has at least one reel for this handle (metered path;
    #    stop_signal-guarded inside run_list_reels — it returns a partial, never
    #    polls). If discovery is throttled, STOP politely.
    if store.count_reels(handle) == 0:
        env = run_list_reels(handle, config=config, count=5)
        if env.get("partial"):
            print(f"POLITE STOP during discovery: {env.get('note')}")
            print("Do NOT retry immediately — the cooldown escalates under abuse.")
            return 0
        if store.count_reels(handle) == 0:
            print(f"no reels discovered for {handle!r}: {env.get('note')}")
            return 1

    # 2) Pick the newest stored shortcode and download it.
    from ig_media_kit.ranking import load_pool
    pool = load_pool(store.csv_path(handle))
    if not pool:
        print(f"store CSV for {handle!r} is empty after discovery")
        return 1
    pool.sort(key=lambda r: (r.get("media_id") or 0), reverse=True)
    shortcode = pool[0]["shortcode"]
    print(f"downloading reel {shortcode!r} for handle {handle!r} ...")

    result = run_download_reel(shortcode, config=config)
    print({k: result.get(k) for k in
           ("shortcode", "handle", "local_mp4", "cached", "refreshed",
            "partial", "stop_reason", "note")})

    if result.get("partial"):
        print("POLITE STOP during re-resolve — do NOT retry immediately.")
        return 0
    if result.get("error"):
        print(f"HARD FAILURE: {result['error']}")
        return 1

    path = Path(result["local_mp4"])
    if not path.exists() or path.stat().st_size == 0:
        print(f"HARD FAILURE: no file on disk at {path}")
        return 1
    head = path.read_bytes()[: FTYP_OFFSET + 4]
    if head[FTYP_OFFSET:FTYP_OFFSET + 4] != b"ftyp":
        print(f"HARD FAILURE: file at {path} is not a valid mp4 (no ftyp at "
              f"offset {FTYP_OFFSET}); first bytes = {head!r}")
        return 1

    print(f"OK: valid mp4 ftyp confirmed on disk at {path} "
          f"({path.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
