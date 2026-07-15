---
artifact_type: handoff
artifact_version: 2
producer_role: review
consumer_role: implementation
plan_type: findings
slug: fetch-engine-store-foundation
scope_hint: fetch-engine-store-foundation — T1 build review (round 1)
canonical_name: review-findings
review_round: build-review-1
overlays: []
status: draft
version: 1
created: 2026-07-15T13:20:00Z
updated: 2026-07-15T13:20:00Z
prior_versions: []
---

# Review findings: fetch-engine-store-foundation — T1 build (round 1)

Multi-perspective review of the from-scratch T1 foundation build
(`implementation-build.md` v1) against the approved plan (`planner-task.md` v3)
and the project's load-bearing invariants. This is the **BUILD** review, distinct
from the earlier plan review (`review-findings-plan.md`).

**Summary:** The build is high quality and faithfully implements every load-bearing
invariant the plan flagged. All 43 offline tests pass (`pytest -q` re-run confirmed:
`43 passed in 0.06s`). The round-2 shortcode-ordering bug is correctly avoided
(membership + numeric watermark, no `<=` on shortcodes). Anonymity, durable-first
persistence, atomic state writes, the fail-closed stop_signal classifier, the
`pages_fetched == 1` short-circuit, and no-sleep-on-sync are all present and
tested. **No blocking findings.** Recommendation: **APPROVE**. Six non-blocking
suggestions and one tracked accepted-debt item (the pinned-reel follow-up) below.

## Triage Decision

Scope: **medium** (multi-module greenfield: config/http/fetch/store/window/server + tests)
Partition: **backend** (Python, flat-file store, external API client)
Memory overrides: `always_include` Reliability / Rate-Limit Reviewer; `always_exclude` Accessibility. Custom rule fired: change touches an IG-hitting code path → Reliability reviewer included, scope treated as ≥ medium.

Selected reviewers:
- Reliability / Rate-Limit Reviewer (common, project-custom) — politeness invariant is load-bearing
- Security & Trust Reviewer (common) — anonymity / auth / secrets
- Error Handling & Resilience Inspector (common) — stop_signal, partial failure, transport failures
- Data Integrity & Migration Reviewer (backend) — durable-first CSV/YAML, atomic rename
- Domain Logic Reviewer (backend) — plan adherence, top_scan semantics, pinned-reel deviation
- Test Coverage Auditor (common) — assertion quality, missing paths
- Tech Debt Sentinel (common, baseline) — the TODO marker
- Naming & Clarity Guardian (common, baseline)

Skipped: Accessibility (no UI — excluded by memory); Concurrency & State Safety (single-process, single-threaded sync path — no shared mutable state across threads); API/Contract, Backward-Compat (greenfield, no consumers yet); Performance Critic (metered path is deliberately bounded; not perf-sensitive); Infra/Deployment (no infra changes).

---

## Reliability / Rate-Limit Reviewer — "Every extra page is someone else's shared IP burning."

**Verdict:** Suggestions

The politeness contract is implemented correctly and is the strongest part of the
build. `x-ig-app-id: 936619743392459` is injected via `API_HEADERS` on every
metadata call (`http_client.get_api`), and both IG-hitting callers
(`resolve_user_id`, `_get_feed_page`) route through it — CDN (`get_cdn`) correctly
omits it. The stop_signal classifier (`classify_response`) covers the whole family
(401/429→`rate_limited`, 403→`forbidden`, 3xx→login/challenge/unknown by inspecting
the redirect target rather than following it, 200+challenge/login body→stop) and
**fails closed** — any unclassifiable non-feed response becomes `stop(unknown)`
(`http_client.py:196`). `fetch_window` stops on the FIRST stop_signal, returns a
partial with cursor + newest-id intact, sets no sleep and does no poll
(`fetch.py:260-268`). The `<=4 pages/call` cap is enforced by the `for page_index in
range(max_pages)` loop with `max_pages` sourced from `config.fetch.max_pages_per_call`
(default 4). The sync path passes `sleep=None`, so `fetch_window` never sleeps
(`fetch.py:250`, `window.py:82`) — verified by `test_fetch_never_sleeps_on_sync_path`.

