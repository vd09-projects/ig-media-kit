"""T5 server wiring — shared context, the frozen four-tool surface, the never-raise
guarantee, one-gate (divergent-store rejection), config precedence, and startup
resume-before-serve. Offline: no network, no real mcp.run()."""

from __future__ import annotations

import pytest

from ig_media_kit import batch, coverage, fetch_gate, mcp_server
from ig_media_kit.config import load_config
from ig_media_kit.fetch import FetchMode, normalize_item

# The frozen public surface: exactly these four tools, with these param names.
EXPECTED_SURFACE = {
    "list_reels": {"handle", "count", "sort_by", "min_views", "min_duration",
                   "max_age_days", "scan_depth", "fresh_fetch", "config_path"},
    "download_reel": {"shortcode", "config_path"},
    "start_batch_fetch": {"handles", "scope", "count", "sort_by", "min_views",
                          "min_duration", "max_age_days", "download_top",
                          "callback_url", "config_path"},
    "get_batch_status": {"job_id", "config_path"},
}


@pytest.fixture(autouse=True)
def _isolation():
    mcp_server.reset_context()
    fetch_gate.reset_gate()
    batch.reset_batch_state()
    yield
    mcp_server.reset_context()
    fetch_gate.reset_gate()
    batch.reset_batch_state()


def _write_config(path, store_dir) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "channels:\n  - natgeo\n"
        "top_reels:\n  count: 5\n  sort_by: play_count\n  min_play_count: 0\n"
        "fetch:\n  scan_depth: 90\n  max_pages_per_call: 4\n  page_pace_seconds: 1.5\n"
        f"output:\n  store_dir: {store_dir}\n  media_dir: {store_dir}-media\n",
        encoding="utf-8",
    )
    return str(path)


def _seed_contiguous(store, handle):
    from tests.conftest import load_feed
    reels = [r for r in (normalize_item(i, 1) for i in load_feed()["items"]) if r]
    store.write_window(handle, reels, user_id="787132", next_cursor=None,
                       stop_reason="end_of_feed", mode=FetchMode.TOP_SCAN)
    pks = [r.media_id for r in reels]
    store.save_coverage_segments(handle, [coverage._segment(max(pks), min(pks), None, True)])


# --- frozen four-tool surface -----------------------------------------------

def test_four_tool_surface_snapshot():
    tools = {t.name: set((t.parameters or {}).get("properties", {}).keys())
             for t in mcp_server.mcp._tool_manager.list_tools()}
    assert set(tools) == set(EXPECTED_SURFACE), "tool set drifted from the frozen four"
    for name, params in EXPECTED_SURFACE.items():
        assert tools[name] == params, f"{name} param signature drifted: {tools[name]}"


def test_no_stale_top_reels_or_batch_fetch_tool():
    names = {t.name for t in mcp_server.mcp._tool_manager.list_tools()}
    assert "top_reels" not in names        # stale stub removed
    assert "batch_fetch" not in names      # renamed to start_batch_fetch


# --- never-raise across all four --------------------------------------------

def test_all_four_tools_never_raise_when_run_throws(tmp_path, monkeypatch):
    mcp_server.install_context(load_config(_write_config(tmp_path / "c.yaml", tmp_path / "s")))

    def boom(*_a, **_k):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(mcp_server, "run_list_reels", boom)
    monkeypatch.setattr(mcp_server, "run_download_reel", boom)
    monkeypatch.setattr(mcp_server, "run_start_batch_fetch", boom)
    monkeypatch.setattr(mcp_server, "run_get_batch_status", boom)

    outs = {
        "list_reels": mcp_server.list_reels("h"),
        "download_reel": mcp_server.download_reel("sc"),
        "start_batch_fetch": mcp_server.start_batch_fetch(handles=["h"]),
        "get_batch_status": mcp_server.get_batch_status("j"),
    }
    for name, out in outs.items():
        assert isinstance(out, dict), f"{name} did not return a dict"
        blob = (out.get("error") or "") + (out.get("note") or "")
        assert "kaboom" in blob, f"{name} swallowed the failure detail: {out}"


# --- download envelope preserved through the wrapper ------------------------

