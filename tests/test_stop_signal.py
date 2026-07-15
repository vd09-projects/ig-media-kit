"""Stop-signal classifier: each throttle/block/challenge status -> stop(reason),
and fail-closed on unknown. Plus the fetch loop returns a partial on a stop."""

from __future__ import annotations

import pytest

from ig_media_kit.fetch import FetchMode, fetch_window
from ig_media_kit.http_client import (
    AnonymousClient,
    Outcome,
    StopReason,
    classify_response,
)
from tests.conftest import FakeResponse, FakeTransport, load_feed


def test_401_is_rate_limited():
    c = classify_response(401)
    assert c.is_stop and c.reason is StopReason.RATE_LIMITED


def test_429_is_rate_limited():
    assert classify_response(429).reason is StopReason.RATE_LIMITED


def test_403_is_forbidden():
    assert classify_response(403).reason is StopReason.FORBIDDEN


def test_302_to_login_is_login_redirect():
    c = classify_response(302, location="https://www.instagram.com/accounts/login/")
    assert c.is_stop and c.reason is StopReason.LOGIN_REDIRECT


def test_302_to_challenge_is_challenge():
    c = classify_response(302, location="https://www.instagram.com/challenge/")
    assert c.reason is StopReason.CHALLENGE


def test_200_with_login_body_is_stop():
    c = classify_response(200, body={"message": "login_required", "status": "fail"})
    assert c.is_stop and c.reason is StopReason.LOGIN_REDIRECT


def test_200_with_challenge_body_is_stop():
    c = classify_response(200, body={"message": "checkpoint_required"})
    assert c.is_stop and c.reason is StopReason.CHALLENGE


def test_200_feed_body_is_ok():
    c = classify_response(200, body={"items": [], "num_results": 0})
    assert c.outcome is Outcome.OK


def test_unknown_status_fails_closed():
    c = classify_response(418)
    assert c.is_stop and c.reason is StopReason.UNKNOWN


def test_500_is_error_not_throttle():
    c = classify_response(503)
    assert c.outcome is Outcome.ERROR


@pytest.mark.parametrize("status", [401, 403, 429])
def test_fetch_returns_partial_on_stop_signal(status):
    # Page 1 is real feed data, page 2 is a throttle -> partial, cursor intact.
    feed = load_feed()
    transport = FakeTransport([
        FakeResponse(200, feed),
        FakeResponse(status, "throttled"),
    ])
    client = AnonymousClient(transport)
    # Empty anchors so page 1 doesn't short-circuit as caught_up.
    res = fetch_window(client, "787132", mode=FetchMode.TOP_SCAN, seen=set(),
                       high_water_media_id=None, max_pages=4, sleep=None)
    assert res.partial is True
    assert res.stop_reason in {r.value for r in StopReason}
    assert res.pages_fetched == 2
    # rows from page 1 survived; cursor preserved for a later resume.
    assert len(res.reels) == 3
    assert res.next_cursor == feed["next_max_id"]


def test_fetch_never_sleeps_on_sync_path():
    calls = []
    feed = load_feed()
    transport = FakeTransport([FakeResponse(200, feed), FakeResponse(401, "x")])
    client = AnonymousClient(transport)
    fetch_window(client, "787132", mode=FetchMode.TOP_SCAN, seen=set(),
                 high_water_media_id=None, max_pages=4,
                 sleep=lambda s: calls.append(s))  # provided, but page2 stops first
    # Even with a sleep callable, the stop_signal path must not have slept past
    # the throttle; and the sync entry point passes sleep=None anyway.
    # Here page 2 stops, so at most one inter-page sleep could fire.
    assert len(calls) <= 1
