"""T4 async batch runner — state model + atomic checkpoint, serialized per-handle
fill loop with escalating cooldown, aggregation (global/per_channel stable
envelope), optional download_top, SSRF-guarded callback + retry, entrypoints +
liveness + restart-resume. Offline: injected transports + a fake clock, zero real
sleeps and zero real IG/callback network."""

from __future__ import annotations

import threading
import time

import pytest

from ig_media_kit import batch
from ig_media_kit.batch import (
    BatchDeps, BatchJob, HandleProgress, JobPhase, OUTCOME_COVERED,
    _aggregate, _default_poster, _download_top, _fill_handle, _launch,
    _post_callback, _run_job, resume_pending_jobs, run_get_batch_status,
    run_start_batch_fetch, validate_callback_url,
)
from ig_media_kit.config import (
    BatchSettings, Config, FetchSettings, OutputSettings, TopReelsFilter,
)
from ig_media_kit.fetch import FetchMode, normalize_item
from ig_media_kit.fetch_gate import FetchGate
from ig_media_kit.http_client import AnonymousClient
from ig_media_kit.store import Store
from tests.conftest import FakeResponse, FakeTransport

USER_ID = "787132"
NOW = 1_700_000_000
PUBLIC_IP = "93.184.216.34"


@pytest.fixture(autouse=True)
def _reset_batch():
    batch.reset_batch_state()
    yield
    batch.reset_batch_state()


class FakeClock:
    def __init__(self, t: float = float(NOW)) -> None:
        self.t = t
        self.sleeps: list[float] = []
        self._lock = threading.Lock()

    def now(self) -> float:
        with self._lock:
            return self.t

    def sleep(self, d: float) -> None:
        with self._lock:
            self.sleeps.append(d)
            self.t += max(0.0, d)


def _config(tmp_path, *, channels=None, max_pages=4, batch_settings=None):
    return Config(
        channels=channels or [],
        top_reels=TopReelsFilter(count=10, sort_by="play_count"),
        fetch=FetchSettings(scan_depth=90, max_pages_per_call=max_pages,
                            page_pace_seconds=1.5),
        output=OutputSettings(store_dir=str(tmp_path / "store"),
                              media_dir=str(tmp_path / "media")),
        batch=batch_settings or BatchSettings(),
        raw={},
    )


def _clip(pk: int, code: str, *, plays: int = 1000, url: str = "u") -> dict:
    return {
        "pk": str(pk), "id": f"{pk}_{USER_ID}", "code": code,
        "product_type": "clips", "media_type": 2, "play_count": plays,
        "ig_play_count": plays, "like_count": 10, "comment_count": 1,
        "taken_at": NOW - 100, "video_duration": 30.0,
        "caption": {"text": code}, "video_versions": [{"url": url}],
    }


def _profile() -> FakeResponse:
    return FakeResponse(200, {"data": {"user": {"id": USER_ID}}})


def _page(items, *, more=False, next_id=None) -> FakeResponse:
    return FakeResponse(200, {"num_results": len(items), "more_available": more,
                              "next_max_id": next_id, "items": items})


def _make_deps(config, responses, clock, *, resolver=None, poster=None, jitter=None):
    store = Store(config.output.store_dir)
    gate = FetchGate(
        cooldown_base_s=config.batch.cooldown_base_s,
        cooldown_escalation_factor=config.batch.cooldown_escalation_factor,
        cooldown_cap_s=config.batch.cooldown_cap_s,
        clock=clock.now, sleep=clock.sleep, state_path=None,
    )
    transport = FakeTransport(list(responses))
    deps = BatchDeps(
        config=config, store=store, gate=gate, clock=clock.now, sleep=clock.sleep,
        client_factory=lambda: AnonymousClient(transport),
        resolver=resolver or (lambda h: [PUBLIC_IP]),
        poster=poster or (lambda *a, **k: 200),
        jitter=jitter or (lambda base: 0.0),
    )
    return deps, store, gate, transport


def _seed(store, handle, *, code, pk, plays=1000, url="u", fetched_at=NOW):
    reel = normalize_item(_clip(pk, code, plays=plays, url=url), fetched_at)
    store.write_window(handle, [reel], user_id=USER_ID, next_cursor=None,
                       stop_reason="end_of_feed", mode=FetchMode.TOP_SCAN)


