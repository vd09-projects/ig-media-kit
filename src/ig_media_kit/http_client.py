"""Anonymous HTTP client wrapper + stop_signal classifier + anonymity guard.

Task T1.3. Three load-bearing contracts (see CLAUDE.md invariants + plan):

1. ``classify_response`` — the single stop_signal classifier. EVERY IG-hitting
   caller branches on this, never on a raw status code. It maps the whole
   throttle/block/challenge family (401/403/429, 302-to-login/challenge,
   200+challenge-JSON) to a typed stop reason, and FAILS CLOSED: anything it
   cannot positively classify as feed-shaped data becomes ``stop(unknown)``.
2. ``assert_anonymous`` — ANONYMOUS is keyed off *auth* cookies/params, not off
   all cookies. Benign anonymous cookies (mid/csrftoken/ig_did/datr) are
   permitted (and may be required); sessionid/ds_user_id/auth params/Authorization
   header are rejected. Enforced in code so no later tool can introduce auth.
3. ``AnonymousClient`` — thin curl_cffi wrapper, impersonate="chrome", mandatory
   ``x-ig-app-id`` header on every API call. Metadata calls do NOT transparently
   follow a 302 (a login/challenge redirect is a stop_signal — the classifier
   inspects the target). CDN calls (fbcdn) DO follow redirects (download is a
   later ticket, but the capability lives here).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Mapping, Protocol

from . import IG_APP_ID

# --- Anonymity policy -------------------------------------------------------

# Cookies that indicate an *authenticated* session — their presence is a hard
# anonymity violation.
AUTH_COOKIE_NAMES = frozenset({"sessionid", "ds_user_id", "ds_user"})
# Request param / header names that carry auth — also rejected.
AUTH_PARAM_NAMES = frozenset({"access_token", "sessionid", "ds_user_id", "signed_body"})
AUTH_HEADER_NAMES = frozenset({"authorization", "x-ig-set-www-claim", "ig-u-ds-user-id"})

# The one mandatory header on every metadata API call.
API_HEADERS = {
    "x-ig-app-id": IG_APP_ID,
    "Accept": "application/json",
}

IMPERSONATE = "chrome"


class AnonymityViolation(RuntimeError):
    """Raised when a request would carry an authenticated session or auth params.

    This is a programming error (a code path tried to authenticate) — it must
    never be caught-and-continued; it exists to make the anonymous-only
    invariant unbreakable in code.
    """


def assert_anonymous(
    *,
    headers: Mapping[str, Any] | None = None,
    cookies: Mapping[str, Any] | None = None,
    params: Mapping[str, Any] | None = None,
) -> None:
    """Assert a request carries no authenticated session or auth params.

    Permits benign anonymous cookies (mid/csrftoken/ig_did/datr/...). Raises
    :class:`AnonymityViolation` naming the offending key otherwise.
    """
    for name in (cookies or {}):
        if name.lower() in AUTH_COOKIE_NAMES:
            raise AnonymityViolation(f"auth cookie present: {name!r} — anonymous-only invariant")
    for name in (params or {}):
        if name.lower() in AUTH_PARAM_NAMES:
            raise AnonymityViolation(f"auth param present: {name!r} — anonymous-only invariant")
    for name in (headers or {}):
        if name.lower() in AUTH_HEADER_NAMES:
            raise AnonymityViolation(f"auth header present: {name!r} — anonymous-only invariant")


# --- Stop-signal classifier -------------------------------------------------


class StopReason(str, Enum):
    """Typed reasons the fetch loop stops. Distinguishes ABNORMAL stops (the
    throttle/block/challenge family) from NORMAL end-of-walk reasons, which are
    emitted by the fetch layer, not the classifier."""

    RATE_LIMITED = "rate_limited"
    LOGIN_REDIRECT = "login_redirect"
    CHALLENGE = "challenge"
    FORBIDDEN = "forbidden"
    UNKNOWN = "unknown"


# The set of abnormal (throttle/block/challenge) reasons — used by callers to
# tell a stop_signal apart from a normal caught_up/end_of_feed/page_cap stop.
STOP_SIGNAL_REASONS = frozenset(r.value for r in StopReason)


class Outcome(str, Enum):
    OK = "ok"
    STOP = "stop"
    ERROR = "error"


@dataclass(frozen=True)
class Classification:
    """Result of classifying one response.

    ``outcome`` is ``ok`` (feed-shaped data, proceed), ``stop`` (a stop_signal —
    the caller must stop the page-walk and return a partial, with ``reason``
    set), or ``error`` (a transport/5xx failure worth surfacing distinctly).
    """

    outcome: Outcome
    reason: StopReason | None = None
    status_code: int | None = None
    detail: str = ""

    @property
    def is_stop(self) -> bool:
        return self.outcome is Outcome.STOP


# Body markers that signal a login/challenge wall on a 200 (or any) response.
_LOGIN_MARKERS = ("login_required", "require_login", "please wait a few minutes")
_CHALLENGE_MARKERS = ("checkpoint_required", "challenge_required", "challenge", "spam")
# Substrings in a redirect Location that mean "bounced to a login/challenge wall".
_LOGIN_REDIRECT_PATHS = ("/accounts/login", "/login")
_CHALLENGE_REDIRECT_PATHS = ("/challenge", "/checkpoint")


def _body_text(body: Any) -> str:
    if body is None:
        return ""
    if isinstance(body, str):
        return body.lower()
    if isinstance(body, Mapping):
        # Flatten a shallow JSON body to text so we can substring-scan markers.
        parts = []
        for k, v in body.items():
            parts.append(str(k))
            if isinstance(v, (str, int, float, bool)):
                parts.append(str(v))
        return " ".join(parts).lower()
    return str(body).lower()


def classify_response(
    status_code: int,
    *,
    headers: Mapping[str, Any] | None = None,
    body: Any = None,
    location: str | None = None,
) -> Classification:
    """Classify one IG response into ok | stop(reason) | error. FAILS CLOSED.

    ``body`` may be a parsed JSON mapping, a raw text string, or None.
    ``location`` is the redirect target for a 3xx (or pass it explicitly).
    Anything not positively feed-shaped is treated as a stop, not as ``ok``.
    """
    headers = headers or {}
    loc = (location or headers.get("location") or headers.get("Location") or "").lower()

    # --- explicit throttle/block status codes ---
    if status_code == 401:
        return Classification(Outcome.STOP, StopReason.RATE_LIMITED, status_code,
                              "401 — anonymous rate-limit / require_login")
    if status_code == 429:
        return Classification(Outcome.STOP, StopReason.RATE_LIMITED, status_code, "429 too many requests")
    if status_code == 403:
        return Classification(Outcome.STOP, StopReason.FORBIDDEN, status_code, "403 forbidden")

    # --- redirects: inspect the target rather than following it ---
    if 300 <= status_code < 400:
        if any(p in loc for p in _CHALLENGE_REDIRECT_PATHS):
            return Classification(Outcome.STOP, StopReason.CHALLENGE, status_code, f"redirect to challenge: {loc}")
        if any(p in loc for p in _LOGIN_REDIRECT_PATHS):
            return Classification(Outcome.STOP, StopReason.LOGIN_REDIRECT, status_code, f"redirect to login: {loc}")
        # Any other unexpected redirect on a metadata call — fail closed.
        return Classification(Outcome.STOP, StopReason.UNKNOWN, status_code, f"unexpected redirect: {loc}")

    # --- 200: could still be a challenge/login wall in the body ---
    if status_code == 200:
        text = _body_text(body)
        if text:
            if any(m in text for m in _CHALLENGE_MARKERS):
                return Classification(Outcome.STOP, StopReason.CHALLENGE, status_code, "200 challenge-body")
            if any(m in text for m in _LOGIN_MARKERS):
                return Classification(Outcome.STOP, StopReason.LOGIN_REDIRECT, status_code, "200 login-required body")
        return Classification(Outcome.OK, None, status_code, "ok")

    # --- server errors: surface distinctly, not as a throttle ---
    if status_code >= 500:
        return Classification(Outcome.ERROR, None, status_code, f"server error {status_code}")

    # --- anything else (fail closed) ---
    return Classification(Outcome.STOP, StopReason.UNKNOWN, status_code, f"unclassified status {status_code}")


# --- HTTP wrapper -----------------------------------------------------------


class Transport(Protocol):
    """Minimal request transport — curl_cffi's ``Session.request`` matches it.

    Injectable so the client is unit-testable without network. Must return an
    object exposing ``status_code``, ``headers`` (Mapping), ``url``, ``text``,
    and a ``json()`` method (may raise if the body is not JSON)."""

    def __call__(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, Any],
        params: Mapping[str, Any] | None,
        cookies: Mapping[str, Any] | None,
        impersonate: str,
        allow_redirects: bool,
    ) -> Any: ...


@dataclass
class ResponseView:
    """Normalized, transport-agnostic view of a response the fetch layer reads."""

    status_code: int
    headers: dict[str, Any]
    url: str
    text: str
    _json: Any = None
    _json_parsed: bool = False
    _raw: Any = None

    def json(self) -> Any:
        return self._json

    @property
    def location(self) -> str | None:
        return self.headers.get("location") or self.headers.get("Location")


def _to_view(raw: Any) -> ResponseView:
    headers = dict(getattr(raw, "headers", {}) or {})
    parsed: Any = None
    try:
        parsed = raw.json()
    except Exception:  # noqa: BLE001 — non-JSON bodies are normal (redirects/challenges)
        parsed = None
    return ResponseView(
        status_code=int(getattr(raw, "status_code")),
        headers=headers,
        url=str(getattr(raw, "url", "")),
        text=getattr(raw, "text", "") or "",
        _json=parsed,
        _json_parsed=parsed is not None,
        _raw=raw,
    )


def _default_transport() -> Transport:
    """Build the real curl_cffi transport. Imported lazily so unit tests that
    inject a fake transport never need curl_cffi installed."""
    from curl_cffi import requests as cffi_requests  # local import by design

    session = cffi_requests.Session()

    def _transport(method, url, *, headers, params, cookies, impersonate, allow_redirects):
        return session.request(
            method,
            url,
            headers=dict(headers),
            params=dict(params) if params else None,
            cookies=dict(cookies) if cookies else None,
            impersonate=impersonate,
            allow_redirects=allow_redirects,
        )

    return _transport


class AnonymousClient:
    """Anonymous curl_cffi wrapper. Never sends auth; carries the required header.

    A per-process cookie jar is permitted to hold benign anonymous cookies
    (mid/csrftoken/ig_did) — IG may require them — but ``assert_anonymous``
    guards every send so it can never contain an auth cookie.
    """

    def __init__(
        self,
        transport: Transport | None = None,
        *,
        cookies: Mapping[str, Any] | None = None,
    ) -> None:
        self._transport = transport or _default_transport()
        # Benign anonymous cookie jar — guarded on every send.
        self._cookies: dict[str, Any] = dict(cookies or {})
        assert_anonymous(cookies=self._cookies)

    @property
    def cookies(self) -> dict[str, Any]:
        return dict(self._cookies)

    def update_cookies(self, cookies: Mapping[str, Any]) -> None:
        """Merge benign anonymous cookies IG set on a prior hit. Guarded."""
        merged = {**self._cookies, **dict(cookies)}
        assert_anonymous(cookies=merged)
        self._cookies = merged

    def get_api(
        self,
        url: str,
        *,
        params: Mapping[str, Any] | None = None,
        extra_headers: Mapping[str, Any] | None = None,
    ) -> ResponseView:
        """GET a metered instagram.com metadata endpoint.

        Adds the mandatory ``x-ig-app-id`` header, impersonates chrome, and does
        NOT follow redirects (a login/challenge 302 must reach the classifier,
        not be transparently followed). Asserts anonymity before sending.
        """
        headers = {**API_HEADERS, **dict(extra_headers or {})}
        assert_anonymous(headers=headers, cookies=self._cookies, params=params)
        raw = self._transport(
            "GET",
            url,
            headers=headers,
            params=params,
            cookies=self._cookies or None,
            impersonate=IMPERSONATE,
            allow_redirects=False,
        )
        view = _to_view(raw)
        # Absorb any benign anonymous cookies IG set (guarded).
        set_cookies = _extract_set_cookies(raw)
        if set_cookies:
            try:
                self.update_cookies(set_cookies)
            except AnonymityViolation:
                # IG tried to hand us an auth cookie on an anonymous call — do
                # NOT store it; the guard's refusal is the correct behaviour.
                pass
        return view

    def get_cdn(self, url: str, *, extra_headers: Mapping[str, Any] | None = None) -> ResponseView:
        """GET an fbcdn.net asset. Follows redirects (fbcdn does 1 redirect;
        a bare GET returns 302/0 bytes). Download itself is a later ticket —
        this exists so the redirect-follow capability lives in one place."""
        headers = dict(extra_headers or {})
        assert_anonymous(headers=headers)
        raw = self._transport(
            "GET",
            url,
            headers=headers,
            params=None,
            cookies=None,
            impersonate=IMPERSONATE,
            allow_redirects=True,
        )
        return _to_view(raw)


def _extract_set_cookies(raw: Any) -> dict[str, Any]:
    """Best-effort read of cookies a transport set, as a plain dict.

    curl_cffi exposes ``response.cookies`` as a jar; fall back to empty."""
    jar = getattr(raw, "cookies", None)
    if not jar:
        return {}
    try:
        return {k: v for k, v in dict(jar).items()}
    except Exception:  # noqa: BLE001
        try:
            return {c.name: c.value for c in jar}  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            return {}