The round-2 regression is genuinely fixed: the top_scan stop condition is seen-set
MEMBERSHIP (`shortcode in seen_set`) plus a numeric `media_id <= high_water_media_id`
watermark — no lexical comparison on shortcodes anywhere (`fetch._consume_page:342-355`).
`test_caught_up_short_circuits_on_membership_page1` and `_on_watermark_page1` prove
`pages_fetched == 1`. The caught-up short-circuit is **sound as shipped**.

**Issues Found:**
- [SUGGESTION] `fetch_window` maps a 5xx `Outcome.ERROR` to `stop_reason =
  StopReason.UNKNOWN.value` and returns a partial (`fetch.py:265-268`). Safe for
  politeness (it stops, no sleep, no poll), but it collapses a transient server
  error into the same bucket as an unrecognized throttle, losing observability. A
  distinct `server_error` reason would let a caller/reviewer tell "IG 503'd" from
  "we hit a shape we don't understand." Not load-bearing.
- [FYI] The escalating-cooldown concern (6.6→13 min, budget 48→36→12) is respected
  by construction — the sync path stops on first 401 and never polls, so it cannot
  extend a cooldown. No literal cooldown timer is needed in T1 (batch runner, later
  ticket, owns pacing).

---

## Security & Trust Reviewer — "I assume every input is hostile until proven otherwise."

**Verdict:** LGTM

The anonymous-only invariant is enforced in code, not just by convention.
`assert_anonymous` keys off AUTH cookies/params/headers (`sessionid`, `ds_user_id`,
`ds_user`; `access_token`, `signed_body`, ...; `authorization`, `ig-u-ds-user-id`,
`x-ig-set-www-claim`) and permits benign anonymous cookies — matched
case-insensitively (`name.lower() in ...`). It is called at three choke points:
construction, `update_cookies`, and every `get_api` send (`http_client.py:298,307,324`).
Critically, when IG hands back an auth cookie via Set-Cookie, `get_api` catches the
`AnonymityViolation` and simply does not store it (`http_client.py:340-343`) rather
than crashing the fetch — this is the right call and is covered by
`test_ig_set_auth_cookie_from_server_not_stored`. No secrets, tokens, or credentials
anywhere; `IG_APP_ID = "936619743392459"` is the documented public IG web app id, not
a secret. No personal-identity linkage. The probe (`probe/probe_spike.py`) is
throwaway and carries its own independent `_assert_anonymous` abort.

**Issues Found:**
- [FYI] `AnonymityViolation` subclasses `RuntimeError` and its docstring says it "must
  never be caught-and-continued" — yet `get_api` deliberately catches it for the
  Set-Cookie case. This is a justified, well-commented exception (refusing to store
  an offered auth cookie is not "continuing an auth attempt"), but the docstring's
  absolute phrasing slightly under-sells that one legitimate catch. Cosmetic.

---

## Error Handling & Resilience Inspector — "Happy path is easy. I review the other 47 paths."

**Verdict:** Suggestions

Stop_signal handling is thorough and the durable-first retry story is genuinely
resilient (see Data Integrity). Two gaps in the failure surface:

**Issues Found:**
- [SUGGESTION] **Transport-level exceptions are unhandled.** `classify_response`
  handles HTTP *responses*, but a connection reset / DNS failure / read timeout
  raised by `curl_cffi.Session.request` propagates uncaught through
  `_get_feed_page` → `fetch_window` → `run_window` → the `list_reels` MCP tool,
  surfacing as a raw traceback. The invariant "stop + return partial with no
  exception" is written about the throttle/block family (which is honored), so this
  is not a strict violation — but a network blip during a window currently crashes
  the tool call instead of returning a typed partial. Recommend wrapping the
  transport call (or the `fetch_window`/`run_window` body) to convert a transport
  error into a partial with a typed reason (e.g. `transport_error`). Also note no
  timeout is set on the `curl_cffi` session (`_default_transport`), so a hung
  connection blocks indefinitely — add a request timeout.
