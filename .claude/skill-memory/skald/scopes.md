# Skald — Scope Registry
<!-- rune-generated: 2026-07-14 | git: acdd3c5 | rune: 1.0 -->

Append-only registry of every scope Skald has touched in this project. Maintained by Skald — humans should not edit freehand except for slug-rename operations.

---

```yaml
scopes:
  - slug: fetch-engine-store-foundation
    title: T1 — Fetch engine + store foundation
    created: 2026-07-14T17:50:46Z
    aliases: []
    reasoning: >
      Created for the T1 foundational plumbing plan (anonymous curl_cffi fetch
      primitive + CSV/YAML store + politeness + config + FastMCP skeleton) that
      all four MCP tools depend on. Slug is kebab-case from the task's scope
      noun; first artifact is a mimir planner-task.
  - slug: t2-list-reels-discovery-ranking
    title: T2 — list_reels: anonymous discovery + ranking (call-driven fill)
    created: 2026-07-15T14:22:41Z
    aliases: []
    reasoning: >
      Created for the T2 plan fleshing out the first full MCP tool (list_reels)
      on top of the merged T1 foundation (PR #7): serve-from-store fast path,
      top-check + deepen two-phase call-driven fill toward scan_depth=90,
      coverage-segment gap tracking, partial-on-stop_signal, and rank-over-pool.
      Slug supplied explicitly via --scope; kebab-case with the T2 task prefix,
      mirroring the T1 scope naming convention. First artifact is a mimir
      planner-task.
  - slug: t3-download-reel-signed-url-refresh
    title: T3 — download_reel: mp4 download + signed-URL refresh
    created: 2026-07-15T19:25:20Z
    aliases: []
    reasoning: >
      Created for the T3 plan implementing the download_reel MCP tool on top of
      the T1 fetch/store foundation and T2 list_reels ergonomics: shortcode->
      owner-handle+row resolution, a strict no-network cached-hit gate, TTL-margin
      freshness, a shortcode/media_id-anchored owner-feed re-resolve (never
      positional per standing order), binary redirect-follow CDN download with
      ftyp-verify, and an atomic manifest local_mp4/video_url/fetched_at rewrite.
      Slug supplied explicitly via --scope; kebab-case with the T3 task prefix,
      mirroring the T1/T2 scope naming convention. First artifact is a mimir
      planner-task.
```

---

Bootstrap empty. Skald appends entries as it creates scopes (newest at bottom, never reorder, never delete). See Skald's SKILL.md and the scope-registry template for field definitions.
