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
  - slug: t4-async-batch-runner
    title: T4 — Async batch runner: start_batch_fetch + get_batch_status + callback
    created: 2026-07-16T03:18:00Z
    aliases: []
    reasoning: >
      Created for the T4 plan breaking down the async batch subsystem — the
      only background-job path — on top of the shipped T2 fetch engine
      (run_window / coverage / ranking / Store) and T3 downloader
      (run_download_reel): start_batch_fetch returns a job_id instantly and runs
      detached, a serialized single-IP fetch worker fills each handle across
      escalating rate-limit cooldowns (the runner is the only sleeper) and
      checkpoints after every window for kill/restart resume, global/per_channel
      top-N aggregation over the full deduped pool, optional download_top, and a
      bare non-IG callback POST with bounded retry+backoff decoupled from result
      durability; get_batch_status is a pure read. Slug supplied explicitly via
      --scope; kebab-case with the T4 task prefix, mirroring the T1/T2/T3 scope
      naming convention. First artifact is a mimir planner-task with the
      concurrency overlay active.
  - slug: t5-ship-mcp-server-packaging
    title: "T5 — Ship: MCP server wiring, packaging, docs, extensibility, smoke"
    created: 2026-07-16T07:57:44Z
    aliases: []
    reasoning: >
      Created for the T5 capstone ship plan that assembles the four already-
      merged tools (list_reels, download_reel, start_batch_fetch,
      get_batch_status) into one runnable/packaged/documented FastMCP server:
      shared server context + --config/$IG_MK_CONFIG resolution + explicit
      resume_pending_jobs at startup (daemon-thread decision), freezing the
      four-tool public surface (drop top_reels stub, rename batch_fetch ->
      start_batch_fetch), the product_type dispatch switch + disabled non-clip
      stub, pyproject/config verification, README rewrite, and an offline
      fixture/dry-run smoke harness (live IG run deferred to pilots #10/#14).
      Slug supplied explicitly via --scope; kebab-case with the T5 task prefix,
      mirroring the T1-T4 scope naming convention. First artifact is a mimir
      planner-task with the public-api-change overlay active.
```

---

Bootstrap empty. Skald appends entries as it creates scopes (newest at bottom, never reorder, never delete). See Skald's SKILL.md and the scope-registry template for field definitions.
