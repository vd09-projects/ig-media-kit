# ig-media-kit

Self-hosted MCP server that fetches top Instagram **reels** (with view counts) from a
list of public channels — **anonymously: no login, no account, no personal-identity
link**. The Instagram counterpart to `yt-media-kit`.

Four MCP tools sit over one shared, anonymous fetch engine: a paced
`GET /api/v1/feed/user/{id}/` walk returns `play_count` + metadata + the mp4 URL in one
call (no cookies). Python + [`curl_cffi`](https://github.com/lexiforest/curl_cffi)
(`impersonate="chrome"`) + FastMCP. No yt-dlp, no browser automation.

- **Language:** Python 3.12+
- **Storage:** flat files — CSV manifests + YAML state per handle; mp4s on disk. No DB.
- **Status:** the four tools are built, tested, and wired into a runnable server.
  The end-to-end smoke ships as an **offline fixture/dry-run harness**; a live IG run is
  deferred to the pilot tickets (#10 / #14 — see [Smoke test](#smoke-test)).

---

## Install

Fresh clone → editable or plain install into a Python 3.12+ virtualenv:

```bash
git clone <this-repo> ig-media-kit
cd ig-media-kit
python3 -m venv .venv && source .venv/bin/activate
pip install .            # or:  pip install -e '.[dev]'   (dev adds pytest)
```

This installs the `ig-media-kit` console script and the `ig_media_kit` package
(`curl_cffi`, `mcp[cli]`, `PyYAML` are pinned in `pyproject.toml`).

```bash
ig-media-kit --help
```

## Configure

Copy / edit `config.yaml` (it mirrors yt-media-kit's ergonomics):

```yaml
channels:                 # public handles to scan (no @, no URL — just the handle)
  - natgeo
  - nike

top_reels:                # ranking/filter defaults (per-call args override these)
  count: 5
  sort_by: "play_count"   # play_count | like_count | comment_count | taken_at
  min_play_count: 100000
  max_age_days: 30        # null = all time

fetch:                    # politeness / effort knobs — load-bearing, see below
  scan_depth: 90
  max_pages_per_call: 4
  page_pace_seconds: 1.5

batch:                    # async runner knobs (the ONLY sleeper); optional
  cooldown_base_s: 400.0
  cooldown_escalation_factor: 2.0

output:
  store_dir: "./store"    # CSV manifests + YAML state (git-ignored, made at runtime)
  media_dir: "./media"    # downloaded mp4s (git-ignored, made at runtime)
```

**Config path resolution** (highest priority first): the explicit `--config` flag
(or a tool's `config_path` arg) **>** the `$IG_MK_CONFIG` environment variable **>**
`./config.yaml` in the working directory.

```bash
export IG_MK_CONFIG=/etc/ig-media-kit/config.yaml   # picked up when --config is omitted
```

## Run

Start the MCP server over stdio — via the console script **or** the module:

```bash
ig-media-kit --config config.yaml
# equivalently:
python -m ig_media_kit.mcp_server --config config.yaml
```

Point your MCP client (e.g. Claude Desktop, the `mcp` CLI) at that command; it will list
the four tools with their input schemas.

**Startup job adoption.** On boot — *before* it begins serving — the server calls
`resume_pending_jobs` and **re-adopts any pending `store/_batch` jobs** a prior process
left behind (a kill/restart resumes an in-flight batch from its last checkpoint rather
than restarting it). The startup line reports how many jobs were re-adopted. Because of
this, a **dev/experimental run should point at a throwaway `store_dir`** (via `--config`
or `$IG_MK_CONFIG`) so it doesn't adopt a real batch's checkpoints.

`store/` and `media/` are created at runtime and are git-ignored — you don't pre-create
them.

## The four tools

| Tool | When to use it |
|---|---|
| **`list_reels`** | Fast, synchronous top-N for **one** handle. Never sleeps; serves straight from the store with zero network once coverage is complete, and returns a ranked **partial** (not a block) on the first rate-limit. Reach for this interactively. |
| **`download_reel`** | Download **one** reel's mp4 by shortcode, on demand. Serves a cached file with zero network; re-resolves an expired signed URL from the owner feed when needed. |
| **`start_batch_fetch`** | **Async** multi-handle fill + top-N aggregation across channels (global or per-channel), with optional top-N download and an optional callback POST. Returns a `job_id` immediately and runs detached. This is the **only** path allowed to sleep/pace across rate-limit cooldowns. |
| **`get_batch_status`** | Poll a batch `job_id` for phase, per-handle progress, liveness, and the aggregated result. Pure read — **no IG network**, safe to call during a cooldown. |

Each tool returns a typed dict envelope and **never raises to the client** — every
failure (unknown handle, rate-limit, bad body) comes back as an envelope with a `note`
and an `error`/`partial` marker.

## Anonymity & politeness (load-bearing)

This tool is **anonymous only.** No login, no cookies, no session, no account — on any
code path. Every metadata call carries the public web app id header
(`x-ig-app-id: 936619743392459`) and nothing that identifies a user.

- **The metadata API is metered.** IG rate-limits the feed endpoint to roughly
  **~48 items per ~6.6-minute window per IP**, and the cooldown **escalates under abuse**
  (measured ~6.6 → ~13 min; the item budget degrades ~48 → 36 → 12). So the tools pace
  pages ~1–2 s, cap ~4 pages per call, **stop and return a partial on the first
  rate-limit**, and **never poll during a cooldown** (polling extends it). `list_reels`
  never sleeps; only `start_batch_fetch` may — it sleeps cooldowns out on a background
  thread, serialized through one process-wide fetch gate so only one IG window is ever in
  flight.
- **The video CDN is not metered.** Getting the mp4 URL (the metadata call) is the
  bottleneck; downloading the mp4 from `fbcdn.net` is unmetered and paced freely.
- **Signed-URL TTL ≈ 36 h.** A reel's `video_versions[0].url` carries an expiry (`oe=`).
  The store keeps `video_url` + `fetched_at`; `download_reel` **re-resolves a fresh URL
  from the owner feed once the stored one is older than ~24 h** (a margin under the ~36 h
  TTL) rather than handing back a dead link.

## Burner accounts: out of scope, rejected

A logged-in **burner account** — the only verified way to fetch deep/all-time history
past the ~48/window anonymous cap — was researched and **explicitly rejected**. A
de-linked burner is creatable, but it is consumable (bans in hours to ~1 year) and
carries a low-but-nonzero residual risk to the user's real account, which is
unacceptable. This project authenticates on **no** code path, ever. Full rationale:
[`research/no-login-reel-fetch/report.md` → "Burner option — investigated and rejected"](research/no-login-reel-fetch/report.md#burner-option--investigated-and-rejected-v3).

## Smoke test

An offline **fixture/dry-run** harness exercises the full wiring —
`list_reels` → `download_reel` → `start_batch_fetch` + a local callback receiver →
`get_batch_status`, plus a simulated mid-fetch rate-limit — with **zero IG network**
(it fails hard if any code path tries to build a real network transport):

```bash
python -m probe.probe_smoke          # exits 0 and prints the step trace
pytest tests/test_smoke.py           # the same harness, as a CI check
```

**The live smoke run is DEFERRED to pilot tickets #10 / #14** and is opt-in only — it is
never run by default or in CI, because a live run consumes this IP's escalating IG
cooldown budget. To run it once the pilots are ready:

```bash
IG_MK_SMOKE_LIVE=1 python -m probe.probe_smoke --handle <public_handle>
```

Until then the fixture harness is the source of truth for the wiring; the live pass is
not faked green.

## Learn more

- [`problem-statement.md`](problem-statement.md) — what we're building and why.
- [`research/no-login-reel-fetch/report.md`](research/no-login-reel-fetch/report.md) —
  the verified architecture decision (anonymous `feed/user` fetch, 4-tool MCP,
  call-driven fill, async batch).
- `research/no-login-reel-fetch/architecture.html` — visual architecture + flow diagrams.
