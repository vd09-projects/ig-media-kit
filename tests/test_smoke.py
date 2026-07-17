"""CI wrapper for the offline fixture/dry-run smoke harness (T5, AC#4).

Exercises the full wired surface — list_reels -> download_reel ->
start_batch_fetch (+ local callback sink) -> get_batch_status, plus a simulated
mid-fetch 401 — with ZERO IG network. The live run is DEFERRED to pilots #10/#14
and is NOT invoked here."""

from __future__ import annotations

from probe.probe_smoke import run_smoke


def test_fixture_smoke_exercises_the_wired_surface():
    trace = run_smoke()

    for step in ("startup", "list_reels", "download_reel", "start_batch_fetch",
                 "get_batch_status", "mid_fetch_401"):
        assert step in trace, f"smoke skipped {step}: {trace}"

    assert trace["startup"]["resumed"] == 0
    assert trace["list_reels"]["count"] >= 1
    assert trace["download_reel"]["local_mp4"]
    assert trace["start_batch_fetch"]["callback_delivered"] is True
    assert trace["get_batch_status"]["phase"] == "done"

    # A mid-fetch 401 through the wired context is a partial with a stop_reason,
    # and it stopped on the FIRST 401 (politeness counter-metric).
    assert trace["mid_fetch_401"]["partial"] is True
    assert trace["mid_fetch_401"]["stop_reason"] == "rate_limited"
    assert trace["mid_fetch_401"]["ig_calls"] == 2

    assert trace["zero_ig_network"] is True
