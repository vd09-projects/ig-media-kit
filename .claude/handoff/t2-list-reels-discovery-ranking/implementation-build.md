---
artifact_type: handoff
artifact_version: 2
producer_role: implementation
consumer_role: review
plan_type: build
slug: t2-list-reels-discovery-ranking
scope_hint: T2 followup markers — backfill real GitHub issue numbers
canonical_name: implementation-build
overlays: []
status: draft
version: 1
created: 2026-07-15T17:31:36Z
updated: 2026-07-15T17:31:36Z
prior_versions: []
---

# Build summary: T2 followup markers — backfill real GitHub issue numbers

**Mode:** iterate (SMALL, comment/docstring only — no logic change, build not re-run)

**Scope:** Append the real tracking issue number to the two self-describing followup markers left during the T2 build. This is the only point a real issue number enters the code.

**Files modified:**
- `src/ig_media_kit/window.py` — the `run_window`-unreferenced / "avoid two compose paths drifting" marker now ends with `(tracked: #8)`. Marker text otherwise unchanged; still states what (run_window unreferenced), where (the sync compose primitive vs. T2's inlined two-phase compose), and why (consolidate onto the batch runner or retire to avoid drift).
- `src/ig_media_kit/fetch.py` — the `PINNED_PREFIX_BOUND` marker now carries `(tracked: #9)` with a one-line why-restatement ("If IG raises its pin cap above this, top_scan under-collects."). The original two lines describing the pin-cap tolerance are intact.

**Tests written:** none — comment/docstring-only edit, no runtime surface changed.

**Quality gate:** PASS. `pytest -q` -> 90 passed (unchanged). No logic touched; the two edits are inside comment blocks only. Both markers remain self-describing (what + where + why) and now additionally carry their issue number.

**Decision marks:** none.

Ready for review.