- [SUGGESTION] **200-with-challenge-body would drop a co-resident page of real items
  (plan Open Question #5).** If IG ever returns a 200 whose body carries BOTH real
  feed `items` AND a top-level challenge/login marker, `fetch_window` classifies the
  page as a stop and returns BEFORE `_consume_page` runs (`fetch.py:260-264`), so
  those real items are never persisted. The plan flagged exactly this
  ("if so, the classifier must persist the real items before stopping — durable-first
  still applies"). The probe did not observe this shape this session, and
  `_body_text` only scans TOP-LEVEL scalar values (nested `items` lists are not
  flattened, so a normal feed cannot false-positive), so the risk is low and
  fail-closed is the safe default for politeness. Left as a suggestion rather than a
  blocker because the shape is unconfirmed — but if it is ever observed, the fix is
  to consume/persist `items` first, then stop.

---

## Data Integrity & Migration Reviewer — "Data outlives code. Treat it accordingly."

**Verdict:** LGTM

Durable-first ordering is implemented exactly as the plan mandates and is the
best-tested invariant in the build. `Store.write_window` (a) appends CSV rows and
`os.fsync`es them FIRST (`_append_csv:186-187`), (b) only then advances
`high_water_media_id` (via `max(existing, candidate)` — monotonic, so pinning can
never regress it) and seeds/advances `deep_cursor` over PERSISTED items only, and
(c) writes `state.yaml` atomically via temp-file + `os.replace`
(`_write_state_atomic:199-204`). The `_after_csv_hook` seam makes the crash window
directly testable, and `test_durable_first_failure_leaves_anchor_unadvanced` proves
a fault between (a) and (c) leaves the anchor un-advanced and the rows re-dedupe on
retry (none lost, none duped). `test_state_yaml_is_atomic_full_write` confirms no
leftover `.tmp`. The store is append-only — never destructively capped. `seen` is
DERIVED from the CSV `shortcode` column (single source of truth), and CSV uses
`csv.QUOTE_MINIMAL`, so captions with commas/newlines round-trip
(`test_caption_with_comma_survives_csv_round_trip`). `media_id` persists as a numeric
`int` and `shortcode` as the opaque key — kept distinct throughout
(`test_cursor_and_anchor_round_trip` asserts `isinstance(high_water_media_id, int)`).

**Issues Found:**
- [FYI] On a caught-up top_scan (`pages_fetched == 1`), `result.next_cursor` is set
  from page 1's `next_max_id` and, because `state.deep_cursor is None` on first
  seed, becomes the deep_cursor seed. This is a sensible seed for a later
  deep-backfill caller and does not corrupt top_scan state (the two anchors advance
  independently). No action.

---

## Domain Logic Reviewer — "Does the code do what the spec actually needs?"

**Verdict:** Suggestions

Strong spec alignment. Every T1.x task maps to shipped code: T1.1 config +
`$IG_MK_CONFIG` + per-call deep-merge; T1.3 classifier + anonymity guard; T1.4
`resolve_user_id` returns a clean typed failure (never raises); T1.5 normalization
with `product_type == "clips"` dispatch (the documented extensibility switch),
`video_versions[0].url`, `fetched_at` stored (signed-URL TTL concern), and the
two traversal modes (`top_scan` wired, `deep_resume` supported-not-wired exactly as
scoped); T1.6 store; T1.7 sync compose; T1.8 FastMCP skeleton with `list_reels`
wired and three honest stubs. The Success Metric's anti-regression check
(`pages_fetched == 1` on caught-up) is encoded as a test. Deferred deep-backfill
caller and downloader are correctly out of scope.

**Issues Found:**
- [SUGGESTION → tracked as Accepted Debt] **Pinned-reel under-collection** (the
  build's transparently-flagged deviation). The T1.2 live probe found the owner feed
  is NOT strictly newest-first: natgeo pins reels, so a pinned (older, smaller-pk,
  already-seen) reel can sit ABOVE genuinely newer reels. Consequence: a subsequent
  top_scan can hit the pinned reel first — either via membership OR via the
  `media_id <= high_water_media_id` watermark — and STOP above new reels, under-collecting
  the "new reels below a pinned block." **Assessed as safe-as-shipped and NOT a
  blocker:** (1) the caught-up==1-page short-circuit itself remains correct (when
  genuinely caught up, stopping on the first known item is exactly right, and
  `high_water` only advances monotonically so no data is ever lost or over-counted);
  (2) the store is append+dedupe, so the worst case is *temporary under-collection*
  of some new reels on a pinned account, self-healing as the pinned set changes or a
  later deep pass runs — never corruption or loss. The build did the disciplined
  thing: a precise self-describing TODO in `_consume_page` (`fetch.py:326-336`) plus
  a discovered-followup ticket, and explicitly did NOT weaken the short-circuit to
  patch it. Correct call. Track the hardening (bounded known-prefix skip, or exclude
  the resolved pinned-shortcode set from the stop check) as its own ticket that must
  not reintroduce the budget-burn regression.

---

## Test Coverage Auditor — "An untested change is an unverified assumption."

**Verdict:** Suggestions

43 tests, assertion quality is high — they check outcomes (row counts, stop reasons,
`pages_fetched`, numeric-vs-string types, on-disk cookie/param presence), not just
"no exception." Injectable transport + clock make them deterministic and offline.
The load-bearing behaviors all have targeted tests: fail-closed classifier, partial
on 401/403/429, no-sleep, membership AND watermark short-circuit each proving
`pages_fetched == 1`, durable-first fault injection, atomic state, anonymity guard
(construction/update/server-set), config override merge.

**Issues Found:**
- [SUGGESTION] No test exercises the transport-exception path (connection error /
  timeout mid-window) — matching the Error Handling gap. Add one once that path
  returns a typed partial.
- [SUGGESTION] No test for the 200-with-challenge-body-plus-real-items case (Open
  Question #5). Even as an xfail/documented-gap test, it would pin the intended
  behavior.
- [FYI] `get_cdn`'s redirect-follow (`allow_redirects=True`) is asserted only
  indirectly. A one-line test that `get_cdn` sends `allow_redirects=True` and no
  `x-ig-app-id` would lock the CDN-vs-metadata distinction. Low priority — download
  is a later ticket.

---

## Tech Debt Sentinel — "Every shortcut is a loan. I'm here to read the terms."

**Verdict:** LGTM

The single TODO (`fetch._consume_page:326-336`) is exemplary: it names the exact
condition (feed not pk-descending under pinning), the exact consequence
(under-collection below a pinned block), the exact non-fix (do NOT weaken the
short-circuit), a concrete fix direction, and points at a filed discovered-followup.
This is a tracked loan with terms, not an abandoned note. No hardcoded magic values
that should be config (page cap, pace, scan_depth all live in `FetchSettings`);
`IG_APP_ID` and endpoint URLs are named module constants. No copy-paste. No pattern
violations — the `product_type` dispatch is the documented switch.

**Issues Found:**
- [FYI] Minor dead imports: `field` and `Callable` in `http_client.py` (line 23-25)
  appear unused; `Classification` is imported in `fetch.py` but only used implicitly
  as the untyped `cls`. Harmless; a linter pass would clear them.

---

## Naming & Clarity Guardian — "If I can't understand it in 30 seconds, neither can on-call at 2am."

**Verdict:** LGTM

Naming carries the semantics that matter here. `high_water_media_id` is
unambiguously numeric (the plan renamed it from `high_water_id` precisely to stop the
shortcode-ordering bug from re-entering) and `shortcode` vs `media_id` are distinct
fields on `ReelRecord` with a docstring stating they are "never conflated." `StopKind`
(normal end-of-walk: caught_up/end_of_feed/page_cap/depth_reached) vs `StopReason`
(abnormal throttle family) cleanly separates the two stop taxonomies the plan insisted
must not be conflated. `FetchMode.TOP_SCAN`/`DEEP_RESUME` are self-documenting.
Comments explain *why* (e.g. the watermark being a "monotonic BACKSTOP only").

**Issues Found:**
- [FYI] `Classification.outcome` and the `Outcome` enum vs `ReelRecord`-less
  `FetchResult.stop_reason` (a str holding either a `StopKind` or `StopReason` value)
  is a slight type-smell — a reader must know the string can come from two enums.
  The docstring calls it out, so it's clear enough; a union or a discriminant would be
  marginally cleaner. Cosmetic.

---

## Review Summary

| Reviewer | Verdict | Blocking | Suggestions | FYI | Confidence |
|---|---|---|---|---|---|
| Reliability / Rate-Limit | Suggestions | 0 | 1 | 1 | HIGH |
| Security & Trust | LGTM | 0 | 0 | 1 | HIGH |
| Error Handling & Resilience | Suggestions | 0 | 2 | 0 | HIGH |
| Data Integrity & Migration | LGTM | 0 | 0 | 1 | HIGH |
| Domain Logic | Suggestions | 0 | 1 | 0 | HIGH |
| Test Coverage Auditor | Suggestions | 0 | 2 | 1 | HIGH |
| Tech Debt Sentinel | LGTM | 0 | 0 | 1 | HIGH |
| Naming & Clarity Guardian | LGTM | 0 | 0 | 1 | HIGH |

**Overall Recommendation:** APPROVE

**Rationale:** Every load-bearing invariant the plan called out is implemented and
tested: anonymity is enforced in code at three choke points (and refuses a
server-offered auth cookie without crashing), the stop_signal classifier covers the
full throttle/block/challenge family and fails closed, the sync path never sleeps and
is capped at 4 pages, durable-first ordering fsyncs CSV before advancing anchors and
writes state via atomic temp+`os.replace`, and the round-2 shortcode-ordering bug is
decisively avoided (membership + numeric watermark, `pages_fetched == 1` proven by
test). The caught-up short-circuit is sound as shipped. No finding rises to
must-fix-before-merge. The pinned-reel deviation was disciplined-ly surfaced (not
silently redesigned) and is safe as shipped — under-collection is temporary and
self-healing, never data loss or a politeness regression. The remaining items are
robustness hardening (transport-exception handling, request timeout, the unconfirmed
200-challenge-with-items case) that are legitimately deferrable for a T1 foundation.

**Blocking Items:** None.

**Top Suggestions:**
1. Wrap transport-level exceptions (connection/DNS/timeout) into a typed partial so a
   network blip returns cleanly instead of crashing the `list_reels` tool; add a
   request timeout to the `curl_cffi` session. (Error Handling, Test Coverage)
2. If a 200-with-challenge body ever co-resides with real `items`, persist those
   items before stopping (plan Open Question #5 / durable-first completeness). Pin the
   intended behavior with a test even while the shape is unconfirmed. (Error Handling,
   Test Coverage)
3. Give a 5xx its own `server_error` stop reason instead of collapsing it into
   `unknown` in `fetch_window`. (Reliability)
4. Clear dead imports (`field`, `Callable`, unused `Classification`). (Tech Debt)

**Corroborated Findings (2+ reviewers — highest signal):**
- Transport-exception / timeout gap — flagged by Error Handling AND Test Coverage.
- 200-challenge-with-items (Open Question #5) — flagged by Error Handling AND Test
  Coverage.

**Accepted Debt:**
- Pinned-reel under-collection hardening (`fetch._consume_page`) — the owner feed is
  not strictly newest-first for accounts that pin reels. Safe as shipped (temporary
  under-collection, self-healing, no loss). Follow-up: harden the top_scan stop
  (bounded known-prefix skip, or exclude the resolved pinned-shortcode set) in its own
  ticket, WITHOUT weakening the caught-up short-circuit — the fix must preserve the
  `pages_fetched == 1` anti-regression invariant. Owner: build team. Timeline: next
  T-series ticket, before a pinned-account handle is relied on in production.
