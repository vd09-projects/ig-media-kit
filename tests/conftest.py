"""Shared test fixtures — offline only. No network.

A ``FakeTransport`` records what the client sent (so anonymity can be asserted)
and returns canned responses driving the fetch loop against the captured feed
JSON fixture.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


def load_feed() -> dict:
    with (FIXTURES / "feed_sample.json").open("r", encoding="utf-8") as fh:
        return json.load(fh)


class FakeResponse:
    """Mimics the transport return contract (status_code/headers/url/text/json)."""

    def __init__(self, status_code: int, body: Any = None, headers: dict | None = None,
                 url: str = "", cookies: dict | None = None, content: bytes = b""):
        self.status_code = status_code
        self._body = body
        self.headers = headers or {}
        self.url = url
        self.cookies = cookies or {}
        # Raw bytes for a binary (CDN mp4) fetch — read by AnonymousClient.download_cdn.
        self.content = content
        self.text = "" if body is None else (body if isinstance(body, str) else json.dumps(body))

    def json(self) -> Any:
        if self._body is None or isinstance(self._body, str):
            raise ValueError("no json body")
        return self._body


class FakeTransport:
    """Injectable transport. ``responses`` is a list consumed FIFO; each call
    records the (method, url, headers, params, cookies, allow_redirects) sent."""

    def __init__(self, responses: list[FakeResponse]):
        self._responses = list(responses)
        self.calls: list[dict] = []

    def __call__(self, method, url, *, headers, params, cookies, impersonate, allow_redirects):
        self.calls.append({
            "method": method, "url": url, "headers": dict(headers),
            "params": dict(params) if params else {},
            "cookies": dict(cookies) if cookies else {},
            "impersonate": impersonate, "allow_redirects": allow_redirects,
        })
        if not self._responses:
            raise AssertionError(f"FakeTransport exhausted on call to {url}")
        return self._responses.pop(0)


@pytest.fixture
def feed_body() -> dict:
    return load_feed()
