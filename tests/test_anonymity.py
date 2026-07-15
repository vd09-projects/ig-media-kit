"""Anonymity guard: the required header is sent; NO auth cookies/params/headers
ever leave the client; benign anonymous cookies are permitted."""

from __future__ import annotations

import pytest

from ig_media_kit import IG_APP_ID
from ig_media_kit.http_client import (
    AnonymityViolation,
    AnonymousClient,
    assert_anonymous,
)
from tests.conftest import FakeResponse, FakeTransport, load_feed


def test_required_header_present_and_no_auth_sent():
    transport = FakeTransport([FakeResponse(200, load_feed())])
    client = AnonymousClient(transport, cookies={"mid": "abc", "csrftoken": "z"})
    client.get_api("https://i.instagram.com/api/v1/feed/user/1/", params={"count": 12})
    call = transport.calls[0]
    # mandatory header on every API call
    assert call["headers"]["x-ig-app-id"] == IG_APP_ID
    # benign cookies allowed; NO auth cookie present
    assert "mid" in call["cookies"]
    assert "sessionid" not in call["cookies"]
    assert "ds_user_id" not in call["cookies"]
    # metadata calls do NOT transparently follow redirects
    assert call["allow_redirects"] is False
    # no auth params
    assert "access_token" not in call["params"]


def test_benign_cookies_permitted():
    assert_anonymous(cookies={"mid": "x", "csrftoken": "y", "ig_did": "z", "datr": "d"})
    # constructing a client with benign cookies must not raise
    AnonymousClient(FakeTransport([]), cookies={"mid": "x"})


def test_auth_cookie_rejected_on_construction():
    with pytest.raises(AnonymityViolation):
        AnonymousClient(FakeTransport([]), cookies={"sessionid": "SECRET"})


def test_auth_cookie_rejected_on_update():
    client = AnonymousClient(FakeTransport([]), cookies={"mid": "x"})
    with pytest.raises(AnonymityViolation):
        client.update_cookies({"ds_user_id": "12345"})


def test_auth_param_rejected():
    with pytest.raises(AnonymityViolation):
        assert_anonymous(params={"access_token": "T"})


def test_auth_header_rejected():
    with pytest.raises(AnonymityViolation):
        assert_anonymous(headers={"Authorization": "Bearer T"})


def test_ig_set_auth_cookie_from_server_not_stored():
    # If IG hands back an auth cookie, the client refuses to store it (guarded)
    # rather than crashing the fetch.
    resp = FakeResponse(200, load_feed(), cookies={"sessionid": "EVIL", "mid": "ok"})
    client = AnonymousClient(FakeTransport([resp]), cookies={})
    client.get_api("https://i.instagram.com/api/v1/feed/user/1/", params={"count": 12})
    assert "sessionid" not in client.cookies
