"""Offline fixture/dry-run smoke harness for the wired MCP surface (T5, AC#4).

Exercises the full four-tool wiring end to end —
``list_reels`` -> ``download_reel`` -> ``start_batch_fetch`` (+ a local callback
receiver) -> ``get_batch_status`` — plus a simulated mid-fetch rate-limit, with
**ZERO IG network**. It installs the SAME shared server context ``main()`` installs
(``mcp_server.startup``) and drives the tools through it, injecting fakes ONLY where
a network stub is unavoidable:

  * the CDN body for ``download_reel`` (ftyp-valid bytes via a fake transport),
  * the IG feed transport for the batch fill (fixture feed via a fake transport),
  * the callback SINK — a real localhost HTTP receiver reached through an injected
    ``poster`` (the TEST SEAM). The production ``https``/SSRF callback guard is NOT
    loosened: ``validate_callback_url`` runs unchanged against an injected
    public-IP resolver and passes on a benign ``https`` URL, while the actual
    delivery is the injected poster's POST to ``127.0.0.1``.

The whole run executes under a guard that makes building a REAL network transport a
hard failure — so "zero IG network" is ENFORCED, not merely intended.

    Fixture mode (default):   python -m probe.probe_smoke
    CI:                       pytest tests/test_smoke.py

The LIVE run (real public handle, real CDN) is DEFERRED to pilot tickets #10/#14. It
is opt-in only (``IG_MK_SMOKE_LIVE=1``) and is NOT implemented as a passing run here —
it prints the documented procedure and exits WITHOUT faking a green live result.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import tempfile
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

from ig_media_kit import batch, coverage, fetch_gate, mcp_server
from ig_media_kit.batch import BatchDeps, run_start_batch_fetch
from ig_media_kit.config import load_config
from ig_media_kit.download import run_download_reel
from ig_media_kit.fetch import FetchMode, normalize_item
from ig_media_kit.fill import run_fill
from ig_media_kit.http_client import AnonymousClient

# A globally-routable literal so the (unchanged) SSRF guard accepts the benign
# https callback host — the injected resolver returns THIS, not a real DNS lookup.
_PUBLIC_IP = "93.184.216.34"
# A minimal ISO-BMFF/mp4 opener: 4-byte box size, then the 'ftyp' box TYPE at
# offset 4 (matches tests/test_download.MP4_BYTES).
_MP4_BYTES = b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isomiso2"
_USER_ID = "787132"
_FIXTURE = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "feed_sample.json"


# --- fakes (transport + local callback sink) --------------------------------


class _FakeResponse:
    """Mimics the transport return contract (status_code/headers/url/text/json/content)."""

    def __init__(self, status_code: int, body: Any = None, *, content: bytes = b""):
        self.status_code = status_code
        self._body = body
        self.headers: dict[str, Any] = {}
        self.url = ""
        self.cookies: dict[str, Any] = {}
        self.content = content
        self.text = "" if body is None else (body if isinstance(body, str) else json.dumps(body))

    def json(self) -> Any:
        if self._body is None or isinstance(self._body, str):
            raise ValueError("no json body")
        return self._body


class _FakeTransport:
    """Injectable transport — ``responses`` consumed FIFO; records each call."""

    def __init__(self, responses: list[_FakeResponse]):
        self._responses = list(responses)
        self.calls: list[str] = []

    def __call__(self, method, url, *, headers, params, cookies, impersonate, allow_redirects):
        self.calls.append(url)
        if not self._responses:
            raise AssertionError(f"fake transport exhausted on call to {url}")
        return self._responses.pop(0)


class _CallbackSink:
    """A REAL localhost HTTP receiver — the injected poster delivers to it, proving a
    genuine local POST round-trip without loosening the production callback guard."""

    def __init__(self) -> None:
        self.received: list[dict[str, Any]] = []
        sink = self

        class _Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802 — http.server naming
                length = int(self.headers.get("Content-Length", 0))
                raw = self.rfile.read(length) if length else b"{}"
                try:
                    sink.received.append(json.loads(raw.decode("utf-8")))
                except ValueError:
                    sink.received.append({"_unparseable": raw[:200].decode("latin-1")})
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"ok")

            def log_message(self, *_a) -> None:  # silence the default stderr logging
                return

        self._server = HTTPServer(("127.0.0.1", 0), _Handler)
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def poster(self, url, envelope, *, pinned_ip, port, host) -> int:
        """Injected callback transport (the TEST SEAM). Ignores the guard-validated
        target and delivers the envelope to the localhost sink over plain HTTP —
        the production ``_default_poster`` (curl_cffi, https, redirect-off) is not
        used, and the production guard that validated ``url`` is left intact."""
        body = json.dumps(envelope).encode("utf-8")
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/callback", data=body,
            headers={"Content-Type": "application/json"}, method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:  # noqa: S310 — localhost only
            return int(resp.status)

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()


def _forbid_real_transport() -> Any:
    raise AssertionError(
        "fixture-mode smoke tried to build a REAL network transport — "
        "the zero-IG invariant is broken (a code path constructed a live client)"
    )


# --- fixtures / seeding ------------------------------------------------------


def _load_items() -> list[dict[str, Any]]:
    with _FIXTURE.open("r", encoding="utf-8") as fh:
        return json.load(fh)["items"]


def _write_config(tmp: Path) -> Path:
    cfg = tmp / "config.yaml"
    cfg.write_text(
        "channels:\n  - natgeo\n  - nike\n"
        "top_reels:\n  count: 5\n  sort_by: play_count\n  min_play_count: 0\n"
        "fetch:\n  scan_depth: 90\n  max_pages_per_call: 4\n  page_pace_seconds: 1.5\n"
        f"output:\n  store_dir: {tmp / 'store'}\n  media_dir: {tmp / 'media'}\n",
        encoding="utf-8",
    )
    return cfg


def _seed_contiguous(store, handle: str, items: list[dict[str, Any]], fetched_at: int) -> list:
    """Seed a handle with the fixture clips + a single terminal coverage segment so
    ``list_reels`` serves from the store with zero network."""
    reels = [r for r in (normalize_item(i, fetched_at) for i in items) if r]
    store.write_window(handle, reels, user_id=_USER_ID, next_cursor=None,
                       stop_reason="end_of_feed", mode=FetchMode.TOP_SCAN)
    pks = [r.media_id for r in reels]
    store.save_coverage_segments(
        handle, [coverage._segment(max(pks), min(pks), None, True)])
    return reels


def _feed_responses(items: list[dict[str, Any]]) -> list[_FakeResponse]:
    """A cold-fetch feed: profile (user_id) then one end-of-feed page."""
    return [
        _FakeResponse(200, {"data": {"user": {"id": _USER_ID}}}),
        _FakeResponse(200, {"num_results": len(items), "more_available": False,
                            "next_max_id": None, "items": items}),
    ]


# --- the smoke run -----------------------------------------------------------


def run_smoke() -> dict[str, Any]:
    """Drive the wired surface offline and return a per-step trace. Raises
    AssertionError on any wiring failure. Enforces zero real IG network."""
    import ig_media_kit.http_client as http_client

    trace: dict[str, Any] = {}
    tmp = Path(tempfile.mkdtemp(prefix="ig-smoke-"))
    sink = _CallbackSink()
    real_transport = http_client._default_transport
    http_client._default_transport = _forbid_real_transport  # zero-IG guard
    prior_env = os.environ.pop("IG_MK_CONFIG", None)
    try:
        fetch_gate.reset_gate()
        batch.reset_batch_state()
        mcp_server.reset_context()

        items = _load_items()
        now = int(time.time())
        cfg_path = _write_config(tmp)

        # --- startup: install the shared context + EXPLICIT resume before serving ---
        resumed = mcp_server.startup(str(cfg_path))
        ctx = mcp_server.current_context()
        assert ctx is not None, "startup did not install a server context"
        assert resumed["count"] == 0, f"clean store should re-adopt 0 jobs, got {resumed}"
        trace["startup"] = {"resumed": resumed["count"], "store_dir": ctx.config.output.store_dir}

        # Seed natgeo (list + download); leave nike/coldzone cold for the fetch paths.
        seeded = _seed_contiguous(ctx.store, "natgeo", items, fetched_at=now)
        top_shortcode = max(seeded, key=lambda r: r.play_count or 0).shortcode

        # --- 1) list_reels through the wired TOOL (serve-from-store, zero network) ---
        listed = mcp_server.list_reels("natgeo")
        assert not listed.get("error"), f"list_reels errored: {listed}"
        assert listed["reels"], "list_reels returned no reels from the seeded store"
        assert listed["partial"] is False and listed["pages_fetched"] == 0, \
            f"served-from-store should be non-partial, zero-page: {listed}"
        plays = [r.get("play_count") or 0 for r in listed["reels"]]
        assert plays == sorted(plays, reverse=True), f"reels not ranked desc: {plays}"
        trace["list_reels"] = {"count": listed["count_returned"], "top": listed["reels"][0]["shortcode"]}

        # --- 2) download_reel against a STUBBED CDN body (ftyp bytes), shared ctx ---
        cdn_client = AnonymousClient(_FakeTransport([_FakeResponse(200, content=_MP4_BYTES)]))
        dl = run_download_reel(top_shortcode, config=ctx.config, store=ctx.store,
                               client=cdn_client, now=lambda: now)
        assert not dl.get("error"), f"download_reel errored: {dl}"
        assert dl["local_mp4"] and Path(dl["local_mp4"]).exists(), f"no mp4 written: {dl}"
        assert Path(dl["local_mp4"]).read_bytes() == _MP4_BYTES, "written mp4 != stubbed body"
        trace["download_reel"] = {"shortcode": top_shortcode, "local_mp4": dl["local_mp4"]}

        # --- 3) start_batch_fetch: daemon fill of a COLD handle via injected seams ---
        feed = _FakeTransport(_feed_responses(items))
        deps = BatchDeps(
            config=ctx.config, store=ctx.store, gate=ctx.gate,
            client_factory=lambda: AnonymousClient(feed),
            resolver=lambda _host: [_PUBLIC_IP],   # guard runs unchanged; resolves public
            poster=sink.poster,                     # TEST SEAM: deliver to localhost sink
            jitter=lambda _base: 0.0,
        )
        started = run_start_batch_fetch(
            config=ctx.config, handles=["nike"], scope="global", count=3,
            sort_by="play_count", callback_url="https://smoke.example/callback",
            deps=deps, background=True,
        )
        assert started.get("ok"), f"start_batch_fetch rejected: {started}"
        job_id = started["job_id"]

        # --- 4) get_batch_status through the wired TOOL until terminal (IG-free) ---
        deadline = time.monotonic() + 5.0
        status = mcp_server.get_batch_status(job_id)
        while status.get("phase") not in ("done", "failed") and time.monotonic() < deadline:
            time.sleep(0.02)
            status = mcp_server.get_batch_status(job_id)
        assert status["found"] and status["phase"] == "done", f"batch did not finish: {status}"
        assert sink.received, "callback sink received nothing"
        cb_results = sink.received[-1].get("results") or {}
        assert cb_results, f"callback envelope carried no results: {sink.received[-1]}"
        trace["start_batch_fetch"] = {"job_id": job_id, "phase": status["phase"],
                                      "callback_delivered": bool(sink.received)}
        trace["get_batch_status"] = {"phase": status["phase"],
                                     "per_handle": status["per_handle"]}

        # --- 5) simulated mid-fetch 401 through the command-side FILL primitive -----
        # After the CQRS split (T17) list_reels never fetches, so the mid-fetch 401
        # politeness path lives in fill.run_fill (the batch runner's fetch unit).
        rl_transport = _FakeTransport([
            _FakeResponse(200, {"data": {"user": {"id": _USER_ID}}}),  # profile ok
            _FakeResponse(401),                                        # first feed page 401
        ])
        rl = run_fill("coldzone", config=ctx.config, store=ctx.store,
                      client=AnonymousClient(rl_transport), now=lambda: now)
        assert rl["partial"] is True, f"401 must yield a partial: {rl}"
        assert rl["stop_reason"] == "rate_limited", f"unexpected stop_reason: {rl}"
        # Politeness counter-metric: stopped on the FIRST 401 (profile + one page only),
        # capped, never kept paging, never slept (the fill primitive never sleeps).
        assert len(rl_transport.calls) == 2, \
            f"did not stop on first 401 (paged {len(rl_transport.calls)} times)"
        assert rl["pages_fetched"] <= ctx.config.fetch.max_pages_per_call
        trace["mid_fetch_401"] = {"partial": rl["partial"], "stop_reason": rl["stop_reason"],
                                  "ig_calls": len(rl_transport.calls)}

        # --- zero-IG proof: no code path built a real transport this whole run ------
        assert http_client._default_transport is _forbid_real_transport
        trace["zero_ig_network"] = True
        return trace
    finally:
        http_client._default_transport = real_transport
        if prior_env is not None:
            os.environ["IG_MK_CONFIG"] = prior_env
        sink.close()
        fetch_gate.reset_gate()
        batch.reset_batch_state()
        mcp_server.reset_context()
        shutil.rmtree(tmp, ignore_errors=True)


_LIVE_PROCEDURE = """\
LIVE smoke — DEFERRED to pilot tickets #10 / #14 (NOT run here).

