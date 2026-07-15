"""[SPIKE — THROWAWAY, NOT SHIPPED] Task T1.2 — verify-by-pilot probe gate.

Hits web_profile_info and /api/v1/feed/user/{id}/ anonymously (x-ig-app-id +
impersonate=chrome, NO auth cookies) against ONE public handle and records what
we rely on before building the fetch primitive on it:

  * user_id resolution from web_profile_info
  * feed items carry play_count / ig_play_count
  * product_type == "clips" for reels; carousel/image -> play_count null
  * the 12/page hard cap
  * next_max_id == "{media_id}_{user_id}" and the pk-vs-shortcode distinction
  * feed items are newest-first (pk monotonically decreasing)
  * the stop-signal family (401/403/429/302-to-login/200+challenge)
  * which benign cookies IG sets and whether the feed needs them

Run:  python probe/probe_spike.py [handle]

POLITE: one profile call + at most 2 feed pages, no retries, no polling. If IG
is unreachable / immediately throttled from this environment, it prints the
observed stop_signal and exits — it does NOT fake a result. See the build
artifact's "Probe outcome" for what actually happened in this session.
"""

from __future__ import annotations

import json
import sys

APP_ID = "936619743392459"
PROFILE_URL = "https://i.instagram.com/api/v1/users/web_profile_info/"
FEED_URL = "https://i.instagram.com/api/v1/feed/user/{uid}/"
AUTH_COOKIES = {"sessionid", "ds_user_id"}


def _assert_anonymous(cookies: dict) -> None:
    bad = {k for k in cookies if k.lower() in AUTH_COOKIES}
    if bad:
        raise SystemExit(f"ABORT: auth cookies present {bad} — anonymous-only invariant")


def main(handle: str = "natgeo") -> int:
    from curl_cffi import requests  # local import — spike only

    session = requests.Session()
    headers = {"x-ig-app-id": APP_ID, "Accept": "application/json"}
    findings: dict = {"handle": handle}

    # 1) resolve user_id
    r = session.get(PROFILE_URL, params={"username": handle}, headers=headers,
                    impersonate="chrome", allow_redirects=False)
    findings["profile_status"] = r.status_code
    findings["set_cookies"] = list(dict(getattr(r, "cookies", {}) or {}).keys())
    _assert_anonymous(dict(getattr(session, "cookies", {}) or {}))
    if r.status_code != 200:
        findings["stop_signal_observed"] = {"where": "profile", "status": r.status_code,
                                            "location": r.headers.get("Location")}
        print(json.dumps(findings, indent=2))
        return 0
    user = (((r.json() or {}).get("data") or {}).get("user") or {})
    uid = user.get("id")
    findings["user_id"] = uid
    if not uid:
        findings["error"] = "no user_id in web_profile_info"
        print(json.dumps(findings, indent=2))
        return 0

    # 2) page the feed (<=2 pages, polite)
    pages = []
    cursor = None
    for i in range(2):
        params = {"count": 12}
        if cursor:
            params["max_id"] = cursor
        fr = session.get(FEED_URL.format(uid=uid), params=params, headers=headers,
                         impersonate="chrome", allow_redirects=False)
        if fr.status_code != 200:
            findings["stop_signal_observed"] = {"where": f"feed_page_{i}", "status": fr.status_code,
                                                "location": fr.headers.get("Location")}
            break
        body = fr.json() or {}
        items = body.get("items") or []
        page = {
            "status": fr.status_code,
            "num_items": len(items),
            "next_max_id": body.get("next_max_id"),
            "more_available": body.get("more_available"),
            "sample": [
                {
                    "pk": it.get("pk"),
                    "code": it.get("code"),
                    "product_type": it.get("product_type"),
                    "play_count": it.get("play_count"),
                    "ig_play_count": it.get("ig_play_count"),
                    "has_video_url": bool((it.get("video_versions") or [])),
                }
                for it in items[:3]
            ],
        }
        pages.append(page)
        # newest-first check
        pks = [int(it["pk"]) for it in items if str(it.get("pk", "")).isdigit()]
        page["pks_descending"] = pks == sorted(pks, reverse=True)
        page["cursor_matches_last_pk"] = (
            bool(body.get("next_max_id")) and pks
            and str(body["next_max_id"]).split("_")[0] == str(pks[-1])
        )
        cursor = body.get("next_max_id")
        if not cursor or not body.get("more_available"):
            break
    findings["pages"] = pages
    print(json.dumps(findings, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "natgeo"))


# ---------------------------------------------------------------------------
# LIVE PROBE FINDINGS — captured 2026-07-15, handle=natgeo (durable record; the
# offline unit tests run against tests/fixtures/feed_sample.json, shaped to match).
# ---------------------------------------------------------------------------
# CONFIRMED (all relied-upon shapes observed live):
#   * web_profile_info -> data.user.id resolves anonymously: natgeo = 787132.
#   * Feed page returns HTTP 200 with 12 items -> the 12/page hard cap holds.
#   * Reels are product_type == "clips"; each carried play_count + ig_play_count
#     (e.g. DZpQwxqimz2 play_count=4,540,702) and a real video_versions[0].url
#     (has_video_url == true).
#   * next_max_id == "{media_id}_{user_id}" — "3939050233582227798_787132"; its
#     left half equals the last item's pk (cursor_matches_last_pk == true).
#   * pk (numeric) and code/shortcode (opaque) are DISTINCT fields, as relied on.
#   * Anonymous cookies IG set on the first hit: csrftoken, mid, ig_did, ig_nrcb.
#     NO auth cookie (sessionid/ds_user_id) appeared. Feed worked carrying these
#     benign cookies -> anonymity guard keys off auth cookies only (Finding 3).
#   * STOP-SIGNAL FAMILY (Finding 1), observed LIVE: the SECOND feed page in the
#     same short window returned HTTP 401 with no Location -> exactly the
#     rate_limited stop_signal the classifier maps 401 -> RATE_LIMITED. This is
#     the metadata IP-rate-limit kicking in (~48/6.6min, escalating) — confirms
#     both the throttle status AND why the sync path must stop-and-return-partial
#     on the first stop_signal rather than poll.
# FLAGGED (round-2-anticipated deviation — feed NOT strictly newest-first):
#   * pks_descending == FALSE. Sampled pks: 3920.. , 3900.. , 3941.. — the third
#     reel was CREATED AFTER the first two yet appears below them => natgeo PINS
#     reels; pinned (older, smaller-pk) reels sit ABOVE newer ones. Consequence:
#     the "first-known == caught-up" premise is weakened for pinned accounts. The
#     PRIMARY membership stop and the SECONDARY watermark both remain correct for
#     the caught-up==1-page invariant, but "new reels below a pinned block" can be
#     under-collected. Handled transparently: code marker in fetch._consume_page +
#     a discovered followup ticket (do NOT weaken the short-circuit to fix it).
# NOT observed this session: a 302-to-login redirect and a 200-with-challenge
#   body (we hit the 401 rate-limit first). The classifier covers them by the
#   CLAUDE.md-documented shapes and fails closed on anything unrecognized.

