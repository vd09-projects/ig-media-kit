# IG Media Kit — Problem Statement

**Author:** Claude (Opus 4.8) · via Claude Code
**Date:** 2026-07-13
**Model to copy:** `yt-media-kit` (sibling MCP for YouTube)

---

## What we want

An MCP, the **IG Media Kit**, that works just like `yt-media-kit` — but for Instagram.

In a config file I list one or more **Instagram channels (handles)**. I run it, and it **fetches the top reels from each channel** — downloads the video files and returns the data (views, likes, comments, caption, date, duration). That's it. Same ergonomics as yt-media-kit: channels + filters in config, overridable per call.

## Background — what yt-media-kit does (the thing to mirror)

`yt-media-kit` is a dumb fetcher: you give it YouTube channels + filters (view floor, duration, age); it lists the top videos on those channels, downloads what you asked for, writes local files. It doesn't pick channels, score, or analyze — it just fetches from the channels you name. IG Media Kit should be the same, for Instagram reels.

## Requirements

1. **Config-driven, multiple channels.** Give it a list of IG handles; it fetches from each.
2. **Top-N per channel, or all — configurable.** Default: top-N by performance (views/engagement), with filters (min views, duration, age), like yt-media-kit. Option: pull everything.
3. **Download the video** (reel mp4) to local files, stable naming.
4. **Return the data** per item — views, likes, comments, caption, date, duration — as a manifest (CSV/JSON) linking each file to its numbers.
5. **Fetch-more / skip-seen** — don't re-download reels already pulled (same as yt-media-kit ticket T5).
6. **Start with reels; built to extend.** Reels are the priority now. Long-term we want all media types (image posts, carousels, later stories) — so write it so adding a type is easy, not a rewrite.

## Non-goals

- **No channel discovery.** It does not find competitors or decide who to pull from — that's the analyst's job, separate from this kit.
- **No analysis, scoring, or hypotheses** — a downstream consumer does that.
- **No publishing** (that's `ig-mcp`).
- **No transcription** — reels have no downloadable subtitle track; I'll make captions myself (Wispr Flow) if needed. Downloading a caption track is a nice-to-have only if Instagram makes it cheap.

## The one hard part (for a separate solution-research session)

Unlike YouTube — where yt-dlp fetches with no login — **Instagram does not allow easy anonymous fetching**, and I do **not** want to use my own Instagram credentials. Reconciling "fetch without my personal login" with Instagram's access model is the main thing to solve, and it belongs to a **separate research session**, not this problem statement. A working fetch method has already been validated manually (it yields views + a downloadable video URL); that session can start from there.

## Success = 

Give it a config of IG handles → get the top reels from each as local mp4s + a metrics manifest. As simple to use as yt-media-kit.