def test_download_tool_preserves_partial_vs_typed_error(tmp_path, monkeypatch):
    mcp_server.install_context(load_config(_write_config(tmp_path / "c.yaml", tmp_path / "s")))

    monkeypatch.setattr(mcp_server, "run_download_reel",
                        lambda *_a, **_k: {"partial": True, "stop_reason": "rate_limited"})
    metered = mcp_server.download_reel("sc")
    assert metered["partial"] is True and metered["stop_reason"] == "rate_limited"

    monkeypatch.setattr(mcp_server, "run_download_reel",
                        lambda *_a, **_k: {"partial": False, "error": "aged out of reach"})
    aged = mcp_server.download_reel("sc")
    assert aged["partial"] is False and aged["error"] == "aged out of reach"


# --- one gate per process: divergent-store_dir config_path is rejected -------

def test_divergent_store_dir_config_path_is_rejected(tmp_path):
    ctx = mcp_server.install_context(load_config(_write_config(tmp_path / "a.yaml", tmp_path / "store-a")))
    divergent = _write_config(tmp_path / "b.yaml", tmp_path / "store-b")

    out = mcp_server.list_reels("natgeo", config_path=divergent)
    assert isinstance(out, dict), "rejection must be an envelope, not a raise"
    assert out.get("error") and "store_dir" in out["error"], f"not the store-split rejection: {out}"
    # The server context is untouched — no second store was adopted.
    assert mcp_server.current_context() is ctx


def test_same_store_dir_config_path_reuses_server_context(tmp_path):
    ctx = mcp_server.install_context(load_config(_write_config(tmp_path / "a.yaml", tmp_path / "store-a")))
    _seed_contiguous(ctx.store, "natgeo")
    # A different config FILE but the SAME store_dir — a store-compatible override.
    compatible = _write_config(tmp_path / "a2.yaml", tmp_path / "store-a")

    out = mcp_server.list_reels("natgeo", config_path=compatible)
    assert not out.get("error"), f"same-store override wrongly rejected: {out}"
    assert out["reels"], "override did not reuse the seeded server store"
    assert out["pages_fetched"] == 0, "reused context should serve from store, zero network"


# --- config precedence: explicit > $IG_MK_CONFIG > default (via load_config) -

def test_startup_reuses_load_config_precedence(tmp_path, monkeypatch):
    env_cfg = _write_config(tmp_path / "env.yaml", tmp_path / "store-env")
    explicit_cfg = _write_config(tmp_path / "exp.yaml", tmp_path / "store-exp")
    monkeypatch.setenv("IG_MK_CONFIG", env_cfg)
    monkeypatch.setattr(mcp_server, "resume_pending_jobs",
                        lambda *_a, **_k: {"count": 0, "tmp_swept": 0})

    # explicit --config wins over $IG_MK_CONFIG
    mcp_server.startup(explicit_cfg)
    assert mcp_server.current_context().config.output.store_dir == str(tmp_path / "store-exp")

    # omitting --config falls back to $IG_MK_CONFIG (not a reimplemented resolver)
    mcp_server.reset_context()
    mcp_server.startup(None)
    assert mcp_server.current_context().config.output.store_dir == str(tmp_path / "store-env")


# --- startup: explicit resume runs, before serving, 0 adopted on clean store -

def test_startup_resumes_zero_on_clean_store(tmp_path):
    resumed = mcp_server.startup(_write_config(tmp_path / "c.yaml", tmp_path / "store"))
    assert resumed["count"] == 0
    assert mcp_server.current_context() is not None


def test_resume_runs_before_mcp_run(tmp_path, monkeypatch):
    order: list[str] = []
    monkeypatch.setattr(mcp_server, "resume_pending_jobs",
                        lambda *_a, **_k: (order.append("resume"), {"count": 0, "tmp_swept": 0})[1])
    monkeypatch.setattr(mcp_server.mcp, "run", lambda: order.append("run"))

    mcp_server.main(["--config", _write_config(tmp_path / "c.yaml", tmp_path / "store")])
    assert order == ["resume", "run"], "resume_pending_jobs must complete before mcp.run()"
    assert mcp_server.current_context() is not None


def test_help_exits_zero():
    with pytest.raises(SystemExit) as exc:
        mcp_server.main(["--help"])
    assert exc.value.code == 0