def _wait_for(pred, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(0.005)
    return False


# --- Step 1/2: checkpoint round-trip + atomic write + tmp sweep -------------

def test_batchjob_round_trips_through_store(tmp_path):
    config = _config(tmp_path)
    store = Store(config.output.store_dir)
    job = BatchJob(
        job_id="abc123", phase=JobPhase.FETCHING,
        params={"handles": ["a"], "scope": "global", "count": 3,
                "sort_by": "play_count", "filters": {}, "download_top": False,
                "callback_url": None},
        per_handle=[HandleProgress("a", outcome=OUTCOME_COVERED, pages_spent=4)],
        heartbeat_at=float(NOW), created=float(NOW), updated=float(NOW),
    )
    store.save_batch_job(job.job_id, job.to_dict())
    reloaded = BatchJob.from_dict(store.load_batch_job("abc123"))
    assert reloaded.phase is JobPhase.FETCHING
    assert reloaded.handles() == ["a"]
    assert reloaded.progress("a").outcome == OUTCOME_COVERED
    assert reloaded.progress("a").pages_spent == 4


def test_load_unknown_job_is_none(tmp_path):
    store = Store(_config(tmp_path).output.store_dir)
    assert store.load_batch_job("nope") is None


def test_sweep_batch_tmp_removes_orphans_only(tmp_path):
    store = Store(_config(tmp_path).output.store_dir)
    store.save_batch_job("real", {"job_id": "real", "phase": "done"})
    orphan = store.batch_dir() / "real.json.tmp"
    orphan.write_text("torn", encoding="utf-8")
    removed = store.sweep_batch_tmp()
    assert removed == 1
    assert not orphan.exists()
    assert store.load_batch_job("real") is not None  # canonical untouched


def test_list_batch_jobs_excludes_result_and_gate(tmp_path):
    store = Store(_config(tmp_path).output.store_dir)
    store.save_batch_job("j1", {"job_id": "j1", "phase": "queued"})
    store.save_batch_result("j1", {"job_id": "j1"})
    (store.batch_dir() / "_gate.json").write_text("{}", encoding="utf-8")
    assert store.list_batch_jobs() == ["j1"]


# --- Step 4: serialized per-handle fill with escalating cooldown ------------

def test_fill_handle_completes_in_one_unit(tmp_path):
    config = _config(tmp_path, channels=["natgeo"])
    clock = FakeClock()
    deps, store, gate, transport = _make_deps(
        config, [_profile(), _page([_clip(10, "AAA")], more=False)], clock)
    job = BatchJob("j", JobPhase.FETCHING,
                   params={"filters": {}}, per_handle=[HandleProgress("natgeo")])
    _fill_handle(job, "natgeo", deps=deps, client=deps.client_factory(),
                 checkpoint=lambda: None)
    assert job.progress("natgeo").outcome == OUTCOME_COVERED
    assert store.count_reels("natgeo") == 1
    assert clock.sleeps == []  # nothing metered → no cooldown sleep


def test_fill_handle_cooldown_then_resume_no_poll_during_sleep(tmp_path):
    config = _config(tmp_path, channels=["natgeo"])
    clock = FakeClock()
    # window1: resolve ok, then a 401 on the feed page (metered stop).
    # window2 (after the gate sleeps the cooldown): the feed page succeeds.
    deps, store, gate, transport = _make_deps(
        config,
        [_profile(), FakeResponse(401), _page([_clip(10, "AAA")], more=False)],
        clock,
    )
    job = BatchJob("j", JobPhase.FETCHING,
                   params={"filters": {}}, per_handle=[HandleProgress("natgeo")])
    _fill_handle(job, "natgeo", deps=deps, client=deps.client_factory(),
                 checkpoint=lambda: None)

    assert job.progress("natgeo").outcome == OUTCOME_COVERED
    assert store.count_reels("natgeo") == 1
    # Exactly one cooldown sleep of the base duration; the escalated wait was
    # slept out BEFORE the resume window — IG was not polled during it.
    assert clock.sleeps == [config.batch.cooldown_base_s]
    # 3 IG calls total: profile, the 401 feed page, the resume feed page.
    assert len(transport.calls) == 3


def test_fill_handle_stall_guard_bounds_hard_block(tmp_path):
    # A handle that keeps 401-ing (0 rows persisted) must not spin forever.
    config = _config(tmp_path, channels=["natgeo"],
                     batch_settings=BatchSettings(retries=2, cooldown_base_s=10.0))
    clock = FakeClock()
    responses = [_profile()] + [FakeResponse(401) for _ in range(20)]
    deps, store, gate, transport = _make_deps(config, responses, clock)
    job = BatchJob("j", JobPhase.FETCHING,
                   params={"filters": {}}, per_handle=[HandleProgress("natgeo")])
    _fill_handle(job, "natgeo", deps=deps, client=deps.client_factory(),
                 checkpoint=lambda: None)
    assert job.progress("natgeo").outcome == "partial"
    # Bounded by retries+2 stalls, never the full 20 canned 401s.
    assert len(clock.sleeps) <= config.batch.retries + 3


def test_two_workers_no_window_opens_after_401_until_cooldown_slept(tmp_path):
    """F6 regression for F1: under two concurrent workers sharing the process gate
    (resume relaunches every non-terminal job on its own thread; a new start can
    overlap), once worker A hits a 401 NO IG window — not A's next unit, not
    worker B's first unit — may open until the escalated cooldown has been slept
    out. This fails if the cooldown is registered AFTER the gate releases (the
    pre-fix race): B would acquire the just-released gate and hit the still-401'd
    IP at NOW before A's back-off landed. Guards the single-IP-under-abuse
    invariant."""
    config = _config(tmp_path, channels=["a", "b"])
    clock = FakeClock()
    base = config.batch.cooldown_base_s
    a_hit_401 = threading.Event()

    class RecordingTransport(FakeTransport):
        """Records the virtual-clock time of every IG call it serves; optionally
        fires a hook the moment it serves a 401."""

        def __init__(self, responses, *, on_401=None):
            super().__init__(responses)
            self._on_401 = on_401
            self.times: list[float] = []

        def __call__(self, method, url, **kw):
            self.times.append(clock.now())
            resp = super().__call__(method, url, **kw)
            if self._on_401 is not None and resp.status_code == 401:
                self._on_401()
            return resp

    gate = FetchGate(
        cooldown_base_s=base,
        cooldown_escalation_factor=config.batch.cooldown_escalation_factor,
        cooldown_cap_s=config.batch.cooldown_cap_s,
        clock=clock.now, sleep=clock.sleep, state_path=None,
    )
    store = Store(config.output.store_dir)

    # Worker A: resolve → 401 (metered stop) → resume page completes the handle.
    a_tx = RecordingTransport(
        [_profile(), FakeResponse(401), _page([_clip(10, "AAA")], more=False)],
        on_401=a_hit_401.set)
    # Worker B: resolve → page completes. Must not touch IG until A's cooldown
    # is slept out.
    b_tx = RecordingTransport([_profile(), _page([_clip(21, "BBB")], more=False)])

    def _deps(tx):
        return BatchDeps(
            config=config, store=store, gate=gate,
            clock=clock.now, sleep=clock.sleep,
            client_factory=lambda: AnonymousClient(tx),
            resolver=lambda h: [PUBLIC_IP], poster=lambda *a, **k: 200,
            jitter=lambda _b: 0.0,
        )

    a_deps, b_deps = _deps(a_tx), _deps(b_tx)

    def run_a():
        job = BatchJob("ja", JobPhase.FETCHING, params={"filters": {}},
                       per_handle=[HandleProgress("a")])
        _fill_handle(job, "a", deps=a_deps, client=a_deps.client_factory(),
                     checkpoint=lambda: None)

    def run_b():
        a_hit_401.wait(timeout=2.0)  # B only starts after A has served the 401
        job = BatchJob("jb", JobPhase.FETCHING, params={"filters": {}},
                       per_handle=[HandleProgress("b")])
        _fill_handle(job, "b", deps=b_deps, client=b_deps.client_factory(),
                     checkpoint=lambda: None)

    ta = threading.Thread(target=run_a, name="A")
    tb = threading.Thread(target=run_b, name="B")
    ta.start(); tb.start()
    ta.join(timeout=5.0); tb.join(timeout=5.0)
    assert not ta.is_alive() and not tb.is_alive()

    # B started only after A's 401, so EVERY B IG call (even its profile resolve)
    # must land at or after the cooldown was slept out (NOW + base) — never at
    # NOW, which the pre-fix mutate-after-release race would have permitted.
    assert b_tx.times, "worker B never hit IG"
    assert min(b_tx.times) >= float(NOW) + base
    # The cooldown was slept exactly once at base before any post-401 window.
    assert base in clock.sleeps
    # Both handles still completed under the serialized, backed-off schedule.
    assert store.count_reels("a") == 1
    assert store.count_reels("b") == 1


# --- Step 6: aggregation, stable envelope for both scopes -------------------

def _seed_three(store):
    _seed(store, "a", code="a1", pk=11, plays=100)
    _seed(store, "a", code="a2", pk=12, plays=300)
    _seed(store, "b", code="b1", pk=21, plays=200)
    _seed(store, "c", code="c1", pk=31, plays=50)
    _seed(store, "c", code="c2", pk=32, plays=400)


def _agg_job(handles):
    return BatchJob("j", JobPhase.AGGREGATING,
                    params={"scope": "global", "count": 3, "sort_by": "play_count",
                            "filters": {}},
                    per_handle=[HandleProgress(h, outcome=OUTCOME_COVERED)
                                for h in handles])


def test_aggregate_global_true_merged_topn(tmp_path):
    config = _config(tmp_path)
    clock = FakeClock()
    deps, store, *_ = _make_deps(config, [], clock)
    _seed_three(store)
    job = _agg_job(["a", "b", "c"])
    env = _aggregate(job, deps=deps)
    assert list(env["results"].keys()) == ["*"]
    codes = [r["shortcode"] for r in env["results"]["*"]]
    assert codes == ["c2", "a2", "b1"]  # 400, 300, 200 across all channels


def test_aggregate_per_channel_independent_topn(tmp_path):
    config = _config(tmp_path)
    clock = FakeClock()
    deps, store, *_ = _make_deps(config, [], clock)
    _seed_three(store)
    job = _agg_job(["a", "b", "c"])
    job.params["scope"] = "per_channel"
    job.params["count"] = 2
    env = _aggregate(job, deps=deps)
    assert set(env["results"].keys()) == {"a", "b", "c"}
    assert [r["shortcode"] for r in env["results"]["a"]] == ["a2", "a1"]
    assert [r["shortcode"] for r in env["results"]["b"]] == ["b1"]
    assert [r["shortcode"] for r in env["results"]["c"]] == ["c2", "c1"]


def test_aggregate_envelope_keys_identical_across_scopes(tmp_path):
    config = _config(tmp_path)
    clock = FakeClock()
    deps, store, *_ = _make_deps(config, [], clock)
    _seed_three(store)
    g = _agg_job(["a", "b", "c"])
    g.params["scope"] = "global"
    p = _agg_job(["a", "b", "c"])
    p.params["scope"] = "per_channel"
    env_g = _aggregate(g, deps=deps)
    env_p = _aggregate(p, deps=deps)
    assert set(env_g.keys()) == set(env_p.keys())
    for key in ("job_id", "scope", "sort_by", "count", "filters", "status",
                "generated_at", "results", "per_handle_fetch", "downloads", "errors"):
        assert key in env_g and key in env_p


# --- Step 7: optional top-N download ----------------------------------------

def test_download_top_records_cached_and_typed_partial(tmp_path):
    config = _config(tmp_path, channels=["a"])
    clock = FakeClock()
    # "a1" is cached on disk (no network); "a2" is stale + no user_id so its
    # re-resolve 401s → a typed partial note, NOT a job failure.
    deps, store, gate, transport = _make_deps(config, [FakeResponse(401)], clock)
    _seed(store, "a", code="a1", pk=11, plays=100, fetched_at=NOW)
    mp4 = tmp_path / "media" / "a" / "a1.mp4"
    mp4.parent.mkdir(parents=True, exist_ok=True)
    mp4.write_bytes(b"\x00\x00\x00\x18ftypisomdata")
    store.update_local_mp4("a", "a1", local_mp4=str(mp4))
    # a2 stale (fetched long ago) and its state has a user_id so re-resolve pages.
    _seed(store, "a", code="a2", pk=12, plays=300, fetched_at=NOW - 10 * 86400)

    result_env = {"results": {"*": [{"shortcode": "a1"}, {"shortcode": "a2"}]}}
    store.save_batch_result("j", result_env)
    job = BatchJob("j", JobPhase.DOWNLOADING, params={"scope": "global"},
                   per_handle=[HandleProgress("a")], result_ref="j.result.json")

    _download_top(job, deps=deps, client=deps.client_factory(),
                  checkpoint=lambda: None)
    assert job.downloads["a1"]["local_mp4"] == str(mp4)
    assert job.downloads["a1"]["partial"] is False
    assert job.downloads["a2"]["partial"] is True   # metered re-resolve stop
    # the gate is not left held after a metered re-resolve inside the wrap
    with gate.acquire():
        pass


# --- Step 8: callback SSRF guard, retry, anonymity --------------------------

@pytest.mark.parametrize("url,ips,ok", [
    ("https://ok.example.com/cb", [PUBLIC_IP], True),
    ("http://ok.example.com/cb", [PUBLIC_IP], False),          # not https
    ("https://meta/cb", ["169.254.169.254"], False),           # cloud metadata
    ("https://internal/cb", ["10.1.2.3"], False),              # private
    ("https://loop/cb", ["127.0.0.1"], False),                 # loopback
    ("https://ll/cb", ["169.254.1.1"], False),                 # link-local
])
def test_callback_ssrf_guard(url, ips, ok):
    valid, ip, port, host, err = validate_callback_url(url, resolver=lambda h: ips)
    assert valid is ok
    if ok:
        assert ip == PUBLIC_IP and port == 443


def test_callback_rejected_target_never_posts(tmp_path):
    config = _config(tmp_path)
    clock = FakeClock()
    posted = []
    deps, store, gate, transport = _make_deps(
        config, [], clock,
        resolver=lambda h: ["10.0.0.1"],  # private → must be rejected
        poster=lambda *a, **k: posted.append(1) or 200,
    )
    store.save_batch_result("j", {"job_id": "j"})
    job = BatchJob("j", JobPhase.CALLING_BACK,
                   params={"callback_url": "https://internal/cb"},
                   per_handle=[], result_ref="j.result.json")
    _post_callback(job, deps=deps, checkpoint=lambda: None)
    assert posted == []  # no POST reached the internal target
    assert job.callback["delivered"] is False
    assert "rejected" in job.callback["last_status"]


def test_callback_retry_500_500_200(tmp_path):
    config = _config(tmp_path, batch_settings=BatchSettings(
        retries=5, backoff_base_s=2.0, backoff_cap_s=60.0))
    clock = FakeClock()
    statuses = iter([500, 500, 200])
    calls = []

    def poster(url, envelope, **kw):
        calls.append(kw)
        return next(statuses)

    deps, store, gate, transport = _make_deps(config, [], clock, poster=poster)
    store.save_batch_result("j", {"job_id": "j"})
    job = BatchJob("j", JobPhase.CALLING_BACK,
                   params={"callback_url": "https://ok.example.com/cb"},
                   per_handle=[], result_ref="j.result.json")
    _post_callback(job, deps=deps, checkpoint=lambda: None)
    assert len(calls) == 3
    assert job.callback["delivered"] is True
    # Backoff grew between attempts: base*2**0, base*2**1.
    assert clock.sleeps == [2.0, 4.0]


def test_callback_permanent_fail_still_reaches_done(tmp_path):
    config = _config(tmp_path, channels=["a"], batch_settings=BatchSettings(retries=3))
    clock = FakeClock()
    deps, store, gate, transport = _make_deps(
        config, [_profile(), _page([_clip(11, "a1", plays=100)], more=False)],
        clock, poster=lambda *a, **k: 500)
    out = run_start_batch_fetch(
        config=config, handles=None, scope="global", count=3,
        callback_url="https://ok.example.com/cb", deps=deps, background=False)
    status = run_get_batch_status(out["job_id"], config=config, deps=deps)
    assert status["phase"] == "done"                     # callback failure ≠ job failure
    assert status["callback"]["delivered"] is False       # exhausted retries
    assert status["result"]["results"]["*"][0]["shortcode"] == "a1"  # result durable


def test_default_poster_is_anonymous_no_redirect_and_pinned(monkeypatch):
    import curl_cffi.requests as cffi
    captured = {}

    class _R:
        status_code = 204

    def fake_post(url, **kw):
        captured["url"] = url
        captured.update(kw)
        return _R()

    monkeypatch.setattr(cffi, "post", fake_post)
    st = _default_poster("https://cb.example.com/x", {"job_id": "j"},
                         pinned_ip=PUBLIC_IP, port=443, host="cb.example.com")
    assert st == 204
    lower = {k.lower() for k in captured["headers"]}
    assert "x-ig-app-id" not in lower           # NOT an IG call
    assert "authorization" not in lower
    assert captured["allow_redirects"] is False  # redirect-follow disabled
    assert captured["resolve"] == [f"cb.example.com:443:{PUBLIC_IP}"]  # DNS-pin
    assert not captured.get("cookies")


# --- Step 9: entrypoints, validation, liveness, instant return --------------

def test_start_validates_scope_sort_count_callback(tmp_path):
    config = _config(tmp_path, channels=["a"])
    clock = FakeClock()
    deps, *_ = _make_deps(config, [], clock)
    assert run_start_batch_fetch(config=config, scope="weird", deps=deps,
                                 background=False)["ok"] is False
    assert run_start_batch_fetch(config=config, sort_by="bogus", deps=deps,
                                 background=False)["ok"] is False
    assert run_start_batch_fetch(config=config, count=0, deps=deps,
                                 background=False)["ok"] is False
    assert run_start_batch_fetch(config=config, handles=["not_a_channel"],
                                 deps=deps, background=False)["ok"] is False
    assert run_start_batch_fetch(
        config=config, callback_url="http://insecure/cb", deps=deps,
        background=False)["ok"] is False


def test_start_synchronous_reaches_done_with_topn(tmp_path):
    config = _config(tmp_path, channels=["natgeo"])
    clock = FakeClock()
    deps, store, gate, transport = _make_deps(
        config, [_profile(), _page([_clip(10, "AAA", plays=999)], more=False)], clock)
    out = run_start_batch_fetch(config=config, scope="global", count=3,
                                deps=deps, background=False)
    assert out["ok"] is True
    status = run_get_batch_status(out["job_id"], config=config, deps=deps)
    assert status["phase"] == "done"
    assert status["result"]["results"]["*"][0]["shortcode"] == "AAA"


def test_start_returns_instantly_then_job_completes(tmp_path):
    config = _config(tmp_path, channels=["natgeo"])
    clock = FakeClock()
    deps, store, gate, transport = _make_deps(
        config, [_profile(), _page([_clip(10, "AAA")], more=False)], clock)
    out = run_start_batch_fetch(config=config, scope="global", count=3,
                                deps=deps, background=True)
    assert out["phase"] == "queued"          # returned before the job finished
    job_id = out["job_id"]
    assert _wait_for(lambda: (store.load_batch_job(job_id) or {}).get("phase")
                     == "done")


def test_get_status_unknown_job_is_typed_not_found(tmp_path):
    config = _config(tmp_path)
    clock = FakeClock()
    deps, *_ = _make_deps(config, [], clock)
    status = run_get_batch_status("does-not-exist", config=config, deps=deps)
    assert status["found"] is False
    assert status["phase"] is None


def test_liveness_cooldown_vs_dead_worker(tmp_path):
    config = _config(tmp_path, batch_settings=BatchSettings(heartbeat_stale_s=100.0))
    clock = FakeClock()
    deps, store, *_ = _make_deps(config, [], clock)

    # cooldown-sleeping: a LIVE worker thread AND sleep_until in the future. The
    # daemon that sleeps out a cooldown is still alive (blocked in the gate), so
    # liveness is checked first (F2) and a live+sleeping job reads as sleeping.
    keep_alive = threading.Event()
    _launch("s", lambda: keep_alive.wait(timeout=2.0))
    try:
        sleeping = BatchJob("s", JobPhase.FETCHING, params={}, per_handle=[],
                            sleep_until=clock.now() + 500.0, heartbeat_at=clock.now())
        store.save_batch_job("s", sleeping.to_dict())
        assert run_get_batch_status("s", config=config, deps=deps)["liveness"] \
            == "cooldown-sleeping"
    finally:
        keep_alive.set()
        time.sleep(0.02)

    # dead-worker: no live thread, heartbeat older than the stale threshold.
    dead = BatchJob("d", JobPhase.FETCHING, params={}, per_handle=[],
                    heartbeat_at=clock.now() - 5000.0)
    store.save_batch_job("d", dead.to_dict())
    assert run_get_batch_status("d", config=config, deps=deps)["liveness"] \
        == "dead-worker"


def test_liveness_crash_mid_cooldown_is_not_masked_as_sleeping(tmp_path):
    # F2 regression: a worker that CRASHED mid-cooldown — thread gone, but the
    # last checkpoint still carries a future sleep_until — must NOT report
    # "cooldown-sleeping" (which would mask the crash for up to cooldown_cap_s).
    # With no live thread and a stale heartbeat it reads as dead-worker.
    config = _config(tmp_path, batch_settings=BatchSettings(heartbeat_stale_s=100.0))
    clock = FakeClock()
    deps, store, *_ = _make_deps(config, [], clock)
    crashed = BatchJob("c", JobPhase.FETCHING, params={}, per_handle=[],
                       sleep_until=clock.now() + 500.0,        # cooldown still ahead
                       heartbeat_at=clock.now() - 5000.0)      # but heartbeat stale
    store.save_batch_job("c", crashed.to_dict())
    assert run_get_batch_status("c", config=config, deps=deps)["liveness"] \
        == "dead-worker"


# --- Step 9: restart resume + idempotency -----------------------------------

def test_resume_pending_jobs_readopts_from_checkpoint(tmp_path):
    config = _config(tmp_path, channels=["a", "b"])
    clock = FakeClock()
    # handle "a" already covered (seed its manifest); "b" still pending. The
    # checkpoint is written as if a prior worker died mid-fetch after "a".
    deps, store, gate, transport = _make_deps(
        config, [_profile(), _page([_clip(21, "BBB")], more=False)], clock)
    _seed(store, "a", code="AAA", pk=11, plays=100)
    a_count_before = store.count_reels("a")
    job = BatchJob(
        "resumed", JobPhase.FETCHING,
        params={"handles": ["a", "b"], "scope": "global", "count": 3,
                "sort_by": "play_count", "filters": {}, "download_top": False,
                "callback_url": None},
        per_handle=[HandleProgress("a", outcome=OUTCOME_COVERED),
                    HandleProgress("b")],
        heartbeat_at=clock.now(), created=clock.now(), updated=clock.now())
    store.save_batch_job("resumed", job.to_dict())

    # Simulate a full process restart: no threads, in-memory state gone.
    batch.reset_batch_state()
    resume_pending_jobs(config, deps=deps)
    assert _wait_for(lambda: (store.load_batch_job("resumed") or {}).get("phase")
                     == "done")

    reloaded = BatchJob.from_dict(store.load_batch_job("resumed"))
    assert reloaded.progress("a").outcome == OUTCOME_COVERED
    assert reloaded.progress("b").outcome == OUTCOME_COVERED
    assert store.count_reels("a") == a_count_before  # "a" untouched on resume
    assert store.count_reels("b") == 1
    # No profile re-resolve for "a" (it was skipped); only "b" was fetched.
    result = store.load_batch_result("resumed")
    codes = {r["shortcode"] for r in result["results"]["*"]}
    assert codes == {"AAA", "BBB"}
    assert len(codes) == 2  # no duplicate shortcodes


def test_resume_terminal_jobs_are_not_relaunched(tmp_path):
    config = _config(tmp_path)
    clock = FakeClock()
    deps, store, *_ = _make_deps(config, [], clock)
    store.save_batch_job("done1", BatchJob("done1", JobPhase.DONE, params={},
                                           per_handle=[]).to_dict())
    out = resume_pending_jobs(config, deps=deps)
    assert out["relaunched"] == []


def test_launch_guard_is_idempotent():
    gate_open = threading.Event()

    def blocker():
        gate_open.wait(timeout=2.0)

    try:
        started1 = _launch("dup", blocker)
        started2 = _launch("dup", blocker)   # already alive → not relaunched
        assert started1 is True
        assert started2 is False
    finally:
        gate_open.set()
        time.sleep(0.02)