A live pass hits real Instagram + fbcdn and therefore consumes THIS IP's escalating
rate-limit budget (~48 items / ~6.6-min window, cooldown escalating ~6.6 -> ~13 min).
It is intentionally not automated and never runs in CI. When the pilots are ready,
run it by hand against a public handle you choose:

  1. Point at a THROWAWAY store so it can't adopt a real batch's checkpoints:
        export IG_MK_CONFIG=/tmp/ig-smoke-live/config.yaml   # store_dir under /tmp
  2. Run one anonymous list + one download against a real public handle:
        python -m probe.probe_spike <handle>       # anonymous feed walk + views
        python -m probe.probe_download <shortcode> # real fbcdn mp4 download
  3. Optionally exercise the async runner end to end (it sleeps real cooldowns):
        python -m probe.probe_batch <handleA> <handleB> <handleC>
  4. Confirm: reels carry play_count, the mp4 is ftyp-valid, and a metered stop
     returns a partial (never a block). Record the outcome on #10 / #14.

This command does NOT perform the live run and does NOT fake a passing result.
"""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="probe.probe_smoke",
        description="Offline fixture/dry-run smoke for the wired MCP surface "
                    "(live run deferred to pilots #10/#14).",
    )
    ap.add_argument("--handle", default=None,
                    help="public handle for the DEFERRED live run (with IG_MK_SMOKE_LIVE=1)")
    args = ap.parse_args(argv)

    if os.environ.get("IG_MK_SMOKE_LIVE") == "1":
        print(_LIVE_PROCEDURE)
        if args.handle:
            print(f"(requested live handle: {args.handle} — run the steps above manually)")
        return 0

    trace = run_smoke()
    print("ig-media-kit fixture smoke — wired surface, ZERO IG network\n")
    for step in ("startup", "list_reels", "download_reel", "start_batch_fetch",
                 "get_batch_status", "mid_fetch_401", "zero_ig_network"):
        print(f"  [ok] {step:<18} {trace.get(step)}")
    print("\nSMOKE OK — list -> download -> batch+callback -> status, plus mid-fetch 401")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
